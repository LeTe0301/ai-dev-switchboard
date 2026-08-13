# ai-dev-switchboard

A small self-hosted web UI that starts and stops AI coding-agent sessions
per project — Claude Code, aider, Codex, or any CLI coding tool — from a
phone or laptop, without keeping a terminal open. One tmux session per
project, a "trust this folder"-style startup prompt cleared automatically,
and either the engine's own hosted remote-control link or a built-in web
terminal, whichever the engine supports.

It grew out of a real homelab setup (Proxmox host + LXC container running
several projects side by side) and has since been generalized: which
engines are available is a config file, not code (see
[`docs/ADDING_AN_ENGINE.md`](docs/ADDING_AN_ENGINE.md)), auth works with or
without a Proxmox host, and the self-hosted git-hosting piece is optional.

## Quickstart

**On a Proxmox VE host** — creates a new LXC container and installs
everything into it, prompting for the handful of decisions that actually
need a human (container ID, resources, auth):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/ai-dev-switchboard/main/ct/create.sh)"
```

**On any existing Debian/Ubuntu box or container** (no Proxmox needed):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/ai-dev-switchboard/main/install.sh)"
```

Both scripts are plain, short, and meant to be read before you run them —
`curl` the URL yourself first if you'd rather review it than trust a
README. Neither depends on the other: `install.sh` works standalone on a
box you already have; `ct/create.sh` is only the "also create the box"
wrapper around it.

Either way you end up with:

