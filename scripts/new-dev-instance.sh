#!/usr/bin/env bash
# Register a repo as a toggleable project: clones the bare repo (from
# new-repo.sh) into $PROJECTS_DIR/<name>, where the web UI will find it
# automatically. Also installs a post-receive hook on the bare repo so every
# future push to main automatically re-syncs that working copy too — not
# just present once, kept current with zero manual steps. Safe to rerun.
#
# Usage: new-dev-instance.sh <name>
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/git-hosting.env
[ -f "$CONFIG" ] && source "$CONFIG"
GIT_ROOT="${GIT_ROOT:-/srv/git}"
GIT_USER="${GIT_USER:-git}"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi

NAME="$1"
BARE="$GIT_ROOT/repos/${NAME}.git"
DEST="${PROJECTS_DIR}/${NAME}"

if [ ! -d "$BARE" ]; then
    echo "No such repo: $BARE (create it first with new-repo.sh $NAME)" >&2
    exit 1
fi

mkdir -p "$PROJECTS_DIR"
if [ -d "$DEST" ]; then
    echo "Already exists: $DEST"
else
    su "$RUN_USER" -s /bin/bash -c "git clone '$BARE' '$DEST'"
    echo "Ready: $DEST — will show up in the web UI now."
fi

# Install (or update) the auto-sync post-receive hook. Every hook here reads
# ref updates from a cached copy of stdin (see new-repo.sh) rather than
# stdin directly, so this block coexists safely with an auto-deploy block
# new-repo.sh may already have installed — two independent `while read`
# loops both consuming raw stdin would starve whichever runs second.
HOOK="${BARE}/hooks/post-receive"
MARKER="# --- project-sync (added by new-dev-instance.sh) ---"

if [ ! -f "$HOOK" ]; then
    su "$GIT_USER" -s /bin/bash -c "cat > '$HOOK'" <<'PREAMBLE'
#!/usr/bin/env bash
set -euo pipefail
_STDIN_CACHE=$(mktemp)
trap 'rm -f "$_STDIN_CACHE"' EXIT
cat > "$_STDIN_CACHE"
PREAMBLE
fi

if ! grep -qF "$MARKER" "$HOOK" 2>/dev/null; then
    su "$GIT_USER" -s /bin/bash -c "cat >> '$HOOK'" <<HOOKEOF

${MARKER}
while read -r oldrev newrev refname; do
    branch=\$(git rev-parse --symbolic --abbrev-ref "\$refname")
    if [ "\$branch" = "main" ]; then
        sudo -u ${RUN_USER} /usr/local/bin/ai-dev-switchboard-project-sync.sh "${NAME}"
    fi
done < "\$_STDIN_CACHE"
HOOKEOF
    su "$GIT_USER" -s /bin/bash -c "chmod +x '$HOOK'"
    echo "Auto-sync hook installed — pushes to main will keep $DEST current automatically."
fi
