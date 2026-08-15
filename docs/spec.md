# Spec: Install wizard UI — part 3: live storage/bridge enumeration + CTID/hostname hard-block validation (BACKLOG item 15, pieces 2-4)

## Summary
Inside `ct/create.sh`'s Advanced branch only, replace the free-text
storage-pool and network-bridge `ask()` prompts with live-enumerated
`whiptail --menu` pickers (`pvesm status -content rootdir` for storage;
kernel bridges + SDN vnets for network bridges), and add hard-block,
loop-until-valid CTID/hostname validation before `pct create` is ever
attempted — closing out BACKLOG item 15 (pieces 2, 3, and 4; piece 1
shipped in PR #21, piece 5 shipped in PR #20).

## Routing note
Workflow: `workflows/feature.md`. Single file (`ct/create.sh`), single
architectural layer (the `whiptail` TUI prompt sequence inside the
already-existing Advanced branch) — no schema, API, or web-UI layer
involved, matching parts 1 and 2's own routing reasoning. All three pieces
(storage enumeration, bridge enumeration, CTID/hostname validation) are
independent, non-overlapping edits to three different existing `ask()`
call sites in the same branch of the same file, with no shared state
beyond variables that already exist — this does not meet the
load-balanced-decomposition bar for splitting into sub-specs (that bar is
about work spanning multiple architectural layers — e.g. schema + API +
UI — not about a file having three independent edit sites in one layer).
Routes through **ux-designer** first, same reason parts 1/2 did: not for
visual design, but for the wording of the new menu row descriptions and
the new hard-block error `msgbox` text.

**This is the last scoped piece of BACKLOG item 15.** After this cycle,
items 1-5 of the "Shape of the work" list in `docs/BACKLOG.md`'s item 15
entry are all shipped (1: part 2, 2-4: this part, 5: part 1) and item 6 is
a standing non-goal. No part 4 is anticipated; item 15 should be marked
shipped/closed in `docs/BACKLOG.md` once this cycle is reviewer-approved.

## Goals
- **Piece 2 — storage-pool enumeration.** Replace
  `STORAGE=$(ask "Storage pool for the container's root disk:"
  "$DEFAULT_STORAGE")` (current `ct/create.sh:84`) with: run `pvesm status
  -content rootdir`, filter to rows whose `Status` column is `active`,
  and if one or more remain, present them as a `whiptail --menu` (each row
  labelled with its storage type and free space, e.g.
  `local-lvm   lvmthin, 362GiB free`); the operator's selection becomes
  `STORAGE`. If enumeration yields zero usable pools, fall back to
  exactly today's free-text `ask()` behavior (see Edge cases).
- **Piece 3 — network-bridge enumeration.** Replace
  `BRIDGE=$(ask "Network bridge:" "$DEFAULT_BRIDGE")` (current
  `ct/create.sh:88`) with: enumerate live kernel bridges (`ip -o link show
  type bridge`) plus any Proxmox SDN vnets (`/etc/pve/sdn/vnets.cfg`, same
  file community-scripts' own `_detect_bridges()` reads), and if one or
  more are found, present them as a `whiptail --menu`; the operator's
  selection becomes `BRIDGE` (SDN entries are shown prefixed `sdn:` in the
  menu label for clarity but the prefix is stripped before being used as
  the actual `-net0 bridge=...` value). If enumeration yields zero
  results, fall back to exactly today's free-text `ask()` behavior (see
  Edge cases).
- **Piece 4 — CTID/hostname hard-block validation.** Wrap both
  `CTID=$(ask "Container ID (must be free):" "$(default_ctid)")` and
  `CT_HOSTNAME=$(ask "Hostname:" "$DEFAULT_CT_HOSTNAME")` (current
  `ct/create.sh:82-83`) each in their own loop-until-valid retry (same
  interaction shape as the existing ollama endpoint retry loop,
  `ct/create.sh:158-196`): on invalid input, show a `msgbox` explaining
  exactly what's wrong and re-show the same `ask()` prompt; on valid
  input, proceed. This is a **hard block** — there is no "continue
  anyway" escape hatch, per the reasoning already settled in part 1's
  spec's "Deferred to a later part" section (preserved below verbatim
  since part 1's spec.md has since been overwritten by parts 2 and this
  one):

  > CTID uniqueness (checkable exactly via `pct status "$CTID"`/`pvesh get
  > /cluster/resources`) and RFC1123 hostname syntax are exact rules `pct
  > create` itself enforces, not guesses — checking them before `pct
  > create` and giving a clear whiptail error is strictly better than
  > surfacing the same rule as a raw `pct create` stack trace later, and
  > loops the operator back to re-enter the field rather than aborting the
  > whole run.

  CTID validation: numeric, and in Proxmox's actual valid VMID range
  (100-999999999 — 1-99 are reserved), and not already in use on this
  node (`pct status "$CTID"` exits 0 iff a container/VM with that ID
  already exists locally). Hostname validation: RFC1123 basic shape only
  — each dot-separated label is 1-63 characters, alphanumeric plus
  hyphen, must not start or end with a hyphen; overall length <= 253.
  This is a narrow, mechanical regex/exit-code check, not a
  reimplementation of Proxmox's full hostname/VMID validation.
