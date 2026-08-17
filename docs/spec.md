# Spec: Dedicated team chat page (`/team/<project>`)

## Routing note (read first)
**Workflow: `workflows/feature.md`.** Independent of the two-part
"concurrent sessions" spec (`docs/spec.md` + `docs/spec-feature1-part2-
multi-session-ui.md`) — no ordering dependency, can be built before,
after, or interleaved with either part. **Does need ux-designer**: this
introduces a genuinely new page layout (full-page chat-style surface),
not a reuse of an existing visual pattern.

This file is written now, alongside the two-part session spec, as a
complete ready-to-execute plan. It is **not** the currently-active
`docs/spec.md` (that slot currently holds part 1 of the session-identity
work, queued to run first per this session's recommended ordering — see
that file's own routing note). When this feature's turn comes, promote
this file's content into `docs/spec.md` (copy over, refresh any line
numbers that shifted in the interim, overwrite).

Already approved by Leo — no sign-off gate applies.

## Summary
Move the AI-team interface (status strip, escalation/answer panel,
interject box, live event feed, composition picker, start/stop controls)
off the per-project dashboard row and onto its own dedicated page at
`/team/<project>`, so it no longer shares screen space with the
project's engine/session controls; the dashboard row keeps only a compact
status indicator and a link into that page.

## Goals
- New route `GET /team/<project>` serving a real page — full team
  interface for exactly one project, addressable/bookmarkable/linkable on
  its own.
- Dashboard row's team section shrinks to a compact status badge (idle /
  running / blocked / finished / error — reusing the exact status
  vocabulary `/status` already computes server-side) plus an "Open team
  chat →" link to `/team/<name>`. No task textarea, composition picker,
  event feed, escalation panel, or interject box left inline on the
  dashboard, for **any** team status including idle (starting a team also
  moves fully to the dedicated page).
- The dedicated page supports the complete lifecycle: idle launcher
  (task text + Lead/Teammates composition picker + Start), through
  running/blocked (status strip, escalation panel when
  `waiting_on_you`, interject compose box, add-member control, live event
  feed), to finished/error (summary, Stop-adjacent state) — functionally
  everything `teamRow()` renders today, just relocated.
- Reuses the **existing** backend surface unchanged: `/status` (filtered
  client-side to one project), `POST /projects/<name>/team/start|stop|
  resolve|board-resolve|interject|add-member`, `GET /projects/<name>/
  team/events|inbox|branches|grounding`. Confirmed via archaeology (see
  "Background") that all of these are already project-scoped and
  sufficient — **no new backend routes**.

## Non-goals
- No chat-bubble redesign of the event feed. Already explicitly decided
  against in backlog item 19 (`docs/BACKLOG.md:1240-1247`): "~10
  structurally different event kinds across more than two participants
  doesn't fit a two-party bubble layout," and a redesign risked breaking
  the existing `role="log"`/`aria-live="polite"` accessibility contract
  for no functional gain. "Dedicated page" and "chat bubbles" are
  orthogonal asks — Leo's request is about *location* (own page vs.
  inline), not the feed's visual format — and this spec does not revisit
  that prior decision.
- No new backend routes, no change to `app/teams.py`, no change to the
  event envelope shape (`{ts, agent, seq, kind, text, meta}`) or the
  cursor-polling mechanism.
- No multi-project view on the team page — one project per page load,
  selected via the URL path segment. Switching projects means navigating
  to a different URL (via the dashboard's own link, or a browser back/
  forward), not an in-page project switcher.
- Not fixing item 20 (`.team-btn` WCAG AA contrast) — separate, already-
  backlogged, unrelated to this change even though the same button family
  is relocated here.
- No "last event preview" on the dashboard's compact badge (a chat-app-
  style conversation-list preview) — not requested; flagged as a
  possible future nice-to-have, not built speculatively here (see Open
  questions).
- Not building a genuinely separate frontend app/build pipeline. This
  codebase has zero frontend build tooling today — one inline Python-
  string HTML/CSS/JS template, one file (`app/app.py`). A separate SPA/
  framework would be a real architectural deviation from that
  established pattern for no stated benefit (Leo's own request text
  leaves "same app, new route" as an explicit valid option and gives an
  example URL, `dev.tailbe22cd.ts.net/team/<project>`, matching that
  exact shape) — this spec deliberately stays inside the existing
  single-file convention instead.

## Background / current state

