"""
Tests for team session lifecycle -- worktrees + tmux dashboard session
(backlog item 6d, part 1 -- docs/spec.md). Mirrors tests/test_teams_
headless.py's/test_teams_lead.py's own tiered structure, extended for real
git:

Tier 1 -- pure unit, no subprocess, no tmux, no git: _worktree_path()/
_agent_log_path()/_team_session_name()/_iso_to_epoch(); _new_state()'s two
additive fields; launch_team()'s rollback ordering (monkeypatched worktree
creation, no real git/tmux involved); sweep_dead_teams()'s status-transition
decisions against hand-constructed run.json fixtures (mirrors 6c's own
InProgressCrashRecoveryPureTests technique).

Tier 2 -- real git, temp directories: _validate_project_for_team() against a
real `git init`'d temp repo in each of the four states (clean, dirty-tracked,
dirty-untracked-only, detached HEAD) and a genuinely non-git directory;
_create_worktree()/_remove_worktree() against a real repo (these two do go
through a real, throwaway tmux session via _run_run_user_command() -- the
ONLY sanctioned RUN_USER crossing, TMUX patched down to plain `tmux`, same
technique tier 2/3 below use -- but exercise no DASHBOARD session/windows,
which is what "no tmux" means in docs/spec.md's own Test Plan for this
tier), including the dirty-refusal case and the branch-survives-removal
claim, both verified via real `git` commands, not assumed.

Tier 3 -- real tmux, real git, real subprocess (the bulk of the risk
surface, correspondingly the bulk of this file), same TMUX monkeypatch/
real-session technique tests/test_teams_headless.py already established:
launch_team()/stop_team()/sweep_dead_teams() end to end (session/windows/
worktrees all real, asserted via `tmux list-windows`/`git worktree list`);
a real team_run()-driven run with a stubbed lead that delegates twice to the
same teammate, asserting worktree isolation + session continuity + dashboard
log accumulation all hold simultaneously; the strict-umask permission test
(reusing 6a's own fixture pattern); the engine-name reservation, against a
real scratch .engine file; and a real crash-then-relaunch-then-reap
regression test for a genuine defect found while building this: a
team-<project> session NAME (and a .teams/<agent> worktree PATH) is scoped
to the PROJECT, not to any one run_id, so a stale sweep/stop of an old,
terminal run could otherwise kill/destroy a NEWER run's live session/
worktree sitting at the identical name/path -- see
SessionOwnershipCollisionRealTmuxTests's own docstring for the mechanism.

Run with:
    python3 -m unittest discover -s tests -v
"""
import contextlib
import io
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

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-lifecycle-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
import teams as teamsmod  # noqa: E402


def _patch_tmux(testcase, argv):
    """Patches TMUX in both app.py (tmux_has() reads its own module
    namespace) and teams.py (its own `from app import TMUX` binding) -- see
    tests/test_teams_headless.py's identical helper/docstring."""
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


# Test-isolation: every real (non-mocked) team-<project> tmux session name
# below is otherwise a bare, unscoped literal (team-demo, team-proj, etc.)
# -- a global machine property this process does not own. Two concurrent
# test-suite runs (or a suite run alongside a real switchboard session)
# would collide directly on the tmux server's own session namespace, and
# this file's own tearDown sweep (_kill_leftover_team_sessions() below)
# would then destroy the OTHER run's live sessions too. Same technique
# tests/test_teams_headless.py's own _RUN_ID_SCOPE/_SESSION_PREFIX already
# establishes -- scoping every literal project name below with this
# process's own pid, and narrowing the sweep to match only THIS process's
# own scoped sessions.
_RUN_ID_SCOPE = f"p{os.getpid()}"


def _scoped(name):
    return f"{name}-{_RUN_ID_SCOPE}"


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, check=check)


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")


def _tmux_windows(session):
    r = subprocess.run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
                       capture_output=True, text=True)
    return r.stdout.split()


def _tmux_capture(session, window):
    r = subprocess.run(["tmux", "capture-pane", "-t", f"{session}:{window}", "-p", "-S", "-2000"],
                       capture_output=True, text=True)
    return r.stdout


def _kill_leftover_team_sessions():
    # Scoped to this process's own team-<project>-<scope> sessions only --
    # the old unconditional "team-"/"switchboard-" prefix match would also
    # kill a CONCURRENT process's own live sessions (its own team-* dashboard
    # sessions, or its own switchboard-headless-*/switchboard-worktree-op-*
    # sessions from an entirely different test file), which is exactly the
    # cross-process collision docs/spec.md (BACKLOG item 9) fixes. No
    # "switchboard-" sweep at all: _run_run_user_command()'s own
    # switchboard-worktree-op-* sessions already self-clean unconditionally
    # in their own `finally` block (see app/teams.py), and their op_id has
    # no per-process token to scope a sweep by safely.
    r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
    for name in r.stdout.splitlines():
        if name.startswith("team-") and name.endswith(f"-{_RUN_ID_SCOPE}"):
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


def _lead(tier=1, kind="ollama", name="stub"):
    return {"kind": kind, "name": name, "tier": tier}


# ─── Tier 1: pure helpers, no subprocess, no tmux, no git ─────────────────
class PureHelperTests(unittest.TestCase):
    def test_worktree_path_is_dot_teams_slash_agent(self):
        self.assertEqual(teamsmod._worktree_path("/x/proj", "claude"), "/x/proj.teams/claude")

    def test_agent_log_path(self):
        orig = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = "/tmp/switchboard-state-x"
        try:
            self.assertEqual(
                teamsmod._agent_log_path("run1", "claude"),
                os.path.join("/tmp/switchboard-state-x", "leads", "run1", "agents", "claude.jsonl"))
        finally:
            teamsmod.TEAM_STATE_DIR = orig

    def test_team_session_name(self):
        self.assertEqual(teamsmod._team_session_name("myproj"), "team-myproj")

    def test_iso_to_epoch_round_trips_now_iso(self):
        now = teamsmod._now_iso()
        epoch = teamsmod._iso_to_epoch(now)
        self.assertAlmostEqual(epoch, time.time(), delta=5)


class RunIdRegexValidationTests(unittest.TestCase):
    """_RUN_ID_RE (docs/BACKLOG.md item 11(b), docs/spec.md "Backend
    hardening"): matches _run_id()'s own generation shape exactly
    (f"{int(time.time())}-{secrets.token_hex(6)}"). Used by app.py's three
    team/* routes to reject a client-supplied run_id at intake, before it
    ever reaches teams._run_dir()/a path-join -- see
    docs/implementation.md "Deviations from spec" for why the check lives
    at that intake point in app.py rather than inside _run_dir() itself
    (_run_dir() is also shared by internal/CLI/test callers that construct
    run_id values outside this exact shape, and must keep accepting those
    unchanged)."""

    def test_a_genuinely_generated_run_id_matches(self):
        run_id = teamsmod._run_id()
        self.assertTrue(teamsmod._RUN_ID_RE.match(run_id))

    def test_path_traversal_run_id_does_not_match(self):
        self.assertIsNone(teamsmod._RUN_ID_RE.match("../../outside/evilrun"))

    def test_run_id_with_slash_does_not_match(self):
        self.assertIsNone(
            teamsmod._RUN_ID_RE.match("1700000000-abc123def456/../../etc/passwd"))

    def test_empty_run_id_does_not_match(self):
        self.assertIsNone(teamsmod._RUN_ID_RE.match(""))

    def test_syntactically_plausible_but_nonexistent_run_id_matches(self):
        # _RUN_ID_RE only validates SHAPE, not existence -- a well-shaped
        # but never-created run_id must still match here (existence is what
        # the subsequent open() call 404s/400s on, via the route's own
        # OSError handling downstream of this check, not this regex).
        self.assertTrue(teamsmod._RUN_ID_RE.match("1700000000-abc123def456"))

    def test_uppercase_hex_does_not_match(self):
        self.assertIsNone(teamsmod._RUN_ID_RE.match("1700000000-ABC123DEF456"))

    def test_wrong_length_hex_portion_does_not_match(self):
        self.assertIsNone(teamsmod._RUN_ID_RE.match("1700000000-abc123"))

    def test_nul_byte_in_run_id_does_not_match(self):
        self.assertIsNone(
            teamsmod._RUN_ID_RE.match("1700000000-abc123def456\x00/etc/passwd"))

    def test_run_dir_itself_does_not_validate_shared_internal_helper_unchanged(self):
        # _run_dir() deliberately still joins ANY string unvalidated
        # (docs/implementation.md "Deviations from spec") -- internal
        # callers (CLI, sweep_dead_teams, many existing pure-unit test
        # fixtures) pass run_id values that never matched _RUN_ID_RE and
        # must keep working exactly as before this cycle.
        d = teamsmod._run_dir("r1")
        self.assertEqual(d, os.path.join(teamsmod._leads_root(), "r1"))


