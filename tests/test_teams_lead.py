"""
Tests for the roster + lead loop (backlog item 6c -- docs/spec.md). Mirrors
tests/test_teams_headless.py's own three-tier structure (explicitly reused
per that file's header comment, and per docs/spec.md's own "Test plan"):

Tier 1 (bulk of this file) -- pure unit, no subprocess, no tmux, no network:
_lead_tier_for_engine()/roster(), _lead_tools()'s enum, _validate_lead_
action() against every §9 category, _parse_tier1_action()/_parse_tier3_
action(), _round_context()/_assemble_prompt()'s final cap against a
pathological every-sub-budget-maxed-at-once case, the tier-1 transport-
retry wrapper against a monkeypatched _lead_tier1_call()/urlopen,
_write_ask_user_inbox()'s shape/truncation, persistence/crash-recovery
reconstruction, and team_step()/team_run() driven with _call_lead()/
agent_run() monkeypatched out (no tmux needed to test the LOOP's own
bookkeeping).

Tier 2 -- real tmux, test-authored helper processes, no real engine CLI, no
sudo beyond what RealTmuxHeadlessTests already exercises: the {schema}
substitution into a real (test-fixture) .engine file's HEADLESS_CMD,
agent_run(..., schema=...)'s rundir/schema.json write/permissions/cleanup,
and the tier-3 stand-in fixtures (tests/fixtures/headless/tier3_stub_*.sh)
driving a full team_run() end to end via the REAL CLI as a SEPARATE
subprocess invocation for the ask_user/team-resolve acceptance criterion.

Tier 3 (manual, developer stage, not in this file) -- the real tier-1
Ollama run and whichever of claude/codex is available, both recorded in
docs/implementation.md with actual commands/output.

Run with:
    python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures", "headless")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-lead-test-")
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


def _write_script(dirpath, filename, body):
    path = os.path.join(dirpath, filename)
    with open(path, "w") as f:
        f.write(textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def _fixture(name):
    return os.path.join(FIXTURES_DIR, name)


def _patch_tmux(testcase, argv):
    orig_app, orig_teams = appmod.TMUX, teamsmod.TMUX
    appmod.TMUX = argv
    teamsmod.TMUX = argv

    def _restore():
        appmod.TMUX = orig_app
        teamsmod.TMUX = orig_teams
    testcase.addCleanup(_restore)


def _forbid_spawn(testcase):
    orig_run, orig_has = subprocess.run, teamsmod.tmux_has

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


def _dummy_agent_result(**overrides):
    base = {"ok": True, "text": "", "session_id": None, "exit_code": 0, "cancelled": False,
           "cancel_reason": None, "event_count": 0, "truncated": False, "log_path": None,
           "stderr_tail": "", "error": None}
    base.update(overrides)
    return base


class _StateTestCase(unittest.TestCase):
    """Common setUp/tearDown for tests that build in-memory state dicts and
    persist them under a scratch TEAM_STATE_DIR/scratch project workdir --
    no tmux, no real engine, used throughout the Tier 1 section below."""

    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-lead-proj-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lead-state-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.projdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _lead(self, tier=1, kind="ollama", name="m"):
        return {"kind": kind, "name": name, "tier": tier}


# ─── Tier 1: _lead_tier_for_engine() / roster() ───────────────────────────
class LeadTierForEngineTests(unittest.TestCase):
    def _engine(self, **kw):
        defaults = dict(name="e", label="E", cmd="e {name}", url_regex=None, startup=[],
                        headless_cmd="e -p {resume} {schema}", headless_format="plain",
                        headless_prompt="arg", headless_resume=None,
                        headless_lead_format=None, headless_schema_flag=None)
        defaults.update(kw)
        return appmod.Engine(defaults["name"], defaults["label"], defaults["cmd"],
                             defaults["url_regex"], defaults["startup"], defaults["headless_cmd"],
                             defaults["headless_format"], defaults["headless_prompt"],
                             defaults["headless_resume"], defaults["headless_lead_format"],
                             defaults["headless_schema_flag"])

    def test_no_hints_defaults_to_tier3(self):
        self.assertEqual(teamsmod._lead_tier_for_engine(self._engine()), 3)

    def test_schema_flag_present_auto_detects_tier2(self):
        e = self._engine(headless_schema_flag="--json-schema {schema}")
        self.assertEqual(teamsmod._lead_tier_for_engine(e), 2)

    def test_lead_format_prose_overrides_tier3_despite_schema_flag(self):
        e = self._engine(headless_schema_flag="--json-schema {schema}", headless_lead_format="prose")
        self.assertEqual(teamsmod._lead_tier_for_engine(e), 3)

    def test_lead_format_schema_overrides_tier2_without_schema_flag(self):
        e = self._engine(headless_lead_format="schema")
        self.assertEqual(teamsmod._lead_tier_for_engine(e), 2)

    def test_unrecognized_lead_format_falls_through_to_auto_detection(self):
        e = self._engine(headless_lead_format="bogus", headless_schema_flag="--x {schema}")
        self.assertEqual(teamsmod._lead_tier_for_engine(e), 2)
        e2 = self._engine(headless_lead_format="bogus")
        self.assertEqual(teamsmod._lead_tier_for_engine(e2), 3)


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-engines-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_base = teamsmod.TEAM_LLM_BASE_URL
        self._orig_model = teamsmod.TEAM_LLM_MODEL
        appmod.ENGINES_DIR = self.engines_dir

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_LLM_BASE_URL = self._orig_base
        teamsmod.TEAM_LLM_MODEL = self._orig_model
        shutil.rmtree(self.engines_dir, ignore_errors=True)

    def test_headless_disabled_engine_absent_from_roster(self):
        _write_engine_file(self.engines_dir, "plain.engine", "LABEL=Plain\nCMD=plain\n")
        names = [e["name"] for e in teamsmod.roster()]
        self.assertNotIn("plain", names)

    def test_headless_engine_tiered_correctly(self):
        _write_engine_file(self.engines_dir, "a.engine", """\
            LABEL=A
            CMD=unused
            HEADLESS_CMD=a -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema {schema}
            """)
        entry = next(e for e in teamsmod.roster() if e["name"] == "a")
        self.assertEqual(entry, {"name": "a", "kind": "engine", "label": "A", "tier": 2,
                                 "delegate_capable": True, "schema_flag_error": None})

    def test_schema_flag_config_error_surfaced_at_roster_build_time(self):
        # docs/spec.md "Correction: {schema} is inline for Claude, a file
        # for Codex" -- a HEADLESS_SCHEMA_FLAG declaring neither {schema}
        # nor {schema_file} must be reported here, not only at the first
        # tier-2 lead call.
        _write_engine_file(self.engines_dir, "broken.engine", """\
            LABEL=Broken
            CMD=unused
            HEADLESS_CMD=broken -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema literally-hardcoded-no-placeholder
            """)
        entry = next(e for e in teamsmod.roster() if e["name"] == "broken")
        self.assertEqual(entry["tier"], 2)  # still auto-detects tier 2 (flag is present)
        self.assertIsNotNone(entry["schema_flag_error"])
        self.assertIn("neither", entry["schema_flag_error"])

    def test_lead_format_override_both_directions_visible_in_roster(self):
        _write_engine_file(self.engines_dir, "b.engine", """\
            LABEL=B
            CMD=unused
            HEADLESS_CMD=b -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema {schema}
            HEADLESS_LEAD_FORMAT=prose
            """)
        _write_engine_file(self.engines_dir, "c.engine", """\
            LABEL=C
            CMD=unused
            HEADLESS_CMD=c -p {resume}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_LEAD_FORMAT=schema
            """)
        r = teamsmod.roster()
        self.assertEqual(next(e for e in r if e["name"] == "b")["tier"], 3)
        self.assertEqual(next(e for e in r if e["name"] == "c")["tier"], 2)

    def test_ollama_entry_present_only_when_both_env_vars_set(self):
        teamsmod.TEAM_LLM_BASE_URL, teamsmod.TEAM_LLM_MODEL = None, ""
        self.assertFalse(any(e["kind"] == "ollama" for e in teamsmod.roster()))
        teamsmod.TEAM_LLM_BASE_URL, teamsmod.TEAM_LLM_MODEL = "http://x:11434/v1", ""
        self.assertFalse(any(e["kind"] == "ollama" for e in teamsmod.roster()))
        teamsmod.TEAM_LLM_BASE_URL, teamsmod.TEAM_LLM_MODEL = "http://x:11434/v1", "qwen3:8b"
        entry = next(e for e in teamsmod.roster() if e["kind"] == "ollama")
        self.assertEqual(entry, {"name": "qwen3:8b", "kind": "ollama", "label": "qwen3:8b",
                                 "tier": 1, "delegate_capable": False})

    def test_cli_roster_subcommand_prints_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = teamsmod.main(["roster"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), teamsmod.roster())


# ─── Tier 1: {schema}/{schema_file} two-placeholder design ─────────────────
# (docs/spec.md "Correction: {schema} is inline for Claude, a file for
# Codex" -- found by actually running a real, logged-in `claude` CLI, which
# rejected a file path where it wanted the schema's JSON text inline).
class SchemaPlaceholderKindTests(unittest.TestCase):
    def test_file_placeholder(self):
        self.assertEqual(teamsmod._schema_placeholder_kind("--output-schema {schema_file}"), "file")

    def test_inline_placeholder(self):
        self.assertEqual(teamsmod._schema_placeholder_kind("--json-schema {schema}"), "inline")

    def test_neither_placeholder_is_none(self):
        self.assertIsNone(teamsmod._schema_placeholder_kind("--schema literally-hardcoded"))

    def test_empty_or_none_is_none(self):
        self.assertIsNone(teamsmod._schema_placeholder_kind(""))
        self.assertIsNone(teamsmod._schema_placeholder_kind(None))

    def test_both_placeholders_prefers_file(self):
        self.assertEqual(teamsmod._schema_placeholder_kind("{schema_file} {schema}"), "file")


class ResolveSchemaFragmentTests(unittest.TestCase):
    def test_file_form_substitutes_path(self):
        frag = teamsmod._resolve_schema_fragment("--output-schema {schema_file}", {"a": 1}, "/tmp/s.json")
        self.assertEqual(frag, "--output-schema /tmp/s.json")

    def test_inline_form_substitutes_shell_quoted_json_text(self):
        schema = {"type": "object", "properties": {"tool": {"type": "string"}}}
        frag = teamsmod._resolve_schema_fragment("--json-schema {schema}", schema, "/tmp/unused.json")
        self.assertTrue(frag.startswith("--json-schema "))
        quoted = frag[len("--json-schema "):]
        # round-trips through shlex as ONE token, and that token is the
        # schema's own JSON text
        (token,) = shlex.split(quoted)
        self.assertEqual(json.loads(token), schema)


class BuildHeadlessArgvSinglePassSubstitutionTests(unittest.TestCase):
    """
    docs/test-review.md Finding #1 (must-fix, sub-spec 6c round 3): three
    sequential str.replace() passes over one shared string let a LATER pass
    rescan (and corrupt) text an EARLIER pass had just inserted. Fixed by
    _substitute_headless_tokens()'s single-pass, simultaneous re.sub().
    Both reviewer-confirmed repros below are permanent regression tests.
    """

    def _engine(self, **kw):
        defaults = dict(name="e", label="E", cmd="e {name}", url_regex=None, startup=[],
                        headless_cmd="e {resume} {schema} --prompt-file {prompt_file}",
                        headless_format="plain", headless_prompt="arg", headless_resume=None,
                        headless_lead_format=None, headless_schema_flag="--json-schema {schema}")
        defaults.update(kw)
        return appmod.Engine(defaults["name"], defaults["label"], defaults["cmd"],
                             defaults["url_regex"], defaults["startup"], defaults["headless_cmd"],
                             defaults["headless_format"], defaults["headless_prompt"],
                             defaults["headless_resume"], defaults["headless_lead_format"],
                             defaults["headless_schema_flag"])

    def test_schema_text_containing_literal_prompt_file_token_is_not_rewritten(self):
        # Repro #1: HEADLESS_PROMPT=file + HEADLESS_SCHEMA_FLAG -- the only
        # reachable combination with a real risk, and the one no shipped
        # engine (nor, before this test, any test) exercised.
        e = self._engine(headless_prompt="file")
        schema = {"type": "object", "properties": {
            "p": {"type": "string", "description": "see {prompt_file}"}}}
        argv = teamsmod._build_headless_argv(e, prompt="ignored", session_id=None,
                                             prompt_path="/run/abc/prompt.txt",
                                             schema=schema, schema_path="/run/abc/schema.json")
        schema_arg = argv[argv.index("--json-schema") + 1]
        parsed = json.loads(schema_arg)
        self.assertEqual(parsed, schema)  # byte-for-byte round trip, untouched
        self.assertIn("{prompt_file}", parsed["properties"]["p"]["description"])
        self.assertNotIn("/run/abc/prompt.txt", schema_arg)  # never leaked into the schema
        # the real {prompt_file} substitution still happened, once, correctly
        self.assertIn("/run/abc/prompt.txt", argv)

    def test_resume_fragment_containing_literal_schema_token_is_not_rewritten(self):
        # Repro #2: a session_id (sourced from an engine CLI's own JSON
        # output -- semi-trusted, not developer-controlled) containing a
        # literal "{schema}" substring must not be rescanned/corrupted by
        # the later {schema} substitution.
        e = self._engine(headless_prompt="arg", headless_resume="--resume {session_id}")
        session_id = "weird-{schema}-id"
        argv = teamsmod._build_headless_argv(e, prompt="hello", session_id=session_id,
                                             prompt_path=None, schema={"a": 1},
                                             schema_path="/run/x/schema.json")
        self.assertIn(session_id, argv)  # exact, unmangled session id present
        resume_idx = argv.index("--resume")
        self.assertEqual(argv[resume_idx + 1], session_id)
        # the real {schema} substitution still happened, once, correctly,
        # as its OWN separate argv element
        schema_arg = argv[argv.index("--json-schema") + 1]
        self.assertEqual(json.loads(schema_arg), {"a": 1})

    def test_prompt_file_token_left_untouched_when_not_in_file_mode(self):
        # "an engine that is arg-prompted must still leave a literal
        # {prompt_file} untouched... rather than the single-pass mapping
        # silently substituting it in a mode where it was never valid"
        e = self._engine(headless_cmd="e --note {prompt_file} --resume {resume}",
                         headless_prompt="arg", headless_schema_flag=None)
        argv = teamsmod._build_headless_argv(e, prompt="hi", session_id=None, prompt_path=None)
        self.assertIn("{prompt_file}", argv)

    def test_ordinary_case_all_three_tokens_substituted_correctly(self):
        e = self._engine(headless_prompt="file", headless_resume="--resume {session_id}")
        argv = teamsmod._build_headless_argv(
            e, prompt="ignored", session_id="sid-1", prompt_path="/run/y/prompt",
            schema={"tool": "x"}, schema_path="/run/y/schema.json")
        self.assertNotIn("{resume}", argv)
        self.assertNotIn("{schema}", argv)
        self.assertNotIn("{prompt_file}", argv)
        self.assertIn("/run/y/prompt", argv)
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), {"tool": "x"})


class SubstituteHeadlessTokensPureTests(unittest.TestCase):
    def test_single_pass_does_not_rescan_replacement_text(self):
        # {resume}'s own replacement value is deliberately the literal text
        # "{schema}" -- if this were rescanned (the sequential-replace bug),
        # it would go on to be replaced by {schema}'s own mapped value
        # ("literal-schema"). A correct single pass leaves it as "{schema}".
        result = teamsmod._substitute_headless_tokens(
            "{resume} {schema}", {"{resume}": "{schema}", "{schema}": "literal-schema"})
        self.assertEqual(result, "{schema} literal-schema")

    def test_absent_key_leaves_token_untouched(self):
        result = teamsmod._substitute_headless_tokens(
            "{resume} {prompt_file}", {"{resume}": "R"})
        self.assertEqual(result, "R {prompt_file}")

    def test_no_tokens_present_is_a_no_op(self):
        result = teamsmod._substitute_headless_tokens("plain command", {"{resume}": "R"})
        self.assertEqual(result, "plain command")


class ValidatePromptSizeSchemaInteractionTests(unittest.TestCase):
    def test_inline_schema_summed_with_prompt_in_arg_mode(self):
        # docs/spec.md's own correction: "the inline form puts the schema
        # in argv alongside the prompt, so the check must account for
        # both, not the prompt alone."
        orig_cap = teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES
        teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES = 100
        try:
            # each alone is fine, but together they exceed the 100-byte cap
            prompt = "x" * 60
            schema_text = "y" * 60
            with self.assertRaises(ValueError):
                teamsmod._validate_prompt_size("arg", prompt, schema_text)
            # prompt alone (no schema) still fits
            teamsmod._validate_prompt_size("arg", prompt, None)
        finally:
            teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES = orig_cap

    def test_inline_schema_checked_independently_in_stdin_mode(self):
        orig_cap = teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES
        teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES = 50
        try:
            # prompt is huge but stdin mode has no argv concern for it --
            # only the inline schema's own size matters here
            huge_prompt = "x" * 100000
            teamsmod._validate_prompt_size("stdin", huge_prompt, None)  # fine, no schema
            with self.assertRaises(ValueError):
                teamsmod._validate_prompt_size("stdin", huge_prompt, "y" * 60)
        finally:
            teamsmod.TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES = orig_cap

    def test_no_schema_byte_for_byte_unchanged_behavior(self):
        # existing 6a callers never pass schema_text -- must be identical
        # to the pre-6c-correction behavior.
        teamsmod._validate_prompt_size("arg", "a normal prompt")
        teamsmod._validate_prompt_size("stdin", "a normal prompt")


class SchemaFlagConfigErrorAgentRunTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-badschema-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-badschema-projects-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        appmod.ENGINES_DIR = self.engines_dir
        _write_engine_file(self.engines_dir, "broken.engine", """\
            LABEL=Broken
            CMD=unused
            HEADLESS_CMD=broken -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema literally-hardcoded-no-placeholder
            """)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def test_neither_placeholder_raises_before_spawning(self):
        _forbid_spawn(self)
        with self.assertRaises(ValueError):
            teamsmod.agent_run("broken", self.workdir, "hi", schema={"type": "object"}, timeout=5)


class CliTeamStartSchemaConfigErrorTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-cli-badschema-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-cli-badschema-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lead-cli-badschema-state-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        _write_engine_file(self.engines_dir, "broken.engine", """\
            LABEL=Broken
            CMD=unused
            HEADLESS_CMD=broken -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema literally-hardcoded-no-placeholder
            """)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_team_start_rejects_misconfigured_tier2_lead_before_running(self):
        _forbid_spawn(self)
        rc = teamsmod.main(["team-start", self.workdir, "--task", "x",
                           "--lead", "broken", "--members", ""])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.isdir(os.path.join(self.state_dir, "leads")))


# ─── Tier 1: _lead_tools() ─────────────────────────────────────────────────
class LeadToolsTests(unittest.TestCase):
    def test_enum_reflects_team_members(self):
        tools = teamsmod._lead_tools(["claude", "aider"])
        names = [t["function"]["name"] for t in tools]
        self.assertEqual(names, ["delegate", "fact_check", "ask_user", "board_read",
                                 "board_write", "finish"])
        delegate = tools[0]
        self.assertEqual(delegate["function"]["parameters"]["properties"]["agent"]["enum"],
                         ["claude", "aider"])

    def test_empty_members_gives_empty_enum(self):
        tools = teamsmod._lead_tools([])
        delegate = tools[0]
        self.assertEqual(delegate["function"]["parameters"]["properties"]["agent"]["enum"], [])

    # ─── backlog item 7 part 1: board_read/board_write native schema ──────
    def test_board_read_args_all_optional(self):
        tools = teamsmod._lead_tools(["claude"])
        board_read = next(t for t in tools if t["function"]["name"] == "board_read")
        self.assertEqual(board_read["function"]["parameters"]["required"], [])
        self.assertEqual(set(board_read["function"]["parameters"]["properties"]), {"ref", "query"})

    def test_board_write_verb_enum_and_required_args(self):
        tools = teamsmod._lead_tools(["claude"])
        board_write = next(t for t in tools if t["function"]["name"] == "board_write")
        params = board_write["function"]["parameters"]
        self.assertEqual(set(params["required"]), {"verb", "ref", "value"})
        self.assertEqual(set(params["properties"]["verb"]["enum"]),
                         {"set_status", "amend_description", "append_comment"})


# ─── Tier 1: _validate_lead_action() -- every §9 category ─────────────────
class ValidateLeadActionTests(unittest.TestCase):
    def test_none_raw_is_not_a_dict(self):
        r = teamsmod._validate_lead_action(None, ["claude"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "not_a_dict"))

    def test_list_raw_is_not_a_dict(self):
        r = teamsmod._validate_lead_action([1, 2], ["claude"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "not_a_dict"))

    def test_unknown_tool_name(self):
        r = teamsmod._validate_lead_action({"tool": "delete_repo", "args": {}}, ["claude"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "unknown_tool"))

    def test_args_not_a_dict(self):
        r = teamsmod._validate_lead_action({"tool": "fact_check", "args": ["x"]}, ["claude"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "not_a_dict"))

    def test_args_missing_entirely_treated_as_not_a_dict(self):
        r = teamsmod._validate_lead_action({"tool": "finish"}, ["claude"], 1)
        self.assertEqual((r["ok"], r["reason"]), (False, "not_a_dict"))

    def test_missing_required_arg(self):
        r = teamsmod._validate_lead_action({"tool": "delegate", "args": {"agent": "claude"}},
                                           ["claude"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "missing_args"))

    def test_wrong_type_arg(self):
        r = teamsmod._validate_lead_action(
            {"tool": "delegate", "args": {"agent": "claude", "task": 12345}}, ["claude"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_ask_user_options_not_a_list_is_wrong_type(self):
        r = teamsmod._validate_lead_action(
            {"tool": "ask_user", "args": {"question": "q?", "options": "not a list"}}, [], 1)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_ask_user_option_missing_label_is_wrong_type(self):
        r = teamsmod._validate_lead_action(
            {"tool": "ask_user", "args": {"question": "q?",
                                          "options": [{"description": "no label"}]}}, [], 1)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_ask_user_multi_select_wrong_type(self):
        r = teamsmod._validate_lead_action(
            {"tool": "ask_user", "args": {"question": "q?", "options": [
                {"label": "A", "description": "a"}], "multi_select": "yes"}}, [], 1)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_ask_user_valid_shape_ok(self):
        r = teamsmod._validate_lead_action(
            {"tool": "ask_user", "args": {"question": "q?", "header": "Hdr", "options": [
                {"label": "A", "description": "a"}, {"label": "B", "description": "b"}],
                "multi_select": False}}, [], 0)
        self.assertTrue(r["ok"])

    def test_delegate_agent_not_on_team_is_business_rule_not_malformed(self):
        r = teamsmod._validate_lead_action(
            {"tool": "delegate", "args": {"agent": "gemini", "task": "do x"}},
            ["claude", "aider"], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "agent_not_on_team"))
        self.assertIn("gemini", r["detail"])
        self.assertIn("claude", r["detail"])

    def test_delegate_agent_on_team_ok(self):
        r = teamsmod._validate_lead_action(
            {"tool": "delegate", "args": {"agent": "claude", "task": "do x"}}, ["claude"], 0)
        self.assertTrue(r["ok"])

    def test_finish_with_zero_actions_is_premature(self):
        r = teamsmod._validate_lead_action({"tool": "finish", "args": {"summary": "done"}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "premature_finish"))

    def test_finish_with_prior_actions_ok(self):
        r = teamsmod._validate_lead_action({"tool": "finish", "args": {"summary": "done"}}, [], 3)
        self.assertTrue(r["ok"])

    def test_fact_check_valid_ok(self):
        r = teamsmod._validate_lead_action({"tool": "fact_check", "args": {"claim": "x"}}, [], 0)
        self.assertTrue(r["ok"])


# ─── Tier 1: _parse_tier1_action() / _tier1_extra_tool_call_count() ───────
class ParseTier1ActionTests(unittest.TestCase):
    def _payload(self, message):
        return {"choices": [{"message": message}]}

    def test_well_formed_single_tool_call(self):
        payload = self._payload({"tool_calls": [
            {"function": {"name": "finish", "arguments": '{"summary": "done"}'}}]})
        self.assertEqual(teamsmod._parse_tier1_action(payload),
                         {"tool": "finish", "args": {"summary": "done"}})

    def test_only_first_tool_call_honored(self):
        payload = self._payload({"tool_calls": [
            {"function": {"name": "finish", "arguments": '{"summary": "first"}'}},
            {"function": {"name": "ask_user", "arguments": '{"question": "q", "options": []}'}}]})
        self.assertEqual(teamsmod._parse_tier1_action(payload)["tool"], "finish")
        self.assertEqual(teamsmod._tier1_extra_tool_call_count(payload), 1)

    def test_no_extra_calls_reports_zero(self):
        payload = self._payload({"tool_calls": [
            {"function": {"name": "finish", "arguments": "{}"}}]})
        self.assertEqual(teamsmod._tier1_extra_tool_call_count(payload), 0)

    def test_malformed_arguments_json_returns_none(self):
        payload = self._payload({"tool_calls": [
            {"function": {"name": "finish", "arguments": "{not valid json"}}]})
        self.assertIsNone(teamsmod._parse_tier1_action(payload))

    def test_arguments_parsing_to_non_dict_is_passed_through(self):
        payload = self._payload({"tool_calls": [
            {"function": {"name": "finish", "arguments": "[1, 2]"}}]})
        self.assertEqual(teamsmod._parse_tier1_action(payload), {"tool": "finish", "args": [1, 2]})

    def test_no_tool_calls_falls_back_to_tier3_parser_and_recovers(self):
        payload = self._payload({"content": 'blah\n```json\n{"tool": "finish", '
                                            '"args": {"summary": "recovered"}}\n```\n'})
        self.assertEqual(teamsmod._parse_tier1_action(payload),
                         {"tool": "finish", "args": {"summary": "recovered"}})

    def test_no_tool_calls_and_no_fence_falls_through_to_malformed(self):
        payload = self._payload({"content": "I am just going to talk about this instead."})
        self.assertIsNone(teamsmod._parse_tier1_action(payload))

    def test_missing_choices_returns_none(self):
        self.assertIsNone(teamsmod._parse_tier1_action({}))

    def test_message_not_a_dict_returns_none(self):
        self.assertIsNone(teamsmod._parse_tier1_action({"choices": [{"message": "nope"}]}))

    def test_function_not_a_dict_returns_none(self):
        payload = self._payload({"tool_calls": [{"function": "nope"}]})
        self.assertIsNone(teamsmod._parse_tier1_action(payload))

    def test_extra_tool_call_count_on_malformed_payload_is_zero(self):
        self.assertEqual(teamsmod._tier1_extra_tool_call_count({}), 0)


# ─── Tier 1: _parse_tier3_action() ─────────────────────────────────────────
class ParseTier3ActionTests(unittest.TestCase):
    def test_well_formed_fence(self):
        text = 'preamble\n```json\n{"tool": "finish", "args": {"summary": "ok"}}\n```\ntrailer'
        self.assertEqual(teamsmod._parse_tier3_action(text),
                         {"tool": "finish", "args": {"summary": "ok"}})

    def test_no_fence_at_all_returns_none(self):
        self.assertIsNone(teamsmod._parse_tier3_action("just talking, no json here"))

    def test_empty_text_returns_none(self):
        self.assertIsNone(teamsmod._parse_tier3_action(""))
        self.assertIsNone(teamsmod._parse_tier3_action(None))

    def test_fence_with_invalid_json_returns_none(self):
        text = '```json\n{"tool": "finish", "args": {summary: "bad"}\n```'
        self.assertIsNone(teamsmod._parse_tier3_action(text))

    def test_fence_missing_required_keys_returns_none(self):
        text = '```json\n{"foo": "bar"}\n```'
        self.assertIsNone(teamsmod._parse_tier3_action(text))

    def test_args_not_a_dict_is_passed_through_not_rejected_here(self):
        text = '```json\n{"tool": "finish", "args": "not an object"}\n```'
        self.assertEqual(teamsmod._parse_tier3_action(text), {"tool": "finish", "args": "not an object"})

    def test_only_first_fence_tried_even_if_malformed_and_second_is_valid(self):
        text = ('```json\n{not valid\n```\n\nsome text\n\n'
                '```json\n{"tool": "finish", "args": {"summary": "second, valid"}}\n```')
        self.assertIsNone(teamsmod._parse_tier3_action(text))

    def test_bare_fence_no_json_marker_still_parsed(self):
        text = '```\n{"tool": "finish", "args": {"summary": "ok"}}\n```'
        self.assertEqual(teamsmod._parse_tier3_action(text),
                         {"tool": "finish", "args": {"summary": "ok"}})


# ─── Tier 1: _system_framing() -- fact_check mitigation clauses, every tier
class SystemFramingTests(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-lead-framing-")
        os.makedirs(os.path.join(self.projdir, "docs"))
        with open(os.path.join(self.projdir, "docs", "ARCHITECTURE.md"), "w") as f:
            f.write("# Architecture\n\nA small app.\n")

    def tearDown(self):
        shutil.rmtree(self.projdir, ignore_errors=True)

    def test_mitigation_clauses_present_every_tier(self):
        for tier in (1, 2, 3):
            text = teamsmod._system_framing(self.projdir, ["claude"], tier)
            self.assertIn("phrase the claim as a short quotation of exact", text)
            self.assertIn("treat the claim as unverified, not", text)
            # backlog item 19 part 1, docs/spec.md "Acceptance criteria":
            # _INTERJECT_MITIGATION present every tier, same loop.
            self.assertIn("human_interject is an unsolicited", text)

    def test_tier1_omits_prose_tool_descriptions_tier2_3_include_them(self):
        t1 = teamsmod._system_framing(self.projdir, ["claude"], 1)
        t2 = teamsmod._system_framing(self.projdir, ["claude"], 2)
        t3 = teamsmod._system_framing(self.projdir, ["claude"], 3)
        self.assertNotIn("You have exactly six tools", t1)
        self.assertIn("You have exactly six tools", t2)
        self.assertIn("You have exactly six tools", t3)

    def test_tier3_asks_for_a_fenced_block_tier2_does_not(self):
        t2 = teamsmod._system_framing(self.projdir, ["claude"], 2)
        t3 = teamsmod._system_framing(self.projdir, ["claude"], 3)
        self.assertIn("fenced code block", t3)
        self.assertNotIn("fenced code block", t2)

    def test_grounding_digest_included(self):
        text = teamsmod._system_framing(self.projdir, [], 1)
        self.assertIn("Architecture", text)


# ─── Tier 1: _round_context() / _assemble_prompt() bounding ────────────────
class PromptBoundingTests(unittest.TestCase):
    def test_oversize_delegate_result_gets_explicit_truncation_marker(self):
        text = "X" * (teamsmod.TEAM_DELEGATE_RESULT_MAX_CHARS + 5000)
        last_entry = {"round": 3, "tool": "delegate", "full_result_text": text, "log_path": "/tmp/foo.jsonl"}
        ctx = teamsmod._round_context("task", [], last_entry, 4, 8)
        self.assertIn("...[truncated,", ctx)
        self.assertIn("/tmp/foo.jsonl", ctx)

    def test_undersize_result_has_no_truncation_marker(self):
        last_entry = {"round": 1, "tool": "fact_check", "full_result_text": "short result", "log_path": None}
        ctx = teamsmod._round_context("task", [], last_entry, 2, 8)
        self.assertNotIn("...[truncated,", ctx)
        self.assertIn("short result", ctx)

    def test_history_lines_are_one_line_summaries_not_full_text(self):
        history = [{"round": 1, "tool": "delegate", "args_summary": "delegate(agent=claude)",
                    "outcome_summary": "ok, 4 chars (see log)", "full_result_text": "SECRET" * 1000,
                    "log_path": None}]
        ctx = teamsmod._round_context("task", history, None, 2, 8)
        self.assertIn("delegate(agent=claude)", ctx)
        self.assertNotIn("SECRET" * 1000, ctx)

    def test_every_sub_budget_maxed_simultaneously_still_respects_final_cap(self):
        projdir = tempfile.mkdtemp(prefix="switchboard-lead-pathological-")
        try:
            os.makedirs(os.path.join(projdir, "docs"))
            with open(os.path.join(projdir, "docs", "ARCHITECTURE.md"), "w") as f:
                f.write("# Architecture\n\n" + ("This sentence repeats itself. " * 5000))
            members = [f"teammate-{i}" for i in range(20)]
            system = teamsmod._system_framing(projdir, members, tier=3)

            history = []
            for i in range(1, teamsmod.TEAM_MAX_ROUNDS + 1):
                history.append({
                    "round": i, "tool": "delegate",
                    "args_summary": "delegate(agent=teammate-0) " + ("x" * 500),
                    "outcome_summary": "ok, " + ("y" * 500) + " chars (see log)",
                    "full_result_text": "z" * 500,
                    "log_path": "/var/lib/ai-dev-switchboard/teams/leads/somewhere/log.jsonl",
                })
            last_entry = dict(history[-1])
            last_entry["full_result_text"] = "R" * (teamsmod.TEAM_DELEGATE_RESULT_MAX_CHARS * 5)

            round_context = teamsmod._round_context("Task: " + ("T" * 2000), history, last_entry,
                                                     teamsmod.TEAM_MAX_ROUNDS, teamsmod.TEAM_MAX_ROUNDS)
            uncapped_len = len((system + "\n\n" + round_context).encode("utf-8"))
            self.assertGreater(uncapped_len, teamsmod.TEAM_LEAD_PROMPT_MAX_CHARS,
                               "pathological input wasn't actually pathological -- test is too weak")

            capped = teamsmod._assemble_prompt(system, round_context)
            self.assertLessEqual(len(capped.encode("utf-8")), teamsmod.TEAM_LEAD_PROMPT_MAX_CHARS)
        finally:
            shutil.rmtree(projdir, ignore_errors=True)

    def test_split_capped_prompt_normal_case(self):
        s, u = teamsmod._split_capped_prompt("SYS", "USER TEXT")
        self.assertEqual((s, u), ("SYS", "USER TEXT"))

    def test_split_capped_prompt_system_alone_exceeding_cap(self):
        orig = teamsmod.TEAM_LEAD_PROMPT_MAX_CHARS
        teamsmod.TEAM_LEAD_PROMPT_MAX_CHARS = 10
        try:
            s, u = teamsmod._split_capped_prompt(
                "this system framing is already way longer than ten characters", "user text")
        finally:
            teamsmod.TEAM_LEAD_PROMPT_MAX_CHARS = orig
        self.assertEqual(u, "")
        self.assertLessEqual(len(s.encode("utf-8")), 10)


# ─── Tier 1: tier-1 transport-retry wrapper ─────────────────────────────────
class Tier1TransportRetryTests(unittest.TestCase):
    def test_succeeds_after_failures_within_budget(self):
        calls = {"n": 0}

        def fake_call(base_url, model, system, user, tools, *, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("boom")
            return {"choices": [{"message": {"tool_calls": []}}]}

        orig = teamsmod._lead_tier1_call
        teamsmod._lead_tier1_call = fake_call
        try:
            payload, err = teamsmod._tier1_call_with_retry(
                "http://x", "m", "s", "u", [], timeout=1, retry_budget=2)
        finally:
            teamsmod._lead_tier1_call = orig
        self.assertIsNone(err)
        self.assertEqual(calls["n"], 3)
        self.assertIsNotNone(payload)

    def test_exhausts_budget_and_returns_clear_actionable_error(self):
        def always_raise(*a, **kw):
            raise urllib.error.URLError("connection refused")

        orig = teamsmod._lead_tier1_call
        teamsmod._lead_tier1_call = always_raise
        try:
            payload, err = teamsmod._tier1_call_with_retry(
                "http://100.70.98.74:11434/v1", "qwen3:8b", "s", "u", [], timeout=1, retry_budget=2)
        finally:
            teamsmod._lead_tier1_call = orig
        self.assertIsNone(payload)
        self.assertIsNotNone(err)
        self.assertIn("100.70.98.74", err)
        self.assertIn("qwen3:8b", err)
        self.assertNotIn("Traceback", err)

    def test_malformed_json_body_is_also_treated_as_a_transport_failure(self):
        # docs/implementation.md "Deviations from spec": a response body
        # that isn't parseable JSON at all has no other defined bucket, so
        # it is folded into the same transport-retry category.
        def raises_value_error(*a, **kw):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

        orig = teamsmod._lead_tier1_call
        teamsmod._lead_tier1_call = raises_value_error
        try:
            payload, err = teamsmod._tier1_call_with_retry(
                "http://x", "m", "s", "u", [], timeout=1, retry_budget=1)
        finally:
            teamsmod._lead_tier1_call = orig
        self.assertIsNone(payload)
        self.assertIsNotNone(err)


class TeamStepTransportFailureIntegrationTests(_StateTestCase):
    def test_ollama_unreachable_after_retry_budget_exhausted_is_a_clear_error_status(self):
        teamsmod.TEAM_LLM_BASE_URL = "http://example.invalid:11434/v1"
        teamsmod.TEAM_LLM_MODEL = "qwen3:8b"

        def always_raise(req, timeout=None):
            raise urllib.error.URLError("Name or service not known")

        orig_urlopen = urllib.request.urlopen
        urllib.request.urlopen = always_raise
        try:
            state = teamsmod._new_state("run-transport", self.projdir, self._lead(), [], "do something")
            teamsmod.team_step(state)
        finally:
            urllib.request.urlopen = orig_urlopen
        self.assertEqual(state["status"], "error")
        self.assertIn("unreachable", state["error"])
        self.assertNotIn("Traceback", state["error"])
        # never routed through ask_user -- a human can't fix an unreachable
        # LLM backend
        self.assertFalse(os.path.exists(teamsmod._inbox_path(state["run_id"])))


# ─── Tier 1: _write_inbox() ─────────────────────────────────────────────────
class WriteInboxTests(_StateTestCase):
    def test_shape_and_header_truncation(self):
        state = teamsmod._new_state("run-inbox", self.projdir,
                                    self._lead(kind="engine", name="claude", tier=2), ["claude"], "task")
        args = {"question": "Should we do X?", "header": "ThisHeaderIsWayTooLong",
               "options": [{"label": "Yes", "description": "do it"},
                          {"label": "No", "description": "don't"}], "multi_select": False}
        teamsmod._write_ask_user_inbox(state, args)
        with open(teamsmod._inbox_path("run-inbox")) as f:
            inbox = json.load(f)
        self.assertEqual(inbox["kind"], "ask_user")
        self.assertLessEqual(len(inbox["header"]), 12)
        self.assertEqual(inbox["question"], "Should we do X?")
        self.assertEqual(len(inbox["options"]), 2)
        self.assertFalse(inbox["multi_select"])


# ─── Tier 1: malformed-retry-budget-exhausted escalation ───────────────────
class MalformedRetryEscalationTests(_StateTestCase):
    def test_malformed_lead_output_retried_then_escalates_with_raw_text_included(self):
        state = teamsmod._new_state("run-malformed", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return None, None, "not json at all, garbage output"

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_run(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "blocked_ask_user")
        self.assertEqual(len(state["history"]), teamsmod.TEAM_LEAD_MALFORMED_RETRY_BUDGET + 1)
        with open(teamsmod._inbox_path("run-malformed")) as f:
            inbox = json.load(f)
        self.assertIn("not json at all, garbage output", inbox["question"])
        self.assertIn("could not be parsed", inbox["question"])
        self.assertIn(f"{teamsmod.TEAM_LEAD_MALFORMED_RETRY_BUDGET + 1} attempts", inbox["question"])


# ─── Tier 1: TEAM_MAX_ROUNDS forces escalation ─────────────────────────────
class MaxRoundsEscalationTests(_StateTestCase):
    def test_never_finishing_lead_runs_exactly_max_rounds_then_escalates(self):
        state = teamsmod._new_state("run-maxrounds", self.projdir, self._lead(), [], "task")
        call_count = {"n": 0}

        def fake_call_lead(state, system, round_context, **kwargs):
            call_count["n"] += 1
            return {"tool": "fact_check", "args": {"claim": "todo"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_run(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(call_count["n"], teamsmod.TEAM_MAX_ROUNDS)
        self.assertEqual(state["status"], "escalated_max_rounds")
        # docs/implementation.md "Deviations from spec": max-rounds
        # exhaustion is terminal and does NOT write an inbox.json (only
        # status == "blocked_ask_user" does, per §11's own persistence text).
        self.assertFalse(os.path.exists(teamsmod._inbox_path(state["run_id"])))


# ─── Tier 1: business-rule rejections don't consume the malformed budget ──
class BusinessRuleRejectionTests(_StateTestCase):
    def test_agent_not_on_team_ordinary_round_not_malformed(self):
        state = teamsmod._new_state("run-notonteam", self.projdir, self._lead(), ["claude"], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "gemini", "task": "x"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["malformed_retries"], 0)
        self.assertIn("rejected", state["history"][-1]["outcome_summary"])
        self.assertEqual(state["action_count"], 0)

    def test_premature_finish_ordinary_round_not_malformed(self):
        state = teamsmod._new_state("run-premature", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "finish", "args": {"summary": "too soon"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["malformed_retries"], 0)
        self.assertIn("rejected", state["history"][-1]["outcome_summary"])

    def test_resumed_run_round_gt_1_action_count_still_0_is_still_premature(self):
        # A run resumed after an ask_user where the very first action was
        # the escalation itself: history already has one entry, round
        # number is already > 1, but action_count is still 0.
        state = teamsmod._new_state("run-resumed-premature", self.projdir, self._lead(), [], "task")
        state["history"] = [{"round": 1, "tool": "ask_user", "args_summary": "ask_user(...)",
                             "outcome_summary": "answered: go ahead", "full_result_text": "go ahead",
                             "log_path": None}]
        state["round"] = 1
        state["action_count"] = 0

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertIn("rejected", state["history"][-1]["outcome_summary"])
        self.assertEqual(state["history"][-1]["round"], 2)


# ─── Tier 1: delegate bookkeeping / teammate session continuity (§12) ──────
class DelegateBookkeepingTests(_StateTestCase):
    def test_successful_delegate_updates_bookkeeping_and_session(self):
        state = teamsmod._new_state("run-delegate", self.projdir, self._lead(), ["claude"], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "claude", "task": "do x"}}, None, "..."

        seen = {}

        def fake_agent_run(engine, workdir, prompt, *, session_id=None, **kw):
            seen["session_id"] = session_id
            return _dummy_agent_result(text="done", session_id="sess-1", log_path="/tmp/a.jsonl")

        orig_call_lead, orig_agent_run = teamsmod._call_lead, teamsmod.agent_run
        teamsmod._call_lead, teamsmod.agent_run = fake_call_lead, fake_agent_run
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead, teamsmod.agent_run = orig_call_lead, orig_agent_run
        self.assertIsNone(seen["session_id"])  # first delegation, no cached session yet
        self.assertEqual(state["action_count"], 1)
        self.assertEqual(state["teammate_sessions"]["claude"], "sess-1")
        self.assertIsNone(state["in_progress_delegate"])
        self.assertEqual(state["history"][-1]["tool"], "delegate")
        # docs/spec.md "Correction: repeated delegation of an
        # already-completed task" -- the summary must state agent, task,
        # and success EXPLICITLY and SALIENTLY, not just "ok, N chars".
        self.assertIn("agent=claude", state["history"][-1]["args_summary"])
        self.assertIn("do x", state["history"][-1]["args_summary"])
        self.assertIn("SUCCEEDED", state["history"][-1]["outcome_summary"])

    def test_second_delegation_to_same_agent_passes_cached_session_id(self):
        state = teamsmod._new_state("run-delegate2", self.projdir, self._lead(), ["claude"], "task")
        state["teammate_sessions"]["claude"] = "sess-prev"

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "claude", "task": "do y"}}, None, "..."

        seen = {}

        def fake_agent_run(engine, workdir, prompt, *, session_id=None, **kw):
            seen["session_id"] = session_id
            return _dummy_agent_result(text="done2", session_id="sess-2")

        orig_call_lead, orig_agent_run = teamsmod._call_lead, teamsmod.agent_run
        teamsmod._call_lead, teamsmod.agent_run = fake_call_lead, fake_agent_run
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead, teamsmod.agent_run = orig_call_lead, orig_agent_run
        self.assertEqual(seen["session_id"], "sess-prev")
        self.assertEqual(state["teammate_sessions"]["claude"], "sess-2")

    def test_delegate_failure_still_records_history_and_does_not_raise(self):
        state = teamsmod._new_state("run-delegate-fail", self.projdir, self._lead(), ["claude"], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "delegate", "args": {"agent": "claude", "task": "do z"}}, None, "..."

        def fake_agent_run(engine, workdir, prompt, *, session_id=None, **kw):
            return _dummy_agent_result(ok=False, text="", error="engine crashed", session_id=None)

        orig_call_lead, orig_agent_run = teamsmod._call_lead, teamsmod.agent_run
        teamsmod._call_lead, teamsmod.agent_run = fake_call_lead, fake_agent_run
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead, teamsmod.agent_run = orig_call_lead, orig_agent_run
        self.assertEqual(state["status"], "running")
        self.assertIn("FAILED", state["history"][-1]["outcome_summary"])
        self.assertIn("engine crashed", state["history"][-1]["outcome_summary"])


# ─── Tier 1: repeated-delegation prompt-level mitigation (docs/spec.md
# "Correction: repeated delegation of an already-completed task") ─────────
class DelegationHistoryMitigationTests(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-lead-delegmit-")

    def tearDown(self):
        shutil.rmtree(self.projdir, ignore_errors=True)

    def test_mitigation_clause_present_every_tier(self):
        for tier in (1, 2, 3):
            text = teamsmod._system_framing(self.projdir, ["claude"], tier)
            self.assertIn("do not delegate that task again", text)

    def test_round_context_renders_explicit_salient_delegate_summary(self):
        history = [{"round": 1, "tool": "delegate",
                    "args_summary": 'delegate(agent=claude, task="one word")',
                    "outcome_summary": "SUCCEEDED, 4 chars (see log)",
                    "full_result_text": "Todo", "log_path": "/tmp/x.jsonl"}]
        ctx = teamsmod._round_context("task", history, history[-1], 2, 8)
        self.assertIn("agent=claude", ctx)
        self.assertIn("one word", ctx)
        self.assertIn("SUCCEEDED", ctx)


# ─── Tier 1: fact_check / ask_user / finish branches ───────────────────────
class FactCheckAskUserFinishBranchTests(_StateTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.projdir, "docs"))
        with open(os.path.join(self.projdir, "docs", "ARCHITECTURE.md"), "w") as f:
            f.write("# Architecture\n\nThe server runs as an unprivileged system account.\n")

    def test_fact_check_branch_records_result_and_bumps_action_count(self):
        state = teamsmod._new_state("run-fc", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "fact_check", "args": {"claim": "unprivileged system account"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["action_count"], 1)
        self.assertIn("found=True", state["history"][-1]["outcome_summary"])

    def test_ask_user_branch_writes_inbox_and_blocks(self):
        state = teamsmod._new_state("run-au", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "ask_user", "args": {"question": "Continue?", "header": "Cont",
                                                  "options": [{"label": "Yes", "description": "y"},
                                                             {"label": "No", "description": "n"}]}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "blocked_ask_user")
        self.assertTrue(os.path.exists(teamsmod._inbox_path(state["run_id"])))

    def test_finish_branch_after_prior_action_completes_run(self):
        state = teamsmod._new_state("run-fin", self.projdir, self._lead(), [], "task")
        state["action_count"] = 1  # simulate prior work already done

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "finish", "args": {"summary": "all done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "finished")
        self.assertEqual(state["summary"], "all done")


# ─── Tier 1: _call_lead() dispatch (tier 2/3 branches, no real tmux) ───────
class CallLeadDispatchTests(_StateTestCase):
    def test_tier2_dispatch_calls_agent_run_with_schema_and_parses_json(self):
        state = teamsmod._new_state("run-t2", self.projdir,
                                    self._lead(kind="engine", name="claude", tier=2), ["claude"], "task")

        def fake_agent_run(engine, workdir, prompt, *, schema=None, **kw):
            self.assertEqual(schema, teamsmod._TIER2_LEAD_SCHEMA)
            return _dummy_agent_result(text=json.dumps({"tool": "finish", "args": {"summary": "done"}}))

        orig = teamsmod.agent_run
        teamsmod.agent_run = fake_agent_run
        try:
            raw, err, raw_text = teamsmod._call_lead(state, "sys", "ctx")
        finally:
            teamsmod.agent_run = orig
        self.assertEqual(raw, {"tool": "finish", "args": {"summary": "done"}})
        self.assertIsNone(err)

    def test_tier2_dispatch_malformed_json_preserves_raw_text(self):
        state = teamsmod._new_state("run-t2bad", self.projdir,
                                    self._lead(kind="engine", name="claude", tier=2), ["claude"], "task")

        def fake_agent_run(engine, workdir, prompt, *, schema=None, **kw):
            return _dummy_agent_result(text="not json")

        orig = teamsmod.agent_run
        teamsmod.agent_run = fake_agent_run
        try:
            raw, err, raw_text = teamsmod._call_lead(state, "sys", "ctx")
        finally:
            teamsmod.agent_run = orig
        self.assertIsNone(raw)
        self.assertIsNone(err)
        self.assertEqual(raw_text, "not json")

    def test_tier3_dispatch_uses_fence_parser(self):
        state = teamsmod._new_state("run-t3", self.projdir,
                                    self._lead(kind="engine", name="aider", tier=3), ["aider"], "task")

        def fake_agent_run(engine, workdir, prompt, **kw):
            self.assertNotIn("schema", kw)
            return _dummy_agent_result(
                text='```json\n{"tool": "finish", "args": {"summary": "done"}}\n```')

        orig = teamsmod.agent_run
        teamsmod.agent_run = fake_agent_run
        try:
            raw, err, raw_text = teamsmod._call_lead(state, "sys", "ctx")
        finally:
            teamsmod.agent_run = orig
        self.assertEqual(raw, {"tool": "finish", "args": {"summary": "done"}})


# ─── Tier 1: interject() -- backlog item 19 part 1 ─────────────────────────
class InterjectTests(_StateTestCase):
    def test_running_run_appends_envelope_and_never_touches_run_json(self):
        state = teamsmod._new_state("run-interject", self.projdir, self._lead(), [], "task")
        teamsmod._persist(state)
        run_json_path = teamsmod._run_json_path("run-interject")
        with open(run_json_path, "rb") as f:
            before = f.read()

        result = teamsmod.interject("run-interject", "some text")

        self.assertEqual(result, {"ok": True, "run_id": "run-interject"})
        with open(teamsmod._human_log_path("run-interject")) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertEqual(envelope["agent"], "human")
        self.assertEqual(envelope["kind"], "message")
        self.assertEqual(envelope["text"], "some text")
        self.assertEqual(envelope["meta"], {})
        self.assertIn("ts", envelope)
        self.assertIn("seq", envelope)
        # run.json itself is byte-for-byte unchanged -- no _persist() call
        # reachable from interject() (docs/spec.md "Acceptance criteria").
        with open(run_json_path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_unknown_run_id_returns_shaped_error(self):
        result = teamsmod.interject("does-not-exist", "hi")
        self.assertEqual(result, {"ok": False, "error": "no such run_id: does-not-exist"})

    def test_terminal_statuses_rejected_no_file_written(self):
        for status in ("finished", "error", "escalated_max_rounds", "stopped"):
            run_id = f"run-term-{status}"
            state = teamsmod._new_state(run_id, self.projdir, self._lead(), [], "task")
            state["status"] = status
            teamsmod._persist(state)
            result = teamsmod.interject(run_id, "hi")
            self.assertFalse(result["ok"], status)
            self.assertIn(status, result["error"])
            self.assertFalse(os.path.exists(teamsmod._human_log_path(run_id)), status)

    def test_blocked_ask_user_and_blocked_board_write_allowed(self):
        for status in ("blocked_ask_user", "blocked_board_write"):
            run_id = f"run-blocked-{status}"
            state = teamsmod._new_state(run_id, self.projdir, self._lead(), [], "task")
            state["status"] = status
            teamsmod._persist(state)
            result = teamsmod.interject(run_id, "hi")
            self.assertTrue(result["ok"], status)
            self.assertTrue(os.path.isfile(teamsmod._human_log_path(run_id)), status)


# ─── Tier 1: team_step()'s human.jsonl drain checkpoint -- backlog item 19
# part 1 ─────────────────────────────────────────────────────────────────
class TeamStepDrainInterjectTests(_StateTestCase):
    def test_drain_appends_history_entry_and_never_calls_the_lead(self):
        state = teamsmod._new_state("run-drain", self.projdir, self._lead(), [], "task")
        teamsmod._persist(state)
        result = teamsmod.interject("run-drain", "please pause and explain your progress")
        self.assertTrue(result["ok"])

        def fail_call_lead(*a, **kw):
            self.fail("_call_lead() was called on a round that only drained an interjection")

        orig = teamsmod._call_lead
        teamsmod._call_lead = fail_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig

        self.assertEqual(len(state["history"]), 1)
        entry = state["history"][0]
        self.assertEqual(entry["tool"], "human_interject")
        self.assertEqual(entry["full_result_text"], "please pause and explain your progress")
        self.assertGreater(state["human_cursor"], 0)
        self.assertEqual(state["human_cursor"],
                         os.path.getsize(teamsmod._human_log_path("run-drain")))

    def test_full_text_surfaces_in_next_round_context(self):
        state = teamsmod._new_state("run-drain2", self.projdir, self._lead(), [], "task")
        teamsmod._persist(state)
        teamsmod.interject("run-drain2", "switch to plan B")
        teamsmod.team_step(state)  # round 1: drains only, lead not called
        self.assertEqual(state["history"][0]["tool"], "human_interject")

        captured = {}

        def fake_call_lead(state, system, round_context, **kwargs):
            captured["round_context"] = round_context
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)  # round 2: lead called, sees round 1's text
        finally:
            teamsmod._call_lead = orig

        ctx = captured["round_context"]
        self.assertIn("switch to plan B", ctx)
        self.assertIn("round 1, human_interject", ctx)

    def test_two_queued_messages_drained_together_in_order(self):
        state = teamsmod._new_state("run-drain3", self.projdir, self._lead(), [], "task")
        teamsmod._persist(state)
        teamsmod.interject("run-drain3", "first message")
        teamsmod.interject("run-drain3", "second message")

        def fail_call_lead(*a, **kw):
            self.fail("_call_lead() was called on a round that only drained interjections")

        orig = teamsmod._call_lead
        teamsmod._call_lead = fail_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig

        self.assertEqual(len(state["history"]), 2)
        self.assertEqual([h["tool"] for h in state["history"]], ["human_interject", "human_interject"])
        self.assertEqual(state["history"][0]["full_result_text"], "first message")
        self.assertEqual(state["history"][1]["full_result_text"], "second message")
        self.assertEqual(state["human_cursor"],
                         os.path.getsize(teamsmod._human_log_path("run-drain3")))


# ─── CLI: team-interject -- backlog item 19 part 1 ─────────────────────────
class CliTeamInterjectTests(_StateTestCase):
    def test_parses_run_id_and_text_positionals(self):
        args = teamsmod._parse_args(["team-interject", "abc", "hello there"])
        self.assertEqual(args.command, "team-interject")
        self.assertEqual(args.run_id, "abc")
        self.assertEqual(args.text, "hello there")

    def test_success_prints_queued_message_exit_0_without_driving_the_run(self):
        state = teamsmod._new_state("run-cli-interject", self.projdir, self._lead(), [], "task")
        teamsmod._persist(state)
        call_count = {"n": 0}

        def fake_call_lead(*a, **kw):
            call_count["n"] += 1
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        self.addCleanup(setattr, teamsmod, "_call_lead", orig)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = teamsmod.main(["team-interject", "run-cli-interject", "hello"])
        self.assertEqual(rc, 0)
        self.assertIn("queued for run run-cli-interject", buf.getvalue())
        # no side effect drives the run -- the mocked lead adapter's own
        # call count is unchanged (docs/spec.md "Acceptance criteria").
        self.assertEqual(call_count["n"], 0)
        with open(teamsmod._human_log_path("run-cli-interject")) as f:
            envelope = json.loads(f.readline())
        self.assertEqual(envelope["text"], "hello")

    def test_unresolvable_run_id_exits_nonzero(self):
        rc = teamsmod.main(["team-interject", "no-such-run", "hello"])
        self.assertEqual(rc, 1)


# ─── Tier 1: persistence + crash recovery ──────────────────────────────────
class PersistRoundTripPromptReconstructionTests(_StateTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.projdir, "docs"))
        with open(os.path.join(self.projdir, "docs", "ARCHITECTURE.md"), "w") as f:
            f.write("# Architecture\n\nA small app.\n")

    def test_persist_then_load_reconstructs_identical_next_round_prompt(self):
        state = teamsmod._new_state("run-recon", self.projdir, self._lead(), ["claude"], "do the thing")
        state["history"].append({"round": 1, "tool": "fact_check", "args_summary": 'fact_check("x")',
                                 "outcome_summary": "found=False (unverified)",
                                 "full_result_text": json.dumps({"claim": "x", "found": False, "matches": []}),
                                 "log_path": None})
        state["round"] = 1
        state["action_count"] = 1

        def _build_prompt(s):
            system = teamsmod._system_framing(s["workdir"], s["members"], s["lead"]["tier"])
            last = s["history"][-1]
            ctx = teamsmod._round_context(s["task"], s["history"], last, 2, s["max_rounds"])
            return teamsmod._assemble_prompt(system, ctx)

        live_prompt = _build_prompt(state)
        teamsmod._persist(state)
        loaded = teamsmod._load_state("run-recon")
        loaded_prompt = _build_prompt(loaded)
        self.assertEqual(live_prompt, loaded_prompt)


class InProgressCrashRecoveryPureTests(_StateTestCase):
    def test_in_progress_delegate_recorded_as_unresolved_not_successful(self):
        state = teamsmod._new_state("run-crash", self.projdir,
                                    self._lead(kind="engine", name="claude", tier=2), ["claude"], "task")
        state["in_progress_delegate"] = {"round": 1, "agent": "claude", "task_preview": "do x"}
        teamsmod._persist(state)
        loaded = teamsmod._load_state("run-crash")
        teamsmod._recover_in_progress(loaded)
        self.assertIsNone(loaded["in_progress_delegate"])
        self.assertEqual(len(loaded["history"]), 1)
        self.assertEqual(loaded["history"][0]["tool"], "delegate")
        self.assertIn("interrupted", loaded["history"][0]["outcome_summary"])
        self.assertEqual(loaded["action_count"], 1)

    def test_no_in_progress_is_a_no_op(self):
        state = teamsmod._new_state("run-noop", self.projdir, self._lead(), [], "task")
        teamsmod._recover_in_progress(state)
        self.assertEqual(state["history"], [])
        self.assertEqual(state["action_count"], 0)


# ─── Tier 2: schema-constrained agent_run(), no spawn on unsupported engine
class AgentRunSchemaNoSpawnTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-noschema-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-noschema-projects-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        appmod.ENGINES_DIR = self.engines_dir
        _write_engine_file(self.engines_dir, "noschema.engine", """\
            LABEL=NoSchema
            CMD=unused
            HEADLESS_CMD=headless-tool -p {resume}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def test_schema_without_schema_flag_raises_before_spawning(self):
        _forbid_spawn(self)
        with self.assertRaises(ValueError):
            teamsmod.agent_run("noschema", self.workdir, "hi", schema={"type": "object"}, timeout=5)

    def test_no_schema_argument_unaffected(self):
        # existing agent_run() call shape (no schema=) must remain unaffected
        # by this cycle's changes; this is a validation-only assertion (no
        # spawn happens either way for a workdir-doesn't-exist case) that
        # the new keyword-only parameter didn't change positional behavior.
        import inspect
        sig = inspect.signature(teamsmod.agent_run)
        self.assertIn("schema", sig.parameters)
        self.assertIsNone(sig.parameters["schema"].default)
        self.assertEqual(sig.parameters["schema"].kind, inspect.Parameter.KEYWORD_ONLY)


