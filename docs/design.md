# Design: Minimal per-project team control (sub-spec 6d part 2a)

## Summary
A single-row team control rendered inline on each project row, positioned after the deploy row, with two visual modes: an idle state showing a task-text input and "Start team" button, and a running state showing a coarse status label (idle/running/blocked/finished/error) and "Stop team" button. Error messages (tier-3-only refusal, missing roster members) display inline in the same row, following the `deploy` row's messaging pattern. No new visual language — all styling and typography reuse the existing page conventions.

## ui-ux-pro-max choices
- **Style**: Inline row control, following the existing `deployRow()` pattern (not the checkbox-toggle, since team start requires a task text input, not a boolean flip)
- **Palette**: Reuses existing page token set; no new colors introduced
- **Typography**: Existing body/label sizes; no new typefaces
- **Relevant UX guidelines applied**:
  - Button disabled state when input is empty (client-side hint; server-side validation is authoritative)
  - Clear, actionable error messages naming the two concrete fixes (for tier-3-only refusal)
  - Status labels are coarse and polled, designed to tolerate staleness up to one poll interval (~4 seconds)
  - Confirmation dialog on Stop (destructive action: kills processes, tears down worktrees, may discard in-flight work)

## Component reuse
- **Reused**: Existing HTML/JS patterns from `deployRow()` and `doDeploy()` — inline row rendering, direct `fetch()` POST plumbing, inline result message slot, existing TOTP code-overlay machinery for confirmation (via `toggle()`/`handleActionResult()`)
- **Reused**: Existing `/status` poll (every 4 seconds, unchanged) — no new timer; team row updates from the existing `team` field added to the per-instance status dict
- **New (none)**: No new component, no new library. Plain HTML/CSS/JS matching the page's embedded script.

## States

### Idle
Rendered when `team.status === "idle"` or team is `null` (no run ever started for this project):

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ <textarea id="task-<name>" placeholder="Task description...">   │
│ </textarea>                                             │
│                                                         │
│ <button onclick="doTeamStart('<name>')"  [disabled] >  │
│   Start team                                            │
│ </button>                                               │
└─────────────────────────────────────────────────────────┘
```

**Styling**: Single row. Label "Team" in the row header (consistent with other rows on the page). Textarea is full-width, single column layout, placeholder text "Task description...". Button "Start team" appears below the textarea, styled to match other action buttons on the page (same class/styling as "Deploy" button). Button is **disabled** (greyed out, `disabled` attribute set) while textarea is empty (client-side validation only; empty task will fail with a 400 server-side if somehow submitted anyway). Button text is exactly "Start team".

**Copy**: 
- Textarea placeholder: "Task description..."
- Button label: "Start team"

### Populated / Running
Rendered when `team.status` is one of: `"running"`, `"blocked"`, `"finished"`, `"error"`:

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ Status: [running]   ID: run-abc123                     │
│                                                         │
│ <button onclick="doTeamStop('<name>')"                 │
│   Stop team                                             │
│ </button>                                               │
└─────────────────────────────────────────────────────────┘
```

