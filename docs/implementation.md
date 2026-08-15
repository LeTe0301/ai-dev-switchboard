# Implementation: item 15 follow-up -- carry forward rejected CTID/hostname on retry

## Summary
Two-line fix in `ct/create.sh`'s Advanced-path CTID and hostname validation
retry loops: the `ask()` box's pre-fill now uses `"${VAR:-$DEFAULT}"` instead
of always the constant default, so a value the operator just typed and had
rejected is carried forward as the next retry's pre-fill instead of being
silently discarded and replaced with the original default every time.

## Root cause
Both loops re-assigned their own working variable (`CTID` / `CT_HOSTNAME`)
from `ask()` on every iteration, but the `ask()` call's pre-fill argument was
always the constant original default (`$(default_ctid)` /
`$DEFAULT_CT_HOSTNAME`) rather than the variable itself -- so on a re-prompt
after a validation failure, the just-rejected value the operator typed was
thrown away instead of being offered back for editing. Flagged as a
non-blocking cosmetic follow-up in part 3's `docs/test-review.md` (PR #22);
the original code matched the spec's own draft at the time, not a developer
deviation.

## Changes by file
- `ct/create.sh`:
  - CTID retry loop (`while :; do CTID=$(ask ...); ... done`, ~line 128):
    pre-fill changed from `"$(default_ctid)"` to `"${CTID:-$(default_ctid)}"`.
  - Hostname retry loop (`while :; do CT_HOSTNAME=$(ask ...); ... done`,
    ~line 141): pre-fill changed from `"$DEFAULT_CT_HOSTNAME"` to
    `"${CT_HOSTNAME:-$DEFAULT_CT_HOSTNAME}"`.

## Key decisions / tradeoffs
- `${VAR:-$DEFAULT}` is `set -u`-safe: bash's default-value expansion does
  not trigger `nounset` on an unset `VAR`, so no separate `: "${CTID:=}"`
  initialization was needed before the loop.
- As a side effect, `default_ctid()` (a live `pvesh get /cluster/nextid`
  call) now only runs once (on the loop's first iteration, while `CTID` is
  still unset) instead of on every retry -- a minor efficiency improvement,
  not a behavior change worth its own callout beyond this note.
- No other line in either loop, or anywhere else in the file, was touched --
  matches the spec's explicit non-goal that the (already-correct) ollama
  retry loop from part 1 is not in scope.

## Deviations from spec
None. Implemented exactly per `docs/spec.md`'s "Proposed approach" --
identical before/after code, same two call sites, same reasoning.

## Known limitations
None beyond what the spec itself already scoped down to a two-line, single-
file, cosmetic pre-fill fix. Not exercised against a real Proxmox host/real
`whiptail` TTY (same limitation the underlying loops already carried before
this fix); verified statically per "How to verify locally" below.

## How to verify locally
```bash
bash -n ct/create.sh
# no output = syntax OK

shellcheck ct/create.sh
# no output, exit 0 = zero-warning baseline preserved

git diff ct/create.sh
# confirms exactly the two one-line changes described above
```
Manual/interactive check (optional, requires a real Proxmox host):
start `ct/create.sh` in Advanced mode, at the CTID prompt enter an
already-in-use ID, confirm the re-prompt's pre-filled value is the
just-rejected ID (not the live `default_ctid()` value); repeat for the
hostname prompt with an invalid hostname, confirming the re-prompt pre-fills
the just-rejected hostname (not `$DEFAULT_CT_HOSTNAME`).
