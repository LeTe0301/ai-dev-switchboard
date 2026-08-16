# Test & Review: Surface a finished team run's `summary` in `/status`'s team block (backlog item 45)

## Scope
Covers all acceptance criteria in `docs/spec.md`: `/status`'s per-project
`team.summary` field (backend), the `.team-sub` display line under a
"Finished" status strip (frontend), the empty-string suppression case, the
defensive non-`finished` gate, and the no-run-ever-started `None` default.
Reviewed the actual diff (`app/app.py`, `tests/test_team_routes.py`,
`tests/test_team_frontend.js`) against `docs/spec.md` and
`docs/implementation.md`.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `finish(summary="X")` → `/status` `team.summary == "X"` | Automated | pass | `test_summary_field` (`tests/test_team_routes.py`), ran via `pytest tests/test_team_routes.py -k summary` |
| 2 | Non-`finished` status (`running`, `blocked_ask_user`, `blocked_board_write`, `escalated_max_rounds`, `error`, `stopped`) → `team.summary is None` | Automated | pass | same test, multi-status-case-dict loop over all 6 statuses |
| 3 | No run ever started → `team.summary is None` (not a missing key) | Automated | pass | `test_summary_field_none_when_no_run_ever_started` |
| 4 | `finished` + non-empty `summary` → "Finished" strip unchanged AND a `.team-sub` line with the escaped text | Automated | pass | `tests/test_team_frontend.js` new test, `node tests/test_team_frontend.js` |
| 5 | `finished` + empty-string `summary` → no `.team-sub` line rendered | Automated | pass | `tests/test_team_frontend.js` new test, asserts `subCount === 0` |
| 6 | Non-`finished` status → no summary line regardless of `team.summary` value (defensive gate) | Automated | pass | `tests/test_team_frontend.js` new test (`status: 'running'` with `summary` set) |
| 7 | Existing `/status`-response tests continue passing (purely additive JSON shape) | Automated | pass | full `test_team_routes.py` run, 131/131; the one pre-existing exact-full-dict test (`test_status_idle_when_no_run_ever_started`) was updated to include `summary: None` and passes |
| 8 | `summary` HTML-injection is escaped, not rendered raw | Automated | pass | extra test beyond the literal ACs, `<script>alert(1)</script>` case asserts `&lt;script&gt;` present, raw tag absent |

Additionally performed a revert-and-watch-it-fail check (not simulated): stashed only `app/app.py`'s change (kept the new tests), reran both suites. Result: 3/3 new backend tests failed with `KeyError: 'summary'` (including the updated exact-dict test), 2/110 frontend tests failed (the non-empty-summary and script-escaping cases; the empty-string and defensive-gate cases correctly did not fail, since their expected behavior — "render nothing" — degrades a `KeyError`'d `undefined` to falsy the same way, which is expected and not a gap). Restored the change; all suites returned to green. This confirms the new tests genuinely exercise the new code path rather than passing tautologically.

## Regression check
- `pytest tests/test_team_routes.py -q` → **131 passed**.
- `node tests/test_team_frontend.js` → **ALL PASS (110/110)**.
- Full suite: `pytest tests/ -q` → **1271 passed, 3 failed, 3 skipped** (160s). The 3 failures are all in `tests/test_teams_grounding.py` (`test_discovers_architecture_backlog_readme_no_claude_or_agents`, `test_load_grounding_against_this_repo_is_non_empty`, `test_grounding_subcommand_against_this_repos_own_tree`), all failing because this sandbox has a real `CLAUDE.md` at the repo root that those grounding-discovery tests don't expect (they assert exactly 3 discovered files; this environment has 4, `CLAUDE.md` included). Confirmed independently pre-existing and unrelated to this diff by re-running the same 3 tests against `git stash` (this diff fully reverted) — identical 3 failures, same assertion diffs. No relation to `app/teams.py`/team status/summary logic. No flake from `test_teams_headless.py` was observed on this run.

## Defects found
None. Testing pass is clean — proceeding to review.

---

## Spec coverage
All 7 acceptance criteria in `docs/spec.md` are implemented and covered by an automated test:
1. `finish(summary="X")` round-trip — implemented (`app/app.py` L6036), tested (case #1 above).
2. Non-`finished` statuses → `None` — implemented (same line, no extra gating needed per spec's own reasoning since `state["summary"]` defaults `None`), tested across all 6 non-`finished` statuses (case #2).
3. No-run → `None` — implemented (`run is not None else None` ternary), tested (case #3).
4. Finished + non-empty summary → visible `.team-sub` line — implemented (`finishedSummary` block, L4419-4420), tested (case #4).
5. Finished + empty-string summary → no line — implemented (falsy-string short-circuit), tested (case #5).
6. Non-finished + summary set anyway → no line (defensive) — implemented (`team.status === 'finished'` gate checked first), tested (case #6).
7. Existing `/status` tests unaffected — confirmed, full file green including the one updated exact-dict test.

No gaps found. Non-goals were checked directly against the diff: no `error`-status treatment was added (grep confirms `"summary"` is the only new key in the status-handler dict, and `renderTeamStatusStrip()`'s `error` branch is untouched), no new tool schema/`finish` behavior change, no success/failure text classification anywhere in the diff.

## Findings (most severe first)
None — no must-fix, should-fix, or nit findings.

Points specifically verified during review, for the record:
- **XSS/escaping**: `esc()` (`app/app.py` L3383-3385) uses the standard `textContent` → `innerHTML` DOM round-trip, which is a robust browser-native escape (not a hand-rolled regex substitution), and is the same function already used for every other model/user-supplied string in this file (`ENGINE_LABELS`, `team.status` fallback, `run_id`, etc.) — no new escaping mechanism introduced, no gap. The injection test (`<script>alert(1)</script>` → `&lt;script&gt;`, no raw tag) is a real DOM-executed check via the existing test harness's real-`<script>`-extraction approach used throughout `test_team_frontend.js`, not a string-matching approximation.
- **Placement/pattern fidelity**: the `finishedSummary` block is byte-for-byte the pattern the spec specified (mirrors `escalatedNote`, same `.team-sub` class, inserted at the same position in the concatenated return string, right before `escalationPanel`). No deviation.
- **Scope discipline**: diff touches exactly the two functions the spec named (status-handler dict, `teamRow()`) plus tests — no incidental refactors, no touched `finish` tool schema, no new helper functions, no CSS additions (`.team-sub` reused verbatim as claimed).
- **Test-file correctness**: the one pre-existing exact-full-dict test against `team` was correctly identified as the sole test needing an update (confirmed via grep — only one such assertion exists in the repo).

## Follow-ups (non-blocking)
- None raised by this cycle. The spec's own "Open questions" already flags `error` status as a possible independent follow-up if the user wants matching treatment later — not needed now.

## Overall verdict
**Approve.**
