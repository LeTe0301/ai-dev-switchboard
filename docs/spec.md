# Spec: Land backlog items 47 and 48 in code (Taiga subpath rendering, Gitea stale ROOT_URL)

**Workflow: bugfix (two independent, already root-caused bugs bundled into
one cycle since both touch `install.sh`'s optional-service provisioning and
neither needs a design pass — no new UI, no new page).** Written directly by
the orchestrator rather than dispatched to `product-manager`, per the global
workflow's "right-sizing dispatches" rule 1: both root causes were already
found and fixed *live* on a real CT110 deployment (see `docs/BACKLOG.md`
items 47 and 48) — the only remaining work is landing the equivalent fix in
this repo's own `install.sh`, a mechanical port rather than a fresh
triage/scoping call.

**User-reported context (2026-08-18):** "gitea doesnt start signed in" and
"taiga which doesnt even [load] the webui" — both match items 48 and 47
below closely enough to be the same underlying bugs, not new ones. Confirm
that during the reviewer's live test pass.

---

## Part A — Item 48: Gitea under a subpath keeps stale links after `ROOT_URL`/`DOMAIN` change

**Root cause (already confirmed live on CT110, see `docs/BACKLOG.md` item
48):** Gitea's official Docker image only applies `GITEA__server__ROOT_URL`
/ `GITEA__server__DOMAIN` env vars to the persisted `app.ini` on a
container's *first ever* start. `install.sh` (`install.sh:864-872`)
correctly recomputes and re-writes these into `$GITEA_DIR/.env` on every
run via `set_env`, but nothing forces Gitea's `server` container (the
service name in `config/gitea-docker-compose.yml:26` — **not** `gitea`) to
actually pick up a changed value once `app.ini` already exists in the
persisted `gitea` volume. A plain `docker compose up -d` (what
`scripts/gitea-up.sh` runs on every toggle-on) is a no-op against an
already-running/created container — it does not re-apply changed env vars.
Confirmed fix on CT110: `docker compose up -d --force-recreate server`.

**Trigger scenario:** any install where Gitea is toggled on before (or
without) `PUBLISH_MODE=tailscale`+`BASE_URL` being configured, and that
changes on a later `install.sh` re-run — `ROOT_URL`/`DOMAIN` get written to
`.env` correctly but the running container keeps serving stale values,
generating every link/form-action without the correct subpath prefix. This
plausibly explains "gitea doesn't start signed in": if login POSTs back to
a `ROOT_URL` that doesn't match what the browser is actually on (e.g. still
pointing at `http://127.0.0.1:3000` instead of the real
`https://<tailnet-host>/gitea`), the session cookie / redirect can fail to
land the browser back in an authenticated state even though the credential
check itself succeeded.

**Fix to implement**, in `install.sh`'s Gitea block (around
`install.sh:860-872`):

1. Before calling `set_env "$GITEA_ENV" GITEA__server__ROOT_URL ...` /
   `GITEA__server__DOMAIN ...`, capture the **previous** persisted values
   via `get_env "$GITEA_ENV" GITEA__server__ROOT_URL` /
   `GITEA__server__DOMAIN` (same idiom `GITEA_SECRET_KEY`/
   `GITEA_INTERNAL_TOKEN` already use a few lines above for their own
   "only set if empty" checks).
2. After computing the new `GITEA_ROOT_URL_VALUE`/`GITEA_DOMAIN_VALUE` and
   writing them, compare old vs. new. If either changed **and** the
   `server` container currently exists (e.g. `docker compose ps -q server`
   returns non-empty, guarded the same "warn, don't fail the install" way
   the pre-pull step at `install.sh` already does for network failures),
   force-recreate it: `(cd "$GITEA_DIR" && docker compose up -d
   --force-recreate server)`. Skip cleanly (no error) when the container
   doesn't exist yet — nothing to recreate on a fresh install, first start
   will already pick up the correct values.
3. Keep this scoped to the `server` service only (`--force-recreate
   server`, not a bare `--force-recreate` for the whole stack) — no reason
   to touch `db`.

No changes needed to `config/gitea-docker-compose.yml` or `app.py` for this
part.

---

## Part B — Item 47: Taiga frontend under a tailscale-serve subpath

**Two compounding causes were found live on CT110 (`docs/BACKLOG.md` item
47) — verify both against this repo's *current* code before touching
anything, since one of them may already be fixed:**