class NewStateAdditiveFieldsTests(unittest.TestCase):
    def test_defaults_are_none_and_empty_dict(self):
        state = teamsmod._new_state("r1", "/wd", _lead(), [], "task")
        self.assertIsNone(state["project_name"])
        self.assertEqual(state["worktrees"], {})

    def test_explicit_values_preserved(self):
        state = teamsmod._new_state("r1", "/wd", _lead(), ["claude"], "task",
                                    project_name="demo", worktrees={"claude": "/wd.teams/claude"})
        self.assertEqual(state["project_name"], "demo")
        self.assertEqual(state["worktrees"], {"claude": "/wd.teams/claude"})


class LaunchTeamRollbackOrderingTests(unittest.TestCase):
    """No real git/tmux -- _create_worktree()/_remove_worktree()/
    _validate_project_for_team()/tmux_has() are all monkeypatched out, so
    this only exercises launch_team()'s own ORDERING logic."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-rollbackstate-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir
        self._orig_validate = teamsmod._validate_project_for_team
        self._orig_tmux_has = teamsmod.tmux_has
        teamsmod._validate_project_for_team = lambda workdir: None
        teamsmod.tmux_has = lambda session: False

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        teamsmod._validate_project_for_team = self._orig_validate
        teamsmod.tmux_has = self._orig_tmux_has
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_third_member_fails_rolls_back_first_two_worktrees_and_leaves_no_state(self):
        created, removed = [], []

        def fake_create(workdir, agent, run_id):
            if agent == "gemini":
                return {"ok": False, "error": "boom: gemini failed"}
            created.append(agent)
            return {"ok": True, "path": f"{workdir}.teams/{agent}", "branch": f"team-{run_id}-{agent}"}

        def fake_remove(workdir, path):
            removed.append(path)
            return "removed"

        orig_create, orig_remove = teamsmod._create_worktree, teamsmod._remove_worktree
        teamsmod._create_worktree, teamsmod._remove_worktree = fake_create, fake_remove
        try:
            result = teamsmod.launch_team("/proj", "task", _lead(), ["claude", "codex", "gemini"])
        finally:
            teamsmod._create_worktree, teamsmod._remove_worktree = orig_create, orig_remove

        self.assertFalse(result["ok"])
        self.assertIn("boom: gemini failed", result["error"])
        self.assertEqual(created, ["claude", "codex"])
        self.assertEqual(sorted(removed), ["/proj.teams/claude", "/proj.teams/codex"])
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))  # nothing persisted


class SweepDeadTeamsPureTests(unittest.TestCase):
    """Hand-constructed run.json fixtures at every relevant status/age
    combination, mirrors 6c's own InProgressCrashRecoveryPureTests
    technique. tmux_has()/_remove_worktree() monkeypatched -- no real
    git/tmux."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-sweepstate-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir
        self._orig_ttl = teamsmod.TEAM_SESSION_STALE_TTL_SECONDS
        self._orig_tmux_has = teamsmod.tmux_has
        self._orig_remove = teamsmod._remove_worktree
        self._extra_dirs = []

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = self._orig_ttl
        teamsmod.tmux_has = self._orig_tmux_has
        teamsmod._remove_worktree = self._orig_remove
        shutil.rmtree(self.state_dir, ignore_errors=True)
        for d in self._extra_dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _make_run(self, run_id, status, project_name="demo", worktrees=None, age_seconds=0):
        state = teamsmod._new_state(run_id, "/proj", _lead(), [], "task",
                                    project_name=project_name, worktrees=worktrees or {})
        state["status"] = status
        teamsmod._persist(state)
        if age_seconds:
            path = teamsmod._run_json_path(run_id)
            with open(path) as f:
                data = json.load(f)
            data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime(time.time() - age_seconds))
            with open(path, "w") as f:
                json.dump(data, f)
        return state

    def _real_dir(self):
        d = tempfile.mkdtemp(prefix="switchboard-lifecycle-fakewt-")
        self._extra_dirs.append(d)
        return d

    def test_running_with_dead_session_marked_error_no_same_pass_sweep(self):
        self._make_run("r1", "running", worktrees={"claude": "/proj.teams/claude"})
        teamsmod.tmux_has = lambda session: False
        teamsmod._remove_worktree = lambda workdir, path: self.fail("must not sweep in the same pass")
        results = teamsmod.sweep_dead_teams()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "marked_error")
        state = teamsmod._load_state("r1")
        self.assertEqual(state["status"], "error")
        self.assertIn("gone", state["error"])
        self.assertEqual(state["worktrees"], {"claude": "/proj.teams/claude"})  # untouched this pass

    def test_terminal_past_ttl_sweeps_and_clears_only_removed_entries(self):
        wt_dir = self._real_dir()
        claude_path = os.path.join(wt_dir, "claude")
        codex_path = os.path.join(wt_dir, "codex")
        os.makedirs(claude_path)
        os.makedirs(codex_path)
        self._make_run("r2", "finished", worktrees={"claude": claude_path, "codex": codex_path},
                       age_seconds=1000)
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 100
        teamsmod.tmux_has = lambda session: False
        outcomes = {"claude": "removed", "codex": "dirty"}
        teamsmod._remove_worktree = lambda workdir, path: outcomes[os.path.basename(path)]
        results = teamsmod.sweep_dead_teams()
        self.assertEqual(results[0]["action"], "swept")
        self.assertEqual(results[0]["detail"]["worktrees"], {"claude": "removed", "codex": "dirty"})
        state = teamsmod._load_state("r2")
        self.assertEqual(state["worktrees"], {"codex": codex_path})  # removed dropped, dirty kept

    def test_within_ttl_not_swept(self):
        self._make_run("r3", "error", worktrees={}, age_seconds=1)
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 1000
        teamsmod.tmux_has = lambda session: False
        self.assertEqual(teamsmod.sweep_dead_teams(), [])

    def test_blocked_ask_user_never_swept_even_with_ttl_zero(self):
        self._make_run("r4", "blocked_ask_user", worktrees={"claude": "/nope"}, age_seconds=100000)
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 0
        teamsmod.tmux_has = lambda session: True
        self.assertEqual(teamsmod.sweep_dead_teams(), [])
        state = teamsmod._load_state("r4")
        self.assertEqual(state["status"], "blocked_ask_user")
        self.assertEqual(state["worktrees"], {"claude": "/nope"})

    def test_bare_team_start_with_no_project_name_is_skipped_entirely(self):
        self._make_run("r5", "running", project_name=None, worktrees={})
        teamsmod.tmux_has = lambda session: self.fail("must never check tmux for a bare run")
        self.assertEqual(teamsmod.sweep_dead_teams(), [])

    def test_corrupt_run_json_skipped_not_fatal_to_other_runs(self):
        self._make_run("r6", "finished")
        with open(teamsmod._run_json_path("r6"), "w") as f:
            f.write("{not valid json")
        self._make_run("r7", "running", worktrees={})
        teamsmod.tmux_has = lambda session: True  # r7's session is alive, nothing to do for it
        results = teamsmod.sweep_dead_teams()
        r6_result = next(r for r in results if r["run_id"] == "r6")
        self.assertEqual(r6_result["action"], "skipped")


