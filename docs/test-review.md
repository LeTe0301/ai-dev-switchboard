# Test & Review: test-infrastructure isolation (BACKLOG item 9)

## Scope
Covers `docs/spec.md`'s 6 acceptance criteria (committed `5503d24`) for the
uncommitted, test-only diff on `backlog/test-isolation-9`:
`tests/test_deploy_target.py` (Part A: orphan detection + tearDown backstop),
`tests/test_teams_lifecycle.py` and `tests/test_team_routes.py` (Part B:
per-process tmux session scoping). Reviewed against `docs/implementation.md`'s
own claims, re-derived independently (own concurrent-process run, own planted
foreign sessions, own revert-and-rewatch-it-fail checks) rather than trusted
from the developer's report.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Orphan `/home/deploy` (no `deploy` user, directory present) causes `InstallScriptDeployTargetBlockTests` to be **skipped**, not run against stale state | Automated (`DeployTargetOrphanDetectionTests`, ran as-is) + my own revert check: reverted just `setUp`'s guard back to "user exists only", reran the same test in isolation | pass (as shipped); **as-shipped test correctly fails when the fix is reverted**, proving it's discriminating | `python3 -m unittest tests.test_deploy_target.DeployTargetOrphanDetectionTests -v` → OK with fix in place; same command with `setUp` reverted → `FAILED (failures=1)`, `AssertionError: 0 != 1: InstallScriptDeployTargetBlockTests must be skipped...` |
| 2 | `tearDown`'s backstop actually removes `/home/deploy` after a forced mid-test failure, so the *next* run's `setUp` sees clean state | Automated (`DeployTargetTearDownBackstopTests`, ran as-is) + my own revert check: reverted only the `sudo rm -rf /home/deploy` backstop line (kept `userdel -r deploy`), reran the same test in isolation | **FAIL — see Defect 1** | See Defect 1 below |
| 3 | Two full runs of `test_team_routes.py`/`test_teams_lifecycle.py`'s real-tmux classes launched **concurrently** (two separate processes), both pass, zero session-name collisions | Automated — launched two genuinely separate background `python3 -m unittest` processes myself (not reusing the developer's report) | pass | Both processes: `Ran 136 tests ... OK`, 18.5s/18.7s respectively; `tmux list-sessions` afterward showed zero leftover `team-*`/`switchboard-*` sessions |
| 4 | No test in either file still creates a session literally named `team-demo`/`team-proj`/`team-atomicdemo`/`team-failchain`/`team-sessionrace`/`team-clidemo` | Automated grep of both files post-fix, done independently (not trusting the diff description) | pass | `grep -n '"team-demo"\|"team-proj"\|"team-atomicdemo"\|"team-failchain"\|"team-sessionrace"\|"team-clidemo"'` against both files: zero matches. Broader sweep of every remaining bare project-name literal (`"demo"`, `"proj"`, etc.) confirmed the only unscoped survivors are in `NewStateAdditiveFieldsTests`/`SweepDeadTeamsPureTests`, which are pure/mocked (`tmux_has` monkeypatched, no real tmux touched) — correctly out of the criterion's own scope ("not synthetic/mocked ones") |
| 5 | `switchboard-worktree-op-` sweep in `test_teams_lifecycle.py`'s `tearDown` only targets this process's own scoped prefix — a concurrent process's own sessions survive | Automated — planted my own foreign sessions (a superset of the developer's own proof: `switchboard-worktree-op-8888888888-cafebabe0001`, `team-otherproj-p8888888`, and an additional `switchboard-headless-p8888888-somehow`), ran the full lifecycle+routes suite, confirmed survival | pass | All 3 planted sessions confirmed alive via `tmux list-sessions` both before and after `python3 -m unittest tests.test_teams_lifecycle tests.test_team_routes -q` (`Ran 136 tests ... OK`) |
| 6 | Full existing suite passes with no regression (Python 790 baseline + 2 new = 792, Node 84) | Automated, ran myself | pass | `python3 -m unittest discover -s tests -v` → `Ran 792 tests in 137.553s`, `OK`; all 4 Node suites: 15/15, 8/8, 52/52, 9/9 = 84/84 |

