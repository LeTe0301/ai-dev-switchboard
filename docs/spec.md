# Spec: Upload wizard polish (backlog item 3's deferred follow-ups)

## Summary
Closes out the three small, low-risk polish items backlog item 3 (folder
upload → auto-detect repo(s), shipped 2026-08-12, commit `893840c`)
explicitly deferred rather than blocking that ship: make `UPLOAD_MAX_ENTRIES`
a real settable env var, restyle step 5's single/split choice as the
pill-button look `docs/design.md` originally called for, and only show step
5's "Back" button in the ambiguous sub-case where there's actually something
to go back and change.

## Goals
- `UPLOAD_MAX_ENTRIES` becomes a real `switchboard.env` knob, read the same
  way `UPLOAD_STAGING_TTL_SECONDS`/`GITEA_POLL_INTERVAL_SECONDS` already are,
  instead of only being documented as a comment with no effect.
- Step 5's "Single project" / "Split out nested repos" (or "Each subfolder
  as its own project") choice renders as pill buttons matching the visual
  language already used for the engine picker (`engineRow`) and the VS Code
  toggle (`codeRow`), per `docs/design.md`'s original "Radio buttons visually
  styled as pills" wireframe note — while keeping the underlying
  `<input type="radio">` for accessibility/keyboard semantics (see "Proposed
  approach").
- Step 5's "Back" button (`renderStep5Actions()`) is only rendered in the
  ambiguous case (`d.ambiguous === true`), matching `docs/design.md`'s own
  wireframes: sub-case A (unambiguous) shows only `[ Confirm > ]`; sub-cases
  B/C (ambiguous) show `[ Back < ]  [ Confirm > ]`.

## Non-goals
- Not changing what "Back" *does* (it still fully resets the wizard to step
  1 via `resetWizardState()` — no partial/undo-one-step semantics). The
  BACKLOG note flags "no partial back exists" as a separate, vaguer
  observation; giving step 5 real back-to-step-4 semantics without forcing
  a re-upload would be a materially bigger change (need to keep the phase-1
  staging token alive and re-enter step 5 rather than discarding it) and is
  explicitly out of scope for this pass. See "Open questions."
- Not changing `UPLOAD_MAX_ENTRIES`'s default value (stays `20000`) or its
  enforcement point (`app.py`'s zip-entry-count check) — only how the value
  is sourced.
- Not touching step 6 ("Confirm")'s own "Back to review" / "Start over"
  buttons — those already work as designed per `docs/design.md`, not part of
  the three deferred nits.
- Not a redesign of the wizard beyond these three items — no new UX pass on
  the rest of the upload flow.

## Background / current state
- `app/app.py` line 85: `UPLOAD_MAX_ENTRIES = 20000` — a bare Python
  constant, never read from the environment. `config/switchboard.env.example`
  (around line 251) has a comment explicitly telling the operator that
  setting a value there "would have no effect." Every sibling tunable in
  this same file/area (`UPLOAD_STAGING_TTL_SECONDS`, `UPLOAD_MAX_BYTES`,
  `GITEA_POLL_INTERVAL_SECONDS`) already follows the
  `int(os.environ.get("X", "default"))` pattern.
- `app/app.py` `renderStep5()` (~line 2489) renders the single/split choice
  as two `<label class="wizard-check-row"><input type="radio" ...>` rows —
  functionally correct (keyboard/screen-reader accessible via native radio
  semantics) but visually a plain checkbox-style row, not the pill look
  `docs/design.md`'s original step 5 wireframe (commit `893840c` version,
  "Key design elements" #2: "Radio buttons visually styled as pills (round
  background, green when selected) rather than native radio inputs") called
  for. The codebase already has two other pill-styled choice widgets to
  match: `engineRow()`'s engine picker and `codeRow()`'s VS Code toggle pill
  (both `<span class="pill...">`, no underlying `<input>`).
- `app/app.py` `renderStep5Actions()` (~line 2536) always renders both
  `Back` and `Confirm` buttons unconditionally, even when `!d.ambiguous`
  (the "one project to register" sub-case, where `renderStep5()` returns
  early after a single confirmation line with no choice to make). The
  original design.md wireframe's sub-case A shows only `[ Confirm > ]`
  in that case.

## Proposed approach

**1. `UPLOAD_MAX_ENTRIES` env var**
- `app/app.py` line 85: change to
  `UPLOAD_MAX_ENTRIES = int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))`,
  same pattern/placement as its neighbors.
- `config/switchboard.env.example`: replace the current "this is a hardcoded
  constant, setting it here does nothing" comment block with a real
  commented-out `#UPLOAD_MAX_ENTRIES=20000` line plus a one-line description
  of what it guards against (many-tiny-files zip DoS), matching the style of
  `#GITEA_POLL_INTERVAL_SECONDS=45` elsewhere in the same file.
- `docs/BACKLOG.md` item 3's deferred-items bullet list: mark this sub-item
  done (or remove it, since the whole "Status" block will need a short note
  that these three follow-ups shipped — see "Affected areas").

**2. Pill-styled step 5 choice**
- Keep the real `<input type="radio" name="wizard-mode">` elements (do not
  switch to bare `<span class="pill">` like `engineRow`/`codeRow` — those
  have no underlying form control and are mouse/click-only; step 5's choice
  is exactly the kind of binary settings choice where native radio
  keyboard/screen-reader semantics matter and the BACKLOG note explicitly
  called the current version "accessible," which this pass should not
  regress). Instead, visually restyle the existing `<label
  class="wizard-check-row">` wrapper for just these two rows with a new CSS
  class, e.g. `wizard-check-row.pill-choice`, that gives it the same visual
  treatment as `.pill`/`.pill.active` (rounded background, `#2a2a2a`
  idle / `#34c759` + dark text when the radio inside is `:checked`, using a
  CSS `:has()` selector — same browsers this app already targets support it,
  since `engineRow`'s active-state pill already relies on JS-computed
  classes rather than any older-browser-compat constraint) — `border-radius:
  20px`, `padding: 5px 12px` matching `.pill`'s own values, keeping the
  44px-min-height/8px-gap requirements from `docs/design.md`'s accessibility
  section on the outer `<label>`.
- `renderStep5()`: add the `pill-choice` class to the two `wizard-check-row`
  labels for the mode choice only (not the split-candidate checkboxes below
  them, which stay checkbox-styled, matching the original wireframe where
  only the top single/split choice is pill-styled).

**3. Conditional Back button**
- `renderStep5Actions()`: change signature to `renderStep5Actions(d)` (or
  read `wizardState.detectResult` directly, matching how `renderStep5()`
  itself gets `d`) and only emit the `Back` button's HTML when `d.ambiguous`
  is true; always emit `Confirm`. Update the one call site that invokes
  `renderStep5Actions()` to pass `wizardState.detectResult` if the signature
  changes.

## Affected areas
- `app/app.py`: `UPLOAD_MAX_ENTRIES` declaration (~line 85); CSS block
  (~line 1386, near `.pill`) for the new `pill-choice` styling;
  `renderStep5()` (~line 2489) for the two mode-choice label classes;
  `renderStep5Actions()` (~line 2536) for the conditional Back button.
- `config/switchboard.env.example`: `UPLOAD_MAX_ENTRIES` comment → real
  commented-out line.
- `docs/BACKLOG.md`: item 3's "Status" note, updating the three deferred
  bullets to reflect they've shipped (with a commit reference once merged).
- `tests/test_upload_frontend.js` (new file — no upload-wizard frontend JS
  test file exists yet; only `tests/test_upload.py`/
  `tests/test_new_project_from_upload.py` cover the backend today). Follow
  `tests/test_deploy_frontend.js`'s established pattern of extracting the
  real rendered `<script>` from `render_page()` and driving it against
  stubbed `document`/`fetch` — one test per acceptance criterion below.
  `tests/test_upload.py`: extend with the `UPLOAD_MAX_ENTRIES` env-var
  acceptance criteria.
- No `app.py` route/backend-logic changes beyond the `UPLOAD_MAX_ENTRIES`
  env read — no data model, API, or install.sh changes.

## Edge cases
- `UPLOAD_MAX_ENTRIES` set to a non-numeric value in `switchboard.env`:
  matches existing sibling behavior for the same class of var — this repo's
  established convention (see `_load_deploy_map()`'s per-entry validation,
  `docs/implementation.md` 2c-2b) is "malformed input must not crash the
  whole process," but `UPLOAD_MAX_ENTRIES`'s siblings
  (`UPLOAD_MAX_BYTES`/`UPLOAD_STAGING_TTL_SECONDS`/
  `GITEA_POLL_INTERVAL_SECONDS`) all use a bare `int(...)` with no
  try/except — a bad value crashes the process at startup, loudly, not
  silently at request time. Match that existing sibling precedent exactly
  (fail fast at import time) rather than inventing new tolerant-parsing
  behavior for just this one var — consistency with its neighbors matters
  more here than defensive-coding perfection.
- Step 5 pill choice: `:checked`-driven `:has()` styling must still visibly
  distinguish the selected pill without JS involvement beyond the existing
  `onchange="setWizardMode(...)"` (no new JS state needed — the browser's
  own `:checked` state drives the CSS).
- Conditional Back button: the unambiguous sub-case must still let the user
  abandon the wizard entirely — confirm the wizard's existing modal-level
  close/X control (not `renderStep5Actions()`'s own buttons) remains
  reachable when Back is hidden, so removing Back doesn't strand anyone.

## Acceptance criteria
- [ ] Given `switchboard.env` sets `UPLOAD_MAX_ENTRIES=5`, when the app
  starts, then a zip with more than 5 entries is rejected by the existing
  entry-count guard (same behavior as today's hardcoded `20000`, just at the
  configured threshold).
- [ ] Given no `UPLOAD_MAX_ENTRIES` is set, when the app starts, then the
  effective limit is still `20000` (default unchanged).
- [ ] Given step 5 renders in the ambiguous case, when the page loads, then
  the "Single project" / "Split..." choice renders as two pill-styled
  elements (rounded background) with an `active`/checked visual state on
  the currently selected one, and clicking either still calls
  `setWizardMode()`/updates `wizardState.mode` exactly as before (no
  behavior regression, visual-only change).
- [ ] Given step 5 renders in the ambiguous case, when a keyboard-only user
  tabs to the choice, then focus lands on the underlying radio input (native
  radio keyboard semantics preserved, not replaced by an unfocusable span).
- [ ] Given step 5 renders in the unambiguous ("one project to register")
  sub-case, when the page loads, then no "Back" button is rendered — only
  "Confirm".
- [ ] Given step 5 renders in the ambiguous sub-case, when the page loads,
  then "Back" is rendered exactly as today (click still calls
  `resetWizardState(); renderWizard();`).

## Open questions
- Whether to also give step 5's "Back" real back-to-step-4 (re-pick file)
  semantics instead of a full wizard reset — flagged as a non-goal above;
  proceeding under the assumption that only the *visibility* condition
  (BACKLOG's specific, concrete complaint) is in scope for this pass, not a
  reworked back-stack. Flag to the user if this reading is too narrow.
- Whether `:has()` CSS selector support is acceptable given this project's
  browser-support bar — no explicit minimum-browser-version policy exists
  elsewhere in the repo that this spec author could find; proceeding under
  the assumption that a `:has()`-based active-pill style is fine (same
  assumption implicitly already made by every other CSS feature in this
  fairly modern, single-page embedded app). If the developer finds contrary
  guidance, fall back to a small `onchange`-driven `classList.toggle`
  instead — functionally equivalent, no product-level decision either way.

## Risk / rollback notes
Purely additive/cosmetic frontend change plus one env-var wiring change,
all inside `app/app.py`'s embedded `PAGE_TEMPLATE` and one config-example
comment — no data model, migration, or route changes. Rollback is a
straight `git revert` of the single commit. Worst case if something's
wrong: `UPLOAD_MAX_ENTRIES` env parsing throws at startup on a malformed
value (matches sibling-var behavior, loud and immediate, not a silent
runtime bug) or the pill CSS renders oddly in an unusual browser (visual
only, choice still fully functional via the underlying radio input).
