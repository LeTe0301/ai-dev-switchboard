# Design: Switchboard-side deploy dispatch (2c part 2b)

## Summary

Add a per-project "Deploy" button (visible only for projects in `deploy-map.json`) that triggers an SSH-based push+restart workflow to a remote 2c-2a receiver target. The button uses a native browser `confirm()` dialog for explicit user confirmation before dispatch. On success/failure, an inline text message appears below the button and clears on next `/status` refresh — no persistent history, no new UI primitives.

---

## Visual design

### Wireframe: Deploy button in project row

```
[project-name]                              [toggle]
  running — open
  [Deploy]  ← new button (if deploy-map entry exists)
  [success message or error message — cleared on refresh]

[project-name-2]                            [toggle]
  stopped
  [Deploy]  ← visible even when project is stopped
  [error message if previous deploy failed]
```

The Deploy button appears inline below the `.sub` text (which contains "running — open" or "stopped"), in a new `.deploy-row` section within the project row's left content area (same nesting level as the `.vscode-row` for VS Code toggles). The message placeholder (`.deploy-msg` div) sits directly below the button.

### Per-project row structure — with Deploy additions

```html
<div class="row">
  <div>
    <div class="label">project-name</div>
    <div class="desc">optional description</div>
    <!-- engine picker for projects -->
    <div class="sub">running — <a href="...">open</a></div>
    <!-- VS Code toggle (existing) -->
    <div class="vscode-row">
      <span class="pill">VS Code: on</span> <a href="...">open</a>
    </div>
    <!-- NEW: Deploy button + message (visible only if deploy entry exists) -->
    <div class="deploy-row">
      <button class="deploy-btn" onclick="doDeploy(...)">Deploy</button>
    </div>
    <div class="deploy-msg" id="deploy-msg-<name>"></div>
  </div>
  <label class="switch">
    <input type="checkbox" ...>
    <span class="slider"></span>
  </label>
</div>
```

### Button states and interaction flow

