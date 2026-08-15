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
import ipaddress
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

# GitHub REST API client (backlog item 17 part 1, docs/spec.md) -- unlike
# GITEA_API_TOKEN above, GitHub isn't a service this switchboard runs, so
# there's no bootstrap script: GITHUB_TOKEN is a PAT the operator creates
# directly on github.com and pastes into switchboard.env, same
# documented-but-never-shipped-a-value treatment as SIMPLE_PASSWORD/
# TOTP_SECRET. Gated purely on GITHUB_TOKEN being set -- no separate
# GITHUB_ENABLED toggle (host detection itself needs no token and is always
# available; see docs/spec.md "Non-goals").
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_BASE = "https://api.github.com"        # fixed -- GitHub, unlike
                                                    # self-hosted Gitea, has
                                                    # no configurable port/host
GITHUB_API_TIMEOUT_SECONDS = 15                    # matches _gitea_api's own
                                                    # hardcoded timeout
GITHUB_RATE_LIMIT_FALLBACK_SECONDS = 60            # conservative default
                                                    # cooldown when a 403/429
                                                    # carries neither
                                                    # Retry-After nor a
                                                    # parseable
                                                    # X-RateLimit-Reset

# Clone-from-URL (backlog item 16, docs/spec.md) -- unlike NEW_PROJECT_FROM_
# GITEA_SCRIPT above, installed UNCONDITIONALLY (base install.sh block, like
# NEW_PROJECT_FROM_UPLOAD_SCRIPT) -- cloning an arbitrary external repo URL
# never depends on --with-git-hosting.
NEW_PROJECT_FROM_URL_SCRIPT = os.environ.get(
    "NEW_PROJECT_FROM_URL_SCRIPT",
    "/usr/local/bin/ai-dev-switchboard-new-project-from-url.sh")
# Generous relative to the 30s/60s timeouts create_project()/confirm_upload()
# use for their own privileged scripts -- an arbitrary external repo's
# history can legitimately take a while to transfer, unlike a Gitea repo
# this switchboard just created itself (2b) or a local cp -a (3).
CLONE_TIMEOUT_SECONDS = int(os.environ.get("CLONE_TIMEOUT_SECONDS", "180"))

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

# AI merge-request reviewer (backlog item 8, docs/spec.md; made host-agnostic
# by item 17 part 2, docs/spec.md) -- rides both _gitea_poll_if_due() (Gitea)
# and _github_poll_if_due() (GitHub, below) as its two dispatch triggers:
# watches each registered project's open PRs for a configurable label, and on
# the label-add edge, runs a roster model (item 6's roster, item 6b's
# read-only grounding digest) against the PR diff and posts the review back
# as a single PR comment. Standalone/poll-triggered, not a lead-loop tool -- a
# PR can be tagged with no team session running at all. Off by default
# (AI_REVIEWER_ENABLED gates both hosts identically). Every Gitea repo this
# switchboard's own GITEA_REPO_MAP_FILE registers is automatically in scope
# once enabled; a GitHub-origin project additionally needs its owner/repo
# listed in AI_REVIEWER_GITHUB_REPOS_FILE below -- GitHub-origin repos can be
# arbitrary third-party infrastructure the operator doesn't fully control
# (item 16's clone-by-URL), a materially different trust boundary from
# Gitea's fully-operator-owned repos (docs/spec.md "Settled scope decisions").
AI_REVIEWER_ENABLED = os.environ.get("AI_REVIEWER_ENABLED", "0") == "1"
AI_REVIEWER_LABEL = os.environ.get("AI_REVIEWER_LABEL", "ready for review")
# "kind:name" (e.g. "ollama:qwen3:8b" or "engine:claude") -- split on the
# FIRST ':' only below, since an Ollama tag can itself contain ':'.
# Validated against a live teams.roster() lookup at trigger time, not at
# startup (same "engines.d is meant to be edited without a restart"
# philosophy roster() itself documents) -- unset or naming a roster entry
# that no longer exists is a per-repo, per-poll-pass no-op, logged via the
# state file's last_error, never fatal.
AI_REVIEWER_MODEL = os.environ.get("AI_REVIEWER_MODEL", "")
AI_REVIEWER_MAX_DIFF_BYTES = int(os.environ.get("AI_REVIEWER_MAX_DIFF_BYTES", "40000"))
AI_REVIEWER_MAX_ATTEMPTS = int(os.environ.get("AI_REVIEWER_MAX_ATTEMPTS", "3"))
AI_REVIEWER_STATE_FILE = os.environ.get(
    "AI_REVIEWER_STATE_FILE", "/var/lib/ai-dev-switchboard/ai-reviewer-state.json")
# Hand-edited, operator-maintained allowlist of "owner/repo" strings gating
# which GitHub-origin projects _github_poll_if_due() will ever poll/review at
# all (item 17 part 2, docs/spec.md "Settled scope decisions" #3) -- same
# /etc/ai-dev-switchboard/ placement and "app.py only ever reads it, never
# writes it" contract DEPLOY_MAP_FILE already established. Never authored by
# install.sh or any UI (matches DEPLOY_MAP_FILE's own precedent) -- hand-edit
# it yourself. See _load_ai_reviewer_github_repos().
AI_REVIEWER_GITHUB_REPOS_FILE = os.environ.get(
    "AI_REVIEWER_GITHUB_REPOS_FILE",
    "/etc/ai-dev-switchboard/ai-reviewer-github-repos.json")
# How often _github_poll_if_due() itself runs (seconds) -- independent of
# GITEA_POLL_INTERVAL_SECONDS above (different domain, different tuning
# rationale: GitHub's 5,000 req/hour token-wide rate limit argues for a
# materially more conservative interval than Gitea's loopback-cheap default).
GITHUB_POLL_INTERVAL_SECONDS = int(os.environ.get("GITHUB_POLL_INTERVAL_SECONDS", "120"))

# HTTP-level smoke check (backlog item 18, docs/spec.md) -- a manual,
# per-project "Smoke check" button that makes a single in-process GET
# against that project's own already-captured _session_urls entry. Bounded
# by construction: a request timeout and a capped response-body read, same
# "never trust an outbound call to behave" discipline AI_REVIEWER_MAX_DIFF_
# BYTES already established for a different bounded read.
SMOKE_CHECK_TIMEOUT_SECONDS = int(os.environ.get("SMOKE_CHECK_TIMEOUT_SECONDS", "10"))
SMOKE_CHECK_MAX_BODY_BYTES = int(os.environ.get("SMOKE_CHECK_MAX_BODY_BYTES", "65536"))

# Team session lifecycle, part 2a (backlog item 6d, docs/spec.md §5) --
# throttles _team_reap_if_due()'s own opportunistic sweep_dead_teams() call
# inside _reap_dead_state(), same "not a background thread/timer, throttled
# on every already-frequent /status poll" idiom GITEA_POLL_INTERVAL_SECONDS
# above already establishes. Deliberately independent of that constant --
# different domain, different tuning rationale. Does NOT throttle
# teams.latest_run_for_project()'s own per-project /status lookup -- that
# stays unthrottled (cheap, no subprocess calls, and freshness matters
# there); see docs/spec.md "Proposed approach" §5 for why only the sweep
# itself needs throttling.
TEAM_REAP_POLL_INTERVAL_SECONDS = int(os.environ.get("TEAM_REAP_POLL_INTERVAL_SECONDS", "60"))

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

# Engine-name prefixes _parse_engine_file() reserves outright (see its own
# comment at the point of use) -- "switchboard" (backlog item 6a) and "team"
# (backlog item 6d part 1), each guarding against the identical session-name
# collision shape for its own subsystem.
_RESERVED_ENGINE_NAME_PREFIXES = ("switchboard", "team")


class Engine:
    __slots__ = ("name", "label", "cmd", "url_regex", "startup",
                 "headless_cmd", "headless_format", "headless_prompt", "headless_resume",
                 "headless_lead_format", "headless_schema_flag")

    def __init__(self, name, label, cmd, url_regex, startup,
                 headless_cmd=None, headless_format=None, headless_prompt=None,
                 headless_resume=None, headless_lead_format=None, headless_schema_flag=None):
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
        # Lead-adapter tier hints (backlog item 6c, docs/spec.md "Engine-file
        # extension") -- both optional, additive, None-default, and parsed
        # independently of the headless_enabled nullification above (an
        # engine without them is simply tier-3 by auto-detection, same as
        # "an engine without them is teammate-ineligible" for the four
        # HEADLESS_* keys, here it's "doesn't appear at tier 2" instead).
        # HEADLESS_LEAD_FORMAT has no fixed enum to validate against here --
        # _lead_tier_for_engine() (app/teams.py) only ever compares it
        # against the literal strings "schema"/"prose"; any other value
        # simply falls through to auto-detection, same effect as leaving it
        # unset, so there is nothing to reject at parse time.
        self.headless_lead_format = headless_lead_format
        self.headless_schema_flag = headless_schema_flag

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
    # Reserved engine-name prefixes (docs/spec.md "Session naming", extended
    # by backlog item 6d part 1 "Engine-name reservation"): headless tmux
    # sessions are named f"switchboard-headless-{run_id}" (app/teams.py) via
    # the *same* TMUX rule instance_start() uses, and active_engine() keys
    # purely off f"{engine_name}-{project_name}" with no other cross-check.
    # Reserving only the exact name "switchboard-headless" would still leave
    # a constructible collision open (engine "switchboard" + a project
    # directory literally named "headless-<run_id>"), so the *whole*
    # "switchboard" prefix is reserved. Same bug class, same fix shape for
    # "team": a team-<project> tmux session (app/teams.py, backlog item 6d)
    # is structurally identical to a single-engine session name for any
    # engine literally named "team" (or "team-anything") against ANY
    # project -- f"{engine}-{project}" == f"team-{project}". Any .engine
    # file whose derived name starts with either reserved prefix is ignored,
    # same "intentionally inert" treatment .engine.example templates already
    # get below.
    if name.startswith(_RESERVED_ENGINE_NAME_PREFIXES):
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
    # Lead-adapter hints (backlog item 6c) -- read/defaulted independently of
    # the headless_enabled nullification above; see Engine.__init__'s own
    # docstring comment for why no enum check applies here.
    headless_lead_format = kv.get("HEADLESS_LEAD_FORMAT") or None
    headless_schema_flag = kv.get("HEADLESS_SCHEMA_FLAG") or None
    return Engine(name, kv.get("LABEL", name), cmd, kv.get("URL_REGEX") or None, startup,
                  headless_cmd, headless_format, headless_prompt, headless_resume,
                  headless_lead_format, headless_schema_flag)


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

# Clone-from-URL allowlist (backlog item 16, docs/spec.md "URL validation --
# allowlist, not denylist"). Only http(s)://, ssh://, or git's own scp-like
# user@host:path shorthand are accepted -- file://, git://, ext::/fd::
# transport helpers (a known git RCE shape when an attacker controls the
# clone URL), a bare/relative local filesystem path, and a string with no
# recognizable scheme at all are all rejected before any subprocess is ever
# spawned.
#
# This allowlist is also what has to block git's own known argument-injection
# shape (a "URL" whose host or scp-path component is actually a
# `-oProxyCommand=...`-style flag, CVE-2017-1000117). A first pass at that
# used negative lookaheads pinned right after the scheme/`@` (`(?!-)`); a
# review found that insufficient -- those lookaheads only ever check the
# character *immediately* following a fixed anchor, but both accepted
# grammars allow an optional `user@` (for `scheme://`) or `:path` (for the
# scp-like shorthand) segment between that anchor and the component that
# actually matters to ssh/git, so a URL like `ssh://user@-oProxyCommand=.../x`
# or `user@host:-oProxyCommand=...` slipped straight past the lookahead and
# reached a real `git clone` subprocess, protected only by installed git's
# own (version-dependent) downstream hardening -- not by this codebase.
#
# The regexes below are now only a coarse "does this look like the right
# grammar at all" pre-filter. The actual security-relevant check parses out
# the real host (and, for scp-like shorthand, the real path) component and
# validates THAT specific substring via _clone_url_host_is_safe() /
# `path[0] != "-"` below -- see clone_project_from_url()'s docs/spec.md
# history and docs/test-review.md's item 16 re-review for the two concrete
# bypasses (`ssh://user@-oProxyCommand=...` and
# `user@host:-oProxyCommand=...`) this replaced the lookahead approach to
# close.
CLONE_URL_MAX_LEN = 2048
_CLONE_URL_SCHEME_RE = re.compile(r"^(https?|ssh)://\S+$", re.IGNORECASE)
_CLONE_URL_SCP_RE = re.compile(r"^[A-Za-z0-9_.-]+@\S+:\S.*$")

# Host-validation charset for the non-IPv6 case: must start AND end with an
# alphanumeric character (never '-', the shape ssh/git can mistake for the
# start of an option flag) and contain only characters a real hostname or
# IPv4 literal can use in between.
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _clone_url_host_is_safe(host) -> bool:
    """True only for a syntactically legitimate hostname, IPv4 literal, or
    IPv6 literal -- never for an empty/None host, never for anything
    starting with '-', and never for a stray '@'/':' smuggled in from a
    malformed or adversarial URL. See the module comment above
    _CLONE_URL_SCHEME_RE for why this replaced the prior
    per-character-after-a-fixed-anchor lookahead approach."""
    if not host:
        return False
    if ":" in host:
        # Only ever legitimate for an IPv6 literal (a bracketed
        # ssh://user@[::1]:22/path host, urlsplit() already strips the
        # brackets into plain "::1"). ipaddress.ip_address() is a strict,
        # well-tested parser for that shape (including a %<scope-id>
        # suffix) -- anything that isn't genuinely a valid IPv6 literal is
        # rejected outright rather than falling through to the plain
        # hostname charset below, which would wrongly accept a ':'-laden
        # injection attempt.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return True
    return bool(_SAFE_HOST_RE.match(host))


def _validate_clone_url(url: str) -> str:
    """Returns an error message if url is rejected, None if it passes. See
    module-level comment above _CLONE_URL_SCHEME_RE for the reasoning."""
    err = ("unsupported URL — use http://, https://, ssh://, or "
           "user@host:path (git's own shorthand)")
    if not url or not isinstance(url, str):
        return "a URL is required"
    if len(url) > CLONE_URL_MAX_LEN:
        return f"URL is too long (max {CLONE_URL_MAX_LEN} characters)"
    if any(ord(c) < 0x20 or c == "\x7f" for c in url):
        return "URL contains control characters"

    if _CLONE_URL_SCHEME_RE.match(url):
        # urlsplit() (not hand-rolled slicing) isolates the real host --
        # correctly ignoring an optional user@ prefix, a bracketed IPv6
        # literal, and a :port suffix -- exactly the component ssh/git
        # itself treats as a connection target.
        try:
            host = urllib.parse.urlsplit(url).hostname
        except ValueError:
            return err
        return None if _clone_url_host_is_safe(host) else err

    if _CLONE_URL_SCP_RE.match(url):
        # git's scp-like shorthand has no scheme for urlsplit() to parse --
        # isolate host/path ourselves: split on the first '@' (the regex
        # above already anchored the user to a safe charset with no '@' or
        # ':' in it, so the first '@' is unambiguous), then the first ':'
        # after that splits the real host from the real path. Both
        # components matter here (unlike the scheme case): host is ssh's
        # connection target, and path is what git hands to the remote
        # git-upload-pack invocation -- a leading '-' on either is the
        # injection shape.
        _user, _, rest = url.partition("@")
        host, _, path = rest.partition(":")
        if _clone_url_host_is_safe(host) and path and path[0] != "-":
            return None
        return err

    return err


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


def _gitea_api_raw(method: str, path: str) -> tuple:
    """Like _gitea_api() but returns (status, text) without attempting
    json.loads on the body -- needed for Gitea's `.diff` endpoint (backlog
    item 8), which returns plain diff text, not JSON. _gitea_api() itself
    would misclassify a non-JSON 2xx body as ConnectionError, since its own
    `except (URLError, TimeoutError, ValueError)` also catches json.loads's
    ValueError. Same "raise ConnectionError only on a real transport
    failure, never on a non-2xx status" contract as _gitea_api()."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{GITEA_PORT}/api/v1{path}", method=method,
        headers={"Authorization": f"token {GITEA_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError):
        raise ConnectionError("gitea unreachable")


# ─── external-origin detection + GitHub REST client (backlog item 17 part 1,
# docs/spec.md) ─────────────────────────────────────────────────────────────
# Detects, per project, whether its `origin` remote is this switchboard's
# own local Gitea, github.com, or something else -- unprivileged, on demand,
# no new sudoers entry (SVC_USER already has ambient read access under
# PROJECTS_DIR, same basis teams.load_grounding() already relies on). Plus a
# GitHub REST API client mirroring _gitea_api/_gitea_api_raw's exact
# contract. Nothing here is wired into a poll loop, a route, or item 8's
# Gitea-only reviewer yet -- see docs/spec.md "Non-goals" (that's item 17
# part 2).

def _project_origin_url(name: str) -> str | None:
    """Unprivileged `git remote get-url origin` against
    PROJECTS_DIR/<name>. Returns None (never raises) for: not a git repo,
    no `origin` remote configured, or any subprocess/timeout failure -- all
    three are ordinary, expected states (a local-only `git init` project, an
    upload-wizard project with no remote at all), not errors."""
    try:
        r = subprocess.run(
            ["git", "-C", os.path.join(PROJECTS_DIR, name), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    return url or None


def _classify_origin_url(url: str) -> dict:
    """Never raises. Returns {"kind": "local"|"github"|"external"|"none",
    "owner": str|None, "repo": str|None}.

    "none" -- no url at all (empty string). "local" -- origin's host parses
    as a loopback IP (ipaddress.ip_address(host).is_loopback) -- covers
    every origin this switchboard itself has ever generated (always
    literally 127.0.0.1, see scripts/new-project-from-gitea.sh) and is
    robust to a bracketed ::1 too, without hardcoding the string
    "127.0.0.1". "github" -- host case-insensitively equals "github.com",
    with owner/repo parsed from the path (both scheme:// and
    user@host:path forms) and a trailing ".git" stripped. Anything else
    (unparseable, or a real but non-github, non-loopback host) is
    "external" with owner/repo left None -- no client exists for it in
    this part.

    Parsing detail: try urllib.parse.urlsplit(url).hostname first (handles
    https://github.com/owner/repo.git, ssh://git@github.com/owner/repo.git,
    bracketed IPv6 loopback); if that yields no host (e.g. git's
    scp-shorthand, which has no scheme for urlsplit to parse), fall back to
    a plain user@host:path split. This is a read-only classification of an
    already-existing origin, not a security-validation path like item 16's
    _validate_clone_url() -- item 16's injection-safety regexes are
    deliberately not reused here (see docs/spec.md "Background")."""
    if not url:
        return {"kind": "none", "owner": None, "repo": None}
    try:
        host = None
        path = ""
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            parts = None
        if parts is not None and parts.hostname:
            host = parts.hostname
            path = parts.path or ""
        else:
            _user, sep, rest = url.partition("@")
            if sep:
                h, csep, p = rest.partition(":")
                if h and csep and p:
                    host, path = h, p
        if not host:
            return {"kind": "external", "owner": None, "repo": None}
        try:
            if ipaddress.ip_address(host).is_loopback:
                return {"kind": "local", "owner": None, "repo": None}
        except ValueError:
            pass
        if host.lower() == "github.com":
            segments = [s for s in path.strip("/").split("/") if s]
            owner = segments[0] if len(segments) >= 1 else None
            repo = segments[1] if len(segments) >= 2 else None
            if repo and repo.endswith(".git"):
                repo = repo[:-4]
            return {"kind": "github", "owner": owner, "repo": repo}
        return {"kind": "external", "owner": None, "repo": None}
    except Exception:
        # Classification must never crash a caller over a malformed origin
        # some unrelated process created.
        return {"kind": "external", "owner": None, "repo": None}


def detect_project_origin(name: str) -> dict:
    """Public entry point -- composes _project_origin_url()/
    _classify_origin_url() (docs/spec.md "Detection mechanism")."""
    return _classify_origin_url(_project_origin_url(name) or "")


_github_rate_limit_lock = threading.Lock()
_github_rate_limited_until = 0.0


def _github_rate_limited() -> bool:
    with _github_rate_limit_lock:
        return time.time() < _github_rate_limited_until


def _github_note_rate_limit(headers, status: int) -> None:
    """Called after every real GitHub HTTP response (success or
    HTTPError). Sets _github_rate_limited_until (never lowers an existing,
    still-active cooldown) when:
    - status in (403, 429) and a Retry-After header is present -> now +
      int(Retry-After) seconds (the most authoritative signal GitHub
      gives; a malformed/non-numeric value falls back to
      GITHUB_RATE_LIMIT_FALLBACK_SECONDS rather than being ignored).
    - status in (403, 429) and X-RateLimit-Remaining == "0" (no
      Retry-After) -> X-RateLimit-Reset epoch seconds, if present and
      parses as an int; else now + GITHUB_RATE_LIMIT_FALLBACK_SECONDS as a
      conservative default rather than not backing off at all.
    - Otherwise (a normal 2xx/4xx with remaining quota, or a 403/429 with
      neither signal) -> no-op. Never raises."""
    global _github_rate_limited_until
    if status not in (403, 429):
        return
    now = time.time()
    headers = headers or {}
    retry_after = headers.get("Retry-After")
    if retry_after is not None:
        try:
            until = now + int(retry_after)
        except (TypeError, ValueError):
            until = now + GITHUB_RATE_LIMIT_FALLBACK_SECONDS
    else:
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining != "0":
            return
        reset = headers.get("X-RateLimit-Reset")
        try:
            until = float(int(reset))
        except (TypeError, ValueError):
            until = now + GITHUB_RATE_LIMIT_FALLBACK_SECONDS
    with _github_rate_limit_lock:
        if until > _github_rate_limited_until:
            _github_rate_limited_until = until


def _github_request_headers(accept: str = None) -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub's API rejects requests with no User-Agent at all -- a real,
        # documented GitHub-specific requirement Gitea doesn't have.
        "User-Agent": "ai-dev-switchboard",
    }


def _github_api(method: str, path: str, body: dict = None) -> tuple:
    """Returns (status, parsed_json_or_{}). Never raises for a non-2xx HTTP
    status -- only for a real connection failure, converted to
    ConnectionError, same contract as _gitea_api(). Checks the rate-limit
    cooldown gate BEFORE building the request; if still cooling down,
    returns (429, {"error": "rate limited, retry later"}) without making an
    HTTP call at all. After any real response (success or HTTPError), calls
    _github_note_rate_limit() with the response headers + status."""
    if _github_rate_limited():
        return 429, {"error": "rate limited, retry later"}
    data = json.dumps(body).encode() if body is not None else None
    headers = _github_request_headers()
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GITHUB_API_BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT_SECONDS) as resp:
            _github_note_rate_limit(resp.headers, resp.status)
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        _github_note_rate_limit(e.headers, e.code)
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ConnectionError("github unreachable")


def _github_api_raw(method: str, path: str, accept: str = None) -> tuple:
    """Like _github_api() but returns (status, text) without attempting
    json.loads on the body -- needed for GitHub's diff Accept header
    (application/vnd.github.v3.diff), which returns plain diff text, not
    JSON. Same rate-limit-gate-then-note handling, and the same "raise
    ConnectionError only on a real transport failure, never on a non-2xx
    status" contract, as _github_api()."""
    if _github_rate_limited():
        return 429, "rate limited, retry later"
    headers = _github_request_headers(accept)
    req = urllib.request.Request(f"{GITHUB_API_BASE}{path}", method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT_SECONDS) as resp:
            _github_note_rate_limit(resp.headers, resp.status)
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        _github_note_rate_limit(e.headers, e.code)
        return e.code, (e.read() or b"").decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError):
        raise ConnectionError("github unreachable")


