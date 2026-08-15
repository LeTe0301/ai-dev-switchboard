#!/usr/bin/env bash
# One-time interactive setup for scripts/taiga_push_spec.py -- see
# docs/spec.md "Local backlog tracker (Taiga) -- part 1b: push a spec into
# Taiga". Run once, by RUN_USER, any time after a Taiga user + target
# project already exist (see docs/spec.md "Background" for why this can't
# be done at install.sh --with-taiga time -- no Taiga user account exists
# yet at that point).
#
# Follows install.sh's own prompt()/prompt_secret() idiom (show a default,
# accept an override) for consistency, but is self-contained -- does not
# source install.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.config/ai-dev-switchboard"
CONFIG_FILE="$CONFIG_DIR/taiga-push.env"

prompt() {  # prompt <message> <default> -> echoes the answer
    local msg="$1" def="$2" ans=""
    read -rp "$msg [$def]: " ans || true
    echo "${ans:-$def}"
}
prompt_secret() {  # prompt_secret <message> -> echoes the answer (may be empty), never echoed
    local msg="$1" ans=""
    read -rsp "$msg: " ans || true
    echo >&2
    echo "$ans"
}

echo "== Taiga push config setup =="
echo "Run once, after a Taiga user account and target project already exist"
echo "(create both through Taiga's own web UI first if you haven't yet)."
echo

TAIGA_URL=$(prompt "Taiga URL" "http://127.0.0.1:9000")
TAIGA_USERNAME=$(prompt "Taiga username" "")
TAIGA_PASSWORD=$(prompt_secret "Taiga password (input hidden)")
TAIGA_PROJECT_SLUG=$(prompt "Target Taiga project slug" "")

mkdir -p "$CONFIG_DIR"
# umask 077 first so the file is never created world/group-readable even for
# the instant before chmod runs below -- belt-and-suspenders with the
# explicit chmod, so there's no window where a file with a live password in
# it is briefly more permissive than its final mode.
(
    umask 077
    {
        echo "TAIGA_URL=$TAIGA_URL"
        echo "TAIGA_USERNAME=$TAIGA_USERNAME"
        echo "TAIGA_PASSWORD=$TAIGA_PASSWORD"
        echo "TAIGA_PROJECT_SLUG=$TAIGA_PROJECT_SLUG"
    } > "$CONFIG_FILE"
)
chmod 600 "$CONFIG_FILE"

echo
echo "Wrote $CONFIG_FILE (mode 600)."
echo "Verifying (authenticate + look up the project -- no userstory is created)..."
echo

if python3 "$REPO_ROOT/scripts/taiga_push_spec.py" --config "$CONFIG_FILE" --verify; then
    echo
    echo "Setup verified. You can now run: python3 scripts/taiga_push_spec.py"
    exit 0
else
    echo
    echo "Verification failed -- re-run this script with the correct URL/username/password/project slug, or see the message above." >&2
    exit 1
fi
