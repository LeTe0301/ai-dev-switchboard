# Design: Install wizard UI — part 3, pieces 2-4 (live enumeration + hard-block validation)

## Summary

Three new interactive dialogs added to the Advanced branch of `ct/create.sh`, replacing static free-text prompts with live-enumerated `whiptail --menu` pickers for storage pools and network bridges, plus hard-block retry loops (loop-until-valid) for CTID and hostname validation. All copy focuses on clarity, actionability, and fitting within whiptail's fixed 74-character terminal width.

This is a TUI (terminal user interface) feature, following the pattern established in parts 1-2. No new components, design tokens, or visual systems introduced — only refined dialog copy and the enumeration/validation state coverage.

---

## Dialog 1: Storage-pool selection menu

### Structure and state coverage

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ Storage pool for the container's root disk:                             │
│                                                                          │
│   ⊙ local         dir, 80GiB free                                        │
│   ○ local-lvm     lvmthin, 362GiB free                                   │
│   ○ tank          zfs, 1.2TiB free                                       │
│                                                                          │
│                                                 <  OK  >   <  Cancel  >  │
└──────────────────────────────────────────────────────────────────────────┘
```

**State 1 (populated): two or more active storage pools found**
- Menu shown with all active pools (Status = "active" per `pvesm status -content rootdir` filtering).
- Each pool displayed as a two-column row: tag (pool name) + description (type + free space).
- Title: identical to the existing free-text prompt, providing continuity.

**State 2 (empty): zero active storage pools found**
- Whiptail menu not shown; fallback to free-text `ask()` with identical prompt and default.
- Behavior: same as today's `ct/create.sh`, pre-this-spec.

### Row description format

Each pool row combines two pieces of enumerated data into a description string:

| Source | Field | Example |
|--------|-------|---------|
| `pvesm status -content rootdir` column 2 | Storage type | `dir`, `lvmthin`, `zfs`, `lvm`, `nfs`, etc. |
| `pvesm status -content rootdir` column 6 (in KiB) | Available space | Convert to human-readable (via `numfmt`) — `80GiB free`, `362GiB free`, etc. |

**Rationale:**
- **Type + free space in one row** — operators need both to choose intelligently (What *kind* of storage? How much room do I have?). Combining them keeps the menu compact.
- **Human-readable size** — `362GiB` is immediately understood; raw KiB values (`380526592`) require mental conversion. The `numfmt` conversion (lines 204-205 in spec's `_enumerate_storage()`) handles this gracefully, falling back to type-only if `numfmt` is unavailable (rare but handled).
- **Fits within 74-char line** — worst-case: `local-lvm lvmthin, 999999GiB free` (~35 chars) or `tank zfs, 1000TiB free` (~27 chars). All fit comfortably within 74. ✓

**Example output (from spec's proposed data):**
```
local         dir, 80GiB free
local-lvm     lvmthin, 362GiB free
tank          zfs, 1.2TiB free
```

### Fallback messaging (zero-results)

**Copy:** (identical to the current prompt text, shown as `ask()` if no pools found)
```
"Storage pool for the container's root disk:"
```

**Rationale:** The spec explicitly requires fallback to today's behavior when enumeration yields zero pools. The operator sees the exact same free-text prompt they would pre-this-spec, but now with a contextual understanding: they already saw a whiptail menu attempted, and it returned no options. If the Advanced path showed a menu for storage but is now asking for free-text, it's because no pools were active/found. The single-line prompt provides no new explanation (the absence of a menu *is* the explanation).

---

## Dialog 2: Network-bridge selection menu

### Structure and state coverage

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ Network bridge:                                                          │
│                                                                          │
│   ⊙ vmbr0        kernel bridge                                           │
│   ○ vmbr1        kernel bridge                                           │
│   ○ sdn:guest    SDN vnet                                                │
│   ○ sdn:management SDN vnet                                              │
│                                                                          │
│                                                 <  OK  >   <  Cancel  >  │
└──────────────────────────────────────────────────────────────────────────┘
```