Additional verification performed (not a spec bullet, but part of the dispatch):
- Read `app/teams.py:2931-3052` (`_run_run_user_command()`) directly. Confirmed
  the entire body from session creation through completion sits inside a
  single `try`/`finally` (finally at lines 3049-3052: `shutil.rmtree(rundir,
  ignore_errors=True)` then `if tmux_has(session): kill-session`) — this
  really is unconditional self-cleanup independent of success/failure/
  timeout, so dropping `test_teams_lifecycle.py`'s own `"switchboard-"` sweep
  branch (rather than scoping it) is a correct, verified call, not merely an
  asserted one.
- Confirmed `PrivilegedEndToEndTests` (fixed `TEST_USER = "aidswbtest"`,
  `tests/test_deploy_target.py:762+`) is untouched by this diff, and re-read
  `docs/spec.md`'s Background/Non-goals/acceptance-criteria sections myself:
  the concurrency acceptance criterion (bullet 3) names only
  `test_team_routes.py`/`test_teams_lifecycle.py`; Part A's Background and
  acceptance criteria (bullets 1-2) are scoped specifically to
  `InstallScriptDeployTargetBlockTests`'s `deploy`/`/home/deploy` fixture.
  Nothing in scope asks this cycle to make `PrivilegedEndToEndTests`
  concurrency-safe. The developer's "known limitation, out of scope" framing
  is accurate — not a spec gap being waved away.
- `tests/test_deploy_dispatch.py`, named in spec's Non-goals as sharing the
  same risk class but explicitly out of scope beyond confirming it's
  unaffected: `git diff --stat -- tests/test_deploy_dispatch.py` shows no
  changes, and it's part of the 792 that passed clean.

## Regression check
Full suite run by me: `python3 -m unittest discover -s tests -v` → 792/792,
`OK`. All 4 Node suites run individually → 84/84 total. No regressions
outside the two files under test.

## Defects found

### Defect 1: `DeployTargetTearDownBackstopTests` does not actually prove the backstop does anything — it passes identically with the backstop line removed
- **File**: `tests/test_deploy_target.py`, new class `DeployTargetTearDownBackstopTests`
  (test method `test_teardown_backstop_removes_home_deploy_after_a_forced_mid_test_failure`),
  exercising the `tearDown()` backstop added at line ~444
  (`subprocess.run(["sudo", "rm", "-rf", "/home/deploy"])`).
- **Repro** (exactly the "prove the test is real" technique this project's
  own review history uses, applied here for real):
  1. With the diff as-shipped: `python3 -m unittest
     tests.test_deploy_target.DeployTargetTearDownBackstopTests -v` → `OK`.
  2. Temporarily removed *only* the new `sudo rm -rf /home/deploy` backstop
     line from `tearDown()` (kept everything else, including the existing
     `userdel -r deploy` immediately above it — i.e. reverted to
     pre-BACKLOG-item-9 `tearDown()` exactly).
  3. Re-ran the identical command: `python3 -m unittest
     tests.test_deploy_target.DeployTargetTearDownBackstopTests -v` →
     **still `OK`** (1/1 pass). `/home/deploy` and the `deploy` account were
     both confirmed gone afterward regardless.
  4. Restored the file to the original diff (`diff` against the pre-edit
     copy confirmed byte-identical).
