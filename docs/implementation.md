# Implementation: Surface a finished team run's `summary` in `/status`'s team block

## Summary
`/status`'s per-project `team` object now includes a `summary` field (string
or `null`), read straight off the run's persisted `state["summary"]` — the
same field `finish(summary=...)` already populates but the API never
exposed. The dashboard's team panel now renders that text as a small
`.team-sub` line directly under the "Finished" status strip whenever it's
non-empty, reusing the existing `escalatedNote` sibling-block pattern
verbatim, so a lead that calls `finish` after giving up no longer looks
byte-for-byte identical to a real success.

## Root cause
N/A (this is a feature/gap-closing change, not a bugfix against a specific
reported defect with a single root cause) — see docs/spec.md's
"Background" section for the full investigation: `finish` is the lead's
only way to conclude a run, its tool schema has no success/failure
distinction, and `team_step()` always sets `status = "finished"` regardless
of whether the lead completed the task or gave up and explained why in
`summary`. That `summary` text was already captured server-side but never
reached `/status` or the dashboard.

## Changes by file
- `app/app.py` — status handler team dict (~L6001-6025)
  - Added one additive key, `"summary": run.get("summary") if run is not
    None else None`, alongside the existing `"members"`/`"lead"` keys,
    following the exact same "read straight off the persisted state dict"
    pattern those two already use. No new helper, no status-gating — for
    any non-`finished` status this is `None` today since only the `finish`
    tool branch in `team_step()` ever sets `state["summary"]`.
- `app/app.py` — embedded dashboard JS, `renderTeamPanel`-equivalent
  caller (~L4407-4432)
  - Added a `finishedSummary` sibling block, placed directly after the
    existing `escalatedNote` line and before `escalationPanel` in the
    returned markup string:
    ```js
    const finishedSummary = (team.status === 'finished' && team.summary) ?
      '<div class="team-sub">' + esc(team.summary) + '</div>' : '';
    ```
    Reuses the existing `.team-sub` CSS class (already defined, ~L2909)
    verbatim — no new CSS. `esc()` (already used throughout this file for
    other model/user-supplied text) HTML-escapes the summary since it's
    lead-model-generated text. Gated on `team.status === 'finished'` first,
    so a non-empty `team.summary` on any other status (shouldn't normally
    happen per the backend's own default, but defensive per the spec's
    edge cases) never renders.
- `tests/test_team_routes.py` (`StatusRosterAndCompositionTests`)
  - `test_summary_field` (new) — mirrors `test_escalation_kind_field`'s
    multi-status-case-dict style: `summary` is `None` for every
    non-`finished` status (`running`, `blocked_ask_user`,
    `blocked_board_write`, `escalated_max_rounds`, `error`, `stopped`),
    then confirms the one case that does carry a value — a `finished` run
    with `state["summary"]` set to a specific string — round-trips exactly
    through `/status`.
  - `test_summary_field_none_when_no_run_ever_started` (new) — mirrors
    `test_terminal_field_false_when_no_run_ever_started`'s pattern for the
    `run is None` case.
  - `test_status_idle_when_no_run_ever_started` (existing, updated) — this
    test does an exact full-dict `assertEqual` against `team` for the
    no-run-ever-started case; added `"summary": None` to the expected dict
    since the new key is additive to that same dict. This was the only
    exact-full-dict-match test against `team` in the repo (confirmed via
    grep); every other status/field test asserts a single key, so no other
    test needed updating for the new additive key.
- `tests/test_team_frontend.js`
  - Four new tests placed directly after the existing "finished and error
    render their own status classes, no subtitle" test, using the file's
    established `inst(name, team, overrides)` helper with a `summary`
    field added to the `team` object:
    - non-empty `summary` on a `finished` run renders both the unchanged
      "Finished" label and a `.team-sub` line with the exact escaped text
      (AC4).
    - empty-string `summary` renders no `.team-sub` block at all (AC5).
    - a `summary` containing `<script>...</script>` is HTML-escaped, not
      injected raw (security hygiene, matches this file's own established
      injection-guard convention elsewhere).
    - a non-`finished` status (`running`) with `team.summary` set anyway
      never renders the summary text (AC6, the defensive gating case).

## Key decisions / tradeoffs
- Followed docs/spec.md's proposed backend/frontend snippets and
  placement verbatim (single added dict key; `finishedSummary` as a
  sibling block, same shape as `escalatedNote`) — the spec had already
  made and justified these choices, so no re-derivation.
- Added an HTML-injection regression test for the summary line beyond
  what docs/spec.md's own acceptance criteria literally listed (which
  cover the escape behavior only implicitly via "HTML-escaped, styled
  with `.team-sub`") — matches this test file's own established pattern
  of an explicit injection-guard test for any newly-rendered
  model/user-supplied text field (e.g. the task-text round-trip test), and
  is cheap given the harness was already being extended for this feature.

## Deviations from spec
None. Implemented backend and frontend changes exactly as
docs/spec.md's "Proposed approach" specified, and did not extend the
same treatment to `error` status, per the spec's explicit non-goal.

## Known limitations
- Per docs/spec.md's own "Edge cases": no truncation is applied to long
  `summary` text (matches existing precedent elsewhere in this panel);
  no markdown/newline-aware rendering (plain escaped text in a `<div>`,
  same as `escalatedNote`). Neither was in scope for this change.
- Per docs/spec.md's non-goals: this is purely additive display of the
  lead's own free text — no success/failure classification is attempted
  or added anywhere in this change.

## How to verify locally
```bash
cd /home/dev/projects/ai-dev-switchboard

# Backend: the two new tests plus the one updated exact-dict-match test
/home/dev/.local/bin/uv run --with pytest python -m pytest \
  tests/test_team_routes.py -k "summary or test_status_idle_when_no_run_ever_started" -v

# Backend: full file (131 tests)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_team_routes.py -q

# Frontend: real extracted <script>, all 110 tests including the 4 new ones
node tests/test_team_frontend.js

# Full existing suite (confirms no regressions elsewhere). 3 pre-existing,
# unrelated failures in tests/test_teams_grounding.py are environment-
# specific (this sandbox has a real CLAUDE.md at the repo root, which
# those grounding-discovery tests don't expect) -- reproduced identically
# on a clean `git stash` of this change, so confirmed pre-existing, not
# caused by this diff. One additional test in tests/test_teams_headless.py
# (test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask) is
# a previously-disclosed timing flake (unrelated file, untouched by this
# diff) that failed once and passed on immediate re-run.
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q
# -> 1271 passed, 3 pre-existing grounding failures, 3 skipped
```
