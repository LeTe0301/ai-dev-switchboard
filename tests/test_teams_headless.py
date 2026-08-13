"""
Tests for headless engine invocation (backlog item 6, sub-spec 6a --
docs/spec.md). Three tiers, matching the spec's own "Test plan":

Tier 1 (bulk of this file) -- pure unit, no subprocess, no tmux, no real CLI:
_parse_engine_file()'s four new keys + the reserved-name rule; the pure
argv/script-template construction functions; the envelope translator fed the
recorded fixture files under tests/fixtures/headless/ (see that directory's
own README.md for exactly which fixtures are real captures vs. synthesized);
malformed-line/stream-cap handling fed synthetic fixtures built to be
malformed/oversized on purpose; every "Edge cases" validation failure
asserted as ValueError with subprocess.run/tmux_has monkeypatched to raise
if either is ever called (proves nothing was spawned).

Tier 2 -- real tmux, real (test-authored) helper processes, no real engine
CLI, no sudo: TMUX is monkeypatched from
["sudo", "-u", RUN_USER, "/usr/bin/tmux"] down to ["tmux"] -- same category
of substitution tests/test_gitea_poll.py already applies to
subprocess.run/_gitea_api. Because `from app import TMUX, tmux_has,
load_engines` in app/teams.py creates its own name binding, tests that need
the *new-session*/*kill-session* calls teams.py makes directly AND the
has-session checks inside app.py's own tmux_has() to agree must patch BOTH
appmod.TMUX and teamsmod.TMUX -- see _patch_tmux() below.

Tier 3 (manual, developer stage) is NOT in this file -- see
docs/implementation.md for what was actually run against the real claude/
codex CLIs and what remains unverified (aider was not installed).

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

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures", "headless")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-headless-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
import teams as teamsmod  # noqa: E402


def _write_engine_file(dirpath, filename, body):
    with open(os.path.join(dirpath, filename), "w") as f:
        f.write(textwrap.dedent(body))


def _fixture(name):
    return os.path.join(FIXTURES_DIR, name)


def _patch_tmux(testcase, argv):
    """Patches TMUX in both app.py (which tmux_has()/active_engine() read
    from their own module namespace) and teams.py (which built its own name
    binding via `from app import TMUX`) -- see this file's own docstring."""
    orig_app = appmod.TMUX
    orig_teams = teamsmod.TMUX
    appmod.TMUX = argv
    teamsmod.TMUX = argv

    def _restore():
        appmod.TMUX = orig_app
        teamsmod.TMUX = orig_teams
    testcase.addCleanup(_restore)


def _forbid_spawn(testcase):
    """Fails the test if subprocess.run or tmux_has is ever called -- proves
    a ValueError was raised before anything was spawned (docs/spec.md
    acceptance criteria)."""
    orig_run = subprocess.run
    orig_has = teamsmod.tmux_has

    def _run(*a, **kw):
        testcase.fail(f"subprocess.run() was called: {a!r} {kw!r}")

    def _has(*a, **kw):
        testcase.fail(f"tmux_has() was called: {a!r} {kw!r}")

    subprocess.run = _run
    teamsmod.tmux_has = _has

    def _restore():
        subprocess.run = orig_run
        teamsmod.tmux_has = orig_has
    testcase.addCleanup(_restore)


# ─── Tier 1: engine parsing (Engine.__slots__ + _parse_engine_file()) ─────
class EngineHeadlessParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-engines-")
        self._orig_dir = appmod.ENGINES_DIR
        appmod.ENGINES_DIR = self.tmp

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_engine_with_no_headless_cmd_is_unaffected(self):
        _write_engine_file(self.tmp, "plain.engine", """\
            LABEL=Plain
            CMD=plain-tool {name}
            """)
        engines = appmod.load_engines()
        e = engines["plain"]
        self.assertFalse(e.headless_enabled)
        self.assertIsNone(e.headless_cmd)
        self.assertEqual(e.cmd, "plain-tool {name}")  # byte-for-byte unaffected

    def test_valid_headless_keys_enable_headless(self):
        _write_engine_file(self.tmp, "claude.engine", """\
            LABEL=Claude
            CMD=claude --remote-control {name}
            HEADLESS_CMD=claude -p {resume} --output-format stream-json --verbose
            HEADLESS_FORMAT=claude-stream-json
            HEADLESS_PROMPT=arg
            HEADLESS_RESUME=--resume {session_id}
            """)
        e = appmod.load_engines()["claude"]
        self.assertTrue(e.headless_enabled)
        self.assertEqual(e.headless_format, "claude-stream-json")
        self.assertEqual(e.headless_prompt, "arg")
        self.assertEqual(e.headless_resume, "--resume {session_id}")

    def test_missing_headless_format_leaves_headless_disabled_not_an_exception(self):
        _write_engine_file(self.tmp, "bad.engine", """\
            LABEL=Bad
            CMD=bad-tool
            HEADLESS_CMD=bad-tool --headless
            HEADLESS_PROMPT=arg
            """)
        e = appmod.load_engines()["bad"]
        self.assertFalse(e.headless_enabled)
        self.assertIsNone(e.headless_cmd)
        self.assertEqual(e.cmd, "bad-tool")

    def test_unrecognized_headless_format_value_leaves_headless_disabled(self):
        _write_engine_file(self.tmp, "bad2.engine", """\
            LABEL=Bad2
            CMD=bad-tool
            HEADLESS_CMD=bad-tool --headless
            HEADLESS_FORMAT=not-a-real-format
            HEADLESS_PROMPT=arg
            """)
        e = appmod.load_engines()["bad2"]
        self.assertFalse(e.headless_enabled)

    def test_unrecognized_headless_prompt_value_leaves_headless_disabled(self):
        _write_engine_file(self.tmp, "bad3.engine", """\
            LABEL=Bad3
            CMD=bad-tool
            HEADLESS_CMD=bad-tool --headless
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=not-a-real-mode
            """)
        e = appmod.load_engines()["bad3"]
        self.assertFalse(e.headless_enabled)

    def test_headless_cmd_without_resume_key_is_fine(self):
        _write_engine_file(self.tmp, "aider.engine", """\
            LABEL=aider
            CMD=aider
            HEADLESS_CMD=aider --message-file {prompt_file} --yes-always
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=file
            """)
        e = appmod.load_engines()["aider"]
        self.assertTrue(e.headless_enabled)
        self.assertIsNone(e.headless_resume)

    def test_switchboard_headless_engine_file_is_ignored(self):
        _write_engine_file(self.tmp, "switchboard-headless.engine", """\
            LABEL=Should be ignored
            CMD=whatever
            """)
        self.assertNotIn("switchboard-headless", appmod.load_engines())

    def test_switchboard_exact_name_engine_file_is_ignored(self):
        # The non-obvious collision case (docs/spec.md "Session naming"): an
        # implementation reserving only the exact name "switchboard-headless"
        # passes the test above but fails this one -- both must pass.
        _write_engine_file(self.tmp, "switchboard.engine", """\
            LABEL=Should also be ignored
            CMD=whatever
            """)
        self.assertNotIn("switchboard", appmod.load_engines())

    def test_switchboard_prefixed_other_name_is_also_ignored(self):
        _write_engine_file(self.tmp, "switchboard-anything-else.engine", """\
            LABEL=Also ignored
            CMD=whatever
            """)
        self.assertNotIn("switchboard-anything-else", appmod.load_engines())

    def test_non_switchboard_engine_is_not_affected_by_the_reserved_prefix(self):
        _write_engine_file(self.tmp, "notswitchboard.engine", """\
            LABEL=Fine
            CMD=whatever
            """)
        self.assertIn("notswitchboard", appmod.load_engines())


