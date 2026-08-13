# Spec: Headless engine invocation (backlog item 6, sub-spec 6a)

## Summary
Give the switchboard a way to run any capable engine (`claude`, `codex`,
`aider`) **headlessly** — one bounded, non-interactive turn, structured
output, no `capture-pane` — via a new `agent_run()` library function in a
new `app/teams.py`, plus a small CLI so it's runnable and testable with zero
UI. This is the foundation every later multi-agent-team sub-spec (6b–6f, see
`docs/story.md`) builds on; nothing above the process layer (grounding, lead
loop, per-teammate git worktrees, any web UI) is built here.

**Revision note (this version):** §2–§4 were reworked after review. The
headless process is now spawned **inside a tmux session**, via the
*already-existing* `TMUX` sudoers rule (`app/app.py:191`), instead of adding
a new `bash -lc` sudoers line. Zero new privilege surface. Everything else —
the four `HEADLESS_*` keys, `{resume}`/`{prompt_file}` placeholder
mechanics, the extended `agent_run()` return shape, signal-generic
cancellation classification, and the three-tier test plan — is unchanged
from the prior version. See "Why tmux-hosted" below.

## Goals
- Extend `engines.d/*.engine` with optional `HEADLESS_CMD`, `HEADLESS_FORMAT`,
  `HEADLESS_PROMPT`, `HEADLESS_RESUME` keys, parsed by `_parse_engine_file()`
  in `app/app.py`.
- Ship working headless config for `claude.engine`, `codex.engine`, and
  `aider.engine`, each verified by actually running it.
- `app/teams.py`: `agent_run(engine, workdir, prompt, session_id=None,
  timeout=...)` — spawns the headless process **inside a tmux session, as
  `RUN_USER`, via the existing `TMUX` sudoers rule**, translates its native
  stream into the normalized envelope (`docs/story.md` §4.1), appends it to
  a `.jsonl` log, and returns a normalized result dict.
- A CLI entry point (`python3 app/teams.py run ...`) that exercises the same
  code path with no server, no UI, no team concept involved.
- Zero behavioral change to the existing single-session toggle for any engine
  (present or future) that doesn't define `HEADLESS_CMD`.
- Zero new sudoers surface.

## Non-goals
Explicitly deferred to later sub-specs in `docs/story.md`:
- The lead loop, roster assembly, or any LLM tool-calling (**6c**). `agent_run()`
  runs exactly one bounded turn for exactly one named engine; nothing here
  chooses an engine, plans, or retries at a semantic level.
- `HEADLESS_ROLE_FLAG`, `HEADLESS_SCHEMA_FLAG`, `HEADLESS_LEAD_FORMAT` — these
  three `.engine` keys from `docs/story.md` §4.2 are **not** parsed or used by
  this sub-spec. They only matter once something is choosing a *lead* and
  constraining its output (6c). Adding them now would be unused code. See
  "Open questions" for the (additive, non-breaking) follow-up this implies for
  `_parse_engine_file()` in 6c.
- Grounding / `fact_check` (**6b**).
- Per-teammate git worktrees, multi-window **team** tmux sessions (one
  window per agent, human-attachable, named per project), `install.sh
  --with-ollama` (**6d**). This sub-spec's tmux session is a single-purpose,
  invisible, self-cleaning implementation detail of `agent_run()` — it is
  **not** the "team session" concept 6d builds; see "Why tmux-hosted" for how
  the two relate.
- Any web UI, page, or button (**6e**, **6f**). The CLI entry point is the
  only human-facing surface this sub-spec ships.
- Multi-turn conversational state held in a long-lived process
  (`--input-format stream-json`) — deliberately out of scope per
  `docs/story.md` §4.2's own reasoning; every call is one process, resumed
  by session ID, never a persistent bidirectional pipe.
- `install.sh` / `docs/ARCHITECTURE.md` privilege-boundary changes — moot
  now that no new sudoers line is needed (see "Why tmux-hosted").
- `README.md` changes. Nothing here is an end-user-visible feature yet.

