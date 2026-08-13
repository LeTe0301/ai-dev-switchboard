# Spec: Local git hosting UI + CI/CD (Gitea) — part 2a: install + container toggle

## Summary
Fold a self-hosted Gitea Docker Compose stack into the existing
`install.sh --with-git-hosting` flag (installed off by default, with a
resource-cost callout) and give it a singleton on/off-toggle-plus-link row
in the web UI, following the pattern 1a already established for Taiga —
**not** the repo-creation/registration flow (2b, rewiring `create_project()`
to actually use Gitea) or CI/CD auto-deploy (2c, Gitea Actions/webhooks),
both of which stay explicit future cycles that depend on 2a existing first.

## Goals
- `install.sh --with-git-hosting` — in addition to everything it already
  does today (unchanged, see Non-goals) — installs Docker (reusing 1a's
  install/verify logic, factored out so both features share it rather than
  duplicating it) and Gitea's own officially-documented two-service Docker
  Compose stack (`server` + `db`/Postgres, verified against
  `docs.gitea.com/installation/install-with-docker` — see "Background"),
  configured (generated secrets, loopback-only port bindings, `INSTALL_LOCK`
  pre-set so the public install wizard never becomes a race — see
  "Proposed approach") and **left stopped** after install, mirroring
  Taiga's "installed but off" contract exactly.
- A resource-cost callout at the install prompt/summary, calibrated to
  Gitea's actual (much lighter than Taiga's) footprint — see "Background".
- A new singleton row in the web UI (`kind: 'gitea'`, alongside the
  existing Taiga row, not a per-project row) with an on/off toggle and,
  when on, a link to the running instance.
- Toggling on starts the Gitea stack (`docker compose up -d`); toggling off
  stops it (`docker compose down`), actually freeing resources.
- State survives `ai-dev-switchboard` service restarts correctly, exactly
  like Taiga (Gitea's containers are not children of `app.py`'s process
  tree — state is queried fresh every poll, never trusted from memory).
- The toggle's frontend state machine is built defensively from the start,
  reusing (generalized, not copy-pasted) the exact logic 1a's three review
  rounds hardened for Taiga — see "Proposed approach: generalizing the
  toggle state machine".

