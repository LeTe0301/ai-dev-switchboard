#!/usr/bin/env python3
"""
Tests for switchboard-side deploy dispatch (backlog item 2c, part 2b —
docs/spec.md): the hand-edited deploy-map.json loader (_load_deploy_map),
the per-project non-blocking concurrency guard (_deploy_lock_for), the
actual push+restart dispatch (deploy_run — subprocess.run mocked in
DeployRunTests/DeployEndpointTests, no real rsync/ssh there), the
`/instance/<name>/deploy` route plus `/status`'s new `deploy` field
(exercised end to end against a real ThreadingHTTPServer instance), and
finally PrivilegedDeployRunEndToEndTests, which calls the real deploy_run()
(no mocking at all) against a real throwaway deploy-target receiver on this
machine's own sshd — the same "provision a real receiver, exercise it over
real ssh/rsync" technique tests/test_deploy_target.py's own
PrivilegedEndToEndTests already established for 2c part 2a, reused here to
prove 2b's *caller* side against a real receiver rather than only against
mocks. Structure otherwise mirrors tests/test_gitea_poll.py (map-loading +
dispatch-function unit tests) and tests/test_gitea.py's GiteaEndpointTests
(real HTTP server, login/TOTP helpers).

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_deploy_dispatch.py
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
DEPLOY_TARGET_DIR = os.path.join(REPO_ROOT, "deploy-target")
WRAPPER = os.path.join(DEPLOY_TARGET_DIR, "deploy-wrapper.sh")
RESTART = os.path.join(DEPLOY_TARGET_DIR, "deploy-restart.sh")
sys.path.insert(0, APP_DIR)

HAVE_PASSWORDLESS_SUDO = subprocess.run(
    ["sudo", "-n", "true"], capture_output=True).returncode == 0
HAVE_SSHD_ON_LOCALHOST = subprocess.run(
    ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/22"],
    capture_output=True).returncode == 0

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-deploy-dispatch-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class DeployMapLoadTests(unittest.TestCase):
    """_load_deploy_map -- mirrors _load_gitea_repo_map's "missing/malformed
    -> {}, never crash" tolerance, plus this feature's own per-entry
    validation (required keys present, "key" resolves under
    DEPLOY_KEYS_DIR)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-deploy-map-")
        self._orig_map_file = appmod.DEPLOY_MAP_FILE
        self._orig_keys_dir = appmod.DEPLOY_KEYS_DIR
        appmod.DEPLOY_MAP_FILE = os.path.join(self.tmp, "deploy-map.json")
        appmod.DEPLOY_KEYS_DIR = os.path.join(self.tmp, "deploy-keys")
        os.makedirs(appmod.DEPLOY_KEYS_DIR)

    def tearDown(self):
        appmod.DEPLOY_MAP_FILE = self._orig_map_file
        appmod.DEPLOY_KEYS_DIR = self._orig_keys_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_map(self, obj):
        with open(appmod.DEPLOY_MAP_FILE, "w") as f:
            json.dump(obj, f)

    def _key_path(self, name="myapp_ed25519"):
        return os.path.join(appmod.DEPLOY_KEYS_DIR, name)

    def test_missing_file_loads_as_empty_dict(self):
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_malformed_json_loads_as_empty_dict_not_raise(self):
        with open(appmod.DEPLOY_MAP_FILE, "w") as f:
            f.write("not json{{{")
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_non_dict_top_level_loads_as_empty_dict(self):
        self._write_map(["not", "a", "dict"])
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_valid_entry_loads_with_field_defaults(self):
        self._write_map({"proj": {"host": "10.0.0.5", "deploy_path": "/opt/app",
                                  "service": "app.service", "key": self._key_path()}})
        m = appmod._load_deploy_map()
        self.assertEqual(m["proj"]["host"], "10.0.0.5")
        self.assertEqual(m["proj"]["port"], 22)
        self.assertEqual(m["proj"]["user"], "deploy")
        self.assertEqual(m["proj"]["deploy_path"], "/opt/app")
        self.assertEqual(m["proj"]["service"], "app.service")
        self.assertEqual(m["proj"]["key"], self._key_path())

    def test_explicit_port_and_user_override_defaults(self):
        self._write_map({"proj": {"host": "h", "port": 2222, "user": "svc",
                                  "deploy_path": "/opt/app", "service": "app.service",
                                  "key": self._key_path()}})
        m = appmod._load_deploy_map()
        self.assertEqual(m["proj"]["port"], 2222)
        self.assertEqual(m["proj"]["user"], "svc")

    def test_entry_missing_a_required_key_is_dropped(self):
        base = {"host": "h", "deploy_path": "/opt/app", "service": "app.service",
               "key": self._key_path()}
        for missing in ("host", "deploy_path", "service", "key"):
            entry = dict(base)
            del entry[missing]
            self._write_map({"proj": entry})
            self.assertEqual(appmod._load_deploy_map(), {},
                             f"missing {missing!r} should drop the entry")

    def test_entry_with_key_outside_keys_dir_is_dropped(self):
        outside = os.path.join(self.tmp, "not-the-keys-dir", "key")
        self._write_map({"proj": {"host": "h", "deploy_path": "/opt/app",
                                  "service": "app.service", "key": outside}})
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_entry_with_key_traversal_outside_keys_dir_is_dropped(self):
        traversal = os.path.join(appmod.DEPLOY_KEYS_DIR, "..", "escaped_key")
        self._write_map({"proj": {"host": "h", "deploy_path": "/opt/app",
                                  "service": "app.service", "key": traversal}})
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_entry_with_key_nested_inside_keys_dir_is_kept(self):
        nested = os.path.join(appmod.DEPLOY_KEYS_DIR, "sub", "key")
        self._write_map({"proj": {"host": "h", "deploy_path": "/opt/app",
                                  "service": "app.service", "key": nested}})
        self.assertIn("proj", appmod._load_deploy_map())

    def test_one_malformed_entry_does_not_affect_others(self):
        self._write_map({
            "bad": {"host": "h"},  # missing deploy_path/service/key
            "good": {"host": "h2", "deploy_path": "/opt/app2", "service": "app2.service",
                    "key": self._key_path("good_key")},
        })
        m = appmod._load_deploy_map()
        self.assertEqual(set(m.keys()), {"good"})

    def test_non_dict_entry_value_is_dropped(self):
        self._write_map({"proj": "not-a-dict"})
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_entry_with_non_numeric_port_is_dropped_not_raised(self):
        self._write_map({"proj": {"host": "h", "port": "oops", "deploy_path": "/opt/app",
                                  "service": "app.service", "key": self._key_path()}})
        self.assertEqual(appmod._load_deploy_map(), {})

    def test_one_entry_with_non_numeric_port_does_not_affect_others(self):
        self._write_map({
            "bad": {"host": "h", "port": "oops", "deploy_path": "/opt/app",
                   "service": "app.service", "key": self._key_path("bad_key")},
            "good": {"host": "h2", "deploy_path": "/opt/app2", "service": "app2.service",
                    "key": self._key_path("good_key")},
        })
        m = appmod._load_deploy_map()
        self.assertEqual(set(m.keys()), {"good"})


