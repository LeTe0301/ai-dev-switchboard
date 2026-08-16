# Spec: Concurrent sessions per project — part 1: session-identity backend (ports, tmux naming, status/API layer)

## Routing note (read first)
**Workflow: `workflows/feature.md`.** This is part 1 of a 2-part split of
"multiple concurrent sessions per project" (Leo's request #1, already
approved) — see "Why this is split" below. **Recommend skipping
ux-designer for this part specifically** and going straight to `developer`:
this part is backend-only (session identity, port allocation, tmux naming,
`/status` JSON, new POST routes) and introduces no new visual language —
the existing checkbox UI is deliberately left rendering unchanged this
cycle via a back-compat shim (see "Proposed approach"). Part 2 (next spec,
`docs/spec-feature1-part2-multi-session-ui.md`, already written and queued)
is the one that touches the UI and does need ux-designer.

**Queued after this part:**
1. **This spec** — backend session-identity model, port allocation, new
   `/instance/<name>/spawn` + `/instance/<name>/session/<id>/stop` routes,
   `/status` JSON gains a `sessions` array per project. Additive — the
   existing checkbox toggle keeps working unchanged against a temporary
   back-compat shim.
2. `docs/spec-feature1-part2-multi-session-ui.md` — replace the checkbox
   with a "+" control + per-session list in the frontend, remove the
   back-compat shim and the old `/on`/`/off` routes once nothing calls them.
3. `docs/spec-feature2-team-chat-page.md` — Leo's request #2 (dedicated
   team chat page), independent of both parts above, queued to run
   whenever convenient (no ordering dependency).

Both Leo's requests are **already approved** — no sign-off gate applies
here; this is scoping/spec-writing for already-agreed work, not a
self-proposed idea awaiting confirmation.

## Why this is split
"Affected areas" below spans four distinct layers that would otherwise all
land in one developer dispatch: the session-identity data model (what a
"session" even is), port allocation (`_ttyd_port`/`_ttyd_ports`), the
`/status` JSON contract every frontend row-render depends on, and two new
mutating routes plus a change in semantics of `_reap_dead_state()`'s
self-healing sweep. Per the load-balanced-decomposition rule, that's split
here from the actual UI rewrite (part 2) so each half is independently
buildable and reviewable — this half is a pure backend change verifiable
by hitting the JSON API directly (`curl`/the existing Python test
conventions), the same way `docs/BACKLOG.md`'s own E2E rounds already
verify backend routes headlessly before/independent of browser checks.

## Summary
Replace the single-session-per-(engine,project) assumption baked into
session naming, ttyd port allocation, and `/status`'s JSON shape with a
real per-session identity scheme, so a project can have any number of
concurrent engine sessions running at once — while keeping the existing
checkbox-driven frontend working unmodified against a temporary
back-compat shim until part 2 lands.

## Goals
- A new session-identity scheme: every spawned engine session gets a
  unique `session_id` (also used verbatim as the tmux session name),
  distinct from today's exact `f"{engine_name}-{project_name}"` naming
  which assumed at most one live session per (engine, project).
- ttyd port allocation (`_ttyd_port`/`_ttyd_ports`/`_ttyd_procs`/
  `_ttyd_urls`) and captured hosted-URL tracking (`_session_urls`) re-keyed
  from project name to `session_id`, so N concurrent sessions for one
  project each get their own port/URL/process, independently.
- New routes: `POST /instance/<name>/spawn` (body: `{engine}`) starts an
  *additional* session without regard to whether others are already
  running; `POST /instance/<name>/session/<session_id>/stop` tears down
  exactly one session, leaving siblings untouched.
- `/status`'s per-project object gains a `sessions` array (one entry per
  live session: `session_id`, `engine`, `url`) — the real, new data shape
  part 2's UI will consume.
- `_reap_dead_state()`'s self-healing sweep (today keyed by project name,
  driven by `active_engine()`) generalized to sweep per-session, using
  `tmux_has(session_id)` directly as the liveness check — same philosophy
  ("tmux dying on its own is the source of truth"), applied per session
  instead of per project.
- Zero visible behavior change to the existing checkbox UI this cycle
  (back-compat shim — see "Proposed approach").

## Non-goals
- No frontend changes (no "+" button, no session list UI) — that is part 2
  in full.
- No git-worktree-per-session isolation. Concurrent sessions for the same
  project continue to share the single working copy under `PROJECTS_DIR/
  <name>`, exactly like today's single session does — see "Open questions"
  for why this is an explicit, flagged assumption rather than a silent
  omission.
