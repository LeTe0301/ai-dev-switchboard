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
                          agent_name: str) -> dict:
    """
    Startup confirmation, tailing loop, completion-ordering, and
    cancellation escalation (docs/spec.md §4). Assumes the tmux session has
    already been created by the caller; does not create or tear it down
    itself (agent_run() owns that, so cleanup happens in one place
    regardless of which branch below returns).
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
            if escalation is None and (now - start) >= timeout:
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
             schema: dict = None) -> dict:
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
            timeout=timeout, agent_name=engine)
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


# ─── roster + lead loop (backlog item 6c; docs/spec.md, docs/story.md §4) ──
# Every headless-eligible engines.d entry plus one configured Ollama model,
# each tagged with a lead-adapter tier (1 native tool-calling / 2 schema-
# constrained / 3 prose-parse), and the four-tool lead loop
# (delegate/fact_check/ask_user/finish) that drives agent_run() (6a) and
# fact_check()/load_grounding() (6b/6b.1) into an actual team. CLI-only --
# no web route, no UI (that's 6d/6e/6f).

_LEAD_TOOL_NAMES = ("delegate", "fact_check", "ask_user", "finish")


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


def _tool_prose(team_members: list) -> str:
    """Prose tool descriptions for tier 2/3 (tier 1 gets these for free from
    the native `tools` array and doesn't need them restated, docs/spec.md
    §5)."""
    agents = ", ".join(team_members) if team_members else "(no teammates configured)"
    return (
        "You have exactly four tools:\n"
        f"- delegate(agent, task): give one self-contained task to a named "
        f"teammate agent. agent must be exactly one of: {agents}.\n"
        "- fact_check(claim): verify a claim against the project's own "
        "documentation.\n"
        "- ask_user(question, header, options, multi_select): ask the human "
        "a question when something is genuinely unresolved. options is a "
        "list of 2-4 objects each shaped {label, description}; header is a "
        "short label (12 characters or fewer); multi_select is a boolean.\n"
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
      unknown_tool        -- raw["tool"] not one of the four names
      missing_args         -- a tool-required key absent from raw["args"]
      wrong_type            -- an arg present but the wrong JSON type
      not_a_dict            -- raw itself, or raw["args"], isn't an object
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
                "detail": f"unknown tool {tool!r}; valid tools are delegate, fact_check, ask_user, finish"}
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
               max_rounds: int = None) -> dict:
    now = _now_iso()
    return {
        "run_id": run_id, "workdir": workdir, "lead": lead, "members": list(members),
        "task": task, "status": "running", "round": 0, "action_count": 0,
        "max_rounds": max_rounds or TEAM_MAX_ROUNDS, "teammate_sessions": {},
        "history": [], "malformed_retries": 0, "in_progress_delegate": None,
        "summary": None, "error": None, "created_at": now, "updated_at": now,
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


def _write_inbox(state: dict, args: dict) -> None:
    """Exact §4.5 shape: question, header (<=12 chars, silently truncated --
    cosmetic, never worth spending retry budget on), 2-4 options each
    {label, description}, multi_select."""
    inbox = {
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
        _write_inbox(state, args)
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
def _call_lead(state: dict, system: str, round_context: str):
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
        result = agent_run(state["lead"]["name"], state["workdir"], prompt, schema=_TIER2_LEAD_SCHEMA)
    else:
        result = agent_run(state["lead"]["name"], state["workdir"], prompt)
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
def team_step(state: dict) -> dict:
    """
    One round. Takes/returns the run's own state dict (§11's persisted
    shape); the only I/O is one lead-adapter call, zero or one
    agent_run()/fact_check() call, and the state write at the end. Never
    raises for anything shaped wrong -- see _validate_lead_action().
    """
    round_n = len(state["history"]) + 1
    tier = state["lead"]["tier"]
    system = _system_framing(state["workdir"], state["members"], tier)
    last_entry = state["history"][-1] if state["history"] else None
    round_context = _round_context(state["task"], state["history"], last_entry,
                                   round_n, state["max_rounds"])

    raw, transport_error, raw_text = _call_lead(state, system, round_context)
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
        result = agent_run(agent, state["workdir"], task,
                          session_id=state["teammate_sessions"].get(agent))
        state["in_progress_delegate"] = None
        state["teammate_sessions"][agent] = result.get("session_id")
        state["action_count"] += 1
        text = result.get("text") or ""
        # docs/spec.md "Correction: repeated delegation of an
        # already-completed task" -- the round-history summary must state
        # prior delegations' agent/task/success EXPLICITLY and SALIENTLY,
        # not leave it to be inferred from prose, so a live finding (the
        # lead delegating the identical task twice) is less likely to
        # recur. SUCCEEDED/FAILED in capitals on purpose -- distinct at a
        # glance from fact_check's own lowercase found=True/False summaries
        # a few lines above/below it in the same round history.
        task_preview = task if len(task) <= 100 else task[:97] + "..."
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
        _write_inbox(state, args)
        state["status"] = "blocked_ask_user"
        _append_history(state, round_n, tool="ask_user", args_summary=f'ask_user("{args["question"][:60]}")',
                        outcome_summary="blocked, awaiting human",
                        full_result_text=json.dumps(args), log_path=None,
                        transcript_entries=[("tool_use", args["question"], {"header": args.get("header")})])
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


def team_run(state: dict) -> dict:
    """
    Drives team_step() in a loop until finish / ask_user / TEAM_MAX_ROUNDS.
    This is the function the CLI's team-start/team-resume/team-resolve
    subcommands call; nothing here assumes a foreground TTY, so a later
    sub-spec can run it off a background thread with zero change.
    """
    _recover_in_progress(state)
    while state["status"] == "running":
        if len(state["history"]) >= state["max_rounds"]:
            _force_ask_user(state, question=f"Team did not converge after {state['max_rounds']} rounds.",
                            header="MaxRnds", status="escalated_max_rounds")
            _persist(state)
            break
        team_step(state)
    return state


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
    # "blocked_ask_user" is a normal, expected stopping point (a human needs
    # to answer), not a failure -- same reasoning "cancelled" isn't treated
    # as ok=False's own failure category in agent_run()'s result shape.
    return 0 if status in ("finished", "blocked_ask_user") else 1


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
    try:
        state = _load_state(args.run_id)
    except FileNotFoundError:
        print(f"error: no such run_id: {args.run_id}", file=sys.stderr)
        return 1
    if state["status"] != "blocked_ask_user":
        print(f"error: run {args.run_id} is not blocked on ask_user (status={state['status']})",
             file=sys.stderr)
        return 1
    round_n = len(state["history"]) + 1
    answer = args.answer
    _append_history(state, round_n, tool="ask_user_resolved",
                    args_summary="ask_user_resolved(...)",
                    outcome_summary=f"answered: {answer[:80]}", full_result_text=answer, log_path=None,
                    transcript_entries=[("tool_result", answer, {"resolved": True})])
    inbox_path = _inbox_path(state["run_id"])
    if os.path.exists(inbox_path):
        os.replace(inbox_path, _inbox_resolved_path(state["run_id"]))
    state["status"] = "running"
    _persist(state)
    return _drive_and_report(state)


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

    p_team_resume = sub.add_parser("team-resume", help="Resume a running/crashed run "
                                   "from persisted state, not from memory.")
    p_team_resume.add_argument("run_id")

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
        return _cli_team_resume(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
