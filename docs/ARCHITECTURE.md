# Architecture

## Processes and privilege boundaries

- **`app/app.py`** runs as `SVC_USER` (default `switchboard-svc`), an
  unprivileged system account with no login shell of its own. It can do
  exactly three privileged things, all via a narrow `/etc/sudoers.d` rule:
  run `tmux` as `RUN_USER`, run `ttyd` as `RUN_USER`, run `code-server` as
  `RUN_USER`. Nothing else. A bug in this ~900-line stdlib-only app is not
  an instant path to `RUN_USER`'s account.
- **`RUN_USER`** (default `dev`) is where the actual work happens: project
  files, engine credentials (e.g. `claude`'s own login), and the tmux
  sessions the engines run in all live here. This account needs whatever
  access your real agentic work needs — the switchboard doesn't constrain
  that, by design.
- **Per-project terminals** (the ttyd fallback, VS Code) bind to
  `127.0.0.1` only, regardless of `PUBLISH_MODE`. In `tailscale` mode
  they're published via `tailscale serve --set-path`; in `none` mode
  they're simply not exposed beyond loopback — the operator is expected to
  put their own reverse proxy / SSH tunnel / VPN in front, same trust model
  as tailscale mode (the network boundary IS the auth), just not tied to
  one specific tool.
- **The optional host-control row** talks to a *different* machine over a
  dedicated SSH key that can run exactly three whitelisted scripts
  (`ai-dev-switchboard-host-{start,stop,status}.sh`), nothing else — see
  `host-agent/README.md`.

## In-memory state and its one sharp edge

A few things are deliberately kept in memory rather than on disk, because
they're either cheap to regenerate or genuinely tied to a live process:

- `_session_urls` (captured hosted engine links) and the ttyd/code-server
  process tables in `app.py` — lost on a service restart while sessions are
  still running. `_reap_dead_state()` (called on every `/status`) means
  this self-heals as soon as the underlying tmux session actually ends; the
  gap is specifically "service restarted while a session kept running,"
  where the link just won't show until that session is restarted too.

- The optional host-control row is different: `host-start.sh` persists its
  captured URL to `$HOST_STATE_DIR/host-url` (a *file*, since it's a
  separate process invoked fresh over SSH each time, with no long-lived
  Python process to hold it in memory). This is the one part of the
  original homelab build that shipped an actual bug worth documenting:
  **the file was only written after the full startup sequence succeeded,
  so a slow/timed-out step left the tmux session running but the file
  stale — and the idempotent "already running" fast path never re-checked,
  so every later start just kept serving that stale (eventually archived)
  link forever.** `run_startup_watch()` in `host-agent/lib/engine-lib.sh`
  is the fix: it captures opportunistically throughout the startup
  sequence (not just once at the end) and *always* writes-or-clears
  `URL_FILE` when it's done, success or timeout — so a partial run can
  never leave stale state for a later run to misread as current. The
  "already running" fast path in `host-start.sh` additionally checks
  whether the cached URL predates the session it's attached to, and drops
  it if so, rather than risk serving a link to an engine incarnation that
  no longer exists.

## Why engines are config, not code

The original build had Claude Code and aider handling hardcoded directly
into the toggle logic — a working `if engine == "aider": start_ttyd()`
special case. Generalizing that into `engines.d/*.engine` (see
`docs/ADDING_AN_ENGINE.md`) wasn't just about supporting more tools: it
collapsed two previously-separate, subtly-different implementations of
"handle a startup prompt, then look for a URL" (one in `app.py` for
per-project sessions, one duplicated in the host-control start script) into
one shared, tested behavior — `run_startup_watch()`, implemented once in
Python and once in bash, but driven by the same file format both places. A
new engine, or a fix to how startup prompts get handled, is one file that
both code paths pick up.
