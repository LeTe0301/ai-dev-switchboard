# Spec: Team session lifecycle, part 2a — web routes + background driving thread + cooperative cancellation (sub-spec 6d, part 2a of 2)

## Split rationale (read this first)

`docs/story.md` §5's own 6d entry, and part 1's own "Part 2 preview", bundle
four things into "part 2": HTTP routes + a background thread + cooperative
cancellation, `_reap_dead_state()` wiring, a minimal per-project Start/Stop
template control, and `install.sh --with-ollama`. Per this role's own
"load-balanced decomposition" duty (and per this task's own explicit
instruction to judge the split, not just accept the brief's framing):

**This spec covers HTTP routes + the background thread + cooperative
cancellation + `_reap_dead_state()` wiring + the minimal template
control.** These five are one coherent unit — the template control is a
few lines of HTML/JS calling the two new routes and reading one new
`/status` field, not a picker or a feed, so splitting it from the routes
it calls would add a handoff for no real isolation benefit (the same
reasoning part 1 used to justify bundling its own CLI subcommands with the
backend they drive). `_reap_dead_state()` wiring is the *self-heal* half
of the exact same in-memory-thread-table design the routes introduce, not
a separable concern.

**`install.sh --with-ollama` is its own follow-on spec (part 2b), not
built or reviewed in this cycle.** It is bash-only, has zero code
dependency on this cycle's Python/threading work (it only ever writes
`TEAM_LLM_*` env vars), and this repo has direct, on-point precedent for
splitting exactly this shape apart: `git log` — "2c part 2a" (switchboard-
side deploy dispatch, application code) vs. "2c part 2b" (`install.sh
--with-deploy-target`, an installer flag) were two separate cycles for the
identical reason. A "Part 2b preview" section at the end of this document
carries forward the archaeology already done on `install.sh` (the
`set_env`/`prompt`/`curl`-availability idioms, the exact insertion point)
so the next cycle doesn't have to redo it.

Recommend the orchestrator run these as two sequential build cycles: this
spec (part 2a) first, part 2b after.

## Summary