Or, with `team.status === "blocked"`:

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ Status: [blocked]   ID: run-abc123                     │
│ Lead is waiting for input · check tmux attach           │
│                                                         │
│ <button onclick="doTeamStop('<name>')"                 │
│   Stop team                                             │
│ </button>                                               │
└─────────────────────────────────────────────────────────┘
```

**Styling**: The textarea is **completely hidden** when status is not idle. Row shows: "Status: [label] ID: run-id". Status label is rendered as inline text, not a badge, but color-coded:
- `running` → blue/normal text
- `blocked` → orange/warning color (operator should know to check what's being asked)
- `finished` → green/success color
- `error` → red/error color

Below the status line, when `blocked`, add a subtitle: "Lead is waiting for input · check tmux attach" (encourages operator to use tmux to see the actual question/options).

Button "Stop team" appears below, styled to match "Deploy" button styling. Exactly "Stop team", no verb conjugation.

**Polling & staleness**: The status label reflects the last `/status` poll result, which runs every 4 seconds. A run that briefly reports `error` before self-correcting (e.g., a restart case) may show `error` for up to one poll interval (~4s) before showing correct status — this is an accepted tradeoff per spec. No visual "stale" warning; the design assumes operators understand that statuses are eventually consistent.

### Error (Failed Start)
Rendered when a `POST /projects/<name>/team/start` returns 4xx and the operator is shown the error. Two error cases are designed:

**Case 1: Tier-3-only roster refusal**
```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ <textarea id="task-<name>" placeholder="Task description...">   │
│ </textarea>                                             │
│                                                         │
│ <button onclick="doTeamStart('<name>')">               │
│   Start team                                            │
│ </button>                                               │
│                                                         │
│ ✕ Error: only a tier-3 (prose-parse, least reliable)  │
│   lead is available — configure TEAM_LLM_BASE_URL/     │
│   TEAM_LLM_MODEL, or add a tier-2 (schema-capable)    │
│   engine to engines.d. The CLI's --lead can still      │
│   select a tier-3 lead explicitly.                     │
└─────────────────────────────────────────────────────────┘
```

**Case 2: No roster members available (e.g., only one engine, it was selected as lead)**
```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ <textarea id="task-<name>" placeholder="Task description...">   │
│ </textarea>                                             │
│                                                         │
│ <button onclick="doTeamStart('<name>')">               │
│   Start team                                            │
│ </button>                                               │
│                                                         │
│ ✕ Error: only one headless-eligible engine ('<name>') │
│   is configured and it was selected as lead — add      │
│   another engine to engines.d or configure             │
│   TEAM_LLM_BASE_URL/TEAM_LLM_MODEL to free it up as a  │
│   teammate.                                             │
└─────────────────────────────────────────────────────────┘
```

**Other errors** (unknown project, missing task, network error during start):
```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ <textarea id="task-<name>" placeholder="Task description...">   │
│ </textarea>                                             │
│                                                         │
│ <button onclick="doTeamStart('<name>')">               │
│   Start team                                            │
│ </button>                                               │
│                                                         │
│ ✕ Error: <server error message>                        │
└─────────────────────────────────────────────────────────┘
```

**Styling & behavior**: 
- Error messages appear inline in the row, below the button
- Red text with an error icon (✕ or similar)
- Message uses the exact text returned from the server (`error` field in 4xx response)
- Message persists until the next `refresh()` call (4-second poll), similar to deploy message behavior
- Error does NOT prevent the operator from trying again — textarea and button remain enabled
- Each start attempt clears the previous error message

### Stop Confirmation Dialog
When operator clicks "Stop team", a native `confirm()` dialog appears:

```
┌────────────────────────────────────────────────────────────┐
│ Stop team?                                                 │
│                                                            │
│ This will kill any in-flight processes, remove git        │
│ worktrees, and stop the running session. Any uncommitted  │
│ work will be lost. Continue?                              │
│                                                            │
│         [ Cancel ]              [ OK ]                    │
└────────────────────────────────────────────────────────────┘
```

**Rationale for confirmation**: Stopping a team is destructive (kills processes, tears down worktrees, may discard in-flight work). This matches the `deploy` action's own confirmation pattern, which uses `confirm()` for a deliberate, lightweight confirmation step. The dialog prevents accidental stops on a busy project.

**Exact message** (passed to `confirm()`): `"Stop team? This will kill any in-flight processes, remove git worktrees, and stop the running session. Any uncommitted work will be lost. Continue?"`

If the operator clicks "Cancel", nothing happens — the row stays as-is, status unchanged. If they click "OK", the `doTeamStop()` function proceeds with the same TOTP code-overlay machinery as other destructive actions (`kind='team-stop'`, reusing existing `toggle()`/`handleActionResult()` plumbing).

### Stop Result Message
After `/projects/<name>/team/stop` completes:

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ Status: [running]   ID: run-abc123                     │
│                                                         │
│ <button onclick="doTeamStop('<name>')"                 │
│   Stop team                                             │
│ </button>                                               │
│                                                         │
│ ✓ Team stopped successfully                            │
└─────────────────────────────────────────────────────────┘
```

**Styling**: Green success message, appears inline below the button, persists until next `refresh()` (like deploy message). The status label itself will update to `idle` on the next `/status` poll (at most 4 seconds later).

## Accessibility & platform notes

