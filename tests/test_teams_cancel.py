"""
Tests for cooperative cancellation (backlog item 6d, part 2a --
docs/spec.md "Proposed approach" §7). Mirrors tests/test_teams_lifecycle.py's
own tiered pure/real-tmux split (per docs/spec.md's own "Test plan"):

Tier 1 -- pure unit, no subprocess, no tmux: team_step()'s two new
checkpoints (right after _call_lead() returns; right after the delegate
branch's own agent_run() call returns) against a fake _call_lead()/agent_run()
that return immediately, asserting the exact status/history-entry shape each
checkpoint produces when cancel_event is already set going in; team_run()'s
own top-of-loop checkpoint; _call_lead()'s tier-1 branch accepting but
ignoring cancel_event (no subprocess to signal there).

Tier 2/3 -- real tmux, real subprocess (the bulk of the risk surface, per
this story's own established pattern -- every prior defect in this exact
area was found by exercising real tmux past the spec's enumerated cases):
agent_run(..., cancel_event=...) against a real, deliberately slow stand-in
engine, asserting the real subprocess is actually gone (no leftover
switchboard-headless-* tmux session) within the bounded escalation window,
and cancel_reason == "stopped" in the returned result -- both the TERM-
succeeds and TERM-ignored-escalates-to-KILL cases; team_step()/team_run()'s
own checkpoints exercised end to end via a real controllable stand-in
(lead call and delegate call, respectively), cancel_event.set() triggered
from a SECOND thread mid-call -- the specific "stop arriving mid-delegation"
concurrency case this story's own risk assessment names explicitly.

Run with:
    python3 -m unittest discover -s tests -v
"""
import os
import shutil
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

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-cancel-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
import teams as teamsmod  # noqa: E402


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


def _write_engine_file(dirpath, filename, body):
    with open(os.path.join(dirpath, filename), "w") as f:
        f.write(textwrap.dedent(body))


def _write_script(dirpath, filename, body):
    import stat
    path = os.path.join(dirpath, filename)
    with open(path, "w") as f:
        f.write(textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def _lead(tier=1, kind="ollama", name="stub"):
    return {"kind": kind, "name": name, "tier": tier}


def _dummy_agent_result(**overrides):
    base = {"ok": True, "text": "", "session_id": None, "exit_code": 0, "cancelled": False,
           "cancel_reason": None, "event_count": 0, "truncated": False, "log_path": None,
           "stderr_tail": "", "error": None}
    base.update(overrides)
    return base


def _no_leftover_headless_sessions(testcase):
    r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
    leftover = [n for n in r.stdout.splitlines() if n.startswith(_SESSION_PREFIX)]
    testcase.assertEqual(leftover, [], f"leftover tmux sessions: {leftover}")


# ─── Tier 1: team_step()'s checkpoint 1 (right after _call_lead() returns) ──
class TeamStepCheckpointOneTests(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-cancel-proj-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-cancel-state-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.projdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_cancel_set_before_call_lead_returns_yields_stopped_before_any_other_branch(self):
        # A malformed/failed lead response would ordinarily consume the
        # malformed-retry budget or hit the business-rule path -- checkpoint
        # 1 must win regardless, since it's checked FIRST, ahead of every
        # existing branch (docs/spec.md §7).
        state = teamsmod._new_state("run-ckpt1", self.projdir, _lead(), [], "task")
        cancel_event = threading.Event()
        cancel_event.set()

        def fake_call_lead(state, system, round_context, **kwargs):
            self.assertIs(kwargs.get("cancel_event"), cancel_event)
            return None, None, "garbage, not json"  # would otherwise be "malformed"

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state, cancel_event=cancel_event)
        finally:
            teamsmod._call_lead = orig

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["malformed_retries"], 0)  # never touched -- checkpoint 1 returned first
        self.assertEqual(len(state["history"]), 1)
        entry = state["history"][-1]
        self.assertIsNone(entry["tool"])
        self.assertEqual(entry["outcome_summary"],
                         "stopped by request before this round's action executed")
        # Persisted, not just in-memory.
        reloaded = teamsmod._load_state("run-ckpt1")
        self.assertEqual(reloaded["status"], "stopped")

    def test_cancel_not_set_checkpoint_never_fires_byte_for_byte_unchanged(self):
        state = teamsmod._new_state("run-ckpt1-off", self.projdir, _lead(), [], "task")
        state["action_count"] = 1  # avoid the unrelated "premature finish" business rule
        cancel_event = threading.Event()  # never set

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state, cancel_event=cancel_event)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "finished")

    def test_cancel_event_none_default_byte_for_byte_unchanged(self):
        # Every existing (pre-6d-part-2a) caller never supplies cancel_event
        # at all -- explicitly re-confirmed here rather than only relying on
        # the untouched 6c suite passing.
        state = teamsmod._new_state("run-ckpt1-none", self.projdir, _lead(), [], "task")
        state["action_count"] = 1  # avoid the unrelated "premature finish" business rule

        def fake_call_lead(state, system, round_context, **kwargs):
            self.assertIsNone(kwargs.get("cancel_event"))
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "finished")


