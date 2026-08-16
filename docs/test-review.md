# Test & Review: E2E round 7 — 5 fixes from the round-6 real-CT110 test (items 39-43)

## Scope
Verifies docs/spec.md's five acceptance criteria (items 39-43) against the actual diff on `backlog/e2e-fixes-round6` (uncommitted working tree): `install.sh` AUTH_MODE default-seeding, Gitea admin-bootstrap `--must-change-password=false` + `gitea-configure-api.sh`'s 403 diagnostic, code-server binary path resolution (three locations + persisted env var), `SingletonActionError`-based honest 502 responses for `/host`,`/taiga`,`/gitea` on/off, and `taiga-up.sh`'s one-shot fallback `up -d` after retry exhaustion.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Item 39: pre-seeded `AUTH_MODE=pve` survives `install.sh --yes` | automated, `tests/test_install_auth_mode_default.py` (extracts real install.sh block verbatim) | pass | `test_preseeded_pve_survives_yes_install` — ran, green; reverted `install.sh` → same test errors (marker not found), confirming it's not a self-fulfilling extraction |
| 2 | Item 39: no pre-seed still defaults to `simple` | automated, same file | pass | `test_no_preseed_still_defaults_to_simple` — green |
| 3 | Item 40: printed bootstrap command includes `--must-change-password=false` | code read (`install.sh` diff) | pass | `git diff install.sh` shows the flag added on its own continuation line |
| 4 | Item 40: 403 + "must change" body → targeted diagnostic naming real cause + fix | automated, reviewer-added `tests/test_gitea_configure_api_verify.py` (extracts real verify block verbatim, stubs `curl`) | pass | 4/4 green; reverted `scripts/gitea-configure-api.sh` → extraction fails (marker not found), confirming genuine coverage. No coverage existed before this review pass — developer's own verify-locally list only ran `bash -n` (syntax-only) for item 40 |
| 5 | Item 40: other non-200 (e.g. 401 insufficient scope) still shows generic real output, not the must-change message | automated, same new file | pass | `test_403_without_must_change_body_falls_back_to_generic_output`, `test_other_non_200_status_prints_generic_output_not_bare_curl_error` — green |
| 6 | Item 40: 200 success still falls through to the rest of the script | automated, same new file | pass | `test_200_success_falls_through_past_verification` — green |
| 7 | Item 41: idempotency check finds code-server anywhere on PATH (not just `/usr/local/bin/`) | automated, `tests/test_install_code_server_path.py` | pass | `test_already_installed_at_nonstandard_path_skips_reinstall` (0 curl/install calls) — green |
| 8 | Item 41: nothing on PATH → still proceeds to install (not an error), falls back to literal default | automated, same file | pass | `test_not_on_path_triggers_install_and_falls_back_to_default` — green |
| 9 | Item 41: sudoers rule and `CODE_SERVER_BIN` always agree on the same resolved path | automated, same file | pass | `test_sudoers_rule_matches_resolved_path_exactly` — green |
| 10 | Item 41: `WITH_CODE_SERVER=0` still resolves/persists a value, never installs | automated, same file | pass | `test_with_code_server_disabled_still_resolves_and_persists_default` — green |
| 11 | Item 41: app.py's `CODE_SERVER_BIN` default self-heals via `shutil.which` when env var unset (existing-install-upgrade case) | automated, `tests/test_code_server_bin_default.py` (fresh subprocess import, controlled PATH) | pass | 3/3 green; reverted `app/app.py` → `test_resolves_via_shutil_which_when_env_var_unset_and_on_path` fails (`/usr/local/bin/code-server` returned instead of the fake on-PATH binary), confirming genuine coverage |
| 12 | Item 41 (all four): full revert-check across all three occurrences together | manual `git stash`/`git stash pop` per-file, reran affected suites | pass | see rows 1-11's individual revert notes |
| 13 | Item 42: `host_run`/`taiga_run`/`gitea_run` raise `SingletonActionError` w/ `.stderr` on nonzero returncode for mutating actions | automated, `tests/test_host_control.py`, `tests/test_gitea.py`, `tests/test_taiga.py` | pass | all green; reverted `app/app.py` → all three files error with `AttributeError: module 'app' has no attribute 'SingletonActionError'`, confirming genuine coverage |
| 14 | Item 42: `"status"` action never raises, keeps str/never-raises contract, even on nonzero returncode | automated, same three files | pass | `test_status_*_never_raises_returns_stdout` / `test_status_returns_stdout_regardless_of_returncode` — green in all three |
| 15 | Item 42: `POST /host,/taiga,/gitea` `/on`,`/off` return 502 + `{error, stderr}` (truncated) on failure | automated, same three files, real `ThreadingHTTPServer` + real HTTP requests | pass | 6 new `*_toggle_*_failure_returns_502_*` tests, all green |
| 16 | Item 42: success path unchanged, still `200 {"ok": true}` | automated, pre-existing tests in all three files (unchanged) | pass | full `test_gitea.py`/`test_taiga.py`/`test_host_control.py` runs green, including pre-existing success-path cases |
| 17 | Item 42: `GET /status` and `create_project()`'s gitea status check unaffected | automated, full existing `test_gitea.py`/`test_taiga.py`/`test_status.py`-family suites | pass | full suite run, no regressions (see Regression check) |
| 18 | Item 43: fallback `up -d` (no `rm -f`) runs once after full retry exhaustion, succeeds → exit 0 | automated, `tests/test_taiga_up_retry.py` (runs real `taiga-up.sh` against stubbed `docker`) | pass | `test_fallback_up_after_exhaustion_succeeds` — green; reverted `scripts/taiga-up.sh` → 4 tests fail (wrong call counts / wrong exit code / missing fallback stderr line), confirming genuine coverage |
| 19 | Item 43: fallback also fails → still exits 1 with existing loud failure message | automated, same file | pass | `test_fallback_up_after_exhaustion_also_fails_exits_1` — green |
| 20 | Item 43: fallback never reached when loop already succeeds within normal attempts | automated, same file | pass | `test_fallback_not_reached_when_normal_attempts_already_succeed` — green |
| 21 | Item 43: existing exhaustion-message tests updated for +1 `up -d` call count | automated, same file (updated pre-existing tests) | pass | `test_exhausts_all_attempts_and_fails_loudly`, `test_max_attempts_env_override_is_honored` — green, correctly expect 4 and 6 calls respectively |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests -v` — **1264 tests, 3 failures, 1 skip.**

All 3 failures are in `tests/test_teams_grounding.py` (`DiscoverThisRepoTests`/`GroundingCLITests`), caused by a gitignored, locally-present `CLAUDE.md` at the repo root (`.gitignore` line 6: `/CLAUDE.md`) being picked up by that test's own grounding-file-discovery walk — confirmed **pre-existing and environmental, not a regression**: reran the identical file with `git stash` (all of this cycle's changes removed) and got the exact same 3 failures with an identical diff. Not touched by, and unrelated to, any of items 39-43.

The 1 skip (`PrivilegedDeployRunEndToEndTests` in `test_deploy_dispatch.py`) is the same pre-existing environmental skip implementation.md documents (a real `aidswbdeploy2b` system user already exists on this box) — confirmed by running that file directly.

1264 = the developer's own reported 1260 baseline + 4 tests added by this review pass (`tests/test_gitea_configure_api_verify.py`).

Also ran: `bash -n` on all three modified shell scripts (clean), `python3 -m py_compile app/app.py` (clean), `shellcheck` on `install.sh`/`scripts/gitea-configure-api.sh`/`scripts/taiga-up.sh` — one pre-existing `SC2034` warning (`REPO_ROOT` unused in `gitea-configure-api.sh`, present before this diff, not introduced by it), nothing else.

No defects found in the testing pass — proceeding to review.

---

## Spec coverage
All five acceptance criteria in docs/spec.md are implemented and now have automated test coverage:

- **Item 39**: implemented (`install.sh` AUTH_MODE_DEFAULT seed-from-env) and tested. ✓
- **Item 40**: implemented (printed `--must-change-password=false` + `gitea-configure-api.sh` 403 special-case). Printed-command half is code-inspection-verified (matches spec text exactly); the diagnostic-message half had **zero automated coverage from the developer** (implementation.md's own verify-locally list only runs `bash -n` for it) — closed this gap myself with `tests/test_gitea_configure_api_verify.py`, which genuinely exercises the real branching logic. ✓ (now fully tested)
- **Item 41**: implemented (three locations + persisted `CODE_SERVER_BIN`) and thoroughly tested, including the sudoers/env-var agreement edge case the spec calls out explicitly. ✓
- **Item 42**: implemented (`SingletonActionError`, three `do_POST` branches) and thoroughly tested, including the "status" never-raises contract and all 4 existing status call sites (verified via the unchanged, still-green pre-existing test suites). ✓
- **Item 43**: implemented and thoroughly tested, including the two edge cases spec calls out (fallback unreachable on early success; fallback failure still exits 1 with the existing message). ✓

No acceptance criterion is unimplemented or untested.

## Findings (most severe first)

### 1. Item 40 had no automated test coverage from the developer for its second acceptance-criterion half — should-fix (already closed this pass)
- File: `scripts/gitea-configure-api.sh` (verification block, ~lines 142-160); `docs/implementation.md`'s "How to verify locally" section
- Issue: the developer's own implementation.md lists `bash -n scripts/gitea-configure-api.sh` as item 40's sole verification step — a syntax check that cannot exercise the actual HTTP-status/body branching the fix depends on (the 403+"must change" special case vs. any other non-200). The spec's own acceptance criteria explicitly require this behavior to be verifiable.
- Failure scenario (hypothetical, had this gone uncaught): a future refactor of the verification block silently breaks the 403 special-case (e.g. a typo in the `grep -qi "must change"` pattern, or the wrong exit path) and no test would catch it — the bug would only surface again on a live Gitea instance during the next E2E round.
- Resolution: reviewer wrote and ran `tests/test_gitea_configure_api_verify.py` this pass (4 cases, all green, revert-checked genuine against the pre-fix script) — the underlying implementation is confirmed correct. This is a process note for the developer (extract-verbatim-and-stub was cheap and already established twice this same cycle for `install.sh`), not a blocker, since the gap is now closed with passing tests.

### 2. `gitea-configure-api.sh`'s verification message is mildly confusing (not incorrect) on a pure connection failure — nit
- File: `scripts/gitea-configure-api.sh:146-147`
- Issue: on a genuine curl connection failure (e.g. Gitea not listening at all, curl exit 7), `curl -w '\n%{http_code}'` never appends a status line (curl only emits `-w` output after a completed transfer), so `VERIFY_STATUS="${VERIFY_RAW##*$'\n'}"` ends up holding curl's entire stderr text instead of an actual status code, producing a message like `Verification failed ... (HTTP curl: (7) Failed to connect to 127.0.0.1 port 3000: Connection refused).` Manually verified this exact case in a scratch script.
- Failure scenario: cosmetic only — the script still correctly exits 1 and prints the real curl error text right below via the `else` branch's "Output was:" line, so nothing is lost or misdiagnosed, it just reads oddly. Not in scope of spec's acceptance criteria (which only specify the 403 case and "still fails loudly either way").

### 3. `app/app.py`'s `CODE_SERVER_BIN` default eagerly evaluates `shutil.which()` even when the env var is set — nit
- File: `app/app.py:117-118`
- Issue: `os.environ.get("CODE_SERVER_BIN", shutil.which("code-server") or "/usr/local/bin/code-server")` — Python evaluates the second (default) argument unconditionally before calling `.get()`, so every process start pays for a `shutil.which()` PATH walk even when `CODE_SERVER_BIN` is already set in the environment (the common case on a fully-installed system, since install.sh now always persists it). Functionally harmless (confirmed by `test_explicit_env_var_still_wins_over_shutil_which` — the explicit value still wins), just a wasted lookup at import time, once, not per-request.

## Follow-ups (non-blocking)
- Consider `os.environ.get("CODE_SERVER_BIN") or shutil.which("code-server") or "/usr/local/bin/code-server"` to avoid the always-eager `shutil.which()` call (finding 3) — purely cosmetic/perf, not worth a dedicated cycle on its own.
- Open question already flagged in docs/spec.md (item 42): a dedicated inline error slot in the frontend for host/taiga/gitea toggle failures, instead of falling through to the generic `handleActionResult()` tail — explicitly deferred by spec's own Non-goals, still open for a future round if wanted.
- `mem-search`/claude-mem history was not consulted this pass — no `mem-search` CLI or MCP tool was reachable from this session's available tools; nothing in the diff or findings here suggested it was load-bearing (all findings are new, first-time-in-this-diff observations, not evidence of a recurring pattern), but flagging the gap in the checklist for transparency.

## Overall verdict
**Approve.** All five acceptance criteria (items 39-43) are correctly implemented, and every one now has real, revert-checked automated test coverage (one gap — item 40's diagnostic-message half — found and closed during this review pass, confirmed correct). Full regression suite is clean modulo the same 3 pre-existing environmental failures and 1 pre-existing environmental skip the developer already documented and this pass independently reproduced/confirmed via `git stash`. No must-fix or should-fix findings block approval — the two nits (connection-failure message wording, eager `shutil.which()`) are optional polish, not correctness or security issues.
