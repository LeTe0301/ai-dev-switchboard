# Spec: Team session lifecycle, part 1 — worktrees + tmux dashboard session (backend/CLI) (sub-spec 6d, part 1 of 2)

## Split rationale (read this first)

`docs/story.md` §5's own 6d entry bundles four things into one deliverable:
per-teammate git worktrees, a persistent `team-<project>` tmux session with
one window per agent, self-heal/reap coverage, and web-UI Start/Stop wiring
(plus the fully independent `install.sh --with-ollama`). Per this role's own
"load-balanced decomposition" duty: the first three are one coherent,
high-risk backend layer (new privileged-but-TMUX-only subprocess plumbing,
real git operations, real persistent tmux sessions, a new self-heal
contract) that needs to be right and independently testable via CLI/pytest
— exactly the discipline 6a/6b/6c already established, each shipped as its
own cycle. Bolting the web-UI layer (new HTTP routes, a background thread
inside `app.py`, new template/JS) onto the *same* dispatch as that backend
work repeats the exact "data/resource layer + UI layer in one pass" pattern
this role is supposed to catch.

**This spec covers part 1 only**: worktree lifecycle, the `team-<project>`
tmux session/windows, and the reap/self-heal sweep — all CLI-driven, zero
new HTTP routes, zero change to how a human currently starts a team (still
`python3 app/teams.py team-launch ...` from a shell). **Part 2** (web UI
Start/Stop control, `_reap_dead_state()` wiring, `install.sh --with-ollama`)
is previewed at the end of this document for context, but is its own
follow-on spec/cycle — see "Part 2 preview". Recommend the orchestrator run
these as two sequential build cycles, not one.

Naming follows this repo's own precedent for exactly this kind of split
(see `git log`: "2c part 2a"/"2c part 2b" for `install.sh
--with-deploy-target` vs. the switchboard-side deploy dispatch).

## Summary

`app/teams.py` gains: a TMUX-only synchronous helper for running a single
RUN_USER-privileged shell command (reused for `git worktree add`/`remove` —
no new sudoers rule, the same `TMUX` constant every other RUN_USER crossing
in this codebase already uses); project-precondition validation (git repo,
non-detached HEAD, clean tree); one git worktree per teammate at
`<workdir>.teams/<agent>/`, each on its own branch; a persistent
`team-<project>` tmux session with one window per agent (`lead` plus one per
teammate), each window a live, reconnectable *tail dashboard* over that
agent's own accumulated event log — not the actual headless process itself
(see "Why dashboard windows, not process windows" below for why that's the
correct reading of `docs/story.md` §2.2, not a deviation from it); three new
CLI subcommands (`team-launch`, `team-stop`, `team-reap`); and a
`sweep_dead_teams()` self-heal function extending the same discipline
`_reap_dead_state()`/`_sweep_stale_runs()` already established, for a new
resource type (team sessions + worktrees) rather than a new pattern.

`app/app.py` gains exactly one small, additive change: the existing
`switchboard`-prefix engine-name reservation becomes a `(switchboard,
team)` reservation, for the identical collision-safety reason the
`switchboard` one already exists for.

6c's own three carried-forward limitations (codex tier 2 unverified end to
end; repeated delegation mitigated, not fixed; the `None`-mapping-value
follow-up) are **untouched by this cycle** — 6d never touches the lead
loop, the tier adapters, or `_build_headless_argv()`. Recorded here so they
aren't silently dropped from the story's own record, not because this spec
does anything about them.

## Goals

- One `git worktree` per teammate, created at team-launch time, torn down
  (when clean) on team-stop or self-heal sweep, **never silently discarding
  uncommitted work** — a dirty worktree is left on disk, reported by name,
  not force-removed.
- A `team-<project>` tmux session that exists for the lifetime of a team run
  (survives individual `agent_run()` calls coming and going), with one
  window per agent a human can attach to at any time and see that agent's
  live, accumulating raw event stream — not just its most recent call.
- Starting a team twice for the same project is refused cleanly, before any
  worktree is touched — never a raw tmux "duplicate session" error.
- A crashed `app.py` process, a killed tmux session, or a full host reboot
  while a team is mid-run is detected and reconciled the next time anything
  asks (`team-reap`, or later `_reap_dead_state()` in part 2) — never
  silently left inconsistent, and never optimistically assumed to have
  succeeded, matching 6a's own `_recover_in_progress()` discipline.
