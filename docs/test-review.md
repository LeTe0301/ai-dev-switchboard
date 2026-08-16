# Test & Review: Dedicated team chat page (`GET /team/<project>`) — Taiga #10

## Scope
Re-review pass over `docs/spec.md` + `docs/design.md` as implemented per
`docs/implementation.md`, branch `feature/ad-10/team-chat-page`. This is a
follow-up to the prior cycle's **Blocked** verdict (Defect 1: unconditional
top-level `location.pathname.match(...)` router call broke 55 tests across 5
sibling frontend test files that had no `location` stub in their sandboxes).
The developer's fix — adding the identical `location: { pathname: '/', href:
'' }` stub (matching `tests/test_team_frontend.js`'s own shape) to each of
`tests/test_smoke_check_frontend.js`, `tests/test_clone_frontend.js`,
`tests/test_deploy_frontend.js`, `tests/test_singleton_toggle_frontend.js`,
`tests/test_upload_frontend.js` — was re-verified for real this session, and
the independent review pass (not reached last cycle, since a blocked testing
pass always skips review) was performed in full.

**Overall result: APPROVE.**

## Re-verification of Defect 1's fix

Diffed all 5 previously-broken files against `main`: each received exactly
the same 6-line addition (a `location` stub with an explanatory comment),
scoped to that file's own sandbox-construction helper — matching the prior
review's own "suggested fix direction" verbatim, and consistent with this
diff's established pattern (Deviation 4's `classList` precedent) of scoping
test-harness changes to the file that needs them.

Ran all 6 frontend files for real, this session (`/usr/lib/code-server/lib/node`,
same interpreter used previously):

```
tests/test_team_frontend.js              → ALL PASS (134/134)
tests/test_smoke_check_frontend.js       → ALL PASS (11/11)
tests/test_clone_frontend.js             → ALL PASS (8/8)
tests/test_deploy_frontend.js            → ALL PASS (9/9)
tests/test_singleton_toggle_frontend.js  → ALL PASS (19/19)
tests/test_upload_frontend.js            → ALL PASS (8/8)
```

All six exactly match `docs/implementation.md`'s claimed counts. `node --check`
on all 6 files: syntax OK. Defect 1 is genuinely resolved — no `location is
not defined` errors, no other regressions surfaced.

## Test cases (spec acceptance criteria)

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Running team on `/team/<project>` renders status strip, escalation panel, interject box, add-member control, event feed | Automated (existing, retargeted) | pass | `tests/test_team_frontend.js` — ~90 pre-existing sub-renderer tests, now driven via `renderTeamPageBody()`, PASS |
| 2 | Dashboard row shows only compact badge + link, for every status (idle/running/blocked/finished/error) | Automated (new) | pass | 7 "dashboard row: ..." tests — PASS |
| 3 | Idle project's "Open team chat" link renders the full idle launcher, not blank/read-only | Automated (new) | pass | "renderTeamPage(): idle project renders the full idle launcher..." — PASS |
| 4 | `/team/<project>` unauthenticated behaves like `/` (login overlay, no team data) | Automated (both layers) | pass | JS: "a 401 from /status shows the login overlay..." — PASS. HTTP: `test_team_page_returns_the_same_static_shell_as_root_unauthenticated` / `test_team_page_shell_matches_render_page_directly` — PASS |
| 5 | Unknown project → clear "Unknown project" message + link back, no JS error | Automated | pass | JS "unknown project renders a clear..." — PASS. HTTP `test_team_page_works_for_a_nonexistent_project_name_too` — PASS |
| 6 | Interject/escalation/add-member on the dedicated page call the exact same existing routes | Automated + spy proof | pass | Retargeted dispatch-assertion tests PASS; spy test proves `renderTeamPage()` calls the same `renderTeamStatusStrip` function object, not a forked copy — PASS |
| 7 | `tests/test_team_frontend.js` + `tests/test_team_routes.py` all pass | Automated | pass | 134/134 and 6/6, re-run this session |
| 8 | **Regression: sibling frontend test suites unaffected** | Automated | **pass (was FAIL)** | Defect 1 fix verified — all 5 previously-broken files now 100% passing, re-run this session |
| 9 | Full `tests.test_team_routes` suite vs. `main` baseline — same pre-existing failure set | Automated | pass | Re-established independently this session (see below): 45 errors/2 failures on both branch and `main`, `diff` of sorted FAIL/ERROR test names is byte-identical (empty diff) |
| 10-15 | Deviations 1-4, unauthenticated-leak check, grounding/branches reachability | Manual (carried over) | pass | Unchanged from prior cycle's manual trace/revert-and-watch-it-fail verification (code these checks cover is untouched by the Defect-1 fix) — re-confirmed the relevant code is unchanged in this cycle's diff |

## Regression check

```
NODE=/usr/lib/code-server/lib/node
"$NODE" tests/test_team_frontend.js              → ALL PASS (134/134)
"$NODE" tests/test_smoke_check_frontend.js       → ALL PASS (11/11)
"$NODE" tests/test_clone_frontend.js             → ALL PASS (8/8)
"$NODE" tests/test_deploy_frontend.js            → ALL PASS (9/9)
"$NODE" tests/test_singleton_toggle_frontend.js  → ALL PASS (19/19)
"$NODE" tests/test_upload_frontend.js            → ALL PASS (8/8)

