#!/usr/bin/env python3
"""
Headless engine invocation (backlog item 6, sub-spec 6a -- docs/spec.md).

agent_run(engine, workdir, prompt, ...) runs exactly one bounded,
non-interactive turn of a named engine (claude/codex/aider, or any future
engine whose *.engine file defines HEADLESS_CMD), translates its native
NDJSON/plain-text output into one normalized event envelope per line
(docs/story.md §4.1), appends those events to a durable .jsonl log, and
returns a normalized result dict -- no capture-pane, no screen-scraping, a
real exit code as the completion signal.

The headless process is spawned inside a throwaway tmux session, as
RUN_USER, via the *same* TMUX sudoers rule instance_start() already uses
(app/app.py:191) -- zero new privilege surface (see docs/spec.md "Why
tmux-hosted"). This module never imports anything privileged of its own; it
only re-uses TMUX/tmux_has/load_engines from app.py.

Nothing here chooses an engine, plans, retries at a semantic level, or talks
to any web UI -- that's 6c/6d/6e/6f. This is the foundation they build on:
one bounded turn, one named engine, callable with zero server and zero UI.

Stdlib-only Python, matching app/app.py and scripts/taiga_push_spec.py (the
existing precedent for a standalone stdlib CLI script in this repo).

CLI usage:
    python3 app/teams.py list-engines
    python3 app/teams.py run <engine> <workdir> --prompt "..." [--session-id ID]

Run with:
    python3 -m unittest discover -s tests -v
"""
import argparse
import calendar
import errno
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
from app import TMUX, tmux_has, load_engines  # noqa: E402  app/app.py:191,:1187,:315
import taiga_board  # noqa: E402  backlog item 7 part 1 -- Taiga board_read/board_write client

# ─── config (see config/switchboard.env.example for the full reference) ──
TEAM_STATE_DIR = os.environ.get("TEAM_STATE_DIR", "/var/lib/ai-dev-switchboard/teams")
TEAM_HEADLESS_TIMEOUT_SECONDS = float(os.environ.get("TEAM_HEADLESS_TIMEOUT_SECONDS", "600"))
TEAM_HEADLESS_KILL_GRACE_SECONDS = float(os.environ.get("TEAM_HEADLESS_KILL_GRACE_SECONDS", "10"))
TEAM_HEADLESS_POLL_SECONDS = float(os.environ.get("TEAM_HEADLESS_POLL_SECONDS", "0.5"))
TEAM_HEADLESS_MAX_EVENTS = int(os.environ.get("TEAM_HEADLESS_MAX_EVENTS", "2000"))
TEAM_HEADLESS_MAX_LINE_BYTES = int(os.environ.get("TEAM_HEADLESS_MAX_LINE_BYTES", str(1024 * 1024)))
TEAM_HEADLESS_PROMPT_MAX_BYTES = int(os.environ.get("TEAM_HEADLESS_PROMPT_MAX_BYTES", str(1024 * 1024)))
TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES = int(os.environ.get("TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES", "65536"))
TEAM_HEADLESS_STDERR_TAIL_BYTES = int(os.environ.get("TEAM_HEADLESS_STDERR_TAIL_BYTES", "4096"))
TEAM_HEADLESS_STALE_RUN_TTL_SECONDS = int(os.environ.get("TEAM_HEADLESS_STALE_RUN_TTL_SECONDS", "7200"))

# Measures the UTF-8-encoded byte length of build_digest()'s own returned
# string (the text actually seeded into a lead's system prompt), not the
# size of any source file (docs/spec.md 6b §1).
TEAM_GROUNDING_MAX_BYTES = int(os.environ.get("TEAM_GROUNDING_MAX_BYTES", "8000"))

# Roster + lead loop (backlog item 6c, docs/spec.md §1). Deliberately a
# sibling of, not shared with, DESC_LLM_* (app/app.py:111-112) -- a model
# good at a one-line project description is not necessarily a good
# tool-caller, and pointing both at the same env vars would silently couple
# two independent tuning decisions. TEAM_LLM_BASE_URL unset simply means the
# roster has no tier-1 (Ollama) member; an engines.d-based lead (tier 2/3)
# still works with zero Ollama config at all.
TEAM_LLM_BASE_URL = os.environ.get("TEAM_LLM_BASE_URL") or None
TEAM_LLM_MODEL = os.environ.get("TEAM_LLM_MODEL", "")

# Per-HTTP-call timeout to the tier-1 endpoint. The 6c spike (docs/story.md
# §2.5) measured mean 7.4s, max 20.8s over 10 calls against an IDLE remote
# Ollama; 120 is ~6x the observed max, not the max itself -- 10 samples on an
# idle endpoint is not a load test, so this is deliberately generous rather
# than tight (a too-tight default turns "the endpoint is a little slow right
# now" into a spurious transport-retry).
TEAM_LLM_TIMEOUT_SECONDS = float(os.environ.get("TEAM_LLM_TIMEOUT_SECONDS", "120"))

# Retries for a *transport*-layer failure only (URLError/timeout/HTTP 5xx,
# and -- see _tier1_call_with_retry()'s own docstring for why -- a response
# body that fails to even parse as JSON) talking to the tier-1 endpoint --
# never a model-output-quality problem. 2 (3 attempts total). Distinct from
# TEAM_LEAD_MALFORMED_RETRY_BUDGET on purpose: an unreachable endpoint and a
# model that returns garbage are different failure classes with different
# correct responses (surface a clear operational error vs.
# retry-then-escalate-to-a-human).
TEAM_LLM_TRANSPORT_RETRY_BUDGET = int(os.environ.get("TEAM_LLM_TRANSPORT_RETRY_BUDGET", "2"))

# Hard round ceiling across the whole lead loop, all tiers -- inherited from
# docs/story.md §3's already-settled default (8), not re-derived here, but
# still proven in this cycle's own tests against a real pathological case (a
# stub lead that never calls finish), not just inherited on faith.
TEAM_MAX_ROUNDS = int(os.environ.get("TEAM_MAX_ROUNDS", "8"))

# Retries for a lead action that CANNOT be turned into a valid tool call at
# all, any tier -- unknown tool name, missing/wrong-typed required args,
# unparsable tier-3/tier-2 JSON. 2 (3 attempts total). One shared budget
# across all three tiers, not three near-identical ones -- see
# _validate_lead_action()'s own docstring for why unifying is deliberate.
TEAM_LEAD_MALFORMED_RETRY_BUDGET = int(os.environ.get("TEAM_LEAD_MALFORMED_RETRY_BUDGET", "2"))

# Cap on the MOST RECENT delegation's raw agent_run() `text`, folded verbatim
# into the next round's prompt. 4000 (~1000 tokens) -- proven in this cycle's
# tests against a synthetic delegation result sized like a real oversize case
# (a full file-dump/diff-shaped payload well past this cap), not a typical
# short answer.
TEAM_DELEGATE_RESULT_MAX_CHARS = int(os.environ.get("TEAM_DELEGATE_RESULT_MAX_CHARS", "4000"))

# FINAL, unconditional cap on the entire assembled per-round prompt (system
# framing + grounding digest + round history + most-recent capped result),
# applied last regardless of whether every smaller budget above it was
# individually respected -- same "the heuristic above it doesn't have to be
# right, because the final step always runs" pattern build_digest() already
# uses. 20000, sized with headroom over the sum of its own parts at default
# config (TEAM_GROUNDING_MAX_BYTES 8000 + 8 rounds x ~100-char summary lines
# + one 4000-char capped result + ~2000 chars of framing/instructions ~=
# 15,000) -- proven in this cycle's tests by forcing every sub-budget to its
# own maximum simultaneously (docs/spec.md's own "every magic constant here
# must be justified against a real oversize case" -- see docs/implementation.md).
TEAM_LEAD_PROMPT_MAX_CHARS = int(os.environ.get("TEAM_LEAD_PROMPT_MAX_CHARS", "20000"))

# Team session lifecycle -- worktrees + tmux dashboard session (backlog item
# 6d, part 1, docs/spec.md §11). TEAM_WORKTREE_OP_TIMEOUT_SECONDS bounds a
# single `git worktree add`/`remove` invocation (via _run_run_user_command())
# -- 30s is generous for a well-behaved git command against a local
# filesystem, not tuned against a measured pathological case the way the
# headless-engine timeouts above are (a hung git process, e.g. an index lock
# held by another process, is the only realistic way to hit this).
TEAM_WORKTREE_OP_TIMEOUT_SECONDS = float(os.environ.get("TEAM_WORKTREE_OP_TIMEOUT_SECONDS", "30"))

# TTL backstop for a team-<project> tmux session/its worktrees once the run
# has reached a terminal status (finished/escalated_max_rounds/error/
# stopped) -- mirrors UPLOAD_STAGING_TTL_SECONDS's own precedent (app/app.py,
# docs/ARCHITECTURE.md "Upload staging"): a resource deliberately NOT torn
# down in a `finally`, reclaimed opportunistically instead (sweep_dead_
# teams()). 86400 (24h) -- long enough that a human reviewing a finished
# team's worktrees the next morning still finds them, short enough not to
# accumulate indefinitely on a box that's never rebooted. Never applies to
# status=="blocked_ask_user", at any age -- see sweep_dead_teams()'s own
# docstring and docs/spec.md "Open questions".
TEAM_SESSION_STALE_TTL_SECONDS = int(os.environ.get("TEAM_SESSION_STALE_TTL_SECONDS", "86400"))

# Overwatch feed + escalation inbox (backlog item 6f part 1, docs/spec.md).
# Per-file, per-poll byte cap for GET .../team/events -- tail_jsonl_events()
# never reads more than this many new bytes past a file's own cursor on any
# one call, regardless of how much a chatty team has actually appended.
# 65536 (64 KiB) -- a round, conservative default in the same order of
# magnitude as this module's other per-poll caps (there's no existing
# "how chatty is one agent's event stream per poll interval" measured data
# point the way 6b's own grounding cap had against this repo's real
# BACKLOG.md -- docs/spec.md "Open questions" -- so this is proceeding
# unmeasured, tunable via this env var once part 2 shows real traffic).
TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL = int(
    os.environ.get("TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL", str(64 * 1024)))

# Max length of the free-text `answer` POST /team/resolve accepts (docs/
# spec.md §3) -- a short human answer to a pending ask_user question, not a
# prompt (TEAM_HEADLESS_PROMPT_MAX_BYTES/TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES
# are a materially different budget for a materially different kind of
# text, see _validate_prompt_size()'s own docstring for why this spec
# deliberately doesn't reuse that function). 2000 chars is generous for a
# free-text answer/explanation while still catching an obviously-wrong
# paste (an entire file, a whole prompt) before it's appended to the run's
# history and folded into the lead's next-round context.
TEAM_ASK_USER_ANSWER_MAX_CHARS = int(os.environ.get("TEAM_ASK_USER_ANSWER_MAX_CHARS", "2000"))

# Max length of board_write's own `value` argument (backlog item 7 part 1,
# docs/spec.md "Edge cases") -- a proposed status name/description/comment
# text the LEAD itself supplies, a materially different case from
# TEAM_ASK_USER_ANSWER_MAX_CHARS's own human free-text answer. 8000
# (matching TEAM_GROUNDING_MAX_BYTES's own precedent, not the narrower
# 2000-char ask_user default per docs/spec.md's own "Open questions" --
# "a real userstory description could reasonably be longer") than a short
# human answer/explanation would need.
TEAM_BOARD_WRITE_VALUE_MAX_CHARS = int(os.environ.get("TEAM_BOARD_WRITE_VALUE_MAX_CHARS", "8000"))

# Bash's own `$?` convention for a foreground child killed by signal N is
# 128+N (POSIX, confirmed live for SIGTERM against `claude -p` during this
# sub-spec's Tier 3 verification -- see docs/implementation.md). Generic
# across engines on purpose: Codex's/aider's own signal-exit codes are
# whatever they turn out to be, not assumed to match Claude's.
_SIGNAL_EXIT_NUMBERS = {int(signal.SIGHUP), int(signal.SIGINT), int(signal.SIGQUIT),
                        int(signal.SIGTERM), int(signal.SIGKILL)}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _run_id() -> str:
    # Matches new_session()'s own use of secrets (app/app.py:252) -- also
    # guarantees no two concurrent headless runs collide with each other,
    # independent of the "switchboard" prefix reservation that keeps them
    # from colliding with a *project* session (docs/spec.md "Session naming").
    return f"{int(time.time())}-{secrets.token_hex(6)}"


# Matches _run_id()'s own generation format exactly (int(time.time()) +
# "-" + secrets.token_hex(6)) -- any run_id not shaped like this cannot be
# a real run this process ever created, so rejecting it outright is both
# safe (never a false negative against a real run_id) and correct (never
# lets a client-supplied run_id reach a path-join unvalidated, docs/
# BACKLOG.md item 11(b)). If _run_id()'s own format ever changes, this must
# change with it.
_RUN_ID_RE = re.compile(r"^[0-9]+-[0-9a-f]{12}$")


# ─── prompt/argv/script construction -- pure functions, no subprocess ─────
def _resume_fragment(engine, session_id):
    """
    Returns the string {resume} should be substituted with: "" for a first
    turn (session_id is None), or engine.headless_resume with {session_id}
    substituted into it for a resumed turn. Raises ValueError -- before
    anything is spawned -- if session_id is given for an engine whose
    HEADLESS_CMD has no {resume} token or that defines no HEADLESS_RESUME at
    all (e.g. aider, which has no resume concept -- docs/story.md §2.1).
    """
    if session_id is None:
        return ""
    if "{resume}" not in engine.headless_cmd or not engine.headless_resume:
        raise ValueError(
            f"engine '{engine.name}' does not support session_id/resume")
    return engine.headless_resume.replace("{session_id}", session_id)


# Linux kernel constant (PAGE_SIZE * 32 on every arch that matters here) --
# the ceiling on any *single* argv element, independent of the much larger
# ARG_MAX/`getconf ARG_MAX` (which bounds total argv+env size, not any one
# element). Models the ceiling on the *engine's own* exec() argv element for
# the prompt (HEADLESS_PROMPT=arg) -- not the tmux/bash spawn, whose own
# command line is small and constant-length regardless of prompt size (see
# _build_script()'s docstring). Per-argv-element, so this is never shared
# with the engine's other flags or the script's own paths/redirects.
_MAX_ARG_STRLEN = 131072

# Small, deliberately conservative safety margin reserved out of
# _MAX_ARG_STRLEN -- not a budget shared with anything else, since the
# prompt is always its own isolated argv element on the engine's exec.
# TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES's default (65536) is well under this
# ceiling regardless, so in practice this constant is inert belt-and-braces
# at default config, not a binding value.
_ARG_SCRIPT_OVERHEAD_BYTES = 1024


def _validate_prompt_size(headless_prompt: str, prompt: str, schema_text: str = None) -> None:
    """
    Two byte caps, not one (docs/spec.md §3): `arg` mode has a materially
    tighter ceiling than `stdin`/`file`, since the prompt ends up as its own
    argv element when bash (having read the generated script file) forks
    and execs the real engine binary -- capped by Linux's MAX_ARG_STRLEN.
    `stdin`/`file` modes have no such constraint -- the prompt never appears
    in any argv at all, so a plain raw-byte count is the right check there.

    For `arg` mode, this validates the shlex.quote()'d prompt length rather
    than its raw length. Bash's own quote-removal recovers the raw,
    unescaped prompt before exec'ing the engine, so in principle the raw
    length is what should be checked -- but shlex.quote()'d length is
    always >= raw length (quoting only ever adds bytes, up to 5x for a
    quote-heavy prompt), so validating the quoted form is a sound, if
    occasionally over-conservative, proxy for it.

    `schema_text` (backlog item 6c, docs/spec.md "Correction: {schema} is
    inline for Claude, a file for Codex") is the schema's own raw JSON text
    when -- and only when -- it is being delivered INLINE (a
    HEADLESS_SCHEMA_FLAG using the {schema} placeholder, e.g. Claude Code's
    `--json-schema <schema>`). An inline schema becomes its OWN argv element
    via the same shlex.quote()-then-splice-into-HEADLESS_CMD-then-
    shlex.split() pipeline the arg-mode prompt already uses, REGARDLESS of
    what headless_prompt itself is set to (the schema's own delivery mode is
    independent of the prompt's) -- so it must be checked against the same
    cap either way: summed with the prompt's own quoted length in `arg`
    mode (both are sharing the same "how much are we willing to cram into
    argv" budget), or checked on its own in `stdin`/`file` mode (where the
    prompt itself isn't in argv, but an inline schema still would be).
    `schema_text` is None both when there's no schema at all and when the
    schema is being delivered via the file-path form ({schema_file}) --
    a file path is short and carries no size risk of its own.
    """
    cap = min(TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES, _MAX_ARG_STRLEN - _ARG_SCRIPT_OVERHEAD_BYTES)
    schema_quoted_len = len(shlex.quote(schema_text).encode("utf-8")) if schema_text is not None else 0
    if headless_prompt == "arg":
        quoted_len = len(shlex.quote(prompt).encode("utf-8")) + schema_quoted_len
        if quoted_len > cap:
            raise ValueError(
                f"prompt's shell-escaped length, plus an inline schema's if one is "
                f"given, ({quoted_len} bytes) exceeds the {cap}-byte cap for "
                "HEADLESS_PROMPT=arg -- quote-heavy prompts expand significantly once "
                "shell-escaped (up to 5x for a prompt of mostly single quotes); use an "
                "engine with HEADLESS_PROMPT=stdin or HEADLESS_PROMPT=file for long or "
                "quote-heavy prompts, which have no such limit")
    else:
        n = len(prompt.encode("utf-8"))
        if n > TEAM_HEADLESS_PROMPT_MAX_BYTES:
            raise ValueError(f"prompt exceeds {TEAM_HEADLESS_PROMPT_MAX_BYTES}-byte cap")
    if headless_prompt != "arg" and schema_quoted_len > cap:
        raise ValueError(
            f"inline schema's shell-escaped length ({schema_quoted_len} bytes) exceeds "
            f"the {cap}-byte cap -- an inline (HEADLESS_SCHEMA_FLAG={{schema}}) schema is "
            "always its own argv element regardless of HEADLESS_PROMPT")


def _schema_placeholder_kind(headless_schema_flag: str):
    """
    Which of the two schema-delivery placeholders `headless_schema_flag`
    itself declares (docs/spec.md "Correction: {schema} is inline for
    Claude, a file for Codex") -- mirrors the {prompt}/{prompt_file}
    distinction 6a already established for HEADLESS_PROMPT=arg|file, an
    existing pattern rather than a new mechanism:

      "file"   -- {schema_file} present: substituted with a PATH to a
                  schema.json file written into the run directory
                  (Codex's own --output-schema <FILE>).
      "inline" -- {schema} present (and {schema_file} is not): substituted
                  with the schema's raw JSON TEXT, shlex.quote()'d as a
                  single argv element (Claude Code's own
                  --json-schema <schema>).
      None     -- `headless_schema_flag` is falsy, OR it's set but contains
                  NEITHER placeholder -- a configuration error, checked at
                  callers' own before-anything-is-spawned validation
                  (agent_run()) and surfaced separately at roster-build
                  time (_schema_flag_config_error()), never silently
                  treated the same as "no schema flag at all".

    {schema_file} is checked first so an (unusual, not disallowed) template
    declaring both placeholders prefers the more literal/specific one.
    """
    if not headless_schema_flag:
        return None
    if "{schema_file}" in headless_schema_flag:
        return "file"
    if "{schema}" in headless_schema_flag:
        return "inline"
    return None


def _schema_flag_config_error(e):
    """
    Human-readable configuration-error message if engine `e` declares a
    HEADLESS_SCHEMA_FLAG containing NEITHER {schema} nor {schema_file} --
    an engine like this auto-detects as tier 2 in the roster (any non-empty
    HEADLESS_SCHEMA_FLAG triggers that) but would fail at the first real
    tier-2 lead call. docs/spec.md's own correction requires this be
    "reported as such at roster-build time, not at the first tier-2 lead
    call" -- see roster() and _cli_team_start(), both of which surface this.
    None if there's no schema flag at all (nothing to misconfigure), or if
    it correctly declares one of the two recognized placeholders.
    """
    flag = e.headless_schema_flag
    if not flag:
        return None
    if _schema_placeholder_kind(flag) is not None:
        return None
    return (f"HEADLESS_SCHEMA_FLAG={flag!r} declares neither {{schema}} (inline JSON "
           "text, e.g. Claude Code's --json-schema) nor {schema_file} (a file path, "
           "e.g. Codex's --output-schema) -- tier-2 lead calls for this engine will fail")


def _resolve_schema_fragment(headless_schema_flag: str, schema: dict, schema_path: str) -> str:
    """
    Builds the actual flag+value fragment to splice into HEADLESS_CMD's own
    {schema} token, choosing inline-JSON-text vs. file-path substitution
    based on which placeholder `headless_schema_flag` ITSELF declares
    (docs/spec.md "Correction: {schema} is inline for Claude, a file for
    Codex") -- a separate, two-stage str.replace() pipeline from HEADLESS_
    CMD's own {schema} token, the same two-stage shape {resume}/
    HEADLESS_RESUME's {session_id} substitution already uses. Only ever
    called once _schema_placeholder_kind() has already confirmed the
    template is valid (agent_run()'s own before-anything-is-spawned
    validation raises first), so this never needs to handle the "neither
    placeholder" case itself.
    """
    if "{schema_file}" in headless_schema_flag:
        return headless_schema_flag.replace("{schema_file}", schema_path)
    # {schema}: inline JSON text, shlex.quote()'d as a single argv element
    # -- this string is about to be spliced into a command STRING
    # (HEADLESS_CMD), then re-tokenized by shlex.split() in
    # _build_headless_argv() below, so it must survive that round-trip as
    # ONE token, same discipline _validate_prompt_size()'s own arg-mode
    # prompt check already applies for the identical reason.
    return headless_schema_flag.replace("{schema}", shlex.quote(json.dumps(schema)))


# The three tokens _build_headless_argv() ever substitutes inside
# HEADLESS_CMD. A single compiled alternation, matched in ONE pass -- see
# _substitute_headless_tokens()'s own docstring for why this must be one
# pass and not a sequence of separate str.replace() calls
# (docs/test-review.md Finding #1, sub-spec 6c round 3).
_HEADLESS_CMD_TOKENS = ("{resume}", "{schema}", "{prompt_file}")
_HEADLESS_CMD_TOKEN_RE = re.compile("|".join(re.escape(t) for t in _HEADLESS_CMD_TOKENS))


def _substitute_headless_tokens(cmd: str, mapping: dict) -> str:
    """
    Single-pass, SIMULTANEOUS substitution of HEADLESS_CMD's own {resume}/
    {schema}/{prompt_file} tokens -- one re.sub() scan over the ORIGINAL
    `cmd` text, never a sequence of separate str.replace() calls.

    Why this matters (docs/test-review.md Finding #1, confirmed live by two
    independent repros): a sequence of separate str.replace() calls over
    the same growing string lets a LATER pass rescan text a PRIOR pass just
    inserted. A schema's own JSON text (arbitrary strings in e.g. a
    `description` field) that happens to contain the literal substring
    "{prompt_file}" got silently rewritten to the real rundir prompt-file
    path by a later {prompt_file} pass -- corrupting the schema with no
    exception, no log entry. A `session_id` (sourced from an engine CLI's
    own JSON output -- semi-trusted, not developer-controlled) containing
    the literal substring "{schema}" got rescanned and spliced with schema
    JSON by a later {schema} pass, corrupting the resume argv. Neither case
    is fixable by reordering the three passes: whichever token is
    substituted FIRST is vulnerable to every pass that comes after it, and
    since more than one of these values (schema JSON, session_id) can
    plausibly contain a literal "{other_token}"-shaped substring, no single
    ordering protects all of them -- moving one to the front only relocates
    the bug onto a different token.

    re.sub() with a replacement FUNCTION (below) finds every match against
    the ORIGINAL string in one linear left-to-right scan and splices in
    each match's replacement value verbatim -- it never re-enters or
    rescans a replacement value for further matches. That is what actually
    closes this class of bug structurally (the same "remove the class of
    bug, don't tune around one instance" discipline 6a's own script-file
    fix already established for a different problem), not a "safer"
    ordering of the same sequential-replace shape.

    `mapping` supplies the value for whichever of the three literal tokens
    apply THIS call. A key simply ABSENT from `mapping` -- e.g.
    "{prompt_file}" when HEADLESS_PROMPT != "file" -- leaves that token,
    if one happens to literally appear in HEADLESS_CMD, completely
    UNTOUCHED rather than incidentally substituted in a mode where it was
    never valid: explicit, not incidental (docs/test-review.md's own
    explicit ask).
    """
    def _sub(m):
        return mapping.get(m.group(0), m.group(0))
    return _HEADLESS_CMD_TOKEN_RE.sub(_sub, cmd)


def _build_headless_argv(engine, prompt: str, session_id, prompt_path: str = None,
                         schema: dict = None, schema_path: str = None) -> list:
    """
    Renders engine.headless_cmd into a list of argv tokens. {resume} (and,
    inside HEADLESS_RESUME, {session_id}), {prompt_file}, and {schema} are
    all substituted with plain str.replace()-shaped literal-text
    substitution -- never str.format() -- so a HEADLESS_SCHEMA_FLAG
    carrying a literal JSON Schema (full of {/}) can't break this (6c;
    docs/spec.md §1). The prompt itself is appended as its own list
    element, never string-interpolated, when HEADLESS_PROMPT=arg (Claude
    Code: -p is a boolean "print mode" flag; the query is a positional
    argument).

    All three tokens are resolved to their final values FIRST, then
    substituted into `engine.headless_cmd` together, in ONE
    _substitute_headless_tokens() pass (docs/test-review.md Finding #1) --
    never as a sequence of separate substitutions over the same growing
    string, which would let a later substitution rescan (and potentially
    corrupt) text an earlier one just inserted.

    HEADLESS_CMD's own {schema} token (docs/spec.md §3) is mapped to
    _resolve_schema_fragment()'s output when `schema` is given AND the
    engine declares a schema flag, or to "" otherwise -- same empty-
    string-by-default pattern {resume} already uses. {prompt_file} is only
    ever placed in the mapping when `engine.headless_prompt == "file"` --
    in `arg`/`stdin` mode it is simply never a substitution target, so a
    literal "{prompt_file}" appearing anywhere in HEADLESS_CMD in one of
    those modes is left untouched, not silently substituted in a mode
    where it was never valid.
    """
    mapping = {"{resume}": _resume_fragment(engine, session_id)}
    if schema is not None and engine.headless_schema_flag:
        mapping["{schema}"] = _resolve_schema_fragment(engine.headless_schema_flag, schema, schema_path)
    else:
        mapping["{schema}"] = ""
    if engine.headless_prompt == "file":
        mapping["{prompt_file}"] = prompt_path
    cmd = _substitute_headless_tokens(engine.headless_cmd, mapping)
    argv = shlex.split(cmd)
    if engine.headless_prompt == "arg":
        argv.append(prompt)
    return argv


