#!/usr/bin/env bash
# Privileged hand-off for the folder-upload wizard (see docs/spec.md
# "Crossing the privilege boundary"). Moves an already-validated,
# already-named source directory (staged by app.py, entirely unprivileged,
# under UPLOAD_STAGING_DIR) into $PROJECTS_DIR/<name>, flips ownership to
# RUN_USER, and git-inits it if it doesn't already have its own .git.
#
# Installed UNCONDITIONALLY by install.sh (not behind --with-git-hosting) —
# this script is explicitly the registration path for people who don't have
# git hosting installed at all. Runs as root via a narrowly-scoped sudoers
# entry; keep it as mechanical as possible (mkdir/cp/chown/git-init only),
# same discipline as scripts/new-dev-instance.sh.
#
# Usage: new-project-from-upload.sh <source-dir> <name>
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"

if [ $# -ne 2 ]; then
    echo "Usage: $0 <source-dir> <name>" >&2
    exit 1
fi

SOURCE_DIR="$1"
NAME="$2"

# Defense in depth: re-validate <name> against the same character-class
# shape as app.py's own NAME_RE, even though app.py already checked it —
# never trust the caller blindly, this script carries a broad root grant.
if ! [[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9\ _-]{0,59}$ ]]; then
    echo "Invalid project name: $NAME" >&2
    exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
    echo "No such source directory: $SOURCE_DIR" >&2
    exit 1
fi

mkdir -p "$PROJECTS_DIR"
DEST="${PROJECTS_DIR}/${NAME}"

# Atomic, no -p: fails outright (rather than silently merging into an
# existing directory) if DEST already exists — closes the TOCTOU race
# between app.py's own up-front collision check and this script actually
# running (docs/spec.md "Partial-failure semantics for a multi-project
# confirm call").
if ! mkdir "$DEST"; then
    echo "Already exists: $DEST" >&2
    exit 1
fi

# cp -a, not mv: staging and PROJECTS_DIR may be different filesystems, and
# mv can fail cross-device. The caller (app.py) cleans up staging
# separately either way, regardless of how this call turns out.
cp -a "$SOURCE_DIR/." "$DEST/"

chown -R "$RUN_USER:$RUN_USER" "$DEST"

if [ ! -d "$DEST/.git" ] && command -v git >/dev/null 2>&1; then
    su "$RUN_USER" -s /bin/bash -c \
        "cd '$DEST' && git init -q && git add -A && \
         git -c user.name='ai-dev-switchboard' -c user.email='switchboard@localhost' \
             commit -q -m 'Initial import via ai-dev-switchboard upload'"
fi

echo "Ready: $DEST — will show up in the web UI now."
