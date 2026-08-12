# Spec: Local backlog tracker (Taiga) — part 1a: install flag + singleton UI row

## Summary
Add an optional `install.sh --with-taiga` flag that installs a self-hosted
Taiga instance (via its own official Docker Compose stack) and gives it one
shared on/off-toggle-plus-link row in the web UI, the same way code-server
gets a per-project row today — **not** the "Claude should track it"
MCP/API integration (`docs/BACKLOG.md` item 1's second bullet), which stays
out of scope for a future 1b cycle.

## Goals
- `install.sh --with-taiga`, following the exact `--with-git-hosting` /
  `--with-code-server` flag-parsing pattern, installs Docker + Taiga's own
  official `taiga-docker` Compose stack, configured (generated secrets,
  loopback-only port binding) but **left stopped** after install.
- A new singleton row in the web UI (`kind: 'taiga'`, mirroring the existing
  host-control row, not a per-project row) with an on/off toggle and,
  when on, a link to the running instance.
- Toggling on starts the whole Taiga stack (`docker compose up -d`);
  toggling off stops it (`docker compose down`), actually freeing the RAM —
  this is the point of the toggle, not just session convenience (see
  "Resource-cost callout" below).
- State survives `ai-dev-switchboard` service restarts correctly (Taiga's
  containers are not children of `app.py`'s process tree, unlike
  ttyd/code-server) — Background/Proposed approach below.

## Non-goals
- The MCP/API "Claude should track it" integration (`docs/BACKLOG.md` item
  1, bullet 3) — explicitly deferred to a 1b cycle.
- Gitea (`docs/BACKLOG.md` item 2) — unrelated, separate item.
- Per-project Taiga projects/rows, or any UI beyond the single shared
  toggle+link — Taiga's own web UI handles everything past that link,
  including creating projects, sprints, users, etc. inside Taiga itself.
- Automatic creation of the first Taiga admin/superuser account — this is a
  one-time interactive step documented in `taiga-docker`'s own instructions;
  install.sh prints a pointer to it but does not automate it (doing so
  interactively conflicts with `--yes` non-interactive installs).
- A `--without-taiga` uninstall flag, or any automated removal of
  Docker/the `taiga-docker` checkout — matches every other `--with-*` flag
  in this installer today (none of them have an uninstall path either).
- Auto-upgrading the `taiga-docker` checkout on a re-run of
  `install.sh --with-taiga` (see "Open questions").
- Any change to how `AUTH_MODE`/TOTP work — the Taiga toggle inherits the
  existing shared TOTP gate in `do_POST` for free, no new auth code.
- LAN/tailscale exposure decisions beyond what `PUBLISH_MODE` already
  decides for ttyd/code-server — Taiga's loopback bind + `_publish`/
  `_unpublish` follow the exact same rule, no new publish mode.

## Background / current state
Two existing singleton-ish patterns are the precedent to build on, and this
feature is a genuine hybrid of both — that hybrid shape is the main design
decision this spec is making, so it's worth spelling out clearly:

- **code-server** (`install.sh` lines ~128-166, sudoers line ~242,
  `app.py` `_code_start`/`_code_stop`/`code_running`, `_code_procs`/
  `_code_ports`/`_code_urls` dicts) is **per-project**: one instance per
  project name, tracked as an in-memory `subprocess.Popen` per name.
  `_reap_dead_state()` (`app.py` ~890-904) cleans up when that child process
  dies — which it reliably does on an `ai-dev-switchboard` service
  restart/stop, since it's in the same systemd cgroup (`KillMode=
  control-group` default) and gets killed along with the parent. This is
  the precedent for "how a spawnable, on-demand, loopback-bound local
  process gets a toggle + `_publish`/`_unpublish` URL", but its state model
  (trust the in-memory `Popen` handle) does **not** fit Taiga.
- **host-control** (`app.py` `host_run()` ~916-924, `HOST_CONTROL_ENABLED`/
  `HOST_LABEL` config, the `s.host_enabled` singleton row in `refresh()`
  ~1108, `docs/ARCHITECTURE.md` "In-memory state and its one sharp edge")
  is **singleton**: exactly one row, not per-project, no engine picker
  (`engineRow()` is only called for `kind === 'inst'`). Its state is
  **never** trusted in memory — every `/status` poll (every 4s,
  `setInterval(refresh, 4000)`) re-queries a fresh `host_run("status")`
  subprocess call, because the thing being controlled (a session on a
  genuinely separate machine) outlives `app.py`'s own process regardless of
  what `app.py` remembers.
- **Taiga needs both halves**: it's a *singleton* row like host-control (one
  shared instance, not one per project — `docs/BACKLOG.md` item 1 is
  explicit: "on/off toggle + link, not a full project-per-row entry"), but
  it's a *local* thing like code-server (runs on this box, needs
  `_publish`/`_unpublish` + `PUBLISH_MODE` handling, not an SSH round-trip
  to a different machine). And critically, its lifecycle state must be
  queried fresh like host-control's, **not** trusted from an in-memory
  handle like code-server's — Taiga's containers are managed by `dockerd`,
  entirely outside `app.py`'s process tree, so they do **not** die when the
  `ai-dev-switchboard` systemd unit restarts. An in-memory
  `_taiga_procs`-style dict would silently go stale exactly the way
  `docs/ARCHITECTURE.md` already warns about for `_session_urls`, except
  worse here because the underlying thing being tracked routinely outlives
  a restart instead of rarely doing so.
- **No Docker/container-orchestration usage exists anywhere in this repo
  today** (confirmed: `grep -ri docker` across the whole tree returns
  nothing but this spec). This is a real first — see "Open questions"
  below for the explicit callout.

**Confirmed operationally** (via a fetch of `taiga-docker`'s current
`stable`-branch `docker-compose.yml`, `github.com/taigaio/taiga-docker`):
Taiga's own supported deployment is a 9-service Compose stack
(`taiga-db` (Postgres), `taiga-back`, `taiga-async`,
`taiga-async-rabbitmq`, `taiga-events`, `taiga-events-rabbitmq`,
`taiga-front`, `taiga-protected`, `taiga-gateway`), reading config from a
`.env` file, with exactly one container (`taiga-gateway`, an nginx
front door) publishing a host port — `9000:80` by default, unauthenticated
bind (all interfaces) unless overridden. This matches `docs/BACKLOG.md`
item 1's "several GB RAM" callout: Postgres + 2x RabbitMQ + a Django
backend + async workers is a genuinely heavy stack for a homelab box,
which is exactly why this spec keeps it **off by default** post-install
(see Goals) rather than auto-starting it.

## Proposed approach

### The Docker-dependency decision (read "Open questions" for the flagged tradeoff)
There is no lightweight, no-Docker path to a real, maintainable self-hosted
Taiga: hand-packaging Django + 2x RabbitMQ + Postgres + an nginx gateway as
systemd units would mean this project taking on ongoing maintenance of
someone else's multi-service packaging (tracking every Taiga version bump,
migration step, and inter-service config knob by hand) — work the
upstream `taiga-docker` repo already does and keeps current. **This spec's
call: `--with-taiga` shells out to Taiga's own official `taiga-docker`
Compose stack** (`stable` branch, `github.com/taigaio/taiga-docker`),
accepting that this is the first Docker dependency this codebase has ever
had. This is flagged explicitly under Open Questions for the user's
sign-off before build starts, per this cycle's brief — proceed only once
that's confirmed.

### `install.sh` changes
Follow the existing flag-parsing pattern exactly (`install.sh` lines
47-60): add `WITH_TAIGA=0` and a `--with-taiga) WITH_TAIGA=1 ;;` case arm.

New install block, gated on `[ "$WITH_TAIGA" -eq 1 ]`, placed after the
existing code-server block (~line 166) so it can reuse the same
`path_has_symlink`/`set_env`/`get_env`/`random_token` helpers already
defined earlier in the script:

1. **Docker itself** — `if [ "$WITH_TAIGA" -eq 1 ] && ! command -v docker
   >/dev/null 2>&1`, install via Docker's own official convenience script
   (`curl -fsSL https://get.docker.com | sh`), mirroring the exact
   curl-pipe-sh precedent code-server already uses one block above
   (`install.sh` line 130) rather than the distro's often-stale `docker.io`
   apt package. Idempotent: skip entirely if `docker` is already on the
   box (never assume ownership of a pre-existing Docker install someone
   else set up). After install, verify `docker compose version` works
   (the Compose *plugin*, not the old standalone `docker-compose` v1
   binary) — if it doesn't, echo a clear warning and continue rather than
   `set -e`-aborting the whole install (same "warn and continue" spirit as
   the ttyd-no-prebuilt-binary branch, `install.sh` ~123-125).
