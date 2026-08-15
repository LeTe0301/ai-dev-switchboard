# Test & Review: BACKLOG item 15 part 3, pieces 2-4 -- live storage/bridge enumeration + CTID/hostname hard-block validation

## Scope
`ct/create.sh`'s Advanced branch: `_enumerate_storage()`, `_enumerate_bridges()`,
`_valid_hostname()`, the storage/bridge whiptail-menu-or-fallback blocks, and
the CTID/hostname loop-until-valid hard-block retry loops. Ties to all 11
acceptance criteria in `docs/spec.md`. Same no-CI-harness constraint as parts
1-2 (whiptail TUI, real Proxmox-only commands `pvesm`/`pct`/`ip`) — verified
by rebuilding an independent standalone test harness (own file, written this
session, not reused from the developer's), plus direct diff/code reading and
live `numfmt`/`bash -e` semantics checks.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Storage: 2+ active pools -> whiptail --menu with type+free-space rows, selection -> STORAGE | Automated (mocked `pvesm`) + code read | pass | Harness "`_enumerate_storage`" section: 4-elem array, correct tags `local`/`local-lvm`, desc `dir, 79GiB free`; menu-assignment code read directly (`STORAGE=$(whiptail --menu ...)`, standard pattern already used elsewhere in file) |
| 2 | Storage: 0 active rows -> fallback to original free-text `ask()` w/ `DEFAULT_STORAGE` | Automated (mocked `pvesm`, all-unknown-status input) | pass | Harness: zero-active-rows input -> `STORAGE_MENU_OPTS` length 0, confirming the `-eq 0` fallback trigger condition in `ct/create.sh:264` |
| 3 | Storage: `unknown`-status row excluded from menu | Automated | pass | Harness: 3-row mock (2 active + 1 `unknown`) -> array length 4 (2 pools), `nfsbroken` absent |
| 4 | Bridge: 1+ kernel bridges/vnets -> whiptail --menu, `sdn:` prefix stripped after selection -> BRIDGE | Automated (mocked `ip`) + code read | pass | Harness: `@vmbr0`-suffix stripped correctly (`vmbr1@vmbr0` -> `vmbr1`); `sdn:guest` -> `guest` via `BRIDGE="${BRIDGE#sdn:}"` logic exercised directly; SDN awk one-liner exercised against a sample `vnets.cfg`, extracts both vnet names correctly |
| 5 | Bridge: 0 found -> fallback to original `ask()` w/ `DEFAULT_BRIDGE` | Automated (mocked `ip` returning nothing, no `vnets.cfg`) | pass | Harness: `BRIDGE_MENU_OPTS` length 0 |
| 6 | CTID non-numeric/out-of-range -> msgbox + re-prompt, no proceed/abort | Automated (full loop simulation with scripted `ask()`/`pct` sequence) + adversarial `set -e` check | pass | Harness "Full loop-until-valid simulation": `abc` -> range-fail msgbox, loop continues (not aborted); range-boundary unit checks 99/100/999999999/1000000000/`abc`/`12ab`/empty all correct |
| 7 | CTID already in use -> msgbox + re-prompt (distinct from range error) | Automated (mocked `pct status`) | pass | Harness: CTID `150` (mocked taken) -> collision path taken, second msgbox shown, loop continues; final result after 3 scripted attempts (`abc`, `150`, `151`) is `CTID=151` after exactly 2 msgboxes |
| 8 | CTID valid+unused -> proceeds immediately to hostname, no extra prompt | Code read | pass | `break` exits the CTID `while` loop; next line in the file begins the hostname `while` loop — sequential, no gate in between |
| 9 | Hostname RFC1123 violation -> msgbox + re-prompt | Automated (`_valid_hostname` sourced verbatim) | pass | Harness "`_valid_hostname`": 11/11 cases (leading/trailing hyphen, underscore, space, empty label between dots, empty string, 64-char label all correctly rejected; plain/FQDN/uppercase/63-char-label/253-char-total all correctly accepted) |
| 10 | Hostname valid -> proceeds immediately to storage, no extra prompt | Code read | pass | `break` exits the hostname `while` loop; next statement is `_enumerate_storage` — sequential, no gate in between |
| 11 | Default path completely untouched — no menu/validation ever shown, byte-for-byte unchanged | `git diff` inspection | pass | `git diff -- ct/create.sh`'s single non-helper hunk starts exactly at the `else` line; the `if [ "$INSTALL_MODE" = "default" ]; then ... else` body above it is unchanged context, confirmed no lines inside the Default branch appear in either side of the diff |
| 12 | `shellcheck ct/create.sh` passes with no new warnings | Automated, rerun independently | pass | `shellcheck ct/create.sh` -> exit 0, zero warnings/errors; `git diff -- ct/create.sh \| grep shellcheck` -> no new `# shellcheck disable` comments added by this cycle (the one pre-existing `SC2086` disable at line 355 predates this diff, for `$INSTALL_FLAGS` word-splitting, unrelated) |
| 13 | `bash -n` syntax check | Automated, rerun independently | pass | `bash -n ct/create.sh` -> exit 0, no output |
| 14 (edge) | Exactly one storage pool / one bridge found -> still shown as a one-row menu, not auto-selected | Code read | pass | Only two branches exist (`-eq 0` vs. else); no `-eq 2`/single-item special case anywhere in `_enumerate_storage`/`_enumerate_bridges` or the two call sites |
| 15 (adversarial) | Does `pct status "$CTID"` returning nonzero (CTID genuinely free) trip `set -euo pipefail` and abort the script? | Automated, live bash execution | pass (does NOT abort) | `bash -c 'set -euo pipefail; pct() { return 1; }; if pct status 999 >/dev/null 2>&1; then echo TAKEN; else echo FREE; fi; echo "SCRIPT DID NOT ABORT"'` -> printed `FREE` then `SCRIPT DID NOT ABORT`, confirming a command used directly as an `if` condition is exempt from `errexit`, exactly as `ct/create.sh`'s `if pct status "$CTID" >/dev/null 2>&1; then` relies on |
| 16 (spot-check) | `numfmt --to=iec-i` (used) vs. `--to=iec` (spec's draft) actually produce `GiB` vs. `GB` on the same input | Automated, live `numfmt` execution | pass, confirms developer's claim | `numfmt --to=iec --suffix=B $((380526592*1024))` -> `363GB`; `numfmt --to=iec-i --suffix=B $((380526592*1024))` -> `363GiB` — matches `docs/design.md`'s documented `GiB`-suffixed example output; spec's own draft code (`--to=iec`) would have produced `GB`, not `GiB` |

Harness source (kept for reference, not committed to the repo):
`/tmp/claude-1001/-home-dev-projects-ai-dev-switchboard/29cdfee9-e6b1-430f-b12d-0b9f49085e51/scratchpad/test_harness.sh`,
run as `bash test_harness.sh` -> `=== Summary: 46 passed, 0 failed ===`. This
is an independently-written harness (not the developer's own 33-assertion
one), sourcing the three helper functions verbatim from the current
`ct/create.sh` (`sed -n '32,75p'`) and additionally simulating the full
CTID retry-loop control flow end to end (the developer's harness tested the
range/collision *conditions* in isolation but not the full loop's
iteration/termination behavior; this pass adds that, plus the `set -e`
adversarial check and the live `numfmt` spot-check, neither of which
appeared in the developer's own verification).

## Regression check
No Python/JS files changed in this diff (`git diff --stat`: only
`ct/create.sh` + docs). `ct/create.sh` has no existing automated test
coverage anywhere in `tests/` (confirmed: `grep -rl "create.sh" tests/` and
`find . -iname '*create*test*'` both empty), so there is no pre-existing
suite for this file to regress. No other file in the repo references
`ct/create.sh`'s contents. Full existing Python suite was not re-run since
nothing outside `ct/create.sh` and docs changed — zero risk surface for it.

## Defects found
None — testing pass is clean, proceeding to review.

---

## Spec coverage
All 11 acceptance criteria in `docs/spec.md` map to an implemented code path
and a test case above (test cases 1-13 map 1:1 to the 11 ACs, with 3 and the
one-row-menu edge case as additional coverage beyond the literal AC list).
No acceptance criterion is unimplemented or untested. Both spec Non-goals
(`TEMPLATE_STORAGE` enumeration, storage-space validation) are correctly
left unbuilt — confirmed by reading the current file: `TEMPLATE_STORAGE`
prompt (`ct/create.sh`, still free-text `ask()`) and `DISK_GB` prompt are
both untouched by this diff.

## Findings (most severe first)

### 1. `numfmt --to=iec-i` deviation — verified correct, not a defect
- File: `ct/create.sh:56`
- The developer's claimed deviation (swapping spec's `--to=iec` for
  `--to=iec-i` to match `docs/design.md`'s documented `GiB` output) is
  independently confirmed correct by live execution (test case 16 above).
  Not a finding against the implementation — flagged here only to record
  that it was actually checked, not rubber-stamped.

### 2. CTID/hostname retry loops reset to the *original* default on re-prompt, never carry forward the rejected value — should-fix (spec-level, non-blocking)
- File: `ct/create.sh` CTID loop (`CTID=$(ask "..." "$(default_ctid)")`) and
  hostname loop (`CT_HOSTNAME=$(ask "..." "$DEFAULT_CT_HOSTNAME")`)
- Both loops re-derive the *original* suggested default fresh on every
  iteration, rather than pre-filling the operator's last (rejected) entry
  as the new default the way the existing ollama endpoint retry loop does
  (`_ollama_url_default="$_ollama_url_input"` on retry, `ct/create.sh`'s
  ollama block). Concrete scenario: operator types CTID `150`, told it's
  taken, re-shown the prompt with the *original* `default_ctid()` suggestion
  (e.g. `900`) rather than `150` — they lose their own last attempt and
  have to remember/retype it if they want to try `151` instead of just
  editing `150`.
- This is implemented *exactly* as `docs/spec.md`'s own "Proposed approach"
  code specifies (verbatim, no developer deviation) — verified by comparing
  the diff to spec.md's Piece 4 code block line by line. `docs/design.md`
  doesn't contradict it either (its state-machine tables describe "same
  `ask()` prompt re-shown" without specifying which value the default
  reverts to). This is a latent inconsistency between spec's own prose
  ("same interaction shape as the existing ollama endpoint retry loop,"
  which *does* carry forward) and spec's own literal code (which doesn't) —
  a spec-authoring gap, not an implementation bug. Does not fail any
  acceptance criterion as written (the ACs only require the msgbox +
  re-prompt + no-abort behavior, which is present). Recommend a small
  follow-up spec/cycle to carry the rejected value forward as the new
  default, matching the ollama loop's own established UX, but not a reason
  to withhold approval of this cycle.

### 3. `_valid_hostname()`'s inner `IFS` toggle is dead code — nit
- File: `ct/create.sh:32-44`
- `for _label in $_h; do ... done`'s word-splitting of `$_h` happens once,
  at the point the `for` word-list is evaluated (before the loop body ever
  runs) — bash does not re-split on each iteration. The `IFS=$_old_ifs` /
  `IFS='.'` toggle inside the loop body therefore has no effect on which
  labels are produced; it's inert. Confirmed correct behavior regardless
  (11/11 harness assertions pass, including the empty-label-between-dots
  and multi-label-FQDN cases), so this is purely a code-clarity nit, not a
  correctness issue — safe to leave as is.

### 4. Storage/bridge menu box height (`listheight + 9`) is tighter than this file's other whiptail menus — nit
- File: `ct/create.sh` (storage/bridge menu blocks)
- The file's pre-existing `menu()` helper uses `15 74 3` (+12 over visible
  rows) and the optional-feature checklist uses `18 78 4` (+14); the new
  dynamic storage/bridge menus use `listheight + 9`. This was `docs/spec.md`
  Open question #2, explicitly left for `ux-designer` to "confirm or adjust
  the exact sizing constants" — `docs/design.md` did not supply a different
  number, so the developer correctly treated it as confirmed-as-is rather
  than silently changing it. Purely cosmetic (a few less lines of vertical
  padding around the title/buttons on a real TTY) and not testable without
  a real terminal; not blocking, flagged only as a possible follow-up if a
  real-host run turns out to look cramped.