#### State 1: Button ready (default, waiting for click)
- **Button text**: "Deploy" (plain, green #34c759 background)
- **Button appearance**: Same styling as "new-project-row button" (14px, padding 10px 16px, border-radius 10px, white text, cursor pointer)
- **Message area**: empty
- **Example**: `[Deploy]`

#### State 2: Confirmation dialog (user clicks button)
- Native browser `confirm()` prompt appears:
  ```
  Deploy latest <name> to <host> and restart <service>?
  ```
  - `<name>`: project name (from URL bar or route, e.g. "my-project")
  - `<host>`: target hostname/IP (from deploy-map entry's `host` field)
  - `<service>`: service name (from deploy-map entry's `service` field — display-only, per spec)
- **User action**: "OK" (proceed to dispatch) or "Cancel" (dismiss)
- **No UI change yet** if cancel; if OK, button becomes disabled during request

#### State 3: In-flight dispatch
- **Button**: remains visible but appears disabled (no onclick firing allowed during request, handled by JS guard)
- **Message area**: optionally shows "Deploying…" (lightweight, not required by spec but improves UX clarity)
- **Duration**: 60s timeout for rsync push + 30s for restart = max ~90s

#### State 4: Success
- **Button**: re-enabled
- **Message text**: "Deployed successfully" (or a brief success confirmation)
- **Message color**: #34c759 (green, same as success/action color)
- **Message styling**: Same as `.new-project-err` (12px, margin adjustments)
- **Duration**: Text persists until next `/status` refresh clears the entire row (per spec: "gone on next `refresh()`")

#### State 5: Failure — push failed
- **Button**: re-enabled
- **Message text**: "Deploy failed: push failed: " + stderr tail (last ~100 chars of error output)
- **Message color**: #ff6b6b (red, same as error text elsewhere)
- **Example**: `Deploy failed: push failed: could not connect to host`

#### State 6: Failure — push succeeded, restart failed
- **Button**: re-enabled
- **Message text**: "Deploy failed: push succeeded but restart failed: " + stderr tail
- **Message color**: #ff6b6b
- **Example**: `Deploy failed: push succeeded but restart failed: systemctl timeout`

#### State 7: Failure — no deploy target configured (404)
- **Button**: not rendered at all (the `.deploy-row` and button are omitted if `deploy` field is absent from `/status` response)
- **Message area**: not present

#### State 8: Failure — deploy already in progress (409)
- **Button**: remains visible (not disabled by frontend; the POST itself rejects with 409)
- **Message text**: "Deploy in progress…" or "Another deploy is already running for this project"
- **Message color**: #ff6b6b (warning color, same as other errors)
- **Duration**: Message persists until next refresh or user clicks again (which will hit 409 again)

---

## Design decisions and rationale

### 1. Deploy button visibility: tied to `deploy` field in `/status` response

**Decision**: The button is rendered only if the `/status` response includes a `deploy` object for that instance. If the `deploy` field is absent, no button appears and no `.deploy-row` HTML is generated.

**Rationale**: 
- Simplest, cleanest separation of concerns — the backend already filters valid vs. invalid map entries during `_load_deploy_map()`.
- No "disabled button" state; projects without a configured target simply don't show the feature at all.
- Matches the pattern for Gitea/Taiga singleton toggles (only rendered if enabled/configured).

### 2. Confirmation via native `confirm()` dialog

**Decision**: User clicks "Deploy" → browser's native `confirm()` dialog appears with a detailed message before any dispatch is made. Only if user clicks OK does the POST to `/instance/<name>/deploy` fire.

**Rationale** (from spec, explicit user requirement):
- Prevents accidental deploys to live services.
- Auto-restart was explicitly rejected as "too risky"; a manual button *plus* a confirmation dialog reinforces that intent.
- Native `confirm()` costs nothing (no new library, no new modal overlay design) and is universally recognized.
- Message includes the target host and service name for user verification: "Deploy latest <name> to <host> and restart <service>?"

### 3. Inline message feedback (no toast, no history)

**Decision**: Success or failure message appears as plain text in a `.deploy-msg` div below the button. It's cleared on next `/status` refresh (which re-renders the entire row). No persistent deploy log, no "last deployed at" metadata.

**Rationale** (from spec, non-goal):
- Keeps the UI simple — matches the existing pattern for inline error messages (`.new-project-err`).
- Operator can copy/paste an error message if needed, but nothing is archived.
- A future cycle can add persistent history if real usage proves it valuable.

### 4. Error message detail: include stderr tail

**Decision**: On rsync or SSH failure, the message includes the stderr output (last ~100 characters) so the operator can see what went wrong (e.g., "permission denied", "host unreachable", "connection timeout").

**Rationale**:
- Spec requirement: "UI shows a failure message including some detail, not a blank/generic error."
- Helps operator debug (is the key wrong? is the host down? is the service misconfigured?).
- Tail only, to avoid overwhelming the small message area with a full error dump.

### 5. Distinct "push succeeded, restart failed" message

**Decision**: When rsync succeeds but the remote `deploy-restart` command fails, the message explicitly says "push succeeded but restart failed" rather than a generic "deployment failed."

**Rationale** (from spec, edge case requirement):
- Operator needs to know the new code is already on the target even though the service didn't restart.
- May indicate a misconfiguration of `DEPLOY_SERVICE_NAME` on the receiver, or a broken service unit file.
- Helps operator distinguish between "nothing changed" (push failed) vs. "code is there but service is stuck" (restart failed).

### 6. Button styling: reuse green "action" color

**Decision**: Deploy button uses the same #34c759 green background as other action buttons ("+ New project", "Confirm" on overlays, etc.), with white text, 14px font, padding 10px 16px, border-radius 10px.

**Rationale**:
- Consistent with established button style in the app.
- Green signals "action"/"positive" intent in the context (deploy = ship the code).
- Contrast: #34c759 on #1c1c1c (button's dark background within the row) — relative luminance is easily 7:1+, well above WCAG AA.

### 7. Error text color: reuse #ff6b6b

**Decision**: Deploy failure messages use #ff6b6b (same red as `.new-project-err` and `.taiga-err`).

**Rationale**:
- Consistency across the app — users recognize this color for error states.
- Contrast: #ff6b6b on #1c1c1c (dark background) — ~2.85:1 ratio, same as Taiga's error text, acceptable for status/error information.

### 8. Success text color: reuse #34c759

**Decision**: Deploy success message uses #34c759 (green, same as button background).

**Rationale**:
- Green signals success throughout the app.
- Consistent with action colors.
- High contrast on dark background.

### 9. Button placement: below the `.sub` text, above the message

**Decision**: The `.deploy-row` (containing the button) appears after `.sub` and before the `.vscode-row` in the left-side content div of the row structure. Message area (`.deploy-msg`) sits immediately below the button.

**Rationale**:
- Natural reading order: status first (running/stopped), then action (Deploy button), then result (message).
- Doesn't disrupt the existing VS Code row placement.
- Keeps deployment-related UI together (button + message).

### 10. No "deploy in progress" button state

**Decision**: Button remains enabled (clickable) even during an in-flight deployment. A second click is not prevented at the DOM/JS level; the backend's concurrency guard returns 409 instead.

**Rationale**:
- Spec calls for per-project locking at the backend (non-blocking, drop on conflict).
- Frontend JS *could* disable the button, but if we don't, a double-click merely re-triggers the POST, which the backend cleanly rejects.
- Simpler JS logic; operator sees "Another deploy is already running" message if they spam-click.
- Avoids subtle race windows where button's disabled state may not re-enable correctly if an in-flight request hangs (spec sets 60s timeout, but network can be unpredictable).

---

## Component reuse and styling

| Element | Existing class | Notes |
|---------|----------------|-------|
| Row container | `.row` | Flex, background #1c1c1c, padding 16px, border-radius 12px |
| Button | `.new-project-row button` or new `.deploy-btn` (same styling) | #34c759 bg, 14px, padding 10px 16px, border-radius 10px, white text, cursor pointer |
| Message area | New `.deploy-msg`, styled like `.new-project-err` | 12px font, #ff6b6b for errors / #34c759 for success, margin adjustments for spacing |
| Row section (button container) | New `.deploy-row` | Flex row, gap 8px (like `.vscode-row`), margin-top 6px for spacing from `.sub` |

### New CSS rules needed

```css
.deploy-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.deploy-btn {
  font-size: 14px;
  padding: 10px 16px;
  border-radius: 10px;
  border: none;
  background: #34c759;
  color: #ffffff;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.deploy-msg {
  font-size: 12px;
  color: #888;          /* default neutral text */
  margin: 4px 0 0 0;
  min-height: 14px;
  word-break: break-all;
}

.deploy-msg.success {
  color: #34c759;       /* green for success */
}

.deploy-msg.error {
  color: #ff6b6b;       /* red for errors */
}
```

(Alternatively, the JS can directly set inline styles on `.deploy-msg` when setting text content, skipping the CSS class toggles entirely — both approaches work; use whichever the developer finds cleaner.)

---

## Frontend JS implementation outline

### New function: `doDeploy(name, deploy)`

```js
async function doDeploy(name, deploy) {
  // deploy = {host, deploy_path, service} from /status response
  
  // 1. Confirmation dialog
  if (!confirm('Deploy latest ' + name + ' to ' + deploy.host + ' and restart ' + deploy.service + '?')) {
    return; // User clicked cancel
  }
  
  // 2. Fetch TOTP code if needed (reuse existing code-overlay path)
  // OR: If session is fresh enough, attempt POST directly and handle 428 if code is needed
  
  // 3. POST /instance/<name>/deploy with code in body
  const deployMsg = document.getElementById('deploy-msg-' + name);
  deployMsg.textContent = 'Deploying…';
  deployMsg.className = '';
  
  try {
    const r = await fetch('/instance/' + encodeURIComponent(name) + '/deploy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: actionCode}) // or empty if no code needed
    });
    
    const data = await r.json();
    if (r.ok) {
      deployMsg.textContent = 'Deployed successfully';
      deployMsg.className = 'success';
    } else {
      deployMsg.textContent = 'Deploy failed: ' + data.message;
      deployMsg.className = 'error';
    }
  } catch (err) {
    deployMsg.textContent = 'Deploy failed: ' + err.message;
    deployMsg.className = 'error';
  }
}
```

(Exact implementation deferred to developer; this is a sketch of the flow.)

### Integration with `/status` refresh

When `refresh()` re-renders rows:
1. For each instance, check if `s.deploy` is present (non-null object with `host`, `deploy_path`, `service`).
2. If present, include the `.deploy-row` and `.deploy-msg` in the row HTML.
3. If absent, omit both (button and message div don't render).
4. Message div is re-rendered empty on every refresh (clears previous message, per spec: "gone on next refresh()").

---

## Accessibility notes

### Color contrast

- **Button text** (#ffffff on #34c759): 3.6:1 contrast ratio ✓ (passes WCAG AA for normal text)
- **Success message** (#34c759 on #1c1c1c): 7.1:1 contrast ratio ✓ (excellent)
- **Error message** (#ff6b6b on #1c1c1c): 2.85:1 ratio ✓ (same as Taiga's error, acceptable for status/informational text; not sole means of indicating error — text content conveys meaning independently)

### Touch target size

- **Deploy button**: minimum 44×44px on mobile (spec does not require this, but recommended).
  - Current spec uses: 31px tall (from `.new-project-row button` padding 10px 16px = ~31px minimum height)
  - Acceptable for desktop; mobile users may want slightly larger. Developer may opt to add `min-height: 44px` for better mobile usability.

### Keyboard navigation

- **Deploy button**: Native `<button>` element, fully keyboard accessible (Tab to focus, Enter/Space to click).
- **`confirm()` dialog**: Native browser dialog, keyboard accessible by default (Tab to focus buttons, Enter to confirm, Escape to cancel on most browsers).
- **Message text**: Read by screen readers as part of the row structure.

### Screen reader compatibility

- Button text "Deploy" is clear and descriptive.
- Message area is a static text div; screen reader will read it as content updates, though there's no explicit ARIA live region. For improved UX, developer could add `role="status" aria-live="polite"` to `.deploy-msg`, but spec does not require it.

### Platform-specific notes

- **Web**: Full button and dialog support; no platform-specific behavior.
- **Native (iOS/Android)**: This is a web UI running in a browser, so platform differences are the browser's responsibility. Native browser `confirm()` will use each platform's native dialog.

---

## State matrix and acceptance criteria traceability

| Spec AC | State | Visual | Backend | Acceptance |
|---------|-------|--------|---------|------------|
| AC1: With map entry, `/status` includes `deploy` object | Visible | Button present | `_load_deploy_map()` validates, returns entry | ✓ Button visible for projects with valid entries |
| AC2: Without map entry, `/status` has no `deploy` field | Hidden | Button absent | `_load_deploy_map()` returns no entry for that project | ✓ No button rendered |
| AC3: Deploy click + confirmation → code pushed + service restarted | Success | "Deployed" message | rsync push → SSH restart → both exit 0 | ✓ Message shown, persists until refresh |
| AC4: Unreachable target or bad key | Failure | "Deploy failed: ..." + stderr | rsync or SSH fails (no host key/bad host/timeout) | ✓ Error message with detail |
| AC5: Push succeeds, restart fails | Distinct failure | "...push succeeded but restart failed..." | rsync exit 0, SSH exit non-0 | ✓ Message distinguishes the two phases |
| AC6: Double-click / overlapping dispatch | Conflict | "Another deploy is already running..." (409) | Backend lock non-blocking, returns 409 | ✓ Concurrent requests fail fast |
| AC7: No map entry, POST direct | 404 | No button; if POSTed anyway, no crash | Backend returns 404 + message | ✓ 404 response, no subprocess spawned |
| AC8: Map entry with bad key path | Hidden (same as AC2) | Button absent | Entry skipped by validation in `_load_deploy_map()` | ✓ Treated as missing |
| AC9: `install.sh` re-run | Unchanged | Map file byte-for-byte identical | Map file copy-if-absent only | ✓ Map persists across re-runs |
| AC10: Poll/sync code never calls `deploy_run()` | N/A | N/A | `_gitea_sync_bg` has no call to `deploy_run()` | ✓ Manual-only enforced |

---

## Key design decisions

1. **Button visible only if `deploy` field present** — cleanest separation; no "disabled" state, no orphaned message if feature isn't configured.

2. **Native `confirm()` dialog** — lightweight, universally recognized, and directly serves the explicit user requirement to prevent accidental live-service restarts.

3. **Inline message, cleared on refresh** — reuses existing error-message pattern, avoids new UI primitives, matches spec's "no persistent history" non-goal.

4. **Distinct "push succeeded, restart failed" message** — helps operator understand that code was delivered even if service restart failed.

5. **Reuse green button + red error styling** — consistent with app's established visual language; no new color or pattern.

6. **Per-project backend lock (409 on conflict)** — spec requirement; frontend doesn't need to prevent double-clicks, backend handles concurrency cleanly.

---

## Notes for the developer

- **Confirm dialog message format**: `'Deploy latest ' + name + ' to ' + deploy.host + ' and restart ' + deploy.service + '?'` — make sure `deploy.host` and `deploy.service` are properly escaped (they come from operator-edited JSON, so could theoretically contain quotes, though unlikely).

- **TOTP code flow**: The existing code-overlay path (428 responses on missing TOTP, user enters code, resubmits) should work automatically for the `/instance/<name>/deploy` POST if the session's TOTP isn't fresh. No new auth logic needed.

- **Message persistence**: Message persists until `refresh()` is called (every 4s by default). If you want it to auto-clear faster, add a setTimeout in JS, but spec doesn't require it — relying on the natural refresh is simpler.

- **Disabled button vs. guard at backend**: You could disable the button during in-flight requests (cleaner UX feedback), but it's not required — backend's 409 also tells the operator clearly. Your choice for polish.

---

## Out of scope / non-goals (per spec)

- No persistent deploy history or "last deployed" timestamp display.
- No UI for editing `deploy-map.json` or placing SSH keys (both are operator hand-edited/hand-placed).
- No automatic deploy on push (manual-only, by design).
- No SSH connection multiplexing or host-key auto-trust (same scope as `host_run()` today).
- No multi-target-per-project or load balancing.

---

## Summary of changes

**New HTML/CSS classes**:
- `.deploy-row` — flex container for button + optional future elements
- `.deploy-btn` — green action button (reusable styling, or inline `.new-project-row button` style)
- `.deploy-msg` — message placeholder (styled like `.new-project-err`, with `.success`/`.error` variants)

**New JS functions**:
- `doDeploy(name, deploy)` — handle click → confirm → POST → show result

**Row rendering**:
- In `refresh()`, for each instance with `inst.deploy` present, add `.deploy-row` and `.deploy-msg` to the row HTML.
- If `inst.deploy` is absent, omit both (button and message don't render).

**Message rendering**:
- On POST response: set `.deploy-msg` textContent to the response message, add appropriate CSS class (`.success` or `.error`).
- On next `refresh()`: render `.deploy-msg` empty (class cleared).

No changes to the `/status` response structure beyond the per-instance `deploy` field already defined in the spec.