- A systemd service (`ai-dev-switchboard`) running the web UI on
  `127.0.0.1:8333` — put a reverse proxy, `tailscale serve`, or an SSH
  tunnel in front of it (see [Reaching the UI](#reaching-the-ui) below).
  It has no TLS of its own on purpose.
- A generated TOTP secret, printed at the end — add it to an authenticator
  app. A code from it is asked for once per browser session, the moment
  you actually flip a switch.
- `RUN_USER` (default `dev`) created, with `$RUN_USER/projects` as the
  directory any project folder needs to exist under to show up in the UI.

Log in as `RUN_USER` and run your engine's CLI once interactively (e.g.
`claude`) to finish its own login before starting sessions from the web UI
— the switchboard spawns the same CLI, it doesn't manage its credentials.
It also doesn't install the CLIs themselves — pick whichever you want and
install it as `RUN_USER` first:

```bash
npm install -g @anthropic-ai/claude-code   # Claude Code
npm install -g @openai/codex               # Codex CLI
pipx install aider-chat                    # aider
```

## Use cases

- **Keep coding from your phone.** Start a session on the couch, close the
  tab, pick it back up from a laptop an hour later — the tmux session (and,
  for Claude Code, the hosted link) don't care which device opens them.
- **A homelab with several projects and one machine to run them on.** Every
  project folder under `PROJECTS_DIR` is its own row, its own on/off switch,
  its own engine choice — no per-project systemd units or terminal
  multiplexer muscle memory required.
- **Give a non-technical collaborator a "go" button.** `AUTH_MODE=simple`
  plus a TOTP code is a low-friction way to hand someone a working button
  for "start Claude on the project we're building" without handing them a
  terminal.
- **Compare engines on the same project.** Stop Claude, start aider (or
  Codex) on the same folder from the same switch — useful when one engine
  is rate-limited, one needs LAN-only data handling, or you're just curious
  which one handles a given task better.
- **A private git-hosting box that also runs the agents.** With
  `--with-git-hosting`, a self-hosted Gitea instance (own singleton toggle
  row, off by default) creates real private repos and clones them straight
  into `PROJECTS_DIR` — "create a new project" and "start an agent on it"
  are two clicks total — see [`docs/GIT_HOSTING.md`](docs/GIT_HOSTING.md).

## What you get

- **One row per project folder** under `PROJECTS_DIR`, auto-discovered — no
  registration step. A switch starts/stops that project's engine in its own
  tmux session.
- **A picker for which engine** to start a project with, built from
  whatever's in `engines.d/` — see
  [`docs/ADDING_AN_ENGINE.md`](docs/ADDING_AN_ENGINE.md). Claude Code,
  aider, and Codex CLI all ship working out of the box, each verified by
  actually running it through this switchboard — not guessed.
- **Either a hosted link or a built-in terminal, automatically.** An engine
  that prints its own remote-session URL (like Claude Code) gets that URL
  surfaced directly. Anything else gets a small ttyd web terminal sharing
  the exact tmux pane, no config needed.
- **VS Code in the browser**, independent on/off per project
  (`code-server`, `--with-code-server`; opens in a dark theme by default).
- **An "Upload folder / .zip" wizard** — pick a local folder (zipped
  client-side with a progress bar) or an already-made `.zip`, review the
  server-detected structure (single repo, a folder of independent repos, or
  a monorepo with nested/vendored repos), choose which pieces become their
  own projects, and confirm. Works standalone — unlike "+ New project"
  below, it does **not** need `--with-git-hosting`.
- **A self-hosted Gitea singleton row** — optional, needs
  [`--with-git-hosting`](docs/GIT_HOSTING.md): a 2-container (Gitea +
  Postgres) Docker Compose stack, well under 1 GB RAM once toggled on, off
  by default.
- **A "+ New project" button** — needs Gitea (above) toggled on plus a
  one-time API token bootstrap (`scripts/gitea-configure-api.sh`): creates a
  real, private Gitea repo via its own REST API and clones it into
  `PROJECTS_DIR` in one step, ready to `git push` immediately.
- **An optional extra row** for a persistent session on a *different*
  machine (e.g. the Proxmox host itself, outside any container) — see
  [`host-agent/README.md`](host-agent/README.md).
- **Push a spec into a Taiga backlog item** via
  `scripts/taiga_push_spec.py`, a standalone CLI tool (no new web UI) — see
  `docs/spec.md`.

## Reaching the UI

The service binds `127.0.0.1:8333` and stays there — `PUBLISH_MODE` only
controls per-project ttyd/VS Code terminals, not the main UI itself. Pick
whichever fits how you already reach your machines:

- **Tailscale**: `tailscale serve --bg https+insecure://127.0.0.1:8333` (or
  `--https=443` for a real cert) from the box itself, then open the tailnet
  hostname it gives you. `install.sh`/`ct/create.sh` already prompt for
  `PUBLISH_MODE`/`BASE_URL` at setup time so per-project terminals get
  published the same way — choose `tailscale` there and enter the tailnet
  hostname (from `tailscale status`); edit `switchboard.env` and restart
  only if you want to change that choice later.
- **SSH tunnel**: `ssh -L 8333:127.0.0.1:8333 you@the-box`, then open
  `http://127.0.0.1:8333` locally. Fine for occasional use, not for the
  per-project terminals (those need `PUBLISH_MODE=tailscale` or your own
  reverse proxy to be reachable from elsewhere too).
- **Your own reverse proxy** (nginx, Caddy, etc.) in front, with whatever
  TLS/auth you already run for other self-hosted services.

## Configuration

Everything lives in `/etc/ai-dev-switchboard/switchboard.env` — see
[`config/switchboard.env.example`](config/switchboard.env.example) for the
full annotated reference (auth mode, engines directory, publish mode,
optional host-control, optional per-project description via a local LLM).
`install.sh` writes this file for you and prompts for the handful of values
that need a human; editing it and running `systemctl restart
ai-dev-switchboard` picks up any change (engine files under `engines.d/`
don't even need that — they're re-read live).

## Repo layout

```
app/app.py              the web UI itself — stdlib-only Python, one file
engines.d/               engine definitions (see docs/ADDING_AN_ENGINE.md)
config/                  *.env.example reference configs
install.sh               installer — run on any existing box
ct/create.sh             Proxmox-host wrapper: creates a container, then runs install.sh inside it
scripts/                 optional git-hosting + project-scaffolding (docs/GIT_HOSTING.md);
                          also scripts/taiga_push_spec.py + taiga-configure-push.sh (docs/spec.md)
host-agent/               optional persistent session on a separate machine (host-agent/README.md)
systemd/                 reference systemd unit for manual installs
docs/                    architecture notes, engine format, git-hosting detail
```

## Security notes

- The web UI process runs as its own unprivileged user with a narrowly
  scoped sudoers rule (exactly `tmux`/`ttyd`/`code-server`, as `RUN_USER`,
  nothing else) — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- `AUTH_MODE=simple` stores its password in plain text in
  `switchboard.env` (file mode `600`, owned by the service user). Fine
  behind a boundary you trust (tailnet, SSH tunnel, your own reverse proxy
  + auth); don't bind `LISTEN_HOST` to a public interface.
- TOTP is checked once per browser session, at the first action that
  actually changes state — not on login itself, and not on every
  subsequent action in that session. See the comment on `session_totp_ok`
  in `app.py` for the reasoning.
- The optional host-control SSH channel is scoped to exactly three
  whitelisted scripts via `sudoers.d` — see
  [`host-agent/README.md`](host-agent/README.md).
- `scripts/taiga_push_spec.py` stores its password in plain text in
  `~/.config/ai-dev-switchboard/taiga-push.env` (file mode `600`, owned by
  `RUN_USER`).

## Contributing

Issues and PRs welcome, especially engine definitions for tools beyond
Claude Code, aider, and Codex CLI — a working, *verified* `<tool>.engine`
(actually run it through the switchboard, don't just guess at its prompts —
see [`docs/ADDING_AN_ENGINE.md`](docs/ADDING_AN_ENGINE.md)) is a small,
high-value contribution.

## License

MIT — see [`LICENSE`](LICENSE).
