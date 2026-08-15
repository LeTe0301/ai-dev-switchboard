# Implementation: E2E regression-verification follow-ups, round 5 (items 29-v2, 30-v2, 34, 35)

## Summary
Four independent fixes from a real Proxmox regression-verification pass
(`docs/BACKLOG.md`'s "Items 22-33 regression verification" section, plus
new items 34/35), implemented per `docs/spec.md`:

1. **Item 29 (v2)** — `switchboard-svc` now gets a narrow, single-user
   POSIX ACL read grant on `taiga-push.env` (the round-1 fix closed the
   *path* mismatch but left a *permission* gap: the file is correctly
   `600`/`RUN_USER`-owned, but the service user couldn't read it).
   `app/taiga_board.py`'s `load_config()` now also distinguishes
   "permission denied" from "genuinely missing" instead of conflating
   both into the same "Taiga isn't configured" message.
2. **Item 30 (v2)** — `scripts/taiga-up.sh`'s retry loop is now 5
   attempts with exponential backoff (10/20/40/80s) instead of the
   round-4 fix's flat 3×2s, plus an opt-in-only (default off)
   `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` fallback. `app/app.py`'s
   `taiga_run()` timeout for the `"up"` action was raised from 90s to
   180s — the previous flat 90s would have killed the retry loop
   mid-attempt before it ever got a chance to recover (see "Root cause /
   the 90s timeout check" below).
3. **Item 34** — `install.sh`'s guarded-restart block (refuses to
   restart while any `RUN_USER` tmux session is live) moved from
   right after `systemctl enable --now` (gated on `--update`) to the very
   end of the script, right before `echo "== Done =="`, and now runs
   unconditionally. Previously a fresh `--with-git-hosting` (or any
   `--with-*`) install started the service *before* that block's own
   config write landed in `switchboard.env`, so the flag never actually
   reached the running process's environment (`EnvironmentFile=` is read
   once at process start).
4. **Item 35** — `POST /team/stop` now cleans up a `finished` or
   `escalated_max_rounds` run too, not just an active one. The gate was
   narrowed from `run is None or run["status"] not in (...)` to just
   `run is None`; `teams.stop_team()` was already correctly unconditional,
   the bug was entirely in the route's own pre-check.