def _build_script(argv: list, headless_prompt: str, prompt_path: str,
                   out_path: str, err_path: str, pid_path: str, rc_path: str) -> str:
    """
    Builds the shell script text run by `bash -l <script_path>`
    (agent_run() writes the returned string to RUNDIR/run.sh; see that
    function). Every dynamic value is individually shlex.quote()'d before
    being spliced into this string, which IS interpreted as shell syntax.
    `bash -l <file>` sources the same login-shell startup files
    (`/etc/profile`, `~/.bash_profile`/`~/.profile`) a login `bash -c`
    would, which is what makes RUN_USER's own PATH extensions (nvm/pipx/
    etc.) findable. Backgrounding the engine (`&`) and capturing `$!`
    immediately gives a real, targetable PID for the engine process
    specifically (not tmux's pane leaf, which is `bash` itself -- it can't
    exec-replace itself since it still has the trailing `echo` to run),
    which is what makes a clean, signal-generic stop (see _send_signal())
    possible at all.
    """
    script = shlex.join(argv)
    if headless_prompt == "stdin":
        script += " < " + shlex.quote(prompt_path)
    script += f" >{shlex.quote(out_path)} 2>{shlex.quote(err_path)}"
    script += f" & echo $! >{shlex.quote(pid_path)}; wait $!; echo $? >{shlex.quote(rc_path)}"
    return script


# ─── normalized event envelope translation (docs/story.md §4.1) ──────────
# `kind` is the closed set the (future) overwatch UI renders: message,
# tool_use, tool_result, status, error, handoff. `meta` carries whatever the
# engine gave us verbatim (plus a few of our own bookkeeping keys, e.g.
# `session_id` where we found one), so nothing is lost and a new engine
# needs no UI change. Every translator below returns a list of
# (kind, text, meta) tuples -- zero, one, or several per native line.

def _translate_claude(native: dict) -> list:
    t = native.get("type")
    sid = native.get("session_id")
    if t == "assistant":
        events = []
        for block in (native.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                events.append(("message", block.get("text", ""),
                               {"native_type": "assistant", "block_type": "text", "session_id": sid}))
            elif btype == "tool_use":
                events.append(("tool_use", block.get("name", ""),
                               {"native_type": "assistant", "block_type": "tool_use",
                                "tool_use_id": block.get("id"), "input": block.get("input"),
                                "session_id": sid}))
            elif btype == "thinking":
                # Internal reasoning trace, not a final answer -- kept out of
                # the "message" kind so it never leaks into the assistant-text
                # fallback used for the run's own `text` field (see _Tailer).
                events.append(("status", block.get("thinking", ""),
                               {"native_type": "assistant", "block_type": "thinking", "session_id": sid}))
            else:
                events.append(("status", "",
                               {"native_type": "assistant", "block_type": btype, "session_id": sid}))
        return events
    if t == "user":
        events = []
        for block in (native.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, str):
                    text = content
                elif content is None:
                    text = ""
                else:
                    text = json.dumps(content)
                events.append(("tool_result", text,
                               {"native_type": "user", "tool_use_id": block.get("tool_use_id"),
                                "session_id": sid}))
            else:
                # Symmetric with the assistant branch's own else-clause
                # above: an unrecognized-but-present block type (e.g. a
                # plain "text" block in a user-role message) is still
                # surfaced as a status event, never silently dropped --
                # "nothing lost" (docs/story.md §4.1) applies here too
                # (docs/test-review.md Finding A).
                events.append(("status", "",
                               {"native_type": "user", "block_type": block.get("type"), "session_id": sid}))
        return events
    if t == "result":
        return [("status", native.get("result") or "",
                 {"native_type": "result", "subtype": native.get("subtype"), "session_id": sid,
                  "is_error": native.get("is_error"), "total_cost_usd": native.get("total_cost_usd")})]
    if t == "system":
        return [("status", "", {"native_type": "system", "subtype": native.get("subtype"), "session_id": sid})]
    # Any other valid-JSON type this repo hasn't seen yet (e.g.
    # rate_limit_event, observed live during Tier 3 verification) -- passed
    # through as a status event rather than silently dropped.
    return [("status", "", {"native_type": t or "unknown"})]


def _translate_codex(native: dict) -> list:
    t = native.get("type")
    if t == "thread.started":
        return [("status", "", {"native_type": t, "session_id": native.get("thread_id")})]
    if t in ("turn.started",):
        return [("status", "", {"native_type": t})]
    if t == "turn.completed":
        return [("status", "", {"native_type": t, "usage": native.get("usage")})]
    if t == "turn.failed":
        return [("error", (native.get("error") or {}).get("message", ""), {"native_type": t})]
    if t == "error":
        return [("error", native.get("message", ""), {"native_type": t})]
    if t in ("item.started", "item.completed"):
        item = native.get("item") or {}
        itype = item.get("type")
        completed = (t == "item.completed")
        if itype == "agent_message":
            return [("message" if completed else "status", item.get("text", ""),
                     {"native_type": t, "item_type": itype, "item_id": item.get("id")})]
        if itype == "error":
            return [("error", item.get("message", ""),
                     {"native_type": t, "item_type": itype, "item_id": item.get("id")})]
        if itype in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
            text = item.get("command") or item.get("path") or item.get("tool") or itype
            return [("tool_use" if not completed else "tool_result", text,
                     {"native_type": t, "item_type": itype, "item": item})]
        return [("status", "", {"native_type": t, "item_type": itype, "item": item})]
    return [("status", "", {"native_type": t or "unknown"})]


def _translate_safely(translate_fn, native: dict):
    """
    Boundary wrapper, not per-branch defensiveness: both translators above
    assume specific field shapes (e.g. native["message"] is itself a dict),
    which a syntactically-valid-but-differently-shaped native event can
    violate. Rather than adding isinstance()/`.get()` guards at every
    access site, this is the one place that turns any such failure into a
    clean (events, error_message) pair -- events=[] and a non-None message
    on failure -- so the caller (_Tailer._handle_line()) can degrade it to
    a single error envelope exactly like a json.loads() failure already is.
    """
    try:
        return translate_fn(native), None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


class _HeadlessReadPermissionError(Exception):
    """Raised when a headless run directory's own state files (out.rc,
    out.pid, out.jsonl) can't be *read* by SVC_USER despite being
    traversable -- an unusually strict RUN_USER umask (docs/spec.md "Edge
    cases"). Caught in _run_headless_session(), never left to crash
    agent_run() or silently masquerade as "not ready yet"."""

    def __init__(self, path):
        self.path = path
        super().__init__(f"permission denied reading {path}")


def _read_int_file(path: str):
    """Returns the int content of a small state file, None if it doesn't
    exist yet or is present-but-not-yet-fully-flushed (empty/unparseable --
    treated as "not ready", never a hard failure). Raises
    _HeadlessReadPermissionError specifically for a permission failure,
    which is a real, distinct problem rather than "not ready yet"."""
    try:
        with open(path) as f:
            s = f.read().strip()
    except FileNotFoundError:
        return None
    except PermissionError:
        raise _HeadlessReadPermissionError(path)
    except OSError:
        return None
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


class _Tailer:
    """
    Incremental translator/appender for one headless run's out_path.
    poll() re-seeks to the last-read byte offset each call (never re-reads
    from the start) and only ever processes *complete* lines for the two
    NDJSON formats -- a trailing partial line is held across polls until its
    newline arrives, except on the final poll(final=True) pass, where a
    still-incomplete trailing fragment is given one last parse attempt
    (covers an engine that omits a trailing newline on its very last event).

    HEADLESS_FORMAT=plain (aider) has no line-oriented event stream at all --
    poll() is a no-op for it; the whole captured file is read once, bounded,
    in final_text().
    """

    def __init__(self, out_path: str, headless_format: str, log_path: str, agent_name: str):
        self.out_path = out_path
        self.format = headless_format
        self.log_path = log_path
        self.agent_name = agent_name
        self.offset = 0
        self.partial = b""
        self.seq = 0
        self.event_count = 0
        self.truncated = False
        self.session_id = None
        self._claude_result_text = None
        self._claude_assistant_texts = []
        self._codex_message_texts = []
        self._codex_turn_completed = False
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def poll(self, final: bool = False) -> None:
        if self.format == "plain":
            return
        try:
            with open(self.out_path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return
        except PermissionError:
            raise _HeadlessReadPermissionError(self.out_path)
        self.offset += len(data)
        buf = self.partial + data
        lines = buf.split(b"\n")
        self.partial = lines.pop()
        if final and self.partial:
            lines.append(self.partial)
            self.partial = b""
        for raw in lines:
            self._handle_line(raw)

    def _handle_line(self, raw: bytes) -> None:
        if not raw.strip() or self.truncated:
            return
        if self.event_count >= TEAM_HEADLESS_MAX_EVENTS or len(raw) > TEAM_HEADLESS_MAX_LINE_BYTES:
            self.truncated = True
            self._append("status", "stream truncated (event/line cap reached)",
                         {"native_type": "_truncation_notice"})
            return
        try:
            native = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            self._append("error", "malformed line (json.loads failed)", {"raw_bytes": len(raw)})
            return
        if not isinstance(native, dict):
            self._append("error", "malformed line (not a JSON object)", {"raw_bytes": len(raw)})
            return
        # Boundary guard, same discipline as the json.loads() failure just
        # above: the translators assume a shape (e.g. native["message"] is a
        # dict), and a syntactically-valid-but-differently-shaped native
        # event (an upstream engine CLI version change, a proxy/wrapper,
        # any other schema drift this module doesn't control) can raise
        # AttributeError/TypeError/etc. from deep inside them. That must
        # degrade to one error event, not escape agent_run() uncaught --
        # untrusted external output can never crash the caller here,
        # whether the failure is "not JSON" or "JSON, but the wrong shape."
        if self.format == "claude-stream-json":
            events, translate_error = _translate_safely(_translate_claude, native)
        elif self.format == "codex-jsonl":
            events, translate_error = _translate_safely(_translate_codex, native)
            if translate_error is None and native.get("type") == "turn.completed":
                self._codex_turn_completed = True
        else:
            events, translate_error = [], None
        if translate_error is not None:
            self._append("error", f"translator failed on unexpected event shape ({translate_error})",
                         {"native_type": native.get("type"), "raw_bytes": len(raw)})
            return
        for kind, text, meta in events:
            if meta.get("session_id"):
                self.session_id = meta["session_id"]
            if self.format == "claude-stream-json":
                if meta.get("native_type") == "result":
                    self._claude_result_text = text
                elif kind == "message" and meta.get("native_type") == "assistant":
                    self._claude_assistant_texts.append(text)
            elif self.format == "codex-jsonl":
                if kind == "message" and meta.get("item_type") == "agent_message":
                    self._codex_message_texts.append(text)
            self._append(kind, text, meta)

    def _append(self, kind: str, text: str, meta: dict) -> None:
        self.seq += 1
        self.event_count += 1
        envelope = {"ts": _now_iso(), "agent": self.agent_name, "seq": self.seq,
                    "kind": kind, "text": text or "", "meta": meta or {}}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(envelope) + "\n")

    def final_text(self) -> str:
        if self.format == "claude-stream-json":
            if self._claude_result_text is not None:
                return self._claude_result_text
            return "".join(self._claude_assistant_texts)
        if self.format == "codex-jsonl":
            if self._codex_turn_completed and self._codex_message_texts:
                return self._codex_message_texts[-1]
            return "".join(self._codex_message_texts)
        if self.format == "plain":
            return self._plain_text()
        return ""

    def _plain_text(self) -> str:
        try:
            with open(self.out_path, "rb") as f:
                data = f.read(TEAM_HEADLESS_MAX_LINE_BYTES + 1)
        except FileNotFoundError:
            return ""
        except PermissionError:
            raise _HeadlessReadPermissionError(self.out_path)
        if len(data) > TEAM_HEADLESS_MAX_LINE_BYTES:
            self.truncated = True
            data = data[:TEAM_HEADLESS_MAX_LINE_BYTES]
        text = data.decode("utf-8", "replace")
        if text and self.event_count == 0:
            self._append("message", text, {"native_type": "plain"})
        return text


def _result(*, ok, text, session_id, exit_code, cancelled, cancel_reason,
            event_count, truncated, log_path, stderr_tail, error) -> dict:
    return {"ok": ok, "text": text, "session_id": session_id, "exit_code": exit_code,
            "cancelled": cancelled, "cancel_reason": cancel_reason, "event_count": event_count,
            "truncated": truncated, "log_path": log_path, "stderr_tail": stderr_tail, "error": error}


def _read_stderr_tail(err_path: str, max_bytes: int) -> str:
    try:
        size = os.path.getsize(err_path)
        with open(err_path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        return data.decode("utf-8", "replace")
    except FileNotFoundError:
        return ""
    except PermissionError:
        raise _HeadlessReadPermissionError(err_path)


def _send_signal(session: str, pid: int, sig_name: str) -> None:
    """
    Signaling a RUN_USER-owned PID from SVC_USER directly (os.kill()) would
    fail with EPERM -- cross-UID signals need root or the same UID, and the
    only standing privilege this module has is TMUX. So this reuses exactly
    that: a second, throwaway, self-cleaning tmux session whose entire job
    is one line, run as RUN_USER (so the signal permission check passes).
    No new sudoers surface -- this is `tmux new-session` running a different
    one-line script, nothing tmux itself wasn't already whitelisted for.
    """
    subprocess.run(TMUX + ["new-session", "-d", "-s", f"{session}-kill",
                           "bash", "-lc", f"kill -{sig_name} {pid}"],
                   capture_output=True)


def _sweep_stale_runs() -> None:
    """
    Opportunistic, at the top of every agent_run() call -- not a background
    thread/timer (docs/spec.md "Stale-run sweep"). Covers app.py's/
    teams.py's own process being restarted mid-run; the fuller "service
    restart while a team runs" story is 6d's job, this only guarantees no
    leaked directories. A no-op (no TMUX/subprocess.run call at all) unless
    TEAM_STATE_DIR/_headless already has entries -- important for Tier 1
    tests, which assert nothing is spawned for validation failures.
    """
    headless_root = os.path.join(TEAM_STATE_DIR, "_headless")
    if not os.path.isdir(headless_root):
        return
    now = time.time()
    for run_id in os.listdir(headless_root):
        run_dir = os.path.join(headless_root, run_id)
        try:
            age = now - os.stat(run_dir).st_mtime
        except OSError:
            continue
        if age < TEAM_HEADLESS_STALE_RUN_TTL_SECONDS:
            continue
        session = f"switchboard-headless-{run_id}"
        subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)
        if not tmux_has(session):
            shutil.rmtree(run_dir, ignore_errors=True)


def _run_headless_session(*, session: str, out_path: str, err_path: str, pid_path: str,
                          rc_path: str, log_path: str, headless_format: str, timeout: float,
                          agent_name: str, cancel_event: threading.Event = None) -> dict:
    """
    Startup confirmation, tailing loop, completion-ordering, and
    cancellation escalation (docs/spec.md §4). Assumes the tmux session has
    already been created by the caller; does not create or tear it down
    itself (agent_run() owns that, so cleanup happens in one place
    regardless of which branch below returns).

    `cancel_event` (backlog item 6d part 2a, docs/spec.md §7): optional,
    keyword-only, default None -- every existing caller unaffected. Checked
    on the loop's own existing polling cadence as a SECOND trigger for the
    identical TERM->KILL->kill-session escalation ladder the timeout
    already drives (reason "stopped" instead of "timeout"/"external") --
    not a new ladder, not a new code path.
    """
    deadline = time.time() + 5.0
    pid = None
    try:
        while time.time() < deadline:
            pid = _read_int_file(pid_path)
            if pid is not None:
                break
            time.sleep(0.05)
    except _HeadlessReadPermissionError as e:
        return _result(ok=False, text="", session_id=None, exit_code=None, cancelled=False,
                      cancel_reason=None, event_count=0, truncated=False, log_path=log_path,
                      stderr_tail="", error=f"permission denied reading {e.path} -- check "
                      "RUN_USER's umask (docs/ADDING_AN_ENGINE.md)")
    if pid is None:
        # bash -lc itself failed before ever backgrounding anything (a bug
        # in our own generated script, a missing cwd, bash itself
        # unavailable for RUN_USER) -- an agent_run()-side failure, not an
        # engine failure.
        return _result(ok=False, text="", session_id=None, exit_code=None, cancelled=False,
                      cancel_reason=None, event_count=0, truncated=False, log_path=log_path,
                      stderr_tail="", error="headless session failed to start")

    tailer = _Tailer(out_path, headless_format, log_path, agent_name)
    start = time.time()
    cancel_reason = None
    escalation = None  # None -> "term" -> "kill" -> "kill-session"
    stage_sent_at = None

    def _finish(exit_code, cancelled, reason):
        try:
            tailer.poll(final=True)
        except _HeadlessReadPermissionError:
            pass  # best-effort final flush; the permission problem was
                   # already surfaced by an earlier poll() in the loop below
        stderr_tail = _read_stderr_tail(err_path, TEAM_HEADLESS_STDERR_TAIL_BYTES)
        ok = (exit_code == 0)
        error = None
        if not ok and not cancelled:
            error = stderr_tail.strip() or (
                f"engine exited with code {exit_code}" if exit_code is not None else "unknown failure")
        return _result(ok=ok, text=tailer.final_text(), session_id=tailer.session_id,
                       exit_code=exit_code, cancelled=cancelled, cancel_reason=reason,
                       event_count=tailer.event_count, truncated=tailer.truncated,
                       log_path=log_path, stderr_tail=stderr_tail, error=error)

    try:
        while True:
            tailer.poll()
            rc = _read_int_file(rc_path)
            if rc is not None:
                cancelled = rc >= 128 and (rc - 128) in _SIGNAL_EXIT_NUMBERS
                # A signal-shaped exit code we didn't ourselves initiate (no
                # escalation ever ran) is still a cancellation -- someone
                # else sent it (a future "stop team" action, an operator's
                # own `kill`, docs/spec.md §4) -- classified the same way,
                # just with cancel_reason="external" instead of "timeout".
                reason = cancel_reason or ("external" if cancelled else None)
                return _finish(rc, cancelled, reason)
            if not tmux_has(session):
                # Session ended without ever recording an exit code -- the
                # whole tmux server was killed, kill-session was called
                # externally bypassing this module's own escalation path,
                # disk failure mid-write, etc. Never optimistically assumed
                # clean (docs/spec.md §4 "Completion detection").
                return _finish(None, True, cancel_reason)
            now = time.time()
            if escalation is None and cancel_event is not None and cancel_event.is_set():
                cancel_reason = "stopped"
                _send_signal(session, pid, "TERM")
                escalation, stage_sent_at = "term", now
            elif escalation is None and (now - start) >= timeout:
                cancel_reason = "timeout"
                _send_signal(session, pid, "TERM")
                escalation, stage_sent_at = "term", now
            elif escalation == "term" and (now - stage_sent_at) >= TEAM_HEADLESS_KILL_GRACE_SECONDS:
                _send_signal(session, pid, "KILL")
                escalation, stage_sent_at = "kill", now
            elif escalation == "kill" and (now - stage_sent_at) >= TEAM_HEADLESS_KILL_GRACE_SECONDS:
                subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)
                escalation, stage_sent_at = "kill-session", now
            elif escalation == "kill-session" and (now - stage_sent_at) >= TEAM_HEADLESS_KILL_GRACE_SECONDS:
                # Last resort exhausted and tmux itself is still not gone --
                # stop waiting rather than hang indefinitely; classified the
                # same as any other missing-rc case.
                return _finish(None, True, cancel_reason)
            time.sleep(TEAM_HEADLESS_POLL_SECONDS)
    except _HeadlessReadPermissionError as e:
        if tmux_has(session):
            subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)
        return _result(ok=False, text="", session_id=tailer.session_id, exit_code=None,
                      cancelled=False, cancel_reason=None, event_count=tailer.event_count,
                      truncated=tailer.truncated, log_path=log_path, stderr_tail="",
                      error=f"permission denied reading {e.path} -- check RUN_USER's umask "
                      "(docs/ADDING_AN_ENGINE.md)")


def agent_run(engine: str, workdir: str, prompt: str, *,
             session_id: str = None,
             timeout: float = TEAM_HEADLESS_TIMEOUT_SECONDS,
             log_path: str = None,
             schema: dict = None,
             cancel_event: threading.Event = None) -> dict:
    """
    Runs exactly one bounded, non-interactive turn of `engine` against
    `workdir`, resumed by `session_id` if given. Never raises for anything
    that happens *after* a tmux session is created (a failed/killed/
    unauthenticated engine surfaces as ok=False with a real exit_code or a
    cancellation classification) -- ValueError is reserved for validation
    failures caught before anything is spawned (docs/spec.md "Edge cases").

    `schema` (backlog item 6c, docs/spec.md §7): optional, keyword-only,
    default None -- every existing caller (the `run` CLI subcommand, every
    6a test) is byte-for-byte unaffected. When given, raises ValueError
    *before spawning anything* if `eng.headless_schema_flag` is unset (same
    before-anything-is-spawned discipline `_resume_fragment()` already has
    for an unsupported session_id) -- an engine can't be used as a tier-2
    lead if it never declared a schema flag -- or if it declares one that
    contains NEITHER {schema} nor {schema_file} (docs/spec.md "Correction:
    {schema} is inline for Claude, a file for Codex"; see
    _schema_placeholder_kind()). `schema` is always written to
    `rundir/schema.json` (chmod 0o644, exactly parallel to the existing
    prompt-file handling), regardless of whether the engine's own delivery
    mode actually uses that file -- harmless, and covered by the same
    unconditional `finally: shutil.rmtree(rundir, ...)` cleanup below --
    and substituted into HEADLESS_CMD's {schema} token via
    `_build_headless_argv()`/`_resolve_schema_fragment()` as either that
    path or the schema's own inline JSON text, depending on which
    placeholder the engine declared.

    `cancel_event` (backlog item 6d part 2a, docs/spec.md §7): optional,
    keyword-only, default None -- every existing caller (the `run` CLI
    subcommand, every 6a/6c/6d-part-1 test) is byte-for-byte unaffected.
    Passed straight through to `_run_headless_session()`.
    """
    _sweep_stale_runs()

    engines = load_engines()
    eng = engines.get(engine)
    if eng is None or not eng.headless_enabled:
        raise ValueError(f"engine '{engine}' is unknown or not headless-enabled")
    if not os.path.isdir(workdir):
        raise ValueError(f"workdir does not exist or is not a directory: {workdir}")
    _resume_fragment(eng, session_id)  # raises ValueError as needed, before anything is spawned
    if schema is not None and not eng.headless_schema_flag:
        raise ValueError(
            f"engine '{engine}' does not support schema-constrained output "
            "(no HEADLESS_SCHEMA_FLAG)")
    schema_kind = _schema_placeholder_kind(eng.headless_schema_flag) if schema is not None else None
    if schema is not None and schema_kind is None:
        raise ValueError(f"engine '{engine}': {_schema_flag_config_error(eng)}")
    schema_text = json.dumps(schema) if schema_kind == "inline" else None
    _validate_prompt_size(eng.headless_prompt, prompt, schema_text)

    run_id = _run_id()
    rundir = os.path.join(TEAM_STATE_DIR, "_headless", run_id)
    session = f"switchboard-headless-{run_id}"

    # Everything from here on is fallible (filesystem, subprocess) and must
    # not leak `rundir` or a tmux session on any exception, including one
    # this function doesn't anticipate -- hence everything fallible lives
    # inside this one try, cleaned up unconditionally by the finally below.
    try:
        os.makedirs(rundir, exist_ok=True)
        os.chmod(rundir, 0o711)

        out_path = os.path.join(rundir, "out.jsonl")
        err_path = os.path.join(rundir, "out.err")
        pid_path = os.path.join(rundir, "out.pid")
        rc_path = os.path.join(rundir, "out.rc")
        prompt_path = os.path.join(rundir, "prompt")

        # prompt/script files are written by SVC_USER but must be *read* by
        # RUN_USER (via the tmux pane's bash process) -- chmod explicitly
        # rather than relying on SVC_USER's ambient umask to leave them
        # world-readable, same reasoning as rundir's own explicit 0o711.
        if eng.headless_prompt in ("stdin", "file"):
            with open(prompt_path, "wb") as f:
                f.write(prompt.encode("utf-8"))
            os.chmod(prompt_path, 0o644)

        schema_path = None
        if schema is not None:
            schema_path = os.path.join(rundir, "schema.json")
            with open(schema_path, "w") as f:
                f.write(json.dumps(schema))
            os.chmod(schema_path, 0o644)

        argv = _build_headless_argv(eng, prompt, session_id, prompt_path, schema, schema_path)
        script = _build_script(argv, eng.headless_prompt, prompt_path, out_path, err_path, pid_path, rc_path)

        # Run via `bash -l <script_path>` (not passed inline as a
        # `bash -lc <script>` argv element) so the tmux command line stays
        # small and constant-length regardless of prompt size -- see
        # _build_script()'s and _MAX_ARG_STRLEN's own docstrings for why.
        script_path = os.path.join(rundir, "run.sh")
        with open(script_path, "w") as f:
            f.write(script + "\n")
        os.chmod(script_path, 0o644)

        if log_path is None:
            os.makedirs(os.path.join(TEAM_STATE_DIR, "_adhoc"), exist_ok=True)
            log_path = os.path.join(TEAM_STATE_DIR, "_adhoc",
                                    f"{engine}-{int(time.time())}-{secrets.token_hex(4)}.jsonl")
        else:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

        try:
            subprocess.run(TMUX + ["new-session", "-d", "-s", session, "-c", workdir,
                                   "bash", "-l", script_path])
        except OSError as e:
            # Defense in depth beyond _validate_prompt_size()'s own
            # shlex.quote()'d-length check -- e.g. an unusually long
            # workdir path pushing tmux's own (small, constant-ish) command
            # line over some other limit. Never an unhandled crash; same
            # well-formed-failure shape as the "bash -lc fails before
            # backgrounding anything" edge case already handles for a
            # session that starts but never gets as far as writing out.pid.
            return _result(ok=False, text="", session_id=None, exit_code=None, cancelled=False,
                          cancel_reason=None, event_count=0, truncated=False, log_path=log_path,
                          stderr_tail="", error=f"failed to start headless session: {e}")

        return _run_headless_session(
            session=session, out_path=out_path, err_path=err_path, pid_path=pid_path,
            rc_path=rc_path, log_path=log_path, headless_format=eng.headless_format,
            timeout=timeout, agent_name=engine, cancel_event=cancel_event)
    finally:
        # Durable artifact is log_path; the raw out/err/pid/rc/prompt
        # plumbing under RUNDIR is thrown away regardless of outcome
        # (success, cancelled, or errored) -- SVC_USER owns RUNDIR
        # regardless of which user (RUN_USER, via the tmux pane) wrote the
        # individual files inside it, no sticky bit involved. Safe even if
        # `rundir` was never (fully) created -- shutil.rmtree(ignore_errors)
        # and a not-found `has-session` check are both no-ops in that case.
        shutil.rmtree(rundir, ignore_errors=True)
        if tmux_has(session):
            subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)


