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
