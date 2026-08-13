# Implementation: Local git hosting UI + CI/CD (Gitea) — part 2a: install + container toggle

(1a's and 1b's own implementation notes — the `install.sh --with-taiga`
flag + singleton UI toggle row, and the standalone `taiga_push_spec.py`
CLI — are preserved in git history: `git show ed84d73:docs/implementation.md`
for 1a, `git show aa2b56d:docs/implementation.md` for 1b. This file now
documents 2a only, per this cycle's `docs/spec.md`.)

## Summary
Folded a self-hosted Gitea Docker Compose stack (`server` + `db`/Postgres)
into the existing `install.sh --with-git-hosting` flag — installed
configured-but-stopped, off by default, purely additive alongside the
existing git-hosting flow (nothing in `scripts/git-hosting-setup.sh`,
`new-project.sh`, or `create_project()` was touched) — plus a new singleton
"Gitea" on/off toggle row in the web UI, following 1a's already-shipped
Taiga pattern (commit `ed84d73`) closely. Docker's install/verify logic was
factored out of the `--with-taiga` block into a shared `ensure_docker()`
helper, reused by both flags. The Taiga-only frontend toggle globals
(`taigaPending`/`taigaWasRunning`/`taigaOffPendingCount`) were generalized
into a per-kind `singletonToggleState` map, verified with a parametrized
version of the reviewer's own Node `vm`-based regression harness run against
both `taiga` and `gitea` kinds.

## Changes by file

- **`install.sh`**:
  - New `ensure_docker()` helper (next to `set_env`/`get_env`/`random_token`)
    — installs Docker via `get.docker.com` if missing, verifies the Compose
    plugin, sets `DOCKER_COMPOSE_OK`. The `--with-taiga` block's inline
    Docker steps were replaced with a call to it (pure refactor — same
    behavior, same warning path, just de-duplicated) so a single install run
    using both `--with-taiga` and `--with-git-hosting` only installs/checks
    Docker once.
  - Inside the *existing* `if [ "$WITH_GIT_HOSTING" -eq 1 ]` block (after the
    existing `git-hosting-setup.sh` call, before the block's closing `fi`):
    authors `$GITEA_DIR/docker-compose.yml` from
    `config/gitea-docker-compose.yml` (`install -D -m 644`, overwritten
    every re-run — this project's own file, nothing to preserve), writes
    `$GITEA_DIR/.env` (secrets generated once via `random_token`, checked
    via `get_env` returning empty — same idiom as `TOTP_SECRET`;
    `GITEA__security__INSTALL_LOCK=true` always set;
    `GITEA__server__DOMAIN`/`ROOT_URL` re-derived every run from
    `PUBLISH_MODE`/`BASE_URL`, same shape as Taiga's `TAIGA_DOMAIN`;
    `GITEA_PORT`/`GITEA_SSH_PORT` also written here so Compose's own `.env`
    auto-load can substitute `${GITEA_PORT}`/`${GITEA_SSH_PORT}` in the
    compose file's port mappings), pre-pulls images (warn-and-continue on
    failure), installs the three wrapper scripts, and writes
    `GITEA_ENABLED`/`GITEA_PORT`/`GITEA_LABEL`/`GITEA_DIR`/the three
    `GITEA_*_SCRIPT` paths to `switchboard.env`.
  - Sudoers: three new zero-argument entries
    (`ai-dev-switchboard-gitea-{up,down,status}.sh`) added inside the
    existing `if [ "$WITH_GIT_HOSTING" -eq 1 ]` sudoers stanza, alongside the
    pre-existing `new-project.sh` rule that stanza already had.
  - Top-of-file flag comment (`--with-git-hosting`) and the final summary
    block both updated to mention Gitea, mirroring how `--with-taiga`'s own
    summary block already documents Taiga — Gitea's version notes the
    "well under 1 GB RAM" footprint (not copy-pasted Taiga's "3-5 GB"), and
    that the existing git-hosting flow is completely unaffected.
- **`config/gitea-docker-compose.yml`** (new) — authored directly (no
  upstream repo to clone, unlike `taiga-docker`), verified live against
  `docs.gitea.com/installation/install-with-docker`'s own Postgres example
  (fetched at implementation time, not assumed from the spec — see
  "Verification performed" below): `server` (`docker.gitea.com/gitea:1.27.1`)
  + `db` (`docker.io/library/postgres:14`), both loading `.env` via
  `env_file:`, loopback-only port bindings (`127.0.0.1:${GITEA_PORT}:3000`,
  `127.0.0.1:${GITEA_SSH_PORT}:22`) baked directly into the file (no
  override-file merge trick needed, unlike Taiga).
- **`scripts/gitea-up.sh`**, **`scripts/gitea-down.sh`**,
  **`scripts/gitea-status.sh`** (new) — same shape as
  `taiga-{up,down,status}.sh`: zero-argument, root-run, `$GITEA_DIR`
  hardcoded (sourced from `switchboard.env`), `gitea-status.sh` prints
  `on`/`off` on its first stdout line based on
  `docker compose ps server --format '{{.State}}'`.
- **`app/app.py`**:
  - New config reads: `GITEA_ENABLED`, `GITEA_LABEL`, `GITEA_PORT`,
    `GITEA_UP_SCRIPT`, `GITEA_DOWN_SCRIPT`, `GITEA_STATUS_SCRIPT`.
  - `gitea_run(action)`, `GITEA_URL_PATH = "/gitea"`, `_gitea_display_url()`
    — identical shape to Taiga's equivalents.
  - `/status` (`do_GET`): a `gitea_enabled`/`gitea_label`/`gitea`/
    `gitea_url` quadruplet, computed via a fresh `gitea_run("status")` call
    every poll (never trusted from memory), same as Taiga's.
  - `do_POST`: new `elif parts[0] == "gitea" ...` branch, identical shape to
    the existing `taiga` branch, sitting after the shared TOTP gate.
  - **Frontend JS refactor** (the spec's highest-risk item): generalized
    `taigaPending`/`taigaWasRunning`/`taigaOffPendingCount` into
    `singletonToggleState = {taiga: {...}, gitea: {...}}`, plus a new
    `SINGLETON_TOGGLE_CONFIG` map (per-kind `timeoutMs`/`badgeText`/
    `badgeClass`/`errClass`/`spinnerClass`) and a shared
    `singletonToggleSub(kind, on, url)` function that both replaces the
    inline Taiga-only computation in `refresh()` and updates that kind's own
    state as a side effect (same computation, same order of operations, now
    parametrized). `row()`, `actionPath()`, `handleActionResult()`,
    `toggle()`, `cancelActionCode()`, and `submitActionCode()` all changed
    their `kind === 'taiga'` branches to `kind in singletonToggleState` (or
    `singletonToggleState[kind]` reads/writes) — no other behavior change.
  - New CSS: `.badge.gitea-resources { color: #66d9ff; }` (reusing Taiga's
    already-corrected, already-shipped contrast-verified color pairing
    verbatim — see "Deviations from spec" for the one instruction I did
    *not* follow literally), `.gitea-err`, `.gitea-starting-spinner` +
    `@keyframes gitea-spin`, parallel-named to Taiga's own classes (design.md
    explicitly leaves parallel-vs-unified naming as a developer's call).
- **`config/switchboard.env.example`** — new `## Optional: self-hosted
  Gitea (--with-git-hosting)` section, same comment depth/style as the
  existing Taiga section.
- **`README.md`** — two small additions: the `--with-git-hosting` bullet
  under "Use cases" now mentions Gitea; a new "A self-hosted Gitea singleton
  row" bullet added under "What you get", right after the existing
  "+ New project" bullet.
- **`tests/test_gitea.py`** (new) — backend tests mirroring
  `tests/test_taiga.py`'s structure exactly (`GiteaRunTests`,
  `GiteaDisplayUrlTests`, `GiteaEndpointTests`), written first (TDD — I
  confirmed all 13 failed with `AttributeError: module 'app' has no
  attribute 'gitea_run'` before writing any of `app.py`'s Gitea code, then
  watched them go green once `gitea_run`/`_gitea_display_url`/the `/status`
  fields/the `do_POST` branch were added).
- **`tests/test_singleton_toggle_frontend.js`** (new, replaces
  `tests/test_taiga_frontend.js` — see "Deviations from spec") — the exact
  same Node `vm`-based technique (`document`/`fetch`/timers stubbed, the real
  `<script>` extracted from `render_page()`), but parametrized over
  `kind` ('taiga' | 'gitea') so all six of the original Taiga race-condition
  tests (docs/test-review.md Defects 1 and 2 for 1a) run against **both**
  kinds against the one shared, generalized code path, plus one new
  cross-kind isolation test (toggling Gitea off mid-flight must not disturb
  Taiga's already-running state, and vice versa) that a per-kind-only test
  run couldn't catch, plus (added post-review, see "Fixes from review") one
  new per-kind resource-badge text/class test each. `rowHtml(kind)` scopes
  assertions to exactly one row's own HTML slice (anchored on that row's
  `toggle('<kind>',null,...)` onchange attribute) — needed once both rows
  render at once, since a raw "does the whole page contain the substring
  'stopped'" check is ambiguous the moment a second row exists. 15 tests
  total (was 13 pre-review).
- **`tests/test_taiga_frontend.js`** — deleted; its coverage is a strict
  subset of `test_singleton_toggle_frontend.js`'s `[taiga]`-prefixed cases
  (same assertions, same technique), so keeping both would mean maintaining
  the same race-condition logic twice.

## Fixes from review

`docs/test-review.md`'s testing pass found one must-fix defect (blocking)
and one should-fix defect (non-blocking, addressed in the same pass since
the developer was already going back in). Both are fixed:

- **Defect 1 (must-fix) — printed Gitea admin-account creation command
  failed as documented.** The reviewer ran a real Gitea 1.27.1 container in
  their sandbox and confirmed `docker exec -it ai-dev-switchboard-gitea
  gitea admin user create ...` (the command `install.sh`'s summary block
  printed verbatim) fails with `mustNotRunAsRoot()`, because `docker exec`
  defaults to the container's default user (`root` for this image) and the
  `gitea` binary refuses to run as root. The reviewer empirically verified
  `docker exec -it --user git ai-dev-switchboard-gitea gitea admin user
  create ...` succeeds. Fixed by adding `--user git` to the printed command
  in `install.sh`'s summary block (line ~598). **Independently re-verified
  live** (this session's sandbox, unlike the original implementation
  session, had a working Docker + Compose plugin): stood up the real,
  unmodified `config/gitea-docker-compose.yml` + a matching `.env` against
  real `docker.gitea.com/gitea:1.27.1`/`postgres:14` images, waited for "ORM
  engine initialization successful", then ran both the pre-fix command shape
  (`docker exec ai-dev-switchboard-gitea gitea admin user create ...`, no
  `--user git`) and the fixed shape (`docker exec --user git
  ai-dev-switchboard-gitea gitea admin user create ...`) against it —
  reproduced the exact `mustNotRunAsRoot()` failure for the former and the
  exact `New user 'testadmin' has been successfully created!` success for
  the latter, then tore the stack down (`docker compose down -v`). (`-it`
  itself couldn't be exercised in this non-interactive shell — "the input
  device is not a TTY" — but `-it` vs. plain/no-tty is orthogonal to the
  root-vs-`git`-user bug being fixed here; an operator running this in a
  real interactive terminal gets `-it`'s TTY allocation for free, same as
  before and unaffected by this fix.) Checked the rest of the repo
  for a second place that prints/documents this same literal command:
  `docs/spec.md` mentions it twice but both are elided design-rationale
  references (`docker exec ... gitea admin user create ...` and `gitea admin
  user create --admin --username ... --password ... --email ...`, neither
  includes the container name or `-it`, so neither is "the same command"
  reproduced verbatim) and wasn't touched, since editing the spec isn't this
  role's job; `docs/GIT_HOSTING.md` and `README.md` don't mention this
  command at all. `docs/implementation.md`'s own "Known limitations" bullet
  *did* repeat the un-fixed command verbatim and has been corrected here too.
- **Defect 2 (should-fix) — no automated test asserted per-kind badge
  text/class correctness.** The reviewer's own sabotage probe (hardcoding
  `row()`'s `const cfg = SINGLETON_TOGGLE_CONFIG[kind];` lookup to always
  read `SINGLETON_TOGGLE_CONFIG['taiga']`) passed the entire existing suite
  (135 Python + 13 JS tests) undetected. Added one new test per kind to
  `tests/test_singleton_toggle_frontend.js`
  (`[<kind>] resource badge shows this kind's own text/class while running,
  not another kind's`) that renders that kind's row while running and
  asserts its badge `class="badge <kind-class>"` and badge text both match
  a `BADGE_CONFIG` constant duplicated in the test file (deliberately not
  imported from `app.py`, so a real regression in the shipped config can't
  trivially "match itself"), and that neither the *other* kind's badge class
  nor text appear in that row's HTML. **Verified load-bearing**: reintroduced
  the reviewer's exact sabotage (`SINGLETON_TOGGLE_CONFIG['taiga']`
  hardcoded in `row()`) — the new `[gitea]` badge test failed (`expected
  badge class "gitea-resources", got: ...taiga-ram...⚠ ~3–5 GB RAM...`)
  while the `[taiga]` badge test and all other 13 tests still passed
  (matching the reviewer's own description of this exact failure signature)
  — then reverted the sabotage and confirmed the full 15/15 JS suite passes
  clean again, with no `SABOTAGE` markers left in `app/app.py`
  (`grep -n SABOTAGE app/app.py` → no matches).

Re-ran the full suite after both fixes: `python3 -m unittest discover -s
tests` → 135/135 (unchanged — Defect 2's fix is JS-only, Defect 1's fix is
a printed string only, neither touches Python code); `node
tests/test_singleton_toggle_frontend.js` → **15/15** (13 pre-existing + 2
new badge tests, one per kind); `bash -n install.sh` → clean.

## Key decisions / tradeoffs

- **Postgres over SQLite** (spec's Open Question 1, left for confirmation,
  not re-decided here) — implemented exactly as the spec's default: matches
  Gitea's own documented example, and defers a DB migration 2c would
  otherwise force. Flagged again here for whoever picks up 2b/2c.
- **Image tags pinned to what was actually live at implementation time**
  (`docker.gitea.com/gitea:1.27.1`, `docker.io/library/postgres:14`) —
  fetched live from `docs.gitea.com/installation/install-with-docker` during
  this session (see "Verification performed"), not guessed or carried over
  from stale training knowledge. `docker.gitea.com` (Gitea's own registry,
  not Docker Hub's `gitea/gitea`) is what the current official docs use.
- **`GITEA__server__SSH_PORT` intentionally left at Gitea's own default**,
  not set to `$GITEA_SSH_PORT` — SSH exposure/clone-URL correctness is
  explicitly out of scope for 2a (Non-goals: "no way to create a Gitea repo
  yet regardless"), and getting `SSH_PORT` (displayed clone port) vs.
  `SSH_LISTEN_PORT` (what the container's own SSH server actually binds to)
  wrong risks a container-internal misconfiguration for zero benefit right
  now. Flagged explicitly for 2b, which does need to get this right.
- **`row()`'s badge lookup is `SINGLETON_TOGGLE_CONFIG[kind]`, keyed by the
  same `kind` string already threaded through every other function** —
  avoids a second per-kind config table or a boolean-plus-hardcoded-class
  pair (`showTaigaBadge` before this cycle). This is the shape the spec's
  own pseudocode suggested (`kind in singletonToggleState`), applied
  consistently to the badge lookup too.
- **Test file replacement, not addition** — `test_taiga_frontend.js` was
  deleted rather than kept alongside the new parametrized suite (see
  "Changes by file" above) since its six tests are a strict subset of the
  new file's `[taiga]`-prefixed cases; keeping both would mean two
  copies of the same race-condition assertions to keep in sync.

## Deviations from spec

- **The Gitea CSS badge comment references 1a's own already-corrected
  contrast, not the flawed math the current `docs/design.md` restates.**
  This was explicitly pre-corrected by the orchestrator's own instructions
  before I started (design.md's ~2.1:1 figure is stale copy from before 1a's
  own contrast fix landed) — I reused `.badge.taiga-ram`'s exact, already-
  shipped `#66d9ff` on `#16324a` pairing (~8.14:1, well above WCAG AA) for
  `.badge.gitea-resources` verbatim, and updated the CSS comment to say so
  correctly rather than parroting design.md's inaccurate number. No new
  contrast work was performed, per the explicit instruction.
- **`tests/test_taiga_frontend.js` was deleted rather than left in place.**
  The spec's "Risk / rollback notes" says to "re-run (or adapt)" the
  reviewer's technique — I read "adapt" as license to supersede the file
  with a parametrized version rather than maintain two copies of the same
  six tests going forward. If the reviewer would rather see the original
  file preserved untouched alongside a new Gitea-only file, that's a very
  small, mechanical change to reverse (git history has the original
  verbatim at `ed84d73`).
- **Everything else in `docs/spec.md`'s "Proposed approach" was followed
  literally** — exact env var names/service names verified live against
  `docs.gitea.com` rather than assumed frozen from the spec (per the spec's
  own Open Question 4), same idiom precedent 1a used for `taiga-docker`.

## Known limitations

- Same as 1a's own documented limitation: `GITEA_PORT=3000`/
  `GITEA_SSH_PORT=2222` are fixed, non-interactive defaults — no free-port
  scan (spec's Open Question 5, explicitly accepted, not a gap to close
  here).
- Admin-account creation is a manual, one-time step (`docker exec -it
  --user git ai-dev-switchboard-gitea gitea admin user create --admin ...`),
  printed at the end of `install.sh`'s output — not automated, per the spec's
  Non-goals and Open Question 3. (`--user git` added post-review — see
  "Fixes from review" below; without it the command fails.)
- No uninstall/`--without-git-hosting` path — matches every other `--with-*`
  flag in this installer, including Taiga's.
- Gitea is inert infrastructure only in this cycle: it cannot create or host
  a real repo yet (`create_project()` still calls the unchanged
  `NEW_PROJECT_SCRIPT`). That's explicitly 2b's job.

## What could and couldn't be verified end-to-end

Same documented gap 1a and 1b both hit in this environment: `docker` is
present (`docker --version` succeeds) but the Compose plugin is **not**
(`docker compose version` fails with `'compose' is not a docker command`),
and there's no `pip`/`pyyaml` available either. Concretely, in this session:

**Verified:**
- `install.sh` parses/runs cleanly (`bash -n install.sh`) with the new
  `ensure_docker()` helper and the Gitea block inside the
  `WITH_GIT_HOSTING` block, including the idempotency logic (`get_env`
  returning non-empty skips secret regeneration; `.env`/compose-file/
  sudoers writes are all deterministic re-writes via the existing
  `set_env`/`install`/heredoc idioms already used elsewhere in this file).
- `config/gitea-docker-compose.yml`'s exact shape (service names `server`/
  `db`, `env_file:` usage, image tags, env var names) verified live against
  `docs.gitea.com/installation/install-with-docker` at implementation time
  (fetched via `curl`, not assumed) — not validated with a real
  `docker compose config`/YAML parser in this environment (no `pyyaml`, no
  Compose plugin), so a YAML indentation typo, while checked carefully by
  eye, isn't machine-verified here.
- All three wrapper scripts pass `bash -n`; their fallback behavior (no
  `$GITEA_DIR` yet → `gitea-status.sh` prints `off`) is exercised indirectly
  via `tests/test_gitea.py`'s monkeypatched `gitea_run` (the wrapper
  scripts' own bodies aren't invoked by those tests — only their contract,
  same limitation 1a's own `test_taiga.py` documented).
- `app/app.py`'s backend logic: all 13 new tests in `tests/test_gitea.py`
  pass (`gitea_run()`'s sudo/timeout shape, `_gitea_display_url()`'s two
  modes, the full `/status` + `/gitea/{on,off}` HTTP round-trip against a
  real `ThreadingHTTPServer`, the TOTP gate, the disabled-404 case) — Docker
  itself is never invoked; `gitea_run` is monkeypatched to a stateful fake,
  same technique `test_taiga.py` already uses.
- The full existing suite (135 Python tests across `test_taiga.py`,
  `test_gitea.py`, `test_upload.py`, `test_new_project_from_upload.py`) —
  all green, no regressions from the Docker-helper refactor or the frontend
  generalization.
- **The frontend state-machine generalization specifically** — the
  highest-risk item per the spec's own "Risk / rollback notes":
  `tests/test_singleton_toggle_frontend.js` re-runs the exact same six
  race-condition scenarios (docs/test-review.md Defects 1 and 2) against
  **both** `taiga` and `gitea` kinds (13 tests total, all pass) against the
  real rendered `<script>` extracted from `render_page()`. I additionally
  ran a sabotage check during this session (temporarily made the
  `offPendingCount` release logic gitea-blind, confirmed the `[gitea]`
  network-failure test — and only that one — failed, then reverted) to
  confirm the parametrized suite actually catches a kind-specific
  regression rather than just re-confirming Taiga still works.
- JS syntax of the real rendered `<script>` (`node --check`) — no syntax
  errors from the refactor.

**Could not be verified (same documented gap as 1a/1b):**
- An actual `docker compose pull`/`up -d`/`down`/`ps` cycle against the real
  authored compose file — not run in this environment (no Compose plugin).
  This means the compose file's `env_file:`/`ports:`/`depends_on:` wiring,
  the `GITEA__database__*` values actually reaching Postgres/Gitea correctly,
  and Gitea actually reaching a working login page after `INSTALL_LOCK=true`
  are all verified only by close reading against Gitea's own live docs, not
  by an actual running stack.
- The three wrapper scripts' real `docker compose` invocations (only their
  contract — stdout shape, `sudo`-argument list — is exercised via the
  monkeypatched `gitea_run` in `tests/test_gitea.py`).
- The full install.sh run end-to-end on a real box (creating `$GITEA_DIR`,
  writing/overwriting real files under `/etc`, `/opt`, `/usr/local/bin`,
  generating a real sudoers file and validating it with `visudo`) — not run
  in this sandboxed environment; only static analysis (`bash -n`) and close
  reading against the existing, already-working `--with-taiga` block it
  parallels.

## How to verify locally

```bash
# Backend (fast, no Docker needed):
python3 -m unittest discover -s tests -v

# Frontend regression suite (both taiga and gitea kinds, 13 tests):
node tests/test_singleton_toggle_frontend.js

# install.sh syntax:
bash -n install.sh

# On a real box with Docker + the Compose plugin available, to verify the
# actual stack (not run in this session — see limitations above):
sudo ./install.sh --with-git-hosting
#   then flip the "Gitea" row's toggle in the web UI, or directly:
sudo /usr/local/bin/ai-dev-switchboard-gitea-up.sh
sudo /usr/local/bin/ai-dev-switchboard-gitea-status.sh   # expect "on" once healthy
curl -I http://127.0.0.1:3000                             # expect Gitea's login page
sudo /usr/local/bin/ai-dev-switchboard-gitea-down.sh
sudo /usr/local/bin/ai-dev-switchboard-gitea-status.sh   # expect "off"
```