def _github_token_missing_error() -> dict:
    return {"ok": False, "error": "GITHUB_TOKEN isn't configured -- see switchboard.env"}


def github_list_open_prs(owner: str, repo: str) -> dict:
    """GET /repos/{owner}/{repo}/pulls?state=open. {"ok": True,
    "prs": [...]} -- each item keeps GitHub's own shape (number, title,
    body, labels: [{"name": ...}, ...]), same "don't reshape the upstream
    response" choice _gitea_api's own callers already make."""
    if not GITHUB_TOKEN:
        return _github_token_missing_error()
    try:
        status, resp = _github_api("GET", f"/repos/{owner}/{repo}/pulls?state=open")
    except ConnectionError as e:
        return {"ok": False, "error": str(e)}
    if status != 200 or not isinstance(resp, list):
        return {"ok": False, "error": f"unexpected response (status {status})"}
    return {"ok": True, "prs": resp}


def github_pr_diff(owner: str, repo: str, number: int) -> dict:
    """GET /repos/{owner}/{repo}/pulls/{number} with the diff Accept
    header (_github_api_raw). {"ok": True, "diff": <text>}."""
    if not GITHUB_TOKEN:
        return _github_token_missing_error()
    try:
        status, text = _github_api_raw(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}",
            accept="application/vnd.github.v3.diff")
    except ConnectionError as e:
        return {"ok": False, "error": str(e)}
    if status != 200:
        return {"ok": False, "error": f"diff fetch failed (status {status})"}
    return {"ok": True, "diff": text}


def github_list_branches(owner: str, repo: str) -> dict:
    """GET /repos/{owner}/{repo}/branches. {"ok": True, "branches": [...]}."""
    if not GITHUB_TOKEN:
        return _github_token_missing_error()
    try:
        status, resp = _github_api("GET", f"/repos/{owner}/{repo}/branches")
    except ConnectionError as e:
        return {"ok": False, "error": str(e)}
    if status != 200 or not isinstance(resp, list):
        return {"ok": False, "error": f"unexpected response (status {status})"}
    return {"ok": True, "branches": resp}


def github_post_pr_comment(owner: str, repo: str, number: int, body: str) -> dict:
    """POST /repos/{owner}/{repo}/issues/{number}/comments, {"body": body}
    -- GitHub, like Gitea, treats a PR's comments as issue comments. Posted
    directly and synchronously, same as _gitea_api's own POST call in
    _ai_reviewer_review_run() -- see docs/spec.md "Settled scope decision"
    for why this write needs no extra confirmation gate. {"ok": True} on a
    2xx status."""
    if not GITHUB_TOKEN:
        return _github_token_missing_error()
    try:
        status, _resp = _github_api(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body})
    except ConnectionError as e:
        return {"ok": False, "error": str(e)}
    if status // 100 != 2:
        return {"ok": False, "error": f"comment post failed (status {status})"}
    return {"ok": True}


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
            try:
                _ai_reviewer_poll_repo("gitea", owner_repo, entry)
            except Exception:
                # Same per-repo isolation discipline as _gitea_poll_one above
                # (docs/spec.md backlog item 8, "Edge cases" -- a malformed
                # response for one repo must not stop other repos' polls).
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


# ─── AI merge-request reviewer (backlog item 8; host-agnostic per item 17
# part 2) ────────────────────────────────────────────────────────────────
# See docs/spec.md "The poll extension" (item 8) and "Proposed approach" #1
# (item 17 part 2) -- rides both _gitea_poll_if_due()'s existing per-repo
# loop above (_ai_reviewer_poll_repo("gitea", ...), its own try/except at the
# call site) and _github_poll_if_due() below (_ai_reviewer_poll_repo("github",
# ...)). State file: {pr_key: {"label_present": bool, "attempts": int,
# "reviewed_at": iso|null, "last_error": str|null}} -- pr_key is
# "owner/repo#number" for Gitea (unchanged, byte-for-byte, since item 8
# shipped) or "github:owner/repo#number" for GitHub (see
# _ai_reviewer_pr_key() -- the "github:" prefix can never collide with a
# Gitea owner/repo, which can't contain a colon). Same tmp-file-then-
# os.replace() atomic-write idiom, and the same "missing/corrupt file
# tolerates to {}" idiom, as _load_gitea_repo_map()/_save_gitea_repo_map_
# entry() above.
_ai_reviewer_state_lock = threading.Lock()


def _load_ai_reviewer_state() -> dict:
    try:
        with open(AI_REVIEWER_STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_ai_reviewer_state_entry(pr_key: str, *, label_present: bool, attempts: int,
                                  reviewed_at, last_error) -> None:
    with _ai_reviewer_state_lock:
        s = _load_ai_reviewer_state()
        s[pr_key] = {"label_present": label_present, "attempts": attempts,
                    "reviewed_at": reviewed_at, "last_error": last_error}
        os.makedirs(os.path.dirname(AI_REVIEWER_STATE_FILE), exist_ok=True)
        tmp = AI_REVIEWER_STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2, sort_keys=True)
        os.replace(tmp, AI_REVIEWER_STATE_FILE)


def _ai_reviewer_record_failure(pr_key: str, message: str) -> None:
    """Re-reads the current state entry, increments attempts, records
    last_error. label_present is left True -- already set synchronously by
    the trigger edge in _ai_reviewer_poll_repo() below -- so a failed
    attempt is retried by that function's own "already present" branch,
    never re-triggered as a brand new episode (docs/spec.md "Record
    failure")."""
    prev = _load_ai_reviewer_state().get(pr_key, {})
    _save_ai_reviewer_state_entry(
        pr_key, label_present=True, attempts=prev.get("attempts", 0) + 1,
        reviewed_at=prev.get("reviewed_at"), last_error=message)


# Per-PR (owner/repo#number) non-blocking lock -- same _gitea_sync_lock_for
# idiom as sync-on-push above: a review dispatch that finds the lock already
# held (a previous attempt for this exact PR is still running) is dropped,
# not queued -- the next poll interval sees `attempts` unchanged and retries.
_ai_reviewer_pr_locks_guard = threading.Lock()
_ai_reviewer_pr_locks = {}


def _ai_reviewer_pr_lock_for(pr_key: str) -> threading.Lock:
    with _ai_reviewer_pr_locks_guard:
        lock = _ai_reviewer_pr_locks.get(pr_key)
        if lock is None:
            lock = threading.Lock()
            _ai_reviewer_pr_locks[pr_key] = lock
        return lock


def _ai_reviewer_comment_body(model_entry: dict, review_text: str, diff_truncated: bool) -> str:
    truncation_note = ""
    if diff_truncated:
        truncation_note = (
            f"\n> Note: this diff was truncated to the first {AI_REVIEWER_MAX_DIFF_BYTES} "
            "bytes before review -- some changes may not have been evaluated.\n")
    return (
        f"**AI code review** (model: `{model_entry['kind']}:{model_entry['name']}`, "
        "via ai-dev-switchboard)\n"
        f"{truncation_note}\n"
        f"{review_text}\n\n"
        "---\n"
        "_Comment-only — this review never blocks, approves, or merges this PR._\n"
    )


def _ai_reviewer_pr_key(host: str, owner_repo: str, number) -> str:
    """Gitea's key format is UNCHANGED ("owner/repo#number", no prefix) --
    backward-compatible with every already-persisted AI_REVIEWER_STATE_FILE
    entry on a live install (docs/spec.md item 17 part 2, "Non-goals": no
    change to Gitea's own state-file key format). GitHub gets a "github:"
    prefix -- a string Gitea's own owner/repo naming can never produce (no
    colon allowed), so collision with an existing Gitea key is structurally
    impossible, not just unlikely."""
    return f"{owner_repo}#{number}" if host == "gitea" else f"github:{owner_repo}#{number}"


def _ai_reviewer_review_run(host: str, owner_repo: str, entry: dict, pr: dict) -> None:
    """The real review-generation + comment-post work, off the request
    thread (see _ai_reviewer_review_bg). Never raises -- every failure path
    records it via _ai_reviewer_record_failure() and returns.

    Host-agnostic (item 17 part 2, docs/spec.md "Proposed approach" #1) --
    only the diff-fetch and comment-post calls branch per host; every other
    line (truncation, model resolution, teams.review_pr_diff(), comment-body
    construction, state persistence) is identical for "gitea"/"github"."""
    number = pr.get("number")
    pr_key = _ai_reviewer_pr_key(host, owner_repo, number)
    try:
        if host == "gitea":
            try:
                status, diff_text = _gitea_api_raw(
                    "GET", f"/repos/{owner_repo}/pulls/{number}.diff")
            except ConnectionError as e:
                _ai_reviewer_record_failure(pr_key, str(e))
                return
            if status != 200:
                # Covers a PR closed/merged between label-add-detection and
                # this background run actually running (404), and any other
                # non-2xx response -- an ordinary retried failure, not a
                # crash (docs/spec.md "Edge cases"). A genuinely EMPTY-but-200
                # diff (a PR with no net changes) is deliberately NOT treated
                # as a failure here -- docs/spec.md's own "Edge cases"
                # section settles that explicitly ("still reviewed... not
                # treated as an error"), which this reads as authoritative
                # over the more terse "non-200 or empty" phrasing in the
                # walkthrough above it.
                _ai_reviewer_record_failure(pr_key, f"diff fetch failed (status {status})")
                return
        else:  # github -- reuses part 1's own convenience function directly,
               # normalizing its {"ok": bool, ...} contract against the rest
               # of this function's status-code-based control flow.
            owner, _sep, repo = owner_repo.partition("/")
            result = github_pr_diff(owner, repo, number)
            if not result.get("ok"):
                _ai_reviewer_record_failure(pr_key, result.get("error") or "diff fetch failed")
                return
            diff_text = result["diff"]

        diff_bytes = diff_text.encode("utf-8")
        diff_truncated = len(diff_bytes) > AI_REVIEWER_MAX_DIFF_BYTES
        if diff_truncated:
            diff_text = diff_bytes[:AI_REVIEWER_MAX_DIFF_BYTES].decode("utf-8", errors="ignore")

        kind, _sep, name = AI_REVIEWER_MODEL.partition(":")
        model_entry = None
        if name:
            model_entry = next(
                (m for m in teams.roster() if m["kind"] == kind and m["name"] == name), None)
        if model_entry is None:
            _ai_reviewer_record_failure(
                pr_key, f"AI_REVIEWER_MODEL {AI_REVIEWER_MODEL!r} not found in roster")
            return

        workdir = os.path.join(PROJECTS_DIR, entry["name"])
        result = teams.review_pr_diff(
            model_entry, workdir=workdir, pr_title=pr.get("title", "") or "",
            pr_body=pr.get("body") or "", diff_text=diff_text, diff_truncated=diff_truncated)
        if not result.get("ok"):
            _ai_reviewer_record_failure(pr_key, result.get("error") or "review generation failed")
            return

        comment = _ai_reviewer_comment_body(model_entry, result.get("text", ""), diff_truncated)
        if host == "gitea":
            try:
                status, _resp = _gitea_api(
                    "POST", f"/repos/{owner_repo}/issues/{number}/comments", {"body": comment})
            except ConnectionError as e:
                _ai_reviewer_record_failure(pr_key, str(e))
                return
            if status // 100 != 2:
                _ai_reviewer_record_failure(pr_key, f"comment post failed (status {status})")
                return
        else:  # github
            owner, _sep, repo = owner_repo.partition("/")
            result = github_post_pr_comment(owner, repo, number, comment)
            if not result.get("ok"):
                _ai_reviewer_record_failure(pr_key, result.get("error") or "comment post failed")
                return

        _save_ai_reviewer_state_entry(pr_key, label_present=True, attempts=0,
                                      reviewed_at=teams._now_iso(), last_error=None)
    except Exception as e:
        # Defense in depth -- nothing above this point should be able to
        # raise, but this runs on its own background thread (not inside
        # _gitea_poll_if_due()'s/_github_poll_if_due()'s own per-repo
        # try/except), so an unanticipated exception here must still be
        # recorded rather than silently killing the thread with attempts
        # never incremented.
        _ai_reviewer_record_failure(pr_key, f"{type(e).__name__}: {e}")


def _ai_reviewer_review_bg(host: str, owner_repo: str, entry: dict, pr: dict) -> None:
    """Non-blocking dispatch -- mirrors _gitea_sync_bg() exactly. Returns
    immediately; if a previous attempt for this PR is still in flight, this
    call is simply dropped (the next poll interval retries). The per-PR lock
    is keyed by the now-host-prefixed pr_key, so a Gitea and a GitHub review
    can never contend on the same lock even if their owner/repo strings
    happened to be identical."""
    pr_key = _ai_reviewer_pr_key(host, owner_repo, pr.get("number"))
    lock = _ai_reviewer_pr_lock_for(pr_key)
    if not lock.acquire(blocking=False):
        return

    def _run():
        try:
            _ai_reviewer_review_run(host, owner_repo, entry, pr)
        finally:
            lock.release()

    threading.Thread(target=_run, daemon=True).start()


def _ai_reviewer_poll_repo(host: str, owner_repo: str, entry: dict) -> None:
    """Called from inside _gitea_poll_if_due()'s/_github_poll_if_due()'s
    per-repo loop, gated on AI_REVIEWER_ENABLED. Watches AI_REVIEWER_LABEL on
    owner_repo's open PRs and fires a review on the label-absent ->
    label-present edge (docs/spec.md "The poll extension" step 3).

    The retry branch below (label present, was already present) is gated on
    `last_error is not None`, not merely `attempts < AI_REVIEWER_MAX_
    ATTEMPTS` as docs/spec.md's own walkthrough literally states -- see
    docs/implementation.md "Deviations from spec" for why the literal
    reading would re-post a review on every single poll interval forever
    after the FIRST successful review of an episode (a successful review
    resets attempts to 0, which is always < AI_REVIEWER_MAX_ATTEMPTS),
    directly violating the spec's own acceptance criterion that a still-
    present, never-removed label must not cause a second comment post.
    """
    if not AI_REVIEWER_ENABLED:
        return
    if host == "gitea":
        status, resp = _gitea_api("GET", f"/repos/{owner_repo}/pulls?state=open")
        if status != 200 or not isinstance(resp, list):
            return
        prs = resp
    else:  # github
        if not GITHUB_TOKEN:
            return
        owner, _sep, repo = owner_repo.partition("/")
        result = github_list_open_prs(owner, repo)
        if not result.get("ok"):
            return
        prs = result["prs"]

    state = _load_ai_reviewer_state()
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if number is None:
            continue
        pr_key = _ai_reviewer_pr_key(host, owner_repo, number)
        labels = pr.get("labels") or []
        label_present = AI_REVIEWER_LABEL in [
            l.get("name") for l in labels if isinstance(l, dict)]
        prev = state.get(pr_key, {})
        was_present = prev.get("label_present")

        if not label_present:
            if was_present or pr_key not in state:
                # Arms the next add as a fresh episode -- no-op if already
                # recorded absent.
                _save_ai_reviewer_state_entry(
                    pr_key, label_present=False, attempts=0,
                    reviewed_at=prev.get("reviewed_at"), last_error=None)
            continue

        if not was_present:
            # Trigger edge -- write synchronously BEFORE any slow work so no
            # other/later poll pass can re-decide this is a fresh edge while
            # the review is in flight (closes the double-post race).
            _save_ai_reviewer_state_entry(
                pr_key, label_present=True, attempts=prev.get("attempts", 0),
                reviewed_at=prev.get("reviewed_at"), last_error=None)
            _ai_reviewer_review_bg(host, owner_repo, entry, pr)
            continue

        attempts = prev.get("attempts", 0)
        if prev.get("last_error") is not None and attempts < AI_REVIEWER_MAX_ATTEMPTS:
            _ai_reviewer_review_bg(host, owner_repo, entry, pr)
        # else: either already successfully reviewed this episode (no
        # retry needed) or the attempt budget is exhausted -- give up
        # silently until the label cycles (removed, then re-added).


# ─── GitHub poll pass (backlog item 17 part 2, docs/spec.md) ──────────────
# The GitHub-side counterpart to _gitea_poll_if_due() above, but with a
# narrower purpose: unlike Gitea (which also fast-forward-syncs the local
# checkout via _gitea_poll_one), the ONLY thing that needs periodic
# background polling for a GitHub-origin project is item 8's label-watching
# (a label being added is an event a poll has to notice, not a query an
# operator/script triggers on demand -- see docs/spec.md "Settled scope
# decisions" #1). So this poll loop's only per-repo work is calling
# _ai_reviewer_poll_repo("github", ...) for every allowlisted, GitHub-origin
# local project.
_github_poll_lock = threading.Lock()
_github_poll_last_at = 0.0


