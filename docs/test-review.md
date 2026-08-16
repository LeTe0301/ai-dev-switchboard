# Test & Review: Concurrent sessions per project — part 1: session-identity backend

## Scope
Re-review of the same feature after the developer's fixes for the two
findings in the prior pass of this file (must-fix #1: cross-project session
kill; should-fix #2: doc-accuracy). This pass re-verifies both fixes for
real — including re-running my own original proof-of-concept exploits
against the current code and a revert-and-watch-it-fail check on the new
regression tests — then re-confirms the rest of the feature (already
verified clean in the prior pass) hasn't regressed. All commands below were
run for real, in this session, against the current uncommitted working tree
(`git diff` — `app/app.py`, `docs/implementation.md`, `docs/spec.md` modified;
`tests/test_session_identity.py` untracked/new since the initial commit).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 (re-review) | Cross-project session kill: `POST /instance/<A>/session/<B's-real-session-id>/stop` must not touch B's session, must return `200 {"ok": true}` | Manual PoC, run twice: once via the automated regression test, once via a fully standalone script outside the test framework, against a real `ThreadingHTTPServer` + real tmux | pass | Standalone script: spawned real session for `manualpoc-b` via `/instance/manualpoc-b/spawn`, then `POST /instance/manualpoc-a/session/<sid>/stop` → `200 {"ok": true}`, `tmux_has(sid)` still `True` afterward. `tests/test_session_identity.py::SessionIdentityEndpointTests::test_session_stop_route_rejects_a_session_id_owned_by_a_different_project` — ran, PASS |
| 2 (re-review) | Killing a real out-of-band `team-<project>`-named tmux session (never spawned via this spec's machinery) through this route must be a no-op regardless of URL project name | Manual PoC (standalone script + automated test), real tmux session created directly with `tmux new-session -s team-manualpoc-victim` | pass | Standalone script: `POST /instance/manualpoc-a/session/team-manualpoc-victim/stop` → `200 {"ok": true}`, `tmux_has("team-manualpoc-victim")` still `True` afterward. `tests/test_session_identity.py::SessionIdentityEndpointTests::test_session_stop_route_never_reaches_a_real_team_session` — ran, PASS |
| 3 (re-review) | The two new regression tests actually exercise the fix, not tautological | Revert-and-watch-it-fail: temporarily stripped the `if any(s["session_id"] == parts[3] for s in active_sessions(name)):` guard back to the unconditional call, re-ran just these two tests | pass (both correctly fail pre-fix) | Both tests FAIL against the un-guarded route (`AssertionError: False is not true` on `tmux_has(...)` — i.e. the victim session was actually killed), then both PASS again once the real fix was restored. Confirms the tests are real, not tautological |
| 4 (re-review) | `docs/implementation.md`'s `SmokeCheckRunTests` failure tally and pre-existing-failure file attribution are now accurate | Re-ran `SmokeCheckRunTests` unmodified against the new resolver in a scratch copy, and the full suite's own failing-test-name list | pass | Doc now says "14 failed/errored — 11 `FAIL` + 3 `ERROR`" (matches a fresh re-run); doc's 9-file breakdown (`test_gitea_sync_project` ×5, `test_new_project_from_gitea` ×6, `test_new_project_from_upload` ×4, `test_new_project_from_url` ×12, `test_taiga_push` ×1, `test_team_routes` ×47, `test_teams_grounding` ×3, `test_teams_lead` ×2, `test_teams_lifecycle` ×34) matches this session's independent full-suite failing-test tally exactly (114 = 35 failures + 79 errors, same per-file split) |
| 1–9 (prior pass, unaffected by this diff) | All original acceptance criteria (spawn, per-session stop isolation, legacy on/off shim semantics, reap sweep, headless collision, newest-session smoke-check targeting) | Automated (`tests/test_session_identity.py` + `tests/test_teams_headless.py`), re-run this session | pass | `tests/test_session_identity.py` — 36/36 pass (was 34/34 before the 2 new regression tests were added); `tests/test_teams_headless.py::ActiveEngineHeadlessCollisionTests` — pass |

## Regression check
Full suite: `python3 -m unittest discover -s tests` (run fresh this session,
not reused from the prior pass)

- **Now**: `Ran 1313 tests in 127.080s ... FAILED (failures=35, errors=79, skipped=42)`
- **Prior pass's "after" baseline** (before this cycle's two fixes):
  `Ran 1311 tests ... FAILED (failures=35, errors=79, skipped=42)`
- Delta: exactly +2 tests (the two new regression tests for Finding #1), same
  failure/error/skipped counts. Extracted the full failing-test-name list
  this session and tallied by file — 9 files, same names, same per-file
  counts as `docs/implementation.md`'s corrected write-up (see test case 4
  above) — confirms zero new regressions and the doc's tallies are accurate,
  not just self-consistent.
- `tests/test_session_identity.py` alone: `Ran 36 tests ... OK` (was 34;
  +2 for the new regression tests).
- `python3 -m py_compile app/app.py app/teams.py` — clean, no syntax errors.

## Spec coverage
Unchanged from the prior pass: all 9 stated acceptance-criteria bullets in
`docs/spec.md` are implemented and covered by a test run this session. The
gap the prior review pass surfaced (cross-project/cross-namespace ownership
on `/session/<id>/stop`, not itself a stated AC or edge-case bullet, but a
real hole found by exercising the actual route) is now closed: the route
carries an explicit ownership check plus its own inline comment explaining
why it's there, and two new regression tests cover it directly.

## Findings (most severe first)
None. Both prior findings are resolved:

### Finding #1 (prior must-fix, security) — RESOLVED
`app/app.py`'s `/session/<id>/stop` route now guards with
`if any(s["session_id"] == parts[3] for s in active_sessions(name)):
instance_stop_session(parts[3])` before tearing down (still unconditionally
returning `{"ok": true}` after, preserving the idempotent-no-404 contract
for an already-gone-but-legitimately-owned id). Re-verified with both of my
original live PoC exploits (cross-project session kill; killing a real
`team-*`-named tmux session through this route) run independently outside
the test framework — both now no-op and return `200 {"ok": true}` while the
target session survives. The two new regression tests
(`test_session_stop_route_rejects_a_session_id_owned_by_a_different_project`,
`test_session_stop_route_never_reaches_a_real_team_session`) were confirmed
non-tautological via revert-and-watch-it-fail: both genuinely fail against
the pre-fix code and pass against the current code.

### Finding #2 (prior should-fix, doc accuracy) — RESOLVED
`docs/implementation.md` now says "14 failed/errored — 11 `FAIL` + 3
`ERROR`" (was "13") and lists the correct 9-file spread for the pre-existing
35/79/42 baseline (was "test_teams_lead/test_teams_grounding only"). Both
numbers independently reproduced this session against a fresh full-suite
run — see Regression check above.

## Follow-ups (non-blocking, carried over from the prior pass, still valid)
- `instance_start()` (`app/app.py`) adds a session_id to `_sessions` before
  the real `tmux new-session` subprocess call completes, leaving a narrow
  window where a concurrent `/status` poll's reap sweep could act on an
  about-to-exist session. Inherited verbatim from `docs/spec.md`'s own
  pseudocode, low real-world probability, same shape of race the codebase
  already accepts elsewhere. Worth a small guard in a later pass, not
  blocking.

## Overall verdict
**Approve.** Both findings from the prior pass are genuinely fixed, not
just documented as fixed: the must-fix ownership check was verified against
two independently-run live proof-of-concept exploits (both now no-op
correctly), the two new regression tests were confirmed to actually catch
the regression via a revert-and-watch-it-fail check, and the doc-accuracy
fix's numbers were independently reproduced against a fresh full-suite run
this session (114 failures/errors across the same 9 files, same per-file
counts). The full suite shows zero new regressions (1311 → 1313 tests,
identical 35/79/42 pre-existing failure/error/skip counts, same failing
test names). No new issues surfaced during this re-review. Hands control
back to the product-manager agent for the next iteration.
