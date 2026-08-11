#!/usr/bin/env bash
# One-time, idempotent setup for private git hosting: a restricted `git`
# user (git-shell only — no real shell access) that serves bare repos over
# SSH from $GIT_ROOT/repos, plus a dedicated SSH keypair used by auto-deploy
# hooks (see new-repo.sh's optional target-ip/target-path form). Safe to
# rerun — skips anything that already exists.
#
# Usage: git-hosting-setup.sh
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/git-hosting.env
[ -f "$CONFIG" ] && source "$CONFIG"
GIT_ROOT="${GIT_ROOT:-/srv/git}"
GIT_USER="${GIT_USER:-git}"
RUN_USER="${RUN_USER:-dev}"

GIT_SHELL="$(command -v git-shell || true)"
[ -n "$GIT_SHELL" ] || { echo "git-shell not found — is git installed?" >&2; exit 1; }

if ! id "$GIT_USER" &>/dev/null; then
    useradd -m -d "$GIT_ROOT" -s "$GIT_SHELL" "$GIT_USER"
    echo "Created user $GIT_USER (shell: $GIT_SHELL, home: $GIT_ROOT)"
fi

mkdir -p "$GIT_ROOT/repos" "$GIT_ROOT/.ssh"
chown -R "$GIT_USER:$GIT_USER" "$GIT_ROOT"
chmod 700 "$GIT_ROOT/.ssh"

if [ ! -f "$GIT_ROOT/.ssh/authorized_keys" ]; then
    su "$GIT_USER" -s /bin/bash -c "touch '$GIT_ROOT/.ssh/authorized_keys' && chmod 600 '$GIT_ROOT/.ssh/authorized_keys'"
    echo "Created empty $GIT_ROOT/.ssh/authorized_keys — add developers' public keys there, one per line."
fi

if [ ! -f "$GIT_ROOT/.ssh/deploy_ed25519" ]; then
    su "$GIT_USER" -s /bin/bash -c \
        "ssh-keygen -q -t ed25519 -N '' -C 'ai-dev-switchboard-deploy' -f '$GIT_ROOT/.ssh/deploy_ed25519'"
    echo "Generated deploy keypair: $GIT_ROOT/.ssh/deploy_ed25519{,.pub}"
fi

# The auto-sync post-receive hook new-dev-instance.sh installs runs as
# $GIT_USER but needs to sync the working copy as $RUN_USER (whose
# credentials/permissions the project files actually need) — scoped to
# exactly that one script, nothing else.
SUDOERS_FILE=/etc/sudoers.d/ai-dev-switchboard-git-sync
if [ ! -f "$SUDOERS_FILE" ]; then
    echo "$GIT_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/ai-dev-switchboard-project-sync.sh *" > "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
    echo "Installed $SUDOERS_FILE"
fi

echo ""
echo "git-hosting ready: $GIT_ROOT/repos (SSH as $GIT_USER)"
echo "Deploy public key (paste into target-setup.sh on any auto-deploy target):"
cat "$GIT_ROOT/.ssh/deploy_ed25519.pub"