## Non-goals
- **The repo-creation/registration flow** (`docs/BACKLOG.md` item 2's "the
  web UI's '+ New project' button... needs to call whatever replaces
  new-project.sh") — explicitly 2b. `create_project()` in `app.py` keeps
  calling the existing `NEW_PROJECT_SCRIPT` (today's `new-project.sh`)
  completely unchanged; 2a installs Gitea as inert infrastructure only.
- **CI/CD auto-deploy** (Gitea Actions / webhooks replacing
  `project-sync.sh` + `post-receive`) — explicitly 2c, depends on 2b's repo
  model existing first.
- **Removing or disabling any part of the existing git-hosting flow**
  (`scripts/git-hosting-setup.sh`, `new-repo.sh`, `new-dev-instance.sh`,
  `new-project.sh`, `project-sync.sh`, `target-setup.sh`, their sudoers
  rules, `docs/GIT_HOSTING.md`) — all of it keeps running exactly as it
  does today. See "Proposed approach: sequencing — additive, not a swap"
  for why this is the resolved default for 2a specifically, even though
  `docs/BACKLOG.md` item 2's overall decision is that Gitea eventually
  *replaces* this flow (that swap is 2b's moment, not 2a's).
- **Migration for existing `--with-git-hosting` users.** Confirmed default
  (user sign-off): **new-installs-only, no automatic migration.** Anyone
  who already ran `--with-git-hosting` before this cycle keeps using the
  old flow untouched; picking up Gitea (once 2b makes it functional) is a
  manual re-setup, not an automated migration path, and no automated
  migration path is ever planned for this item.
- **Automated Gitea admin-account creation.** Mirrors 1a's Taiga non-goal
  exactly: `install.sh` configures everything Gitea needs *except* the
  first admin user, and prints a pointer to the one-time manual step
  instead. See "Open questions" for why this one's a slightly closer call
  for Gitea than it was for Taiga, and why the default is still "don't
  automate it."
- **A `--without-git-hosting` uninstall flag**, or any automated removal of
  Docker/the Gitea containers/volumes — matches every other `--with-*` flag
  in this installer (including Taiga's), none of which have an uninstall
  path.
- **Any change to `AUTH_MODE`/TOTP.** The Gitea toggle inherits the
  existing shared TOTP gate in `do_POST` for free, exactly like Taiga's.
- **Exposing Gitea's git-over-SSH port beyond loopback**, or any decision
  about how a developer actually clones/pushes against Gitea (SSH key
  exposure vs. HTTPS+token) — there is no way to create a Gitea repo yet in
  2a's scope, so this decision is deferred to 2b. See "Open questions".
- **A `--with-gitea` flag separate from `--with-git-hosting`.**
  `docs/BACKLOG.md` item 2 is explicit that the Gitea install step "folds
  into... the existing `install.sh --with-git-hosting` flag" — this spec
  follows that literally, not a new flag name.

## Background / current state

### The current git-hosting flow (what this replaces, eventually — not yet)
`docs/GIT_HOSTING.md` + `scripts/git-hosting-setup.sh` today: a restricted
`git` user (real `git-shell`, no real shell) serving bare repos over the
box's **actual OpenSSH daemon** (standard port 22, public-key auth via
`$GIT_ROOT/.ssh/authorized_keys`) from `$GIT_ROOT/repos/`, plus a generic
rsync-based auto-deploy (`new-repo.sh`'s optional target-ip/target-path →
a `post-receive` hook → `project-sync.sh` on the target). `new-project.sh`
(`new-repo.sh` + `new-dev-instance.sh` in one step) is what `app.py`'s
`create_project()` (`app/app.py` line 519) calls via `NEW_PROJECT_SCRIPT`
when the web UI's "+ New project" button is used. Crucially: this flow's
SSH exposure is **not** governed by this project's `PUBLISH_MODE`/loopback
rule at all — it rides the box's already-externally-reachable real sshd,
entirely outside `app.py`'s control. Nothing in 2a touches any of this.

### 1a's Taiga precedent (the shape this spec follows)
`docs/spec.md`/`docs/implementation.md` at commit `ed84d73` (Taiga, 1a) is
the direct precedent — same "new optional self-hosted service with its own
container page" shape `docs/BACKLOG.md` calls out items 1 and 2 as sharing.
Key patterns reused here, verbatim where they still fit:
- **Docker as a first-class dependency, installed via
  `curl -fsSL https://get.docker.com | sh`**, idempotent
  (`command -v docker` check), never touching a pre-existing install.
  Already landed in this codebase for Taiga — 2a reuses it rather than
  re-deciding the tradeoff, but see "Proposed approach" for factoring it
  into a shared helper instead of a second copy-pasted block.
- **Loopback-only port binding via a `docker-compose.override.yml`**
  merged alongside the main compose file, keeping the web UI consistent
  with "everything binds `127.0.0.1` only, `PUBLISH_MODE` decides real
  exposure" (`docs/ARCHITECTURE.md`).
- **Pre-pull images at install time**, not first toggle, so the first UI
  toggle-on is fast instead of blocking on a cold pull.
- **Three fixed, zero-argument, root-run wrapper scripts + narrow sudoers
  entries** (`{taiga}-{up,down,status}.sh`) as the privilege-boundary
  mitigation for Docker-socket access being root-equivalent — this pattern
  (not `new-project-from-upload.sh`'s single-script-with-positional-args
  shape) is what 2a reuses again, since Gitea's toggle is the same
  "toggle a persistent external thing, root-run, zero-trust of caller
  input" shape as Taiga's, not a per-invocation scaffolding action.
- **State never trusted from memory** — `TAIGA_STATUS_SCRIPT`-equivalent
  queried fresh on every `/status` poll, because Docker-managed containers
  outlive `app.py`'s own process tree across a service restart.
- **The frontend toggle state machine, hardened by three real review
  rounds.** `app/app.py`'s `PAGE_TEMPLATE` JS (lines ~1146–1450 today)
  tracks `taigaPending`/`taigaWasRunning`/`taigaOffPendingCount` to survive:
  a slow (30–90s) async start/stop, a poll landing mid-toggle-off, and two
  overlapping toggle-off dispatches racing each other (`docs/test-review.md`
  Defects 1 and 2 for 1a, referenced directly in the current code's
  comments at lines 1163 and 1175). **This is real, hard-won history a
  fresh Gitea toggle must not re-derive from scratch by copy-pasting and
  renaming** — see "Proposed approach: generalizing the toggle state
  machine" for how 2a reuses it correctly instead.

### Gitea's actual official Docker setup (verified, not assumed)
Fetched `docs.gitea.com/installation/install-with-docker` directly (the
same verification step 1a did for `taiga-docker`). Confirmed:
- **Two services**, not Taiga's nine: `server` (the Gitea app itself) and
  `db` (Postgres 14 in the official example). No RabbitMQ, no separate
  async workers, no separate frontend/gateway containers — Gitea is a
  single Go binary serving its own web UI, API, and git-over-SSH directly.
- **Two ports**: `3000` (web) and `22` (git-over-SSH inside the container,
  conventionally mapped to a non-standard host port like `222` in the
  official example — this box's own real sshd is already on host port 22,
  serving the *existing* git-hosting flow's `git` user, so Gitea's SSH
  needs its own distinct host port regardless).
- **Fully configurable via `GITEA__section__KEY`-style environment
  variables** (`GITEA__database__DB_TYPE`, `GITEA__database__HOST`,
  `GITEA__database__NAME`/`USER`/`PASSWD`, etc.) — no `.env`-file-in-a-
  cloned-upstream-repo step the way `taiga-docker` needed; the compose file
  itself is short enough (2 services) that this project can author it
  directly rather than tracking an external "gitea-docker" companion repo
  (there isn't one canonical upstream repo to clone the way
  `taigaio/taiga-docker` exists for Taiga — see "Proposed approach" for
  what this means for 2a's install steps).
- **No default admin account** — a fresh Gitea normally shows a public,
  unauthenticated "finish install" wizard on first web visit (lets whoever
  gets there first pick the admin username/password and finalize DB
  config) unless `GITEA__security__INSTALL_LOCK=true` is set, in which case
  DB/config must already be fully supplied via env vars (as above) and the
  first admin account is created separately via a CLI command
  (`docker exec ... gitea admin user create ...`) — a single non-interactive
  command, unlike Taiga's own interactive superuser prompt. See "Proposed
  approach" for why 2a sets `INSTALL_LOCK=true` (closing the public-wizard
  race) while still not automating the admin-creation step itself.
- **Resource footprint**: Gitea's own documented system requirements are
  "2 CPU cores and 1GB RAM... typically sufficient for small teams," and
  it's documented as runnable on a Raspberry Pi / 512MB VPS. The full
  stack (Gitea + Postgres) is described as comfortably fitting **under 1
  GB RAM** — an order of magnitude lighter than Taiga's several-GB, 9-
  container footprint. This is the number the install-time/UI resource
  callout should be calibrated to (see "Proposed approach").

## Proposed approach

### Sequencing — additive, not a swap (resolved default)
The real question this spec has to settle explicitly (per this cycle's
brief): does 2a need to warn/refuse if `--with-git-hosting` is combined
with the *old* scripts still being invoked elsewhere, or can it ship as
purely additive infrastructure while the actual "replace" moment happens
in 2b?

**Resolved default: purely additive.** `install.sh --with-git-hosting`
keeps doing everything it does today, unchanged, and *also* installs +
configures (but leaves off) the Gitea stack under the same flag. Reasoning:
1. `docs/BACKLOG.md`'s "replace, not addition alongside" decision is about
   the **end state of item 2 as a whole** (2a+2b+2c complete), not a
   constraint on 2a's own intermediate commit — the multi-cycle plan
   already has 2b doing the actual rewire.
2. 2b hasn't shipped yet. `create_project()` still needs a *working*
   git-hosting flow the moment this cycle lands — if 2a disabled or
   warned against the old flow, that would break the web UI's "+ New
   project" button for everyone between now and 2b shipping, for zero
   benefit (Gitea can't create repos yet either way in 2a's scope).
3. Precedent: 1a shipped Taiga fully additively, touching nothing existing.
   Even though Gitea reuses the *same* flag name (not a new one), the
   "replace" is scoped at the flag's behavior *over time* across 2a→2b→2c,
   not at any single sub-spec's commit.
4. `docs/BACKLOG.md`'s own wording — "Gitea install step folds into (or
   replaces) `scripts/git-hosting-setup.sh` under the existing
   `install.sh --with-git-hosting` flag" — the "folds into" phrasing
   supports doing both side by side for now, not an immediate swap.

Concretely: the existing `if [ "$WITH_GIT_HOSTING" -eq 1 ]; then ... fi`
block in `install.sh` (currently lines ~403–420, running the old
`git-hosting-setup.sh` + installing the old wrapper scripts) gets Gitea's
new Docker Compose setup steps **appended inside the same block**, after
the existing steps (preserves today's exact output ordering/behavior for
anyone diffing install logs). No new top-level `if [ "$WITH_GITEA" ...]`
block the way Taiga got one — Gitea shares the existing flag, so it shares
the existing gate.

**Flagged for 2b's own spec, not decided here:** once 2b's rewire of
`create_project()` is live and confirmed working end-to-end, retiring the
old scripts/sudoers rules (`git-hosting-setup.sh`, `new-repo.sh`,
`new-dev-instance.sh`, `new-project.sh`, `project-sync.sh`,
`target-setup.sh`) becomes 2b's own explicit step, not automatic — don't
let this get lost between cycles.

### `install.sh` changes
Placed inside the existing `if [ "$WITH_GIT_HOSTING" -eq 1 ]` block
(~line 403 today), after the existing `git-hosting-setup.sh` call, reusing
this file's own `set_env`/`get_env`/`random_token`/`path_has_symlink`
helpers exactly like the Taiga block does:

1. **Docker itself** — reuse, don't duplicate. Refactor the Taiga block's
   inline Docker install/verify steps (today's lines 242–250) into a small
   `ensure_docker()` shell function defined once near the top of the file
   (alongside `set_env`/`get_env`), called from **both** the `--with-taiga`
   and `--with-git-hosting` blocks. Pure refactor for the Taiga call site —
   no behavior change there. This matters concretely once both flags are
   used together in one install run (a real case now that two features
   need Docker): Docker must only be installed/verified once, not twice.
2. **Authoring the compose file directly** (no upstream repo to clone,
   unlike `taiga-docker` — see "Background"). Ship
   `config/gitea-docker-compose.yml` in this repo's own tree (new file,
   ~20 lines: `server` + `db` services, following the exact shape verified
   against `docs.gitea.com/installation/install-with-docker`), installed
   via `install -m 644 "$REPO_DIR/config/gitea-docker-compose.yml"
   "$GITEA_DIR/docker-compose.yml"` — **overwritten on every re-run**
   (deterministic, like the sudoers file), since it's authored by this
   project, not a user-editable checkout the way `$TAIGA_DIR` is. This
   avoids Taiga's whole "don't clobber a manual `git pull`" concern
   entirely — there's no upstream to pull from.
   - `GITEA_DIR=/opt/ai-dev-switchboard-gitea` (parallel to `TAIGA_DIR`).
   - **Loopback-only binding**, baked directly into this project's own
     compose file (no override-file merge trick needed, unlike Taiga,
     since this project authors the whole file): `server`'s ports map
     `"127.0.0.1:${GITEA_PORT}:3000"` and `"127.0.0.1:${GITEA_SSH_PORT}:22"`.
   - Exact env var names (`GITEA__database__*`, `GITEA__security__*`,
     `GITEA__server__*`) must be re-verified against Gitea's live docs at
     implementation time, not assumed frozen from this spec — see "Open
     questions" (same caveat 1a flagged for `taiga-docker`'s `.env` keys).
3. **Config / secrets** — a `$GITEA_DIR/.env` file, written via this
   file's own `set_env` helper (generic, reusable across compose stacks,
   same as Taiga's usage):
   - `POSTGRES_PASSWORD`/Gitea's `GITEA__security__SECRET_KEY` and
     `GITEA__security__INTERNAL_TOKEN` equivalents — generated once via
     `random_token` on **first install only** (checked via `get_env`
     returning empty, same idiom `TOTP_SECRET` already uses — simpler than
     Taiga's "fresh clone" signal, since there's no clone step here to key
     off of), never regenerated on re-run.
   - `GITEA__security__INSTALL_LOCK=true` — always set (not conditional),
     closing the "first visitor claims the public install wizard" race
     described in "Background". This is what makes leaving Gitea installed
     but off between now and 2b safe even if someone stumbles onto its URL
     before an admin account exists — they hit a login page with nothing
     to log into yet, not an open "configure this server for me" wizard.
   - `GITEA__server__ROOT_URL` derived the same way Taiga's `TAIGA_DOMAIN`
     is (`install.sh` ~278–285): from `PUBLISH_MODE`/`BASE_URL` already
     resolved earlier in this same install run (the tailnet host in
     `tailscale` mode, else `http://127.0.0.1:$GITEA_PORT`).
   - `GITEA__database__DB_TYPE=postgres` + `HOST`/`NAME`/`USER`/`PASSWD` —
     follows the officially-documented example directly (see "Open
     questions" for the SQLite-vs-Postgres tradeoff this spec resolves in
     favor of Postgres, and why).
4. **Pre-pull images at install time** — `docker compose -f
   "$GITEA_DIR/docker-compose.yml" pull`, warn-and-continue (not fatal) on
   failure, identical reasoning and idiom to Taiga's step 5.
5. **Wrapper scripts + sudoers** — see "Crossing the privilege boundary"
   below; installed unconditionally once `WITH_GIT_HOSTING` is set,
   alongside (not instead of) the existing old-flow wrapper scripts this
   block already installs today.
6. **`switchboard.env`** — `set_env "$ENV_FILE" GITEA_ENABLED 1`,
   `GITEA_PORT` (default `3000`), `GITEA_SSH_PORT` (default `2222`, fixed
   non-interactive default — same simplicity precedent as `TAIGA_PORT`, no
   free-port scan), `GITEA_LABEL` (default `"Gitea"`), `GITEA_DIR`, and the
   three `GITEA_UP_SCRIPT`/`GITEA_DOWN_SCRIPT`/`GITEA_STATUS_SCRIPT` paths
   — same shape as Taiga's six keys.
7. **Final summary block** — alongside Taiga's existing "installed but left
   OFF" note, print Gitea's own version calibrated to its real footprint
   (not copy-pasted Taiga's "3–5 GB" number): stays off until toggled;
   uses well under 1 GB when running; before first use, create the first
   admin account via `docker exec -it <gitea container> gitea admin user
   create --admin --username ... --password ... --email ...` (one-time,
   not automated — see Non-goals) — this pointer differs from Taiga's in
   that it's a single scriptable command rather than an interactive
   wizard, worth saying so in the printed text since it's genuinely easier
   for the operator to actually run.

### Crossing the privilege boundary
Identical shape and identical reasoning to Taiga's (`docs/spec.md` at
`ed84d73`, "Crossing the privilege boundary") — Docker-socket access is
root-equivalent regardless of which user nominally owns a container, so
there's no `RUN_USER`-scoped equivalent to reach for. Same mitigation:
three tiny, fixed, zero-argument wrapper scripts, individually whitelisted
in sudoers, each doing exactly one `docker compose` invocation against a
hardcoded `$GITEA_DIR`:

- `scripts/gitea-up.sh` → `/usr/local/bin/ai-dev-switchboard-gitea-up.sh`
  → `cd "$GITEA_DIR" && docker compose up -d`
- `scripts/gitea-down.sh` → `...gitea-down.sh` → `... down`
- `scripts/gitea-status.sh` → `...gitea-status.sh` → prints `on`/`off` as
  its first line (same single-line contract as `taiga-status.sh`/
  `host-status.sh`), based on `docker compose ps server --format
  '{{.State}}'` reporting `running` (verify the compose service name is
  actually `server` at implementation time against the authored compose
  file from step 2 above — it's this project's own file, so this is
  self-consistent, just noting it explicitly).

Sudoers additions (inside the existing sudoers-generation block,
`install.sh` ~358–380, gated on `WITH_GIT_HOSTING` alongside the existing
`new-project.sh` rule already gated there):
```
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-up.sh
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-down.sh
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-status.sh
```

### `app.py` backend changes
New config reads (alongside `TAIGA_ENABLED` etc., ~line 123):
`GITEA_ENABLED`, `GITEA_LABEL`, `GITEA_PORT`, `GITEA_UP_SCRIPT`,
`GITEA_DOWN_SCRIPT`, `GITEA_STATUS_SCRIPT`. (`GITEA_SSH_PORT` is read by
the wrapper scripts, not `app.py` — the web UI never touches the SSH port,
per Non-goals.)

```python
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
```
Toggle-on: `gitea_run("up")` then `_publish(GITEA_URL_PATH, GITEA_PORT)`.
Toggle-off: `_unpublish(GITEA_URL_PATH)` then `gitea_run("down")`. Exactly
Taiga's split (registration-time side effect vs. pure per-poll display
string) for exactly the same reason: `_publish()` must not re-run on every
4s `/status` poll.