- **Root cause**: the test's forced-failure body
  (`_ForcedFailureAfterFixtureCreated.test_forced_failure_after_fixture_created`)
  calls `self.run_block(...)`, asserts `r.returncode == 0` (i.e. the full
  `--with-deploy-target` block, including `useradd deploy`, completed
  successfully) *before* raising. By the time `tearDown()` runs, a fully,
  successfully provisioned `deploy` account genuinely exists. `sudo userdel
  -r deploy` (the pre-existing line, immediately above the new backstop)
  already removes `/home/deploy` as a normal side effect of `-r` whenever the
  account exists and removal succeeds — which it always does here, since
  nothing in the harness spawns a long-running process owned by `deploy`
  (`service="myapp.service"` is never actually started; `pkill -9 -u deploy`
  is a no-op) that could cause `userdel` to fail. So the assertion
  `self.assertFalse(os.path.exists("/home/deploy"))` is satisfied by the
  *pre-existing* `userdel -r` line alone, and the new backstop line
  contributes nothing observable in this specific scenario.
  The test's own docstring claim — "only `tearDown()`'s own unconditional
  backstop is what can remove it" — is factually false for the scenario it
  actually constructs.
- **Why this matters**: `docs/spec.md`'s acceptance criterion 2 ("`tearDown`'s
  backstop actually removes `/home/deploy` even when simulating a failure
  partway through the privileged test body... test this by forcing an
  exception after the fixture is created but before normal cleanup would
  run") is reported as verified in `docs/implementation.md` but is not
  actually exercised by any test in this diff: if a future edit silently
  broke or deleted the backstop line, this regression test would not catch
  it, as long as `userdel -r` kept working normally. The scenario the
  backstop genuinely protects against (per `docs/spec.md`'s own "Background"
  — an interrupted run where `userdel` either already ran with nothing to
  remove, or where a partially-provisioned `/home/deploy` exists with no
  matching account at all) is structurally different from — and not
  covered by — what this test constructs. `DeployTargetOrphanDetectionTests`
  (criterion 1) does cover the "directory with no account" shape for
  `setUp()`'s guard, but nothing in this diff isolates and proves the
  `tearDown()` backstop's own contribution.
- **Severity**: must-fix (uncovered/falsely-claimed acceptance criterion —
  the test needs to construct a scenario where the pre-existing `userdel -r`
  line would *not* already clean up `/home/deploy` on its own, e.g. forcing
  the failure before `useradd` ever completes so no `deploy` account exists
  for `userdel` to act on while `/home/deploy` is already on disk, or making
  `userdel` itself fail/no-op for the duration of the test).

## Overall verdict (first pass)
**Blocked.** Criteria 1, 3, 4, 5, 6 are independently verified and pass.
Criterion 2 (`tearDown` backstop) is not — the only test written for it does
not discriminate between the backstop being present or absent, so it does
not actually prove the acceptance criterion. Per process, the review pass
was not performed since the testing pass did not come back clean; routing
back to the developer to fix `DeployTargetTearDownBackstopTests` so it
constructs a scenario where `userdel -r deploy` alone would not already
remove `/home/deploy`, then re-verify with the same revert-and-rewatch-it-
fail check before resubmitting.

---

## Re-review (after developer's "Review fix" in `docs/implementation.md`)

### Re-verification of Defect 1's fix

Read the reworked `DeployTargetTearDownBackstopTests`
(`tests/test_deploy_target.py:699-762`) directly rather than trusting the
developer's own revert-and-fail proof in `docs/implementation.md`. The
class now:
- constructs `/home/deploy/.ssh/authorized_keys` directly (`sudo mkdir -p`
  + a stale key), the same shape `DeployTargetOrphanDetectionTests` already
  uses, and deliberately never runs `useradd`/`run_block()`;
- asserts `id deploy` returns non-zero (no account) before raising, so the
  scenario's shape is verified inline, not merely assumed;
- runs a real `_ForcedFailureWithNoDeployUser` subclass of
  `InstallScriptDeployTargetBlockTests` through `unittest`'s own
  `TestCase.run()`, which raises `RuntimeError` after constructing the
  fixture, then relies on the inherited, unmodified `tearDown()` running
  normally.