- `team_step()`'s `delegate` branch actually uses the teammate's own
  worktree as `agent_run()`'s `workdir`, and actually gives each teammate a
  stable, appendable log path so its dashboard window shows continuity
  across multiple delegations — both currently missing (6c calls
  `agent_run()` directly against the shared project tree with an ad-hoc
  per-call log path; this is exactly the gap 6c's own "Non-goals" flagged
  as 6d's job).
- Zero new privileged surface: every RUN_USER-owned filesystem/tmux
  operation this spec adds goes through the existing `TMUX` constant.

## Non-goals

- **Web UI, HTTP routes, `_reap_dead_state()` wiring, `install.sh
  --with-ollama`.** All part 2 — see "Part 2 preview". `app.py` does not
  import `app.teams` as of this spec; that import direction is part 2's.
- **Roster/composition picker (lead + members selection UI)** — 6e, per
  `docs/story.md` §5, unchanged.
- **Overwatch feed / escalation inbox UI** — 6f, unchanged. This spec makes
  the *dashboard windows* exist (raw tmux attach); a rendered, filterable
  web feed over the same `.jsonl` files is 6f's job, not duplicated here.
- **Per-teammate `--allowedTools`/`--sandbox` scoping.** Deferred to 6e per
  `docs/story.md` §7's own open question. A worthwhile, explicitly *not
  closed*, side effect of this cycle: a **tier-2/3 lead's own turn still
  runs directly against the shared project tree** (`state["workdir"]`,
  unchanged from 6c) — only *teammates* (delegate targets) get worktrees,
  matching `docs/story.md` §4's own architecture diagram ("one git worktree
  per teammate", not per lead). If a tier-2/3 lead engine's own tool
  permissions aren't scoped down, it retains whatever access that engine
  normally has to the real project tree during its own planning turn — a
  real, carried-forward risk, unchanged from 6c, not fixed here.
- **Automatic merge-back of a teammate's worktree branch.** Per
  `docs/story.md` §7's own leaning ("left for review, consistent with
  deploy being manual-click-only") — adopted as settled by this spec, not
  reopened. `git worktree remove` never touches the branch itself (git's
  own behavior — removing a worktree only unlinks the checkout, the branch
  and its commits remain in the repo's object store), so "left for review"
  holds even after a worktree directory is gone.
- **Cross-project teams, replacing single-engine sessions.** Unchanged
  non-goals from `docs/story.md` §3.
- **Any change to the lead loop, tier adapters, `_build_headless_argv()`,
  grounding, or `_validate_lead_action()`.** Grounding stays keyed off
  `state["workdir"]` (the real project tree) always, never a teammate's
  worktree — a worktree is for a teammate's *code* work; grounding
  documents should reflect the project's own committed truth, not one
  agent's possibly-diverged branch.
- **Solving "how long may a `blocked_ask_user` run sit unanswered before
  its resources are reclaimed."** Deliberately never swept by TTL (see
  "Open questions" — this is a real, disclosed tradeoff, not an oversight).

## Background / current state

From `docs/story.md`/6a-6c (`app/teams.py`, current size 2859 lines):

- `agent_run(engine, workdir, prompt, *, session_id=None, timeout=...,
  log_path=None, schema=None)` (`:915`) — spawns exactly one throwaway tmux
  session per call, named `switchboard-headless-{run_id}` (a fresh random
  `run_id` **every single call**, including repeated delegations to the
  *same* teammate within one team run — continuity across calls is via
  `--resume {session_id}`, not a shared tmux session/window). `log_path`
  defaults to a fresh ad-hoc file per call if not given; when given, events
  are **appended** (`app/teams.py:716`, `open(self.log_path, "a")`) — this
  append behavior is the load-bearing fact that makes a stable per-agent
  dashboard log possible with zero change to `agent_run()`/`_Tailer` itself.
- `team_step()`'s `delegate` branch (`:2482`) currently calls `agent_run(
  agent, state["workdir"], task, session_id=...)` — no `log_path`, no
  worktree. Every delegation runs directly against the shared project
  directory, and every delegation's own event log is a throwaway ad-hoc
  file nothing else ever looks at again.
- `team_run()`'s own docstring / `_drive_and_report()` (`:2662`) already
  establish the "tail a stable file on a background thread for live
  visibility" pattern this spec's dashboard windows generalize — `
  _drive_and_report()` tails `transcript.jsonl` to stderr while
  `team_run()` blocks in the foreground. This spec's `lead` window does the
  identical thing, just via `tail -F` in a tmux pane instead of a Python
  thread writing to stderr.
- `_new_state()` (`:2242`)/`_persist()`/`_load_state()` — `run.json`'s
  current shape has no `worktrees` or `project_name` field; both are
  additive in this spec.
- `_sweep_stale_runs()` (`:785`) — 6a's own precedent for "opportunistic
  sweep at the top of a CLI entry point, not a background thread/timer",
  applied to `TEAM_STATE_DIR/_headless/<run_id>/` dirs past
  `TEAM_HEADLESS_STALE_RUN_TTL_SECONDS`. `sweep_dead_teams()` in this spec
  is the same idiom applied to team sessions/worktrees.
- `app/app.py:191`, `TMUX = ["sudo", "-u", RUN_USER, "/usr/bin/tmux"]` — the
  **only** sanctioned RUN_USER crossing point (`docs/ARCHITECTURE.md`
  "Processes and privilege boundaries"). `app/app.py:739`'s
  `GITEA_SYNC_SCRIPT` sudo call is a *pre-existing, separate* privileged
  path for an unrelated feature (2c) — not a precedent this spec is allowed
  to reuse or extend; this spec's own new privileged operations (`git
  worktree add`/`remove`) go through `TMUX` only.
- `_parse_engine_file()` (`app/app.py:333`) already reserves the whole
  `switchboard` name prefix, specifically because
  `switchboard-headless-<run_id>` session names must be structurally
  immune to an engine literally named `switchboard` (`app/app.py:346-357`,
  `docs/ADDING_AN_ENGINE.md` "Reserved name"). This spec's `team-<project>`
  session name has the exact same exposure against an engine named `team`
  (see "Engine-name reservation" below) — same bug class, same fix shape.
- `docs/ARCHITECTURE.md` "In-memory state and its one sharp edge" already
  documents and *accepts* that `_session_urls`/the ttyd/code-server process
  tables are lost on a service restart, self-healing via `_reap_dead_state
  ()` once the underlying resource's own true state (a tmux session, in
  ttyd/code-server's case a `subprocess.Popen` handle) is checked directly
  rather than trusted from memory. This spec's `sweep_dead_teams()`
  generalizes that *exact, already-accepted* tradeoff to team sessions —
  not a new kind of gap.

## Resolved from `docs/story.md` §5's own open notes

**`_session_urls` does *not* need to become per-window — nothing to
generalize.** `_session_urls`/`url_regex` capture a *hosted browser link*
from an **interactive** engine session started via `instance_start()`
(`engine.cmd`, watched by `run_startup_watch()`). Headless invocations
(`HEADLESS_CMD`, what `agent_run()` and every team window's underlying call
use) are non-interactive, single-turn, and never produce a hosted URL at
all — there is no code path where a team window would ever have one to
capture. This resolves the story's own flagged concern directly, in this
spec, rather than deferring it: **no change to `_session_urls` in 6d, and
none needed in 6e/6f either** — nothing about the roster/composition UI or
the overwatch feed needs a captured browser URL.

**Where `ask_user`/`inbox.json` sits in a live session's lifecycle.**
Unchanged from 6c in every mechanical respect — still written by
`team_step()`'s `ask_user` branch, still lives at `_inbox_path(run_id)`,
still answered via `team-resolve`. What this spec adds: while a run sits
`blocked_ask_user`, its tmux session/windows and worktrees stay fully
alive and are **explicitly exempted from the TTL sweep** (see "Acceptance
criteria" — a genuinely open question, not an escalation forgotten by the
tooling). A human can not only `team-resolve` it but literally `tmux
attach` to a teammate's window first, to see recent raw output for context
before answering — a capability this spec's dashboard windows provide for
free, ahead of 6f's own polished inbox UI.

## Why dashboard windows, not process windows

`docs/story.md` §2.2's settled decision says a teammate is "spawned inside
its own tmux window so a human can still attach and watch raw output
live." Taken completely literally (the window's own pane command *is* the
headless CLI invocation), this doesn't fit how `agent_run()` actually
works, by 6a's own deliberate design (`docs/spec.md` §4.2 in the 6c spec,
"Why the lead never resumes its own session"): every invocation is a
short-lived, single-turn subprocess, not a persistent process a window
could stay attached to across multiple delegations. Reconciling the two:

- The **actual** headless invocation still happens exactly as 6a/6c built
  it — its own short-lived `switchboard-headless-<run_id>` tmux session,
  one per call, unchanged. Nothing here touches that.
- Each **persistent** per-agent window in `team-<project>` is a `tail -F`
  over that agent's own **stable, appended** event log (see "Per-agent
  stable log paths" below) — always there, always attachable, shows the
  full accumulated history across every delegation to that agent in the
  run, which a "window = one ephemeral process" reading could never
  provide (the window would vanish the instant a single turn finished).

This is the more literal reading of the *goal* ("a human can still attach
and watch raw output live") than of the *mechanism* sentence, and it is
also the only reading compatible with the hard constraint that RUN_USER is
reached only through `TMUX` — see "Privilege boundary" below for why a
persistent, driving process *inside* a tmux window doesn't actually work
under that constraint even if it were otherwise desirable.

## Proposed approach

### 1. Privilege boundary — the TMUX-only synchronous RUN_USER helper

New, small, reusable — not `agent_run()`-sized (no NDJSON translation, no
cancellation ladder, no streaming) because a `git worktree` operation is a
fast, well-behaved command, not a long-running agentic process:

```python
def _run_run_user_command(argv: list, cwd: str,
                          timeout: float = TEAM_WORKTREE_OP_TIMEOUT_SECONDS) -> dict:
    """
    Runs argv as RUN_USER, synchronously, via the SAME TMUX constant
    app.py's instance_start()/agent_run() already use -- no new sudoers
    rule, no new privileged path (docs/ARCHITECTURE.md). Spawns a throwaway
    tmux session (`sudo -u RUN_USER tmux new-session -d -c cwd bash -lc
    "argv...; echo $? > rcfile"`), polls for rcfile the same way agent_run()
    polls for out.rc, kills the session if it outlives `timeout` (a single
    TERM-then-kill-session escalation is enough for a well-behaved git
    command -- not the full multi-stage ladder _run_headless_session() uses
    for an LLM-driven engine that might ignore SIGTERM mid-tool-use).
    Returns {"ok": bool, "rc": int|None, "stdout": str, "stderr": str,
    "timed_out": bool}. Never raises.
    """