`/status` (`do_GET`): a `gitea_enabled`/`gitea`/`gitea_label`/`gitea_url`
quadruplet, computed the same way as Taiga's — fresh `gitea_run("status")`
call every poll, never an in-memory dict:
```python
gitea_on, gitea_url = False, None
if GITEA_ENABLED:
    out = gitea_run("status").splitlines()
    gitea_on = bool(out) and out[0] == "on"
    gitea_url = _gitea_display_url() if gitea_on else None
```

`do_POST`: a new branch, identical shape to the existing `taiga` branch,
sitting after the shared TOTP gate:
```python
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
```

### Frontend — which precedent fits, and generalizing the toggle state machine
**Row shape: Taiga's singleton pattern, not code-server's per-project
pattern.** Gitea is one shared box-wide instance — even though it will
eventually *hold* many repos (once 2b ships), the switchboard's own UI
only ever needs one row for it, exactly like Taiga conceptually holding
many backlog projects internally but still surfacing as a single row. This
is a clean fit with the existing singleton precedent (`kind: 'taiga'`),
not the per-project `kind: 'inst'` + `codeRow()` pattern, which exists
because code-server is spawned fresh per project folder — Gitea isn't.

`refresh()` gets a `gitea_enabled` branch alongside the existing `taiga`
one, with its own resource badge text (calibrated to "well under 1 GB",
not copy-pasted Taiga's "3–5 GB"). `actionPath()` gets one new line:
`if (kind === 'gitea') return '/gitea/' + (on ? 'on' : 'off');`.

**Required, not optional: generalize `taigaPending`/`taigaWasRunning`/
`taigaOffPendingCount` into a per-kind state map before adding Gitea's
copy.** Per this cycle's brief — a fresh Gitea toggle must learn from
1a's history, not repeat it blind. Concretely:
```js
// One entry per singleton-toggle kind ('taiga', 'gitea', ...future ones).
// Same {pending, wasRunning, offPendingCount} shape 1a's three review
// rounds hardened for Taiga specifically — see docs/spec.md (this file)
// "Background" for what each field is guarding against.
let singletonToggleState = {
  taiga: {pending: null, wasRunning: false, offPendingCount: 0},
  gitea: {pending: null, wasRunning: false, offPendingCount: 0},
};
```
`refresh()`, `toggle()`, `handleActionResult()`, `cancelActionCode()`, and
`submitActionCode()` (today's lines ~1182–1450) all currently branch on
`kind === 'taiga'` and touch the three bare globals directly — each of
those branches becomes `kind in singletonToggleState` (or an explicit
`['taiga','gitea'].includes(kind)` check, developer's call on exact idiom)
reading/writing `singletonToggleState[kind]` instead. Per-kind values that
were hardcoded to Taiga's numbers (the 90s starting-timeout, the RAM badge
text) become parameters keyed by `kind` too — a small config object is
fine (`{taiga: {timeoutMs: 90000, badge: '⚠ ~3–5 GB RAM when running'},
gitea: {timeoutMs: 90000, badge: '⚠ ~1 GB RAM when running'}}`; Gitea's
stack starts meaningfully faster than Taiga's 9 containers in practice, so
a shorter timeout would also be reasonable, but keeping the same safe
upper bound is fine too — developer's call, not load-bearing for
correctness). **This is a pure refactor for the already-shipped, already-
reviewed Taiga behavior — no behavior change there, verified by re-running
whatever technique the reviewer used to catch 1a's Defects 1 and 2, now
against both kinds.** Exact visual placement of the Gitea row (order
relative to Taiga/host rows, badge styling) is a `ux-designer` call for
`docs/design.md`, not prescribed here — the functional contract above
(singleton row, no engine picker, on/off + link, generalized state
machine) is what this spec fixes.

### `config/switchboard.env.example`
New `## Optional: self-hosted Gitea (--with-git-hosting)` section,
following the exact comment depth/style of the existing Taiga section —
`GITEA_ENABLED`, `GITEA_PORT`, `GITEA_LABEL`, the three script-path
variables, all marked "install.sh sets these for you when you pass
--with-git-hosting."

### `install.sh`'s own top-of-file flag comment + `README.md`
Update the `--with-git-hosting` one-line description at the top of
`install.sh` (currently lines 15–16) to mention it now also installs
Gitea, mirroring how `--with-taiga`'s own description block (lines 21–25)
was added. Check `README.md`'s existing `--with-git-hosting` mentions
(lines 79–82, 105–107, 156, 160) for anywhere a one-line addition noting
"a self-hosted Gitea instance is also installed, off by default" is a
natural fit — a small doc touch, not a rewrite of `docs/GIT_HOSTING.md`
itself (that stays 2b's job, once the flow it documents actually changes).

## Affected areas
- `install.sh` — Docker-install logic factored into a shared
  `ensure_docker()` helper (reused by both `--with-taiga` and
  `--with-git-hosting`); new Gitea Compose config/secrets/pre-pull/wrapper-
  script/sudoers/`switchboard.env` steps appended inside the existing
  `WITH_GIT_HOSTING` block; updated flag-comment header; updated final
  summary block.
- `config/gitea-docker-compose.yml` — new file, authored directly by this
  project (no upstream repo to clone/pin, unlike Taiga).
- `scripts/gitea-up.sh`, `scripts/gitea-down.sh`, `scripts/gitea-status.sh`
  — three new small root-run wrapper scripts, same shape as the Taiga ones.
- `app/app.py` — new config reads, `gitea_run()`, `_gitea_display_url()`,
  `/status` fields, new `do_POST` branch; frontend JS generalization of
  the toggle state machine (`singletonToggleState`, replacing the
  Taiga-only globals) plus the new Gitea row.
- `config/switchboard.env.example` — new documented section.
- `README.md` — small mentions-update, not a rewrite.
- No data model / schema changes. No changes to existing endpoints'
  request/response shapes beyond additive new `/status` fields and one new
  `/gitea/{on,off}` route. `docs/GIT_HOSTING.md` and every existing
  git-hosting script are **unchanged** (see Sequencing above).

This is the same shape and comparable size to 1a's already-shipped Taiga
spec (installer + a new privileged-script layer + app backend + frontend
JS + config docs), which was accepted as a single cycle for the same
reason — not splitting further.

## Edge cases
- **Re-running `install.sh --with-git-hosting`** on a box that already has
  Gitea installed: must not regenerate secrets (checked via `get_env`
  returning a non-empty existing value), not restart already-stopped
  containers, not duplicate sudoers lines, and must re-write
  `docker-compose.yml`/`GITEA__server__ROOT_URL` deterministically every
  run (the compose file and `ROOT_URL` are the two things this project
  *does* want to keep current across `PUBLISH_MODE` changes, unlike the
  once-only secrets).
- **`--with-taiga` and `--with-git-hosting` both used in the same install
  run (in either order)** — Docker is installed/verified exactly once
  (shared `ensure_docker()`), both stacks get independently configured and
  pre-pulled, two independent singleton rows appear with no port
  collisions (`TAIGA_PORT=9000` vs `GITEA_PORT=3000`/`GITEA_SSH_PORT=2222`
  vs the dynamic ttyd/code-server ranges starting at 7700/7900).
- **Docker already present / Compose plugin missing / no network at
  install time** — identical warn-and-continue handling to Taiga's, reused
  verbatim via `ensure_docker()`.
- **`app.py` restarts while Gitea is running** — `/status` must still
  report `gitea: true` with a correct URL on the very next poll, no
  re-toggle needed (state queried fresh, never trusted from memory).
- **Rapid double-toggle / a poll landing mid-toggle-off** — the exact race
  class 1a's three review rounds found for Taiga must not resurface for
  Gitea; covered by construction via the generalized
  `singletonToggleState` reuse (see "Proposed approach"), and must be
  explicitly re-verified by the reviewer's testing pass for the `gitea`
  kind specifically, not assumed safe by analogy alone.
- **Old git-hosting's real sshd (host port 22) coexisting with Gitea's own
  internal SSH server (host port 2222, loopback-only)** — no conflict,
  different ports, different mechanisms (a real system sshd vs. a
  container's own SSH implementation); worth noting explicitly since it's
  a "two SSH-shaped things on one box" situation this project hasn't had
  before, even though Gitea's SSH isn't reachable by anyone yet in 2a's
  scope (see Non-goals).
- **`GITEA_PORT`/`GITEA_SSH_PORT` colliding with a port already in active
  use on the box** (e.g. an operator's own unrelated service already on
  3000) — fixed defaults, no automated free-port scan, same accepted
  limitation `TAIGA_PORT=9000` already has. Operator can hand-edit
  `switchboard.env` + the compose file's ports and re-run.
- **Someone reaches Gitea's URL before an admin account exists** (the
  window between toggle-on and the operator manually running `gitea admin
  user create`) — with `INSTALL_LOCK=true` baked in from install time,
  this is a login page with no account to log into, not an open
  configuration wizard; no data/config is at risk during that window.
- **Toggling Gitea off has no data-loss implication in 2a's scope**, since
  no real repos exist through it yet (registration is 2b) — Postgres's
  data volume persists across a toggle-off/on cycle regardless (Compose
  volumes aren't removed by `down` without `-v`), but this is currently
  moot. Flagged explicitly as something a future 2b/2c cycle needs to
  revisit once Gitea holds real, actively-pushed-to repos (e.g. whether
  "off" should be blocked/warned against if a push could be in flight) —
  not a 2a concern.

## Acceptance criteria
- [ ] Given a box with neither Docker nor Gitea installed, when
  `install.sh --with-git-hosting` runs, then Docker is installed (skipped
  if already present), the Gitea Compose stack (`server` + `db`, per
  Gitea's own official example) is configured under `$GITEA_DIR` with both
  ports bound to `127.0.0.1` only, images are pre-pulled, and the stack is
  left stopped (`docker compose ps` shows nothing running) after install
  completes.
- [ ] Given `install.sh --with-git-hosting` has already run once, when it
  is re-run, then no Gitea secrets are regenerated, no Gitea containers are
  started, and no sudoers lines are duplicated.
- [ ] Given install completed with `--with-git-hosting`, when the web UI
  loads, then a new singleton "Gitea" row appears (same visual family as
  the Taiga row — on/off toggle, resource badge, no engine picker), off by
  default.
- [ ] Given the Gitea row's toggle is switched on, when `/status` is
  polled repeatedly, then it eventually reports `gitea: true` with a
  working `gitea_url`, and opening that link reaches Gitea's own
  login/first-run page (not a public "finish install" wizard, since
  `INSTALL_LOCK=true` is pre-set).
- [ ] Given the Gitea row's toggle is switched off, when `/status` is next
  polled, then it reports `gitea: false` and `docker compose ps` confirms
  the containers are actually stopped.
- [ ] Given `app.py`/the systemd service restarts while Gitea's containers
  are still running, when `/status` is polled afterward, then it correctly
  reports `gitea: true` with no re-toggle needed.
- [ ] Given two rapid, overlapping toggle-off requests for the Gitea row
  (mirroring the exact race class 1a's three review rounds fixed for
  Taiga), when both resolve, then the row settles on an accurate final
  state — no stuck "starting…", no false "error", no incorrectly re-armed
  "unexpectedly stopped" indicator.
- [ ] Given the existing `--with-git-hosting` flow (the `git` user,
  `new-project.sh`, the web UI's "+ New project" button), when
  `install.sh --with-git-hosting` runs under 2a's changes, then all of it
  continues to work completely unchanged — `create_project()` still calls
  the existing `NEW_PROJECT_SCRIPT`, nothing about the old flow is
  disabled, warned about, or altered.
- [ ] Given a box with `--with-taiga` already installed, when
  `--with-git-hosting` (2a) is installed afterward (or both flags are
  passed in the same run), then Docker is not reinstalled/reconfigured a
  second time, and the Taiga and Gitea singleton rows work independently
  with no port collisions.
- [ ] Given TOTP/simple auth is enabled, when the Gitea on/off toggle is
  used, then it goes through the exact same shared TOTP gate as every
  other `/*/{on,off}` action (428/403 behavior), with no special-casing.

## Open questions
1. **DB choice: Postgres (this spec's default) vs. SQLite.** Gitea's own
   officially documented example uses Postgres; SQLite would mean one
   container instead of two and an even lighter footprint. This spec
   defaults to Postgres because (a) it matches what's actually documented
   as Gitea's official example rather than a hand-picked simplification,
   and (b) 2c's CI/CD auto-deploy will add exactly the kind of
   webhook/polling load Gitea's own docs say Postgres handles meaningfully
   better than SQLite — picking Postgres now avoids a future forced DB
   migration when 2c ships. This is a real tradeoff against this project's
   general minimal-footprint bias (an extra container, a bit more RAM) —
   flagging for confirmation rather than treating it as settled.
2. **Git-over-SSH exposure is left undecided (loopback-only for now).**
   2a keeps `GITEA_SSH_PORT` bound to `127.0.0.1`, meaning nobody off-box
   can actually clone/push via SSH yet. This is fine for 2a (there's no
   way to create a Gitea repo yet regardless), but 2b's own spec will need
   to explicitly decide how developers reach Gitea for real git operations
   — expose the SSH port on the tailnet/LAN, or route git-over-HTTPS
   through the same loopback+`tailscale serve` path everything else here
   already uses (avoiding a wholly new "raw TCP port on a non-loopback
   interface" precedent this project has never needed before, since the
   *existing* git-hosting flow's SSH exposure rides the box's own already-
   externally-reachable sshd, not something `app.py`/`PUBLISH_MODE`
   manages). Assumption stated so 2b doesn't have to rediscover this from
   scratch.
3. **Admin-account automation — closer call than Taiga's, still not
   automated.** Unlike Taiga's interactive superuser prompt, Gitea's
   `gitea admin user create --admin --username ... --password ... --email
   ...` is a single non-interactive CLI command, meaning `install.sh`
   genuinely *could* generate a password and run it automatically (the
   `--yes` non-interactive-install objection that ruled this out for Taiga
   doesn't apply the same way here). This spec still defaults to **not**
   automating it, for symmetry with Taiga, to avoid this sub-spec making
   an unreviewed credential-generation/display decision, and because the
   generated-password-in-install-output pattern isn't established
   elsewhere in this file for a *second* service in the same run. Worth
   revisiting as a small, separately-scoped follow-up if the manual step
   proves to be real friction once 2b makes Gitea actually useful.
4. **Exact env var/service names must be re-verified at implementation
   time**, not assumed frozen from this spec (`GITEA__database__*`,
   `GITEA__security__*`, `GITEA__server__*`, the `server`/`db` service
   names) — same caveat 1a flagged for `taiga-docker`'s `.env` keys, now
   pointing at `docs.gitea.com`'s live docs instead of a cloned repo's
   `.env.example`.
5. **Fixed port defaults (`GITEA_PORT=3000`/`GITEA_SSH_PORT=2222`), no
   free-port detection.** Same accepted-limitation precedent as
   `TAIGA_PORT=9000` — flagged, not treated as a gap to close in this
   cycle.
6. **Whether an on/off toggle stays the right UX for Gitea long-term.**
   Taiga is naturally used in bursts (backlog grooming sessions); Gitea,
   once 2b/2c make it hold real, actively-pushed-to repos, is more of an
   always-on service. This cycle's brief is explicit that 2a mirrors the
   toggle pattern regardless, so that's what's specced — flagging for a
   future cycle to revisit once there's real usage data, not blocking 2a.

## Risk / rollback notes
- Reuses an already-accepted risk category (Docker as a dependency, root-
  run wrapper scripts narrowly scoped via sudoers) rather than introducing
  a new one — the privilege-boundary reasoning was already reviewed and
  approved for Taiga in 1a.
- Nothing about the existing git-hosting flow is touched (see Sequencing),
  so rolling back 2a specifically means: stop running the new Gitea steps
  in `install.sh`, remove the three wrapper scripts + their sudoers lines
  + `$GITEA_DIR` (containers/volumes), and revert the `app.py`/frontend
  changes — the old git-hosting flow is unaffected either way, since 2a
  never modifies it.
- The one piece of this spec that touches already-shipped, reviewer-
  approved code is generalizing the Taiga-only toggle globals into
  `singletonToggleState`. This must be verified with the same rigor as any
  refactor of tested logic — re-run (or adapt) whatever test technique the
  reviewer used to catch 1a's Defects 1 and 2 against the *generalized*
  code path for both `taiga` and `gitea`, not just spot-check Gitea in
  isolation, before this is considered safe to ship.