Own repro, performed independently in this session (host confirmed clean
before and after — `id deploy` → no such user, `/home/deploy` absent):
1. `python3 -m unittest
   tests.test_deploy_target.DeployTargetTearDownBackstopTests -v` (as
   shipped) → `OK` (1/1).
2. Backed up the file, then edited only the backstop line out of
   `InstallScriptDeployTargetBlockTests.tearDown()` (removed
   `subprocess.run(["sudo", "rm", "-rf", "/home/deploy"])`, line 444;
   left the pre-existing `userdel -r deploy` call in place, immediately
   above).
3. Re-ran the identical command → **genuine failure**:
   ```
   AssertionError: True is not false : tearDown's backstop must remove
   /home/deploy even when no 'deploy' user ever existed for userdel -r
   to clean it up as a side effect
   FAILED (failures=1)
   ```
   This is the same test, same command, only the production-of-test-fixture
   line removed — and it now fails with a message that names the backstop
   directly, unlike the prior round where the identical revert produced a
   silent pass. This is the discriminating result Defect 1 required.
4. Cleaned up the `/home/deploy` this proof run left behind (`sudo rm -rf
   /home/deploy` — the reverted `tearDown()` had no way to do this itself,
   by design of the experiment), restored the file from the backup
   (`diff` against the pre-edit copy: byte-identical), reran the same
   command → `OK` (1/1) again.

Defect 1 is resolved. Verdict: **fix confirmed independently, not just
trusted from the developer's report.**