`app/app.py` imports `app/teams.py` for the first time (the reverse
direction already exists — `teams.py` has imported `TMUX`/`tmux_has`/
`load_engines` from `app` since 6a). Two new POST routes,
`/projects/<name>/team/start` and `/projects/<name>/team/stop`, wire
`launch_team()`/`stop_team()` (both already built and reviewed in part 1)
into the web UI: `start` computes a **default** team composition (no
picker — 6e's job) and runs `team_run()` on a daemon `threading.Thread`,
tracked in a new in-memory table (`_team_threads`, keyed by project name,
same "in-memory, lost on restart, self-heals" shape as `_ttyd_procs`/
`_session_urls`); `stop` synchronously calls the already-unconditional
`stop_team()` and, if a live thread is tracked, signals a
`threading.Event` so an in-flight round is interrupted rather than waited
out. `agent_run()`, `_run_headless_session()`, `_call_lead()`,
`team_step()`, and `team_run()` each gain one small, additive,
default-`None` `cancel_event` kwarg — the actual mechanism that closes
part 1's own disclosed "four things don't stop together" gap for the
*driving loop and in-flight delegation* (the tmux dashboard session and
worktrees were already closed in part 1). `_reap_dead_state()` gains a
throttled call to `sweep_dead_teams()` plus a new, narrower self-heal
check for a run whose `run.json` says `"running"` but whose driving thread
is gone from `_team_threads` (the service-restart case). A minimal
per-project template control (task-text box, Start/Stop buttons, a coarse
status label) is added, reusing the existing `deploy`-row rendering
pattern as its closest precedent, not the checkbox-toggle pattern (a team
isn't a simple on/off).

**A real, previously-undocumented gap found by codebase archaeology for
this spec, not by guessing:** `install.sh` currently copies only
`app/app.py` to `$INSTALL_DIR/app.py` (`install.sh:214`) — `app/teams.py`
is **never copied** by the installer today, since part 1 never needed it
there (CLI-only, run from a repo checkout). The moment `app.py` gains
`import teams`, a real production install (systemd running
`$INSTALL_DIR/app.py`) would crash on startup with `ModuleNotFoundError`
unless `install.sh` also copies `teams.py`. This is this cycle's scope,
not part 2b's — see "Proposed approach" §6 and "Affected areas".

## Goals

- `POST /projects/<name>/team/start` launches a team for an existing
  project with a task description and a **deterministic, config-driven
  default composition** — no lead/member picker (6e).
- `POST /projects/<name>/team/stop` cleanly tears down a team regardless
  of whether the process that launched it (this app.py instance) is still
  the one running, and regardless of whether a background thread for it
  currently exists in memory (restart-safe, re-derived from `run.json`,
  never dependent on `_team_threads` surviving).
- A `threading.Event`, checked at three well-defined points (between
  rounds; immediately after the lead's own turn returns, before its action
  executes; immediately after a delegate call returns, before its outcome
  is recorded) plus a new `agent_run(..., cancel_event=...)` kwarg that
  SIGTERM-then-escalates an in-flight subprocess exactly the way a
  timeout already does — so **stopping a team actually stops the driving
  loop and any in-flight delegation or lead turn**, closing the specific
  gap part 1's spec explicitly and honestly left open ("Part 2's own
  background-thread integration is where this gets closed properly").
- A service restart while a web-launched team is running is detected and
  reconciled the next time `/status` is polled — the coarse status label
  never lies forever, and `/team/stop` still works correctly even though
  the in-memory thread table was wiped.
- Zero new privileged surface: the background thread only ever calls
  `launch_team()`/`stop_team()`/`team_run()`, all of which already route
  every RUN_USER-crossing operation through the existing `TMUX` constant
  (part 1, unchanged, unmodified by this spec).
- `app.py` actually starts, with `import teams` resolving correctly at
  the exact point in `app.py`'s own top-level execution this spec
  specifies — not merely `py_compile`-clean (see "Proposed approach" §1
  and "Edge cases" for why compile-cleanliness cannot catch this
  particular defect class).

## Non-goals

- **Lead/member picker (6e)** — `/team/start` always uses
  `default_team_composition()` (§2 below); there is no way to override
  lead/members via this route this cycle.
- **Overwatch feed / escalation inbox UI (6f)** — the template control adds
  a coarse status label only (`idle`/`running`/`blocked`/`finished`/
  `error`), not a rendered event timeline. `tmux attach`/`capture-pane`
  (for a human, not for any code path — unchanged from part 1) remains the
  only way to see live raw output until 6f.
- **`install.sh --with-ollama`** — part 2b, see "Part 2b preview".
- **A team-size cap on the default composition.** Only three engines ship
  today (`claude`/`codex`/`aider`), so "every other headless-eligible
  engine" is at most two teammates by default — not a realistic runaway
  case yet. Revisit if the shipped engine count grows materially.
- **Automatic deploy off a team's work, or automatic worktree merge-back.**
  Both already settled non-goals (`docs/story.md` §3, part 1's own
  "Non-goals") — untouched.
- **Any change to `team_step()`'s tool dispatch shape, the four-tool
  schema, the tier adapters, or grounding.** This spec only threads
  `cancel_event` through existing call sites; it adds no new tool, no new
  business rule, and touches no grounding code.
- **Caching/indexing run records for `/status` scale.** `run.json` records
  are never deleted (matches `TEAM_HEADLESS_STALE_RUN_TTL_SECONDS`'s own
  precedent — part 1), so the number of run directories under
  `_leads_root()` grows without bound over an install's lifetime. This
  spec's `latest_run_for_project()` (§4) is an O(all runs ever) scan per
  call, matching `sweep_dead_teams()`'s own already-accepted "small N —
  just iterate it" scale assumption (`_load_gitea_repo_map()`'s own
  precedent). Acceptable at this project's single-operator homelab scale;
  revisit only if that assumption stops holding.

## Background / current state

From part 1 (`app/teams.py`, current size 3661 lines) and `app/app.py`
(3094 lines):

- `launch_team(workdir, task, lead, members, max_rounds=None) -> dict` and
  `stop_team(run_id) -> dict` (`app/teams.py:3061`, `:3134`) — both fully
  built, reviewed (3 rounds), and CLI-driven only today. `stop_team()` is
  explicitly documented as **unconditional** — "works regardless of
  status... same 'an explicit human action always wins' precedent
  `instance_stop()` already sets" — and already ownership-checks the
  dashboard session via `_kill_team_session_if_owned()` before touching
  it, so a second, newer run for the same project is never destroyed by a
  stale one. Neither function touches the driving loop or any in-flight
  `agent_run()` call — that gap is explicitly this spec's job.
- `sweep_dead_teams()` (`:3205`) — self-heals the tmux-session-vs-`run.json`
  mismatch (case 1: `status in (running, blocked_ask_user)` but the
  dashboard session is gone → `status="error"`) and TTL-sweeps terminal
  runs' sessions/worktrees (case 2), never sweeping `blocked_ask_user`
  (case 3). **Not yet called from anywhere in `app.py`** — part 1's own
  scope stopped at the CLI's `team-reap` subcommand.
- `team_run(state) -> dict` (`:2608`) drives `team_step()` in a loop until
  `finished`/`blocked_ask_user`/`escalated_max_rounds`; its own docstring
  states "nothing here assumes a foreground TTY, so a later sub-spec can
  run it off a background thread with zero change" — this spec is that
  later sub-spec, and it is *not* zero change: a real cancellation channel
  is exactly what's missing for "stop" to mean anything mid-flight.
- `agent_run(engine, workdir, prompt, *, session_id=None,
  timeout=TEAM_HEADLESS_TIMEOUT_SECONDS, log_path=None, schema=None)`
  (`:937`) and `_run_headless_session(...)` (`:835`) — already implement a
  full TERM→KILL→`kill-session` escalation ladder (`TEAM_HEADLESS_
  KILL_GRACE_SECONDS`, default 10s per stage) triggered today by exactly
  one condition (`(now - start) >= timeout`) via `_send_signal()`
  (`:792`, the cross-UID-signal-via-a-throwaway-tmux-session trick, TMUX-
  only, no new privilege). Adding a **second** trigger condition
  (`cancel_event.is_set()`) that reuses the identical ladder is additive,
  not a redesign — see "Proposed approach" §1.
- `team_step()`'s delegate branch (`:2510`) and `_call_lead()` (`:2397`)
  both call `agent_run()` — the delegate branch for a teammate (already
  worktree-scoped, per part 1), `_call_lead()` for a tier-2/3 lead's own
  turn (against `state["workdir"]`, unchanged, per part 1's own explicit
  non-goal). Tier 1 (Ollama) is a direct `urllib` HTTP call
  (`_tier1_call_with_retry()`), not a subprocess — it has no PID to
  SIGTERM; see "Edge cases" for how this spec bounds a stop request
  arriving mid-tier-1-call instead.
- `_new_state()`'s `project_name`/`worktrees` fields (part 1) mean a run
  launched via `launch_team()` always has a real `project_name` — this
  spec's new `latest_run_for_project()` (§4) relies on that field, exactly
  as `sweep_dead_teams()` already does.
- `app/app.py:191`, `TMUX = ["sudo", "-u", RUN_USER, "/usr/bin/tmux"]` —
  the only sanctioned RUN_USER crossing point
  (`docs/ARCHITECTURE.md`). This spec adds **no** new privileged call of
  any kind — the background thread only calls functions part 1 already
  built and reviewed.
- `app/app.py`'s existing in-memory-state precedent
  (`_ttyd_procs`/`_code_procs`/`_session_urls`, `:570-612`, `:1282`) and
  `docs/ARCHITECTURE.md` "In-memory state and its one sharp edge" — lost
  on a service restart, self-healed via `_reap_dead_state()` (called on
  every `/status`) once the underlying resource's own true state is
  checked directly. This spec's `_team_threads` is the same pattern,
  generalized to a background thread instead of a `subprocess.Popen`
  handle — not a new kind of gap.
- `app/app.py`'s `_gitea_poll_if_due()` (`:787`) — the **only** existing
  precedent in this codebase for throttling opportunistic work inside
  `_reap_dead_state()`'s own call graph (a module-level
  `Lock`+last-run-timestamp, double-checked after acquiring the lock).
  Reused verbatim in shape for `sweep_dead_teams()`'s own new wiring —
  see "Proposed approach" §5's own rationale for why this matters (an
  unthrottled `sweep_dead_teams()` call on every `/status` poll would
  repeatedly re-attempt a real `git worktree remove` subprocess call,
  bounded by `TEAM_WORKTREE_OP_TIMEOUT_SECONDS`, 30s default, against any
  currently-dirty worktree on **every** poll from **every** open browser
  tab — a real, previously-undisclosed latency/resource landmine found
  by reasoning about call frequency, not by guessing).
- `install.sh:214`, `cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"` —
  `teams.py` is not copied. See "Summary" and "Proposed approach" §6.
- `tests/test_deploy_dispatch.py`'s `DeployEndpointTests` — the
  established real-`ThreadingHTTPServer`+real-`urllib.request.urlopen()`
  end-to-end HTTP test harness this spec's own route tests reuse
  (`cls.server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)`),
  and `InstallShDeployMapBlockTests`' block-extraction technique for
  install.sh, both precedent, not reinvented.

## Proposed approach

### 1. `app.py` imports `teams` — the one genuinely fragile line in this diff

`teams.py` already does `from app import TMUX, tmux_has, load_engines`
(`teams.py:54`) at its own **module level** — i.e., the instant anything
imports `teams`, Python needs `TMUX`/`tmux_has`/`load_engines` to already
exist as attributes of the (possibly still-initializing) `app` module.
When `app.py` itself is the one doing `import teams`, this is a real
circular import, and it only resolves correctly if `import teams` is
placed in `app.py`'s own top-level code **after** all three of those are
already defined:

```python
TMUX = ...            # app.py:191
def load_engines(): ...   # app.py:397 (defines the name at module scope
                           # once the def statement executes)
def tmux_has(...): ...    # app.py:1269
def active_engine(...): ...  # app.py:1274 — last of the group, natural anchor
import teams               # <- must go here or later, never earlier
```

Placed immediately after `active_engine()`'s definition (`app.py:1274-
1275`) and before `_session_urls`'s own declaration (`:1282`) — the exact
neighborhood this spec's own new in-memory state (`_team_threads`, §3)
belongs in anyway, matching `docs/ARCHITECTURE.md`'s existing grouping of
in-memory state. No `sys.path` manipulation needed on `app.py`'s side —
CPython already puts a directly-run script's own directory at
`sys.path[0]`, and `teams.py` lives in the same directory as `app.py` both
in a repo checkout (`app/`) and in a real install (`$INSTALL_DIR`, once
§6 below lands).

**Why `py_compile` cannot catch a wrong placement**: `python3 -m
py_compile app.py` only parses and byte-compiles the file — it never
executes top-level statements, so a circular-import ordering defect is
invisible to it (this is exactly the check this whole story's own
"Verification status" tables have used as their first line, every round,
for every cycle so far — worth naming explicitly since it's a real, novel
gap in that checklist's own coverage, not present before this cycle
because nothing previously imported `teams` from inside `app.py`). The
correct check is an actual import/execution: `python3 -c "import sys;
sys.path.insert(0, 'app'); import app"` (or, more realistically, actually
starting the `ThreadingHTTPServer` and hitting `/status`) — see
"Acceptance criteria".

### 2. `default_team_composition()` — the deterministic, config-driven default

New, in `app/teams.py`, built entirely on `roster()`/`_lead_tier_for_
engine()`/`_schema_flag_config_error()` (all already built in 6c) — no
new engine-iteration logic:

```python
def default_team_composition() -> dict:
    """
    {"ok": True, "lead": {...}, "members": [...]} or
    {"ok": False, "error": "..."}. Backlog item 6d part 2a -- the actual
    lead/member PICKER is 6e's job; this is the deterministic default the
    web route uses until then.

    Lead, in priority order:
      1. The configured Ollama tier-1 model, if TEAM_LLM_BASE_URL and
         TEAM_LLM_MODEL are both set (docs/story.md 3, "Ollama-backed
         local model is the default").
      2. Else the first (sorted by name) headless-eligible engines.d entry
         that is tier 2 (schema-constrained) with no schema_flag_error.
      3. A tier-3 (prose-parse) engine is NEVER selected as the default
         lead -- SETTLED BY THE USER 2026-08-13, against this spec's own
         original recommendation to allow it. If the only headless-eligible
         engines are tier 3, refuse:
           {"ok": False, "error": "only a tier-3 (prose-parse, least
            reliable) lead is available -- configure TEAM_LLM_BASE_URL/
            TEAM_LLM_MODEL, or add a tier-2 (schema-capable) engine to
            engines.d. The CLI's --lead can still select a tier-3 lead
            explicitly."}
         Rationale, recorded so it is not relitigated: tier 3 is the least
         reliable adapter, this route has NO picker (6e) so the operator
         cannot see or override what was chosen, and the UI surfaces only a
         coarse idle/running/blocked/finished/error label -- so a silently
         degraded lead would be genuinely hard to diagnose. The lead loop's
         own malformed-output retry budget would absorb the failures
         quietly. Refusing is loud, and names the two concrete fixes.
         Note this makes the DEFAULT stricter, not the system: 6c's CLI
         `--lead` still accepts a tier-3 lead as an explicit opt-in, and
         that path is unchanged.
      4. Else {"ok": False, "error": "no roster member is available to
         lead a team -- configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL or add
         a headless-eligible engine to engines.d"}.

    Members: every OTHER headless-eligible engines.d entry (an Ollama
    lead isn't itself an engines.d entry, so nothing is excluded from
    members when the lead is Ollama). If that list is empty --
    dependent on 6c's own settled "lead may also be a member, no special
    case" ruling being irrelevant here since the DEFAULT deliberately
    excludes the lead from its own member list, not a conflict with 6c's
    CLI, which still allows an explicit --lead also in --members -- returns
    {"ok": False, "error": "only one headless-eligible engine ('<name>')
    is configured and it was selected as lead -- add another engine to
    engines.d or configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL to free it up
    as a teammate"}.
    """
```

### 3. In-memory thread table (`app.py`)

```python
# Background team-run threads (backlog item 6d part 2a). Keyed by PROJECT
# NAME -- at most one live team per project by construction (launch_team()'s
# own session-name collision check, part 1) -- same "in-memory, lost on
# restart, self-heals via _reap_dead_state()" tradeoff as _ttyd_procs/
# _code_procs above (docs/ARCHITECTURE.md "In-memory state and its one
# sharp edge").
_team_threads: dict[str, dict] = {}   # name -> {"run_id", "thread", "cancel_event"}


def _run_team_in_background(name: str, run_id: str, cancel_event: threading.Event) -> None:
    """
    Spawned by the /team/start route, daemon thread, same "return fast, do
    the real work off the request thread" idiom _gitea_sync_bg()/
    _generate_description_bg() already establish. Loads a fresh state dict
    (never trusts a stale local var) and drives it to completion via
    team_run(state, cancel_event=cancel_event) -- see docs/spec.md
    "Cooperative cancellation" for what that actually stops. team_run()
    is documented "never raises", but wrapped in try/except Exception
    anyway (this story's own repeated lesson: a "never" claim elsewhere in
    this codebase has been wrong before -- see docs/test-review.md's
    Finding #3/#4 for 6d part 1) -- an unexpected exception marks the run
    "error" via teams.mark_run_error() rather than silently vanishing the
    thread with run.json stuck on "running" forever.

    Ownership-checked removal from _team_threads on the way out (mirrors
    part 1's own _kill_team_session_if_owned() lesson, applied proactively
    here rather than rediscovered in review): only pops _team_threads[name]
    if its own run_id still matches -- guards against a subsequent
    stop-then-relaunch having already replaced the entry with a NEWER
    run's thread before this old thread's own cleanup runs.
    """
    try:
        state = teams._load_state(run_id)
        teams.team_run(state, cancel_event=cancel_event)
    except Exception as e:
        try:
            teams.mark_run_error(run_id, f"team run failed with an unexpected error: {e}")
        except FileNotFoundError:
            pass
    finally:
        entry = _team_threads.get(name)
        if entry is not None and entry.get("run_id") == run_id:
            _team_threads.pop(name, None)
```