- Default path is completely untouched by all three pieces — it never
  shows a storage/bridge prompt or a CTID/hostname field to validate in
  the first place (it uses `DEFAULT_STORAGE`/`DEFAULT_BRIDGE` and
  `default_ctid()`/`DEFAULT_CT_HOSTNAME` directly, with no validation, by
  existing intentional design — confirmed not to be touched by this spec).

## Non-goals
- Extending storage enumeration to `TEMPLATE_STORAGE` (current
  `ct/create.sh:90`, the OS-template storage prompt). BACKLOG item 15's
  piece 2 is scoped specifically to `pvesm status -content rootdir` (the
  root-disk storage prompt); `TEMPLATE_STORAGE` needs `-content vztmpl`
  instead and was not named in piece 2's scope. The same helper/menu
  pattern this spec introduces would extend to it cheaply as a fast
  follow, but adding it here without it being asked for is scope creep —
  flagged under Open questions instead of silently included.
- CTID/hostname validation beyond RFC1123 shape and local-node uniqueness
  — no reachability/DNS checks, no cluster-wide VMID check via `pvesh get
  /cluster/resources` (local `pct status` is sufficient for this script's
  existing single-node assumption — `default_ctid()` above it already
  only calls `pvesh get /cluster/nextid`, which is cluster-aware, but this
  script otherwise operates against `pct` locally throughout).
- Storage-space validation (community-scripts' `validate_storage_space()`
  — checking the chosen pool has enough free space for `DISK_GB`). Not
  named in item 15's scoped pieces; a plausible future enhancement, not
  built here.
- Any step-back/step-state-machine navigation, app-defaults save/reuse,
  IPv6/MTU/VLAN fields — all already excluded by item 15's backlog entry
  ("Explicitly out of scope for this item"), unchanged here.
- Any change to the Default path, to `install.sh`, to `app/`, or to any
  web UI code.
- Any change to `DISK_GB`/`CORES`/`MEM_MB`/`IPCONFIG` prompts — untouched,
  still free-text `ask()` exactly as today.

## Background / current state
`ct/create.sh` (288 lines, after parts 1 and 2) has an `if [ "$INSTALL_MODE"
= "default" ]; then ... else ... fi` fork (`ct/create.sh:54-207`). The
`else` (Advanced) branch, lines 82-207, is the exact pre-part-2 prompt
sequence, unchanged by part 2 apart from being relocated inside the
`else`. The three prompts this spec touches, at their current exact line
numbers:

```
82  CTID=$(ask "Container ID (must be free):" "$(default_ctid)")
83  CT_HOSTNAME=$(ask "Hostname:" "$DEFAULT_CT_HOSTNAME")
84  STORAGE=$(ask "Storage pool for the container's root disk:" "$DEFAULT_STORAGE")
...
88  BRIDGE=$(ask "Network bridge:" "$DEFAULT_BRIDGE")
```

`default_ctid()` (`ct/create.sh:50-52`) already calls `pvesh get
/cluster/nextid`, so a fresh CTID suggestion is already collision-free by
construction — validation only matters when the operator *overrides* the
suggested default with something else, which `ask()` always allows.

