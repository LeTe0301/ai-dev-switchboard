# Architecture

## Processes and privilege boundaries

- **`app/app.py`** runs as `SVC_USER` (default `switchboard-svc`), an
  unprivileged system account with no login shell of its own. Via narrow
  `/etc/sudoers.d` rules it can run `tmux`/`ttyd`/`code-server` as
  `RUN_USER`, and run the folder-upload wizard's privileged hand-off script
  (`ai-dev-switchboard-new-project-from-upload.sh` — see below) and the
  clone-from-URL privileged hand-off script
  (`ai-dev-switchboard-new-project-from-url.sh` — see below) — plus,
  only when `--with-git-hosting` is installed, the Gitea toggle wrapper
  triplet and the "+ New project" registration script (see below).
  Nothing else. A bug in this stdlib-only app is not an instant path to
  `RUN_USER`'s account.
- **The folder-upload wizard's privileged hand-off**
  (`scripts/new-project-from-upload.sh`, see `docs/spec.md` "Crossing the
  privilege boundary") follows the same narrow shape as Gitea's own
  registration hand-off below: runs as root via a whitelisted sudoers
  entry, does the minimum mechanical work (atomic `mkdir`, `cp -a`,
  `chown`, an optional `git init`), nothing else. Its sudoers entry lives
  in the **base, always-installed** block of `install.sh`, not behind
  `--with-git-hosting` — this feature is explicitly the
  project-registration path for people *without* git hosting. Everything
  before that hand-off (receiving the upload, staging, detecting structure,
  naming, collision-checking) runs entirely unprivileged as `SVC_USER`,
  inside `UPLOAD_STAGING_DIR` — only the final registration step needs the
  privileged script.
- **Git hosting's own privileged hand-off** (`scripts/new-project-from-gitea.sh`,
  `--with-git-hosting` only, see `docs/spec.md` backlog item 2b) follows the
  exact same mechanical shape: `create_project()` creates the actual repo
  itself first, entirely unprivileged, via Gitea's own REST API (as
  `SVC_USER`, using a token from `scripts/gitea-configure-api.sh`'s one-time
  bootstrap) — only the final `mkdir`/`chown`/`git clone` into
  `PROJECTS_DIR/<name>` crosses into root via a narrowly-scoped sudoers
  entry, same "do the minimum mechanical work as root, nothing else"
  discipline as the upload wizard's own hand-off.
- **Clone-from-URL's own privileged hand-off** (`scripts/new-project-from-url.sh`,
  installed unconditionally, no `--with-git-hosting` dependency — see
  `docs/spec.md` backlog item 16) follows the same mechanical shape again:
  `clone_project_from_url()` validates the URL/name and checks for a
  collision entirely unprivileged, as `SVC_USER` — only the final
  `mkdir`/`chown`/`git clone -- <url> <dest>` crosses into root, cloning as
  `RUN_USER` (so an `ssh://`/scp-like clone of a private repo already
  works if `RUN_USER` has its own working SSH access to that host, with no
  switchboard-managed credential involved). Unlike the two hand-offs above,
  a failed or oversized clone always removes the destination directory
  (rather than leaving a partial clone for manual cleanup) — an arbitrary
  external clone is the one creation path genuinely likely to fail
  partway through a large transfer.
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

- **Upload staging** (`UPLOAD_STAGING_DIR/<token>/`, folder-upload wizard)
  is a deliberate exception to "a request either finishes clean or its
  state is gone": a staged-but-not-yet-confirmed upload genuinely outlives
  a single request, spanning phase 1 (`POST /projects/upload` — detect) and
  phase 2 (`POST /projects/upload/confirm` — register), however long the
  user takes on the review step in between. There's no
  always-cleanup-in-`finally` here on purpose — instead, confirm removes
  its own staging directory only once it **succeeds**; a failed confirm
  (e.g. a name collision) leaves staging in place so the UI's "Back to
  review" button can retry the same token, evaluated fresh, instead of
  hitting a spurious "upload expired." `_reap_dead_state()` (already called
  on every `/status` poll) is the backstop either way — it sweeps any
  staging directory older than `UPLOAD_STAGING_TTL_SECONDS` that was never
  confirmed successfully, whether it was never attempted or was attempted
  and failed. A future reader finding a `UPLOAD_STAGING_DIR/<token>/`
  directory that outlived its originating request should read this as the
  intended retry-then-TTL-cleanup story, not a leak.

