# Test & Review: Folder upload → auto-detect repo(s)

**Revision note**: this revises the previous `docs/test-review.md`, which
blocked on one must-fix defect (Defect 1: "Back to review" retry after any
failed confirm always 404'd with "upload expired"). The developer's fix has
now been re-tested hands-on (both the original repro and fresh boundary
probes) and, that coming back clean, the review pass — not reached last
time — has now also been completed. This file replaces the previous one in
full; rows from the original 25-row table that are still accurate are kept
as-is, row 1 and row 16 are updated to reflect the fix, and new rows are
added for the fix-specific re-verification.

## Scope
Both build cycles (`app/app.py`, `README.md`,
`config/switchboard.env.example`, `docs/ARCHITECTURE.md`, `install.sh`,
`scripts/new-project-from-upload.sh`, `tests/`) plus the post-review fix to
`confirm_upload()` (`app/app.py`) and its corresponding `docs/spec.md`
wording update, tested against all 19 acceptance criteria in `docs/spec.md`
and, specifically for this pass, the original Defect 1 repro plus fresh
TTL-sweep boundary probes the fix's own new behavior needed re-checked
(success still cleans up promptly; a failed-and-abandoned confirm is still
eventually caught by the TTL backstop; a freshly-staged, never-confirmed
upload is not swept early).

All testing below was performed hands-on this session: the full automated
suite re-run for real, a live re-execution of the exact original Defect 1
repro over a real HTTP socket against the actual current `app.py` module
(not the existing test file, and not simulated), a revert-and-watch-it-fail
check proving the three new regression tests actually exercise the fix (not
passing vacuously), and a direct reading of the diff for the review pass.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `python3 -m unittest discover -s tests -v` (full suite) | automated | pass | **75/75 pass**, 0 failures/errors/skips (up from 73/73 in the previous pass — 2 net-new regression tests for the fix; one prior test was renamed/re-asserted in place) |
| 1b | **Fix re-verification**: revert `confirm_upload()`'s cleanup-on-failure fix back to the old unconditional `try/finally shutil.rmtree`, re-run the 3 new/changed regression tests | automated (manual revert + rerun, then restored) | pass (all 3 fail against the reverted code, confirming they genuinely exercise the fix) | `test_failed_confirm_leaves_staging_in_place_for_retry`, `test_retry_on_same_token_after_failed_confirm_evaluated_fresh`, `test_retry_after_failed_confirm_evaluated_fresh_not_expired` all FAIL with `AssertionError: False is not true` against the pre-fix code; fix restored, `git diff` confirmed clean, full suite re-confirmed 75/75 |
| 1c | **Original Defect 1 repro, re-executed live over real HTTP** (upload → confirm with colliding name → fails → confirm again on same token with **different** mode/selected → must not 404) | manual, real `ThreadingHTTPServer` socket, real `urllib` HTTP requests against the actual current `app.py` module (privileged-script boundary monkeypatched only to avoid writing into the real `/home/dev/projects` — no `/etc/ai-dev-switchboard/switchboard.env` exists on this box, confirmed via `test -f`, so the real script would otherwise fall back to that real path; the real script's own end-to-end behavior was already proven with real `sudo` in the previous pass and this cycle's own dev verification, not re-proven here) | pass | first confirm: 400 `{"error":"name collision: myrepo","registered":[]}`, staging still present; retry with `mode:"split"` on the **same token**: 400 (evaluated fresh, real collision) — **not** 404; staging still present after both failures; clearing the collision and confirming a third time on the same token: 200, `registered:["myrepo"]`, staging removed immediately, `myrepo` appears in `/status` |
| 1d | **Boundary: success still cleans up staging promptly** | manual, same live harness | pass | staging directory verified gone via `os.path.isdir()` immediately after the successful (3rd) confirm call above, no TTL wait needed |
| 1e | **Boundary: TTL sweep still catches an abandoned, failed-and-never-retried confirm** | manual, same live harness, `UPLOAD_STAGING_TTL_SECONDS=3` | pass | a second colliding upload's staging directory survived its failed confirm, then — abandoned (never retried) — was swept by `_reap_dead_state()` (triggered via a real `/status` poll) once past the 3s TTL; a subsequent confirm on that now-swept token returned 404 |
| 1f | **Boundary: a freshly-staged, never-confirmed upload is not swept early** | manual, same live harness | pass | a brand-new staged upload survived an immediate `/status` poll (which triggers the sweep) and still confirmed successfully afterward — confirms the TTL sweep's mtime-based cutoff isn't accidentally over-eager as a side effect of the fix |
| 2 | Exclusion checklist: pre-checked, grouped by basename across depths, `.git` never a candidate | manual (Node harness against real served JS, previous pass) | pass (unaffected by this fix; not re-run this pass, code unchanged) | previous pass |
| 3 | Unchecking an exclusion row includes those files in the zip | code review (previous pass) | pass (unaffected, unchanged) | previous pass |
| 4 | Zipping progress indicator advances monotonically to 100% | manual (previous pass) | pass (unaffected, unchanged) | previous pass |
| 5 | Client-side zip writer produces a genuinely valid `.zip` | manual, real tools (previous pass) | pass (unaffected, unchanged) | previous pass |
| 6 | Upload progress independent of zip progress | code review (previous pass) | pass (unaffected, unchanged) | previous pass |
| 7 | Picking a `.zip` directly skips exclusion+zip steps | manual e2e (previous pass) | pass (unaffected, unchanged) | previous pass |
| 8 | Phase 1 success → review step data, nothing registered yet | manual, real HTTP (previous pass) | pass (unaffected, unchanged) | previous pass |
| 9 | "single" mode confirm → exactly one project registered | automated (previous pass, still in 75/75) | pass | test suite |
| 10 | root-`.git` + nested split → root AND nested both registered, duplication present | manual, real HTTP + real privileged script (previous pass) | pass (unaffected, unchanged) | previous pass |
| 11 | no-root-`.git` + subset selected → only selected registered | automated + manual (previous pass, still in 75/75) | pass | test suite |
| 12 | no-root-`.git` split, zero selected → rejected | manual, real HTTP (previous pass) | pass (unaffected, unchanged) | previous pass |
| 13 | confirm called after TTL → 404, nothing registered | manual, real HTTP + real TTL sweep (previous pass); **re-confirmed fresh this pass** via 1e/1f above with the fix in place | pass | this pass's 1e/1f rows |
| 14 | Registered project without its own `.git` gets `git init` + one commit | automated + manual (previous pass) | pass (unaffected, unchanged) | previous pass |
| 15 | Zip-slip / oversized `Content-Length` / uncompressed-over-cap zip → rejected before extraction | manual, real malicious payloads (previous pass) | pass (unaffected, unchanged) | previous pass |
| 16 | Name collision → whole confirm rejected up front, collision(s) named, nothing registered; **and** the natural "Back to review" recovery path from this error now works | manual + automated (previous pass for the rejection itself; **this pass's rows 1c-1f for the retry/recovery path, now fixed**) | **pass** (previously "pass, but see Defect 1" — the retry gap is now closed) | this pass's rows 1c-1f |
| 17 | TOTP gating: phase 1 428→prompt→retry via `?code=`, phase 2 standard JSON-body `code` | manual + automated (previous pass) | pass (unaffected, unchanged) | previous pass |
| 18 | Stale/tampered `selected` path rejected | automated (previous pass, still in 75/75) | pass | test suite |
| 19 | TOCTOU race: one project's registration fails atomically, siblings not rolled back | automated (previous pass, still in 75/75) | pass | test suite |
| 20 | Non-zip/corrupt upload → 400, no staging left behind | automated (previous pass, still in 75/75) | pass | test suite |
| 21 | Feature works fully without `--with-git-hosting` | manual, real e2e (previous pass) | pass (unaffected, unchanged) | previous pass |
| 22 | Successful confirm → appears in `/status`'s `instances` | manual, real HTTP (previous pass); re-confirmed in row 1c above | pass | previous pass + row 1c |
| 23 | Review step defaults (monorepo unchecked, no-root-git checked) | manual (previous pass) | pass (unaffected, unchanged) | previous pass |
| 24 | Untrusted candidate path HTML-escaped, checkbox wired by index | manual (previous pass) | pass (unaffected, unchanged) | previous pass |
| 25 | Zero-selected no-root-`.git` split blocked client-side | manual (previous pass) | pass (unaffected, unchanged) | previous pass |

## Regression check
Full suite re-run for real this pass: `python3 -m unittest discover -s
tests -v` → **75/75 pass**, 0 failures/errors/skips (passwordless `sudo`
available in this sandbox, so `PrivilegedRegistrationTests` ran for real,
not skipped). `python3 -m py_compile app/app.py` (verified via
`ast.parse` after a pre-existing root-owned `__pycache__` file blocked a
direct `py_compile` write — unrelated to this diff, a leftover from an
earlier `sudo`-run test in this same sandbox) and `bash -n
scripts/new-project-from-upload.sh` both confirm no syntax errors. `git
diff --stat` confirms the tracked-file diff is `app/app.py`, `README.md`,
`config/switchboard.env.example`, `docs/ARCHITECTURE.md`, `install.sh` only
— no other part of the app (session/auth, engine start/stop,
`/projects/new`) touched. The fix itself is a small, isolated change to
`confirm_upload()`'s cleanup ordering (moved from an unconditional
`try/finally shutil.rmtree` to `shutil.rmtree` only on the `ok=True` path) —
verified via direct reading of the current source, matching
`docs/implementation.md`'s own description of the change exactly.

## Defects found
None — the previous pass's Defect 1 is confirmed fixed (rows 1b-1f, 16
above). No new defects surfaced during this pass's testing.

