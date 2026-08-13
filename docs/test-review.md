# Test & Review: Upload wizard polish (backlog item 3's deferred follow-ups)

## Scope
Covers `docs/spec.md`'s 6 acceptance criteria: `UPLOAD_MAX_ENTRIES` becoming
a real `switchboard.env` knob (fail-fast, matching sibling vars), step 5's
single/split mode choice rendering as pill-styled labels with native
`<input type="radio">` preserved, and `renderStep5Actions(d)`'s conditional
"Back" button. This is a fresh review for this cycle — the prior
`docs/test-review.md` on disk (2c part 2b, deploy dispatch) was stale and is
superseded by this file.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `UPLOAD_MAX_ENTRIES=5` set → app starts, effective limit is 5 | automated (`UploadMaxEntriesEnvVarTests.test_env_var_set_overrides_default`) + **live E2E** | pass | subprocess reads back `5`; live HTTP server with `UPLOAD_MAX_ENTRIES=5` rejected a 6-entry zip (400 "too many entries in zip file") and accepted a 5-entry zip (200), boundary-tested at exactly the limit |
| 2 | No `UPLOAD_MAX_ENTRIES` set → default stays 20000 | automated (`test_env_var_unset_keeps_default_20000`) | pass | subprocess reads back `20000` |
| 3 | Ambiguous step 5: mode choice renders as 2 pill-styled elements, checked state follows `wizardState.mode`, `setWizardMode()` unchanged | automated (3 JS tests) + live server HTML check | pass | `node tests/test_upload_frontend.js`; live `curl` of a real running server's `/` response contains identical `pill-choice` CSS/HTML at the same lines as the diff |
| 4 | Ambiguous step 5, keyboard: focus lands on the native radio, not a bare span | automated (JS test) | pass | asserts 2 real `<input type="radio">`, no `<span class="pill">` |
| 5 | Unambiguous step 5: no Back button, only Confirm | automated (JS test) | pass | `actionsHtml` has no `Back`/`class="secondary"` |
| 6 | Ambiguous step 5: Back rendered exactly as before (`resetWizardState(); renderWizard();`) | automated (JS test) | pass | onclick string asserted verbatim |
| edge | `UPLOAD_MAX_ENTRIES` non-numeric → fails fast at import time, same as siblings | manual code exercise | pass | `UPLOAD_MAX_ENTRIES=notanumber` and `GITEA_POLL_INTERVAL_SECONDS=notanumber` both raise the identical `ValueError` at the identical `import app` line pattern, exit code 1 |
| edge | Unambiguous case still lets user abandon the wizard with Back hidden | code read | pass | `<span class="back" onclick="closeUploadWizard()">‹ close</span>` at `app/app.py:1529` is modal-level, independent of `wizard-actions`, unaffected by this diff |
| edge | Split-candidate checkboxes stay unstyled (only the 2 mode-choice labels get `pill-choice`) | automated (JS test) | pass | `2 plain wizard-check-row checkbox rows` asserted separately from the 2 `pill-choice` labels |

