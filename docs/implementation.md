# Implementation: Backlog item 7 part 2 -- web UI for approving/rejecting board_write proposals

## Summary
Extended the Teams page's existing `blocked_ask_user` web UI to also handle
`blocked_board_write` runs, reusing part 1's already-shipped
`resolve_board_write()` and the `inbox.json` `kind` discriminator exactly
where their shapes already match `ask_user`'s, and adding new
verb-specific (`set_status`/`amend_description`/`append_comment`)
presentation only where a board-write proposal's own shape needs it. Backend:
a new `escalation_kind` field on `/status`, a new `blocked_board_write`
branch on `GET .../team/inbox` (with a best-effort Taiga subject-enrichment
read), a new `POST /projects/<name>/team/board-resolve` route, and a
one-tuple fix closing `POST .../team/stop`'s disclosed no-op gap. Frontend:
status-strip copy distinction, a verb-specific proposal panel (Approve/
Reject, no free-text field), `doTeamBoardResolve()` wired through the
existing TOTP-retry machinery, and two new merged-event-feed classifiers
(`board-write-proposal`/`board-write-resolved`) checked ahead of the
existing generic `tool_use`/`meta.resolved` branches per the spec's called-
out ordering requirement.

## Root cause
Not applicable (new feature, not a bugfix).

