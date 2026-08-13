# Spec: Local git hosting UI + CI/CD (Gitea) — part 2c, part 2a: deploy-target receiver

## Summary
Add `install.sh --with-deploy-target`, run on a **separate target machine**
(never the switchboard box itself), that provisions a dedicated low-privilege
`deploy` user whose SSH key can only push files into one pre-configured
destination path (via `rrsync`, carried forward from the deleted
`target-setup.sh`) or trigger one fixed, sudoers-scoped restart of one named
systemd service — nothing else, no shell, ever. This is the **receiver only**
(2c-2a); the switchboard-side wiring that decides which project deploys to
which target and actually dispatches these SSH calls off 2c part 1's poll
seam is a separate future cycle (2c-2b) that builds against this cycle's
already-working, independently-testable receiver.

## Goals
- A new `install.sh --with-deploy-target` flag, run **on the target
  machine**, that provisions everything needed to safely receive a deploy:
  a `deploy` system user, an `rrsync`-restricted SSH key scoped to exactly
  one destination path, and a narrowly sudoers-scoped restart of exactly one
  named systemd service.
- Reuse `host-agent`'s proven "SSH → sudoers → one narrow script" shape
  (dedicated low-priv user, forced-command/no-shell SSH restriction, a fixed
  zero-argument sudoers rule naming one exact script) rather than reviving
  the deleted `target-setup.sh`'s systemd-`.path`-unit inotify-trigger
  design.
- Carry forward what's still correct from the deleted `target-setup.sh`: the
  `deploy` user, `rrsync -wo <path>` write-only restriction, the
  `no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty`
  `authorized_keys` flags, and the `/bin/sh` (not `nologin`) shell choice
  (nologin ignores its `-c` argument entirely and never runs the forced
  command at all — this is a hard requirement, not a style choice).
- Support one whitelisted restart command run **over the same SSH
  connection** right after a push completes (via OpenSSH connection
  multiplexing — see "Protocol contract" below) instead of the old
  inotify-triggered `.path` unit.
- Standalone-testable: a target can be provisioned and fully exercised
  (push a file, confirm the path/command restrictions actually hold,
  trigger a restart) using nothing but a manually-generated SSH keypair and
  the `ssh`/`rsync` CLIs — no switchboard/app.py code needs to exist or run
  for any of this to work.

## Non-goals
- Switchboard-side wiring: no per-project target config, no UI capture of
  target host/path/service, no `app.py` dispatch off 2c part 1's poll seam,
  no new `switchboard.env` keys, no new sudoers rules on the *switchboard*
  machine. All of that is 2c-2b, a separate future cycle building against
  this cycle's receiver.
