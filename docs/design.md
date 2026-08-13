# Design: Upload wizard polish (backlog item 3's deferred follow-ups)

## Summary

Visual and configuration polish on the existing upload wizard (shipped commit `893840c`, 2026-08-12), addressing three small deferred items: restyle step 5's single/split radio choice as pill buttons per the original design intent, only render "Back" in the ambiguous case where there's something to go back and change, and wire `UPLOAD_MAX_ENTRIES` to `switchboard.env` so operators can tune it without modifying Python.

---

## Visual design

### Wireframe: Step 5 choice styling

**Ambiguous case (two projects detected, choice required):**

```
Detected structure:
📁 my-project (root)
  has .git
  2 nested repos inside

Legend: How would you like to register it?

┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│ ● Single project (keep all...)   │  │   Split out nested repos:        │
│   (pill-styled, checked=green)   │  │   (pill-styled, unchecked=gray)  │
└──────────────────────────────────┘  └──────────────────────────────────┘

Split candidates:
☑ vendor/repo-1
☐ vendor/repo-2
(regular checkboxes for split selection, unchanged)

[Back <]  [Confirm >]
```

**Unambiguous case (one project detected, no choice needed):**

```
Detected structure:
📁 my-project (root)
  no .git
  3 subfolders

✓ One project to register: "my-project"

(No Back button, only Confirm)

                      [Confirm >]
```

---

## Component reuse and styling

### Existing `.pill` pattern (reused)

The app already styles optional choices as pills (`engineRow`'s engine picker, `codeRow`'s VS Code toggle). Step 5's mode choice adopts the same visual treatment:

```css
.pill {
  font-size: 13px;
  padding: 5px 12px;
  border-radius: 20px;
  background: #2a2a2a;      /* idle: dark gray */
  color: #aaa;              /* idle: light gray text */
  cursor: pointer;
  user-select: none;
  border: 1px solid #3a3a3a;
}

.pill.active {
  background: #34c759;      /* selected: green */
  color: #111;              /* selected: dark text */
  font-weight: 600;
  border-color: #34c759;
}
```

### New CSS rule: `.wizard-check-row.pill-choice`

For the two mode-choice radio labels only (not the split-candidate checkboxes), add a new class that reapplies pill styling on top of the existing `.wizard-check-row` base:

```css
.wizard-check-row.pill-choice {
  /* Override wizard-check-row defaults for this variant */
  padding: 5px 12px;                    /* match .pill padding */
  border-radius: 20px;                  /* match .pill border-radius */
  background: #2a2a2a;                  /* match .pill idle bg */
  color: #aaa;                          /* match .pill idle text */
  border: 1px solid #3a3a3a;            /* match .pill border */
  gap: 8px;                             /* reduce gap from 10px to keep pill compact */
  margin: 0 4px 0 0;                    /* add small right margin for spacing between pills */
  display: inline-flex;                 /* stack horizontally instead of full-width blocks */
}

/* When the underlying radio input is :checked, light up the label */
.wizard-check-row.pill-choice:has(input:checked) {
  background: #34c759;                  /* green when selected */
  color: #111;                          /* dark text when selected */
  font-weight: 600;
  border-color: #34c759;
}
```

**CSS `:has()` fallback note:** If `:has()` support needs verification at implementation time, the developer can fall back to a small `onchange` handler that toggles `.active` or a similar class on the label itself. The JS already exists (`setWizardMode()` calls `renderWizard()` anyway), so both approaches are equally viable.

### Step 5 HTML structure (unchanged underlying semantics)

The two mode-choice radios remain native `<input type="radio" name="wizard-mode">` elements — no bare `<span class="pill">` replacement, keeping keyboard/screen-reader accessibility fully intact. Only the containing `<label>` gets the visual restyle:

```html
<!-- BEFORE (current) -->
<label class="wizard-check-row">
  <input type="radio" name="wizard-mode" ... onchange="setWizardMode('single')">
  <span class="info">Single project (keep all together as ...)</span>
</label>

<!-- AFTER (new) -->
<label class="wizard-check-row pill-choice">    <!-- new class added -->
  <input type="radio" name="wizard-mode" ... onchange="setWizardMode('single')">
  <span class="info">Single project (keep all together as ...)</span>
</label>
```

Split-candidate checkboxes (shown only if `wizardState.mode === 'split'`) stay as plain `wizard-check-row` without the `pill-choice` class — they keep the original checkbox styling (dark background, small checkbox widget, full-width row).

### Conditional "Back" button

Step 5 action buttons render conditionally based on `d.ambiguous`:

```js
function renderStep5Actions(d) {
  let html = '';
  if (d.ambiguous) {
    html += '<button class="secondary" onclick="resetWizardState(); renderWizard();">&lsaquo; Back</button>';
  }
  html += '<button class="primary" onclick="proceedToConfirm()">Confirm &rsaquo;</button>';
  return html;
}
```