# ─── Tier 1: team_step()'s checkpoint 2 (right after the delegate branch's
# own agent_run() call returns) ─────────────────────────────────────────────
class TeamStepCheckpointTwoTests(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-cancel-proj2-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-cancel-state2-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.projdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_cancel_set_before_agent_run_returns_records_clean_stopped_not_failed(self):
        # Not the generic "FAILED (unknown error, see log)" framing a bare
        # cancelled agent_run() result would otherwise produce (docs/spec.md
        # §7) -- a distinct, named outcome instead. cancel_event is set
        # INSIDE the fake agent_run() call (simulating the interruption
        # happening during the call), not before _call_lead() ever runs --
        # setting it that early would make checkpoint 1 intercept first
        # instead, which is checkpoint 1's own test above, not this one.
        state = teamsmod._new_state("run-ckpt2", self.projdir, _lead(), ["claude"], "task")
        cancel_event = threading.Event()

        def fake_call_lead(state, system, round_context, **kwargs):
            self.assertFalse(cancel_event.is_set())
            return {"tool": "delegate", "args": {"agent": "claude", "task": "do it"}}, None, "..."

        def fake_agent_run(engine, workdir, prompt, *, cancel_event=None, **kw):
            self.assertIsNotNone(cancel_event)
            cancel_event.set()
            return _dummy_agent_result(ok=False, cancelled=True, cancel_reason="stopped",
                                       exit_code=128 + 15, text="partial output")

        orig_call_lead, orig_agent_run = teamsmod._call_lead, teamsmod.agent_run
        teamsmod._call_lead = fake_call_lead
        teamsmod.agent_run = fake_agent_run
        try:
            teamsmod.team_step(state, cancel_event=cancel_event)
        finally:
            teamsmod._call_lead = orig_call_lead
            teamsmod.agent_run = orig_agent_run

        self.assertEqual(state["status"], "stopped")
        entry = state["history"][-1]
        self.assertEqual(entry["tool"], "delegate")
        self.assertEqual(entry["outcome_summary"], "stopped by request (delegation interrupted)")
        self.assertIn("claude", entry["args_summary"])
        self.assertIsNone(state["in_progress_delegate"])  # still cleared, same as the ordinary path

    def test_cancel_not_set_delegate_branch_byte_for_byte_unchanged(self):
        state = teamsmod._new_state("run-ckpt2-off", self.projdir, _lead(), ["claude"], "task")
        cancel_event = threading.Event()

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "claude", "task": "do it"}}, None, "..."

        def fake_agent_run(engine, workdir, prompt, **kw):
            return _dummy_agent_result(ok=True, text="done", session_id="s1")

        orig_call_lead, orig_agent_run = teamsmod._call_lead, teamsmod.agent_run
        teamsmod._call_lead = fake_call_lead
        teamsmod.agent_run = fake_agent_run
        try:
            teamsmod.team_step(state, cancel_event=cancel_event)
        finally:
            teamsmod._call_lead = orig_call_lead
            teamsmod.agent_run = orig_agent_run

        self.assertEqual(state["status"], "running")
        self.assertEqual(state["history"][-1]["outcome_summary"], "SUCCEEDED, 4 chars (see log)")