class ActiveEngineHeadlessCollisionTests(unittest.TestCase):
    """docs/spec.md "Session naming": active_engine() must never report a
    live headless run as a project's running engine, even in the
    non-obvious `switchboard` + `headless-<run_id>` shape."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-projects-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_projects_dir = appmod.PROJECTS_DIR
        appmod.ENGINES_DIR = self.engines_dir
        appmod.PROJECTS_DIR = self.projects_dir
        _patch_tmux(self, ["tmux"])
        _write_engine_file(self.engines_dir, "switchboard.engine", """\
            LABEL=Should be ignored
            CMD=whatever
            """)
        _write_engine_file(self.engines_dir, "claude.engine", """\
            LABEL=Claude
            CMD=sleep 100
            """)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        appmod.PROJECTS_DIR = self._orig_projects_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def test_live_headless_session_never_reported_as_project_engine(self):
        run_id = "R"
        session = f"switchboard-headless-{run_id}"
        project_name = f"headless-{run_id}"
        os.makedirs(os.path.join(self.projects_dir, project_name))
        subprocess.run(["tmux", "new-session", "-d", "-s", session, "sleep", "60"])
        self.addCleanup(lambda: subprocess.run(["tmux", "kill-session", "-t", session],
                                               capture_output=True))
        self.assertTrue(appmod.tmux_has(session))
        self.assertIsNone(appmod.active_engine(project_name))


# ─── Tier 1: pure argv/script construction ────────────────────────────────
class HeadlessArgvBuildingTests(unittest.TestCase):
    def _engine(self, **kw):
        defaults = dict(name="claude", label="Claude", cmd="claude {name}", url_regex=None,
                        startup=[], headless_cmd="claude -p {resume} --output-format stream-json",
                        headless_format="claude-stream-json", headless_prompt="arg",
                        headless_resume="--resume {session_id}")
        defaults.update(kw)
        return appmod.Engine(defaults["name"], defaults["label"], defaults["cmd"],
                             defaults["url_regex"], defaults["startup"],
                             defaults["headless_cmd"], defaults["headless_format"],
                             defaults["headless_prompt"], defaults["headless_resume"])

    def test_resume_fragment_empty_on_first_turn(self):
        e = self._engine()
        self.assertEqual(teamsmod._resume_fragment(e, None), "")

    def test_resume_fragment_substitutes_session_id(self):
        e = self._engine()
        self.assertEqual(teamsmod._resume_fragment(e, "abc123"), "--resume abc123")

    def test_resume_fragment_codex_subcommand_swap_shape(self):
        e = self._engine(headless_cmd="codex exec {resume} --json --skip-git-repo-check",
                         headless_resume="resume {session_id}")
        self.assertEqual(teamsmod._resume_fragment(e, "abc123"), "resume abc123")

    def test_session_id_without_resume_key_raises_value_error(self):
        e = self._engine(headless_resume=None)
        with self.assertRaises(ValueError):
            teamsmod._resume_fragment(e, "abc123")

    def test_session_id_without_resume_token_in_cmd_raises_value_error(self):
        e = self._engine(headless_cmd="claude -p --output-format stream-json")
        with self.assertRaises(ValueError):
            teamsmod._resume_fragment(e, "abc123")

    def test_build_headless_argv_first_turn_arg_mode(self):
        e = self._engine()
        argv = teamsmod._build_headless_argv(e, "hello world", None)
        self.assertEqual(argv, ["claude", "-p", "--output-format", "stream-json", "hello world"])

    def test_build_headless_argv_resumed_turn_arg_mode(self):
        e = self._engine()
        argv = teamsmod._build_headless_argv(e, "hello", "sid-1")
        self.assertEqual(argv,
                         ["claude", "-p", "--resume", "sid-1", "--output-format", "stream-json", "hello"])

    def test_build_headless_argv_file_mode_substitutes_prompt_file(self):
        e = self._engine(headless_cmd="aider --message-file {prompt_file} --yes-always",
                         headless_prompt="file", headless_resume=None)
        argv = teamsmod._build_headless_argv(e, "do the thing", None, prompt_path="/tmp/x/prompt")
        self.assertEqual(argv, ["aider", "--message-file", "/tmp/x/prompt", "--yes-always"])

    def test_build_headless_argv_stdin_mode_does_not_append_prompt(self):
        e = self._engine(headless_cmd="some-tool --flag", headless_prompt="stdin", headless_resume=None)
        argv = teamsmod._build_headless_argv(e, "some prompt text", None)
        self.assertEqual(argv, ["some-tool", "--flag"])

    def test_validate_prompt_size_arg_mode_within_cap(self):
        teamsmod._validate_prompt_size("arg", "x" * 100)  # must not raise

    def test_validate_prompt_size_arg_mode_over_cap_raises(self):
        with self.assertRaises(ValueError):
            teamsmod._validate_prompt_size("arg", "x" * (teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES + 1))

    def test_validate_prompt_size_stdin_mode_uses_the_larger_cap(self):
        # Bigger than the arg-mode cap but within the stdin-mode cap -- must not raise.
        n = teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES + 1000
        self.assertLess(n, teamsmod.TEAM_HEADLESS_PROMPT_MAX_BYTES)
        teamsmod._validate_prompt_size("stdin", "x" * n)

    def test_validate_prompt_size_stdin_mode_over_cap_raises(self):
        with self.assertRaises(ValueError):
            teamsmod._validate_prompt_size("stdin", "x" * (teamsmod.TEAM_HEADLESS_PROMPT_MAX_BYTES + 1))

    def test_empty_prompt_allowed(self):
        teamsmod._validate_prompt_size("arg", "")  # must not raise

    # ── docs/test-review.md Defect 1 regression ───────────────────────
    # A raw-byte-count check against TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES is
    # not enough: shlex.quote() expands each "'" to "'\"'\"'", a 5x blowup
    # for quote-heavy content. An ordinary-text prompt of the same raw
    # length would pass both before and after the fix -- these tests use
    # quote-heavy input specifically, which is exactly what slipped through
    # the old raw-length-only check and crashed subprocess.run() with an
    # uncaught OSError well inside the documented "safe" cap.
    def test_quote_heavy_prompt_well_under_old_raw_cap_now_rejected(self):
        # 30,000 raw bytes -- under half of TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES
        # (65536) -- but shlex.quote()'d length is ~150,000 bytes, over
        # Linux's MAX_ARG_STRLEN (131072). docs/test-review.md's exact repro.
        prompt = "'" * 30000
        self.assertLess(len(prompt.encode()), teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES // 2)
        with self.assertRaises(ValueError) as ctx:
            teamsmod._validate_prompt_size("arg", prompt)
        self.assertIn("shell-escaped", str(ctx.exception))

    def test_plain_text_of_the_same_raw_length_is_still_accepted(self):
        # Same raw byte count as the quote-heavy case above, but no
        # expansion on shlex.quote() -- proves the fix targets the actual
        # quoting blowup, not just a smaller raw-byte number.
        prompt = "x" * 30000
        teamsmod._validate_prompt_size("arg", prompt)  # must not raise

    def test_quoted_length_exactly_at_the_cap_is_accepted(self):
        cap = min(teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES,
                  teamsmod._MAX_ARG_STRLEN - teamsmod._ARG_SCRIPT_OVERHEAD_BYTES)
        prompt = "x" * cap  # plain text: quoted length == raw length == cap
        teamsmod._validate_prompt_size("arg", prompt)  # must not raise

    def test_quoted_length_one_over_the_cap_is_rejected(self):
        cap = min(teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES,
                  teamsmod._MAX_ARG_STRLEN - teamsmod._ARG_SCRIPT_OVERHEAD_BYTES)
        prompt = "x" * (cap + 1)
        with self.assertRaises(ValueError):
            teamsmod._validate_prompt_size("arg", prompt)

    def test_build_script_shape(self):
        script = teamsmod._build_script(["echo", "hi there"], "arg", "/x/prompt",
                                        "/x/out.jsonl", "/x/out.err", "/x/out.pid", "/x/out.rc")
        self.assertIn("echo 'hi there'", script)
        self.assertIn(">/x/out.jsonl", script)
        self.assertIn("2>/x/out.err", script)
        self.assertIn("echo $! >/x/out.pid", script)
        self.assertIn("wait $!; echo $? >/x/out.rc", script)
        self.assertNotIn("< ", script)  # no stdin redirect for arg mode

    def test_build_script_stdin_mode_adds_redirect(self):
        script = teamsmod._build_script(["cat"], "stdin", "/x/prompt",
                                        "/x/out.jsonl", "/x/out.err", "/x/out.pid", "/x/out.rc")
        self.assertIn("< /x/prompt", script)


# ─── Tier 1: envelope translation against real/synthesized fixtures ──────
class ClaudeTranslationTests(unittest.TestCase):
    def _load_lines(self):
        with open(_fixture("claude_stream.jsonl")) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_kind_mapping_across_the_real_captured_fixture(self):
        kinds_seen = set()
        session_ids = set()
        result_text = None
        for native in self._load_lines():
            for kind, text, meta in teamsmod._translate_claude(native):
                self.assertIn(kind, {"message", "tool_use", "tool_result", "status", "error", "handoff"})
                kinds_seen.add(kind)
                if meta.get("session_id"):
                    session_ids.add(meta["session_id"])
                if meta.get("native_type") == "result":
                    result_text = text
        self.assertIn("message", kinds_seen)   # the final "ok" assistant text block
        self.assertIn("status", kinds_seen)    # system/result/thinking events
        self.assertEqual(session_ids, {"ec4f3b1b-8ef0-4c51-8740-7349b63d8ac5"})
        self.assertEqual(result_text, "ok")

    def test_thinking_block_is_status_not_message(self):
        for native in self._load_lines():
            if native.get("type") != "assistant":
                continue
            for block in native["message"].get("content", []):
                if block.get("type") == "thinking":
                    events = teamsmod._translate_claude(native)
                    kinds = {k for k, _, m in events if m.get("block_type") == "thinking"}
                    self.assertEqual(kinds, {"status"})
                    return
        self.fail("fixture did not contain a thinking block -- test assumption stale")

    def test_unrecognized_top_level_type_is_passed_through_as_status(self):
        events = teamsmod._translate_claude({"type": "rate_limit_event", "rate_limit_info": {}})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "status")
        self.assertEqual(events[0][2]["native_type"], "rate_limit_event")

    def test_tool_use_and_tool_result_mapping(self):
        assistant = {"type": "assistant", "session_id": "s1",
                     "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash",
                                              "input": {"command": "ls"}}]}}
        events = teamsmod._translate_claude(assistant)
        self.assertEqual(events, [("tool_use", "Bash",
                                   {"native_type": "assistant", "block_type": "tool_use",
                                    "tool_use_id": "tu1", "input": {"command": "ls"}, "session_id": "s1"})])
        user = {"type": "user", "session_id": "s1",
               "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "file1\n"}]}}
        events = teamsmod._translate_claude(user)
        self.assertEqual(events, [("tool_result", "file1\n",
                                   {"native_type": "user", "tool_use_id": "tu1", "session_id": "s1"})])

    def test_user_event_non_tool_result_block_is_not_silently_dropped(self):
        # docs/test-review.md Finding A regression: a user-role message
        # whose content block isn't a tool_result (e.g. a plain "text"
        # block) used to produce zero events -- inconsistent with the
        # "nothing lost" fallback the assistant branch and the top-level
        # unknown-type branch both already have.
        user = {"type": "user", "session_id": "s1",
               "message": {"content": [{"type": "text", "text": "some plain text"}]}}
        events = teamsmod._translate_claude(user)
        self.assertEqual(events, [("status", "",
                                   {"native_type": "user", "block_type": "text", "session_id": "s1"})])

    def test_user_event_with_no_recognized_content_still_returns_empty_list_not_none(self):
        # A user event with an empty content list is a legitimate zero-event
        # case (nothing to report), distinct from Finding A's silent-drop of
        # a *present* but unrecognized block.
        user = {"type": "user", "session_id": "s1", "message": {"content": []}}
        self.assertEqual(teamsmod._translate_claude(user), [])


class TranslatorShapeSafetyTests(unittest.TestCase):
    """docs/test-review.md round 3, Finding 1 regression: syntactically
    valid JSON whose *shape* doesn't match what a translator assumes (a
    field that's a str/list/None where a dict is expected) must degrade to
    one error event via _translate_safely(), never raise uncaught. The raw
    translators themselves are still allowed to raise (that's the whole
    point of the boundary wrapper existing one level up, in _Tailer) --
    these tests confirm both that the raw functions raise (so the wrapper
    is actually doing something) and that the wrapper catches it cleanly."""

    def test_translate_claude_raw_function_still_raises_on_bad_message_shape(self):
        # Confirms the boundary wrapper is load-bearing, not a no-op --
        # without it, this exact input crashes.
        with self.assertRaises(AttributeError):
            teamsmod._translate_claude({"type": "assistant", "message": "not an object"})
        with self.assertRaises(AttributeError):
            teamsmod._translate_claude({"type": "user", "message": [1, 2, 3]})

    def test_translate_codex_raw_function_still_raises_on_bad_item_shape(self):
        with self.assertRaises(AttributeError):
            teamsmod._translate_codex({"type": "item.completed", "item": "not an object"})
        with self.assertRaises(AttributeError):
            teamsmod._translate_codex({"type": "turn.failed", "error": "not an object"})

    def test_translate_safely_catches_claude_shape_crash(self):
        events, err = teamsmod._translate_safely(
            teamsmod._translate_claude, {"type": "assistant", "message": "not an object"})
        self.assertEqual(events, [])
        self.assertIsNotNone(err)
        self.assertIn("AttributeError", err)

    def test_translate_safely_catches_codex_shape_crash(self):
        events, err = teamsmod._translate_safely(
            teamsmod._translate_codex, {"type": "item.completed", "item": "not an object"})
        self.assertEqual(events, [])
        self.assertIsNotNone(err)
        self.assertIn("AttributeError", err)

    def test_translate_safely_passes_through_normal_events_unchanged(self):
        native = {"type": "system", "subtype": "init", "session_id": "s1"}
        events, err = teamsmod._translate_safely(teamsmod._translate_claude, native)
        self.assertIsNone(err)
        self.assertEqual(events, teamsmod._translate_claude(native))

    def test_tailer_emits_one_error_event_for_a_claude_shape_crash_and_continues(self):
        tmp = tempfile.mkdtemp(prefix="switchboard-shape-crash-")
        try:
            out_path = os.path.join(tmp, "out.jsonl")
            log_path = os.path.join(tmp, "log.jsonl")
            with open(out_path, "wb") as f:
                f.write(b'{"type": "system", "subtype": "init"}\n')
                f.write(b'{"type": "assistant", "message": "not an object"}\n')
                f.write(b'{"type": "result", "result": "done"}\n')
            tailer = teamsmod._Tailer(out_path, "claude-stream-json", log_path, "claude")
            tailer.poll()  # must not raise
            with open(log_path) as f:
                events = [json.loads(line) for line in f if line.strip()]
            error_events = [e for e in events if e["kind"] == "error"]
            self.assertEqual(len(error_events), 1)
            self.assertIn("unexpected event shape", error_events[0]["text"])
            # Processing continued past the bad line to the next one.
            self.assertEqual(tailer.final_text(), "done")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tailer_emits_one_error_event_for_a_codex_shape_crash_and_continues(self):
        tmp = tempfile.mkdtemp(prefix="switchboard-shape-crash-codex-")
        try:
            out_path = os.path.join(tmp, "out.jsonl")
            log_path = os.path.join(tmp, "log.jsonl")
            with open(out_path, "wb") as f:
                f.write(b'{"type": "thread.started", "thread_id": "t1"}\n')
                f.write(b'{"type": "item.completed", "item": "not an object"}\n')
                f.write(b'{"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "hi"}}\n')
            tailer = teamsmod._Tailer(out_path, "codex-jsonl", log_path, "codex")
            tailer.poll()  # must not raise
            with open(log_path) as f:
                events = [json.loads(line) for line in f if line.strip()]
            error_events = [e for e in events if e["kind"] == "error"]
            self.assertEqual(len(error_events), 1)
            self.assertEqual(tailer.session_id, "t1")
            self.assertEqual(tailer.final_text(), "hi")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CodexTranslationTests(unittest.TestCase):
    def _load_lines(self, fixture_name):
        with open(_fixture(fixture_name)) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_authfail_fixture_maps_thread_started_and_turn_failed(self):
        events_by_native_type = {}
        for native in self._load_lines("codex_stream_authfail.jsonl"):
            for kind, text, meta in teamsmod._translate_codex(native):
                events_by_native_type.setdefault(meta["native_type"], []).append((kind, text))
        self.assertIn("thread.started", events_by_native_type)
        self.assertIn("turn.failed", events_by_native_type)
        self.assertEqual(events_by_native_type["turn.failed"][0][0], "error")

    def test_thread_started_carries_session_id(self):
        native = {"type": "thread.started", "thread_id": "019ffb02"}
        events = teamsmod._translate_codex(native)
        self.assertEqual(events, [("status", "", {"native_type": "thread.started", "session_id": "019ffb02"})])

    def test_success_fixture_final_message_is_a_message_kind_event(self):
        events = []
        for native in self._load_lines("codex_stream_success.jsonl"):
            events.extend(teamsmod._translate_codex(native))
        message_events = [e for e in events if e[0] == "message"]
        self.assertEqual(message_events, [("message", "pineapple",
                                           {"native_type": "item.completed", "item_type": "agent_message",
                                            "item_id": "item_0"})])

    def test_item_started_agent_message_is_status_not_message(self):
        native = {"type": "item.started", "item": {"id": "item_0", "type": "agent_message"}}
        events = teamsmod._translate_codex(native)
        self.assertEqual(events[0][0], "status")

    def test_command_execution_item_maps_to_tool_use_then_tool_result(self):
        started = {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}}
        completed = {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "ls"}}
        self.assertEqual(teamsmod._translate_codex(started)[0][0], "tool_use")
        self.assertEqual(teamsmod._translate_codex(completed)[0][0], "tool_result")

    def test_top_level_error_event(self):
        events = teamsmod._translate_codex({"type": "error", "message": "boom"})
        self.assertEqual(events, [("error", "boom", {"native_type": "error"})])


# ─── Tier 1: _Tailer -- malformed lines, truncation caps, plain-format text ─
class TailerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-tailer-")
        self.out_path = os.path.join(self.tmp, "out.jsonl")
        self.log_path = os.path.join(self.tmp, "log.jsonl")
        self._orig_max_events = teamsmod.TEAM_HEADLESS_MAX_EVENTS
        self._orig_max_line = teamsmod.TEAM_HEADLESS_MAX_LINE_BYTES

    def tearDown(self):
        teamsmod.TEAM_HEADLESS_MAX_EVENTS = self._orig_max_events
        teamsmod.TEAM_HEADLESS_MAX_LINE_BYTES = self._orig_max_line
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content: bytes):
        with open(self.out_path, "ab") as f:
            f.write(content)

    def _read_log(self):
        try:
            with open(self.log_path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def test_malformed_line_produces_one_error_event_and_run_completes(self):
        self._write(b'{"type": "system", "subtype": "init"}\n')
        self._write(b'not valid json at all\n')
        self._write(b'{"type": "result", "result": "done"}\n')
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        tailer.poll()
        events = self._read_log()
        error_events = [e for e in events if e["kind"] == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(tailer.final_text(), "done")

    def test_incomplete_trailing_line_is_held_until_newline_arrives(self):
        self._write(b'{"type": "result", "result": "partial')
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        tailer.poll()
        self.assertEqual(self._read_log(), [])  # nothing emitted yet -- no newline seen
        self._write(b'-line"}\n')
        tailer.poll()
        events = self._read_log()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "status")

    def test_unterminated_final_line_gets_one_last_parse_attempt_on_final_poll(self):
        self._write(b'{"type": "result", "result": "no-trailing-newline"}')  # deliberately no \n
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        tailer.poll()
        self.assertEqual(self._read_log(), [])
        tailer.poll(final=True)
        events = self._read_log()
        self.assertEqual(len(events), 1)
        self.assertEqual(tailer.final_text(), "no-trailing-newline")

    def test_unterminated_malformed_final_line_produces_error_event_not_an_exception(self):
        self._write(b'{"type": "result", "result": "broken tail"')  # malformed AND no newline
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        tailer.poll(final=True)  # must not raise
        events = self._read_log()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "error")

    def test_max_events_cap_stops_further_translation_with_one_truncation_notice(self):
        teamsmod.TEAM_HEADLESS_MAX_EVENTS = 3
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        for i in range(10):
            self._write(json.dumps({"type": "system", "subtype": f"n{i}"}).encode() + b"\n")
        tailer.poll()
        events = self._read_log()
        # 3 real events up to the cap, plus the one truncation-notice event
        # itself (which the cap check necessarily lets through) -- nothing
        # further after that.
        self.assertEqual(len(events), 4)
        self.assertEqual(events[-1]["meta"]["native_type"], "_truncation_notice")
        self.assertTrue(tailer.truncated)

    def test_max_line_bytes_cap_stops_translation_of_that_line_with_notice(self):
        teamsmod.TEAM_HEADLESS_MAX_LINE_BYTES = 20
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        self._write(json.dumps({"type": "system", "subtype": "small"}).encode() + b"\n")
        self._write(json.dumps({"type": "system", "subtype": "this one is far too long"}).encode() + b"\n")
        tailer.poll()
        events = self._read_log()
        self.assertTrue(tailer.truncated)
        self.assertEqual(events[-1]["meta"]["native_type"], "_truncation_notice")

    def test_truncation_and_ok_exit_code_are_independent(self):
        # docs/spec.md acceptance criteria: truncation must not affect
        # ok/exit_code -- this test only asserts the tailer-level contract
        # (agent_run()'s own exit_code comes from rc_path, not the tailer).
        teamsmod.TEAM_HEADLESS_MAX_EVENTS = 1
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        self._write(json.dumps({"type": "system", "subtype": "a"}).encode() + b"\n")
        self._write(json.dumps({"type": "result", "result": "would have been the answer"}).encode() + b"\n")
        tailer.poll()
        self.assertTrue(tailer.truncated)
        # The real answer was never translated (cap hit first) -- final_text()
        # falls back to the (empty) assistant-text concatenation, not an
        # exception and not a stale/wrong value.
        self.assertEqual(tailer.final_text(), "")

    def test_plain_format_never_attempts_json_parsing(self):
        self._write(b"not json at all, just aider's own stdout\nsecond line\n")
        tailer = teamsmod._Tailer(self.out_path, "plain", self.log_path, "aider")
        tailer.poll()  # must not raise, must not touch the log at all mid-run
        self.assertEqual(self._read_log(), [])
        text = tailer.final_text()
        self.assertEqual(text, "not json at all, just aider's own stdout\nsecond line\n")
        self.assertEqual(len(self._read_log()), 1)  # final_text() appends exactly one message event

    def test_plain_format_bounded_by_max_line_bytes(self):
        teamsmod.TEAM_HEADLESS_MAX_LINE_BYTES = 10
        self._write(b"this text is definitely longer than ten bytes")
        tailer = teamsmod._Tailer(self.out_path, "plain", self.log_path, "aider")
        text = tailer.final_text()
        self.assertEqual(len(text.encode()), 10)
        self.assertTrue(tailer.truncated)

    def test_session_id_is_captured_from_the_first_event_that_carries_one(self):
        self._write(b'{"type": "system", "subtype": "init", "session_id": "sid-early"}\n')
        self._write(b'{"type": "result", "result": "x", "session_id": "sid-early"}\n')
        tailer = teamsmod._Tailer(self.out_path, "claude-stream-json", self.log_path, "claude")
        tailer.poll()
        self.assertEqual(tailer.session_id, "sid-early")


# ─── Tier 1: agent_run() validation -- nothing spawned on any failure ─────
class AgentRunValidationNoSpawnTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-state-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        _write_engine_file(self.engines_dir, "headless.engine", """\
            LABEL=Headless
            CMD=headless-tool
            HEADLESS_CMD=headless-tool -p {resume}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_RESUME=--resume {session_id}
            """)
        _write_engine_file(self.engines_dir, "notheadless.engine", """\
            LABEL=NotHeadless
            CMD=plain-tool
            """)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)
        _forbid_spawn(self)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_unknown_engine_raises(self):
        with self.assertRaises(ValueError):
            teamsmod.agent_run("no-such-engine", self.workdir, "hi")

    def test_non_headless_engine_raises(self):
        with self.assertRaises(ValueError):
            teamsmod.agent_run("notheadless", self.workdir, "hi")

    def test_missing_workdir_raises(self):
        with self.assertRaises(ValueError):
            teamsmod.agent_run("headless", os.path.join(self.projects_dir, "nope"), "hi")

    def test_workdir_that_is_a_file_not_a_directory_raises(self):
        f = os.path.join(self.projects_dir, "afile")
        open(f, "w").close()
        with self.assertRaises(ValueError):
            teamsmod.agent_run("headless", f, "hi")

    def test_session_id_for_engine_without_resume_support_raises(self):
        _write_engine_file(self.engines_dir, "noresume.engine", """\
            LABEL=NoResume
            CMD=x
            HEADLESS_CMD=x --headless
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        with self.assertRaises(ValueError):
            teamsmod.agent_run("noresume", self.workdir, "hi", session_id="abc")

    def test_oversized_arg_prompt_raises(self):
        big = "x" * (teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            teamsmod.agent_run("headless", self.workdir, big)

    def test_quote_heavy_arg_prompt_raises_value_error_not_os_error_nothing_spawned(self):
        # docs/test-review.md Defect 1 regression, at the agent_run() level:
        # this raw length (30,000 bytes) is well under
        # TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES (65536) -- before the fix this
        # would pass _validate_prompt_size() silently and only blow up much
        # later as an uncaught OSError from subprocess.run(), after rundir
        # had already been created (a leak, since that call sat outside the
        # try/finally that owns cleanup). _forbid_spawn() (see setUp) fails
        # this test if subprocess.run/tmux_has is ever called -- proving the
        # rejection happens before anything is spawned, not after a crash.
        quote_heavy = "'" * 30000
        with self.assertRaises(ValueError):
            teamsmod.agent_run("headless", self.workdir, quote_heavy)
        # And no rundir was ever created for it either.
        headless_root = os.path.join(self.state_dir, "_headless")
        self.assertFalse(os.path.isdir(headless_root) and os.listdir(headless_root))


# ─── Tier 1: agent_run()'s OSError-from-spawn defense-in-depth, and the
# rundir-cleanup-on-any-failure contract (docs/test-review.md Defect 1) ───
class AgentRunOSErrorHandlingTests(unittest.TestCase):
    """subprocess.run() itself is mocked to raise OSError (standing in for
    "Argument list too long" or any other spawn-time OS failure) -- proves
    agent_run() (a) never lets that OSError escape, returning a well-formed
    ok=False result instead, and (b) still cleans up `rundir` even though
    the failure happens deep inside the function, well past validation."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-state-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        _write_engine_file(self.engines_dir, "headless.engine", """\
            LABEL=Headless
            CMD=headless-tool
            HEADLESS_CMD=headless-tool -p
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_os_error_from_new_session_spawn_returns_clean_result_and_cleans_up_rundir(self):
        orig_run = subprocess.run

        def _raising_run(cmd, **kwargs):
            if "new-session" in cmd:
                raise OSError(7, "Argument list too long")
            return orig_run(cmd, **kwargs)

        subprocess.run = _raising_run
        orig_tmux_has = teamsmod.tmux_has
        teamsmod.tmux_has = lambda session: False  # nothing was ever really created
        try:
            result = teamsmod.agent_run("headless", self.workdir, "hello", timeout=5)
        finally:
            subprocess.run = orig_run
            teamsmod.tmux_has = orig_tmux_has

        self.assertFalse(result["ok"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(result["cancelled"])
        self.assertIn("failed to start headless session", result["error"])
        headless_root = os.path.join(self.state_dir, "_headless")
        self.assertFalse(os.path.isdir(headless_root) and os.listdir(headless_root),
                         "rundir leaked after an OSError from the new-session spawn")


# ─── Tier 2: real tmux, test-authored helper processes, no sudo ──────────
def _write_script(dirpath, filename, body):
    path = os.path.join(dirpath, filename)
    with open(path, "w") as f:
        f.write(textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmuxHeadlessTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-state-")
        self.scripts_dir = tempfile.mkdtemp(prefix="switchboard-scripts-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        self._orig_poll = teamsmod.TEAM_HEADLESS_POLL_SECONDS
        self._orig_grace = teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        teamsmod.TEAM_HEADLESS_POLL_SECONDS = 0.1
        teamsmod.TEAM_HEADLESS_KILL_GRACE_SECONDS = 1.5
        _patch_tmux(self, ["tmux"])
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
        # Belt-and-braces: nothing in this test class should leave a
        # switchboard-headless-* session running, but sweep just in case a
        # failure left one behind.
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        for name in r.stdout.splitlines():
            if name.startswith("switchboard-headless-"):
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def _no_leftover_sessions(self):
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        leftover = [n for n in r.stdout.splitlines() if n.startswith("switchboard-headless-")]
        self.assertEqual(leftover, [], f"leftover tmux sessions: {leftover}")

    def _no_leftover_rundir(self):
        headless_root = os.path.join(self.state_dir, "_headless")
        if os.path.isdir(headless_root):
            self.assertEqual(os.listdir(headless_root), [])

    def test_success_stream_end_to_end(self):
        script = _write_script(self.scripts_dir, "cat_fixture.py", f"""\
            #!/usr/bin/env python3
            import sys
            with open({_fixture("claude_stream.jsonl")!r}) as f:
                sys.stdout.write(f.read())
            """)
        _write_engine_file(self.engines_dir, "fake.engine", f"""\
            LABEL=Fake
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=claude-stream-json
            HEADLESS_PROMPT=arg
            """)
        log_path = os.path.join(self.state_dir, "run.jsonl")
        result = teamsmod.agent_run("fake", self.workdir, "hello", log_path=log_path, timeout=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["text"], "ok")
        self.assertEqual(result["session_id"], "ec4f3b1b-8ef0-4c51-8740-7349b63d8ac5")
        self.assertFalse(result["cancelled"])
        self.assertGreater(result["event_count"], 0)
        self.assertTrue(os.path.isfile(log_path))
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_shape_crash_line_through_the_real_agent_run_path_does_not_raise(self):
        # docs/test-review.md round 3, Finding 1: the reviewer's own repro
        # was through the *full* real-tmux agent_run() path, not just the
        # translator/tailer directly -- a synthetic engine emits one
        # syntactically-valid-but-wrong-shape line among otherwise-normal
        # ones. agent_run() must not raise, and the run must complete with
        # the good events still translated.
        script = _write_script(self.scripts_dir, "bad_shape.py", """\
            #!/usr/bin/env python3
            import sys
            print('{"type": "system", "subtype": "init", "session_id": "s1"}')
            print('{"type": "assistant", "message": "not an object"}')
            print('{"type": "result", "result": "done"}')
            """)
        _write_engine_file(self.engines_dir, "badshape.engine", f"""\
            LABEL=BadShape
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=claude-stream-json
            HEADLESS_PROMPT=arg
            """)
        result = teamsmod.agent_run("badshape", self.workdir, "hello", timeout=30)  # must not raise
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["text"], "done")
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    # ── docs/test-review.md round 2, Defect 2 regression ──────────────
    # Real tmux, real ordinary plain-text prompt in the 20KB range -- NOT
    # quote-heavy (that's Defect 1's regression, in a different test class,
    # and passes even while Defect 2 is fully live, per the reviewer's own
    # note on how it survived round 1). tmux's own client->server
    # command-passing protocol limit (empirically ~16,318 bytes for a
    # `_build_script()`-shaped command against this box's stock tmux 3.5a)
    # used to reject this silently ("headless session failed to start")
    # even though the prompt was comfortably inside the documented/
    # validated 65536-byte arg-mode cap -- the round-2 fix (script written
    # to RUNDIR/run.sh, run via `bash -l <path>` instead of inline
    # `bash -lc <script>`) makes the tmux command line itself small and
    # constant-length, removing that ceiling entirely.
    def test_ordinary_20kb_plain_text_prompt_actually_runs_arg_mode(self):
        script = _write_script(self.scripts_dir, "dump_arg.py", """\
            #!/usr/bin/env python3
            import sys
            sys.stdout.write(sys.argv[1])
            """)
        _write_engine_file(self.engines_dir, "dumparg.engine", f"""\
            LABEL=DumpArg
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        prompt = "The quick brown fox jumps over the lazy dog. " * 445  # ~20,025 bytes, no quotes/metacharacters
        self.assertGreater(len(prompt.encode()), 20000)
        self.assertLess(len(prompt.encode()), teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES)
        result = teamsmod.agent_run("dumparg", self.workdir, prompt, timeout=30)
        self.assertTrue(result["ok"], f"expected ok=True, got error={result['error']!r}")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["text"], prompt)  # round-tripped byte-for-byte through argv
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_prompt_just_over_tmuxs_old_16kb_limit_still_runs(self):
        # The exact boundary the old (pre-round-2) behavior broke at --
        # comfortably below the round-1 65536-byte cap, comfortably above
        # tmux's own ~16,318-byte internal limit.
        script = _write_script(self.scripts_dir, "dump_arg2.py", """\
            #!/usr/bin/env python3
            import sys
            sys.stdout.write(sys.argv[1])
            """)
        _write_engine_file(self.engines_dir, "dumparg2.engine", f"""\
            LABEL=DumpArg2
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        prompt = "x" * 17000
        result = teamsmod.agent_run("dumparg2", self.workdir, prompt, timeout=30)
        self.assertTrue(result["ok"], f"expected ok=True, got error={result['error']!r}")
        self.assertEqual(result["text"], prompt)
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_stdin_prompt_delivery_and_plain_format_text(self):
        script = _write_script(self.scripts_dir, "echo_stdin.py", """\
            #!/usr/bin/env python3
            import sys
            sys.stdout.write(sys.stdin.read())
            """)
        _write_engine_file(self.engines_dir, "fakestdin.engine", f"""\
            LABEL=FakeStdin
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=stdin
            """)
        result = teamsmod.agent_run("fakestdin", self.workdir, "echo me back please", timeout=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "echo me back please")
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_sigterm_cleanly_stops_and_is_reported_as_cancelled(self):
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
        result = teamsmod.agent_run("hanger", self.workdir, "go", timeout=0.5)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "timeout")
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 128 + 15)  # SIGTERM, confirmed live for claude -p too
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_sigterm_ignored_escalates_to_sigkill(self):
        script = _write_script(self.scripts_dir, "hang_ignore_term.py", """\
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
        result = teamsmod.agent_run("stubborn", self.workdir, "go", timeout=0.5)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "timeout")
        self.assertEqual(result["exit_code"], 128 + 9)  # escalated all the way to SIGKILL
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_forced_session_kill_mid_run_is_classified_as_cancelled_not_success(self):
        script = _write_script(self.scripts_dir, "slow.py", """\
            #!/usr/bin/env python3
            import time
            time.sleep(20)
            print("should never get here")
            """)
        _write_engine_file(self.engines_dir, "slowpoke.engine", f"""\
            LABEL=Slowpoke
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        fixed_run_id = "fixedid-forcekill"
        orig_run_id = teamsmod._run_id
        teamsmod._run_id = lambda: fixed_run_id
        self.addCleanup(lambda: setattr(teamsmod, "_run_id", orig_run_id))
        session = f"switchboard-headless-{fixed_run_id}"

        results = {}

        def _call():
            results["r"] = teamsmod.agent_run("slowpoke", self.workdir, "go", timeout=30)

        t = threading.Thread(target=_call)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline and not appmod.tmux_has(session):
            time.sleep(0.05)
        self.assertTrue(appmod.tmux_has(session), "session never appeared")
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
        t.join(timeout=15)
        self.assertFalse(t.is_alive())
        result = results["r"]
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["ok"])
        self.assertIsNone(result["exit_code"])
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_externally_sent_sigterm_is_classified_as_cancelled_external(self):
        # Not agent_run()'s own timeout escalation -- something else (the
        # test, standing in for a future "stop team" action or an
        # operator's own `kill`) sends SIGTERM directly to the engine PID.
        # docs/spec.md §4: classified identically to a self-initiated
        # cancellation, just with cancel_reason="external".
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
        fixed_run_id = "fixedid-external-term"
        orig_run_id = teamsmod._run_id
        teamsmod._run_id = lambda: fixed_run_id
        self.addCleanup(lambda: setattr(teamsmod, "_run_id", orig_run_id))
        session = f"switchboard-headless-{fixed_run_id}"
        rundir = os.path.join(self.state_dir, "_headless", fixed_run_id)

        results = {}

        def _call():
            results["r"] = teamsmod.agent_run("hanger2", self.workdir, "go", timeout=30)

        t = threading.Thread(target=_call)
        t.start()
        pid_path = os.path.join(rundir, "out.pid")
        deadline = time.time() + 10
        pid = None
        while time.time() < deadline:
            if os.path.isfile(pid_path):
                with open(pid_path) as f:
                    s = f.read().strip()
                if s:
                    pid = int(s)
                    break
            time.sleep(0.05)
        self.assertIsNotNone(pid, "engine pid never appeared")
        os.kill(pid, 15)  # SIGTERM, sent directly -- not via agent_run()'s own escalation
        t.join(timeout=15)
        self.assertFalse(t.is_alive())
        result = results["r"]
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cancel_reason"], "external")
        self.assertEqual(result["exit_code"], 128 + 15)
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_resume_raises_before_any_tmux_session_for_no_resume_engine(self):
        script = _write_script(self.scripts_dir, "noop.py", """\
            #!/usr/bin/env python3
            print("hi")
            """)
        _write_engine_file(self.engines_dir, "noresume2.engine", f"""\
            LABEL=NoResume2
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        with self.assertRaises(ValueError):
            teamsmod.agent_run("noresume2", self.workdir, "go", session_id="abc", timeout=5)
        self._no_leftover_sessions()

    def test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask(self):
        # docs/test-review.md round 3, Finding 3: run.sh (every mode, since
        # round 2) and the prompt file (stdin/file modes, since round 1)
        # are written by SVC_USER but must be *read* by RUN_USER via the
        # tmux pane's bash process. Without an explicit chmod, readability
        # depends entirely on SVC_USER's ambient umask -- under a strict
        # umask (0o077), open(path, "w") produces mode 0o600, unreadable by
        # anyone but the owner. This sets that exact umask for the whole
        # process (umask is process-wide, so it applies to the background
        # thread agent_run() runs in too) and confirms both files still end
        # up world-readable regardless, via the explicit os.chmod() calls.
        script = _write_script(self.scripts_dir, "slow4.py", """\
            #!/usr/bin/env python3
            import time
            time.sleep(5)
            """)
        _write_engine_file(self.engines_dir, "umasktest.engine", f"""\
            LABEL=UmaskTest
            CMD=unused
            HEADLESS_CMD=python3 {script} {{prompt_file}}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=file
            """)
        fixed_run_id = "fixedid-umasktest"
        orig_run_id = teamsmod._run_id
        teamsmod._run_id = lambda: fixed_run_id
        self.addCleanup(lambda: setattr(teamsmod, "_run_id", orig_run_id))
        rundir = os.path.join(self.state_dir, "_headless", fixed_run_id)
        script_path = os.path.join(rundir, "run.sh")
        prompt_path = os.path.join(rundir, "prompt")

        old_umask = os.umask(0o077)
        self.addCleanup(lambda: os.umask(old_umask))

        results = {}

        def _call():
            results["r"] = teamsmod.agent_run("umasktest", self.workdir, "go", timeout=30)

        t = threading.Thread(target=_call)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline and not (os.path.isfile(script_path) and os.path.isfile(prompt_path)):
            time.sleep(0.05)
        self.assertTrue(os.path.isfile(script_path), "run.sh never appeared")
        self.assertTrue(os.path.isfile(prompt_path), "prompt file never appeared")
        script_mode = stat.S_IMODE(os.stat(script_path).st_mode)
        prompt_mode = stat.S_IMODE(os.stat(prompt_path).st_mode)
        self.assertTrue(script_mode & stat.S_IROTH, f"run.sh not world-readable: {oct(script_mode)}")
        self.assertTrue(prompt_mode & stat.S_IROTH, f"prompt file not world-readable: {oct(prompt_mode)}")
        t.join(timeout=15)
        self.assertFalse(t.is_alive())
        self.assertTrue(results["r"]["ok"])  # the run itself succeeded too, not just the mode check
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_permission_denied_state_file_returns_clean_error_not_a_hang(self):
        # docs/test-review.md item 13: verified by hand only, zero automated
        # coverage before this test. chmod 000 on out.jsonl mid-run (as a
        # non-root user, which this test suite always runs as -- chmod 000
        # really does deny even the owner's own open()) reproduces the
        # "RUN_USER's umask is unusually strict" edge case from
        # docs/spec.md "Edge cases" for real, not simulated.
        script = _write_script(self.scripts_dir, "slow3.py", """\
            #!/usr/bin/env python3
            import time
            time.sleep(5)
            print("should not get this far in the test")
            """)
        _write_engine_file(self.engines_dir, "permtest.engine", f"""\
            LABEL=PermTest
            CMD=unused
            HEADLESS_CMD=python3 {script}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        fixed_run_id = "fixedid-permtest"
        orig_run_id = teamsmod._run_id
        teamsmod._run_id = lambda: fixed_run_id
        self.addCleanup(lambda: setattr(teamsmod, "_run_id", orig_run_id))
        out_path = os.path.join(self.state_dir, "_headless", fixed_run_id, "out.jsonl")

        results = {}

        def _call():
            results["r"] = teamsmod.agent_run("permtest", self.workdir, "go", timeout=30)

        t = threading.Thread(target=_call)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline and not os.path.isfile(out_path):
            time.sleep(0.05)
        self.assertTrue(os.path.isfile(out_path), "out.jsonl never appeared")
        os.chmod(out_path, 0o000)
        t.join(timeout=15)
        self.assertFalse(t.is_alive(), "agent_run() hung instead of returning a clean error")
        result = results["r"]
        self.assertFalse(result["ok"])
        self.assertFalse(result["cancelled"])
        self.assertIsNotNone(result["error"])
        self.assertIn("permission denied", result["error"])
        self.assertIn("umask", result["error"])
        self._no_leftover_sessions()
        self._no_leftover_rundir()

    def test_sweep_removes_aged_dir_with_no_live_session(self):
        stale_run_id = "stale-no-session"
        stale_dir = os.path.join(self.state_dir, "_headless", stale_run_id)
        os.makedirs(stale_dir)
        old = time.time() - teamsmod.TEAM_HEADLESS_STALE_RUN_TTL_SECONDS - 100
        os.utime(stale_dir, (old, old))
        teamsmod._sweep_stale_runs()
        self.assertFalse(os.path.isdir(stale_dir))

    def test_sweep_leaves_a_fresh_dir_untouched_and_spawns_nothing_for_it(self):
        fresh_run_id = "fresh-recent"
        fresh_dir = os.path.join(self.state_dir, "_headless", fresh_run_id)
        os.makedirs(fresh_dir)
        orig_run = subprocess.run
        called = []
        subprocess.run = lambda *a, **kw: called.append(a) or orig_run(*a, **kw)
        try:
            teamsmod._sweep_stale_runs()
        finally:
            subprocess.run = orig_run
        self.assertTrue(os.path.isdir(fresh_dir))
        self.assertEqual(called, [])  # age < TTL -- never even checked tmux for it

    def test_sweep_kills_live_session_for_an_aged_dir_and_removes_it(self):
        stale_run_id = "stale-with-live-session"
        stale_dir = os.path.join(self.state_dir, "_headless", stale_run_id)
        os.makedirs(stale_dir)
        old = time.time() - teamsmod.TEAM_HEADLESS_STALE_RUN_TTL_SECONDS - 100
        os.utime(stale_dir, (old, old))
        session = f"switchboard-headless-{stale_run_id}"
        subprocess.run(["tmux", "new-session", "-d", "-s", session, "sleep", "60"])
        self.addCleanup(lambda: subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True))
        self.assertTrue(appmod.tmux_has(session))
        teamsmod._sweep_stale_runs()
        # The sweep's own defensive kill-session tears down even a live,
        # unrelated session matching the name pattern -- and, since the kill
        # succeeded, the now-session-less aged directory is removed too.
        self.assertFalse(appmod.tmux_has(session))
        self.assertFalse(os.path.isdir(stale_dir))


if __name__ == "__main__":
    unittest.main()
