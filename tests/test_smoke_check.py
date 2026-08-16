#!/usr/bin/env python3
"""
Tests for the HTTP-level smoke check (backlog item 18, docs/spec.md): the
per-project non-blocking concurrency guard (_smoke_check_lock_for),
smoke_check_run() itself (exercised against real local HTTP servers/sockets
-- no mocking of urllib, same "provision something real and exercise it"
philosophy tests/test_deploy_dispatch.py's own DeployRunTests/
PrivilegedDeployRunEndToEndTests already establish, scaled down to "a real
local http.server instance" since no privileged receiver is needed here),
and the `POST /projects/<name>/smoke-check` route (exercised end to end
against a real ThreadingHTTPServer instance, same technique as
tests/test_deploy_dispatch.py's own DeployEndpointTests).

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_smoke_check.py
"""
import http.server
import json
import os
import socket
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

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-smoke-check-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def _make_handler(status=200, body=b"hello world", delay=0, content_type="text/plain"):
    """Builds a fresh BaseHTTPRequestHandler subclass per test -- a plain
    class attribute closure, since http.server wants a class (not an
    instance) handed to its server constructor."""
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if delay:
                time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass
    return _Handler


class _RealServer:
    """Spins up a real ThreadingHTTPServer on 127.0.0.1:<ephemeral> serving
    a fixed response -- used as a context manager so each test gets its own
    throwaway server/thread, torn down deterministically."""

    def __init__(self, **handler_kwargs):
        self.handler_kwargs = handler_kwargs

    def __enter__(self):
        handler_cls = _make_handler(**self.handler_kwargs)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class _AcceptNeverRespondServer:
    """A raw listening socket that accepts connections but never writes a
    byte back -- used to exercise smoke_check_run()'s real timeout path
    (docs/spec.md "Risk / rollback notes": "mitigate by testing the timeout
    path explicitly ... rather than assuming the stdlib timeout parameter
    alone is sufficient proof")."""

    def __enter__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self._stop = False
        self._conns = []

        def _accept_loop():
            while not self._stop:
                try:
                    self.sock.settimeout(0.2)
                    conn, _ = self.sock.accept()
                    self._conns.append(conn)
                except OSError:
                    return

        self.thread = threading.Thread(target=_accept_loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        self.thread.join(timeout=2)
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


class SmokeCheckLockTests(unittest.TestCase):
    def setUp(self):
        appmod._smoke_check_locks.clear()

    def tearDown(self):
        appmod._smoke_check_locks.clear()

    def test_different_projects_do_not_share_a_lock(self):
        self.assertIsNot(
            appmod._smoke_check_lock_for("proj"), appmod._smoke_check_lock_for("other"))

    def test_same_project_always_returns_the_same_lock(self):
        self.assertIs(
            appmod._smoke_check_lock_for("proj"), appmod._smoke_check_lock_for("proj"))


class SmokeCheckRunTests(unittest.TestCase):
    """smoke_check_run() -- no urllib mocking anywhere in this class; every
    case is exercised against a real local server or socket."""

    def setUp(self):
        appmod._session_urls.clear()
        appmod._smoke_check_locks.clear()
        self._orig_timeout = appmod.SMOKE_CHECK_TIMEOUT_SECONDS
        self._orig_max_body = appmod.SMOKE_CHECK_MAX_BODY_BYTES
        # Session-identity backend (docs/spec.md, part 1 -- "Smoke-check
        # preserved via a resolver"): smoke_check_run() now resolves its URL
        # via _latest_session_url_for_project(), which requires a real live
        # tracked session (see tests/test_session_identity.py's
        # SessionIdentityEndpointTests for THAT new behavior, exercised end
        # to end). This class predates that change and is about
        # smoke_check_run()'s own HTTP-fetching mechanics (timeouts, body
        # truncation, non-utf8 handling, locking) -- orthogonal to session
        # resolution -- so _latest_session_url_for_project is monkeypatched
        # back to its own exact pre-spec behavior (a direct _session_urls-
        # by-name lookup) for the duration of this class, letting every
        # test below keep writing appmod._session_urls["proj"] = ...
        # unchanged.
        self._orig_latest_url = appmod._latest_session_url_for_project
        appmod._latest_session_url_for_project = appmod._session_urls.get

    def tearDown(self):
        appmod._session_urls.clear()
        appmod._smoke_check_locks.clear()
        appmod.SMOKE_CHECK_TIMEOUT_SECONDS = self._orig_timeout
        appmod.SMOKE_CHECK_MAX_BODY_BYTES = self._orig_max_body
        appmod._latest_session_url_for_project = self._orig_latest_url

    def test_no_captured_url_returns_ok_false_without_touching_the_lock(self):
        result = appmod.smoke_check_run("does-not-exist", "")
        self.assertEqual(result, {"ok": False, "error": "no captured URL for this project"})
        # Never even attempted to acquire the lock -- a lock created lazily
        # elsewhere would still be free.
        self.assertTrue(appmod._smoke_check_lock_for("does-not-exist").acquire(blocking=False))
        appmod._smoke_check_lock_for("does-not-exist").release()

    def test_success_reports_status_code_and_elapsed_ms_no_content_check(self):
        with _RealServer(status=200, body=b"hello world") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertIsInstance(result["elapsed_ms"], int)
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertIsNone(result["content_ok"])

    def test_expect_contains_present_reports_content_ok_true(self):
        with _RealServer(status=200, body=b"hello world, this is a test body") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "this is a test")
        self.assertTrue(result["ok"])
        self.assertTrue(result["content_ok"])

    def test_expect_contains_absent_reports_content_ok_false_alongside_real_status(self):
        with _RealServer(status=200, body=b"hello world") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "definitely not present")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertFalse(result["content_ok"])

    def test_empty_expect_contains_yields_content_ok_none_not_false(self):
        with _RealServer(status=200, body=b"anything") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "")
        self.assertIsNone(result["content_ok"])

    def test_target_http_error_status_is_a_completed_check_not_a_failure(self):
        with _RealServer(status=404, body=b"not found here") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "found")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status_code"], 404)
        self.assertTrue(result["content_ok"])

    def test_target_5xx_is_also_a_completed_check(self):
        with _RealServer(status=500, body=b"boom") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status_code"], 500)

    def test_connection_refused_returns_clean_failure_not_raise(self):
        appmod._session_urls["proj"] = "http://127.0.0.1:1/"  # nothing listens here
        result = appmod.smoke_check_run("proj", "")  # must not raise
        self.assertFalse(result["ok"])
        self.assertIsNone(result["status_code"])
        self.assertIn("refused", result["error"].lower())
        self.assertIsInstance(result["elapsed_ms"], int)

    def test_timeout_returns_within_roughly_the_configured_bound(self):
        appmod.SMOKE_CHECK_TIMEOUT_SECONDS = 1
        with _AcceptNeverRespondServer() as srv:
            appmod._session_urls["proj"] = srv.url
            started = time.monotonic()
            result = appmod.smoke_check_run("proj", "")  # must not hang/raise
            elapsed = time.monotonic() - started
        self.assertFalse(result["ok"])
        self.assertIsNone(result["status_code"])
        self.assertIn("timed out", result["error"].lower())
        self.assertLess(elapsed, 10, "must fail fast, not hang")

    def test_response_body_truncated_at_max_body_bytes_cap(self):
        appmod.SMOKE_CHECK_MAX_BODY_BYTES = 10
        body = b"0123456789" + b"MATCH-PAST-THE-CAP" + (b"x" * 100)
        with _RealServer(status=200, body=body) as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "MATCH-PAST-THE-CAP")
        self.assertTrue(result["ok"])
        # A match that only exists past the cap must NOT be found -- the
        # substring check only ever sees the truncated prefix.
        self.assertFalse(result["content_ok"])

    def test_non_utf8_body_does_not_raise_and_is_decoded_with_errors_ignored(self):
        with _RealServer(status=200, body=b"\xff\xfe not valid utf-8 \x80\x81 but has ok text") as srv:
            appmod._session_urls["proj"] = srv.url
            result = appmod.smoke_check_run("proj", "ok text")  # must not raise
        self.assertTrue(result["ok"])
        self.assertTrue(result["content_ok"])

    def test_concurrent_dispatch_for_same_project_reports_locked(self):
        with _RealServer(status=200, body=b"hi") as srv:
            appmod._session_urls["proj"] = srv.url
            lock = appmod._smoke_check_lock_for("proj")
            self.assertTrue(lock.acquire(blocking=False))
            try:
                result = appmod.smoke_check_run("proj", "")
                self.assertFalse(result["ok"])
                self.assertTrue(result.get("locked"))
            finally:
                lock.release()

    def test_lock_released_after_a_completed_check_allows_the_next_dispatch(self):
        with _RealServer(status=200, body=b"hi") as srv:
            appmod._session_urls["proj"] = srv.url
            r1 = appmod.smoke_check_run("proj", "")
            self.assertTrue(r1["ok"])
            r2 = appmod.smoke_check_run("proj", "")
            self.assertTrue(r2["ok"])

    def test_lock_released_after_a_failed_check_allows_the_next_dispatch(self):
        appmod._session_urls["proj"] = "http://127.0.0.1:1/"
        r1 = appmod.smoke_check_run("proj", "")
        self.assertFalse(r1["ok"])
        with _RealServer(status=200, body=b"hi") as srv:
            appmod._session_urls["proj"] = srv.url
            r2 = appmod.smoke_check_run("proj", "")
        self.assertTrue(r2["ok"])

    def test_different_projects_do_not_block_each_other(self):
        # Project A's check is in flight (lock held); project B must still
        # be able to complete a real check concurrently.
        lock_a = appmod._smoke_check_lock_for("a")
        self.assertTrue(lock_a.acquire(blocking=False))
        try:
            with _RealServer(status=200, body=b"hi") as srv:
                appmod._session_urls["b"] = srv.url
                result = appmod.smoke_check_run("b", "")
            self.assertTrue(result["ok"])
        finally:
            lock_a.release()