## Follow-ups (non-blocking)
- Consider carrying the rejected CTID/hostname value forward as the new
  default on retry, matching the ollama loop's established pattern
  (Finding 2).
- Consider widening the storage/bridge menu box height to match the file's
  other menus' padding if a real-host run looks visually cramped
  (Finding 4).
- `docs/BACKLOG.md` item 15's "Open for the future session" note (both of
  its open questions: whether to ship steps 2-3/5 in the same pass, and
  hard-block-vs-warn validation) was stale — both questions are answered
  by this cycle's own work but the note wasn't updated to say so. Corrected
  directly in this review pass (struck through with a "Resolved" note,
  matching how the shipped-status line above it already documents this
  cycle) rather than looping back to the developer for a docs-only fix.

## BACKLOG.md "shipped in full" claim — confirmed, with one doc fix applied
Read `docs/BACKLOG.md`'s full item 15 entry end to end (original research,
all six "Shape of the work" pieces, the settled checklist-scope decision,
and the "Open for the future session" note). Cross-checked against all
three cycles:
- Piece 5 (optional-feature checklist) — shipped part 1, confirmed present
  in the current file (`FEATURES=$(whiptail --title ... --checklist ...)`).
- Piece 1 (Default/Advanced entry fork) — shipped part 2, confirmed present
  (`INSTALL_MODE=$(whiptail --title ... --menu ...)` fork).