### 4. `latest_run_for_project()` (`app/teams.py`)

```python
def latest_run_for_project(project_name: str) -> dict | None:
    """
    The persisted state dict of the most-recently-updated run recorded for
    this project_name, or None if no run has ever been launched for it
    (backlog item 6d part 2a). At most one run can be non-terminal
    (status in running/blocked_ask_user) at a time -- launch_team()'s own
    session-name collision check (part 1) -- so when a live run exists it
    is always also the most recent; callers that only care about a LIVE
    run check `.get("status") in ("running", "blocked_ask_user")` on the
    result rather than needing a separate function. Same O(all runs under
    _leads_root()) scan idiom sweep_dead_teams() already uses (docs/spec.md
    "Non-goals" -- accepted at this project's scale). An unreadable/corrupt
    run.json for one run_id is skipped, not fatal, matching sweep_dead_
    teams()'s own discipline.
    """
```

Used by all three of: the `/status` route's coarse label (§5, called on
every poll — freshness matters, deliberately **not** throttled, see §5's
own rationale for why this is the opposite call than `sweep_dead_teams()`
gets), the `/team/stop` route (find the run_id to act on), and
`_reap_dead_state()`'s new orphan check (§5).

### 5. Route handlers (`app.py` `do_POST`, matching the existing `parts[0]==...`
dispatch shape verbatim)

```python
elif parts[0] == "projects" and len(parts) == 4 and parts[2] == "team" and parts[3] == "start":
    name = parts[1]
    if name not in instance_names():
        return self._json({"error": "unknown project"}, 404)
    task = (body.get("task") or "").strip()
    if not task:
        return self._json({"error": "a task description is required"}, 400)
    comp = teams.default_team_composition()
    if not comp["ok"]:
        return self._json({"error": comp["error"]}, 400)
    workdir = os.path.join(PROJECTS_DIR, name)
    result = teams.launch_team(workdir, task, comp["lead"], comp["members"])
    if not result["ok"]:
        return self._json({"error": result["error"]}, 400)
    cancel_event = threading.Event()
    t = threading.Thread(target=_run_team_in_background,
                         args=(name, result["run_id"], cancel_event), daemon=True)
    _team_threads[name] = {"run_id": result["run_id"], "thread": t, "cancel_event": cancel_event}
    t.start()
    self._json({"ok": True, "run_id": result["run_id"], "session": result["session"],
               "lead": comp["lead"], "members": comp["members"]})
elif parts[0] == "projects" and len(parts) == 4 and parts[2] == "team" and parts[3] == "stop":
    name = parts[1]
    if name not in instance_names():
        return self._json({"error": "unknown project"}, 404)
    run = teams.latest_run_for_project(name)
    if run is None or run["status"] not in ("running", "blocked_ask_user"):
        return self._json({"ok": True, "message": "no team currently running for this project"})
    entry = _team_threads.get(name)
    if entry is not None and entry.get("run_id") == run["run_id"]:
        entry["cancel_event"].set()
    result = teams.stop_team(run["run_id"])
    self._json({"ok": True, "session_removed": result["session_removed"],
               "worktrees": result["worktrees"]})
```

`/team/stop` calls `stop_team()` **synchronously within the request** —
matching `deploy_run()`'s own precedent (a POST route that blocks on a
bounded, real operation), not `_gitea_sync_bg()`'s "return fast, work off
the request thread" precedent (that one exists specifically because it
runs automatically on every `/status` poll; `/team/stop` is a one-shot,
human-initiated action, same category as `deploy`). It does **not** wait
for `_run_team_in_background`'s own thread to finish — that thread winds
down asynchronously once it observes `cancel_event` (bounded by the
escalation ladder, up to ~20s worst case for a genuinely unresponsive
delegate — see "Edge cases"), self-removing from `_team_threads` via its
own ownership-checked `finally` block.

`/status`'s existing per-instance dict (`do_GET`, `:2950`) gains one new,
**always-present** field (unlike `deploy`/`gitea_sync`, which are only
attached when configured — a team has no such prerequisite, matching
`on`/`engine` being always-present instead):

```python
run = teams.latest_run_for_project(n)
team_status = ("idle" if run is None else
              {"running": "running", "blocked_ask_user": "blocked",
               "escalated_max_rounds": "blocked", "finished": "finished",
               "error": "error", "stopped": "idle"}.get(run["status"], "idle"))
inst["team"] = {"status": team_status, "run_id": run["run_id"] if run else None}
```

**Throttling `sweep_dead_teams()`, NOT this per-project lookup.** Reusing
`_gitea_poll_if_due()`'s exact idiom (`Lock` + last-run timestamp, double-
checked after acquiring the lock — `app.py:783-811`), add:

```python
_team_reap_lock = threading.Lock()
_team_reap_last_at = 0.0

def _team_reap_if_due() -> None:
    global _team_reap_last_at
    if time.time() - _team_reap_last_at < TEAM_REAP_POLL_INTERVAL_SECONDS:
        return
    if not _team_reap_lock.acquire(blocking=False):
        return
    try:
        if time.time() - _team_reap_last_at < TEAM_REAP_POLL_INTERVAL_SECONDS:
            return
        _team_reap_last_at = time.time()
        teams.sweep_dead_teams()
        for name in instance_names():
            run = teams.latest_run_for_project(name)
            if run is None or run["status"] != "running":
                continue
            entry = _team_threads.get(name)
            if entry is not None and entry.get("run_id") == run["run_id"] and entry["thread"].is_alive():
                continue
            teams.mark_run_error(run["run_id"],
                                 "no driving thread found for this run (service restart?) -- "
                                 "stop this team, then start a new one")
    finally:
        _team_reap_lock.release()
```

`TEAM_REAP_POLL_INTERVAL_SECONDS` (new constant, default `"60"`, same
declare-once-at-module-level convention every other interval constant in
this file already uses) — deliberately **independent** of
`GITEA_POLL_INTERVAL_SECONDS` (different domain, different tuning
rationale), placed next to it. Called from `_reap_dead_state()` alongside
its existing sweeps. `latest_run_for_project()` itself (used by `/status`'s
own per-project label above) stays **unthrottled** — a fresh read on every
poll is cheap (one O(all-runs) scan, no subprocess calls) and freshness
matters there (a team started seconds ago must not still show "idle");
what gets throttled is specifically the write-and-subprocess-heavy sweep,
matching exactly what `_gitea_poll_if_due()` already throttles for the
identical reason (avoid hammering a real subprocess/network call on every
poll from every open tab).

### 6. `install.sh` — copy `teams.py` alongside `app.py` (unconditional)

One line, immediately after the existing copy (`install.sh:214`):

```bash
cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"
cp "$REPO_DIR/app/teams.py" "$INSTALL_DIR/teams.py"
```

