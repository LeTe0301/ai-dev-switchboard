# Implementation: test-infrastructure isolation (BACKLOG item 9)

## Summary
Two independent, test-only fixes. **A.** `tests/test_deploy_target.py`'s
privileged fixture class now refuses to run against an orphaned
`/home/deploy` directory (not just a live `deploy` user), and its
`tearDown()` gained an unconditional `sudo rm -rf /home/deploy` backstop so
a partial failure can never leave the directory behind for the next run.
**B.** Every real (non-mocked) `team-<project>` tmux session name and the
`switchboard-worktree-op-` sweep in `tests/test_teams_lifecycle.py` and
`tests/test_team_routes.py` are now scoped to this process's own pid, and
both files' `_kill_leftover_team_sessions()` sweeps were narrowed to only
ever target this process's own scoped sessions — proven by actually running
two full suites of both files concurrently in separate processes.

## Root cause
Not applicable (test-infrastructure hardening, not a bugfix against a single
reported symptom) — see `docs/spec.md` "Background" for the two defects this
closes.

## Changes by file

- `tests/test_deploy_target.py`
  - `InstallScriptDeployTargetBlockTests.setUp()`: skip guard now checks
    `os.path.exists("/home/deploy")` in addition to the existing `id deploy`
    check — either condition means the fixture isn't safely available.
  - `InstallScriptDeployTargetBlockTests.tearDown()`: added an unconditional
    `subprocess.run(["sudo", "rm", "-rf", "/home/deploy"])` immediately after
    the existing `userdel -r deploy` call.
  - Added `DeployTargetOrphanDetectionTests` (new class): constructs a
    genuinely orphaned `/home/deploy` (real directory + stale
    `authorized_keys`, no `deploy` account) and runs a real test method of
    `InstallScriptDeployTargetBlockTests` through `unittest.TestCase.run()`
    with a `unittest.TestResult`, asserting `result.skipped` has exactly one
    entry mentioning `/home/deploy` — proves the class is genuinely
    *skipped*, not that it happens to pass.
  - Added `DeployTargetTearDownBackstopTests` (new class, **reworked once**
    after review — see "Review fix" below): runs a real subclass of
    `InstallScriptDeployTargetBlockTests` (inheriting its real, unmodified
    `setUp`/`tearDown`) whose test body constructs `/home/deploy` directly
    (no `useradd`, no `deploy` account created at all) and then deliberately
    raises `RuntimeError`, through the same `TestCase.run()`/`TestResult`
    mechanism (unittest always calls `tearDown()` after a failing test body
    — standard library behavior, not reimplemented here), then asserts
    `/home/deploy` no longer exists on disk.

