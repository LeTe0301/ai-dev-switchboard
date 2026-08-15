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
command -v python3 >/dev/null 2>&1 || apt-get install -y -qq python3

msg()   { whiptail --title "ai-dev-switchboard" --msgbox "$1" 14 74; }
ask()   { whiptail --title "ai-dev-switchboard" --inputbox "$1" 10 74 "$2" 3>&1 1>&2 2>&3; }
askpw() { whiptail --title "ai-dev-switchboard" --passwordbox "$1" 10 74 3>&1 1>&2 2>&3; }
yesno() { whiptail --title "ai-dev-switchboard" --yesno "$1" 10 74; }
menu()  { whiptail --title "ai-dev-switchboard" --menu "$1" 15 74 3 "${@:2}" 3>&1 1>&2 2>&3; }

_valid_hostname() {
    local _h="$1" _label
    [ -n "$_h" ] && [ "${#_h}" -le 253 ] || return 1
    local _old_ifs=$IFS
    IFS='.'
    for _label in $_h; do
        IFS=$_old_ifs
        [[ "$_label" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] || return 1
        IFS='.'
    done
    IFS=$_old_ifs
    return 0
}

_enumerate_storage() {
    STORAGE_MENU_OPTS=()
    local _line _name _type _status _avail _desc
    while IFS= read -r _line; do
        _name=$(awk '{print $1}' <<<"$_line")
        _type=$(awk '{print $2}' <<<"$_line")
        _status=$(awk '{print $3}' <<<"$_line")
        _avail=$(awk '{print $6}' <<<"$_line")
        [ "$_status" = "active" ] || continue
        if command -v numfmt >/dev/null 2>&1 && [[ "$_avail" =~ ^[0-9]+$ ]]; then
            _desc="${_type}, $(numfmt --to=iec-i --suffix=B $((_avail * 1024)) 2>/dev/null) free"
        else
            _desc="$_type"
        fi
        STORAGE_MENU_OPTS+=("$_name" "$_desc")
    done < <(pvesm status -content rootdir 2>/dev/null | tail -n +2)
}

_enumerate_bridges() {
    BRIDGE_MENU_OPTS=()
    local _br _vnet
    while IFS= read -r _br; do
        [ -n "$_br" ] || continue
        case "$_br" in
            fwbr[0-9]*i[0-9]*) continue ;;  # item 32: Proxmox's own per-container firewall bridge, not a real uplink
        esac
        BRIDGE_MENU_OPTS+=("$_br" "kernel bridge")
    done < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1)
    if [ -f /etc/pve/sdn/vnets.cfg ]; then
        while IFS= read -r _vnet; do
            [ -n "$_vnet" ] && BRIDGE_MENU_OPTS+=("sdn:${_vnet}" "SDN vnet")
        done < <(awk '/^vnet:/{print $2}' /etc/pve/sdn/vnets.cfg 2>/dev/null)
    fi
}

msg "This creates a new LXC container running ai-dev-switchboard: a web UI that starts/stops Claude Code, aider, Codex, or any CLI coding agent per project, from a phone or laptop.\n\nEach next step has a sensible default — press Enter (or Tab, Enter) to accept it."

INSTALL_MODE=$(whiptail --title "ai-dev-switchboard" --menu \
    "How do you want to configure this container?" 15 74 2 \
    "default"  "Create with built-in defaults (fully automated, no prompts)" \
    "advanced" "Walk through every setting (container specs + optional features)" \
    3>&1 1>&2 2>&3)

DEFAULT_CT_HOSTNAME="ai-dev-switchboard"
DEFAULT_STORAGE="local-lvm"
DEFAULT_DISK_GB="20"
DEFAULT_CORES="2"
DEFAULT_MEM_MB="2048"
DEFAULT_BRIDGE="vmbr0"
DEFAULT_IPCONFIG="dhcp"
DEFAULT_TEMPLATE_STORAGE="local"
DEFAULT_RUN_USER="dev"

default_ctid() {
    pvesh get /cluster/nextid 2>/dev/null || echo 900
}