### Architecture note
Same as the session-identity specs: no separate frontend framework — one
big inline HTML/CSS/JS template (`PAGE_TEMPLATE`, defined at
`app/app.py:2805`), served by a hand-rolled `http.server`-based Python
app. `do_GET` (`app/app.py:5894` on) currently special-cases exactly one
path, `"/"` (line 5899: `if self.path == "/": return
self._html(render_page())`), and requires auth for every other GET.
`render_page()` (`app/app.py:5768-5780`) just does template-variable
substitution (login copy) on `PAGE_TEMPLATE` — the page itself carries no
session data; the login overlay and dashboard rows are both populated
client-side, gated on whether `/status` comes back 401 (per `do_GET`'s
own comment at 5895-5898).

### Current team UI, precisely (confirms "frontend-surface only")
`teamRow(name, team)` (`app/app.py:4397-4484`) renders one of two shapes
depending on `team.status`, both **inline inside the project's dashboard
row** (`row()`, called from `refresh()`'s per-project loop,
`app/app.py:3354-3392`):
- `idle`/no team yet: task textarea, "Configure team..." link toggling
  `renderTeamPicker()` (`app/app.py:3729`, Lead/Teammates pill+checkbox
  picker), Start button.
- Otherwise: `renderTeamStatusStrip()` (`app/app.py:3790`), an
  escalation note or `renderEscalationPanel()` (`app/app.py:3913`) when
  `waiting_on_you`, a finished-run summary, `renderTeamInterjectBox()`
  (`app/app.py:4313`), `renderTeamAddMemberControl()` (`app/app.py:4366`),
  `renderTeamFeedToggle()`/`renderTeamFeed()` (`app/app.py:3983`/`4137`,
  the collapsible cursor-polled event log), a Stop button, and
  `renderTeamBranches()` (`app/app.py:3638`).
All of the above are called **from** `teamRow()`, which is itself called
from `row()`'s `(kind === 'inst' ? teamRow(name, team) : '')` line
(`app/app.py:4507`) — i.e. structurally nested inside the same row as the
engine toggle, description, code-server row, smoke-check row, and deploy
row, exactly matching Leo's complaint that it doesn't get its own space.

### Backend routes already sufficient (confirmed, not assumed)
Every route the dedicated page needs already exists and is already
project-scoped by `<name>` in its own path segment:
`POST /projects/<name>/team/start` (`app/app.py:6444`), `/stop` (`6496`),
`/resolve` (`6522`), `/board-resolve` (`6578`), `/interject` (`6627`),
`/add-member` (`6668`); `GET /projects/<name>/team/events` (`6132`,
`_handle_team_events`), `/inbox` (`6134`), `/branches` (`6136`),
`/grounding` (`6124`). `/status` itself (`app/app.py:5903` on) already
returns a `team` object per project (`app/app.py:5973-5978` computes
`team_status`; the full `team` dict assembled further down includes
composition/roster data). This matches the task's own framing: this is a
frontend-surface feature, not new backend plumbing — confirmed by reading
every route this page would call, not assumed from the framing alone.

## Proposed approach

