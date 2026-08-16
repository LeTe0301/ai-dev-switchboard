# Spec: E2E round 7 — 5 fixes from the round-6 real-CT110 test (items 39-43)

## Summary
Fix five bugs found by a hands-on E2E test of branch `backlog/e2e-fixes-round6` @ `140a2ae` on a fresh Proxmox CT110 (docs/BACKLOG.md "Items 39-43"): a silently-ignored `AUTH_MODE` env var under `--yes`, a Gitea admin-bootstrap command that leaves the account unusable, a hardcoded code-server path that no-ops the whole feature, three singleton-toggle POST routes that lie about success, and one more attempt at closing out the still-flaky taiga-gateway startup race (item 30/43).

Same shape as rounds 4-6 (see `b855a1a`, `94f82f8`, `140a2ae`): a straight bugfix batch, no new features. **Skip ux-designer for this cycle** — items 39-42 are backend/install-script only with no user-facing surface change beyond an HTTP response body/status becoming more honest (the frontend's existing generic error handling already covers this, see item 42 below), and item 43 is a pure shell-script retry tweak. Go straight from this spec to developer.

## Goals
- Item 39: `install.sh --yes` (and interactive, for consistency) honors a pre-seeded `AUTH_MODE` in `switchboard.env`, defaulting to `simple` only when nothing is seeded — matching the `RUN_USER_DEFAULT`/`SVC_USER_DEFAULT` idiom already used earlier in the same script.
- Item 40: the Gitea admin-bootstrap command install.sh prints creates an account that is immediately usable by `gitea-configure-api.sh`, regardless of whether it happens to be Gitea's literal first-ever user.
- Item 41: toggling code-server on actually starts a process, on a fresh Debian 12 install where code-server.dev's installer puts the binary at `/usr/bin/code-server`, not `/usr/local/bin/code-server`.
- Item 42: `POST /host/on`, `/host/off`, `/taiga/on`, `/taiga/off`, `/gitea/on`, `/gitea/off` return a response that reflects whether the underlying action actually succeeded, with the real stderr surfaced on failure, instead of an unconditional `{"ok": true}`.
- Item 43: add the one concretely-suggested last-resort fallback step (a plain `docker compose up -d`, no `rm -f` first) to `scripts/taiga-up.sh`'s retry loop, on top of round 6's existing 5-attempt/backoff/settle-window logic, before the script gives up and exits 1.

## Non-goals
- Item 43: no redesign of the retry/backoff strategy itself, no attempt to pin down the actual root cause of the race (still unknown — round 6's own comment already says so), no changes to `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION`'s opt-in full-daemon-restart behavior.
- Item 42: no new frontend error UI for the host/taiga/gitea toggle rows. Unlike `deploy`/`smoke-check` (which have their own dedicated inline result slots in `handleActionResult()`), `kind === 'host'/'taiga'/'gitea'` has no such slot today. This spec makes the HTTP response honest (real status code + error body) but deliberately leaves it falling through to `handleActionResult()`'s existing generic tail (`hideCodeOverlay(); setTimeout(refresh, 1500)`) — the poll-driven `/status` reconciliation this item's own repro already relies on ("`GET /status` correctly reports `host: false` a moment later") keeps working unchanged. Adding a dedicated error slot for these three rows would be a real UI change and belongs in a future round if wanted — flagged under Open questions.
- Item 41: no change to *how* code-server gets installed (still the `curl -fsSL https://code-server.dev/install.sh | sh` one-liner) — only to how its binary location is subsequently detected/referenced.
- Item 40: no change to Gitea's own password-change-on-first-login security model — only to how install.sh's printed bootstrap command interacts with it for this one deliberately-not-first admin account.
- Item 39: no change to `pve` auth mode's own login logic — this is purely about the env-var default being honored under `--yes`.
- No regression sweep beyond what's needed to confirm items 22-38 (already fixed in prior rounds) aren't disturbed — reviewer's normal test pass covers that, not a special mandate here.

## Background / current state

### Item 39 — `AUTH_MODE` ignored under `--yes`
`install.sh` line 327:
```bash
AUTH_MODE=$(prompt "Auth mode: simple (username+password) or pve (Proxmox VE login)" "simple")
```
`prompt()` (defined ~line 151) returns its literal default argument whenever stdin isn't a TTY or `--yes` is set — it never consults `switchboard.env`. Contrast with `RUN_USER_DEFAULT` (line 237-239):
```bash
RUN_USER_DEFAULT="$(get_env "$ENV_FILE" RUN_USER)"; RUN_USER_DEFAULT="${RUN_USER_DEFAULT:-dev}"
RUN_USER=$(prompt "Unprivileged user to run coding sessions as" "$RUN_USER_DEFAULT")
```
which correctly seeds its default from any pre-existing `switchboard.env` value first. `ct/create.sh`'s automated provisioning path pre-seeds `AUTH_MODE=pve` + `PVE_HOST` before calling `install.sh --yes`, expecting exactly this — currently silently discarded, install always lands on `simple`.

### Item 40 — Gitea admin bootstrap 403
`install.sh` prints (~line 1006-1008):
```
docker exec --user git ai-dev-switchboard-gitea gitea admin user create \
  --admin --username <name> --password <password> --email <email>
```
Gitea's CLI defaults `--must-change-password=true` for any account that isn't literally Gitea's first-ever user. `scripts/gitea-configure-api.sh` (run right after, per install.sh's own printed step 2) mints a token for that account and verifies it with `GET /user` (lines 133-139); Gitea rejects with 403 + `"You must change your password"`, which the script's current error handling (lines 136-139) prints as a raw curl failure without explaining the real cause.

### Item 41 — code-server hardcoded path
Two hardcoded occurrences, both wrong on a real Debian 12 install (`code-server.dev`'s installer puts the binary at `/usr/bin/code-server`, confirmed live):
- `install.sh` line 231: `if [ "$WITH_CODE_SERVER" -eq 1 ] && [ ! -x /usr/local/bin/code-server ]; then` — idempotency check re-triggers the install every single run since it never finds the binary where it's actually looking.
- `app/app.py` line 117: `CODE_SERVER_BIN = os.environ.get("CODE_SERVER_BIN", "/usr/local/bin/code-server")` — used at line 738 to `subprocess.Popen(["sudo", "-u", RUN_USER, CODE_SERVER_BIN, ...])`, so the toggle silently no-ops (`_code_start()` captures nothing from the failed spawn — `stdout=DEVNULL, stderr=DEVNULL`).

A third occurrence that the BACKLOG entry doesn't mention but that the fix must also account for, found during this spec's own archaeology: `install.sh` line 568 writes a sudoers rule scoped to the exact literal binary path:
```bash
echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/code-server *"
```
`sudo -u "$RUN_USER"` only matches a sudoers rule against the exact command path invoked — if `CODE_SERVER_BIN` is fixed to resolve to `/usr/bin/code-server` but this sudoers line still only whitelists `/usr/local/bin/code-server`, the toggle would start *failing loudly* (permission denied) instead of silently no-op-ing, which is progress but still broken. This line must be kept in sync with whatever `install.sh` resolves as the real binary path.

### Item 42 — toggle POST routes lie about success
`app/app.py` — three near-identical subprocess wrappers, none check `returncode` or capture `stderr` for the caller:
```python
def host_run(action: str) -> str:              # line 2677
    ...
    r = subprocess.run([...], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

def taiga_run(action: str) -> str:              # line 2696
    ...
    r = subprocess.run(["sudo", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def gitea_run(action: str) -> str:              # line 2752
    ...
    r = subprocess.run(["sudo", script], capture_output=True, text=True, timeout=(...))
    return r.stdout.strip()
```
`do_POST` (lines 6259-6283) calls these and unconditionally replies `{"ok": True}`:
```python
if parts[0] == "host" and ... :
    host_run("start" if parts[1] == "on" else "stop")
    self._json({"ok": True})
elif parts[0] == "taiga" and ...:
    if parts[1] == "on":
        taiga_run("up"); _publish(...)
    else:
        _unpublish(...); taiga_run("down")
    self._json({"ok": True})
elif parts[0] == "gitea" and ...:               # same shape as taiga
    ...
    self._json({"ok": True})
```
All three functions are also called for `"status"` polling, in 4 places, which must keep working exactly as today (`out[0] == "on"` string parsing): `do_GET`'s `/status` handler (lines 5832, 5837, 5842) and `create_project()` (line 2006).

Existing tests (`tests/test_gitea.py`, `tests/test_taiga.py`) monkeypatch `appmod.gitea_run`/`appmod.taiga_run` as plain functions returning bare strings (e.g. `appmod.gitea_run = lambda action: "on"`) and call `appmod.gitea_run("status")` directly expecting a string back — any fix here must not force a wholesale rewrite of those existing mocks/assertions for the happy path.

### Item 43 — taiga-gateway retry still flaky
`scripts/taiga-up.sh` (round 6's fix, item 30 v2) already retries `docker compose up -d` + settle-window recheck + `rm -f taiga-gateway` + exponential backoff, up to `TAIGA_UP_MAX_ATTEMPTS` (default 5) times, then optionally (opt-in, default off) restarts the whole Docker daemon, then gives up:
```bash
while [ "$attempt" -le "$TAIGA_UP_MAX_ATTEMPTS" ]; do
    "${COMPOSE[@]}" up -d
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    ...
    if [ "$attempt" -lt "$TAIGA_UP_MAX_ATTEMPTS" ]; then
        "${COMPOSE[@]}" rm -f taiga-gateway >/dev/null 2>&1 || true
        sleep "$backoff"; backoff=$((backoff * 2))
    fi
    attempt=$((attempt + 1))
done
if [ "$TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION" -eq 1 ]; then ...; fi
echo "taiga-up: taiga-gateway failed to come up after $TAIGA_UP_MAX_ATTEMPTS attempts..." >&2
exit 1
```
Live repro (round 6 E2E, default config, `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` unset/0): all 5 attempts exhausted, script exited 1 — then a bare, manual `docker compose up -d taiga-gateway` (no flags, no `rm -f` first) immediately afterward succeeded in ~3s with clean logs. Root cause still not pinned down (round 6's own comment already says so); this item only adds the one concrete, cheap fallback step the tester's own repro points at.

## Proposed approach

### Item 39
In `install.sh`, replace line 327 with the same seed-from-env idiom `RUN_USER_DEFAULT`/`SVC_USER_DEFAULT` use:
```bash
AUTH_MODE_DEFAULT="$(get_env "$ENV_FILE" AUTH_MODE)"; AUTH_MODE_DEFAULT="${AUTH_MODE_DEFAULT:-simple}"
AUTH_MODE=$(prompt "Auth mode: simple (username+password) or pve (Proxmox VE login)" "$AUTH_MODE_DEFAULT")
```
No other lines need to change — `set_env "$ENV_FILE" AUTH_MODE "$AUTH_MODE"` right after it already persists whatever value is chosen.

### Item 40
1. In `install.sh`'s printed instructions (~line 1007-1008), add `--must-change-password=false` to the printed `gitea admin user create` command.
2. In `scripts/gitea-configure-api.sh`'s verification block (lines 133-139), special-case a 403 response so the real cause is surfaced instead of a bare curl error. `curl -fsS` on a non-2xx exits nonzero and `VERIFY_OUTPUT` contains curl's own error text, not Gitea's JSON body (the `-f` flag suppresses it) — switch that one call to drop `-f` (or add a separate diagnostic re-fetch without `-f` inside the failure branch) so the script can inspect the actual HTTP status/body and print a targeted message when it's a 403 with `"must change"` in the body, pointing at `gitea admin user change-password --must-change-password=false` as the fix, while still failing loudly (non-zero exit) either way.

### Item 41
Resolve the real code-server binary path once, at install time, and keep every reference (idempotency check, sudoers rule, `CODE_SERVER_BIN`) in sync with that single resolved value — this closes the sudoers gap identified in Background above, not just the two locations the BACKLOG entry names.

Recommended shape:
1. In `install.sh`, add a small helper (or inline logic) that resolves `command -v code-server` after the `--with-code-server` block's install step, falling back to `/usr/local/bin/code-server` only if `command -v` finds nothing (matches the pre-existing default so an already-working custom install isn't disturbed). Use that resolved path for:
   - The idempotency check at line 231 (`command -v code-server` in place of `[ ! -x /usr/local/bin/code-server ]`, since the goal there is "does a usable code-server already exist anywhere on PATH", not one specific location).
   - The sudoers line at 568 (write whatever path was actually resolved, not the hardcoded literal).
   - Persist it into `switchboard.env` as `CODE_SERVER_BIN` via `set_env` (same idiom already used for `NEW_PROJECT_FROM_URL_SCRIPT` etc.), so app.py's `os.environ.get("CODE_SERVER_BIN", ...)` picks up the real path without app.py itself needing to re-resolve anything at runtime.
2. In `app/app.py` line 117, additionally make the *default* (used when `CODE_SERVER_BIN` isn't set in the environment at all — e.g. an existing install upgrading without re-running install.sh) resolve via `shutil.which("code-server")` before falling back to the literal `/usr/local/bin/code-server` string, so an already-broken existing install self-heals on a plain code restart without requiring a full re-run of install.sh.

Either half alone is insufficient (env-only fixes fresh installs but not existing broken ones; `shutil.which`-only fixes app.py but leaves install.sh's idempotency check and the sudoers rule still pinned to the wrong path) — both are needed together.

### Item 42
Change `host_run`/`taiga_run`/`gitea_run` so a caller can distinguish success from failure without breaking the existing `"status"` call sites' string-parsing contract. Recommended shape (chosen specifically to minimize churn against the existing test mocks in `tests/test_gitea.py`/`tests/test_taiga.py`, which monkeypatch these three functions as plain callables returning bare strings):

- Keep the return type as `str` (the stripped stdout) for the `"status"` action — completely unchanged, so all 4 existing status call sites (`do_GET`'s `/status` handler lines 5832/5837/5842, `create_project()` line 2006) and every existing status-path test mock keep working with zero changes.
- For the non-`"status"` actions (`"start"/"stop"` for `host_run`, `"up"/"down"` for `taiga_run`/`gitea_run`), raise a small new exception (e.g. `SingletonActionError(Exception)`, carrying `.stderr`) when `r.returncode != 0`, instead of silently returning. Define it once, near `host_run`.
- In `do_POST`'s three branches (lines 6259-6283), wrap the `host_run("start"/"stop")` / `taiga_run("up"/"down")` / `gitea_run("up"/"down")` calls in `try/except SingletonActionError as e`, and on failure return `502` with `{"error": "...", "stderr": (e.stderr or "").strip()[-200:]}` — same truncate-to-200-chars-of-stderr precedent `deploy_run` already uses (`app.py` ~line 1874: `f"push failed: {(push.stderr or '').strip()[-200:]}"`). On success, keep returning `{"ok": True}` exactly as today.
- For `taiga`/`gitea` off, `_unpublish(...)` still runs before `taiga_run("down")`/`gitea_run("down")` is even called today — keep that ordering; only wrap the `_run` call itself in the try/except.
- Existing tests that monkeypatch these three functions for the failure path (if any use a fake `subprocess.run` returning nonzero) will need their fakes updated to actually raise `SingletonActionError` for non-status actions instead of just returning a string — call this out to the reviewer explicitly since it's the one place existing test behavior needs conscious updating, not just new tests added.

### Item 43
In `scripts/taiga-up.sh`, add exactly one fallback step between the retry loop's exhaustion and the final failure message — deliberately the tester's own suggested minimal step, not a broader redesign:
```bash
# ... existing while loop (lines 39-66) unchanged ...

# Item 43: round 6's retry loop still reproduced flaky under live testing --
# all attempts exhausted, but a bare `docker compose up -d` (no `rm -f`
# first) immediately afterward succeeded in ~3s. Root cause still not
# pinned down; this is the cheapest concrete fallback the live repro
# points at, tried before the (opt-in, heavier) full-Docker-daemon-restart
# path below and before giving up.
echo "taiga-up: all $TAIGA_UP_MAX_ATTEMPTS attempts exhausted -- trying one plain 'docker compose up -d' with no rm -f first, as a last resort before giving up" >&2
"${COMPOSE[@]}" up -d
state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
if [ "$state" = "running" ]; then
    exit 0
fi

if [ "$TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION" -eq 1 ]; then
    ... # unchanged
fi

echo "taiga-up: taiga-gateway failed to come up after $TAIGA_UP_MAX_ATTEMPTS attempts..." >&2
exit 1
```
Placed *before* the existing opt-in `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` block (not after) since it's strictly cheaper/safer (no host-wide side effect) and the live repro that motivates it never had that flag set — if this plain retry succeeds, the heavier opt-in path is never reached. No settle-window recheck on this one extra attempt (keep it simple, matching the tester's literal suggestion — round 6's settle-window logic can be revisited separately if this still isn't enough).

## Affected areas
- `install.sh` — items 39, 40, 41 (three separate, independent edits; no shared code path between them).
- `scripts/gitea-configure-api.sh` — item 40.
- `app/app.py` — items 41 (one default value) and 42 (three functions + their `do_POST` callers). Backend only, single file for item 42.
- `scripts/taiga-up.sh` — item 43.
- Tests likely needing updates or additions: `tests/test_install_set_env.py` or a new install.sh-focused test for item 39 (whichever existing harness already extracts install.sh snippets verbatim — see that file's own `_extract_between()` technique); `tests/test_gitea.py` for item 40's verification-message change; `tests/test_gitea.py`/`tests/test_taiga.py` for item 42 (both new failure-path coverage and updating any existing fakes that simulate a failed action); `tests/test_taiga_up_retry.py` for item 43 (it already runs the real `scripts/taiga-up.sh` end to end against a fake `docker`/`docker compose` — see its own module docstring).

This is a flat, single-layer batch (backend Python + shell scripts only, no schema/API/UI layers to split across) — the whole thing fits one developer dispatch, same as rounds 4-6.

## Edge cases
- Item 39: an empty/unset `AUTH_MODE` in an existing `switchboard.env` (fresh install, nothing seeded) must still default to `simple`, not empty string — covered by the `${AUTH_MODE_DEFAULT:-simple}` fallback.
- Item 40: the fix must not assume the printed command is the only way an admin account ever gets created — `gitea-configure-api.sh`'s special-cased 403 message is a diagnostic improvement for *whatever* account state produces that error, not solely the printed-command path.
- Item 41: an operator who already has a working custom code-server install at some other PATH-visible location must not be broken by this change (`command -v`-based resolution handles this naturally); an operator with no code-server on PATH at all (a fresh box, code-server disabled) must not have install.sh's idempotency check treat "not found" as an error — it should still just proceed to install, same as today.
- Item 41: the sudoers rule and `CODE_SERVER_BIN` must always agree on the exact same path — a mismatch after this fix would trade "silent no-op" for "permission denied," which is more debuggable but still broken; acceptance criteria below check this explicitly.
- Item 42: a `SingletonActionError` must never leak out of the `"status"` action path — a transient failed status check must keep degrading to "reported off" (today's self-correcting behavior), not start throwing 500s from `/status` or `create_project()`.
- Item 42: `taiga`/`gitea` off already calls `_unpublish()` before the run call — on a failed `_run("down")`, the singleton is already unpublished but the containers may still be up; this spec doesn't change that ordering/behavior, only the honesty of the HTTP response about the `_run` call itself.
- Item 43: the one extra fallback `up -d` must not run at all if the retry loop already succeeded within its normal attempts (i.e., must be unreachable on the already-covered `exit 0` paths inside the loop) — it only runs after the loop truly exhausts every attempt.
- Item 43: must not swallow the eventual failure — if even the extra fallback doesn't come up running, the script must still exit 1 with the existing loud failure message (falling through to the unchanged final `echo ... >&2; exit 1`), so item 42's now-honest `/taiga/on` response still correctly reports the failure end to end.

## Acceptance criteria
- [ ] **Item 39**: given `switchboard.env` pre-seeded with `AUTH_MODE=pve` and `PVE_HOST=<ip>` before running `install.sh --yes ...`, when install completes, then `switchboard.env` contains `AUTH_MODE=pve` (not `simple`) and the `pve` branch's prompts (`PVE_HOST`) are exercised. Given no pre-seeded `AUTH_MODE`, when running `install.sh --yes`, then it still defaults to `simple` exactly as before.
- [ ] **Item 40**: given the exact command install.sh now prints, when run against a fresh Gitea instance for an account that is *not* Gitea's first-ever user, then the account is created without `must_change_password` set, and a subsequent `scripts/gitea-configure-api.sh` run's `GET /user` verification succeeds without the 403 workaround. Given a token verification does hit a 403 with a "must change password" body (e.g. against an account created some other way), then `gitea-configure-api.sh`'s error output names the real cause and points at the `--must-change-password=false` fix, not just a bare curl error.
- [ ] **Item 41**: given a fresh install with `--with-code-server` on a host where the real binary lands at `/usr/bin/code-server` (not `/usr/local/bin/`), when the install completes and "Code" is toggled on for a project, then a real code-server process starts and `code_on` reports `true`. Given install.sh is re-run a second time in the same state, then it does not redundantly reinstall code-server (idempotency check correctly finds the already-installed binary). Given the resolved binary path, then the sudoers rule for `$SVC_USER ALL=($RUN_USER) NOPASSWD: ...` and `CODE_SERVER_BIN` both reference that same exact path.
- [ ] **Item 42**: given `POST /host/on` (or `/off`) with `HOST_CONTROL_KEY`/target unconfigured or otherwise failing, when the underlying `ssh`/script call exits nonzero, then the response is a non-200 status (502) with a JSON body containing a real `error` message and truncated `stderr`, not `{"ok": true}`. Given the same for `/taiga/on`, `/taiga/off`, `/gitea/on`, `/gitea/off`. Given a successful action for any of the six routes, then the response remains `200 {"ok": true}` exactly as today. Given `GET /status` or `POST /projects/new` (`create_project`'s Gitea-status check), then their behavior for the `"status"` action is completely unchanged (same string-parsing contract, same 4 call sites).
- [ ] **Item 43**: given `scripts/taiga-up.sh`'s retry loop exhausts all `TAIGA_UP_MAX_ATTEMPTS` attempts and taiga-gateway is still not running, when the script reaches its final fallback step, then it runs one plain `docker compose up -d` (no `rm -f` first) and re-checks state before declaring failure; if that fallback succeeds, the script exits 0. Given the fallback also fails, then the script still exits 1 with its existing failure message on stderr (so item 42's now-honest error propagation still reports the real end-to-end failure). Given the retry loop already succeeds within its normal attempts, then the fallback step is never reached (verify via `tests/test_taiga_up_retry.py`'s existing fake-docker-compose harness, extended with a new case for "all normal attempts fail, fallback succeeds").

## Open questions
- Item 42: should `host`/`taiga`/`gitea` toggle failures eventually get their own dedicated inline error slot in the frontend (like `deploy`/`smoke-check` already have), instead of falling through to the generic silent-refresh tail? Proceeding under the assumption that this round stays backend-only per the task's own framing ("no UI/design changes needed") — the response body is now honest and debuggable via devtools/logs even without a visible UI change, and the existing poll-driven reconciliation already surfaces the true state within one `/status` tick. Flagging this as a plausible future round if the human wants toggle failures visible in the UI itself.
- Item 40: exact wording of `gitea-configure-api.sh`'s special-cased 403 message is left to the developer's judgment — the acceptance criterion only requires that the real cause and fix are named, not exact copy.
- Item 41: whether to also backfill `CODE_SERVER_BIN` into an *already-installed* system's `switchboard.env` (i.e., should `install.sh --update`, if it re-runs any part of this block, force-overwrite an existing wrong value) is left to the developer — `set_env`'s existing idempotent-upsert behavior already handles this correctly (it overwrites), so no special-casing should be needed, but flagging it since item 41 was specifically about an existing-install-time bug.
- None of the above are blockers — proceeding straight to developer with the assumptions stated.

## Risk / rollback notes
- All five fixes are localized and independently revertable (five separate concerns across four files, no shared plumbing between them apart from item 41's own two-plus-one-location coupling, which is internal to that one item).
- Item 42 is the one with the widest blast radius (touches three shared subprocess wrapper functions used by both the mutating on/off routes and the read-only status polling), so its "status" call sites must be verified byte-for-byte behavior-identical, not just "probably fine" — called out explicitly in Edge cases and Acceptance criteria above.
- Item 43's fallback step adds at most one more `docker compose up -d` call (no sleep) to the already-long worst-case runtime of a full retry exhaustion — negligible relative to the existing ~220s timeout `taiga_run()` already budgets for the "up" action (`app/app.py` line 2733), no timeout-arithmetic changes needed.
- If item 42's exception-based approach turns out to be awkward in practice (e.g. the developer finds a cleaner shape), the acceptance criteria are written against observable HTTP behavior (status code + body content), not the internal exception-based mechanism — the mechanism described in "Proposed approach" is a strong recommendation made specifically to minimize test-mock churn, not a hard mandate.