TOTP_SECRET=JBSWY3DPEHPK3PXP python3 -m unittest tests.test_team_routes.TeamPageRouteTests -v
  → Ran 6 tests ... OK

TOTP_SECRET=JBSWY3DPEHPK3PXP python3 -m unittest tests.test_team_routes -v
  → Ran 137 tests in 9.701s, FAILED (failures=2, errors=45)

# main baseline (git stash push -u, re-run, git stash pop):
TOTP_SECRET=JBSWY3DPEHPK3PXP python3 -m unittest tests.test_team_routes -v
  → Ran 131 tests in 9.140s, FAILED (failures=2, errors=45)
  # sorted FAIL/ERROR test-name sets, branch vs. main: `diff` exit 0 (empty)
  # — the branch's 6 extra passing tests are TeamPageRouteTests (new); every
  # other failure/error is identical by test name to main, confirmed fresh
  # this session (not just re-trusting the prior cycle's claim).

python3 -m py_compile app/app.py → compiles cleanly.
node --check on all 6 frontend test files → syntax OK.
```

No other Python test file (`test_clone.py`, `test_deploy_dispatch.py`,
`test_gitea*.py`, `test_upload.py`, `test_host_control.py`,
`test_install_*.py`, etc.) references `render_page()`, `PAGE_TEMPLATE`, or
the `/` route directly (confirmed via grep) — the diff's backend surface
touched is `do_GET` (one additive `or` branch) and `PAGE_TEMPLATE` (new ids
+ CSS + a new empty `<div id="team-page">`), none of which any of those
suites exercise; no reason to expect or find collateral breakage there, and
this project has no separate lint/type-check step (no `package.json`,
`.eslintrc*`, `pyproject.toml`, or `setup.cfg` present).

**Testing pass: clean.** Proceeding to the independent review pass.

## Review pass

### Spec-to-code traceability
Re-read `docs/spec.md`'s Goals, Proposed approach §1-5, Edge cases, and all
7 acceptance-criteria checkboxes against the actual `app/app.py` diff
(`git diff main -- app/app.py`, full hunk-by-hunk read this session, not
re-trusted from the prior cycle):
- New `do_GET` branch (`self.path == "/" or _TEAM_PAGE_PATH_RE.match(self.path)`)
  matches spec §1 exactly — same static shell, no project-name validation at
  this layer, `re` already imported (no new import needed, contrary to the
  spec's own hedge that one might be needed).
- Bottom-of-script router matches spec §2's sketch verbatim, including the
  `decodeURIComponent` on the matched path segment.
- `renderTeamPageBody()` (the extraction of the old `teamRow()`) is the
  single implementation both `renderTeamPage()` and — indirectly, since the
  dashboard's new `teamRow()` no longer calls it at all — no duplicate
  exists; grepped for a second definition of any of the sub-renderer names
  spec §3 lists (`renderTeamStatusStrip`, `renderEscalationPanel`, etc.) —
  none found, single implementation confirmed by absence of a fork, not
  merely spy-tested.
- Dashboard's new compact `teamRow()` (`TEAM_STATUS_LABELS` + status badge +
  link) matches spec §4 and `docs/design.md`'s own sketch line-for-line
  (including that neither the spec nor the implementation adds a
  `.team-status.status-idle` CSS color rule — idle intentionally renders in
  the base `.team-status` color, matching design's own sketch, not an
  oversight).
- `#team-page` container, `hideDashboardChromeForTeamPage()`, and the new
  CSS rules match spec §5 / design's "Page Container Styling" section.
- All 7 acceptance-criteria checkboxes have a corresponding test case in the
  table above — no gaps found.