# ─── grounding (docs/story.md §4.3; docs/spec.md 6b) ──────────────────────
# Auto-discovers a project's own documentation, builds a hard-byte-capped
# digest for a lead's system prompt, and answers fact_check(claim,
# grounding) with matching passages or an explicit "no supporting passage
# found". Pure reads only -- no function below this marker ever opens a
# file in a write/append/create mode (neither via a write-mode `open()`
# call nor via a write-capable `os.open()` flag combination) or calls a
# mutating os/shutil function (see tests/test_teams_grounding.py's
# runtime-monkeypatch and static-AST-scan assertions of exactly that).
#
# Every real file this section reads goes through exactly one
# `os.open()`/read/`os.close()` per file -- see _open_grounding_candidate()
# and _read_grounding_candidate() below for why that (rather than a
# realpath()-then-open()-by-path-string pattern, possibly repeated) is what
# actually closes a symlink-swap TOCTOU race, not just a faster re-check of
# the same racy pattern (docs/test-review.md Defect 2, first round).

# Fixed backstop on any single grounding-file read, independent of
# TEAM_GROUNDING_MAX_BYTES -- deliberately not an env var (docs/spec.md 6b
# §1): an operator misconfiguring the digest cap can never make a single
# file read larger than this.
_GROUNDING_READ_CAP_BYTES = 2 * 1024 * 1024

# Defensive bound on the intermediate per-file heading list, independent of
# build_digest()'s own byte truncation -- so a pathological file with
# thousands of #-prefixed lines can't blow up an intermediate list before
# truncation gets a chance to run.
_GROUNDING_MAX_HEADINGS_PER_FILE = 20

# Cap on fact_check()'s own returned match list.
_GROUNDING_FACT_CHECK_MAX_MATCHES = 5

# Bounds on fact_check()'s matching UNIT (docs/spec.md 6b.1) -- a "block" of
# consecutive non-blank lines joined into one matchable region, replacing
# 6b's single-physical-line matcher. Both fixed, deliberately **not**
# environment-configurable, same rationale as _GROUNDING_READ_CAP_BYTES
# above: they are the precision guarantee (an unbounded block is how
# accidental co-occurrence -- and therefore a false "confirmation" -- would
# silently creep in), not an operator preference an operator could loosen.
#
# Round-1 correction (docs/spec.md "Round-1correction", docs/test-review.md):
# the original 12/1500 shipped with round 1 were ~6x too wide -- a block is
# a WRAP-JOINED UNIT (one hard-wrapped sentence, occasionally two), not an
# arbitrary run of lines, and 12 lines carries no co-occurrence signal at
# all. Corrected to 3 lines / 400 chars, sized to the actual defect being
# fixed (a sentence wrapped across two, occasionally three, physical
# lines) and nothing beyond it.
_GROUNDING_BLOCK_MAX_LINES = 3
_GROUNDING_BLOCK_MAX_CHARS = 400

# A block also ends after any line whose stripped text ends in sentence-
# terminal punctuation (optionally followed by a closing quote/bracket/
# emphasis marker) -- now including `:` (docs/spec.md "Round-1 correction"),
# even short of either bound above and even with no blank line separating
# it from the next line. docs/spec.md 6b.1's own block definition is
# literally "delimited by blank lines" -- but this repo's own real
# documentation (see docs/ARCHITECTURE.md) uses TIGHT Markdown lists (no
# blank line between sibling bullets), so blank-line-only delimiting would
# silently merge unrelated adjacent bullets into one matchable region purely
# as an artifact of list formatting -- precisely the accidental-co-occurrence
# risk 6b.1's own design section warns against. It would also regress 6b's
# own existing precision test (two independent one-line sentences with no
# blank line between them must not become one matchable block just because
# neither line is blank). This rule alone was later shown (round 1's
# testing pass) to be insufficient on its own against headings, terse
# non-sentence bullets, and code fences -- see _grounding_structural_kind()
# and the fence handling in _iter_grounding_blocks() below, and
# docs/implementation.md "Deviations from spec" for the full history.
_GROUNDING_SENTENCE_END_RE = re.compile(r"""[.!?:][)\]"'*_`]*$""")

_GROUNDING_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")
_GROUNDING_LIST_MARKER_RE = re.compile(r"^(?:[-*+]|\d+\.)\s")


def _grounding_structural_kind(stripped: str):
    """Classifies a non-blank, non-fence-delimiter stripped line as the
    start of a Markdown structural element (docs/spec.md "Round-1
    correction") -- `"heading"`, `"list"`, `"quote"`, `"table"` -- or
    `None` for ordinary prose. These are mutually exclusive prefixes; a
    line can be at most one."""
    if _GROUNDING_HEADING_RE.match(stripped):
        return "heading"
    if _GROUNDING_LIST_MARKER_RE.match(stripped):
        return "list"
    if stripped.startswith(">"):
        return "quote"
    if stripped.startswith("|"):
        return "table"
    return None

# A specific, greppable sentence used when nothing was discovered -- not an
# empty string a caller could mistake for "not yet loaded".
_GROUNDING_NO_FILES_DIGEST = (
    "No grounding files were found for this project (checked "
    "docs/ARCHITECTURE.md, docs/BACKLOG.md, CLAUDE.md/AGENTS.md, README.md)."
)

# A small, built-in stopword list for fact_check()'s claim tokenizer
# (docs/spec.md 6b §5) -- dropped before term-conjunction matching so a
# claim built entirely from these (or the empty string) is treated as
# having nothing meaningful to search for.
_GROUNDING_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in",
    "on", "and", "or", "that", "this", "it", "for", "with", "as", "at",
    "by", "from", "not", "no", "do", "does", "did", "has", "have", "had",
})

_GROUNDING_TOKEN_RE = re.compile(r"[A-Za-z0-9_']+")

# Set once, process-wide, the first time _open_grounding_candidate() detects
# /proc/self/fd is unresolvable (docs/spec.md 6b.1 follow-up 2) -- so the
# one-time stderr note fires exactly once per process, not once per rejected
# candidate (which could otherwise be up to 4 times per load_grounding()
# call, every call, for the life of the process).
_grounding_proc_warned = False


def _warn_proc_unavailable_once() -> None:
    global _grounding_proc_warned
    if _grounding_proc_warned:
        return
    _grounding_proc_warned = True
    print(
        "grounding: /proc/self/fd did not resolve (realpath() returned the "
        "literal, unresolved path) -- containment cannot be verified for any "
        "grounding candidate, so every candidate is being rejected rather "
        "than trusted. This is expected if /proc is not mounted (e.g. some "
        "minimal containers/namespaces); no path-based realpath() fallback "
        "is used here, since that would reopen the TOCTOU race closed in "
        "6b (docs/test-review.md Defect 2).",
        file=sys.stderr,
    )


def _under_workdir(path: str, workdir_real: str) -> bool:
    """True iff path's realpath is workdir_real itself or lives strictly
    beneath it. A cheap pre-check only -- see _open_grounding_candidate()'s
    own docstring for why this alone is not sufficient as the containment
    guarantee (docs/test-review.md Defect 2: a realpath()-then-open()
    pattern split across two calls left a TOCTOU window). The actual
    guarantee against that race rests on O_NOFOLLOW and the post-open fd
    check below, each tied to the specific file that gets opened rather
    than re-derived from this path string -- this pre-check's own job is
    narrower: reject an out-of-bounds candidate before os.open() is ever
    called on it at all, for the straightforward (non-racing) case."""
    real = os.path.realpath(path)
    return real == workdir_real or real.startswith(workdir_real + os.sep)


def _open_grounding_candidate(path: str, workdir_real: str):
    """
    Opens `path` exactly once and returns `(fd, None)` -- a validated, open,
    read-only file descriptor -- or `(None, reason)` if it's unusable, where
    `reason` is either `None` (the candidate simply doesn't exist -- ENOENT/
    ENOTDIR, the same silent "not present" outcome this has always had, not
    something a caller should treat as a `skipped` entry) or one of
    `"symlink"`, `"not_regular_file"`, `"out_of_bounds"`, `"unreadable"`,
    `"proc_unavailable"` (docs/spec.md 6b.1 follow-ups 1 and 2) -- a
    candidate that genuinely exists but was rejected. `_discover_and_read()`
    uses the non-None reasons to populate `load_grounding()`'s `skipped`
    list; everything else about this function (the single open()/fstat()/
    fd-based containment re-check) is unchanged from 6b. Previously,
    containment was checked once (via `_under_workdir` against the literal
    path string) and the file was then read separately, sometimes twice
    (once to decide "is this usable", once more for its kept content) --
    each read a fresh, independent open() of the same literal path, with a
    real window between "checked" and "read" for the file to be swapped
    out from under the check (Defect 2, reproduced deterministically: an
    in-bounds regular file replaced with a symlink to an out-of-bounds
    target between two reads leaked that target's content). That class of
    bug is closed here structurally, not by re-checking faster: containment
    is verified against the fd's OWN resolved path (`/proc/self/fd/<fd>`,
    which is pinned to the specific inode this fd refers to and cannot
    change out from under it after `os.open()` returns) rather than by
    re-resolving the original path string, and there is exactly one
    open() call per real file read anywhere in this module's grounding
    section -- nothing here ever validates a path and then reads it again
    later through a second, independent open() of that same path.

    `O_NOFOLLOW` rejects the final path component being a symbolic link
    outright (`ELOOP`) -- a literal filesystem symlink candidate is now
    categorically unusable, not "usable if it happens to resolve
    in-bounds": this is what makes the fd-based check airtight against a
    same-path swap-for-a-symlink landing in the gap between the cheap
    `_under_workdir` pre-check and this function's own `os.open()` call --
    if that swap happens, the open() itself fails instead of silently
    following the new symlink. `O_NONBLOCK` prevents `open()` on a named
    pipe with no writer from blocking forever (docs/test-review.md
    Defect 1) -- the same single syscall this function already makes
    handles both adversarial shapes, not two separate guards.

    The `_under_workdir` pre-check above is not redundant with the
    fd-based one: it is what lets an out-of-bounds candidate (e.g. an
    `@../../../etc/hostname`-style indirection target, which involves no
    symlink at all, so `O_NOFOLLOW` alone would not catch it) get rejected
    *before* `os.open()` is ever called on it at all -- verified directly
    by tests/test_teams_grounding.py wrapping `os.open` itself.

    `/proc/self/fd` unavailability (docs/spec.md 6b.1 follow-up 2): if
    `/proc` isn't mounted, `os.path.realpath(f"/proc/self/fd/{fd}")` can't
    resolve anything and simply hands back the same literal, unresolved
    string it was given -- which then never equals/starts-with
    `workdir_real`, so every candidate for every project would previously
    be rejected as "out of bounds" with no distinguishing signal from a
    project that genuinely has no docs. Detected explicitly here (`real ==
    fd_proc_path`, the same condition tests/test_teams_grounding.py
    simulates by monkeypatching os.path.realpath) and surfaced via a
    distinct `"proc_unavailable"` reason plus a one-time stderr note
    (_warn_proc_unavailable_once()) -- deliberately NOT a path-based
    `realpath()` fallback, which would silently reopen the exact TOCTOU
    race Defect 2 closed (validating a path string instead of the fd that
    was actually opened). Failing closed here is correct; failing closed
    *silently* was the bug.
    """
    if not _under_workdir(path, workdir_real):
        return None, "out_of_bounds"
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return None, "not_regular_file"
        # Re-verify containment against the fd's OWN resolved path (tied to
        # the specific open file description, not re-derived from `path`)
        # -- this is what closes the actual TOCTOU window between the
        # cheap `_under_workdir` pre-check above and this function's own
        # `os.open()` call an instant later: a swap landing in that window
        # (the file becoming a symlink, or an intermediate component like
        # `docs/` becoming one) is caught here even in cases O_NOFOLLOW
        # alone would not reject on its own (O_NOFOLLOW only constrains
        # the *final* path component). Verified empirically during the
        # post-review fix pass: removing either this check or O_NOFOLLOW
        # alone still leaves the exact swap-for-a-symlink repro closed
        # (each independently catches that specific shape); removing BOTH
        # is what actually reproduces the original leak -- see
        # tests/test_teams_grounding.py's PostReviewRegressionTests for the
        # regression tests this reasoning is pinned by.
        fd_proc_path = f"/proc/self/fd/{fd}"
        real = os.path.realpath(fd_proc_path)
        if real == fd_proc_path:
            os.close(fd)
            _warn_proc_unavailable_once()
            return None, "proc_unavailable"
        if real != workdir_real and not real.startswith(workdir_real + os.sep):
            os.close(fd)
            return None, "out_of_bounds"
        return fd, None
    except OSError as e:
        # Missing file, permission denied, a directory (see the fstat
        # check above for the common case, but e.g. a race making it
        # disappear entirely is still just an OSError here too),
        # ELOOP (symlink final component, or a genuine symlink loop --
        # O_NOFOLLOW turns a loop into an immediate ELOOP rather than the
        # kernel chasing it), FIFO/device/socket already handled by the
        # fstat check above without ever reaching read -- all reduce to
        # the same "unusable" outcome here, but are distinguished for the
        # `skipped` list: ENOENT/ENOTDIR is "doesn't exist" (not skip-
        # worthy -- unchanged, silent, as it's always been), ELOOP is a
        # rejected symlink, anything else (permission denied, etc.) is
        # generically "unreadable".
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if e.errno == errno.ELOOP:
            return None, "symlink"
        if e.errno in (errno.ENOENT, errno.ENOTDIR):
            return None, None
        return None, "unreadable"


def _read_grounding_candidate(path: str, workdir_real: str,
                              read_cap: int = _GROUNDING_READ_CAP_BYTES):
    """
    The one read of one real grounding file, start to finish: open once
    (validated as above), read up to `read_cap` BYTES (not characters --
    docs/test-review.md Defect 4: the module's original reliance on
    app.py's `_read_head()`, which reads up to `limit` *characters* in text
    mode, meant multi-byte-heavy content could be read up to ~4x past the
    stated byte cap; reading raw bytes directly from the fd and decoding
    only at the end fixes this as a consequence of the same restructuring,
    not a separate patch) off the already-open fd, sniff those same bytes
    for a NUL in the first 512 (the binary check -- no separate open() for
    this anymore, since the bytes are already in hand), then decode as
    UTF-8 with errors="ignore" (same tolerance app.py's `_read_head()`
    itself uses for genuinely malformed-but-not-binary text).

    Returns `(content, reason)`: `content` is `""` for any unusable
    candidate -- missing, wrong type, out-of-bounds, symlink, binary, or
    empty -- the same unified "empty means skip" rule as before, just now
    resting on one filesystem operation per file instead of two or three.
    `reason` passes `_open_grounding_candidate()`'s own reason straight
    through (`None` for success or "doesn't exist"; binary content is
    likewise reported as `reason=None` -- out of scope for the `skipped`
    list per docs/spec.md 6b.1's own minimum reason set, and consistent
    with this module's pre-existing "empty/missing/unusable" unification).
    """
    fd, reason = _open_grounding_candidate(path, workdir_real)
    if fd is None:
        return "", reason
    try:
        chunks = []
        remaining = read_cap
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if b"\x00" in raw[:512]:
        return "", None
    return raw.decode("utf-8", errors="ignore"), None


def _discover_and_read(workdir: str, read_cap: int = _GROUNDING_READ_CAP_BYTES):
    """
    The one place that both decides which of the four candidate sources are
    present *and* fetches their content -- every real underlying file is
    opened and read exactly once here (see _open_grounding_candidate()'s
    docstring for why that matters). `discover_grounding_files()` and
    `load_grounding()` are both thin wrappers around this, so
    `load_grounding()` never re-reads a path `discover_grounding_files()`
    (or this function, on a separate call) already validated.

    Returns `(entries, skipped)`: `entries` is unchanged from 6b (the list
    of usable `{"label", "path", "content"}` dicts). `skipped` (docs/spec.md
    6b.1 follow-up 1) is a new list of `{"label", "relpath", "reason"}`
    dicts, one per candidate that genuinely exists but was rejected --
    populated straight from `_read_grounding_candidate()`'s own `reason`,
    never for a candidate that simply doesn't exist (that stays silent, as
    it always has been -- see `_open_grounding_candidate()`'s docstring).
    `relpath` is the candidate's own path relative to `workdir` (the
    `@target` indirection's target for CLAUDE.md/AGENTS.md when that's what
    was actually rejected, matching `entries`' own "what was actually read"
    convention).
    """
    workdir_real = os.path.realpath(workdir)
    entries = []
    skipped = []

    def _read(label, relpath, path):
        content, reason = _read_grounding_candidate(path, workdir_real, read_cap)
        if reason is not None:
            skipped.append({"label": label, "relpath": relpath, "reason": reason})
        return content

    for relpath in ("docs/ARCHITECTURE.md", "docs/BACKLOG.md"):
        path = os.path.join(workdir, relpath)
        content = _read(relpath, relpath, path)
        if content:
            entries.append({"label": relpath, "path": path, "content": content})

    for name in ("CLAUDE.md", "AGENTS.md"):
        path = os.path.join(workdir, name)
        text = _read(name, name, path)
        if not text:
            continue
        stripped = text.strip()
        if stripped.startswith("@") and len(stripped.splitlines()) == 1:
            target_relpath = stripped[1:]
            target_path = os.path.join(workdir, target_relpath)
            target_content = _read(name, target_relpath, target_path)
            if target_content:
                entries.append({"label": name, "path": target_path, "content": target_content})
                break
            continue  # target missing/unusable/out-of-bounds
        entries.append({"label": name, "path": path, "content": text})
        break

    for name in ("README.md", "Readme.md", "readme.md", "README"):
        path = os.path.join(workdir, name)
        content = _read("README.md", name, path)
        if content:
            entries.append({"label": "README.md", "path": path, "content": content})
            break

    return entries, skipped


def discover_grounding_files(workdir: str) -> list:
    """
    Finds whichever of docs/ARCHITECTURE.md, docs/BACKLOG.md,
    CLAUDE.md/AGENTS.md (first found wins), README.md (casing variants,
    first found wins) are present and usable under `workdir`, in that fixed
    order. Mirrors app.py's _gather_project_context() matching rules in
    full, including the one-line `@target` indirection for CLAUDE.md/
    AGENTS.md (app/app.py:436) -- except the returned `path` for that case
    is the resolved target, not the literal CLAUDE.md/AGENTS.md path
    (docs/spec.md 6b §2), since that target is what's actually read and
    what a fact_check file:line must point a human at. A standalone call to
    this function reads each real file once, on its own, for its own
    purpose (deciding inclusion) -- calling it before also calling
    load_grounding() means the same file is read twice across the two
    *separate* calls, but each read is independently a single, validated
    open() (see _open_grounding_candidate()), so this is not the TOCTOU
    class of bug Defect 2 was: load_grounding() below never chains off of
    a call to this function's own result to avoid re-reading, it always
    goes through _discover_and_read() directly, once, itself. `skipped` is
    intentionally not surfaced from this function -- only `load_grounding()`
    returns it (docs/spec.md 6b.1 follow-up 1's own scope), matching this
    function's pre-existing, unchanged two-key return shape.
    """
    entries, _skipped = _discover_and_read(workdir)
    return [{"label": e["label"], "path": e["path"]} for e in entries]


def _extract_headings(content: str) -> list:
    """
    Scans lines matching ^#{1,6}\\s+.+ (ATX headings), skipping any line
    while inside a fenced code block (toggled by a line starting with
    ``` or ~~~) so a shell comment or shebang inside a fenced example is
    never mistaken for a heading. Capped at
    _GROUNDING_MAX_HEADINGS_PER_FILE, independent of build_digest()'s own
    later byte truncation (docs/spec.md 6b §3).
    """
    headings = []
    in_fence = False
    for line in content.splitlines():
        fence_marker = line.strip()[:3]
        if fence_marker in ("```", "~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^#{1,6}\s+.+", line):
            headings.append(line.strip())
            if len(headings) >= _GROUNDING_MAX_HEADINGS_PER_FILE:
                break
    return headings


def load_grounding(workdir: str, *, max_bytes: int = TEAM_GROUNDING_MAX_BYTES,
                   read_cap: int = _GROUNDING_READ_CAP_BYTES) -> dict:
    """
    Builds one snapshot dict (docs/spec.md 6b §3 "Snapshot semantics") by
    calling _discover_and_read() directly -- exactly one open+read per real
    file, never a second read of a path some earlier call already
    validated (docs/test-review.md Defect 2's fix). The same content is
    both the source for headings/digest and the corpus a later
    fact_check() call against this returned dict searches; nothing
    re-reads from disk for that call. `skipped` (docs/spec.md 6b.1
    follow-up 1) surfaces candidates that genuinely exist but were rejected
    -- see _discover_and_read()'s own docstring for exactly what does and
    doesn't count.
    """
    files = []
    entries, skipped = _discover_and_read(workdir, read_cap)
    for entry in entries:
        content = entry["content"]
        files.append({
            "label": entry["label"],
            "path": entry["path"],
            "relpath": os.path.relpath(entry["path"], workdir),
            "headings": _extract_headings(content),
            "content": content,
            "byte_count": len(content.encode("utf-8")),
        })
    return {
        "workdir": workdir,
        "loaded_at": _now_iso(),
        "files": files,
        "skipped": skipped,
        "digest": build_digest(files, max_bytes),
        "empty": files == [],
    }


