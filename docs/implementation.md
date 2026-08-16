# Implementation: E2E round 9 — item 44's fix (taiga-gateway's host port never actually gets published)

## Summary
`docker-compose.override.yml`'s loopback-only `ports:` entry for
`taiga-gateway` never actually closed the pinned `taiga-docker` checkout's
own unrestricted `ports: - "9000:80"` binding, because Compose merges list
fields like `ports:` by *concatenation*, not by replacement — the two
entries just competed for the same host port, one silently lost the bind,
and `taiga-gateway` ended up running with no port published at all. Fixed
by patching the pinned checkout's own `docker-compose.yml` `ports:` line
directly, in place, via a narrowly-scoped, idempotent, grep-gated `sed`
added to `install.sh`'s `--with-taiga` block, and dropping the
now-redundant, actively-conflicting `ports:` entry from
`docker-compose.override.yml` (the `volumes:`/`depends_on:` entries there
— item 43's mechanism — are untouched).

## Root cause
Per Docker's own docs on multi-file merge behavior: "For the multi-value
options `ports`, `expose`, `external_links`, `dns`, `dns_search`, and
`tmpfs`, Compose concatenates both sets of values," and two `ports:`
entries are only recognized as duplicates (and de-duped) when `ip`,
`target`, `published`, and `protocol` all match exactly. The override's
entry has `ip=127.0.0.1`; the base file's has an implicit `ip=0.0.0.0`.
Different `ip` → never recognized as a duplicate → both survive into the
merged config, and Docker accepts the first bind and fails the second
("address already in use") without crashing the container, leaving
`taiga-gateway` "running" with nothing published — invisible both to
`taiga-up.sh`'s only success check (`docker compose ps` state) and to
`/status`. This is mechanically different from `volumes:`, which Compose
*does* merge by container target path across `-f` files (confirmed and
relied on by item 43's own fix) — there's no equivalent "override wins"
mechanism for `ports:`.

## Changes by file
- `install.sh` (`--with-taiga` block)
  - Added a new step 3 (renumbering the block's existing numbered-comment
    steps 3-7 to 4-8), placed between the existing clone step and the
    `TAIGA_PORT`/`TAIGA_ENV` config step, run **unconditionally** on every
    `install.sh` invocation (not gated behind `TAIGA_FRESH_CLONE`, so a
    pre-fix `$TAIGA_DIR` cloned before this change shipped gets patched on
    its next re-run, not just a brand-new clone):
    ```bash
    TAIGA_COMPOSE_YML="$TAIGA_DIR/docker-compose.yml"
    if grep -q '^[[:space:]]*-[[:space:]]*"9000:80"[[:space:]]*$' "$TAIGA_COMPOSE_YML"; then
        sed -i 's|^\([[:space:]]*-[[:space:]]*\)"9000:80"[[:space:]]*$|\1"127.0.0.1:${TAIGA_PORT}:80"|' "$TAIGA_COMPOSE_YML"
    elif ! grep -q '"127\.0\.0\.1:\${TAIGA_PORT}:80"' "$TAIGA_COMPOSE_YML"; then
        echo "WARNING: expected taiga-gateway's 'ports: - \"9000:80\"' line in $TAIGA_COMPOSE_YML but didn't find it (upstream taiga-docker format may have changed) -- taiga-gateway's host port may end up published on all interfaces (0.0.0.0) instead of loopback-only. Check $TAIGA_COMPOSE_YML's taiga-gateway service manually." >&2
    fi
    ```
    matches docs/spec.md's "Proposed approach" verbatim.
  - Removed the `docker-compose.override.yml` heredoc's `taiga-gateway:`
    `ports:` block (`- "127.0.0.1:${TAIGA_PORT}:80"`) — the base file's
    own patched line is now the single source of truth. The
    `volumes:`/`depends_on:` entries in that same service block (item
    43's fix) are untouched.
  - Updated the explanatory comment block (item 30/43's) to describe the
    new mechanism instead of the old "loopback binding is added here"
    claim, and to explicitly call out step 3's `ports:` patch as a
    narrow, explicit exception to the item-30 "don't patch the pinned
    checkout" decision, referencing item 44. Removed a now-inaccurate
    sentence claiming the override heredoc's single-quoting was for
    `${TAIGA_PORT}` (that reference moved to the base file, in step 3).

- `docs/BACKLOG.md`
  - Added a "Round 9 fix" entry under item 44 documenting what was
    implemented and how it was verified, and updated the "To resume if
    this session is interrupted mid-loop" checklist to reflect the fix is
    now implemented and pending review/retest (not "not yet
    fixed/committed"). Deliberately does **not** mark item 44 "confirmed
    fixed" — per this file's own established convention (e.g. item 43's
    `edb4619` fix commit also didn't declare "confirmed fixed" at
    commit time; that language is reserved for an actual retest report),
    and because this fix's own verification is sandbox-only (see "Known
    limitations").

- `tests/test_install_taiga_gateway_port.py` (new)
  - Extracts install.sh's real step-3 patch block verbatim (same
    `_extract_between()`-style harness `tests/test_install_code_server_path.py`
    already establishes) and runs it as a standalone bash script against
    a scratch `$TAIGA_DIR/docker-compose.yml`, seeded in one test with the
    **real** upstream `taigaio/taiga-docker` `stable` branch
    `docker-compose.yml` content (fetched verbatim during this fix's
    development — see "How to verify locally" for how this was confirmed
    against the live file, not guessed at).
  - `test_fresh_unpatched_checkout_gets_port_patched_to_loopback_only` —
    AC1 (fresh install / first patch).
  - `test_only_the_ports_line_changes_rest_of_file_untouched` — asserts
    exactly one line differs before/after, confirming the "narrow, one
    line, one file" scope claim mechanically, not just by inspection.
  - `test_rerun_against_preexisting_unpatched_install_still_gets_patched`
    — AC4 (pre-fix install re-run).
  - `test_rerun_against_already_patched_checkout_is_byte_identical_noop` —
    AC5 (idempotency: 2nd and 3rd runs are a clean no-op, byte-identical,
    no warning).
  - `test_unexpected_format_warns_loudly_and_leaves_file_untouched` — AC6
    (sed pattern mismatch warns to stderr, doesn't fail, file untouched).
  - `test_already_patched_form_present_from_the_start_is_a_clean_noop` —
    the `elif` branch's own recognized-already-patched path, distinct
    from the mismatch-warning path.

## Key decisions / tradeoffs
- Followed docs/spec.md's proposed `sed`/`grep` patterns and code
  placement verbatim, per the spec's own explicit instruction that this
  decision was already made and shouldn't be re-derived.
- Renumbered the `--with-taiga` block's existing numbered-comment steps
  (3→4, 4→5, 5→6, 6→7, 7→8) rather than inserting an unnumbered step,
  matching this file's own established "numbered narrative comment"
  convention for this block (and the parallel `--with-git-hosting` block)
  — a larger diff than strictly minimal, but every changed line is a
  comment-only renumber, not a logic change.
- Fetched the real upstream `taigaio/taiga-docker` `stable` branch
  `docker-compose.yml` (network access was available in this sandbox,
  unlike round 8's) and confirmed the `sed`/`grep` patterns match it
  byte-for-byte (`sed -n '144,147p'` → `ports:\n      - "9000:80"`),
  rather than relying solely on docs/spec.md's own already-fetched quote.
  Embedded that same verbatim content as the new test file's fixture, and
  verified programmatically (diffed against the freshly-fetched copy)
  that the two are identical.

## Deviations from spec
None substantive. Two small additions beyond the spec's literal text,
both within its stated intent:
- The spec's "Affected areas" left the new test file's exact contents
  unspecified beyond "following the exact extraction-and-run-standalone
  technique... see Acceptance criteria for what it must cover" — wrote 6
  tests mapping onto the spec's acceptance criteria (see "Changes by
  file" above) rather than a smaller set.
- Renumbered the surrounding comment-only "step N." labels in
  `install.sh`'s `--with-taiga` block (not mentioned in the spec, which
  only specified the new step's placement and content) — a stylistic
  consistency choice, not a behavior change; flagging in case the
  reviewer would rather see a smaller diff that leaves stale numbering in
  place.

## Known limitations
- **No live `$TAIGA_DIR`/Taiga install available in this sandbox** (same
  constraint round 8's developer had) — the mechanical file-patching and
  its idempotency are covered by the new automated test, and the merged
  `docker compose config` result was confirmed via a synthetic
  reconstruction (see "How to verify locally") using the real fetched
  upstream file plus a real installed Docker Compose v5.4.0, not just
  read from documentation. What was **not** done: an actual `install.sh
  --with-taiga` run, an actual `POST /taiga/on`, an actual `curl
  http://127.0.0.1:9000/`, or an actual `docker port` check against a
  running `taiga-gateway` container (AC3, and the item-43 regression
  check in AC7) — those need the pve peer's hands-on retest, per
  docs/spec.md's own "Open questions." Flagging explicitly for the
  reviewer to confirm hands-on, same caveat pattern round 8's spec/
  implementation used.
- Per docs/spec.md's own "Open questions": an already-installed host's
  `$TAIGA_DIR` is pinned to whatever commit was first cloned there and
  never `git pull`'d, so it's possible (though unlikely, per the file's
  content not having meaningfully changed) that an older pinned checkout
  has a slightly different `ports:` line format/spacing than the
  `stable`-branch-HEAD content fetched and tested here. The
  warn-don't-silently-noop branch is the deliberate mitigation if that
  assumption turns out wrong on a real host; verified via a dedicated
  test (`test_unexpected_format_warns_loudly_and_leaves_file_untouched`)
  that the warning path actually fires and doesn't corrupt the file.
- Did not add an HTTP-reachability check to `taiga-up.sh`'s own success
  detection (docs/spec.md Non-goals / Open question #2) — out of scope
  for this fix.

## How to verify locally
```bash
cd /home/dev/projects/ai-dev-switchboard

# Syntax check
bash -n install.sh

# New test file (item 44's own coverage)
python3 tests/test_install_taiga_gateway_port.py -v

# Full existing test suite (confirms no regressions elsewhere -- 3
# pre-existing, unrelated failures in tests/test_teams_grounding.py are
# environment-specific: this sandbox has a real, gitignored CLAUDE.md at
# the repo root, which those grounding-discovery tests don't expect;
# reproduced identically on a clean `git stash`, so not caused by this
# change)
python3 -m unittest discover -s tests

# Confirm the sed/grep patterns match the real upstream file (requires
# network):
curl -fsSL https://raw.githubusercontent.com/taigaio/taiga-docker/stable/docker-compose.yml \
    | sed -n '/taiga-gateway:/,/depends_on:/p'
# -> should show "ports:\n      - \"9000:80\"" verbatim

# Confirm the merged docker compose config has exactly one ports: entry,
# bound to 127.0.0.1:9000 (synthetic reconstruction, no real Docker
# install/network needed beyond the docker CLI itself):
SCRATCH=$(mktemp -d)
curl -fsSL https://raw.githubusercontent.com/taigaio/taiga-docker/stable/docker-compose.yml \
    -o "$SCRATCH/docker-compose.yml"
printf 'TAIGA_PORT=9000\n' > "$SCRATCH/.env"
touch "$SCRATCH/docker-compose.override.taiga-gateway.conf"
# Run install.sh's real step-3 patch against $SCRATCH/docker-compose.yml
# (extract lines between "# 3. Item 44 (round 9):" and "# 4. Config —"),
# then write docker-compose.override.yml with just the volumes:/
# depends_on: block (no ports:), then:
( cd "$SCRATCH" && docker compose -f docker-compose.yml -f docker-compose.override.yml config ) \
    | sed -n '/^  taiga-gateway:/,/^  [a-z]/p'
# -> exactly one `ports:` entry, host_ip: 127.0.0.1, published: "9000"
```