### Scope of this round's diff
`git diff --stat` shows all three test files still modified (expected —
nothing has been committed yet on this branch; the whole cycle's diff
remains uncommitted pending this approval). Confirmed this fix round itself
only touched `tests/test_deploy_target.py`:
- File mtimes: `tests/test_team_routes.py` and `tests/test_teams_lifecycle.py`
  both last modified *before* this file's first review pass completed
  (`docs/test-review.md`'s own mtime), while `tests/test_deploy_target.py`'s
  mtime is later than both — consistent with only the deploy-target file
  being touched after the first review round.
- `git diff --stat` sizes for `test_team_routes.py` (306 lines) and
  `test_teams_lifecycle.py` (120 lines) match what was already verified in
  the first pass; no new edits to reconcile.
- Read the current `test_deploy_target.py` diff in full: the only
  substantive change from the first-pass version is the body of
  `DeployTargetTearDownBackstopTests` (renamed inner class
  `_ForcedFailureAfterFixtureCreated` → `_ForcedFailureWithNoDeployUser`,
  fixture construction swapped from `run_block()` to direct
  `mkdir`/`authorized_keys`). `InstallScriptDeployTargetBlockTests.setUp`/
  `tearDown` and `DeployTargetOrphanDetectionTests` are unchanged from what
  was already verified and passed in the first round.

### Full suite re-run (this session)
- `python3 -m unittest tests.test_deploy_target -v` → `Ran 32 tests ... OK`.
- `python3 -m unittest discover -s tests -v` → `Ran 792 tests in 136.171s`,
  `OK`.
- Node, run individually: `test_singleton_toggle_frontend.js` 15/15,
  `test_upload_frontend.js` 8/8, `test_team_frontend.js` 52/52,
  `test_deploy_frontend.js` 9/9 — 84/84 total.

No regressions. Testing pass is now clean — proceeding to the review pass.

### Review pass

**Spec-to-code traceability** (`docs/spec.md` acceptance criteria, all six):
1. Orphan `/home/deploy` (no user, directory present) → class skipped:
   covered by `DeployTargetOrphanDetectionTests`, unchanged from the first
   pass, independently verified there. **Met.**
2. `tearDown` backstop removes `/home/deploy` after a forced mid-body
   failure: now genuinely covered by the reworked
   `DeployTargetTearDownBackstopTests`, independently re-verified above via
   revert-and-fail. **Met.**
3. Two full concurrent runs of the real-tmux classes in both files, zero
   collisions: unchanged from the first pass (already independently
   verified with two of my own separate background processes). **Met.**
4. No test creates a session literally named `team-demo`/`team-proj`/etc.:
   unchanged from the first pass (already independently grepped). **Met.**
5. `switchboard-worktree-op-` sweep only targets this process's own scope:
   unchanged from the first pass (already independently verified by
   planting foreign sessions). **Met.**
6. Full existing suite passes with no regression: re-run fresh this round,
   792/792 Python, 84/84 Node. **Met.**

All six acceptance criteria are implemented and covered by a test that
actually discriminates pass/fail on the behavior in question — no gaps.

**Correctness.** The reworked test correctly isolates the variable under
test: by never creating a `deploy` account, the pre-existing `userdel -r
deploy` call in the inherited `tearDown()` genuinely has nothing to act on
(a no-op), so only the new backstop line can be responsible for removing
`/home/deploy` — confirmed by the revert-and-fail check both the developer
and I ran independently, with matching results. The test still satisfies
the literal acceptance-criterion wording ("forcing an exception after the
fixture is created but before normal cleanup would run") — the "fixture"
here is `/home/deploy` itself, which is what's actually under test; the
account-creation step was never load-bearing for what criterion 2 asks to
be proven, only incidental to how the first-pass version of the test
happened to construct it.

One asymmetry worth noting but not blocking: `DeployTargetOrphanDetectionTests`
registers `self.addCleanup(lambda: subprocess.run(["sudo", "rm", "-rf",
"/home/deploy"]))` for its own directly-created fixture, but
`DeployTargetTearDownBackstopTests`'s outer test method has no equivalent
`addCleanup` — it relies entirely on the inner subclass's own `tearDown()`
(the very thing under test) to clean up. If the backstop were ever broken
again, this specific test would leave `/home/deploy` on disk after failing.
This isn't a false-pass risk (the test would still fail, correctly), and
the consequence is fail-safe rather than fail-dangerous: any subsequent
test run would trip the criterion-1 orphan guard in `setUp()` and skip
cleanly rather than run against dirty state, exactly as designed. Noted as
a should-fix / nice-to-have, not a blocker.

**Security.** No new production code. All new `subprocess.run()` calls in
this round use fixed argument lists or hardcoded shell strings with no
interpolated external input — no injection surface. Real host mutation
(`/home/deploy` creation/removal) remains gated behind
`HAVE_PASSWORDLESS_SUDO` and the class's own `setUp()` pre-flight checks,
consistent with the rest of the file's existing convention.

**Simplicity / scope.** The fix is a minimal, surgical rework of exactly
the one test method Defect 1 named — no other test, no production file,
touched. Matches `docs/spec.md`'s non-goals (test-only diff) and doesn't
introduce any new abstraction or generalization beyond what's needed to
isolate the fixture-construction step.

## Findings (ranked)

1. **Should-fix (non-blocking):** `DeployTargetTearDownBackstopTests`
   (`tests/test_deploy_target.py:702-762`) doesn't `addCleanup` its own
   directly-constructed `/home/deploy` fixture the way
   `DeployTargetOrphanDetectionTests` does. Low-value but consistent hygiene
   fix — wrap the fixture creation with the same `self.addCleanup(lambda:
   subprocess.run(["sudo", "rm", "-rf", "/home/deploy"]))` pattern used two
   classes above it, so a future regression in the backstop doesn't also
   leave state dirty on this test's own failure path. No functional impact
   given the fail-safe interaction with the criterion-1 orphan guard.

No must-fix findings. No nits beyond the above.

## Overall verdict (this round)
**Approved.** All six acceptance criteria in `docs/spec.md` are implemented
and independently, discriminately verified (including a from-scratch
revert-and-fail re-check of the specific fix this round addresses). Full
regression suite is clean (792/792 Python, 84/84 Node). One should-fix
follow-up noted above (test cleanup hygiene) — does not block approval.
Ready to hand back to product-manager for the next iteration.