2. **The `taiga-docker` checkout** — `TAIGA_DIR=/opt/ai-dev-switchboard-taiga`
   (parallel to the existing `/opt/ai-dev-switchboard-src` bootstrap-clone
   pattern at the top of this same file). `[ -d "$TAIGA_DIR/.git" ] ||
   git clone --branch stable --depth 1
   https://github.com/taigaio/taiga-docker.git "$TAIGA_DIR"`. **Do not**
   `git pull` on re-run — pinned at whatever commit was first cloned,
   matching this file's own "never clobbers already-set values" philosophy
   (see "Open questions" — flagged as an assumption, not a hard requirement).
3. **Config** — copy `taiga-docker`'s example env file to its real `.env`
   if not already present (same "copy example only if missing" idiom as
   `switchboard.env`/`git-hosting.env` a few lines up), then reuse this
   same script's own `set_env`/`get_env` helpers (they're generic
   `<file> <key> <value>` functions, not switchboard-specific) to fill in:
   - `SECRET_KEY`, `POSTGRES_PASSWORD`, RabbitMQ credentials — generated
     once via the existing `random_token` helper, preserved on re-run
     exactly like `TOTP_SECRET`/`SIMPLE_PASSWORD` already are.
   - `TAIGA_SCHEME=http`, `TAIGA_DOMAIN` derived automatically from
     whatever `PUBLISH_MODE`/`BASE_URL` already resolved to earlier in
     this same install run (`BASE_URL`'s host in `tailscale` mode, else
     `localhost`) — no separate interactive prompt (see "Open questions").
   - The exact `.env` key names must be verified against whatever's
     actually in the `stable` branch at implementation time, not assumed
     from this spec — `taiga-docker` is a live external repo that can
     rename keys between versions (see "Open questions").