## Changes by file
- `app/app.py`:
  - `/status` handler -- `team_status` map gains `"blocked_board_write":
    "blocked"`; `waiting_on_you` now also true for `"blocked_board_write"`;
    new `escalation_kind` field (`"ask_user"`/`"board_write"`/`None`), a
    direct string comparison against the already-loaded `run["status"]`,
    no extra read.
  - `_handle_team_inbox()` -- new `blocked_board_write` branch, factored
    into a sibling `_handle_team_inbox_board_write(state)` (mirrors the
    ask_user branch's own missing/malformed-inbox fallback discipline
    exactly), plus a best-effort `teams.taiga_board.resolve_session()` +
    `get_userstory()` subject-enrichment read (catches
    `teams.taiga_board.TaigaPushError`, degrades to omitting `"subject"`
    -- never a 500, never blocks Approve/Reject). Calls through
    `teams.taiga_board`, not a separately-imported module, matching
    `tests/test_teams_board.py`'s own established "monkeypatch the module
    the caller imports it through" convention (`app/teams.py` already
    does `import taiga_board` at module level; `app/app.py` already does
    `import teams`, so `teams.taiga_board` is the one live reference).
  - New `POST /projects/<name>/team/board-resolve` route in `do_POST()`
    -- mirrors `/team/resolve`'s validation shape exactly (unknown
    project 404; empty `run_id` falls back to
    `teams.latest_run_for_project()`; non-empty `run_id` validated via
    `teams._RUN_ID_RE` then ownership-checked; `status !=
    "blocked_board_write"` -> 400; `action not in ("approve", "reject")`
    -> 400 before calling `resolve_board_write()`; the same defensive
    "a team thread is already running" 400 check; on success, starts
    `_run_team_in_background()` on a new thread exactly like
    `/team/resolve` does).
  - `POST .../team/stop` -- one-tuple fix: `("running", "blocked_ask_user")`
    -> `("running", "blocked_ask_user", "blocked_board_write")`, closing
    part 1's disclosed no-op gap.
  - Frontend JS (inline in the page template):
    - `renderTeamStatusStrip()` -- branches on `team.escalation_kind` when
      `blocked && waiting_on_you`: "⚠ Board write pending approval" for
      `board_write`, unchanged "⚠ Waiting on you" for `ask_user`.
    - `renderEscalationPanel()` -- shared fetch/cache/loading/fetch-failure
      preamble unchanged; the "already resolved" race message now branches
      on `escalation_kind` for distinct copy (`board_write`: "This
      proposal was already approved or rejected."; `ask_user`'s own text
      byte-for-byte unchanged); then branches to a new
      `renderBoardWriteEscalationPanel(name, cached)` for `board_write`,
      the existing ask_user form body otherwise unchanged.
    - New `renderBoardWriteEscalationPanel()` -- verb-specific summary line
      (`set_status`: "Move **subject** from **current** to **proposed**.";
      `amend_description`: "Replace **subject**'s description" + Current/
      Proposed `<textarea readonly>` comparison blocks; `append_comment`:
      "Add a comment to **subject**" + a single Comment-text block, no
      current-value comparison), lead's note (truncated to 200 chars,
      existing precedent) if non-null, and Approve/Reject buttons -- no
      free-text field. Subject falls back to `#ref` when the enrichment
      read failed.
    - New `truncateText(text, max)` helper, the 200-char-plus-ellipsis
      precedent extracted from `teamFeedEventBody()`'s own fact-check-match
      rendering (used for the panel's one-line summary/note; the longer
      description/comment comparison blocks instead rely on the scrollable
      `max-height: 200px` box itself per docs/design.md, not hard
      truncation).
    - New `doTeamBoardResolve(name, action)` -- clears the row's message
      slot, records `action` into a new client-side map
      `teamBoardResolveAction[name]` (same "small map keyed by name,
      survives a TOTP retry" pattern `teamEscalationOther` already
      establishes -- see "Key decisions" below for why this was chosen
      over `pendingToggle`), then `toggle('team-board-resolve', name,
      true, null)`.
    - `actionPath()`/`actionBody()`/`handleActionResult()` gain
      `'team-board-resolve'` cases: path
      `/projects/<name>/team/board-resolve`; body `{action:
      teamBoardResolveAction[name]}`; result handling mirrors
      `'team-resolve'`'s own inline-message-slot pattern exactly (success:
      "✓ Board write resolved", clears the inbox cache; failure: "✕ Error:
      ..."). The 428-overlay label switch gains `'Resolving board write: '
      + name`.
    - `teamFeedEventKindClass()`/`teamFeedEventBody()` gain two new checks,
      placed **before** the existing generic `tool_result`+`meta.resolved`
      -> `'resolved'` branch per the spec's called-out ordering
      requirement: `tool_use` + `meta.verb !== undefined` ->
      `'board-write-proposal'`; `tool_result` + `meta.approved !==
      undefined` -> `'board-write-resolved'` (parses
      `resolve_board_write()`'s own literal `outcome_summary`/
      `full_result_text` strings to distinguish "approved and applied" /
      "approved but Taiga rejected: ..." / "rejected by human").
    - New CSS: `.team-escalation-proposal`/`-summary`/`-label`/`-box`/
      `-note`, reusing every existing color/spacing token
      (`.team-escalation`'s own wrapper, `#0a0a0a`/`#333`/`#ccc` for the
      read-only comparison boxes per docs/design.md's own accessibility
      analysis) -- no new components, no new libraries.
- `tests/test_team_routes.py`:
  - `TeamStopEndpointTests`: `test_status_maps_every_run_status_to_the_
    coarse_label` extended with `"blocked_board_write": "blocked"`; new
    `test_stop_on_blocked_board_write_now_actually_stops`.
  - `test_status_idle_when_no_run_ever_started` updated for the new
    `escalation_kind: None` field (exact-dict-equality regression).
  - `StatusRosterAndCompositionTests`: `test_waiting_on_you_true_only_for_
    blocked_ask_user_never_for_escalated_max_rounds` extended with
    `"blocked_board_write": True`; new `test_escalation_kind_field`.
  - `TeamInboxEndpointTests`: new board_write-branch tests (exact persisted
    shape + subject enrichment; Taiga-unreachable graceful degradation;
    missing/malformed inbox.json fallback) plus a regression test pinning
    the ask_user branch's response shape byte-for-byte.
  - New `TeamBoardResolveEndpointTests` class, mirroring
    `TeamResolveEndpointTests`'s own structure/naming (unknown project;
    wrong status; invalid/missing action; cross-project run_id; path-
    traversal run_id; TOTP 428/403; approve/reject each resolving and
    starting the background thread; a genuine two-concurrent-approves race
    proving exactly one winner).
  - New module-level `_patch_taiga_board()` test helper, mirroring
    `tests/test_teams_board.py`'s own helper of the same name/shape exactly
    (monkeypatches `teamsmod.taiga_board`'s named attributes for one test,
    restored via `addCleanup`).