class DeployRunTests(unittest.TestCase):
    """deploy_run() -- the actual push+restart dispatch. subprocess.run is
    mocked throughout (no real rsync/ssh); command shapes are asserted
    against deploy-target/README.md's "Protocol contract" verbatim."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-deploy-run-")
        self._orig_map_file = appmod.DEPLOY_MAP_FILE
        self._orig_keys_dir = appmod.DEPLOY_KEYS_DIR
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self._orig_subprocess_run = subprocess.run
        appmod.DEPLOY_MAP_FILE = os.path.join(self.tmp, "deploy-map.json")
        appmod.DEPLOY_KEYS_DIR = os.path.join(self.tmp, "deploy-keys")
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(appmod.DEPLOY_KEYS_DIR)
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "proj"))
        self.key_path = os.path.join(appmod.DEPLOY_KEYS_DIR, "proj_ed25519")
        with open(appmod.DEPLOY_MAP_FILE, "w") as f:
            json.dump({"proj": {"host": "10.0.0.5", "port": 2222, "user": "deploy",
                                "deploy_path": "/opt/app", "service": "app.service",
                                "key": self.key_path}}, f)
        appmod._deploy_locks.clear()

    def tearDown(self):
        appmod.DEPLOY_MAP_FILE = self._orig_map_file
        appmod.DEPLOY_KEYS_DIR = self._orig_keys_dir
        appmod.PROJECTS_DIR = self._orig_projects_dir
        subprocess.run = self._orig_subprocess_run
        appmod._deploy_locks.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_subprocess(self, push_rc=0, push_err="", restart_rc=0, restart_err=""):
        calls = []

        def _fake(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[0] == "rsync":
                return subprocess.CompletedProcess(cmd, push_rc, "", push_err)
            return subprocess.CompletedProcess(cmd, restart_rc, "", restart_err)
        subprocess.run = _fake
        return calls

    def test_no_map_entry_returns_404_and_spawns_nothing(self):
        calls = self._fake_subprocess()
        status, msg = appmod.deploy_run("does-not-exist")
        self.assertEqual(status, 404)
        self.assertIn("no deploy target", msg)
        self.assertEqual(calls, [])

    def test_success_returns_200_and_runs_push_then_restart_with_exact_argv(self):
        calls = self._fake_subprocess()
        status, msg = appmod.deploy_run("proj")
        self.assertEqual(status, 200)
        self.assertEqual(msg, "deployed")
        self.assertEqual(len(calls), 2)

        push_cmd, push_kwargs = calls[0]
        self.assertEqual(push_cmd, [
            "rsync", "-e",
            f"ssh -i {self.key_path} -o BatchMode=yes -o ConnectTimeout=10 -p 2222",
            "-a", f"{appmod.PROJECTS_DIR}/proj/", "deploy@10.0.0.5:"])
        self.assertEqual(push_kwargs.get("timeout"), 60)

        restart_cmd, restart_kwargs = calls[1]
        self.assertEqual(restart_cmd, [
            "ssh", "-i", self.key_path, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-p", "2222", "deploy@10.0.0.5", "deploy-restart"])
        self.assertEqual(restart_kwargs.get("timeout"), 30)

    def test_push_failure_returns_502_and_never_attempts_restart(self):
        calls = self._fake_subprocess(push_rc=1, push_err="Permission denied")
        status, msg = appmod.deploy_run("proj")
        self.assertEqual(status, 502)
        self.assertTrue(msg.startswith("push failed:"), msg)
        self.assertIn("Permission denied", msg)
        self.assertEqual(len(calls), 1)  # rsync only -- restart never attempted

    def test_push_ok_restart_failure_returns_502_with_distinct_message(self):
        calls = self._fake_subprocess(restart_rc=1, restart_err="systemctl timeout")
        status, msg = appmod.deploy_run("proj")
        self.assertEqual(status, 502)
        self.assertTrue(msg.startswith("push succeeded but restart failed:"), msg)
        self.assertIn("systemctl timeout", msg)
        self.assertEqual(len(calls), 2)

    def test_subprocess_raising_on_push_returns_502_not_raise(self):
        def _raise(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)
        subprocess.run = _raise
        status, msg = appmod.deploy_run("proj")  # must not raise
        self.assertEqual(status, 502)

    def test_subprocess_raising_on_restart_returns_502_distinct_message(self):
        def _fake(cmd, **kwargs):
            if cmd[0] == "rsync":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise subprocess.TimeoutExpired(cmd, 30)
        subprocess.run = _fake
        status, msg = appmod.deploy_run("proj")  # must not raise
        self.assertEqual(status, 502)
        self.assertTrue(msg.startswith("push succeeded but restart failed:"), msg)

    def test_concurrent_dispatch_for_same_project_returns_409(self):
        lock = appmod._deploy_lock_for("proj")
        self.assertTrue(lock.acquire(blocking=False))
        try:
            calls = self._fake_subprocess()
            status, msg = appmod.deploy_run("proj")
            self.assertEqual(status, 409)
            self.assertEqual(calls, [])
        finally:
            lock.release()

    def test_lock_released_after_success_allows_next_dispatch(self):
        self._fake_subprocess()
        status1, _ = appmod.deploy_run("proj")
        self.assertEqual(status1, 200)
        status2, _ = appmod.deploy_run("proj")
        self.assertEqual(status2, 200)

    def test_lock_released_after_failure_allows_next_dispatch(self):
        self._fake_subprocess(push_rc=1)
        status1, _ = appmod.deploy_run("proj")
        self.assertEqual(status1, 502)
        self._fake_subprocess()  # now succeeds
        status2, _ = appmod.deploy_run("proj")
        self.assertEqual(status2, 200)

    def test_different_projects_do_not_share_a_lock(self):
        self.assertIsNot(appmod._deploy_lock_for("proj"), appmod._deploy_lock_for("other"))
        self.assertIs(appmod._deploy_lock_for("proj"), appmod._deploy_lock_for("proj"))


class DeployNeverCalledFromPollSyncTests(unittest.TestCase):
    """AC10 (docs/spec.md "Acceptance criteria"): 2c part 1's poll/sync path
    must never call deploy_run() -- dispatch is manual-only. Drives the real
    (unmocked) _gitea_poll_one -> _gitea_sync_bg chain through a SHA change,
    same "wait for the background thread's lock to release" technique
    tests/test_gitea_poll.py's GiteaSyncBgConcurrencyTests already uses, and
    asserts appmod.deploy_run was never invoked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-poll-no-deploy-")
        self._orig_repo_map_file = appmod.GITEA_REPO_MAP_FILE
        self._orig_gitea_api = appmod._gitea_api
        self._orig_subprocess_run = subprocess.run
        self._orig_deploy_run = appmod.deploy_run
        appmod.GITEA_REPO_MAP_FILE = os.path.join(self.tmp, "gitea-repo-map.json")

    def tearDown(self):
        appmod.GITEA_REPO_MAP_FILE = self._orig_repo_map_file
        appmod._gitea_api = self._orig_gitea_api
        subprocess.run = self._orig_subprocess_run
        appmod.deploy_run = self._orig_deploy_run
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sha_change_drives_sync_but_never_deploy_run(self):
        owner_repo = "admin/proj"
        deploy_calls = []
        appmod.deploy_run = lambda name: deploy_calls.append(name) or (200, "deployed")
        appmod._gitea_api = lambda method, path, **kw: (
            200, {"commit": {"id": "newsha"}})
        subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "synced\n", "")

        appmod._gitea_poll_one(owner_repo, {"name": "proj", "remote_sha": "oldsha"})

        # Wait for _gitea_sync_bg's background thread to actually run and
        # release its lock (same polling idiom as
        # test_lock_is_released_after_a_completed_sync_so_the_next_dispatch_can_run).
        for _ in range(50):
            if appmod._gitea_sync_lock_for(owner_repo).acquire(blocking=False):
                appmod._gitea_sync_lock_for(owner_repo).release()
                break
            time.sleep(0.02)

        self.assertEqual(deploy_calls, [])


