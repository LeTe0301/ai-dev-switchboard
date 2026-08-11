#!/usr/bin/env bash
# One-liner entry point — run ON a Proxmox VE host:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/LeTe0301/ai-dev-switchboard/main/ct/create.sh)"
#
# Walks you through creating a new Debian LXC container, clones this repo
# into it, and runs install.sh inside — end to end, from a blank Proxmox
# host to a running switchboard, the same way community-scripts/ProxmoxVE's
# ct/*.sh one-liners bootstrap other apps. This one is much smaller since it
# only knows how to install this one tool — no shared framework, just pct
# and whiptail.
#
# Every prompt has a sane default (press Enter to accept it). To run this
# fully non-interactively instead, set the corresponding CT_* / SWB_* env
# vars below before invoking and answer nothing — see the "non-interactive"
# block near the bottom for exactly what gets read.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/LeTe0301/ai-dev-switchboard.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

command -v pct >/dev/null 2>&1 || { echo "pct not found — this script must run on a Proxmox VE host." >&2; exit 1; }
command -v whiptail >/dev/null 2>&1 || apt-get install -y -qq whiptail

msg()   { whiptail --title "ai-dev-switchboard" --msgbox "$1" 14 74; }
ask()   { whiptail --title "ai-dev-switchboard" --inputbox "$1" 10 74 "$2" 3>&1 1>&2 2>&3; }
askpw() { whiptail --title "ai-dev-switchboard" --passwordbox "$1" 10 74 3>&1 1>&2 2>&3; }
yesno() { whiptail --title "ai-dev-switchboard" --yesno "$1" 10 74; }
menu()  { whiptail --title "ai-dev-switchboard" --menu "$1" 15 74 3 "${@:2}" 3>&1 1>&2 2>&3; }

msg "This creates a new LXC container running ai-dev-switchboard: a web UI that starts/stops Claude Code, aider, Codex, or any CLI coding agent per project, from a phone or laptop.\n\nEach next step has a sensible default — press Enter (or Tab, Enter) to accept it."

CTID=$(ask "Container ID (must be free):" "$(pvesh get /cluster/nextid 2>/dev/null || echo 900)")
CT_HOSTNAME=$(ask "Hostname:" "ai-dev-switchboard")
STORAGE=$(ask "Storage pool for the container's root disk:" "local-lvm")
DISK_GB=$(ask "Disk size (GB):" "8")
CORES=$(ask "CPU cores:" "2")
MEM_MB=$(ask "Memory (MB):" "2048")
BRIDGE=$(ask "Network bridge:" "vmbr0")
IPCONFIG=$(ask "IP config (dhcp, or e.g. 192.168.1.50/24,gw=192.168.1.1):" "dhcp")
TEMPLATE_STORAGE=$(ask "Storage where the Debian 12 container template lives/downloads to:" "local")

RUN_USER=$(ask "Unprivileged user to run coding sessions as (created inside the container):" "dev")

AUTH_MODE=$(menu "How should the web UI authenticate you?" \
    "simple" "A single username + password you set now" \
    "pve"    "Real Proxmox VE credentials (checked against this host's API)")

SIMPLE_USERNAME=""
SIMPLE_PASSWORD=""
if [ "$AUTH_MODE" = "simple" ]; then
    SIMPLE_USERNAME=$(ask "Web UI username:" "admin")
    SIMPLE_PASSWORD=$(askpw "Web UI password:")
fi

WITH_GIT_HOSTING=0
if yesno "Enable git-hosting on this container too?\n\nPrivate bare repos over SSH + the \"+ New project\" button in the UI (clone-and-register in one step). You can always add this later."; then
    WITH_GIT_HOSTING=1
fi

WITH_CODE_SERVER=0
if yesno "Enable code-server (VS Code in the browser) per project?"; then
    WITH_CODE_SERVER=1
fi

TOTP_SECRET="$(head -c20 /dev/urandom | base32 | tr -d '=' | head -c32)"

# ── template
TEMPLATE=$(pveam available --section system 2>/dev/null | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
    pveam update >/dev/null
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

# ── create + start
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    -hostname "$CT_HOSTNAME" -storage "$STORAGE" -rootfs "${STORAGE}:${DISK_GB}" \
    -cores "$CORES" -memory "$MEM_MB" -net0 "name=eth0,bridge=${BRIDGE},ip=${IPCONFIG}" \
    -unprivileged 1 -onboot 1 -features nesting=1

pct start "$CTID"
echo "Waiting for networking inside the container..."
for _ in $(seq 1 30); do
    pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 2
done

# ── bootstrap the container: clone the repo, write config, run install.sh
pct exec "$CTID" -- bash -c "apt-get update -qq && apt-get install -y -qq git curl sudo"
pct exec "$CTID" -- git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" /opt/ai-dev-switchboard-src

pct exec "$CTID" -- mkdir -p /etc/ai-dev-switchboard
TMP_ENV=$(mktemp)
trap 'rm -f "$TMP_ENV"' EXIT
cat > "$TMP_ENV" <<EOF
RUN_USER=${RUN_USER}
PROJECTS_DIR=/home/${RUN_USER}/projects
ENGINES_DIR=/etc/ai-dev-switchboard/engines.d
AUTH_MODE=${AUTH_MODE}
SIMPLE_USERNAME=${SIMPLE_USERNAME}
SIMPLE_PASSWORD=${SIMPLE_PASSWORD}
TOTP_SECRET=${TOTP_SECRET}
PUBLISH_MODE=none
LISTEN_HOST=127.0.0.1
LISTEN_PORT=8333
HOST_CONTROL_ENABLED=0
EOF
pct push "$CTID" "$TMP_ENV" /etc/ai-dev-switchboard/switchboard.env
rm -f "$TMP_ENV"
trap - EXIT

INSTALL_FLAGS="--yes"
[ "$WITH_GIT_HOSTING" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-git-hosting"
[ "$WITH_CODE_SERVER" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-code-server"

# shellcheck disable=SC2086
pct exec "$CTID" -- bash /opt/ai-dev-switchboard-src/install.sh $INSTALL_FLAGS

CT_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')

SUMMARY="Done.\n\nContainer ${CTID} (${CT_HOSTNAME}) is running at ${CT_IP:-<unknown>}.\n\nWeb UI (bound to 127.0.0.1:8333 inside the container by default — see README.md to expose it via tailscale serve / a reverse proxy / an SSH tunnel):\n  pct exec ${CTID} -- ss -ltnp | grep 8333   # confirm it's listening\n  ssh -L 8333:127.0.0.1:8333 root@${CT_IP:-<container-ip>}   # quick one-off tunnel\n  then open http://127.0.0.1:8333\n\nTOTP secret (add to an authenticator app):\n  ${TOTP_SECRET}\n\nNext: log in inside the container as ${RUN_USER} and run your engine's CLI once interactively (e.g. \`claude\`) to finish ITS login, before starting sessions from the web UI."

whiptail --title "ai-dev-switchboard" --msgbox "$SUMMARY" 24 78
echo -e "$SUMMARY"