Re-checked `misc/build.func` (community-scripts/ProxmoxVE) directly this
session for the bridge-enumeration mechanism, per this cycle's
instructions, rather than guessing: its `_detect_bridges()` helper (inside
`advanced_settings()`) parses `/etc/network/interfaces` and
`/etc/network/interfaces.d/*` line-by-line for `iface`/`bridge-ports`/
`bridge_vlan-aware`/`ovs_type OVSBridge` keywords to build `BRIDGES`, then
separately reads Proxmox SDN vnets via `awk '/^vnet:/{print $2}'
/etc/pve/sdn/vnets.cfg`, prefixing SDN entries `sdn:` in the resulting
`BRIDGE_MENU_OPTIONS`.

**Deliberate deviation, flagged explicitly:** this spec does not port
`_detect_bridges()`'s text-parsing of `/etc/network/interfaces` verbatim.
Per item 15's own backlog guidance ("borrow the *pattern*, not the
*code*") and this project's "no shared framework, just pct and whiptail"
design constraint, this spec instead uses `ip -o link show type bridge`
to read live kernel bridge state directly via `iproute2` (already present
on every Debian/Proxmox host, no config-file parsing/regex fragility) —
functionally equivalent for this script's purpose (every bridge a
container could actually attach to is, by definition, an up kernel
bridge), simpler, and more robust than re-parsing ifupdown config syntax
in bash. The SDN-vnet half (`/etc/pve/sdn/vnets.cfg` via the same `awk`
one-liner) is kept as-is since it is already exactly this simple. This is
called out here per skill 5's "flag deliberate deviations" rule, not
silently substituted.

`pvesm status -content rootdir` output (confirmed via Proxmox
documentation/forum references, columns are header + one row per storage):
```
Name             Type     Status           Total            Used       Available        %
local            dir      active        101584140        13735316        82694508  13.52%
local-lvm        lvmthin  active        380526592               0       380526592   0.00%
```
Column 1 = name, column 2 = type, column 3 = status, column 6 = available
(KiB). Rows with `Status != active` (e.g. a misconfigured/unreachable NFS
mount showing `unknown`) are excluded from the menu — an inactive pool
would fail `pct create` immediately anyway, so offering it in the picker
would just relocate the same failure one step later.

## Proposed approach

### Shared helpers (added near the existing `msg`/`ask`/`askpw`/`yesno`/
`menu` block, `ct/create.sh:26-30`)
```bash
_valid_hostname() {
    local _h="$1" _label
    [ -n "$_h" ] && [ "${#_h}" -le 253 ] || return 1
    local _old_ifs=$IFS
    IFS='.'
    for _label in $_h; do
        IFS=$_old_ifs
        [[ "$_label" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] || return 1
        IFS='.'
    done
    IFS=$_old_ifs
    return 0
}

_enumerate_storage() {
    STORAGE_MENU_OPTS=()
    local _line _name _type _status _avail _desc
    while IFS= read -r _line; do
        _name=$(awk '{print $1}' <<<"$_line")
        _type=$(awk '{print $2}' <<<"$_line")
        _status=$(awk '{print $3}' <<<"$_line")
        _avail=$(awk '{print $6}' <<<"$_line")
        [ "$_status" = "active" ] || continue
        if command -v numfmt >/dev/null 2>&1 && [[ "$_avail" =~ ^[0-9]+$ ]]; then
            _desc="${_type}, $(numfmt --to=iec --suffix=B $((_avail * 1024)) 2>/dev/null) free"
        else
            _desc="$_type"
        fi
        STORAGE_MENU_OPTS+=("$_name" "$_desc")
    done < <(pvesm status -content rootdir 2>/dev/null | tail -n +2)
}

_enumerate_bridges() {
    BRIDGE_MENU_OPTS=()
    local _br _vnet
    while IFS= read -r _br; do
        [ -n "$_br" ] && BRIDGE_MENU_OPTS+=("$_br" "kernel bridge")
    done < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1)
    if [ -f /etc/pve/sdn/vnets.cfg ]; then
        while IFS= read -r _vnet; do
            [ -n "$_vnet" ] && BRIDGE_MENU_OPTS+=("sdn:${_vnet}" "SDN vnet")
        done < <(awk '/^vnet:/{print $2}' /etc/pve/sdn/vnets.cfg 2>/dev/null)
    fi
}
```
(`numfmt`'s bytes conversion multiplies by 1024 because `pvesm status`'s
`Available` column is in KiB, per the sample output above; if `numfmt` is
unavailable — unlikely on Debian, but not assumed — the description just
omits the free-space figure rather than failing.)

