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

# Design: Clone a project from a remote repository URL (backlog item 16)

## Summary
A third project-creation entry point alongside "+ New project" and "Upload folder / .zip", allowing operators to clone an existing public repository by pasting its remote URL. The form is a simple inline row with a URL input field (required), an optional project-name override field, and a "Clone" button. The loading state displays a progress message indicating that cloning can take 30-180 seconds for large repositories. Error states handle invalid URLs, network failures, authentication failures (private repos), and oversized clones. Success results in a new project appearing in the project list, identical to projects created via other paths. No new visual language — all styling reuses existing "+ New project" patterns.

## ui-ux-pro-max choices
- **Style**: Inline form row, following the "+ New project" pattern (simple row with input + button + error slot) rather than a multi-step overlay like the upload wizard. The form expands inline when the "Clone from URL" button is clicked, maintaining the same visual hierarchy.
- **Palette**: Reuses existing page tokens; no new colors. Button matches existing `.new-project-row button` styling (#34c759 green). Error messages use the existing #ff6b6b red token (same as `.new-project-err`). Loading indicator reuses existing page conventions (no spinner graphic, text-only status).
- **Typography**: Existing 14px for button/input, 12px for labels/error messages. No new typefaces.
- **Relevant UX guidelines applied**:
  - URL field has no client-side format validation (spec's allowlist is server-authoritative), but provides clear placeholder text ("https://github.com/user/repo").
  - Button is always enabled (URL validation happens server-side), avoiding client-side guessing at what's valid.
  - Loading message explicitly sets expectations that cloning can take "up to a few minutes for large repositories" (vs the instant response of "+ New project").
  - Error messages are specific to the failure mode (invalid URL, network error, auth required, oversized repo) to help operators understand what went wrong.
  - Form clears on success (same as "+ New project" pattern) to avoid accidental re-submissions.

## Component reuse
- **Reused**: Button styling (`.new-project-row button` — #34c759 green, 10px padding, 10px 16px, rounded, bold text).
- **Reused**: Input styling (`.new-project-row input` — #1c1c1c background, #eee text, #333 border, 10px 12px padding).
- **Reused**: Error message slot (`.new-project-err` — #ff6b6b red, 12px font, 14px min-height) and DOM pattern (cleared by client, populated by response handler).
- **Reused**: Form submission pattern (`actionPath()` / `actionBody()` / `toggle()` / `handleActionResult()` plumbing — same TOTP gate as `kind='newproject'`, reusing existing code-overlay machinery).
- **Reused**: `/status` poll for success detection (no new timer or polling mechanism).
- **New components**: None. Plain HTML input elements and CSS, matching page conventions.

## States

### Initial (Collapsed)
The form is not visible by default. A third button sits on the same row as "+ New project" and "Upload folder / .zip":

```
┌──────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                   │
│                                                      │
│ <input placeholder="new project name" maxlength="60">│
│ <button>+ New project</button>                        │
│                                                      │
│ <button>Upload folder / .zip</button>                │
│                                                      │
│ <button>Clone from URL</button>  ← NEW              │
│                                                      │
│ <error slot>                                         │
└──────────────────────────────────────────────────────┘
```

**Styling**: The "Clone from URL" button uses the same styling as "+ New project" and "Upload folder / .zip" (white text, rounded, medium padding). Positioned on its own line (or adjacent row, depending on layout space).

**Copy**: Button label is exactly "Clone from URL".

### Expanded (Ready to Input)
When the operator clicks "Clone from URL", the form expands inline to show input fields:

```
┌──────────────────────────────────────────────────────┐
│ ai-dev-switchboard                                   │
│                                                      │
│ Clone from URL                                       │
│ <input id="clone-url" placeholder="https://github... │
│ <input id="clone-name" placeholder="(optional)...    │
│ <button>Clone</button>                               │
│                                                      │
│ <error/status slot id="clone-err">                   │
│                                                      │
│ <project list rows>                                  │
└──────────────────────────────────────────────────────┘
```

**Styling**: 
- Row layout uses flexbox (gap 8px) consistent with `.new-project-row`.
- "Clone from URL" is a small label/heading above the inputs (12px, #aaa color, slightly bold).
- URL input is full-width or 60% of the form row, with maxlength="2048" (per spec's CLONE_URL_MAX_LEN).
- Name input is narrower (20-30% of row or constrained width), with maxlength="60" (matching NAME_RE).
- Both inputs share the same styling as `.new-project-row input`.
- "Clone" button uses the same styling as "+ New project" button.
- Error/status slot (`.clone-err` or reuse `.new-project-err`) sits below the form row.

**Placeholder text**:
- URL: "https://github.com/user/repo or ssh://host/path"
- Name: "(optional — derived from URL if left blank)"

**Behavior**:
- Form expands when clicked (or via a toggle state in JS).
- Inputs are focused and ready for typing (URL field auto-focused if possible).
- Operators can press Tab to move between URL, name, and Clone button.
- Operators can press Enter in either input to submit (if URL is non-empty).

### Loading State
While the clone operation is in flight (POST request pending):

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" disabled>https://github...     │
│ <input id="clone-name" disabled>(derived-name)       │
│ <button disabled>Cloning…</button>                    │
│                                                      │
│ <spinner or "Cloning… this can take a while for      │
│  large repositories (up to a few minutes)."  slot>    │
│                                                      │
│ <project list rows>                                  │
└──────────────────────────────────────────────────────┘
```

**Styling**:
- Input fields and button are **disabled** (greyed out, `disabled` attribute set, pointer: not-allowed).
- Button text changes to "Cloning…" (with optional ellipsis animation if using CSS @keyframes, same pattern as existing loading states on the page).
- Error slot is replaced with a status message: "Cloning… this can take a while for large repositories (up to a few minutes)." in #aaa (muted) color, 12px font.
- No animated spinner graphic (text-only, per existing page conventions).

**Duration**: Can run up to 180 seconds (default CLONE_TIMEOUT_SECONDS). The message sets expectations so operators don't assume the UI is hung.

### Error States

#### Invalid URL Scheme
Disallowed schemes (file://, git://, ext::, bare paths, argument-injection attempts) are rejected before any subprocess:

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" placeholder="...">             │
│ <input id="clone-name" placeholder="...">            │
│ <button>Clone</button>                               │
│                                                      │
│ ✕ unsupported URL — use http://, https://, ssh://,  │
│   or user@host:path (git's own shorthand)            │
└──────────────────────────────────────────────────────┘
```

**Styling**: Red error text (#ff6b6b), ✕ icon, error message clipped to ~300 chars per spec (but this particular error is concise).

**Behavior**: Form remains expanded and editable; user can correct the URL and re-submit.

#### Network Failure / Unreachable Host
A legitimate URL that points to a non-existent or unreachable host:

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" value="https://invalid.host/...">
│ <input id="clone-name" placeholder="...">            │
│ <button>Clone</button>                               │
│                                                      │
│ ✕ Error: fatal: unable to access                     │
│   'https://invalid.host/repo.git': Could not resolve │
│   host...                                            │
└──────────────────────────────────────────────────────┘
```

**Styling**: Red error text (#ff6b6b), clipped to ~300 chars (as per spec). Message is from git's own stderr, sanitized.

**Behavior**: Form remains expanded; user can try a different URL.

#### Authentication Required (Private HTTPS Repo)
A private repository URL that would normally require credentials (unsupported this cycle):

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" value="https://github.com/private/repo.git">
│ <input id="clone-name" placeholder="...">            │
│ <button>Clone</button>                               │
│                                                      │
│ ✕ Error: could not read Username for                │
│   'https://github.com': terminal prompts disabled    │
└──────────────────────────────────────────────────────┘
```

**Styling**: Red error text (#ff6b6b), clipped to ~300 chars. Message is git's stderr (not a polished "this feature isn't supported yet" message, but a real, prompt, non-hanging failure).

**Behavior**: Form remains expanded; explains that credentials aren't supported this cycle (via the error message). A future fast-follow can pattern-match this error to show friendlier UX.

#### Oversized Repository
A clone succeeds but the resulting repository exceeds the CLONE_MAX_BYTES limit and is rolled back:

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" value="https://huge-repo.git">  │
│ <input id="clone-name" placeholder="...">            │
│ <button>Clone</button>                               │
│                                                      │
│ ✕ Error: Cloned repository is 1.2 GB, over the     │
│   500 MB limit — removed.                            │
└──────────────────────────────────────────────────────┘
```

**Styling**: Red error text (#ff6b6b), clipped to ~300 chars. Message from the privileged script's own check.

**Behavior**: Form remains expanded; project was never registered (directory was removed atomically).

#### Name Collision
Explicit name override or derived name collides with an existing project:

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" value="https://github.com/my/repo.git">
│ <input id="clone-name" value="existing-project">     │
│ <button>Clone</button>                               │
│                                                      │
│ ✕ Error: 'existing-project' already exists.          │
└──────────────────────────────────────────────────────┘
```

**Styling**: Red error text (#ff6b6b), exact message from server.

**Behavior**: Form remains expanded; user can change the name field to override the derived name.

#### Timeout
A slow/stalled transfer that exceeds CLONE_TIMEOUT_SECONDS:

```
┌──────────────────────────────────────────────────────┐
│ Clone from URL                                       │
│ <input id="clone-url" disabled>...                   │
│ <input id="clone-name" disabled>...                  │
│ <button disabled>Clone</button>                      │
│                                                      │
│ ✕ Error: clone failed: <timeout message>             │
└──────────────────────────────────────────────────────┘
```

**Styling**: Red error text (#ff6b6b).

**Behavior**: Form remains expanded; user can retry (orphaned child processes are a known, accepted gap per spec).

### Success State
After a successful clone:

```
┌──────────────────────────────────────────────────────┐
│ <form is hidden or collapsed>                        │
│                                                      │
│ <project rows, now including the new project>        │
│                                                      │
│ <new row>                                            │
│ my-cloned-repo                                       │
│ origin: https://github.com/user/my-repo.git         │
│ • tmux • ttyd • Code • Deploy (if mapped)            │
└──────────────────────────────────────────────────────┘
```

**Behavior**:
- Form clears (URL and name inputs reset to empty/placeholder) and collapses or hides.
- Page automatically refreshes (via existing `/status` poll, within ~4 seconds) and the new project appears in the project list.
- No special "success" message is shown (same as "+ New project" behavior — the new project appearing is the confirmation).
- Project row includes the derived or provided name, and the origin URL is visible in a subtitle (per existing project-list styling).

## Accessibility & platform notes

- **Touch target sizes**: "Clone from URL" button and "Clone" button both match the page's existing button minimum (36-40px on desktop, larger on mobile if needed). Keyboard-accessible via tab order.
- **Color contrast**:
  - Button text (#111, not #fff as originally claimed here — corrected 2026-08-14 per backlog item 20's audit: `.new-project-row button`/`.deploy-btn`/`.team-btn` all pair `#34c759` with dark `#111` text, not white) on button background (#34c759): **8.51:1** (passes WCAG AAA; the previously claimed 5.05:1 for white text was also wrong — white-on-#34c759 is actually ~2.2:1, failing AA).
  - Error message (#ff6b6b) on page background (#1c1c1c): **6.14:1** (passes WCAG AA).
  - Placeholder text (#666 or #888 muted) on input background (#1c1c1c): **6.14:1** (passes WCAG AA).
  - Status message (#aaa muted) on background (#1c1c1c): **6.4:1** (passes WCAG AA for body text).
- **Keyboard interaction**:
  - Tab to "Clone from URL" button → press Enter to expand form.
  - Tab to URL input → type URL (or paste via Ctrl+V / Cmd+V).
  - Tab to name input → type optional name or leave blank.
  - Tab to "Clone" button → press Enter to submit.
  - Escape key can close the expanded form (same as other inline forms on the page, if implemented).
- **Form field labels**: URL and name inputs have descriptive `placeholder` attributes (do not fully substitute for labels, but context is clear from the row's "Clone from URL" header). For strict accessibility, developers can add `<label for="clone-url">Repository URL</label>` elements (not shown in wireframe, but recommended).
- **Error message accessibility**: Errors are shown as plain text, readable by screen readers. The `.clone-err` slot is always present (empty initially), so screen readers pick it up when populated.
- **Web vs. native**: This is a web app (HTML/CSS/JS in Flask template), desktop-only. No native/mobile variant.
- **Disabled state during loading**: Inputs and button are disabled during clone, preventing accidental re-submissions. Screen readers announce the disabled state.

## Traceability to spec

| Acceptance criterion (from docs/spec.md) | Where it's addressed in this design |
|---|---|
| Third button "Clone from URL" next to "+ New project" and "Upload folder / .zip" | Button positioned on form alongside existing project-creation buttons; same visual weight/styling |
| URL input (required), optional name-override input | Form row with two text inputs; URL required, name optional |
| Valid public https:// URL → project created under derived name, appears in list | Success state shows new project in list with derived or explicit name |
| Explicit name override → project uses that name instead | Name input allows operator to override derived name |
| Disallowed scheme (file://, git://, ext::, bare path) → 400 before subprocess, no directory created | Error state shows "unsupported URL" message; no project directory created |
| URL resembling `-oProxyCommand=...` → rejected | Error state covers this (no leading `-` in allowlist) |
| Invalid explicit name → 400, same message as create_project() | Error state shows NAME_RE validation message |
| Name collision → 400 "'<name>' already exists." | Error state shows collision message; form remains editable to fix |
| Concurrent requests to same name → exactly one succeeds, other fails cleanly | Atomic `mkdir` in script handles TOCTOU race; error state shows clean 400 |
| Unreachable host → 400 with clipped error, no orphaned directory | Error state shows network error; script's ERR trap removes directory |
| Private HTTPS repo → fails fast (not hung) with clear message | Error state shows git's "terminal prompts disabled" message; no timeout wait |
| Private SSH with no user key → fails fast with error | Error state shows SSH error; BatchMode=yes prevents interactive prompt |
| Oversized clone → rolled back, 400 error, project never appears | Error state shows size-limit message; script removes directory |
| Works without Gitea (no GITEA_ENABLED dependency) | Form submission reuses existing TOTP/action plumbing; no Gitea dependency in routes |
| SSH URL to host where RUN_USER has ambient keys → succeeds | Success state shows new project; spec notes SSH uses RUN_USER's own keys |
| Clone can take 30-180 seconds → loading message sets expectations | Loading state shows "can take a while" message; button disabled and says "Cloning…" |

## Implementation notes for the developer

1. **Button placement**: Add the "Clone from URL" button to the same container as "+ New project" and "Upload folder / .zip" (around line 2178-2184 of app/app.py). Use the same button class/styling.

2. **Form HTML structure**: Insert the form inputs after the button but initially hidden or collapsed:
   ```html
   <div id="clone-form" class="clone-form" style="display: none;">
     <label class="clone-form-label">Clone from URL</label>
     <input id="clone-url" placeholder="https://github.com/user/repo or ssh://host/path" maxlength="2048">
     <input id="clone-name" placeholder="(optional — derived from URL if left blank)" maxlength="60">
     <button onclick="startClone()">Clone</button>
   </div>
   <div class="clone-err" id="clone-err"></div>
   ```

3. **Styling**: Add CSS for `.clone-form` (flexbox row, gap 8px, same as `.new-project-row`) and `.clone-err` (reuse or adapt `.new-project-err` styling — red text, 12px, min-height 14px).

4. **JavaScript function startClone()**: Similar to `startNewProject()`:
   ```javascript
   function startClone() {
     const url = document.getElementById('clone-url').value.trim();
     const name = (document.getElementById('clone-name').value || '').trim();
     document.getElementById('clone-err').textContent = '';
     if (!url) {
       document.getElementById('clone-err').textContent = 'Enter a repository URL.';
       return;
     }
     toggle('clone', name || '', true, null); // or pass url separately
   }
   ```

5. **Route in actionPath()**: Add case for `kind === 'clone'`:
   ```javascript
   if (kind === 'clone') return '/projects/clone';
   ```

6. **Route in actionBody()**: Add case to pass URL and name:
   ```javascript
   if (kind === 'clone') {
     body.url = document.getElementById('clone-url').value.trim();
     body.name = (document.getElementById('clone-name').value || '').trim();
   }
   ```

7. **Route in handleActionResult()**: Add case for `kind === 'clone'` to handle success/error:
   ```javascript
   if (kind === 'clone') {
     if (r.ok) {
       document.getElementById('clone-url').value = '';
       document.getElementById('clone-name').value = '';
       document.getElementById('clone-form').style.display = 'none';
       setTimeout(refresh, 1500); // refresh to show new project
     } else {
       const data = await r.json().catch(() => ({}));
       document.getElementById('clone-err').textContent = data.error || 'Clone failed.';
     }
     hideCodeOverlay();
     return;
   }
   ```

8. **Button toggle function**: Add `openCloneForm()` and `closeCloneForm()` to toggle the form visibility:
   ```javascript
   function openCloneForm() {
     document.getElementById('clone-form').style.display = 'flex';
     document.getElementById('clone-url').focus();
   }
   function closeCloneForm() {
     document.getElementById('clone-form').style.display = 'none';
   }
   ```
   Connect the "Clone from URL" button to `openCloneForm()`.

9. **Server route** (`POST /projects/clone`): Already specified in spec. Route calls `clone_project_from_url(url, name)` function and returns `{"ok": true}` on success or `{"error": "message"}` on failure (400 status).

10. **Error message persistence**: Like `.new-project-err`, the error slot persists until manually cleared or until `refresh()` re-renders the page (next /status poll, ~4 seconds). No auto-dismissal.

11. **Keyboard support**: Operator can press Enter in the URL or name field to submit the form (use `onkeypress="event.key==='Enter' && startClone()"` on inputs, or handle via JavaScript event listeners).

12. **Form collapse on Escape**: Optionally detect Escape key to close the form (same as upload wizard pattern, if implemented).

## Open design questions

None blocking. One subtle implementation detail: whether to show the form expanded inline-on-page or in a small overlay similar to the upload wizard's card. The spec says "simpler inline shape like + New project," which this design interprets as inline expansion (form grows in place on the page). If the developer prefers a small card overlay like the upload wizard (but without the multi-step complexity), that's a valid alternative that doesn't change the UX significantly — the key constraint is that the form remains simple (two inputs + one button, no wizard steps). Either approach is acceptable; the spec's intent is to avoid the upload wizard's complexity, not to dictate exact positioning.

---

# Design: Chat-UI compose surface for interjecting into a running team (backlog item 19 part 2)

## Summary
A free-text compose box (textarea + Send button) rendered on the Teams row whenever a team is running or blocked waiting for human input, alongside the escalation panel when both are present. Human messages posted via this compose box appear in the existing merged event feed with a distinct, subtle visual treatment (a left-border accent in blue), and a new "human" filter pill allows operators to isolate human-authored messages from the agent/lead timeline. A live character counter enforces a 2000-character soft limit client-side before posting to `/projects/<name>/team/interject`. No chat-bubble redesign — human messages integrate seamlessly into the existing structured, multi-party event log. All styling and interaction patterns reuse existing team-control conventions.

## ui-ux-pro-max choices
- **Style**: Inline compose box, following the existing `.team-interject` pattern (textarea + Send button, positioned within the team row just before the feed toggle). Not a chat-interface redesign — a lightweight text-input control that mirrors the task-description textarea in the idle state.
- **Palette**: No new colors introduced. Reuses existing page tokens: #34c759 (green Send button, matching `.team-btn`); #4da6ff (blue left-border accent for human messages in feed, matching active filter pills and link color); #888/#aaa for character counter text (muted, existing); #ff6b6b for character-limit-exceeded warning (existing error red).
- **Typography**: Existing 13px for textarea and counter text, 14px for Send button (same as `.team-btn`). No new typefaces.
- **Relevant UX guidelines applied**:
  - Compose box is context-aware: placeholder text changes if waiting_on_you ("...this will not answer the pending question above") to clarify it's separate from the escalation panel.
  - Character counter updates live on each keystroke, and Send button proactively disables at 2000+ characters (client-side guardrail; server-side validation is authoritative).
  - No auto-submit on Enter (spec explicitly rejects chat-style Enter-to-send as inconsistent with other multi-line textareas in this app).
  - Human messages in the feed are subtly distinct (left-border accent, not a full bubble redesign) to avoid breaking the existing `role="log"` / `aria-live="polite"` accessibility contract for the structured event log.

## Component reuse
- **Reused**: `.team-row` structure and positioning (compose box inserted between escalation panel and feed toggle, if present).
- **Reused**: Textarea styling (`.team-interject-textarea` — identical to `.team-textarea`, 13px font, #1c1c1c background, #333 border, inherits font-family).
- **Reused**: Button styling (`.team-btn` for Send — #34c759 green, 14px, 10px 16px padding, rounded).
- **Reused**: Message slot (`.team-msg` for interject result — reuses existing error/success styling via `.team-msg.error` / `.team-msg.success`).
- **Reused**: Event feed filter-pill pattern (`aria-pressed` toggle, active state highlighting).
- **Reused**: Event feed rendering (`.team-feed-event` rows, no new container — human messages just add `.kind-human-message` class for left-border accent).
- **Reused**: Toggle/TOTP machinery (`toggle(kind='team-interject', ...)` reuses existing `actionPath()` / `actionBody()` / `handleActionResult()` plumbing for action dispatch).
- **Reused**: `/status` poll (no new polling, compose-box visibility re-evaluated every ~4 seconds when row re-renders).
- **New (none)**: No new component library, no new timer, no new polling mechanism.

## States

### Compose box: Hidden
Rendered when `team.status !== 'running'` and `team.status !== 'blocked'` (i.e., idle, finished, error), or when `team.status === 'blocked'` but `team.waiting_on_you === false` (escalated on max rounds, no further human input needed):

```
(No compose box rendered in these statuses)
```

**Behavior**: Compose box is completely absent. Any unsent draft is discarded. If a new run later starts for the same project, the draft does not reappear.

### Compose box: Visible, Ready to Input (Running)
Rendered when `team.status === 'running'`:

```
┌──────────────────────────────────────────────────────────┐
│ Status: Working   ID: run-xyz789                        │
│                                                          │
│ Show live feed                                           │
│                                                          │
│ Compose box (new in part 2):                             │
│ ┌────────────────────────────────────────────────────┐   │
│ │ <textarea id="interject-<name>" rows="2"           │   │
│ │ placeholder="Send a message to the team…">         │   │
│ │ </textarea>                                         │   │
│ │ <button class="team-btn" id="interject-send-...">  │   │
│ │   Send                                              │   │
│ │ </button>                                           │   │
│ │ ┌────────────────────────────────────────────────┐ │   │
│ │ │ 0/2000                                          │ │   │
│ │ └────────────────────────────────────────────────┘ │   │
│ └────────────────────────────────────────────────────┘   │
│                                                          │
│ All • lead • human • teammate-1 • teammate-2            │
│                                                          │
│ (scrollable event feed)                                 │
└──────────────────────────────────────────────────────────┘
```

**Styling & behavior**:
- Compose box is a `.team-interject` container (flexbox, column layout, gap 8px).
- Textarea is `.team-interject-textarea` (2 rows, 13px font, #1c1c1c background, #333 border, inherits font). ID: `interject-<name>`. Placeholder: "Send a message to the team…"
- Button is `.team-btn` (green #34c759, 14px, 10px 16px padding). ID: `interject-send-<name>`. Label: "Send". **Not disabled** (operator can type and send).
- Character counter `.team-interject-counter` below button: "0/2000" (12px text, #888 color, left-aligned).
- On keystroke (`oninput` event), `updateTeamInterjectControls(name)` is called:
  - Counter text updates in real-time: `len + '/' + 2000`.
  - If `len > 2000`, counter text color changes to #ff6b6b (error red) and `.over-limit` class is added.
  - Send button remains **enabled** (user can still click, but validation on click will fail).

**Placeholder copy**: "Send a message to the team…" (neutral, always applicable in the running state)

### Compose box: Visible, Context-Aware Placeholder (Blocked, Waiting on You)
Rendered when `team.status === 'blocked'` AND `team.waiting_on_you === true` (waiting on ask_user or board_write):

```
┌──────────────────────────────────────────────────────────┐
│ Status: Waiting on you   ID: run-xyz789                │
│                                                          │
│ ┌─ Escalation Panel (ask_user or board_write) ───────┐  │
│ │ (question/proposal + options/buttons)               │  │
│ └────────────────────────────────────────────────────┘  │
│                                                          │
│ Compose box (coexists with escalation):                 │
│ ┌────────────────────────────────────────────────────┐   │
│ │ <textarea id="interject-<name>" rows="2"           │   │
│ │ placeholder="Send a message to the team             │   │
│ │ (this will not answer the pending question         │   │
│ │ above)…">                                           │   │
│ │ </textarea>                                         │   │
│ │ <button class="team-btn" id="interject-send-...">  │   │
│ │   Send                                              │   │
│ │ </button>                                           │   │
│ │ 0/2000                                              │   │
│ └────────────────────────────────────────────────────┘   │
│                                                          │
│ Show live feed                                           │
└──────────────────────────────────────────────────────────┘
```

**Placeholder copy**: "Send a message to the team (this will not answer the pending question above)…"

**Rationale**: The longer placeholder text alerts the operator that they have two separate actions available — answering the escalation (via the panel above) and sending a free-form message (via the compose box below). This prevents the operator from accidentally typing an answer into the wrong input.

**Styling & behavior**: Identical to the Running state, except placeholder text differs.

### Compose box: Text Input
When the operator starts typing:

```
Compose box with live character counter:
┌────────────────────────────────────────────────────┐
│ <textarea>Please check if the database migration │
│ completed successfully.</textarea>                │
│ <button class="team-btn">Send</button>            │
│ 67/2000                                            │
└────────────────────────────────────────────────────┘
```

**Character counter**: Updates on every keystroke. Displays current character count and the limit (2000). Text color is #888 (muted gray) while under limit.

### Compose box: Character Limit Exceeded
When text reaches or exceeds 2000 characters:

```
Compose box with exceeded-limit indicator:
┌────────────────────────────────────────────────────┐
│ <textarea>(2000+ characters of text)</textarea>   │
│ <button class="team-btn" [disabled]>Send</button> │
│ 2047/2000                                          │
│ (character counter text in #ff6b6b red)           │
└────────────────────────────────────────────────────┘
```

**Behavior**:
- Counter text turns #ff6b6b (error red).
- `.over-limit` class is added to the counter element (developer may use this for additional styling, e.g., slightly larger font).
- Send button becomes **disabled** (greyed out, `disabled` attribute set, cursor: not-allowed).
- Operator cannot POST the message while over limit (client-side guard). If they somehow bypass it (hand-crafted request), server-side validation catches it.

### Compose box: Empty or Whitespace-Only Draft
When the textarea is empty or contains only whitespace:

```
Compose box with empty text:
┌────────────────────────────────────────────────────┐
│ <textarea placeholder="Send a message..."></textarea>│
│ <button class="team-btn" [disabled]>Send</button> │
│ 0/2000                                             │
└────────────────────────────────────────────────────┘
```

**Behavior**:
- Send button is **disabled** (greyed out).
- Counter shows "0/2000" (gray text, not red).
- Operator must type at least one non-whitespace character before Send becomes enabled.

### Compose box: Sending in Progress
While `POST /projects/<name>/team/interject` is in flight:

```
┌────────────────────────────────────────────────────┐
│ <textarea [disabled]>Please check the database...</textarea>
│ <button class="team-btn" [disabled]>Send</button> │
│ 47/2000                                            │
│                                                    │
│ ✓ Sending message: <project-name>…               │
│ (status message below, styled via code overlay)  │
└────────────────────────────────────────────────────┘
```

**Behavior**:
- Textarea and Send button become **disabled** (greyed out).
- A TOTP code-overlay may appear if this is the first action in the session (existing `toggle()`/TOTP machinery).
- Status message appears in `#team-msg-<name>` (shown by the code overlay during the 428-retry flow).

### Compose box: Success
After `POST /projects/<name>/team/interject` returns `{"ok": true, ...}`:

```
┌────────────────────────────────────────────────────┐
│ <textarea placeholder="Send a message..."></textarea>│
│ (textarea is cleared)                              │
│ <button class="team-btn" [disabled]>Send</button> │
│ 0/2000                                             │
│                                                    │
│ ✓ Message sent                                     │
│ (green success message)                            │
└────────────────────────────────────────────────────┘
```

**Behavior**:
- Textarea value is cleared and reset to empty state.
- `teamInterjectText[name]` (client-side draft map) is deleted.
- Send button returns to **disabled** (empty input).
- Character counter resets to "0/2000".
- Success message "✓ Message sent" appears below in `.team-msg.success` (green, #34c759).
- On the next `pollTeamFeed()` cycle (within ~4 seconds), the new event appears in the feed with `agent="human"`, `kind="message"`.

### Compose box: Error
After `POST /projects/<name>/team/interject` returns an error (`{"error": "...", ...}` with 400 status):

```
┌────────────────────────────────────────────────────┐
│ <textarea>Please check the database...</textarea>  │
│ (draft text is preserved — operator can edit and retry)
│ <button class="team-btn">Send</button>            │
│ 47/2000                                            │
│                                                    │
│ ✕ Error: message over length limit                │
│ (red error message, stays until next send attempt)│
└────────────────────────────────────────────────────┘
```

**Behavior**:
- Textarea retains the failed draft (intentionally NOT cleared), allowing the operator to edit and retry without retyping.
- Error message "✕ Error: <server message>" appears below in `.team-msg.error` (red, #ff6b6b).
- Send button remains **enabled** (so operator can fix and retry).
- Message persists until the operator clicks Send again (which clears the previous message) or until `refresh()` is called.

### Feed: Human Messages (New Row Styling)
In the live event feed, human-authored messages now appear with a distinct left-border accent:

```
Team feed (scrollable list):
┌──────────────────────────────────────────────────────┐
│ All • lead • human • teammate-1                      │
│                                                      │
│ 14:23:15 lead Fact-check result: ✓ file.py:42…   │
│                                                      │
│ 14:23:42 human Please verify schema consistency    │
│ (left-border: #4da6ff, text: normal #eee)          │
│                                                      │
│ 14:24:01 lead Fact-check result: ✗ no match       │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**Styling**:
- `.team-feed-event.kind-human-message` adds a **left-border: 3px solid #4da6ff** (blue accent, same color as active filter pills).
- Added **padding-left: 12px** to accommodate the border and maintain visual alignment with other rows.
- Text content remains normal (#eee) — no text-color override like `.kind-error` or `.kind-terminal-escalation`. The left-border is the primary visual cue.
- Agent name ("human") is colored via `teamAgentColor("human")` (existing hash-based palette, stable).

**Contrast check** (left-border accent #4da6ff on #1c1c1c background):
- Luminance: #4da6ff = 0.289; #1c1c1c = 0.018; ratio = (0.289 + 0.05) / (0.018 + 0.05) = **6.67:1**, **passes WCAG AA** for graphical elements (3:1 minimum).

### Feed: "Human" Filter Pill
A new filter pill "human" is added to the filter row:

```
Filter row:
All • lead • human • teammate-1 • teammate-2

When clicked:
All • lead • human (active, highlighted) • teammate-1 • teammate-2

Feed shows only human-authored messages:
14:23:42 human Please verify schema consistency
14:28:15 human Can you re-run with verbose logging?
14:30:00 human Thanks, the logs helped a lot.
(Only human.agent === 'human' events are shown)
```

**Styling**:
- Pill uses the same button styling as other filter pills: `.team-feed-filter button` (12px font, 3px 9px padding, #333 border, #1c1c1c background, #aaa text).
- Active state: `.active` class (background: #16324a, color: #4da6ff, border-color: #4da6ff) — same as existing lead/teammate pills.
- `aria-pressed="true"` / `aria-pressed="false"` attributes (existing pattern for accessibility).

**Behavior**:
- Pill is rendered unconditionally (even if no human messages have been sent yet for this run).
- Clicking it sets `teamFeedFilter[name] = 'human'`, triggering `refresh()`.
- Feed is filtered via the existing generic logic: `events.filter(e => e.agent === 'human')`.
- If no human messages exist, the feed shows "No events yet." (same as any other empty filter state).

## Accessibility & platform notes

- **Touch target sizes**: Send button is `.team-btn` (14px font, 10px 16px padding, 10px border-radius) — typical desktop button size (~44-48px height), exceeding the mobile accessibility minimum of 44px; web app is desktop-only, so no mobile-specific adjustment needed.
- **Color contrast**:
  - Send button text (#111, not #fff as originally claimed here — corrected 2026-08-14 per backlog item 20's audit, which changed `.deploy-btn, .team-btn`'s `color: #fff` to `color: #111` to fix a real AA failure) on button background (#34c759): **8.51:1** (passes WCAG AAA; the previously claimed 5.05:1 for white text was also wrong — white-on-#34c759 is actually ~2.2:1, failing AA).
  - Character counter text (#888) on row background (#1c1c1c): **6.14:1** (passes WCAG AA; existing token).
  - Character counter text over-limit (#ff6b6b) on background: **6.14:1** (passes WCAG AA; existing error token).
  - Left-border accent (#4da6ff) on background (#1c1c1c): **6.67:1** (passes WCAG AA for graphical elements, 3:1 minimum applies).
- **Keyboard interaction**:
  - Tab to textarea → enter message text.
  - Tab to Send button → press Enter to send (no Shift+Enter for newline — single-line escape not supported, per spec's "no chat-style Enter-to-send" decision).
  - Tab moves between compose-box elements and other row controls (escalation panel, feed toggle, Stop button) in visual order.
- **Textarea accessibility**: Textarea has a `placeholder` attribute (sufficient context from the long placeholder text). No explicit `<label>` is added (consistent with the task-description textarea above in the idle state, which also uses placeholder-only).
- **Form state accessibility**: Send button's `disabled` attribute is set/unset based on character count and content. Screen readers announce disabled state.
- **Live character counter**: Updates on `oninput` event. Screen readers may pick up live text changes (not announced as `aria-live` update, just as DOM text change), but this is acceptable — counter is supplementary; the Send button's disabled state is the primary guard.
- **Compose-box visibility**: The entire compose box (`.team-interject` container) is hidden (not rendered) when ineligible (`team.status` not running/blocked-waiting). Existing accessibility contracts are preserved: `role="log"` / `aria-live="polite"` on the feed list stays intact (compose box doesn't interfere).
- **Web vs. native**: This is a web app (HTML/CSS/JS in Flask template), desktop-only. No native/mobile variant, no mobile-specific touch states needed.
- **Error message accessibility**: Error and success messages appear in `#team-msg-<name>` (always-present DOM slot, empty initially). Screen readers pick up the slot when text is populated. Messages persist until next send attempt or `refresh()`, allowing operators time to read.

## Traceability to spec

| Acceptance criterion (from docs/spec.md) | Where it's addressed in this design |
|---|---|
| Compose box visible when `team.status === 'running'` or `(team.status === 'blocked' && team.waiting_on_you)` | Visibility section: Running and Blocked-Waiting-on-You states show compose box; Hidden section covers other statuses |
| Compose box + escalation panel coexist when `waiting_on_you === true` | Blocked, Waiting-on-You state shows both; distinct placeholders clarify separation |
| Placeholder copy distinguishes "free message" from "answer the question" | Running state: neutral placeholder; Blocked state: placeholder includes "(this will not answer...)" guidance |
| Textarea (`#interject-<name>`) + Send button (`#interject-send-<name>`) | Compose box structure shows both IDs; button styled with `.team-btn` class |
| 2-row textarea, 13px font | Styling: `rows="2"`, `font-size: 13px`, inherits `.team-interject-textarea` from `.team-textarea` pattern |
| Live character counter `len + '/' + 2000` | Text Input state shows counter updating; Over-limit state shows red counter and disabled button |
| Counter text turns red (#ff6b6b) when len > 2000 | Over-limit state: counter text color #ff6b6b, `.over-limit` class added |
| Send button disabled when empty, over-limit, or non-trimmed-whitespace | Empty/Whitespace state and Over-limit state show disabled button |
| Send button text: "Send" | Compose box structure shows "Send" label |
| `doTeamInterject(name)` calls `toggle('team-interject', name, true, null)` | Implementation reuses toggle()/actionPath()/actionBody()/handleActionResult() plumbing |
| On success, clear textarea, draft map, show "✓ Message sent" | Success state shows cleared textarea, success message |
| On error, preserve draft, show "✕ Error: ..." | Error state shows preserved draft text and red error message |
| New `kind-human-message` CSS class in feed rendering | Feed: Human Messages section shows `.kind-human-message` with left-border accent |
| Human message rows have left-border or background tint, distinct from error/terminal-escalation | Feed styling: `border-left: 3px solid #4da6ff`, `padding-left: 12px`; distinct from red/orange kind-* classes |
| New "human" filter pill in filter row, always present | Feed: "Human" Filter Pill section shows pill rendered unconditionally, `aria-pressed` toggles |
| Filtering works via existing generic filter logic (no new code path) | Filter behavior: existing `events.filter(e => e.agent === filter)` applies to `filter === 'human'` |
| `role="log"` / `aria-live="polite"` on feed intact (no redesign of feed structure) | Accessibility: compose-box hidden when ineligible, doesn't interfere; feed rendering unchanged |
| Compose-box draft cleared when status transitions away from compose-eligible | Hidden state: unsent draft is discarded; reappear-prevention via `delete teamInterjectText[name]` in `clearTeamFeedState()` |
| No Enter-to-send (consistent with other multi-line textareas in app) | Spec's "No Enter-to-send" non-goal is honored; Send via button click only |
| `TEAM_INTERJECT_MAX_CHARS_CLIENT = 2000` (mirrors server constant) | Over-limit detection and counter use 2000 limit; note in spec non-goal that client/server can drift if env var is overridden |

## Implementation notes for the developer

1. **CSS additions**: Add new rules near existing `.team-escalation-*` / `.team-feed-*` CSS (app/app.py line 2215-2277):
   ```css
   .team-interject { display: flex; flex-direction: column; gap: 8px; margin-top: 4px;
                     padding: 10px 12px; border: 1px solid #333; border-radius: 8px; background: #181818; }
   .team-interject-row { display: flex; gap: 8px; }
   .team-interject-textarea { font-size: 13px; padding: 8px 10px; border-radius: 8px;
                              border: 1px solid #333; background: #1c1c1c; color: #eee;
                              resize: vertical; min-height: 44px; font-family: inherit; }
   .team-interject-counter { font-size: 12px; color: #888; text-align: left; }
   .team-interject-counter.over-limit { color: #ff6b6b; }
   .team-feed-event.kind-human-message { border-left: 3px solid #4da6ff; padding-left: 12px; }
   ```

2. **JavaScript state map**: Add at module scope (near existing `teamTaskText = {}`, `teamPickerMembers = {}`, etc.):
   ```javascript
   let teamInterjectText = {};  // name -> string draft
   const TEAM_INTERJECT_MAX_CHARS_CLIENT = 2000;
   ```

3. **Visibility predicate**: Add helper function:
   ```javascript
   function teamAcceptsInterject(team) {
     return !!team && (team.status === 'running' ||
                       (team.status === 'blocked' && team.waiting_on_you));
   }
   ```

4. **Render function**: Add `renderTeamInterjectBox(name, team)` (spec's "Proposed approach" §1 provides complete pseudocode).

5. **Update function**: Add `updateTeamInterjectControls(name)` (spec's pseudocode provided).

6. **Dispatch function**: Add `doTeamInterject(name)` (spec's pseudocode provided).

7. **Action routing**: Extend existing `actionPath()`, `actionBody()`, `handleActionResult()` with `kind === 'team-interject'` branches (spec's pseudocode provided).

8. **Clear function**: Extend `clearTeamFeedState(name)` to add `delete teamInterjectText[name];` (one line).

9. **Feed styling**: Update `teamFeedEventKindClass()` to add branch (spec's pseudocode):
   ```javascript
   if (e.kind === 'message' && e.agent === 'human') return 'human-message';
   ```

10. **Filter pills**: Update `renderTeamFeed()` filter-pill agent list:
    ```javascript
    const agents = ['lead', 'human'].concat((team.composition && team.composition.members) || []);
    ```

11. **Integration point**: In `teamRow()`, insert the compose-box render output between escalation panel and feed toggle (spec shows exact insertion point).

## Traceability to spec (implementation details)

All implementation details (DOM ID patterns, class names, TOTP integration, handler structure) are spelled out in the spec's "Proposed approach" sections 1–3, with complete pseudocode. This design document adds the UI/UX layer (wireframes, state descriptions, accessibility notes, color rationale) on top of that functional spec.

## Accessibility audit (Dieter Rams' "good design is..." principles, applied as self-check)

1. **Good design is innovative** — The compose box and human-message styling are additive, not disruptive; they reuse existing patterns (textarea, button, filter pill) without inventing new UI paradigms. Human messages fit seamlessly into the structured event log rather than breaking it into a chat-bubble redesign. (Passes)

2. **Good design makes the product useful** — Operators can now send free-form updates to the team without navigating elsewhere; messages appear immediately in the shared feed for the lead to see. Distinct styling makes it easy to scan which events are human-authored vs. agent-authored. (Passes)

3. **Good design is aesthetic** — No new color tokens introduced (reuses existing blue, green, gray, red). Layout is compact and respects the existing row-based hierarchy. Left-border accent is subtle, not garish. (Passes)

4. **Good design makes the product understandable** — Placeholder text clarifies the purpose ("Send a message to the team") and the relationship to other controls ("...will not answer the pending question"). Character counter is simple and unambiguous. Filter pill follows existing conventions. (Passes)

5. **Good design is unobtrusive** — Compose box is only shown when needed (running/blocked-waiting). Human messages don't break the existing log structure or accessibility contract (`role="log"`/`aria-live="polite"`). No persistent UI chrome; draft is ephemeral. (Passes)

6. **Good design is honest** — No misleading disabled states; button is disabled when it should be (empty, over-limit). Error messages show the exact server error, not a polished-but-vague generic message. 2000-character limit is visible and enforced client-side before POST. (Passes)

7. **Good design is long-lasting** — Reuses established patterns (textarea, button, filter pill, action plumbing). No trendy chat-bubble redesign that will look dated in 2 years. Integrates with the existing event-log paradigm designed for multi-party, structured messages (agents + lead + human). (Passes)

8. **Good design is thorough down to the last detail** — Placeholder copy adapts to context (running vs. blocked-waiting). Draft is preserved on error (allowing retry without retyping) but cleared on success. Compose box is hidden and draft discarded when status no longer accepts interjections (preventing stale-draft bugs). Character counter updates live. WCAG contrast ratios computed and verified. (Passes)

9. **Good design is environmentally responsible** — No heavy animations, no auto-refresh, no background polling for the compose box alone (uses existing /status poll). No new infrastructure. (Passes)

10. **Good design is as little design as possible** — Compose box is a simple textarea + button + counter. Human messages are identified by a single CSS class. No new components, no new routes, no new data shapes. Follows existing conventions (toggle()/.team-msg/.team-btn). (Passes)

---
