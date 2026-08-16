# Implementation: Dedicated team chat page (`GET /team/<project>`)

## Summary
Moved the AI-team interface (status strip, escalation/answer panel, interject
box, live event feed, composition picker, start/stop controls) off the
per-project dashboard row and onto its own full page at `/team/<project>`.
The dashboard's `teamRow()` now renders only a compact status badge + an
"Open team chat →" link; the dedicated page reuses every existing
`render*`/`doTeam*` function verbatim (extracted into one shared
`renderTeamPageBody()`), with no new backend routes and no changes to
`app/teams.py`.

**Post-review update:** `docs/test-review.md`'s testing pass blocked on a
regression (Defect 1) — the new bottom-of-script router's unconditional
`location.pathname.match(...)` call broke 55 tests across 5 pre-existing,
unrelated frontend test files that share the same "extract and execute the
real `<script>`" technique but had no `location` stub in their sandboxes.
Fixed by adding the same stub to each of those 5 files; see "Changes by
file" and "How to verify locally" below for the specifics and full re-run
results (all 6 frontend files now pass 100%, Python suites unchanged/no new
regressions).

## Changes by file

- `app/app.py`
  - `do_GET`: one new route branch, `self.path == "/" or
    _TEAM_PAGE_PATH_RE.match(self.path)`, serving the same unauthenticated
    static shell as `/`. New module-level `_TEAM_PAGE_PATH_RE =
    re.compile(r"^/team/[^/]+/?$")` next to `NAME_RE`. `re` was already
    imported — no new import needed.
  - `PAGE_TEMPLATE` body: added `<div id="team-page"></div>` after `#rows`.
    Added `id="page-title"` (`<h1>`), `id="new-project-row"`, and
    `id="upload-folder-btn"` to existing dashboard-chrome elements (the
    second `.upload-wizard-btn`, "Clone from URL", already had
    `id="clone-toggle-btn"`) so the new team-page router can hide/show them
    via `getElementById` — the test harness's own DOM stub only implements
    `getElementById`, not `querySelector`, so this was cheaper and more
    consistent with the file's existing ID-driven convention than adding
    `querySelector` support to five identical test-file stubs (design.md's
    own sketch used `querySelector('.new-project-row')` etc.; this is a
    documented, intentional deviation from that sketch — see "Deviations").
  - New `<style>` rules: `#team-page`/`#team-page.active`,
    `#rows.hidden-for-team-page`, `.team-page-header`,
    `.team-page-back-link`(`:hover`), `.team-page-not-found*` — verbatim
    from `docs/design.md`'s own CSS blocks, no new color tokens.
  - `refresh()`: removed the per-project `pollTeamFeed()` trigger block
    (the dashboard no longer renders the feed at all, so nothing there
    needs to poll it); the equivalent now lives in `renderTeamPage()`.
  - `teamRow(name, team)` (the old ~90-line function that rendered the full
    idle-launcher/running-state body inline on the dashboard) was renamed
    to **`renderTeamPageBody(name, team)`**, body otherwise unchanged. A
    brand-new, much smaller `teamRow(name, team)` replaces it: a status
    badge (`<div class="team-status status-<status>">`, reusing the
    already-defined-but-previously-unused `.team-status`/`.status-*` CSS)
    plus `<a href="/team/<name>" class="team-configure-btn">Open team
    chat →</a>`. Applies uniformly to every status including idle.
  - New functions (bottom of `<script>`, right before the router):
    `hideDashboardChromeForTeamPage()`, `goToDashboard()`,
    `teamPageHeader(name)`, `renderTeamPageNotFound(projectName)`,
    `renderTeamPage(projectName)` (fetches `/status`, same 401→`showOverlay()`
    handling as `refresh()`, finds the project by name in `s.instances`,
    mounts `teamPageHeader(...) + renderTeamPageBody(...)` into
    `#team-page`, and — new — replicates `refresh()`'s old per-project
    `pollTeamFeed()` trigger for this one project).
  - Bottom-of-script routing: `refresh(); setInterval(refresh, 4000);`
    replaced with the `location.pathname.match(/^\/team\/([^/]+)\/?$/)`
    branch from `docs/design.md`, dispatching to `renderTeamPage(...)` +
    its own `setInterval` instead when the path matches.
  - **New (beyond the spec/design sketch): `refreshCurrentView()` +
    `TEAM_PAGE_PROJECT`.** Auditing every existing fire-and-forget
    `refresh()` call used by an action handler to reflect its own state
    change immediately (composition picker open/lead-change/mate-toggle,
    grounding fetch, inbox fetch, escalation option change, feed
    toggle/filter, login) found that all of these are also reachable from
    the team page (e.g. the idle launcher's "Configure team..." picker).
    Left as bare `refresh()` calls, every one of them would have silently
    re-rendered the *hidden* `#rows` instead of `#team-page`, leaving the
    team page stale until the next 4s poll tick — a real, user-visible
    regression tied directly to acceptance criteria (e.g. "logging in from
    `/team/<project>`'s shared overlay" would otherwise never show the team
    page until the next tick). Added a top-level `let TEAM_PAGE_PROJECT =
    null;` (set by the router) and `refreshCurrentView()` (calls
    `renderTeamPage(TEAM_PAGE_PROJECT)` if set, else `refresh()`), and
    swapped the 9 relevant call sites (`login()`, `fetchTeamGrounding()`,
    `toggleTeamPicker()`, `onTeamLeadChange()`, `onTeamMateToggle()`,
    `fetchTeamInbox()`, `onEscalationOptionChange()`, `toggleTeamFeed()`,
    `setTeamFeedFilter()`) to call it instead. Left untouched:
    `pickEngine()`'s and the upload wizard's own `refresh()` calls — both
    dashboard-only, never reachable from the team page.

- `tests/test_team_frontend.js`
  - `makeElementStub()`: `classList` is now a real, stateful `Set`-backed
    stub (`add`/`remove`/`contains`), not the no-op every sibling test file
    still uses — this file is the first to actually assert `.contains()`
    (`#team-page`/`#rows`'s own `.active`/`.hidden-for-team-page` toggling,
    the login overlay's `.show`). Scoped to this file only.
  - `createCase(locationPathname)`: now accepts an optional path, defaulting
    to `'/'` so every pre-existing caller is unaffected; added a `location`
    stub (`{pathname, href}`) to the sandbox.
  - `instanceRowHtml(name)`: retargeted from slicing the dashboard's own
    `#rows` HTML to calling `renderTeamPageBody(name, TEAM_BY_NAME[name])`
    directly via `vm.runInContext` (same lexical-scope technique
    `setTeamTaskText()` already used). This is the key move that let all
    ~90 pre-existing tests of the extracted sub-renderers (idle launcher,
    composition picker, escalation panel, interject box, add-member
    control, event feed, board-write panel, branches panel) keep their own
    assertions **completely unmodified** — only what the helper points at
    changed, matching `docs/spec.md`'s own "existing coverage... must keep
    passing unmodified" instruction literally.
  - New `dashboardRowHtml(name)`: the *old* `instanceRowHtml()`
    implementation (slices `#rows` by `<div class="label">`), used by the
    new dashboard compact-summary tests.
  - New `simulateTeamPageRender(instances)`: replicates the adjacency the
    old `teamRow()` (called from `refresh()`'s per-project loop) used to
    provide for free — rendering every project's full body (which is what
    triggered `renderTeamBranches()`'s one-time branches fetch, idle or
    not) and, for a non-idle project, seeding `teamFeedOpen` then firing
    `pollTeamFeed()`. Wired into `bootstrapCase()` (also used directly by
    the branches-panel tests), `rerenderRow()`, and `drainTriggeredRefresh()`
    — the three places this file's own helpers simulate a "poll tick" — so
    the ~40 feed/escalation/board-write/branches tests that depend on this
    side effect firing automatically also needed no body changes.
  - New test sections: dashboard compact-summary across all five statuses
    (7 tests), `renderTeamPage()` found/idle/blocked/unknown/401 + the
    same-shared-function spy proof (6 tests), client-side router dispatch
    (3 tests), and the `refreshCurrentView()` regression pair (picker-open
    and login-from-team-page both landing on `#team-page`, not `#rows`;
    2 tests). 19 new tests; 115 pre-existing + 19 = 134 total, all passing.

- `tests/test_smoke_check_frontend.js`, `tests/test_clone_frontend.js`,
  `tests/test_deploy_frontend.js`, `tests/test_singleton_toggle_frontend.js`,
  `tests/test_upload_frontend.js`
  - **Post-review fix (test-review.md Defect 1).** Each file's own
    sandbox-construction helper now includes the same `location: { pathname:
    '/', href: '' }` stub `tests/test_team_frontend.js` already had. The
    bottom-of-script router added to `app/app.py` (see above) calls
    `location.pathname.match(...)` unconditionally at script-load time; all
    five of these files extract and `vm.runInContext` the real `<script>`
    from `render_page()` the same way `test_team_frontend.js` does, so
    without this stub every one of their sandboxes threw `ReferenceError:
    location is not defined` as soon as the script loaded — a regression the
    reviewer caught (55/55 tests failing across these 5 files) that the
    original diff missed because only `test_team_frontend.js`'s own sandbox
    was updated and re-run. No other change to these files; the stub is
    inert for all of them since none of their own tests reference
    `location` — it exists purely so the router branch at the bottom of the
    script doesn't throw during sandbox setup.

- `tests/test_team_routes.py`
  - New `TeamPageRouteTests` class (6 tests): `GET /team/<project>` returns
    a byte-identical 200 shell to `GET /` unauthenticated; matches
    `appmod.render_page()` directly; works for a nonexistent project name
    (no server-side existence check, per spec); accepts an optional
    trailing slash; handles a URL-encoded project name; and paths that
    merely resemble the route (`/team`, `/team/`, `/teamfoo`) correctly
    fall through to the normal authenticated-route 401, not the shell.
    There was no pre-existing Python test of `GET /` itself to "mirror" (a
    grep across every `tests/*.py` file found none) — this class is
    self-contained rather than literally extending an existing one.

## Key decisions / tradeoffs

- **Single `renderTeamPageBody(name, team)` extraction, not the two
  functions (`renderTeamIdleLauncher`/`renderTeamRunningState`)
  `docs/design.md`'s own implementation sketch shows.** `docs/spec.md`
  explicitly leaves "the exact extraction shape" to the developer. Keeping
  the old `teamRow()`'s single idle/non-idle dispatch body intact under a
  new name (rather than splitting it into two functions) meant the ~90
  pre-existing tests of that body's behavior needed zero changes to their
  own assertions — only the test harness's one `instanceRowHtml()` helper
  needed retargeting. This is called out explicitly since design.md names
  the two-function split in several places (wireframe comments, "Summary of
  Design Decisions").
- **`refreshCurrentView()`/`TEAM_PAGE_PROJECT`** (see "Changes by file"
  above) — not in the spec or design docs at all, added after finding, by
  auditing every `refresh()` call site, that several action handlers
  reachable from the team page would otherwise leave it stale for up to 4s
  after an action. This is a correctness fix directly serving acceptance
  criteria ("same login-overlay behavior as `/`"), not scope creep — it
  doesn't add any new capability, only makes existing capabilities that
  moved to the new page keep behaving the way they always did.
- **`classList` made stateful in `test_team_frontend.js` only** (not the
  four sibling test files that share the same no-op stub) — the minimal
  change needed to actually assert the new page's show/hide behavior,
  rather than skipping that coverage or rewriting `hideDashboardChromeForTeamPage()`
  to avoid `classList` entirely.
- **IDs added to existing dashboard-chrome elements** instead of
  `querySelector` (see "Changes by file") — additive, no class names
  removed, matches the file's existing ID-driven convention.

## Deviations from spec

- None from `docs/spec.md`'s Goals/Non-goals/Acceptance criteria — the
  route, the reuse constraint ("no duplicated copy of any of the listed
  render functions"), the dashboard's compact-summary shape, and every edge
  case (unauthenticated, unknown project, URL-unsafe names, empty roster)
  are implemented as specified.
- From `docs/design.md`'s own *implementation sketch* (which the spec
  explicitly says is not binding on the developer): used one
  `renderTeamPageBody()` instead of two (`renderTeamIdleLauncher`/
  `renderTeamRunningState`); used `getElementById` on newly-added element
  IDs instead of `querySelector`; added `refreshCurrentView()`, which
  design.md's sketch didn't have (its sketch called `renderTeamPage()`'s
  own action handlers' `refresh()` calls unmodified, which would have had
  the staleness bug described above).

## Known limitations

- **Losing not-yet-submitted interject/task text on navigation** —
  acknowledged in `docs/spec.md` itself as a pre-existing-equivalent,
  non-regression behavior: navigating away from `/team/<name>` and back (or
  reloading) loses in-progress textarea text, exactly as reloading today's
  dashboard already would. Not addressed here, per spec.
- No "last event preview" on the dashboard's compact badge — explicitly out
  of scope per `docs/spec.md` Non-goals/Open questions.
- The pre-existing `docs/BACKLOG.md` item 20 (`.team-btn` WCAG AA contrast)
  is unaddressed here, per spec — the same button family is relocated, not
  fixed.

## How to verify locally

```bash
# Frontend (plain Node, no deps) -- this sandbox's system PATH has no node,
# but a usable one ships with code-server. Run the new file plus all 5
# sibling frontend suites that extract/execute the same rendered <script>
# (each one needed the `location` stub fix -- see "Changes by file"):
NODE=/usr/lib/code-server/lib/node
"$NODE" tests/test_team_frontend.js              # -> ALL PASS (134/134)
"$NODE" tests/test_smoke_check_frontend.js       # -> ALL PASS (11/11)
"$NODE" tests/test_clone_frontend.js             # -> ALL PASS (8/8)
"$NODE" tests/test_deploy_frontend.js            # -> ALL PASS (9/9)
"$NODE" tests/test_singleton_toggle_frontend.js  # -> ALL PASS (19/19)
"$NODE" tests/test_upload_frontend.js            # -> ALL PASS (8/8)

# Backend routes (real ThreadingHTTPServer + urllib, no mocks)
TOTP_SECRET=JBSWY3DPEHPK3PXP python3 -m unittest tests.test_team_routes.TeamPageRouteTests -v
# -> Ran 6 tests ... OK

# Full team-routes suite (pre-existing baseline has 45 errors/2 failures in
# this sandbox unrelated to this change -- git commit fails here because no
# git user.name/email is configured, and two CLI-timing tests are flaky;
# confirmed identical set of failing/erroring test names on this branch's
# own last commit via `git stash`, both before this work started and again
# after the location-stub fix, sorted-name diff empty both times):
TOTP_SECRET=JBSWY3DPEHPK3PXP python3 -m unittest tests.test_team_routes -v
# -> Ran 137 tests ... FAILED (failures=2, errors=45)

# Manual check once the server is running: open /team/<any-project-name> --
# unauthenticated shows the login overlay; after login, an idle project
# shows the full task-textarea launcher, a running one shows the status
# strip/feed/interject box; the dashboard's own row for that project now
# shows only a status badge + "Open team chat →" link.
```
