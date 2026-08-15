#!/usr/bin/env bash
# Root-run wrapper: starts the self-hosted Taiga docker compose stack (see
# docs/spec.md "Crossing the privilege boundary" — Docker socket access is
# root-equivalent, so this is command-narrowing at the sudoers layer instead
# of RUN_USER/SVC_USER separation). Zero arguments, $TAIGA_DIR hardcoded,
# same idiom scripts/new-project-from-upload.sh already uses: source
# switchboard.env if present, fall back to install.sh's own default path.
#
# Idempotent: `docker compose up -d` against an already-running stack is a
# no-op, matching how host-agent's start/stop/status triplet is already
# assumed idempotent with no extra locking anywhere in this codebase.
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
TAIGA_DIR="${TAIGA_DIR:-/opt/ai-dev-switchboard-taiga}"

cd "$TAIGA_DIR" && exec docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