- `tests/test_team_frontend.js`: new tests for the board_write status-strip
  copy; `renderEscalationPanel()`'s three verb-specific layouts (including
  the `#ref` fallback and the distinct "already resolved" race copy);
  `doTeamBoardResolve()`'s approve/reject dispatch, success/error result
  handling, and 428-then-retry (asserting the retried request resends the
  *same* action the operator originally clicked); `teamFeedEventKindClass()`/
  `teamFeedEventBody()`'s two new classifiers for all three resolution
  outcomes, plus a regression test proving an ask_user-shaped
  `tool_result` (`meta.resolved` only, no `meta.approved`) still renders
  the unchanged generic `'resolved'`/`'Answer: ...'` output.

## Key decisions / tradeoffs
- **`doTeamBoardResolve()`'s TOTP-retry plumbing** (spec's own "Open
  questions", left as a developer judgment call): chose the "small
  client-side map keyed by name" pattern (`teamBoardResolveAction[name]`,
  set before `toggle()`'s first optimistic POST) over reading the global
  `pendingToggle` context inside `actionBody()`. Reason: `pendingToggle` is
  only populated once a 428 has actually been seen (`handleActionResult()`
  sets it inside the `r.status === 428` branch) -- `actionBody()` is also
  called on the very FIRST, optimistic (no-code) POST, before any 428 has
  happened, at which point `pendingToggle` is still `null`/stale from a
  previous action. A client-side map keyed by name, set synchronously by
  `doTeamBoardResolve()` before `toggle()` is ever called, is correct on
  both the first attempt and any retry -- exactly the same reasoning
  `team-start`'s own task/lead/members fields already apply (read from a
  live DOM element/client-side mirror, never from `pendingToggle`).
