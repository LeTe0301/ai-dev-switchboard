# Spec: Team launcher fixes — broken Start button, lead/teammate exclusivity, undiscoverable chat UI, unexplained Smoke check

## Routing note (read first)
**Workflow: `workflows/bugfix.md`.** This is a bundle of three real bugs (#2
Start team broken, #3 lead/teammate exclusivity, #4 unexplained Smoke check)
plus one investigation-turned-documentation-answer (#1 chat UI location —
see "Background" below: no new UI needs to be built for it). All four fixes
live in a single file (`app/app.py`, the monolithic inline HTML/CSS/JS
switchboard UI + its Python HTTP handlers) and reuse patterns already
established elsewhere in that same file — no new visual language, no new
architectural layer, no schema/API shape change. **Recommend skipping
ux-designer** and going straight to `developer`: the disabled-checkbox
treatment reuses the exact `:disabled { opacity: 0.6; cursor: not-allowed; }`
pattern `.clone-form` already uses (`app/app.py:3042`), and the Smoke-check
copy/tooltip and idle-state chat hint are copy-only additions styled after
the existing `.team-tier-3-caveat` inline-hint pattern (`app/app.py:3714`).
Nothing here requires a new design decision.

**Note on `docs/`:** `docs/spec.md`, `docs/design.md`, `docs/implementation.md`,
and `docs/test-review.md` currently hold backlog item 45's completed,
committed work (already merged — see `de60bf4`/`391865c` in git log). This
spec overwrites `docs/spec.md` for the new cycle per the standard convention;
flagging this explicitly so the orchestrator can mention it to the user
before the ux-designer/developer stages proceed. `docs/story.md` (the
item-6 multi-agent-orchestration story doc) is unrelated and untouched.

## Summary
Fix three launcher bugs (Start team silently/confusingly failing, an
engine picked as Lead being constructible as a Teammate too, and the Smoke
check button having no explanation of what it does), and answer the "where
is the chat UI" question by pointing to the in-page live event feed +
compose box that already ships (backlog item 19) but is only discoverable
once a team is actually running — which today it can't be, because of bug
#2/#3.

## Goals
- Diagnose and fix why clicking "Start team" fails/does nothing for a
  composition matching the reported screenshot (Lead: aider, Teammates:
  claude checked).
- Fix a confirmed code defect: switching the Lead dropdown does not clear a
  previously-checked Teammate of the same engine from the underlying
  selection state, only from the checkbox *render* — so an invalid
  "engine is both lead and teammate" composition can still be silently
  carried into the Start request even though the UI never shows it checked.
- Change the Teammates list so the engine currently selected as Lead is
  shown disabled/grayed-out (not hidden), and is auto-unchecked the moment
  it becomes Lead, per the user's explicit ask — this is a deliberate
  behavior change from today's "omit the option entirely" approach.
- Add a short explanation (label text, helper text, or tooltip) to the
  Smoke check control so a user understands what clicking it does before
  clicking it.
- Answer the "where do I chat with the running team" question: point at
  the feature that already exists (item 19's in-page live event feed +
  compose box), and make its existence discoverable from the idle/launcher
  state shown in the screenshot (where nothing indicates it's there,
  because it isn't rendered until a team is running).

## Non-goals
- Building a new, separate chat UI page/service/route. One already exists
  in-page (item 19 part 2); this spec does not redesign it, only makes its
  existence known before a team is started.
- Any change to `teams.validate_composition()`'s actual rule set — "lead
  cannot also be a teammate" stays a real server-side rule (defense in
  depth); this spec only prevents the *UI* from constructing that state,
  it does not relax or restructure the backend validation.
- Fixing item 20 (`.team-btn` WCAG AA contrast) — a separate, already
  backlogged, non-blocking item; not in scope here even though the same
  file/button family is touched.
- Redesigning the event feed into chat bubbles — already explicitly
  rejected in backlog item 19's own writeup (`docs/BACKLOG.md:1240-1247`)
  for accessibility reasons; not being revisited.
- Any change to how compositions are persisted/loaded server-side
  (`teams.save_composition`/`load_compositions`) — only the client-side
  picker's own transient in-memory state (`teamPickerMembers`) is in scope.

## Background / current state

### Architecture note
This project has no separate frontend framework/template files — the whole
UI (HTML/CSS/JS) is generated inline as Python string literals inside
`app/app.py` (a stdlib `http.server`-based app, hand-rolled routing via
`elif parts[0] == "projects" and ...` dispatch, ~6700 lines). All four
fixes below are edits to this one file. Multi-agent team backend logic
(worktrees, tmux sessions, composition validation) lives in
`app/teams.py` (~5200 lines).

### Issue #1 — "where is the chat UI"
There is no separate hosted chat UI, service, or port. **The switchboard
page itself is the chat UI** — this shipped as backlog item 19 ("Interactive
chat UI for the AI team"), see `docs/BACKLOG.md:1173-1259`. Concretely:
`teamRow()` (`app/app.py:4362`) renders one of two very different things for
the same project depending on `team.status`:
- `status === 'idle'` (or no team yet) → the launcher/config screen the
  user's screenshot shows: task textarea, Configure/Lead/Teammates picker,
  Start team button.
- Any other status (`running`/`blocked`/`finished`/`error`) → a live,
  cursor-polled, per-agent-colored event feed (`role="log"`,
  `aria-live="polite"`), a status strip, an escalation-answer panel, and
  (when running/blocked-waiting-on-you) a compose box wired to
  `POST .../team/interject` — this is the actual "chat with the team"
  surface, all rendered in place of the launcher inline on this same page,
  no navigation required.

Because the user has never gotten past the broken Start button, they have
never seen this state render — hence assuming it must be hosted elsewhere.
The real fix here is not a new link to an external page (there isn't one to
link to); it's (a) fixing #2/#3 so a team can actually start, and (b) adding
a one-line discoverability hint in the *idle* state so it's clear something
appears here once started, rather than the current silence.

### Issue #2 — "Start team" doesn't work
Traced the full path: button → `doTeamStart()` (`app/app.py:4876`) →
`toggle('team-start', ...)` → `actionBody()` builds `{task, lead, members}`
when the picker is open and `teamCompositionError(name)` is falsy
(`app/app.py:4507-4522`) → `POST /projects/<name>/team/start` →
server route at `app/app.py:6401-6452` → `teams.validate_composition()`
(`app/teams.py:2092`) → `teams.launch_team()` (`app/teams.py:3956`).
Errors from any of these DO surface, but only as small (`font-size: 12px`,
`color: #888`) gray text in a `.team-msg` slot directly under the button
(`app/app.py:2910-2912`, `4615-4624`) — easy to miss, especially on mobile,
which is consistent with the button "appearing" to do nothing even when it
actually returned a 400.
The concrete, confirmed root cause for the exact composition in the
screenshot (Lead: aider, Teammate: claude) is **issue #3 below**: if the
user had claude *or* aider checked as a teammate at any point before
picking aider as Lead, the checkbox disappears (good) but the underlying
`teamPickerMembers` Set still contains that name (bug), so
`teamCompositionError()`/the server's `validate_composition()` both
correctly reject it with "Lead cannot also be a teammate" — surfaced only
in the easy-to-miss gray text. Fixing #3 fixes this specific repro.
**Developer must still reproduce Start team end-to-end** (task text + a
*clean* composition, e.g. Lead: claude, Teammate: aider or codex, no prior
checkbox history) to confirm no *second*, independent failure exists (e.g.
a leftover `team-<project>` tmux session from an earlier attempt blocking
`launch_team()`'s `tmux_has(session)` check at `app/teams.py:3990-3992`, or
a dirty-worktree rejection from `_validate_project_for_team()`) — if one is
found, fix it too and document it in `docs/implementation.md`; if the only
repro is the #3 scenario, say so explicitly.

### Issue #3 — Lead/Teammate exclusivity (confirmed code defect)
- `renderTeamPicker()` (`app/app.py:3702-3729`) already **filters** the
  Lead's own engine out of the rendered Teammate checkboxes entirely
  (`app/app.py:3717`: `.filter(e => e.delegate_capable && !(lead && ...))`)
  — this is tested, deliberate behavior
  (`tests/test_team_frontend.js:639`: "the saved composition pre-selects
  the lead and excludes it from the teammate checkboxes").
- The bug: `onTeamLeadChange()` (`app/app.py:3664-3669`) updates
  `teamPickerLead[name]` but never touches `teamPickerMembers[name]` — so
  if a user had already checked engine X as a teammate, then changes Lead
  to X, the checkbox for X vanishes from the picker (filtered out) but `X`
  remains in the `teamPickerMembers[name]` Set. `teamCompositionError()`
  (`app/app.py:3572-3579`) then correctly reports "Lead cannot also be a
  teammate" for a state the user can no longer see or uncheck in the UI —
  Start stays silently blocked with no visible way to fix it apart from
  guessing to re-open a picker row that looks fine.
- User's explicit requested fix is a *different* UI treatment than
  today's "filter out" approach: show the Lead's engine in the Teammates
  list **disabled/grayed-out** (not hidden) rather than removed, so its
  state is always visible, and **auto-uncheck** it (clearing it from
  `teamPickerMembers[name]`) the moment it becomes Lead. This also
  structurally fixes the stale-Set bug above, since the entry is
  actively cleared on lead change rather than merely hidden from render.

### Issue #4 — Smoke check has no explanation
`smokeCheckRow()` (`app/app.py:3453-3463`) renders an "optional: text that
should appear in the response" input, a bare "Smoke check" button, and an
empty message slot — no label or tooltip says what the button itself does.
Server-side, `smoke_check_run()` (`app/teams.py`... actually `app/app.py:1923`,
docstring at `app/app.py:1923-1949`) does exactly one thing: a single HTTP
GET against the project's own captured, server-derived session URL
(`_session_urls[name]`), reporting status code + elapsed time, and
optionally checking whether the response body contains the text typed into
the adjacent input. It's a manual, one-click HTTP health check — nothing
more (no auth, no side effects, no scheduling).

## Proposed approach

### #1 (discoverability, no new UI)
In the idle-state branch of `teamRow()` (`app/app.py:4362` area), add one
short line of static hint copy near the task textarea or Configure link —
e.g. "Once started, you'll see live team activity and can chat with it
right here" — styled as a small muted note (reuse an existing muted-text
class, e.g. the same treatment `.team-lead-picker label`/`.team-grounding`
already use at `app/app.py:2924`, not a new color/weight). No new element
needs to react to state; this is static copy shown only in the idle
branch (naturally disappears once `teamRow()` switches to the
running/blocked branch, same as the rest of that branch already does).

### #2 (Start team)
No isolated fix beyond #3 unless the developer's end-to-end reproduction
(see "Background" above) turns up a second failure. If it does, root-cause
and fix it using the existing error-surfacing path (`.team-msg`), and
additionally consider (small, low-risk, in scope): bumping `.team-msg`'s
error state to be more visually prominent than plain gray-on-dark (e.g.
reuse `.team-msg.error`'s existing `color: #ff6b6b` — check whether the
generic `.team-msg` base style at `app/app.py:2910` is what's actually
winning for the case observed, or whether `.error` is applied correctly
already) so a real rejection is never mistaken for "nothing happened."
Only fix this if the developer's own repro shows it's actually
contributing to the reported failure — don't change working styling
speculatively.

### #3 (Lead/Teammate exclusivity)
1. In `renderTeamPicker()` (`app/app.py:3702-3729`): stop filtering the
   Lead's own engine out of `mateOptions`. Instead render every
   `delegate_capable` roster entry as before, but when an entry matches
   the current `lead` (`kind`+`name`), render its checkbox with the
   `disabled` attribute and add a `disabled`-styled wrapper class (reuse
   the `.clone-form input:disabled { opacity: 0.6; cursor: not-allowed; }`
   pattern at `app/app.py:3042` — add an equivalent rule scoped to
   `.team-mates-picker input:disabled` + its label, e.g. dim the label
   text too so the whole row reads as inactive, not just the box).
   Leave it unchecked (never render it as checked-and-disabled, since
   step 2 guarantees it's never in the Set once it's the Lead).
2. In `onTeamLeadChange()` (`app/app.py:3664-3669`): after updating
   `teamPickerLead[name]`, if the new lead's `name` is present in
   `teamPickerMembers[name]`, delete it from that Set before calling
   `refresh()`. This is the actual bug fix — it guarantees the invalid
   state can never persist past a Lead change, matching the user's "so
   this invalid state can't be constructed in the first place" ask.
3. `teamCompositionError()`'s existing "Lead cannot also be a teammate"
   check (`app/app.py:3577`) stays as-is — now unreachable via normal UI
   interaction, but kept as defense-in-depth exactly like the server-side
   `validate_composition()` check it already mirrors.
4. Update `tests/test_team_frontend.js:639`'s existing assertion (currently
   "excludes it from the teammate checkboxes") to instead assert the
   checkbox is present, unchecked, and disabled.

### #4 (Smoke check copy)
Add a short static helper line under the smoke-check row in
`smokeCheckRow()` (`app/app.py:3453-3463`) — e.g. "Makes a single request
to this session's URL and reports whether it responds (optionally checking
the response text)" — styled as a small muted note (reuse the same
existing muted-hint styling referenced in #1, e.g.
`.smoke-check-msg`'s own font-size/color precedent at `app/app.py:2891`,
but as a *static* line, not the dynamic result slot). A `title="..."`
tooltip attribute on the button itself is an acceptable alternative/addition
to the visible line — developer's call on which is more legible in the
existing layout, but at least one of the two (visible helper text
preferred, since a hover-only tooltip doesn't work well on the touch/mobile
PWA context the screenshot is from) must be present.

