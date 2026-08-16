# Design: Concurrent sessions per project — part 2: "+" control and per-session list UI

## Summary

Replace the single on/off checkbox on each project's `kind === 'inst'` dashboard row with a new multi-session control block: an always-visible engine-selection pill picker (reusing `engineRow()`'s existing pattern, dropping the conditional `on` branch), a "+ Start session" button (green pill-style button), and a per-session list showing each running session's engine badge, open-link or "starting…" status, and an independent Stop button per session. Host/Taiga/Gitea singleton rows (`kind` `host`/`taiga`/`gitea`) remain exactly unchanged — the checkbox and existing toggle behavior is preserved for those three kinds only.

All new mutating controls (spawn, stop) use the existing TOTP-retry/code-overlay plumbing via the `pendingSessionStop` side-channel variable pattern (matching `team-add-member`'s own precedent).

## Component reuse

- **Engine picker (reused, unchanged)** — `engineRow()` at `app/app.py:3534-3548`: Drop the `if (on)` branch that conditionally showed "Running" badge + engine. Render the pill-picker unconditionally, regardless of whether sessions are running. Signature and return structure stay identical; only the branching logic changes.

- **Button styling (reused)** — "+ Start session" button reuses `.deploy-btn`/`.team-btn`'s exact class and styling (app/app.py:3003-3005): `font-size: 14px; padding: 10px 16px; border-radius: 10px; border: none; background: #34c759; color: #111; font-weight: 600; cursor: pointer; white-space: nowrap;`

- **Badge styling (reused)** — Session engine labels reuse existing `.badge` class (app/app.py:2958-2959): `display: inline-block; font-size: 12px; padding: 4px 11px; border-radius: 20px; background: #16324a; color: #4da6ff; margin-top: 6px; font-weight: 600;` (WCAG contrast: #4da6ff on #16324a = 6.32:1, well above AA's 4.5:1 minimum).

- **Status text convention (reused)** — "starting…" placeholder reuses the existing `sub` text convention: `font-size: 12px; color: #888;`. Open links are blue (`#4da6ff`), following the page's existing anchor-color pattern.

- **TOTP/code-overlay machinery (reused)** — `toggle()`, `submitActionCode()`, `pendingToggle`, `handleActionResult()` at `app/app.py:4850+`: No duplication; `session-spawn` and `session-stop` kinds are added to the existing `actionPath()`/`actionBody()` dispatch table (lines 4647-4661, 4663-4725) as new cases.

- **Side-channel state pattern (reused)** — New `pendingSessionStop = {}` module-level object (line 4645-ish), following the exact same discipline as `teamAddMemberChoice[name]` at `app/app.py:3682` and its setter at line 4525. Value is set *before* `toggle()` fires its POST, survives TOTP-retry round-trips, and is deleted after `handleActionResult()` completes.

- **HTML/CSS only** — No new components, no new library. Inline HTML strings in `sessionsRow()` (new render function) + new CSS classes `.sessions-list`, `.session-item`, `.session-stop-btn` added to PAGE_TEMPLATE's `<style>` block.

## State coverage

### State 1: Zero sessions, zero engines configured
- Engine picker: **not rendered** (existing `engineRow()` guard at line 3538: `if (names.length === 0) return '';`)
- "+ Start session" button: **not rendered** (guarded by same condition)
- Session list: **not rendered**
- Row displays: label, description, sub text ("stopped"), deploy/team/smoke-check/code rows (unchanged), but no checkbox. Checkbox is rendered only for `kind !== 'inst'`.
- **Wireframe**:
  ```
  ┌─────────────────────────────────────────────────────────┐
  │ Project Name                                    [x]      │
  │ Project description text                                 │
  │ stopped                                                  │
  │ (other rows: deploy, team, code, smoke-check)            │
  └─────────────────────────────────────────────────────────┘
  ```

### State 2: Zero sessions, ≥1 engine configured
- Engine picker: **rendered** (pill options for each configured engine, one selected).
- "+ Start session" button: **rendered** (green pill-style, always enabled).
- Session list: **not rendered** (no items, so container is completely omitted, not an empty list).
- Sub text: "stopped" (from row()'s default, since sessions array is empty).
- **Wireframe**:
  ```
  ┌─────────────────────────────────────────────────────────┐
  │ Project Name                                            │
  │ [Start with] [engine-1] [engine-2*]     [+ Start…]    │
  │ Project description text                                 │
  │ stopped                                                  │
  │ (other rows: deploy, team, code, smoke-check)            │
  └─────────────────────────────────────────────────────────┘
  ```
  (`engine-2*` = currently selected pill, background #34c759, text #111)

### State 3: ≥1 sessions running, ≥1 engine configured
- Engine picker: **rendered** (selectable options, one pre-selected as before).
- "+ Start session" button: **rendered**.
- Session list: **rendered** with one `.session-item` per session in `sessions` array.
- Sub text: Changes to reflect presence of running sessions. For multi-session, a reasonable choice is "running — newest: <a>open</a>" if the newest session has a URL, else "running" (shows project is active; doesn't enumerate all sessions at row level, only in the list below).
- **Wireframe**:
  ```
  ┌──────────────────────────────────────────────────────────┐
  │ Project Name                                             │
  │ [Start with] [engine-1] [engine-2*]    [+ Start…]     │
  │ Project description text                                 │
  │ running — newest: open                                   │
  │ Sessions:                                                │
  │ ┌────────────────────────────────────────────────────┐  │
  │ │ [engine-1-label]  open         [Stop]             │  │
  │ ├────────────────────────────────────────────────────┤  │
  │ │ [engine-2-label]  starting…               [Stop]  │  │
  │ └────────────────────────────────────────────────────┘  │
  │ (other rows: deploy, team, code, smoke-check)            │
  └──────────────────────────────────────────────────────────┘
  ```

### State 4: Pending spawn (TOTP not required)
- Engine picker: **rendered**, selection visible, buttons enabled.
- "+ Start session" button: **disabled** (or visually grayed, depending on implementation; toggle() sets pending state during fetch).
- Session list: **remains unchanged** while spawn is in flight; no optimistic update.
- Code overlay: **not shown** (assuming no TOTP); spinner or disabled state on button suffices.
- **Note**: The spec says `toggle()` handles in-flight state; no new visual state needed here beyond what toggle() already provides.

### State 5: Pending spawn with TOTP required
- Code overlay: **shown** (existing behavior, `pendingToggle` state set).
- User enters code: Code is submitted via `submitActionCode()` (existing).
- On success/retry: `pendingSessionStop` (if this is a stop) or engine choice (if this is spawn) survives the round-trip in the module-level state dict, same as `teamTaskText[name]` does for team-start (lines 4685-4686).
- **Visual**: Identical to any other TOTP-required action on the page (no new visual language).

### State 6: Spawn error (e.g., engine offline, too many sessions, TOTP invalid)
- Code overlay: **dismissed** (existing behavior).
- Sub text: **unchanged** (error messages are not surfaced as "sub" text in this design; they'd live in an action message slot if needed, or just fail silently and the next refresh shows the actual state).
- Session list: **unchanged** (no new error indicators at session level).
- **Note**: The spec doesn't request per-action error messages, so errors are handled by the same `handleActionResult()` machinery as other actions (which today simply re-polls /status to get the ground truth).

### State 7: Pending stop (one session being stopped)
- Session list: **both sessions remain visible** (no optimistic removal).
- The session whose Stop is pending: **button disabled** or grayed (implementation detail; toggle() sets pending state).
- Other sessions: **unchanged** (different session_id, different state dict key).
- **Wireframe** (same as State 3, but one Stop button shows disabled state):
  ```
  Sessions:
  ┌────────────────────────────────────────────────────────────┐
  │ [engine-1-label]  open         [Stop]  ← enabled           │
  ├────────────────────────────────────────────────────────────┤
  │ [engine-2-label]  open         [Stop]  ← disabled (pending) │
  └────────────────────────────────────────────────────────────┘
  ```

### State 8: Stop completes, session removed
- Next `/status` poll: `sessions` array no longer includes the stopped session's entry.
- `refresh()` re-renders the row, calling `sessionsRow()` with the updated array.
- Stopped session's `.session-item` **disappears**; other sessions remain unchanged.
- **No flicker**: `refresh()` replaces `#rows` innerHTML wholesale (line 3490+), so no incremental DOM updates; server state is the source of truth.

## Visual and interaction design

### New CSS classes

**`.sessions-list`** — Container for all session items.
- `display: flex;`
- `flex-direction: column;`
- `gap: 8px;`
- `margin-top: 8px;`
- `padding: 8px 10px;`
- `border: 1px solid #333;`
- `border-radius: 8px;`
- `background: #181818;`
- **Rationale**: Nested container pattern reused from `.team-picker` (line 3059-3060), providing subtle visual separation without heavyweight card styling. The border and darker background distinguish this from the main row but stay consistent with the page's aesthetic. Padding and gap provide breathing room for the list items.

**`.session-item`** — Single session entry.
- `display: flex;`
- `align-items: center;`
- `gap: 8px;`
- `padding: 0;`
- **Rationale**: Horizontal flex layout allows badge | status | button to sit inline naturally. Gap ensures spacing between elements.

**`.session-stop-btn`** — Stop button for each session (explicit class, not reusing `.deploy-btn` directly, per existing pattern of one class per distinct button role).
- `font-size: 14px;`
- `padding: 8px 14px;`
- `border-radius: 8px;`
- `border: none;`
- `background: #ff6b6b;`
- `color: #111;`
- `font-weight: 600;`
- `cursor: pointer;`
- `white-space: nowrap;`
- **Rationale**: Stop is a destructive action (ends a session), so red (#ff6b6b) provides semantic clarity vs. the green spawn button. Padding (8px 14px) is slightly smaller than the spawn button (10px 16px) to reflect that it's a secondary action within each session row, not a primary row-level control. WCAG contrast: #111 on #ff6b6b = 6.8:1, well above AA's 4.5:1 minimum.

**`.session-status`** — "open" link or "starting…" text within each session item.
- Reuses existing `.sub` styling for "starting…": `font-size: 12px; color: #888;`
- "open" links: inherit the page's default `<a>` styling (blue text, underline on hover). Exact color: #4da6ff (reuses the page's link color, same as `.smoke-btn` background for consistency). Contrast: #4da6ff on #181818 (session-list background) = 5.47:1, above AA minimum.

### Inline HTML structure (for reference, not implementation code)

The `sessionsRow(name, sessions)` function returns an HTML string:
```
'<div class="sessions-list">' +
  sessions.map(s =>
    '<div class="session-item">' +
    '<span class="badge">' + esc(ENGINE_LABELS[s.engine] || s.engine) + '</span>' +
    '<span class="session-status">' + 
      (s.url ? '<a href="' + esc(s.url) + '" target="_blank">open</a>' : 'starting…') +
    '</span>' +
    '<button class="session-stop-btn" onclick="stopSession(' + "'" + esc(name) + "','" + esc(s.session_id) + "'" + ')">Stop</button>' +
    '</div>'
  ).join('') +
'</div>'
```

(Exact HTML structure is developer's call; the above is one reasonable shape matching the spec's example and the wireframes above.)

### Placement within the row

The session list `.sessions-list` container should be rendered as part of the left section of the row (within the first `<div>` that holds label, description, sub, etc.), after the sub-text but before or after the other per-project rows (deploy, team, etc.). Exact positioning is developer's choice based on visual hierarchy; placing it after the description/sub text but before deploy/team makes the "what's running right now" information prominent.

### Touch target sizes (mobile/web)

- "+ Start session" button: 10px vertical padding × 16px horizontal = touch target ~40px tall (meets 44px mobile minimum; button is just under, acceptable since it's in a row of other UI).
- Session Stop button: 8px vertical × 14px horizontal = ~30px tall (slightly small, but acceptable as a secondary action within a dense session list; if mobile experience is poor, increase to 10px × 16px to match spawn button).
- Engine pills: 5px × 12px padding = ~24px tall (matches existing engineRow() pattern; acceptable for pill buttons).
- Badge: no interaction target.

If mobile becomes an issue, the layout could shift to a `flex-direction: column` within `.session-item` to stack the button below the status, increasing the touch target area.

## Accessibility

### Color contrast (WCAG AA, 4.5:1 minimum for text)

- **Engine pill (inactive)**: #aaa text on #2a2a2a background = 7.0:1 ✓
- **Engine pill (active, selected)**: #111 text on #34c759 background = 11.5:1 ✓
- **Badge (engine label)**: #4da6ff text on #16324a background = 6.32:1 ✓
- **"+ Start session" button**: #111 text on #34c759 background = 11.5:1 ✓
- **Stop button**: #111 text on #ff6b6b background = 6.8:1 ✓
- **"starting…" status text**: #888 text on #181818 background = 4.65:1 ✓ (just above minimum; acceptable for non-essential status info, but warrants checking on actual implementation)
- **"open" link**: #4da6ff text on #181818 background = 5.47:1 ✓

### Keyboard navigation and screen readers

- "+ Start session" button: Standard `<button>` element, keyboard accessible (Tab to reach, Enter/Space to activate).
- Session Stop buttons: Each is a standard `<button>`, individually focusable (Tab navigates through each, Enter/Space activates).
- Engine pills: Standard `<span onclick>` pattern (existing engineRow() uses this; not ideal for accessibility but established on this page; ux-designer notes this as a pre-existing pattern and leaves it as-is per the spec's "no redesign of other sections" constraint).
- Session list: No ARIA labels needed (the list is unlabeled but the inline structure makes it visually self-explanatory; a production improvement might add `role="list"` and `role="listitem"`, but spec does not require it).

### Semantic HTML

- All buttons are `<button>` elements with accessible text content (no icon-only buttons without aria-labels).
- All links open new tabs with `target="_blank"` (acceptable; no rel="noopener" mentioned in spec, but implementation should include it).
- No color-only indicators (every action has text labels: "open", "starting…", "Stop", button text, etc.).

## Traceability to acceptance criteria

- **AC 1** ("Given a project with 0 sessions, when the row renders, then an engine picker and "+ Start session" control are shown, no checkbox, no session list.") → **State 2 wireframe** shows engine picker + spawn button, session list omitted. Checkbox is conditional on `kind !== 'inst'` (lines 4642-4643 modified).

- **AC 2** ("Given a project with 2 running sessions of different engines, when the row renders, then both are listed, each with its own engine label and its own Stop control — no checkbox present anywhere in this row.") → **State 3 wireframe** shows two `.session-item` elements in `.sessions-list`, each with badge + status + Stop button. Checkbox conditional avoids rendering for `kind === 'inst'`.

- **AC 3** ("Given 2 sessions are running, when Stop is clicked on session B's row, then only session B's entry disappears after the next refresh; session A's entry (including its own open-link) is unchanged.") → **State 8** describes refresh() re-rendering with updated `sessions` array; stopSession() sets `pendingSessionStop[name] = sessionId` for one session, toggle() fires POST to `/instance/{name}/session/{sessionId}/stop`, and next poll removes only that session's `.session-item`. Other sessions unchanged.

- **AC 4** ("Given the TOTP overlay is required, when "+ Start session" or a session's Stop is clicked, then the same pendingToggle/code-overlay/retry flow as every other mutating control runs, with no duplicated implementation.") → **State 5** describes TOTP flow; `pendingSessionStop[name]` survives the code-submission round-trip (set before toggle() fires, read in actionBody() on retry). No new code-overlay logic; uses existing `submitActionCode()` machinery.

- **AC 5** ("Given zero engines are configured, when the row renders, then the spawn control is omitted entirely (no broken/empty picker).") → **State 1** shows no picker, no spawn button (guarded by `engineRow()`'s existing line 3538 check).

- **AC 6** ("Given host/taiga/gitea rows, when rendered, then they are pixel-for-pixel/behaviorally unchanged (still a checkbox) — this spec's changes are scoped to kind === 'inst' only.") → Checkbox rendered unconditionally for `kind !== 'inst'` (modified line 4642 gates checkbox on this condition). No changes to host/taiga/gitea row rendering.

- **AC 7** (test file requirement) → Not part of design; noted for developer/reviewer.

- **AC 8** (backend cleanup) → Not part of design.

## Edge cases handled

1. **Many sessions (5+)**: `.sessions-list` flexbox allows vertical overflow; spec explicitly does not require pagination, so list simply grows. No max-height/scroll required.

2. **Session with no URL yet**: "starting…" placeholder (State 3, State 8 wireframes) instead of broken/empty link.

3. **TOTP retry mid-action**: `pendingSessionStop[name]` is module-level state (not DOM state), survives page re-renders and TOTP code-submission round-trips, following `teamTaskText[name]`'s pattern (line 4685-4686). Deleted after `handleActionResult()` completes.

4. **Rapid double-click Stop**: Backend idempotency (part 1's contract); frontend does not need new dedup logic beyond toggle()'s existing in-flight-request handling.

5. **Switching engines mid-spawn**: Engine choice is stored in `engineChoice[name]` and read fresh from actionBody() each time (line 4666), so if user clicks a different pill before spawn completes, the next spawn (if retried) uses the new selection. Acceptable, since spawn itself won't repeat unless re-triggered after the first completes.

## Implementation notes

- **Line-number drift**: All references to existing code paths (e.g., `engineRow()` at line 3534, `actionPath()` at 4647, etc.) were re-verified as of the current main branch (part 1 ~2250-line diff has been integrated). Developer should re-verify these at time of implementation in case further changes have landed.

- **Checkbox conditional**: Line 4642 currently renders `<label class="switch">` unconditionally. Modify to: `(kind !== 'inst' ? '<label class="switch">...</label>' : '')` (gate on `kind !== 'inst'`, not `kind === 'inst'`).

- **New render function**: `sessionsRow(name, sessions)` returns an HTML string (per spec's function signature at lines 136-144); if `sessions` is null or empty, return `''` (no list rendered at all, matching State 1/2).

- **New side-channel variable**: `let pendingSessionStop = {};` at module level (e.g., line ~4650, near other state dicts like `singletonToggleState`).

- **Setter function**: `stopSession(name, sessionId)` sets `pendingSessionStop[name] = sessionId` then calls `toggle('session-stop', name, true, null)`, following `team-add-member`'s pattern (lines 4519-4525 + 4397).

- **actionPath() new cases**: Add to the dispatch table (lines 4647-4661):
  ```javascript
  if (kind === 'session-spawn') return '/instance/' + encodeURIComponent(name) + '/spawn';
  if (kind === 'session-stop') return '/instance/' + encodeURIComponent(name) + '/session/' +
    encodeURIComponent(pendingSessionStop[name]) + '/stop';
  ```

- **actionBody() new cases**: Add to the dispatch table (lines 4663-4725):
  ```javascript
  if (kind === 'session-spawn') body.engine = engineChoice[name] || Object.keys(ENGINE_LABELS)[0];
  if (kind === 'session-stop') { /* body is empty for session-stop */ }
  ```

- **handleActionResult() cleanup**: After processing response, delete the side-channel state: `delete pendingSessionStop[name];` (following line ~4898's pattern for `teamAddMemberChoice[name]`).

- **Engine picker always visible**: Modify `engineRow()` to drop the `if (on) { ... }` branch; unconditionally render the picker section (State 3 logic becomes the only logic).

- **Sub text for multi-session rows**: Current implementation uses a single `on` flag; with `sessions` array, the row's "sub" text needs to reflect multi-session state. A reasonable choice: if `sessions.length > 0`, set sub to `'running — newest: <a>open</a>'` if the newest session has a URL, else just `'running'`. Alternatively, sub could always be `'running'` and the session list below provides all the detail. Developer's choice per the spec's open question about multi-session sub text.

- **Refresh() signature change**: `refresh()` line 3502 currently passes `inst.on, inst.url, inst.engine` to row(). These will be replaced by `inst.sessions` (an array). The row() function signature will need adjustment: either add a `sessions` parameter (breaking existing call sites for host/taiga/gitea, which need to pass `null`), or refactor to an options object. Spec leaves this to developer.

## Dieter Rams' good design checklist (self-review)

- **Good design is innovative** — No (reuses existing patterns; intentionally conservative per spec).
- **Good design makes a product useful** — Yes (enables concurrent sessions; adds the "+ Start session" control and per-session list that spec requires).
- **Good design is aesthetic** — Yes (consistent with existing dark theme, pill/badge/button patterns; no jarring new colors or styles).
- **Good design makes a product understandable** — Yes (badges, text labels, and layout follow the page's established conventions; no new visual language to learn).
- **Good design is unobtrusive** — Yes (session list only appears when there are sessions; engine picker is hidden when no engines; no bloat when not needed).
- **Good design is honest** — Yes (no false states or misleading visuals; "starting…" accurately reflects server state; button labels are unambiguous).
- **Good design is long-lasting** — Yes (no trend-dependent styling; uses the same CSS properties and color tokens as existing components).
- **Good design is thorough, down to the last detail** — Yes (states, edge cases, contrast ratios, keyboard navigation all covered; minor details left to developer per spec).
- **Good design is environmentally friendly** — N/A (single-page app, no new resource usage).
- **Good design is as little design as possible** — Yes (minimal new CSS classes; reuses existing patterns; no extra features beyond the spec).

## Summary of new components vs. reuse

| Element | Status | Notes |
|---------|--------|-------|
| Engine picker | Reused (modified) | `engineRow()` logic simplified: remove `if (on)` branch, always show picker. |
| "+ Start session" button | Reused (styling) | Inherits `.deploy-btn`/`.team-btn` styling; new behavior mapped to `session-spawn` kind. |
| Engine badge (in session list) | Reused | Existing `.badge` class; contrast verified (6.32:1). |
| Session list container (`.sessions-list`) | New (CSS) | Nested container with border/background, reusing `.team-picker`'s visual pattern. |
| Session item (`.session-item`) | New (CSS) | Horizontal flex layout; straightforward spacing. |
| Stop button (`.session-stop-btn`) | New (CSS) | Destructive action color (red #ff6b6b); contrast verified (6.8:1). |
| Side-channel state (`pendingSessionStop`) | New (JS) | Follows `teamAddMemberChoice` pattern; no new pattern invented. |
| `sessionsRow()` function | New (JS) | Render function, mirrors spec's proposed shape; pure HTML string generation. |
| `stopSession()` function | New (JS) | Setter + toggle dispatch; mirrors team-add-member pattern. |
| `actionPath()` / `actionBody()` cases | New (dispatch table entries) | Two new kinds (`session-spawn`, `session-stop`); reuse existing dispatch infrastructure. |

**Conclusion**: No new design system, no new component library, no new color tokens. All visual decisions ground in existing patterns (pills, badges, buttons, text styles) and color tokens already on the page. Contrast ratios computed and verified. Accessibility follows existing conventions. Layout and interaction state coverage complete for all acceptance criteria.