- **Truncation**: used the spec's own 200-char default (matching
  `teamFeedEventBody()`'s existing fact-check-match precedent) for the
  one-line proposal summary and the lead's note, but relied on the
  description/comment comparison blocks' own `max-height: 200px;
  overflow-y: auto` scrollable box (per docs/design.md's explicit
  recommendation) rather than a second, harder text truncation for those
  -- the box itself already bounds the panel's vertical footprint
  regardless of how long the underlying text is, so a second truncation
  layer would only lose information without solving a layout problem the
  scroll box doesn't already solve.
- **Subject enrichment read placement**: implemented as a private sibling
  method `_handle_team_inbox_board_write(state)` rather than inlining a
  second branch into `_handle_team_inbox()` itself -- keeps the existing
  ask_user branch's own code path completely untouched (byte-for-byte,
  confirmed by a dedicated regression test) and keeps the new branch's
  own missing/malformed-inbox fallback logic (which is structurally
  different from ask_user's -- different field names, a different
  fallback shape) legible on its own rather than interleaved with it.
- **Approve/Reject button styling**: reused the existing `.team-btn` class
  verbatim for both buttons (same class the ask_user panel's own "Submit
  answer" button, "Start team", "Stop team", and "Deploy" already use) --
  docs/design.md's own accessibility section speculates about a
  `#4da6ff`/blue button background, but no such button variant exists
  anywhere else on this page; `.team-btn`'s actual shipped
  green/`#34c759` background already satisfies "both primary-style, not
  Approve highlighted and Reject greyed" (both buttons render identically),
  so reusing it exactly (per this project's "match existing conventions,
  don't invent a new component" discipline) was chosen over introducing a
  new button variant color the rest of the page doesn't have.

## Deviations from spec
None substantive. Two of the spec's own explicitly-left-open points were
resolved during implementation, both recorded above under "Key decisions"
rather than left ambiguous:
- `doTeamBoardResolve()`'s exact TOTP-retry plumbing mechanism (spec's
  "Open questions" -- explicitly a developer's call).
- Exact truncation length/strategy for long `value`/`note`/
  `current_value.description` text (spec's "Open questions" -- explicitly
  "not a blocker either way").

One minor, disclosed wording deviation from docs/design.md's own ASCII
mockups: the board-write-proposal event-feed line (§6, `teamFeedEventBody()`)
literally reuses the transcript's own `args_summary` text as its trailing
segment (e.g. `board_write (set_status): ref #42 — board_write(set_status,
ref=42)`), per the spec's own explicit formula (`'board_write (' +
meta.verb + '): ref #' + meta.ref + ' — ' + esc(e.text)`, "reusing the
transcript's own args_summary text, already verb/ref-specific") -- not the
design doc's more polished illustrative example text ("— 'Move to In
progress per delegate'"), which does not correspond to any string the
backend actually persists in this transcript entry's `text` field
(`team_step()`'s own `board_write` branch sets it to exactly
`args_summary`, not the proposal's `note`). Implemented per the spec's
literal, backend-accurate formula rather than the design doc's
illustrative (but not backend-derived) copy.

## Known limitations
- Same live-Taiga-instance caveat part 1 already disclosed: no live Taiga
  server was reachable in this sandbox, so the subject-enrichment read's
  exact behavior against a real board (response shape, timing) is verified
  only via the same monkeypatched-`teams.taiga_board` seam part 1's own
  tests already established, not against a live instance.
- The board-write proposal panel's Approve/Reject buttons are visually
  identical (both `.team-btn`, both green) per docs/design.md's own "both
  primary-style" requirement -- an operator relies on the button *label*,
  not color, to distinguish them. This matches the existing page's own
  established pattern (Start/Stop/Deploy/Submit-answer all share this one
  button style) and was a deliberate "match existing conventions" choice,
  not an oversight, but is worth naming explicitly since docs/design.md's
  accessibility section briefly speculated about a different color.

## How to verify locally
```
# Full existing suite, including this cycle's new/extended tests, all green:
python3 -m unittest discover -s tests -v

# Just this cycle's new/extended backend route tests:
python3 -m unittest tests.test_team_routes -v

# Frontend tests (extracts the real, rendered <script> from
# app.render_page() via a Python subprocess, runs it in a Node vm sandbox
# with stub document/fetch/confirm -- no browser, no headless Chrome):
node tests/test_team_frontend.js

# Manual smoke test against a real run (requires
# ~/.config/ai-dev-switchboard/taiga-push.env for the subject-enrichment
# read to succeed -- omitting it still works, "subject" is simply absent):
#   1. Start the app.py server, log in, clear the TOTP gate once.
#   2. Start a team whose lead calls board_write (or drive one manually via
#      the CLI's team-start, then have the lead call board_write).
#   3. Refresh the Teams page -- status strip should read "⚠ Board write
#      pending approval", not the old "idle" fallback.
#   4. The escalation panel should show the verb-specific proposal
#      (subject from Taiga if reachable, else "#<ref>"), with Approve/
#      Reject buttons and no free-text field.
#   5. Click Approve or Reject -- confirm the TOTP overlay appears on the
#      first mutating action of the session, and that the row's message
#      slot shows "✓ Board write resolved" (or a clear error).
#   6. Click "Stop team" while a board_write proposal is still pending on
#      a different run -- confirm it actually stops (session_removed/
#      worktrees in the response), not the old silent no-op message.
```

## Post-review fix (test-review.md must-fix #1)
`docs/test-review.md`'s item 7 part 2 review found (verdict: changes
requested) that `TeamBoardResolveEndpointTests.test_approve_resolves_and_
starts_background_thread` and `test_reject_resolves_and_starts_background_
thread_no_taiga_call` never actually verified the "starts the background
driving thread" half of their own name/criterion -- every assertion in both
tests was satisfied by `resolve_board_write()`'s own synchronous side
effects (inbox move, history entry) alone, proven via the reviewer's
revert-and-fail (deleting the route's entire `cancel_event`/`Thread`/
`_team_threads_set()`/`t.start()` block left all 12 tests in the class
passing). The route's production code in `app/app.py` was already correct
(confirmed to mirror `/team/resolve`'s dispatch exactly) -- this was a
test-coverage gap only, no production code change.

Fixed per the reviewer's recommended fix (a): both tests now install a
`_record_team_threads()` fixture that rebinds `app.py`'s own module-level
`threading` name (not the shared `threading` module -- rebinding the
module's own attribute globally also intercepted `ThreadingHTTPServer`'s
per-connection threads in an earlier attempt, inflating the recorded count
to 3) to a thin proxy whose `Thread` is a subclass recording `(target,
args)` on every construction before delegating to the real
`threading.Thread`. This lets the background thread still genuinely run
(so the existing inbox/history assertions stay meaningful) while adding an
unambiguous, race-free assertion that `_run_team_in_background` was
constructed with `(name, run_id, cancel_event)` -- stronger than
`TeamResolveEndpointTests`'s existing `elapsed < 3.0` timing proxy, which a
fast stub lead could satisfy even with no thread dispatch at all.

Re-verified with the same revert-and-fail technique the reviewer used:
temporarily removed the route's thread-dispatch block again -- both
strengthened tests failed (`AssertionError: 0 != 1` on the recorded-thread
count) while the other 10 tests in the class stayed green; restored the
block and reran -- all 12 pass again, `git diff --stat app/app.py`
byte-identical to the pre-probe state (+302/-11, matching the reviewer's
own probe). Full suites also reran clean: `python3 -m unittest discover -s
tests` -> `Ran 874 tests` / `OK`; `node tests/test_team_frontend.js` ->
`ALL PASS (74/74)`.

No change to `app/app.py`, `docs/design.md`, or any other file -- this fix
is scoped to `tests/test_team_routes.py` only, per the dispatch's explicit
"do NOT touch the should-fix" instruction (the WCAG contrast finding on
`.team-btn` is unchanged, still open as a non-blocking follow-up).

# Implementation: Backlog item 7 part 1 -- board_read/board_write on the lead loop (backend)

## Summary
Added `board_read`/`board_write` as a fifth and sixth tool on the team
lead's tool loop in `app/teams.py`, backed by a new stdlib-only Taiga REST
client module (`app/taiga_board.py`). `board_read` executes and returns in
the same round (like `fact_check`); `board_write` never calls Taiga
directly -- it queues a proposal into a generalized `inbox.json` (new
`kind` discriminator) and blocks the run with a new `blocked_board_write`
status, exactly mirroring how `ask_user`/`blocked_ask_user` already work.
A new `resolve_board_write(run_id, action)` (approve/reject), reusing
`resolve_ask_user()`'s exact atomic-`os.replace()` race-safety shape, is
the only code path that actually calls Taiga's write API, and a new CLI
subcommand (`team-board-resolve`) drives it end to end with zero
`app/app.py` changes, matching part 1's explicit "backend + CLI only"
scope.

## Root cause
Not applicable (new feature, not a bugfix).

## Changes by file
- `app/taiga_board.py` (new) -- Taiga REST client: `TaigaPushError`/
  `TaigaConnectionError`/`TaigaHTTPError` (copied, not imported, from
  `scripts/taiga_push_spec.py`, per the spec's own explicit Non-goal),
  `_taiga_request()` (the one monkeypatched seam), `load_config()`,
  `authenticate()`, `lookup_project()`, `resolve_session()` (glue:
  config+auth+project-lookup in one call -- see "Key decisions"),
  `get_userstory()`, `list_userstories()`, `list_userstory_statuses()`,
  `set_status()`/`amend_description()`/`append_comment()` (all
  `version`-aware, PATCH via a shared `_patch_userstory_or_raise()`
  helper).
- `app/teams.py`:
  - `_LEAD_TOOL_NAMES` gains `board_read`, `board_write`;
    `_LEAD_TOOL_REQUIRED_ARGS` gains matching entries (`board_read`'s args
    all optional).
  - `_lead_tools()`/`_tool_prose()` gain the two new tool descriptions
    (tier 1 native schema + tier 2/3 prose); `"You have exactly four
    tools"` -> `"...six tools"`.
  - New `_BOARD_WRITE_MITIGATION` constant, appended in
    `_system_framing()` after the two existing mitigation clauses --
    states explicitly that `board_write` only queues a proposal and never
    applies it, and that a second `board_write` while one is pending will
    be rejected.
  - `_validate_lead_action()` gains `board_read`'s optional `ref`/`query`
    type checks, and `board_write`'s verb-enum check (`unknown_verb`,
    counts against the malformed-retry budget, same family as
    `unknown_tool`), positive-`ref` check, and a new
    `TEAM_BOARD_WRITE_VALUE_MAX_CHARS`-based length cap (`value_too_long`,
    same family).
  - `team_step()` gains two branches: `board_read` (executes+persists in
    the same round, catches `taiga_board.TaigaPushError` and folds it into
    an ordinary round outcome) and `board_write` (defensive "already
    pending" business-rule rejection if `state["status"] != "running"`;
    snapshots `current_value` via one `get_userstory()` call at proposal
    time for display only; writes the inbox; sets
    `status="blocked_board_write"`).
  - `_write_inbox()` renamed to `_write_ask_user_inbox()` (adds
    `"kind": "ask_user"`, every other field unchanged); new sibling
    `_write_board_inbox()` (`"kind": "board_write"` shape: verb, ref,
    value, note, current_value, proposed_at).
  - New `resolve_board_write(run_id, action)`, next to
    `resolve_ask_user()`, reusing its exact race-safety shape (read
    inbox.json's content before the atomic move, but never act on it --
    no Taiga call, no history entry -- until after `os.replace()` has
    already won the race). `approve` resolves `set_status`'s
    human-readable `value` to a numeric status id via a new
    `taiga_board.list_userstory_statuses()` lookup, fetches a *fresh*
    `version` via `get_userstory()`, then calls the matching write
    function; any `TaigaPushError` (conflict, network failure, unknown
    status name) is recorded in history as
    `"approved but Taiga rejected the write: {detail}"` -- the run always
    resumes.
  - Audited every literal `"blocked_ask_user"` string-check in
    `app/teams.py` (grepped per the spec's own instruction) and added
    `"blocked_board_write"` to the two that were status-shape-specific
    and would otherwise have missed it: `sweep_dead_teams()`'s crash-
    detection tuple (`("running", "blocked_ask_user")` ->
    `+"blocked_board_write"`) and `_team_exit_code()`'s "normal stopping
    point" tuple. Updated the docstrings that describe these same
    invariants in prose (`latest_run_for_project()`,
    `sweep_dead_teams()`) for accuracy. `_recover_in_progress()` and
    `stop_team()`'s own terminal-status check needed no change --
    `stop_team()`'s check is already a generic "not in the terminal set"
    check, not a `blocked_ask_user`-specific one.
  - New `_cli_team_board_resolve()` and a `team-board-resolve`
    `--run-id --action {approve,reject}` subcommand in `_parse_args()`/
    `main()`, mirroring `_cli_team_resolve()`'s own shape exactly
    (resolve, then `_drive_and_report()` on success).
  - New config constant `TEAM_BOARD_WRITE_VALUE_MAX_CHARS` (default 8000,
    near `TEAM_ASK_USER_ANSWER_MAX_CHARS`) and `_BOARD_WRITE_VERBS`
    (near `_LEAD_TOOL_NAMES`).
- `tests/test_teams_board.py` (new, 61 tests) -- Part A: `taiga_board.py`
  unit tests, monkeypatching `_taiga_request` exactly like
  `tests/test_taiga_push.py`. Part B: `app/teams.py` integration tests
  (`_validate_lead_action()`'s new categories, `team_step()`'s two new
  branches, `resolve_board_write()` including a genuinely-concurrent
  two-thread race test mirroring `TeamResolveEndpointTests`'s own
  `ask_user` race test, the `team-board-resolve` CLI end to end, and the
  `sweep_dead_teams()`/`_team_exit_code()` status-audit points).
- `tests/test_teams_lead.py` -- updated the pre-existing tests that
  literally hardcoded the old four-tool list/count (`LeadToolsTests`,
  `SystemFramingTests`'s "exactly four tools" assertions) and the
  `_write_inbox` -> `_write_ask_user_inbox` rename (`WriteInboxTests`,
  header comment), since these directly assert on the tool contract this
  spec changes -- not incidental collateral damage.

## Key decisions / tradeoffs
- **`taiga_board.resolve_session()`** (config load + authenticate +
  project lookup in one call) is not one of the eight functions the
  spec's own "Proposed approach" code block names explicitly. It's a
  small private-in-spirit wiring helper mirroring the exact three-step
  sequence `scripts/taiga_push_spec.py`'s own `_run()` already performs in
  this order -- added because every one of `team_step()`'s two branches
  and `resolve_board_write()` needs the identical three-step setup, and
  the spec's own Non-goals rule out refactoring `taiga_push_spec.py`
  itself to share it from there.
- **`taiga_board.list_userstory_statuses()`** is also not in the spec's
  eight-function list, but is necessary to fulfil the spec's own explicit
  requirement that "the apply step uses the id" for `set_status`: the
  acceptance criteria's own example (`board_write(verb="set_status",
  ref=42, value="In progress")`) stores a human-readable status *name* in
  the proposal, but `taiga_board.set_status()`'s own spec'd signature
  takes a numeric `status_id`. `resolve_board_write()`'s approve branch
  resolves the proposal's `value` to a status id via this lookup
  immediately before applying, case-insensitively matched against the
  project's own configured statuses; no match is folded into the same
  "approved but Taiga rejected the write" outcome family used for a
  genuine Taiga-side conflict.
- Simplified error-message wrapping relative to `taiga_push_spec.py`'s own
  three-layer pattern (`_authenticate`/`_lookup_project` raising internal
  marker exceptions, then a separate `*_or_raise()` layer translating them
  with an embedded config path): `taiga_board.py`'s functions raise
  clear, final `TaigaPushError` messages directly. This module has one
  caller category (`app/teams.py`, always via `DEFAULT_CONFIG_PATH`, no
  `--config` override concept), so the extra indirection that
  `taiga_push_spec.py` needs for its own multiple CLI call sites (normal
  push / `--dry-run` / `--verify`) isn't earning its keep here.
- `board_write` does **not** increment `state["action_count"]`, matching
  `ask_user`'s own existing precedent (also does not) -- both are
  escalations, not completed actions. `resolve_board_write()` likewise
  does not increment it, matching `resolve_ask_user()`. (A run whose
  *only* action before `finish` is a single `board_write` will still see
  `finish` rejected once as `premature_finish` immediately after
  resolving, for the same reason a bare `ask_user`-then-`finish` run
  would -- this is pre-existing behavior, not something this cycle
  introduces or needs to fix.)
- `current_value` for `append_comment` is `{}` (comments have no single
  "current value" to snapshot for display, unlike a status or a
  description).

## Deviations from spec
None substantive. Two points the spec flagged as open/needing developer
judgment during implementation, both resolved above under "Key decisions"
rather than left ambiguous:
- The exact Taiga optimistic-concurrency error shape (spec's own "Open
  questions": "the developer should confirm... against Taiga's own API
  docs or a live instance"). No live instance was available in this
  sandbox (matching `scripts/taiga_push_spec.py`'s own prior precedent,
  see its `docs/implementation.md`). Modeled as documented: HTTP 400/409
  on `PATCH` -> a clear "changed concurrently" conflict message, fully
  covered by the monkeypatched-seam test suite; the *exact* status code a
  live Taiga returns remains unconfirmed and is flagged again below under
  "Known limitations".
- `TEAM_BOARD_WRITE_VALUE_MAX_CHARS` was left as a developer's-call tuning
  constant per the spec; set to 8000 (matching `TEAM_GROUNDING_MAX_BYTES`'s
  precedent) rather than the narrower 2000-char `ask_user` default, per
  the spec's own stated reasoning that a userstory description could
  reasonably run longer than a short human answer.

## Known limitations
- **No live Taiga instance was reachable in this sandbox** (same
  constraint `scripts/taiga_push_spec.py`'s own test suite already
  documents) -- every Taiga-facing test monkeypatches `_taiga_request`
  (Part A) or the higher-level `taiga_board` functions `team_step()`/
  `resolve_board_write()` call (Part B). The exact HTTP status Taiga
  returns for a version conflict, and the exact comment-posting endpoint
  shape, are modeled per the spec's own documented Taiga REST
  conventions but not verified against a live server.
- **`app/app.py` is untouched, as the spec explicitly requires** (part 2
  is a future cycle) -- this is a disclosed, expected gap, not an
  oversight: a run that calls `board_write` today will show as `"idle"`
  on the Teams page's own status pill (`_handle_team_status`'s status-name
  map in `app/app.py` doesn't have a `"blocked_board_write"` entry, so it
  falls through to that map's own `"idle"` default) and `GET .../team/
  inbox` won't report it as pending (that handler only checks
  `status == "blocked_ask_user"`). Both are part 2's job to extend, per
  the spec's own explicit sequencing. Fully driveable and testable via the
  CLI (`team-board-resolve`) in the meantime, with zero web-visible change
  for any run that never calls `board_read`/`board_write`.
  **Also**, found by the reviewer: `app/app.py`'s pre-existing `POST
  .../team/stop` route silently no-ops for a run blocked on
  `blocked_board_write`, the same way it already does for
  `escalated_max_rounds` -- the web Stop button does nothing, though the
  CLI's `team-stop` still works and no data is lost. Not a new regression
  (the route's handling is status-list-based and simply doesn't yet name
  the new status), but part 2 should extend that list alongside the
  status-pill and inbox fixes above.
- A run whose only action before attempting `finish` was a single
  `board_write` (now resolved) will see one `premature_finish` rejection
  before a subsequent `finish` succeeds -- pre-existing `ask_user`
  behavior, not new, noted above under "Key decisions".

## How to verify locally
```
# Full existing suite, including the new file, all green:
python3 -m unittest discover -s tests -v

# Just this cycle's new tests (61):
python3 -m unittest tests.test_teams_board -v

# Grounding's read-only guard, unmodified, still passing (confirms no new
# board-access function was added to _GROUNDING_FUNCS and no grounding-
# section function calls into taiga_board):
python3 -m unittest tests.test_teams_grounding.GroundingStaticASTScanTests -v

# CLI, end to end, against a test-double (no live Taiga instance needed --
# the tests above monkeypatch app.taiga_board's own functions in-process;
# a real run requires ~/.config/ai-dev-switchboard/taiga-push.env, see
# scripts/taiga-configure-push.sh):
python3 -m app.teams team-start <workdir> --task "..." --lead <engine>
# once the lead calls board_write and the run reports blocked_board_write:
python3 -m app.teams team-board-resolve --run-id <run_id> --action approve
python3 -m app.teams team-board-resolve --run-id <run_id> --action reject
```
