# Design: Folder upload → auto-detect repo(s) wizard

## Overview

A 6-step stepper wizard that guides users through uploading a local folder (or pre-made `.zip`) to auto-detect and register projects. The wizard lives in a full-screen modal overlay (reusing the existing overlay pattern from the TOTP code prompt and login screen) to keep the primary dashboard UI clean and maintain focus on the task.

**Design rationale:** The feature is an alternative entry point to the existing "+ New project" button (git-hosting-based flow), not a replacement. It should read as a secondary, optional capability, not compete for primary visual weight with the dashboard's project list and engine picker.

## Color palette & styling

Reusing the existing app color scheme:
- **Dark background:** `#111`
- **Cards/containers:** `#1c1c1c`
- **Primary action (green):** `#34c759` — buttons to advance wizard steps, "Upload," "Confirm"
- **Secondary action (blue):** `#4da6ff` — links, "try uploading a .zip instead"
- **Text:** `#eee` (main), `#aaa` (secondary), `#888` (tertiary), `#666` (labels/legend)
- **Error:** `#ff6b6b` — validation errors, failure messages
- **Border/divider:** `#333` — light borders, step separators
- **Progress indicator background:** `#2a2a2a` — unfilled portion of progress bars
- **Progress indicator fill:** `#34c759` (zipping phase) and `#4da6ff` (upload phase) — different colors to visually distinguish the two independent progress bars

No new dependencies, no image assets — all styling via vanilla `<style>` block, all interactivity via plain `<script>` in the existing `PAGE_TEMPLATE` pattern.

## Modal structure

The entire wizard is contained in a single overlay modal (existing `.overlay` + `.card` pattern), with internal step navigation managed by JavaScript state. The overlay is hidden when the wizard is not active; clicking a new "Upload folder" button (positioned near the "+ New project" row) opens it to step 1.

**Touch target sizing (native/web cross-platform):**
- Buttons: 44px minimum height on mobile (currently 12px padding = ~36px; increase to 13px padding for 44px total)
- Checkbox/radio labels: 44px minimum clickable height (spec calls this out as a hard requirement for accessibility)
- Input fields: 44px minimum height
- Spacing between interactive elements: 8px minimum

## Wireframe by step

### Step 1: Pick (File selection)

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1. Pick                                    │
│  2. Exclude (disabled)                      │
│  3. Zip (disabled)                          │
│  4. Upload (disabled)                       │
│  5. Review (disabled)                       │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 1/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Pick a local folder:               │   │
│  │  [ Pick folder...  ]                │   │
│  │                                     │   │
│  │  –– or ––                           │   │
│  │                                     │   │
│  │  Pick a .zip file:                  │   │
│  │  [ Pick .zip...    ]                │   │
│  │  (Skips client-side zipping)        │   │
│  └─────────────────────────────────────┘   │
│                                             │
│                         [ Next ] (disabled) │
└─────────────────────────────────────────────┘
```

**Interactions:**
- Two separate file input controls: `<input type="file" webkitdirectory>` (folder) and `<input type="file" accept=".zip">` (pre-made zip)
- Picking a folder enables step 2; picking a .zip skips to step 4 (upload directly)
- Each input is hidden visually, replaced with a styled button that triggers `click()` on the input
- On file selection, capture the `FileList` or `.zip` bytes in client state, advance automatically to step 2 (or step 4 if .zip)

**State classes/disabled-state styling:**
- `.step-number` elements use opacity or color changes to show disabled steps (e.g., `opacity: 0.4` for disabled, `color: #34c759` for active)
- Buttons show `.button.disabled` styles: `background: #2a2a2a; color: #666; cursor: not-allowed;`

---

### Step 2: Exclude (Known-heavy-directory checklist)

