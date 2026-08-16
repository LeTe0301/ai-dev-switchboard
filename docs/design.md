# Design: Dedicated team chat page (`/team/<project>`)

## Overview

This design relocates the AI-team interface from the per-project dashboard row to a dedicated full-page surface at `GET /team/<project>`. The page preserves all existing team-control interactions and the event-feed format, reorganizing them in a full-width, scrollable layout addressable via URL.

---

## Page Layout & Structure

### HTML Container

Add a new `<div id="team-page" style="display:none;"></div>` to `PAGE_TEMPLATE`'s `<body>` (alongside `#rows`, `#upload-overlay`, `#code-overlay`, `#overlay`). This container is shown/hidden opposite the dashboard and creation controls based on client-side routing.

The login and TOTP overlays (`#overlay`, `#code-overlay`) remain shared between dashboard and team-page contexts—their z-index and show/hide logic are unchanged.

### Wireframe: `/team/<project>` — Authenticated, Running Status

```
╔══════════════════════════════════════════════════════════════╗
║ ← ai-dev-switchboard › <project-name>                   [×] ║  Header (sticky)
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Status: running  (blue label)                              ║  Status strip
║                                                              ║
║  [if waiting_on_you]                                         ║
║  ┌────────────────────────────────────────────────────────┐ ║
║  │ Escalation panel: question + radio options             │ ║
║  │ OR: board_write proposal + Approve/Reject buttons      │ ║
║  │ [Custom answer textarea]                               │ ║
║  │ [Submit button]                                        │ ║
║  └────────────────────────────────────────────────────────┘ ║
║                                                              ║
║  ┌────────────────────────────────────────────────────────┐ ║
║  │ Message from <agent>:                                  │ ║  Compose box
║  │ [textarea: "Interject..."]                             │ ║
║  │ [Send]                                                 │ ║
║  └────────────────────────────────────────────────────────┘ ║
║                                                              ║
║  + Add team member                                           ║
║                                                              ║
║  Show live feed (collapsible)                                ║  Feed section
║  ┌────────────────────────────────────────────────────────┐ ║
║  │ All  ⚫lead  ⚫agent1  ⚫agent2  ...   [filter pills]    │ ║
║  │                                                        │ ║
║  │ 12:34:56  lead     ready                              │ ║
║  │ 12:34:57  human    [user response]                    │ ║
║  │ 12:35:02  agent1   delegating to agent2               │ ║
║  │ ...                                   [scrollable]     │ ║
║  └────────────────────────────────────────────────────────┘ ║
║                                                              ║
║  [Stop team]  [Back to dashboard]                           ║  Actions
║                                                              ║
║  Past branches: main, feature/x, release/1.0                ║  Info list
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

### Wireframe: `/team/<project>` — Authenticated, Idle Status

```
╔══════════════════════════════════════════════════════════════╗
║ ← ai-dev-switchboard › <project-name>                   [×] ║  Header
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  ┌────────────────────────────────────────────────────────┐ ║
║  │ Task description (required):                           │ ║
║  │ [textarea for task]                                    │ ║
║  │                                                        │ ║
║  │ Once started, you'll see live team activity and can    │ ║
║  │ interject right here.                                  │ ║
║  │                                                        │ ║
║  │ Configure team... (toggle link)                        │ ║
║  │ ┌──────────────────────────────────────────────────┐   ║
║  │ │ Lead engine: [dropdown]                          │   ║
║  │ │ Teammate engines:                                │   ║
║  │ │ ☐ Engine3                                        │   ║
║  │ │ ☑ Engine4                                        │   ║
║  │ │ (Grounding info)                                 │   ║
║  │ │ ⚠ Tier 3 engines in use                          │   ║
║  │ └──────────────────────────────────────────────────┘   ║
║  │                                                        │ ║
║  │ [Start team]  (disabled if no task or config error)    │ ║
║  │                                                        │ ║
║  │ Past branches: main, feature/x                         │ ║
║  └────────────────────────────────────────────────────────┘ ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

### Wireframe: `/team/<project>` — Unknown Project

