# Spec: Per-project HTTP-level smoke check ("Smoke check" button)

## Summary
Add a manual, one-click "Smoke check" button per project row that makes a
single in-process HTTP GET against that project's own already-captured
hosted dev-server URL and reports status code, response time, and an
optional response-body substring check — an honest, dependency-free
HTTP-level health check, explicitly **not** real browser QA/testing
automation (no JS execution, no rendering, no DOM interaction).

## Background / current state

This spec is the outcome of re-scoping BACKLOG item 18 (`docs/BACKLOG.md`),
which investigated `garrytan/gstack` and came back blocked pending four
user questions. The user has now answered:
1. All three named capabilities are wanted: browser QA/testing automation,
   cross-model code review, and a security audit skill.
2. gstack's own new runtime dependencies (Bun, Chromium, optionally
   ngrok/Supabase) are **not** acceptable to add to the LXC.

Reconciling those two answers (full writeup in `docs/BACKLOG.md` item 18):
- **Cross-model code review is already shipped** — backlog item 8's
  `app/teams.py:review_pr_diff()` already runs any roster model/engine
  (Ollama or any `engines.d/*.engine` entry — genuinely cross-model,
  cross-engine) against a PR diff, grounded in the project's own
  documented conventions, and posts the result as a PR comment. No new
  code needed for this capability; the user's ask likely predates knowing
  item 8 (built earlier this same session) already covers it.
- **A security audit skill already exists at a different layer** — the
  `claude-security` plugin (`claude-plugins-official` marketplace,
  `skills/claude-security/SKILL.md`, jobs `scan-codebase.md`/
  `scan-changes.md`) is installed in the Claude Code plugin registry and
  directly invocable against this (or any) repo without any code change
  inside `ai-dev-switchboard` itself — the same "route to the matching
  skill directly, don't build a parallel in-repo mechanism" pattern this
  project's own CLAUDE.md already calls for. No new code needed.
- **Real browser QA (JS rendering, DOM interaction, screenshots) is
  blocked** — it fundamentally needs a headless browser engine
  (Chromium or equivalent), which the user just declined. Recorded as
  blocked in `docs/BACKLOG.md`, not built as a hollow version of "browser
  QA" that doesn't actually render anything.
- **What IS genuinely buildable without new dependencies**: an HTTP-level
  smoke check — status code, response timing, basic content assertion —
  against a project's own running dev server. `curl` and `python3` are
  already installed by `install.sh`'s baseline `apt-get install` line
  (`install.sh:214`), and `urllib.request` (Python stdlib) is already this
  codebase's established in-process HTTP client convention (`_gitea_api()`,
  `_github_api()`, the login/description-LLM calls — all `urllib.request`,
  never `curl` subprocess or a third-party HTTP library). This spec covers
  that increment only.

Today, `app/app.py`'s `/status` polling loop already captures each running
project's hosted dev-server URL via `engine.url_regex` watching the tmux
pane (`_session_urls`, `app/app.py:2221` onward), and surfaces it as
`inst["url"]` in the `/status` JSON (`app/app.py:5214-5217`). This is
already the switchboard's own trusted, server-derived URL for that
project — the natural, safe target for a smoke check (no user-supplied
arbitrary URL, so no SSRF-style "check any URL the client asks for"
concern).

The manual-trigger, synchronous, request-thread dispatch pattern this spec
follows is `deploy_run()` (`app/app.py:1600`) — a per-project
`threading.Lock` (mirroring `_deploy_lock_for()`, `app/app.py:1591`)
serializes concurrent clicks, and the button only renders when its target
is present, mirroring `deployRow()`'s "rendered only when a deploy-map
entry exists" pattern (`app/app.py:2958-2963`).

## Goals
- A "Smoke check" button per project row, visible only when that project
  currently has a captured hosted URL (`inst.url` non-null).
- One GET request against that URL, reporting: final HTTP status code,
  elapsed time in milliseconds, and (only if the operator typed an
  optional substring into an accompanying text field) whether the
  response body contains that substring.
- Bounded, safe-by-construction: request timeout, capped response-body
  read size, no new runtime dependency, no new persisted state file.
- Honest naming and scope throughout the UI/docs: "Smoke check" or
  "HTTP smoke check" — never "browser QA" or "browser test" anywhere in
  UI copy, code comments, or docs, since this does not execute JS or
  render anything.