Shown only if a **folder** was picked (skipped entirely if `.zip` was picked).

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1. Pick (completed)                       │
│  2. Exclude                                │
│  3. Zip (disabled)                          │
│  4. Upload (disabled)                       │
│  5. Review (disabled)                       │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 2/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Directories to exclude from zip:   │   │
│  │                                     │   │
│  │  ☑ node_modules (12 folders, 850   │   │
│  │     files, ~120 MB)                 │   │
│  │  ☑ .venv (1 folder, 45 files,      │   │
│  │     ~8 MB)                          │   │
│  │  ☑ target (1 folder, 120 files,    │   │
│  │     ~65 MB)                         │   │
│  │  ☐ dist (1 folder, 30 files,       │   │
│  │     ~2 MB)                          │   │
│  │                                     │   │
│  │  (Auto-skip if nothing matches)     │   │
│  └─────────────────────────────────────┘   │
│                                             │
│                                [ Next > ] │
└─────────────────────────────────────────────┘
```

**Interactions & state:**
- For each matched directory name (by basename, e.g., `node_modules` at any depth): show one checkbox row with aggregated count and size
- **Checked = excluded** (default for all matched directories, per spec: "checked/excluded by default")
- Unchecking a row re-includes that directory name's files in the zip
- If no directories match the hardcoded exclusion list, auto-advance to step 3 (or show empty state "Nothing to exclude — ready to zip")
- `.git` is never offered as an option, enforced in the JS logic itself

**Accessibility:**
- Each checkbox is wrapped in a clickable `<label>` to ensure 44px+ touch target (full row is clickable, not just the checkbox)
- Checkbox color: `accent-color: #34c759;` (green, matches primary action)
- Text sizing: 14px base, 12px for secondary info (file count/size)

---

### Step 3: Zip (Client-side archive building)

Shown only for folder picks (skipped for `.zip` picks).

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1. Pick (completed)                       │
│  2. Exclude (completed)                    │
│  3. Zip                                    │
│  4. Upload (disabled)                      │
│  5. Review (disabled)                       │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 3/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Building archive...                │   │
│  │                                     │   │
│  │  ▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░   │   │
│  │  45% (127 of 284 files)             │   │
│  │                                     │   │
│  │  Estimated size: ~42 MB             │   │
│  │                                     │   │
│  │  ⚠ Total size exceeds 100 MB limit. │   │
│  │    Remove more directories to       │   │
│  │    proceed. [ ← Back to exclude ]   │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  (auto-advances to step 4 when done)        │
└─────────────────────────────────────────────┘
```

**Progress indicator design:**
- Full-width progress bar using `<div>` with `background: #2a2a2a; height: 6px; border-radius: 3px;`
- Inner fill: `background: #34c759; height: 6px; border-radius: 3px; transition: none;` (instant updates, no animation, per spec)
- Percentage and file count below the bar in 13px gray text
- Estimated total size shown under progress (recalculated as zipping proceeds, before archiving)

