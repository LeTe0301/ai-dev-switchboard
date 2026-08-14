"""
Tests for the team session lifecycle's web routes + background driving
thread (backlog item 6d, part 2a -- docs/spec.md). Mirrors tests/test_
deploy_dispatch.py's own real-ThreadingHTTPServer + real-urllib.request.
urlopen() end-to-end HTTP harness (DeployEndpointTests) verbatim in
technique, extended for threading (two-concurrent-starts race, mid-delegate
stop timing, service-restart simulation) -- per this story's own established
pattern that every prior defect in this exact area (tmux session lifecycle
under partial failure or concurrency) was found by exercising the real thing
past the spec's enumerated cases, not by reasoning about it.

Also covers: install.sh's new unconditional `teams.py` copy line
(InstallShTeamsPyBlockTests, mirrors InstallShDeployMapBlockTests' block-
extraction technique), and the explicit regression guard that the CLI's
--lead still accepts a tier-3 lead even though the web route's DEFAULT
composition refuses one (docs/spec.md "Acceptance criteria").

Run with:
    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures", "headless")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-team-routes-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
import teams as teamsmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def _fixture(name):
    return os.path.join(FIXTURES_DIR, name)


def _patch_tmux(testcase, argv):
    """Same technique tests/test_teams_headless.py/test_teams_lifecycle.py
    already establish -- TMUX patched in both app.py's and teams.py's own
    module namespaces (the latter built its own `from app import TMUX`
    binding)."""
    orig_app, orig_teams = appmod.TMUX, teamsmod.TMUX
    appmod.TMUX = argv
    teamsmod.TMUX = argv

    def _restore():
        appmod.TMUX = orig_app
        teamsmod.TMUX = orig_teams
    testcase.addCleanup(_restore)


def _write_engine_file(dirpath, filename, body):
    with open(os.path.join(dirpath, filename), "w") as f:
        f.write(textwrap.dedent(body))


def _write_script(dirpath, filename, body):
    path = os.path.join(dirpath, filename)
    with open(path, "w") as f:
        f.write(textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, check=check)


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")


# Test-isolation: run_id (and therefore every switchboard-headless-<run_id>
# tmux session name derived from it) is otherwise a bare, unscoped value --
# a global machine property this process does not own. Concurrent test
# processes would then both see and, worse, sweep each other's live
# sessions in tearDown. Scoping every run_id with this process's own pid
# makes every "no leftover switchboard-headless-*" assertion/sweep concern
# only sessions this process could have created. Same technique tests/
# test_teams_headless.py's own _scope_run_ids()/_SESSION_PREFIX establish.
_RUN_ID_SCOPE = f"p{os.getpid()}"
_SESSION_PREFIX = f"switchboard-headless-{_RUN_ID_SCOPE}"


def _scope_run_ids(testcase):
    orig = teamsmod._run_id
    testcase.addCleanup(lambda: setattr(teamsmod, "_run_id", orig))
    teamsmod._run_id = lambda: f"{_RUN_ID_SCOPE}-{orig()}"


def _kill_leftover_team_sessions():
    r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
    for name in r.stdout.splitlines():
        if name.startswith("team-") or name.startswith(_SESSION_PREFIX):
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


class _SlowGetDict(dict):
    """A dict whose .get() call for one specific key sleeps briefly before
    returning -- widens whatever window sits between a caller's read and
    its later write/pop, standing in for real scheduler preemption
    (docs/test-review.md's own reproduction technique for this exact
    defect), without needing a test-only hook inside the function under
    test. __setitem__/pop are left completely unmodified -- only the read
    is slowed."""

    def __init__(self, *a, slow_key=None, delay=0.0, **kw):
        super().__init__(*a, **kw)
        self._slow_key = slow_key
        self._delay = delay

    def get(self, key, default=None):
        value = super().get(key, default)
        if key == self._slow_key and self._delay:
            time.sleep(self._delay)
        return value


class TeamThreadsLockTests(unittest.TestCase):
    """docs/test-review.md must-fix: app.py's own _run_team_in_background()
    cleanup used to check-then-act on _team_threads with no lock --
    `entry = _team_threads.get(name)` then, later and separately,
    `_team_threads.pop(name, None)`, which removes whatever is CURRENTLY
    keyed there, not specifically the validated entry. A /team/start for
    the SAME project landing in that window had its own fresh entry
    silently destroyed by the old thread's own cleanup -- the fourth
    check-then-act-on-shared-state defect in this exact 6d subsystem (after
    the ownership stamp, the unstamped-session window, and the
    partial-creation cleanup, all part 1). Fixed by _team_threads_lock +
    _team_threads_set()/_team_threads_get()/_team_threads_pop_if_owned() --
    the ONLY sanctioned accessors, confirmed by grep in docs/implementation.md.

    Two tests, same widening technique (_SlowGetDict, an artificially
    widened window standing in for real scheduler preemption -- a widened
    window proving a narrow race is real is this project's own established
    discipline, e.g. CreateTeamSessionAtomicStampTests in part 1):
    the first confirms the REAL, shipped _team_threads_pop_if_owned()/
    _team_threads_set() survive it; the second confirms the identical
    technique, pointed at a faithful reproduction of the OLD, unlocked
    shape, reliably reproduces the loss -- so the first test's clean pass
    is not merely because the widening technique itself is too weak to
    ever catch anything (the same "verify the methodology actually pins
    the behavior" discipline CreateTeamSessionAtomicStampTests already
    established)."""

    def setUp(self):
        self._orig_team_threads = appmod._team_threads
        appmod._team_threads = {}

    def tearDown(self):
        appmod._team_threads = self._orig_team_threads

    def test_pop_if_owned_survives_a_widened_window_against_a_concurrent_relaunch(self):
        old_entry = {"run_id": "old-run", "thread": None, "cancel_event": threading.Event()}
        new_entry = {"run_id": "new-run", "thread": None, "cancel_event": threading.Event()}
        appmod._team_threads = _SlowGetDict({"proj": old_entry}, slow_key="proj", delay=0.3)

        t = threading.Thread(target=appmod._team_threads_pop_if_owned, args=("proj", "old-run"))
        t.start()
        time.sleep(0.05)  # let the old thread's cleanup actually enter its (slow) read
        appmod._team_threads_set("proj", new_entry)  # the concurrent relaunch
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "old thread's cleanup did not finish in time")

        result = appmod._team_threads.get("proj")
        self.assertEqual(result, new_entry,
                         "the concurrent relaunch's fresh entry must survive the old thread's "
                         "own cleanup even under an artificially widened window -- got "
                         f"{result!r}")

    def test_widening_technique_actually_catches_the_naive_unlocked_pattern(self):
        # Sanity-checks the SlowGetDict methodology itself against a
        # faithful reproduction of the PRE-FIX shape (no lock at all) --
        # same delay, same timing, same relaunch trigger as the test above,
        # so a clean pass there is not merely because the technique itself
        # is too weak to ever catch anything.
        threads = _SlowGetDict({"proj": {"run_id": "old-run"}}, slow_key="proj", delay=0.3)

        def naive_pop_if_owned(name, run_id):
            entry = threads.get(name)  # slow, no lock -- the exact pre-fix shape
            if entry is not None and entry.get("run_id") == run_id:
                threads.pop(name, None)

        t = threading.Thread(target=naive_pop_if_owned, args=("proj", "old-run"))
        t.start()
        time.sleep(0.05)
        threads["proj"] = {"run_id": "new-run"}  # unlocked relaunch write -- nothing to block on
        t.join(timeout=5)

        self.assertIsNone(threads.get("proj"),
                          "sanity check: the naive, unlocked pattern is expected to lose the "
                          "concurrent relaunch's entry -- if this assertion fails, the widening "
                          "technique itself isn't discriminating, and the test above's clean "
                          "pass wouldn't actually prove anything")


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class _RealHTTPTeamTestCase(unittest.TestCase):
    """Common real-ThreadingHTTPServer harness -- same technique tests/
    test_deploy_dispatch.py's DeployEndpointTests already establishes."""

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
        self.tmp = tempfile.mkdtemp(prefix="switchboard-team-routes-")
        self.projects_dir = os.path.join(self.tmp, "projects")
        self.engines_dir = os.path.join(self.tmp, "engines")
        self.state_dir = os.path.join(self.tmp, "state")
        os.makedirs(self.projects_dir)
        os.makedirs(self.engines_dir)
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        self._orig_llm_base = teamsmod.TEAM_LLM_BASE_URL
        self._orig_llm_model = teamsmod.TEAM_LLM_MODEL
        self._orig_reap_interval = appmod.TEAM_REAP_POLL_INTERVAL_SECONDS
        self._orig_reap_last_at = appmod._team_reap_last_at
        appmod.PROJECTS_DIR = self.projects_dir
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        teamsmod.TEAM_LLM_BASE_URL = None
        teamsmod.TEAM_LLM_MODEL = None
        # Reaping is throttled -- start "just reaped" so an incidental
        # /status call inside a test doesn't opportunistically sweep before
        # the test itself is ready to observe that. Individual tests that
        # WANT a reap pass to fire force it via _team_reap_last_at = 0 (or
        # TEAM_REAP_POLL_INTERVAL_SECONDS = 0) explicitly.
        appmod._team_reap_last_at = time.time()
        appmod._team_threads.clear()
        _patch_tmux(self, ["tmux"])
        _scope_run_ids(self)

    def tearDown(self):
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        teamsmod.TEAM_LLM_BASE_URL = self._orig_llm_base
        teamsmod.TEAM_LLM_MODEL = self._orig_llm_model
        appmod.TEAM_REAP_POLL_INTERVAL_SECONDS = self._orig_reap_interval
        appmod._team_reap_last_at = self._orig_reap_last_at
        appmod._team_threads.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)
        _kill_leftover_team_sessions()

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

    def _project(self, name="proj"):
        path = os.path.join(self.projects_dir, name)
        _init_repo(path)
        return path

    def _write_fast_finisher_engine(self, name, first_tool="ask_user"):
        """A tier-2 (schema-flag-bearing) engine whose script prints valid
        JSON immediately -- used as the lead so a real /team/start's
        background thread converges quickly without a real LLM/CLI."""
        if first_tool == "ask_user":
            payload = ('{"tool": "ask_user", "args": {"question": "q?", "header": "Q", '
                      '"options": [{"label": "a", "description": "a"}, '
                      '{"label": "b", "description": "b"}]}}')
        else:
            payload = '{"tool": "finish", "args": {"summary": "done"}}'
        script = _write_script(self.engines_dir, f"{name}.py", f"""\
            #!/usr/bin/env python3
            print({payload!r})
            """)
        _write_engine_file(self.engines_dir, f"{name}.engine", f"""\
            LABEL={name}
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema {{schema}}
            """)