if [ "$INSTALL_MODE" = "default" ]; then
    CTID=$(default_ctid)
    CT_HOSTNAME="$DEFAULT_CT_HOSTNAME"
    STORAGE="$DEFAULT_STORAGE"
    DISK_GB="$DEFAULT_DISK_GB"
    CORES="$DEFAULT_CORES"
    MEM_MB="$DEFAULT_MEM_MB"
    BRIDGE="$DEFAULT_BRIDGE"
    IPCONFIG="$DEFAULT_IPCONFIG"
    TEMPLATE_STORAGE="$DEFAULT_TEMPLATE_STORAGE"
    RUN_USER="$DEFAULT_RUN_USER"

    AUTH_MODE="pve"
    SIMPLE_USERNAME=""
    SIMPLE_PASSWORD=""

    WITH_GIT_HOSTING=0
    WITH_CODE_SERVER=0
    WITH_TAIGA=0
    WITH_OLLAMA=0
    OLLAMA_BASE_URL_NORM=""
    OLLAMA_MODEL_INPUT=""

    PUBLISH_MODE="none"
    BASE_URL=""

    whiptail --title "ai-dev-switchboard" --msgbox "About to create:\n\n  CTID: ${CTID}\n  Hostname: ${CT_HOSTNAME}\n  Storage: ${STORAGE} (${DISK_GB}G disk)\n  CPU / RAM: ${CORES} cores / ${MEM_MB}MB\n  Network: bridge ${BRIDGE}, ${IPCONFIG}\n  Run-as user: ${RUN_USER}\n  Web UI login: your existing Proxmox VE credentials\n  Optional features: none enabled\n  Terminal publishing: loopback only\n\nPress Enter to create it, or Cancel to abort." 20 74