- No configurable cap on session count ("any amount," per Leo's own
  wording) — no admission-control/resource-limit logic added.
- No change to `code-server`'s lifecycle (`_code_start`/`_code_stop`/
  `_code_port`/`code_running`) — it is already project-scoped (one
  instance per project, independent of engine sessions) and untouched by
  this spec; confirmed via archaeology that nothing about it assumes
  single-*engine*-session, only single-*code-server*-process, which stays
  true.
- No change to `app/teams.py` or any team-session (`team-<project>`)
  behavior — team sessions are a structurally separate concept (one
  session, multiple tmux *windows*) and are explicitly out of scope; the
  only touchpoint is preserving the existing reserved-engine-name-prefix
  collision guard described below, unchanged.
- Not fixing the existing "ttyd/code ports grow forever, never reclaimed"
  limitation (`_next_ttyd_port`/`_next_code_port`, documented in
  `docs/ARCHITECTURE.md` as an accepted "one sharp edge"). This spec keeps
  the exact same never-reclaim allocator, just re-keyed by `session_id`;
  concurrent spawning consumes ports somewhat faster over a long uptime,
  but the existing mitigation (restart the service) is unchanged and this
  spec does not attempt to improve it.
- Not changing how `smoke_check_run()`'s externally-visible single-URL
  contract *should* work once multiple sessions exist for a project — see
  "Edge cases" for how this spec preserves current behavior mechanically,
  and "Open questions" for the real product question left to part 2.

## Background / current state

### Architecture note
No separate frontend framework/build step — the whole UI is generated
inline as Python string literals inside `app/app.py` (~6700 lines,
`http.server`-based, hand-rolled routing). This spec only touches Python
functions in that file; no `<script>`/CSS changes.

### Today's single-instance assumption, traced end to end
- **Session naming** (`instance_start`, `app/app.py:2603-2626`): `session =
  f"{engine_name}-{name}"` (line 2612) — a pure function of (engine,
  project), so starting the same engine twice for one project is
  structurally impossible; `instance_start` also explicitly guards on it:
  `if active_engine(name) is not None ... return` (line 2609).
- **`active_engine(name)`** (`app/app.py:2395-2396`): `next((e for e in
  load_engines() if tmux_has(f"{e}-{name}")), None)` — returns *at most
  one* engine name per project, by construction. Its only real callers:
  `instance_start`'s own guard (2609), `_reap_dead_state()` (2658, 2662),
  and `/status`'s per-project loop (5957).
- **ttyd port allocation** (`app/app.py:681-717`): `_ttyd_ports`/
  `_ttyd_procs`/`_ttyd_urls` are all `dict[str, ...]` keyed by **project
  name**, `_next_ttyd_port` starts at 7700 and only ever grows.
  `_ttyd_start(name, session)`/`_ttyd_stop(name)` assume one ttyd process
  per project.
- **Captured hosted URL** (`_session_urls`, `app/app.py:2573`): also keyed
  by project name, populated by `run_startup_watch(session, name, engine,
  timeout)` (2576-2600) — note this function *already* takes `session`
  (the tmux session string) and `name` (the dict key) as two separate
  params, which is a convenient existing seam: today they're
  `f"{engine}-{name}"` and `name` respectively, but nothing requires that.