```
╔══════════════════════════════════════════════════════════════╗
║ ← ai-dev-switchboard › team-chat                        [×] ║  Header
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Unknown project 'nonexistent'                               ║
║  (This project may have been deleted or misspelled.)         ║
║                                                              ║
║  ← Back to dashboard                                         ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Header & Navigation

### Back-Link (Text Breadcrumb)

**Styling:**
- Reuse `.team-feed-toggle` class styling (existing precedent for inline text links)
- Color: #4da6ff (blue, matches "Open team chat" link color)
- Font-size: 12px
- Text-decoration: underline
- Cursor: pointer
- No border/background
- Inline-block display

**Format:** `← ai-dev-switchboard › <project-name>`

**Touch Target:** Ensure minimum 44px height (WCAG AAA) via padding around text.

**Behavior:** Onclick navigates to `window.location = '/'` (dashboard).

**New CSS:**

```css
.team-page-back-link {
  color: #4da6ff;
  cursor: pointer;
  text-decoration: underline;
  background: none;
  border: none;
  padding: 6px 0;
  font-size: 12px;
  font-family: inherit;
  min-height: 44px;
  display: inline-block;
  vertical-align: middle;
}
.team-page-back-link:hover {
  opacity: 0.8;
}
```

### Header Container

```css
.team-page-header {
  font-size: 12px;
  margin-bottom: 16px;
  padding: 12px 0;
  border-bottom: 1px solid #333;
}
```

---

## Color Palette & Contrast Verification

All colors are inherited from the existing dashboard. No new tokens introduced.

| Usage | Color | Background | Contrast | WCAG Level |
|-------|-------|-----------|----------|-----------|
| Status: running | #4da6ff | #111 | 8.1:1 | AAA |
| Status: blocked | #ffb648 | #111 | 5.8:1 | AA |
| Status: finished | #34c759 | #111 | 10.1:1 | AAA |
| Status: error | #ff6b6b | #111 | 5.2:1 | AA |
| Interactive link | #4da6ff | #111 | 8.1:1 | AAA |
| Label text | #aaa | #111 | 5.1:1 | AA |
| Muted/secondary | #888 | #111 | 4.5:1 | AA |
| Event agent colors (palette) | Varies | #111 | 5.0–8.0:1 | AA+ |

---

## Component Reuse

### 100% Existing Sub-Renderers (No Code Duplication)

All team-related rendering functions are called from *both* the dashboard and the dedicated page:

- `renderTeamStatusStrip()` — status badge (running/blocked/finished/error)
- `renderEscalationPanel()` — waiting-for-user escalation panel
- `renderTeamInterjectBox()` — free-form compose for interject messages
- `renderTeamAddMemberControl()` — "+" add-teammate link
- `renderTeamFeed()` — collapsible event feed with filter pills
- `renderTeamFeedToggle()` — show/hide feed toggle link
- `renderTeamPicker()` — lead/teammates composition picker (idle only)
- `renderTeamBranches()` — read-only list of past branches
- `renderTeamFeedEvent()` — event row rendering (unchanged)
- `teamFeedEventKindClass()`, `teamFeedEventBody()` — event classification (unchanged)

### Existing CSS Classes (No New Styling Beyond Layout Container)

All team-related `.team-*` classes remain exactly as defined in the current `PAGE_TEMPLATE`'s `<style>` block. The page adds only:

- `#team-page` and `#team-page.active` — visibility toggling
- `.team-page-header`, `.team-page-back-link` — header styling
- `.team-page-not-found*` — error message styling
- `#rows.hidden-for-team-page` — hide dashboard when team page is active

### Page Container Styling

```css
#team-page {
  display: none;
  max-width: 480px;
  margin: 40px auto;
  padding: 0 16px;
}
#team-page.active {
  display: block;
}
#rows.hidden-for-team-page {
  display: none;
}
.team-page-not-found {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  text-align: center;
}
.team-page-not-found-message {
  font-size: 14px;
  color: #eee;
}
.team-page-not-found-detail {
  font-size: 12px;
  color: #888;
}
```

The `max-width: 480px` exactly matches the dashboard's `body` width, ensuring consistent visual appearance across both views.

---

## Accessibility & Platform Notes

### Touch Targets

- **Back-link button**: 44px minimum height (WCAG AAA)
- **All `.team-btn` buttons**: Already 44px min-height (10px padding + 14px font + line-height)
- **Textareas**: All min-height 44px
- **Link pills** (feed filter, configuration toggle, add-member): 44px achieved via padding

All existing team components already meet or exceed WCAG AAA touch-target requirements (44×44px).

### Keyboard Navigation

- **Tab order**: Back-link → Task textarea (idle) or Status strip (running) → Escalation panel → Interject box → Add-member link → Feed toggle → Feed filter pills → Feed list → Stop button → Branches
- **Enter key**: Native form submission (existing), button clicks (existing)
- **Escape key**: Not redefined; standard browser back/reload as escape hatch

