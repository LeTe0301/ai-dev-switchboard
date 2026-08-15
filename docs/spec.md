# Spec: Install wizard UI — part 2: Default/Advanced entry fork (BACKLOG item 15, piece 1)

## Summary
Add a `whiptail --menu` entry fork ("Default Install" / "Advanced Install")
at the very start of `ct/create.sh`'s prompt sequence, mirroring
community-scripts/ProxmoxVE's `install_script()` menu: **Advanced** walks
the exact prompt sequence `ct/create.sh` already has today, completely
unchanged (including part 1's optional-feature checklist and its
taiga/ollama follow-ups); **Default** skips every prompt that already has
a sensible built-in default, enables zero optional features, and lands the
operator at one final confirmation screen before `pct create` runs.

## Routing note
Workflow: `workflows/feature.md`. Single file (`ct/create.sh`), single
architectural layer (the `whiptail` TUI prompt sequence) — no schema, API,
or web-UI layer involved, matching part 1's own routing reasoning. Routes
through **ux-designer** first for the same reason part 1 did: not for
visual design (`ui-ux-pro-max`'s normal tooling doesn't apply to a TUI),
but for entry-menu copy, the two option descriptions, and the exact wording
of the new Default-path confirmation `msgbox` — refining what's drafted
below, not inventing new visual design.

## Goals
- New `whiptail --menu` shown immediately after the existing intro `msg()`
  call (today's `ct/create.sh:32`), before what's currently the CTID
  `ask()` at line 34: two options, `default` and `advanced`, each with a
  short one-line description (see "Proposed approach" for draft copy;
  ux-designer owns final wording).
- **Advanced** path: today's existing flow, unchanged in prompt text,
  order, and default values — every `ask`/`menu`/`whiptail --checklist`
  call between today's lines 34-158 (CTID through the optional-feature
  checklist, taiga/ollama follow-ups, and `PUBLISH_MODE`/`BASE_URL`) keeps
  running exactly as it does today. This is the "don't regress the
  existing flow" anchor — Advanced is the pre-existing code path relocated
  behind a menu choice, not a rewrite.
