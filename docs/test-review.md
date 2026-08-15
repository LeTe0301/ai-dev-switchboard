# Test & Review: follow-up fixes — items 8, 12 (piece C), 20 (broader audit)

## Scope
Independent re-verification of `docs/implementation.md`'s three bundled
fixes against `docs/spec.md`'s acceptance criteria: item 8 (episode-keyed
AI-reviewer lock), item 12 piece C (widened poll-boundary gate), item 20
(broader button/control contrast audit). This document has two passes:

1. The **original** testing/review pass (below, unchanged from the first
   sitting) — found Defect 1 (must-fix: a TOCTOU in the episode-currency
   check) and Defect 2 (should-fix: a TOCTOU in lock-dict cleanup), plus two
   minor gaps, and blocked.
2. A **re-review pass** (new section below, this sitting) — independently
   re-verifies the developer's claimed fixes for both defects against the
   actual shipped code (not the developer's summary), reruns the full
   suite, and issues the final verdict for this cycle.

---

## Original pass (first sitting) — test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Piece 1 AC1 — label removed-and-readded while previous episode's thread is still in-flight dispatches a NEW thread, not silently dropped | Automated: `tests.test_ai_reviewer.AiReviewerEpisodeRaceTests.test_new_episode_is_dispatched_not_dropped_while_stale_episode_in_flight`, run directly; also independently reverted `lock_key = (pr_key, episode)` back to `lock_key = pr_key` and reran — test failed with the exact message the developer's docs claim ("episode 2's review thread was never dispatched -- dropped by the stale (pr_key, episode-1) lock"), then restored and reconfirmed green | pass | Ran locally: `Ran 2 tests ... FAILED (failures=1)` on revert, `OK` after restore |
| 2 | Piece 1 AC2 — a stale (old-episode) thread's eventual completion write is a no-op; state stays on whichever episode is actually current | Automated (developer's own coarse-timing test) + a new, targeted adversarial repro racing the trigger-edge write directly against the stale thread's own check-then-write window | **FAIL** | `race_repro3.py` reproduced a real clobber against the shipped `_ai_reviewer_record_failure`/`_save_ai_reviewer_state_entry` code — see original Defect 1 below |
| 3 | Piece 1 AC3 — normal, non-racing single-episode review is unaffected | Automated: full `tests.test_ai_reviewer` suite (83 tests) | pass | `Ran 83 tests in 0.668s ... OK` |
| 4 | Piece 1 AC4 — `_ai_reviewer_pr_locks` does not grow unbounded; a finished thread's `(pr_key, episode)` entry is removed | Automated + adversarial repro targeting the cleanup mechanism | pass (AC holds), but see original Defect 2 (should-fix) | Both dedicated tests pass; `race_repro2.py` showed the underlying release/pop TOCTOU |
| 5 | Piece 1 AC5 — pre-existing state entries with no `episode` key read correctly, no crash | Manual script (`backcompat_check.py`) | pass | manual only; no dedicated unit test yet — flagged as a minor gap |
| 6 | Piece 1 — retry branch passes the SAME lock key as the original trigger | Code read | pass | `app/app.py:1619,1648-1649` |
| 7-11 | Piece 2 AC1-AC4 + backend status vocabulary | Automated `tests/test_team_frontend.js` + code read | pass | `node tests/test_team_frontend.js` → `ALL PASS (106/106)` |
| 12-14 | Piece 3 AC1-AC3 — audit table, `.wizard-step` fix, `.team-btn`/`.deploy-btn` untouched | Independent WCAG recompute of all 16 pairs + code read | pass, with one gap (`.card .back` misclassified in "Considered and excluded") | `contrast.py` output matched table; `.card .back` gap noted |

### Original pass — defects found
**Defect 1 (must-fix)**: `_ai_reviewer_record_failure()` and the final
success-write guard checked `prev.get("episode", 0) == episode` via an
**unlocked** read, then separately called `_save_ai_reviewer_state_entry()`
under the lock — not atomic with each other. A concurrent trigger-edge write
landing in the gap let a stale thread's already-passed check clobber the
newer episode's state, moving `episode` backwards. Repro'd against the
actual shipped code, no modification needed.

**Defect 2 (should-fix, not blocking)**: `_ai_reviewer_review_bg()`'s
`_run()` closure did `lock.release()` then, as a separate statement,
`_ai_reviewer_pr_locks.pop(lock_key, None)` — a TOCTOU that could let two
callers run concurrently for the same `(pr_key, episode)` key. Judged
currently unreachable via the app's real call graph (poll passes are
serialized, tens of seconds to minutes apart — many orders of magnitude
wider than the microsecond release-to-pop gap), so non-blocking, but worth
closing defensively.

