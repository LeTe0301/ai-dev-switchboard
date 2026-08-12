# Implementation: Local backlog tracker (Taiga) — part 1a: install flag + singleton UI row

Adds an optional `install.sh --with-taiga` flag that installs Docker +
Taiga's own official `taiga-docker` Compose stack (this codebase's
first-ever Docker dependency, per-spec user-approved), left stopped after
install, plus a singleton on/off toggle row in the web UI mirroring the
existing host-control row (`POST /taiga/on`, `POST /taiga/off`, `/status`'s
`taiga`/`taiga_enabled`/`taiga_label`/`taiga_url` fields). Follows
`docs/spec.md` and `docs/design.md` closely; the one deliberate departure
from `docs/design.md` is described under "Deviations" below (the
resource-cost badge contrast fix).

## What changed, by file

### `install.sh`

- New `WITH_TAIGA=0` flag var (line 57) + `--with-taiga) WITH_TAIGA=1 ;;`
  case arm (line 64), following the exact pattern of the other `--with-*`
  flags.
- New gated block (lines 235-340), placed **after** the `-- Publishing --`
  section rather than immediately after the code-server block the spec's
  line reference pointed at — see "Deviations" below for why. It:
  1. Installs Docker via `curl -fsSL https://get.docker.com | sh` if
     `docker` isn't already on the box (idempotent — never touches an
     existing install); verifies `docker compose version` works, warning
     and continuing (not aborting) if the Compose plugin is missing.
  2. Clones `taigaio/taiga-docker` (`--branch stable --depth 1`) to
     `/opt/ai-dev-switchboard-taiga` if not already present; never
     `git pull`'d on re-run.
  3. Writes `taiga-docker`'s own `.env` (verified against the live `stable`
     branch — see "Key decisions" for the real key names, which differ
     slightly from the spec's rough sketch): `SECRET_KEY`,
     `POSTGRES_PASSWORD`, `RABBITMQ_PASS`, `RABBITMQ_ERLANG_COOKIE` are
     randomized **only right after a fresh clone** (never re-randomized on
     re-run — see "Key decisions" for why this needed a different
     idempotency mechanism than `TOTP_SECRET`'s "empty means generate"
     check). `TAIGA_SCHEME=http`, `TAIGA_DOMAIN`, and `TAIGA_PORT` are
     re-derived and rewritten every run (not one-time secrets).
  4. Writes `docker-compose.override.yml` binding `taiga-gateway` to
     `127.0.0.1:${TAIGA_PORT}:80` (regenerated deterministically every run;
     `${TAIGA_PORT}` is deliberately left for Compose itself to substitute
     from `taiga-docker`'s own `.env`, not expanded by this shell).
  5. Pre-pulls all 9 images (`docker compose ... pull`), warn-and-continue
     on failure.
  6. Installs the three wrapper scripts to `/usr/local/bin/`.
  7. Writes `TAIGA_ENABLED=1`, `TAIGA_PORT`, `TAIGA_LABEL`, `TAIGA_DIR`,
     `TAIGA_UP_SCRIPT`/`TAIGA_DOWN_SCRIPT`/`TAIGA_STATUS_SCRIPT` into
     `switchboard.env`.
- Sudoers block (existing `-- sudoers --` section) gained three zero-argument
  entries gated on `WITH_TAIGA` (lines 371-378).
- Final `== Done ==` summary gained a Taiga-specific block (lines 446-455):
  RAM/disk warning, and a pointer to `taiga-docker`'s own
  `./taiga-manage.sh createsuperuser` one-time admin-creation step (verified
  against the live `stable` branch's README — see "Key decisions").

### `scripts/taiga-up.sh`, `scripts/taiga-down.sh`, `scripts/taiga-status.sh` (new)

Three fixed, zero-argument, root-run wrapper scripts, following
`host-agent/host-{start,stop,status}.sh`'s exact shape (source
`/etc/ai-dev-switchboard/switchboard.env` if present, fall back to a
hardcoded default `TAIGA_DIR`, same idiom `new-project-from-upload.sh`
already uses). `taiga-up.sh`/`taiga-down.sh` `cd "$TAIGA_DIR" && exec docker
compose -f docker-compose.yml -f docker-compose.override.yml {up -d|down}`.
`taiga-status.sh` prints `on`/`off` on the first line based on `docker
compose ps taiga-gateway --format '{{.State}}'` (taiga-gateway has no
Compose healthcheck upstream, so `State` becomes `running` shortly after the
container starts — confirmed against the live `docker-compose.yml`, not
assumed).

### `app/app.py`

- New config reads (line ~120-127, alongside `HOST_CONTROL_ENABLED` etc.):
  `TAIGA_ENABLED`, `TAIGA_LABEL`, `TAIGA_PORT`, `TAIGA_UP_SCRIPT`,
  `TAIGA_DOWN_SCRIPT`, `TAIGA_STATUS_SCRIPT`.
- `taiga_run(action)` (line 945) and `_taiga_display_url()` (line 957),
  placed right after `host_run()`, matching the spec's code sample exactly
  (10s timeout for `status`, 90s for `up`/`down`; `_taiga_display_url()`
  returns `f"{BASE_URL}/taiga"` in `tailscale` mode, else
  `http://127.0.0.1:{TAIGA_PORT}`).
- `/status` (`do_GET`) gained a `taiga_on, taiga_url` block that calls
  `taiga_run("status")` fresh on every poll (never trusted from memory —
  same reasoning as `host_run("status")`), and the response gained
  `taiga_enabled`/`taiga_label`/`taiga`/`taiga_url` fields.
- `do_POST` gained a `parts[0] == "taiga"` branch (line 2291) sitting after
  the shared TOTP gate, mirroring the `host` branch: 404 if
  `TAIGA_ENABLED` is false; on `on`, `taiga_run("up")` then
  `_publish(TAIGA_URL_PATH, TAIGA_PORT)`; on `off`,
  `_unpublish(TAIGA_URL_PATH)` then `taiga_run("down")`.
- CSS: `.badge.taiga-ram` (brightened text — see "Deviations"),
  `.taiga-err`, `.taiga-starting-spinner` + `@keyframes taiga-spin` (from
  `docs/design.md`'s exact snippet).
- `row()` gained two new optional trailing params, `subOverride` and
  `showTaigaBadge` — every existing call site (`inst`, `host`) is unchanged
  (both params default to `undefined`, which falls through to the original
  on/off sub-text computation and no badge). This is the "entirely via the
  existing `row()` function" reuse `docs/design.md` calls for.
- `refresh()` gained the Taiga row + its 4-state machine (`stopped` /
  `starting…` + spinner / `running — open` / `error`), including the
  design's "was running, briefly reports off → re-arm starting instead of
  flashing error" flicker-avoidance behavior and the 90-second timeout
  fallback. `toggle()`, `handleActionResult()` (401 path), and
  `cancelActionCode()` were each given a small `kind === 'taiga'`
  branch to keep the optimistic `taigaPending`/`taigaWasRunning` state
  consistent when a toggle-on gets reverted (401, or the user cancels the
  TOTP prompt) rather than only when it succeeds — see "Key decisions" for
  why this went beyond `docs/design.md`'s own (admittedly abstract)
  pseudocode.
- `actionPath()` gained the `kind === 'taiga'` branch from the spec.
- **Post-review fix (`docs/test-review.md` Defect 1)**: added a third piece
  of state, `taigaOffInFlight`, alongside `taigaPending`/`taigaWasRunning` —
  see "Fix: Defect 1" below for the full race and the fix.
- **Post-review fix (`docs/test-review.md` Defect 2)**: replaced
  `taigaOffInFlight` (a plain boolean) with `taigaOffPendingCount` (a
  reference count) — see "Fix: Defect 2" below.

### `config/switchboard.env.example`

New `## Optional: self-hosted Taiga (--with-taiga)` section, matching the
depth of the existing `HOST_CONTROL_ENABLED` section, plus a short note that
`TAIGA_DIR` is also written there but only read by the wrapper scripts, not
`app.py` itself.

### `tests/test_taiga.py` (new)

23 assertions across three `unittest.TestCase` classes, following this
repo's existing convention (`tests/test_upload.py`'s
`CreateProjectsFromSelectionTests`) of monkeypatching the one function that
shells out (`appmod.taiga_run`) rather than mocking `subprocess` globally:

- `TaigaRunTests` — `taiga_run()` itself: correct script path + timeout per
  action, asserts on an invalid action.
- `TaigaDisplayUrlTests` — `_taiga_display_url()` in both `PUBLISH_MODE`s.
- `TaigaEndpointTests` — real `ThreadingHTTPServer` + `appmod.Handler`,
  `taiga_run` monkeypatched to a stateful fake (mirrors what dockerd would
  actually report): `/status` fields when off/on, `POST /taiga/{on,off}`'s
  428/403/200 flow through the shared TOTP gate, the 404-when-disabled path
  (asserting `taiga_run` is never called), and 401 on an unauthenticated
  `/status`.

### `tests/test_taiga_frontend.js` (new)

A Node `vm`-based regression harness for the Taiga toggle's frontend state
machine, extracting the real, rendered `<script>` from `app.render_page()`
at test-run time (via a `python3 -c` subprocess, not a hand-copied
snapshot) and running it with `document`/`fetch`/`setTimeout`/
`setInterval`/`Date.now` stubbed — see "Fix: Defect 1 (toggle-off /
concurrent-poll race)" below for why this exists and what it covers. Run
with `node tests/test_taiga_frontend.js`.