**Signature change note:** Function now takes `d` (the `detectResult` object) as a parameter instead of reading it implicitly. Call site changes from `renderStep5Actions()` to `renderStep5Actions(wizardState.detectResult)`.

---

## Design decisions and rationale

### 1. Pill styling for mode choice (visual consistency)

**Decision:** Step 5's single/split mode choice renders as pill-styled elements matching `engineRow` and `codeRow`, not as plain dark checkboxes.

**Rationale:**
- Original `docs/design.md` at commit `893840c` explicitly called for this visual treatment.
- Consistent with the app's established pattern for optional/toggleable choices (engine picker, VS Code toggle).
- Green (#34c759) when selected, dark gray (#2a2a2a) when idle — same palette as the rest of the UI.
- The backlog note confirms this is a known "visual polish gap," not a new design decision.

### 2. Keep underlying `<input type="radio">` (accessibility)

**Decision:** The pill styling is CSS-only, wrapping the existing native radio inputs. No replacement with bare `<span>` elements.

**Rationale:**
- Spec explicitly calls for "keeping the underlying `<input type="radio">` for accessibility/keyboard semantics."
- Screen readers and keyboard navigation (Tab, arrow keys) continue to work unchanged.
- No regression from today's already-accessible state.
- Contrasts with `engineRow`/`codeRow` (which use bare `<span class="pill">` with click-only JS), making those mouse-centric — step 5's mode choice is a core form input where keyboard support matters.

### 3. CSS `:has()` for checked-state styling

**Decision:** Use `label:has(input:checked)` selector to style the label based on the radio's `:checked` state, no additional JS state tracking.

**Rationale:**
- Browser support: modern browsers (Chrome 105+, Safari 15.4+, Firefox 121+) all support `:has()`. The app is a modern single-page web app with no explicit legacy-browser support policy.
- Cleaner than adding a separate `.active` class and toggling it in `onchange` handlers.
- The browser's own `:checked` state is the source of truth; CSS hooks directly into it.
- If the developer finds a genuine `:has()` compatibility issue at build time, a simple `onchange` handler on the radio (toggling `.active` on the parent label) is a trivial fallback — spec notes this.

### 4. Inline-flex layout for pills (horizontal stacking)

**Decision:** Two mode-choice pills stack horizontally (side-by-side) on the same line, not full-width rows.

**Rationale:**
- Both pills fit comfortably on one line in typical form widths.
- Consistent with how `engineRow` displays multiple engine options (flex, gap, no wrapping).
- Uses horizontal space efficiently; full-width `.wizard-check-row` blocks would waste space.
- `display: inline-flex` on the label, `margin-right: 4px` for inter-pill gap.

### 5. Conditional "Back" button (visual/UX clarity)

**Decision:** Render "Back" button only when `d.ambiguous === true`. Unambiguous case shows only "Confirm" button.

**Rationale:**
- Original design.md wireframe explicitly showed two states:
  - Sub-case A (unambiguous): `[ Confirm > ]` only.
  - Sub-cases B/C (ambiguous): `[ Back < ]  [ Confirm > ]`.
- The unambiguous case has no choice to go back and unmake (the wizard detected exactly one project, no alternate mode to select).
- Cleaner UI: removes a non-functional "Back" button that would reset the whole wizard, when there's nothing to reconsider in step 5 itself.
- The wizard's own modal-level close/X control (unchanged) still lets users abandon at any time.

### 6. Function signature: `renderStep5Actions(d)` instead of `renderStep5Actions()`

**Decision:** Pass `wizardState.detectResult` explicitly to `renderStep5Actions()` so it can check `d.ambiguous` and conditionally render "Back".

**Rationale:**
- Matches the pattern `renderStep5()` already uses (receives `d` as a local variable from `wizardState.detectResult`).
- Cleaner than having `renderStep5Actions()` reach into global `wizardState` itself.
- Single call site (around line 2590-ish in the existing wizard-render chain) changes from `renderStep5Actions()` to `renderStep5Actions(wizardState.detectResult)` — straightforward.

---

## Accessibility notes

### Color contrast

- **Pill idle state** (#aaa on #2a2a2a): ~4.5:1 ratio ✓ (meets WCAG AA for text)
- **Pill active state** (#111 on #34c759): ~11:1 ratio ✓ (excellent; same as existing `.pill.active`)
- **Split-candidate checkboxes** (unchanged): dark background with native checkbox widget, no contrast concerns

### Touch target size

- **Pill buttons** (mode choice): min-height 44px preserved from base `.wizard-check-row` ✓
- **Radio input widget**: 18px width/height (per existing `.wizard-check-row input`), adequate for touch
- Both meet mobile-friendly touch target sizing without additional padding.

### Keyboard navigation

- **Tab key**: Focus moves to the underlying `<input type="radio">` inside the pill label (visible focus ring, native browser behavior)
- **Arrow keys** (within a radio group): Left/Right arrows move between "Single" and "Split" radios (native radio group behavior)
- **Enter/Space**: Toggles the focused radio (native behavior, triggers `onchange="setWizardMode(...)"`)
- **Screen readers**: Each radio is announced as a radio button with its associated label text ("Single project...", "Split out..."), per native `<label>` + `<input>` semantics

### Platform-specific notes

- **Web**: Native radio behavior fully supported; `:has()` CSS applies automatically.
- **Mobile browsers**: Touch targets meet 44px minimum; native browser radios render as platform-specific widgets.

---

## State matrix and acceptance criteria traceability

| Spec AC | State | Visual | Keyboard | Acceptance |
|---------|-------|--------|----------|------------|
| AC1: env var set | Config | N/A (backend) | N/A | ✓ `UPLOAD_MAX_ENTRIES` read from environment |
| AC2: env var default | Config | N/A | N/A | ✓ Defaults to `20000` if not set |
| AC3: Step 5 ambiguous, pills render | Populated | Two pill-styled elements side-by-side, green when selected | Tab to radio, arrow keys, focus visible | ✓ Pills styled like `.pill`/`.pill.active`, click/keyboard updates mode |
| AC4: Step 5 ambiguous, keyboard | Navigation | N/A | Focus on radio input inside pill | ✓ Tab lands on radio, arrow keys work, no unfocusable span |
| AC5: Step 5 unambiguous, no Back | Hidden | Only `[ Confirm > ]` visible | Tab skips to Confirm (no Back button) | ✓ Back button absent when `d.ambiguous === false` |
| AC6: Step 5 ambiguous, Back visible | Visible | `[ Back < ]  [ Confirm > ]` visible | Tab to Back, Tab to Confirm | ✓ Back rendered when `d.ambiguous === true` |

---

## Key design decisions

1. **Pill styling reuses existing `.pill` pattern** — visual consistency with engine picker and VS Code toggle.

2. **Underlying `<input type="radio">` preserved** — no accessibility regression; keyboard/screen-reader semantics unchanged.

3. **CSS `:has()` for checked styling** — hooks into native `:checked` state without additional JS tracking; fallback to `onchange` class toggle if needed.

4. **Inline-flex layout** — two pills stack horizontally, matching `engineRow`'s pattern and efficient use of space.

5. **Conditional "Back" button** — only shown in ambiguous case where there's an actual choice to reconsider; unambiguous case cleaner with Confirm-only.

6. **Minimal function signature change** — only `renderStep5Actions(wizardState.detectResult)` vs. current `renderStep5Actions()`, single call-site update.

---

## Notes for the developer

- **CSS `:has()` implementation**: If browser-testing reveals any issue, a one-line `onchange` handler can toggle a class on the label: `document.querySelector('label[for="wizard-mode-single"]').classList.toggle('active')` or similar. The spec notes this as acceptable.

- **Split-candidate checkboxes unchanged**: Only add `pill-choice` class to the *first two* radio labels (mode choice), not the subsequent checkbox labels for split candidates. The checkboxes stay as plain `.wizard-check-row`.

- **Call-site update**: Find the existing call to `renderStep5Actions()` (likely in a `<div class="wizard-actions">` render block) and pass `wizardState.detectResult`: `renderStep5Actions(wizardState.detectResult)`.

- **Modal close/X behavior preserved**: The unambiguous sub-case still has the modal-level close control (top-right X), so users can abandon the wizard if needed. "Back" just isn't relevant when there's no choice to reconsider.

---

## Summary of changes

**New CSS class:**
- `.wizard-check-row.pill-choice` — applies pill styling (rounded, gray idle / green active) to mode-choice labels.

**CSS `:has()` rule:**
- `.wizard-check-row.pill-choice:has(input:checked)` — styles the label green when its contained radio is checked.

**HTML changes:**
- Add `pill-choice` class to the two mode-choice `<label class="wizard-check-row pill-choice">` elements only (lines ~2513 and ~2517).

**JS function signature:**
- `renderStep5Actions(d)` instead of `renderStep5Actions()` — now takes `detectResult` object to check `d.ambiguous`.
- Conditionally render `Back` button only if `d.ambiguous === true`.

**Call site update:**
- Pass `wizardState.detectResult` to `renderStep5Actions(wizardState.detectResult)` at its one invocation site.

**Backend (not UI-visible):**
- `UPLOAD_MAX_ENTRIES` wired to `int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))` — handled by developer per spec.
- `config/switchboard.env.example` comment updated — handled by developer per spec.

---

## Out of scope / non-goals (per spec)

- Not changing "Back" semantics (still resets to step 1, full wizard reset).
- Not redesigning any other step or section of the upload wizard.
- Not touching step 6 ("Confirm") buttons.
- No partial undo / back-to-step-4 without re-upload.