- Automatic SSH keypair generation, on either side. Same precedent as
  `--with-host-control` (see `host-agent/README.md`: "you still
  generate/place the SSH key yourself... since that's the one part worth
  doing by hand rather than having an install script mint credentials
  silently"). The operator generates a keypair by hand and pastes the
  **public** key into the `--with-deploy-target` prompt; the private key
  never touches the target machine.
- Multiple deploy targets (multiple paths/services) on the *same* target
  machine in one install. This cycle provisions exactly one `deploy` user,
  one destination path, one restartable service per target machine — see
  Open questions.
- A generic "run any command" remote-execution facility. The restart action
  is one fixed, pre-baked `systemctl restart <name>` of a service name
  chosen at install time — not an arbitrary-command channel.
- Reviving the systemd `.path`-unit / inotify auto-trigger — deliberately
  replaced per the user's confirmed design.
- Any change to the switchboard's own `README.md` "optional extra row" UI
  bullet list — this cycle adds no web UI row (there's nothing in the UI to
  drive it yet); README changes here are limited to the repo-layout tree
  and the security-notes list (see "Affected areas").

## Background / current state
- `host-agent/` (see `host-agent/README.md`, and `install.sh`'s
  `WITH_HOST_CONTROL` block, lines ~361-364 and ~583-597) is the direct
  precedent: a dedicated low-priv user (`switchboard`) on a *separate*
  machine, an SSH key restricted via `sudoers.d` to exactly three named
  scripts run as root (`ai-dev-switchboard-host-{start,stop,status}.sh`),
  config read from `/etc/ai-dev-switchboard/host.env` (installed from a
  repo-tracked `host.env.example`, then `set_env`-patched), scripts
  installed from repo-tracked files under `host-agent/` via `install -m
  755`. `install.sh --with-host-control` automates provisioning the
  scripts/config/sudoers on the target side; the human still generates and
  places the SSH key by hand.
- The deleted `docs/GIT_HOSTING.md` (`git show dcc582b:docs/GIT_HOSTING.md`)
  and `scripts/target-setup.sh` (`git show dcc582b:scripts/target-setup.sh`)
  described the *old* auto-deploy target: a `deploy` user created with
  `useradd -m -d /home/deploy -s /bin/sh deploy` (the `/bin/sh` choice is
  explained in-script: `nologin` doesn't run the forced command at all, it
  just prints a banner and exits, since sshd invokes a forced command via
  `"$SHELL -c '<command>'"`), an `authorized_keys` entry restricted to
  `command="/usr/bin/rrsync -wo <path>",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty
  <pubkey>`, and a `systemd .path` unit that watched
  `<path>/.deployed` and ran `systemctl restart <service>` on any write —
  the inotify-triggered piece the user has now explicitly asked to drop in
  favor of an explicit restart command sent over SSH right after the push.
- 2c part 1 (current `docs/implementation.md`/`docs/test-review.md`, this
  same `docs/spec.md` file's previous contents — preserved at commit
  `7451c05`) already keeps `PROJECTS_DIR/<name>` fast-forwarded whenever a
  Gitea-backed project's default branch moves. This cycle's receiver is
  designed to eventually receive a push of that same freshly-synced
  `PROJECTS_DIR/<name>` tree from 2c-2b — but 2c-2a itself doesn't care
  where the pushed files came from; it only has to safely receive whatever
  is rsync'd at it.
- `scripts/gitea-sync-project.sh` and `scripts/new-project-from-gitea.sh`
  are this session's most recent examples of the project's "never trust
  input blindly, re-validate everything even when the caller already
  validated it" discipline (regex-validating arguments, using `--` before
  user-influenced values passed to a command, etc.) — followed here even
  though the "input" in this case is a root-authored config file, not
  network input, because config can still be hand-edited incorrectly.

## Proposed approach

### 1. New top-level `deploy-target/` directory (mirrors `host-agent/`)
- `deploy-target/README.md` — same shape as `host-agent/README.md`: why a
  separate SSH channel, what `--with-deploy-target` automates vs. what the
  operator still does by hand, and the exact commands to verify the
  restriction (see "Acceptance criteria").
- `deploy-target/deploy-target.env.example` — mirrors `host.env.example`;
  documents `DEPLOY_PATH` and `DEPLOY_SERVICE_NAME`. Installed to
  `/etc/ai-dev-switchboard/deploy-target.env` (copied once if absent, then
  `set_env`-patched — identical idiom to `host.env`).
- `deploy-target/deploy-wrapper.sh` — the `authorized_keys` forced command.
  Installed to `/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh`
  (`install -m 755`). Sources `deploy-target.env`, then branches on
  `$SSH_ORIGINAL_COMMAND`:
  - Begins with `rsync --server` (the string the `rsync` client sends as
    its remote-shell command) → `exec /usr/bin/rrsync -wo "$DEPLOY_PATH"`,
    after validating `DEPLOY_PATH` is set and starts with `/` (fail closed
    if the config is somehow empty/malformed — never fall through to
    calling `rrsync` with an unset path).
  - Exactly equals the literal string `deploy-restart` (a fixed protocol
    keyword, not a filesystem path or a secret — the actual security
    boundary is possession of the SSH private key, not knowledge of this
    string) → `exec sudo -n /usr/local/bin/ai-dev-switchboard-deploy-restart.sh`.
  - Anything else (including no command at all, i.e. someone tries a bare
    `ssh deploy@target` for an interactive shell) → print an error to
    stderr and `exit 1`. `no-pty` in the `authorized_keys` line is a second,
    independent layer that blocks interactive shell allocation even if this
    branch were ever bypassed.
