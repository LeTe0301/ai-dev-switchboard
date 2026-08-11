#!/usr/bin/env bash
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/host.env
[ -f "$CONFIG" ] && source "$CONFIG"

SESSION="${HOST_SESSION:-ai-dev-switchboard-host}"
STATE_DIR="${HOST_STATE_DIR:-/var/lib/ai-dev-switchboard}"
URL_FILE="$STATE_DIR/host-url"

# Status is always derived live from tmux, never from a cached file — a
# cached on/off flag can't self-heal if the session dies on its own
# (crash, `/exit`, `tmux kill-session` from inside). Only the URL, which
# tmux has no notion of, needs a cache at all.
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "on"
    [ -f "$URL_FILE" ] && cat "$URL_FILE"
else
    echo "off"
fi
