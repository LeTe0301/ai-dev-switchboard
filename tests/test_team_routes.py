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

Also covers the overwatch feed + escalation inbox's backend routes
(backlog item 6f part 1, docs/spec.md -- TeamEventsEndpointTests,
TeamInboxEndpointTests, TeamResolveEndpointTests), same real-HTTP harness.

Run with:
    python3 -m unittest discover -s tests -v
"""
import builtins
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
import urllib.parse
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


# ─── run_id path-traversal validation helpers (docs/BACKLOG.md item 11(b)) ──
# _leads_root() == TEAM_STATE_DIR/leads, so a run_id of TRAVERSAL_RUN_ID
# joined UNvalidated into that (the pre-fix defect) resolves, once the OS
# walks the ".."s at open() time, to TEAM_STATE_DIR's *parent* + "outside/
# evilrun" -- i.e. self.tmp/outside/evilrun for every test class here
# (TEAM_STATE_DIR is always self.tmp/"state"). Reproduces the exact
# traversal the reviewer found during 6f part 1 (docs/spec.md Background).
TRAVERSAL_RUN_ID = "../../outside/evilrun"


def _plant_traversal_target(tmp_dir, relative="run.json", content=None):
    """Plants a real run.json-shaped file at the exact path TRAVERSAL_RUN_ID
    would reach if a route's run_id ever reached _run_dir() unvalidated --
    used to prove the fixed route never opens it (not just that the
    response status/shape happens to look right)."""
    d = os.path.join(tmp_dir, "outside", "evilrun")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, relative)
    with open(path, "w") as f:
        json.dump(content or {"run_id": "evilrun", "project_name": _PROJ,
                              "status": "blocked_ask_user", "members": []}, f)
    return path


def _get_forbidding_open_of(testcase, forbidden_path, action):
    """Runs `action()` (a zero-arg callable that performs one HTTP request
    against the real server) while builtins.open raises AssertionError if
    ever called with `forbidden_path` -- proving a planted file a
    path-traversal run_id would reach is genuinely never opened, mirroring
    tests/test_teams_grounding.py's GroundingReadOnlyRuntimeTests own
    monkeypatched-builtins.open technique. Safe against the real
    ThreadingHTTPServer's separate handling thread because builtins is
    process-global and the test blocks on the request (via urlopen) before
    restoring the patch, so no unrelated open() call races the guard."""
    forbidden_abs = os.path.abspath(forbidden_path)
    real_open = builtins.open

    def _guarded(file, *a, **kw):
        try:
            hit = os.path.abspath(str(file)) == forbidden_abs
        except (TypeError, ValueError):
            hit = False
        if hit:
            raise AssertionError(f"open() called for forbidden path {file!r}")
        return real_open(file, *a, **kw)

    builtins.open = _guarded
    try:
        return action()
    finally:
        builtins.open = real_open


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


def _patch_taiga_board(testcase, **fakes):
    """Monkeypatches the named teamsmod.taiga_board attributes for the
    duration of one test, restoring the originals on cleanup -- same
    "monkeypatch the module the caller imports it through" convention
    tests/test_teams_board.py's own _patch_taiga_board() already
    establishes (backlog item 7 part 2, docs/spec.md §2: app.py's own
    subject-enrichment read in GET .../team/inbox calls teams.taiga_board,
    not a separately-imported module)."""
    originals = {}
    for name, fn in fakes.items():
        originals[name] = getattr(teamsmod.taiga_board, name)
        setattr(teamsmod.taiga_board, name, fn)

    def _restore():
        for name, fn in originals.items():
            setattr(teamsmod.taiga_board, name, fn)

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


def _envelope(agent, seq, kind="message", text="x", ts="2026-01-01T00:00:00Z"):
    """One §4.1-shaped event envelope, written directly to a run's own
    transcript.jsonl/agents/<agent>.jsonl for TeamEventsEndpointTests/
    TeamInboxEndpointTests -- a fixed `ts` by default so ordering-sensitive
    assertions are deterministic, not dependent on real wall-clock
    resolution."""
    return {"ts": ts, "agent": agent, "seq": seq, "kind": kind, "text": text, "meta": {}}


def _append_jsonl(path, *envelopes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for e in envelopes:
            f.write(json.dumps(e) + "\n")


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
# test_teams_headless.py's own _scope_run_ids()/_SESSION_PREFIX establish --
# EXCEPT the scope token here is a fixed-width, all-digit prefix (not a
# "p<pid>-" prefix as a separate hyphen-delimited segment) so a scoped
# run_id stays exactly _RUN_ID_RE-shaped (docs/BACKLOG.md item 11(b)):
# unlike test_teams_headless.py/test_teams_cancel.py, THIS file is the only
# one that drives a real HTTP request with an explicit, client-supplied
# run_id through app.py's three team/* routes, which now validate that
# run_id against teams._RUN_ID_RE before ever reaching a path-join -- a
# "p12345-..." shaped scoped run_id would otherwise be rejected there as
# malformed, even though it's a genuinely legitimate run this test process
# itself created (see docs/implementation.md "Deviations from spec").
# Fixed-width (8 digits, comfortably covering any real pid) rather than
# bare `str(os.getpid())` so no two distinct pids' scope tokens can ever be
# a startswith-prefix of one another.
_RUN_ID_SCOPE = f"{os.getpid():08d}"
_SESSION_PREFIX = f"switchboard-headless-{_RUN_ID_SCOPE}"


# Test-isolation (continued): every real _PROJ/_PROJ_A/_PROJ_B project
# name in this file becomes a real team-<project> tmux session name
# (_team_session_name()) once launch_team()/_create_team_session() touches
# it -- scoped the same way as _RUN_ID_SCOPE above, for the same reason
# (BACKLOG item 9): two concurrent runs of this file must never contend
# for the same tmux session name.
_PROJ = f"proj-{_RUN_ID_SCOPE}"
_PROJ_A = f"proj-a-{_RUN_ID_SCOPE}"
_PROJ_B = f"proj-b-{_RUN_ID_SCOPE}"


def _scope_run_ids(testcase):
    """Wraps teamsmod._run_id() so every run_id it produces for the
    duration of testcase carries this process's own scope token WHILE
    staying _RUN_ID_RE-shaped: the scope token and the real epoch are both
    all-digits, so they're concatenated into one numeric prefix (not joined
    by their own separator) ahead of "-" + the real 12-hex-char suffix."""
    orig = teamsmod._run_id
    testcase.addCleanup(lambda: setattr(teamsmod, "_run_id", orig))

    def _scoped():
        epoch, hexpart = orig().split("-")
        return f"{_RUN_ID_SCOPE}{epoch}-{hexpart}"
    teamsmod._run_id = _scoped


def _kill_leftover_team_sessions():
    # "team-" is scoped to this process's own team-<project>-<scope>
    # sessions only (see _PROJ/_PROJ_A/_PROJ_B above) -- an unscoped
    # "team-" prefix match would kill a CONCURRENT process's own live team
    # sessions too, defeating the whole point of scoping project names
    # (docs/spec.md, BACKLOG item 9).
    r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
    for name in r.stdout.splitlines():
        if (name.startswith("team-") and name.endswith(f"-{_RUN_ID_SCOPE}")) or name.startswith(_SESSION_PREFIX):
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
        appmod._team_threads = _SlowGetDict({_PROJ: old_entry}, slow_key=_PROJ, delay=0.3)

        t = threading.Thread(target=appmod._team_threads_pop_if_owned, args=(_PROJ, "old-run"))
        t.start()
        time.sleep(0.05)  # let the old thread's cleanup actually enter its (slow) read
        appmod._team_threads_set(_PROJ, new_entry)  # the concurrent relaunch
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "old thread's cleanup did not finish in time")

        result = appmod._team_threads.get(_PROJ)
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
        threads = _SlowGetDict({_PROJ: {"run_id": "old-run"}}, slow_key=_PROJ, delay=0.3)

        def naive_pop_if_owned(name, run_id):
            entry = threads.get(name)  # slow, no lock -- the exact pre-fix shape
            if entry is not None and entry.get("run_id") == run_id:
                threads.pop(name, None)

        t = threading.Thread(target=naive_pop_if_owned, args=(_PROJ, "old-run"))
        t.start()
        time.sleep(0.05)
        threads[_PROJ] = {"run_id": "new-run"}  # unlocked relaunch write -- nothing to block on
        t.join(timeout=5)

        self.assertIsNone(threads.get(_PROJ),
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

    def _get(self, path, cookie):
        req = urllib.request.Request(f"{self.base}{path}", headers={"Cookie": cookie})
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {}

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

    def _project(self, name=_PROJ):
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
        self._project(_PROJ)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {"task": "  ", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("task description", payload["error"])
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))

    def test_tier3_only_roster_refuses_400_naming_both_fixes_no_side_effects(self):
        # docs/spec.md §2, SETTLED BY THE USER 2026-08-13: the DEFAULT
        # composition never picks a tier-3 lead. No TEAM_LLM_*, and the only
        # headless-eligible engine is tier 3 (no HEADLESS_SCHEMA_FLAG).
        self._project(_PROJ)
        _write_engine_file(self.engines_dir, "prose.engine", """\
            LABEL=Prose
            CMD=unused
            HEADLESS_CMD=echo hi
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("tier-3", payload["error"])
        self.assertIn("TEAM_LLM_BASE_URL", payload["error"])
        self.assertIn("--lead can still", payload["error"])
        # Real persisted state, not just the response -- no worktree, no
        # session, no run.json at all (docs/spec.md Defect #4's own class:
        # a refusal that still leaves something behind).
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))
        self.assertFalse(appmod.tmux_has(f"team-{_PROJ}"))
        self.assertFalse(os.path.isdir(f"{os.path.join(self.projects_dir, _PROJ)}.teams"))

    def test_happy_path_tier2_default_lead_persisted_correctly(self):
        self._project(_PROJ)
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
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie,
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
        self.assertEqual(state["project_name"], _PROJ)
        self.assertTrue(appmod.tmux_has(payload["session"]))

    def test_ollama_configured_selects_ollama_lead(self):
        self._project(_PROJ)
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
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["lead"], {"kind": "ollama", "name": "qwen3:8b", "tier": 1})
        self.assertEqual(payload["members"], ["onlymember"])
        state = teamsmod._load_state(payload["run_id"])
        self.assertEqual(state["lead"]["kind"], "ollama")

    def test_two_near_simultaneous_starts_exactly_one_succeeds(self):
        # Real concurrency -- two threads racing the same route, not two
        # sequential calls (docs/spec.md "Edge cases").
        self._project(_PROJ)
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
            results[i] = self._post(f"/projects/{_PROJ}/team/start", cookie,
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
        self._project(_PROJ)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_stop_on_unknown_project_404(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/nope/team/stop", cookie, {"code": code})
        self.assertEqual(status, 404)

    def test_stop_on_already_finished_team_is_idempotent_ok(self):
        repo = self._project(_PROJ)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        state = teamsmod._load_state(run_id)
        state["status"] = "finished"
        state["summary"] = "done"
        teamsmod._persist(state)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("no team currently running", payload["message"])

    def test_stop_mid_delegate_terminates_real_subprocess_promptly(self):
        repo = self._project(_PROJ)
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
        appmod._team_threads[_PROJ] = {"run_id": run_id, "thread": None, "cancel_event": cancel_event}
        t = threading.Thread(target=appmod._run_team_in_background, args=(_PROJ, run_id, cancel_event))
        appmod._team_threads[_PROJ]["thread"] = t
        t.start()
        try:
            time.sleep(0.5)  # let the real delegate subprocess actually spawn and get into its poll loop
            cookie = self._login()
            code = self._totp_code()
            start = time.time()
            status, payload = self._post(f"/projects/{_PROJ}/team/stop", cookie, {"code": code})
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
        repo = self._project(_PROJ)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        cookie = self._login()
        cases = {
            "running": "running", "blocked_ask_user": "blocked",
            "blocked_board_write": "blocked",  # backlog item 7 part 2, docs/spec.md §1
            "escalated_max_rounds": "blocked", "finished": "finished",
            "error": "error", "stopped": "idle",
        }
        for raw_status, expected in cases.items():
            state = teamsmod._load_state(run_id)
            state["status"] = raw_status
            teamsmod._persist(state)
            s = self._get_status(cookie)
            by_name = {i["name"]: i for i in s["instances"]}
            self.assertEqual(by_name[_PROJ]["team"]["status"], expected, raw_status)
            self.assertEqual(by_name[_PROJ]["team"]["run_id"], run_id)

    def test_status_idle_when_no_run_ever_started(self):
        self._project(_PROJ)
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        # docs/spec.md 6e: "composition" is additive -- no roster member at
        # all (no engines.d entries in this test's own scratch engines_dir,
        # no Ollama configured) means default_team_composition() itself
        # refuses, so composition is None here (no saved composition
        # either). "waiting_on_you" (backlog item 6f part 1) is additive
        # too -- False, no run at all. "escalation_kind" (backlog item 7
        # part 2) is additive too -- None, no run at all.
        self.assertEqual(by_name[_PROJ]["team"],
                         {"status": "idle", "run_id": None, "composition": None,
                          "waiting_on_you": False, "escalation_kind": None})

    def test_stop_on_blocked_board_write_now_actually_stops(self):
        # docs/spec.md §4 (backlog item 7 part 2) -- the disclosed no-op gap
        # from part 1's docs/implementation.md: a run blocked on
        # blocked_board_write used to fall into the early-return "no team
        # currently running" branch and Stop silently did nothing. Now it
        # actually tears the run down, same shape as a "running"/
        # "blocked_ask_user" stop already returns.
        repo = self._project(_PROJ)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        state = teamsmod._load_state(run_id)
        state["status"] = "blocked_board_write"
        teamsmod._persist(state)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("session_removed", payload)
        self.assertIn("worktrees", payload)
        self.assertNotIn("message", payload)
        self.assertEqual(teamsmod._load_state(run_id)["status"], "stopped")


# ─── Roster & composition UI (backlog item 6e, docs/spec.md) ──────────────
class StatusRosterAndCompositionTests(_RealHTTPTeamTestCase):
    """GET /status's two additive fields: top-level "roster" and per-
    instance inst.team.composition (saved composition, else
    default_team_composition()'s own pick, else None)."""

    def test_roster_reflects_engines_d_live_no_cache(self):
        self._project(_PROJ)
        cookie = self._login()
        s = self._get_status(cookie)
        self.assertEqual(s["roster"], [])  # nothing in engines_dir yet
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        s2 = self._get_status(cookie)
        names = [e["name"] for e in s2["roster"]]
        self.assertIn("helper", names)  # picked up without a restart

    def test_composition_falls_back_to_default_when_none_saved(self):
        self._project(_PROJ)
        self._write_fast_finisher_engine("lead2")
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(by_name[_PROJ]["team"]["composition"],
                         {"lead": {"kind": "engine", "name": "lead2", "tier": 2}, "members": ["helper"]})

    def test_composition_prefers_saved_over_default(self):
        self._project(_PROJ)
        self._write_fast_finisher_engine("lead2")
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        # A saved composition that differs from what default_team_
        # composition() would itself pick (helper as lead instead).
        teamsmod.save_composition(_PROJ, {"kind": "engine", "name": "helper"},
                                  [{"kind": "engine", "name": "lead2"}])
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(by_name[_PROJ]["team"]["composition"],
                         {"lead": {"kind": "engine", "name": "helper"}, "members": ["lead2"]})

    def test_composition_not_none_for_tier3_only_roster_with_no_saved_composition(self):
        # Reviewer-found defect (docs/implementation.md's "6e fix" cycle):
        # default_team_composition() refuses for THREE distinct reasons --
        # an empty roster, a single already-picked-lead engine with nothing
        # left to delegate to, or (this test's own case) a real roster
        # that's tier-3-only, which 6d part 2 settled must never be
        # auto-picked as the DEFAULT lead. Before the fix, all three
        # collapsed into composition=None, which the frontend's
        # `composition === null` check can't tell apart from "no roster
        # member at all" -- it rendered a permanently-disabled Start button
        # with no way to ever open the picker for exactly this case. This
        # reproduces the reviewer's own live repro: one tier-3 engines.d
        # entry, no Ollama configured, no saved composition.
        self._project(_PROJ)
        _write_engine_file(self.engines_dir, "prose.engine", """\
            LABEL=Prose
            CMD=unused
            HEADLESS_CMD=echo hi
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        # roster() itself is unaffected -- one real, pickable tier-3 entry.
        self.assertEqual([e["name"] for e in s["roster"]], ["prose"])
        # composition must be a real (non-None) object with nothing
        # pre-selected -- the frontend opens the picker off this, not off
        # default_team_composition()'s own "ok" flag.
        self.assertEqual(by_name[_PROJ]["team"]["composition"], {"lead": None, "members": []})

    def test_composition_stays_none_for_a_genuinely_empty_roster(self):
        # Contrast case for the fix above -- no engines.d entries, no
        # Ollama configured, so roster() itself is empty and composition
        # must remain None (the frontend's permanent-refusal, disabled-
        # Start state is still correct here; only the tier-3-only/single-
        # engine cases above changed).
        self._project(_PROJ)
        cookie = self._login()
        s = self._get_status(cookie)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(s["roster"], [])
        self.assertIsNone(by_name[_PROJ]["team"]["composition"])

    def test_waiting_on_you_true_only_for_blocked_ask_user_never_for_escalated_max_rounds(self):
        # docs/spec.md §4/"Open questions": additive-only field, true iff
        # the project's LATEST run status is "blocked_ask_user" OR (backlog
        # item 7 part 2, docs/spec.md §1) "blocked_board_write" -- every
        # existing /status field/value stays byte-for-byte unchanged (this
        # test only checks the one new key; the full-dict exact-match tests
        # above/elsewhere already pin the rest).
        repo = self._project(_PROJ)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        cookie = self._login()
        cases = {
            "running": False, "blocked_ask_user": True,
            "blocked_board_write": True,
            "escalated_max_rounds": False, "finished": False,
            "error": False, "stopped": False,
        }
        for raw_status, expected in cases.items():
            state = teamsmod._load_state(run_id)
            state["status"] = raw_status
            teamsmod._persist(state)
            s = self._get_status(cookie)
            by_name = {i["name"]: i for i in s["instances"]}
            self.assertEqual(by_name[_PROJ]["team"]["waiting_on_you"], expected, raw_status)

    def test_escalation_kind_field(self):
        # docs/spec.md §1 (backlog item 7 part 2): a direct string
        # comparison against the LATEST run's own status -- "ask_user" for
        # blocked_ask_user, "board_write" for blocked_board_write, None for
        # every other status (including "no run at all", covered separately
        # by test_status_idle_when_no_run_ever_started above).
        repo = self._project(_PROJ)
        result = teamsmod.launch_team(repo, "task", {"kind": "ollama", "name": "m", "tier": 1}, [])
        run_id = result["run_id"]
        cookie = self._login()
        cases = {
            "running": None, "blocked_ask_user": "ask_user",
            "blocked_board_write": "board_write",
            "escalated_max_rounds": None, "finished": None,
            "error": None, "stopped": None,
        }
        for raw_status, expected in cases.items():
            state = teamsmod._load_state(run_id)
            state["status"] = raw_status
            teamsmod._persist(state)
            s = self._get_status(cookie)
            by_name = {i["name"]: i for i in s["instances"]}
            self.assertEqual(by_name[_PROJ]["team"]["escalation_kind"], expected, raw_status)


# ─── GET /projects/<name>/team/grounding (backlog item 6e) ─────────────────
class TeamGroundingEndpointTests(_RealHTTPTeamTestCase):
    def test_unknown_project_404(self):
        cookie = self._login()
        status, payload = self._get("/projects/nope/team/grounding", cookie)
        self.assertEqual(status, 404)

    def test_no_grounding_files_returns_empty_list_not_an_error(self):
        # A bare repo (no README/ARCHITECTURE/BACKLOG/CLAUDE.md) -- unlike
        # _project()'s own helper, which writes a real README.md via
        # _init_repo(), this is the genuine "none of the four files exist"
        # case (docs/spec.md "Edge cases": load_grounding() already returns
        # files: [], empty: True).
        os.makedirs(os.path.join(self.projects_dir, "bare"))
        cookie = self._login()
        status, payload = self._get("/projects/bare/team/grounding", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["files"], [])

    def test_found_files_shape_never_leaks_content_digest_or_headings(self):
        repo = self._project(_PROJ)
        with open(os.path.join(repo, "README.md"), "w") as f:
            f.write("# Title\n\nSecret internal detail that must never reach the browser.\n")
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/grounding", cookie)
        self.assertEqual(status, 200)
        readme = next(f for f in payload["files"] if f["relpath"] == "README.md")
        self.assertEqual(set(readme.keys()), {"label", "relpath", "byte_count"})
        self.assertGreater(readme["byte_count"], 0)
        body = json.dumps(payload)
        self.assertNotIn("Secret internal detail", body)
        self.assertNotIn("digest", payload)

    def test_read_only_no_totp_required(self):
        # Matches /status's own gating -- _authed() only, unlike every
        # mutating POST route (docs/spec.md "Read-only, no TOTP needed").
        self._project(_PROJ)
        cookie = self._login()
        req = urllib.request.Request(f"{self.base}/projects/{_PROJ}/team/grounding",
                                     headers={"Cookie": cookie})
        resp = urllib.request.urlopen(req)
        self.assertEqual(resp.status, 200)


# ─── GET /projects/<name>/team/events (backlog item 6f part 1, docs/spec.md) ──
class TeamEventsEndpointTests(_RealHTTPTeamTestCase):
    """Hand-constructs run.json + raw .jsonl log files directly (no real
    launch_team()/tmux session needed -- this route only ever reads
    already-persisted state and log files) so event content/ordering/
    cursor behavior is deterministic and independent of a real lead loop."""

    def _make_run(self, name=_PROJ, members=None, status="running"):
        os.makedirs(os.path.join(self.projects_dir, name), exist_ok=True)
        run_id = teamsmod._run_id()
        state = teamsmod._new_state(run_id, os.path.join(self.projects_dir, name),
                                    {"kind": "ollama", "name": "m", "tier": 1},
                                    members or [], "task", project_name=name)
        state["status"] = status
        teamsmod._persist(state)
        return run_id

    def test_unknown_project_404(self):
        cookie = self._login()
        status, payload = self._get("/projects/nope/team/events", cookie)
        self.assertEqual(status, 404)

    def test_no_run_ever_started_returns_clean_empty_state(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"run_id": None, "events": [], "cursors": {}})

    def test_events_from_lead_and_teammates_merged_and_sorted_chronologically(self):
        run_id = self._make_run(members=["claude"])
        _append_jsonl(teamsmod._transcript_path(run_id),
                     _envelope("lead", 1, text="first", ts="2026-01-01T00:00:00Z"),
                     _envelope("lead", 2, text="third", ts="2026-01-01T00:00:02Z"))
        _append_jsonl(teamsmod._agent_log_path(run_id, "claude"),
                     _envelope("claude", 1, text="second", ts="2026-01-01T00:00:01Z"))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual([e["text"] for e in payload["events"]], ["first", "second", "third"])
        # A member that never delegated to (no log file yet) is present-but-
        # empty, offset 0 -- not an error, and still gets its own cursor
        # entry so a client's cursor map stays complete across polls.
        self.assertEqual(payload["cursors"]["claude"], os.path.getsize(teamsmod._agent_log_path(run_id, "claude")))
        self.assertIn("lead", payload["cursors"])

    def test_a_member_with_no_log_file_yet_is_present_but_empty(self):
        run_id = self._make_run(members=["neverdelegated"])
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["cursors"].get("neverdelegated"), 0)

    def test_cursor_returns_only_new_events_on_repeat_poll_no_event_returned_twice(self):
        run_id = self._make_run()
        lead_path = teamsmod._transcript_path(run_id)
        _append_jsonl(lead_path, _envelope("lead", 1, text="one"))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(len(payload["events"]), 1)
        cursor_q = urllib.parse.quote(json.dumps(payload["cursors"]))
        status2, payload2 = self._get(f"/projects/{_PROJ}/team/events?cursor={cursor_q}", cookie)
        self.assertEqual(payload2["events"], [])
        _append_jsonl(lead_path, _envelope("lead", 2, text="two"))
        status3, payload3 = self._get(f"/projects/{_PROJ}/team/events?cursor={cursor_q}", cookie)
        self.assertEqual([e["text"] for e in payload3["events"]], ["two"])

    def test_malformed_cursor_falls_back_to_a_full_replay_not_a_400(self):
        run_id = self._make_run()
        _append_jsonl(teamsmod._transcript_path(run_id), _envelope("lead", 1, text="one"))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events?cursor=not-json", cookie)
        self.assertEqual(status, 200)
        self.assertEqual([e["text"] for e in payload["events"]], ["one"])

    def test_truncated_agent_reports_flag_and_follow_up_poll_drains_the_rest(self):
        run_id = self._make_run()
        one_line = json.dumps(_envelope("lead", 1, text="a")) + "\n"
        cap = len(one_line.encode()) * 2  # exactly 2 whole lines' worth
        orig_cap = teamsmod.TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL
        teamsmod.TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL = cap
        self.addCleanup(setattr, teamsmod, "TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL", orig_cap)
        _append_jsonl(teamsmod._transcript_path(run_id),
                     _envelope("lead", 1, text="a"), _envelope("lead", 2, text="b"),
                     _envelope("lead", 3, text="c"))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(status, 200)
        self.assertEqual([e["text"] for e in payload["events"]], ["a", "b"])
        self.assertEqual(payload["truncated"], {"lead": True})
        cursor_q = urllib.parse.quote(json.dumps(payload["cursors"]))
        status2, payload2 = self._get(f"/projects/{_PROJ}/team/events?cursor={cursor_q}", cookie)
        self.assertEqual([e["text"] for e in payload2["events"]], ["c"])
        self.assertEqual(payload2["truncated"], {})

    def test_malformed_line_becomes_one_error_event_no_500_for_whole_poll(self):
        run_id = self._make_run()
        path = teamsmod._transcript_path(run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(_envelope("lead", 1, text="ok-before")) + "\n")
            f.write("not valid json at all\n")
            f.write(json.dumps(_envelope("lead", 3, text="ok-after")) + "\n")
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(status, 200)
        kinds = [e["kind"] for e in payload["events"]]
        self.assertIn("error", kinds)
        self.assertEqual(len(payload["events"]), 3)

    def test_cross_project_run_id_404_no_data_leaked(self):
        run_a = self._make_run(name=_PROJ_A)
        os.makedirs(os.path.join(self.projects_dir, _PROJ_B))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ_B}/team/events?run_id={run_a}", cookie)
        self.assertEqual(status, 404)

    # ── run_id path-traversal validation (docs/BACKLOG.md item 11(b)) ───

    def test_path_traversal_run_id_404_planted_file_never_opened(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        planted = _plant_traversal_target(self.tmp)
        cookie = self._login()
        status, payload = _get_forbidding_open_of(
            self, planted,
            lambda: self._get(f"/projects/{_PROJ}/team/events?run_id={TRAVERSAL_RUN_ID}", cookie))
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "unknown run_id for this project"})

    def test_url_encoded_traversal_run_id_404(self):
        # %2e%2e%2f decodes to the same literal "../" the regex already
        # rejects (docs/spec.md "Edge cases") -- urllib.parse.parse_qs
        # decodes percent-encoding before the route ever sees the string.
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        planted = _plant_traversal_target(self.tmp)
        encoded = "%2e%2e%2f%2e%2e%2foutside%2fevilrun"
        cookie = self._login()
        status, payload = _get_forbidding_open_of(
            self, planted,
            lambda: self._get(f"/projects/{_PROJ}/team/events?run_id={encoded}", cookie))
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "unknown run_id for this project"})

    def test_malformed_non_traversal_run_id_404_no_500(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        for bad_run_id in ("not-a-real-run", "1700000000-ABC123DEF456"):
            status, payload = self._get(
                f"/projects/{_PROJ}/team/events?run_id={bad_run_id}", cookie)
            self.assertEqual(status, 404, bad_run_id)
            self.assertEqual(payload, {"error": "unknown run_id for this project"}, bad_run_id)

    def test_run_id_with_nul_byte_404_no_500(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        run_id = urllib.parse.quote("1700000000-abc123def456\x00/etc/passwd")
        status, payload = self._get(f"/projects/{_PROJ}/team/events?run_id={run_id}", cookie)
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "unknown run_id for this project"})

    def test_finished_run_no_thread_running_still_returns_full_history(self):
        run_id = self._make_run(status="finished")
        _append_jsonl(teamsmod._transcript_path(run_id), _envelope("lead", 1, text="done"))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/events", cookie)
        self.assertEqual(status, 200)
        self.assertEqual([e["text"] for e in payload["events"]], ["done"])


# ─── GET /projects/<name>/team/inbox (backlog item 6f part 1, docs/spec.md) ──
class TeamInboxEndpointTests(_RealHTTPTeamTestCase):
    def _make_run(self, name=_PROJ, status="blocked_ask_user"):
        os.makedirs(os.path.join(self.projects_dir, name), exist_ok=True)
        run_id = teamsmod._run_id()
        state = teamsmod._new_state(run_id, os.path.join(self.projects_dir, name),
                                    {"kind": "ollama", "name": "m", "tier": 1}, [], "task",
                                    project_name=name)
        state["status"] = status
        teamsmod._persist(state)
        return run_id

    def test_unknown_project_404(self):
        cookie = self._login()
        status, payload = self._get("/projects/nope/team/inbox", cookie)
        self.assertEqual(status, 404)

    def test_no_run_ever_started_pending_false(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"pending": False})

    def test_run_not_blocked_pending_false(self):
        self._make_run(status="running")
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(payload, {"pending": False})

    def test_genuinely_blocked_returns_exact_persisted_question_shape(self):
        run_id = self._make_run()
        inbox = {"question": "which way?", "header": "Choice", "multi_select": False,
                 "options": [{"label": "a", "description": "a"}, {"label": "b", "description": "b"}]}
        with open(teamsmod._inbox_path(run_id), "w") as f:
            json.dump(inbox, f)
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"pending": True, "run_id": run_id, **inbox})

    def test_missing_inbox_json_still_pending_true_with_fallback_question(self):
        self._make_run()  # blocked, but inbox.json never written
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["pending"])
        self.assertTrue(payload["question"])  # non-empty fallback, never blank
        self.assertEqual(payload["options"], [])

    def test_malformed_inbox_json_still_pending_true_with_fallback_question(self):
        run_id = self._make_run()
        with open(teamsmod._inbox_path(run_id), "w") as f:
            f.write("not valid json")
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertTrue(payload["pending"])
        self.assertTrue(payload["question"])

    def test_cross_project_run_id_404(self):
        run_a = self._make_run(name=_PROJ_A)
        os.makedirs(os.path.join(self.projects_dir, _PROJ_B))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ_B}/team/inbox?run_id={run_a}", cookie)
        self.assertEqual(status, 404)

    # ── run_id path-traversal validation (docs/BACKLOG.md item 11(b)) ───

    def test_path_traversal_run_id_404_planted_file_never_opened(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        planted = _plant_traversal_target(self.tmp)
        cookie = self._login()
        status, payload = _get_forbidding_open_of(
            self, planted,
            lambda: self._get(f"/projects/{_PROJ}/team/inbox?run_id={TRAVERSAL_RUN_ID}", cookie))
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "unknown run_id for this project"})

    def test_malformed_non_traversal_run_id_404_no_500(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox?run_id=not-a-real-run", cookie)
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "unknown run_id for this project"})

    def test_inbox_reflects_only_genuinely_pending_across_a_resolve_and_repoll_sequence(self):
        # Reviewer-added: docs/spec.md never explicitly walks a
        # resolve-then-re-poll sequence for GET .../team/inbox -- confirm
        # the route doesn't keep reporting a just-resolved question as
        # still pending. Uses the same no-op _run_team_in_background
        # monkeypatch idiom TeamResolveEndpointTests' own
        # test_cli_and_route_produce_identical_persisted_state_for_same_input
        # already establishes, so the assertion below isn't racing an
        # unrelated background drive.
        orig_drive = appmod._run_team_in_background
        appmod._run_team_in_background = lambda *a, **kw: None
        self.addCleanup(setattr, appmod, "_run_team_in_background", orig_drive)

        run_id = self._make_run()
        with open(teamsmod._inbox_path(run_id), "w") as f:
            json.dump({"question": "which way?", "header": "Choice",
                       "options": [], "multi_select": False}, f)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertTrue(payload["pending"])

        status2, payload2 = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                       {"answer": "go left", "code": code})
        self.assertEqual(status2, 200, payload2)

        status3, payload3 = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status3, 200)
        self.assertEqual(payload3, {"pending": False})

    # ── blocked_board_write branch (backlog item 7 part 2, docs/spec.md §2) ──

    def _make_board_write_run(self, name=_PROJ, write_inbox=True,
                              verb="set_status", ref=42, value="In progress",
                              note="moving to in-progress", current_value=None):
        run_id = self._make_run(name=name, status="blocked_board_write")
        if write_inbox:
            inbox = {"kind": "board_write", "verb": verb, "ref": ref, "value": value,
                     "note": note, "current_value": current_value or {},
                     "proposed_at": "2026-08-14T12:00:00Z"}
            with open(teamsmod._inbox_path(run_id), "w") as f:
                json.dump(inbox, f)
        return run_id

    def test_board_write_genuinely_blocked_returns_exact_persisted_proposal_shape_plus_subject(self):
        run_id = self._make_board_write_run(
            current_value={"status_id": 1, "status_name": "New"})
        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://taiga", "tok", 7),
            get_userstory=lambda *a, **k: {"subject": "Implement auth system"})
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {
            "pending": True, "run_id": run_id, "kind": "board_write",
            "verb": "set_status", "ref": 42, "value": "In progress",
            "note": "moving to in-progress",
            "current_value": {"status_id": 1, "status_name": "New"},
            "proposed_at": "2026-08-14T12:00:00Z",
            "subject": "Implement auth system"})

    def test_board_write_taiga_unreachable_degrades_gracefully_no_subject(self):
        run_id = self._make_board_write_run()

        def _raise(*a, **k):
            raise teamsmod.taiga_board.TaigaPushError("connection refused")
        _patch_taiga_board(self, resolve_session=_raise)
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["pending"])
        self.assertEqual(payload["kind"], "board_write")
        self.assertEqual(payload["verb"], "set_status")
        self.assertEqual(payload["ref"], 42)
        self.assertNotIn("subject", payload)

    def test_board_write_missing_inbox_json_still_pending_true_with_fallback(self):
        run_id = self._make_board_write_run(write_inbox=False)
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["pending"])
        self.assertEqual(payload["kind"], "board_write")
        self.assertIsNone(payload["verb"])
        self.assertTrue(payload["note"])  # non-empty fallback, never blank

    def test_board_write_malformed_inbox_json_still_pending_true_with_fallback(self):
        run_id = self._make_board_write_run()
        with open(teamsmod._inbox_path(run_id), "w") as f:
            f.write("not valid json")
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["pending"])
        self.assertIsNone(payload["verb"])
        self.assertTrue(payload["note"])

    def test_ask_user_branch_response_shape_unchanged_regression(self):
        # docs/spec.md §2: "the ask_user branch is entirely unchanged --
        # same response shape". Confirms the new board_write branch above
        # was added alongside, not in place of, the existing ask_user path.
        run_id = self._make_run()
        inbox = {"question": "which way?", "header": "Choice", "multi_select": False,
                 "options": [{"label": "a", "description": "a"}]}
        with open(teamsmod._inbox_path(run_id), "w") as f:
            json.dump(inbox, f)
        cookie = self._login()
        status, payload = self._get(f"/projects/{_PROJ}/team/inbox", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"pending": True, "run_id": run_id, **inbox})


# ─── POST /projects/<name>/team/board-resolve (backlog item 7 part 2, docs/spec.md) ──
class TeamBoardResolveEndpointTests(_RealHTTPTeamTestCase):
    def _make_run(self, name=_PROJ, status="blocked_board_write", write_inbox=True,
                  verb="set_status", ref=42, value="In progress"):
        os.makedirs(os.path.join(self.projects_dir, name), exist_ok=True)
        run_id = teamsmod._run_id()
        state = teamsmod._new_state(run_id, os.path.join(self.projects_dir, name),
                                    {"kind": "ollama", "name": "m", "tier": 1}, [], "task",
                                    project_name=name)
        state["status"] = status
        teamsmod._persist(state)
        if write_inbox and status == "blocked_board_write":
            with open(teamsmod._inbox_path(run_id), "w") as f:
                json.dump({"kind": "board_write", "verb": verb, "ref": ref, "value": value,
                          "note": None, "current_value": {}, "proposed_at": "2026-08-14T12:00:00Z"}, f)
        return run_id

    def test_unknown_project_404(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/nope/team/board-resolve", cookie,
                                     {"action": "approve", "code": code})
        self.assertEqual(status, 404)

    def test_not_blocked_400_no_resolve_call_no_thread(self):
        run_id = self._make_run(status="running", write_inbox=False)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "approve", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("no pending board write", payload["error"])
        self.assertEqual(teamsmod._load_state(run_id)["status"], "running")
        self.assertIsNone(appmod._team_threads_get(_PROJ))

    def test_no_run_at_all_400(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "approve", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("no run found", payload["error"])

    def test_explicit_run_id_for_a_different_project_400(self):
        run_a = self._make_run(name=_PROJ_A)
        os.makedirs(os.path.join(self.projects_dir, _PROJ_B))
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ_B}/team/board-resolve", cookie,
                                     {"action": "approve", "run_id": run_a, "code": code})
        self.assertEqual(status, 400)
        self.assertIn("different project", payload["error"])

    def test_path_traversal_run_id_400_planted_file_never_opened_no_thread_started(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        planted = _plant_traversal_target(self.tmp)
        cookie = self._login()
        code = self._totp_code()
        status, payload = _get_forbidding_open_of(
            self, planted,
            lambda: self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                               {"action": "approve", "run_id": TRAVERSAL_RUN_ID, "code": code}))
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "no run found for this project"})
        self.assertIsNone(appmod._team_threads_get(_PROJ))

    def test_invalid_action_400_before_resolve_called(self):
        run_id = self._make_run()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "maybe", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("action must be", payload["error"])
        self.assertEqual(teamsmod._load_state(run_id)["status"], "blocked_board_write")
        self.assertTrue(os.path.isfile(teamsmod._inbox_path(run_id)))

    def test_missing_action_400(self):
        self._make_run()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie, {"code": code})
        self.assertEqual(status, 400)
        self.assertIn("action must be", payload["error"])

    def _record_team_threads(self):
        """
        docs/test-review.md (item 7 part 2 review, must-fix #1): the two
        success-path tests below previously only asserted
        resolve_board_write()'s own SYNCHRONOUS side effects (inbox move,
        history entry), which are satisfied whether or not the route ever
        starts the background driving thread -- proven vacuous by the
        reviewer's revert-and-fail (deleting the route's entire
        cancel_event/Thread/_team_threads_set()/t.start() block left all 12
        tests in this class passing). Fixed per the reviewer's recommended
        fix (a): rebind app.py's own module-level `threading` name to a
        thin proxy whose Thread is a recording subclass (records target +
        args on construction, then delegates to the real threading.Thread),
        so the background thread still genuinely runs (existing
        inbox/history assertions stay meaningful) while giving an
        unambiguous, race-free record that _run_team_in_background() was
        actually dispatched -- unlike the weaker `elapsed < 3.0` timing
        proxy TeamResolveEndpointTests uses, which a fast stub could
        satisfy even with no thread at all.
        """
        started = []
        real_threading = appmod.threading

        class _RecordingThread(threading.Thread):
            def __init__(self, target=None, args=(), **kwargs):
                started.append((target, args))
                super().__init__(target=target, args=args, **kwargs)

        class _FakeThreadingModule:
            """
            Only overrides Thread; everything else (Event, Lock, ...)
            delegates to the real `threading` module. Rebinds app.py's own
            module-level `threading` name (appmod.threading) rather than
            mutating the shared `threading` module's Thread attribute --
            the latter would also intercept ThreadingHTTPServer's own
            per-connection threads (it does its own `import threading`
            against the same module object), which is what made an earlier
            version of this fixture record 3 threads instead of 1.
            """
            Thread = _RecordingThread

            def __getattr__(self, name):
                return getattr(real_threading, name)

        appmod.threading = _FakeThreadingModule()
        self.addCleanup(setattr, appmod, "threading", real_threading)
        return started

    def test_approve_resolves_and_starts_background_thread(self):
        run_id = self._make_run()
        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://taiga", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "version": 3},
            list_userstory_statuses=lambda *a, **k: [{"id": 2, "name": "In progress"}],
            set_status=lambda *a, **k: {"id": 1, "version": 4})
        started = self._record_team_threads()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "approve", "code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(len(started), 1)
        target, args = started[0]
        self.assertIs(target, appmod._run_team_in_background)
        self.assertEqual(args[0], _PROJ)
        self.assertEqual(args[1], run_id)
        self.assertIsInstance(args[2], threading.Event)
        deadline = time.time() + 5
        while time.time() < deadline:
            if not os.path.isfile(teamsmod._inbox_path(run_id)):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.isfile(teamsmod._inbox_resolved_path(run_id)))
        state = teamsmod._load_state(run_id)
        resolved = [h for h in state["history"] if h["tool"] == "board_write_resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["outcome_summary"], "approved and applied")

    def test_reject_resolves_and_starts_background_thread_no_taiga_call(self):
        run_id = self._make_run()

        def _fail(*a, **k):
            raise AssertionError("reject must never call Taiga")
        _patch_taiga_board(self, resolve_session=_fail)
        started = self._record_team_threads()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "reject", "code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(started), 1)
        target, args = started[0]
        self.assertIs(target, appmod._run_team_in_background)
        self.assertEqual(args[0], _PROJ)
        self.assertEqual(args[1], run_id)
        self.assertIsInstance(args[2], threading.Event)
        deadline = time.time() + 5
        while time.time() < deadline:
            if not os.path.isfile(teamsmod._inbox_path(run_id)):
                break
            time.sleep(0.05)
        state = teamsmod._load_state(run_id)
        resolved = [h for h in state["history"] if h["tool"] == "board_write_resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["outcome_summary"], "rejected by human")

    def test_totp_428_with_no_code(self):
        self._make_run()
        cookie = self._login()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "approve"})
        self.assertEqual(status, 428)

    def test_totp_403_with_wrong_code(self):
        self._make_run()
        cookie = self._login()
        status, payload = self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                     {"action": "approve", "code": "000000"})
        self.assertEqual(status, 403)

    def test_two_concurrent_resolves_exactly_one_succeeds(self):
        run_id = self._make_run()
        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://taiga", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "version": 3},
            list_userstory_statuses=lambda *a, **k: [{"id": 2, "name": "In progress"}],
            set_status=lambda *a, **k: {"id": 1, "version": 4})
        cookie = self._login()
        code = self._totp_code()
        results = []

        def _do_post():
            results.append(self._post(f"/projects/{_PROJ}/team/board-resolve", cookie,
                                      {"action": "approve", "code": code}))

        t1 = threading.Thread(target=_do_post)
        t2 = threading.Thread(target=_do_post)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, [200, 400])
        loser = next(r for r in results if r[0] == 400)
        self.assertTrue(
            "no pending board write" in loser[1]["error"]
            or "already running" in loser[1]["error"]
            or "is not blocked on board_write" in loser[1]["error"],
            loser[1]["error"])


# ─── POST /projects/<name>/team/resolve (backlog item 6f part 1, docs/spec.md) ──
class TeamResolveEndpointTests(_RealHTTPTeamTestCase):
    def _make_run(self, name=_PROJ, status="blocked_ask_user", write_inbox=True):
        os.makedirs(os.path.join(self.projects_dir, name), exist_ok=True)
        run_id = teamsmod._run_id()
        state = teamsmod._new_state(run_id, os.path.join(self.projects_dir, name),
                                    {"kind": "ollama", "name": "m", "tier": 1}, [], "task",
                                    project_name=name)
        state["status"] = status
        teamsmod._persist(state)
        if write_inbox and status == "blocked_ask_user":
            with open(teamsmod._inbox_path(run_id), "w") as f:
                json.dump({"question": "q?", "header": "Q", "options": [], "multi_select": False}, f)
        return run_id

    def test_unknown_project_404(self):
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post("/projects/nope/team/resolve", cookie,
                                     {"answer": "yes", "code": code})
        self.assertEqual(status, 404)

    def test_not_blocked_400_specific_reason_no_state_mutated(self):
        run_id = self._make_run(status="running", write_inbox=False)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "yes", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("no pending question", payload["error"])
        self.assertEqual(teamsmod._load_state(run_id)["status"], "running")

    def test_no_run_at_all_400_specific_reason(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "yes", "code": code})
        self.assertEqual(status, 400)
        self.assertIn("no run found", payload["error"])

    def test_explicit_run_id_for_a_different_project_400_specific_reason(self):
        run_a = self._make_run(name=_PROJ_A)
        os.makedirs(os.path.join(self.projects_dir, _PROJ_B))
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ_B}/team/resolve", cookie,
                                     {"answer": "yes", "run_id": run_a, "code": code})
        self.assertEqual(status, 400)
        self.assertIn("different project", payload["error"])

    # ── run_id path-traversal validation (docs/BACKLOG.md item 11(b)) ───

    def test_path_traversal_run_id_400_planted_file_never_opened_no_thread_started(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        planted = _plant_traversal_target(self.tmp)
        cookie = self._login()
        code = self._totp_code()
        status, payload = _get_forbidding_open_of(
            self, planted,
            lambda: self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                               {"answer": "yes", "run_id": TRAVERSAL_RUN_ID, "code": code}))
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "no run found for this project"})
        self.assertIsNone(appmod._team_threads_get(_PROJ))

    def test_malformed_non_traversal_run_id_400_no_500(self):
        os.makedirs(os.path.join(self.projects_dir, _PROJ))
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "yes", "run_id": "not-a-real-run", "code": code})
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "no run found for this project"})

    def test_empty_answer_400_before_any_mutation(self):
        run_id = self._make_run()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "   ", "code": code})
        self.assertEqual(status, 400)
        self.assertEqual(teamsmod._load_state(run_id)["status"], "blocked_ask_user")
        self.assertTrue(os.path.isfile(teamsmod._inbox_path(run_id)))

    def test_oversized_answer_400_before_any_mutation(self):
        run_id = self._make_run()
        orig = teamsmod.TEAM_ASK_USER_ANSWER_MAX_CHARS
        teamsmod.TEAM_ASK_USER_ANSWER_MAX_CHARS = 5
        self.addCleanup(setattr, teamsmod, "TEAM_ASK_USER_ANSWER_MAX_CHARS", orig)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "way too long", "code": code})
        self.assertEqual(status, 400)
        self.assertEqual(teamsmod._load_state(run_id)["status"], "blocked_ask_user")

    def test_genuinely_blocked_valid_answer_resolves_and_returns_immediately(self):
        run_id = self._make_run()
        cookie = self._login()
        code = self._totp_code()
        start = time.time()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "Yes, proceed", "code": code})
        elapsed = time.time() - start
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["run_id"], run_id)
        self.assertLess(elapsed, 3.0)  # never waits for the lead's next round
        # Poll until the background thread has actually persisted the move.
        deadline = time.time() + 5
        while time.time() < deadline:
            if not os.path.isfile(teamsmod._inbox_path(run_id)):
                break
            time.sleep(0.05)
        self.assertFalse(os.path.isfile(teamsmod._inbox_path(run_id)))
        self.assertTrue(os.path.isfile(teamsmod._inbox_resolved_path(run_id)))
        state = teamsmod._load_state(run_id)
        self.assertNotEqual(state["status"], "blocked_ask_user")
        answered = [h for h in state["history"] if h["tool"] == "ask_user_resolved"]
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0]["full_result_text"], "Yes, proceed")

    def test_two_concurrent_resolves_exactly_one_succeeds(self):
        run_id = self._make_run()
        cookie = self._login()
        code = self._totp_code()
        results = []

        def _do_post():
            results.append(self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                      {"answer": "an answer", "code": code}))

        t1 = threading.Thread(target=_do_post)
        t2 = threading.Thread(target=_do_post)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, [200, 400])
        loser = next(r for r in results if r[0] == 400)
        # docs/spec.md "Edge cases" own check-then-act window (deliberately
        # not lock-guarded -- "the same single-writer assumption every
        # other run.json mutator in this codebase already carries"): for
        # two GENUINELY simultaneous callers (both threads start with zero
        # synchronization here, tighter than a realistic back-to-back
        # double-submit), the loser can be caught at any of three points --
        # resolve_ask_user()'s own upfront status check, its own move-then-
        # persist step losing the race (an OSError caught and turned into
        # the same shaped result -- see resolve_ask_user()'s own docstring,
        # found live by this exact test), or the route's own defensive
        # "already running" check right before it would otherwise spawn a
        # second driving thread. Whichever one catches it, the loser always
        # gets a clean, shaped 400 (never an unhandled exception/connection
        # reset) and the one invariant docs/spec.md actually guarantees --
        # "exactly one thread is ever spawned for one answer" -- holds;
        # which of the three reasons the loser gets is timing-dependent,
        # not part of that guarantee.
        self.assertTrue(
            "no pending question" in loser[1]["error"]
            or "already running" in loser[1]["error"]
            or "is not blocked on ask_user" in loser[1]["error"],
            loser[1]["error"])

    def test_loser_whose_exists_check_lands_after_winner_already_renamed_does_not_report_ok(self):
        # Reviewer-added repro (docs/spec.md "Edge cases": "the second's own
        # freshly-reloaded state ... sees status != blocked_ask_user and
        # returns [a] 400"). resolve_ask_user() used to only raise OSError
        # when its OWN os.replace() call collided with another rename in
        # flight (the crash the developer already fixed). But a genuinely-
        # concurrent loser whose own `_load_state()` read completed BEFORE
        # the winner persisted, and whose own `os.path.exists(inbox_path)`
        # check then landed AFTER the winner's real os.replace() had already
        # completed, hit neither guard: `if os.path.exists(inbox_path):` was
        # simply False, no os.replace() was attempted, no OSError was
        # raised -- the loser fell straight through to
        # `state["status"] = "running"; _persist(state)` and returned
        # {"ok": True}, built from its own STALE in-memory state, silently
        # overwriting the winner's already-persisted history. Fixed by
        # removing the separate exists() check entirely and making the
        # unconditional os.replace() call the sole arbiter of "did I win"
        # (see resolve_ask_user()'s own docstring). This test is now the
        # permanent regression guard for that fix.
        #
        # Reproduced deterministically (no thread-timing flakiness) via a
        # one-shot hook on the loser's own `_load_state()` call: at the
        # exact moment the loser has read its (still blocked_ask_user)
        # snapshot but before it proceeds, a REAL winning resolve_ask_user()
        # call is run to completion for real (real os.replace(), real
        # persist) -- exactly the interleaving the spec's own prose assumes
        # can happen, just made deterministic instead of thread-scheduled.
        run_id = self._make_run()
        orig_load_state = teamsmod._load_state
        hook_fired = []

        def _patched_load_state(rid):
            snapshot = orig_load_state(rid)
            if rid == run_id and not hook_fired:
                hook_fired.append(True)
                teamsmod._load_state = orig_load_state  # avoid re-entering this hook
                winner_result = teamsmod.resolve_ask_user(run_id, "winner answer")
                self.assertTrue(winner_result["ok"], winner_result)
                teamsmod._load_state = _patched_load_state
            return snapshot

        teamsmod._load_state = _patched_load_state
        self.addCleanup(setattr, teamsmod, "_load_state", orig_load_state)

        loser_result = teamsmod.resolve_ask_user(run_id, "loser answer")

        final_state = teamsmod._load_state(run_id)
        answered = [h for h in final_state["history"] if h["tool"] == "ask_user_resolved"]

        # This is the permanent regression guard for the fix that closed
        # this exact race: resolve_ask_user() no longer gates the move on a
        # separate `if os.path.exists(inbox_path):` check-then-act -- it
        # calls os.replace() unconditionally and lets THAT call's own
        # FileNotFoundError be the sole arbiter of "did I win". So the
        # loser in this timing now hits the same OSError branch the
        # genuinely-simultaneous-callers race already exercises: it reports
        # {"ok": False, ...} and never touches (let alone persists) its own
        # stale in-memory state, so the winner's already-persisted
        # ask_user_resolved history entry survives untouched.
        self.assertFalse(loser_result["ok"], loser_result)
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0]["full_result_text"], "winner answer")  # winner's entry survives

    def test_loser_leaves_no_stale_transcript_entry(self):
        # docs/spec.md (6f part 1b): resolve_ask_user() used to call
        # _append_history() -- which writes transcript.jsonl synchronously,
        # unconditionally, with no rollback -- BEFORE the os.replace() win/
        # lose decision point. A losing caller's in-memory state["history"]
        # mutation was correctly discarded (it never reaches _persist()),
        # but its transcript.jsonl write had already hit disk with no way to
        # undo it, leaving a permanent, spurious tool_result entry for an
        # answer nobody actually accepted. Fixed by moving the
        # _append_history() call to after os.replace() has already
        # succeeded, so only the winner ever writes to the transcript.
        #
        # Reused verbatim: the same deterministic one-shot hook on
        # _load_state() that
        # test_loser_whose_exists_check_lands_after_winner_already_renamed_
        # does_not_report_ok uses above, just asserting transcript.jsonl
        # content instead of state["history"] content.
        run_id = self._make_run()
        orig_load_state = teamsmod._load_state
        hook_fired = []

        def _patched_load_state(rid):
            snapshot = orig_load_state(rid)
            if rid == run_id and not hook_fired:
                hook_fired.append(True)
                teamsmod._load_state = orig_load_state  # avoid re-entering this hook
                winner_result = teamsmod.resolve_ask_user(run_id, "winner answer")
                self.assertTrue(winner_result["ok"], winner_result)
                teamsmod._load_state = _patched_load_state
            return snapshot

        teamsmod._load_state = _patched_load_state
        self.addCleanup(setattr, teamsmod, "_load_state", orig_load_state)

        loser_result = teamsmod.resolve_ask_user(run_id, "loser answer")
        self.assertFalse(loser_result["ok"], loser_result)

        with open(teamsmod._transcript_path(run_id)) as f:
            transcript_lines = [json.loads(line) for line in f if line.strip()]
        resolved_entries = [e for e in transcript_lines if e["kind"] == "tool_result"
                            and e.get("meta", {}).get("resolved")]
        # Exactly one ask_user_resolved entry -- the winner's -- and never
        # the loser's, no matter how the loser's own call is timed relative
        # to the winner's.
        self.assertEqual(len(resolved_entries), 1)
        self.assertEqual(resolved_entries[0]["text"], "winner answer")

    def test_cli_and_route_produce_identical_persisted_state_for_same_input(self):
        # docs/spec.md's own acceptance criterion: the CLI's team-resolve
        # and this route both funnel through the same shared
        # teams.resolve_ask_user() -- proven by comparing each path's own
        # persisted output immediately after ITS OWN resolve step. The
        # route's subsequent background-thread DRIVE (team_run(), which
        # this project's own "ollama" test lead fails immediately without
        # TEAM_LLM_BASE_URL configured) is a separate, deliberately ASYNC
        # concern (unlike the CLI's own synchronous _drive_and_report()) --
        # comparing FULL final state after however long that unrelated
        # background thread happens to take would conflate two different
        # things this criterion isn't about, so it's neutralized here via a
        # harmless no-op patch (same monkeypatch idiom _patch_tmux() above
        # already establishes for this exact class of "swap out a real
        # side effect for a no-op" need).
        orig_drive = appmod._run_team_in_background
        appmod._run_team_in_background = lambda *a, **kw: None
        self.addCleanup(setattr, appmod, "_run_team_in_background", orig_drive)

        run_id_cli = self._make_run(name=_PROJ)
        result_cli = teamsmod.resolve_ask_user(run_id_cli, "same answer")
        self.assertTrue(result_cli["ok"])
        state_cli = teamsmod._load_state(run_id_cli)

        run_id_route = self._make_run(name=_PROJ)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/resolve", cookie,
                                     {"answer": "same answer", "run_id": run_id_route, "code": code})
        self.assertEqual(status, 200)
        state_route = teamsmod._load_state(run_id_route)

        self.assertEqual(state_cli["status"], state_route["status"])
        cli_hist = [{k: v for k, v in h.items() if k != "log_path"} for h in state_cli["history"]]
        route_hist = [{k: v for k, v in h.items() if k != "log_path"} for h in state_route["history"]]
        self.assertEqual(cli_hist, route_hist)


# ─── POST /projects/<name>/team/start with a submitted composition ────────
class TeamStartWithCompositionEndpointTests(_RealHTTPTeamTestCase):
    def _roster_engines(self):
        self._write_fast_finisher_engine("lead2")
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        _write_engine_file(self.engines_dir, "other.engine", """\
            LABEL=Other
            CMD=unused
            HEADLESS_CMD=echo unused
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)

    def test_valid_submitted_composition_used_instead_of_default_and_persisted(self):
        self._project(_PROJ)
        self._roster_engines()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "helper"},
            "members": [{"kind": "engine", "name": "lead2"}, {"kind": "engine", "name": "other"}],
        })
        self.assertEqual(status, 200, payload)
        # NOT default_team_composition()'s own pick (which would choose
        # lead2 as lead, tier 2) -- the operator's own submitted choice won.
        self.assertEqual(payload["lead"], {"kind": "engine", "name": "helper", "tier": 3})
        self.assertEqual(sorted(payload["members"]), ["lead2", "other"])
        state = teamsmod._load_state(payload["run_id"])
        self.assertEqual(state["lead"], {"kind": "engine", "name": "helper", "tier": 3})
        comps = teamsmod.load_compositions()
        self.assertEqual(comps[_PROJ]["lead"], {"kind": "engine", "name": "helper"})
        self.assertEqual(sorted(comps[_PROJ]["members"]), ["lead2", "other"])

    def test_empty_members_rejected_no_launch_no_side_effects(self):
        self._project(_PROJ)
        self._roster_engines()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "helper"}, "members": [],
        })
        self.assertEqual(status, 400)
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))
        self.assertEqual(teamsmod.load_compositions(), {})

    def test_duplicate_member_rejected(self):
        self._project(_PROJ)
        self._roster_engines()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "helper"},
            "members": [{"kind": "engine", "name": "lead2"}, {"kind": "engine", "name": "lead2"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("duplicate", payload["error"].lower())

    def test_lead_also_in_members_rejected(self):
        self._project(_PROJ)
        self._roster_engines()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "helper"},
            "members": [{"kind": "engine", "name": "helper"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("also be a teammate", payload["error"])

    def test_unknown_lead_rejected_naming_it_never_falls_back_to_default(self):
        self._project(_PROJ)
        self._roster_engines()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "ghost-engine-removed-since-saved"},
            "members": [{"kind": "engine", "name": "helper"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("ghost-engine-removed-since-saved", payload["error"])
        # Never silently substituted with default_team_composition()'s pick.
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))

    def test_stale_saved_composition_referencing_a_removed_engine_is_rejected(self):
        # Edge case (docs/spec.md): a composition saved while an engine
        # existed, then that engine's engines.d file is removed before the
        # next start -- rejected, not silently substituted.
        self._project(_PROJ)
        self._roster_engines()
        teamsmod.save_composition(_PROJ, {"kind": "engine", "name": "helper"},
                                  [{"kind": "engine", "name": "other"}])
        os.remove(os.path.join(self.engines_dir, "helper.engine"))
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "helper"},
            "members": [{"kind": "engine", "name": "other"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("helper", payload["error"])

    def test_no_lead_members_in_body_is_byte_for_byte_unchanged_default_behavior(self):
        self._project(_PROJ)
        self._roster_engines()
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie,
                                     {"task": "do it", "code": code})
        self.assertEqual(status, 200, payload)
        # default_team_composition()'s own pick (lead2, tier 2).
        self.assertEqual(payload["lead"], {"kind": "engine", "name": "lead2", "tier": 2})
        # No composition read or written for this call.
        self.assertEqual(teamsmod.load_compositions(), {})

    def test_saved_on_validated_start_even_if_launch_team_itself_later_fails(self):
        # docs/spec.md: "saved on successful validation, independent of
        # whether launch_team() itself later succeeds" -- simulated here by
        # a team session name collision (a real, already-running team).
        self._project(_PROJ)
        self._roster_engines()
        result = teamsmod.launch_team(os.path.join(self.projects_dir, _PROJ), "already running",
                                      {"kind": "ollama", "name": "m", "tier": 1}, ["helper"])
        self.assertTrue(result["ok"], result)
        cookie = self._login()
        code = self._totp_code()
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "other"},
            "members": [{"kind": "engine", "name": "helper"}],
        })
        self.assertEqual(status, 400)  # launch_team() itself refuses (session already taken)
        comps = teamsmod.load_compositions()
        self.assertEqual(comps[_PROJ]["lead"], {"kind": "engine", "name": "other"})