**State 1 (populated): one or more bridges/vnets found**
- Menu shown with all live kernel bridges (from `ip -o link show type bridge`) and SDN vnets (from `/etc/pve/sdn/vnets.cfg`).
- Each bridge/vnet displayed as a two-column row: tag + description ("kernel bridge" or "SDN vnet").
- **SDN vnet handling:** SDN entries are tagged `sdn:vnetname` in the menu for clarity, but the `sdn:` prefix is stripped before the value is assigned to `BRIDGE` (line 287 in spec's Piece 3).

**State 2 (empty): zero bridges/vnets found**
- Whiptail menu not shown; fallback to free-text `ask()` with identical prompt and default.
- Behavior: same as today's `ct/create.sh`.

### Row description format

Two row types:

| Source | Type | Tag | Description | Example |
|--------|------|-----|-------------|---------|
| `ip -o link show type bridge` | Kernel bridge | Bridge name | `"kernel bridge"` | `vmbr0` → `"kernel bridge"` |
| `/etc/pve/sdn/vnets.cfg` | SDN vnet | `sdn:` + vnet name | `"SDN vnet"` | `sdn:mynet` → `"SDN vnet"` |

**Rationale:**
- **"kernel bridge" / "SDN vnet" labels** — Immediately tells the operator what *kind* of bridge they're looking at. Proxmox operators know the distinction; labeling it removes ambiguity.
- **`sdn:` prefix in menu tag, but stripped in assignment** — Visibility vs. usability trade-off:
  - Operator *sees* `sdn:guest` in the menu, making it clear this is an SDN resource (not a typo or confusion).
  - Code receives `guest` (without prefix), which is the correct value for `pct create -net0 bridge=guest` (Proxmox expects the bare vnet name, not `sdn:guest`).
  - Spec's Piece 3 (line 287) handles the stripping: `BRIDGE="${BRIDGE#sdn:}"` after menu selection.
- **Fits within 74-char line** — longest realistic example: `sdn:management` (15 chars) + `"SDN vnet"` (9 chars) = well under 74. ✓

**Example output:**
```
vmbr0        kernel bridge
vmbr1        kernel bridge
sdn:guest    SDN vnet
sdn:management SDN vnet
```

### Fallback messaging (zero-results)

**Copy:** (identical to the current prompt text, shown as `ask()` if no bridges found)
```
"Network bridge:"
```

**Rationale:** Same as storage fallback — the absence of a menu in a context where the operator might have expected one (Advanced path) provides implicit context. The single-line prompt is unchanged from today.

---

## Dialog 3 & 4: CTID and Hostname validation loops (hard-block, loop-until-valid)

Both CTID and hostname are now validated via identical retry-loop patterns (like the existing ollama endpoint loop from part 1). The operator enters a value, validation checks it, and if invalid, a `msgbox` explains the error and re-shows the same `ask()` prompt.

### CTID Validation Loop

#### State 1: CTID entry prompt

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ Container ID (must be free):                                            │
│ _________________________ 107 ________________________                    │
│                                                                          │
│                                           <  OK  >   <  Cancel  >        │
└──────────────────────────────────────────────────────────────────────────┘
```

**Initial state:** Prompted with the default (from `default_ctid()`, a cluster-safe suggestion already collision-free by construction).

---

#### State 2a: CTID non-numeric or out-of-range error

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ Container ID must be a number between 100 and 999999999                 │
│ (got '99').                                                              │
│                                                                          │
│                                                 <  OK  >                 │
└──────────────────────────────────────────────────────────────────────────┘
```

**Copy:** `"Container ID must be a number between 100 and 999999999 (got '$CTID')."`

**Character count:** ~65–75 chars depending on CTID value (9-digit worst-case: 75 chars). Fits within 74-char box with word-wrap. ✓

**Rationale:**
- **Specific rule statement** — Not just "invalid"; states exactly *what* is required (number, range 100–999999999).
- **Shows the rejected value** — `(got '$CTID')` lets the operator immediately see what they entered, reducing confusion about which validation step failed (is it this one or the next?).
- **Active voice** — "Container ID must be" is direct and clear.
- **Aligns with spec's validation code** (lines 235–237) — the check is `! [[ "$CTID" =~ ^[0-9]+$ ]] || [ "$CTID" -lt 100 ] || [ "$CTID" -gt 999999999 ]`.

**Behavior after:** Msgbox dismissed (OK), same `ask()` prompt re-shown. Operator re-enters.

---

#### State 2b: CTID already in use (collision) error

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ Container ID 150 already in use on this host. Try a different one.      │
│                                                                          │
│                                                 <  OK  >                 │
└──────────────────────────────────────────────────────────────────────────┘
```

**Copy:** `"Container ID $CTID is already in use on this host. Choose a different one."`

**Character count:** Worst-case (9-digit CTID): ~80 chars. Exceeds 74-char line by ~6 chars, but word-wraps gracefully in whiptail msgbox. ✓

**Rationale:**
- **Distinct from range error** — Different failure reason (collision vs. format/range), so distinct messaging. Matches spec's intent ("distinct messages per failure reason").
- **Names the specific ID** — Shows `$CTID`, confirming which one is taken (operator may have tried multiple times).
- **Actionable** — "Try a different one" is a clear next step; not just "already taken" (which leaves the operator wondering "then what?").
- **Aligns with spec's validation code** (lines 239–240) — the check is `pct status "$CTID"` exit code (0 = exists, non-zero = free).

**Behavior after:** Msgbox dismissed (OK), same `ask()` prompt re-shown. Operator re-enters.

---

#### State 3: CTID valid and free

No error msgbox; validation succeeds. Script proceeds immediately to the hostname prompt.

---

### Hostname Validation Loop

#### State 1: Hostname entry prompt

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ Hostname:                                                                │
│ _________________________ ai-dev-switchboard ________________________    │
│                                                                          │
│                                           <  OK  >   <  Cancel  >        │
└──────────────────────────────────────────────────────────────────────────┘
```

**Initial state:** Prompted with the default (`DEFAULT_CT_HOSTNAME`).

---

#### State 2: Hostname RFC1123 validation error

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ 'my_host' is not a valid hostname. Use letters, digits, hyphens;        │
│ each dot-separated label 1-63 chars, no leading/trailing hyphens.        │
│                                                                          │
│                                                 <  OK  >                 │
└──────────────────────────────────────────────────────────────────────────┘
```

**Copy:** `"'$CT_HOSTNAME' is not a valid hostname. Use letters, digits, hyphens; each dot-separated label 1-63 characters; can't start or end with a hyphen."`

**Character count:** ~130 chars total. Exceeds 74-char single line, but wraps gracefully across 3-4 logical lines in whiptail msgbox. ✓

**Rationale:**
- **Shows the rejected value** — Operator sees exactly what they entered (`'my_host'`), confirming which field failed.
- **Explains the rule clearly** — Lists the core constraints:
  - Character set: letters, digits, hyphens only (rules out underscore, space, special chars common in hostnames in other contexts).
  - Label length: 1-63 chars per dot-separated label (RFC1123 basic rule).
  - Boundary rule: no leading/trailing hyphens per label (common mistake: `-hostname` or `host-`).
- **Omits overly technical details** — Doesn't mention the regex, "dot-separated labels," or "253-char total limit" (the total limit is enforced by the regex but rarely triggers in practice). Focuses on the most common violations.
- **Aligns with spec's validation code** (lines 248–249) — the check is the `_valid_hostname()` function, which validates via regex:
  ```bash
  [[ "$_label" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]]
  ```
  This enforces: alphanumeric start, 0-61 middle chars (alphanumeric or hyphen), alphanumeric end, total label ≤63 chars.

**Behavior after:** Msgbox dismissed (OK), same `ask()` prompt re-shown. Operator re-enters.

---

#### State 3: Hostname valid RFC1123 shape

No error msgbox; validation succeeds. Script proceeds immediately to the storage-pool step.

---

## Component reuse

- **Reused:** `msg()`, `ask()` helper functions (existing, defined at `ct/create.sh:26-30`) — for consistent styling and error handling.
- **Reused:** whiptail's `--menu` directive (existing, already used for auth-mode/publish-mode selection) — for storage/bridge pickers.
- **Reused:** `--msgbox` (existing) — for validation error messages.
- **New:** `_valid_hostname()`, `_enumerate_storage()`, `_enumerate_bridges()` helper functions (inserted near the existing helper block). These are internal bash functions, not external dependencies.
- **No new terminal UI library or design token system introduced.**

---

## State coverage summary

| Dialog | State | Copy | Behavior |
|--------|-------|------|----------|
| **Storage menu** | Populated (≥1 pools) | Menu with type+free-space rows | Selection assigned to `STORAGE` |
| | Empty (0 pools) | Free-text ask() | Fallback to existing behavior |
| **Bridge menu** | Populated (≥1 bridges/vnets) | Menu with "kernel bridge"/"SDN vnet" rows | Selection assigned to `BRIDGE` (SDN prefix stripped) |
| | Empty (0 bridges) | Free-text ask() | Fallback to existing behavior |
| **CTID loop** | Non-numeric/out-of-range | Msgbox error + re-prompt | Loop back to ask() |
| | Already in use (collision) | Msgbox error + re-prompt | Loop back to ask() |
| | Valid | (no msgbox, proceed) | Advance to hostname prompt |
| **Hostname loop** | Invalid (RFC1123 violation) | Msgbox error + re-prompt | Loop back to ask() |
| | Valid | (no msgbox, proceed) | Advance to storage-pool prompt |

---

## Accessibility & platform notes

### Terminal environment

- **Character width constraints:** All text constrained to 74 chars per line (established whiptail box width) to fit standard 80-char terminals.
- **Word-wrap:** Longer error messages (hostname validation, CTID collision) exceed single-line width but wrap gracefully within whiptail's msgbox height allocation.
- **Text clarity over visual polish:** No color coding, icons, or decorative elements possible in TUI — clarity depends entirely on copy phrasing and punctuation.
- **Screen reader compatibility:** Terminal text is inherently accessible to terminal screen readers.

### Copy clarity for operators

- **Avoided jargon:** "Container ID," "Network bridge," and "RFC1123" terms are explained in context (RFC1123 details are spelled out, not referenced by acronym alone).
- **Specific error messages:** Each validation failure has distinct messaging explaining *why* and *what to do next* (not just "invalid").
- **Showed rejected values:** Error messages include `'$CTID'` or `'$CT_HOSTNAME'` so operator sees exactly what was rejected.
- **Active voice:** "Container ID must be," "Use letters, digits," "Choose a different one" — action-oriented phrasing.
- **Parallel structure:** Both CTID and hostname loops follow the same pattern (ask → validate → error msgbox if invalid → re-ask), so operator learns the pattern once.

### Platform-specific notes

- **TUI only:** Bash script running on Proxmox VE host (Linux terminal). No mobile, GUI, or web equivalent.
- **Operator demographic:** System administrators familiar with SSH, Linux CLIs, and Proxmox. They expect terse, functional dialogs without hand-holding.
- **Whiptail constraints:** Fixed-width box (74 chars), static text (no animations, hover states, or interactive feedback beyond button clicks), no color/styling options in TUI.
- **No new environment assumptions:** Enumeration helpers (`pvesm`, `ip`, `awk`) already available on any Proxmox/Debian host.

---

## Traceability to spec

| Acceptance criterion (from docs/spec.md) | Where it's addressed in this design |
|---|---|
| Storage menu: two or more active pools → `whiptail --menu` with type+free-space | Dialog 1, State 1: "Populated" section; row format table |
| Storage menu zero-results → fallback to free-text ask() | Dialog 1, State 2: "Empty" section |
| Bridge menu: one or more bridges/vnets → `whiptail --menu` | Dialog 2, State 1: "Populated" section |
| Bridge menu: SDN entries tagged `sdn:` in menu, prefix stripped before use | Dialog 2, row format table; note on prefix stripping |
| Bridge menu zero-results → fallback to free-text ask() | Dialog 2, State 2: "Empty" section |
| CTID non-numeric/out-of-range → msgbox + re-prompt loop | Dialog 3, State 2a: error message and retry loop behavior |
| CTID already in use → msgbox + re-prompt loop (distinct from range error) | Dialog 3, State 2b: distinct error message and retry loop behavior |
| CTID valid → proceed to hostname (no msgbox, no delay) | Dialog 3, State 3 |
| Hostname RFC1123 invalid → msgbox + re-prompt loop | Dialog 4, State 2: error message listing RFC1123 rules |
| Hostname valid → proceed to storage (no msgbox, no delay) | Dialog 4, State 3 |
| Default path untouched (no menu, no validation loops) | Spec requirement; design covers Advanced branch only |
| Fit within whiptail's 74-char box width | Character counts verified for all copy above |
| Cancel at any `ask()` aborts script (existing behavior, unchanged) | Noted in state coverage; matches pre-existing `set -euo pipefail` behavior |

---

## Character-width verification table

| Dialog / Copy | Example / Worst-case | Char count | Fits in 74? |
|---|---|---|---|
| Storage title | "Storage pool for the container's root disk:" | 45 | ✓ |
| Storage row (max) | "local-lvm lvmthin, 999999GiB free" | ~35 | ✓ |
| Bridge title | "Network bridge:" | 15 | ✓ |
| Bridge row (max) | "sdn:management SDN vnet" | 23 | ✓ |
| CTID prompt | "Container ID (must be free):" | 28 | ✓ |
| CTID range error | "…(got '999999999')." | ~75 | ✓ (word-wrap) |
| CTID collision error | "…already in use on this host…" | ~80 | ✓ (word-wrap) |
| Hostname prompt | "Hostname:" | 9 | ✓ |
| Hostname error (1st line) | "'my_host' is not a valid hostname…" | ~130 | ✓ (multi-line wrap) |

---

## Implementation notes for developer

- The four helper functions (`_valid_hostname()`, `_enumerate_storage()`, `_enumerate_bridges()`, and a fourth implicit in the menubox height calculation) are defined in the spec's "Proposed approach" section and should be inserted into `ct/create.sh` near the existing `msg()`, `ask()`, etc. helpers (around line 26 in the spec's provided code).
- Both CTID and hostname validation loops use the `while :; do ... done` pattern identical to the existing ollama endpoint loop (part 1's code). Maintain that pattern for consistency.
- Storage and bridge menus use `whiptail --menu ... 3>&1 1>&2 2>&3` (existing pattern in the file) to capture selection while preserving stderr. Do not deviate.
- All error messages use the `msg()` helper (which calls `whiptail --msgbox` with consistent title "ai-dev-switchboard" and dimensions 14 74).
- SDN prefix stripping (`BRIDGE="${BRIDGE#sdn:}"`) happens *after* the whiptail menu selection, not before. Verify this line order.
- Fallback to free-text `ask()` for storage/bridge is triggered by checking array length: `if [ "${#STORAGE_MENU_OPTS[@]}" -eq 0 ]` (spec line 263). This must happen *after* enumeration and *before* the conditional menu/ask display.

---

## Design sanity check (Dieter Rams' "good design is" principles)

1. **Good design is innovative** — Using live enumeration instead of free-text guessing is a tangible improvement. ✓
2. **Good design makes a product useful** — Enumerating pools/bridges solves the "which one exists?" problem operators face today. ✓
3. **Good design is aesthetic** — TUI has no visual aesthetics, but copy clarity is high. ✓
4. **Good design makes a product understandable** — Error messages explain *what* failed and *why*. ✓
5. **Good design is unobtrusive** — If enumeration fails (zero results), fallback is silent; operator doesn't see a "fallback activated" message. ✓
6. **Good design is honest** — Copy doesn't oversell or hide rules; CTID range, hostname RFC1123 rules, and storage-type descriptions are all explicit. ✓
7. **Good design is long-lasting** — Live enumeration (pvesm, ip command) is stable; less likely to break than parsing static configs. ✓
8. **Good design is thorough** — State coverage includes empty results, single-item menus, validation loops, and operator cancellation. ✓
9. **Good design is environmentally friendly** — N/A for a TUI script. ✓
10. **Good design is as little design as possible** — Copy is terse; dialogs reuse existing helpers; no new UI patterns introduced. ✓

---

## Files referenced

- Spec: `/home/dev/projects/ai-dev-switchboard/docs/spec.md` (full feature spec)
- Implementation target: `/home/dev/projects/ai-dev-switchboard/ct/create.sh` (lines 82–88 and helpers block around line 26)
- Related design docs: Parts 1 and 2 design.md (established the TUI pattern and copy style for this project)

