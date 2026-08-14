# Implementation: 6f part 2 follow-ups (BACKLOG item 12)

## Summary
Three independent, frontend-only fixes inside `app/app.py`'s already-shipped
`teamRow()` non-idle branch (6f part 2), plus their permanent coverage in
`tests/test_team_frontend.js`. **A.** Added a regression test for the
"already answered" escalation race branch (`renderEscalationPanel()`'s
`!cached.pending` branch), which was previously reachable/correct but
untested. **B.** Added the ARIA attributes `docs/design.md`'s 6f part 2
"Accessibility & platform notes" section specifies but that were never
implemented: `role="log"`/`aria-live="polite"` on the event feed's list
container, `aria-pressed` on each per-agent filter pill, and
`<fieldset>`/`<legend>` around the escalation option group. **C.** Added a
transient "pending classification" rendering state for the fact_check-vs-
finish disambiguation's poll-boundary edge case: a `tool_use` event with
empty `meta` that is the event buffer's own last lead-agent event, while
`team.status === 'running'`, no longer renders as an assumed finish
summary — it renders a distinct transient state until the paired
`tool_result` or a terminal status arrives on a later poll.

No Python file was touched (matches `docs/spec.md`'s Non-goals).

## Root cause
Not applicable (three independently-diagnosed follow-up items recorded
during 6f part 2's own review, not a bugfix against a single reported
symptom) — see `docs/spec.md` "Background" for each item's own diagnosis.

## Changes by file

- `app/app.py`
  - **Part A**: no production code change — the `!cached.pending` branch in
    `renderEscalationPanel()` was already correct (added during 6f part 2,
    confirmed by the reviewer with a targeted, uncommitted test at the
    time); this cycle only adds its permanent test.
  - **Part B**:
    - `renderTeamFeed()`: the `.team-feed-list` div (the scrollable
      container that actually gains new child rows on each poll — not the
      outer `.team-feed` wrapper, which also holds the non-live filter row)
      now carries `role="log" aria-live="polite"`.
    - `renderTeamFeed()`'s filter-pill `map()`: each `<button>` now carries
      `aria-pressed="true"`/`aria-pressed="false"` reflecting
      `filter === a`.
    - `renderEscalationPanel()`: the option group (the native
      radio/checkbox `<label>` elements built into `optionsHtml`) is now
      wrapped in `<fieldset class="team-escalation-options">` with a
      `<legend class="team-escalation-question">` — see "Key decisions"
      below for why the legend *replaces* the previously-separate
      `.team-escalation-question` div rather than duplicating its text.
    - New CSS: `.team-escalation-options` (border/margin/padding reset so
      the fieldset doesn't introduce a visible box), `legend.team-
      escalation-question` (padding reset, `display: block`, so it reads
      identically to the div it replaced), `.team-feed-event.kind-pending-
      classification` (dimmed/italic, matching the other `kind-*`
      variants' styling convention).
  - **Part C**:
    - `teamFeedEventKindClass(e, leadEvents, status)`,
      `teamFeedEventBody(e, leadEvents, status)`, and
      `renderTeamFeedEvent(e, leadEvents, status)` all gained a third
      `status` parameter (threaded from `renderTeamFeed()`'s own
      `team.status`, its only caller).
    - `teamFeedEventKindClass()`'s `tool_use`-with-empty-`meta` branch: when
      there is no next lead event (`findNextLeadEvent()` returns `null`)
      AND `e.agent === 'lead'` AND `status === 'running'`, the event now
      classifies as `'pending-classification'` instead of falling straight
      to `'finish'`. Every other combination (a next event exists, or the
      status is any non-`'running'` value, or the event isn't the lead's
      own) is unchanged.
    - `teamFeedEventBody()`: `'pending-classification'` renders `'⋯
      pending…'`.

- `tests/test_team_frontend.js`
  - **Part A**: new test `'waiting_on_you true but a fresh /team/inbox
    already reports pending:false renders "already answered", no form'`,
    inserted between the existing `'escalated_max_rounds ... never fetches
    /team/inbox'` and `'selecting a single-select option...'` tests. Drives
    exactly the scenario `docs/spec.md` part A describes: `team.waiting_on_
    you: true` (the cached `/status` read) followed by
    `deliverTeamInbox(..., { pending: false })` (the real backend's own
    exact response shape for a non-`blocked_ask_user` state — confirmed
    against `app/app.py`'s `_handle_team_inbox()`). Asserts the "already
    answered" copy renders, no `team-escalation-form`, and that it isn't
    conflated with the separate fetch-failure copy.
  - **Part B**: three new tests —
    - `'the event feed list container carries role="log" and aria-
      live="polite"'` — regex-asserts both attributes on the
      `.team-feed-list` div specifically (not just present anywhere in the
      row).
    - `'per-agent filter pills carry aria-pressed, toggling true/false as
      the selected pill changes'` — asserts the default "All" pill starts
      `aria-pressed="true"` and "helper" starts `aria-pressed="false"`,
      then calls `setTeamFeedFilter('proj', 'helper')` and re-asserts both
      values flipped — the "toggle test" the spec's acceptance criteria
      explicitly ask for, not just a static single-state check.
    - `'the escalation option group is wrapped in <fieldset>/<legend>,
      legend text is the question'` — regex-asserts a `<fieldset>` whose
      `<legend>` text is the question, then slices the markup between
      `</legend>` and `</fieldset>` and asserts the radio options are
      inside that slice (not just present somewhere in the row).
  - **Part C**: the pre-existing test `'a tool_use with empty meta and no
    following lead event renders as the finish summary, not a fact_check
    claim'` used `team.status: 'running'` — exactly the scenario this
    cycle's new transient state now intercepts, so its old assertion
    (`[Finish summary]`) would have become wrong under the new behavior.
    Renamed to `'...renders as the finish summary once the run has ended'`
    and its instance status changed to `'finished'` (a genuinely terminal
    poll, where the disambiguation is unambiguous) — same assertions,
    still passing, still exercising the same code path for the terminal
    case. Three new tests added in its place for the running/transient
    behavior:
    - `'a trailing tool_use with empty meta renders a transient pending
      state while team.status is still "running"'` — the exact scenario
      the old test used to (mis)cover; asserts `kind-pending-
      classification` renders and neither `[Finish summary]` nor
      `fact_check:` appear.
    - `'the transient pending state resolves to fact_check once the paired
      tool_result arrives on a later poll'` — first poll shows the
      transient state; a second `pollTeamFeed()` call delivers the paired
      `tool_result` with `meta.found`; asserts the transient class is gone
      and it resolves to the fact_check rendering.
    - `'the transient pending state resolves to finish once a terminal
      status arrives on a later poll'` — first poll (status `running`)
      shows the transient state; a second `rerenderRow()` with status
      `'finished'` (no new event) asserts the transient class is gone and
      it resolves to `[Finish summary]`.

No changes to any Python file, any backend route, or any other frontend
test file (`test_deploy_frontend.js`/`test_singleton_toggle_frontend.js`/
`test_upload_frontend.js`).

## Key decisions / tradeoffs

- **The `aria-checked` ambiguity (`docs/spec.md` part B) resolves to: it
  does not apply anywhere in this codebase's current markup.**
  `docs/design.md`'s "Accessibility & platform notes" section says:
  *"Filter pills should be `<button>` or `<input type="radio">` with
  `aria-pressed="true"` / `aria-checked="true"` for selected pill."* This
  single sentence pairs the two attributes with the two *alternative*
  implementations of the *same* element (filter pills) — `<button>` →
  `aria-pressed`, `<input type="radio">` → `aria-checked` — not two
  different elements. `app/app.py`'s `renderTeamFeed()` already renders
  filter pills as `<button class="team-feed-pill">` (confirmed by reading
  the code before making any change), so `aria-pressed` is the attribute
  that applies, which part B's own preceding bullet already calls out
  explicitly ("`aria-pressed` ... on each per-agent filter pill button").
  Separately, the escalation panel's own options are native
  `<input type="radio">`/`<input type="checkbox">` elements (confirmed in
  `renderEscalationPanel()`) — but design.md's escalation-specific
  guidance ("Escalation form: `<fieldset>` for radio/checkbox groups with
  `<legend>` for the question") never mentions `aria-checked` for them,
  and native form controls' `checked` DOM property is already exposed to
  assistive tech without a redundant `aria-checked` attribute (standard
  ARIA authoring-practices guidance: don't add ARIA state attributes that
  duplicate what a native control's own semantics already convey). So
  `aria-checked` was not added anywhere — `aria-pressed` on the button
  pills and `<fieldset>`/`<legend>` on the native option group are the
  complete, correct implementation of design.md's own text as written.

- **The escalation panel's `<legend>` *replaces* the previously-separate
  `.team-escalation-question` div, rather than duplicating its text.**
  `docs/spec.md` part B leaves the legend's exact placement to the
  developer ("developer's call, document the choice"). Rendering the
  question both as a plain, visible div *and* again as the fieldset's
  legend would have produced two visually-identical lines stacked on top
  of each other. Instead, the `<legend class="team-escalation-question">`
  element now *is* the visible question line (same class, same text, same
  visual position — CSS resets the browser's default fieldset border/
  padding and legend padding so the rendered layout is pixel-identical to
  before), and the old separate div was removed. This keeps exactly one
  visible question line while still giving the option group a
  screen-reader-visible association with the question via `<legend>`.

- **The transient "pending classification" state is gated on
  `e.agent === 'lead'` in addition to `!next` and `status === 'running'`.**
  `docs/spec.md`'s own acceptance criterion wording is specifically about
  "the buffer's own last **lead-agent** event" — `findNextLeadEvent()`
  already only searches `leadEvents` (pre-filtered to `agent === 'lead'`),
  so for a non-lead event `indexOf(e)` returns `-1` and `next` is always
  `null` regardless of position, which without the explicit agent guard
  would have made teammates' own (hypothetical) empty-`meta` `tool_use`
  events also flicker through the transient state. The guard keeps every
  non-lead event's classification byte-for-byte unchanged from before this
  cycle (falls straight to `'finish'`, same as always) and confines the
  new behavior to exactly the scenario the spec describes.

- **Copy for the transient state (`'⋯ pending…'`) is a developer choice, not
  literally specified.** `docs/spec.md` part C describes the target
  behavior as "a transient '…' / pending-classification state" without
  committing to exact wording. A bare ellipsis alone seemed too cryptic for
  a log line a human operator is reading in real time; `'⋯ pending…'` keeps
  the visual "still working" cue while being self-explanatory, and its own
  `kind-pending-classification` CSS class (dimmed, italic) visually
  distinguishes it from both the eventual fact_check and finish renderings
  it resolves into.

## Deviations from spec
None. All three pieces match `docs/spec.md`'s "Proposed approach" as
written; the `aria-checked` ambiguity was resolved by reading
`docs/design.md`'s exact wording as instructed (see "Key decisions" above),
not by guessing or re-designing.

## Known limitations
- The transient `'pending-classification'` state is only reachable in a
  real, un-mocked deployment during the sub-millisecond-to-4-second window
  `docs/spec.md`'s own "Background" describes (the lead's `tool_use` and
  paired `tool_result` transcript entries are written synchronously,
  sub-millisecond apart, relative to the 4s poll cadence) — this cycle
  does not (and per `docs/spec.md`'s Non-goals, is not asked to) make that
  window provably unreachable; it only ensures it renders correctly *if*
  hit, which the new tests construct directly rather than relying on
  timing to reproduce.
- No visual/screen-reader manual smoke test was performed (e.g. an actual
  screen reader announcing the `aria-live="polite"` region, or a real
  browser rendering the reset `<fieldset>`/`<legend>` box model) — coverage
  here is markup-presence assertions against the real rendered `<script>`,
  the same technique and the same level of rigor every other test in this
  file already uses; a full manual accessibility audit is out of this
  cycle's scope per `docs/spec.md`'s Non-goals ("this cycle implements
  [design.md's] spec, it doesn't design new UI").

## How to verify locally

Frontend tests (this cycle's own):
```
node tests/test_team_frontend.js
```
Expect `ALL PASS (59/59)` (52 baseline + 1 for part A + 3 for part B + 3 for
part C, replacing 1 renamed test — net +7).

Full Node suite (no regressions in the other three files, all unchanged):
```
node tests/test_deploy_frontend.js             # 9/9
node tests/test_singleton_toggle_frontend.js    # 15/15
node tests/test_team_frontend.js                # 59/59
node tests/test_upload_frontend.js              # 8/8
```
Total: 91/91 (baseline 84 + 7 new).

Full Python suite (untouched by this cycle, re-run to confirm no
regression):
```
python3 -m unittest discover -s tests   # 792/792 OK, unchanged
```

Manual spot-check of the real rendered markup (this project's established
extract-and-inspect technique, same one every test in `test_team_
frontend.js` already uses under the hood):
```
TOTP_SECRET=JBSWY3DPEHPK3PXP python3 -c "
import sys; sys.path.insert(0, 'app')
import app as appmod
print(appmod.render_page())
" | grep -o 'role="log"[^>]*aria-live="polite"'
```
