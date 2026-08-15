# Design: Install wizard UI — part 1: optional-feature checklist (BACKLOG item 15, piece 5)

## Overview

This design refines the exact dialog flow and copy for three TUI screens that replace `ct/create.sh:56-64`'s two standalone `yesno` prompts with a unified feature-selection checklist plus two conditional follow-ups. This is a terminal UI running interactively on a Proxmox VE host over SSH/console — no graphical layout, visual design system, or accessibility considerations beyond clear messaging, logical flow, and keyboard navigation.

---

## Dialog flow sequence

### 1. Optional-features checklist (replaces `ct/create.sh:56-64`)

**Trigger:** After auth-mode selection, before publish-mode selection.

**Screen:** `whiptail --checklist` (18 rows, 78 columns wide, 4 visible rows)

**Title:** "ai-dev-switchboard" (standard window title)

**Prompt text:**
```
Optional features to enable on this container (Space to toggle, Enter to confirm):
```

**Checklist rows** (all default to `OFF`):

| Tag            | Description                                              | Default | Width note                  |
|----------------|----------------------------------------------------------|---------|-----------------------------|
| `git-hosting`  | Private repos over SSH + "+" New project button         | OFF     | 56 chars, fits within 78    |
| `code-server`  | VS Code in the browser, per project                     | OFF     | 38 chars, fits within 78    |
| `taiga`        | Self-hosted Taiga backlog/kanban tracker                | OFF     | 48 chars, fits within 78    |
| `ollama`       | Link a remote Ollama for multi-agent team leads         | OFF     | 49 chars, fits within 78    |

**Row-label rationale:**
- **git-hosting:** Condensed from install.sh's header comment (15-19) emphasizing "private repos over SSH" and the UI integration point ("+ New project").
- **code-server:** Condensed from install.sh (24-25), simple and direct.
- **taiga:** Condensed from install.sh (31-35), one-line summary of "self-hosted backlog tracker."
- **ollama:** Condensed from install.sh (36-43), emphasizing "link existing" (nothing installs locally) and the "multi-agent team leads" use case.

All labels fit comfortably within the 78-column width.