## A restart can very likely take down every RUN_USER tmux session, not just the switchboard's own process

The generated systemd unit (`install.sh`, mirrored in
`systemd/ai-dev-switchboard.service`) sets no `KillMode` at all, so
systemd's default (`KillMode=control-group`) applies: on `systemctl
restart`, systemd sends `SIGTERM`/`SIGKILL` to **every process still in
that unit's cgroup**, not just the unit's own direct child. Every
per-project engine session and every team session is started via `sudo -u
$RUN_USER tmux new-session -d ...` (`app.py`, and `run_startup_watch`) as a
descendant of the service process, and nothing in this codebase moves that
spawned `tmux` server out of the service's own cgroup first (no
`systemd-run --scope`, no explicit cgroup move, no `Delegate=yes`). The
practical implication: **restarting `ai-dev-switchboard.service` while any
session is running very likely takes down the entire `RUN_USER` tmux
server, not just the switchboard's own web process** — every open project
session, not only team runs. **Empirically confirmed** (backlog item 14's
review, 2026-08-14): a throwaway systemd unit matching this project's
generated shape exactly (`Type=simple`, no `KillMode`), with a tmux
session spawned as a second user from inside the service process
(mirroring `app.py`'s own spawn pattern), lost its entire tmux server on
`systemctl restart` — not just the service's own process. This was
previously only inferred from documented `systemd.kill(5)` default
behavior; it is now verified against a real unit/cgroup.

This is also worse than it first looks because a team run's own driving
loop (`app/teams.py`'s `_tail_loop`/lead loop) is a `threading.Thread`
living *inside* the service's own Python process — restarting that process
ends that thread outright, mid-run, with no resume, independent of the
cgroup question above.

`install.sh --update`/`--upgrade` (docs/BACKLOG.md item 14) is built around
this finding: it refuses to restart the service (not just warn) whenever
`RUN_USER` has any live tmux session, full stop, no override flag — see
that item and `install.sh`'s own "guarded restart" comment. This doesn't
fix the underlying process-isolation gap (a real fix would look like
`systemd-run --scope`-launching each spawned tmux server, or `Delegate=yes`
plus an explicit cgroup move) — that's a separate, larger change this spec
deliberately didn't take on.

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

## Reviewing a team's work after it stops

`stop_team()` (`app/teams.py`) removes each teammate's git worktree
directory once the run stops or finishes, but deliberately never deletes
the branch that worktree was checked out onto (`team-{run_id}-{agent}`,
created by `_create_worktree()`) — nothing a teammate committed is ever
silently lost. Once the worktree is gone, though, the switchboard itself no
longer tracks that branch anywhere in its own run state; the Teams page's
"Past team branches" panel (and the `team-branches <project_workdir>` CLI
subcommand, both backed by `teams.list_team_branches()`) exist purely to
help you *find* it again — neither one offers a merge/delete action, by
design (backlog item 13): reviewing, merging, and cleaning up is left to
you, using plain git, directly against the project's own repository:

```sh
# See what a teammate actually did, before deciding whether to keep any of it.
git -C <project_dir> log team-<run_id>-<agent>

# Bring it into your current branch, exactly like reviewing any other branch.
git -C <project_dir> merge team-<run_id>-<agent>

# Once you're done with it (merged, or decided to discard it), the branch
# does not clean itself up -- delete it explicitly when you're ready.
git -C <project_dir> branch -D team-<run_id>-<agent>
```

A branch left unreviewed costs nothing beyond `git branch --list`'s own
output growing by one line over time — there is no TTL, sweep, or
auto-deletion for `team-*` branches anywhere in this codebase, on purpose.
