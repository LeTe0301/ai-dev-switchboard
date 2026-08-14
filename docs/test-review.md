# Test & Review: 6f part 2 follow-ups (BACKLOG item 12)

## Scope
Covers all three items in `docs/spec.md` (BACKLOG item 12): **A** — permanent
regression test for the escalation-panel "already answered" race branch,
**B** — the ARIA attributes `docs/design.md`'s existing 6f part 2
"Accessibility & platform notes" section specifies (`role="log"`/
`aria-live="polite"`, `aria-pressed`, `<fieldset>`/`<legend>`), **C** — a
transient "pending classification" rendering state for the fact_check-vs-
finish poll-boundary edge case. Frontend-only (`app/app.py`'s `PAGE_TEMPLATE`
JS/CSS, `tests/test_team_frontend.js`); no Python file touched, confirmed by
`git diff --name-only` against the working tree.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Permanent test proves the "already answered" branch (`waiting_on_you: true` cached + fresh `/team/inbox` `{"pending": false}`) renders distinct copy, no submit form | Automated, `tests/test_team_frontend.js` | pass | `node tests/test_team_frontend.js` → `PASS - waiting_on_you true but a fresh /team/inbox already reports pending:false renders "already answered", no form` |
| 2 | "Already answered" is distinct from the normal question-form state | Automated, reviewer-written adversarial test (same run_id family, `cached.pending: true`) | pass | scratch harness run, `PASS - [ADVERSARIAL] "already answered" is distinct from the normal question-form state` |
| 3 | "Already answered" is distinct from a genuine `/team/inbox` fetch failure | Automated, reviewer-written adversarial test (500 response → `cached === null`) | pass | scratch harness run, `PASS - [ADVERSARIAL] "already answered" is distinct from a genuine /team/inbox fetch failure (500)` |
| 4 | `.team-feed-list` carries `role="log"` and `aria-live="polite"` in real rendered markup | Automated + manual extraction, real `<script>` from `app.render_page()` | pass | `node tests/test_team_frontend.js` → `PASS - the event feed list container carries role="log" and aria-live="polite"`; manual `grep` on `render_page()` output confirms `role="log" aria-live="polite"` on the template string |
| 5 | Filter pills carry `aria-pressed`, value toggles on selection change | Automated (2-pill toggle) + reviewer adversarial (3-pill, exactly-one-true invariant) | pass | `PASS - per-agent filter pills carry aria-pressed, toggling true/false as the selected pill changes`; `PASS - [ADVERSARIAL] filter pills with 3 agents: only the actually-selected pill is aria-pressed=true, all others false` |
| 6 | Escalation option group wrapped in `<fieldset>`/`<legend>`, legend text is the question, options inside the fieldset | Automated, regex + slice-between-tags assertion on real markup | pass | `PASS - the escalation option group is wrapped in <fieldset>/<legend>, legend text is the question` |
| 7 | Trailing empty-meta `tool_use` (last lead event) while `status === 'running'` renders transient state, not finish/fact_check | Automated | pass | `PASS - a trailing tool_use with empty meta renders a transient pending state while team.status is still "running"` |
| 8 | Transient state resolves to fact_check once paired `tool_result` arrives on a later poll | Automated | pass | `PASS - the transient pending state resolves to fact_check once the paired tool_result arrives on a later poll` |
| 9 | Transient state resolves to finish once a terminal status arrives on a later poll | Automated | pass | `PASS - the transient pending state resolves to finish once a terminal status arrives on a later poll` |
| 10 | Renamed test (`status: 'running'` → `'finished'`) is a legitimate adaptation, not weakened coverage | Reviewer trace: called `teamFeedEventKindClass`/`teamFeedEventBody` directly against the real extracted script with the OLD test's exact input (`status: 'running'`) | pass | `status=running -> kindClass=pending-classification, body="⋯ pending…"` (old assertion `[Finish summary]` now provably false under the old scenario — genuinely intercepted, not cosmetic); `status=finished -> kindClass=finish, body="[Finish summary] All done: summary text"` (renamed scenario genuinely hits finish) |
| 11 | Adversarial: trailing empty-meta `tool_use` (last lead event) while `status === 'blocked'` (not running, not terminal) | Reviewer-written adversarial test | pass (see Findings #1 for the resulting observation) | `PASS - [ADVERSARIAL] trailing empty-meta tool_use with team.status "blocked" ... falls to finish, per the literal status==="running" gate` |
| 12 | `aria-checked` resolution ("applies nowhere") matches `docs/design.md`'s literal text | Manual re-read of `docs/design.md` lines 991-994 | pass | Quoted below in Review pass |
| 13 | `<fieldset>`/`<legend>` placement choice is reasonable and documented | Manual re-read of `docs/design.md` line 994 + `docs/implementation.md` "Key decisions" | pass | See Review pass |

## Regression check
- `tests/test_team_frontend.js`: `node tests/test_team_frontend.js` → `ALL PASS (59/59)` (52 baseline + 7 new, matches `docs/implementation.md`'s own count).
- Full Node suite: `test_deploy_frontend.js` 9/9, `test_singleton_toggle_frontend.js` 15/15, `test_team_frontend.js` 59/59, `test_upload_frontend.js` 8/8 → **91/91**, no regressions in the three unchanged files.
- Full Python suite: `python3 -m unittest discover -s tests` → **`Ran 792 tests ... OK`**, unchanged (no Python file touched; re-ran anyway per this cycle's own convention). The "duplicate session: team-sessionrace-p4079817" lines in the output are pre-existing log noise from an unrelated test, not new failures.
- Working-tree diff confirmed scoped to exactly `app/app.py`, `docs/implementation.md`, `tests/test_team_frontend.js` (`git status --porcelain`) — no other file touched, satisfying `docs/spec.md`'s Non-goals ("No new UI surface, no new route, no backend change").

No defects found in the testing pass — proceeding to the review pass.

---

## Spec coverage

| Acceptance criterion (`docs/spec.md`) | Implemented | Tested | Notes |
|---|---|---|---|
| Permanent test for "already answered" race branch | Yes (test only; production code pre-existing) | Yes (#1 above, plus reviewer's #2/#3 for the three-way distinctness) | Full coverage |
| `role="log"`/`aria-live="polite"` on feed list, verified via real rendered markup | Yes | Yes (#4) | Attributes on `.team-feed-list` specifically, not the outer wrapper — matches design.md's intent (the element that actually gains new rows) |
| Filter pills carry the ARIA state attribute(s) design.md specifies, with a toggle test | Yes (`aria-pressed` only — see `aria-checked` resolution below) | Yes (#5, incl. reviewer's 3-pill invariant test) | |
| Escalation option group wrapped in `<fieldset>`/`<legend>` | Yes | Yes (#6) | |
| Transient state for trailing empty-meta `tool_use` while `status === 'running'` | Yes | Yes (#7) | |
| Transient state resolves correctly once resolved by a later poll | Yes | Yes (#8, #9) | Both resolution paths (fact_check and finish) independently tested |
| Full existing suite passes, no regression | Yes | Yes | Python 792/792, Node 91/91 |

All seven acceptance criteria are implemented and independently verified by tests I ran myself this session (developer's tests plus my own adversarial constructions). No gaps.

## Review pass

### `aria-checked` resolution vs. `docs/design.md`'s literal text
`docs/design.md` line 993: *"Filter pills should be `<button>` or
`<input type="radio">` with `aria-pressed="true"` / `aria-checked="true"` for
selected pill."* This is one sentence pairing `aria-pressed`/`aria-checked`
with the two *alternative* implementations of the same element (filter
pills as `<button>` vs. as `<input type="radio">`), not two different UI
elements. `app/app.py`'s `renderTeamFeed()` renders pills as
`<button class="team-feed-pill">` — confirmed directly in the diff and in
the extracted rendered markup — so `aria-pressed` is the attribute that
applies, and `aria-checked` correctly does not appear anywhere. Separately,
design.md's escalation-specific line 994 ("`<fieldset>` for radio/checkbox
groups with `<legend>` for the question") never mentions `aria-checked` for
the escalation inputs either. The developer's reading in
`docs/implementation.md` "Key decisions" is accurate to design.md's literal
wording, not a favorable paraphrase — confirmed by reading the design doc
directly rather than trusting the summary.

### `<fieldset>`/`<legend>` placement
`docs/spec.md` explicitly left this to the developer's judgment. The chosen
approach — the `<legend class="team-escalation-question">` *replaces* the
previously-separate `.team-escalation-question` div (same class, same text,
CSS-reset to look identical) rather than duplicating the question text —
avoids a visually duplicated line while still giving the fieldset its
required screen-reader association. Verified in the diff
(`app/app.py`: the old `<div class="team-escalation-question">` line is
deleted, its class/content moved onto the new `<legend>`) and confirmed
structurally correct by test #6's slice-between-`</legend>`-and-`</fieldset>`
assertion (radios are genuinely inside the fieldset, after the legend, not
just present somewhere in the row).

### Correctness
- The `status === 'running'` gate for the transient state is applied
  literally as `docs/spec.md`'s Background/Proposed-approach text specifies.
  Test #11 (reviewer-constructed) confirms that a `status: 'blocked'` value
  with the same trailing empty-meta event does **not** get the transient
  treatment — it falls straight to `'finish'`, same as pre-cycle behavior.
  This is not a bug against this cycle's spec (which explicitly scopes the
  fix to `status === 'running'` and to a race the reviewer already
  documented as practically unreachable), but it's worth flagging as a
  should-fix-in-a-later-cycle observation — see Findings below — since the
  underlying rendering ambiguity (a trailing empty-meta `tool_use` with no
  paired `tool_result` yet) is equally present, in principle, whenever the
  run hasn't reached a truly terminal status, not just while `running`.
- `e.agent === 'lead'` guard in the transient-state condition: confirmed
  necessary and correctly scoped — without it, a non-lead agent's own
  (hypothetical) empty-meta `tool_use` would also match `!next` (since
  `findNextLeadEvent` only searches `leadEvents`, `indexOf` returns `-1` for
  non-lead events, always yielding `next === null`), which would have
  incorrectly extended new behavior beyond the spec's literal "last
  lead-agent event" wording. The guard prevents that.
- Renamed test is a genuine adaptation, not weakened coverage — confirmed
  via direct function-level trace (#10 above): the old scenario's inputs,
  re-run against the real post-diff script, now produce
  `pending-classification`, provably falsifying the old test's original
  assertion under the old inputs. The rename is not cosmetic.

### Security
No new user input handling introduced. `esc(cached.question)` is correctly
applied to the new `<legend>` content (same escaping already used
throughout this file for LLM-authored / operator-authored text). No new
routes, no new fetch targets, no new SQL/shell/command surfaces. Nothing in
this diff touches authn/authz or secrets.

### Simplicity / scope
The diff is minimal and proportionate to the three-item spec: no new
abstractions, no speculative generality. Threading a third `status`
parameter through `teamFeedEventKindClass`/`teamFeedEventBody`/
`renderTeamFeedEvent` is the smallest change that satisfies part C without
introducing global state. The CSS resets for `<fieldset>`/`<legend>` are
scoped narrowly (only the two new selectors) and exist specifically to keep
the visual layout unchanged, which is a reasonable, minimal tradeoff for
adding required a11y structure without a visual regression — appropriately
flagged in `docs/implementation.md`'s "Known limitations" as not
manually/visually verified, consistent with this file's existing
markup-presence-only testing convention.

## Findings (most severe first)

### 1. Transient "pending classification" gate is scoped to `status === 'running'` only, not to "run hasn't reached a terminal status" — should-fix (follow-up, not blocking)
- File: `app/app.py`, `teamFeedEventKindClass()`, the
  `if (!next && e.agent === 'lead' && status === 'running') return 'pending-classification';`
  line.
- Issue: the condition matches `docs/spec.md`'s literal wording exactly, but
  the wording itself only anticipated the `running` case. A trailing
  empty-meta `tool_use` from the lead with no paired `tool_result` yet, and
  `team.status === 'blocked'` (e.g. the lead is mid-way through emitting a
  fact_check tool_use/tool_result pair right as an `ask_user` escalation
  from a **different**, in-flight round gets appended and flips status to
  `blocked` before the pair completes — a narrower but structurally similar
  poll-boundary race to the one this cycle already fixed for `running`)
  still falls through to `'finish'`, i.e. the exact assumed-finish bug this
  cycle was written to eliminate, just gated to a status this cycle didn't
  cover.
- Failure scenario: `team.status: 'blocked'`, last lead event is an
  empty-meta `tool_use` with no next lead event yet — renders
  `[Finish summary] ...` instead of the transient state, even though the
  run has not actually finished (confirmed empirically, test case #11
  above).
- This does not block approval: `docs/spec.md`'s own Non-goals disclaim
  "attempting to make the poll-boundary race... provably unreachable," and
  the implementation matches the spec's literal, deliberately-narrow scope
  exactly — this is a spec-scoping observation for a future cycle, not an
  implementation defect against this cycle's spec.

### 2. No visual/manual accessibility smoke test performed — nit
- File: `docs/implementation.md` "Known limitations" (already
  self-disclosed).
- Issue: no real screen reader or browser was used to confirm the
  `aria-live="polite"` region announces correctly, or that the
  `<fieldset>`/`<legend>` CSS reset actually renders pixel-identical to the
  pre-cycle layout in a real browser (only markup-presence assertions and a
  `grep` on the rendered template were used).
- Not blocking: this matches the same level of rigor every other test in
  `tests/test_team_frontend.js` already uses (this codebase has no browser/
  screen-reader test harness), and `docs/spec.md`'s Non-goals explicitly
  scope this cycle to implementing already-specified attributes, not a full
  accessibility audit.

## Follow-ups (non-blocking)
- Consider widening the transient-classification gate from
  `status === 'running'` to `status !== 'finished' && status !== 'error'`
  (or equivalent "non-terminal" check) in a future cycle, per Finding #1.
- A manual screen-reader/browser smoke test of the new `role="log"`/
  `aria-live`/`fieldset`/`legend` markup would be a reasonable low-cost
  addition whenever this codebase's first browser-based test harness (if
  ever added) lands — not warranted as a one-off for this cycle.

## Overall verdict
**Approve.** All seven acceptance criteria are implemented and verified by
tests I ran myself this session (the developer's new tests plus my own
adversarial constructions covering the three-way "already answered"
distinctness, the 3-pill `aria-pressed` invariant, the `blocked`-status
edge case, and a direct function-level trace proving the renamed test is a
genuine adaptation). Full regression suite is clean: Python 792/792, Node
91/91. The `aria-checked` and `<fieldset>`/`<legend>`-placement developer
calls both check out against `docs/design.md`'s literal text. One
should-fix-later observation (Finding #1) and one pre-existing, self-
disclosed nit (Finding #2) — neither blocks this cycle.