# ─── Restart persistence for a saved composition (docs/spec.md acceptance
# criteria: "verified by restarting the process in a test, not just
# re-calling the function in-process") ──────────────────────────────────────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class CompositionSurvivesRealProcessRestartTests(_RealHTTPTeamTestCase):
    """load_compositions() has no in-memory cache at all -- it re-reads
    TEAM_STATE_DIR/compositions.json from disk on every single call, so
    there is nothing a same-process re-call could get subtly wrong that a
    fresh process wouldn't also get right. Still, the acceptance criterion
    explicitly calls for a genuine process-boundary crossing (guards
    against a FUTURE regression that adds an in-memory cache with no
    invalidation) -- proven here with a real, separate `python3` subprocess
    running its OWN fresh ThreadingHTTPServer instance against the exact
    same TEAM_STATE_DIR/PROJECTS_DIR/ENGINES_DIR this test's own server
    uses, the same "impractical to literally kill+restart this process, so
    prove the property that actually matters via a genuinely independent
    process" reasoning ServiceRestartSimulationTests' own docstring already
    establishes for the analogous _team_threads case."""

    def test_get_status_from_a_freshly_started_separate_process_reflects_the_saved_composition(self):
        self._project(_PROJ)
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
        status, payload = self._post(f"/projects/{_PROJ}/team/start", cookie, {
            "task": "do it", "code": code,
            "lead": {"kind": "engine", "name": "helper"},
            "members": [{"kind": "engine", "name": "lead2"}],
        })
        self.assertEqual(status, 200, payload)

        script = textwrap.dedent(f"""\
            import sys, json, threading, urllib.request
            sys.path.insert(0, {APP_DIR!r})
            import app as appmod
            from http.server import ThreadingHTTPServer
            server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            base = "http://127.0.0.1:%d" % port
            req = urllib.request.Request(base + "/login", method="POST",
                data=json.dumps({{"username": "testuser", "password": "testpass"}}).encode(),
                headers={{"Content-Type": "application/json"}})
            resp = urllib.request.urlopen(req)
            cookie = resp.headers.get("Set-Cookie").split(";")[0]
            req2 = urllib.request.Request(base + "/status", headers={{"Cookie": cookie}})
            resp2 = urllib.request.urlopen(req2)
            sys.stdout.write(resp2.read().decode())
            server.shutdown()
            """)
        env = dict(os.environ)
        env["PROJECTS_DIR"] = self.projects_dir
        env["ENGINES_DIR"] = self.engines_dir
        env["TEAM_STATE_DIR"] = self.state_dir
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(r.stdout)
        by_name = {i["name"]: i for i in s["instances"]}
        self.assertEqual(by_name[_PROJ]["team"]["composition"],
                         {"lead": {"kind": "engine", "name": "helper"}, "members": ["lead2"]})


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
        repo = self._project(_PROJ)
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
        status, payload = self._post(f"/projects/{_PROJ}/team/stop", cookie, {"code": code})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(appmod.tmux_has(session))
        self.assertFalse(os.path.isdir(teamsmod._worktree_path(repo, "helper")))
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["status"], "stopped")

    def test_status_shows_running_truthfully_until_reap_runs_then_flips_to_error(self):
        repo = self._project(_PROJ)
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
        self.assertEqual(by_name[_PROJ]["team"]["status"], "running")

        # Force the reap pass due (docs/spec.md acceptance criteria:
        # "monkeypatched to 0") -- the NEXT /status poll runs
        # _team_reap_if_due(), which finds status=="running" with no
        # matching alive entry in _team_threads and marks it "error".
        appmod.TEAM_REAP_POLL_INTERVAL_SECONDS = 0
        appmod._team_reap_last_at = 0.0
        s2 = self._get_status(cookie)
        by_name2 = {i["name"]: i for i in s2["instances"]}
        self.assertEqual(by_name2[_PROJ]["team"]["status"], "error")
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
        self.workdir = os.path.join(self.projects_dir, _PROJ)
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
                                    project_name=_PROJ)
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
