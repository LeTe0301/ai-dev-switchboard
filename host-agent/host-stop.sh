#!/usr/bin/env bash
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/host.env
[ -f "$CONFIG" ] && source "$CONFIG"

SESSION="${HOST_SESSION:-ai-dev-switchboard-host}"
STATE_DIR="${HOST_STATE_DIR:-/var/lib/ai-dev-switchboard}"
STATUS_FILE="$STATE_DIR/host-status"
URL_FILE="$STATE_DIR/host-url"

mkdir -p "$STATE_DIR"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
fi
echo "off" > "$STATUS_FILE"
rm -f "$URL_FILE"
echo "stopped"
