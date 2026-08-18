# Implementation: Land backlog items 47 and 48 in code (Taiga subpath rendering, Gitea stale ROOT_URL)

## Summary

Ported two already-root-caused, already-live-fixed bugs (`docs/BACKLOG.md`
items 47 and 48) into `install.sh` itself, so a fresh/re-run install
reproduces the fixes that were previously only applied by hand on CT110.

**Part A (item 48):** `install.sh`'s Gitea block now captures
`GITEA__server__ROOT_URL`/`GITEA__server__DOMAIN`'s *previous* persisted
values via `get_env` before overwriting them, and force-recreates the
`server` container (the actual service name in
`config/gitea-docker-compose.yml:26` — not `gitea`) when either value
actually changed and the container already exists. Gitea's official Docker
image only applies those env vars to the persisted `app.ini` on a
container's first-ever start, so without this, a later `install.sh` re-run
that changes `PUBLISH_MODE`/`BASE_URL` silently leaves the running container
serving stale links/form-actions indefinitely.

**Part B (item 47b):** `install.sh`'s Taiga block now writes `SUBPATH` and
`WEBSOCKETS_SCHEME` into `$TAIGA_ENV` (`$TAIGA_DIR/.env`), alongside the
existing `TAIGA_SCHEME`/`TAIGA_DOMAIN` values. Confirmed against the real
cloned `taiga-docker` checkout at `/opt/ai-dev-switchboard-taiga` (this
sandbox has a live one) that taiga-front reads these — and `TAIGA_SCHEME`/
`TAIGA_DOMAIN` — from the *same* root `.env` Compose already auto-loads for
taiga-back/taiga-gateway; there is no separate `taiga-front/.env` file, so
`TAIGA_SCHEME`/`TAIGA_DOMAIN` were already reaching taiga-front for free —
only `SUBPATH`/`WEBSOCKETS_SCHEME` were missing entirely (confirmed by grep:
no reference to either anywhere in `install.sh` before this change).

**Post-review fix-up round (`docs/test-review.md`, Blocked verdict, 2
defects):** the reviewer's testing pass live-tested this cycle's own claims
instead of taking them on faith, and found both were wrong in a real
Compose environment:

