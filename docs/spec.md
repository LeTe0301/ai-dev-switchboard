# Spec: Item 44 — taiga-gateway's host port never actually gets published (Compose `ports:` merge collision)

## Summary
Patch the pinned `taiga-docker` checkout's own `docker-compose.yml` `ports:`
line for `taiga-gateway` (via a narrowly-scoped, idempotent `sed`, applied by
install.sh) instead of trying to override it from
`docker-compose.override.yml`, because Compose merges list fields like
`ports:` by concatenation, not by replacement — the override approach can
never actually close this gap, and left `taiga-gateway` silently unreachable
on every run since round 6.

## Goals
- Taiga is reachable at `http://127.0.0.1:${TAIGA_PORT}/` immediately after
  any successful `POST /taiga/on` (or manual `docker compose up -d`), on
  both a fresh `--with-taiga` install and a re-run/upgrade of an existing
  one.
- `taiga-gateway` publishes to `127.0.0.1` only, never `0.0.0.0` — matches
  this project's existing "everything binds loopback, the network boundary
  IS the auth" posture (`docs/ARCHITECTURE.md`, and the same rule Gitea and
  the per-project ttyd/VS Code terminals already follow).
- Do not reopen item 43 (the DNS-race fix, same override file/mechanism).
- The fix is idempotent and self-repairing on re-run: it must correctly
  patch an existing pre-fix install (`$TAIGA_DIR` cloned before this change
  shipped) on its next `install.sh` run, not just a brand-new clone, and
  must not double-patch or corrupt the line on a third/fourth run.

## Non-goals
- Not revisiting or touching the item-43 lazy-DNS-resolver `taiga.conf`
  bind-mount or the `taiga-front` `service_healthy` depends_on gate —
  unchanged, different field of the same override mechanism.
- Not fixing item 42's broader "subprocess return codes are ignored,
  `{"ok": true}` regardless of success" pattern for `host_run`/other
  `*_run()` helpers — separate backlog item.
- Not adding an HTTP-reachability check to `taiga-up.sh`'s own success
  detection (still just the existing `docker compose ps` state check). The
  root-cause fix here removes the specific silent-failure mode item 44
  found (a live, unreachable port while state reads "running"); a broader
  "verify actual reachability, not just container state" hardening of
  `taiga-up.sh` is a separable, later decision — see Open questions.
- Not making `TAIGA_PORT` user-configurable — stays hardcoded at `9000` as
  today. The patched line still templates `${TAIGA_PORT}` (Compose-side
  substitution from `$TAIGA_DIR/.env`, not this shell) so nothing further
  is needed if it's ever made promptable later.
- Not touching Gitea, code-server, ttyd, or any other service's port
  binding — none of them share this problem (see Background).
- Not retroactively fixing an *already-running*, broken `taiga-gateway`
  container in place — like every other config change in this project
  (README "Configuration": editing `switchboard.env` + restart picks up
  the change), the fix takes effect on the next `docker compose up -d`
  (via a toggle or manually), which Compose recreates because the
  effective config changed. No migration script.

