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
#   --with-taiga          also install Docker + Taiga's own official
#                         taiga-docker Compose stack (self-hosted backlog
#                         tracker), left OFF until toggled in the web UI —
#                         see docs/spec.md and the "Optional: self-hosted
#                         Taiga" section below
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
WITH_TAIGA=0
for arg in "$@"; do
    case "$arg" in
        --yes) YES=1 ;;
        --with-git-hosting) WITH_GIT_HOSTING=1 ;;
        --with-code-server) WITH_CODE_SERVER=1 ;;
        --with-host-control) WITH_HOST_CONTROL=1 ;;
        --with-taiga) WITH_TAIGA=1 ;;
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
path_has_symlink() {  # path_has_symlink <abs path> -> true if any component is a symlink
    local p="$1" check="" part
    local IFS=/
    for part in $p; do
        [ -n "$part" ] || continue
        check="$check/$part"
        [ -L "$check" ] && return 0
    done
    return 1
}

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

if [ "$WITH_CODE_SERVER" -eq 1 ]; then
    echo "-- code-server default theme --"
    CODE_SERVER_DIR="/home/$RUN_USER/.local/share/code-server"
    CODE_SERVER_USER_DIR="$CODE_SERVER_DIR/User"
    CODE_SERVER_SETTINGS="$CODE_SERVER_USER_DIR/settings.json"
    if path_has_symlink "$CODE_SERVER_SETTINGS"; then
        # RUN_USER controls everything under their own home directory, so
        # refuse to mkdir/write through a symlink planted anywhere in this
        # path (dangling or not) — never follow it as root, same as we'd
        # never clobber a real pre-existing settings.json.
        echo "Skipping code-server theme seed: a symlink exists under $CODE_SERVER_DIR — not following it." >&2
    else
        mkdir -p "$CODE_SERVER_USER_DIR"
        if [ ! -f "$CODE_SERVER_SETTINGS" ] && [ ! -L "$CODE_SERVER_SETTINGS" ]; then
            cat > "$CODE_SERVER_SETTINGS" <<'JSON'
{
  "workbench.colorTheme": "Default Dark+"
}
JSON
        fi
        chown -R "$RUN_USER:$RUN_USER" "$CODE_SERVER_DIR"
    fi
fi

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

echo "-- Publishing --"
PUBLISH_MODE=$(prompt "Publish per-project terminals via tailscale serve, or keep them loopback-only? (tailscale/none)" "$(get_env "$ENV_FILE" PUBLISH_MODE)")
set_env "$ENV_FILE" PUBLISH_MODE "$PUBLISH_MODE"
if [ "$PUBLISH_MODE" = "tailscale" ]; then
    BASE_URL=$(prompt "Tailnet hostname per-project terminals get published under (see 'tailscale status' to find it — leave blank to fill in later)" "$(get_env "$ENV_FILE" BASE_URL)")
    set_env "$ENV_FILE" BASE_URL "$BASE_URL"
fi