class SmokeCheckEndpointTests(unittest.TestCase):
    """End-to-end HTTP tests against a real ThreadingHTTPServer instance --
    same technique as tests/test_deploy_dispatch.py's DeployEndpointTests."""

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
        self.tmp = tempfile.mkdtemp(prefix="switchboard-smoke-check-endpoint-")
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self._orig_smoke_check_run = appmod.smoke_check_run
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "proj"))
        appmod._session_urls.clear()
        appmod._smoke_check_locks.clear()

    def tearDown(self):
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod.smoke_check_run = self._orig_smoke_check_run
        appmod._session_urls.clear()
        appmod._smoke_check_locks.clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

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

    def test_smoke_check_post_unknown_project_returns_404(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/nope/smoke-check", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_smoke_check_post_without_code_returns_428(self):
        cookie = self._login()
        status, _ = self._post("/projects/proj/smoke-check", cookie)
        self.assertEqual(status, 428)

    def test_smoke_check_post_dispatches_and_returns_success_result(self):
        appmod.smoke_check_run = lambda name, expect_contains: (
            {"ok": True, "status_code": 200, "elapsed_ms": 42, "content_ok": None})
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/smoke-check", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status_code"], 200)
        self.assertEqual(payload["elapsed_ms"], 42)

    def test_smoke_check_post_target_side_failure_still_returns_http_200(self):
        appmod.smoke_check_run = lambda name, expect_contains: (
            {"ok": False, "status_code": None, "elapsed_ms": 1000, "error": "connection refused"})
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/smoke-check", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "connection refused")

    def test_smoke_check_post_lock_contention_surfaces_as_409(self):
        appmod.smoke_check_run = lambda name, expect_contains: (
            {"ok": False, "error": "a smoke check for this project is already in progress", "locked": True})
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/smoke-check", cookie, {"code": code})
        self.assertEqual(status, 409)
        self.assertFalse(payload["ok"])
        self.assertNotIn("locked", payload, "internal-only marker must not leak to the client")

    def test_smoke_check_post_passes_expect_contains_through_to_dispatch(self):
        captured = {}

        def _fake(name, expect_contains):
            captured["name"] = name
            captured["expect_contains"] = expect_contains
            return {"ok": True, "status_code": 200, "elapsed_ms": 1, "content_ok": True}
        appmod.smoke_check_run = _fake
        cookie = self._login()
        code = self._totp_code()
        self._post("/projects/proj/smoke-check", cookie,
                   {"code": code, "expect_contains": "  needle  "})
        self.assertEqual(captured["name"], "proj")
        self.assertEqual(captured["expect_contains"], "needle")

    def test_smoke_check_post_non_string_expect_contains_is_coerced_to_empty(self):
        captured = {}

        def _fake(name, expect_contains):
            captured["expect_contains"] = expect_contains
            return {"ok": True, "status_code": 200, "elapsed_ms": 1, "content_ok": None}
        appmod.smoke_check_run = _fake
        cookie = self._login()
        code = self._totp_code()
        self._post("/projects/proj/smoke-check", cookie, {"code": code, "expect_contains": 12345})
        self.assertEqual(captured["expect_contains"], "")

    def test_smoke_check_post_missing_expect_contains_defaults_to_empty(self):
        captured = {}

        def _fake(name, expect_contains):
            captured["expect_contains"] = expect_contains
            return {"ok": True, "status_code": 200, "elapsed_ms": 1, "content_ok": None}
        appmod.smoke_check_run = _fake
        cookie = self._login()
        code = self._totp_code()
        self._post("/projects/proj/smoke-check", cookie, {"code": code})
        self.assertEqual(captured["expect_contains"], "")

if __name__ == "__main__":
    unittest.main()