## Background / current state
`install.sh`'s `--with-taiga` block (~line 390-640) clones a pinned,
never-`git pull`'d checkout of `taigaio/taiga-docker` (`stable` branch) to
`$TAIGA_DIR=/opt/ai-dev-switchboard-taiga`, then writes a repo-owned
`docker-compose.override.yml` alongside it for two things: (item 30/43) a
`taiga-front` healthcheck + `service_healthy` depends_on gate and a
bind-mounted lazy-DNS-resolver `taiga.conf`, and (since round 6) a
loopback-only `ports: - "127.0.0.1:${TAIGA_PORT}:80"` entry for
`taiga-gateway`, added specifically because the pinned checkout's own
`docker-compose.yml` already publishes:
```
  taiga-gateway:
    image: nginx:1.19-alpine
    ports:
      - "9000:80"
```
— confirmed verbatim against the real upstream `stable` branch
(`https://raw.githubusercontent.com/taigaio/taiga-docker/stable/docker-compose.yml`,
fetched during this spec's research) — unrestricted (`0.0.0.0`), conflicting
with this project's loopback-only rule.

The `volumes:` half of this same override mechanism genuinely works — item
43's own fix relies on it (Compose merges `volumes:` entries by container
target path across `-f` files, so a single `./docker-compose.override.taiga-gateway.conf:/etc/nginx/conf.d/default.conf`
entry replaces only that one mount). `ports:` does not get the same
treatment. Per Docker's own docs
(`docs.docker.com/compose/how-tos/multiple-compose-files/merge/`, confirmed
during this spec's research): "For the multi-value options `ports`,
`expose`, `external_links`, `dns`, `dns_search`, and `tmpfs`, Compose
concatenates both sets of values" — and where Compose *does* recognize a
match between two `ports:` entries (to avoid literal duplicates), it does so
only when all of `ip`, `target`, `published`, and `protocol` are identical.
Our override's entry has `ip=127.0.0.1`; the base file's has an implicit
`ip=0.0.0.0`. Different `ip` → never recognized as a duplicate → both
entries survive into the merged config. Confirmed directly by the round-8
E2E retest (`docs/BACKLOG.md` item 44): Docker accepts the first bind
attempt and fails the second ("address already in use") without crashing
the container, so `taiga-gateway` ends up **running with no port published
at all**, invisible to `taiga-up.sh`'s only success check (`docker compose
ps taiga-gateway` state == `running`) and to `/status`. This bug has existed
since round 6 (item 30) but was masked the whole time by item 43's crash
loop — round 8 fixing item 43 is what made it observable for the first
time.

Checked for precedent before picking a direction: every other service this
project runs is either fully repo-authored (Gitea's
`config/gitea-docker-compose.yml`, whose comment explicitly notes it bakes
loopback-only directly into the file "unlike Taiga's separate
`docker-compose.override.yml` merge trick") or launched directly by
`app.py` with an explicit bind flag it fully controls (`ttyd -i 127.0.0.1`,
`code-server --bind-addr 127.0.0.1:$port`). None of them are a *pinned,
third-party* Compose file publishing to `0.0.0.0` with no config knob to
change it — Taiga is the only service in this codebase with that specific
problem, so there is no existing "we already accepted 0.0.0.0 for a
third-party image here" precedent to fall back on. `docs/ARCHITECTURE.md`
states the project's loopback rule in stronger terms than "nice to have":
"the network boundary IS the auth" (of the per-project terminals, but the
same trust model is what item 30/round 6 explicitly extended to Taiga in
the first place). Taken together, this rules out option (b) (accept
`0.0.0.0:9000` for Taiga) — it would be a real, avoidable regression
against an already-established hard rule, not a narrow case of "matching
existing precedent."

## Proposed approach
**Patch the pinned checkout's `docker-compose.yml` directly, narrowly, via
a targeted `sed`** — this reverses part of the item-30 architecture
decision (`install.sh` ~line 452-455, "not by patching
taiga.conf/docker-compose.yml inside the pinned, third-party taiga-docker
checkout itself"), and that reversal needs to be explicit and justified,
not quiet:

- That decision was made assuming the override-file idiom (a second `-f`
  file Compose auto-merges) was sufficient for *everything* the item-30
  loopback-binding and health-gating work needed to change about the
  pinned checkout. Item 43's fix proved that assumption right for
  `volumes:` (target-path merge). Item 44 proves it mechanically wrong for
  `ports:` (concatenation, no equivalent override mechanism) — this is not
  a change of philosophy, it's new evidence that one field genuinely can't
  be done the original way, confirmed by testing Compose's real merge
  behavior (see Background), not assumed.
- The reversal is scoped to exactly one line, in exactly one file
  (`docker-compose.yml`'s `taiga-gateway.ports` entry) — `taiga.conf`
  (item 43) stays entirely on the override-file/bind-mount mechanism,
  untouched by this change, because that mechanism genuinely works for it.
  Nothing else in `$TAIGA_DIR` is opened for writing by this fix.

Concretely, add a new step between the existing clone step (~line 397-400)
and the `TAIGA_PORT`/`TAIGA_ENV` setup (~line 411), run unconditionally on
every `install.sh` invocation (not gated behind `TAIGA_FRESH_CLONE` —
existing pre-fix installs must get patched on their next re-run, not just a
brand-new clone):

```bash
TAIGA_COMPOSE_YML="$TAIGA_DIR/docker-compose.yml"
if grep -q '^[[:space:]]*-[[:space:]]*"9000:80"[[:space:]]*$' "$TAIGA_COMPOSE_YML"; then
    sed -i 's|^\([[:space:]]*-[[:space:]]*\)"9000:80"[[:space:]]*$|\1"127.0.0.1:${TAIGA_PORT}:80"|' "$TAIGA_COMPOSE_YML"
elif ! grep -q '"127\.0\.0\.1:\${TAIGA_PORT}:80"' "$TAIGA_COMPOSE_YML"; then
    echo "WARNING: expected taiga-gateway's 'ports: - \"9000:80\"' line in $TAIGA_COMPOSE_YML but didn't find it (upstream taiga-docker format may have changed) -- taiga-gateway's host port may end up published on all interfaces (0.0.0.0) instead of loopback-only. Check $TAIGA_COMPOSE_YML's taiga-gateway service manually." >&2
fi
```

Notes on this shape:
- The `grep`-then-`sed` (rather than a bare unconditional `sed -i`) makes
  the step self-documenting about its own idempotency and gives it a
  concrete failure mode: if a future upstream checkout ever changes this
  line's exact text, install.sh warns loudly instead of silently leaving
  the port unpatched (the same class of "don't silently claim success"
  discipline item 42/44 themselves are about) — it does not `exit 1` or
  block installation, since the rest of the Taiga stack still installs
  fine either way and this shouldn't be a hard blocker.
- `${TAIGA_PORT}` in the replacement is single-quoted/literal — same
  reason the existing `docker-compose.override.yml` heredoc uses a
  single-quoted `<<'YAML'`: Compose (not this installer's shell) resolves
  it from `$TAIGA_DIR/.env` at `docker compose` time.
- Once patched, the line no longer matches the `grep` condition, so a
  second/third `install.sh` run is a clean no-op (verified via the
  `elif` branch actively confirming the already-patched form is present,
  not just assuming it).

**Drop `docker-compose.override.yml`'s now-redundant, actively-conflicting
`ports:` entry for `taiga-gateway`** (the two lines at ~line 597-599):
```yaml
  taiga-gateway:
    ports:
      - "127.0.0.1:${TAIGA_PORT}:80"
```
removed entirely; the `volumes:`/`depends_on:` entries in that same
service block (item 43's fix) are untouched. The base file's own patched
`ports:` line is now the single source of truth for the binding.

Update the explanatory comment above both blocks (currently item 4's
comment, ~line 434-472) to describe the new mechanism instead of the old
"one is added here" claim, and reference item 44 the same way the existing
comment block already references item 43 immediately below it.

## Affected areas
- `install.sh` — new patch step (as above), removal of the override's
  `ports:` block, updated explanatory comments. No other file in this
  repo needs a code change.
- `docs/BACKLOG.md` — mark item 44 resolved as part of this fix's commit,
  same pattern as prior rounds' commits (e.g. `edb4619` closing item 43).
- New test file, e.g. `tests/test_install_taiga_gateway_port.py`, following
  the exact extraction-and-run-standalone technique
  `tests/test_install_code_server_path.py` already establishes
  (`_extract_between()` pulls the real snippet out of `install.sh`'s own
  source, runs it as a standalone bash script against a scratch
  `$TAIGA_DIR` seeded with upstream's real `docker-compose.yml` content, no
  real Docker/network needed) — see Acceptance criteria for what it must
  cover.

## Edge cases
- **Fresh install** (`TAIGA_FRESH_CLONE=1`): patch applies immediately
  after clone, before the pre-pull step.
- **Re-run/upgrade of an existing install that predates this fix**
  (`TAIGA_FRESH_CLONE=0`, checkout still has the original unpatched
  `"9000:80"` line): must still get patched — this is why the step is not
  gated behind `TAIGA_FRESH_CLONE`.
- **Re-run of an already-patched install**: must be a clean no-op, not a
  double-patch or corruption — covered by the `grep`-gated `sed` plus a
  test asserting running the snippet twice in a row leaves the file
  byte-identical after the first run.
- **A currently-running, pre-fix `taiga-gateway` container** (already in
  the "running but nothing published" state item 44 describes) at the
  moment `install.sh` is re-run: the file-level fix does not retroactively
  fix that already-created container; it takes effect the next time
  `docker compose up -d` runs (toggle-off/on, or a manual re-run) — Compose
  recreates the container because its effective config changed. Same
  behavior as any other config change in this project already has;
  explicitly not treated as a gap needing special-casing.
- **Sed pattern mismatch** (upstream format changes in some future
  re-clone, or a user manually edited the pinned checkout): handled by the
  warn-don't-silently-noop branch above, rather than leaving `0.0.0.0`
  exposure completely unflagged.
- **Must not reopen item 43**: this fix only edits the `ports:` field on
  `taiga-gateway` in the base file; item 43's `taiga.conf` bind-mount and
  `taiga-front` health-gate remain entirely on the override-file mechanism
  (different field, same file, non-overlapping edit) — regression-checked
  in Acceptance criteria.
- **Port collision must not resurface under any install path**: fresh
  install, first re-run after upgrading past this fix, and any subsequent
  re-run must all converge on exactly one `ports:` binding for
  `taiga-gateway` in the merged config, never two.

## Acceptance criteria
- [ ] Given a fresh `--with-taiga` install, when `install.sh` completes,
      then `$TAIGA_DIR/docker-compose.yml`'s `taiga-gateway` service
      `ports:` line reads `"127.0.0.1:${TAIGA_PORT}:80"` (patched from the
      pinned checkout's original `"9000:80"`), and
      `docker-compose.override.yml`'s `taiga-gateway` block no longer has a
      `ports:` key at all.
- [ ] Given the patched files, when running `docker compose -f
      docker-compose.yml -f docker-compose.override.yml config` in
      `$TAIGA_DIR`, then the merged config shows exactly one `ports` entry
      for `taiga-gateway`, bound to `127.0.0.1:9000->80/tcp` — not two
      entries, not `0.0.0.0`.
- [ ] Given a fresh install, when `POST /taiga/on` succeeds (or a manual
      `docker compose up -d`), then `curl http://127.0.0.1:9000/` returns a
      real HTTP response (not "Failed to connect"), and `docker port` on
      the `taiga-gateway` container shows `80/tcp -> 127.0.0.1:9000`, not
      `0.0.0.0:9000` and not empty.
- [ ] Given `install.sh` is re-run against an already-existing, unpatched
      `$TAIGA_DIR` (simulating an install that predates this fix), when
      `install.sh` completes, then the same patched state above is
      reached.
- [ ] Given `install.sh` is re-run against an already-patched `$TAIGA_DIR`
      (this fix already applied on a previous run), when `install.sh`
      completes, then `docker-compose.yml`'s content is byte-identical to
      before the re-run (no double-patch, no corruption), and the warning
      branch is not triggered.
- [ ] Given `$TAIGA_DIR/docker-compose.yml` doesn't contain the expected
      `"9000:80"` line and also doesn't already contain the patched form
      (simulating an unexpected upstream format change), when `install.sh`
      runs, then it prints a loud warning to stderr rather than silently
      leaving the file untouched, and does not fail/exit the install over
      it.
- [ ] Given the same fresh-install scenario, when item 43's regression
      check is performed (repeating round 8's retest method — several
      `POST /taiga/on` trials on a fresh install), then `taiga-gateway`
      still shows zero DNS-race crashes, and the lazy-resolver `taiga.conf`
      bind-mount plus the `taiga-front` `service_healthy` depends_on gate
      are confirmed unchanged/still in effect.

## Open questions
- **Can't verify against a real, already-installed `$TAIGA_DIR` from this
  sandbox.** This spec's upstream `docker-compose.yml` content is fetched
  fresh from `taigaio/taiga-docker`'s `stable` branch HEAD today
  (2026-08-16); an existing installed host's `$TAIGA_DIR` is pinned to
  whatever commit was first cloned there and never `git pull`'d, so it's
  possible (though the file hasn't meaningfully changed in a while, per
  its content) that an older pinned checkout has a slightly different
  `ports:` line format/spacing. Proceeding on the assumption that the
  `sed`/`grep` patterns above match today's real content; the
  warn-don't-silently-noop branch is the deliberate mitigation if that
  assumption turns out wrong on a real host — flagging for the reviewer to
  confirm hands-on against whatever `$TAIGA_DIR` state its own test
  environment has, same caveat pattern round 8's spec used.
- **Should `taiga-up.sh` also gain an HTTP-reachability check**, not just
  the existing container-state check, as defense-in-depth against a
  future, different silent-failure mode? Scoped out of this fix (see
  Non-goals) since the root-cause fix removes the specific mechanism item
  44 found — flagging in case the human would rather bundle that
  hardening into this same cycle instead of leaving it as a separate,
  future backlog item.

## Risk / rollback notes
Low risk: the change is a single, narrowly-targeted, idempotent `sed` on
one line of one file, plus removing two lines from a repo-owned,
already-regenerated-every-run override file — no data loss, no schema/API
change, nothing touches a running container directly. Worst case if the
`sed` pattern doesn't match a real host's pinned checkout: the warn branch
fires, the port stays unpatched exactly as it is today (not worse than the
current state), and it's visibly flagged instead of silent. Rollback is
reverting `install.sh`'s diff (drop the patch step, restore the override's
`ports:` block) — but note that by itself re-reopens item 44, so a
rollback should be paired with reverting to accepting `0.0.0.0` exposure
consciously (option (b)) rather than silently going back to the original
broken double-bind, if this direction is ever reversed.