- Pieces 2-4 (storage enumeration, bridge enumeration, CTID/hostname
  validation) — shipped this part 3, verified above.
- Piece 6 ("explicitly out of scope") is a standing non-goal, not a piece
  that needed building.
- Both entries in item 15's own "Open for the future session" note are
  resolved by what actually shipped (steps 2-3/5 shipped in the same
  overall multi-part effort, not step-1-alone; validation is a hard block).
- No item-15-scoped piece was silently dropped. The only unbuilt items
  touching this area (`TEMPLATE_STORAGE` live enumeration,
  `DISK_GB`-vs-pool-free-space validation) were never part of item 15's own
  six-piece "Shape of the work" list — both are explicitly framed as
  Non-goals in this cycle's own `docs/spec.md` (new ideas surfaced *during*
  scoping this spec, not pre-existing backlog debt), so their absence does
  not contradict "shipped in full."

Verdict on claim 6: **holds**. The "shipped in full" status line itself is
accurate as written; the one thing that needed correcting was the stale
"Open for the future session" paragraph beneath it, which I fixed directly
in `docs/BACKLOG.md` (struck through, replaced with a "Resolved" note) as
part of this review rather than looping back to the developer for a
docs-only change.

## Overall verdict
**Approve with follow-ups.** All 11 acceptance criteria verified with actual
evidence from an independently-rebuilt test harness plus direct diff/code
reading; `bash -n` and `shellcheck` both independently reconfirmed clean;
the `numfmt --to=iec-i` deviation is independently verified correct; the
Default branch is confirmed byte-for-byte untouched; the adversarial
`set -euo pipefail`/`pct status` interaction is confirmed safe. Two
should-fix/nit findings (Finding 2: retry loops don't carry forward the
rejected value; Finding 3/4: dead IFS toggle, tighter menu padding) are
non-blocking UX/style points, not correctness or security issues, and
don't fail any acceptance criterion. `docs/BACKLOG.md`'s "shipped in full"
claim for item 15 is confirmed accurate; its stale "Open for the future
session" note was corrected directly in this pass. This closes out BACKLOG
item 15 — control returns to product-manager for the next iteration.