## Fix: Defect 1 (toggle-off / concurrent-poll race)

Post-review fix for `docs/test-review.md`'s "Defect 1" — the one must-fix
finding from the first review pass on this backlog item. Everything else in
this document above and below describes the original (already-reviewed)
implementation; this section documents only the fix, scoped entirely to the
frontend `<script>` state machine in `app/app.py` (no backend/`install.sh`/
wrapper-script changes).

**The bug** (full root cause in `docs/test-review.md` Defect 1): `toggle()`
reset `taigaWasRunning = false` synchronously the instant the user clicked
the toggle off, *before* the `POST /taiga/off` request — which blocks
server-side on `docker compose down`, up to 90s for the 9-service stack —
had resolved. If the regular 4-second `/status` poll landed while that
request was still in flight, it would (correctly, at that instant) see
`taiga: true` and clobber `taigaWasRunning` back to `true`. Once `down`
actually finished and the next poll correctly reported `taiga: false`,
`refresh()`'s "was running, now isn't → treat as a transient hiccup, re-arm
starting" logic then misread the successful toggle-off as an *unexpected*
stop, eventually rendering a false `error` for an operation that fully
succeeded.

**The fix**: added a third flag, `taigaOffInFlight`, that's `true` for the
exact window between an intentional toggle-off's request being sent and it
resolving:

- `refresh()`'s `s.taiga === true` branch no longer writes
  `taigaWasRunning = true` while `taigaOffInFlight` is set — a poll landing
  mid-flight can no longer clobber the toggle-off's own reset.
- `refresh()`'s "was running → now isn't → re-arm starting" transition also
  checks `!taigaOffInFlight` as an extra defensive guard (redundant given
  the invariant above, but makes the suppression explicit at the point
  where the bug actually manifested).
- `taigaOffInFlight` is set immediately before, and cleared immediately
  after, *every* request that can actually trigger `taiga_run("down")`
  server-side — both `toggle()`'s direct path and `submitActionCode()`'s
  retry path (the request that follows a 428 TOTP-code prompt; the initial
  428 response itself never touches the server, so it doesn't need the
  guard, but the retried request that follows it does, and
  `taigaWasRunning` is reset to `false` again at that point too, since polls
  may have run — correctly setting it back to `true` — during however long
  the user took to type the code).

This distinguishes the two scenarios the pre-fix logic conflated: an
intentional toggle-off (now tracked, suppresses the transition until the
backend actually confirms `off`) versus Taiga crashing unexpectedly while
the user did nothing (`taigaOffInFlight` never set, so the existing
"transient hiccup → re-arm starting, don't flash error immediately, but
still error out after 90s if it never recovers" behavior is completely
unchanged — verified below).

## Fix: Defect 2 (overlapping toggle-off requests reopened Defect 1's race)

Post-review fix for `docs/test-review.md`'s "Defect 2" — the one must-fix
finding from the second (re-verification) review pass, found while probing
beyond the exact scenario Defect 1's fix already covered. Scoped entirely to
the same frontend `<script>` state machine in `app/app.py` as the Defect 1
fix above; no backend/`install.sh`/wrapper-script changes.

**The bug** (full root cause + repro in `docs/test-review.md` Defect 2):
`taigaOffInFlight` (the flag the Defect 1 fix introduced) was a single
shared boolean, not a reference count or a token tied to a specific
in-flight request. Both `toggle()` and `submitActionCode()` set it `true`
before dispatching their own `POST /taiga/off` and unconditionally set it
back to `false` the instant *their own* request resolved, with no awareness
of whether a second, independently-dispatched off request was still
outstanding. Because the toggle checkbox is never disabled while an action
is in flight, and `refresh()`'s 4-second poll keeps re-rendering the row as
checked for as long as the (accurate, still-in-progress) `docker compose
down` hasn't finished, an impatient user re-clicking the toggle during that
up-to-90s window fires a second, genuine `POST /taiga/off` while the first
is still outstanding — no precise timing luck required, just an ordinary
user reacting to what the UI is accurately showing them. If the first of
the two off requests resolved first, its completion cleared the shared flag
globally, even though the second `down` was still genuinely running. A
`/status` poll landing in that reopened window (correctly) reported
`taiga: true`, and with the flag now (wrongly) `false`, `refresh()` re-armed
`taigaWasRunning = true` — reopening the exact false
`starting…`→`error` end state Defect 1's fix was built to close, via a
different trigger.

**The fix**: replaced the single boolean `taigaOffInFlight` with a reference
count, `taigaOffPendingCount` (int, starts at 0):

- `toggle()`'s off branch and `submitActionCode()`'s off-retry branch each
  `taigaOffPendingCount++` immediately before dispatching their own
  `POST /taiga/off`, and `taigaOffPendingCount = Math.max(0,
  taigaOffPendingCount - 1)` immediately after their own request resolves —
  each dispatch owns exactly one increment/decrement pair, regardless of how
  many other off requests are concurrently in flight.
- `refresh()`'s two guards (the "don't let a mid-flight poll re-arm
  `taigaWasRunning`" branch, and the "was running → now isn't → re-arm
  starting" transition) now check `taigaOffPendingCount === 0` instead of
  `!taigaOffInFlight` — "no off request in flight" is only true once *every*
  dispatched off request has resolved, not just the most recent one to
  finish.