---

## Spec coverage
All 19 acceptance criteria in `docs/spec.md` are implemented and covered by
at least one test, per the table above (18 unaffected by this fix and
already verified in the previous pass, 1 — the collision/rejection
criterion — now additionally covers the "Back to review" retry path that
`docs/design.md`'s Step 6 promises but the acceptance criteria list itself
doesn't separately enumerate as its own bullet). No gaps found.

## Findings (most severe first)

### 1. `docs/ARCHITECTURE.md` still describes the pre-fix cleanup behavior — should-fix
- File: `docs/ARCHITECTURE.md:80-81`
- Issue: the "Upload staging" bullet under "In-memory state and its one
  sharp edge" still reads *"confirm removes its own staging directory the
  moment it finishes (success **or** failure)"* — this is the exact
  behavior Defect 1 identified as broken and the developer fixed. `docs/
  spec.md`'s parallel "Cleanup on confirm" bullet was correctly updated to
  say cleanup happens **only on success**, with failure deliberately
  leaving staging in place for retry — but this near-duplicate description
  in `ARCHITECTURE.md` was missed.
- Failure scenario: this paragraph's own stated purpose is "a future reader
  finding a `UPLOAD_STAGING_DIR/<token>/` directory that outlived its
  originating request should read this as the intended TTL/idle-cleanup
  story, not a leak." As currently worded, a future reader who reads this
  paragraph and then observes a staging directory surviving a **failed**
  confirm (the now-correct, intended behavior) would conclude the opposite
  of what the paragraph intends — that something is wrong, since the
  paragraph still claims cleanup happens on failure too. This directly
  undermines the doc's own stated goal and should be a quick one-sentence
  fix to match `docs/spec.md`'s already-corrected wording (cleanup only on
  success; failure leaves staging for the "Back to review" retry, with the
  TTL sweep as the backstop for abandoned failures).

