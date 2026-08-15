# Test & Review: taiga-up.sh resilience from Proxmox E2E test round 4 (item 30)

## Scope
Independent re-verification of `scripts/taiga-up.sh`'s retry/detect/fail-loudly
rewrite and its new test harness (`tests/test_taiga_up_retry.py`) against
`docs/spec.md`'s five acceptance criteria, plus a full-suite regression check
and a final isolation check that this branch's own diff (vs.
`backlog/e2e-fixes-round3`) contains nothing but this item's changes.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | AC1: `taiga-gateway` runs on first `up -d` → exit 0 immediately, zero retry/sleep overhead | Automated (`test_succeeds_on_first_attempt_no_retry`) + code trace | pass | `python3 tests/test_taiga_up_retry.py -v` → ok; asserts `up_calls==1`, `rm_calls==0`; loop's `exit 0` fires before the `rm`/`sleep` block on the first iteration |
| 2 | AC2: non-`running` state → removes just `taiga-gateway`, retries, capped at `TAIGA_UP_MAX_ATTEMPTS` total attempts | Automated (`test_succeeds_on_second_attempt_after_one_retry`, `test_exhausts_all_attempts_and_fails_loudly`) + deliberate off-by-one mutation test | pass | See "Loop-bound trace" below — mutating `-le`→`-lt` caused `up_calls` to drop from 3→2, caught by the existing tests, then reverted and diff-confirmed clean |
| 3 | AC3: all attempts fail → exit non-zero, stderr names container, attempt count, and 3 "look next" pointers | Automated (`test_exhausts_all_attempts_and_fails_loudly`) | pass | asserts exact substrings: `taiga-gateway failed to come up after 3 attempts`, `manual intervention needed`, `docker compose logs taiga-gateway`, `docker network ls`, `disk space` |
| 4 | AC4: `TAIGA_UP_MAX_ATTEMPTS` env-overridable, default 3 | Automated (`test_max_attempts_env_override_is_honored`, default used implicitly by all other cases) | pass | override to 5 → `up_calls==5`, `rm_calls==4`, stderr `after 5 attempts` |
| 5 | AC5: `bash -n` / `shellcheck` clean | Manual command run | pass | `bash -n scripts/taiga-up.sh` exit 0; `shellcheck scripts/taiga-up.sh` exit 0, zero warnings/output |
| 6 | Final failure message fires only after full exhaustion, not on every intermediate failure | Code trace | pass | message is textually after the `done` closing the `while` loop — only reachable once the loop condition (`attempt -le MAX`) goes false; both retry tests confirm the *intermediate* message (`didn't come up cleanly`) is what's seen on non-final attempts |
| 7 | `rm -f taiga-gateway` targets only the one container, never a full stack `down` | Code read + test | pass | script never calls `down`; `"${COMPOSE[@]}" rm -f taiga-gateway` is the only removal call, gated inside `if [ "$attempt" -lt "$TAIGA_UP_MAX_ATTEMPTS" ]` so it never runs after the final attempt (exhaustion test: `rm_calls==2` for `max_attempts=3`) |
| 8 | Test harness runs the REAL script, not a reimplementation | Code read + deliberate revert-and-watch-it-fail check | pass | harness reads `scripts/taiga-up.sh` verbatim via `open(TAIGA_UP_SH).read()` and appends it after stub definitions, run via `bash -c`; confirmed by mutating the real script (`-le`→`-lt`) and observing 2 of 4 tests fail with the exact expected wrong counts, then reverting |
| 9 | Disclosed SC1090 deviation is accurate and scoped to only that one warning | Manual shellcheck runs, before/after directive | pass | `taiga-status.sh`, `gitea-up.sh`, `taiga-down.sh` each independently show the identical unaddressed `SC1090` warning today; removing the new `# shellcheck disable=SC1090` line from a scratch copy of `taiga-up.sh` reproduces exactly that one warning and no other — directive suppresses nothing beyond the disclosed finding |
| 10 | `app/app.py::taiga_run()` and `scripts/gitea-up.sh` untouched (Non-goals) | `git diff HEAD -- app/app.py scripts/gitea-up.sh` | pass | empty diff for both files |