- `tests/test_teams_lifecycle.py`
  - Added `_RUN_ID_SCOPE = f"p{os.getpid()}"` and a `_scoped(name)` helper
    (`f"{name}-{_RUN_ID_SCOPE}"`), same technique
    `tests/test_teams_headless.py`'s own `_RUN_ID_SCOPE`/`_SESSION_PREFIX`
    already established.
  - Every literal project name that becomes part of a real
    `team-<project>` tmux session in a real-tmux test class now goes
    through `_scoped(...)`: `demo`, `dirty`, `detached`, `nongit`, `demo2`,
    `demo3`, `demo4`, `"demo with space"`, `proj`, `clidemo`, `atomicdemo`,
    `failchain`, `failchain2`, `sessionrace`. Every corresponding literal
    `"team-<name>"` assertion string was replaced with
    `teamsmod._team_session_name(_scoped("<name>"))` (never reimplementing
    the `team-` prefix in the test).
  - `_kill_leftover_team_sessions()`: the sweep now only kills sessions
    matching `name.startswith("team-") and name.endswith(f"-{_RUN_ID_SCOPE}")`
    — the old unconditional `"team-"`/`"switchboard-"` prefix match is
    removed entirely (see "Key decisions" below for why the
    `"switchboard-"` branch was dropped rather than scoped).
  - `RunRunUserCommandRealTmuxTests.test_no_leftover_session_or_rundir`
    (found while proving the concurrency criterion by hand — see "Key
    decisions"): now snapshots existing `switchboard-worktree-op-*`
    sessions *before* its own `_run_run_user_command()` call and only
    asserts no *new* ones appeared, instead of a blind post-hoc
    `list-sessions` scan that would false-positive-fail in the presence of
    a concurrent process's own, unrelated `switchboard-worktree-op-*`
    session.

- `tests/test_team_routes.py`
  - Added `_PROJ = f"proj-{_RUN_ID_SCOPE}"`, `_PROJ_A = f"proj-a-{_RUN_ID_SCOPE}"`,
    `_PROJ_B = f"proj-b-{_RUN_ID_SCOPE}"`, reusing the file's own existing
    `_RUN_ID_SCOPE` (already defined for `switchboard-headless-*` scoping
    from 6d part 2a's own follow-up).
  - `_project(self, name=_PROJ)`'s default is now scoped, matching
    `docs/spec.md`'s explicit callout.
  - Every literal `"proj"` / `"proj-a"` / `"proj-b"` value used as a real
    project directory name, dict key, or `/projects/<name>/...` URL segment
    across every `_RealHTTPTeamTestCase` subclass and
    `OrphanCheckSelfCorrectsForLiveCliRunTests` (both real-tmux) was
    replaced with `_PROJ`/`_PROJ_A`/`_PROJ_B` (plain-string URLs converted
    to f-strings where needed; already-f-string URLs had only the
    `/projects/proj.../` segment substituted). `TeamThreadsLockTests`'
    in-memory-dict-only tests (no tmux involved at all) were also updated
    to `_PROJ` for internal consistency, though they don't strictly need
    scoping.
  - `_kill_leftover_team_sessions()`: same fix shape as
    `test_teams_lifecycle.py` — `"team-"` matches now require
    `.endswith(f"-{_RUN_ID_SCOPE}")`; the existing `_SESSION_PREFIX`
    (`switchboard-headless-<scope>`) branch was already correctly scoped
    and is unchanged.

No changes to `app/teams.py` or any other production file.

## Key decisions / tradeoffs

- **`_kill_leftover_team_sessions()`'s `"switchboard-"` branch was removed
  entirely in `test_teams_lifecycle.py`, not scoped.** Its only real
  purpose there is cleaning up `switchboard-worktree-op-*` sessions from
  `_run_run_user_command()` (`app/teams.py`), but that function's `op_id`
  is `f"{int(time.time())}-{secrets.token_hex(6)}"` — no per-process token
  to scope a sweep by safely, and it's production code this cycle must not
  touch. `_run_run_user_command()` already self-cleans its own session
  unconditionally in its own `finally` block (verified by reading
  `app/teams.py:3049-3052`), so a defensive sweep for it in the test file
  was unsafe-for-concurrency and, on inspection, redundant for the normal
  case. `test_team_routes.py`'s own `_kill_leftover_team_sessions()` already
  had a correctly-scoped `_SESSION_PREFIX` branch for
  `switchboard-headless-*` (from a prior cycle), which was left untouched.
- **`RunRunUserCommandRealTmuxTests.test_no_leftover_session_or_rundir` was
  additionally hardened** beyond what `docs/spec.md`'s "Proposed approach"
  literally enumerates. Found by directly constructing the acceptance
  criterion's own scenario (planting a fake foreign
  `switchboard-worktree-op-*` session and running the file's tests): the
  sweep itself correctly left the foreign session untouched, but this
  *unrelated* test's own blind `list-sessions` + prefix-filter assertion
  false-positive-failed in its presence (a read, not a kill, so it doesn't
  violate the "sweep must not touch others" criterion, but it does mean
  the file's tests wouldn't all pass cleanly next to a concurrent process,
  which is the spirit of the criterion). Fixed with a before/after
  baseline diff instead of an absolute scan. This is judgment applied
  in the same file/area the spec already names, not new scope.
- **`_PROJ`/`_PROJ_A`/`_PROJ_B` chosen over per-test-method local variables**
  in `test_team_routes.py`. Given ~140 call sites across the file, module-
  level constants (computed once from `_RUN_ID_SCOPE`, already
  process-scoped) kept the diff a mechanical, uniform substitution rather
  than restructuring every test method to compute and thread through a
  local scoped name.
- **A few substitutions landed slightly wider than the strict minimum**
  (e.g., `ValidateProjectForTeamRealGitTests`/`CreateRemoveWorktreeRealGitTests`
  in `test_teams_lifecycle.py`, which touch real git but never a real
  `team-<project>` tmux session, and `TeamThreadsLockTests` in
  `test_team_routes.py`, an in-memory-dict-only test). These were included
  because the exact-substring replacements (`os.path.join(self.tmp,
  "proj")`, `"proj"`) are identical regardless of which test class uses
  them, and scoping them is harmless (no assertion depends on the specific
  unscoped literal) while keeping the mechanical substitution simple and
  exhaustive rather than hand-carving exclusions.

## Deviations from spec
None. Both fixes match `docs/spec.md`'s "Proposed approach" section; the
`test_no_leftover_session_or_rundir` hardening above is an extension found
while proving the spec's own acceptance criteria, in the same file/function
the spec already names, not a new area of scope.

## Review fix: `DeployTargetTearDownBackstopTests` didn't actually prove the
backstop line (post-review rework)

**Finding (not disputed).** The reviewer reverted only the new
`sudo rm -rf /home/deploy` backstop line (keeping the pre-existing
`userdel -r deploy` call intact) and reran
`DeployTargetTearDownBackstopTests` — it still passed. Root cause: the
test's forced-failure scenario always ran *after* a fully successful
`useradd deploy` (via `run_block()`), so the pre-existing `userdel -r
deploy` already removed `/home/deploy` as an ordinary side effect of
removing the account (typical `useradd -m`/`userdel -r` behavior), entirely
independent of whether the new backstop line existed. The test's own
docstring claim that "only `tearDown()`'s own unconditional backstop is
what can remove it" was not actually true for the scenario it constructed.

**Fix.** Reworked the test body's fixture-construction step so it no longer
calls `run_block()` (which runs a real `useradd deploy`) at all. Instead it
constructs the orphan shape directly — `sudo mkdir -p /home/deploy/.ssh` +
a stale `authorized_keys` — the exact same shape
`DeployTargetOrphanDetectionTests` already uses above it in this file, with
no `deploy` account ever created. With no matching account, the inherited
`tearDown()`'s pre-existing `sudo userdel -r deploy` call fails as a no-op
(nothing to delete), so only the new, explicit `sudo rm -rf /home/deploy`
backstop line can be what removes the directory. The test's own body
asserts `id deploy` is non-zero (no account) before raising, so the
scenario's shape is verified, not just assumed.

**Revert-and-fail proof (the same check the reviewer used), re-run in this
session:**
1. Confirmed the host was clean beforehand (`sudo test -e /home/deploy` →
   absent, `id deploy` → no such user).
2. Temporarily reverted only the `subprocess.run(["sudo", "rm", "-rf",
   "/home/deploy"])` backstop line in `InstallScriptDeployTargetBlockTests
   .tearDown()` (kept the pre-existing `userdel -r deploy` call intact) and
   ran `python3 -m unittest
   tests.test_deploy_target.DeployTargetTearDownBackstopTests -v`:
   ```
   FAIL: test_teardown_backstop_removes_home_deploy_after_a_forced_mid_test_failure
   AssertionError: True is not false : tearDown's backstop must remove
   /home/deploy even when no 'deploy' user ever existed for userdel -r to
   clean it up as a side effect
   Ran 1 test in 0.048s
   FAILED (failures=1)
   ```
   The test now genuinely fails without the backstop line — proving it's
   the thing being tested.
3. Manually removed the leftover `/home/deploy` this proof run left behind
   (the reverted tearDown had no way to clean it up, by design), restored
   the backstop line, and reran the same command:
   ```
   test_teardown_backstop_removes_home_deploy_after_a_forced_mid_test_failure ... ok
   Ran 1 test in 0.055s
   OK
   ```
   Confirmed the working tree diff round-tripped back to exactly the
   restored version (`diff` against a pre-revert backup produced no
   output).

**Full-suite re-verification after the fix**, host confirmed clean
(`/home/deploy` absent, no `deploy` user) before and after:
```
python3 -m unittest tests.test_deploy_target -v      # 32/32 OK
python3 -m unittest discover -s tests                # 792/792 OK (unchanged
                                                       # — this cycle only
                                                       # reworked one
                                                       # existing test's
                                                       # internals, no
                                                       # tests added/removed)
node tests/test_singleton_toggle_frontend.js          # 15/15
node tests/test_upload_frontend.js                    # 8/8
node tests/test_team_frontend.js                       # 52/52
node tests/test_deploy_frontend.js                     # 9/9
```
No regressions. No production code touched.

## Known limitations
- `tests/test_deploy_target.py`'s `PrivilegedEndToEndTests` class (a
  *different* class from the one this cycle fixes) uses a fixed, unscoped
  system username (`TEST_USER = "aidswbtest"`) and is **not** safe to run
  concurrently across two processes — confirmed directly: running
  `test_deploy_target.py` in two simultaneous processes produces real SSH/
  rsync failures there from the two processes racing the same system
  account. This is out of scope for this cycle (`docs/spec.md`'s
  concurrency acceptance criterion names only
  `tests/test_team_routes.py`/`tests/test_teams_lifecycle.py`; the
  `PrivilegedEndToEndTests` username shape is a pre-existing, separate
  condition this spec's "Non-goals" doesn't ask this cycle to fix).
- The orphan-detection fix in `InstallScriptDeployTargetBlockTests.setUp()`
  still only guards against `/home/deploy` specifically — a differently
  named leftover (e.g. a stale sudoers file with no `/home/deploy`) is not
  detected, matching `docs/spec.md`'s own scope (directory presence, not a
  general leftover-state scan).

