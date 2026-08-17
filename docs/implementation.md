# Implementation: Concurrent sessions per project — part 2: "+" control and per-session list UI

## Summary
Replaced the single on/off checkbox on each project's `kind === 'inst'`
dashboard row with an always-visible engine picker + "+ Start session"
button and a per-session list, each entry independently stoppable —
consuming part 1's `POST /instance/<name>/spawn` and `POST /instance/<name>/
session/<id>/stop`. Removed the now-unused back-compat shim from part 1
entirely: the `/instance/<name>/on`/`off` routes, `instance_stop()`, and
`/status`'s singular `on`/`engine`/`url` per-project fields are all gone.
Host/Taiga/Gitea rows are untouched (still a checkbox).

## Changes by file
- `app/app.py`:
  - `engineRow(name)` (was `engineRow(name, on, engine)`): dropped the
    `if (on) { Running badge }` branch entirely — now always renders the
    pill picker, since "what to spawn next" is relevant regardless of what's
    already running. The "+ Start session" button (`.session-spawn-btn`,
    dispatches `toggle('session-spawn', name, true, null)`) is appended
    inside this same function, sharing its existing empty-roster guard
    (`if (names.length === 0) return '';`) so the button is omitted
    whenever the picker would be.
  - New `sessionsRow(name, sessions)`: one `.session-item` per entry in
    `sessions` (engine badge, "open" link or "starting…" placeholder, a
    `.session-stop-btn` calling `stopSession(name, s.session_id)`). Returns
    `''` when `sessions` is empty (no session list container at all, not an
    empty one).
  - New `instSessionsSub(sessions)`: the row's own "sub" text for a
    multi-session project — `'stopped'` (0 sessions), `'running'` (≥1
    session, newest has no URL yet), or `'running — newest: <a>open</a>'`
    (newest has a URL) — computed in `refresh()` and passed through as
    `subOverride`, the same plumbing `singletonToggleSub()` already
    supplies for Taiga/Gitea.
  - New `stopSession(name, sessionId)` + new module-level
    `pendingSessionStop = {}`: sets the side-channel value *before*
    `toggle('session-stop', name, true, null)` fires, following
    `teamAddMemberChoice`'s exact precedent so it survives a 428-then-retry
    round trip. Cleared in `handleActionResult()`'s generic fallback path
    (`if (kind === 'session-stop') delete pendingSessionStop[name];`).
  - `row()`: 7th positional param renamed `engine` → `sessions` (the
    `engine` param was only ever read by `engineRow()`, which no longer
    needs it). Added `(kind === 'inst' ? sessionsRow(name, sessions) : '')`
    right after the `.sub` div. The checkbox `<label class="switch">...`
    block is now gated on `kind !== 'inst'` — Host/Taiga/Gitea keep it
    unconditionally; `inst` rows render nothing there.
  - `actionPath()`/`actionBody()`: added `'session-spawn'` →
    `/instance/<name>/spawn` (body: `{engine: engineChoice[name] ||
    Object.keys(ENGINE_LABELS)[0]}`, the same shape the old `kind ===
    'inst'` branch used to build) and `'session-stop'` →
    `/instance/<name>/session/<pendingSessionStop[name]>/stop` (no extra
    body fields). Removed the old catch-all fallback line
    (`/instance/<name>/(on|off)`) — it was only ever reached by the now-gone
    `'inst'` toggle kind.
  - `handleActionResult()`'s 428 code-overlay label switch: added
    `'session-spawn'` → `'Starting a session: <name>'` and `'session-stop'`
    → `'Stopping session: <name>'` (both dispatch with `on=true`, so without
    their own case Stop would misleadingly show "Turning on: ...").
  - `refresh()`: reads `inst.sessions` (part 1's real array) instead of the
    removed `inst.on`/`inst.url`/`inst.engine`. Computes `newestUrl` (the
    last session's `url`, or `null`) client-side and passes it through
    `row()`'s existing `url` param — this is what still gates
    `smokeCheckRow()`'s visibility (spec's Non-goals: smoke-check stays
    project-level, targeting the newest session).
  - New CSS: `.sessions-list`/`.session-item`/`.session-status`/
    `.session-stop-btn` (new rules, per docs/design.md's exact property
    values), plus `.session-spawn-btn` added to the existing shared
    `.deploy-btn, .team-btn { ... }` selector (same "own class, shared
    shape" precedent `.team-btn`'s own comment already establishes).
  - Backend cleanup (§6): removed the `POST /instance/<name>/on|off` route
    branch, removed `instance_stop()` (only ever backed that route), and
    removed `on`/`engine`/`url` from `/status`'s per-project JSON — only
    `sessions`/`desc`/`code_on`/`code_url`/(`deploy`/`gitea_sync`/`team`
    when present) remain. Updated a few docstrings/comments
    (`active_sessions()`, `instance_start()`) that referenced the
    since-removed shim/fields.
- `tests/test_multi_session_frontend.js` (new): the canonical
  `kind='inst'`-row frontend test file the spec calls for — 13 tests
  covering all of the spec's acceptance criteria (0-session/0-engine and
  0-session/≥1-engine states, 2-session independent Stop, the removal of
  session B leaving session A's own open-link untouched, "starting…" for a
  URL-less session, both multi-session sub-text variants, the spawn
  dispatch body, the 428/TOTP retry path for both spawn and stop —
  including `pendingSessionStop` surviving the retry — and Host/Taiga/Gitea
  keeping exactly 3 checkboxes unaffected). Same `vm`-based real-`<script>`-
  extraction technique as `tests/test_deploy_frontend.js`/`tests/
  test_smoke_check_frontend.js`.
- `tests/test_smoke_check_frontend.js`: fixtures updated from the removed
  `on`/`url`/`engine` shape to `sessions: [{session_id, engine, url}]` (or
  `sessions: []`) — confirmed via a real run that these fixtures previously
  relied on the exact fields this cycle deletes (3 of 11 tests failed before
  this fix, since `smokeCheckRow()` no longer had any URL to gate on).
- `tests/test_deploy_frontend.js`: re-run, confirmed still passes
  unmodified (`deployRow()`'s visibility gates on `deploy`, never `on`/
  `url`/`engine`/`sessions`, so the now-stale `on`/`url`/`engine` fields in
  its fixtures are simply unread, harmless extra JSON properties — left as
  is per minimal-diff discipline, since touching an already-passing file
  isn't required).
- `tests/test_session_identity.py` (part 1's own backend test file):
  removed the tests that exercised the now-deleted surface —
  `test_legacy_instance_stop_*` (2, Tier 2), `test_legacy_on_route_*`/
  `test_legacy_off_route_*` (4, Tier 3), and
  `test_status_back_compat_fields_reflect_the_most_recently_started_session`
  (1, Tier 3) — and added 3 replacements that assert the removal itself:
  `test_legacy_on_route_no_longer_exists`/`test_legacy_off_route_no_longer_exists`
  (both now 404) and
  `test_status_no_longer_includes_the_removed_back_compat_fields` (asserts
  `on`/`engine`/`url` are absent from `/status`'s per-project JSON, matching
  the spec's own acceptance criterion). Updated the file's docstring and a
  couple of in-class docstrings that named `instance_stop()`/the legacy
  shim. Net: 36 → 32 tests in this file (all still passing).

## Key decisions / tradeoffs
- **`engineRow()`'s signature was simplified**, not literally preserved.
  docs/design.md's "Component reuse" section says the signature "stay[s]
  identical", but the `on`/`engine` params become fully dead once the
  `if (on)` branch is gone — keeping two permanently-unused parameters
  (and having every call site pass meaningless placeholder values just to
  satisfy them) is worse for a future reader than dropping them. The
  function's *behavior* matches every state/wireframe in docs/design.md
  exactly; only the parameter list shrank. Recorded here as the one place
  I deviated from design.md's literal wording (see "Deviations from
  spec/design" below).
- **"+ Start session" is emitted by `engineRow()` itself**, not a separate
  render function. The spec describes it as "a button next to/below the
  engine picker" and both share the identical empty-roster guard (AC 5) —
  one function, one guard, is simpler than threading the same `names.length
  === 0` check through two call sites, and matches the wireframes' layout
  (button inline with the picker row).
- **`.session-spawn-btn` is its own CSS class**, not a literal
  `class="deploy-btn"` reuse, even though docs/design.md's wording says the
  button "reuses `.deploy-btn`/`.team-btn`'s exact class and styling". This
  follows the codebase's own pre-existing, explicitly-documented convention
  (see `.team-btn`'s own comment: "shares this shape byte-for-byte... but
  is its OWN class") — reusing the *shared CSS rule* while keeping every
  button role's own class name, exactly like `.team-btn` and `.smoke-btn`
  already do.
- **`name`/`session_id` are not `esc()`-escaped inside onclick attribute
  strings** in `sessionsRow()`, even though docs/design.md's own inline
  snippet shows `esc(name)`/`esc(s.session_id)`. Every existing onclick
  call site in this file (`pickEngine`, `doDeploy`, `doTeamAddMember`,
  `doSmokeCheck`, etc.) follows the same "no `esc()` for `name` in an
  onclick string" convention — matching that established, file-wide
  pattern beats introducing a one-off exception, especially since
  docs/design.md itself flags its own snippet as "one reasonable shape...
  not implementation code".
- **`row()`'s 7th positional param was repurposed** (`engine` → `sessions`)
  rather than appended as a new trailing param or refactored into an
  options object. This kept every non-`inst` call site (`host`/`taiga`/
  `gitea`, which already pass `null` in that slot) byte-for-byte unchanged,
  satisfying the spec's explicit "keep the three non-inst call sites'
  behavior byte-for-byte unchanged" requirement with the smallest possible
  diff.
- **`newestUrl` (for `smokeCheckRow()`'s gate) is computed client-side in
  `refresh()`** from `inst.sessions[inst.sessions.length - 1].url`, rather
  than adding a new backend-resolved field. `active_sessions()`'s own
  insertion-order guarantee (oldest-first) is exactly what the backend's
  own `_latest_session_url_for_project()` already relies on, so mirroring
  it client-side is a one-line computation, not a new field the spec's own
  "only sessions/desc/code_on/code_url/deploy/gitea_sync/team" acceptance
  criterion would have to special-case.

## Deviations from spec
- docs/design.md's "signature and return structure stay identical" note
  for `engineRow()` was not followed literally — see "Key decisions" above.
  Functionally, every state/wireframe/acceptance criterion the design
  describes for the engine picker + spawn button is implemented exactly as
  specified; only the now-dead `on`/`engine` parameters were dropped.
- No other deviations. `smokeCheckRow` continues to receive a single
  resolved `url` targeting the newest session (spec's Non-goals — not made
  session-scoped). The "Open questions" in docs/spec.md (smoke-check
  per-session scoping, exact visual treatment) were both left as the
  spec's own stated defaults — no low-risk addition was obvious enough to
  fold in without complicating the row layout, matching the spec's own
  guidance not to add it speculatively.

## Known limitations
- Same non-goals the spec explicitly accepts: no per-session git-worktree
  isolation, no configurable max-session-count/resource-limit UI, no
  pagination/collapse for a project with many (5+) concurrent sessions.
- Smoke-check stays project-level (targets the newest session only) — a
  project with several concurrent sessions has no way to smoke-check a
  specific non-newest one from this UI.
- Engine pills remain a `<span onclick>` pattern (not a real focusable
  control) — a pre-existing accessibility gap on this page docs/design.md
  explicitly leaves alone, unrelated to this cycle's own change.

## How to verify locally
1. `python3 -m py_compile app/app.py` — no syntax errors.
2. Frontend tests (plain Node, no dependencies — this sandbox had no
   system-wide `node`; a portable Node 20 tarball was used instead, any
   local Node 18+ works the same way):
   ```
   node tests/test_multi_session_frontend.js   # new, 13/13 pass
   node tests/test_smoke_check_frontend.js     # 11/11 pass (fixtures updated)
   node tests/test_deploy_frontend.js          # 9/9 pass (unmodified)
   node tests/test_singleton_toggle_frontend.js  # 19/19 pass (Host/Taiga/Gitea unaffected)
   node tests/test_team_frontend.js            # 115/115 pass
   node tests/test_clone_frontend.js           # 8/8 pass
   node tests/test_upload_frontend.js          # 8/8 pass
   ```
3. `python3 tests/test_session_identity.py` — 32/32 pass (confirms
   `/instance/<name>/on|off` now 404, `/status` no longer includes `on`/
   `engine`/`url`, and every part-1 spawn/stop/session-liveness behavior
   this cycle didn't touch still works).
4. Full existing suite, for a no-regressions check:
   `python3 -m unittest discover -s tests` → `Ran 1309 tests ... FAILED
   (failures=35, errors=79, skipped=42)` — the exact same 35/79/42
   pre-existing failure/error/skip tally across the exact same 9 files
   (`test_team_routes` ×47, `test_teams_lifecycle` ×34,
   `test_new_project_from_url` ×12, `test_new_project_from_gitea` ×6,
   `test_gitea_sync_project` ×5, `test_new_project_from_upload` ×4,
   `test_teams_grounding` ×3, `test_teams_lead` ×2, `test_taiga_push` ×1)
   documented as pre-existing/environmental in part 1's own
   `docs/implementation.md` (confirmed here again by running the same
   files against an untouched `git stash` of this tree — identical
   failures either way). Test count dropped from 1313 → 1309 (net -4, from
   trimming `test_session_identity.py`'s now-obsolete legacy-shim tests
   while adding 3 replacements — see "Changes by file").
5. Manual/live check (needs real tmux + `TOTP_SECRET`/`AUTH_MODE`, same as
   any local run): load the dashboard, confirm a project with 0 sessions
   shows the engine picker + "+ Start session" button and no checkbox;
   click "+ Start session" for two different engines, confirm both appear
   in the session list with independent Stop buttons and (once each
   engine's own URL is captured) independent "open" links; click Stop on
   one, confirm only it disappears on the next 4s poll while the other's
   own state is untouched; confirm Host/Taiga/Gitea rows (if enabled)
   still show their checkbox exactly as before.

## Fix-up round: `pendingSessionStop` overwrite on concurrent Stop clicks (hotfix/ad-9)

An independent `/code-review` pass found a real bug on top of the already
reviewer-approved cycle above: `pendingSessionStop[name]` was a single value
(`name -> one session_id`), following `team-add-member`'s own side-channel
precedent — but unlike `team-add-member` (nothing in the UI lets you queue a
second "add" before the first resolves), nothing disabled a project's *other*
sessions' Stop buttons while one session's Stop was already sitting on the
shared 428/TOTP code overlay. Scenario: project `proj` has sessions A and B
running. Click Stop on A → `stopSession()` sets `pendingSessionStop['proj'] =
'A'`, fires the POST, server responds 428 (first mutating action in this
browser session, TOTP required), code overlay shows ("Stopping session:
proj" — no session id shown, by design). Before typing the code, click Stop
on B → `stopSession()` **overwrites** `pendingSessionStop['proj'] = 'B'`,
fires a second POST, also gets 428. The user types one code, submits once:
`submitActionCode()` re-read `pendingSessionStop[name]` at retry time — now
`'B'` — and retried *only* B's stop. A's original (no-code) POST had already
gotten its own 428 and was never automatically retried once its own
session_id was clobbered, so A's stop was silently dropped: the session kept
running with no error shown to the operator. The `tests/
test_multi_session_frontend.js` test in place before this round ("a 428
mid-stop...") only ever exercised a single-session project and never
triggered the interleaving.

### Root cause
Two separate problems compounded:
1. `actionPath()`'s `'session-stop'` case re-read `pendingSessionStop[name]`
   **at fetch time** (both for the original dispatch and, implicitly, for
   any retry), instead of using the specific session_id each individual
   click actually meant to target. A later click's write silently redirected
   an earlier click's still-in-flight request.
2. `submitActionCode()`'s retry path fires exactly **one** `performAction()`
   call per code submission, driven by the single global `pendingToggle`
   context — there was no way for a second, concurrent 428 (same kind, same
   project, different session) to be remembered as "also still pending" once
   `pendingToggle` had already been claimed by the first.

### Fix
- `pendingSessionStop[name]` is now an **array** (a per-project queue of
  session ids that have an in-flight-or-TOTP-pending stop request), not a
  single value. `stopSession()` pushes (deduped) onto the queue instead of
  overwriting it.
- The session_id each individual dispatch actually targets is now threaded
  **explicitly** through the call chain — `toggle(kind, name, on,
  checkboxEl, sessionId)` → `performAction(kind, name, on, code, sessionId)`
  → `actionPath(kind, name, on, sessionId)` — captured once, synchronously,
  at the moment `stopSession()` is called (before any other click handler
  can run, since JS is single-threaded up to the next `await`). `actionPath`
  no longer reads `pendingSessionStop[name]` at all for URL-building; the
  dict is now purely bookkeeping for "which ids are still owed a retry."
- New `submitSessionStopRetries(name, on, code)`, called from
  `submitActionCode()` instead of the single generic `performAction()` call
  whenever `pendingToggle.kind === 'session-stop'`: walks the *entire*
  `pendingSessionStop[name]` queue and retries every still-queued session_id
  with the one code the operator typed, sequentially (not
  `Promise.all`/parallel — TOTP is validated once per browser session
  server-side, so a 403 on the first retry means every other queued id would
  fail identically; the loop stops immediately on a 403 rather than firing
  the rest, leaving the whole queue intact for a correct retry). A 401 (full
  re-auth needed) aborts the batch the same way `handleActionResult()`'s own
  401 branch does elsewhere, leaving every remaining id queued. Each
  successfully-resolved id (success or a non-retryable 4xx like an
  already-gone session) is removed from the queue as it resolves.
- `handleActionResult()`'s generic-fallback cleanup (used when a stop
  succeeds *without* ever needing a TOTP retry) now removes only its own
  `ctx.sessionId` from the queue, not the whole project's entry — so two
  concurrent, TOTP-already-cleared stops on the same project don't clobber
  each other's bookkeeping either.
- `cancelActionCode()` now clears the whole `pendingSessionStop[name]` entry
  for a canceled session-stop overlay (nothing was actually stopped
  server-side yet, per the existing 428-before-touching-anything contract
  every other kind's own cancel-branch already relies on), so a later,
  unrelated code retry can't accidentally resurrect an abandoned stop.

This is the "hold more than one pending action at a time" option from the
bug report's two suggested fix shapes (rather than "reject/queue a second
Stop click with a UI signal") — it fully resolves both sessions from a
single code entry with no extra click required from the operator, and only
touches the `session-stop` kind's own code paths (the shared `pendingToggle`/
`toggle()`/`performAction()` plumbing every other kind uses is otherwise
unchanged; `toggle()`'s new `sessionId` parameter is optional and `undefined`
for every kind besides `session-stop`).

### Regression tests added (`tests/test_multi_session_frontend.js`, now 15 tests)
- *"two concurrent Stop clicks on different sessions of the SAME project,
  both pending on the same shared 428/TOTP overlay, are BOTH retried by a
  single code submission -- neither is silently dropped"* — reproduces the
  bug's exact scenario (Stop A → 428, Stop B → 428, submit one code) and
  asserts both `/instance/proj/session/claude-proj-A/stop` and `/instance/
  proj/session/codex-proj-B/stop` are retried with the typed code, in
  sequence.
- *"a wrong TOTP code during a two-session-stop retry batch leaves BOTH
  sessions queued for another attempt (no partial silent drop)"* — asserts a
  403 on the first retry doesn't fire the second, and that a subsequent
  correct-code submission still retries both.
- Verified both new tests actually catch the bug: temporarily reverted only
  `app/app.py` (`git stash push -- app/app.py`, keeping the new test file)
  and re-ran — both new tests failed (`session A's retry must not be
  dropped` / wrong session retried first) exactly as the bug report
  describes; restored the fix (`git stash pop`) and re-ran clean.

### Verification (this round)
- `python3 -m py_compile app/app.py` — no syntax errors.
- `node --check <extracted <script>>` — no syntax errors in the rendered
  page script (same portable-Node-20 setup as the original cycle).
- `node tests/test_multi_session_frontend.js` — **15/15 pass** (13 existing
  + 2 new).
- All other frontend test files re-run, unmodified, no regressions:
  `test_clone_frontend.js` 8/8, `test_deploy_frontend.js` 9/9,
  `test_singleton_toggle_frontend.js` 19/19, `test_smoke_check_frontend.js`
  11/11, `test_team_frontend.js` 115/115, `test_upload_frontend.js` 8/8.
- `python3 tests/test_session_identity.py` — 37/37 pass (this file grew from
  32 to 37 between this round and the original cycle via the intervening
  team-launcher hotfix round already merged into this branch; unrelated to
  this fix).
- Full suite: `python3 -m unittest discover -s tests` → `Ran 1314 tests ...
  FAILED (failures=35, errors=79, skipped=42)` — the exact same 35/79/42
  tally, across the exact same 9 pre-existing/environmental files
  (`test_team_routes`, `test_teams_lifecycle`, `test_new_project_from_url`,
  `test_new_project_from_gitea`, `test_gitea_sync_project`,
  `test_new_project_from_upload`, `test_teams_grounding`,
  `test_teams_lead`, `test_taiga_push`) already documented above. Test count
  is 1314 (1309 documented above + 5 from the intervening team-launcher
  hotfix round's own additions to `test_session_identity.py`-adjacent
  suites, + this round's own 2 new frontend tests, which aren't part of this
  Python-only count) — no new failures/errors anywhere in this run.

### Known limitation carried forward
The same class of "single shared `pendingToggle` slot" issue could in
principle also occur across **different projects** (e.g. Project X's spawn
and Project Y's stop both 428'ing before either code is typed) — out of
scope for this fix-up, which is scoped exactly to the bug report's "two
sessions of the same project" case. `pendingToggle` itself remains a single
global for every other kind, unchanged; only `session-stop`'s own queue
(`pendingSessionStop`) was generalized to hold more than one entry.