```

Every new privileged filesystem operation in this spec (`git worktree add`,
`git worktree remove`, and the read-only precondition checks below when run
as RUN_USER rather than SVC_USER — see next section) goes through this one
function. Both `git worktree add` and `git worktree remove` need to be
RUN_USER (they write into `PROJECTS_DIR`-owned territory and should pick up
RUN_USER's own git identity config for free, the same reason `instance_
start()`'s engine sessions run as RUN_USER).

### 2. Project preconditions — `_validate_project_for_team()`

Read-only checks against the project's own working tree (**not** a
worktree — the main directory a team is launched against). Run as
**SVC_USER**, plain `subprocess.run(["git", "-C", workdir, ...])` — no
privilege crossing needed, matching the *existing* precedent that grounding
discovery (`load_grounding()`) already reads project files directly as
SVC_USER with no `TMUX` involvement:

```python
def _validate_project_for_team(workdir: str) -> str | None:
    """Returns a specific error message, or None if the project is a clean,
    non-detached git repo. Three distinct checks, three distinct messages
    (never one generic "invalid project"):
      1. `git -C workdir rev-parse --is-inside-work-tree` fails/!= "true"
         -> "not a git repository"
      2. `git -C workdir symbolic-ref -q HEAD` fails (detached HEAD)
         -> "HEAD is detached -- check out a branch before starting a team"
      3. `git -C workdir status --porcelain` non-empty (tracked OR
         untracked changes -- see "Open questions" for the untracked-file
         judgment call)
         -> "working tree has uncommitted changes -- commit or stash them
             before starting a team"
    Never raises -- a git binary missing or an unexpected subprocess error
    also degrades to a specific, non-traceback message.
    """
