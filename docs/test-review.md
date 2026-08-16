# Test & Review: Concurrent sessions per project — part 2: "+" control and per-session list UI

## Scope
Frontend replacement of the `kind === 'inst'` checkbox with an always-on
engine picker + "+ Start session" control + per-session list (each
independently stoppable), consuming part 1's backend (`POST
/instance/<name>/spawn`, `POST /instance/<name>/session/<id>/stop`,
`/status`'s `sessions` array), plus removal of part 1's now-dead
back-compat shim (`/on`/`/off` routes, `instance_stop()`, `/status`'s
singular `on`/`engine`/`url` fields). Covers every acceptance criterion in
`docs/spec.md`, both deviations the developer flagged in
`docs/implementation.md` ("Key decisions"/"Deviations from spec"), and the
regression/test-count claims in `docs/implementation.md`'s "How to verify
locally". All commands below were run for real, in this session, against
the current uncommitted working tree (`git diff HEAD` — `app/app.py`,
`docs/design.md`, `docs/implementation.md`, `docs/spec.md`,
`tests/test_session_identity.py`, `tests/test_smoke_check_frontend.js`
modified; `tests/test_multi_session_frontend.js` new/untracked).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| AC1 | 0 sessions, ≥1 engine: picker + "+ Start session" shown, no checkbox, no session list | Automated | pass | `test_multi_session_frontend.js` "0 sessions, >=1 engine configured..." — ran, PASS |
| AC2 | 2 running sessions of different engines: both listed, own engine label + own Stop, no checkbox anywhere in row | Automated | pass | same file, "2 running sessions of different engines..." — ran, PASS; also confirmed exactly 2 `.session-stop-btn` via a count assertion |
| AC3 | Stop on session B: only B's entry disappears next refresh, A (incl. its own open-link) unchanged | Automated | pass | same file, "after Stop resolves and the next refresh()..." — ran, PASS |
| AC4 | TOTP overlay required on spawn or stop: same `pendingToggle`/code-overlay/retry flow, no duplicated implementation | Automated | pass | same file, "a 428 mid-spawn..." and "a 428 mid-stop..." (confirms `pendingSessionStop` survives the retry and targets the *same* session_id) — both ran, PASS |
| AC5 | 0 engines configured: spawn control omitted entirely | Automated | pass | same file, "0 sessions, 0 engines configured..." — ran, PASS |
| AC6 | host/taiga/gitea rows unaffected (still checkbox) | Automated + diff read | pass | same file, "Host/Taiga/Gitea rows keep their checkbox..." — ran, PASS (exactly 3 checkboxes); `git diff` confirms zero changed lines at any of the 3 non-`inst` `row()` call sites |
| AC7 | New canonical `kind='inst'` test file passes; 4 pre-existing frontend files continue to pass | Automated, actually executed | pass | see "Regression check" below — all 7 files, 183/183 tests |
| AC8 | `/on`/`/off` 404; `/status` no longer includes `on`/`engine`/`url` | Automated (Python, Tier 3 real HTTP) | pass | `tests/test_session_identity.py::test_legacy_on_route_no_longer_exists`, `::test_legacy_off_route_no_longer_exists`, `::test_status_no_longer_includes_the_removed_back_compat_fields` — ran, PASS |
| Edge: 0 sessions/0 engines | No spawn control, no session list, other rows unaffected | Automated | pass | covered by AC5's test |
| Edge: session with no URL | "starting…" placeholder, never a broken href | Automated | pass | "a session with no captured URL yet shows a 'starting…' placeholder..." — ran, PASS; explicit assertion no `<a href="null"` |
| Edge: sub text, newest has URL / newest still starting | Both variants render correctly | Automated | pass | both "sub text: running with..." tests — ran, PASS |
| Edge: dispatch body for spawn | `POST /instance/<name>/spawn` body `{engine: <selected>}` | Automated | pass | "clicking '+ Start session' dispatches..." — ran, PASS |
| Golden path (spawn → appears in /status → independent stop) | Real `ThreadingHTTPServer` + real tmux, not mocked | Automated (Tier 2/3 Python) | pass | `test_spawn_creates_a_tmux_session_and_appears_in_status`, `test_spawn_twice_yields_two_distinct_simultaneous_sessions`, `test_session_stop_route_tears_down_only_the_targeted_session` — all ran, PASS. See "Manual/browser exercise" note below for what this substitutes for. |
| `engineRow()` signature-drop deviation | Confirmed behavior-preserving, not a missed case | Manual code read + test | pass | Single call site (`grep -n "engineRow("` → only the definition and one call in `row()`); every design.md state (0/1/2/3-session, TOTP, error) has a corresponding passing test above; no dead branch reachable |
| `test_smoke_check_frontend.js` fixture fix is genuine, not masking a regression | Revert-and-watch-it-fail on the fixture-only diff | Manual, actually executed | pass | `git stash push -- tests/test_smoke_check_frontend.js && node tests/test_smoke_check_frontend.js` → exactly 3/11 FAIL (matches developer's claim); failures are `smokeCheckRow()` correctly rendering nothing because `newestUrl` is null when `inst.sessions` is absent from the stale fixture shape — confirms the fix is a legitimate fixture-shape update against the new real `/status` contract, not a masked regression |

## Regression check
Frontend, actually executed with a portable Node 20 binary (system `node` absent, confirmed):
```
node tests/test_multi_session_frontend.js    → ALL PASS (13/13)
node tests/test_smoke_check_frontend.js      → ALL PASS (11/11)
node tests/test_deploy_frontend.js           → ALL PASS (9/9)
node tests/test_singleton_toggle_frontend.js → ALL PASS (19/19)
node tests/test_team_frontend.js             → ALL PASS (115/115)
node tests/test_clone_frontend.js            → ALL PASS (8/8)
node tests/test_upload_frontend.js           → ALL PASS (8/8)
```
183/183 total, matching every number in `docs/implementation.md`'s "How to verify locally".

Backend:
- `python3 -m py_compile app/app.py` → clean.
- `python3 tests/test_session_identity.py` → `Ran 32 tests ... OK` (matches claim).
- `python3 -m unittest discover -s tests` → `Ran 1309 tests in 126.8s ... FAILED (failures=35, errors=79, skipped=42)`. Extracted the full FAIL/ERROR list and tallied by file: `test_team_routes` ×47, `test_teams_lifecycle` ×34, `test_new_project_from_url` ×12, `test_new_project_from_gitea` ×6, `test_gitea_sync_project` ×5, `test_new_project_from_upload` ×4, `test_teams_grounding` ×3, `test_teams_lead` ×2, `test_taiga_push` ×1 — exact match, same 9 files, same counts, to `docs/implementation.md`'s claim. This confirms the pre-existing-failure baseline is unchanged by this diff (independently re-run, not reused from the developer's own report).

## Manual/browser exercise
No browser-automation tool (Playwright/Puppeteer/similar) is available in
this environment, so I could not literally click through the dashboard in
a browser — noting this explicitly rather than skipping it silently, per
the role's instructions. In its place I used two techniques that together
cover the same ground, both already established in this codebase as the
project's own substitute for a browser harness (confirmed no such harness
exists — `tests/test_deploy_frontend.js`/`tests/
test_smoke_check_frontend.js`'s own comments, and `docs/implementation.md`'s
"How to verify locally" §5 similarly falls back to "needs real tmux +
TOTP_SECRET/AUTH_MODE, same as any local run" for its own manual step):
1. **Frontend**: the real `<script>` extracted verbatim from
   `app.render_page()` executed in a Node `vm` context against stubbed
   `document`/`fetch` — this is real rendering/dispatch/DOM-update logic
   running, not a description of it, exercising `toggle()`,
   `stopSession()`, `refresh()`, `handleActionResult()` exactly as a
   browser would invoke them.
2. **Backend**: `test_session_identity.py`'s Tier 2/3 tests spin up a real
   `ThreadingHTTPServer` and real `tmux` sessions (not mocked) and drive
   the actual golden path end to end: spawn via `POST
   /instance/<name>/spawn` → session appears in `/status`'s `sessions`
   array with a real session_id → `POST
   /instance/<name>/session/<id>/stop` tears down only that session,
   leaving a sibling untouched. All three ran and passed this session (see
   table above).
I deliberately did not start the actual production server against this
machine's real `PROJECTS_DIR`/engines — doing so would spawn real
tmux/engine sessions on the host outside any test sandbox, which is a
disproportionate and unnecessary risk for what the two techniques above
already verify for real.

## Deviation review (the two flagged items)
1. **`engineRow(name, on, engine)` → `engineRow(name)`**: confirmed
   behavior-preserving. `on`/`engine` were only ever read inside the now-
   deleted `if (on) { Running badge }` branch; grep confirms exactly one
   call site (`row()`'s own `kind === 'inst'` ternary), already updated to
   the new 2-arg call. Every design.md state (0-session, ≥1-engine,
   multi-session-with-picker-still-visible) has a passing test above. This
   is a real, disclosed deviation from design.md's literal wording, but not
   a missed case — I checked, not assumed.
2. **`tests/test_smoke_check_frontend.js` fixture update**: confirmed
   genuine via revert-and-watch-it-fail (see test table) — the 3
   pre-fix failures are explained exactly by `smokeCheckRow()` correctly
   gating on the new `sessions`-derived `newestUrl` instead of the removed
   `url` field, not by any behavior change in `smokeCheckRow()` itself.

## Spec coverage
All 8 acceptance criteria in `docs/spec.md` are implemented and covered by
a test that actually ran this session (see table above; AC7/AC8 map to the
"Regression check" section rather than a single row). All 6 "Edge cases"
are either directly tested or, for the two explicitly declared out of
scope by the spec itself (many-sessions pagination, rapid-double-click
dedup — spec says "not required"/"frontend does not need its own dedup
logic"), correctly left untested by design, matching the spec's own stated
position rather than an omission.

## Findings (most severe first)

### 1. Stale cross-file doc-comment referencing a function this diff deletes — nit
- File: `app/teams.py:4042`
- Issue: `stop_team()`'s own docstring says `same "an explicit human action
  always wins" precedent instance_stop() already sets` — `instance_stop()`
  is deleted by this diff (`app/app.py`), so the reference now points at
  nothing. Zero functional impact (it's a comment in an unrelated,
  untouched module), but a future reader following the reference will hit
  a dead end.
