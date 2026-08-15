#!/usr/bin/env bash
# The one script `deploy` may run as root (docs/spec.md part 2c-2a,
# "deploy-restart.sh"), via the narrowly-scoped, zero-argument sudoers rule
# in /etc/sudoers.d/ai-dev-switchboard-deploy-target. Installed to
# /usr/local/bin/ai-dev-switchboard-deploy-restart.sh, root-owned, mode 755.
#
# Re-validates DEPLOY_SERVICE_NAME even though it comes from a root-authored
# config file — defense in depth, same discipline every other privileged
# script in this repo already follows (see scripts/gitea-sync-project.sh,
# scripts/new-project-from-gitea.sh) even when the "input" is config, not
# network input, because config can still be hand-edited incorrectly.
#
# Left to fail loudly (set -euo pipefail, no error swallowing) — a restart
# failure must propagate back over SSH as a non-zero exit, not be silently
# absorbed.
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/deploy-target.env
[ -f "$CONFIG" ] && source "$CONFIG"

if [ -z "${DEPLOY_SERVICE_NAME:-}" ]; then
    echo "deploy-restart: DEPLOY_SERVICE_NAME isn't set in $CONFIG." >&2
    exit 1
fi
if ! [[ "$DEPLOY_SERVICE_NAME" =~ ^[A-Za-z0-9@_.-]+$ ]]; then
    echo "deploy-restart: invalid DEPLOY_SERVICE_NAME: $DEPLOY_SERVICE_NAME" >&2
    exit 1
fi

systemctl restart -- "$DEPLOY_SERVICE_NAME"
