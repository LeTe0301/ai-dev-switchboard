#!/usr/bin/env python3
"""
ai-dev-switchboard: a dependency-free toggle app for AI coding-agent
sessions, one per project directory, each its own tmux session.

Engines (Claude Code, aider, Codex, or anything else that runs as a CLI in a
terminal) are config, not code — see ENGINES_DIR (config/switchboard.env)
and the *.engine files under engines.d/. An engine that prints its own
hosted "remote control" link (like Claude Code's claude.ai/code/session_...
URL) gets that link surfaced directly in the UI; any engine that doesn't
gets a built-in ttyd web terminal automatically, no extra config. Adding a
new engine is a config file, not a code change.

Optionally, one more row can control a persistent session on a separate
machine (e.g. a Proxmox host outside any container) over a narrowly-scoped
SSH channel — see HOST_CONTROL_ENABLED and host-agent/.

Login is either real Proxmox VE credentials (checked live against a PVE
host's own API) or a single configured username/password, your choice
(AUTH_MODE). Either way, a session cookie is required for every request, and
a TOTP code (RFC 6238 — any authenticator app) is asked for separately, once
per browser session, the moment a switch is actually flipped.

This process is meant to run as its own unprivileged service user (see
systemd/ai-dev-switchboard.service + scripts/install.sh) — a bug in this app
is not an instant path to RUN_USER's account. It only runs tmux/ttyd/
code-server as RUN_USER via a narrowly-scoped sudoers rule; the spawned
coding sessions themselves run as RUN_USER and keep whatever access that
account has for real agentic work.

No framework, stdlib only. Meant to sit behind a reverse proxy / tailscale
serve / SSH tunnel of your own — never expose LISTEN_HOST/LISTEN_PORT
directly.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shlex
import shutil
import ssl
import stat
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

# ─── config (see config/switchboard.env.example for the full reference) ──
TOTP_SECRET = os.environ["TOTP_SECRET"]

AUTH_MODE = os.environ.get("AUTH_MODE", "simple")
PVE_HOST = os.environ.get("PVE_HOST", "")
PVE_PORT = int(os.environ.get("PVE_PORT", "8006"))
PVE_REALM = os.environ.get("PVE_REALM", "pam")
SIMPLE_USERNAME = os.environ.get("SIMPLE_USERNAME", "")
SIMPLE_PASSWORD = os.environ.get("SIMPLE_PASSWORD", "")

RUN_USER = os.environ.get("RUN_USER", "dev")
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", f"/home/{RUN_USER}/projects")
ENGINES_DIR = os.environ.get("ENGINES_DIR", "/etc/ai-dev-switchboard/engines.d")

# Folder-upload wizard (see docs/spec.md "Folder upload → auto-detect
# repo(s)") — phase 1 (POST /projects/upload) stages an uploaded zip and
# detects structure only, entirely unprivileged, under UPLOAD_STAGING_DIR
# (owned by this process's own unprivileged user, no sudo involved). Phase 2
# (POST /projects/upload/confirm) re-walks that staged tree, derives/
# collision-checks names, and only then crosses the privilege boundary via
# NEW_PROJECT_FROM_UPLOAD_SCRIPT to actually register under PROJECTS_DIR.
UPLOAD_STAGING_DIR = os.environ.get(
    "UPLOAD_STAGING_DIR", "/var/lib/ai-dev-switchboard/uploads")
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", "104857600"))  # 100 MiB
# Cheap guard against a many-tiny-files DoS shape (see docs/spec.md "Size
# limits") — env-overridable constant, same style as UPLOAD_STAGING_TTL_SECONDS.
UPLOAD_MAX_ENTRIES = int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))
# How long an unconfirmed staged upload is kept before _reap_dead_state()
# sweeps it as abandoned (docs/spec.md "Two-phase protocol") — a confirmed
# upload's staging directory is removed immediately regardless of this.
UPLOAD_STAGING_TTL_SECONDS = int(os.environ.get("UPLOAD_STAGING_TTL_SECONDS", "1800"))
# The privileged hand-off script (docs/spec.md "Crossing the privilege
# boundary") that actually moves a validated, already-named staged directory
# into PROJECTS_DIR/<name> as RUN_USER. Installed unconditionally by
# install.sh, unlike NEW_PROJECT_FROM_GITEA_SCRIPT below (--with-git-hosting
# only) — this feature is explicitly the path for people WITHOUT git hosting
# installed.
NEW_PROJECT_FROM_UPLOAD_SCRIPT = os.environ.get(
    "NEW_PROJECT_FROM_UPLOAD_SCRIPT",
    "/usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh")

PUBLISH_MODE = os.environ.get("PUBLISH_MODE", "none")  # "tailscale" | "none"
BASE_URL = os.environ.get("BASE_URL", "")

LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8333"))

HOST_CONTROL_ENABLED = os.environ.get("HOST_CONTROL_ENABLED", "0") == "1"
HOST_CONTROL_KEY = os.environ.get("HOST_CONTROL_KEY", "")
HOST_CONTROL_USER = os.environ.get("HOST_CONTROL_USER", "switchboard")
HOST_IP = os.environ.get("HOST_IP", "")
HOST_LABEL = os.environ.get("HOST_LABEL", "Remote host")

DESC_LLM_BASE_URL = os.environ.get("DESC_LLM_BASE_URL") or None
DESC_LLM_MODEL = os.environ.get("DESC_LLM_MODEL", "")
DESC_CACHE_FILE = os.environ.get("DESC_CACHE_FILE", "/var/lib/ai-dev-switchboard/descriptions.json")

TTYD_BIN = os.environ.get("TTYD_BIN", "/usr/local/bin/ttyd")
CODE_SERVER_BIN = os.environ.get("CODE_SERVER_BIN", "/usr/local/bin/code-server")

# Self-hosted Taiga (backlog item 1a) — a singleton on/off toggle row like
# host-control above, not a per-project row like ttyd/code-server. Off
# unless install.sh --with-taiga was used AND the toggle is flipped on.
TAIGA_ENABLED = os.environ.get("TAIGA_ENABLED", "0") == "1"
TAIGA_LABEL = os.environ.get("TAIGA_LABEL", "Taiga")
TAIGA_PORT = int(os.environ.get("TAIGA_PORT", "9000"))
TAIGA_UP_SCRIPT = os.environ.get("TAIGA_UP_SCRIPT", "/usr/local/bin/ai-dev-switchboard-taiga-up.sh")
TAIGA_DOWN_SCRIPT = os.environ.get("TAIGA_DOWN_SCRIPT", "/usr/local/bin/ai-dev-switchboard-taiga-down.sh")
TAIGA_STATUS_SCRIPT = os.environ.get("TAIGA_STATUS_SCRIPT", "/usr/local/bin/ai-dev-switchboard-taiga-status.sh")

# Self-hosted Gitea (backlog item 2a) — same singleton on/off toggle shape as
# Taiga above, folded into --with-git-hosting rather than its own flag (see
# docs/spec.md "Sequencing — additive, not a swap"). GITEA_SSH_PORT is read
# by the wrapper scripts, not here — the web UI never touches the SSH port
# (no way to create a Gitea repo yet in this cycle's scope).
GITEA_ENABLED = os.environ.get("GITEA_ENABLED", "0") == "1"
GITEA_LABEL = os.environ.get("GITEA_LABEL", "Gitea")
GITEA_PORT = int(os.environ.get("GITEA_PORT", "3000"))
GITEA_UP_SCRIPT = os.environ.get("GITEA_UP_SCRIPT", "/usr/local/bin/ai-dev-switchboard-gitea-up.sh")
GITEA_DOWN_SCRIPT = os.environ.get("GITEA_DOWN_SCRIPT", "/usr/local/bin/ai-dev-switchboard-gitea-down.sh")
GITEA_STATUS_SCRIPT = os.environ.get("GITEA_STATUS_SCRIPT", "/usr/local/bin/ai-dev-switchboard-gitea-status.sh")
# Repo creation/registration (backlog item 2b) -- GITEA_API_TOKEN is minted
# once by scripts/gitea-configure-api.sh (a separate one-time bootstrap step
# from the admin-account creation above) and read here exactly like every
# other SVC_USER-consumed secret in this file (TOTP_SECRET, HOST_CONTROL_KEY)
# -- see docs/spec.md "Token reuse and where it lives" for why this lives in
# switchboard.env rather than a RUN_USER-owned config file the way
# taiga_push_spec.py's own credential does.
GITEA_API_TOKEN = os.environ.get("GITEA_API_TOKEN", "")
NEW_PROJECT_FROM_GITEA_SCRIPT = os.environ.get(
    "NEW_PROJECT_FROM_GITEA_SCRIPT",
    "/usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh")

# Poll-based sync-on-push (backlog item 2c, part 1 -- docs/spec.md) -- keeps
# PROJECTS_DIR/<name> in sync when a push lands on its Gitea repo from
# somewhere else (another contributor via Gitea's own web UI, a merged PR, a
# second agent session elsewhere). GITEA_SYNC_SCRIPT is run via
# `sudo -u RUN_USER` (never root -- see scripts/gitea-sync-project.sh's own
# header). GITEA_REPO_MAP_FILE is the owner/repo -> local name/branch/
# sync-state mapping create_project() writes and the poll loop reads/updates
# -- SVC_USER-owned, same directory DESC_CACHE_FILE already lives in (see
# docs/spec.md "Repo-map + sync-state file" for why this can't just be an
# ambient read of each project's own .git/config).
GITEA_SYNC_SCRIPT = os.environ.get(
    "GITEA_SYNC_SCRIPT", "/usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh")
GITEA_REPO_MAP_FILE = os.environ.get(
    "GITEA_REPO_MAP_FILE", "/var/lib/ai-dev-switchboard/gitea-repo-map.json")
# Independent of the frontend's fast 4-second /status poll -- polling
# Gitea's API is a real network call per registered project, not the cheap
# in-memory bookkeeping _reap_dead_state() itself redoes on every tick. Not
# written by install.sh (docs/spec.md "Open questions" #5) -- an
# env-overridable constant, same style as UPLOAD_STAGING_TTL_SECONDS.
GITEA_POLL_INTERVAL_SECONDS = int(os.environ.get("GITEA_POLL_INTERVAL_SECONDS", "45"))

# Switchboard-side deploy dispatch (backlog item 2c, part 2b -- docs/spec.md).
# DEPLOY_MAP_FILE is a hand-edited, operator-maintained project -> deploy-
# target mapping (host/port/user/deploy_path/service/key) -- unlike
# GITEA_REPO_MAP_FILE above, app.py only ever *reads* this file, never writes
# it (docs/spec.md "No UI for authoring deploy-map.json or placing keys").
# DEPLOY_KEYS_DIR is the mode-700, SVC_USER-owned directory every map
# entry's "key" path must resolve under (defense in depth against a
# hand-edited map pointing somewhere unintended) -- see _load_deploy_map.
DEPLOY_MAP_FILE = os.environ.get(
    "DEPLOY_MAP_FILE", "/etc/ai-dev-switchboard/deploy-map.json")
DEPLOY_KEYS_DIR = os.environ.get(
    "DEPLOY_KEYS_DIR", "/etc/ai-dev-switchboard/deploy-keys")

STARTUP_TIMEOUT = int(os.environ.get("STARTUP_TIMEOUT_SECONDS", "45"))

# This service runs as its own unprivileged user — tmux sessions must run as
# RUN_USER instead (project files, engine credentials all live there),
# scoped via a sudoers.d rule to exactly `sudo -u RUN_USER /usr/bin/tmux *`
# (+ ttyd, code-server), nothing else. See scripts/install.sh.
TMUX = ["sudo", "-u", RUN_USER, "/usr/bin/tmux"]

SESSIONS = {}  # session id -> {"expiry": epoch, "totp_ok": bool}
SESSION_TTL = 12 * 3600


# ─── TOTP (RFC 6238) ───────────────────────────────────────────────────────
def totp_at(secret_b32: str, when: float, interval=30, digits=6) -> str:
    pad = secret_b32 + "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(pad.upper())
    counter = int(when // interval)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_verify(secret_b32: str, code: str, window=1) -> bool:
    code = (code or "").strip()
    if not code:
        return False
    now = time.time()
    return any(hmac.compare_digest(totp_at(secret_b32, now + d * 30), code)
              for d in range(-window, window + 1))


# ─── auth ──────────────────────────────────────────────────────────────────
def pve_login(username: str, password: str) -> bool:
    username = (username or "").strip()
    if not username or not password or not PVE_HOST:
        return False
    if "@" not in username:
        username = f"{username}@{PVE_REALM}"
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"https://{PVE_HOST}:{PVE_PORT}/api2/json/access/ticket", data=data)
    # PVE's web UI cert is self-signed by default; only ever point this at a
    # known host on your own network, same trust level as the host-control
    # SSH channel below.
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            payload = json.loads(resp.read())
        return bool(payload.get("data", {}).get("ticket"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
        return False


def simple_login(username: str, password: str) -> bool:
    if not SIMPLE_USERNAME or not SIMPLE_PASSWORD:
        return False
    return (hmac.compare_digest((username or "").strip(), SIMPLE_USERNAME) and
            hmac.compare_digest(password or "", SIMPLE_PASSWORD))


def do_login(username: str, password: str) -> bool:
    return pve_login(username, password) if AUTH_MODE == "pve" else simple_login(username, password)


def new_session() -> str:
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = {"expiry": time.time() + SESSION_TTL, "totp_ok": False}
    return sid


def session_valid(sid: str) -> bool:
    s = SESSIONS.get(sid)
    if s is None:
        return False
    if s["expiry"] < time.time():
        del SESSIONS[sid]
        return False
    return True


def session_totp_ok(sid: str) -> bool:
    # TOTP is verified once per session rather than on every single mutating
    # action. First action after login still needs a code (see do_POST) —
    # after that one succeeds, this flips true and the rest of the session
    # is free of the prompt. A stolen/replayed session cookie alone still
    # isn't enough to *do* anything until it clears that first check.
    s = SESSIONS.get(sid)
    return bool(s and s.get("totp_ok"))


def mark_session_totp_ok(sid: str) -> None:
    if sid in SESSIONS:
        SESSIONS[sid]["totp_ok"] = True


# ─── engines: config, not code (engines.d/*.engine) ────────────────────────
# HEADLESS_FORMAT/HEADLESS_PROMPT known values (backlog item 6a, docs/spec.md
# "Engine and _parse_engine_file() -- four new keys, additive only"). Kept
# here rather than inside _parse_engine_file() so app/teams.py can reference
# the same sets without re-declaring them.
_HEADLESS_FORMATS = {"claude-stream-json", "codex-jsonl", "plain"}
_HEADLESS_PROMPT_MODES = {"arg", "stdin", "file"}


class Engine:
    __slots__ = ("name", "label", "cmd", "url_regex", "startup",
                 "headless_cmd", "headless_format", "headless_prompt", "headless_resume")

    def __init__(self, name, label, cmd, url_regex, startup,
                 headless_cmd=None, headless_format=None, headless_prompt=None,
                 headless_resume=None):
        self.name = name
        self.label = label
        self.cmd = cmd
        self.url_regex = re.compile(url_regex) if url_regex else None
        self.startup = startup  # list of (match_str, keys_to_send)
        # Headless invocation (backlog item 6a, docs/spec.md) -- all four
        # optional, all None unless HEADLESS_CMD plus a recognized
        # HEADLESS_FORMAT/HEADLESS_PROMPT are all present (see
        # _parse_engine_file()). HEADLESS_RESUME may legitimately stay None
        # even when the other three are set (an engine with no resume
        # concept at all, e.g. aider).
        self.headless_cmd = headless_cmd
        self.headless_format = headless_format
        self.headless_prompt = headless_prompt
        self.headless_resume = headless_resume

    @property
    def headless_enabled(self) -> bool:
        return bool(self.headless_cmd and self.headless_format and self.headless_prompt)


def _parse_engine_file(path: str):
    kv = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    cmd = kv.get("CMD")
    if not cmd:
        return None
    name = os.path.splitext(os.path.basename(path))[0]
    # Reserved engine-name prefix (docs/spec.md "Session naming"): headless
    # tmux sessions are named f"switchboard-headless-{run_id}" (app/teams.py)
    # via the *same* TMUX rule instance_start() uses, and active_engine()
    # keys purely off f"{engine_name}-{project_name}" with no other
    # cross-check. Reserving only the exact name "switchboard-headless"
    # would still leave a constructible collision open (engine "switchboard"
    # + a project directory literally named "headless-<run_id>"), so the
    # *whole* "switchboard" prefix is reserved -- any .engine file whose
    # derived name starts with it is ignored, same "intentionally inert"
    # treatment .engine.example templates already get below.
    if name.startswith("switchboard"):
        return None
    startup = []
    i = 1
    while f"STARTUP_MATCH_{i}" in kv and f"STARTUP_SEND_{i}" in kv:
        startup.append((kv[f"STARTUP_MATCH_{i}"], kv[f"STARTUP_SEND_{i}"]))
        i += 1
    # HEADLESS_CMD present but HEADLESS_FORMAT/HEADLESS_PROMPT missing or
    # unrecognized -> parse the rest of the file normally, leave headless
    # fields unset (Engine.headless_enabled == False). Never an exception,
    # never a load_engines() failure -- same best-effort philosophy as the
    # `except OSError: continue` in load_engines() itself.
    headless_cmd = kv.get("HEADLESS_CMD") or None
    headless_format = kv.get("HEADLESS_FORMAT") or None
    headless_prompt = kv.get("HEADLESS_PROMPT") or None
    headless_resume = kv.get("HEADLESS_RESUME") or None
    if headless_cmd and (headless_format not in _HEADLESS_FORMATS or
                          headless_prompt not in _HEADLESS_PROMPT_MODES):
        headless_cmd = headless_format = headless_prompt = headless_resume = None
    return Engine(name, kv.get("LABEL", name), cmd, kv.get("URL_REGEX") or None, startup,
                  headless_cmd, headless_format, headless_prompt, headless_resume)


def load_engines() -> dict:
    """
    Re-read every time rather than caching: engines.d is meant to be edited
    live (add/remove/tune an engine without restarting the service), and a
    directory scan of a handful of small files is cheap next to everything
    else a session start/status poll already does.
    """
    engines = {}
    if not os.path.isdir(ENGINES_DIR):
        return engines
    for fn in sorted(os.listdir(ENGINES_DIR)):
        if not fn.endswith(".engine"):
            continue  # .engine.example templates are intentionally inert
        try:
            e = _parse_engine_file(os.path.join(ENGINES_DIR, fn))
        except OSError:
            continue
        if e:
            engines[e.name] = e
    return engines


# ─── per-project descriptions (optional — off unless DESC_LLM_BASE_URL set) ─
_desc_lock = threading.Lock()
_desc_pending = set()


def _load_desc_cache() -> dict:
    try:
        with open(DESC_CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_desc_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(DESC_CACHE_FILE), exist_ok=True)
    tmp = DESC_CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, DESC_CACHE_FILE)


_DESC = _load_desc_cache() if DESC_LLM_BASE_URL else {}


def _read_head(path: str, limit: int) -> str:
    try:
        with open(path, "r", errors="ignore") as f:
            return f.read(limit)
    except OSError:
        return ""


def _gather_project_context(workdir: str) -> str:
    parts = []
    try:
        entries = sorted(os.listdir(workdir))[:40]
        parts.append("Top-level files/folders: " + ", ".join(entries))
    except OSError:
        pass

    for readme in ("README.md", "Readme.md", "readme.md", "README"):
        text = _read_head(os.path.join(workdir, readme), 2000)
        if text:
            parts.append(f"{readme}:\n{text}")
            break

    for doc in ("CLAUDE.md", "AGENTS.md"):
        text = _read_head(os.path.join(workdir, doc), 1500)
        stripped = text.strip()
        if stripped.startswith("@") and len(stripped.splitlines()) == 1:
            text = _read_head(os.path.join(workdir, stripped[1:]), 1500)
        if text:
            parts.append(f"{doc}:\n{text}")
            break

    pkg = _read_head(os.path.join(workdir, "package.json"), 4000)
    if pkg:
        try:
            data = json.loads(pkg)
            deps = list(data.get("dependencies", {}).keys())[:15]
            parts.append(f"package.json: name={data.get('name')!r} "
                        f"description={data.get('description')!r} dependencies={deps}")
        except ValueError:
            pass

    for req in ("requirements.txt", "pyproject.toml"):
        text = _read_head(os.path.join(workdir, req), 800)
        if text:
            parts.append(f"{req}:\n{text}")
            break

    return "\n\n".join(parts)[:6000]


def _summarize_project(workdir: str) -> str:
    context = _gather_project_context(workdir)
    if not context.strip():
        return ""
    prompt = (f"{context}\n\n"
              "Based only on the above, write ONE concise sentence (under 100 characters) "
              "describing what this project/app actually does. Plain language, no marketing "
              "fluff, no 'this repo contains'. If you genuinely can't tell, say so briefly.")
    data = json.dumps({"model": DESC_LLM_MODEL, "temperature": 0.3, "stream": False,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(f"{DESC_LLM_BASE_URL}/chat/completions", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
        text = (payload["choices"][0]["message"].get("content") or "").strip()
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, IndexError):
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text[:180]


def _generate_description_bg(name: str, workdir: str):
    try:
        desc = _summarize_project(workdir)
        if desc:
            with _desc_lock:
                _DESC[name] = desc
                _save_desc_cache(_DESC)
    finally:
        _desc_pending.discard(name)


def get_description(name: str, workdir: str) -> str:
    if not DESC_LLM_BASE_URL:
        return ""
    if name in _DESC:
        return _DESC[name]
    if name not in _desc_pending:
        _desc_pending.add(name)
        threading.Thread(target=_generate_description_bg, args=(name, workdir),
                         daemon=True).start()
    return ""


def instance_names():
    if not os.path.isdir(PROJECTS_DIR):
        return []
    return sorted(d for d in os.listdir(PROJECTS_DIR)
                  if os.path.isdir(os.path.join(PROJECTS_DIR, d)) and not d.startswith("."))


# ─── publishing per-project terminals (ttyd fallback, VS Code) ────────────
def _publish(path: str, port: int) -> str:
    if PUBLISH_MODE == "tailscale":
        subprocess.run(["tailscale", "serve", "--bg", f"--set-path={path}",
                        f"http://127.0.0.1:{port}"], capture_output=True)
        return f"{BASE_URL}{path}"
    # PUBLISH_MODE=none: bind stays loopback-only (see below), and it's on
    # the operator to expose it their own way — SSH tunnel, their own
    # reverse proxy, WireGuard, tailscale serve run by hand, whatever. This
    # URL is honest about that: it's only reachable from the machine itself
    # unless something else is forwarding it.
    return f"http://127.0.0.1:{port}"


def _unpublish(path: str) -> None:
    if PUBLISH_MODE == "tailscale":
        subprocess.run(["tailscale", "serve", "--https=443", f"--set-path={path}", "off"],
                       capture_output=True)


# Any engine without its own url_regex (see Engine above) gets one of these
# automatically: a tiny ttyd web terminal sharing the exact tmux pane. No
# per-engine special-casing — it's purely "does this engine have a hosted
# link or not". In-memory bookkeeping, same restart tradeoff as
# _session_urls below (see docs/ARCHITECTURE.md).
_ttyd_procs: dict[str, subprocess.Popen] = {}
_ttyd_ports: dict[str, int] = {}
_ttyd_urls: dict[str, str] = {}
_next_ttyd_port = 7700


def _ttyd_port(name: str) -> int:
    global _next_ttyd_port
    if name not in _ttyd_ports:
        _ttyd_ports[name] = _next_ttyd_port
        _next_ttyd_port += 1
    return _ttyd_ports[name]


def _ttyd_start(name: str, session: str):
    port = _ttyd_port(name)
    path = f"/term/{urllib.parse.quote(name)}"
    # No -b/--base-path: in tailscale mode, `tailscale serve --set-path`
    # strips the /term/<name> prefix before forwarding, so ttyd sees a plain
    # request at "/" — telling ttyd its own base path here would make it
    # expect the prefix still on the request and 404 everything.
    proc = subprocess.Popen(
        ["sudo", "-u", RUN_USER, TTYD_BIN, "-p", str(port), "-i", "127.0.0.1",
         "-t", f"titleFixed={name}",
         "-W", "/usr/bin/tmux", "attach-session", "-t", session],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _ttyd_procs[name] = proc
    _ttyd_urls[name] = _publish(path, port)


def _ttyd_stop(name: str):
    proc = _ttyd_procs.pop(name, None)
    if proc is not None:
        proc.terminate()
    if name in _ttyd_ports:
        _unpublish(f"/term/{urllib.parse.quote(name)}")
    _ttyd_urls.pop(name, None)


# VS Code in the browser, spawnable per project regardless of which (if any)
# engine is running there — same on-demand, per-project-path pattern as the
# ttyd fallback above.
_code_procs: dict[str, subprocess.Popen] = {}
_code_ports: dict[str, int] = {}
_code_urls: dict[str, str] = {}
_next_code_port = 7900


def _code_port(name: str) -> int:
    global _next_code_port
    if name not in _code_ports:
        _code_ports[name] = _next_code_port
        _next_code_port += 1
    return _code_ports[name]


def code_running(name: str) -> bool:
    proc = _code_procs.get(name)
    return proc is not None and proc.poll() is None


def _code_start(name: str, workdir: str):
    if code_running(name):
        return
    port = _code_port(name)
    path = f"/code/{urllib.parse.quote(name)}"
    proc = subprocess.Popen(
        ["sudo", "-u", RUN_USER, CODE_SERVER_BIN, "--bind-addr", f"127.0.0.1:{port}",
         "--auth", "none", workdir],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _code_procs[name] = proc
    _code_urls[name] = _publish(path, port)


def _code_stop(name: str):
    proc = _code_procs.pop(name, None)
    if proc is not None:
        proc.terminate()
    if name in _code_ports:
        _unpublish(f"/code/{urllib.parse.quote(name)}")
    _code_urls.pop(name, None)


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$")


def _gitea_slug(name: str) -> str:
    # NAME_RE already guarantees name starts with an alnum and is otherwise
    # [A-Za-z0-9 _-]{0,59} -- the only translation Gitea's own
    # [A-Za-z0-9_.-]+ rules require is turning spaces into '-' (docs/spec.md
    # "Local name -> Gitea slug mapping").
    return re.sub(r"\s+", "-", name.strip())


def _gitea_api(method: str, path: str, body: dict = None) -> tuple:
    """Returns (status, parsed_json_or_{}). Never raises for a non-2xx HTTP
    status (the caller inspects `status`, e.g. 409 for a name collision) --
    only for a connection failure, converted to ConnectionError so callers
    can give the same "Gitea isn't reachable" message the gitea_run("status")
    pre-flight check already covers for the toggled-off case. Always
    127.0.0.1:GITEA_PORT -- this call is intra-box (app.py and Gitea's
    container both run on the same host), never routed through
    tailscale serve/BASE_URL regardless of PUBLISH_MODE (docs/spec.md
    "The Gitea API call")."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{GITEA_PORT}/api/v1{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 "Authorization": f"token {GITEA_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ConnectionError("gitea unreachable")


# ─── poll-based sync-on-push (backlog item 2c, part 1) ─────────────────────
# See docs/spec.md "Repo-map + sync-state file" -- resolves owner/repo ->
# PROJECTS_DIR/<name> without app.py (running as SVC_USER) ever needing an
# ambient filesystem read into RUN_USER's home directory.
_gitea_map_lock = threading.Lock()


def _load_gitea_repo_map() -> dict:
    try:
        with open(GITEA_REPO_MAP_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_gitea_repo_map_entry(owner_repo: str, name: str, branch: str,
                               sync_state=None, sync_at=None, remote_sha=None) -> None:
    """Read-modify-write, tmp-file-then-os.replace() -- same idiom
    _save_desc_cache already uses, plus a lock around the read-modify-write
    itself since (unlike the description cache) multiple threads can call
    this concurrently for different projects (create_project() and every
    in-flight _gitea_sync_run() call)."""
    with _gitea_map_lock:
        m = _load_gitea_repo_map()
        m[owner_repo] = {"name": name, "branch": branch, "sync_state": sync_state,
                         "sync_at": sync_at, "remote_sha": remote_sha}
        os.makedirs(os.path.dirname(GITEA_REPO_MAP_FILE), exist_ok=True)
        tmp = GITEA_REPO_MAP_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(m, f, indent=2, sort_keys=True)
        os.replace(tmp, GITEA_REPO_MAP_FILE)


# Per-project (keyed by owner/repo) non-blocking lock for sync concurrency --
# mirrors the _desc_pending per-name-set idiom above: a poll-triggered sync
# attempt that finds the lock already held for this project is simply
# dropped, not queued (docs/spec.md "Concurrency" -- the next poll interval
# converges to the same end state regardless).
_gitea_sync_locks_guard = threading.Lock()
_gitea_sync_locks = {}


def _gitea_sync_lock_for(owner_repo: str) -> threading.Lock:
    with _gitea_sync_locks_guard:
        lock = _gitea_sync_locks.get(owner_repo)
        if lock is None:
            lock = threading.Lock()
            _gitea_sync_locks[owner_repo] = lock
        return lock


def _gitea_sync_run(name: str, branch: str, owner_repo: str, observed_sha: str) -> None:
    """The actual sync attempt: runs GITEA_SYNC_SCRIPT as RUN_USER and
    records the outcome in the repo-map, keyed by owner_repo. Only ever
    called off the /status request thread (see _gitea_sync_bg). A non-zero
    exit (argv/config problem, or the script's own `git fetch` failing) is
    NOT recorded -- remote_sha is deliberately left untouched so the next
    poll interval still sees a diff and retries, rather than silently
    giving up on a transient failure."""
    try:
        r = subprocess.run(["sudo", "-u", RUN_USER, GITEA_SYNC_SCRIPT, name, branch],
                           capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return
    if r.returncode != 0:
        return
    lines = (r.stdout or "").strip().splitlines()
    state = lines[-1] if lines else "error"
    _save_gitea_repo_map_entry(owner_repo, name, branch, sync_state=state,
                               sync_at=time.time(), remote_sha=observed_sha)


def _gitea_sync_bg(name: str, branch: str, owner_repo: str, observed_sha: str) -> None:
    """Spawned by _gitea_poll_one whenever a polled branch's SHA has moved
    since it was last checked. Returns immediately -- the real fetch/sync
    work (a potentially slow `sudo -u RUN_USER` subprocess call) runs on its
    own thread, mirroring _generate_description_bg's own "return fast, do
    the real work off the request thread" idiom, since a git fetch
    shouldn't run synchronously inside a /status request."""
    lock = _gitea_sync_lock_for(owner_repo)
    if not lock.acquire(blocking=False):
        return  # a sync for this project is already in flight

    def _run():
        try:
            _gitea_sync_run(name, branch, owner_repo, observed_sha)
        finally:
            lock.release()

    threading.Thread(target=_run, daemon=True).start()


_gitea_poll_lock = threading.Lock()
_gitea_poll_last_at = 0.0


def _gitea_poll_if_due(gitea_on: bool) -> None:
    global _gitea_poll_last_at
    if not GITEA_ENABLED or not gitea_on:
        return  # feature off, or Gitea itself isn't currently running --
                 # don't hammer _gitea_api with ConnectionErrors
    if time.time() - _gitea_poll_last_at < GITEA_POLL_INTERVAL_SECONDS:
        return
    if not _gitea_poll_lock.acquire(blocking=False):
        return  # another /status request is already mid-poll-pass
    try:
        if time.time() - _gitea_poll_last_at < GITEA_POLL_INTERVAL_SECONDS:
            return  # lost the race -- someone else just finished a pass
        _gitea_poll_last_at = time.time()
        for owner_repo, entry in _load_gitea_repo_map().items():
            try:
                _gitea_poll_one(owner_repo, entry)
            except Exception:
                # One malformed/unexpected response must not silently kill
                # polling for every other registered project in this pass --
                # skip and retry this entry next interval, same "availability
                # nit, never a correctness/safety issue" tolerance the rest
                # of this feature already accepts.
                pass
    finally:
        _gitea_poll_lock.release()


def _gitea_poll_one(owner_repo: str, entry: dict) -> None:
    branch = entry.get("branch", "main")
    try:
        status, resp = _gitea_api("GET", f"/repos/{owner_repo}/branches/{branch}")
    except ConnectionError:
        return  # transient; retried next interval
    if status != 200 or not isinstance(resp, dict):
        return  # repo/branch renamed/deleted, or an unexpected response
                 # shape -- retried next interval either way (e.g. Gitea
                 # mid-restart)
    remote_sha = (resp.get("commit") or {}).get("id", "")
    if not remote_sha or remote_sha == entry.get("remote_sha"):
        return  # nothing new since the last time this was checked
    _gitea_sync_bg(entry["name"], branch, owner_repo, remote_sha)


# ─── switchboard-side deploy dispatch (backlog item 2c, part 2b) ──────────
# See docs/spec.md "Proposed approach" and deploy-target/README.md's
# "Protocol contract". DEPLOY_MAP_FILE is hand-edited by the operator (this
# module never writes it, unlike GITEA_REPO_MAP_FILE above) -- loading
# tolerates a missing/malformed file (-> {}) the same "never crash" way
# _load_gitea_repo_map does, and additionally drops (never raises on) any
# individual entry that's missing a required key or whose "key" path
# resolves outside DEPLOY_KEYS_DIR, so one bad hand-edited entry can't take
# down every other project's Deploy button.
_DEPLOY_MAP_REQUIRED_KEYS = ("host", "deploy_path", "service", "key")


def _load_deploy_map() -> dict:
    try:
        with open(DEPLOY_MAP_FILE) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    keys_root = os.path.realpath(DEPLOY_KEYS_DIR)
    out = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        if any(not entry.get(k) for k in _DEPLOY_MAP_REQUIRED_KEYS):
            continue
        key_path = os.path.realpath(entry["key"])
        if key_path != keys_root and not key_path.startswith(keys_root + os.sep):
            continue  # "key" escapes DEPLOY_KEYS_DIR -- treat as absent, not a crash
        try:
            port = int(entry.get("port") or 22)
        except (TypeError, ValueError):
            continue  # non-numeric "port" -- treat as absent, not a crash
        out[name] = {"host": entry["host"], "port": port,
                     "user": entry.get("user") or "deploy",
                     "deploy_path": entry["deploy_path"], "service": entry["service"],
                     "key": entry["key"]}
    return out


# Per-project non-blocking lock, same guarded-dict idiom as
# _gitea_sync_lock_for -- a concurrent second deploy dispatch for the same
# project is dropped (409), never queued (deploy-target's own receiver adds
# no locking of its own, so this cycle's caller must serialize invocations
# per target -- see deploy-target/README.md "Protocol contract" point 4).
_deploy_locks_guard = threading.Lock()
_deploy_locks = {}


def _deploy_lock_for(name: str) -> threading.Lock:
    with _deploy_locks_guard:
        lock = _deploy_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _deploy_locks[name] = lock
        return lock


def deploy_run(name: str) -> tuple:
    """Synchronous, request-thread dispatch -- mirrors host_run()'s own
    shape (not 2c part 1's background-thread-plus-poll one), since a
    manually clicked one-shot action can and should just block the request
    and return a real result. Follows deploy-target/README.md's "Protocol
    contract" exactly: rsync push with a bare destination (rrsync has
    already fixed it server-side to DEPLOY_PATH), then a second SSH
    connection sending the literal "deploy-restart" command. Returns
    (http_status, message) -- 404 no target configured, 409 already in
    progress, 502 push or restart failed, 200 success."""
    entry = _load_deploy_map().get(name)
    if entry is None:
        return 404, "no deploy target configured for this project"

    lock = _deploy_lock_for(name)
    if not lock.acquire(blocking=False):
        return 409, "a deploy for this project is already in progress"
    try:
        key, host, port, user = entry["key"], entry["host"], entry["port"], entry["user"]
        source = f"{PROJECTS_DIR}/{name}/"  # trailing slash: copy contents, not the dir itself
        try:
            push = subprocess.run(
                ["rsync", "-e",
                 f"ssh -i {key} -o BatchMode=yes -o ConnectTimeout=10 -p {port}",
                 "-a", source, f"{user}@{host}:"],
                capture_output=True, text=True, timeout=60)
        except (subprocess.SubprocessError, OSError) as e:
            return 502, f"push failed: {e}"
        if push.returncode != 0:
            return 502, f"push failed: {(push.stderr or '').strip()[-200:]}"

        try:
            restart = subprocess.run(
                ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 "-p", str(port), f"{user}@{host}", "deploy-restart"],
                capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as e:
            return 502, f"push succeeded but restart failed: {e}"
        if restart.returncode != 0:
            # Surfaced distinctly from a push failure, per deploy-target/
            # README.md's protocol contract: "a non-zero exit means the
            # restart itself failed... surface that, don't swallow it" --
            # an operator reading this needs to know the new code is
            # already on the target even though the service didn't pick it
            # up.
            return 502, f"push succeeded but restart failed: {(restart.stderr or '').strip()[-200:]}"

        return 200, "deployed"
    finally:
        lock.release()


def create_project(name: str) -> tuple[bool, str]:
    if not NAME_RE.match(name or ""):
        return False, "Use letters, numbers, spaces, - or _ (must start with a letter/number)."
    if name in instance_names():
        return False, f"'{name}' already exists."
    if not GITEA_ENABLED:
        return False, ("Gitea isn't installed on this box (install.sh --with-git-hosting) "
                       f"-- or create {PROJECTS_DIR}/{name} yourself (e.g. `git init`) "
                       "and it'll show up here.")
    if not GITEA_API_TOKEN:
        return False, ("Gitea API token isn't configured yet -- run "
                       "scripts/gitea-configure-api.sh once (see docs/GIT_HOSTING.md).")
    status_out = gitea_run("status").splitlines()
    if not status_out or status_out[0] != "on":
        return False, "Gitea is installed but not running -- toggle it on first."

    slug = _gitea_slug(name)
    try:
        status, resp = _gitea_api("POST", "/user/repos",
                                  {"name": slug, "private": True, "auto_init": True,
                                   "default_branch": "main"})
    except ConnectionError:
        return False, "Couldn't reach Gitea -- is it actually running?"
    if status in (409, 422):
        return False, f"A Gitea repository named '{slug}' already exists -- pick a different name."
    if status not in (200, 201):
        return False, f"Gitea rejected the repo creation (HTTP {status})."
    owner = resp.get("owner", {}).get("login", "")
    repo_name = resp.get("name", slug)
    if not owner:
        return False, "Gitea's response didn't include an owner -- can't continue."

    r = subprocess.run(["sudo", NEW_PROJECT_FROM_GITEA_SCRIPT, owner, repo_name, name],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        # Best-effort cleanup: the Gitea repo now exists but nothing landed
        # in PROJECTS_DIR -- don't leave an orphaned repo behind silently.
        # Failure of the cleanup itself is swallowed (the original error is
        # what the user needs to see), same "best-effort, not guaranteed"
        # tradeoff docs/spec.md "Risk / rollback notes" accepts explicitly.
        try:
            _gitea_api("DELETE", f"/repos/{owner}/{repo_name}")
        except ConnectionError:
            pass
        return False, (r.stderr or r.stdout or "registration script failed").strip()[:300]

    # Best-effort, non-fatal (docs/spec.md "Repo-map write in
    # create_project()") -- a pure local JSON file write, not a Gitea API
    # call, so it can really only fail on a disk/permission problem. A
    # failure here doesn't fail create_project()'s own return value: the
    # primary outcome (a real repo, cloned and working) already succeeded;
    # losing only the auto-sync nicety is the same degrade-gracefully
    # tradeoff already accepted for the cleanup-on-clone-failure path above.
    try:
        _save_gitea_repo_map_entry(f"{owner}/{repo_name}", name, "main")
    except OSError:
        pass
    return True, ""


# ─── folder upload → auto-detect repo(s), phase 1 (detect only) ───────────
# See docs/spec.md "Zip-slip protection" / "Detection and the two-phase
# protocol". Everything in this phase-1 section only ever reads/writes
# under UPLOAD_STAGING_DIR, never PROJECTS_DIR — see the phase-2 section
# further down (create_projects_from_selection / confirm_upload) for the
# naming/collision-checking/privileged-registration half.
class UploadRejected(Exception):
    """A whole upload is rejected (zip-slip-shaped entry, corrupt/oversized
    zip, etc.) — always aborts the entire upload, never a partial one."""


def _zip_entry_target(staging_root_real: str, info: zipfile.ZipInfo) -> str:
    """
    Validates one zip entry against zip-slip-shaped attacks and returns the
    on-disk path it would extract to. Raises UploadRejected on any
    violation — see docs/spec.md "Zip-slip protection" for the exact
    mechanics this mirrors. Doesn't touch disk itself; extraction only
    happens after every entry in the archive has passed this check (see
    _extract_zip_safely below), so a single bad entry anywhere aborts the
    whole upload before anything is written.
    """
    name = info.filename
    if "\x00" in name:
        raise UploadRejected(f"zip entry contains a NUL byte: {name!r}")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise UploadRejected(f"zip entry has an absolute path: {name!r}")
    if ".." in normalized.split("/"):
        raise UploadRejected(f"zip entry contains a '..' path component: {name!r}")
    target = os.path.realpath(os.path.join(staging_root_real, normalized))
    if target != staging_root_real and \
            os.path.commonpath([target, staging_root_real]) != staging_root_real:
        raise UploadRejected(f"zip entry resolves outside the staging directory: {name!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise UploadRejected(f"zip entry is a symlink, not allowed: {name!r}")
    return target


def _extract_zip_safely(zf: zipfile.ZipFile, staging_subdir: str) -> None:
    """
    Validates every entry first (see _zip_entry_target), then extracts only
    if the whole archive passes — "any rejection aborts the entire upload,
    nothing partial is left staged" (docs/spec.md). staging_subdir must not
    already exist; created here once validation has passed.
    """
    staging_root_real = os.path.realpath(staging_subdir)
    targets = {info.filename: _zip_entry_target(staging_root_real, info)
              for info in zf.infolist()}
    os.makedirs(staging_subdir)
    for info in zf.infolist():
        target = targets[info.filename]
        if info.filename.endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _unwrap_single_wrapper_folder(staged_root: str) -> str:
    """
    If the staged root contains exactly one non-hidden top-level entry and
    it's a directory, treat that subdirectory as the effective root for
    detection — the exact shape GitHub/GitLab/Bitbucket's own "Download
    ZIP" always produces (e.g. myrepo-main/), and also what a client-built
    zip from a picked *folder* naturally has (webkitdirectory paths are all
    prefixed with the picked folder's own name). Applied once, not
    recursively; hidden top-level entries (e.g. a stray .DS_Store) don't
    prevent the unwrap.
    """
    non_hidden = [e for e in os.listdir(staged_root) if not e.startswith(".")]
    if len(non_hidden) == 1:
        candidate = os.path.join(staged_root, non_hidden[0])
        if os.path.isdir(candidate) and not os.path.islink(candidate):
            return candidate
    return staged_root


def detect_structure(effective_root: str) -> dict:
    """
    Phase 1's read-only detection walk (docs/spec.md "Detection and the
    two-phase protocol", step 1). Registers nothing — just describes what
    was found so the (future) review step can offer a real choice. Root
    name here is purely informational for that review step; phase 2 is
    what actually derives/sanitizes a project name from it.
    """
    root_name = os.path.basename(effective_root.rstrip(os.sep)) or effective_root
    root_git_dir = os.path.join(effective_root, ".git")
    root_has_git = os.path.isdir(root_git_dir) and not os.path.islink(root_git_dir)

    nested_git_paths = []
    for dirpath, dirnames, _filenames in os.walk(effective_root):
        git_dir = os.path.join(dirpath, ".git")
        if ".git" in dirnames and os.path.isdir(git_dir) and not os.path.islink(git_dir):
            if dirpath != effective_root:
                rel = os.path.relpath(dirpath, effective_root).replace(os.sep, "/")
                nested_git_paths.append(rel)
            dirnames.remove(".git")  # never descend into a .git dir itself

    top_level_subdirs = []
    loose_top_level_files = 0
    if not root_has_git:
        for entry in sorted(os.listdir(effective_root)):
            full = os.path.join(effective_root, entry)
            if os.path.isdir(full):
                if not entry.startswith("."):
                    top_level_subdirs.append(entry)
            else:
                loose_top_level_files += 1

    ambiguous = ((root_has_git and len(nested_git_paths) >= 1) or
                (not root_has_git and len(top_level_subdirs) >= 2))

    return {
        "root_name": root_name,
        "root_has_git": root_has_git,
        "nested_git_paths": sorted(nested_git_paths),
        "top_level_subdirs": top_level_subdirs,
        "loose_top_level_files": loose_top_level_files,
        "ambiguous": ambiguous,
    }


# ─── folder upload → auto-detect repo(s), phase 2 (confirm + register) ────
# See docs/spec.md "Detection and the two-phase protocol" (phase 2) and
# "Partial-failure semantics for a multi-project confirm call". Naming/
# collision-checking here still runs entirely unprivileged; only the final
# per-project registration step crosses into RUN_USER's territory, via
# NEW_PROJECT_FROM_UPLOAD_SCRIPT (see "Crossing the privilege boundary").
def _derive_project_name(raw: str) -> str:
    """
    Sanitizes a raw folder name (from the uploaded zip's own contents —
    fully attacker-controlled) into a NAME_RE-valid project name
    (docs/spec.md step 5): strip disallowed characters, strip any leading
    non-alnum run (NAME_RE requires starting with a letter/number), cap at
    60 chars. Falls back to "upload-<8 hex chars>" if nothing usable
    survives.
    """
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", raw or "")
    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", cleaned)[:60]
    if NAME_RE.match(cleaned):
        return cleaned
    return f"upload-{secrets.token_hex(4)}"


def _register_via_privileged_script(source: str, name: str) -> subprocess.CompletedProcess:
    """
    The one line that actually crosses the privilege boundary — split out
    from create_projects_from_selection() so tests can substitute a fake
    without needing a real sudoers rule / installed script / RUN_USER on the
    test box.
    """
    return subprocess.run(["sudo", NEW_PROJECT_FROM_UPLOAD_SCRIPT, source, name],
                          capture_output=True, text=True, timeout=60)


def create_projects_from_selection(staging_root: str, mode: str, selected: list):
    """
    Phase 2's core logic (docs/spec.md "Detection and the two-phase
    protocol" phase 2). Re-walks the staging tree fresh via
    detect_structure() rather than trusting the client's `selected` list
    blindly — any path that doesn't match a currently-valid candidate from
    that fresh walk is rejected, nothing registered. Every resulting
    project's name is derived/sanitized and collision-checked (against
    existing PROJECTS_DIR entries and against each other) up front, before
    any privileged script runs — a collision rejects the whole call.

    Returns (ok: bool, error: str, registered: list[str], skipped: int).
    On a genuine TOCTOU race defeating one specific project's registration
    after sibling projects in the same call already succeeded, that one
    fails (named in the error, `registered` still lists the siblings that
    already succeeded) — those siblings are NOT rolled back, see
    docs/spec.md "Partial-failure semantics" for why that's intentional.
    """
    effective_root = _unwrap_single_wrapper_folder(staging_root)
    detection = detect_structure(effective_root)

    if mode not in ("single", "split"):
        return False, "mode must be 'single' or 'split'", [], 0
    if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
        return False, "selected must be a list of strings", [], 0

    to_register = []  # [(source_dir, raw_name_for_sanitizing), ...]
    skipped = 0

    if mode == "single":
        # `selected` is ignored in single mode (docs/spec.md "Wire format
        # and endpoints": "ignored/must be empty when mode == 'single'").
        to_register.append((effective_root, detection["root_name"]))
    else:
        candidates = (detection["nested_git_paths"] if detection["root_has_git"]
                     else detection["top_level_subdirs"])
        valid = set(candidates)
        invalid = sorted(set(selected) - valid)
        if invalid:
            return False, ("selection contains a path that is no longer a valid "
                           f"candidate (stale or tampered): {', '.join(invalid)}"), [], 0
        chosen = list(dict.fromkeys(p for p in selected if p in valid))  # de-dupe, keep order
        skipped = len(valid) - len(chosen)

        if detection["root_has_git"]:
            # Root is ALSO always registered in this shape (docs/spec.md
            # "Root-has-.git + split") — selecting zero nested paths is
            # equivalent to "single", not an error.
            to_register.append((effective_root, detection["root_name"]))
            for p in chosen:
                to_register.append((os.path.join(effective_root, p), os.path.basename(p)))
        else:
            if not chosen:
                return False, "select at least one project", [], 0
            for p in chosen:
                to_register.append((os.path.join(effective_root, p), p))

    names = [_derive_project_name(raw) for _source, raw in to_register]

    existing = set(instance_names())
    seen = set()
    collisions = set()
    for n in names:
        if n in existing or n in seen:
            collisions.add(n)
        seen.add(n)
    if collisions:
        return False, f"name collision: {', '.join(sorted(collisions))}", [], 0

    registered = []
    for (source, _raw), name in zip(to_register, names):
        r = _register_via_privileged_script(source, name)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "registration failed").strip()[:300]
            return False, f"'{name}' failed to register: {err}", registered, 0
        registered.append(name)

    return True, "", registered, skipped


_UPLOAD_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def confirm_upload(token: str, mode: str, selected: list):
    """
    Route logic for POST /projects/upload/confirm. Validates the token
    shape (it's used directly in a filesystem path, so only the exact
    secrets.token_hex(16) shape is accepted — anything else is rejected
    before ever touching the filesystem), delegates to
    create_projects_from_selection(), and performs confirm-triggered
    cleanup: UPLOAD_STAGING_DIR/<token>/ is removed (best-effort) only once
    this call *succeeds* — a failed confirm (e.g. a name collision) leaves
    staging in place so the UI's "Back to review" button can retry the same
    token with a tweaked selection, evaluated fresh against the still-staged
    tree (docs/spec.md "Two-phase protocol"). Abandoned staging from a
    failed confirm that's never retried is still cleaned up eventually by
    the existing UPLOAD_STAGING_TTL_SECONDS sweep in _reap_dead_state().
    Returns (http_status, response_dict).
    """
    if not token or not _UPLOAD_TOKEN_RE.match(token):
        return 404, {"error": "upload expired or not found — start over"}
    staging_root = os.path.join(UPLOAD_STAGING_DIR, token)
    if not os.path.isdir(staging_root):
        return 404, {"error": "upload expired or not found — start over"}
    ok, err, registered, skipped = create_projects_from_selection(staging_root, mode, selected)
    if not ok:
        return 400, {"error": err, "registered": registered}
    shutil.rmtree(staging_root, ignore_errors=True)
    return 200, {"ok": True, "registered": registered, "skipped": skipped}


def tmux_has(session: str) -> bool:
    r = subprocess.run(TMUX + ["has-session", "-t", session], capture_output=True)
    return r.returncode == 0


def active_engine(name: str) -> str | None:
    return next((e for e in load_engines() if tmux_has(f"{e}-{name}")), None)


# Hosted remote-control links (e.g. Claude Code's claude.ai/code/session_...
# URL) captured from a session's own startup output — in-memory only, same
# lifetime as running sessions (lost on a service restart; see
# docs/ARCHITECTURE.md for the accepted tradeoff and its one sharp edge).
_session_urls: dict[str, str] = {}


def run_startup_watch(session: str, name: str, engine: "Engine", timeout: int = STARTUP_TIMEOUT):
    """
    Works through engine.startup (scripted one-time interactions like
    clearing a "trust this folder" prompt) in order, and — if the engine
    defines url_regex — opportunistically captures its hosted link along the
    way into _session_urls. Polls the pane once a second for up to `timeout`
    seconds, exiting early once the startup script is exhausted and (if
    applicable) a URL has been captured.
    """
    remaining = list(engine.startup)
    for _ in range(timeout):
        time.sleep(1)
        r = subprocess.run(TMUX + ["capture-pane", "-t", session, "-p"],
                           capture_output=True, text=True)
        pane = r.stdout
        if engine.url_regex:
            m = engine.url_regex.search(pane)
            if m:
                _session_urls[name] = m.group(0)
        if remaining and remaining[0][0] in pane:
            _, send = remaining.pop(0)
            subprocess.run(TMUX + ["send-keys", "-t", session, send, "Enter"])
            continue
        if not remaining and (not engine.url_regex or name in _session_urls):
            break


def instance_start(name: str, engine_name: str = "claude"):
    engines = load_engines()
    engine = engines.get(engine_name)
    if engine is None:
        return
    workdir = os.path.join(PROJECTS_DIR, name)
    if active_engine(name) is not None or not os.path.isdir(workdir):
        return
    _session_urls.pop(name, None)
    session = f"{engine_name}-{name}"
    cmd = engine.cmd.format(name=shlex.quote(name))
    # Run the engine command directly as the tmux session's own command (not
    # via send-keys into a persistent shell). That way the *session's*
    # lifetime is tied to the *engine process's* lifetime — when the engine
    # exits (user quits, process dies), the pane's command exits and tmux
    # tears the session down with it, so `tmux has-session` (and thus "is
    # this running") self-heals instead of reporting true forever against a
    # bare leftover shell.
    subprocess.run(TMUX + ["new-session", "-d", "-s", session, "-c", workdir,
                           "bash", "-lc", cmd])
    if engine.startup or engine.url_regex:
        run_startup_watch(session, name, engine)
    if not engine.url_regex:
        _ttyd_start(name, session)


def instance_stop(name: str):
    # Deliberately does NOT touch VS Code (_code_stop) — code-server has its
    # own independent on/off lifecycle, spawnable and usable whether or not
    # an engine session is running.
    _session_urls.pop(name, None)
    _ttyd_stop(name)
    for e in load_engines():
        subprocess.run(TMUX + ["kill-session", "-t", f"{e}-{name}"], capture_output=True)


def _reap_dead_state():
    """
    tmux sessions die on their own the moment the engine process inside them
    exits (see instance_start), so active_engine() is the source of truth
    for whether a project is "running" — but leftover bookkeeping (a
    captured hosted URL, a still-running ttyd/code-server proc, a
    tailscale-serve mapping pointing at a now-dead port) doesn't clear
    itself just because the tmux session went away between polls. Called on
    every /status so the UI self-heals even when a session ends on its own
    (crash, quit, `tmux kill-session` from inside) rather than only when the
    user explicitly flips the switch off.

    Also sweeps abandoned upload-wizard staging directories (docs/spec.md
    "Two-phase protocol" — TTL/idle cleanup for abandoned uploads), reusing
    this same "opportunistic cleanup on a request that already happens
    often" precedent rather than a dedicated background thread/timer.
    """
    engines = load_engines()
    for name in list(_session_urls):
        e = engines.get(active_engine(name) or "")
        if e is None or not e.url_regex:
            _session_urls.pop(name, None)
    for name in set(_ttyd_urls) | set(_ttyd_procs):
        ae = active_engine(name)
        e = engines.get(ae or "")
        # ttyd fallback belongs to whichever engine is active AND lacks its
        # own hosted link; anything else means it's stale.
        if ae is None or (e is not None and e.url_regex):
            _ttyd_stop(name)
    for name in list(_code_procs):
        proc = _code_procs.get(name)
        if proc is not None and proc.poll() is not None:
            _code_stop(name)
    if os.path.isdir(UPLOAD_STAGING_DIR):
        cutoff = time.time() - UPLOAD_STAGING_TTL_SECONDS
        for entry in os.listdir(UPLOAD_STAGING_DIR):
            full = os.path.join(UPLOAD_STAGING_DIR, entry)
            try:
                if os.path.isdir(full) and os.path.getmtime(full) < cutoff:
                    shutil.rmtree(full, ignore_errors=True)
            except OSError:
                continue


def host_run(action: str) -> str:
    assert action in ("start", "stop", "status")
    r = subprocess.run(
        ["ssh", "-i", HOST_CONTROL_KEY, "-o", "BatchMode=yes",
         "-o", "ConnectTimeout=5", f"{HOST_CONTROL_USER}@{HOST_IP}",
         f"sudo /usr/local/bin/ai-dev-switchboard-host-{action}.sh"],
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip()


# ─── self-hosted Taiga (backlog item 1a) ───────────────────────────────────
# Singleton, like host-control above — but unlike host-control (a session on
# a genuinely separate machine) and unlike ttyd/code-server (in-memory
# subprocess.Popen handles that die with app.py's own process), Taiga's
# containers are managed by dockerd, entirely outside app.py's process tree.
# They survive an `ai-dev-switchboard` service restart, so state is never
# trusted from memory here — every /status poll calls taiga_run("status")
# fresh, exactly like host_run("status") already does for the host row.
def taiga_run(action: str) -> str:
    assert action in ("up", "down", "status")
    script = {"up": TAIGA_UP_SCRIPT, "down": TAIGA_DOWN_SCRIPT,
              "status": TAIGA_STATUS_SCRIPT}[action]
    r = subprocess.run(["sudo", script], capture_output=True, text=True,
                       timeout=(10 if action == "status" else 90))
    return r.stdout.strip()


TAIGA_URL_PATH = "/taiga"  # fixed, singleton — no per-name path like /term or /code


def _taiga_display_url() -> str:
    return f"{BASE_URL}{TAIGA_URL_PATH}" if PUBLISH_MODE == "tailscale" \
        else f"http://127.0.0.1:{TAIGA_PORT}"


# ─── self-hosted Gitea (backlog item 2a) ───────────────────────────────────
# Singleton, exactly like Taiga above — same "containers outlive app.py's own
# process tree, so state is never trusted from memory" reasoning (see
# taiga_run's own comment), just against Gitea's own two-service (server +
# db) stack instead of Taiga's nine.
def gitea_run(action: str) -> str:
    assert action in ("up", "down", "status")
    script = {"up": GITEA_UP_SCRIPT, "down": GITEA_DOWN_SCRIPT,
              "status": GITEA_STATUS_SCRIPT}[action]
    r = subprocess.run(["sudo", script], capture_output=True, text=True,
                       timeout=(10 if action == "status" else 90))
    return r.stdout.strip()


GITEA_URL_PATH = "/gitea"  # fixed, singleton — same shape as TAIGA_URL_PATH


def _gitea_display_url() -> str:
    return f"{BASE_URL}{GITEA_URL_PATH}" if PUBLISH_MODE == "tailscale" \
        else f"http://127.0.0.1:{GITEA_PORT}"


PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ai-dev-switchboard</title>
<meta name="description" content="Start/stop AI coding-agent sessions per project, spawn VS Code, create new projects.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%A7%B0%3C/text%3E%3C/svg%3E">
<style>
  body { font-family: -apple-system, sans-serif; background: #111; color: #eee;
         max-width: 480px; margin: 40px auto; padding: 0 16px; }
  h1 { font-size: 20px; }
  .row { display: flex; justify-content: space-between; align-items: center;
         padding: 16px; background: #1c1c1c; border-radius: 12px; margin-bottom: 12px; }
  .label { font-size: 16px; }
  .desc { font-size: 12px; color: #aaa; margin-top: 2px; }
  .engine-label { font-size: 11px; color: #666; text-transform: uppercase;
                  letter-spacing: 0.5px; margin-top: 8px; }
  .engine-picker { display: flex; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
  .pill { font-size: 13px; padding: 5px 12px; border-radius: 20px; background: #2a2a2a;
          color: #aaa; cursor: pointer; user-select: none; border: 1px solid #3a3a3a; }
  .pill.active { background: #34c759; color: #111; font-weight: 600; border-color: #34c759; }
  .badge { display: inline-block; font-size: 12px; padding: 4px 11px; border-radius: 20px;
           background: #16324a; color: #4da6ff; margin-top: 6px; font-weight: 600; }
  /* Taiga's resource-cost badge: brighter text than the plain .badge default
     (#4da6ff) — #66d9ff on the same #16324a background clears the WCAG AA
     4.5:1 text-contrast threshold (~8.1:1) with real headroom, see
     docs/implementation.md for the contrast math. */
  .badge.taiga-ram { color: #66d9ff; }
  /* Gitea's resource-cost badge reuses the exact same already-verified
     color pairing as Taiga's above (#66d9ff on #16324a, ~8.14:1 contrast,
     well above WCAG AA) — same badge styling, different tone (docs/design.md
     "Resource badge: informational tone, not warning"). */
  .badge.gitea-resources { color: #66d9ff; }
  .sub { font-size: 12px; color: #888; margin-top: 4px; word-break: break-all; }
  .taiga-err { color: #ff6b6b; }
  .gitea-err { color: #ff6b6b; }
  .taiga-starting-spinner { display: inline-block; width: 12px; height: 12px;
                             margin-left: 4px; vertical-align: middle;
                             animation: taiga-spin 1s linear infinite; }
  .gitea-starting-spinner { display: inline-block; width: 12px; height: 12px;
                             margin-left: 4px; vertical-align: middle;
                             animation: gitea-spin 1s linear infinite; }
  @keyframes taiga-spin {
    0% { transform: rotate(0deg); opacity: 0.6; }
    50% { opacity: 1; }
    100% { transform: rotate(360deg); opacity: 0.6; }
  }
  @keyframes gitea-spin {
    0% { transform: rotate(0deg); opacity: 0.6; }
    50% { opacity: 1; }
    100% { transform: rotate(360deg); opacity: 0.6; }
  }
  .vscode-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .pill.code-pill { background: #2a2a2a; }
  .pill.code-pill.active { background: #4da6ff; color: #111; border-color: #4da6ff; }
  /* Deploy dispatch (backlog item 2c, part 2b -- docs/design.md "Component
     reuse and styling"). .deploy-btn reuses .new-project-row button's own
     green/white/pill shape verbatim; .deploy-msg reuses .new-project-err's
     shape, with .success/.error variants instead of a single fixed color. */
  .deploy-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .deploy-btn { font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none;
                background: #34c759; color: #fff; font-weight: 600; cursor: pointer;
                white-space: nowrap; }
  .deploy-msg { font-size: 12px; color: #888; margin: 4px 0 0; min-height: 14px; word-break: break-all; }
  .deploy-msg.success { color: #34c759; }
  .deploy-msg.error { color: #ff6b6b; }
  .new-project-row { display: flex; gap: 8px; padding: 4px 0 16px; }
  .new-project-row input { flex: 1; font-size: 14px; padding: 10px 12px; border-radius: 10px;
                            border: 1px solid #333; background: #1c1c1c; color: #eee; }
  .new-project-row button { font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none;
                             background: #34c759; color: #111; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .new-project-err { color: #ff6b6b; font-size: 12px; margin: -10px 0 12px; min-height: 14px; }
  .switch { position: relative; width: 51px; height: 31px; flex-shrink: 0; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider { position: absolute; inset: 0; background: #444; border-radius: 31px;
            cursor: pointer; transition: 0.2s; }
  .slider:before { content: ""; position: absolute; height: 27px; width: 27px; left: 2px;
                    top: 2px; background: white; border-radius: 50%; transition: 0.2s; }
  input:checked + .slider { background: #34c759; }
  input:checked + .slider:before { transform: translateX(20px); }
  a { color: #4da6ff; }
  .empty { color: #666; font-size: 14px; padding: 8px 16px; }

  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.72);
             display: none; align-items: center; justify-content: center; z-index: 10; }
  .overlay.show { display: flex; }
  .card { background: #1c1c1c; border-radius: 16px; padding: 24px; width: 100%;
          max-width: 340px; box-sizing: border-box; }
  .card h2 { margin: 0 0 4px; font-size: 18px; }
  .card p.hint { margin: 0 0 16px; font-size: 13px; color: #888; }
  .card input { width: 100%; box-sizing: border-box; font-size: 16px;
                padding: 12px; border-radius: 10px; border: none;
                background: #111; color: #eee; margin-bottom: 12px; }
  .card input#login-code { font-size: 20px; letter-spacing: 4px; text-align: center; }
  .card button { width: 100%; padding: 12px; border-radius: 10px; border: none;
                 background: #34c759; color: #111; font-size: 16px; font-weight: 600; }
  .card .err { color: #ff6b6b; font-size: 14px; margin-bottom: 12px; min-height: 18px; }
  .card .back { display: block; text-align: center; margin-top: 12px; font-size: 13px;
                color: #888; cursor: pointer; }

  .upload-wizard-btn { display: block; width: 100%; box-sizing: border-box; font-size: 14px;
                        padding: 13px 16px; border-radius: 10px; border: 1px solid #333;
                        background: #1c1c1c; color: #4da6ff; font-weight: 600; cursor: pointer;
                        margin: 0 0 16px; text-align: center; }
  .wizard-card { max-width: 420px; max-height: 85vh; overflow-y: auto; }
  .wizard-step-indicator { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
  .wizard-step { font-size: 11px; padding: 4px 9px; border-radius: 12px; background: #2a2a2a; color: #666; }
  .wizard-step.active { background: #16324a; color: #4da6ff; font-weight: 600; }
  .wizard-step.done { color: #34c759; }
  .wizard-step.disabled { opacity: 0.4; }
  .wizard-body p { font-size: 13px; color: #aaa; margin: 0 0 10px; }
  .wizard-body p.err { color: #ff6b6b; }
  .wizard-pick-row button { width: 100%; padding: 13px; border-radius: 10px; border: 1px solid #333;
                             background: #2a2a2a; color: #eee; font-size: 14px; font-weight: 600;
                             cursor: pointer; margin-bottom: 8px; min-height: 44px; }
  .wizard-or { text-align: center; color: #666; font-size: 12px; margin: 10px 0; }
  .wizard-check-row { display: flex; align-items: center; min-height: 44px; gap: 10px;
                       padding: 4px 0; cursor: pointer; }
  .wizard-check-row input { accent-color: #34c759; width: 18px; height: 18px; flex-shrink: 0; }
  .wizard-check-row .info { font-size: 13px; }
  .wizard-check-row .info .sub { font-size: 12px; color: #888; }
  /* Step 5's single/split mode choice, styled as pills matching .pill/
     .pill.active (engineRow/codeRow) while keeping a real <input
     type="radio"> underneath for keyboard/screen-reader semantics -- see
     docs/design.md "New CSS rule: .wizard-check-row.pill-choice". */
  .wizard-check-row.pill-choice { padding: 5px 12px; border-radius: 20px; background: #2a2a2a;
                                    color: #aaa; border: 1px solid #3a3a3a; gap: 8px;
                                    margin: 0 4px 0 0; display: inline-flex; }
  .wizard-check-row.pill-choice:has(input:checked) { background: #34c759; color: #111;
                                                        font-weight: 600; border-color: #34c759; }
  .wizard-progress-bg { background: #2a2a2a; height: 6px; border-radius: 3px; margin: 10px 0 6px; overflow: hidden; }
  .wizard-progress-fill { height: 6px; border-radius: 3px; transition: none; }
  .wizard-progress-fill.zip { background: #34c759; }
  .wizard-progress-fill.upload { background: #4da6ff; }
  .wizard-progress-label { font-size: 12px; color: #888; }
  .wizard-warn { background: rgba(255, 193, 7, 0.1); border-left: 4px solid #ffc107; padding: 10px 12px;
                 border-radius: 6px; font-size: 13px; color: #eee; margin: 10px 0; }
  .wizard-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 4px; }
  .wizard-actions button { padding: 13px 18px; border-radius: 10px; border: none; font-size: 14px;
                            font-weight: 600; cursor: pointer; min-height: 44px; }
  .wizard-actions .primary { background: #34c759; color: #111; }
  .wizard-actions .secondary { background: #2a2a2a; color: #aaa; }
  .wizard-json { background: #111; border: 1px solid #333; border-radius: 8px; padding: 10px;
                 font-size: 11px; color: #aaa; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }
</style></head>
<body>
<h1>ai-dev-switchboard</h1>
<div class="new-project-row">
  <input id="new-project-name" placeholder="new project name" maxlength="60">
  <button onclick="startNewProject()">+ New project</button>
</div>
<div class="new-project-err" id="new-project-err"></div>
<button class="upload-wizard-btn" onclick="openUploadWizard()">Upload folder / .zip</button>
<div id="rows"></div>

<div id="upload-overlay" class="overlay">
  <div class="card wizard-card">
    <h2>Upload local folder or .zip</h2>
    <div id="wizard-steps" class="wizard-step-indicator"></div>
    <div class="wizard-body" id="wizard-body"></div>
    <div class="err" id="wizard-err"></div>
    <div class="wizard-actions" id="wizard-actions"></div>
    <span class="back" onclick="closeUploadWizard()">&lsaquo; close</span>
  </div>
</div>
<input type="file" id="wizard-folder-input" webkitdirectory style="display:none">
<input type="file" id="wizard-zip-input" accept=".zip" style="display:none">

<div id="code-overlay" class="overlay">
  <div class="card">
    <h2>Authenticator code</h2>
    <p class="hint" id="code-overlay-label">Confirm this action.</p>
    <div class="err" id="err-code"></div>
    <input id="action-code" placeholder="6-digit code" inputmode="numeric" maxlength="6">
    <button onclick="submitActionCode()">Confirm</button>
    <span class="back" onclick="cancelActionCode()">&lsaquo; cancel</span>
  </div>
</div>

<div id="overlay" class="overlay">
  <div class="card">
    <h2 id="login-title">__LOGIN_TITLE__</h2>
    <p class="hint">__LOGIN_HINT__</p>
    <div class="err" id="err-creds"></div>
    <input id="login-user" placeholder="username" autocomplete="username" value="__LOGIN_USER_HINT__">
    <input id="login-pass" type="password" placeholder="password" autocomplete="current-password">
    <button onclick="login()">Sign in</button>
  </div>
</div>

<script>
function showOverlay() {
  document.getElementById('overlay').classList.add('show');
  document.getElementById('err-creds').textContent = '';
  document.getElementById('login-pass').value = '';
}
function hideOverlay() { document.getElementById('overlay').classList.remove('show'); }
async function login() {
  const username = document.getElementById('login-user').value;
  const password = document.getElementById('login-pass').value;
  if (!username || !password) {
    document.getElementById('err-creds').textContent = 'Enter username and password.';
    return;
  }
  const r = await fetch('/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({username, password})});
  if (r.ok) {
    document.getElementById('login-pass').value = '';
    hideOverlay();
    refresh();
  } else {
    document.getElementById('err-creds').textContent = 'Wrong username or password — try again.';
  }
}
document.getElementById('login-pass').addEventListener('keydown', e => { if (e.key === 'Enter') login(); });

let ENGINE_LABELS = {};
let engineChoice = {};  // project name -> engine name, picked before starting
// project name -> {host, deploy_path, service} from /status's per-instance
// "deploy" field, refreshed every refresh() call -- doDeploy() looks a
// project's target info up here instead of it being inlined into the
// rendered onclick="..." attribute (docs/spec.md's own note: deploy.host/
// deploy.service come from an operator-hand-edited JSON file and could in
// principle contain quote characters, so they're never embedded directly
// into HTML markup).
let DEPLOY_TARGETS = {};

// Singleton-toggle rows (Taiga, Gitea, ...future ones) need more visual
// states than the generic on/off rows above (see docs/design.md "How
// starting→running detection works") — their Docker stacks can take 30-90s
// to come up, so a toggle-on doesn't mean "running" yet the way it does for
// host-control's SSH-backed session. This state machine was hardened for
// Taiga specifically across three real review rounds (docs/test-review.md
// at ed84d73, Defects 1 and 2) before being generalized here to a per-kind
// map so Gitea (backlog item 2a) reuses the exact same hardened logic
// instead of a copy-pasted-and-renamed fork — see docs/spec.md
// "Proposed approach: generalizing the toggle state machine".
//
// Per kind: `pending` tracks an in-flight "waiting to see <kind>:true"
// window (cleared once /status confirms it, or after that kind's own
// timeout — design.md's fallback so the row never gets stuck showing
// "starting…" forever on a genuine failure). `wasRunning` lets a later poll
// tell "toggled off on purpose" apart from "was running, suddenly isn't" (a
// transient container hiccup) — the latter re-arms a fresh starting window
// instead of flashing "error" immediately. `offPendingCount` covers the
// window between intentional toggle-off request(s) being sent and them
// resolving — the backend blocks on `docker compose down` for up to 90s, so
// a regular 4s /status poll can easily land mid-flight and still
// (accurately) report <kind>:true. Without tracking this, that poll would
// re-set wasRunning=true and clobber the toggle-off's own reset, making
// refresh() misread the eventual real "off" as an *unexpected* stop instead
// of the intentional one it actually is (see docs/test-review.md Defect 1).
// While this count is > 0, refresh() must not let a poll re-arm
// wasRunning — only the toggle-off code paths themselves increment/
// decrement it.
//
// offPendingCount is a count, not a boolean, because the toggle checkbox is
// never disabled while an action is in flight and the row keeps
// re-rendering as "on" (accurately) for as long as any prior off request is
// still running — an impatient user can realistically fire a second,
// independent off dispatch before the first resolves. A plain boolean that
// either off request's own completion unconditionally clears to false would
// let the *first* to resolve declare the coast clear while the *second* off
// request is still genuinely outstanding, reopening Defect 1's exact false
// starting…/error outcome via a different trigger (docs/test-review.md
// Defect 2). Only treat "no off request in flight" as true once the count
// reaches zero, i.e. every dispatched off request has resolved.
let singletonToggleState = {
  taiga: {pending: null, wasRunning: false, offPendingCount: 0},
  gitea: {pending: null, wasRunning: false, offPendingCount: 0},
};
// Per-kind values that used to be hardcoded to Taiga's own numbers/text
// (the 90s starting-timeout, the resource badge) — see docs/design.md
// "Resource badge: informational tone, not warning" for why Gitea's badge
// uses a different symbol/tone/color-class than Taiga's, and "Starting-state
// timeout" for why both kinds keep the same safe 90s upper bound rather than
// tuning Gitea's down (a safety ceiling, not a performance target).
const SINGLETON_TOGGLE_CONFIG = {
  taiga: {timeoutMs: 90000, badgeText: '⚠ ~3–5 GB RAM when running',
          badgeClass: 'taiga-ram', errClass: 'taiga-err', spinnerClass: 'taiga-starting-spinner'},
  gitea: {timeoutMs: 90000, badgeText: 'ℹ ~1 GB RAM when running',
          badgeClass: 'gitea-resources', errClass: 'gitea-err', spinnerClass: 'gitea-starting-spinner'},
};
// Computes a singleton-toggle row's sub-text (running/starting…/stopped/
// error) AND updates that kind's own singletonToggleState entry as a side
// effect — same single computation refresh() used to do inline for Taiga
// alone, now shared by every kind in SINGLETON_TOGGLE_CONFIG. Returns
// {sub, showBadge}.
function singletonToggleSub(kind, on, url) {
  const cfg = SINGLETON_TOGGLE_CONFIG[kind];
  const st = singletonToggleState[kind];
  let sub, showBadge = true;
  if (on) {
    sub = 'running' + (url ? ' — <a href="' + url + '" target="_blank">open</a>' : '');
    st.pending = null;
    // Don't let a poll landing mid-toggle-off re-arm wasRunning — the
    // toggle-off itself already reset it and owns that reset until every
    // dispatched off request resolves (see offPendingCount's declaration
    // comment above).
    if (st.offPendingCount === 0) st.wasRunning = true;
  } else {
    if (st.wasRunning && st.offPendingCount === 0) {
      st.pending = {startTime: Date.now()};
      st.wasRunning = false;
    }
    if (st.pending) {
      if (Date.now() - st.pending.startTime > cfg.timeoutMs) {
        sub = '<span class="' + cfg.errClass + '">error</span>';
        showBadge = false;
      } else {
        sub = 'starting… <span class="' + cfg.spinnerClass + '">◌</span>';
      }
    } else {
      sub = 'stopped';
    }
  }
  return {sub, showBadge};
}

async function refresh() {
  const r = await fetch('/status');
  if (r.status === 401) { showOverlay(); return; }
  const s = await r.json();
  ENGINE_LABELS = s.engines || {};
  DEPLOY_TARGETS = {};
  let html = '';
  for (const inst of s.instances) {
    if (inst.deploy) DEPLOY_TARGETS[inst.name] = inst.deploy;
    html += row(inst.name, inst.on, inst.url, 'inst', inst.name, inst.desc, inst.engine,
               inst.code_on, inst.code_url, undefined, undefined, inst.gitea_sync, inst.deploy);
  }
  if (s.instances.length === 0) html += '<div class="empty">No project folders under the configured PROJECTS_DIR yet.</div>';
  if (s.host_enabled) html += row(s.host_label, s.host, s.host_url, 'host', null, '', null, false, null);
  if (s.taiga_enabled) {
    const {sub, showBadge} = singletonToggleSub('taiga', s.taiga, s.taiga_url);
    html += row(s.taiga_label, s.taiga, s.taiga_url, 'taiga', null, '', null, false, null, sub, showBadge);
  }
  if (s.gitea_enabled) {
    const {sub, showBadge} = singletonToggleSub('gitea', s.gitea, s.gitea_url);
    html += row(s.gitea_label, s.gitea, s.gitea_url, 'gitea', null, '', null, false, null, sub, showBadge);
  }
  document.getElementById('rows').innerHTML = html;
}
function esc(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}
function pickEngine(name, engine) {
  engineChoice[name] = engine;
  refresh();
}
function engineRow(name, on, engine) {
  // Only real per-project instances get an engine choice — the host row (if
  // enabled) uses a single fixed engine, no picker needed there.
  const names = Object.keys(ENGINE_LABELS);
  if (names.length === 0) return '';
  if (on) {
    return '<div class="engine-label">Running</div>' +
      '<div class="badge">' + esc(ENGINE_LABELS[engine] || engine) + '</div>';
  }
  const chosen = engineChoice[name] || names[0];
  return '<div class="engine-label">Start with</div>' +
    '<div class="engine-picker">' + names.map(e =>
    '<span class="pill' + (e === chosen ? ' active' : '') + '" onclick="pickEngine(' +
    "'" + name + "','" + e + "'" + ')">' + esc(ENGINE_LABELS[e]) + '</span>').join('') + '</div>';
}
function codeRow(name, codeOn, codeUrl) {
  // VS Code (code-server) is independent of the engine switch — spawnable
  // (and stoppable) whether or not an engine session is running.
  return '<div class="vscode-row">' +
    '<span class="pill code-pill' + (codeOn ? ' active' : '') + '" onclick="toggleCode(' +
    "'" + name + "'," + (codeOn ? 'true' : 'false') + ')">' +
    (codeOn ? 'VS Code: on' : 'VS Code: start') + '</span>' +
    (codeOn && codeUrl ? '<a href="' + codeUrl + '" target="_blank">open</a>' : '') +
    '</div>';
}
// A poll-triggered sync's skip states get one small suffix on the row's
// existing .sub text (docs/spec.md "Not surfaced as a 'notification'..."
// — an informational addition to text that's already there, not a new
// badge/icon system). "synced", absent, or in-flight states add nothing.
function gitSyncSuffix(gitSync) {
  if (!gitSync || !gitSync.state) return '';
  if (gitSync.state === 'skipped-dirty') return ' · sync skipped: local changes';
  if (gitSync.state === 'skipped-diverged') return ' · sync skipped: local commits ahead';
  return '';
}
// Manual, one-click, human-confirmed deploy dispatch (backlog item 2c, part
// 2b — docs/spec.md/docs/design.md). Rendered only when deploy is present
// (a deploy-map.json entry exists for this project) — no "disabled" state,
// the row/button/message just don't exist at all otherwise. Reused-message
// slot (.deploy-msg) is always rendered empty here; doDeploy() fills it in
// after the POST resolves, and it's gone again on the next refresh() (no
// persisted history — docs/spec.md non-goals).
function deployRow(name, deploy) {
  if (!deploy) return '';
  return '<div class="deploy-row"><button class="deploy-btn" onclick="doDeploy(' +
    "'" + name + "'" + ')">Deploy</button></div>' +
    '<div class="deploy-msg" id="deploy-msg-' + esc(name) + '"></div>';
}
function row(label, on, url, kind, name, desc, engine, codeOn, codeUrl, subOverride, showBadge, gitSync, deploy) {
  // subOverride lets a singleton-toggle row (Taiga/Gitea — see refresh())
  // supply its own starting/running/stopped/error text instead of this
  // generic on/off computation — every other kind (inst/host/code) omits it
  // and keeps the plain behavior unchanged. showBadge + SINGLETON_TOGGLE_CONFIG
  // (keyed by kind) supply that row's own resource-cost badge text/class.
  const sub = (subOverride != null ? subOverride :
    (on ? (url ? 'running — <a href="' + url + '" target="_blank">open</a>' : 'running') : 'stopped')) +
    gitSyncSuffix(gitSync);
  const cfg = SINGLETON_TOGGLE_CONFIG[kind];
  const arg = name ? "'" + kind + "','" + name + "'" : "'" + kind + "',null";
  return '<div class="row"><div><div class="label">' + esc(label) + '</div>' +
    (kind === 'inst' ? engineRow(name, on, engine) : '') +
    (showBadge && cfg ? '<div class="badge ' + cfg.badgeClass + '">' + cfg.badgeText + '</div>' : '') +
    (desc ? '<div class="desc">' + esc(desc) + '</div>' : '') +
    '<div class="sub">' + sub + '</div>' +
    (kind === 'inst' ? codeRow(name, codeOn, codeUrl) : '') +
    (kind === 'inst' ? deployRow(name, deploy) : '') +
    '</div>' +
    '<label class="switch"><input type="checkbox" ' + (on ? 'checked' : '') +
    ' onchange="toggle(' + arg + ', this.checked, this)"><span class="slider"></span></label></div>';
}
let pendingToggle = null;  // {kind, name, on, checkboxEl} — only set while the code overlay is up

function actionPath(kind, name, on) {
  if (kind === 'host') return '/host/' + (on ? 'on' : 'off');
  if (kind in singletonToggleState) return '/' + kind + '/' + (on ? 'on' : 'off');
  if (kind === 'code') return '/instance/' + encodeURIComponent(name) + '/code/' + (on ? 'on' : 'off');
  if (kind === 'newproject') return '/projects/new';
  if (kind === 'deploy') return '/instance/' + encodeURIComponent(name) + '/deploy';
  return '/instance/' + encodeURIComponent(name) + '/' + (on ? 'on' : 'off');
}
function actionBody(kind, name, on, code) {
  const body = {};
  if (code) body.code = code;
  if (on && kind === 'inst') body.engine = engineChoice[name] || Object.keys(ENGINE_LABELS)[0];
  if (kind === 'newproject') body.name = name;
  return body;
}
async function performAction(kind, name, on, code) {
  return fetch(actionPath(kind, name, on), {method: 'POST', headers: {'Content-Type': 'application/json'},
                                            body: JSON.stringify(actionBody(kind, name, on, code))});
}
// TOTP is verified once per session server-side (see session_totp_ok in
// app.py), not on every action — so every action is attempted optimistically
// without a code first. A 428 means "this session hasn't cleared the TOTP
// check yet", which is when the code overlay actually shows up; a 403 only
// happens after that, if the code the user just typed was wrong. Every
// action after the first successful one in a session goes straight through
// with no overlay at all.
async function handleActionResult(r, ctx) {
  const {kind, name, on, checkboxEl} = ctx;
  if (r.status === 401) {
    hideCodeOverlay();
    if (checkboxEl) checkboxEl.checked = !on;
    if (kind in singletonToggleState) {
      singletonToggleState[kind].pending = null;
      singletonToggleState[kind].wasRunning = false;
    }
    showOverlay();
    return;
  }
  if (r.status === 428) {
    pendingToggle = ctx;
    document.getElementById('code-overlay-label').textContent =
      kind === 'deploy' ? 'Deploying: ' + (name || 'this') :
      (on ? 'Turning on: ' : 'Turning off: ') + (name || 'this');
    document.getElementById('action-code').value = '';
    document.getElementById('err-code').textContent = '';
    document.getElementById('code-overlay').classList.add('show');
    document.getElementById('action-code').focus();
    return;
  }
  if (r.status === 403) {
    document.getElementById('err-code').textContent = 'Wrong code — try again.';
    return;
  }
  if (r.status === 400) {
    const err = await r.json().catch(() => ({}));
    hideCodeOverlay();
    if (checkboxEl) checkboxEl.checked = !on;
    document.getElementById('new-project-err').textContent = err.error || 'Could not create project.';
    return;
  }
  if (kind === 'deploy') {
    // Its own inline result slot (docs/design.md States 4-8) — never the
    // generic setTimeout(refresh, 1500) below, since that would wipe the
    // message almost immediately instead of letting it "persist until next
    // refresh()" per spec.
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('deploy-msg-' + name);
    if (msgEl) {
      msgEl.textContent = r.ok ? 'Deployed successfully' : 'Deploy failed: ' + (data.message || 'unknown error');
      msgEl.className = 'deploy-msg ' + (r.ok ? 'success' : 'error');
    }
    return;
  }
  if (kind === 'newproject') document.getElementById('new-project-name').value = '';
  hideCodeOverlay();
  setTimeout(refresh, 1500);
}
async function toggle(kind, name, on, checkboxEl) {
  if (kind in singletonToggleState) {
    const st = singletonToggleState[kind];
    // Optimistic, ahead of the POST resolving (docs/design.md "Starting
    // state is optimistic + poll-driven") — refresh() picks this up on its
    // very next call, whether that's the setTimeout(refresh, 1500) below or
    // the regular 4s poll.
    if (on) { st.pending = {startTime: Date.now()}; }
    else {
      st.pending = null;
      st.wasRunning = false;
      // Held until every dispatched off POST resolves — see
      // offPendingCount's declaration comment for why this has to survive
      // both concurrent polls and a second, overlapping off dispatch.
      st.offPendingCount++;
    }
  }
  const ctx = {kind, name, on, checkboxEl};
  try {
    const r = await performAction(kind, name, on, null);
    handleActionResult(r, ctx);
  } finally {
    // A network-level failure (performAction's fetch() rejects, not just a
    // non-2xx status) must still release this — otherwise the counter leaks
    // permanently and silently disables the "unexpected stop while running"
    // detection for the rest of the page's life.
    if (kind in singletonToggleState && !on) {
      singletonToggleState[kind].offPendingCount = Math.max(0, singletonToggleState[kind].offPendingCount - 1);
    }
  }
}
function toggleCode(name, currentlyOn) {
  toggle('code', name, !currentlyOn, null);
}
// Manual dispatch (backlog item 2c, part 2b — docs/design.md "Confirmation
// via native confirm() dialog"): a deliberate, lightweight confirmation
// step before anything is sent, then a thin wrapper around the same
// toggle()/performAction()/handleActionResult() machinery every other
// action already uses — including the shared TOTP code-overlay retry path
// (a 428 mid-flow reuses that existing overlay, not a new one).
function doDeploy(name) {
  const deploy = DEPLOY_TARGETS[name];
  if (!deploy) return;
  if (!confirm('Deploy latest ' + name + ' to ' + deploy.host + ' and restart ' + deploy.service + '?')) {
    return;
  }
  const msgEl = document.getElementById('deploy-msg-' + name);
  if (msgEl) { msgEl.textContent = 'Deploying…'; msgEl.className = 'deploy-msg'; }
  toggle('deploy', name, true, null);
}
function startNewProject() {
  const name = document.getElementById('new-project-name').value.trim();
  document.getElementById('new-project-err').textContent = '';
  if (!name) {
    document.getElementById('new-project-err').textContent = 'Enter a project name.';
    return;
  }
  toggle('newproject', name, true, null);
}
function hideCodeOverlay() {
  document.getElementById('code-overlay').classList.remove('show');
  pendingToggle = null;
  wizardAwaitingCode = false;
  wizardConfirmAwaitingCode = false;
}
function cancelActionCode() {
  // Neither of the upload wizard's own TOTP retries (phase 1's startUpload,
  // phase 2's runConfirm) touch any checkbox — just close the overlay and
  // let the user retry from wherever the wizard already is.
  if (wizardAwaitingCode || wizardConfirmAwaitingCode) {
    hideCodeOverlay();
    return;
  }
  // The checkbox already flipped visually the instant it was clicked (that's
  // how the change event works) — revert it since nothing actually happened
  // (the server returns 428, before touching anything, when a code is due).
  if (pendingToggle && pendingToggle.checkboxEl) {
    pendingToggle.checkboxEl.checked = !pendingToggle.on;
  }
  if (pendingToggle && pendingToggle.kind in singletonToggleState) {
    // Nothing actually started server-side — undo the optimistic marker
    // toggle() set before the code overlay ever showed up.
    singletonToggleState[pendingToggle.kind].pending = null;
    singletonToggleState[pendingToggle.kind].wasRunning = false;
  }
  hideCodeOverlay();
}
async function submitActionCode() {
  // The upload wizard's phase-1 request carries its code via ?code= on a
  // raw XHR, not through performAction's JSON-body path (see
  // docs/spec.md's phase-1 deviation) — reusing this same code overlay and
  // its Enter-to-submit wiring, just routed differently on submit. Phase 2
  // (confirm) uses the standard JSON-body code field like every other
  // action, so it's routed to runConfirm(code) instead.
  if (wizardConfirmAwaitingCode) {
    const code = document.getElementById('action-code').value;
    runConfirm(code);
    return;
  }
  if (wizardAwaitingCode) {
    const code = document.getElementById('action-code').value;
    startUpload(code);
    return;
  }
  if (!pendingToggle) return;
  const {kind, name, on} = pendingToggle;
  const code = document.getElementById('action-code').value;
  if (kind in singletonToggleState && !on) {
    // This retry (after a 428 asked for a TOTP code) is the request that
    // actually triggers <kind>_run("down") server-side — the first attempt
    // never touched anything. Re-arm the same intentional-off guard toggle()
    // uses, since polls may have run (and correctly set wasRunning back to
    // true) during however long the user took to type the code.
    singletonToggleState[kind].wasRunning = false;
    singletonToggleState[kind].offPendingCount++;
  }
  try {
    const r = await performAction(kind, name, on, code);
    handleActionResult(r, pendingToggle);
  } finally {
    // See toggle()'s matching comment: must release on a network-level
    // failure too, not just after a resolved response.
    if (kind in singletonToggleState && !on) {
      singletonToggleState[kind].offPendingCount = Math.max(0, singletonToggleState[kind].offPendingCount - 1);
    }
  }
}
document.getElementById('action-code').addEventListener('keydown', e => { if (e.key === 'Enter') submitActionCode(); });

// ─── folder upload → auto-detect repo(s): client-side zip writer + wizard ──
// See docs/spec.md "Folder upload → auto-detect repo(s)" and docs/design.md.
// All six wizard steps (Pick/Exclude/Zip/Upload/Review/Confirm) are wired
// end to end here: steps 1-4 drive POST /projects/upload (phase 1 —
// detect only), steps 5-6 drive POST /projects/upload/confirm (phase 2 —
// register).

// === ZIP WRITER START (store mode, no compression — see docs/spec.md "Client-side zip writer") ===
const ZIP_CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) {
    crc = (ZIP_CRC_TABLE[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8)) >>> 0;
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

function zipDosDateTime(date) {
  const year = Math.max(0, date.getFullYear() - 1980);
  const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
  const dosDate = (year << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return {dosTime, dosDate};
}

function makeZipByteWriter() {
  const chunks = [];
  let offset = 0;
  return {
    push(u8) { chunks.push(u8); offset += u8.length; },
    get offset() { return offset; },
    concat() {
      const out = new Uint8Array(offset);
      let pos = 0;
      for (const c of chunks) { out.set(c, pos); pos += c.length; }
      return out;
    },
  };
}

// entries: [{path: 'relative/path.txt', file: File}, ...] (forward slashes,
// no leading slash). onProgress(filesDone, filesTotal, bytesDone, bytesTotal)
// is called once per file, after that file's bytes are written into the
// archive buffer — used to drive step 3's progress bar.
async function buildZipStore(entries, onProgress) {
  const encoder = new TextEncoder();
  const writer = makeZipByteWriter();
  const central = [];
  const bytesTotal = entries.reduce((sum, e) => sum + e.file.size, 0);
  let bytesDone = 0;

  for (let i = 0; i < entries.length; i++) {
    const {path, file} = entries[i];
    const nameBytes = encoder.encode(path);
    const buf = new Uint8Array(await file.arrayBuffer());
    const crc = crc32(buf);
    const {dosTime, dosDate} = zipDosDateTime(new Date(file.lastModified || Date.now()));
    const localOffset = writer.offset;

    const header = new DataView(new ArrayBuffer(30));
    header.setUint32(0, 0x04034b50, true);
    header.setUint16(4, 20, true);       // version needed to extract
    header.setUint16(6, 0x0800, true);   // general purpose flag: UTF-8 filename (bit 11)
    header.setUint16(8, 0, true);        // compression method: 0 = stored
    header.setUint16(10, dosTime, true);
    header.setUint16(12, dosDate, true);
    header.setUint32(14, crc, true);
    header.setUint32(18, buf.length, true);  // compressed size == uncompressed (store mode)
    header.setUint32(22, buf.length, true);  // uncompressed size
    header.setUint16(26, nameBytes.length, true);
    header.setUint16(28, 0, true);       // extra field length

    writer.push(new Uint8Array(header.buffer));
    writer.push(nameBytes);
    writer.push(buf);

    central.push({nameBytes, crc, size: buf.length, offset: localOffset, dosTime, dosDate});

    bytesDone += buf.length;
    if (onProgress) onProgress(i + 1, entries.length, bytesDone, bytesTotal);
  }

  const centralStart = writer.offset;
  for (const c of central) {
    const header = new DataView(new ArrayBuffer(46));
    header.setUint32(0, 0x02014b50, true);
    header.setUint16(4, 20, true);       // version made by
    header.setUint16(6, 20, true);       // version needed to extract
    header.setUint16(8, 0x0800, true);   // UTF-8 filename flag
    header.setUint16(10, 0, true);       // compression method: stored
    header.setUint16(12, c.dosTime, true);
    header.setUint16(14, c.dosDate, true);
    header.setUint32(16, c.crc, true);
    header.setUint32(20, c.size, true);
    header.setUint32(24, c.size, true);
    header.setUint16(28, c.nameBytes.length, true);
    header.setUint16(30, 0, true);       // extra field length
    header.setUint16(32, 0, true);       // file comment length
    header.setUint16(34, 0, true);       // disk number start
    header.setUint16(36, 0, true);       // internal file attributes
    header.setUint32(38, 0, true);       // external file attributes
    header.setUint32(42, c.offset, true);

    writer.push(new Uint8Array(header.buffer));
    writer.push(c.nameBytes);
  }
  const centralSize = writer.offset - centralStart;

  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(4, 0, true);
  eocd.setUint16(6, 0, true);
  eocd.setUint16(8, central.length, true);
  eocd.setUint16(10, central.length, true);
  eocd.setUint32(12, centralSize, true);
  eocd.setUint32(16, centralStart, true);
  eocd.setUint16(20, 0, true);
  writer.push(new Uint8Array(eocd.buffer));

  return writer.concat();
}
// === ZIP WRITER END ===

// Known-heavy-directory exclusion list (docs/spec.md "Known-heavy-directory
// exclusion") — hardcoded here, not a switchboard.env knob. .git is never
// offered, enforced by simply never being in this list.
const HEAVY_DIR_NAMES = ['node_modules', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache',
  'target', 'dist', 'build', 'vendor', '.tox', '.next', '.nuxt', '.gradle', 'Pods', '.cache'];

// Mirrors the server's UPLOAD_MAX_BYTES default — used only for the
// client-side pre-flight warning (a nicety, not a hard requirement; the
// server enforces its own configured cap regardless of what this constant
// says). A custom-configured UPLOAD_MAX_BYTES on the server won't be
// reflected here without editing this file — see docs/implementation.md.
const CLIENT_UPLOAD_MAX_BYTES = 104857600;

const WEBKITDIRECTORY_SUPPORTED = 'webkitdirectory' in document.createElement('input');
const WIZARD_STEP_LABELS = ['Pick', 'Exclude', 'Zip', 'Upload', 'Review', 'Confirm'];

let wizardState = null;
let wizardAwaitingCode = false;
let wizardConfirmAwaitingCode = false;

function resetWizardState() {
  wizardState = {
    step: 1,
    files: [],           // File[] from webkitdirectory picking (folder case)
    zipFile: null,        // File, when a .zip was picked directly
    exclusionGroups: [],  // [{name, folderCount, fileCount, size, files, excluded}]
    zipBytes: null,       // Uint8Array built by buildZipStore
    zipError: null,       // 'no-files' | 'too-large' | null
    zipProgress: {done: 0, total: 0},
    uploadProgress: {loaded: 0, total: 0},
    detectResult: null,
    error: '',
    // Review step (5) state — see initReviewState().
    mode: 'single',        // 'single' | 'split'
    splitCandidates: [],   // detectResult.nested_git_paths or .top_level_subdirs, fixed per detectResult
    splitSelected: [],     // booleans, indexed the same as splitCandidates
    // Confirm step (6) state — see runConfirm().
    confirmMode: 'single',
    confirmSelected: [],
    confirmStatus: null,    // null | 'pending' | 'success' | 'error'
    confirmRegistered: [],
    confirmSkipped: 0,
    confirmErrorMsg: '',
  };
}

function openUploadWizard() {
  resetWizardState();
  document.getElementById('upload-overlay').classList.add('show');
  renderWizard();
}
function closeUploadWizard() {
  document.getElementById('upload-overlay').classList.remove('show');
  wizardAwaitingCode = false;
  wizardConfirmAwaitingCode = false;
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.getElementById('upload-overlay').classList.contains('show')) {
    closeUploadWizard();
  }
});

function formatBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

function getIncludedFiles() {
  const excluded = new Set();
  for (const g of wizardState.exclusionGroups) {
    if (g.excluded) for (const f of g.files) excluded.add(f);
  }
  return wizardState.files.filter(f => !excluded.has(f));
}

// Groups matched files by heavy-directory basename across every depth (one
// checklist row per name, e.g. "node_modules" whether it appears once or at
// ten different depths) — see docs/spec.md "Known-heavy-directory exclusion".
function computeExclusionGroups(files) {
  const groups = {};
  for (const f of files) {
    const relPath = (f.webkitRelativePath || f.name).replace(/\\\\/g, '/');
    const parts = relPath.split('/');
    for (let i = 1; i < parts.length - 1; i++) {
      const dirName = parts[i];
      if (dirName === '.git') continue; // never offered as excludable, no exceptions
      if (HEAVY_DIR_NAMES.includes(dirName)) {
        if (!groups[dirName]) groups[dirName] = {name: dirName, dirPaths: new Set(), files: [], size: 0};
        groups[dirName].dirPaths.add(parts.slice(0, i + 1).join('/'));
        groups[dirName].files.push(f);
        groups[dirName].size += f.size;
        break; // stop at the first (shallowest) heavy-dir match for this file
      }
    }
  }
  return Object.keys(groups).sort().map(name => {
    const g = groups[name];
    return {name: g.name, folderCount: g.dirPaths.size, fileCount: g.files.length,
           size: g.size, files: g.files, excluded: true};
  });
}
function toggleExclusionGroup(i, checked) {
  wizardState.exclusionGroups[i].excluded = checked;
}

function initWizardInputs() {
  document.getElementById('wizard-folder-input').addEventListener('change', onWizardFolderPicked);
  document.getElementById('wizard-zip-input').addEventListener('change', onWizardZipPicked);
}

function onWizardFolderPicked(e) {
  const fileList = Array.from(e.target.files || []);
  e.target.value = '';
  if (fileList.length === 0) return;
  wizardState.files = fileList;
  wizardState.exclusionGroups = computeExclusionGroups(fileList);
  if (wizardState.exclusionGroups.length === 0) {
    // Nothing matched the heavy-directory list — skip straight to zipping.
    enterStep3();
  } else {
    wizardState.step = 2;
    renderWizard();
  }
}
function onWizardZipPicked(e) {
  const files = e.target.files;
  e.target.value = '';
  if (!files || files.length === 0) return;
  wizardState.zipFile = files[0];
  wizardState.step = 4;
  renderWizard();
  startUpload();
}

function enterStep3() {
  const included = getIncludedFiles();
  wizardState.step = 3;
  if (included.length === 0) {
    wizardState.zipError = 'no-files';
    renderWizard();
    return;
  }
  const totalSize = included.reduce((s, f) => s + f.size, 0);
  if (totalSize > CLIENT_UPLOAD_MAX_BYTES) {
    wizardState.zipError = 'too-large';
    renderWizard();
    return;
  }
  wizardState.zipError = null;
  renderWizard();
  runZipping(included);
}

async function runZipping(included) {
  const entries = included.map(f => ({path: (f.webkitRelativePath || f.name).replace(/\\\\/g, '/'), file: f}));
  wizardState.zipProgress = {done: 0, total: entries.length};
  let bytes;
  try {
    bytes = await buildZipStore(entries, (done, total) => {
      wizardState.zipProgress = {done, total};
      updateZipProgressUI();
    });
  } catch (ex) {
    wizardState.error = 'Failed to build zip: ' + (ex && ex.message ? ex.message : ex);
    renderWizard();
    return;
  }
  wizardState.zipBytes = bytes;
  wizardState.uploadProgress = {loaded: 0, total: bytes.length};
  wizardState.step = 4;
  renderWizard();
  startUpload();
}

function startUpload(code) {
  wizardState.error = '';
  const blob = wizardState.zipFile ? wizardState.zipFile : new Blob([wizardState.zipBytes], {type: 'application/zip'});
  wizardState.uploadProgress = {loaded: 0, total: blob.size};
  renderWizard();
  const xhr = new XMLHttpRequest();
  // TOTP for this one endpoint is carried via ?code=, not a JSON body — see
  // docs/spec.md's phase-1 deviation (Content-Type here is the raw zip
  // bytes, so there's no JSON body to put a code field in).
  let url = '/projects/upload';
  if (code) url += '?code=' + encodeURIComponent(code);
  xhr.open('POST', url);
  xhr.setRequestHeader('Content-Type', 'application/zip');
  xhr.upload.onprogress = function(e) {
    if (e.lengthComputable) {
      wizardState.uploadProgress = {loaded: e.loaded, total: e.total};
      updateUploadProgressUI();
    }
  };
  xhr.onload = function() {
    if (xhr.status === 401) {
      wizardState.error = 'Session expired — refresh the page and sign in again.';
      renderWizard();
      return;
    }
    if (xhr.status === 428) {
      showWizardCodeOverlay();
      return;
    }
    if (xhr.status === 403) {
      showWizardCodeError('Wrong code — try again.');
      return;
    }
    let payload = {};
    try { payload = JSON.parse(xhr.responseText); } catch (ex) {}
    if (xhr.status === 200) {
      hideCodeOverlay();
      wizardState.detectResult = payload;
      initReviewState();
      setTimeout(function() { wizardState.step = 5; renderWizard(); }, 250);
      return;
    }
    wizardState.error = describeUploadError(xhr.status, payload);
    renderWizard();
  };
  xhr.onerror = function() {
    wizardState.error = 'Connection lost — your upload is still in progress on the server. ' +
      'Refresh the page to check status, or start over.';
    renderWizard();
  };
  xhr.send(blob);
}

function describeUploadError(status, err) {
  if (status === 413) return 'Uploaded file is too large. Go back and exclude more directories.';
  if (status === 400) return 'Upload failed: ' + (err.error || 'invalid upload.');
  return 'Upload failed (' + status + '): ' + (err.error || 'unknown error.');
}

function showWizardCodeOverlay() {
  wizardAwaitingCode = true;
  document.getElementById('code-overlay-label').textContent = 'Confirm this upload.';
  document.getElementById('action-code').value = '';
  document.getElementById('err-code').textContent = '';
  document.getElementById('code-overlay').classList.add('show');
  document.getElementById('action-code').focus();
}
function showWizardCodeError(msg) {
  document.getElementById('err-code').textContent = msg;
}

function updateZipProgressUI() {
  const p = wizardState.zipProgress;
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  const fill = document.getElementById('wizard-zip-fill');
  const label = document.getElementById('wizard-zip-label');
  if (fill) fill.style.width = pct + '%';
  if (label) label.textContent = pct + '% (' + p.done + ' of ' + p.total + ' files)';
}
function updateUploadProgressUI() {
  const p = wizardState.uploadProgress;
  const pct = p.total ? Math.round((p.loaded / p.total) * 100) : 0;
  const fill = document.getElementById('wizard-upload-fill');
  const label = document.getElementById('wizard-upload-label');
  if (fill) fill.style.width = pct + '%';
  if (label) label.textContent = pct + '% (' + formatBytes(p.loaded) + ' of ' + formatBytes(p.total) + ')';
}

function renderStepIndicator() {
  return WIZARD_STEP_LABELS.map((label, i) => {
    const n = i + 1;
    let cls = 'wizard-step';
    if (n < wizardState.step) cls += ' done';
    else if (n === wizardState.step) cls += ' active';
    else cls += ' disabled';
    return '<span class="' + cls + '">' + n + '. ' + esc(label) + '</span>';
  }).join('');
}

function clickWizardFolderInput() { document.getElementById('wizard-folder-input').click(); }
function clickWizardZipInput() { document.getElementById('wizard-zip-input').click(); }

function renderStep1() {
  let html = '<div class="wizard-pick-row">';
  if (WEBKITDIRECTORY_SUPPORTED) {
    html += '<button onclick="clickWizardFolderInput()">Pick a folder&hellip;</button>';
  } else {
    html += '<p>This browser does not support picking a whole folder here — pick a .zip instead.</p>';
  }
  html += '<div class="wizard-or">&ndash; or &ndash;</div>';
  html += '<button onclick="clickWizardZipInput()">Pick a .zip&hellip;</button>';
  html += '<p>Picking a .zip skips client-side zipping and uploads it as-is.</p>';
  html += '</div>';
  return html;
}

function renderStep2() {
  let html = '<p>Directories to exclude from the zip (checked = excluded):</p>';
  wizardState.exclusionGroups.forEach((g, i) => {
    html += '<label class="wizard-check-row">' +
      '<input type="checkbox" ' + (g.excluded ? 'checked' : '') +
      ' onchange="toggleExclusionGroup(' + i + ', this.checked)">' +
      '<span class="info"><div>' + esc(g.name) + '</div>' +
      '<div class="sub">' + g.folderCount + ' folder' + (g.folderCount === 1 ? '' : 's') + ', ' +
      g.fileCount + ' file' + (g.fileCount === 1 ? '' : 's') + ', ~' + formatBytes(g.size) +
      '</div></span></label>';
  });
  return html;
}
function renderStep2Actions() {
  return '<button class="secondary" onclick="wizardState.step = 1; renderWizard();">&lsaquo; Back</button>' +
         '<button class="primary" onclick="enterStep3()">Next &rsaquo;</button>';
}

function renderStep3() {
  if (wizardState.zipError === 'no-files') {
    return '<p class="err">No files to upload after exclusions.</p>';
  }
  if (wizardState.zipError === 'too-large') {
    const total = getIncludedFiles().reduce((s, f) => s + f.size, 0);
    return '<div class="wizard-warn">Total size (' + formatBytes(total) + ') exceeds the ' +
      formatBytes(CLIENT_UPLOAD_MAX_BYTES) + ' upload limit. Remove more directories to proceed.</div>';
  }
  const p = wizardState.zipProgress;
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  return '<p>Building archive&hellip;</p>' +
    '<div class="wizard-progress-bg"><div class="wizard-progress-fill zip" id="wizard-zip-fill" ' +
    'style="width:' + pct + '%"></div></div>' +
    '<div class="wizard-progress-label" id="wizard-zip-label">' + pct + '% (' + p.done + ' of ' + p.total + ' files)</div>';
}
function renderStep3Actions() {
  if (wizardState.zipError) {
    return '<button class="secondary" onclick="wizardState.step = 2; wizardState.zipError = null; renderWizard();">' +
      '&lsaquo; Back to exclude</button>';
  }
  return '';
}

function renderStep4() {
  const p = wizardState.uploadProgress;
  const pct = p.total ? Math.round((p.loaded / p.total) * 100) : 0;
  return '<p>Uploading archive&hellip;</p>' +
    '<div class="wizard-progress-bg"><div class="wizard-progress-fill upload" id="wizard-upload-fill" ' +
    'style="width:' + pct + '%"></div></div>' +
    '<div class="wizard-progress-label" id="wizard-upload-label">' + pct + '% (' +
    formatBytes(p.loaded) + ' of ' + formatBytes(p.total) + ')</div>';
}

// ─── Step 5: Review — docs/design.md "Step 5: Review" ──────────────────────
// Shows phase 1's detected structure and lets the user choose single-vs-
// split (see docs/spec.md "Detection and the two-phase protocol"). Called
// once, right after a successful phase-1 upload (see startUpload's
// xhr.onload 200 branch above) — NOT re-derived on every render, since the
// user's checkbox choices need to persist across re-renders of this step.
function initReviewState() {
  const d = wizardState.detectResult;
  wizardState.mode = 'single';
  wizardState.splitCandidates = d.root_has_git ? d.nested_git_paths : d.top_level_subdirs;
  // Defaults per docs/spec.md "Detection and the two-phase protocol":
  // monorepo nested paths default UNCHECKED (safer — most nested .git dirs
  // are vendored content nobody meant to surface); no-root-.git subfolders
  // default CHECKED (matches the old auto-register-every-subfolder default).
  const defaultChecked = !d.root_has_git;
  wizardState.splitSelected = wizardState.splitCandidates.map(() => defaultChecked);
}
function setWizardMode(mode) { wizardState.mode = mode; renderWizard(); }
// Indexed by position in wizardState.splitCandidates, never by the
// candidate's own path string — those path strings come straight out of an
// untrusted uploaded zip's own entry names, so they're never interpolated
// into an inline onXXX="..." HTML attribute (same defensive pattern
// toggleExclusionGroup(i, ...) above already uses for the same reason).
function toggleSplitPath(i, checked) { wizardState.splitSelected[i] = checked; }

function renderStep5() {
  const d = wizardState.detectResult;
  let html = '<p><strong>Detected structure:</strong></p>';
  html += '<p>&#128193; ' + esc(d.root_name) + ' (root)<br>' +
    (d.root_has_git ? 'has .git' : 'no .git') + '</p>';
  if (d.root_has_git) {
    html += '<p>' + (d.nested_git_paths.length
      ? d.nested_git_paths.length + ' nested repo' + (d.nested_git_paths.length === 1 ? '' : 's') + ' inside'
      : 'no nested repos detected') + '</p>';
  } else {
    html += '<p>' + d.top_level_subdirs.length + ' subfolder' +
      (d.top_level_subdirs.length === 1 ? '' : 's') +
      (d.loose_top_level_files
        ? ', ' + d.loose_top_level_files + ' loose top-level file' + (d.loose_top_level_files === 1 ? '' : 's')
        : '') + '</p>';
  }

  if (!d.ambiguous) {
    html += '<p>&#10003; One project to register: "' + esc(d.root_name) + '"</p>';
    return html;
  }

  html += '<fieldset style="border:none;padding:0;margin:10px 0;">' +
    '<legend style="font-size:13px;color:#aaa;padding:0 0 4px;">How would you like to register it?</legend>';
  html += '<label class="wizard-check-row pill-choice"><input type="radio" name="wizard-mode" ' +
    (wizardState.mode === 'single' ? 'checked' : '') + ' onchange="setWizardMode(\\'single\\')">' +
    '<span class="info">Single project (keep all together as "' + esc(d.root_name) + '")</span></label>';
  const splitLabel = d.root_has_git ? 'Split out nested repos:' : 'Each subfolder as its own project:';
  html += '<label class="wizard-check-row pill-choice"><input type="radio" name="wizard-mode" ' +
    (wizardState.mode === 'split' ? 'checked' : '') + ' onchange="setWizardMode(\\'split\\')">' +
    '<span class="info">' + esc(splitLabel) + '</span></label>';
  html += '</fieldset>';

  if (wizardState.mode === 'split') {
    wizardState.splitCandidates.forEach((p, i) => {
      html += '<label class="wizard-check-row"><input type="checkbox" ' +
        (wizardState.splitSelected[i] ? 'checked' : '') +
        ' onchange="toggleSplitPath(' + i + ', this.checked)">' +
        '<span class="info">' + esc(p) + '</span></label>';
    });
    if (d.root_has_git) {
      html += '<div class="wizard-warn">Splitting creates duplicate copies of selected folders ' +
        'on disk. Choose carefully.</div>';
    }
  }
  return html;
}
function renderStep5Actions(d) {
  let html = '';
  if (d.ambiguous) {
    html += '<button class="secondary" onclick="resetWizardState(); renderWizard();">&lsaquo; Back</button>';
  }
  html += '<button class="primary" onclick="proceedToConfirm()">Confirm &rsaquo;</button>';
  return html;
}

function proceedToConfirm() {
  const d = wizardState.detectResult;
  wizardState.error = '';
  let mode = 'single', selected = [];
  if (d.ambiguous && wizardState.mode === 'split') {
    mode = 'split';
    selected = wizardState.splitCandidates.filter((p, i) => wizardState.splitSelected[i]);
    if (!d.root_has_git && selected.length === 0) {
      wizardState.error = 'Select at least one folder to register.';
      renderWizard();
      return;
    }
  }
  wizardState.confirmMode = mode;
  wizardState.confirmSelected = selected;
  wizardState.confirmStatus = 'pending';
  wizardState.confirmRegistered = [];
  wizardState.confirmSkipped = 0;
  wizardState.confirmErrorMsg = '';
  wizardState.step = 6;
  renderWizard();
  runConfirm();
}

// ─── Step 6: Confirm — docs/design.md "Step 6: Confirm" ────────────────────
// POST /projects/upload/confirm — an ordinary JSON body like every other
// mutating endpoint (docs/spec.md "Wire format and endpoints": phase 2 has
// no ?code= deviation, unlike phase 1), so its own TOTP retry goes through
// the standard code overlay too, just gated on wizardConfirmAwaitingCode
// instead of wizardAwaitingCode (see submitActionCode/cancelActionCode
// above).
function showWizardConfirmCodeOverlay() {
  wizardConfirmAwaitingCode = true;
  document.getElementById('code-overlay-label').textContent = 'Confirm registering these project(s).';
  document.getElementById('action-code').value = '';
  document.getElementById('err-code').textContent = '';
  document.getElementById('code-overlay').classList.add('show');
  document.getElementById('action-code').focus();
}

async function runConfirm(code) {
  wizardState.confirmStatus = 'pending';
  renderWizard();
  const body = {token: wizardState.detectResult.token, mode: wizardState.confirmMode,
                selected: wizardState.confirmSelected};
  if (code) body.code = code;
  let r;
  try {
    r = await fetch('/projects/upload/confirm', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  } catch (ex) {
    wizardState.confirmStatus = 'error';
    wizardState.confirmErrorMsg = 'Connection lost — try again.';
    renderWizard();
    return;
  }
  if (r.status === 401) {
    wizardState.confirmStatus = 'error';
    wizardState.confirmErrorMsg = 'Session expired — refresh the page and sign in again.';
    renderWizard();
    return;
  }
  if (r.status === 428) {
    showWizardConfirmCodeOverlay();
    return;
  }
  if (r.status === 403) {
    showWizardCodeError('Wrong code — try again.');
    return;
  }
  const payload = await r.json().catch(() => ({}));
  hideCodeOverlay();
  if (r.ok) {
    wizardState.confirmStatus = 'success';
    wizardState.confirmRegistered = payload.registered || [];
    wizardState.confirmSkipped = payload.skipped || 0;
    renderWizard();
    setTimeout(refresh, 1500);
    return;
  }
  wizardState.confirmStatus = 'error';
  wizardState.confirmErrorMsg = payload.error || 'Registration failed.';
  wizardState.confirmRegistered = payload.registered || [];
  renderWizard();
}

function renderStep6() {
  if (wizardState.confirmStatus === 'success') {
    let html = '<p style="color:#34c759">&#10003; Success!</p><p>Registered projects:</p><ul>';
    wizardState.confirmRegistered.forEach(n => { html += '<li>' + esc(n) + '</li>'; });
    html += '</ul>';
    if (wizardState.confirmSkipped) {
      html += '<p class="sub">(' + wizardState.confirmSkipped + ' skipped as unselected)</p>';
    }
    html += '<p>They&rsquo;ll show up in the dashboard shortly.</p>';
    return html;
  }
  if (wizardState.confirmStatus === 'error') {
    let html = '<p class="err" style="color:#ff6b6b">&#10007; Registration failed</p>' +
      '<p class="err">Error: ' + esc(wizardState.confirmErrorMsg) + '</p>';
    if (wizardState.confirmRegistered.length) {
      html += '<p class="sub">Already registered before the failure: ' +
        wizardState.confirmRegistered.map(esc).join(', ') + '</p>';
    }
    return html;
  }
  return '<p>Registering projects&hellip;</p>';
}
function renderStep6Actions() {
  if (wizardState.confirmStatus === 'success') {
    return '<button class="primary" onclick="closeUploadWizard(); refresh();">Done, close wizard</button>';
  }
  if (wizardState.confirmStatus === 'error') {
    return '<button class="secondary" onclick="wizardState.step = 5; wizardState.confirmStatus = null; ' +
      'renderWizard();">&lsaquo; Back to review</button>' +
      '<button class="primary" onclick="resetWizardState(); renderWizard();">Start over</button>';
  }
  return '';
}

function renderWizard() {
  document.getElementById('wizard-steps').innerHTML = renderStepIndicator();
  document.getElementById('wizard-err').textContent = wizardState.error || '';
  let body = '', actions = '';
  if (wizardState.step === 1) { body = renderStep1(); }
  else if (wizardState.step === 2) { body = renderStep2(); actions = renderStep2Actions(); }
  else if (wizardState.step === 3) { body = renderStep3(); actions = renderStep3Actions(); }
  else if (wizardState.step === 4) { body = renderStep4(); }
  else if (wizardState.step === 5) { body = renderStep5(); actions = renderStep5Actions(wizardState.detectResult); }
  else if (wizardState.step === 6) { body = renderStep6(); actions = renderStep6Actions(); }
  document.getElementById('wizard-body').innerHTML = body;
  document.getElementById('wizard-actions').innerHTML = actions;
}

initWizardInputs();

refresh(); setInterval(refresh, 4000);
</script>
</body></html>"""


def render_page() -> str:
    if AUTH_MODE == "pve":
        title, hint, user_hint = "Proxmox login", (
            "Your PVE credentials — the authenticator code is asked for separately, "
            "only when you actually flip a switch."), "root"
    else:
        title, hint, user_hint = "Sign in", (
            "The authenticator code is asked for separately, only when you actually "
            "flip a switch."), ""
    return (PAGE_TEMPLATE
            .replace("__LOGIN_TITLE__", title)
            .replace("__LOGIN_HINT__", hint)
            .replace("__LOGIN_USER_HINT__", user_hint))


class Handler(BaseHTTPRequestHandler):
    def _session_id(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return part[len("session="):]
        return None

    def _authed(self):
        sid = self._session_id()
        return sid is not None and session_valid(sid)

    def _json(self, obj, code=200, extra_headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body_str, code=200):
        body = body_str.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _handle_upload(self, query: str):
        """
        Phase 1 of the folder-upload wizard (POST /projects/upload — see
        docs/spec.md "Wire format and endpoints"). Called from do_POST's
        early branch, BEFORE the shared _read_json_body() call, since that
        call reads exactly Content-Length bytes and json.loads()s them —
        run unmodified against a raw zip body it would silently consume and
        discard those bytes before this handler ever saw them. TOTP is
        checked here too (via ?code=, not the JSON body — the one
        deliberate deviation from every other mutating endpoint), staging
        does consume real server resources. Stages + detects structure
        only; registers nothing under PROJECTS_DIR — that's phase 2
        (POST /projects/upload/confirm), a later build cycle.
        """
        sid = self._session_id()
        if sid is None or not session_valid(sid):
            return self._json({"error": "not authenticated"}, 401)
        if not session_totp_ok(sid):
            code = urllib.parse.parse_qs(query).get("code", [""])[0]
            if not code:
                return self._json({"error": "totp_required"}, 428)
            if not totp_verify(TOTP_SECRET, code):
                return self._json({"error": "invalid or missing 2FA code"}, 403)
            mark_session_totp_ok(sid)

        # Size limit check 1 of 2 (docs/spec.md "Size limits"): reject
        # before reading any of the body at all if Content-Length is
        # missing, zero, or oversized. No chunked-transfer support.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > UPLOAD_MAX_BYTES:
            return self._json(
                {"error": "missing, zero, or oversized Content-Length"}, 413)

        raw = self.rfile.read(length)
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return self._json({"error": "not a valid zip file"}, 400)

        infolist = zf.infolist()
        if not infolist:
            return self._json({"error": "empty zip file"}, 400)
        if len(infolist) > UPLOAD_MAX_ENTRIES:
            return self._json({"error": "too many entries in zip file"}, 400)

        # Size limit check 2 of 2: the uncompressed total, before extracting
        # anything — catches a zip-bomb-shaped mismatch (small compressed
        # upload, huge decompressed size). Can't happen for a client-built
        # (store-mode) zip, but still matters for the pick-a-pre-made-.zip
        # fallback path.
        total_uncompressed = sum(i.file_size for i in infolist)
        if total_uncompressed > UPLOAD_MAX_BYTES:
            return self._json(
                {"error": "uncompressed contents exceed the size limit"}, 413)

        token = secrets.token_hex(16)
        staging_subdir = os.path.join(UPLOAD_STAGING_DIR, token)
        try:
            _extract_zip_safely(zf, staging_subdir)
        except UploadRejected as e:
            shutil.rmtree(staging_subdir, ignore_errors=True)
            return self._json({"error": str(e)}, 400)

        effective_root = _unwrap_single_wrapper_folder(staging_subdir)
        detection = detect_structure(effective_root)
        detection["token"] = token
        self._json(detection)

    def do_GET(self):
        # The page itself is a static shell (no session data in it) — the login
        # overlay and the dashboard rows are both populated client-side, gated
        # on whether /status comes back 401. Nothing sensitive is served here
        # without a valid session.
        if self.path == "/":
            return self._html(render_page())
        if not self._authed():
            return self._json({"error": "not authenticated"}, 401)
        if self.path == "/status":
            _reap_dead_state()
            engines = load_engines()
            host_on, host_url = False, None
            if HOST_CONTROL_ENABLED:
                out = host_run("status").splitlines()
                host_on = bool(out) and out[0] == "on"
                host_url = out[1] if host_on and len(out) > 1 else None
            taiga_on, taiga_url = False, None
            if TAIGA_ENABLED:
                out = taiga_run("status").splitlines()
                taiga_on = bool(out) and out[0] == "on"
                taiga_url = _taiga_display_url() if taiga_on else None
            gitea_on, gitea_url = False, None
            if GITEA_ENABLED:
                out = gitea_run("status").splitlines()
                gitea_on = bool(out) and out[0] == "on"
                gitea_url = _gitea_display_url() if gitea_on else None
            # Opportunistic work on this already-frequent request, same
            # precedent _reap_dead_state() itself established -- internally
            # throttled to its own GITEA_POLL_INTERVAL_SECONDS interval, so
            # this is a cheap no-op on every tick that isn't due yet (see
            # docs/spec.md "The poll mechanism").
            _gitea_poll_if_due(gitea_on)
            # Reverse-indexed by name, small N -- same "just iterate it"
            # style instance_names() itself already uses -- to attach an
            # optional gitea_sync field to each row below, when present.
            gitea_sync_by_name = {e.get("name"): e for e in _load_gitea_repo_map().values()}
            # Read once per /status call, same reasoning as _load_deploy_map's
            # own docstring: the file is tiny, hand-edited rarely, and this
            # avoids any staleness question after an operator edits it (no
            # caching -- docs/spec.md "Loading").
            deploy_map = _load_deploy_map()
            instances = []
            for n in instance_names():
                engine = active_engine(n)
                e = engines.get(engine) if engine else None
                url = (_session_urls.get(n) if (e and e.url_regex) else
                       _ttyd_urls.get(n) if engine else None)
                inst = {"name": n, "on": engine is not None, "engine": engine,
                       "url": url,
                       "desc": get_description(n, os.path.join(PROJECTS_DIR, n)),
                       "code_on": code_running(n), "code_url": _code_urls.get(n)}
                sync_entry = gitea_sync_by_name.get(n)
                if sync_entry is not None:
                    inst["gitea_sync"] = {"state": sync_entry.get("sync_state"),
                                          "at": sync_entry.get("sync_at")}
                deploy_entry = deploy_map.get(n)
                if deploy_entry is not None:
                    # "key" (and port/user) deliberately excluded -- the
                    # private key path never needs to reach the client, and
                    # port/user aren't needed for the confirm-dialog text
                    # (docs/spec.md "/status response addition").
                    inst["deploy"] = {"host": deploy_entry["host"],
                                      "deploy_path": deploy_entry["deploy_path"],
                                      "service": deploy_entry["service"]}
                instances.append(inst)
            self._json({"instances": instances,
                       "engines": {name: e.label for name, e in engines.items()},
                       "host_enabled": HOST_CONTROL_ENABLED, "host_label": HOST_LABEL,
                       "host": host_on, "host_url": host_url,
                       "taiga_enabled": TAIGA_ENABLED, "taiga_label": TAIGA_LABEL,
                       "taiga": taiga_on, "taiga_url": taiga_url,
                       "gitea_enabled": GITEA_ENABLED, "gitea_label": GITEA_LABEL,
                       "gitea": gitea_on, "gitea_url": gitea_url})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/login":
            body = self._read_json_body()
            if not do_login(body.get("username", ""), body.get("password", "")):
                return self._json({"error": "invalid credentials"}, 401)
            sid = new_session()
            cookie = f"session={sid}; HttpOnly; Secure; SameSite=Lax; Max-Age={SESSION_TTL}; Path=/"
            return self._json({"ok": True}, extra_headers={"Set-Cookie": cookie})

        # Raw-bytes upload route needs its own early branch, before the
        # shared _read_json_body() call below — see _handle_upload's
        # docstring / docs/spec.md "Background" for why. Routed on
        # split.path (not self.path) since this is the one POST route that
        # carries a query string (?code=).
        split = urllib.parse.urlsplit(self.path)
        if split.path == "/projects/upload":
            return self._handle_upload(split.query)

        sid = self._session_id()
        if sid is None or not session_valid(sid):
            return self._json({"error": "not authenticated"}, 401)

        body = self._read_json_body()
        # TOTP is verified once per session (see session_totp_ok) rather than
        # on every mutating action. Not yet verified this session and no code
        # given: 428 tells the frontend "prompt for a code", distinct from
        # 403 ("a code was given and it was wrong") so the UI only shows an
        # error for an actually-wrong code, not for the expected first ask.
        if not session_totp_ok(sid):
            code = body.get("code", "")
            if not code:
                return self._json({"error": "totp_required"}, 428)
            if not totp_verify(TOTP_SECRET, code):
                return self._json({"error": "invalid or missing 2FA code"}, 403)
            mark_session_totp_ok(sid)

        parts = [unquote(p) for p in self.path.strip("/").split("/")]
        if parts[0] == "host" and len(parts) == 2 and parts[1] in ("on", "off"):
            if not HOST_CONTROL_ENABLED:
                return self._json({"error": "host control disabled"}, 404)
            host_run("start" if parts[1] == "on" else "stop")
            self._json({"ok": True})
        elif parts[0] == "taiga" and len(parts) == 2 and parts[1] in ("on", "off"):
            if not TAIGA_ENABLED:
                return self._json({"error": "taiga disabled"}, 404)
            if parts[1] == "on":
                taiga_run("up")
                _publish(TAIGA_URL_PATH, TAIGA_PORT)
            else:
                _unpublish(TAIGA_URL_PATH)
                taiga_run("down")
            self._json({"ok": True})
        elif parts[0] == "gitea" and len(parts) == 2 and parts[1] in ("on", "off"):
            if not GITEA_ENABLED:
                return self._json({"error": "gitea disabled"}, 404)
            if parts[1] == "on":
                gitea_run("up")
                _publish(GITEA_URL_PATH, GITEA_PORT)
            else:
                _unpublish(GITEA_URL_PATH)
                gitea_run("down")
            self._json({"ok": True})
        elif parts[0] == "projects" and len(parts) == 2 and parts[1] == "new":
            ok, err = create_project((body.get("name") or "").strip())
            if not ok:
                return self._json({"error": err}, 400)
            self._json({"ok": True})
        elif parts[0] == "projects" and len(parts) == 3 and parts[1] == "upload" and parts[2] == "confirm":
            # Phase 2 of the upload wizard — ordinary JSON body, code (if
            # needed) already handled by the shared TOTP gate above, unlike
            # phase 1's ?code= deviation (docs/spec.md "Wire format and
            # endpoints").
            status, payload = confirm_upload(
                body.get("token", ""), body.get("mode", ""), body.get("selected") or [])
            self._json(payload, status)
        elif parts[0] == "instance" and len(parts) == 3 and parts[2] in ("on", "off"):
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown instance"}, 404)
            if parts[2] == "on":
                engines = load_engines()
                default_engine = next(iter(engines), "claude")
                engine = body.get("engine") if body.get("engine") in engines else default_engine
                instance_start(name, engine)
            else:
                instance_stop(name)
            self._json({"ok": True})
        elif parts[0] == "instance" and len(parts) == 4 and parts[2] == "code" and parts[3] in ("on", "off"):
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown instance"}, 404)
            if parts[3] == "on":
                _code_start(name, os.path.join(PROJECTS_DIR, name))
            else:
                _code_stop(name)
            self._json({"ok": True})
        elif parts[0] == "instance" and len(parts) == 3 and parts[2] == "deploy":
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown instance"}, 404)
            status, msg = deploy_run(name)
            self._json({"ok": status == 200, "message": msg}, status)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    os.makedirs(UPLOAD_STAGING_DIR, exist_ok=True)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.serve_forever()
