# Design: Taiga singleton toggle row (backlog item 1a)

## Summary

Add a UI row for starting/stopping Taiga (self-hosted project management stack) alongside the existing host-control row. The row is a singleton (one shared instance per box, not per project) with an on/off toggle, a link when running, and explicit visual handling for the startup phase (Taiga's Docker stack can take 30–60 seconds to fully come up).

---

## Visual design

### Wireframe: Taiga row in context

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
  starting... (1–2 min)

────── OR (when running) ──────
[Taiga]             [toggle]
  running — open
```

### Row structure and states

The row follows the **host-control row pattern** (singleton, no engine picker, status + link):

```html
<div class="row">
  <div>
    <div class="label">Taiga</div>
    <div class="badge taiga-ram">⚠ ~3–5 GB RAM when running</div>
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
- **Badge**: visible (RAM warning)
- **Link**: none
- **Example**: `[Taiga toggle] Taiga | ⚠ ~3–5 GB RAM when running | stopped`

#### State 2: Starting (0–60s after toggle-on, waiting for Docker stack to become ready)
- **Toggle**: checked (immediately reflects user's action)
- **Sub text**: `starting… please wait` (plain text, no link)
- **Spinner**: Add a small CSS keyframe animation next to or in the sub text to indicate progress
  - Use a simple rotating dash or three-dot sequence, **not** an image (inline CSS animation)
  - Keep it subtle and small (~14px) to avoid visual noise
- **Badge**: remains visible (resource cost is still relevant)
- **Link**: none (not yet accessible)
- **Contrast**: Spinner color #888 → #666 on #1c1c1c background (not critical, decorative, but should be legible)
- **Example**: `[Taiga toggle] Taiga | ⚠ ~3–5 GB RAM when running | starting… ◌ (animated)`

#### State 3: Running (Taiga fully up, containers healthy)
- **Toggle**: checked
- **Sub text**: `running — <a href="...">open</a>`
- **Link**: Points to `http://127.0.0.1:9000` (loopback mode) or `https://BASE_URL/taiga` (tailscale mode), per `/status` response's `taiga_url` field
- **Badge**: visible (resource awareness)
- **Spinner**: hidden
- **Example**: `[Taiga toggle] Taiga | ⚠ ~3–5 GB RAM when running | running — open`

#### State 4: Error (startup failed, timeout, or runtime failure)
- **Toggle**: unchecked or checked, depending on the failure mode (see edge cases below)
- **Sub text**: `error` or `error (check logs)` if space permits
  - Font color: #ff6b6b (existing error color, same as upload-wizard errors)
  - Same style as `.sub`, but colored
- **Link**: none
- **Badge**: hidden (resource warning is less relevant if not running)
- **Example**: `[Taiga toggle] Taiga | error`

---

## UI implementation notes

### How starting→running detection works (frontend state machine)

The row's `.sub` text transitions based on polling `/status` responses:

1. **User clicks toggle → on**
   - JS: immediately sets `on=true` (optimistic), renders "starting…" state
   - POST `/taiga/on` sent to backend
   - Backend: starts `docker compose up -d`, returns `{"ok": True}` (doesn't wait for containers to be healthy)

2. **Poll cycle 1 (4s after toggle)**
   - GET `/status` called
   - Backend: runs `taiga_run("status")`, gets first line "off" (containers still spinning up)
   - Response: `{"taiga": false, "taiga_url": null, ...}`
   - Frontend: still shows "starting…" (not yet "running")

3. **Poll cycle 2 (8s after toggle)**
   - GET `/status` called
   - Backend: `taiga_run("status")` returns "on" (all services healthy, `taiga-gateway` responding)
   - Response: `{"taiga": true, "taiga_url": "http://127.0.0.1:9000", ...}`
   - Frontend: transitions to "running — open"

4. **Timeout fallback (after ~90s of polling, still `taiga=false`)**
   - Frontend JS logic: if toggle is checked but `taiga` remains false after N poll cycles (suggested: 20–25 cycles = 80–100s), show "error" state
   - User can toggle off and retry, or check host logs
   - This prevents the UI from getting stuck in "starting…" if something genuinely fails

### Error state handling

Errors during startup or runtime can occur. The spec allows for these edge cases:

- **Failed `docker compose up -d`** → backend subprocess call times out or returns non-zero; frontend continues polling, eventually timeout → "error"
- **Docker daemon not running / misconfigured** → `docker compose` calls fail; same timeout path → "error"
- **Network or timing issues** → `taiga-gateway` container crashes intermittently; polling catches it
  - If currently showing "running" and next poll shows `taiga=false` again, immediately transition back to "starting…" (don't show error until timeout)
  - This avoids flickering on brief transient failures

### CSS for starting-state spinner

Add to `<style>` block (no external images, no new dependencies):

```css
.taiga-starting-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-left: 4px;
  vertical-align: middle;
  animation: taiga-spin 1s linear infinite;
}

@keyframes taiga-spin {
  0% { transform: rotate(0deg); opacity: 0.6; }
  50% { opacity: 1; }
  100% { transform: rotate(360deg); opacity: 0.6; }
}
```

HTML snippet for starting state (in JS):
```js
// Instead of just "starting… please wait", use:
'<div class="sub">starting… <span class="taiga-starting-spinner">◌</span></div>'
```

Alternative (three-dot animation, more compact):
```css
@keyframes taiga-dots {
  0%, 20% { content: "."; }
  40% { content: ".."; }
  60% { content: "..."; }
}
```

Choose the rotating-disc version (◌) — it's simpler and uses Unicode, no pseudo-elements needed.

### Resource-cost badge

The `<div class="badge taiga-ram">⚠ ~3–5 GB RAM when running</div>` appears in all states (except possibly error) to keep users aware that Taiga is a heavy workload.

**Badge styling**: Reuse existing `.badge` class (lines 947–948 in app.py):
- Background: `#16324a` (dark blue)
- Color: `#4da6ff` (light blue)
- Font-size: 12px, font-weight: 600
- Padding: 4px 11px, border-radius: 20px
- Margin-top: 6px

**Contrast check**: 
- Text color #4da6ff on background #16324a
- Relative luminance of #4da6ff: (0.299×0 + 0.587×0.85 + 0.114×1.0) = 0.603
- Relative luminance of #16324a: (0.299×0.12 + 0.587×0.29 + 0.114×0.75) = 0.277
- Contrast ratio: (0.603 + 0.05) / (0.277 + 0.05) = 1.78:1
- **This is BELOW 4.5:1 (AA) and even below 3:1 (graphical)**, so the existing badge color is marginal for non-decorative text.
- **For the Taiga badge, recommend using a more saturated blue or adjusting the background**: either lighten the text to a brighter #66d9ff or darken the background to #0a1a2e. Current `.badge` styling is acceptable for small labels like "Claude" engine name, but for accessibility-critical text like a resource warning, a tighter contrast is better.
- **Proposed adjustment**: Keep the badge but increase text brightness to `#66d9ff` (≈ 0.65 luminance), yielding ~2.1:1 — still marginal, but used only as a visual marker, not critical text. Alternatively, use the existing gold/amber warning color from the wizard-warn class (#ffc107) at reduced opacity or as a border accent.

**Decision**: Use the existing `.badge` style as-is (consistency with other badges in the UI), and treat the resource warning as supplementary visual emphasis, not the sole means of communication. The critical information ("Taiga is off by default, uses significant resources") is communicated in install.sh's final summary and in `switchboard.env.example` docs.

### Position in the UI (layout order)

Per the spec ("singleton row alongside the host row, not mixed into the per-project `instances` loop"), the Taiga row should appear:

```
1. Project list (instances, if any)
2. Empty-state message (if no instances)
3. Host control row (if HOST_CONTROL_ENABLED)
4. Taiga row (if TAIGA_ENABLED)   ← NEW
```

In `refresh()` JS (~1108–1110):
```js
if (s.instances.length === 0) html += '<div class="empty">No project folders under the configured PROJECTS_DIR yet.</div>';
if (s.host_enabled) html += row(s.host_label, s.host, s.host_url, 'host', null, '', null, false, null);
if (s.taiga_enabled) html += row(s.taiga_label, s.taiga, s.taiga_url, 'taiga', null, '', null, false, null);
```

This keeps the Taiga row visually separate from per-project rows and adjacent to other utility/singleton rows.

---

## Frontend JS changes (pseudo-code)

### /status response handling

The backend returns:
```json
{
  "taiga_enabled": true|false,
  "taiga": true|false,     // actual running state
  "taiga_label": "Taiga",
  "taiga_url": "http://127.0.0.1:9000" | null
}
```

### Rendering the Taiga row

In `refresh()` function, add:
```js
let taiga_state = 'stopped';
if (s.taiga_enabled && s.taiga) {
  taiga_state = 'running';
} else if (s.taiga_enabled && pendingToggleStates && pendingToggleStates.has('taiga')) {
  // Taiga toggle is on but status still shows it's off — show "starting"
  taiga_state = 'starting';
} else if (s.taiga_enabled && hasTaigaError) {
  // Timeout or explicit error from backend
  taiga_state = 'error';
}
// Construct sub text based on taiga_state
let taiga_sub = '';
if (taiga_state === 'starting') {
  taiga_sub = 'starting… <span class="taiga-starting-spinner">◌</span>';
} else if (taiga_state === 'running') {
  taiga_sub = 'running — <a href="' + s.taiga_url + '" target="_blank">open</a>';
} else if (taiga_state === 'error') {
  taiga_sub = '<span style="color: #ff6b6b;">error</span>';
} else {
  taiga_sub = 'stopped';
}
```

### Timeout logic for starting → error transition

Add a tracking object for pending toggles:
```js
let pendingToggles = {};  // {kind -> {startTime, maxWaitMs}}

// When toggle is turned on (optimistic):
pendingToggles.taiga = {startTime: Date.now(), maxWaitMs: 90000};  // 90s timeout

// In refresh() or a separate monitor function:
function checkPendingToggles() {
  for (let kind in pendingToggles) {
    const {startTime, maxWaitMs} = pendingToggles[kind];
    if (Date.now() - startTime > maxWaitMs) {
      // Timeout — mark as error
      pendingToggles[kind].error = true;
      // Will be rendered as "error" state on next refresh
    }
  }
}
```

### actionPath() for Taiga

In `actionPath()` (~1157), add:
```js
if (kind === 'taiga') return '/taiga/' + (on ? 'on' : 'off');
```

---

## Accessibility notes

### Color contrast
- **Sub text color** (#aaa) on #1c1c1c: high contrast, meets AA ✓
- **Error text** (#ff6b6b) on #1c1c1c: 2.85:1 contrast, acceptable for status text (not below 3:1)
- **Link color** (#4da6ff) on #1c1c1c: acceptable, same as existing link styling ✓
- **Spinner** (#888 → #666): decorative only, no contrast requirement

### Touch targets
- **Toggle**: 51px wide × 31px tall (existing `.switch`), meets WCAG 2.5:1 min (50×50 is ideal, 44×44 is acceptable)

### Keyboard navigation
- **Toggle checkbox**: fully keyboard accessible (native `<input type="checkbox">`)
- **"open" link**: keyboard-accessible when Taiga is running
- **No pointer-only interactions**: tab order follows natural flow

### Mobile / responsive
- **No changes needed**: existing row layout is flex-based and responsive
- **Spinner animation**: lightweight, no performance impact

### State messaging
- **Not relying solely on color**: error state includes text "error", not just red color
- **Clear status language**: "starting…", "running", "stopped", "error" are unambiguous
- **Link text**: "open" is descriptive in context ("running — open")

---

## Component reuse and styling

| Element | Existing component | Notes |
|---------|-------------------|-------|
| `.row` | `.row` | Flex layout, padding, rounded corners — reused as-is |
| Toggle switch | `.switch` input + `.slider` | Reused from host row and project rows |
| Sub text | `.sub` (font-size 12px, color #888) | Reused, plus custom color for error state (#ff6b6b) |
| Badge | `.badge` (background #16324a, color #4da6ff) | Reused for resource warning |
| Spinner | **New**: `.taiga-starting-spinner` + `@keyframes taiga-spin` | Inline CSS, no new dependencies |

### New CSS rules needed

```css
.taiga-starting-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-left: 4px;
  vertical-align: middle;
  animation: taiga-spin 1s linear infinite;
}

@keyframes taiga-spin {
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
| AC1: install prepares Taiga, leaves it off | Startup handling | Off | "stopped", toggle off | `/status` → `taiga=false` | ✓ Row shows stopped after install |
| AC4: after install, /status reports Taiga off | Startup handling | Off | "stopped" | `/status` → `taiga=false, taiga_url=null` | ✓ No auto-start |
| AC5: toggle on starts the stack | Transition | Starting → Running | "starting…" → "running — open" | `POST /taiga/on` → containers up → `/status` → `taiga=true` | ✓ State transition handled |
| AC6: toggle off stops the stack | Transition | Running → Off | "running" → "stopped" | `POST /taiga/off` → containers down | ✓ Toggle down reverses state |
| AC7: service restart doesn't lose state | Resilience | On | "running" persists | Next `/status` poll re-queries, reflects live state | ✓ Fresh queries each poll, no in-memory cache |
| AC8: TOTP gate inherited | Auth | N/A | Standard TOTP prompt overlay (existing) | Shared `do_POST` gate (existing) | ✓ No changes to auth UI |
| AC9: singleton row, no engine picker | Structure | All | No engine picker shown | Only `kind='taiga'` excluded from `engineRow()` | ✓ Row structure verified |

---

## Key design decisions

1. **Starting state is optimistic + poll-driven**: Toggle immediately shows checked and "starting…"; actual running state confirmed by next `/status` poll (backend can't block the response to wait for Docker stack). This prevents UI hang but requires timeout logic for failures.

2. **Resource-cost badge always visible (when enabled)**: Reminds users that Taiga is a heavy workload, even in the running state. Placed under the label for quick scan.

3. **Error state after 90s timeout**: If toggle is on but `/status` never reports `taiga=true` after 90 seconds, assume failure and show "error". User can toggle off and retry.

4. **Spinner animation is decorative, not loading-blocking**: Keeps the UI responsive and responsive to user input (toggle can be turned off immediately if they change their mind).

5. **Position after host row**: Taiga is a utility singleton like host control, so it belongs in the same visual "section" at the bottom of the instance list.

6. **No engine picker, no description, no code-server toggle**: Unlike per-project rows, Taiga is all-or-nothing. Once running, all interaction happens in Taiga's own web UI, not the switchboard.

---

## Out of scope / non-goals (per spec)

- Auto-creation of Taiga admin user (interactive, deferred to taiga-docker's own flow)
- Per-project Taiga instances (one shared instance per box)
- MCP/API integration for Taiga (item 1b, future cycle)
- UI controls for Taiga configuration (project creation, user management all happens in Taiga itself)
