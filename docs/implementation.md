# Implementation: taiga-up.sh resilience from Proxmox E2E test round 4 (item 30)

## Summary
Rewrote `scripts/taiga-up.sh` (docs/BACKLOG.md item 30) to detect and
recover from a real, reproduced Docker Compose port-bind race that can
leave `taiga-gateway` (the Taiga stack's only public entrypoint) created
but never network-attached. Previously the script was a 6-line wrapper
that ran `docker compose up -d` and exec'd out with whatever exit code
Compose returned — nothing downstream inspected it, so one bad pass left
the feature silently, indefinitely wedged (correctly reported "off" by
`taiga-status.sh`, but with no automatic path back to "on"). The new
script attempts `up -d`, checks `taiga-gateway`'s resulting state via the
same `docker compose ps taiga-gateway --format '{{.State}}'` idiom
`taiga-status.sh` already uses, and — if not `running` — removes just
that one container and retries, up to `TAIGA_UP_MAX_ATTEMPTS` (default 3,
env-overridable) total attempts, before failing loudly with a specific,
actionable stderr message.

## Root cause
Not applicable in the "logic defect" sense — root cause of the underlying
transient port-bind race is explicitly not pinned down by the E2E tester
and is out of scope for this fix (see spec's Non-goals). What this fixes
is the *lack of detection and recovery*: `taiga-up.sh` had no state check
at all, so a wedged `taiga-gateway` container was indistinguishable from a
successful start as far as the script (and anything calling it) was
concerned.

## Changes by file
- `scripts/taiga-up.sh` — rewritten per `docs/spec.md`'s "Proposed
  approach" (implemented essentially verbatim, one addition — see
  "Deviations from spec" below):
  - Header comment explains the fix's reasoning (the port-bind race, why a
    bare `docker start` retry can't fix it, and the detect-then-retry-
    then-fail-loudly mechanism), replacing the old header's "no extra
    locking" note (superseded — the script is no longer just an idempotent
    one-shot `exec`).
  - `TAIGA_UP_MAX_ATTEMPTS="${TAIGA_UP_MAX_ATTEMPTS:-3}"` — new
    env-overridable tunable, matching this project's established
    `${VAR:-default}` convention.
  - `COMPOSE=(docker compose -f docker-compose.yml -f
    docker-compose.override.yml)` array, reused across all three Compose
    invocations (`up -d`, `ps taiga-gateway`, `rm -f taiga-gateway`)
    instead of the old single inline `exec`.
  - `while` loop: run `up -d`, check `taiga-gateway`'s state, `exit 0` on
    `running`. On any other state, print a stderr message naming the
    state and attempt count, then (only if attempts remain) `rm -f
    taiga-gateway` (the specific, wedged container only — never a full
    stack `down`, so the other 8 already-healthy containers are
    undisturbed) and `sleep 2` before the next attempt.
  - After the loop exits (all attempts exhausted): a final stderr message
    naming the container, the attempt count, and where to look next
    (`docker compose logs taiga-gateway`, `docker network ls`, disk
    space), then `exit 1`.
- `tests/test_taiga_up_retry.py` (new) — standalone test harness covering
  the retry/detection/failure logic (see "How to verify locally" below).

## Key decisions / tradeoffs
- Implemented the spec's "Proposed approach" code essentially verbatim —
  it was already near-final. One line added beyond the spec's literal
  code block: a `# shellcheck disable=SC1090` directive above the
  `source "$CONFIG"` line (see "Deviations from spec").
- Test harness follows the exact precedent `tests/test_create_enumerate_
  bridges.py` established this session: stub the one non-pure external
  dependency (`docker`) as a shell function (shell functions take
  priority over `PATH` executables, so no real Docker daemon or binary is
  needed) and run the *real* script, not a reimplementation. Since
  `taiga-up.sh` is the entire unit under test (unlike `_enumerate_
  bridges()`, which is one function inside a larger file), the harness
  reads the whole script's source verbatim from disk and appends it after
  the `docker`/`sleep` stub function definitions in one generated bash
  script, then runs that via `subprocess.run(["bash", "-c", ...])`.
- The `docker` stub tracks how many times `up -d` has been called via a
  counter *file* (not a shell variable) because `state=$(...)` is a
  command substitution, which forks a subshell — a plain variable
  increment inside the stub would not survive back to the next `ps` call
  in the same script. `rm -f taiga-gateway` calls are logged to a second
  file for the same reason, so the harness can assert exactly how many
  retries fired.
