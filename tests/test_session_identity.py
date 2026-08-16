"""
Tests for the concurrent-sessions-per-project session-identity backend
(parts 1 and 2, docs/spec.md): `_new_session_id()`'s shape/uniqueness, the
`_sessions` registry (`_sessions_add`/`_sessions_pop`/`active_sessions()`),
`instance_start()`/`instance_stop_session()`/`_reap_dead_state()`'s
per-session semantics, `/status`'s `sessions` array, the
`POST /instance/<name>/spawn` + `POST /instance/<name>/session/<id>/stop`
routes, and `smoke_check_run()`'s deterministic newest-session URL
resolution. Part 1's temporary `instance_stop()` legacy bulk-stop helper
and the `/on`/`/off` back-compat shim it backed (along with `/status`'s
back-compat `on`/`engine`/`url` singular fields) were removed in part 2
(docs/spec.md "Routes" §6) once the frontend stopped calling them -- this
file no longer exercises any of that removed surface.

Three tiers, same split as tests/test_teams_headless.py:

Tier 1 -- pure unit, no subprocess, no tmux: `_new_session_id()`'s shape and
uniqueness, `_sessions_add`/`_sessions_pop`/`active_sessions()` with
`tmux_has` monkeypatched (no real tmux needed to prove the registry's own
filtering/ordering logic).

Tier 2 -- real tmux (TMUX patched down to ["tmux"], same technique as
tests/test_teams_headless.py's `_patch_tmux()`), ttyd's own real
`sudo -u RUN_USER ttyd ...` spawn faked out (subprocess.Popen patched --
see `_patch_ttyd_popen()`) since sudo/ttyd aren't available/safe to invoke
for real in a test sandbox, while `_ttyd_port()`'s own real (pure) port
allocation and `_publish()`/`_unpublish()`'s real (PUBLISH_MODE=none, so
no-op) behavior are left untouched: instance_start()/instance_stop_session()
/_reap_dead_state()'s actual tmux-session lifecycle.

Tier 3 -- end-to-end HTTP tests against a real ThreadingHTTPServer instance,
same technique as tests/test_smoke_check.py's SmokeCheckEndpointTests /
tests/test_deploy_dispatch.py's DeployEndpointTests.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_session_identity.py
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-session-identity-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

# Same process-scoping reasoning as tests/test_teams_headless.py's own
# _RUN_ID_SCOPE -- project/session names below are otherwise a bare,
# unscoped value this process doesn't own.
_SCOPE = f"p{os.getpid()}"


def _write_engine_file(dirpath, filename, body):
    with open(os.path.join(dirpath, filename), "w") as f:
        f.write(textwrap.dedent(body))


def _patch_tmux(testcase, argv):
    orig = appmod.TMUX
    appmod.TMUX = argv
    testcase.addCleanup(lambda: setattr(appmod, "TMUX", orig))


class _FakeTtydProc:
    def __init__(self, *a, **kw):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def poll(self):
        return None if not self.terminated else 0


def _patch_ttyd_popen(testcase):
    """Fakes _ttyd_start()'s real `sudo -u RUN_USER ttyd ...` subprocess
    spawn (not available/safe to invoke for real in a test sandbox) while
    leaving _ttyd_port()'s own real (pure) port-allocation bookkeeping and
    _publish()/_unpublish()'s real PUBLISH_MODE=none no-op behavior
    untouched -- mocking only the one non-pure boundary _ttyd_start()
    itself crosses. Replaces the whole function (not subprocess.Popen
    globally) because tmux_has()/instance_start()'s own real tmux calls go
    through the exact same subprocess.run/Popen machinery and must keep
    working for real in these tests -- same "mock the external dependency,
    exercise the real unit" approach tests/test_teams_headless.py's
    _patch_tmux() already established for TMUX itself."""
    orig_start = appmod._ttyd_start
    orig_stop = appmod._ttyd_stop

    def fake_start(name, session):
        port = appmod._ttyd_port(name)
        path = f"/term/{urllib.parse.quote(name)}"
        appmod._ttyd_procs[name] = _FakeTtydProc()
        appmod._ttyd_urls[name] = appmod._publish(path, port)

    def fake_stop(name):
        proc = appmod._ttyd_procs.pop(name, None)
        if proc is not None:
            proc.terminate()
        if name in appmod._ttyd_ports:
            appmod._unpublish(f"/term/{urllib.parse.quote(name)}")
        appmod._ttyd_urls.pop(name, None)

    appmod._ttyd_start = fake_start
    appmod._ttyd_stop = fake_stop

    def _restore():
        appmod._ttyd_start = orig_start
        appmod._ttyd_stop = orig_stop
    testcase.addCleanup(_restore)


# ─── Tier 1: pure unit -- _new_session_id() ────────────────────────────────

class NewSessionIdTests(unittest.TestCase):
    def test_shape_is_engine_project_timestamp_hex(self):
        sid = appmod._new_session_id("claude", "myproj")
        self.assertRegex(sid, r"^claude-myproj-\d+-[0-9a-f]{6}$")

    def test_starts_with_the_exact_engine_project_prefix(self):
        # Preserves the reserved-engine-name-prefix collision guard
        # (docs/spec.md "The reserved-name collision guard") -- session
        # names must still start with f"{engine_name}-{project_name}".
        sid = appmod._new_session_id("codex", "some-project")
        self.assertTrue(sid.startswith("codex-some-project-"))

    def test_two_calls_in_the_same_process_are_unique(self):
        ids = {appmod._new_session_id("claude", "myproj") for _ in range(500)}
        self.assertEqual(len(ids), 500)

    def test_different_projects_never_collide(self):
        a = appmod._new_session_id("claude", "proj-a")
        b = appmod._new_session_id("claude", "proj-b")
        self.assertNotEqual(a, b)


# ─── Tier 1: pure unit -- the _sessions registry ───────────────────────────

class SessionsRegistryUnitTests(unittest.TestCase):
    def setUp(self):
        self._orig_sessions = dict(appmod._sessions)
        self._orig_tmux_has = appmod.tmux_has
        appmod._sessions.clear()

    def tearDown(self):
        appmod._sessions.clear()
        appmod._sessions.update(self._orig_sessions)
        appmod.tmux_has = self._orig_tmux_has

    def test_active_sessions_filters_to_the_given_project_only(self):
        appmod._sessions_add("s1", "proj", "claude")
        appmod._sessions_add("s2", "other", "claude")
        appmod.tmux_has = lambda sid: True
        result = appmod.active_sessions("proj")
        self.assertEqual(result, [{"session_id": "s1", "engine": "claude"}])

    def test_active_sessions_excludes_dead_tmux_sessions(self):
        appmod._sessions_add("alive", "proj", "claude")
        appmod._sessions_add("dead", "proj", "codex")
        appmod.tmux_has = lambda sid: sid == "alive"
        result = appmod.active_sessions("proj")
        self.assertEqual(result, [{"session_id": "alive", "engine": "claude"}])

    def test_active_sessions_preserves_insertion_order_oldest_first(self):
        appmod._sessions_add("a", "proj", "e1")
        appmod._sessions_add("b", "proj", "e2")
        appmod._sessions_add("c", "proj", "e3")
        appmod.tmux_has = lambda sid: True
        result = appmod.active_sessions("proj")
        self.assertEqual([r["session_id"] for r in result], ["a", "b", "c"])

    def test_sessions_pop_of_unknown_id_returns_none(self):
        self.assertIsNone(appmod._sessions_pop("does-not-exist"))

    def test_sessions_pop_removes_and_returns_the_entry(self):
        appmod._sessions_add("x", "proj", "claude")
        popped = appmod._sessions_pop("x")
        self.assertEqual(popped, {"project": "proj", "engine": "claude"})
        self.assertNotIn("x", appmod._sessions)

    def test_sessions_pop_is_idempotent_second_pop_is_a_clean_no_op(self):
        appmod._sessions_add("x", "proj", "claude")
        appmod._sessions_pop("x")
        self.assertIsNone(appmod._sessions_pop("x"))

    def test_sessions_ids_returns_every_registered_id_across_all_projects(self):
        # Fix-up regression (docs/test-review.md finding #3): _sessions_ids()
        # is the lock-guarded 4th accessor _reap_dead_state()'s global sweep
        # now goes through, instead of reading `list(_sessions)` directly.
        appmod._sessions_add("a", "proj1", "claude")
        appmod._sessions_add("b", "proj2", "codex")
        self.assertEqual(set(appmod._sessions_ids()), {"a", "b"})


# ─── Tier 1: pure unit -- engines-dict threading into URL resolution ──────

class ResolveSessionUrlEnginesThreadingTests(unittest.TestCase):
    """Fix-up regression (docs/test-review.md finding #4): _resolve_session_
    url()/_latest_session_url_for_project() must use a CALLER-SUPPLIED
    engines dict when given one, not silently reload load_engines() (a full
    uncached ENGINES_DIR scan+parse) underneath -- proven here by making a
    fresh load_engines() call raise, then confirming resolution still
    succeeds using only the dict passed in."""

    def setUp(self):
        self._orig_ttyd_urls = dict(appmod._ttyd_urls)
        self._orig_load_engines = appmod.load_engines

    def tearDown(self):
        appmod._ttyd_urls.clear()
        appmod._ttyd_urls.update(self._orig_ttyd_urls)
        appmod.load_engines = self._orig_load_engines

    def test_resolve_session_url_does_not_reload_engines_when_a_dict_is_passed(self):
        def _boom():
            raise AssertionError("load_engines() must not be called when engines is provided")
        appmod.load_engines = _boom
        appmod._ttyd_urls["sid-x"] = "http://example.invalid/x"
        fake_engine = appmod.Engine("claude", "Claude", "sleep 100", None, [])
        result = appmod._resolve_session_url("sid-x", "claude", {"claude": fake_engine})
        self.assertEqual(result, "http://example.invalid/x")

    def test_resolve_session_url_falls_back_to_loading_engines_when_none_passed(self):
        appmod._ttyd_urls["sid-y"] = "http://example.invalid/y"
        appmod.load_engines = lambda: {
            "claude": appmod.Engine("claude", "Claude", "sleep 100", None, [])}
        result = appmod._resolve_session_url("sid-y", "claude")
        self.assertEqual(result, "http://example.invalid/y")


# ─── Tier 2: real tmux, ttyd's Popen faked ─────────────────────────────────

class InstanceLifecycleRealTmuxTests(unittest.TestCase):
    """Exercises instance_start()/instance_stop_session()/
    _reap_dead_state() against real tmux sessions -- same real-tmux
    technique as tests/test_teams_headless.py's RealTmuxHeadlessTests."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-si-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-si-projects-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_projects_dir = appmod.PROJECTS_DIR
        appmod.ENGINES_DIR = self.engines_dir
        appmod.PROJECTS_DIR = self.projects_dir
        _patch_tmux(self, ["tmux"])
        _patch_ttyd_popen(self)
        _write_engine_file(self.engines_dir, "claude.engine", """\
            LABEL=Claude
            CMD=sleep 100
            """)
        _write_engine_file(self.engines_dir, "codex.engine", """\
            LABEL=Codex
            CMD=sleep 100
            """)
        self.project = f"proj-{_SCOPE}"
        os.makedirs(os.path.join(self.projects_dir, self.project))
        self._spawned = []

    def tearDown(self):
        for sid in self._spawned:
            import subprocess
            subprocess.run(["tmux", "kill-session", "-t", sid], capture_output=True)
        appmod.ENGINES_DIR = self._orig_engines_dir
        appmod.PROJECTS_DIR = self._orig_projects_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def _start(self, engine="claude"):
        sid = appmod.instance_start(self.project, engine)
        self.assertIsNotNone(sid)
        self._spawned.append(sid)
        return sid

    def test_spawn_same_engine_twice_creates_two_independent_live_sessions(self):
        sid1 = self._start("claude")
        sid2 = self._start("claude")
        self.assertNotEqual(sid1, sid2)
        self.assertTrue(appmod.tmux_has(sid1))
        self.assertTrue(appmod.tmux_has(sid2))
        live = {s["session_id"] for s in appmod.active_sessions(self.project)}
        self.assertEqual(live, {sid1, sid2})

    def test_spawn_different_engines_both_tracked_independently(self):
        sid1 = self._start("claude")
        sid2 = self._start("codex")
        by_id = {s["session_id"]: s["engine"] for s in appmod.active_sessions(self.project)}
        self.assertEqual(by_id, {sid1: "claude", sid2: "codex"})

    def test_each_session_gets_its_own_ttyd_port(self):
        sid1 = self._start("claude")
        sid2 = self._start("claude")
        self.assertIn(sid1, appmod._ttyd_ports)
        self.assertIn(sid2, appmod._ttyd_ports)
        self.assertNotEqual(appmod._ttyd_ports[sid1], appmod._ttyd_ports[sid2])

    def test_stop_session_tears_down_only_the_targeted_session(self):
        sid1 = self._start("claude")
        sid2 = self._start("claude")
        appmod.instance_stop_session(sid1)
        self.assertFalse(appmod.tmux_has(sid1))
        self.assertTrue(appmod.tmux_has(sid2))
        # _ttyd_ports itself is the never-reclaimed port allocator
        # (docs/spec.md Non-goals: "Not fixing the existing 'ttyd/code
        # ports grow forever, never reclaimed' limitation") -- only
        # _ttyd_procs/_ttyd_urls are torn down on stop.
        self.assertNotIn(sid1, appmod._ttyd_procs)
        self.assertNotIn(sid1, appmod._ttyd_urls)
        self.assertIn(sid2, appmod._ttyd_procs)
        self.assertIn(sid2, appmod._ttyd_urls)
        live = {s["session_id"] for s in appmod.active_sessions(self.project)}
        self.assertEqual(live, {sid2})

    def test_stop_session_on_an_already_gone_id_is_a_clean_no_op(self):
        appmod.instance_stop_session(f"never-existed-{_SCOPE}")  # must not raise

    def test_reap_dead_state_prunes_exactly_the_dead_sessions_bookkeeping(self):
        import subprocess
        sid1 = self._start("claude")
        sid2 = self._start("claude")
        subprocess.run(["tmux", "kill-session", "-t", sid1], capture_output=True)
        self.assertFalse(appmod.tmux_has(sid1))
        self.assertTrue(appmod.tmux_has(sid2))
        appmod._reap_dead_state()
        self.assertNotIn(sid1, appmod._sessions)
        self.assertIn(sid2, appmod._sessions)
        self.assertNotIn(sid1, appmod._ttyd_procs)
        self.assertNotIn(sid1, appmod._ttyd_urls)
        self.assertIn(sid2, appmod._ttyd_procs)
        self.assertIn(sid2, appmod._ttyd_urls)
        live = {s["session_id"] for s in appmod.active_sessions(self.project)}
        self.assertEqual(live, {sid2})

    def test_reap_dead_state_cleans_orphaned_bookkeeping_not_backed_by_sessions(self):
        # Fix-up regression (docs/test-review.md finding #2): the
        # independent second sweep over _ttyd_urls/_ttyd_procs/
        # _session_urls must clean up an entry whose session_id was NEVER
        # (or is no longer) present in appmod._sessions at all, as long as
        # its tmux session is dead -- not just entries the primary
        # _sessions-driven sweep already knows about. Without this sweep,
        # such an entry (e.g. left behind by a race, or any future bug)
        # would be permanently unreachable by self-healing.
        orphan_id = f"orphan-{_SCOPE}"
        appmod._ttyd_urls[orphan_id] = "http://example.invalid/orphan"
        appmod._ttyd_procs[orphan_id] = _FakeTtydProc()
        appmod._session_urls[orphan_id] = "http://example.invalid/orphan"
        self.assertNotIn(orphan_id, appmod._sessions)
        self.assertFalse(appmod.tmux_has(orphan_id))
        appmod._reap_dead_state()
        self.assertNotIn(orphan_id, appmod._ttyd_urls)
        self.assertNotIn(orphan_id, appmod._ttyd_procs)
        self.assertNotIn(orphan_id, appmod._session_urls)

    def test_instance_start_does_not_register_a_session_that_never_came_up(self):
        # Fix-up regression (docs/test-review.md finding #1): _sessions_add()
        # must only run AFTER tmux_has() confirms the new session actually
        # exists, not before subprocess.run -- closing the window where a
        # concurrent reap sweep could see a registered-but-not-yet-real
        # session_id and tear it down out from under a still-in-flight
        # instance_start(), permanently leaking the tmux+ttyd process it
        # goes on to create. Simulated by forcing tmux_has() to always
        # report False (as if tmux never actually brought the session up);
        # a real tmux session named `forced_id` may still get created by
        # the real `tmux new-session` call underneath (that part of the
        # mechanism isn't being faked), so it's killed in cleanup
        # regardless of whether it exists -- tolerant, same as every other
        # teardown in this file.
        forced_id = f"claude-{self.project}-forced-{_SCOPE}"
        orig_new_id = appmod._new_session_id
        appmod._new_session_id = lambda engine_name, project_name: forced_id
        self.addCleanup(lambda: setattr(appmod, "_new_session_id", orig_new_id))
        orig_tmux_has = appmod.tmux_has
        appmod.tmux_has = lambda sid: False
        self.addCleanup(lambda: setattr(appmod, "tmux_has", orig_tmux_has))
        import subprocess
        self.addCleanup(lambda: subprocess.run(
            ["tmux", "kill-session", "-t", forced_id], capture_output=True))

        result = appmod.instance_start(self.project, "claude")

        self.assertIsNone(result)
        self.assertNotIn(forced_id, appmod._sessions)

    def test_instance_start_unknown_engine_returns_none(self):
        self.assertIsNone(appmod.instance_start(self.project, "no-such-engine"))

    def test_instance_start_unknown_project_returns_none(self):
        self.assertIsNone(appmod.instance_start(f"no-such-project-{_SCOPE}", "claude"))


# ─── Tier 3: end-to-end HTTP, real ThreadingHTTPServer ─────────────────────

class SessionIdentityEndpointTests(unittest.TestCase):
    """Same technique as tests/test_smoke_check.py's SmokeCheckEndpointTests
    / tests/test_deploy_dispatch.py's DeployEndpointTests -- a real server,
    real tmux (patched down to ["tmux"]), ttyd's own Popen faked."""

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
        # Fix-up (docs/test-review.md finding #5): this must clear/isolate
        # appmod._sessions (the per-project session-identity registry this
        # class's tests actually exercise), NOT appmod.SESSIONS (an
        # unrelated, pre-existing login/auth-cookie store, app.py:306) --
        # the two are different globals that happen to have similarly-named
        # module attributes. The original `appmod.SESSIONS.clear()` here
        # cleared the wrong one: intended per-test isolation of _sessions
        # silently did nothing (stale entries accumulated across test
        # methods in this process), while unintentionally invalidating any
        # currently-authenticated login session elsewhere in the shared
        # `app` module every time this setUp ran. Confirmed no test in this
        # class or file depends on that accidental SESSIONS-clearing side
        # effect: every test authenticates fresh via self._authed() ->
        # self._login() after setUp runs, which always installs its own new
        # cookie/session id regardless of what SESSIONS held beforehand.
        self._orig_sessions = dict(appmod._sessions)
        appmod._sessions.clear()
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-si-endpoint-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-si-endpoint-projects-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_projects_dir = appmod.PROJECTS_DIR
        appmod.ENGINES_DIR = self.engines_dir
        appmod.PROJECTS_DIR = self.projects_dir
        _patch_tmux(self, ["tmux"])
        _patch_ttyd_popen(self)
        _write_engine_file(self.engines_dir, "claude.engine", """\
            LABEL=Claude
            CMD=sleep 100
            """)
        _write_engine_file(self.engines_dir, "codex.engine", """\
            LABEL=Codex
            CMD=sleep 100
            """)
        self.project = f"proj-{_SCOPE}"
        os.makedirs(os.path.join(self.projects_dir, self.project))
        self._spawned = []

    def tearDown(self):
        import subprocess
        for sid in self._spawned:
            subprocess.run(["tmux", "kill-session", "-t", sid], capture_output=True)
        appmod.ENGINES_DIR = self._orig_engines_dir
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod._sessions.clear()
        appmod._sessions.update(self._orig_sessions)
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

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

    def _get_status(self, cookie):
        req = urllib.request.Request(f"{self.base}/status", headers={"Cookie": cookie})
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())

    def _authed(self):
        cookie = self._login()
        code = self._totp_code()
        return cookie, code

    def _spawn(self, cookie, code, engine="claude"):
        status, payload = self._post(f"/instance/{self.project}/spawn", cookie,
                                     {"code": code, "engine": engine})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"], payload)
        self._spawned.append(payload["session_id"])
        return payload["session_id"]

    def test_spawn_creates_a_tmux_session_and_appears_in_status(self):
        cookie, code = self._authed()
        sid = self._spawn(cookie, code, "claude")
        self.assertTrue(appmod.tmux_has(sid))
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        sessions = by_name[self.project]["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], sid)
        self.assertEqual(sessions[0]["engine"], "claude")

    def test_spawn_twice_yields_two_distinct_simultaneous_sessions(self):
        cookie, code = self._authed()
        sid1 = self._spawn(cookie, code, "claude")
        sid2 = self._spawn(cookie, code, "codex")
        self.assertNotEqual(sid1, sid2)
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        ids = {sess["session_id"] for sess in by_name[self.project]["sessions"]}
        self.assertEqual(ids, {sid1, sid2})
        self.assertTrue(appmod.tmux_has(sid1))
        self.assertTrue(appmod.tmux_has(sid2))

    def test_spawn_unknown_instance_returns_404(self):
        cookie, code = self._authed()
        status, payload = self._post(f"/instance/no-such-{_SCOPE}/spawn", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_session_stop_route_tears_down_only_the_targeted_session(self):
        cookie, code = self._authed()
        sid1 = self._spawn(cookie, code, "claude")
        sid2 = self._spawn(cookie, code, "claude")
        status, payload = self._post(
            f"/instance/{self.project}/session/{sid1}/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(appmod.tmux_has(sid1))
        self.assertTrue(appmod.tmux_has(sid2))
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        ids = {sess["session_id"] for sess in by_name[self.project]["sessions"]}
        self.assertEqual(ids, {sid2})

    def test_session_stop_route_on_already_gone_id_returns_ok_true_not_404(self):
        cookie, code = self._authed()
        status, payload = self._post(
            f"/instance/{self.project}/session/never-existed-{_SCOPE}/stop",
            cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_session_stop_route_unknown_instance_returns_404(self):
        cookie, code = self._authed()
        status, payload = self._post(
            f"/instance/no-such-{_SCOPE}/session/whatever/stop", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_session_stop_route_rejects_a_session_id_owned_by_a_different_project(self):
        """Regression for docs/test-review.md Finding #1: the route's own
        URL shape (/instance/<name>/session/<id>/stop) implies <id> belongs
        to <name>, but nothing enforced that -- passing project A's real
        session_id under project B's URL must be a silent no-op (still
        {"ok": true}, matching the route's existing idempotent style), and
        must NOT tear down project A's actual session."""
        cookie, code = self._authed()
        sid_a = self._spawn(cookie, code, "claude")
        other_project = f"proj-b-{_SCOPE}"
        os.makedirs(os.path.join(self.projects_dir, other_project), exist_ok=True)
        status, payload = self._post(
            f"/instance/{other_project}/session/{sid_a}/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        # Session A must still be alive -- the cross-project id was rejected,
        # not honored.
        self.assertTrue(appmod.tmux_has(sid_a))
        ids = {s["session_id"] for s in appmod.active_sessions(self.project)}
        self.assertEqual(ids, {sid_a})

    def test_session_stop_route_never_reaches_a_real_team_session(self):
        """Regression for docs/test-review.md Finding #1: a real tmux
        session named exactly like app/teams.py's own deterministic
        f"team-{project_name}" naming (never spawned via this spec's
        instance_start()/active_sessions() machinery at all) must never be
        killable through this route, regardless of which project name
        appears in the URL -- team-session teardown is app/teams.py's own
        lifecycle (worktree cleanup, run-status bookkeeping, _team_threads
        ownership), untouched by this spec (docs/spec.md Non-goals)."""
        import subprocess
        team_session = f"team-victim-{_SCOPE}"
        subprocess.run(["tmux", "new-session", "-d", "-s", team_session, "sleep 100"],
                       capture_output=True)
        self.addCleanup(lambda: subprocess.run(
            ["tmux", "kill-session", "-t", team_session], capture_output=True))
        self.assertTrue(appmod.tmux_has(team_session))
        cookie, code = self._authed()
        status, payload = self._post(
            f"/instance/{self.project}/session/{team_session}/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        # The team-named tmux session must still be alive -- this route
        # never had ownership of it, so it must never touch it.
        self.assertTrue(appmod.tmux_has(team_session))

    def test_legacy_on_route_no_longer_exists(self):
        """Regression for part 2 (docs/spec.md "Routes" §6 / acceptance
        criteria): the /on back-compat shim route is gone entirely -- an
        unmatched POST falls through do_POST()'s own final `else` branch, a
        bare 404 with no JSON body (self.send_response(404);
        self.end_headers()), same as any other unrecognized route."""
        cookie, code = self._authed()
        status, _ = self._post(f"/instance/{self.project}/on", cookie,
                               {"code": code, "engine": "claude"})
        self.assertEqual(status, 404)

    def test_legacy_off_route_no_longer_exists(self):
        cookie, code = self._authed()
        status, _ = self._post(f"/instance/{self.project}/off", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_status_no_longer_includes_the_removed_back_compat_fields(self):
        """Regression for part 2 (docs/spec.md acceptance criteria):
        `/status`'s per-project JSON no longer includes `on`/`engine`/`url`
        -- only `sessions` (plus desc/code_on/code_url/deploy/gitea_sync/
        team, unaffected)."""
        cookie, code = self._authed()
        self._spawn(cookie, code, "claude")
        self._spawn(cookie, code, "codex")
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        inst = by_name[self.project]
        self.assertNotIn("on", inst)
        self.assertNotIn("engine", inst)
        self.assertNotIn("url", inst)
        self.assertEqual(len(inst["sessions"]), 2)

    def test_status_reports_zero_sessions_cleanly(self):
        cookie, code = self._authed()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        inst = by_name[self.project]
        self.assertEqual(inst["sessions"], [])

    def test_latest_session_url_for_project_picks_the_newest_session(self):
        cookie, code = self._authed()
        self._spawn(cookie, code, "claude")
        sid2 = self._spawn(cookie, code, "codex")
        newest_url = appmod._ttyd_urls.get(sid2)
        self.assertIsNotNone(newest_url)
        self.assertEqual(appmod._latest_session_url_for_project(self.project), newest_url)

    def test_smoke_check_targets_the_most_recently_started_sessions_url(self):
        """docs/spec.md acceptance criteria: "verified by asserting which
        of two distinct mock URLs the check actually hit" -- spins up two
        real, distinguishable local HTTP servers bound to the EXACT ttyd
        ports each session was allocated (both engines here lack
        url_regex, so ttyd is each session's own resolved URL), then
        asserts POST .../smoke-check only ever reaches the NEWER session's
        server."""
        import http.server as _hs
        cookie, code = self._authed()
        self._spawn(cookie, code, "claude")
        sid2 = self._spawn(cookie, code, "codex")
        port1 = appmod._ttyd_ports[self._spawned[0]]
        port2 = appmod._ttyd_ports[sid2]

        hits = {"1": 0, "2": 0}

        def _make_handler(counter_key):
            class _H(_hs.BaseHTTPRequestHandler):
                def do_GET(self):
                    hits[counter_key] += 1
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

                def log_message(self, *a):
                    pass
            return _H

        srv1 = ThreadingHTTPServer(("127.0.0.1", port1), _make_handler("1"))
        srv2 = ThreadingHTTPServer(("127.0.0.1", port2), _make_handler("2"))
        t1 = threading.Thread(target=srv1.serve_forever, daemon=True)
        t2 = threading.Thread(target=srv2.serve_forever, daemon=True)
        t1.start()
        t2.start()
        try:
            status, payload = self._post(f"/projects/{self.project}/smoke-check", cookie,
                                         {"code": code})
        finally:
            srv1.shutdown()
            srv1.server_close()
            srv2.shutdown()
            srv2.server_close()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(hits, {"1": 0, "2": 1})


if __name__ == "__main__":
    unittest.main()
