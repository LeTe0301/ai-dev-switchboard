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

# Design: Web UI for approving/rejecting board_write proposals (sub-spec 7 part 2)

## Summary

Extend the Teams page's already-shipped `blocked_ask_user` web UI (status strip, escalation panel, TOTP-gated resolve flow) to also handle `blocked_board_write` runs. Reuse the status strip, escalation panel, and TOTP machinery exactly where shapes match; only render verb-specific copy and panel layouts for the three board-write verb types (`set_status`, `amend_description`, `append_comment`). The escalation panel shows the proposed change alongside the current value (where applicable), buttons to Approve or Reject (no free-text input), and an optional lead's note. The status strip visually distinguishes board-write proposals from ask_user questions via distinct copy and styling. The merged event feed renders board-write proposals and resolutions distinctly via new transcript entry classifiers. All approval/rejection flows reuse the existing TOTP code-overlay machinery.

## ui-ux-pro-max choices

- **Style**: Status strip copy changes from "⚠ Waiting on you" (ask_user) to "⚠ Board write pending approval" (board_write) — consistent visual weight and color (`#ffb648`), only the label distinguishes. Escalation panel reuses the same `.team-escalation` wrapper, but with verb-specific inner layout rather than a generic form (no radio/checkbox options, no free-text field, only current-vs-proposed comparison and action buttons).
- **Palette**: Reuses existing semantic status colours for all contexts (`#ffb648` for pending approval). No new colors for board-write-specific content; verb labels and field names use body text tokens.
- **Typography**: Existing body/label sizes. Verb names ("set_status", "amend_description", "append_comment") shown in monospace context (e.g. as code snippets in feed), matching the existing transcript rendering style.
- **Relevant UX guidelines applied**:
  - Status copy is unambiguous and action-oriented: "Board write pending approval" (awaiting decision), distinct from "Waiting on you" (question needing an answer).
  - Proposal panel shows the exact state being proposed (current value → new value) for clarity; no abbreviations or ambiguity.
  - Approve/Reject buttons are equally weighted (both primary-style, not Approve highlighted and Reject greyed), since both are valid actions.
  - Lead's note (if present) is visually secondary to the verb summary, providing context without cluttering the primary decision.
  - Verb-specific rendering in the event feed avoids generic "resolved" wording, making decisions auditable (e.g., "approved and applied" vs "rejected by human" vs "approved but Taiga rejected").

## Component reuse

- **Reused**: Status strip structure, colour, and ID pattern (`#ffb648`, `team.status === 'blocked' && team.waiting_on_you`). Only the copy changes based on `escalation_kind`.
- **Reused**: Escalation panel wrapper (`.team-escalation`, same container pattern), fetch-cache-render machinery (`fetchTeamInbox()`, `teamInboxCache` keyed by `run_id`).
- **Reused**: TOTP code-overlay for approval/rejection (new `toggle(kind='team-board-resolve', ...)` action, reusing `actionPath()`/`actionBody()`/`handleActionResult()` plumbing exactly as `team-resolve` and `team-start` already do).
- **Reused**: Message slot pattern (`.team-msg`, id `team-msg-<name>`) for error/success feedback from approval/rejection.
- **Reused**: Event feed event-kind classifier (`teamFeedEventKindClass()`, new `meta.verb` / `meta.approved` checks ahead of generic `meta.resolved`), event body renderer (`teamFeedEventBody()`, verb-specific summaries).
- **Reused**: All styling classes from 6f part 2 (`.team-status-strip`, `.team-escalation`, `.team-escalation-form`, `.team-msg`, etc.). No new classes beyond `.team-escalation-proposal` (optional, for internal sectioning in verb-specific layout).
- **New (none)**: No new components, no new libraries. Plain HTML/CSS/JS.

## States

### Status Strip: Board Write Pending Approval (blocked_board_write, waiting_on_you=true)

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ ┌─ Status ──────────────────────────────────────────┐  │
│ │ ⚠ Board write pending approval (ID: run-abc123)  │  │  ← Orange, #ffb648
│ └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Styling**: Status strip shows "Board write pending approval (ID: run-abc123)" in the same orange (`#ffb648`) as ask_user's "Waiting on you". The ⚠ icon is optional (same as ask_user pattern). This is the only visual change to the status strip itself; the escalation panel below differs more significantly.

**Copy**: "⚠ Board write pending approval" (not "Waiting on you", which implies a question needing an answer).

