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
# shellcheck disable=SC1090
[ -f "$CONFIG" ] && source "$CONFIG"
TAIGA_DIR="${TAIGA_DIR:-/opt/ai-dev-switchboard-taiga}"
TAIGA_UP_MAX_ATTEMPTS="${TAIGA_UP_MAX_ATTEMPTS:-5}"
TAIGA_UP_RETRY_BACKOFF_SECONDS="${TAIGA_UP_RETRY_BACKOFF_SECONDS:-10}"
# Item 30: the original success check was a single point-in-time read right
# after `up -d` returns -- round 5 saw the gateway report "running" for
# under a second before crashing. Sleep this long and re-check before
# trusting a "running" read; a die-before-settled is treated as a failed
# attempt, same as any other (docs/spec.md "Proposed approach" Item 30).
TAIGA_UP_SETTLE_SECONDS="${TAIGA_UP_SETTLE_SECONDS:-5}"
# Item 30 (v2): a full `systemctl restart docker` was the only 100%-
# reliable recovery found on the verification host, but it restarts
# EVERY Docker container on this machine, not just Taiga's -- a real,
# host-wide side effect this project doesn't take automatically/
# unattended anywhere else without an explicit operator decision.
# Default OFF; an operator who's confirmed this is safe on their own host
# (e.g. nothing else Docker-based shares it) can opt in.
TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION="${TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION:-0}"

cd "$TAIGA_DIR" || { echo "taiga-up: $TAIGA_DIR not found" >&2; exit 1; }

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)

attempt=1
backoff="$TAIGA_UP_RETRY_BACKOFF_SECONDS"
while [ "$attempt" -le "$TAIGA_UP_MAX_ATTEMPTS" ]; do
    "${COMPOSE[@]}" up -d
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    if [ "$state" = "running" ]; then
        sleep "$TAIGA_UP_SETTLE_SECONDS"
        state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
        if [ "$state" = "running" ]; then
            exit 0
        fi
        echo "taiga-up: taiga-gateway was running but died within the ${TAIGA_UP_SETTLE_SECONDS}s settle window (state: ${state:-<none>}), attempt $attempt/$TAIGA_UP_MAX_ATTEMPTS" >&2
    else
        echo "taiga-up: taiga-gateway didn't come up cleanly (state: ${state:-<none>}), attempt $attempt/$TAIGA_UP_MAX_ATTEMPTS" >&2
    fi
    if [ "$attempt" -lt "$TAIGA_UP_MAX_ATTEMPTS" ]; then
        "${COMPOSE[@]}" rm -f taiga-gateway >/dev/null 2>&1 || true
        sleep "$backoff"
        backoff=$((backoff * 2))
    fi
    attempt=$((attempt + 1))
done

# Item 43 (round 7): round 6's retry loop still reproduced flaky under live
# testing on a fresh CT110 -- all attempts exhausted, but a bare `docker
# compose up -d` (no `rm -f` first) run manually right afterward succeeded
# in ~3s with clean logs. Root cause still not pinned down; this is the
# cheapest concrete fallback the live repro points at, tried before the
# (opt-in, heavier, host-wide) full-Docker-daemon-restart path below and
# before giving up. No settle-window recheck on this one extra attempt --
# keep it simple, matching the live repro's own literal suggestion.
echo "taiga-up: all $TAIGA_UP_MAX_ATTEMPTS attempts exhausted -- trying one plain 'docker compose up -d' with no rm -f first, as a last resort before giving up" >&2
"${COMPOSE[@]}" up -d
state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
if [ "$state" = "running" ]; then
    exit 0
fi

if [ "$TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION" -eq 1 ]; then
    echo "taiga-up: all $TAIGA_UP_MAX_ATTEMPTS attempts exhausted -- TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION=1, restarting the Docker daemon itself (affects every container on this host) and trying once more" >&2
    systemctl restart docker
    sleep 5
    "${COMPOSE[@]}" up -d
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    if [ "$state" = "running" ]; then
        exit 0
    fi
fi

echo "taiga-up: taiga-gateway failed to come up after $TAIGA_UP_MAX_ATTEMPTS attempts -- manual intervention needed (check 'docker compose logs taiga-gateway' in $TAIGA_DIR, 'docker network ls', available disk space, or set TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION=1 to let this script restart the Docker daemon itself as a last resort next time)." >&2
exit 1