```

### 3. Worktree paths, creation, removal

```python
def _worktree_path(project_workdir: str, agent: str) -> str:
    return f"{project_workdir}.teams/{agent}"   # docs/story.md §4, verbatim
```

`_create_worktree(project_workdir, agent, run_id)`:
- Fails with a **specific** message if `_worktree_path()` already exists
  (a previous run's leftover — see "Edge cases" for the dirty-leftover
  case's exact message).
- `git -C project_workdir worktree add -b team-{run_id}-{agent}
  {worktree_path} HEAD`, via `_run_run_user_command()`.
- On failure, returns the command's own stderr tail as the error (never a
  bare non-zero exit with no explanation).

`_remove_worktree(project_workdir, path)` — `git -C project_workdir
worktree remove {path}` (**no** `--force`), via `_run_run_user_command()`.
Three-way outcome, not a bool: `"removed"` (rc 0), `"dirty"` (git refused
because of local/untracked changes — path is left exactly as-is, branch
untouched), `"error"` (anything else — also left as-is). Callers (team-stop,
sweep) report `"dirty"` distinctly from `"error"`, since a dirty worktree
being left behind is the **intended** safety behavior, not a failure.

**Rollback on partial creation failure.** `launch_team()` (below) creates
worktrees for `--members` **in order**; if member *N* fails, every worktree
already created for members `1..N-1` in this same launch attempt is removed
(via `_remove_worktree()`, always clean at this point — nothing has run in
them yet) before the launch call returns its error — "leaves nothing
behind" is a property of the whole launch attempt, not per-member.

### 4. State shape additions

`_new_state()` gains two fields, both additive, existing callers (6c's own
`_cli_team_start()`, every existing test) unaffected by omitting them:

```python
def _new_state(run_id, workdir, lead, members, task, max_rounds=None,
               *, project_name: str = None, worktrees: dict = None) -> dict:
    ...
    "project_name": project_name,   # None for a bare team-start (6c path)
    "worktrees": worktrees or {},   # {} for a bare team-start (6c path)
```

`team_step()`'s `delegate` branch (`app/teams.py:2482`), the one behavioral
change to existing lead-loop code in this whole spec:

```python
worktree = state.get("worktrees", {}).get(agent)
result = agent_run(agent, worktree or state["workdir"], task,
                   session_id=state["teammate_sessions"].get(agent),
                   log_path=_agent_log_path(state["run_id"], agent))
```

A run with no `worktrees` entry for `agent` (every existing 6c test, and
any bare `team-start` CLI invocation that skips `team-launch`) falls back
to `state["workdir"]` — **byte-for-byte the existing 6c behavior** — this
is the one place this spec touches shared lead-loop code, and it's an
additive fallback, not a replacement.

### 5. Per-agent stable log paths + permissions

```python
def _agent_log_path(run_id: str, agent: str) -> str:
    return os.path.join(_run_dir(run_id), "agents", f"{agent}.jsonl")
```

Passed explicitly to `agent_run(..., log_path=...)` from the delegate
branch above — `agent_run()`/`_Tailer` already **append** to a given
`log_path` (`app/teams.py:716`, unchanged), so passing the *same* path
across multiple delegations to the same agent within one run accumulates
them into one growing file with zero change to that machinery.

**Permission requirement this spec introduces and must get right (new —
under 6c, nothing but the SVC_USER-owned Python process itself ever read
these files, so this gap didn't matter yet).** The dashboard windows run as
RUN_USER (via `TMUX`) and must be able to *read* files written by the
SVC_USER-owned driving process. `launch_team()`:
- Creates `_run_dir(run_id)/agents/` with `os.chmod(..., 0o755)`.
- Pre-touches an empty `{agent}.jsonl` per teammate, `chmod 0o644`, before
  that teammate's window is created — so the window's `tail -F` never
  races file creation, and read permission is never dependent on
  `_Tailer`'s own default `open(..., "a")` mode (which doesn't widen an
  already-existing file's permissions).
- Also `chmod`s `_run_dir(run_id)` itself and touches+chmods
  `transcript.jsonl` the same way, for the `lead` window's own target.

This mirrors the *exact* discipline `agent_run()` already uses for
`prompt_path`/`schema_path` ("written by SVC_USER but must be read by
RUN_USER... chmod explicitly rather than relying on ambient umask") —
reused here, not reinvented.

### 6. `team-<project>` tmux session + dashboard windows

```python
def _team_session_name(project_name: str) -> str:
    return f"team-{project_name}"

def _create_team_session(project_name: str, run_id: str, members: list) -> dict:
    """
    {"ok": True} or {"ok": False, "error": "..."}. Refuses up front
    (tmux_has() check, same precondition style instance_start() already
    uses for active_engine()) if a session with this name already exists --
    never lets a raw `tmux new-session` "duplicate session" error be the
    thing that surfaces. Window 0 named "lead", one window per member in
    --members order, named after the member's own engine name. Each
    window's pane command:
        bash -lc 'tail -n +1 -F {log_path} || sleep infinity'
    (`-n +1` so attaching mid-run shows full history, not just new lines;
    `|| sleep infinity` keeps the window alive/attachable even if `tail`
    itself fails outright, e.g. a permission edge case, rather than the
    window vanishing -- "one window per agent" must hold even in a
    misconfigured-permissions scenario, not just the happy path).
    `set-option -t {session} remain-on-exit on` for the same reason.
    """
