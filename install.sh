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
#   --with-git-hosting    also installs Docker + a self-hosted Gitea Compose
#                         stack (server + db), left OFF until toggled in the
#                         web UI — the "+ New project" button then creates
#                         real Gitea repos via its own REST API and clones
#                         them into PROJECTS_DIR (see docs/GIT_HOSTING.md,
#                         docs/spec.md backlog item 2b). Two one-time manual
#                         steps after toggling Gitea on (create its admin
#                         account, then run scripts/gitea-configure-api.sh)
#                         — see this script's own printed summary.
#   --with-code-server    also install code-server (VS Code in the browser)
#   --with-host-control   also install host-agent/ on THIS machine (see
#                         host-agent/README.md — usually installed on a
#                         *different* machine than the web UI by hand instead)
#   --with-deploy-target  provision THIS machine as a deploy receiver (see
#                         deploy-target/README.md) — run on a *separate*
#                         target machine, never the switchboard box itself
#   --with-taiga          also install Docker + Taiga's own official
#                         taiga-docker Compose stack (self-hosted backlog
#                         tracker), left OFF until toggled in the web UI —
#                         see docs/spec.md and the "Optional: self-hosted
#                         Taiga" section below
#   --with-ollama         LINK an existing, already-running remote Ollama for
#                         multi-agent team leads (docs/story.md §2.5) —
#                         installs nothing locally (no package, no model
#                         pull, no container, no systemd unit). Prompts for
#                         an endpoint URL + model name, validates both are
#                         actually reachable/present, writes TEAM_LLM_BASE_URL/
#                         TEAM_LLM_MODEL. Refuses to write config it can't
#                         verify — see docs/spec.md (6d part 2b)
#   --update, --upgrade   update an already-installed box: fast-forwards
#                         this checkout to origin/$REPO_BRANCH (never a
#                         destructive reset — refuses on local changes, a
#                         branch mismatch, or real divergence from origin),
#                         re-runs the steps below against that fresh
#                         checkout, then restarts ai-dev-switchboard — but
#                         ONLY if RUN_USER has no live tmux session; if one
#                         is live, the restart is deferred (code is still
#                         updated) and the script prints how to finish by
#                         hand once it's safe. Exact synonyms, same flag.
#
# Safe to re-run: every step here either checks for existing state first or
# overwrites deterministically-generated files (units, sudoers), never
# clobbers switchboard.env values that are already set. --update
# additionally pulls $REPO_DIR first and may defer its own service restart
# around live sessions (see above) — nothing else changes when it's absent.
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
WITH_DEPLOY_TARGET=0
WITH_TAIGA=0
WITH_OLLAMA=0
UPDATE=0
for arg in "$@"; do
    case "$arg" in
        --yes) YES=1 ;;
        --with-git-hosting) WITH_GIT_HOSTING=1 ;;
        --with-code-server) WITH_CODE_SERVER=1 ;;
        --with-host-control) WITH_HOST_CONTROL=1 ;;
        --with-deploy-target) WITH_DEPLOY_TARGET=1 ;;
        --with-taiga) WITH_TAIGA=1 ;;
        --with-ollama) WITH_OLLAMA=1 ;;
        --update|--upgrade) UPDATE=1 ;;
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
# Hoisted up from the "-- Config --" step below (still assigned there too,
# idempotently) so the RUN_USER/SVC_USER prompts further down can read an
# already-configured value via get_env, same as PVE_HOST/SIMPLE_USERNAME/
# PUBLISH_MODE already do (docs/BACKLOG.md item 14).
ENV_FILE="$CONFIG_DIR/switchboard.env"

# ── Update (--update/--upgrade): pull latest $REPO_BRANCH into $REPO_DIR,
# before anything else in this script reads from it (docs/BACKLOG.md item
# 14). Never a destructive `git reset --hard` -- fetch, then a
# `merge --ff-only`, mirroring scripts/gitea-sync-project.sh's own
# fetch-then-ff-only-merge shape (dirty check, ancestor check, never reset
# --hard).
if [ "$UPDATE" -eq 1 ]; then
    echo "-- Update (--update/--upgrade): pulling $REPO_BRANCH into $REPO_DIR --"
    if [ ! -d "$REPO_DIR/.git" ]; then
        echo "ERROR: $REPO_DIR is not a git checkout, so --update has nothing to pull. Re-run via the curl-pipe installer (which clones \$SRC_DIR itself), or 'git clone $REPO_URL' and run install.sh --update from inside that clone." >&2
        exit 1
    fi
    if [ -n "$(git -C "$REPO_DIR" status --porcelain)" ]; then
        echo "ERROR: $REPO_DIR has uncommitted local changes -- refusing to pull over them. Commit, stash, or discard them, then re-run --update." >&2
        exit 1
    fi
    CURRENT_BRANCH="$(git -C "$REPO_DIR" symbolic-ref --short -q HEAD || true)"
    if [ "$CURRENT_BRANCH" != "$REPO_BRANCH" ]; then
        echo "ERROR: $REPO_DIR is checked out on '${CURRENT_BRANCH:-<detached HEAD>}', not \$REPO_BRANCH ('$REPO_BRANCH') -- switch to $REPO_BRANCH yourself (or set REPO_BRANCH to match what's checked out) before re-running --update." >&2
        exit 1
    fi
    git -C "$REPO_DIR" fetch origin "$REPO_BRANCH"
    if ! git -C "$REPO_DIR" merge-base --is-ancestor HEAD "origin/$REPO_BRANCH"; then
        echo "ERROR: $REPO_DIR's local $REPO_BRANCH has diverged from origin/$REPO_BRANCH -- refusing to merge. Resolve by hand, then re-run --update." >&2
        exit 1
    fi
    git -C "$REPO_DIR" merge --ff-only "origin/$REPO_BRANCH"
    echo "Pulled $(git -C "$REPO_DIR" rev-parse --short HEAD) ($REPO_BRANCH)."
fi

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
    local file="$1" key="$2" val="$3" val_escaped
    # $val is operator-supplied and must never be shelled through sed's
    # pattern language unescaped (docs/BACKLOG.md item 10): a literal `|`
    # breaks this sed expression (aborting the whole install.sh run on a
    # re-run) and a literal `&` is silently corrupted via sed's whole-match
    # backreference. Escape backslash first (so the escaping added for
    # `&`/`|` below isn't itself re-escaped), then `&`, then the `|`
    # delimiter. The `>>` append path (first-write case) never goes through
    # sed, so it already handles any character correctly and is untouched.
    val_escaped=$(printf '%s' "$val" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val_escaped}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}
get_env() {  # get_env <file> <KEY> -> value or empty
    grep "^${2}=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true
}
random_token() { head -c "${1:-16}" /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-16}"; }
ensure_docker() {  # ensure_docker -> installs Docker if missing, verifies the
    # Compose plugin, sets DOCKER_COMPOSE_OK=1/0. Idempotent (never touches a
    # pre-existing Docker install, however it got there) and safe to call more
    # than once in the same run — shared by both --with-taiga and
    # --with-git-hosting (see docs/spec.md "install.sh changes" #1) so a
    # single install using both flags only installs/checks Docker once.
    if ! command -v docker >/dev/null 2>&1; then
        echo "Installing Docker (via Docker's own convenience script)..."
        curl -fsSL https://get.docker.com | sh
    fi
    DOCKER_COMPOSE_OK=1
    if ! docker compose version >/dev/null 2>&1; then
        echo "WARNING: 'docker compose' (the Compose plugin, not the old standalone docker-compose v1 binary) isn't available. Continuing, but the Docker Compose stack(s) below will be installed without being functional until you install the Compose plugin yourself (https://docs.docker.com/compose/install/)." >&2
        DOCKER_COMPOSE_OK=0
    fi
}
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
apt-get install -y -qq python3 tmux git curl sudo rsync openssh-client ca-certificates acl

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

