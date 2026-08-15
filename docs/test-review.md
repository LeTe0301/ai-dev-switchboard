# Test & Review: Proxmox E2E round-2 fixes (backlog items 28, 29, 33)

## Scope
Three independent bugfixes from `docs/spec.md`: (1) `app/teams.py`'s
`rundir` permission wall (`0o711` → `0o733`, both call sites) plus new
tmux-`new-session` stderr surfacing in `agent_run()` and
`_run_run_user_command()`; (2) `app/taiga_board.py`'s `DEFAULT_CONFIG_PATH`
now resolved against `RUN_USER`'s home explicitly instead of
`os.path.expanduser("~/...")`; (3) `/team/interject`'s 400 error string
corrected from "message" to "text". Item 28 is explicitly the second of two
bugs that together made multi-agent teams non-functional on a fresh
install (item 27 shipped in PR #27), so it got the deepest independent
verification per the dispatch instructions.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Both `rundir` chmod call sites in `app/teams.py` changed `0o711`→`0o733`; zero remaining `0o711` at those two sites | Automated: `grep -n "0o711\|0o733" app/teams.py`, manually classified every hit | pass | Lines 1095, 3473 now `0o733`. Two other `0o711` hits remain: line 1106 is a **comment** (now stale — see Findings), line 1917 is `os.chmod(TEAM_STATE_DIR, ...)`, an unrelated directory, not one of the two sites this fix targets |
| 2 | `agent_run()`'s success path (tmux `new-session` rc=0) is byte-for-byte unchanged | Manual read of full function (lines 1085-1179) + existing `RealTmuxHeadlessTests` (real tmux, real subprocesses) rerun | pass | Code: rc==0 falls through unchanged to `_run_headless_session(...)`. Test: `test_success_stream_end_to_end`, `test_shape_crash_line_through_the_real_agent_run_path_does_not_raise`, `test_ordinary_20kb_plain_text_prompt_actually_runs_arg_mode`, etc. all still pass against real tmux |
| 3 | `agent_run()`'s new failure path (tmux `new-session` rc≠0) actually gets exercised and returns early with the new stderr-including message, not a fall-through | Automated, new test, real tmux (no mock): forced a genuine `tmux new-session` failure via a pre-occupied duplicate session name, then reverted the fix and re-ran to confirm it fails pre-fix | pass | New test `tests/test_teams_headless.py::RealTmuxHeadlessTests::test_tmux_new_session_nonzero_returncode_surfaces_stderr_not_generic_vanished`. Post-fix: `ok`. Pre-fix (`git stash push -- app/teams.py`): **fails** — assertion `'failed to start headless session:' not found in 'headless session failed to start'`, i.e. pre-fix it silently degrades to the generic vanished-session message exactly as the bug report describes |
| 4 | `_run_run_user_command()`'s success path unchanged | Manual read of full function (lines 3441-3524) + existing `RunRunUserCommandRealTmuxTests.test_success` rerun | pass | Code: rc==0 falls through unchanged to the rc-polling loop. Test passes |
| 5 | `_run_run_user_command()`'s new failure path actually gets exercised and returns early with the new stderr-including message | Automated, new test, real tmux (no mock): pinned `secrets.token_hex`/`time.time` to make `op_id` predictable, pre-occupied that exact session name, forced a real duplicate-session tmux failure; reverted and re-ran to confirm pre-fix failure | pass | New test `tests/test_teams_lifecycle.py::RunRunUserCommandRealTmuxTests::test_tmux_new_session_nonzero_returncode_surfaces_stderr_not_generic_vanished`. Post-fix: `ok`. Pre-fix: **fails** — `'failed to start command:' not found in 'command session ended unexpectedly'` (and took the full ~30s vanished-fallback poll/timeout path, matching the bug report) |
| 6 | The `chmod(rundir, ...)` site the developer touched (spec said "inside `_run_headless_session()`", code has it inside `agent_run()`) is still the *correct* rundir — the one `_run_headless_session()`'s own redirect-and-background script actually writes into | Manual trace, not developer self-assessment: `agent_run()` computes `rundir` (line 1086), builds `script_path = rundir/run.sh` via `_build_script()` (which literally builds `... >out_path 2>err_path & echo $! >pid_path; wait $!; echo $? >rc_path`, all paths under `rundir`), spawns `bash -l script_path` as `RUN_USER` via `sudo -u`/tmux, then calls `_run_headless_session()` with those same `out_path`/`err_path`/`pid_path`/`rc_path` | pass | `_build_script()` docstring literally: "agent_run() writes the returned string to RUNDIR/run.sh; see that function" — confirms it's the same directory regardless of which function's source contains the literal `os.chmod` line |
| 7 | `taiga_board.py`'s `RUN_USER` resolution genuinely independent — no `import app`/`from app import ...` | `grep -n "^import\|^from" app/taiga_board.py` | pass | Only `json, os, stat, sys, urllib.*` imported; no `app` import anywhere in the file |
| 8 | `RUN_USER` default matches `app.py:69` exactly (`"dev"`) | Read `app/app.py:69` and `app/taiga_board.py:44` side by side | pass | Both: `os.environ.get("RUN_USER", "dev")`, byte-for-byte identical |
| 9 | Spec's own acceptance command, rerun myself (not developer's reported output) | `RUN_USER=dev python3 -c "import sys; sys.path.insert(0, 'app'); import taiga_board; print(taiga_board.DEFAULT_CONFIG_PATH)"` | pass | Printed `/home/dev/.config/ai-dev-switchboard/taiga-push.env`, matching spec exactly. Also reran with `RUN_USER` unset — same output (default `"dev"` applies) |
| 10 | No other reference to the old `expanduser`-based path anywhere in `taiga_board.py` or its callers (`app/teams.py`'s `board_read`/`board_write`) | `grep -rn "expanduser" app/ scripts/`; `grep -n "board_read\|board_write\|taiga_board\." app/teams.py` | pass | Only remaining `expanduser` hit is `scripts/taiga_push_spec.py` (explicit spec non-goal — correctly `~`-relative, runs as `RUN_USER`). All `teams.py` call sites (`board_read`, `board_write`, `resolve_board_write`) go through `taiga_board.resolve_session()`/`taiga_board.get_userstory()` etc. with no separately-computed path of their own |
| 11 | `/team/interject`'s served error string now says "text" not "message" | Read `app/app.py:6468-6478` directly (post-diff source, not the diff hunk alone) | pass | Line 6475: `f"text must be non-empty and at most "` |
| 12 | No test anywhere in `tests/` still asserts the old "message must be non-empty" wording | `grep -rn "message must be non-empty" tests/ app/ scripts/` (whole tree, not just the two files the developer checked) | pass | Zero hits anywhere in the repo |
| 13 | Full existing suite (regression) | `python3 -m unittest discover -s tests` (run myself, full, twice — once before adding new tests, once after) | pass | Before: `Ran 1198 tests ... OK`. After adding the 2 new tests above: `Ran 1200 tests ... OK`. No failures, no errors |
| 14 | Compile check | `python3 -m py_compile app/teams.py app/taiga_board.py app/app.py tests/test_teams_headless.py tests/test_teams_lifecycle.py` | pass | Clean, no output |

## Regression check
Full suite run twice by me directly (not reused from the developer's
report): `python3 -m unittest discover -s tests` → `Ran 1198 tests ... OK`
(pre-existing baseline, matches developer's reported count) and, after
adding the two new regression tests for fix 1's failure branches, `Ran 1200
tests ... OK`. Also reran the two most relevant files directly
(`tests.test_teams_headless tests.test_team_routes`) → `Ran 217 tests ...
OK`, matching the developer's own focused re-run count. No regressions
anywhere in the suite.

## Defects found
None.

---

## Spec coverage
All three fixes' acceptance criteria are implemented and independently
verified against the actual diff (not developer self-report):

- **Fix 1 (item 28)**: both `0o711`→`0o733` sites confirmed by direct grep
  and classification of every remaining `0o711` hit in the file (cases 1).
  Both functions' success paths confirmed unchanged and failure paths
  confirmed to actually return early with the new stderr-threaded message
  — via new automated tests that force a *real* tmux `new-session` failure
  (duplicate session name, no mocking) and are confirmed to genuinely
  exercise the new branch by reverting the fix and watching them fail
  pre-fix (cases 2-5). The spec-vs-code function-labeling mismatch the
  developer flagged is confirmed harmless by tracing `rundir`'s actual
  usage through `_build_script()`, independent of the developer's own
  self-assessment (case 6).
- **Fix 2 (item 29)**: independent-resolution (no `app` import), exact
  default-value match with `app.py:69`, and the spec's own acceptance
  command rerun directly by me (not trusting the developer's reported
  output) all confirmed (cases 7-9). Confirmed no stale reference to the
  old per-process `expanduser` path remains anywhere `taiga_board.py` or
  its `teams.py` callers touch (case 10).
- **Fix 3 (item 33)**: served string confirmed from the actual post-diff
  source, and a whole-tree grep (not just the two files the developer
  checked) confirms no test anywhere still asserts the old wording (cases
  11-12).

No acceptance criterion in `docs/spec.md` is unimplemented or untested.

## Findings (most severe first)

### 1. Stale comment references the old `0o711` value — nit
- File: `app/teams.py:1106`
- Issue: the comment reads `"...same reasoning as rundir's own explicit
  0o711."` — this is now inaccurate; `rundir`'s own chmod is `0o733` as of
  this fix (line 1095). The comment's *reasoning* (explicit chmod rather
  than relying on ambient umask) is still correct, only the cited literal
  value is stale.
- Failure scenario: none functionally — this is a doc-drift nit, not a
  behavior bug. Worth a one-word fix (`0o711` → `0o733`) in a follow-up so
  a future reader isn't misled about `rundir`'s actual current mode.

### 2. Empty-stderr edge case produces a slightly bare error message — nit
- File: `app/teams.py:1162` and `app/teams.py:3496`
- Issue: `error=f"failed to start headless session: {tmux_result.stderr.strip()}"`
  (and the `_run_run_user_command()` equivalent) — if `tmux` exits non-zero
  with empty stderr (uncommon but not impossible, e.g. some `SIGKILL`-via-
  ulimit or resource-exhaustion paths that don't reach tmux's own
  error-printing code), the message ends with a trailing `": "` and no
  detail, e.g. `"failed to start headless session: "`.
- Failure scenario: purely cosmetic (a slightly bare, not misleading,
  message) — the `ok=False`/early-return behavior itself is correct
  either way. Not worth blocking on; a `or "(no stderr)"` fallback would
  be a small future polish, not required.

## Follow-ups (non-blocking)
- Fix Finding 1 (stale `0o711` comment reference) whenever this file is
  next touched.
- Optionally handle the empty-stderr edge case (Finding 2) with a fallback
  string, low priority.

## Overall verdict
**Approve.** All three fixes match `docs/spec.md`'s exact before/after
code, both fix-1 error-handling paths were independently confirmed correct
by tracing the full functions (not just the diff hunks) and by new,
real-tmux (no-mock) regression tests that were verified to genuinely
exercise the new failure branches via a revert-and-watch-it-fail check.
Fix 2's independent-resolution and default-value claims were verified from
source, and its acceptance command was rerun directly rather than trusted
from the developer's report. Fix 3's served string and the absence of any
stale test assertion were confirmed via a whole-tree grep. Full existing
suite reruns clean (1198 pre-existing, 1200 with the two new regression
tests added by this review pass). Two nits found, neither blocking.