# ─── Tier 2: real git, temp directories ───────────────────────────────────
class ValidateProjectForTeamRealGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-validate-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_repo_on_a_branch_is_none(self):
        repo = os.path.join(self.tmp, "clean")
        _init_repo(repo)
        self.assertIsNone(teamsmod._validate_project_for_team(repo))

    def test_dirty_tracked_file(self):
        repo = os.path.join(self.tmp, "dirty-tracked")
        _init_repo(repo)
        with open(os.path.join(repo, "README.md"), "a") as f:
            f.write("more\n")
        err = teamsmod._validate_project_for_team(repo)
        self.assertIn("uncommitted", err)

    def test_dirty_untracked_only(self):
        repo = os.path.join(self.tmp, "dirty-untracked")
        _init_repo(repo)
        with open(os.path.join(repo, "new.txt"), "w") as f:
            f.write("x")
        err = teamsmod._validate_project_for_team(repo)
        self.assertIn("uncommitted", err)

    def test_detached_head(self):
        repo = os.path.join(self.tmp, "src")
        _init_repo(repo)
        clone = os.path.join(self.tmp, _scoped("detached"))
        _git(self.tmp, "clone", "-q", repo, clone)
        sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
        _git(clone, "checkout", "-q", sha)
        err = teamsmod._validate_project_for_team(clone)
        self.assertIn("detached", err)

    def test_non_git_directory(self):
        nongit = os.path.join(self.tmp, _scoped("nongit"))
        os.makedirs(nongit)
        self.assertEqual(teamsmod._validate_project_for_team(nongit), "not a git repository")


class CreateRemoveWorktreeRealGitTests(unittest.TestCase):
    """Real git AND a real (throwaway) tmux session via _run_run_user_
    command() -- the only sanctioned RUN_USER crossing -- but no DASHBOARD
    session/windows (that's Tier 3 below); TMUX patched to plain `tmux`."""

    def setUp(self):
        _patch_tmux(self, ["tmux"])
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-cwstate-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-cw-")
        self.repo = os.path.join(self.tmp, _scoped("proj"))
        _init_repo(self.repo)

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_create_then_remove_clean_branch_survives_removal(self):
        result = teamsmod._create_worktree(self.repo, "claude", "run1")
        self.assertTrue(result["ok"], result)
        path = result["path"]
        self.assertTrue(os.path.isdir(path))
        self.assertIn(path, _git(self.repo, "worktree", "list").stdout)
        outcome = teamsmod._remove_worktree(self.repo, path)
        self.assertEqual(outcome, "removed")
        self.assertFalse(os.path.isdir(path))
        branches = _git(self.repo, "branch", "--list").stdout
        self.assertIn("team-run1-claude", branches)  # branch survives, real git behavior

    def test_remove_dirty_worktree_left_fully_intact(self):
        result = teamsmod._create_worktree(self.repo, "claude", "run2")
        path = result["path"]
        with open(os.path.join(path, "scratch.txt"), "w") as f:
            f.write("dirty")
        outcome = teamsmod._remove_worktree(self.repo, path)
        self.assertEqual(outcome, "dirty")
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.isfile(os.path.join(path, "scratch.txt")))

    def test_create_on_existing_leftover_path_specific_message_no_git_dump(self):
        os.makedirs(teamsmod._worktree_path(self.repo, "claude"))
        result = teamsmod._create_worktree(self.repo, "claude", "run3")
        self.assertFalse(result["ok"])
        self.assertIn("claude", result["error"])
        self.assertIn(teamsmod._worktree_path(self.repo, "claude"), result["error"])
        self.assertIn("git worktree remove --force", result["error"])


class ListTeamBranchesRealGitTests(unittest.TestCase):
    """Real git, no tmux (backlog item 13, docs/spec.md acceptance
    criterion 1) -- list_team_branches() is a plain subprocess.run() call
    against the project's own checkout, same convention _validate_project_
    for_team() above already establishes; no _run_run_user_command()/tmux
    involvement at all."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-branches-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zero_matching_branches_returns_empty_list(self):
        repo = os.path.join(self.tmp, "clean")
        _init_repo(repo)
        self.assertEqual(teamsmod.list_team_branches(repo), [])

    def test_non_git_directory_returns_empty_list_not_an_exception(self):
        nongit = os.path.join(self.tmp, "nongit")
        os.makedirs(nongit)
        self.assertEqual(teamsmod.list_team_branches(nongit), [])

    def test_nonexistent_directory_returns_empty_list_not_an_exception(self):
        self.assertEqual(teamsmod.list_team_branches(os.path.join(self.tmp, "does-not-exist")), [])

    def test_multiple_branches_correct_metadata_and_run_id_agent_parsed(self):
        repo = os.path.join(self.tmp, "multi")
        _init_repo(repo)
        head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        run_id = "1700000000-abc123def456"  # shaped exactly like _run_id()'s own output
        _git(repo, "branch", f"team-{run_id}-claude", "HEAD")
        _git(repo, "branch", f"team-{run_id}-gpt", "HEAD")
        # A real second commit on one of the two, so committer_date/subject/
        # commit are independently verified per-branch, not just copy-pasted
        # off HEAD for both.
        _git(repo, "checkout", "-q", f"team-{run_id}-claude")
        with open(os.path.join(repo, "work.txt"), "w") as f:
            f.write("teammate work\n")
        _git(repo, "add", "work.txt")
        _git(repo, "commit", "-q", "-m", "teammate commit")
        claude_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "checkout", "-q", "master" if "master" in _git(repo, "branch").stdout
             else "main")

        result = teamsmod.list_team_branches(repo)
        by_branch = {b["branch"]: b for b in result}
        self.assertEqual(set(by_branch), {f"team-{run_id}-claude", f"team-{run_id}-gpt"})

        claude = by_branch[f"team-{run_id}-claude"]
        self.assertEqual(claude["run_id"], run_id)
        self.assertEqual(claude["agent"], "claude")
        self.assertEqual(claude["commit"], claude_sha)
        self.assertEqual(claude["subject"], "teammate commit")
        self.assertTrue(claude["committer_date"])

        gpt = by_branch[f"team-{run_id}-gpt"]
        self.assertEqual(gpt["run_id"], run_id)
        self.assertEqual(gpt["agent"], "gpt")
        self.assertEqual(gpt["commit"], head_sha)
        self.assertEqual(gpt["subject"], "init")

    def test_branch_not_matching_naming_convention_gets_none_run_id_and_agent(self):
        repo = os.path.join(self.tmp, "handmade")
        _init_repo(repo)
        _git(repo, "branch", "team-my-hand-made-branch", "HEAD")
        result = teamsmod.list_team_branches(repo)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["branch"], "team-my-hand-made-branch")
        self.assertIsNone(result[0]["run_id"])
        self.assertIsNone(result[0]["agent"])

    def test_survives_worktree_removal_same_as_create_remove_worktree_tests_above(self):
        # End-to-end against the real _create_worktree()/_remove_worktree()
        # pair above -- the exact scenario item 13 exists to surface: once
        # the worktree is gone, list_team_branches() is the only remaining
        # way to discover the branch.
        _patch_tmux(self, ["tmux"])
        state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-branches-state-")
        orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = state_dir
        try:
            repo = os.path.join(self.tmp, "survives")
            _init_repo(repo)
            created = teamsmod._create_worktree(repo, "claude", "survive-run")
            self.assertTrue(created["ok"], created)
            outcome = teamsmod._remove_worktree(repo, created["path"])
            self.assertEqual(outcome, "removed")
            result = teamsmod.list_team_branches(repo)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["branch"], "team-survive-run-claude")
        finally:
            teamsmod.TEAM_STATE_DIR = orig_state_dir
            shutil.rmtree(state_dir, ignore_errors=True)


class CliTeamBranchesTests(unittest.TestCase):
    """`team-branches <workdir>` CLI subcommand (backlog item 13, docs/
    spec.md acceptance criterion 2) -- same io.StringIO/redirect_stdout
    technique tests/test_teams_lead.py's test_cli_roster_subcommand_prints_
    json already establishes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-cli-branches-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prints_same_data_as_list_team_branches_as_json(self):
        repo = os.path.join(self.tmp, "proj")
        _init_repo(repo)
        _git(repo, "branch", "team-1700000000-abc123def456-claude", "HEAD")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = teamsmod.main(["team-branches", repo])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), teamsmod.list_team_branches(repo))

    def test_exits_0_when_list_is_empty(self):
        repo = os.path.join(self.tmp, "clean")
        _init_repo(repo)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = teamsmod.main(["team-branches", repo])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), [])


