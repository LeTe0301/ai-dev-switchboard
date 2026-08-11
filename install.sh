#!/usr/bin/env bash
# ai-dev-switchboard installer. Two ways to run it:
#
#   1. From an existing clone of this repo:
#        sudo ./install.sh [flags]
#
#   2. Piped straight from GitHub, nothing cloned yet:
#        bash -c "$(curl -fsSL https://raw.githubusercontent.com/LeTe0301/ai-dev-switchboard/main/install.sh)"
#      (clones the repo to /opt/ai-dev-switchboard-src first, then re-execs
#      itself from that real checkout — everything below assumes case 1)
#
# Flags (all optional):
#   --yes                 non-interactive: use defaults / env-var overrides
#                         instead of prompting (what ct/create.sh uses)
#   --with-git-hosting    also set up private bare-repo hosting + the
#                         "+ New project" button (see scripts/, docs/GIT_HOSTING.md)
#   --with-code-server    also install code-server (VS Code in the browser)
#   --with-host-control   also install host-agent/ on THIS machine (see
#                         host-agent/README.md — usually installed on a
#                         *different* machine than the web UI by hand instead)
#
# Safe to re-run: every step here either checks for existing state first or
# overwrites deterministically-generated files (units, sudoers), never
# clobbers switchboard.env/git-hosting.env values that are already set.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/LeTe0301/ai-dev-switchboard.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
SRC_DIR="${SRC_DIR:-/opt/ai-dev-switchboard-src}"

# ── bootstrap: if there's no real app/app.py next to this script, we're
# running from a `bash -c "$(curl ...)"` pipe with nothing on disk — clone
# the repo and re-exec from the real checkout instead of trying to install
# from a one-shot stdin stream.
_this_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || true)"
if [ -z "$_this_dir" ] || [ ! -f "$_this_dir/app/app.py" ]; then
    command -v git >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq git; }
    if [ -d "$SRC_DIR/.git" ]; then
        git -C "$SRC_DIR" pull --ff-only
    else
        git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$SRC_DIR"
    fi
    exec bash "$SRC_DIR/install.sh" "$@"
fi
REPO_DIR="$_this_dir"

# ── flags
YES=0
WITH_GIT_HOSTING=0
WITH_CODE_SERVER=0
WITH_HOST_CONTROL=0
for arg in "$@"; do
    case "$arg" in
        --yes) YES=1 ;;
        --with-git-hosting) WITH_GIT_HOSTING=1 ;;
        --with-code-server) WITH_CODE_SERVER=1 ;;
        --with-host-control) WITH_HOST_CONTROL=1 ;;
        *) echo "Unknown flag: $arg (see the top of install.sh for the list)" >&2; exit 1 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root (sudo ./install.sh ...)." >&2
    exit 1
fi

CONFIG_DIR=/etc/ai-dev-switchboard
INSTALL_DIR=/opt/ai-dev-switchboard
STATE_DIR=/var/lib/ai-dev-switchboard
mkdir -p "$CONFIG_DIR" "$INSTALL_DIR" "$STATE_DIR"

interactive() { [ "$YES" -eq 0 ] && [ -t 0 ]; }
prompt() {  # prompt <message> <default> -> echoes the answer
    local msg="$1" def="$2" ans=""
    if interactive; then read -rp "$msg [$def]: " ans </dev/tty || true; fi
    echo "${ans:-$def}"
}
prompt_secret() {  # prompt_secret <message> -> echoes the answer (may be empty)
    local msg="$1" ans=""
    if interactive; then read -rsp "$msg: " ans </dev/tty || true; echo >&2; fi
    echo "$ans"
}
set_env() {  # set_env <file> <KEY> <value> — idempotent upsert
    local file="$1" key="$2" val="$3"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}
get_env() {  # get_env <file> <KEY> -> value or empty
    grep "^${2}=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true
}
random_token() { head -c "${1:-16}" /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-16}"; }

echo "== ai-dev-switchboard install =="
echo "Repo: $REPO_DIR"

echo "-- Installing dependencies (apt) --"
apt-get update -qq
apt-get install -y -qq python3 tmux git curl sudo rsync openssh-client ca-certificates