```

### 7. `launch_team()` / `team-launch`

```python
def launch_team(workdir: str, task: str, lead: dict, members: list,
                max_rounds: int = None) -> dict:
    """
    {"ok": True, "run_id": ..., "session": "team-<name>",
     "worktrees": {agent: path, ...}} or {"ok": False, "error": "..."}.
    Order: validate project (§2) -> refuse if the team session name is
    already taken (§6, BEFORE touching any worktree) -> create worktrees
    for each member in order, rolling back on partial failure (§3) ->
    _new_state() + _persist() (status="running", 0 rounds -- team-resume
    drives it, unchanged from 6c) -> create the tmux session/windows (§6),
    rolling back (remove all worktrees just created + delete the fresh
    run_id's state dir) if session creation itself fails. Does NOT drive
    the lead loop -- that's still team-resume <run_id>, completely
    unchanged from 6c (a freshly-launched run's status="running", 0 rounds
    is exactly what team-resume already expects).
    """
```

CLI: `team-launch <workdir> --task "..." (--lead <name> | --lead-ollama)
--members a,b,c` — same argument shape as 6c's own `team-start`, deliberately
(copy-pasted validation for `--lead`/`--lead-ollama`/`--members`, reused, not
reinvented). Prints `{run_id, session, worktrees}` as JSON on success.

### 8. `team-stop`

```python
def stop_team(run_id: str) -> dict:
    """
    Unconditional -- works regardless of status (running / blocked_ask_user
    / finished / error / escalated_max_rounds), same "an explicit human
    action always wins" precedent instance_stop() already sets. Kills the
    team-<project> tmux session (if present -- a no-op, not an error, if
    it's already gone). Attempts _remove_worktree() for EVERY teammate
    regardless of an earlier one's outcome (one dirty/errored worktree
    never aborts the rest). If status was non-terminal, marks it "stopped"
    (a new status value, additive to the existing enum) and persists.
    Returns {"session_removed": bool, "worktrees": {agent: "removed"|
    "dirty"|"error"|"absent", ...}}.
    """
```

**What "stopping a team" does and does not stop, stated explicitly (the
coordinator's own four-part question).** In this CLI-only part 1 (no
background thread yet — see "Part 2 preview"), the **driving process** is
whatever foreground `team-start`/`team-resume`/`team-resolve` invocation is
currently running `team_run()` for that `run_id`, if any — `team-stop` does
**not** reach into and interrupt it (there is no cooperative-cancellation
channel yet; that's part 2's job, see below). `team-stop`'s own four
targets: (1) the **team-`<project>` tmux session's windows** — always torn
down; (2) each **teammate's worktree** — attempted, dirty ones left behind,
reported; (3) **any currently in-flight `agent_run()` call's own throwaway
`switchboard-headless-<run_id>` session** — **not** touched by `team-stop`
at all (it's a different tmux session, created and owned entirely by that
`agent_run()` call); (4) **the driving loop itself** — not interrupted.
Practical consequence, disclosed rather than hidden: running `team-stop`
while a `team-start`/`team-resume` process for the same `run_id` is still
active in another terminal is a real footgun in part 1 — recommended
sequence is Ctrl-C the driving process, *then* `team-stop`, not the other
way around. If a delegate call's own worktree is removed out from under a
still-running `agent_run()` call anyway, that call's own already-defensive
"the tmux session/workdir vanished" handling (`app/teams.py:881-887`, "never
optimistically assumed clean") absorbs it as a failed/errored delegation —
not a crash, but not clean either. **Part 2's own background-thread
integration is where this gets closed properly** (a real cancellation
signal the driving thread can observe between rounds and mid-call) — see
"Part 2 preview".

### 9. `sweep_dead_teams()` / `team-reap`

```python
def sweep_dead_teams() -> list[dict]:
    """
    Opportunistic (called at the top of team-launch/team-stop/team-reap,
    same "not a background thread/timer" philosophy _sweep_stale_runs()
    already documents), plus its own dedicated `team-reap` CLI subcommand
    for explicit/scripted use and straightforward testing. For every run_id
    under _leads_root():
      1. status in (running, blocked_ask_user) but the team-<project>
         session is gone (tmux_has() false) -- crash/reboot: mark
         status="error", error naming what was observed, persist. This is
         now terminal (falls into case 2 below).
      2. status in (finished, escalated_max_rounds, error, stopped) AND
         age since updated_at > TEAM_SESSION_STALE_TTL_SECONDS -- sweep:
         kill the session if still present, attempt _remove_worktree() for
         every teammate (best-effort; one failure doesn't stop the rest),
         leave run.json/transcript.jsonl in place (matches
         TEAM_HEADLESS_STALE_RUN_TTL_SECONDS's own precedent -- the state
         *record* persists, only the live session/worktrees are reclaimed).
      3. status == "blocked_ask_user" is NEVER swept by TTL, at any age --
         see "Open questions".
    Returns a list of {run_id, action, detail} for team-reap's own printed
    report. Never raises -- a corrupt/missing run.json for one run_id is
    skipped, not fatal to the sweep of every other run_id.
    """
```

### 10. Engine-name reservation

```python
_RESERVED_ENGINE_NAME_PREFIXES = ("switchboard", "team")
...
if name.startswith(_RESERVED_ENGINE_NAME_PREFIXES):
    return None
