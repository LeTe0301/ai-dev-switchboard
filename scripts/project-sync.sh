#!/usr/bin/env bash
# Run via `sudo -u $RUN_USER` by a bare repo's post-receive hook after every
# push to main — keeps $PROJECTS_DIR/<name> (the web UI's working copy)
# always in sync automatically, no manual `git pull` step. Safe to rerun.
#
# Usage: project-sync.sh <name>
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/git-hosting.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi

DEST="${PROJECTS_DIR}/$1"
[ -d "$DEST/.git" ] || { echo "no working copy at $DEST yet, skipping sync" >&2; exit 0; }

cd "$DEST"
git fetch origin main
git reset --hard origin/main