**(b) Confirmed still missing — fix this one.** `taiga-front`'s own `.env`
supports `SUBPATH`, `TAIGA_SCHEME`, and `WEBSOCKETS_SCHEME` (first-class in
its Docker image) but `install.sh`'s Taiga block only ever sets
`TAIGA_SCHEME` on the **gateway's** `.env` (`install.sh:438`,
`$TAIGA_ENV`, i.e. `$TAIGA_DIR/.env` — this is `taiga-back`/
`taiga-gateway`'s shared env file, not `taiga-front`'s own). `taiga-front`
gets no `.env` file written at all today — confirmed by grep, no
`SUBPATH`/`WEBSOCKETS_SCHEME` reference anywhere in `install.sh`. Since
`TAIGA_URL_PATH` is a fixed `"/taiga"` (`app/app.py:2978`, singleton, same
shape as Gitea's `GITEA_URL_PATH`), the subpath value to populate is known
statically, not derived per-request.

Add, alongside the existing `TAIGA_SCHEME`/`TAIGA_DOMAIN` block
(`install.sh:438-451`): write `SUBPATH`, `TAIGA_SCHEME`,
`WEBSOCKETS_SCHEME` into a `taiga-front`-specific env source. Check
`taiga-docker`'s actual compose file (`$TAIGA_DIR/docker-compose.yml`,
cloned at `install.sh:392-396`) for how `taiga-front`'s service picks up
its own `.env` — it's likely a *separate* env file (upstream
`taiga-docker` convention: `taiga-front/.env` alongside the root `.env`
used by back/gateway) or the same root `.env` with different variable
names (`taiga-front`'s image reads `TAIGA_URL`/`SUBPATH`/etc. — confirm the
exact var names upstream expects by reading that image's own docs/entrypoint
in the cloned checkout, don't guess). Mirror the existing "only set the
non-secret values every run, tracking whatever PUBLISH_MODE/BASE_URL
currently resolve to" behavior the gateway's own `TAIGA_SCHEME`/
`TAIGA_DOMAIN` already follow — same conditional (`PUBLISH_MODE=tailscale`
+ `BASE_URL` set → derive from `BASE_URL`; else → local/plain values), same
`set_env` idiom.

**(a) Possibly already fixed — verify live, don't blind-patch.** The
backlog item's other stated cause was `location /`'s (frontend) variable
`proxy_pass` dropping URI rewriting the same way items 42/43 originally
found for `/api/`/`/admin/`. The *current* `docker-compose.override.taiga-
gateway.conf` heredoc (`install.sh:538-611`) already uses the identical
`set $upstream_front taiga-front; proxy_pass http://$upstream_front/;`
variable pattern, with a `resolver 127.0.0.11 valid=10s;` directive, for
**every** location including `location /` — this may already be the
item-43-style fix the backlog note says is still missing, in which case
that half of item 47 is stale and needs no further change. Note also that
`app/app.py:703-706`'s own comment on `_ttyd_start()` confirms `tailscale
serve --set-path` **strips** the subpath prefix before forwarding to the
backend — so `taiga-gateway` itself receives plain, unprefixed paths, not
`/taiga/...`-prefixed ones. This means the actual failure mode is more
likely entirely explained by (b) (taiga-front generating wrong absolute
asset/API/WebSocket URLs because it doesn't know it's mounted under
`/taiga`), not by nginx's proxy_pass behavior at all.

**Verification the reviewer must do for both (a) and (b):** bring up a
real `--with-taiga` stack with `PUBLISH_MODE=tailscale` (or simulate the
subpath by hitting the gateway through a path prefix, if no real tailnet is
available in the sandbox) and confirm the frontend renders styled, with
working asset/API/WebSocket requests — not just that the root page returns
200. A raw `curl` of `/` alone will not catch this class of bug (that's
exactly how it went unnoticed the first time — "the first request under a
subpath is always literally `/`, so it worked by coincidence").

---

## Non-goals

- Item 41 (VS Code / code-server hardcoded path) — already fixed in this
  repo's code (`app/app.py:117-127`, `install.sh:231-250` both resolve
  `code-server` dynamically via `command -v`/`shutil.which`). Not part of
  this cycle. If the user's live "VS Code sessions don't start" report
  persists, it's a separate investigation (most likely the deployed box
  predates this fix and needs `install.sh --update`) — out of scope here.
- Item 50 (backup-before-upgrade) — unrelated, separate backlog item, not
  touched by this cycle.
- No new UI, no `docs/design.md` needed.

## Acceptance criteria

1. A fresh `--with-git-hosting` install, followed by a second `install.sh`
   run where `BASE_URL`/`PUBLISH_MODE` changed, force-recreates Gitea's
   `server` container and the new `ROOT_URL`/`DOMAIN` are reflected in
   generated links without a manual restart.
2. A fresh `--with-taiga` install under `PUBLISH_MODE=tailscale` renders a
   fully styled Taiga frontend at `$BASE_URL/taiga`, with working
   asset/API/WebSocket requests through the subpath — verified with an
   actual browser-level check (Playwright), not just an HTTP status code.
3. Existing test suite stays green; no regression in the non-tailscale
   (`PUBLISH_MODE=none`) path for either service.