else
    while :; do
        CTID=$(ask "Container ID (must be free):" "${CTID:-$(default_ctid)}")
        if ! [[ "$CTID" =~ ^[0-9]+$ ]] || [ "$CTID" -lt 100 ] || [ "$CTID" -gt 999999999 ]; then
            msg "Container ID must be a number between 100 and 999999999 (got '$CTID')."
            continue
        fi
        if pct status "$CTID" >/dev/null 2>&1; then
            msg "Container ID $CTID is already in use on this host. Choose a different one."
            continue
        fi
        break
    done

    while :; do
        CT_HOSTNAME=$(ask "Hostname:" "${CT_HOSTNAME:-$DEFAULT_CT_HOSTNAME}")
        if ! _valid_hostname "$CT_HOSTNAME"; then
            msg "'$CT_HOSTNAME' is not a valid hostname. Use letters, digits, hyphens; each dot-separated label 1-63 characters; can't start or end with a hyphen."
            continue
        fi
        break
    done

    _enumerate_storage
    if [ "${#STORAGE_MENU_OPTS[@]}" -eq 0 ]; then
        STORAGE=$(ask "Storage pool for the container's root disk:" "$DEFAULT_STORAGE")
    else
        _storage_rows=$(( ${#STORAGE_MENU_OPTS[@]} / 2 ))
        _storage_listheight=$(( _storage_rows < 8 ? _storage_rows : 8 ))
        STORAGE=$(whiptail --title "ai-dev-switchboard" --menu \
            "Storage pool for the container's root disk:" \
            "$(( _storage_listheight + 9 ))" 78 "$_storage_listheight" \
            "${STORAGE_MENU_OPTS[@]}" 3>&1 1>&2 2>&3)
    fi
    DISK_GB=$(ask "Disk size (GB):" "$DEFAULT_DISK_GB")
    CORES=$(ask "CPU cores:" "$DEFAULT_CORES")
    MEM_MB=$(ask "Memory (MB):" "$DEFAULT_MEM_MB")
    _enumerate_bridges
    if [ "${#BRIDGE_MENU_OPTS[@]}" -eq 0 ]; then
        BRIDGE=$(ask "Network bridge:" "$DEFAULT_BRIDGE")
    else
        _bridge_rows=$(( ${#BRIDGE_MENU_OPTS[@]} / 2 ))
        _bridge_listheight=$(( _bridge_rows < 8 ? _bridge_rows : 8 ))
        BRIDGE=$(whiptail --title "ai-dev-switchboard" --menu \
            "Network bridge:" \
            "$(( _bridge_listheight + 9 ))" 78 "$_bridge_listheight" \
            "${BRIDGE_MENU_OPTS[@]}" 3>&1 1>&2 2>&3)
        BRIDGE="${BRIDGE#sdn:}"
    fi
    IPCONFIG=$(ask "IP config (dhcp, or e.g. 192.168.1.50/24,gw=192.168.1.1):" "$DEFAULT_IPCONFIG")
    TEMPLATE_STORAGE=$(ask "Storage where the Debian 12 container template lives/downloads to:" "$DEFAULT_TEMPLATE_STORAGE")

    RUN_USER=$(ask "Unprivileged user to run coding sessions as (created inside the container):" "$DEFAULT_RUN_USER")

    AUTH_MODE=$(menu "How should the web UI authenticate you?" \
        "simple" "A single username + password you set now" \
        "pve"    "Real Proxmox VE credentials (checked against this host's API)")

    SIMPLE_USERNAME=""
    SIMPLE_PASSWORD=""
    if [ "$AUTH_MODE" = "simple" ]; then
        SIMPLE_USERNAME=$(ask "Web UI username:" "admin")
        SIMPLE_PASSWORD=$(askpw "Web UI password:")
    fi

    FEATURES=$(whiptail --title "ai-dev-switchboard" --checklist \
        "Optional features to enable on this container (Space to toggle, Enter to confirm):" \
        18 78 4 \
        "git-hosting" "Private repos over SSH + \"+ New project\" button" OFF \
        "code-server" "VS Code in the browser, per project" OFF \
        "taiga"       "Self-hosted Taiga backlog/kanban tracker" OFF \
        "ollama"      "Link a remote Ollama for multi-agent team leads" OFF \
        3>&1 1>&2 2>&3)

    WITH_GIT_HOSTING=0
    WITH_CODE_SERVER=0
    WITH_TAIGA=0
    WITH_OLLAMA=0
    for _item in $FEATURES; do
        _item="${_item%\"}"; _item="${_item#\"}"
        case "$_item" in
            git-hosting) WITH_GIT_HOSTING=1 ;;
            code-server) WITH_CODE_SERVER=1 ;;
            taiga)       WITH_TAIGA=1 ;;
            ollama)      WITH_OLLAMA=1 ;;
        esac
    done

    if [ "$WITH_TAIGA" -eq 1 ]; then
        msg "Taiga runs 9 containers and can use several GB of RAM (and real disk, for Postgres/RabbitMQ data volumes) once turned on in the web UI; toggling it back off frees that RAM again right away."
    fi

    OLLAMA_BASE_URL_NORM=""
    OLLAMA_MODEL_INPUT=""
    if [ "$WITH_OLLAMA" -eq 1 ]; then
        # Copied verbatim from install.sh's own --with-ollama block (exact
        # model-id comparison against data[].id — never substring/grep, since
        # e.g. "qwen3:8" must not false-positive-match "qwen3:8b").
        OLLAMA_MODEL_CHECK_SCRIPT=$(cat <<'PYEOF'
import json
import sys

wanted = sys.argv[1]
raw = sys.stdin.read()
try:
    data = json.loads(raw)
    ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
except Exception:
    print("PARSE_ERROR")
    sys.exit(0)
if wanted in ids:
    print("OK")
else:
    print("MODEL_ABSENT:" + ",".join(str(i) for i in ids if i is not None))
PYEOF
)
        _ollama_url_default="http://127.0.0.1:11434/v1"
        _ollama_model_default="qwen3:8b"
        while :; do
            _ollama_url_input=$(ask "Ollama endpoint URL (OpenAI-compatible, e.g. an existing remote Ollama's /v1)" "$_ollama_url_default")
            _ollama_model_input=$(ask "Model name" "$_ollama_model_default")
            _ollama_url_norm="${_ollama_url_input%/}"
            _ollama_models_json=$(curl -fsS --max-time 10 "$_ollama_url_norm/models" 2>/dev/null || true)
            if [ -z "$_ollama_models_json" ]; then
                _ollama_fail_msg="Could not reach $_ollama_url_norm/models (unreachable, no response, or an HTTP error)."
            else
                _ollama_check=$(printf '%s' "$_ollama_models_json" | python3 -c "$OLLAMA_MODEL_CHECK_SCRIPT" "$_ollama_model_input")
                case "$_ollama_check" in
                    OK)
                        OLLAMA_BASE_URL_NORM="$_ollama_url_norm"
                        OLLAMA_MODEL_INPUT="$_ollama_model_input"
                        break
                        ;;
                    MODEL_ABSENT:*)
                        _ollama_available="${_ollama_check#MODEL_ABSENT:}"
                        if [ -z "$_ollama_available" ]; then
                            _ollama_fail_msg="Reached $_ollama_url_norm but it has no models available."
                        else
                            _ollama_fail_msg="Reached $_ollama_url_norm but model '$_ollama_model_input' is not available there. Available: $_ollama_available"
                        fi
                        ;;
                    *)
                        _ollama_fail_msg="Reached $_ollama_url_norm/models but its response could not be parsed as JSON."
                        ;;
                esac
            fi
            msg "$_ollama_fail_msg"
            if yesno "Try a different URL/model?"; then
                _ollama_url_default="$_ollama_url_input"
                _ollama_model_default="$_ollama_model_input"
                continue
            else
                WITH_OLLAMA=0
                msg "Continuing without linking Ollama. You can re-run 'install.sh --with-ollama' inside the container later once the endpoint is reachable."
                break
            fi
        done
    fi

    PUBLISH_MODE=$(menu "How should per-project ttyd/VS Code terminals be published beyond this container?" \
        "none"      "Loopback only — you handle exposing them yourself" \
        "tailscale" "Publish via 'tailscale serve --set-path' (requires tailscale installed+logged in later)")

    BASE_URL=""
    if [ "$PUBLISH_MODE" = "tailscale" ]; then
        BASE_URL=$(ask "Tailnet hostname per-project terminals get published under (see 'tailscale status' inside the container later — leave blank to fill in afterward):" "")
    fi
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
PUBLISH_MODE=${PUBLISH_MODE}
BASE_URL=${BASE_URL}
LISTEN_HOST=127.0.0.1
LISTEN_PORT=8333
HOST_CONTROL_ENABLED=0
EOF
if [ "$WITH_OLLAMA" -eq 1 ]; then
    {
        echo "TEAM_LLM_BASE_URL=${OLLAMA_BASE_URL_NORM}"
        echo "TEAM_LLM_MODEL=${OLLAMA_MODEL_INPUT}"
    } >> "$TMP_ENV"
