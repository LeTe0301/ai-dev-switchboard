#!/usr/bin/env bash
# Root-run wrapper: reports whether the Gitea stack is up, one word ("on" or
# "off") on the first line of stdout — same single-line contract
# host-agent/host-status.sh and taiga-status.sh already use (app.py's
# gitea_run() reads out[0] == "on", mirroring taiga_run()'s own out[0]
# check). Always queried fresh, never cached — see docs/spec.md "Background"
# for why Gitea's containers can't be trusted from app.py's own in-memory
# state the way code-server's subprocess.Popen handles are.
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
GITEA_DIR="${GITEA_DIR:-/opt/ai-dev-switchboard-gitea}"

cd "$GITEA_DIR" 2>/dev/null || { echo "off"; exit 0; }
state=$(docker compose ps server --format '{{.State}}' 2>/dev/null)
if [ "$state" = "running" ]; then
    echo "on"
else
    echo "off"
fi
