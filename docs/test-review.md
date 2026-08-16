# Test & Review: Concurrent sessions per project — part 1: session-identity backend

## Scope (this pass)
Fix-up round on top of an already reviewer-approved cycle (see history below).
An independent `/code-review` pass (outside this pipeline) caught 6 real
defects after approval; `docs/implementation.md`'s "Fix-up pass 2" section
documents all 6. This pass verifies each of the 6 fixes for real (not by
reading the diff and trusting the writeup) — revert-and-watch-it-fail checks
against 4 of them, direct code/lock inspection for the 5th, a quick read for
the 6th (doc-only) — then re-runs the full regression suite. Scope is
deliberately this fix-up round only, not a from-scratch re-review of the
whole feature (already covered by the prior two passes below).

### History
1. Original pass: approved with 2 findings (must-fix cross-project session
   kill, should-fix doc accuracy).
2. Re-review pass (first fix-up): both findings verified fixed, **approved**.
3. **This pass**: verifying a second, independent fix-up round (6 defects
   from a `/code-review` pass) on top of pass 2's approval.

All commands below were run for real, in this session, against the current
uncommitted working tree on `feature/ad-8/session-identity-backend`
(`git diff` — `app/app.py`, `app/teams.py`, `docs/implementation.md`,
`tests/test_session_identity.py`, `tests/test_smoke_check.py` modified).

## Test cases

