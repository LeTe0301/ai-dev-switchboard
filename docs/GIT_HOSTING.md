# Git hosting (`--with-git-hosting`)

Optional. Adds:

- A restricted `git` user (git-shell only — no real shell access) serving
  private bare repos over SSH from `$GIT_ROOT/repos/`.
- A generic auto-deploy mechanism: push to `main` → rsync to a target
  machine → that machine restarts its own service. No shared root, no
  access back to the git-hosting box from the target beyond one restricted
  rsync key.
- The web UI's "+ New project" button (`create_project()` in `app.py` calls
  `ai-dev-switchboard-new-project.sh`, installed by this feature).

## One-time: add your SSH key

```bash
ssh-keygen -t ed25519 -C "you@your-laptop"
cat ~/.ssh/id_ed25519.pub
```

Append that to `$GIT_ROOT/.ssh/authorized_keys` on the git-hosting machine
(as the `git` user, or via `sudo`), then pushes will work.

## Everyday use: new private repo

```bash
# repo only, no auto-deploy
ai-dev-switchboard-new-repo.sh myproject

# repo + auto-deploy to a target machine on push to main
ai-dev-switchboard-new-repo.sh myproject 192.168.1.60 /opt/myproject
```

This prints the exact remote to add:

```bash
git remote add dev ssh://git@<git-hosting-host>/srv/git/repos/myproject.git
git push dev main
```

Or use the web UI's "+ New project" button, which runs `new-repo.sh` +
`new-dev-instance.sh` together in one step (this is exactly what
`ai-dev-switchboard-new-project.sh` does).

## Wiring up a deploy target

Once, on the target machine, as root:

```bash
ai-dev-switchboard-target-setup.sh /opt/myproject myproject-service "<pubkey from new-repo.sh's output>"
```

This creates a `deploy` user whose SSH key can *only* write into
`/opt/myproject` (enforced by `rrsync` — no shell, no other commands) and a
`systemd .path` unit that restarts `myproject-service` automatically the
moment new files land. The target needs `rsync` installed
(`apt-get install rsync`) and `myproject-service.service` to already exist
before the first push.

## How the pieces fit together

- `scripts/git-hosting-setup.sh` — one-time (idempotent): creates the `git`
  user, the deploy SSH keypair, and the sudoers rule that lets the `git`
  user run `project-sync.sh` as `RUN_USER`. Run by `install.sh
  --with-git-hosting`.
- `scripts/new-repo.sh <name> [target-ip target-path]` — creates
  `$GIT_ROOT/repos/<name>.git`, with an optional auto-deploy
  `post-receive` hook.
- `scripts/new-dev-instance.sh <name>` — clones that bare repo into
  `$PROJECTS_DIR/<name>` (where the web UI finds it) and installs a second
  `post-receive` hook block that keeps the working copy synced on every
  push to `main`, forever.
- `scripts/new-project.sh <name>` — the two above, in one step. What the
  web UI's button calls.
- `scripts/project-sync.sh <name>` — what the auto-sync hook actually runs
  (`git fetch && git reset --hard origin/main` in the working copy).
- `scripts/target-setup.sh` — run once on any auto-deploy *target*, not the
  git-hosting box itself.

All of these read `$CONFIG_DIR/git-hosting.env` (default
`/etc/ai-dev-switchboard/git-hosting.env`) for `GIT_ROOT`, `GIT_USER`,
`RUN_USER`, `PROJECTS_DIR`, and `ADVERTISE_HOST` — see
`config/git-hosting.env.example`.