## How to verify locally

Part A (orphan detection + backstop), single process:
```
python3 -m unittest tests.test_deploy_target -v
```
`DeployTargetOrphanDetectionTests` and `DeployTargetTearDownBackstopTests`
require passwordless `sudo` (same gate as the rest of the file) and
construct/remove a real `/home/deploy` directory.

Part B concurrency proof (the actual regression test for the reported
problem — must be two separate processes, not sequential):
```
python3 -m unittest tests.test_teams_lifecycle tests.test_team_routes -v &
python3 -m unittest tests.test_teams_lifecycle tests.test_team_routes -v &
wait
```
Both must print `OK` with zero failures/errors, and `tmux list-sessions`
afterward must show no leftover `team-*`/`switchboard-*` sessions from
either run.

Part B sweep-safety proof (a concurrent process's own sessions must survive
this file's tearDown sweep):
```
tmux new-session -d -s "switchboard-worktree-op-9999999999-deadbeef0000" "sleep 60"
tmux new-session -d -s "team-otherproj-p9999999" "sleep 60"
python3 -m unittest tests.test_teams_lifecycle -q
tmux list-sessions   # both planted sessions must still be listed
tmux kill-session -t "switchboard-worktree-op-9999999999-deadbeef0000"
tmux kill-session -t "team-otherproj-p9999999"
```

Full regression suite:
```
python3 -m unittest discover -s tests -v   # expect 792 tests, OK (790 baseline + 2 new)
node tests/test_singleton_toggle_frontend.js   # 15/15
node tests/test_upload_frontend.js             # 8/8
node tests/test_team_frontend.js               # 52/52
node tests/test_deploy_frontend.js             # 9/9
```