- **`instance_stop(name)`** (`app/app.py:2629-2636`): kills *every*
  engine's session for a project name in a loop (`for e in
  load_engines(): kill-session -t f"{e}-{name}"`) — today equivalent to
  "kill the one that's running" since at most one exists, but written as
  an unconditional bulk-stop.
- **`_reap_dead_state()`** (`app/app.py:2639-2685`): sweeps `_session_urls`
  and the ttyd dicts by checking `active_engine(name)` per project — a
  project-level check, not a per-session one.
- **`/status`'s per-project shape** (`app/app.py:5955-5964`): `engine =
  active_engine(n)`; `url = _session_urls.get(n) if ... else
  _ttyd_urls.get(n) if engine else None`; `inst = {"name": n, "on": engine
  is not None, "engine": engine, "url": url, ...}` — one engine, one URL,
  one on/off boolean, per project, full stop.
- **Routes** (`app/app.py:6417-6428`): `POST /instance/<name>/on` (body
  `{engine}`, falls back to the first configured engine if the given one
  is missing/invalid — `engine = body.get("engine") if body.get("engine")
  in engines else default_engine`, no error surfaced for a bad value) and
  `POST /instance/<name>/off` — both operate on "the" session for a
  project.
- **`smoke_check_run(name, expect_contains)`** (`app/app.py:1923-1953`+):
  reads `_session_urls.get(name)` directly — another project-name-keyed
  consumer that would silently break once `_session_urls` is re-keyed.

### The reserved-name collision guard (must be preserved unchanged)
`_RESERVED_ENGINE_NAME_PREFIXES = ("switchboard", "team")`
(`app/app.py:405`), enforced in `_parse_engine_file` (`app/app.py:450-479`)
so no `.engine` file can define an engine literally named `team`,
`team-anything`, `switchboard`, or `switchboard-anything` — because
`f"{engine}-{project}"` would otherwise be constructible to collide with a
real `team-<project>` team session or a `switchboard-headless-<run_id>`
CLI-headless session. This check is keyed on the **engine name itself**,
not on the full session-name string, so it remains correct unchanged by
this spec's naming scheme (see "Proposed approach" — new session names
still *start with* `f"{engine_name}-{project_name}"`, just with a
guaranteed-unique suffix appended, and engine names still can't be `team`/
`switchboard`-prefixed). `tests/test_teams_headless.py`'s
`ActiveEngineHeadlessCollisionTests` (line 247) directly exercises this
contract against `active_engine()` and must be updated (see "Affected
areas") to exercise the new session-lookup function's equivalent
guarantee instead, not deleted.

### Concurrency model
The server is a real `ThreadingHTTPServer` (`app/app.py:56,6744`) — one
thread per request. The existing `_team_threads_lock` (`app/app.py:2419-
2427`) exists specifically because unguarded check-then-act races on
in-memory dicts have been found and fixed **four separate times** in this
codebase's own team-session subsystem (per that lock's own comment). The
new per-session bookkeeping dict this spec introduces is exactly the same
shape of hazard (concurrent spawn/stop/reap touching the same dict) and
should not become defect instance #5 — see "Proposed approach".

## Proposed approach

### 1. New session-identity scheme
```python
def _new_session_id(engine_name: str, project_name: str) -> str:
    return f"{engine_name}-{project_name}-{int(time.time())}-{secrets.token_hex(3)}"
```
This is the tmux session name **and** the dict key used everywhere below
— no separate id-vs-tmux-name mapping needed. It starts with the exact
`f"{engine_name}-{project_name}"` prefix today's code already produces
(preserving the reserved-prefix collision guarantee above), with a
timestamp+hex suffix (same style as the existing `_run_id()` helper in
`app/teams.py:252`, `f"{int(time.time())}-{secrets.token_hex(6)}"` —
shortened to 3 hex bytes here since this id is in-memory-only, never
persisted to disk, and needs only to disambiguate concurrent spawns within
one process's lifetime, not survive a restart). Collision probability is
accepted at the same standard the codebase already uses for `_run_id()`
and `_ai_reviewer_scratch`'s `secrets.token_hex(8)` (`app/teams.py:1924`)
— not literally proven impossible, consistent with this codebase's
existing risk tolerance for this exact class of identifier.

### 2. New session registry, lock-guarded
```python
_sessions: dict[str, dict] = {}   # session_id -> {"project": str, "engine": str}
_sessions_lock = threading.Lock()

def _sessions_add(session_id, project, engine):
    with _sessions_lock:
        _sessions[session_id] = {"project": project, "engine": engine}

def _sessions_pop(session_id):
    with _sessions_lock:
        return _sessions.pop(session_id, None)

def active_sessions(project_name: str) -> list[dict]:
    """Replaces active_engine() as the per-project liveness source of
    truth. Self-healing like today's active_engine() -- tmux_has() is
    checked fresh here, not just relied on via the periodic reap sweep."""
    with _sessions_lock:
        snapshot = [(sid, info) for sid, info in _sessions.items()
                    if info["project"] == project_name]
    return [{"session_id": sid, "engine": info["engine"]}
            for sid, info in snapshot if tmux_has(sid)]