4. **Loopback-only binding** — `taiga-gateway`'s default `9000:80` port
   mapping binds all interfaces, which conflicts with this project's
   "everything binds `127.0.0.1` only, `PUBLISH_MODE`/a reverse proxy
   decides real exposure" rule (`docs/ARCHITECTURE.md` "Per-project
   terminals... bind to `127.0.0.1` only"). Write a
   `docker-compose.override.yml` next to the cloned `docker-compose.yml`
   (Compose auto-merges `docker-compose.yml` + `docker-compose.override.yml`
   in the same directory — this is the standard Compose mechanism for
   local overrides without touching the upstream-tracked file, so a future
   manual `git pull` in `$TAIGA_DIR` never conflicts with it) that
   overrides just `taiga-gateway`'s `ports:` to `"127.0.0.1:${TAIGA_PORT}:80"`.
5. **Pre-pull images at install time, not first toggle** — run
   `docker compose -f "$TAIGA_DIR/docker-compose.yml" -f
   "$TAIGA_DIR/docker-compose.override.yml" pull` (pull only, **not**
   `up`) during install. Without this, the *first* time someone flips the
   UI toggle on, that HTTP request blocks on pulling 9 images over the
   network — a bad first impression and a real timeout risk. Pulling at
   install time means every later toggle-on is just "start already-cached
   containers", fast. Warn-and-continue (not fatal) if the pull fails
   (e.g. no network at install time) — Taiga stays installed-but-unpulled,
   and the first toggle-on will just be slow instead, same UX depth as
   `ttyd`'s "no prebuilt binary, install yourself" degrade path.
