#!/usr/bin/env python3
"""
Tests for host_run() and the POST /host/on,/off routes (docs/BACKLOG.md
item 42, docs/spec.md "Item 42 -- toggle POST routes lie about success"):
host_run() previously never checked `returncode`, so a failed ssh/script
call was silently swallowed and do_POST unconditionally replied
{"ok": true}. host_run() now raises SingletonActionError for a nonzero
returncode on "start"/"stop" (never for "status", which keeps its
str/never-raises contract -- the 3 existing /status call sites parse its
stdout directly), and do_POST's /host/on,/off branches now catch that and
return a real 502 error instead.

No dedicated host_run test file existed before this cycle -- this file
follows the same structure tests/test_gitea.py's GiteaRunTests/
GiteaEndpointTests and tests/test_taiga.py's TaigaRunTests/
TaigaEndpointTests already establish for the same singleton-toggle shape.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_host_control.py
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

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-host-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class HostRunTests(unittest.TestCase):
    """host_run() itself -- the one function that shells out via ssh."""

    def setUp(self):
        self._orig_run = subprocess.run
        self.calls = []

    def tearDown(self):
        subprocess.run = self._orig_run

    def _fake_run(self, returncode, stdout="", stderr=""):
        def _fake(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        subprocess.run = _fake

    def test_status_returns_stdout_regardless_of_returncode(self):
        # "status" keeps its today-unchanged str/never-raises contract.
        self._fake_run(1, stdout="off\n", stderr="ignored")
        out = appmod.host_run("status")
        self.assertEqual(out, "off")

    def test_start_success_returns_stdout(self):
        self._fake_run(0, stdout="on\n")
        out = appmod.host_run("start")
        self.assertEqual(out, "on")

    def test_start_nonzero_returncode_raises_with_stderr(self):
        self._fake_run(1, stderr="ssh: connection refused")
        with self.assertRaises(appmod.SingletonActionError) as ctx:
            appmod.host_run("start")
        self.assertEqual(ctx.exception.stderr, "ssh: connection refused")

    def test_stop_nonzero_returncode_raises(self):
        self._fake_run(1, stderr="boom")
        with self.assertRaises(appmod.SingletonActionError):
            appmod.host_run("stop")

    def test_invalid_action_asserts(self):
        with self.assertRaises(AssertionError):
            appmod.host_run("frobnicate")


class HostEndpointTests(unittest.TestCase):
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
        self._orig_host_run = appmod.host_run
        self._orig_enabled = appmod.HOST_CONTROL_ENABLED
        appmod.HOST_CONTROL_ENABLED = True

    def tearDown(self):
        appmod.host_run = self._orig_host_run
        appmod.HOST_CONTROL_ENABLED = self._orig_enabled

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

    def test_toggle_on_success_returns_200_ok(self):
        appmod.host_run = lambda action: "on"
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/host/on", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))

    def test_toggle_on_failure_returns_502_with_error_and_stderr(self):
        def _fake(action):
            raise appmod.SingletonActionError("host start failed", stderr="ssh: connection refused")
        appmod.host_run = _fake
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/host/on", cookie, {"code": code})
        self.assertEqual(status, 502)
        self.assertIn("error", payload)
        self.assertEqual(payload.get("stderr"), "ssh: connection refused")

    def test_toggle_off_failure_returns_502_with_error_and_stderr(self):
        def _fake(action):
            raise appmod.SingletonActionError("host stop failed", stderr="timed out")
        appmod.host_run = _fake
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/host/off", cookie, {"code": code})
        self.assertEqual(status, 502)
        self.assertIn("error", payload)
        self.assertEqual(payload.get("stderr"), "timed out")

    def test_disabled_returns_404(self):
        appmod.HOST_CONTROL_ENABLED = False
        cookie = self._login()
        code = self._totp_code()
        status, _ = self._post("/host/on", cookie, {"code": code})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
