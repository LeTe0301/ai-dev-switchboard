# Test & Review: E2E regression-verification follow-ups, round 5 (items 29-v2, 30-v2, 34, 35)

## Scope
Independent verification of all four fixes against `docs/spec.md`'s acceptance
criteria: item 29 (v2)'s ACL grant + except-clause-ordering fix, item 30
(v2)'s longer/smarter `taiga-up.sh` retry + `taiga_run()` timeout arithmetic,
item 34's guarded-restart-block relocation, and item 35's `/team/stop` gate
widening. Plus independent verification of the developer's two disclosed
test-harness claims (the `test_deploy_target.py` end-marker fix, and the 3
`test_teams_grounding` failures attributed to an untracked `CLAUDE.md`).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Item 29: except-clause order — `PermissionError` caught before `OSError` | Read actual diff (`app/taiga_board.py`) | pass | New `except PermissionError:` clause sits immediately before the pre-existing `except OSError:` — confirmed by direct diff read, not the developer's claim |
| 2 | Item 29: `install.sh` `runtime.env` now includes `SVC_USER`, script reads it (not hardcoded) | Read diff (`install.sh`, `scripts/taiga-configure-push.sh`) | pass | `runtime.env` gains `SVC_USER=$SVC_USER` (line ~506, `$SVC_USER` already defined at line 240, well before); `taiga-configure-push.sh` greps `^SVC_USER=` from `/etc/ai-dev-switchboard/runtime.env`, only falling back to the literal `"switchboard-svc"` if the file/key is absent |
| 3 | Item 29: except-clause ordering actually takes effect (not just present) | `python3 -m unittest tests.test_teams_board.LoadConfigPermissionTests -v` | pass | 3/3 tests pass; monkeypatches `open()` to raise `PermissionError` vs. plain `OSError` vs. missing-file, confirms 3 distinct correct outcomes |
| 4 | Item 29: ACL grant/permission-denied UX (getfacl grant, `sudo -u <svc>` read, board_read success, actionable warnings) | Manual — not exercisable | not testable in this sandbox (root-only, no second unprivileged user) | Same disclosed limitation as `docs/implementation.md`; code read confirms both warning branches (`setfacl` missing / `setfacl` fails) and the grant call itself are present and correctly ordered after `chmod 600` |
| 5 | Item 30: retry-loop attempt count / backoff values match claim | Read `scripts/taiga-up.sh` directly, hand-summed | pass | 5 attempts, backoff 10→20→40→80 (doubling), sleeps only between attempts 1-4 (guarded by `attempt -lt MAX`) = 150s total sleep, confirmed by direct arithmetic, not trusted from implementation.md |
| 6 | Item 30: `taiga_run()`'s `"up"` timeout raised to a value that "comfortably" exceeds 150s | Read `app/app.py:2696-2711` + independent judgment | **fail — see Finding 2** | Raised to 180s (30s of slack across 5×`up -d` + 5×`ps` + 4×`rm -f` = 14 real subprocess calls, ~2.1s/call average) — thin for the very failure mode being retried (a struggling Docker daemon); see Finding 2 |
| 7 | Item 30: `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` defaults to `0`/off | Read diff | pass | `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION="${TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION:-0}"` |
| 8 | Item 30: `bash -n`/shellcheck clean | `bash -n scripts/taiga-up.sh`; `shellcheck scripts/taiga-up.sh` | pass | both clean |
| 9 | Item 30: existing retry-loop test harness still exercises real behavior | `python3 -m unittest tests.test_taiga.TaigaRunTests tests.test_taiga_up_retry -v` | pass | `test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop` asserts `timeout==180`; `test_taiga_up_retry.py` (unmodified) still passes against the new retry logic since it asserts call counts/messages, not durations |
| 10 | **New finding**: frontend "starting…"→"error" UI timer for Taiga stayed at 90s while the backend's blocking `taiga_run("up")` can now legitimately take up to 180s | Read `app/app.py:3208-3255`, `4707-4723`, `4461-4463` directly | **fail — see Finding 1** | `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` is still hardcoded `90000`, untouched by this diff (confirmed via `git diff main -- app/app.py`); `toggle()` sets `st.pending.startTime` at click time and `performAction()`'s `fetch()` has no abort/timeout of its own — the UI flips to "error" purely on elapsed wall-clock time, independent of whether the backend POST is still in flight |
| 11 | Item 34: guarded-restart block moved (not duplicated) | `grep -n "Guarded restart -- refuses to restart\|LIVE_SESSIONS="` install.sh | pass | exactly one occurrence of each |
| 12 | Item 34: block now sits right before `echo "== Done =="`, after every `--with-*` block | `grep -n` line numbers | pass | block at line 918-936, `echo "== Done =="` at line 937; last `--with-*` block (deploy-target) ends before it |
| 13 | Item 34: no `if [ "$UPDATE" -eq 1 ]` wrapper remains around the block | `grep -n "UPDATE.*-eq 1"` install.sh | pass | only remaining occurrence (line 126) is the unrelated, pre-existing `--update` git-pull block, not the restart block |
| 14 | Item 34: live-session detection/defer logic byte-for-byte preserved | `python3 -m unittest tests.test_install_update.GuardedRestartBlockTests -v` | pass | 3/3 pass — no-live-session restarts, multi-session names all sessions, unrelated personal session still counted |
| 15 | Item 34: `bash -n`/shellcheck clean | `bash -n install.sh`; `shellcheck install.sh` | pass | clean except 2 pre-existing, unrelated notes (SC2015 line 70, SC2001 the moved `sed` call — both predate this change) |
| 16 | Item 35: `/team/stop` on a `finished` run actually tears down session + worktrees | `python3 -m unittest tests.test_team_routes.TeamStopEndpointTests -v` | pass | `test_stop_on_finished_team_now_actually_cleans_up_and_allows_restart` — real HTTP request against a real run, asserts `session_removed`/`worktrees` present, no `message` key, `tmux_has(session)` false afterward |
| 17 | Item 35: same for `escalated_max_rounds` | same run | pass | `test_stop_on_escalated_max_rounds_team_now_actually_cleans_up` |
| 18 | Item 35: follow-up `/team/start` on the same project succeeds immediately after | same test, continued | pass | `status2 == 200` for the second `/team/start` call in `test_stop_on_finished_team_now_actually_cleans_up_and_allows_restart` |
| 19 | Item 35: existing `running`/`blocked_ask_user`/`blocked_board_write` behavior unchanged | Code trace (gate is a strict superset of the old one) + existing tests | pass | `test_stop_mid_delegate_terminates_real_subprocess_promptly` (running), `test_stop_on_blocked_board_write_now_actually_stops` (blocked_board_write) both still pass; `blocked_ask_user` code path is untouched (same `if run is None` check never fires for it, identical to before) |
| 20 | Item 35: `run is None` still returns the same no-op message | `test_stop_with_no_team_ever_started_is_idempotent_ok` | pass | unchanged |
| 21 | Item 35: widening the gate doesn't unsafely call `stop_team()` for a status the old gate was protecting | Read `app/teams.py:4029-4062` (`stop_team()`) directly — confirmed `app/teams.py` has **zero diff** in this round | pass | docstring + body confirmed genuinely unconditional/safe for every status (`session_removed`, per-agent worktree teardown with dirty/error entries preserved, status only overwritten if non-terminal); no side effect specific to a status the old tuple excluded |
| 22 | `tests/test_deploy_target.py`'s claimed end-marker collateral-damage fix | Read diff + reasoning | pass | old end marker `echo "== Done =="` would now also pull in the relocated (and `$RUN_USER`-referencing) guarded-restart block into a harness that never supplies `$RUN_USER`; new end marker (`"# Guarded restart -- refuses to restart"`) stops before it — logically sound, and `test_deploy_target.py`'s 32 tests pass |
| 23 | Claimed 3 pre-existing `test_teams_grounding` failures caused by an untracked `CLAUDE.md`, not a regression | `git status` (confirms `CLAUDE.md` untracked, no diff); moved the file aside and re-ran the 3 failing tests plus their siblings | pass | with `CLAUDE.md` present: 3 failures (exact same tests named in implementation.md); with `CLAUDE.md` moved out of the repo root: same 10 tests, **all pass** — independently confirms root cause, not just accepted on faith |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` (my own run,
this session) — **1213 tests, 3 failures** (identical count/names to
implementation.md's claim). All 3 independently confirmed attributable to the
untracked `CLAUDE.md` file (test case 23 above), not a regression from this
round's diff. No `test_teams_headless` flake observed in my run (it appears
implementation.md's mention of that was itself non-deterministic/pre-existing,
consistent with its own description).

Type-check/lint: `bash -n` clean on all three touched shell files
(`install.sh`, `scripts/taiga-configure-push.sh`, `scripts/taiga-up.sh`);
`shellcheck` clean except the 2 pre-existing notes noted in test case 15;
`python3 -m py_compile app/app.py app/taiga_board.py` clean.

## Defects found
None that block the testing pass — all automated tests pass, the full suite
regression is clean once the pre-existing `CLAUDE.md`-caused failures are
accounted for, and every acceptance criterion in `docs/spec.md` that's
exercisable in this sandbox is genuinely covered and passing. Proceeding to
the review pass. (Two issues surfaced during my own independent review of the
diff beyond the spec's literal acceptance criteria — see Findings 1 and 2
below, not defects in the "testing pass" sense since nothing here fails an
existing test or a stated acceptance criterion, but real gaps found by
reading the actual diff as instructed.)

---

## Spec coverage
- **Fix 1 (item 29 v2)**: fully implemented and covered as far as this
  sandbox allows. Except-clause ordering independently verified (not just
  trusted) — correct. `SVC_USER` plumbing independently verified — correct,
  not hardcoded. The one gap (real ACL grant end-to-end) is genuinely not
  testable here and is honestly disclosed as such by the developer; no
  reason to doubt it given the surrounding code reads correctly.
- **Fix 2 (item 30 v2)**: implemented per spec's code block. Retry
  arithmetic independently re-derived and matches (150s worst-case sleep).
  `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` defaults off, confirmed. **The
  180s backend timeout margin is thin and worth a specific opinion (Finding
  2), and a real, verifiable side effect of raising it — the frontend's
  parallel 90s UI timeout for Taiga was never updated to match (Finding
  1)** — this is the single highest-value gap found in this review: the
  spec's own "Affected areas" section states "No frontend/JS changes needed
  for any of these four," and that assumption turns out to be wrong
  specifically for fix 2.
- **Fix 3 (item 34)**: fully implemented and covered. Block genuinely moved
  (not duplicated), genuinely unconditional, live-session-defer logic
  preserved and tested.
- **Fix 4 (item 35)**: fully implemented and covered. Gate genuinely
  narrowed to `run is None` only; `stop_team()`'s own unconditional safety
  independently re-confirmed by reading its (unmodified) source directly,
  not just its docstring's claim.

## Findings (most severe first)

### 1. Frontend "starting…"→"error" timeout for Taiga was not updated alongside the backend timeout it was explicitly designed to mirror — must-fix
- File: `app/app.py:3208-3219` (`SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs`), vs. `app/app.py:2696-2711` (`taiga_run()`'s new 180s timeout for `"up"`)
- Issue: before this round, both the backend's blocking `taiga_run("up")` call (90s) and the frontend's "give up and show error" timer for the Taiga toggle (`timeoutMs: 90000`) were 90s — and the pre-existing comment at `app/app.py:3208-3213` states this explicitly: *"both kinds keep the same safe 90s upper bound rather than tuning Gitea's down (a safety ceiling, not a performance target)."* This round raised the backend's `"up"` timeout to 180s specifically to give the new 5-attempt/exponential-backoff retry loop (worst case 150s of sleep alone, plus real `docker compose` calls) room to run to completion — but left the frontend's `timeoutMs` at 90000, unchanged, breaking the invariant the original design explicitly relied on. `toggle()` (`app/app.py:4707-4723`) sets `st.pending.startTime = Date.now()` at click time, and `performAction()`'s `fetch()` (`app/app.py:4461-4463`) has no `AbortController`/timeout of its own — the frontend's error display is driven purely by wall-clock elapsed time since the click, completely independent of whether the backend POST is still in flight.
- Failure scenario: exactly the failure mode fix 2 was built for — a real port-bind race or nginx/DNS propagation delay needing multiple retries. At the 90s mark, the frontend flips the Taiga row to `<span class="taiga-err">error</span>` even though the backend is very plausibly still legitimately retrying (it now has up to 180s to do so, and the spec's own stated observation was that real recovery needed "tens of seconds to a couple of minutes"). Two consequences: (a) this reproduces, for the frontend specifically, the exact same "shows broken when it isn't" user-visible symptom the whole verification round exists to eliminate; (b) more seriously, the checkbox is never disabled during "starting"/"error" (confirmed: `row()` at `app/app.py:4345-4368` renders a plain, always-enabled `<input type="checkbox">`), so an operator who sees the false "error" and clicks the toggle again fires a second, concurrent `POST /taiga/on` → a second `taiga_run("up")` — or worse, toggles to "off" → a concurrent `taiga_run("down")` — running in a separate thread (the server is `ThreadingHTTPServer`, confirmed) against the *same* Taiga Docker Compose stack while the first `up` attempt is still mid-retry. That's a real, backend-level race this fix's own design (moving to a longer, patient retry loop specifically to avoid needing operator intervention) was trying to avoid inviting.
- Fix direction (not prescribing the exact number, per this pipeline's own developer-decides-implementation-details convention): raise `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` to something that comfortably covers `taiga_run()`'s new 180s ceiling (e.g. matching it, or exceeding it with the same kind of margin reasoning used for the backend change), or split it out from the shared Gitea value if Gitea's own backend timeout wasn't changed (it wasn't — `taiga_run()`'s `"down"`/`"status"` and the Gitea path all remain 90s, so keeping `SINGLETON_TOGGLE_CONFIG.gitea.timeoutMs` at 90000 is still correct).

### 2. 180s backend timeout leaves ~30s of real margin across 14 real subprocess calls for the exact failure mode being retried — should-fix, worth tightening or documenting
- File: `app/app.py:2696-2711` (`taiga_run()`), `scripts/taiga-up.sh` (retry loop)
- Issue: 180s − 150s of pure `sleep` = 30s of slack for the *other* real work in the loop: 5× `docker compose up -d`, 5× `docker compose ps taiga-gateway`, and 4× `docker compose rm -f taiga-gateway` — 14 real subprocess invocations averaging ~2.1s each if the full 30s is spent evenly. That's workable when Docker is healthy, but this retry loop exists specifically *because* Docker is in a degraded, slow-to-recover state on the affected host (the spec's own words: real recovery needed "tens of seconds to a couple of minutes," and a full daemon restart was the only reliable fix found). It's plausible that even one or two of those 14 calls — especially `up -d`, which has to re-create the container each time after the preceding `rm -f` — takes several seconds longer than average under the exact degraded conditions this loop is meant to survive, which would kill the whole retry attempt via the outer `subprocess.run(..., timeout=180)` mid-loop, silently truncating the number of attempts actually completed and defeating the point of raising `TAIGA_UP_MAX_ATTEMPTS` to 5 in the first place.
- Failure scenario: on a host where each `docker compose` invocation legitimately takes ~5s under load (not unreasonable for a struggling daemon), 14 calls cost ~70s — already more than double the assumed 30s margin — meaning the 180s ceiling would be hit partway through attempt 4 or 5, killing the subprocess with a `TimeoutExpired` before the retry loop's own logic ever gets to report final exhaustion or (if the backoff had continued) recover.
- This is explicitly the judgment call the spec flagged for review attention ("review should specifically check the retry-timing-vs-90s-timeout arithmetic before approving") and the developer's choice (raise the timeout to preserve the full 150s of retry headroom rather than shrinking the constants) is a reasonable one, not obviously wrong — I'm not asserting it's insufficient, only that the margin is genuinely thin for the specific failure mode under discussion and that this thinness isn't stated anywhere in `docs/implementation.md`. Recommend either widening the margin further (e.g. 210-240s) or explicitly documenting in the script/implementation doc why 30s was judged sufficient (e.g. an observed real timing budget from the verification host), so a future reader doesn't have to re-derive this from scratch.

## Follow-ups (non-blocking)
- Consider whether `taiga_run()`'s `"down"` action (still 90s) is safe given the same-shaped Docker contention this round diagnosed for `"up"` — out of scope for this round (not part of any of the four items), but worth a future look if a `taiga-down.sh` equivalent hang is ever reported.

## Overall verdict
Changes requested. All four fixes (items 29-v2, 30-v2, 34, 35) are correctly
implemented, match their spec's code blocks, and are genuinely covered by
passing automated tests I ran myself this session — the testing pass is
clean, and Findings 1-2 are review-pass findings, not test failures. Finding
1 is a must-fix: it's a real, diff-caused regression (the frontend's Taiga
timeout was previously deliberately kept in sync with the backend's, and no
longer is), it can plausibly trigger a concrete backend race (concurrent
`up`/`down` invocations against the same Docker Compose stack) precisely
during the failure mode this round exists to fix, and it directly contradicts
the spec's "Affected areas" claim that no frontend changes were needed.
Finding 2 is a should-fix/follow-up, not blocking on its own. Route back to
the developer for Finding 1 at minimum before re-review; Finding 2 can be
addressed in the same pass or explicitly deferred with reasoning recorded.

---

# Re-review: fix-back cycle (round 5, Findings 1 and 2)

## Scope
Independent re-verification of the developer's fix-back for the two findings
above (`docs/implementation.md`'s "Fix-back cycle: round 5 review findings"
section), against the actual diff in `app/app.py` and
`tests/test_singleton_toggle_frontend.js` — not the developer's narrative of
it. All changes in this branch are still uncommitted (`git status` shows
working-tree modifications only, no new commits since the original round-5
diff was written), so `git diff main` is the complete, single diff under
review here.

## Re-verification cases

| # | Check | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` is really `180000` now | Read `app/app.py` directly (`grep -n "timeoutMs: 180000"`) | pass | `taiga: {timeoutMs: 180000, ...}` — confirmed by direct read, not the claim |
| 2 | It matches the *real* backend `"up"` timeout, read independently | Read `taiga_run()` at `app/app.py:2697-2726` directly | pass | `timeout = 180 if action == "up" else (10 if action == "status" else 90)` — 180 for `"up"`, matches `180000`ms exactly |
| 3 | Gitea's `timeoutMs`/backend timeout genuinely untouched this round | Read `SINGLETON_TOGGLE_CONFIG.gitea` and `gitea_run()` (`app/app.py:2740-2746`) directly; `git diff main -- app/app.py` scoped to `gitea_run` | pass | `gitea: {timeoutMs: 90000, ...}`; `gitea_run()` still `timeout=(10 if action == "status" else 90)`, byte-for-byte unchanged, zero diff in that function |
| 4 | Disabled-checkbox logic actually disables during "starting" and re-enables on both terminal outcomes | Read `singletonToggleSub()` (`app/app.py:3260-3290`), `row()` (`4381-4409`), `refresh()`'s two call sites, and `toggle()` (`4746-4776`) directly, hand-traced every transition | pass | `disabled` initialized `false`; set `true` only in the one branch where `sub` is literally `'starting… ...'` (both the on-dispatch path and the "was running, suddenly isn't" re-arm path funnel through the same branch); left `false` in the `on` branch (→ "running") and in the elapsed-timeout branch (→ "error") — both terminal outcomes correctly re-enable |
| 5 | Disabled logic is provably safe against showing "error"/re-enabling *before* the backend call could possibly still be running | Reasoned from wall-clock ordering: client's `Date.now()` pending-start fires at click time, strictly before the request even reaches the server; server's own `taiga_run()` timeout window starts strictly later and is bounded by the same 180s. Client's 180000ms window (started earlier) therefore always outlives the server's 180s window (started later) | pass | No clock-skew-free path lets the frontend flip to "error"/re-enable before the backend's blocking call has genuinely resolved (returned or been killed by its own `subprocess.run(timeout=180)`) |
| 6 | Race concern (Finding 1's *literal, narrated* scenario) is closed | Traced `toggle()` → `singletonToggleSub()` → `row()` for the on-dispatch path specifically | pass | With `disabled=true` for the full "starting…" window and that window now guaranteed ≥ the backend's own blocking duration (case 5), the exact scenario Finding 1 described (false "error" at 90s while `taiga_run("up")` is still legitimately retrying, operator re-clicks, fires a second concurrent `taiga_run()`) can no longer occur |
| 7 | Whether *any other* path still allows a double-fire | Traced the "off"-dispatch's own in-flight window (`toggle()` off-branch, `singletonToggleSub()`'s off/`wasRunning` branch) | **gap found — see Finding 3 below** | Turning Taiga *off* still shows `'stopped'` optimistically and immediately (`st.pending` is nulled at click time, so `singletonToggleSub()` falls straight to the `else` `'stopped'` branch), with `disabled` never set `true` anywhere in that path — the checkbox stays fully clickable while `taiga_run("down")` (up to 90s, unchanged) is still genuinely in flight server-side |
| 8 | New tests in `test_singleton_toggle_frontend.js` genuinely exercise disable/re-enable, not a tautology | Ran suite as-is (19/19 pass), then reverted `disabled = true;` → `disabled = false;` at `app/app.py:3283` in place and re-ran | pass | With the revert: 4/19 fail, exactly the 2 new tests per kind (`checkbox is disabled while "starting…"...` and `checkbox is re-enabled once an on-dispatch actually succeeds`) — confirms the tests are wired to the real behavior, not asserting a static attribute. File restored byte-for-byte after (`diff` against a pre-edit backup showed no difference) |
| 9 | `TIMEOUT_MS_CONFIG` test duplication tracks the real value, per the same rationale as the existing `BADGE_CONFIG` duplication | Reverted `timeoutMs: 180000` → `90000` in `app/app.py` only (leaving the test file's own `TIMEOUT_MS_CONFIG.taiga = 180000` untouched) and re-ran | **pass with a caveat — see Finding 4 below** | All 19 tests still passed — the two new tests advance the mock clock by `TIMEOUT_MS_CONFIG[kind] + 1000` (180000+1000ms), which is *past* both the real 90s and a hypothetically-reverted 90s value, so they can't actually distinguish a frontend/backend timeout mismatch by themselves; that specific cross-language invariant is enforced only by the code comments (which do explicitly cross-reference each other) and by manual review (case 2 above), not by any automated assertion. File restored, diff-verified clean afterward |
| 10 | `offPendingCount` mechanism (the "off" path) genuinely untouched | `git diff main -- app/app.py`, isolated to `toggle()`'s body and `singletonToggleSub()`'s `st.offPendingCount === 0` checks | pass | Zero code diff in `toggle()`'s function body or either `offPendingCount === 0` check — only the doc comment above `singletonToggleState`'s declaration was reworded to describe the now-more-nuanced disabled-state behavior accurately |
| 11 | `node tests/test_singleton_toggle_frontend.js` | Ran directly, this session | pass | `ALL PASS (19/19)` — 15 pre-existing + 4 new (2 per kind) |
| 12 | Full suite, no new regressions | `python3 -m unittest discover -s tests`, this session | pass | `Ran 1213 tests ... FAILED (failures=3)` — identical 3 failure names as the original round-5 pass (`test_teams_grounding.DiscoverThisRepoTests.test_discovers_architecture_backlog_readme_no_claude_or_agents`, `.test_load_grounding_against_this_repo_is_non_empty`, `test_teams_grounding.GroundingCLITests.test_grounding_subcommand_against_this_repos_own_tree`); `git status` re-confirms the untracked `CLAUDE.md` root cause is still present and unchanged — root cause already independently proven in the original pass (moved the file aside, all 3 passed), not re-litigated here per proportional verification depth |
| 13 | `py_compile`/syntax on touched files | `python3 -m py_compile app/app.py app/taiga_board.py`; `node -c tests/test_singleton_toggle_frontend.js` | pass | both clean (no shell files touched in this fix-back cycle, so `bash -n` doesn't apply here — it was already run and clean against the shell files in the original pass) |

## New findings from the re-review

### 3. A different, pre-existing double-fire path remains open: the "off"-dispatch's own in-flight window — should-fix, not a regression from this fix-back
- File: `app/app.py` — `toggle()`'s `else` (off) branch (`~4754-4761`), `singletonToggleSub()`'s off-branch (`~3272-3288`)
- Issue: the fix-back correctly closes the *specific, narrated* race from Finding 1 (a false "error" during an on-dispatch's mismatched-timeout window leading to a re-click). But it only disables the checkbox while `sub` is `'starting…'` — which is never true during an *off*-dispatch. When an operator turns Taiga off, `toggle()` immediately nulls `st.pending` and `singletonToggleSub()` falls straight to `sub = 'stopped'` (the optimistic, immediate branch), leaving the checkbox fully clickable while `taiga_run("down")` (its own up-to-90s timeout, unchanged) is genuinely still executing server-side. An operator who clicks it back on during that window fires a concurrent `taiga_run("up")` against the same Docker Compose stack while `"down"` is still in flight.
- This is **not a regression introduced by this fix-back** — the off-dispatch path was already unguarded before this round's changes (the pre-existing `offPendingCount` mechanism only keeps the UI's own displayed state internally consistent across overlapping off-dispatches; its own doc comment has always said it exists because the checkbox "is never disabled" for that path, not because it prevents concurrent backend execution). It also was not literally what Finding 1's own narrated failure scenario described (that scenario was specifically anchored to the on-dispatch's "starting…" window and the 180s/90s mismatch). The developer's implementation.md is honest about this scoping: "the spec/task for this fix-back only asked for the 'starting' state to be guarded."
- Recommend as a follow-up for a future round: extend the same `disabled` treatment to the off-dispatch's own in-flight window (e.g. a `stopping…` sub-state akin to `starting…`, or reusing `offPendingCount > 0` to also drive `toggleDisabled`), for symmetry and to fully close the double-fire class of bug, not just the specific instance already reported. Not blocking this cycle's approval — it's a real but pre-existing, disclosed, out-of-the-literal-ask gap, not a new defect caused by this diff.

### 4. No automated test enforces the frontend/backend timeout cross-invariant going forward — nit, informational
- File: `tests/test_singleton_toggle_frontend.js` (`TIMEOUT_MS_CONFIG`), `tests/test_taiga.py` (`test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop`)
- Issue: these two values (JS frontend `timeoutMs`, Python backend `taiga_run()` timeout) are both hardcoded/duplicated across two independent test files (Python and JS), each verified against its own side only. Nothing programmatically asserts they stay equal (or frontend ≥ backend) going forward — a future change to one without the other (recreating exactly this fix-back's own root cause) would only be caught by a human reading the cross-referencing code comments, not by a test failure.
- This matches an existing, already-accepted codebase convention (the same pattern as `BADGE_CONFIG`'s duplication, cited by the developer as precedent), so I'm not treating it as inconsistent with project norms — noting it only as a nit/informational observation for a possible future hardening (e.g. a single Python test that shells out to extract both the rendered JS constant and the Python timeout value and asserts frontend ≥ backend numerically), not a should-fix.

## Overall verdict (re-review)
**Approve with follow-ups.** Both of the original findings are genuinely,
independently verified as fixed:
- Finding 1 (must-fix): `timeoutMs` now provably matches `taiga_run()`'s real
  180s timeout (read directly, not trusted), Gitea correctly left alone, and
  the disabled-checkbox guard is real, correctly wired through every call
  site, provably safe against premature re-enabling (wall-clock ordering
  argument, case 5 above), and backed by tests that fail when the guard is
  reverted (case 8, an actual revert-and-watch-it-fail check performed this
  session).
- Finding 2 (should-fix): addressed via documentation exactly as the
  original review's own framing asked for ("the primary ask here is
  documenting the reasoning, not necessarily changing the number").
- Regression check clean: full suite still exactly the same 3 pre-existing,
  `CLAUDE.md`-caused failures, nothing new; frontend suite 19/19 including 4
  new tests that are demonstrably tied to real behavior, not tautological.

Two new, non-blocking items surfaced during this re-review's independent
race analysis (Finding 3, should-fix — a different, pre-existing,
already-disclosed double-fire path via the off-dispatch's own in-flight
window, not a regression from this cycle's diff; Finding 4, nit — no
automated cross-language invariant test, consistent with existing project
convention). Neither blocks merge: both are honestly pre-existing gaps
outside the literal scope of what was asked for in this fix-back, not new
defects caused by it, and are recorded here as follow-ups for a future
round.
