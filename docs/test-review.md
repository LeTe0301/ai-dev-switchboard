# Test & Review: Local backlog tracker (Taiga) — part 1a: install flag + singleton UI row

## Scope
Testing + review pass against `docs/spec.md`'s acceptance criteria and edge
cases for `install.sh --with-taiga` + the singleton Taiga toggle row, covering
`install.sh`, `app/app.py`, `scripts/taiga-{up,down,status}.sh`,
`config/switchboard.env.example`, and `tests/test_taiga.py`.

## Re-verification pass (second testing pass, after the Defect 1 fix)

Scope of this pass: re-run the original Defect 1 repro against the fixed
code, check the developer's stated extension (the 428/TOTP-retry path via
`submitActionCode()` also setting/clearing `taigaOffInFlight`), confirm the
legitimate hiccup/recovery/eventual-error behavior is genuinely unaffected,
and specifically try to break the fix with edge cases beyond what it was
built to handle (concurrent operations, per this role's boundary-testing
mandate). Result: **the originally-reported race is fixed, but a new,
closely-related race was found and reproduced — see Defect 2 below.**

### What was re-verified as fixed

- Ran the developer's committed `tests/test_taiga_frontend.js` (extracts the
  real, rendered `<script>` from `app.render_page()` via a `python3 -c`
  subprocess, runs it in a Node `vm` with `fetch`/`document`/timers
  stubbed — the same ad hoc technique this reviewer used to originally find
  Defect 1, now committed) — `node tests/test_taiga_frontend.js` → **4/4
  PASS**:
  1. The reviewer's exact original repro (toggle-off click, POST left
     unresolved, a concurrent poll lands mid-flight reporting `taiga: true`,
     POST resolves ~95s later) → settles on `stopped`, never
     `starting…`/`error`. **Confirmed independently — this is the exact
     scenario Defect 1 described, and it no longer reproduces.**
  2. The same race via the 428/TOTP-retry path (`submitActionCode()`) — also
     settles on `stopped`.
  3. Unexpected stop while running (no toggle clicked) → single poll blip
     re-arms `starting…`, then recovers to `running` — unaffected by the fix.
  4. Same, but never recovers → still surfaces `error` after 90s — unaffected
     by the fix.