Unconditional (not gated behind any flag) — the moment `app.py` imports
`teams` at module load, a real install is broken without this line,
regardless of whether the operator ever configures `TEAM_LLM_*` or uses
`--with-ollama`. Matches the existing unconditional `engines.d/*.engine`
copy loop immediately below it in the same "-- App + engines --" block.

### 7. Cooperative cancellation — the mechanism, precisely

**`agent_run(..., cancel_event: threading.Event = None)`** — additive
kwarg, default `None` (every existing caller — the `run` CLI subcommand,
every 6a/6c/6d-part-1 test — byte-for-byte unaffected). Passed straight
through to `_run_headless_session(..., cancel_event=cancel_event)`.

**`_run_headless_session(..., cancel_event: threading.Event = None)`** —
one new `if` branch in the existing escalation `if/elif` chain
(`app/teams.py:911-925`), reusing the identical TERM→KILL→`kill-session`
ladder the timeout path already drives, just with a second trigger:

```python
if escalation is None and cancel_event is not None and cancel_event.is_set():
    cancel_reason = "stopped"
    _send_signal(session, pid, "TERM")
    escalation, stage_sent_at = "term", now
elif escalation is None and (now - start) >= timeout:
    cancel_reason = "timeout"
    ...  # unchanged
```

No change to the KILL/`kill-session` stages, `_finish()`, or the
`cancelled`/`reason` classification logic — a SIGTERM'd process still
exits 143, still lands in `_SIGNAL_EXIT_NUMBERS`, still gets `cancelled=
True` with `reason=cancel_reason` (now `"stopped"` instead of `"timeout"`/
`"external"`, a third value in an existing three-way — no new field, no
new shape). Checked on the loop's own existing `TEAM_HEADLESS_POLL_
SECONDS` cadence (0.5s default) — cancellation latency is bounded by that
interval, negligible next to the escalation ladder's own ~20s worst case.

**`_call_lead(state, system, round_context, *, cancel_event=None)`** —
additive kwarg, threaded into its own two `agent_run()` call sites (tier
2 and tier 3 — the lead's own turn, unchanged from part 1 in every other
respect, still runs against `state["workdir"]`, never a worktree). Tier 1
(the Ollama HTTP call) has no subprocess to signal — `cancel_event` is
accepted but unused in that branch; see "Edge cases" for the resulting,
disclosed, bounded latency.

**`team_step(state, *, cancel_event=None)`** — additive kwarg, two new
checkpoints, both additive (when `cancel_event is None`, behavior is
byte-for-byte the existing 6c/part-1 shape):

1. Immediately after `_call_lead()` returns, **before** any of the
   existing transport-error/malformed/business-rule/tool-execution
   branches: if `cancel_event.is_set()`, set `status="stopped"`, append
   one history entry (`tool=None`, `outcome_summary="stopped by request
   before this round's action executed"`), persist, return. This is
   checked **first**, ahead of everything else, so a stop always wins
   regardless of what the lead's own (possibly cancelled, possibly
   merely-slow-tier-1) call returned — a tier-2/3 lead call interrupted by
   `cancel_event` would otherwise be misread as an ordinary malformed/
   failed action (consuming the malformed-retry budget, or worse,
   eventually forcing an `ask_user` escalation instead of a clean stop).
2. Immediately after the delegate branch's own `agent_run(...,
   cancel_event=cancel_event)` call returns, **before** the existing
   SUCCEEDED/FAILED history framing: if `cancel_event.is_set()`, the same
   clean "stopped" outcome is recorded instead (with the delegate's own
   `agent`/`task` still named, `outcome_summary="stopped by request
   (delegation interrupted)"`) — not the generic `FAILED (unknown error,
   see log)` framing a bare cancelled `agent_run()` result would otherwise
   produce, which would be technically correct but misleadingly
   indistinguishable from a real failure.

**`team_run(state, *, cancel_event=None)`** — additive kwarg, one new
checkpoint at the top of the existing `while` loop, alongside the
existing `max_rounds` check: if `cancel_event.is_set()`, set
`status="stopped"`, persist, break — cheapest checkpoint, avoids even
starting a new round.

**Why this is sound regardless of write ordering between the two
threads.** `stop_team()` (called from the HTTP request-handler thread) and
`team_run()`'s own background thread never share a mutable Python object —
each loads its own fresh `state` dict from disk and persists its own
writes (`_persist()`'s atomic tmp+`os.replace()`, unchanged from part 1).
The only cross-thread coordination is `cancel_event` itself. Once it's
set, **both** threads independently converge on writing `status="stopped"`
(the background thread via one of its three checkpoints; `stop_team()`
directly) — so whichever one's `_persist()` call happens to land last, the
final on-disk value is the same, and `stop_team()`'s own status-setting
line (`if state["status"] not in (..., "stopped"): state["status"] =
"stopped"`) is already a no-op if the background thread got there first.
No lock is needed on `run.json` itself for this property to hold — see
"Edge cases" for the one place a stale-write race is real but benign
(a trimmed `worktrees` map entry).

### 8. `launch_team()` — the carried-forward `project_name` guard

Part 1's own disclosed, deliberately-left-open item: `project_name =
os.path.basename(os.path.normpath(workdir))` yields `""` for a
filesystem-root `workdir`, producing a degenerate `"team-"` session name.
**Decision, made explicit here rather than carried a third cycle: this
route cannot reach that case.** `workdir` is constructed exclusively as
`os.path.join(PROJECTS_DIR, name)` where `name` is checked against
`instance_names()` (real, existing `PROJECTS_DIR` subdirectories) —
*identical* to every existing `instance/<name>/...` route's own
precondition (`app.py:3057-3058`, `:3069-3070`, `:3078-3079`). No
operator-supplied absolute path of any shape ever reaches `launch_team()`
through this or any other route. **Still added anyway, per the reviewer's
own explicit part-1 recommendation** ("worth a one-line guard... cheap and
never triggered under normal use"), since it's a one-line, zero-risk
addition that closes the item definitively rather than carrying it again:

```python
project_name = os.path.basename(os.path.normpath(workdir))
if not project_name:
    return {"ok": False, "error": "workdir has no derivable project name"}
