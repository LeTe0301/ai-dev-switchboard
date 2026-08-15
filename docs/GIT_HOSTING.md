# Git hosting (`--with-git-hosting`)

Optional. Adds a self-hosted [Gitea](https://gitea.com) instance (a
2-container Docker Compose stack: `server` + `db`/Postgres, well under 1 GB
RAM while running) plus the web UI's "+ New project" button, which creates a
real, private Gitea repository and clones it into `PROJECTS_DIR/<name>` in
one click.

This replaced an earlier lightweight git-shell/bare-repo/rsync setup
(backlog item 2b) — if you're looking for that flow's docs, they're in git
history (`git show dcc582b:docs/GIT_HOSTING.md`); it's gone from new
installs, and this document only describes the current Gitea-backed flow.

## How it fits together

- Gitea is a **singleton, off-by-default toggle row** in the web UI (`--with-git-hosting`
  installs it stopped; flip the "Gitea" row's toggle to start/stop it). One
  instance for the whole box, not per project.
- **"+ New project"** calls Gitea's own `POST /user/repos` REST API
  (unprivileged, as the web UI's own service user) to create a private repo,
  then hands off to a small privileged script that clones it into
  `PROJECTS_DIR/<name>`, owned by `RUN_USER` — that's what makes it show up
  in the web UI (`instance_names()` just lists `PROJECTS_DIR` subdirectories).
- From then on, `PROJECTS_DIR/<name>` **is** your primary working copy: an
  agent session commits there and pushes to Gitea directly (`git push`,
  already fully configured — see "Everyday use" below), the same direction
  any normal git workflow uses. This is the opposite of the old flow, where
  a bare repo was the source of truth and `PROJECTS_DIR/<name>` was a
  passively-synced mirror.

## One-time setup (after `install.sh --with-git-hosting`)

Two manual steps, in order, after the "Gitea" row is toggled on and the
stack has finished starting:

**1. Create Gitea's own admin account** (a single non-interactive command —
not automated by this installer, no password ever stored by this project
beyond this one command you type yourself):

```bash
docker exec -it --user git ai-dev-switchboard-gitea gitea admin user create \
  --admin --username <name> --password <password> --email <email>
```

(`--user git` matters — `docker exec` defaults to the container's `root`
user, and Gitea's own CLI refuses to run as root.)

**2. Mint an API token for the web UI to use**, by running the bootstrap
script once, as root:

```bash
sudo scripts/gitea-configure-api.sh
```

This prompts for the admin username and container name (both default to the
values above), mints a `write:repository,write:user`-scoped Personal Access
Token directly via Gitea's own CLI (**no password is ever asked for or
stored by this script** — it only needs `docker exec` access to the
container), writes it into `/etc/ai-dev-switchboard/switchboard.env` as
`GITEA_API_TOKEN`, restarts the `ai-dev-switchboard` service so it picks up
the new value, and verifies the token actually works (`GET /user` against
Gitea). Safe to re-run any time — it mints a fresh token (with a unique
name each run — Gitea rejects reusing a token name) and overwrites the old
one, useful for rotation.

Once both steps are done, "+ New project" works.

## Everyday use: new private repo

Click "+ New project" in the web UI, type a name, confirm. Behind the
scenes:

1. `app.py` calls `POST /user/repos` against Gitea (`private: true`,
   `auto_init: true`, `default_branch: "main"`), authenticated with the
   token from step 2 above.
2. A privileged hand-off script clones the new repo into
   `PROJECTS_DIR/<name>`, owned by `RUN_USER` — the clone's `origin` remote
   already has the same token embedded (`http://oauth2:<token>@...`), so
   `git push`/`git pull` from that working copy (as `RUN_USER`, e.g. from an
   agent session) work immediately, no extra credential setup.
3. The new project shows up in the web UI right away.

Local project names can contain spaces (same rules as every other project
name here); Gitea's own repo names can't, so spaces are mapped to hyphens
for the Gitea side only (e.g. local name `my project` becomes Gitea repo
`my-project`) — the local `PROJECTS_DIR/<name>` folder keeps the original
name with spaces.

## Reaching a repo from outside the box

Gitea's own web UI, and the token-authenticated clone URL, are both reached
the same way every other per-feature URL in this project is:

- **`PUBLISH_MODE=tailscale`**: `https://<your-tailnet-host>/gitea/...` (the
  same published `/gitea` path the Gitea row's own link already uses) — a
  developer's laptop can `git clone`/`git push` against that URL directly.
- **`PUBLISH_MODE=none`**: `http://127.0.0.1:$GITEA_PORT/...`, reachable
  only from the box itself unless you put your own reverse proxy / SSH
  tunnel / VPN in front.

`app.py`'s own API call and the privileged clone script always talk to
Gitea over `127.0.0.1:$GITEA_PORT` directly (both run on the same host as
Gitea) — `PUBLISH_MODE`/`tailscale serve` only matters for an *external*
git client reaching in from elsewhere, which needs no extra code on this
project's side; it falls out of the Gitea toggle's existing `_publish()`
call.

## Auto-sync of `PROJECTS_DIR/<name>` when someone pushes from elsewhere

If another contributor pushes to the same repo from somewhere else (Gitea's
own web UI, a merged PR, a second agent session elsewhere), `app.py`
notices and, when it's safe, catches your local working copy up
automatically — no webhook, no extra listener, no extra secret. It works by
**polling**, not by Gitea pushing a notification: every
`GITEA_POLL_INTERVAL_SECONDS` (45s by default), `app.py` asks Gitea's own
REST API whether each Gitea-backed project's default branch has moved since
the last check, and only fetches when it actually has.

**What "safe" means, exactly** — this never overwrites or discards local
work:

1. `git fetch origin main` (never touches your working tree or `HEAD` by
   itself).
2. If the working copy has uncommitted changes, sync is skipped entirely
   (recorded as `skipped-dirty` — visible as a small note next to that
   project's row in the web UI). Nothing is touched.
3. If local `HEAD` isn't a strict/equal ancestor of the newly fetched
   ref — a genuinely diverged history, or local commits of your own not yet
   pushed — sync is also skipped (`skipped-diverged`). Nothing is touched,
   no history is rewritten, no commit is ever lost.
4. Otherwise: a guaranteed no-op-or-clean `git merge --ff-only` — never
   `git reset --hard`.

**Two things this is honest about, on purpose:**

- **Latency, not instant.** A push landing elsewhere shows up in your local
  working copy within `GITEA_POLL_INTERVAL_SECONDS` (up to ~45s by
  default), not immediately. If you know a push just happened and don't
  want to wait, `git pull` manually — that always works regardless.
- **The two skip cases above still need a manual `git pull`/resolve.** If
  you have uncommitted changes or unpushed local commits when someone else
  pushes, auto-sync steps back and leaves it to you rather than guessing.

## What's NOT included (yet)

- **CI/CD auto-deploy** (Gitea Actions or webhooks replacing the old
  `project-sync.sh` + `post-receive` deploy-to-a-target-machine flow) — a
  future cycle, not built yet. (Backlog item 2c, part 2 — reuses this
  cycle's poll-detected-a-push dispatch point rather than building its own
  detection mechanism.)
- **A manual "check now" button** — auto-sync always waits for the next due
  poll interval; there's no on-demand trigger in the web UI (yet).
- **Multiple Gitea orgs, or a separate Gitea account per developer.** Every
  repo is created under the single admin account from step 1 above
  (`POST /user/repos`, not `/orgs/{org}/repos`) — same single-shared-identity
  model the old `git` system user had, just under Gitea's terms.
- **Projects not created through the "+ New project" flow.** Auto-sync only
  covers projects `create_project()` itself registered (it's the only thing
  that records the `owner/repo` -> local-project mapping this relies on) —
  a manually `git init`'d project, or one registered via the folder-upload
  wizard with a hand-added Gitea remote, isn't polled.

## Troubleshooting

- **"Gitea isn't installed on this box"** — you don't have
  `--with-git-hosting` installed. Either re-run `install.sh
  --with-git-hosting`, or just `git init` a folder under `PROJECTS_DIR`
  yourself; it'll show up in the web UI either way.
- **"Gitea is installed but not running"** — flip the "Gitea" row's toggle
  on first.
- **"Gitea API token isn't configured yet"** — run `sudo
  scripts/gitea-configure-api.sh` (see "One-time setup" above).
- **"A Gitea repository named '...' already exists"** — the name you typed
  (after spaces are mapped to hyphens) collides with a repo that already
  exists in Gitea, even if no `PROJECTS_DIR` folder of that exact name
  exists locally yet (e.g. it was created directly through Gitea's own web
  UI). Pick a different name.
- **"+ New project" failed partway through** — if Gitea's own API call
  succeeded but the clone step failed (disk full, a network hiccup, etc.),
  the just-created Gitea repo is deleted automatically (best-effort) so you
  don't end up with an orphaned repo. If a retry with the same name then
  says "already exists" locally even though nothing shows up in the web UI,
  a previous attempt's `PROJECTS_DIR/<name>` directory was left behind empty
  — `rmdir` it by hand and try again.
