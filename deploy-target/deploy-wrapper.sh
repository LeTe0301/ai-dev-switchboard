#!/usr/bin/env bash
# The `authorized_keys` forced command for the `deploy` user (docs/spec.md
# part 2c-2a, "deploy-wrapper.sh"). Installed to
# /usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh and invoked by sshd
# for every connection authenticated with the matching key, regardless of
# what command the client asked for — sshd puts the client's own request
# into $SSH_ORIGINAL_COMMAND and runs *this* script instead (that's what
# `command="..."` in authorized_keys means). `no-pty` in that same
# authorized_keys line is a second, independent layer blocking interactive
# shell allocation even if the branching below were ever bypassed.
#
# Only ever does a literal `case` string match against
# $SSH_ORIGINAL_COMMAND — never `eval`s or otherwise re-interprets it. The
# actual rsync wire-protocol argument parsing (and the enforcement that no
# path outside DEPLOY_PATH can ever be written) is delegated entirely to
# rrsync itself, which reads $SSH_ORIGINAL_COMMAND on its own — this script
# doesn't hand-parse rsync's protocol arguments, by design (docs/spec.md
# "Edge cases" — that parsing is exactly what rrsync exists to do safely).
set -uo pipefail

CONFIG=/etc/ai-dev-switchboard/deploy-target.env
[ -f "$CONFIG" ] && source "$CONFIG"

case "${SSH_ORIGINAL_COMMAND:-}" in
    "rsync --server"*)
        # Fail closed: never call rrsync with an unset or malformed path.
        # A blank/relative DEPLOY_PATH would otherwise let rrsync run with
        # no root restriction at all (or a nonsensical relative one) —
        # refuse outright instead.
        if [ -z "${DEPLOY_PATH:-}" ] || [ "${DEPLOY_PATH:0:1}" != "/" ]; then
            echo "deploy-wrapper: DEPLOY_PATH is unset or not an absolute path — refusing to run rrsync. Check $CONFIG." >&2
            exit 1
        fi
        exec /usr/bin/rrsync -wo "$DEPLOY_PATH"
        ;;
    "deploy-restart")
        # A fixed protocol keyword, not a filesystem path or a secret — the
        # actual security boundary is possession of the SSH private key,
        # not knowledge of this string (docs/spec.md).
        exec sudo -n /usr/local/bin/ai-dev-switchboard-deploy-restart.sh
        ;;
    *)
        # Anything else, including no command at all (a bare interactive
        # `ssh deploy@target` attempt) — no shell, ever.
        echo "deploy-wrapper: command not permitted for this key." >&2
        exit 1
        ;;
esac