```

### 9. Minimal template control

One new render function, `teamRow(name, team)`, styled after `deployRow()`
(`app.py:1841-1846`) — a single-purpose row, not the checkbox-toggle
pattern (`toggle()`/`row()`), since starting a team needs a task-text
input, not a boolean flip. Rendered unconditionally per project (no
"only if configured" gate, unlike `deployRow()`):

- `team.status === "idle"`: a `<textarea>` for the task text plus a
  "Start team" button (`doTeamStart(name)`), disabled while the textarea
  is empty (client-side only — the route's own `400` on an empty task is
  the real, authoritative validation, matching `startNewProject()`'s own
  client-then-server pattern).
- Any other status: the textarea/button are replaced by the coarse label
  (`running`/`blocked`/`finished`/`error`) plus a "Stop team" button
  (`doTeamStop(name)`), reusing `toggle()`'s existing POST+`handleAction
  Result()` plumbing for the TOTP overlay dance (`kind='team-stop'`,
  `actionPath()` gains one more branch: `/projects/<name>/team/stop`) —
  `doTeamStart()` follows `doDeploy()`'s own direct-`fetch()`-plus-inline-
  result-slot shape instead, since it needs to send a JSON body
  (`{task: ...}`) that isn't shaped like any existing `toggle()` action
  body.
- No poll-interval-specific new JS timer — the existing `refresh()`
  4-second poll (unchanged) already re-fetches `/status` and re-renders
  this row from its new `team` field, same as every other row.

## Affected areas

- `app/teams.py` — `agent_run()`/`_run_headless_session()`/`_call_lead()`/
  `team_step()`/`team_run()` each gain one additive `cancel_event` kwarg
  (no existing signature's positional shape changes); `default_team_
  composition()`, `latest_run_for_project()`, `mark_run_error()` (new);
  `launch_team()`'s one-line `project_name` guard.
- `app/app.py` — `import teams` (placement matters, see §1); `_team_
  threads` (new in-memory table), `_run_team_in_background()`,
  `TEAM_REAP_POLL_INTERVAL_SECONDS`, `_team_reap_lock`/`_team_reap_last_
  at`/`_team_reap_if_due()` (new); `_reap_dead_state()` gains one call to
  `_team_reap_if_due()`; two new `do_POST` branches (`/projects/<name>/
  team/start`, `/team/stop`); `/status`'s per-instance dict gains one new
  `team` field; the embedded `<script>` gains `teamRow()`/`doTeamStart()`/
  `doTeamStop()` and one `actionPath()` branch.
- `install.sh` — one new unconditional `cp` line (§6).
- `config/switchboard.env.example` — `TEAM_REAP_POLL_INTERVAL_SECONDS`,
  same commented-out-with-explanation style as `GITEA_POLL_INTERVAL_
  SECONDS`.
- New tests: `tests/test_teams_cancel.py` (cooperative-cancellation
  mechanism, pure + real-tmux, mirrors `tests/test_teams_lifecycle.py`'s
  own split) and `tests/test_team_routes.py` (real `ThreadingHTTPServer` +
  real `urllib.request.urlopen()`, mirrors `DeployEndpointTests`).
  `tests/test_teams_lead.py`/`tests/test_teams_lifecycle.py` gain a small
  number of additive cases for the new kwargs' default-`None` byte-for-
  byte-unchanged behavior, not a rewrite.

## Edge cases

- **Two concurrent `POST /team/start` for the same project** (real
  concurrency — two near-simultaneous requests, not two sequential calls)
  — the second's `launch_team()` call hits the exact same session-name
  collision refusal part 1 already built and tested (`tmux_has(session)`
  check, before any worktree is touched); the route surfaces it as a 400
  with `launch_team()`'s own message. The first request's own thread/
  worktrees/session are completely unaffected — no new locking needed at
  the route layer, this is entirely `launch_team()`'s own already-reviewed
  atomicity.
- **`POST /team/stop` arriving while `team_step()` is mid-delegate** — the
  in-flight `agent_run()` call is SIGTERM'd (via `cancel_event`, checked
  on `TEAM_HEADLESS_POLL_SECONDS`'s own cadence), escalating to KILL then
  `kill-session` if unresponsive — bounded by `2 ×
  TEAM_HEADLESS_KILL_GRACE_SECONDS` (default 20s) worst case. The HTTP
  response itself does **not** wait for this — `/team/stop` returns as
  soon as `stop_team()`'s own (separate, already-fast) session/worktree
  teardown completes; the delegate's own throwaway `switchboard-headless-
  <run_id>` session and the driving thread wind down asynchronously after.
- **`POST /team/stop` arriving while a tier-1 lead call is in flight** — no
  subprocess to SIGTERM (it's a blocking `urllib` HTTP call). The call is
  allowed to finish naturally, bounded by `TEAM_LLM_TIMEOUT_SECONDS`
  (default 120s) times up to `TEAM_LLM_TRANSPORT_RETRY_BUDGET + 1`
  attempts (default 3) in the worst case — a real, disclosed, bounded
  latency, not immediate, but self-limiting: `team_step()`'s checkpoint 1
  discards whatever the call returns (or however it failed) the instant
  control returns, so the round's action never actually executes once a
  stop was requested, even though the underlying HTTP call itself
  couldn't be interrupted mid-flight. The dashboard session and worktrees
  are torn down immediately regardless (that part of "stop" was already
  unconditional and thread-independent in part 1).
- **`POST /team/stop` with no team ever started for the project, or one
  that already finished/errored/was already stopped** — `{"ok": True,
  "message": "no team currently running for this project"}`, matching
  `instance_stop()`'s own "always safe to call" precedent; no call into
  `teams.stop_team()` at all (nothing to act on).
- **Service restart while a web-launched team is running** — the tmux
  dashboard session (a RUN_USER process tree, independent of `app.py`'s
  own SVC_USER process) survives; `app.py`'s own `_team_threads` does
  not. `sweep_dead_teams()`'s existing case-1 check (session-gone →
  error) does **not** fire here, correctly — the session is not gone,
  only the driving thread is. `_team_reap_if_due()`'s new orphan check
  (§5) closes this specific gap: a run recorded `"running"` with no
  matching, alive thread in `_team_threads` is marked `"error"`
  (surfacing as the `error` coarse label), and `/team/stop` — re-deriving
  the run_id via `latest_run_for_project()`, never dependent on
  `_team_threads` — still correctly tears down the old session/worktrees
  regardless of whether the orphan check has run yet.
- **A legitimate, concurrent CLI-driven `team-resume <run_id>` against a
  web-launched run** (an operator manually running the CLI against a run
  the web route launched — explicitly still possible, same footgun class
  part 1 already disclosed) — `_team_reap_if_due()`'s orphan check has no
  way to distinguish this from a genuine restart-orphan (both look like
  "status running, no matching live entry in `_team_threads`"), and would
  mark the run `"error"` even though a real process is still driving it.
  **This is a deliberate, disclosed, self-correcting tradeoff, not an
  oversight**: the CLI process's own next `_persist()` call (every round,
  unconditionally, unchanged from part 1) unconditionally overwrites
  `state["status"]` with whatever it actually is — so the mis-flip is
  visible for at most one poll interval (`TEAM_REAP_POLL_INTERVAL_
  SECONDS`, up to 60s) before the CLI process's own next round corrects
  it. A stricter design (a persisted `driver` marker distinguishing
  "web-launched" from "CLI-launched" runs, so the orphan check only ever
  acts on the former) was considered and rejected as unnecessary
  complexity for a narrow, low-severity, self-correcting edge case — see
  "Open questions" if this tradeoff should be revisited.
- **`default_team_composition()` with zero headless-eligible engines and
  no `TEAM_LLM_*` configured** — `{"ok": False, "error": "no roster
  member is available to lead a team..."}`, surfaced as the route's own
  400; no worktree, no session, no `run.json` created (the route never
  calls `launch_team()` in this case at all).
- **An oversized task-text body** — already bounded by existing, unchanged
  machinery: `TEAM_LEAD_PROMPT_MAX_CHARS` caps the assembled round-context
  prompt (tier 2/3) and `_split_capped_prompt()` (tier 1) regardless of
  how the task text originally arrived (CLI arg or HTTP body) — no new
  validation needed at the route level.
- **`app.py` restarted while `_team_reap_if_due()`'s own lock is held mid-
  pass** — the in-process `Lock` is process-local; a restart simply drops
  it, no persisted lock file to clean up, no different from any other
  in-memory state this spec or `_gitea_poll_if_due()` already accepts
  being lost on restart.
- **A stale `worktrees` map entry written by a losing writer in the
  cross-thread race described in §7** — benign: a losing write can only
  ever "restore" an entry to a path that `stop_team()`/`sweep_dead_teams()`
  already correctly classify as `"absent"` on their next pass (the
  directory really is gone), never a *different*, newer run's live
  resource at that path (that specific danger — the part-1 "Defect #1"
  class — is about a DIFFERENT run's collision, not this run's own
  already-removed entry reappearing) — self-corrects on the next sweep,
  not a new instance of that defect class.

## Acceptance criteria

- [ ] `python3 -c "import sys; sys.path.insert(0, 'app'); import app"`
      (or an equivalent real process start) succeeds with no
      `ImportError`/`AttributeError` — proven by actually executing the
      import, not `py_compile` alone (see "Proposed approach" §1 for why
      `py_compile` cannot catch this class of defect).
- [ ] `POST /projects/<name>/team/start` against a real scratch project
      (clean git repo, real headless-eligible test-fixture engines, no
      `TEAM_LLM_*` configured, at least one tier-2 engine present) returns
      200 with a real `run_id`/`session`; `run.json` shows the first tier-2
      engine as lead and every other headless-eligible engine as members —
      verified by inspecting the real, persisted state, not just the HTTP
      response.
- [ ] **Tier-3-only roster refuses** (user decision, §2): no `TEAM_LLM_*`,
      and every headless-eligible engine is tier 3 → `/team/start` returns
      a 4xx naming both concrete fixes (configure `TEAM_LLM_*`, or add a
      tier-2 engine). No worktree, no tmux session, no `run.json` created.
      Must be asserted against real persisted state, not just the response
      body — a refusal that still leaves a session behind is the exact
      failure shape part 1's Defect #4 was.
- [ ] **The CLI's `--lead` still accepts a tier-3 lead**, unchanged — the
      refusal above constrains only the web route's default composition,
      not the system. Regression-test this explicitly so a later reader
      does not "tidy" the two into one rule.
- [ ] Same, with `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` set (unreachable
      endpoint is fine — `launch_team()` never dials it, unchanged from
      part 1) — the Ollama model is selected as lead instead.
- [ ] `POST .../team/start` on an unknown project name → 404, no launch
      attempted (no worktree, no session, no `run.json`).
- [ ] Two real, near-simultaneous `POST .../team/start` requests for the
      same project (issued from two separate threads/connections, not
      sequential calls) — exactly one succeeds; the other gets `launch_
      team()`'s own collision error; the successful run's worktrees/
      session/thread are byte-for-byte unaffected by the losing attempt.
- [ ] `POST .../team/stop` while the background thread is genuinely
      mid-delegate (a real, slow-by-design stand-in engine fixture that
      sleeps past a short test timeout) — the HTTP response returns
      promptly (bounded by `stop_team()`'s own teardown time, not by the
      delegate's own escalation ladder); the delegate's own throwaway
      tmux session is gone within the ladder's bounded time; `run.json`'s
      final status is `"stopped"`, not left on `"running"` and not a raw
      traceback anywhere in the process's own stderr.
- [ ] `POST .../team/stop` while the background thread is between rounds
      (not mid-call) — status flips to `"stopped"` via the between-round
      checkpoint alone; no delegate/lead call has to run to completion or
      time out first.
- [ ] `POST .../team/stop` on a project with no team ever started, and on
      one whose team already finished — both return `{"ok": True}` with
      no error, matching `instance_stop()`'s own idempotent precedent.
- [ ] **Service restart with a run in flight, real not simulated in one
      process**: launch a team via the route in one process; terminate
      that process (simulating a restart) **without** calling `/team/
      stop` first (the real tmux dashboard session survives, independent
      of the killed process); start a fresh process (fresh, empty `_team_
      threads`); call `/team/stop` for that project against the fresh
      process — the OLD run's real session/worktrees are still correctly
      torn down, proven via real `tmux list-windows`/`git worktree list`
      checks, not by inspecting in-memory state.
- [ ] Same restart scenario, but call `GET /status` on the fresh process
      **before** calling `/team/stop` — the project's coarse team status
      shows `"running"` (truthful, matching the real, still-running
      record) until either `/team/stop` is called or, after `TEAM_REAP_
      POLL_INTERVAL_SECONDS` (monkeypatched to `0` for the test) elapses
      and a `/status` poll runs `_team_reap_if_due()`, at which point it
      flips to `"error"` — both paths independently verified as real
      outcomes, not asserted from the design alone.
- [ ] A stand-in for "a human is still legitimately running `team-resume`
      via the CLI against a web-launched run" (a real, separate
      subprocess calling `team-resume <run_id>`, not a web-route thread)
      is not permanently disrupted by `_team_reap_if_due()`'s orphan check
      firing once — its own next `_persist()` call restores the correct,
      live status, verified by observing `run.json` across at least two
      of that process's own rounds spanning one reap pass.
- [ ] `agent_run()`/`_run_headless_session()`/`_call_lead()`/`team_step()`/
      `team_run()`'s existing (no-`cancel_event`) behavior is byte-for-
      byte unchanged — the full `tests/test_teams_headless.py`,
      `tests/test_teams_lead.py`, and `tests/test_teams_lifecycle.py`
      suites pass with zero modification to their own assertions.
- [ ] `install.sh`'s "-- App + engines --" block copies `app/teams.py` to
      `$INSTALL_DIR/teams.py`, verified by actually extracting and running
      that block (same technique `InstallShDeployMapBlockTests` already
      established for this file) against a scratch `$INSTALL_DIR`, not by
      grepping the source alone.
- [ ] Full test suite green, several consecutive runs (this story's own
      established discipline given 4 of 9 total defects so far were found
      in exactly this area — tmux/concurrency — of part 1 alone); `git
      diff --stat -- app/teams.py` shows no change to any existing
      function's positional-argument shape (only new keyword-only params
      and new functions).

