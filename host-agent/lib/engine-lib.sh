#!/usr/bin/env bash
# Shared helpers for reading *.engine files from bash. Mirrors
# app/app.py's _parse_engine_file: plain KEY=value lines, "#" comments and
# blank lines ignored, values never shell-evaluated (an engine file is
# just data on both the Python and the bash side — safe to author without
# worrying about accidental code execution).
#
# Meant to be sourced, not run directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/engine-lib.sh"

# engine_field <file> <KEY> — prints the value, or fails (rc=1) if absent.
engine_field() {
    local file="$1" key="$2" line k v
    while IFS= read -r line; do
        case "$line" in
            \#*|"") continue ;;
        esac
        case "$line" in
            *=*) ;;
            *) continue ;;
        esac
        k="${line%%=*}"
        v="${line#*=}"
        k="$(printf '%s' "$k" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        if [ "$k" = "$key" ]; then
            printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
            return 0
        fi
    done < "$file"
    return 1
}

# run_startup_watch <session> <engine_file> <url_file> [<timeout_seconds>]
#
# Works through the engine's STARTUP_MATCH_N/STARTUP_SEND_N pairs in order
# (e.g. clearing Claude Code's one-time "trust this folder" prompt), and —
# if the engine defines URL_REGEX — opportunistically captures its hosted
# link along the way. Polls the pane once a second for up to <timeout>
# seconds (default 45), exiting early once the startup script is exhausted
# and (if applicable) a URL has been captured.
#
# Always writes url_file at the end: the captured URL if one was found, or
# removes the file entirely if not — regardless of whether the loop above
# timed out or completed cleanly. A partial/timed-out run must never leave
# a *stale* file (from some earlier run) sitting there to be misread as
# still current — that was the switchboard's one shipped bug (see
# docs/ARCHITECTURE.md's "known sharp edges" section) and this function
# exists specifically so both host-start.sh's own runs and any future
# engine-startup logic share the fix instead of re-diverging.
run_startup_watch() {
    local session="$1" engine_file="$2" url_file="$3" timeout="${4:-45}"
    local url_regex
    url_regex=$(engine_field "$engine_file" "URL_REGEX") || url_regex=""

    local -a matches=() sends=()
    local i=1 m s
    while true; do
        m=$(engine_field "$engine_file" "STARTUP_MATCH_$i") || break
        s=$(engine_field "$engine_file" "STARTUP_SEND_$i") || break
        matches+=("$m"); sends+=("$s")
        i=$((i + 1))
    done

    local idx=0 url="" pane n
    for ((n = 0; n < timeout; n++)); do
        sleep 1
        pane=$(tmux capture-pane -t "$session" -p)
        if [ -n "$url_regex" ]; then
            m=$(printf '%s' "$pane" | grep -oE "$url_regex" | tail -1)
            [ -n "$m" ] && url="$m"
        fi
        if [ "$idx" -lt "${#matches[@]}" ]; then
            case "$pane" in
                *"${matches[$idx]}"*)
                    tmux send-keys -t "$session" "${sends[$idx]}" Enter
                    idx=$((idx + 1))
                    continue
                    ;;
            esac
        fi
        if [ "$idx" -ge "${#matches[@]}" ] && { [ -z "$url_regex" ] || [ -n "$url" ]; }; then
            break
        fi
    done

    if [ -n "$url" ]; then
        printf '%s\n' "$url" > "$url_file"
    else
        rm -f "$url_file"
    fi
}
