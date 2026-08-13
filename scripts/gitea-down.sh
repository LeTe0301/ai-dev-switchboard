#!/usr/bin/env bash
# Root-run wrapper: stops the self-hosted Gitea docker compose stack, freeing
# the RAM it holds — see scripts/gitea-up.sh and docs/spec.md "Crossing the
# privilege boundary" for the full rationale (same zero-argument,
# hardcoded-$GITEA_DIR shape).
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
GITEA_DIR="${GITEA_DIR:-/opt/ai-dev-switchboard-gitea}"

cd "$GITEA_DIR" && exec docker compose down
