#!/usr/bin/env bash
# Root-run wrapper: starts the self-hosted Gitea docker compose stack (see
# docs/spec.md "Crossing the privilege boundary" — same reasoning as
# scripts/taiga-up.sh: Docker socket access is root-equivalent, so this is
# command-narrowing at the sudoers layer instead of RUN_USER/SVC_USER
# separation). Zero arguments, $GITEA_DIR hardcoded, same idiom
# scripts/new-project-from-upload.sh and taiga-up.sh already use: source
# switchboard.env if present, fall back to install.sh's own default path.
#
# Idempotent: `docker compose up -d` against an already-running stack is a
# no-op, matching how every other wrapper triplet in this codebase is
# already assumed idempotent with no extra locking anywhere.
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
GITEA_DIR="${GITEA_DIR:-/opt/ai-dev-switchboard-gitea}"

cd "$GITEA_DIR" && exec docker compose up -d
