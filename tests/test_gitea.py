#!/usr/bin/env python3
"""
Tests for self-hosted Gitea: the singleton on/off toggle (backlog item 2a)
plus repo creation via Gitea's own REST API (backlog item 2b — see
docs/spec.md, docs/design.md for each). Docker itself is never invoked here:
the one function that shells out to `sudo` + the gitea-{up,down,status}.sh
wrapper scripts (gitea_run) is monkeypatched to a fake, same technique
tests/test_taiga.py's TaigaRunTests/TaigaEndpointTests already use for
taiga_run — the real wrapper scripts' own `docker compose` invocations
aren't exercised here (no Docker Compose plugin available at 2a's own
implementation time; see docs/implementation.md "What could and couldn't be
verified"). Structure mirrors tests/test_taiga.py closely for 2a's own
classes, since that's the exact same singleton-toggle shape applied to a
second service.

2b's own classes (GiteaSlugTests, GiteaApiTests, CreateProjectGiteaTests)
follow tests/test_taiga_push.py's own established convention for this
project's HTTP-calling code: monkeypatch `urllib.request.urlopen` directly
to test the one low-level function that calls it (_gitea_api, mirroring
_taiga_request), then monkeypatch that whole function (plus gitea_run/
subprocess.run) to test the higher-level flow built on top of it
(create_project) — no real Docker/network calls anywhere in this file.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_gitea.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-gitea-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))
# GITEA_ENABLED must be "1" the first time app.py is ever imported in this
# process (module-level globals are computed once, at import time — same
# constraint tests/test_taiga.py's own os.environ.setdefault calls already
# live with). Individual tests below still monkeypatch appmod.GITEA_ENABLED
# directly where they need it False. TAIGA_ENABLED is set too so this file
# can also be run standalone in a process that hasn't already imported app
# via test_taiga.py (both are harmless no-ops if already set).
os.environ.setdefault("TAIGA_ENABLED", "1")
os.environ.setdefault("GITEA_ENABLED", "1")
os.environ.setdefault("GITEA_LABEL", "Gitea")
os.environ.setdefault("GITEA_PORT", "3123")

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class GiteaRunTests(unittest.TestCase):
    """gitea_run() itself — the one function that shells out to sudo."""

    def setUp(self):
        self._orig_run = subprocess.run
        self.calls = []

    def tearDown(self):
        subprocess.run = self._orig_run

    def _fake_run(self, stdout):
        def _fake(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
        subprocess.run = _fake

    def test_status_calls_sudo_status_script_with_short_timeout(self):
        self._fake_run("on\n")
        out = appmod.gitea_run("status")
        self.assertEqual(out, "on")
        cmd, kwargs = self.calls[0]
        self.assertEqual(cmd, ["sudo", appmod.GITEA_STATUS_SCRIPT])
        self.assertEqual(kwargs.get("timeout"), 10)

    def test_up_uses_longer_timeout(self):
        self._fake_run("")
        appmod.gitea_run("up")
        cmd, kwargs = self.calls[0]
        self.assertEqual(cmd, ["sudo", appmod.GITEA_UP_SCRIPT])
        self.assertEqual(kwargs.get("timeout"), 90)

    def test_down_uses_longer_timeout(self):
        self._fake_run("")
        appmod.gitea_run("down")
        cmd, kwargs = self.calls[0]
        self.assertEqual(cmd, ["sudo", appmod.GITEA_DOWN_SCRIPT])
        self.assertEqual(kwargs.get("timeout"), 90)

    def test_invalid_action_asserts(self):
        with self.assertRaises(AssertionError):
            appmod.gitea_run("frobnicate")

    # ── item 42: nonzero returncode on a mutating action raises ─────────

    def _fake_run_nonzero(self, stderr):
        def _fake(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 1, "", stderr)
        subprocess.run = _fake

    def test_up_nonzero_returncode_raises_singleton_action_error_with_stderr(self):
        self._fake_run_nonzero("docker: connection refused")
        with self.assertRaises(appmod.SingletonActionError) as ctx:
            appmod.gitea_run("up")
        self.assertEqual(ctx.exception.stderr, "docker: connection refused")

    def test_down_nonzero_returncode_raises_singleton_action_error(self):
        self._fake_run_nonzero("boom")
        with self.assertRaises(appmod.SingletonActionError):
            appmod.gitea_run("down")

    def test_status_nonzero_returncode_never_raises_returns_stdout(self):
        # "status" must keep its today-unchanged str/never-raises contract
        # (docs/spec.md Edge cases item 42) -- a transient failed status
        # check degrades to "reported off" via the caller's own
        # `out[0] == "on"` parsing, not a raised exception.
        def _fake(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 1, "", "transient error")
        subprocess.run = _fake
        out = appmod.gitea_run("status")
        self.assertEqual(out, "")


class GiteaDisplayUrlTests(unittest.TestCase):
    def setUp(self):
        self._orig_mode = appmod.PUBLISH_MODE
        self._orig_base = appmod.BASE_URL

    def tearDown(self):
        appmod.PUBLISH_MODE = self._orig_mode
        appmod.BASE_URL = self._orig_base

    def test_loopback_mode_returns_local_port_url(self):
        appmod.PUBLISH_MODE = "none"
        self.assertEqual(appmod._gitea_display_url(), f"http://127.0.0.1:{appmod.GITEA_PORT}")

    def test_tailscale_mode_returns_base_url_plus_fixed_path(self):
        appmod.PUBLISH_MODE = "tailscale"
        appmod.BASE_URL = "https://dev.example.ts.net"
        self.assertEqual(appmod._gitea_display_url(), "https://dev.example.ts.net/gitea")


class GiteaEndpointTests(unittest.TestCase):
    """End-to-end HTTP tests against a real ThreadingHTTPServer instance."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        appmod.SESSIONS.clear()
        self._orig_gitea_run = appmod.gitea_run
        self._orig_enabled = appmod.GITEA_ENABLED
        appmod.GITEA_ENABLED = True
        self.gitea_state = "off"

    def tearDown(self):
        appmod.gitea_run = self._orig_gitea_run
        appmod.GITEA_ENABLED = self._orig_enabled

    def _fake_gitea_run(self):
        # Stateful fake: "up"/"down" flip an in-memory state, "status"
        # reports it — mirrors what the real wrapper scripts + dockerd do,
        # without ever touching Docker itself.
        def _fake(action):
            if action == "up":
                self.gitea_state = "on"
            elif action == "down":
                self.gitea_state = "off"
            return self.gitea_state
        appmod.gitea_run = _fake

    def _login(self):
        req = urllib.request.Request(
            f"{self.base}/login", method="POST",
            data=json.dumps({"username": "testuser", "password": "testpass"}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        return resp.headers.get("Set-Cookie").split(";")[0]

    def _totp_code(self):
        return appmod.totp_at(appmod.TOTP_SECRET, time.time())

    def _get_status(self, cookie):
        req = urllib.request.Request(f"{self.base}/status", headers={"Cookie": cookie})
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())

    def _post(self, path, cookie, body=None):
        req = urllib.request.Request(
            f"{self.base}{path}", method="POST",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {}

    def test_status_reports_gitea_fields_when_off(self):
        self._fake_gitea_run()
        cookie = self._login()
        s = self._get_status(cookie)
        self.assertTrue(s["gitea_enabled"])
        self.assertEqual(s["gitea_label"], appmod.GITEA_LABEL)
        self.assertFalse(s["gitea"])
        self.assertIsNone(s["gitea_url"])

    def test_status_reports_gitea_on_with_url(self):
        self._fake_gitea_run()
        self.gitea_state = "on"
        cookie = self._login()
        s = self._get_status(cookie)
        self.assertTrue(s["gitea"])
        self.assertEqual(s["gitea_url"], f"http://127.0.0.1:{appmod.GITEA_PORT}")

    def test_status_includes_gitea_sync_for_a_project_with_a_repo_map_entry(self):
        # docs/spec.md part 2c part 1, acceptance criteria: sync_state/
        # sync_at present when a repo-map entry exists for that project.
        # gitea_state stays "off" here deliberately (default from setUp) so
        # _gitea_poll_if_due(gitea_on=False) never fires a real network call.
        self._fake_gitea_run()
        tmp = tempfile.mkdtemp(prefix="switchboard-status-sync-")
        orig_projects_dir = appmod.PROJECTS_DIR
        orig_map_file = appmod.GITEA_REPO_MAP_FILE
        try:
            appmod.PROJECTS_DIR = os.path.join(tmp, "projects")
            os.makedirs(os.path.join(appmod.PROJECTS_DIR, "proj"))
            appmod.GITEA_REPO_MAP_FILE = os.path.join(tmp, "gitea-repo-map.json")
            appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main",
                                              sync_state="skipped-dirty", sync_at=123.0,
                                              remote_sha="abc123")
            cookie = self._login()
            s = self._get_status(cookie)
            by_name = {i["name"]: i for i in s["instances"]}
            self.assertIn("proj", by_name)
            self.assertEqual(by_name["proj"]["gitea_sync"],
                             {"state": "skipped-dirty", "at": 123.0})
        finally:
            appmod.PROJECTS_DIR = orig_projects_dir
            appmod.GITEA_REPO_MAP_FILE = orig_map_file
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_omits_gitea_sync_for_a_project_without_a_repo_map_entry(self):
        self._fake_gitea_run()
        tmp = tempfile.mkdtemp(prefix="switchboard-status-nosync-")
        orig_projects_dir = appmod.PROJECTS_DIR
        orig_map_file = appmod.GITEA_REPO_MAP_FILE
        try:
            appmod.PROJECTS_DIR = os.path.join(tmp, "projects")
            os.makedirs(os.path.join(appmod.PROJECTS_DIR, "plain"))
            appmod.GITEA_REPO_MAP_FILE = os.path.join(tmp, "gitea-repo-map.json")  # never written
            cookie = self._login()
            s = self._get_status(cookie)
            by_name = {i["name"]: i for i in s["instances"]}
            self.assertIn("plain", by_name)
            self.assertNotIn("gitea_sync", by_name["plain"])
        finally:
            appmod.PROJECTS_DIR = orig_projects_dir
            appmod.GITEA_REPO_MAP_FILE = orig_map_file
            shutil.rmtree(tmp, ignore_errors=True)

    def test_toggle_on_without_code_returns_428(self):
        self._fake_gitea_run()
        cookie = self._login()
        status, _ = self._post("/gitea/on", cookie)
        self.assertEqual(status, 428)
        self.assertEqual(self.gitea_state, "off")  # nothing started before the code gate

    def test_toggle_on_with_wrong_code_returns_403(self):
        self._fake_gitea_run()
        cookie = self._login()
        status, _ = self._post("/gitea/on", cookie, {"code": "000000"})
        self.assertEqual(status, 403)
        self.assertEqual(self.gitea_state, "off")

    def test_toggle_on_with_correct_code_starts_stack_then_off_stops_it(self):
        self._fake_gitea_run()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/gitea/on", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(self.gitea_state, "on")
        # TOTP is verified once per session — the next action needs no code.
        status2, payload2 = self._post("/gitea/off", cookie)
        self.assertEqual(status2, 200)
        self.assertTrue(payload2.get("ok"))
        self.assertEqual(self.gitea_state, "off")

    def test_toggle_on_failure_returns_502_with_error_and_stderr(self):
        # Item 42: a failed gitea_run("up") must surface as a real error,
        # not an unconditional {"ok": true} (docs/spec.md Item 42
        # acceptance criteria).
        def _fake(action):
            if action == "up":
                raise appmod.SingletonActionError("gitea up failed", stderr="disk full")
            return self.gitea_state
        appmod.gitea_run = _fake
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/gitea/on", cookie, {"code": code})
        self.assertEqual(status, 502)
        self.assertIn("error", payload)
        self.assertEqual(payload.get("stderr"), "disk full")

    def test_toggle_off_failure_returns_502_with_error_and_stderr(self):
        def _fake(action):
            if action == "down":
                raise appmod.SingletonActionError("gitea down failed", stderr="timeout")
            return self.gitea_state
        appmod.gitea_run = _fake
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/gitea/off", cookie, {"code": code})
        self.assertEqual(status, 502)
        self.assertIn("error", payload)
        self.assertEqual(payload.get("stderr"), "timeout")

    def test_disabled_returns_404_and_never_calls_gitea_run(self):
        called = []
        appmod.gitea_run = lambda action: called.append(action) or "off"
        appmod.GITEA_ENABLED = False
        cookie = self._login()
        code = self._totp_code()
        status, _ = self._post("/gitea/on", cookie, {"code": code})
        self.assertEqual(status, 404)
        self.assertEqual(called, [])

    def test_unauthenticated_status_returns_401(self):
        req = urllib.request.Request(f"{self.base}/status")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)