6. **Wrapper scripts + sudoers** — see "Crossing the privilege boundary"
   below.
7. **`switchboard.env`** — `set_env "$ENV_FILE" TAIGA_ENABLED 1`,
   `TAIGA_PORT` (a fixed default, e.g. `9000` — no interactive prompt,
   matching `TTYD_BIN`/`CODE_SERVER_BIN` being plain non-interactive
   defaults today), `TAIGA_LABEL` (default `"Taiga"`),
   `TAIGA_UP_SCRIPT`/`TAIGA_DOWN_SCRIPT`/`TAIGA_STATUS_SCRIPT` (the
   installed `/usr/local/bin/...` paths, mirroring how
   `NEW_PROJECT_FROM_UPLOAD_SCRIPT` is recorded today).
8. **Final summary block** (`install.sh` ~308-318) — when
   `WITH_TAIGA=1`, print an explicit resource-cost + next-steps note:
   Taiga runs 9 containers and can use several GB RAM once turned on; it
   stays off until toggled in the web UI; and point at `taiga-docker`'s
   own documented one-time "create your first admin user" command to run
   once the stack is up (this project deliberately doesn't automate that
   step — see Non-goals).

### Crossing the privilege boundary
`SVC_USER` (the unprivileged process `app.py` runs as) cannot be added to
the `docker` group to run `docker compose` directly — Docker group
membership is root-equivalent (full access to the Docker socket, which can
mount arbitrary host paths into a container), a much broader grant than
every other sudoers rule this project uses, all of which are narrowly
scoped to one specific binary/script (`docs/ARCHITECTURE.md`
"Processes and privilege boundaries"). **This is a deliberate, called-out
deviation from the RUN_USER/SVC_USER user-separation model used
everywhere else in this project**: Docker daemon access is inherently
root-equivalent regardless of which user nominally owns a container, so
there's no "run Taiga's containers as RUN_USER" equivalent to reach for.
The mitigation is the same one this project already uses at the
command-narrowing layer instead of the user-separation layer: three tiny,
fixed, zero-argument wrapper scripts, whitelisted individually in sudoers,
each doing exactly one `docker compose` invocation against a hardcoded
`$TAIGA_DIR` — no passthrough arguments, no injection surface. This
mirrors `host-agent/{host-start,host-stop,host-status}.sh` +
`host_run()`'s action-triplet shape (`app.py` ~916-924) far more closely
than `new-project-from-upload.sh`'s single-script-with-positional-args
shape, since host-control already established exactly this "fixed
start/stop/status triplet, root-run, zero-trust of caller-supplied
arguments" pattern for an analogous "toggle a persistent external thing"
case:

- `scripts/taiga-up.sh` → installed as
  `/usr/local/bin/ai-dev-switchboard-taiga-up.sh` → `cd "$TAIGA_DIR" &&
  docker compose -f docker-compose.yml -f docker-compose.override.yml
  up -d`
