# Spec: Item 43 — close the taiga-gateway startup DNS race at its root (lazy nginx upstream resolution), plus honest fallback reporting in taiga-up.sh

## Summary
Fix `taiga-gateway`'s nginx to resolve its upstream hostnames (`taiga-front`, `taiga-back`, `taiga-events`, `taiga-protected`) lazily at request time instead of once at config-load/startup — via a repo-owned override file that Compose mounts *over* the pinned checkout's own `taiga-gateway/taiga.conf`, never editing that checkout in place — and make `scripts/taiga-up.sh`'s round-7 last-resort fallback reuse the existing settle-and-recheck window so it can no longer report success for a container that's about to crash.

## Goals
- Close the deterministic Docker-Compose-DNS startup race (item 43 / item 30) that makes `taiga-gateway` exit(1) at boot if `taiga-front`'s (or any other upstream's) DNS record isn't registered with Docker's embedded resolver (127.0.0.11) yet when nginx loads its config.
- Do this without editing any git-tracked file inside the pinned, third-party `taigaio/taiga-docker` checkout at `$TAIGA_DIR` (preserves the spirit of the item-30 architecture decision at install.sh ~line 452-455 — see "Proposed approach" for why this is achievable, not a reversal).
- Make `scripts/taiga-up.sh`'s round-7 fallback attempt (the plain `up -d` tried after `TAIGA_UP_MAX_ATTEMPTS` is exhausted) honest: it must no longer be able to report `running`/exit 0 for a container that dies seconds later, the same guarantee the main retry loop already has via its settle-and-recheck window.

## Non-goals
- Not touching `app/app.py`'s `taiga_run()` / `/taiga/on` error surfacing — item 42 already fixed that in round 7 (confirmed: `taiga_run()` now raises on non-zero exit, comment at app/app.py ~line 2688); this spec's fallback fix only stops the fallback from lying to `taiga_run()` in the first place.
- Not removing item 30's `taiga-front` healthcheck / `depends_on: condition: service_healthy` gate in `docker-compose.override.yml` — kept as defense-in-depth alongside the nginx fix (see "Open questions" #3 for the reasoning).
- Not attempting the opt-in `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` host-wide Docker daemon restart path — out of scope, unrelated to this item, already gated correctly behind an explicit operator opt-in.
- Not `git pull`-ing or otherwise updating the pinned `taigaio/taiga-docker` checkout itself, and not changing install.sh's "pinned at first clone, never re-pulled" policy.
- Not adding a UI/UX layer — this is backend/infra only (nginx config + two shell scripts); ux-designer is skipped for this cycle, same as round 7.