# ── Optional: self-hosted Taiga (--with-taiga) ───────────────────────────
# Placed here (after Publishing, not right after the code-server block like
# every other --with-* flag) because deriving TAIGA_DOMAIN below needs
# PUBLISH_MODE/BASE_URL already resolved — see docs/spec.md's "Config" step,
# which explicitly wants TAIGA_DOMAIN derived from values "already resolved
# earlier in this same install run". Reuses set_env/get_env/random_token/
# path_has_symlink exactly like every block above it.
if [ "$WITH_TAIGA" -eq 1 ]; then
    echo "-- Self-hosted Taiga (--with-taiga) --"

    # 1. Docker itself — same curl-pipe-sh precedent code-server's own
    # install already uses one block above, rather than the distro's often-
    # stale docker.io apt package. Idempotent: never touch a pre-existing
    # Docker install, however it got there.
    if ! command -v docker >/dev/null 2>&1; then
        echo "Installing Docker (via Docker's own convenience script)..."
        curl -fsSL https://get.docker.com | sh
    fi
    TAIGA_COMPOSE_OK=1
    if ! docker compose version >/dev/null 2>&1; then
        echo "WARNING: 'docker compose' (the Compose plugin, not the old standalone docker-compose v1 binary) isn't available. Taiga will be installed but not functional until you install the Compose plugin yourself (https://docs.docker.com/compose/install/). Continuing." >&2
        TAIGA_COMPOSE_OK=0
    fi

    # 2. The taiga-docker checkout — pinned at whatever commit is first
    # cloned; never `git pull`'d on re-run (docs/spec.md "Open questions").
    TAIGA_DIR=/opt/ai-dev-switchboard-taiga
    TAIGA_FRESH_CLONE=0
    if [ ! -d "$TAIGA_DIR/.git" ]; then
        git clone --branch stable --depth 1 https://github.com/taigaio/taiga-docker.git "$TAIGA_DIR"
        TAIGA_FRESH_CLONE=1
    fi

    # 3. Config — taiga-docker ships a real (not .example) .env file with
    # insecure placeholder defaults (SECRET_KEY="taiga-secret-key", etc.),
    # so unlike switchboard.env's TOTP_SECRET there's no "empty means
    # generate one" signal to key off of. Only randomize secrets right after
    # a fresh clone — never re-randomize on re-run — same "preserved on
    # re-run" behavior TOTP_SECRET/SIMPLE_PASSWORD already get elsewhere in
    # this file. TAIGA_SCHEME/TAIGA_DOMAIN aren't one-time secrets, so they're
    # re-derived every run to track whatever PUBLISH_MODE/BASE_URL currently
    # resolve to.
    TAIGA_PORT=9000
    TAIGA_ENV="$TAIGA_DIR/.env"
    if [ "$TAIGA_FRESH_CLONE" -eq 1 ]; then
        set_env "$TAIGA_ENV" SECRET_KEY "$(random_token 32)"
        set_env "$TAIGA_ENV" POSTGRES_PASSWORD "$(random_token 24)"
        set_env "$TAIGA_ENV" RABBITMQ_PASS "$(random_token 24)"
        set_env "$TAIGA_ENV" RABBITMQ_ERLANG_COOKIE "$(random_token 32)"
    fi
    set_env "$TAIGA_ENV" TAIGA_SCHEME "http"
    if [ "$PUBLISH_MODE" = "tailscale" ] && [ -n "$BASE_URL" ]; then
        TAIGA_DOMAIN_VALUE="${BASE_URL#https://}"
        TAIGA_DOMAIN_VALUE="${TAIGA_DOMAIN_VALUE#http://}"
    else
        TAIGA_DOMAIN_VALUE="localhost:$TAIGA_PORT"
    fi
    set_env "$TAIGA_ENV" TAIGA_DOMAIN "$TAIGA_DOMAIN_VALUE"
    # TAIGA_PORT also lives in taiga-docker's own .env (not just
    # switchboard.env below) — Compose auto-loads .env from the project
    # directory for variable substitution, which is what lets the
    # docker-compose.override.yml below reference ${TAIGA_PORT} without the
    # wrapper scripts needing to export anything themselves.
    set_env "$TAIGA_ENV" TAIGA_PORT "$TAIGA_PORT"

    # 4. Loopback-only binding — taiga-gateway's own docker-compose.yml
    # binds "9000:80" on all interfaces, which conflicts with this project's
    # "everything binds 127.0.0.1 only" rule. Compose auto-merges
    # docker-compose.yml + docker-compose.override.yml in the same
    # directory, so this override never conflicts with a future manual
    # `git pull` in $TAIGA_DIR. Regenerated deterministically every run,
    # like the systemd unit / sudoers file below. The single-quoted heredoc
    # is deliberate: ${TAIGA_PORT} must stay literal here so Compose (not
    # this shell) substitutes it from $TAIGA_DIR/.env at `docker compose`
    # time.
    cat > "$TAIGA_DIR/docker-compose.override.yml" <<'YAML'
services:
  taiga-gateway:
    ports:
      - "127.0.0.1:${TAIGA_PORT}:80"