# Item 41 (docs/BACKLOG.md): the code-server.dev installer actually puts
# the binary at /usr/bin/code-server on a real Debian 12 box, not
# /usr/local/bin/code-server -- a hardcoded /usr/local/bin/ path here made
# this idempotency check re-trigger the install every single run. Detect
# "does a usable code-server already exist anywhere on PATH" via `command
# -v` instead of one specific location.
if [ "$WITH_CODE_SERVER" -eq 1 ] && ! command -v code-server >/dev/null 2>&1; then
    echo "-- Installing code-server --"
    curl -fsSL https://code-server.dev/install.sh | sh
fi
# Resolve the real installed path once, here, and keep every other
# reference (the sudoers rule below, and app.py's CODE_SERVER_BIN) in sync
# with this single resolved value -- a mismatch between this and the
# sudoers rule would trade "silent no-op" for "permission denied", still
# broken (docs/spec.md Item 41). Falls back to the pre-existing literal
# default only when `command -v` finds nothing (WITH_CODE_SERVER=0, or a
# fresh box with code-server disabled), so this never errors out here.
CODE_SERVER_BIN="$(command -v code-server 2>/dev/null || true)"
CODE_SERVER_BIN="${CODE_SERVER_BIN:-/usr/local/bin/code-server}"
set_env "$ENV_FILE" CODE_SERVER_BIN "$CODE_SERVER_BIN"

echo "-- Users --"
RUN_USER_DEFAULT="$(get_env "$ENV_FILE" RUN_USER)"; RUN_USER_DEFAULT="${RUN_USER_DEFAULT:-dev}"
SVC_USER_DEFAULT="$(get_env "$ENV_FILE" SVC_USER)"; SVC_USER_DEFAULT="${SVC_USER_DEFAULT:-switchboard-svc}"
RUN_USER=$(prompt "Unprivileged user to run coding sessions as" "$RUN_USER_DEFAULT")
SVC_USER=$(prompt "Unprivileged user to run the web UI process as" "$SVC_USER_DEFAULT")

id "$RUN_USER" &>/dev/null || { useradd -m -s /bin/bash "$RUN_USER"; echo "Created $RUN_USER"; }
id "$SVC_USER" &>/dev/null || { useradd -r -m -d "/home/$SVC_USER" -s /usr/sbin/nologin "$SVC_USER"; echo "Created $SVC_USER"; }
# Item 24 fix: $STATE_DIR itself was never chowned (only its later-created
# uploads/ subdirectory was) -- SVC_USER couldn't write directly under it
# (e.g. GITEA_REPO_MAP_FILE). SVC_USER doesn't exist until the useradd
# above, so this has to live here rather than next to $STATE_DIR's mkdir.
chown "$SVC_USER:$SVC_USER" "$STATE_DIR"
# Item 27 fix: SVC_USER (running app.py) needs to run read-only git
# inspection commands (_check_git_repo_state()) against every RUN_USER-
# owned project directory. Git's "dubious ownership" protection
# (CVE-2022-24765) refuses this by default across a user boundary. `*`
# (not a glob against a fixed path) is required -- git's own
# safe.directory only matches literal paths or the literal string `*`,
# and projects are created dynamically after install, so a fixed list of
# literal paths can't work here. Bounded, deliberate trade-off: SVC_USER
# only ever runs read-only inspection git commands directly (writes
# already cross into RUN_USER via sudo -u), so this doesn't hand out any
# privilege beyond what the account already effectively has.
sudo -u "$SVC_USER" git config --global --add safe.directory '*'

PROJECTS_DIR="/home/$RUN_USER/projects"
mkdir -p "$PROJECTS_DIR"
chown "$RUN_USER:$RUN_USER" "$PROJECTS_DIR"

# Switchboard-side deploy dispatch (backlog item 2c, part 2b -- docs/spec.md).
# Unconditional (no --with-* flag -- this feature has no install-time
# on/off switch, only a data-presence gate: no project shows a Deploy
# button until deploy-map.json has real hand-edited entries in it).
# Permissions/ownership are reasserted every run (harmless, idempotent) --
# never touches key *content*, the operator places keys here by hand.
mkdir -p "$CONFIG_DIR/deploy-keys"
chmod 700 "$CONFIG_DIR/deploy-keys"
chown "$SVC_USER:$SVC_USER" "$CONFIG_DIR/deploy-keys"

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
        chown -R "$RUN_USER:$RUN_USER" "/home/$RUN_USER/.local"
    fi
fi

