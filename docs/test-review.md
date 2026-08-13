# Test & Review: Local git hosting UI + CI/CD (Gitea) — part 2c, part 1: poll-based sync-on-push

## Scope
Covers all 10 acceptance criteria in `docs/spec.md` for the poll-based
sync-on-push feature: the repo-map write in `create_project()`, the
throttled `/status`-piggybacked poll (`_gitea_poll_if_due`/`_gitea_poll_one`),
the SHA-diff gate, the sync dispatch (`_gitea_sync_bg`/`_gitea_sync_run`),
and the low-privilege `scripts/gitea-sync-project.sh` safety logic (dirty
check, fast-forward-safety check, `git merge --ff-only`). Given this cycle's
explicit "safety, not features" framing, most of the added scrutiny went
into adversarially attacking `scripts/gitea-sync-project.sh`'s safety
guarantees with constructed repo states and real concurrency, beyond what
the developer's own test suite covers.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `create_project()` success writes repo-map entry with null sync fields | Automated (`tests/test_gitea.py::test_happy_path_writes_repo_map_entry_with_null_sync_fields`) | pass | Ran `python3 -m unittest tests.test_gitea -v`, all pass |
| 2 | Repo-map write failure doesn't fail `create_project()` | Automated + direct read of `app/app.py:772-783` (`try/except OSError: pass`) | pass | Test passes; code confirms `return True, ""` is unconditional after the try/except |
| 3 | `_gitea_api` called once per repo-map entry when poll is due | Automated (`test_gitea_poll.py::GiteaPollIfDueTests`) + own real-concurrency stress test (30 threads calling `_gitea_poll_if_due` simultaneously) | pass | Existing tests pass; my own script: 30 concurrent `/status`-style calls → exactly 1 `_gitea_api` call total |
| 4 | Throttle holds across repeated `/status` calls before interval elapses | Automated + real-concurrency stress test (above) | pass | Same run as #3 — throttle is genuinely race-safe under real threading, not just sequential mock timestamps |
| 5 | No polling when `GITEA_ENABLED=False` or Gitea not running | Automated (`GiteaPollIfDueTests.test_gitea_disabled_*`, `test_gitea_on_false_*`) | pass | `python3 -m unittest tests.test_gitea_poll -v` |
| 6 | SHA-match skips sync attempt (no subprocess) | Automated + own direct check with a `subprocess.run` spy | pass | My own script: real `subprocess.run` never invoked when polled SHA equals stored `remote_sha` |
| 7 | SHA-diff → fast-forward when clean+ancestor; repo-map updated after | Automated (mocked in `test_gitea_poll.py`) + real git ops (`test_gitea_sync_project.py::test_clean_fast_forwardable_syncs_and_updates_working_tree`) | pass | Both suites pass; `HEAD` in dest matches origin exactly after sync |
| 8 | Dirty working copy → skip, uncommitted changes byte-for-byte intact, `remote_sha` still updated | Automated (`test_dirty_working_tree_is_skipped_and_left_byte_for_byte_intact`) + adversarial: staged-but-uncommitted change, untracked-only file, concurrent uncommitted edit mid-race-window | pass | See "Adversarial safety testing" below — all variants correctly caught as dirty, nothing touched |
| 9 | Diverged/ahead local HEAD → skip, no destructive op, commit still reachable | Automated (`test_diverged_local_commit_is_skipped_and_not_lost`, `test_local_ahead_only_...`) + adversarial: local merge commit, concurrent local commit landing mid-fetch | pass | See "Adversarial safety testing" below |
| 10 | Non-200 branch lookup → skipped without raising, repo-map untouched | Automated (`GiteaPollOneTests.test_non_200_status_skipped_without_raising`) + own repro of a related but uncovered case (malformed 200 body) | pass (AC) / **gap found**, see Findings #1 | AC itself passes; adjacent edge case (200 with non-dict body) raises uncaught `AttributeError` — see Findings |
| 11 (edge, not separately numbered in AC but spec-relevant) | Full suite passes, no real Docker/network calls in new test files, `test_gitea_sync_project.py` needs no root | Automated + direct verification | pass | `python3 -m unittest discover -s tests -v` → 213/213 pass; `grep` for docker/http/urllib in both new test files found only comments; ran as uid 1001 (`dev`, non-root) directly, all 10 script tests pass |
| 12 | `/status` includes `gitea_sync` when repo-map entry exists, omits (not null) otherwise | Automated (`test_status_includes_gitea_sync_...`, `test_status_omits_gitea_sync_...`) | pass | `python3 -m unittest tests.test_gitea -v` |
| 13 | Frontend `.sub` suffix appears only for skip states, other rows unaffected | Manual, via `render_page()`'s actual rendered `<script>` executed in a Node `vm` sandbox (not a hand-copied snippet) | pass | See "Frontend verification" below |

