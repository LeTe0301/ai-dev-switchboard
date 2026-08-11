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
import json
import os
import re
import secrets
import shlex
import ssl
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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
NEW_PROJECT_SCRIPT = os.environ.get(
    "NEW_PROJECT_SCRIPT", "/usr/local/bin/ai-dev-switchboard-new-project.sh")

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
class Engine:
    __slots__ = ("name", "label", "cmd", "url_regex", "startup")

    def __init__(self, name, label, cmd, url_regex, startup):
        self.name = name
        self.label = label
        self.cmd = cmd
        self.url_regex = re.compile(url_regex) if url_regex else None
        self.startup = startup  # list of (match_str, keys_to_send)


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
    startup = []
    i = 1
    while f"STARTUP_MATCH_{i}" in kv and f"STARTUP_SEND_{i}" in kv:
        startup.append((kv[f"STARTUP_MATCH_{i}"], kv[f"STARTUP_SEND_{i}"]))
        i += 1
    return Engine(name, kv.get("LABEL", name), cmd, kv.get("URL_REGEX") or None, startup)


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


def create_project(name: str) -> tuple[bool, str]:
    if not NAME_RE.match(name or ""):
        return False, "Use letters, numbers, spaces, - or _ (must start with a letter/number)."
    if name in instance_names():
        return False, f"'{name}' already exists."
    if not os.path.exists(NEW_PROJECT_SCRIPT):
        return False, ("New-project scaffolding isn't installed on this box — create "
                       f"{PROJECTS_DIR}/{name} yourself (e.g. `git init`) and it'll show "
                       "up here, or install scripts/ from the repo (see docs/GIT_HOSTING.md).")
    r = subprocess.run(["sudo", NEW_PROJECT_SCRIPT, name],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "new-project script failed").strip()[:300]
    return True, ""


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


def host_run(action: str) -> str:
    assert action in ("start", "stop", "status")
    r = subprocess.run(
        ["ssh", "-i", HOST_CONTROL_KEY, "-o", "BatchMode=yes",
         "-o", "ConnectTimeout=5", f"{HOST_CONTROL_USER}@{HOST_IP}",
         f"sudo /usr/local/bin/ai-dev-switchboard-host-{action}.sh"],
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip()


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
  .sub { font-size: 12px; color: #888; margin-top: 4px; word-break: break-all; }
  .vscode-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .pill.code-pill { background: #2a2a2a; }
  .pill.code-pill.active { background: #4da6ff; color: #111; border-color: #4da6ff; }
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
</style></head>
<body>
<h1>ai-dev-switchboard</h1>
<div class="new-project-row">
  <input id="new-project-name" placeholder="new project name" maxlength="60">
  <button onclick="startNewProject()">+ New project</button>
</div>
<div class="new-project-err" id="new-project-err"></div>
<div id="rows"></div>

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

async function refresh() {
  const r = await fetch('/status');
  if (r.status === 401) { showOverlay(); return; }
  const s = await r.json();
  ENGINE_LABELS = s.engines || {};
  let html = '';
  for (const inst of s.instances) {
    html += row(inst.name, inst.on, inst.url, 'inst', inst.name, inst.desc, inst.engine,
               inst.code_on, inst.code_url);
  }
  if (s.instances.length === 0) html += '<div class="empty">No project folders under the configured PROJECTS_DIR yet.</div>';
  if (s.host_enabled) html += row(s.host_label, s.host, s.host_url, 'host', null, '', null, false, null);
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
function row(label, on, url, kind, name, desc, engine, codeOn, codeUrl) {
  const sub = on ? (url ? 'running — <a href="' + url + '" target="_blank">open</a>' : 'running') : 'stopped';
  const arg = name ? "'" + kind + "','" + name + "'" : "'" + kind + "',null";
  return '<div class="row"><div><div class="label">' + esc(label) + '</div>' +
    (kind === 'inst' ? engineRow(name, on, engine) : '') +
    (desc ? '<div class="desc">' + esc(desc) + '</div>' : '') +
    '<div class="sub">' + sub + '</div>' +
    (kind === 'inst' ? codeRow(name, codeOn, codeUrl) : '') +
    '</div>' +
    '<label class="switch"><input type="checkbox" ' + (on ? 'checked' : '') +
    ' onchange="toggle(' + arg + ', this.checked, this)"><span class="slider"></span></label></div>';
}
let pendingToggle = null;  // {kind, name, on, checkboxEl} — only set while the code overlay is up

function actionPath(kind, name, on) {
  if (kind === 'host') return '/host/' + (on ? 'on' : 'off');
  if (kind === 'code') return '/instance/' + encodeURIComponent(name) + '/code/' + (on ? 'on' : 'off');
  if (kind === 'newproject') return '/projects/new';
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
    showOverlay();
    return;
  }
  if (r.status === 428) {
    pendingToggle = ctx;
    document.getElementById('code-overlay-label').textContent =
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
  if (kind === 'newproject') document.getElementById('new-project-name').value = '';
  hideCodeOverlay();
  setTimeout(refresh, 1500);
}
async function toggle(kind, name, on, checkboxEl) {
  const ctx = {kind, name, on, checkboxEl};
  const r = await performAction(kind, name, on, null);
  handleActionResult(r, ctx);
}
function toggleCode(name, currentlyOn) {
  toggle('code', name, !currentlyOn, null);
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
}
function cancelActionCode() {
  // The checkbox already flipped visually the instant it was clicked (that's
  // how the change event works) — revert it since nothing actually happened
  // (the server returns 428, before touching anything, when a code is due).
  if (pendingToggle && pendingToggle.checkboxEl) {
    pendingToggle.checkboxEl.checked = !pendingToggle.on;
  }
  hideCodeOverlay();
}
async function submitActionCode() {
  if (!pendingToggle) return;
  const {kind, name, on} = pendingToggle;
  const code = document.getElementById('action-code').value;
  const r = await performAction(kind, name, on, code);
  handleActionResult(r, pendingToggle);
}
document.getElementById('action-code').addEventListener('keydown', e => { if (e.key === 'Enter') submitActionCode(); });

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
            instances = []
            for n in instance_names():
                engine = active_engine(n)
                e = engines.get(engine) if engine else None
                url = (_session_urls.get(n) if (e and e.url_regex) else
                       _ttyd_urls.get(n) if engine else None)
                instances.append({"name": n, "on": engine is not None, "engine": engine,
                                  "url": url,
                                  "desc": get_description(n, os.path.join(PROJECTS_DIR, n)),
                                  "code_on": code_running(n), "code_url": _code_urls.get(n)})
            self._json({"instances": instances,
                       "engines": {name: e.label for name, e in engines.items()},
                       "host_enabled": HOST_CONTROL_ENABLED, "host_label": HOST_LABEL,
                       "host": host_on, "host_url": host_url})
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
        elif parts[0] == "projects" and len(parts) == 2 and parts[1] == "new":
            ok, err = create_project((body.get("name") or "").strip())
            if not ok:
                return self._json({"error": err}, 400)
            self._json({"ok": True})
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
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.serve_forever()
