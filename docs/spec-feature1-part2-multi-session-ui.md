# Spec: Concurrent sessions per project — part 2: "+" control and per-session list UI

## Routing note (read first)
**Workflow: `workflows/feature.md`. Queued after `docs/spec.md` (part 1,
backend).** This part **does** need ux-designer — it replaces a boolean
checkbox with a new list-of-sessions layout, a real new visual pattern for
the `kind === 'inst'` row. Do not build this before part 1 has landed
(it depends on part 1's `POST /instance/<name>/spawn`, `POST /instance/
<name>/session/<id>/stop`, and `/status`'s new `sessions` array all
existing and working).

This file is written now, alongside part 1 and the feature-2 spec, as a
complete ready-to-execute plan — per the product-manager's own "don't stop
at a pitch" convention — but is **not** the currently-active
`docs/spec.md`. When part 1 is reviewer-approved, promote this file's
content into `docs/spec.md` (copy over, refresh any line numbers that
shifted, overwrite) for the next build cycle.

## Summary
Replace the single on/off checkbox on each project's dashboard row with a
"+ Start session" control (engine picker, always available) and a list of
that project's currently-running sessions, each independently stoppable —
consuming part 1's new backend surface — and remove the now-unused
back-compat shim (old `/on`/`/off` routes and `/status`'s singular `on`/
`engine`/`url` fields).

## Goals
- The `kind === 'inst'` row (per-project dashboard row) no longer renders
  a `<label class="switch"><input type="checkbox" ...>` toggle. Host/
  Taiga/Gitea singleton-toggle rows (`kind` `host`/`taiga`/`gitea`) are
  **unaffected** — they keep their existing checkbox unchanged.
- An always-visible engine picker + "+ Start session" button (reusing the
  existing pill-picker visual pattern from `engineRow()`,
  `app/app.py:3401-3415`) that calls `POST /instance/<name>/spawn`.
- A session list: one entry per item in that project's `/status` `sessions`
  array, showing its engine label, an "open" link when a URL is captured
  (else a "starting…" placeholder — mirroring the existing `sub` text
  convention `'running — <a>open</a>'` / `'running'`), and a "Stop"
  button that calls `POST /instance/<name>/session/<session_id>/stop` for
  that specific session only.
- All new mutating controls (spawn, stop) go through the exact same
  TOTP-retry/code-overlay plumbing (`pendingToggle`, `submitActionCode()`,
  `performAction()`/`handleActionResult()`) every other action on this
  page already uses — no parallel/duplicate implementation.
- Cleanup: remove the part-1 back-compat shim entirely once nothing in the
  frontend calls it — old `/instance/<name>/on`/`/off` routes deleted from
  `app/app.py`, `/status`'s singular `on`/`engine`/`url` fields removed
  from the per-project JSON (keep `desc`/`code_on`/`code_url`/`deploy`/
  `gitea_sync`/`team`, all still project-level and unaffected).

## Non-goals
- No per-session git worktree isolation (carried over from part 1's own
  non-goal — this is a UI-layer spec, doesn't reopen that decision).
- No configurable maximum session count / resource-limit UI.
- No change to `codeRow()`/`deployRow()`/`smokeCheckRow()`/`teamRow()`'s
  own project-level (not per-session) behavior, beyond `smokeCheckRow`
  continuing to receive a single resolved `url` (part 1's "newest session"
  resolver) — whether smoke-check becomes session-scoped is an explicit
  open question below, not decided/built here either, unless the ux-
  designer's pass concludes it's trivial to add and low-risk; default
  assumption is **no** (keep smoke-check project-level, targeting the
  newest session, exactly as part 1 left it).
- No visual redesign of the row's other sections (description, deploy row,
  team row) — only the on/off checkbox area changes shape.

## Background / current state
Assumes part 1 (`docs/spec.md` as currently written) has shipped:
`POST /instance/<name>/spawn`, `POST /instance/<name>/session/<id>/stop`,
and `/status`'s per-project `sessions: [{session_id, engine, url}, ...]`
array all exist and work, alongside the temporary back-compat `on`/
`engine`/`url` singular fields and the old `/on`/`/off` routes (all to be
removed here).

### Current frontend, precisely
- `refresh()` (`app/app.py:3354-3392`): loops `s.instances`, calls
  `row(inst.name, inst.on, inst.url, 'inst', inst.name, inst.desc,
  inst.engine, inst.code_on, inst.code_url, ...)` — reads exactly the
  singular fields part 1 marked as back-compat-only.
- `row()` (`app/app.py:4485-4510`): for `kind === 'inst'`, renders
  `engineRow(name, on, engine)` then, at the end, an unconditional
  `<label class="switch"><input type="checkbox" ... onchange="toggle(arg,
  this.checked, this)">` — this checkbox is shared code with `host`/
  `taiga`/`gitea` rows (`arg` and the `toggle()` call are kind-agnostic);
  only the `kind === 'inst'` case needs its own replacement block, the
  shared checkbox markup for the other three kinds must stay exactly as-
  is.