def build_digest(files: list, max_bytes: int = TEAM_GROUNDING_MAX_BYTES) -> str:
    """
    Pure, no disk I/O -- assembles headings + a per-file snippet into one
    text blob, then unconditionally hard-truncates the result to `max_bytes`
    of UTF-8-encoded output (docs/spec.md 6b §4). The per-file share
    (`per_file_budget`) is a fairness heuristic only; the final encode-
    slice-decode step is the actual safety guarantee and runs every time,
    regardless of file count/size or whether the heuristic above it is
    "right".
    """
    if not files:
        return _GROUNDING_NO_FILES_DIGEST
    if max_bytes <= 0:
        return ""

    per_file_budget = max(200, max_bytes // len(files))
    sections = []
    for f in files:
        section = f"## {f['label']}\n"
        if f["headings"]:
            section += "\n".join(f["headings"]) + "\n"
        section += f["content"][:per_file_budget]
        sections.append(section)
    text = "\n\n".join(sections)

    encoded = text.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


def _significant_terms(claim: str) -> list:
    """Lowercase, tokenize on [A-Za-z0-9_']+, drop stopwords and
    single-character tokens (docs/spec.md 6b §5 step 1). Never treats
    `claim` as a regex -- only as data fed to a fixed pattern we wrote."""
    tokens = _GROUNDING_TOKEN_RE.findall(claim.lower())
    return [t for t in tokens if t not in _GROUNDING_STOPWORDS and len(t) > 1]


def _iter_grounding_blocks(content: str) -> list:
    """
    Splits `content` into fact_check()'s matching UNIT (docs/spec.md 6b.1,
    "Round-1 correction"): a "block" is a **wrap-joined unit** -- just wide
    enough to reunite one hard-wrapped sentence, no wider -- consecutive
    non-blank lines joined with a single space (never concatenated bare --
    "...an" + "unprivileged..." must become "an unprivileged", never
    "anunprivileged", so a wrap boundary never accidentally fuses into a
    term nothing on disk actually spells out). A block ends at **any** of:

    - a blank line;
    - the previous line's stripped text ending in sentence-terminal
      punctuation (`_GROUNDING_SENTENCE_END_RE`: `.`, `!`, `?`, or `:`),
      even with no blank line separating it from the next line -- round 1's
      own fix for 6b's existing precision test and this repo's own
      tight-list Markdown, retained;
    - the **current** line being the start of a new structural element --
      a heading, list item, block quote, or table row
      (`_grounding_structural_kind()`) -- which is a hard boundary from
      whatever came before, "even mid-run" (docs/spec.md). A **heading**
      line is additionally excluded from matching entirely, never added to
      any block: unlike a list item or block quote, an ATX heading is
      always exactly one line (never has a "wrapped continuation" to
      legitimately join), and it's a title, not a prose assertion -- a
      claim quoting a heading verbatim isn't "confirmed" by the section
      title alone, and a heading can never drag unrelated following prose
      into its own block either (docs/test-review.md's own heading-merge
      attacks, round 1);
    - a fenced code-block delimiter (` ``` ` or `~~~`) -- a hard boundary
      in both directions, and the delimiter line itself is never
      matchable. Every line **between** a pair of delimiters is likewise
      excluded from every block entirely (code is not prose and cannot
      support a claim, docs/spec.md) -- not merely kept from merging, but
      never part of any block's text at all, the same treatment as a
      heading;
    - `_GROUNDING_BLOCK_MAX_LINES` lines accumulated, or the next line
      would push the joined length past `_GROUNDING_BLOCK_MAX_CHARS` --
      both fixed, non-configurable (see their own module-level comment).

    Returns a list of `{"start_line", "end_line", "text"}` dicts, 1-indexed,
    in document order. `text` is unconditionally truncated to
    `_GROUNDING_BLOCK_MAX_CHARS` before being returned -- the same
    unconditional-final-truncation pattern build_digest() already uses --
    so even a single physical line far longer than the cap (which cannot be
    split any further) never becomes an unbounded matchable region.
    """
    blocks = []
    cur_lines = []  # list of (lineno, stripped_text)
    cur_len = 0      # len(" ".join(text for _, text in cur_lines))
    in_fence = False

    def flush():
        if cur_lines:
            text = " ".join(t for _, t in cur_lines)
            blocks.append({
                "start_line": cur_lines[0][0],
                "end_line": cur_lines[-1][0],
                "text": text[:_GROUNDING_BLOCK_MAX_CHARS],
            })
        cur_lines.clear()

    for lineno, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()

        if stripped[:3] in ("```", "~~~"):
            # A fence delimiter is a hard boundary either way, and is
            # itself never matchable -- toggling in/out excludes every
            # line between a pair of delimiters from every block.
            flush()
            cur_len = 0
            in_fence = not in_fence
            continue

        if in_fence:
            continue  # fenced content is excluded from matching entirely

        if not stripped:
            flush()
            cur_len = 0
            continue

        kind = _grounding_structural_kind(stripped)
        if kind == "heading":
            # Excluded from matching entirely -- see this function's own
            # docstring for why a heading gets fenced-code-like treatment,
            # not just boundary treatment.
            flush()
            cur_len = 0
            continue
        if kind is not None:
            # list / quote / table: a hard boundary from whatever came
            # before, even mid-run -- but the marker line itself still
            # starts a normal, still-joinable block, so its own wrapped
            # continuation lines (which are not themselves markers) still
            # accumulate via the rules below.
            flush()
            cur_len = 0
        elif cur_lines:
            prev_ends_sentence = bool(_GROUNDING_SENTENCE_END_RE.search(cur_lines[-1][1]))
            projected_len = cur_len + 1 + len(stripped)
            if (prev_ends_sentence
                    or len(cur_lines) >= _GROUNDING_BLOCK_MAX_LINES
                    or projected_len > _GROUNDING_BLOCK_MAX_CHARS):
                flush()
                cur_len = 0

        cur_len += (1 if cur_lines else 0) + len(stripped)
        cur_lines.append((lineno, stripped))

    flush()
    return blocks


def fact_check(claim: str, grounding: dict, *,
              max_matches: int = _GROUNDING_FACT_CHECK_MAX_MATCHES) -> dict:
    """
    Deterministic, precision-biased textual match against the FULL per-file
    content grounding["files"][*]["content"] (not grounding["digest"]) --
    docs/spec.md 6b §5, matching unit widened to a bounded block by 6b.1
    (see _iter_grounding_blocks()). A block matches iff EVERY significant
    term of `claim` is a case-insensitive substring of it -- a strict
    conjunctive match, no scoring, no nearest-weak-match fallback: an empty
    `terms` list or zero matching blocks both simply return found=False,
    never an exception. `max_matches` still caps the returned list, but now
    counts blocks, not lines (docs/spec.md 6b.1 "Result shape").
    """
    terms = _significant_terms(claim)
    matches = []
    if terms:
        for f in grounding.get("files", []):
            for block in _iter_grounding_blocks(f.get("content", "")):
                lower = block["text"].lower()
                if all(term in lower for term in terms):
                    matches.append({
                        "label": f["label"],
                        "path": f["path"],
                        "relpath": f["relpath"],
                        "line": block["start_line"],
                        "file_line": f"{f['label']}:{block['start_line']}",
                        "text": block["text"],
                        "end_line": block["end_line"],
                    })
                    if len(matches) >= max_matches:
                        break
            if len(matches) >= max_matches:
                break
    return {"claim": claim, "found": bool(matches), "matches": matches}


# ─── AI merge-request reviewer (backlog item 8, docs/spec.md) ──────────────
# One bounded, one-shot call per PR (diff + grounding digest in, review text
# out) -- not a lead-loop tool, no board_read/board_write/ask_user, no
# tier-driven behavior (see docs/spec.md "Why roster tiers don't matter
# here"). Driven entirely from app.py's Gitea poll extension; this module
# only supplies the model-call primitive.

_AI_REVIEWER_SYSTEM = (
    "You are an AI code reviewer for a pull request. Give short, specific, "
    "comment-only advisory feedback grounded in the project's own documented "
    "conventions. You have no authority to block, approve, or merge -- never "
    "phrase anything as if you do."
)


def _build_review_prompt(pr_title: str, pr_body: str, diff_text: str, diff_truncated: bool,
                         digest: str) -> str:
    """Pure string-builder, no I/O -- unit-testable directly. Role framing +
    the grounding digest verbatim + the (possibly-truncated) diff + an
    explicit truncation notice when diff_truncated + output-format
    instructions (docs/spec.md "teams.review_pr_diff()")."""
    truncation_note = (
        "\n(Note: this diff was truncated before being sent to you -- some "
        "changes may not be reflected below. Do not claim the diff is "
        "complete.)\n" if diff_truncated else ""
    )
    return (
        "You are reviewing a pull request for consistency with this "
        "project's own documented conventions -- not a general code "
        "review. Use the project grounding digest below (its own "
        "auto-discovered docs) as your source of truth for what "
        "\"consistent\" means here.\n\n"
        f"## Project grounding\n{digest}\n\n"
        f"## Pull request\nTitle: {pr_title}\n\n"
        f"Description:\n{pr_body}\n"
        f"{truncation_note}\n"
        f"## Diff\n{diff_text}\n\n"
        "Write a short markdown review: a brief overall assessment, then "
        "specific observations citing the relevant file/hunk. This is "
        "comment-only advisory feedback -- never phrase anything as "
        "blocking, approving, requesting changes, or merging; you have no "
        "authority to do any of those."
    )


def review_pr_diff(model: dict, workdir: str, pr_title: str, pr_body: str,
                   diff_text: str, diff_truncated: bool) -> dict:
    """{"ok": True, "text": <review markdown>} or {"ok": False, "error": "..."}.

    `workdir` is the project's REAL directory (PROJECTS_DIR/<name>) -- read
    only via load_grounding()'s already read-only-guaranteed path (6b's
    monkeypatch + AST scan already prove nothing under that call path can
    write). For an `engine`-kind model, `workdir` is used ONLY to build the
    grounding digest -- the reviewing engine itself is never pointed at it
    (see below).
    """
    digest = load_grounding(workdir)["digest"]
    prompt = _build_review_prompt(pr_title, pr_body, diff_text, diff_truncated, digest)

    if model.get("kind") == "ollama":
        payload, err = _tier1_call_with_retry(
            TEAM_LLM_BASE_URL, model["name"], system=_AI_REVIEWER_SYSTEM, user=prompt,
            tools=[], timeout=TEAM_LLM_TIMEOUT_SECONDS,
            retry_budget=TEAM_LLM_TRANSPORT_RETRY_BUDGET)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "text": _tier1_raw_text(payload)}

    # kind == "engine": the reviewing engine is NEVER given the project's
    # real working copy. This is the load-bearing safety property of this
    # whole feature (docs/spec.md) -- a freshly created, RUN_USER-owned
    # throwaway scratch directory, unconditionally deleted afterward, so
    # even a rogue/over-eager headless invocation that ignores its
    # instructions and tries to edit files can only ever touch a directory
    # that's gone seconds later. No worktree, no branch, no risk to the
    # real project checkout.
    scratch = os.path.join(TEAM_STATE_DIR, "_ai_reviewer_scratch", secrets.token_hex(8))
    os.makedirs(TEAM_STATE_DIR, exist_ok=True)
    os.chmod(TEAM_STATE_DIR, 0o711)  # same "don't rely on SVC_USER's ambient
                                      # umask" discipline agent_run()'s own
                                      # rundir/prompt-file chmods already use
                                      # -- RUN_USER's mkdir -p below needs to
                                      # be able to cd into this directory.
    mk = _run_run_user_command(["mkdir", "-p", scratch], cwd=TEAM_STATE_DIR)
    if not mk["ok"]:
        return {"ok": False,
                "error": mk.get("error") or "failed to create review scratch directory"}
    try:
        try:
            result = agent_run(model["name"], scratch, prompt, timeout=TEAM_HEADLESS_TIMEOUT_SECONDS)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "engine review failed"}
        return {"ok": True, "text": result.get("text", "")}
    finally:
        _run_run_user_command(["rm", "-rf", scratch], cwd=TEAM_STATE_DIR)


# ─── roster + lead loop (backlog item 6c; docs/spec.md, docs/story.md §4) ──
# Every headless-eligible engines.d entry plus one configured Ollama model,
# each tagged with a lead-adapter tier (1 native tool-calling / 2 schema-
# constrained / 3 prose-parse), and the four-tool lead loop
# (delegate/fact_check/ask_user/finish) that drives agent_run() (6a) and
# fact_check()/load_grounding() (6b/6b.1) into an actual team. CLI-only --
# no web route, no UI (that's 6d/6e/6f).

_LEAD_TOOL_NAMES = ("delegate", "fact_check", "ask_user", "board_read", "board_write", "finish")

# The exactly-three board_write verbs (docs/spec.md "Verb set naming" --
# set_status covers what the backlog calls "move card", since in Taiga's
# own data model a Kanban column IS a userstory's status).
_BOARD_WRITE_VERBS = {"set_status", "amend_description", "append_comment"}


def _lead_tier_for_engine(e) -> int:
    """
    Auto-detects a headless-eligible engine's lead-adapter tier, with an
    explicit override escape hatch (docs/spec.md "Engine-file extension"):
    HEADLESS_LEAD_FORMAT=schema|prose in an engine definition overrides the
    auto-detected tier when an engine gains or loses a structured-output
    flag. Any other/unset HEADLESS_LEAD_FORMAT value falls through to
    auto-detection based on whether HEADLESS_SCHEMA_FLAG is present.
    """
    if e.headless_lead_format == "schema":
        return 2
    if e.headless_lead_format == "prose":
        return 3
    if e.headless_schema_flag:
        return 2
    return 3


def roster() -> list:
    """
    [{name, kind: "engine"|"ollama", label, tier: 1|2|3, delegate_capable,
      schema_flag_error}]  ("engine" entries only for the last key)

    kind="ollama" entries are lead-only (delegate_capable=False -- there is
    no agent_run() path for an Ollama chat-completion model, only for a
    headless-eligible engines.d entry). Re-reads load_engines() live, same
    no-cache philosophy load_engines() itself already documents (engines.d
    is meant to be edited without a restart).

    `schema_flag_error` (docs/spec.md "Correction: {schema} is inline for
    Claude, a file for Codex") -- None for an engine with no
    HEADLESS_SCHEMA_FLAG at all, or one that declares a recognized
    placeholder correctly; otherwise the human-readable reason a tier-2
    lead call for this engine would fail. Surfaced here, at ROSTER-build
    time, per the spec's own explicit requirement -- not discovered only
    once a team is already running and the first tier-2 call fails.
    """
    entries = []
    if TEAM_LLM_BASE_URL and TEAM_LLM_MODEL:
        entries.append({"name": TEAM_LLM_MODEL, "kind": "ollama", "label": TEAM_LLM_MODEL,
                        "tier": 1, "delegate_capable": False})
    for e in sorted(load_engines().values(), key=lambda e: e.name):
        if not e.headless_enabled:
            continue
        entries.append({"name": e.name, "kind": "engine", "label": e.label,
                        "tier": _lead_tier_for_engine(e), "delegate_capable": True,
                        "schema_flag_error": _schema_flag_config_error(e)})
    return entries


def default_team_composition() -> dict:
    """
    {"ok": True, "lead": {...}, "members": [...]} or
    {"ok": False, "error": "..."}. Backlog item 6d part 2a -- the actual
    lead/member PICKER is 6e's job; this is the deterministic default the
    web route uses until then. Built entirely on roster()/_lead_tier_for_
    engine()/_schema_flag_config_error() -- no new engine-iteration logic.

    Lead, in priority order:
      1. The configured Ollama tier-1 model, if TEAM_LLM_BASE_URL and
         TEAM_LLM_MODEL are both set (docs/story.md §3, "Ollama-backed
         local model is the default").
      2. Else the first (sorted by name) headless-eligible engines.d entry
         that is tier 2 (schema-constrained) with no schema_flag_error.
      3. A tier-3 (prose-parse) engine is NEVER selected as the default
         lead -- SETTLED BY THE USER 2026-08-13, against this spec's own
         original recommendation to allow it. If the only headless-eligible
         engines are tier 3, refuse, naming both concrete fixes. Note this
         makes the DEFAULT stricter, not the system: 6c's CLI --lead still
         accepts a tier-3 lead as an explicit opt-in, and that path is
         unchanged (see CliTeamStartTierThreeLeadStillAllowedTests).
      4. Else refuse -- no roster member at all is available to lead.

    Members: every OTHER headless-eligible engines.d entry (an Ollama lead
    isn't itself an engines.d entry, so nothing is excluded from members
    when the lead is Ollama). If that list is empty, refuse, naming the
    lead that was selected and the two ways to free up a teammate.
    """
    entries = roster()
    engine_entries = [e for e in entries if e["kind"] == "engine"]
    excluded_name = None

    if TEAM_LLM_BASE_URL and TEAM_LLM_MODEL:
        lead = {"kind": "ollama", "name": TEAM_LLM_MODEL, "tier": 1}
    else:
        tier2 = [e for e in engine_entries if e["tier"] == 2 and not e.get("schema_flag_error")]
        if tier2:
            picked = tier2[0]
            lead = {"kind": "engine", "name": picked["name"], "tier": 2}
            excluded_name = picked["name"]
        elif any(e["tier"] == 3 for e in engine_entries):
            return {"ok": False, "error": (
                "only a tier-3 (prose-parse, least reliable) lead is available -- "
                "configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL, or add a tier-2 "
                "(schema-capable) engine to engines.d. The CLI's --lead can still "
                "select a tier-3 lead explicitly.")}
        else:
            # Either no headless-eligible engine at all, or every one that
            # exists is a misconfigured tier-2 (schema_flag_error set) --
            # neither is a genuine tier-3-only situation, so the more
            # specific message above doesn't apply; fold into the generic
            # "nothing usable" refusal (roster()'s own schema_flag_error
            # field already surfaces the specific misconfiguration).
            return {"ok": False, "error": (
                "no roster member is available to lead a team -- configure "
                "TEAM_LLM_BASE_URL/TEAM_LLM_MODEL or add a headless-eligible "
                "engine to engines.d")}

    members = [e["name"] for e in engine_entries if e["name"] != excluded_name]
    if not members:
        return {"ok": False, "error": (
            f"only one headless-eligible engine ('{lead['name']}') is configured "
            "and it was selected as lead -- add another engine to engines.d or "
            "configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL to free it up as a teammate")}
    return {"ok": True, "lead": lead, "members": members}


def validate_composition(lead: dict, members: list) -> str:
    """
    None if valid, else a human-readable reason (docs/spec.md 6e "Proposed
    approach" §1). Built on roster() (called once internally, the source of
    truth for tier/delegate_capable/schema_flag_error), not on
    load_engines() directly -- same reasoning default_team_composition()
    already established.

    `lead`/`members` are the raw wire-format objects a POST body carries
    (docs/design.md "API shape for composition in JSON"): `lead` is
    {"kind": "engine"|"ollama", "name": str}, each `members` entry is
    {"kind": "engine", "name": str} -- only "name" is actually read off a
    member entry (its own declared "kind" is never trusted; a member only
    ever matches a roster entry looked up by kind=="engine" specifically,
    which is also what keeps the Ollama entry, if any, from ever being
    accepted as a teammate -- it is never delegate_capable).
    """
    if not isinstance(lead, dict) or lead.get("kind") not in ("engine", "ollama") or not lead.get("name"):
        return "Lead is required"
    lead_kind, lead_name = lead["kind"], lead["name"]

    entries = roster()
    by_key = {(e["kind"], e["name"]): e for e in entries}
    lead_entry = by_key.get((lead_kind, lead_name))
    if lead_entry is None:
        return f"Unknown lead: {lead_name}"
    # Same tier-2-only schema-flag protection _cli_team_start() already
    # gives, now shared -- a tier-3 lead has no schema flag to be wrong
    # about, so this never applies to one.
    if lead_entry["tier"] == 2 and lead_entry.get("schema_flag_error"):
        return (f"lead engine '{lead_name}' is misconfigured for tier-2 use: "
                f"{lead_entry['schema_flag_error']}")

    if not isinstance(members, list) or not members:
        return "At least one teammate is required"
    names = []
    for m in members:
        name = m.get("name") if isinstance(m, dict) else None
        if not name:
            return "At least one teammate is required"
        names.append(name)
    if len(set(names)) != len(names):
        return "Teammate list contains duplicates"
    if lead_name in names:
        return "Lead cannot also be a teammate"
    for name in names:
        member_entry = by_key.get(("engine", name))
        if member_entry is None:
            return f"Unknown teammate: {name}"
        if not member_entry.get("delegate_capable"):
            return f"Teammate {name} is not delegate-capable"
    return None


def _compositions_path() -> str:
    return os.path.join(TEAM_STATE_DIR, "compositions.json")


def load_compositions() -> dict:
    """{project_name: {"lead": {"kind", "name"}, "members": [str, ...],
    "saved_at": iso}}. Same missing/corrupt-file tolerance as app.py's own
    _load_desc_cache() -- never a 500, just an empty dict (docs/spec.md
    "Edge cases": 'compositions.json missing or corrupt')."""
    try:
        with open(_compositions_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_composition(project_name: str, lead: dict, members: list) -> None:
    """
    Upserts one project's composition, atomic write via .tmp + os.replace()
    (same convention app.py's own _save_desc_cache() and this module's own
    _persist() already use). `lead`/`members` are the raw wire-format
    objects (see validate_composition()'s own docstring) -- ONLY validated
    values ever reach here (docs/spec.md "saved on successful validation").

    Stores only kind+name for lead, never tier/schema_flag_error -- those
    are always re-derived live from roster() at read time, so a later
    engines.d edit can't leave a stale tier displayed or trusted. Members
    are stored as a plain list of names, the same shape
    default_team_composition() already returns -- the only valid `kind` a
    member can have is "engine" (delegate_capable is Ollama-exclusionary by
    construction), so nothing is lost by dropping it.
    """
    path = _compositions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    compositions = load_compositions()
    compositions[project_name] = {
        "lead": {"kind": lead["kind"], "name": lead["name"]},
        "members": [m["name"] for m in members],
        "saved_at": _now_iso(),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(compositions, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


# ─── the four-tool schema (reused verbatim in shape from
# scripts/spike-lead-toolcalling.py, docs/spec.md §4) -- the one deliberate
# change from the spike's own throwaway harness is that delegate.agent's
# enum is built from the running team's ACTUAL --members list, not
# hardcoded to ["claude", "codex", "aider"].
def _lead_tools(team_members: list) -> list:
    return [
        {"type": "function", "function": {
            "name": "delegate",
            "description": "Give one self-contained task to a named teammate agent.",
            "parameters": {"type": "object", "properties": {
                "agent": {"type": "string", "enum": list(team_members)},
                "task": {"type": "string", "description": "The full self-contained task."}},
                "required": ["agent", "task"]}}},
        {"type": "function", "function": {
            "name": "fact_check",
            "description": "Verify a claim against the project's own documentation.",
            "parameters": {"type": "object", "properties": {
                "claim": {"type": "string"}}, "required": ["claim"]}}},
        {"type": "function", "function": {
            "name": "ask_user",
            "description": "Ask the human a question when something is genuinely unresolved.",
            "parameters": {"type": "object", "properties": {
                "question": {"type": "string"},
                "header": {"type": "string"},
                "options": {"type": "array", "items": {"type": "object", "properties": {
                    "label": {"type": "string"}, "description": {"type": "string"}}}},
                "multi_select": {"type": "boolean"}},
                "required": ["question", "options"]}}},
        {"type": "function", "function": {
            "name": "board_read",
            "description": "Look up userstories on this project's Taiga kanban board -- one "
                           "card by ref, a bounded substring-matched list by query, or the "
                           "most recently modified cards if neither is given.",
            "parameters": {"type": "object", "properties": {
                "ref": {"type": "integer", "description": "Look up one specific card by its ref number."},
                "query": {"type": "string", "description": "Substring to match against card subjects."}},
                "required": []}}},
        {"type": "function", "function": {
            "name": "board_write",
            "description": "Propose one narrow change to one existing Taiga card. Does NOT "
                           "apply the change -- queues it for human approval.",
            "parameters": {"type": "object", "properties": {
                "verb": {"type": "string", "enum": sorted(_BOARD_WRITE_VERBS)},
                "ref": {"type": "integer"},
                "value": {"type": "string"},
                "note": {"type": "string"}},
                "required": ["verb", "ref", "value"]}}},
        {"type": "function", "function": {
            "name": "finish",
            "description": "Conclude the task with a summary.",
            "parameters": {"type": "object", "properties": {
                "summary": {"type": "string"}}, "required": ["summary"]}}},
    ]


# Tier 2's schema file wraps the same four tools as ONE object (a JSON
# Schema constrains shape, not a discriminated union of four shapes) --
# _validate_lead_action() below is what actually checks `args` matches
# whichever `tool` was named, same as it does for tier 1/3.
_TIER2_LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": list(_LEAD_TOOL_NAMES)},
        "args": {"type": "object"},
    },
    "required": ["tool", "args"],
}

# Required verbatim (or materially equivalent) in _system_framing(), every
# tier (docs/spec.md §13) -- the prompt-level mitigation for 6b.1's own
# closing recommendation ("6c will handle recall at the prompt level
# instead, by instructing the lead to make short claims quoting exact
# phrases", 3e79cb0).
_FACT_CHECK_MITIGATION = (
    "When you use fact_check, phrase the claim as a short quotation of exact "
    "wording from the project's own docs -- a distinctive phrase or term "
    "copied verbatim -- rather than paraphrasing the idea in your own words. "
    "fact_check is a literal substring matcher; it has no synonym or fuzzy "
    "matching, so an exact quotation is far more likely to be found even "
    "when the underlying claim is true.\n\n"
    "If fact_check returns found: false, treat the claim as unverified, not "
    "false. This tool failing to find supporting text does not mean the "
    "claim is wrong -- only that this specific tool could not locate it. An "
    "unverified claim may still be true. Use your own judgment, a "
    "teammate's own report, or ask_user if something is genuinely "
    "unresolved -- never conclude a claim is false solely because "
    "fact_check returned found: false."
)

# Required verbatim (or materially equivalent) in _system_framing(), every
# tier -- the prompt-level mitigation for a real, live finding from this
# sub-spec's own tier-1 verification (docs/spec.md "Correction: repeated
# delegation of an already-completed task"): a real qwen3:8b run delegated
# the identical task to `claude` twice before calling finish, even though a
# correct, well-formed prior result was already in the round history. A
# judgment miss, same class as the spike's own single wrong_tool -- not a
# crash, not a spec violation, mitigated the same way fact_check's own
# recall gap is (docs/spec.md §13, above): make the thing the model should
# notice explicit and salient in the prompt, rather than left to be
# inferred from prose. See team_step()'s own delegate-branch history
# summaries (args_summary/outcome_summary) for the "explicit and salient"
# half of this fix -- SUCCEEDED/FAILED plus the task text, not just
# "ok, N chars" the way round 1 of this sub-spec had it.
_DELEGATION_HISTORY_MITIGATION = (
    "Before calling delegate, check the round history above for a prior "
    "delegate round to the SAME agent for the same or a substantially "
    "similar task. If one SUCCEEDED, do not delegate that task again -- use "
    "the prior result instead (fact_check it if you're unsure it's "
    "correct, or ask_user if it's genuinely ambiguous). Only re-delegate "
    "the same task to the same agent if the prior attempt FAILED or its "
    "result was clearly incomplete."
)

# Required verbatim (or materially equivalent) in _system_framing(), every
# tier (backlog item 7 part 1, docs/spec.md §3) -- states explicitly what
# board_write's tool_result does NOT mean, the same "make it explicit and
# salient, don't leave it to be inferred" discipline the two mitigation
# clauses above already established.
_BOARD_WRITE_MITIGATION = (
    "You also have read/write access to this project's Taiga kanban board via "
    "board_read and board_write. board_write does NOT apply your change -- "
    "every board_write call is queued as a pending proposal and takes effect "
    "ONLY after a human explicitly approves it. Its tool_result tells you the "
    "proposal was queued, never that the board was actually changed -- do not "
    "assume a board_write call has succeeded, and do not repeat an identical "
    "board_write call while a prior one is still pending (a run can only have "
    "one board write pending approval at a time; a second board_write call "
    "while one is already pending will be rejected)."
)


def _tool_prose(team_members: list) -> str:
    """Prose tool descriptions for tier 2/3 (tier 1 gets these for free from
    the native `tools` array and doesn't need them restated, docs/spec.md
    §5)."""
    agents = ", ".join(team_members) if team_members else "(no teammates configured)"
    return (
        "You have exactly six tools:\n"
        f"- delegate(agent, task): give one self-contained task to a named "
        f"teammate agent. agent must be exactly one of: {agents}.\n"
        "- fact_check(claim): verify a claim against the project's own "
        "documentation.\n"
        "- ask_user(question, header, options, multi_select): ask the human "
        "a question when something is genuinely unresolved. options is a "
        "list of 2-4 objects each shaped {label, description}; header is a "
        "short label (12 characters or fewer); multi_select is a boolean.\n"
        "- board_read(ref, query): look up userstories on this project's "
        "Taiga kanban board -- one card by ref, a bounded substring-matched "
        "list by query, or the most recently modified cards if neither is "
        "given. Both arguments optional.\n"
        "- board_write(verb, ref, value, note): propose one narrow change "
        "to one existing card. verb must be exactly one of set_status, "
        "amend_description, append_comment (set_status moves the card "
        "between Kanban columns by changing its status). Does NOT apply "
        "the change -- queues it for human approval; note is optional.\n"
        "- finish(summary): conclude the task with a summary.\n"
    )


def _system_framing(workdir: str, team_members: list, tier: int) -> str:
    """
    Role framing, the grounding digest (load_grounding(workdir)["digest"],
    already capped by 6b/6b.1's own TEAM_GROUNDING_MAX_BYTES), and, tier 2/3
    only, prose tool descriptions. Always includes, verbatim, the two
    required fact_check-precision-mitigation clauses (docs/spec.md §5, §13)
    plus the repeated-delegation mitigation (docs/spec.md "Correction:
    repeated delegation of an already-completed task"), regardless of tier.
    Called fresh every round (not cached), same
    always-re-read philosophy load_grounding()/load_engines() already use --
    a project's docs edited mid-run are picked up, and a crash-recovered run
    rebuilds the identical prompt a live run would have.
    """
    grounding = load_grounding(workdir)
    agents = ", ".join(team_members) if team_members else "(no teammates configured)"
    parts = [
        "You are the lead of a team of coding agents working on one "
        f"project. Your teammates: {agents}. Delegation is read/write "
        "against the project's own working directory; grounding (below) is "
        "read-only -- you never write to the project's own docs directly.",
        "Project grounding (auto-discovered project docs):\n" + grounding["digest"],
    ]
    if tier == 2:
        parts.append(_tool_prose(team_members))
        parts.append(
            "Respond with a single JSON object shaped {\"tool\": <tool name>, "
            "\"args\": {...}} matching the required output schema. Do not "
            "include any text outside that JSON object.")
    elif tier == 3:
        parts.append(_tool_prose(team_members))
        parts.append(
            "Respond with exactly one fenced code block, opened with "
            "```json and closed with ```, containing a single JSON object "
            "shaped {\"tool\": <tool name>, \"args\": {...}}. Nothing else "
            "outside the fence.")
    parts.append(_FACT_CHECK_MITIGATION)
    parts.append(_DELEGATION_HISTORY_MITIGATION)
    parts.append(_BOARD_WRITE_MITIGATION)
    return "\n\n".join(parts)


def _round_context(task: str, history: list, last_entry, round_n: int, max_rounds: int) -> str:
    """
    The part of the prompt that grows, and the part that's bounded
    (docs/spec.md §5). `history` is the run's own list of per-round record
    dicts ({round, tool, args_summary, outcome_summary, full_result_text,
    log_path}); only ONE-LINE summaries of every PRIOR round go in, never
    full text -- `last_entry` (the single most recent entry, or None) is the
    only one whose full_result_text is shown, and even that is hard-capped
    at TEAM_DELEGATE_RESULT_MAX_CHARS with a non-silent truncation marker.
    """
    lines = [f"Task: {task}", "", "Round history:"]
    if history:
        for h in history:
            lines.append(f"  round {h['round']}: {h['args_summary']} -> {h['outcome_summary']}")
    else:
        lines.append("  (no rounds yet)")
    lines.append("")
    if last_entry is not None:
        text = last_entry.get("full_result_text") or ""
        encoded = text.encode("utf-8")
        capped = encoded[:TEAM_DELEGATE_RESULT_MAX_CHARS]
        capped_text = capped.decode("utf-8", errors="ignore")
        suffix = ""
        if len(encoded) > TEAM_DELEGATE_RESULT_MAX_CHARS:
            remaining = len(encoded) - len(capped)
            log_note = last_entry.get("log_path") or "(no log for this outcome)"
            suffix = f"\n...[truncated, {remaining} more chars, full text in {log_note}]"
        lines.append(f"Most recent result (round {last_entry['round']}, {last_entry['tool']}):")
        lines.append(capped_text + suffix)
        lines.append("")
    lines.append(f"Round {round_n} of {max_rounds}. What do you do next?")
    return "\n".join(lines)


def _assemble_prompt(system: str, round_context: str) -> str:
    """
    The whole assembled prompt (_system_framing() + _round_context(),
    joined) passed through one FINAL, unconditional cap -- the exact same
    "the heuristic above it doesn't have to be right, because the final step
    always runs" pattern build_digest() already uses (docs/spec.md §5).
    """
    text = system + "\n\n" + round_context
    encoded = text.encode("utf-8")[:TEAM_LEAD_PROMPT_MAX_CHARS]
    return encoded.decode("utf-8", errors="ignore")


def _split_capped_prompt(system: str, round_context: str):
    """
    Tier 1 needs system/user as two separate chat messages, not one joined
    string -- this returns (system_part, user_part) such that their
    concatenation (with the same "\\n\\n" separator _assemble_prompt() uses)
    is byte-for-byte the capped text _assemble_prompt() would produce, so
    the SAME TEAM_LEAD_PROMPT_MAX_CHARS invariant holds for tier 1's two-
    message form as it does for tier 2/3's one joined string, without ever
    re-growing past the cap.
    """
    capped = _assemble_prompt(system, round_context)
    sep = "\n\n"
    if capped.startswith(system + sep):
        return system, capped[len(system) + len(sep):]
    # Truncation cut into the system framing itself -- only possible if
    # framing ALONE already exceeds the cap (an unusually large grounding
    # digest plus an unusually long team-member list). Rather than guessing
    # at a split point inside possibly-mid-character truncated text, the
    # whole capped blob becomes the system message and the user slot is
    # empty -- still respects the same total-length invariant.
    return capped, ""


# ─── tier 1: native tool-calling (Ollama, /v1/chat/completions) ───────────
def _lead_tier1_call(base_url: str, model: str, system: str, user: str, tools: list, *,
                     timeout: float) -> dict:
    """
    Same urllib shape as app.py's _summarize_project(), plus `tools` --
    identical body shape to scripts/spike-lead-toolcalling.py's own call().
    Raises on transport failure (caught by _tier1_call_with_retry() below),
    never on a well-formed-but-wrong-shaped response body -- that's
    _parse_tier1_action()'s job, not this function's.
    """
    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "tools": tools, "stream": False, "temperature": 0}).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _tier1_call_with_retry(base_url: str, model: str, system: str, user: str, tools: list, *,
                           timeout: float, retry_budget: int):
    """
    Returns (payload, None) on success, or (None, error_message) once
    retry_budget is exhausted. TEAM_LLM_TRANSPORT_RETRY_BUDGET's own proof
    obligation scopes this to URLError/timeout/HTTP 5xx -- urllib.error.
    URLError (which HTTPError subclasses) is itself a subclass of OSError,
    and a raw socket timeout is also an OSError, so catching OSError alone
    covers all three. A response body that isn't valid JSON at all
    (json.loads() raising ValueError) is ALSO folded into this same
    transport-retry category -- there is no other defined bucket for "the
    HTTP call itself succeeded but the endpoint returned something that
    isn't a parseable response body" (a proxy error page, a truncated
    response), and it is not a model-tool-choice problem the way a
    malformed *tool call* is -- see docs/implementation.md "Deviations from
    spec" for this explicit, disclosed extension of the constant's literal
    wording.
    """
    last_err = None
    for _attempt in range(retry_budget + 1):
        try:
            return _lead_tier1_call(base_url, model, system, user, tools, timeout=timeout), None
        except (OSError, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}"
    return None, (f"tier-1 lead endpoint {base_url!r} (model={model!r}) unreachable "
                  f"after {retry_budget + 1} attempt(s): {last_err}")