def _load_ai_reviewer_github_repos() -> set:
    """Hand-edited JSON array of "owner/repo" strings -- app.py only ever
    reads this file, same DEPLOY_MAP_FILE contract (never written by this
    module). Missing/malformed/not-a-list-of-strings -> empty set (nothing
    opted in), never raises -- same "never crash, safe-degrade" idiom every
    loader in this file already follows."""
    try:
        with open(AI_REVIEWER_GITHUB_REPOS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {x for x in data if isinstance(x, str) and x}


def _github_poll_if_due() -> None:
    """Throttled, lock-guarded poll pass -- same double-checked lock +
    timestamp shape as _gitea_poll_if_due(). No `_on`/enabled-toggle
    parameter (unlike Gitea, GitHub isn't a locally-run service with an
    on/off container state) -- gating is entirely via AI_REVIEWER_ENABLED/
    GITHUB_TOKEN/the allowlist below, all checked inside this function."""
    global _github_poll_last_at
    if not AI_REVIEWER_ENABLED or not GITHUB_TOKEN:
        return  # nothing this poll exists to do would run anyway -- see
                 # docs/spec.md "Settled scope decisions" #1: this poll has
                 # no purpose independent of item 8's label-watching.
    if time.time() - _github_poll_last_at < GITHUB_POLL_INTERVAL_SECONDS:
        return
    if not _github_poll_lock.acquire(blocking=False):
        return  # another /status request is already mid-poll-pass
    try:
        if time.time() - _github_poll_last_at < GITHUB_POLL_INTERVAL_SECONDS:
            return  # lost the race -- someone else just finished a pass
        _github_poll_last_at = time.time()
        allowed = _load_ai_reviewer_github_repos()
        if not allowed:
            return  # nothing opted in -- skip even the instance_names() walk
        for name in instance_names():
            try:
                origin = detect_project_origin(name)
            except Exception:
                continue  # same per-project isolation discipline as
                           # _gitea_poll_if_due()'s own per-repo try/except
            if origin.get("kind") != "github":
                continue
            owner, repo = origin.get("owner"), origin.get("repo")
            if not owner or not repo:
                continue
            owner_repo = f"{owner}/{repo}"
            if owner_repo not in allowed:
                continue
            try:
                _ai_reviewer_poll_repo("github", owner_repo, {"name": name})
            except Exception:
                # One malformed/unexpected response must not stop polling
                # for every other allowlisted project in this pass -- same
                # discipline _gitea_poll_if_due()'s own per-repo try/except
                # already establishes.
                pass
    finally:
        _github_poll_lock.release()


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


# Per-project non-blocking lock, identical shape to _deploy_locks/
# _deploy_lock_for above -- a concurrent second smoke check for the same
# project is dropped (the route maps this to 409), never queued.
_smoke_check_locks_guard = threading.Lock()
_smoke_check_locks = {}


def _smoke_check_lock_for(name: str) -> threading.Lock:
    with _smoke_check_locks_guard:
        lock = _smoke_check_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _smoke_check_locks[name] = lock
        return lock


def smoke_check_run(name: str, expect_contains: str) -> dict:
    """Synchronous, request-thread dispatch -- same "manually clicked
    one-shot action can and should just block the request" reasoning
    deploy_run()'s own docstring gives. HTTP-level only (backlog item 18,
    docs/spec.md): a single GET against _session_urls[name], the
    switchboard's own trusted, server-derived URL for that project (never
    an arbitrary client-supplied URL, so no SSRF-style concern). Never
    raises -- every branch below returns a dict; on/OSError-shaped failures
    from urlopen()/resp.read() are caught and turned into a clean
    {"ok": False, ...} result instead.

    Returns one of:
    - {"ok": False, "error": "no captured URL for this project"} -- no
      _session_urls entry for `name` (engine off, no url_regex, or hasn't
      printed a matching URL yet). Returned immediately, before the lock is
      even touched.
    - {"ok": False, "error": <msg>, "locked": True} -- a check for this
      project is already in flight. "locked" is an internal-only marker
      the route below reads (and strips) to answer with HTTP 409, mirroring
      deploy_run()'s own 409 contract -- never sent to the client verbatim.
    - {"ok": False, "status_code": None, "elapsed_ms": int, "error": <msg>}
      -- the request itself never completed (timeout, connection refused,
      or any other transport-level failure).
    - {"ok": True, "status_code": int, "elapsed_ms": int,
       "content_ok": bool | None} -- a completed request, success or not:
      a 404/500 from the target is a smoke-check RESULT, not a mechanism
      failure, so it lands here with its real status_code, same as a 200.
      content_ok is None (never False) when `expect_contains` was empty --
      "not checked" must stay visibly distinct from "checked and failed".
    """
    url = _session_urls.get(name)
    if url is None:
        return {"ok": False, "error": "no captured URL for this project"}

    lock = _smoke_check_lock_for(name)
    if not lock.acquire(blocking=False):
        return {"ok": False, "error": "a smoke check for this project is already in progress",
                "locked": True}
    try:
        start = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=SMOKE_CHECK_TIMEOUT_SECONDS) as resp:
                status_code = resp.status
                body = resp.read(SMOKE_CHECK_MAX_BODY_BYTES)
        except urllib.error.HTTPError as e:
            # Still a completed request -- a 4xx/5xx from the target is a
            # smoke-check RESULT, not a mechanism failure.
            status_code = e.code
            body = e.read(SMOKE_CHECK_MAX_BODY_BYTES) or b""
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError) as e:
            # A bare TimeoutError/ConnectionRefusedError can surface here
            # un-wrapped (e.g. resp.read() timing out on a stalled body
            # AFTER urlopen() already returned headers successfully) as well
            # as wrapped inside URLError.reason (e.g. a refused connection
            # at connect time) -- unwrap either shape the same way.
            elapsed_ms = int((time.monotonic() - start) * 1000)
            reason = e.reason if isinstance(e, urllib.error.URLError) else e
            if isinstance(reason, TimeoutError):
                error = f"timed out after {SMOKE_CHECK_TIMEOUT_SECONDS}s"
            elif isinstance(reason, ConnectionRefusedError):
                error = "connection refused"
            else:
                error = str(reason) or "request failed"
            return {"ok": False, "status_code": None, "elapsed_ms": elapsed_ms, "error": error}

        elapsed_ms = int((time.monotonic() - start) * 1000)
        content_ok = None
        if expect_contains:
            # errors="ignore" -- never raise on a non-UTF-8 body, same
            # decode discipline AI_REVIEWER's own diff-decode already uses.
            # The substring check only ever sees the (possibly truncated,
            # per SMOKE_CHECK_MAX_BODY_BYTES) prefix read above -- a match
            # past the cap is a documented, accepted limitation, not a bug.
            body_text = body.decode("utf-8", errors="ignore")
            content_ok = expect_contains in body_text
        return {"ok": True, "status_code": status_code, "elapsed_ms": elapsed_ms,
               "content_ok": content_ok}
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


def _last_path_segment_from_clone_url(url: str) -> str:
    """Naming-only heuristic ("what's the repo's own name") for
    clone_project_from_url()'s default-name derivation (backlog item 16,
    docs/spec.md "Name derivation and collision handling") -- this never
    itself needs to reject anything, since _validate_clone_url() has already
    run by the time this is called."""
    m = _CLONE_URL_SCP_RE.match(url)
    path = url.split(":", 1)[1] if m else urllib.parse.urlsplit(url).path
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if last.endswith(".git"):
        last = last[:-4]
    return last