### 1. Backend: one new `do_GET` route branch
Add, alongside the existing `if self.path == "/":` check
(`app/app.py:5899`), a match for `^/team/[^/]+/?$` that serves the exact
same `self._html(render_page())` — same unauthenticated static shell,
same "nothing sensitive served without a session" security model
(`do_GET`'s own existing comment). The project name in the URL is never
read/validated server-side at this layer — exactly like today's `/`,
everything is resolved client-side against `/status` after login,
including "does this project even exist" (see Edge cases). Use a
precompiled `re` pattern (module already needs `re` available — check
whether it's already imported near the top of `app/app.py`; if not, this
is the one new import this whole feature requires).

### 2. Client-side routing (bottom of `<script>`)
Today: `refresh(); setInterval(refresh, 4000);` (`app/app.py:5763`).
Replace with a branch on `location.pathname`:
```js
const teamPageMatch = location.pathname.match(/^\/team\/([^/]+)\/?$/);
if (teamPageMatch) {
  const TEAM_PAGE_PROJECT = decodeURIComponent(teamPageMatch[1]);
  renderTeamPage(TEAM_PAGE_PROJECT);
  setInterval(() => renderTeamPage(TEAM_PAGE_PROJECT), 4000);
} else {
  refresh(); setInterval(refresh, 4000);
}
```
`renderTeamPage(projectName)` is new: fetches `/status` (unchanged
endpoint — same auth/401→login-overlay handling `refresh()` already has,
copy that exact handling rather than reimplementing it), finds the
matching entry in `s.instances` by `name`, and either renders the full
team surface (§3) or an "unknown project" message with a link back to
`/` (see Edge cases) if no match is found.

### 3. Reuse, don't fork, the existing sub-renderers
`renderTeamPage`'s body should call the same functions `teamRow()`
already calls (`renderTeamStatusStrip`, `renderEscalationPanel`,
`renderTeamInterjectBox`, `renderTeamAddMemberControl`,
`renderTeamFeedToggle`/`renderTeamFeed`, `renderTeamPicker`,
`renderTeamBranches`, the idle-state task textarea + Start button, and
`doTeamStart`/`doTeamStop` for the actions) — either by having
`teamRow(name, team)` itself become a shared body-builder called from
both contexts (dashboard's compact version wraps a *different*,
much smaller function; the full version is what mounts on the page), or
by extracting `teamRow()`'s existing non-idle/idle bodies into a new
`renderTeamPageBody(name, team)` that both `teamRow()` (if it still needs
any inline remnant — see §4, it shouldn't) and `renderTeamPage()` call.
Developer's call on the exact extraction shape; the hard requirement is
**no duplicated copy of any of the listed render functions** — one
implementation, mounted in two different containers.

### 4. Dashboard's `teamRow()` shrinks to a compact summary
For the dashboard context only: status badge (map `team.status` the same
way `/status`'s own `team_status` computation already does —
`idle`/`running`/`blocked`/`finished`/`error`, `app/app.py:5974-5978`) +
`'<a href="/team/' + encodeURIComponent(name) + '">Open team chat →</a>'`.
This applies uniformly regardless of status — including `idle`, since
starting a team now only happens on the dedicated page (today's idle-
state task textarea/picker/Start button move there entirely, per Goals).
`row()`'s existing `(kind === 'inst' ? teamRow(name, team) : '')` call
site (`app/app.py:4507`) is unchanged in *shape*, just now renders the
much smaller compact block.

### 5. Full-page layout container
New HTML container in `PAGE_TEMPLATE`'s `<body>` (alongside `#rows`,
`#upload-overlay`, etc. — see `app/app.py:3134-3177`), e.g. `<div
id="team-page" style="display:none;"></div>`, shown/hidden opposite `
#rows` and the new-project/upload/clone controls based on which branch
§2's routing takes (the plain dashboard chrome — "+ New project", upload
wizard button, clone form — should not render on the team page; only the
login/TOTP overlays are shared between both contexts). Exact header/back-
link/spacing treatment is ux-designer's call (`docs/design.md`).

## Affected areas
- `app/app.py`: `do_GET` (new route branch, `re` import if not already
  present), bottom-of-script routing branch, new `renderTeamPage()`, the
  `teamRow()`/`renderTeamPageBody()` extraction (§3), dashboard's
  compact-summary block, new `#team-page` container + its show/hide
  wiring, new `<style>` rules for the full-page layout (ux-designer).
- `tests/test_team_frontend.js`: existing coverage of the extracted sub-
  renderers must keep passing unmodified (they're being *called from* a
  new place, not changed); add new coverage for (a) the dashboard's
  compact-summary+link rendering across all five statuses, and (b)
  `renderTeamPage()`'s own behavior — found-project renders the full
  surface via the shared sub-renderers (assert it's the *same* functions,
  not a forked duplicate — e.g. by spying/monkeypatching one of them in
  the test harness and confirming both contexts call it), unknown-project
  renders the fallback message.
- `tests/test_team_routes.py`: add a smoke assertion that `GET /team/
  <any-name>` returns the same static shell as `GET /`, unauthenticated,
  200 — mirrors whatever existing assertion already covers `/`.
- No `app/teams.py` changes.
- `docs/implementation.md` — developer's usual write-up.

## Edge cases
- **Unauthenticated access to `/team/<project>`** — identical behavior to
  `/`: shell loads, `/status` 401s, login overlay shows, no team data
  visible before login. No new auth code path.
- **Unknown/nonexistent project name in the URL** (typo, deleted project)
  — `renderTeamPage()` finds no match in `s.instances`; render a clear
  "Unknown project" message with a link back to `/`, not a JS
  exception/blank page.
