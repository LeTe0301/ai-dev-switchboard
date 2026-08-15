#!/usr/bin/env bash
# Root-run wrapper: stops the self-hosted Taiga docker compose stack, freeing
# the RAM it holds — see scripts/taiga-up.sh and docs/spec.md "Crossing the
# privilege boundary" for the full rationale (same zero-argument,
# hardcoded-$TAIGA_DIR shape).
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
TAIGA_DIR="${TAIGA_DIR:-/opt/ai-dev-switchboard-taiga}"

cd "$TAIGA_DIR" && exec docker compose -f docker-compose.yml -f docker-compose.override.yml down
