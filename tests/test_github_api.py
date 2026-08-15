#!/usr/bin/env python3
"""
Tests for backlog item 17 part 1: unprivileged per-project external-origin
detection (_project_origin_url/_classify_origin_url/detect_project_origin)
plus a GitHub REST API client (_github_api/_github_api_raw, the rate-limit
cooldown gate, and the github_*() read+write convenience functions) --
see docs/spec.md. Nothing in this part is wired into a poll loop, a route,
or item 8's Gitea-only reviewer -- these are pure, directly-callable
functions exercised in isolation.

OriginDetectionTests monkeypatches subprocess.run (mirroring
tests/test_gitea.py's GiteaRunTests _fake_run technique) rather than
touching a real git repo. GithubApiTests follows tests/test_gitea.py's
GiteaApiTests convention exactly: monkeypatch urllib.request.urlopen
directly to test the one low-level pair of functions that call it
(_github_api/_github_api_raw), then GithubConvenienceTests monkeypatches
those two functions to test the higher-level github_*() wrappers built on
top -- no real network call anywhere in this file.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_github_api.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-github-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402


# ─── origin detection ──────────────────────────────────────────────────────

class OriginDetectionTests(unittest.TestCase):
    """_project_origin_url()/_classify_origin_url()/detect_project_origin()
    -- subprocess.run is faked (same technique as test_gitea.py's
    GiteaRunTests) so no real git repo is ever touched."""

    def setUp(self):
        self._orig_run = subprocess.run

    def tearDown(self):
        subprocess.run = self._orig_run

    def _fake_run(self, returncode, stdout=""):
        calls = []

        def _fake(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        subprocess.run = _fake
        return calls

    # -- _project_origin_url --

    def test_project_origin_url_runs_git_remote_get_url_unprivileged(self):
        calls = self._fake_run(0, "http://oauth2:tok@127.0.0.1:3000/owner/repo.git\n")
        url = appmod._project_origin_url("myproj")
        self.assertEqual(url, "http://oauth2:tok@127.0.0.1:3000/owner/repo.git")
        cmd, kwargs = calls[0]
        self.assertEqual(cmd, ["git", "-C", os.path.join(appmod.PROJECTS_DIR, "myproj"),
                               "remote", "get-url", "origin"])
        self.assertNotIn("sudo", cmd)
        self.assertEqual(kwargs.get("timeout"), 10)

    def test_project_origin_url_returns_none_on_nonzero_exit(self):
        self._fake_run(1, "")
        self.assertIsNone(appmod._project_origin_url("noorigin"))

    def test_project_origin_url_returns_none_on_empty_stdout(self):
        self._fake_run(0, "\n")
        self.assertIsNone(appmod._project_origin_url("weird"))

    def test_project_origin_url_returns_none_on_subprocess_failure(self):
        def _raise(cmd, **kwargs):
            raise subprocess.SubprocessError("boom")
        subprocess.run = _raise
        self.assertIsNone(appmod._project_origin_url("crashy"))

    def test_project_origin_url_returns_none_on_timeout(self):
        def _raise(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 10))
        subprocess.run = _raise
        self.assertIsNone(appmod._project_origin_url("slow"))

    def test_project_origin_url_never_raises(self):
        def _raise(cmd, **kwargs):
            raise OSError("no such file")
        subprocess.run = _raise
        self.assertIsNone(appmod._project_origin_url("gone"))

    # -- _classify_origin_url --

    def test_loopback_https_form_classifies_local(self):
        r = appmod._classify_origin_url(
            "http://oauth2:tok@127.0.0.1:3000/owner/repo.git")
        self.assertEqual(r["kind"], "local")

    def test_loopback_ipv6_classifies_local(self):
        r = appmod._classify_origin_url("http://[::1]:3000/owner/repo.git")
        self.assertEqual(r["kind"], "local")

    def test_loopback_any_port_classifies_local(self):
        r = appmod._classify_origin_url(
            "http://oauth2:tok@127.0.0.1:9999/owner/repo.git")
        self.assertEqual(r["kind"], "local")

    def test_github_https_form(self):
        r = appmod._classify_origin_url("https://github.com/owner/repo.git")
        self.assertEqual(r, {"kind": "github", "owner": "owner", "repo": "repo"})

    def test_github_scp_shorthand_form_matches_https(self):
        r = appmod._classify_origin_url("git@github.com:owner/repo.git")
        self.assertEqual(r, {"kind": "github", "owner": "owner", "repo": "repo"})

    def test_github_ssh_scheme_form_matches_https(self):
        r = appmod._classify_origin_url("ssh://git@github.com/owner/repo.git")
        self.assertEqual(r, {"kind": "github", "owner": "owner", "repo": "repo"})

    def test_github_host_case_insensitive(self):
        r = appmod._classify_origin_url("https://GitHub.COM/o/r")
        self.assertEqual(r["kind"], "github")
        self.assertEqual(r["owner"], "o")
        self.assertEqual(r["repo"], "r")

    def test_non_loopback_non_github_host_classifies_external(self):
        r = appmod._classify_origin_url("https://git.example.com/owner/repo.git")
        self.assertEqual(r, {"kind": "external", "owner": None, "repo": None})

    def test_malformed_url_classifies_external_never_raises(self):
        r = appmod._classify_origin_url("::::not a url at all::::")
        self.assertEqual(r["kind"], "external")
        self.assertIsNone(r["owner"])
        self.assertIsNone(r["repo"])

    def test_empty_string_classifies_none(self):
        r = appmod._classify_origin_url("")
        self.assertEqual(r, {"kind": "none", "owner": None, "repo": None})

    # -- detect_project_origin --

    def test_detect_project_origin_no_remote_returns_none_kind(self):
        self._fake_run(1, "")
        r = appmod.detect_project_origin("localonly")
        self.assertEqual(r, {"kind": "none", "owner": None, "repo": None})

    def test_detect_project_origin_composes_url_lookup_and_classification(self):
        self._fake_run(0, "https://github.com/acme/widget.git\n")
        r = appmod.detect_project_origin("widget")
        self.assertEqual(r, {"kind": "github", "owner": "acme", "repo": "widget"})

    def test_detect_project_origin_never_raises_for_not_a_repo(self):
        self._fake_run(128, "fatal: not a git repository\n")
        r = appmod.detect_project_origin("notarepo")
        self.assertEqual(r["kind"], "none")


# ─── GitHub REST client ─────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class GithubApiTests(unittest.TestCase):
    """_github_api()/_github_api_raw() -- the two low-level functions that
    call urllib.request directly, following tests/test_gitea.py's
    GiteaApiTests convention."""

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen
        self._orig_token = appmod.GITHUB_TOKEN
        appmod.GITHUB_TOKEN = "test-gh-token"
        self._orig_until = appmod._github_rate_limited_until
        appmod._github_rate_limited_until = 0.0
        self._orig_time = time.time

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen
        appmod.GITHUB_TOKEN = self._orig_token
        appmod._github_rate_limited_until = self._orig_until
        time.time = self._orig_time

    def test_success_sends_expected_request_and_parses_json(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["data"] = req.data
            captured["timeout"] = timeout
            return _FakeResp(200, json.dumps({"ok": True}).encode())

        urllib.request.urlopen = fake_urlopen
        status, body = appmod._github_api("GET", "/repos/o/r/pulls?state=open")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(captured["url"], f"{appmod.GITHUB_API_BASE}/repos/o/r/pulls?state=open")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["headers"].get("authorization"), "Bearer test-gh-token")
        self.assertTrue(captured["headers"].get("user-agent"))
        self.assertEqual(captured["headers"].get("x-github-api-version"), "2022-11-28")

    def test_post_body_sent_as_json(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            return _FakeResp(201, b"{}")

        urllib.request.urlopen = fake_urlopen
        appmod._github_api("POST", "/repos/o/r/issues/1/comments", {"body": "hi"})
        self.assertEqual(json.loads(captured["data"]), {"body": "hi"})

    def test_http_error_with_json_body_returns_status_and_parsed_body_never_raises(self):
        def raise_http_error(req, timeout=None):
            raise urllib.error.HTTPError(
                "http://x", 404, "Not Found", {},
                io.BytesIO(json.dumps({"message": "Not Found"}).encode()))

        urllib.request.urlopen = raise_http_error
        status, body = appmod._github_api("GET", "/repos/o/r/pulls/999")
        self.assertEqual(status, 404)
        self.assertEqual(body, {"message": "Not Found"})

    def test_connection_failure_raises_connection_error(self):
        def raise_url_error(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = raise_url_error
        with self.assertRaises(ConnectionError):
            appmod._github_api("GET", "/user")

    def test_timeout_raises_connection_error(self):
        def raise_timeout(req, timeout=None):
            raise TimeoutError("timed out")

        urllib.request.urlopen = raise_timeout
        with self.assertRaises(ConnectionError):
            appmod._github_api("GET", "/user")

    def test_raw_returns_text_body_with_diff_accept_header(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _FakeResp(200, b"diff --git a/x b/x\n")

        urllib.request.urlopen = fake_urlopen
        status, text = appmod._github_api_raw(
            "GET", "/repos/o/r/pulls/1", accept="application/vnd.github.v3.diff")
        self.assertEqual(status, 200)
        self.assertEqual(text, "diff --git a/x b/x\n")
        self.assertEqual(captured["headers"].get("accept"), "application/vnd.github.v3.diff")

    def test_raw_http_error_returns_status_and_text_never_raises(self):
        def raise_http_error(req, timeout=None):
            raise urllib.error.HTTPError("http://x", 500, "Server Error", {},
                                         io.BytesIO(b"boom"))

        urllib.request.urlopen = raise_http_error
        status, text = appmod._github_api_raw("GET", "/repos/o/r/pulls/1")
        self.assertEqual(status, 500)
        self.assertEqual(text, "boom")

    # -- rate-limit gate --

    def test_rate_limit_remaining_zero_trips_cooldown_short_circuits_next_call(self):
        now = 1_000_000.0
        time.time = lambda: now
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            return _FakeResp(403, b"{}", headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(now + 100)),
            })

        urllib.request.urlopen = fake_urlopen
        status, _ = appmod._github_api("GET", "/user")
        self.assertEqual(status, 403)
        self.assertEqual(len(calls), 1)

        # A call made BEFORE the reset epoch short-circuits, no urlopen call.
        time.time = lambda: now + 50
        status2, _ = appmod._github_api("GET", "/user")
        self.assertEqual(status2, 429)
        self.assertEqual(len(calls), 1)

        # A call made AFTER the reset epoch proceeds normally.
        time.time = lambda: now + 101
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(200, b"{}")
        status3, _ = appmod._github_api("GET", "/user")
        self.assertEqual(status3, 200)

    def test_retry_after_header_trips_cooldown_short_circuits_next_call(self):
        now = 2_000_000.0
        time.time = lambda: now
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            return _FakeResp(403, b"{}", headers={"Retry-After": "30"})

        urllib.request.urlopen = fake_urlopen
        status, _ = appmod._github_api("GET", "/user")
        self.assertEqual(status, 403)
        self.assertEqual(len(calls), 1)

        time.time = lambda: now + 10
        status2, _ = appmod._github_api("GET", "/user")
        self.assertEqual(status2, 429)
        self.assertEqual(len(calls), 1)  # short-circuited, no new urlopen call

        time.time = lambda: now + 31
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(200, b"{}")
        status3, _ = appmod._github_api("GET", "/user")
        self.assertEqual(status3, 200)

    def test_raw_also_respects_the_shared_cooldown_gate(self):
        now = 3_000_000.0
        time.time = lambda: now
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(
            403, b"{}", headers={"Retry-After": "20"})
        appmod._github_api_raw("GET", "/repos/o/r/pulls/1")

        calls = []
        time.time = lambda: now + 5
        urllib.request.urlopen = lambda req, timeout=None: calls.append(req)
        status, _ = appmod._github_api_raw("GET", "/repos/o/r/pulls/1")
        self.assertEqual(status, 429)
        self.assertEqual(calls, [])

    def test_normal_2xx_with_remaining_quota_does_not_trip_cooldown(self):
        time.time = lambda: 4_000_000.0
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(
            200, b"{}", headers={"X-RateLimit-Remaining": "4999"})
        appmod._github_api("GET", "/user")
        self.assertEqual(appmod._github_rate_limited_until, 0.0)

    def test_malformed_rate_limit_reset_falls_back_to_default_cooldown(self):
        now = 5_000_000.0
        time.time = lambda: now
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(
            403, b"{}", headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "garbage"})
        appmod._github_api("GET", "/user")
        self.assertAlmostEqual(
            appmod._github_rate_limited_until, now + appmod.GITHUB_RATE_LIMIT_FALLBACK_SECONDS,
            delta=1)

    def test_cooldown_never_lowered_by_a_later_smaller_signal(self):
        now = 6_000_000.0
        time.time = lambda: now
        appmod._github_rate_limited_until = now + 500
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(
            403, b"{}", headers={"Retry-After": "5"})
        appmod._github_api("GET", "/user")
        self.assertEqual(appmod._github_rate_limited_until, now + 500)


class GithubConvenienceTests(unittest.TestCase):
    """github_list_open_prs/github_pr_diff/github_list_branches/
    github_post_pr_comment -- monkeypatches _github_api/_github_api_raw,
    following tests/test_gitea.py's CreateProjectGiteaTests convention of
    faking the lower-level client function."""

    def setUp(self):
        self._orig_token = appmod.GITHUB_TOKEN
        self._orig_api = appmod._github_api
        self._orig_api_raw = appmod._github_api_raw
        appmod.GITHUB_TOKEN = "test-gh-token"
        self.calls = []

    def tearDown(self):
        appmod.GITHUB_TOKEN = self._orig_token
        appmod._github_api = self._orig_api
        appmod._github_api_raw = self._orig_api_raw

    def _fake_api(self, status, body):
        def _fake(method, path, body_=None):
            self.calls.append((method, path, body_))
            return status, body
        appmod._github_api = _fake

    def _fake_api_raw(self, status, text):
        def _fake(method, path, accept=None):
            self.calls.append((method, path, accept))
            return status, text
        appmod._github_api_raw = _fake

    def test_list_open_prs_builds_correct_call_and_shape(self):
        self._fake_api(200, [{"number": 1, "title": "x", "labels": [{"name": "ready"}]}])
        r = appmod.github_list_open_prs("acme", "widget")
        self.assertEqual(self.calls, [("GET", "/repos/acme/widget/pulls?state=open", None)])
        self.assertEqual(r, {"ok": True, "prs": [{"number": 1, "title": "x",
                                                   "labels": [{"name": "ready"}]}]})

    def test_list_open_prs_non_200_returns_error(self):
        self._fake_api(500, {})
        r = appmod.github_list_open_prs("acme", "widget")
        self.assertFalse(r["ok"])

    def test_pr_diff_builds_correct_call_and_shape(self):
        self._fake_api_raw(200, "diff --git a/x b/x\n")
        r = appmod.github_pr_diff("acme", "widget", 7)
        self.assertEqual(self.calls, [("GET", "/repos/acme/widget/pulls/7",
                                       "application/vnd.github.v3.diff")])
        self.assertEqual(r, {"ok": True, "diff": "diff --git a/x b/x\n"})

    def test_list_branches_builds_correct_call_and_shape(self):
        self._fake_api(200, [{"name": "main"}, {"name": "dev"}])
        r = appmod.github_list_branches("acme", "widget")
        self.assertEqual(self.calls, [("GET", "/repos/acme/widget/branches", None)])
        self.assertEqual(r, {"ok": True, "branches": [{"name": "main"}, {"name": "dev"}]})

    def test_post_pr_comment_builds_correct_call_and_body(self):
        self._fake_api(201, {"id": 99})
        r = appmod.github_post_pr_comment("acme", "widget", 7, "nice PR")
        self.assertEqual(self.calls, [
            ("POST", "/repos/acme/widget/issues/7/comments", {"body": "nice PR"})])
        self.assertEqual(r, {"ok": True})

    def test_post_pr_comment_non_2xx_returns_error(self):
        self._fake_api(403, {"message": "forbidden"})
        r = appmod.github_post_pr_comment("acme", "widget", 7, "nice PR")
        self.assertFalse(r["ok"])

    def test_connection_error_from_client_is_turned_into_error_dict(self):
        def _raise(method, path, body_=None):
            raise ConnectionError("github unreachable")
        appmod._github_api = _raise
        r = appmod.github_list_open_prs("acme", "widget")
        self.assertEqual(r["ok"], False)
        self.assertIn("github unreachable", r["error"])

    def test_missing_token_short_circuits_every_convenience_function_no_client_call(self):
        appmod.GITHUB_TOKEN = ""

        def _boom_api(*a, **kw):
            raise AssertionError("_github_api must not be called when GITHUB_TOKEN is unset")

        def _boom_api_raw(*a, **kw):
            raise AssertionError("_github_api_raw must not be called when GITHUB_TOKEN is unset")

        appmod._github_api = _boom_api
        appmod._github_api_raw = _boom_api_raw

        r1 = appmod.github_list_open_prs("acme", "widget")
        r2 = appmod.github_pr_diff("acme", "widget", 1)
        r3 = appmod.github_list_branches("acme", "widget")
        r4 = appmod.github_post_pr_comment("acme", "widget", 1, "x")
        for r in (r1, r2, r3, r4):
            self.assertFalse(r["ok"])
            self.assertIn("GITHUB_TOKEN", r["error"])


if __name__ == "__main__":
    unittest.main()