## Adversarial safety testing (scripts/gitea-sync-project.sh)

Per the explicit instruction to try to break the dirty-check and
fast-forward-safety check, I ran the real script (not mocked) against
constructed repo states beyond the developer's own test file:

1. **Staged-but-uncommitted change** (`git add` without commit) — correctly
   caught by `git status --porcelain` → `skipped-dirty`, file left as staged.
2. **Untracked-only file** (no modified tracked files at all) — also
   correctly caught as dirty (porcelain output is non-empty for untracked
   files too) → `skipped-dirty`, nothing merged.
3. **Detached HEAD, clean, ancestor of new remote** — `git merge --ff-only`
   succeeds and moves the detached `HEAD` forward. Not destructive (old
   position stays in reflog, no commits lost) but worth noting: this is a
   real behavior the spec doesn't explicitly discuss. See Findings (nit).
4. **Local merge commit that is "ancestor-adjacent" but not actually an
   ancestor** (a real `git merge --no-ff` of a local feature branch into
   `main`) — `git merge-base --is-ancestor` correctly reports false →
   `skipped-diverged`; the merge commit and the feature branch's file both
   remain untouched.
5. **Concurrent commit landing between `git fetch` and the dirty/ancestor
   checks** (simulated with a real background process racing a real `sleep`
   window inserted into a copy of the script) — the new local commit is
   picked up by the checks that run after it lands, correctly producing
   `skipped-diverged`; no data lost.
