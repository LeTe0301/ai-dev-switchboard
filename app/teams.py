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


def _validate_prompt_size(headless_prompt: str, prompt: str) -> None:
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
    """
    if headless_prompt == "arg":
        quoted_len = len(shlex.quote(prompt).encode("utf-8"))
        cap = min(TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES, _MAX_ARG_STRLEN - _ARG_SCRIPT_OVERHEAD_BYTES)
        if quoted_len > cap:
            raise ValueError(
                f"prompt's shell-escaped length ({quoted_len} bytes) exceeds the "
                f"{cap}-byte cap for HEADLESS_PROMPT=arg -- quote-heavy prompts expand "
                "significantly once shell-escaped (up to 5x for a prompt of mostly "
                "single quotes); use an engine with HEADLESS_PROMPT=stdin or "
                "HEADLESS_PROMPT=file for long or quote-heavy prompts, which have no "
                "such limit")
    else:
        n = len(prompt.encode("utf-8"))
        if n > TEAM_HEADLESS_PROMPT_MAX_BYTES:
            raise ValueError(f"prompt exceeds {TEAM_HEADLESS_PROMPT_MAX_BYTES}-byte cap")


def _build_headless_argv(engine, prompt: str, session_id, prompt_path: str = None) -> list:
    """
    Renders engine.headless_cmd into a list of argv tokens. {resume} (and,
    inside HEADLESS_RESUME, {session_id}) and {prompt_file} are substituted
    with plain str.replace() -- never str.format() -- so a future
    HEADLESS_SCHEMA_FLAG carrying a literal JSON Schema (full of {/}) can't
    break this (6c; docs/spec.md §1). The prompt itself is appended as its
    own list element, never string-interpolated, when HEADLESS_PROMPT=arg
    (Claude Code: -p is a boolean "print mode" flag; the query is a
    positional argument).
    """
    cmd = engine.headless_cmd.replace("{resume}", _resume_fragment(engine, session_id))
    if engine.headless_prompt == "file":
        cmd = cmd.replace("{prompt_file}", prompt_path)
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
             log_path: str = None) -> dict:
    """
    Runs exactly one bounded, non-interactive turn of `engine` against
    `workdir`, resumed by `session_id` if given. Never raises for anything
    that happens *after* a tmux session is created (a failed/killed/
    unauthenticated engine surfaces as ok=False with a real exit_code or a
    cancellation classification) -- ValueError is reserved for validation
    failures caught before anything is spawned (docs/spec.md "Edge cases").
    """
    _sweep_stale_runs()

    engines = load_engines()
    eng = engines.get(engine)
    if eng is None or not eng.headless_enabled:
        raise ValueError(f"engine '{engine}' is unknown or not headless-enabled")
    if not os.path.isdir(workdir):
        raise ValueError(f"workdir does not exist or is not a directory: {workdir}")
    _resume_fragment(eng, session_id)  # raises ValueError as needed, before anything is spawned
    _validate_prompt_size(eng.headless_prompt, prompt)

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

        argv = _build_headless_argv(eng, prompt, session_id, prompt_path)
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
    Opens `path` exactly once and returns a validated, open, read-only file
    descriptor, or None if it's unusable for any reason -- the single
    operation both discovery and content-reading are now built on (post-
    review fix, docs/test-review.md Defects 1 and 2). Previously,
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
    """
    if not _under_workdir(path, workdir_real):
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return None
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
        real = os.path.realpath(f"/proc/self/fd/{fd}")
        if real != workdir_real and not real.startswith(workdir_real + os.sep):
            os.close(fd)
            return None
        return fd
    except OSError:
        # Missing file, permission denied, a directory (see the fstat
        # check above for the common case, but e.g. a race making it
        # disappear entirely is still just an OSError here too),
        # ELOOP (symlink final component, or a genuine symlink loop --
        # O_NOFOLLOW turns a loop into an immediate ELOOP rather than the
        # kernel chasing it), FIFO/device/socket already handled by the
        # fstat check above without ever reaching read -- all reduce to
        # the same "unusable" outcome here.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return None