## Root cause / the 90s timeout check (item 30, explicitly required by the spec)
`app/app.py`'s `taiga_run()` shells out via `subprocess.run(["sudo",
script], ..., timeout=...)`. Before this change, every action (`up`,
`down`) used a flat 90s timeout. `scripts/taiga-up.sh`'s new retry
constants (5 attempts, exponential backoff starting at 10s, doubling
each time) sleep 10+20+40+80 = **150s** across the 4 inter-attempt
sleeps alone (before the 5th and final attempt), plus the `docker
compose up -d`/`ps` calls themselves in between — comfortably past the
previous 90s ceiling. Confirmed by reading `taiga_run()` directly
(`app/app.py:2696-2702` before this change: `timeout=(10 if action ==
"status" else 90)`). Per the spec's explicit instruction to raise the
timeout or tune the retry constants down (not leave the arithmetic
unchecked), I raised `taiga_run()`'s own timeout for `"up"` specifically
to **180s** — the spec's stated design reasoning is that real recovery on
the verification host needed "tens of seconds to a couple of minutes,"
so keeping the full 150s of retry headroom (rather than shrinking the
constants) better matches what was actually observed, with margin for
the `up -d`/`ps` calls themselves. `"down"`/`"status"` keep their
original timeouts (10s/90s) — neither retries.

## Changes by file
- `install.sh`:
  - `apt-get install` line (item 29): added `acl` so `setfacl`/`getfacl`
    are available.
  - `runtime.env` write (item 29): added `SVC_USER=$SVC_USER` as a third,
    non-secret line (same category as the existing `RUN_USER`/
    `PROJECTS_DIR`), so `scripts/taiga-configure-push.sh` (which runs as
    `RUN_USER`, not `SVC_USER`) can look up who to grant the ACL to
    without hardcoding `"switchboard-svc"`.
  - Guarded-restart block (item 34): deleted from right after
    `systemctl enable --now ai-dev-switchboard` (previously gated on
    `$UPDATE -eq 1`), re-inserted verbatim-in-logic but unconditional
    right before `echo "== Done =="`, after every `--with-*` block. The
    live-tmux-session detection/defer logic itself is byte-for-byte
    unchanged; only the gate (`if [ "$UPDATE" -eq 1 ]`, removed) and the
    deferred-restart message (no longer `--update`-specific wording,
    dropped the now-inapplicable "New code was copied to $INSTALL_DIR"
    phrase) changed, matching the spec's exact code block.
- `scripts/taiga-configure-push.sh` (item 29): after the existing
  `chmod 600 "$CONFIG_FILE"` line, added the ACL grant — reads
  `SVC_USER` from `/etc/ai-dev-switchboard/runtime.env` (falling back to
  the literal `"switchboard-svc"` default if that file or key is
  missing), then `setfacl -m u:${SVC_USER_NAME}:r "$CONFIG_FILE"` if
  `setfacl` is available, with specific, actionable stderr warnings if
  either `setfacl` is missing or the grant itself fails (e.g. a
  filesystem without POSIX ACL support).
- `app/taiga_board.py` (item 29): `load_config()` gained a new
  `except PermissionError:` clause immediately **before** the existing
  `except OSError:` clause (ordering matters — `PermissionError` is an
  `OSError` subclass, so the reverse order would make the new clause dead
  code). Raises a distinct `TaigaPushError` naming the real cause
  ("Found {path} but couldn't read it (permission denied)...") and
  pointing at both the automatic fix (re-run
  `taiga-configure-push.sh`) and the manual one (`setfacl -m
  u:<service-user>:r {path}`).
- `scripts/taiga-up.sh` (item 30): `TAIGA_UP_MAX_ATTEMPTS` default raised
  3 → 5; new `TAIGA_UP_RETRY_BACKOFF_SECONDS` (default 10, doubling each
  retry) replaces the old flat `sleep 2`; new
  `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` (default `0`/off) — if an
  operator opts in, a full `systemctl restart docker` (affects every
  container on the host, not just Taiga's) plus one more `up -d` attempt
  runs only after all bounded retries are exhausted. Final failure
  message updated to mention this opt-in as a next step.
- `app/app.py`:
  - `taiga_run()` (item 30): timeout for the `"up"` action raised from a
    flat 90s to 180s specifically (see "Root cause" above); `"down"`/
    `"status"` unchanged.
  - `/team/stop` route (item 35): narrowed `if run is None or
    run["status"] not in (...)` to `if run is None`. Nothing else in the
    route changed — `teams.stop_team()`'s own unconditional cleanup logic
    (session kill + worktree teardown) now actually runs for a terminal
    run too.
- `tests/test_taiga.py`: `test_up_uses_longer_timeout` renamed
  `test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop` and its
  assertion updated from `90` to `180`, matching the `taiga_run()` change.
- `tests/test_teams_board.py`: new `LoadConfigPermissionTests` class —
  three tests covering the except-clause-ordering fix (see "How this was
  verified" below).
- `tests/test_team_routes.py`: `TeamStopEndpointTests`'s
  `test_stop_on_already_finished_team_is_idempotent_ok` (asserted the
  *old*, buggy no-op behavior) replaced with
  `test_stop_on_finished_team_now_actually_cleans_up_and_allows_restart`
  (asserts the real teardown, then a follow-up `/team/start` on the same
  project succeeding — the spec's own acceptance criterion) and a new
  `test_stop_on_escalated_max_rounds_team_now_actually_cleans_up`
  (the spec's second named terminal status).
- `tests/test_install_update.py`: `_build_restart_block_harness()`
  updated to extract the block from its new start marker (`"# Guarded
  restart -- refuses to restart"` instead of the old `--update`-comment
  marker) through to `echo "== Done =="` (instead of stopping at the
  `--with-git-hosting` guard, since the block is no longer sandwiched
  between two `--with-*` sections); dropped the now-unused `UPDATE=1`/
  `$INSTALL_DIR`-in-message assumptions from the harness and its
  assertions, since the block runs unconditionally now and its message no
  longer mentions `$INSTALL_DIR`.
- `tests/test_deploy_target.py`: both `_extract_between(...,
  'echo "== Done =="')` end markers (the standalone deploy-target-block
  harness and the combined host-control+deploy-target harness) changed to
  stop at `"# Guarded restart -- refuses to restart"` instead — item 34
  moved the guarded-restart block (which references `$RUN_USER`, not
  supplied by either harness) to sit between the deploy-target block and
  `echo "== Done =="`, so the old end marker started pulling that
  unrelated block into these harnesses too, causing a `RUN_USER: unbound
  variable` failure. This is a pure test-harness-boundary fix, no
  behavior assertion changed.

## Key decisions / tradeoffs
- Implemented all four fixes essentially verbatim from the spec's own
  code blocks — the spec was already fully diagnosed and code-complete
  (per its own "Orchestrator note"). The one real judgment call left open
  was item 30's 90s-vs-150s timeout arithmetic, resolved by raising the
  timeout (180s) rather than shrinking the retry constants, for the
  reasoning given above.
