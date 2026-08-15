# Design: Install wizard UI — part 2, piece 1 (Default/Advanced entry fork)

## Summary

Two new whiptail dialogs inserted into `ct/create.sh` before the first `ask()` call: a two-option menu fork (Default/Advanced) with clear, unambiguous descriptions of each path, and a final summary `msgbox` for the Default path before `pct create` runs. The Default path is presented as "create with built-in settings" (not a preview), and its auth mode explicitly references "existing Proxmox VE credentials" to prevent login-surprise later.

## Design notes

This is a TUI (terminal user interface) feature, not a web UI. Design work here focuses on **dialog copy clarity and flow**, not visual styling — `ui-ux-pro-max` visual tooling does not apply. The whiptail TUI is already part of the project (established at `ct/create.sh:26-30`) and uses a fixed-width terminal box model (`--inputbox`/`--menu`/`--msgbox` with width 74 chars, variable heights). All new dialogs conform to the existing helper-function pattern and title ("ai-dev-switchboard").

**No new components or design tokens introduced** — only refined wording and structural placement of dialogs already in scope.

---

## Dialog 1: Entry menu (new, inserted after `ct/create.sh:32`)

### Structure and wireframe

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                              │
│                                                                                 │
│ How do you want to configure this container?                                    │
│                                                                                 │
│   ⊙ default   Create with built-in defaults (fully automated, no prompts)       │
│   ○ advanced  Walk through every setting (container specs + optional features) │
│                                                                                 │
│                                           <  OK  >   <  Cancel  >              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Implementation details

```bash
INSTALL_MODE=$(whiptail --title "ai-dev-switchboard" --menu \
    "How do you want to configure this container?" 15 74 2 \
    "default"  "Create with built-in defaults (fully automated, no prompts)" \
    "advanced" "Walk through every setting (container specs + optional features)" \
    3>&1 1>&2 2>&3)
```

### Copy decisions and rationale

**Title:** `"ai-dev-switchboard"` — consistent with all other dialogs in the file (established at `ct/create.sh:26`), no change.

**Prompt text:** `"How do you want to configure this container?"` — clear, question form, neutral tone.

**Option 1 (default):**
- **Text:** `"Create with built-in defaults (fully automated, no prompts)"`
- **Character count:** 55 (fits within 74-char max ✓)
- **Rationale:** 
  - "Create with" makes explicit that this creates a real container (not a preview or dry-run), addressing spec's open question #3.
  - "built-in defaults" refers to the literal `DEFAULT_*` constants in the code, a familiar term for operators.
  - "(fully automated, no prompts)" removes ambiguity in "zero extra prompts" — "extra" confused the distinction between "no prompts total" vs. "no extra customization." Explicit "no prompts" + "fully automated" clarifies the path is entirely non-interactive (except for the final confirmation).
  - Parenthetical format (non-essential clarification) follows standard UX copy convention.

**Option 2 (advanced):**
- **Text:** `"Walk through every setting (container specs + optional features)"`
- **Character count:** 61 (fits within 74-char max ✓)
- **Rationale:**
  - "Walk through" is the established phrase in `ct/create.sh:33` ("Walks you through"), matching the operator's mental model if they've read the intro.
  - "every setting" is concrete and symmetric to Default's "no prompts" — if you pick Advanced, you get *all* the prompts.
  - "(container specs + optional features)" parallels the existing terminology in part 1's optional-feature checklist and clarifies that Advanced includes both infrastructure choices *and* the part-1-shipped feature toggles.

### State and behavior

- **Initial state:** menu selection defaults to "default" (first option) — whiptail convention.
- **Cancel/Esc:** causes `whiptail --menu` to return non-zero exit, failing the `INSTALL_MODE=$(...)` assignment under `set -euo pipefail`, aborting the entire script before any other prompts run — consistent with today's existing Cancel behavior across all dialogs in the file.
- **Selection:** returns either `"default"` or `"advanced"` (string), used by the `if [ "$INSTALL_MODE" = "default" ]` fork below.