class DeployEndpointTests(unittest.TestCase):
    """End-to-end HTTP tests against a real ThreadingHTTPServer instance --
    same technique as tests/test_gitea.py's GiteaEndpointTests."""

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
        self.tmp = tempfile.mkdtemp(prefix="switchboard-deploy-endpoint-")
        self._orig_map_file = appmod.DEPLOY_MAP_FILE
        self._orig_keys_dir = appmod.DEPLOY_KEYS_DIR
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self._orig_deploy_run = appmod.deploy_run
        appmod.DEPLOY_MAP_FILE = os.path.join(self.tmp, "deploy-map.json")
        appmod.DEPLOY_KEYS_DIR = os.path.join(self.tmp, "deploy-keys")
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(appmod.DEPLOY_KEYS_DIR)
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "proj"))

    def tearDown(self):
        appmod.DEPLOY_MAP_FILE = self._orig_map_file
        appmod.DEPLOY_KEYS_DIR = self._orig_keys_dir
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod.deploy_run = self._orig_deploy_run
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_map(self, entry, key_name="proj_key"):
        entry = dict(entry)
        entry.setdefault("key", os.path.join(appmod.DEPLOY_KEYS_DIR, key_name))
        with open(appmod.DEPLOY_MAP_FILE, "w") as f:
            json.dump({"proj": entry}, f)

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

    def test_status_includes_deploy_fields_without_key_for_mapped_project(self):
        self._write_map({"host": "10.0.0.5", "deploy_path": "/opt/app", "service": "app.service"})
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(by_name["proj"]["deploy"],
                         {"host": "10.0.0.5", "deploy_path": "/opt/app", "service": "app.service"})
        self.assertNotIn("key", by_name["proj"]["deploy"])

    def test_status_omits_deploy_field_for_unmapped_project(self):
        cookie = self._login()  # no deploy-map.json written at all
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertNotIn("deploy", by_name["proj"])

    def test_status_omits_deploy_field_for_entry_with_key_outside_keys_dir(self):
        outside = os.path.join(self.tmp, "outside", "key")
        self._write_map({"host": "h", "deploy_path": "/opt/app", "service": "app.service",
                         "key": outside})
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertNotIn("deploy", by_name["proj"])

    def test_deploy_post_without_map_entry_returns_404_no_crash(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/instance/proj/deploy", cookie, {"code": code})
        self.assertEqual(status, 404)
        self.assertIn("no deploy target", payload.get("message", ""))

    def test_deploy_post_unknown_instance_returns_404(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/instance/nope/deploy", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_deploy_post_without_code_returns_428(self):
        self._write_map({"host": "h", "deploy_path": "/opt/app", "service": "app.service"})
        cookie = self._login()
        status, _ = self._post("/instance/proj/deploy", cookie)
        self.assertEqual(status, 428)

    def test_deploy_post_dispatches_and_returns_success_result(self):
        self._write_map({"host": "h", "deploy_path": "/opt/app", "service": "app.service"})
        appmod.deploy_run = lambda name: (200, "deployed")
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/instance/proj/deploy", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["message"], "deployed")

    def test_deploy_post_surfaces_failure_status_and_ok_false(self):
        self._write_map({"host": "h", "deploy_path": "/opt/app", "service": "app.service"})
        appmod.deploy_run = lambda name: (502, "push failed: nope")
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/instance/proj/deploy", cookie, {"code": code})
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["message"], "push failed: nope")

    def test_status_survives_non_numeric_port_and_keeps_other_projects_intact(self):
        # Regression test for a reviewer-found bug: a hand-edited, non-numeric
        # "port" in one project's entry must not crash /status for every
        # project -- it should just drop that one entry (mirrors
        # test_entry_with_key_outside_keys_dir_is_dropped's "treat as
        # absent, not a crash" contract, at the /status level).
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "proj2"))
        with open(appmod.DEPLOY_MAP_FILE, "w") as f:
            json.dump({
                "proj": {"host": "h", "port": "oops", "deploy_path": "/opt/app",
                        "service": "app.service",
                        "key": os.path.join(appmod.DEPLOY_KEYS_DIR, "proj_key")},
                "proj2": {"host": "h2", "deploy_path": "/opt/app2", "service": "app2.service",
                         "key": os.path.join(appmod.DEPLOY_KEYS_DIR, "proj2_key")},
            }, f)
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertNotIn("deploy", by_name["proj"])
        self.assertEqual(by_name["proj2"]["deploy"],
                         {"host": "h2", "deploy_path": "/opt/app2", "service": "app2.service"})

    def test_deploy_post_surfaces_409_from_concurrency_guard(self):
        self._write_map({"host": "h", "deploy_path": "/opt/app", "service": "app.service"})
        appmod.deploy_run = lambda name: (409, "a deploy for this project is already in progress")
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/instance/proj/deploy", cookie, {"code": code})
        self.assertEqual(status, 409)
        self.assertFalse(payload["ok"])


INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


class InstallShDeployMapBlockTests(unittest.TestCase):
    """install.sh's two new *unconditional* blocks (docs/spec.md "Proposed
    approach" #7): deploy-keys/ dir creation and deploy-map.json
    copy-if-absent + set_env. Extracted verbatim from install.sh's real
    source (same technique tests/test_deploy_target.py's own
    _build_deploy_target_block_harness uses for --with-deploy-target) and
    run with SVC_USER set to this test process's own user -- unlike that
    file's harness, neither block here ever calls `prompt` (no interactive
    read), so a plain subprocess with no pty and no sudo is enough; `chown`
    to your own already-owned uid/gid needs no elevated privilege either."""

    def setUp(self):
        with open(INSTALL_SH) as f:
            self.source = f.read()
        self.helpers = _extract_between(self.source, "interactive() {", "random_token() {")
        self.keys_block = _extract_between(
            self.source,
            'chown "$RUN_USER:$RUN_USER" "$PROJECTS_DIR"\n\n# Switchboard-side deploy dispatch',
            'if [ "$WITH_CODE_SERVER" -eq 1 ]')
        # Put the marker's own first comment line back -- _extract_between's
        # start index lands right after the literal search string above.
        self.keys_block = "# Switchboard-side deploy dispatch" + self.keys_block
        self.map_block = _extract_between(
            self.source,
            'set_env "$ENV_FILE" UPLOAD_STAGING_TTL_SECONDS "1800"\n\n# Switchboard-side deploy dispatch',
            'chown "$SVC_USER:$SVC_USER" "$ENV_FILE"')
        self.map_block = "# Switchboard-side deploy dispatch" + self.map_block
        self.tmp = tempfile.mkdtemp(prefix="aidswb-deploy-map-block-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, config_dir, env_file, extra=""):
        whoami = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
        script = f"""#!/usr/bin/env bash
set -euo pipefail
YES=1
CONFIG_DIR={config_dir}
SVC_USER={whoami}
ENV_FILE={env_file}
{self.helpers}
{extra}
{self.keys_block}
{self.map_block}
"""
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_deploy_keys_dir_created_mode_700(self):
        config_dir = os.path.join(self.tmp, "config")
        os.makedirs(config_dir)
        env_file = os.path.join(self.tmp, "switchboard.env")
        open(env_file, "w").close()
        r = self._run(config_dir, env_file)
        self.assertEqual(r.returncode, 0, r.stderr)
        keys_dir = os.path.join(config_dir, "deploy-keys")
        self.assertTrue(os.path.isdir(keys_dir))
        self.assertEqual(oct(os.stat(keys_dir).st_mode & 0o777), "0o700")

    def test_deploy_map_json_created_as_empty_object_when_absent(self):
        config_dir = os.path.join(self.tmp, "config")
        os.makedirs(config_dir)
        env_file = os.path.join(self.tmp, "switchboard.env")
        open(env_file, "w").close()
        r = self._run(config_dir, env_file)
        self.assertEqual(r.returncode, 0, r.stderr)
        map_file = os.path.join(config_dir, "deploy-map.json")
        with open(map_file) as f:
            self.assertEqual(json.load(f), {})

    def test_rerun_does_not_touch_an_existing_hand_edited_map(self):
        config_dir = os.path.join(self.tmp, "config")
        os.makedirs(config_dir)
        env_file = os.path.join(self.tmp, "switchboard.env")
        open(env_file, "w").close()
        map_file = os.path.join(config_dir, "deploy-map.json")
        hand_edited = '{\n  "my-project": {"host": "10.0.0.5"}\n}\n'
        with open(map_file, "w") as f:
            f.write(hand_edited)
        r = self._run(config_dir, env_file)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(map_file) as f:
            self.assertEqual(f.read(), hand_edited, "must be byte-for-byte unchanged")

    def test_env_file_gets_deploy_map_file_and_deploy_keys_dir_vars(self):
        config_dir = os.path.join(self.tmp, "config")
        os.makedirs(config_dir)
        env_file = os.path.join(self.tmp, "switchboard.env")
        open(env_file, "w").close()
        r = self._run(config_dir, env_file)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(env_file) as f:
            content = f.read()
        self.assertIn(f"DEPLOY_MAP_FILE={config_dir}/deploy-map.json", content)
        self.assertIn(f"DEPLOY_KEYS_DIR={config_dir}/deploy-keys", content)

    def test_rerun_reasserts_deploy_keys_permissions(self):
        config_dir = os.path.join(self.tmp, "config")
        os.makedirs(config_dir)
        env_file = os.path.join(self.tmp, "switchboard.env")
        open(env_file, "w").close()
        keys_dir = os.path.join(config_dir, "deploy-keys")
        os.makedirs(keys_dir)
        os.chmod(keys_dir, 0o755)  # simulate drifted permissions
        r = self._run(config_dir, env_file)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(oct(os.stat(keys_dir).st_mode & 0o777), "0o700")


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to provision a real throwaway "
                     "deploy-target receiver (system user, sudoers, systemd)")
