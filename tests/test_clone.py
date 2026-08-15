#!/usr/bin/env python3
"""
Tests for backlog item 16 (clone a project from an arbitrary remote URL --
see docs/spec.md). App-level half of the split docs/spec.md's own "Affected
areas" calls for: _validate_clone_url(), _last_path_segment_from_clone_url(),
clone_project_from_url()'s collision/name-override/subprocess-failure
branches, and the POST /projects/clone route -- mirroring
tests/test_gitea.py's/tests/test_upload.py's own split between script-level
(tests/test_new_project_from_url.py) and app-level tests here.
subprocess.run is monkeypatched for clone_project_from_url() (same
convention CreateProjectGiteaTests uses for create_project()) -- the real
privileged script is covered by tests/test_new_project_from_url.py.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_clone.py
"""
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

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-clone-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class ValidateCloneUrlTests(unittest.TestCase):
    """_validate_clone_url() -- allowlist-only URL scheme validation
    (docs/spec.md "URL validation -- allowlist, not denylist")."""

    def test_empty_url_rejected(self):
        self.assertIn("required", appmod._validate_clone_url(""))
        self.assertIn("required", appmod._validate_clone_url(None))

    def test_none_and_non_string_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url(None))

    def test_https_accepted(self):
        self.assertIsNone(appmod._validate_clone_url("https://github.com/user/repo.git"))

    def test_http_accepted(self):
        self.assertIsNone(appmod._validate_clone_url("http://example.com/repo.git"))

    def test_ssh_scheme_accepted(self):
        self.assertIsNone(appmod._validate_clone_url("ssh://git@example.com/repo.git"))

    def test_scp_like_shorthand_accepted(self):
        self.assertIsNone(appmod._validate_clone_url("git@github.com:user/repo.git"))

    def test_file_scheme_rejected(self):
        err = appmod._validate_clone_url("file:///etc/passwd")
        self.assertIsNotNone(err)
        self.assertIn("unsupported URL", err)

    def test_git_scheme_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("git://example.com/repo.git"))

    def test_ext_transport_helper_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("ext::sh -c id"))

    def test_fd_transport_helper_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("fd::1"))

    def test_bare_local_path_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("/etc/passwd"))

    def test_relative_local_path_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("../../etc"))

    def test_argument_injection_shape_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("-oProxyCommand=touch /tmp/pwned"))

    def test_ssh_scheme_argument_injection_shape_rejected(self):
        # test-review.md item 16 must-fix: a scheme-prefixed
        # CVE-2017-1000117-shaped host must not slip past the allowlist just
        # because the whole string starts with a fixed 'ssh://' prefix.
        err = appmod._validate_clone_url("ssh://-oProxyCommand=id")
        self.assertIsNotNone(err)
        self.assertIn("unsupported URL", err)

    def test_https_scheme_argument_injection_shape_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("https://-something"))

    def test_scp_like_shorthand_argument_injection_shape_rejected(self):
        # Same CVE-2017-1000117 shape via git's scp-like user@host:path
        # shorthand -- a leading '-' right after '@' must also be rejected.
        self.assertIsNotNone(appmod._validate_clone_url("user@-oProxyCommand=touch${IFS}/tmp/x:path"))
        self.assertIsNotNone(appmod._validate_clone_url("user@-host:path"))

    def test_ssh_scheme_userat_prefix_hides_malicious_host_rejected(self):
        # docs/test-review.md item 16 re-review, repro 1: a 'user@' prefix
        # in front of an ssh:// host must not hide a leading '-' on the
        # REAL host from validation -- the character checked can't just be
        # whatever comes right after '://'.
        err = appmod._validate_clone_url(
            "ssh://user@-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker")
        self.assertIsNotNone(err)
        self.assertIn("unsupported URL", err)

    def test_scp_like_shorthand_malicious_path_after_colon_rejected(self):
        # docs/test-review.md item 16 re-review, repro 2: a leading '-'
        # right after the ':' path separator (not just right after '@')
        # must also be rejected -- this is the component git hands to the
        # remote git-upload-pack invocation for the scp-like shorthand.
        err = appmod._validate_clone_url(
            "user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker")
        self.assertIsNotNone(err)
        self.assertIn("unsupported URL", err)

    def test_ssh_scheme_password_and_userat_prefix_hides_malicious_host_rejected(self):
        # user:password@-host -- the userinfo segment (now with an embedded
        # ':') must still not hide a malicious host after the '@'.
        err = appmod._validate_clone_url("ssh://user:password@-host/repo")
        self.assertIsNotNone(err)

    def test_ssh_scheme_double_at_hides_malicious_host_rejected(self):
        # A second '@' inside the userinfo segment must not let a
        # malicious host slip past by looking like it's "the userinfo" --
        # RFC 3986 authority parsing treats the LAST '@' as the userinfo/
        # host boundary (same semantics urlsplit() uses), so the real host
        # here is '-oproxycommand=...', not 'decoy'.
        err = appmod._validate_clone_url(
            "ssh://real@decoy@-oProxyCommand=touch${IFS}/tmp/x/repo")
        self.assertIsNotNone(err)

    def test_scp_like_shorthand_double_at_in_host_rejected(self):
        err = appmod._validate_clone_url(
            "user@decoy@-oProxyCommand=touch${IFS}/tmp/x:path")
        self.assertIsNotNone(err)

    def test_ssh_scheme_empty_host_after_at_rejected(self):
        err = appmod._validate_clone_url("ssh://user@/repo")
        self.assertIsNotNone(err)

    def test_scp_like_shorthand_empty_host_rejected(self):
        err = appmod._validate_clone_url("user@:path")
        self.assertIsNotNone(err)

    def test_scp_like_shorthand_empty_path_rejected(self):
        err = appmod._validate_clone_url("user@host:")
        self.assertIsNotNone(err)

    def test_bracketed_ipv6_host_accepted(self):
        # A legitimate bracketed IPv6 literal host must still work --
        # tightening the allowlist must not collaterally break a real
        # ssh://user@[::1]:22/path URL shape.
        self.assertIsNone(appmod._validate_clone_url("ssh://user@[::1]:22/repo"))

    def test_bracketed_ipv6_host_with_leading_dash_after_bracket_rejected(self):
        # Sanity check: an IPv6-shaped host string that isn't actually a
        # valid IPv6 literal (garbage after the brackets) must still be
        # rejected, not accepted just because it "looks bracketed".
        err = appmod._validate_clone_url("ssh://user@[not-an-ipv6]:22/repo")
        self.assertIsNotNone(err)

    def test_no_scheme_at_all_rejected(self):
        self.assertIsNotNone(appmod._validate_clone_url("justastring"))

    def test_too_long_rejected(self):
        url = "https://example.com/" + ("a" * appmod.CLONE_URL_MAX_LEN)
        err = appmod._validate_clone_url(url)
        self.assertIsNotNone(err)
        self.assertIn("too long", err)

    def test_control_characters_rejected(self):
        err = appmod._validate_clone_url("https://example.com/re\x00po.git")
        self.assertIsNotNone(err)
        self.assertIn("control characters", err)

    def test_scheme_case_insensitive(self):
        self.assertIsNone(appmod._validate_clone_url("HTTPS://example.com/repo.git"))


