#!/usr/bin/env bash
# One-time token bootstrap for Gitea's REST API (see docs/spec.md "backlog
# item 2b" -- "scripts/gitea-configure-api.sh (new) -- one-time token
# bootstrap"). Run once, as ROOT (unlike scripts/taiga-configure-push.sh,
# which runs as RUN_USER), after Gitea's own admin account already exists
# (install.sh's own printed instructions cover that first step).
#
# Mints a scoped Personal Access Token non-interactively via Gitea's own
# CLI -- no admin password is ever prompted for or handled by this script --
# and writes it into /etc/ai-dev-switchboard/switchboard.env as
# GITEA_API_TOKEN, which app.py (running as SVC_USER) reads to create repos
# through create_project(). Safe to re-run: generates a fresh token and
# overwrites the old one every time (useful for rotation).
#
# Usage: sudo scripts/gitea-configure-api.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root (sudo scripts/gitea-configure-api.sh)." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE=/etc/ai-dev-switchboard/switchboard.env

interactive() { [ -t 0 ]; }
prompt() {  # prompt <message> <default> -> echoes the answer
    local msg="$1" def="$2" ans=""
    if interactive; then read -rp "$msg [$def]: " ans </dev/tty || true; fi
    echo "${ans:-$def}"
}
set_env() {  # set_env <file> <KEY> <value> -- same idempotent-upsert idiom install.sh uses
    local file="$1" key="$2" val="$3"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}

echo "== Gitea API token bootstrap =="
echo "Run once, after Gitea's own admin account already exists (see install.sh's"
echo "own printed instructions if you haven't done that yet)."
echo

GITEA_ADMIN_USER=$(prompt "Gitea admin username" "admin")
GITEA_CONTAINER=$(prompt "Gitea container name" "ai-dev-switchboard-gitea")

if [ ! -f "$CONFIG_FILE" ]; then
    echo "$CONFIG_FILE doesn't exist -- run install.sh --with-git-hosting first." >&2
    exit 1
fi

# Belt-and-suspenders: this file already carries other secrets (TOTP_SECRET,
# SIMPLE_PASSWORD) and install.sh already sets it 600/SVC_USER-owned -- this
# script must not loosen that, but warn (not block) if it's somehow already
# wrong, same "warn-not-block" spirit taiga_push_spec.py's own
# _check_config_permissions applies to its own (narrower) config file.
CONFIG_MODE=$(stat -c '%a' "$CONFIG_FILE" 2>/dev/null || echo "")
if [ -n "$CONFIG_MODE" ] && [ "$CONFIG_MODE" != "600" ]; then
    echo "WARNING: $CONFIG_FILE isn't mode 600 (currently $CONFIG_MODE) -- it's about to hold a live API token. Fix its permissions." >&2
fi