- `scripts/taiga-down.sh` → `...taiga-down.sh` → `... down`
- `scripts/taiga-status.sh` → `...taiga-status.sh` → prints `on` or `off`
  as its first line (same single-line contract `host-status.sh` already
  uses, per `host_run()`'s `out[0] == "on"` check) — e.g. based on whether
  `docker compose ps taiga-gateway --format '{{.State}}'` reports
  `running`.

All three take **zero arguments** (even narrower than
`new-project-from-upload.sh`'s `*`-suffixed sudoers line), `$TAIGA_DIR`
hardcoded inside each script the same way `new-project-from-upload.sh`
sources `switchboard.env` and falls back to a hardcoded default (follow
that exact idiom — `CONFIG=/etc/ai-dev-switchboard/switchboard.env;
[ -f "$CONFIG" ] && source "$CONFIG"`). Sudoers additions (gated behind
`WITH_TAIGA`, inside the existing sudoers-generation block,
`install.sh` ~237-251):

```
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-taiga-up.sh
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-taiga-down.sh
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-taiga-status.sh
```

### `app.py` changes
New config reads (alongside `HOST_CONTROL_ENABLED` etc., ~line 107):
`TAIGA_ENABLED`, `TAIGA_LABEL`, `TAIGA_PORT`, `TAIGA_UP_SCRIPT`,
`TAIGA_DOWN_SCRIPT`, `TAIGA_STATUS_SCRIPT`.

```python
def taiga_run(action: str) -> str:
    assert action in ("up", "down", "status")
    script = {"up": TAIGA_UP_SCRIPT, "down": TAIGA_DOWN_SCRIPT,
              "status": TAIGA_STATUS_SCRIPT}[action]
    r = subprocess.run(["sudo", script], capture_output=True, text=True,
                       timeout=(10 if action == "status" else 90))
    return r.stdout.strip()
```
(Longer timeout for `up`/`down` than `host_run`'s 30s — a local
`docker compose up -d`/`down` against an already-pulled stack should be
fast, but give it real headroom; `status` stays short since it's just
`docker compose ps`.)

URL handling deliberately does **not** reuse `_publish()` on every
`/status` poll — `_publish()` has a real side effect (re-issuing
`tailscale serve --bg ...`) meant to run once per toggle-on, not once
every 4 seconds. Split into a registration call (toggle time only) and a
pure display-string function (safe to call every poll):

```python
TAIGA_URL_PATH = "/taiga"  # fixed, singleton — no per-name path like /term or /code

def _taiga_display_url() -> str:
    return f"{BASE_URL}{TAIGA_URL_PATH}" if PUBLISH_MODE == "tailscale" \
        else f"http://127.0.0.1:{TAIGA_PORT}"
```
Toggle-on: `taiga_run("up")` then `_publish(TAIGA_URL_PATH, TAIGA_PORT)`
(registers the tailscale-serve path in `tailscale` mode; a no-op return in
`none` mode, matching `_publish`'s existing behavior). Toggle-off:
`_unpublish(TAIGA_URL_PATH)` then `taiga_run("down")`. No per-name port
allocator dict is needed (unlike `_code_port()`/`_ttyd_port()`) — Taiga is
a singleton with one fixed port.

`/status` (`do_GET`, ~2107-2128): alongside the existing `host_enabled`/
`host`/`host_url` triplet, add a `taiga_enabled`/`taiga`/`taiga_label`/
`taiga_url` triplet, computed the same way — fresh `taiga_run("status")`
call every poll (not an in-memory dict), `taiga_url` only populated via
`_taiga_display_url()` when on:
```python
taiga_on, taiga_url = False, None
if TAIGA_ENABLED:
    out = taiga_run("status").splitlines()
    taiga_on = bool(out) and out[0] == "on"
    taiga_url = _taiga_display_url() if taiga_on else None
```

`do_POST` (~2169 on): new branch alongside the existing `host`
on/off branch —
```python
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
```
This sits after the shared TOTP gate (~2161-2167) exactly like the
existing `host`/`instance`/`code` branches — it inherits 428/403 TOTP
behavior for free, no special-casing.

### Frontend (`PAGE_TEMPLATE`, `refresh()`/`row()`/`actionPath()`)
Add a `taiga_enabled` row to `refresh()` (~1108) the same way the host row
is added, as a **singleton row alongside the host row, not mixed into the
per-project `instances` loop**:
```js
if (s.taiga_enabled) html += row(s.taiga_label, s.taiga, s.taiga_url, 'taiga', null, '', null, false, null);
```
`row()`'s existing `kind === 'inst'` guards on `engineRow()`/`codeRow()`
already correctly exclude both for any other `kind` (including `'taiga'`)
with no change needed there. `actionPath()` (~1157) needs one new line:
```js
if (kind === 'taiga') return '/taiga/' + (on ? 'on' : 'off');
```
Exact visual placement (before/after the host row, any RAM-cost tooltip
or badge, etc.) is a `ux-designer` call for `docs/design.md`, not
prescribed here — the functional contract above (singleton row, no engine
picker, on/off + link) is what this spec fixes.

### `config/switchboard.env.example`
Document the new keys in a new `## Optional: self-hosted Taiga (--with-taiga)`
section, following the exact comment style/depth of the existing
`HOST_CONTROL_ENABLED` section (lines 87-103) — `TAIGA_ENABLED`,
`TAIGA_PORT`, `TAIGA_LABEL`, and the three script-path variables, all
marked "install.sh sets these for you when you pass --with-taiga".

## Affected areas
- `install.sh` — new flag, Docker install block, `taiga-docker` clone +
  config + pre-pull, wrapper-script install, sudoers additions,
  `switchboard.env` keys, final summary note. (Single file, but see note
  below on why this isn't split further.)
- `scripts/taiga-up.sh`, `scripts/taiga-down.sh`, `scripts/taiga-status.sh`
  — three new small root-run wrapper scripts.
- `app/app.py` — new config reads, `taiga_run()`, `_taiga_display_url()`,
  `/status` fields, new `do_POST` branch, `refresh()`/`actionPath()` JS.
- `config/switchboard.env.example` — new documented section.
- No data model / schema changes. No changes to existing endpoints'
  request/response shapes beyond additive new `/status` fields and one new
  `/taiga/{on,off}` route.

This does touch installer + a new privileged-script layer + the app +
frontend JS, which on paper looks like several architectural layers — but
it's the same shape (and comparable size) as the already-shipped
code-server and host-control features, each of which landed as one spec/
one build cycle in this same repo. Not splitting further.

## Edge cases
- **Re-running `install.sh --with-taiga`** on a box that already has it
  installed: must not re-clone (checked via `$TAIGA_DIR/.git`), not
  regenerate secrets (checked via `get_env` returning a non-empty existing
  value, same idiom as `TOTP_SECRET`), not restart already-stopped
  containers, not duplicate sudoers lines (whole file is regenerated
  deterministically each run, same as today).
- **Docker already present** (installed by the operator for something
  else, e.g. via a different distro path than `get.docker.com`) — the
  `command -v docker` check must skip the install step entirely, never
  touch their existing Docker config.
- **`docker` present but the Compose *plugin* isn't** (an old standalone
  `docker-compose` v1 binary, no `docker compose` subcommand) — detect via
  `docker compose version`, warn clearly and continue (not a fatal
  `set -e` abort), leaving Taiga installed-but-not-functional until the
  operator sorts out their own Compose install — matches the ttyd
  no-prebuilt-binary degrade path already in this file.
- **No network at install time** (image pull fails) — warn and continue;
  Taiga stays configured but with uncached images, so the first UI
  toggle-on will simply be slow (pulls then) instead of the install
  failing outright.
- **`app.py` restarts while Taiga is running** — `/status` must still
  report `taiga: true` with a correct URL on the very next poll, with no
  re-toggle needed — this is the central reason state is queried fresh via
  `taiga_run("status")` every poll rather than trusted from memory (see
  Background).
- **Rapid double-toggle** (two quick `POST /taiga/on`, or on-then-off
  before the first completes) — `docker compose up -d`/`down` are both
  idempotent by design, matching how `host_run()`'s SSH calls are already
  assumed idempotent with no extra locking in this codebase; no new
  locking needed here either.
- **Toggling `/taiga/on` or `/taiga/off` when `TAIGA_ENABLED` is false**
  (e.g. someone hand-edits `switchboard.env` without ever having run
  `--with-taiga`, so the scripts/checkout don't exist) — 404, exactly
  mirroring the existing `host disabled` → 404 branch.
- **TOTP not yet verified this session** — inherited automatically from
  the shared gate in `do_POST`; no special-casing needed, but worth an
  explicit acceptance criterion (below) since it's easy for a reviewer to
  assume new routes need their own check.
- **Disk space** — 9 images plus Postgres/RabbitMQ data volumes need real
  disk, not just RAM; call this out alongside the RAM warning in the
  install summary (Proposed approach, step 8).
- **Platform**: this feature only makes sense on `x86_64`/`aarch64` Linux
  like the rest of the installer (Docker's convenience script already
  handles that distinction internally) — no new platform branching needed
  beyond what `get.docker.com` itself does.

## Acceptance criteria
- [ ] Given a box without Docker, when `install.sh --with-taiga` is run,
      then `docker` and the Compose plugin are installed, `/opt/ai-dev-switchboard-taiga`
      contains a `stable`-branch checkout with generated secrets and a
      `docker-compose.override.yml` binding `taiga-gateway` to
      `127.0.0.1:$TAIGA_PORT`, images are pre-pulled, and no Taiga
      containers are running yet.
- [ ] Given `install.sh --with-taiga` completes, then `switchboard.env` has
      `TAIGA_ENABLED=1` plus the port/label/script-path keys, the three
      wrapper scripts exist at `/usr/local/bin/ai-dev-switchboard-taiga-{up,down,status}.sh`
      mode 755, and `/etc/sudoers.d/ai-dev-switchboard` grants `SVC_USER`
      exactly those three scripts as root (`visudo -cf` still passes).
- [ ] Given `install.sh` is run **without** `--with-taiga`, then no Docker
      install is attempted, `TAIGA_ENABLED` is unset, and the web UI is
      byte-for-byte unchanged in its rendered rows versus before this
      feature (no empty/broken Taiga row appears).
- [ ] Given `TAIGA_ENABLED=1` right after install, when `/status` is
      polled, then the response reports Taiga off (containers not
      auto-started by install).
- [ ] Given the Taiga row's toggle is switched on with a valid TOTP code,
      when the request completes, then `docker compose ps` under
      `/opt/ai-dev-switchboard-taiga` shows `taiga-gateway` running, and
      the next `/status` poll reports Taiga on with a URL (loopback in
      `PUBLISH_MODE=none`, `BASE_URL/taiga` in `PUBLISH_MODE=tailscale`).
- [ ] Given the Taiga row's toggle is switched off, when the request
      completes, then all `taiga-docker` containers are stopped and, in
      `tailscale` mode, the `/taiga` `tailscale serve` path mapping is
      removed.
- [ ] Given `app.py`/the systemd service restarts while Taiga containers
      are already running, when `/status` is polled afterward, then Taiga
      still reports on with a correct URL, with no re-toggle required.
- [ ] Given TOTP has not yet been verified this browser session, when
      `POST /taiga/on` or `/taiga/off` is called, then it returns 428
      (no code) / 403 (wrong code) exactly like the existing `instance`/
      `host` toggle routes, with no bespoke Taiga-specific auth code.
- [ ] Given both project rows and the Taiga row are visible, then Taiga
      renders as exactly one always-present row (when enabled) with no
      engine picker, distinct from the per-project `instances` list —
      never one row per project.

## Open questions
- **The Docker dependency itself** — this is the one flagged explicitly
  per this cycle's brief, not a minor implementation detail: shipping
  `--with-taiga` means this codebase takes on its first-ever Docker
  dependency (confirmed nothing Docker-related exists in the repo today).
  This spec's assumption is **yes, proceed** — shelling out to Taiga's own
  official `taiga-docker` Compose stack is the only realistic way to get a
  maintainable self-hosted Taiga (see "The Docker-dependency decision"
  above for the reasoning) — but this materially changes the project's
  dependency footprint for anyone who opts in, and should get explicit
  user sign-off before build starts, not just be inferred from this spec
  existing.
- **`taiga-docker`'s exact `.env` key names** — verified today
  (`SECRET_KEY`, `POSTGRES_PASSWORD`, RabbitMQ credentials, `TAIGA_SCHEME`,
  `TAIGA_DOMAIN`) against the live `stable` branch, but it's an external
  repo that can rename keys in a future version bump before this build
  actually runs. Assumption: the developer re-verifies against whatever's
  actually in the cloned `stable` branch at implementation time rather
  than trusting these names as gospel from this spec.
- **No auto-pull/upgrade of the `taiga-docker` checkout on re-run** —
  assumed pinned-at-first-clone (see Proposed approach step 2), matching
  this installer's existing "never clobbers already-set values"
  philosophy. Flag if a future session wants an explicit
  `--upgrade-taiga`-style path instead.
- **`TAIGA_DOMAIN`/`TAIGA_SCHEME` derived automatically, no interactive
  prompt** — assumed acceptable (derived from `PUBLISH_MODE`/`BASE_URL`,
  same values already computed earlier in the same install run); an
  operator who wants a custom domain can hand-edit `taiga-docker`'s own
  `.env` afterward, same as any other `taiga-docker` knob this project
  doesn't manage.
- **`TAIGA_PORT` fixed default (9000), not interactively prompted** —
  matches `TTYD_BIN`/`CODE_SERVER_BIN` being plain env defaults with no
  prompt today; flag if a collision with something else on 9000 turns out
  to be common enough to warrant a prompt.
- **One shared Taiga instance for the whole box** (not one per switchboard
  project) — this is explicitly what `docs/BACKLOG.md` item 1 already
  settled ("Open for the future session: ... whether one shared Taiga
  project covers all switchboard projects or one per project folder" is
  about organizing *within* Taiga's own multi-project support, once
  logged in — not about running multiple Taiga *instances*, which this
  spec does not do).

## Risk / rollback notes
- **Blast radius is bounded by "off by default"**: install prepares
  everything but starts nothing, so `--with-taiga` alone (before ever
  flipping the toggle) costs disk space (checkout + pulled images) but no
  extra RAM/CPU at runtime.
- **Immediate rollback**: flip the toggle off in the UI — `docker compose
  down` stops all 9 containers right away, freeing RAM. This is the
  primary "something's using too many resources" escape hatch and is
  exercised by this spec's own acceptance criteria, not just a
  theoretical rollback path.
- **Full removal** (uninstalling Docker, deleting `$TAIGA_DIR`, removing
  the sudoers lines) is a manual step, not automated by this feature — see
  Non-goals. Document this as a known gap, not a bug, matching every other
  `--with-*` flag's lack of an uninstall path today.
- **What could break existing functionality**: nothing, if
  `--with-taiga` is never passed — every change here is additive and
  gated behind `WITH_TAIGA`/`TAIGA_ENABLED`, following the same pattern
  `--with-host-control`/`HOST_CONTROL_ENABLED` already use for zero
  impact on installs that don't opt in (see the "Given install.sh is run
  without --with-taiga..." acceptance criterion above).
- **Docker install itself is the riskiest single step** (runs a curl-piped
  root install script, same trust model this project already accepts for
  code-server's own `curl -fsSL https://code-server.dev/install.sh | sh`)
  — no new trust boundary is being crossed relative to precedent already
  in this file, just a new vendor.
