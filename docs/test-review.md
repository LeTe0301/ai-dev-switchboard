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
