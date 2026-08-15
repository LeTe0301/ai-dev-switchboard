# Spec: ct/create.sh fixes from Proxmox E2E test round 3 (items 31, 32)

## Summary
Two more bugs from the same Proxmox E2E test (`docs/BACKLOG.md` items 31,
32), both in `ct/create.sh`. Both fully diagnosed with exact repro and a
clear fix already established by the E2E tester.

## Orchestrator note
No product-manager/ux-designer dispatch — same "fully-diagnosed follow-up"
precedent as rounds 1/2 (PRs #27, #28).

---

## Fix 1 — Item 31: raise `DEFAULT_DISK_GB` — 8G fills completely once Gitea + Taiga + code-server are all enabled

**Where**: `ct/create.sh:87`.

**Current**:
```bash
DEFAULT_DISK_GB="8"
```

**Problem**: following Advanced Install with all four optional features
enabled and the installer's own default disk size fills the container's
entire root disk (`df -h /` → 100% used, 0 available). The actual symptom
a real user hits is not an out-of-space message — it's `taiga-db`'s
Postgres instance failing to start with `FATAL: could not write init
file`, giving no hint the real problem is disk space.

**Fix**: raise the default to `20`:
```bash
DEFAULT_DISK_GB="20"
```
(The E2E report suggested a 20-24G range; 20 is the conservative end of
that range — enough headroom for all four optional features per the
report's own measured breakdown (~2.2G base + apt, ~3.9G Docker images/
volumes for Gitea+Taiga, ~683M for one pipx-installed engine, plus
working room) without being needlessly large for an installer default
aimed at a homelab-scale Proxmox host.)

**Non-goal**: the E2E report also suggests `taiga-up.sh`/`gitea-up.sh`
could `df`-check the target filesystem before calling `docker compose up`
and refuse with a clear message instead of letting Postgres's own opaque
error be the first sign of trouble, and that the storage-pool step could
size its own suggested default off the pool's live free space. Both are
real, reasonable follow-ups but are separate, not-yet-scoped features —
out of scope for this fix, which only raises the static default.

**Acceptance**: `grep 'DEFAULT_DISK_GB=' ct/create.sh` shows `"20"`. No
behavior change to the Advanced path's own disk-size prompt (still shows
this as its pre-filled default, still fully editable).

---

## Fix 2 — Item 32: filter Proxmox's own per-container firewall bridges out of the live bridge-enumeration menu

**Where**: `ct/create.sh:64-75` (`_enumerate_bridges()`).

**Current**:
```bash
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

**Problem**: on a host with per-container firewalling enabled, `ip -o link
show type bridge` also lists the auto-created `fwbrXXXiY` bridges Proxmox
creates for *other* containers' firewall rules (e.g. `fwbr101i0`,
`fwbr106i0`) — not just real switch/uplink bridges (`vmbr0`) a new
container should actually attach to. Picking one of these by mistake would
create a container with effectively no working uplink. Nothing in the
current menu distinguishes them from a real bridge.

**Fix**: exclude Proxmox's own fixed `fwbrNNNiM` naming convention (always
`fwbr` + digits + `i` + digits) from the kernel-bridge loop:
```bash
_enumerate_bridges() {
    BRIDGE_MENU_OPTS=()
    local _br _vnet
    while IFS= read -r _br; do
        [ -n "$_br" ] || continue
        case "$_br" in
            fwbr[0-9]*i[0-9]*) continue ;;  # item 32: Proxmox's own per-container firewall bridge, not a real uplink
        esac
        BRIDGE_MENU_OPTS+=("$_br" "kernel bridge")
    done < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1)
    if [ -f /etc/pve/sdn/vnets.cfg ]; then
        while IFS= read -r _vnet; do
            [ -n "$_vnet" ] && BRIDGE_MENU_OPTS+=("sdn:${_vnet}" "SDN vnet")
        done < <(awk '/^vnet:/{print $2}' /etc/pve/sdn/vnets.cfg 2>/dev/null)
    fi
}
```
The `case` pattern `fwbr[0-9]*i[0-9]*` matches bash's own glob syntax
(this file uses `case`/glob patterns elsewhere, e.g. the URL-scheme
matching in the Advanced path's ollama/clone-adjacent logic — consistent
with the file's existing idiom, not a new pattern style). Verify it
matches `fwbr101i0`/`fwbr106i0`/`fwbr107i0` (the report's own examples)
and does NOT match a real bridge name like `vmbr0` or an operator-named
bridge like `vmbr1`.

**Acceptance**: given a host with both `vmbr0` and one or more
`fwbrNNNiM`-pattern interfaces present (`ip -o link show type bridge`),
`_enumerate_bridges()`'s resulting `BRIDGE_MENU_OPTS` contains `vmbr0` but
none of the `fwbrNNNiM` entries.

## Affected areas
`ct/create.sh` only, two independent, non-overlapping edits (a constant
value and a filter added to one existing loop).

## Risk / rollback notes
Both changes are small and low-risk. Fix 1 only changes a default value
(fully overridable by the operator either way, in both Default and
Advanced modes). Fix 2 only narrows what's already a live-enumerated,
operator-reviewed menu — worst case of an over-broad filter pattern is
hiding a real bridge that happens to look like the excluded pattern,
which is why the acceptance criterion above explicitly checks the filter
against a real bridge name too, not just the excluded ones. Plain `git
revert` if anything regresses.