# Scopes: write:repository (git-http push/pull on repos this token can
# already reach) PLUS write:user -- verified live (not assumed) against a
# real Gitea 1.27.1 instance that `POST /user/repos` (creating a repo under
# the authenticated user's own account, exactly what create_project() calls)
# requires write:user, not write:repository alone: Gitea returned 403
# "token does not have at least one of required scope(s), required=[write:user]"
# with a write:repository-only token. write:user also happens to be what
# `GET /user` (this script's own verification call below) requires, which a
# docs.gitea.com-only reading missed. Still meaningfully narrower than `all`.
#
# read:issue,write:issue -- added for backlog item 8 (AI merge-request
# reviewer, docs/spec.md "Token scope"): reading a PR's labels and posting a
# PR comment are both, in Gitea's own data model, issue-family endpoints
# (a PR IS an issue under the hood -- POST .../issues/{index}/comments is
# how a PR comment is posted), distinct from the repository scope above.
# This is the design's own best-informed assumption, flagged as the one
# piece most worth confirming live against the real instance -- if Gitea
# ever rejects a call with 403 for insufficient scope, this is the first
# thing to check. Re-running this script (safe/idempotent, see the header
# above) mints a fresh token with the widened scope for an existing install.
# Token name includes a timestamp plus a random suffix -- verified live
# (not assumed) that Gitea rejects a second generate-access-token call
# reusing the same --token-name ("Command error: access token name has
# been used already"), which would otherwise make this script's own "safe
# to re-run" contract (docs/spec.md "Edge cases") false the second time
# it's ever run. Gitea has no CLI to delete/rotate a token by name without
# the account's own password (which this script deliberately never
# handles), so a unique name per run is the simplest fix that doesn't
# reintroduce a password prompt.
#
# A bare `date +%s` is NOT sufficient on its own: it only has 1-second
# resolution, and this script completes (mint token + write file + restart
# a unit + curl-verify) in well under a second, so two runs issued back to
# back (a human hitting up-arrow+enter twice, a provisioning script
# retrying for idempotency) can land in the same second and collide --
# reproduced live, 3 out of 5 back-to-back runs with no artificial delay
# failed this way with a timestamp-only name. Appending 8 random
# alphanumeric characters (same `/dev/urandom`-via-base64 idiom as
# install.sh's own random_token() helper -- this script doesn't source
# install.sh, so it's inlined here rather than duplicating the function)
# closes that window regardless of how fast the script runs.
TOKEN_NAME="ai-dev-switchboard-$(date +%s)-$(head -c 8 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 8)"
echo
echo "Minting a write:repository,write:user,read:issue,write:issue-scoped token via 'docker exec --user git $GITEA_CONTAINER gitea admin user generate-access-token' (no password needed)..."
TOKEN_OUTPUT=$(docker exec --user git "$GITEA_CONTAINER" gitea admin user generate-access-token \
    --username "$GITEA_ADMIN_USER" --token-name "$TOKEN_NAME" \
    --scopes write:repository,write:user,read:issue,write:issue --raw 2>&1) || {
    echo "Failed to generate a token. Output was:" >&2
    echo "$TOKEN_OUTPUT" >&2
    echo "Common causes: Gitea isn't running, '$GITEA_CONTAINER' is the wrong container name, or '$GITEA_ADMIN_USER' doesn't exist yet (create it first -- see install.sh's printed instructions)." >&2
    exit 1
}
GITEA_API_TOKEN="$(echo "$TOKEN_OUTPUT" | tail -1 | tr -d '[:space:]')"
if [ -z "$GITEA_API_TOKEN" ]; then
    echo "Token generation produced no output -- something went wrong. Raw output:" >&2
    echo "$TOKEN_OUTPUT" >&2
    exit 1
fi

set_env "$CONFIG_FILE" GITEA_API_TOKEN "$GITEA_API_TOKEN"
echo "Wrote GITEA_API_TOKEN into $CONFIG_FILE."

echo "Restarting ai-dev-switchboard so it picks up the new token (EnvironmentFile= is read once at process start, not live)..."
systemctl restart ai-dev-switchboard

GITEA_PORT="$(grep '^GITEA_PORT=' "$CONFIG_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
GITEA_PORT="${GITEA_PORT:-3000}"

echo
echo "Verifying the token actually works (GET /user)..."
VERIFY_OUTPUT=$(curl -fsS -H "Authorization: token $GITEA_API_TOKEN" \
    "http://127.0.0.1:${GITEA_PORT}/api/v1/user" 2>&1) || {
    echo "Verification failed -- Gitea didn't accept the new token. Output was:" >&2
    echo "$VERIFY_OUTPUT" >&2
    exit 1
}
VERIFIED_LOGIN=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('login',''))" <<<"$VERIFY_OUTPUT" 2>/dev/null || true)
if [ -z "$VERIFIED_LOGIN" ]; then
    echo "Verification failed -- couldn't parse a username out of Gitea's /user response:" >&2
    echo "$VERIFY_OUTPUT" >&2
    exit 1
fi

echo
echo "Setup verified -- authenticated as '$VERIFIED_LOGIN'."
echo "The web UI's '+ New project' button can now create real Gitea repos."
