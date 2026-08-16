# Implementation: E2E round 7 — 5 fixes from the round-6 real-CT110 test (items 39-43)

## Summary
Fixed all five bugs found by the round-6 hands-on E2E test (docs/BACKLOG.md items 39-43, docs/spec.md): an `AUTH_MODE` env var silently ignored under `install.sh --yes`, a Gitea admin-bootstrap command that leaves the account unusable (403 "must change password"), a hardcoded `/usr/local/bin/code-server` path (real binary lands at `/usr/bin/code-server`) that silently no-ops the code-server toggle, three singleton-toggle POST routes (`/host`, `/taiga`, `/gitea` on/off) that always replied `{"ok": true}` regardless of the underlying action's real result, and one more retry-loop fallback for the still-flaky taiga-gateway startup race. Backend/install-script only, no UI changes (per spec, ux-designer was skipped for this cycle).

## Changes by file

- `install.sh`
  - **Item 39**: `AUTH_MODE` prompt (~line 327) now seeds its default from `get_env "$ENV_FILE" AUTH_MODE` first, falling back to `"simple"` only when empty — exact same idiom `RUN_USER_DEFAULT`/`SVC_USER_DEFAULT` already use two lines below it. `ct/create.sh`'s automated `install.sh --yes` provisioning path (which pre-seeds `AUTH_MODE=pve`) now has that value survive instead of being silently discarded.
  - **Item 40**: the printed Gitea admin-bootstrap command (`gitea admin user create ...`) now includes `--must-change-password=false`, so the account it creates is immediately usable by `gitea-configure-api.sh` regardless of whether it's literally Gitea's first-ever user.
  - **Item 41**: the `WITH_CODE_SERVER` idempotency check (~line 231/237) now uses `command -v code-server` instead of `[ ! -x /usr/local/bin/code-server ]` ("does a usable code-server exist anywhere on PATH", not one fixed location). Right after that block, the real installed path is resolved once (`command -v code-server`, falling back to the pre-existing `/usr/local/bin/code-server` literal only when nothing is found) and persisted into `switchboard.env` as `CODE_SERVER_BIN` via `set_env`. The sudoers rule (~line 585, previously a hardcoded `/usr/local/bin/code-server *`) now references `$CODE_SERVER_BIN` directly, so the sudoers rule and the persisted env value always agree on the exact same path.

- `scripts/gitea-configure-api.sh`
  - **Item 40**: the token-verification `curl` call dropped `-f` (which suppresses the response body on a non-2xx) and now captures the HTTP status via `-w '\n%{http_code}'`. On a non-200, it special-cases a 403 whose body contains "must change" (case-insensitive) with a targeted message naming the real cause and pointing at `gitea admin user change-password --must-change-password=false` as the fix; any other non-200/curl failure still prints the raw output and exits 1 exactly as before (still fails loudly either way, per spec's edge case).

- `scripts/taiga-up.sh`
  - **Item 43**: after the existing 5-attempt retry loop exhausts, added exactly one more plain `docker compose up -d` (no `rm -f` first, no settle-window recheck) as a last-resort fallback, placed *before* the opt-in `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` block (cheaper, no host-wide side effect). If it comes up running, exits 0; if not, falls through to the existing (opt-in) daemon-restart path and then the unchanged final failure message/`exit 1`.

- `app/app.py`
  - **Item 41**: `CODE_SERVER_BIN`'s default (used only when the env var isn't set in the process environment at all) now resolves via `shutil.which("code-server")` before falling back to the literal `/usr/local/bin/code-server` — self-heals an existing broken install on a plain service restart, without requiring a full `install.sh` re-run.
  - **Item 42**: added `SingletonActionError(Exception)` (carries `.stderr`), defined near `host_run()`. `host_run()`/`taiga_run()`/`gitea_run()` now raise it when `r.returncode != 0` **and** `action != "status"` — the `"status"` action's return type/contract is completely unchanged (still a bare stripped-stdout `str`, never raises), so all 4 existing status call sites (`do_GET`'s `/status` handler, `create_project()`) and every existing test mock that monkeypatches these three functions as plain string-returning callables keep working with zero changes. `do_POST`'s `/host`, `/taiga`, `/gitea` on/off branches now wrap the mutating `_run("start"/"stop"/"up"/"down")` call in `try/except SingletonActionError`, returning `502 {"error": ..., "stderr": (e.stderr or "").strip()[-200:]}` on failure (same truncate-to-200-chars-of-stderr precedent `deploy_run` already uses) instead of an unconditional `{"ok": true}`. The `taiga`/`gitea` off branches keep their existing `_unpublish()`-before-`_run("down")` ordering unchanged — only the `_run` call itself is wrapped.