class LastPathSegmentTests(unittest.TestCase):
    """_last_path_segment_from_clone_url() -- naming-only heuristic, never
    itself a validity gate (validation already ran by the time this is
    called)."""

    def test_https_url(self):
        self.assertEqual(
            appmod._last_path_segment_from_clone_url("https://github.com/user/my-repo.git"),
            "my-repo")

    def test_https_url_without_dot_git_suffix(self):
        self.assertEqual(
            appmod._last_path_segment_from_clone_url("https://github.com/user/my-repo"),
            "my-repo")

    def test_scp_like_shorthand(self):
        self.assertEqual(
            appmod._last_path_segment_from_clone_url("git@github.com:user/my-repo.git"),
            "my-repo")

    def test_trailing_slash_yields_empty_segment(self):
        self.assertEqual(
            appmod._last_path_segment_from_clone_url("https://github.com/"), "")

    def test_ssh_scheme_url(self):
        self.assertEqual(
            appmod._last_path_segment_from_clone_url("ssh://git@example.com/path/to/repo.git"),
            "repo")


class DeriveProjectNameFallbackPrefixTests(unittest.TestCase):
    """_derive_project_name()'s new optional fallback_prefix parameter --
    default preserves the upload wizard's existing behavior unchanged."""

    def test_default_prefix_still_upload(self):
        self.assertTrue(appmod._derive_project_name("").startswith("upload-"))

    def test_custom_prefix_used_on_fallback(self):
        self.assertTrue(appmod._derive_project_name("", fallback_prefix="clone").startswith("clone-"))

    def test_valid_raw_name_ignores_prefix(self):
        self.assertEqual(appmod._derive_project_name("my-repo", fallback_prefix="clone"), "my-repo")

    def test_unusable_raw_name_falls_back_with_custom_prefix(self):
        name = appmod._derive_project_name("???", fallback_prefix="clone")
        self.assertTrue(name.startswith("clone-"))
        self.assertTrue(appmod.NAME_RE.match(name))


