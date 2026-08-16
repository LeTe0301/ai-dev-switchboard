# Spec: Surface a finished team run's `summary` in `/status`'s team block

## Summary
`/status`'s `team` object never includes the run's `summary` field, so a team
lead that gives up and calls `finish(summary="...")` with an honest
explanation of failure looks byte-for-byte identical, in both the API and the
dashboard, to a run that actually succeeded — this exposes `summary`
(already captured server-side for every `finish`-concluded run) through
`/status` and the dashboard status strip so a human can tell the two apart
without opening `run.json` by hand.

## Background / current state / investigation findings

This is backlog item 45. The backlog entry left it explicitly unconfirmed
whether this is a real code gap or just small-model (`qwen3:8b`) tool-choice
variance between round 6 (lead called `ask_user`, a genuine escalation) and
round 10 (lead called `finish` with an apologetic summary, no work done).
Investigated `app/teams.py` and `app/app.py` directly before writing this
spec; findings:

1. **The lead has exactly one way to conclude a run: `finish`.** Its tool
   schema (`app/teams.py` `_lead_tools()`, ~L2248-2252) is
   `{"name": "finish", "parameters": {"summary": {"type": "string"}},
   "required": ["summary"]}` — a single free-text field, no
   success/failure/partial enum or boolean anywhere in the schema. The tool
   description is just `"Conclude the task with a summary."` There is no
   separate `give_up`/`error`-kind tool. When `finish` is called,
   `team_step()` (~L3368-3373) unconditionally does
   `state["status"] = "finished"; state["summary"] = args["summary"]` —
   it does not (and structurally cannot) distinguish "I completed the task"
   from "I'm concluding because I couldn't." Round 6 vs round 10 is the
   model choosing between two *already-existing, both example-correct*
   tools (`ask_user` to escalate, or `finish` to conclude-and-explain) —
   not a parsing bug, not a misrouted action, not something
   `_validate_lead_action()` gets wrong. Confirmed: this is a real,
   structural gap in the app's status model, independent of which specific
   round's model behavior triggered it — `finished` can never mean
   anything other than "the lead called finish," and the app currently
   throws away the one piece of context (`summary`) that would let a human
   tell honest-failure-via-finish apart from a real success.

2. **`summary` is populated only via the `finish` path, and is already
   captured but never surfaced.** `_new_state()` (`app/teams.py` ~L2811)
   initializes every run with `"summary": None, "error": None`. Of the four
   terminal statuses (`teams.TEAM_TERMINAL_STATUSES = ("finished",
   "escalated_max_rounds", "error", "stopped")`), only the `finish` branch
   ever sets `state["summary"]`; `escalated_max_rounds`/`error`/`stopped`
   leave it `None` (their own terminal-ness is already otherwise
   explained: `escalated_max_rounds` has a max-rounds status strip message,
   `error` has `state["error"]`, `stopped` is a deliberate human-initiated
   stop). So `summary` is a `finish`-only field today, exactly the field
   the E2E tester already confirmed "plainly describes the failure" when
   they manually opened `run.json` — it just never reaches `/status` or the
   dashboard.