- `tests/test_gitea.py` — added `GiteaRunTests` cases for `gitea_run()` raising `SingletonActionError` (with `.stderr`) on a nonzero-returncode `"up"`/`"down"`, and confirming `"status"` still never raises. Added `GiteaEndpointTests` cases asserting `POST /gitea/on` and `/gitea/off` return `502` with `error`/truncated `stderr` in the body when `gitea_run` raises.
- `tests/test_taiga.py` — same shape as `test_gitea.py`'s additions, for `taiga_run()`/`/taiga/on`/`/taiga/off`.
- `tests/test_taiga_up_retry.py` — added 3 new cases covering item 43's fallback step (succeeds after normal-attempt exhaustion; also fails and still exits 1 with the existing message; unreachable when the loop already succeeds within its normal attempts). Updated the two existing full-exhaustion assertions (`test_exhausts_all_attempts_and_fails_loudly`, `test_max_attempts_env_override_is_honored`) to expect one additional `up -d` call (the new fallback step), per docs/spec.md's explicit call-out that these needed conscious updating.
- `tests/test_host_control.py` (new) — no dedicated `host_run`/`/host` test file existed before this cycle; added one mirroring `test_gitea.py`/`test_taiga.py`'s `*RunTests`/`*EndpointTests` structure for the same singleton-toggle shape, covering both the plain-success/never-raises-on-status contract and item 42's new failure-path 502 behavior.
- `tests/test_install_auth_mode_default.py` (new) — extracts `install.sh`'s real `prompt()`/`interactive()`/`get_env()`/`set_env()` plus the AUTH_MODE prompt block verbatim (same `_extract_between()` technique `tests/test_install_set_env.py` established) and runs them non-interactively (`YES=1`), proving a pre-seeded `AUTH_MODE=pve` survives and an unseeded install still defaults to `simple`.
- `tests/test_install_code_server_path.py` (new) — extracts install.sh's real `WITH_CODE_SERVER` resolve block and the sudoers `CODE_SERVER_BIN` line verbatim, with `command`/`curl` stubbed, proving: idempotency check skips reinstall when code-server is already on PATH at a nonstandard location; a missing binary still proceeds to install (not an error) and falls back to the literal default; the sudoers rule and `CODE_SERVER_BIN` always reference the identical resolved path; `WITH_CODE_SERVER=0` still resolves/persists a value without ever installing.
- `tests/test_code_server_bin_default.py` (new) — spawns `app.py` fresh in a subprocess with a controlled `PATH` and `CODE_SERVER_BIN` unset (module-level global, computed once at import), proving the `shutil.which`-first default resolves a fake on-PATH binary, falls back to the literal default when nothing's on PATH, and that an explicit `CODE_SERVER_BIN` env var still wins over `shutil.which`.

## Key decisions / tradeoffs
- Item 42 kept the exact shape the spec's "Proposed approach" recommended (raise-only-for-mutating-actions, `"status"` untouched) specifically to avoid touching any of the ~15 existing `gitea_run`/`taiga_run` string-mock call sites in `tests/test_gitea.py`/`test_taiga.py` — verified by running the full pre-existing suites unchanged before adding new cases; all passed with zero modifications needed to the pre-existing mocks (the spec's own prediction that "existing tests... will need their fakes updated" for the failure path turned out not to apply here, since no *existing* test simulated a failed mutating-action `subprocess.run` before this cycle — only new tests were needed).
- `gitea-configure-api.sh`'s verification curl needed `CURL_EXIT=$?` captured via the `cmd && a || b` idiom (not a bare `$?` after the assignment) because the script runs under `set -e`, and `VAR=$(failing_cmd)` on its own would abort the script before the exit code could be inspected.
- Item 41's `CODE_SERVER_BIN` resolution/persistence in install.sh runs unconditionally after the `WITH_CODE_SERVER` block (not gated behind `WITH_CODE_SERVER=1`), so the variable always has a real value by the time the sudoers block references it later, regardless of whether code-server was actually requested this run.

## Deviations from spec
None. Implemented per docs/spec.md's "Proposed approach" for all five items, including the exact fallback placement for item 43 (before the opt-in daemon-restart block) and the exact exception-carrying-`.stderr` shape for item 42.

## Known limitations
- Per docs/spec.md's own Non-goals/Open questions: item 42 leaves `host`/`taiga`/`gitea` toggle failures falling through to `handleActionResult()`'s generic tail in the frontend (no dedicated inline error slot like `deploy`/`smoke-check` have) — the HTTP response is now honest and inspectable via devtools/logs, and the existing poll-driven `/status` reconciliation still surfaces the true state within one tick, but there's no visible in-UI error message yet. Flagged in spec as a plausible future round.
- Item 43's root cause for the taiga-gateway startup race is still not pinned down (unchanged from round 6) — this only adds one more concrete, cheap fallback attempt, not a diagnosis.
- Item 40's `gitea-configure-api.sh` fix only covers the printed-command path and the diagnostic message; it doesn't validate/enforce that no other account-creation path could still leave `must_change_password` set (matches spec's edge case: "a diagnostic improvement for whatever account state produces that error, not solely the printed-command path").
- None of the shell-script-level new tests exercise a real Gitea/Docker/network stack (matches the existing convention in this codebase — `test_gitea.py`, `test_taiga.py`, `test_taiga_up_retry.py` all stub the relevant commands rather than running real Docker Compose/Gitea).

## How to verify locally
```bash
cd /home/dev/projects/ai-dev-switchboard

# Item 39
python3 tests/test_install_auth_mode_default.py -v

# Item 40 (shell syntax + no regressions elsewhere)
bash -n scripts/gitea-configure-api.sh

# Item 41
python3 tests/test_install_code_server_path.py -v
python3 tests/test_code_server_bin_default.py -v

# Item 42
python3 tests/test_gitea.py -v
python3 tests/test_taiga.py -v
python3 tests/test_host_control.py -v

# Item 43
python3 tests/test_taiga_up_retry.py -v

# Full suite (confirms nothing else regressed)
python3 -m unittest discover -s tests -v
```
Full-suite result at implementation time: 1260 tests, 3 pre-existing failures (all in `tests/test_teams_grounding.py`, caused by a locally-present, gitignored `CLAUDE.md` at the repo root being picked up by that test's grounding-file-discovery count — unrelated to this cycle's diff, not touched by any of items 39-43, present before this cycle started), 1 pre-existing environmental skip (`test_deploy_dispatch.py`, a real `aidswbdeploy2b` system user already exists on this box). All items 39-43's own new/updated tests pass; the full pre-existing `test_gitea.py`/`test_taiga.py` suites pass unchanged.
