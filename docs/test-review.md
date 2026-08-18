# Test & Review: Land backlog items 47 and 48 in code (Taiga subpath rendering, Gitea stale ROOT_URL)

## Scope

Re-review pass following the prior **Blocked** verdict (2 defects: item
47(a) `location /` collapsing every request to `index.html`; item 48's
`--force-recreate server` also recreating `db`). This pass independently
re-verifies both fix-up-round claims against real Docker myself — not by
re-reading `docs/implementation.md`'s account of them — then, since both
came back clean, runs the full independent review pass (spec coverage,
correctness, security, simplicity) against the actual diff.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Defect 1 fix — `location /` forwards `$request_uri`, not a bare `/`; distinct content per path, real 404 for unknown paths, query string preserved | Automated — `tests/test_install_taiga_gateway_root_location.py`, real heredoc extracted verbatim, real `nginx:1.19-alpine` gateway + fake-backend container on isolated Docker network, run live this session | pass | `python3 tests/test_install_taiga_gateway_root_location.py -v` → `Ran 8 tests ... OK` (run live this session) |
| 1b | Revert-and-watch-it-fail (my own, independent of the developer's) — does the test suite genuinely exercise the `$request_uri` fix? | Automated — reverted just `proxy_pass http://$upstream_front$request_uri;` back to `proxy_pass http://$upstream_front/;` in the real `install.sh`, reran, restored | pass | 6/8 failed as expected (`test_nonexistent_path_404s_distinctly_not_index_fallback`: `200 != 404`; `test_query_string_is_preserved`: got `INDEX-CONTENT` instead of the real conf.json; `test_three_distinct_paths_return_three_distinct_bodies`: `1 != 3`, all three collapsed to one body) — 2 tests that don't exercise the URI-forwarding behavior (`test_root_returns_index`, static `ProxyPassLineTests`... note: static tests also correctly failed since they assert the literal string) correctly still passed only where expected; `diff` confirmed `install.sh` byte-identical after restore |
| 2 | Defect 1 fix — full acceptance criterion 2, real browser-level check through a subpath-stripping proxy against the *live* Taiga stack (not the isolated scratch repro) | Manual/live — built my own scratch gateway container running the real, fixed heredoc conf extracted from `install.sh`, joined to the live `ai-dev-switchboard-taiga_taiga` Docker network, fronted by my own subpath-stripping proxy (`/taiga/` → strip prefix), then a real headless Playwright/Chromium session against it, run live this session | pass | Page title `"Discover projects - Taiga"` (real app renders, not blank); 18 network responses captured, 0 HTML-masquerading-as-CSS/JS/JSON (`HTML_MASQUERADE_COUNT: 0`); direct `curl` confirmed `text/css`, `application/javascript`, `image/svg+xml`, real Django 404 (not `index.html`) for `/taiga/api/v1/`, and `conf.json` correctly showing `"baseHref": "/taiga/"`, `"eventsUrl": "wss://..."` (Part B/b's `SUBPATH`/`WEBSOCKETS_SCHEME` wiring, confirmed still correct) |
| 3 | Defect 1 fix — `/api/`, `/admin/`, `/media/` locations left untouched, still correct | Automated + manual — `ProxyPassLineTests.test_other_locations_left_untouched`; live `curl` through my own scratch proxy to `/taiga/api/v1/` in test #2 above | pass | Static assertions pass; live `/taiga/api/v1/` returns a real Django "Not Found" 404 page (`text/html`, genuine app response), not `index.html`'s content |
| 4 | Defect 2 fix — `--no-deps` genuinely keeps `db` out of Compose's recreate scope with real `docker compose` | Automated — `tests/test_install_gitea_recreate.py::GiteaForceRecreateRealDockerTests`, real isolated Gitea+Postgres stack from the actual `config/gitea-docker-compose.yml`, run live this session | pass | `python3 tests/test_install_gitea_recreate.py -v` → `Ran 7 tests ... OK`; test asserts `server`'s `Created` timestamp changes and `db`'s is byte-identical before/after, plus `app.ini` inside the recreated container shows `DOMAIN = second.example.com` / `ROOT_URL = https://second.example.com/gitea` |
| 4b | Revert-and-watch-it-fail (my own) — does the test suite genuinely catch `db` being pulled into scope without `--no-deps`? | Automated — reverted `--no-deps` out of both occurrences in the real `install.sh` (functional line + warning text), reran, restored | pass | 2/7 failed as expected: `test_force_recreate_touches_server_only_not_db` → `AssertionError: db must NOT be recreated` with two genuinely different real `Created` timestamps (`...51.607...Z` vs `...52.417...Z`); `test_changed_domain_and_root_url_force_recreates_server_only` → command string mismatch; other 5 tests (which don't assert the flag) correctly still passed; `diff` confirmed `install.sh` byte-identical after restore |
| 5 | AC1 — Gitea force-recreate unit logic (fresh install, unchanged values, changed values, no container yet, DOMAIN-only change, failed recreate warns) | Automated — `GiteaForceRecreateTests` (mocked `docker`), run live | pass | `Ran 7 tests ... OK` includes both mocked (6) and real-Docker (1) classes |
| 6 | AC2 — `taiga-front` `SUBPATH`/`WEBSOCKETS_SCHEME` unit logic (unchanged this round, previously confirmed) | Automated — `tests/test_install_taiga_front_subpath.py`, run live | pass | `Ran 5 tests ... OK` |
| 7 | AC3 — full pre-existing `install.sh`-related suite stays green | Automated, run live this session | pass | `test_install_taiga_gateway_port.py` (6), `test_install_set_env.py` (8), `test_install_auth_mode_default.py` (2), `test_install_code_server_path.py` (4), `test_install_ollama.py` (16), `test_install_update.py` (20) — all `OK` |
| 8 | AC3 — no new regression in `test_gitea.py`/`test_taiga.py` | Automated, run live this session | pass (pre-existing, unrelated) | Both show the same one `RemoteDisconnected` error each as the prior review round (already confirmed pre-existing via `git stash` comparison last round; error class/count unchanged) |
| 9 | `bash -n install.sh` | Automated | pass | Clean |
| 10 | Nginx correctness of `$request_uri` fix against precedence/other locations/query strings (explicit re-ask from the dispatch prompt) | Manual — read full heredoc `install.sh:593-668`; confirmed `/api/`, `/admin/`, `/static/`, `/_protected/`, `/media/exports/`, `/media/`, `/events` are all longer/more-specific prefix matches that take precedence over the catch-all `location /` regardless of what `location /`'s `proxy_pass` target is (`$request_uri` change doesn't touch location-matching, only what's forwarded once matched); query string preserved (test #1, `test_query_string_is_preserved`) | pass | No location-precedence change, no double-slash, no query-string loss |

## Regression check

Full `install.sh`-related suite: all green, all run live this session (see
#5–9). `test_gitea.py`/`test_taiga.py`: same single pre-existing
`RemoteDisconnected` error each as last round — not a new regression from
this fix-up round's diff.

All Docker scratch containers/networks created during this session's
independent verification were torn down and confirmed gone (`docker ps -a`
/ `docker network ls`, filtered for my own container/network name prefixes,
both empty) before writing this report.

## Spec coverage

| AC | Covered by | Status |
|---|---|---|
| AC1 (Gitea `server` force-recreate, `db` untouched, new values land) | Test #4, #4b, #5 | met — independently reverified live |
| AC2 (Taiga renders fully styled under subpath, working asset/API/WebSocket, real browser check) | Test #1, #1b, #2, #3 | met — independently reverified live, including my own from-scratch browser pass against the real Taiga stack |
| AC3 (existing suite green, no regression in `PUBLISH_MODE=none`) | Test #5 (`test_unchanged_values_never_calls_recreate_even_if_container_exists`, `test_fresh_install_no_prior_values_no_container_skips_cleanly`), #6, #7, #8 | met |

## Review pass

Read the full diff (`git diff -- install.sh`, both new test files in full)
directly, not just the prior round's report.

**Correctness:**
- `proxy_pass http://$upstream_front$request_uri;` is standard, correct
  nginx idiom for "forward the exact original request" when a location's
  prefix doesn't need stripping — `$request_uri` is nginx's raw,
  unmodified URI including the query string, unaffected by any internal
  rewrite. Confirmed live it does not change location-matching precedence
  (that's resolved before `proxy_pass` ever runs) and does not disturb
  `/api/`, `/admin/`, `/media/`, `/events`, `/static/`, `/_protected/` —
  all of those are separate, more-specific `location` blocks that already
  win over the catch-all `location /` regardless of its `proxy_pass`
  target.
- `--no-deps` on the Gitea recreate command is correctly scoped: read
  `config/gitea-docker-compose.yml` directly — the stack has exactly two
  services (`server`, `db`), both loading the same `env_file: - .env`,
  `server` has `depends_on: - db`. `--no-deps` excludes `db` from Compose's
  dependency-resolution scope entirely, which is exactly right here since
  `db` never needs the new `GITEA__server__ROOT_URL`/`DOMAIN` values (it
  only reads `POSTGRES_*`).
- The `GITEA_DOMAIN_PREV`/`GITEA_ROOT_URL_PREV` `get_env`-before-`set_env`
  capture is correctly ordered (captured immediately before the
  overwriting `set_env` calls) and the changed-value comparison is
  correctly value-based rather than input-based (`PUBLISH_MODE`/
  `BASE_URL` changing isn't the trigger — the *computed* value changing
  is), matching the spec's own reasoning.

**One should-fix (non-blocking) noted, not previously flagged:** the
`docker compose ps -q server` "does the container currently exist" check
(`install.sh`, unchanged by this fix-up round, part of the original Part A
landing) only matches a **running** container — `docker compose ps -q`
without `-a` excludes stopped-but-not-removed containers (confirmed live
this session against a real Compose stack: a `stop`ped service returns
empty from `ps -q`, non-empty from `ps -a -q`). In this codebase's normal
operational flow this is a non-issue: `scripts/gitea-down.sh` runs `docker
compose down` (full removal, not `stop`), so the only two real states are
"running" (`ps -q` non-empty, correctly triggers recreate-if-changed) or
"removed" (a later `scripts/gitea-up.sh`'s plain `docker compose up -d`
creates a genuinely new container, which is a first-ever start for that
container instance and picks up the current `.env` values on its own,
without needing this block at all). The gap only exists if Gitea's
container were stopped-but-not-removed via `docker stop`/`docker compose
stop` directly, bypassing this project's own toggle scripts — an
out-of-band operational path, not something either the spec or this
round's tests exercise. Worth a one-line comment or a follow-up switching
the check to `ps -a -q` for robustness against that out-of-band case, but
it does not fail any stated acceptance criterion and is not a regression
introduced by this fix-up round (the check itself predates it).

**Security:** no new external input handling; `GITEA_DOMAIN_VALUE`/
`GITEA_ROOT_URL_VALUE` are derived from `BASE_URL`, already-trusted
operator-set config, same as before this cycle. No new shell-injection
surface — the new `docker compose` invocations use fixed argument lists,
no string-interpolated external data. No secrets touched or logged.

**Simplicity:** both fixes are minimal, single-purpose diffs — one flag
(`--no-deps`) and one nginx directive fragment (`$request_uri`) — with no
speculative generality, no unrelated refactor, no new abstraction. Test
additions follow the codebase's own established `HAVE_DOCKER`-gated,
verbatim-extraction convention (matches `tests/test_gitea_sync_project.py`'s
`HAVE_GIT` idiom) rather than introducing a new test pattern.

**Spec/deviation honesty:** `docs/implementation.md`'s "Deviations from
spec" section accurately frames the fix-up round as a correction of the
original pass's verification method (read-the-syntax vs. live-test),
matching what I independently reproduced this session (revert-and-fail on
both fixes, live Playwright pass against the real stack).

## Overall verdict

**Approve, with one non-blocking follow-up.**

Both previously-blocking defects are fixed and independently reverified
live this session — not re-asserted from the developer's report:
- Defect 1 (item 47(a)): reverting `$request_uri` reproduces the
  collapse-to-`index.html` failure (6/8 tests fail); with the fix, a real
  headless Playwright pass against the live Taiga stack (through a
  from-scratch subpath-stripping proxy I built myself, not reused from the
  developer's own scratch setup) renders the real app with zero
  HTML-masquerading-as-asset responses across 18 captured requests.
- Defect 2 (item 48): reverting `--no-deps` reproduces `db` being
  recreated (real, distinct `Created` timestamps) on a real isolated
  Gitea+Postgres stack; with the fix, `db`'s timestamp is provably
  unchanged while `server`'s `app.ini` correctly reflects the new
  `DOMAIN`/`ROOT_URL`.

All three acceptance criteria are met and covered by tests I ran myself
this session. Full pre-existing suite stays green; the one
`RemoteDisconnected` failure each in `test_gitea.py`/`test_taiga.py` is
the same pre-existing, unrelated issue confirmed last round.

**Follow-up (should-fix, does not block this cycle):** switch the Gitea
force-recreate block's existence check from `docker compose ps -q server`
to `docker compose ps -a -q server` (or equivalent), so a container that's
stopped-but-not-removed via an out-of-band `docker stop`/`compose stop`
(bypassing this project's own `gitea-down.sh`/`gitea-up.sh` toggle
scripts, which use full `down`/`up -d`) doesn't silently skip the
value-changed recreate. Narrow edge case, not exercised by the spec's
acceptance criteria or this round's tests, and not a regression introduced
by this fix-up round.

Control returns to product-manager for the next iteration.
