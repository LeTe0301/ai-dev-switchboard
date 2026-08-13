# Implementation: Upload wizard polish (backlog item 3's deferred follow-ups)

## Summary
Closed out the three small, low-risk polish items backlog item 3 explicitly
deferred at ship time: `UPLOAD_MAX_ENTRIES` is now a real `switchboard.env`
knob (was a bare Python constant), step 5's single/split mode choice renders
as pill-styled labels (CSS-only, real `<input type="radio">` kept underneath)
matching `engineRow`/`codeRow`'s existing pill look, and step 5's "Back"
button is now only rendered in the ambiguous sub-case.

## Root cause
N/A — polish/config item, not a bugfix.

## Changes by file
- `app/app.py`
  - `UPLOAD_MAX_ENTRIES` (~line 84): changed from a bare `20000` constant to
    `int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))`, exact same pattern
    as `UPLOAD_STAGING_TTL_SECONDS`/`GITEA_POLL_INTERVAL_SECONDS`. No
    try/except (matches sibling precedent — a malformed value fails fast and
    loudly at import time, not silently at request time).
  - CSS block (near `.wizard-check-row .info .sub`): added
    `.wizard-check-row.pill-choice` and
    `.wizard-check-row.pill-choice:has(input:checked)`, matching `.pill`/
    `.pill.active`'s padding/border-radius/colors per `docs/design.md`.
  - `renderStep5()`: added the `pill-choice` class to the two mode-choice
    `<label class="wizard-check-row">` elements only (the split-candidate
    checkboxes below stay plain `wizard-check-row`, unstyled).
  - `renderStep5Actions()` → `renderStep5Actions(d)`: now takes the
    `detectResult` object and only emits the "Back" button's HTML when
    `d.ambiguous` is true; "Confirm" is always emitted.
  - `renderWizard()`'s step-5 branch: updated the one call site to
    `renderStep5Actions(wizardState.detectResult)`.
- `config/switchboard.env.example`: replaced the "this is a hardcoded
  constant, setting it here does nothing" comment block with a real
  commented-out `#UPLOAD_MAX_ENTRIES=20000` line plus a one-line description
  of the many-tiny-files zip DoS it guards against, matching
  `#GITEA_POLL_INTERVAL_SECONDS=45`'s style elsewhere in the same file.
- `docs/BACKLOG.md`: struck through item 3's three deferred-polish bullets
  with a note that they shipped in this pass.
- `tests/test_upload.py`: added `UploadMaxEntriesEnvVarTests` (two cases —
  env var set overrides the default, env var unset keeps `20000`). Imports
  `app.py` in a fresh subprocess per case (`sys.executable -c ...`) rather
  than mutating the already-imported `appmod` shared by every other test in
  the module, since the thing under test is specifically the module-import-
  time `os.environ.get(...)` read.
- `tests/test_upload_frontend.js` (new): frontend tests for step 5's pill
  styling and conditional Back button, following
  `tests/test_deploy_frontend.js`'s established pattern — extracts the real
  rendered `<script>` from `app.render_page()` via a Python subprocess and
  runs it in a Node `vm` context against minimal `document`/`fetch` stubs.
  8 tests: pill-choice class present on exactly the 2 mode-choice labels;
  split-candidate checkboxes stay unstyled; checked state follows
  `wizardState.mode` and re-renders correctly after `setWizardMode()`;
  `setWizardMode()` still updates `wizardState.mode` (no regression); the
  mode choice still uses real, focusable `<input type="radio">` (not a bare
  `<span class="pill">`); unambiguous case renders Confirm-only; ambiguous
  case renders Back+Confirm with Back's `resetWizardState(); renderWizard();`
  onclick unchanged; and `renderStep5Actions(d)`'s new signature returns the
  right HTML directly for both `d.ambiguous` values.

## Key decisions / tradeoffs
- Kept the native `<input type="radio">` per spec/design — the pill look is
  purely a `<label>`-level CSS restyle (`pill-choice` class), not a
  `engineRow`/`codeRow`-style bare-span replacement. This preserves Tab/
  arrow-key/Enter/Space native radio semantics and screen-reader
  announcement, matching the spec's explicit accessibility requirement.
- Used CSS `:has()` for the checked-state pill styling, per design.md's
  primary recommendation — no compatibility issue found (this is a modern,
  single-page app with no stated legacy-browser policy, and `:has()` is
  well-supported in the browsers this app already implicitly targets), so
  the `onchange`-driven `classList.toggle` fallback design.md offered as a
  backup was not needed.
- `UPLOAD_MAX_ENTRIES` parsing intentionally has no try/except, matching its
  siblings' fail-fast-at-import behavior rather than adding new tolerant
  parsing just for this one variable.
- For the `UPLOAD_MAX_ENTRIES` env-var test, chose a subprocess-per-case
  import over `importlib.reload()` or monkeypatching the shared `appmod`
  object, since `test_upload.py` imports `app` once at module load and is
  shared by every other test class in the file — reloading in-process risked
  leaking a mutated environment/module state into unrelated tests run later
  in the same process.

## Deviations from spec
None. Implemented per `docs/spec.md`'s "Proposed approach" and
`docs/design.md`'s exact CSS/HTML/JS specifications (class name
`wizard-check-row.pill-choice`, `renderStep5Actions(d)` signature, call site
passing `wizardState.detectResult`). The `:has()` vs. `onchange`-fallback
open question in spec.md resolved to `:has()` (no contrary browser-support
signal was found anywhere in the repo).

## Known limitations
- The `:has()` CSS rule's actual visual rendering (green pill when checked)
  cannot be exercised by the Node-based frontend test (no real browser/CSS
  engine in that harness) — the test instead asserts on the DOM-observable
  proxy for correctness: the `checked` attribute is present on the right
  `<input>` after each state change, and the `pill-choice` class is present
  on both labels. Actual visual/contrast correctness was verified by reading
  the CSS values directly against design.md's stated palette (`#2a2a2a`/
  `#aaa` idle, `#34c759`/`#111` checked — identical to the already-shipped
  `.pill`/`.pill.active` values elsewhere in the same file).
- No change to what "Back" does when clicked (still a full wizard reset via
  `resetWizardState()`) — explicitly out of scope per spec's non-goals.

## How to verify locally
```bash
# Backend: UPLOAD_MAX_ENTRIES env-var read + full existing upload suite
python3 -m unittest tests.test_upload -v

# Full existing python suite (nothing else touched, but a good sanity pass)
python3 -m unittest discover -s tests -v

# Frontend: step 5 pill styling + conditional Back button
node tests/test_upload_frontend.js

# Other frontend suites, to confirm no regression from the renderWizard()
# call-site edit or CSS block addition
node tests/test_deploy_frontend.js
node tests/test_singleton_toggle_frontend.js

# Manual/visual check (optional): start the app, open the upload wizard,
# upload a folder that yields an ambiguous detection result (a root with a
# .git plus nested repos, or a root with no .git and multiple subfolders) to
# see the pill-styled single/split choice and the Back+Confirm buttons; a
# root with exactly one project to register shows Confirm only.
```
All commands above were run during implementation; both python suites pass
(67/67 in `test_upload`, 289/289 overall) and all three JS suites pass
(8/8 new, 9/9 deploy, 15/15 singleton-toggle).
