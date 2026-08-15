#!/usr/bin/env python3
"""
Tests for the self-hosted Taiga singleton toggle (backlog item 1a — see
docs/spec.md, docs/design.md). Docker itself is never invoked here: the one
function that shells out to `sudo` + the taiga-{up,down,status}.sh wrapper
scripts (taiga_run) is monkeypatched to a fake, same technique
tests/test_upload.py's CreateProjectsFromSelectionTests already uses for
_register_via_privileged_script — the real wrapper scripts' own `docker
compose` invocations aren't exercised here (no Docker Compose plugin
available in this environment; see docs/implementation.md "What could and
couldn't be verified").

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_taiga.py
"""
import json
import os
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

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-taiga-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))
# TAIGA_ENABLED must be "1" the first time app.py is ever imported in this
# process (module-level globals are computed once, at import time — same
# constraint tests/test_upload.py's own os.environ.setdefault calls already
# live with). Individual tests below still monkeypatch appmod.TAIGA_ENABLED
# directly where they need it False.
os.environ.setdefault("TAIGA_ENABLED", "1")
os.environ.setdefault("TAIGA_LABEL", "Taiga")
os.environ.setdefault("TAIGA_PORT", "9123")

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class TaigaRunTests(unittest.TestCase):
    """taiga_run() itself — the one function that shells out to sudo."""

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
        out = appmod.taiga_run("status")
        self.assertEqual(out, "on")
        cmd, kwargs = self.calls[0]
        self.assertEqual(cmd, ["sudo", appmod.TAIGA_STATUS_SCRIPT])
        self.assertEqual(kwargs.get("timeout"), 10)

    def test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop(self):
        # Item 30 (v2): scripts/taiga-up.sh's own retry loop now sleeps up
        # to 150s (5 attempts, 10/20/40/80s exponential backoff) before
        # giving up -- comfortably past the previous flat 90s timeout here,
        # which would have killed the script mid-retry. Raised to 180s for
        # "up" specifically (docs/spec.md "Fix 2", the explicitly-required
        # arithmetic check against taiga_run()'s real caller-side timeout).
        self._fake_run("")
        appmod.taiga_run("up")
        cmd, kwargs = self.calls[0]
        self.assertEqual(cmd, ["sudo", appmod.TAIGA_UP_SCRIPT])
        self.assertEqual(kwargs.get("timeout"), 180)

    def test_down_uses_longer_timeout(self):
        self._fake_run("")
        appmod.taiga_run("down")
        cmd, kwargs = self.calls[0]
        self.assertEqual(cmd, ["sudo", appmod.TAIGA_DOWN_SCRIPT])
        self.assertEqual(kwargs.get("timeout"), 90)

    def test_invalid_action_asserts(self):
        with self.assertRaises(AssertionError):
            appmod.taiga_run("frobnicate")


class TaigaDisplayUrlTests(unittest.TestCase):
    def setUp(self):
        self._orig_mode = appmod.PUBLISH_MODE
        self._orig_base = appmod.BASE_URL

    def tearDown(self):
        appmod.PUBLISH_MODE = self._orig_mode
        appmod.BASE_URL = self._orig_base

    def test_loopback_mode_returns_local_port_url(self):
        appmod.PUBLISH_MODE = "none"
        self.assertEqual(appmod._taiga_display_url(), f"http://127.0.0.1:{appmod.TAIGA_PORT}")

    def test_tailscale_mode_returns_base_url_plus_fixed_path(self):
        appmod.PUBLISH_MODE = "tailscale"
        appmod.BASE_URL = "https://dev.example.ts.net"
        self.assertEqual(appmod._taiga_display_url(), "https://dev.example.ts.net/taiga")


class TaigaEndpointTests(unittest.TestCase):
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
        self._orig_taiga_run = appmod.taiga_run
        self._orig_enabled = appmod.TAIGA_ENABLED
        appmod.TAIGA_ENABLED = True
        self.taiga_state = "off"

    def tearDown(self):
        appmod.taiga_run = self._orig_taiga_run
        appmod.TAIGA_ENABLED = self._orig_enabled

    def _fake_taiga_run(self):
        # Stateful fake: "up"/"down" flip an in-memory state, "status"
        # reports it — mirrors what the real wrapper scripts + dockerd do,
        # without ever touching Docker itself.
        def _fake(action):
            if action == "up":
                self.taiga_state = "on"
            elif action == "down":
                self.taiga_state = "off"
            return self.taiga_state
        appmod.taiga_run = _fake

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

    def test_status_reports_taiga_fields_when_off(self):
        self._fake_taiga_run()
        cookie = self._login()
        s = self._get_status(cookie)
        self.assertTrue(s["taiga_enabled"])
        self.assertEqual(s["taiga_label"], appmod.TAIGA_LABEL)
        self.assertFalse(s["taiga"])
        self.assertIsNone(s["taiga_url"])

    def test_status_reports_taiga_on_with_url(self):
        self._fake_taiga_run()
        self.taiga_state = "on"
        cookie = self._login()
        s = self._get_status(cookie)
        self.assertTrue(s["taiga"])
        self.assertEqual(s["taiga_url"], f"http://127.0.0.1:{appmod.TAIGA_PORT}")

    def test_toggle_on_without_code_returns_428(self):
        self._fake_taiga_run()
        cookie = self._login()
        status, _ = self._post("/taiga/on", cookie)
        self.assertEqual(status, 428)
        self.assertEqual(self.taiga_state, "off")  # nothing started before the code gate

    def test_toggle_on_with_wrong_code_returns_403(self):
        self._fake_taiga_run()
        cookie = self._login()
        status, _ = self._post("/taiga/on", cookie, {"code": "000000"})
        self.assertEqual(status, 403)
        self.assertEqual(self.taiga_state, "off")

    def test_toggle_on_with_correct_code_starts_stack_then_off_stops_it(self):
        self._fake_taiga_run()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/taiga/on", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(self.taiga_state, "on")
        # TOTP is verified once per session — the next action needs no code.
        status2, payload2 = self._post("/taiga/off", cookie)
        self.assertEqual(status2, 200)
        self.assertTrue(payload2.get("ok"))
        self.assertEqual(self.taiga_state, "off")

    def test_disabled_returns_404_and_never_calls_taiga_run(self):
        called = []
        appmod.taiga_run = lambda action: called.append(action) or "off"
        appmod.TAIGA_ENABLED = False
        cookie = self._login()
        code = self._totp_code()
        status, _ = self._post("/taiga/on", cookie, {"code": code})
        self.assertEqual(status, 404)
        self.assertEqual(called, [])

    def test_unauthenticated_status_returns_401(self):
        req = urllib.request.Request(f"{self.base}/status")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