This directly addresses the root cause the reviewer identified (a shared
boolean can't distinguish "zero off requests outstanding" from "one of
several just finished, others still running") without changing the
interaction model: the checkbox still isn't disabled during a pending
action (matching every other toggle kind — `inst`, `host`, `code` — none of
which disable their control while in flight either), so a second dispatch
can still happen, but the state machine now correctly tracks it instead of
losing track of it. Considered disabling the checkbox during a pending
Taiga action instead (the reviewer's other suggested direction, arguably
also reasonable given Taiga's uniquely long ~90s window) but chose the
reference-count fix as the more minimal, surgical change: it fixes the
actual defect (state tracking, not UI affordance) without introducing a new
interaction pattern that every other toggle kind in this app doesn't have,
and without touching `row()`/`performAction()`/`handleActionResult()` or
adding new disabled/re-enable bookkeeping across the 401/403/cancel paths.

**Regression test**: extended `tests/test_taiga_frontend.js` with a 5th
test, `two overlapping toggle-off dispatches: first resolving must not let
a mid-flight poll re-arm starting while the second is still outstanding` —
dispatches two independent `toggle('taiga', null, false, ...)` calls before
either's `POST /taiga/off` resolves, resolves the first, lets a poll land
mid-flight (correctly reporting `taiga: true`, since the second `down` is
still running), resolves the second, then asserts the final state is
`stopped`, never `starting…`/`error`.

**Verified the new test actually catches the bug, and that the fix doesn't
regress anything else**:
1. Wrote the test first (TDD) and ran it against the pre-Defect-2-fix code
   (`taigaOffInFlight` boolean, i.e. the Defect-1-fix-only state) — it
   failed exactly as Defect 2 describes (final state stuck on `starting…`),
   while tests 1-4 (Defect 1's fix + the two hiccup/recovery cases) still
   passed unaffected — confirming the new test isolates Defect 2
   specifically, not some other latent issue.
2. Implemented the `taigaOffPendingCount` fix; re-ran — all 5/5 pass.
3. Re-confirmed load-bearing-ness by mechanically reverting *only* this
   fix's edits (counter → boolean, functionally identical to step 1) back
   to a temp copy, re-running — test 5 failed again, tests 1-4 still passed
   — then restored the fixed file and re-ran — 5/5 pass again, `diff`
   confirmed byte-for-byte restoration of the intended fixed state.
4. Re-ran the full existing regression set: `python3 -m unittest discover
   -s tests` → 88/88 pass (unchanged — this fix is frontend-`<script>`-only,
   same as Defect 1's fix), `python3 -m py_compile app/app.py`, `bash -n
   install.sh`, `bash -n scripts/taiga-{up,down,status}.sh`, and `node
   --check` on the freshly re-extracted rendered `<script>` — all pass.

**Regression test**: `tests/test_taiga_frontend.js` (see "What changed,
by file" above), run against the real rendered `<script>`:

1. `toggle-off race: concurrent poll mid-flight still settles on stopped`
   — reproduces the reviewer's exact repro (toggle-off click, POST left
   unresolved, a concurrent poll lands mid-flight and correctly reports
   `taiga: true`, mock time is advanced 95s total — past what would have
   been the false-error window under the old bug — before the POST
   resolves) and asserts the final state is `stopped`, never `starting`/
   `error`.
2. `toggle-off race via the 428/TOTP-code retry path also settles on
   stopped` — same race, but through `submitActionCode()`'s retry path
   (the request that follows a 428), confirming that path is equally
   covered.
3. `unexpected stop while running (no toggle): single blip re-arms
   starting, then recovers` — the legitimate case this logic exists for:
   Taiga stops reporting `on` with no toggle ever clicked, must show
   `starting…` (not `stopped`, not `error`) on the first missed poll, then
   cleanly recover to `running` if it comes back — confirming the fix
   didn't touch this path's behavior.
4. `unexpected stop while running that never recovers still surfaces error
   after 90s` — same scenario, but never recovers; confirms the pre-existing
   90s timeout → `error` fallback still fires correctly when nobody
   toggled anything.

**Verified the tests actually catch the bug**: temporarily reverted just
the four `taigaOffInFlight`-related edits (the flag's declaration, the two
`refresh()` guards, and the set/clear points in `toggle()`/
`submitActionCode()`) back to the pre-fix code, re-ran the suite — tests 1
and 2 (the two race tests) failed exactly as expected (final state
`starting…`, not `stopped`), tests 3 and 4 (the untouched legitimate-crash
path) still passed. Restored the fix and re-ran — all 4/4 pass.
`python3 -m unittest discover -s tests -v` still 88/88, `python3 -m
py_compile app/app.py`, `bash -n install.sh`, `bash -n
scripts/taiga-{up,down,status}.sh`, and `node --check` on the freshly
re-extracted rendered `<script>` all still pass — no regressions from this
fix.

## Fix: Defect 3 (taigaOffPendingCount leaked on a network-level failure)

Non-blocking follow-up the reviewer flagged when approving the Defect 2 fix:
`taigaOffPendingCount++`/`--` in `toggle()` and `submitActionCode()` sat
directly around `await performAction(...)` with no `try`/`finally`.
`performAction()` just returns `fetch(...)` directly with no catch of its
own, so a genuine network-level failure (the connection drops while
`docker compose down` is still running server-side — a rejected promise,
not just a resolved non-2xx response) propagated straight out of the
`await`, skipping the decrement entirely. The counter would leak upward
permanently, silently and irreversibly suppressing `refresh()`'s
`taigaOffPendingCount === 0` guards — including the legitimate "unexpected
stop while running" hiccup-detection path — for the rest of the page's
life, with no way to recover short of a full reload.

**The fix**: wrapped each call site's `await performAction(...)` +
`handleActionResult(...)` in `try { ... } finally { decrement }`, so the
decrement always runs — on success, on a resolved error response, and on a
rejected fetch promise alike. Deliberately kept as a `finally`, not a
`catch` — the failure itself isn't swallowed or handled here (it still
propagates to the caller exactly as before, matching the app's existing
error-handling shape elsewhere), only the counter bookkeeping is made
unconditional.

**Regression test**: added a 6th test to `tests/test_taiga_frontend.js`,
`a network-level failed toggle-off still releases taigaOffPendingCount
(does not leak)`. Since `toggle()`'s off branch itself directly sets
`taigaWasRunning = false` regardless of outcome, that alone can't
distinguish "the counter released" from "it leaked" — so the test instead:
dispatches a toggle-off that rejects at the network level; polls again
reporting Taiga is (still) genuinely running, which only re-arms
`taigaWasRunning` via the `taigaOffPendingCount === 0` guard (a leaked
counter would keep this permanently suppressed); then polls once more
reporting an unrelated, genuine unexpected stop and asserts the
hiccup-detection `starting…` state actually fires — which is only possible
if `taigaWasRunning` got re-armed in the prior step, which only happens if
the counter actually reached 0 after the failed request.

**Verified the test is load-bearing**: reverted just the two `try`/`finally`
edits back to the unconditional pre-fix form, re-ran — the new test 6
failed exactly as expected (final poll showed `stopped` instead of
`starting…`, i.e. the leaked counter suppressed hiccup detection), tests
1-5 unaffected. Restored the fix — `node tests/test_taiga_frontend.js` →
6/6 pass. `python3 -m unittest discover -s tests -v` → 88/88 pass (still
frontend-`<script>`-only), `bash -n install.sh` and the three wrapper
scripts still pass.

## Key decisions

- **`taiga-docker`'s real `.env` key names, verified against the live
  `stable` branch** (fetched directly, not assumed from the spec): the
  ones actually present are `TAIGA_SCHEME`, `TAIGA_DOMAIN`, `SUBPATH`,
  `WEBSOCKETS_SCHEME`, `SECRET_KEY`, `POSTGRES_USER`/`POSTGRES_PASSWORD`,
  `RABBITMQ_USER`/`RABBITMQ_PASS`/`RABBITMQ_VHOST`/`RABBITMQ_ERLANG_COOKIE`,
  plus SMTP/telemetry/attachment settings left untouched. There is no
  `TAIGA_PORT` key upstream — `taiga-gateway`'s port is hardcoded
  `9000:80` in `docker-compose.yml`, so the port is only ever changed via
  `docker-compose.override.yml`; I added `TAIGA_PORT` to `taiga-docker`'s
  own `.env` too (beyond what the spec listed) specifically so the override
  file's `${TAIGA_PORT}` reference resolves via Compose's own `.env`
  auto-load, without the wrapper scripts needing to export anything.
- **`.env` idempotency needed a different mechanism than `TOTP_SECRET`'s**:
  `taiga-docker` ships a real, already-populated `.env` (not a `.env.example`
  with blank values), so there's no "value is empty → generate one" signal
  to key off. Used a `TAIGA_FRESH_CLONE` flag instead — secrets are only
  randomized in the same run that performed the `git clone`, never on a
  later re-run — verified in isolation (see "Verification performed") with
  a fake `.env` shaped exactly like the real upstream defaults: a second
  run left every secret byte-for-byte identical.
- **Block placement moved from "right after the code-server block" to
  "right after `-- Publishing --`"** — see "Deviations" for the reasoning
  (the spec's own step 3 needs `PUBLISH_MODE`/`BASE_URL`, which aren't
  resolved until later in the script).
- **`TAIGA_DOMAIN` in loopback mode uses `localhost:$TAIGA_PORT`, not bare
  `localhost`** — the spec's prose said "else localhost" casually; upstream's
  own shipped default is `TAIGA_DOMAIN=localhost:9000` (confirmed from the
  live `.env`), and Taiga's frontend/backend use this value to build
  absolute URLs, so keeping the port avoids generating links that silently
  assume port 80. Automatic, no prompt, still derived purely from
  already-known values (`TAIGA_PORT`) — consistent with the spec's actual
  intent ("no separate interactive prompt"), just a more literal reading of
  what makes the resulting Taiga installation actually work.
- **Frontend edge cases design.md's pseudocode left abstract** — I extended
  `handleActionResult()`'s 401 branch and `cancelActionCode()` to also
  clear `taigaPending`/`taigaWasRunning` when a taiga toggle-on is reverted
  (session expired, or the user cancels the TOTP prompt) — otherwise the
  row would show "starting…" for up to 90 seconds after an action that
  never actually reached the server. `docs/design.md`'s own pseudocode
  (`pendingToggleStates`, `hasTaigaError`) was explicitly abstract on this
  point, not a literal spec to follow byte-for-byte, so this is a judgment
  call filling in the gap, not a deviation from an explicit instruction.
- **Resource-cost badge contrast** (see "Deviations" below) — recomputed
  the actual WCAG relative-luminance contrast ratio (the standard formula,
  gamma-correcting each channel) rather than trusting `docs/design.md`'s
  own arithmetic, which used a non-standard formula and got the wrong
  numbers.

## Deviations from spec / design

1. **Taiga install block placement** (`docs/spec.md` "Proposed approach"
   said "placed after the existing code-server block (~line 166)"): landed
   after the `-- Publishing --` section instead (current line 235), because
   the very same spec section's step 3 requires `TAIGA_DOMAIN` to be
   "derived automatically from whatever `PUBLISH_MODE`/`BASE_URL` already
   resolved to earlier in this same install run" — and those two values
   aren't actually prompted for until the `-- Publishing --` section, well
   after where the spec's line reference points. Placing the whole block
   there instead (rather than splitting Docker-install from
   secrets-and-domain-config into two separate locations) keeps it a single
   readable unit and still satisfies every other placement constraint the
   spec lists (after `RUN_USER`/`set_env`/`get_env`/`random_token` are
   defined; sudoers additions still live in the pre-existing sudoers
   section per the spec's own explicit carve-out for that piece; the final
   summary note still lives in the pre-existing `== Done ==` section).
2. **Resource-cost badge contrast fix, diverging from `docs/design.md`'s
   own conclusion.** `docs/design.md` computed the existing `.badge` pairing
   (`#4da6ff` on `#16324a`) at "~1.78:1" using a non-standard, non-gamma-
   corrected formula, correctly flagged that as failing both the 4.5:1 (AA
   text) and 3:1 (graphical) thresholds, floated `#66d9ff` as a fix — then
   explicitly decided *not* to apply it ("Decision: Use the existing
   `.badge` style as-is... treat the resource warning as supplementary").
   Per this task's explicit instruction, I did not carry that
   inconsistency forward. Recomputing with the actual WCAG relative-
   luminance formula (linearizing each sRGB channel: `c ≤ 0.03928 → c/12.92`,
   else `((c+0.055)/1.055)^2.4`, then `L = 0.2126·R + 0.7152·G + 0.0722·B`,
   contrast `= (L_light + 0.05) / (L_dark + 0.05)`):
   - Background `#16324a`: `L ≈ 0.02945`.
   - Existing `.badge` text `#4da6ff`: `L ≈ 0.36058` → contrast **≈ 5.17:1**
     (already passes AA 4.5:1, contrary to design.md's own math — its
     formula wasn't the real WCAG one).
   - `#66d9ff` (design.md's own suggested fix, applied here as a
     `.badge.taiga-ram` modifier, scoped to the Taiga badge only —
     shipped as `color: #66d9ff` in `app/app.py`'s `<style>` block):
     `L ≈ 0.59668` → contrast **≈ 8.14:1**, comfortably clearing both the
     4.5:1 text threshold and the 3:1 graphical-element threshold with real
     headroom, not just barely.
   Shipped `#66d9ff` via a new `.badge.taiga-ram` class (not a change to the
   base `.badge` class, which the engine-name badges elsewhere still use) —
   scoped to exactly the element this task called out.

## Known limitations

- **Docker itself, the `taiga-docker` clone/config/pre-pull steps, the real
  wrapper scripts' `docker compose` invocations, and a real
  toggle-through-startup-to-running cycle were not run end-to-end.** This
  sandbox has a Docker *daemon* (26.1.5, already running unrelated
  containers for another project) but **no Compose plugin** (`docker
  compose` fails with "'compose' is not a docker command"; `docker-compose`
  v1 also isn't installed) and very little headroom to spare (`df -h /` →
  1.4G free of 16G; `free -h` → 2.0Gi total RAM, ~26Mi free at the time of
  writing) — nowhere near enough for a real 9-container, several-GB Taiga
  stack, and this is a shared box already running other people's
  containers, so I did not attempt to install the Compose plugin or run
  Taiga for real here. What *was* verified for real instead (see
  "Verification performed"): every piece of logic that doesn't require
  Docker to actually be functional — script syntax, the exact `.env`/
  `docker-compose.override.yml` content produced by the install-block logic
  (extracted into isolated, side-effect-free snippets and actually run),
  the fresh-clone-vs-re-run idempotency of secret generation (against a
  faked `.env` shaped exactly like the real upstream defaults, fetched
  live), the real (unmodified) `taiga-{up,down,status}.sh` scripts' graceful
  degradation when `$TAIGA_DIR` doesn't exist, and all of `app.py`'s
  Taiga logic with `taiga_run()`/`subprocess.run` monkeypatched.
- **`visudo` is not installed in this sandbox**, so the generated sudoers
  lines' `visudo -cf` validation (which `install.sh` itself runs) could not
  be independently re-verified here. The three added lines are syntactically
  identical in shape to the pre-existing `new-project.sh`/
  `new-project-from-upload.sh` sudoers lines already in this file (just
  without the trailing `*`, since these scripts take zero arguments), which
  already pass `visudo -cf` in this codebase.
- **The frontend JS state machine is now covered by a committed regression
  test, `tests/test_taiga_frontend.js`** (added in the Defect 1 fix — see
  "Fix: Defect 1" above). Originally (first review pass) this was verified
  ad hoc and not committed, since this project's test suite is otherwise
  Python-stdlib-`unittest`-only by explicit convention
  (`tests/test_upload.py`'s own docstring: "no pytest, no third-party test
  runner") with no prior JS test tooling in the repo. The reviewer's own
  testing pass used the same ad hoc Node `vm` technique to find Defect 1, so
  it's now committed as a plain, dependency-free Node script (matching the
  reviewer's approach rather than introducing a third technique) rather than
  staying ad hoc — the same technique having now caught a real bug is
  reason enough to keep it as a permanent regression test.
- **Taiga's own first-admin-account creation** is deliberately not
  automated (matches spec's non-goals) — install.sh only prints the
  pointer command.

## Verification performed

1. **Environment check, first**: confirmed Docker daemon present but no
   Compose plugin (`docker compose version` → "'compose' is not a docker
   command"), confirmed live network access (`curl -sI https://github.com`
   → 200), confirmed disk/RAM headroom too tight for a real Taiga stack
   (see "Known limitations"). This is why the Docker-dependent paths below
   were verified via isolated logic extraction rather than a real install.
2. **`taiga-docker`'s actual `.env`/`docker-compose.yml` fetched live**
   from `github.com/taigaio/taiga-docker` (`stable` branch) and read in
   full — this is what the exact key names in "Key decisions" above are
   based on, not assumed from the spec.
3. **`install.sh` syntax**: `bash -n install.sh` — passes.
4. **The three new scripts' syntax**: `bash -n scripts/taiga-{up,down,status}.sh`
   — all pass.
5. **The real (unmodified) wrapper scripts, run directly** in this sandbox
   (no `$TAIGA_DIR`, no config file present — the real fallback path a
   fresh box without a Taiga install would hit): `taiga-status.sh` prints
   `off`, exit 0; `taiga-up.sh`/`taiga-down.sh` fail cleanly (`cd` error,
   non-zero exit, no traceback) — matches `taiga_run()`'s existing
   "returncode never checked" precedent, same as `host_run()`.
6. **TAIGA_DOMAIN derivation logic**, extracted verbatim into an isolated
   script and run for all 4 branches (`none` mode; `tailscale` + https
   `BASE_URL`; `tailscale` + http `BASE_URL`; `tailscale` mode with
   `BASE_URL` left blank, the installer's own documented "fill in later"
   case): all 4 produced the expected value, including the
   `set -u`-safety of the `[ "$PUBLISH_MODE" = "tailscale" ] && [ -n
   "$BASE_URL" ]` short-circuit when `BASE_URL` was never assigned at all
   in that run.
7. **`docker-compose.override.yml` content**: generated via the exact
   heredoc used in `install.sh`, confirmed `${TAIGA_PORT}` stays literal
   (not shell-expanded) — left for Compose's own `.env`-driven substitution.
8. **`.env` secret-generation idempotency**: extracted the exact
   `TAIGA_FRESH_CLONE`-gated logic into an isolated script, run twice
   against a faked `.env` shaped exactly like the real upstream defaults
   (fetched in step 2) — first run (fresh clone) randomizes all 4 secrets;
   second run (re-run, not a fresh clone) leaves the file **byte-for-byte
   identical** (`diff` confirmed).
9. **`app/app.py` compiles**: `python3 -m py_compile` — passes.
10. **New Python test suite**: `python3 tests/test_taiga.py -v` → 13/13
    pass. Full suite: `python3 -m unittest discover -s tests -v` → **88/88
    pass** (75 pre-existing + 13 new), confirming no regressions.
11. **Rendered `PAGE_TEMPLATE` JS syntax**: extracted the real,
    runtime-rendered `<script>` contents from `render_page()` (not the raw
    Python source, which would trip on Python's own string-escaping) —
    `node --check` passes.
12. **Frontend state machine, Node `vm` harness** (first review pass, ad
    hoc, not committed at the time): 23/23 assertions pass, covering
    `actionPath()`, `row()`'s new `subOverride`/`showTaigaBadge` params
    (with every existing non-taiga call site's default behavior unchanged),
    and `refresh()`'s full 4-state machine end-to-end against a stubbed
    `fetch('/status')` — stopped, starting (+ spinner), running (+ open
    link), the transient running→off "re-arm starting, don't flash error"
    behavior, and the 90-second timeout → error transition (badge correctly
    hidden). This pass's own scenarios did **not** include a concurrent
    poll landing while a toggle-off's own POST was still unresolved — that
    gap is exactly what the reviewer's own testing pass (Defect 1) caught,
    and what item 14 below now covers with a committed test.
13. **Resource-cost badge contrast**: recomputed by hand using the actual
    WCAG relative-luminance formula (see "Deviations" for the full
    numbers) — confirmed `#66d9ff` on `#16324a` clears both the 4.5:1 and
    3:1 thresholds with real headroom (≈8.14:1).
14. **Defect 1 fix verification** (post-review): `node
    tests/test_taiga_frontend.js` → 4/4 pass against the fixed code,
    including the reviewer's exact race (both directly via `toggle()` and
    via the 428/TOTP-code retry path) and the still-unaffected
    "unexpected stop while running" hiccup/recovery/eventual-error cases.
    Confirmed the two race tests actually catch the bug (not just pass
    vacuously) by temporarily reverting the four `taigaOffInFlight`-related
    edits back to the pre-fix code and re-running — both race tests failed
    exactly as expected (final state stuck on `starting…`), the two
    unrelated hiccup-case tests still passed. Restored the fix; re-ran the
    full existing regression set (`python3 -m unittest discover -s tests`
    → 88/88, `python3 -m py_compile app/app.py`, `bash -n install.sh`,
    `bash -n scripts/taiga-{up,down,status}.sh`, `node --check` on the
    freshly re-extracted rendered `<script>`) — all still pass, no
    regressions from the fix.
15. **Defect 2 fix verification** (second post-review round): wrote the new
    5th test first, confirmed it failed against the Defect-1-fix-only code
    (reproducing Defect 2's exact symptom), implemented the
    `taigaOffPendingCount` reference-count fix, re-ran — `node
    tests/test_taiga_frontend.js` → **5/5 pass**. Re-confirmed load-bearing
    by mechanically reverting only this fix's edits back to a temp copy and
    re-running (test 5 failed again, tests 1-4 unaffected), then restored
    and diffed byte-for-byte against the intended fixed state. Re-ran the
    full existing regression set (`python3 -m unittest discover -s tests`
    → 88/88, `python3 -m py_compile app/app.py`, `bash -n install.sh`,
    `bash -n scripts/taiga-{up,down,status}.sh`, `node --check` on the
    freshly re-extracted rendered `<script>`) — all still pass, no
    regressions from either fix.

## How to verify locally

```bash
cd /home/dev/projects/ai-dev-switchboard
bash -n install.sh
for f in scripts/taiga-up.sh scripts/taiga-down.sh scripts/taiga-status.sh; do bash -n "$f"; done
python3 -m unittest discover -s tests -v   # expect 88/88 pass
node tests/test_taiga_frontend.js          # expect 5/5 pass — covers Defect 1's race + Defect 2's overlapping-off race
```

Real end-to-end smoke test (needs a box with real spare RAM/disk and either
an existing Docker+Compose install or network access for
`get.docker.com`/the `taiga-docker` clone/image pulls — not run in this
sandbox, see "Known limitations"):

```bash
sudo ./install.sh --with-taiga
grep TAIGA_ /etc/ai-dev-switchboard/switchboard.env
sudo cat /etc/sudoers.d/ai-dev-switchboard | grep taiga
docker compose -f /opt/ai-dev-switchboard-taiga/docker-compose.yml \
  -f /opt/ai-dev-switchboard-taiga/docker-compose.override.yml ps
# → nothing running yet (install leaves it stopped)

# flip the toggle from the web UI (or curl directly, once logged in + TOTP-cleared):
curl -s -b <cookie> -X POST http://127.0.0.1:8333/taiga/on
sleep 30
curl -s -b <cookie> http://127.0.0.1:8333/status | python3 -m json.tool
# → "taiga": true, "taiga_url": "http://127.0.0.1:9000" (loopback mode)

curl -s -b <cookie> -X POST http://127.0.0.1:8333/taiga/off
docker compose -f /opt/ai-dev-switchboard-taiga/docker-compose.yml \
  -f /opt/ai-dev-switchboard-taiga/docker-compose.override.yml ps
# → all stopped again

# one-time admin creation, after a toggle-on:
cd /opt/ai-dev-switchboard-taiga && ./taiga-manage.sh createsuperuser
```

Service-restart-survives-state check (AC7):

```bash
# with Taiga toggled on and running:
sudo systemctl restart ai-dev-switchboard
curl -s -b <cookie> http://127.0.0.1:8333/status | python3 -m json.tool
# → "taiga": true still, no re-toggle needed (queried fresh from dockerd,
#   never trusted from app.py's own in-memory state)
```