- **Project with an active team `blocked_ask_user`/`waiting_on_you`** —
  dashboard badge reflects "blocked" distinctly; no answer-capability
  inline on the dashboard anymore (must click through to the page) — this
  is an intentional behavior change per Goals, not an oversight.
- **Long/URL-unsafe project names** — reuse the existing
  `encodeURIComponent`/`unquote` handling already used by the team API
  routes and the ttyd/code-server path builders (`/term/<name>`, `/code/
  <name>`) — no new escaping logic invented.
- **Multiple browser tabs open on the same project's `/team/<name>`
  simultaneously** — already safe: the event feed's cursor-based polling
  (`GET .../team/events?cursor=...`) already supports concurrent viewers
  today (used from the dashboard's own polling); nothing about moving it
  to a different container changes that guarantee.
- **Losing not-yet-submitted interject/task text on navigation** — a real
  but pre-existing-equivalent behavior: today's SPA never fully reloads
  so in-progress textarea state (`teamTaskText[name]`) survives across
  `refresh()`'s polling re-renders; navigating *to* `/team/<name>` for the
  first time is unaffected (fresh state), but navigating away and back
  (or reloading the tab) loses in-progress text — exactly as reloading
  today's dashboard already would. Not a new regression; one line in
  `docs/implementation.md` acknowledging it is enough, not a blocker.
- **Empty roster / `composition === null`** on the dedicated page's idle
  launcher — reuse the exact existing "No roster members available"
  branch/messaging (`app/app.py:4412-4421`), unchanged.

## Acceptance criteria
- [ ] Given a project with a running team, when navigating to `/team/
      <project-name>`, then the status strip, escalation panel (if
      `waiting_on_you`), interject box, add-member control, and event feed
      for that project render on the dedicated page — same data/behavior
      as previously rendered inline.
- [ ] Given any project row on `/`, when it renders, then it shows only a
      compact team-status badge and an "Open team chat" link — no task
      textarea, composition picker, event feed, escalation panel, or
      interject box inline, for every team status (idle/running/blocked/
      finished/error).
- [ ] Given a project with no team ever started (`status === 'idle'`),
      when its "Open team chat" link is clicked, then `/team/<project-
      name>` renders the full idle launcher (task textarea + composition
      picker + Start button) — not a blank or read-only page.
- [ ] Given `/team/<a-real-project-name>` loaded unauthenticated, then the
      same login-overlay behavior as `/` occurs, no team data visible
      before authentication.
- [ ] Given `/team/<a-nonexistent-project-name>` loaded while
      authenticated, then a clear "unknown project" message with a link
      back to `/` is shown, no JS error.
- [ ] Given the interject box, escalation panel, and add-member control on
      the dedicated page, when used, then they call the exact same
      existing routes (`/team/interject`, `/team/resolve`/`/team/board-
      resolve`, `/team/add-member`) — verified via network-call
      assertions in the frontend test, not just visual inspection.
- [ ] `tests/test_team_frontend.js` (existing + new coverage) and `tests/
      test_team_routes.py` (existing + the new `/team/<name>` shell
      assertion) all pass.

## Open questions
- **Visual treatment** of the full-page layout (header, back-link
  placement, overall spacing, degree to which it echoes the dashboard's
  existing dark theme) — explicitly left to ux-designer's `docs/design.md`
  pass, not decided here.
- **Dashboard badge "last event" preview** (chat-app-style conversation
  preview text) — not requested by Leo; assumption is **not building this
  now**, flagged so it can be explicitly requested later rather than
  silently added as scope creep.
- **Exact route shape**: assuming `/team/<project>` verbatim (matches
  Leo's own example URL in the request, `dev.tailbe22cd.ts.net/team/
  <project>`) — no query params, one path segment. Low-stakes assumption;
  a one-line change if a different shape (e.g. `/team/<project>/chat`) is
  actually wanted.

## Risk / rollback notes
Additive route + extraction/reuse of existing render functions into a
shared body callable from two containers — no backend route/data changes,
no `app/teams.py` changes. The main regression risk is accidentally
breaking `teamRow()`'s dashboard-embedded behavior while extracting the
shared body; mitigated by running the existing `tests/
test_team_frontend.js` suite (should pass unmodified if the extraction
preserves each sub-renderer's own contract) plus the new page-specific
coverage above. Rollback is a plain revert of the commit.
