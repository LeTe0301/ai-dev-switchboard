# Spec: taiga-up.sh resilience from Proxmox E2E test round 4 (item 30)

## Summary
Last item from the Proxmox E2E test (`docs/BACKLOG.md` item 30):
`taiga-up.sh` has zero resilience to a real, reproduced Docker Compose
port-bind race that can leave `taiga-gateway` (the stack's only public
entrypoint) permanently network-less after a transient failure. Root cause
of the *initial* failure is explicitly NOT fully pinned down by the E2E
tester (flagged honestly rather than guessed at) — this spec does not
attempt to fix that. What it does fix: `taiga-up.sh` currently has no way
to detect this happened, and no retry — one bad pass leaves the feature
silently, indefinitely broken (correctly reported as "off," not a lying
UI, but with no automatic path back to "on"). This is a mitigation for the
*symptom* (a wedged container after a failed attempt), not a fix for
whatever transient condition first triggers it.

## Orchestrator note
No product-manager/ux-designer dispatch. Unlike rounds 1-3, this one does
involve a real (if narrow) design judgment call — the retry bound and
backoff shape — but the underlying mechanism (detect-then-retry-then-fail-
loudly) is fully specified by the E2E report and reuses an already-shipped
detection pattern (`scripts/taiga-status.sh`'s own `docker compose ps
taiga-gateway --format '{{.State}}'` check), so this stays a developer+
reviewer cycle, not a full triage.

## Background
Current `scripts/taiga-up.sh` (13 lines): sources config, `cd`s into
`$TAIGA_DIR`, and execs one `docker compose up -d` — no state check, no
retry, no failure signal beyond whatever exit code `docker compose`
itself returns (which nothing downstream currently inspects).

`scripts/taiga-status.sh` already establishes the exact detection idiom to
reuse:
```bash
state=$(docker compose -f docker-compose.yml -f docker-compose.override.yml \
    ps taiga-gateway --format '{{.State}}' 2>/dev/null)
if [ "$state" = "running" ]; then echo "on"; else echo "off"; fi
```

`app/app.py`'s `taiga_run("up")` calls this script with a fixed 90-second
timeout and does not currently inspect its exit code or stderr (only
`stdout.strip()` is used, and nothing today captures stdout from
`taiga-up.sh` — its only output today would be `docker compose`'s own
noise). This spec's retry loop must fit comfortably inside that 90s
budget.

## Proposed approach
Rewrite `scripts/taiga-up.sh` to attempt `docker compose up -d`, check
`taiga-gateway`'s resulting state via the same idiom `taiga-status.sh`
already uses, and — if not `running` — remove just the gateway container
and retry, up to a bounded number of attempts, before failing loudly
(non-zero exit, a clear stderr message) rather than silently leaving
things broken:

```bash
#!/usr/bin/env bash
# Root-run wrapper: starts the self-hosted Taiga docker compose stack (see
# docs/spec.md "Crossing the privilege boundary" -- Docker socket access is
# root-equivalent, so this is command-narrowing at the sudoers layer instead
# of RUN_USER/SVC_USER separation).
#
# Idempotent: `docker compose up -d` against an already-running stack is a
# no-op. Item 30 (docs/BACKLOG.md): a real, reproduced Docker Compose
# port-bind race can leave taiga-gateway -- the stack's only public
# entrypoint -- created but never network-attached, and a bare `docker
# start` retry never re-attempts the network-connect step (only a full
# remove+recreate does). Detects this via the same `docker compose ps
# taiga-gateway --format '{{.State}}'` idiom scripts/taiga-status.sh
# already uses, and retries a bounded number of times before failing
# loudly -- this does not fix whatever transient condition first triggers
# the race (root cause wasn't pinned down), it makes one bad attempt
# recoverable instead of silently, indefinitely wedging the feature.
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
TAIGA_DIR="${TAIGA_DIR:-/opt/ai-dev-switchboard-taiga}"
TAIGA_UP_MAX_ATTEMPTS="${TAIGA_UP_MAX_ATTEMPTS:-3}"

cd "$TAIGA_DIR" || { echo "taiga-up: $TAIGA_DIR not found" >&2; exit 1; }

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)

