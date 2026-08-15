# Implementation: ct/create.sh fixes from Proxmox E2E test round 3 (items 31, 32)

## Summary
Two independent fixes to `ct/create.sh` from `docs/spec.md`, both already
fully diagnosed with exact before/after code by the E2E tester: (1) raised
`DEFAULT_DISK_GB` from `"8"` to `"20"`, since 8G fills the container's root
disk completely once all four optional features (Gitea, Taiga, code-server,
Ollama) are enabled, surfacing as an opaque Postgres `FATAL: could not
write init file` rather than an out-of-space message; (2) added a `case`
filter to `_enumerate_bridges()` that excludes Proxmox's own
auto-created `fwbrNNNiM` per-container firewall bridges (e.g. `fwbr101i0`)
from the live bridge-selection menu, so an operator can no longer
accidentally pick one instead of a real uplink bridge like `vmbr0`.

## Root cause
Not applicable in the "bugfix root cause" sense for fix 1 (a static default
value being too small, not a logic defect). Fix 2's root cause: `ip -o link
show type bridge` lists *every* kernel bridge device, and Proxmox's
per-container firewalling creates one `fwbrNNNiM` bridge per running
container with firewalling enabled — `_enumerate_bridges()` previously had
no filter distinguishing those from real switch/uplink bridges, so they
appeared in the menu indistinguishably from `vmbr0`.

## Changes by file
- `ct/create.sh:91` — `DEFAULT_DISK_GB="8"` → `DEFAULT_DISK_GB="20"`.
  Used by both the Default path (final container config, no prompt) and as
  the pre-filled default on the Advanced path's disk-size prompt (still
  fully editable there either way).
- `ct/create.sh:64-79` (`_enumerate_bridges()`) — added, inside the
  kernel-bridge `while` loop, immediately after the existing
  `[ -n "$_br" ] || continue` empty-line guard:
  ```bash
  case "$_br" in
      fwbr[0-9]*i[0-9]*) continue ;;  # item 32: Proxmox's own per-container firewall bridge, not a real uplink
  esac
  ```
  before the `BRIDGE_MENU_OPTS+=(...)` line, matching the spec's exact
  before/after code and the file's existing `case`/glob idiom used
  elsewhere (e.g. the Advanced path's ollama model-check branch).
- `tests/test_create_enumerate_bridges.py` (new) — standalone test harness
  covering item 32's filter (see "How to verify locally" below for what it
  proves and how).

## Key decisions / tradeoffs
- Applied both fixes exactly as specified in `docs/spec.md` (exact
  before/after code, exact line ranges) — no independent design choices
  needed for either fix.
- For the new test harness, followed the precedent already established by
  `tests/test_install_set_env.py` (extract the real function verbatim out
  of the shipped script's source via a small `_extract_between()` helper,
  then execute it in a scratch bash subprocess) rather than reimplementing
  `_enumerate_bridges()`'s filter logic in Python — this way the tests
  exercise `create.sh`'s actual filter, not a copy that could silently
  drift from the real file.
- Stubbed the `ip` command as a shell *function* (bash functions take
  priority over `PATH` executables) rather than mocking via a fake
  `PATH` entry or a wrapper script — simpler, no extra temp files, and it's
  the same "monkeypatch the one non-pure external dependency" technique
  the task asked to try before concluding anything is untestable.
- The harness only feeds the kernel-bridge branch (`ip -o link show type
  bridge`); it does not exercise the separate SDN-vnet branch
  (`/etc/pve/sdn/vnets.cfg`), since that branch is untouched by this fix
  and outside item 32's acceptance criterion.
- Added two extra cases beyond the spec's literal acceptance criterion
  (empty-menu-when-only-fwbr-present, and near-miss names like
  `fwbridge0`/`vmbr-media0` that must *not* be excluded) to positively
  confirm the glob is neither too broad nor too narrow, per the task's own
  emphasis that this is "the one part of this round with real logic to get
  right."

## Deviations from spec
None. Both fixes match `docs/spec.md`'s exact before/after code and line
locations verbatim.

## Known limitations
- Both fixes are inherently only fully exercisable on a real Proxmox VE
  host with `pct`/`whiptail`/actual kernel bridges present — this repo's
  test suite has no such environment, consistent with prior E2E-fix
  rounds (see `docs/implementation.md` history for items 22-27, 28/29/33).
  Fix 1 is a one-line constant change verified by direct `grep`. Fix 2's
  actual filter *logic* (the part with real risk of a too-broad/too-narrow
  pattern) is verified by the new `tests/test_create_enumerate_bridges.py`
  harness, which runs the real extracted function against representative
  `ip -o link show type bridge` output — this was not "not testable"; the
  cheapest working approach (stub the one external command, run the real
  function) worked on the first attempt.

## How to verify locally
```bash
# Fix 1 — acceptance criterion from docs/spec.md:
grep 'DEFAULT_DISK_GB=' ct/create.sh
# -> DEFAULT_DISK_GB="20"

# Both fixes — syntax + lint, zero-warning baseline preserved:
bash -n ct/create.sh
shellcheck ct/create.sh

# Fix 2 — standalone filter harness (extracts the real _enumerate_bridges()
# out of ct/create.sh, stubs `ip` with canned `ip -o link show type
# bridge` output including both vmbr0/vmbr1 and fwbr101i0/fwbr106i0/
# fwbr107i0, and asserts BRIDGE_MENU_OPTS keeps the real bridges and drops
# exactly the fwbrNNNiM-pattern ones — plus empty-only-fwbr,
# no-fwbr-regression, near-miss-name, and "@"-suffix edge cases):
python3 tests/test_create_enumerate_bridges.py -v

# Full existing suite — no regressions:
python3 -m unittest discover -s tests
# -> Ran 1205 tests ... OK
```

All four commands above were run during implementation:
`bash -n` passed, `shellcheck` reported zero warnings, the new 5-test
harness passed on its own, and the full suite (including those 5 new
tests) passed: `Ran 1205 tests ... OK`, no failures or errors.