6. **Concurrent uncommitted edit landing between the ancestor check and the
   `git merge --ff-only` call, on a file the incoming commit also touches**
   — `git merge --ff-only` itself refuses at apply time ("Your local changes
   ... would be overwritten by merge... Aborting"), exits 1, and the
   uncommitted edit is left completely intact. This confirms the safety
   doesn't just rely on the earlier point-in-time checks — git's own merge
   machinery re-validates the actual working tree at apply time, closing
   the race window genuinely, not just probabilistically.
7. **Concurrent uncommitted edit to an *unrelated* file in the same race
   window** — `git merge --ff-only` succeeds (the incoming commit doesn't
   touch that file), and the uncommitted edit survives untouched alongside
   the newly-merged content. Confirms ff-only doesn't clobber anything
   outside its own diff.
8. **True concurrent-thread race on the per-project lock** — 20 real
   Python threads simultaneously calling `_gitea_sync_bg` for the same
   `owner_repo` (not the developer's own pre-acquire-then-assert-dropped
   test, but genuine concurrent dispatch) resulted in exactly 1
   `subprocess.run` call. Lock is genuinely race-safe.
9. **True concurrent-thread race on the repo-map file** — 100 threads
   writing 100 different entries concurrently via `_save_gitea_repo_map_entry`
   produced all 100 entries with zero lost updates (the single
   `_gitea_map_lock` around the whole read-modify-write cycle prevents the
   lost-update race a per-key lock scheme would have been vulnerable to).

None of these adversarial cases produced data loss, a silent history
rewrite, or a double-apply. `git merge --ff-only` (never `git reset --hard`,
never a bare `git merge` that could fabricate a merge commit) is confirmed
as the actual operation used, and it is confirmed to be a genuine loud
no-op/refusal — not just by reading the script, but by constructing states
that specifically try to defeat it.

## Frontend verification (no docs/design.md — developer judgment call)

Confirmed the developer's judgment that this didn't need a full design
pass. Extracted the *actual* rendered `<script>` block via
`appmod.render_page()` (not a hand-copied excerpt — this catches any
Python-level string-escaping mismatch a naive regex-on-raw-source
extraction would miss, which I hit and worked around) and ran it in a
Node `vm` sandbox with stubbed DOM. Calling the real `row()` function:
- `gitSync.state === 'skipped-dirty'` → `...sub">stopped · sync skipped: local changes</div>`
- `gitSync.state === 'skipped-diverged'` → `...sub">stopped · sync skipped: local commits ahead</div>`
- `gitSync.state === 'synced'` or `gitSync` omitted → plain `running`/`stopped`, unchanged
- The `host` row (called with no `gitSync` argument at all, matching the
  real call site) renders identically to its pre-existing behavior.

This is genuinely minimal: one suffix appended to an existing `.sub` text
node, reusing the existing render path, no new CSS/DOM/badge system, and
every other `row()` call site is unaffected because the new parameter is
purely additive and trailing. The orchestrator's call to skip a
`docs/design.md` pass for this holds up.

## Regression check
Full existing suite: `python3 -m unittest discover -s tests -v` — **213/213
pass** (matches the implementation doc's claimed count: 173 pre-existing +
40 new/extended this cycle). Frontend regression suite:
`node tests/test_singleton_toggle_frontend.js` — **15/15 pass**, unmodified.
`bash -n scripts/gitea-sync-project.sh`, `bash -n install.sh`,
`python3 -m py_compile app/app.py` — all clean.

## Defects found
None blocking. Testing pass is clean — proceeding to review.

---

## Spec coverage
All 10 acceptance criteria in `docs/spec.md` are implemented and covered by
tests (automated where possible, plus my own adversarial extensions for the
sync-safety logic specifically called out as needing extra scrutiny). No
criterion was found unimplemented or untested.

- Repo-map write on `create_project()` success, null sync fields — ✅ implemented, tested (#1).
- Repo-map write failure non-fatal — ✅ implemented, tested (#2).
- One `_gitea_api` call per repo-map entry per due poll — ✅ implemented, tested, and independently stress-tested under real concurrency (#3).
- Throttle holds across repeated calls — ✅ implemented, tested, stress-tested (#4).
- No polling when disabled/off — ✅ implemented, tested (#5).
- SHA-match skips sync (no subprocess) — ✅ implemented, tested, independently verified with a real subprocess spy (#6).
- SHA-diff → correct fast-forward + repo-map update — ✅ implemented, tested with both mocked and real git (#7).
- Dirty → skip, byte-for-byte intact, `remote_sha` still updated — ✅ implemented, tested, and adversarially extended (staged, untracked-only, race-window cases) (#8).
- Diverged/ahead → skip, no destructive op — ✅ implemented, tested, and adversarially extended (local merge commit, race-window concurrent commit) (#9).
- Non-200 → skipped without raising, repo-map untouched — ✅ implemented, tested for the literal AC. **Adjacent gap found** for a malformed-but-200 response — see Findings #1 (does not invalidate this AC, which only concerns status codes).
- Full suite passes, no real Docker/network in new tests, no root needed — ✅ verified directly.
- `/status` includes/omits `gitea_sync` correctly — ✅ implemented, tested (#12).

The developer's flagged deviation (`_gitea_sync_run` factored out of
`_gitea_sync_bg`) was independently verified, not just read: my own 20-thread
concurrent-dispatch test against the real (unmocked) locking/threading code
confirms the factoring has no behavioral difference from the spec's stated
intent (per-project non-blocking lock, background thread, fast return) —
exactly 1 real sync attempt out of 20 concurrent dispatches for the same
project.

## Findings (most severe first)

### 1. Unhandled exception when a Gitea branch-lookup response is 200 but not a JSON object — should-fix
- File: `app/app.py:721` (`_gitea_poll_one`), called from `app/app.py:2640`
  (`_gitea_poll_if_due(gitea_on)` inside `do_GET`'s `/status` handler, no
  surrounding `try/except`)
- Issue: `remote_sha = (resp.get("commit") or {}).get("id", "")` assumes
  `resp` is always a dict. `_gitea_api` only converts network-level errors,
  HTTP error statuses, and JSON-parse failures into `ConnectionError`
  (caught by `_gitea_poll_one`) — a **200** response whose body parses as
  valid JSON but isn't an object (e.g. literal `null`, or an array) passes
  straight through as `resp`, and `resp.get(...)` then raises
  `AttributeError`. Reproduced directly: monkeypatching `_gitea_api` to
  return `(200, None)` and calling `_gitea_poll_one` raises
  `AttributeError: 'NoneType' object has no attribute 'get'` uncaught.
  `_gitea_poll_if_due`'s `try/finally` only releases the poll lock in
  `finally` — it does not catch the exception, so it propagates into
  `do_GET`, which has no generic exception handler either (confirmed by
  reading the `Handler` class and `do_GET`/`do_POST`; only
  `_read_json_body` has a narrow local `try/except`). The default
  `ThreadingHTTPServer` behavior for an unhandled exception in a handler
  thread is to print a traceback and reset that connection — it does not
  crash the whole server, but that specific `/status` request fails for
  the client.
- Failure scenario: a Gitea instance (or a reverse proxy/misconfiguration
  in front of it) that returns a 200 with an unexpected body shape for one
  registered project's branch-lookup endpoint. Because the `for` loop in
  `_gitea_poll_if_due` (`app/app.py:665-666`-ish, iterating
  `_load_gitea_repo_map().items()`) has no per-entry exception handling,
  the exception aborts the *entire* poll pass partway through — every other
  registered project after the malformed one in iteration order is also
  skipped for that interval, not just the offending one. This recurs every
  `GITEA_POLL_INTERVAL_SECONDS` for as long as the malformed-response
  condition persists, degrading (but not destroying) sync availability for
  every Gitea-backed project, not only the misbehaving one. Not data loss,
  not a literal acceptance-criterion violation (the spec's own "Edge cases"
  and AC #10 only specify non-200 status handling), and consistent with the
  spec's own stated risk tolerance ("worst case: sync doesn't happen when
  it safely could have") — but the blast radius (all projects, not just the
  one with the bad response) is broader than what the spec's edge-case
  analysis anticipated, and a bare `except Exception` (or narrowing to
  `except (AttributeError, TypeError, KeyError)`) around the body inside
  `_gitea_poll_one`, or around each iteration of the loop in
  `_gitea_poll_if_due`, would close this cleanly without changing any
  currently-tested behavior.

### 2. Detached HEAD is silently fast-forwarded — nit
- File: `scripts/gitea-sync-project.sh:99-103` (the ancestor check / `git
  merge --ff-only`)
- Issue: if `PROJECTS_DIR/<name>` happens to be in a detached-HEAD state
  (e.g. an agent session intentionally checked out an older commit to
  inspect/test something) and that detached HEAD is a clean ancestor of the
  newly fetched ref, `git merge --ff-only` succeeds and moves the detached
  HEAD forward — verified directly (test 3 in my adversarial run). Not
  destructive (the old position remains in the reflog, no commit is lost,
  and this satisfies the spec's literal "clean + ancestor → fast-forward"
  rule exactly as written), but it's a case the spec's prose doesn't
  explicitly call out, and it means an agent session that deliberately
  detached HEAD to look at history could have that state silently moved
  out from under it. Not a blocker — no data loss, and arguably correct
  per the letter of the spec — but worth a documentation note or an
  explicit decision in a follow-up if it turns out to surprise anyone in
  practice.

## Follow-ups (non-blocking)
- Consider a bare-minimum `except Exception` guard around the per-entry
  body of `_gitea_poll_one` (or around each iteration in
  `_gitea_poll_if_due`'s loop) so one malformed response can't suppress
  polling for every other registered project in the same pass (Finding #1).
- Consider explicitly deciding (and documenting) whether a detached-HEAD
  working copy should be fast-forwarded like any other clean+ancestor case,
  or treated as an additional skip condition (Finding #2) — current
  behavior is safe but undiscussed.
- The `at` (`sync_at`) timestamp is sent in `/status`'s `gitea_sync` field
  but not currently surfaced anywhere in the UI (only `state` drives the
  `.sub` suffix). Consistent with the spec explicitly leaving exact UI
  treatment to developer discretion — not a defect, just noting in case a
  future design pass wants to use it (e.g. a tooltip with "skipped 3m ago").

## Overall verdict
**Approve with follow-ups.** All 10 acceptance criteria are implemented and
verified — most of them with hands-on adversarial testing beyond the
developer's own suite, specifically targeting the sync-safety logic this
cycle's core value proposition depends on (dirty-check, fast-forward-safety
check, per-project lock, poll throttle). I was unable to break the
data-loss/history-rewrite guarantees despite deliberately constructing
staged-only, untracked-only, detached-HEAD, local-merge-commit, and
genuine-concurrent-race scenarios against the real script and real threads
— `git merge --ff-only` held up as a true loud-refusal-or-clean-no-op in
every case, including races that landed inside the gap between the
Python-level checks and the actual merge. The one real bug found (Finding
#1, an uncovered exception path on a malformed-but-200 Gitea response) is
should-fix, not must-fix: it doesn't violate any literal acceptance
criterion, doesn't risk data loss, and degrades gracefully in the direction
the spec already accepts ("sync doesn't happen when it safely could have")
— it just has a wider blast radius (all registered projects' polling for
that interval, not only the malformed one) than the spec's edge-case
analysis anticipated. The frontend judgment call to skip a `docs/design.md`
pass held up under direct verification of the actual rendered script.