def _tier1_raw_text(payload) -> str:
    """Best-effort extraction of "what the model actually said", for a
    malformed-escalation's raw-text-included requirement (docs/spec.md §9)
    -- never raises regardless of payload's shape."""
    try:
        msg = payload["choices"][0]["message"]
        if isinstance(msg, dict):
            calls = msg.get("tool_calls")
            if calls:
                return json.dumps(calls)
            return msg.get("content") or ""
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return json.dumps(payload)
    except (TypeError, ValueError):
        return str(payload)


def _parse_tier1_action(payload):
    """
    Reads payload["choices"][0]["message"] (docs/spec.md §6). tool_calls
    empty/absent -> PROSE FALLBACK -- attempt the tier-3 fenced-json-block
    parser against message.get("content") before giving up (the acceptance
    criterion "a tier-1 model that ignores the tools array... falls back to
    tier-3 parsing rather than silently doing nothing", implemented as one
    literal function call). tool_calls non-empty -> only tool_calls[0] is
    honored; any further entries are the caller's own concern to log (this
    function mirrors the spike's own classify(), which only ever inspects
    calls[0]). Returns a {"tool":..., "args":...} dict, or None if no
    action could be extracted at all -- None is itself a valid input to
    _validate_lead_action() (not isinstance(None, dict) -> "not_a_dict",
    one of the malformed categories), so this never needs a separate
    early-exit path in the caller.
    """
    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    calls = msg.get("tool_calls") or []
    if not calls:
        return _parse_tier3_action(msg.get("content") or "")
    call = calls[0]
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict):
        return None
    name = fn.get("name")
    raw_args = fn.get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except ValueError:
        return None
    return {"tool": name, "args": args}


def _tier1_extra_tool_call_count(payload) -> int:
    """How many tool_calls beyond [0] a tier-1 response carried -- purely
    for the caller's own logging (docs/spec.md §6: "any further entries are
    logged... but never acted on"), never affects parsing."""
    try:
        calls = payload["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return 0
    return max(0, len(calls) - 1)


# ─── tier 3: prose-parse (aider, and any engine with no structured-output
# flag) ─────────────────────────────────────────────────────────────────
_TIER3_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def _parse_tier3_action(text: str):
    """
    The FIRST fenced ```json block found, tried once (docs/spec.md §8): if
    it parses as a dict with both "tool" and "args" keys, that's the
    action; otherwise None -- never an exception, and a second/third fenced
    block is never consulted even if the first one fails (mirrors tier 1's
    calls[0]-only rule: "more than one candidate is ambiguous-but-only-the-
    first-is-tried"). This is also the exact function tier 1's prose-
    fallback path calls (§6) -- one parser, two callers. Whether `args`
    itself is a dict is NOT checked here -- that's _validate_lead_action()'s
    "not_a_dict" category, same as every other tier.
    """
    if not text:
        return None
    m = _TIER3_FENCE_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(parsed, dict) or "tool" not in parsed or "args" not in parsed:
        return None
    return {"tool": parsed.get("tool"), "args": parsed.get("args")}


# ─── shared action validation -- one pipeline, three "get me a dict"
# adapters (docs/spec.md §9) ────────────────────────────────────────────
# required-arg shape per tool: (key, expected_python_type). One shared
# table/validator across all three tiers, not three near-identical ones --
# a malformed action from ANY tier means the same thing (the lead's output
# could not be turned into a valid tool call) and should cost the same
# shared TEAM_LEAD_MALFORMED_RETRY_BUDGET.
_LEAD_TOOL_REQUIRED_ARGS = {
    "delegate": (("agent", str), ("task", str)),
    "fact_check": (("claim", str),),
    "ask_user": (("question", str), ("options", list)),
    "board_read": (),  # every arg optional -- special-cased below, same as ask_user's own optional args
    "board_write": (("verb", str), ("ref", int), ("value", str)),
    "finish": (("summary", str),),
}


def _validate_lead_action(raw, team_members: list, action_count: int) -> dict:
    """
    Returns {"ok": True, "tool": ..., "args": ...} or
    {"ok": False, "reason": "<category>", "detail": "..."}. Never raises --
    raw is untrusted model output, the least trustworthy input in this
    system, same standing as agent_run()'s own untrusted engine-stdout
    input. `raw` may itself be None (a tier adapter that could not extract
    anything at all) -- not isinstance(None, dict), so this degrades to the
    same "not_a_dict" malformed category as any other shape failure, no
    separate early-exit path needed by callers.

    Categories (docs/spec.md "Shape robustness"):
      unknown_tool        -- raw["tool"] not one of the six names
      missing_args         -- a tool-required key absent from raw["args"]
      wrong_type            -- an arg present but the wrong JSON type
      not_a_dict            -- raw itself, or raw["args"], isn't an object
      unknown_verb          -- board_write.verb not one of the three known
                              verbs (backlog item 7 part 1, docs/spec.md
                              "Edge cases" -- malformed-shape, same as
                              unknown_tool, not a business rule)
      value_too_long        -- board_write.value exceeds
                              TEAM_BOARD_WRITE_VALUE_MAX_CHARS (malformed
                              shape, same family as the above)
      agent_not_on_team    -- delegate.agent not in team_members (valid
                              shape, invalid *value* -- NOT malformed)
      premature_finish     -- finish with action_count == 0 (valid shape,
                              rejected on a business rule -- NOT malformed)

    Two different outcome families, handled differently on purpose (see
    docs/spec.md §9): (1) malformed shape counts against the shared
    malformed-retry budget and is re-prompted with the specific reason;
    (2) a valid shape rejected on a business rule does NOT consume that
    budget -- fed back as an ordinary tool-result-shaped error, consuming
    one ordinary round like a fact_check miss does.
    """
    if not isinstance(raw, dict):
        return {"ok": False, "reason": "not_a_dict", "detail": "the lead's action is not a JSON object"}
    tool = raw.get("tool")
    if tool not in _LEAD_TOOL_REQUIRED_ARGS:
        return {"ok": False, "reason": "unknown_tool",
                "detail": f"unknown tool {tool!r}; valid tools are delegate, fact_check, ask_user, "
                         "board_read, board_write, finish"}
    args = raw.get("args")
    if not isinstance(args, dict):
        return {"ok": False, "reason": "not_a_dict", "detail": f"{tool}'s args is not a JSON object"}
    for key, expected_type in _LEAD_TOOL_REQUIRED_ARGS[tool]:
        if key not in args:
            return {"ok": False, "reason": "missing_args", "detail": f"{tool} requires '{key}'"}
        if not isinstance(args[key], expected_type):
            return {"ok": False, "reason": "wrong_type",
                    "detail": f"{tool}.{key} must be a {expected_type.__name__}"}
    if tool == "ask_user":
        for opt in args["options"]:
            if not isinstance(opt, dict) or not isinstance(opt.get("label"), str):
                return {"ok": False, "reason": "wrong_type",
                        "detail": "ask_user.options items must be objects with a string 'label'"}
        if "multi_select" in args and not isinstance(args["multi_select"], bool):
            return {"ok": False, "reason": "wrong_type", "detail": "ask_user.multi_select must be a boolean"}
        if "header" in args and not isinstance(args["header"], str):
            return {"ok": False, "reason": "wrong_type", "detail": "ask_user.header must be a string"}
    if tool == "board_read":
        if "ref" in args and args["ref"] is not None and not isinstance(args["ref"], int):
            return {"ok": False, "reason": "wrong_type", "detail": "board_read.ref must be an int"}
        if "query" in args and args["query"] is not None and not isinstance(args["query"], str):
            return {"ok": False, "reason": "wrong_type", "detail": "board_read.query must be a string"}
    if tool == "board_write":
        if args["verb"] not in _BOARD_WRITE_VERBS:
            valid = ", ".join(sorted(_BOARD_WRITE_VERBS))
            return {"ok": False, "reason": "unknown_verb",
                    "detail": f"unknown verb {args['verb']!r}; valid verbs are {valid}"}
        if args["ref"] <= 0:
            return {"ok": False, "reason": "wrong_type", "detail": "board_write.ref must be a positive int"}
        if len(args["value"]) > TEAM_BOARD_WRITE_VALUE_MAX_CHARS:
            return {"ok": False, "reason": "value_too_long",
                    "detail": f"board_write.value exceeds {TEAM_BOARD_WRITE_VALUE_MAX_CHARS} chars"}
        if "note" in args and args["note"] is not None and not isinstance(args["note"], str):
            return {"ok": False, "reason": "wrong_type", "detail": "board_write.note must be a string"}
    if tool == "delegate" and args["agent"] not in team_members:
        members_str = ", ".join(team_members) if team_members else "(none)"
        return {"ok": False, "reason": "agent_not_on_team",
                "detail": f"agent {args['agent']!r} is not on this team. Team members: {members_str}."}
    if tool == "finish" and action_count == 0:
        return {"ok": False, "reason": "premature_finish",
                "detail": "finish called with zero prior delegate/fact_check actions this run"}
    return {"ok": True, "tool": tool, "args": args}


# ─── persistence (docs/spec.md §11) ───────────────────────────────────────
def _leads_root() -> str:
    return os.path.join(TEAM_STATE_DIR, "leads")


def _run_dir(run_id: str) -> str:
    # Deliberately NOT validated against _RUN_ID_RE here (docs/BACKLOG.md
    # item 11(b); see docs/implementation.md "Deviations from spec" for the
    # concrete conflict this avoided): this helper is shared by every
    # internal caller too -- the CLI (team-status/team-stop/team-reap),
    # sweep_dead_teams(), and a wide swath of existing pure-unit tests all
    # construct or pass run_id values that were never meant to match
    # _run_id()'s exact generation shape (short synthetic ids in tests,
    # arbitrary CLI-typed ids that should 404 with "no such run_id" rather
    # than a shape-validation error). The actual attacker-reachable surface
    # -- a client-supplied run_id on GET .../team/events, GET .../team/
    # inbox, POST .../team/resolve -- is validated at its intake in
    # app/app.py (_team_events_run_and_ownership() and the POST /team/
    # resolve handler) against _RUN_ID_RE, BEFORE any of those callers ever
    # reach this function, so a malformed/traversal run_id from those three
    # routes still never reaches a path-join.
    return os.path.join(_leads_root(), run_id)


def _run_json_path(run_id: str) -> str:
    return os.path.join(_run_dir(run_id), "run.json")


def _transcript_path(run_id: str) -> str:
    return os.path.join(_run_dir(run_id), "transcript.jsonl")


def _inbox_path(run_id: str) -> str:
    return os.path.join(_run_dir(run_id), "inbox.json")


def _inbox_resolved_path(run_id: str) -> str:
    return os.path.join(_run_dir(run_id), "inbox.resolved.json")


def _new_state(run_id: str, workdir: str, lead: dict, members: list, task: str,
               max_rounds: int = None, *, project_name: str = None,
               worktrees: dict = None) -> dict:
    now = _now_iso()
    return {
        "run_id": run_id, "workdir": workdir, "lead": lead, "members": list(members),
        "task": task, "status": "running", "round": 0, "action_count": 0,
        "max_rounds": max_rounds or TEAM_MAX_ROUNDS, "teammate_sessions": {},
        "history": [], "malformed_retries": 0, "in_progress_delegate": None,
        "summary": None, "error": None, "created_at": now, "updated_at": now,
        # Both additive (backlog item 6d part 1, docs/spec.md §4) -- None/{}
        # for a bare team-start (6c path, no team-launch), exactly the same
        # shape 6c's own tests already persist. Only launch_team() ever
        # supplies real values.
        "project_name": project_name, "worktrees": worktrees or {},
    }


def _persist(state: dict) -> None:
    """Written after every round, not just at completion -- this is what
    makes crash recovery possible (docs/spec.md §11): because the per-round
    prompt is always rebuilt fresh from this file, team-resume after a
    crash reconstructs the exact prompt a live process would have built
    next. Atomic write (tmp + os.replace), same convention app.py's own
    _save_desc_cache() already uses."""
    state["updated_at"] = _now_iso()
    d = _run_dir(state["run_id"])
    os.makedirs(d, exist_ok=True)
    path = _run_json_path(state["run_id"])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def _load_state(run_id: str) -> dict:
    with open(_run_json_path(run_id)) as f:
        return json.load(f)


def _next_transcript_seq(run_id: str) -> int:
    try:
        with open(_transcript_path(run_id), "rb") as f:
            return sum(1 for _ in f) + 1
    except FileNotFoundError:
        return 1


def _append_transcript(run_id: str, kind: str, text: str, meta: dict = None) -> None:
    """One envelope per call, same {ts, agent, seq, kind, text, meta} shape
    §4.1 already defines, agent="lead" always here -- durable audit trail,
    unrelated to and unbounded by the per-round PROMPT bounding (§5): this
    keeps the FULL text, never TEAM_DELEGATE_RESULT_MAX_CHARS-capped."""
    envelope = {"ts": _now_iso(), "agent": "lead", "seq": _next_transcript_seq(run_id),
               "kind": kind, "text": text or "", "meta": meta or {}}
    path = _transcript_path(run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(envelope) + "\n")


def _append_history(state: dict, round_n: int, *, tool, args_summary: str, outcome_summary: str,
                    full_result_text: str, log_path, transcript_entries: list) -> None:
    state["history"].append({
        "round": round_n, "tool": tool, "args_summary": args_summary,
        "outcome_summary": outcome_summary, "full_result_text": full_result_text or "",
        "log_path": log_path,
    })
    state["round"] = len(state["history"])
    for kind, text, meta in transcript_entries:
        _append_transcript(state["run_id"], kind, text, meta)


def _write_ask_user_inbox(state: dict, args: dict) -> None:
    """Exact §4.5 shape (renamed from _write_inbox, backlog item 7 part 1,
    docs/spec.md §4: inbox.json is now generalized with a "kind"
    discriminator, "ask_user" for this shape): question, header (<=12
    chars, silently truncated -- cosmetic, never worth spending retry
    budget on), 2-4 options each {label, description}, multi_select. Every
    field but the new "kind" is byte-for-byte unchanged from before this
    rename."""
    inbox = {
        "kind": "ask_user",
        "question": args.get("question", ""),
        "header": (args.get("header") or "")[:12],
        "options": args.get("options") or [],
        "multi_select": bool(args.get("multi_select", False)),
    }
    d = _run_dir(state["run_id"])
    os.makedirs(d, exist_ok=True)
    path = _inbox_path(state["run_id"])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(inbox, f, indent=2)
    os.replace(tmp, path)


def _write_board_inbox(state: dict, verb: str, ref: int, value: str, note, current_value: dict) -> None:
    """Sibling of _write_ask_user_inbox() -- kind="board_write" shape
    (backlog item 7 part 1, docs/spec.md §4). Same tmp-file + os.replace()
    atomic-write pattern, same _inbox_path(). `current_value` is a
    display-only snapshot (read via one get_userstory() call at proposal
    time) -- never reused as the `version` passed to the actual write,
    which resolve_board_write() always fetches fresh immediately before
    applying (docs/spec.md's own optimistic-concurrency requirement)."""
    inbox = {
        "kind": "board_write",
        "verb": verb,
        "ref": ref,
        "value": value,
        "note": note,
        "current_value": current_value,
        "proposed_at": _now_iso(),
    }
    d = _run_dir(state["run_id"])
    os.makedirs(d, exist_ok=True)
    path = _inbox_path(state["run_id"])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(inbox, f, indent=2)
    os.replace(tmp, path)


def _force_ask_user(state: dict, *, question: str, header: str, status: str = "blocked_ask_user") -> None:
    """
    Shared "stop the loop and record a clear reason" helper for both
    escalation paths (docs/spec.md §10): the malformed-retry-budget-
    exhausted path (status="blocked_ask_user", inbox.json written so a
    human can answer it via team-resolve and the run can continue) and the
    TEAM_MAX_ROUNDS-exhausted path (status="escalated_max_rounds", a
    TERMINAL status -- per §11's own persistence section, "inbox.json --
    present only while status == 'blocked_ask_user'", a materially DIFFERENT
    status value from "escalated_max_rounds" in that very same enum, so
    max-rounds exhaustion does not write an inbox.json or claim to be
    resumable the way a genuine ask_user escalation is; see
    docs/implementation.md "Deviations from spec" for why this reading was
    chosen over §10's more abbreviated pseudocode, which names both cases
    with the same helper function).
    """
    round_n = len(state["history"]) + 1
    if status == "blocked_ask_user":
        args = {
            "question": question,
            "header": header,
            "options": [
                {"label": "Continue", "description":
                 "Resume the team; the lead will see this answer in its next round."},
                {"label": "Abort", "description": "Stop this run without further action."},
            ],
            "multi_select": False,
        }
        _write_ask_user_inbox(state, args)
        _append_history(state, round_n, tool="ask_user", args_summary=f'ask_user("{question[:60]}")',
                        outcome_summary="forced escalation, awaiting human",
                        full_result_text=question, log_path=None,
                        transcript_entries=[("error", question, {"forced": True})])
    else:
        _append_history(state, round_n, tool=None, args_summary="(max rounds reached)",
                        outcome_summary=question, full_result_text=question, log_path=None,
                        transcript_entries=[("status", question, {"forced": True, "final_status": status})])
    state["status"] = status


# ─── tier 2 wrapper (schema-constrained, via agent_run(..., schema=...)) ──
def _call_lead(state: dict, system: str, round_context: str, *,
               cancel_event: threading.Event = None):
    """
    Dispatches to the right adapter for state["lead"]["tier"]. Returns
    (raw_or_None, transport_error_or_None, raw_text) -- raw_text is the
    best-effort original text the lead actually produced, kept even when
    raw parsing failed entirely, so a malformed-budget-exhausted escalation
    can include it verbatim (docs/spec.md §9's "never dropped" rule).
    Only tier 1 can produce a non-None transport_error (docs/spec.md
    "TEAM_LLM_TRANSPORT_RETRY_BUDGET... talking to the tier-1 endpoint");
    an agent_run() failure for a tier 2/3 lead's own turn (engine crashed,
    non-zero exit, no output) instead yields raw=None, which flows through
    the ordinary shared malformed-retry path -- there is no separate
    "transport retry" concept for an engines.d-hosted lead the way there is
    for the tier-1 HTTP endpoint.

    `cancel_event` (backlog item 6d part 2a, docs/spec.md §7): optional,
    keyword-only, default None -- threaded into the tier-2/tier-3 lead's own
    `agent_run()` call (it has a real subprocess to SIGTERM). Tier 1 (the
    Ollama HTTP call) has no subprocess to signal -- accepted but unused
    there; team_step()'s own checkpoint (right after this function returns)
    is what actually bounds a stop request arriving mid-tier-1-call.
    """
    tier = state["lead"]["tier"]
    if tier == 1:
        sys_part, user_part = _split_capped_prompt(system, round_context)
        tools = _lead_tools(state["members"])
        payload, err = _tier1_call_with_retry(
            TEAM_LLM_BASE_URL, state["lead"]["name"], sys_part, user_part, tools,
            timeout=TEAM_LLM_TIMEOUT_SECONDS, retry_budget=TEAM_LLM_TRANSPORT_RETRY_BUDGET)
        if err is not None:
            return None, err, ""
        raw = _parse_tier1_action(payload)
        extra = _tier1_extra_tool_call_count(payload)
        if extra:
            _append_transcript(state["run_id"], "status",
                              f"{extra} additional tool_calls beyond [0] discarded, never acted on",
                              {"discarded_tool_calls": extra})
        return raw, None, _tier1_raw_text(payload)

    prompt = _assemble_prompt(system, round_context)
    if tier == 2:
        result = agent_run(state["lead"]["name"], state["workdir"], prompt,
                           schema=_TIER2_LEAD_SCHEMA, cancel_event=cancel_event)
    else:
        result = agent_run(state["lead"]["name"], state["workdir"], prompt, cancel_event=cancel_event)
    text = result.get("text") or ""
    if not text and not result.get("ok"):
        text = result.get("error") or ""
    if tier == 2:
        try:
            parsed = json.loads(text)
        except ValueError:
            return None, None, text
        if not isinstance(parsed, dict) or "tool" not in parsed or "args" not in parsed:
            return None, None, text
        return {"tool": parsed.get("tool"), "args": parsed.get("args")}, None, text
    return _parse_tier3_action(text), None, text


# ─── the lead loop (docs/spec.md §10) ─────────────────────────────────────
def team_step(state: dict, *, cancel_event: threading.Event = None) -> dict:
    """
    One round. Takes/returns the run's own state dict (§11's persisted
    shape); the only I/O is one lead-adapter call, zero or one
    agent_run()/fact_check() call, and the state write at the end. Never
    raises for anything shaped wrong -- see _validate_lead_action().

    `cancel_event` (backlog item 6d part 2a, docs/spec.md §7): optional,
    keyword-only, default None -- byte-for-byte unchanged behavior when
    omitted. Two checkpoints, both additive: immediately after _call_lead()
    returns (checked FIRST, ahead of every existing branch, so a stop always
    wins over a malformed/failed lead call); immediately after the delegate
    branch's own agent_run() call returns (before the existing SUCCEEDED/
    FAILED history framing).
    """
    round_n = len(state["history"]) + 1
    tier = state["lead"]["tier"]
    system = _system_framing(state["workdir"], state["members"], tier)
    last_entry = state["history"][-1] if state["history"] else None
    round_context = _round_context(state["task"], state["history"], last_entry,
                                   round_n, state["max_rounds"])

    raw, transport_error, raw_text = _call_lead(state, system, round_context,
                                               cancel_event=cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        state["status"] = "stopped"
        _append_history(state, round_n, tool=None, args_summary="(stopped)",
                        outcome_summary="stopped by request before this round's action executed",
                        full_result_text="", log_path=None, transcript_entries=[])
        _persist(state)
        return state
    if transport_error is not None:
        # Ollama unreachable/timed out/5xx after exhausting the transport
        # retry budget -- a clear, actionable, non-traceback operational
        # error, never routed through ask_user (a human answering a
        # question can't fix an unreachable LLM backend).
        state["status"] = "error"
        state["error"] = transport_error
        _persist(state)
        return state

    action = _validate_lead_action(raw, state["members"], state["action_count"])
    if not action["ok"]:
        if action["reason"] in ("agent_not_on_team", "premature_finish"):
            # Valid shape, rejected on a business rule -- ordinary round,
            # does NOT consume the malformed-retry budget; fed back as a
            # tool-result-shaped error, same as a fact_check miss.
            state["malformed_retries"] = 0
            _append_history(state, round_n, tool=action.get("tool") or (raw or {}).get("tool"),
                            args_summary=f"{(raw or {}).get('tool')}(...)",
                            outcome_summary=f"rejected: {action['detail']}",
                            full_result_text=action["detail"], log_path=None,
                            transcript_entries=[("status", action["detail"],
                                                {"reason": action["reason"]})])
            _persist(state)
            return state
        # Malformed shape -- counts against TEAM_LEAD_MALFORMED_RETRY_BUDGET.
        if state["malformed_retries"] >= TEAM_LEAD_MALFORMED_RETRY_BUDGET:
            snippet = (raw_text or "")[:2000]
            question = (
                "The lead's output could not be parsed after "
                f"{TEAM_LEAD_MALFORMED_RETRY_BUDGET + 1} attempts. Raw text: {snippet}")
            _force_ask_user(state, question=question, header="Parse", status="blocked_ask_user")
            _persist(state)
            return state
        state["malformed_retries"] += 1
        _append_history(state, round_n, tool=None, args_summary="(malformed)",
                        outcome_summary=f"malformed ({action['reason']}): {action['detail']}",
                        full_result_text=f"{action['detail']}\n\nRaw text: {raw_text}", log_path=None,
                        transcript_entries=[("error", action["detail"], {"reason": action["reason"]})])
        _persist(state)
        return state

    # Valid, executable action -- reset the consecutive-malformed streak.
    state["malformed_retries"] = 0
    tool, args = action["tool"], action["args"]

    if tool == "delegate":
        agent, task = args["agent"], args["task"]
        state["in_progress_delegate"] = {"round": round_n, "agent": agent, "task_preview": task[:200]}
        _persist(state)  # marked BEFORE the blocking call -- see docs/spec.md
                         # "Mid-delegate crash"
        # backlog item 6d part 1, docs/spec.md §4: a teammate with its own
        # worktree (created by launch_team()) runs there, not against the
        # shared project tree -- and every delegation to the same agent
        # shares one stable, append-mode log path, so that agent's tmux
        # dashboard window shows continuity across multiple delegations. A
        # run with no worktrees entry for `agent` (every 6c test, any bare
        # team-start that skipped team-launch) falls back to
        # state["workdir"] -- byte-for-byte the existing 6c behavior.
        worktree = state.get("worktrees", {}).get(agent)
        result = agent_run(agent, worktree or state["workdir"], task,
                          session_id=state["teammate_sessions"].get(agent),
                          log_path=_agent_log_path(state["run_id"], agent),
                          cancel_event=cancel_event)
        state["in_progress_delegate"] = None
        state["teammate_sessions"][agent] = result.get("session_id")
        state["action_count"] += 1
        text = result.get("text") or ""
        task_preview = task if len(task) <= 100 else task[:97] + "..."
        # backlog item 6d part 2a, docs/spec.md §7: checked BEFORE the
        # existing SUCCEEDED/FAILED framing below, so an in-flight
        # delegation interrupted by a stop request is recorded as a clean
        # "stopped" outcome, not misread as an ordinary failure.
        if cancel_event is not None and cancel_event.is_set():
            state["status"] = "stopped"
            _append_history(state, round_n, tool="delegate",
                            args_summary=f'delegate(agent={agent}, task="{task_preview}")',
                            outcome_summary="stopped by request (delegation interrupted)",
                            full_result_text=text, log_path=result.get("log_path"),
                            transcript_entries=[
                                ("handoff", task, {"agent": agent}),
                                ("tool_result", text, {"agent": agent, "ok": result["ok"],
                                                       "log_path": result.get("log_path")}),
                            ])
            _persist(state)
            return state
        # docs/spec.md "Correction: repeated delegation of an
        # already-completed task" -- the round-history summary must state
        # prior delegations' agent/task/success EXPLICITLY and SALIENTLY,
        # not leave it to be inferred from prose, so a live finding (the
        # lead delegating the identical task twice) is less likely to
        # recur. SUCCEEDED/FAILED in capitals on purpose -- distinct at a
        # glance from fact_check's own lowercase found=True/False summaries
        # a few lines above/below it in the same round history.
        if result["ok"]:
            outcome_summary = f'SUCCEEDED, {len(text)} chars (see log)'
        else:
            outcome_summary = f"FAILED ({result.get('error') or 'unknown error'}, see log)"
        _append_history(state, round_n, tool="delegate",
                        args_summary=f'delegate(agent={agent}, task="{task_preview}")',
                        outcome_summary=outcome_summary,
                        full_result_text=text, log_path=result.get("log_path"),
                        transcript_entries=[
                            ("handoff", task, {"agent": agent}),
                            ("tool_result", text, {"agent": agent, "ok": result["ok"],
                                                   "log_path": result.get("log_path")}),
                        ])
    elif tool == "fact_check":
        claim = args["claim"]
        grounding = load_grounding(state["workdir"])
        result = fact_check(claim, grounding)
        state["action_count"] += 1
        outcome = f"found={result['found']}" + ("" if result["found"] else " (unverified)")
        result_text = json.dumps(result)
        _append_history(state, round_n, tool="fact_check", args_summary=f'fact_check("{claim[:60]}")',
                        outcome_summary=outcome, full_result_text=result_text, log_path=None,
                        transcript_entries=[
                            ("tool_use", claim, {}),
                            ("tool_result", result_text, {"found": result["found"]}),
                        ])
    elif tool == "ask_user":
        _write_ask_user_inbox(state, args)
        state["status"] = "blocked_ask_user"
        _append_history(state, round_n, tool="ask_user", args_summary=f'ask_user("{args["question"][:60]}")',
                        outcome_summary="blocked, awaiting human",
                        full_result_text=json.dumps(args), log_path=None,
                        transcript_entries=[("tool_use", args["question"], {"header": args.get("header")})])
    elif tool == "board_read":
        # backlog item 7 part 1, docs/spec.md §3: executes and persists in
        # the SAME round, exactly like fact_check -- never blocks. Any
        # TaigaPushError (not configured, unreachable, no such ref) is
        # caught and turned into an ordinary round outcome, never raised.
        ref = args.get("ref")
        query = args.get("query")
        args_summary = f"board_read(ref={ref}, query={query!r})"
        try:
            base_url, token, project_id = taiga_board.resolve_session()
            if ref is not None:
                result = taiga_board.get_userstory(base_url, token, project_id, ref)
            else:
                result = taiga_board.list_userstories(base_url, token, project_id, query=query)
        except taiga_board.TaigaPushError as e:
            state["action_count"] += 1
            detail = f"Taiga error: {e}"
            _append_history(state, round_n, tool="board_read", args_summary=args_summary,
                            outcome_summary=detail, full_result_text=detail, log_path=None,
                            transcript_entries=[("tool_use", args_summary, {}),
                                                ("tool_result", detail, {"ok": False})])
            _persist(state)
            return state
        state["action_count"] += 1
        result_text = json.dumps(result)
        _append_history(state, round_n, tool="board_read", args_summary=args_summary,
                        outcome_summary="ok", full_result_text=result_text, log_path=None,
                        transcript_entries=[("tool_use", args_summary, {}),
                                            ("tool_result", result_text, {"ok": True})])
    elif tool == "board_write":
        # backlog item 7 part 1, docs/spec.md §3: never calls Taiga's write
        # API itself -- queues a proposal in inbox.json (kind="board_write")
        # and blocks the run, mirroring the ask_user branch immediately
        # above. `current_value` is read via one get_userstory() call made
        # HERE, at proposal time, for DISPLAY only -- resolve_board_write()
        # always re-fetches a fresh `version` immediately before the actual
        # write, never this snapshot.
        verb, ref, value = args["verb"], args["ref"], args["value"]
        note = args.get("note")
        args_summary = f"board_write({verb}, ref={ref})"
        if state["status"] != "running":
            # Should be unreachable -- team_run()'s own loop only calls
            # team_step() while status == "running" -- but cheap to assert
            # defensively, same posture as app.py's own "already running"
            # check on POST /team/resolve. Business-rule outcome, NOT
            # malformed -- matches agent_not_on_team's own precedent.
            state["malformed_retries"] = 0
            detail = (f"a board_write or ask_user is already pending for this run "
                     f"(status={state['status']})")
            _append_history(state, round_n, tool="board_write", args_summary=args_summary,
                            outcome_summary=f"rejected: {detail}", full_result_text=detail, log_path=None,
                            transcript_entries=[("status", detail, {"reason": "board_write_already_pending"})])
            _persist(state)
            return state
        try:
            base_url, token, project_id = taiga_board.resolve_session()
            current = taiga_board.get_userstory(base_url, token, project_id, ref)
        except taiga_board.TaigaPushError as e:
            # Never queues a proposal on a snapshot failure (bad ref,
            # unreachable Taiga) -- fed back as an ordinary round outcome so
            # the lead can retry or ask_user instead (docs/spec.md
            # "Proposed approach" §3).
            state["action_count"] += 1
            detail = f"Taiga error: {e}"
            _append_history(state, round_n, tool="board_write", args_summary=args_summary,
                            outcome_summary=detail, full_result_text=detail, log_path=None,
                            transcript_entries=[("tool_use", args_summary, {}),
                                                ("tool_result", detail, {"ok": False})])
            _persist(state)
            return state
        if verb == "set_status":
            current_value = {"status_id": current["status_id"], "status_name": current["status_name"]}
        elif verb == "amend_description":
            current_value = {"description": current["description"]}
        else:  # append_comment -- no single "current value" to snapshot
            current_value = {}
        _write_board_inbox(state, verb, ref, value, note, current_value)
        state["status"] = "blocked_board_write"
        _append_history(state, round_n, tool="board_write", args_summary=args_summary,
                        outcome_summary="blocked, awaiting board-write approval",
                        full_result_text=json.dumps({"verb": verb, "ref": ref, "value": value,
                                                     "note": note, "current_value": current_value}),
                        log_path=None,
                        transcript_entries=[("tool_use", args_summary, {"verb": verb, "ref": ref})])
    else:  # finish
        state["status"] = "finished"
        state["summary"] = args["summary"]
        _append_history(state, round_n, tool="finish", args_summary="finish(...)",
                        outcome_summary="finished", full_result_text=args["summary"], log_path=None,
                        transcript_entries=[("tool_use", args["summary"], {})])

    _persist(state)
    return state


def _recover_in_progress(state: dict) -> None:
    """
    docs/spec.md "Mid-delegate crash": a round left "in_progress" by a
    process death mid-agent_run() is NEVER assumed to have succeeded on
    resume -- recorded as an error-kind event and fed to the lead as an
    unresolved result, exactly the same discipline agent_run()'s own
    _run_headless_session() already applies to a vanished tmux session.
    No-op if there is nothing in progress (the common case).
    """
    pending = state.get("in_progress_delegate")
    if not pending:
        return
    round_n = pending["round"]
    agent = pending["agent"]
    text = (f"delegation to '{agent}' possibly interrupted by a restart, outcome unknown "
           f"(task preview: {pending.get('task_preview', '')!r})")
    state["in_progress_delegate"] = None
    state["action_count"] += 1
    _append_history(state, round_n, tool="delegate", args_summary=f"delegate(agent={agent})",
                    outcome_summary="interrupted, outcome unknown", full_result_text=text, log_path=None,
                    transcript_entries=[("error", text, {"agent": agent, "interrupted": True})])
    _persist(state)


def team_run(state: dict, *, cancel_event: threading.Event = None) -> dict:
    """
    Drives team_step() in a loop until finish / ask_user / TEAM_MAX_ROUNDS.
    This is the function the CLI's team-start/team-resume/team-resolve
    subcommands call; nothing here assumes a foreground TTY, so a later
    sub-spec can run it off a background thread with zero change.

    `cancel_event` (backlog item 6d part 2a, docs/spec.md §7): optional,
    keyword-only, default None -- every existing CLI caller unaffected
    (cancel_event is never supplied there). Checked at the top of the loop,
    alongside the existing max_rounds check -- the cheapest possible
    checkpoint, avoids even starting a new round.
    """
    _recover_in_progress(state)
    while state["status"] == "running":
        if cancel_event is not None and cancel_event.is_set():
            state["status"] = "stopped"
            _persist(state)
            break
        if len(state["history"]) >= state["max_rounds"]:
            _force_ask_user(state, question=f"Team did not converge after {state['max_rounds']} rounds.",
                            header="MaxRnds", status="escalated_max_rounds")
            _persist(state)
            break
        team_step(state, cancel_event=cancel_event)
    return state


# ─── team session lifecycle: worktrees + tmux dashboard session ──────────
# (backlog item 6d, part 1, docs/spec.md) -- per-teammate git worktrees, a
# persistent team-<project> tmux session with one DASHBOARD window per agent
# (a `tail -F` over that agent's own stable, append-mode event log -- NOT a
# window whose command is the headless CLI itself; see docs/story.md §2.2's
# own "Correction" and docs/spec.md "Why dashboard windows, not process
# windows" for why that's the correct reading, not a deviation), and a
# sweep_dead_teams() self-heal function for both. CLI-only this part
# (team-launch/team-stop/team-reap) -- no HTTP route, no background thread,
# no change to how a human currently drives a run (team-resume, unchanged).

# Fixed, deliberately not an env var -- same rationale as
# _GROUNDING_READ_CAP_BYTES: the grace window between a targeted SIGTERM and
# falling back to `tmux kill-session` for a worktree op is an implementation
# detail of the escalation itself, not an operator knob a well-behaved git
# command should ever need tuned.
_WORKTREE_OP_KILL_GRACE_SECONDS = 3.0


def _run_run_user_command(argv: list, cwd: str, timeout: float = None) -> dict:
    """
    Runs argv as RUN_USER, synchronously, via the SAME TMUX constant
    app.py's instance_start()/agent_run() already use -- no new sudoers
    rule, no new privileged path (docs/ARCHITECTURE.md "Processes and
    privilege boundaries"). Spawns a throwaway tmux session (`sudo -u
    RUN_USER tmux new-session -d -c cwd bash -lc "argv...; echo $? >
    rcfile"`), polls for rcfile the same way agent_run()/_run_headless_
    session() poll for out.rc, and -- if the command outlives `timeout` --
    sends one targeted SIGTERM to the backgrounded process (reusing
    _send_signal(), the same cross-UID-signal-via-a-throwaway-tmux-session
    trick agent_run() already uses), then falls back to `tmux kill-session`
    after a short, fixed grace period if it's still not gone. A single
    TERM-then-kill-session escalation is enough for a well-behaved git
    command -- not the full multi-stage ladder _run_headless_session() uses
    for an LLM-driven engine that might ignore SIGTERM mid-tool-use.

    Returns {"ok": bool, "rc": int|None, "stdout": str, "stderr": str,
    "timed_out": bool, "error": str|None}. Never raises -- a failure to
    start the session, a permission error reading RUN_USER-written state
    files back (a strict RUN_USER umask, mirroring agent_run()'s own
    _HeadlessReadPermissionError handling), a vanished session, and a
    timeout all degrade to a specific `ok=False`/`error`, never an
    exception escaping to the caller.
    """
    if timeout is None:
        timeout = TEAM_WORKTREE_OP_TIMEOUT_SECONDS
    op_id = f"{int(time.time())}-{secrets.token_hex(6)}"
    rundir = os.path.join(TEAM_STATE_DIR, "_worktree_ops", op_id)
    session = f"switchboard-worktree-op-{op_id}"
    try:
        os.makedirs(rundir, exist_ok=True)
        os.chmod(rundir, 0o711)
        out_path = os.path.join(rundir, "out")
        err_path = os.path.join(rundir, "err")
        pid_path = os.path.join(rundir, "pid")
        rc_path = os.path.join(rundir, "rc")

        script = shlex.join(argv)
        script += f" >{shlex.quote(out_path)} 2>{shlex.quote(err_path)}"
        script += f" & echo $! >{shlex.quote(pid_path)}; wait $!; echo $? >{shlex.quote(rc_path)}"

        try:
            subprocess.run(TMUX + ["new-session", "-d", "-s", session, "-c", cwd,
                                   "bash", "-lc", script])
        except OSError as e:
            return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                    "timed_out": False, "error": f"failed to start command: {e}"}

        def _finish_if_rc_ready(rc):
            if rc is None:
                return None
            try:
                stdout = _read_stderr_tail(out_path, TEAM_HEADLESS_STDERR_TAIL_BYTES)
                stderr = _read_stderr_tail(err_path, TEAM_HEADLESS_STDERR_TAIL_BYTES)
            except _HeadlessReadPermissionError as e:
                return {"ok": False, "rc": rc, "stdout": "", "stderr": "",
                        "timed_out": False, "error": f"permission denied reading {e.path}"}
            return {"ok": rc == 0, "rc": rc, "stdout": stdout, "stderr": stderr,
                    "timed_out": False,
                    "error": None if rc == 0 else (stderr.strip() or f"command exited with code {rc}")}

        deadline = time.time() + timeout
        pid = None
        escalated_at = None
        while True:
            try:
                if pid is None:
                    pid = _read_int_file(pid_path)
                rc = _read_int_file(rc_path)
            except _HeadlessReadPermissionError as e:
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "timed_out": False, "error": f"permission denied reading {e.path}"}
            result = _finish_if_rc_ready(rc)
            if result is not None:
                return result
            if not tmux_has(session):
                # The rc-file WRITE always causally precedes the pane
                # process exiting (bash's own last statement is `echo $? >
                # rcfile`, only after which the script -- and therefore the
                # pane's sole process -- exits), so once tmux_has() has
                # transitioned to False the write is guaranteed to already
                # be on disk. But OUR two observations (the rc read a few
                # lines above, and this has-session check) are not atomic
                # with each other -- for a very fast command (a plain
                # `echo`, `git status` against a small repo) the entire
                # spawn-run-exit-teardown sequence can complete within a
                # single polling iteration, faster than our own rc read
                # noticed it. A real, frequently-reproducing race found by
                # exercising this for real (not a hypothetical): re-read
                # rc_path ONE more time before concluding "vanished without
                # a recorded exit code" -- structurally closes the gap
                # rather than papering over it with a retry/sleep.
                try:
                    rc = _read_int_file(rc_path)
                except _HeadlessReadPermissionError as e:
                    return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                            "timed_out": False, "error": f"permission denied reading {e.path}"}
                result = _finish_if_rc_ready(rc)
                if result is not None:
                    return result
                # Genuinely gone with no exit code ever recorded (the whole
                # tmux server killed, kill-session called externally, disk
                # failure mid-write) -- never optimistically assumed clean,
                # same discipline _run_headless_session() already applies
                # to this exact ambiguity for the headless-engine case.
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "timed_out": False, "error": "command session ended unexpectedly"}
            now = time.time()
            if now >= deadline and escalated_at is None:
                if pid is not None:
                    _send_signal(session, pid, "TERM")
                escalated_at = now
            elif escalated_at is not None and (now - escalated_at) >= _WORKTREE_OP_KILL_GRACE_SECONDS:
                subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "timed_out": True, "error": f"command timed out after {timeout}s"}
            time.sleep(0.1)
    finally:
        shutil.rmtree(rundir, ignore_errors=True)
        if tmux_has(session):
            subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)