## Test plan

Mirrors part 1's own split (pure-logic / real-git-and-tmux / real-HTTP),
extended for threading and HTTP:

**Pure logic, no subprocess, no tmux, no HTTP:**
`default_team_composition()`'s priority-order logic against hand-built
`roster()` fixtures (Ollama configured / tier-2-only / tier-3-only /
empty); `latest_run_for_project()`'s selection logic against hand-
constructed `run.json` fixtures at every status/`updated_at` combination;
`_substitute...`-style unit coverage is not needed here (no new
substitution logic), but the three `team_step()`/`team_run()` cancellation
checkpoints each get a dedicated pure test using a fake `_call_lead()`/
`agent_run()` that returns immediately, asserting the exact `status`/
history-entry shape each checkpoint produces when `cancel_event` is
already set going in.

**Real tmux, real subprocess (the bulk of the risk surface, per this
story's own established pattern that every prior defect in this exact
area was found by exercising real tmux past the spec's enumerated cases):**
`agent_run(..., cancel_event=...)` against a real, deliberately slow
stand-in engine fixture (sleeps well past a short test timeout), asserting
the real subprocess is actually gone (`tmux_has()` false) within the
bounded escalation window, and `cancel_reason == "stopped"` in the
returned result; `team_step()`/`team_run()`'s own checkpoints exercised
end to end via a real `team-resume`-equivalent call with a real,
controllable stand-in delegate, `cancel_event.set()` triggered from a
second thread mid-call.

**Real `ThreadingHTTPServer`, real `urllib.request.urlopen()` (mirrors
`DeployEndpointTests` verbatim in technique):** both new routes' happy
paths; the two-concurrent-starts race (issued via two real threads against
the same running test server); the mid-delegate-stop timing case; the
service-restart simulation (kill and restart the actual test server
process, or an equivalent same-process "clear `_team_threads`, drop and
recreate the `Handler`'s own module-level state" technique if a full
process restart proves impractical inside `unittest` — to be resolved by
the developer, documented either way, not guessed at here).

**install.sh:** `InstallShDeployMapBlockTests`-style block extraction —
extract the "-- App + engines --" block verbatim from the real
`install.sh` source and run it against a scratch `$INSTALL_DIR`, asserting
`teams.py` actually lands there with the right content.

## Open questions

### Settled by the user (2026-08-13) — build to these, do not reopen

- **`default_team_composition()` REFUSES rather than picking a tier-3
  lead.** Decided **against this spec's own original recommendation**,
  which was to allow it — §2 above has been rewritten accordingly, and
  that rewritten text is what to build. Rationale as accepted: tier 3 is
  the least reliable adapter; this route has no picker (6e), so the
  operator cannot see or override the choice; the UI shows only a coarse
  status label; and the lead loop's malformed-output retry budget would
  absorb the resulting failures quietly. A silently degraded lead is
  therefore hard to diagnose, and refusing is loud and names the fix.
  This constrains only the DEFAULT — 6c's CLI `--lead` still accepts a
  tier-3 lead as an explicit opt-in, unchanged.
- **The self-correcting false positive in `_team_reap_if_due()`'s orphan
  check is ACCEPTED as specified.** A legitimate concurrent CLI-driven run
  may be briefly, incorrectly flipped to `"error"` for up to one reap
  interval. No persisted `driver` marker this cycle. Rationale as
  accepted: the wrong label is transient, bounded, and self-correcting,
  and the alternative adds a new persisted field that must stay correct
  across crashes — more state in precisely the area that has already
  produced four defects. Matches how part 1's `blocked_ask_user`-TTL and
  dirty-tree decisions were settled: accept a documented tradeoff over
  speculative hardening. Revisit only if real usage shows it mattering.

## Part 2b preview (not this cycle's scope)

For context only — its own follow-on spec/cycle, not built or reviewed
here (see "Split rationale"):

- `install.sh --with-ollama` — new flag, off by default, following
  `--with-deploy-target`'s own exact interactive shape
  (`install.sh:625-704`, the closest existing precedent: an optional flag
  block using `prompt()`/`prompt_secret()`, the idempotent `set_env()`
  upsert idiom, and a final printed summary). Prompts for an existing
  Ollama-compatible endpoint URL and model name (`prompt "Ollama endpoint
  URL (OpenAI-compatible, e.g. an existing remote Ollama's /v1)" "
  http://127.0.0.1:11434/v1"`, `prompt "Model name" "qwen3:8b"`),
  validates reachability with `curl` (already installed unconditionally,
  `install.sh:146`) against the endpoint's own `/v1/models` or Ollama's
  native `/api/tags`, and that the named model is actually present in the
  response, before writing anything — refuses to write config for an
  endpoint it can't reach or a model it can't find, matching this spec's
  own `launch_team()`/`default_team_composition()` "fail the start, don't
  write config that fails later" discipline. On success, `set_env
  "$ENV_FILE" TEAM_LLM_BASE_URL "..."` / `set_env "$ENV_FILE" TEAM_LLM_
  MODEL "..."`. Installs nothing locally, per `docs/story.md` §2.5
  (settled — the standard container has ~715MB free RAM with swap
  exhausted; no tool-capable model fits).
- No code dependency on this spec's own routes/threading — `--with-ollama`
  only ever writes env vars `default_team_composition()` (this spec)
  already reads at request time; the two cycles compose with zero shared
  surface beyond that existing env-var contract.
- Test plan preview: `InstallShDeployMapBlockTests`-style block extraction
  (real `bash -c`, no VM), with a stubbed local HTTP server standing in
  for "a reachable Ollama" (matching `TAIGA_ENV`/`GITEA_ENV` setup's own
  established block-extraction test technique), plus the negative case
  (unreachable endpoint → refuses to write `TEAM_LLM_*`, verified by
  inspecting the resulting env file).

## Risk / rollback notes

Every new kwarg (`agent_run()`/`_run_headless_session()`/`_call_lead()`/
`team_step()`/`team_run()`'s `cancel_event`) is additive, keyword-only,
default `None` — no existing caller's behavior changes, and the entire
CLI-only workflow (`team-launch`/`team-start`/`team-resume`/`team-stop`
from a shell) is completely unaffected, since `cancel_event` is never
supplied there. The two new routes and the in-memory thread table are new
surface with no existing caller. The one place a mistake would be
immediately visible rather than silently wrong is `import teams`'s own
placement in `app.py` (§1) — a wrong placement fails loudly at process
startup (`ImportError`), not subtly at runtime, which is why "actually
start the process" is its own, separate acceptance criterion distinct
from `py_compile`. Rollback is `git revert` of this cycle's commit(s); no
schema/data migration (the two new `run.json`-adjacent functions,
`latest_run_for_project()`/`mark_run_error()`, only ever read/write the
existing, unchanged `run.json` shape). A team left running by a reverted
build is inert once reverted the same way part 1 already documented for
its own feature (`tmux kill-session`/`git worktree remove --force` by
hand, if ever needed) — nothing new to clean up beyond what part 1 already
disclosed.