class RunRunUserCommandRealTmuxTests(unittest.TestCase):
    def setUp(self):
        _patch_tmux(self, ["tmux"])
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-rucstate-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir
        self._orig_grace = teamsmod._WORKTREE_OP_KILL_GRACE_SECONDS
        teamsmod._WORKTREE_OP_KILL_GRACE_SECONDS = 0.5
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-ruc-")

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        teamsmod._WORKTREE_OP_KILL_GRACE_SECONDS = self._orig_grace
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_success(self):
        result = teamsmod._run_run_user_command(["echo", "hello there"], cwd=self.tmp)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["stdout"].strip(), "hello there")

    def test_nonzero_exit_reports_stderr_as_error(self):
        result = teamsmod._run_run_user_command(["bash", "-c", "echo boom 1>&2; exit 3"], cwd=self.tmp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 3)
        self.assertIn("boom", result["stderr"])
        self.assertIn("boom", result["error"])

    def test_timeout_escalates_to_kill_session_and_reports_timed_out(self):
        result = teamsmod._run_run_user_command(
            ["bash", "-c", "trap '' TERM; sleep 30"], cwd=self.tmp, timeout=0.3)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["ok"])
        self.assertIsNone(result["rc"])

    def test_no_leftover_session_or_rundir(self):
        # Baseline BEFORE our own call, not a blind post-hoc scan -- op_id
        # (app/teams.py's own _run_run_user_command()) has no per-process
        # scope token, so a concurrent process's own, unrelated
        # switchboard-worktree-op-* session can legitimately be alive on
        # the shared tmux server at the same moment (docs/spec.md, BACKLOG
        # item 9). Asserting against a baseline -- only sessions that are
        # NEW since this test's own call -- isolates "did OUR OWN call leak
        # a session" from "does some unrelated session already exist for an
        # unrelated reason", without ever touching (let alone killing) that
        # other session.
        before = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                                capture_output=True, text=True)
        baseline = {n for n in before.stdout.splitlines() if n.startswith("switchboard-worktree-op-")}
        teamsmod._run_run_user_command(["echo", "hi"], cwd=self.tmp)
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        leftover = [n for n in r.stdout.splitlines()
                   if n.startswith("switchboard-worktree-op-") and n not in baseline]
        self.assertEqual(leftover, [])
        ops_root = os.path.join(self.state_dir, "_worktree_ops")
        if os.path.isdir(ops_root):
            self.assertEqual(os.listdir(ops_root), [])


# ─── Tier 3: real tmux, real git, real subprocess ─────────────────────────
class _RealTmuxTeamLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        _patch_tmux(self, ["tmux"])
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-state-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-tmp-")

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)
        _kill_leftover_team_sessions()


class LaunchTeamRealTmuxTests(_RealTmuxTeamLifecycleTestCase):
    def test_launch_creates_worktrees_and_session_windows(self):
        repo = os.path.join(self.tmp, _scoped("demo"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "do stuff", _lead(), ["claude", "codex"])
        self.assertTrue(result["ok"], result)
        run_id = result["run_id"]
        self.assertEqual(result["session"], teamsmod._team_session_name(_scoped("demo")))
        self.assertTrue(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("demo"))))
        self.assertEqual(_tmux_windows(teamsmod._team_session_name(_scoped("demo"))), ["lead", "claude", "codex"])
        for agent in ("claude", "codex"):
            path = teamsmod._worktree_path(repo, agent)
            self.assertTrue(os.path.isdir(path))
            self.assertIn(path, _git(repo, "worktree", "list").stdout)
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["round"], 0)
        self.assertEqual(set(state["worktrees"]), {"claude", "codex"})

    def test_dirty_tree_leaves_no_worktree_no_session_no_state_dir(self):
        repo = os.path.join(self.tmp, _scoped("dirty"))
        _init_repo(repo)
        with open(os.path.join(repo, "x.txt"), "w") as f:
            f.write("x")
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        self.assertFalse(result["ok"])
        self.assertIn("uncommitted", result["error"])
        self.assertFalse(os.path.exists(teamsmod._worktree_path(repo, "claude")))
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("dirty"))))
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))

    def test_detached_head_leaves_nothing_behind(self):
        src = os.path.join(self.tmp, "src")
        _init_repo(src)
        clone = os.path.join(self.tmp, _scoped("detached"))
        _git(self.tmp, "clone", "-q", src, clone)
        sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
        _git(clone, "checkout", "-q", sha)
        result = teamsmod.launch_team(clone, "task", _lead(), ["claude"])
        self.assertFalse(result["ok"])
        self.assertIn("detached", result["error"])
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("detached"))))

    def test_non_git_directory_leaves_nothing_behind(self):
        nongit = os.path.join(self.tmp, _scoped("nongit"))
        os.makedirs(nongit)
        result = teamsmod.launch_team(nongit, "task", _lead(), ["claude"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not a git repository")
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("nongit"))))

    def test_double_launch_refused_first_run_byte_for_byte_unaffected(self):
        repo = os.path.join(self.tmp, _scoped("demo2"))
        _init_repo(repo)
        first = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        self.assertTrue(first["ok"], first)
        before = teamsmod._load_state(first["run_id"])
        second = teamsmod.launch_team(repo, "again", _lead(), ["claude"])
        self.assertFalse(second["ok"])
        self.assertTrue(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("demo2"))))
        self.assertEqual(_tmux_windows(teamsmod._team_session_name(_scoped("demo2"))), ["lead", "claude"])
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "claude")))
        after = teamsmod._load_state(first["run_id"])
        self.assertEqual(before, after)

    def test_leftover_dirty_worktree_from_previous_run_refuses_new_launch_cleanly(self):
        repo = os.path.join(self.tmp, _scoped("demo3"))
        _init_repo(repo)
        first = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        self.assertTrue(first["ok"], first)
        wt_path = teamsmod._worktree_path(repo, "claude")
        with open(os.path.join(wt_path, "scratch.txt"), "w") as f:
            f.write("dirty")
        stop_result = teamsmod.stop_team(first["run_id"])
        self.assertEqual(stop_result["worktrees"]["claude"], "dirty")
        self.assertTrue(os.path.isdir(wt_path))

        second = teamsmod.launch_team(repo, "task2", _lead(), ["claude"])
        self.assertFalse(second["ok"])
        self.assertIn("claude", second["error"])
        self.assertIn(wt_path, second["error"])
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("demo3"))))

    def test_member_named_twice_degrades_cleanly_with_full_rollback(self):
        repo = os.path.join(self.tmp, _scoped("demo4"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude", "claude"])
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(teamsmod._worktree_path(repo, "claude")))
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("demo4"))))
        self.assertFalse(os.path.isdir(teamsmod._leads_root()))

    def test_project_path_containing_a_space(self):
        repo = os.path.join(self.tmp, _scoped("demo with space"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "claude")))
        self.assertTrue(teamsmod.tmux_has(result["session"]))


class _AddTeamMemberRealTmuxTestCase(_RealTmuxTeamLifecycleTestCase):
    """_RealTmuxTeamLifecycleTestCase's own real-git/real-tmux setUp,
    extended with a scratch ENGINES_DIR: unlike launch_team()'s own
    --members (plain strings, never checked against roster()),
    add_team_member() DOES validate the requested agent against roster()
    (backlog item 21 part 1, docs/spec.md 'Proposed approach' §1 step 3),
    so real .engine fixture files matching every agent name used below are
    required (the default ENGINES_DIR, /etc/ai-dev-switchboard/engines.d,
    does not exist on a dev/test host)."""

    def setUp(self):
        super().setUp()
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-addmember-engines-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        appmod.ENGINES_DIR = self.engines_dir
        for name in ("claude", "codex", "aider"):
            _write_engine_file(self.engines_dir, f"{name}.engine", f"""\
                LABEL={name.title()}
                CMD=unused
                HEADLESS_CMD={name} -p {{resume}}
                HEADLESS_FORMAT=plain
                HEADLESS_PROMPT=arg
                """)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        super().tearDown()


