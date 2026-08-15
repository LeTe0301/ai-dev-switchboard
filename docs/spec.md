# Spec: item 15 follow-up — carry forward rejected CTID/hostname on retry

## Summary
`ct/create.sh`'s Advanced-path CTID and hostname validation loops (added in
part 3, PR #22) are hard-block/loop-until-valid, but on each re-prompt after
a failure they re-fill the `ask()` box with the *original* default
(`$(default_ctid)` / `$DEFAULT_CT_HOSTNAME`) instead of the value the
operator just typed and had rejected — forcing them to retype the whole
field instead of editing it. Flagged as a non-blocking cosmetic follow-up in
part 3's `docs/test-review.md`, matching how spec's own draft code was
written (not a developer deviation).

## Orchestrator note
Mechanical, fully diagnosed fix (exact two lines, exact root cause, already
reviewer-confirmed) — no product-manager/ux-designer dispatch needed, per
this project's own "skip full triage for a fully-diagnosed, single-file
follow-up" precedent (e.g. item 20's PR #12, item 13's PR #8).

## Proposed approach
`ct/create.sh:127-138` (CTID loop) and `ct/create.sh:140-147` (hostname
loop): change the `ask()` pre-fill from the constant default to
`"${VAR:-$DEFAULT}"` so the loop's own working variable — already holding
the just-rejected value on any iteration after the first — is used as the
pre-fill once it's set, falling back to today's original default only on
the very first iteration (when the variable is still unset):

```bash
while :; do
    CTID=$(ask "Container ID (must be free):" "${CTID:-$(default_ctid)}")
    ...
done

while :; do
    CT_HOSTNAME=$(ask "Hostname:" "${CT_HOSTNAME:-$DEFAULT_CT_HOSTNAME}")
    ...
done
```
`${VAR:-...}` is `set -u`-safe (bash's default-value expansion does not
trigger `nounset` on an unset `VAR`), and as a side effect `default_ctid()`
(a live `pvesh get /cluster/nextid` call) now only runs once instead of on
every retry — a minor efficiency improvement, not a behavior change worth
calling out further.

## Non-goals
Nothing else in `ct/create.sh` changes. The ollama retry loop already
carries forward rejected values correctly (part 1) and is not touched.

## Acceptance criteria
- [ ] Given an invalid CTID is entered, when the loop re-prompts, then the
      `ask()` box's pre-filled value is the just-rejected CTID, not
      `default_ctid()`'s value.
- [ ] Given an invalid hostname is entered, when the loop re-prompts, then
      the pre-filled value is the just-rejected hostname, not
      `$DEFAULT_CT_HOSTNAME`.
- [ ] Given the very first prompt (nothing entered yet), the pre-fill is
      unchanged from today (`default_ctid()` / `$DEFAULT_CT_HOSTNAME`).
- [ ] `bash -n` / `shellcheck` clean, matching the file's existing
      zero-warning baseline.

## Risk / rollback notes
Two-line change inside an already-hard-blocked validation loop; worst case
is a cosmetic pre-fill regression, not a new failure mode. Plain revert.
