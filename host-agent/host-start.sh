#!/usr/bin/env bash
# Spawn a persistent engine session directly on this machine, in a detached
# tmux session — controlled remotely by the switchboard's optional "host
# row" (see HOST_CONTROL_ENABLED in config/switchboard.env.example).
# Idempotent: if the session's already running, refreshes what it safely
# can and returns.
#
# This script (and host-stop.sh / host-status.sh) run ON the controlled
# machine over the narrow SSH channel the web UI uses — they read their own
# config from /etc/ai-dev-switchboard/host.env (see host.env.example),
# separate from the web UI's own switchboard.env, since this machine isn't
# necessarily the one running the web UI at all.
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/host.env
[ -f "$CONFIG" ] && source "$CONFIG"

SESSION="${HOST_SESSION:-ai-dev-switchboard-host}"
WORKDIR="${HOST_WORKDIR:-$HOME}"
STATE_DIR="${HOST_STATE_DIR:-/var/lib/ai-dev-switchboard}"
STATUS_FILE="$STATE_DIR/host-status"
URL_FILE="$STATE_DIR/host-url"
ENGINES_DIR="${ENGINES_DIR:-/etc/ai-dev-switchboard/engines.d}"
ENGINE="${HOST_ENGINE:-claude}"
ENGINE_FILE="$ENGINES_DIR/${ENGINE}.engine"
SESSION_NAME_ARG="${HOST_SESSION_NAME:-host}"
TIMEOUT="${STARTUP_TIMEOUT_SECONDS:-45}"

# readlink -f (not just dirname "${BASH_SOURCE[0]}"): install.sh installs
# this as a symlink under /usr/local/bin pointing back at the real copy
# under /opt, so the naive form would look for lib/ next to the symlink
# and miss it.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/engine-lib.sh
source "$SCRIPT_DIR/lib/engine-lib.sh"

mkdir -p "$STATE_DIR"

if [ ! -f "$ENGINE_FILE" ]; then
    echo "no such engine '$ENGINE' ($ENGINE_FILE not found)" >&2
    exit 1
fi
CMD_TEMPLATE=$(engine_field "$ENGINE_FILE" "CMD") || { echo "engine '$ENGINE' has no CMD set" >&2; exit 1; }
CMD="${CMD_TEMPLATE//\{name\}/$SESSION_NAME_ARG}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    # Already running — grab whatever URL is on screen right now as a
    # bonus (harmless, read-only). If nothing's visible AND the cached
    # URL_FILE predates this tmux session, it's definitely stale (left over
    # from an earlier incarnation of this session) — drop it rather than
    # keep serving a link we know is wrong. Never send keystrokes here:
    # this session may be mid-use by whoever's actually working in it.
    url_regex=$(engine_field "$ENGINE_FILE" "URL_REGEX") || url_regex=""
    m=""
    if [ -n "$url_regex" ]; then
        m=$(tmux capture-pane -t "$SESSION" -p | grep -oE "$url_regex" | tail -1)
    fi
    if [ -n "$m" ]; then
        printf '%s\n' "$m" > "$URL_FILE"
    else
        session_created=$(tmux display-message -t "$SESSION" -p '#{session_created}')
        file_mtime=$(stat -c %Y "$URL_FILE" 2>/dev/null || echo 0)
        if [ "$session_created" -gt "$file_mtime" ]; then
            rm -f "$URL_FILE"
        fi
    fi
    echo "on" > "$STATUS_FILE"
    echo "already running"
    exit 0
fi

tmux new-session -d -s "$SESSION" -c "$WORKDIR"
tmux send-keys -t "$SESSION" "$CMD" Enter

# run_startup_watch always writes (or clears) URL_FILE itself at the end,
# regardless of whether it fully succeeded — see lib/engine-lib.sh. That's
# what keeps this idempotent: a slow/timed-out engine can't leave stale
# state sitting around to be misread as current on the next run.
run_startup_watch "$SESSION" "$ENGINE_FILE" "$URL_FILE" "$TIMEOUT"

echo "on" > "$STATUS_FILE"
if [ -s "$URL_FILE" ]; then
    echo "started: $(cat "$URL_FILE")"
else
    echo "started (no hosted link captured — engine may have none, or startup took longer than ${TIMEOUT}s; check: tmux attach -t $SESSION)"
fi