# ─── Tier 2: real tmux, {schema} substitution + rundir/schema.json ─────────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmuxSchemaTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-schema-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-schema-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lead-schema-state-")
        self.scripts_dir = tempfile.mkdtemp(prefix="switchboard-lead-schema-scripts-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        _patch_tmux(self, ["tmux"])
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.scripts_dir, ignore_errors=True)

    def test_schema_file_form_written_with_correct_permissions_content_then_cleaned_up(self):
        # {schema_file} -- the FILE-path delivery form (Codex's own
        # --output-schema <FILE>), docs/spec.md's own correction.
        script = _write_script(self.scripts_dir, "read_schema.py", """\
            #!/usr/bin/env python3
            import sys, os, stat, json
            args = sys.argv[1:]
            idx = args.index("--schema-file")
            schema_path = args[idx + 1]
            mode = oct(stat.S_IMODE(os.stat(schema_path).st_mode))
            with open(schema_path) as f:
                content = f.read()
            print(json.dumps({"path": schema_path, "mode": mode, "content": content}))
            """)
        _write_engine_file(self.engines_dir, "schemaeng.engine", f"""\
            LABEL=SchemaEng
            CMD=unused
            HEADLESS_CMD=python3 {script} {{schema}}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema-file {{schema_file}}
            """)
        eng = appmod.load_engines()["schemaeng"]
        self.assertEqual(teamsmod._schema_placeholder_kind(eng.headless_schema_flag), "file")
        result = teamsmod.agent_run("schemaeng", self.workdir, "hello",
                                    schema={"tool": "x"}, timeout=30)
        self.assertTrue(result["ok"], result.get("error"))
        payload = json.loads(result["text"])
        self.assertEqual(payload["mode"], "0o644")
        self.assertEqual(json.loads(payload["content"]), {"tool": "x"})
        headless_root = os.path.join(self.state_dir, "_headless")
        if os.path.isdir(headless_root):
            self.assertEqual(os.listdir(headless_root), [])

    def test_schema_inline_form_arrives_as_shell_quoted_json_text_in_argv(self):
        # {schema} -- the INLINE delivery form (Claude Code's own
        # --json-schema <schema>), the corrected form after this sub-spec's
        # own real-CLI finding (docs/spec.md's correction).
        script = _write_script(self.scripts_dir, "dump_schema_arg.py", """\
            #!/usr/bin/env python3
            import sys, json
            args = sys.argv[1:]
            idx = args.index("--json-schema")
            print(json.dumps({"schema_arg": args[idx + 1]}))
            """)
        _write_engine_file(self.engines_dir, "inlineschemaeng.engine", f"""\
            LABEL=InlineSchemaEng
            CMD=unused
            HEADLESS_CMD=python3 {script} {{schema}}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--json-schema {{schema}}
            """)
        eng = appmod.load_engines()["inlineschemaeng"]
        self.assertEqual(teamsmod._schema_placeholder_kind(eng.headless_schema_flag), "inline")
        schema = {"type": "object", "properties": {"tool": {"type": "string"}},
                 "required": ["tool"]}
        result = teamsmod.agent_run("inlineschemaeng", self.workdir, "hello",
                                    schema=schema, timeout=30)
        self.assertTrue(result["ok"], result.get("error"))
        payload = json.loads(result["text"])
        # arrived as ONE argv element, and round-trips to the exact schema
        self.assertEqual(json.loads(payload["schema_arg"]), schema)

    def test_no_schema_argument_leaves_schema_token_empty(self):
        script = _write_script(self.scripts_dir, "dump_args.py", """\
            #!/usr/bin/env python3
            import sys, json
            print(json.dumps(sys.argv[1:]))
            """)
        _write_engine_file(self.engines_dir, "noargschema.engine", f"""\
            LABEL=NoArgSchema
            CMD=unused
            HEADLESS_CMD=python3 {script} {{schema}}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema-file {{schema_file}}
            """)
        result = teamsmod.agent_run("noargschema", self.workdir, "hello", timeout=30)
        self.assertTrue(result["ok"], result.get("error"))
        argv = json.loads(result["text"])
        self.assertNotIn("--schema-file", argv)