- `sleep` is also stubbed to a no-op. This is not part of the logic under
  test (it exists to avoid hammering a wedged Compose stack), and
  stubbing it keeps the exhaustion test fast (confirmed: full 4-test file
  runs in ~0.04s, not ~4s+, proving the stub is actually taking effect and
  not silently falling through to a real `sleep 2`).
- `TAIGA_DIR` is pointed at an empty temp directory per test — the stub
  never inspects its contents, only the script's own `cd "$TAIGA_DIR"`
  needs it to exist. `/etc/ai-dev-switchboard/switchboard.env` does not
  exist in this sandbox (confirmed directly), so the unconditional
  `CONFIG=/etc/ai-dev-switchboard/switchboard.env` line's `[ -f "$CONFIG"
  ] && source "$CONFIG"` is a safe no-op in the test environment, same as
  it is for every other script in `scripts/` that uses this exact idiom.
- Covered four cases: (1) `running` on attempt 1 → exit 0, zero `rm`/sleep
  overhead on the common path (spec's first acceptance criterion,
  explicit about "no unnecessary retry/sleep delay added to the normal
  path"); (2) recovers on attempt 2 after exactly one targeted `rm -f
  taiga-gateway`; (3) exhausts all `TAIGA_UP_MAX_ATTEMPTS` (default 3) and
  exits 1 with the exact expected stderr wording (container name, attempt
  count, and all three "look next" pointers); (4) confirms the
  `TAIGA_UP_MAX_ATTEMPTS` env override is honored (5 attempts, 4 retries)
  rather than only testing the default.

## Deviations from spec
- Added `# shellcheck disable=SC1090` above the `source "$CONFIG"` line,
  which is not present in the spec's literal code block. Without it,
  `shellcheck scripts/taiga-up.sh` reports one warning (`SC1090:
  ShellCheck can't follow non-constant source`) — a pre-existing,
  unaddressed warning present identically in `taiga-status.sh`,
  `gitea-up.sh`, and `taiga-down.sh` today (confirmed by running
  `shellcheck` against each). The spec's acceptance criteria explicitly
  require "`bash -n` / `shellcheck` clean," so I added the directive
  (matching the inline-suppression convention `ct/create.sh:359` already
  uses for a different warning) rather than leaving the new script with a
  warning the spec asked to avoid. This is a lint annotation only — no
  behavior change, and the sourcing logic is otherwise character-for-
  character identical to the spec's code block. I did not touch the other
  three scripts that share this same warning — out of scope for this fix.
- Nothing else deviates. `app/app.py`'s `taiga_run()` and
  `scripts/gitea-up.sh` were not touched, per the spec's Non-goals.

## Known limitations
- This can't be exercised against a real Docker daemon running a real
  Taiga stack in this sandbox (no Docker Compose plugin available here),
  consistent with `tests/test_taiga.py`'s own documented limitation for
  `taiga_run()`. What *is* fully covered by the new harness is the
  script's own control flow — the part with real risk of a bug (retry
  bound, which container gets removed, exit codes, exact stderr wording)
  — by running the real, unmodified script against a stubbed `docker`
  that simulates the reported failure mode precisely (state stays
  non-`running` until a configurable attempt number). This was not
  "untestable"; the same stub-the-one-external-command technique already
  proven in this session's `test_create_enumerate_bridges.py` worked
  without modification.
- The harness does not simulate `docker compose up -d` itself failing
  (non-zero exit) — the script's `set -uo pipefail` (no `-e`) means a
  failed `up -d` call falls through to the state check exactly like a
  successful-but-not-running one does, so this is already covered by the
  same code path the "exhausts all attempts" test exercises; a
  dedicated case wasn't added since it would exercise identical logic to
  an existing test, not new behavior.

## How to verify locally
```bash
# Syntax + lint, zero-warning baseline:
bash -n scripts/taiga-up.sh
shellcheck scripts/taiga-up.sh
# -> both exit 0, no warnings

# New retry/detection/failure-signal harness:
python3 tests/test_taiga_up_retry.py -v
# -> Ran 4 tests ... OK (succeeds-on-first-attempt, succeeds-after-one-
#    retry, exhausts-all-attempts-with-expected-stderr, and
#    TAIGA_UP_MAX_ATTEMPTS-env-override cases)

# Full existing suite -- no regressions:
python3 -m unittest discover -s tests
# -> Ran 1209 tests ... OK
```

All commands above were run during implementation: `bash -n` passed,
`shellcheck` reported zero warnings, the new 4-test harness passed on its
own (0.041s -- confirms the `sleep` stub is actually taking effect, not
silently falling through to a real 2s sleep), and the full suite
(including those 4 new tests, 1205 → 1209) passed with no failures or
errors.