```

Same bug class as the existing `switchboard` reservation: an
`engines.d/team.engine` (or `team-anything.engine`) would produce a
single-engine session name `f"{engine}-{project}"` = `f"team-{project}"`
for **any** project — structurally identical to `team-<project>`'s own
team-session name. Reserving the engine-name prefix (not restricting
project names — `NAME_RE` already can't produce a colliding session name on
its own) is the complete, sufficient fix, exactly mirroring
`docs/ADDING_AN_ENGINE.md`'s existing "Reserved name" section, which this
spec extends rather than duplicates.

### 11. New config constants

```python
TEAM_WORKTREE_OP_TIMEOUT_SECONDS = float(os.environ.get("TEAM_WORKTREE_OP_TIMEOUT_SECONDS", "30"))
TEAM_SESSION_STALE_TTL_SECONDS = int(os.environ.get("TEAM_SESSION_STALE_TTL_SECONDS", "86400"))
```

Same declare-once-at-module-level convention as every other `TEAM_*`
constant. `TEAM_SESSION_STALE_TTL_SECONDS` mirrors `UPLOAD_STAGING_TTL_
SECONDS`'s own precedent (`app/app.py`) — a TTL backstop for a resource
that's deliberately *not* torn down in a `finally` (docs/ARCHITECTURE.md
"Upload staging" section, same idiom).

## Affected areas

- `app/teams.py` — `_run_run_user_command()`, `_validate_project_for_team()`,
  `_worktree_path()`/`_create_worktree()`/`_remove_worktree()`,
  `_agent_log_path()`, `_team_session_name()`/`_create_team_session()`,
  `launch_team()`/`stop_team()`/`sweep_dead_teams()`, three new CLI
  subcommands, `_new_state()`'s two additive fields, `team_step()`'s
  delegate branch (worktree + explicit `log_path`), two new config
  constants.
- `app/app.py` — exactly one change: `_RESERVED_ENGINE_NAME_PREFIXES`
  extended from `("switchboard",)`-equivalent to `("switchboard", "team")`.
  No routes, no template/JS, no new import of `app.teams`.
- `config/switchboard.env.example` — `TEAM_WORKTREE_OP_TIMEOUT_SECONDS`,
  `TEAM_SESSION_STALE_TTL_SECONDS`, same commented-out-with-explanation
  style as every existing `TEAM_*` block.
- `docs/ADDING_AN_ENGINE.md` — "Reserved name" section updated to name both
  prefixes.
- New tests: `tests/test_teams_lifecycle.py` (new file), real git repos in
  temp dirs, real tmux (same `TMUX` monkeypatch/real-tmux technique
  `tests/test_teams_headless.py` already established), no mocked git.

## Edge cases

- **Second `team-launch` for the same project while the first is still
  running** — refused at the session-name check, before any worktree is
  touched; the first run's own worktrees/session are untouched.
- **`team-launch` where a previous (stopped-but-dirty) run left a worktree
  behind for one of the requested members** — specific error naming the
  agent and path ("a previous team run's worktree for 'claude' still has
  uncommitted changes at `<path>` — resolve and remove it manually
  (`git worktree remove --force`) before starting a new team"), not a raw
  git "already exists" stderr dump. No worktrees created for this attempt.
- **`--lead <engine>` where that same engine is also in `--members`** —
  allowed (6c's own open question, adopted as settled: no special case).
  Worth stating precisely since it can look like a conflict: the lead's own
  tier-2/3 turns use `state["workdir"]` (no worktree, unchanged from 6c);
  `delegate` calls to that same engine name use its worktree. Two different
  code paths, keyed differently (`_call_lead()` vs. the delegate branch),
  genuinely no conflict.
- **A member named twice in `--members`** — `_create_worktree()`'s
  own "path already exists" check fires on the *second* occurrence within
  the *same* launch attempt (the first occurrence's worktree now exists) —
  degrades to the ordinary worktree-creation-failure path (specific error,
  rollback), not a crash. Not deduplicated silently — a human who typed
  `claude,claude` gets told why, not a silently-halved team.
- **Project path containing a space** (legal per `NAME_RE`) — every
  subprocess call in this spec passes argv elements as a list (no shell
  interpolation anywhere in `_run_run_user_command()`/`_create_team_session
  ()`), so this is exercised directly, not assumed safe by construction.
- **`team-stop` on a `run_id` that doesn't exist** — same "no such run_id"
  message pattern 6c's own `team-status`/`team-resolve`/`team-resume`
  already use for the identical case, not a new shape.
- **`git worktree remove` refusing on untracked-but-not-dirty-per-git's-own-
  tracked-file-definition content** — see "Open questions"; the spec's own
  reading is that `git status --porcelain` being non-empty (tracked OR
  untracked) is what `_validate_project_for_team()` blocks *launch* on, and
  `git worktree remove`'s own refusal (its actual behavior, not assumed) is
  what determines "dirty" on *removal* — these are two different git
  operations' own native behavior, not something this spec re-implements;
  verified against the real `git` binary, not asserted from documentation.
- **A hung `git worktree add`/`remove` (e.g. an index lock held by another
  process)** — bounded by `TEAM_WORKTREE_OP_TIMEOUT_SECONDS`, never hangs
  the launch/stop call indefinitely; escalates TERM-then-kill-session, same
  general shape as `_run_headless_session()`'s own ladder, simpler because a
  well-behaved git process doesn't need the multi-stage version.
- **Host reboot vs. service restart** — indistinguishable from
  `sweep_dead_teams()`'s own perspective (tmux session absent either way);
  handled by the same single code path, not two.
- **A run's `run.json` is corrupt or unreadable** during a sweep pass —
  skipped, logged in the sweep's own returned report, never fatal to
  sweeping every other run_id.

## Acceptance criteria

- [ ] `team-launch` against a real scratch git repo (clean tree, HEAD on a
      real branch) with two real teammates creates: `run.json`
      (`status: "running"`, 0 rounds, `worktrees` populated), one real `git
      worktree` per teammate at `<workdir>.teams/<agent>` each on its own
      `team-<run_id>-<agent>` branch (verified via `git worktree list`
      against the real repo), and a real `team-<project>` tmux session with
      windows `lead`, `<agent1>`, `<agent2>` in that order (verified via
      `tmux list-windows`) — real tmux, real git, not mocked.
- [ ] `team-launch` against a dirty tree / detached HEAD / non-git
      directory each produce their own distinct, specific error message,
      and each leaves **no** worktree, **no** tmux session, and **no**
      `run.json`/state directory behind — verified by inspecting the
      filesystem after the call, not just its return value.
- [ ] `team-launch` twice against the same project (second attempt before
      the first is stopped) is refused before any worktree is touched; the
      first run's worktrees/session are byte-for-byte unaffected by the
      refused second attempt.
- [ ] Attaching to a teammate's window (`tmux capture-pane -t
      team-<project>:<agent> -p`, for test purposes only — never used by
      any code path in this spec to *read* an agent's answer, only by the
      test asserting what a human would see) shows that agent's
      accumulated raw event stream across **at least two separate
      delegations** to it within one real, driven run (`team-resume`) —
      the second delegation's content is visibly appended, not replacing
      the first's.
- [ ] The `lead` window shows `transcript.jsonl`'s content live during a
      real driven run, matching what `_drive_and_report()`'s own existing
      stderr tail shows for the same run.
- [ ] A real delegation writes a file; the file lands inside the teammate's
      own worktree directory, **not** the shared project directory —
      proven by inspecting both directories after the call, and a second
      delegation to the same teammate correctly resumes the same
      `session_id` while still targeting the same worktree (session
      continuity and worktree isolation both hold simultaneously, not
      traded off against each other).
- [ ] `team-stop` on a `running`/`blocked_ask_user` run kills the real tmux
      session (`tmux_has()` false afterward) and, given one teammate with a
      real uncommitted change written into its worktree and one clean,
      removes the clean one entirely (directory gone, `git worktree list`
      no longer shows it, its branch still present in `git branch --list`)
      and leaves the dirty one fully intact on disk, reported by name —
      real git behavior, not asserted from documentation.
- [ ] `team-stop` on an already-`finished` run still tears down the
      session/attempts worktree cleanup (unconditional, not status-gated).
- [ ] Simulated crash (`tmux kill-session -t team-<project>` on a
      `running` run, outside this codebase's own control) followed by
      `team-reap`: status flips to `"error"` naming the vanished session;
      with `TEAM_SESSION_STALE_TTL_SECONDS` monkeypatched to `0`, a
      **second** `team-reap` pass then sweeps its worktrees the same way
      `team-stop` would.
- [ ] A `blocked_ask_user` run is never swept by `team-reap` regardless of
      `TEAM_SESSION_STALE_TTL_SECONDS` (proven with the TTL forced to `0`)
      — its session and worktrees are still present after a reap pass.
- [ ] `agent_run()`'s and `team_step()`'s existing (no-worktree,
      no-explicit-`log_path`) behavior is byte-for-byte unchanged — the
      full `tests/test_teams_headless.py` and `tests/test_teams_lead.py`
      suites pass with zero modification, and 6c's own bare `team-start`
      (no `team-launch`) still runs exactly as before, against
      `state["workdir"]` directly.
- [ ] An `engines.d/team.engine` (or `team-anything.engine`) file is
      silently ignored by `load_engines()`, exactly like the existing
      `switchboard`-prefix case — proven with a real scratch `.engine`
      file, not asserted from the reservation list alone.
- [ ] Every file a dashboard window needs to read (`agents/<agent>.jsonl`,
      `transcript.jsonl`, their containing directories) is actually
      readable by RUN_USER's tmux pane under a **realistic strict umask**
      for the SVC_USER-owned writing process — reusing 6a's own precedent
      test technique (`test_run_sh_and_prompt_file_are_world_readable_
      under_a_strict_umask`), not merely asserted from the chmod calls'
      presence in the diff.
- [ ] Full test suite green, several consecutive runs; `git diff --stat --
      app/app.py` limited to the one-line reservation-tuple change.

## Test plan

Mirrors `tests/test_teams_headless.py`'s own structure (pure-logic tests
separate from real-tmux/real-git tests), extended for git:

**Pure logic, no subprocess, no tmux, no git:** `_worktree_path()`;
`_agent_log_path()`; the rollback-ordering logic in `launch_team()` given a
monkeypatched `_create_worktree()` that fails on the Nth call;
`sweep_dead_teams()`'s status-transition decisions given hand-constructed
`run.json` fixtures at every relevant status/age combination (mirrors 6c's
own `InProgressCrashRecoveryPureTests` technique).

**Real git, temp directories, no tmux:** `_validate_project_for_team()`
against a real `git init`'d temp repo in each of the four states (clean,
dirty-tracked, dirty-untracked-only, detached HEAD) and a genuinely
non-git directory; `_create_worktree()`/`_remove_worktree()` against a real
repo, including the dirty-refusal case and the branch-survives-removal
claim, both verified via real `git` commands, not assumed.

**Real tmux, real git, real subprocess (the bulk of the risk surface,
correspondingly the bulk of the test budget) — same `TMUX` monkeypatch/
real-session technique `tests/test_teams_headless.py` already
established:** `launch_team()` end to end against a real scratch git repo
(session/windows/worktrees all real, asserted via `tmux list-windows`/
`git worktree list`, not mocked); a real `team-resume`-driven run with a
stub lead that delegates twice to the same teammate, asserting the
dashboard window's accumulated content and the worktree-vs-shared-tree file
placement; `stop_team()`'s three-outcome worktree removal against real
dirty/clean worktrees; the crash-simulation + `team-reap` sequence with a
real `tmux kill-session`; the permission test against a strict umask,
reusing 6a's own fixture pattern.

## Open questions

### Settled by the user (2026-08-13) — build to these, do not reopen

- **Dirty-tree check blocks on ANY non-empty `git status --porcelain`
  output, untracked files included.** Chosen over the looser
  tracked-modifications-only reading. Rationale accepted as written: it is
  the safer default and loosening a too-strict check later is lower risk
  than a team silently working from an incomplete view of what the human
  actually has on disk. This spec already defaults to this — no change.
- **`blocked_ask_user` runs are NEVER swept by TTL, at any age.** No
  backstop TTL this cycle. Rationale accepted as written: "how long is
  abandoned?" is a product judgment with no operational signal behind it
  yet, and an invented number risks reclaiming a question someone still
  intends to answer. Revisit only with real usage data. This spec already
  defaults to this — no change.
- **6d is split into two build cycles.** This spec is part 1 (worktree +
  tmux dashboard lifecycle, CLI/backend only). Part 2 — web routes, the
  background driving thread, and `install.sh --with-ollama` — is its own
  later spec, previewed below. Rationale accepted: part 1 alone is
  real-git + real-tmux + a new self-heal contract, comparable in size and
  risk to any one of 6a/6b/6c, and folding HTTP routes plus threading into
  the same dispatch is the shape that produced this story's largest
  defects.

### Still open (not needed for this cycle)

- **Worktree base ref: always the project's current `HEAD` at launch time,
  never a specific named branch the operator picks.** Reasonable given
  "one team, one snapshot of the project" — flagging only because a future
  request ("start a team against a specific feature branch, not whatever's
  currently checked out") would need this revisited; not needed for this
  cycle's own acceptance criteria.

## Part 2 preview (not this cycle's scope)

For context only — this is its own follow-on spec, not built or reviewed
as part of this cycle:

- `app/app.py` imports `app.teams` for the first time. New routes:
  `POST /projects/<name>/team/start` (task text in the body; a **default**
  composition — the configured Ollama model as lead if `TEAM_LLM_BASE_URL`/
  `TEAM_LLM_MODEL` are set, else the first tier-2 `engines.d` engine, every
  other headless-eligible engine as teammates — since the actual
  lead/member **picker** is 6e's own deliverable, not duplicated here),
  calling `launch_team()` then starting `team_run()` on a `threading.Thread`
  (per 6c's own docstring: "so 6d can later run it off a background thread
  with zero change") tracked in an in-memory dict analogous to `_ttyd_procs`
  /`_code_procs`; `POST /projects/<name>/team/stop` calling `stop_team()`,
  additionally setting a `threading.Event` the driving thread checks between
  rounds and an `agent_run()`-level `cancel_event` kwarg (new, additive,
  default `None`) so an in-flight delegation is SIGTERM'd promptly instead
  of waited out — this is what actually closes the "four things don't stop
  together" gap part 1 explicitly leaves open.
- `_reap_dead_state()` gains a call to `sweep_dead_teams()`, plus a check
  against the in-memory thread-tracking dict (a driving thread that's not
  `.is_alive()` while `run.json` still says `"running"` is the
  service-restart-mid-run case, the same already-accepted class of gap
  `_session_urls`/ttyd's own tables already have).
- A minimal per-project "Start team"/"Stop team" control in the page
  template — a task-text prompt plus the two buttons, a coarse status label
  (idle/running/blocked/finished/error) — deliberately **no** lead/member
  picker (6e) and **no** live feed (6f).
- `install.sh --with-ollama` — prompts for an existing Ollama endpoint URL
  and model name, validates reachability (e.g. the OpenAI-compatible
  `/v1/models` endpoint, or Ollama's own `/api/tags`) and that the named
  model is actually present, writes `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` via
  the existing `set_env` idiom, installs nothing locally (per
  `docs/story.md` §2.5, settled) — refuses to write config for an endpoint
  it can't reach, same "fail the start, don't write config that fails
  later" discipline this spec's own `team-launch` already applies.

## Risk / rollback notes

Everything in this spec is new code behind three new CLI subcommands, two
new `_new_state()` fields (both `None`/`{}`-default, existing callers
unaffected), one new optional `agent_run()` call-site change inside
`team_step()`'s own delegate branch (additive fallback to existing
behavior when no worktree exists), and one new tuple entry in
`app/app.py`'s engine-name reservation. No existing route, session
lifecycle, or CLI behavior changes for any caller that doesn't use
`team-launch`. Rollback is `git revert` of this cycle's commit(s); no
schema/data migration. A partially-tested-out worktree/session from this
feature is inert once reverted (nothing left running references it) and
can be cleaned up by hand (`git worktree remove --force`, `tmux
kill-session`) if desired, same as any other manually-inspected leftover
state in this codebase.