3. **`/status`'s team block does not include `summary` at all.** The dict
   built in `app/app.py`'s status handler (~L6001-6016) is `{"status":
   ..., "run_id": ..., "composition": ..., "waiting_on_you": ...,
   "escalation_kind": ..., "terminal": ..., "members": ..., "lead": ...}`
   — no `summary` key, no `error` key.

4. **The frontend needs a (small) matching change, not just the JSON
   field, to actually surface this to a human.** `renderTeamStatusStrip()`
   (`app/app.py` ~L3755-3777) renders `status === 'finished'` as a bare
   `'Finished'` string; it does not read any other field. Adding `summary`
   to the JSON response alone would make it visible only via devtools/raw
   fetch, not on the dashboard a human is actually watching — so this spec
   includes a small, pattern-matching frontend addition (see below), not
   just a backend field.

**Conclusion: this is a real, worth-fixing gap, and it is fixable
independent of root cause.** Whether or not round 6 vs round 10's specific
tool choice was small-model non-determinism, the underlying problem is that
the app's status model conflates "lead completed the task" and "lead gave
up and said so" into one identical-looking `finished` state, while already
possessing (and discarding, display-wise) the one field that tells them
apart. Rejected direction: a new distinct `give_up`/error-kind tool for the
lead (backlog's option (a)). Two reasons: (i) it doesn't reliably fix
anything — an 8B model choosing between `finish` and a new `give_up` tool is
the same class of judgment call as choosing between `finish` and `ask_user`
today, so nothing guarantees the model calls it correctly either; (ii) it
would still need this exact same `/status`+frontend surfacing fix on top to
actually reach a human, plus new prompt guidance, more schema surface, and
new tests, for a benefit this simpler fix already captures. Going with
backlog's option (b): surface `summary` universally.

## Goals
- `/status`'s per-project `team` object includes the run's `summary` field
  (string or `null`) for every run that has one, regardless of terminal
  status, not just conceptually-successful ones.
- The dashboard's team status strip shows that summary text (visibly, not
  just in the JSON) when a run has finished, so a human glancing at the
  dashboard — not just someone willing to open devtools or `run.json` — can
  tell a self-reported-failure `finish` apart from a real completion.

## Non-goals
- No new tool for the lead (`give_up`/error-kind or otherwise) — see
  "Conclusion" above for why this is deliberately not pursued now.
- No change to `finish`'s tool schema, its system-prompt description, or
  `_validate_lead_action()`'s handling of it.
- No attempt to programmatically classify a `summary` string as
  "success-shaped" vs "failure-shaped" (e.g. keyword sniffing for "failed"/
  "could not"). The fix is making the model's own honest text visible, not
  second-guessing or re-interpreting it.
- Not extending `error` (state carrying `state["error"]`, a short
  operational message like "Ollama unreachable") into a similarly-visible
  status-strip detail line. `error` status is already visually and
  semantically distinct (red `status-error`, a genuinely different failure
  mode — transport/backend failure, not a lead's own judgment call) and
  isn't the case backlog item 45 is about. Could be a quick, independent
  follow-up; out of scope here to keep this change narrowly scoped to the
  one confirmed gap.
- No change to `escalated_max_rounds`'s or `stopped`'s status-strip
  copy — those already have their own explanatory text
  (`escalatedNote`/`"Blocked — Max rounds reached"`) and never carry a
  `summary` value regardless of this change.

## Proposed approach

### Backend (`app/app.py`, status handler, ~L6001-6016)
Add one key to the `inst["team"]` dict already built there:
```python
"summary": run.get("summary") if run is not None else None,
```
placed alongside the existing `"members"`/`"lead"` keys (same "read
straight off the persisted state dict" pattern those two already use — no
new helper needed). This is additive-only: every existing key/value in the
dict is unchanged. For any non-`finished` status this is simply `None`
today (see "Background" point 2), so no extra status-gating logic is
needed on the backend side — the frontend decides when to render it (see
below), matching how `waiting_on_you`/`escalation_kind` already separate
"data always present" from "when the UI chooses to use it."

### Frontend (`app/app.py`, embedded dashboard JS)
In `renderTeamStatusStrip()` (~L3774), the `finished` branch currently
returns a bare status strip with no room for extra text (unlike the
`blocked` branch, which already conditionally appends
`escalatedNote`/`escalationPanel` as sibling elements in the caller). Two
options were available: (a) grow `renderTeamStatusStrip()` itself, or (b)
add a sibling `finishedSummary` block in the same caller
(`renderTeamPanel`-equivalent function around L4407-4411) exactly the way
`escalatedNote` already exists as a sibling of `statusStrip`. Use (b) — it
reuses the exact established pattern for "extra explanatory line under the
status strip" instead of inventing a second way to do the same thing:

```js
const finishedSummary = (team.status === 'finished' && team.summary) ?
  '<div class="team-sub">' + esc(team.summary) + '</div>' : '';
```

placed directly after the existing `escalatedNote` line, and included in
the same string-concatenation the caller already does for
`escalatedNote`/`escalationPanel` (so it renders as a fourth, one-off
sibling block right under the status strip, before the escalation panel —
harmless when `escalationPanel` is empty, which it always is for a
`finished` run since `waiting_on_you` is false there).

Reuses the existing `.team-sub` CSS class verbatim (already defined,
~L2909: `font-size: 12px; color: #888;`) — same muted, secondary-info
treatment `escalatedNote` already gets. No new CSS. `esc()` (already used
throughout this file for other user/model-supplied text) is required here
since `summary` is lead-model-generated text, not app-controlled.

## Affected areas
- `app/app.py` — status handler dict (~L6001-6016): one added key.
- `app/app.py` — embedded dashboard JS: `renderTeamStatusStrip()` callers
  (~L4407-4411), one added conditional line + inclusion in the returned
  markup string.
- `tests/test_team_routes.py` — extend `StatusRosterAndCompositionTests`
  (existing class, ~L864) with a new test for the `summary` field,
  following `test_terminal_field`'s exact pattern (~L1014-1043): set
  `state["status"]`/`state["summary"]` directly via `teamsmod._load_state`
  + `teamsmod._persist`, hit `/status`, assert
  `by_name[_PROJ]["team"]["summary"]`. Cover at minimum: `finished` with a
  set summary (non-null, exact text), and one non-`finished` status (e.g.
  `running` or `blocked_ask_user`) where it's `None`/absent-summary,
  mirroring `test_escalation_kind_field`'s multi-status-case-dict style
  (~L997-1010) if convenient rather than writing four separate tests.