## Non-goals
- **Not real browser QA/testing automation.** No headless browser, no JS
  execution, no DOM interaction, no screenshots, no click/type simulation.
  That capability remains blocked pending the Chromium/Bun dependency
  question and is recorded separately in `docs/BACKLOG.md` item 18 — this
  spec does not attempt a partial or disguised version of it.
- No scheduling/cron/automatic periodic checks — manual button click only,
  consistent with this project's existing "deploy is manual-click only"
  precedent (backlog item 2c part 2).
- No persisted history of past smoke-check results. The result renders
  into an ephemeral per-project message slot (mirrors `.deploy-msg`) that
  is gone on the next page refresh — no new state file, no database row.
- No configurable HTTP method, headers, auth, or request body — GET only,
  unauthenticated, since the target is always the switchboard's own
  LAN-local dev server captured by `_session_urls`, never an arbitrary
  external or user-typed URL.
- No support for checking a project that has no currently-captured URL —
  the button simply does not render for that row; there is no manual
  "enter a URL to check" fallback in this pass.
- No new roster/engine/model concept — this has nothing to do with the
  engine roster (items 6/8); it is a plain HTTP request the switchboard
  process itself makes.

## Proposed approach

**Backend (`app/app.py`):**
- New pure-ish dispatch function `smoke_check_run(name: str, expect_contains: str) -> dict`,
  placed near `deploy_run()` and following its docstring style. Contract:
  - Look up `_session_urls.get(name)`. If absent, return
    `{"ok": False, "error": "no captured URL for this project"}` immediately
    (defensive — the button shouldn't be clickable in this state, but the
    route must not trust the client).
  - Acquire a per-project lock (new `_smoke_check_locks`/`_smoke_check_lock_for()`,
    identical shape to `_deploy_locks`/`_deploy_lock_for()`). Non-blocking
    acquire; if already held, the route returns 409 (mirrors `deploy_run()`'s
    409 contract) without running a second check.
  - `urllib.request.urlopen(url, timeout=SMOKE_CHECK_TIMEOUT_SECONDS)`,
    timed with `time.monotonic()` around the call. Read at most
    `SMOKE_CHECK_MAX_BODY_BYTES` (default 65536) of the response body via
    `resp.read(SMOKE_CHECK_MAX_BODY_BYTES)` — same bounded-read discipline
    `AI_REVIEWER_MAX_DIFF_BYTES` already established for a different diff
    read.
  - On success: `{"ok": True, "status_code": resp.status, "elapsed_ms": int,
    "content_ok": None if not expect_contains else (expect_contains in
    body_text)}`, where `body_text` is decoded `errors="ignore"` (same
    pattern `AI_REVIEWER`'s diff-decode already uses).
  - On `urllib.error.HTTPError`: still a completed request — capture
    `e.code` as the status code (a 404/500 from the target is a smoke-check
    **result**, not a mechanism failure) and continue exactly as the
    success path above (read `e.read()` for the body).
  - On `urllib.error.URLError`/`socket.timeout`/`TimeoutError`/`ConnectionRefusedError`:
    `{"ok": False, "status_code": None, "elapsed_ms": int, "error": <short
    message, e.g. "timed out after 10s" / "connection refused">}`.
  - `finally`: release the lock.
- New route `POST /projects/<name>/smoke-check` (follows the
  `/projects/<name>/team/...` sub-resource convention, not the older
  `/instance/<name>/deploy` one, since this is new work). Body:
  `{"expect_contains": "<string, may be empty>"}`. Validates `name` is a
  real instance (404 if not, same as other per-project routes); calls
  `smoke_check_run()`; returns its dict as JSON with HTTP 200 for any
  *completed* check (success or target-side failure alike — see "On
  `URLError`" above), 404 for unknown project, 409 for lock contention.
- Two new env-configurable constants, following the existing
  `UPLOAD_MAX_BYTES`/`AI_REVIEWER_MAX_DIFF_BYTES` naming and
  `int(os.environ.get(...))` pattern:
  - `SMOKE_CHECK_TIMEOUT_SECONDS` (default `10`)
  - `SMOKE_CHECK_MAX_BODY_BYTES` (default `65536`)
  Add both, with a one-line comment, to `config/switchboard.env.example`
  next to the other numeric tunables.

**Frontend (`app/app.py`'s inline JS/CSS):**
- `smokeCheckRow(name, url)` — mirrors `deployRow(name, deploy)`'s "return
  '' if not present" shape: renders nothing when `inst.url` is falsy.
  When present, renders a `.smoke-check-row` containing:
  - A small text `<input>` for the optional `expect_contains` substring
    (placeholder text making clear it's optional, e.g. "optional: text
    that should appear in the response"), persisted across `refresh()`
    re-renders per-project the same way `teamTaskText`/`engineChoice`
    already survive the 4-second poll (a new `smokeCheckExpect` map keyed
    by project name).
  - A button, class `.smoke-btn` (own class, not reusing `.deploy-btn`/
    `.team-btn`, since backlog item 20's still-open WCAG contrast issue on
    that shared green should not be inherited sight-unseen by a new
    control — pick an explicitly-checked, passing color pair for
    `.smoke-btn` from the start; do not reuse `#34c759`/white without
    verifying contrast).
  - A `.smoke-check-msg` slot (mirrors `.deploy-msg`), filled in by
    `doSmokeCheck()` after the POST resolves: on success, something like
    `"200 OK · 84ms"` plus, only when `expect_contains` was non-empty,
    `" · content: found"`/`" · content: NOT found"`; on failure, the
    `error` string plainly.
- `doSmokeCheck(name)` — mirrors `doDeploy(name)`'s shape (fetch, await,
  render into the msg slot) but with **no `confirm()` dialog** — unlike
  deploy (which mutates a remote target) or team-start (which spawns
  processes), a GET request has no side effect worth a confirmation
  interruption.

## Affected areas
- `app/app.py`: `smoke_check_run()`, `_smoke_check_lock_for()`, new route
  `POST /projects/<name>/smoke-check`, `smokeCheckRow()`/`doSmokeCheck()`
  JS, `.smoke-check-row`/`.smoke-btn`/`.smoke-check-msg` CSS, two new env
  constants near the existing `AI_REVIEWER_*`/`UPLOAD_*` block.
- `config/switchboard.env.example`: two new documented lines
  (`SMOKE_CHECK_TIMEOUT_SECONDS`, `SMOKE_CHECK_MAX_BODY_BYTES`).
- No changes to `install.sh` (no new package, no new `--with-*` flag — this
  ships enabled-by-default, matching how the existing `url` capture itself
  has no on/off toggle).
- No changes to any privileged/`RUN_USER`-hand-off script — this is a
  plain in-process `urlopen()` call, not a subprocess or privilege
  boundary crossing.
- New tests: `tests/test_smoke_check.py` (or alongside `test_deploy_dispatch.py`'s
  existing pattern), covering `smoke_check_run()` directly (mock
  `urlopen`/inject a local test HTTP server) plus the route's 404/409
  contract — following this project's existing precedent of testing
  dispatch functions directly rather than only through the Flask test
  client (see `tests/test_deploy_dispatch.py`).

## Edge cases
- Project has no captured `url` (engine off, or engine has no
  `url_regex`, or hasn't printed a matching URL yet) → button doesn't
  render; a direct POST to the route still returns a clean
  `{"ok": false, "error": "no captured URL for this project"}` rather than
  a 500, since `_session_urls` can change between page load and click.
- Concurrent clicks for the same project → second request gets 409
  immediately, no queuing, no duplicate in-flight request (mirrors
  `deploy_run()`'s existing 409 contract).
- Concurrent clicks for *different* projects → fully independent, no
  shared lock (mirrors `_deploy_locks` being a dict keyed by name).
- Target times out → reported as a normal (non-500) failure result with
  an elapsed time close to the configured timeout, not a hung request
  thread.
- Target refuses the connection (dev server crashed after its URL was
  captured but before the click) → same as timeout: a clean `ok: false`
  result, not an unhandled exception.
- Target redirects → `urllib.request.urlopen()`'s default redirect
  handling follows the chain; the **final** response's status code and
  body are what's reported (no separate "N redirects followed" detail in
  this pass — noted as a possible future refinement, not required now).
- Target returns a very large body (e.g. an accidentally-proxied large
  asset) → capped at `SMOKE_CHECK_MAX_BODY_BYTES`, so neither the check
  nor the substring assertion reads unbounded data; the substring check
  runs only against the truncated prefix, which is an accepted, documented
  limitation (a match past the cap won't be found) rather than a silent
  correctness bug.
- Response body isn't valid UTF-8 → decode with `errors="ignore"` before
  the substring check, matching this codebase's existing diff-decode
  precedent — never raise on decode.
- `expect_contains` is empty/omitted → `content_ok` is `null`/`None`, never
  `false` — "not checked" must be visibly distinct from "checked and
  failed" in both the JSON contract and the rendered message.
- Unknown/nonexistent project name in the URL path → 404, same as other
  per-project routes' existing validation.
- A smoke check clicked immediately after the project's engine is
  stopped mid-request (URL was valid at request-build time, engine dies
  during the request) → falls into the "connection refused"/timeout
  failure path above; no special-casing needed.

## Acceptance criteria
- [ ] Given a project whose `/status` response has a non-null `url`, when
      the page renders that project's row, then a "Smoke check" button is
      present in that row.
- [ ] Given a project whose `/status` response has `url: null`, when the
      page renders that row, then no "Smoke check" button is present.
- [ ] Given a click on "Smoke check" with the optional text field left
      empty, when the target responds `200` within the timeout, then the
      UI displays the status code and an elapsed time in milliseconds, and
      does not display any content-assertion verdict.
- [ ] Given the optional text field contains a substring that IS present
      in the target's response body, when the check completes, then the
      UI shows a positive content-match indication alongside status/timing.
- [ ] Given the optional text field contains a substring that is NOT
      present in the response body, when the check completes, then the UI
      shows a negative content-match indication, **and** still separately
      shows the real status code/timing (a 200 with a failed content match
      is not collapsed into a single pass/fail flag).
- [ ] Given the target is unreachable (connection refused), when the check
      runs, then the switchboard's own route responds `200` with a JSON
      body reporting `ok: false` and a human-readable error — never an
      unhandled 500.
- [ ] Given the target does not respond within `SMOKE_CHECK_TIMEOUT_SECONDS`,
      when the check runs, then the request thread returns within
      approximately that bound (not hung indefinitely) with a timeout
      error reported.
- [ ] Given a smoke check is already in-flight for project X, when a second
      request for project X arrives before the first completes, then the
      second request receives `409` immediately.
- [ ] Given two different projects, when smoke checks are triggered for
      both concurrently, then neither blocks on the other.
- [ ] Given a completed smoke check result is shown in the UI, when the
      page is refreshed (or the next 4-second poll re-renders the row),
      then the result is gone — nothing is persisted server-side.
- [ ] `install.sh` requires no new `apt-get`/Docker/binary-download step
      for this feature to work on a fresh install.
- [ ] The chosen `.smoke-btn` color pairing passes a real WCAG AA contrast
      check (≥4.5:1 for normal text), verified by computed contrast ratio
      at implementation time — not reused from `.deploy-btn`/`.team-btn`
      without checking, given backlog item 20's still-open finding on that
      exact shared class.

## Open questions
- **Naming of the button/feature in user-facing copy** — this spec uses
  "Smoke check." If the user would prefer different wording (e.g. "Health
  check," "Ping check"), that's a cheap rename with no design impact;
  proceeding under "Smoke check" as the working name since it most
  precisely describes "cheap, shallow, catches-the-obvious-breakage"
  without implying either deep testing or browser rendering.
- **Whether `expect_contains` should be per-project-remembered** (e.g.
  saved to a config file so it doesn't need retyping every session) is
  left as ephemeral client-side-only state (`smokeCheckExpect`, cleared on
  page reload) for this pass, consistent with the "no persisted history"
  non-goal. If this turns out to be a real recurring annoyance, a small
  follow-up could persist it the same way `deploy-map.json` persists
  deploy targets — not done here to avoid adding a new config file for a
  single text field on a first pass.
- **Real browser QA remains explicitly blocked**, not attempted in any
  partial form here — see `docs/BACKLOG.md` item 18's reconciliation notes
  for what would need to change (Chromium/Bun or an equivalent headless
  engine) for that capability to become buildable.

## Risk / rollback notes
- Purely additive: a new route, a new button that only renders under a
  specific existing condition (`inst.url` present), and two new env
  constants with safe defaults. No existing route, data model, or
  behavior is modified.
- Worst-case failure mode is a hung request thread if `urlopen`'s
  `timeout=` were somehow not honored (e.g. a bug in exception handling
  swallowing the timeout) — mitigate by testing the timeout path
  explicitly (mocking a slow/never-responding server) rather than assuming
  the stdlib timeout parameter alone is sufficient proof.
- Rollback is trivial: remove the route and the button; no migration, no
  persisted state to clean up, since this feature deliberately persists
  nothing.