## Loop-bound trace (off-by-one check)
Traced by hand and then confirmed empirically: `while [ "$attempt" -le "$TAIGA_UP_MAX_ATTEMPTS" ]` with `attempt` starting at 1 and incrementing once per full loop body — this runs the body exactly `TAIGA_UP_MAX_ATTEMPTS` times (attempts 1..N inclusive), matching the spec's "up to `TAIGA_UP_MAX_ATTEMPTS` total attempts." To make sure this wasn't just my own re-derivation being wrong in the same way the code might be wrong, I mutated the real script's comparison from `-le` to `-lt` (which would make it an off-by-one, running only N-1 attempts) and reran the real test suite: `test_exhausts_all_attempts_and_fails_loudly` and `test_max_attempts_env_override_is_honored` both failed with exactly the predicted wrong counts (`2 != 3`, `4 != 5`). Reverted immediately after (`diff` confirmed byte-identical to the pre-mutation file). This also served as the "genuinely exercises this fix, not a vacuous test" check called for in the task.

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests`

- Baseline (this diff stashed, i.e. `backlog/e2e-fixes-round3` tip): **1205 tests, OK, 0 failures.**
- With diff applied, run in isolation with no other test processes on the box: **1209 tests, OK, 0 failures** (1205 + the 4 new `test_taiga_up_retry.py` cases — matches the developer's claimed count exactly).

One earlier run *with the diff applied* did show 4 failures, all in
`PrivilegedEndToEndTests`/`PrivilegedDeployRunEndToEndTests` (`test_deploy_target.py`,
`test_deploy_dispatch.py`) with errors like `switchboard-deploy-wrapper.sh: not found`.
I did not take this at face value — these tests provision real system users, a
real sshd session against 127.0.0.1, and real systemd units
(`docs/spec.md`'s own Non-goals confirm this change touches nothing in that
area), so a causal link to a `taiga-up.sh`-only diff plus an independent new
test file is not plausible on its face. I re-ran exactly those 4 tests in
isolation (both with and without the diff stashed) and they passed cleanly
both times, then re-ran the entire suite once more end-to-end with no other
test processes running concurrently on the box and got a clean `1209 OK`.
Root cause: I had a stray overlapping background full-suite invocation of my
own running at the same time as that one failing run, which raced the same
real system user / sshd / systemd-unit provisioning the privileged tests use.
This is pre-existing environment flakiness under concurrent invocation, not a
regression introduced by this diff — confirmed, not assumed, via the
baseline-vs-diff comparison above. Documenting it here since the developer's
own implementation.md claim of "Ran 1209 tests ... OK" should not be taken on
faith either, and this record shows it checks out under a clean run.

Type-check/lint: `bash -n scripts/taiga-up.sh` and `shellcheck scripts/taiga-up.sh` both clean (see test case 5).

## Defects found
None. Testing pass is clean.

---

## Spec coverage
All 5 acceptance criteria in `docs/spec.md` are implemented and covered by
either the automated harness or a direct code/behavior trace (see test cases
1-5 above; no gaps found). The spec's "Proposed approach" code block was
compared line-by-line against `scripts/taiga-up.sh` and matches exactly,
with one disclosed, verified-accurate deviation (the `SC1090` suppression
comment, needed to satisfy the spec's own "shellcheck clean" criterion —
see test case 9). Both Non-goals items that name specific files
(`app/app.py::taiga_run()`, `scripts/gitea-up.sh`) were confirmed untouched
(test case 10). The other two Non-goals (root-cause fix, pre-flight `df`
check) are correctly absent from the diff — nothing in the script attempts
either.

## Isolation check (this round's own diff vs. round 3)
`git diff backlog/e2e-fixes-round3 HEAD --stat` is empty — `HEAD` on this
branch is still exactly `backlog/e2e-fixes-round3`'s tip (no commits made
yet this round). The only changes present are uncommitted working-tree
changes: `git status --porcelain` shows exactly `docs/implementation.md`
(modified), `docs/spec.md` (modified), `scripts/taiga-up.sh` (modified),
and `tests/test_taiga_up_retry.py` (untracked/new) — nothing left over from
an earlier round, nothing extraneous.

## Findings (most severe first)
None — no must-fix, should-fix, or nit findings from the correctness,
security, or simplicity passes. The diff is a near-verbatim implementation
of the spec's own proposed code (one disclosed, verified line added), the
new test file follows this session's already-proven real-script-with-
stubbed-`docker` technique faithfully, and there is no unnecessary
abstraction, dead code, or scope creep beyond `scripts/taiga-up.sh` and its
dedicated test file.

## Follow-ups (non-blocking)
- None specific to this item. (The spec's own Non-goals already list the
  known adjacent follow-ups — root-cause investigation, `df` pre-flight
  check, `app.py` stderr surfacing — as explicitly out of scope, not as
  gaps in this change.)

## Overall verdict
Approve.