## Affected areas
- `app/app.py` — `teamRow()`, `renderTeamPicker()`, `onTeamLeadChange()`,
  `smokeCheckRow()`, associated inline `<style>` rules (all within the
  existing single-file inline UI; no new files).
- `tests/test_team_frontend.js` — update the existing assertion at line
  639 for the new disabled-not-hidden behavior; add new coverage (see
  Acceptance criteria) for the lead-change-clears-stale-member fix and the
  disabled-checkbox rendering.
- No backend (`app/teams.py`) changes expected unless issue #2's
  reproduction surfaces a genuine second bug there — see "Proposed
  approach" #2.
- `docs/implementation.md` — developer's usual write-up, including an
  explicit statement of what issue #2's root-cause investigation found
  (the #3 scenario alone, or an additional distinct bug).

## Edge cases
- Lead changed to an engine that was never checked as a teammate — no-op
  for the Set-clearing step (nothing to delete); checkbox renders disabled
  as normal.
- Lead cleared back to "Choose a lead..." (empty selection) — no engine
  should render disabled in Teammates; previously-disabled engine's
  checkbox becomes normally selectable again (auto-uncheck logic must only
  fire on an actual lead *change*, and disabling is derived live off the
  current `lead` value every render, not a one-time flag).
- A saved composition loaded via `toggleTeamPicker()`'s pre-population
  (`app/app.py:3648-3656`) can never itself contain lead-in-members
  (server-side `save_composition()` only persists post-`validate_composition()`
  compositions) — no special-case handling needed there, but worth a
  regression test asserting the pre-populated picker doesn't show that
  engine checked.
- Rapid Lead switching back and forth between two engines, each previously
  checked as a teammate — each switch must independently clear only the
  newly-selected lead's own stale membership, never the other engine's
  legitimate one.
- Mobile/touch context (the screenshot's actual environment) — the Smoke
  check helper text must be visible without hover, per "Proposed approach"
  #4's tooltip caveat.
- No roster members at all / `composition === null` — unaffected by any of
  these changes; that branch already returns early before the picker
  renders (`app/app.py:3719-3726`... actually the `composition === null`
  branch at `~4382-4386`).

## Acceptance criteria
- [ ] Given the Teammates list is open with Lead set to engine X, when X is
      also checked as a teammate from a prior selection, then changing Lead
      away from X and back to X (or to any other engine and back) never
      leaves X checked in `teamPickerMembers` — `teamCompositionError()`
      returns null for an otherwise-valid composition.
- [ ] Given Lead is set to engine X, when the Teammates list renders, then
      X's checkbox is visible, unchecked, and has the `disabled` attribute
      (styled visually dimmed, per the `.clone-form input:disabled`
      precedent) — not omitted from the list.
- [ ] Given a valid composition (Lead ≠ any checked Teammate, at least one
      Teammate checked, task text non-empty), when "Start team" is clicked,
      then a `POST .../team/start` request is sent and either succeeds
      (team status leaves idle) or shows a legible, correctly-colored error
      message from the actual server response — never silent no-op.
- [ ] Given the reported screenshot's exact composition (Lead: aider,
      Teammate: claude checked) with no prior conflicting checkbox history,
      when "Start team" is clicked, then the team starts successfully (or,
      if a genuine second bug is found in reproduction, that bug is fixed
      too and documented).
- [ ] Given a project in the idle/launcher state, when the row renders,
      then a short static line is present indicating that team chat/live
      activity appears in this same location once started.
- [ ] Given a project's Smoke check row renders, when viewed without any
      hover/interaction (mobile/touch), then visible text explains what
      clicking "Smoke check" does.
- [ ] `tests/test_team_frontend.js:639`'s assertion is updated to match the
      new disabled-not-hidden rendering, and passes.
- [ ] All pre-existing tests in `tests/test_team_frontend.js` and
      `tests/test_teams_composition.py` continue to pass unmodified except
      the one intentionally updated above.

## Open questions
- Exact wording for the idle-state chat hint and the Smoke check helper
  text is left to the developer/reviewer's judgment within the constraints
  above (must be visible without hover; must not overstate — e.g. don't
  call it "chat" if that reads as promising a dedicated messaging UI
  beyond what item 19 actually built). Flagging as non-blocking copy, not
  a product decision.
- If issue #2's end-to-end reproduction with a clean composition (see
  "Background" #2) turns up a second, independent failure beyond the #3
  scenario, that's a real open question whose answer isn't knowable from
  static code reading alone — assumption going in: the #3 scenario is the
  sole cause, to be confirmed or refuted by the developer's own repro
  before writing `docs/implementation.md`.

## Risk / rollback notes
All four changes are additive/localized string-template and small-function
edits inside one existing file, no schema/API/data-format changes, and no
change to server-side validation rules (`validate_composition()` untouched
per Non-goals). Rollback is a plain revert of the commit. The one behavior
change with test-visible impact is #3 switching from "filter out" to
"disabled in place" — covered by the updated + new test assertions above,
so a regression would fail CI rather than ship silently.