| # | Fix | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Resource-leak fix: `instance_start()` registers in `_sessions` only after `tmux_has()` confirms the session is real | Revert-and-watch-it-fail: temporarily restored `_sessions_add()` to before `subprocess.run` and neutralized the `tmux_has()` guard, re-ran the new regression test | pass | Reverted code: `test_instance_start_does_not_register_a_session_that_never_came_up` FAILS (`'claude-proj-...-forced-...' is not None`). Current code: same test PASSES. Also manually verified the "no half-created tmux session left behind" half of the claim: `tmux new-session -d -s X ... bash -lc "exit 1"` self-tears-down immediately (`tmux has-session` → "can't find session" right after), confirming a fast-failing engine command leaves nothing for `instance_start()` to leak on its `tmux_has()`-false failure path |
| 2 | Self-healing regression fix: independent second sweep over `_ttyd_urls`/`_ttyd_procs`/`_session_urls` restored in `_reap_dead_state()` | Revert-and-watch-it-fail: temporarily removed the second sweep loop, re-ran the new orphan regression test (which seeds an entry with no backing `_sessions` record) | pass | Reverted code: `test_reap_dead_state_cleans_orphaned_bookkeeping_not_backed_by_sessions` FAILS (`'orphan-...' unexpectedly found in {...}`). Current code: same test PASSES |
| 3 | Lock-discipline fix: new `_sessions_ids()` lock-guarded accessor, `_reap_dead_state()`'s primary sweep routed through it | Direct code read + grep for any other new unguarded `_sessions` access in this diff | pass | `_sessions_ids()` (`app/app.py`) is `with _sessions_lock: return list(_sessions)` — genuinely lock-guarded. `_reap_dead_state()`'s primary sweep now reads `for session_id in _sessions_ids():`, not `list(_sessions)`. Grepped every `_sessions` reference in `app/app.py`: the only other direct-dict access is inside `active_sessions()` (pre-existing, already lock-guarded, unmodified by this diff) — no new unguarded access introduced anywhere in this fix-up round |
| 4 | Perf fix: `/status` and `smoke_check_run()` load `engines` once and thread it through `_resolve_session_url()`/`_latest_session_url_for_project()` | Traced both call sites; revert-and-watch-it-fail on the new threading regression test | pass | `/status`'s handler loads `engines = load_engines()` once (line 6113) before its per-project loop and passes it into every `_resolve_session_url(s["session_id"], s["engine"], engines)` call in that loop (line 6178) — exactly one call per request regardless of session count. `smoke_check_run()` loads `engines` once and passes it to `_latest_session_url_for_project(name, engines)`. Reverted `_resolve_session_url()` to always call `load_engines()` internally: `test_resolve_session_url_does_not_reload_engines_when_a_dict_is_passed` FAILS (`AssertionError: load_engines() must not be called when engines is provided`). Current code: same test PASSES, sibling fallback test also PASSES |
| 5 | Test-bug fix: `SessionIdentityEndpointTests.setUp()`/`tearDown()` now save/clear/restore `appmod._sessions`, not `appmod.SESSIONS` | Direct code read; confirmed `SESSIONS` (app.py:306, login/auth-cookie store) and `_sessions` (session-identity registry) are genuinely distinct globals; scanned the file for any test depending on the old `SESSIONS.clear()` side effect | pass | `grep -n "^SESSIONS"` confirms `SESSIONS = {}  # session id -> {"expiry": ..., "totp_ok": bool}` at app.py:306 — unrelated to `_sessions`. Every test in `SessionIdentityEndpointTests` calls `self._authed()` → `self._login()` after `setUp()`, installing a fresh cookie regardless of `SESSIONS`'s prior contents — confirmed by reading the class; no test asserts on or depends on `SESSIONS` state. `tests.test_session_identity` — 41/41 pass (ran fresh this session) |
| 6 | Doc fix: `app/teams.py`'s `_create_team_session()` docstring updated from `active_engine()` to `active_sessions()` | Quick read | pass | Docstring now reads "same precondition style app.py's legacy /on route already uses via active_sessions()" — accurate against current code (`active_engine()` no longer exists anywhere in the codebase) |
| Deviation | `tests/test_smoke_check.py`'s `_latest_session_url_for_project` monkeypatch updated to accept the new `engines` positional arg | Direct code read + reasoned through the failure mode it prevents | pass, sound | `appmod._latest_session_url_for_project = lambda name, engines=None: appmod._session_urls.get(name)` — correct. Without this, a bare `appmod._session_urls.get` monkeypatch would receive the caller's `engines` dict as `dict.get()`'s own `default` parameter (`dict.get(key, default)`), returning the dict itself in place of a URL whenever the primary lookup missed — plausible, not a hypothetical (matches `dict.get`'s actual signature). The fix is minimal and doesn't change any of the 25 pre-existing test bodies in that class |
| Regression | Full existing regression suite unaffected | `python3 -m unittest discover -s tests` | pass | `Ran 1318 tests in 127.019s ... FAILED (failures=35, errors=79, skipped=42)` — matches implementation.md's claim exactly. Extracted the full failing-test list and tallied by file this session: `test_gitea_sync_project`×5, `test_new_project_from_gitea`×6, `test_new_project_from_upload`×4, `test_new_project_from_url`×12, `test_taiga_push`×1, `test_team_routes`×47, `test_teams_grounding`×3, `test_teams_lead`×2, `test_teams_lifecycle`×34 = 114 (35+79) — identical to implementation.md's stated 9-file breakdown, same per-file counts |