```
Every mutation and every liveness-deciding read goes through one of these
three functions — same "sanctioned access points only" discipline
`_team_threads_set/_get/_pop_if_owned()` already established
(`app/app.py:2430-2440`) for exactly this defect class. `active_engine()`
itself is removed (its only callers are rewritten below); do not leave it
around half-used.

### 3. Rewire ttyd/URL bookkeeping to key on `session_id`
`_ttyd_ports`/`_ttyd_procs`/`_ttyd_urls`/`_session_urls`: unchanged in
shape (`dict[str, ...]`), but every caller now passes a `session_id`
instead of a project name. `_ttyd_port()`/`_ttyd_start()`/`_ttyd_stop()`
(`app/app.py:687-717`) need no *internal* changes beyond that — they were
already generic over their `name` parameter's meaning. `_code_port`/
`_code_procs`/etc. (`app/app.py:723-761`) are **not** touched (see
Non-goals).

### 4. `instance_start` → returns a `session_id`, no longer guards on "already running"
```python
def instance_start(name: str, engine_name: str = "claude") -> str | None:
    engines = load_engines()
    engine = engines.get(engine_name)
    workdir = os.path.join(PROJECTS_DIR, name)
    if engine is None or not os.path.isdir(workdir):
        return None
    session_id = _new_session_id(engine_name, name)
    _sessions_add(session_id, name, engine_name)
    _session_urls.pop(session_id, None)
    cmd = engine.cmd.format(name=shlex.quote(name))
    subprocess.run(TMUX + ["new-session", "-d", "-s", session_id, "-c", workdir,
                           "bash", "-lc", cmd])
    if engine.startup or engine.url_regex:
        run_startup_watch(session_id, session_id, engine)
    if not engine.url_regex:
        _ttyd_start(session_id, session_id)
    return session_id
```
The old `active_engine(name) is not None: return` guard is **removed**
here — multiplicity is now the point. (The *route* layer re-adds an
equivalent guard for the old `/on` endpoint only, to preserve its exact
back-compat behavior — see point 6.)

### 5. New per-session stop, `_reap_dead_state()` generalized
```python
def instance_stop_session(session_id: str) -> None:
    """Idempotent -- stopping an already-gone session_id is a clean no-op,
    same tolerant style instance_stop() already has today (killing a
    nonexistent tmux session today just fails silently under
    capture_output=True, never raises)."""
    _sessions_pop(session_id)
    _session_urls.pop(session_id, None)
    _ttyd_stop(session_id)
    subprocess.run(TMUX + ["kill-session", "-t", session_id], capture_output=True)
```
`_reap_dead_state()` (`app/app.py:2639-2685`): replace the `active_engine`-
driven ttyd/`_session_urls` sweep with a direct per-session sweep:
```python
for session_id in list(_sessions):
    if not tmux_has(session_id):
        instance_stop_session(session_id)
```
This is simpler than today's version (no `engines.get(active_engine(...))`
indirection needed) since `session_id` *is* the tmux session name — the
liveness check is a single direct `tmux_has()` call.

### 6. Routes
- **New** `POST /instance/<name>/spawn` — body `{engine}`, same
  fallback-to-default-engine behavior as today's `/on` (no new
  validation): `if name not in instance_names(): 404`; else
  `session_id = instance_start(name, engine)`; respond
  `{"ok": session_id is not None, "session_id": session_id}`.
- **New** `POST /instance/<name>/session/<session_id>/stop` — `if name not
  in instance_names(): 404`; else `instance_stop_session(session_id)`;
  respond `{"ok": true}` unconditionally (idempotent, matches "Non-goals"/
  the tolerant style noted above — do not 404 on an already-gone
  session_id, that's a normal race, not a client error).
- **Unchanged, temporarily** `POST /instance/<name>/on` /`/off`
  (back-compat shim, removed in part 2 once the frontend stops calling
  them): `/on` becomes `if active_sessions(name): return {"ok": true}`
  (no-op — mirrors today's exact "already running" guard, generalized to
  "already has *any* session") `else instance_start(name, engine)`. `/off`
  becomes `for s in active_sessions(name): instance_stop_session(s
  ["session_id"])` (stop *all* sessions for the project — mirrors today's
  bulk-kill-every-engine loop, generalized to "every session" instead of
  "every engine").

### 7. `/status` JSON — additive
Per-project object gains `"sessions": [{"session_id", "engine", "url"},
...]` (one entry per `active_sessions(n)` result, `url` resolved the same
way today's single-`url` line already does per session: `_session_urls.get
(sid) if that session's engine has url_regex else _ttyd_urls.get(sid)`).
**Also emit the existing singular fields unchanged**, as a temporary
back-compat shim consumed only by today's still-unmodified frontend:
`"on": len(sessions) > 0`, `"engine": <most-recently-started session's
engine, or None>`, `"url": <that same session's resolved url, or None>`.
"Most recently started" = highest embedded timestamp in the `session_id`,
or just track insertion order in `_sessions` (a plain dict already
preserves insertion order) — either is fine, developer's call, but must be
deterministic (not "whichever `dict` iteration happens to return first").
Part 2 removes these three back-compat fields once nothing reads them.