class CloneProjectFromUrlTests(unittest.TestCase):
    """clone_project_from_url() -- mocks subprocess.run, PROJECTS_DIR is a
    real temp directory so instance_names()'s own filesystem check runs for
    real, same convention CreateProjectGiteaTests uses for create_project()."""

    def setUp(self):
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self.tmp = tempfile.mkdtemp(prefix="switchboard-clone-project-")
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(appmod.PROJECTS_DIR)
        self._orig_subprocess_run = subprocess.run
        self.subprocess_calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        appmod.PROJECTS_DIR = self._orig_projects_dir
        subprocess.run = self._orig_subprocess_run

    def _fake_subprocess_ok(self):
        def _fake(cmd, **kwargs):
            self.subprocess_calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "Ready: /x\n", "")
        subprocess.run = _fake

    def _fake_subprocess_fail(self, stderr="clone script failed"):
        def _fake(cmd, **kwargs):
            self.subprocess_calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 1, "", stderr)
        subprocess.run = _fake

    def _fake_subprocess_raises(self, exc):
        def _fake(cmd, **kwargs):
            self.subprocess_calls.append((cmd, kwargs))
            raise exc
        subprocess.run = _fake

    def test_invalid_url_rejected_before_any_subprocess(self):
        ok, msg = appmod.clone_project_from_url("file:///etc/passwd", "")
        self.assertFalse(ok)
        self.assertIn("unsupported URL", msg)
        self.assertEqual(self.subprocess_calls, [])

    def test_derives_name_from_url_when_no_override(self):
        self._fake_subprocess_ok()
        ok, msg = appmod.clone_project_from_url("https://github.com/user/my-repo.git", "")
        self.assertTrue(ok)
        cmd, kwargs = self.subprocess_calls[0]
        self.assertEqual(cmd, ["sudo", appmod.NEW_PROJECT_FROM_URL_SCRIPT,
                               "https://github.com/user/my-repo.git", "my-repo"])
        self.assertEqual(kwargs.get("timeout"), appmod.CLONE_TIMEOUT_SECONDS)

    def test_explicit_name_override_used_instead_of_derived(self):
        self._fake_subprocess_ok()
        ok, msg = appmod.clone_project_from_url(
            "https://github.com/user/my-repo.git", "custom-name")
        self.assertTrue(ok)
        cmd, kwargs = self.subprocess_calls[0]
        self.assertEqual(cmd[-1], "custom-name")

    def test_explicit_name_failing_name_re_rejected_before_subprocess(self):
        ok, msg = appmod.clone_project_from_url(
            "https://github.com/user/my-repo.git", "../escape")
        self.assertFalse(ok)
        self.assertIn("letters, numbers", msg)
        self.assertEqual(self.subprocess_calls, [])

    def test_name_collision_rejected_before_subprocess(self):
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "dup"))
        ok, msg = appmod.clone_project_from_url("https://github.com/user/repo.git", "dup")
        self.assertFalse(ok)
        self.assertEqual(msg, "'dup' already exists.")
        self.assertEqual(self.subprocess_calls, [])

    def test_derived_name_collision_rejected_before_subprocess(self):
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "my-repo"))
        ok, msg = appmod.clone_project_from_url("https://github.com/user/my-repo.git", "")
        self.assertFalse(ok)
        self.assertEqual(msg, "'my-repo' already exists.")
        self.assertEqual(self.subprocess_calls, [])

    def test_trailing_slash_url_falls_back_to_clone_prefixed_name(self):
        self._fake_subprocess_ok()
        ok, msg = appmod.clone_project_from_url("https://github.com/", "")
        self.assertTrue(ok)
        cmd, kwargs = self.subprocess_calls[0]
        self.assertTrue(cmd[-1].startswith("clone-"), cmd[-1])

    def test_subprocess_failure_returns_clipped_error(self):
        self._fake_subprocess_fail(stderr="x" * 500)
        ok, msg = appmod.clone_project_from_url("https://github.com/user/repo.git", "")
        self.assertFalse(ok)
        self.assertLessEqual(len(msg), 300)

    def test_subprocess_timeout_caught_and_returns_clean_error(self):
        self._fake_subprocess_raises(
            subprocess.TimeoutExpired(cmd="clone", timeout=appmod.CLONE_TIMEOUT_SECONDS))
        ok, msg = appmod.clone_project_from_url("https://github.com/user/repo.git", "")
        self.assertFalse(ok)
        self.assertIn("clone failed", msg)

    def test_subprocess_oserror_caught_and_returns_clean_error(self):
        self._fake_subprocess_raises(OSError("no such file"))
        ok, msg = appmod.clone_project_from_url("https://github.com/user/repo.git", "")
        self.assertFalse(ok)
        self.assertIn("clone failed", msg)

    def test_no_gitea_dependency_reads(self):
        # docs/spec.md acceptance criteria: works whether or not
        # --with-git-hosting was ever installed -- reads neither
        # GITEA_ENABLED nor GITEA_API_TOKEN.
        self._fake_subprocess_ok()
        orig_enabled = appmod.GITEA_ENABLED
        orig_token = appmod.GITEA_API_TOKEN
        appmod.GITEA_ENABLED = False
        appmod.GITEA_API_TOKEN = ""
        try:
            ok, msg = appmod.clone_project_from_url("https://github.com/user/repo.git", "")
            self.assertTrue(ok)
        finally:
            appmod.GITEA_ENABLED = orig_enabled
            appmod.GITEA_API_TOKEN = orig_token


