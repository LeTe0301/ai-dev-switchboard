#!/usr/bin/env bash
# Run ONCE on any machine that should receive auto-deploys from a git-hosting
# box running new-repo.sh's auto-deploy form. Sets up a 'deploy' user whose
# SSH key can ONLY write files into <path> (via rrsync — no shell, no other
# commands), plus a systemd .path unit that restarts <service> automatically
# whenever a new deploy lands. The git-hosting box never gets a shell, sudo,
# or root here — just that one restricted rsync.
#
# Usage (as root, on the TARGET machine):
#   target-setup.sh <path> <service> <deploy-pubkey>
#
# Example:
#   target-setup.sh /opt/receipts paperless-ai "ssh-ed25519 AAAA... ai-dev-switchboard-deploy"
#
# The pubkey is printed by new-repo.sh / git-hosting-setup.sh / lives at
# $GIT_ROOT/.ssh/deploy_ed25519.pub on the git-hosting box.
set -euo pipefail

if [ $# -ne 3 ]; then
    echo "Usage: $0 <path> <service> <deploy-pubkey>" >&2
    exit 1
fi

DEPLOY_PATH="$1"
SERVICE="$2"
PUBKEY="$3"

id deploy &>/dev/null || useradd -m -d /home/deploy -s /bin/sh deploy
# /bin/sh, not nologin: sshd invokes the authorized_keys forced command via
# the account's shell ("$SHELL -c '<command>'"). nologin prints a banner to
# that -c invocation and corrupts the rsync protocol stream — it looks like a
# hang or "protocol incompatibility", not a permission error. The forced
# command still fully restricts what this account can do; the shell here is
# never reachable interactively since the key has no unrestricted access.
mkdir -p "$DEPLOY_PATH"
chown deploy:deploy "$DEPLOY_PATH"

mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh

if [ ! -x /usr/bin/rrsync ]; then
    echo "rrsync not found — is rsync installed? (apt-get install rsync; Debian 12+ ships rrsync in the package)" >&2
    exit 1
fi

echo "command=\"/usr/bin/rrsync -wo ${DEPLOY_PATH}\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ${PUBKEY}" \
    > /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

cat > "/etc/systemd/system/deploy-watch-${SERVICE}.service" <<EOF
[Unit]
Description=Restart ${SERVICE} after a new deploy lands in ${DEPLOY_PATH}

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl restart ${SERVICE}
EOF

cat > "/etc/systemd/system/deploy-watch-${SERVICE}.path" <<EOF
[Path]
PathModified=${DEPLOY_PATH}/.deployed

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "deploy-watch-${SERVICE}.path"

echo "Done. Pushes to the matching repo's main branch will sync into"
echo "${DEPLOY_PATH} here and restart ${SERVICE} automatically."
echo "(Make sure ${SERVICE}.service actually exists before the first push.)"