- `engineRow(name, on, engine)` (`app/app.py:3401-3415`): today branches
  on `on` — shows a "Running" badge when true, an engine picker (pills,
  `engineChoice[name]` selection state) when false. This needs to become
  **always** the engine-picker (no more "already running, hide the
  picker" branch — multiplicity means the picker for "what to spawn
  next" is relevant regardless of what's already running).
- `actionPath()`/`actionBody()` (`app/app.py:4514-4529`/`4530+`): kind-
  keyed dispatch table already handles kinds needing no extra state
  (`'code'`, `'deploy'`, etc.) and kinds needing extra state threaded via
  a side-channel module-level variable rather than `toggle()`'s own
  `name`/`on`/`checkboxEl` params, which have no slot for it — see
  `team-add-member`'s existing precedent (`app/app.py:4366-4396`,
  `4538-4539`'s own comment: "rather than threading url/name through
  toggle()'s own name/on/checkboxEl parameters (which don't have a slot
  for a second [value])"). Per-session Stop needs exactly this pattern
  (the extra value being *which* `session_id* to stop).
- `toggle(kind, name, on, checkboxEl)` (`app/app.py:4850+`): the shared
  entry point for every mutating action on the page, handling the TOTP
  code-overlay retry flow generically over `kind`/`name`. Reused as-is.
- No dedicated frontend test file exists yet for `kind === 'inst'` rows —
  confirmed via `tests/test_deploy_frontend.js:190-193` and `tests/
  test_smoke_check_frontend.js:195-198`'s own comments, both explicitly
  noting there's no earlier canonical `kind='inst'`-row test file to
  follow. This spec's own new test file becomes that canonical file.

## Proposed approach

### 1. `engineRow()` → always the picker
Drop the `if (on) { ... Running badge ... }` branch entirely; always
render the pill-picker (`engineChoice[name]` selection state, unchanged).
Rename if it reads better once its purpose is "pick what to spawn next"
rather than "pick what to start" (developer/ux-designer's call — not a
functional change either way).

### 2. New "+ Start session" control
A button next to/below the engine picker, calling `toggle('session-
spawn', name, true, null)`. `actionPath('session-spawn', name, ...)` →
`/instance/<name>/spawn`. `actionBody('session-spawn', name, ...)` → same
`{engine: engineChoice[name] || Object.keys(ENGINE_LABELS)[0]}` shape
`actionBody`'s existing `kind === 'inst'` branch already builds
(`app/app.py:4533`) — just re-keyed to the new kind. Button always
enabled when `Object.keys(ENGINE_LABELS).length > 0` (mirrors
`engineRow()`'s own existing empty-roster guard,
`app/app.py:3404-3405`); omitted entirely when no engines are configured
at all, same as today.

### 3. New session list
New render function, e.g. `sessionsRow(name, sessions)`:
```js
function sessionsRow(name, sessions) {
  if (!sessions || sessions.length === 0) return '';
  return '<div class="sessions-list">' + sessions.map(s =>
    '<div class="session-item">' +
      '<span class="badge">' + esc(ENGINE_LABELS[s.engine] || s.engine) + '</span>' +
      (s.url ? ' <a href="' + s.url + '" target="_blank">open</a>' : ' starting…') +
      ' <button onclick="stopSession(' + "'" + name + "','" + s.session_id + "'" + ')">Stop</button>' +
    '</div>').join('') + '</div>';
}
function stopSession(name, sessionId) {
  pendingSessionStop[name] = sessionId;
  toggle('session-stop', name, true, null);
}
```
`pendingSessionStop` is a new module-level `{}` (same idiom as
`teamAddMemberChoice`/other side-channel state already in the file).
`actionPath('session-stop', name, ...)` → `/instance/<name>/session/' +
encodeURIComponent(pendingSessionStop[name]) + '/stop'`. Exact CSS class
names/visual treatment are ux-designer's call (`docs/design.md`) — the
above is the functional shape, not the final styling.

### 4. `row()`'s `kind === 'inst'` checkbox removed
The trailing `<label class="switch">...</label>` block
(`app/app.py:4509-4510`) becomes conditional: rendered as today for
`host`/`taiga`/`gitea`, and for `kind === 'inst'` replaced by nothing (the
engine-picker + spawn button + session list, inserted earlier in the row
alongside `engineRow()`'s own existing call site, cover its role
entirely). Concretely: gate the existing checkbox markup on `kind !==
'inst'`, and add the new spawn/session-list block inside the existing
`(kind === 'inst' ? ... : '')` conditional chain already present in
`row()` right next to `engineRow()`'s own call.

### 5. `refresh()` updated
Read `inst.sessions` (part 1's new array) instead of `inst.on`/`inst.url`/
`inst.engine`; pass it through to `row()` in place of those three
params (signature change — update every call site, including the `host`/
`taiga`/`gitea` calls which pass `null`/fixed values for the now-removed
positional params, or refactor `row()`'s signature to an options object if
that's cleaner — developer's call, but keep the three non-`inst` call
sites' behavior byte-for-byte unchanged).

### 6. Backend cleanup
Delete `POST /instance/<name>/on` and `/off`'s route branches
(`app/app.py:6417-6428`), `instance_stop()` (only ever existed to back
that route — confirm nothing else calls it before deleting; if
`instance_stop_session`/the `/off`-shim loop from part 1 already
subsumed it, this may already be dead by the time this cycle starts).
Delete `/status`'s three back-compat fields (`on`/`engine`/`url` on the
per-project object) once `refresh()` no longer reads them.

## Affected areas
- `app/app.py`: `engineRow()`, `row()`, `refresh()`, `actionPath()`/
  `actionBody()` (new `session-spawn`/`session-stop` kinds), new
  `sessionsRow()`/`stopSession()` functions, new `pendingSessionStop`
  state, `<style>` additions for `.sessions-list`/`.session-item` (per
  ux-designer's `docs/design.md`), backend route cleanup (§6 above).
- New `tests/test_multi_session_frontend.js` — Node `vm`-based extraction
  of the real rendered `<script>` from `app.render_page()`, same technique
  as `tests/test_singleton_toggle_frontend.js`/`tests/
  test_team_frontend.js`. This becomes the canonical `kind='inst'`-row
  frontend test file (see "Background" — none exists yet).
- `tests/test_deploy_frontend.js`/`tests/test_smoke_check_frontend.js`:
  both explicitly note (per their own comments cited above) that they
  render a `kind='inst'` row as a side effect — re-run after this change
  to confirm they still pass against the new row shape (no checkbox
  assumed in either file today, per a quick read, but must be confirmed,
  not assumed).
- `docs/implementation.md` — developer's usual write-up.

## Edge cases
- **Zero sessions running, zero engines configured** — no spawn control,
  no session list, row shows only the description/other project-level
  rows (deploy/team/etc.) — matches `engineRow()`'s existing empty-roster
  guard behavior.
- **Zero sessions running, ≥1 engine configured** — spawn control shown,
  session list area empty/omitted (not an empty list with a header and no
  rows).
- **Many sessions (e.g. 5+) for one project** — list simply grows; no
  pagination/collapse required by this spec (not requested, not a
  correctness issue, revisit only if it becomes a real problem later).
- **A session with no captured URL yet** (still starting, or a `url_regex`
  engine that hasn't printed its link) — "starting…" placeholder, not a
  broken/empty link.
- **Stopping session A while session B (different engine) keeps running**
  — B's row must not flicker/re-render incorrectly; `refresh()`'s full
  `#rows`-innerHTML replace already handles this correctly today for
  other rows, no new risk introduced, but worth an explicit test (see
  Acceptance criteria).
- **Rapid double-click Stop on the same session** — idempotent per part
  1's backend contract; frontend does not need its own dedup logic beyond
  what `toggle()`'s existing in-flight-request handling already provides.
- **TOTP required mid-spawn or mid-stop** — identical code-overlay/retry
  flow as every other action; `pendingSessionStop[name]` must survive the
  retry round-trip (set *before* `toggle()`'s first optimistic POST fires,
  same discipline `team-interject`'s own doc comment already establishes
  at `app/app.py:4336-4349` — read/copy that exact pattern, don't
  reinvent it).

## Acceptance criteria
- [ ] Given a project with 0 sessions, when the row renders, then an
      engine picker and "+ Start session" control are shown, no checkbox,
      no session list.
- [ ] Given a project with 2 running sessions of different engines, when
      the row renders, then both are listed, each with its own engine
      label and its own Stop control — no checkbox present anywhere in
      this row.
- [ ] Given 2 sessions are running, when Stop is clicked on session B's
      row, then only session B's entry disappears after the next refresh;
      session A's entry (including its own open-link) is unchanged.
- [ ] Given the TOTP overlay is required, when "+ Start session" or a
      session's Stop is clicked, then the same `pendingToggle`/code-
      overlay/retry flow as every other mutating control runs, with no
      duplicated implementation.
- [ ] Given zero engines are configured, when the row renders, then the
      spawn control is omitted entirely (no broken/empty picker).
- [ ] Given `host`/`taiga`/`gitea` rows, when rendered, then they are
      pixel-for-pixel/behaviorally unchanged (still a checkbox) — this
      spec's changes are scoped to `kind === 'inst'` only.
- [ ] `tests/test_multi_session_frontend.js` passes; `tests/
      test_singleton_toggle_frontend.js`, `tests/test_team_frontend.js`,
      `tests/test_deploy_frontend.js`, `tests/test_smoke_check_frontend.js`
      all continue to pass unmodified (or with only the confirmed-necessary
      adjustments noted in "Affected areas").
- [ ] `POST /instance/<name>/on` and `/off` no longer exist (return 404,
      or are simply absent from the route table) and nothing in the
      frontend references them; `/status`'s per-project JSON no longer
      includes `on`/`engine`/`url` (only `sessions`, `desc`, `code_on`,
      `code_url`, `deploy`, `gitea_sync`, `team`).

## Open questions
- **Smoke-check's per-session scoping**: default assumption (stated in
  Non-goals) is to leave it project-level, targeting whatever part 1's
  "newest session" resolver picks. If ux-designer's pass surfaces an easy,
  low-risk way to let the user pick which session to smoke-check instead,
  that's a reasonable addition to fold in here — but not a requirement,
  and not to be added speculatively if it complicates the row layout.
- **Visual treatment of the session list** (badges vs. plain text, compact
  vs. card-style, whether "Stop" needs a confirm step) — left entirely to
  `docs/design.md`.

## Risk / rollback notes
Frontend-only changes plus deletion of already-superseded back-compat
backend code from part 1 — no data model changes beyond what part 1
already made permanent (in-memory, lost-on-restart, unchanged). Rollback
is a plain revert of the commit; part 1's backend remains intact and
correct on its own even if this part is rolled back (the back-compat
shim simply keeps serving the old checkbox UI, exactly as it did the
moment part 1 shipped).