# ─── POST /projects/<name>/team/start ──────────────────────────────────────
class TeamStartEndpointTests(_RealHTTPTeamTestCase):
    def test_unknown_project_404_no_side_effects(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/nope/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 404)
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))

    def test_missing_task_returns_400(self):
        self._project("proj")
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/start", cookie, {"task": "  ", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("task description", payload["error"])
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))

    def test_tier3_only_roster_refuses_400_naming_both_fixes_no_side_effects(self):
        # docs/spec.md §2, SETTLED BY THE USER 2026-08-13: the DEFAULT
        # composition never picks a tier-3 lead. No TEAM_LLM_*, and the only
        # headless-eligible engine is tier 3 (no HEADLESS_SCHEMA_FLAG).
        self._project("proj")
        _write_engine_file(self.engines_dir, "prose.engine", """\
            LABEL=Prose
            CMD=unused
            HEADLESS_CMD=echo hi
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("tier-3", payload["error"])
        self.assertIn("TEAM_LLM_BASE_URL", payload["error"])
        self.assertIn("--lead can still", payload["error"])
        # Real persisted state, not just the response -- no worktree, no
        # session, no run.json at all (docs/spec.md Defect #4's own class:
        # a refusal that still leaves something behind).
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))
        self.assertFalse(appmod.tmux_has("team-proj"))
        self.assertFalse(os.path.isdir(f"{os.path.join(self.projects_dir, 'proj')}.teams"))

    def test_happy_path_tier2_default_lead_persisted_correctly(self):
        self._project("proj")
        self._write_fast_finisher_engine("lead2")
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["lead"], {"kind": "engine", "name": "lead2", "tier": 2})
        self.assertEqual(payload["members"], ["helper"])
        run_id = payload["run_id"]
        # Real, persisted state -- lead/members are set once at launch and
        # never mutated by the driving thread, so this is race-free even
        # though the background thread may already be running.
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["lead"], {"kind": "engine", "name": "lead2", "tier": 2})
        self.assertEqual(state["members"], ["helper"])
        self.assertEqual(state["project_name"], "proj")
        self.assertTrue(appmod.tmux_has(payload["session"]))

    def test_ollama_configured_selects_ollama_lead(self):
        self._project("proj")
        teamsmod.TEAM_LLM_BASE_URL = "http://127.0.0.1:1/v1"  # never dialed -- launch_team() doesn't call it
        teamsmod.TEAM_LLM_MODEL = "qwen3:8b"
        _write_engine_file(self.engines_dir, "onlymember.engine", """\
            LABEL=OnlyMember
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["lead"], {"kind": "ollama", "name": "qwen3:8b", "tier": 1})
        self.assertEqual(payload["members"], ["onlymember"])
        state = teamsmod._load_state(payload["run_id"])
        self.assertEqual(state["lead"]["kind"], "ollama")

    def test_two_near_simultaneous_starts_exactly_one_succeeds(self):
        # Real concurrency -- two threads racing the same route, not two
        # sequential calls (docs/spec.md "Edge cases").
        self._project("proj")
        self._write_fast_finisher_engine("lead2")
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        code = self._totp_code()
        results = [None, None]
        barrier = threading.Barrier(2)

        def _fire(i):
            barrier.wait()
            results[i] = self._post("/projects/proj/team/start", cookie,
                                    {"task": f"do it {i}", "code": code})

        threads = [threading.Thread(target=_fire, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, [200, 400])
        winner = next(r for r in results if r[0] == 200)
        loser = next(r for r in results if r[0] == 400)
        # Real finding, exercising the real thing (repeatedly, past this
        # test's own first few observations) rather than trusting the
        # spec's own stated assumption: launch_team()'s own collision can
        # surface via any of SEVERAL distinct checks depending on exact
        # thread scheduling -- confirmed empirically, not guessed at: the
        # session-name pre-check in launch_team() itself ("a team is
        # already running... stop it first", the case docs/spec.md's own
        # "Edge cases" text describes), _create_worktree()'s own "path
        # already exists"/"still has uncommitted changes" pre-check, a raw
        # `git worktree add` failure (git's own real stderr text, which
        # varies by git version -- not something a test should pin), the
        # analogous pre-check inside _create_team_session() itself, and a
        # real tmux "duplicate session" `new-session` failure (see
        # docs/implementation.md/tests/test_teams_lifecycle.py's own
        # SessionCreationRaceRealTmuxTests for the severe defect THIS
        # specific one used to cause, now fixed, in _create_team_session()'s
        # own failure-cleanup). All are legitimate instances of
        # "launch_team()'s own collision error" (the AC's own wording, which
        # names no one specific message) -- rather than pin one particular
        # message shape (fragile, and it was this test's own narrower
        # assertion that masked the tmux-session defect from ever being
        # caught here), assert only that a real, non-empty error came back,
        # and -- the property that actually matters -- that the winner's own
        # resources are completely intact regardless of which check fired.
        self.assertTrue(loser[1].get("error"), loser[1])
        # The winning run's own worktree/session are completely unaffected
        # by the losing attempt.
        state = teamsmod._load_state(winner[1]["run_id"])
        self.assertEqual(set(state["worktrees"]), {"helper"})
        self.assertTrue(appmod.tmux_has(winner[1]["session"]))
        self.assertEqual(teamsmod._team_session_run_id(winner[1]["session"]), winner[1]["run_id"])


# ─── POST /projects/<name>/team/stop ────────────────────────────────────────
class TeamStopEndpointTests(_RealHTTPTeamTestCase):
    def setUp(self):
        super().setUp()
        self._orig_poll = teamsmod.TEAM_HEADLESS_POLL_SECONDS
        self._orig_grace = teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = 0.1
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = 1.0

    def tearDown(self):
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = self._orig_poll
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = self._orig_grace
        super().tearDown()

    def test_stop_with_no_team_ever_started_is_idempotent_ok(self):
        self._project("proj")
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_stop_on_unknown_project_404(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/nope/team/stop", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_stop_on_already_finished_team_is_idempotent_ok(self):
        repo = self._project("proj")
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        state = teamsmod._load_state(run_id)
        state["status"] = "finished"
        state["summary"] = "done"
        teamsmod._persist(state)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("no team currently running", payload["message"])

    def test_stop_mid_delegate_terminates_real_subprocess_promptly(self):
        repo = self._project("proj")
        script = _write_script(self.engines_dir, "slow.py", """\
            #!/usr/bin/env python3
            import time
            while True:
                time.sleep(0.05)
            """)
        _write_engine_file(self.engines_dir, "slowmate.engine", f"""\
            LABEL=Slow
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, ["slowmate"])
        self.assertTrue(result["ok"], result)
        run_id = result["run_id"]

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "slowmate", "task": "go slow"}}, None, "..."

        orig_call_lead = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        cancel_event = threading.Event()
        appmod._team_threads["proj"] = {"run_id": run_id, "thread": None, "cancel_event": cancel_event}
        t = threading.Thread(target=appmod._run_team_in_background, args=("proj", run_id, cancel_event))
        appmod._team_threads["proj"]["thread"] = t
        t.start()
        try:
            time.sleep(0.5)  # let the real delegate subprocess actually spawn and get into its poll loop
            cookie = self._login()
            code = self._totp_code()
            start = time.time()
            status, payload = self._post("/projects/proj/team/stop", cookie, {"code": code})
            elapsed = time.time() - start
        finally:
            teamsmod._call_lead = orig_call_lead
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        # The HTTP response itself does not wait for the driving thread's own
        # escalation ladder (docs/spec.md "Edge cases") -- bounded by stop_
        # team()'s own fast session/worktree teardown, not by 2 x KILL_GRACE.
        self.assertLess(elapsed, 3.0)
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "background thread did not wind down within the bounded window")
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["status"], "stopped")
        # Real subprocess actually gone.
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        leftover = [n for n in r.stdout.splitlines() if n.startswith(_SESSION_PREFIX)]
        self.assertEqual(leftover, [])

    def test_status_maps_every_run_status_to_the_coarse_label(self):
        repo = self._project("proj")
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        cookie = self._login()
        cases = {
            "running": "running", "blocked_ask_user": "blocked",
            "escalated_max_rounds": "blocked", "finished": "finished",
            "error": "error", "stopped": "idle",
        }
        for raw_status, expected in cases.items():
            state = teamsmod._load_state(run_id)
            state["status"] = raw_status
            teamsmod._persist(state)
            s = self._get_status(cookie)
            by_name = {i["name"]: i for i in s["instances"]}
            self.assertEqual(by_name["proj"]["team"]["status"], expected, raw_status)
            self.assertEqual(by_name["proj"]["team"]["run_id"], run_id)

    def test_status_idle_when_no_run_ever_started(self):
        self._project("proj")
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(by_name["proj"]["team"], {"status": "idle", "run_id": None})


# ─── Service-restart simulation ────────────────────────────────────────────
class ServiceRestartSimulationTests(_RealHTTPTeamTestCase):
    """docs/spec.md's own note that a full separate-process restart is
    impractical inside unittest -- uses the equivalent same-process
    technique instead (clearing _team_threads directly), documented here
    per the spec's own explicit "to be resolved by the developer" note.
    _team_threads is the ONLY in-memory state a real process restart would
    actually lose (docs/spec.md's own design: the tmux dashboard session and
    git worktrees are OS/filesystem-level and survive a killed app.py
    process unaffected; run.json is durable). Clearing exactly that dict
    entry is therefore functionally equivalent to a real restart from the
    one angle stop_team()'s/latest_run_for_project()'s own restart-safety
    claim is actually about."""

    def test_stop_after_simulated_restart_still_tears_down_real_session_and_worktrees(self):
        repo = self._project("proj")
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, ["helper"])
        self.assertTrue(result["ok"], result)
        run_id, session = result["run_id"], result["session"]
        self.assertTrue(appmod.tmux_has(session))
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "helper")))
        # Simulate a service restart: the process that launched this run is
        # gone, so _team_threads is empty in the "fresh" process -- but the
        # real tmux session and worktree survive, and run.json still says
        # "running".
        appmod._team_threads.clear()

        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/proj/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(appmod.tmux_has(session))
        self.assertFalse(os.path.isdir(teamsmod._worktree_path(repo, "helper")))
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["status"], "stopped")

    def test_status_shows_running_truthfully_until_reap_runs_then_flips_to_error(self):
        repo = self._project("proj")
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        self.assertTrue(result["ok"], result)
        run_id = result["run_id"]
        appmod._team_threads.clear()  # simulated restart -- see class docstring

        cookie = self._login()
        # Reap pass NOT due yet (setUp already stamped _team_reap_last_at to
        # "just now") -- /status must show the truthful, still-"running"
        # state, not a lie.
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(by_name["proj"]["team"]["status"], "running")

        # Force the reap pass due (docs/spec.md acceptance criteria:
        # "monkeypatched to 0") -- the NEXT /status poll runs
        # _team_reap_if_due(), which finds status=="running" with no
        # matching alive entry in _team_threads and marks it "error".
        appmod.TEAM_REAP_POLL_INTERVAL_SECONDS = 0
        appmod._team_reap_last_at = 0.0
        s2 = self._get_status(cookie)
        by_name2 = {i["name"]: i for i in s2["instances"]}
        self.assertEqual(by_name2["proj"]["team"]["status"], "error")
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["status"], "error")


# ─── The legitimate concurrent CLI team-resume case self-corrects ──────────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class OrphanCheckSelfCorrectsForLiveCliRunTests(unittest.TestCase):
    """docs/spec.md "Open questions" -- SETTLED BY THE USER 2026-08-13: the
    orphan check's transient false positive for a genuinely still-live
    CLI-driven run is an accepted, self-correcting tradeoff. Proven here
    with a REAL, separate `team-resume` subprocess (not a web-route
    thread) -- the orphan check has no way to distinguish this from a
    genuine restart-orphan and marks it "error" once, but the CLI process's
    own next _persist() call restores the true status."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-orphan-selfcorrect-")
        self.projects_dir = os.path.join(self.tmp, "projects")
        self.engines_dir = os.path.join(self.tmp, "engines")
        self.state_dir = os.path.join(self.tmp, "state")
        os.makedirs(self.projects_dir)
        os.makedirs(self.engines_dir)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self._orig_engines_dir_app = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        self._orig_reap_interval = appmod.TEAM_REAP_POLL_INTERVAL_SECONDS
        self._orig_reap_last_at = appmod._team_reap_last_at
        appmod.PROJECTS_DIR = self.projects_dir
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        appmod._team_threads.clear()

        # Each real subprocess invocation of this script is one round: it
        # counts itself via a counter file next to the script (fixed path,
        # known to this test), sleeps briefly (gives the test time to
        # inject the orphan check between rounds), then emits fact_check
        # (rounds 1-2) or finish (round 3) as tier-3 fenced JSON.
        self.counter_path = os.path.join(self.tmp, "round_counter")
        _write_script(self.tmp, "multi_round_lead.py", f"""\
            #!/usr/bin/env python3
            import os, time
            counter_path = {self.counter_path!r}
            n = 0
            if os.path.exists(counter_path):
                with open(counter_path) as f:
                    n = int(f.read().strip())
            n += 1
            with open(counter_path, "w") as f:
                f.write(str(n))
            time.sleep(1.0)
            if n < 3:
                print("```json")
                print('{{"tool": "fact_check", "args": {{"claim": "placeholder"}}}}')
                print("```")
            else:
                print("```json")
                print('{{"tool": "finish", "args": {{"summary": "done after orphan check"}}}}')
                print("```")
            """)
        _write_engine_file(self.engines_dir, "multiround.engine", f"""\
            LABEL=MultiRound
            CMD=unused
            HEADLESS_CMD=python3 {os.path.join(self.tmp, "multi_round_lead.py")}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        self.env = dict(os.environ)
        self.env["ENGINES_DIR"] = self.engines_dir
        self.env["TEAM_STATE_DIR"] = self.state_dir
        self.env["PROJECTS_DIR"] = self.projects_dir

    def tearDown(self):
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod.ENGINES_DIR = self._orig_engines_dir_app
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        appmod.TEAM_REAP_POLL_INTERVAL_SECONDS = self._orig_reap_interval
        appmod._team_reap_last_at = self._orig_reap_last_at
        appmod._team_threads.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_orphan_check_firing_once_does_not_permanently_disrupt_a_live_cli_run(self):
        lead = {"kind": "engine", "name": "multiround", "tier": 3}
        state = teamsmod._new_state(teamsmod._run_id(), self.workdir, lead, [], "task",
                                    project_name="proj")
        teamsmod._persist(state)
        run_id = state["run_id"]

        cmd = [sys.executable, os.path.join(APP_DIR, "teams.py"), "team-resume", run_id]
        proc = subprocess.Popen(cmd, cwd=REPO_ROOT, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # Wait for round 1 to actually persist (real, not assumed).
            deadline = time.time() + 10
            while time.time() < deadline:
                s = teamsmod._load_state(run_id)
                if len(s["history"]) >= 1:
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(len(teamsmod._load_state(run_id)["history"]), 1)

            # _team_threads has no entry for this run (it's CLI-driven, not
            # web-route-driven) -- force the orphan check to fire once.
            appmod.TEAM_REAP_POLL_INTERVAL_SECONDS = 0
            appmod._team_reap_last_at = 0.0
            appmod._team_reap_if_due()
            mid_state = teamsmod._load_state(run_id)
            self.assertEqual(mid_state["status"], "error")  # the transient false positive itself

            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()

        self.assertEqual(proc.returncode, 0, proc.stderr.read())
        final_state = json.loads(proc.stdout.read())
        self.assertEqual(final_state["status"], "finished")
        self.assertEqual(len(final_state["history"]), 3)
        # Re-read from disk too, not just the CLI's own stdout.
        self.assertEqual(teamsmod._load_state(run_id)["status"], "finished")


# ─── Regression: the CLI's --lead still accepts a tier-3 lead, unchanged ──
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class CliTierThreeLeadStillAllowedRegressionTests(unittest.TestCase):
    """The refusal in default_team_composition() constrains only the WEB
    ROUTE's default -- 6c's CLI --lead is untouched, unchanged, and still
    accepts a tier-3 lead explicitly (docs/spec.md "Acceptance criteria":
    regression-test this explicitly so a later reader does not 'tidy' the
    two into one rule)."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-tier3-cli-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-tier3-cli-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-tier3-cli-state-")
        self.workdir = tempfile.mkdtemp(prefix="switchboard-tier3-cli-workdir-")
        _write_engine_file(self.engines_dir, "tier3lead.engine", f"""\
            LABEL=Tier3Lead
            CMD=unused
            HEADLESS_CMD={_fixture("tier3_stub_two_step.sh")}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        self.env = dict(os.environ)
        self.env["ENGINES_DIR"] = self.engines_dir
        self.env["TEAM_STATE_DIR"] = self.state_dir
        self.env["PROJECTS_DIR"] = self.projects_dir

    def tearDown(self):
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_cli_team_start_with_explicit_tier3_lead_succeeds_unaffected_by_web_default_refusal(self):
        # Contrast case for TeamStartEndpointTests.
        # test_tier3_only_roster_refuses_400... -- the SAME tier-3-only
        # engine set is refused by the web route's default, but still
        # explicitly selectable via the CLI's --lead.
        cmd = [sys.executable, os.path.join(APP_DIR, "teams.py"), "team-start", self.workdir,
              "--task", "do the thing", "--lead", "tier3lead", "--members", ""]
        r = subprocess.run(cmd, cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        state = json.loads(r.stdout)
        self.assertEqual(state["status"], "finished")
        self.assertEqual(state["lead"], {"kind": "engine", "name": "tier3lead", "tier": 3})


# ─── install.sh copies teams.py ────────────────────────────────────────────
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")


class InstallShTeamsPyCopyTests(unittest.TestCase):
    """docs/spec.md "Proposed approach" §6 -- one new unconditional `cp`
    line, right after the existing app.py copy. Same block-extraction
    technique tests/test_deploy_dispatch.py's InstallShDeployMapBlockTests
    already established for this file."""

    def test_app_plus_engines_block_copies_teams_py_verbatim(self):
        with open(INSTALL_SH) as f:
            source = f.read()
        start = source.index('echo "-- App + engines --"')
        end = source.index('echo "-- Config --"', start)
        block = source[start:end]
        self.assertIn('cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"', block)
        self.assertIn('cp "$REPO_DIR/app/teams.py" "$INSTALL_DIR/teams.py"', block)
        # Unconditional -- not gated behind any `if`/flag check, matching
        # the existing app.py copy's own unconditional shape.
        app_line_idx = block.index('cp "$REPO_DIR/app/app.py"')
        teams_line_idx = block.index('cp "$REPO_DIR/app/teams.py"')
        between = block[app_line_idx:teams_line_idx]
        self.assertNotIn("if ", between)

    def test_block_actually_lands_teams_py_in_a_scratch_install_dir(self):
        with open(INSTALL_SH) as f:
            source = f.read()
        start = source.index('echo "-- App + engines --"')
        end = source.index('echo "-- Config --"', start)
        block = source[start:end]
        tmp = tempfile.mkdtemp(prefix="switchboard-install-teams-copy-")
        try:
            install_dir = os.path.join(tmp, "install")
            config_dir = os.path.join(tmp, "config")
            os.makedirs(install_dir)
            os.makedirs(config_dir)
            script = f"""#!/usr/bin/env bash
set -euo pipefail
REPO_DIR={REPO_ROOT}
INSTALL_DIR={install_dir}
CONFIG_DIR={config_dir}
{block}
"""
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            dest = os.path.join(install_dir, "teams.py")
            self.assertTrue(os.path.isfile(dest))
            with open(dest) as f, open(os.path.join(APP_DIR, "teams.py")) as f2:
                self.assertEqual(f.read(), f2.read())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
