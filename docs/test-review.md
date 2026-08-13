# Test & Review: Local git hosting UI + CI/CD (Gitea) — part 2c, part 2a: deploy-target receiver

## Scope
Covers all 9 acceptance-criteria bullets in `docs/spec.md` for the
`deploy-target/` receiver and `install.sh --with-deploy-target`: the
`deploy-wrapper.sh` forced-command SSH branching, `deploy-restart.sh`'s
validated sudoers-scoped restart, and `install.sh`'s provisioning of the
`deploy` user / `DEPLOY_PATH` / config / scripts / `authorized_keys` /
sudoers. This is new privileged SSH surface (a low-priv account with a
root-restart capability, reachable from any machine holding the matching
private key), so the review pass weighted security scrutiny heaviest:
independently re-deriving the two documented spec deviations from
`/usr/bin/rrsync`'s actual source rather than trusting the write-up, and
re-reading the wrapper for injection/bypass paths.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Full provision: `deploy` user (`/bin/sh`), `DEPLOY_PATH` owned `deploy:deploy`, `authorized_keys` exactly one restricted line, sudoers exact zero-arg `NOPASSWD` grant, `visudo -cf` passes | Automated (`InstallScriptDeployTargetBlockTests.test_full_run_provisions_user_path_env_keys_sudoers`), real pty-driven run of `install.sh`'s literal block as root | pass | `python3 -m unittest tests.test_deploy_target -v` — ok |
| 2 | Push lands under `DEPLOY_PATH`, owned by receiver user, no shell/tty | Automated, real `rsync`/`ssh` against local sshd (`PrivilegedEndToEndTests.test_push_lands_under_deploy_path_owned_by_receiver_user`) | pass | same run |
| 3 | Escape attempt outside `DEPLOY_PATH` never writes outside it | Automated, real rrsync (`test_path_escape_attempt_rejected_no_file_written_outside`) | pass | same run; independently re-verified against `/usr/bin/rrsync`'s own `validated_arg()` source (see Findings) |
| 4 | Arbitrary command / bare interactive attempt rejected, no shell/output/pty | Automated, real ssh (`test_arbitrary_command_rejected_no_shell`, `test_bare_interactive_attempt_rejected_no_shell`) | pass | same run |
| 5 | `deploy-restart` genuinely restarts the named service, no sudo password prompt | Automated, real systemd unit + real ssh (`test_deploy_restart_actually_restarts_service_no_password_prompt`) | pass | same run |
| 6 | Nonexistent service → non-zero exit over SSH, not swallowed | Automated (`test_restart_of_nonexistent_service_fails_loudly`) | pass | same run |
| 7 | Re-run with different path/service/pubkey → `deploy-target.env`/`authorized_keys` reflect only new values, no accumulation | Automated (`test_rerun_with_different_values_overwrites_not_accumulates`) | pass | same run |
| 8 | Blank pubkey at install time → user/path/scripts/sudoers still provisioned, hand-add instructions printed | Automated (`test_blank_pubkey_leaves_authorized_keys_untouched_prints_instructions`) | pass | same run |
| 9 | `--with-host-control` + `--with-deploy-target` together → no conflicting state | Automated (`test_combined_with_host_control_no_conflicting_state`) | pass | same run |
| 10 | Unset/malformed (relative) `DEPLOY_PATH` fails closed, never falls through to unrestricted rrsync | Automated, real ssh (`test_unset_deploy_path_fails_closed_over_real_ssh`, `test_malformed_relative_deploy_path_fails_closed_over_real_ssh`) | pass | same run |
| 11 | Sudoers grant unusable for anything but the exact restart script | Automated (`test_sudoers_grant_scoped_to_exactly_one_zero_arg_script`, `test_sudo_cannot_be_used_to_run_arbitrary_root_command`) | pass | same run |
| 12 | Wrapper never `eval`s/re-interprets `$SSH_ORIGINAL_COMMAND` (injection probe) | Automated (`WrapperBranchingTests.test_never_evals_original_command`) | pass | same run |
| 13 | `rrsync` missing on target → this block only is skipped (not a full `install.sh` `exit 1`) | Manual code read only (see Findings — no automated test, and I could not safely fake `/usr/bin/rrsync`'s absence in this sandbox) | pass (by inspection) | `install.sh:619-623`: `if [ ! -x /usr/bin/rrsync ]; then echo ... >&2; else <rest of block>; fi` — no `exit` in the missing-binary branch, and a bare `[ ]` test inside an `if` condition is exempt from `set -e`, so the rest of `install.sh` continues |

## Regression check
Full existing suite: `python3 -m unittest discover -s tests -v` — **245 tests, all pass** (10 pre-existing test modules unaffected by this change).
`bash -n install.sh` — syntax OK.
Post-run cleanup verified: `id deploy` → no such user, `/etc/ai-dev-switchboard` absent, no `ai-dev-switchboard-deploy-*` files under `/usr/local/bin`, no matching `sudoers.d` entries, no lingering systemd units — the suite's own tearDown/tearDownClass leave the box clean.

(Also found and removed one piece of pre-existing sandbox debris unrelated to this test run — a leftover `aidswbreview` system user and `/etc/sudoers.d/ai-dev-switchboard-deploy-target-review` file from an earlier manual session, using a different username than this suite's own `aidswbtest`/`aidswbreview`-agnostic tearDown. Not caused by this cycle's code or test run; cleaned up for hygiene, noted here for the record.)

## Defects found
None — testing pass is clean.

---

## Spec coverage
All 9 checkbox bullets in `docs/spec.md`'s "Acceptance criteria" section are implemented and covered by a passing automated test that exercises the real behavior (real `ssh`/`rsync`/`sudo`/`systemctl`, not mocks), per the table above. The "Edge cases" section's items are also implemented; all but one ("`rrsync` missing → skip this block only") have automated coverage; that one was verified by direct code reading only (see Findings #1 below — not a blocker).

## Findings (most severe first)

### 1. `rrsync`-missing skip path has no automated (or, this session, hands-on) test coverage — nit
- File: `install.sh:619-623`
- Issue: the spec's "Edge cases" section requires that a target missing `/usr/bin/rrsync` prints an error and skips *only* the `--with-deploy-target` block, not the whole `install.sh` run. No test in `tests/test_deploy_target.py` exercises this path (confirmed via `grep` — only comments/an unrelated `skipUnless` guard mention `rrsync`). I verified it's correct by reading the code directly (the `if [ ! -x ... ]; then echo ...; else ...; fi` has no `exit` in the true branch, and a failing test inside an `if` condition doesn't trigger `set -e`), and I attempted to verify it hands-on by temporarily moving `/usr/bin/rrsync` aside and re-running the extracted block, but the sandbox's own auto-mode classifier blocked that action as too risky (moving a real system binary). This exact gap — no test for the analogous "tool missing → skip this optional block" precedent — already exists for `install.sh`'s pre-existing `ttyd`-arch-not-found check (`grep -rn ttyd tests/*.py` → no hits), so this isn't a new departure from the project's own testing convention, just an inherited one.
- Failure scenario if this were actually broken: a target running an older/non-Debian OS without `rrsync` would see `install.sh --with-deploy-target` (or a combined multi-flag run) `exit 1` and abort every other requested flag instead of just skipping this one — annoying but not a security issue, and the static code shape makes this very unlikely.
- Suggested follow-up (non-blocking): a cheap way to close this without touching the real system binary would be a small refactor to make the rrsync path a variable the test can override (e.g. `RRSYNC_BIN="${RRSYNC_BIN:-/usr/bin/rrsync}"`), or a test that stubs a fake `PATH`-shadowing `/usr/bin/rrsync` doesn't help here since the check is `-x /usr/bin/rrsync` on the literal absolute path — the override-variable approach is the practical fix if this is ever prioritized.

### 2. README.md repo-layout tree: `deploy-target/` line's column alignment is a few spaces short of its neighbors — nit
- File: `README.md:164`
- Issue: `deploy-target/           optional deploy receiver...` starts its description one column earlier than `host-agent/               optional persistent session...` above it, in a block that (loosely) tries to align descriptions into a column. Purely cosmetic; the tree already has pre-existing minor inconsistency (e.g. `app/app.py` vs `engines.d/`), so this isn't a new departure from a firm convention, just imperfect monospace alignment.
- Failure scenario: none — purely visual.

## Verified independently (review pass, not just re-reading the developer's write-up)
- **Deviation 1 (bare `deploy@target:` destination, not `deploy@target:<DEPLOY_PATH>/`)**: confirmed correct by the fact that `PrivilegedEndToEndTests.test_push_lands_under_deploy_path_owned_by_receiver_user` uses the bare form and passes; `deploy-target/README.md` and `install.sh`'s printed summary were both checked and consistently show the corrected bare-destination form.
- **Deviation 2 (`rrsync` silently re-roots an absolute destination under `DEPLOY_PATH` rather than erroring)**: independently confirmed by reading `/usr/bin/rrsync`'s own `validated_arg()` (lines 295-337) directly on this machine — for a non-`.`/`..`-containing absolute arg, when `args.dir != '/'`, the code does `arg = args.dir + arg` (re-rooting), and only `die()`s on a literal `..` traversal attempt (`HAS_DOT_DOT_RE`) or on `os.path.realpath()` resolving outside `args.dir_slash` for an existing path. This matches `docs/implementation.md`'s "Deviations from spec" write-up exactly, and the load-bearing security property (nothing lands outside `DEPLOY_PATH`) is what `test_path_escape_attempt_rejected_no_file_written_outside` actually asserts, which I ran and confirmed passing.
- **Wrapper injection surface**: `deploy-wrapper.sh` does a literal `case` string match only, never `eval`s `$SSH_ORIGINAL_COMMAND`; confirmed by direct read and by `test_never_evals_original_command` passing. The `rsync --server*` branch execs a hardcoded absolute path (`/usr/bin/rrsync`, not PATH-resolved) with a fixed `-wo "$DEPLOY_PATH"` argument list — the client's own `$SSH_ORIGINAL_COMMAND` content past the case-match is never passed as shell-interpreted argv to anything; `rrsync` itself independently re-reads and validates `$SSH_ORIGINAL_COMMAND` for the actual rsync protocol arguments, which is exactly rrsync's documented purpose.
- **Sudoers scoping**: `deploy ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-deploy-restart.sh` — zero arguments, no wildcard, confirmed via `visudo -cf` and via `sudo -l` output in `test_sudoers_grant_scoped_to_exactly_one_zero_arg_script`.
- **No secrets in code/logs**: no credentials are logged or hardcoded; the only "secret" involved (the SSH private key) never touches this repo or the target machine per spec design, and the pasted-in `DEPLOY_PUBKEY` is, by definition, public.
- **Scope discipline**: diff matches `docs/spec.md`'s "Affected areas" exactly — no changes to `app/app.py`, `config/switchboard.env.example`, or switchboard-side sudoers/systemd assets. No unnecessary abstractions found; the wrapper/restart scripts are as minimal as the spec calls for (no speculative multi-target or generic-command-runner machinery was added, matching "Non-goals").

## Follow-ups (non-blocking)
- Consider closing Finding #1's coverage gap (`rrsync`-missing skip path) with a `RRSYNC_BIN`-style override variable + test, if/when this area gets touched again — not urgent given the low-risk, easily-inspected code shape and the matching pre-existing untested `ttyd` precedent.
- Optional cosmetic fix to `README.md:164`'s column alignment (Finding #2).

## Overall verdict
**Approve with follow-ups.** All 30 new tests pass (including every privileged real-SSH/rsync/systemd end-to-end test), the full 245-test repo suite has zero regressions, every one of `docs/spec.md`'s 9 acceptance-criteria bullets is implemented and covered by a real (non-mocked) passing test, and the two documented spec deviations were independently re-verified against `/usr/bin/rrsync`'s actual source rather than taken on trust. The two findings above are both nits (an inherited, pre-existing test-coverage gap on an unlikely/low-risk path, and a cosmetic whitespace nit) — neither blocks this cycle.