echo "-- App + engines --"
cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"
cp "$REPO_DIR/app/teams.py" "$INSTALL_DIR/teams.py"
cp "$REPO_DIR/app/taiga_board.py" "$INSTALL_DIR/taiga_board.py"
mkdir -p "$CONFIG_DIR/engines.d"
for f in "$REPO_DIR"/engines.d/*.engine; do
    [ -e "$f" ] || continue
    dest="$CONFIG_DIR/engines.d/$(basename "$f")"
    [ -e "$dest" ] || cp "$f" "$dest"
done

echo "-- Config --"
# ENV_FILE itself is already assigned above (hoisted so the RUN_USER/
# SVC_USER prompts could read it too -- see that comment).
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

AUTH_MODE_DEFAULT="$(get_env "$ENV_FILE" AUTH_MODE)"; AUTH_MODE_DEFAULT="${AUTH_MODE_DEFAULT:-simple}"
AUTH_MODE=$(prompt "Auth mode: simple (username+password) or pve (Proxmox VE login)" "$AUTH_MODE_DEFAULT")
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
    # stale docker.io apt package. Factored into the shared ensure_docker()
    # helper (defined near set_env/get_env above) so --with-git-hosting's own
    # Gitea steps below can reuse it instead of a second copy-pasted block —
    # pure refactor, no behavior change here (docs/spec.md "install.sh
    # changes" #1).
    ensure_docker
    TAIGA_COMPOSE_OK=$DOCKER_COMPOSE_OK

    # 2. The taiga-docker checkout — pinned at whatever commit is first
    # cloned; never `git pull`'d on re-run (docs/spec.md "Open questions").
    TAIGA_DIR=/opt/ai-dev-switchboard-taiga
    TAIGA_FRESH_CLONE=0
    if [ ! -d "$TAIGA_DIR/.git" ]; then
        git clone --branch stable --depth 1 https://github.com/taigaio/taiga-docker.git "$TAIGA_DIR"
        TAIGA_FRESH_CLONE=1
    fi

    # 3. Item 44 (round 9): patch the pinned checkout's own
    # docker-compose.yml `ports:` line for taiga-gateway directly, in place
    # -- Compose merges list fields like `ports:` by *concatenation*, not by
    # replacement (unlike `volumes:`, which item 43 below relies on merging
    # by container target path), so a docker-compose.override.yml `ports:`
    # entry can never actually close the upstream file's own unrestricted
    # "9000:80" binding; it just adds a second, losing bind, and
    # taiga-gateway ends up running with no port published at all
    # (docs/spec.md "Background / current state", item 44). This is a
    # narrow, explicit exception to item 30's "don't patch the pinned
    # checkout" decision (see step 5 below) -- scoped to exactly this one
    # `ports:` line; taiga.conf (item 43) stays entirely on the
    # override-file/bind-mount mechanism, untouched here. Runs
    # unconditionally on every install.sh invocation (not gated on
    # TAIGA_FRESH_CLONE), so a pre-fix $TAIGA_DIR cloned before this change
    # shipped still gets patched on its next re-run, not just a brand-new
    # clone. grep-gated before the sed so a second/third run against an
    # already-patched checkout is a clean, byte-identical no-op instead of a
    # double-patch; warns loudly to stderr (does not fail the install) if
    # neither the expected original line nor the already-patched form is
    # found, in case a future re-clone of a newer upstream commit changes
    # this line's exact format. ${TAIGA_PORT} in the replacement is
    # single-quoted/literal on purpose -- Compose (not this shell) resolves
    # it from $TAIGA_DIR/.env at `docker compose` time.
    TAIGA_COMPOSE_YML="$TAIGA_DIR/docker-compose.yml"
    if grep -q '^[[:space:]]*-[[:space:]]*"9000:80"[[:space:]]*$' "$TAIGA_COMPOSE_YML"; then
        sed -i 's|^\([[:space:]]*-[[:space:]]*\)"9000:80"[[:space:]]*$|\1"127.0.0.1:${TAIGA_PORT}:80"|' "$TAIGA_COMPOSE_YML"
    elif ! grep -q '"127\.0\.0\.1:\${TAIGA_PORT}:80"' "$TAIGA_COMPOSE_YML"; then
        echo "WARNING: expected taiga-gateway's 'ports: - \"9000:80\"' line in $TAIGA_COMPOSE_YML but didn't find it (upstream taiga-docker format may have changed) -- taiga-gateway's host port may end up published on all interfaces (0.0.0.0) instead of loopback-only. Check $TAIGA_COMPOSE_YML's taiga-gateway service manually." >&2
    fi

    # 4. Config — taiga-docker ships a real (not .example) .env file with
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
    # TAIGA_SCHEME has to be the scheme the BROWSER speaks, not the one
    # taiga-gateway itself listens on (which is always plain http on :80
    # behind the published port). taiga-front bakes this value straight into
    # conf.json's `api` URL, so hardcoding "http" while the UI is actually
    # served over https (PUBLISH_MODE=tailscale -> `tailscale serve`) makes
    # the SPA issue http:// XHRs from an https:// page. Browsers block that
    # as mixed content, so the page still renders fully styled while every
    # API call — login, navigation, project load — silently fails. Derived
    # from BASE_URL rather than assumed, and mirrors the ws/wss choice
    # WEBSOCKETS_SCHEME already makes for the very same reason.
    if [ "$PUBLISH_MODE" = "tailscale" ] && [ -n "$BASE_URL" ]; then
        case "$BASE_URL" in
            https://*) TAIGA_SCHEME_VALUE="https" ;;
            *)         TAIGA_SCHEME_VALUE="http" ;;
        esac
        TAIGA_DOMAIN_VALUE="${BASE_URL#https://}"
        TAIGA_DOMAIN_VALUE="${TAIGA_DOMAIN_VALUE#http://}"
    else
        TAIGA_SCHEME_VALUE="http"
        TAIGA_DOMAIN_VALUE="localhost:$TAIGA_PORT"
    fi
    set_env "$TAIGA_ENV" TAIGA_SCHEME "$TAIGA_SCHEME_VALUE"
    set_env "$TAIGA_ENV" TAIGA_DOMAIN "$TAIGA_DOMAIN_VALUE"

    # 4b. taiga-front's own SUBPATH/WEBSOCKETS_SCHEME (backlog item 47b).
    # Confirmed against the cloned checkout's own $TAIGA_DIR/docker-
    # compose.yml: taiga-front's `environment:` block reads TAIGA_URL/
    # TAIGA_WEBSOCKETS_URL/TAIGA_SUBPATH from ${TAIGA_SCHEME}/${TAIGA_DOMAIN}/
    # ${WEBSOCKETS_SCHEME}/${SUBPATH} — there's no separate taiga-front/.env
    # file the way upstream's own docs initially suggest; it's the exact
    # same root $TAIGA_ENV Compose already auto-loads for taiga-back/
    # taiga-gateway. TAIGA_SCHEME/TAIGA_DOMAIN above already reach
    # taiga-front for free through this same file; only SUBPATH/
    # WEBSOCKETS_SCHEME were never written anywhere. TAIGA_URL_PATH mirrors
    # app.py's own fixed TAIGA_URL_PATH constant (app/app.py:2978, singleton,
    # same shape as Gitea's GITEA_URL_PATH above) — and, per app.py's own
    # _taiga_display_url(), that subpath only applies under
    # PUBLISH_MODE=tailscale; PUBLISH_MODE=none access is direct
    # (http://127.0.0.1:$TAIGA_PORT, no subpath prefix at all), so SUBPATH/
    # WEBSOCKETS_SCHEME follow the same PUBLISH_MODE/BASE_URL conditional as
    # TAIGA_DOMAIN above rather than being unconditional constants.
    TAIGA_URL_PATH="/taiga"  # mirrors app.py's TAIGA_URL_PATH constant
    if [ "$PUBLISH_MODE" = "tailscale" ] && [ -n "$BASE_URL" ]; then
        TAIGA_SUBPATH_VALUE="$TAIGA_URL_PATH"
        TAIGA_WEBSOCKETS_SCHEME_VALUE="wss"
    else
        TAIGA_SUBPATH_VALUE=""
        TAIGA_WEBSOCKETS_SCHEME_VALUE="ws"
    fi
    set_env "$TAIGA_ENV" SUBPATH "$TAIGA_SUBPATH_VALUE"
    set_env "$TAIGA_ENV" WEBSOCKETS_SCHEME "$TAIGA_WEBSOCKETS_SCHEME_VALUE"

    # TAIGA_PORT also lives in taiga-docker's own .env (not just
    # switchboard.env below) — Compose auto-loads .env from the project
    # directory for variable substitution, which is what lets
    # docker-compose.yml's own taiga-gateway ports: line (patched in step 3
    # above) reference ${TAIGA_PORT} without the wrapper scripts needing to
    # export anything themselves.
    set_env "$TAIGA_ENV" TAIGA_PORT "$TAIGA_PORT"

    # 5. (item 30) Health-gating taiga-gateway's startup on taiga-front
    # actually being resolvable — taiga-gateway's own docker-compose.yml
    # binds "9000:80" on all interfaces by default (now patched to
    # loopback-only at the source, in step 3 above — item 44), and its
    # taiga.conf does `proxy_pass http://taiga-front/;` with no `resolver`
    # directive, so nginx resolves that hostname once at startup — if
    # taiga-front isn't attached to the network yet (an ordinary Compose
    # startup-ordering race; upstream's own `depends_on: [taiga-front, ...]`
    # only waits for it to *start*, not to be ready), taiga-gateway crashes
    # immediately and, because the exited container retains its port
    # reservation, every remove+recreate collides on the same port and can
    # never recover (docs/BACKLOG.md item 30). taiga-front has no healthcheck
    # upstream, so one is added here, and taiga-gateway's depends_on for it
    # is upgraded to `condition: service_healthy` (the same long-form
    # depends_on/condition syntax upstream's own docker-compose.yml already
    # uses for taiga-back -> taiga-db, proven compatible with this stack) —
    # Compose won't start taiga-gateway until taiga-front is genuinely
    # healthy, eliminating the race instead of retrying into it. This is
    # deliberately done here, in the override file this repo already
    # regenerates deterministically every run, and not by patching
    # taiga.conf/docker-compose.yml inside the pinned, third-party
    # taiga-docker checkout itself (docs/spec.md "Proposed approach" —
    # Item 30 architecture decision; step 3 above's `ports:` patch is a
    # narrow, explicit, later exception to this for exactly one line —
    # item 44, round 9). The healthcheck test command uses
    # `http://127.0.0.1/`, not `http://localhost/` — confirmed hands-on
    # against the real taigaio/taiga-front:latest image that its bundled
    # nginx only listens on 0.0.0.0:80 (no IPv6 listener), so BusyBox wget
    # resolving "localhost" to ::1 first gets "Connection refused" even
    # though the server is up; 127.0.0.1 sidesteps that entirely (BusyBox
    # wget has no -4/--prefer-family flag to force IPv4 for a "localhost"
    # URL instead). Compose merges depends_on per-service-key across -f
    # files, so listing all three of taiga-gateway's existing dependencies
    # here (only taiga-front's condition actually changes) is both correct
    # and self-documenting. Compose auto-merges docker-compose.yml +
    # docker-compose.override.yml in the same directory, so this override
    # never conflicts with a future manual `git pull` in $TAIGA_DIR.
    # Regenerated deterministically every run, like the systemd unit /
    # sudoers file below.
    #
    # Item 43 (round 8): item 30's healthcheck above narrows the DNS race
    # for taiga-front specifically but doesn't close it — nginx still
    # resolves all 4 of taiga-gateway's proxy_pass upstream hostnames
    # (taiga-front, taiga-back x2, taiga-events, taiga-protected) exactly
    # once, at config-load time, so a `service_healthy` gate that only
    # covers taiga-front's own container being up doesn't guarantee
    # taiga-front's (or any of the other three, which have no healthcheck
    # at all) DNS record has actually propagated through Docker's embedded
    # resolver (127.0.0.11) at that exact instant — confirmed live: gateway
    # still crashed with the identical taiga-front DNS error even with the
    # healthcheck gate in place. The real fix is nginx-side: make it resolve
    # those hostnames lazily, at request time, instead of once at startup.
    # docker-compose.override.taiga-gateway.conf (written just below) is
    # upstream's own taiga-gateway/taiga.conf with a `resolver 127.0.0.11
    # valid=10s;` directive added and each bare-hostname proxy_pass target
    # rewritten to a `set $upstream_x <host>; proxy_pass
    # http://$upstream_x...;` variable indirection — nginx only performs the
    # DNS lookup for a variable proxy_pass target at request time, honoring
    # `resolver`'s TTL, which is nginx's own documented fix for this exact
    # class of Docker-Compose startup-DNS race. It's bind-mounted over the
    # pinned checkout's own taiga.conf via the `volumes:` entry added to
    # taiga-gateway below — Compose merges `volumes:` entries by container
    # target path across -f files, so this single entry *replaces only* the
    # /etc/nginx/conf.d/default.conf mount; the base file's
    # taiga-static-data/taiga-media-data mounts on the same service are left
    # untouched. This is the same override-file idiom already used for
    # depends_on above, just extended to volumes — the pinned checkout's own
    # $TAIGA_DIR/taiga-gateway/taiga.conf (as opposed to its
    # docker-compose.yml, which step 3 above does patch for item 44) is
    # never opened for writing. Item 30's healthcheck/service_healthy gate
    # is kept in place as defense-in-depth alongside this fix, not replaced
    # by it.
    #
    # Item 47(a) (backlog item 47, reviewer-found post-approval regression
    # -- docs/test-review.md Defect 1): `location /`'s `proxy_pass
    # http://$upstream_front/;` looked like the same variable-indirection
    # fix items 42/43 already applied successfully to /api/, /admin/,
    # /media/ below -- but live-tested with a real nginx+backend pair, it
    # collapses every request path to a bare `/`, so taiga-front's
    # index.html is served for every asset/API/conf.json request instead of
    # the real file (confirmed live against both the real running gateway
    # container and an isolated scratch nginx+fake-backend reproduction
    # this session). Root cause: unlike /api/, /admin/, /media/ (whose
    # proxy_pass target has a real trailing path segment after the
    # variable, e.g. ".../api/"), `location /`'s target ends in a bare "/"
    # with nothing else -- for that specific bare-URI + variable-upstream
    # combination, nginx does not append the client's actual request path
    # to the forwarded request the way it does for the other locations.
    # Fixed by explicitly appending $request_uri (nginx's own raw,
    # unmodified request URI including any query string) to the proxy_pass
    # target, forwarding the exact path the client requested instead of a
    # constant "/" -- verified live (isolated nginx+fake-backend
    # reproduction): distinct real content for /, /conf.json, and
    # /js/app-loader.js, and a genuine 404 (not index.html) for a
    # nonexistent path, both with and without a query string. Left
    # /api/, /admin/, /media/ untouched -- reviewer confirmed those already
    # forward correctly today; this is a narrow, targeted fix for the one
    # broken location, not a blanket rewrite of the whole file.
    cat > "$TAIGA_DIR/docker-compose.override.taiga-gateway.conf" <<'NGINX'
server {
    listen 80 default_server;

    client_max_body_size 100M;
    charset utf-8;

    resolver 127.0.0.11 valid=10s;

    # Frontend
    location / {
        set $upstream_front taiga-front;
        proxy_pass http://$upstream_front$request_uri;
        proxy_pass_header Server;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
    }

    # API
    location /api/ {
        set $upstream_back taiga-back;
        # $request_uri, NOT a literal "/api/". When proxy_pass contains
        # BOTH a variable and a URI part, nginx passes that URI as-is,
        # *replacing* the original request URI -- so /api/v1/stats/discover
        # arrived at taiga-back as bare /api/ and 404'd. Every API call
        # failed: the SPA loaded fully styled but could not navigate or log
        # in. The `location /` block above already had the correct shape.
        proxy_pass http://$upstream_back:8000$request_uri;
        proxy_pass_header Server;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
    }

    # Admin
    location /admin/ {
        set $upstream_back taiga-back;
        # Same variable-plus-URI nginx trap as /api/ above: a literal
        # "/admin/" here would replace the whole request URI, collapsing
        # every /admin/... path to bare /admin/.
        proxy_pass http://$upstream_back:8000$request_uri;
        proxy_pass_header Server;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
    }

    # Static
    location /static/ {
        alias /taiga/static/;
    }

    # Media
    location /_protected/ {
        internal;
        alias /taiga/media/;
        add_header Content-disposition "attachment";
    }

    # Unprotected section
    location /media/exports/ {
        alias /taiga/media/exports/;
        add_header Content-disposition "attachment";
    }

    location /media/ {
        set $upstream_protected taiga-protected;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://$upstream_protected:8003/;
        proxy_redirect off;
    }

    # Events
    location /events {
        set $upstream_events taiga-events;
        proxy_pass http://$upstream_events:8888/events;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }
}
NGINX
    cat > "$TAIGA_DIR/docker-compose.override.yml" <<'YAML'
services:
  taiga-front:
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1/ || exit 1"]
      interval: 2s
      timeout: 3s
      retries: 30
      start_period: 5s
  taiga-gateway:
    volumes:
      - ./docker-compose.override.taiga-gateway.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      taiga-front:
        condition: service_healthy
      taiga-back:
        condition: service_started
      taiga-events:
        condition: service_started
YAML

    # 6. Pre-pull images at install time, not first toggle — otherwise the
    # first UI toggle-on blocks on pulling 9 images over the network.
    # Warn-and-continue (not fatal) if there's no network right now; the
    # first toggle-on will simply be slow instead.
    if [ "$TAIGA_COMPOSE_OK" -eq 1 ]; then
        echo "Pre-pulling Taiga's images (9 images — this can take a while)..."
        if ! ( cd "$TAIGA_DIR" && docker compose -f docker-compose.yml -f docker-compose.override.yml pull ); then
            echo "WARNING: pre-pulling Taiga's images failed (no network at install time?) — Taiga stays installed, just with uncached images; the first toggle-on will pull them then instead. Continuing." >&2
        fi
    fi

    # 7. Wrapper scripts (root-run, zero arguments — see docs/spec.md
    # "Crossing the privilege boundary"). Sudoers entries for these are
    # added below, alongside every other sudoers rule this installer
    # generates.
    install -m 755 "$REPO_DIR/scripts/taiga-up.sh" /usr/local/bin/ai-dev-switchboard-taiga-up.sh
    install -m 755 "$REPO_DIR/scripts/taiga-down.sh" /usr/local/bin/ai-dev-switchboard-taiga-down.sh
    install -m 755 "$REPO_DIR/scripts/taiga-status.sh" /usr/local/bin/ai-dev-switchboard-taiga-status.sh

    # 8. switchboard.env — TAIGA_DIR is also recorded here (beyond what
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

# Switchboard-side deploy dispatch (backlog item 2c, part 2b -- docs/spec.md
# "Proposed approach" #7). Copy-if-absent ONLY -- unlike every set_env call
# in this file, deploy-map.json is a hand-edited-by-the-operator JSON object
# keyed per-project, not a KEY=VALUE file with single-key patch semantics,
# so a re-run must never overwrite a real hand-edited map (deploy-keys/'s
# permissions/ownership above are still reasserted every run; this file is
# not).
[ -f "$CONFIG_DIR/deploy-map.json" ] || echo '{}' > "$CONFIG_DIR/deploy-map.json"
set_env "$ENV_FILE" DEPLOY_MAP_FILE "$CONFIG_DIR/deploy-map.json"
set_env "$ENV_FILE" DEPLOY_KEYS_DIR "$CONFIG_DIR/deploy-keys"

chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Item 25 fix: a small, deliberately world-readable (644) sibling file
# holding ONLY non-secret, install-time-static values -- gitea-sync-
# project.sh runs as RUN_USER (not SVC_USER) and cannot read the 600
# switchboard.env, but needs RUN_USER/PROJECTS_DIR to locate the project
# it's syncing. Never write a secret into this file.
# Item 29 (v2): SVC_USER's own literal username is added here too -- not a
# secret (same category as RUN_USER/PROJECTS_DIR above), and lets scripts
# like taiga-configure-push.sh (which runs as RUN_USER, not SVC_USER) look
# up who to grant a read ACL to without hardcoding "switchboard-svc".
RUNTIME_ENV_FILE="$CONFIG_DIR/runtime.env"
cat > "$RUNTIME_ENV_FILE" <<EOF
RUN_USER=$RUN_USER
PROJECTS_DIR=$PROJECTS_DIR
SVC_USER=$SVC_USER
EOF
chmod 644 "$RUNTIME_ENV_FILE"

echo "-- Clone project from URL (backlog item 16, works standalone, no --with-git-hosting needed) --"
install -m 755 "$REPO_DIR/scripts/new-project-from-url.sh" \
    /usr/local/bin/ai-dev-switchboard-new-project-from-url.sh
set_env "$ENV_FILE" NEW_PROJECT_FROM_URL_SCRIPT \
    "/usr/local/bin/ai-dev-switchboard-new-project-from-url.sh"
# CLONE_TIMEOUT_SECONDS/CLONE_MAX_BYTES stay commented-out optional
# overrides in config/switchboard.env.example (app.py's/the script's own
# defaults already cover them) -- same "not force-set by install.sh"
# treatment UPLOAD_MAX_BYTES/UPLOAD_MAX_ENTRIES already get.

echo "-- sudoers (scoped: $SVC_USER can only run tmux/ttyd/code-server AS $RUN_USER) --"
SUDOERS=/etc/sudoers.d/ai-dev-switchboard
{
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/bin/tmux *"
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/ttyd *"
    # Item 41: must reference the same resolved $CODE_SERVER_BIN persisted
    # above, not a hardcoded literal -- sudo only matches a rule against
    # the exact command path invoked, so a mismatch here would turn a
    # silent no-op into a loud "permission denied" instead of actually
    # fixing the toggle.
    echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: $CODE_SERVER_BIN *"
    if [ "$WITH_GIT_HOSTING" -eq 1 ]; then
        # Poll-triggered sync-on-push (backlog item 2c, part 1 —
        # docs/spec.md "The sync decision") — grouped with the other
        # ALL=($RUN_USER) rules above, NOT the ALL=(root) ones below: this
        # script never needs root (the working copy and its ownership
        # already exist; see scripts/gitea-sync-project.sh's own header).
        echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh *"
    fi
    # Unconditional (not gated behind --with-git-hosting) — the folder-upload
    # wizard is explicitly the project-registration path for people WITHOUT
    # git hosting installed.
    echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh *"
    # Also unconditional (backlog item 16, docs/spec.md) — cloning an
    # arbitrary external repo URL never depends on --with-git-hosting
    # either, same reasoning as the upload wizard's own rule above.
    echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-url.sh *"
    if [ "$WITH_GIT_HOSTING" -eq 1 ]; then
        # Gitea's own toggle wrapper triplet (docs/spec.md "Crossing the
        # privilege boundary") — zero-argument narrowing, same as Taiga's
        # rules below.
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-up.sh"
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-down.sh"
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-status.sh"
        # create_project()'s privileged clone hand-off (backlog item 2b,
        # replaces the old new-project.sh rule) — narrowed to exactly this
        # one script, arguments passed through (owner/repo/name, all
        # re-validated by the script itself; see docs/spec.md "The
        # privileged registration script").
        echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh *"
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
    # ── Self-hosted Gitea (part of --with-git-hosting). Backlog item 2b
    # retired the old git-shell/bare-repo scripts (git-hosting-setup.sh,
    # new-repo.sh, new-dev-instance.sh, new-project.sh, project-sync.sh,
    # target-setup.sh) and config/git-hosting.env.example entirely — repo
    # creation/registration now goes through Gitea's own REST API (see
    # docs/spec.md backlog item 2b, docs/GIT_HOSTING.md). Only
    # new-project-from-gitea.sh (the privileged clone hand-off) is installed
    # system-wide below; gitea-configure-api.sh is a one-time operator tool
    # run from the repo checkout, same as taiga-configure-push.sh.
    echo "-- Self-hosted Gitea (part of --with-git-hosting) --"

    # 1. Docker itself — shared with --with-taiga via ensure_docker() (see
    # that helper's own definition above); a no-op re-check if --with-taiga
    # already ran it earlier in this same install.
    ensure_docker
    GITEA_COMPOSE_OK=$DOCKER_COMPOSE_OK

    # 2. Author the compose file directly — no upstream repo to clone/pin the
    # way taiga-docker is for Taiga (docs/spec.md "Background" /
    # "Authoring the compose file directly"). Overwritten deterministically
    # on every re-run (like the sudoers file) since it's authored by this
    # project, not a user-editable checkout.
    GITEA_DIR=/opt/ai-dev-switchboard-gitea
    GITEA_PORT=3000
    GITEA_SSH_PORT=2222
    GITEA_URL_PATH="/gitea"  # mirrors app.py's GITEA_URL_PATH constant
    install -D -m 644 "$REPO_DIR/config/gitea-docker-compose.yml" "$GITEA_DIR/docker-compose.yml"
    mkdir -p "$GITEA_DIR/gitea" "$GITEA_DIR/postgres"

    # 3. Config / secrets — $GITEA_DIR/.env, via set_env (generic, reusable
    # across compose stacks, same as Taiga's usage). Secrets generated once
    # via random_token, checked via get_env returning empty (same idiom
    # TOTP_SECRET already uses) — never regenerated on re-run. ROOT_URL/
    # DOMAIN aren't secrets, so they're re-derived every run to track
    # whatever PUBLISH_MODE/BASE_URL currently resolve to (same as Taiga's
    # TAIGA_DOMAIN).
    GITEA_ENV="$GITEA_DIR/.env"
    GITEA_DB_PASSWORD="$(get_env "$GITEA_ENV" POSTGRES_PASSWORD)"
    if [ -z "$GITEA_DB_PASSWORD" ]; then
        GITEA_DB_PASSWORD="$(random_token 24)"
        set_env "$GITEA_ENV" POSTGRES_PASSWORD "$GITEA_DB_PASSWORD"
    fi
    set_env "$GITEA_ENV" POSTGRES_USER "gitea"
    set_env "$GITEA_ENV" POSTGRES_DB "gitea"
    set_env "$GITEA_ENV" GITEA__database__DB_TYPE "postgres"
    set_env "$GITEA_ENV" GITEA__database__HOST "db:5432"
    set_env "$GITEA_ENV" GITEA__database__NAME "gitea"
    set_env "$GITEA_ENV" GITEA__database__USER "gitea"
    set_env "$GITEA_ENV" GITEA__database__PASSWD "$GITEA_DB_PASSWORD"

    GITEA_SECRET_KEY="$(get_env "$GITEA_ENV" GITEA__security__SECRET_KEY)"
    [ -n "$GITEA_SECRET_KEY" ] || set_env "$GITEA_ENV" GITEA__security__SECRET_KEY "$(random_token 32)"
    GITEA_INTERNAL_TOKEN="$(get_env "$GITEA_ENV" GITEA__security__INTERNAL_TOKEN)"
    [ -n "$GITEA_INTERNAL_TOKEN" ] || set_env "$GITEA_ENV" GITEA__security__INTERNAL_TOKEN "$(random_token 64)"
    # Always set (not conditional) — closes the "first visitor claims the
    # public install wizard" race (docs/spec.md "Background"): DB/config is
    # already fully supplied above via env vars, so leaving Gitea installed
    # but off is safe even before an admin account exists.
    set_env "$GITEA_ENV" GITEA__security__INSTALL_LOCK "true"

    # Captured *before* being overwritten below (backlog item 48) — Gitea's
    # official image only applies GITEA__server__ROOT_URL/DOMAIN to the
    # persisted app.ini on a container's first-ever start, so a value change
    # here needs to force-recreate the already-running `server` container
    # once the new values are written (see step 3b below), or the container
    # keeps serving stale links/form-actions indefinitely.
    GITEA_DOMAIN_PREV="$(get_env "$GITEA_ENV" GITEA__server__DOMAIN)"
    GITEA_ROOT_URL_PREV="$(get_env "$GITEA_ENV" GITEA__server__ROOT_URL)"

    if [ "$PUBLISH_MODE" = "tailscale" ] && [ -n "$BASE_URL" ]; then
        GITEA_DOMAIN_VALUE="${BASE_URL#https://}"
        GITEA_DOMAIN_VALUE="${GITEA_DOMAIN_VALUE#http://}"
        GITEA_ROOT_URL_VALUE="${BASE_URL}${GITEA_URL_PATH}"
    else
        GITEA_DOMAIN_VALUE="127.0.0.1:$GITEA_PORT"
        GITEA_ROOT_URL_VALUE="http://127.0.0.1:$GITEA_PORT"
    fi
    set_env "$GITEA_ENV" GITEA__server__DOMAIN "$GITEA_DOMAIN_VALUE"
    set_env "$GITEA_ENV" GITEA__server__ROOT_URL "$GITEA_ROOT_URL_VALUE"

    # 3b. Force-recreate `server` (the service name in
    # config/gitea-docker-compose.yml — NOT `gitea`) if ROOT_URL/DOMAIN
    # actually changed and the container already exists (backlog item 48).
    # A plain `docker compose up -d` (what scripts/gitea-up.sh runs on every
    # toggle-on) is a no-op against an already-created container — it does
    # not re-apply changed env vars into the persisted app.ini. Skipped
    # cleanly on a fresh install: nothing to recreate yet, and the first-ever
    # start will already pick up the correct values. Warn-and-continue (not
    # fatal) on failure, same idiom as the pre-pull step below.
    #
    # `--no-deps` (docs/test-review.md Defect 2, reviewer-found post-approval
    # regression): `server` and `db` both load the same `env_file: - .env`
    # (config/gitea-docker-compose.yml), so a changed `.env` also changes
    # Compose's own computed config hash for `db`, not just `server` — and
    # because `server` has `depends_on: - db`, naming only `server` on the
    # command line still pulls `db` into scope (to satisfy the dependency),
    # so Compose recreated `db` too even though `--force-recreate` was only
    # given for `server` (reproduced live: `db`'s container `Created`
    # timestamp changed on a real, isolated Gitea+Postgres compose stack).
    # `--no-deps` keeps `db` out of scope entirely, so only `server` itself
    # gets processed — confirmed live on the same isolated stack: `server`
    # recreates (new `Created` timestamp, new `ROOT_URL`/`DOMAIN` correctly
    # in `app.ini`) while `db`'s `Created` timestamp is provably unchanged.
    if [ "$GITEA_DOMAIN_VALUE" != "$GITEA_DOMAIN_PREV" ] || [ "$GITEA_ROOT_URL_VALUE" != "$GITEA_ROOT_URL_PREV" ]; then
        if [ -n "$(cd "$GITEA_DIR" && docker compose ps -q server 2>/dev/null)" ]; then
            echo "Gitea's ROOT_URL/DOMAIN changed — force-recreating the server container so it picks up the new values..."
            if ! ( cd "$GITEA_DIR" && docker compose up -d --force-recreate --no-deps server ); then
                echo "WARNING: force-recreating Gitea's server container failed — it may keep serving stale ROOT_URL/DOMAIN values until it's restarted manually (e.g. 'docker compose up -d --force-recreate --no-deps server' in $GITEA_DIR)." >&2
            fi
        fi
    fi
    # GITEA_PORT/GITEA_SSH_PORT also live in this same .env (not just
    # switchboard.env below) — Compose auto-loads .env from the project
    # directory for variable substitution, which is what lets
    # gitea-docker-compose.yml reference ${GITEA_PORT}/${GITEA_SSH_PORT}
    # without the wrapper scripts needing to export anything themselves
    # (same idiom as Taiga's own TAIGA_PORT in taiga-docker's .env).
    set_env "$GITEA_ENV" GITEA_PORT "$GITEA_PORT"
    set_env "$GITEA_ENV" GITEA_SSH_PORT "$GITEA_SSH_PORT"

    # 4. Pre-pull images at install time, not first toggle — otherwise the
    # first UI toggle-on blocks on pulling images over the network.
    # Warn-and-continue (not fatal) if there's no network right now; the
    # first toggle-on will simply be slow instead (identical reasoning and
    # idiom to Taiga's own pre-pull step).
    if [ "$GITEA_COMPOSE_OK" -eq 1 ]; then
        echo "Pre-pulling Gitea's images..."
        if ! ( cd "$GITEA_DIR" && docker compose pull ); then
            echo "WARNING: pre-pulling Gitea's images failed (no network at install time?) — Gitea stays installed, just with uncached images; the first toggle-on will pull them then instead. Continuing." >&2
        fi
    fi

    # 5. Wrapper scripts (root-run, zero arguments — see docs/spec.md
    # "Crossing the privilege boundary"). Sudoers entries for these are added
    # above, alongside every other sudoers rule this installer generates.
    install -m 755 "$REPO_DIR/scripts/gitea-up.sh" /usr/local/bin/ai-dev-switchboard-gitea-up.sh
    install -m 755 "$REPO_DIR/scripts/gitea-down.sh" /usr/local/bin/ai-dev-switchboard-gitea-down.sh
    install -m 755 "$REPO_DIR/scripts/gitea-status.sh" /usr/local/bin/ai-dev-switchboard-gitea-status.sh

    # 5b. Repo creation/registration (backlog item 2b) — the privileged
    # clone hand-off create_project() calls once Gitea's own API has already
    # created the repo (docs/spec.md "The privileged registration script").
    # Installed system-wide, unlike gitea-configure-api.sh (a one-time
    # operator tool run from the repo checkout, same as
    # taiga-configure-push.sh) — its sudoers entry is added above alongside
    # the gitea-{up,down,status}.sh ones.
    install -m 755 "$REPO_DIR/scripts/new-project-from-gitea.sh" \
        /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh

    # 5c. Poll-triggered sync-on-push (backlog item 2c, part 1 —
    # docs/spec.md). Run via `sudo -u $RUN_USER` (its sudoers entry is added
    # above, alongside the other ALL=($RUN_USER) rules — NOT grouped with
    # the ALL=(root) Gitea rules above, since this script never needs root).
    install -m 755 "$REPO_DIR/scripts/gitea-sync-project.sh" \
        /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh

    # 6. switchboard.env — GITEA_DIR is also recorded here (beyond what
    # app.py itself reads) because the wrapper scripts above source this
    # same file for it, exactly like TAIGA_DIR's usage above.
    # GITEA_API_TOKEN is deliberately NOT set here — it doesn't exist until
    # the operator runs scripts/gitea-configure-api.sh, a separate one-time
    # step after this install finishes (see the summary block below).
    set_env "$ENV_FILE" GITEA_ENABLED 1
    set_env "$ENV_FILE" GITEA_PORT "$GITEA_PORT"
    set_env "$ENV_FILE" GITEA_LABEL "Gitea"
    set_env "$ENV_FILE" GITEA_DIR "$GITEA_DIR"
    set_env "$ENV_FILE" GITEA_UP_SCRIPT "/usr/local/bin/ai-dev-switchboard-gitea-up.sh"
    set_env "$ENV_FILE" GITEA_DOWN_SCRIPT "/usr/local/bin/ai-dev-switchboard-gitea-down.sh"
    set_env "$ENV_FILE" GITEA_STATUS_SCRIPT "/usr/local/bin/ai-dev-switchboard-gitea-status.sh"
    set_env "$ENV_FILE" NEW_PROJECT_FROM_GITEA_SCRIPT \
        "/usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh"
    # GITEA_SYNC_SCRIPT/GITEA_REPO_MAP_FILE (docs/spec.md part 2c part 1) —
    # same $STATE_DIR DESC_CACHE_FILE already uses.
    # GITEA_POLL_INTERVAL_SECONDS is deliberately NOT written here — it has
    # a built-in default (45) in app.py itself and is documented as an
    # optional override in config/switchboard.env.example instead (docs/
    # spec.md "Open questions" #5), same style as GITEA_PORT/GITEA_LABEL.
    set_env "$ENV_FILE" GITEA_SYNC_SCRIPT \
        "/usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh"
    set_env "$ENV_FILE" GITEA_REPO_MAP_FILE "$STATE_DIR/gitea-repo-map.json"
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

# ── Optional: link an existing remote Ollama for multi-agent team leads
# (--with-ollama). This LINKS a remote endpoint — it never installs Ollama,
# a model, a container, or a systemd unit on this machine. The standard
# switchboard container has ~715MB free RAM with swap already exhausted
# (docs/story.md §2.5); no tool-capable model fits here, so the endpoint
# must already be running somewhere else. TEAM_LLM_BASE_URL/TEAM_LLM_MODEL
# are already documented in config/switchboard.env.example (added in 6c) —
# this block writes them, it does not introduce them.
if [ "$WITH_OLLAMA" -eq 1 ]; then
    echo "-- Link an existing Ollama (multi-agent team leads) --"
    echo "This LINKS a remote Ollama endpoint — nothing is installed on this"
    echo "machine. Point it at an Ollama instance already running elsewhere"
    echo "(e.g. another host on your tailnet)."

    OLLAMA_BASE_URL_DEFAULT="$(get_env "$ENV_FILE" TEAM_LLM_BASE_URL)"
    OLLAMA_MODEL_DEFAULT="$(get_env "$ENV_FILE" TEAM_LLM_MODEL)"
    OLLAMA_BASE_URL_INPUT=$(prompt "Ollama endpoint URL (OpenAI-compatible, e.g. an existing remote Ollama's /v1)" "${OLLAMA_BASE_URL_DEFAULT:-http://127.0.0.1:11434/v1}")
    OLLAMA_MODEL_INPUT=$(prompt "Model name" "${OLLAMA_MODEL_DEFAULT:-qwen3:8b}")

    # Normalise a trailing slash so we validate — and write — the exact URL
    # TEAM_LLM_BASE_URL will be, never "//models" (docs/spec.md
    # "Validation"). Deliberately does NOT append /v1 if the operator left
    # it off — silently rewriting their URL is how the wrong endpoint gets
    # validated instead of the one that was actually typed.
    OLLAMA_BASE_URL_NORM="${OLLAMA_BASE_URL_INPUT%/}"

    # Validate against the exact OpenAI-compatible path the lead adapter
    # will really call (app/teams.py's _tier1_call() hits
    # "$TEAM_LLM_BASE_URL/chat/completions") — NOT Ollama's native
    # /api/tags, which lives outside /v1 and would validate a different URL
    # than the one being written. Bounded so an unreachable/stalled host can
    # never hang the installer.
    OLLAMA_MODELS_JSON=$(curl -fsS --max-time 10 "$OLLAMA_BASE_URL_NORM/models" 2>/dev/null || true)

    if [ -z "$OLLAMA_MODELS_JSON" ]; then
        echo "Could not reach $OLLAMA_BASE_URL_NORM/models (unreachable, no response, or an HTTP error) — writing nothing. The team lead will fall back to an engines.d tier-2 engine until this is fixed. Re-run --with-ollama once the endpoint is reachable." >&2
    else
        # Parsed with python3 (unconditionally installed above), never
        # grep — a substring match would false-positive "qwen3:8" against
        # an endpoint advertising only "qwen3:8b". Exact id comparison
        # only. A 200 response that isn't valid JSON (an HTML proxy login
        # page, a captive portal) is treated as a parse failure, not as a
        # valid empty model list.
        #
        # The script text is built into a variable via a heredoc, THEN run
        # as `python3 -c "$VAR"` with the JSON piped separately — `python3
        # - <<PYEOF` (script AND data both on stdin) doesn't work here, the
        # heredoc always wins that fd and the piped JSON would never reach
        # sys.stdin.
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
        OLLAMA_MODEL_CHECK=$(printf '%s' "$OLLAMA_MODELS_JSON" | \
            python3 -c "$OLLAMA_MODEL_CHECK_SCRIPT" "$OLLAMA_MODEL_INPUT")
        case "$OLLAMA_MODEL_CHECK" in
            OK)
                set_env "$ENV_FILE" TEAM_LLM_BASE_URL "$OLLAMA_BASE_URL_NORM"
                set_env "$ENV_FILE" TEAM_LLM_MODEL "$OLLAMA_MODEL_INPUT"
                echo "Linked remote Ollama: $OLLAMA_BASE_URL_NORM, model $OLLAMA_MODEL_INPUT."
                echo "Nothing was installed locally — this endpoint runs elsewhere."
                ;;
            MODEL_ABSENT:*)
                OLLAMA_AVAILABLE="${OLLAMA_MODEL_CHECK#MODEL_ABSENT:}"
                if [ -z "$OLLAMA_AVAILABLE" ]; then
                    echo "Reached $OLLAMA_BASE_URL_NORM but it has no models available — writing nothing." >&2
                else
                    echo "Reached $OLLAMA_BASE_URL_NORM but model '$OLLAMA_MODEL_INPUT' is not available there — writing nothing. Available models: $OLLAMA_AVAILABLE" >&2
                fi
                ;;
            *)
                echo "Reached $OLLAMA_BASE_URL_NORM/models but its response could not be parsed as JSON (a proxy login page or captive portal can look like this) — writing nothing." >&2
                ;;
        esac
    fi
fi

# ── Optional: deploy-target receiver (--with-deploy-target). Fully
# independent of --with-host-control above (different user, different
# config file, different sudoers file, no shared state) — safe to use
# either or both on the same box. Run on the SEPARATE machine that will
# receive deploys, never on the switchboard box itself (docs/spec.md
# part 2c-2a — see deploy-target/README.md).
if [ "$WITH_DEPLOY_TARGET" -eq 1 ]; then
    echo "-- Deploy-target receiver (this machine) --"
    echo "Run this on the SEPARATE machine that will receive deploys — never"
    echo "on the switchboard box itself. See deploy-target/README.md."

    DEPLOY_PATH_INPUT=$(prompt "Destination path this target will receive deploys into" "")
    DEPLOY_SERVICE_NAME_INPUT=$(prompt "Systemd service name to restart after each deploy" "")
    DEPLOY_PUBKEY=$(prompt "Public key to authorize for this target (paste the contents of e.g. deploy_ed25519.pub — leave blank to add by hand later)" "")

    if [ ! -x /usr/bin/rrsync ]; then
        # Skip only this block, not the whole install.sh run over one
        # optional flag — same "continue"-equivalent precedent the
        # ttyd-arch-not-found check above already uses.
        echo "ERROR: /usr/bin/rrsync not found. 'apt-get install rsync' should already have provided it on Debian 12+ (rsync itself is already installed unconditionally above) — if this target is on an older or non-Debian OS, install rrsync yourself before re-running --with-deploy-target. Skipping deploy-target provisioning." >&2
    else
        id deploy &>/dev/null || useradd -m -d /home/deploy -s /bin/sh deploy

        # Additive, never wipes pre-existing content — same idiom as
        # PROJECTS_DIR's own mkdir -p + chown earlier in this script.
        if [ -n "$DEPLOY_PATH_INPUT" ]; then
            mkdir -p "$DEPLOY_PATH_INPUT"
            chown deploy:deploy "$DEPLOY_PATH_INPUT"
        fi

        mkdir -p /home/deploy/.ssh
        chmod 700 /home/deploy/.ssh

        DEPLOY_ENV="$CONFIG_DIR/deploy-target.env"
        [ -f "$DEPLOY_ENV" ] || cp "$REPO_DIR/deploy-target/deploy-target.env.example" "$DEPLOY_ENV"
        # Only overwrite the keys actually supplied this run — same
        # idempotent-upsert idiom as every other set_env call in this file,
        # so a blank re-run answer leaves the previous value untouched
        # rather than clearing it (docs/spec.md "Edge cases").
        [ -n "$DEPLOY_PATH_INPUT" ] && set_env "$DEPLOY_ENV" DEPLOY_PATH "$DEPLOY_PATH_INPUT"
        [ -n "$DEPLOY_SERVICE_NAME_INPUT" ] && set_env "$DEPLOY_ENV" DEPLOY_SERVICE_NAME "$DEPLOY_SERVICE_NAME_INPUT"

        install -m 755 "$REPO_DIR/deploy-target/deploy-wrapper.sh" /usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh
        install -m 755 "$REPO_DIR/deploy-target/deploy-restart.sh" /usr/local/bin/ai-dev-switchboard-deploy-restart.sh

        if [ -n "$DEPLOY_PUBKEY" ]; then
            # Deterministically overwritten (like the sudoers file below) —
            # this cycle supports exactly one authorized key per target.
            printf 'command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty %s\n' \
                "$DEPLOY_PUBKEY" > /home/deploy/.ssh/authorized_keys
            chown -R deploy:deploy /home/deploy/.ssh
            chmod 600 /home/deploy/.ssh/authorized_keys
        else
            echo "No public key supplied — deploy-target is provisioned but not yet"
            echo "reachable over SSH. Add one by hand later:"
            echo "  echo 'command=\"/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <pubkey>' | sudo tee /home/deploy/.ssh/authorized_keys"
            echo "  sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys && sudo chmod 600 /home/deploy/.ssh/authorized_keys"
        fi

        DEPLOY_SUDOERS=/etc/sudoers.d/ai-dev-switchboard-deploy-target
        # Zero arguments — same "narrower than every other rule" idiom this
        # script already uses for gitea-{up,down,status}.sh and
        # taiga-{up,down,status}.sh: root-restart-equivalent access here, no
        # passthrough arguments accepted.
        echo "deploy ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-deploy-restart.sh" > "$DEPLOY_SUDOERS"
        chmod 440 "$DEPLOY_SUDOERS"
        visudo -cf "$DEPLOY_SUDOERS" || { echo "Generated sudoers file failed validation — see $DEPLOY_SUDOERS" >&2; exit 1; }

        echo ""
        echo "Deploy-target receiver provisioned on this machine (target-side only —"
        echo "no switchboard/app.py wiring exists yet to drive it; see"
        echo "deploy-target/README.md). The PRIVATE half of the SSH keypair belongs"
        echo "on whatever machine will eventually push here (a future switchboard-side"
        echo "caller, or you, for manual verification), never on this target machine."
        echo "Manual test sequence, once a key is authorized (see"
        echo "deploy-target/README.md for why the destination is left bare):"
        echo "  rsync -e \"ssh -i <key>\" -a <source>/ deploy@<this-host>:"
        echo "  ssh -i <key> deploy@<this-host> deploy-restart"
    fi
fi

# Guarded restart -- refuses to restart (not just warns) whenever
# RUN_USER has ANY live tmux session, no --force override (item 13's own
# no-`--force` precedent). Runs unconditionally, not just for --update:
# `systemctl enable --now` above starts the service before any --with-*
# block below it has finished writing its own config to switchboard.env
# (item 34) -- EnvironmentFile= is read once at process start, so without
# this final restart, a fresh install's own optional-feature flags never
# actually take effect in the running process, only on disk.
LIVE_SESSIONS="$(sudo -u "$RUN_USER" tmux list-sessions -F '#{session_name}' 2>/dev/null || true)"
if [ -n "$LIVE_SESSIONS" ]; then
    echo "WARNING: $RUN_USER has live tmux session(s):" >&2
    echo "$LIVE_SESSIONS" | sed 's/^/  - /' >&2
    echo "ai-dev-switchboard was NOT restarted -- restarting now would very likely interrupt these (see docs/ARCHITECTURE.md). Stop them (or wait for them to finish), then run: sudo systemctl restart ai-dev-switchboard -- to pick up every config value written during this install/update run." >&2
else
    echo "-- Restarting ai-dev-switchboard to pick up this run's full configuration --"
    systemctl restart ai-dev-switchboard
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
if [ "$WITH_GIT_HOSTING" -eq 1 ]; then
    echo ""
    echo "Gitea: installed but left OFF — flip the 'Gitea' row's toggle in the"
    echo "web UI to start it. A 2-container stack (Gitea + Postgres), well under"
    echo "1 GB of RAM once turned on; toggling it back off frees that right away."
    echo "The web UI's '+ New project' button creates real Gitea repos once you've"
    echo "done TWO one-time steps, in order, after the Gitea toggle is on and the"
    echo "stack has finished starting:"
    echo "  1. Create Gitea's own admin account (a single non-interactive command):"
    echo "       docker exec --user git ai-dev-switchboard-gitea gitea admin user create \\"
    echo "         --admin --username <name> --password <password> --email <email> \\"
    echo "         --must-change-password=false"
    echo "  2. Run scripts/gitea-configure-api.sh once (as root) to mint an API token"
    echo "     for the web UI to use — see docs/GIT_HOSTING.md."
fi
echo ""
echo "Next: log in as $RUN_USER and run your engine's CLI once interactively"
echo "(e.g. \`claude\`) to finish ITS login, before starting sessions from the UI."