- `deploy-target/deploy-restart.sh` — the one script `deploy` may run as
  root. Installed to
  `/usr/local/bin/ai-dev-switchboard-deploy-restart.sh` (`install -m 755`,
  root-owned). Sources `deploy-target.env` for `DEPLOY_SERVICE_NAME`,
  validates it against `^[A-Za-z0-9@_.-]+$` (defense in depth against a
  hand-edited config, same discipline as
  `scripts/gitea-sync-project.sh`'s argument validation), then runs
  `systemctl restart -- "$DEPLOY_SERVICE_NAME"`. Left to fail loudly
  (`set -euo pipefail`, no error swallowing) — a restart failure must
  propagate back over SSH as a non-zero exit, not be silently absorbed.

### 2. `install.sh --with-deploy-target` (new flag, run on the target machine)
New block placed immediately after the existing `WITH_HOST_CONTROL` block
(around line 597) — both flags are "this matters on a machine other than
the one running the web UI" family, and are fully independent (different
user, different config file, different sudoers file; safe to use either or
both on the same box).

Steps, following the `WITH_HOST_CONTROL` block's own shape:
1. Prompt (via the existing `prompt` helper, same idiom as every other
   `install.sh` flag):
   - `DEPLOY_PATH` — "Destination path this target will receive deploys
     into" (no default; required to proceed with steps 4-7 below).
   - `DEPLOY_SERVICE_NAME` — "Systemd service name to restart after each
     deploy" (no default; required to proceed with steps 6-7 below).
   - `DEPLOY_PUBKEY` — "Public key to authorize for this target (paste the
     contents of e.g. `deploy_ed25519.pub` — leave blank to add by hand
     later)" (optional; gates step 8 only).
2. Verify `/usr/bin/rrsync` exists (`rsync` itself is already an
   unconditional `apt-get install` dependency at the top of `install.sh`,
   and Debian 12+ ships `rrsync` in that same package). If missing: print an
   error explaining why (`apt-get install rsync` should already have
   provided it; flag if the target OS is older than Debian 12) and **skip
   the rest of this block only** (`continue`-equivalent — do not `exit 1`
   the whole `install.sh` run over one optional flag, matching the
   ttyd-arch-not-found precedent already in the script).
3. `id deploy &>/dev/null || useradd -m -d /home/deploy -s /bin/sh deploy`
   — idempotent, `/bin/sh` (not `nologin`) for the reason documented above.
4. `mkdir -p "$DEPLOY_PATH"; chown deploy:deploy "$DEPLOY_PATH"` (only if
   `DEPLOY_PATH` was supplied; additive, never wipes pre-existing content —
   same idiom as `PROJECTS_DIR`'s own `mkdir -p` + `chown` earlier in
   `install.sh`).
5. `mkdir -p /home/deploy/.ssh; chmod 700 /home/deploy/.ssh`.
6. Install/patch `/etc/ai-dev-switchboard/deploy-target.env` (copy the
   `.example` once if absent, then `set_env DEPLOY_PATH` /
   `set_env DEPLOY_SERVICE_NAME` — same idiom as `host.env`/`TAIGA_DIR`/etc
   throughout the rest of `install.sh`), only overwriting the keys that
   were actually supplied this run.
7. `install -m 755` the two `deploy-target/` scripts to
   `/usr/local/bin/ai-dev-switchboard-deploy-{wrapper,restart}.sh`.
8. If `DEPLOY_PUBKEY` was supplied: write
   `/home/deploy/.ssh/authorized_keys` deterministically (overwrite, like
   the sudoers file below — this cycle supports exactly one authorized key
   per target, see Open questions):
   ```
   command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <DEPLOY_PUBKEY>
   ```
   `chown -R deploy:deploy /home/deploy/.ssh; chmod 600
   /home/deploy/.ssh/authorized_keys`. If blank: leave any existing
   `authorized_keys` untouched and print instructions for adding the line
   above by hand later (mirrors `BASE_URL`'s "leave blank to fill in later"
   idiom already used elsewhere in `install.sh`).