# ─── Tier 2: real tmux, tier-3 stand-in fixtures driving the full loop ─────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmuxTier3StandInTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-tier3-engines-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-tier3-projects-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lead-tier3-state-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_STATE_DIR = self.state_dir
        _patch_tmux(self, ["tmux"])
        self.workdir = os.path.join(self.projects_dir, "proj")
        os.makedirs(self.workdir)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _wire_engine(self, name, script_name):
        _write_engine_file(self.engines_dir, f"{name}.engine", f"""\
            LABEL={name}
            CMD=unused
            HEADLESS_CMD={_fixture(script_name)}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)

    def test_well_formed_two_step_fixture_drives_a_real_finish(self):
        self._wire_engine("tier3lead", "tier3_stub_two_step.sh")
        eng = appmod.load_engines()["tier3lead"]
        self.assertEqual(teamsmod._lead_tier_for_engine(eng), 3)
        state = teamsmod._new_state("run-real-tier3-ok", self.workdir,
                                    {"kind": "engine", "name": "tier3lead", "tier": 3}, [], "do the thing")
        teamsmod.team_run(state)
        self.assertEqual(state["status"], "finished")
        self.assertEqual(state["history"][0]["tool"], "fact_check")
        self.assertEqual(state["history"][1]["tool"], "finish")

    def test_no_fence_fixture_retries_then_escalates(self):
        self._wire_engine("tier3nofence", "tier3_stub_no_fence.sh")
        state = teamsmod._new_state("run-real-tier3-nofence", self.workdir,
                                    {"kind": "engine", "name": "tier3nofence", "tier": 3}, [], "do the thing")
        teamsmod.team_run(state)
        self.assertEqual(state["status"], "blocked_ask_user")
        self.assertEqual(len(state["history"]), teamsmod.TEAM_LEAD_MALFORMED_RETRY_BUDGET + 1)
        with open(teamsmod._inbox_path(state["run_id"])) as f:
            inbox = json.load(f)
        self.assertIn("decided not to use any tool", inbox["question"])

    def test_malformed_fence_fixture_retries_then_escalates(self):
        self._wire_engine("tier3badfence", "tier3_stub_malformed_fence.sh")
        state = teamsmod._new_state("run-real-tier3-badfence", self.workdir,
                                    {"kind": "engine", "name": "tier3badfence", "tier": 3}, [], "do the thing")
        teamsmod.team_run(state)
        self.assertEqual(state["status"], "blocked_ask_user")
        self.assertEqual(len(state["history"]), teamsmod.TEAM_LEAD_MALFORMED_RETRY_BUDGET + 1)


# ─── Tier 2: real subprocess CLI, ask_user -> team-resolve in a SEPARATE
# process invocation (docs/spec.md's own explicit acceptance criterion) ────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class ResolveInSeparateProcessTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-resolve-engines-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lead-resolve-state-")
        self.workdir = tempfile.mkdtemp(prefix="switchboard-lead-resolve-workdir-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-resolve-projects-")
        _write_engine_file(self.engines_dir, "tier3lead.engine", f"""\
            LABEL=Tier3Lead
            CMD=unused
            HEADLESS_CMD={_fixture("tier3_stub_resolve_flow.sh")}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        self.env = dict(os.environ)
        self.env["ENGINES_DIR"] = self.engines_dir
        self.env["TEAM_STATE_DIR"] = self.state_dir
        self.env["PROJECTS_DIR"] = self.projects_dir

    def tearDown(self):
        shutil.rmtree(self.engines_dir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.workdir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def _run_cli(self, *args):
        cmd = [sys.executable, os.path.join(APP_DIR, "teams.py")] + list(args)
        return subprocess.run(cmd, cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=90)

    def test_team_start_blocks_then_team_resolve_in_a_separate_process_resumes_to_finished(self):
        r1 = self._run_cli("team-start", self.workdir, "--task", "do the thing",
                           "--lead", "tier3lead", "--members", "")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        state1 = json.loads(r1.stdout)
        self.assertEqual(state1["status"], "blocked_ask_user")
        run_id = state1["run_id"]
        inbox_path = os.path.join(self.state_dir, "leads", run_id, "inbox.json")
        self.assertTrue(os.path.isfile(inbox_path))

        # A genuinely separate process invocation -- proves the loop resumes
        # from persisted state on disk, not from any in-memory state of the
        # process that blocked (which has already exited by this point).
        r2 = self._run_cli("team-resolve", run_id, "--answer", "Yes")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        state2 = json.loads(r2.stdout)
        self.assertEqual(state2["status"], "finished")
        self.assertFalse(os.path.isfile(inbox_path))
        self.assertTrue(os.path.isfile(
            os.path.join(self.state_dir, "leads", run_id, "inbox.resolved.json")))

    def test_team_status_reports_persisted_state(self):
        r1 = self._run_cli("team-start", self.workdir, "--task", "do the thing",
                           "--lead", "tier3lead", "--members", "")
        run_id = json.loads(r1.stdout)["run_id"]
        r2 = self._run_cli("team-status", run_id)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(json.loads(r2.stdout)["run_id"], run_id)


# ─── Tier 2: real subprocess CLI, mid-delegate crash + team-resume ─────────
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class ResumeAfterMidDelegateCrashTests(unittest.TestCase):
    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-lead-crash-engines-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-lead-crash-state-")
        self.workdir = tempfile.mkdtemp(prefix="switchboard-lead-crash-workdir-")
        self.projects_dir = tempfile.mkdtemp(prefix="switchboard-lead-crash-projects-")
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
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.workdir, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def _run_cli(self, *args):
        cmd = [sys.executable, os.path.join(APP_DIR, "teams.py")] + list(args)
        return subprocess.run(cmd, cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=90)

    def test_team_resume_after_hand_constructed_in_progress_crash_reaches_finished(self):
        # Hand-constructs a run.json left "in_progress" by a simulated
        # mid-delegate crash (docs/spec.md "Mid-delegate crash"), IN this
        # test process, then resumes it via a genuinely separate `team-
        # resume` subprocess invocation -- end to end, not just at the
        # state-file level.
        run_id = "hand-crashed-run"
        orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir
        try:
            state = teamsmod._new_state(run_id, self.workdir,
                                        {"kind": "engine", "name": "tier3lead", "tier": 3}, [], "task")
            state["in_progress_delegate"] = {"round": 1, "agent": "someagent", "task_preview": "do x"}
            teamsmod._persist(state)
        finally:
            teamsmod.TEAM_STATE_DIR = orig_state_dir

        r = self._run_cli("team-resume", run_id)
        self.assertEqual(r.returncode, 0, r.stderr)
        final = json.loads(r.stdout)
        self.assertEqual(final["status"], "finished")
        self.assertIn("interrupted", final["history"][0]["outcome_summary"])
        self.assertEqual(final["history"][1]["tool"], "finish")


if __name__ == "__main__":
    unittest.main()