class AddTeamMemberRealTmuxTests(_AddTeamMemberRealTmuxTestCase):
    def test_add_member_creates_worktree_and_window_queues_event(self):
        repo = os.path.join(self.tmp, _scoped("demo"))
        _init_repo(repo)
        launch = teamsmod.launch_team(repo, "task", _lead(), ["codex"])
        self.assertTrue(launch["ok"], launch)
        run_id = launch["run_id"]
        session = teamsmod._team_session_name(_scoped("demo"))

        result = teamsmod.add_team_member(run_id, "aider")

        self.assertEqual(result, {"ok": True, "agent": "aider",
                                  "worktree": teamsmod._worktree_path(repo, "aider")})
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "aider")))
        self.assertIn(teamsmod._worktree_path(repo, "aider"),
                      _git(repo, "worktree", "list").stdout)
        self.assertEqual(sorted(_tmux_windows(session)), sorted(["lead", "codex", "aider"]))

        # Never touches run.json directly (mirrors interject()'s own
        # race-avoidance design, docs/spec.md "Proposed approach" §1).
        state = teamsmod._load_state(run_id)
        self.assertNotIn("aider", state["members"])
        self.assertNotIn("aider", state.get("worktrees", {}))

        with open(teamsmod._membership_log_path(run_id)) as f:
            envelope = json.loads(f.readline())
        self.assertEqual(envelope["agent"], "aider")
        self.assertEqual(envelope["kind"], "member_joined")
        self.assertEqual(envelope["worktree"], teamsmod._worktree_path(repo, "aider"))

    def test_add_member_while_blocked_ask_user_succeeds_immediately_and_drain_waits_for_resume(self):
        # docs/spec.md "Acceptance criteria": add_team_member() succeeds
        # (real worktree + window created immediately) while the run is
        # blocked_ask_user, and the queued member_joined event is only
        # drained once the run is later resumed -- not before. Closes the
        # test-coverage gap flagged in docs/test-review.md's BACKLOG item
        # 21 part 1 review (Finding 1): the existing
        # test_blocked_ask_user_and_blocked_board_write_do_not_hit_the_
        # status_check (tests/test_teams_lead.py) only proves the status
        # *gate* accepts this status (via a deliberately-unknown engine
        # name that fails at a later check); it never reaches the real
        # worktree/window/drain path this test exercises.
        repo = os.path.join(self.tmp, _scoped("demo-blocked"))
        _init_repo(repo)
        launch = teamsmod.launch_team(repo, "task", _lead(), ["codex"])
        self.assertTrue(launch["ok"], launch)
        run_id = launch["run_id"]
        session = teamsmod._team_session_name(_scoped("demo-blocked"))

        state = teamsmod._load_state(run_id)
        state["status"] = "blocked_ask_user"
        teamsmod._persist(state)

        result = teamsmod.add_team_member(run_id, "aider")

        self.assertEqual(result, {"ok": True, "agent": "aider",
                                  "worktree": teamsmod._worktree_path(repo, "aider")})
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "aider")))
        self.assertIn(teamsmod._worktree_path(repo, "aider"),
                      _git(repo, "worktree", "list").stdout)
        self.assertEqual(sorted(_tmux_windows(session)), sorted(["lead", "codex", "aider"]))

        # Queued but NOT yet drained -- state["members"] still reflects the
        # pre-add roster while the run remains blocked.
        blocked_state = teamsmod._load_state(run_id)
        self.assertEqual(blocked_state["status"], "blocked_ask_user")
        self.assertNotIn("aider", blocked_state["members"])
        self.assertNotIn("aider", blocked_state.get("worktrees", {}))
        with open(teamsmod._membership_log_path(run_id)) as f:
            envelope = json.loads(f.readline())
        self.assertEqual(envelope["agent"], "aider")
        self.assertEqual(envelope["kind"], "member_joined")

        # Resume: status flips back to running, and the NEXT team_step()
        # call is the one that actually drains the queued event -- same
        # "only drains at the next round boundary" behavior the running-
        # status test above (test_drain_at_next_round_boundary_makes_
        # agent_delegate_eligible) already proves for the unblocked case.
        blocked_state["status"] = "running"

        def fail_call_lead(*a, **kw):
            self.fail("_call_lead() was called on a round that only drained membership")

        orig = teamsmod._call_lead
        teamsmod._call_lead = fail_call_lead
        try:
            teamsmod.team_step(blocked_state)
        finally:
            teamsmod._call_lead = orig
        self.assertIn("aider", blocked_state["members"])
        self.assertEqual(blocked_state["worktrees"]["aider"],
                         teamsmod._worktree_path(repo, "aider"))

    def test_drain_at_next_round_boundary_makes_agent_delegate_eligible(self):
        repo = os.path.join(self.tmp, _scoped("demo2"))
        _init_repo(repo)
        launch = teamsmod.launch_team(repo, "task", _lead(), ["codex"])
        run_id = launch["run_id"]
        teamsmod.add_team_member(run_id, "aider")
        state = teamsmod._load_state(run_id)

        def fail_call_lead(*a, **kw):
            self.fail("_call_lead() was called on a round that only drained membership")

        orig = teamsmod._call_lead
        teamsmod._call_lead = fail_call_lead
        try:
            teamsmod.team_step(state)  # round 1: drains only
        finally:
            teamsmod._call_lead = orig
        self.assertIn("aider", state["members"])

        # docs/spec.md "Acceptance criteria": _lead_tools()/_validate_lead_
        # action() both now accept agent="aider" with NO code change in
        # either function -- both already rebuild from state["members"]
        # fresh every call.
        tools = teamsmod._lead_tools(state["members"])
        delegate_enum = next(t["function"]["parameters"]["properties"]["agent"]["enum"]
                             for t in tools if t["function"]["name"] == "delegate")
        self.assertIn("aider", delegate_enum)
        result = teamsmod._validate_lead_action(
            {"tool": "delegate", "args": {"agent": "aider", "task": "x"}},
            state["members"], state["action_count"])
        self.assertTrue(result["ok"], result)

    def test_tmux_session_gone_rolls_back_worktree(self):
        repo = os.path.join(self.tmp, _scoped("demo3"))
        _init_repo(repo)
        launch = teamsmod.launch_team(repo, "task", _lead(), ["codex"])
        run_id = launch["run_id"]
        session = teamsmod._team_session_name(_scoped("demo3"))
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)

        result = teamsmod.add_team_member(run_id, "aider")

        self.assertFalse(result["ok"])
        self.assertIn("no longer running", result["error"])
        self.assertFalse(os.path.exists(teamsmod._worktree_path(repo, "aider")))
        self.assertFalse(os.path.exists(teamsmod._membership_log_path(run_id)))