class ProjectsCloneEndpointTests(unittest.TestCase):
    """End-to-end HTTP tests against POST /projects/clone.
    clone_project_from_url is monkeypatched at the app module level (same
    convention ConfirmUploadEndpointTests uses for
    _register_via_privileged_script) -- the real privileged script is
    covered by tests/test_new_project_from_url.py."""

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
        self._orig_clone = appmod.clone_project_from_url

    def tearDown(self):
        appmod.clone_project_from_url = self._orig_clone

    def _login(self):
        req = urllib.request.Request(
            f"{self.base}/login", method="POST",
            data=json.dumps({"username": "testuser", "password": "testpass"}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        return resp.headers.get("Set-Cookie").split(";")[0]

    def _totp_code(self):
        return appmod.totp_at(appmod.TOTP_SECRET, time.time())

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

    def test_clone_prompts_for_totp_when_not_cleared(self):
        calls = []
        appmod.clone_project_from_url = lambda url, name: (calls.append((url, name)), (True, ""))[1]
        cookie = self._login()
        status, _ = self._post("/projects/clone", cookie,
                               {"url": "https://github.com/user/repo.git"})
        self.assertEqual(status, 428)
        self.assertEqual(calls, [])

    def test_clone_with_correct_code_succeeds(self):
        calls = []

        def _fake(url, name):
            calls.append((url, name))
            return True, ""
        appmod.clone_project_from_url = _fake
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/clone", cookie,
                                     {"url": "https://github.com/user/repo.git", "code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(calls, [("https://github.com/user/repo.git", "")])

    def test_clone_failure_returns_400_with_error(self):
        appmod.clone_project_from_url = lambda url, name: (False, "unsupported URL")
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/clone", cookie,
                                     {"url": "file:///etc/passwd", "code": code})
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("error"), "unsupported URL")

    def test_clone_passes_name_override_through(self):
        calls = []

        def _fake(url, name):
            calls.append((url, name))
            return True, ""
        appmod.clone_project_from_url = _fake
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(
            "/projects/clone", cookie,
            {"url": "https://github.com/user/repo.git", "name": " custom ", "code": code})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [("https://github.com/user/repo.git", "custom")])


if __name__ == "__main__":
    unittest.main()