## Regression check
- `python3 -m unittest discover -s tests -v` → **289/289 pass** (matches developer's report).
- `node tests/test_upload_frontend.js` → **8/8 pass**.
- `node tests/test_deploy_frontend.js` → **9/9 pass** (no regression).
- `node tests/test_singleton_toggle_frontend.js` → **15/15 pass** (no regression).

All four numbers independently re-run and confirmed this session, matching the developer's reported figures exactly.

## Live exercise (beyond the Node-`vm` harness)
Started a real `ThreadingHTTPServer` instance of `app.Handler` (both via the
module's own `__main__` entrypoint and, for the entry-count check, via an
in-process server matching `tests/test_upload.py`'s own HTTP-test technique):
- Fetched `/` with `curl` and confirmed the served page's CSS/HTML for
  `pill-choice` and `renderStep5Actions(d)` are byte-identical to the diff
  (not just what the JS test's subprocess extraction produced).
- Did a full login → TOTP → `POST /projects/upload` round trip with
  `UPLOAD_MAX_ENTRIES=5`: a 6-entry zip was rejected (400), a 5-entry zip
  (exactly at the limit) was accepted (200) — proves the env var is actually
  wired into the enforcement check at `app/app.py:2794`, not just parsed and
  unused.
- Confirmed the malformed-value crash behavior is bit-for-bit identical in
  shape to the sibling `GITEA_POLL_INTERVAL_SECONDS` var.
- No real browser was available in this environment to visually render
  `:has()`; verified instead by reading the actual CSS values and comparing
  against the already-shipped `.pill`/`.pill.active` rules (see Findings).

## Findings (most severe first)

### 1. `docs/design.md`'s stated contrast ratios are inaccurate (both understate the actual, safer, value) — nit
- File: `docs/design.md:215-216`
- Design doc states pill-idle contrast (`#aaa` on `#2a2a2a`) as "~4.5:1" and
  pill-active (`#111` on `#34c759`) as "~11:1". Recomputed both from the
  literal hex values using WCAG relative luminance: idle is actually
  **~6.18:1**, active is actually **~8.52:1**. Both real values still
  comfortably clear the 4.5:1 AA text threshold, so there is no accessibility
  regression — the stated numbers were just imprecise, and in the safe
  direction (understating idle, overstating active, both still passing).
  These are also pre-existing `.pill`/`.pill.active` values reused verbatim,
  not new colors introduced by this diff. Not blocking; flagging only
  because the design doc's own numbers were off and a future contrast change
  nearby should recompute rather than copy this doc's figures forward.

No other findings. Correctness, security, and simplicity review below found nothing further.

## Spec coverage
All 6 acceptance criteria plus all 3 documented edge cases in `docs/spec.md`
are implemented and covered by at least one test that was actually run this
session (automated or live-exercised), per the table above. No gap.

## Correctness / security / simplicity review
- **Env var fail-fast**: `UPLOAD_MAX_ENTRIES = int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))` at `app/app.py:84` is textually and behaviorally identical in pattern to `GITEA_POLL_INTERVAL_SECONDS` (line 170) and `UPLOAD_STAGING_TTL_SECONDS` — confirmed both via code read and by triggering the same `ValueError` shape for both at import time.
- **Single enforcement site**: `UPLOAD_MAX_ENTRIES` is read exactly once at module import and used at exactly one call site (`app/app.py:2794`, `if len(infolist) > UPLOAD_MAX_ENTRIES:`) — no stale-copy or shadowing risk.
- **Conditional Back button**: `wizardState.detectResult` is always set (`app/app.py:2341`) before `wizardState.step` can become `5` (single assignment site, immediately followed by the `step = 5` transition), so `renderStep5Actions(wizardState.detectResult)`'s `d` is never undefined when step 5 renders — no null-deref risk. `proceedToConfirm()`/Confirm's own behavior is untouched by this diff (only the surrounding conditional emits Back's HTML or not); the unambiguous flow's Confirm button and its onclick are unchanged.
- **No injection/security surface**: purely a CSS class addition, a conditional string-concat around an already-existing static button, and a config-driven `int()` — no new user input reaches HTML output or a shell/SQL boundary. `esc()` usage on `d.root_name`/`splitLabel` in `renderStep5()` is pre-existing and untouched.
- **Scope**: diff matches `docs/spec.md`'s "Affected areas" exactly — `app/app.py` (const, CSS, `renderStep5()`, `renderStep5Actions()`, one call site), `config/switchboard.env.example`, `docs/BACKLOG.md`, plus the two test files. No unrelated changes found in `git diff`.

## Follow-ups (non-blocking)
- None required. The contrast-figure nit above is informational only.

## Overall verdict
**Approve.** Testing pass is clean: 289/289 Python, 8/8 new + 9/9 + 15/15 JS,
all independently re-run this session, plus a live HTTP end-to-end exercise
(real server, real login/TOTP/upload round trip) confirming `UPLOAD_MAX_ENTRIES`
actually gates the entry-count guard at the configured threshold and that the
served page's markup/CSS matches the diff verbatim. Review pass found no
must-fix or should-fix issues — one informational nit on `docs/design.md`'s
imprecise (but safely-directioned) contrast figures. All 6 acceptance
criteria and all 3 edge cases are implemented and covered. This closes the
build cycle — hand back to product-manager.