def clone_project_from_url(url: str, name_override: str) -> tuple[bool, str]:
    """Clones an arbitrary existing remote git repo URL into
    PROJECTS_DIR/<name> (backlog item 16, docs/spec.md). No Gitea
    involvement at all -- reads neither GITEA_ENABLED nor GITEA_API_TOKEN,
    same "general-purpose entry point, no dependency" positioning as the
    upload wizard. Validation/naming/collision checks run entirely
    unprivileged here; only the final mkdir/chown/git-clone crosses into
    root via NEW_PROJECT_FROM_URL_SCRIPT, same privilege-separation shape as
    create_project()'s own NEW_PROJECT_FROM_GITEA_SCRIPT hand-off above."""
    url = (url or "").strip()
    err = _validate_clone_url(url)
    if err:
        return False, err

    name = (name_override or "").strip()
    if name:
        if not NAME_RE.match(name):
            return False, "Use letters, numbers, spaces, - or _ (must start with a letter/number)."
    else:
        name = _derive_project_name(_last_path_segment_from_clone_url(url), fallback_prefix="clone")

    if name in instance_names():
        return False, f"'{name}' already exists."

    # deploy_run() (app/app.py) is this codebase's own precedent for
    # wrapping a synchronous, request-thread subprocess.run(..., timeout=...)
    # call in try/except (subprocess.SubprocessError, OSError) -- catches
    # subprocess.TimeoutExpired too (a SubprocessError subclass), unlike
    # create_project()'s/confirm_upload()'s own privileged-script calls,
    # which don't guard against a timeout exception at all (a pre-existing
    # gap in both, out of scope to fix here, but not one this new code
    # should repeat).
    try:
        r = subprocess.run(["sudo", NEW_PROJECT_FROM_URL_SCRIPT, url, name],
                           capture_output=True, text=True, timeout=CLONE_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"clone failed: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "clone script failed").strip()[:300]
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
def _derive_project_name(raw: str, fallback_prefix: str = "upload") -> str:
    """
    Sanitizes a raw name (from the uploaded zip's own contents, or —
    backlog item 16 — a clone URL's last path segment; either way, fully
    attacker-controlled) into a NAME_RE-valid project name (docs/spec.md
    step 5): strip disallowed characters, strip any leading non-alnum run
    (NAME_RE requires starting with a letter/number), cap at 60 chars. Falls
    back to "<fallback_prefix>-<8 hex chars>" if nothing usable survives --
    fallback_prefix defaults to "upload" (the upload wizard's own original,
    unchanged behavior); clone_project_from_url() passes "clone" instead.
    """
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", raw or "")
    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", cleaned)[:60]
    if NAME_RE.match(cleaned):
        return cleaned
    return f"{fallback_prefix}-{secrets.token_hex(4)}"


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


# Team session lifecycle, part 2a (backlog item 6d, docs/spec.md §1) -- the
# reverse import direction (teams.py -> app) has existed since 6a; this is
# the first time app.py itself imports teams. MUST sit here, after TMUX
# (:191), load_engines() (:397), tmux_has()/active_engine() (above) are all
# already defined -- teams.py does `from app import TMUX, tmux_has,
# load_engines` at ITS OWN module level, so app.py must have already
# defined all three by the time this import runs, or the circular import
# breaks. Never move this earlier. No sys.path manipulation needed -- both
# files live in the same directory, in a repo checkout and in a real
# install alike (see install.sh's own new teams.py copy line below).
import teams  # noqa: E402

# Background team-run threads (backlog item 6d part 2a). Keyed by PROJECT
# NAME -- at most one live team per project by construction (launch_team()'s
# own session-name collision check, part 1) -- same "in-memory, lost on
# restart, self-heals via _reap_dead_state()" tradeoff as _ttyd_procs/
# _code_procs below (docs/ARCHITECTURE.md "In-memory state and its one
# sharp edge").
_team_threads: dict[str, dict] = {}   # name -> {"run_id", "thread", "cancel_event"}

# Guards every access to _team_threads (docs/test-review.md must-fix: a
# check-then-act race on this dict -- the fourth instance of this exact
# defect class in this story's own 6d subsystem, after the ownership stamp,
# the unstamped-session window, and the partial-creation cleanup, all in
# part 1). _team_threads_set()/_team_threads_get()/_team_threads_pop_if_
# owned() below are the ONLY sanctioned way to touch _team_threads anywhere
# in this file -- every call site was audited and switched to go through
# one of these three, not just the one with the originally-reported bug.
_team_threads_lock = threading.Lock()


def _team_threads_set(name: str, entry: dict) -> None:
    with _team_threads_lock:
        _team_threads[name] = entry


def _team_threads_get(name: str) -> dict | None:
    with _team_threads_lock:
        return _team_threads.get(name)


def _team_threads_pop_if_owned(name: str, run_id: str) -> None:
    """
    Atomically removes _team_threads[name] iff it is STILL the entry for
    run_id at the moment of removal -- the read-check-pop sequence happens
    under a single _team_threads_lock acquisition, so there is no
    observable window between the check and the pop for a concurrent
    /team/start (_team_threads_set()) to install a fresh entry for the same
    project that this call could then destroy. Previously this was a bare
    check-then-act (`entry = _team_threads.get(name)` ... later
    `_team_threads.pop(name, None)`) -- the pop removed whatever was
    CURRENTLY keyed there, not specifically the validated entry, so a
    relaunch landing in that window had its own fresh entry silently
    destroyed by the old thread's own cleanup (docs/test-review.md
    must-fix, reproduced with an artificially widened window; see
    TeamThreadsLockTests). Fixed structurally -- one atomic operation, not
    a narrowed window.
    """
    with _team_threads_lock:
        entry = _team_threads.get(name)
        if entry is not None and entry.get("run_id") == run_id:
            _team_threads.pop(name, None)


def _run_team_in_background(name: str, run_id: str, cancel_event: threading.Event) -> None:
    """
    Spawned by the /team/start route, daemon thread, same "return fast, do
    the real work off the request thread" idiom _gitea_sync_bg()/
    _generate_description_bg() already establish. Loads a fresh state dict
    (never trusts a stale local var) and drives it to completion via
    team_run(state, cancel_event=cancel_event) -- see docs/spec.md
    "Cooperative cancellation" for what that actually stops. team_run() is
    documented "never raises", but wrapped in try/except Exception anyway
    (this story's own repeated lesson: a "never" claim elsewhere in this
    codebase has been wrong before) -- an unexpected exception marks the
    run "error" via teams.mark_run_error() rather than silently vanishing
    the thread with run.json stuck on "running" forever.

    Ownership-checked removal from _team_threads on the way out (mirrors
    part 1's own _kill_team_session_if_owned() lesson) via _team_threads_
    pop_if_owned() -- atomic, so this genuinely guards against a subsequent
    stop-then-relaunch having already replaced the entry with a NEWER run's
    thread before this old thread's own cleanup runs (see that function's
    own docstring for why the earlier, unlocked version of this comment's
    claim did not actually hold).
    """
    try:
        state = teams._load_state(run_id)
        teams.team_run(state, cancel_event=cancel_event)
    except Exception as e:
        try:
            teams.mark_run_error(run_id, f"team run failed with an unexpected error: {e}")
        except FileNotFoundError:
            pass
    finally:
        _team_threads_pop_if_owned(name, run_id)


def _parse_events_cursor(raw: str) -> dict:
    """
    GET .../team/events' own `?cursor=` query param (backlog item 6f part
    1, docs/spec.md §1 "Open questions" -- cursor wire format): a single
    URL-encoded JSON object, {"<agent>": <byte_offset>, ...}. A malformed
    value -- not valid JSON, not a JSON object, or any value that isn't a
    non-negative int -- degrades to {} ("from the start") rather than a
    400: a stale/hand-crafted cursor should never break a client's poll
    loop, only cost it a full re-fetch.
    """
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(obj, dict):
        return {}
    for v in obj.values():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return {}
    return obj


_team_reap_lock = threading.Lock()
_team_reap_last_at = 0.0


def _team_reap_if_due() -> None:
    """
    Called from _reap_dead_state() on every /status. Same throttling idiom
    as _gitea_poll_if_due() (Lock + last-run timestamp, double-checked
    after acquiring the lock) -- an unthrottled sweep_dead_teams() call on
    every poll from every open browser tab would repeatedly re-attempt a
    real `git worktree remove` subprocess call against any currently-dirty
    worktree (docs/spec.md "Proposed approach" §5).

    Also closes the service-restart gap: a run recorded status="running"
    with no matching, ALIVE thread in _team_threads (this process was
    restarted, or a run this process never launched is otherwise orphaned)
    is marked "error" via teams.mark_run_error() -- surfaced as the `error`
    coarse label on the next /status poll. A run genuinely still being
    driven by a separate CLI process (`team-resume`) looks identical from
    here and may be transiently mis-flipped for up to one interval -- an
    accepted, disclosed, self-correcting tradeoff (docs/spec.md "Open
    questions", settled by the user 2026-08-13): that process's own next
    _persist() call unconditionally overwrites status with the truth.
    """
    global _team_reap_last_at
    if time.time() - _team_reap_last_at < TEAM_REAP_POLL_INTERVAL_SECONDS:
        return
    if not _team_reap_lock.acquire(blocking=False):
        return
    try:
        if time.time() - _team_reap_last_at < TEAM_REAP_POLL_INTERVAL_SECONDS:
            return
        _team_reap_last_at = time.time()
        teams.sweep_dead_teams()
        for name in instance_names():
            run = teams.latest_run_for_project(name)
            if run is None or run["status"] != "running":
                continue
            entry = _team_threads_get(name)
            if entry is not None and entry.get("run_id") == run["run_id"] and entry["thread"].is_alive():
                continue
            teams.mark_run_error(run["run_id"],
                                 "no driving thread found for this run (service restart?) -- "
                                 "stop this team, then start a new one")
    finally:
        _team_reap_lock.release()


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
    # Team session lifecycle, part 2a (backlog item 6d, docs/spec.md §5) --
    # internally throttled to its own TEAM_REAP_POLL_INTERVAL_SECONDS, same
    # precedent as _gitea_poll_if_due() below.
    _team_reap_if_due()


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
  /* .team-btn (backlog item 6d part 2a) shares this shape byte-for-byte
     ("same class/styling as 'Deploy' button", docs/design.md) but is its
     OWN class, not a literal reuse of .deploy-btn -- a project with no
     deploy-map entry must never render anything class="deploy-btn" at all
     (tests/test_deploy_frontend.js's own existing, reviewer-approved
     assertion), and every project unconditionally renders a team control. */
  .deploy-btn, .team-btn { font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none;
                background: #34c759; color: #111; font-weight: 600; cursor: pointer;
                white-space: nowrap; }
  .deploy-msg { font-size: 12px; color: #888; margin: 4px 0 0; min-height: 14px; word-break: break-all; }
  .deploy-msg.success { color: #34c759; }
  .deploy-msg.error { color: #ff6b6b; }
  /* HTTP-level smoke check (backlog item 18, docs/spec.md) -- .smoke-btn is
     its OWN class, deliberately NOT reusing .deploy-btn/.team-btn's shared
     green (#34c759): backlog item 20 already fixed that pairing's text
     color to #111 for the two existing call sites, but a brand-new control
     shouldn't inherit any shared button rule's color sight-unseen. Instead
     reuses #4da6ff/#111 -- the same pairing .pill.code-pill.active already
     ships elsewhere on this page -- independently verified here (not
     assumed) at 7.39:1 by WCAG relative luminance, comfortably passing
     AA's 4.5:1 minimum (see docs/implementation.md for the computed
     figures). */
  .smoke-check-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
  .smoke-check-row input { flex: 1; min-width: 140px; font-size: 13px; padding: 8px 10px;
                            border-radius: 8px; border: 1px solid #333; background: #1c1c1c; color: #eee; }
  .smoke-btn { font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none;
               background: #4da6ff; color: #111; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .smoke-check-msg { font-size: 12px; color: #888; margin: 4px 0 0; min-height: 14px; word-break: break-all; }
  .smoke-check-msg.success { color: #34c759; }
  .smoke-check-msg.error { color: #ff6b6b; }
  /* Minimal per-project team control (backlog item 6d, part 2a --
     docs/design.md). Follows .deploy-row/.deploy-btn/.deploy-msg's own
     shape verbatim -- a single-purpose row, not the checkbox-toggle
     pattern, since starting a team needs a task-text input, not a boolean
     flip. */
  .team-row { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .team-textarea { font-size: 13px; padding: 8px 10px; border-radius: 8px;
                    border: 1px solid #333; background: #1c1c1c; color: #eee;
                    resize: vertical; min-height: 44px; font-family: inherit; }
  .team-actions { display: flex; align-items: center; gap: 8px; }
  .team-status { font-size: 13px; }
  .team-status.status-running { color: #4da6ff; }
  .team-status.status-blocked { color: #ffb648; }
  .team-status.status-finished { color: #34c759; }
  .team-status.status-error { color: #ff6b6b; }
  .team-sub { font-size: 12px; color: #888; }
  .team-msg { font-size: 12px; color: #888; margin: 0; min-height: 14px; word-break: break-all; }
  .team-msg.success { color: #34c759; }
  .team-msg.error { color: #ff6b6b; }
  /* Roster & composition UI (backlog item 6e, docs/design.md) -- the
     idle-state row's "Configure team..." picker panel. No new color
     tokens beyond .team-tier-3-caveat's #ffb648 (design.md: "no existing
     warning/orange token was already in use on this page"); everything
     else reuses tokens already established by the 6d rows above. */
  .team-configure-row { margin-top: 2px; }
  .team-configure-btn { color: #4da6ff; cursor: pointer; font-size: 12px;
                         background: none; border: none; padding: 0; text-decoration: underline; }
  .team-picker { display: flex; flex-direction: column; gap: 8px; margin-top: 4px;
                 padding: 8px 10px; border: 1px solid #333; border-radius: 8px; background: #181818; }
  .team-lead-picker, .team-mates-picker, .team-grounding { display: flex; flex-direction: column; gap: 4px; }
  .team-lead-picker label, .team-mates-picker > label:first-child { font-size: 12px; color: #aaa; }
  .team-lead-picker select { font-size: 13px; padding: 6px 8px; border-radius: 8px;
                              border: 1px solid #333; background: #1c1c1c; color: #eee; font-family: inherit; }
  .team-mates-picker label { font-size: 13px; color: #eee; display: flex; align-items: center; gap: 6px; }
  .team-tier-3-caveat { font-size: 12px; color: #ffb648; border-left: 2px solid #ffb648;
                         padding: 4px 8px; background: #1c1c1c; }
  .team-grounding { font-size: 12px; color: #888; }
  .team-validation-error { font-size: 12px; color: #ff6b6b; min-height: 14px; }
  /* Past team branches panel (backlog item 13, docs/spec.md) -- read-only,
     list-only, no action buttons. Same font-size/color tokens as
     .team-grounding above, its closest existing precedent (a small,
     muted, informational list already living in this same team panel
     area). */
  .team-branches { font-size: 12px; color: #888; margin-top: 6px;
                    display: flex; flex-direction: column; gap: 4px; }
  .team-branches-title { color: #aaa; }
  .team-branch-row { display: flex; gap: 8px; flex-wrap: wrap; }
  .team-branch-name { color: #eee; font-family: monospace; }
  .team-branch-commit { font-family: monospace; }
  /* Live event feed + escalation inbox (backlog item 6f part 2, docs/
     design.md) -- status strip replaces the old plain "Status: [label]"
     line the non-idle branch used to render; escalation panel and the
     collapsible feed are new panels below it. Status strip colors reuse
     the same four status-* tokens 6d part 2a already established
     (`.team-status.status-*` above); only the agent-identity palette
     (`TEAM_AGENT_PALETTE` in the script below) is new, chosen distinct
     from all four semantic tokens per docs/spec.md's own constraint. */
  .team-status-strip { font-size: 13px; font-weight: 600; }
  .team-status-strip.status-running { color: #4da6ff; }
  .team-status-strip.status-blocked { color: #ffb648; }
  .team-status-strip.status-finished { color: #34c759; }
  .team-status-strip.status-error { color: #ff6b6b; }
  .team-escalation { display: flex; flex-direction: column; gap: 8px; margin-top: 4px;
                      padding: 10px 12px; border: 1px solid #333; border-radius: 8px; background: #181818; }
  .team-escalation-form { display: flex; flex-direction: column; gap: 8px; }
  .team-escalation-header { font-size: 12px; color: #aaa; }
  .team-escalation-question { font-size: 13px; color: #eee; }
  .team-escalation-options { border: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  legend.team-escalation-question { padding: 0; display: block; }
  .team-escalation-option { font-size: 13px; color: #eee; display: flex; align-items: flex-start; gap: 6px; }
  .team-escalation-option-desc { font-size: 12px; color: #888; }
  .team-escalation-form textarea { font-size: 13px; padding: 8px 10px; border-radius: 8px;
                                    border: 1px solid #333; background: #1c1c1c; color: #eee;
                                    resize: vertical; min-height: 44px; font-family: inherit; }
  /* Board-write proposal panel (backlog item 7 part 2, docs/design.md
     "Escalation Panel: set_status/amend_description/append_comment Verb")
     -- reuses .team-escalation's own wrapper/gap/padding above; only the
     inner layout differs (verb summary + current/proposed comparison
     blocks + buttons, no radio/checkbox form). */
  .team-escalation-proposal { display: flex; flex-direction: column; gap: 8px; }
  .team-escalation-proposal-summary { font-size: 13px; color: #eee; }
  .team-escalation-proposal-label { font-size: 12px; color: #aaa; }
  .team-escalation-proposal-box { font-size: 12px; color: #ccc; background: #0a0a0a;
                                   border: 1px solid #333; border-radius: 6px; padding: 0.5em;
                                   max-height: 200px; overflow-y: auto; font-family: monospace;
                                   line-height: 1.2; width: 100%; resize: vertical; box-sizing: border-box; }
  .team-escalation-proposal-note { font-size: 12px; color: #888; }
  .team-feed-toggle-row { margin-top: 2px; }
  .team-feed-toggle { color: #4da6ff; cursor: pointer; font-size: 12px;
                       background: none; border: none; padding: 0; text-decoration: underline; }
  .team-feed { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
  .team-feed-filter { display: flex; flex-wrap: wrap; gap: 6px; }
  .team-feed-filter button { font-size: 12px; padding: 3px 9px; border-radius: 12px; border: 1px solid #333;
                              background: #1c1c1c; color: #aaa; cursor: pointer; }
  .team-feed-filter button.active { background: #16324a; color: #4da6ff; border-color: #4da6ff; }
  /* Reuses .wizard-card's own max-height:85vh/overflow-y:auto scroll
     pattern (docs/spec.md: "reuse that pattern for the feed's own scroll
     container rather than inventing a new one"). monospace + 1.4
     line-height is this codebase's first log-like panel (docs/spec.md:
     "no existing monospace/log-styling precedent"). */
  .team-feed-list { max-height: 85vh; overflow-y: auto; padding: 8px 10px; border: 1px solid #333;
                     border-radius: 8px; background: #181818; font-family: monospace; font-size: 12px;
                     line-height: 1.4; }
  .team-feed-empty { color: #888; }
  .team-feed-event { padding: 2px 0; color: #eee; }
  .team-feed-event.kind-error { color: #ff6b6b; }
  .team-feed-event.kind-terminal-escalation { color: #ffb648; }
  .team-feed-event.kind-pending-classification { color: #888; font-style: italic; }
  .team-feed-ts { color: #888; }
  .team-feed-agent { font-weight: 600; }
  .team-feed-match { padding-left: 14px; }
  .team-feed-nomatch { color: #ff6b6b; }
  /* Compose box for interjecting into a running team (backlog item 19 part
     2, docs/design.md) -- reuses .team-textarea's own shape verbatim for
     the textarea and .team-btn for Send; only the outer wrapper + counter
     are new. Human messages in the feed get a left-border accent, not a
     chat-bubble redesign (docs/spec.md "Proposed approach" §2). */
  .team-interject { display: flex; flex-direction: column; gap: 8px; margin-top: 4px;
                     padding: 10px 12px; border: 1px solid #333; border-radius: 8px; background: #181818; }
  .team-interject-row { display: flex; gap: 8px; }
  .team-interject-textarea { font-size: 13px; padding: 8px 10px; border-radius: 8px;
                              border: 1px solid #333; background: #1c1c1c; color: #eee;
                              resize: vertical; min-height: 44px; font-family: inherit; flex: 1; }
  .team-interject-counter { font-size: 12px; color: #888; text-align: left; }
  .team-interject-counter.over-limit { color: #ff6b6b; }
  .team-feed-event.kind-human-message { border-left: 3px solid #4da6ff; padding-left: 12px; }
  /* "+" add-teammate control (backlog item 21 part 2, docs/design.md) --
     reuses .team-lead-picker select's own declaration block verbatim for
     the <select>, and .team-sub's own muted-informational-text token
     (#888/12px) for the two disabled-reason strings. No new color tokens. */
  .team-add-member { display: flex; gap: 8px; align-items: center; margin-top: 4px; }
  .team-add-member select { font-size: 13px; padding: 6px 8px; border-radius: 8px;
                              border: 1px solid #333; background: #1c1c1c; color: #eee; font-family: inherit; }
  .team-add-member-reason { font-size: 12px; color: #888; }
  .team-feed-event.kind-member-joined { border-left: 3px solid currentColor; padding-left: 12px; }
  .new-project-row { display: flex; gap: 8px; padding: 4px 0 16px; }
  .new-project-row input { flex: 1; font-size: 14px; padding: 10px 12px; border-radius: 10px;
                            border: 1px solid #333; background: #1c1c1c; color: #eee; }
  .new-project-row button { font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none;
                             background: #34c759; color: #111; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .new-project-err { color: #ff6b6b; font-size: 12px; margin: -10px 0 12px; min-height: 14px; }
  .clone-form { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0 8px; align-items: center; }
  .clone-form-label { flex-basis: 100%; font-size: 12px; color: #aaa; font-weight: 600; }
  .clone-form input { flex: 1; min-width: 160px; font-size: 14px; padding: 10px 12px; border-radius: 10px;
                       border: 1px solid #333; background: #1c1c1c; color: #eee; }
  .clone-form input#clone-name { flex: 0 1 200px; }
  .clone-form button { font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none;
                        background: #34c759; color: #111; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .clone-form input:disabled, .clone-form button:disabled { opacity: 0.6; cursor: not-allowed; }
  .clone-err { color: #ff6b6b; font-size: 12px; margin: -4px 0 12px; min-height: 14px; }
  .clone-status { color: #aaa; font-size: 12px; margin: -4px 0 12px; min-height: 14px; }
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
<button class="upload-wizard-btn" id="clone-toggle-btn" onclick="openCloneForm()">Clone from URL</button>
<div class="clone-form" id="clone-form" style="display: none;">
  <div class="clone-form-label">Clone from URL</div>
  <input id="clone-url" placeholder="https://github.com/user/repo or ssh://host/path" maxlength="2048"
         onkeypress="event.key==='Enter' && startClone()">
  <input id="clone-name" placeholder="(optional — derived from URL)" maxlength="60"
         onkeypress="event.key==='Enter' && startClone()">
  <button onclick="startClone()">Clone</button>
</div>
<div class="clone-err" id="clone-err"></div>
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
// Roster & composition UI (backlog item 6e, docs/design.md) -- ROSTER is
// global (not per-project, matching /status's own top-level "roster"
// field), refreshed every refresh() call, re-read live off engines.d each
// time (no client-side caching, mirroring roster()'s own no-cache
// philosophy). TEAM_BY_NAME lets an onclick handler (which only ever gets
// a project `name`, not the whole `team` object) look up that project's
// current inst.team -- specifically inst.team.composition, used to seed
// the picker's pre-selection the first time it's opened for a project.
let ROSTER = [];
let TEAM_BY_NAME = {};

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
  ROSTER = s.roster || [];
  // "+" add-teammate control (backlog item 21 part 2) -- live override of
  // the hardcoded default, same idiom as ROSTER itself above.
  if (s.team_max_members) TEAM_MAX_MEMBERS_CLIENT = s.team_max_members;
  DEPLOY_TARGETS = {};
  TEAM_BY_NAME = {};
  let html = '';
  for (const inst of s.instances) {
    if (inst.deploy) DEPLOY_TARGETS[inst.name] = inst.deploy;
    TEAM_BY_NAME[inst.name] = inst.team;
    html += row(inst.name, inst.on, inst.url, 'inst', inst.name, inst.desc, inst.engine,
               inst.code_on, inst.code_url, undefined, undefined, inst.gitea_sync, inst.deploy, inst.team);
    // Live event feed polling (backlog item 6f part 2, docs/spec.md
    // "Proposed approach" §3) -- folded into this existing 4s cycle, no new
    // setInterval. teamFeedOpen[inst.name] is only ever set once row()'s
    // own teamRow() call above has already run for this project (it seeds
    // the default-open state the first time a row renders non-idle), so
    // this check always sees the up-to-date value. Fire-and-forget: does
    // not block this refresh() call's own render.
    if (inst.team && inst.team.status !== 'idle' && teamFeedOpen[inst.name]) {
      pollTeamFeed(inst.name);
    }
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
// Per-project client state for the optional expect_contains text field
// (backlog item 18, docs/spec.md) -- survives refresh()'s own full-row
// re-render the same way teamTaskText already does below, since the input
// would otherwise be wiped every 4-second poll while the operator is still
// typing.
let smokeCheckExpect = {};
// HTTP-level smoke check (backlog item 18, docs/spec.md). Rendered only
// when this project currently has a captured hosted URL (inst.url
// non-null) -- mirrors deployRow()'s own "return '' if not present" shape
// -- since a direct POST with no captured URL is still handled cleanly
// server-side, but there is nothing useful to click otherwise. Reused-
// message slot (.smoke-check-msg) is always rendered empty here;
// doSmokeCheck() (via handleActionResult()'s own kind === 'smoke-check'
// branch) fills it in after the POST resolves, and it's gone again on the
// next refresh() (no persisted history — docs/spec.md non-goals).
function smokeCheckRow(name, url) {
  if (!url) return '';
  const text = smokeCheckExpect[name] || '';
  return '<div class="smoke-check-row">' +
    '<input id="smoke-expect-' + esc(name) + '" maxlength="500" ' +
    'placeholder="optional: text that should appear in the response" value="' + esc(text) + '" ' +
    'oninput="smokeCheckExpect[' + "'" + name + "'" + '] = this.value">' +
    '<button class="smoke-btn" onclick="doSmokeCheck(' + "'" + name + "'" + ')">Smoke check</button>' +
    '</div>' +
    '<div class="smoke-check-msg" id="smoke-check-msg-' + esc(name) + '"></div>';
}
// Per-project client state for the in-progress task text (backlog item 6d,
// part 2a) -- survives refresh()'s own full-row re-render the same way
// engineChoice already does for the engine picker, since the team row's
// textarea would otherwise be wiped every 4-second poll while the operator
// is still typing.
let teamTaskText = {};
// Roster & composition UI (backlog item 6e, docs/design.md) -- per-project
// picker state, all keyed by project name, all surviving refresh()'s own
// full-row re-render the same way teamTaskText already does above.
let teamPickerOpen = {};        // name -> bool, closed by default
let teamPickerInitialized = {}; // name -> bool, true once seeded from inst.team.composition
let teamPickerLead = {};        // name -> {kind, name} | null
let teamPickerMembers = {};     // name -> Set<string> (engine names)
let teamGroundingCache = {};    // name -> {files, skipped} | null (fetch failed) | undefined (not fetched yet)
// Past team branches panel (backlog item 13, docs/spec.md) -- name -> branch[]
// | null (fetch failed) | undefined (not fetched yet). Fetched once per
// project the first time its row renders (see renderTeamBranches() below),
// NOT joined to the existing 4s /status poll cycle -- this data only
// changes when a team run stops, so a single fetch per project per page
// load is enough (docs/spec.md).
let teamBranchesCache = {};

// Live event feed + escalation inbox (backlog item 6f part 2, docs/spec.md
// / docs/design.md) -- per-project client state, all keyed by project name
// (or, for the inbox cache, by run_id -- a question is a property of a
// specific run, not a project) and all surviving refresh()'s own full-row
// re-render the same way teamTaskText/teamPickerLead already do above.
let teamFeedOpen = {};          // name -> bool, undefined until the row first renders non-idle
let teamFeedCursor = {};        // name -> {agent: byte_offset}, the last /team/events cursor
let teamFeedEvents = {};        // name -> event[] (rolling buffer, most recent 500 kept)
let teamFeedFilter = {};        // name -> "all" | agent name
let teamFeedPolling = {};       // name -> bool, true while pollTeamFeed()'s own drain loop is in flight
let teamInboxCache = {};        // run_id -> inbox response | 'pending' | null (fetch failed)
let teamEscalationSelected = {}; // name -> Set<number> (indices into the cached inbox's own options[])
let teamEscalationOther = {};   // name -> string, the free-text "Other" answer in progress
// board_write escalation panel (backlog item 7 part 2, docs/spec.md §5) --
// which action ("approve"/"reject") the operator just clicked, set by
// doTeamBoardResolve() BEFORE toggle()'s first (optimistic, no-code) POST
// fires and read back by actionBody()'s 'team-board-resolve' branch -- same
// "small client-side map keyed by name, surviving a TOTP retry" pattern
// teamEscalationOther already establishes above, so a 428-then-retry re-
// reads the SAME action the operator originally clicked rather than
// something sourced from the (possibly-already-gone) pendingToggle context.
let teamBoardResolveAction = {};
// Chat-UI compose surface (backlog item 19 part 2, docs/spec.md "Proposed
// approach" §1) -- per-project draft-text mirror, same "survives a full-row
// refresh() re-render and a 428 TOTP retry" idiom teamTaskText already
// establishes above. TEAM_INTERJECT_MAX_CHARS_CLIENT hardcodes the server's
// documented default (teams.TEAM_INTERJECT_MAX_CHARS) rather than fetching
// it live -- mirrors doTeamResolve()'s own existing hardcoded-2000
// precedent; see docs/spec.md "Non-goals" for the accepted drift risk if
// the env var is ever overridden.
let teamInterjectText = {};  // name -> string draft
const TEAM_INTERJECT_MAX_CHARS_CLIENT = 2000;

// "+" add-teammate control (backlog item 21 part 2, docs/spec.md "Proposed
// approach" §5) -- teamAddMemberChoice mirrors teamInterjectText's own
// "survives a mid-flow re-render/428 retry" idiom: the selected agent name
// is saved BEFORE toggle()'s POST fires, so a 428-then-retry resends the
// SAME agent rather than re-reading a possibly-already-redrawn <select>.
// TEAM_MAX_MEMBERS_CLIENT is a `let` (not `const`, unlike
// TEAM_INTERJECT_MAX_CHARS_CLIENT above) -- hardcoded default matching the
// server's own default, but overwritten from s.team_max_members on every
// /status poll (refresh()), since this cap is cheap to fetch live and
// unlike the interject char limit is directly gating a control's disabled
// state, not just advisory copy.
let teamAddMemberChoice = {};  // name -> agent name
let TEAM_MAX_MEMBERS_CLIENT = 6;

// Agent-identity colour palette (docs/design.md "ui-ux-pro-max choices") --
// deliberately distinct from the four semantic status colors
// (#4da6ff/#ffb648/#34c759/#ff6b6b) so agent identity in the feed is never
// confused with run status. Hash is deterministic -- stable across polls
// and page reloads for the same agent name.
const TEAM_AGENT_PALETTE = ['#d084d0', '#6eb5d4', '#b4a84d', '#84b484', '#d4a484', '#a49ed4'];
function teamAgentColor(agentName) {
  const s = agentName || '';
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return TEAM_AGENT_PALETTE[h % TEAM_AGENT_PALETTE.length];
}
// Clears every piece of feed/escalation client state for a project -- called
// when its row falls back into the idle branch (docs/spec.md "Edge cases":
// "A team stops ... the feed/escalation panel and their client-side state
// ... are cleared when the row re-renders back into the idle branch").
// teamInboxCache is intentionally NOT touched here -- it is keyed by
// run_id, not project name, and a finished run's own run_id is never
// reused (teams._run_id() draws from secrets), so its cache entry just
// goes cold rather than needing active cleanup.
function clearTeamFeedState(name) {
  delete teamFeedOpen[name];
  delete teamFeedCursor[name];
  delete teamFeedEvents[name];
  delete teamFeedFilter[name];
  delete teamEscalationSelected[name];
  delete teamEscalationOther[name];
  // Chat-UI compose surface (backlog item 19 part 2) -- an unsent draft is
  // discarded the moment a project falls back to the idle branch, same
  // "no attempt to persist/restore" treatment every other per-status-scoped
  // input in this app already gets (docs/spec.md "Edge cases").
  delete teamInterjectText[name];
}

// Client-side mirror of teams.validate_composition()'s rules (docs/spec.md
// 6e) -- fast feedback only; the server's own check in POST .../team/start
// remains the source of truth. Duplicate-teammate is structurally
// impossible via checkboxes (a Set), so there's no dedicated check for it
// here, matching docs/design.md's own note.
function teamCompositionError(name) {
  const lead = teamPickerLead[name];
  if (!lead) return 'Lead is required';
  const members = Array.from(teamPickerMembers[name] || []);
  if (members.length === 0) return 'At least one teammate is required';
  if (members.indexOf(lead.name) !== -1) return 'Lead cannot also be a teammate';
  return null;
}
function tierLabel(tier) {
  return tier === 1 ? 'tier 1 - native tools' : tier === 2 ? 'tier 2 - schema constrained' : 'tier 3 - prose parse';
}
// Fetched once per picker-open (docs/design.md "Roster and grounding are
// fetched on picker open"), not on every 4s poll -- cached client-side
// until the picker is closed/reopened for this project.
async function fetchTeamGrounding(name) {
  try {
    const r = await fetch('/projects/' + encodeURIComponent(name) + '/team/grounding');
    teamGroundingCache[name] = r.ok ? await r.json() : null;
  } catch (e) {
    teamGroundingCache[name] = null;
  }
  refresh();
}
// Past team branches panel (backlog item 13, docs/spec.md) -- fetched once
// per project, the first time renderTeamBranches() below finds no cache
// entry for it yet. Deliberately does NOT call refresh() itself once
// resolved (unlike fetchTeamGrounding()/fetchTeamInbox() above, both of
// which are triggered by a direct operator action expecting immediate
// feedback): this fetch fires passively as a side effect of a normal row
// render, and docs/spec.md itself says this data "does NOT need to join
// the existing 4s /status poll cycle" -- the already-running setInterval(
// refresh, 4000) picks up the now-cached result on its own next tick
// regardless, so forcing an extra immediate refresh() here would only add
// an unnecessary redundant render for every project on every page load.
async function fetchTeamBranches(name) {
  try {
    const r = await fetch('/projects/' + encodeURIComponent(name) + '/team/branches');
    teamBranchesCache[name] = r.ok ? await r.json() : null;
  } catch (e) {
    teamBranchesCache[name] = null;
  }
}
// List-only, no action buttons (docs/spec.md scope). Always rendered (idle
// or not) since a project's past branches are independent of any run
// currently in progress. committer_date is shown as its own YYYY-MM-DD date
// (the ISO-strict string's own date portion) -- no relative-time formatting
// dependency added for a small, informational, once-per-load list.
function renderTeamBranches(name) {
  const id = 'team-branches-' + esc(name);
  if (teamBranchesCache[name] === undefined) {
    fetchTeamBranches(name);  // picked up by the next normal refresh() poll
    return '<div class="team-branches" id="' + id + '">Loading past team branches…</div>';
  }
  const branches = teamBranchesCache[name];
  if (branches === null) {
    return '<div class="team-branches" id="' + id + '">Past team branches unavailable</div>';
  }
  if (branches.length === 0) {
    return '<div class="team-branches" id="' + id + '">No past team branches</div>';
  }
  const rows = branches.map(b => {
    const shortCommit = (b.commit || '').slice(0, 7);
    const dateLabel = b.committer_date ? b.committer_date.slice(0, 10) : '';
    return '<div class="team-branch-row">' +
      '<span class="team-branch-name">' + esc(b.branch) + '</span>' +
      '<span class="team-branch-commit">' + esc(shortCommit) + '</span>' +
      '<span class="team-branch-subject">' + esc(b.subject || '') + '</span>' +
      '<span class="team-branch-date">' + esc(dateLabel) + '</span>' +
      '</div>';
  }).join('');
  return '<div class="team-branches" id="' + id + '">' +
    '<div class="team-branches-title">Past team branches</div>' + rows + '</div>';
}
function toggleTeamPicker(name) {
  teamPickerOpen[name] = !teamPickerOpen[name];
  if (teamPickerOpen[name] && !teamPickerInitialized[name]) {
    // Pre-select from inst.team.composition (docs/spec.md: the saved
    // composition if one exists, else default_team_composition()'s own
    // pick, else -- unreachable here, since the "no composition at all"
    // case never renders a Configure button to click in the first place).
    const team = TEAM_BY_NAME[name];
    const comp = team && team.composition;
    teamPickerLead[name] = comp ? comp.lead : null;
    teamPickerMembers[name] = new Set(comp ? comp.members : []);
    teamPickerInitialized[name] = true;
  }
  if (teamPickerOpen[name] && teamGroundingCache[name] === undefined) {
    fetchTeamGrounding(name);  // its own refresh() covers this render
    return;
  }
  refresh();
}
function onTeamLeadChange(name) {
  const sel = document.getElementById('team-lead-' + name);
  const val = sel ? sel.value : '';
  teamPickerLead[name] = val ? JSON.parse(val) : null;
  refresh();
}
function onTeamMateToggle(name, memberName, checked) {
  const set = teamPickerMembers[name] || (teamPickerMembers[name] = new Set());
  if (checked) set.add(memberName); else set.delete(memberName);
  refresh();
}
// docs/design.md "Grounding Files" -- always the same four canonical slots,
// in this fixed order, so an ABSENT file (e.g. no docs/ARCHITECTURE.md) is
// as visible as a found one, never silently omitted from the list.
const TEAM_GROUNDING_SLOTS = [
  {display: 'docs/ARCHITECTURE.md', labels: ['docs/ARCHITECTURE.md']},
  {display: 'docs/BACKLOG.md', labels: ['docs/BACKLOG.md']},
  {display: 'CLAUDE.md / AGENTS.md', labels: ['CLAUDE.md', 'AGENTS.md']},
  {display: 'README.md', labels: ['README.md']},
];
function renderTeamGrounding(name) {
  const id = 'team-grounding-' + esc(name);
  const g = teamGroundingCache[name];
  if (g === undefined) return '<div class="team-grounding" id="' + id + '">Loading grounding files…</div>';
  if (g === null) return '<div class="team-grounding" id="' + id + '">Grounding unavailable</div>';
  if (!g.files || g.files.length === 0) {
    return '<div class="team-grounding" id="' + id + '">No grounding files discovered</div>';
  }
  const byLabel = {};
  g.files.forEach(f => { byLabel[f.label] = f; });
  const rows = TEAM_GROUNDING_SLOTS.map(slot => {
    const found = slot.labels.map(l => byLabel[l]).find(Boolean);
    return found ?
      '<div>✓ ' + esc(slot.display) + ' (' + found.byte_count + ' bytes)</div>' :
      '<div>✗ ' + esc(slot.display) + ' (not found)</div>';
  }).join('');
  return '<div class="team-grounding" id="' + id + '">' + rows + '</div>';
}
function renderTeamPicker(name) {
  const lead = teamPickerLead[name];
  const members = teamPickerMembers[name] || new Set();
  const leadOptions = '<option value="">Choose a lead...</option>' + ROSTER.map(e => {
    const val = JSON.stringify({kind: e.kind, name: e.name});
    const selected = lead && lead.kind === e.kind && lead.name === e.name ? ' selected' : '';
    return "<option value='" + val + "'" + selected + '>' + esc(e.name) + ' (' + tierLabel(e.tier) + ')</option>';
  }).join('');
  const leadPicker = '<div class="team-lead-picker"><label>Lead</label>' +
    '<select id="team-lead-' + esc(name) + '" onchange="onTeamLeadChange(' + "'" + name + "'" + ')">' +
    leadOptions + '</select></div>';
  const leadEntry = lead ? ROSTER.find(e => e.kind === lead.kind && e.name === lead.name) : null;
  const caveat = leadEntry && leadEntry.tier === 3 ?
    '<div class="team-tier-3-caveat">⚠ This engine&#39;s reliability is lower due to prose-parsing ' +
    'tool-calling. Use only if no tier-1 or tier-2 lead is available.</div>' : '';
  const mateOptions = ROSTER.filter(e => e.delegate_capable && !(lead && lead.kind === e.kind && lead.name === e.name))
    .map(e => {
      const checked = members.has(e.name) ? ' checked' : '';
      return '<label><input type="checkbox" id="team-mate-' + esc(name) + '-' + esc(e.name) + '"' + checked +
        ' onchange="onTeamMateToggle(' + "'" + name + "','" + e.name + "'" + ', this.checked)"> ' +
        esc(e.name) + ' (' + tierLabel(e.tier) + ')</label>';
    }).join('');
  const mates = '<div class="team-mates-picker"><label>Teammates</label>' + mateOptions + '</div>';
  const err = teamCompositionError(name);
  const errDiv = '<div class="team-validation-error" id="team-validation-' + esc(name) + '">' +
    (err ? esc(err) : '') + '</div>';
  return '<div class="team-picker">' + leadPicker + caveat + mates + renderTeamGrounding(name) + errDiv + '</div>';
}
// Combined disabled-state recompute for the plain task-text oninput path
// (docs/design.md: Start is disabled if the task is empty OR, once the
// picker is open, a composition validation error exists) -- a live DOM
// update, not a full refresh(), matching 6d's own original oninput
// handler's lightweight style exactly (typing must not re-fetch /status).
function updateTeamStartButton(name) {
  const btn = document.getElementById('start-btn-' + name);
  if (!btn) return;
  const taskEl = document.getElementById('task-' + name);
  const taskOk = !!(taskEl && taskEl.value.trim());
  const compErr = teamPickerOpen[name] ? teamCompositionError(name) : null;
  btn.disabled = !taskOk || !!compErr;
}
// Minimal per-project team control (backlog item 6d, part 2a; extended with
// a lead/teammate picker in 6e -- docs/design.md). Rendered unconditionally
// per project (no "only if configured" gate, unlike deployRow()) -- styled
// after deployRow()'s own shape (a single-purpose row, not the
// checkbox-toggle pattern), since starting a team needs a task-text input,
// not a boolean flip.
// Live event feed + escalation inbox (backlog item 6f part 2, docs/spec.md
// / docs/design.md) -- status strip, escalation-answer panel, and the
// collapsible merged event feed, all rendered inline in teamRow()'s
// non-idle branch below. No new page/route -- built entirely against 6f
// part 1's already-shipped GET .../team/events, GET .../team/inbox, and
// POST .../team/resolve.
function renderTeamStatusStrip(team) {
  const idSuffix = team.run_id ? ' (ID: ' + esc(team.run_id) + ')' : '';
  if (team.status === 'running') {
    return '<div class="team-status-strip status-running">Working' + idSuffix + '</div>';
  }
  if (team.status === 'blocked') {
    if (team.waiting_on_you) {
      // escalation_kind (backlog item 7 part 2, docs/spec.md §1 / docs/
      // design.md "Status Strip: Board Write Pending Approval") -- same
      // orange "blocked"/waiting-on-you visual weight either way, only the
      // copy distinguishes a pending board-write proposal from an ask_user
      // question, so the strip is legible without opening the panel below.
      if (team.escalation_kind === 'board_write') {
        return '<div class="team-status-strip status-blocked waiting-on-you">⚠ Board write pending approval' + idSuffix + '</div>';
      }
      return '<div class="team-status-strip status-blocked waiting-on-you">⚠ Waiting on you' + idSuffix + '</div>';
    }
    return '<div class="team-status-strip status-blocked">Blocked — Max rounds reached' + idSuffix + '</div>';
  }
  if (team.status === 'finished') return '<div class="team-status-strip status-finished">Finished' + idSuffix + '</div>';
  if (team.status === 'error') return '<div class="team-status-strip status-error">Error' + idSuffix + '</div>';
  return '<div class="team-status-strip">' + esc(team.status) + idSuffix + '</div>';
}
// Fetched once per run_id (docs/design.md "On first render for a given
// run_id ... fetch GET .../team/inbox once and cache the result client-side
// keyed by run_id, not re-fetched on every poll tick") -- same
// fetch-then-refresh() idiom fetchTeamGrounding() already uses above.
async function fetchTeamInbox(name, runId) {
  teamInboxCache[runId] = 'pending';
  try {
    const r = await fetch('/projects/' + encodeURIComponent(name) + '/team/inbox?run_id=' + encodeURIComponent(runId));
    teamInboxCache[runId] = r.ok ? await r.json() : null;
  } catch (e) {
    teamInboxCache[runId] = null;
  }
  refresh();
}
// Options are addressed by INDEX into the cached inbox's own options[], not
// by label text -- labels are LLM-authored free text that could contain
// quotes/HTML and must never be embedded into an onclick/onchange
// attribute string (unlike engine/project names, which are NAME_RE-
// restricted elsewhere in this file). teamEscalationSelected[name] survives
// refresh()'s own full-row re-render the same way teamPickerMembers already
// does for the composition picker.
function onEscalationOptionChange(name, idx, multiSelect, checked) {
  const set = teamEscalationSelected[name] || (teamEscalationSelected[name] = new Set());
  if (multiSelect) {
    if (checked) set.add(idx); else set.delete(idx);
  } else {
    set.clear();
    if (checked) set.add(idx);
  }
  refresh();
}
// Shared by doTeamResolve()'s own client-side validation and
// actionBody()'s 'team-resolve' body so they can never diverge (docs/
// spec.md "Proposed approach" §2: free text wins if filled in, else the
// chosen option's label(s) joined with ", " for multi-select).
function computeTeamResolveAnswer(name) {
  const other = (teamEscalationOther[name] || '').trim();
  if (other) return other;
  const team = TEAM_BY_NAME[name];
  const runId = team && team.run_id;
  const cached = runId ? teamInboxCache[runId] : null;
  const options = (cached && cached.options) || [];
  const idxs = Array.from(teamEscalationSelected[name] || []);
  const labels = idxs.map(i => options[i] && options[i].label).filter(Boolean);
  if (cached && cached.multi_select) return labels.join(', ');
  return labels[0] || '';
}
// Shared long-text truncation (docs/spec.md "Edge cases": "Very long
// value/note/current_value.description text ... truncate/scroll, same
// general long-text handling ... 200-char truncation with an ellipsis is
// the existing precedent" -- teamFeedEventBody()'s own fact-check-match
// rendering below). Used for the board-write panel's one-line proposal
// summary and lead's note (docs/design.md recommends the longer
// current/proposed description-or-comment BLOCKS instead rely on the
// scrollable max-height box itself -- see .team-escalation-proposal-box).
function truncateText(text, max) {
  const s = text || '';
  return s.length > max ? s.slice(0, max) + '…' : s;
}
// Verb-specific board-write proposal panel (backlog item 7 part 2, docs/
// spec.md §5 / docs/design.md "Escalation Panel: set_status/
// amend_description/append_comment Verb") -- fetched/cached the same way
// renderEscalationPanel()'s ask_user branch already is (caller passes the
// already-resolved `cached` inbox response); only the render step differs.
function renderBoardWriteEscalationPanel(name, cached) {
  const subject = cached.subject ? esc(cached.subject) : ('#' + esc(cached.ref));
  const note = cached.note ? '<div class="team-escalation-proposal-note">Lead\\'s note: ' +
    esc(truncateText(cached.note, 200)) + '</div>' : '';
  let summary, blocks;
  if (cached.verb === 'set_status') {
    const cur = (cached.current_value && cached.current_value.status_name) || '(unknown)';
    summary = 'Move <strong>' + subject + '</strong> from <strong>' + esc(cur) +
      '</strong> to <strong>' + esc(cached.value || '') + '</strong>.';
    blocks = '';
  } else if (cached.verb === 'amend_description') {
    summary = 'Replace <strong>' + subject + '</strong>\\'s description';
    const curDesc = (cached.current_value && cached.current_value.description);
    blocks =
      '<div class="team-escalation-proposal-label">Current:</div>' +
      '<textarea class="team-escalation-proposal-box" readonly rows="6">' +
      esc(curDesc || '(description not available)') + '</textarea>' +
      '<div class="team-escalation-proposal-label">Proposed:</div>' +
      '<textarea class="team-escalation-proposal-box" readonly rows="6">' +
      esc(cached.value || '(description not available)') + '</textarea>';
  } else { // append_comment
    summary = 'Add a comment to <strong>' + subject + '</strong>';
    blocks =
      '<div class="team-escalation-proposal-label">Comment text:</div>' +
      '<textarea class="team-escalation-proposal-box" readonly rows="4">' +
      esc(cached.value || '(comment not available)') + '</textarea>';
  }
  return '<div class="team-escalation" id="team-escalation-' + esc(name) + '">' +
    '<div class="team-escalation-proposal">' +
    '<div class="team-escalation-proposal-summary">' + summary + '</div>' +
    blocks + note +
    '<div class="team-actions">' +
    '<button class="team-btn" onclick="doTeamBoardResolve(' + "'" + name + "'" + ", 'approve')" + '">Approve</button>' +
    '<button class="team-btn" onclick="doTeamBoardResolve(' + "'" + name + "'" + ", 'reject')" + '">Reject</button>' +
    '</div></div></div>';
}
function renderEscalationPanel(name, team) {
  if (!team.waiting_on_you) return '';
  const runId = team.run_id;
  const cached = runId ? teamInboxCache[runId] : null;
  if (cached === undefined) {
    if (runId) fetchTeamInbox(name, runId);
    return '<div class="team-escalation" id="team-escalation-' + esc(name) + '">Loading question…</div>';
  }
  if (cached === 'pending') {
    return '<div class="team-escalation" id="team-escalation-' + esc(name) + '">Loading question…</div>';
  }
  if (cached === null) {
    return '<div class="team-escalation" id="team-escalation-' + esc(name) + '">' +
      'Could not load the pending question. Check `tmux attach`.</div>';
  }
  if (!cached.pending) {
    // A narrow race, not a fetch failure: this project's own last /status
    // snapshot still says waiting_on_you (team.waiting_on_you is a
    // moment-in-time read), but /team/inbox -- fetched a beat later --
    // already reports no pending question/proposal (e.g. another tab/
    // operator just resolved it). The next 4s poll will pick up the
    // resolved status.
    const already = team.escalation_kind === 'board_write' ?
      'This proposal was already approved or rejected.' : 'This question was already answered.';
    return '<div class="team-escalation" id="team-escalation-' + esc(name) + '">' + already + '</div>';
  }
  if (team.escalation_kind === 'board_write') {
    return renderBoardWriteEscalationPanel(name, cached);
  }
  const selected = teamEscalationSelected[name] || new Set();
  const inputType = cached.multi_select ? 'checkbox' : 'radio';
  const optionsHtml = (cached.options || []).map((opt, i) => {
    const checked = selected.has(i) ? ' checked' : '';
    const desc = opt.description ?
      '<div class="team-escalation-option-desc">' + esc(opt.description) + '</div>' : '';
    return '<label class="team-escalation-option"><input type="' + inputType + '" name="escalation-option-' +
      esc(name) + '"' + checked + ' onchange="onEscalationOptionChange(' + "'" + name + "'," + i + ',' +
      (cached.multi_select ? 'true' : 'false') + ', this.checked)"><span>' + esc(opt.label) + '</span>' +
      desc + '</label>';
  }).join('');
  const otherText = teamEscalationOther[name] || '';
  // <fieldset>/<legend> around the option group (docs/design.md
  // "Accessibility & platform notes": "Escalation form: <fieldset> for
  // radio/checkbox groups with <legend> for the question"; docs/spec.md
  // part B: legend text is the pending question's own question/header
  // text). The fieldset's <legend> IS the previously-separate
  // ".team-escalation-question" div -- reusing the same class/text there
  // rather than rendering the question twice (once as a plain div, once
  // as the legend) keeps the visible layout unchanged (still one question
  // line, right above the options) while giving screen readers the
  // group-to-question association <fieldset>/<legend> is for. Wraps the
  // native radio/checkbox inputs specifically -- not the header chip, not
  // the free-text "Other" field (which has its own <label>, per design.md,
  // and isn't part of the option group being grouped).
  const optionsFieldset = '<fieldset class="team-escalation-options">' +
    '<legend class="team-escalation-question">' + esc(cached.question) + '</legend>' +
    optionsHtml + '</fieldset>';
  return '<div class="team-escalation" id="team-escalation-' + esc(name) + '">' +
    '<div class="team-escalation-form">' +
    (cached.header ? '<div class="team-escalation-header">' + esc(cached.header) + '</div>' : '') +
    optionsFieldset +
    '<label>Other (free text)<br><textarea id="escalation-other-' + esc(name) + '" rows="3" ' +
    'oninput="teamEscalationOther[' + "'" + name + "'" + '] = this.value;">' + esc(otherText) + '</textarea></label>' +
    '<div class="team-actions"><button class="team-btn" onclick="doTeamResolve(' + "'" + name + "'" +
    ')">Submit answer</button></div>' +
    '</div></div>';
}
// The escalation form's submit result (error/success) renders in the row's
// own single "id=team-msg-<name>" slot below (docs/design.md "Message slot
// (.team-msg pattern)") -- not a second/duplicate message element here.
function renderTeamFeedToggle(name) {
  const open = !!teamFeedOpen[name];
  return '<div class="team-feed-toggle-row"><a class="team-feed-toggle" onclick="toggleTeamFeed(' +
    "'" + name + "'" + ')">' + (open ? 'Hide live feed' : 'Show live feed') + '</a></div>';
}
// Positional fact_check-vs-finish disambiguation (docs/spec.md
// "Background"): a `tool_use` event with empty `meta` is ambiguous between
// a fact_check claim and a finish summary -- resolved by looking at the
// NEXT event in the LEAD's own transcript sequence (not the merged/
// interleaved buffer), never by content. `leadEvents` is the full buffer
// filtered to agent==='lead' and sorted by seq; object identity (not a
// seq/agent match) finds `e`'s own position, since the array elements are
// the very same objects the merged buffer holds.
function findNextLeadEvent(e, leadEvents) {
  const idx = leadEvents.indexOf(e);
  if (idx === -1) return null;
  return leadEvents[idx + 1] || null;
}
// `status` (team.status) is only consulted for the tool_use/empty-meta
// branch below (backlog item 12, part C) -- every other kind/meta
// combination disambiguates purely from the event's own shape and its
// position in leadEvents, same as before.
function teamFeedEventKindClass(e, leadEvents, status) {
  const meta = e.meta || {};
  if (e.kind === 'error') return 'error';
  // Chat-UI compose surface (backlog item 19 part 2, docs/spec.md "Proposed
  // approach" §2) -- a human-authored interjection gets its own row
  // classification (a left-border accent, not a chat-bubble redesign);
  // kind==='message' never matches any of the other branches below, so
  // this is safe placed early, before the final `return e.kind;` fallback.
  if (e.kind === 'message' && e.agent === 'human') return 'human-message';
  // "+" add-teammate control (backlog item 21 part 2, docs/spec.md
  // "Proposed approach" §3) -- kind==='member_joined' is structurally
  // disjoint from every other branch here (none of them check that kind
  // value), so placement doesn't matter beyond being before the final
  // `return e.kind;` fallback.
  if (e.kind === 'member_joined') return 'member-joined';
  // Board-write proposal/resolution (backlog item 7 part 2, docs/spec.md
  // §6 / docs/design.md) -- checked BEFORE the generic 'tool_result' +
  // meta.resolved -> 'resolved' branch below, since a board_write_resolved
  // transcript entry's own meta ALSO sets meta.resolved: true (part 1,
  // resolve_board_write()'s own transcript_entries -- see docs/spec.md
  // "Background") on both approve and reject. meta.verb/meta.approved are
  // never set on an ask_user transcript entry, so these two checks are
  // strictly narrower and never widen what the existing checks below match.
  if (e.kind === 'tool_use' && meta.verb !== undefined) return 'board-write-proposal';
  if (e.kind === 'tool_result' && meta.approved !== undefined) return 'board-write-resolved';
  if (e.kind === 'tool_result' && meta.found !== undefined) return 'fact-check-result';
  if (e.kind === 'tool_result' && meta.resolved) return 'resolved';
  if (e.kind === 'handoff') return 'handoff';
  if (e.kind === 'tool_use' && meta.header !== undefined) return 'ask-user';
  if (e.kind === 'tool_use' && Object.keys(meta).length === 0) {
    const next = findNextLeadEvent(e, leadEvents);
    if (next && next.kind === 'tool_result' && next.meta && next.meta.found !== undefined) {
      return 'fact-check-claim';
    }
    // Poll-boundary self-healing refinement (docs/spec.md "Background"
    // item C / docs/design.md is silent -- new for this cycle): `e` is the
    // event buffer's own LAST lead-agent event (no next lead event yet)
    // AND the run hasn't finished (team.status === 'running') -- the
    // paired tool_result (fact_check) or the terminal status (finish)
    // simply hasn't arrived on a poll yet, so render neither assumption
    // and wait for the next poll instead of guessing "finish". Once a
    // terminal status arrives (status !== 'running') or the paired
    // tool_result shows up (next is no longer null), this branch stops
    // matching and the disambiguation above resolves normally.
    if (!next && e.agent === 'lead' && status === 'running') return 'pending-classification';
    return 'finish';
  }
  if (e.kind === 'status' && meta.forced && meta.final_status) return 'terminal-escalation';
  return e.kind;
}
function teamFeedEventBody(e, leadEvents, status) {
  const meta = e.meta || {};
  const cls = teamFeedEventKindClass(e, leadEvents, status);
  // "+" add-teammate control (backlog item 21 part 2) -- the agent name
  // itself is already rendered by renderTeamFeedEvent()'s own existing
  // <span class="team-feed-agent">, no need to repeat it in the body text.
  if (cls === 'member-joined') return '→ joined the team';
  if (cls === 'board-write-proposal') {
    return 'board_write (' + esc(meta.verb || '') + '): ref #' + esc(meta.ref || '') + ' — ' + esc(e.text || '');
  }
  if (cls === 'board-write-resolved') {
    // Parses resolve_board_write()'s own literal outcome_summary/
    // full_result_text strings (docs/spec.md §6 -- "stable enough to
    // branch on") rather than reusing the generic 'Answer: ' + text copy.
    const text = e.text || '';
    if (meta.approved === false) {
      return '✕ Change rejected by human';
    }
    if (text.startsWith('approved and applied')) {
      return '✓ Change approved and applied';
    }
    const failMatch = /^approved but Taiga rejected the write: (.*)$/.exec(text);
    if (failMatch) {
      return '⚠ Change approved but Taiga rejected the write: ' + esc(failMatch[1]);
    }
    return '✓ Change approved';
  }
  if (cls === 'fact-check-claim') {
    return 'fact_check: ' + esc(e.text || '');
  }
  if (cls === 'fact-check-result') {
    let parsed = null;
    try { parsed = JSON.parse(e.text || '{}'); } catch (err) { parsed = null; }
    if (!parsed || !parsed.found) {
      return 'Fact-check result: <span class="team-feed-nomatch">✗ no supporting passage found</span>';
    }
    const matches = (parsed.matches || []).map(m => {
      const t = m.text || '';
      const shown = t.length > 200 ? t.slice(0, 200) + '…' : t;
      return '<div class="team-feed-match">✓ ' + esc(m.file_line || '') + ' — ' + esc(shown) + '</div>';
    }).join('');
    return 'Fact-check result:' + matches;
  }
  if (cls === 'resolved') return 'Answer: ' + esc(e.text || '');
  if (cls === 'handoff') return 'Delegating to ' + esc(meta.agent || '');
  if (cls === 'ask-user') return 'ask_user: ' + esc(meta.header || e.text || '');
  if (cls === 'pending-classification') return '⋯ pending…';
  if (cls === 'finish') return '[Finish summary] ' + esc(e.text || '');
  if (cls === 'terminal-escalation') {
    return '✕ Escalated: max rounds reached (' + esc(meta.final_status || '') + ')';
  }
  if (e.kind === 'error') return '✕ ' + esc(e.text || '');
  if (e.kind === 'message' || e.kind === 'status') return esc(e.text || '');
  return '[' + esc(e.kind || '') + '] ' + esc(e.text || '');
}
function renderTeamFeedEvent(e, leadEvents, status) {
  const color = teamAgentColor(e.agent);
  const ts = (e.ts || '').length >= 19 ? e.ts.slice(11, 19) : (e.ts || '');
  const kindClass = teamFeedEventKindClass(e, leadEvents, status);
  const body = teamFeedEventBody(e, leadEvents, status);
  // kind-member-joined's CSS border-left uses `currentColor` (docs/design.md
  // "the joined agent's own color"), which resolves against the element the
  // rule is DECLARED on -- the outer div below -- not a descendant's inline
  // style. Setting color only on the .team-feed-agent span (as this row
  // already does, for the name text) leaves the outer div's own color
  // unset/inherited, so the border renders neutral instead of per-agent
  // (docs/test-review.md, BACKLOG item 21 part 2 review, finding 1). Set the
  // inline style on the outer div itself for this one kind only -- every
  // other kind is untouched, matching kind-human-message's own hardcoded
  // (not currentColor-based) border color, which never had this problem.
  const borderStyle = kindClass === 'member-joined' ? ' style="border-left-color:' + color + '"' : '';
  return '<div class="team-feed-event kind-' + esc(kindClass) + '"' + borderStyle + '>' +
    '<span class="team-feed-ts">' + esc(ts) + '</span> ' +
    '<span class="team-feed-agent" style="color:' + color + '">' + esc(e.agent) + '</span> ' +
    '<span class="team-feed-text">' + body + '</span></div>';
}
function renderTeamFeed(name, team) {
  if (!teamFeedOpen[name]) return '';
  const events = teamFeedEvents[name] || [];
  const filter = teamFeedFilter[name] || 'all';
  // 'human' (backlog item 19 part 2, docs/spec.md "Proposed approach" §2)
  // is shown unconditionally, right after 'lead', matching lead's own
  // "always present" behavior -- no filtering code change needed, the
  // existing generic filter below already isolates human.jsonl's own
  // events once selected.
  // Backlog item 21 part 2, docs/spec.md "Proposed approach" §4 -- sourced
  // from the LIVE roster (team.members, /status's own live field) instead
  // of the stale team.composition.members (a saved/default PICKER
  // preference, never updated by add_team_member()); without this a newly
  // -added teammate never gets its own clickable filter pill.
  const agents = ['lead', 'human'].concat(team.members || []);
  const pills = ['all'].concat(agents).map(a => {
    const label = a === 'all' ? 'All' : a;
    const isActive = filter === a;
    const active = isActive ? ' active' : '';
    return '<button class="team-feed-pill' + active + '" aria-pressed="' + (isActive ? 'true' : 'false') +
      '" onclick="setTeamFeedFilter(' + "'" + name + "','" + a + "'" + ')">' + esc(label) + '</button>';
  }).join('');
  const filtered = filter === 'all' ? events : events.filter(e => e.agent === filter);
  const leadEvents = events.filter(e => e.agent === 'lead').sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const listHtml = filtered.length === 0 ? '<div class="team-feed-empty">No events yet.</div>' :
    filtered.map(e => renderTeamFeedEvent(e, leadEvents, team.status)).join('');
  // role="log"/aria-live="polite" (docs/design.md "Accessibility &
  // platform notes": "Event list items should be in an <article> or
  // similar container with role="log" and aria-live="polite" to announce
  // new events to screen readers") -- on the scrollable list container
  // itself (the element that actually gains new child rows on each poll),
  // not the outer .team-feed wrapper (which also holds the non-live filter
  // row).
  return '<div class="team-feed">' +
    '<div class="team-feed-filter">' + pills + '</div>' +
    '<div class="team-feed-list" role="log" aria-live="polite">' + listHtml + '</div></div>';
}
// Reopening always starts fresh from cursor={} (docs/spec.md "Edge cases":
// "reopening it starts from cursor {} again ... rather than trying to
// resume a stale cursor from before it was closed"), same as a full page
// reload's own behavior -- simpler, and no extra state to keep consistent.
function toggleTeamFeed(name) {
  const opening = !teamFeedOpen[name];
  teamFeedOpen[name] = opening;
  if (opening) {
    teamFeedCursor[name] = {};
    teamFeedEvents[name] = [];
  } else {
    delete teamFeedCursor[name];
    delete teamFeedEvents[name];
  }
  refresh();
}
// Client-side only -- filtering never refetches (docs/spec.md "Edge cases":
// "Switching the per-agent filter does not reset or refetch").
function setTeamFeedFilter(name, agent) {
  teamFeedFilter[name] = agent;
  refresh();
}
// Folded into refresh()'s existing 4s poll cycle (docs/spec.md "Proposed
// approach" §3), not a new setInterval -- called from refresh() itself for
// every project whose feed is open. Drains any `truncated: true` file
// immediately in a loop rather than waiting for the next 4s tick (docs/
// spec.md: "loop until no file reports truncated"). Deliberately does NOT
// call refresh() itself once the drain completes -- this app has no
// per-row incremental re-render, only the full refresh(), and calling it
// here would have refresh() invoke pollTeamFeed() again immediately (since
// the feed is still open), self-sustaining a tight fetch loop far faster
// than the intended 4s cadence. The freshly-appended events render on the
// next natural 4s tick instead -- well within "one subsequent poll
// interval", which is what every acceptance criterion here actually
// requires. teamFeedPolling[name] guards against two overlapping drains for
// the same project (e.g. a slow poll still in flight when the next tick
// fires).
async function pollTeamFeed(name) {
  if (teamFeedPolling[name]) return;
  teamFeedPolling[name] = true;
  try {
    let more = true;
    while (more) {
      const cursor = teamFeedCursor[name] || {};
      const cursorJson = encodeURIComponent(JSON.stringify(cursor));
      let r;
      try {
        r = await fetch('/projects/' + encodeURIComponent(name) + '/team/events?run_id=&cursor=' + cursorJson);
      } catch (e) {
        break;  // network hiccup -- the next 4s tick retries from the same cursor
      }
      if (!r.ok) break;
      const data = await r.json().catch(() => null);
      if (!data) break;
      if (data.events && data.events.length) {
        teamFeedEvents[name] = (teamFeedEvents[name] || []).concat(data.events);
        // Re-sort by (ts, agent, seq) on every append -- merges safely even
        // if two agents' events arrive slightly out of ts order across
        // polls (docs/spec.md "Proposed approach" §3). ts is a fixed-width
        // ISO-8601 UTC string (teams._now_iso()), so lexical compare sorts
        // chronologically.
        teamFeedEvents[name].sort((a, b) =>
          (a.ts || '').localeCompare(b.ts || '') || (a.agent || '').localeCompare(b.agent || '') ||
          (a.seq || 0) - (b.seq || 0));
        if (teamFeedEvents[name].length > 500) {
          teamFeedEvents[name] = teamFeedEvents[name].slice(-500);
        }
      }
      teamFeedCursor[name] = data.cursors || teamFeedCursor[name] || {};
      more = !!(data.truncated && Object.keys(data.truncated).some(k => data.truncated[k]));
    }
  } finally {
    teamFeedPolling[name] = false;
  }
}
// Client-side validation only (docs/design.md) -- the route's own 400 is
// the authoritative check either way, same discipline doTeamStart() already
// follows. Reuses toggle()'s TOTP-retry/code-overlay plumbing exactly like
// doTeamStart()/doTeamStop() above.
function doTeamResolve(name) {
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  const answer = computeTeamResolveAnswer(name);
  if (!answer || answer.length > 2000) {
    if (msgEl) {
      msgEl.textContent = 'Answer must be non-empty and at most 2000 characters';
      msgEl.className = 'team-msg error';
    }
    return;
  }
  toggle('team-resolve', name, true, null);
}
// Board-write proposal approve/reject (backlog item 7 part 2, docs/spec.md
// §5 / docs/design.md "Frontend: New doTeamBoardResolve() function") --
// parallel to doTeamResolve() above, no free-text client-side validation
// needed (resolve_board_write() takes no free text). teamBoardResolveAction
// is set BEFORE toggle()'s first optimistic (no-code) POST fires, so
// actionBody() can read it back on both that first attempt and any TOTP-
// retry attempt via submitActionCode() -- see its own declaration comment.
function doTeamBoardResolve(name, action) {
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  teamBoardResolveAction[name] = action;
  toggle('team-board-resolve', name, true, null);
}
// Chat-UI compose surface (backlog item 19 part 2, docs/spec.md "Proposed
// approach" §1) -- single source of truth for compose-box visibility, used
// both by renderTeamInterjectBox() below and by teamRow()'s own idle/
// non-idle branching indirectly (via team.status), matching exactly which
// statuses teams.interject() itself accepts server-side (running,
// blocked_ask_user, blocked_board_write -- i.e. team.status==='blocked' &&
// team.waiting_on_you), never escalated_max_rounds.
function teamAcceptsInterject(team) {
  return !!team && (team.status === 'running' ||
                     (team.status === 'blocked' && team.waiting_on_you));
}
// Narrow, direct-DOM update on oninput (matches updateTeamStartButton()'s
// own idiom above) -- no refresh() call here, so typing in the textarea
// never re-renders the row or loses focus/cursor position.
function updateTeamInterjectControls(name) {
  const btn = document.getElementById('interject-send-' + name);
  if (!btn) return;
  const text = teamInterjectText[name] || '';
  const len = text.length;
  const over = len > TEAM_INTERJECT_MAX_CHARS_CLIENT;
  btn.disabled = !text.trim() || over;
  const counterEl = document.getElementById('interject-counter-' + name);
  if (counterEl) {
    counterEl.textContent = len + '/' + TEAM_INTERJECT_MAX_CHARS_CLIENT;
    counterEl.className = 'team-interject-counter' + (over ? ' over-limit' : '');
  }
}
// Returns '' -- and proactively discards any stale draft -- whenever the
// current status doesn't accept an interjection (docs/spec.md "Edge cases":
// a status transition away from compose-eligible discards the unsent
// draft, no persist/restore). Placeholder copy is context-aware (docs/
// spec.md "Proposed approach" §3): when the escalation panel is also
// showing, the longer copy makes clear this box does NOT answer the
// pending question above.
function renderTeamInterjectBox(name, team) {
  if (!teamAcceptsInterject(team)) { delete teamInterjectText[name]; return ''; }
  const text = teamInterjectText[name] || '';
  const len = text.length;
  const over = len > TEAM_INTERJECT_MAX_CHARS_CLIENT;
  const disabled = !text.trim() || over;
  const placeholder = team.waiting_on_you ?
    'Send a message to the team (this will not answer the pending question above)…' :
    'Send a message to the team…';
  return '<div class="team-interject">' +
    '<div class="team-interject-row">' +
    '<textarea class="team-interject-textarea" id="interject-' + esc(name) + '" rows="2" ' +
    'placeholder="' + esc(placeholder) + '" oninput="teamInterjectText[' + "'" + name + "'" +
    '] = this.value; updateTeamInterjectControls(' + "'" + name + "'" + ');">' + esc(text) + '</textarea>' +
    '<button class="team-btn" id="interject-send-' + esc(name) + '"' + (disabled ? ' disabled' : '') +
    ' onclick="doTeamInterject(' + "'" + name + "'" + ')">Send</button>' +
    '</div>' +
    '<div class="team-interject-counter' + (over ? ' over-limit' : '') + '" id="interject-counter-' + esc(name) + '">' +
    len + '/' + TEAM_INTERJECT_MAX_CHARS_CLIENT + '</div></div>';
}
// Client-side validation only (docs/spec.md "Proposed approach" §1) -- the
// route's own 400 (app/app.py POST .../team/interject) is the authoritative
// check either way, same discipline doTeamResolve()/doTeamStart() already
// follow. Reuses toggle()'s TOTP-retry/code-overlay plumbing exactly like
// every other team-* action above.
function doTeamInterject(name) {
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  const text = (teamInterjectText[name] || '').trim();
  if (!text || text.length > TEAM_INTERJECT_MAX_CHARS_CLIENT) {
    if (msgEl) {
      msgEl.textContent = 'Message must be non-empty and at most ' + TEAM_INTERJECT_MAX_CHARS_CLIENT + ' characters';
      msgEl.className = 'team-msg error';
    }
    return;
  }
  toggle('team-interject', name, true, null);
}
// "+" add-teammate control (backlog item 21 part 2, docs/spec.md "Proposed
// approach" §5) -- pure, no I/O. `e.kind === 'engine'` mirrors
// add_team_member()'s own server-side rejection of the Ollama lead-only
// roster entry -- same rule, restated client-side for fast feedback, same
// "client mirrors server, server stays authoritative" discipline
// teamCompositionError() already documents for the Start-time picker.
function teamAddMemberEligible(team) {
  const already = new Set((team && team.members) || []);
  const leadName = team && team.lead && team.lead.kind === 'engine' ? team.lead.name : null;
  return ROSTER.filter(e => e.kind === 'engine' && e.name !== leadName && !already.has(e.name));
}
// Visibility gate reuses teamAcceptsInterject(team) as-is (docs/spec.md
// "Background": interject() and add_team_member() were both built to
// accept the identical status set -- running/blocked_ask_user/
// blocked_board_write) -- intentional reuse, not incidental.
function renderTeamAddMemberControl(name, team) {
  if (!teamAcceptsInterject(team)) return '';
  const members = (team && team.members) || [];
  const atCap = members.length >= (TEAM_MAX_MEMBERS_CLIENT || 6);
  if (atCap) {
    return '<div class="team-add-member"><span class="team-add-member-reason">' +
      'Team is at the maximum of ' + (TEAM_MAX_MEMBERS_CLIENT || 6) + ' teammates.</span></div>';
  }
  const eligible = teamAddMemberEligible(team);
  if (eligible.length === 0) {
    return '<div class="team-add-member"><span class="team-add-member-reason">' +
      'No more roster engines available to add.</span></div>';
  }
  const options = eligible.map(e =>
    '<option value="' + esc(e.name) + '">' + esc(e.name) + ' (' + tierLabel(e.tier) + ')</option>').join('');
  return '<div class="team-add-member">' +
    '<select id="team-add-member-select-' + esc(name) + '">' + options + '</select>' +
    '<button class="team-btn" onclick="doTeamAddMember(' + "'" + name + "'" + ')">+ Add</button></div>';
}
// Reuses toggle()'s TOTP-retry/code-overlay plumbing exactly like every
// other team-* action above -- teamAddMemberChoice[name] is set BEFORE
// toggle() fires so a 428-then-retry resends the same agent (docs/spec.md
// "Proposed approach" §5).
function doTeamAddMember(name) {
  const sel = document.getElementById('team-add-member-select-' + name);
  if (!sel || !sel.value) return;
  teamAddMemberChoice[name] = sel.value;
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  toggle('team-add-member', name, true, null);
}
function teamRow(name, team) {
  const msgSlot = '<div class="team-msg" id="team-msg-' + esc(name) + '"></div>';
  if (!team || team.status === 'idle') {
    clearTeamFeedState(name);
    const text = teamTaskText[name] || '';
    const taskArea = '<textarea class="team-textarea" id="task-' + esc(name) + '" placeholder="Task description..." ' +
      'oninput="teamTaskText[' + "'" + name + "'" + '] = this.value; ' +
      "updateTeamStartButton('" + esc(name) + "');" + '">' +
      esc(text) + '</textarea>';
    // team === null is a defensive-only shape (the real /status always
    // sends an object per project, see docs/spec.md 6d) -- the picker only
    // applies once there IS a team object to read inst.team.composition
    // off of, so `composition` stays `undefined` (not `null`) in that
    // case, which renders the same plain row 6d always has.
    const composition = team ? team.composition : undefined;
    if (composition === null) {
      // docs/spec.md "no usable roster member at all" -- picker area shows
      // the refusal text, Start button omitted entirely, not a broken/
      // empty picker.
      return '<div class="team-row">' + taskArea +
        '<div class="team-msg error">✕ No roster members available. Add an engine to engines.d ' +
        'or configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL.</div>' +
        '<div class="team-actions"><button class="team-btn" id="start-btn-' + esc(name) + '" disabled>' +
        'Start team</button></div>' + msgSlot + renderTeamBranches(name) + '</div>';
    }
    const open = composition !== undefined && !!teamPickerOpen[name];
    const configureRow = composition !== undefined ?
      '<div class="team-configure-row"><a class="team-configure-btn" onclick="toggleTeamPicker(' +
      "'" + name + "'" + ')">' + (open ? 'Hide configuration' : 'Configure team...') + '</a></div>' : '';
    const picker = open ? renderTeamPicker(name) : '';
    const startDisabled = !text.trim() || (open && !!teamCompositionError(name));
    return '<div class="team-row">' + taskArea + configureRow + picker +
      '<div class="team-actions"><button class="team-btn" id="start-btn-' + esc(name) + '"' +
      (startDisabled ? ' disabled' : '') +
      ' onclick="doTeamStart(' + "'" + name + "'" + ')">Start team</button></div>' +
      msgSlot + renderTeamBranches(name) + '</div>';
  }
  // Overwatch feed + escalation inbox (backlog item 6f part 2, docs/
  // spec.md / docs/design.md) -- 4-state status strip, an escalation-answer
  // panel when waiting_on_you, and the collapsible merged event feed
  // (default OPEN the first time this project's row renders non-idle --
  // docs/design.md "expanded by default whenever team.status !== 'idle'",
  // seeded once here the same way teamPickerInitialized seeds the
  // composition picker's own pre-selection once above).
  if (teamFeedOpen[name] === undefined) teamFeedOpen[name] = true;
  const statusStrip = renderTeamStatusStrip(team);
  const escalatedNote = (team.status === 'blocked' && !team.waiting_on_you) ?
    '<div class="team-sub">Escalated — max rounds reached. No pending question to answer. ' +
    'Review the feed below or Stop team and start a new run.</div>' : '';
  const escalationPanel = team.waiting_on_you ? renderEscalationPanel(name, team) : '';
  // Chat-UI compose surface (backlog item 19 part 2, docs/spec.md "Proposed
  // approach" §1) -- positioned between the escalation panel and the feed
  // toggle: escalation resolution (answering a specific pending question)
  // stays visually first when present; the always-available free-form
  // channel sits directly below it; both sit above the passive, scrollable
  // log feed.
  const interjectBox = renderTeamInterjectBox(name, team);
  // "+" add-teammate control (backlog item 21 part 2, docs/spec.md
  // "Proposed approach" §5) -- its own visual block, between the compose
  // box and the feed toggle (not inside .team-actions, which stays
  // reserved for the single "Stop team" button per existing convention).
  const addMemberControl = renderTeamAddMemberControl(name, team);
  const feedToggle = renderTeamFeedToggle(name);
  const feedPanel = renderTeamFeed(name, team);
  return '<div class="team-row">' + statusStrip + escalatedNote + escalationPanel +
    interjectBox + addMemberControl + feedToggle + feedPanel +
    '<div class="team-actions"><button class="team-btn" onclick="doTeamStop(' +
    "'" + name + "'" + ')">Stop team</button></div>' +
    msgSlot + renderTeamBranches(name) + '</div>';
}
function row(label, on, url, kind, name, desc, engine, codeOn, codeUrl, subOverride, showBadge, gitSync, deploy, team) {
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
    (kind === 'inst' ? smokeCheckRow(name, url) : '') +
    (kind === 'inst' ? deployRow(name, deploy) : '') +
    (kind === 'inst' ? teamRow(name, team) : '') +
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
  if (kind === 'clone') return '/projects/clone';
  if (kind === 'deploy') return '/instance/' + encodeURIComponent(name) + '/deploy';
  if (kind === 'team-start') return '/projects/' + encodeURIComponent(name) + '/team/start';
  if (kind === 'team-stop') return '/projects/' + encodeURIComponent(name) + '/team/stop';
  if (kind === 'team-resolve') return '/projects/' + encodeURIComponent(name) + '/team/resolve';
  if (kind === 'team-board-resolve') return '/projects/' + encodeURIComponent(name) + '/team/board-resolve';
  if (kind === 'team-interject') return '/projects/' + encodeURIComponent(name) + '/team/interject';
  if (kind === 'team-add-member') return '/projects/' + encodeURIComponent(name) + '/team/add-member';
  if (kind === 'smoke-check') return '/projects/' + encodeURIComponent(name) + '/smoke-check';
  return '/instance/' + encodeURIComponent(name) + '/' + (on ? 'on' : 'off');
}
function actionBody(kind, name, on, code) {
  const body = {};
  if (code) body.code = code;
  if (on && kind === 'inst') body.engine = engineChoice[name] || Object.keys(ENGINE_LABELS)[0];
  if (kind === 'newproject') body.name = name;
  // Clone-from-URL (backlog item 16, docs/spec.md/docs/design.md) -- reads
  // straight from the still-live inputs, same "survives a TOTP retry"
  // discipline team-start's own task/lead/members fields already rely on
  // above, rather than threading url/name through toggle()'s own
  // name/on/checkboxEl parameters (which don't have a slot for a second
  // string like this).
  if (kind === 'clone') {
    body.url = document.getElementById('clone-url').value.trim();
    body.name = (document.getElementById('clone-name').value || '').trim();
  }
  // Team start's task text isn't shaped like any other kind's body field --
  // read directly from the still-live textarea (or the client-side
  // teamTaskText[] mirror, in case a TOTP retry's own submitActionCode()
  // path fires after the row has already re-rendered) so a retry after a
  // 428 sends the operator's current text, not a stale snapshot.
  if (kind === 'team-start') {
    const el = document.getElementById('task-' + name);
    body.task = (el ? el.value : (teamTaskText[name] || '')).trim();
    // Roster & composition UI (backlog item 6e, docs/design.md
    // "Implementation notes" §7): only included once the picker has been
    // opened AND a valid composition is selected -- omitted entirely
    // otherwise, so a start with the picker never touched (or left
    // invalid) stays byte-for-byte the 6d default-composition body a
    // stale/old client would also send (docs/spec.md "backward
    // compatible"). doTeamStart() already refuses to dispatch at all on an
    // invalid composition, so this condition is a defense-in-depth repeat
    // of that same check, not the only thing guarding it.
    if (teamPickerOpen[name] && !teamCompositionError(name)) {
      body.lead = teamPickerLead[name];
      body.members = Array.from(teamPickerMembers[name] || []).map(n => ({kind: 'engine', name: n}));
    }
  }
  // Live event feed + escalation inbox (backlog item 6f part 2, docs/
  // spec.md "Proposed approach" §2) -- computeTeamResolveAnswer() is the
  // single shared implementation of the free-text-wins/else-labels-joined
  // convention, also used by doTeamResolve()'s own client-side validation,
  // so the two can never diverge.
  if (kind === 'team-resolve') body.answer = computeTeamResolveAnswer(name);
  // Board-write proposal approve/reject (backlog item 7 part 2, docs/
  // spec.md §5) -- sourced from the client-side map doTeamBoardResolve()
  // populates BEFORE dispatching, same "survives a TOTP retry" discipline
  // team-start's own task/lead/members fields already rely on above.
  if (kind === 'team-board-resolve') body.action = teamBoardResolveAction[name];
  // Chat-UI compose surface (backlog item 19 part 2, docs/spec.md "Proposed
  // approach" §1) -- reads the live textarea first, falls back to the
  // teamInterjectText[] mirror, same "survives a mid-flow re-render/428
  // retry" reasoning team-start's own task-text field already relies on.
  if (kind === 'team-interject') {
    const el = document.getElementById('interject-' + name);
    body.text = (el ? el.value : (teamInterjectText[name] || '')).trim();
  }
  // "+" add-teammate control (backlog item 21 part 2, docs/spec.md
  // "Proposed approach" §5) -- sourced from the client-side map
  // doTeamAddMember() populates BEFORE dispatching (same "survives a TOTP
  // retry" discipline team-board-resolve's own action field already relies
  // on above), never a re-read of the (possibly-already-redrawn) <select>.
  if (kind === 'team-add-member') body.agent = teamAddMemberChoice[name];
  // HTTP-level smoke check (backlog item 18, docs/spec.md) -- reads the
  // still-live input first, falls back to the smokeCheckExpect[] mirror,
  // same "survives a mid-flow re-render/428 retry" reasoning team-start's
  // own task-text field already relies on above. Always sent (possibly
  // empty) -- an empty string is the documented "don't check content" case,
  // not an omitted field.
  if (kind === 'smoke-check') {
    const el = document.getElementById('smoke-expect-' + name);
    body.expect_contains = (el ? el.value : (smokeCheckExpect[name] || '')).trim();
  }
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
      kind === 'team-start' ? 'Starting team: ' + (name || 'this') :
      kind === 'team-stop' ? 'Stopping team: ' + (name || 'this') :
      kind === 'team-resolve' ? 'Submitting answer: ' + (name || 'this') :
      kind === 'team-board-resolve' ? 'Resolving board write: ' + (name || 'this') :
      kind === 'team-interject' ? 'Sending message: ' + (name || 'this') :
      kind === 'team-add-member' ? 'Adding teammate: ' + (name || 'this') :
      kind === 'smoke-check' ? 'Smoke checking: ' + (name || 'this') :
      kind === 'clone' ? 'Cloning from URL' :
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
  if (kind === 'team-start' || kind === 'team-stop') {
    // Its own inline result slot (docs/design.md "Error (Failed Start)"/
    // "Stop Result Message") — handled BEFORE the generic 400 branch below,
    // since /team/start's own validation failures (empty task, tier-3-only
    // refusal, no roster member) surface as a 400 that belongs in THIS
    // row's own message slot, not the new-project error field.
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (kind === 'team-start') {
        if (r.ok && data.ok) {
          msgEl.textContent = '';
          msgEl.className = 'team-msg';
        } else {
          msgEl.textContent = '✕ Error: ' + (data.error || 'could not start team');
          msgEl.className = 'team-msg error';
        }
      } else {
        msgEl.textContent = r.ok ? '✓ Team stopped successfully' : '✕ Error: ' + (data.error || 'could not stop team');
        msgEl.className = 'team-msg ' + (r.ok ? 'success' : 'error');
      }
    }
    return;
  }
  if (kind === 'team-resolve') {
    // Its own inline result slot (docs/design.md "Escalation Panel" /
    // "Message slot"), same pattern as team-start/team-stop above -- and
    // handled before the generic 400 branch below for the same reason
    // (an over-length/empty-answer 400 belongs in THIS row's own
    // team-msg slot, not the new-project error field).
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (r.ok && data.ok) {
        msgEl.textContent = '✓ Answer submitted';
        msgEl.className = 'team-msg success';
        const team = TEAM_BY_NAME[name];
        if (team && team.run_id) delete teamInboxCache[team.run_id];
        delete teamEscalationSelected[name];
        delete teamEscalationOther[name];
      } else {
        msgEl.textContent = '✕ Error: ' + (data.error || 'could not submit answer');
        msgEl.className = 'team-msg error';
      }
    }
    return;
  }
  if (kind === 'team-board-resolve') {
    // Its own inline result slot (docs/design.md "Frontend:
    // handleActionResult() extension"), same pattern as team-resolve above
    // -- handled before the generic 400 branch below for the same reason
    // (a wrong-status/invalid-action/two-tab-race 400 belongs in THIS
    // row's own team-msg slot, not the new-project error field).
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (r.ok && data.ok) {
        msgEl.textContent = '✓ Board write resolved';
        msgEl.className = 'team-msg success';
        const team = TEAM_BY_NAME[name];
        if (team && team.run_id) delete teamInboxCache[team.run_id];
        delete teamBoardResolveAction[name];
      } else {
        msgEl.textContent = '✕ Error: ' + (data.error || 'could not resolve board write');
        msgEl.className = 'team-msg error';
      }
    }
    return;
  }
  if (kind === 'team-interject') {
    // Its own inline result slot (docs/design.md "Compose box: Success" /
    // "Compose box: Error"), same pattern as team-resolve/team-board-resolve
    // above -- handled before the generic 400 branch below for the same
    // reason (an over-length/empty-message 400 belongs in THIS row's own
    // team-msg slot, not the new-project error field).
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (r.ok && data.ok) {
        msgEl.textContent = '✓ Message sent';
        msgEl.className = 'team-msg success';
        delete teamInterjectText[name];
        const ta = document.getElementById('interject-' + name);
        if (ta) ta.value = '';
        updateTeamInterjectControls(name);
      } else {
        // Draft text is deliberately NOT cleared on failure -- the operator
        // can fix and resend without retyping.
        msgEl.textContent = '✕ Error: ' + (data.error || 'could not send message');
        msgEl.className = 'team-msg error';
      }
    }
    return;
  }
  if (kind === 'team-add-member') {
    // Its own inline result slot (docs/design.md "Success Feedback" /
    // "Error Feedback"), same pattern as team-interject above -- handled
    // before the generic 400 branch below for the same reason (a
    // cap-reached/already-a-member/race 400 belongs in THIS row's own
    // team-msg slot, not the new-project error field). Deliberately says
    // "will join... at its next round," never "has joined" -- the new
    // teammate isn't actually reachable until team_step()'s own membership
    // drain runs at the run's next round boundary (docs/spec.md's own
    // "accurate, non-oversold feedback" goal).
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (r.ok && data.ok) {
        msgEl.textContent = '✓ \\'' + esc(data.agent) + '\\' will join the team at its next round';
        msgEl.className = 'team-msg success';
        delete teamAddMemberChoice[name];
      } else {
        msgEl.textContent = '✕ Error: ' + (data.error || 'could not add teammate');
        msgEl.className = 'team-msg error';
      }
    }
    return;
  }
  if (kind === 'clone') {
    // Its own inline result slot (docs/design.md "Error States" /
    // "Success State"), same pattern as team-start/deploy above -- handled
    // before the generic 400 branch below for the same reason (a
    // clone-specific 400 belongs in THIS row's own clone-err slot, not the
    // new-project error field).
    hideCodeOverlay();
    setCloneFormBusy(false);
    const data = await r.json().catch(() => ({}));
    const errEl = document.getElementById('clone-err');
    if (r.ok && data.ok) {
      errEl.textContent = '';
      errEl.className = 'clone-err';
      document.getElementById('clone-url').value = '';
      document.getElementById('clone-name').value = '';
      closeCloneForm();
      setTimeout(refresh, 1500);
    } else {
      errEl.className = 'clone-err';
      errEl.textContent = data.error || 'Clone failed.';
    }
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
  if (kind === 'smoke-check') {
    // Its own inline result slot (docs/spec.md "Proposed approach") --
    // never the generic setTimeout(refresh, 1500) below, same "persist
    // until the next refresh() re-render, not wiped almost immediately"
    // reasoning the deploy branch above already follows. Reads data.ok,
    // NOT r.ok -- the route answers HTTP 200 for both a successful AND a
    // target-side-failed completed check (docs/spec.md: "a completed
    // check (success or target-side failure alike)... 200"); only a
    // locked-out (409) or unknown-project (404) dispatch has r.ok false
    // here, and those still carry a plain {ok: false, error} body this
    // same branch renders correctly either way.
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('smoke-check-msg-' + name);
    if (msgEl) {
      if (data.ok) {
        let text = data.status_code + ' · ' + data.elapsed_ms + 'ms';
        if (data.content_ok !== null && data.content_ok !== undefined) {
          text += data.content_ok ? ' · content: found' : ' · content: NOT found';
        }
        msgEl.textContent = text;
        msgEl.className = 'smoke-check-msg success';
      } else {
        msgEl.textContent = data.error || 'Smoke check failed.';
        msgEl.className = 'smoke-check-msg error';
      }
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
// Manual, one-click HTTP-level smoke check (backlog item 18, docs/spec.md)
// -- same toggle()/performAction()/handleActionResult() plumbing doDeploy()
// above uses (including the shared TOTP code-overlay retry path), but with
// NO confirm() dialog: unlike Deploy (mutates a remote target) or Stop team
// (kills processes and removes worktrees), a GET request against the
// project's own already-running dev server has no side effect worth a
// confirmation interruption.
function doSmokeCheck(name) {
  const msgEl = document.getElementById('smoke-check-msg-' + name);
  if (msgEl) { msgEl.textContent = 'Checking…'; msgEl.className = 'smoke-check-msg'; }
  toggle('smoke-check', name, true, null);
}
// Minimal per-project team control (backlog item 6d, part 2a; extended with
// a composition picker in 6e -- docs/design.md). Client-side validation
// only (the route's own 400 is the real, authoritative check either way)
// -- reuses toggle()'s own TOTP-retry/code-overlay plumbing exactly like
// doDeploy() above, just with actionBody()'s own kind==='team-start'
// branch supplying the {task, lead, members} body every other kind's shape
// doesn't need.
function doTeamStart(name) {
  const el = document.getElementById('task-' + name);
  const task = (el ? el.value : '').trim();
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  if (!task) {
    if (msgEl) { msgEl.textContent = 'Enter a task description.'; msgEl.className = 'team-msg error'; }
    return;
  }
  if (teamPickerOpen[name]) {
    const err = teamCompositionError(name);
    if (err) {
      if (msgEl) { msgEl.textContent = err; msgEl.className = 'team-msg error'; }
      return;
    }
  }
  toggle('team-start', name, true, null);
}
// Confirmation via native confirm() (docs/design.md "Stop Confirmation
// Dialog") -- stopping a team is destructive (kills in-flight processes,
// removes git worktrees, may discard uncommitted work), same lightweight-
// confirmation precedent doDeploy() already sets for a different
// destructive action.
function doTeamStop(name) {
  if (!confirm('Stop team? This will kill any in-flight processes, remove git worktrees, and stop the running session. Any uncommitted work will be lost. Continue?')) {
    return;
  }
  toggle('team-stop', name, true, null);
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
// Clone from URL (backlog item 16, docs/design.md "Component reuse") --
// same inline-form/actionPath()/actionBody()/toggle() plumbing as
// startNewProject() above, extended with a disabled/"Cloning…" loading
// state (docs/design.md "Loading State") since a clone can legitimately
// take up to CLONE_TIMEOUT_SECONDS (180s default), unlike + New project's
// near-instant response.
function openCloneForm() {
  document.getElementById('clone-form').style.display = 'flex';
  document.getElementById('clone-err').textContent = '';
  document.getElementById('clone-url').focus();
}
function closeCloneForm() {
  document.getElementById('clone-form').style.display = 'none';
}
function setCloneFormBusy(busy) {
  document.getElementById('clone-url').disabled = busy;
  document.getElementById('clone-name').disabled = busy;
  document.querySelectorAll('#clone-form button').forEach(b => { b.disabled = busy; });
  const btn = document.querySelector('#clone-form button');
  if (btn) btn.textContent = busy ? 'Cloning…' : 'Clone';
}
function startClone() {
  const url = document.getElementById('clone-url').value.trim();
  const errEl = document.getElementById('clone-err');
  errEl.textContent = '';
  errEl.className = 'clone-err';
  if (!url) {
    errEl.textContent = 'Enter a repository URL.';
    return;
  }
  setCloneFormBusy(true);
  errEl.textContent = 'Cloning… this can take a while for large repositories (up to a few minutes).';
  errEl.className = 'clone-status';
  toggle('clone', url, true, null);
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
  if (pendingToggle && pendingToggle.kind === 'clone') {
    // Nothing actually started server-side either (same reasoning as
    // above) -- re-enable the form so the operator can retry.
    setCloneFormBusy(false);
    const errEl = document.getElementById('clone-err');
    errEl.textContent = '';
    errEl.className = 'clone-err';
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
            # Roster & composition UI (backlog item 6e, docs/spec.md) --
            # global, not per-project, computed once per poll next to the
            # existing load_engines() call above. roster() re-reads
            # load_engines() itself, duplicating one directory scan per
            # poll -- the same accepted cost default_team_composition()
            # already carries by calling roster() internally, not worth
            # threading a pre-loaded engines dict through for.
            roster = teams.roster()
            # Read once per /status call, same "avoid staleness after an
            # operator edits it" reasoning _load_deploy_map()/_load_gitea_
            # repo_map() already establish -- used below, per project, to
            # populate inst.team.composition.
            compositions = teams.load_compositions()
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
            # GitHub's own poll-loop-doubling-as-item-8-dispatch counterpart
            # (item 17 part 2, docs/spec.md) -- internally throttled to its
            # own GITHUB_POLL_INTERVAL_SECONDS interval, and a guaranteed
            # no-op unless AI_REVIEWER_ENABLED, GITHUB_TOKEN, and a non-empty
            # AI_REVIEWER_GITHUB_REPOS_FILE are all set.
            _github_poll_if_due()
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
                # Team session lifecycle, part 2a (backlog item 6d,
                # docs/spec.md §5) -- always present (unlike deploy/
                # gitea_sync, which are only attached when configured), same
                # "always-present" treatment on/engine already get. A fresh
                # read on every poll -- deliberately unthrottled, unlike
                # sweep_dead_teams() below (see _team_reap_if_due()) --
                # since a team started seconds ago must not still show
                # "idle".
                run = teams.latest_run_for_project(n)
                team_status = ("idle" if run is None else
                              {"running": "running", "blocked_ask_user": "blocked",
                               "blocked_board_write": "blocked",
                               "escalated_max_rounds": "blocked", "finished": "finished",
                               "error": "error", "stopped": "idle"}.get(run["status"], "idle"))
                # Roster & composition UI (backlog item 6e, docs/spec.md) --
                # what the picker pre-selects: the saved composition if one
                # exists, else default_team_composition()'s own pick if it
                # can produce one, else None (no roster member at all can
                # lead). Only meaningful when status=="idle" but cheap
                # enough to compute unconditionally, consistent with
                # team's own existing "always present" treatment.
                saved_comp = compositions.get(n)
                if saved_comp is not None:
                    composition = {"lead": saved_comp["lead"], "members": saved_comp["members"]}
                else:
                    default_comp = teams.default_team_composition()
                    if default_comp["ok"]:
                        composition = {"lead": default_comp["lead"], "members": default_comp["members"]}
                    elif roster:
                        # Reviewer fix (2026-08-14): default_team_composition()
                        # refuses for three distinct reasons -- an empty
                        # roster, a single already-picked-lead engine with
                        # nothing left to delegate to, or (the case this
                        # branch exists for) a roster that's real but
                        # tier-3-only, which 6d part 2 settled must never be
                        # auto-picked as the DEFAULT lead. Collapsing all
                        # three into composition=None broke this sub-spec's
                        # own headline acceptance criterion for the third
                        # case: the frontend's `composition === null` check
                        # can't tell "nothing to show" apart from "something
                        # real exists, just not an automatic pick", so it
                        # rendered a permanently-disabled Start button with
                        # no way to ever open the picker for a tier-3-only
                        # roster with no saved composition yet. `roster`
                        # (this same /status call's own top-level list,
                        # computed once above) is the authoritative "does a
                        # real, pickable member exist at all" signal --
                        # independent of whether the automatic default could
                        # use one. When it's non-empty, the picker must still
                        # be openable, so composition is a real (non-None)
                        # object with nothing pre-selected -- `lead: None`
                        # mirrors docs/design.md's own "Choose a lead..."
                        # empty-select default, letting the operator pick
                        # explicitly instead of the automatic default that
                        # just declined to.
                        composition = {"lead": None, "members": []}
                    else:
                        composition = None
                # Overwatch feed + escalation inbox (backlog item 6f part 1,
                # docs/spec.md §4) -- additive only. True iff a human can
                # actually resolve this project's current run right now via
                # POST .../team/resolve -- deliberately NOT true for
                # "escalated_max_rounds", a terminal status with no
                # inbox.json and nothing to resume (docs/spec.md "Open
                # questions"), which stays under the coarser "blocked"
                # team_status bucket above instead.
                waiting_on_you = run is not None and run["status"] in (
                    "blocked_ask_user", "blocked_board_write")
                # escalation_kind (backlog item 7 part 2, docs/spec.md §1) --
                # a direct string comparison against run["status"], already
                # loaded above for team_status/waiting_on_you; distinguishes
                # the two escalation kinds so the frontend can render
                # different status-strip copy/panel without an extra round
                # trip to GET .../team/inbox.
                escalation_kind = (
                    "ask_user" if run is not None and run["status"] == "blocked_ask_user" else
                    "board_write" if run is not None and run["status"] == "blocked_board_write" else
                    None)
                inst["team"] = {"status": team_status, "run_id": run["run_id"] if run else None,
                                "composition": composition, "waiting_on_you": waiting_on_you,
                                "escalation_kind": escalation_kind,
                                # Backlog item 21 part 2, docs/spec.md
                                # "Proposed approach" §1 -- the run's LIVE
                                # roster/lead, read directly off the
                                # persisted state dict rather than
                                # re-derived from `composition` above
                                # (a saved/default PICKER preference, never
                                # updated by add_team_member()). Grows the
                                # moment team_step()'s membership drain
                                # runs, never earlier -- matching part 1's
                                # own "next round boundary" delivery
                                # semantics.
                                "members": run.get("members", []) if run is not None else [],
                                "lead": run.get("lead") if run is not None else None}
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
                       "roster": roster,
                       # Backlog item 21 part 2, docs/spec.md "Proposed
                       # approach" §1 -- a single process-wide constant, not
                       # per-project, same "computed once, shipped once per
                       # /status call" treatment `roster` itself gets above.
                       "team_max_members": teams.TEAM_MAX_MEMBERS,
                       "host_enabled": HOST_CONTROL_ENABLED, "host_label": HOST_LABEL,
                       "host": host_on, "host_url": host_url,
                       "taiga_enabled": TAIGA_ENABLED, "taiga_label": TAIGA_LABEL,
                       "taiga": taiga_on, "taiga_url": taiga_url,
                       "gitea_enabled": GITEA_ENABLED, "gitea_label": GITEA_LABEL,
                       "gitea": gitea_on, "gitea_url": gitea_url})
        else:
            # Roster & composition UI (backlog item 6e, docs/spec.md) --
            # GET /projects/<name>/team/grounding, read-only discovery
            # metadata only (never content/digest/headings, which would
            # ship a project's full doc text to the browser for what's
            # meant to be a before-you-start summary, not a viewer). No
            # TOTP needed, matching /status's own gating -- _authed() only,
            # already checked above. Also: overwatch feed + escalation
            # inbox (backlog item 6f part 1, docs/spec.md §1/§2) --
            # /team/events and /team/inbox, the first GET routes to carry a
            # query string (?run_id=/?cursor=), routed via urllib.parse.
            # urlsplit()/parse_qs() the same way /projects/upload's own POST
            # branch and the shared TOTP `?code=` parsing already do.
            split = urllib.parse.urlsplit(self.path)
            parts = [unquote(p) for p in split.path.strip("/").split("/")]
            query = urllib.parse.parse_qs(split.query)
            if len(parts) == 4 and parts[0] == "projects" and parts[2] == "team" and parts[3] == "grounding":
                name = parts[1]
                if name not in instance_names():
                    return self._json({"error": "unknown project"}, 404)
                g = teams.load_grounding(os.path.join(PROJECTS_DIR, name))
                files = [{"label": f["label"], "relpath": f["relpath"], "byte_count": f["byte_count"]}
                        for f in g["files"]]
                return self._json({"files": files, "skipped": g["skipped"]})
            if len(parts) == 4 and parts[0] == "projects" and parts[2] == "team" and parts[3] == "events":
                return self._handle_team_events(parts[1], query)
            if len(parts) == 4 and parts[0] == "projects" and parts[2] == "team" and parts[3] == "inbox":
                return self._handle_team_inbox(parts[1], query)
            if len(parts) == 4 and parts[0] == "projects" and parts[2] == "team" and parts[3] == "branches":
                # Backlog item 13, docs/spec.md -- read-only surviving-
                # branch discoverability. Same gating as /team/grounding
                # above: no TOTP needed (_authed() only, already checked at
                # the top of do_GET), same project-scoping 404.
                name = parts[1]
                if name not in instance_names():
                    return self._json({"error": "unknown project"}, 404)
                return self._json(teams.list_team_branches(os.path.join(PROJECTS_DIR, name)))
            self.send_response(404)
            self.end_headers()

    def _team_events_run_and_ownership(self, name: str, query: dict):
        """
        Shared "which run, does the caller own it" resolution for both GET
        .../team/events and GET .../team/inbox (docs/spec.md §1/§2, same
        run_id-defaults-to-latest, same ownership check). Returns (state,
        error_response) -- exactly one of the two is non-None: state is the
        resolved run's persisted dict (possibly None with no error, meaning
        "no run exists yet for this project" -- not an error for either
        route), error_response is an already-built (payload, status) tuple
        the caller should return via self._json(*error_response) for an
        unknown project or a cross-project run_id.
        """
        if name not in instance_names():
            return None, ({"error": "unknown project"}, 404)
        run_id = (query.get("run_id") or [None])[0]
        if run_id:
            # docs/BACKLOG.md item 11(b): validated against teams._RUN_ID_RE
            # -- the exact shape teams._run_id() actually generates -- here,
            # at the client-supplied-run_id intake point, BEFORE it ever
            # reaches teams.load_state_for_project()/_load_state()/
            # _run_dir() and a path-join. Same "unknown run_id" 404 as a
            # syntactically-valid-but-nonexistent run_id already gets, so a
            # malformed/traversal run_id (e.g. "../../outside/evilrun")
            # never opens any file outside _leads_root() and never gets a
            # distinguishable error shape.
            if not teams._RUN_ID_RE.match(run_id):
                return None, ({"error": "unknown run_id for this project"}, 404)
            state = teams.load_state_for_project(run_id, name)
            if state is None:
                return None, ({"error": "unknown run_id for this project"}, 404)
            return state, None
        return teams.latest_run_for_project(name), None

    def _handle_team_events(self, name: str, query: dict):
        """
        GET /projects/<name>/team/events (backlog item 6f part 1, docs/
        spec.md §1) -- cursor-based, per-file byte-capped merge of a run's
        lead transcript.jsonl and every teammate's own agents/<agent>.jsonl,
        chronologically sorted. No TOTP needed, read-only, same gating as
        /team/grounding above.
        """
        state, err = self._team_events_run_and_ownership(name, query)
        if err is not None:
            return self._json(*err)
        if state is None:
            return self._json({"run_id": None, "events": [], "cursors": {}})
        run_id = state["run_id"]
        cursor = _parse_events_cursor((query.get("cursor") or [None])[0])
        # "human" (backlog item 19 part 1, docs/spec.md "Proposed approach"
        # §4): a run's own human.jsonl, merged in exactly like a teammate's
        # own log -- no other change to this function, the existing
        # per-file cursor/byte-cap/truncation-flag/chronological-merge-sort
        # logic below is already generic over the file list.
        # "membership" (backlog item 21 part 2, docs/spec.md "Proposed
        # approach" §2) -- merges add_team_member()'s own member_joined
        # envelopes in. The "membership" label here is only used for the
        # malformed-line fallback and this file's own cursors-dict key; it
        # does NOT override the `agent` field already embedded in each
        # membership.jsonl line (the newly-joined agent's own name), so a
        # member_joined event surfaces tagged with that agent's name/color,
        # not a generic "membership" pseudo-agent.
        files = [("lead", teams._transcript_path(run_id)), ("human", teams._human_log_path(run_id)),
                 ("membership", teams._membership_log_path(run_id))]
        files += [(m, teams._agent_log_path(run_id, m)) for m in state.get("members", [])]
        all_events = []
        cursors = {}
        truncated = {}
        for agent, path in files:
            offset = cursor.get(agent, 0)
            events, new_offset, was_truncated = teams.tail_jsonl_events(
                path, offset, teams.TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL, agent=agent)
            all_events.extend(events)
            cursors[agent] = new_offset
            if was_truncated:
                truncated[agent] = True
        all_events.sort(key=lambda e: (e.get("ts", ""), e.get("agent", ""), e.get("seq", 0)))
        self._json({"run_id": run_id, "events": all_events, "cursors": cursors, "truncated": truncated})

    def _handle_team_inbox(self, name: str, query: dict):
        """
        GET /projects/<name>/team/inbox (backlog item 6f part 1, docs/
        spec.md §2; extended by backlog item 7 part 2, docs/spec.md §2 for
        the board_write branch below) -- "is there a pending question/
        proposal right now", without the caller having to scan the merged
        event feed for the latest ask_user/board_write entry itself.
        """
        state, err = self._team_events_run_and_ownership(name, query)
        if err is not None:
            return self._json(*err)
        if state is None:
            return self._json({"pending": False})
        if state.get("status") == "blocked_board_write":
            return self._handle_team_inbox_board_write(state)
        if state.get("status") != "blocked_ask_user":
            return self._json({"pending": False})
        run_id = state["run_id"]
        question = header = None
        options, multi_select = [], False
        try:
            with open(teams._inbox_path(run_id)) as f:
                inbox = json.load(f)
            question = inbox.get("question") or None
            header = inbox.get("header") or ""
            options = inbox.get("options") or []
            multi_select = bool(inbox.get("multi_select", False))
        except (OSError, ValueError):
            question = None
        if not question:
            # inbox.json missing/unreadable/malformed despite status ==
            # "blocked_ask_user" (docs/spec.md "Edge cases") -- still
            # "pending": true with a safe, non-empty fallback question,
            # never silently under-reports a real block.
            question = ("The team is waiting for input, but the original question could not "
                        "be read -- check `tmux attach` or answer with any text to unblock it.")
            header, options, multi_select = "", [], False
        self._json({"pending": True, "run_id": run_id, "question": question,
                   "header": header, "options": options, "multi_select": multi_select})

    def _handle_team_inbox_board_write(self, state: dict):
        """
        The blocked_board_write branch of GET .../team/inbox (backlog item
        7 part 2, docs/spec.md §2). Reads inbox.json's own board_write shape
        (same fields resolve_board_write() itself reads) and adds a
        best-effort, failure-tolerant "subject" enrichment via one
        taiga_board.get_userstory() read -- current_value never carries the
        card's title, only a status/description snapshot (docs/spec.md
        "Background") -- so this is the one place that fetches it for
        display. Never touches `version` and never affects approve/reject,
        which stays fully functional even if this read fails.
        """
        run_id = state["run_id"]
        verb = ref = value = note = proposed_at = None
        current_value = {}
        try:
            with open(teams._inbox_path(run_id)) as f:
                inbox = json.load(f)
            verb = inbox.get("verb")
            ref = inbox.get("ref")
            value = inbox.get("value")
            note = inbox.get("note")
            current_value = inbox.get("current_value") or {}
            proposed_at = inbox.get("proposed_at")
        except (OSError, ValueError):
            verb = None
        if verb is None:
            # inbox.json missing/unreadable/malformed despite status ==
            # "blocked_board_write" (docs/spec.md "Edge cases") -- still
            # "pending": true, with a safe fallback note the panel can
            # render without crashing; approve/reject still work blind via
            # the CLI's own team-board-resolve in this rare case.
            self._json({
                "pending": True, "run_id": run_id, "kind": "board_write",
                "verb": None, "ref": None, "value": None,
                "note": ("The team is waiting on a board-write approval, but the details could "
                         "not be read -- check `tmux attach` or use the CLI's "
                         "`team-board-resolve` to approve/reject blind."),
                "current_value": {}, "proposed_at": None})
            return
        payload = {"pending": True, "run_id": run_id, "kind": "board_write",
                  "verb": verb, "ref": ref, "value": value, "note": note,
                  "current_value": current_value, "proposed_at": proposed_at}
        try:
            base_url, token, project_id = teams.taiga_board.resolve_session()
            userstory = teams.taiga_board.get_userstory(base_url, token, project_id, ref)
            payload["subject"] = userstory.get("subject") or None
        except teams.taiga_board.TaigaPushError:
            # Best-effort/read-only (docs/spec.md §2, "Edge cases": "Taiga
            # unreachable") -- every inbox.json-sourced field above is still
            # returned, "subject" is simply omitted, Approve/Reject remain
            # fully functional.
            pass
        self._json(payload)

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
        elif parts[0] == "projects" and len(parts) == 2 and parts[1] == "clone":
            # Backlog item 16, docs/spec.md "New route -- POST /projects/
            # clone" -- an ordinary JSON-body POST (no special early-branch
            # the way /projects/upload needs for its raw-bytes body), so it
            # goes through the shared TOTP gate exactly like /projects/new.
            ok, err = clone_project_from_url(body.get("url") or "", (body.get("name") or "").strip())
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
        elif parts[0] == "projects" and len(parts) == 4 and parts[2] == "team" and parts[3] == "start":
            # Team session lifecycle, part 2a (backlog item 6d, docs/spec.md
            # §5) plus roster & composition UI (backlog item 6e) -- body
            # gains two OPTIONAL keys, lead/members. Zero new privileged
            # surface: launch_team()/team_run() already route every
            # RUN_USER-crossing operation through the existing TMUX constant
            # (part 1, unchanged).
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            task = (body.get("task") or "").strip()
            if not task:
                return self._json({"error": "a task description is required"}, 400)
            if "lead" in body and "members" in body:
                # A submitted composition -- validated, then saved as a side
                # effect of a validated start REGARDLESS of whether
                # launch_team() itself later succeeds (docs/spec.md: "a
                # dirty-tree/session-collision failure below shouldn't
                # discard the user's picker choice"). Never falls back to
                # default_team_composition() on a validation failure -- a
                # user who picked X should either get X or a clear reason
                # they can't, not a different Y they didn't choose.
                lead_in, members_in = body["lead"], body["members"]
                err = teams.validate_composition(lead_in, members_in)
                if err:
                    return self._json({"error": err}, 400)
                teams.save_composition(name, lead_in, members_in)
                # validate_composition() already confirmed lead_in matches a
                # real roster() entry by (kind, name) -- re-derive its tier
                # live rather than trust a client-submitted one (roster() is
                # the only source of truth for tier, same discipline
                # save_composition() itself applies to persisted state).
                roster_entry = next(e for e in teams.roster()
                                    if e["kind"] == lead_in["kind"] and e["name"] == lead_in["name"])
                lead = {"kind": lead_in["kind"], "name": lead_in["name"], "tier": roster_entry["tier"]}
                members = [m["name"] for m in members_in]
            else:
                comp = teams.default_team_composition()
                if not comp["ok"]:
                    return self._json({"error": comp["error"]}, 400)
                lead, members = comp["lead"], comp["members"]
            workdir = os.path.join(PROJECTS_DIR, name)
            result = teams.launch_team(workdir, task, lead, members)
            if not result["ok"]:
                return self._json({"error": result["error"]}, 400)
            cancel_event = threading.Event()
            t = threading.Thread(target=_run_team_in_background,
                                 args=(name, result["run_id"], cancel_event), daemon=True)
            _team_threads_set(name, {"run_id": result["run_id"], "thread": t, "cancel_event": cancel_event})
            t.start()
            self._json({"ok": True, "run_id": result["run_id"], "session": result["session"],
                       "lead": lead, "members": members})
        elif parts[0] == "projects" and len(parts) == 4 and parts[2] == "team" and parts[3] == "stop":
            # Restart-safe by construction: re-derived from run.json via
            # latest_run_for_project(), never dependent on _team_threads
            # surviving a service restart (docs/spec.md "Goals").
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            run = teams.latest_run_for_project(name)
            # "blocked_board_write" added (backlog item 7 part 2, docs/
            # spec.md §4) -- previously this tuple only named
            # "blocked_ask_user", so a run blocked on a pending board-write
            # proposal fell into the early-return "no team currently
            # running" branch below and Stop silently did nothing (disclosed
            # gap, docs/implementation.md "Known limitations"). stop_team()
            # itself already handles any non-terminal status generically, so
            # this one-tuple extension is the entire fix.
            if run is None or run["status"] not in ("running", "blocked_ask_user", "blocked_board_write"):
                return self._json({"ok": True, "message": "no team currently running for this project"})
            entry = _team_threads_get(name)
            if entry is not None and entry.get("run_id") == run["run_id"]:
                entry["cancel_event"].set()
            result = teams.stop_team(run["run_id"])
            self._json({"ok": True, "session_removed": result["session_removed"],
                       "worktrees": result["worktrees"]})
        elif parts[0] == "projects" and len(parts) == 4 and parts[2] == "team" and parts[3] == "resolve":
            # Overwatch feed + escalation inbox (backlog item 6f part 1,
            # docs/spec.md §3) -- answers a pending ask_user and resumes the
            # lead loop on a background thread, mirroring /team/start's own
            # non-blocking discipline. Reached through the shared TOTP gate
            # above -- no new gating code, same as /team/start|stop.
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            run_id = (body.get("run_id") or "").strip() or None
            if run_id:
                # docs/BACKLOG.md item 11(b): same validation, same intake-
                # point placement, as _team_events_run_and_ownership() above
                # -- reject before _load_state()/_run_dir() ever join this
                # into a path. Same "no run found" 400 a syntactically-
                # valid-but-nonexistent run_id already gets via the except
                # clause below, so this adds no new error shape.
                if not teams._RUN_ID_RE.match(run_id):
                    return self._json({"error": "no run found for this project"}, 400)
                try:
                    state = teams._load_state(run_id)
                except (OSError, ValueError):
                    return self._json({"error": "no run found for this project"}, 400)
                if state.get("project_name") != name:
                    return self._json({"error": "this run belongs to a different project"}, 400)
            else:
                state = teams.latest_run_for_project(name)
                if state is None:
                    return self._json({"error": "no run found for this project"}, 400)
                run_id = state["run_id"]
            if state.get("status") != "blocked_ask_user":
                return self._json({"error": "no pending question for this project"}, 400)
            answer = (body.get("answer") or "").strip()
            if not answer or len(answer) > teams.TEAM_ASK_USER_ANSWER_MAX_CHARS:
                return self._json(
                    {"error": f"answer must be non-empty and at most "
                              f"{teams.TEAM_ASK_USER_ANSWER_MAX_CHARS} characters"}, 400)
            result = teams.resolve_ask_user(run_id, answer)
            if not result["ok"]:
                return self._json({"error": result["error"]}, 400)
            # Defensive, should-be-unreachable check (docs/spec.md §3): at
            # most one non-terminal run per project (launch_team()'s own
            # invariant) plus team_run()'s loop already exiting (and its
            # thread already popped) the instant a run goes
            # blocked_ask_user means no live thread should exist here --
            # cheap to assert rather than trust, turning an already-
            # impossible race into a clear error instead of two threads
            # driving one run.
            if _team_threads_get(name) is not None:
                return self._json({"error": "a team thread is already running for this project"}, 400)
            cancel_event = threading.Event()
            t = threading.Thread(target=_run_team_in_background,
                                 args=(name, run_id, cancel_event), daemon=True)
            _team_threads_set(name, {"run_id": run_id, "thread": t, "cancel_event": cancel_event})
            t.start()
            self._json({"ok": True, "run_id": run_id})
        elif (parts[0] == "projects" and len(parts) == 4 and parts[2] == "team"
              and parts[3] == "board-resolve"):
            # Board-write proposal approve/reject (backlog item 7 part 2,
            # docs/spec.md §3) -- a NEW, dedicated route (not an overload of
            # POST .../team/resolve above): resolve_board_write()'s own
            # input shape (run_id + action enum, no free text) differs
            # enough from resolve_ask_user()'s (run_id + free-text answer)
            # that branching one route on two body shapes wasn't worth it.
            # Mirrors the CLI's own team-board-resolve naming exactly.
            # Reached through the same shared TOTP gate above -- no new
            # gating code, same as /team/start|stop|resolve.
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            run_id = (body.get("run_id") or "").strip() or None
            if run_id:
                # docs/BACKLOG.md item 11(b): same validation, same intake-
                # point placement, as /team/resolve above.
                if not teams._RUN_ID_RE.match(run_id):
                    return self._json({"error": "no run found for this project"}, 400)
                try:
                    state = teams._load_state(run_id)
                except (OSError, ValueError):
                    return self._json({"error": "no run found for this project"}, 400)
                if state.get("project_name") != name:
                    return self._json({"error": "this run belongs to a different project"}, 400)
            else:
                state = teams.latest_run_for_project(name)
                if state is None:
                    return self._json({"error": "no run found for this project"}, 400)
                run_id = state["run_id"]
            if state.get("status") != "blocked_board_write":
                return self._json({"error": "no pending board write for this project"}, 400)
            action = body.get("action")
            if action not in ("approve", "reject"):
                return self._json({"error": "action must be 'approve' or 'reject'"}, 400)
            result = teams.resolve_board_write(run_id, action)
            if not result["ok"]:
                return self._json({"error": result["error"]}, 400)
            # Same defensive, should-be-unreachable check /team/resolve
            # already has above -- cheap to keep consistent.
            if _team_threads_get(name) is not None:
                return self._json({"error": "a team thread is already running for this project"}, 400)
            cancel_event = threading.Event()
            t = threading.Thread(target=_run_team_in_background,
                                 args=(name, run_id, cancel_event), daemon=True)
            _team_threads_set(name, {"run_id": run_id, "thread": t, "cancel_event": cancel_event})
            t.start()
            self._json({"ok": True, "run_id": run_id})
        elif (parts[0] == "projects" and len(parts) == 4 and parts[2] == "team"
              and parts[3] == "interject"):
            # Queue a free-text human message for a running lead's next
            # round (backlog item 19 part 1, docs/spec.md "Proposed
            # approach" §5) -- a materially different action from
            # /team/resolve|board-resolve above: this does NOT resume a
            # stopped loop, so unlike those two routes it never spins up a
            # background driving thread. Reached through the same shared
            # TOTP gate above -- no new gating code.
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            run_id = (body.get("run_id") or "").strip() or None
            if run_id:
                # docs/BACKLOG.md item 11(b): same validation, same intake-
                # point placement, as /team/resolve|board-resolve above.
                if not teams._RUN_ID_RE.match(run_id):
                    return self._json({"error": "no run found for this project"}, 400)
                try:
                    state = teams._load_state(run_id)
                except (OSError, ValueError):
                    return self._json({"error": "no run found for this project"}, 400)
                if state.get("project_name") != name:
                    return self._json({"error": "this run belongs to a different project"}, 400)
            else:
                state = teams.latest_run_for_project(name)
                if state is None:
                    return self._json({"error": "no run found for this project"}, 400)
                run_id = state["run_id"]
            # Length/emptiness validation happens at THIS route layer, not
            # inside teams.interject() -- mirroring /team/resolve's own
            # TEAM_ASK_USER_ANSWER_MAX_CHARS check above exactly.
            text = (body.get("text") or "").strip()
            if not text or len(text) > teams.TEAM_INTERJECT_MAX_CHARS:
                return self._json(
                    {"error": f"message must be non-empty and at most "
                              f"{teams.TEAM_INTERJECT_MAX_CHARS} characters"}, 400)
            result = teams.interject(run_id, text)
            if not result["ok"]:
                return self._json({"error": result["error"]}, 400)
            self._json({"ok": True, "run_id": run_id})
        elif (parts[0] == "projects" and len(parts) == 4 and parts[2] == "team"
              and parts[3] == "add-member"):
            # Add one more teammate engine to an already-running team
            # (backlog item 21 part 1, docs/spec.md "Proposed approach" §5)
            # -- same shape/order/run_id-resolution as /team/interject
            # above; the allowed-status set is identical to interject()'s
            # own and is more naturally owned by teams.add_team_member()
            # itself, not duplicated at this route layer (unlike /team/
            # resolve's own status check). No background thread spun up
            # here either -- same reasoning /team/interject already
            # documents: this never resumes a stopped loop, so there is
            # nothing to (re-)drive. Reached through the same shared TOTP
            # gate every other /team/* route already sits behind.
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            run_id = (body.get("run_id") or "").strip() or None
            if run_id:
                # docs/BACKLOG.md item 11(b): same validation, same intake-
                # point placement, as /team/interject|resolve|board-resolve above.
                if not teams._RUN_ID_RE.match(run_id):
                    return self._json({"error": "no run found for this project"}, 400)
                try:
                    state = teams._load_state(run_id)
                except (OSError, ValueError):
                    return self._json({"error": "no run found for this project"}, 400)
                if state.get("project_name") != name:
                    return self._json({"error": "this run belongs to a different project"}, 400)
            else:
                state = teams.latest_run_for_project(name)
                if state is None:
                    return self._json({"error": "no run found for this project"}, 400)
                run_id = state["run_id"]
            agent = (body.get("agent") or "").strip()
            if not agent:
                return self._json({"error": "agent is required"}, 400)
            result = teams.add_team_member(run_id, agent)
            if not result["ok"]:
                return self._json({"error": result["error"]}, 400)
            self._json({"ok": True, "run_id": run_id, "agent": agent})
        elif (parts[0] == "projects" and len(parts) == 3 and parts[2] == "smoke-check"):
            # HTTP-level smoke check (backlog item 18, docs/spec.md) -- new
            # /projects/<name>/... sub-resource route (not the older
            # /instance/<name>/deploy shape), reached through the same
            # shared TOTP gate above. Body: {"expect_contains": "<string,
            # may be empty>"}. 404 for an unknown project (checked here,
            # same as every other per-project route); smoke_check_run()
            # itself never 404s -- an unknown/URL-less project it can't
            # reach still completes as a clean {"ok": False, ...} result,
            # since _session_urls can change between page load and click.
            name = parts[1]
            if name not in instance_names():
                return self._json({"error": "unknown project"}, 404)
            raw_expect = body.get("expect_contains")
            expect_contains = raw_expect.strip() if isinstance(raw_expect, str) else ""
            result = smoke_check_run(name, expect_contains)
            # "locked" is smoke_check_run()'s own internal-only marker for
            # lock contention (see its docstring) -- popped here so it
            # never reaches the client, and mapped to 409, mirroring
            # deploy_run()'s own 409 contract. Every other completed check
            # (success or target-side failure alike) is HTTP 200.
            if result.pop("locked", False):
                self._json(result, 409)
            else:
                self._json(result, 200)
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