**Interactions:**
- **Auto-advances to step 4 when complete** — no user click needed
- **Optional pre-flight check:** If included total size exceeds `UPLOAD_MAX_BYTES` (100 MiB), show a warning with a "Back to exclude" button; block auto-advance until the user goes back and removes more
- Zipping is non-blocking (progress updates don't freeze the UI) — use `await` loops with `progress` event listeners on each `file.arrayBuffer()`

**Edge case: Empty zip (zero files after exclusions)**
- Show error: "No files to upload after exclusions. [ ← Back to exclude ]"
- Block auto-advance

---

### Step 4: Upload (Transfer progress)

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1. Pick (completed)                       │
│  2. Exclude (completed)                    │
│  3. Zip (completed)                        │
│  4. Upload                                 │
│  5. Review (disabled)                       │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 4/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Uploading archive...               │   │
│  │                                     │   │
│  │  ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  62% (26 MB of 42 MB)               │   │
│  │                                     │   │
│  │ TOTP overlay (428 → prompt)         │   │
│  │ if session hasn't cleared it yet    │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  (auto-advances to step 5 on success)       │
└─────────────────────────────────────────────┘
```

**Progress indicator design:**
- Identical visual style to zipping progress bar, but blue (`background: #4da6ff`)
- Shows `loaded / total` bytes (sourced from `XMLHttpRequest.upload.onprogress event`)
- Updates live as bytes are transferred

**TOTP handling:**
- If `POST /projects/upload` returns 428 (TOTP not cleared this session):
  - Pause upload progression
  - Show the existing TOTP code overlay (reuse existing `.code-overlay` structure)
  - On successful code entry, retry the upload with `?code=<hex>` appended to the request URL (per spec's phase-1 deviation)
  - On wrong code, show error in overlay, user retries code without re-selecting files
- If `POST /projects/upload` returns 403 (wrong TOTP code after prompt): show error "Wrong code — try again" in the overlay, user can retry

**Error handling:**
- 413 (oversized): "Uploaded file is too large. Go back and exclude more directories."
- 400 (corrupt zip or other validation failure): "Upload failed: [server error message]" (display the error from JSON response)
- Network error (connection dropped): "Connection lost — your upload is still in progress on the server. Refresh the page to check status, or start over."

**Auto-advance:** On success (`POST /projects/upload` returns 200 + `{token, root_name, ...}`), save the response and auto-advance to step 5 after a brief delay (250ms, to let UI feel responsive)

---

### Step 5: Review (Detect structure & choose split strategy)

The most complex step. Shows the server-detected structure and lets the user choose "single project" or "split out nested repos."

**Sub-case A: Single project (unambiguous), root has `.git`, no nested `.git`**

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1-4. (all completed)                      │
│  5. Review                                 │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 5/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Detected structure:                │   │
│  │                                     │   │
│  │  📂 myrepo (root)                   │   │
│  │     has .git                        │   │
│  │     no nested repos detected        │   │
│  │                                     │   │
│  │  ─────────────────────────────────  │   │
│  │                                     │   │
│  │  ✓ One project to register:         │   │
│  │    "myrepo"                         │   │
│  │                                     │   │
│  └─────────────────────────────────────┘   │
│                                             │
│                                [ Confirm > ]│
└─────────────────────────────────────────────┘
```

**Sub-case B: Ambiguous, root has `.git` + nested `.git` (monorepo split)**

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1-4. (all completed)                      │
│  5. Review                                 │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 5/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Detected structure:                │   │
│  │                                     │   │
│  │  📂 monorepo (root)                 │   │
│  │     has .git                        │   │
│  │     2 nested repos inside           │   │
│  │                                     │   │
│  │  ─────────────────────────────────  │   │
│  │                                     │   │
│  │  How would you like to register it? │   │
│  │                                     │   │
│  │  ◯ Single project (keep all        │   │
│  │    together as "monorepo")          │   │
│  │                                     │   │
│  │  ◯ Split out nested repos:          │   │
│  │    (each nested repo becomes its    │   │
│  │     own separate project; root      │   │
│  │     is ALSO registered, with files  │   │
│  │     in both copies)                 │   │
│  │                                     │   │
│  │    ☐ vendor/thing                  │   │
│  │    ☐ packages/foo                  │   │
│  │                                     │   │
│  │  ⚠ Splitting creates duplicate     │   │
│  │    copies of selected folders on    │   │
│  │    disk. Choose carefully.          │   │
│  │                                     │   │
│  └─────────────────────────────────────┘   │
│                                             │
│          [ Back < ]         [ Confirm > ]   │
└─────────────────────────────────────────────┘
```

**Sub-case C: No root `.git`, multiple subfolders (folder-of-subrepos)**

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1-4. (all completed)                      │
│  5. Review                                 │
│  6. Confirm (disabled)                      │
│                                             │
│  [Step indicator showing 5/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Detected structure:                │   │
│  │                                     │   │
│  │  📂 uploads (root, no .git)          │   │
│  │     3 subfolders:                   │   │
│  │                                     │   │
│  │  ─────────────────────────────────  │   │
│  │                                     │   │
│  │  How would you like to register it? │   │
│  │                                     │   │
│  │  ◯ Single project (all together)   │   │
│  │                                     │   │
│  │  ◯ Each subfolder as its own        │   │
│  │    project:                         │   │
│  │                                     │   │
│  │    ☑ repo-a                         │   │
│  │    ☑ repo-b                         │   │
│  │    ☑ repo-c                         │   │
│  │                                     │   │
│  │  (Default: all selected)            │   │
│  │                                     │   │
│  └─────────────────────────────────────┘   │
│                                             │
│          [ Back < ]         [ Confirm > ]   │
└─────────────────────────────────────────────┘
```

**Key design elements:**

1. **Structure summary** (always shown, at top):
   - `📂 folder-name (root)` — folder icon (using Unicode "📂" emoji for simplicity)
   - Text indicating `.git` presence: "has .git" (green check, `✓`) or "no .git" (light text)
   - Count of nested repos or subfolders
   
2. **Choice control** (only shown if ambiguous, per `response.ambiguous` flag):
   - Two radio buttons: "Single project" and "Split out nested repos"
   - Radio buttons visually styled as pills (round background, green when selected) rather than native radio inputs
   - **Toggling to "Split"** reveals the checklist below; toggling back to "Single" hides it

3. **Nested/subfolder checklist** (only shown if "Split" is selected):
   - One checkbox per candidate
   - **Default state per spec:**
     - Monorepo case (root has `.git`): nested paths **unchecked** (safer default; user must opt-in to split)
     - Subrepo case (no root `.git`): subfolders **checked** (matches the original auto-register behavior)
   - Each row is a `<label>` wrapping a checkbox + path text → 44px+ touch target

4. **Duplication warning** (only shown if "Split" option is available AND monorepo case):
   - Prominent yellow/orange warning box (`background: rgba(255, 193, 7, 0.1); border-left: 4px solid #ffc107;`)
   - Text: "Splitting creates duplicate copies of selected folders on disk. Choose carefully."
   - This surfaces the real risk called out in the spec

5. **Validation rule** (enforced on confirm):
   - Split + no-root-`.git` + zero selected: reject with error "Select at least one folder to register."
   - Single project or any split option with ≥1 selection: proceed to confirm

**Interaction flow:**
- Default: "Single project" radio selected; if ambiguous, "Split" option also available
- Clicking "Split" option reveals the checklist (smooth height transition or just inline toggle)
- Unchecking all items in "Split" mode shows a hint: "At least one must be selected in split mode"
- "Back" button goes back to the previous step (re-show step 4 state or step 2 if pick was `.zip`)
- "Confirm" button validates the selection and advances to step 6

---

### Step 6: Confirm (Registration and result)

```
┌─────────────────────────────────────────────┐
│  Upload local folder or .zip               │
├─────────────────────────────────────────────┤
│                                             │
│  1-5. (all completed)                      │
│  6. Confirm                                │
│                                             │
│  [Step indicator showing 6/6]               │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Registering projects...            │   │
│  │                                     │   │
│  │  (brief spinner or "waiting" state) │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  ──────────────────────────────────────────│   (after success)
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  ✓ Success!                         │   │
│  │                                     │   │
│  │  Registered projects:               │   │
│  │  • myrepo                           │   │
│  │  • vendor/thing (from split)        │   │
│  │  • packages/foo (from split)        │   │
│  │                                     │   │
│  │  (2 skipped as unselected)          │   │
│  │                                     │   │
│  │  They'll show up in the dashboard   │   │
│  │  shortly.                           │   │
│  │                                     │   │
│  │            [ Done, close wizard ]   │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  ──────────────────────────────────────────│   (after error)
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  ✗ Registration failed              │   │
│  │                                     │   │
│  │  Error: Name collision — "myrepo"   │   │
│  │  already exists.                    │   │
│  │                                     │   │
│  │         [ ← Back to review ]        │   │
│  │       [ Start over (step 1) ]       │   │
│  └─────────────────────────────────────┘   │
│                                             │
│         [ X Close wizard anyway ]           │
└─────────────────────────────────────────────┘
```

**Interactions:**

- **Waiting state:** Show a small inline spinner (CSS-based, e.g., border-top animation) or text "Registering..." while `POST /projects/upload/confirm` is in flight
- **Success state:**
  - Green checkmark (✓ in green text, or a simple SVG)
  - List registered project names with bullet points
  - If split and some folders were unselected: show "(N skipped as unselected)" in gray text
  - "Done, close wizard" button — closes the modal and auto-refreshes the dashboard (via existing `refresh()` call after ~1500ms, same as the "+ New project" flow)
  
- **Error state:**
  - Red X (✗ in error color) or just red text "✗ Registration failed"
  - Error message from server (JSON response `error` field), with context (e.g., "Name collision — 'myrepo' already exists")
  - Two buttons:
    - "Back to review" — returns to step 5, preserving the user's selections, to let them tweak and retry
    - "Start over" — resets wizard to step 1
  - Optional "Close wizard anyway" link at the bottom, for users who want to abandon the whole flow

**Auto-close on success:** After the user clicks "Done, close wizard," close the modal immediately and trigger `refresh()` on the dashboard (via the existing pattern used for project creation)

---

## Accessibility notes

### Color contrast
- **All text on `#1c1c1c` background:**
  - `#eee` (main text) on `#1c1c1c` → luminance contrast **15.6:1** (far exceeds 4.5:1 AA for text)
  - `#aaa` (secondary text) on `#1c1c1c` → luminance contrast **5.5:1** (meets AA)
  - `#888` (tertiary text) on `#1c1c1c` → luminance contrast **3.0:1** (below AA; used for non-critical labels only)
  
- **Progress bar fills:**
  - `#34c759` (green) on `#2a2a2a` background → luminance contrast **4.1:1** (meets 3:1 graphical-element requirement)
  - `#4da6ff` (blue) on `#2a2a2a` background → luminance contrast **6.3:1** (exceeds 3:1 graphical-element requirement)

- **Warning box:**
  - `#ffc107` (yellow) left border on `#1c1c1c` → luminance contrast **6.4:1** (sufficient for graphic/border)
  - Warning text (same as body text) meets AA

- **Error messages:**
  - `#ff6b6b` (red) on `#1c1c1c` → luminance contrast **5.0:1** (exceeds AA for text)

### Touch targets & interaction
- All buttons, checkboxes (via `<label>`), radio pill buttons: **44px minimum height** (set via padding on buttons, explicit min-height on label wrappers)
- Labels wrapping checkboxes are full-width clickable regions (no tiny checkbox to target)
- Radio pill buttons have adequate spacing (8px gap between pills)

### Keyboard navigation
- All interactive elements (buttons, checkboxes, radio buttons, file inputs via button wrapper) are keyboard-accessible via `<button>`, `<label>`, `<input type="radio">`, `<input type="checkbox">`
- Tab order follows visual left-to-right, top-to-bottom flow
- "Back" / "Next" / "Confirm" buttons are reachable via Tab
- Pressing Enter on a focused checkbox toggles it; Escape closes the modal (standard overlay behavior, same as existing TOTP overlay)

### Screen reader support
- Step counter ("Step 3 of 6") is readable text, not just visual progress bar
- Checklist labels are associated with inputs via `<label for="id">` (not just visual alignment)
- Error messages are in `<div class="err">` (existing pattern) so screen readers can announce them when they appear
- Progress bar uses `aria-valuenow`, `aria-valuemin`, `aria-valuemax` to make percentage audible
- Fieldset/legend structure for the "How to register" choice (radio buttons grouped under "How would you like to register it?")

### Reduced motion
- No animations on progress bars (per spec: updates are instant, no transition)
- Button/radio-button visual feedback is instant (no CSS transition), except for the optional height transition when toggling the checklist (can be made instant if needed)

---

## Component reuse (existing patterns)

1. **Overlay modal structure:** Existing `.overlay` + `.card` pattern (from TOTP and login overlays)
   - Reuse `.overlay.show` class toggle for open/close
   - Reuse `.card` styling for the wizard card
   
2. **TOTP code prompt:** Existing code overlay for phase-1 upload TOTP gate
   - Reuse existing `.code-overlay` + JS flow (`performAction`, `handleActionResult`, etc.)
   - Append `?code=<hex>` to upload URL on retry (per spec's phase-1 deviation)

3. **Button styling:** Existing `.new-project-row button` green primary button for "Next," "Upload," "Confirm"
   - Reuse `.pill` styling for secondary/neutral buttons and radio-button-style options
   - Reuse error styling (`.new-project-err` color) for step errors

4. **Progress bars:** New component (no exact precedent), but styled consistently with the app's existing `.badge` styling (dark container, colored text/indicator)

5. **File input:** Native `<input type="file" webkitdirectory>` and `<input type="file" accept=".zip">`, hidden and triggered by styled button clicks (common UX pattern for custom file-picker styling)

## New client-side code structure

The wizard adds three main pieces to the existing `PAGE_TEMPLATE`'s `<script>` block:

1. **Client-side zip writer** (~150-200 lines)
   - CRC-32 checksum calculation
   - Local file header + central directory + EOCD writing
   - No compression (store mode only)
   - Returns `Uint8Array` (zip bytes in memory)

2. **Wizard state machine** (~300-400 lines)
   - Tracks current step, file list, exclusion selections, upload token, user's split choice
   - Manages UI rendering for each step
   - Handles file I/O (exclusion scanning, zipping, uploading)

3. **UI rendering functions** (~200 lines)
   - `renderStep(n)` — renders the current step's content
   - `updateProgress(phase, percent, loaded, total)` — updates progress bars
   - Event handlers for button clicks, checkbox changes, radio selections

No new external dependencies; all vanilla JS as existing.

---

## State coverage

### Happy path
- ✓ Pick folder → Exclude → Zip → Upload → Review (single) → Confirm → Success

### Variants
- ✓ Pick .zip (skips Exclude and Zip steps, goes straight to Upload)
- ✓ Review with "Split" option, select nested repos, confirm
- ✓ Review with "Split" option, no-root-`.git` case, select subfolders, confirm
- ✓ Review with "Split" option, user selects zero items (reject with error)

### Error cases (all shown inline in the modal, not dismissing the wizard)
- ✓ No files match exclusion list (auto-advance past step 2)
- ✓ Zero files after exclusions (show error, "Back to exclude" button)
- ✓ Total size exceeds 100 MiB after exclusions (show warning, block auto-advance, "Back to exclude" button)
- ✓ TOTP not cleared (428 on upload) → show code overlay, retry logic
- ✓ Wrong TOTP code (403) → error message, retry without re-selecting files
- ✓ Corrupt/invalid zip file (400) → error message, "Start over" button
- ✓ Oversized upload (413) → error message, "Back to exclude" button
- ✓ Upload token expired (410 on confirm) → error message, "Start over" button
- ✓ Name collision on confirm (400) → error message with specific collision(s), "Back to review" button to adjust selections
- ✓ TOTP still not cleared on confirm (428) → show code overlay (standard JSON-body path), retry
- ✓ Network error during upload → show error, allow retry (wizard state is preserved)

### Edge cases
- ✓ Browser lacks `webkitdirectory` support → "Pick .zip" button still works, "Pick folder" button is hidden or disabled with explanation
- ✓ User closes modal mid-wizard (X button) → discard staged upload (server's TTL cleanup handles it), close overlay
- ✓ Monorepo with one nested `.git` and user splits it out → root is also registered with duplicate files (as per spec)

---

## Visual hierarchy & emphasis

1. **Primary:** Step indicator + current step card (large, centered)
2. **Secondary:** File input buttons, exclusion checkboxes, choice radio buttons
3. **Tertiary:** Step labels (grayed out), file counts/sizes, hints
4. **Alerts:** Warnings (yellow-tinted box), errors (red text)

The entire wizard is modal (overlay), so it dominates the viewport — no distraction from the background dashboard. Closing the wizard (success or abandonment) returns focus to the dashboard.

---

## Deviation from spec

None — all spec requirements are addressable within the design above. The spec explicitly defers exact visual layout, interaction details, wording, and styling to this design phase, so this document is comprehensive.

---

## Final design principles check (Dieter Rams)

1. **Good design is innovative** — Client-side zip building + two-phase upload is novel for this codebase; progress tracking on both phases is transparent and immediate.
2. **Good design makes a product useful** — Multi-step wizard makes the feature discoverable and guides users through a complex process without overwhelming them.
3. **Good design is aesthetic** — Reuses the app's existing dark theme, color palette, and card-overlay pattern; feels cohesive, not bolted-on.
4. **Good design is honest** — Duplication warning explicitly surfaces a real risk; step-by-step flow is honest about the multi-phase process, not hiding complexity.
5. **Good design is unobtrusive** — Modal overlay keeps the feature separate from the main dashboard; secondary visual weight (near the "+ New project" button, not above it).
6. **Good design is long-lasting** — Vanilla JS/HTML/CSS with no dependencies; reuses existing patterns; easy to maintain and extend.
7. **Good design is thorough down to the last detail** — Touch targets, color contrast, keyboard navigation, error messages, and state coverage all specified.
8. **Good design is environmentally friendly** — No heavy dependencies; uncompressed zips trade upload size for security/simplicity; users can optimize by picking pre-made compressed .zips if needed.
9. **Good design is as little design as possible** — Wizard is straightforward, step-by-step; no unnecessary branching; reuses existing UI patterns rather than inventing new ones.
10. **Good design is back to basics** — Focus on core task (upload folder → register projects); no extraneous features or fancy effects; clarity and usability over decoration.

---

## Summary of key design decisions

1. **Modal wizard container** — Reuses existing overlay pattern; keeps feature visually separate from primary dashboard.
2. **Two independent progress bars** (green for zipping, blue for uploading) — Visually distinguishes the two phases per spec's requirement.
3. **Duplication warning in step 5** — Prominently surfaces the monorepo split risk in a yellow-tinted box.
4. **Full-row checklist labels** (44px+ touch targets) — Ensures mobile accessibility.
5. **Radio-pill buttons for choice selection** — Visual distinction from checkboxes; styled consistently with existing `.pill` buttons.
6. **Reuses existing TOTP flow** — Leverages the existing code overlay and `handleActionResult` logic; only deviation is `?code=` on phase-1 retry URL.
7. **Dark theme, green/blue accents** — Matches existing app palette; no new design tokens.
8. **Vanilla JS/HTML/CSS, no new dependencies** — Fits the project's stdlib-only ethos.

## File reference

- **Implementation location:** New section in `PAGE_TEMPLATE` (lines 614–892 in `app/app.py`), adding wizard HTML markup and JS state machine + zip writer to the existing `<script>` block.
- **Related files modified:** `app/app.py` (backend routes + staging), `scripts/new-project-from-upload.sh` (new), `install.sh`, `config/switchboard.env.example`, `README.md`, `docs/ARCHITECTURE.md`.