9. Write `/etc/sudoers.d/ai-dev-switchboard-deploy-target` (mode 440,
   `visudo -cf`-validated, same pattern as the main `$SUDOERS` file):
   ```
   deploy ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-deploy-restart.sh
   ```
   Zero arguments — same "narrower than every other rule" idiom
   `install.sh` already uses for `gitea-{up,down,status}.sh` and
   `taiga-{up,down,status}.sh` (Docker-socket-equivalent access there;
   root-restart-equivalent access here — same reasoning for not accepting
   any passthrough arguments).
10. Print a summary block (mirrors the `WITH_HOST_CONTROL` summary):
    confirms this is the target-side half only, restates that the private
    key half of the keypair belongs on whatever machine will eventually
    push here (2c-2b, or a human doing the manual verification below), and
    prints the two-step manual test command sequence from "Acceptance
    criteria" below.

Update the flag-list comment block near the top of `install.sh` (next to
`--with-host-control`'s own entry) with a matching one-line description.

### Protocol contract (for 2c-2b, or any manual caller, to build against)
Not built or wired up this cycle, but documented in
`deploy-target/README.md` so a future caller needs zero re-derivation:
1. Open one SSH connection to `deploy@<target>` using the matching private
   key (`ssh -o ControlMaster=auto -o ControlPersist=...` recommended so
   step 2 reuses the same authenticated connection rather than
   re-handshaking).
2. Push files: `rsync -e "ssh -i <key> ..." -a <source>/
   deploy@<target>:<DEPLOY_PATH>/`.
3. On success, trigger the restart over the same connection:
   `ssh -i <key> deploy@<target> deploy-restart`. Non-zero exit means the
   restart itself failed (e.g. bad service name) — the caller should
   surface that, not swallow it.
4. This receiver adds no locking of its own between overlapping push+
   restart pairs — a future caller (2c-2b) must serialize invocations for
   the same target rather than firing two deploys at once (see Edge cases).

## Affected areas
- New: `deploy-target/README.md`, `deploy-target/deploy-target.env.example`,
  `deploy-target/deploy-wrapper.sh`, `deploy-target/deploy-restart.sh`.
- `install.sh`: new `--with-deploy-target` flag + `WITH_DEPLOY_TARGET`
  variable (top-of-file flag parsing, alongside `WITH_HOST_CONTROL`), new
  provisioning block placed after the existing `WITH_HOST_CONTROL` block,
  updated flag-list usage comment near the top of the file.
- `README.md` (top-level): add a `deploy-target/` line to the repo-layout
  tree (mirroring the existing `host-agent/` line), and one bullet to the
  existing security-notes list describing the new SSH-key restriction
  (mirroring the existing host-control security bullet). Explicitly note in
  that new bullet that this is receiver-only infrastructure with no
  switchboard UI consumer yet, to avoid implying a UI feature exists before
  2c-2b ships.
- No changes to `app/app.py`, `config/switchboard.env.example`, or any
  existing sudoers/systemd asset on the *switchboard* machine — all new
  surface here lives on the separate target machine only.

## Edge cases
- **`rrsync` missing** (older/non-Debian target) — block prints an error
  and is skipped; the rest of `install.sh`'s run (other flags) still
  completes. See step 2 above.
- **Re-running `--with-deploy-target`** with a different path/service/
  pubkey — `deploy-target.env` and `authorized_keys` are deterministically
  overwritten (like the sudoers file elsewhere in `install.sh`), not
  accumulated. The *previous* `DEPLOY_PATH`'s ownership/content is left
  untouched (nothing deletes or un-chowns it) — a known limitation of the
  "one target path per machine" scope, called out in Open questions.
- **Blank pubkey at install time** — user/path/scripts/sudoers are still
  fully provisioned; `authorized_keys` is left as-is (empty on a fresh
  install) and the printed summary explains how to add the line by hand.
  Nothing is "half-broken" in this state — it's simply not yet reachable
  over SSH until a key is added.
- **Path-escape attempts via the SSH command string** — the wrapper does a
  literal `case` string match (never `eval`s or otherwise re-interprets
  `$SSH_ORIGINAL_COMMAND`), and the actual rsync-argument parsing is
  delegated entirely to `rrsync` itself (upstream-maintained specifically
  to safely parse rsync's wire protocol and reject any path outside the
  one root it's given) — this cycle doesn't reimplement that parsing, it
  relies on `rrsync -wo` exactly as the deleted `target-setup.sh` did.
- **Arbitrary-command attempts** (`ssh deploy@target "whoami"`, or no
  command at all for an interactive shell) — rejected by the wrapper's
  default case; `no-pty` in `authorized_keys` independently blocks pty
  allocation regardless.
- **Restart of a nonexistent/misconfigured service** — `systemctl restart`
  fails loudly under `set -euo pipefail`; the non-zero exit propagates back
  to the SSH client. Not treated as a soft/ignorable failure.
- **File ownership after a push** — pushed files land owned by
  `deploy:deploy`. If `DEPLOY_SERVICE_NAME` runs as a different Unix user,
  that service needs its own read access to `DEPLOY_PATH` (group
  membership, permissions, or running as `deploy` itself) — this cycle
  doesn't manage the target service's own permissions, same gap existed in
  the deleted `target-setup.sh`. Documented as an operator responsibility
  in `deploy-target/README.md`.
- **Concurrent/overlapping push+restart pairs** — no locking is added on
  the receiver side; see "Protocol contract" #4 and Open questions.
- **Host-control and deploy-target on the same box** — fully independent
  (different user, different sudoers file, different config file); no
  interaction, no shared state, both may coexist.

## Acceptance criteria
- [ ] Given a target machine with `install.sh --with-deploy-target` run
  supplying a path, service name, and a pasted pubkey, then: a `deploy`
  system user exists with shell `/bin/sh`; `<DEPLOY_PATH>` exists, owned by
  `deploy:deploy`; `/home/deploy/.ssh/authorized_keys` contains exactly one
  line, a `command=".../deploy-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty`
  restriction; `/etc/sudoers.d/ai-dev-switchboard-deploy-target` grants
  `deploy` `NOPASSWD` root execution of exactly
  `ai-dev-switchboard-deploy-restart.sh` with no arguments, and passes
  `visudo -cf`.
- [ ] Given the matching private key, when running `rsync -e "ssh -i <key>"
  -a ./somefile deploy@target:<DEPLOY_PATH>/`, then the file lands under
  `<DEPLOY_PATH>` owned by `deploy`, with no shell/tty ever allocated.
- [ ] Given the same key, when attempting to rsync to a path outside
  `<DEPLOY_PATH>` (e.g. targeting `/etc/`), then `rrsync` refuses and no
  file is written outside `<DEPLOY_PATH>`.
- [ ] Given the same key, when running any command other than the rsync
  protocol invocation or the literal string `deploy-restart` (e.g.
  `ssh -i <key> deploy@target whoami`, or no command at all), then the
  connection is rejected — no shell, no command output, no pty.
- [ ] Given the same key, when running
  `ssh -i <key> deploy@target deploy-restart`, then `<DEPLOY_SERVICE_NAME>`
  restarts on the target (verified via e.g. `systemctl show -p
  ActiveEnterTimestamp` or the journal), with no sudo password prompt.
- [ ] Given `DEPLOY_SERVICE_NAME` naming a nonexistent unit, when
  `deploy-restart` is sent, then the SSH client sees a non-zero exit —
  the failure is not silently swallowed.
- [ ] Given `install.sh --with-deploy-target` is re-run with different
  values, then `deploy-target.env` and `authorized_keys` reflect only the
  new values (no duplicated/stale entries).
- [ ] Given `install.sh --with-deploy-target` is run with a blank pubkey,
  then the user/path/scripts/sudoers are still provisioned and the printed
  summary explains how to add a key by hand.
- [ ] Given `install.sh` is run with both `--with-host-control` and
  `--with-deploy-target` in the same invocation (same target machine),
  then both provision successfully with no conflicting user/file/sudoers
  state.

## Open questions
- **One deploy target per machine, not per-project multiplexed onto one
  machine.** Per the user's confirmed scope, the *switchboard* side decides
  targets per-project (2c-2b's concern) — this cycle only needs to support
  "some project deploying to some target." Proceeding under the assumption
  that "some target" = one physical/VM machine = one `deploy` user = one
  path = one service, for this cycle. If a real homelab setup later wants
  two independently-deployed projects landing on the *same* target machine,
  that needs a follow-up (e.g. a second `deploy-<name>` user + a second
  `authorized_keys`/sudoers pair per additional project) — not built here,
  flagged as a likely near-future ask rather than blocking this cycle.
- **File ownership handoff to the restarted service.** Assumption: the
  operator ensures `DEPLOY_SERVICE_NAME` can read files under
  `DEPLOY_PATH` (own it as `deploy`, or grant group access) — this cycle
  doesn't manage that, matching the same gap in the deleted
  `target-setup.sh`. Flagged, not blocking.
- **No locking between concurrent push+restart pairs.** Assumption: 2c-2b
  (or any manual caller) serializes invocations per target rather than
  firing overlapping deploys. If this proves to be a real problem in
  practice, a follow-up could add an `flock` around `DEPLOY_PATH` inside
  `deploy-wrapper.sh` — not added preemptively here to keep this cycle's
  surface minimal and match `host-agent`'s own "no extra machinery beyond
  what's needed" precedent.
- **Sentinel string stability.** `deploy-restart` is now a fixed protocol
  keyword any future caller (2c-2b) must send verbatim. Assumption: fine to
  hard-code (it's not a secret — the SSH key is the actual boundary) and
  changing it later would be a deliberate, documented breaking change to
  the contract in `deploy-target/README.md`, not a config option that needs
  to exist yet.
- **Restart mechanism is `systemctl restart` only**, not a general
  command-runner. Assumption: correct for this cycle — every other
  managed process in this repo (Taiga, Gitea, host-agent's own sessions
  are tmux not systemd, but the *services* pattern elsewhere is
  systemd-first) is systemd-managed, and `host-agent` itself already
  established the "fixed named scripts, not passthrough commands"
  precedent. A future cycle could add a "custom command" mode if a real
  non-systemd target shows up; not building it speculatively.

## Risk / rollback notes
- **New privileged surface**: a new low-priv account with root-restart
  capability, on a machine outside the switchboard's own trust boundary.
  Mitigated by: SSH key restricted to exactly two forced actions (write-only
  rsync into one path, or one exact zero-argument root-owned restart
  script), no shell, no port/X11/agent forwarding, no pty — verified live
  per the "Acceptance criteria" SSH tests above, not just by code review.
  This mirrors `host-agent`'s own already-shipped, already-reviewed
  privilege boundary rather than inventing a new shape.
- **Opt-in only, on a machine other than the switchboard's own**: not
  running `--with-deploy-target` leaves any given target machine completely
  untouched; this cycle changes nothing about the switchboard machine
  itself (no new `switchboard.env` keys, no new switchboard-side sudoers
  rules, no new listener).
- **Rollback** (on the target machine): remove
  `/etc/sudoers.d/ai-dev-switchboard-deploy-target`; remove
  `/etc/ai-dev-switchboard/deploy-target.env`; remove
  `/usr/local/bin/ai-dev-switchboard-deploy-{wrapper,restart}.sh`; `userdel
  -r deploy` (or just clear `/home/deploy/.ssh/authorized_keys` to disable
  SSH access without deleting the account/path). Document these exact steps
  in `deploy-target/README.md`.