def _validate_project_for_team(workdir: str):
    """
    Read-only checks against the project's own working tree (NOT a
    worktree -- the main directory a team is launched against). Run as
    SVC_USER, plain subprocess.run(["git", "-C", workdir, ...]) -- no
    privilege crossing needed, matching the existing precedent that
    grounding discovery (load_grounding()) already reads project files
    directly as SVC_USER with no TMUX involvement. Returns a specific error
    message, or None if the project is a clean, non-detached git repo.
    Three distinct checks, three distinct messages, in order:
      1. `git -C workdir rev-parse --is-inside-work-tree` fails/!= "true"
         -> "not a git repository"
      2. `git -C workdir symbolic-ref -q HEAD` fails (detached HEAD)
         -> "HEAD is detached -- check out a branch before starting a team"
      3. `git -C workdir status --porcelain` non-empty (tracked OR
         untracked changes -- settled by the user, docs/spec.md "Open
         questions": the safer default)
         -> "working tree has uncommitted changes -- commit or stash them
             before starting a team"
    Never raises -- a missing git binary or an unexpected subprocess error
    also degrades to a specific, non-traceback message.
    """
    try:
        r = subprocess.run(["git", "-C", workdir, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    except OSError as e:
        return f"could not run git: {e}"
    if r.returncode != 0 or r.stdout.strip() != "true":
        return "not a git repository"

    try:
        r = subprocess.run(["git", "-C", workdir, "symbolic-ref", "-q", "HEAD"],
                           capture_output=True, text=True)
    except OSError as e:
        return f"could not run git: {e}"
    if r.returncode != 0:
        return "HEAD is detached -- check out a branch before starting a team"

    try:
        r = subprocess.run(["git", "-C", workdir, "status", "--porcelain"],
                           capture_output=True, text=True)
    except OSError as e:
        return f"could not run git: {e}"
    if r.returncode != 0:
        return f"could not check working tree status: {r.stderr.strip() or 'unknown error'}"
    if r.stdout.strip():
        return "working tree has uncommitted changes -- commit or stash them before starting a team"
    return None


def _worktree_path(project_workdir: str, agent: str) -> str:
    return f"{project_workdir}.teams/{agent}"  # docs/spec.md §3, verbatim


def _create_worktree(project_workdir: str, agent: str, run_id: str) -> dict:
    """
    {"ok": True, "path": ..., "branch": ...} or {"ok": False, "error": ...}.
    A leftover from a previous run (the path already exists -- including the
    same-launch-attempt "member named twice" case, docs/spec.md "Edge
    cases") gets a SPECIFIC message naming the agent and path, never a raw
    git "already exists" stderr dump. On a real `git worktree add` failure,
    the error is the command's own stderr tail, never a bare non-zero exit
    with no explanation.
    """
    path = _worktree_path(project_workdir, agent)
    if os.path.exists(path):
        return {"ok": False, "error":
                f"a previous team run's worktree for '{agent}' still has uncommitted "
                f"changes at {path} -- resolve and remove it manually "
                "(`git worktree remove --force`) before starting a new team"}
    branch = f"team-{run_id}-{agent}"
    result = _run_run_user_command(
        ["git", "-C", project_workdir, "worktree", "add", "-b", branch, path, "HEAD"],
        cwd=project_workdir)
    if not result["ok"]:
        detail = (result.get("stderr") or "").strip() or result.get("error") or "unknown error"
        return {"ok": False, "error": f"failed to create worktree for '{agent}': {detail}"}
    return {"ok": True, "path": path, "branch": branch}


def _remove_worktree(project_workdir: str, path: str) -> str:
    """
    `git -C project_workdir worktree remove {path}` (NO --force) via
    _run_run_user_command(). Three-way outcome, not a bool: "removed" (rc
    0), "dirty" (git refused because of local/untracked changes -- path is
    left exactly as-is, branch untouched -- git's own real behavior,
    confirmed live: "contains modified or untracked files, use --force to
    delete it"), "error" (anything else -- also left as-is). Callers report
    "dirty" distinctly from "error", since a dirty worktree being left
    behind is the INTENDED safety behavior, not a failure. Callers are
    responsible for checking existence first (this function assumes `path`
    exists -- a nonexistent path is a caller-level "absent" case, not one of
    this function's three).
    """
    result = _run_run_user_command(
        ["git", "-C", project_workdir, "worktree", "remove", path],
        cwd=project_workdir)
    if result["ok"]:
        return "removed"
    stderr = (result.get("stderr") or "").lower()
    if "modified or untracked files" in stderr or "use --force" in stderr:
        return "dirty"
    return "error"


# Matches _create_worktree()'s own "team-{run_id}-{agent}" naming convention
# (app/teams.py:3428) exactly, reusing _RUN_ID_RE's own run_id shape (rather
# than a looser "team-<anything>-<anything>" split) so a hand-created branch
# that merely starts with "team-" -- or an agent name containing its own
# hyphens -- is never misparsed into a bogus run_id/agent pair: the run_id
# group can only match a string _run_id() could actually have generated,
# whatever's left (including embedded hyphens) is the agent name verbatim.
_TEAM_BRANCH_RE = re.compile(r"^team-([0-9]+-[0-9a-f]{12})-(.+)$")


def list_team_branches(project_workdir: str) -> list[dict]:
    """
    Read-only: `git branch --list 'team-*'` against project_workdir
    (backlog item 13, docs/spec.md). Returns one dict per matching branch:
      {"branch": str, "run_id": str|None, "agent": str|None,
       "commit": str, "subject": str, "committer_date": str}
    run_id/agent are parsed from the "team-{run_id}-{agent}" naming
    convention (_create_worktree()'s own format, via _TEAM_BRANCH_RE) on a
    best-effort basis -- both None if a branch name doesn't match (e.g. a
    hand-created branch that happens to start with "team-"); never raises on
    a parse miss.

    Plain subprocess.run(["git", "-C", project_workdir, ...]), the same
    read-your-own-checkout-directly convention _validate_project_for_team()
    already establishes above -- not _run_run_user_command(): this reads the
    switchboard's own project checkout, no RUN_USER privilege crossing
    needed.

    Returns [] if project_workdir isn't a git repo, has zero matching
    branches, or the git command fails for any reason (including a missing
    git binary) -- this is a read-only convenience listing, not load-bearing
    for any run's own state, so it degrades silently rather than raising.
    """
    fmt = "%(refname:short)\t%(objectname)\t%(committerdate:iso-strict)\t%(subject)"
    try:
        r = subprocess.run(
            ["git", "-C", project_workdir, "branch", "--list", "team-*", f"--format={fmt}"],
            capture_output=True, text=True)
    except OSError:
        return []
    if r.returncode != 0:
        return []
    branches = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue  # never raises on a malformed/unexpected line
        branch, commit, committer_date, subject = parts
        m = _TEAM_BRANCH_RE.match(branch)
        branches.append({
            "branch": branch,
            "run_id": m.group(1) if m else None,
            "agent": m.group(2) if m else None,
            "commit": commit,
            "subject": subject,
            "committer_date": committer_date,
        })
    return branches


def _agent_log_path(run_id: str, agent: str) -> str:
    return os.path.join(_run_dir(run_id), "agents", f"{agent}.jsonl")


def _team_session_name(project_name: str) -> str:
    return f"team-{project_name}"


# tmux session-scoped user option (must start with "@", tmux's own
# convention) this module stamps onto every team-<project> session at
# creation time -- see _kill_team_session_if_owned()'s own docstring for why
# this exists: team-<project> session NAMES are scoped to the project, not
# to any one run_id, so once one run's session is gone, a later, different
# run for the SAME project can legitimately create another session under
# the IDENTICAL name. Without a way to tell "is the currently-live
# team-<project> session actually mine", a stale stop_team()/
# sweep_dead_teams() pass over an old, terminal run could kill a newer run's
# live session out from under it -- a real defect found by exercising a
# crash-then-relaunch-then-reap sequence for real, not a hypothetical.
_TEAM_SESSION_RUN_ID_OPTION = "@switchboard_team_run_id"


def _team_session_run_id(session: str) -> str:
    """Returns the run_id stamped into `session`'s own tmux user option, or
    "" if the session doesn't exist or was never stamped (e.g. a
    partially-failed _create_team_session() call)."""
    r = subprocess.run(TMUX + ["show-options", "-t", session, "-v", _TEAM_SESSION_RUN_ID_OPTION],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _kill_team_session_if_owned(session: str, run_id: str) -> bool:
    """
    Kills `session` and returns True (removed, or was already absent)
    UNLESS it exists AND is stamped with a DIFFERENT run_id than `run_id` --
    in which case it's left completely alone (it belongs to a newer run for
    the same project) and this returns True anyway, since from THIS run_id's
    own perspective its session is not there to remove either way (it was
    never that one). An unstamped session is treated as safe to kill.

    This is only correct because _create_team_session() creates AND stamps
    a team-<project> session in ONE atomic tmux client invocation (`new-
    session ... ; set-option ... remain-on-exit ... ; set-option ...
    _TEAM_SESSION_RUN_ID_OPTION ...`, chained via tmux's own `;` command
    separator) -- there is never a moment where a session with this name
    exists on the server but hasn't been stamped yet. An earlier version of
    this function's docstring claimed the same thing about three SEPARATE
    tmux calls ("should not happen in practice") -- that claim was false: a
    real, independently-confirmed race existed in that window (no locking
    exists anywhere in this module; sweep_dead_teams() runs opportunistically
    inside every launch_team()/stop_team() call for ANY project, not just
    the one being launched, so the window was reachable without any
    concurrency on the SAME project at all -- a solo crash of the creating
    process between the `new-session` and the stamp was enough). Fixed by
    removing the observable unstamped state entirely (atomic creation),
    not by making this function fail-closed on an unstamped session -- a
    fail-closed rule here would instead strand a genuinely orphaned
    unstamped session (the one class the OLD three-call code really could
    still produce, e.g. a crash between the `new-session` reply and the
    stamp) permanently, requiring manual `tmux kill-session` cleanup. See
    docs/implementation.md "Defects found and fixed" for the full history.
    """
    if not tmux_has(session):
        return True
    owner = _team_session_run_id(session)
    if owner and owner != run_id:
        return True  # belongs to a different, newer run -- not touched
    subprocess.run(TMUX + ["kill-session", "-t", session], capture_output=True)
    return not tmux_has(session)


def _create_team_session(project_name: str, run_id: str, members: list) -> dict:
    """
    {"ok": True, "session": ...} or {"ok": False, "error": ...}. Refuses up
    front (tmux_has() check, same precondition style instance_start() already
    uses for active_engine()) if a session with this name already exists --
    never lets a raw `tmux new-session` "duplicate session" error be the
    thing that surfaces. Window 0 named "lead" (transcript.jsonl), one window
    per member in --members order, named after the member's own engine name
    (that agent's own .jsonl log, see _agent_log_path()). Each window's pane
    command is `tail -n +1 -F {log_path} || sleep infinity` (`-n +1` so
    attaching mid-run shows full history, not just new lines; `|| sleep
    infinity` keeps the window alive/attachable even if `tail` itself fails
    outright, e.g. a permission edge case, rather than the window vanishing).

    The session is created, `remain-on-exit on` set, AND stamped with its
    own run_id as a tmux user option (see _TEAM_SESSION_RUN_ID_OPTION) --
    all in ONE atomic tmux client invocation, chained via tmux's own `;`
    command separator (its own argv element, not shell-escaped -- no shell
    is involved, subprocess.run() gets a plain argv list). This is
    deliberate, not a style choice: tmux processes every command in one
    `;`-chained batch from a single client connection without yielding to
    any OTHER client's request in between (the whole point of `;`-chaining,
    verified directly against the real binary), so there is never a moment
    where a session with this name exists on the server but isn't stamped
    yet -- closing a real race an earlier, three-separate-calls version of
    this function had (see _kill_team_session_if_owned()'s own docstring
    for the full history: no locking exists anywhere in this module, and
    sweep_dead_teams() runs opportunistically inside every launch_team()/
    stop_team() call for ANY project, so the unstamped window was reachable
    even from a solo crash with no concurrency at all). What that race
    could produce was not merely a killed session -- launch_team() could
    return a false {"ok": True} for a session already-dead-but-still-
    starting, self-healing only whenever a later sweep happened to run.

    Callers (launch_team()) are responsible for pre-touching/chmod'ing every
    log file this creates a window over BEFORE calling this -- see
    launch_team()'s own docstring for why (the window's `tail -F` must never
    race file creation, and read permission must never depend on _Tailer's
    own default append-mode `open()` widening an already-existing file).

    **The `;`-chain is atomic against a concurrent OBSERVER, not against a
    failure partway through it -- these are two different guarantees, and
    only the first was closed by making creation+stamping one call.**
    Confirmed live: if `new-session` itself fails (e.g. a genuine
    duplicate-session race between this call's own upfront `tmux_has()`
    check and its actual `new-session` -- see below, this race very much
    CAN happen and does, reliably, under real concurrent launches for the
    SAME project), tmux aborts the WHOLE chain before any `set-option`
    runs, so a losing racer's chain can never MODIFY a winning racer's
    already-stamped session. But if `new-session` SUCCEEDS and a LATER
    link fails (in practice only the run_id `set-option` could --
    `remain-on-exit`'s value is a hardcoded literal, and `@`-prefixed user
    options accept any string, so neither is expected to fail under normal
    operation), tmux does NOT roll back the already-successful
    `new-session` -- the session survives, alive, unstamped. Left alone,
    this is a lockout with no self-heal path: every future launch_team()
    for this project is refused by the upfront tmux_has() check, sweep_
    dead_teams() can never find it (this call's own caller, launch_team(),
    already deleted the run's run.json on this failure path), and recovery
    is manual `tmux kill-session` only -- the same "fallible call sitting
    outside its own cleanup, leaking a resource" shape as 6a's own first
    defect (there, a rundir; here, a tmux session).

    **On this failure path, the session (if one now exists at all) is
    killed via `_kill_team_session_if_owned(session, run_id)` -- not a raw,
    unconditional `kill-session`.** An earlier version of this function
    used a raw kill (reasoning: "this call's own upfront `tmux_has()`
    check already confirmed no session existed when THIS call started, and
    a failing `new-session` aborts before any `set-option`, so a
    concurrent second caller can never reach this branch holding someone
    ELSE's session at this name"). **That reasoning was false, and the
    defect it produced was real, not theoretical**: it only accounts for
    ordering AFTER this call's own `new-session` attempt, not for a
    DIFFERENT concurrent caller's `new-session` succeeding in the window
    between THIS call's own upfront check and its own `new-session`
    attempt (both callers can observe `tmux_has(session)` as False at
    their own upfront check, since neither has created anything yet) --
    when that happens, THIS call's own `new-session` fails with tmux's
    real "duplicate session" error (nothing of this call's own creation),
    but `tmux_has(session)` is now True because the OTHER caller's session
    exists. The old, raw `kill-session` could not tell the difference and
    killed the OTHER (winning) caller's real, live session. Reproduced
    directly with two real, barrier-synchronized concurrent `_create_team_
    session()` calls for the same project: 20/20 trials destroyed the
    winner's session under the old code (see `SessionCreationRaceRealTmux
    Tests`). `_kill_team_session_if_owned()` closes this correctly, reusing
    the SAME ownership-stamp mechanism that already makes `stop_team()`'s/
    `sweep_dead_teams()`'s own analogous races safe: the winner's session,
    once observable at all, is ALWAYS already fully stamped (atomicity, see
    above) with the WINNER's own run_id -- a losing racer's own run_id
    never matches it, so `_kill_team_session_if_owned()` correctly leaves
    it alone. THIS call's own genuine partial-creation leftover (new-
    session succeeded, the later stamp link failed) is, by construction,
    UNSTAMPED -- which `_kill_team_session_if_owned()` already treats as
    safe to kill for any run_id, correctly reclaiming it.
    """
    session = _team_session_name(project_name)
    if tmux_has(session):
        return {"ok": False, "error": f"a team session is already running for "
                                       f"'{project_name}' ({session})"}
    lead_log = _transcript_path(run_id)
    try:
        r = subprocess.run(TMUX + [
            "new-session", "-d", "-s", session, "-n", "lead",
            "bash", "-lc", f"tail -n +1 -F {shlex.quote(lead_log)} || sleep infinity",
            ";", "set-option", "-t", session, "remain-on-exit", "on",
            ";", "set-option", "-t", session, _TEAM_SESSION_RUN_ID_OPTION, run_id,
        ])
    except OSError as e:
        return {"ok": False, "error": f"failed to create team session: {e}"}
    if r.returncode != 0 or not tmux_has(session):
        # `new-session` itself may have failed -- either nothing was ever
        # created (this call's own chain aborted before creating anything),
        # OR a DIFFERENT, concurrently-racing caller's `new-session` won
        # for this exact session name (this call's own upfront tmux_has()
        # check passed when nothing existed yet, but the OTHER caller
        # created+stamped its session before THIS call's own new-session
        # attempt reached the server -- tmux's real "duplicate session"
        # error, not a shape this code created). OR `new-session` succeeded
        # for THIS call and a LATER link failed (the session exists,
        # half-configured, unstamped -- a genuine leftover of THIS call).
        # `tmux_has(session)` being true here does NOT by itself
        # distinguish "my own leftover" from "someone else's live, winning
        # session" -- ownership-checked cleanup via _kill_team_session_if_
        # owned(), not a raw kill-session (see this function's own
        # docstring for the real, reproduced defect the raw-kill version
        # of this line used to cause).
        _kill_team_session_if_owned(session, run_id)
        return {"ok": False, "error": "failed to create team session (tmux new-session failed)"}
    for agent in members:
        log_path = _agent_log_path(run_id, agent)
        subprocess.run(TMUX + ["new-window", "-t", session, "-n", agent, "bash", "-lc",
                               f"tail -n +1 -F {shlex.quote(log_path)} || sleep infinity"])
    return {"ok": True, "session": session}


def launch_team(workdir: str, task: str, lead: dict, members: list,
                max_rounds: int = None) -> dict:
    """
    {"ok": True, "run_id": ..., "session": "team-<name>",
     "worktrees": {agent: path, ...}} or {"ok": False, "error": ...}.
    Order (docs/spec.md §7): sweep dead teams opportunistically (same "not a
    background thread/timer" philosophy _sweep_stale_runs() already
    documents) -> validate project (§2) -> refuse if the team session name
    is already taken (§6, BEFORE touching any worktree) -> create worktrees
    for each member in order, rolling back on partial failure (§3) ->
    _new_state() + _persist() (status="running", 0 rounds -- team-resume
    drives it, unchanged from 6c) -> create the tmux session/windows (§6),
    rolling back (remove all worktrees just created + delete the fresh
    run_id's state dir) if session creation itself fails. Does NOT drive the
    lead loop -- that's still team-resume <run_id>, completely unchanged
    from 6c (a freshly-launched run's status="running", 0 rounds is exactly
    what team-resume already expects).
    """
    sweep_dead_teams()
    err = _validate_project_for_team(workdir)
    if err:
        return {"ok": False, "error": err}

    project_name = os.path.basename(os.path.normpath(workdir))
    if not project_name:
        # backlog item 6d part 2a, docs/spec.md §8: a filesystem-root
        # workdir would otherwise yield a degenerate "team-" session name.
        # Carried forward from part 1's own disclosed, deliberately-left-
        # open item, closed now that part 2's HTTP route is the first real
        # caller that could plausibly reach this with an operator-adjacent
        # path (though in practice it still can't -- workdir is always
        # PROJECTS_DIR/<name> with an already-NAME_RE-validated <name>).
        return {"ok": False, "error": "workdir has no derivable project name"}
    session = _team_session_name(project_name)
    if tmux_has(session):
        return {"ok": False, "error": f"a team is already running for '{project_name}' "
                                       f"({session}) -- stop it first"}

    run_id = _run_id()
    worktrees = {}
    for agent in members:
        result = _create_worktree(workdir, agent, run_id)
        if not result["ok"]:
            for done_path in worktrees.values():
                _remove_worktree(workdir, done_path)
            return {"ok": False, "error": result["error"]}
        worktrees[agent] = result["path"]

    state = _new_state(run_id, workdir, lead, members, task, max_rounds,
                       project_name=project_name, worktrees=worktrees)
    _persist(state)

    # Permission requirement (docs/spec.md §5): every file a dashboard
    # window's `tail -F` needs to read is written by SVC_USER but must be
    # readable by RUN_USER's tmux pane -- chmod explicitly rather than
    # relying on SVC_USER's ambient umask, the exact same discipline
    # agent_run() already uses for prompt_path/schema_path. Done BEFORE
    # _create_team_session() so no window's `tail -F` ever races file
    # creation.
    d = _run_dir(run_id)
    agents_dir = os.path.join(d, "agents")
    os.makedirs(agents_dir, exist_ok=True)
    os.chmod(d, 0o755)
    os.chmod(agents_dir, 0o755)
    transcript_path = _transcript_path(run_id)
    open(transcript_path, "a").close()
    os.chmod(transcript_path, 0o644)
    for agent in members:
        log_path = _agent_log_path(run_id, agent)
        open(log_path, "a").close()
        os.chmod(log_path, 0o644)

    session_result = _create_team_session(project_name, run_id, members)
    if not session_result["ok"]:
        for path in worktrees.values():
            _remove_worktree(workdir, path)
        shutil.rmtree(d, ignore_errors=True)
        return {"ok": False, "error": session_result["error"]}

    return {"ok": True, "run_id": run_id, "session": session, "worktrees": worktrees}


def stop_team(run_id: str) -> dict:
    """
    Unconditional -- works regardless of status (running / blocked_ask_user
    / finished / error / escalated_max_rounds / stopped), same "an explicit
    human action always wins" precedent instance_stop() already sets. Kills
    the team-<project> tmux session (if present -- a no-op, not an error, if
    it's already gone, or if this run never had one -- a bare team-start
    that skipped team-launch). Attempts _remove_worktree() for EVERY
    teammate regardless of an earlier one's outcome (one dirty/errored
    worktree never aborts the rest). If status was non-terminal, marks it
    "stopped" (a new status value, additive to the existing enum) and
    persists. Raises FileNotFoundError for an unknown run_id (same as
    _load_state() itself -- callers/CLI handle it the same way team-status/
    team-resolve/team-resume already do).

    Returns {"session_removed": bool, "worktrees": {agent: "removed"|
    "dirty"|"error"|"absent", ...}}.

    A "removed"/"absent" outcome ALSO drops that agent's entry from the
    persisted state["worktrees"] map before writing it back (a real defect
    found by exercising this for real, not a spec-literal requirement: the
    same project+agent's worktree PATH is not run-scoped -- _worktree_path()
    is purely a function of project_workdir+agent, docs/spec.md §3 -- so a
    LATER, unrelated run against the same project+agent can legitimately
    recreate a worktree at that identical path once this run's copy is
    gone. If this run's own stale "removed" path entry were left in place,
    a future sweep_dead_teams()/team-stop call re-processing THIS run would
    find a directory sitting at that path again -- now the LATER run's live
    worktree -- and destroy it, believing it was reclaiming its own leftover
    resource. "dirty"/"error" entries are kept (the directory is genuinely
    still there, and _create_worktree()'s own "path already exists" check
    already protects it from being silently overwritten by a future launch,
    so leaving the record in place for a human to find via team-status is
    both safe and useful).
    """
    sweep_dead_teams()
    state = _load_state(run_id)
    project_name = state.get("project_name")
    session_removed = True
    if project_name:
        session = _team_session_name(project_name)
        # Ownership-checked (_kill_team_session_if_owned(), not a raw
        # kill-session): the currently-live team-<project> session might no
        # longer be THIS run's own -- a different, newer run for the same
        # project could have created another session under the identical
        # name after this one's own session already ended some other way.
        session_removed = _kill_team_session_if_owned(session, run_id)

    worktrees_result = {}
    remaining_worktrees = {}
    for agent, path in (state.get("worktrees") or {}).items():
        if not os.path.exists(path):
            worktrees_result[agent] = "absent"
            continue
        outcome = _remove_worktree(state["workdir"], path)
        worktrees_result[agent] = outcome
        if outcome in ("dirty", "error"):
            remaining_worktrees[agent] = path
    state["worktrees"] = remaining_worktrees

    if state["status"] not in ("finished", "escalated_max_rounds", "error", "stopped"):
        state["status"] = "stopped"
    _persist(state)

    return {"session_removed": session_removed, "worktrees": worktrees_result}


def mark_run_error(run_id: str, message: str) -> None:
    """
    Backlog item 6d part 2a. Loads the run's own persisted state fresh
    (never trusts a caller-held copy -- same discipline every other
    run.json-touching function here already follows), sets
    status="error"/error=message, and persists. Raises FileNotFoundError
    for an unknown run_id, same as _load_state() itself -- callers (the
    background driving thread's own try/except, _team_reap_if_due()'s
    orphan check) handle that the same way every other run.json reader
    already does.

    Unconditional, like stop_team()'s own status-setting line -- an
    unexpected exception mid-run (app.py's _run_team_in_background()) or a
    detected orphaned driving thread (_team_reap_if_due()) both mean "this
    run's own on-disk status can no longer be trusted to self-correct", so
    this always overwrites it. No lock on run.json -- same accepted
    "whichever writer's _persist() call lands last wins" tradeoff docs/
    spec.md §7 already documents for the cancel_event/stop_team() race; a
    genuinely still-live writer (e.g. a concurrent CLI team-resume) simply
    overwrites this on its own next round, exactly as docs/spec.md's own
    disclosed, accepted "Open questions" tradeoff describes.
    """
    state = _load_state(run_id)
    state["status"] = "error"
    state["error"] = message
    _persist(state)


def latest_run_for_project(project_name: str) -> dict | None:
    """
    The persisted state dict of the most-recently-updated run recorded for
    this project_name, or None if no run has ever been launched for it
    (backlog item 6d part 2a). At most one run can be non-terminal (status
    in running/blocked_ask_user/blocked_board_write, the last added by
    backlog item 7 part 1) at a time -- launch_team()'s own session-name
    collision check (part 1) -- so when a live run exists it is always also
    the most recent; callers that only care about a LIVE run check
    `.get("status") in ("running", "blocked_ask_user", "blocked_board_write")`
    on the result rather than needing a separate function. Same O(all runs under
    _leads_root()) scan idiom sweep_dead_teams() already uses (docs/spec.md
    "Non-goals" -- accepted at this project's scale). An unreadable/corrupt
    run.json for one run_id is skipped, not fatal, matching sweep_dead_
    teams()'s own discipline; a run with no project_name (a bare team-start
    that skipped team-launch, 6c-only) is skipped too -- it was never
    launched for any project by this function's own definition.
    """
    root = _leads_root()
    if not os.path.isdir(root):
        return None
    latest = None
    latest_epoch = -1.0
    for run_id in os.listdir(root):
        try:
            state = _load_state(run_id)
        except (OSError, ValueError):
            continue
        if state.get("project_name") != project_name:
            continue
        try:
            epoch = _iso_to_epoch(state.get("updated_at", ""))
        except ValueError:
            continue
        if epoch > latest_epoch:
            latest_epoch = epoch
            latest = state
    return latest


def _iso_to_epoch(s: str) -> float:
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


def sweep_dead_teams() -> list:
    """
    Opportunistic (called at the top of launch_team()/stop_team(), plus its
    own dedicated team-reap CLI subcommand for explicit/scripted use and
    straightforward testing) -- same "not a background thread/timer"
    philosophy _sweep_stale_runs() already documents. For every run_id under
    _leads_root() with a project_name (a bare team-start that skipped
    team-launch has no team session/worktrees to sweep -- skipped entirely):

      1. status in (running, blocked_ask_user, blocked_board_write) but the
         team-<project> session is gone (tmux_has() false) -- crash/reboot:
         mark status="error", error naming what was observed, persist, and
         STOP for this run_id this pass (does not fall through to step 2 in
         the SAME call -- "now terminal" means eligible starting the NEXT
         sweep pass, matching the acceptance criterion's own two-pass
         sequence: crash-detection first, worktree/session sweep only on a
         subsequent call). blocked_board_write (backlog item 7 part 1,
         docs/spec.md §4) joins blocked_ask_user here for the identical
         reason: it's also a normal, expected stopping point where nothing
         is "in progress", not a live-processing status.
      2. status in (finished, escalated_max_rounds, error, stopped) AND age
         since updated_at > TEAM_SESSION_STALE_TTL_SECONDS -- sweep: kill
         the session if still present, attempt _remove_worktree() for every
         teammate (best-effort; one failure doesn't stop the rest), leave
         run.json/transcript.jsonl in place (matches TEAM_HEADLESS_STALE_
         RUN_TTL_SECONDS's own precedent -- the state RECORD persists, only
         the live session/worktrees are reclaimed).
      3. status in ("blocked_ask_user", "blocked_board_write") is NEVER
         swept by TTL, at any age (docs/spec.md "Open questions" -- settled
         by the user: no backstop TTL this cycle) -- its session/worktrees
         stay alive so a human can `tmux attach` for context before
         answering/resolving, same reasoning docs/spec.md "Resolved from
         docs/story.md §5's own open notes" already gives.

    Returns a list of {run_id, action, detail} for team-reap's own printed
    report. Never raises -- a corrupt/missing run.json for one run_id is
    skipped, not fatal to the sweep of every other run_id.
    """
    results = []
    root = _leads_root()
    if not os.path.isdir(root):
        return results
    now = time.time()
    for run_id in sorted(os.listdir(root)):
        try:
            state = _load_state(run_id)
        except (OSError, ValueError):
            results.append({"run_id": run_id, "action": "skipped",
                            "detail": "unreadable/corrupt run.json"})
            continue

        project_name = state.get("project_name")
        if not project_name:
            continue

        status = state.get("status")
        session = _team_session_name(project_name)

        if status in ("running", "blocked_ask_user", "blocked_board_write"):
            if not tmux_has(session):
                state["status"] = "error"
                state["error"] = f"team session '{session}' is gone (crash or reboot)"
                _persist(state)
                results.append({"run_id": run_id, "action": "marked_error",
                                "detail": state["error"]})
            continue  # never sweep worktrees/session in this same pass

        if status not in ("finished", "escalated_max_rounds", "error", "stopped"):
            continue  # some other/unknown status -- nothing this sweep does

        try:
            age = now - _iso_to_epoch(state.get("updated_at", ""))
        except ValueError:
            continue  # unparsable updated_at -- never guess an age
        if age <= TEAM_SESSION_STALE_TTL_SECONDS:
            continue

        # Ownership-checked, same reasoning as stop_team()'s own comment: the
        # currently-live team-<project> session might belong to a newer run
        # for the same project, not to run_id (a real collision found by
        # exercising crash-then-relaunch-then-reap for real).
        removed_session = _kill_team_session_if_owned(session, run_id)
        worktrees_result = {}
        remaining_worktrees = {}
        for agent, path in (state.get("worktrees") or {}).items():
            if not os.path.exists(path):
                worktrees_result[agent] = "absent"
                continue
            outcome = _remove_worktree(state["workdir"], path)
            worktrees_result[agent] = outcome
            if outcome in ("dirty", "error"):
                remaining_worktrees[agent] = path
        # A "removed"/"absent" entry is dropped from the persisted
        # state["worktrees"] map -- see stop_team()'s own docstring for why
        # (the path is not run-scoped, so leaving a stale entry here risks a
        # LATER run's live worktree at the same path being destroyed by a
        # future sweep pass re-processing THIS already-terminal run).
        state["worktrees"] = remaining_worktrees
        _persist(state)
        results.append({"run_id": run_id, "action": "swept",
                        "detail": {"session_removed": removed_session,
                                   "worktrees": worktrees_result}})
    return results


# ─── Overwatch feed + escalation inbox (backlog item 6f part 1, docs/spec.md) ──
def load_state_for_project(run_id: str, project_name: str) -> dict | None:
    """
    docs/spec.md §1's ownership check, shared by GET .../team/events and
    GET .../team/inbox for their own optional, explicit `run_id` override:
    loads run_id's state and returns it iff it both exists AND belongs to
    project_name, else None -- the two GET routes reply 404 either way,
    collapsing "no such run" and "wrong project" into one outcome (unlike
    POST /team/resolve, docs/spec.md §3, which needs to tell those two
    apart for its own error message, so that route calls _load_state()
    directly instead of this helper). Same "an unreadable/corrupt run.json
    is skipped, not fatal" discipline latest_run_for_project() already
    applies, here surfaced as "not found" rather than a 500.
    """
    try:
        state = _load_state(run_id)
    except (OSError, ValueError):
        return None
    if state.get("project_name") != project_name:
        return None
    return state


def tail_jsonl_events(path: str, offset: int, max_bytes: int, agent: str = None) -> tuple:
    """
    docs/spec.md §1's stricter, per-poll-bounded cousin of _tail_log_once()
    (see below) -- that CLI-only tailer has no byte cap and silently drops
    a trailing partial line via splitlines(), fine for its own
    print-to-stderr-on-a-0.2s-loop use but not for GET .../team/events's
    own "never lose, never duplicate a partial line across a poll
    boundary" requirement.

    Reads at most max_bytes + 1 bytes starting at `offset` -- the +1 solely
    to detect whether more complete-line data exists beyond the cap (the
    `truncated` return value); that extra byte, if read, is never itself
    parsed as part of any event. Splits on b"\\n"; the LAST element (a
    possibly-partial trailing line -- including the case where this call's
    own bounded read stopped mid-line exactly at max_bytes) is never turned
    into an event, and `new_offset` is walked back to just after the last
    complete "\\n" actually seen in the (at-most-max_bytes) portion this
    call processes -- so the very next call, given the returned new_offset,
    picks up exactly where this one stopped, on a clean line boundary,
    byte-for-byte (no gap, no duplicate) -- the same "hold a partial line
    across polls" discipline _Tailer.poll() already uses
    (app/teams.py:673-679).

    A line that fails to parse as JSON, or parses to something other than a
    JSON object, becomes one synthetic {"kind": "error", ...} envelope for
    that position rather than raising or being silently dropped -- mirrors
    _Tailer._handle_line's own malformed-line discipline
    (app/teams.py:691-698); the rest of this call's lines, and every other
    file's own poll, are unaffected.

    Returns (events: list[dict], new_offset: int, truncated: bool).
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(max_bytes + 1)
    except FileNotFoundError:
        return [], offset, False
    truncated = len(raw) > max_bytes
    data = raw[:max_bytes] if truncated else raw
    lines = data.split(b"\n")
    trailing_partial = lines.pop()  # never parsed this call -- held for the next poll
    new_offset = offset + (len(data) - len(trailing_partial))
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            label = agent or os.path.basename(path)
            events.append({
                "ts": _now_iso(), "agent": label, "seq": 0, "kind": "error",
                "text": f"malformed line in {label}'s log (json.loads failed)",
                "meta": {"raw_bytes": len(line)},
            })
            continue
        events.append(obj)
    return events, new_offset, truncated


def resolve_ask_user(run_id: str, answer: str) -> dict:
    """
    Extracted from _cli_team_resolve()'s own body (backlog item 6f part 1,
    docs/spec.md §3) so the CLI and the new POST /team/resolve route share
    exactly one load-check-append-move-flip-persist sequence and can never
    drift apart. Always reloads state fresh via _load_state(run_id) itself
    -- never accepts a caller-supplied state dict -- so a concurrent
    resolve for the same run_id (two tabs, a double-submit) always
    re-checks status against what is ACTUALLY on disk at the moment this
    call runs, not a value the caller read earlier (docs/spec.md "Edge
    cases", two concurrent POST /team/resolve calls: the first to persist
    wins, the second's own freshly-reloaded state here sees
    status != "blocked_ask_user" and returns the same ordinary "nothing
    pending" outcome any other caller gets).

    Returns {"ok": True, "state": state} (the just-persisted state, e.g.
    for the CLI's own _drive_and_report() to keep driving, or the route to
    read run_id back off) on success, or {"ok": False, "error": "<reason>"}
    for the ordinary "wrong state" case -- never raises for that case, same
    "return a shaped result, don't make the caller catch" convention
    validate_composition()/launch_team() already use. The exact "no such
    run_id"/"is not blocked on ask_user" error text below matches
    _cli_team_resolve()'s own pre-extraction messages byte-for-byte, since
    the CLI's own printed error line is `f"error: {result['error']}"`
    (see _cli_team_resolve()) and must stay observably unchanged.

    The move step below is wrapped in its own try/except OSError -- found
    live, via TeamResolveEndpointTests' own two-genuinely-simultaneous-
    callers test (backlog item 6f part 1): the pre-extraction body's status
    check above narrows the race window but does not close it -- two
    callers can both pass it before either writes, and the SECOND to reach
    os.replace(inbox_path, ...) then raises FileNotFoundError (its own
    target was already renamed away by the first), an unhandled exception
    that reached all the way out through do_POST and reset the client's
    connection instead of returning a clean 400. This is still the exact
    same not-lock-guarded, single-writer-assumption race docs/spec.md
    "Edge cases" already accepts and declines to harden further -- "the
    first to persist wins" -- this change only ensures the LOSER of that
    race gets the shaped {"ok": False, ...} result the design always
    intended for it, instead of an unhandled exception; it adds no new
    locking and does not change who wins.

    A second, narrower instance of the same race survived that first fix:
    an earlier version of this function gated the move on a separate
    `if os.path.exists(inbox_path):` check-then-act, and a loser whose
    exists() call happened to land AFTER the winner had already renamed the
    file away would simply see False -- no exception raised at all -- fall
    through to flip its own stale in-memory state to "running", persist
    it (clobbering the winner's just-persisted ask_user_resolved history
    entry), and report {"ok": True} for an answer nobody recorded. Fixed by
    deleting the separate exists() check entirely and calling
    os.replace(inbox_path, ...) unconditionally: os.replace() is now the
    SOLE atomic arbiter of "did I win" (via FileNotFoundError on the loser
    side), so there is exactly one check-then-act decision point instead of
    two independent ones layered on top of each other. state["status"] is
    only flipped and persisted after os.replace() has already succeeded, so
    a loser never touches state at all.

    A third instance of the same "act before the win/lose decision" mistake
    (docs/spec.md 6f part 1b): this function used to call _append_history()
    -- which writes transcript.jsonl synchronously, unconditionally, with
    no rollback path -- BEFORE the os.replace() call above ever ran. A
    losing caller's own state["history"] mutation was correctly discarded
    (it's in-memory only until _persist(), which only ever runs on the
    winning path), but its _append_history() call had already appended a
    spurious ask_user_resolved entry to transcript.jsonl on disk, for an
    answer nobody actually accepted -- exactly the kind of misleading entry
    the team/events feed (6f part 1) would otherwise render. Fixed by moving
    the round_n/_append_history() call to after os.replace() has already
    succeeded: a loser now returns from the except OSError: branch above
    without ever calling _append_history(), so it leaves zero trace in
    transcript.jsonl, same as it already left zero trace in state["history"].
    """
    try:
        state = _load_state(run_id)
    except FileNotFoundError:
        return {"ok": False, "error": f"no such run_id: {run_id}"}
    if state.get("status") != "blocked_ask_user":
        return {"ok": False,
                "error": f"run {run_id} is not blocked on ask_user (status={state.get('status')})"}
    inbox_path = _inbox_path(run_id)
    try:
        os.replace(inbox_path, _inbox_resolved_path(run_id))
    except OSError:
        try:
            status_now = _load_state(run_id).get("status", "unknown")
        except (OSError, ValueError):
            status_now = "unknown"
        return {"ok": False,
                "error": f"run {run_id} is not blocked on ask_user (status={status_now})"}
    round_n = len(state["history"]) + 1
    _append_history(state, round_n, tool="ask_user_resolved",
                    args_summary="ask_user_resolved(...)",
                    outcome_summary=f"answered: {answer[:80]}", full_result_text=answer, log_path=None,
                    transcript_entries=[("tool_result", answer, {"resolved": True})])
    state["status"] = "running"
    _persist(state)
    return {"ok": True, "state": state}


def resolve_board_write(run_id: str, action: str) -> dict:
    """
    Resume half of board_write's blocking shape (backlog item 7 part 1,
    docs/spec.md §4), living right next to resolve_ask_user() and reusing
    its exact race-safety shape byte-for-byte -- see that function's own
    docstring (immediately above) for the full history of why each of
    these three properties matters (found live, across three separate
    races, during 6f part 1/1b): (1) state is always reloaded fresh from
    disk, never a caller-supplied dict, so two concurrent resolves for the
    same run race safely; (2) `os.replace(inbox_path, inbox_resolved_path)`
    is the SOLE atomic arbiter of "who won" -- inbox.json's own content
    (verb/ref/value) is read BEFORE the os.replace() call (needed to know
    what to apply), but nothing derived from it is ACTED on -- no Taiga
    call, no history entry -- until AFTER os.replace() has already
    succeeded, so a losing concurrent caller can never trigger a real Taiga
    write and leaves zero trace in transcript.jsonl; (3) the history/
    transcript entry is appended only after that same successful replace.

    `action` is "approve" or "reject" -- unlike resolve_ask_user(), there is
    no free-text argument here (the proposed change is already fully
    specified by the pending inbox.json itself; the human's only input is
    the accept/reject decision).

    - status != "blocked_board_write" -> {"ok": False, "error": ...}, same
      shape/wording convention as resolve_ask_user()'s own two error
      messages.
    - action not in ("approve", "reject") -> {"ok": False, "error": ...}
      before anything is touched on disk.
    - action == "reject": no Taiga call at all. History entry:
      tool="board_write_resolved", outcome_summary="rejected by human".
    - action == "approve": calls taiga_board.get_userstory() for a FRESH
      `version` (never the proposal-time snapshot in inbox.json's own
      current_value), then the matching taiga_board.set_status()/
      amend_description()/append_comment() call -- set_status additionally
      resolves inbox.json's own human-readable `value` (e.g. "In progress")
      to the numeric status id that call requires, via
      taiga_board.list_userstory_statuses() (docs/spec.md "status_id for
      set_status": "the apply step uses the id"). On success: history entry
      outcome_summary="approved and applied", full_result_text includes the
      Taiga response. On ANY taiga_board.TaigaPushError (network failure,
      stale version/conflict, vanished ref, or no such status name on this
      board): history entry outcome_summary=f"approved but Taiga rejected
      the write: {detail}" -- the inbox is STILL resolved and the run STILL
      resumes (never left permanently stuck on a Taiga outage or a conflict
      a human already approved past); the lead sees the failure next round.
    - Either branch: state["status"] = "running", persist, return
      {"ok": True, "state": state} -- identical return shape to
      resolve_ask_user() so a caller (the CLI's own team-board-resolve, or
      a future web route) can reuse the exact same "resume driving the
      loop" dispatch both already share.
    """
    try:
        state = _load_state(run_id)
    except FileNotFoundError:
        return {"ok": False, "error": f"no such run_id: {run_id}"}
    if state.get("status") != "blocked_board_write":
        return {"ok": False,
                "error": f"run {run_id} is not blocked on board_write (status={state.get('status')})"}
    if action not in ("approve", "reject"):
        return {"ok": False, "error": f"invalid action: {action!r} (must be 'approve' or 'reject')"}

    inbox_path = _inbox_path(run_id)
    try:
        with open(inbox_path) as f:
            inbox = json.load(f)
    except (OSError, ValueError):
        inbox = {}

    try:
        os.replace(inbox_path, _inbox_resolved_path(run_id))
    except OSError:
        try:
            status_now = _load_state(run_id).get("status", "unknown")
        except (OSError, ValueError):
            status_now = "unknown"
        return {"ok": False,
                "error": f"run {run_id} is not blocked on board_write (status={status_now})"}

    verb, ref, value = inbox.get("verb"), inbox.get("ref"), inbox.get("value")
    round_n = len(state["history"]) + 1
    if action == "reject":
        outcome_summary = "rejected by human"
        full_result_text = "rejected by human"
        transcript_entries = [("tool_result", full_result_text, {"resolved": True, "approved": False})]
    else:
        try:
            base_url, token, project_id = taiga_board.resolve_session()
            current = taiga_board.get_userstory(base_url, token, project_id, ref)
            us_id, version = current["id"], current["version"]
            if verb == "set_status":
                statuses = taiga_board.list_userstory_statuses(base_url, token, project_id)
                target = next((s for s in statuses if str(s.get("name", "")).strip().lower()
                              == str(value).strip().lower()), None)
                if target is None:
                    raise taiga_board.TaigaPushError(f"no such status on this board: {value!r}")
                result = taiga_board.set_status(base_url, token, us_id, version, target["id"])
            elif verb == "amend_description":
                result = taiga_board.amend_description(base_url, token, us_id, version, value)
            else:  # append_comment
                result = taiga_board.append_comment(base_url, token, us_id, version, value)
            outcome_summary = "approved and applied"
            full_result_text = json.dumps(result)
        except taiga_board.TaigaPushError as e:
            outcome_summary = f"approved but Taiga rejected the write: {e}"
            full_result_text = outcome_summary
        transcript_entries = [("tool_result", full_result_text, {"resolved": True, "approved": True})]

    _append_history(state, round_n, tool="board_write_resolved",
                    args_summary=f"board_write_resolved(verb={verb}, ref={ref})",
                    outcome_summary=outcome_summary, full_result_text=full_result_text, log_path=None,
                    transcript_entries=transcript_entries)
    state["status"] = "running"
    _persist(state)
    return {"ok": True, "state": state}


# ─── CLI ────────────────────────────────────────────────────────────────
def _tail_log_once(log_path: str, offset: int) -> int:
    try:
        with open(log_path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return offset
    for line in data.splitlines():
        if line.strip():
            print(line.decode("utf-8", "replace"), file=sys.stderr)
    return offset + len(data)


def _cli_run(args: argparse.Namespace) -> int:
    log_path = args.log_path
    if log_path is None:
        os.makedirs(os.path.join(TEAM_STATE_DIR, "_adhoc"), exist_ok=True)
        log_path = os.path.join(TEAM_STATE_DIR, "_adhoc",
                                f"{args.engine}-{int(time.time())}-{secrets.token_hex(4)}.jsonl")

    stop = threading.Event()
    state = {"offset": 0}

    def _tail_loop():
        while not stop.is_set():
            state["offset"] = _tail_log_once(log_path, state["offset"])
            time.sleep(0.2)

    t = threading.Thread(target=_tail_loop, daemon=True)
    t.start()
    try:
        result = agent_run(args.engine, args.workdir, args.prompt,
                           session_id=args.session_id, timeout=args.timeout, log_path=log_path)
    finally:
        stop.set()
        t.join(timeout=2)
        state["offset"] = _tail_log_once(log_path, state["offset"])  # flush any trailing events

    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def _cli_list_engines(args: argparse.Namespace) -> int:
    engines = load_engines()
    rows = [{"name": e.name, "label": e.label, "headless_enabled": e.headless_enabled}
            for e in sorted(engines.values(), key=lambda e: e.name)]
    print(json.dumps(rows, indent=2))
    return 0


def _cli_grounding(args: argparse.Namespace) -> int:
    print(json.dumps(load_grounding(args.workdir), indent=2))
    return 0


def _cli_fact_check(args: argparse.Namespace) -> int:
    grounding = load_grounding(args.workdir)
    print(json.dumps(fact_check(args.claim, grounding), indent=2))
    return 0


def _cli_roster(args: argparse.Namespace) -> int:
    print(json.dumps(roster(), indent=2))
    return 0


def _team_exit_code(status: str) -> int:
    # "blocked_ask_user"/"blocked_board_write" (the latter added by backlog
    # item 7 part 1) are both normal, expected stopping points (a human
    # needs to answer/approve), not a failure -- same reasoning "cancelled"
    # isn't treated as ok=False's own failure category in agent_run()'s
    # result shape.
    return 0 if status in ("finished", "blocked_ask_user", "blocked_board_write") else 1


def _drive_and_report(state: dict) -> int:
    """team-start/team-resolve/team-resume all block in the foreground
    until finished/blocked_ask_user/escalated_max_rounds/error, tailing
    round-by-round progress to stderr the same way _cli_run() already tails
    an agent_run() log (reuses _tail_log_once() directly against this run's
    own transcript.jsonl)."""
    log_path = _transcript_path(state["run_id"])
    stop = threading.Event()
    tail_state = {"offset": 0}

    def _tail_loop():
        while not stop.is_set():
            tail_state["offset"] = _tail_log_once(log_path, tail_state["offset"])
            time.sleep(0.2)

    t = threading.Thread(target=_tail_loop, daemon=True)
    t.start()
    try:
        team_run(state)
    finally:
        stop.set()
        t.join(timeout=2)
        tail_state["offset"] = _tail_log_once(log_path, tail_state["offset"])
    print(json.dumps(state, indent=2))
    return _team_exit_code(state["status"])


def _cli_team_start(args: argparse.Namespace) -> int:
    if args.lead_ollama:
        if not (TEAM_LLM_BASE_URL and TEAM_LLM_MODEL):
            print("error: --lead-ollama requires TEAM_LLM_BASE_URL and TEAM_LLM_MODEL "
                 "to be set", file=sys.stderr)
            return 1
        lead = {"kind": "ollama", "name": TEAM_LLM_MODEL, "tier": 1}
    else:
        eng = load_engines().get(args.lead)
        if eng is None or not eng.headless_enabled:
            print(f"error: lead engine '{args.lead}' is unknown or not headless-enabled",
                 file=sys.stderr)
            return 1
        tier = _lead_tier_for_engine(eng)
        if tier == 2:
            # Reported here, at team-start time, not only once the first
            # real tier-2 lead call fails mid-run (docs/spec.md
            # "Correction: {schema} is inline for Claude, a file for Codex").
            schema_err = _schema_flag_config_error(eng)
            if schema_err:
                print(f"error: lead engine '{args.lead}' is misconfigured for tier-2 "
                     f"use: {schema_err}", file=sys.stderr)
                return 1
        lead = {"kind": "engine", "name": eng.name, "tier": tier}
    if not os.path.isdir(args.workdir):
        print(f"error: workdir does not exist or is not a directory: {args.workdir}", file=sys.stderr)
        return 1
    members = [m.strip() for m in (args.members or "").split(",") if m.strip()]
    run_id = _run_id()
    state = _new_state(run_id, args.workdir, lead, members, args.task)
    _persist(state)
    print(f"run_id: {run_id}", file=sys.stderr)
    return _drive_and_report(state)


def _cli_team_status(args: argparse.Namespace) -> int:
    try:
        state = _load_state(args.run_id)
    except FileNotFoundError:
        print(f"error: no such run_id: {args.run_id}", file=sys.stderr)
        return 1
    print(json.dumps(state, indent=2))
    return 0


def _cli_team_resolve(args: argparse.Namespace) -> int:
    """
    Thin CLI wrapper over the shared resolve_ask_user() (backlog item 6f
    part 1, docs/spec.md §3) -- zero change to this subcommand's own
    observable behavior (exit codes, blocking, stderr/stdout) from before
    the extraction: the same two error strings, still foreground-blocking
    via _drive_and_report() on success.
    """
    result = resolve_ask_user(args.run_id, args.answer)
    if not result["ok"]:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    return _drive_and_report(result["state"])


def _cli_team_board_resolve(args: argparse.Namespace) -> int:
    """
    Thin CLI wrapper over resolve_board_write() (backlog item 7 part 1,
    docs/spec.md §5), mirroring _cli_team_resolve()'s own shape exactly: on
    success, drives the resumed run to its next stopping point via
    _drive_and_report(); on failure, prints the shaped error and exits 1.
    This is part 1's own only human-facing surface -- zero app.py/web
    involvement, matching 6f part 1's identical "CLI-testable, no web UI
    yet" precedent for the ask_user escalation.
    """
    result = resolve_board_write(args.run_id, args.action)
    if not result["ok"]:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    return _drive_and_report(result["state"])


def _cli_team_resume(args: argparse.Namespace) -> int:
    try:
        state = _load_state(args.run_id)
    except FileNotFoundError:
        print(f"error: no such run_id: {args.run_id}", file=sys.stderr)
        return 1
    if state["status"] != "running":
        print(f"run {args.run_id} is not in a resumable state (status={state['status']})",
             file=sys.stderr)
        print(json.dumps(state, indent=2))
        return _team_exit_code(state["status"])
    return _drive_and_report(state)


def _cli_team_launch(args: argparse.Namespace) -> int:
    """
    Same argument shape as team-start, deliberately (docs/spec.md §7):
    copy-pasted --lead/--lead-ollama/--members validation, reused not
    reinvented. Creates the worktrees/tmux dashboard session and persists a
    fresh run (status="running", 0 rounds) but does NOT drive it -- run
    `team-resume <run_id>` (unchanged from 6c) to actually run the lead
    loop. Prints {run_id, session, worktrees} as JSON on success.
    """
    if args.lead_ollama:
        if not (TEAM_LLM_BASE_URL and TEAM_LLM_MODEL):
            print("error: --lead-ollama requires TEAM_LLM_BASE_URL and TEAM_LLM_MODEL "
                 "to be set", file=sys.stderr)
            return 1
        lead = {"kind": "ollama", "name": TEAM_LLM_MODEL, "tier": 1}
    else:
        eng = load_engines().get(args.lead)
        if eng is None or not eng.headless_enabled:
            print(f"error: lead engine '{args.lead}' is unknown or not headless-enabled",
                 file=sys.stderr)
            return 1
        tier = _lead_tier_for_engine(eng)
        if tier == 2:
            schema_err = _schema_flag_config_error(eng)
            if schema_err:
                print(f"error: lead engine '{args.lead}' is misconfigured for tier-2 "
                     f"use: {schema_err}", file=sys.stderr)
                return 1
        lead = {"kind": "engine", "name": eng.name, "tier": tier}
    members = [m.strip() for m in (args.members or "").split(",") if m.strip()]
    result = launch_team(args.workdir, args.task, lead, members)
    if not result["ok"]:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _cli_team_stop(args: argparse.Namespace) -> int:
    try:
        result = stop_team(args.run_id)
    except FileNotFoundError:
        print(f"error: no such run_id: {args.run_id}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _cli_team_reap(args: argparse.Namespace) -> int:
    print(json.dumps(sweep_dead_teams(), indent=2))
    return 0


def _cli_team_branches(args: argparse.Namespace) -> int:
    """Backlog item 13, docs/spec.md -- prints list_team_branches()'s result
    as JSON. No run_id argument (scoped to a project directory, not a run,
    same as `grounding`'s own CLI shape). Exits 0 even when the list is
    empty -- list_team_branches() itself never raises, so there is no error
    branch to handle here, unlike _cli_team_status()'s FileNotFoundError
    catch."""
    print(json.dumps(list_team_branches(args.workdir), indent=2))
    return 0


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="teams.py",
        description="Run a single headless engine turn, list engines.d headless "
                     "eligibility, inspect a project's grounding, or run a team's "
                     "lead loop -- no server, no UI (backlog items 6a/6b/6c).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run one bounded, non-interactive engine turn.")
    p_run.add_argument("engine", help="Engine name (a .engine filename stem from ENGINES_DIR).")
    p_run.add_argument("workdir", help="Working directory to run the engine in.")
    p_run.add_argument("--prompt", required=True, help="The prompt/task for this turn.")
    p_run.add_argument("--session-id", default=None, help="Resume a prior turn by session id.")
    p_run.add_argument("--timeout", type=float, default=TEAM_HEADLESS_TIMEOUT_SECONDS,
                       help=f"Seconds before a clean SIGTERM stop (default {TEAM_HEADLESS_TIMEOUT_SECONDS}).")
    p_run.add_argument("--log-path", default=None,
                       help="Where to append the translated .jsonl event log "
                            "(default: TEAM_STATE_DIR/_adhoc/<engine>-<ts>-<rand>.jsonl).")

    sub.add_parser("list-engines", help="List engines.d entries and their headless eligibility.")

    p_grounding = sub.add_parser("grounding", help="Print load_grounding() as JSON for a project directory.")
    p_grounding.add_argument("workdir", help="Project directory to discover grounding files under.")

    p_fact_check = sub.add_parser("fact-check", help="Print fact_check() as JSON for a claim.")
    p_fact_check.add_argument("workdir", help="Project directory to discover grounding files under.")
    p_fact_check.add_argument("claim", help="The claim text to fact-check against the project's grounding.")

    sub.add_parser("roster", help="List every teammate-eligible engine plus the configured "
                                  "Ollama model, tagged with lead-adapter tier (backlog item 6c).")

    p_team_start = sub.add_parser("team-start", help="Start a new team run and drive it to "
                                  "completion/escalation (backlog item 6c).")
    p_team_start.add_argument("workdir", help="Project directory the team works against.")
    p_team_start.add_argument("--task", required=True,
                              help="The run's own task text, given once, never re-truncated.")
    lead_group = p_team_start.add_mutually_exclusive_group(required=True)
    lead_group.add_argument("--lead", default=None,
                            help="engines.d engine name to use as lead (tier 2/3).")
    lead_group.add_argument("--lead-ollama", action="store_true",
                            help="Use the configured TEAM_LLM_* Ollama model as lead (tier 1).")
    p_team_start.add_argument("--members", default="",
                              help="Comma-separated engines.d engine names delegate() may target.")

    p_team_status = sub.add_parser("team-status", help="Print a run's persisted state as JSON.")
    p_team_status.add_argument("run_id")

    p_team_resolve = sub.add_parser("team-resolve", help="Answer a blocked ask_user "
                                    "escalation and resume the run.")
    p_team_resolve.add_argument("run_id")
    p_team_resolve.add_argument("--answer", required=True,
                                help="Label or free text answering the pending question.")

    p_team_board_resolve = sub.add_parser("team-board-resolve", help="Approve or reject a "
                                          "pending board_write proposal and resume the run "
                                          "(backlog item 7 part 1).")
    p_team_board_resolve.add_argument("--run-id", required=True)
    p_team_board_resolve.add_argument("--action", required=True, choices=["approve", "reject"])

    p_team_resume = sub.add_parser("team-resume", help="Resume a running/crashed run "
                                   "from persisted state, not from memory.")
    p_team_resume.add_argument("run_id")

    p_team_launch = sub.add_parser("team-launch", help="Create per-teammate git worktrees "
                                   "and a persistent team-<project> tmux dashboard session, "
                                   "then persist a fresh run WITHOUT driving it -- follow with "
                                   "team-resume (backlog item 6d part 1).")
    p_team_launch.add_argument("workdir", help="Project directory the team works against.")
    p_team_launch.add_argument("--task", required=True,
                               help="The run's own task text, given once, never re-truncated.")
    lead_group2 = p_team_launch.add_mutually_exclusive_group(required=True)
    lead_group2.add_argument("--lead", default=None,
                             help="engines.d engine name to use as lead (tier 2/3).")
    lead_group2.add_argument("--lead-ollama", action="store_true",
                             help="Use the configured TEAM_LLM_* Ollama model as lead (tier 1).")
    p_team_launch.add_argument("--members", default="",
                               help="Comma-separated engines.d engine names delegate() may "
                                    "target; each gets its own git worktree.")

    p_team_stop = sub.add_parser("team-stop", help="Kill a team's tmux session and attempt "
                                 "worktree removal, unconditionally (backlog item 6d part 1).")
    p_team_stop.add_argument("run_id")

    sub.add_parser("team-reap", help="Opportunistically reconcile crashed team sessions and "
                                     "sweep stale worktrees/sessions past their TTL (backlog "
                                     "item 6d part 1).")

    p_team_branches = sub.add_parser("team-branches", help="List surviving team-* git branches "
                                     "for a project directory as JSON (backlog item 13).")
    p_team_branches.add_argument("workdir", help="Project directory to list team-* branches in.")

    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "run":
            return _cli_run(args)
        if args.command == "list-engines":
            return _cli_list_engines(args)
        if args.command == "grounding":
            return _cli_grounding(args)
        if args.command == "fact-check":
            return _cli_fact_check(args)
        if args.command == "roster":
            return _cli_roster(args)
        if args.command == "team-start":
            return _cli_team_start(args)
        if args.command == "team-status":
            return _cli_team_status(args)
        if args.command == "team-resolve":
            return _cli_team_resolve(args)
        if args.command == "team-board-resolve":
            return _cli_team_board_resolve(args)
        if args.command == "team-resume":
            return _cli_team_resume(args)
        if args.command == "team-launch":
            return _cli_team_launch(args)
        if args.command == "team-stop":
            return _cli_team_stop(args)
        if args.command == "team-branches":
            return _cli_team_branches(args)
        return _cli_team_reap(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