- **Default** path: a new branch that, with zero additional dialogs beyond
  one final confirmation screen:
  - Resolves `CTID` via the same `pvesh get /cluster/nextid` (fallback
    `900`) logic the CTID prompt already uses today — computed, not
    asked, since it's inherently host-specific/dynamic and can't be a
    static literal the way the other fields below are.
  - Uses the exact literal default value each other currently-`ask()`ed
    field already has today: hostname `ai-dev-switchboard`, storage
    `local-lvm`, disk `8`, cores `2`, memory `2048`, bridge `vmbr0`,
    ipconfig `dhcp`, template storage `local`, run-user `dev`.
  - Sets `AUTH_MODE=pve` (checks the switchboard web UI login against this
    Proxmox host's own real PVE credentials — an already-shipped auth mode,
    just selected without asking) rather than `simple`, so no
    username/password needs to be collected or generated for this path at
    all. See "Open questions" #1 for the reasoning and the alternative
    considered.
  - Sets `PUBLISH_MODE=none` / `BASE_URL=""` — matches the existing menu's
    own first-listed, zero-follow-up option.
  - Skips part 1's optional-feature checklist entirely: all four
    `WITH_GIT_HOSTING`/`WITH_CODE_SERVER`/`WITH_TAIGA`/`WITH_OLLAMA` stay
    `0`, no taiga or ollama follow-up screens are shown. See "Open
    questions" #2 for why this is gated behind Default/Advanced rather
    than shown unconditionally — grounded in how community-scripts'
    own `build.func` treats per-app feature toggles (folded into
    `advanced_settings()`'s own step walk; Default mode shows zero of
    them and silently uses whatever's absent-means-off).
  - Shows exactly one `whiptail --msgbox` immediately before container
    creation, summarizing every resolved value, so the operator gets one
    last look before anything is created (mirrors community-scripts' own
    `echo_default` pre-build summary).
- Extract the field literals used above into named `DEFAULT_*` constants
  (`DEFAULT_CT_HOSTNAME`, `DEFAULT_STORAGE`, `DEFAULT_DISK_GB`,
  `DEFAULT_CORES`, `DEFAULT_MEM_MB`, `DEFAULT_BRIDGE`, `DEFAULT_IPCONFIG`,
  `DEFAULT_TEMPLATE_STORAGE`, `DEFAULT_RUN_USER`) read by *both* paths —
  Advanced's `ask()` calls pass `"$DEFAULT_*"` as their pre-fill instead of
  a repeated literal, and Default's branch assigns from the same variable
  directly. This is the mechanical device that keeps the two paths from
  silently drifting apart if one is edited later without the other.
- A shared `default_ctid()` function (today's
  `pvesh get /cluster/nextid 2>/dev/null || echo 900` one-liner, extracted)
  used by both the Advanced CTID prompt's pre-fill and the Default path's
  silent resolution — same reasoning as the `DEFAULT_*` constants above.

## Non-goals
- **Pieces 2-4 of BACKLOG item 15 are explicitly deferred to a later
  part**: live storage-pool enumeration (piece 2), live network-bridge
  enumeration (piece 3), CTID/hostname pre-validation before `pct create`
  (piece 4). See "Deferred to a later part" below for why this cycle does
  not bundle them in.
- Any change to the Advanced path's actual prompt sequence, wording, field
  ordering, or default values beyond sourcing them from the new
  `DEFAULT_*` constants — Advanced is a relocation of existing code, not a
  redesign.
- Any change to `install.sh`, `app/`, or any web UI code.
- App-defaults save/reuse file, a "User Defaults" / "Settings" menu entry
  — community-scripts' own entry menu has these; item 15's backlog entry
  already excludes them explicitly ("Explicitly out of scope for this
  item"), unchanged by this spec. The entry menu here has exactly two
  options: Default, Advanced.
- Auto-generating a `SIMPLE_PASSWORD` for the Default path (considered and
  rejected — see "Open questions" #1). `AUTH_MODE=pve` is used instead,
  which needs no generated secret at all.
- Building or fixing the "non-interactive... CT_* / SWB_* env vars... see
  the 'non-interactive' block near the bottom" feature the file's own
  header comment (`ct/create.sh:13-16`) currently promises but which does
  not exist anywhere in the file today (verified: no `CT_*`/`SWB_*` env
  var is read anywhere in the current 240-line file beyond `REPO_URL`/
  `REPO_BRANCH`). This is a pre-existing inconsistency, not introduced by
  this change or by part 1 — flagged under "Open questions" #3, not fixed
  here; a real env-var-driven non-interactive mode is a materially
  different feature from this spec's whiptail-based Default/Advanced fork
  and deserves its own scoping pass if pursued.

## Deferred to a later part
Pieces 2 (live storage-pool enumeration), 3 (live network-bridge
enumeration), and 4 (CTID/hostname pre-validation, hard-block per part 1's
already-settled reasoning) are **not** built in this cycle.

This cycle's own open question was whether piece 1 has enough of a
structural dependency on 2-4 that it needs to bundle them in, or risks
being a hollow shell without them. Resolved: **no bundling needed.**
Piece 1's Default path proves out a real, non-hollow behavioral difference
using only today's *already-existing* static defaults (zero-prompt vs. a
full walk) — it does not need pieces 2-4 to exist to be meaningful. The
dependency actually runs the other way: pieces 2-4 are enhancements that
only make sense *inside* the Advanced branch this spec establishes (Default
never shows a storage/bridge prompt to enumerate, or a CTID/hostname field
to validate, in the first place — those prompts simply don't run under
Default). So piece 1 first, then 2-4 layered into the now-existing Advanced
branch, is the correct build order, not the reverse. A future
product-manager pass picks up pieces 2-4 as **part 3** once this part 2 has
shipped and been reviewed.

## Background / current state
`ct/create.sh` (240 lines after part 1) is a flat `whiptail`-based TUI: one
intro `msg()`, then a strictly sequential run of `ask`/`menu`/
`whiptail --checklist` calls with no branching — CTID, hostname, storage,
disk, cores, memory, bridge, ipconfig, template storage, run-user
(`ct/create.sh:34-44`); auth mode + credentials (`46-55`); the
part-1-shipped optional-feature checklist plus taiga/ollama follow-ups
(`57-149`); publish mode (`151-158`); then TOTP secret generation, template
resolution, `pct create`/`pct start`, in-container bootstrap, and the final
`SUMMARY` msgbox (`160-239`), none of which is touched by this spec — the
fork only spans lines 34-158, everything from `TOTP_SECRET=...` (line 160)
onward is shared, unchanged code regardless of which path was taken.

Item 15's backlog entry (`docs/BACKLOG.md`, "## 15. Install wizard UI")
documents, from a direct read of community-scripts/ProxmoxVE's actual
`misc/build.func` source, that its `install_script()` shows a
`whiptail --menu` with **Default Install / Advanced Install / User
Defaults / App Defaults (if saved) / Settings**: Default calls
`base_settings()` and proceeds immediately with zero further prompts;
Advanced additionally walks `advanced_settings()`, an explicit step
state-machine covering every container-spec field. This spec adopts the
Default/Advanced fork itself (piece 1), not the state-machine/step-back
navigation (that's a separate, larger piece of the researched pattern,
not part of item 15's scoped pieces 1-5) or the User-Defaults/App-Defaults
entries (explicitly excluded, see "Non-goals").

Re-checked directly against `misc/build.func` this session (not assumed
from memory) for how community-scripts itself handles the specific
question this cycle needed answered — is a feature-toggle checklist a
separate concept from the Default/Advanced container-spec fork, or does
everything go through the one fork: their `default_var_settings()` shows
**no** interactive prompts of any kind, loading everything (including
optional per-app toggles like `var_fuse`/`var_tun`/`var_gpu`) from
`ENV var_* > default.vars > built-ins` — absent means off. There is **no
dedicated checklist UI** for feature toggles in their framework; toggles
that do get asked interactively are folded into `advanced_settings()`'s
own step walk (individual yes/no prompts among its other steps), gated
behind Advanced exactly the same as every container-spec field. This
directly answers this cycle's second open question (see Goals, and "Open
questions" #2): the precedent is to gate optional-feature prompting behind
Default/Advanced, not to treat it as orthogonal.

## Proposed approach

### 1. Entry menu (new, inserted after today's `ct/create.sh:32`)
```bash
INSTALL_MODE=$(whiptail --title "ai-dev-switchboard" --menu \
    "How do you want to configure this container?" 15 74 2 \
    "default"  "Recommended settings, zero extra prompts" \
    "advanced" "Walk every setting yourself (container specs + optional features)" \
    3>&1 1>&2 2>&3)
```
(ux-designer to refine the two description strings and title/box sizing;
draft copy above conveys the mechanism only.)

### 2. Shared defaults (new, above the fork)
```bash
DEFAULT_CT_HOSTNAME="ai-dev-switchboard"
DEFAULT_STORAGE="local-lvm"
DEFAULT_DISK_GB="8"
DEFAULT_CORES="2"
DEFAULT_MEM_MB="2048"
DEFAULT_BRIDGE="vmbr0"
DEFAULT_IPCONFIG="dhcp"
DEFAULT_TEMPLATE_STORAGE="local"
DEFAULT_RUN_USER="dev"

default_ctid() {
    pvesh get /cluster/nextid 2>/dev/null || echo 900
}
```

### 3. The fork itself (replaces today's `ct/create.sh:34-158`)
```bash
if [ "$INSTALL_MODE" = "default" ]; then
    CTID=$(default_ctid)
    CT_HOSTNAME="$DEFAULT_CT_HOSTNAME"
    STORAGE="$DEFAULT_STORAGE"
    DISK_GB="$DEFAULT_DISK_GB"
    CORES="$DEFAULT_CORES"
    MEM_MB="$DEFAULT_MEM_MB"
    BRIDGE="$DEFAULT_BRIDGE"
    IPCONFIG="$DEFAULT_IPCONFIG"
    TEMPLATE_STORAGE="$DEFAULT_TEMPLATE_STORAGE"
    RUN_USER="$DEFAULT_RUN_USER"

    AUTH_MODE="pve"
    SIMPLE_USERNAME=""
    SIMPLE_PASSWORD=""

    WITH_GIT_HOSTING=0
    WITH_CODE_SERVER=0
    WITH_TAIGA=0
    WITH_OLLAMA=0
    OLLAMA_BASE_URL_NORM=""
    OLLAMA_MODEL_INPUT=""

    PUBLISH_MODE="none"
    BASE_URL=""

    whiptail --title "ai-dev-switchboard" --msgbox "About to create:\n\n  CTID: ${CTID}\n  Hostname: ${CT_HOSTNAME}\n  Storage: ${STORAGE} (${DISK_GB}G disk)\n  CPU / RAM: ${CORES} cores / ${MEM_MB}MB\n  Network: bridge ${BRIDGE}, ${IPCONFIG}\n  Run-as user: ${RUN_USER}\n  Login: your Proxmox VE credentials\n  Optional features: none enabled\n  Terminal publishing: loopback only\n\nPress Enter to create it, or Cancel to abort." 20 74
else
    CTID=$(ask "Container ID (must be free):" "$(default_ctid)")
    CT_HOSTNAME=$(ask "Hostname:" "$DEFAULT_CT_HOSTNAME")
    STORAGE=$(ask "Storage pool for the container's root disk:" "$DEFAULT_STORAGE")
    DISK_GB=$(ask "Disk size (GB):" "$DEFAULT_DISK_GB")
    CORES=$(ask "CPU cores:" "$DEFAULT_CORES")
    MEM_MB=$(ask "Memory (MB):" "$DEFAULT_MEM_MB")
    BRIDGE=$(ask "Network bridge:" "$DEFAULT_BRIDGE")
    IPCONFIG=$(ask "IP config (dhcp, or e.g. 192.168.1.50/24,gw=192.168.1.1):" "$DEFAULT_IPCONFIG")
    TEMPLATE_STORAGE=$(ask "Storage where the Debian 12 container template lives/downloads to:" "$DEFAULT_TEMPLATE_STORAGE")
    RUN_USER=$(ask "Unprivileged user to run coding sessions as (created inside the container):" "$DEFAULT_RUN_USER")

    AUTH_MODE=$(menu "How should the web UI authenticate you?" \
        "simple" "A single username + password you set now" \
        "pve"    "Real Proxmox VE credentials (checked against this host's API)")

    SIMPLE_USERNAME=""
    SIMPLE_PASSWORD=""
    if [ "$AUTH_MODE" = "simple" ]; then
        SIMPLE_USERNAME=$(ask "Web UI username:" "admin")
        SIMPLE_PASSWORD=$(askpw "Web UI password:")
    fi

    # ...today's existing checklist block (ct/create.sh:57-78), taiga
    # follow-up (80-82), and ollama follow-up (84-149) — unchanged, moved
    # verbatim into this branch...

    PUBLISH_MODE=$(menu "How should per-project ttyd/VS Code terminals be published beyond this container?" \
        "none"      "Loopback only — you handle exposing them yourself" \
        "tailscale" "Publish via 'tailscale serve --set-path' (requires tailscale installed+logged in later)")

    BASE_URL=""
    if [ "$PUBLISH_MODE" = "tailscale" ]; then
        BASE_URL=$(ask "Tailnet hostname per-project terminals get published under (see 'tailscale status' inside the container later — leave blank to fill in afterward):" "")
    fi
fi
```
Everything from today's `TOTP_SECRET=...` (`ct/create.sh:160`) onward is
untouched, outside this `if`/`else`, and runs identically regardless of
which branch executed.

## Affected areas
- `ct/create.sh` only — single file, the prompt-sequence section
  (`ct/create.sh:32-158` today). No schema, API, or web UI code involved.
- No changes to `install.sh`, `app/`, or `config/`.

## Edge cases
- **Cancel/Esc at the new entry menu itself**: `whiptail --menu`'s Cancel
  exit code makes the `INSTALL_MODE=$(...)` assignment fail under
  `set -euo pipefail`, aborting the whole run before anything else runs —
  consistent with every other dialog's existing Cancel-aborts behavior in
  this file.
- **Default path, `pvesh get /cluster/nextid` fails** (host not clustered,
  API transiently unreachable): falls back to `900`, identical to today's
  existing CTID-prompt fallback — no new failure mode.
- **Default path, resolved CTID collides with an existing container**
  (race, stale `nextid`, or simply already in use): `pct create` fails
  with Proxmox's own real error — identical to today's unvalidated-CTID
  behavior in the existing flow; catching this ahead of time is
  explicitly piece 4's job (deferred, see "Deferred to a later part").
- **Default path, `local-lvm`/`local` don't actually exist on this
  particular host**: `pveam`/`pct create` fail with Proxmox's own real
  error — identical to today's unvalidated-storage behavior; catching this
  ahead of time is explicitly piece 2's job (deferred).
- **Default path final confirmation msgbox, Cancel pressed**: aborts the
  whole run, no container created — same Cancel-aborts precedent as every
  other dialog in the file, including the pre-existing one (see part 1's
  own reviewer finding) that whiptail's Cancel and "No" are
  indistinguishable inside an `if whiptail --yesno; then...else...fi`
  idiom; this new confirmation uses a plain `--msgbox` (informational,
  single dismiss button) so no such ambiguity applies here.
- **Advanced path chosen**: zero behavior change versus pre-change
  `ct/create.sh` — a diff of the Advanced branch's body against the
  pre-change file (outside the `if`/`else` wrapper and the
  literal-to-`$DEFAULT_*`-variable substitutions) should show no
  structural change: same prompts, same order, same resolved default
  values.
- **No live switching between Default and Advanced mid-run**: once a path
  is chosen and begins, there is no back-navigation to the entry menu —
  consistent with this file's existing lack of step-back navigation
  elsewhere (noted as a known gap in the backlog's own research, not
  something this piece adds or removes).

## Acceptance criteria
- [ ] Given the operator selects "Default Install", when the script
      proceeds, then no `ask`/`menu`/`whiptail --checklist` dialog is shown
      for CTID, hostname, storage, disk, cores, memory, bridge, ipconfig,
      template storage, run-user, auth mode, optional features, or publish
      mode — only the entry menu itself and the one final confirmation
      `msgbox` are shown.
- [ ] Given "Default Install", when the script proceeds, then
      `AUTH_MODE=pve`, all four `WITH_*` flags are `0`,
      `OLLAMA_BASE_URL_NORM`/`OLLAMA_MODEL_INPUT` are empty,
      `PUBLISH_MODE=none`, `BASE_URL=""`, `RUN_USER=dev`, and every other
      field equals its corresponding `DEFAULT_*` constant (hostname
      `ai-dev-switchboard`, storage `local-lvm`, disk `8`, cores `2`,
      memory `2048`, bridge `vmbr0`, ipconfig `dhcp`, template storage
      `local`), verifiable by extracting the Default branch into a
      standalone harness with `ask`/`menu`/`whiptail` stubbed out (same
      technique part 1's reviewer used) and asserting on the resulting
      variable values.
- [ ] Given "Advanced Install", when the script proceeds, then every
      dialog from today's existing flow (CTID through the ollama
      follow-up through publish mode) is shown, in the same order, with
      the same prompt text and the same default value as before this
      change — confirmed by diffing the Advanced branch's body against
      pre-change `ct/create.sh:34-158`.
- [ ] Given either path, when it completes, then both converge on the
      identical set of variable names and both leave every one of them
      set to a defined (possibly empty-string) value before reaching
      today's unchanged `TOTP_SECRET=...` line — no variable is
      conditionally undefined depending on which path ran.
- [ ] Given "Default Install", when the final confirmation `msgbox` is
      shown, then it lists the resolved CTID, hostname, storage+disk,
      cores+memory, bridge+ipconfig, run-user, "your Proxmox VE
      credentials" for login, "none enabled" for optional features, and
      "loopback only" for publishing — before any `pct create` call is
      reached in the script.
- [ ] `bash -n ct/create.sh` and `shellcheck ct/create.sh` both pass with
      zero errors and no new warnings versus the pre-change file, matching
      part 1's own testing bar.

## Open questions
1. **Default path's `AUTH_MODE`: `pve` vs. auto-generated `simple`
   credentials.** Assumption this spec proceeds under: `AUTH_MODE=pve`.
   Considered and rejected: defaulting to `simple` with an auto-generated
   password (mirroring the existing `TOTP_SECRET` auto-generation
   precedent at `ct/create.sh:160`) — rejected because it adds a new
   secret-generation code path and a new "write this down, it's shown only
   once" burden for zero benefit over `pve`, which needs no new secret at
   all: the operator already has Proxmox VE credentials for this host (a
   precondition for running the wizard in the first place), so reusing
   them is strictly less friction. If the user disagrees at review time,
   this is a small, isolated change (swap the three `AUTH_MODE`/
   `SIMPLE_USERNAME`/`SIMPLE_PASSWORD` lines in the Default branch).
2. **Gating the part-1 optional-feature checklist behind Default/Advanced,
   vs. showing it unconditionally regardless of mode.** Resolved, not
   left open: gated (Default = skip entirely, matches every flag's
   existing off-by-default posture; Advanced = shown exactly as today).
   Grounded in a direct re-check of community-scripts' own `build.func`
   this session (see "Background / current state") — their own framework
   treats per-app optional-feature toggles as folded into the
   Default/Advanced fork, not orthogonal to it, and Default mode shows
   zero of them. Recorded here so a future pass doesn't need to
   re-research this.
3. **Stale header comment referencing a non-existent "non-interactive...
   CT_*/SWB_* env vars" block.** Discovered while reading the current
   file (`ct/create.sh:13-16` promises a block that doesn't exist anywhere
   in the 240-line file). Not fixed in this spec — a real env-var-driven
   non-interactive mode is a different, larger feature than this spec's
   whiptail Default/Advanced fork. Flagging for a future bugfix/feature
   pass to either build that block or correct the comment; either is a
   reasonable fix, but picking one is a product decision outside this
   spec's scope.
4. **Exact copy for the entry menu's two option descriptions and the
   Default-path confirmation `msgbox`.** Left to ux-designer per this
   item's own established routing pattern (part 1 routed dialog-flow/copy
   decisions to ux-designer the same way); draft copy above is a
   placeholder conveying mechanism, not finalized wording.

## Risk / rollback notes
Single-file change to a script with no persisted state of its own (it only
writes into the container it creates) and no CI dependency on it (part 1's
own test-review confirmed `tests/` has no reference to `create.sh`).
Rollback is a plain `git revert`/checkout of `ct/create.sh` to its
pre-change version — no data migration, no downstream code depends on the
new `INSTALL_MODE` variable or the `DEFAULT_*` constants existing. The
main risk is the Advanced branch silently drifting from today's exact
behavior during the literal-to-`$DEFAULT_*`-variable refactor described in
"Proposed approach" #3 — mitigated by the diff-based acceptance criterion
above, which the reviewer can check mechanically against pre-change
`ct/create.sh`.