YAML

    # 5. Pre-pull images at install time, not first toggle — otherwise the
    # first UI toggle-on blocks on pulling 9 images over the network.
    # Warn-and-continue (not fatal) if there's no network right now; the
    # first toggle-on will simply be slow instead.
    if [ "$TAIGA_COMPOSE_OK" -eq 1 ]; then
        echo "Pre-pulling Taiga's images (9 images — this can take a while)..."
        if ! ( cd "$TAIGA_DIR" && docker compose -f docker-compose.yml -f docker-compose.override.yml pull ); then
            echo "WARNING: pre-pulling Taiga's images failed (no network at install time?) — Taiga stays installed, just with uncached images; the first toggle-on will pull them then instead. Continuing." >&2
        fi
    fi

    # 6. Wrapper scripts (root-run, zero arguments — see docs/spec.md
    # "Crossing the privilege boundary"). Sudoers entries for these are
    # added below, alongside every other sudoers rule this installer
    # generates.
    install -m 755 "$REPO_DIR/scripts/taiga-up.sh" /usr/local/bin/ai-dev-switchboard-taiga-up.sh
    install -m 755 "$REPO_DIR/scripts/taiga-down.sh" /usr/local/bin/ai-dev-switchboard-taiga-down.sh
    install -m 755 "$REPO_DIR/scripts/taiga-status.sh" /usr/local/bin/ai-dev-switchboard-taiga-status.sh

    # 7. switchboard.env — TAIGA_DIR is also recorded here (beyond what
    # app.py itself reads) because the wrapper scripts above source this
    # same file for it, exactly like new-project-from-upload.sh already
    # sources RUN_USER/PROJECTS_DIR from it.
    set_env "$ENV_FILE" TAIGA_ENABLED 1
    set_env "$ENV_FILE" TAIGA_PORT "$TAIGA_PORT"
    set_env "$ENV_FILE" TAIGA_LABEL "Taiga"
    set_env "$ENV_FILE" TAIGA_DIR "$TAIGA_DIR"
    set_env "$ENV_FILE" TAIGA_UP_SCRIPT "/usr/local/bin/ai-dev-switchboard-taiga-up.sh"
    set_env "$ENV_FILE" TAIGA_DOWN_SCRIPT "/usr/local/bin/ai-dev-switchboard-taiga-down.sh"
    set_env "$ENV_FILE" TAIGA_STATUS_SCRIPT "/usr/local/bin/ai-dev-switchboard-taiga-status.sh"
fi

if [ "$WITH_HOST_CONTROL" -eq 1 ]; then
    set_env "$ENV_FILE" HOST_CONTROL_ENABLED 1
fi

echo "-- Folder-upload wizard (works standalone, no --with-git-hosting needed) --"
install -m 755 "$REPO_DIR/scripts/new-project-from-upload.sh" \
    /usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh
mkdir -p "$STATE_DIR/uploads"
chown "$SVC_USER:$SVC_USER" "$STATE_DIR/uploads"
set_env "$ENV_FILE" NEW_PROJECT_FROM_UPLOAD_SCRIPT \
    "/usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh"
set_env "$ENV_FILE" UPLOAD_STAGING_TTL_SECONDS "1800"

chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "-- sudoers (scoped: $SVC_USER can only run tmux/ttyd/code-server AS $RUN_USER) --"
SUDOERS=/etc/sudoers.d/ai-dev-switchboard
{
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/bin/tmux *"
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/ttyd *"
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/code-server *"
    # Unconditional (not gated behind --with-git-hosting) — the folder-upload
    # wizard is explicitly the project-registration path for people WITHOUT
    # git hosting installed.
    echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh *"
    if [ "$WITH_GIT_HOSTING" -eq 1 ]; then
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project.sh *"
    fi
    if [ "$WITH_TAIGA" -eq 1 ]; then
        # Zero arguments (no trailing " *") — narrower than every other rule
        # above, since Docker socket access is root-equivalent (see
        # docs/spec.md "Crossing the privilege boundary"): these three fixed
        # scripts are the entire narrowing, no passthrough arguments at all.
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-taiga-up.sh"
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-taiga-down.sh"
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-taiga-status.sh"
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
if [ "$PUBLISH_MODE" = "tailscale" ]; then
    echo "Publish mode: tailscale (per-project terminals will auto-publish via 'tailscale serve --set-path'). The main UI itself is NOT auto-published — still run 'tailscale serve --bg https+insecure://127.0.0.1:$(get_env "$ENV_FILE" LISTEN_PORT)' yourself (see README.md \"Reaching the UI\")."
fi
echo "TOTP secret (add to an authenticator app): $TOTP_SECRET"
[ -n "$SIMPLE_PASSWORD_SHOWN" ] && echo "Generated web UI password: $SIMPLE_PASSWORD_SHOWN"
if [ "$WITH_TAIGA" -eq 1 ]; then
    echo ""
    echo "Taiga: installed but left OFF — flip the 'Taiga' row's toggle in the"
    echo "web UI to start it. Runs 9 containers and can use several GB of RAM"
    echo "(and real disk, for Postgres/RabbitMQ data volumes) once turned on;"
    echo "toggling it back off frees that RAM again right away."
    echo "Before first use, create Taiga's own admin account (one-time,"
    echo "interactive — not automated by this installer):"
    echo "  cd $TAIGA_DIR && ./taiga-manage.sh createsuperuser"
    echo "(run that after the Taiga toggle is on and the stack has finished starting)."
fi
echo ""
echo "Next: log in as $RUN_USER and run your engine's CLI once interactively"
echo "(e.g. \`claude\`) to finish ITS login, before starting sessions from the UI."