**Minor gaps**: no dedicated AC5 regression test; `.card .back` audit note
factually wrong (called it an anchor; it's a `<span onclick=...>`).

### Original pass — verdict
**Blocked** on Defect 1. Route back to developer.

---

## Re-review pass (this sitting) — verifying the developer's fix

The developer's claim: Defect 1 fixed via a new
`_ai_reviewer_save_if_current_episode(pr_key, episode, **fields)`
(`app/app.py:1397-1421`) that performs the read, the episode-currency check,
and the write all inside one `_ai_reviewer_state_lock` critical section.
Defect 2 fixed by popping the lock-dict entry before releasing the lock,
inside the same `_ai_reviewer_pr_locks_guard` critical section
(`app/app.py:1604-1624`). New regression test
`AiReviewerEpisodeAtomicWriteRaceTests` (`tests/test_ai_reviewer.py:956-1063`).
AC5 gained dedicated coverage; the `.card .back` doc error corrected.

### Re-review test cases

| # | Item | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `_ai_reviewer_save_if_current_episode` — read+check+write genuinely all inside `_ai_reviewer_state_lock`; no early-return-before-acquire or reentrant-deadlock path | Read the function directly (`app/app.py:1397-1421`): single `with _ai_reviewer_state_lock:` block wraps `_load_ai_reviewer_state()`, the `episode` check, `_ai_reviewer_state_entry_dict()`, and `_write_ai_reviewer_state()`. `_write_ai_reviewer_state()` and `_load_ai_reviewer_state()` do plain file I/O and never touch `_ai_reviewer_state_lock` themselves (confirmed by reading both — `_write_ai_reviewer_state`'s own docstring: "Assumes the caller already holds `_ai_reviewer_state_lock`") | pass | No code path returns before the `with` statement acquires the lock; no nested lock acquisition anywhere in the call chain |
| 2 | Reproduce the ORIGINAL Defect 1 race against the NEW code, using my own independently-written repro (not the developer's test) | Wrote `race_repro_v3.py`: hooks `_load_ai_reviewer_state()` to inject a concurrent fresh trigger-edge write (episode 2) exactly at the second (currency-check) read inside `_ai_reviewer_record_failure`→`_ai_reviewer_save_if_current_episode`'s call chain, then lets the stale (episode 1) call finish | pass — **no clobber** against current code; **confirmed clobbers** when I temporarily reverted `_ai_reviewer_save_if_current_episode()` to the old unlocked-check-then-separate-write shape, then confirmed clean again after restoring | Current code: `final state: {'episode': 2, 'last_error': None, 'attempts': 0, ...}` → `NO CLOBBER`. Reverted code: `final state: {'episode': 1, 'last_error': 'stale failure from episode 1', 'attempts': 2}` → `CLOBBERED`. Restored, diff stat confirmed unchanged (`app/app.py \| 199 ++...`), `AiReviewerEpisodeAtomicWriteRaceTests` also independently re-run: passes on current code, fails with `1 != 2` on the reverted code, matching the developer's own claimed repro exactly |
| 3 | Defect 2 fix (pop-before-release) doesn't create a NEW problem — traced whether a caller acquiring via `_ai_reviewer_pr_lock_for()` between pop and release could get an orphaned/duplicate lock | Read `app/app.py:1450-1456` (`_ai_reviewer_pr_lock_for`) and `1604-1624` (`_run()`'s cleanup) together | pass — no new problem introduced by this specific fix | Both the pop and the release happen inside the SAME `_ai_reviewer_pr_locks_guard` critical section, and `_ai_reviewer_pr_lock_for()` also acquires that same guard before reading/inserting into the dict — so no other thread can observe the dict in a state where the entry has been popped but the lock not yet released, or vice versa; that specific window is genuinely closed. (See "Residual observation" below for a narrower, structurally pre-existing race this fix does not — and was never claimed to — close.) |
| 4 | Full suite re-run | `python3 -m unittest discover -s tests` (twice), `python3 -m unittest tests.test_ai_reviewer -v`, `node tests/test_team_frontend.js` | pass | Run 1: `Ran 1198 tests ... FAILED (failures=1)` — the one failure, `RealTmuxHeadlessTests.test_forced_session_kill_mid_run_is_classified_as_cancelled_not_success`, is in a file this diff doesn't touch (`git diff --stat -- tests/test_teams_headless.py app/teams.py` is empty) and passed both standalone and on a full immediate re-run (Run 2: `Ran 1198 tests ... OK`) — pre-existing environment flakiness (real-tmux session-name collision visible in the log: `duplicate session: team-sessionrace-p<pid>` immediately preceding the failure), not a regression from this diff. `tests.test_ai_reviewer`: `Ran 85 tests ... OK` (matches claim: 83 + 2 new). `node tests/test_team_frontend.js`: `ALL PASS (106/106)` (matches claim) |
| 5 | `.card .back` correction is accurate and in the audit table | Read `app/app.py:2989` (`.card { background: #1c1c1c; ...}`) and `app/app.py:3000-3001` (`.card .back { ...color: #888; cursor: pointer; }`), confirmed it's a `<span class="back" onclick=...>` (`app/app.py:3080,3093`), not an `<a>`. Independently recomputed WCAG contrast from the literal hex values (own script, standard relative-luminance formula) | pass | `#888888` on `#1c1c1c` = **4.81:1** (recomputed independently, matches `docs/implementation.md`'s claimed figure exactly) — passes AA's 4.5:1 by a real but modest margin. Confirmed present in the audit table (`docs/implementation.md` line ~251) and removed from "Considered and excluded" (line ~271-273), replaced with an accurate note that it was previously mis-grouped there |
| 6 | AC5 regression test is genuine, not just present | Read `tests/test_ai_reviewer.py:284-316` (`test_pre_existing_state_entry_missing_episode_key_is_backward_compatible`) | pass | Seeds a raw pre-fix-format JSON entry (no `episode` key, bypasses `_save_ai_reviewer_state_entry`), drives both the no-op poll path and the label-removed-then-readded path, asserts no crash, `episode` defaults to `0`, then bumps to `1` on the next trigger edge. Ran as part of the 85-test `test_ai_reviewer` run above |

