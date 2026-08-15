# deploy-target

Optional. Turns a machine into a **deploy receiver**: a dedicated low-priv
`deploy` user whose SSH key can only push files into one pre-configured
destination path, or trigger one fixed restart of one named systemd
service — nothing else, no shell, ever.

This is the **receiver only** — everything in this directory is
standalone-testable with nothing but a manually-generated SSH keypair and
the `ssh`/`rsync` CLIs, no switchboard/`app.py` code needs to exist or run.
The switchboard-side piece that actually decides which project deploys to
which target and sends the push (backlog item 2c, part 2b — a hand-edited
`deploy-map.json` plus a "Deploy" button in the web UI) now exists too —
see "Switchboard-side caller (2c part 2b)" below.

## Why a separate SSH channel instead of just another "project"

Same reasoning as [`host-agent/README.md`](../host-agent/README.md): a
deploy target is deliberately a *different* machine than the one running
the switchboard's web UI, so it's controlled over a narrowly-scoped SSH key
instead of anything running as the web UI's own process. The key can do
exactly two things: write-only rsync into one path (via `rrsync`), or
trigger one fixed root-run restart script — never an interactive shell,
never an arbitrary command.

## Setup

On the **target machine** (the one that will receive deploys):

1. `sudo ./install.sh --with-deploy-target`, from a clone of this repo.
   Prompts for:
   - `DEPLOY_PATH` — the one destination path this target receives files
     into (e.g. `/opt/myapp`).
   - `DEPLOY_SERVICE_NAME` — the systemd service restarted after each
     deploy (e.g. `myapp.service`).
   - `DEPLOY_PUBKEY` — paste the contents of a public key you've already
     generated (e.g. `deploy_ed25519.pub`) to authorize it immediately, or
     leave blank to add it by hand later (see step 3).

   This creates the `deploy` system user (shell `/bin/sh` — see "Why
   `/bin/sh`, not `nologin`" below), creates `DEPLOY_PATH` owned by
   `deploy:deploy`, installs `deploy-wrapper.sh` /
   `deploy-restart.sh` to `/usr/local/bin/`, writes
   `/etc/ai-dev-switchboard/deploy-target.env`, and writes a narrowly
   scoped `/etc/sudoers.d/ai-dev-switchboard-deploy-target` granting
   `deploy` passwordless root execution of exactly `deploy-restart.sh`,
   with no arguments.

2. Generate a keypair for this target if you haven't already — **on
   whatever machine will eventually push here** (a future 2c-2b caller, or
   your own machine for manual testing), not on the target itself:
   ```
   ssh-keygen -t ed25519 -f deploy_ed25519 -N "" -C "deploy@<target-name>"
   ```
   The private key never touches the target machine — only its public half
   goes into step 1's `DEPLOY_PUBKEY` prompt (or step 3 below).

3. If you left `DEPLOY_PUBKEY` blank in step 1, add it by hand:
   ```
   echo 'command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <paste pubkey>' \
     | sudo tee /home/deploy/.ssh/authorized_keys
   sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys
   sudo chmod 600 /home/deploy/.ssh/authorized_keys
   ```

## Why `/bin/sh`, not `nologin`

`deploy`'s shell must be `/bin/sh`, not `/usr/sbin/nologin`. sshd invokes a
forced `authorized_keys` `command="..."` as `"$SHELL" -c '<command>'` —
`nologin` ignores its `-c` argument entirely, just prints a banner, and
exits without ever running the forced command at all. `/bin/sh` runs it
correctly while still refusing any *interactive* shell (there's no
forced-command case for that — see `deploy-wrapper.sh`'s default branch).

## Verifying the restriction (manual test, or use before 2c-2b exists)

With the matching private key (`deploy_ed25519` from step 2 above):

```
# 1. Push a file — lands under DEPLOY_PATH, owned by deploy, no shell/tty.
#    Leave the destination path bare (just "deploy@<target>:") — rrsync
#    has already fixed the destination to DEPLOY_PATH server-side, so
#    repeating an absolute path here would be resolved *relative to*
#    DEPLOY_PATH too (nesting it a level deeper, or erroring if the
#    resulting intermediate directories don't already exist) rather than
#    being treated as the real absolute path.
rsync -e "ssh -i deploy_ed25519" -a ./somefile deploy@<target>:

# 2. Trigger the restart, over the same key (open with connection
#    multiplexing if you want this to reuse a still-open push connection
#    instead of re-handshaking):
ssh -i deploy_ed25519 deploy@<target> deploy-restart

# 3. Confirm anything else is refused — no shell, no output, no pty:
ssh -i deploy_ed25519 deploy@<target> whoami        # rejected
ssh -i deploy_ed25519 deploy@<target>                # rejected (bare shell attempt)

# 4. Confirm the path restriction actually holds — an escape attempt at
#    a path outside DEPLOY_PATH must be refused by rrsync, not silently
#    redirected or partially applied:
rsync -e "ssh -i deploy_ed25519" -a ./somefile deploy@<target>:/etc/   # rejected
```

## Protocol contract (for a future switchboard-side caller, or any manual one)

Not built or wired up by this cycle — documented here so a future caller
needs zero re-derivation:

1. Open one SSH connection to `deploy@<target>` using the matching private
   key (`ssh -o ControlMaster=auto -o ControlPersist=...` recommended so
   step 2 reuses the same authenticated connection rather than
   re-handshaking).
2. Push files: `rsync -e "ssh -i <key> ..." -a <source>/ deploy@<target>:`
   — leave the destination path bare (see "Verifying the restriction"
   above for why repeating `<DEPLOY_PATH>` here doesn't do what it looks
   like it does).
3. On success, trigger the restart over the same connection:
   `ssh -i <key> deploy@<target> deploy-restart`. A non-zero exit means the
   restart itself failed (e.g. a bad service name) — surface that, don't
   swallow it.
4. This receiver adds no locking of its own between overlapping push +
   restart pairs — a future caller must serialize invocations for the same
   target rather than firing two deploys at once.

## Switchboard-side caller (2c part 2b)

The real caller against this receiver's "Protocol contract" above now
exists: `app.py`'s `deploy_run()`, wired to a per-project "Deploy" button
in the web UI. It's driven by a separate, hand-edited map file (default
`/etc/ai-dev-switchboard/deploy-map.json`, one entry per switchboard
project — see `config/deploy-map.json.example` for the exact schema and
`docs/spec.md`'s 2c part 2b for the full design) naming which target each
project deploys to and where its private key lives. This receiver's own
setup above (steps 1-3) is unchanged and still exactly what you do first,
on the target machine — the map file is the separate, switchboard-side step
that then points a project at it. Deploy is manual-only: a push landing on
a project's Gitea repo (2c part 1's poll/sync) never triggers a deploy by
itself, only an explicit, confirmed click on the "Deploy" button does.

Before the first click against a given target, make sure `SVC_USER` (the
account `app.py` itself runs as) already trusts that target's host key —
e.g. `sudo -u <SVC_USER> ssh -i <key> deploy@<target> true` once by hand.
`deploy_run()` deliberately never sets `StrictHostKeyChecking=no` (same
precedent host-control's own SSH channel already carries) — an untrusted
host key fails the click loudly and immediately instead of either hanging
or silently trusting an unverified host.

## What this cycle doesn't do

- No SSH keypair generation — you generate/place the key by hand (step 2
  above), same precedent as `host-agent`'s own setup.
- No support for multiple deploy targets (multiple paths/services) on the
  *same* target machine in one install — this cycle provisions exactly one
  `deploy` user, one destination path, one restartable service per target
  machine.
- No management of the restarted service's own file permissions. Pushed
  files land owned by `deploy:deploy`; if `DEPLOY_SERVICE_NAME` runs as a
  different Unix user, that service needs its own read access to
  `DEPLOY_PATH` (group membership, permissions, or running as `deploy`
  itself) — this is an operator responsibility, not something this receiver
  manages.

## Removing a deploy target

On the target machine:
```
sudo rm -f /etc/sudoers.d/ai-dev-switchboard-deploy-target
sudo rm -f /etc/ai-dev-switchboard/deploy-target.env
sudo rm -f /usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh
sudo rm -f /usr/local/bin/ai-dev-switchboard-deploy-restart.sh
sudo userdel -r deploy
```
Or, to disable SSH access without deleting the account/path, just clear
`/home/deploy/.ssh/authorized_keys` instead of the last step.