---

## Dialog 2: Default-path confirmation msgbox (new, in the Default branch before container creation)

### Structure and wireframe

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                                       │
│                                                                          │
│ About to create:                                                         │
│                                                                          │
│   CTID: 100                                                              │
│   Hostname: ai-dev-switchboard                                           │
│   Storage: local-lvm (8G disk)                                           │
│   CPU / RAM: 2 cores / 2048MB                                            │
│   Network: bridge vmbr0, dhcp                                            │
│   Run-as user: dev                                                       │
│   Web UI login: your existing Proxmox VE credentials                     │
│   Optional features: none enabled                                        │
│   Terminal publishing: loopback only                                     │
│                                                                          │
│ Press Enter to create it, or Cancel to abort.                            │
│                                                                          │
│                                         <  OK  >                         │
└──────────────────────────────────────────────────────────────────────────┘
```

### Implementation details

```bash
whiptail --title "ai-dev-switchboard" --msgbox \
    "About to create:\n\n  CTID: ${CTID}\n  Hostname: ${CT_HOSTNAME}\n  Storage: ${STORAGE} (${DISK_GB}G disk)\n  CPU / RAM: ${CORES} cores / ${MEM_MB}MB\n  Network: bridge ${BRIDGE}, ${IPCONFIG}\n  Run-as user: ${RUN_USER}\n  Web UI login: your existing Proxmox VE credentials\n  Optional features: none enabled\n  Terminal publishing: loopback only\n\nPress Enter to create it, or Cancel to abort." 20 74