class CliTeamAddMemberSubprocessTests(unittest.TestCase):
    """team-launch then team-add-member through the REAL CLI as a separate
    subprocess, same technique CliTeamLifecycleSubprocessTests above already
    establishes (no TMUX monkeypatch -- a fresh subprocess re-imports app.py,
    so an in-process patch wouldn't apply anyway; relies on the same real
    `sudo -u $RUN_USER tmux` path that class's own docstring already proved
    works in this environment). Verifies docs/spec.md 'Acceptance criteria':
    team-add-member produces the same effect as the HTTP route, without any
    web server running."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-addmember-clistate-")
        self.tmp = tempfile.mkdtemp(prefix="switchboard-addmember-cli-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-addmember-cliprojects-")
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-addmember-cliengines-")
        for name in ("codex", "aider"):
            _write_engine_file(self.engines_dir, f"{name}.engine", f"""\
                LABEL={name.title()}
                CMD=unused
                HEADLESS_CMD={name} -p {{resume}}
                HEADLESS_FORMAT=plain
                HEADLESS_PROMPT=arg
                """)
        self.env = dict(os.environ)
        self.env["TEAM_STATE_DIR"] = self.state_dir
        self.env["PROJECTS_DIR"] = self.projects_dir
        self.env["ENGINES_DIR"] = self.engines_dir
        self.env["TEAM_LLM_BASE_URL"] = "http://127.0.0.1:1/v1"  # never actually called
        self.env["TEAM_LLM_MODEL"] = "stub-model"

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        _kill_leftover_team_sessions()

    def _run_cli(self, *args):
        cmd = [sys.executable, os.path.join(APP_DIR, "teams.py")] + list(args)
        return subprocess.run(cmd, cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=60)

    def test_cli_add_member_creates_worktree_and_window(self):
        repo = os.path.join(self.tmp, _scoped("clidemo2"))
        _init_repo(repo)
        r1 = self._run_cli("team-launch", repo, "--task", "do it", "--lead", "codex",
                           "--members", "")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        run_id = json.loads(r1.stdout)["run_id"]

        r2 = self._run_cli("team-add-member", run_id, "aider")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("added 'aider'", r2.stdout)
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "aider")))
        session = teamsmod._team_session_name(_scoped("clidemo2"))
        self.assertIn("aider", _tmux_windows(session))

        self._run_cli("team-stop", run_id)

    def test_cli_add_member_unknown_run_id_nonzero_exit(self):
        r = self._run_cli("team-add-member", "no-such-run", "aider")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such run_id", r.stderr)


class StopTeamRealTmuxTests(_RealTmuxTeamLifecycleTestCase):
    def test_stop_running_run_kills_session_dirty_left_clean_removed(self):
        repo = os.path.join(self.tmp, _scoped("demo"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude", "codex"])
        run_id = result["run_id"]
        dirty_path = teamsmod._worktree_path(repo, "claude")
        with open(os.path.join(dirty_path, "scratch.txt"), "w") as f:
            f.write("dirty")

        stop_result = teamsmod.stop_team(run_id)
        self.assertTrue(stop_result["session_removed"])
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("demo"))))
        self.assertEqual(stop_result["worktrees"]["claude"], "dirty")
        self.assertEqual(stop_result["worktrees"]["codex"], "removed")
        self.assertTrue(os.path.isdir(dirty_path))
        self.assertFalse(os.path.isdir(teamsmod._worktree_path(repo, "codex")))
        branches = _git(repo, "branch", "--list").stdout
        self.assertIn(f"team-{run_id}-claude", branches)
        self.assertIn(f"team-{run_id}-codex", branches)

    def test_stop_on_already_finished_run_still_unconditional(self):
        repo = os.path.join(self.tmp, _scoped("demo2"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        run_id = result["run_id"]
        state = teamsmod._load_state(run_id)
        state["status"] = "finished"
        teamsmod._persist(state)

        stop_result = teamsmod.stop_team(run_id)
        self.assertTrue(stop_result["session_removed"])
        self.assertFalse(teamsmod.tmux_has(teamsmod._team_session_name(_scoped("demo2"))))
        self.assertEqual(stop_result["worktrees"]["claude"], "removed")
        state2 = teamsmod._load_state(run_id)
        self.assertEqual(state2["status"], "finished")  # not overwritten by an unconditional stop

    def test_stop_unknown_run_id_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            teamsmod.stop_team("no-such-run-id")


class TeamReapRealTmuxTests(_RealTmuxTeamLifecycleTestCase):
    def test_crash_then_reap_is_a_two_pass_sequence(self):
        repo = os.path.join(self.tmp, _scoped("demo"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        run_id, session = result["run_id"], result["session"]
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)  # simulated crash
        self.assertFalse(teamsmod.tmux_has(session))

        results1 = teamsmod.sweep_dead_teams()
        self.assertEqual(len(results1), 1)
        self.assertEqual(results1[0]["action"], "marked_error")
        state = teamsmod._load_state(run_id)
        self.assertEqual(state["status"], "error")
        self.assertIn(session, state["error"])
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "claude")))  # not yet swept

        orig_ttl = teamsmod.TEAM_SESSION_STALE_TTL_SECONDS
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 0
        try:
            results2 = teamsmod.sweep_dead_teams()
        finally:
            teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = orig_ttl
        self.assertEqual(results2[0]["action"], "swept")
        self.assertFalse(os.path.isdir(teamsmod._worktree_path(repo, "claude")))
        self.assertIn(f"team-{run_id}-claude", _git(repo, "branch", "--list").stdout)

    def test_blocked_ask_user_never_swept_regardless_of_ttl(self):
        repo = os.path.join(self.tmp, _scoped("demo2"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        run_id, session = result["run_id"], result["session"]
        state = teamsmod._load_state(run_id)
        state["status"] = "blocked_ask_user"
        teamsmod._persist(state)

        orig_ttl = teamsmod.TEAM_SESSION_STALE_TTL_SECONDS
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 0
        try:
            results = teamsmod.sweep_dead_teams()
        finally:
            teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = orig_ttl
        self.assertEqual(results, [])
        self.assertTrue(teamsmod.tmux_has(session))
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "claude")))


class SessionOwnershipCollisionRealTmuxTests(_RealTmuxTeamLifecycleTestCase):
    """
    Regression tests for a real defect found by exercising a crash-then-
    relaunch-then-reap sequence for real while building this feature (not a
    hypothetical, and not in the spec's own enumerated cases): a
    team-<project> tmux session NAME (_team_session_name()) and a
    .teams/<agent> worktree PATH (_worktree_path()) are BOTH pure functions
    of project_name/agent, not of any one run_id. Once an OLD run's own
    session/worktree is genuinely gone (crashed, or cleanly reclaimed by a
    prior sweep/stop), a completely different, LATER run for the SAME
    project can legitimately create a new session/worktree under the
    IDENTICAL name/path. Without the ownership check this feature's
    _kill_team_session_if_owned() adds (a tmux user option stamped with the
    owning run_id at session-creation time) and without dropping a
    "removed"/"absent" worktree entry from a swept run's own persisted
    state, a stale sweep_dead_teams()/stop_team() pass re-processing the OLD
    (already-terminal) run's record would destroy the NEW run's live
    session/worktree, believing it was reclaiming its own leftover
    resource.
    """

    def test_stale_terminal_run_never_kills_a_newer_runs_live_session_via_sweep(self):
        repo = os.path.join(self.tmp, _scoped("demo3"))
        _init_repo(repo)
        first = teamsmod.launch_team(repo, "task1", _lead(), ["claude"])
        run_id1, session = first["run_id"], first["session"]
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)

        orig_ttl = teamsmod.TEAM_SESSION_STALE_TTL_SECONDS
        teamsmod.sweep_dead_teams()  # pass 1: marks run_id1 status=error
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 0
        teamsmod.sweep_dead_teams()  # pass 2: sweeps run_id1's own worktree/session record
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = orig_ttl
        self.assertFalse(os.path.isdir(teamsmod._worktree_path(repo, "claude")))

        second = teamsmod.launch_team(repo, "task2", _lead(), ["claude"])
        self.assertTrue(second["ok"], second)
        run_id2 = second["run_id"]
        self.assertTrue(teamsmod.tmux_has(session))  # a NEW, live team-demo3 session
        state2 = teamsmod._load_state(run_id2)
        state2["status"] = "blocked_ask_user"
        teamsmod._persist(state2)

        # Re-process run_id1 again (still terminal, TTL forced to 0) -- must
        # NOT touch run_id2's live session or worktree.
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 0
        try:
            teamsmod.sweep_dead_teams()
        finally:
            teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = orig_ttl
        self.assertTrue(teamsmod.tmux_has(session))
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "claude")))
        self.assertEqual(teamsmod._load_state(run_id2)["status"], "blocked_ask_user")

    def test_stale_terminal_run_never_kills_a_newer_runs_live_session_via_explicit_stop(self):
        repo = os.path.join(self.tmp, _scoped("demo4"))
        _init_repo(repo)
        first = teamsmod.launch_team(repo, "task1", _lead(), ["claude"])
        run_id1, session = first["run_id"], first["session"]
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
        teamsmod.sweep_dead_teams()  # marks run_id1 status=error
        orig_ttl = teamsmod.TEAM_SESSION_STALE_TTL_SECONDS
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = 0
        teamsmod.sweep_dead_teams()  # sweeps run_id1's own record
        teamsmod.TEAM_SESSION_STALE_TTL_SECONDS = orig_ttl

        second = teamsmod.launch_team(repo, "task2", _lead(), ["claude"])
        self.assertTrue(second["ok"], second)
        self.assertTrue(teamsmod.tmux_has(session))

        # An explicit team-stop on the OLD run_id1 must not touch the NEW
        # run's live session or worktree.
        teamsmod.stop_team(run_id1)
        self.assertTrue(teamsmod.tmux_has(session))
        self.assertTrue(os.path.isdir(teamsmod._worktree_path(repo, "claude")))


class CreateTeamSessionAtomicStampTests(_RealTmuxTeamLifecycleTestCase):
    """
    Regression test for a real defect found in review (reviewer-verified,
    independently confirmed here): _create_team_session() used to create
    the session, set remain-on-exit, and stamp its ownership option
    (_TEAM_SESSION_RUN_ID_OPTION) as THREE separate tmux client invocations.
    Between the first and third, the session existed on the server but was
    NOT yet stamped -- and _kill_team_session_if_owned() treats an unstamped
    session as safe to kill for any run_id. There is no locking anywhere in
    this module, and sweep_dead_teams() runs opportunistically inside every
    launch_team()/stop_team() call for ANY project (not just the one being
    launched), so this window was reachable without any concurrency on the
    SAME project at all -- a solo crash of the creating process between the
    `new-session` reply and the stamp was enough, and depending on timing
    launch_team() could return a false {"ok": True} for an already-dead
    session with no member windows.

    Fixed by making session creation, remain-on-exit, and the ownership
    stamp ONE atomic tmux client invocation (`;`-chained), so no unstamped
    session is ever observable by a concurrent client. This test proves
    that property directly -- a tight concurrent poller watching for
    "session exists but has no stamp" during _create_team_session()'s own
    run -- rather than merely checking the stamp is present after the call
    returns (which the OLD, racy code also satisfied, just not atomically).
    Independently verified against a standalone reproduction of the OLD
    three-call shape before trusting this methodology: 10/10 trials observed
    the unstamped window there, 0/10 against the fixed one-call shape.
    """

    def test_session_is_never_observable_unstamped_during_creation(self):
        project_name = _scoped("atomicdemo")
        run_id = "run-atomic-1"
        session = teamsmod._team_session_name(project_name)

        observed_unstamped = threading.Event()
        stop_polling = threading.Event()

        def _poll():
            while not stop_polling.is_set():
                if teamsmod.tmux_has(session) and not teamsmod._team_session_run_id(session):
                    observed_unstamped.set()
                    return

        poller = threading.Thread(target=_poll)
        poller.start()
        try:
            result = teamsmod._create_team_session(project_name, run_id, [])
        finally:
            stop_polling.set()
            poller.join(timeout=5)

        self.assertTrue(result["ok"], result)
        self.assertEqual(teamsmod._team_session_run_id(session), run_id)
        self.assertFalse(observed_unstamped.is_set(),
                         "session was observable without its ownership stamp during creation")


class CreateTeamSessionFailingLinkCleanupTests(_RealTmuxTeamLifecycleTestCase):
    """
    Regression test for a should-fix the reviewer logged against
    CreateTeamSessionAtomicStampTests' own fix: tmux's `;`-chain is atomic
    against a concurrent OBSERVER (nothing else can see a half-stamped
    session), but NOT against a failure partway through the chain itself --
    confirmed live: if `new-session` succeeds but a LATER link fails (in
    practice unreachable with the real, hardcoded `remain-on-exit`/run_id
    values, since neither can fail for any well-formed run_id -- reproduced
    here for REAL by monkeypatching _TEAM_SESSION_RUN_ID_OPTION to a
    non-"@"-prefixed name, which makes the real tmux `set-option` call
    itself genuinely fail with "invalid option", exercising the actual
    failure path rather than a mock), tmux does NOT roll back the already-
    successful `new-session` -- the session used to survive, alive and
    unstamped, permanently: every future launch_team() for the project was
    locked out by the upfront tmux_has() precondition, sweep_dead_teams()
    could never find it (launch_team() never persists a run.json on this
    failure path), and recovery was manual `tmux kill-session` only. Same
    shape as 6a's own first defect (a fallible call sitting outside its own
    cleanup, leaking a resource) -- fixed the same way, not a narrower
    patch: the failure path now kills whatever it finds at that exact
    session name, since it can only be the session this call itself just
    created (see _create_team_session()'s own docstring for why that's
    true structurally, not just usually).
    """

    def test_session_is_cleaned_up_when_a_later_chain_link_fails(self):
        project_name = _scoped("failchain")
        run_id = "run-failchain-1"
        session = teamsmod._team_session_name(project_name)
        orig_option = teamsmod._TEAM_SESSION_RUN_ID_OPTION
        teamsmod._TEAM_SESSION_RUN_ID_OPTION = "not-a-real-tmux-option"
        try:
            result = teamsmod._create_team_session(project_name, run_id, [])
        finally:
            teamsmod._TEAM_SESSION_RUN_ID_OPTION = orig_option
        self.assertFalse(result["ok"], result)
        self.assertFalse(teamsmod.tmux_has(session),
                         "a partially-created, unstamped session survived a failing chain link")

    def test_a_later_launch_for_the_same_project_is_not_locked_out_after_the_failure(self):
        # The lockout consequence, proven directly: launch_team()'s own
        # tmux_has() precondition must not still see a leftover session
        # from the earlier failed _create_team_session() call.
        project_name = _scoped("failchain2")
        run_id = "run-failchain2-1"
        orig_option = teamsmod._TEAM_SESSION_RUN_ID_OPTION
        teamsmod._TEAM_SESSION_RUN_ID_OPTION = "not-a-real-tmux-option"
        try:
            first = teamsmod._create_team_session(project_name, run_id, [])
        finally:
            teamsmod._TEAM_SESSION_RUN_ID_OPTION = orig_option
        self.assertFalse(first["ok"], first)

        second = teamsmod._create_team_session(project_name, "run-failchain2-2", [])
        self.assertTrue(second["ok"], second)
        self.assertTrue(teamsmod.tmux_has(second["session"]))


class SessionCreationRaceRealTmuxTests(_RealTmuxTeamLifecycleTestCase):
    """
    Regression test for a real, severe defect found while verifying backlog
    item 6d part 2a's own two-concurrent-starts acceptance criterion (not
    hypothetical, not in any spec's own enumerated cases -- found by running
    that criterion's own real-concurrency test many times in a row and
    noticing a rare, third failure shape): _create_team_session()'s own
    failure-cleanup branch used to kill whatever session it found at
    `session` unconditionally (a RAW `kill-session`, not ownership-checked).

    Two real, Barrier-synchronized callers can both pass their own upfront
    `tmux_has(session)` check as False (neither has created anything yet),
    then race to `new-session -s <same name>`. tmux itself only lets ONE
    such call actually create the session server-side -- the LOSER's own
    `new-session` genuinely fails (a real "duplicate session" error, tmux
    never rolls back or half-creates anything for the loser). But the
    loser's OLD cleanup code read this as "a session exists at this name,
    and my own upfront check said nothing existed when I started, so
    whatever's there now must be MY OWN leftover" -- an unconditional
    kill-session then destroyed the WINNER's real, live, already-stamped
    session. Reproduced directly, standalone, before trusting this as a
    regression test: 20/20 trials destroyed the winner's session against
    the old (raw-kill) code, 0/30 against the fix (see docs/implementation.md
    for the full reproduction script). Fixed by routing the same cleanup
    through _kill_team_session_if_owned(session, run_id) instead of a raw
    kill-session -- see _create_team_session()'s own docstring for the full
    ownership reasoning (ONLY correct because a winner's session, once
    observable at all, is ALWAYS already fully stamped with its own run_id,
    per this same file's own atomic-creation guarantee).
    """

    def test_barrier_synchronized_concurrent_create_never_kills_the_winners_session(self):
        project_name = _scoped("sessionrace")
        session = teamsmod._team_session_name(project_name)
        TRIALS = 15
        for i in range(TRIALS):
            subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
            results = {}
            barrier = threading.Barrier(2)

            def _create(run_id, key):
                barrier.wait()
                results[key] = teamsmod._create_team_session(project_name, run_id, [])

            run_id_a, run_id_b = f"run-a-{i}", f"run-b-{i}"
            t1 = threading.Thread(target=_create, args=(run_id_a, "a"))
            t2 = threading.Thread(target=_create, args=(run_id_b, "b"))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            oks = {k: v for k, v in results.items() if v["ok"]}
            fails = {k: v for k, v in results.items() if not v["ok"]}
            self.assertEqual(len(oks), 1, f"trial {i}: expected exactly one winner, got {results!r}")
            self.assertEqual(len(fails), 1, f"trial {i}: expected exactly one loser, got {results!r}")
            winner_key = next(iter(oks))
            winner_run_id = run_id_a if winner_key == "a" else run_id_b

            self.assertTrue(teamsmod.tmux_has(session),
                            f"trial {i}: the WINNER's session was destroyed by the loser's own cleanup")
            self.assertEqual(teamsmod._team_session_run_id(session), winner_run_id,
                             f"trial {i}: session survived but is stamped with the wrong run_id")
            # Two legitimate shapes for the loser's own error, depending on
            # exactly how the two threads interleave: it either loses at
            # its own upfront tmux_has() pre-check (if the winner's session
            # already exists by the time the loser's check runs) or loses
            # at the real tmux "duplicate session" new-session race (if
            # both threads pass the pre-check before either has created
            # anything) -- both are real, both are covered by this same
            # fix, and under real system load (e.g. this file's own full
            # suite run) either can fire; asserting only one specific
            # shape is exactly the kind of over-narrow assertion that let
            # the original defect this test guards go unnoticed elsewhere
            # in this cycle (see tests/test_team_routes.py's own
            # near-identical lesson for the launch_team()-level version of
            # this).
            loser_error = next(iter(fails.values()))["error"]
            self.assertTrue(
                "failed to create team session" in loser_error or "already running" in loser_error,
                loser_error)
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)


class LaunchTeamWorldReadableUnderStrictUmaskTests(_RealTmuxTeamLifecycleTestCase):
    def test_every_dashboard_input_file_world_readable_under_strict_umask(self):
        # docs/spec.md acceptance criteria: reuses 6a's own precedent test
        # technique (test_run_sh_and_prompt_file_are_world_readable_under_a_
        # strict_umask) -- a strict SVC_USER umask must not prevent RUN_
        # USER's tmux dashboard panes from reading these files.
        repo = os.path.join(self.tmp, _scoped("proj"))
        _init_repo(repo)
        old_umask = os.umask(0o077)
        self.addCleanup(lambda: os.umask(old_umask))
        result = teamsmod.launch_team(repo, "task", _lead(), ["claude"])
        self.assertTrue(result["ok"], result)
        run_id = result["run_id"]
        d = teamsmod._run_dir(run_id)
        agents_dir = os.path.join(d, "agents")
        transcript_path = teamsmod._transcript_path(run_id)
        claude_log = teamsmod._agent_log_path(run_id, "claude")
        for path in (d, agents_dir, transcript_path, claude_log):
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertTrue(mode & stat.S_IROTH, f"{path} not world-readable: {oct(mode)}")
            if os.path.isdir(path):
                self.assertTrue(mode & stat.S_IXOTH, f"{path} not world-traversable: {oct(mode)}")


class TeamRunDelegateWorktreeAndDashboardTests(_RealTmuxTeamLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-driveengines-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        appmod.ENGINES_DIR = self.engines_dir

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        super().tearDown()

    def _capture_with_retry(self, session, window, needles):
        deadline = time.time() + 10
        pane = ""
        while time.time() < deadline:
            pane = _tmux_capture(session, window)
            if all(n in pane for n in needles):
                break
            time.sleep(0.2)
        return pane

    def test_two_delegations_same_teammate_worktree_isolated_session_continuous_log_accumulates(self):
        script = _write_script(self.tmp, "fake_teammate.py", """\
            #!/usr/bin/env python3
            import glob, json
            n = len(glob.glob("call-*.marker")) + 1
            with open(f"call-{n}.marker", "w") as f:
                f.write("here\\n")
            print(json.dumps({"type": "system", "subtype": "init",
                              "session_id": "sess-continuity-fixed"}))
            print(json.dumps({"type": "result", "subtype": "success",
                              "result": f"call {n} done",
                              "session_id": "sess-continuity-fixed"}))
            """)
        _write_engine_file(self.engines_dir, "faketm.engine", f"""\
            LABEL=FakeTeammate
            CMD=unused
            HEADLESS_CMD=python3 {script} {{resume}}
            HEADLESS_FORMAT=claude-stream-json
            HEADLESS_PROMPT=arg
            HEADLESS_RESUME=--resume {{session_id}}
            """)
        repo = os.path.join(self.tmp, _scoped("proj"))
        _init_repo(repo)
        result = teamsmod.launch_team(repo, "do the thing", _lead(), ["faketm"])
        self.assertTrue(result["ok"], result)
        run_id, session = result["run_id"], result["session"]
        state = teamsmod._load_state(run_id)

        calls = {"n": 0}

        def fake_call_lead(state, system, round_context, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                return ({"tool": "delegate", "args": {"agent": "faketm", "task": f"task {calls['n']}"}},
                       None, "...")
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig_call_lead = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_run(state)
        finally:
            teamsmod._call_lead = orig_call_lead

        self.assertEqual(state["status"], "finished")
        self.assertEqual(state["teammate_sessions"]["faketm"], "sess-continuity-fixed")

        wt_path = teamsmod._worktree_path(repo, "faketm")
        self.assertTrue(os.path.isfile(os.path.join(wt_path, "call-1.marker")))
        self.assertTrue(os.path.isfile(os.path.join(wt_path, "call-2.marker")))
        self.assertFalse(os.path.isfile(os.path.join(repo, "call-1.marker")))
        self.assertFalse(os.path.isfile(os.path.join(repo, "call-2.marker")))

        # Dashboard window shows BOTH calls' accumulated output, not just
        # the latest -- the whole point of a stable, append-mode log path.
        pane = self._capture_with_retry(session, "faketm", ["call 1 done", "call 2 done"])
        self.assertIn("call 1 done", pane)
        self.assertIn("call 2 done", pane)

        # The lead window shows transcript.jsonl's content live, matching
        # what _drive_and_report()'s own stderr tail shows for the same run.
        with open(teamsmod._transcript_path(run_id)) as f:
            transcript_lines = [json.loads(line) for line in f if line.strip()]
        needles = [e["text"][:30] for e in transcript_lines if e["text"]]
        lead_pane = self._capture_with_retry(session, "lead", needles[-1:])
        for needle in needles:
            self.assertIn(needle, lead_pane)


class EngineNameReservationRealTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-reservedengines-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        appmod.ENGINES_DIR = self.engines_dir

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)

    def test_team_prefixed_engine_files_are_silently_ignored(self):
        _write_engine_file(self.engines_dir, "team.engine", "LABEL=Team\nCMD=team-tool {name}\n")
        _write_engine_file(self.engines_dir, "team-anything.engine",
                           "LABEL=TeamAnything\nCMD=team-tool2 {name}\n")
        _write_engine_file(self.engines_dir, "teamster.engine", "LABEL=Teamster\nCMD=teamster {name}\n")
        engines = appmod.load_engines()
        self.assertNotIn("team", engines)
        self.assertNotIn("team-anything", engines)
        self.assertNotIn("teamster", engines)

    def test_switchboard_prefix_still_reserved_unchanged(self):
        _write_engine_file(self.engines_dir, "switchboard.engine", "LABEL=SB\nCMD=x {name}\n")
        self.assertNotIn("switchboard", appmod.load_engines())

    def test_unrelated_engine_name_unaffected(self):
        _write_engine_file(self.engines_dir, "claude.engine", "LABEL=Claude\nCMD=claude {name}\n")
        self.assertIn("claude", appmod.load_engines())


class CliTeamLifecycleSubprocessTests(unittest.TestCase):
    """team-launch/team-stop/team-reap through the REAL CLI as a separate
    subprocess, same technique tests/test_teams_lead.py's own
    ResolveInSeparateProcessTests already established -- no TMUX monkeypatch
    (a fresh subprocess re-imports app.py, so an in-process patch wouldn't
    apply anyway); relies on the same real `sudo -u $RUN_USER tmux` path
    already proven to work in this environment by that existing, passing
    test class."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-clistate-")
        self.tmp = tempfile.mkdtemp(prefix="switchboard-lifecycle-cli-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lifecycle-cliprojects-")
        self.env = dict(os.environ)
        self.env["TEAM_STATE_DIR"] = self.state_dir
        self.env["PROJECTS_DIR"] = self.projects_dir
        self.env["TEAM_LLM_BASE_URL"] = "http://127.0.0.1:1/v1"  # never actually called
        self.env["TEAM_LLM_MODEL"] = "stub-model"

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        _kill_leftover_team_sessions()

    def _run_cli(self, *args):
        cmd = [sys.executable, os.path.join(APP_DIR, "teams.py")] + list(args)
        return subprocess.run(cmd, cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=60)

    def test_team_launch_then_team_stop_then_team_reap_via_real_cli(self):
        repo = os.path.join(self.tmp, _scoped("clidemo"))
        _init_repo(repo)
        r1 = self._run_cli("team-launch", repo, "--task", "do it", "--lead-ollama", "--members", "")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        launch_result = json.loads(r1.stdout)
        run_id = launch_result["run_id"]
        self.assertEqual(launch_result["session"], teamsmod._team_session_name(_scoped("clidemo")))
        self.assertEqual(launch_result["worktrees"], {})

        r2 = self._run_cli("team-stop", run_id)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        stop_result = json.loads(r2.stdout)
        self.assertTrue(stop_result["session_removed"])

        r3 = self._run_cli("team-reap")
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIsInstance(json.loads(r3.stdout), list)

    def test_team_stop_unknown_run_id_cli_error_message(self):
        r = self._run_cli("team-stop", "no-such-run-id")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such run_id", r.stderr)


if __name__ == "__main__":
    unittest.main()