### Piece 4 — replace `ct/create.sh:82-83`
```bash
while :; do
    CTID=$(ask "Container ID (must be free):" "$(default_ctid)")
    if ! [[ "$CTID" =~ ^[0-9]+$ ]] || [ "$CTID" -lt 100 ] || [ "$CTID" -gt 999999999 ]; then
        msg "Container ID must be a number between 100 and 999999999 (got '$CTID')."
        continue
    fi
    if pct status "$CTID" >/dev/null 2>&1; then
        msg "Container ID $CTID is already in use on this host. Choose a different one."
        continue
    fi
    break
done

while :; do
    CT_HOSTNAME=$(ask "Hostname:" "$DEFAULT_CT_HOSTNAME")
    if ! _valid_hostname "$CT_HOSTNAME"; then
        msg "'$CT_HOSTNAME' is not a valid hostname (letters, digits, hyphens; each dot-separated label 1-63 characters; can't start or end with a hyphen)."
        continue
    fi
    break
done
```
Whiptail Cancel inside either `ask()` call still aborts the whole run
immediately (pre-existing `set -euo pipefail` behavior identical to every
other `ask()` in the file — not a new edge case introduced by the retry
loop, which only re-prompts on *validation failure*, not on Cancel).

### Piece 2 — replace `ct/create.sh:84`
```bash
_enumerate_storage
if [ "${#STORAGE_MENU_OPTS[@]}" -eq 0 ]; then
    STORAGE=$(ask "Storage pool for the container's root disk:" "$DEFAULT_STORAGE")
else
    _storage_rows=$(( ${#STORAGE_MENU_OPTS[@]} / 2 ))
    _storage_listheight=$(( _storage_rows < 8 ? _storage_rows : 8 ))
    STORAGE=$(whiptail --title "ai-dev-switchboard" --menu \
        "Storage pool for the container's root disk:" \
        "$(( _storage_listheight + 9 ))" 78 "$_storage_listheight" \
        "${STORAGE_MENU_OPTS[@]}" 3>&1 1>&2 2>&3)
fi
```

### Piece 3 — replace `ct/create.sh:88`
```bash
_enumerate_bridges
if [ "${#BRIDGE_MENU_OPTS[@]}" -eq 0 ]; then
    BRIDGE=$(ask "Network bridge:" "$DEFAULT_BRIDGE")
else
    _bridge_rows=$(( ${#BRIDGE_MENU_OPTS[@]} / 2 ))
    _bridge_listheight=$(( _bridge_rows < 8 ? _bridge_rows : 8 ))
    BRIDGE=$(whiptail --title "ai-dev-switchboard" --menu \
        "Network bridge:" \
        "$(( _bridge_listheight + 9 ))" 78 "$_bridge_listheight" \
        "${BRIDGE_MENU_OPTS[@]}" 3>&1 1>&2 2>&3)
    BRIDGE="${BRIDGE#sdn:}"
fi
```

All four blocks stay inside the existing `else` (Advanced) branch,
between the current `else` (line 81) and the existing `DISK_GB=$(ask
...)` line — insertion order (CTID/hostname validation first, then
storage, unchanged disk/cores/mem, then bridge, unchanged ipconfig/
template-storage/run-user) matches the file's existing field order
exactly; no field is reordered.

## Affected areas
- `ct/create.sh` only — the four helper functions (added near the
  existing helper block) plus the four `ask()` call sites inside the
  Advanced branch (`ct/create.sh:82-83-84-88` at current line numbers;
  will shift once the CTID/hostname loops are inserted, since they add
  lines above the storage prompt — developer should re-read the file
  after each edit rather than trusting these line numbers past the first
  change). No other file changes.