fi
pct push "$CTID" "$TMP_ENV" /etc/ai-dev-switchboard/switchboard.env
rm -f "$TMP_ENV"
trap - EXIT

INSTALL_FLAGS="--yes"
[ "$WITH_GIT_HOSTING" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-git-hosting"
[ "$WITH_CODE_SERVER" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-code-server"
[ "$WITH_TAIGA" -eq 1 ]       && INSTALL_FLAGS="$INSTALL_FLAGS --with-taiga"
[ "$WITH_OLLAMA" -eq 1 ]      && INSTALL_FLAGS="$INSTALL_FLAGS --with-ollama"

# shellcheck disable=SC2086
pct exec "$CTID" -- bash /opt/ai-dev-switchboard-src/install.sh $INSTALL_FLAGS

CT_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')

PUBLISH_NOTE=""
if [ "$PUBLISH_MODE" = "tailscale" ]; then
    PUBLISH_NOTE="\n\nPublish mode: tailscale (per-project terminals will auto-publish via 'tailscale serve --set-path' once tailscale is installed+logged in inside the container). The main UI itself is NOT auto-published — still run 'tailscale serve --bg https+insecure://127.0.0.1:8333' inside the container yourself (see README.md \"Reaching the UI\")."
fi

SUMMARY="Done.\n\nContainer ${CTID} (${CT_HOSTNAME}) is running at ${CT_IP:-<unknown>}.\n\nWeb UI (bound to 127.0.0.1:8333 inside the container by default — see README.md to expose it via tailscale serve / a reverse proxy / an SSH tunnel):\n  pct exec ${CTID} -- ss -ltnp | grep 8333   # confirm it's listening\n  ssh -L 8333:127.0.0.1:8333 root@${CT_IP:-<container-ip>}   # quick one-off tunnel\n  then open http://127.0.0.1:8333${PUBLISH_NOTE}\n\nTOTP secret (add to an authenticator app):\n  ${TOTP_SECRET}\n\nNext: log in inside the container as ${RUN_USER} and run your engine's CLI once interactively (e.g. \`claude\`) to finish ITS login, before starting sessions from the web UI."

# The tailscale PUBLISH_NOTE adds several wrapped lines on top of the
# baseline summary — a fixed 24-row box clips the TOTP secret/login
# instructions off the bottom in that case (whiptail --msgbox doesn't
# scroll), so grow the box when that note is present.
SUMMARY_HEIGHT=24
[ "$PUBLISH_MODE" = "tailscale" ] && SUMMARY_HEIGHT=32
whiptail --title "ai-dev-switchboard" --msgbox "$SUMMARY" "$SUMMARY_HEIGHT" 78
echo -e "$SUMMARY"