### Residual observation (non-blocking, not a new defect, pre-existing)
While tracing item 3 above, I confirmed a narrower, structurally different
race still theoretically exists: `_ai_reviewer_review_bg()`'s
`lock = _ai_reviewer_pr_lock_for(lock_key); if not lock.acquire(blocking=False)`
is itself two separate statements with no lock held between them. A caller
that fetches a lock reference from the dict *before* another thread's
pop-then-release cleanup runs, then calls `.acquire()` *after* that cleanup
completes, can succeed in acquiring an orphaned lock object no longer
present in the dict — and a third, later caller for the same key would then
create a genuinely new lock and could run concurrently with the second. This
is **not created by this fix** (it exists independent of pop/release
ordering — it's inherent to the two-step fetch-then-acquire pattern at the
call site) and is subject to the exact same "unreachable via the app's real
call graph" reasoning the original Defect 2 finding already established:
`_ai_reviewer_review_bg()` is only reachable from a single-pass-at-a-time
poll loop, so two dispatches for the identical `(pr_key, episode)` key are
tens of seconds to minutes apart in practice, not microseconds. Not raised
as a new defect; noted for completeness since the task asked me to trace
this specifically. Worth folding into the same defensive follow-up as the
original Defect 2 note if this code is ever touched again.

### Regression check (re-review pass)
- `python3 -m unittest discover -s tests` — run twice: `Ran 1198 tests`
  both times (matches `docs/implementation.md`'s claimed count exactly);
  first run had one unrelated, pre-existing flaky failure (see test case 4
  above), second run clean (`OK`).
- `python3 -m unittest tests.test_ai_reviewer -v` — `Ran 85 tests ... OK`
  (matches claim: 83 original + 2 new this pass).
- `node tests/test_team_frontend.js` — `ALL PASS (106/106)` (matches claim).

### Spec coverage (re-review)
- Piece 1 (item 8) AC1-AC5: all five now genuinely covered and verified.
  AC2 (stale completion is a no-op) — the one AC the original pass found
  unmet — is now independently confirmed fixed via a fresh, differently-
  constructed repro against the shipped code (not just re-running the
  developer's own test), including a revert-and-watch-it-fail check.
- Piece 2 (item 12 piece C): unchanged since the original pass, which found
  no gaps; re-confirmed via the full frontend suite this sitting.
- Piece 3 (item 20): the one completeness gap from the original pass
  (`.card .back`) is now closed — recomputed and confirmed accurate.

### Findings (re-review)
No must-fix or should-fix findings from this pass. The one residual
observation above is explicitly non-blocking, pre-existing (not introduced
by this fix), and already covered by the original Defect 2 finding's own
"currently unreachable via the real call graph" reasoning.

### Re-review verdict
**Approve.** Both defects from the original pass are genuinely closed:
Defect 1's fix makes the episode-currency check and the write atomic under
one lock, and I independently reproduced the original clobber against a
temporarily-reverted copy of the fix and confirmed it no longer occurs
against the shipped code. Defect 2's fix closes the specific TOCTOU it
targeted (pop and release are now atomic under the same guard). AC5 has
dedicated regression coverage. The `.card .back` documentation error is
corrected and its ratio independently reconfirmed. Full test suite (1198),
`test_ai_reviewer` (85), and frontend suite (106) all pass, matching the
developer's claimed counts exactly; the one full-suite failure observed on
the first run is an unrelated, pre-existing, environment-dependent flaky
test (real tmux session-name collision) in a file this diff never touches,
and it passed on immediate re-run and in isolation.

## Follow-ups (non-blocking, optional)
- Fold the residual fetch-then-acquire race noted above into the same
  defensive cleanup as the original (now-closed) Defect 2 finding, if
  `_ai_reviewer_review_bg()`/`_ai_reviewer_pr_lock_for()` are touched again.
- `RealTmuxHeadlessTests.test_forced_session_kill_mid_run_is_classified_as_cancelled_not_success`
  intermittently fails under `unittest discover`'s full-suite run (passes in
  isolation) — likely real-tmux session-name collision with another test in
  the same process; unrelated to this cycle but worth a look if it recurs.