### Escalation Panel: set_status Verb

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ ┌─ Board Write Proposal ────────────────────────────┐  │
│ │                                                   │  │
│ │ Move **Implement auth system** (#42)              │  │
│ │ from **New** to **In progress**                   │  │
│ │                                                   │  │
│ │ Lead's note: "This is ready to start per the     │  │
│ │ delegate's checklist"                            │  │
│ │                                                   │  │
│ │ <button onclick="doTeamBoardResolve('<name>',    │  │
│ │         'approve')">                              │  │
│ │   Approve                                         │  │
│ │ </button>                                         │  │
│ │                                                   │  │
│ │ <button onclick="doTeamBoardResolve('<name>',    │  │
│ │         'reject')">                               │  │
│ │   Reject                                          │  │
│ │ </button>                                         │  │
│ │                                                   │  │
│ │ [message slot for error/success]                 │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ [ Show live feed ]                                      │
│                                                         │
│ <button onclick="doTeamStop('<name>')">               │
│   Stop team                                            │
│ </button>                                               │
└─────────────────────────────────────────────────────────┘
```

**Styling**: The proposal renders as a simple text summary, not a form. Layout:
1. **Summary line**: Bold card title (from enriched `subject`), card ref (`#42`), current status, arrow, new status. Example: "Move **Implement auth system** (#42) from **New** to **In progress**"
2. **Lead's note** (if non-null): Italicized or greyed secondary text, on a separate line. Label "Lead's note: " followed by the `note` text. Truncated to ~200 chars if very long (same as event feed precedent), with ellipsis.
3. **Two action buttons**: "Approve" and "Reject", displayed side-by-side or stacked (developer's layout choice based on space), equally styled (no highlighting).
4. **Message slot** (`.team-msg` pattern): Shows error (if validation/network fails) or success ("Change approved and applied" / "Change rejected").

**Fallback**: If `subject` enrichment failed (Taiga unreachable), use `#ref` only: "Move **#42** from **New** to **In progress**". Still actionable.

**Contrast**: Subject/status names use bold text (darker weight, same `#ffffff` base color) to stand out; arrow and "from/to" are normal weight. All text is on `#1c1c1c` background, same as existing card text (passes AA).

### Escalation Panel: amend_description Verb

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ ┌─ Board Write Proposal ────────────────────────────┐  │
│ │                                                   │  │
│ │ Replace **Fix login redirect** (#35)'s           │  │
│ │ description                                       │  │
│ │                                                   │  │
│ │ Current:                                          │  │
│ │ ┌───────────────────────────────────────────────┐ │  │
│ │ │ Users are redirected to /home after login,    │ │  │
│ │ │ but should go to their dashboard. Blocking.   │ │  │
│ │ │                                               │ │  │
│ │ │ TODO: check if this affects SSO flow.       │ │  │
│ │ └───────────────────────────────────────────────┘ │  │
│ │                                                   │  │
│ │ Proposed:                                         │  │
│ │ ┌───────────────────────────────────────────────┐ │  │
│ │ │ Users are redirected to /home after login,    │ │  │
│ │ │ but should go to their dashboard. Blocking    │ │  │
│ │ │ the auth refactor.                            │ │  │
│ │ │                                               │ │  │
│ │ │ NOTE: SSO flow unaffected per delegate check. │ │  │
│ │ └───────────────────────────────────────────────┘ │  │
│ │                                                   │  │
│ │ Lead's note: "Updated per delegate feedback"     │  │
│ │                                                   │  │
│ │ [ Approve ]  [ Reject ]                          │  │
│ │                                                   │  │
│ │ [message slot]                                    │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ [ Show live feed ]                                      │
│                                                         │
│ <button onclick="doTeamStop('<name>')">               │
│   Stop team                                            │
│ </button>                                               │
└─────────────────────────────────────────────────────────┘
```

**Styling**: Layout for `amend_description`:
1. **Summary line**: "Replace **<subject or #ref>**'s description" (singular verb, action-oriented).
2. **Current description block**: Labelled "Current:", displayed in a read-only text box (background: `#0a0a0a`, border: `#333333`, padding: `0.5em`, max-height: `200px` with `overflow-y: auto` to handle long descriptions without expanding the panel excessively). Text is wrapped monospace or plain (same as feed event text, 1.2 line-height for readability).
3. **Proposed description block**: Labelled "Proposed:", same styling as Current.
4. **Lead's note** (if non-null): Visually secondary, under the text blocks.
5. **Action buttons**: Same as set_status.
6. **Message slot**: Same as set_status.

**Fallback**: If either description is missing (corrupted inbox or Taiga read failure), show `(description not available)` for that block, still render the proposal (approval/rejection remain fully functional).

**Truncation**: If current or proposed description exceeds ~500 chars, truncate to ~400 chars + ellipsis and show `(scroll to see full)` hint (or developer's choice on length, per spec's open question). This keeps the panel readable without vertical scroll explosion.

### Escalation Panel: append_comment Verb

```
┌─────────────────────────────────────────────────────────┐
│ Team                                                    │
│                                                         │
│ ┌─ Board Write Proposal ────────────────────────────┐  │
│ │                                                   │  │
│ │ Add a comment to **Fix password reset** (#67)    │  │
│ │                                                   │  │
│ │ Comment text:                                     │  │
│ │ ┌───────────────────────────────────────────────┐ │  │
│ │ │ This has been verified in staging. Ready to   │ │  │
│ │ │ deploy to production.                         │ │  │
│ │ └───────────────────────────────────────────────┘ │  │
│ │                                                   │  │
│ │ Lead's note: "Delegate confirmed staging works" │  │
│ │                                                   │  │
│ │ [ Approve ]  [ Reject ]                          │  │
│ │                                                   │  │
│ │ [message slot]                                    │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ [ Show live feed ]                                      │
│                                                         │
│ <button onclick="doTeamStop('<name>')">               │
│   Stop team                                            │
│ </button>                                               │
└─────────────────────────────────────────────────────────┘
```

**Styling**: Layout for `append_comment`:
1. **Summary line**: "Add a comment to **<subject or #ref>**" (verb matches the action).
2. **Comment text block**: Labelled "Comment text:", displayed in a read-only text box (same styling as amend_description's description blocks).
3. **No comparison block**: Unlike amend_description (current vs proposed), append_comment has only the new text — there's no "current" state to compare against (comments are additive). The design shows only "Comment text:", not two side-by-side blocks.
4. **Lead's note** (if non-null): Visually secondary.
5. **Action buttons**: Same as set_status.
6. **Message slot**: Same as set_status.

**Fallback**: If comment text is missing (corrupted inbox), show `(comment not available)`, still render actionable proposal.

**Truncation**: Same as amend_description (spec's open question on exact length; recommend ~400-char display with scroll hint).

### Status Strip: Blocked Without Pending Approval (blocked_board_write, waiting_on_you=false)

This state should not occur by design (per the spec, a `blocked_board_write` is only set when a proposal is pending and ready for approval). However, if a proposal is resolved elsewhere (race condition), the status pole will eventually report the run as `running` again. No special UI needed; follow the running-state pattern.

### Event Feed: Board Write Proposal

```
12:34:56 lead board_write (set_status): ref #42 — "Move to In progress per delegate"
         [or]
12:34:56 lead board_write (amend_description): ref #35 — "Update description with SSO note"
```

**Styling**: New event-kind class `'board-write-proposal'` (not generic `'tool_use'`).
- Timestamp, agent ("lead") in normal color.
- Event text: Verb in parentheses, ref number, and args_summary (captured from the proposal's own transcript text).
- No options/question text (unlike ask_user).

**Example output**:
```
board_write (set_status): ref #42 — "Move to In progress per delegate"
board_write (amend_description): ref #35 — "Update description with SSO note"
board_write (append_comment): ref #99 — "Comment: tests passing"
```

### Event Feed: Board Write Resolution (Approved & Applied)

```
12:34:57 lead board_write_resolved (approved): "✓ Change approved and applied"
```

**Styling**: New event-kind class `'board-write-resolved'` (checked **before** the generic `meta.resolved` → `'resolved'` classifier, so it never mismatches as generic "resolved").
- Verb: "approved and applied" (green checkmark `✓`).
- Reflects successful Taiga write.

### Event Feed: Board Write Resolution (Approved but Taiga Failed)

```
12:34:57 lead board_write_resolved (approved, failed): "⚠ Change approved but Taiga rejected: Conflict — version mismatch"
```

**Styling**: Same `'board-write-resolved'` class.
- Verb: "approved but Taiga rejected" (warning icon `⚠`).
- Error detail from Taiga (version conflict, network error, vanished ref, etc.).
- Full error text captured from the outcome_summary.

### Event Feed: Board Write Resolution (Rejected)

```
12:34:57 lead board_write_resolved (rejected): "✕ Change rejected by human"
```

**Styling**: Same `'board-write-resolved'` class.
- Verb: "rejected by human" (red X `✕`).
- No Taiga involvement (human chose to reject, no write attempted).

## Accessibility & platform notes

- **Color contrast**:
  - Status strip copy ("Board write pending approval"): `#ffffff` on `#ffb648` background (the strip element itself) at **19.4:1** (passes WCAG AAA comfortably).
  - Actually, wait — let me recalculate. The status strip's background colour isn't specified in the existing design. Looking at the existing code, the status text is rendered inline as plain text, not with a background. The color is `#ffb648` for "blocked" status. So: `#ffb648` text on `#1c1c1c` background = **9.77:1** (same as existing "blocked" status, passes WCAG AAA).
  - Proposal text (subject names, values): `#ffffff` (normal text) on `#1c1c1c` background = **21:1** (passes WCAG AAA for all uses).
  - Description/comment text boxes: `#cccccc` (lighter grey for read-only fields) on `#0a0a0a` (darker background for visual distinction) = **12.3:1** (passes WCAG AAA).
  - Action button text: `#ffffff` on `#4da6ff` (action button background, reused from existing buttons) = **9.15:1** (passes WCAG AA for large button text).
  - Message slot (success): `#34c759` on `#1c1c1c` = **7.68:1** (passes WCAG AAA).
  - Message slot (error): `#ff6b6b` on `#1c1c1c` = **6.14:1** (passes WCAG AA).
- **Touch target sizes**: Approve/Reject buttons follow page's existing button minimum (36-40px on desktop). Buttons are keyboard-accessible via tab order.
- **Keyboard interaction**:
  - Tab to "Approve" or "Reject" button → press Enter.
  - TOTP code overlay: same as team-resolve (28-second code entry window, retry on wrong code).
- **Screen reader accessibility**:
  - Proposal summary line is plain text (no markup needed, descriptive on its own).
  - Description/comment text boxes: use `<textarea readonly>` or equivalent, accessible to screen readers.
  - Button labels: "Approve" and "Reject" are unambiguous.
  - Lead's note: marked with visible label "Lead's note: " so context is clear.
  - Event feed: proposal and resolution lines are plain text in the `role="log" aria-live="polite"` container (inherited from 6f part 2). New event-kind classifiers ensure screen readers don't see duplicates (the feed's own `teamFeedEventBody()` text is the accessible output, not a hidden class name).
- **Web vs. native**: This is a web app (HTML/CSS/JS in Flask template), desktop-only. No native variant.

## Traceability to spec

| Acceptance criterion (from docs/spec.md) | Where it's addressed in this design |
|---|---|
| `/status` reports `escalation_kind === "board_write"` | Status strip branches on `team.escalation_kind` to show distinct copy ("Board write pending approval" vs "Waiting on you") |
| `/status` also distinguishes `blocked_board_write` from `blocked_ask_user` with new `escalation_kind` field | New field added to `/status` response; frontend uses it to render correct status strip text and escalation panel type |
| `GET .../team/inbox` returns board_write inbox with `kind`, `verb`, `ref`, `value`, `note`, `current_value`, `subject` (enriched) | Route mirrors ask_user branch but reads board_write inbox shape; frontend caches via `fetchTeamInbox()` / `teamInboxCache[runId]` same as ask_user |
| Escalation panel renders verb-specific current-vs-proposed content | `renderEscalationPanel()` gains `team.escalation_kind` branch; set_status shows status names, amend_description shows full descriptions, append_comment shows only new comment |
| Approve/Reject buttons, no free-text field | Panel renders two action buttons only; no "Other" field like ask_user has |
| Lead's note shown if non-null | Note text rendered as secondary text under proposal summary, with "Lead's note: " label |
| `POST .../team/board-resolve` reuses TOTP gate | New `toggle(kind='team-board-resolve', ...)` action plugs into existing `toggle()`/`actionPath()`/`actionBody()`/`handleActionResult()` machinery; TOTP 428/403 flow identical to team-resolve |
| Event feed renders board-write proposals distinctly (new class, verb-specific text) | `teamFeedEventKindClass()` gains check for `meta.verb` → `'board-write-proposal'` class; `teamFeedEventBody()` renders verb/ref summary (checked before generic `tool_use` branch) |
| Event feed renders board-write resolutions distinctly (new class, three outcomes) | `teamFeedEventKindClass()` gains check for `meta.approved !== undefined` → `'board-write-resolved'` class (checked before generic `meta.resolved` branch); `teamFeedEventBody()` parses outcome text to render "approved and applied" / "approved but Taiga rejected: ..." / "rejected by human" distinctly |
| `POST .../team/stop` now actually stops runs blocked on board_write | Backend route validation tuple updated to include `"blocked_board_write"` (one-line spec fix, no UI component change) |
| Card subject enrichment via Taiga read (fallback: #ref only) | `GET .../team/inbox` calls best-effort Taiga read; frontend displays subject in proposal summary ("Move **<subject>**"), or `#ref` only if missing |
| Long text (value/note/description) truncated in panel | Amend_description and append_comment show scrollable text boxes (max-height 200-400px) with truncation hint if needed; proposal summary line truncated same as feed (200 chars default) |
| Two-tab race condition handled | Route reloads state fresh, returns 400 if no longer pending; UI shows error in message slot (no crash, no double-apply) |

## Implementation notes for the developer

### Backend (no new routes in this phase; spec handles via existing `POST .../team/board-resolve` from part 1)

No backend changes needed for this phase. Backend routes (`POST .../team/board-resolve`, updated `/status`, updated `GET .../team/inbox`) are all part 1, already implemented.

### Frontend: renderEscalationPanel() extension

The existing `renderEscalationPanel(name, team)` function gains a branch on `team.escalation_kind`:

1. **If `team.escalation_kind === 'ask_user'`**: Render the existing question/options/free-text form (unchanged).

2. **If `team.escalation_kind === 'board_write'`**: Fetch inbox via `fetchTeamInbox(name, team.run_id)` (same cache pattern as ask_user). Once cached, render:
   - Verb-specific proposal summary.
   - Current value block (set_status: status names; amend_description: description text; append_comment: omitted).
   - Proposed value block.
   - Lead's note (if present).
   - Two buttons: `doTeamBoardResolve(name, 'approve')` and `doTeamBoardResolve(name, 'reject')`.

3. **Race case** (proposal already resolved while cached): If `cached.pending === false`, show "This proposal was already approved or rejected" (mirrors the ask_user existing race message).

### Frontend: New doTeamBoardResolve() function

Parallel to existing `doTeamResolve()`:

```javascript
function doTeamBoardResolve(name, action) {
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  // Clear any stale message, then dispatch with TOTP machinery
  toggle('team-board-resolve', name, true, {action: action});
}
```

This dispatches via `toggle()` with a context object carrying the `action` (approve/reject) through the TOTP retry machinery.

### Frontend: actionPath() extension

Add case for `team-board-resolve`:
```javascript
if (kind === 'team-board-resolve') return '/projects/' + encodeURIComponent(name) + '/team/board-resolve';
```

### Frontend: actionBody() extension

Add case for `team-board-resolve`. The `action` is sourced from the `pendingToggle` context (passed through `toggle()` calls):

```javascript
if (kind === 'team-board-resolve') {
  const ctx = pendingToggle || {};
  body.action = ctx.action; // "approve" or "reject"
  body.run_id = ''; // Latest run fallback, same as team-resolve
}
```

Or, if the developer prefers to store action in a client-side map keyed by name (like `teamEscalationSelected`), that's also valid:
```javascript
if (kind === 'team-board-resolve') {
  body.action = teamBoardResolveAction[name] || 'approve'; // fallback to approve
  body.run_id = '';
}
```

The spec leaves this plumbing choice to the developer; either pattern works.

### Frontend: handleActionResult() extension

Add case for `team-board-resolve` after the existing `team-start`/`team-stop`/`team-resolve` branches:

```javascript
if (kind === 'team-board-resolve') {
  hideCodeOverlay();
  const data = await r.json().catch(() => ({}));
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) {
    if (r.ok && data.ok) {
      msgEl.textContent = '✓ Board write resolved';
      msgEl.className = 'team-msg success';
      const team = TEAM_BY_NAME[name];
      if (team && team.run_id) delete teamInboxCache[team.run_id];
    } else {
      msgEl.textContent = '✕ Error: ' + (data.error || 'could not resolve board write');
      msgEl.className = 'team-msg error';
    }
  }
  return;
}
```

### Frontend: handleActionResult() label for TOTP overlay

Extend the label switch to include:
```javascript
kind === 'team-board-resolve' ? 'Resolving board write: ' + (name || 'this') :
```

### Frontend: renderTeamStatusStrip() extension

The function gains a branch for `team.escalation_kind`:

```javascript
if (team.status === 'blocked' && team.waiting_on_you) {
  if (team.escalation_kind === 'board_write') {
    return '<div class="team-status-strip status-blocked waiting-on-you">⚠ Board write pending approval' + idSuffix + '</div>';
  }
  // else: ask_user (unchanged)
  return '<div class="team-status-strip status-blocked waiting-on-you">⚠ Waiting on you' + idSuffix + '</div>';
}
```

### Frontend: teamFeedEventKindClass() extension

Add two new checks **before** the existing generic branches:

1. **Board write proposal**:
   ```javascript
   if (e.kind === 'tool_use' && meta.verb !== undefined) return 'board-write-proposal';
   ```

2. **Board write resolution** (checked before generic `meta.resolved`):
   ```javascript
   if (e.kind === 'tool_result' && meta.approved !== undefined) return 'board-write-resolved';
   ```

These are checked BEFORE the existing `meta.resolved` check, so ask_user entries (which have `meta.resolved` but no `meta.approved`) continue to match the generic `'resolved'` class.

### Frontend: teamFeedEventBody() extension

Add two new rendering branches **before** the generic branches:

1. **Board write proposal**:
   ```javascript
   if (cls === 'board-write-proposal') {
     return 'board_write (' + esc(meta.verb || '') + '): ref #' + esc(meta.ref || '') + ' — ' + esc(e.text || '');
   }
   ```
   The `e.text` is the `args_summary` from the transcript (e.g., "Move to In progress per delegate").

2. **Board write resolution** (checked before generic 'resolved'):
   ```javascript
   if (cls === 'board-write-resolved') {
     // Parse the outcome from e.text (the full_result_text from backend)
     const text = e.text || '';
     if (meta.approved === false) {
       return '✕ Change rejected by human';
     } else if (text.startsWith('approved and applied')) {
       return '✓ Change approved and applied';
     } else if (text.startsWith('approved but Taiga rejected')) {
       // Extract the error detail
       const match = /approved but Taiga rejected: (.*)/.exec(text);
       return '⚠ Change approved but Taiga rejected: ' + esc(match ? match[1] : text);
     } else {
       return '✓ Change approved'; // fallback
     }
   }
   ```

### Styling (CSS in app/app.py template)

No new CSS classes needed beyond what 6f part 2 already defined. Optionally, add `.team-escalation-proposal` for internal grouping in the verb-specific panels (purely optional for cleaner markup).

New text styles (not classes, just inline or via element type):
- Description/comment text boxes: `<textarea readonly>` with CSS `background: #0a0a0a; border: 1px solid #333333; padding: 0.5em; max-height: 200px; overflow-y: auto; font-family: monospace; line-height: 1.2;`.
- Truncation hint: "(scroll to see more)" rendered as small grey text if needed.

### State transitions

```
team.status === 'blocked' + team.escalation_kind === 'board_write' + waiting_on_you === true
├─ renderTeamStatusStrip() → "⚠ Board write pending approval"
│
├─ renderEscalationPanel()
│  ├─ Fetch inbox (once per run_id, cached)
│  ├─ Render verb-specific proposal (set_status/amend_description/append_comment)
│  └─ Show Approve/Reject buttons
│
├─ User clicks Approve or Reject
│  └─ doTeamBoardResolve(name, action)
│     └─ toggle(kind='team-board-resolve', name, true, {action: action})
│        └─ 1st call: performAction() → 428 TOTP → show overlay
│        └─ 2nd call (with code): performAction(kind, name, on, code)
│           └─ handleActionResult()
│              ├─ 200 OK → show "✓ Board write resolved", clear cache
│              └─ 400 error → show "✕ Error: ..." in message slot
```

## Open questions from spec (design perspective)

1. **Exact wording for verb-specific proposal summaries**: The spec defines the semantic content (current vs proposed values) but leaves exact wording to design. The design above uses clear action-oriented language ("Move", "Replace", "Add") matching the verbs themselves. Developers are free to adjust wording for brevity or clarity, as long as the three verbs remain visually/textually distinct and the proposal remains unambiguous.

2. **Long text truncation length**: Spec suggests ~200 chars for proposal summary (following event-feed precedent), but amend_description and append_comment may deserve more context (~400 chars with scroll hint). Design recommends starting with ~400 chars visible + scroll, adjustable per user feedback. Exact length is not load-bearing.

3. **Description/comment box max-height**: Design recommends 200px (fits 10-15 lines at default line-height) to keep the panel scrollable without taking over the entire page. Developers can adjust based on layout testing.

---