- Item 30's `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` opt-in-only default
  is exactly as the spec's "Design decision" section states — did not
  second-guess it. It's a real, stated design call the spec itself flags
  for extra review attention.

## Deviations from spec
None. All four fixes match the spec's code blocks; the test-file changes
above are necessitated by the fixes themselves (existing tests asserted
the *old*, now-intentionally-changed behavior, or extracted install.sh
blocks by markers that moved) and were updated to match, not deviations
from the spec's intent.

## Known limitations
- **Item 29's real ACL grant** cannot be exercised end-to-end in this
  sandbox — no second, unprivileged `switchboard-svc`-equivalent user
  exists here to prove `sudo -u switchboard-svc cat taiga-push.env`
  actually succeeds after the grant, and this sandbox runs as root, so a
  real permission denial can't even be reproduced naturally to test
  against. What *is* verified: `scripts/taiga-configure-push.sh` is
  `bash -n`/shellcheck-clean, and `app/taiga_board.py`'s except-clause
  ordering is directly proven via `tests/test_teams_board.py`'s new
  `LoadConfigPermissionTests`, which monkeypatches `builtins.open` to
  raise `PermissionError` (vs. a generic `OSError` vs. a genuinely
  missing file) and confirms three distinct, correct outcomes — this
  specifically proves the fix (a bare `except OSError:` ahead of the more
  specific `except PermissionError:` would silently swallow it into the
  wrong message) actually takes effect, not just that the code compiles.