## Edge cases
- **Zero active storage pools found by `pvesm status -content rootdir`**
  (e.g. a fresh host with only backup/ISO storage, no rootdir-capable
  pool visible) — falls back to today's free-text `ask()` with
  `DEFAULT_STORAGE` as the shown default, identical to pre-this-spec
  behavior. Explicit decision, not an oversight (per Goals/Proposed
  approach above).
- **Zero bridges found** (no kernel bridge up, no SDN vnets configured —
  practically rare since `vmbr0` normally exists on any real PVE host,
  but possible on a minimal/testing host) — same fallback to free-text
  `ask()` with `DEFAULT_BRIDGE`.
- **Exactly one storage pool / one bridge found** — still shown as a
  one-row `whiptail --menu`, not auto-selected. (community-scripts
  auto-picks silently when there's exactly one pool; this spec
  deliberately keeps the operator's explicit confirmation step even for a
  single option, consistent with this project's existing "no silent
  skip once you're in Advanced" posture — Advanced means every field is
  shown. Flagged here as a deliberate, minor deviation from the
  researched pattern rather than left implicit.)
- **CTID re-entered identical to a rejected value** — loop re-validates
  identically each pass; no infinite-loop risk beyond the operator simply
  retyping the same bad value forever, same as the existing ollama retry
  loop's behavior.
- **Hostname containing uppercase letters** — RFC1123 technically permits
  them in the syntax sense though DNS resolution normalizes to lowercase;
  `_valid_hostname`'s regex accepts uppercase (matches
  `[a-zA-Z0-9]`), consistent with `validate_hostname`-style checks being
  about *shape*, not about forcing case normalization the operator didn't
  ask for.
- **`pct status "$CTID"` exit code when CTID belongs to a VM, not a
  container** — `pct status` still exits 0 for a VMID that's a QEMU VM
  (Proxmox VMIDs are shared across the whole `pct`/`qm` namespace, and
  `pct status` on a VM's ID errors with a message but the important part
  — the ID being taken — is what matters here); either way a non-zero
  exit means the ID is genuinely free, a zero exit means it is not,
  regardless of which resource type owns it — correct behavior either
  way for this check's purpose (must the ID be free), no special-casing
  needed.
- **`numfmt` unavailable** — storage menu descriptions omit the free-space
  figure and show just the storage type; does not block the picker itself
  from working.
