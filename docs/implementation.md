# Implementation: Concurrent sessions per project — part 1: session-identity backend (ports, tmux naming, status/API layer)

## Summary
Replaced the single-session-per-(engine, project) assumption in `app/app.py`
with a real per-session identity scheme: every spawned engine session now
gets a unique `session_id` (also its tmux session name), a lock-guarded
`_sessions` registry replaces `active_engine()`, ttyd port/URL bookkeeping
is re-keyed from project name to `session_id`, and two new routes
(`POST /instance/<name>/spawn`, `POST /instance/<name>/session/<id>/stop`)
let a project run any number of concurrent sessions. `/status` gains a real
`sessions` array per project while keeping the existing `on`/`engine`/`url`
singular fields as a back-compat shim so today's checkbox frontend keeps
working unmodified. No frontend changes this cycle (part 2, queued
separately, does the UI rewrite and removes the shim).

## Changes by file
- `app/app.py`:
  - Removed `active_engine()`. Added `_new_session_id(engine_name,
    project_name)` (tmux-session-name-and-dict-key generator, same
    timestamp+hex-suffix style as `app/teams.py`'s `_run_id()`), the
    `_sessions` registry (`_sessions_add`/`_sessions_pop`/
    `active_sessions()`, lock-guarded via `_sessions_lock` — same
    "sanctioned access points only" discipline as `_team_threads_set/get/
    pop_if_owned()`).
  - `instance_start(name, engine_name)` now returns the new session's
    `session_id` (or `None` on an unknown engine/project) instead of
    guarding on "already running" — multiplicity is the point; the guard
    moved to the legacy `/on` route only.
  - New `instance_stop_session(session_id)` tears down exactly one session
    (idempotent, tolerant of an already-gone id). `instance_stop(name)` is
    kept only as the legacy bulk-stop backing the `/off` back-compat route
    (loops `active_sessions(name)`, generalized from "every engine" to
    "every session").
  - `_reap_dead_state()`'s ttyd/`_session_urls` sweep is now a direct
    per-session `tmux_has(session_id)` check (`for session_id in
    list(_sessions): if not tmux_has(session_id): instance_stop_session(
    session_id)`), simpler than the old `active_engine()`-driven version.
  - New `_resolve_session_url(session_id, engine_name)` (shared "what URL
    represents this session" logic — `_session_urls` if the engine has
    `url_regex`, else `_ttyd_urls`) and `_latest_session_url_for_project(
    name)` (deterministically the most-recently-started live session's
    resolved URL, by `active_sessions()`'s own insertion-order guarantee).
    `smoke_check_run()`'s single lookup line now calls the latter instead
    of reading `_session_urls[name]` directly.
  - `/status`'s per-project loop now builds a `sessions` array (one entry
    per live session: `session_id`, `engine`, `url`) via `active_sessions(n)`
    + `_resolve_session_url()`, and derives the back-compat `on`/`engine`/
    `url` fields from the last (newest) entry.
  - New routes: `POST /instance/<name>/spawn` (`{engine}` body, same
    fallback-to-default-engine as `/on`, responds `{"ok", "session_id"}`)
    and `POST /instance/<name>/session/<session_id>/stop` (idempotent,
    always `{"ok": true}`, never 404s on an already-gone id, and — added in
    this fix-up pass, see "Deviations from spec" — only actually tears the
    session down when `session_id` is one of `name`'s own live sessions per
    `active_sessions(name)`; otherwise it's a silent no-op, same as the
    already-gone-id case). The legacy `POST /instance/<name>/on`/`off`
    routes are unchanged in behavior (generalized to "any session"/"every
    session" respectively) and kept as the back-compat shim for part 2 to
    remove.
  - Updated a few in-code comments that referenced `active_engine()` by
    name (the reserved-engine-prefix-guard comment, the `import teams`
    ordering comment) so they describe the new scheme instead of a removed
    function.
- `tests/test_teams_headless.py`: `ActiveEngineHeadlessCollisionTests`
  updated to assert `appmod.active_sessions(project_name) == []` instead of
  `appmod.active_engine(project_name) is None` (spec's mandated update);
  also updated `_patch_tmux()`'s docstring reference from `active_engine()`
  to `active_sessions()`.
- `tests/test_smoke_check.py`: `SmokeCheckRunTests.setUp`/`tearDown` now
  monkeypatch `appmod._latest_session_url_for_project` to a direct
  `_session_urls.get` lookup (its own exact pre-spec behavior) for the
  duration of that class — see "Deviations from spec" below for why this
  was necessary.
- `tests/test_session_identity.py` (new): three tiers, matching this
  repo's existing convention (`tests/test_teams_headless.py`,
  `tests/test_smoke_check.py`) — pure-unit tests for `_new_session_id()`
  and the `_sessions` registry; real-tmux integration tests (TMUX patched
  to `["tmux"]`, ttyd's own `_ttyd_start`/`_ttyd_stop` functions swapped
  for fakes that keep the real, pure `_ttyd_port()` allocation and
  `_publish()`/`_unpublish()` no-op behavior but skip the real `sudo -u
  RUN_USER ttyd ...` spawn) for `instance_start`/`instance_stop_session`/
  `instance_stop`/`_reap_dead_state`; and end-to-end HTTP tests against a
  real `ThreadingHTTPServer` for `/spawn`, `/session/<id>/stop`, the legacy
  `/on`/`/off` shim, `/status`'s new `sessions` array and back-compat
  fields, and smoke-check's deterministic newest-session targeting (two
  real local HTTP servers bound to the exact allocated ttyd ports, proving
  which one the check actually hit). Two more end-to-end tests added in
  this fix-up pass (see "Deviations from spec"):
  `test_session_stop_route_rejects_a_session_id_owned_by_a_different_project`
  and `test_session_stop_route_never_reaches_a_real_team_session`.

## Key decisions / tradeoffs
- `_resolve_session_url()`/`_latest_session_url_for_project()` are shared
  by `/status`'s per-session array, its back-compat `url` field, and
  `smoke_check_run()` — one implementation, per the spec's own preference,
  rather than three copies of the same url_regex-vs-ttyd resolution rule.
- `_ttyd_ports` itself is never pruned on session stop (only `_ttyd_procs`/
  `_ttyd_urls` are) — this is the pre-existing, explicitly-accepted
  "ports grow forever" allocator (`docs/ARCHITECTURE.md`, reaffirmed as a
  non-goal here), unchanged except for being keyed by `session_id` instead
  of project name. The new tests assert against `_ttyd_procs`/`_ttyd_urls`
  for teardown, not `_ttyd_ports`, to match this intentionally.
- Test-only ttyd fakery replaces the whole `_ttyd_start`/`_ttyd_stop`
  function pair (not `subprocess.Popen` globally) so real tmux calls
  (`subprocess.run`/`Popen` under the hood) keep working unmodified in the
  same test process — an earlier draft that patched `subprocess.Popen`
  globally broke every real tmux invocation and was corrected before this
  file's tests were finalized (see verification below).

## Deviations from spec
- **`tests/test_smoke_check.py`'s `SmokeCheckRunTests` needed a setup-only
  update, not left "unmodified"** as the acceptance criteria's closing
  bullet asks for everything except `ActiveEngineHeadlessCollisionTests`.
  This is an unavoidable, mechanical consequence of §8's own resolver
  design: `smoke_check_run()` now calls `_latest_session_url_for_project()`,
  which requires a real *live, tracked* session (`_sessions` registry entry
  + `tmux_has()` true) — but `SmokeCheckRunTests`'s 15 pre-existing tests
  each seed a URL by writing `appmod._session_urls["proj"] = srv.url`
  directly, with no tracked session and no tmux session behind it, so they
  could never resolve under the new mechanism (confirmed by running them
  unmodified against the new code: **14 failed/errored** — 11 `FAIL` + 3
  `ERROR` — of the class's 15 tests). Verified this was a real, unavoidable
  conflict — not a mistaken reading — by running the full suite against the
  pre-change baseline and the post-change code and diffing the
  failing-test-name sets; those 14 were the *only* difference, and every
  other pre-existing test (1277 baseline tests) passed identically before
  and after. Fixed with the smallest possible change: `setUp`/`tearDown`
  now monkeypatch `appmod._latest_session_url_for_project` to
  `appmod._session_urls.get` (its own exact pre-spec lookup) for the
  duration of that class only, leaving every individual test body
  (including all `_session_urls["proj"] = srv.url` lines) untouched. The new
  session-registry-backed resolution path itself (a real live session,
  deterministically newest-first) is exercised directly and end-to-end in
  the new `tests/test_session_identity.py::SessionIdentityEndpointTests`.
- **Fix-up pass (post-review): `POST /instance/<name>/session/<session_id>/
  stop` had no ownership check between `session_id` and `name`.** Flagged
  as a must-fix security finding in the first review pass
  (`docs/test-review.md` Finding #1, proven live against a real server: a
  session_id from one project could be torn down by URL-ing a *different*
  project name, and — more severely — a real tmux session literally named
  `team-<project>` (`app/teams.py`'s own deterministic naming) could be
  killed through this route entirely, bypassing `app/teams.py`'s own
  team-stop lifecycle). This was a genuine gap in the original
  implementation, not a spec gap — `docs/spec.md`'s route description
  implies but never states an ownership check, and the original code took
  the caller-supplied `session_id` at face value. Fixed exactly per the
  reviewer's suggested shape: the route now only calls
  `instance_stop_session(session_id)` when `session_id` is one of `name`'s
  own live sessions (`any(s["session_id"] == session_id for s in
  active_sessions(name))`); otherwise it's a silent no-op, folded into the
  same idempotent "already gone" case the route already handled — still
  unconditionally `{"ok": true}`. Two new regression tests added to
  `tests/test_session_identity.py::SessionIdentityEndpointTests`: one
  proving a session_id from project A is rejected/no-op'd (and A's session
  stays alive) when passed under project B's URL, one proving a real
  `team-*`-named tmux session (spawned directly via `tmux new-session`, not
  through this spec's machinery) is never reachable through this route
  regardless of which project name is in the URL.
- **Fix-up pass 2 (post-`/code-review`, independent of the reviewer
  pipeline that already approved this cycle): six bugs fixed, none of them
  spec gaps — all genuine implementation defects against the design the
  spec itself already called for.**
  1. **Resource leak in `instance_start()`** (`app/app.py`): `_sessions_add()`
     was called *before* `subprocess.run(TMUX + ["new-session", ...])`
     completed, not after. A concurrent `/status` poll's
     `_reap_dead_state()` (a separate request thread —
     `ThreadingHTTPServer`) could see the not-yet-real `session_id` in
     `_sessions`, observe `tmux_has()` still false, and call
     `instance_stop_session()` on it — popping it back out of `_sessions`/
     `_session_urls` moments before this function's own `subprocess.run`
     actually finished creating the real tmux session (and, if applicable,
     starting ttyd). The result: a live tmux+ttyd process registered
     nowhere, invisible to `/status`, unstoppable via any route, leaked
     until a process restart. Fixed by moving `_sessions_add()` to after a
     new `tmux_has(session_id)` verification step following
     `subprocess.run` — `instance_start()` now returns `None` (registering
     nothing) if that check fails, closing the race at its source rather
     than narrowing the window. Regression test:
     `InstanceLifecycleRealTmuxTests::test_instance_start_does_not_register_a_session_that_never_came_up`
     (forces `tmux_has` to report false and confirms nothing is ever
     registered).
  2. **Regression: `_reap_dead_state()` dropped the pre-existing independent
     sweep over `_ttyd_urls`/`_ttyd_procs`/`_session_urls`** (`app/app.py`):
     restored as a second, independent loop over
     `set(_ttyd_urls) | set(_ttyd_procs) | set(_session_urls)`, calling
     `instance_stop_session()` for any key whose tmux session is dead —
     same self-healing guarantee the pre-session-identity version of this
     function had (there, keyed by `active_engine()`; the primary sweep
     alone only walks `_sessions`' own registered ids, so any bookkeeping
     entry that drifts outside `_sessions` for any reason — including
     finding #1's race, or any future bug — would otherwise be permanently
     unreachable by self-healing). Regression test:
     `InstanceLifecycleRealTmuxTests::test_reap_dead_state_cleans_orphaned_bookkeeping_not_backed_by_sessions`.
  3. **Lock discipline violated in `_reap_dead_state()`** (`app/app.py`):
     it read `_sessions` via a bare `for session_id in list(_sessions):`,
     unguarded by `_sessions_lock` — violating this module's own stated
     "every mutation and every liveness-deciding read of `_sessions` goes
     through one of the [sanctioned] functions below, nothing else touches
     `_sessions` directly" invariant. Fixed by adding a fourth sanctioned
     accessor, `_sessions_ids()` (lock-guarded snapshot of every registered
     id across all projects), and routing this sweep through it; the
     module-level invariant comment above `_sessions` is updated
     accordingly ("one of the four functions"). Regression test:
     `SessionsRegistryUnitTests::test_sessions_ids_returns_every_registered_id_across_all_projects`.
  4. **Perf: `/status` and `smoke_check_run()` reloaded `load_engines()`
     redundantly** (`app/app.py`): `_resolve_session_url()` did its own
     internal `load_engines()` call on every invocation — a full uncached
     `os.listdir(ENGINES_DIR)`-plus-parse, once *per live session*, even
     though `/status`'s handler already loads `engines` once near the top
     of the request. Fixed by giving `_resolve_session_url()` and
     `_latest_session_url_for_project()` both an optional `engines` param
     (falls back to a fresh `load_engines()` call only when the caller has
     none of its own, preserving the direct-test-call signatures both
     already had), and threading the already-loaded dict through from
     `/status`'s per-session loop and from `smoke_check_run()` (which now
     loads `engines` once itself and passes it down, rather than each
     helper reloading independently for the one lookup it does).
     `tests/test_smoke_check.py::SmokeCheckRunTests`'s
     `_latest_session_url_for_project` monkeypatch (see the deviation
     above) had to be updated in lockstep — a bare `appmod._session_urls.get`
     would otherwise have silently accepted the newly-threaded `engines`
     dict as `dict.get()`'s own `default` argument, returning it verbatim
     as a "URL" (caught immediately by the existing test suite: one
     `AttributeError` from `urlopen()` trying to treat a dict as a URL).
     Replaced with `lambda name, engines=None: appmod._session_urls.get(name)`.
     Regression tests:
     `ResolveSessionUrlEnginesThreadingTests::test_resolve_session_url_does_not_reload_engines_when_a_dict_is_passed`
     (forces a fresh `load_engines()` call to raise, proving the passed
     dict is actually used) and its sibling
     `test_resolve_session_url_falls_back_to_loading_engines_when_none_passed`.
  5. **Test bug: `SessionIdentityEndpointTests.setUp()` cleared the wrong
     global** (`tests/test_session_identity.py`): `appmod.SESSIONS.clear()`
     clears the pre-existing, unrelated login/auth-cookie store
     (`app.py:306`), not this class's own intended target, the `_sessions`
     per-project session-identity registry — two different globals with
     similarly-named module attributes. Intended per-test isolation of
     `_sessions` was silently doing nothing (stale entries accumulated
     across test methods within the class's shared `self.project` scope),
     while unintentionally invalidating any currently-authenticated login
     session elsewhere in the shared `app` module on every test's `setUp`.
     Fixed: `setUp`/`tearDown` now save/clear/restore `appmod._sessions`
     instead (matching the same pattern `SessionsRegistryUnitTests` already
     uses), and `SESSIONS.clear()` is dropped entirely — confirmed no test
     in this class or file depended on that accidental side effect, since
     every test authenticates fresh via `self._authed()` -> `self._login()`
     *after* `setUp` runs, installing its own new cookie regardless of
     `SESSIONS`'s prior contents.
  6. **Minor: stale `active_engine()` reference in `app/teams.py`**
     (`_create_team_session()`'s docstring): updated to reference the
     current guard this codebase actually uses (`app.py`'s legacy `/on`
     route via `active_sessions()`), since `active_engine()` no longer
     exists and `instance_start()` itself no longer carries this
     particular guard (it moved to the `/on` route only, per the original
     spec's point 6).

  All fixes verified: `python3 -m py_compile app/app.py app/teams.py
  tests/test_session_identity.py tests/test_smoke_check.py` clean;
  `tests/test_session_identity.py` (41 tests, 36 original + 5 new for this
  pass), `tests/test_smoke_check.py`, and
  `tests/test_teams_headless.py::ActiveEngineHeadlessCollisionTests` all
  pass (67 tests total across the three files); full suite
  (`python3 -m unittest discover -s tests`) reports `Ran 1318 tests ...
  FAILED (failures=35, errors=79, skipped=42)` — identical failure/error/
  skip tally to the pre-fix-up baseline recorded above (`1313` tests before
  this pass, `+5` new passing tests, same 9 pre-existing/environmental
  failing files, confirmed by diffing the failing-test-name set against the
  prior run: no new failures, no new errors, nothing newly passing that
  wasn't already passing).
- No other deviations — routes, JSON shapes, `_reap_dead_state()`'s sweep,
  and the back-compat `/on`/`/off` semantics all match the spec's
  "Proposed approach" code blocks essentially verbatim.

## Known limitations
- Same limitations the spec explicitly accepts as non-goals: no per-session
  git-worktree isolation (concurrent sessions for one project still share
  the single working copy — a real concurrent-write-conflict risk, called
  out but not mitigated), no admission control/session-count cap, and
  `_ttyd_ports`/`_next_ttyd_port`/`_next_code_port` still never reclaim a
  port over a long uptime (pre-existing, unchanged, re-keyed only).
- The frontend's checkbox toggle still only ever shows/controls "the
  newest session" (via the back-compat `on`/`engine`/`url` fields) — a
  project with 2+ concurrent sessions has no visible way from today's UI
  to see or stop the others until part 2 lands.

## How to verify locally
1. `python3 -m py_compile app/app.py app/teams.py` — no syntax errors.
2. `python3 -m unittest discover -s tests -v` (or `python3 -m unittest
   tests.test_session_identity tests.test_smoke_check
   tests.test_teams_headless -v` to just run what this cycle touched) —
   all of `tests/test_session_identity.py` (41 tests — 34 from the
   original cycle, +2 from the first fix-up pass, +5 from the second
   fix-up pass documented above), all of `tests/test_smoke_check.py`, and
   `tests/test_teams_headless.py`'s `ActiveEngineHeadlessCollisionTests`
   pass (67 tests total across the three files). Confirmed via a
   full-suite run against the current working tree: `Ran 1318 tests ...
   FAILED (failures=35, errors=79, skipped=42)` — the same 35/79/42
   pre-existing tally as the unmodified `main` baseline (`1277` tests),
   confirmed genuinely pre-existing/environmental (e.g.
   `test_team_routes.py`'s failures are `git commit` exit-128s from no
   global `git user.email`/`user.name` in this sandbox), plus 41 new
   passing tests (1277 → 1318 total). The pre-existing failures/errors
   span **9 files**, not just two:
   `test_gitea_sync_project` (×5), `test_new_project_from_gitea` (×6),
   `test_new_project_from_upload` (×4), `test_new_project_from_url` (×12),
   `test_taiga_push` (×1), `test_team_routes` (×47), `test_teams_grounding`
   (×3), `test_teams_lead` (×2), `test_teams_lifecycle` (×34) —
   `test_team_routes.py`/`test_teams_lifecycle.py` account for the large
   majority.
3. Manual/live check (needs real tmux + `TOTP_SECRET`/`AUTH_MODE` env,
   same as any local run): `POST /instance/<name>/spawn` with `{"engine":
   "claude", "code": "<totp>"}` twice in a row for the same project — both
   calls return distinct `session_id`s, `tmux has-session -t <id>` is true
   for both, and `GET /status`'s `sessions` array for that project lists
   both. `POST /instance/<name>/session/<id>/stop` on one of them leaves
   the other running (`tmux has-session` still true, still in `/status`).
   The old checkbox UI (`POST .../on`, `.../off`) still works unmodified.