- **Item 30's real Docker port-bind race** (and the nginx/DNS failure
  mode) cannot be reproduced in this sandbox either — no Docker Compose
  plugin available, consistent with every prior Taiga-related round's own
  documented limitation (`tests/test_taiga.py`, `tests/
  test_taiga_up_retry.py`'s own header comments). The existing
  `tests/test_taiga_up_retry.py` harness (stubs `docker` as a shell
  function, runs the real, unmodified script) was not re-run against new
  hardcoded expectations since it doesn't assert specific backoff
  durations, only call counts and messages — it passed unmodified
  against the new retry logic. What *is* verified: `bash -n`/shellcheck
  on `taiga-up.sh`, and the 180s-timeout arithmetic check documented
  above, backed by `tests/test_taiga.py`'s updated
  `test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop`.
- **Item 34's real "process environment picks up a fresh config write"
  proof** (via `/proc/<pid>/environ`) needs a real systemd service
  restart cycle on a real box — not reproducible in this sandbox. What
  *is* verified: `tests/test_install_update.py`'s
  `GuardedRestartBlockTests` runs the real, unmodified block (extracted
  verbatim from `install.sh`'s current source, not a reimplementation)
  against fake `sudo`/`tmux`/`systemctl` stand-ins, confirming the
  live-session-defer / clean-restart logic is byte-for-byte preserved at
  its new, unconditional location.
- Item 35 is fully covered — no real infrastructure gap.

## How this was verified
```bash
# Syntax/lint on every touched shell file:
bash -n install.sh
bash -n scripts/taiga-configure-push.sh
bash -n scripts/taiga-up.sh
shellcheck install.sh scripts/taiga-configure-push.sh scripts/taiga-up.sh
# -> all bash -n clean; shellcheck reports only two pre-existing,
#    unrelated style notes (SC2015 at install.sh:70, predates this
#    change; SC2001 at the moved sed call, same call the pre-fix block
#    already had -- not a new issue)

# Python compiles:
python3 -m py_compile app/app.py app/taiga_board.py

# Item 29's except-clause-ordering fix, in isolation:
python3 -m unittest tests.test_teams_board.LoadConfigPermissionTests -v
# -> Ran 3 tests ... OK

# Item 30's timeout-arithmetic regression guard:
python3 -m unittest tests.test_taiga.TaigaRunTests -v
# -> Ran 5 tests ... OK

# Item 34's guarded-restart-block relocation:
python3 -m unittest tests.test_install_update -v
# -> Ran 20 tests ... OK

# Item 34's deploy-target harness boundary fix:
python3 -m unittest tests.test_deploy_target -v
# -> Ran 32 tests ... OK

# Item 35's route fix:
python3 -m unittest tests.test_team_routes.TeamStopEndpointTests -v
# -> Ran 8 tests ... OK

# Full existing suite -- no regressions introduced by this round:
python3 -m unittest discover -s tests
# -> Ran 1213 tests ... FAILED (failures=3)
```
The 3 remaining failures (`test_teams_grounding.DiscoverThisRepoTests
.test_discovers_architecture_backlog_readme_no_claude_or_agents`,
`.test_load_grounding_against_this_repo_is_non_empty`,
`test_teams_grounding.GroundingCLITests
.test_grounding_subcommand_against_this_repos_own_tree`) are **pre-
existing and unrelated** — confirmed via `git stash` (they fail
identically on the unmodified tree). Root cause: an untracked `CLAUDE.md`
file present at the repo root in this sandbox session (not created by
this round's changes, not part of `docs/spec.md`'s scope) that these
tests' own grounding-file discovery logic picks up, shifting their
expected file-count/list assertions by one. A `test_teams_headless
.RealTmuxHeadlessTests.test_run_sh_and_prompt_file_are_world_readable_
under_a_strict_umask` failure also appeared in one full-suite run but
passed cleanly both before and after this round's changes when run in
isolation — a pre-existing flake under full-suite load (real tmux
timing), not a regression from this round.

---

## Fix-back cycle: round 5 review findings (docs/test-review.md)

The reviewer's testing+review pass approved the four items above but
requested changes on two review-pass findings (not test failures) before
final approval. Both are addressed here.

### Finding 1 (must-fix) — frontend Taiga timeout out of sync with the new 180s backend timeout, plus no double-submission guard
- **Root cause**: item 30 (v2) raised `taiga_run()`'s backend `"up"`
  timeout from 90s to 180s to give `scripts/taiga-up.sh`'s new retry loop
  room to run to exhaustion, but `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs`
  (`app/app.py`, JS embedded in `render_page()`) was left at the old
  `90000`. The pre-existing comment at that definition explicitly states
  the two timeouts are kept in sync as "a safety ceiling, not a
  performance target" — this round broke that invariant for Taiga
  specifically (Gitea's backend timeout is unchanged at 90s, so its
  `timeoutMs` correctly stayed 90000).
- **Fix**: `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` raised `90000` →
  `180000`, matching `taiga_run()`'s actual `"up"` timeout exactly (not
  guessed — read directly from the `timeout = 180 if action == "up" else
  ...` line). Comments at both the backend `taiga_run()` timeout and the
  frontend `SINGLETON_TOGGLE_CONFIG` now cross-reference each other and
  state the invariant explicitly, so a future change to one is more
  likely to prompt updating the other.
- **The masked race**: the reviewer found that because the frontend's
  "starting…"→"error" flip could happen at 90s while the backend's
  `taiga_run("up")` call could legitimately still be in flight for up to
  180s, an operator could see a false "error", click the (never-disabled)
  checkbox again, and fire a second, concurrent `taiga_run("up")` (or an
  "off" toggle → concurrent `taiga_run("down")`) against the same Docker
  Compose stack. Fixed by making the checkbox genuinely non-interactive
  for the duration of the "starting…" display: `singletonToggleSub()` now
  also returns a `disabled` flag (true only while its own computed `sub`
  is the "starting…" text — i.e. an on-dispatch is still within its own
  `timeoutMs` window), threaded through a new `toggleDisabled` parameter
  on `row()` that renders a `disabled` attribute on the `<input
  type="checkbox">`. `refresh()`'s two singleton-toggle call sites (Taiga,
  Gitea) pass this through; every other row kind (`inst`/`host`/`code`)
  passes `undefined` and is unaffected.
  - This only works correctly *because* Finding 1's timeoutMs fix is also
    in place: once `timeoutMs` is kept >= the backend's own blocking
    timeout, "error" only ever displays after the backend call is
    guaranteed to have already resolved (the route's handler blocks on
    `taiga_run()` before responding), so re-enabling the checkbox at that
    point is re-enabling on a genuine terminal state, not racing an
    in-flight request. This is documented inline at
    `singletonToggleSub()`'s own doc comment.
  - The pre-existing `offPendingCount` mechanism (the *off*-path's own
    double-submission guard, from a prior review round's Defects 1/2) was
    deliberately left alone — it guards a different state (an intentional
    toggle-off's own in-flight window, where `pending` is nulled
    immediately, not "starting…") via counting rather than disabling, and
    the spec/task for this fix-back only asked for the "starting" state to
    be guarded. Its own doc comment was updated to say so precisely,
    instead of continuing to claim the checkbox is "never disabled while
    an action is in flight" (no longer true for the on-dispatch case).

### Finding 2 (should-fix) — undocumented 180s timeout margin arithmetic
- Added a comment directly at the `taiga_run()` timeout definition
  (`app/app.py`) spelling out the arithmetic the reviewer asked to have
  documented: 180s total − 150s worst-case pure `sleep` across the retry
  loop's 4 inter-attempt backoffs = ~30s for the loop's 14 real
  subprocess calls (5× `up -d`, 5× `ps`, 4× `rm -f`), ~2.1s/call average
  if spread evenly. Explicitly notes this is thin for the exact
  degraded-Docker scenario the retry loop exists to survive, but frames
  the 180s ceiling as a last-resort safety net against the subprocess
  wedging entirely — not the retry loop's primary defense mechanism (that's
  the 5-attempt/exponential-backoff logic itself) — and tells a future
  reader to widen both this value and `SINGLETON_TOGGLE_CONFIG.taiga
  .timeoutMs` together if real-world margin proves too thin.