@unittest.skipUnless(HAVE_SSHD_ON_LOCALHOST,
                     "needs a real sshd reachable on 127.0.0.1:22 to exercise "
                     "deploy_run()'s real rsync/ssh calls")
class PrivilegedDeployRunEndToEndTests(unittest.TestCase):
    """Provisions a real (throwaway) deploy-target receiver on this machine
    -- a system user, the two installed scripts, a sudoers rule, and a real
    systemd unit to restart -- using the exact line shapes install.sh
    generates, then calls the real appmod.deploy_run() (subprocess.run is
    NOT mocked here) against it over real ssh/rsync to 127.0.0.1. This is
    the one place in this file that proves 2b's caller side actually speaks
    2c-2a's receiver protocol correctly, not just that it builds the right
    argv (DeployRunTests) or that the route wires it up (DeployEndpointTests).
    Never uses the literal username "deploy" (test_deploy_target.py's own
    PrivilegedEndToEndTests already claims "aidswbtest" -- this class uses a
    different throwaway name so both test files can run in the same process/
    box without colliding)."""

    TEST_USER = "aidswbdeploy2b"

    @classmethod
    def setUpClass(cls):
        if subprocess.run(["id", cls.TEST_USER], capture_output=True).returncode == 0:
            raise unittest.SkipTest(
                f"a real '{cls.TEST_USER}' user already exists on this machine -- refusing to touch it")
        cls.base = tempfile.mkdtemp(prefix="aidswb-deploy-run-e2e-")
        os.chmod(cls.base, 0o755)
        cls.deploy_path = os.path.join(cls.base, "dest")
        cls.key = os.path.join(cls.base, "id_ed25519")
        cls.service_name = "aidswbdeploy2b.service"

        subprocess.run(["sudo", "useradd", "-m", "-d", f"/home/{cls.TEST_USER}",
                       "-s", "/bin/sh", cls.TEST_USER], check=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", cls.key, "-N", "", "-q"], check=True)
        with open(cls.key + ".pub") as f:
            cls.pubkey = f.read().strip()

        subprocess.run(["sudo", "mkdir", "-p", cls.deploy_path], check=True)
        subprocess.run(["sudo", "chown", f"{cls.TEST_USER}:{cls.TEST_USER}", cls.deploy_path], check=True)

        subprocess.run(["sudo", "mkdir", "-p", f"/home/{cls.TEST_USER}/.ssh"], check=True)
        ak_line = ('command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",'
                  f'no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty {cls.pubkey}\n')
        subprocess.run(["sudo", "bash", "-c", f"cat > /home/{cls.TEST_USER}/.ssh/authorized_keys"],
                       input=ak_line, text=True, check=True)
        subprocess.run(["sudo", "chown", "-R", f"{cls.TEST_USER}:{cls.TEST_USER}",
                       f"/home/{cls.TEST_USER}/.ssh"], check=True)
        subprocess.run(["sudo", "chmod", "700", f"/home/{cls.TEST_USER}/.ssh"], check=True)
        subprocess.run(["sudo", "chmod", "600", f"/home/{cls.TEST_USER}/.ssh/authorized_keys"], check=True)

        subprocess.run(["sudo", "install", "-m", "755", WRAPPER,
                       "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh"], check=True)
        subprocess.run(["sudo", "install", "-m", "755", RESTART,
                       "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh"], check=True)

        # Only this test's own deploy-target.env belongs to us. On a box where
        # the switchboard is actually installed, /etc/ai-dev-switchboard already
        # holds the live switchboard.env, engines.d/ and deploy-keys/ -- so record
        # whether we created the directory, and tear down accordingly.
        cls._config_dir_created = not os.path.isdir("/etc/ai-dev-switchboard")
        subprocess.run(["sudo", "mkdir", "-p", "/etc/ai-dev-switchboard"], check=True)
        cls.config_file = "/etc/ai-dev-switchboard/deploy-target.env"
        cls._write_config(cls.deploy_path, cls.service_name)

        cls.sudoers_file = "/etc/sudoers.d/ai-dev-switchboard-deploy-target-2b-test"
        subprocess.run(["sudo", "bash", "-c", f"cat > {cls.sudoers_file}"],
                       input=(f"{cls.TEST_USER} ALL=(root) NOPASSWD: "
                              "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh\n"),
                       text=True, check=True)
        subprocess.run(["sudo", "chmod", "440", cls.sudoers_file], check=True)
        v = subprocess.run(["sudo", "visudo", "-cf", cls.sudoers_file], capture_output=True, text=True)
        assert v.returncode == 0, v.stderr

        cls.unit_file = f"/etc/systemd/system/{cls.service_name}"
        subprocess.run(["sudo", "bash", "-c", f"cat > {cls.unit_file}"], input=(
            "[Unit]\nDescription=ai-dev-switchboard deploy_run() e2e test service\n\n"
            "[Service]\nType=simple\nExecStart=/bin/sleep infinity\n\n"
            "[Install]\nWantedBy=multi-user.target\n"), text=True, check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "start", cls.service_name], check=True)

        # deploy_run() deliberately never overrides StrictHostKeyChecking
        # (docs/spec.md "Open questions": the operator is expected to have
        # already trusted each target host once by hand, same precedent
        # host_run() already carries) -- so, to exercise the real shipped
        # code path rather than a weakened one, seed this process's own
        # ~/.ssh/known_hosts with 127.0.0.1's real host key first (exactly
        # the one-time manual step a real operator would do), and restore
        # its exact prior contents in tearDownClass.
        cls._known_hosts = os.path.expanduser("~/.ssh/known_hosts")
        os.makedirs(os.path.dirname(cls._known_hosts), mode=0o700, exist_ok=True)
        if os.path.exists(cls._known_hosts):
            with open(cls._known_hosts) as f:
                cls._known_hosts_orig = f.read()
        else:
            cls._known_hosts_orig = None
        scan = subprocess.run(["ssh-keyscan", "-p", "22", "127.0.0.1"],
                              capture_output=True, text=True, timeout=10)
        with open(cls._known_hosts, "a") as f:
            f.write(scan.stdout)

    @classmethod
    def _write_config(cls, deploy_path, service_name):
        subprocess.run(["sudo", "bash", "-c", f"cat > {cls.config_file}"],
                       input=f"DEPLOY_PATH={deploy_path}\nDEPLOY_SERVICE_NAME={service_name}\n",
                       text=True, check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["sudo", "systemctl", "stop", cls.service_name], capture_output=True)
        subprocess.run(["sudo", "rm", "-f", cls.unit_file])
        subprocess.run(["sudo", "systemctl", "daemon-reload"])
        subprocess.run(["sudo", "rm", "-f", cls.sudoers_file])
        # Never rm -rf the config dir: it is the live install location on any
        # box that actually runs the switchboard. Remove only the file this
        # test wrote, then the directory itself only if we created it (plain
        # rmdir, so a non-empty real install is left alone either way).
        subprocess.run(["sudo", "rm", "-f", "/etc/ai-dev-switchboard/deploy-target.env"])
        if getattr(cls, "_config_dir_created", False):
            subprocess.run(["sudo", "rmdir", "/etc/ai-dev-switchboard"],
                           capture_output=True)
        subprocess.run(["sudo", "rm", "-f",
                       "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",
                       "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh"])
        subprocess.run(["sudo", "pkill", "-9", "-u", cls.TEST_USER], capture_output=True)
        time.sleep(0.3)
        subprocess.run(["sudo", "userdel", "-r", cls.TEST_USER], capture_output=True)
        shutil.rmtree(cls.base, ignore_errors=True)

        if cls._known_hosts_orig is None:
            try:
                os.remove(cls._known_hosts)
            except OSError:
                pass
        else:
            with open(cls._known_hosts, "w") as f:
                f.write(cls._known_hosts_orig)

    def setUp(self):
        self._write_config(self.deploy_path, self.service_name)
        subprocess.run(["sudo", "bash", "-c", f"rm -rf {self.deploy_path}/*"], check=True)
        self.tmp = tempfile.mkdtemp(prefix="switchboard-deploy-run-e2e-map-")
        self._orig_map_file = appmod.DEPLOY_MAP_FILE
        self._orig_keys_dir = appmod.DEPLOY_KEYS_DIR
        self._orig_projects_dir = appmod.PROJECTS_DIR
        appmod.DEPLOY_KEYS_DIR = os.path.dirname(self.key)  # self.base -- key already lives there
        appmod.DEPLOY_MAP_FILE = os.path.join(self.tmp, "deploy-map.json")
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "proj"))
        with open(os.path.join(appmod.PROJECTS_DIR, "proj", "hello.txt"), "w") as f:
            f.write("hello from deploy_run() e2e test\n")
        appmod._deploy_locks.clear()

    def tearDown(self):
        appmod.DEPLOY_MAP_FILE = self._orig_map_file
        appmod.DEPLOY_KEYS_DIR = self._orig_keys_dir
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod._deploy_locks.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_map(self, port=22):
        with open(appmod.DEPLOY_MAP_FILE, "w") as f:
            json.dump({"proj": {"host": "127.0.0.1", "port": port, "user": self.TEST_USER,
                                "deploy_path": self.deploy_path, "service": self.service_name,
                                "key": self.key}}, f)

    # -- Acceptance criterion: reachable target, valid entry -> push lands,
    # service restarts, 200 "deployed".
    def test_success_pushes_file_and_restarts_service(self):
        self._write_map()
        before = subprocess.run(
            ["sudo", "systemctl", "show", "-p", "ActiveEnterTimestamp", self.service_name],
            capture_output=True, text=True, check=True).stdout.strip()
        time.sleep(1.1)  # ActiveEnterTimestamp has 1-second resolution

        status, msg = appmod.deploy_run("proj")

        self.assertEqual(status, 200, msg)
        self.assertEqual(msg, "deployed")
        landed = os.path.join(self.deploy_path, "hello.txt")
        content = subprocess.run(["sudo", "cat", landed], capture_output=True,
                                 text=True, check=True).stdout
        self.assertEqual(content, "hello from deploy_run() e2e test\n")
        after = subprocess.run(
            ["sudo", "systemctl", "show", "-p", "ActiveEnterTimestamp", self.service_name],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertNotEqual(before, after, "service must have actually restarted")

    # -- Acceptance criterion: push succeeds, restart fails (misconfigured
    # DEPLOY_SERVICE_NAME on the target) -> 502, distinct message.
    def test_restart_failure_on_target_surfaces_distinct_502(self):
        self._write_config(self.deploy_path, "aidswbdeploy2b-nonexistent.service")
        self._write_map()
        try:
            status, msg = appmod.deploy_run("proj")
        finally:
            self._write_config(self.deploy_path, self.service_name)
        self.assertEqual(status, 502)
        self.assertTrue(msg.startswith("push succeeded but restart failed:"), msg)
        # The push itself must still have landed -- restart failing doesn't
        # mean the push didn't happen.
        landed = os.path.join(self.deploy_path, "hello.txt")
        exists = subprocess.run(["sudo", "test", "-f", landed])
        self.assertEqual(exists.returncode, 0)

    # -- Acceptance criterion: unreachable target -> 502 within roughly the
    # configured timeouts, no indefinite hang.
    def test_unreachable_target_returns_502_no_hang(self):
        # An address that's routable but refuses the connection immediately
        # (nothing listens on 127.0.0.1:1) -- fails fast rather than
        # actually waiting out ConnectTimeout, but proves the same code
        # path a slower-to-fail unreachable host would also take.
        self._write_map(port=1)
        started = time.time()
        status, msg = appmod.deploy_run("proj")
        elapsed = time.time() - started
        self.assertEqual(status, 502)
        self.assertTrue(msg.startswith("push failed:"), msg)
        self.assertLess(elapsed, 15, "must fail fast, not hang")


if __name__ == "__main__":
    unittest.main()