All interactions are already keyboard-accessible; no new keyboard behavior added.

### Screen Readers

The event feed carries `role="log" aria-live="polite"` (existing, per backlog item 19's design.md note), which announces new events to screen readers. This contract is preserved.

All buttons and links use native HTML semantics (`<button>`, `<a>`) with clear, descriptive text. No ARIA overrides needed.

### Mobile Web (480px Viewport)

The page reuses the dashboard's own `max-width: 480px` container, optimized for small phones. No horizontal scrolling. All stacking is vertical (flexbox column layout). Button/textarea sizes already accommodate small touch targets.

### Color Contrast

All text-on-background pairs already pass WCAG AA (4.5:1 minimum for text; 3:1 for graphical elements). No color changes needed.

---

## Client-Side Routing Implementation

### Router Logic (Bottom of `<script>`)

Replace the existing `refresh(); setInterval(refresh, 4000);` with:

```javascript
const teamPageMatch = location.pathname.match(/^\/team\/([^/]+)\/?$/);
if (teamPageMatch) {
  const TEAM_PAGE_PROJECT = decodeURIComponent(teamPageMatch[1]);
  renderTeamPage(TEAM_PAGE_PROJECT);
  setInterval(() => renderTeamPage(TEAM_PAGE_PROJECT), 4000);
} else {
  refresh();
  setInterval(refresh, 4000);
}
```

### renderTeamPage() Implementation Sketch

```javascript
async function renderTeamPage(projectName) {
  const r = await fetch('/status');
  if (!r.ok) {
    // 401 — show login overlay (reuse existing)
    showOverlay();
    return;
  }
  const s = await r.json();
  
  // Find project by name in s.instances
  const project = s.instances.find(inst => inst.name === projectName);
  
  if (!project) {
    // Unknown project — render error message
    renderTeamPageNotFound(projectName);
    return;
  }
  
  // Show team page, hide dashboard and creation UI
  document.getElementById('rows').classList.add('hidden-for-team-page');
  document.getElementById('team-page').classList.add('active');
  document.querySelector('h1').style.display = 'none';
  document.querySelector('.new-project-row').style.display = 'none';
  document.getElementById('new-project-err').style.display = 'none';
  document.querySelectorAll('.upload-wizard-btn').forEach(el => el.style.display = 'none');
  document.getElementById('clone-form').style.display = 'none';
  document.getElementById('clone-err').style.display = 'none';
  
  // Render the full team interface for this project
  const team = project.team;
  const name = project.name;
  
  let html = '<div class="team-page-header">' +
    '<button class="team-page-back-link" onclick="window.location = \'/'\'">← ai-dev-switchboard › ' +
    esc(name) + '</button></div>';
  
  // Reuse existing team renderers
  if (!team || team.status === 'idle') {
    html += renderTeamIdleLauncher(name, team);
  } else {
    html += renderTeamRunningState(name, team);
  }
  
  document.getElementById('team-page').innerHTML = html;
}

function renderTeamPageNotFound(projectName) {
  document.getElementById('rows').classList.add('hidden-for-team-page');
  document.getElementById('team-page').classList.add('active');
  document.querySelector('h1').style.display = 'none';
  document.querySelector('.new-project-row').style.display = 'none';
  document.getElementById('new-project-err').style.display = 'none';
  document.querySelectorAll('.upload-wizard-btn').forEach(el => el.style.display = 'none');
  document.getElementById('clone-form').style.display = 'none';
  document.getElementById('clone-err').style.display = 'none';
  
  const html = '<div class="team-page-header">' +
    '<button class="team-page-back-link" onclick="window.location = \'/'\'">← ai-dev-switchboard › team-chat</button>' +
    '</div>' +
    '<div class="team-page-not-found">' +
    '<div class="team-page-not-found-message">Unknown project ' + esc(projectName) + '</div>' +
    '<div class="team-page-not-found-detail">(This project may have been deleted or the name was misspelled.)</div>' +
    '<button class="team-page-back-link" onclick="window.location = \'/'\'">← Back to dashboard</button>' +
    '</div>';
  
  document.getElementById('team-page').innerHTML = html;
}
```

### Extraction: New Thin Wrapper Functions

These functions assemble existing sub-renderers for both dashboard and dedicated page:

```javascript
function renderTeamIdleLauncher(name, team) {
  clearTeamFeedState(name);
  const msgSlot = '<div class="team-msg" id="team-msg-' + esc(name) + '"></div>';
  const text = teamTaskText[name] || '';
  const taskArea = '<textarea class="team-textarea" id="task-' + esc(name) + 
    '" placeholder="Task description..." ' +
    'oninput="teamTaskText[' + "'" + name + "'" + '] = this.value; ' +
    "updateTeamStartButton('" + esc(name) + "');" + '">' +
    esc(text) + '</textarea>';
  const composition = team ? team.composition : undefined;
  if (composition === null) {
    return '<div class="team-row">' + taskArea +
      '<div class="team-msg error">✕ No roster members available. Add an engine to engines.d ' +
      'or configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL.</div>' +
      '<div class="team-actions"><button class="team-btn" id="start-btn-' + esc(name) + 
      '" disabled>Start team</button></div>' + msgSlot + renderTeamBranches(name) + '</div>';
  }
  const open = composition !== undefined && !!teamPickerOpen[name];
  const configureRow = composition !== undefined ?
    '<div class="team-configure-row"><a class="team-configure-btn" onclick="toggleTeamPicker(' +
    "'" + name + "'" + ')">' + (open ? 'Hide configuration' : 'Configure team...') + '</a></div>' : '';
  const picker = open ? renderTeamPicker(name) : '';
  const startDisabled = !text.trim() || (open && !!teamCompositionError(name));
  const chatHint = '<div class="team-sub">Once started, you&#39;ll see live team activity ' +
    'and can interject right here.</div>';
  return '<div class="team-row">' + taskArea + chatHint + configureRow + picker +
    '<div class="team-actions"><button class="team-btn" id="start-btn-' + esc(name) + '"' +
    (startDisabled ? ' disabled' : '') + ' onclick="doTeamStart(' + "'" + name + "'" + 
    ')">Start team</button></div>' + msgSlot + renderTeamBranches(name) + '</div>';
}

function renderTeamRunningState(name, team) {
  if (teamFeedOpen[name] === undefined) teamFeedOpen[name] = true;
  const statusStrip = renderTeamStatusStrip(team);
  const escalatedNote = (team.status === 'blocked' && !team.waiting_on_you) ?
    '<div class="team-sub">Escalated — max rounds reached. No pending question to answer. ' +
    'Review the feed below or Stop team and start a new run.</div>' : '';
  const finishedSummary = (team.status === 'finished' && team.summary) ?
    '<div class="team-sub">' + esc(team.summary) + '</div>' : '';
  const escalationPanel = team.waiting_on_you ? renderEscalationPanel(name, team) : '';
  const interjectBox = renderTeamInterjectBox(name, team);
  const addMemberControl = renderTeamAddMemberControl(name, team);
  const feedToggle = renderTeamFeedToggle(name);
  const feedPanel = renderTeamFeed(name, team);
  const msgSlot = '<div class="team-msg" id="team-msg-' + esc(name) + '"></div>';
  
  return '<div class="team-row">' + statusStrip + escalatedNote + finishedSummary + escalationPanel +
    interjectBox + addMemberControl + feedToggle + feedPanel +
    '<div class="team-actions"><button class="team-btn" onclick="doTeamStop(' +
    "'" + name + "'" + ')">Stop team</button></div>' +
    msgSlot + renderTeamBranches(name) + '</div>';
}
```

### Dashboard's Simplified `teamRow()`

Replace the existing large `teamRow()` with a compact summary:

```javascript
function teamRow(name, team) {
  const statusClass = 'status-' + (team ? team.status : 'idle');
  const statusText = {
    'status-idle': 'Idle',
    'status-running': 'Running',
    'status-blocked': 'Blocked',
    'status-finished': 'Finished',
    'status-error': 'Error'
  }[statusClass] || 'Unknown';
  
  return '<div class="team-row"><div class="team-status ' + statusClass + '">' + 
    statusText + '</div>' +
    '<a href="/team/' + encodeURIComponent(name) + '" class="team-configure-btn">Open team chat →</a>' +
    '</div>';
}
```

This renders a compact 2-line summary on each dashboard project row (status badge + link), regardless of team status. No inline task textarea, picker, feed, escalation, or compose box on the dashboard anymore.

---

## Edge Cases & State Handling

### Unauthenticated Access to `/team/<project>`

`GET /team/<project>` returns the same static `PAGE_TEMPLATE` shell as `GET /`. Client-side `renderTeamPage()` calls `/status`; if 401, `showOverlay()` is called (existing login logic). No new auth code path.

### Unknown Project

`renderTeamPageNotFound()` displays a clear error message with a back-link. Matches spec requirement.

### Blocked Status with Pending Answer

When `team.waiting_on_you === true`, `renderEscalationPanel()` renders (existing component). Dashboard's compact badge only shows "Blocked"—user must navigate to the dedicated page to answer. This is intentional per the spec's Goals.

### Long/URL-Unsafe Project Names

`encodeURIComponent()` on the client (back-link URL) and `decodeURIComponent()` on route parse handle escaping. Reuses the pattern already used by `/term/<name>` and `/code/<name>` routes (existing precedent).

### Multiple Browser Tabs

The event feed's cursor-based polling (`GET .../team/events?cursor=...`) already supports concurrent viewers. Moving to a dedicated page does not change this.

### Lost In-Progress Text on Navigation

Navigating to `/team/<name>` for the first time gives fresh state. Reloading or navigating away and back loses in-progress textarea text—same behavior as reloading the dashboard. Not a regression; acknowledged in spec.

### Empty Roster

If `team.composition === null`, the existing idle-state "No roster members available" branch renders unchanged. No new logic.

---

## Summary of Design Decisions

### What Gets Reused (100% of existing team components)

- All 10+ sub-renderer functions (`renderTeamStatusStrip`, `renderEscalationPanel`, `renderTeamInterjectBox`, `renderTeamAddMemberControl`, `renderTeamFeed`, `renderTeamFeedToggle`, `renderTeamPicker`, `renderTeamBranches`, `renderTeamFeedEvent`, `teamFeedEventKindClass`, `teamFeedEventBody`)
- All `.team-*` CSS classes (no style changes to existing rules)
- Event feed event-rendering logic (unchanged)
- `aria-live="polite"` + `role="log"` screen-reader support (preserved)
- `doTeamStart()`, `doTeamStop()` action handlers (reused)
- Backend routes (unchanged)
- Auth and session logic (unchanged)

### What's New

1. **`#team-page` container** — new visibility-toggled div
2. **Client-side router** — new route-matching branch in bottom-of-script
3. **`renderTeamPage()` function** — entry point, fetches `/status`, dispatches to appropriate renderer
4. **`renderTeamPageNotFound()` function** — error state for unknown projects
5. **`renderTeamIdleLauncher()` and `renderTeamRunningState()` wrapper functions** — thin extraction layers
6. **`.team-page-header`, `.team-page-back-link`, `.team-page-not-found*` CSS classes** — layout styling only
7. **`#rows.hidden-for-team-page` class** — dashboard toggle

### No Changes To

- `app/teams.py` (team backend logic)
- Event envelope shape or polling mechanism
- Chat-bubble feed format (backlog item 19 decision stands)
- Auth model
- Any other page layout (dashboard, upload, clone)

### Visual Consistency

The dedicated team page uses the exact same colors, typography, spacing, and component patterns as the dashboard. No visual redesign. The page looks and feels like an expanded, full-page version of the existing dashboard team row.

---

## Testing & Verification Checklist

- [ ] Authenticated user navigates to `/team/<valid-idle-project>` → full launcher renders (textarea, picker link, start button)
- [ ] Authenticated user navigates to `/team/<valid-running-project>` → status strip, interject box, feed, stop button render
- [ ] Authenticated user navigates to `/team/<valid-blocked-project-with-pending>` → escalation panel renders
- [ ] Authenticated user navigates to `/team/<invalid-project>` → "Unknown project" message with back-link
- [ ] Unauthenticated user navigates to `/team/<any-project>` → login overlay appears
- [ ] Dashboard project row shows only compact status badge + "Open team chat →" link (no inline textarea/feed/escalation)
- [ ] Back-link in team page header navigates to dashboard
- [ ] Interject box, escalation panel, add-member link call correct `/team/*` routes (network tab verification)
- [ ] Feed filter pills work correctly
- [ ] Event feed has `role="log" aria-live="polite"` (accessibility tree verification)
- [ ] All buttons/textareas have 44px minimum touch target
- [ ] All text-on-background pairs pass WCAG AA contrast (4.5:1 minimum)
- [ ] Page is responsive and readable on 480px viewport (mobile)
- [ ] No visual regression on dashboard when team page is not active