- **Touch target sizes**: Button "Start team" and "Stop team" follow the page's existing button styling and hit area (minimum 44px on mobile, but this is a desktop web app, so typical 36-40px desktop minimum). Both buttons are keyboard-accessible via tab order and Enter key.
- **Color contrast** (corrected 2026-08-14, reviewer-found: the analysis below originally assumed a light theme that was never built and the surrounding app's own dark UI, and its arithmetic was wrong in 3 of 5 cases even against that assumed background. This app's real background is `#111` (page body) / `#1c1c1c` (each `.row`, including the team row) — every status color is a token already shipped elsewhere on this page, not a new one invented for this feature. Ratios below are computed against `#1c1c1c`, the actual background the status text sits on, using the standard WCAG relative-luminance formula, not estimated):
  - Running (`#4da6ff`, the page's own existing link-blue token — see the `a { color: #4da6ff; }` rule): contrast ratio **6.67:1** on `#1c1c1c`, **passes WCAG AA** (4.5:1) for normal text; just under AAA's 7:1 threshold, which this design does not require.
  - Blocked (`#ffb648`, new — no existing "warning/orange" token was already in use on this page to reuse): contrast ratio **9.77:1** on `#1c1c1c`, **passes WCAG AAA**.
  - Finished (`#34c759`, the page's own existing success-green token — see `.deploy-msg.success`/`input:checked + .slider`): contrast ratio **7.68:1** on `#1c1c1c`, **passes WCAG AAA**.
  - Error (`#ff6b6b`, the page's own existing error-red token — see `.deploy-msg.error`/`.taiga-err`/`.gitea-err`): contrast ratio **6.14:1** on `#1c1c1c`, **passes WCAG AA** comfortably (well clear of 4.5:1); no darkening needed, unlike the original (light-theme-assumed) analysis concluded.
  - Error icon (✕): as a graphical element (non-text), needs 3:1 minimum contrast against its background — `#ff6b6b` on `#1c1c1c` at 6.14:1 clears this with margin.
  - Error/success message text (`.team-msg.error`/`.team-msg.success`, same `#ff6b6b`/`#34c759` tokens `.deploy-msg` already established): both already meet AA (and AAA) for text at this app's real background, confirmed by the same computation above.
- **Web vs. native**: This is a web app (HTML/CSS/JS in a Flask template), no native/mobile variant. Desktop-only. No hover states for error messages (they're static text).
- **Textarea accessibility**: Textarea has a `placeholder` attribute (does not substitute for a label, but this row is already labeled "Team" in the row header, so context is clear). Textarea is keyboard-accessible and screen-reader-visible.
- **Keyboard interaction**:
  - Tab into textarea → enter task
  - Tab to "Start team" button → press Enter to start (button is auto-disabled when textarea is empty)
  - When team is running, Tab into "Stop team" button → press Enter (confirm() dialog appears)
- **Status label readability**: The coarse status labels (idle/running/blocked/finished/error) are intentionally short to be glance-readable; detailed diagnostics require `tmux attach`.

## Traceability to spec

| Acceptance criterion (from docs/spec.md) | Where it's addressed in this design |
|---|---|
| Minimal per-project control with task-text input, Start/Stop buttons, coarse status label | Idle state (textarea + Start button); Running state (status label + Stop button) |
| Follows the existing `deploy` row rendering pattern, not checkbox-toggle | `teamRow()` function styled after `deployRow()` — single-purpose row, inline render, direct fetch plumbing for Start, toggle()/handleActionResult() for Stop |
| Tier-3-only roster refuses with actionable error message | Error state shows exact message from server: names both concrete fixes (configure TEAM_LLM_*, or add tier-2 engine) |
| Task-text input empty state disables Start button | Textarea is validated client-side (disabled button while empty); server-side 400 if somehow empty task is sent |
| No lead/member picker (6e) | Not designed — default composition only, per `default_team_composition()` in backend |
| No live event feed, no rendered timeline (6f) | Not designed — status label only; operator uses `tmux attach` to see details (unchanged from part 1) |
| Blocked state indicates lead is waiting | Status shows "blocked"; subtitle "Lead is waiting for input · check tmux attach" prompts operator action |
| Status is polled, can be stale | Design tolerates up to 4-second staleness (existing poll interval); restart-error transient is accepted per spec tradeoff |
| Start spawns processes and creates worktrees | Implicit in backend; UI just shows success/error |
| Stop kills processes, tears down worktrees | Confirmation dialog warns operator; same pattern as Deploy (confirm() for destructive action) |
| Error messages are inline in row | Error state shows red text below button; persists until next refresh() |
| `/status` field `team.status` maps: running/blocked_ask_user/escalated_max_rounds/finished/error/stopped/idle | Mapping in JavaScript: running→"running", blocked_ask_user→"blocked", escalated_max_rounds→"blocked", finished→"finished", error→"error", stopped→"idle", null→"idle" |
| Server routes POST `/projects/<name>/team/start` and `/projects/<name>/team/stop` exist | `doTeamStart()` calls `fetch()` to `/projects/<name>/team/start` with `{task: ...}` body; `doTeamStop()` uses `toggle(kind='team-stop')` → `actionPath()` routes to `/projects/<name>/team/stop` |
| TOTP code overlay for destructive actions | Stop button action reuses existing `toggle(kind, name, on, checkboxEl)` and TOTP `handleActionResult()` machinery |

## Implementation notes for the developer

1. **Render function**: Add `teamRow(name, team)` to `app/app.py`'s embedded JavaScript. Called from `row()` when `kind === 'inst'`, after `deployRow()` and `codeRow()`, to maintain consistent row order across all projects.

2. **Status mapping**: In `/status` GET handler, map latest run state to coarse label:
   ```
   running → "running"
   blocked_ask_user → "blocked"
   escalated_max_rounds → "blocked"
   finished → "finished"
   error → "error"
   stopped → "idle"
   null → "idle"
   ```

3. **Textarea ID and message slot ID**: `id="task-<name>"` for textarea; `id="team-msg-<name>"` for error/success message slot (follows deploy-msg pattern).

4. **Error message slot**: Always rendered (empty initially), filled by `doTeamStart()` or the response handler.

5. **Confirmation message exact text** (for `confirm()` in `doTeamStop()`): "Stop team? This will kill any in-flight processes, remove git worktrees, and stop the running session. Any uncommitted work will be lost. Continue?"

6. **Client-side start validation**: Textarea empty check before `doTeamStart()` proceeds — if empty, do not POST, show client-side message "Enter a task description."

7. **Color tokens** (corrected 2026-08-14 — see "Color contrast" above): this app's real theme is dark (`#111`/`#1c1c1c` backgrounds), not the light theme this line originally assumed. Use the page's own existing tokens: `#ff6b6b` for error (already used by `.deploy-msg.error`/`.taiga-err`/`.gitea-err`), `#4da6ff` for "running" (already used for links, `a { color: #4da6ff; }`), `#34c759` for "finished" (already used by `.deploy-msg.success`). No existing "warning/orange" token was already in use on this page — `#ffb648` is a new addition for "blocked", chosen for AAA contrast (9.77:1) against `#1c1c1c`.

8. **Styling**: `.team-row`, `.team-textarea`, `.team-status`, `.team-msg` classes, following page's existing BEM-lite naming (e.g., `deploy-row`, `deploy-msg`). No new component library.

9. **Polling refresh**: No new timer. Existing `refresh()` every 4s already re-fetches `/status` and re-renders the row via the new `team` field in the status dict.

---

# Design: Roster & composition UI (sub-spec 6e)

## Summary

Extend the idle-state team row with a lead/teammate picker that allows the operator to select which roster members lead and which are teammates before starting a team. The picker lives inside the existing per-project idle-state row (preserving the one-page architecture), replacing the 6d design's minimal row with an expanded, collapsible picker panel. The roster is fetched fresh from `/status` (no cache) and reflects `engines.d` live; each member shows its lead-adapter tier (1/2/3) and, for tier 3, a plain-language reliability caveat. Before-start grounding discovery shows which of the four discovery files were found, alerting the operator to absences (e.g., no `ARCHITECTURE.md`). Client-side validation mirrors the server's own rules: non-empty members, no duplicates, no lead-also-a-teammate exclusion. Submitted compositions are persisted to `TEAM_STATE_DIR/compositions.json` and pre-populate the picker on the next `/status` poll, surviving service restarts.

## Open questions — resolved decisions

**Question 1: Can an engine be selected as lead AND as a delegate-target teammate simultaneously?**

**Decision: No.** Lead's name must not also appear in `members`. This rule mirrors `default_team_composition()`'s own existing exclusion of its picked engine-lead from the members list, applied here as a code rule rather than an accident of how the default is built.

**Rationale:**
- Matches existing precedent: `default_team_composition()` never includes its chosen lead in the members list, and applying the same rule here keeps the mental model consistent across the codebase.
- Simplicity: A lead that's also a teammate would require reasoning about which worktree/session is leading vs. delegating to itself, introducing subtle questions about context and state.
- The one-line change to relax this rule in `validate_composition()` is trivial if evidence emerges that the use case is real and valued — the technical cost of keeping it tight is minimal.

A future relaxation (if the delegation-to-same-engine-fresh-worktree use case proves essential) is a compatible, backward-compatible change with no migration required.

**Question 2: Where does the roster/composition UI live?**

**Decision: Inside the per-project idle-state teamRow(), as an expanded picker section.**

**Rationale:**
- The app is one-page architecture; `render_page()` serves a static shell with everything rendered client-side from `/status`. Adding a new page breaks this design.
- The picker needs project context: which project are we configuring? The idle row already carries that.
- The grounding files are project-specific; they belong in a per-project context.
- The existing 6d row already lives here; extending it with picker UI reuses the render pattern rather than inventing a new surface.
- An operator would naturally look for composition config where they see the team control — in the project row itself — not in a separate settings page.

## ui-ux-pro-max choices

- **Style**: Expanded picker panel within the existing idle-state team row, using a collapsible/expandable pattern to keep the UI compact unless actively configured.
- **Palette**: Reuses existing page tokens (`#4da6ff` for tier labels, `#cccccc`/`#aaaaaa` for disabled checkboxes, `#34c759` for saved-composition confirmation). No new color tokens for the picker UI itself (tier-3 caveat text uses existing `#ffb648` warning orange, computed for 9.77:1 contrast with `#1c1c1c`).
- **Typography**: Existing page font sizes and weights; no new typefaces. Tier labels as small `.badge` elements (existing pattern from 6d for status labels). Checkbox labels use body text.
- **Relevant UX guidelines applied**:
  - Checkboxes for multi-select (teammate list): standard, unchecked by default; only valid saved-composition pre-populates them.
  - Radio or select for single-choice (lead): select-dropdown preferred for space efficiency given roster can grow to many engines.
  - Tier labels as visual chips/badges: colors and short text ("tier 1", "tier 2", "tier 3") make the tradeoff visible at a glance.
  - Tier-3 caveat as a tooltip or collapsible note (kept close to the tier label, not a separate field): "This engine relies on prose parsing for tool-calling decisions, which is less reliable than native tool support (tier 1) or constrained-output JSON (tier 2). Use this if no tier-1 or tier-2 lead is available."
  - Grounding summary: a simple list of file names with checkmarks/crosses or "found"/"not found" status (read-only, no action here — just discovery).
  - Validation messaging: client-side mirrors server rules inline before submission; server-side validation is the source of truth.
  - Pre-selection from saved composition: if `inst.team.composition` exists (from `/status`), pre-select those lead/members in the picker; else fall back to `default_team_composition()`'s pick if available.

## Component reuse

- **Reused**: Existing HTML/CSS/JS patterns from `teamRow()` (6d) — inline row rendering, same `.team-row` / `.team-msg` classes, direct `fetch()` POST plumbing to `/projects/<name>/team/start`.
- **Reused**: Existing `/status` poll (every 4 seconds) — roster and pre-selected composition come from `/status`'s new `roster` and `inst.team.composition` fields; no new timer.
- **Reused**: Existing grounding route call pattern — new `GET /projects/<name>/team/grounding` route (backend-defined in spec) is called once on row render/refresh, similar to how the existing page fetches project data.
- **Reused**: Existing TOTP code-overlay machinery for the Start button (same as 6d).
- **Reused**: Existing error/success message slot (`.team-msg` id pattern).
- **New (none)**: No new component library, no new external dependencies. Plain HTML (select, input checkboxes, labels) + inline CSS (no new CSS classes beyond `.team-lead-picker`, `.team-grounding`, etc., following BEM-lite naming).

## States

### Idle, Picker Closed (initial)
Rendered when `team.status === "idle"` or team is `null`, picker not yet expanded:

```
┌─────────────────────────────────────────────────────────────┐
│ Team                                                        │
│                                                             │
│ <textarea placeholder="Task description...">...</textarea>  │
│ [ Configure team... ] (toggles picker open)                │
│                                                             │
│ <button disabled/enabled>Start team</button>               │
└─────────────────────────────────────────────────────────────┘
```

**Styling**: Textarea unchanged from 6d. A small inline link or button "Configure team..." appears below the textarea, styled to look clickable (color: `#4da6ff`, cursor: pointer, no background). Clicking it toggles the picker panel open. If no saved composition exists, the button text is "Configure team..."; if a saved composition exists, the button text is "Reconfigure team" (optional — same button text works fine).

**Behavior**:
- Roster and grounding are fetched on picker open (not on every 4s poll, but on the click that opens the picker).
- If roster is empty (no engines, no Ollama), show "No roster members available" and disable configure button.
- If default composition is rejected but the roster is non-empty (e.g., tier-3-only), `/status` sends `composition: {"lead": null, "members": []}` rather than `null` — the picker behaves exactly like any other unconfigured composition: collapsed behind "Configure team...", nothing pre-selected, no auto-expand and no special-cased always-visible caveat. See "Tier-3-Only Roster" below, corrected post-implementation.

### Idle, Picker Expanded
Rendered when operator clicks "Configure team..." and the picker opens:

```
┌─────────────────────────────────────────────────────────────┐
│ Team                                                        │
│                                                             │
│ <textarea placeholder="Task description...">...</textarea>  │
│                                                             │
│ [ ▼ Hide configuration ] (click to close picker)           │
│                                                             │
│ ┌─ Lead ────────────────────────────────────────┐          │
│ │ <select id="team-lead-<name>">                │          │
│ │   <option value="">Choose a lead...           │          │
│ │   <option value='{"kind":"engine","name":"claude"}'>     │
│ │     claude (tier 2 - schema constrained)     │          │
│ │   <option ...>ollama (tier 1 - native tools) │          │
│ │   <option ...>aider (tier 3 - prose parse... │          │
│ │ </select>                                     │          │
│ └───────────────────────────────────────────────┘          │
│                                                             │
│ ┌─ Tier 3 Caveat (shown if tier-3 lead selected) ─┐        │
│ │ ⚠ This engine's reliability is lower due to    │        │
│ │   prose-parsing tool-calling. Use only if no   │        │
│ │   tier-1 or tier-2 lead is available.          │        │
│ └───────────────────────────────────────────────────┘      │
│                                                             │
│ ┌─ Teammates ────────────────────────────────────┐          │
│ │ ☐ claude (tier 2 - schema constrained)        │          │
│ │ ☐ codex (tier 2 - schema constrained)         │          │
│ │ ☑ aider (tier 3 - prose parse, unreliable)    │          │
│ │ [current lead not listed — excluded]          │          │
│ └───────────────────────────────────────────────┘          │
│                                                             │
│ ┌─ Grounding Files ──────────────────────────────┐          │
│ │ ✓ docs/ARCHITECTURE.md (1.2 KB)               │          │
│ │ ✓ docs/BACKLOG.md (3.5 KB)                    │          │
│ │ ✗ CLAUDE.md / AGENTS.md (not found)           │          │
│ │ ✓ README.md (2.1 KB)                          │          │
│ └───────────────────────────────────────────────┘          │
│                                                             │
│ [validation error, if any: "Lead is required"]            │
│                                                             │
│ <button disabled/enabled>Start team</button>               │
└─────────────────────────────────────────────────────────────┘
```

**Lead picker (select dropdown)**:
- Label: "Lead"
- Options: each roster member, rendered as `<option value='{"kind":"engine"|"ollama","name":"..."}'>NAME (tier X - DESCRIPTION)</option>`.
- Default: `<option value="">Choose a lead...</option>` (pre-select to empty if no saved composition; pre-select to saved lead if composition exists).
- On change: immediately show/hide tier-3 caveat if tier-3 is selected; recompute teammate checkboxes to exclude the newly selected lead; rerun client-side validation.

**Tier-3 caveat** (conditional):
- Shown only if the selected lead is tier 3.
- Layout: small alert box (background: `#1c1c1c` darkened slightly, border: `#ffb648` left edge, padding: `0.5em`).
- Icon: optional ⚠ (warning icon, `#ffb648`).
- Text: "This engine's reliability is lower due to prose-parsing tool-calling. Use only if no tier-1 or tier-2 lead is available."
- Contrast: `#ffb648` text on `#1c1c1c` background = **9.77:1** (passes WCAG AAA).

**Teammate checkboxes (multi-select)**:
- Label: "Teammates"
- Options: each roster member with `delegate_capable: true`, rendered as `<label><input type="checkbox" id="team-mate-..."> NAME (tier X - DESCRIPTION)</label>`.
- Excluded: the currently selected lead (cannot be a teammate of itself).
- Excluded: Ollama entry (it's not delegate_capable, per the spec).
- Default: unchecked, unless a saved composition pre-populates them.
- On change: rerun client-side validation (must have at least one teammate selected).
- Visual feedback: disabled checkboxes (for non-delegate-capable members and the lead) are greyed out, not removed.

**Grounding summary**:
- Label: "Grounding Files"
- Layout: list of four file checks, each as a row: `[✓/✗] FILENAME (size)` or `[✓/✗] FILENAME (not found)`.
- Files displayed (in this order, always):
  1. `docs/ARCHITECTURE.md`
  2. `docs/BACKLOG.md`
  3. `CLAUDE.md` (or `AGENTS.md` if CLAUDE.md not found)
  4. `README.md`
- Icons: `✓` (green, `#34c759`) for found files; `✗` (red, `#ff6b6b`) for not found.
- Information: size in KB/MB if file is found; `(not found)` if not.
- Behavior: read-only list, no action here. Fetched from `GET /projects/<name>/team/grounding` on picker open.
- If no grounding files are found: "No grounding files discovered" (still allows team start, per spec).
- If grounding fetch fails: show "Grounding unavailable" (does not block team start).

**Client-side validation** (shown inline before submit):
- If lead is not selected: "Lead is required" (red text, below lead picker).
- If teammates are empty: "At least one teammate is required" (red text, below teammate checkboxes).
- If lead's name is also in teammates: "Lead cannot also be a teammate" (red text, clarifying the exclusion rule).
- Validation runs on lead/teammate change and before submit; Start button is disabled if any validation error exists.

**Copy**:
- Configure button: "Configure team..."
- Lead label: "Lead"
- Teammates label: "Teammates"
- Grounding label: "Grounding Files"
- Tier-3 caveat: "This engine's reliability is lower due to prose-parsing tool-calling. Use only if no tier-1 or tier-2 lead is available."

### Composition Saved
After `/projects/<name>/team/start` succeeds with a submitted composition (POST body includes `lead` and `members`):

```
[same as Idle, Picker Expanded, but]
✓ Composition saved and team started
```

**Styling**: Green success message (color: `#34c759`, same as 6d's finish status) appears below the Start button, persists until next `/status` poll, then fades when status changes to `running`.

### No Roster Available
If `roster()` returns empty (no engines, no Ollama, or all are non-headless):

```
┌─────────────────────────────────────────────────────────────┐
│ Team                                                        │
│                                                             │
│ <textarea placeholder="Task description...">...</textarea>  │
│                                                             │
│ ✕ No roster members available. Add an engine to            │
│   engines.d or configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL.│
│                                                             │
│ <button disabled>Start team</button> [disabled permanently]│
└─────────────────────────────────────────────────────────────┘
```

**Styling**: Error message (red, `#ff6b6b`) replaces the configure button. Start button is disabled with no option to enable. No picker shown.

### Tier-3-Only Roster (no tier-1 or tier-2)
If only tier-3 members are available and no saved composition:

```
[Idle, Picker Closed — same as any unconfigured composition]
[ Configure team... ]
```

**Corrected post-implementation** (this section originally described an
auto-expanded picker with an always-visible caveat; that didn't match what
was actually built, and the reviewer's second pass flagged the drift —
tracked here rather than silently rewritten). The actual behavior: `/status`
sends `composition: {"lead": null, "members": []}` for this roster shape —
identical in kind to any other project with no saved composition yet. The
picker stays collapsed behind "Configure team..." like normal; once opened,
the Lead dropdown lists only the tier-3 option(s) (there's nothing else to
list), and the tier-3 caveat appears the same way it does for any tier-3
selection elsewhere — conditionally, once that lead is actually chosen, not
pinned open by default. This is simpler than the original design and still
satisfies the spec's acceptance criterion: tier-3 is selectable as lead,
never blocked.

### Server Validation Error
If `POST /projects/<name>/team/start` with composition returns `400 {"error": "..."}`:

```
[Picker still expanded with last-selected values retained]
✕ Error: <server error message>
[Start button remains enabled]
```

**Styling**: Red error message (same as 6d), appears below the button, persists until the operator makes a change or next refresh. Specific error messages from `validate_composition()`:
- "Lead is required"
- "At least one teammate is required"
- "Teammate list contains duplicates"
- "Lead cannot also be a teammate"
- "Unknown lead: {name}" (if a saved composition references a removed engine)
- "Unknown teammate: {name}" (if a saved composition references a removed engine)
- "Teammate {name} is not delegate-capable" (Ollama, or non-headless engine)

## Accessibility & platform notes

- **Touch target sizes**: Select dropdown and checkboxes follow native browser defaults (36-40px minimum hit area on desktop). Checkbox labels are clickable (wrap label around input, or use `for` attribute).
- **Color contrast**:
  - Lead/teammate tier labels (`#4da6ff` on `#1c1c1c`): **6.67:1**, passes WCAG AA for normal text.
  - Tier-3 caveat header (`#ffb648` on `#1c1c1c`): **9.77:1**, passes WCAG AAA.
  - Grounding "found" icons (`#34c759` on `#1c1c1c`): **7.68:1**, passes WCAG AAA (graphical element, 3:1 minimum).
  - Grounding "not found" icons (`#ff6b6b` on `#1c1c1c`): **6.14:1**, passes WCAG AA (graphical element, 3:1 minimum).
  - Validation error text (`#ff6b6b` on `#1c1c1c`): **6.14:1**, passes WCAG AA.
- **Web vs. native**: This is a web app (HTML/CSS/JS in Flask template), desktop-only. No mobile optimization.
- **Keyboard interaction**:
  - Tab into lead select → arrow keys to change lead or Enter to open dropdown (browser default).
  - Tab into each teammate checkbox → Space to toggle.
  - Tab into "Start team" button → Enter to submit (disabled if validation error exists).
  - Escape key (optional, browser default) closes select dropdown.
- **Screen reader accessibility**: Select and checkboxes are native HTML, screen-reader-accessible by default. Labels are associated via `<label>` or `for` attribute. Tier and validation messages are plain text in `aria-live` regions for async updates (optional, but recommended for validation errors that appear after picker interaction).
- **Disabled state**: Disabled checkboxes (for non-delegate-capable members, the lead) are visually greyed out and not keyboard-focusable. Disabled Start button is greyed out when validation fails.

## Traceability to spec (6e acceptance criteria)

| Acceptance criterion | Where it's addressed in this design |
|---|---|
| Roster reflects `engines.d` live (re-read per request) | Roster fetched from `/status` response on picker open; no cache. Lead/teammate options show live engine/Ollama list with tiers. |
| Every member selectable as lead | Lead dropdown includes all roster members (tier 1/2/3). No blocking; tier 3 shows a plain-language reliability caveat, not a disable. |
| Tier shown for each roster member | Each lead/teammate option includes tier label: "tier 1 - native tools", "tier 2 - schema constrained", "tier 3 - prose parse". |
| Tier-3 member shows plain-language note | Tier-3 lead shows caveat: "This engine's reliability is lower due to prose-parsing..." Tier-3 teammate label includes "(prose parse, unreliable)". |
| Grounding files shown before start | New `GET /projects/<name>/team/grounding` returns list of which four files were found; picker displays them as a checklist with ✓/✗ icons. |
| Absent file is visible, not silent | A missing `ARCHITECTURE.md`, e.g., is marked with ✗ and "(not found)" label; visible to operator before start. |
| Saved composition persists across restarts | Composition submitted via POST body is saved to `TEAM_STATE_DIR/compositions.json` by backend; `/status` poll returns it in `inst.team.composition` field; picker pre-selects it on next poll. Verified by restarting service in test. |
| Empty teammate list rejected with clear reason | Client-side validation shows "At least one teammate is required" inline; server rejects with 400 {"error": "..."} if somehow empty list is submitted. |
| Duplicate teammate rejected | Client-side validation shows "Teammate list contains duplicates" if same teammate is checked twice (structurally impossible via checkboxes, but spec-required). Server rejects with specific message. |
| Lead also in members rejected | Client-side validation shows "Lead cannot also be a teammate" if lead's name is in selected teammates. Server rejects with specific message. |
| Unknown roster member rejected | If a saved composition references a removed engine, server rejects with "Unknown lead: {name}" or "Unknown teammate: {name}". Never silently substituted. |
| No usable roster member shows error, no picker | If `default_team_composition()` returns an error (tier-3 only, or empty roster), the picker area shows the error text inline; Start button is disabled; no picker controls shown. |
| Grounding route called per project | `GET /projects/<name>/team/grounding` called once on picker open, not on every poll. Result cached client-side until picker is closed/reopened. |
| No lead/member picker shown when team is running | When `team.status !== "idle"`, the entire picker/configure panel is hidden; only the running status/stop button is shown (unchanged from 6d). |

## Implementation notes for the developer

### Backend (Python / app/teams.py + app/app.py)

1. **New functions in app/teams.py**:
   - `validate_composition(lead: dict, members: list) -> str | None` — returns None if valid, else error message.
   - `_compositions_path() -> str` — path to `TEAM_STATE_DIR/compositions.json`.
   - `load_compositions() -> dict` — `{project_name: {"lead": {...}, "members": [...], "saved_at": iso}}`.
   - `save_composition(project_name: str, lead: dict, members: list) -> None` — atomic write via `.tmp` + `os.replace()`.

2. **Extended functions**:
   - `roster()` already exists (line ~1777); called by `GET /status` to return all roster members with tier/delegate_capable.
   - `load_grounding(workdir)` already exists; `GET /projects/<name>/team/grounding` calls it and returns `{"files": [...], "skipped": [...]}`.
   - `POST /projects/<name>/team/start` extended: reads optional `lead`/`members` from body, calls `validate_composition()`, saves if valid, uses them for `launch_team()` instead of `default_team_composition()`.

3. **GET /status** (extended):
   - Top-level: add `"roster": teams.roster()`.
   - Per-instance `inst["team"]`: add `"composition"` — saved composition if exists, else `default_team_composition()`'s result if ok, else None.

4. **GET /projects/<name>/team/grounding** (new):
   - 404 if `name` not a known project.
   - Calls `teams.load_grounding(workdir)`.
   - Returns `{"files": [{"label", "relpath", "byte_count"}, ...], "skipped": [...]}` — never `content`/`digest`.
   - No TOTP needed (read-only, like `/status`).

5. **POST /projects/<name>/team/start** (extended):
   - Body gains optional `lead` and `members`.
   - If both present: validate, save, use for `launch_team()`.
   - If neither present: unchanged from 6d (backward compatible).

### Frontend (JavaScript in app/app.py template)

1. **Picker toggle and expand/collapse**:
   - Add event listener to "Configure team..." button: `openTeamCompositionPicker(name)`.
   - Button text changes to "Hide configuration" when picker is open.
   - Click toggles `.team-picker` panel visibility (display: none/block).

2. **Lead dropdown (`<select id="team-lead-<name>">`)**:
   - Populate from `roster` array in `/status` response.
   - Format: `<option value='{"kind":"...","name":"..."}'>NAME (tier X - DESC)</option>`.
   - On change: call `updateTeammateCheckboxes(name)` (filter to exclude selected lead).
   - On change: call `showTier3Caveat(name)` (show/hide caveat based on selected lead's tier).
   - On change: call `validateTeamComposition(name)` (rerun validation).

3. **Tier-3 caveat (`<div class="team-tier-3-caveat">`)**:
   - Hidden by default (`display: none`).
   - Shown when selected lead's tier is 3.
   - Contains fixed text: "This engine's reliability is lower...".

4. **Teammate checkboxes (`<input type="checkbox" id="team-mate-...">`)**:
   - Populate from roster, filtered to delegate_capable entries and excluding current lead.
   - Each checkbox state tracked in memory or derived from checked property.
   - On change: call `validateTeamComposition(name)`.

5. **Grounding summary (`<div class="team-grounding">`)**:
   - Fetch `GET /projects/<name>/team/grounding` on picker open.
   - Display as list: `✓ FILENAME (size)` or `✗ FILENAME (not found)`.
   - If fetch fails, show "Grounding unavailable" (non-blocking).

6. **Client-side validation** (`validateTeamComposition(name)`):
   - Lead required: if lead select is empty, show error.
   - Teammates required: if no teammate checkbox is checked, show error.
   - No duplicates: structural impossibility via checkboxes, but check anyway.
   - Lead not in teammates: if lead name equals any checked teammate, show error.
   - Disable Start button if any error; enable if valid.

7. **doTeamStart() extended**:
   - Read lead select value and teammate checkbox states.
   - If picker is open (composition is configured), build `lead`/`members` JSON objects and include in POST body.
   - If picker is closed (using default), omit `lead`/`members` from body (backward compatible).
   - On 400 response, parse error message and show in `.team-msg` slot.

8. **Client state persistence** (from 6d):
   - `teamTaskText[name]` already holds textarea text across polls.
   - Add `teamPickerState[name]` to hold lead/members selection across polls (or re-derive from `.team-msg` on each refresh).

9. **Styling classes** (new):
   - `.team-picker` — wrapper for the entire picker panel.
   - `.team-picker-open` — variant when expanded (optional, for CSS state-based styling).
   - `.team-lead-picker` — lead select container.
   - `.team-tier-3-caveat` — tier-3 warning box.
   - `.team-mates-picker` — teammates checkboxes container.
   - `.team-grounding` — grounding files list.
   - `.team-validation-error` — inline error message (reuse from 6d).

10. **API shape for composition in JSON**:
    - Lead: `{"kind": "engine" | "ollama", "name": str}`
    - Members: `[{"kind": "engine", "name": str}, ...]`
    - Example: `{"lead": {"kind": "engine", "name": "claude"}, "members": [{"kind": "engine", "name": "codex"}]}`

11. **Backward compatibility**:
    - If `lead`/`members` are omitted from POST body, server uses `default_team_composition()` (unchanged from 6d).
    - Stale client that doesn't send `lead`/`members` still works.
    - New client with old server (missing validation) will get a 400 or generic error, which is acceptable.

### State diagram (client-side)

```
Idle, status === 'idle'
├─ Picker closed (default)
│  └─ Click "Configure team..." → Picker opens, fetches roster+grounding
│
└─ Picker open
   ├─ Select lead (optional, starts empty)
   ├─ Check teammates (optional, starts unchecked)
   ├─ View grounding (read-only)
   ├─ Validation error shown if present
   └─ Click "Start team" → POST with {task, lead, members}
      ├─ Success (200) → Composition saved, team started, status → running
      └─ Error (400) → Error message shown, picker remains open, can retry
```