attempt=1
while [ "$attempt" -le "$TAIGA_UP_MAX_ATTEMPTS" ]; do
    "${COMPOSE[@]}" up -d
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    if [ "$state" = "running" ]; then
        exit 0
    fi
    echo "taiga-up: taiga-gateway didn't come up cleanly (state: ${state:-<none>}), attempt $attempt/$TAIGA_UP_MAX_ATTEMPTS" >&2
    if [ "$attempt" -lt "$TAIGA_UP_MAX_ATTEMPTS" ]; then
        "${COMPOSE[@]}" rm -f taiga-gateway >/dev/null 2>&1 || true
        sleep 2
    fi
    attempt=$((attempt + 1))
done

echo "taiga-up: taiga-gateway failed to come up after $TAIGA_UP_MAX_ATTEMPTS attempts -- manual intervention needed (check 'docker compose logs taiga-gateway' in $TAIGA_DIR, 'docker network ls', and available disk space)." >&2
exit 1
```

Key decisions, stated explicitly since this is the one real judgment call
in this round:
- **`TAIGA_UP_MAX_ATTEMPTS=3` default, env-overridable** — matches this
  project's own established convention (every tunable elsewhere in this
  codebase is an `os.environ.get(...)`-with-default in Python or
  `${VAR:-default}` in bash, always overridable, never hardcoded silently).
  3 attempts × (a `docker compose up -d` call, typically a few seconds for
  an already-mostly-up stack, + a 2s sleep between retries) comfortably
  fits inside `taiga_run()`'s existing 90-second timeout in `app/app.py`
  — do not change that timeout as part of this fix.
- **`rm -f taiga-gateway` between attempts, not a full stack `down`** —
  narrowest retry that still forces Compose's full create→connect-
  network→publish-ports sequence to run again for the one broken
  container, without disrupting the other 8 already-healthy containers
  (Postgres, RabbitMQ, etc.) on every retry.
- **Exit non-zero with a specific, actionable stderr message on final
  failure** — today's script has no failure signal at all beyond
  whatever `docker compose` itself returns (unobserved by any caller).
  This doesn't change `taiga_run()`'s current behavior of not surfacing
  script stderr to the web UI (out of scope for this fix — see
  Non-goals) but does mean `journalctl`/direct script invocation now
  gives an operator a real, specific signal instead of silence.

## Non-goals
- **Fixing the underlying transient port-bind race's root cause.** Not
  pinned down by the E2E tester, not guessed at here. This is a mitigation
  for the resulting wedged state, not a fix for whatever triggers it.
- **Changing `app/app.py`'s `taiga_run()`** to surface `taiga-up.sh`'s
  stderr to the web UI, or to change its 90s timeout. A real UX
  improvement, but the E2E report scoped this fix to the shell script
  itself; a caller-side surfacing change is a separate, larger decision
  (does every `taiga_run()` caller in the UI want a verbose failure
  reason shown, or just the existing on/off toggle state?) not asked for
  here.
- **A pre-flight `df` check before `docker compose up`** — a different,
  already-tracked follow-up (mentioned alongside item 31/`DEFAULT_DISK_GB`
  in the E2E report), not part of this item's own scoped fix.
- **`gitea-up.sh`** — the E2E report's own retry suggestion is specific to
  Taiga's 9-container, multi-network-attachment stack; Gitea's 2-container
  stack was not reported as hitting this race, and applying the same
  pattern there without a reported failure to fix would be speculative.

## Acceptance criteria
- [ ] Given `taiga-gateway` comes up `running` on the first `docker
      compose up -d` (the common case), the script exits 0 immediately —
      no unnecessary retry/sleep delay added to the normal path.
- [ ] Given the first attempt leaves `taiga-gateway` in a non-`running`
      state, the script removes just that container and retries, up to
      `TAIGA_UP_MAX_ATTEMPTS` total attempts.
- [ ] Given all attempts fail, the script exits non-zero with a specific
      stderr message naming the container, the attempt count, and where
      to look next (compose logs, docker network state, disk space) —
      not a bare failure with no explanation.
- [ ] `TAIGA_UP_MAX_ATTEMPTS` is read from the environment with a default
      of `3`, matching this project's own established tunable-constant
      convention.
- [ ] `bash -n` / `shellcheck` clean.

## Affected areas
`scripts/taiga-up.sh` only. No Python/JS changes.

## Risk / rollback notes
Low risk: the retry logic only runs additional `docker compose` calls
against a stack that's already in a broken state (never touches a
healthy stack beyond the one now-conditional `rm -f` on the specific
already-non-running container). Worst case of a bug here is the same
"stays broken, reported as off" behavior the script already has today —
not a new failure mode, not data loss (Postgres/RabbitMQ data volumes are
untouched by this script regardless). Plain `git revert` if anything
regresses.
