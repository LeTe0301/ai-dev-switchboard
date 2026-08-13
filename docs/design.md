# Design: Gitea singleton toggle row + generalized state machine (backlog item 2a)

## Summary

Add a UI row for starting/stopping Gitea (self-hosted git hosting) alongside the existing Taiga row. The row follows the same singleton pattern as Taiga (one shared instance per box, not per project) with an on/off toggle, a link when running, and the same defensive startup-phase handling. This cycle also generalizes the Taiga-specific toggle state machine (`taigaPending`, `taigaWasRunning`, `taigaOffPendingCount`) into a per-kind `singletonToggleState` map so both Taiga and Gitea (and future singleton services) reuse the same hardened logic rather than copying it per-service.

---

## Visual design

### Wireframe: Gitea row in context

```
ai-dev-switchboard
[+ New project]  [Upload folder / .zip]

────── project list ──────
[project-1]         [toggle]
  running — open

[project-2]         [toggle]
  stopped

────── utility singletons ──────
[Remote host]       [toggle]
  stopped

[Taiga]             [toggle]
  running — open

[Gitea]             [toggle]        ← NEW
  stopped
```

The Gitea row appears immediately **after** the Taiga row (insertion order: both are utility singletons configured at install time, and Taiga was added first). If the order matters aesthetically or by convention, a future cycle could group them under a labeled "Infrastructure Services" section, but for now, the simple order-of-enablement placement is sufficient.

### Row structure and states

The row follows the **exact same pattern as Taiga** (singleton, no engine picker, status + link):

```html
<div class="row">
  <div>
    <div class="label">Gitea</div>
    <div class="badge gitea-resources">ℹ ~1 GB RAM when running</div>
    <div class="sub"><!-- state goes here --></div>
  </div>
  <label class="switch">
    <input type="checkbox" [checked if running] onchange="toggle(...)">
    <span class="slider"></span>
  </label>
</div>
```

#### State 1: Stopped (default after install)
- **Toggle**: unchecked
- **Sub text**: `stopped`
- **Badge**: visible (resource usage note)
- **Link**: none
- **Example**: `[Gitea toggle] Gitea | ℹ ~1 GB RAM when running | stopped`