# ─── backlog item 2b: repo creation via Gitea's REST API ──────────────────

class GiteaSlugTests(unittest.TestCase):
    """_gitea_slug() -- the local NAME_RE-valid name -> Gitea repo-name
    mapping (docs/spec.md "Local name -> Gitea slug mapping"). NAME_RE
    already guarantees a leading alnum and an otherwise
    [A-Za-z0-9 _-]{0,59} body, a subset of Gitea's own [A-Za-z0-9_.-]+
    rules except for spaces -- so the only translation needed is spaces to
    hyphens."""

    def test_no_spaces_unchanged(self):
        self.assertEqual(appmod._gitea_slug("myproject-1_a"), "myproject-1_a")

    def test_single_space_becomes_hyphen(self):
        self.assertEqual(appmod._gitea_slug("my project"), "my-project")

    def test_multiple_internal_spaces_collapse_to_one_hyphen(self):
        # NAME_RE allows runs of spaces (e.g. "a  b") -- a naive per-space
        # replace would produce "a--b"; \s+ collapses the whole run.
        self.assertEqual(appmod._gitea_slug("a   b   c"), "a-b-c")

    def test_leading_trailing_whitespace_stripped_not_hyphenated(self):
        # NAME_RE's own leading-alnum requirement means a name can't
        # actually start with whitespace, but .strip() is defensive here
        # regardless (docs/spec.md "Edge cases" -- verify the mapping can
        # never itself violate Gitea's rules).
        self.assertEqual(appmod._gitea_slug("  a b  "), "a-b")

    def test_result_never_contains_a_space(self):
        for name in ("a b", "a  b c", "Project Name 123", "x"):
            self.assertNotIn(" ", appmod._gitea_slug(name))

    def test_output_matches_gitea_character_class(self):
        gitea_re = __import__("re").compile(r"^[A-Za-z0-9_.-]+$")
        for name in ("a b", "My Project_1", "x-y.z", "a   b"):
            self.assertRegex(appmod._gitea_slug(name), gitea_re)