# ─── Tier 1: team_run()'s own top-of-loop checkpoint ───────────────────────
class TeamRunLoopCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-cancel-proj3-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-cancel-state3-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.projdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_cancel_set_before_team_run_starts_never_calls_team_step_at_all(self):
        # The cheapest possible checkpoint -- avoids even starting a new
        # round (docs/spec.md §7).
        state = teamsmod._new_state("run-ckptloop", self.projdir, _lead(), [], "task")
        cancel_event = threading.Event()
        cancel_event.set()
        call_count = {"n": 0}

        def fake_call_lead(state, system, round_context, **kwargs):
            call_count["n"] += 1
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_run(state, cancel_event=cancel_event)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(call_count["n"], 0)
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["history"], [])

    def test_cancel_set_between_rounds_stops_before_the_next_round_starts(self):
        state = teamsmod._new_state("run-ckptloop2", self.projdir, _lead(), [], "task")
        cancel_event = threading.Event()
        call_count = {"n": 0}

        def fake_call_lead(state, system, round_context, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                cancel_event.set()  # simulates a stop arriving while round 1 is being processed
                return {"tool": "fact_check", "args": {"claim": "x"}}, None, "..."
            self.fail("team_run() must not start a second round once cancel_event is set")

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_run(state, cancel_event=cancel_event)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(call_count["n"], 1)
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(len(state["history"]), 1)  # round 1's own fact_check round completed normally

    def test_cancel_event_none_default_byte_for_byte_unchanged(self):
        state = teamsmod._new_state("run-ckptloop-none", self.projdir, _lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            # First round establishes prior work (avoids the unrelated
            # "premature finish" business rule); second round finishes.
            if state["action_count"] == 0:
                return {"tool": "fact_check", "args": {"claim": "x"}}, None, "..."
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_run(state)  # no cancel_event kwarg at all
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "finished")


# ─── Tier 1: _call_lead()'s tier-1 branch accepts but ignores cancel_event ──
class CallLeadTier1CancelEventIgnoredTests(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-cancel-proj4-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-cancel-state4-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.projdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_tier1_has_no_subprocess_to_signal_cancel_event_accepted_but_unused(self):
        state = teamsmod._new_state("run-tier1cancel", self.projdir, _lead(tier=1), [], "task")
        cancel_event = threading.Event()
        cancel_event.set()

        def fake_tier1_call(base_url, model, sys_part, user_part, tools, *, timeout, retry_budget):
            return {"choices": [{"message": {"tool_calls": [
                {"function": {"name": "finish", "arguments": '{"summary": "done"}'}}]}}]}, None

        orig = teamsmod._tier1_call_with_retry
        teamsmod._tier1_call_with_retry = fake_tier1_call
        try:
            raw, err, raw_text = teamsmod._call_lead(state, "sys", "ctx", cancel_event=cancel_event)
        finally:
            teamsmod._tier1_call_with_retry = orig
        self.assertIsNone(err)
        self.assertEqual(raw["tool"], "finish")


# ─── Tier 2/3: real tmux, real subprocess ───────────────────────────────────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmuxCancelEventAgentRunTests(unittest.TestCase):
    """agent_run(..., cancel_event=...) against a real, deliberately slow
    stand-in engine -- mirrors tests/test_teams_headless.py's own
    RealTmuxHeadlessTests timeout-escalation tests, with cancel_event as the
    trigger instead of timeout expiring."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-cancel-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-cancel-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-cancel-headlessstate-")
        self.scripts_dir = tempfile.mkdtemp(prefix="switchboard-cancel-scripts-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        self._orig_poll = teamsmod.TEAM_HEADLESS_POLL_SECONDS
        self._orig_grace = teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = 0.1
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = 1.0
        _patch_tmux(self, ["tmux"])
        _scope_run_ids(self)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = self._orig_poll
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = self._orig_grace
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.scripts_dir, ignore_errors=True)
        _no_leftover_headless_sessions_ignore = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        for name in _no_leftover_headless_sessions_ignore.stdout.splitlines():
            if name.startswith(_SESSION_PREFIX):
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_cancel_event_set_from_second_thread_mid_flight_terminates_and_reports_stopped(self):
        script = _write_script(self.scripts_dir, "hang.py", """\
            #!/usr/bin/env python3
            import time
            while True:
                time.sleep(0.05)
            """)
        _write_engine_file(self.engines_dir, "hanger.engine", f"""\
            LABEL=Hanger
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cancel_event = threading.Event()
        result_holder = {}

        def _run():
            result_holder["result"] = teamsmod.agent_run(
                "hanger", self.workdir, "go", timeout=30, cancel_event=cancel_event)

        t = threading.Thread(target=_run)
        t.start()
        time.sleep(0.5)  # let the real tmux session actually start and get into its poll loop
        cancel_event.set()
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "agent_run() did not return within the bounded escalation window")
        result = result_holder["result"]
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "stopped")
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 128 + 15)  # SIGTERM was enough
        _no_leftover_headless_sessions(self)

    def test_cancel_event_already_set_before_call_terminates_immediately(self):
        script = _write_script(self.scripts_dir, "hang2.py", """\
            #!/usr/bin/env python3
            import time
            while True:
                time.sleep(0.05)
            """)
        _write_engine_file(self.engines_dir, "hanger2.engine", f"""\
            LABEL=Hanger2
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cancel_event = threading.Event()
        cancel_event.set()
        result = teamsmod.agent_run("hanger2", self.workdir, "go", timeout=30, cancel_event=cancel_event)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "stopped")
        _no_leftover_headless_sessions(self)

    def test_sigterm_ignored_via_cancel_event_escalates_to_sigkill(self):
        script = _write_script(self.scripts_dir, "stubborn.py", """\
            #!/usr/bin/env python3
            import signal
            import time
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(0.05)
            """)
        _write_engine_file(self.engines_dir, "stubborn.engine", f"""\
            LABEL=Stubborn
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        cancel_event = threading.Event()
        cancel_event.set()
        result = teamsmod.agent_run("stubborn", self.workdir, "go", timeout=30, cancel_event=cancel_event)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "stopped")
        self.assertEqual(result["exit_code"], 128 + 9)  # escalated all the way to SIGKILL
        _no_leftover_headless_sessions(self)

    def test_timeout_still_wins_when_cancel_event_never_set_byte_for_byte_unchanged(self):
        script = _write_script(self.scripts_dir, "hang3.py", """\
            #!/usr/bin/env python3
            import time
            while True:
                time.sleep(0.05)
            """)
        _write_engine_file(self.engines_dir, "hanger3.engine", f"""\
            LABEL=Hanger3
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        result = teamsmod.agent_run("hanger3", self.workdir, "go", timeout=0.5)  # no cancel_event at all
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "timeout")
        _no_leftover_headless_sessions(self)


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmuxMidDelegateAndMidLeadCallCancelTests(unittest.TestCase):
    """team_step()/team_run()'s own checkpoints exercised end to end via a
    real, controllable stand-in and a real background thread setting
    cancel_event mid-call -- the specific 'stop arriving mid-delegation'
    (and mid-lead-call) concurrency case this cycle's own risk assessment
    names explicitly. No launch_team()/worktrees involved -- the delegate
    branch falls back to state["workdir"] with no worktrees entry, same
    byte-for-byte 6c behavior part 1 already establishes."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-cancel-mdengines-")
        self.workdir = tempfile.mkdtemp(prefix="switchboard-cancel-mdworkdir-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-cancel-mdstate-")
        self.scripts_dir = tempfile.mkdtemp(prefix="switchboard-cancel-mdscripts-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        self._orig_poll = teamsmod.TEAM_HEADLESS_POLL_SECONDS
        self._orig_grace = teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = 0.1
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = 1.0
        _patch_tmux(self, ["tmux"])
        _scope_run_ids(self)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = self._orig_poll
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = self._orig_grace
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.workdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.scripts_dir, ignore_errors=True)
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        for name in r.stdout.splitlines():
            if name.startswith(_SESSION_PREFIX):
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def _write_slow_engine(self, name):
        script = _write_script(self.scripts_dir, f"{name}.py", """\
            #!/usr/bin/env python3
            import time
            while True:
                time.sleep(0.05)
            """)
        _write_engine_file(self.engines_dir, f"{name}.engine", f"""\
            LABEL={name}
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)

    def test_stop_mid_delegate_terminates_real_subprocess_and_records_stopped(self):
        self._write_slow_engine("slowmate")
        state = teamsmod._new_state("run-middelegate", self.workdir, _lead(), ["slowmate"], "task")
        teamsmod._persist(state)

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "slowmate", "task": "go slow"}}, None, "..."

        cancel_event = threading.Event()
        orig_call_lead = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        t = threading.Thread(target=teamsmod.team_run, args=(state,), kwargs={"cancel_event": cancel_event})
        try:
            t.start()
            time.sleep(0.5)  # let agent_run() actually spawn the real tmux session
            cancel_event.set()
            t.join(timeout=10)
        finally:
            teamsmod._call_lead = orig_call_lead
        self.assertFalse(t.is_alive(), "team_run() did not return within the bounded escalation window")
        self.assertEqual(state["status"], "stopped")
        entry = state["history"][-1]
        self.assertEqual(entry["tool"], "delegate")
        self.assertEqual(entry["outcome_summary"], "stopped by request (delegation interrupted)")
        # Real subprocess actually gone, not just marked in state.
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        leftover = [n for n in r.stdout.splitlines() if n.startswith(_SESSION_PREFIX)]
        self.assertEqual(leftover, [])

    def test_stop_mid_lead_call_terminates_real_subprocess_and_records_stopped(self):
        self._write_slow_engine("slowlead")
        lead = {"kind": "engine", "name": "slowlead", "tier": 3}
        state = teamsmod._new_state("run-midlead", self.workdir, lead, [], "task")
        teamsmod._persist(state)

        cancel_event = threading.Event()
        t = threading.Thread(target=teamsmod.team_run, args=(state,), kwargs={"cancel_event": cancel_event})
        t.start()
        time.sleep(0.5)
        cancel_event.set()
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "team_run() did not return within the bounded escalation window")
        self.assertEqual(state["status"], "stopped")
        entry = state["history"][-1]
        self.assertIsNone(entry["tool"])
        self.assertEqual(entry["outcome_summary"],
                         "stopped by request before this round's action executed")
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        leftover = [n for n in r.stdout.splitlines() if n.startswith(_SESSION_PREFIX)]
        self.assertEqual(leftover, [])


if __name__ == "__main__":
    unittest.main()