```

### Line-by-line breakdown and content fit

| Line | Content | Char count (max: 74) | Notes |
|------|---------|--------|---|
| 1 | `About to create:` | 15 | ✓ |
| 2 | (blank) | — | ✓ |
| 3 | `  CTID: 100` | ~13 (CTID is 3 digits max) | ✓ |
| 4 | `  Hostname: ai-dev-switchboard` | 32 | ✓ |
| 5 | `  Storage: local-lvm (8G disk)` | 30 | ✓ |
| 6 | `  CPU / RAM: 2 cores / 2048MB` | 29 | ✓ |
| 7 | `  Network: bridge vmbr0, dhcp` | 31 | ✓ |
| 8 | `  Run-as user: dev` | 19 | ✓ |
| 9 | `  Web UI login: your existing Proxmox VE credentials` | 54 | ✓ (see rationale below) |
| 10 | `  Optional features: none enabled` | 34 | ✓ |
| 11 | `  Terminal publishing: loopback only` | 36 | ✓ |
| 12 | (blank) | — | ✓ |
| 13 | `Press Enter to create it, or Cancel to abort.` | 47 | ✓ |

**Total logical lines:** 13. **Msgbox height:** 20 rows. Whiptail allocates 3 rows for border/title; remaining 17 rows accommodate 13 lines of content comfortably, with ~4 rows of breathing room. **No truncation risk.** ✓

### Copy decisions and rationale

**Heading:** `"About to create:"` — mirrors the `echo_default` pre-build summary pattern from community-scripts (spec background section), establishing it as a familiar "last chance to review" checkpoint.

**CTID, Hostname, Storage, etc. fields:**
- **Format:** Two-space indentation, colon-separated label/value, using the exact variable names from the Default branch (`${CTID}`, `${CT_HOSTNAME}`, etc.) — allows operator to see the *resolved* values, not just prompts.
- **"Storage: ${STORAGE} (${DISK_GB}G disk)"** — combines two related fields (storage pool + disk size) on one line for compactness, using parenthetical for the secondary detail (follows the entry-menu copy convention).
- **"CPU / RAM: ${CORES} cores / ${MEM_MB}MB"** — similarly combines vCPU + memory in a readable format. "cores" and "MB" are familiar to the operator demographic (Proxmox admins). "vCPU" would be overly precise; "RAM" alone would omit the unit.
- **"Network: bridge ${BRIDGE}, ${IPCONFIG}"** — shows both the Layer-2 bridge (vmbr0) and Layer-3 config (dhcp or static IP) on one line. Operator sees "what bridge am I plugged into" and "will I get an IP how" in one glance.

**Web UI login (addressing spec's open question #4):**
- **Text:** `"Web UI login: your existing Proxmox VE credentials"`
- **Rationale:**
  - Replaces the spec's draft "Login: your Proxmox VE credentials" with two improvements:
    1. Prefix "Web UI" to clarify this is authentication to the container's web interface (not an SSH login, not the physical Proxmox host).
    2. Add "existing" before "Proxmox VE credentials" to make explicit that no new username/password is being created or generated for this container — the operator will use their current PVE login. This prevents the surprise mentioned in open question #4: when the web UI later asks for login, the operator will already know to reach for their PVE credentials instead of expecting a new username/password from this wizard.
  - The possessive "your" + explicit "existing" = unambiguous that this is reusing current credentials, not a new credential.

**Optional features and Terminal publishing:**
- **`"Optional features: none enabled"`** — explicitly states all four toggles (git-hosting, code-server, taiga, ollama) are off, matching the Default path's design (no interactive feature checklist). Clear and prevents operator surprise if they later expect these to be available.
- **`"Terminal publishing: loopback only"`** — parallels the existing publish-mode menu's first option ("Loopback only — you handle exposing them yourself"). Operator sees Default uses the no-publishing default, and knows they can expose via SSH tunnels, reverse proxy, or other manual means later.

**Final instruction:** `"Press Enter to create it, or Cancel to abort."` 
- Explicit, action-oriented.
- Reminds operator that pressing Cancel here still aborts (no container is created yet) — mirrors the spec's edge-case handling ("Cancel pressed: aborts the whole run, no container created").
- Familiar whiptail msgbox idiom.

### State and behavior

- **Shown only if `INSTALL_MODE = "default"`** — Advanced branch skips this dialog entirely and goes straight into its `ask()` chain.
- **All variables resolved:** By the time this msgbox is displayed, `CTID`, `CT_HOSTNAME`, `STORAGE`, `DISK_GB`, `CORES`, `MEM_MB`, `BRIDGE`, `IPCONFIG`, `RUN_USER`, and all the `WITH_*` / `PUBLISH_MODE` / `BASE_URL` fields are set. The msgbox expands them in-place with `${VAR}` syntax.
- **Cancel/Esc:** causes `whiptail --msgbox` to return non-zero exit, aborting the script before `pct create` is called — consistent with spec's edge case and existing dialog Cancel behavior.
- **Enter/OK:** user accepts, script continues to the unchanged code section (TOTP secret generation, template resolution, `pct create`, etc.), shared by both paths.

---

## Component reuse

- Reused: `msg()`, `ask()`, `menu()` helper functions (existing, defined at `ct/create.sh:26-30`) — for consistent styling, title, and error handling across all dialogs.
- Reused: whiptail's `--msgbox` (existing, part of the project's standard whiptail usage) — for the Default-path confirmation dialog.
- Reused: `--menu` directive (existing, already used for auth-mode and publish-mode selection at lines 46-48 and 151-158) — for the entry fork itself.
- No new terminal UI library, color system, or component framework introduced.

---

## Accessibility & platform notes

### Terminal environment

- **Character width constraints:** All text constrained to 74 chars per line (established whiptail box width across the file) to fit standard 80-char terminals with margins. Verified above per dialog.
- **Text clarity over visual polish:** No color, graphics, or decorative elements possible in whiptail TUI — clarity depends entirely on copy, line breaks, and indentation.
- **Screen reader compatibility:** Terminal environment; text is inherently accessible to terminal screen readers (Jaws, NVDA, VoiceOver terminal mode) — no non-semantic markup that would obstruct readability.

### Wording choices for accessibility

- **Avoided metaphors:** "Create with built-in defaults" is literal; no "quick-start," "wizard," or other abstract terms that might confuse non-native-English operators.
- **Active voice:** "Create with…," "Walk through…" — actions the operator is choosing, not passive descriptions.
- **Parenthetical clarifications:** Used sparingly to avoid overload, but present to disambiguate (e.g., "no prompts," "your existing credentials").
- **Field names:** Match variable names in the code and existing prompts — operator sees `CTID`, not "Container ID (numeric)" — consistency prevents re-learning.

### Platform-specific notes

- **TUI only:** This feature is a shell script running on a Proxmox VE host (Linux terminal). No web UI, mobile, or GUI equivalent.
- **Operator demographic:** System administrators familiar with SSH, Linux CLIs, and Proxmox. They expect terse, functional dialogs without excessive explanation.
- **No hover states, animations, or responsive design:** TUI dialogs are static text in fixed-width boxes. No interaction beyond menu selection and text input.

---

## Traceability to spec

| Acceptance criterion (from docs/spec.md) | Where it's addressed in this design |
|---|---|
| Entry menu shown immediately after intro `msg()`, before CTID `ask()` (line 34 today) | Dialog 1 structure and placement note above; inserted after `ct/create.sh:32` |
| Two options: "default" and "advanced" with short one-line descriptions | Dialog 1 copy: "default" → "Create with built-in defaults (fully automated, no prompts)"; "advanced" → "Walk through every setting (container specs + optional features)" |
| Default descriptions fit in whiptail menu row width (15 74 2) | Dialog 1: both options verified ≤ 61 chars (fits within 74) ✓ |
| Default path: zero additional dialogs beyond one final confirmation | Dialog 2 is the sole new dialog in Default path; Advanced path unchanged |
| Default path confirmation msgbox lists CTID, hostname, storage+disk, cores+memory, bridge+ipconfig, run-user, login mode, optional features, publishing | Dialog 2: all nine elements present in the wireframe/implementation above |
| Default confirmation msgbox fits in 20 74 box without truncation | Dialog 2: content verified at 13 logical lines (fits within 20-row height with 7 rows buffer) ✓ |
| "Recommended settings, zero extra prompts" wording must clearly convey Default creates a real container (not a dry run, open question #3) | Dialog 1 copy revised to "Create with built-in defaults (fully automated, no prompts)" — "Create with" makes explicit; "no prompts" removes "extra" ambiguity |
| Auth mode wording must make clear Default uses "your existing Proxmox VE login" (not a new credential, open question #4) | Dialog 2 copy: "Web UI login: your existing Proxmox VE credentials" — "existing" added; "Web UI" scope-clarified |
| Cancel at entry menu aborts script | Behavior note: `INSTALL_MODE=$(...)` under `set -euo pipefail` fails on non-zero exit from whiptail; script aborts before any other prompt |
| Cancel at Default confirmation msgbox aborts script before `pct create` | Behavior note: `whiptail --msgbox` returns non-zero on Cancel; script aborts before TOTP/container-create code |
| Advanced path identical to today's flow (prompt text, order, defaults) | Not changed by this design; developer's diff-check against pre-change file verifies this |

---

## Implementation notes for developer

- Extract the nine `DEFAULT_*` constants (hostname, storage, disk, cores, memory, bridge, ipconfig, template storage, run-user) and `default_ctid()` function per spec's "Proposed approach" #2 — used by both Default and Advanced branches.
- Entry menu returns string (`"default"` or `"advanced"`); use `if [ "$INSTALL_MODE" = "default" ]; then...else...fi` to branch.
- Default branch assigns all variables directly from `DEFAULT_*` and function; Advanced branch wraps existing `ask()`/`menu()` calls in the else block (verbatim code moves from today's lines 34-158 into the else branch, with only the literal-to-variable substitution changes).
- Confirmation msgbox in the Default branch uses `${VAR}` expansions; ensure all variables are set before that line is reached.
- No changes to lines 160-239 (TOTP, template resolution, `pct create`, summary) — shared exit path for both branches.