class GiteaApiTests(unittest.TestCase):
    """_gitea_api() -- the one function that calls urllib.request directly,
    following tests/test_taiga_push.py's own convention of monkeypatching
    urllib.request.urlopen for exactly this seam."""

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen
        self._orig_token = appmod.GITEA_API_TOKEN
        appmod.GITEA_API_TOKEN = "test-token-abc"

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen
        appmod.GITEA_API_TOKEN = self._orig_token

    class _FakeResp:
        def __init__(self, status, body):
            self.status = status
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_success_sends_expected_request_and_parses_json(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["data"] = req.data
            captured["timeout"] = timeout
            return self._FakeResp(201, json.dumps({"name": "myrepo"}).encode())

        urllib.request.urlopen = fake_urlopen
        status, body = appmod._gitea_api("POST", "/user/repos", {"name": "myrepo"})
        self.assertEqual(status, 201)
        self.assertEqual(body, {"name": "myrepo"})
        self.assertEqual(captured["url"], f"http://127.0.0.1:{appmod.GITEA_PORT}/api/v1/user/repos")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["headers"].get("authorization"), "token test-token-abc")
        self.assertEqual(json.loads(captured["data"]), {"name": "myrepo"})

    def test_no_body_sends_no_data(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            return self._FakeResp(200, b"{}")

        urllib.request.urlopen = fake_urlopen
        appmod._gitea_api("DELETE", "/repos/owner/repo")
        self.assertIsNone(captured["data"])

    def test_http_error_with_json_body_returns_status_and_parsed_body(self):
        def raise_http_error(req, timeout=None):
            raise urllib.error.HTTPError(
                "http://x", 409, "Conflict", {},
                io.BytesIO(json.dumps({"message": "already exists"}).encode()))

        urllib.request.urlopen = raise_http_error
        status, body = appmod._gitea_api("POST", "/user/repos", {"name": "dup"})
        self.assertEqual(status, 409)
        self.assertEqual(body, {"message": "already exists"})

    def test_http_error_with_non_json_body_returns_status_and_empty_dict(self):
        def raise_http_error(req, timeout=None):
            raise urllib.error.HTTPError("http://x", 500, "Server Error", {},
                                         io.BytesIO(b"not json"))

        urllib.request.urlopen = raise_http_error
        status, body = appmod._gitea_api("GET", "/user")
        self.assertEqual(status, 500)
        self.assertEqual(body, {})

    def test_connection_failure_raises_connection_error(self):
        def raise_url_error(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = raise_url_error
        with self.assertRaises(ConnectionError):
            appmod._gitea_api("GET", "/user")

    def test_timeout_raises_connection_error(self):
        def raise_timeout(req, timeout=None):
            raise TimeoutError("timed out")

        urllib.request.urlopen = raise_timeout
        with self.assertRaises(ConnectionError):
            appmod._gitea_api("GET", "/user")


class CreateProjectGiteaTests(unittest.TestCase):
    """create_project() rewritten to go through Gitea's API + the new
    privileged registration script (docs/spec.md "Rewritten
    create_project()"). Mocks _gitea_api/gitea_run/subprocess.run --
    PROJECTS_DIR is a real temp directory so instance_names()'s own
    filesystem check runs for real, same convention tests/test_upload.py
    already uses."""

    def setUp(self):
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self.tmp = tempfile.mkdtemp(prefix="switchboard-create-project-gitea-")
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(appmod.PROJECTS_DIR)

        self._orig_enabled = appmod.GITEA_ENABLED
        self._orig_token = appmod.GITEA_API_TOKEN
        self._orig_gitea_run = appmod.gitea_run
        self._orig_gitea_api = appmod._gitea_api
        self._orig_subprocess_run = subprocess.run
        self._orig_map_file = appmod.GITEA_REPO_MAP_FILE
        appmod.GITEA_REPO_MAP_FILE = os.path.join(self.tmp, "gitea-repo-map.json")

        appmod.GITEA_ENABLED = True
        appmod.GITEA_API_TOKEN = "test-token"
        appmod.gitea_run = lambda action: "on"
        self.api_calls = []
        self.subprocess_calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod.GITEA_ENABLED = self._orig_enabled
        appmod.GITEA_API_TOKEN = self._orig_token
        appmod.gitea_run = self._orig_gitea_run
        appmod._gitea_api = self._orig_gitea_api
        subprocess.run = self._orig_subprocess_run
        appmod.GITEA_REPO_MAP_FILE = self._orig_map_file

    def _fake_gitea_api(self, responses):
        """responses: list of (status, body) tuples, one per call in order."""
        def _fake(method, path, body=None):
            self.api_calls.append((method, path, body))
            return responses[len(self.api_calls) - 1]
        appmod._gitea_api = _fake

    def _fake_subprocess_ok(self):
        def _fake(cmd, **kwargs):
            self.subprocess_calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "Ready: /x\n", "")
        subprocess.run = _fake

    def _fake_subprocess_fail(self, stderr="registration failed"):
        def _fake(cmd, **kwargs):
            self.subprocess_calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 1, "", stderr)
        subprocess.run = _fake

    def test_invalid_name_rejected_before_any_gitea_call(self):
        self._fake_gitea_api([])
        ok, msg = appmod.create_project("../escape")
        self.assertFalse(ok)
        self.assertIn("letters, numbers", msg)
        self.assertEqual(self.api_calls, [])

    def test_name_already_exists_locally_rejected_before_any_gitea_call(self):
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "dup"))
        self._fake_gitea_api([])
        ok, msg = appmod.create_project("dup")
        self.assertFalse(ok)
        self.assertIn("already exists", msg)
        self.assertEqual(self.api_calls, [])

    def test_gitea_not_installed_returns_clear_message_no_legacy_reference(self):
        appmod.GITEA_ENABLED = False
        self._fake_gitea_api([])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("--with-git-hosting", msg)
        self.assertNotIn("NEW_PROJECT_SCRIPT", msg)
        self.assertEqual(self.api_calls, [])

    def test_gitea_installed_but_off_returns_toggle_message_no_api_call(self):
        appmod.gitea_run = lambda action: "off"
        self._fake_gitea_api([])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("toggle it on", msg.lower())
        self.assertEqual(self.api_calls, [])

    def test_missing_token_returns_message_pointing_at_bootstrap_script(self):
        appmod.GITEA_API_TOKEN = ""
        self._fake_gitea_api([])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("gitea-configure-api.sh", msg)
        self.assertEqual(self.api_calls, [])

    def test_gitea_name_collision_409_returns_specific_message_no_dir_left(self):
        self._fake_gitea_api([(409, {"message": "already exists"})])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("already exists", msg)
        self.assertIn("Gitea", msg)
        self.assertFalse(os.path.isdir(os.path.join(appmod.PROJECTS_DIR, "newproj")))

    def test_gitea_name_collision_422_also_treated_as_collision(self):
        self._fake_gitea_api([(422, {})])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("already exists", msg)

    def test_gitea_rejects_creation_other_status_returns_message_with_status(self):
        self._fake_gitea_api([(500, {})])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("500", msg)

    def test_gitea_response_missing_owner_returns_error(self):
        self._fake_gitea_api([(201, {"name": "newproj"})])
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("owner", msg.lower())

    def test_happy_path_creates_repo_and_calls_privileged_script(self):
        self._fake_gitea_api([(201, {"name": "newproj", "owner": {"login": "admin"}})])
        self._fake_subprocess_ok()
        ok, msg = appmod.create_project("newproj")
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertEqual(len(self.api_calls), 1)
        method, path, body = self.api_calls[0]
        self.assertEqual((method, path), ("POST", "/user/repos"))
        self.assertEqual(body["name"], "newproj")
        self.assertTrue(body["private"])
        self.assertTrue(body["auto_init"])
        self.assertEqual(body["default_branch"], "main")

        self.assertEqual(len(self.subprocess_calls), 1)
        cmd, kwargs = self.subprocess_calls[0]
        self.assertEqual(cmd, ["sudo", appmod.NEW_PROJECT_FROM_GITEA_SCRIPT,
                               "admin", "newproj", "newproj"])
        self.assertEqual(kwargs.get("timeout"), 30)

    def test_happy_path_writes_repo_map_entry_with_null_sync_fields(self):
        # docs/spec.md part 2c part 1, acceptance criteria: a successful
        # create_project() (Gitea flow) leaves a GITEA_REPO_MAP_FILE entry
        # mapping owner/repo -> the local name/branch, with
        # sync_state/sync_at/remote_sha all null -- verified without a live
        # Gitea instance (subprocess/API are already mocked above).
        self._fake_gitea_api([(201, {"name": "newproj", "owner": {"login": "admin"}})])
        self._fake_subprocess_ok()
        ok, msg = appmod.create_project("newproj")
        self.assertTrue(ok)
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m, {"admin/newproj": {"name": "newproj", "branch": "main",
                                               "sync_state": None, "sync_at": None,
                                               "remote_sha": None}})

    def test_repo_map_write_failure_does_not_fail_create_project(self):
        # Best-effort/non-fatal (docs/spec.md "Repo-map write in
        # create_project()"): the real repo+clone already succeeded, so a
        # pure local JSON-file-write failure must not turn that into an
        # overall failure -- no regression to 2b's existing behavior.
        self._fake_gitea_api([(201, {"name": "newproj", "owner": {"login": "admin"}})])
        self._fake_subprocess_ok()

        def _raise(*a, **kw):
            raise OSError("disk full")
        orig = appmod._save_gitea_repo_map_entry
        appmod._save_gitea_repo_map_entry = _raise
        try:
            ok, msg = appmod.create_project("newproj")
        finally:
            appmod._save_gitea_repo_map_entry = orig
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_name_with_spaces_uses_slug_for_gitea_but_original_name_for_script(self):
        self._fake_gitea_api([(201, {"name": "my-project", "owner": {"login": "admin"}})])
        self._fake_subprocess_ok()
        ok, msg = appmod.create_project("my project")
        self.assertTrue(ok)
        method, path, body = self.api_calls[0]
        self.assertEqual(body["name"], "my-project")
        cmd, kwargs = self.subprocess_calls[0]
        self.assertEqual(cmd, ["sudo", appmod.NEW_PROJECT_FROM_GITEA_SCRIPT,
                               "admin", "my-project", "my project"])

    def test_privileged_script_failure_cleans_up_gitea_repo_and_returns_error(self):
        self._fake_gitea_api([
            (201, {"name": "newproj", "owner": {"login": "admin"}}),  # create
            (204, {}),  # best-effort DELETE cleanup
        ])
        self._fake_subprocess_fail(stderr="mkdir failed: disk full")
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("mkdir failed", msg)
        self.assertEqual(len(self.api_calls), 2)
        method, path, body = self.api_calls[1]
        self.assertEqual((method, path), ("DELETE", "/repos/admin/newproj"))

    def test_privileged_script_failure_cleanup_itself_failing_is_swallowed(self):
        def _fake(method, path, body=None):
            self.api_calls.append((method, path, body))
            if method == "POST":
                return 201, {"name": "newproj", "owner": {"login": "admin"}}
            raise ConnectionError("gitea unreachable")
        appmod._gitea_api = _fake
        self._fake_subprocess_fail(stderr="clone failed")
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertIn("clone failed", msg)

    def test_privileged_script_stderr_truncated_to_300_chars(self):
        self._fake_gitea_api([
            (201, {"name": "newproj", "owner": {"login": "admin"}}),
            (204, {}),
        ])
        self._fake_subprocess_fail(stderr="x" * 500)
        ok, msg = appmod.create_project("newproj")
        self.assertFalse(ok)
        self.assertLessEqual(len(msg), 300)


if __name__ == "__main__":
    unittest.main()