**User interaction:**
- **Space** toggles a row's state (OFF → ON or ON → OFF).
- **Tab/Shift-Tab** navigates between rows.
- **Enter** confirms and proceeds.
- **Escape/Cancel** aborts the entire wizard (standard whiptail behavior per spec's edge-case rule).

**Output parsing:** `whiptail --checklist` returns quoted tag names, one per line (e.g., `"git-hosting"\n"taiga"`). Spec's proposed `ct/create.sh` code handles quote-stripping safely without `eval`.

---

### 2. Taiga resource-cost acknowledgment (conditional: only if taiga checked)

**Trigger:** Immediately after checklist confirmation, if `WITH_TAIGA=1`.

**Screen:** `whiptail --msgbox` (single acknowledgment, no choice)

**Title:** "ai-dev-switchboard"

**Message text:**
```
Taiga runs 9 containers and can use several GB of RAM (and real disk, 
for Postgres/RabbitMQ data volumes) once turned on in the web UI; 
toggling it back off frees that RAM again right away.
```

**Rationale:**
This is adapted from install.sh:920-922 (the resource-cost callout from the end-of-run summary), with "in the web UI" added for clarity in the ct/create.sh context (at this point, Taiga will be installed but not yet running — the web UI is where the operator enables it). The callout is stripped of context about "installed but left OFF" and admin-account setup, which aren't relevant at wizard time.

**User interaction:**
- **Enter/Escape/OK button** dismisses and continues to the next step (or ollama prompts, if ollama is also checked).

---

### 3. Ollama endpoint & model validation (conditional: only if ollama checked)

**Trigger:** If `WITH_OLLAMA=1`, after taiga msgbox (if shown) or immediately after checklist (if taiga not checked).

This is a **retry loop**, not a single prompt. On failure, the operator chooses to retry with different values or skip.

#### 3a. Endpoint URL prompt

**Screen:** `whiptail --inputbox`

**Title:** "ai-dev-switchboard"

**Prompt text:**
```
Ollama endpoint URL (OpenAI-compatible, e.g. an existing remote Ollama's /v1)
```

**Default value:**
```
http://127.0.0.1:11434/v1
```

(Matches install.sh:753.)

**User interaction:**
- **Enter** submits the value.
- **Escape/Cancel** aborts the entire wizard.

---

#### 3b. Model name prompt

**Screen:** `whiptail --inputbox`

**Title:** "ai-dev-switchboard"

**Prompt text:**
```
Model name
```

**Default value:**
```
qwen3:8b
```

(Matches install.sh:754.)

**User interaction:**
- **Enter** submits the value and proceeds to validation.
- **Escape/Cancel** aborts the entire wizard.

---

#### 3c. Validation & failure handling

The script (via mirrored install.sh:761-805 logic) performs:
1. Normalizes the URL by removing trailing slash (if present).
2. Runs `curl -fsS --max-time 10 "$URL/models"` to fetch the available models list.
3. Parses the JSON response with an inline Python script to check for an **exact** model ID match (never substring/prefix — e.g., `qwen3:8` will not match `qwen3:8b`).

**Three possible failure modes:**

##### 3c-i. Unreachable endpoint

**Trigger:** `curl` fails (host unreachable, connection refused, timeout, HTTP error, or no response).

**Screen:** `whiptail --msgbox` (error message only)

**Message text:**
```
Could not reach <URL>/models (unreachable, no response, or an HTTP error).
```

Where `<URL>` is the normalized URL entered (e.g., `http://127.0.0.1:11434/v1`).

**Then:** Immediately followed by a `whiptail --yesno` (see 3c-iv below).

---

##### 3c-ii. Endpoint reachable, but model list is empty

**Trigger:** JSON parse succeeds, but the `data[]` array is empty.

**Screen:** `whiptail --msgbox`

**Message text:**
```
Reached <URL> but it has no models available.
```

**Then:** Immediately followed by a `whiptail --yesno` (see 3c-iv below).

---

##### 3c-iii. Endpoint reachable, model list has entries, but requested model absent

**Trigger:** JSON parse succeeds, model list is non-empty, but the exact model ID is not in the list.

**Screen:** `whiptail --msgbox`

**Message text (two variants, depending on what's available):**

If model list is empty (should not happen, but handled):
```
Reached <URL> but model '<model-name>' is not available there.
```

If model list has entries:
```
Reached <URL> but model '<model-name>' is not available there.
Available: <comma-separated model IDs>
```

Example with actual models:
```
Reached http://127.0.0.1:11434/v1 but model 'llama2' is not available there.
Available: qwen3:8b, mistral:latest, phi:2.5k
```

**Then:** Immediately followed by a `whiptail --yesno` (see 3c-iv below).

---

##### 3c-iv. Unparseable JSON response

**Trigger:** `curl` succeeds (HTTP 200), but the response is not valid JSON (e.g., a captive portal login page, an HTML proxy error, malformed text).

**Screen:** `whiptail --msgbox`

**Message text:**
```
Reached <URL>/models but its response could not be parsed as JSON.
```

**Then:** Immediately followed by a `whiptail --yesno` (see 3c-v below).

---

#### 3d. Retry-vs-skip choice (after any failure)

**Screen:** `whiptail --yesno`

**Title:** "ai-dev-switchboard"

**Prompt text:**
```
Try a different URL/model?
```

**Button labels:** "Yes" / "No"

---

#### 3e. Success path

**Trigger:** Exact model ID match found in the endpoint's model list.

**On success:**
- No dialog shown (silent success).
- The validated, trailing-slash-normalized URL and model name are stored for `switchboard.env` assembly (spec's step 4).
- Loop exits; wizard continues to the next step (or final summary, if no other items need follow-up).

---

#### 3f. Retry loop defaults (critical for UX)

**On retry (Yes to "Try a different URL/model?"):**
- The prompt for step 3a re-appears, but with the **previously-entered (failed) URL as the new default** (not the original `http://127.0.0.1:11434/v1`).
- The prompt for step 3b re-appears with the **previously-entered (failed) model name as the new default** (not the original `qwen3:8b`).
- The loop then validates the new values against the same logic as before.

**On skip (No to "Try a different URL/model?"):**
- `WITH_OLLAMA` is reset to `0`.
- `TEAM_LLM_BASE_URL` and `TEAM_LLM_MODEL` are **not** appended to `switchboard.env`.
- `--with-ollama` is **not** appended to `INSTALL_FLAGS`.
- A single `msgbox` is shown (see 3g below).
- Wizard proceeds to the next step (or final summary).

---

#### 3g. Skip acknowledgment

**Screen:** `whiptail --msgbox` (shown only if operator chose "No" to retry after a failure)

**Title:** "ai-dev-switchboard"

**Message text:**
```
Continuing without linking Ollama. You can re-run 'install.sh --with-ollama' 
inside the container later once the endpoint is reachable.
```

**User interaction:**
- **Enter/OK** dismisses and proceeds to the final summary.

---

## State coverage

### 1. No features checked
- Checklist shows all four rows unchecked.
- Confirm → proceed directly to final summary.
- `INSTALL_FLAGS` contains only `--yes` (no `--with-*` flags).

### 2. Only git-hosting checked
- Checklist shows git-hosting checked, others unchecked.
- No taiga/ollama follow-ups.
- Confirm → proceed directly to final summary.
- `INSTALL_FLAGS` includes `--with-git-hosting`.

### 3. Only code-server checked
- Checklist shows code-server checked, others unchecked.
- No taiga/ollama follow-ups.
- Confirm → proceed directly to final summary.
- `INSTALL_FLAGS` includes `--with-code-server`.

### 4. Only taiga checked
- Checklist shows taiga checked, others unchecked.
- Confirm → taiga msgbox shown → proceed to final summary.
- `INSTALL_FLAGS` includes `--with-taiga`.

### 5. Only ollama checked
- Checklist shows ollama checked, others unchecked.
- Confirm → ollama URL/model prompts → validation loop.
- On success: proceed to final summary. `INSTALL_FLAGS` includes `--with-ollama`, `TEAM_LLM_*` in `switchboard.env`.
- On skip: skip msgbox shown, proceed to final summary. `WITH_OLLAMA` reset to 0, no `TEAM_LLM_*` or `--with-ollama`.

### 6. Taiga + ollama checked
- Checklist shows both checked.
- Confirm → taiga msgbox → ollama URL/model prompts → validation loop → final summary.

### 7. All four checked
- Checklist shows all four checked.
- Confirm → taiga msgbox → ollama prompts → validation loop → final summary.
- `INSTALL_FLAGS` includes all four `--with-*` flags.

### 8. Ollama validation loop edge cases
- **Blank URL or model submitted:** Treated as invalid input (empty/malformed URL fails the `curl`, proceeding to unreachable-endpoint failure message).
- **Container network later can't reach endpoint:** Install.sh's own `--with-ollama` block re-validates from inside the container; if it fails, it prints a warning but doesn't un-write the already-set `TEAM_LLM_*` values (edge case documented in spec, accepted as inherent to double-validation).

---

## Accessibility & platform notes

### Terminal environment
- All prompts use standard `whiptail` dialogs (built-in to Debian 12, assumed available).
- **No graphical layout, colors, or fonts** — purely text-based, ANSI-compatible.
- **Keyboard-only navigation** — Tab, Space, Enter, Escape. Mouse support is optional in whiptail but not relied upon.
- All message text fits within the standard 78-column width (confirmed above for checklist row labels).

### Error message clarity
- Each ollama failure message names the specific failure reason (unreachable vs. model-absent vs. unparseable).
- Error messages include the actual URL/model entered so the operator can see exactly what was tried.
- Retry loop's pre-fill of failed values lets the operator easily tweak a single field without re-typing both.

### Network timeouts & escape hatch
- `curl` is bounded by `--max-time 10` (10-second timeout), preventing indefinite hangs.
- **Escape/Cancel always aborts** the entire wizard at any point (standard whiptail behavior per spec).
- No unbounded retry loop — operator can bail out with Cancel instead of being forced to retry.

---

## Component & helper reuse

### Existing `ct/create.sh` helpers
- **`msg()`** — `whiptail --msgbox` wrapper (used for taiga callout, ollama skip msgbox).
- **`ask()`** — `whiptail --inputbox` wrapper (used for ollama URL and model prompts).
- **`yesno()`** — `whiptail --yesno` wrapper (used for ollama retry-or-skip choice).

All three are already defined at `ct/create.sh:25-28`; no new wrappers needed.

### New dependencies
- **`python3`** — Required on the Proxmox host for ollama model-list parsing (via the JSON-parsing heredoc copied from install.sh). Installed unconditionally near `ct/create.sh:23` (alongside the existing `whiptail` preflight, same idiom) so it's available before the checklist screen even asks.

### Code reuse from install.sh
- **Ollama validation logic** (install.sh:761-805) is copied verbatim into `ct/create.sh`:
  - The `curl -fsS --max-time 10 "$URL/models"` command.
  - The Python heredoc (`OLLAMA_MODEL_CHECK_SCRIPT`) for exact model-ID matching.
  - The three failure-reason detection patterns (`PARSE_ERROR`, `MODEL_ABSENT:...`, `OK`).
- **No shared file or function library** — copy-paste idiom as per spec's "no shared framework" principle.

---

## Checklist row labels validation

Confirmed all row labels fit within the 78-column screen width:

| Row           | Tag + description + padding | Character count |
|---------------|----------------------------|-----------------|
| git-hosting   | `git-hosting` + 56 chars   | ~70 chars       |
| code-server   | `code-server` + 38 chars   | ~52 chars       |
| taiga         | `taiga` + 48 chars         | ~57 chars       |
| ollama        | `ollama` + 49 chars        | ~58 chars       |

All fit comfortably in 78 columns; no wrapping or truncation expected.

---

## Taiga msgbox wording validation

The message:
> "Taiga runs 9 containers and can use several GB of RAM (and real disk, for Postgres/RabbitMQ data volumes) once turned on in the web UI; toggling it back off frees that RAM again right away."

Is adapted from install.sh:920-922 (resource-cost callout) with "in the web UI" added for TUI context. Tested for standalone clarity:
- **Subject:** Taiga feature.
- **Resource cost:** 9 containers, several GB RAM, real disk.
- **Control point:** "in the web UI" (clarifies where operator toggles it).
- **Reversibility:** "toggling it back off frees that RAM again right away" (reassures).

Reads clearly as a single msgbox without the surrounding install.sh context about admin-account setup or "installed but left OFF."

---

## Ollama failure message validation

All three failure messages include the actual URL/model and name the specific reason:

1. **Unreachable:** "Could not reach <URL>/models (unreachable, no response, or an HTTP error)."
   - Clarifies that it's a network-level or HTTP issue.
   - Gives the operator the exact URL that failed to respond.

2. **Model absent (empty list):** "Reached <URL> but it has no models available."
   - Confirms endpoint is reachable.
   - Clarifies the problem is the model list being empty, not network failure.

3. **Model absent (model not in list):** "Reached <URL> but model '<model>' is not available there. Available: <list>."
   - Confirms endpoint is reachable.
   - Names the specific model not found.
   - Optionally shows available models so the operator can pick one.

4. **Unparseable JSON:** "Reached <URL>/models but its response could not be parsed as JSON."
   - Confirms endpoint is reachable.
   - Clarifies it's a response-format issue (e.g., captive portal login page, proxy error).

All fit within a standard whiptail msgbox (14 rows, 74 columns as per `ct/create.sh:25`).

---

## Retry loop pre-fill validation

The loop correctly pre-fills failed values as new defaults:

```bash
_ollama_url_default="$_ollama_url_input"      # Previously-entered URL
_ollama_model_default="$_ollama_model_input"  # Previously-entered model
```

This allows the operator to:
- Correct a typo in the URL without re-typing the model.
- Try a different model without re-typing the URL.
- Re-enter after an external network issue is resolved (endpoint back online).

The loop does not reset to the hardcoded defaults (`http://127.0.0.1:11434/v1` / `qwen3:8b`) on each iteration, which would be frustrating.

---

## Final summary (unchanged)

The wizard's final `msgbox` (`ct/create.sh:130-146`) is **not** extended with ollama linkage success/failure callouts. Ollama validation feedback happens at the time of the follow-up prompts themselves (success: silent exit; failure: specific failure msgbox + retry-or-skip choice). This matches today's precedent where the summary also doesn't explicitly call out `WITH_GIT_HOSTING`/`WITH_CODE_SERVER`.

---

## Summary of key design decisions

1. **Single unified checklist** replaces two yesno prompts, improving UX by showing all four options at once with clear one-line descriptions.

2. **Taiga msgbox** isolates the resource-cost callout as a dedicated acknowledgment, ensuring operators understand the RAM/disk implications before proceeding. (No force-exit, just acknowledgment.)

3. **Ollama validation at wizard time** (host-side, before `install.sh` runs) provides real-time feedback on endpoint reachability and model availability, instead of silent fallback to defaults when `install.sh --yes` skips interactive prompts.

4. **Three distinct failure messages** (unreachable, model-absent, unparseable) clarify exactly what went wrong and what to try next.

5. **Retry loop pre-fills failed values** as new defaults, letting the operator iterate without re-typing.

6. **Helper-function reuse** (existing `msg`, `ask`, `yesno` wrappers) keeps implementation simple and consistent with existing ct/create.sh style.

7. **Copy adaptation from install.sh** (taiga resource-cost callout, ollama validation logic, failure message patterns) ensures consistency without introducing new documentation.

**Component reuse vs. new:**
- **Reused:** `msg()`, `ask()`, `yesno()` whiptail helpers; ollama validation logic from install.sh.
- **New:** `whiptail --checklist` screen (not used elsewhere in ct/create.sh); taiga msgbox wording adapted from install.sh's end-of-run summary; ollama failure message templates adapted from install.sh's stderr echoes.