## Background / current state
`taiga-gateway` (upstream image `nginx:1.19-alpine`) mounts its nginx config from the pinned checkout via a **bind mount**, declared in the pinned `$TAIGA_DIR/docker-compose.yml`:
```yaml
  taiga-gateway:
    image: nginx:1.19-alpine
    ports:
      - "9000:80"
    volumes:
      - ./taiga-gateway/taiga.conf:/etc/nginx/conf.d/default.conf
      - taiga-static-data:/taiga/static
      - taiga-media-data:/taiga/media
```
(confirmed against `taigaio/taiga-docker`'s `stable` branch on GitHub directly, fetched today — see Open Questions #1 for a caveat on this).

The mounted `taiga-gateway/taiga.conf` has **5** `proxy_pass` locations, of which **4** reference a bare upstream hostname resolved once when nginx loads this config at container start:
```
location /       { proxy_pass http://taiga-front/; }
location /api/   { proxy_pass http://taiga-back:8000/api/; }
location /admin/ { proxy_pass http://taiga-back:8000/admin/; }
location /media/ { proxy_pass http://taiga-protected:8003/; }
location /events { proxy_pass http://taiga-events:8888/events; }
```
(`/static/`, `/_protected/`, `/media/exports/` are plain `alias`, not `proxy_pass` — not affected.)

If Docker's embedded DNS (127.0.0.11) hasn't registered a referenced service's name yet at the moment nginx loads this config, nginx's `[emerg] host not found in upstream "..."` config-load failure kills the whole process, and the container exits(1) immediately — fatal, not transient, and Docker's own `depends_on` (even with `condition: service_started`) only guarantees the *other* container has *started*, not that its DNS record has propagated yet. This is a narrow, real, reproducible startup-time race, confirmed twice live (round 6 original report, round 7 retest with a pinned-down mechanism).

Round 6 (item 30) added, in the repo-owned `docker-compose.override.yml` (install.sh ~line 434-492, regenerated deterministically every run, **not** part of the pinned checkout's own git history):
- A healthcheck on `taiga-front` (`wget --spider http://127.0.0.1/`).
- `taiga-gateway`'s `depends_on.taiga-front` upgraded to `condition: service_healthy`.

This narrows the race for `taiga-front` specifically but does not close it (confirmed by round 7's live retest: gateway still crashed with the exact `taiga-front` DNS error), and never covered `taiga-back`/`taiga-events`/`taiga-protected` at all — none of those three have a healthcheck upstream, so they were never gated beyond `condition: service_started`, which is the weakest guarantee Compose offers.

Round 7 (item 43, commit `a6991c2`) added a last-resort plain `docker compose up -d` fallback in `scripts/taiga-up.sh` (lines 68-81, after the 5-attempt retry loop exhausts) with no settle-window recheck — its own code comment says "no settle-window recheck on this one extra attempt — keep it simple." Round 7's retest found this fallback *does* report success now (catching the container in a brief pre-crash `running` window), but the container still crashes seconds later from the identical nginx DNS error — so the toggle response goes back to lying, via a new mechanism, about the exact failure item 42 was meant to stop lying about.

`taiga-up.sh`'s main retry loop (lines 47-59) already has the fix pattern needed for the fallback: sleep `TAIGA_UP_SETTLE_SECONDS` (default 5) after seeing `running`, then re-check before trusting it.

Docker Compose's documented multi-file merge behavior (confirmed against Docker's own docs): `volumes:` entries merge **by container target path** across `-f` files — an override entry whose target matches a base entry's target *replaces* that one entry; entries at other targets are left untouched. This is the same principle install.sh's existing `docker-compose.override.yml` already relies on for `depends_on`/`ports`, just not yet exercised for `volumes`.

## Proposed approach

### 1. Real fix: lazy DNS resolution via a repo-owned nginx conf, bind-mounted over the pinned checkout's own file

Add a second heredoc-generated file, written unconditionally every install.sh run (same "deterministically regenerated, untracked by the pinned checkout's git history" idiom as `docker-compose.override.yml` itself), e.g. `$TAIGA_DIR/docker-compose.override.taiga-gateway.conf`:

```nginx
server {
    listen 80 default_server;

    client_max_body_size 100M;
    charset utf-8;

    resolver 127.0.0.11 valid=10s;

    # Frontend
    location / {
        set $upstream_front taiga-front;
        proxy_pass http://$upstream_front/;
        proxy_pass_header Server;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
    }

    # API
    location /api/ {
        set $upstream_back taiga-back;
        proxy_pass http://$upstream_back:8000/api/;
        proxy_pass_header Server;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
    }

    # Admin
    location /admin/ {
        set $upstream_back taiga-back;
        proxy_pass http://$upstream_back:8000/admin/;
        proxy_pass_header Server;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
    }

    # Static
    location /static/ {
        alias /taiga/static/;
    }

    # Media
    location /_protected/ {
        internal;
        alias /taiga/media/;
        add_header Content-disposition "attachment";
    }

    # Unprotected section
    location /media/exports/ {
        alias /taiga/media/exports/;
        add_header Content-disposition "attachment";
    }

    location /media/ {
        set $upstream_protected taiga-protected;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://$upstream_protected:8003/;
        proxy_redirect off;
    }

    # Events
    location /events {
        set $upstream_events taiga-events;
        proxy_pass http://$upstream_events:8888/events;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }
}
```
This is upstream's own `taiga-gateway/taiga.conf` verbatim (fetched directly from `taigaio/taiga-docker`'s `stable` branch today — see Open Questions #1), with exactly two categories of change: one `resolver 127.0.0.11 valid=10s;` line added, and each of the 4 bare-hostname `proxy_pass` targets rewritten to `set $upstream_x <hostname>; proxy_pass http://$upstream_x...;` — nginx only does the DNS lookup for a `proxy_pass` target when it's a variable, at *request* time, honoring `resolver`'s TTL, instead of once at config load. This is nginx's standard, documented fix for this exact class of Docker-Compose-DNS race. `/static/`, `/_protected/`, `/media/exports/` are untouched (`alias`, no proxy, no DNS involved).

Then extend the *existing* `docker-compose.override.yml` heredoc (install.sh, currently lines 473-492) to add a `volumes:` key under the existing `taiga-gateway:` block:
```yaml
  taiga-gateway:
    ports:
      - "127.0.0.1:${TAIGA_PORT}:80"
    volumes:
      - ./docker-compose.override.taiga-gateway.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      taiga-front:
        condition: service_healthy
      taiga-back:
        condition: service_started
      taiga-events:
        condition: service_started
```
Because Compose merges `volumes:` by container target path (confirmed against Docker's docs — see "Background"), this single-entry `volumes:` list **replaces only** the `/etc/nginx/conf.d/default.conf` mount; the base file's other two `taiga-gateway` mounts (`taiga-static-data:/taiga/static`, `taiga-media-data:/taiga/media`) are left untouched and still apply. The pinned checkout's own `$TAIGA_DIR/taiga-gateway/taiga.conf` file is never opened for writing by this change — the mount is redirected, not the file it used to point at.

**Why this preserves the item-30 decision's spirit rather than reversing it**: that decision's actual constraint (install.sh's own comment) is "not by patching taiga.conf/docker-compose.yml inside the pinned, third-party checkout itself" — i.e. never write into files that checkout's own `git` history tracks, so a hypothetical future `git pull` in `$TAIGA_DIR` never conflicts. `docker-compose.override.taiga-gateway.conf` is a new, untracked file living alongside `docker-compose.override.yml` (which the decision's own text already treats as the correct pattern), regenerated deterministically every install.sh run, and it changes *only* which file Compose bind-mounts into the container — the pinned checkout's own `taiga-gateway/taiga.conf` is byte-for-byte untouched on disk. This is a direct application of the same override-file idiom the item-30 decision established, extended from `depends_on`/`ports` (already overridden today) to `volumes` (not yet exercised until now) — not a departure from it.

Both files get written in the same "4. Loopback-only binding, and (item 30) health-gating..." section of install.sh, right before the existing `docker-compose.override.yml` heredoc, and the section's own comment block should be extended (not replaced) to record this second layer of the item-30/43 fix and its reasoning, matching the file's existing very-heavily-commented style.

### 2. Stopgap: settle-and-recheck on the round-7 fallback too

In `scripts/taiga-up.sh`, the round-7 fallback block (currently lines 76-81):
```bash
"${COMPOSE[@]}" up -d
state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
if [ "$state" = "running" ]; then
    exit 0
fi
```
must gain the identical settle-and-recheck the main loop already does at lines 50-56 before trusting `running`:
```bash
"${COMPOSE[@]}" up -d
state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
if [ "$state" = "running" ]; then
    sleep "$TAIGA_UP_SETTLE_SECONDS"
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    if [ "$state" = "running" ]; then
        exit 0
    fi
    echo "taiga-up: last-resort attempt reported running but died within the ${TAIGA_UP_SETTLE_SECONDS}s settle window (state: ${state:-<none>})" >&2
fi
```
Prefer factoring the "up -d, check running, sleep+recheck" sequence (now used identically 2 places, or 3 counting each loop iteration) into a small shell function to avoid a third copy-paste of the same 6 lines, if that doesn't make the diff harder to review than the straightforward duplication — developer's call, not a hard requirement. Update the block's own comment (currently says "No settle-window recheck on this one extra attempt — keep it simple") to reflect the new behavior and why it changed (item 43 stopgap, kept as defense-in-depth even after the real nginx fix lands, in case the nginx fix needs a second round to land cleanly).

### 3. Tests
Extend `tests/test_taiga_up_retry.py` (existing harness: real `scripts/taiga-up.sh` run as a subprocess with `docker` stubbed as a shell function reporting state via a counter file — see its own module docstring) with a case for the fallback: all `TAIGA_UP_MAX_ATTEMPTS` attempts fail, then the fallback's own `up -d` call reports `running` on the first `ps` check but `exited` on the settle-window recheck — assert the script now exits 1 (not 0), proving the fallback can no longer fabricate success for a container about to crash. The existing `settle_die_at` parameter in `_run()`'s helper (per its docstring) already models "running on first check, exited after settle sleep" for the main loop — extend it (or add an analogous parameter) to also cover the fallback's own extra attempt.

No test is added asserting the nginx conf's actual DNS-resolution behavior against a real Docker daemon — that requires a live Taiga stack and embedded DNS, which isn't available in unit-test scope; verifying AC1-AC4 below is a job for the next real E2E retest round, called out explicitly rather than faked with a shell-level assertion that wouldn't actually prove nginx behaves correctly under a real DNS race.

## Affected areas
- `install.sh` (~line 434-492 section): new heredoc for `docker-compose.override.taiga-gateway.conf`, extended `docker-compose.override.yml` heredoc (`volumes:` key added to `taiga-gateway:`), extended comment block.
- `scripts/taiga-up.sh` (lines 68-81): fallback block gains settle-and-recheck, comment updated.
- `tests/test_taiga_up_retry.py`: new test case for the fallback's settle-window behavior.
- No API/interface changes, no data model changes. Single architectural layer (install/infra shell + one vendored-but-overridden config file) — no split into sub-specs needed (skill 11 N/A here).

## Edge cases
- **Re-run on an existing `$TAIGA_DIR`** (not a fresh clone): both new/changed files must be written unconditionally every run, same as `docker-compose.override.yml` already is today (not gated behind `TAIGA_FRESH_CLONE`, which only gates one-time secret randomization) — otherwise a host that was set up before this fix ships never picks it up on a subsequent `install.sh` re-run.
- **A host operator who manually customized the pinned checkout's own `taiga-gateway/taiga.conf`** (e.g. added a header, changed `client_max_body_size`): after this change, that customization is silently superseded at the *mount* level (Compose uses our override's target-matching entry, not the base's), even though the physical file on disk is untouched and looks unmodified if someone inspects it directly. This is a real, if narrow, behavior change — flagged under Open Questions rather than silently absorbed.
- **`taiga-back`/`taiga-events`/`taiga-protected` never had item 30's healthcheck-based gating** (only `taiga-front` did) — this fix is actually their *only* protection against the same class of race, not just an incremental improvement over an existing gate. Worth stating in the acceptance criteria explicitly rather than only testing the one upstream (`taiga-front`) that's already been observed racing live.
- **Docker's embedded resolver itself being unreachable** (unusual host-level Docker networking misconfiguration): nginx would serve a `502` on the affected location rather than fail to load config — a strict improvement over today's full container crash regardless of this edge case's likelihood.
- **The fallback's new settle window adds `TAIGA_UP_SETTLE_SECONDS` (default 5s) to the worst-case `/taiga/on` request duration** in the already-slow exhausted-retry-budget path — acceptable; item 42's fix means a real failure now surfaces correctly either way, and this path is already the ~2m45s worst case, not the common one.
- **Concurrent/double `/taiga/on` submissions** — already handled by item 36's earlier fix; out of scope here, not re-touched.

## Acceptance criteria
- [ ] Given a fresh `--with-taiga` install (new `$TAIGA_DIR` clone) or a re-run of `install.sh` against an existing `$TAIGA_DIR`, when install.sh's Taiga section runs, then `$TAIGA_DIR/taiga-gateway/taiga.conf` (the pinned checkout's own git-tracked file) is byte-for-byte unchanged from what `git clone` produced.
- [ ] Given the same run, when it completes, then `$TAIGA_DIR/docker-compose.override.taiga-gateway.conf` exists, contains `resolver 127.0.0.11 valid=10s;`, and all 4 previously-bare-hostname `proxy_pass` locations (`/`, `/api/`, `/admin/`, `/media/`, `/events`) now reference a `set $upstream_...` variable instead of a literal hostname directly in `proxy_pass`.
- [ ] Given two consecutive `install.sh` runs against the same `$TAIGA_DIR`, when both complete, then `docker-compose.override.taiga-gateway.conf`'s content is byte-for-byte identical across both runs (deterministic regeneration, same as `docker-compose.override.yml` today).
- [ ] Given `docker compose -f docker-compose.yml -f docker-compose.override.yml config` is run in `$TAIGA_DIR`, when `taiga-gateway`'s resolved config is inspected, then its `/etc/nginx/conf.d/default.conf` mount source is `docker-compose.override.taiga-gateway.conf`, and its `taiga-static-data`/`taiga-media-data` mounts are still present and unchanged from the base file.
- [ ] Given a real Taiga install reproducing the original race (`taiga-front`'s Docker DNS record not yet registered at the moment `taiga-gateway`'s nginx boots), when `taiga-gateway` starts, then nginx starts successfully and the container does not exit(1) — this is the E2E-verifiable claim for the next retest round, not unit-testable in this repo.
- [ ] Given `scripts/taiga-up.sh`'s main retry loop exhausts all `TAIGA_UP_MAX_ATTEMPTS`, when the round-7 last-resort fallback's `up -d` reports `taiga-gateway` as `running` but the container dies within `TAIGA_UP_SETTLE_SECONDS`, then the script exits 1 (not 0) — proven by a new automated test in `tests/test_taiga_up_retry.py` using the existing stub-`docker`-as-shell-function technique.
- [ ] Given the fallback's `up -d` reports `running` and the container is still `running` after the settle window, when the script finishes, then it exits 0 exactly as it does today (no regression to the already-working case).
- [ ] `python3 -m unittest discover -s tests` still passes in full, including the existing `tests/test_taiga_up_retry.py` cases.

## Open questions
- **#1 (flagged per the brief's own instruction — no live Taiga install available in this sandbox to verify against)**: the exact literal content of `taiga-gateway/taiga.conf` and `docker-compose.yml` used above was fetched directly from `taigaio/taiga-docker`'s `stable` branch on GitHub today (2026-08-16), not from an actual `$TAIGA_DIR` on a real installed host, because none exists in this sandbox. install.sh pins whatever commit was first cloned on a given host — if an existing test host's `$TAIGA_DIR` was cloned from an older `stable` HEAD whose `taiga.conf`/`docker-compose.yml` structure differed (unlikely for a file this stable, but not independently verified here), the heredoc content and/or the volume-mount target path could be stale for that specific host. Proceeding on the assumption this file's shape has been stable for a long time; developer should diff the heredoc against the actual `$TAIGA_DIR/taiga-gateway/taiga.conf` on whatever host round-8's retest runs against (or on this dev box's own `$TAIGA_DIR` if a Taiga install exists there) before considering AC1-AC4 verified, not just AC1-AC3 (which are mechanical/install.sh-only and don't depend on this).
- **#2**: the `volumes:` merge-by-target-path behavior is confirmed against Docker's own current documentation, not independently re-verified against the exact Compose version `ensure_docker()` installs on a live host. This has been stable Compose-v2 behavior for a long time; low risk, but not hands-on-verified here either — same category of gap as #1.
- **#3**: proceeding under the assumption that item 30's `taiga-front` healthcheck / `service_healthy` gate should stay in `docker-compose.override.yml` even after this fix lands, as defense-in-depth (cheap, already-working code, and a second, independent layer in case the nginx fix needs a follow-up round) — not proposing its removal. Flagging in case there's a reason to prefer simplifying it away instead once the nginx fix is confirmed sufficient on its own.
- **#4**: the new file's name (`docker-compose.override.taiga-gateway.conf`) is a proposal for consistency with `docker-compose.override.yml`'s existing naming; no strong constraint found elsewhere in the codebase, developer may rename if something reads more clearly, as long as it stays deterministically-regenerated and untracked by the pinned checkout exactly like `docker-compose.override.yml`.

## Risk / rollback notes
- Both changes are purely additive/config-level and confined to `install.sh`'s Taiga section, `scripts/taiga-up.sh`, and one new test file — no changes to `app/app.py`, the frontend, or any other feature area. Rollback is a plain `git revert` of the commit; on an already-installed host, re-running `install.sh` after a revert regenerates the old two-file `docker-compose.override.yml` (no `volumes:` override) and removes the need for the new conf file (stale copies of it left on disk are harmless — Compose only reads what the current override file references).
- Worst-case failure mode if the nginx conf heredoc has a typo: `docker compose up -d taiga-gateway` fails immediately and loudly at `nginx -t`/config-parse time (same failure class Docker already surfaces today), not a silent partial-behavior regression — easy to catch in the next E2E retest round.
- If AC1-AC4 turn out to need a second round to fully close (per Open Question #1's caveat), the taiga-up.sh stopgap (AC6-AC7) still ships independent value on its own: it stops the toggle from lying about success even if the underlying race isn't fully closed yet, same reasoning the brief itself gave for scoping it in regardless of which real-fix path was chosen.