- This is a single-file, single-layer change (both backend and frontend
  live in `app/app.py`) — no data model/migration/API-shape-breaking
  change, no separate ux-designer pass needed (see "Open questions" below).

## Edge cases
- `run is None` (no run ever started for this project) — `summary` must be
  `None`, same as every other run-scoped field in this dict already
  degrades to `None`/`False` in that case (matches `test_terminal_field_
  false_when_no_run_ever_started`'s existing precedent for the same
  no-run case).
- `finish` called with an empty-but-present `summary` string (`""`) — the
  tool schema requires the key but not a non-empty value.
  `team.summary` would be `""`, which is falsy in JS, so
  `finishedSummary` correctly renders nothing (no empty `<div
  class="team-sub"></div>` litter) — verify this is the desired behavior
  (it is: an empty string carries no information worth a UI line) rather
  than a special-cased bug.
- A `finished` run whose `summary` describes success (the common/intended
  case) — renders the same as a failure summary; this is deliberate (see
  non-goals: no success/failure classification). The line is simply always
  shown for a non-empty `finish` summary, success or failure alike; a
  reviewer/user reading it can already tell which is which from the prose
  itself, exactly as the E2E tester already could when reading `run.json`
  by hand.
- Very long `summary` text — no truncation is being added here; `esc()`
  handles HTML-safety but not length. `full_result_text`/other transcript
  fields elsewhere in this codebase are also rendered untruncated in the
  feed, so this matches existing precedent rather than introducing a new
  truncation policy. If this proves visually noisy in practice, that's a
  follow-up, not blocking this fix.
- `summary` containing newlines/markdown-like text — rendered as plain
  escaped text inside a `<div>` (browser default block-level wrapping,
  no `pre`/`white-space` handling) — matches how other free-text fields in
  this same panel (e.g. `escalatedNote`'s own text) are already rendered.

## Acceptance criteria
- [ ] Given a run whose lead called `finish(summary="X")`, when `GET
      /status` is polled, then that project's `team.summary` equals `"X"`.
- [ ] Given a run with any non-`finished` status (`running`,
      `blocked_ask_user`, `blocked_board_write`, `escalated_max_rounds`,
      `error`, `stopped`), when `GET /status` is polled, then
      `team.summary` is `None` (matching `state["summary"]`'s own default,
      since only `finish` ever sets it).
- [ ] Given no run has ever started for a project, when `GET /status` is
      polled, then `team.summary` is `None` (not a missing key, matching
      the existing degrade-to-None/False convention for this dict's other
      fields).
- [ ] Given a `finished` run with a non-empty `summary`, when the dashboard
      renders that project's team panel, then the status strip shows
      "Finished" AND a second line directly below it shows the summary
      text (HTML-escaped), styled with the existing `.team-sub` class.
- [ ] Given a `finished` run with an empty-string `summary`, when the
      dashboard renders that project's team panel, then no empty summary
      line is rendered (just "Finished", unchanged from today).
- [ ] Given any non-`finished` status, when the dashboard renders that
      project's team panel, then no summary line is rendered regardless of
      whatever value `team.summary` happens to hold (defensive — should
      always be `None` per the second criterion above, but the frontend
      check is gated on `team.status === 'finished'` first regardless).
- [ ] All existing `/status`-response tests continue passing unchanged
      (this is purely additive to the JSON shape).

## Open questions
- **Skipping ux-designer for this cycle** — assumption, not a blocker:
  this reuses an existing CSS class (`.team-sub`) and an existing
  compositional pattern (`escalatedNote` as a sibling block under the
  status strip) with zero new visual/interaction design decisions to make
  (no new color, no new component, no new state machine) — routing
  straight to `developer` per the product-manager role's own "skip
  ux-designer unless this genuinely needs a UI-visible change beyond just
  exposing a JSON field" guidance. If the developer or reviewer finds this
  reads as visually awkward in practice (e.g. summary text is too long for
  the row), loop back for a design pass then rather than pre-emptively
  designing for a problem not yet observed.
- **Whether to also do the same for `error`** — explicitly scoped out
  (see Non-goals) rather than silently bundled in, since it wasn't the
  confirmed gap and touches a different status/field. Flagging in case the
  user wants it folded into the same cycle instead of a follow-up — proceeding
  under the assumption they don't, since it wasn't asked for.

## Risk / rollback notes
Low risk: two small additive changes in one already-frequently-touched
file (`app/app.py`), no schema/migration, no new endpoint, no change to
existing field values or the lead's tool-calling behavior at all. If
`summary` text ever contains something visually disruptive in practice,
rollback is a one-line revert of the `finishedSummary` block (backend
field can stay — it's harmless dead data in the JSON even unused). No
existing behavior is removed, so this is easy to bisect/revert
independently from any other concurrent change.
