# Implementation: Folder upload → auto-detect repo(s) — build cycle 2 of 2

This is **cycle 2 of 2** per `docs/spec.md`'s "Recommended build sequencing"
section. Cycle 1 (already merged) built the client-side store-mode zip
writer, wizard UI steps 1-4 (Pick/Exclude/Zip/Upload), and the backend
phase-1 endpoint (`POST /projects/upload` — staging + zip-slip protection +
size caps + structure detection only, registering nothing). This cycle
finishes the feature:

- `POST /projects/upload/confirm` (phase 2 — naming, collision-checking,
  privileged registration, partial-failure handling).
- `scripts/new-project-from-upload.sh` (the privileged hand-off script) and
  its unconditional `install.sh` wiring.
- `config/switchboard.env.example` documentation for all four upload-wizard
  config vars (including the two cycle 1 introduced but never documented).
- TTL/idle cleanup for abandoned uploads, wired into the existing
  `_reap_dead_state()`, plus confirm-triggered cleanup.
- Wizard UI steps 5-6 (Review, Confirm), replacing cycle 1's raw-JSON
  placeholder.
- `README.md` / `docs/ARCHITECTURE.md` updates.
- Resolves the deep-`.git`-nesting question cycle 1 flagged but didn't
  cleanly assign to either cycle.

## What changed, by file

### `app/app.py`

**New config** (module-level, next to `UPLOAD_MAX_BYTES`):
- `UPLOAD_STAGING_TTL_SECONDS` (default `1800`)
- `NEW_PROJECT_FROM_UPLOAD_SCRIPT` (default
  `/usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh`)

**New module-level functions** (added right after `detect_structure()`,
in a new "phase 2 (confirm + register)" section):
- `_derive_project_name(raw) -> str` — sanitizes a raw (fully
  attacker-controlled, since it comes from the uploaded zip's own folder
  names) name against `NAME_RE`'s character class; strips a leading
  non-alnum run, truncates to 60 chars, falls back to
  `upload-<8 hex chars>` if nothing usable survives.
- `_register_via_privileged_script(source, name) -> CompletedProcess` — the
  one line that actually shells out to `sudo` + the real script. Deliberately
  split into its own function (rather than inlined in the loop below) so
  tests can monkeypatch it directly instead of needing a real sudoers rule /
  installed script / matching `RUN_USER` on the test box — see "Not
  testable is a claim to verify" in the testing section below.