## Background / current state
- Engines are config, not code (`docs/ARCHITECTURE.md` "Why engines are
  config, not code"): `engines.d/*.engine` is `KEY=value` text, parsed by
  `_parse_engine_file()` (`app/app.py:294`) into an `Engine`
  (`app/app.py:283`, `__slots__`), collected by `load_engines()`
  (`app/app.py:315`), which **deliberately re-reads `ENGINES_DIR` on every
  call** — no caching, so files can be edited live. This sub-spec must not
  break that property.
- The existing single-session path (`instance_start()`, `app/app.py:1230`)
  runs an engine **interactively**: `TMUX + ["new-session", "-d", "-s",
  session, "-c", workdir, "bash", "-lc", cmd]` (`app/app.py:1248`), i.e. the
  engine's own CLI process becomes the tmux pane's command, running as
  `RUN_USER` via a narrowly-scoped sudoers rule (`app/app.py:191`,
  `TMUX = ["sudo", "-u", RUN_USER, "/usr/bin/tmux"]`). `run_startup_watch()`
  (`app/app.py:1201`) then polls `tmux capture-pane` to clear one-time
  trust prompts and to capture a hosted URL if `URL_REGEX` matches.
  **This sub-spec's headless path must never call `capture-pane`** — the
  whole point of headless mode is a real completion signal (an exit code
  written to a file, once the process is actually done) instead of
  screen-scraping.
- `RUN_USER` (not the switchboard's own `SVC_USER`) is where engine
  credentials live (`claude`'s own login, `aider`'s config, `codex`'s auth —
  `docs/ARCHITECTURE.md` "Processes and privilege boundaries") and where
  `RUN_USER`'s shell profile (nvm/pipx/etc. `PATH` extensions) makes the CLI
  binaries findable at all. `SVC_USER` cannot write into `PROJECTS_DIR/<name>`
  directly (it's `chown RUN_USER:RUN_USER`, `install.sh:177`).
- No third-party dependencies anywhere in this project; `app/teams.py` is
  stdlib-only, matching `app/app.py` and `scripts/taiga_push_spec.py` (the
  existing precedent for a standalone stdlib CLI script in this repo —
  `argparse`, `_parse_args()`/`_run()`/`main()` shape, `if __name__ ==
  "__main__":` at the bottom, one clear exception type main() catches).

## Why tmux-hosted (superseding the prior version's §2)

The prior version of this spec ran the headless process via a direct
`subprocess.Popen(["sudo", "-u", RUN_USER, "bash", "-lc", ...])`, needing one
new sudoers line. Rejected — not because the trust-boundary reasoning was
wrong (it wasn't: `SVC_USER` can already run arbitrary commands as
`RUN_USER` today, via `TMUX`'s existing wildcard), but because it's not the
*cheapest* correct option once `docs/story.md` §2.2's already-settled
architecture ("tmux hosts, NDJSON carries") is taken seriously:

1. **§2.2 already settled this.** A direct `bash -lc` subprocess quietly
   opts 6a out of half the settled architecture.
2. **The `.jsonl` log is already the transport (§4).** Tailing a file isn't
   extra machinery here — it *is* the machinery, and 6f's overwatch feed
   tails these same files.
3. **6d needs one tmux window per agent regardless.** Building 6a this way
   means 6d *generalizes* this sub-spec's plumbing (a named, attachable team
   window instead of an invisible throwaway session) rather than replacing
   it outright.
4. **Attach-to-watch comes free** — a stated 6d acceptance criterion — for
   the exact same reason.
5. **Zero new privilege.** No new sudoers line, no new `docs/ARCHITECTURE.md`
   paragraph justifying a second standing path into `RUN_USER`.

**This sub-spec's tmux session is not the "team session" 6d builds.** It's
one throwaway, unlabeled, single-purpose session per `agent_run()` call —
created, used purely as a `RUN_USER`-privileged host for one background
process plus its completion signal, and torn down within the same call.
Nothing about it is human-facing, named per project, or long-lived. 6d's job
is to make a **visible, multi-window, per-team** tmux session where a human
can attach to watch — a materially different lifecycle built on top of the
same primitive.

## Proposed approach

### 1. `Engine` and `_parse_engine_file()` — four new keys, additive only

`app/app.py`'s `Engine.__slots__` gains four fields, all optional, all
defaulting to values that make the engine **headless-ineligible** if any
required piece is missing or unrecognized:

```
HEADLESS_CMD=claude -p {resume} --output-format stream-json --verbose
HEADLESS_FORMAT=claude-stream-json      # claude-stream-json | codex-jsonl | plain — required if HEADLESS_CMD set
HEADLESS_PROMPT=arg                     # arg | stdin | file — required if HEADLESS_CMD set
HEADLESS_RESUME=--resume {session_id}   # optional — omit entirely if the engine has no resume concept (aider)
```

Parsing rule in `_parse_engine_file()`: if `HEADLESS_CMD` is present but
`HEADLESS_FORMAT` is missing/not one of the three known values, or
`HEADLESS_PROMPT` is missing/not one of `arg|stdin|file`, the engine is
parsed exactly as it is today (`LABEL`/`CMD`/`URL_REGEX`/`STARTUP_*` all
still work) but its headless fields are left unset/`None` and
`Engine.headless_enabled` is `False` — **never** an exception, never a
`load_engines()` failure, consistent with the function's existing
best-effort philosophy (`app/app.py:328`'s `except OSError: continue`). An
engine with no `HEADLESS_CMD` line at all behaves **byte-for-byte** as it
does today.

`Engine` gains a read-only `headless_enabled` property
(`bool(self.headless_cmd and self.headless_format and self.headless_prompt)`).

**One reserved engine-name prefix.** `_parse_engine_file()` treats a `.engine`
file whose derived name (filename stem) **starts with** `switchboard` as
invalid/ignored — the same "intentionally inert" treatment `.engine.example`
templates already get (`app/app.py:327`). This is what makes the tmux
session-naming scheme in §2 below *structurally* collision-proof rather than
merely improbable — see "Session naming" for why. Worth exactly one line in
`docs/ADDING_AN_ENGINE.md` so a future engine author isn't surprised by it.

Note it must be a **prefix** rule, not an exact-name rule. Reserving only the
exact name `switchboard-headless` leaves a real collision open: an engine
named `switchboard` combined with a project directory named
`headless-<run_id>` also renders `f"{e}-{name}"` as
`switchboard-headless-<run_id>`. Reserving the whole `switchboard` prefix
makes that unconstructible, because no loaded engine's name can begin the
string at all.

**Placeholder tokens are substituted with plain `str.replace()`, never
`str.format()`** — `HEADLESS_SCHEMA_FLAG` (6c) will carry a literal JSON
Schema, which is full of `{`/`}`; using `.format()` anywhere in this
substitution chain would break the moment that key is added.

### 2. Crossing into `RUN_USER` — the existing `TMUX` sudoers rule, nothing new

`app/teams.py` spawns every headless run via the same constant
`instance_start()` already uses:

```python
from app import TMUX, tmux_has, load_engines  # app/app.py:191, :1187, :315
```

No new module-level constant, no new sudoers line. The shape (concretely
resolving the coordinator's sketch, with one addition — capturing the real
child PID — explained below):

```python
RUNDIR = os.path.join(TEAM_STATE_DIR, "_headless", run_id)   # SVC_USER-owned
os.makedirs(RUNDIR, exist_ok=True)
os.chmod(RUNDIR, 0o711)   # traversable by anyone, listable by no one but the owner

argv = _headless_argv(engine, prompt, session_id)   # list, see §3 — shlex.join()'d once, at the end
script = (
    f"{shlex.join(argv)}"
    f"{' < ' + shlex.quote(promptf) if headless_prompt == 'stdin' else ''}"
    f" >{shlex.quote(out_path)} 2>{shlex.quote(err_path)}"
    f" & echo $! >{shlex.quote(pid_path)}; wait $!; echo $? >{shlex.quote(rc_path)}"
)
session = f"switchboard-headless-{run_id}"     # see "Session naming" below
subprocess.run(TMUX + ["new-session", "-d", "-s", session, "-c", workdir,
                       "bash", "-lc", script])
```

Every dynamic value (paths, the engine's own argv) is either passed as its
own `subprocess.run()` list element (no shell involved, hence no quoting
needed for `-c workdir`/`-s session`) or individually `shlex.quote()`'d
before being spliced into the one string that *is* interpreted as shell
syntax (`script`, handed to `bash -lc`) — the same argv-list-then-
`shlex.join()` discipline the prior version used, now applied to the small
set of shell operators (`<`, `>`, `&`, `;`) this shape genuinely needs.
This exactly mirrors `instance_start()`'s own `cmd = engine.cmd.format(
name=shlex.quote(name))` (`app/app.py:1240`).

**Why capture `$!` into `pid_path`, beyond the coordinator's sketch.** The
sketch's shape (`<cmd> > out.jsonl 2> out.err; echo $? > out.rc`) has no way
to target *just the engine process* for a clean `SIGTERM` later — the only
things `tmux` itself exposes are the whole session (`kill-session`, too
blunt: it tears down the pane before the trailing `echo $? >out.rc` can
ever run, permanently losing the exit code) or the pane's *leaf* process
(`#{pane_pid}`, which is `bash` itself here, not the engine, since `bash`
doesn't `exec`-replace itself — it can't, because it still has the trailing
`echo` to run after). Backgrounding the command (`&`) and capturing `$!`
immediately gives a real, targetable PID for the engine process specifically,
while leaving the wrapping shell alive to still write `out.rc` normally
after that process exits for any reason, including a `SIGTERM` we sent it.
This is what makes "clean stop" (§4) actually possible with this transport.

**`TEAM_STATE_DIR`** (new env var, default `/var/lib/ai-dev-switchboard/teams`)
holds both the durable `.jsonl` translated logs and each run's throwaway
`_headless/<run_id>/` directory. Created lazily
(`os.makedirs(TEAM_STATE_DIR, exist_ok=True)`), the same way
`_save_desc_cache()` creates `DESC_CACHE_FILE`'s parent directory
(`app/app.py:351`) — no `install.sh` change needed. `/var/lib/
ai-dev-switchboard` itself already has default (`mkdir -p`, root, typical
`022` umask → `0755`) traverse-for-others permissions from `install.sh:89`,
so `RUN_USER` can already reach anything SVC_USER creates underneath it
without any group/ACL setup — the `0711` on each run's own subdirectory
(§ above) is what actually matters, and is set directly by `agent_run()`
itself, an ordinary unprivileged `chmod` on a directory it just created and
owns. **The `RUN_USER`-run child process never needs elevated access to
write there** — it's writing into a directory `SVC_USER` (the directory's
owner) deliberately made traversable+writable by everyone; conversely
`SVC_USER` reads files `RUN_USER`'s shell created inside it by exact path
(needs no `r`/list permission on the directory at all, only `x`/traverse,
which `0711` grants) — and `SVC_USER`, as the directory's owner, can delete
everything inside it during cleanup regardless of who created each file,
since no sticky bit is set. This is the same "drop box" permission pattern
`/tmp` itself uses, just narrower (`0711` instead of `01777` — no listing,
no cross-run visibility, no deletion by anyone but the owning process).

### 3. Building the command: `{resume}` and `{prompt_file}` placeholders

Unchanged from the engine-file-author's point of view — only *how*
`agent_run()` delivers the prompt for `stdin`/`file` modes got simpler
(§2's shared run directory means it can just write a file directly, no
mid-shell `mktemp`/`cat`/`trap` dance is needed anymore).

`HEADLESS_CMD` is a template string, substituted (via `.replace()`, per
above) before `shlex.split()`:

- **`{resume}`** — replaced with the empty string on a first turn
  (`session_id=None`), or with `HEADLESS_RESUME`'s own template (with
  `{session_id}` substituted into *it* first) when resuming. Sits *inline*
  inside `HEADLESS_CMD` because Claude Code's resume is a flag
  (`claude -p --resume abc123 ...`) but Codex's is a **subcommand swap**
  (`codex exec resume abc123 ...`, not `codex exec ... --resume abc123`):
  ```
  # claude.engine
  HEADLESS_CMD=claude -p {resume} --output-format stream-json --verbose
  HEADLESS_RESUME=--resume {session_id}

  # codex.engine
  HEADLESS_CMD=codex exec {resume} --json --skip-git-repo-check
  HEADLESS_RESUME=resume {session_id}
  ```
  (Exact final flags confirmed during Tier 3 verification — the shapes above
  are the resolved *mechanism*, not a claim these are the final byte-for-byte
  flags.) If `session_id` is passed to `agent_run()` for an engine whose
  `HEADLESS_CMD` has no `{resume}` token (or no `HEADLESS_RESUME` key at all
  — `aider.engine`), `agent_run()` raises `ValueError` **before creating any
  tmux session**.

- **`{prompt_file}`** — only meaningful when `HEADLESS_PROMPT=file`
  (`aider`, via `--message-file`). Substituted with the literal path of a
  file `agent_run()` writes **directly** (`open(promptf, "wb").write(...)`)
  into the same `0711` run directory §2 already sets up, *before* the tmux
  session is created — no shell-side `mktemp`/`cat` prelude needed, since
  `SVC_USER` already owns that directory and can write into it freely; the
  only thing that has to happen inside `RUN_USER`'s shell is reading a path
  it was handed, exactly as `--message-file` already expects.

`HEADLESS_PROMPT` modes, concretely:
- **`arg`** — prompt appended as one extra Python list element *after*
  splitting the substituted `HEADLESS_CMD`, never string-interpolated
  (Claude Code: `-p` is a boolean "print mode" flag; the query itself is a
  positional argument).
- **`stdin`** — same file-write as `file` mode, but the file is fed to the
  engine as its actual stdin via a plain shell redirect (`< promptf`, added
  in §2's `script` construction) rather than a named-file flag —
  functionally equivalent to a live pipe for the engine's own purposes, and
  considerably simpler than one (no writer-thread/pipe-deadlock concern
  exists at all now, since it's an ordinary file the child opens and reads
  at its own pace).
- **`file`** — as above, path substituted into `{prompt_file}`.

Two byte caps, not one, because `arg` mode has a materially tighter ceiling
than `stdin`/`file` do: the *entire* `script` string (redirects, `wait`,
the engine's own argv with the prompt inlined) becomes a single argv
element to the outer `bash -lc` invocation, and Linux caps any *single*
argv element at `MAX_ARG_STRLEN` (~128 KiB) regardless of the much larger
overall `ARG_MAX`. `TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES` (default 65536,
comfortably under that ceiling) applies specifically to `arg` mode;
`TEAM_HEADLESS_PROMPT_MAX_BYTES` (default 1 MiB, well under Claude's own
documented 10 MB piped-stdin cap) applies to `stdin`/`file` modes, which
have no such constraint since the prompt never appears in any argv at all.
Both raise `ValueError` before anything is spawned. This is also a concrete,
practical reason (beyond the shell-escaping one already given) to prefer
`stdin`/`file` mode for any engine expected to receive long delegation
prompts.

### 4. `agent_run()` — signature, execution, return shape

```python
def agent_run(engine: str, workdir: str, prompt: str, *,
              session_id: str | None = None,
              timeout: float = TEAM_HEADLESS_TIMEOUT_SECONDS,
              log_path: str | None = None) -> dict:
```

- `engine` is a **name** (`.engine` filename stem), resolved via
  `load_engines()` on every call — preserving that function's documented
  no-caching contract (`app/app.py:317`'s docstring).
- Validates, in order, before creating any tmux session: engine known and
  `headless_enabled`; `workdir` exists and is a directory; `session_id`
  compatible with the engine's resume support; prompt within the
  mode-appropriate byte cap. Any failure here is a `ValueError` with a
  specific message.
- Opportunistically sweeps stale runs first (see "Cleanup" below) — cheap,
  mirrors `_reap_dead_state()`'s own "cleanup on a request that already
  happens often" precedent, without being wired into that function (a
  different module, a different concern; `app.py` never needs to know
  headless runs exist in this sub-spec).
- `log_path` defaults to `TEAM_STATE_DIR/_adhoc/<engine>-<ts>-<rand>.jsonl`
  when not given. 6d will pass its own `<agent>.jsonl` path once team
  sessions exist; `agent_run()` itself has no notion of a team.
- **Startup confirmation**: immediately after `tmux new-session -d`
  returns, poll (bounded, ~5s) for `pid_path` to appear and contain a valid
  integer. If it never does even though the session exists, that means
  `bash -lc` itself failed before backgrounding anything (a bug in our own
  generated `script`, a missing `cwd`, `bash` itself unavailable for
  `RUN_USER`) — a `agent_run()`-side failure, not an engine failure:
  `ok=False`, `error="headless session failed to start"`, session torn
  down, return immediately without entering the tailing loop below.
- **Tailing**: incremental read of `out_path` by byte offset (`seek()` to
  the last-read position each poll, never re-reading from the start), a
  partial trailing line held across polls until its newline arrives.
  Malformed-line and stream-volume-cap handling are **unchanged** from the
  prior version: a `json.loads()` failure (including an unterminated final
  line once the file stops growing) produces one `kind="error"` envelope
  and parsing continues; `TEAM_HEADLESS_MAX_EVENTS`/`TEAM_HEADLESS_MAX_LINE_BYTES`
  stop further per-line translation past their caps (with one
  truncation-notice event) without affecting `ok`/`exit_code`.
- **Completion detection — exact ordering.** Each poll (`TEAM_HEADLESS_POLL_SECONDS`,
  default 0.5s, between iterations — same "poll on an interval" shape as
  `run_startup_watch()`'s own loop, `app/app.py:1213`): tail whatever's new
  in `out_path`, then check `rc_path` **before** checking whether the
  session still exists. This ordering isn't arbitrary — the wrapping shell
  always writes `rc_path` (via `wait $!; echo $? >rc_path`) strictly
  *before* it finishes and the pane's command exits, so **`rc_path`
  becoming valid always happens no later than the session disappearing,
  never after.** Concretely:
  - `rc_path` exists and parses as an int → the real command genuinely
    finished; `exit_code` is that value. (If it exists but is empty/
    unparseable — the `echo`'s write hasn't fully landed yet — treat as
    "not ready", keep polling; this is bounded by the same overall timeout
    and grace-period budget below, not an unbounded wait.)
  - `rc_path` still absent **and** `tmux_has(session)` is now `False` → the
    session ended without ever recording an exit code (the whole tmux
    server was killed, `kill-session` was called externally bypassing this
    module's own cancellation path below, disk failure mid-write, etc.).
    **Treated as `cancelled=True`, `ok=False`, `exit_code=None`** — never
    as success. This is deliberately the one case this design cannot fully
    explain after the fact, so it is never optimistically assumed clean.
  - Neither yet, and `timeout` hasn't elapsed → keep polling.
- **Cancellation — targeted `SIGTERM`, never `capture-pane`, never a blunt
  `kill-session` first.** Signaling a `RUN_USER`-owned PID from `SVC_USER`
  directly (`os.kill()`) would fail with `EPERM` — cross-UID signals need
  root or the same UID, and the only standing privilege this module has is
  `TMUX`. So cancellation reuses **exactly that**, nothing more: a second,
  throwaway, self-cleaning tmux session whose entire job is one line —
  ```python
  subprocess.run(TMUX + ["new-session", "-d", "-s", f"{session}-kill",
                         "bash", "-lc", f"kill -{sig_name} {pid}"])
  ```
  — run as `RUN_USER` (so the signal permission check passes: same UID as
  the target), self-terminating the instant `kill` returns, exactly the
  same auto-cleanup property `instance_start()`'s own doc comment already
  establishes for any tmux pane whose command exits (`app/app.py:1244-1247`).
  No new sudoers surface at all — this is `tmux new-session` running a
  different one-line script, nothing `tmux` itself wasn't already whitelisted
  for. Escalation, once `timeout` elapses:
  1. `kill -TERM <pid>` via the helper above; `cancel_reason="timeout"`.
  2. Keep polling for `rc_path` (should appear promptly — the wrapping
     shell's remaining work after the child dies is trivial). If it hasn't
     within `TEAM_HEADLESS_KILL_GRACE_SECONDS` (default 10), escalate:
     `kill -KILL <pid>` via the same helper shape.
  3. If `rc_path` *still* never appears within one more grace window, the
     wrapping shell itself is wedged — last resort, `tmux kill-session -t
     <session>` (idempotent; harmless if it's already gone, same
     `capture_output=True`-swallowed-errors style `instance_stop()` already
     uses at `app/app.py:1263`). `exit_code=None`, classified per the
     "missing rc" rule above.
  An externally-sent `SIGTERM` (not initiated by `agent_run()`'s own
  `timeout` — e.g. a future 6d "stop team" action, an operator's own `kill`
  against the PID) is classified identically once observed in `rc_path`
  (`cancel_reason="external"`) — cancellation classification doesn't care
  who sent the signal, only what the exit code says.
- **`ok`/`exit_code`/cancellation classification are otherwise exactly as
  the prior version specified — unchanged**: `ok = (exit_code == 0)`;
  `cancelled = exit_code is not None and exit_code >= 128 and
  (exit_code - 128) in {SIGHUP, SIGINT, SIGQUIT, SIGTERM, SIGKILL}` — a
  general Unix signal-exit convention, not a Claude-only "143" special
  case, since Codex's/aider's own signal-exit codes are unconfirmed pending
  Tier 3 verification. Bash's `$?` capturing `128+N` for a foreground child
  killed by signal `N` is standard POSIX shell behavior (confirmed, not an
  assumption specific to this design) — it applies here because the
  engine process is `wait`ed on directly by PID, not backgrounded further
  or wrapped in anything that would mask its wait status.
- **Cleanup**: once a result is determined (success, cancelled, or missing-
  rc), `agent_run()` does one final unconditional tail pass (closes a small
  window where the last bytes of `out_path` were flushed between the last
  poll and the process actually exiting), reads `err_path` bounded by
  `TEAM_HEADLESS_STDERR_TAIL_BYTES` into `stderr_tail`, then
  `shutil.rmtree(RUNDIR, ignore_errors=True)` — safe and needing no special
  privilege, since `SVC_USER` owns `RUNDIR` regardless of which user wrote
  the individual files inside it (no sticky bit set). The **durable**
  artifact is `log_path` (this module's own translated `.jsonl`), not the
  raw `out_path`/`err_path`/`pid_path`/`rc_path`/prompt-file plumbing, which
  is deleted along with the rest of `RUNDIR`. If the session somehow still
  exists at this point, one final defensive `tmux kill-session` (same
  swallowed-error style as above).
- **Stale-run sweep** (opportunistic, at the top of every `agent_run()`
  call, not a background thread/timer): any `TEAM_STATE_DIR/_headless/<id>/`
  directory older than `TEAM_HEADLESS_STALE_RUN_TTL_SECONDS` (default 7200)
  **and** whose corresponding session name no longer exists is removed, with
  a defensive `tmux kill-session` attempt on the matching name regardless.
  Covers `app.py`'s/`teams.py`'s own process being restarted mid-run — the
  fuller "service restart while a team runs" story (state reconstruction,
  UI self-healing) is 6d's job; this sub-spec only guarantees it doesn't
  leak directories forever.
- **PID reuse** (the target PID having already exited and been recycled by
  the OS before a signal reaches it) is a generic, low-probability Unix race
  every process-management tool accepts; not specially defended against
  here beyond the ordinary short poll cadence keeping the exposure window
  small. Noted, not treated as a hard requirement to eliminate.

**Return shape** (unchanged from the prior version):

```python
{
  "ok": bool,
  "text": str,
  "session_id": str | None,
  "exit_code": int | None,
  "cancelled": bool,
  "cancel_reason": "timeout" | "external" | None,
  "event_count": int,
  "truncated": bool,
  "log_path": str,
  "stderr_tail": str,
  "error": str | None,
}
```

`text` extraction per format is unchanged: **claude-stream-json** — the
final `result` line's `result` field, else the concatenation of `assistant`
text blocks seen so far. **codex-jsonl** — the completed turn's final
message item text, same fallback. **plain** (aider) — the full captured
`out_path` content, bounded by the same general size discipline.

### 5. Session naming — why it can't collide with a project session

`instance_start()` names project sessions `f"{engine_name}-{name}"`
(`app/app.py:1239`); `active_engine()` (`app/app.py:1192`) and
`_reap_dead_state()` (`app/app.py:1266`) both key off that exact shape —
`active_engine()` does a **targeted** `tmux_has(f"{e}-{name}")` check per
real engine `e` (from `load_engines()`) and real project `name` (from
`instance_names()`); neither function scans `tmux list-sessions` blindly.

Headless sessions are named `f"switchboard-headless-{run_id}"` (§2) and
their throwaway kill-helper `f"switchboard-headless-{run_id}-kill"` (§4).
For either to be mistaken for a project session, `active_engine()` would have
to find some loaded engine `e` and project `name` where
`f"{e}-{name}"` equals a live headless session name. Two ways that could
happen, and both must be closed:

- `e == "switchboard-headless"`, any `name` — the obvious case.
- `e == "switchboard"`, `name == f"headless-{run_id}"` — the non-obvious one.
  `active_engine()` would then report that project as running engine
  `switchboard` for as long as the headless run lives, i.e. an existing
  project row showing the wrong state. Improbable (the directory name would
  have to match a live run's random token) but constructible, so an
  exact-name reservation is not sufficient.

Reserving the entire `switchboard` **prefix** in `_parse_engine_file()` (§1)
closes both: no loaded engine's name can begin the string, so no
`f"{e}-{name}"` can ever equal `switchboard-headless-*`. This closes the gap
**structurally**, not probabilistically: no live cross-referencing against
`load_engines()`/`instance_names()` is needed at session-creation time, and
none is added, keeping `agent_run()`'s hot path simple.

`run_id` itself (`f"{int(time.time())}-{secrets.token_hex(6)}"`, matching
`new_session()`'s own use of `secrets` at `app/app.py:252`) also guarantees
no two concurrent headless runs collide with each other.

### 6. Normalized event envelope, CLI entry point

**Unchanged from the prior version** — the §4.1 translation table (native
event → `kind`/`text`/`meta`, per engine and format), and the CLI's shape
(`python3 app/teams.py run/list-engines`, `argparse`, stderr-streams-events/
stdout-prints-final-JSON, matching `scripts/taiga_push_spec.py`'s
`_parse_args()`/`_run()`/`main()` shape) — neither depends on the transport
mechanism reworked above.

## Affected areas
- `app/app.py` — `Engine.__slots__` + `_parse_engine_file()`: four new
  optional keys, plus the reserved-name check (§1). **No other change** —
  `TMUX`, `tmux_has()`, `instance_start()`/`instance_stop()`/
  `_reap_dead_state()` are all read-only imports for `teams.py`, untouched
  themselves.
- `app/teams.py` — **new**. `agent_run()`, the envelope translator, the
  tmux-hosted spawn/tail/cancel/cleanup machinery, the CLI.
- `engines.d/claude.engine`, `engines.d/codex.engine`, `engines.d/aider.engine`
  — new `HEADLESS_*` keys, each verified per "Test plan" below.
- `config/switchboard.env.example` — new section: `TEAM_STATE_DIR` plus
  `TEAM_HEADLESS_TIMEOUT_SECONDS`, `TEAM_HEADLESS_KILL_GRACE_SECONDS`,
  `TEAM_HEADLESS_POLL_SECONDS`, `TEAM_HEADLESS_MAX_EVENTS`,
  `TEAM_HEADLESS_MAX_LINE_BYTES`, `TEAM_HEADLESS_PROMPT_MAX_BYTES`,
  `TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES`, `TEAM_HEADLESS_STDERR_TAIL_BYTES`,
  `TEAM_HEADLESS_STALE_RUN_TTL_SECONDS` — all optional, arbitrary-but-
  reasonable built-in defaults, same documented-but-commented-out style as
  `GITEA_POLL_INTERVAL_SECONDS`. **Not** included: `TEAM_LLM_BASE_URL`/
  `TEAM_LLM_MODEL`/`TEAM_MAX_ROUNDS` (6c), `TEAM_GROUNDING_MAX_BYTES` (6b).
- `docs/ADDING_AN_ENGINE.md` — documents the four new keys, the
  `{resume}`/`{prompt_file}` placeholder mechanics, the no-`HEADLESS_CMD`-
  means-unaffected rule, and the one-line note about the reserved
  `switchboard-headless` engine name.
- `tests/test_teams_headless.py` — **new**, following
  `tests/test_gitea_poll.py`'s conventions (`sys.path.insert(0, APP_DIR)`,
  env vars via `os.environ.setdefault` before import, `unittest`).

No `install.sh` change. No `docs/ARCHITECTURE.md` change. No schema/data-model
changes. No HTTP API changes — `app/teams.py` is not wired into `app.py`'s
`Handler` in this sub-spec (that first happens in 6d).

## Edge cases
- Engine name not found, or found but not `headless_enabled` → `ValueError`,
  no tmux session created.
- `workdir` missing or not a directory → `ValueError`, no tmux session
  created.
- `session_id` given for an engine with no `{resume}` support → `ValueError`.
- Prompt exceeds the mode-appropriate byte cap (§3) → `ValueError`.
- Engine binary not on `RUN_USER`'s `PATH`, or not logged in → a normal
  nonzero `exit_code` path (`bash -lc` itself starts fine, backgrounds a
  command that immediately fails) — `ok=False`, `error` derived from
  `stderr_tail`, not a Python exception. Distinct from every `ValueError`
  case above (those are "never spawned anything"; this is "spawned, and the
  engine itself failed fast").
- `bash -lc` fails before even backgrounding anything (missing `bash` for
  `RUN_USER`, a bug in the generated `script`) → the "startup confirmation"
  timeout path in §4 (`pid_path` never appears) — `ok=False`,
  `error="headless session failed to start"`.
- `rc_path` present but empty/unparseable at the moment it's first observed
  → treated as "not yet flushed", retried on the next poll, bounded by the
  overall timeout/grace budget — never immediately misreported as a
  malformed result.
- Session ends with `rc_path` still absent (§4's "missing rc" case) →
  `cancelled=True`, `ok=False`, `exit_code=None`. Never treated as success.
- Two concurrent `agent_run()` calls against the **same** `workdir` — not
  prevented or detected at this layer; worktree-per-teammate isolation is
  explicitly 6d's job. Each gets its own `run_id`-namespaced tmux session
  and run directory regardless, so they don't collide with *each other* —
  only shared-directory semantics (both editing the same files) are
  unmanaged here, exactly as running two interactive engine sessions in the
  same directory already is today.
- `RUN_USER`'s shell umask is unusually strict (e.g. `077`), making
  `out.jsonl`/`out.rc`/etc. unreadable by `SVC_USER` despite the `0711`
  directory permission being correct → surfaces as a `PermissionError` when
  `agent_run()` tries to read a file it can traverse to but not open; caught
  and reported as a clear, specific error (not a silent hang, not a crash)
  rather than assumed away. Documented as an environmental precondition
  (default umask) in `docs/ADDING_AN_ENGINE.md`'s headless section.
- Empty prompt string (`""`) → allowed through as-is, no special-casing.
- `log_path`'s parent directory missing → created
  (`os.makedirs(..., exist_ok=True)`).
- A truly empty stdout (process ran, produced nothing, exited 0) →
  `text=""`, `ok=True`. Not an error.
- PID reuse (§4) — accepted low-probability race, not specially defended.
- Platform: already Linux-only (`sudo`, `tmux`, `systemd`); no new
  cross-platform concern.

## Acceptance criteria
- [ ] Given `claude.engine` with valid `HEADLESS_*` keys, when
      `agent_run("claude", <scratch workdir>, "<short prompt>")` runs
      against the real `claude` CLI logged in as `RUN_USER`, then it returns
      `ok=True`, non-empty `text`, a non-`None` `session_id`, `exit_code=0`
      — verified by actually running it, not guessed. No new sudoers entry
      required for this to work.
- [ ] Same for `codex.engine` and `aider.engine` (aider without
      `session_id`) — each verified by actually running it.
- [ ] Given `session_id` from a prior `claude`/`codex` run, when
      `agent_run()` is called again with it, then the constructed command
      uses each engine's own resume syntax correctly (`--resume <id>` for
      Claude; `exec resume <id>` for Codex) and the turn is genuinely
      continued (verified with a turn-2 question that only makes sense with
      turn-1 context).
- [ ] Given an engine `.engine` file with no `HEADLESS_CMD` (or malformed
      `HEADLESS_FORMAT`/`HEADLESS_PROMPT`), when `load_engines()` parses it,
      then `Engine.headless_enabled` is `False`, and every pre-existing
      engine-loading/instance-toggle test in `tests/` still passes
      unmodified.
- [ ] Given an `.engine` file named `switchboard-headless.engine`, when
      `load_engines()` parses it, then it is ignored (not returned in the
      engines dict), same as an `.engine.example` file.
- [ ] Given an `.engine` file named `switchboard.engine` — the non-obvious
      collision case from §2 "Session naming" — when `load_engines()` parses
      it, then it is likewise ignored. An implementation that reserves only
      the exact name `switchboard-headless` passes the test above and fails
      this one; both must pass.
- [ ] Given a loaded engine named `switchboard` is impossible per the rule
      above, when a headless run is live for `run_id = R` and a project
      directory named `headless-R` exists, then `active_engine("headless-R")`
      returns `None` — the headless session is never reported as that
      project's running engine.
- [ ] Given a fixture stream containing one line that fails `json.loads()`,
      when `agent_run()` processes it (Tier 1/2 test, no real engine
      needed), then exactly one `kind="error"` envelope is appended and the
      run completes normally; no exception escapes `agent_run()`.
- [ ] Given a running headless process, when `agent_run()`'s own `timeout`
      elapses, then it sends a targeted `SIGTERM` to the engine process
      specifically (not the tmux session), the session's own `rc_path`
      still gets written normally, and `cancelled=True`/`cancel_reason=
      "timeout"` is reported. Claude Code's specific 143-on-`SIGTERM`
      behavior is confirmed via Tier 3 verification; Codex's/aider's own
      signal-exit codes are recorded (whatever they turn out to be), not
      assumed.
- [ ] Given `agent_run()` called with `session_id` set against an engine
      with no resume support (aider), then it raises `ValueError`
      immediately, before any tmux session is created.
- [ ] Given a process that never exits on its own, when `timeout` elapses,
      then `agent_run()` escalates `SIGTERM` → (grace period) → `SIGKILL` →
      (grace period) → last-resort `kill-session`, and still returns a
      well-formed result dict in every branch of that escalation, never
      hanging indefinitely.
- [ ] Given a tmux session that disappears with `rc_path` never written
      (simulated via a Tier 2 test that force-kills the whole session out
      from under `agent_run()`), then the result is `cancelled=True`,
      `ok=False`, `exit_code=None` — never reported as success.
- [ ] Given a fixture stream exceeding `TEAM_HEADLESS_MAX_EVENTS` or a
      single line exceeding `TEAM_HEADLESS_MAX_LINE_BYTES`, then further
      per-line events stop being appended, one truncation-notice event is
      appended, `truncated=True`, and `ok`/`exit_code` still reflect the
      real process outcome.
- [ ] After any `agent_run()` call (success, cancelled, or errored), the
      per-run directory under `TEAM_STATE_DIR/_headless/` is gone and no
      tmux session matching `switchboard-headless-*` remains — verified
      directly (`tmux list-sessions`, directory listing), not just inferred
      from the return value.
- [ ] `docs/ADDING_AN_ENGINE.md` documents `HEADLESS_CMD`, `HEADLESS_FORMAT`,
      `HEADLESS_PROMPT`, `HEADLESS_RESUME`, the `{resume}`/`{prompt_file}`
      placeholder mechanics, the reserved `switchboard-headless` name, and
      states that `HEADLESS_ROLE_FLAG`/`HEADLESS_SCHEMA_FLAG`/
      `HEADLESS_LEAD_FORMAT` are reserved for 6c and not yet consumed.
- [ ] `python3 app/teams.py list-engines` and `python3 app/teams.py run ...`
      both work against a real `ENGINES_DIR`/`PROJECTS_DIR` with no server
      running, no other part of the app touched, and no `install.sh`
      changes applied.

## Test plan

**Tier 1 — pure unit, no subprocess, no tmux, no real CLI**
(`tests/test_teams_headless.py`, bulk of the file): `_parse_engine_file()`'s
four new keys and the reserved-name rule; the pure argv/`script`-template
construction function (`{resume}`/`{prompt_file}` rendering, per-mode
branching, both byte caps) tested as pure input-in/string-or-`ValueError`-out
functions, no process involved; the envelope translator fed the recorded
fixture files below, asserting the exact `kind`/`meta` mapping table; the
malformed-line and stream-cap behaviors fed synthetic fixtures built to be
malformed/oversized on purpose; every "Edge cases" validation failure
asserted as `ValueError` with `subprocess.run`/`tmux_has` monkeypatched to
fail the test if either is ever called (proves nothing was spawned).

**Tier 2 — real tmux, real (test-authored) process, no real engine CLI, no
sudo**: `TMUX` monkeypatched from `["sudo", "-u", RUN_USER, "/usr/bin/tmux"]`
down to `["tmux"]` (same category of substitution `test_gitea_poll.py`
already applies to `subprocess.run`/`_gitea_api`) — real `tmux`, running as
whichever user runs the test suite, no `RUN_USER` account or passwordless
sudo needed in CI. A tiny Python helper script (written via `tempfile` by
the test itself) stands in for "the engine": one variant prints fixture
NDJSON to stdout and exits 0; one hangs, to exercise real
`SIGTERM`→grace→`SIGKILL` escalation end to end (including confirming
`$?` really does land as `128+15` in `rc_path` for a plain `SIGTERM`,
closing that "confirm, don't assume" item from the coordinator's ask); one
is killed by the test forcing `tmux kill-session` on it directly mid-run, to
exercise the missing-`rc_path` classification; one reads its actual stdin,
to exercise `stdin`-mode prompt delivery. This tier is what actually proves
the tailing-by-offset, completion-ordering, and cleanup logic in §4, without
needing a real `RUN_USER`/engine CLI at all.

**Tier 3 — real CLI verification (manual, developer stage, per
`docs/ADDING_AN_ENGINE.md`'s standing rule)**: run each of `claude`, `codex`,
`aider` for real, headless, through the actual tmux-hosted path, against a
scratch project. Confirm the binary is on `RUN_USER`'s `PATH` and logged in;
run `python3 app/teams.py run <engine> <scratch dir> --prompt "..."` for a
first turn and again with `--session-id` to confirm resume; capture the
real output into `tests/fixtures/headless/<engine>_stream.jsonl` (`.txt` for
aider) for Tier 1 to replay deterministically; send a real `SIGTERM`
mid-run (`kill -TERM <pid>`, or via the CLI's own future interrupt handling)
to confirm/record the actual signal-exit code per engine, replacing the
"143 confirmed for Claude, unconfirmed for the other two" note above with
real findings. **If a given CLI is not installed on the box doing this
pass**, that engine's `HEADLESS_*` keys ship marked explicitly **"unverified
— believed correct per vendor docs as of 2026-08-13, not yet run
end-to-end"** in both the commit message and `docs/ADDING_AN_ENGINE.md`,
mirroring this repo's own existing verified/documented distinction — never
silently marked done. `docs/implementation.md` records which of the three
actually got a real run.

## Open questions
- **`HEADLESS_ROLE_FLAG`/`HEADLESS_SCHEMA_FLAG`/`HEADLESS_LEAD_FORMAT`**
  remain deliberately unparsed in 6a (see "Non-goals") — 6c will need one
  more small, additive touch to `_parse_engine_file()`/`Engine.__slots__`.
- **Exact final `HEADLESS_CMD`/`HEADLESS_RESUME` flag text for Codex** is
  the resolved *mechanism* (§3), confirmed byte-for-byte in Tier 3. If
  Codex's actual `exec resume` syntax doesn't accept trailing flags the way
  assumed, the fix is confined to `codex.engine`'s own two lines.
- **`TEAM_HEADLESS_POLL_SECONDS` default (0.5s)** trades latency
  (how quickly a new stream event or a completed run is noticed) against
  overhead (a `tmux has-session`/file-`stat` pair per tick per in-flight
  run). Fine for 6a's single-run CLI use; 6c's lead loop or 6f's overwatch
  feed may want this tuned per-context once there are many concurrent runs
  — flagging now so a future sub-spec doesn't treat the current default as
  load-bearing.
- **Whether the opportunistic stale-run sweep (§4) should eventually fold
  into `_reap_dead_state()`** rather than staying `teams.py`-local, once 6d
  gives headless runs an actual team/session concept `app.py` needs to know
  about for its own "self-heal on restart" story. Leaning: keep it local
  through 6a–6c, revisit in 6d when there's a real reason to unify the two
  sweeps.
- **`--include-partial-messages`/`stream_event`** remains deliberately
  unrequested by default (unchanged from the prior version).

## Risk / rollback notes
Every change here is additive to existing files (`Engine`,
`_parse_engine_file()`, `switchboard.env.example`) plus wholly new files
(`app/teams.py`, `tests/test_teams_headless.py`, three new fixture files).
`install.sh` and `docs/ARCHITECTURE.md` are untouched. Nothing in `app.py`'s
HTTP handler, `instance_start()`/`instance_stop()`, or any existing route is
touched — the regression risk to today's single-session toggle is exactly
what the "zero behavioral change" acceptance criterion tests for directly.
The only operationally live surface this sub-spec introduces at all is a
new, empty-by-default directory (`TEAM_STATE_DIR`) and, transiently, tmux
sessions matching `switchboard-headless-*` that are torn down within the
same `agent_run()` call that creates them — nothing persists past a single
invocation except the translated `.jsonl` log files. Rollback is reverting
the commit; there is no sudoers file, install-time state, or privilege
change to separately undo.
