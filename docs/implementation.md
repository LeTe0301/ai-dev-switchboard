# Implementation: follow-up fixes — items 8, 12 (piece C), 20 (broader audit)

## Summary
Three independent, non-overlapping fixes in `app/app.py`, all fully
diagnosed in `docs/spec.md` before this cycle started:

1. **Item 8** — the AI-reviewer poll/lock chain now threads a persisted
   `episode: int` through every function in the trigger→dispatch→run→
   completion-write chain, and keys the in-flight per-PR lock on
   `(pr_key, episode)` instead of `pr_key` alone. A label removed and
   re-added while the *previous* episode's review thread is still running
   now dispatches a real new thread (previously silently dropped by the
   stale thread's still-held lock), and a stale thread's eventual
   completion write is now a guarded no-op against a since-superseded
   episode instead of clobbering the new episode's state.
2. **Item 12 piece C** — `teamFeedEventKindClass()`'s poll-boundary
   transient-classification gate widened from `status === 'running'` to
   `status !== 'finished' && status !== 'error'` (the full non-terminal
   status set: `'idle'`/`'running'`/`'blocked'`), closing the same gap the
   reviewer confirmed adversarially for `status === 'blocked'`.
3. **Item 20** — a full audit of every CSS rule in `app/app.py`'s
   `<style>` block that pairs a `color` with a `background`/
   `background-color` on an interactive control (buttons, pills, badges,
   status strips). One failure found and fixed: `.wizard-step`'s default
   (non-active, non-done) state paired `#666` text on a `#2a2a2a`
   background (~2.5:1, fails WCAG AA's 4.5:1 normal-text minimum) — fixed
   to `#aaa`, matching `.pill`'s/`.wizard-actions .secondary`'s own
   already-passing `#2a2a2a`/`#aaa` pairing elsewhere in this exact file
   (~6.18:1), the same precedent-reuse approach PR #12 used for
   `.team-btn`. Full audit table below.

## Follow-up fix pass (response to reviewer findings, `docs/test-review.md`)
The reviewer's testing pass blocked this cycle on one must-fix defect in
piece 1 (item 8) and asked for three small adjacent fixes bundled into the
same pass. This is **not** a fresh feature — it's a correctness fix to the
piece 1 implementation described above, plus two documentation/test
corrections. Nothing else in pieces 1-3 changed.

- **Defect 1 (must-fix) — stale-episode completion write could still
  clobber a newer episode's state.** `_ai_reviewer_record_failure()` and the
  final success-write guard inside `_ai_reviewer_review_run()` used to check
  `prev.get("episode", 0) == episode` via an **unlocked** read of
  `_load_ai_reviewer_state()`, and only *then* call
  `_save_ai_reviewer_state_entry()`, which did its own separate, later
  read+write under `_ai_reviewer_state_lock`. That check-then-write pair was
  not atomic with each other: a concurrent trigger-edge write (a fresh
  episode) landing in the gap between the stale thread's unlocked check and
  its own later locked write let the stale thread's already-passed check go
  on to overwrite the newer episode's state anyway, moving `episode`
  backwards. Fixed by introducing
  `_ai_reviewer_save_if_current_episode(pr_key, episode, **fields)`
  (`app/app.py` ~1397-1421), which performs the read, the `episode`-currency
  check, AND the write all inside ONE `_ai_reviewer_state_lock` critical
  section (refactored the write body itself out into a small shared
  `_ai_reviewer_state_entry_dict()`/`_write_ai_reviewer_state()` pair so both
  `_save_ai_reviewer_state_entry()` — the unconditional write, still used by
  the trigger edge and the label-absent "arm next add" path, which have no
  currency check to make — and the new guarded variant share the same
  write logic instead of duplicating it). Both previously-vulnerable call
  sites (`_ai_reviewer_record_failure()` at ~1451-1466 and the final success
  write inside `_ai_reviewer_review_run()` at ~1573-1575) now call
  `_ai_reviewer_save_if_current_episode()` instead of doing their own
  unlocked check followed by a separate `_save_ai_reviewer_state_entry()`
  call.
- **Defect 2 (should-fix) — lock-dict cleanup TOCTOU.**
  `_ai_reviewer_review_bg()`'s `_run()` closure (~1604-1624) used to do
  `lock.release()` and then, as a *separate* statement,
  `_ai_reviewer_pr_locks.pop(lock_key, None)` under
  `_ai_reviewer_pr_locks_guard` — non-atomic (currently unreachable via this
  app's real call graph per the reviewer's own analysis, but a latent
  TOCTOU). Fixed by moving the pop inside the same
  `_ai_reviewer_pr_locks_guard` critical section as the release, popping
  before releasing, so no other thread can observe the dict entry at all
  until it's already gone.
- **Minor gap 1 — missing AC5 regression test.** Added
  `AiReviewerPollRepoTests.test_pre_existing_state_entry_missing_episode_key_is_backward_compatible`
  (`tests/test_ai_reviewer.py`) — seeds a raw, pre-fix-format state entry
  (no `episode` key at all, written directly as JSON, bypassing
  `_save_ai_reviewer_state_entry()` which always includes `episode` now) and
  drives it through both the no-op poll path (label already present,
  already reviewed) and the label-removed-then-readded path, confirming no
  crash and that the missing key defaults to `0` via `.get("episode", 0)`.
- **Minor gap 2 — `.card .back` audit note was factually wrong.**
  `docs/implementation.md`'s "Considered and excluded" section wrongly
  grouped `.card .back` with `.team-configure-btn`/`.team-feed-toggle` as a
  literal `<a>` anchor styled like the global link rule. It's actually a
  `<span onclick=...>` with its own distinct `#888` color (confirmed at
  `app/app.py:3000-3001`, `.card`'s background `#1c1c1c` at
  `app/app.py:2989`). Corrected: removed it from the "excluded" list and
  added it to the actual audit table with its real ratio (4.81:1,
  independently recomputed against the WCAG relative-luminance formula —
  passes AA's 4.5:1 threshold by a real but modest margin; no code change
  needed).

### New regression test for the fix itself
`AiReviewerEpisodeAtomicWriteRaceTests` (`tests/test_ai_reviewer.py`,
~956-1063) reproduces the reviewer's own repro technique directly against
the shipped fix: hooks `_load_ai_reviewer_state()` (the exact read whose
locked/unlocked status Defect 1 was about) so that, immediately after it
returns a value to its caller, an independent, unsynchronized concurrent
fresh trigger-edge write (episode 2) is injected — then confirms the stale
(episode 1) call's own guarded write does not clobber it. **Verified this
test actually catches the regression, not just exercises the code path**:
temporarily reverted `_ai_reviewer_save_if_current_episode()` to the exact
pre-fix "unlocked check, then separate locked write" shape and reran this
test — it failed with `1 != 2` (`s[pr_key]["episode"]`), i.e. the exact
backwards-episode clobber Defect 1 describes, with the fresh write
confirmed to have landed inside what should have been the stale check's own
critical section (`landed_before_read_returned: [True]`). Restored the fix
and confirmed green again, then reran the full `tests.test_ai_reviewer`
suite (85 tests) clean.

## Root cause
- **Item 8**: no episode identity existed anywhere in the AI-reviewer state
  — state was keyed purely by `pr_key`, so there was no way to distinguish
  "this completion belongs to episode N" from "a newer episode M > N is now
  current," and the in-flight lock (keyed on `pr_key` alone) meant a stale
  thread's held lock silently blocked a brand-new episode's dispatch.
- **Item 12 piece C**: the transient-classification gate only checked
  `status === 'running'`, missing the structurally identical poll-boundary
  gap for `status === 'blocked'` (a *different* in-flight round's
  `ask_user` escalation can flip status to `'blocked'` while this event's
  own paired `tool_result` hasn't arrived yet) and `status === 'idle'`.
- **Item 20**: PR #12 fixed `.team-btn`/`.deploy-btn`'s specific failure but
  never audited the rest of the file's button/pill/badge/status-strip color
  pairings for the same class of drift.

## Changes by file

### `app/app.py` — Piece 1 (item 8), lines ~1369-1650
- `_save_ai_reviewer_state_entry()` — new required `episode: int` keyword
  param, persisted in the state entry (`s[pr_key]["episode"] = episode`).
- `_ai_reviewer_record_failure()` — new required `episode: int` positional
  param. Re-reads the current state entry first; if its `episode` no longer
  matches the caller's `episode`, the write is a silent no-op (superseded
  by a newer episode).
- `_ai_reviewer_pr_lock_for()` — signature widened from `pr_key: str` to
  `key` (no behavior change — `dict.get`/`dict[key] = ...` already work
  identically for a `str` or `tuple` key; only the type hint needed
  updating for accuracy).
- `_ai_reviewer_review_run()` — new required `episode: int` param, threaded
  into all 9 `_ai_reviewer_record_failure(...)` call sites inside it, and
  into the final success-save path, which is now guarded the same way
  (`if prev.get("episode", 0) == episode: _save_ai_reviewer_state_entry(...)`).
- `_ai_reviewer_review_bg()` — new required `episode: int` param. The
  in-flight lock is now keyed on `(pr_key, episode)` instead of `pr_key`
  alone. The background thread's `_run()` closure now also removes the
  `(pr_key, episode)` entry from `_ai_reviewer_pr_locks` once the thread
  finishes (`finally: lock.release(); ... _ai_reviewer_pr_locks.pop(lock_key, None)`)
  — without this, `_ai_reviewer_pr_locks` would grow by one entry per
  label-toggle cycle forever instead of just once per PR.
- `_ai_reviewer_poll_repo()` — the trigger edge (label-absent →
  label-present) now increments `episode = prev.get("episode", 0) + 1`
  before writing state and dispatching; the retry branch (label still
  present, `last_error` set) passes the *current* (unchanged) episode; the
  label-absent branch carries `episode` forward unchanged (arms the next
  add, doesn't start a new episode).

### `app/app.py` — Piece 2 (item 12 piece C), line ~3860
- `teamFeedEventKindClass()`'s poll-boundary gate:
  `status === 'running'` → `status !== 'finished' && status !== 'error'`.
  Comment above it rewritten to describe the widened non-terminal set
  instead of the single `'running'` value it used to name.

### `app/app.py` — Piece 3 (item 20), line ~2769
- `.wizard-step`'s default-state `color` changed from `#666` to `#aaa`
  (background `#2a2a2a` unchanged). New comment states the WCAG failure
  ratio and the in-file precedent reused, mirroring `.badge.taiga-ram`'s
  own existing comment style. `.wizard-step.active`/`.wizard-step.done`
  (which override `color` but not `background`) were already passing and
  are untouched.

### `tests/test_ai_reviewer.py`
- Every existing call site of `_save_ai_reviewer_state_entry()`,
  `_ai_reviewer_review_run()`, `_ai_reviewer_review_bg()`, and
  `_ai_reviewer_pr_lock_for()` updated to pass/expect the new `episode`
  parameter (mechanical — the new param is required, not optional, so
  every pre-existing call needed updating). Two full-state-dict equality
  assertions gained the new `"episode"` key. `bg_calls` tuple
  destructuring (`host, owner_repo, entry, pr = ...`) widened to 5 elements.
  `AiReviewerReviewBgConcurrencyTests`'s two lock tests updated to lock/
  dispatch on `(pr_key, episode)` tuples, and
  `test_lock_is_released_after_completion_so_next_dispatch_can_run` now
  also asserts the `(pr_key, episode)` entry is removed from
  `_ai_reviewer_pr_locks` after the thread finishes (item 8's own bounded-
  growth acceptance criterion), replacing its previous (now-invalid, since
  cleanup deletes the entry) "reacquire the same lock object" polling
  trick with a direct wait-for-`calls`-then-wait-for-cleanup sequence.
- New `AiReviewerEpisodeRaceTests` class (2 tests) — drives the REAL
  `_ai_reviewer_poll_repo → _ai_reviewer_review_bg → _ai_reviewer_review_run`
  chain end to end (none of the three mocked out, only `_gitea_api`/
  `_gitea_api_raw`/`teams.roster`/`teams.review_pr_diff` faked), with a
  `threading.Event`-gated fake diff-fetch that blocks the FIRST (episode 1)
  call until the test explicitly releases it — simulating "the previous
  episode's review thread is still in flight." Proves, per the spec's own
  acceptance criteria:
  - `test_new_episode_is_dispatched_not_dropped_while_stale_episode_in_flight`:
    while episode 1's thread is blocked mid-flight, a label-removed then
    label-re-added poll pair dispatches a real episode-2 thread (asserted
    by a second diff-fetch call actually happening, not just the state
    file saying `episode: 2`); episode 2 completes normally; episode 1 is
    then released and its completion is confirmed to be a no-op (state
    still reflects episode 2's own `reviewed_at`/`attempts`/`last_error`
    unchanged).
  - `test_locks_dict_does_not_grow_unbounded_across_episodes`: the stale
    episode's `(pr_key, 1)` lock entry is removed from
    `_ai_reviewer_pr_locks` once its thread finishes.
  - **Verified this harness actually tests the fix, not just the code
    path**: temporarily reverted `_ai_reviewer_review_bg()`'s lock key back
    to plain `pr_key` (no episode) and reran
    `AiReviewerEpisodeRaceTests` — `test_new_episode_is_dispatched_not_
    dropped_while_stale_episode_in_flight` failed exactly as expected
    ("episode 2's review thread was never dispatched -- dropped by the
    stale (pr_key, episode-1) lock"), then restored the fix and confirmed
    all 83 tests in the file pass again.

### `tests/test_team_frontend.js`
- Two new direct-call unit tests (matching the existing `c.call(...)`
  style already used elsewhere in this file, e.g. the human-message test):
  - `teamFeedEventKindClass classifies a trailing empty-meta lead tool_use
    as pending-classification for every non-terminal status` — covers
    `'idle'`/`'running'`/`'blocked'`, `'blocked'` being the exact
    adversarial case the reviewer found.
  - `teamFeedEventKindClass still classifies a trailing empty-meta lead
    tool_use as finish for genuinely terminal statuses` — covers
    `'finished'`/`'error'`, unchanged.

## Item 20 — full audit table
WCAG AA thresholds applied: 4.5:1 for normal text, 3:1 for large-or-bold
text (~18.66px+, or ~14.66px+ bold) — none of the pairs below are large
enough or bold enough at a small-enough size to qualify for the 3:1
threshold, so 4.5:1 was applied uniformly. Ratios computed independently
from the actual hex values (WCAG relative-luminance formula), not taken
from any in-file comment.

| Selector(s) | Background | Text color | Ratio | Result |
|---|---|---|---|---|
| `.pill` (default), `.pill.code-pill`, `.wizard-actions .secondary`, `.wizard-check-row.pill-choice` (default) | `#2a2a2a` | `#aaa` | 6.18:1 | pass |
| `.pill.active`, `.wizard-actions .primary`, `.wizard-check-row.pill-choice:has(input:checked)`, `.card button`, `.clone-form button`, `.new-project-row button` (same green-button family as `.team-btn`/`.deploy-btn`, already fixed by PR #12 — not re-touched, listed here only to confirm the shared pairing is still sound) | `#34c759` | `#111` | 8.51:1 | pass |
| `.badge` (bare), `.team-feed-filter button.active`, `.wizard-step.active` | `#16324a` | `#4da6ff` | 5.17:1 | pass |
| `.badge.taiga-ram`, `.badge.gitea-resources` | `#16324a` | `#66d9ff` | 8.14:1 | pass |
| `.pill.code-pill.active`, `.smoke-btn` | `#4da6ff` | `#111` | 7.39:1 | pass |
| `.team-feed-filter button` (default) | `#1c1c1c` | `#aaa` | 7.34:1 | pass |
| `.team-status-strip.status-running`, `.upload-wizard-btn` | `#1c1c1c` | `#4da6ff` | 6.67:1 | pass |
| `.team-status-strip.status-blocked`, `.team-status.status-blocked` (dead CSS, unused — see note) | `#1c1c1c` | `#ffb648` | 9.77:1 | pass |
| `.team-status-strip.status-finished`, `.team-status.status-finished` (dead CSS) | `#1c1c1c` | `#34c759` | 7.68:1 | pass |
| `.team-status-strip.status-error`, `.team-status.status-error` (dead CSS) | `#1c1c1c` | `#ff6b6b` | 6.14:1 | pass |
| `.wizard-step` (default, non-active, non-done) | `#2a2a2a` | `#666` → **fixed to `#aaa`** | 2.50:1 → **6.18:1** | **FAIL → fixed** |
| `.wizard-step.done` | `#2a2a2a` | `#34c759` | 6.47:1 | pass |
| `.wizard-pick-row button` | `#2a2a2a` | `#eee` | 12.37:1 | pass |
| `.card .back` (clickable `<span onclick=...>`, not an anchor) | `#1c1c1c` | `#888` | 4.81:1 | pass |

**`.wizard-step.disabled`** (`opacity: 0.4`) was not separately computed —
WCAG's own inactive-UI-component exemption (SC 1.4.3 / 1.4.11) applies to
disabled controls, and opacity-blended contrast against a non-solid
effective background is outside "compute from the actual hex values" as
the spec's own approach describes it.

### Considered and excluded (out of scope per spec's own "Non-goals")
- **Form inputs/selects** (`.team-add-member select`, `.team-lead-picker
  select`, `.clone-form input`, `.smoke-check-row input`, `.card input`,
  `.team-textarea`, `.team-interject-textarea`, `.team-escalation-form
  textarea`) — all pair `#eee` text on `#1c1c1c`/`#111` backgrounds
  (14.69:1 / 16.28:1 respectively, both comfortably passing regardless);
  excluded as form fields, not "buttons, pills, badges, status strips" per
  the spec's own scoping, matching item 20's original backlog framing.
- **`.team-configure-btn`, `.team-feed-toggle`** — both literal `<a>`
  anchor tags (confirmed in the JS: `<a class="team-configure-btn"
  onclick=...>`), styled identically to the global `a { color: #4da6ff }`
  link rule with `background: none` — links, explicitly out of scope.
  (`.card .back` was previously and incorrectly grouped with these two —
  see correction below; it is not an anchor and was not actually excluded
  from the audit, it's now listed in the table above.)
- **`.wizard-warn`, `.team-tier-3-caveat`, `.team-escalation-proposal-box`**
  — static informational/warning text blocks, not clickable controls; same
  category as body text, which the spec's Non-goals excludes. (`.wizard-warn`'s
  background is also a translucent `rgba(255,193,7,0.1)` over the page
  background, not a flat hex pair a WCAG ratio can be computed from
  directly.)
- **`.deploy-msg`/`.team-msg`/`.smoke-check-msg`/`.clone-err`/`.clone-status`/
  `.team-validation-error`/`.wizard-body p.err`/`.card .err`/`.taiga-err`/
  `.gitea-err`/`.new-project-err`** — plain feedback/error text spans with
  no paired background of their own (sit on the page/card background),
  same body-text category, out of scope.

## Key decisions / tradeoffs
- **Item 8's guard is on the WRITE, not the work.** `_ai_reviewer_record_
  failure()`/the final success save check `episode` before persisting, but
  the diff-fetch/review-generation/comment-post calls themselves still run
  to completion for a stale episode once unblocked — matching the spec's
  own "Thread episode through the whole review-run call chain so a
  completion **write** only applies if it's still for the current episode"
  (not "abort the stale run early"). A stale episode can still post a
  (superseded, extra) PR comment if it happens to complete after being
  unblocked; the spec's Fix section doesn't ask for early abort, and adding
  one would be scope beyond what was diagnosed.
- **`_ai_reviewer_pr_lock_for()`'s signature widened to `key` (untyped)**
  rather than `key: str | tuple`, matching the spec's own literal
  instruction ("only its type hint... needs updating for accuracy") —
  Python's runtime behavior needed no change either way since `dict`
  already treats `str`/`tuple` keys identically.
- **Dead CSS left in place** (`.team-status.status-*`, unreferenced by any
  JS render path — confirmed via `grep -n "'team-status'"` returning no
  hits). Computed and listed in the audit table anyway since it's still
  live, shipped CSS with an identical failure profile to its `-strip`
  successor, but removing dead code is outside this spec's scope (a
  drive-by cleanup item 20 never asked for).

## Deviations from spec
None. Piece 1 implemented exactly per docs/spec.md's "Fix" section's
near-final code (episode threading, lock-keying, cleanup). Piece 2 is the
exact single-line + comment change the spec specifies. Piece 3 found
exactly one failure (`.wizard-step`), fixed using an existing in-file
passing precedent, matching the spec's required approach; the full
checked-pairs table (including zero-failure pairs) is above, satisfying
the spec's "if zero additional failures are found... say so plainly...
with the full list of pairs checked" clause even though one failure WAS
found.

## Known limitations
- Item 8: a stale (superseded) episode's review-generation/comment-post
  work still runs to completion once unblocked (see "Key decisions"
  above) — it just can no longer clobber the newer episode's persisted
  state. In the pathological case where a stale episode's diff-fetch is
  still blocked past the point where its host API call would otherwise
  time out, no new behavior was introduced here; existing timeout/error
  handling in `_ai_reviewer_review_run()` is unchanged.
- Item 20: `.wizard-step.disabled`'s opacity-blended effective contrast
  was not computed (see audit table note above) — WCAG's own exemption for
  inactive controls applies.

## How to verify locally
```
# Full existing suite plus this cycle's new/updated tests (including the
# fix-pass additions):
python3 -m unittest discover -s tests
# Ran 1198 tests ... OK

# Just this cycle's touched/added backend tests:
python3 -m unittest tests.test_ai_reviewer -v
# Ran 85 tests ... OK (83 from the original cycle + 2 new from this fix
# pass: the AC5 backward-compat regression test and
# AiReviewerEpisodeAtomicWriteRaceTests)

# Frontend tests (extracts the real, rendered <script> from
# app.render_page() via a Python subprocess, runs it in a Node vm sandbox):
node tests/test_team_frontend.js
# ALL PASS (106/106) -- unchanged by this fix pass (no frontend code touched)

# Regression-proof for piece 1's race harness (confirms the new tests
# actually exercise the fix, not just the code path): temporarily revert
# `lock_key = (pr_key, episode)` back to `lock_key = pr_key` in
# _ai_reviewer_review_bg(), rerun
# `python3 -m unittest tests.test_ai_reviewer.AiReviewerEpisodeRaceTests -v`
# -- test_new_episode_is_dispatched_not_dropped_while_stale_episode_in_flight
# fails with "episode 2's review thread was never dispatched -- dropped by
# the stale (pr_key, episode-1) lock". Restore the fix and rerun to confirm
# green again.

# Regression-proof for the follow-up fix pass's Defect 1 fix: temporarily
# revert `_ai_reviewer_save_if_current_episode()` (app/app.py ~1397) back to
# the pre-fix "unlocked check, then separate call to
# _save_ai_reviewer_state_entry()" shape, rerun
# `python3 -m unittest tests.test_ai_reviewer.AiReviewerEpisodeAtomicWriteRaceTests -v`
# -- fails with `1 != 2` on `s[pr_key]["episode"]` (the exact backwards-
# episode clobber Defect 1 describes). Restore the fix and rerun to confirm
# green again.

# Manual smoke test (piece 1, requires a live Gitea instance with
# AI_REVIEWER_ENABLED=1):
#   1. Open a PR, add the AI_REVIEWER_LABEL. Watch the poll dispatch a
#      review (comment posted, state's "episode": 1).
#   2. While that review is still generating (e.g. against a slow model),
#      remove and re-add the label.
#   3. Confirm a SECOND review comment eventually posts (episode 2's
#      dispatch was not dropped), and the state file's final "episode"
#      reflects 2, not clobbered back to episode 1's own completion.

# Manual visual check (piece 3): open the project-creation wizard's step
# indicator (.wizard-step-indicator) -- the not-yet-reached step pills
# should render legibly (light gray, not dark gray-on-gray).
```
