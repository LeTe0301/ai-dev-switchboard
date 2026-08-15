# Test & Review: ct/create.sh fixes from Proxmox E2E test round 3 (items 31, 32)

## Scope
Two independent fixes to `ct/create.sh` from `docs/spec.md`: (1) item 31,
`DEFAULT_DISK_GB` raised `"8"` → `"20"`; (2) item 32, a `case` filter added
to `_enumerate_bridges()`'s kernel-bridge loop to exclude Proxmox's own
auto-created `fwbrNNNiM` per-container firewall bridges from the live
bridge-selection menu. Verified against the actual working-tree diff
(`git diff HEAD -- ct/create.sh`), not developer self-report.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Item 31: `DEFAULT_DISK_GB` is exactly `"20"`, nothing else in the constant block changed | `grep -n 'DEFAULT_DISK_GB=' ct/create.sh`; `git diff HEAD -- ct/create.sh` read in full | pass | Line 91: `DEFAULT_DISK_GB="20"`. Full diff shows only two hunks total in the file: the item-32 filter (lines 65-72) and this one-line constant change (line 91) — no other lines touched |
| 2 | Item 31: Advanced path's disk-size prompt still uses this as pre-filled default, still fully editable (no behavior change beyond the value) | Read `ct/create.sh` around the Advanced disk-size prompt; confirmed only the constant's assignment changed, the prompt call site is untouched | pass | No diff hunk anywhere near the prompt call site itself; only the constant declaration changed |
| 3 | Item 32: real `fwbrNNNiM` examples from the report (`fwbr101i0`, `fwbr106i0`, `fwbr107i0`) are excluded, `vmbr0`/`vmbr1` survive | Automated: `python3 tests/test_create_enumerate_bridges.py -v` (extracts the real `_enumerate_bridges()` out of `ct/create.sh`, stubs `ip`, runs it for real) | pass | `test_real_bridges_kept_firewall_bridges_excluded` — `ok` |
| 4 | Item 32: host with only `fwbrNNNiM` interfaces yields an empty menu (no crash, no stray entries) | Automated, same harness | pass | `test_only_firewall_bridges_yields_empty_menu` — `ok` |
| 5 | Item 32: host with no `fwbrNNNiM` interfaces at all is unaffected (regression) | Automated, same harness | pass | `test_no_firewall_bridges_present_regression` — `ok` |
| 6 | Item 32: near-miss names `fwbridge0` (starts with "fwbr" but no digit immediately after) and `vmbr-media0` (contains "i0" but doesn't start with "fwbr") survive the filter | Automated, same harness | pass | `test_pattern_not_too_broad_similar_looking_names_survive` — `ok` |
| 7 | Item 32: a firewall bridge with an `@if12`-style VLAN suffix (stripped upstream by the existing `cut -d'@' -f1`) is still excluded | Automated, same harness | pass | `test_firewall_bridge_with_at_suffix_still_excluded` — `ok` |
| 8 | Item 32: independent verification of the actual bash glob-match semantics of `fwbr[0-9]*i[0-9]*` (not trusting the developer's test suite or the code comment's paraphrase) against a wider adversarial name list | Manual/automated: wrote a standalone script exercising the exact `case`/glob pattern from the diff directly in `bash` against 27 constructed names (`vmbr0`, `vmbr1`, `vmbr-media0`, real `fwbrNNNiM` examples incl. multi-digit and single-digit forms, `fwbridge0`, `fwbr123index5`, `fwbri0`, `fwbrlan0`, `fwbr0-lan`, `fwbr0vlan15`, `fwbr10internal5`, `FWBR1I2`, `xfwbr1i2`, plus trailing-garbage forms `fwbr1i2x`/`fwbr1i2extra`/`fwbr1abci2`) | pass (see Findings for one non-blocking observation) | Full transcript below. All real Proxmox-shaped names matched; all realistic operator/near-miss names did not match; see finding #1 for the one edge case (contrived trailing garbage) that does match but has no real-world naming collision |
| 9 | `bash -n ct/create.sh` (syntax) | Ran directly | pass | No output, exit 0 |
| 10 | `shellcheck ct/create.sh` (lint) | Ran directly | pass | Exit 0, zero warnings |
| 11 | Full existing suite (regression) | `python3 -m unittest discover -s tests`, run directly, full run to completion (not truncated) | pass | `Ran 1205 tests in 161.024s` / `OK`, exit code 0 |

### Case 8 transcript (bash, real `case`/glob evaluation of the literal pattern from the diff)
```
$ pattern: fwbr[0-9]*i[0-9]*
no-match: vmbr0
no-match: vmbr1
no-match: vmbr-media0
MATCH   : fwbr101i0
MATCH   : fwbr106i0
MATCH   : fwbr107i0
MATCH   : fwbr0i0
MATCH   : fwbr1000i5
no-match: fwbridge0
no-match: fwbr123index5
no-match: fwbr1i
no-match: fwbriggle
no-match: fwbr
no-match: fwbri0
MATCH   : fwbr9999999i9999999
no-match: xfwbr1i2
MATCH   : fwbr1i2x        <- see finding #1
no-match: FWBR1I2
MATCH   : fwbr1i2extra    <- see finding #1
no-match: fwbr1iX
no-match: fwbr10internal5
no-match: fwbrlan0
no-match: fwbr0-lan
no-match: fwbr0vlan15
MATCH   : fwbr1abci2      <- see finding #1
no-match: fwbr1i
no-match: fwbr0i
```
Reasoning for the specific case the task flagged (`fwbridge0`): bash `case`
glob semantics, not regex. After the literal `fwbr` prefix, the string
remaining is `idge0`. `[0-9]` requires *exactly one* digit character next —
the very next character is `i`, not a digit, so this branch fails
immediately; there is no other position in the string where `[0-9]`
(prefix digit) through `i` (literal) through `[0-9]` (suffix digit) can
all be satisfied while the whole pattern consumes the whole string. So
`fwbridge0` correctly does **not** match — the specific false-positive
trap the task asked me to trace through does not occur.

## Regression check
Full suite run directly by me to completion: `python3 -m unittest discover
-s tests` → `Ran 1205 tests in 161.024s` / `OK`, exit code 0. Matches the
developer's reported count exactly. No regressions.

## Defects found
None (testing pass is clean; proceeding to review pass).

---

## Spec coverage
- **Item 31** acceptance criterion (`grep 'DEFAULT_DISK_GB=' ct/create.sh`
  shows `"20"`, no other behavior change) — implemented and tested,
  case 1-2. The working-tree diff confirms this is the *only* line touched
  besides the item-32 filter — no scope creep.
- **Item 32** acceptance criterion (`_enumerate_bridges()`'s
  `BRIDGE_MENU_OPTS` contains `vmbr0` but none of the `fwbrNNNiM` entries,
  given both present) — implemented and tested by the developer's 5-test
  harness (cases 3-7) plus my own independent 27-name adversarial glob
  trace directly against the real `case` pattern (case 8), run for real in
  `bash`, not inferred. Both the developer's near-miss cases and mine agree
  the pattern is neither too narrow (misses no real `fwbrNNNiM` shape,
  including multi-digit and single-digit CTID/netid) nor too broad against
  any *realistic* bridge name (`vmbr0`, `vmbr-media0`, `fwbridge0`,
  `fwbrlan0`, `fwbr0-lan`, etc. all correctly survive).

## Findings (most severe first)

### 1. `case` pattern is looser than its own comment claims — matches arbitrary trailing/embedded characters, not just digits — should-fix
- File: `ct/create.sh:70`
- Issue: the inline comment says the pattern is Proxmox's "fixed
  `fwbrNNNiM` naming convention (always `fwbr` + digits + `i` + digits)",
  and `docs/spec.md` describes it the same way. That's not quite what
  `fwbr[0-9]*i[0-9]*` actually matches in bash glob semantics: `[0-9]` is
  a single-character class (exactly one digit), and each `*` that follows
  it is an independent wildcard matching **any characters at all**, not a
  digit-repeat quantifier (glob has no `+`/`{n,}`-style repetition; that
  would need `extglob`'s `+([0-9])`). So the real matched shape is `fwbr`
  + exactly one digit + *anything* + `i` + exactly one digit + *anything*.
  Concretely: `fwbr1i2extra`, `fwbr1i2x`, and `fwbr1abci2` all match the
  filter today, even though none of them is an actual
  `fwbr<digits>i<digits>`-only Proxmox bridge name (verified directly in
  `bash`, case 8 above).
- Failure scenario: this is not a false negative (every real
  Proxmox-generated `fwbrNNNiM` name, including edge cases like
  single-digit or 7-digit CTID/netid, still matches and gets excluded
  correctly — confirmed). It's a theoretical false positive: a
  hypothetical kernel bridge whose name happens to be `fwbr<digit>` +
  arbitrary characters + `i<digit>` + arbitrary trailing characters (e.g.
  an operator naming a real uplink bridge `fwbr1i2-uplink`) would be
  silently hidden from the menu by this filter even though it isn't a
  Proxmox firewall bridge. In practice this is very low risk: `fwbr` is
  Proxmox's own reserved prefix for auto-generated firewall bridges, no
  operator is likely to hand-name a real uplink bridge that way, and none
  of the acceptance criterion's real-world examples (`vmbr0`, `vmbr1`,
  operator-named bridges) are anywhere near this shape. Does not violate
  `docs/spec.md`'s literal acceptance criterion and does not warrant
  blocking this round, but the comment/spec description of the pattern's
  behavior is inaccurate and worth tightening in a follow-up (either fix
  the comment to describe what the pattern actually does, or tighten the
  pattern itself, e.g. with `extglob`'s `+([0-9])i+([0-9])` or an
  explicit `[[ "$_br" =~ ^fwbr[0-9]+i[0-9]+$ ]]` regex check, so the code
  matches its own stated intent).

## Follow-ups (non-blocking)
- Consider the tightened pattern from finding #1 in a future small fix, or
  at minimum correct the inline comment/spec wording so it accurately
  describes "starts with `fwbr`+digit, contains `i`+digit somewhere after,
  arbitrary characters otherwise" rather than "always digits only" — since
  this file already reaches for `[[ =~ ]]` elsewhere it would be a small,
  low-risk follow-up, not urgent.
- (Carried over from `docs/spec.md`'s own non-goal, unchanged by this
  round): `taiga-up.sh`/`gitea-up.sh` proactively `df`-checking free space
  before `docker compose up`, and sizing the storage-pool step's suggested
  disk default off live free space, remain real, separately-scoped
  follow-ups.

## Overall verdict
Approve.