### 8. Smoke-check preserved via a resolver, not touched otherwise
Add `_latest_session_url_for_project(name) -> str | None` (project-name
in, most-recently-started live session's resolved URL out — same "most
recent" rule as point 7's back-compat `url` field, ideally sharing one
implementation with it rather than two). Change `smoke_check_run`'s single
line `url = _session_urls.get(name)` to `url =
_latest_session_url_for_project(name)`. Behavior for the common
single-session case is externally identical to today; for multi-session
projects it deterministically targets the newest session, not an
arbitrary/undefined one. This is a mechanical preservation of current
behavior, not a new product decision — see "Open questions" for the real
one (does smoke-check need to become session-scoped in the UI?), correctly
left to part 2.

## Affected areas
- `app/app.py`: `_ttyd_*`/`_session_urls` dict semantics (re-keyed, no
  shape change), `active_engine()` removed and replaced by
  `active_sessions()`/`_sessions_add`/`_sessions_pop`, `instance_start()`,
  new `instance_stop_session()`, `instance_stop()` (kept only for the
  back-compat `/off` shim — or inlined into the route, developer's call),
  `_reap_dead_state()`, `smoke_check_run()`'s one-line URL lookup, `/status`
  handler (`app/app.py:5955-5964` area), new POST route branches near
  `app/app.py:6417-6428`.
- **No** frontend (`<script>`/`<style>` inside `PAGE_TEMPLATE`) changes.
- `tests/test_teams_headless.py`: `ActiveEngineHeadlessCollisionTests`
  (line 247) currently asserts `appmod.active_engine(project_name) is
  None` against a live `switchboard-headless-<run_id>` session — update to
  assert the equivalent via `active_sessions(project_name) == []` (same
  collision-safety contract, new function name).
- New Python unit tests (new file or extend an existing `tests/
  test_*.py`) for: `_new_session_id` uniqueness/prefix shape, `spawn`
  allowing N concurrent sessions for one project (including N sessions of
  the *same* engine — see "Open questions"), `session/<id>/stop` only
  tearing down the targeted session, `_reap_dead_state()` pruning exactly
  the dead session's bookkeeping when one of several dies, and the `/on`/
  `/off` back-compat routes' preserved semantics (no-op-if-already-running
  / stop-all, respectively).
- `docs/implementation.md` — developer's usual write-up.

## Edge cases
- **Same engine spawned twice concurrently for one project** — explicitly
  allowed (see Open questions); each gets an independent `session_id`,
  tmux session, and (if applicable) ttyd port/captured URL. No dedup.
- **Two spawns within the same wall-clock second** — disambiguated by the
  trailing `secrets.token_hex(3)`; accepted collision risk per "Proposed
  approach" §1.
- **Stopping an already-gone `session_id`** (double-click, stale frontend
  cache, or a race with the reap sweep) — idempotent no-op, `{"ok": true}`,
  never a 404/500 (see §6).
- **Concurrent stop + reap-sweep racing on the same `session_id`** — both
  go through `_sessions_pop`, guarded by `_sessions_lock`; the second
  caller's pop returns `None` and short-circuits cleanly, no double
  teardown.
- **Unknown engine name passed to `/spawn`** — falls back to the default
  engine, silently, identically to today's `/on` route (no new validation
  introduced — explicit non-goal, not an oversight).
- **Unknown/nonexistent project name** — `404 {"error": "unknown
  instance"}`, matching every existing `/instance/<name>/...` route's
  convention.
- **Project with zero sessions calling `/off`** (back-compat route) — no-
  op, `{"ok": true}`, matches today's exact behavior.
- **`_session_urls`/ttyd bookkeeping for a session whose engine has no
  `url_regex`** — unchanged fallback to the ttyd-hosted terminal URL, now
  correctly per-session instead of per-project.
- **Shared working copy under concurrent sessions** (two sessions running
  `git` operations, or editing the same files, at once) — a real
  correctness risk, explicitly accepted and not mitigated by this spec;
  see "Open questions".

