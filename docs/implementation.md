# Implementation: E2E round 8 — item 43's real fix (lazy nginx upstream resolution) + honest taiga-up.sh fallback reporting

## Summary
Closed the `taiga-gateway` startup DNS race at its root: nginx now resolves its 4 upstream hostnames (`taiga-front`, `taiga-back` x2, `taiga-events`, `taiga-protected`) lazily at request time via a `resolver` directive + `set $upstream_x`/`proxy_pass http://$upstream_x` indirection, instead of once at config-load time. Delivered as a new repo-owned, deterministically-regenerated nginx conf that `install.sh` bind-mounts *over* the pinned `taigaio/taiga-docker` checkout's own `taiga-gateway/taiga.conf` via an extended `docker-compose.override.yml` `volumes:` entry — the pinned checkout's own file is never opened for writing. Also made `scripts/taiga-up.sh`'s round-7 last-resort fallback attempt reuse the main retry loop's existing settle-and-recheck window, so it can no longer report success for a `taiga-gateway` container that's about to crash.

## Root cause
`taiga-gateway`'s bundled nginx config does `proxy_pass http://taiga-front/;` (and 3 similar bare-hostname targets) with no `resolver` directive — nginx resolves a bare-hostname `proxy_pass` target exactly once, at config-load time, using whatever `/etc/resolv.conf` gives it (Docker's embedded DNS at 127.0.0.11). If that upstream's DNS record hasn't propagated yet at that exact instant (an ordinary Compose startup-ordering race — `depends_on` only guarantees a container has *started*, not that its DNS record is registered), nginx's config load fails with `[emerg] host not found in upstream "..."` and the whole process — and therefore the container — exits(1) immediately. Round 6's `taiga-front` healthcheck/`service_healthy` gate narrows this only for that one upstream's container-readiness, not its DNS-propagation timing, which is why it was confirmed live (round 7 retest) to still not close the race, and never covered the other 3 upstreams at all (none of which have a healthcheck).

## Changes by file
- `install.sh` (Taiga section, "4. Loopback-only binding, and (item 30) health-gating..." block, ~line 434-611)
  - Added a new heredoc, written unconditionally every run (same idiom as `docker-compose.override.yml`), writing `$TAIGA_DIR/docker-compose.override.taiga-gateway.conf`: upstream's own `taiga-gateway/taiga.conf` content (as confirmed by product-manager against `taigaio/taiga-docker`'s `stable` branch — see "Known limitations" for what I could/couldn't independently verify) plus `resolver 127.0.0.11 valid=10s;` and the 4 bare-hostname `proxy_pass` targets rewritten to `set $upstream_x <host>; proxy_pass http://$upstream_x...;`.
  - Extended the existing `docker-compose.override.yml` heredoc's `taiga-gateway:` block with a `volumes:` key: `- ./docker-compose.override.taiga-gateway.conf:/etc/nginx/conf.d/default.conf`. Compose merges `volumes:` by container target path across `-f` files, so this single entry replaces only that one mount; the base file's `taiga-static-data`/`taiga-media-data` mounts on the same service are untouched (confirmed empirically — see "How to verify locally").
  - Extended the section's comment block (didn't replace it) to record the item-43 reasoning alongside the existing item-30 comment, matching the file's existing heavily-commented style.
  - Item 30's `taiga-front` healthcheck / `depends_on: condition: service_healthy` gate is unchanged — kept as defense-in-depth per spec.