- **Confirmed the tests are not tautological**: temporarily neutered just the
  two `refresh()` guards the fix added (`if (!taigaOffInFlight)
  taigaWasRunning = true;` → `taigaWasRunning = true;`, and `if
  (taigaWasRunning && !taigaOffInFlight)` → `if (taigaWasRunning)`), re-ran
  the suite — tests 1 and 2 failed exactly as expected (both landed on
  `starting…`, reproducing Defect 1's original symptom), tests 3 and 4 still
  passed. Restored `app/app.py` from an untouched copy immediately after
  (`git diff --stat` confirms a clean restore — no residual change).
- **The 428/TOTP-retry path extension is correct, not just plausible**: read
  `toggle()` and `submitActionCode()` directly (`app/app.py` lines
  1323-1416). Confirmed `taigaOffInFlight` is set immediately before, and
  cleared immediately after, the one request in each path that actually
  reaches `taiga_run("down")` server-side — `toggle()`'s direct POST when no
  code is required, and `submitActionCode()`'s POST after a 428. The initial
  428-triggering attempt in `toggle()` also briefly sets/clears the flag
  around a request that never reaches `taiga_run` (server returns 428 before
  touching anything) — harmless, since it's a fast round-trip, not the up-to-90s
  window the flag exists to protect. Because JS is single-threaded and no
  `await` sits between "POST resolves" and "clear the flag" in either path,
  there's no window during which another turn of the event loop could
  observe a stale `true` after a real completion — for a *single* off
  request. (This exact single-boolean assumption is what breaks under a
  second, overlapping off request — see Defect 2.)
- Confirmed the full pre-existing regression suite is still green after the
  fix: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  → **88/88 pass** (unchanged from the original pass — the fix is
  frontend-`<script>`-only, no backend/`install.sh`/wrapper-script changes,
  matching `docs/implementation.md`'s claim). `python3 -m py_compile
  app/app.py`, `bash -n install.sh`, `bash -n
  scripts/taiga-{up,down,status}.sh` all still pass.

### Defect 2 (new): a second, overlapping toggle-off request reopens the exact race Defect 1's fix was built to close

- **How this was found**: per this pass's mandate to probe beyond the exact
  scenario already fixed (concurrent operations / duplicate dispatch), not
  just re-confirm it. Wrote two additional ad hoc Node `vm` probes using the
  same harness technique as the committed suite (direct `toggle()`/
  `submitActionCode()` invocation, not DOM click simulation — the same proxy
  the committed tests already use): (A) a toggle-**on** racing a still-in-flight
  toggle-off, and (B) two overlapping toggle-**off** dispatches. Probe A
  settled correctly on the state matching the final server-reported truth in
  every variant tried — no defect there (see "Probe A: not a defect, but a
  flagged residual risk" below for a related, non-blocking observation).
  Probe B reproduced Defect 1's exact false-`error` end state.
- **Root cause**: `taigaOffInFlight` is a single shared boolean, not a
  reference count or a token tied to a specific in-flight request. `toggle()`
  and `submitActionCode()` each unconditionally set it `true` before their
  own `POST /taiga/off` and unconditionally set it back to `false` the
  instant *their own* request resolves — with no awareness of whether a
  second, independently-dispatched off request is still outstanding. If two
  toggle-off requests overlap, the **first** one to resolve clears the flag
  to `false` globally, even though the **second** one (a genuine,
  still-running `docker compose down`) hasn't finished yet. A `/status` poll
  landing in that reopened window sees `taiga: true` (accurate — the second
  `down` is still in progress) and, with the flag now (wrongly) `false`,
  re-arms `taigaWasRunning = true` — exactly the clobber Defect 1 described,
  now via a different trigger.
- **Why two overlapping off requests is realistic, not contrived**: the
  toggle checkbox is never disabled while a Taiga action is in flight, and
  `refresh()`'s 4-second poll unconditionally re-renders the row (a brand-new
  `<input type="checkbox">` element via `innerHTML` replacement) using
  whatever `/status` currently reports. While the first `docker compose
  down` is still running (up to 90s for this 9-service stack), every such
  poll re-renders the checkbox as **checked** (`taiga: true` is the accurate
  server truth at that instant) — visually, to the user, it looks like their
  off-click "didn't take" and reverted back to on. An impatient user clicking
  it again during that window fires a second, genuine `POST /taiga/off`
  while the first is still outstanding. This requires no precise timing luck
  at all (unlike the original Defect 1 repro, which needed a poll to land in
  a specific narrow window) — it only requires an ordinary user reacting to
  what the UI is, accurately, showing them during a slow operation.
- **Repro** (reproduced against the real, unmodified rendered `<script>`,
  same extraction technique as the committed suite):
  1. `refresh()` reports `taiga: true` → row shows `running`.
  2. User clicks toggle off: `toggle('taiga', null, false, cb1)` — sends
     `POST /taiga/off` #1, left unresolved (simulating `docker compose down`
     still running). `taigaOffInFlight` → `true`.
  3. Before #1 resolves, a second off dispatch fires (see realism note
     above): `toggle('taiga', null, false, cb2)` — sends `POST /taiga/off`
     #2, also left unresolved. `taigaOffInFlight` is still `true` (set again,
     redundantly, by the second call).
  4. `POST /taiga/off` #1 resolves 200. `toggle()`'s continuation for call
     #1 unconditionally runs `taigaOffInFlight = false` — even though `POST
     /taiga/off` #2 is still outstanding.
  5. A `/status` poll lands here and (correctly, at this instant) reports
     `taiga: true`, because the *second* `down` genuinely hasn't finished.
     With `taigaOffInFlight` now `false`, `refresh()` writes `taigaWasRunning
     = true` — the exact clobber Defect 1 described.
  6. `POST /taiga/off` #2 finally resolves 200 (the real completion). The
     next poll correctly reports `taiga: false`.
  7. Observed rendered `<div class="sub">`: **`starting… <span
     class="taiga-starting-spinner">◌</span>`** — not `stopped`, immediately
     after a fully successful toggle-off.
  8. Extending the probe 91 simulated seconds further, with Taiga
     legitimately off on every subsequent poll: observed sub is **`<span
     class="taiga-err">error</span>`** — the identical false-failure outcome
     Defect 1 originally described, now via a duplicate-dispatch trigger
     instead of a single-request/poll-timing trigger.
- **Expected vs. actual**: expected the row to settle on `stopped` after two
  overlapping, both-ultimately-successful toggle-off requests. Actual: the
  row shows `starting…` then, ~90s later, a persistent false `error` —
  functionally the same regression the fix was written to close.
- **Severity**: must-fix. Reopens the class of bug Defect 1 already
  identified as must-fix, via a trigger that's arguably *more* likely in
  practice than the original (no precise poll-timing needed — an ordinary
  impatient double-click during a long, accurately-still-showing-as-"on" 90s
  operation is enough). Confirmed via direct execution against the real
  rendered frontend logic, not inferred from reading the diff.
- **Suggested direction** (not prescriptive — developer's call): a single
  shared boolean can't distinguish "no off requests in flight" from "one of
  several off requests just finished, others are still running." Either (a)
  make the guard a reference count (increment before each off-triggering
  POST, decrement after, treat "in flight" as count > 0) rather than a plain
  boolean two independent call sites both write, or (b) disable the toggle
  control (or otherwise make the row inert to further clicks) for the
  duration of an in-flight Taiga action so a second off dispatch can't be
  fired from the same page in the first place, or (c) don't try to
  reconstruct "intentional off" from timing/flags at all — suppress the
  "was running → now isn't → re-arm starting" transition based on the actual
  server-confirmed state (e.g. only treat a `false` report as unexpected if
  it follows a `true` report with no intervening off request of any kind
  still outstanding, tracked by a monotonically increasing generation
  counter bumped on every off dispatch and compared, not a boolean).

### Probe A: not a defect, but a flagged residual risk (non-blocking, out of this fix's scope)

Racing a toggle-**on** against a still-in-flight toggle-**off** (e.g. the
user clicks off, then before it resolves, clicks on again) did **not** break
the frontend state machine in anything tried — the row always ended up
matching whatever the server subsequently reported, no false `error`. This
is not a defect. However, reading `app/app.py`'s POST handler (lines
2322-2331) during this probe confirmed there is **no server-side
serialization** between concurrent `/taiga/on` and `/taiga/off` requests —
both would call `taiga_run("up")`/`taiga_run("down")` (i.e. `docker compose
up`/`docker compose down` against the same stack) fully concurrently if
dispatched close together, with no lock analogous to `_desc_lock`
(`app/app.py:283`) guarding them. This sandbox has no working `docker
compose` to actually exercise this against real containers (same
environment limitation as the original pass), so whether concurrent
`up`/`down` against the same Compose project actually corrupts state is
unverified, not confirmed. This is also a **pre-existing pattern**, not
introduced by this fix — no other toggle kind (`host`, per-instance,
`code`) has server-side locking against concurrent on/off either. Flagging
for the developer's awareness, not elevating to a defect: out of scope for
this narrowly-scoped frontend fix, and not something this fix regressed.

**Environment check (done first, since this materially changes what "tested"
can mean here):** confirmed independently — Docker daemon 26.1.5 present,
`docker compose` **not** available (`'compose' is not a docker command'`),
`docker-compose` v1 absent, 1.4G disk free of 16G, ~373Mi RAM free of 2.0Gi.
This matches `docs/implementation.md`'s stated environment exactly — the
developer's claim that a real end-to-end 9-container Taiga install/toggle
cycle could not be run in this sandbox is confirmed true, not just accepted
on faith.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `install.sh --with-taiga` installs Docker, clones `stable`, writes `.env`+override, pre-pulls, leaves stopped | Manual: isolated re-execution of the exact install-block logic (real `set_env`/`random_token` functions, real heredoc) since Compose isn't available here to run the real block | Pass (logic-level) | See "Isolated logic verification" below |
| 2 | `switchboard.env` gets `TAIGA_ENABLED=1` + keys; wrapper scripts installed mode 755; sudoers grants exactly 3 zero-arg entries; `visudo -cf` passes | Manual: read diff, confirm `install -m 755` + `set_env` calls present; `visudo` **not installed in this sandbox**, could not independently re-run `visudo -cf` | Pass (structural), **unverified** (visudo itself) | `install.sh` lines 325-334, 371-378; `which visudo` → not found |
| 3 | Without `--with-taiga`: no Docker install attempted, `TAIGA_ENABLED` unset, UI rows unchanged | Automated: full existing suite (75 pre-existing tests) + read of `install.sh`'s `if [ "$WITH_TAIGA" -eq 1 ]` gating | Pass | `python3 -m unittest discover -s tests -v` → 88/88 (see Regression check) |
| 4 | Right after install, `/status` reports Taiga off | Automated | Pass | `tests/test_taiga.py::test_status_reports_taiga_fields_when_off` |
| 5 | Toggle on with valid TOTP → containers running, next `/status` reports on with correct URL (both `PUBLISH_MODE`s) | Automated (backend, `taiga_run` faked) + manual isolated check of `_taiga_display_url()` for both modes | Pass | `test_toggle_on_with_correct_code_starts_stack_then_off_stops_it`, `TaigaDisplayUrlTests` (2/2) |
| 6 | Toggle off → containers stopped, tailscale path unpublished | Automated (backend) | Pass | same test as #5 (off half); `_unpublish`/`_publish` call order read in diff, matches spec |
| 7 | `app.py`/service restart while Taiga running → `/status` still reports on, no re-toggle | Automated + code read | Pass | `/status` calls `taiga_run("status")` fresh every request (`app.py:2224-2228`), never an in-memory dict — confirmed by reading the diff, not just trusting the doc |
| 8 | TOTP not yet verified → 428 (no code) / 403 (wrong code) on `/taiga/on`/`off`, no bespoke auth code | Automated | Pass | `test_toggle_on_without_code_returns_428`, `test_toggle_on_with_wrong_code_returns_403`; confirmed in source the `taiga` branch sits after the shared TOTP gate (`app.py:2277-2300`), no special-casing |
| 9 | Taiga renders as exactly one singleton row, no engine picker, distinct from `instances` | Manual: read `refresh()`/`row()` diff; `showTaigaBadge`/`subOverride` params default `undefined` for every other call site (`inst`, `host`) — confirmed unchanged behavior for those | Pass | `app.py` diff, `row()` signature change |
| 10 | Disabled (`TAIGA_ENABLED=0`) → `/taiga/on`\|`/off` returns 404, `taiga_run` never called | Automated | Pass | `test_disabled_returns_404_and_never_calls_taiga_run` |
| 11 | Unauthenticated `/status` → 401 | Automated | Pass | `test_unauthenticated_status_returns_401` |
| 12 | Re-running `install.sh --with-taiga`: no re-clone, no secret regeneration, `TAIGA_DOMAIN` re-derives from current `PUBLISH_MODE`/`BASE_URL` | Manual: isolated re-execution of the real `TAIGA_FRESH_CLONE`-gated + `TAIGA_DOMAIN` logic against a faked `.env` shaped like real upstream defaults | Pass | Reproduced independently — see "Isolated logic verification" |
| 13 | `docker` present but no Compose plugin → warn, continue, not fatal | Manual: read `install.sh` lines 240-244 | Pass | `TAIGA_COMPOSE_OK` flag, no `exit`, matches ttyd precedent |
| 14 | No network at install time → warn, continue | Manual: read `install.sh` lines 265-270 (`if ! (... pull ); then` warns, doesn't abort) | Pass | code read |
| 15 | Real (unmodified) wrapper scripts' fallback when `$TAIGA_DIR`/config absent | Manual, executed for real in this sandbox | Pass | `taiga-status.sh` → `off`, exit 0; `taiga-up.sh`/`taiga-down.sh` → clean `cd` failure, exit 1, no traceback |
| 16 | Badge contrast (`#66d9ff` on `#16324a`) actually clears WCAG AA 4.5:1 / 3:1 | Manual: recomputed independently with the real WCAG relative-luminance formula, not trusting either design.md's or implementation.md's numbers | Pass | Recomputed: `L(#16324a)=0.02946`, `L(#4da6ff)=0.36058` → 5.17:1 (existing color already passed, design.md's own "1.78:1" math was wrong), `L(#66d9ff)=0.59668` → **8.14:1** — matches implementation.md's numbers exactly |
| 17 | Install-block placement deviation (after `-- Publishing --`, not after code-server) doesn't break ordering | Manual: confirmed `REPO_DIR`, `set_env`/`get_env`/`random_token`, `PUBLISH_MODE`/`BASE_URL` are all defined/resolved before line 235 (the Taiga block); sudoers/summary blocks still live in their original pre-existing sections | Pass | `install.sh` line numbers cross-checked |
| 18 | `TAIGA_DOMAIN`/`TAIGA_PORT` `set -u`-safety when `BASE_URL` is never assigned (`PUBLISH_MODE=none`) vs. assigned-but-blank (`PUBLISH_MODE=tailscale`, blank prompt answer) | Manual, executed both real branches under `set -euo pipefail` | Pass | `none` mode: short-circuit prevents `$BASE_URL` reference entirely, no error. `tailscale` mode: `BASE_URL=$(prompt ...)` always assigns (possibly `""`) before the Taiga block runs, so `[ -n "$BASE_URL" ]` is always safe in the real script — confirmed no reachable unbound-variable path |
| 19 | Rapid toggle-off followed by a concurrent `/status` poll landing mid-flight (frontend state machine) — **original Defect 1 repro** | Automated, `tests/test_taiga_frontend.js` (committed) + independent revert-and-watch-it-fail check | Pass (fixed) | `node tests/test_taiga_frontend.js` → test 1 passes; fails when the fix's two `refresh()` guards are neutered (see "Re-verification pass" below) |
| 20 | Same race via the 428/TOTP-retry path (`submitActionCode()`) | Automated, `tests/test_taiga_frontend.js` (committed) | Pass | test 2 |
| 21 | Unexpected stop while running (no toggle) — single blip re-arms `starting…`, then recovers | Automated, `tests/test_taiga_frontend.js` (committed) | Pass — unaffected by the fix | test 3 |
| 22 | Unexpected stop while running that never recovers — still surfaces `error` after 90s | Automated, `tests/test_taiga_frontend.js` (committed) | Pass — unaffected by the fix | test 4 |
| 23 | **Two overlapping toggle-off requests (duplicate dispatch)** | Manual: ad hoc Node `vm` probe, same technique as the committed suite | **FAIL** | See Defect 2 below |
| 24 | Toggle-on racing a still-in-flight toggle-off | Manual: ad hoc Node `vm` probe | Pass (frontend); server-side concurrency unverified — see "Probe A" below | not a defect |

## Regression check
Full existing suite run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` — **88/88 pass** (75 pre-existing + 13 new `test_taiga.py`), matching `docs/implementation.md`'s claim exactly. `python3 -m py_compile app/app.py` and `bash -n install.sh` / `bash -n scripts/taiga-{up,down,status}.sh` all pass (compiled to a scratch path to route around an unrelated pre-existing root-owned `app/__pycache__` permission artifact in this sandbox, not caused by this diff).

## Defects found

### Defect 1 (RESOLVED — see "Re-verification pass" above): Toggling Taiga off can leave the row falsely stuck on "starting…" then "error", even though the toggle succeeded and Taiga is correctly stopped

**Status: fixed and independently re-verified** (re-ran the exact repro below against the fixed code — settles cleanly on `stopped`; confirmed the regression test fails again when the fix is reverted). Left below verbatim for the historical record; **Defect 2 above is what currently blocks this cycle**, not this one.

- **Root cause** (`app/app.py`, rendered JS around line 1171-1194, the `refresh()` function): the "was running, now reports off → treat as a transient hiccup, re-arm 'starting' instead of flashing error" logic (`if (taigaWasRunning) { taigaPending = {startTime: Date.now()}; taigaWasRunning = false; }`) is driven by a `taigaWasRunning` flag that both `refresh()` (on every poll) **and** `toggle()` (once, synchronously, when the user clicks the toggle) write to. `toggle('taiga', ..., false, ...)` resets `taigaWasRunning = false` immediately, *before* the `POST /taiga/off` request (which blocks server-side on `docker compose down`, up to 90s per `taiga_run`'s own timeout) has resolved. If the regular 4-second `/status` poll (`setInterval(refresh, 4000)`) happens to land while that `down` is still in flight — a realistic window for a 9-container stack including Postgres + 2x RabbitMQ graceful shutdown — `refresh()` still sees `taiga: true` from the backend and **overwrites `taigaWasRunning` back to `true`**, clobbering the reset `toggle()` just did. Once `down` actually finishes and the next poll correctly reports `taiga: false`, `refresh()` now (incorrectly) treats this as an *unexpected* stop of a running instance rather than the intentional toggle-off it actually was, and re-arms the starting/90s-timeout machinery — eventually rendering `error` for a toggle that fully succeeded.
- **Repro**: exact sequence, reproduced against the real, unmodified rendered JS (extracted verbatim from `render_page()`'s `<script>` block, run in a Node `vm` context with `fetch`/`document`/`setInterval` stubbed):
  1. `refresh()` with `{taiga: true, taiga_url: "http://127.0.0.1:9000", ...}` → row shows "running — open"; internally `taigaWasRunning` becomes `true`.
  2. User clicks the toggle off: `toggle('taiga', null, false, checkboxEl)` fires, resets the pending/was-running markers, sends `POST /taiga/off` (kept unresolved in the harness, simulating `docker compose down` still running server-side).
  3. **Before that POST resolves**, a concurrent `refresh()` (the regular 4s poll) runs and gets `{taiga: true, ...}` back from `/status` (accurate — the containers genuinely haven't stopped yet) — this re-arms `taigaWasRunning = true`.
  4. The `POST /taiga/off` finally resolves (`down` completed server-side); the next `refresh()` gets `{taiga: false, taiga_url: null}` (correct — Taiga really is off now).
  5. Observed rendered `<div class="sub">`: **`starting… <span class="taiga-starting-spinner">◌</span>`** — not `stopped`, immediately after a fully successful, user-initiated toggle-off.
  6. Simulated 91 seconds later, with Taiga still legitimately off on every subsequent poll (no further state change, matching the real world where nothing restarts it): observed rendered sub text is **`<span class="taiga-err">error</span>`** — a false failure indicator for an operation that succeeded.
- **Expected vs. actual**: expected the row to settle on `stopped` shortly after a successful toggle-off (per `docs/design.md`'s own state matrix and its "avoids flickering" rationale, which was written for *unexpected* runtime failures, not for this race against the toggle-off's own in-flight request). Actual: the row shows `starting…` and then, ~90s later, a persistent false `error`, with no automatic recovery short of a full page reload (in-memory JS state) or another manual toggle.
- **Severity**: must-fix. This is a real, plausible race (not a contrived one) — `docker compose down` for a 9-service stack (Postgres + 2x RabbitMQ shutdown) realistically takes several seconds, comfortably overlapping a 4-second poll interval — and it produces materially wrong information directly contradicting the design doc's own explicit goals ("clear status language," "not relying solely on color," avoiding false-flicker) for the one thing this cycle's UI work exists to communicate: whether Taiga is actually on or off. It's also not covered by the developer's own described verification ("an explicit toggle-off correctly landing on 'stopped' (not another starting/error cycle)" — that check, as described, did not include a concurrent poll landing mid-flight during the toggle-off's own request).
- **Suggested direction** (not prescriptive — developer's call): the toggle-off's "this was intentional" signal needs to survive concurrent polls until the backend actually confirms `off`, not just be set once and left racing against `refresh()`. E.g., a flag that's only cleared once `/status` itself reports `taiga: false` following a toggle-off (rather than `toggle()` clearing `taigaWasRunning` preemptively before the POST even completes), or suppressing `refresh()`'s "was running → now isn't" transition while a toggle POST for `taiga` is still in flight.

## Overall verdict (as of the original pass — superseded below)
~~**Blocked.** One reproducible defect (Defect 1)...~~ — superseded by the re-verification pass. See below for the current verdict.

**Not a blocker, but flag for the developer's awareness (residual risk, not re-tested here):** `visudo` isn't installed in this sandbox either, so the three new sudoers lines' `visudo -cf` pass couldn't be independently re-confirmed — same gap the developer already flagged. The lines are structurally identical (no wildcards, no unusual characters) to existing lines in this file that do pass, so this is low risk, not elevated to a defect. (Still true, unaffected by this round.)

---

## Overall verdict (current, after the re-verification pass)

**Blocked — again, on a new defect.** Defect 1 is genuinely fixed: the
reviewer's original repro (a single toggle-off racing a concurrent
mid-flight poll), its 428/TOTP-retry-path variant, and both legitimate
hiccup/recovery scenarios were all re-run against the real rendered frontend
logic and behave correctly, and the fix was confirmed to be load-bearing (the
committed tests fail again when it's reverted). But probing beyond the exact
scenario already fixed — as this pass's boundary/concurrent-operations
mandate calls for — found **Defect 2**: two overlapping toggle-off requests
(a realistic, easily-triggered case given the toggle isn't disabled during a
pending action and the row visibly reverts to "on" every ~4s while a slow
`docker compose down` is still genuinely in progress) reopens the identical
false `starting…`→`error` outcome Defect 1 already described, because the
fix's `taigaOffInFlight` is a plain boolean shared across independently-
dispatched off requests rather than something that tracks "is *any* off
request still outstanding." Per process, the review pass was not carried
further once this second testing-pass failure was confirmed. Everything
evaluated in the original pass (backend `app.py` logic, `install.sh`'s
install/clone/`.env`/pre-pull logic, the three wrapper scripts, sudoers
scoping, badge contrast, all spec ACs' automatable pieces) and everything
re-confirmed in this pass (Defect 1's fix itself, the 428/TOTP-retry
extension, the hiccup/recovery paths, the full 88-test regression suite)
still stands and does not need to be re-litigated — only Defect 2's fix
needs re-review.

**Must-fix before re-review:**
1. Defect 2 above — the `taigaOffInFlight` boolean doesn't survive two
   overlapping toggle-off requests; needs a reference count, a request-
   generation token, or disabling the control during an in-flight action
   (developer's call — see "Suggested direction" under Defect 2).

**Not a blocker, but flag for the developer's awareness:**
- The `visudo` gap noted in the original pass (still unverified, still low
  risk, unchanged by this round).
- Probe A (non-blocking, see above): no server-side serialization between
  concurrent `/taiga/on` and `/taiga/off` (or two dispatches of the same
  action) — a pre-existing pattern shared with every other toggle kind in
  this app, not introduced or regressed by this fix, and not verifiable
  end-to-end in this sandbox (no working `docker compose`). Worth a look
  if/when this graduates past the single-admin-session assumption, but not
  something this cycle's narrowly-scoped frontend fix needs to solve.

---

## Third pass: re-verification of the `taigaOffPendingCount` (Defect 2) fix, harder concurrency probing, and the first full independent review

Scope of this pass, per dispatch: (1) re-verify the developer's replacement
of the single `taigaOffInFlight` boolean with a reference count,
`taigaOffPendingCount`, against the exact Defect 2 repro and against harder
probes than either defect was originally found with (3+ overlapping
toggle-offs, an off/on/off interleave, and whether the counter can get
permanently stuck instead of just momentarily wrong); (2) only if that held
up, do the first full independent review pass this backlog item has reached
(both prior passes stopped at "blocked" before getting here) — spec/design
traceability, correctness, security, simplicity, across the *entire* diff,
not just the two defect fixes.

### Re-run of the committed suite (hands-on, this session)

- `node tests/test_taiga_frontend.js` → **5/5 PASS** (tests 1-4 from the
  Defect 1 re-verification pass, plus the new 5th test for Defect 2's exact
  overlapping-off repro).
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` →
  **88/88 pass** (unchanged — this fix is frontend-`<script>`-only).
- `python3 -m py_compile` (routed to a scratch `.pyc` path, working around
  the same pre-existing root-owned `app/__pycache__` permission artifact
  noted in round 1 — unrelated to this diff), `bash -n install.sh`,
  `bash -n scripts/taiga-{up,down,status}.sh`, and `node --check` on a
  freshly re-extracted rendered `<script>` (with `TOTP_SECRET`/
  `TAIGA_ENABLED` set — an earlier attempt without those env vars silently
  reused a stale file from a previous probe run and would have been a false
  "pass"; corrected and re-run for real) — all pass.

### Confirmed load-bearing (revert-and-watch-it-fail), independently, not just re-reading the developer's own account

Mechanically reverted **only** the counter-vs-boolean edits in `app/app.py`
back to the pre-Defect-2-fix shape (`taigaOffPendingCount` → `taigaOffInFlight`,
`=== 0` checks → `!taigaOffInFlight`, `count++`/`Math.max(0, count-1)` →
`= true`/`= false`) via a scripted, function-level search-and-replace (not
hand-edited), leaving comments referencing the old name untouched (cosmetic
only) — verified afterward that no *executable* line still referenced
`taigaOffPendingCount`. Re-ran the suite against that reverted file:

- Tests 1-4 (Defect 1's fix + the two legitimate hiccup/recovery cases) —
  still **PASS**, confirming the revert didn't collaterally break anything
  unrelated.
- Test 5 (Defect 2's regression test) — **FAILED**, with the exact
  pre-fix symptom: final rendered state stuck on `starting…` after both
  overlapping off requests had genuinely succeeded.

Restored `app/app.py` from the untouched original immediately after
(`git diff --stat` confirms the restored file is byte-for-byte identical to
what was in the working tree before this probe — same 178-line diff size as
before and after). **This confirms test 5 is load-bearing, not tautological,
and that the counter fix — not something else — is what makes it pass.**

### Harder probing beyond the two scenarios already found (this pass's own mandate)

All three probes below were written fresh this session (ad hoc Node `vm`
scripts using the same harness technique as the committed suite — ES the
technique itself was already validated twice over in prior rounds, so
effort here went into the new scenarios, not re-litigating whether the
technique is sound) and run against the **real, unmodified, currently
committed-in-working-tree** rendered `<script>`.

**1. Three (not just two) overlapping toggle-off dispatches — does the
counter genuinely generalize, or was two a coincidence?**

Dispatched three independent `toggle('taiga', null, false, ...)` calls
before any of their `POST /taiga/off` resolved (`taigaOffPendingCount`
correctly read `3`), then resolved them one at a time in dispatch order,
with a `/status` poll landing after each resolution while the row was
still (accurately) reporting `taiga: true` because later off requests were
still outstanding. **Result: no false `starting…`/`error` after any of the
three resolutions**; the count stepped `3 → 2 → 1 → 0` exactly as each
request's own decrement ran, and only after the third (final) genuine
completion did the row settle on `stopped`. Confirms the fix is a real
count, not a fixed-for-two special case — **PASS**.

**2. Toggle-off, then toggle-on, then a second toggle-off, all overlapping**

Dispatched off → on → off in quick succession (all three requests left
unresolved simultaneously at first), then resolved the *on* request, then
the *first* off request, let a mid-flight poll land (correctly reporting
`taiga: true`, since the second off's `down` was still outstanding), then
resolved the second (final) off request. **Result: settles cleanly on
`stopped`, never `starting…`/`error`, at every intermediate step** — the
count went `2 → 1` after the first off resolved (the on/off pairing didn't
cross-contaminate each other's bookkeeping, since only `!on` branches touch
`taigaOffPendingCount`) and `1 → 0` after the second. **PASS** — no defect,
counter correctly tracks only off-dispatches regardless of interleaved
on-dispatches.

**3. Does the counter ever get permanently stuck above zero on a failure
path — specifically, a `fetch()` that rejects rather than resolving?**
**New finding — see "Fourth finding" below.**

Every scenario above (and the committed suite) only ever resolves the
`POST /taiga/off` fetch with an HTTP status (200, 428) — a *successful*
round-trip to the server, even when the server's own answer is an error
status. Per this pass's explicit mandate to check the failure path, not
just the success path, this probe instead **rejected** the fetch promise
itself (simulating a real network-layer failure — e.g. a connection drop
mid-request — as opposed to the server responding with a non-2xx status,
which the code already handles correctly since the decrement isn't gated on
`r.status`). Result: **the counter never recovers** — see below.

### Fourth finding (new, this pass): a rejected (network-level-failed) `POST /taiga/off` leaks `taigaOffPendingCount` permanently, silently disabling the "unexpected stop while running" detection for the rest of the page's life

- **Root cause**: both `toggle()`'s off branch and `submitActionCode()`'s
  off-retry branch increment `taigaOffPendingCount` immediately before
  `await performAction(...)`, then decrement it on the very next line
  *after* that `await` — with no `try`/`catch`/`finally`. If the `fetch()`
  promise itself **rejects** (a real network-layer failure — connection
  reset, DNS blip, the browser genuinely failing to complete the round-trip
  — distinct from the server responding with an HTTP error status, which
  *is* handled correctly since the decrement doesn't check `r.status`), the
  `await` throws, `toggle()`/`submitActionCode()` return a rejected promise,
  and the decrement line **never runs**. `taigaOffPendingCount` is left
  incremented forever — there is no code path anywhere in the script that
  can bring it back down once this happens (a later, separate, fully
  successful off dispatch only brings it back to 1, not 0, since its own
  decrement only undoes its own increment).
- **This is not new to the Defect 2 fix** — I confirmed by reading
  `docs/test-review.md`'s own historical record of the Defect 1 fix (the
  single-boolean version): it had the exact same shape (`taigaOffInFlight =
  true` before the `await`, `taigaOffInFlight = false` unconditionally
  after, no `try`/`catch`), so a rejected fetch would have left it stuck
  `true` forever too. This gap has existed since the *first* post-review fix
  and was not caught by either of the first two testing passes, because
  neither one's probes ever rejected a fetch — both (and the committed
  suite) only ever resolve `/taiga/off` with a status code, which is a
  different code path (handled fine) from an outright network failure.
- **Effect once triggered**: `refresh()`'s guard (`if
  (taigaOffPendingCount === 0) taigaWasRunning = true;`) never fires again
  for the rest of the page's life, because the count never returns to `0`.
  This means `taigaWasRunning` can never become `true` again, which means
  the *other* guard (`if (taigaWasRunning && taigaOffPendingCount === 0)`)
  can also never fire. Concretely: after the leak, if Taiga is later
  legitimately running and then genuinely crashes unexpectedly (nobody
  touched the toggle — the exact scenario `docs/design.md`'s "Docker
  daemon not running / misconfigured → docker compose calls fail; same
  timeout path → 'error'" and "runtime failure... polling catches it"
  language explicitly calls for), the row silently shows `stopped`
  immediately instead of `starting…` then, after 90s, `error`. This is the
  *opposite* failure mode from Defects 1/2 (which produced a false
  *positive* error) — this one produces a silent false *negative*,
  permanently masking `docs/design.md`'s own explicitly-required crash
  detection for the remainder of the browser session (until a full page
  reload, which resets all in-memory JS state, same recovery path already
  accepted elsewhere in this document for the pre-Defect-1-fix bug).
- **Repro** (reproduced against the real, unmodified rendered `<script>`,
  same extraction technique as the committed suite):
  1. `refresh()` reports `taiga: true` → row shows `running`.
  2. User clicks toggle off: `toggle('taiga', null, false, cb)` — sends
     `POST /taiga/off` #1. `taigaOffPendingCount` → `1`.
  3. The fetch **rejects** (simulated network failure, not an HTTP error
     status) instead of resolving. `toggle()`'s `await performAction(...)`
     throws; the line that would decrement `taigaOffPendingCount` never
     runs. `taigaOffPendingCount` stays `1` forever.
  4. A later poll (correctly) still reports `taiga: true` (Taiga never
     actually stopped — the off click never reached the server). Row shows
     `running`, as expected — no immediately visible symptom yet.
  5. Sometime later, Taiga genuinely, unexpectedly crashes — no toggle
     touched. The next poll reports `taiga: false`.
  6. Observed rendered `<div class="sub">`: **`stopped`** — not `starting…`
     then `error` after 90s, which is what `docs/design.md`'s crash-
     detection logic is supposed to produce for exactly this scenario, and
     which the row would correctly have produced had the leak never
     happened (confirmed by the same steps run without the rejected fetch
     in step 3 — see the "unexpected stop while running" tests already in
     the committed suite, which this leak silently defeats once triggered).
- **Severity**: **should-fix, not a blocker for this round.** Reasoning:
  - It is not a regression introduced by this round's `taigaOffPendingCount`
    fix — the identical gap already existed in the boolean version shipped
    for Defect 1, undetected by two prior testing passes, and this round's
    fix does not make it any better or worse (same shape, different
    variable).
  - It does not reproduce either Defect 1 or Defect 2 — it's a third,
    distinct, orthogonal failure mode (silent false-negative masking vs.
    both defects' false-positive `error`).
  - Triggering it requires an actual `fetch()`-level rejection (a genuine
    network-layer failure over the up-to-90s window the `off` POST can be
    outstanding for — plausible on a flaky connection, a mid-request
    `systemctl restart` of the service, a Tailscale reconnect, etc., but
    meaningfully rarer than Defect 2's "ordinary impatient double-click,"
    which required no network fault at all) — a non-2xx HTTP response
    (the much more common failure shape) is already handled correctly.
  - Recoverable by a full page reload, consistent with this document's
    existing acceptance of "no automatic recovery short of a full page
    reload" for JS-in-memory-state bugs elsewhere in this feature.
- **Suggested direction** (not prescriptive): wrap the decrement in a
  `finally` block (or equivalent `try`/`catch`) around the `await
  performAction(...)` calls in both `toggle()` and `submitActionCode()`'s
  off paths, so the count is decremented whether the request resolves or
  rejects — the same fix shape would also have closed this gap in the
  original Defect 1 boolean, had it been applied there.

### Independent review pass (first time this backlog item has reached this stage — both prior passes stopped at "blocked")

**Spec-to-code traceability**: re-read `docs/spec.md`'s 9 acceptance
criteria and its "Edge cases" section fresh against the current diff. All 9
ACs still map to the test-case table below (rows 1-9), unchanged from round
1 — nothing new to cover; spec.md's content growth in this round's `git
diff --stat` is prior rounds' review-driven elaboration, not new
functional scope. Spec's own "Rapid double-toggle" edge case explicitly
scopes *backend* idempotency only ("`docker compose up -d`/`down` are both
idempotent by design... no new locking needed here either") — confirmed
this is a deliberate, explicit design decision, not a gap Probe A
(round 2) or this round's probes should be held against; the *frontend*
state-machine correctness (Defects 1/2/this pass's 4th finding) is a
separate concern the spec doesn't explicitly enumerate but is still
correctly in scope as "the UI must not lie about Taiga's actual state,"
which is this feature's entire point per `docs/design.md`.

**Correctness (full diff, not just the two fixes)**: read `app/app.py`'s
entire Taiga-related diff line by line this pass (config reads, `taiga_run`,
`_taiga_display_url`, the `/status` block, the `do_POST` `taiga` branch,
`row()`'s new params, `refresh()`'s state machine, `toggle()`/
`submitActionCode()`/`handleActionResult()`/`cancelActionCode()`), plus
`install.sh`'s full `--with-taiga` block and the three wrapper scripts.
Nothing beyond the 4th finding above stood out:
- `do_POST`'s `taiga` branch sits after the shared TOTP gate and mirrors
  `host`'s branch shape exactly; `on`/`off` order (`taiga_run("up")` then
  `_publish`; `_unpublish` then `taiga_run("down")`) is correct — publish
  only after the stack is asked to come up, unpublish before asking it to
  stop.
- `taiga_run()` builds `["sudo", script]` with `script` drawn from one of
  three fixed module-level constants (never user input) — no argv
  injection surface. The three wrapper scripts are zero-argument, hardcoded
  `$TAIGA_DIR` fallback, matching the sudoers entries' exact zero-argument
  scoping (no trailing ` *`) — narrower than the `new-project*.sh` sudoers
  lines already in this file, appropriately so given Docker socket access
  is root-equivalent.
- `row()`'s `subOverride`/`showTaigaBadge` params default `undefined` and
  every existing non-`taiga` call site (`inst`, `host`) omits them —
  confirmed unchanged behavior there (already covered by the 88/88 Python
  suite, which exercises `host` rows, and by this pass's own re-run of the
  frontend suite, whose `setupCase()` baseline includes a `host_enabled:
  false` row alongside every taiga-focused assertion).
- `install.sh`'s Docker-install/clone/`.env`/pre-pull/wrapper-install/
  sudoers/summary block: re-read in full this pass — consistent with round
  1's structural findings, nothing changed here since (confirmed via
  `docs/implementation.md`'s explicit claim that Defect 1/2's fixes were
  frontend-`<script>`-only, cross-checked against the actual `git diff
  --stat` above, which shows `install.sh` unchanged in this round beyond
  what round 1 already reviewed).

**Security**: no new injection surface (all subprocess argv is fixed
constants, not user/request input); the TOTP/session gate is inherited
automatically (no bespoke Taiga auth code, matching AC8); no secrets are
logged or returned in any response body; sudoers scoping is the narrowest
in the file (zero-argument, matching the spec's own "Crossing the privilege
boundary" reasoning for why Docker access can't be narrowed further than
"exactly these three scripts, no arguments"). Nothing found.

**Simplicity/scope**: the `taigaOffPendingCount` fix is minimal and
scoped exactly to the defect (four edit sites: declaration, two `refresh()`
guards, the increment/decrement pairs in the two dispatch paths) — no new
abstractions, no speculative generality, and the developer's explicit
choice not to add checkbox-disabling (which would have touched
`row()`/`performAction()`/`handleActionResult()`/the 401/cancel paths too)
is a reasonable, well-justified minimal-diff call, consistent with this
pipeline's own stated philosophy. No dead code left behind from either fix
round (confirmed `taigaOffInFlight` only survives in comments/historical
docs, never in executable code — see grep above).

**Recomputed the WCAG contrast independently a third time** (not trusting
either `docs/design.md`'s or `docs/implementation.md`'s numbers, and not
trusting round 1's own recorded numbers without redoing the arithmetic):
using the real WCAG relative-luminance formula from the literal hex values,
`L(#16324a) ≈ 0.02946`, existing `.badge` (`#4da6ff`) contrast `≈ 5.17:1`,
new `.badge.taiga-ram` (`#66d9ff`) contrast `≈ 8.14:1` — matches
`docs/implementation.md`'s claimed numbers exactly, both clear the relevant
thresholds with real headroom.

### Updated test cases (this pass)

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 25 | Defect 2 fix re-verified against the exact original repro | Automated, `tests/test_taiga_frontend.js` test 5 (committed) | Pass | `node tests/test_taiga_frontend.js` → 5/5; confirmed load-bearing via scripted revert-and-watch-it-fail (test 5 fails on the reverted boolean, tests 1-4 unaffected), then restored |
| 26 | **Three (not two) overlapping toggle-off dispatches** | Manual, ad hoc Node `vm` probe (harder than the committed test) | Pass — counter genuinely generalizes | See "Harder probing" #1 above |
| 27 | Toggle-off → toggle-on → toggle-off, all overlapping | Manual, ad hoc Node `vm` probe | Pass — no cross-contamination between on/off bookkeeping | See "Harder probing" #2 above |
| 28 | **Rejected (network-failed) `POST /taiga/off` — does the counter ever get permanently stuck?** | Manual, ad hoc Node `vm` probe | **FAIL** (new finding, should-fix not blocking — see "Fourth finding" above) | `taigaOffPendingCount` leaks to a permanent nonzero floor, silently disabling future crash detection |

## Regression check (this pass)
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` →
**88/88 pass**. `node tests/test_taiga_frontend.js` → **5/5 pass**.
`python3 -m py_compile app/app.py` (scratch-path workaround for the
pre-existing `__pycache__` permission artifact), `bash -n install.sh`,
`bash -n scripts/taiga-{up,down,status}.sh`, `node --check` on a freshly
re-extracted rendered `<script>` (with `TOTP_SECRET`/`TAIGA_ENABLED` set,
not reusing a stale scratch file) — all pass. `git status --short` /
`git diff --stat` after all probing confirms the working tree is
byte-for-byte what it was before this pass started — no residue from any
revert-and-restore probe.

## Defects found (this pass)

### Defect 2 (RESOLVED — confirmed this pass): two overlapping toggle-off requests reopened Defect 1's race via a shared boolean

**Status: fixed and independently re-verified**, including against harder
probes than the original repro (3+ overlapping off requests, an
off/on/off interleave) — the counter is a real reference count, not a
fixed-for-two special case. See "Third pass" above for the full
re-verification. Left in the "Re-verification pass" section above verbatim
for the historical record.

### New Finding: `taigaOffPendingCount` never decrements if its `POST /taiga/off` rejects (network failure) rather than resolving, silently and permanently disabling the "unexpected stop while running" crash-detection this feature's own design doc requires

**Status: should-fix, not blocking this round.** Full root cause, repro,
and severity reasoning under "Fourth finding" above. Pre-existing since the
Defect 1 fix (same shape, different variable name), not introduced or
worsened by this round's Defect 2 fix, does not reproduce either target
defect, requires a genuine network-layer failure (not just a non-2xx HTTP
response, which is already handled correctly) to trigger, and is
recoverable by a page reload. Suggested direction: wrap the
increment/decrement pair in `try`/`finally` (or equivalent) in both
`toggle()`'s and `submitActionCode()`'s off paths.

## Overall verdict (final, after the third pass)

**Approve, with one non-blocking follow-up.**

Both defects this backlog item's review history identified (Defect 1: a
single toggle-off racing a concurrent poll; Defect 2: two overlapping
toggle-off dispatches reopening the same race via a shared boolean) are
genuinely fixed, independently re-verified hands-on this session (not
inferred from the developer's account), and confirmed load-bearing via
scripted revert-and-watch-it-fail checks against the real rendered
`<script>`. The `taigaOffPendingCount` reference-count fix was probed
harder than either original defect required — three-and-more overlapping
off dispatches, and an off/on/off interleave — and holds up correctly in
both; it is a genuine reference count, not a boolean-shaped fix for
exactly two overlapping requests. The full regression suite (88 Python
tests + 5 frontend tests) passes, and this pass's independent review of the
*entire* diff (not just the two defect fixes) — spec traceability,
correctness, security, simplicity, and an independently recomputed WCAG
contrast check — found nothing else rising to must-fix.

One new, narrower issue was found by probing beyond what either original
defect covered (a rejected/network-failed `POST /taiga/off`, as opposed to
a resolved-with-an-error-status one, leaks `taigaOffPendingCount`
permanently and silently disables this feature's crash-detection for the
rest of the page's life). This is real and worth fixing, but is
**not a blocker for this round**: it predates this round's fix (present
identically in the Defect 1 boolean, missed by two prior passes), doesn't
reproduce either target defect, needs a genuine network-layer fault to
trigger (not just an HTTP error response), and is recoverable by a page
reload. Recommended as a follow-up item, not a required fix before this
cycle closes.

**Should-fix (non-blocking follow-up):**
1. Wrap the `taigaOffPendingCount` increment/decrement pairs in
   `toggle()`'s and `submitActionCode()`'s off paths in `try`/`finally` (or
   equivalent), so a rejected (network-failed) `POST /taiga/off` doesn't
   leak the counter permanently — see "Fourth finding" above for the full
   repro and reasoning.

**Not a blocker, unchanged from prior rounds:**
- The `visudo` gap (still unverified in this sandbox, still low risk).
- Probe A / no server-side serialization between concurrent `/taiga/on`
  and `/taiga/off` — confirmed this round to be an explicit, deliberate
  spec decision (`docs/spec.md`'s "Rapid double-toggle" edge case), not a
  gap; unaffected by anything in this round's fix or findings.

**Everything else evaluated across all three passes** (backend `app.py`
logic, `install.sh`'s install/clone/`.env`/pre-pull logic, the three
wrapper scripts, sudoers scoping, badge contrast recomputed three times
independently and matching each time, all 9 spec ACs' automatable pieces,
Defect 1's fix, Defect 2's fix) stands and does not need to be
re-litigated further.