- **Defect 1 (must-fix, blocking):** this cycle's original claim that item
  47(a) (`location /`'s variable `proxy_pass`) was "already fixed" was
  disproven live — `location /` collapsed *every* request path to a bare
  `/`, so `taiga-front`'s `index.html` was served for every asset/API/
  `conf.json` request instead of the real file, confirmed with both direct
  `curl` against the real running gateway container and a real headless
  Playwright pass. **Fixed this round** by appending `$request_uri` to the
  `proxy_pass` target (see "Changes by file" and "Fix-up round
  verification" below) — the previous "syntax looks like items 42/43's
  fix, so it must already be fixed" reasoning was a read-the-config-not-
  the-runtime-behavior mistake; item 47(a) needed a real code change after
  all, not a no-op.
- **Defect 2 (should-fix):** the Gitea `--force-recreate server` command
  also recreated the `db` container as a real Compose side effect (both
  services share `env_file: - .env`, and `server`'s `depends_on: - db`
  pulls `db` into Compose's scope even when only `server` is named),
  contradicting the spec's "scoped to `server` only, no reason to touch
  `db`" intent — the original `tests/test_install_gitea_recreate.py`
  assertion (`assertNotIn(" db", ...)`) was true of the mocked command
  *string* but not of real Compose's actual container lifecycle. **Fixed
  this round** by adding `--no-deps` to the recreate command, verified live
  against a real, isolated Gitea+Postgres compose stack.

Both fixes were developed and verified this round against real Docker (see
"Fix-up round verification"), not re-asserted from reading config syntax —
the exact gap the reviewer's report identified in the original pass.

## Changes by file

- `install.sh`
  - Gitea block (`--with-git-hosting`): added `GITEA_DOMAIN_PREV`/
    `GITEA_ROOT_URL_PREV` captured via `get_env` immediately before the
    existing `set_env "$GITEA_ENV" GITEA__server__DOMAIN/ROOT_URL` calls,
    then a new "3b." step: if either new value differs from its captured
    previous value, checks `docker compose ps -q server` (run from
    `$GITEA_DIR`) for a non-empty result and, if so, runs
    `docker compose up -d --force-recreate --no-deps server` — scoped to
    `server` only, never a bare `--force-recreate`, and `--no-deps` added
    in this fix-up round so `db` is genuinely never touched, not just never
    *named* on the command line (see Defect 2 above). Warns to stderr and
    continues (does not fail the install) if the recreate command itself
    fails, same idiom the pre-pull step a few lines below already uses for
    network failures. Skips cleanly, no warning, when the container doesn't
    exist yet (fresh install) or the values didn't change (ordinary
    re-run).
  - Taiga block (`--with-taiga`): new "4b." step right after the existing
    `TAIGA_DOMAIN` `set_env` call. Defines `TAIGA_URL_PATH="/taiga"`
    (mirrors `app.py`'s own `TAIGA_URL_PATH` constant, same shape as
    Gitea's `GITEA_URL_PATH` in the Gitea block) and, under the exact same
    `PUBLISH_MODE=tailscale && -n BASE_URL` conditional `TAIGA_DOMAIN`
    already uses, sets `SUBPATH="/taiga"` + `WEBSOCKETS_SCHEME="wss"` when
    published via tailscale, else `SUBPATH=""` + `WEBSOCKETS_SCHEME="ws"`
    (matching `app.py`'s own `_taiga_display_url()`, which only prefixes
    `/taiga` under `PUBLISH_MODE=tailscale` — plain `PUBLISH_MODE=none`
    access is direct, `http://127.0.0.1:$TAIGA_PORT`, no subpath). Both
    written via the same `set_env` idiom, re-derived every run (not
    "only-if-empty"), exactly like `TAIGA_DOMAIN`.
  - `docker-compose.override.taiga-gateway.conf` heredoc (item 47(a),
    **fix-up round**): `location /`'s `proxy_pass http://$upstream_front/;`
    changed to `proxy_pass http://$upstream_front$request_uri;`. Root
    cause: unlike `/api/`, `/admin/`, `/media/` below (whose `proxy_pass`
    target has a real trailing path segment after the variable, e.g.
    `.../api/`), `location /`'s target ended in a bare `/` with nothing
    else — for that specific bare-URI + variable-upstream combination,
    nginx does not append the client's actual request path to the
    forwarded request. `$request_uri` (nginx's own raw, unmodified request
    URI, including any query string) is appended explicitly instead,
    forwarding the exact path the client requested. `/api/`, `/admin/`,
    `/media/` left untouched — confirmed already working correctly, both by
    the reviewer and independently re-confirmed this round.
  - No changes to `config/gitea-docker-compose.yml` or `app/app.py`.

- `tests/test_install_gitea_recreate.py`
  - Existing `GiteaForceRecreateTests` (mocked-`docker`, 6 tests): the one
    assertion that named the exact recreate command string now expects
    `up -d --force-recreate --no-deps server` (was `... --force-recreate
    server`). Otherwise unchanged.
  - **New (fix-up round): `GiteaForceRecreateRealDockerTests`** (1 test,
    `@unittest.skipUnless(shutil.which("docker"), ...)`, same
    tool-availability-gated idiom `tests/test_gitea_sync_project.py`
    already uses for `HAVE_GIT`). Builds a real, isolated Gitea+Postgres
    compose stack from the actual `config/gitea-docker-compose.yml`
    (`container_name` overridden to unique per-process values so it never
    collides with any real running install), changes
    `ROOT_URL`/`DOMAIN` via the real `set_env`, runs the real extracted
    "3b" block against real `docker compose`, and asserts `server`'s
    `Created` timestamp changes (recreated) while `db`'s `Created`
    timestamp is byte-identical before/after (genuinely untouched) — plus
    that the new `ROOT_URL`/`DOMAIN` actually land in the recreated
    container's `app.ini`. This is the layer that actually catches Defect
    2's class of bug: the pre-existing mocked-`docker` test only proves the
    command *string* is correct, which is exactly how the original `db`
    side effect went unnoticed — real Compose's dependency-scope-inclusion
    + shared-`env_file`-config-hash-diffing behavior can only be observed
    against real Compose.

- `tests/test_install_taiga_gateway_root_location.py` (new, fix-up round)
  - `ProxyPassLineTests` (2 tests, no Docker needed): static, always-run
    guard on the real heredoc content extracted verbatim from `install.sh`
    — asserts `location /` forwards `$request_uri` (not a bare `/`), and
    that `/api/`/`/admin/`/`/media/` remain untouched. Cheap regression
    guard against reverting/misapplying the fix.
  - `RootLocationRuntimeTests` (6 tests, `@unittest.skipUnless(shutil.which
    ("docker"), ...)`): the real repro/verification technique — spins up a
    real `nginx:1.19-alpine` gateway (the extracted, real conf) fronting a
    real fake-backend container standing in for `taiga-front` (distinct
    static files at `/`, `/conf.json`, `/js/app-loader.js`) on an isolated
    scratch Docker network, and asserts each path returns its own distinct
    real content (not identical bytes for everything — the exact shape of
    the original defect), a nonexistent path 404s distinctly, and a query
    string is preserved. A static string check alone cannot catch this
    class of bug — that's exactly how the original, incorrect "already
    fixed" claim slipped through in the first place.

- `tests/test_install_taiga_front_subpath.py` — unchanged this round (Part
  B/b was independently confirmed correct by the reviewer, no defect
  found).

## Key decisions / tradeoffs

- **`SUBPATH`/`WEBSOCKETS_SCHEME` written to the same root `$TAIGA_ENV`
  file, not a separate `taiga-front/.env`.** The spec flagged this as an
  open question to resolve by reading the real checkout rather than
  guessing. Confirmed directly against `/opt/ai-dev-switchboard-taiga/
  docker-compose.yml` (a real cloned `taiga-docker` `stable`-branch
  checkout present in this sandbox): `taiga-front`'s `environment:` block
  reads `TAIGA_URL: "${TAIGA_SCHEME}://${TAIGA_DOMAIN}"`,
  `TAIGA_WEBSOCKETS_URL: "${WEBSOCKETS_SCHEME}://${TAIGA_DOMAIN}"`,
  `TAIGA_SUBPATH: "${SUBPATH}"` — all Compose-substituted from the project
  directory's own root `.env`, the exact same file `TAIGA_SCHEME`/
  `TAIGA_DOMAIN` already write to. Independently re-confirmed correct by
  the reviewer's own live `docker exec ... cat conf.json` check.
- **`SUBPATH`/`WEBSOCKETS_SCHEME` follow the same `PUBLISH_MODE`/`BASE_URL`
  conditional as `TAIGA_DOMAIN`, not an unconditional constant.** Matches
  `app.py`'s own `_taiga_display_url()` (`app/app.py:2981-2983`), which
  only prefixes `/taiga` when `PUBLISH_MODE == "tailscale"`. Independently
  re-confirmed correct by the reviewer (test case 3 in
  `docs/test-review.md`) by reading `app.py` directly.
- **`TAIGA_SCHEME` left untouched (still unconditionally `"http"`).**
  Already written every run into the exact same `$TAIGA_ENV` file
  `taiga-front` reads, so it already reached `taiga-front` with zero
  additional wiring needed — not reworking its own value logic was out of
  scope for this cycle's "wiring" task.
- **Gitea force-recreate keyed off comparing old vs. new values, not off
  `PUBLISH_MODE`/`BASE_URL` changing.** The *values* are what actually
  matter to `app.ini`, not the inputs that produced them (e.g. Gitea
  toggled on for the first time between runs with unchanged
  `PUBLISH_MODE`/`BASE_URL` would still need `GITEA_PORT` reflected).
  Comparing `GITEA_DOMAIN_VALUE`/`GITEA_ROOT_URL_VALUE` against their
  `get_env`-captured previous values directly is both simpler and strictly
  more accurate.
- **`$request_uri` over other candidate fixes for item 47(a).** Considered
  and rejected: removing the `location /` variable indirection entirely
  (reverting to a bare hostname `proxy_pass http://taiga-front/;`) — this
  would reintroduce the item-30 Docker-Compose-startup-DNS race the
  `resolver`+variable pattern exists to fix in the first place (nginx
  resolves a non-variable `proxy_pass` target once, at config-load time).
  `$request_uri` keeps the variable/`resolver` mechanism intact while
  fixing the path-forwarding bug, and is nginx's own standard idiom for
  "forward the exact original request" when the location's own prefix
  match doesn't need stripping (verified live — see "Fix-up round
  verification").
- **`--no-deps` over the reviewer's other suggested option (splitting
  `db` onto its own `.env` slice).** Both were offered as valid fixes in
  `docs/test-review.md`. `--no-deps` is the smaller, more targeted change
  — one flag on the existing command, versus restructuring
  `config/gitea-docker-compose.yml`'s `env_file:` wiring and introducing a
  second env file with its own conventions. Verified live to fully resolve
  the defect (see "Fix-up round verification") — `db`'s `Created`
  timestamp is provably unchanged, with no observed downside.
- **Test technique: verbatim block/config extraction, mocked `docker` for
  cheap logic tests, real `docker`/Compose (gated on availability) for the
  two runtime behaviors that a mock cannot exercise.** This project's
  established convention (`tests/test_install_set_env.py`,
  `tests/test_install_taiga_gateway_port.py`) uses mocked `docker` for
  cheap, deterministic `install.sh`-logic tests. This fix-up round adds
  real-Docker tests specifically because *both* defects were, in
  substance, real-runtime-behavior bugs a mock cannot see: nginx's
  variable-`proxy_pass`-plus-bare-URI quirk, and real Compose's
  dependency-scope-inclusion + shared-`env_file` config-hash diffing.
  Gated on `shutil.which("docker")` via the same `unittest.skipUnless`
  idiom `tests/test_gitea_sync_project.py` already establishes for
  `HAVE_GIT`, so the suite degrades gracefully (skip, not fail) in an
  environment without Docker.

## Deviations from spec

- None from the spec's required behavior in the original pass; see "Key
  decisions" above for two points where the spec's own prose was ambiguous
  and resolved by reading the actual code it described.
- Item 47(a): the spec said "verify, don't blind-patch" — the original pass
  verified (by reading config syntax) and incorrectly concluded no patch
  was needed. This round's live-testing (matching the reviewer's own
  methodology) showed a patch genuinely was needed; landed one, verified
  the same way. This is not a deviation from the spec's instruction so much
  as a correction of the original pass's verification method — the spec's
  actual instruction ("verify live before concluding it's already fixed")
  is what this round now actually follows.

## Known limitations

- **`shellcheck` not runnable in this sandbox** — no `shellcheck` binary
  installed, and none available via any already-installed package manager
  cache checked (`apt list --installed`, `pip3 list`). Fell back to
  `bash -n install.sh` (clean) plus close manual review matching the
  file's existing quoting/`local`/`set_env`/`get_env` conventions
  line-for-line. If CI or the reviewer's environment has `shellcheck`
  available, worth a follow-up run there.
- No change to `TAIGA_SCHEME`'s own hardcoded-`"http"` behavior (see "Key
  decisions" above) — out of scope for this cycle.
- The live, currently-running `ai-dev-switchboard-taiga-taiga-gateway-1`/
  `ai-dev-switchboard-gitea` containers in this sandbox were **not**
  reconfigured with these fixes during this session — verification against
  them was read-only (`curl`, `docker exec ... cat`) or via a separate
  scratch gateway container joined to the same Docker network (never
  mutating the live containers themselves), mirroring the reviewer's own
  methodology. The live containers will pick up both fixes the next time
  `install.sh` (or the reviewer's own next testing pass) actually re-runs
  against them.

## Fix-up round verification

Both defects were reproduced against real Docker before writing a fix, and
the fix re-verified against real Docker afterward — not re-asserted from
reading code, which is what caused the original miss.

**Defect 1 (item 47(a), `location /`):**
- Reproduced live against the real, already-running
  `ai-dev-switchboard-taiga-taiga-gateway-1` container: `curl` to `/`,
  `/conf.json`, and a nonexistent path on `127.0.0.1:9000` all returned
  identical `200`/`text/html`/`size=140635` — matching `docs/test-review.md`
  exactly.
- Built an isolated scratch reproduction (own Docker network, a fake
  `taiga-front` backend serving 3 distinguishable static files, a gateway
  container running the *original* extracted heredoc conf) — confirmed the
  same collapse-to-`/` behavior in isolation, ruling out any live-deployment
  drift as the cause.
- Applied the `$request_uri` fix to the scratch gateway conf — `/`,
  `/conf.json`, `/js/app-loader.js` each returned their own real, distinct
  content; a nonexistent path returned a genuine `404`; a query string was
  preserved. Tore down the scratch containers/network.
- Applied the fix to `install.sh`'s real heredoc, then did an **additional**
  full end-to-end check against the actual live Taiga stack (not just the
  isolated scratch repro): started a new gateway container running the
  *fixed*, extracted-from-`install.sh` conf, joined to the live
  `ai-dev-switchboard-taiga_taiga` Docker network (so it could resolve the
  real `taiga-front`/`taiga-back` etc. by name) plus a small
  subpath-stripping proxy in front of it (emulating `tailscale serve
  --set-path`, same technique `docs/test-review.md` used) — then ran a real
  headless Playwright/Chromium session against it. Result: page title
  "Discover projects - Taiga" (the real app, not a blank/error shell), and
  every one of 18 captured network responses came back with its own correct
  content-type (`text/css`, `image/svg+xml`, `application/javascript`,
  `application/json`, font/image MIME types) — zero HTML-typed-when-not-
  expected responses, a night-and-day contrast to the pre-fix "everything is
  `text/html`" symptom. The only remaining console errors were
  `ERR_CONNECTION_REFUSED` on the app's own outbound API calls to
  `dev.tailbe22cd.ts.net` — expected and unrelated: that tailnet hostname
  isn't reachable from this sandbox's own network, not a defect in the
  gateway config. Never modified the live gateway container itself — only
  read from it (`curl`, no mutation) and stood up separate scratch
  containers alongside it, then tore them down.
- Revert-and-watch-it-fail: reverted just the `$request_uri` change,
  reran `tests/test_install_taiga_gateway_root_location.py` — 6 of 8 tests
  failed as expected (the 2 static-check tests and the `/`-still-works test
  correctly still passed); restored the fix, 8/8 pass again.

**Defect 2 (item 48, Gitea `--no-deps`):**
- Reproduced live: built an isolated, real Gitea+Postgres compose stack
  from the actual `config/gitea-docker-compose.yml` (unique
  `container_name`s, no contact with any live Gitea container), changed
  `ROOT_URL`/`DOMAIN`, ran `docker compose up -d --force-recreate server`
  (no `--no-deps`) — confirmed `db`'s `Created` timestamp changed
  (recreated) even though only `server` was named on the command line,
  matching `docs/test-review.md`'s own finding.
- Added `--no-deps` to the same command against the same stack — `server`
  recreated (new `Created` timestamp, `app.ini` correctly showing the new
  `DOMAIN`/`ROOT_URL`) while `db`'s `Created` timestamp was byte-identical
  before/after.
- Applied the fix to `install.sh`, then wrote
  `GiteaForceRecreateRealDockerTests` (real Docker, see "Changes by file")
  to lock this in as an automated regression test rather than a one-off
  manual check.
- Revert-and-watch-it-fail: reverted just the `--no-deps` flag (both the
  functional line and the warning-message text), reran
  `tests/test_install_gitea_recreate.py` — 2 of 7 tests failed as expected
  (`GiteaForceRecreateRealDockerTests`'s real-Compose test with an
  `AssertionError` showing `db`'s timestamp actually changed, plus the
  mocked command-string assertion); the other 5 (which don't assert the
  exact flag) correctly still passed. Restored the fix, 7/7 pass again.

All scratch Docker containers/networks created during this verification
were torn down before finishing (confirmed via `docker ps -a` showing none
remain).

## How to verify locally

```bash
# Syntax check (no shellcheck available in this sandbox -- see "Known
# limitations"):
bash -n install.sh

# Regression tests (mix of pure-Python-and-mocked-docker, and real Docker
# gated on availability via unittest.skipUnless):
python3 tests/test_install_gitea_recreate.py -v
# -> Ran 7 tests ... OK (6 mocked-docker + 1 real-docker, skips the real
#    one gracefully if docker isn't on PATH)
python3 tests/test_install_taiga_front_subpath.py -v
# -> Ran 5 tests ... OK
python3 tests/test_install_taiga_gateway_root_location.py -v
# -> Ran 8 tests ... OK (2 static + 6 real-docker, same graceful skip)

# Pre-existing install.sh-related suites, confirmed still green against
# this change:
python3 tests/test_install_taiga_gateway_port.py -v   # -> Ran 6 tests ... OK
python3 tests/test_install_set_env.py -v               # -> Ran 8 tests ... OK
python3 tests/test_install_auth_mode_default.py -v     # -> Ran 2 tests ... OK
python3 tests/test_install_code_server_path.py -v      # -> Ran 4 tests ... OK
python3 tests/test_install_ollama.py -v                # -> Ran 16 tests ... OK
python3 tests/test_install_update.py -v                # -> Ran 20 tests ... OK

# tests/test_gitea.py and tests/test_taiga.py both show one
# RemoteDisconnected error each (confirmed pre-existing, unrelated to this
# cycle's diff, in the original pass).

# Manual/live check for the reviewer's next pass (needs a real
# --with-git-hosting / --with-taiga install under PUBLISH_MODE=tailscale):
#   Part A: fresh --with-git-hosting install, then re-run install.sh with
#   PUBLISH_MODE/BASE_URL changed -- confirm `server` recreates (new
#   container ID/start time) and `db` does NOT (same container ID/start
#   time as before), and that generated Gitea links/form-actions reflect
#   the new ROOT_URL without a manual restart.
#   Part B: fresh --with-taiga install under PUBLISH_MODE=tailscale ->
#   $BASE_URL/taiga renders fully styled with working asset/API/WebSocket
#   requests through the subpath, verified with a real Playwright browser
#   session (not just curl'ing `/`) -- this sandbox already has a live
#   taiga-front/taiga-gateway stack running (`docker ps -a`) that could be
#   used for that pass.
```