if [ ! -x /usr/local/bin/ttyd ]; then
    echo "-- Installing ttyd (per-project fallback terminal) --"
    case "$(uname -m)" in
        x86_64) TTYD_ARCH=x86_64 ;;
        aarch64) TTYD_ARCH=aarch64 ;;
        *) TTYD_ARCH="" ;;
    esac
    if [ -n "$TTYD_ARCH" ]; then
        curl -fsSL "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.$TTYD_ARCH" -o /usr/local/bin/ttyd
        chmod +x /usr/local/bin/ttyd
    else
        echo "No prebuilt ttyd for $(uname -m) — install it yourself (https://github.com/tsl0922/ttyd) and re-run." >&2
    fi
fi

if [ "$WITH_CODE_SERVER" -eq 1 ] && [ ! -x /usr/local/bin/code-server ]; then
    echo "-- Installing code-server --"
    curl -fsSL https://code-server.dev/install.sh | sh
fi

echo "-- Users --"
RUN_USER=$(prompt "Unprivileged user to run coding sessions as" "dev")
SVC_USER=$(prompt "Unprivileged user to run the web UI process as" "switchboard-svc")

id "$RUN_USER" &>/dev/null || { useradd -m -s /bin/bash "$RUN_USER"; echo "Created $RUN_USER"; }
id "$SVC_USER" &>/dev/null || { useradd -r -m -d "/home/$SVC_USER" -s /usr/sbin/nologin "$SVC_USER"; echo "Created $SVC_USER"; }

PROJECTS_DIR="/home/$RUN_USER/projects"
mkdir -p "$PROJECTS_DIR"
chown "$RUN_USER:$RUN_USER" "$PROJECTS_DIR"