- `scripts/taiga-up.sh`
  - The round-7 last-resort fallback block (after the 5-attempt retry loop exhausts) now sleeps `TAIGA_UP_SETTLE_SECONDS` after an initial `running` read and rechecks before trusting it — identical pattern to the main loop's existing settle-and-recheck (lines 50-56), applied literally per docs/spec.md's proposed code. On a die-before-settled, it prints `taiga-up: last-resort attempt reported running but died within the ${TAIGA_UP_SETTLE_SECONDS}s settle window (state: ...)` to stderr and falls through to the existing (opt-in) daemon-restart path / final failure message and `exit 1`, instead of exiting 0.
  - Updated the block's comment to explain why the settle-and-recheck was added (round 8, item 43) and that it's kept as defense-in-depth even after the nginx fix, in case that fix needs a second round.
  - Did not factor the "up -d, check running, sleep+recheck" sequence into a shared function (spec left this as developer's call) — kept the straightforward duplication; the fallback's own extra messaging (`last-resort attempt reported running...` vs. the loop's `taiga-gateway was running but died within the...`) differs enough between the two call sites that a shared helper would need a message parameter, and the diff stays smaller/easier to review as a direct copy of the main loop's already-proven 6-line pattern.

- `tests/test_taiga_up_retry.py`
  - Added `test_fallback_settle_window_recheck_catches_gateway_that_dies_before_settling`: fallback's own `up -d` (the 4th call, `max_attempts=3`) reports `running` on its first `ps` check but `exited` on the settle-window recheck — asserts `returncode == 1` (not 0), proving the fallback can no longer fabricate success (AC6).
  - Added `test_fallback_settle_window_recheck_still_honors_genuine_success`: fallback reports `running` and stays `running` through the settle window — asserts `returncode == 0`, no regression to the already-working case (AC7). (Overlaps in shape with the pre-existing `test_fallback_up_after_exhaustion_succeeds`, kept as an explicit AC7-mapped regression guard per the spec's own acceptance-criteria wording.)
  - Both reuse the existing `_run()` helper's `settle_die_at` parameter unchanged — its docstring already documents it as tracking the raw `up -d` call count (`n`), which naturally covers the fallback's 4th call with no code changes to the test harness itself, only new test cases.

## Key decisions / tradeoffs
- Followed docs/spec.md's proposed nginx conf and `docker-compose.override.yml` heredoc content verbatim rather than re-deriving it, per the developer brief's explicit instruction (no live Taiga install in this sandbox either).
- Validated the merge/mount mechanism empirically rather than only trusting Docker's documented `volumes:`-merges-by-target-path behavior (see "How to verify locally" — ran `docker compose config` against a synthetic base `docker-compose.yml` built from the exact structure docs/spec.md's "Background" section quotes, using the real generated override files).
- Went further than the spec's minimum verification ask and reproduced the actual DNS race live against the real `nginx:1.19-alpine` image (already cached on this box) using a throwaway Docker network with no `taiga-front` container present yet: the original bare-hostname conf crashed with the exact `[emerg] host not found in upstream "taiga-front"` error from the bug reports; the new lazy-resolver conf started and stayed `running` under the identical condition, and once a stand-in `taiga-front` container joined the network, a request through the gateway proxied through successfully. This isn't a substitute for a full live Taiga stack retest (real `taiga-front`/`taiga-back`/`taiga-events`/`taiga-protected` images, real Compose startup ordering, real `install.sh` run) but it does directly exercise the exact nginx mechanism the fix depends on, at the same layer the original bug reports were observed at.

## Deviations from spec
None. Implemented docs/spec.md's "Proposed approach" sections 1-3 as written, including the exact nginx conf content, the exact `volumes:` override shape, and the exact fallback settle-and-recheck code given in section 2.

## Known limitations
- **Open question #1 from docs/spec.md** (upstream `taiga.conf`/`docker-compose.yml` content fetched from `taigaio/taiga-docker`'s `stable` branch on GitHub, not from a live `$TAIGA_DIR`): I have no live `$TAIGA_DIR` on this box either (confirmed — `/opt/ai-dev-switchboard-taiga` doesn't exist here), so I could not diff the heredoc against a real installed host's copy. I did, however, syntax-check the generated conf with a real `nginx -t` (via the `nginx:1.19-alpine` image already cached locally) and live-reproduce the actual DNS race against that same image (see "Key decisions" above) — this validates the conf's nginx syntax and the lazy-resolution mechanism itself, but not that it's byte-for-byte what a specific already-installed host's pinned checkout currently has. Per the spec's own open question, this still needs confirming against whatever host round-8's retest runs against before AC4/AC5 (the live-race acceptance criteria) are considered fully closed — AC1-AC3 (mechanical, install.sh-only) are confirmed here.
- **Open question #2** (Compose `volumes:`-merges-by-target-path behavior): confirmed empirically in this sandbox against the real installed Docker Compose v5.4.0 (`docker compose config` output showed the override's bind mount replacing only the `/etc/nginx/conf.d/default.conf` target, with `taiga-static-data`/`taiga-media-data` volume mounts unchanged) — not just trusted from documentation. Not independently re-verified against whatever exact Compose version `ensure_docker()` installs on a live target host, but this is the same Compose major version family, so low residual risk.
- No unit test asserts the nginx conf's DNS-resolution behavior against Docker's embedded resolver in the actual `install.sh`-produced Taiga stack (real `taiga-front`/`taiga-back`/`taiga-events`/`taiga-protected` containers, real startup ordering) — that's out of unit-test scope per the spec, and remains a job for the next real E2E retest round on a live host.
- `scripts/taiga-up.sh`'s fallback settle-window fix only stops it from *reporting* success dishonestly; it doesn't itself close the underlying race (the nginx fix does that). Both changes ship independent value per the spec's risk/rollback notes.

## How to verify locally
```bash
cd /home/dev/projects/ai-dev-switchboard

# Syntax check both changed shell scripts
bash -n install.sh
bash -n scripts/taiga-up.sh

# taiga-up.sh regression + new fallback-settle-window tests
python3 tests/test_taiga_up_retry.py -v

# Full existing test suite (confirms no regressions elsewhere)
python3 -m unittest discover -s tests

# Static validation of the generated Compose override + nginx conf (no live
# $TAIGA_DIR needed -- extracts install.sh's own heredoc-writing lines
# verbatim and runs them against a synthetic base docker-compose.yml built
# from docs/spec.md's confirmed upstream structure):
SCRATCH=$(mktemp -d)
printf 'TAIGA_PORT=9000\n' > "$SCRATCH/.env"
cat > "$SCRATCH/docker-compose.yml" <<'EOF'
services:
  taiga-gateway:
    image: nginx:1.19-alpine
    ports:
      - "9000:80"
    volumes:
      - ./taiga-gateway/taiga.conf:/etc/nginx/conf.d/default.conf
      - taiga-static-data:/taiga/static
      - taiga-media-data:/taiga/media
  taiga-front: { image: taigaio/taiga-front:latest }
  taiga-back: { image: taigaio/taiga-back:latest }
  taiga-events: { image: taigaio/taiga-events:latest }
volumes:
  taiga-static-data:
  taiga-media-data:
EOF
mkdir -p "$SCRATCH/taiga-gateway"
echo "PLACEHOLDER-DO-NOT-TOUCH" > "$SCRATCH/taiga-gateway/taiga.conf"
sed -n '504,609p' install.sh > /tmp/heredoc_extract.sh   # line numbers may shift; grep 'cat > "$TAIGA_DIR' to relocate
TAIGA_DIR="$SCRATCH" bash -c "$(cat /tmp/heredoc_extract.sh)"
diff <(echo PLACEHOLDER-DO-NOT-TOUCH) "$SCRATCH/taiga-gateway/taiga.conf"   # AC1: base file untouched
grep -n 'resolver\|upstream_' "$SCRATCH/docker-compose.override.taiga-gateway.conf"  # AC2
cd "$SCRATCH" && docker compose -f docker-compose.yml -f docker-compose.override.yml config  # AC4: mount source + other mounts

# Syntax-check the generated nginx conf with a real nginx binary (via Docker)
docker run --rm -v "$SCRATCH/docker-compose.override.taiga-gateway.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.19-alpine nginx -t

# Live-reproduce the exact DNS race (AC5) against the real nginx image, no
# taiga-front registered yet on the network:
docker network create taiga-dns-race-test
docker run -d --name gw-fixed --network taiga-dns-race-test \
  -v "$SCRATCH/docker-compose.override.taiga-gateway.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:1.19-alpine
sleep 2 && docker inspect -f '{{.State.Status}}' gw-fixed   # expect "running", not "exited"
docker rm -f gw-fixed && docker network rm taiga-dns-race-test
```
Results at implementation time: `bash -n` clean on both scripts; `python3 tests/test_taiga_up_retry.py -v` — 12/12 pass (2 new); full suite — 1266 tests, 3 failures (all pre-existing, unrelated `test_teams_grounding.py` cases caused by a locally-present gitignored root `CLAUDE.md`), 1 pre-existing environmental skip, 0 new failures; `docker compose config` confirmed the `/etc/nginx/conf.d/default.conf` mount source is the new override conf and `taiga-static-data`/`taiga-media-data` mounts are unchanged; `nginx -t` reported the generated conf syntactically valid; live DNS-race reproduction confirmed the container stays `running` (vs. the original bare-hostname conf, which reliably reproduced the reported `[emerg] host not found in upstream "taiga-front"` crash under the identical no-DNS-record-yet condition).
