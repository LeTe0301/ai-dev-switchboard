#!/usr/bin/env bash
# Privileged hand-off for create_project() once Gitea has already created
# the repo via its own REST API (see docs/spec.md "backlog item 2b" -- "The
# privileged registration script"). Places a correctly-owned clone under
# PROJECTS_DIR/<name> as RUN_USER -- the one thing that genuinely needs
# root. Same mechanical, narrow shape as
# scripts/new-project-from-upload.sh -- no new pattern introduced.
#
# Installed system-wide (unlike gitea-configure-api.sh, which stays a
# one-time operator tool run from the repo checkout) as
# /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh, run via a
# narrowly-scoped sudoers entry gated on --with-git-hosting.
#
# Usage: new-project-from-gitea.sh <owner> <gitea-repo-name> <name>
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"
GITEA_PORT="${GITEA_PORT:-3000}"
# GITEA_API_TOKEN is read from this same file, never passed via argv/ps --
# root can always read a 600 file regardless of its owning user, which is
# exactly why the token never needs to travel on the command line here.

if [ $# -ne 3 ]; then
    echo "Usage: $0 <owner> <gitea-repo-name> <name>" >&2
    exit 1
fi

OWNER="$1"
REPO="$2"
NAME="$3"

# Defense in depth: re-validate <name> against the same character-class
# shape as app.py's own NAME_RE, even though app.py already checked it --
# never trust the caller blindly, this script carries a broad root grant.
if ! [[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9\ _-]{0,59}$ ]]; then
    echo "Invalid project name: $NAME" >&2
    exit 1
fi

# Cheap sanity check against a compromised/buggy caller constructing a
# malicious clone target -- this script is about to run `git clone` as
# root-elevated-to-RUN_USER against a URL it builds from these two
# arguments.
if ! [[ "$OWNER" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "Invalid Gitea owner: $OWNER" >&2
    exit 1
fi
if ! [[ "$REPO" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "Invalid Gitea repo name: $REPO" >&2
    exit 1
fi

if [ -z "${GITEA_API_TOKEN:-}" ]; then
    echo "GITEA_API_TOKEN isn't set in $CONFIG -- run scripts/gitea-configure-api.sh first." >&2
    exit 1
fi

mkdir -p "$PROJECTS_DIR"
DEST="${PROJECTS_DIR}/${NAME}"

# Atomic, no -p: fails outright (rather than silently merging into an
# existing directory) if DEST already exists -- closes the same TOCTOU race
# new-project-from-upload.sh already closes for its own caller.
if ! mkdir "$DEST"; then
    echo "Already exists: $DEST" >&2
    exit 1
fi

chown "$RUN_USER:$RUN_USER" "$DEST"

CLONE_URL="http://oauth2:${GITEA_API_TOKEN}@127.0.0.1:${GITEA_PORT}/${OWNER}/${REPO}.git"

# Clones directly as RUN_USER into the now-RUN_USER-owned empty DEST -- no
# separate cp -a + recursive chown pass needed the way
# new-project-from-upload.sh needs one, since su already writes as the
# right user from the start. The token stays embedded in the resulting
# .git/config's origin remote deliberately (docs/spec.md "Token reuse and
# where it lives") -- not stripped out after cloning.
#
# Output captured (not streamed straight to our own stderr) because git's
# own failure messages sometimes echo the URL it was trying to reach,
# which would otherwise leak GITEA_API_TOKEN into app.py's captured
# stderr -- and from there into the (truncated) message create_project()
# returns to the web UI, and into journalctl. Redact before printing.
CLONE_OUTPUT=$(su "$RUN_USER" -s /bin/bash -c "git clone '$CLONE_URL' '$DEST'" 2>&1) || {
    echo "git clone failed -- $DEST is left in place (empty or partial); a manual rmdir may be needed before retrying with the same name." >&2
    echo "${CLONE_OUTPUT//$GITEA_API_TOKEN/REDACTED}" >&2
    exit 1
}

echo "Ready: $DEST — will show up in the web UI now."