echo "-- App + engines --"
cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"
mkdir -p "$CONFIG_DIR/engines.d"
for f in "$REPO_DIR"/engines.d/*.engine; do
    [ -e "$f" ] || continue
    dest="$CONFIG_DIR/engines.d/$(basename "$f")"
    [ -e "$dest" ] || cp "$f" "$dest"
done

echo "-- Config --"
ENV_FILE="$CONFIG_DIR/switchboard.env"
[ -f "$ENV_FILE" ] || cp "$REPO_DIR/config/switchboard.env.example" "$ENV_FILE"

set_env "$ENV_FILE" RUN_USER "$RUN_USER"
set_env "$ENV_FILE" PROJECTS_DIR "$PROJECTS_DIR"
set_env "$ENV_FILE" ENGINES_DIR "$CONFIG_DIR/engines.d"
set_env "$ENV_FILE" DESC_CACHE_FILE "$STATE_DIR/descriptions.json"

TOTP_SECRET="$(get_env "$ENV_FILE" TOTP_SECRET)"
if [ -z "$TOTP_SECRET" ]; then
    TOTP_SECRET="$(head -c20 /dev/urandom | base32 | tr -d '=' | head -c32)"
    set_env "$ENV_FILE" TOTP_SECRET "$TOTP_SECRET"
fi

AUTH_MODE=$(prompt "Auth mode: simple (username+password) or pve (Proxmox VE login)" "simple")
set_env "$ENV_FILE" AUTH_MODE "$AUTH_MODE"
SIMPLE_PASSWORD_SHOWN=""
if [ "$AUTH_MODE" = "pve" ]; then
    PVE_HOST=$(prompt "Proxmox VE host IP" "$(get_env "$ENV_FILE" PVE_HOST)")
    set_env "$ENV_FILE" PVE_HOST "$PVE_HOST"
else
    SIMPLE_USERNAME=$(prompt "Web UI username" "$(get_env "$ENV_FILE" SIMPLE_USERNAME)")
    [ -n "$SIMPLE_USERNAME" ] || SIMPLE_USERNAME="admin"
    SIMPLE_PASSWORD=$(prompt_secret "Web UI password (leave blank to auto-generate)")
    if [ -z "$SIMPLE_PASSWORD" ]; then
        SIMPLE_PASSWORD="$(get_env "$ENV_FILE" SIMPLE_PASSWORD)"
        if [ -z "$SIMPLE_PASSWORD" ]; then
            SIMPLE_PASSWORD="$(random_token 16)"
            SIMPLE_PASSWORD_SHOWN="$SIMPLE_PASSWORD"
        fi
    fi
    set_env "$ENV_FILE" SIMPLE_USERNAME "$SIMPLE_USERNAME"
    set_env "$ENV_FILE" SIMPLE_PASSWORD "$SIMPLE_PASSWORD"
fi

if [ "$WITH_HOST_CONTROL" -eq 1 ]; then
    set_env "$ENV_FILE" HOST_CONTROL_ENABLED 1
fi

chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "-- sudoers (scoped: $SVC_USER can only run tmux/ttyd/code-server AS $RUN_USER) --"
SUDOERS=/etc/sudoers.d/ai-dev-switchboard
{
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/bin/tmux *"
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/ttyd *"
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/code-server *"
    if [ "$WITH_GIT_HOSTING" -eq 1 ]; then
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project.sh *"
    fi
} > "$SUDOERS"
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS" || { echo "Generated sudoers file failed validation — see $SUDOERS" >&2; exit 1; }

echo "-- systemd service --"
cat > /etc/systemd/system/ai-dev-switchboard.service <<EOF
[Unit]
Description=ai-dev-switchboard
After=network.target

[Service]
Type=simple
User=$SVC_USER
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $INSTALL_DIR/app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ai-dev-switchboard

if [ "$WITH_GIT_HOSTING" -eq 1 ]; then
    echo "-- Git hosting + project scaffolding --"
    install -m 755 "$REPO_DIR/scripts/new-project.sh" /usr/local/bin/ai-dev-switchboard-new-project.sh
    install -m 755 "$REPO_DIR/scripts/new-repo.sh" /usr/local/bin/ai-dev-switchboard-new-repo.sh
    install -m 755 "$REPO_DIR/scripts/new-dev-instance.sh" /usr/local/bin/ai-dev-switchboard-new-dev-instance.sh
    install -m 755 "$REPO_DIR/scripts/project-sync.sh" /usr/local/bin/ai-dev-switchboard-project-sync.sh
    install -m 755 "$REPO_DIR/scripts/target-setup.sh" /usr/local/bin/ai-dev-switchboard-target-setup.sh
    install -m 755 "$REPO_DIR/scripts/git-hosting-setup.sh" /usr/local/bin/ai-dev-switchboard-git-hosting-setup.sh
    # new-project.sh / new-dev-instance.sh call the others by their
    # installed /usr/local/bin/ai-dev-switchboard-*.sh names directly.

    GH_ENV="$CONFIG_DIR/git-hosting.env"
    [ -f "$GH_ENV" ] || cp "$REPO_DIR/config/git-hosting.env.example" "$GH_ENV"
    set_env "$GH_ENV" RUN_USER "$RUN_USER"
    set_env "$GH_ENV" PROJECTS_DIR "$PROJECTS_DIR"
    /usr/local/bin/ai-dev-switchboard-git-hosting-setup.sh
    set_env "$ENV_FILE" NEW_PROJECT_SCRIPT "/usr/local/bin/ai-dev-switchboard-new-project.sh"
fi

if [ "$WITH_HOST_CONTROL" -eq 1 ]; then
    echo "-- Host-control agent (this machine) --"
    echo "Only the SSH-channel plumbing needs to exist on the machine the web"
    echo "UI's HOST_CONTROL_KEY actually points at — see host-agent/README.md"
    echo "if that's a DIFFERENT machine than this one (the common case)."
    mkdir -p "$INSTALL_DIR/host-agent/lib"
    cp "$REPO_DIR/host-agent/lib/engine-lib.sh" "$INSTALL_DIR/host-agent/lib/"
    for f in host-start host-stop host-status; do
        cp "$REPO_DIR/host-agent/${f}.sh" "$INSTALL_DIR/host-agent/${f}.sh"
        chmod 755 "$INSTALL_DIR/host-agent/${f}.sh"
        ln -sf "$INSTALL_DIR/host-agent/${f}.sh" "/usr/local/bin/ai-dev-switchboard-${f}.sh"
    done
    [ -f "$CONFIG_DIR/host.env" ] || cp "$REPO_DIR/host-agent/host.env.example" "$CONFIG_DIR/host.env"
    set_env "$CONFIG_DIR/host.env" ENGINES_DIR "$CONFIG_DIR/engines.d"
fi

echo ""
echo "== Done =="
echo "Web UI: http://127.0.0.1:$(get_env "$ENV_FILE" LISTEN_PORT):  put a reverse proxy / tailscale serve / SSH tunnel in front (see README.md)."
echo "TOTP secret (add to an authenticator app): $TOTP_SECRET"
[ -n "$SIMPLE_PASSWORD_SHOWN" ] && echo "Generated web UI password: $SIMPLE_PASSWORD_SHOWN"
echo ""
echo "Next: log in as $RUN_USER and run your engine's CLI once interactively"
echo "(e.g. \`claude\`) to finish ITS login, before starting sessions from the UI."