- Per the reviewer's own framing ("the primary ask here is documenting
  the reasoning, not necessarily changing the number"), the 180s value
  itself was left unchanged — no new evidence surfaced during this
  fix-back cycle to justify picking a different number over the
  developer's original, reviewer-acknowledged-as-reasonable judgment call.

### Files changed in this fix-back cycle
- `app/app.py`:
  - `taiga_run()`: added the Finding 2 margin-arithmetic comment (no
    behavior change — timeout stays 180 for `"up"`).
  - `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs`: `90000` → `180000`
    (Finding 1's core fix).
  - `singletonToggleSub()`: now returns `{sub, showBadge, disabled}`
    (previously `{sub, showBadge}`); `disabled` is `true` exactly while
    `sub` is the "starting…" text.
  - `row()`: new trailing `toggleDisabled` parameter, rendered as a
    `disabled` attribute on the row's `<input type="checkbox">`.
  - `refresh()`: both singleton-toggle `row(...)` call sites now pass
    `disabled` through from `singletonToggleSub()`'s result.
  - `offPendingCount`'s own doc comment reworded to accurately describe
    the now-more-nuanced disabled-state behavior (see above).
- `tests/test_singleton_toggle_frontend.js`:
  - New `TIMEOUT_MS_CONFIG` constant (duplicated from `app.py`'s real
    values, same rationale as the existing `BADGE_CONFIG` duplication —
    catches a real regression instead of trivially passing because both
    sides read the same broken value).
  - The existing `"unexpected stop while running that never recovers
    still surfaces error after 90s"` test was hardcoded to a 91000ms
    advance and would otherwise now fail for Taiga (its real threshold is
    180000ms) — updated to advance `TIMEOUT_MS_CONFIG[kind] + 1000` per
    kind instead of a fixed magic number, renamed to match.
  - Two new tests per kind (Taiga and Gitea, via the existing
    `registerSingletonToggleTests(kind)` loop): the checkbox is disabled
    while "starting…" and re-enabled once it becomes "error" after
    `timeoutMs`; and the checkbox is re-enabled once an on-dispatch
    actually succeeds ("running"). Added after implementing the `disabled`
    plumbing (not strict test-first for these two), but the fix's own
    necessity was verified test-first in the TDD sense: running the
    existing suite against the app.py change alone (before touching the
    test file) surfaced the pre-existing 91000ms-hardcoded test failing
    exactly as expected for Taiga, independently confirming the timeoutMs
    change was a real, test-visible behavior change, not just a comment
    update. These two new tests then close the gap the reviewer's finding
    was actually about — the *disabled* behavior itself had no coverage
    at all before this cycle.

## How this fix-back cycle was verified
```bash
# Frontend regression suite for the exact code touched (Node, no deps):
node tests/test_singleton_toggle_frontend.js
# -> ALL PASS (19/19) -- 15 pre-existing + 4 new (2 per kind)

# Python compiles:
python3 -m py_compile app/app.py
# -> clean

# Full existing suite -- no regressions introduced by this fix-back:
python3 -m unittest discover -s tests
# -> Ran 1213 tests ... FAILED (failures=3)
```
The same 3 pre-existing, `CLAUDE.md`-caused failures as the prior round
(see above) — identical names, identical root cause, independently
re-confirmed by `git status` showing `CLAUDE.md` still untracked in this
sandbox. No new failures introduced by this fix-back cycle's changes.

## Deviations from spec/review findings in this fix-back cycle
None. Both findings were implemented as directed: Finding 1's fix
direction text explicitly left the exact `timeoutMs` number open
("comfortably covers... e.g. matching it") — matched it exactly (180000)
rather than adding extra margin, since the review's own arithmetic
treats the 180s backend value as already-decided in Finding 2 (a
separate, "should-fix... not necessarily changing the number" concern),
and matching exactly is the simplest reading that satisfies the stated
invariant without conflating the two findings. Finding 2 was addressed
by documentation only, per the reviewer's own explicit framing of what
was actually being asked for.