## Regression check
- `python3 -m py_compile app/app.py app/teams.py tests/test_session_identity.py tests/test_smoke_check.py` — clean.
- `python3 -m unittest discover -s tests` — `Ran 1318 tests ... FAILED (failures=35, errors=79, skipped=42)`, same pre-existing 9-file failure/error spread as the prior-pass baseline (`1313` tests) plus this round's 5 new passing regression tests (1313 → 1318).
- `tests/test_session_identity.py` alone — `Ran 41 tests ... OK` (was 36 before this round's 5 new regression tests).
- `tests/test_smoke_check.py` alone — `Ran 25 tests ... OK`.
- `tests/test_teams_headless.py::ActiveEngineHeadlessCollisionTests` — `OK`.

## Spec coverage
Unchanged from the prior two passes: all 9 stated acceptance-criteria bullets
in `docs/spec.md` remain implemented and covered by tests that ran clean this
session. None of this round's 6 fixes touch spec-level behavior — they're
implementation-correctness/perf/test-hygiene fixes against the design the
spec already called for, as `docs/implementation.md` itself states and as
verified above.

## Findings (most severe first)

### Finding: doc-accuracy — "153 tests total across the three files" is wrong (should-fix)
`docs/implementation.md`, both in the "Fix-up pass 2" summary and in "How to
verify locally," states: *"`tests/test_session_identity.py` (41 tests ...),
`tests/test_smoke_check.py`, and `tests/test_teams_headless.py`'s
`ActiveEngineHeadlessCollisionTests` all pass (153 tests total across the
three files)."*

Actually ran all three together this session:
```
python3 -m unittest tests.test_session_identity tests.test_smoke_check tests.test_teams_headless.ActiveEngineHeadlessCollisionTests
Ran 67 tests ... OK
```
41 (`test_session_identity.py`) + 25 (`test_smoke_check.py`) + 1
(`ActiveEngineHeadlessCollisionTests`) = **67**, not 153 — off by 86 tests,
more than double the real count. This is a fresh claim introduced in this
exact fix-up round (confirmed via `git diff docs/implementation.md`, both
occurrences are new `+` lines, not carried over from the prior approved
pass), stated twice with "verified"/"confirmed" language, in the same
section that had a near-identical doc-accuracy defect flagged and fixed in
the prior review pass (that pass's Finding #2). All the *substantive* claims
in this same paragraph (the 1318/35/79/42 full-suite tally, the 9-file
breakdown, the 41-test count for `test_session_identity.py`) independently
checked out exactly against this session's own runs — only the "153 tests
total" arithmetic is wrong. Doesn't affect the correctness of any shipped
code (the underlying tests genuinely exist, genuinely pass, genuinely cover
the fixes), so this doesn't block approval — but given this is a repeat of
the exact category of error the prior pass already dinged once, it's worth
fixing in the next doc touch rather than carrying it forward again.

## Follow-ups (non-blocking, carried over, still valid)
- Same as the prior pass: no new ones surfaced by this round's fixes
  themselves. The one follow-up recorded in the prior pass (a narrow
  registration-before-confirmation race in `instance_start()`) is now
  **resolved** — that's exactly finding #1 fixed in this round, verified
  above.

## Overall verdict
**Approve.** All 6 fixes from this fix-up round are genuinely correct, not
just documented as fixed:
- Finding #1 (resource leak) and finding #2 (self-healing regression) were
  each independently confirmed via a revert-and-watch-it-fail check against
  their own regression test, plus a real manual tmux check for #1's
  "no half-created session left behind" half of the claim.
- Finding #3 (lock discipline) was confirmed by direct inspection: the new
  `_sessions_ids()` accessor is genuinely lock-guarded, and no other new
  unguarded `_sessions` access exists anywhere in this diff.
- Finding #4 (perf) was confirmed both by tracing `/status`'s call sites
  (exactly one `load_engines()` per request) and via revert-and-watch-it-fail
  on its own regression test.
- Finding #5 (test bug) was confirmed by verifying `SESSIONS` and `_sessions`
  are genuinely distinct globals and that no test depended on the old
  accidental clearing behavior.
- Finding #6 (doc fix) is accurate against current code.
- The `test_smoke_check.py` monkeypatch deviation is a correct, necessary
  adaptation — reasoned through the exact failure mode (`dict.get`'s
  `default` param) it prevents.

Full regression suite shows zero new failures (1313 → 1318 tests, identical
35/79/42 pre-existing failure/error/skip tally, same 9 files, same per-file
counts, verified by an independent tally of this session's own run).

One should-fix, non-blocking finding: `docs/implementation.md`'s "153 tests
total across the three files" claim is wrong (actual: 67) — worth a quick
correction next time this doc is touched, not worth a loop back to the
developer on its own. Hands control back to the product-manager agent for
the next iteration.