- Failure scenario: none functional — purely a documentation-accuracy nit,
  outside this diff's own "Affected areas" list, so easy to have missed.

### 2. `docs/design.md`'s stated WCAG contrast numbers are imprecise — nit
- File: `docs/design.md` (Accessibility section)
- Issue: recomputed relative-luminance contrast from the actual hex values
  used in the shipped CSS gives different numbers than design.md states,
  e.g. `.session-status`'s `#888` on `#181818` is 5.01:1 (design.md says
  4.65:1), the badge's `#4da6ff` on `#16324a` is 5.17:1 (design.md says
  6.32:1), the inactive pill's `#aaa` on `#2a2a2a` is 6.18:1 (design.md
  says 7.0:1). Every pair still comfortably clears the 4.5:1 AA text
  minimum either way, so no pass/fail conclusion changes — this is a
  documentation-precision nit, not a real accessibility defect, and it's a
  design.md artifact rather than something the developer introduced.
- Failure scenario: none — flagging only because recomputing stated
  contrast numbers rather than trusting them is this role's own standard.

### 3. `rel="noopener"` inconsistently applied across the two new `target="_blank"` link sites — nit
- File: `app/app.py` — `sessionsRow()` (has `rel="noopener"`) vs.
  `instSessionsSub()` (does not)