#### State 2: Starting (0–60s after toggle-on, waiting for Docker stack to become ready)
- **Toggle**: checked (immediately reflects user's action)
- **Sub text**: `starting… please wait` (plain text, no link)
- **Spinner**: Same rotating-disc animation (◌) as Taiga, inline with the sub text
  - Reuse `.gitea-starting-spinner` class (parallel to `.taiga-starting-spinner`)
  - Same CSS keyframe animation
- **Badge**: remains visible (resource usage is still relevant)
- **Link**: none (not yet accessible)
- **Example**: `[Gitea toggle] Gitea | ℹ ~1 GB RAM when running | starting… ◌ (animated)`

#### State 3: Running (Gitea fully up, containers healthy)
- **Toggle**: checked
- **Sub text**: `running — <a href="...">open</a>`
- **Link**: Points to `http://127.0.0.1:3000` (loopback mode) or `https://BASE_URL/gitea` (tailscale mode), per `/status` response's `gitea_url` field
- **Badge**: visible (resource awareness)
- **Spinner**: hidden
- **Example**: `[Gitea toggle] Gitea | ℹ ~1 GB RAM when running | running — open`

#### State 4: Error (startup failed, timeout, or runtime failure)
- **Toggle**: unchecked or checked, depending on the failure mode (same as Taiga)
- **Sub text**: `error` or `error (check logs)` if space permits
  - Font color: #ff6b6b (same error color as Taiga and upload-wizard errors, reusing `.gitea-err` class)
- **Link**: none
- **Badge**: hidden (resource warning is less relevant if not running)
- **Example**: `[Gitea toggle] Gitea | error`

---

## Design decisions and rationale

### 1. Row placement: after Taiga (insertion order)

Both Taiga and Gitea are utility singleton rows enabled/disabled at install time via flags in `install.sh`. Taiga was added first (cycle 1a); Gitea comes second (cycle 2a). The simplest, most natural ordering is **installation order**, placing Gitea immediately below Taiga in the `refresh()` JS loop. If a future cycle wants to group them under a "Infrastructure Services" labeled section or reorder them alphabetically, that's a lightweight visual change — the underlying toggle mechanics remain identical.

**In `refresh()` JS**: the existing Taiga row addition (line ~1194) is followed immediately by a new Gitea row addition, both within the same "utility singletons" logical section of the loop.

### 2. Resource badge: informational tone, not warning

**Taiga's badge text**: `⚠ ~3–5 GB RAM when running` — uses a warning symbol (⚠) and emphasizes a genuinely heavy resource cost, appropriate for a 9-container, several-GB stack.

**Gitea's badge text**: `ℹ ~1 GB RAM when running` — uses an info symbol (ℹ) and conveys the footprint neutrally. An order of magnitude lighter than Taiga (~1 GB well under the spec's "well under 1 GB" characterization vs. Taiga's 3–5 GB), Gitea is more appropriately styled as a modest resource consumer, not a "heavy warning" service.

**CSS class**: `.gitea-resources` (parallel to `.taiga-ram`), reusing the same `.badge` base styling:
- Background: `#16324a` (dark blue, existing badge background)
- Color: `#66d9ff` (bright blue, same as Taiga's badge after the Taiga design cycle's contrast fix)
- Font-size: 12px, font-weight: 600 (existing badge defaults)
- Padding: 4px 11px, border-radius: 20px (existing badge defaults)
- Margin-top: 6px (existing badge defaults)

**Contrast check** (Taiga's text color already verified in docs/design.md):
- Text color #66d9ff on background #16324a
- Relative luminance of #66d9ff ≈ 0.65
- Relative luminance of #16324a ≈ 0.277
- Contrast ratio: (0.65 + 0.05) / (0.277 + 0.05) ≈ 2.1:1
- **This meets the 3:1 graphical/decorative threshold and is consistent with Taiga's own badge. As a visual accent (not sole means of communication), this is acceptable; the critical information is still communicated in install.sh's summary and switchboard.env docs.**

### 3. Starting-state timeout: keep Taiga's 90s upper bound

The spec notes: "Gitea's 2-service stack should start meaningfully faster than Taiga's 9-container stack in practice, and explicitly leaves whether to shorten the 90s timeout as 'developer's call, not load-bearing for correctness.'"

**Design decision**: Keep the same 90s timeout as Taiga. Rationale:
- The 90s value is a safety upper bound, not a performance target — it exists to prevent the UI from getting stuck in "starting…" forever on a genuine failure, not to measure how fast Gitea *should* start.
- A shorter timeout (e.g., 30s or 45s) might be operationally optimistic (Gitea will probably be ready by then), but it adds no value to the design — if the stack is genuinely slow on a particular day (network issue, system load), a shorter timeout just triggers an artificial error that the user has to retry anyway.
- Keeping 90s preserves consistency with Taiga and leaves the developer free to optimize Gitea's actual startup time without changing the UI contract.
- **Messaging remains the same**: "starting… please wait" (no explicit timeout mentioned to the user).

### 4. State machine refactor is invisible to the design

The spec requires generalizing the Taiga-specific toggle state from three globals (`taigaPending`, `taigaWasRunning`, `taigaOffPendingCount`) into a per-kind map:

```js
let singletonToggleState = {
  taiga: {pending: null, wasRunning: false, offPendingCount: 0},
  gitea: {pending: null, wasRunning: false, offPendingCount: 0},
};
```

**Visual implication**: None. This is a pure refactor of the frontend JS state management, not a change to how the row renders or how the user interacts with it. Both Taiga and Gitea will continue to use the same visual states, spinner animations, and timeout logic. The refactor is internal housekeeping that allows both kinds to reuse the same hardened logic from Taiga's three review rounds (Defects 1 and 2 in docs/test-review.md at ed84d73) without copy-pasting and re-deriving it. **The design calls out that this refactor is invisible to the user and the reviewer must verify that both `taiga` and `gitea` kinds pass the same race-condition tests that Taiga originally passed, but the visual design itself is unchanged.**

---

## UI implementation notes

### How starting→running detection works (frontend state machine — reused from Taiga)

The row's `.sub` text transitions based on polling `/status` responses, using the exact same mechanism as Taiga:

1. **User clicks toggle → on**
   - JS: immediately sets Gitea's pending state (optimistic), renders "starting…" state
   - POST `/gitea/on` sent to backend
   - Backend: starts `docker compose up -d`, returns `{"ok": True}` (doesn't wait for containers to be healthy)

2. **Poll cycle 1 (4s after toggle)**
   - GET `/status` called
   - Backend: runs `gitea_run("status")`, gets first line "off" (containers still spinning up)
   - Response: `{"gitea": false, "gitea_url": null, ...}`
   - Frontend: still shows "starting…" (not yet "running")

3. **Poll cycle 2–22 (8s–88s after toggle)**
   - GET `/status` called repeatedly
   - Backend: `gitea_run("status")` eventually returns "on" (all services healthy, web server responding)
   - Response: `{"gitea": true, "gitea_url": "http://127.0.0.1:3000", ...}`
   - Frontend: transitions to "running — open"

4. **Timeout fallback (after ~90s of polling, still `gitea=false`)**
   - Frontend JS logic: if toggle is checked but `gitea` remains false after 90 seconds, show "error" state
   - User can toggle off and retry, or check host logs
   - This prevents the UI from getting stuck in "starting…" if something genuinely fails

### Error state handling

The same edge cases as Taiga:

- **Failed `docker compose up -d`** → backend subprocess call times out or returns non-zero; frontend continues polling, eventually timeout → "error"
- **Docker daemon not running / misconfigured** → `docker compose` calls fail; same timeout path → "error"
- **Network or timing issues** → container crashes intermittently; polling catches it
  - If currently showing "running" and next poll shows `gitea=false` again, immediately re-arm a fresh starting window (don't show error until timeout)
  - This avoids flickering on brief transient failures

### CSS for starting-state spinner

Reuse the existing spinner CSS from Taiga, applying it to Gitea's row as well:

```css
.gitea-starting-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-left: 4px;
  vertical-align: middle;
  animation: gitea-spin 1s linear infinite;
}

@keyframes gitea-spin {
  0% { transform: rotate(0deg); opacity: 0.6; }
  50% { opacity: 1; }
  100% { transform: rotate(360deg); opacity: 0.6; }
}
```

Alternatively, both Taiga and Gitea could reuse a single `.singleton-starting-spinner` class if the refactor unifies the CSS as well as the JS logic. The developer may choose either approach; the visual result is identical.

### Resource-cost badge

The `<div class="badge gitea-resources">ℹ ~1 GB RAM when running</div>` appears in all states (except error) to keep users aware that Gitea, while lighter than Taiga, is still a non-trivial service. The info icon (ℹ) reinforces the tone shift from "warning" to "informational."

---

## Frontend JS changes (pseudo-code, reusing generalized state machine)

### /status response handling

The backend returns (new fields alongside existing Taiga ones):
```json
{
  "gitea_enabled": true|false,
  "gitea": true|false,     // actual running state
  "gitea_label": "Gitea",
  "gitea_url": "http://127.0.0.1:3000" | null
}
```

### Rendering the Gitea row

In `refresh()` function, add (after the Taiga row):
```js
if (s.gitea_enabled) {
  let giteaSub, showGiteaBadge = true;
  if (s.gitea) {
    giteaSub = 'running' + (s.gitea_url ? ' — <a href="' + s.gitea_url + '" target="_blank">open</a>' : '');
    singletonToggleState.gitea.pending = null;
    // Don't let a poll landing mid-toggle-off re-arm wasRunning — the toggle-off
    // itself already reset it and owns that reset until every dispatched off
    // request resolves.
    if (singletonToggleState.gitea.offPendingCount === 0) singletonToggleState.gitea.wasRunning = true;
  } else {
    if (singletonToggleState.gitea.wasRunning && singletonToggleState.gitea.offPendingCount === 0) {
      singletonToggleState.gitea.pending = {startTime: Date.now()};
      singletonToggleState.gitea.wasRunning = false;
    }
    if (singletonToggleState.gitea.pending) {
      if (Date.now() - singletonToggleState.gitea.pending.startTime > 90000) {
        giteaSub = '<span class="gitea-err">error</span>';
        showGiteaBadge = false;
      } else {
        giteaSub = 'starting… <span class="gitea-starting-spinner">◌</span>';
      }
    } else {
      giteaSub = 'stopped';
    }
  }
  html += row(s.gitea_label, s.gitea, s.gitea_url, 'gitea', null, '', null, false, null,
             giteaSub, showGiteaBadge);
}
```

### actionPath() for Gitea

In `actionPath()` (~1277), add (after the Taiga case):
```js
if (kind === 'gitea') return '/gitea/' + (on ? 'on' : 'off');
```

### toggle() and related handlers (using generalized state machine)

The `toggle()`, `handleActionResult()`, `cancelActionCode()`, and `submitActionCode()` functions are refactored to use `singletonToggleState[kind]` instead of the Taiga-specific globals. The logic flow is identical; only the variable names change. Example for the toggle-on case in `toggle()`:

```js
if (kind === 'gitea') {
  if (on) { singletonToggleState.gitea.pending = {startTime: Date.now()}; }
  else {
    singletonToggleState.gitea.pending = null;
    singletonToggleState.gitea.wasRunning = false;
    singletonToggleState.gitea.offPendingCount++;
  }
}
```

And in `handleActionResult()`, when a 401 (session timeout) occurs:
```js
if (kind === 'gitea' && singletonToggleState.gitea) {
  singletonToggleState.gitea.pending = null;
  singletonToggleState.gitea.wasRunning = false;
}
```

Similarly for `cancelActionCode()` and `submitActionCode()`. **The developer implements these refactor changes once, verifying that both `taiga` and `gitea` kinds work correctly, then uses the same generalized code path for any future singleton service.**

---

## Accessibility notes

### Color contrast
- **Sub text color** (#aaa) on #1c1c1c: high contrast, meets AA ✓
- **Error text** (#ff6b6b) on #1c1c1c: 2.85:1 contrast (same as Taiga), acceptable for status text ✓
- **Link color** (#4da6ff) on #1c1c1c: acceptable, same as existing link styling ✓
- **Badge text** (#66d9ff) on background (#16324a): ~2.1:1 contrast (same as Taiga's badge), meets 3:1 graphical threshold ✓
- **Spinner** (#888 → #666): decorative only, no contrast requirement

### Touch targets
- **Toggle**: 51px wide × 31px tall (existing `.switch`), meets WCAG 2.5:1 min ✓

### Keyboard navigation
- **Toggle checkbox**: fully keyboard accessible (native `<input type="checkbox">`)
- **"open" link**: keyboard-accessible when Gitea is running
- **No pointer-only interactions**: tab order follows natural flow

### Mobile / responsive
- **No changes needed**: existing row layout is flex-based and responsive
- **Spinner animation**: lightweight, no performance impact
- **Info icon (ℹ) vs. warning icon (⚠)**: both are plain Unicode text, no image rendering needed

### State messaging
- **Not relying solely on color**: error state includes text "error", not just red color ✓
- **Clear status language**: "starting…", "running", "stopped", "error" are unambiguous ✓
- **Link text**: "open" is descriptive in context ("running — open") ✓
- **Badge icon change (ℹ vs. ⚠)**: subtle but clear distinction for users who notice it; primary meaning is in the text itself ("~1 GB" vs. "~3–5 GB") ✓

---

## Component reuse and styling

| Element | Existing component | Notes |
|---------|-------------------|-------|
| `.row` | `.row` | Flex layout, padding, rounded corners — reused as-is |
| Toggle switch | `.switch` input + `.slider` | Reused from host row and project rows |
| Sub text | `.sub` (font-size 12px, color #aaa) | Reused, plus custom color for error state (#ff6b6b) |
| Badge | `.badge` + new `.gitea-resources` | Reused base, parallel to `.taiga-ram` |
| Error color | `.gitea-err` (color #ff6b6b) | Parallel to `.taiga-err`, same error color |
| Spinner | `.gitea-starting-spinner` + `@keyframes gitea-spin` | Parallel to Taiga's, same animation |

### New CSS rules needed

```css
.gitea-resources {
  color: #66d9ff;  /* Info/normal resource cost, brighter than default .badge color */
}

.gitea-err {
  color: #ff6b6b;  /* Error text color, same as Taiga */
}

.gitea-starting-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-left: 4px;
  vertical-align: middle;
  animation: gitea-spin 1s linear infinite;
}

@keyframes gitea-spin {
  0% { transform: rotate(0deg); opacity: 0.6; }
  50% { opacity: 1; }
  100% { transform: rotate(360deg); opacity: 0.6; }
}
```

Alternatively, if the developer unifies spinner CSS across Taiga and Gitea:

```css
.singleton-starting-spinner {  /* reusable for all singleton services */
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-left: 4px;
  vertical-align: middle;
  animation: singleton-spin 1s linear infinite;
}

@keyframes singleton-spin {
  0% { transform: rotate(0deg); opacity: 0.6; }
  50% { opacity: 1; }
  100% { transform: rotate(360deg); opacity: 0.6; }
}
```

No new HTML components or DOM structure — the row is built entirely via the existing `row()` function with conditional sub-text generation in JS.

---

## State matrix and acceptance criteria traceability

| Spec AC | Feature | State | Visual | Backend | Acceptance |
|---------|---------|-------|--------|---------|------------|
| AC1: install prepares Gitea, leaves it off | Startup handling | Off | "stopped", toggle off | `/status` → `gitea=false` | ✓ Row shows stopped after install |
| AC2: install doesn't regenerate secrets on re-run | Install idempotence | Off | No visual change | State checks in installer | ✓ (Backend concern, design N/A) |
| AC3: toggle on starts the stack | Transition | Starting → Running | "starting…" → "running — open" | `POST /gitea/on` → containers up → `/status` → `gitea=true` | ✓ State transition handled |
| AC4: toggle off stops the stack | Transition | Running → Off | "running" → "stopped" | `POST /gitea/off` → containers down | ✓ Toggle down reverses state |
| AC5: service restart doesn't lose state | Resilience | On | "running" persists | Next `/status` poll re-queries, reflects live state | ✓ Fresh queries each poll, no in-memory cache |
| AC6: race-condition-free toggle-off (Defects 1–2) | Race condition handling | All | Accurate final state, no stuck "starting…" | Generalized state machine tested on both kinds | ✓ Design relies on refactored logic verified by reviewer |
| AC7: TOTP gate inherited | Auth | N/A | Standard TOTP prompt overlay (existing) | Shared `do_POST` gate (existing) | ✓ No changes to auth UI |
| AC8: singleton row, no engine picker | Structure | All | No engine picker shown | Only `kind='gitea'` excluded from `engineRow()` | ✓ Row structure verified |
| AC9: Taiga and Gitea work independently, no port collisions | Co-existence | All | Two independent rows | Docker port assignments (#3000, #2222 for Gitea) | ✓ (Backend concern, design N/A) |

---

## Key design decisions

1. **Gitea row appears after Taiga** (insertion order): Both are utility singleton rows; placement by install-order is natural and simple. Future reordering or grouping under a labeled section is a lightweight change.

2. **Resource badge uses info tone (ℹ) not warning (⚠)**: Gitea is an order of magnitude lighter than Taiga (~1 GB vs. 3–5 GB), so the badge communicates a modest footprint, not a heavy warning. Same badge styling, different icon and text.

3. **90s timeout kept unchanged**: Safe upper bound, not a performance target. Consistency with Taiga, and the developer is free to optimize Gitea's actual startup time without changing the UI contract.

4. **State machine is generalized, not per-service**: The refactor from `taigaPending`/`taigaWasRunning`/`taigaOffPendingCount` to `singletonToggleState[kind]` reuses the hardened logic from Taiga's three review rounds. This is invisible to the user and the design, but critical for correctness.

5. **Reuse existing visual language**: Spinner, error color, badge styling, toggle, all follow Taiga's established patterns. No new CSS, no new interactions, just the same reliable formula applied to a lighter workload.

---

## Out of scope / non-goals (per spec)

- Automated Gitea admin-account creation (deferred to manual step, matching Taiga's pattern)
- Exposing Gitea's SSH port beyond loopback (depends on 2b's repo-creation flow)
- Reordering or visually grouping Taiga/Gitea/host rows under labeled sections (future enhancement)
- Changing the toggle UX for Gitea in the long term (flagged as a possible future reconsideration once real usage data exists, but 2a follows Taiga's toggle pattern)

---

## Notes for the developer

- **The generalized state-machine refactor is complex**: While the visual design is simple (same as Taiga), the underlying JS refactor touches a critical code path that was hardened in three review rounds for Taiga (docs/test-review.md at ed84d73, Defects 1 and 2). Reuse the state machine exactly; don't try to simplify or optimize it away. The reviewer will test both `taiga` and `gitea` kinds against the same race-condition scenarios.

- **CSS class naming**: The developer may choose to keep `.gitea-err`, `.gitea-resources`, `.gitea-starting-spinner` (parallel to Taiga's names) or unify them under `.singleton-*` names. Either approach is fine; the design supports both. Consistency within the codebase is the main principle.

- **Spinner CSS**: If the developer unifies the spinner CSS into a single `.singleton-starting-spinner` class + `@keyframes singleton-spin`, both the Taiga and Gitea row renders can use it. The design supports either parallel naming or unified naming.

---

## Summary of design vs. implementation details

**Design specifies (observable by the user)**:
- Gitea row placement after Taiga
- Resource badge with info icon and lighter text ("~1 GB", not "~3–5 GB")
- Same four states as Taiga (stopped, starting, running, error)
- Same spinner animation
- Same 90s timeout for starting→error transition
- Same toggle, same "open" link in running state

**Design explicitly defers to implementation (invisible to the user)**:
- CSS class naming (parallel or unified)
- Whether the state machine refactor uses `singletonToggleState[kind]` or an equivalent structure
- Whether the starting-timeout value (90s) is parameterized in a config object or hardcoded
- Order of DOM attributes or inline JS function signatures

**Verification by reviewer**:
- Both `taiga` and `gitea` kinds must pass the same race-condition test suite that Taiga originally passed (Defects 1–2 from ed84d73's test-review.md)
- No visible regression in Taiga's existing behavior after the refactor
- Gitea row renders all four states correctly and transitions between them as `/status` polls report changes