## Follow-ups (non-blocking)
- `docs/spec.md:192` ("Any `UPLOAD_STAGING_DIR/<token>/` whose directory
  mtime is older than the TTL and **was never confirmed** is pruned") is
  now slightly imprecise post-fix: a staging directory can now also survive
  a confirm attempt that *was* made but failed, and still be swept by this
  same TTL sweep once idle long enough (verified in row 1e above). The
  sweep's actual implementation (mtime-based, no notion of "was confirmed")
  already behaves correctly regardless of this wording; this is a
  documentation-precision nit only, lower priority than Finding 1 since it
  doesn't risk being read backwards the way Finding 1 does.

## Overall verdict
**Approve with follow-ups.** The testing pass is fully clean: the original
Defect 1 repro was re-executed live against the current code and no longer
404s on retry, all three new/changed regression tests were proven to
actually exercise the fix via a revert-and-watch-it-fail check, the fresh
TTL-sweep boundary probes (success still cleans up promptly; an abandoned
failed confirm is still eventually swept; a freshly-staged upload isn't
swept early) all passed, and the full 75-test suite is green. The
independent review pass found no correctness, security, or spec-coverage
issues in the fix itself or in the surrounding diff — the change is small,
isolated, and matches its own documentation in `docs/implementation.md` and
the now-corrected `docs/spec.md`. One should-fix, non-blocking finding: 
`docs/ARCHITECTURE.md` still describes the old (bugged) cleanup-on-failure
behavior in one paragraph and should be updated to match `docs/spec.md`'s
corrected wording — worth a quick follow-up but does not block shipping
this build cycle.