### Correctness review
- `_TEAM_PAGE_PATH_RE = re.compile(r"^/team/[^/]+/?$")` correctly requires at
  least one non-slash character (rejects bare `/team` and `/team/`, both
  exercised by `test_paths_that_merely_resemble_the_team_route_do_not_match`)
  and rejects further path segments (`/team/x/y` doesn't match, since
  `[^/]+/?$` only allows one optional trailing slash) — consistent with the
  spec's "one path segment" assumption.
- `refreshCurrentView()`/`TEAM_PAGE_PROJECT` (the developer's self-flagged
  beyond-spec addition) is a straightforward null-check dispatcher; every
  call site converted from `refresh()` was traced in the prior cycle and
  re-confirmed unchanged in this cycle's diff — no new call sites introduced
  by the Defect-1 fix itself (the fix touched only test files).
- `renderTeamPage()` correctly re-fetches `/status` on every call (each 4s
  tick and on initial load) rather than reusing stale dashboard state — same
  pattern `refresh()` already uses.
- No off-by-one or state-leak issues found in the `teamPageMatch` regex
  handling, `TEAM_STATUS_LABELS` fallback (`|| 'Unknown'`, defensive against
  an unrecognized status value), or `hideDashboardChromeForTeamPage()`'s
  direct `getElementById` calls (all target ids that were verifiably added
  to `PAGE_TEMPLATE` in this same diff).

### Security review
- `esc()` is applied to every piece of user/server-controlled string
  interpolated into the new HTML (`teamRow()`'s `status` value,
  `teamPageHeader()`'s `name`, `renderTeamPageNotFound()`'s `projectName`) —
  no unescaped interpolation found in the new code.
- The new `do_GET` branch serves a byte-identical, session-free static shell
  regardless of the URL's project-name segment — verified at the HTTP level
  this session (`test_team_page_shell_matches_render_page_directly` compares
  against `render_page()` directly) — no path-traversal or filesystem access
  keyed off the URL segment; every other GET route remains gated by
  `_authed()` (`do_GET`'s existing code, unchanged by this diff).
- No new secrets, credentials, or logging of sensitive data introduced.

### Simplicity / scope review
- The diff stays inside the existing single-file convention (no new build
  tooling, no new dependency), matches the spec's explicit non-goals (no new
  backend routes, no `app/teams.py` changes, no event-envelope changes) —
  confirmed via `git diff --stat main` showing changes confined to
  `app/app.py` and test files.
- `refreshCurrentView()` is flagged by the developer as beyond the spec's
  literal sketch; on inspection it's a minimal, necessary fix for a real gap
  (stale team page on any handler-triggered re-render) rather than
  speculative generality — a single 4-line function, not a new abstraction
  layer. Not scope creep.
- The 5-file test-fixture fix for Defect 1 is the minimal correct diff: one
  stub line + a comment per file, no shared helper module invented, matching
  the codebase's existing per-file test-fixture convention.
- `.aider.chat.history.md` is an untracked, incidental local file (8 lines,
  just aider startup banner text, no secrets) — not part of this diff, not
  staged, no action needed.

### Findings
No must-fix or should-fix findings. One optional observation, not a
blocker:

- **Nit:** `docs/spec.md`'s "Proposed approach" §1 hedges that `re` "if not
  [already imported], this is the one new import this whole feature
  requires" — the implementation found `re` was already imported and needed
  no new import. This is already correctly documented in
  `docs/implementation.md`'s "Changes by file" section; no action needed,
  noting only for completeness.

## Overall verdict
**Approve.** Defect 1 is genuinely and completely resolved — all 6 frontend
test files pass at their claimed counts (134/134, 11/11, 8/8, 9/9, 19/19,
8/8), re-run for real this session, and the Python suite's pre-existing
failure set (45 errors/2 failures) was independently re-established against
a fresh `main` baseline this session and matches byte-for-byte by test name.
The independent review pass — spec-to-code traceability across all 7
acceptance criteria, correctness, security, and simplicity/scope — turned up
no must-fix or should-fix issues against the actual diff. Hands back to the
orchestrator for commit + PR.

## Post-approval fix-up round (independent `/code-review` finding, commit
`ed6f934`)

