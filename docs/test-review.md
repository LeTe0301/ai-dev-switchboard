# Test & Review: Backlog item 7 part 1 — board_read/board_write on the lead loop (backend)

## Scope
Covers `docs/spec.md` (committed `63c97b1`) against the uncommitted working
tree: `app/taiga_board.py` (new), `app/teams.py` (tool contract, inbox
generalization, `resolve_board_write()`, CLI subcommand, status audit),
`tests/test_teams_board.py` (new, 61 tests), `tests/test_teams_lead.py`
(updated for the rename/new tool count), `docs/implementation.md` (rewritten).
Confirmed via `git diff --stat`/`git status` that `app/app.py` and
`scripts/taiga_push_spec.py` are untouched, matching the spec's explicit
part-1 scope.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `board_read(ref=42)` returns same round, `action_count` +1, status stays `running` | Automated, `TeamStepBoardReadTests.test_board_read_by_ref_returns_result_same_round_no_block` | pass | `python3 -m unittest tests.test_teams_board -v` → OK; traced test body directly against `team_step()`, real assertions on `state["status"]`/`action_count`/`full_result_text` |
| 2 | `board_read()` no-args lists recent; Taiga error and "not configured" both fold into an ordinary outcome, never crash | Automated, `test_board_read_no_args_lists_recent`/`test_board_read_taiga_error_is_ordinary_outcome_not_crash`/`test_board_read_not_configured_is_ordinary_outcome` | pass | Same run |
| 3 | `board_write(verb="set_status", ref=42, value="In progress")` sets `blocked_board_write`, `inbox.json` has exact `{"kind": "board_write", "verb", "ref", "value", ...}` shape, zero Taiga PATCH calls at proposal time | Automated, `TeamStepBoardWriteTests.test_board_write_blocks_and_writes_inbox_no_taiga_write_call` | pass | Same run; asserted `patch_calls == []` and inbox contents directly |
| 4 | `board_write` snapshot failure (bad ref/unreachable) never queues a proposal, status stays `running`, ordinary outcome | Automated, `test_board_write_snapshot_failure_never_queues_proposal` | pass | Same run |
| 5 | `resolve_board_write(run_id, "approve")` makes exactly one Taiga PATCH with a **freshly-fetched** version (not the proposal-time snapshot), inbox moved to `inbox.resolved.json`, status → `running`, history entry recorded | Automated, `ResolveBoardWriteTests.test_approve_set_status_makes_exactly_one_patch_with_fresh_version` (also amend_description/append_comment variants) | pass | Test deliberately makes proposal-time snapshot carry no version and asserts the applied PATCH used `version=99` from a fresh `get_userstory()` stub, not any stale value |
| 6 | `resolve_board_write(run_id, "reject")` makes zero Taiga calls, inbox still resolves, status → `running` | Automated, `test_reject_makes_zero_taiga_calls_and_resumes` | pass | Asserted `calls == []` on the `resolve_session` stub |
| 7 | Taiga PATCH fails at approve time (`TaigaHTTPError`/`TaigaPushError`): inbox still resolves, status still → `running`, failure detail present in `outcome_summary` | Automated, `test_approve_taiga_failure_still_resolves_and_resumes`, `test_approve_unknown_status_name_is_recorded_as_taiga_rejected_not_stuck` | pass | Both branches (generic Taiga failure and "no such status name") verified never leave the run stuck |
| 8 | Two genuinely simultaneous `resolve_board_write()` calls: exactly one succeeds, loser gets `{"ok": False}`, no unhandled exception, no transcript entry for the loser | Automated, real-thread test, `test_two_concurrent_resolves_exactly_one_succeeds_no_taiga_call_for_loser` | pass | Ran 5x in a row for stability — consistently `[False, True]`, `len(call_log) == 1` (loser never reached the Taiga call), exactly one `board_write_resolved` history entry |
| 9 | `board_write` while a proposal (or `ask_user`) is already pending: rejected as business-rule outcome, does not consume malformed-retry budget | Automated, `test_board_write_while_already_blocked_is_business_rule_not_malformed`, `test_board_write_while_blocked_ask_user_also_rejected` | pass | Asserted `state["malformed_retries"] == 0` and outcome text |
| 10 | `GroundingStaticASTScanTests` passes unmodified; no board function added to `_GROUNDING_FUNCS`; no grounding-section function calls into `taiga_board` | Automated + independent reviewer AST script | pass | `python3 -m unittest tests.test_teams_grounding.GroundingStaticASTScanTests -v` → OK (3/3); independent `ast.walk()` script (not reusing the test's own code) over the 12 named grounding functions found zero `Attribute`/`Name` references to any board function or `taiga_board` |
| 11 | `team-board-resolve --action approve\|reject` works end-to-end via the CLI, zero `app.py` involvement | Automated, `CliTeamBoardResolveTests.test_approve_end_to_end_drives_to_finished`/`test_reject_end_to_end_drives_to_finished`/`test_unresolvable_run_id_exits_nonzero` | pass | Drove `teamsmod.main([...])` directly, confirmed `rc == 0` and final `status == "finished"` |
| 12 | `sweep_dead_teams()`/`_team_exit_code()` correctly treat `blocked_board_write` like `blocked_ask_user` (crash-detected only if session actually gone, never TTL-swept, exit code 0) | Automated, `BlockedBoardWriteStatusAuditTests` | pass | `test_sweep_marks_crashed_session_error_when_blocked_on_board_write` genuinely exercises the crash-detection branch (no live tmux session in test env) and confirms `action == "marked_error"` — proves no orphaning risk from the disclosed `app.py` gap |
| 13 | Full existing suite green, no regression | Automated | pass | `python3 -m unittest discover -s tests` → `Ran 855 tests ... OK` (794 baseline + 61 new, matches `docs/implementation.md`'s own count) |
| 14 | Node suite unaffected | Automated | pass | `test_singleton_toggle_frontend.js` 15/15, `test_upload_frontend.js` 8/8, `test_team_frontend.js` 59/59, `test_deploy_frontend.js` 9/9 → **91/91** |
| 15 | Every remaining `"blocked_ask_user"` literal in `app/teams.py` correctly audited (either left alone because it's ask_user-specific, or extended to also cover `blocked_board_write`) | Manual grep + read of each of the 12 hit sites | pass | See "Correctness review" below — `_force_ask_user`/`resolve_ask_user` sites correctly left alone; `sweep_dead_teams()`/`_team_exit_code()`/`latest_run_for_project()` docstring correctly extended |
| 16 | The two extra `taiga_board.py` functions (`resolve_session()`, `list_userstory_statuses()`) are glue/lookup necessities, not lead-facing verb-set expansion | Manual: diffed `_LEAD_TOOL_NAMES`/`_lead_tools()`/`_tool_prose()`, confirmed only `board_read`/`board_write` are lead-visible; confirmed both extra functions are called only from `team_step()`/`resolve_board_write()` internals, never exposed as a tool | pass | See "Non-goal compliance" below |
| 17 | Approval gate cannot be bypassed; a rejected/never-approved proposal can never reach Taiga | Manual: grepped every call site of `taiga_board.set_status`/`amend_description`/`append_comment` | pass | All three calls exist in exactly one place — inside `resolve_board_write()`'s `action == "approve"` branch, gated by an explicit `action in ("approve", "reject")` check with no other code path in the diff calling them |

## Regression check
- Full Python suite: `python3 -m unittest discover -s tests` → `Ran 855 tests in 136.599s` / `OK`. Ran the new file in isolation too: `python3 -m unittest tests.test_teams_board -v` → `Ran 61 tests` / `OK`, and `tests.test_teams_lead` → `Ran 114 tests` / `OK`.
- Full Node suite (4 files, all run for real): 91/91, all green — none of these files were touched by this diff, confirming zero collateral breakage.
- Re-ran the concurrency race test 5x back-to-back for stability (no flake observed).
- Confirmed via `git status`/`git diff --stat` that only the files claimed in the task description changed; `app/app.py` and `scripts/taiga_push_spec.py` are untouched.

No test failures — proceeding to the review pass.

---

## Spec coverage

| Acceptance criterion (`docs/spec.md`) | Implemented | Tested | Notes |
|---|---|---|---|
| `board_read(ref=42)` returns same round, `action_count`+1, status unchanged | Yes | Yes (#1) | |
| `board_write(...)` blocks, writes exact inbox shape, zero Taiga writes | Yes | Yes (#3) | |
| `resolve_board_write(..., "approve")`: one PATCH, fresh version, inbox moved, status resumes, history recorded | Yes | Yes (#5) | Fresh-version discipline specifically verified (version deliberately differs from the proposal-time snapshot) |
| `resolve_board_write(..., "reject")`: zero Taiga calls, inbox resolves, status resumes | Yes | Yes (#6) | |
| Taiga PATCH failure at approve time never leaves the run stuck | Yes | Yes (#7) | |
| Two genuinely simultaneous resolves: exactly one wins, no crash, no spurious transcript entry | Yes | Yes (#8) | Reused the same real-thread technique `TeamResolveEndpointTests` already validated for `ask_user` — confirmed this file's test genuinely exercises the equivalent race windows (see below) |
| `board_write` while already pending: business-rule rejection, not malformed | Yes | Yes (#9) | Both `blocked_board_write` and `blocked_ask_user` pending cases covered |
| `GroundingStaticASTScanTests` passes unmodified | Yes (no change) | Yes (#10) | Independently re-verified with a separate AST script, not just re-running the existing test |
| `team-board-resolve` CLI works end-to-end, zero `app.py` involvement | Yes | Yes (#11) | |
| Full existing suite green | Yes | Yes (#13, #14) | |

All nine acceptance criteria in `docs/spec.md` are implemented and independently verified by tests I ran myself this session.

## Review pass

### Race-safety shape vs. `resolve_ask_user()` (the specific ask from the dispatch prompt)
`resolve_board_write()` reuses `resolve_ask_user()`'s exact shape with one
necessary structural difference: because the proposal's `verb`/`ref`/`value`
live *inside* `inbox.json` (unlike `ask_user`'s `answer`, which arrives as a
caller argument), `resolve_board_write()` must read `inbox.json`'s content
*before* calling `os.replace()`, to know what to apply. I traced this
carefully against both of `resolve_ask_user()`'s two previously-fixed races:

- **The crash/`FileNotFoundError` race** (loser's own `os.replace()` call
  collides with the winner's): still closed. `os.replace()` is called
  unconditionally, wrapped in `try/except OSError`, exactly as
  `resolve_ask_user()` does.
- **The `exists()`-check race** (a separate check-then-act window): not
  reintroduced. There is no `os.path.exists()` guard anywhere in
  `resolve_board_write()` — `os.replace()` is the sole atomic arbiter, same
  as the fixed `resolve_ask_user()`.
- **The "act before win/lose is known" race** (history/Taiga call before the
  replace succeeds): not reintroduced, and correctly extended to a stricter
  new requirement this function has that `ask_user` doesn't. I confirmed by
  reading line order: `verb, ref, value = inbox.get(...)` and every use of
  those values (the Taiga calls, `_append_history()`) appear strictly
  *after* the `try: os.replace(...) except OSError: return ...` block. The
  pre-replace `open()`/`json.load()` read is wrapped in its own
  `except (OSError, ValueError): inbox = {}` and its result is never acted
  on by a loser, since a loser always returns from the `except OSError`
  branch before reaching the `verb, ref, value = ...` line. A loser can read
  stale-but-harmless content and still never call Taiga or write history.

This is correctly reasoned, not just superficially "mirrored" — confirmed by
tracing the actual line order, not by trusting the docstring's own claim.

### Non-goal compliance: two extra client functions
- `resolve_session()` — pure three-step wiring (`load_config` →
  `authenticate` → `lookup_project`), never lead-exposed. Confirmed
  `_LEAD_TOOL_NAMES`/`_lead_tools()`/`_tool_prose()` only add `board_read`/
  `board_write` — no new tool name reaches the lead's own contract.
- `list_userstory_statuses()` — a read-only lookup used exclusively inside
  `resolve_board_write()`'s `approve` branch to translate `set_status`'s
  human-readable `value` to the numeric id `taiga_board.set_status()`
  requires, exactly as the spec's own "status_id for set_status" section
  demands ("the apply step uses the id"). Not callable by the lead.

Both are genuinely necessary plumbing the spec's own text requires but its
literal 8-function code block didn't happen to enumerate — not scope creep,
and the "exactly three write verbs, no passthrough" Non-goal is intact
(verified directly: only three functions ever appear in a PATCH call site,
all three gated behind the single `action == "approve"` branch).

### Security: approval gate cannot be bypassed
Grepped every call site of `taiga_board.set_status`/`amend_description`/
`append_comment` in `app/teams.py` — all three appear exactly once each,
all three inside `resolve_board_write()`'s `else:` (approve) branch, gated
by `action in ("approve", "reject")` checked before anything is touched on
disk. `board_write` itself (`team_step()`) never calls a write verb, only
`get_userstory()` (a GET) for the display-only snapshot. There is currently
no web route at all (part 2), so the only way to reach `resolve_board_write`
is the CLI or a direct Python call — matches the spec's explicit "approval
is the only path" and "CLI-testable, no web UI yet" scope. `ref`/`verb` are
JSON-type-checked (`int`/enum-checked string) before ever reaching a URL
f-string, and `slug` is the only value URL-quoted via
`urllib.parse.quote()` — no injection surface identified.

### Disclosed gap: does `blocked_board_write` risk orphaning under `app/app.py`'s blindness to it?
Traced this directly, the single highest-value check per the dispatch
prompt:
- `sweep_dead_teams()` (in `app/teams.py`, not `app/app.py`) **was** updated
  to include `blocked_board_write` in both its crash-detection tuple and its
  never-TTL-swept tuple — independently confirmed by reading the diff and by
  `BlockedBoardWriteStatusAuditTests.test_sweep_marks_crashed_session_error_when_blocked_on_board_write`,
  which genuinely exercises the crash-detection branch (no live tmux
  session) and gets the expected `marked_error` outcome, same as
  `blocked_ask_user` would.
- The only things `app/app.py` gets wrong for this status are purely
  cosmetic/informational: the status pill falls through `.get(status,
  "idle")` to `"idle"` (line ~3990-3993), and `GET .../team/inbox` doesn't
  report it as pending (checks `status == "blocked_ask_user"` only,
  line 4177) — both exactly as disclosed in `docs/implementation.md`'s
  "Known limitations."
- **One additional gap, not disclosed in `docs/implementation.md`**: the
  pre-existing `/team/stop` route (`app/app.py` line 4364,
  `run["status"] not in ("running", "blocked_ask_user")`) also silently
  no-ops for a `blocked_board_write` run — the web UI's Stop button won't
  actually stop it. This is **not a new regression**: this exact guard
  already had the identical blind spot for `escalated_max_rounds` before
  this diff, so it's a pre-existing pattern this diff's new status simply
  also falls into, not something this diff broke. The CLI's `team-stop`
  (`stop_team()`) is unconditional and works regardless of status, so the
  run is not actually unstoppable — just not stoppable from the web UI
  until part 2. Because it causes no data loss, no orphaning, and is
  consistent with the spec's own "app.py untouched" scope, this is a
  **should-fix** documentation gap (add it to `docs/implementation.md`'s
  "Known limitations" alongside the other two), not a blocker.

**Conclusion: no orphaning/stuck-run risk.** The one gap found is
UI-cosmetic and has a working CLI workaround.

### Correctness: `"blocked_ask_user"` literal audit
Independently grepped all 12 hits of `"blocked_ask_user"` in `app/teams.py`
(not just re-reading the two functions named in the dispatch prompt):
- `_force_ask_user()`'s default param and internal `if status ==
  "blocked_ask_user":` branch, and its one `blocked_ask_user`-specific call
  site — correctly left alone; this function is ask_user-specific by
  design (its `status` kwarg is also used for `escalated_max_rounds`).
- `team_step()`'s own `ask_user` branch — correctly left alone (unrelated
  to board_write).
- `resolve_ask_user()`'s own status check and docstring references —
  correctly left alone (this function only ever resolves `ask_user`;
  `resolve_board_write()` is its own new, parallel function with its own
  `blocked_board_write` check).
- `latest_run_for_project()`'s docstring, `sweep_dead_teams()`'s docstring
  and both status tuples, `_team_exit_code()` — all correctly extended to
  include `blocked_board_write`.
- One comment (`TEAM_SESSION_STALE_TTL_SECONDS`'s own comment, line ~152)
  mentions only `blocked_ask_user` but explicitly defers to
  `sweep_dead_teams()`'s own (correctly updated) docstring for the current
  behavior — a **nit**, not a defect (no functional impact, purely a stale
  cross-reference in a comment).

The audit was complete. No status-shape-specific site was missed.

### Simplicity
No unnecessary abstraction found. `taiga_board.py`'s single-layer error
translation (vs. `taiga_push_spec.py`'s three-layer pattern) is a reasonable,
disclosed simplification given this module has exactly one caller category.
The `board_write` "already pending" defensive check duplicates
`_append_history()`/`_persist()` call shape inline rather than routing
through the shared `agent_not_on_team`/`premature_finish` block earlier in
`team_step()` — mildly duplicative but the shared block is keyed off
`_validate_lead_action()`'s return value and doesn't have access to
`state["status"]`, so factoring it in would cost more than it saves for one
call site. Not worth a follow-up.

## Findings (ranked)

1. **Should-fix** — `docs/implementation.md`'s "Known limitations" section
   should also disclose that `POST .../team/stop` (`app/app.py` line 4364)
   silently no-ops for a `blocked_board_write` run (web UI Stop button does
   nothing; `team-stop` CLI still works). No code change needed, no data
   loss, not a regression — just an omission in the disclosed-gaps writeup
   that a future part-2 author should know about alongside the two gaps
   already listed there.
2. **Nit** — `app/teams.py`'s `TEAM_SESSION_STALE_TTL_SECONDS` comment
   (line ~152) still says "Never applies to `status=="blocked_ask_user"`"
   without mentioning `blocked_board_write`, even though the code it
   defers to (`sweep_dead_teams()`) was correctly updated. Optional
   one-line touch-up.
3. **Nit** — `tests/test_teams_board.py`'s `TaigaRequestSeamTests` has no
   dedicated test for the malformed-`base_url`/`ValueError`-at-`Request()`-
   construction path that `scripts/taiga_push_spec.py`'s own test suite
   covers (referenced in this module's own docstring as "Defect 1's fix,
   carried forward"). The code correctly carries the fix forward (verified
   by reading `_taiga_request()`'s `except (urllib.error.URLError, OSError,
   ValueError)` clause) — this is a coverage-parity nit, not a functional
   gap.

No must-fix findings.

## Overall verdict: **Approve** (with the one should-fix noted above as a
non-blocking follow-up)

All nine acceptance criteria are implemented and independently verified.
The race-safety shape correctly reuses `resolve_ask_user()`'s hardening
(traced line-by-line, not just trusted). The approval gate cannot be
bypassed. The two extra client functions are genuine plumbing, not scope
creep. `sweep_dead_teams()` correctly prevents any orphaning risk from the
disclosed `app.py` blindness to the new status. Full regression suite
(855 Python + 91 Node) is green. The one should-fix (undisclosed `/team/stop`
no-op) and two nits do not block this cycle — they're worth a quick
follow-up note in `docs/implementation.md` but not worth bouncing back to
the developer for.

---

# Test & Review: Backlog item 7 part 2 — web UI for approving/rejecting board_write proposals

## Scope
Covers `docs/spec.md`'s newest section (item 7 part 2) and `docs/design.md`'s
newest section against the uncommitted working tree on
`backlog/lead-kanban-write-web-7b`: `app/app.py` (313 lines, +302/-11 —
`/status`'s `escalation_kind`, `_handle_team_inbox()`'s new
`blocked_board_write` branch + `_handle_team_inbox_board_write()`, new
`POST .../team/board-resolve` route, `/team/stop`'s one-tuple fix, and the
frontend JS/CSS), `tests/test_team_routes.py` (+342), `tests/test_team_frontend.js`
(+270). Confirmed via `git diff --stat` that `app/teams.py`/`app/taiga_board.py`
are untouched, matching the spec's explicit "call site, not a new function"
scope.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `blocked_board_write` → `/status` reports `status="blocked"`, `waiting_on_you=true`, `escalation_kind="board_write"` | Automated, `test_escalation_kind_field`, `test_waiting_on_you_true_only_for_blocked_ask_user_never_for_escalated_max_rounds` | pass | `python3 -m unittest tests.test_team_routes -v` → OK |
| 2 | `blocked_ask_user` → `escalation_kind="ask_user"` (regression) | Automated, same `test_escalation_kind_field` cases dict | pass | Same run |
| 3 | `running`/`finished`/`error`/`stopped`/no-run → `escalation_kind` null/absent | Automated, same test + `test_status_idle_when_no_run_ever_started` | pass | Same run |
| 4 | `GET .../team/inbox` on `blocked_board_write` returns exact persisted shape + enriched `subject` | Automated, `test_board_write_genuinely_blocked_returns_exact_persisted_proposal_shape_plus_subject` | pass | Same run |
| 5 | Same but Taiga unreachable → still `pending:true`, `subject` omitted, not a 500 | Automated, `test_board_write_taiga_unreachable_degrades_gracefully_no_subject` | pass | Same run |
| 6 | `inbox.json` missing/malformed despite `blocked_board_write` → safe fallback, never a 500 | Automated, `test_board_write_missing_inbox_json_still_pending_true_with_fallback`, `test_board_write_malformed_inbox_json_still_pending_true_with_fallback` | pass | Same run |
| 7 | `blocked_ask_user` branch of `/team/inbox` byte-for-byte unchanged (regression) | Automated, `test_ask_user_branch_response_shape_unchanged_regression` | pass | Same run |
| 8 | `POST .../team/board-resolve` approve/reject: 200, resolves, resumes | Automated, `TeamBoardResolveEndpointTests.test_approve_resolves_and_starts_background_thread`/`test_reject_resolves_and_starts_background_thread_no_taiga_call` | **pass, but the "starts the background driving thread" half of this criterion is not actually exercised** | See "Review pass" §1 below — confirmed via revert-and-fail that these two tests still pass with the thread-dispatch code entirely removed |
| 9 | Wrong status / invalid action / missing run / cross-project run_id / path-traversal run_id → 400, no mutation, no thread | Automated, `test_not_blocked_400_no_resolve_call_no_thread`, `test_invalid_action_400_before_resolve_called`, `test_missing_action_400`, `test_no_run_at_all_400`, `test_explicit_run_id_for_a_different_project_400`, `test_path_traversal_run_id_400_planted_file_never_opened_no_thread_started` | pass | Same run |
| 10 | TOTP 428 (no code) / 403 (wrong code), same shared gate | Automated, `test_totp_428_with_no_code`, `test_totp_403_with_wrong_code` | pass | Same run |
| 11 | Two concurrent approves: exactly one 200, one 400 | Automated, real-thread, `test_two_concurrent_resolves_exactly_one_succeeds` | pass | Same run |
| 12 | `POST .../team/stop` now actually stops a `blocked_board_write` run (was a silent no-op) | Automated, `test_stop_on_blocked_board_write_now_actually_stops` | pass — **and independently confirmed load-bearing** | Revert-and-fail: reverted the one-tuple fix (`"running", "blocked_ask_user", "blocked_board_write"` → `"running", "blocked_ask_user"`), reran the test, got a genuine `AssertionError: 'session_removed' not found in {..., 'message': 'no team currently running...'}`; restored, reran, green again |
| 13 | `renderEscalationPanel()` on `escalation_kind='board_write'` renders verb-specific content (all 3 verbs) + Approve/Reject, no free-text field; `ask_user` path unchanged | Automated, `node tests/test_team_frontend.js` — 5 new panel tests (set_status, `#ref` fallback, amend_description, append_comment, "already resolved" race copy) | pass | `node tests/test_team_frontend.js` → 74/74 |
| 14 | `doTeamBoardResolve()` dispatches the right action, success/error inline messaging, 428-then-retry resends the same action | Automated, 4 new tests | pass | Same run |
| 15 | `teamFeedEventKindClass()`/`teamFeedEventBody()` new `board-write-proposal`/`board-write-resolved` classes, checked **before** the generic `tool_result`+`meta.resolved`→`'resolved'` branch; ask_user regression unaffected | Automated, 5 new tests | pass — **and independently confirmed load-bearing (the specific ordering)** | Revert-and-fail: moved the `meta.approved !== undefined` check to *after* the `meta.resolved` check, reran — 3 tests failed exactly as predicted (`board-write-resolved` never matched, fell through to generic `'resolved'`/`'Answer: ...'` instead); restored the correct order, reran — 74/74 green again |
| 16 | Full existing suite green, no regression | Automated | pass | `python3 -m unittest discover -s tests -v` → `Ran 874 tests in 137.308s` / `OK` (matches the 813 pre-existing baseline + 61 from part 1 already counted there, no reduction) |

## Regression check
- Full Python suite: `python3 -m unittest discover -s tests -v` → `Ran 874 tests` / `OK`, 0 failures/errors (`grep -c "^FAIL\|^ERROR"` → 0).
- Full Node suite: `node tests/test_team_frontend.js` → `ALL PASS (74/74)`.
- `tests.test_team_routes` in isolation: `Ran 95 tests` / `OK`.
- Two revert-and-fail checks per this project's established discipline (BACKLOG item 9 history) — both confirmed genuinely load-bearing, not vacuous (see test cases #12, #15 above); `git diff --stat app/app.py` confirmed byte-identical to the pre-check state after each restore (+302/-11 both times).
- A third, self-initiated revert-and-fail check (not one of the two the dispatch prompt named) on the new route's thread-dispatch block found a genuine gap — see "Review pass" §1.

No test run itself failed — proceeding to the review pass. (The one substantive finding below surfaced during independent review verification, not during the testing pass itself, per this role's own testing-pass-vs-review-pass distinction.)

---

## Spec coverage

| Acceptance criterion (`docs/spec.md`) | Implemented | Tested | Notes |
|---|---|---|---|
| `/status`: `blocked_board_write` → blocked/waiting_on_you/`escalation_kind="board_write"` | Yes | Yes (#1) | |
| `/status`: `blocked_ask_user` → `escalation_kind="ask_user"` (regression) | Yes | Yes (#2) | |
| `/status`: other statuses/no-run → `escalation_kind` null | Yes | Yes (#3) | |
| `GET .../team/inbox`: board_write branch, exact shape + subject | Yes | Yes (#4) | |
| `GET .../team/inbox`: Taiga-unreachable graceful degradation | Yes | Yes (#5) | |
| `GET .../team/inbox`: ask_user branch unchanged | Yes | Yes (#7) | |
| `POST .../team/board-resolve`: approve returns 200, calls `resolve_board_write` once, **starts the background driving thread** | Yes (code read directly, matches `/team/resolve`'s dispatch exactly) | **No** — see must-fix #1 | The route code is correct; the two tests written for it do not exercise this half of the criterion at all (proven by revert-and-fail) |
| Same for reject | Yes | **No** — same gap | |
| Wrong status → 400, zero `resolve_board_write()` calls, no thread | Yes | Yes (#9) | |
| Invalid action → 400 before calling `resolve_board_write()` | Yes | Yes (#9) | |
| TOTP 428/403, same shared gate | Yes | Yes (#10) | |
| `POST .../team/stop` fix | Yes | Yes (#12, revert-and-fail confirmed) | |
| `renderEscalationPanel()` board_write branch, all 3 verbs, no free-text field; ask_user unchanged | Yes | Yes (#13) | |
| `doTeamBoardResolve()` approve/reject dispatch, TOTP-retry resending the same action | Yes | Yes (#14) | |
| `teamFeedEventKindClass()` new classes, correct precedence order | Yes | Yes (#15, revert-and-fail confirmed) | |
| Full existing suite green | Yes | Yes (#16) | |

17 of 18 acceptance criteria are implemented and independently verified by
tests I ran myself this session. One (the "starts the background driving
thread" half of the approve/reject criterion) is implemented correctly but
not provably covered by any test — see must-fix #1.

## Review pass

### 1. Must-fix: the new route's "starts the background driving thread" tests are vacuous
`docs/spec.md`'s acceptance criteria explicitly require: "...calls
`teams.resolve_board_write(run_id, "approve")` exactly once, **and starts the
background driving thread** (mirroring `/team/resolve`'s own dispatch —
verified via the same thread-registration check `test_team_routes.py`'s
existing `/team/resolve` tests use)."

I traced `resolve_board_write()` and confirmed it performs its own
`os.replace()`/history-append synchronously, *inside* the route handler,
before the route ever reaches the `cancel_event = threading.Event(); t =
threading.Thread(...); t.start()` block. That means
`test_approve_resolves_and_starts_background_thread`'s and
`test_reject_resolves_and_starts_background_thread_no_taiga_call`'s own
assertions (inbox moved to `.resolved.json`, exactly one
`board_write_resolved` history entry) are all satisfied by
`resolve_board_write()` alone — none of them require the subsequent thread
dispatch to have happened at all.

I verified this is not a hypothetical concern: I temporarily commented out
the entire `cancel_event`/`Thread`/`_team_threads_set`/`t.start()` block in
the new route (leaving only the `{"ok": True, "run_id": run_id}` response),
reran `tests.test_team_routes.TeamBoardResolveEndpointTests`, and **all 12
tests, including both "starts_background_thread"-named tests, still passed**.
I restored the code immediately afterward and reran to confirm the restore
was clean (`git diff --stat app/app.py` byte-identical to before the probe).

The shipped route code itself is correct — I read it directly and it
mirrors `/team/resolve`'s dispatch pattern exactly (same
`_team_threads_get()` defensive check, same `threading.Thread(target=
_run_team_in_background, ...)`, same `_team_threads_set()`/`t.start()`
sequence). This is a test-coverage gap, not a functional bug. I also
checked whether `TeamResolveEndpointTests`' own precedent test
(`test_genuinely_blocked_valid_answer_resolves_and_returns_immediately`)
provides a real template to copy: it adds one thing these two new tests
don't — an `elapsed < 3.0` fast-return timing assertion, intended as
indirect proof the POST didn't block for the full driving loop. No
existing test in this file (for `/team/start`, `/team/resolve`, or now
`/team/board-resolve`) asserts `_team_threads_get()` is *populated* on a
success path (the three existing `_team_threads_get()` call sites all check
the *rejection* paths, confirmed by grep) — so the spec's own premise that
this "verified via the same thread-registration check" precedent already
exists is not quite accurate either; the closest existing precedent is the
weaker timing proxy, and even that is missing from the two new tests here.

**Recommended fix**: add a deterministic check to both success-path tests —
either (a) monkeypatch `appmod.threading.Thread` (or `appmod
._run_team_in_background`) for the duration of the test to record it was
invoked with `(name, run_id, cancel_event)`, which is unambiguous and
race-free, or (b) at minimum mirror `/team/resolve`'s own `elapsed < 3.0`
timing proxy for consistency with existing precedent. (a) is preferable
since a fast stub lead could complete in well under 3 seconds whether or
not it was ever actually threaded, making (b) a weak signal on its own.

### 2. Should-fix (non-blocking): Approve/Reject button contrast — design doc's own claim is inaccurate, and the actual implemented color fails WCAG
Recomputed WCAG relative-luminance contrast from the literal hex values
rather than trusting `docs/design.md`'s "Accessibility & platform notes"
section, per this role's own mandate:
- `docs/design.md` claims: "Action button text: `#ffffff` on `#4da6ff`
  (action button background, reused from existing buttons) = 9.15:1
  (passes WCAG AA for large button text)."
- This is inaccurate on two counts. First, `#4da6ff` is not the color the
  implementation (or any existing button on this page) actually uses —
  `docs/implementation.md`'s own "Key decisions" section discloses that the
  developer deliberately reused the existing `.team-btn` class verbatim
  (background `#34c759`, per `app/app.py` line ~1701, the same class
  Start/Stop/Deploy/Submit-answer already share), not a new `#4da6ff`
  variant. Second, recomputing contrast for the color that *is* actually
  used — white `#ffffff` text on `#34c759` — gives **≈2.22:1**
  (relative luminances 1.0 and 0.4232; `(1.0+0.05)/(0.4232+0.05) ≈ 2.22`),
  which fails WCAG AA for text (4.5:1) and fails even the 3:1 threshold for
  large/graphical elements.
- This is **not a new regression** — `.team-btn`'s white-on-`#34c759`
  styling is inherited unchanged from 6d part 2a (Start/Stop/Deploy) and 6f
  part 2 (Submit answer), both already reviewed and approved in earlier
  cycles this session, and the developer's choice to reuse it verbatim here
  (rather than inventing a new button color) is the right scope-discipline
  call, explicitly disclosed in "Known limitations." But this diff does
  extend the same low-contrast text to two more interactive controls
  (Approve/Reject), and the design doc's own accessibility section makes a
  factually wrong "passes AA" claim for this cycle specifically.
- I independently re-verified the design doc's *other* contrast claims for
  this cycle (status-strip `#ffb648`-on-`#1c1c1c` = 9.77:1; proposal text
  `#ffffff`-on-`#1c1c1c` ≈ 21:1; message-slot success `#34c759`-on-`#1c1c1c`
  = 7.68:1; error `#ff6b6b`-on-`#1c1c1c` = 6.14:1) and all of those compute
  correctly and do pass — only the button-text-on-button-background claim
  is wrong, both in which color it names and in whether the actual color
  passes.
- **Recommendation**: not a blocker for this narrow feature (label text,
  not color, already distinguishes Approve from Reject, and fixing it
  ad hoc for just these two buttons would create a new visual
  inconsistency with Start/Stop/Deploy/Submit-answer). File a follow-up
  backlog item to fix `.team-btn`'s contrast project-wide in one pass, and
  correct `docs/design.md`'s accessibility section to reflect the actual
  shipped color rather than the unused `#4da6ff`.

### 3. Correctness: the two disclosed deviations, independently verified
- **`teamBoardResolveAction[name]` map instead of `pendingToggle`**:
  confirmed sound by reading `toggle()`/`handleActionResult()` directly.
  `toggle(kind, name, on, checkboxEl)`'s fourth parameter is specifically
  `checkboxEl` (used only to revert an on/off switch's checked state on
  cancel, line ~3200), not a generic context bag — `pendingToggle` is set
  to `{kind, name, on, checkboxEl}` only inside `handleActionResult()`'s
  `r.status === 428` branch (line 2973), so it is `null`/stale on the very
  first optimistic POST. The design doc's own suggested `actionBody()`
  snippet (`const ctx = pendingToggle || {}; body.action = ctx.action;`)
  would in fact never work as written — `pendingToggle.action` is never a
  top-level property under any code path, only `pendingToggle.checkboxEl`
  is populated from the fourth `toggle()` argument, and even that only
  after a 428. The developer's chosen alternative is not just "a reasonable
  choice between two working options" as the design doc frames it — it is
  the *only* one of the two that actually functions, and the 428-then-retry
  test (test case #14) confirms the retry resends the correct action.
- **`args_summary` reused verbatim instead of the design doc's illustrative
  feed-line text**: confirmed by reading `app/teams.py` line 3008/3052
  directly — the proposal's transcript entry is literally
  `("tool_use", args_summary, {"verb": verb, "ref": ref})` where
  `args_summary = f"board_write({verb}, ref={ref})"`. The lead's `note` is
  never placed into this transcript entry's `text` field anywhere in
  `team_step()`'s `board_write` branch. The developer's implementation
  matches `docs/spec.md` §6's own literal formula
  (`'board_write (' + meta.verb + '): ref #' + meta.ref + ' — ' + esc(e.text)`)
  exactly — this is compliance with the spec over a design-doc mockup that
  turned out not to correspond to any backend-persisted string, correctly
  disclosed as such, not a defect.

### 4. TOTP gating and `resolve_board_write()` call correctness (specifically requested checks)
- The new route sits inside the same `do_POST()` dispatch chain, after the
  single shared TOTP gate at the top of the method (`session_totp_ok`/428/403,
  lines ~4454-4470) that every other mutating route (`/team/start`,
  `/team/stop`, `/team/resolve`) already passes through — there is no
  separate/duplicated gating logic for the new route, confirmed by reading
  `do_POST()` top-to-bottom.
- Validation order/shape is byte-for-byte structurally identical to
  `/team/resolve`'s own route (unknown project 404 → empty-`run_id`
  fallback via `latest_run_for_project()` → `_RUN_ID_RE` validation →
  `_load_state()` → ownership check → status check → the route-specific
  body validation → the "already running" defensive check → thread
  dispatch), confirmed by reading both route bodies side by side.
- `teams.resolve_board_write(run_id, action)` is called with exactly the
  signature `resolve_board_write(run_id: str, action: str)` defined in
  `app/teams.py` line 4085 — confirmed by grep and by direct comparison of
  the call site's two positional arguments against the function signature.

### 5. Simplicity
No unnecessary abstraction. `_handle_team_inbox_board_write()` as a sibling
method (rather than inlining into `_handle_team_inbox()`) is a reasonable,
disclosed choice that keeps the unchanged `ask_user` branch legible and
literally untouched (confirmed by the passing byte-for-byte regression
test). `truncateText()` is a small, genuinely-reused extraction, not a new
abstraction layer. No new libraries, no new CSS components beyond what
`docs/design.md` itself calls for.

### 6. Security
- Every new interpolated value in `renderBoardWriteEscalationPanel()`
  (`subject`, `cur`, `cached.value`, `curDesc`, `cached.note` via
  `truncateText()`, `cached.ref` in the `#ref` fallback) is passed through
  `esc()` (confirmed `esc()`'s implementation uses
  `textContent`/`innerHTML`, a real HTML-escaping mechanism, not a
  hand-rolled regex) — no new XSS surface.
- `name` is interpolated unescaped into the `onclick="doTeamBoardResolve('...',...)"` attribute
  strings for the two new buttons — matches the exact pre-existing
  convention `doTeamResolve()`/`doTeamStart()`/`doTeamStop()` already use
  identically elsewhere on this page (project names are constrained at
  creation time), not a new or widened surface introduced by this diff.
- The subject-enrichment Taiga read (`_handle_team_inbox_board_write()`) is
  read-only, wrapped in `except teams.taiga_board.TaigaPushError: pass`,
  and never touches `version` or performs a write — matches the spec's own
  "read, not proposal-affecting" requirement.
- The approval gate is unchanged from part 1 (already reviewed): the new
  route only ever calls `resolve_board_write()`, never a `taiga_board`
  write function directly.

## Findings (ranked)

1. **Must-fix** — `tests/test_team_routes.py`'s
   `test_approve_resolves_and_starts_background_thread` and
   `test_reject_resolves_and_starts_background_thread_no_taiga_call` do not
   verify the "starts the background driving thread" half of their own
   acceptance criterion. Confirmed via revert-and-fail: both tests (and the
   whole `TeamBoardResolveEndpointTests` class) still pass with the route's
   entire `cancel_event`/`Thread`/`_team_threads_set()`/`t.start()` block
   removed. The shipped route code is correct (verified by direct reading
   against `/team/resolve`'s identical pattern); only the tests need
   strengthening. See "Review pass" §1 for the recommended fix.
2. **Should-fix** — Approve/Reject buttons (`.team-btn`, white text on
   `#34c759`) compute to ≈2.22:1 contrast, failing WCAG AA; `docs/design.md`'s
   own accessibility section makes an inaccurate "9.15:1, passes AA" claim
   against a `#4da6ff` background that isn't actually used anywhere on this
   page. Inherited from already-approved earlier cycles (6d part 2a, 6f
   part 2), not a new regression, and correctly reusing existing convention
   is the right scope-discipline call for this diff — but this does extend
   the underlying issue to two more controls. Recommend a follow-up backlog
   item to fix `.team-btn`'s contrast project-wide, plus a correction to
   `docs/design.md`'s accessibility section. See "Review pass" §2.
3. **Nit** — none beyond the above; the rest of the diff (validation order,
   TOTP gating, escaping, the two disclosed deviations, the ordering-critical
   event-feed fix, the `/team/stop` fix) all independently checked out under
   direct code reading and/or revert-and-fail.

## Overall verdict: **Changes requested**

Every acceptance criterion is either fully implemented-and-tested (17/18) or
implemented-correctly-but-under-tested (1/18, the thread-dispatch half of
the board-resolve success criterion). The one must-fix is a test-strengthening
task, not a functional bug — the shipped `app/app.py` code correctly starts
the background driving thread on both approve and reject, mirroring
`/team/resolve` exactly, confirmed by direct reading. Once
`TeamBoardResolveEndpointTests`'s two success-path tests are strengthened to
actually prove the thread dispatch happened (not just that
`resolve_board_write()`'s own synchronous side effects occurred), this cycle
should re-enter the reviewer's testing pass to confirm the new assertions are
themselves genuine (not vacuous), then proceed to approval — the should-fix
accessibility note does not need to block that.

---

## Re-review: Backlog item 7 part 2 — must-fix #1 fix-and-reapprove round

### Scope
Covers `tests/test_team_routes.py`'s diff only (the developer's disclosed
"Post-review fix" in `docs/implementation.md`, scoped to that file alone —
confirmed no other file changed for this specific fix beyond the two docs
files the developer itself updates as part of any cycle). Re-verified from
scratch, not by re-trusting the developer's own revert-and-fail claim.

### What I checked
1. **Read the actual diff.** `tests/test_team_routes.py` gained a
   `_record_team_threads()` fixture on `TeamBoardResolveEndpointTests` that
   rebinds `appmod.threading` (i.e. `app.py`'s own module-level name bound
   by its `import threading` at line 49 — not the shared `threading` module
   object) to a thin proxy object whose `Thread` attribute is a subclass
   recording `(target, args)` on every construction, then delegating to the
   real `threading.Thread.__init__`/`.start()`. Both
   `test_approve_resolves_and_starts_background_thread` and
   `test_reject_resolves_and_starts_background_thread_no_taiga_call` now
   install this fixture and assert `len(started) == 1`,
   `target is appmod._run_team_in_background`, and `args == (name, run_id,
   cancel_event-instance)`. Confirmed the rebind is scoped correctly: since
   `app/app.py`'s own `threading` name is a module-level binding distinct
   from any other module's own `import threading` reference, this does not
   touch `ThreadingHTTPServer`'s per-connection thread creation (which lives
   in `http.server`'s own module namespace) — matches the fixture's own
   docstring explanation of why an earlier attempt (mutating the shared
   `threading` module's `Thread` attribute globally) over-recorded 3 threads
   instead of 1.
2. **Ran the full suite for real.**
   `python3 -m unittest discover -s tests -v` → `Ran 874 tests in 137.360s`
   / `OK`. `node tests/test_team_frontend.js` → `ALL PASS (74/74)`. Also ran
   `TeamBoardResolveEndpointTests` in isolation → `Ran 12 tests` / `OK`,
   including both strengthened tests passing (`started` recorded exactly one
   thread for each).
3. **My own independent revert-and-fail**, not a re-run of the developer's:
   temporarily deleted the entire `cancel_event =
   threading.Event()`/`t = threading.Thread(...)`/`_team_threads_set(...)`/
   `t.start()` block from the `POST .../team/board-resolve` route in
   `app/app.py` (lines ~4711-4717), leaving only the
   `{"ok": True, "run_id": run_id}` response. Reran
   `TeamBoardResolveEndpointTests` — both strengthened tests now fail
   genuinely:
   `AssertionError: 0 != 1` on `self.assertEqual(len(started), 1)` for both
   `test_approve_resolves_and_starts_background_thread` and
   `test_reject_resolves_and_starts_background_thread_no_taiga_call` — while
   the other 10 tests in the class stayed green (they don't touch thread
   dispatch at all). This proves the strengthened assertions are genuinely
   load-bearing against exactly the code path the must-fix identified, not
   vacuously passing for some other reason.
4. **Restored** the deleted block and reran: all 12 tests pass again, and
   `git diff --stat app/app.py` reports `313 ++...--- / 302 insertions(+),
   11 deletions(-)`, byte-identical to the pre-probe state (confirmed
   against the state captured before my probe) — no residual change from my
   own revert-and-fail check.
5. **Confirmed the should-fix was correctly left untouched.** `.team-btn`'s
   CSS (`app/app.py` line 1701: `background: #34c759; color: #fff`) is
   unchanged — no contrast fix was applied, matching the explicit "do NOT
   touch the should-fix" instruction for this round. `docs/design.md` was
   not further modified for this fix (the developer's own disclosure that
   this fix is scoped to `tests/test_team_routes.py` only checks out).
6. **Confirmed no other regression.** Full Python (874/874) and Node
   (74/74) suites both green, matching the pre-fix baseline counts exactly
   (874 was already the count before this fix — a test-only change adds no
   new test files, so the total is unchanged from the prior round's `Ran
   874 tests` because the two strengthened tests replace, not add to, the
   existing two test methods).

### Verdict on the must-fix
**Resolved.** The fix is real, not cosmetic: the new assertions are proven
load-bearing by my own from-scratch revert-and-fail (not a re-trust of the
developer's report), the fixture's scoping rationale (module-level rebind,
not global mutation) checks out by direct reading of `app.py`'s own
`import threading` and the route's call sites, and the restore left no
residual diff. All 18 of `docs/spec.md`'s item 7 part 2 acceptance criteria
are now implemented and independently verified by tests I ran myself this
session (the 17 already-clean ones from the prior round, plus the
previously-gapped "starts the background driving thread" half of #8, now
closed).

The should-fix (WCAG contrast on `.team-btn`, pre-existing from earlier
cycles, not a regression) remains open as a non-blocking follow-up, exactly
as instructed — not touched this round, correctly so.

## Overall verdict (re-review): **Approve**

This closes the fix-and-reapprove round and the item 7 part 2 build cycle.
No further changes requested. Outstanding non-blocking follow-ups for a
future cycle: (1) disclose `/team/stop`'s pre-existing no-op-on-
`blocked_board_write` gap in `docs/implementation.md`'s "Known limitations"
(carried from part 1's review, already fixed in part 2's own code per test
case #12 — this is now just a stale doc-only item, arguably moot), (2) file
a project-wide `.team-btn` contrast fix and correct `docs/design.md`'s
accessibility section to name the actual `#34c759` color instead of the
unused `#4da6ff`.

# Test & Review: Backlog item 8 — AI merge-request reviewer, Gitea-only

## Scope
Full testing + review pass against `docs/spec.md`'s 12 acceptance criteria
for the poll-triggered AI PR reviewer (`app/app.py`'s
`_ai_reviewer_poll_repo`/`_ai_reviewer_review_bg`/`_ai_reviewer_review_run`
+ `app/teams.py`'s `review_pr_diff`/`_build_review_prompt`), plus the three
disclosed deviations in `docs/implementation.md` and the two safety
properties the assigning prompt specifically called out for hands-on
verification (engine-kind workdir isolation, the double-post race).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `AI_REVIEWER_ENABLED=0` → no Gitea calls, no review | Automated | pass | `test_disabled_makes_no_calls` |
| 2 | Label add edge → comment posted, state records `label_present:true` | Automated (split across dispatch + run layers, same layering as `_gitea_sync_bg`) | pass | `test_label_add_edge_triggers_dispatch_and_synchronous_state_write`, `test_empty_but_200_diff_is_still_reviewed_not_an_error` (posts to `/repos/admin/proj/issues/1/comments`) |
| 3 | Label still present, no removal → no second comment-post | Automated | pass | `test_label_still_present_after_a_successful_review_does_not_redispatch` |
| 4 | Label removed then re-added → exactly one new episode | Automated | pass* | `test_label_removed_then_readded_is_exactly_one_new_episode` — see Finding 1 for a narrower, untested cross-episode overlap case |
| 5 | Diff over `AI_REVIEWER_MAX_DIFF_BYTES` → truncated, comment notes it | Automated | pass | `test_diff_exceeding_cap_is_truncated_and_comment_notes_it` (byte-exact assertion) |
| 6 | `AI_REVIEWER_MODEL` unset/unknown → no crash, `last_error` recorded, other repos unaffected | Automated (per-repo isolation inferred by construction — see Spec coverage) | pass | `test_model_not_in_roster_records_failure_no_diff_fetch`, `test_model_unset_records_failure` |
| 7 | Review failure → `attempts` increments, `label_present` stays true; gives up after `AI_REVIEWER_MAX_ATTEMPTS` | Automated | pass | `test_diff_fetch_non_200_records_failure_and_posts_no_comment`, `test_attempts_exhausted_gives_up_silently` |
| 8 | Engine-kind review never touches `PROJECTS_DIR/<name>`; scratch dir always removed | Automated + hands-on adversarial revert-and-fail | pass | `ReviewPrDiffEngineTests` (5 tests) + my own revert (see "Hands-on verification" below) |
| 9 | Ollama-kind review calls `_tier1_call_with_retry` with `tools=[]` | Automated | pass | `test_calls_tier1_with_empty_tools_list_and_returns_text` |
| 10 | PR on unregistered repo never inspected | Structural (by construction — see Spec coverage) | pass | code read: `_ai_reviewer_poll_repo` only called from `_gitea_poll_if_due()`'s `_load_gitea_repo_map()` loop |
| 11 | Grounding digest present in the constructed prompt | Automated | pass | `test_contains_grounding_digest_verbatim` |
| 12 | No code path calls a Gitea approve/merge/branch-write endpoint | Automated + manual diff read | pass | `test_no_call_ever_targets_a_merge_or_approve_endpoint` + full read of the `app.py`/`teams.py` diff (only 3 new Gitea calls: `GET .../pulls`, `GET .../pulls/{n}.diff`, `POST .../issues/{n}/comments`) |

\* Row 4: the automated test covers the AC as literally written (label
cycles with no overlapping in-flight review). A related, narrower race
*not* covered by any AC or test is documented as Finding 1 below.

## Hands-on verification (per the assigning prompt's specific asks)

**Engine-kind workdir isolation, traced not trusted.** Read `agent_run()`
(`app/teams.py:993`): it launches `subprocess.run(TMUX + ["new-session",
"-d", "-s", session, "-c", workdir, "bash", "-l", script_path])`, i.e. the
tmux pane's cwd is set to whatever `workdir` argument `agent_run()` is
given — and `TMUX = ["sudo", "-u", RUN_USER, "/usr/bin/tmux"]`
(`app/app.py:226`), so this really is the reviewing engine's own process
cwd, as `RUN_USER`, not just a display artifact. `review_pr_diff()`'s
engine branch (`app/teams.py:1882`) passes `scratch` — never `workdir`
(the real `PROJECTS_DIR/<name>` argument) — as that argument. I then did a
genuine revert-and-fail check (not just a read): edited a scratch copy of
`teams.py` to pass `workdir` instead of `scratch` into that `agent_run()`
call, ran `ReviewPrDiffEngineTests`, and confirmed it fails (4/5 tests red,
`AssertionError: ... == ...` on the workdir-equality assertion and on the
scratch-dir-removed assertions), then restored the original file and
re-confirmed all 5 pass green. This is real evidence the tests catch a
broken isolation guarantee, not just that they pass against already-correct
code. (Note: this revert-and-restore cycle transiently corrupted a stale
`.pyc` cache — see "Process note" below — resolved by clearing
`__pycache__`, not a code issue.)

**Double-post race.** Traced `_gitea_poll_if_due()` (`app/app.py:842`):
`_gitea_poll_lock` is held (non-blocking acquire) for the *entire* per-repo
loop, including the new `_ai_reviewer_poll_repo()` call — so two poll
passes can never run concurrently process-wide; a second `/status` request
arriving mid-pass just no-ops (`if not _gitea_poll_lock.acquire(blocking=False):
return`). The one place a real race is even possible is a
review running on its own background thread outliving the poll pass that
spawned it — and the deployment is a single `python3 app.py` process
(`systemd/ai-dev-switchboard.service`: `Type=simple`, no worker pool), so
all the in-process `threading.Lock`s here are the real synchronization
primitive, not merely process-local theater. For that scenario: the
synchronous `label_present:true` write happens in `_ai_reviewer_poll_repo()`
*before* `_ai_reviewer_review_bg()` is even called, and the deviation's
`last_error is not None` gate means a poll pass landing while a review for
the *same episode* is still in flight (`attempts=0`, `last_error=None`,
both set by that synchronous write) takes the "already reviewed, don't
retry" branch, not a redispatch — confirmed by `test_label_still_present_after_a_successful_review_does_not_redispatch`'s
same-shaped setup and reasoned through by hand for the in-flight case
specifically (both reach the same state). The per-PR `threading.Lock` in
`_ai_reviewer_review_bg()` is real defense-in-depth on top: even if a
redispatch attempt were made, `lock.acquire(blocking=False)` would return
`False` and the second dispatch would silently no-op. **This closes the
double-post race the deviation and Edge Cases section are about.** In the
course of constructing this scenario by hand I found a *different*,
narrower race the per-PR lock does *not* close — see Finding 1.

## Regression check
Full existing suite: `python3 -m unittest discover -s tests` — **920 tests,
OK** (re-run twice for confidence after a self-inflicted stale-`.pyc`
false-failure, see "Process note" below; both clean runs post-cache-clear
were 920/920 green, matching `docs/implementation.md`'s own claimed count).
`tests.test_ai_reviewer` alone: 46/46 green. `python3 -m py_compile
app/app.py app/teams.py`: clean. `bash -n scripts/gitea-configure-api.sh`:
clean.

## Process note (not a code defect)
Mid-session, my own adversarial revert-and-restore of `app/teams.py`
(edit → test → restore, all within the same filesystem second) left a
stale `__pycache__/teams.cpython-313.pyc` that Python's mtime-based
invalidation didn't detect, causing `ReviewPrDiffEngineTests` to
transiently and non-deterministically fail against the *correct,
unmodified* source on a couple of subsequent runs. `diff`-confirmed the
restored `app/teams.py` was byte-identical to the pre-edit copy; clearing
all `__pycache__` directories fixed it immediately and reproducibly.
Recorded here per this role's "actual execution, not simulation" and
"systematic debugging" discipline — flagging it so it isn't mistaken for a
real flaky test in this suite, and because a future session hitting the
same symptom should reach for `find . -name __pycache__ -exec rm -rf {} +`
before assuming a genuine regression.

## Spec coverage
All 12 acceptance criteria: implemented and covered (automated test or,
for #10, structurally guaranteed by control flow and confirmed by direct
code read — `_ai_reviewer_poll_repo` is only ever called with `owner_repo`
values drawn from `_load_gitea_repo_map()`'s own iteration, identical
shape to the already-established `_gitea_poll_one` scoping, no dedicated
test exists for this in `test_gitea_poll.py` either). Two deviations from
the spec's own literal walkthrough text were independently re-verified
against the spec's literal wording, not just taken on the developer's
word:

- **Deviation 1 (retry gate additionally requires `last_error is not
  None`)**: confirmed by reading `docs/spec.md` line 73 ("2xx → record
  success: `{label_present: True, attempts: 0, ... last_error: None}`")
  against line 64 ("Present now, was already present, `attempts <
  AI_REVIEWER_MAX_ATTEMPTS`: ... spawn `_ai_reviewer_review_bg()` again")
  — the literal reading is indeed self-contradictory with acceptance
  criterion #3 exactly as the developer describes (a successful review
  resets `attempts` to 0, which is always `< AI_REVIEWER_MAX_ATTEMPTS`,
  so the literal condition alone would redispatch forever). The
  developer's fix is correct and necessary, not an unjustified
  reinterpretation.
- **Deviation 2 (empty-200-diff is not a failure)**: confirmed
  `docs/spec.md` line 68 ("Non-200 or empty → record failure") directly
  contradicts line 114's Edge Cases entry ("Empty diff ... still reviewed
  ... not treated as an error"). No acceptance criterion mentions empty
  diff as a failure case, so favoring the more specific, explicitly-settled
  Edge Cases text over the terser walkthrough prose is the correct
  resolution and doesn't regress any AC.
- **Deviation 3 (`TEAM_STATE_DIR` explicit `chmod(0o711)`)**: sanity-checked
  against `install.sh`, which never creates `$STATE_DIR/teams` itself —
  it's lazily `os.makedirs`'d by `SVC_USER`-run code with no explicit
  chmod anywhere pre-existing in `teams.py`. No `UMask=` directive is set
  in `systemd/ai-dev-switchboard.service`, so under today's *default*
  systemd umask this directory would likely end up `0755` (traversable by
  `RUN_USER` regardless) — meaning the chmod is not strictly load-bearing
  under the current default install. But it becomes load-bearing under any
  stricter umask (a `UMask=0027` hardening directive, or a different
  default shell/service umask), which is exactly the class of "don't rely
  on `SVC_USER`'s ambient umask" problem `agent_run()`'s own `rundir`
  chmod already exists to guard against. This is genuine, precedented
  defensive coding, not scope creep — confirmed sound, not overreach.

## Findings (most severe first)

### 1. Cross-episode review overlap can silently drop a re-triggered review — should-fix
- File: `app/app.py`, `_ai_reviewer_poll_repo()` (~line 1049, the trigger-edge branch) + `_ai_reviewer_review_bg()` (~line 1023, the per-PR lock)
- Issue: the per-PR `threading.Lock` in `_ai_reviewer_review_bg()` is keyed only on `pr_key` (`owner/repo#number`), not on anything episode-specific. If a review genuinely outlives a full label-remove-then-re-add cycle (plausible: `TEAM_HEADLESS_TIMEOUT_SECONDS`/engine review latency can exceed several `GITEA_POLL_INTERVAL_SECONDS` — default 45s — intervals; a human or CI removing and re-adding the label in that window is realistic, not contrived), the sequence is: (1) old episode's review is still running, holding the per-PR lock; (2) label observed removed, state set `label_present:false`; (3) label observed re-added → this **is** a genuine new trigger edge, so `_ai_reviewer_poll_repo()` correctly writes the synchronous `label_present:true, attempts:0, last_error:None` state and calls `_ai_reviewer_review_bg()` for the *new* episode; (4) `_ai_reviewer_review_bg()`'s lock is still held by the *old* episode's thread, so the new dispatch is silently dropped (returns immediately, same as the "in-flight retry of the same episode" case it was designed for); (5) when the *old* thread eventually finishes — say, successfully — it calls `_save_ai_reviewer_state_entry(..., attempts=0, reviewed_at=now, last_error=None)`, overwriting the new episode's state with what looks like a completed, successful review of the *new* episode. Net effect: exactly one comment is posted (generated from the *old*, pre-removal diff), the new episode's own review never runs, and nothing in the state file or logs indicates anything was missed — `last_error` stays `None`. This undercuts the spec's own stated Goal ("Re-fire only when the label is explicitly removed and re-added") for this specific timing window, though it does not double-post, does not touch the real workdir, and does not crash.
- Failure scenario: operator adds label → engine-kind review takes 3 minutes to run (within `TEAM_HEADLESS_TIMEOUT_SECONDS`, longer than one 45s poll interval) → operator, unaware the first review is still working, removes and re-adds the label after 90s (e.g. to fix a typo in the PR description first) → the old review finishes at the 3-minute mark and posts a comment based on the diff as it was *before* the operator's fix, while the newly-intended re-review silently never happens; state file shows a clean, error-free `reviewed_at` timestamp, giving no signal anything is wrong.
- Not covered by any test or any acceptance criterion in `docs/spec.md` — none of the 12 ACs specify behavior for "label re-added while a previous review for an earlier episode is still in flight." Recommend either keying the per-PR lock (or a check inside it) to the specific episode/trigger-write, or having a review's completion re-check whether the state it's about to write still corresponds to the episode it was dispatched for before overwriting.

### 2. No direct multi-repo isolation test for `_ai_reviewer_poll_repo`'s own try/except wrapper — nit
- File: `app/app.py`, the `_gitea_poll_if_due()` call site (~line 865)
- Issue: AC #6 ("other registered repos' polls in the same pass are unaffected") is verified only by analogy to `_gitea_poll_one`'s already-tested identical wrapper shape, not by a dedicated test that registers two repos, makes one raise inside `_ai_reviewer_poll_repo`, and asserts the second repo's poll still runs. Low risk given the wrapper is a two-line `try/except Exception: pass` identical to the already-covered precedent, but it's the one AC in this feature not backed by a test that would actually fail if the wrapper were accidentally removed.

### 3. No direct test for "PR on unregistered repo never inspected" (AC #10) — nit
- File: `tests/test_ai_reviewer.py`
- Issue: verified only by code read (the poll only iterates `_load_gitea_repo_map()`); a one-line test constructing a repo map without the target repo and asserting `_gitea_api` is never called for it would make this AC self-verifying rather than inference-by-construction.

## Follow-ups (non-blocking)
- Consider Finding 1 for a near-term follow-up cycle — narrow but real, and self-diagnosing would help (e.g. logging when a dispatch is dropped due to an already-held per-PR lock, distinguishing "same-episode retry, expected" from "different episode, dropped").
- Findings 2–3: add the two structural tests called out, next time this file is touched.

## Overall verdict: **Approve with follow-ups**

All 12 acceptance criteria are implemented and either directly tested or
provably true by construction; the full 920-test suite is green with no
regressions; the three disclosed deviations were each independently
re-verified against the spec's own literal text (not taken on trust) and
are all sound, necessary, and correctly scoped — not overreach. The two
safety properties called out for hands-on verification both check out:
engine-kind reviews are genuinely isolated to a throwaway scratch
directory (confirmed via source trace through `agent_run()`'s real tmux
`-c` argument *and* a live revert-and-fail check), and the double-post
race is genuinely closed by the synchronous state write plus the
deviation's `last_error` gate plus the per-PR lock, given the single-
process deployment topology. One should-fix (Finding 1) is a real, if
narrow, gap in the "re-fire on relabel" guarantee under review-overrun
timing that no acceptance criterion or test currently covers — it doesn't
block this cycle (no data-loss, no double-post, no workdir-isolation
breach, self-recovers on the next true label cycle) but is worth a
follow-up. Two nits (Findings 2–3) are test-coverage-only, no behavior
risk.

# Test & Review: Backlog item 13 — surviving team branch discoverability

## Scope
Testing + review pass against `docs/spec.md`'s 6 acceptance criteria for
read-only team-branch discoverability: `app/teams.py`'s
`list_team_branches()`/`_TEAM_BRANCH_RE`, the `team-branches` CLI
subcommand, the `GET /projects/<name>/team/branches` route, the Teams
page's "Past team branches" panel, and the new `docs/ARCHITECTURE.md`
section — plus the two disclosed deviations (full-vs-short commit hash,
plain `YYYY-MM-DD` instead of relative date) and the four specific things
the assigning prompt called out for hands-on verification (never-raises
failure paths, the naming-convention regex on a non-matching branch, the
web route's auth/project-scoping guard, and genuine end-to-end read-only-
ness).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1a | Multiple `team-*` branches, correct `branch`/`commit`/`subject`/`committer_date`, `run_id`/`agent` parsed from naming convention | Automated (real git, no mocks) | pass | `tests/test_teams_lifecycle.py::ListTeamBranchesRealGitTests::test_multiple_branches_correct_metadata_and_run_id_agent_parsed` |
| 1b | Zero matching branches → `[]`, not an exception | Automated | pass | `...::test_zero_matching_branches_returns_empty_list` |
| 1c | Non-git directory → `[]` | Automated + manual (`list_team_branches('/tmp/nongit')` → `[]`, exit 0 via CLI) | pass | `...::test_non_git_directory_returns_empty_list_not_an_exception`; manual CLI run this session |
| 1d | Nonexistent directory → `[]` | Automated | pass | `...::test_nonexistent_directory_returns_empty_list_not_an_exception` |
| 1e | Missing `git` binary → `[]`, never raises | Manual (patched `subprocess.run` to raise `FileNotFoundError`, called `list_team_branches()` directly this session) | pass | inline repro this session: `patch('teams.subprocess.run', side_effect=FileNotFoundError(...))` → `[]` |
| 1f | Malformed/short `--format` line → skipped, never raises | Read code path (`len(parts) != 4: continue`) — no adversarial-format test exists, but the parse loop can't crash on it | pass (by construction) | `app/teams.py` `list_team_branches()` |
| 1g | Branch name not matching `team-{run_id}-{agent}` convention → `run_id`/`agent` both `None`, no crash, no mis-parse (e.g. a hand-made branch with embedded hyphens) | Automated + manual (`team-my-hand-made-branch`) | pass | `...::test_branch_not_matching_naming_convention_gets_none_run_id_and_agent`; manual CLI run this session (`team-my-hand-made` → `run_id: null, agent: null`) |
| 1h | Branch survives worktree removal (the actual scenario item 13 exists for) | Automated, real `_create_worktree()`/`_remove_worktree()` pair | pass | `...::test_survives_worktree_removal_same_as_create_remove_worktree_tests_above` |
| 2a | `team-branches <workdir>` CLI prints same JSON as `list_team_branches()` | Automated | pass | `...::CliTeamBranchesTests::test_prints_same_data_as_list_team_branches_as_json` |
| 2b | CLI exits 0 on an empty list | Automated + manual | pass | `...::test_exits_0_when_list_is_empty`; manual CLI run this session |
| 3a | `GET /projects/<name>/team/branches` reachable through the real HTTP guard, same JSON shape as `list_team_branches()` | Automated (real `HTTPServer`, real login flow) | pass | `tests/test_team_routes.py::TeamBranchesEndpointTests::test_matching_branches_returned_same_shape_as_list_team_branches` |
| 3b | Unknown project → 404 | Automated | pass | `...::test_unknown_project_404` |
| 3c | Empty list → 200 `[]`, not an error | Automated | pass | `...::test_no_matching_branches_returns_empty_list_not_an_error` |
| 3d | No TOTP required (read-only, matches `/team/grounding` gating) | Automated | pass | `...::test_read_only_no_totp_required` |
| 3e | Guard is genuinely the same code path as sibling `/team/*` routes, not just similar | Read code directly (`app/app.py` `do_GET()`) | pass | `_authed()` check at top of `do_GET` (line 4431) is unconditional for every branch below it; the new `/team/branches` arm (line 4621) uses the identical `name not in instance_names(): 404` guard, same lines-away pattern as `/team/grounding` immediately above it — not a re-implementation, same literal idiom |
| 4a | Teams page shows "Past team branches" panel populated from the route | Automated (Node/vm harness, no browser) | pass | `tests/test_team_frontend.js`: `'branch entries render name/short commit/subject/date, no action buttons'` |
| 4b | No action buttons (list-only) | Automated | pass | same test, explicit `!/<button[^>]*>/` assertion scoped to the panel's own markup |
| 4c | Panel renders for both idle and running rows | Automated | pass | `'idle row fetches team branches once...'`, `'a running team row also renders the past branches panel'` |
| 4d | Fetched once per project, not joined to the 4s poll | Automated | pass | `'team branches are fetched only once per project, cached across later poll cycles'` |
| 4e | Fetch failure degrades to a clear message, not a crash | Automated | pass | `'a fetch failure (non-ok status) renders "Past team branches unavailable"'` |
| 5 | `docs/ARCHITECTURE.md` gains the new section with `git log`/`git merge`/`git branch -D` | Read diff directly | pass | `docs/ARCHITECTURE.md` "Reviewing a team's work after it stops" — all three commands present, against `team-<run_id>-<agent>` |
| 6a | No existing test regresses | Automated, full suite | pass (see Regression check — 3 unrelated pre-existing failures, not caused by this diff) | `.test_full_run.log` (932 tests) |
| 6b | New tests cover multiple branches, zero branches, non-git dir, non-matching name, CLI, route | Automated | pass | see rows 1a–3d above |
| — | Genuinely read-only end-to-end (explicit ask) | Read code directly: the only `subprocess`/git invocation anywhere in `list_team_branches()`, the CLI wrapper, and the route handler is the single `git branch --list ... --format=...` call | pass | `app/teams.py` lines ~3460–3524 (no `branch -D`/`checkout`/`merge`/`push`/`commit` anywhere in the new code path); `app/app.py`'s route handler only calls `teams.list_team_branches(...)` and JSON-serializes the result |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests -v` (932
tests, 138s) — **3 failures, all pre-existing and unrelated to this diff**:

- `test_deploy_dispatch.PrivilegedDeployRunEndToEndTests.test_restart_failure_on_target_surfaces_distinct_502`
- `test_deploy_dispatch.PrivilegedDeployRunEndToEndTests.test_success_pushes_file_and_restarts_service`
- `test_deploy_target.InstallScriptDeployTargetBlockTests.test_blank_pubkey_leaves_authorized_keys_untouched_prints_instructions`

All three are in the real-host-mutating "provisions a throwaway system
user, sudoers rule, and systemd unit" test classes for a completely
separate subsystem (deploy dispatch / install-script deploy-target
provisioning); the failures are `switchboard-deploy-wrapper.sh: not found`
/ `ai-dev-switchboard-deploy-wrapper.sh: not found` and an
`authorized_keys` assertion — host-state/wrapper-installation issues on
this sandbox, not logic failures. Confirmed unrelated by: (1) neither
failing test file contains any reference to "team" or the new code at all
(`grep -i team tests/test_deploy_dispatch.py tests/test_deploy_target.py`
→ no hits), (2) this cycle's diff touches only `app/teams.py`'s new
`list_team_branches()`/CLI, `app/app.py`'s new route/frontend panel, and
team-specific test files — nothing in deploy dispatch or install.sh, (3)
`git status` at the start of this review showed these two test files and
`install.sh` already committed (from an earlier, already-merged cycle),
not part of the working tree under review here. Flagged for the
orchestrator's awareness as a separate, pre-existing environment issue
worth its own follow-up — not a regression this cycle introduced, and not
blocking this backlog item's verdict.

All 12 new backend tests (`ListTeamBranchesRealGitTests` × 6,
`CliTeamBranchesTests` × 2, `TeamBranchesEndpointTests` × 4) pass. Frontend:
`node tests/test_team_frontend.js` → **ALL PASS (80/80)**, including the 6
new tests for this panel.

## Spec coverage
All 6 acceptance criteria implemented and tested — see the Test cases
table above (rows grouped 1a–1h → criterion 1, 2a–2b → criterion 2, 3a–3e →
criterion 3, 4a–4e → criterion 4, 5 → criterion 5, 6a–6b → criterion 6). No
gaps.

The two disclosed deviations were independently re-checked against
`docs/spec.md`'s own literal text, not taken on trust:
- Full (not abbreviated) commit hash from the function/CLI/route, shortened
  to 7 chars only in the UI panel — matches `docs/spec.md`'s own literal
  `--format` string (`%(objectname)`, full hash) for the backend/CLI/route,
  and its separate UI description ("short commit hash") for the panel only.
  Not actually a deviation from anything acceptance-criteria-visible; sound.
- Plain `YYYY-MM-DD` date slice instead of a relative ("3 days ago") string
  — `docs/spec.md`'s acceptance criteria don't test for a specific date
  format (criterion 4 only requires the panel to be "populated... with no
  action buttons"), and the developer's stated rationale (no relative-time
  formatting dependency exists anywhere else in this stdlib-only codebase)
  is verified true by inspection — no other `app/app.py`/`app/teams.py`
  code does relative-time formatting. Proportionate, correctly scoped.

## Findings (most severe first)

### 1. Possible duplicate in-flight `/team/branches` fetch on rapid re-render — nit
- File: `app/app.py`, `renderTeamBranches()` (~line 2581) /
  `fetchTeamBranches()` (~line 2560)
- Issue: `renderTeamBranches()` fires `fetchTeamBranches(name)` whenever
  `teamBranchesCache[name] === undefined`, with no in-flight sentinel. If
  `refresh()`/`teamRow()` renders again before the first fetch resolves
  (e.g. a second poll tick or an explicit `refresh()` from an unrelated
  action within that window), the cache is still `undefined`, so a second
  concurrent GET fires.
- Failure scenario: harmless in practice — the request is a side-effect-
  free GET, and this is the exact same pattern this codebase's own
  `teamGroundingCache`/`fetchTeamGrounding()` already uses (no in-flight
  guard there either), just with a wider exposure window since
  `renderTeamBranches()` is called from every unconditional `teamRow()`
  render rather than gated behind a user gesture like opening the picker.
  Not a correctness bug, not new to this diff's own convention — noted for
  awareness only, not worth a fix.

### 2. No explicit "no cookie → 401" test for `/team/branches` — nit
- File: `tests/test_team_routes.py`, `TeamBranchesEndpointTests`
- Issue: the new test class doesn't include a case asserting an
  unauthenticated request gets a 401.
- Failure scenario: none observed — `_authed()` is checked unconditionally
  at the top of `do_GET()` before any route matching happens, so this is
  covered by construction, and the sibling `TeamGroundingEndpointTests`
  class this one was modeled on has the identical omission. Consistent with
  existing project convention, not a gap this diff introduced.

## Follow-ups (non-blocking)
- Investigate/fix the 3 pre-existing `PrivilegedDeployRunEndToEndTests`/
  `InstallScriptDeployTargetBlockTests` host-state failures (missing deploy
  wrapper script on this sandbox) as its own separate item — unrelated to
  backlog item 13, but real and currently red.

## Overall verdict: **Approve**

All 6 acceptance criteria are implemented and directly tested, all four
specifically-called-out verification points check out against the actual
code (never-raises failure paths including a live simulated missing-`git`-
binary repro, the naming-convention regex's non-matching-branch handling,
an auth/project-scoping guard confirmed identical to its sibling
`/team/*` routes by reading `do_GET()` directly rather than trusting the
developer's claim, and a read-only code path confirmed by inspection to
contain exactly one git subprocess call, a `branch --list`). Both disclosed
deviations are sound and within scope. The full 932-test suite has 3
failures, both investigated and confirmed pre-existing/environmental
(real-host deploy-wrapper provisioning, a wholly separate subsystem with
zero references to this diff's code) rather than caused by this change.
Two nits noted for awareness, neither blocking.

# Test & Review: Backlog item 16 — clone a project by `git clone <url>` directly

## Scope
Testing + review pass against `docs/spec.md`'s 12 acceptance criteria for
clone-from-URL: `app/app.py`'s `_validate_clone_url()`,
`_last_path_segment_from_clone_url()`, `_derive_project_name()`'s new
`fallback_prefix` param, `clone_project_from_url()`, the new
`POST /projects/clone` route, the new privileged script
`scripts/new-project-from-url.sh`, unconditional `install.sh` wiring, and
the frontend inline "Clone from URL" form — plus the assigning prompt's
four specific hands-on asks: (1) constructing a real adversarial URL and
confirming rejection at both the app-level allowlist and the script's own
defense-in-depth layer, (2) verifying the `trap cleanup ERR` → `EXIT` bash
semantics claim with a minimal repro, (3) verifying the `install.sh`
placement-constraint claim against `tests/test_deploy_dispatch.py`, (4)
tracing the oversized-clone rollback end-to-end, and (5) independently
re-confirming the "4 pre-existing `test_deploy_frontend.js` failures" claim
via `git stash`.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Valid public `https://` URL, no `name` override → project appears under derived name, working `.git`, owned by `RUN_USER`, no restart needed | Automated, real `git http-backend`-backed server + real `sudo` | pass | `tests/test_new_project_from_url.py::PrivilegedCloneTests::test_clones_real_public_repo_owned_by_run_user` |
| 2 | Explicit `name` override → registers under that exact name | Automated (app-level mocked + script-level real, incl. spaces/hyphens) | pass | `tests/test_clone.py::CloneProjectFromUrlTests::test_explicit_name_override_used_instead_of_derived`; `tests/test_new_project_from_url.py::PrivilegedCloneTests::test_name_with_space_and_hyphen_accepted` |
| 3 | Disallowed scheme (`file://`, `ext::`, `fd::`, `git://`, bare/relative local path) → 400 before any subprocess, no directory created | Automated, both layers | pass | `tests/test_clone.py::ValidateCloneUrlTests` (11 cases); `tests/test_new_project_from_url.py::ArgumentValidationTests` |
| 4 | Schemeless `-oProxyCommand=...`-shaped URL → rejected, never reaches `git clone` as an argv token | Automated + manual adversarial construction (see Hands-on verification) | **pass for the literal schemeless case; gap found in the broader injection-defense goal for the `ssh://`-scheme variant — see Finding 1** | `tests/test_clone.py::ValidateCloneUrlTests::test_argument_injection_shape_rejected`; my own repro below |
| 5 | Explicit `name` failing `NAME_RE` → 400, same message as `create_project()` | Automated | pass | `tests/test_clone.py::CloneProjectFromUrlTests::test_explicit_name_failing_name_re_rejected_before_subprocess`; message text confirmed byte-identical via `grep` (both call sites at `app/app.py:1277`/`1366`) |
| 6 | Name collision (explicit or derived) → 400 `"'<name>' already exists."`, existing project untouched | Automated | pass | `tests/test_clone.py::CloneProjectFromUrlTests::test_name_collision_rejected_before_subprocess`, `test_derived_name_collision_rejected_before_subprocess` |
| 7 | Two concurrent same-name clones → exactly one succeeds, other fails cleanly | Automated, atomic-`mkdir`-no-`-p` proof (same technique as sibling items 2b/3 — neither has a literal-threads test either, confirmed by inspection) | pass | `tests/test_new_project_from_url.py::PrivilegedCloneTests::test_already_existing_target_fails_atomically_no_dash_p` |
| 8 | Unreachable/nonexistent host → 400 clipped error, well under timeout, no orphaned directory | Automated, real HTTP server returning a 404-shaped bad path | pass | `tests/test_new_project_from_url.py::PrivilegedCloneTests::test_clone_failure_removes_dest_deviation_from_gitea_sibling` |
| 9 | Private HTTPS repo (auth required) → fails fast, not hung | Automated, real HTTP server always demanding Basic auth | pass | `tests/test_new_project_from_url.py::PrivilegedAuthRequiredTests::test_auth_required_repo_fails_fast_not_hangs` (bounded 30s `PRIVILEGED_TEST_TIMEOUT` is itself part of the assertion) |
| 10 | Oversized clone → `DEST` removed, 400, never appears in project list | Automated, real clone + `CLONE_MAX_BYTES=1` forcing rollback (see Hands-on verification) | pass | `tests/test_new_project_from_url.py::PrivilegedCloneTests::test_oversized_clone_rolled_back_and_removed` |
| 11 | No `--with-git-hosting` dependency (`GITEA_ENABLED=0`, no token) → still succeeds | Automated, monkeypatched `GITEA_ENABLED`/`GITEA_API_TOKEN` | pass | `tests/test_clone.py::CloneProjectFromUrlTests::test_no_gitea_dependency_reads` |
| 12 | `ssh://`/scp-like URL to a host `RUN_USER` already has SSH access to → succeeds with no switchboard-side credential | Validation-regex-only (no live SSH server in this sandbox) — disclosed known limitation, matches item 2b's own precedent exactly (confirmed by reading `tests/test_new_project_from_gitea.py`, which also only exercises the HTTP path end-to-end) | pass (regex level); not live-SSH-tested (accepted, precedented gap) | `tests/test_clone.py::ValidateCloneUrlTests::test_ssh_scheme_accepted`/`test_scp_like_shorthand_accepted` |
| 13 | `scripts/new-project-from-url.sh` run directly with wrong arg count / invalid name/URL → exits 1, touches no filesystem state | Automated, unprivileged | pass | `tests/test_new_project_from_url.py::ArgumentValidationTests` (8 cases) |
| — | Frontend: open/close toggle, empty-URL guard, loading/"Cloning…" state, success clears+hides form, 400 shows server error, 428 TOTP retry, cancel re-enables form | Automated, real rendered `<script>` extracted from `render_page()` in a Node `vm` sandbox (same proven technique as `test_deploy_frontend.js`) | pass | `node tests/test_clone_frontend.js` → 8/8 |

## Hands-on verification (per the assigning prompt's specific asks)

**1. Adversarial URL construction — app-level allowlist.** Ran a battery of
crafted URLs directly against `_validate_clone_url()`:
```
'-oProxyCommand=touch /tmp/pwned-app'      -> REJECTED
'ext::sh -c touch /tmp/pwned2'             -> REJECTED
'fd::5:'                                   -> REJECTED
'file:///etc/passwd'                       -> REJECTED
'--upload-pack=touch /tmp/y'               -> REJECTED
'git://example.com/repo.git'               -> REJECTED
'/etc/passwd', '../../etc/passwd'          -> REJECTED
'user@-oProxyCommand=id:path'              -> REJECTED (the `=` isn't in
                                               the scp-like host char class)
'ssh://-oProxyCommand=touch${IFS}/tmp/x'   -> ACCEPTED  <-- see Finding 1
'ssh://-oProxyCommand=id'                  -> ACCEPTED  <-- see Finding 1
```
Most of the spec's own enumerated shapes are correctly rejected. Two
`ssh://`-scheme variants are **not** — `_CLONE_URL_SCHEME_RE = r"^(https?|ssh)://\S+$"`
places no constraint on the character immediately after `://`, so a
scheme-prefixed CVE-2017-1000117-shaped host (`-oProxyCommand=...`) passes.
See Finding 1 below for the full analysis (including proof that the same
gap exists in the script's own bash-regex mirror, and that the only actual
protection in this repo's git — 2.47.3 — is git's own upstream 2017 patch,
not this codebase's validation).

**2. Adversarial URL — script-level defense-in-depth, end to end.** Ran
`scripts/new-project-from-url.sh` for real, via `sudo`, exactly as
`clone_project_from_url()` would invoke it:
```
$ sudo env RUN_USER=$(id -un) PROJECTS_DIR=/tmp/npfu-manual-test/projects \
    bash scripts/new-project-from-url.sh \
    'ssh://-oProxyCommand=touch${IFS}/tmp/pwned-via-script2' 'advtest2'
git clone failed:
Cloning into '/tmp/npfu-manual-test/projects/advtest2'...
fatal: strange hostname '-oProxyCommand=touch${IFS}' blocked
exit: 1
```
Confirmed: (a) the URL is never shell-interpolated — it reaches `git
clone` as a genuine argv token via `su ... -c '...git clone -- "$1" "$2"' _
"$URL" "$DEST"`, exactly as `docs/spec.md` §5 "DEVIATION 2" describes,
proven by the fact that `${IFS}` in the payload is never expanded (it
appears literally in git's own error message, not executed); (b) no file
was created at `/tmp/pwned-via-script2`; (c) `DEST` was correctly removed
(the `EXIT`-trap cleanup ran); (d) the thing that actually stopped the
clone was git's own "strange hostname blocked" guard (added upstream for
CVE-2017-1000117, present since git 2.14.1, Aug 2017), not this script's
own bash-regex re-validation (`^(https?|ssh)://[^[:space:]]+$`, which has
the identical gap as the Python regex it mirrors). Net effect on this
sandbox (git 2.47.3): safe. See Finding 1 for why this is still worth
fixing.

**3. `trap cleanup ERR` → `EXIT` bash-semantics claim.** Verified directly
with a minimal repro before trusting the developer's claim:
```
$ cat trap_test.sh   # trap cleanup ERR; echo before; exit 1
$ ./trap_test.sh
before                    # cleanup never printed — ERR trap did not fire
$ cat trap_test2.sh  # trap cleanup EXIT; echo before; exit 1
$ ./trap_test2.sh
before
CLEANUP RAN               # EXIT trap fired correctly on explicit exit 1
$ cat trap_test3.sh  # trap cleanup EXIT; echo before; trap - EXIT; echo success
$ ./trap_test3.sh
before
success                   # cleanup did NOT run once cleared before success
```
Confirms the developer's bash-semantics claim exactly (`ERR` doesn't fire
on an explicit `exit` builtin; `EXIT` does; clearing it with `trap - EXIT`
right before the final success echo correctly avoids removing `DEST` on
the success path) — also independently confirmed by the real privileged
test suite (`test_clones_real_public_repo_owned_by_run_user` passes with
`DEST` present after a successful clone; `test_oversized_clone_rolled_back_and_removed`
passes with `DEST` absent after a triggered failure).

**4. `install.sh` placement-constraint claim.** Read
`tests/test_deploy_dispatch.py:557-559`: `InstallShDeployMapBlockTests`
extracts the deploy-map config block via the exact-substring marker
`'set_env "$ENV_FILE" UPLOAD_STAGING_TTL_SECONDS "1800"\n\n# Switchboard-side deploy dispatch'`.
Confirmed this substring is real, present verbatim in `install.sh`
(`grep -n` around lines 196-198, 407-409), and that the developer's new
block is placed after the deploy-dispatch block's own closing
`chown`/`chmod` pair (line ~420-423), not between the two halves of that
marker — preserving the exact adjacency the test regex depends on. Ran
`python3 -m unittest tests.test_deploy_dispatch -v`: 42/42 pass, confirming
the constraint is real and the fix doesn't reintroduce it.

**5. Oversized-clone rollback, traced concretely.** The real privileged
test (`test_oversized_clone_rolled_back_and_removed`) clones a genuine
one-commit repo over real `git http-backend`, forces `CLONE_MAX_BYTES=1`
via env, and asserts (a) the script exits nonzero, (b) stderr contains
"byte limit", (c) `PROJECTS_DIR/toobig` does not exist afterward. Ran it
directly: passes. Traced the code path: `du -sb "$DEST"` runs after the
clone succeeds, compares against `CLONE_MAX_BYTES` (default 524288000 =
500 MiB), and on breach falls through to the `EXIT` trap's `rm -rf "$DEST"`
(verified in point 3 above) rather than a separate explicit cleanup call —
correct and consistent with the rest of the script's failure handling.

**6. Pre-existing `test_deploy_frontend.js` failures.** Ran
`node tests/test_deploy_frontend.js` on the working tree: 4/9 fail (the
same 4 named in `docs/implementation.md`: cancel-dialog-sends-no-request,
quote-containing-host-renders-safely, confirmed-deploy-success-message,
428-mid-dispatch-retry). Then `git stash -u` (removing every change in
this cycle's diff, including all new files) and re-ran the identical
command: **same 4 tests fail, identical failure messages**, confirming
they are unrelated to this diff. `git stash pop` restored the working
tree. Independently confirmed, not taken on trust.

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` (988
tests, 142.8s) — **0 failures, all pass**. Also ran the new test files
individually: `tests.test_clone` (41/41), `tests.test_new_project_from_url`
(15/15, all run for real against `sudo` — passwordless sudo is available
on this sandbox, none skipped), `node tests/test_clone_frontend.js` (8/8),
and `tests.test_deploy_dispatch` (42/42, confirming the `install.sh`
placement change doesn't break the marker-extraction tests). The 4
pre-existing `test_deploy_frontend.js` failures are unrelated (see Hands-on
verification #6) and not counted against this diff.

## Spec coverage
12 of 12 acceptance criteria implemented and tested — see the Test cases
table above. Criterion 4 (argument-injection rejection) is satisfied for
its own literal wording (a schemeless `-oProxyCommand=...` string) but the
broader injection-defense **goal** it exists to serve ("closing off the
known git argument-injection ... RCE shapes", per spec's own "Goals"
section) has a real gap for the `ssh://`-scheme case — see Finding 1.
Criterion 12 (live SSH clone) is validation-level-only, an accepted,
precedented gap matching item 2b's own test suite. No other gaps.

## Findings (most severe first)

### 1. `ssh://`-scheme URLs with a leading-`-` host bypass this codebase's own injection-defense allowlist; only git's own upstream 2017 hardening prevents exploitation — must-fix
- File: `app/app.py:715` (`_CLONE_URL_SCHEME_RE = re.compile(r"^(https?|ssh)://\S+$", re.IGNORECASE)`), mirrored in `scripts/new-project-from-url.sh:29` (`^(https?|ssh)://[^[:space:]]+$`)
- Issue: neither regex constrains the character immediately following
  `://`, so a URL like `ssh://-oProxyCommand=id` (or any other single-token
  `-o...`-shaped SSH flag with no embedded whitespace) matches both the
  app-level allowlist and the script's own bash re-validation, and is
  handed to `git clone` as a real argv token. This is exactly the
  CVE-2017-1000117 shape the spec's own text explicitly claims this
  allowlist defends against ("`_validate_clone_url()` ... blocks git's own
  known argument-injection shape ... every accepted pattern above requires
  a fixed non-'-' prefix ... so a leading '-' can never match either
  regex"). That claim is true of the *whole string* (thanks to the literal
  `ssh://` prefix) but false of the *host component* that actually reaches
  `ssh` — which is the part CVE-2017-1000117 is about. I verified this
  gap for real, end to end, via `sudo`: the crafted URL passes both
  validation layers and reaches `git clone` as an argv token; the clone
  then fails only because installed git (2.47.3) itself refuses with
  `fatal: strange hostname '-oProxyCommand=...' blocked` — an upstream git
  hardening patch shipped in git ≥2.14.1 (Aug 2017), not anything this
  codebase's own allowlist did. `docs/spec.md`'s "Open questions" section
  explicitly (and, per this finding, incorrectly) states: "if a target's
  git predates `--` support for `clone` specifically, the allowlist regex
  alone still fully closes the argument-injection shape described above" —
  demonstrably false for this URL shape on any git older than 2.14.1.
- Failure scenario: an operator's switchboard host runs a git build
  predating August 2017 (realistic on a long-lived enterprise/LTS
  distribution image — the same "unverified minimum git version" risk
  class the spec itself already flags elsewhere, but this specific
  instance is a full argument-injection RCE via the `RUN_USER`-privileged
  `ssh` subprocess spawned by the script, not merely a `--`-flag
  compatibility question). Submits `ssh://-oProxyCommand=<attacker
  command>` through the web form. On such a host, both validation layers
  accept it and `ssh` executes the injected `ProxyCommand` as `RUN_USER`.
- Suggested fix (not applied by me — reviewer doesn't fix): tighten both
  regexes to reject a `-` immediately after `://` (e.g.
  `r"^(https?|ssh)://(?!-)\S+$"` in Python, `^(https?|ssh)://[^-[:space:]]` in
  bash, or equivalently parse out and check the actual host component)
  — closing the gap at this codebase's own validation layer rather than
  depending on git's own upstream patch, consistent with this project's
  stated "never trust a single layer" defense-in-depth discipline
  elsewhere in this very diff (the script's own header comments make
  exactly this argument for re-validating in bash at all).

### 2. `docs/design.md`'s stated WCAG contrast ratios don't match the actual hex values — should-fix (design doc, not this diff's code)
- File: `docs/design.md` "Accessibility & platform notes" (~line 512-516)
- Issue: recomputed all four stated pairs from the literal hex values used
  in `app/app.py`'s CSS (WCAG relative-luminance formula):
  - Button text `#fff` on button bg `#34c759`: design.md claims **5.05:1**;
    actual is **2.22:1** — fails WCAG AA for normal text (4.5:1) and even
    the large-text threshold (3:1); the button text is 14px/600-weight,
    below the ~18.7px-bold "large text" cutoff, so 4.5:1 is the applicable
    bar.
  - Placeholder `#666`/`#888` on input bg `#1c1c1c`: design.md claims
    **6.14:1** for both (identical to its separately-stated error-text
    ratio, suggesting a copy/paste error); actual is **2.97:1** for `#666`
    (fails even the 3:1 non-text threshold) and **4.81:1** for `#888`
    (passes AA, barely). Moot in practice: `app/app.py`'s CSS diff adds no
    `::placeholder` rule at all (grepped the whole file — none exists
    anywhere in this codebase), so the actual placeholder color is
    whatever the browser applies by default, not `#666`/`#888` — this part
    of design.md's accessibility analysis was never grounded in real CSS.
  - Error text `#ff6b6b` / status text `#aaa` on `#1c1c1c`: design.md's
    claims (6.14:1 / 6.4:1) are close to my recomputed values (6.14:1 /
    7.34:1) — the error-text figure is exactly right; the status-text
    figure is off but still comfortably passes AA either way.
- Failure scenario: none for this cycle specifically — the button styling
  is reused verbatim from the pre-existing "+ New project"/"Upload folder /
  .zip" buttons (not new to this diff; the same contrast gap already ships
  in production today), and the placeholder-color claim was never backed
  by real CSS in the first place. Not blocking backlog item 16 — flagged
  as a design-doc accuracy issue and a pre-existing, out-of-scope
  accessibility gap worth its own follow-up, not something this
  developer's diff introduced or is expected to fix here.

## Follow-ups (non-blocking)
- Investigate/fix the button-text-on-green-background contrast gap
  (`#fff` on `#34c759`, 2.22:1) as its own accessibility item — affects
  "+ New project" and "Upload folder / .zip" too, pre-existing and outside
  this backlog item's scope.
- Once Finding 1 is fixed, consider adding an explicit
  `tests/test_clone.py` case for `ssh://-oProxyCommand=...`-shaped URLs
  (and the bash-level equivalent in `tests/test_new_project_from_url.py`)
  so a future regression in either regex is caught without relying on
  installed git's own version-dependent hardening.

## Overall verdict: **Changes requested**

The testing pass is clean: all 13 acceptance-criteria-derived test cases
pass (12 from `docs/spec.md`'s literal acceptance-criteria list plus the
frontend suite), the full 988-test regression suite is green, and all five
of the assigning prompt's specific hands-on verification asks (adversarial
URL at both layers, `trap` bash-semantics repro, `install.sh` placement
constraint, oversized-clone rollback trace, independent re-confirmation of
the pre-existing frontend-test failures) check out — most of them exactly
as the developer described. However, the same hands-on adversarial-URL
exercise the assigning prompt specifically called for surfaced a real gap
(Finding 1): `_validate_clone_url()`'s own allowlist, and its bash mirror
in `scripts/new-project-from-url.sh`, do not actually close the
`ssh://`-scheme argument-injection shape they explicitly claim to close in
both `docs/spec.md`'s own text and the code's own comments — only
installed git's own 2017 upstream patch currently prevents exploitation on
this sandbox, which is not something this codebase's validation logic
accounts for, tests against, or can rely on for an environment running an
older git. Given this is the single most safety-critical property of the
entire feature (the reason a narrowly-scoped root-privileged script exists
at all) and the fix is small and precise, this is a must-fix, not a nit —
route back to the developer to tighten both regexes (or otherwise reject a
leading `-` in the URL's actual host component, not just at the very start
of the whole string) before re-review. Finding 2 (design.md's inaccurate
WCAG contrast claims) is a should-fix, non-blocking on its own, but is
included for the same cycle's fix-and-reapprove pass since it's cheap to
correct alongside Finding 1.

---

## Re-review: Backlog item 16 — Finding 1 fix-and-reapprove round

### Scope
Covers the developer's disclosed "Post-review fix" in `docs/implementation.md`:
the tightened `_CLONE_URL_SCHEME_RE`/`_CLONE_URL_SCP_RE` in `app/app.py`
(now `r"^(https?|ssh)://(?!-)\S+$"` / `r"^[A-Za-z0-9_.-]+@(?!-)[A-Za-z0-9_.-]+:\S.*$"`,
`app/app.py:722-723`), the matching bash re-validation in
`scripts/new-project-from-url.sh:34-35`
(`^(https?|ssh)://[^-[:space:]][^[:space:]]*$` /
`^[A-Za-z0-9_.-]+@[^-[:space:]][A-Za-z0-9_.-]*:[^[:space:]].*$`), and the
new adversarial test cases in `tests/test_clone.py` and
`tests/test_new_project_from_url.py`. Re-verified independently — I did not
re-run or re-trust the developer's own repro/revert-and-fail claims, I
constructed my own adversarial inputs from scratch per the assigning
prompt's explicit instruction.

### What I checked
1. **Read the actual diff.** Confirmed the regex change is exactly as
   `docs/implementation.md` describes, and confirmed via `git diff --stat`
   that this round's diff is scoped to `app/app.py`, the script, and the
   two test files (plus the docs pair every cycle updates) — no unrelated
   changes.
2. **Constructed independent adversarial URLs** (not reusing the
   developer's own `ssh://-oProxyCommand=id` / `user@-oProxyCommand=id:path`
   test cases) and ran them against both the live Python regexes and a real
   `sudo` invocation of the actual privileged script, mirroring the exact
   harness the developer's own privileged tests use.
3. **Ran `tests/test_clone.py` and `tests/test_new_project_from_url.py` in
   full** to confirm every legitimate URL shape still passes.
4. **Ran the full suite twice** (`python3 -m unittest discover -s tests`)
   and `node tests/test_clone_frontend.js`, and traced down an initial
   spurious failure to my own accidental concurrent invocation rather than
   a real regression (see "Regression check" below).
5. **Confirmed the WCAG should-fix was correctly left untouched.**
6. **Checked `docs/spec.md`'s "Open questions" text** for the
   already-flagged incorrect claim.

### Finding 1 (carried over from the prior round): **NOT resolved** — the tightened regex still lets a crafted URL reach a real `git clone` subprocess before this codebase's own validation rejects it

The developer's fix correctly closes the *literal* case flagged in the
prior round (`ssh://-oProxyCommand=id`, no `user@` prefix — confirmed
rejected at both layers, `Unsupported URL: ...` printed before any
`Cloning into...` line). But my own independently-constructed adversarial
URLs show the same underlying vulnerability class (CVE-2017-1000117-shaped
argument injection) is still open via two variants neither regex — nor any
new test — covers:

**Repro 1 — `ssh://` scheme, malicious host hidden behind a `user@` prefix.**
The negative lookahead in `_CLONE_URL_SCHEME_RE` (`(?!-)`) only checks the
character immediately after `://`; it never re-checks after an optional
`user@` segment, so a URL whose first character after `://` is an
innocuous username still matches even though the actual *host* component
(after `@`) starts with `-`:
```
$ python3 -c "
import re
r = re.compile(r'^(https?|ssh)://(?!-)\S+\$', re.IGNORECASE)
print(bool(r.match('ssh://user@-oProxyCommand=id/repo')))"
True   # ACCEPTED -- should be rejected
```
Confirmed the bash mirror in the script has the identical gap:
```
$ bash -c '
URL="ssh://user@-oProxyCommand=id/repo"
[[ "$URL" =~ ^(https?|ssh)://[^-[:space:]][^[:space:]]*$ ]] && echo "ACCEPTED by bash regex"'
ACCEPTED by bash regex
```
Ran the real, `sudo`-privileged script end to end, exactly as the
developer's own `test_ssh_argument_injection_shape_rejected_before_any_subprocess`
does, with a `${IFS}`-encoded `touch` marker:
```
$ mkdir -p /tmp/npfu-bypass-test/projects
$ sudo env RUN_USER=$(id -un) PROJECTS_DIR=/tmp/npfu-bypass-test/projects \
    bash scripts/new-project-from-url.sh \
    'ssh://user@-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker' 'bypasstest'
git clone failed:
Cloning into '/tmp/npfu-bypass-test/projects/bypasstest'...
hostname contains invalid characters
fatal: Could not read from remote repository.
```
Observed: the script's own `Unsupported URL: ...` rejection message never
appeared — the URL passed the script's own bash re-validation and `git
clone` was genuinely invoked (`Cloning into ...` printed) with the crafted
URL as a real argv token. The marker file (`/tmp/npfu-bypass-marker`) was
**not** created and the attempt failed, but only because installed git
(2.47.3) independently rejects this hostname shape ("hostname contains
invalid characters") — the exact same "protected by git's own upstream
hardening, not by this codebase's allowlist" gap the prior round's Finding
1 already flagged, just reachable through a slightly different string
shape the fix didn't anticipate.

**Repro 2 — scp-like shorthand, leading `-` after the `:` path separator**
(the specific variant the assigning prompt suggested trying).
`_CLONE_URL_SCP_RE`'s negative lookahead only guards the character right
after `@` (the host); nothing constrains the character right after `:`
(the path/command component git hands to the remote transport):
```
$ python3 -c "
import re
r = re.compile(r'^[A-Za-z0-9_.-]+@(?!-)[A-Za-z0-9_.-]+:\S.*\$')
print(bool(r.match('user@127.0.0.1:-oProxyCommand=id')))"
True   # ACCEPTED -- should be rejected
```
Real `sudo` run of the script:
```
$ sudo env RUN_USER=$(id -un) PROJECTS_DIR=/tmp/npfu-bypass-test/projects \
    bash scripts/new-project-from-url.sh \
    'user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker3' 'scptest'
git clone failed:
Cloning into '/tmp/npfu-bypass-test/projects/scptest'...
fatal: strange pathname '-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker3' blocked
```
Same shape: the script's own bash re-validation never fires (`Unsupported
URL:` never printed), `git clone` is genuinely invoked, and only git's own
separate "strange pathname blocked" hardening (a different check than the
hostname one) stops it. Marker file not created — safe on this sandbox's
git, not safe by construction.

**Neither repro is covered by any test.** Grepped both test files: the
developer's new adversarial cases are `ssh://-oProxyCommand=id` (no `user@`
prefix) and `user@-oProxyCommand=id:path` (leading `-` right after `@`,
not after `:`) — the exact two shapes above (`user@` hiding the malicious
host after `://`, and a malicious path immediately after `:`) are absent
from both `tests/test_clone.py::ValidateCloneUrlTests` and
`tests/test_new_project_from_url.py::ArgumentValidationTests`.

This means acceptance criterion 4 ("a URL shaped like an argument-injection
attempt is rejected — never reaches `git clone` as an argv token") is still
not fully met: both repros above **do** reach `git clone` as a real argv
token via a genuine subprocess call, contradicting the criterion's own
"never reaches `git clone` as an argv token" wording, exactly as the prior
round's Finding 1 already established for the un-prefixed case. The fix is
a real, partial improvement (the literal case it targeted is closed) but
does not close the vulnerability class it was dispatched to close, on an
older/differently-hardened git this remains exploitable exactly as before.

### Regression check (the parts of the fix that do work)
- Every legitimate URL shape still accepted: `tests/test_clone.py` (44
  tests) + `tests/test_new_project_from_url.py` (18 tests) = 62/62 pass,
  rerun directly: `python3 -m unittest tests.test_clone
  tests.test_new_project_from_url -v` → `Ran 62 tests` / `OK`.
- Full suite: `python3 -m unittest discover -s tests` → `Ran 994 tests` /
  `OK`, 0 failures, run cleanly and in isolation. (An earlier run of mine
  showed one failure in `test_teams_headless.RealTmuxHeadlessTests.
  test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask`
  — traced this down before treating it as a regression: it was caused by
  my own accidental overlap of two full-suite invocations racing on the
  same PID-derived tmux session name (`team-sessionrace-p<pid>` collisions
  visible in the log). A clean, single, non-concurrent rerun passed
  994/994 with no failures. Not a regression in this diff, self-inflicted
  test-harness noise on my part, confirmed and ruled out before writing
  this up.)
- Node: `node tests/test_clone_frontend.js` → 8/8 pass, unaffected (this
  fix touches no frontend code).
- WCAG should-fix confirmed correctly left untouched, per the explicit
  instruction not to touch it this round: `docs/design.md` lines 512-516
  (the button/error/placeholder/status contrast claims) are byte-identical
  to the prior round.

### Doc accuracy note (not a blocker on its own, folding into this write-up per the assigning prompt's ask)
`docs/spec.md`'s "Open questions" section (lines ~636-643) still states:
"...the allowlist regex alone still fully closes the argument-injection
shape described above" — already disproven by the first review round, and
now doubly so given the two additional bypasses found this round. Since
this cycle is already going back to the developer for Finding 1's
re-fix, recommend correcting this sentence in the same pass rather than
leaving a durable spec doc with a demonstrably false safety claim — not a
separate blocking item, just worth folding into the same commit that fixes
Finding 1 properly.

### Verdict on the must-fix: **NOT resolved — still a must-fix**
The developer's regex tightening is a genuine, real improvement (the
literal `ssh://-oProxyCommand=...`/`user@-oProxyCommand=...:path` shapes
from the prior round's own repro are now correctly rejected before any
subprocess), but it is not a complete fix for the vulnerability class the
prior round's Finding 1 identified. A `user@` prefix in front of a
malicious `ssh://` host, and a malicious path immediately after the `:` in
the scp-like shorthand, both still reach a real `git clone` subprocess
invocation with the crafted string as an argv token, protected only by
installed git's own (version-dependent) downstream hardening — exactly the
residual-risk shape the prior round asked to be closed at this codebase's
own validation layer instead. Recommended fix direction for the next
round: parse out the actual host component (split on the last unescaped
`@` for `ssh://`, and require the path segment after `:` in the scp-like
form to also reject a leading `-`) rather than only guarding the character
immediately following the scheme/`@`, since both accepted grammars allow
an optional `user@`/`:path` segment between the checked character and the
component that actually matters to `ssh`/`git`.

## Overall verdict (re-review): **Blocked**

---

## Re-review: Backlog item 16 — third round, component-isolation fix-and-reapprove

### Scope
Covers the developer's disclosed "Second post-review fix" in
`docs/implementation.md`: abandonment of lookahead-anchored regexes entirely
in favor of real component isolation — `urllib.parse.urlsplit(url).hostname`
for `scheme://` URLs, manual "split on `@`, then `:`" for scp-like
shorthand, both validated through a new `_clone_url_host_is_safe()` helper
(`app/app.py:702-810`), mirrored in bash via parameter expansion in
`scripts/new-project-from-url.sh:22-106`. Per the assigning prompt's
explicit instruction, this review does **not** re-run or re-trust the
developer's own repro/revert-and-fail claims or either of my own prior two
rounds' adversarial lists — every URL below is independently constructed for
this round, and every accept/reject claim is backed by either a real `sudo`
invocation of the actual privileged script or a direct call into the live
`app._validate_clone_url()` in this session (not simulated).

### 1. Both prior rounds' bypass URLs — now genuinely blocked, verified live
Ran the exact round-1 (`ssh://-oProxyCommand=id`, `user@-oProxyCommand=id:path`)
and round-2 (`ssh://user@-oProxyCommand=touch${IFS}/tmp/x`,
`user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/x`) URLs against both the live
Python validator and a real `sudo` invocation of
`scripts/new-project-from-url.sh`. All four are rejected at both layers
(`_validate_clone_url()` returns the error string; the script prints
`Unsupported URL: ...` and exits 1 before any `git clone` invocation, `rc=1`,
no marker file, no `PROJECTS_DIR/<name>` directory left).

### 2. Double-`@` semantics — verified against git's *actual* behavior, not assumed
Before trusting the "split on first/last `@`" reasoning in either the code
comments or `docs/implementation.md`, I determined git's own real semantics
empirically (not from the man page, which is silent on double-`@`): using
`GIT_SSH_COMMAND` pointed at a logging stub, I confirmed `git` hands the
**entire** `[user@]host[:port]` (or scp-form `[user@]host`) string to `ssh`
as one unsplit argv token, and separately confirmed via `ssh -G` that
OpenSSH 10.0 itself resolves username/hostname by splitting on the **last**
unescaped `@` (`real@decoy@127.0.0.1` → `user=real@decoy`,
`hostname=127.0.0.1`) — i.e. an earlier `@` is swallowed into the username,
not the host boundary.
- **Scheme form (`ssh://`)**: `urllib.parse.urlsplit(url).hostname` also
  resolves on the *last* `@` (`ssh://real@decoy@-oProxyCommand=pwn/repo` →
  `hostname='-oproxycommand=pwn'`, correctly flagged unsafe) — this matches
  git/ssh's real behavior exactly, confirmed both via direct Python call and
  a real `sudo` run (`ssh://real@decoy@-oProxyCommand=pwn/repo` → rejected,
  `Unsupported URL`, no clone attempt) and a real-`sudo` run of the
  legitimate counterpart (`ssh://real@decoy@127.0.0.1/doesnotexist` →
  correctly *accepted*, real `Cloning into...` attempted, fails only on
  "repository not found", exactly as a genuine double-`@` username should
  behave).
- **Scp-like form**: the code splits on the *first* `@`, not the last —
  I verified this is still safe, not a bypass, but for a subtler reason than
  "correctly matches git": the app's `host` extraction is
  "everything between the first `@` and the following `:`", which
  necessarily *includes* any additional `@` characters when more than one is
  present, and `@` is not in `_SAFE_HOST_RE`'s allowed charset — so a
  malicious real-host-after-the-last-`@` is always caught (by the `@`
  appearing where it shouldn't, not because the split boundary was correct).
  Real `sudo` run confirms: `real@decoy@-oProxyCommand=pwn:repo` → rejected,
  `Unsupported URL`, no clone attempt. One side effect, confirmed
  intentional and non-security: this also **over-rejects** a *legitimate*
  double-`@` scp-form username (`real@decoy@127.0.0.1:doesnotexist`, where
  ssh would resolve `user=real@decoy`, `host=127.0.0.1`) — verified via real
  `sudo` run: rejected, even though this exact shape is accepted in scheme
  form. A false negative on an unusual-but-legitimate URL, not a security
  gap — not blocking.

### 3. IPv6 bracketed forms, with and without a leading `-` inside the brackets
- `ssh://[::1]/repo`, `ssh://[::1]:22/repo`, `ssh://user@[::1]:22/repo` — all
  correctly **accepted** (`ipaddress.ip_address("::1")` succeeds); verified
  the accepted case doesn't collaterally break anything by real `sudo` run
  of `ssh://user@[::1]:22/repo` — proceeds to a genuine SSH connection
  attempt (`Permission denied (publickey,password)` from real localhost
  sshd), never `Unsupported URL`.
- `ssh://[-oProxyCommand=pwn]/repo` (leading `-` inside the brackets) —
  correctly **rejected** at both layers (`ipaddress.ip_address()` raises
  `ValueError` on a non-IPv6 bracket payload); confirmed via a real `sudo`
  run: `Unsupported URL`, no clone attempt.

### 4. Trailing `:` / empty-component edge cases
| Case | `_validate_clone_url()` | Real `sudo` script | Assessment |
|---|---|---|---|
| `user@host:` (scp, no path) | reject | reject (`Unsupported URL`) | correct — `path` empty fails the check |
| `@host:path` (scp, empty user) | reject | reject | correct — doesn't match `_CLONE_URL_SCP_RE`'s `[A-Za-z0-9_.-]+` (needs ≥1 char) |
| `user@:path` (scp, empty host) | reject | (not separately re-run — same code path as `@host:path`, already exercised) | correct — `_clone_url_host_is_safe("")` is `False` |
| `ssh://@host/repo` (scheme, empty user) | **accept** | accepted, real connection attempted, `Permission denied` | benign — empty userinfo is not attacker-advantageous, `hostname='host'` is genuinely safe |
| `ssh://user@:22/repo` (scheme, empty host) | reject | reject | correct — `urlsplit().hostname` is `None`/empty, `_clone_url_host_is_safe(None)` is `False` |
| `ssh://host:/repo` (scheme, trailing `:`, empty port) | **accept** | (not separately re-run; `urlsplit().port` is simply `None`, never checked or misused) | benign — no injection surface, port is simply absent |

### 5. Git-vs-validator "what counts as scp-remote-syntax at all" mismatch — checked, confirmed safe
Per `git-clone(1)`'s own GIT URLS section, scp-like syntax is "only
recognized if there are no slashes before the first colon" — a URL like
`user@evil/proxy:something` is *not* treated as a remote by git at all, it
falls back to being parsed as a **local filesystem path** (verified
directly: no `SSH_ARGS` were ever logged by a `GIT_SSH_COMMAND` spy for this
exact string, confirming ssh was never invoked). This is exactly the
generic risk class the assigning prompt named ("our validator's idea of the
host differs from git's own idea of the host") — but `_CLONE_URL_SCP_RE`
has no equivalent "no slash before the colon" carve-out, so I checked
whether this divergence is exploitable. It is not: any `/` occurring in what
the app extracts as `host` (the substring between the first `@` and the
first `:`) is rejected outright, because `/` is not in `_SAFE_HOST_RE`'s
allowed charset — so any string shaped this way is rejected by the app's
own host-safety check regardless of git's differing scp-vs-local
classification. Confirmed via a real `sudo` run:
`user@evil/proxy:something` → rejected, `Unsupported URL`, no clone
attempt (and, separately, no local-path clone was possible even had it
passed, since nothing here is exploitable either way).

### 6. New finding — should-fix, not a must-fix: the `scheme://` port component is silently unvalidated at both layers
Neither `_validate_clone_url()` nor `_host_is_safe()`/the bash port-strip
(`${hostport%:*}`) ever inspects what follows a `:` after the host in
`scheme://host:port/path` form when that content isn't a clean numeric port.
`urllib.parse.urlsplit(url).hostname` is accessed but `.port` never is, so
an invalid port string never even raises; the bash mirror discards
everything after the last `:` via `${hostport%:*}` with no check on what was
discarded.
- Constructed `ssh://127.0.0.1:-oProxyCommand=touch${IFS}/tmp/marker/repo`
  and `ssh://real@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/marker2/repo`.
  Both are **accepted** by `_validate_clone_url()` (host resolves to
  `127.0.0.1`, which is safe; the malformed port is never examined) and by
  the bash script's own validation (same reasoning). Ran both for real via
  `sudo`: in both cases the script proceeds past validation and genuinely
  invokes `git clone` — `Cloning into ...` is printed, no `Unsupported URL`.
  **However**, in this sandbox (git 2.47.3, OpenSSH 10.0), git does *not*
  split a non-numeric "port" into a separate argv token the way it does for
  a real numeric port (confirmed by comparing the `GIT_SSH_COMMAND` spy log
  for a numeric-port URL, which shows a separate `-p <port>` argument, vs.
  the non-numeric case, which shows the whole `host:garbage` string passed
  as one combined token) — and real OpenSSH itself then rejects that
  combined string outright with `hostname contains invalid characters`
  before attempting any connection (confirmed directly:
  `ssh -o BatchMode=yes '127.0.0.1:-oProxyCommand=touch /tmp/x' echo hi` →
  same error, same non-creation of the marker). No marker file was created
  in either real `sudo` run; no directory was left behind.
- I assessed this is a **materially different (and safer) case than the two
  must-fix rounds already closed**, not a re-opening of the same
  vulnerability class: in both prior rounds, the malicious payload became
  the *first character* of the exact argv token handed to `ssh`, which is
  the literal CVE-2017-1000117 mechanism (`getopt`-style parsing treats a
  leading `-` on a positional argument as a flag). Here, the malicious
  content is *embedded mid-token* (`host:payload`), never the leading
  character of the combined destination string handed to `ssh` — so even on
  an ssh/git combination *without* OpenSSH's "hostname contains invalid
  characters" sanity check, there is no mechanism by which this string would
  be re-split into a discrete flag argument; `getopt`-style option detection
  only ever inspects the first character of a token. I could not construct
  a plausible exploitation path for this specific case, unlike the two prior
  rounds where the mechanism was concrete and version-dependent only on
  *whether* the CVE-2017-1000117 patch was present, not on a difference in
  mechanism.
- Recommend closing it anyway on defense-in-depth grounds consistent with
  this whole must-fix arc's own stated discipline (validate the real
  component, don't lean on git/ssh's own hardening) — e.g. also access
  `urlsplit(url).port` inside a `try/except ValueError` and reject on
  failure, and have the bash mirror validate the port substring
  (`^[0-9]+$`) rather than discarding it unchecked. Not blocking this round.

### Regression check
- `python3 -m unittest tests.test_clone tests.test_new_project_from_url -v`
  → **`Ran 80 tests` / `OK`** (62 legitimate/adversarial-regression cases +
  18 this-round adversarial cases, exactly matching
  `docs/implementation.md`'s own count), run directly in this session, not
  taken on trust.
- `python3 -m unittest discover -s tests` → **`Ran 1012 tests` / `OK`**, 0
  failures, run to completion in this session (145.6s). (Noisy interleaved
  stdout from real-tmux/team-lifecycle tests — JSON transcript dumps,
  `duplicate session: team-sessionrace-p<pid>` lines — is expected
  pre-existing test-harness chatter from those suites, not a failure
  indicator; confirmed the actual summary line reads `Ran 1012 tests` /
  `OK` with no `FAILED`/`ERROR:` lines anywhere in the log.)
- `node tests/test_clone_frontend.js` → **8/8 PASS**, run directly, unrelated
  to this round's Python/bash-only diff.
- `git diff --stat` / `git diff app/app.py | grep '^@@'` confirms this
  round's diff is scoped to `app/app.py` (imports, the clone-URL validation
  block, `_clone_url_host_is_safe()`), `scripts/new-project-from-url.sh`,
  and the docs pair every cycle updates — no unrelated changes, no scope
  creep.

### Spec / criterion coverage
Acceptance criterion 4 ("a URL shaped like an argument-injection attempt is
rejected — never reaches `git clone` as an argv token") is now genuinely
met for every adversarial shape I could construct that maps to the actual
CVE-2017-1000117 mechanism (a crafted string becoming the *first character*
of an argv token handed to `ssh`), across both grammars, including double-`@`
variants verified against git's own real (not assumed) host-resolution
semantics, and IPv6 bracketed forms. The one residual gap found (§6 above)
does not reach that mechanism and is assessed should-fix, not must-fix — see
reasoning above.

### Verdict on the must-fix: **RESOLVED**
Unlike the previous two rounds, I was not able to construct an adversarial
URL, in either accepted grammar, that reaches `git clone` as an argv token
whose leading character is attacker-controlled and equal to `-`. Every prior
bypass shape (this round's own repro attempts included) is now rejected
before any subprocess is spawned, confirmed via real `sudo` runs against the
actual privileged script, not simulation. The bash mirror in
`scripts/new-project-from-url.sh` reaches the same accept/reject decision as
the Python validator for every case I tried (including the one case where
both are more conservative than strictly necessary — double-`@` scp-form —
which is a shared, not divergent, false negative).

## Overall verdict (third re-review): **Approve**

This closes the fix-and-reapprove cycle for backlog item 16. The one new
should-fix (§6, unvalidated `scheme://` port component) and the two
carried-forward non-blocking items from the first round (WCAG button
contrast; the informational `docs/spec.md` accuracy note, already corrected
per the developer's second post-review fix) are recorded as follow-ups, not
blockers — none of them reopen the argument-injection vulnerability class
this must-fix arc was dispatched to close.

The testing pass fails on the same must-fix acceptance criterion (#4,
argument-injection rejection "before any subprocess") the prior round
flagged: my own independently-constructed adversarial URLs — one `ssh://`
variant and one scp-like variant, neither identical to the developer's own
test list, per the assigning prompt's explicit instruction — both pass the
tightened regex at both the Python and bash layers and reach a genuine
`git clone` subprocess invocation via a real `sudo` run of the privileged
script, stopped only by installed git's own downstream hardening. Per this
role's process, a failed testing pass routes straight back to the
developer without a review pass — the should-fix (WCAG contrast, correctly
left untouched this round) and the docs/spec.md doc-accuracy note above
remain open follow-ups for whenever this item is eventually approved, not
addressed in this blocked round. Full regression suite (994 Python + 8
clone-frontend Node tests) is otherwise green, and every legitimate URL
shape this feature needs to accept still works correctly — only the
injection-rejection criterion itself remains unmet.