- **Non-Proxmox test environment (no `pct`/`pvesm`/`ip -o link show type
  bridge` returning real data)** — same preflight as today
  (`ct/create.sh:22`, `command -v pct` already hard-required at the top
  of the file); this spec does not change that requirement, and the
  reviewer's testing pass should mock/stub `pvesm status`/`pct
  status`/`ip -o link show` output rather than requiring a live Proxmox
  host, matching how prior parts' shell-level logic was already
  reviewer-tested by argument/output stubbing rather than requiring real
  infrastructure.

## Acceptance criteria
- [ ] Given the Advanced path and `pvesm status -content rootdir`
      returning two or more `active` rows, when the storage step is
      reached, then a `whiptail --menu` is shown listing each active
      pool's name with a type+free-space description, and the operator's
      selection is used as `STORAGE`.
- [ ] Given the Advanced path and `pvesm status -content rootdir`
      returning zero `active` rows, when the storage step is reached,
      then the original free-text `ask()` prompt (with `DEFAULT_STORAGE`
      as its shown default) is shown instead, unchanged from pre-this-spec
      behavior.
- [ ] Given the Advanced path and one or more kernel bridges (`ip -o link
      show type bridge`) and/or SDN vnets present, when the bridge step is
      reached, then a `whiptail --menu` is shown listing them, an SDN
      entry's `sdn:` label prefix is stripped before being assigned to
      `BRIDGE`, and the operator's selection becomes `BRIDGE`.
- [ ] Given the Advanced path and zero bridges/vnets found, when the
      bridge step is reached, then the original free-text `ask()` prompt
      (with `DEFAULT_BRIDGE` as its shown default) is shown instead.
- [ ] Given the Advanced path and a non-numeric or out-of-range
      (<100 or >999999999) CTID entered, when the CTID step's `ask()`
      returns, then a `msgbox` explains the problem and the same `ask()`
      prompt is re-shown (the run does not proceed to the hostname step
      or abort).
- [ ] Given the Advanced path and a CTID that `pct status` reports as
      already in use, when the CTID step's `ask()` returns, then a
      `msgbox` explains the collision and the same `ask()` prompt is
      re-shown.
- [ ] Given the Advanced path and a numeric, unused CTID, when the CTID
      step's `ask()` returns, then the run proceeds immediately to the
      hostname step with no extra prompt or delay.
- [ ] Given the Advanced path and a hostname violating RFC1123 shape
      (e.g. starts with a hyphen, contains an underscore or space, an
      empty label between two dots), when the hostname step's `ask()`
      returns, then a `msgbox` explains the problem and the same `ask()`
      prompt is re-shown.
- [ ] Given the Advanced path and a valid RFC1123 hostname, when the
      hostname step's `ask()` returns, then the run proceeds immediately
      to the storage step with no extra prompt or delay.
- [ ] Given the Default path, when `ct/create.sh` runs end to end, then
      no storage/bridge menu and no CTID/hostname validation loop is ever
      shown — behavior is byte-for-byte identical to before this spec
      (Default remains completely untouched).
- [ ] `shellcheck ct/create.sh` (or the project's existing lint step, if
      any is already run on this file — check how parts 1/2 were verified
      and match it) passes with no new warnings introduced by this
      spec's additions.

## Open questions
1. Should `TEMPLATE_STORAGE` (`ct/create.sh:90`, `-content vztmpl`) get
   the same live-enumeration treatment as `STORAGE` in this same cycle,
   since the helper pattern is identical and the marginal cost is low?
   **Assumption proceeding under: no** — piece 2 as scoped in
   `docs/BACKLOG.md` names `-content rootdir` specifically and does not
   mention `TEMPLATE_STORAGE`; flagged here rather than silently bundled
   in, per scope discipline. Easy fast-follow if wanted.
2. Should the storage/bridge menu's box height be dynamically sized (as
   proposed: `min(rows, 8)` visible rows, box height `rows+9`) or fixed
   like the existing checklist's `18 78 4`? **Assumption proceeding
   under: dynamic**, since storage-pool/bridge counts genuinely vary
   host-to-host (unlike the checklist's fixed 4 always-present feature
   toggles) and a fixed height would either waste space (few pools) or
   clip the list (many pools, e.g. a host with 10 ZFS datasets each
   exposed as separate storage). ux-designer should confirm or adjust the
   exact sizing constants during its pass, not re-litigate whether dynamic
   sizing is the right call.
3. Is `pct status "$CTID"` (local-node-only) sufficient for CTID
   collision checking, or should this also check
   `pvesh get /cluster/resources --type vmid` for cluster-wide safety on
   a clustered Proxmox setup? **Assumption proceeding under: local-node
   only (`pct status`) is sufficient** — this script already only ever
   creates the container on the node it's run on via local `pct`/`pveam`
   calls throughout (no cluster-target selection exists anywhere in the
   file), so cluster-wide uniqueness is outside this script's existing
   scope; `default_ctid()`'s own `pvesh get /cluster/nextid` call already
   provides cluster-safe suggestions for the common "just press Enter"
   case, which is the practical mitigation that matters most.

## Risk / rollback notes
Purely additive changes to three existing `ask()` call sites plus four new
helper functions, all confined to `ct/create.sh`'s Advanced branch; the
Default branch and every other part of the file (template resolution,
`pct create`/`pct start`, in-container bootstrap, summary box) are
byte-for-byte unchanged. Worst-case failure mode is a whiptail menu
showing unexpected/garbled entries if `pvesm status`/`ip -o link show
type bridge` output ever changes format on a future Proxmox/iproute2
version — degrades to a confusing menu, not a script crash (the parsing
is defensive: non-matching rows are simply skipped via the `active`
status filter and empty-line guards). Rollback is a straight `git revert`
of this cycle's commit; no data migration, no state left behind by a
partial run beyond what already happens today if `pct create` fails
partway (unchanged, pre-existing behavior).