## Acceptance criteria
- [ ] Given a project with zero sessions, when `POST /instance/<name>/
      spawn` is called with a valid engine, then a new tmux session
      exists (`tmux has-session` true), `/status` lists it under that
      project's `sessions` array with the right `engine`, and the
      response includes a `session_id`.
- [ ] Given a project already running engine X, when `POST /instance/
      <name>/spawn` is called again (with X or a different engine Y),
      then a second, independent tmux session is created — both appear
      simultaneously in `/status`'s `sessions` array, distinct
      `session_id`s, neither one killed.
- [ ] Given a project with 2 running sessions, when `POST /instance/
      <name>/session/<id>/stop` targets one of them, then only that
      session's tmux session, ttyd process/port, and `_session_urls` entry
      are torn down; the sibling session is completely unaffected
      (confirmed via `/status` before and after).
- [ ] Given a project with 2 running sessions, when the legacy `POST /
      instance/<name>/off` is called, then **all** sessions for that
      project are stopped (back-compat bulk-stop, matching today's
      pre-spec behavior for the single-session case, generalized).
- [ ] Given a project with ≥1 running session, when the legacy `POST /
      instance/<name>/on` is called, then it is a no-op (`{"ok": true}`,
      no new session spawned) — matches today's exact guard, generalized.
- [ ] Given one of two running sessions for a project dies on its own
      (kill its tmux session directly, simulating engine exit), when
      `/status` is next polled, then `_reap_dead_state()` prunes exactly
      that session's bookkeeping; the sibling session's entry in
      `/status`'s `sessions` array is untouched.
- [ ] Given `tests/test_teams_headless.py`'s
      `ActiveEngineHeadlessCollisionTests`, when updated to call
      `active_sessions()` instead of `active_engine()`, then it still
      passes — a live `switchboard-headless-<run_id>` tmux session is
      never reported as one of a project's active sessions.
- [ ] Given a project with 2 sessions of different engines, when `POST /
      instance/<name>/smoke-check` runs, then it targets the most-
      recently-started session's captured URL deterministically (not an
      arbitrary one) — verified by asserting which of two distinct mock
      URLs the check actually hit.
- [ ] All pre-existing Python tests continue to pass unmodified except the
      one intentionally updated above.

## Open questions
- **Same-engine multiplicity**: assumption is that spawning the *same*
  engine twice for one project is allowed (Leo's wording was "as many
  concurrent sessions as he wants," not "one per distinct engine") —
  proceeding on that basis; flagging in case the actual intent was closer
  to "one session per engine, but pick a different engine each time,"
  which would be a materially smaller/safer feature. If that's wrong,
  it's a small guard to add later (reject `/spawn` when the requested
  engine already has a live session for that project), not a rearchitect.
- **Shared working copy, no per-session worktree isolation**: this spec
  deliberately does not give each spawned session its own git worktree
  (unlike the team feature's own per-agent-worktree precedent in
  `app/teams.py`). Leo's request text frames this purely as a session-
  identity/port/UI concern, not a workspace-isolation one, and backlog
  item 21 (docs/BACKLOG.md:1297) already flagged this exact question as
  unresolved for a future pass — proceeding on the assumption that a
  shared working copy (today's status quo) is acceptable for this cycle,
  with the risk of concurrent-write conflicts between sibling sessions
  called out explicitly rather than silently accepted. If Leo wants
  isolation, that's a genuinely separate, larger feature (its own spec),
  not a tweak to this one.
- **Smoke-check's real UI treatment once multiple sessions exist**: this
  spec only preserves current *mechanical* behavior (targets the newest
  session, deterministically). Whether smoke-check should become a per-
  session control in part 2's UI, or stay a single project-level "check
  the newest one" action, is a real product decision left to part 2 —
  not decided here.

## Risk / rollback notes
Purely additive at the route/JSON level (new fields, new endpoints; old
fields/endpoints unchanged in behavior this cycle) and a rename/re-keying
of internal-only dicts with no external contract today (nothing outside
`app.py` reads `_ttyd_ports`/`_session_urls`/`active_engine` directly).
The one real behavior change with test-visible impact is `active_engine()`
being removed in favor of `active_sessions()` — covered by the updated
headless-collision test above, so a regression fails CI rather than
shipping silently. Rollback is a plain revert of the commit; no data
migration involved since all of this state is in-memory only (already
documented in `docs/ARCHITECTURE.md` as lost-on-restart by design).