def _read_grounding_candidate(path: str, workdir_real: str,
                              read_cap: int = _GROUNDING_READ_CAP_BYTES) -> str:
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
    itself uses for genuinely malformed-but-not-binary text). Returns ""
    for any unusable candidate -- missing, wrong type, out-of-bounds,
    symlink, binary, or empty -- the same unified "empty means skip" rule
    as before, just now resting on one filesystem operation per file
    instead of two or three.
    """
    fd = _open_grounding_candidate(path, workdir_real)
    if fd is None:
        return ""
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
        return ""
    return raw.decode("utf-8", errors="ignore")


def _discover_and_read(workdir: str, read_cap: int = _GROUNDING_READ_CAP_BYTES) -> list:
    """
    The one place that both decides which of the four candidate sources are
    present *and* fetches their content -- every real underlying file is
    opened and read exactly once here (see _open_grounding_candidate()'s
    docstring for why that matters). `discover_grounding_files()` and
    `load_grounding()` are both thin wrappers around this, so
    `load_grounding()` never re-reads a path `discover_grounding_files()`
    (or this function, on a separate call) already validated.
    """
    workdir_real = os.path.realpath(workdir)
    entries = []

    for relpath in ("docs/ARCHITECTURE.md", "docs/BACKLOG.md"):
        path = os.path.join(workdir, relpath)
        content = _read_grounding_candidate(path, workdir_real, read_cap)
        if content:
            entries.append({"label": relpath, "path": path, "content": content})

    for name in ("CLAUDE.md", "AGENTS.md"):
        path = os.path.join(workdir, name)
        text = _read_grounding_candidate(path, workdir_real, read_cap)
        if not text:
            continue
        stripped = text.strip()
        if stripped.startswith("@") and len(stripped.splitlines()) == 1:
            target_path = os.path.join(workdir, stripped[1:])
            target_content = _read_grounding_candidate(target_path, workdir_real, read_cap)
            if target_content:
                entries.append({"label": name, "path": target_path, "content": target_content})
                break
            continue  # target missing/unusable/out-of-bounds
        entries.append({"label": name, "path": path, "content": text})
        break

    for name in ("README.md", "Readme.md", "readme.md", "README"):
        path = os.path.join(workdir, name)
        content = _read_grounding_candidate(path, workdir_real, read_cap)
        if content:
            entries.append({"label": "README.md", "path": path, "content": content})
            break

    return entries


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
    goes through _discover_and_read() directly, once, itself.
    """
    return [{"label": e["label"], "path": e["path"]} for e in _discover_and_read(workdir)]


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
    re-reads from disk for that call.
    """
    files = []
    for entry in _discover_and_read(workdir, read_cap):
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


def fact_check(claim: str, grounding: dict, *,
              max_matches: int = _GROUNDING_FACT_CHECK_MAX_MATCHES) -> dict:
    """
    Deterministic, precision-biased textual match against the FULL per-file
    content grounding["files"][*]["content"] (not grounding["digest"]) --
    docs/spec.md 6b §5. A line matches iff EVERY significant term of `claim`
    is a case-insensitive substring of it -- a strict conjunctive match, no
    scoring, no nearest-weak-match fallback: an empty `terms` list or zero
    matching lines both simply return found=False, never an exception.
    """
    terms = _significant_terms(claim)
    matches = []
    if terms:
        for f in grounding.get("files", []):
            for lineno, line in enumerate(f.get("content", "").splitlines(), start=1):
                lower = line.lower()
                if all(term in lower for term in terms):
                    matches.append({
                        "label": f["label"],
                        "path": f["path"],
                        "relpath": f["relpath"],
                        "line": lineno,
                        "file_line": f"{f['label']}:{lineno}",
                        "text": line.strip(),
                    })
                    if len(matches) >= max_matches:
                        break
            if len(matches) >= max_matches:
                break
    return {"claim": claim, "found": bool(matches), "matches": matches}


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


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="teams.py",
        description="Run a single headless engine turn, list engines.d headless "
                     "eligibility, or inspect a project's grounding -- no server, "
                     "no UI (backlog items 6a/6b).",
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
        return _cli_fact_check(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