- Issue: both are new-this-cycle functions rendering the same kind of
  "open" link to a session's captured URL; one has the `rel="noopener"`
  hardening design.md recommends, the other doesn't. Matches a
  pre-existing inconsistency already in the file (e.g. `sub`'s own
  singleton-row link at `app/app.py:3472`/`4685` also omits it), so this
  isn't a new pattern, just an opportunity to make the two new sites
  consistent with each other.
- Failure scenario: theoretical reverse-tabnabbing via `window.opener` if
  a session's captured URL were ever attacker-influenced — low severity,
  pre-existing pattern elsewhere in the file, not introduced fresh by this
  diff's core logic.

None of the above block approval — all are nits/should-fix items with zero
functional or test impact.

## Follow-ups (non-blocking)
- Fix `app/teams.py:4042`'s dangling `instance_stop()` reference (Finding 1).
- Add `rel="noopener"` to `instSessionsSub()`'s link for consistency with `sessionsRow()` (Finding 3) — while at it, the pre-existing `sub`/singleton-row link sites could pick it up too, but that's outside this cycle's scope.
- Recompute/correct `docs/design.md`'s contrast figures if that file is revisited for any other reason (Finding 2) — not worth a dedicated cycle on its own.

## Overall verdict
**Approve.** All 8 acceptance criteria implemented and independently
verified this session (not inferred from the developer's report); the
entire frontend suite (183 tests across 7 files, including the new
canonical `kind='inst'` file) and the backend session-identity suite (32
tests) were actually executed and passed; the full regression suite
(1309 tests) was independently re-run and matches the claimed pre-existing
35 failures/79 errors/42 skipped across the same 9 files exactly, with the
fixture-shape claim in `test_smoke_check_frontend.js` confirmed genuine via
a real revert-and-watch-it-fail check, not just re-read. Both developer-
flagged deviations checked out as disclosed and correctly scoped, not
covers for missed cases. Findings above are all nits with zero blocking
impact — safe to hand back to product-manager for the next iteration.
