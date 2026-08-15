# Spec: 6f part 2 follow-ups (BACKLOG item 12)

## Why this is a small, orchestrator-authored cycle

Three non-blocking should-fix/nit items the reviewer recorded during 6f
part 2's own approval — all fully diagnosed already, no new product/design
decision, all inside already-shipped Teams-page code
(`app/app.py`'s `teamRow()` non-idle branch, `tests/test_team_frontend.js`).
No new UI surface is being added — this is test coverage + accessibility
attributes on existing rendering — so ux-designer is skipped, matching this
pipeline's own precedent for bugfix-shaped cycles. `docs/design.md`'s
existing 6f part 2 section (from `story/multi-agent-teams`, still present
in this branch's history) already specifies the exact ARIA attributes to
add — this cycle implements that spec, it doesn't design new UI.

## Background

Three items, found together during 6f part 2's review, all approved as
non-blocking at the time:

**A. Untested "already answered" race branch.** `renderEscalationPanel()`'s
`!cached.pending` branch — rendered when a cached `/status` snapshot still
says `waiting_on_you` but a freshly-fetched `GET .../team/inbox` already
reports `pending: false` (the escalation was answered between the last
`/status` poll and the panel opening) — was added by the developer beyond
`docs/design.md`'s own text. It's reachable and correct (the reviewer
confirmed with a targeted, uncommitted test at the time), but has zero
permanent coverage in `tests/test_team_frontend.js`.

**B. Missing ARIA attributes.** `docs/design.md`'s "Accessibility &
platform notes" section for 6f part 2 specifies `role="log"`/
`aria-live="polite"` on the event feed list, `aria-pressed`/`aria-checked`
on the per-agent filter pills, and `<fieldset>`/`<legend>` wrapping the
escalation option group. None of these were implemented in `app/app.py`.
Basic keyboard operability is intact today (native `<button>`/
`<input type="radio/checkbox">`/`<label>` elements throughout), so this
isn't a live defect, but it's the first scrollable log-like/live-region
panel in this codebase and the design doc's own recommendation was never
actually applied.

**C. fact_check/finish poll-boundary misclassification (self-healing).**
The positional disambiguation `docs/spec.md`'s 6f part 2 text specifies (a
`tool_use` event is a fact_check claim if immediately followed by a
`tool_result` with `meta.found`, otherwise rendered as the run's finish
summary) can transiently misclassify a fact_check claim as a finish
summary if its `tool_use` event lands in the client's event buffer before
the paired `tool_result` — e.g. split across two `GET .../team/events`
polls. The reviewer traced this to being practically unreachable (both
transcript entries are written in one synchronous server-side call,
sub-millisecond apart relative to the 4s poll cadence) and self-correcting
within one more poll — not a live bug, but worth a small rendering
refinement per the backlog item's own suggested direction: render an
explicit transient state for a `tool_use` event that is the event buffer's
own last lead-agent event while `team.status` is still `running`, rather
than assuming it's the finish summary.

## Non-goals

- No new UI surface, no new route, no backend change. This is test
  coverage (A), applying already-specified ARIA attributes (B), and one
  small rendering-logic refinement (C) inside already-shipped code.
- Not re-opening or re-designing any part of 6f part 2's already-approved
  interaction model.
- Not attempting to make the poll-boundary race (C) provably unreachable
  (it already effectively is, per the reviewer's own tracing) — just
  giving it a better transient rendering, per the backlog item's own
  stated direction, rather than leaving it silently assumed-as-finish.

## Proposed approach

### A. Regression test for the "already answered" race branch

Add a permanent test to `tests/test_team_frontend.js` driving exactly the
scenario the reviewer originally probed by hand: a cached `/status`
snapshot with `waiting_on_you: true`, followed by a `GET .../team/inbox`
fetch returning `{"pending": false}`. Assert `renderEscalationPanel()`
renders the "already answered" copy (distinct from both the normal
question-form state and the fetch-failure state) and does NOT render a
submit form.

### B. ARIA attributes per `docs/design.md`

Add, exactly as `docs/design.md`'s "Accessibility & platform notes"
section for 6f part 2 already specifies:
- `role="log"` and `aria-live="polite"` on the event feed's list container.
- `aria-pressed` (reflecting selected/not-selected state) on each per-agent
  filter pill button.
- `aria-checked` on... [developer: confirm from `docs/design.md`'s exact
  wording whether this applies to the filter pills or the escalation
  radio/checkbox inputs — the backlog item's summary conflates the two,
  the design doc is the source of truth].
- Wrap the escalation panel's option group in `<fieldset>`/`<legend>`
  (legend text: the pending question's own `question`/`header` text, or a
  reasonable fallback if that's awkward structurally — developer's call,
  document the choice).

Update `docs/implementation.md`'s "Deviations from spec" framing
retroactively if useful, but the main deliverable is the attributes
themselves plus a `tests/test_team_frontend.js` assertion that they're
present in the rendered markup for each relevant state.

### C. Transient rendering for the poll-boundary edge case

In the event-kind rendering logic (`teamFeedEventKindClass()`/
`teamFeedEventBody()` or wherever the fact_check-vs-finish disambiguation
currently lives), add the narrow additional check: if a `tool_use` event
with empty `meta` is the event buffer's own LAST lead-agent event AND
`team.status === "running"` (the run hasn't finished), render it as a
transient "…" / pending-classification state rather than assuming it's
the finish summary. Once the paired `tool_result` arrives on a later poll
(or the run's status moves to a terminal state), it resolves to whichever
of fact_check/finish is actually correct, same as today.

## Acceptance criteria

Each must be verifiable by running something, not by reading the diff.

- [ ] A permanent test in `tests/test_team_frontend.js` proves the
      "already answered" branch renders its distinct copy and no submit
      form, for the specific cached-`waiting_on_you`-true /
      fresh-`pending`-false scenario.
- [ ] The event feed list container carries `role="log"` and
      `aria-live="polite"` in rendered output — verified by extracting the
      real rendered markup (this project's established
      extract-and-inspect-the-real-`<script>`/DOM technique), not by
      reading the source only.
- [ ] Per-agent filter pills carry the ARIA state attribute(s)
      `docs/design.md` specifies, verified the same way, including a
      toggle test (attribute value actually changes when a pill is
      selected vs. not).
- [ ] The escalation option group is wrapped in `<fieldset>`/`<legend>` in
      rendered output.
- [ ] A `tool_use` event with empty `meta` that is the buffer's own last
      lead-agent event, while `team.status === "running"`, renders the new
      transient state — not the finish-summary text — verified with a
      targeted test constructing exactly that buffer state.
- [ ] Once a paired `tool_result` (or a terminal `team.status`) arrives on
      a subsequent poll, the transient state resolves correctly to
      fact_check or finish as appropriate — same disambiguation logic as
      before, just gated by the new transient check first.
- [ ] Full existing suite (Python: 792 baseline going into this cycle,
      Node: 84, with `test_team_frontend.js`'s own count growing by
      whatever this cycle adds) still passes with no regression.

## Risk / rollback

Frontend-only diff inside already-shipped, already-reviewed 6f part 2
code. Rollback is reverting the relevant `app/app.py` template sections
and the new tests. No backend/production-Python risk.