**Scope of this round.** After the approval above, a separate `/code-review`
pass (outside this pipeline) caught a real spec violation the testing and
review passes above did not exercise: `renderTeamPage()`'s 401 branch called
only `showOverlay()`, never `hideDashboardChromeForTeamPage()` — so a
bookmarked/shared `/team/<project>` link opened with no valid session (fresh
browser, expired cookie) left the dashboard's project-creation chrome
(title, "+ New project" row, upload/clone buttons) visible behind the
translucent login overlay, contradicting `docs/spec.md` §5 ("only the
login/TOTP overlays are shared between both contexts"). This is a narrow,
single-issue fix-up review, not a full re-review of the whole feature — the
rest of the feature was already verified clean above and is unaffected by
this one-line change.

**Fix verified for real, this session**, against commit `ed6f934` checked
out directly (`app/app.py` line 5865: `if (r.status === 401) {
hideDashboardChromeForTeamPage(); showOverlay(); return; }`, matching every
other exit path's ordering — `renderTeamPageNotFound()` and the
found-project path both already called `hideDashboardChromeForTeamPage()`
before this fix):

- `tests/test_team_frontend.js` → **ALL PASS (135/135)**, run fresh this
  session (was 134; +1 for the new regression test
  `renderTeamPage(): a 401 from /status also hides the dashboard chrome, not
  just the overlay`).
- **Revert-and-watch-it-fail, performed independently this session** (not
  reused from the developer's own claim): temporarily reverted just the
  one-line `app/app.py` fix back to `if (r.status === 401) { showOverlay();
  return; }` and re-ran the file — exactly 1 test failed
  (`renderTeamPage(): a 401 from /status also hides the dashboard chrome,
  not just the overlay`, `1/135 test(s) FAILED`), all 134 others still
  passed. Restored the fix (`git diff` empty afterward, confirming a clean
  revert) and re-ran — back to 135/135. Confirms the new test is a genuine,
  non-tautological regression test for this exact fix.
- The 5 sibling frontend files, unaffected by this change, re-run to
  confirm no regression: `test_smoke_check_frontend.js` 11/11,
  `test_clone_frontend.js` 8/8, `test_deploy_frontend.js` 9/9,
  `test_singleton_toggle_frontend.js` 19/19, `test_upload_frontend.js` 8/8
  — all match the documented baseline exactly.
- `tests.test_team_routes.TeamPageRouteTests` → 6/6 `OK` (this class only
  covers the unauthenticated static shell, not the client-side 401 branch,
  so it needed no new case — correctly unaffected).
- Full `tests.test_team_routes` suite → `Ran 137 tests ... FAILED
  (failures=2, errors=45)`, same 2 flaky CLI-timing `FAIL`s
  (`test_orphan_check_firing_once_does_not_permanently_disrupt_a_live_cli_run`,
  `test_cli_team_start_with_explicit_tier3_lead_succeeds_unaffected_by_web_default_refusal`)
  and same 45 environmental `ERROR`s (git-identity-less sandbox), byte-for-byte
  identical to the baseline already documented above — zero new regressions.
- `python3 -m py_compile app/app.py` and `node --check
  tests/test_team_frontend.js` — both clean.

**Independent review of the diff itself** (`app/app.py` +1/-1 line,
`tests/test_team_frontend.js` +31 lines, `docs/implementation.md` doc-only):
`hideDashboardChromeForTeamPage()` only sets `style.display` on independent
elements and toggles `#rows`/`#team-page` classes; `showOverlay()` only
touches `#overlay`/`#err-creds`/`#login-pass` — the two functions share no
target elements, so the ordering is provably inert either way and the fix
introduces no new risk. The diff is minimal and scoped exactly to the
reported finding — no unrelated changes, no scope creep. No security
concerns (no new user input, no new interpolation). Confirmed all 7
acceptance criteria from the original pass still hold; this fix closes a gap
in edge-case coverage for the "Unauthenticated access to `/team/<project>`"
edge case (`docs/spec.md` "Edge cases") that neither the original
implementation nor the original testing pass had exercised at this level of
detail (the original 401 test only asserted the overlay showed, not that
dashboard chrome was hidden).

### Findings
None. No must-fix, should-fix, or nit issues found in this fix-up round.

### Verdict for this round
**Approve.** The fix is real, minimal, and correctly verified: the new
regression test was independently confirmed non-tautological via
revert-and-watch-it-fail (fails pre-fix, passes post-fix), the full 6-file
frontend suite plus the Python route suite show zero regressions against
the already-documented baseline, and the diff itself is small enough that a
line-by-line read found no further issues. The original approval above
stands; this round supersedes it only insofar as it closes the one gap the
independent `/code-review` pass found. Hands control back to the
orchestrator for merge/PR — no further build cycle needed for Taiga #10.