- `create_projects_from_selection(staging_root, mode, selected) -> (ok, err, registered, skipped)`
  — phase 2's core logic. Re-derives `effective_root` via
  `_unwrap_single_wrapper_folder` and re-runs `detect_structure()` fresh
  (never trusts the client's `selected` list), validates the selection
  against that fresh walk, derives+sanitizes every resulting project's name,
  collision-checks all of them up front (against existing `PROJECTS_DIR`
  entries and against each other) before any privileged script runs, then
  registers each one in turn via `_register_via_privileged_script`. On a
  race defeating one specific registration after siblings in the same call
  already succeeded, returns `ok=False` with that one named in the error and
  `registered` still listing the siblings that succeeded — those are not
  rolled back (matches spec's explicit "Partial-failure semantics").
- `confirm_upload(token, mode, selected) -> (http_status, response_dict)` —
  the route's actual logic. Validates the token shape strictly (only the
  exact `secrets.token_hex(16)` shape is accepted — it's used directly in a
  filesystem path, same defensive posture as the zip-slip checks), delegates
  to `create_projects_from_selection`, and removes
  `UPLOAD_STAGING_DIR/<token>/` only once that call **succeeds** — see
  "Post-review fix" below for why a failed confirm now leaves staging in
  place instead of always cleaning up.

**`Handler.do_POST`** — one new `elif` branch routing
`POST /projects/upload/confirm` (matched on `parts == ["projects", "upload",
"confirm"]`) to `confirm_upload()`. Unlike phase 1, this route needed **no**
special-casing in `do_POST` — it's an ordinary JSON body, goes through the
exact same shared TOTP gate every other mutating endpoint already uses
(`code` from the JSON body, not `?code=`).

**`_reap_dead_state()`** — extended with a TTL sweep at the end of the
function: any `UPLOAD_STAGING_DIR/<token>/` whose directory mtime is older
than `UPLOAD_STAGING_TTL_SECONDS` is removed. Reuses the function's existing
"opportunistic cleanup on a request that already happens often" precedent
(already called on every `/status` poll) rather than adding a background
thread/timer, per spec's explicit instruction.

**`PAGE_TEMPLATE`**'s `<script>` block:
- `resetWizardState()` gained the review/confirm-step fields: `mode`,
  `splitCandidates`, `splitSelected` (step 5), `confirmMode`,
  `confirmSelected`, `confirmStatus`, `confirmRegistered`, `confirmSkipped`,
  `confirmErrorMsg` (step 6).
- New `wizardConfirmAwaitingCode` boolean, parallel to cycle 1's
  `wizardAwaitingCode` — phase 2's TOTP retry uses the **standard**
  JSON-body `code` field (unlike phase 1's `?code=` deviation), so it needed
  its own flag to route `submitActionCode()`/`cancelActionCode()` correctly,
  but still reuses the exact same `#code-overlay` DOM and Enter-to-submit
  wiring. `hideCodeOverlay()`/`cancelActionCode()`/`closeUploadWizard()` each
  gained one line to also clear this new flag.
- `initReviewState()`, `setWizardMode()`, `toggleSplitPath()`,
  `renderStep5()`, `renderStep5Actions()`, `proceedToConfirm()` — the real
  Review step, replacing cycle 1's `renderStep5Placeholder`/
  `renderStep5Actions`. Implements all three of design.md's Review sub-cases
  (unambiguous single-project; monorepo split with nested paths defaulting
  **unchecked**; folder-of-subrepos split with subfolders defaulting
  **checked**), the duplication warning (shown only when a monorepo split is
  actually offered — root has `.git` and `mode === 'split'`), and the
  client-side "select at least one" pre-flight check for the no-root-`.git`
  split-with-zero-selected case (server still enforces this too — this is
  purely a fail-fast nicety, same spirit as cycle 1's client-side size-cap
  warning).
- `showWizardConfirmCodeOverlay()`, `runConfirm()`, `renderStep6()`,
  `renderStep6Actions()` — the real Confirm step, calling
  `POST /projects/upload/confirm` and rendering success (registered names +
  skipped count) or failure (inline error, "Back to review"/"Start over",
  preserving any partially-registered names from the response) per
  design.md.
- `renderWizard()`'s dispatcher updated to call the real step 5/6 functions
  instead of the placeholder.

## Deep `.git`-nesting decision (cycle 1's flagged, unresolved question)

Cycle 1's walk-pruning (`detect_structure`'s `os.walk` over `dirnames`) only
removes `".git"` itself from traversal once found — it does **not** prune
the rest of that `.git`'s containing directory. Cycle 1's own
`docs/implementation.md` already made a call here ("this reading is the more
thorough one and costs nothing extra to compute") but flagged it as
not-explicitly-spec'd. This cycle **confirms that decision unchanged** and
makes it explicit + tested, since the dispatch asked for it to be resolved
intentionally rather than left as an implicit side effect:

**Decision**: a `.git` found inside another already-discovered nested
repo's own *working tree* (as opposed to inside that repo's `.git`
*internals*, which genuinely is pruned) is still reported as its own,
separate `nested_git_paths` entry. Concretely, given
`vendor/thing/.git` and `vendor/thing/subvendor/.git`, both
`"vendor/thing"` and `"vendor/thing/subvendor"` are reported — genuine
repo-in-repo-in-repo nesting is surfaced, not silently collapsed into one
entry. No code change was needed (cycle 1's behavior already did this); this
cycle adds `DetectStructureTests.test_deeply_nested_git_inside_a_nested_repos_own_tree_is_reported`
in `tests/test_upload.py` to lock the decision in as intentional and
regression-tested, and restates the rationale in that test's own docstring.

Reasoning for keeping it as "report everything, let the user decide" rather
than "only report the shallowest nested repo per branch": the review step's
whole point (per spec) is surfacing structure for an **explicit** user
choice rather than silently collapsing it — collapsing deep nesting down to
one candidate would hide a real, selectable repo from that choice for no
stated benefit, and the extra `os.walk` cost of not pruning is negligible
(the walk already visits every directory regardless).

## `scripts/new-project-from-upload.sh` (new)

Mirrors `scripts/new-dev-instance.sh`'s structure (config sourcing, usage
check, `set -euo pipefail`) rather than copying it verbatim, since its job
is different (moving an already-staged directory, not cloning from a bare
repo):

1. Sources `/etc/ai-dev-switchboard/switchboard.env` (the **main**
   switchboard config, not `git-hosting.env` — this script must work on an
   install that never ran `--with-git-hosting` at all) for `RUN_USER`/
   `PROJECTS_DIR`, falling back to `dev`/`/home/dev/projects` if the file
   doesn't exist.
2. Re-validates `<name>` against the same character-class shape as
   `NAME_RE`, defense in depth (never trust the caller, even though it's
   `app.py` itself — this script carries a broad root grant).
3. `mkdir -p "$PROJECTS_DIR"` (creates it if this is a fresh install), then
   `mkdir "$DEST"` **without** `-p` at the leaf — fails atomically rather
   than silently merging if the target already exists, closing the TOCTOU
   race between `app.py`'s own up-front collision check and this script
   actually running.
4. `cp -a "$SOURCE_DIR/." "$DEST/"` — not `mv` (staging and `PROJECTS_DIR`
   may be different filesystems).
5. `chown -R "$RUN_USER:$RUN_USER" "$DEST"`.
6. If `$DEST/.git` doesn't already exist and `git` is installed: `git init`
   + one commit, run as `RUN_USER` via `su`, with `-c user.name=`/`-c
   user.email=` scoped to just that one commit (never written to
   `RUN_USER`'s global git config). Skipped silently (script still exits 0)
   if `git` isn't installed at all.

## `install.sh`

- Installs the new script **unconditionally** (right after the auth-mode
  config block, before the `--with-git-hosting`-gated block further down) —
  not behind `--with-git-hosting`.
- Creates `$STATE_DIR/uploads` (`/var/lib/ai-dev-switchboard/uploads`,
  matching `UPLOAD_STAGING_DIR`'s own default) and `chown`s it to
  `$SVC_USER`.
- `set_env`s `NEW_PROJECT_FROM_UPLOAD_SCRIPT` and
  `UPLOAD_STAGING_TTL_SECONDS` (`1800`) into the generated `switchboard.env`.
- Adds the new script's sudoers line to the **base, always-installed**
  block (alongside `tmux`/`ttyd`/`code-server`), not the
  `WITH_GIT_HOSTING`-conditional block below it.

`UPLOAD_STAGING_DIR` itself is deliberately **not** `set_env`'d — only
created as a directory. `app.py`'s own built-in default already matches
`$STATE_DIR/uploads` exactly, so there's nothing to reconcile; this mirrors
how several other optional config values (e.g. `DESC_LLM_BASE_URL`) are left
for the operator to override in the generated file rather than being forced
in explicitly.

## `config/switchboard.env.example`

Added a new "Folder-upload wizard" section documenting `UPLOAD_STAGING_DIR`,
`UPLOAD_MAX_BYTES` (both introduced by cycle 1, never documented there — a
gap this cycle's dispatch explicitly asked to close), the new
`UPLOAD_STAGING_TTL_SECONDS`, and `NEW_PROJECT_FROM_UPLOAD_SCRIPT`. See
"Deviations" below for why `UPLOAD_MAX_ENTRIES` is documented as a
**comment only**, not a real `KEY=value` line.

## `README.md` / `docs/ARCHITECTURE.md`

- `README.md`: one new bullet under "What you get", between the VS Code
  bullet and the git-hosting "+ New project" bullet, describing the wizard
  and explicitly noting it doesn't need `--with-git-hosting`.
- `docs/ARCHITECTURE.md`: updated the first privilege-boundary bullet to
  mention the new script (rather than continuing to claim `SVC_USER` can do
  "exactly three privileged things", which was already slightly imprecise
  before this cycle — the git-hosting script existed too, just conditionally
  — and would have been flatly wrong immediately below where this cycle adds
  a second always-installed sudo grant); added a new bullet specifically
  about the upload wizard's hand-off script and its unconditional sudoers
  placement; added a new bullet under "In-memory state and its one sharp
  edge" explaining that upload staging deliberately outlives a single
  request between phase 1 and phase 2, with the TTL/confirm-cleanup story
  rather than an always-cleanup-in-`finally` one.

## Testing

Per this role's "not testable is a claim to verify" discipline, each new
piece was tested by actually trying the cheapest thing that could work,
rather than being declared untestable:

### `tests/test_upload.py` (extended — 73 tests total now, up from 32)

- `DeriveProjectNameTests` — `_derive_project_name` directly (passthrough,
  disallowed-character stripping, leading-non-alnum stripping, 60-char
  truncation, empty/all-disallowed-input fallback).
- `CreateProjectsFromSelectionTests` — `create_projects_from_selection`
  directly against real staged directory trees, with
  `_register_via_privileged_script` monkeypatched to a fake that copies
  without `sudo` (the real script itself is covered separately, see below).
  Covers: single mode; monorepo split (root + selected nested, with the
  duplication actually verified on disk); monorepo split with zero selected
  (equivalent to single, not an error); folder-of-subrepos split (only
  selected registered, `skipped` count correct); folder-of-subrepos split
  with zero selected (rejected); a stale/tampered `selected` path (rejected,
  fresh-walk-validated); a name collision against an existing project; a
  name collision between the root and a split-out subfolder; the TOCTOU
  race (one registration fails, the sibling registered earlier in the same
  call is **not** rolled back); an invalid `mode`.
- `ConfirmUploadTests` — `confirm_upload()` directly: unknown token (404),
  a malformed/path-traversal-shaped token rejected **without touching the
  filesystem** (same defensive posture as zip-slip checks — the token is
  used directly as a path component), confirm-triggered cleanup on both
  success and failure.
- `ConfirmUploadEndpointTests` — full HTTP-level tests against a real
  `ThreadingHTTPServer`, covering `docs/spec.md`'s phase-2 acceptance
  criteria end to end (upload → confirm): single-mode registration
  appearing in `instance_names()`/`/status`; split-mode skipping unselected
  folders; zero-selected rejection; expired/unknown token; stale selected
  path; collision rejection; TOTP-via-JSON-body-code (not `?code=`) 428→
  retry flow; unauthenticated confirm rejected.
- `UploadStagingTTLSweepTests` — `_reap_dead_state()`'s new sweep: an
  artificially-backdated staging directory is removed, a recent one is kept.
- `DetectStructureTests` gained the deep-`.git`-nesting regression test
  described above.

### `tests/test_new_project_from_upload.py` (new)

No existing precedent for testing this repo's shell scripts existed (no CI
config, no prior script test file) — built one, invoking the real script via
`subprocess` rather than mocking bash. Argument-validation cases (wrong arg
count, invalid name, missing source dir) need no privilege and run as-is.
The rest (`PrivilegedRegistrationTests`) run the script via real `sudo`
(gated on `sudo -n true` succeeding, skipped cleanly otherwise) with
`RUN_USER`/`PROJECTS_DIR` passed through `sudo env KEY=VAL ... bash script
...` — `env` sets them for the child process directly regardless of
`sudo`'s own environment-reset policy, which a plain inherited-environment
approach would NOT survive (see "Verification performed" below for a real
example of this exact gap surfacing during manual smoke-testing). Covers:
successful copy + chown + git-init; source left in place (not moved);
atomic `mkdir` failure on an already-existing target, with the first
registration's content proven untouched; an existing `.git` repo not being
re-initialized (pre-existing commit preserved, no "Initial import" commit
added); names with spaces/hyphens accepted; and — the one edge case that
needed real infrastructure to test properly — **git not installed at all**,
built by constructing a restricted `PATH` (symlinks to every tool the script
needs except `git`) and confirming registration still succeeds, just without
a `.git` directory.

### Verification performed (manual, beyond the automated suite)

1. Ran the full automated suite (`python3 -m unittest discover -s tests
   -v`) — 73/73 pass.
2. A Node-based harness (mirroring cycle 1's own "extract the real rendered
   `<script>` and run it under Node against a minimal DOM/fetch stub"
   technique) exercising `initReviewState`/`renderStep5`/`toggleSplitPath`/
   `proceedToConfirm`/`runConfirm`/`renderStep6` directly: confirmed the
   monorepo-vs-subrepo default-checked-state split, the duplication warning
   appearing **only** for an offered monorepo split (not for the no-root-git
   case, not when mode is "single"), the unambiguous-shape confirm-and-
   continue path, that a hostile candidate path/root name (crafted like
   `<img src=x onerror=alert(1)>/evil` — untrusted, since it originates from
   an uploaded zip's own entry names) gets HTML-escaped rather than injected,
   that split-checkbox toggling is wired by **index** into
   `splitCandidates` rather than interpolating the untrusted path string
   into an inline `onchange="..."` attribute (mirrors the existing
   `toggleExclusionGroup(i, ...)` pattern from cycle 1, for the same
   injection-avoidance reason), the zero-selected no-root-git client-side
   pre-flight block (no fetch call made), and `runConfirm`'s three response
   branches (200/428/400, including that a 428 opens the code overlay
   rather than showing an inline error, and that a 400's partial
   `registered` list is preserved and rendered).
3. **Full production-shape end-to-end smoke test**: a real `switchboard.env`
   written to `/etc/ai-dev-switchboard/` (matching exactly what `install.sh`
   would produce), a real `app.py` process, real HTTP calls (login → phase-1
   upload with a root `.git` + one nested `.git` → phase-2 confirm with
   `mode: "split"`) — through the **real, unmodified**
   `scripts/new-project-from-upload.sh` via real `sudo` (no monkeypatching).
   Confirmed: both the root and the split-out nested folder appear as
   separate `PROJECTS_DIR` entries (with the duplication physically present
   in both copies, as spec requires), both correctly `chown`'d, both show up
   in `/status`'s `instances`, and the staging directory is gone immediately
   after confirm. This is the same technique cycle 1 used for its own
   end-to-end zip-writer verification, now covering the full two-phase
   round trip.

   **A genuine near-miss during this pass, worth recording**: an earlier
   attempt at this same smoke test relied on setting `PROJECTS_DIR`/
   `RUN_USER` as inherited environment variables around a `sudo subprocess.run(...)`
   call — but `sudo` resets its child's environment by default, so those
   overrides never reached the script, which fell back to its own
   `/etc/ai-dev-switchboard/switchboard.env`-sourced (or hardcoded default)
   values instead and wrote into the **real** `/home/dev/projects/myrepo`
   and `/home/dev/projects/thing` on the dev box running this session (an
   unrelated, pre-existing directory of real projects). Caught immediately
   via `ls`/`git log` sanity checks after the run, removed with `sudo rm
   -rf` before doing anything else, confirmed removed. This was a test-
   harness mistake, not a product-code defect: `app.py`'s actual
   `_register_via_privileged_script` (like `create_project()`'s existing
   `NEW_PROJECT_SCRIPT` invocation, same established pattern) never passes
   `RUN_USER`/`PROJECTS_DIR` via environment at all — it relies entirely on
   the privileged script sourcing `/etc/ai-dev-switchboard/switchboard.env`
   itself, which is exactly what a real `install.sh`-provisioned box has.
   The corrected smoke test (writing a real, temporary
   `/etc/ai-dev-switchboard/switchboard.env` under a cleanup `trap`, matching
   what `install.sh` actually produces) is what's described above, and is
   what actually proves the real deployment shape works.

## Post-review fix: Defect 1 (retry after failed confirm always 404'd)

`docs/test-review.md`'s testing pass found a must-fix, blocking defect:
`confirm_upload()` deleted `UPLOAD_STAGING_DIR/<token>/` unconditionally in
a `finally` block, on both success **and** failure. That matched the
original spec wording ("Cleanup on confirm ... success or failure") but
broke design.md's Step 6 "Back to review" button, which lets the user tweak
their single/split selection and retry `/projects/upload/confirm` on the
same token after a failure (e.g. a name collision) — since staging was
already gone after the first failed attempt, that retry always 404'd with
"upload expired," even a moment later, forcing a full wizard restart for
something as small as fixing a name collision.

**Fix**: `confirm_upload()` (`app/app.py`) now only removes the staging
directory when `create_projects_from_selection()` returns `ok=True`. On
failure, staging is left in place so a retried confirm call on the same
token is evaluated fresh against the still-staged tree, exactly as
design.md's "Back to review" flow expects. Abandoned staging from a failed
confirm that's never retried is still cleaned up eventually by the existing
`UPLOAD_STAGING_TTL_SECONDS` sweep in `_reap_dead_state()` — no new cleanup
mechanism was added.

`docs/spec.md`'s "Cleanup on confirm" bullet (under "Two-phase protocol")
was updated to match this corrected behavior — it no longer says cleanup
happens "success or failure."

**Regression coverage** (`tests/test_upload.py`):
- `ConfirmUploadTests.test_failed_confirm_leaves_staging_in_place_for_retry`
  — renamed/re-asserted from the old (now-incorrect)
  `test_failed_confirm_still_cleans_up_staging`, which asserted the buggy
  behavior; now asserts staging survives a failed confirm.
- `ConfirmUploadTests.test_retry_on_same_token_after_failed_confirm_evaluated_fresh`
  — unit-level: first confirm call fails (stale selection), staging
  survives, a second confirm call on the same token with a different
  selection succeeds.
- `ConfirmUploadEndpointTests.test_retry_after_failed_confirm_evaluated_fresh_not_expired`
  — full HTTP-level reproduction of the reviewer's exact repro: upload a
  zip whose root name already collides with an existing project, confirm
  fails (400, not 404), staging still present, a "Back to review"-style
  retry with `mode: "split"` on the same token is evaluated fresh (still
  400 on the same real collision, but never a spurious 404), then clearing
  the collision and retrying a third time on the same token succeeds.

Full suite re-run after the fix: `python3 -m unittest discover -s tests -v`
→ **75/75 pass** (73 from before this fix, plus the 2 new regression tests
added here — one existing test was renamed/re-asserted in place rather than
added, so the net new count is 2).

## Deviations from spec / design

- **`UPLOAD_MAX_ENTRIES` documented as a comment, not a settable
  `KEY=value` line**, in `config/switchboard.env.example`. The dispatch
  asked for it to be documented there; it's genuinely a hardcoded Python
  constant (`UPLOAD_MAX_ENTRIES = 20000` in `app.py`, never read from
  `os.environ`) — a deliberate choice cycle 1 already made and documented
  ("not exposed as its own switchboard.env knob"). Presenting it as a real
  `UPLOAD_MAX_ENTRIES=20000` line in the example env file would mislead an
  operator into thinking setting it there has an effect, when it wouldn't.
  Resolved by documenting its existence/value as a comment only — satisfies
  "document it" without the misleading implication. Flagging this
  explicitly since it's a case where the literal dispatch wording and the
  actual code shape didn't quite line up, and I judged making it look
  falsely-configurable to be worse than the small deviation.
- **Step 5's radio-choice control uses native `<input type="radio">`
  wrapped in the existing `.wizard-check-row` label pattern** (same class
  cycle 1's exclusion checklist already uses), not design.md's described
  "visually styled as pills (round background, green when selected)."
  Functionally equivalent (44px+ touch target already guaranteed by that
  existing class, keyboard-accessible, a `<fieldset>`/`<legend>` wraps the
  choice per design's accessibility notes) and reuses an existing CSS class
  outright rather than adding a new one — judged as the smaller, more
  in-keeping-with-the-file's-existing-patterns diff. The pixel-level pill
  styling was skipped as a visual-only difference with no functional or
  accessibility impact.
- **Step 5's "Back" button always resets the wizard to step 1** (labeled
  "‹ Back", matching design's wireframe), rather than attempting to "go
  back" to a meaningful intermediate state. design.md's wireframe shows a
  literal "[ Back < ]" button on the ambiguous sub-cases, but by the time
  step 5 is reached the upload has already completed (a token already
  exists server-side) — there's no cheap, meaningful "undo" back to step 4
  short of a full restart, so this button restarts the wizard. The
  abandoned token is cleaned up by the existing TTL sweep, same as closing
  the wizard outright.
- **Step 5's "Back" button is shown even for the unambiguous sub-case**
  (design's wireframe for sub-case A shows only "[ Confirm > ]", no Back).
  Kept for consistency/simplicity across all three sub-cases rather than
  conditionally omitting it — harmless (it's just "start over"), and
  avoids a step-5-specific conditional purely for one button's visibility.

## Known limitations

- Same browser-level testing gap cycle 1 already documented and this cycle
  inherits: no headless-browser harness exists in this repo (adding one,
  e.g. Playwright, would be a new dependency well beyond this feature's
  scope). What's covered instead: every new pure/mockable piece of step 5/6
  client logic exercised directly under Node against its actual extracted
  source (see "Verification performed" above), plus full end-to-end
  verification of the real HTTP+privileged-script round trip. What's still
  **not** covered by an automated test: the DOM-wiring glue itself
  (`renderWizard`'s `innerHTML` assignments, the actual click/change event
  dispatch a real browser would perform) — verified by code review and
  by tracing the call graph, not by driving a real browser. A manual
  click-through in an actual browser (see "How to verify locally" below) is
  recommended before treating the whole six-step wizard as verified from an
  actual user's perspective.
- `create_projects_from_selection`'s collision check and
  `_register_via_privileged_script` calls are not thread-safe against two
  *concurrent* confirm calls racing each other in-process (only the
  documented-as-intentional TOCTOU race against an *external* actor, e.g. a
  manually-created directory, is handled — via the privileged script's own
  atomic `mkdir`). Two simultaneous confirm calls from the same browser
  session (which the UI doesn't allow, since the wizard is a single modal)
  registering the same name is not specifically guarded against beyond what
  the atomic `mkdir` already provides per-invocation; not called out as a
  scenario in spec, and the existing `NEW_PROJECT_SCRIPT`/`create_project()`
  flow has the identical characteristic already.

## How to verify locally

Backend + script tests (all 75, after the post-review fix above):
```bash
cd /home/dev/projects/ai-dev-switchboard
python3 -m unittest discover -s tests -v
```

The privileged-script tests (`tests/test_new_project_from_upload.py`'s
`PrivilegedRegistrationTests`) need passwordless `sudo` to exercise the real
`mkdir`/`cp`/`chown`/`su` chain — they skip cleanly (not fail) if
unavailable.

Manual end-to-end (recommended before treating this feature as fully done):
```bash
cd /home/dev/projects/ai-dev-switchboard
TOTP_SECRET=$(python3 -c "import base64,os; print(base64.b32encode(os.urandom(10)).decode())") \
AUTH_MODE=simple SIMPLE_USERNAME=admin SIMPLE_PASSWORD=change-me \
PROJECTS_DIR=/tmp/switchboard-dev-projects \
UPLOAD_STAGING_DIR=/tmp/switchboard-dev-uploads \
NEW_PROJECT_FROM_UPLOAD_SCRIPT=$PWD/scripts/new-project-from-upload.sh \
python3 app/app.py
```
Then open `http://127.0.0.1:8333`, sign in, click "Upload folder / .zip",
walk through the full six-step wizard (Pick → Exclude → Zip → Upload →
Review → Confirm) against a real local folder that has a root `.git` plus at
least one nested/vendored `.git` (to exercise the split UI and duplication
warning). Note the privileged hand-off will use its own default `RUN_USER`/
`PROJECTS_DIR` (`dev`/`/home/dev/projects`) unless
`/etc/ai-dev-switchboard/switchboard.env` exists and is sourced by the
script — for a fully isolated local test, write a temporary
`/etc/ai-dev-switchboard/switchboard.env` pointing `PROJECTS_DIR` at the
same `/tmp/switchboard-dev-projects` path above (and remove it afterward),
exactly as this cycle's own end-to-end smoke test did (see "Verification
performed" above) — otherwise the script will register real projects under
the real `/home/dev/projects`.
