# Test & Review: Backlog item 19 part 1 -- interject a free-form message into a running team (backend)

## Scope
Covers every acceptance criterion in `docs/spec.md` for this cycle:
`teams.interject()`, `team_step()`'s new drain checkpoint, the
`_INTERJECT_MITIGATION` prompt clause, `POST /projects/<name>/team/interject`,
the `team-interject` CLI subcommand, and `human.jsonl`'s merge into
`GET .../team/events`. Branch: `backlog/team-chat-interrupt-19`. Nothing
committed by the developer; nothing committed by this review.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `interject()` on a `running` run: `{"ok": True, ...}`, envelope appended to `human.jsonl`, `run.json` byte-for-byte unchanged | automated | pass | `InterjectTests.test_running_run_appends_envelope_and_never_touches_run_json` |
| 2 | `interject()` on a terminal status (`finished`/`error`/`escalated_max_rounds`/`stopped`): `{"ok": False, "error": ...}` naming the status, `human.jsonl` not written | automated | pass | `InterjectTests.test_terminal_statuses_rejected_no_file_written` |
| 3 | `interject()` on unknown `run_id`: `{"ok": False, "error": "no such run_id: <id>"}` | automated | pass | `InterjectTests.test_unknown_run_id_returns_shaped_error` |
| 4 | Queued-but-undrained message + `running`: next `team_step()` appends one `human_interject` history entry, advances cursor, does NOT call the lead | automated | pass | `TeamStepDrainInterjectTests.test_drain_appends_history_entry_and_never_calls_the_lead` |
| 5 | Drained round N's full text surfaces in round N+1's `_round_context()` as `"(round N, human_interject)"` | automated | pass | `TeamStepDrainInterjectTests.test_full_text_surfaces_in_next_round_context`; traced `_round_context()` (app/teams.py:2391) format string myself |
| 6 | Two messages queued before one drain: two separate entries, in file order, cursor past both in one call | automated | pass | `TeamStepDrainInterjectTests.test_two_queued_messages_drained_together_in_order` |
| 7 | `GET .../team/events` returns the posted message, `agent="human"`, `kind="message"`, via real POST then real GET | automated | pass | `TeamInterjectEndpointTests.test_posted_message_appears_in_team_events_feed` |
| 8 | `run_id` omitted -> `latest_run_for_project(name)` | automated | pass | `TeamInterjectEndpointTests.test_run_id_omitted_defaults_to_latest_run_for_project` |
| 9 | `run_id` belonging to a different project -> 400 "different project" | automated | pass | `TeamInterjectEndpointTests.test_explicit_run_id_for_a_different_project_400_specific_reason` |
| 10 | Path-traversal `run_id` rejected against `_RUN_ID_RE` before any file access | automated | pass | `TeamInterjectEndpointTests.test_path_traversal_run_id_400_planted_file_never_opened` (planted-file-never-opened technique) |
| 11 | Empty/whitespace or oversized `text` -> 400, `teams.interject()` never called | automated | pass | `TeamInterjectEndpointTests.test_empty_text_400_teams_interject_never_called`, `test_oversized_text_400_teams_interject_never_called` (call-count double) |
| 12 | CLI `team-interject`: prints `queued for run <id>`, exit 0, no lead call | automated | pass | `CliTeamInterjectTests.test_success_prints_queued_message_exit_0_without_driving_the_run` |
| 13 | All four required-verbatim mitigation clauses present every tier, including `_INTERJECT_MITIGATION` | automated | pass | `SystemFramingTests.test_mitigation_clauses_present_every_tier` (extended, general loop -- see "Deviation check" below) |
| 14 | **Concurrency claim (spec's core correctness argument)**: interject arriving while the driving thread is genuinely mid-round (blocked in a slow lead/delegate call) does not race, corrupt, or get lost/misapplied | manual, hands-on repro (not read-and-trust) | pass | Scratch script, real `threading.Thread`, real `Event`-gated blocking call; see "Concurrency verification" below |
| 15 | `human_cursor` persistence across a simulated crash+restart: no double-delivery, no loss | manual, hands-on repro | pass | Scratch script, `_load_state()` reload mid-sequence; see "Concurrency verification" below |
| 16 | `blocked_ask_user`/`blocked_board_write` accept an interjection (queued, not delivered until resumed) | automated | pass | `InterjectTests.test_blocked_ask_user_and_blocked_board_write_allowed`, `TeamInterjectEndpointTests.test_blocked_ask_user_run_accepts_an_interjection_too` |
| 17 | Malformed non-traversal `run_id` -> same 400 shape, no 500 | automated | pass | `TeamInterjectEndpointTests.test_malformed_non_traversal_run_id_400_no_500` |
| 18 | Unknown project -> 404; no run at all -> 400 with specific reason | automated | pass | `TeamInterjectEndpointTests.test_unknown_project_404`, `test_no_run_at_all_400_specific_reason` |
| 19 | Success path starts no background thread (unlike `/team/resolve`) | automated | pass | `TeamInterjectEndpointTests.test_valid_running_run_returns_ok_appends_no_thread_started` (asserts `_team_threads_get` is `None`) |

### Concurrency verification (done by hand, not accepted on the design's own say-so)
Wrote and ran a standalone script (not part of the committed test suite --
scratch verification only) that:
1. Starts a real background thread calling `team_step()` with a `_call_lead`
   stub gated on a `threading.Event`, so the "driving thread" is
   *genuinely* blocked inside its own lead call, matching the reviewer
   brief's required scenario ("mid-round, e.g. blocked in a slow delegate
   call") -- not a simulated/inferred version of it.
2. While that thread is blocked, calls `teams.interject()` from the main
   thread (the "request thread"). Confirmed `run.json`'s bytes are
   byte-for-byte identical before and after the interject call, while the
   driving thread is still mid-round.
3. Releases the driving thread, lets its own round-end `_persist()` run,
   and confirms: (a) `run.json` is valid, parseable JSON reflecting exactly
   the round the driving thread was already committed to, not corrupted or
   silently overwritten; (b) the interjected message is durably present in
   `human.jsonl`, untouched; (c) `human_cursor` on disk is correctly still
   behind the message's offset (not drained mid-round, matching the design
   claim it's picked up only at the next boundary).
4. A second run of the same scenario, where the round wasn't terminal
   (finish rejected as premature), continued: called `team_step()` again
   for the actual next round boundary and confirmed the queued message is
   drained there -- exactly once, as its own `human_interject` entry, lead
   not called that round -- then called `team_step()` a third time and
   confirmed no double-delivery (message doesn't reappear).
5. A separate script simulated a crash+restart: drained one message,
   persisted, posted a second message "during the outage", `del`eted the
   in-memory state object, reloaded fresh via `_load_state()` (exactly what
   a resumed process does), and confirmed the reloaded `human_cursor`
   carries forward correctly, the first message's history entry survived,
   and the next `team_step()` call drains exactly the second message once
   -- no loss, no double-delivery across the simulated restart boundary.

All five checks passed. This corroborates the spec's own architectural
claim (never call `_persist()` from `interject()`, so there's nothing for
the driving thread's own last-write-wins `_persist()` to clobber) against
an actual concurrent execution, not just a read of the code.

## Regression check
Full existing suite: `python3 -m unittest discover -s tests` -> **Ran 1034
tests in 145.844s -- OK** (zero failures this run; the flaky
`test_two_near_simultaneous_starts_exactly_one_succeeds` real-tmux timing
race the developer flagged in `docs/implementation.md` did not reproduce in
this run, consistent with it being flaky/timing-load-dependent, not related
to this change).

Targeted re-run: `python3 -m unittest tests.test_teams_lead
tests.test_team_routes tests.test_teams_board -v` and the 4 new/extended
classes individually -- all pass, matching the developer's own reported
counts.

No lint/type-check tooling exists in this project (no `Makefile`,
`pyproject.toml`, `.flake8`, or CI config referencing one) -- not
applicable.

No frontend changes in this diff (confirmed via `git diff app/app.py`: the
only change is the one-tuple `files` list extension in
`_handle_team_events()` and the new backend route; no JS touched) -- the
existing generic `kind === 'message'` rendering path (`app/app.py`,
`teamFeedEventBody()`) already `esc()`s `e.text`, confirmed by reading it
directly; no new XSS surface.

## Defects found
None. Testing pass is clean -- proceeding to the review pass.

---

## Spec coverage
Every acceptance criterion in `docs/spec.md` maps to at least one automated
test that was actually run this session (see table above, all pass). The
one criterion phrased as "extend the existing per-tier framing test the
same way `_BOARD_WRITE_MITIGATION` was covered" required checking a factual
claim before trusting it: grepped `tests/test_teams_lead.py` and
`tests/test_teams_board.py` myself for `_BOARD_WRITE_MITIGATION` -- zero
hits anywhere in the test suite. Only `_FACT_CHECK_MITIGATION` is checked,
inside the general `SystemFramingTests
.test_mitigation_clauses_present_every_tier` loop; `_DELEGATION_HISTORY_MITIGATION`
has its own dedicated class. The developer's disclosed deviation (extend
the general loop, since no dedicated precedent for board_write actually
exists) is accurate and is the more faithful reading of the criterion's own
intent ("covered the same way" -> not specially, since board_write wasn't
either).

Edge cases from `docs/spec.md` not restated as acceptance criteria but
worth confirming were honored, not silently dropped: the "message posted in
the exact instant a run exhausts max_rounds is stranded" and "two
simultaneous posts could compute the same cosmetic seq" tradeoffs are both
explicitly named in `docs/implementation.md`'s "Known limitations" as
carried-forward, accepted, narrow -- consistent with `docs/spec.md`'s own
framing (not silently reinterpreted as fixed or as new problems).

No gaps found -- no acceptance criterion is implemented-but-untested or
tested-but-not-actually-implemented.

## Findings (most severe first)

No must-fix or should-fix findings.

### Nit: scratch verification scripts are not part of the committed suite
The two hands-on concurrency/restart scripts I wrote to verify the core
race-safety and cursor-persistence claims (see "Concurrency verification"
above) live only in this session's scratchpad, not in `tests/`. The
existing automated tests (`TeamStepDrainInterjectTests`, `InterjectTests`)
already prove the individual mechanics (drain-not-double-delivered,
run.json-untouched-by-interject) in isolation; what my scripts add is
proof under genuine concurrent thread execution and a genuine
reload-from-disk, which the existing suite doesn't exercise with real
threads. Optional follow-up: promote a trimmed version of scenario 2 (queued
mid-round, drained exactly once at the next boundary, no double-delivery)
into `tests/test_teams_lead.py` as a `threading`-based regression test, so
this guarantee is enforced by CI rather than only by this one-time review
pass. Not a blocker -- the design is now verified correct, and the existing
tests already cover the same logical guarantees at the single-thread level.

## Follow-ups (non-blocking)
- Optional: add a `threading`-based regression test for the mid-round
  concurrency scenario (see nit above), so future changes to
  `team_step()`'s drain checkpoint or `_persist()` timing can't silently
  reintroduce the race this cycle was built specifically to avoid.
- Part 2 (chat-bubble UI) is out of scope here per `docs/spec.md`'s own
  explicit Non-goals; no action needed from this review.

## Overall verdict
**Approve.** All acceptance criteria are implemented and covered by tests I
personally ran this session; the concurrency-safety claim central to this
spec was independently verified against real concurrent execution (not
accepted on the design doc's own say-so), and `human_cursor` persistence
across a simulated crash/restart was independently verified as well. The
one disclosed deviation (mitigation-clause test placement) was checked
against the actual test file and found accurate and reasonable. No
security, correctness, or scope-creep issues found in the diff. One
non-blocking nit/follow-up only (promoting the ad hoc concurrency repro
into the committed suite) -- does not block this cycle.

---

# Test & Review: Backlog item 19 part 2 -- chat-UI compose surface for interjecting into a running team (frontend)

## Scope
Covers every acceptance criterion in `docs/spec.md` for this cycle (the
compose box's visibility gate, coexistence with the escalation panel, the
character counter/disabled-Send guard, the `team-interject` dispatch/
428-retry/result-handling branch, the `human-message` feed classification +
filter pill, and draft-discard-on-status-transition), plus independent
verification of the developer's one disclosed deviation (no in-flight
Send-disable) against both `docs/spec.md`'s "Non-goals" and the actual
behavior of every sibling `team-*`/`deploy` action button. Branch:
`backlog/team-chat-ui-19b`. Frontend-only per spec's own non-goal (no
`app/teams.py`, route, or data-shape change) -- confirmed via `git diff
--stat`. Nothing committed by the developer; nothing committed by this
review.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Visibility: `team.status === 'running'` renders the compose box (`#interject-<name>` + `#interject-send-<name>`) | automated | pass | `test_team_frontend.js`: "running renders the compose box (textarea + Send button)" |
| 2 | Visibility: `team.status === 'blocked' && waiting_on_you === true` renders BOTH the escalation panel and the compose box, with the context-aware placeholder | automated | pass | "blocked + waiting_on_you renders BOTH the escalation panel and the compose box, with the context-aware placeholder" |
| 3 | Visibility: `idle`, `finished`, `error`, and `blocked` w/ `waiting_on_you === false` (escalated_max_rounds) each individually omit the compose box | automated | pass | "idle, finished, error, and blocked-without-waiting_on_you each omit the compose box" (loops all 4 cases) |
| 4 | `teamAcceptsInterject()` matches exactly the statuses `teams.interject()` accepts server-side | automated + manual code trace | pass | "teamAcceptsInterject() matches exactly the statuses teams.interject() accepts server-side"; independently traced `app/teams.py:4288-4291` (`interject()`'s own status check) and `app/app.py:5409` (route's own check) and `app/app.py:4930-4989` (status-collapse map + `waiting_on_you` definition) against the frontend predicate myself -- see "Deviation / cross-cutting check" below |
| 5 | Empty/whitespace draft: Send disabled. Draft > 2000 chars: Send disabled, counter carries `over-limit` | automated | pass | "an empty or whitespace-only draft keeps Send disabled; an over-2000-char draft disables Send and marks the counter over-limit" |
| 6 | Non-empty ≤2000-char Send click -> `doTeamInterject()` -> `toggle('team-interject', name, true, null)` -> POST `{text: <trimmed>}` [+ `code` on retry] to `/projects/<name>/team/interject`, 428-overlay label `"Sending message: <name>"`, retry resends the same trimmed text | automated | pass | "clicking Send dispatches POST ... and the 428 code-overlay label reads..." |
| 7 | Empty/whitespace Send click: no request dispatched, client-side error shown | automated | pass | "an empty/whitespace draft sends no request and shows a client-side error, mirroring doTeamResolve()" |
| 8 | `{"ok": true, ...}` response: `#team-msg-<name>` shows "✓ Message sent" (success), textarea + draft mirror cleared, Send re-disabled | automated | pass | "a successful send shows 'Message sent', clears the textarea and the draft mirror, and re-disables Send" |
| 9 | `{"error": "..."}` 400 response: `#team-msg-<name>` shows "✕ Error: <server message>" (error), draft preserved (textarea NOT cleared) | automated | pass | "a failed send preserves the draft text (textarea not cleared) and shows the server error" |
| 10 | A human feed event (`agent: "human"`, `kind: "message"`) -> `teamFeedEventKindClass()` returns `'human-message'`; rendered row carries `kind-human-message` | automated | pass | "a human-authored feed event classifies as human-message and renders with the kind-human-message row class" |
| 11 | Filter-pill order `All, lead, human, <member1>, ...`; clicking `human` filters via the existing generic `agent === filter` logic, no new filter code path | automated | pass | "renderTeamFeed() lists filter pills in order All, lead, human, <member1>, ..." |
| 12 | `human` pill renders unconditionally, even before any interjection has been sent | automated | pass | "the human filter pill renders even before any interjection has been sent for a run" |
| 13 | Unsent draft discarded on transition to a compose-ineligible status; does not resurrect on a later run for the same project | automated | pass | "an unsent draft is discarded once the status transitions to a compose-ineligible one on the next poll" |
| 14 | Draft discarded on the idle-transition path specifically (`clearTeamFeedState()`'s own new line) | automated | pass | "a team stopping (going idle) clears the compose-box draft state" |
| 15 | `git diff` for this cycle touches only `app/app.py` (+ tests/docs) -- no `app/teams.py`, route, config, or data-shape change | automated (`git diff --stat`) | pass | `git diff --stat`: only `app/app.py`, `tests/test_team_frontend.js`, `docs/*.md` changed; zero hunks in `app/teams.py` |
| 16 | `role="log"`/`aria-live="polite"` accessibility contract on the feed genuinely unchanged | manual diff-check (not accepted on the developer's own say-so) | pass | `git diff app/app.py \| grep 'role="log"\|aria-live'` returns **zero** hunks; the only two occurrences of the string in the file are both pre-existing, unmodified lines at `app/app.py:3285-3294` |
| 17 | XSS: human message text escaped the same way as every other feed entry's text | manual code read | pass | `teamFeedEventBody()`'s generic `kind === 'message' \|\| kind === 'status'` branch (`app/app.py:3216`, unmodified by this diff) applies `esc(e.text \|\| '')`, the same `textContent`-based escaping (`app/app.py:2612-2614`) every other text-bearing branch uses; this branch is untouched code, not new for this cycle |
| 18 | Client `TEAM_INTERJECT_MAX_CHARS_CLIENT` (2000) actually matches server `teams.TEAM_INTERJECT_MAX_CHARS` default | manual code read | pass (matches default; drift risk is disclosed, not silently missed) | `app/teams.py:199`: `TEAM_INTERJECT_MAX_CHARS = int(os.environ.get("TEAM_INTERJECT_MAX_CHARS", "2000"))` vs. `app/app.py`'s hardcoded `2000` -- see "Deviation / cross-cutting check" below for the drift-handling analysis |
| 19 | In-flight Send-disable deviation: spec's "Non-goals" is unambiguous, and matches every sibling `team-*`/`deploy` button's actual behavior | manual code read across `doTeamStart`, `doTeamStop`, `doTeamResolve`, `doTeamBoardResolve`, `doDeploy`, and the shared `toggle()`/`performAction()` dispatcher | pass (deviation reasoning confirmed accurate) | See "Deviation / cross-cutting check" below |

## Deviation / cross-cutting check

**In-flight Send-disable (design doc's wireframe vs. spec's Non-goals).**
Read both documents directly rather than trusting the developer's own
framing in `docs/implementation.md`:
- `docs/spec.md` "Non-goals": *"No double-submit / in-flight Send-disable
  protection beyond what other actions in this app already have. No
  existing action button (Submit answer, Approve/Reject, Stop team)
  disables itself while its own POST is in flight ... not introducing a new
  pattern for this one control alone."* This is unambiguous -- it doesn't
  hedge or leave room for a UI-only exception.
- `docs/design.md`'s "Compose box: Sending in Progress" wireframe does show
  both the textarea and Send button as `[disabled]`, which is a real
  conflict with the spec, not a developer misreading.
- I independently read `doTeamStart()` (`app/app.py:3882-3899`),
  `doTeamStop()` (`3905-3910`), `doTeamResolve()` (`3375-3387`),
  `doTeamBoardResolve()` (`3395-3400`), `doDeploy()` (`3865-3874`), and the
  shared `toggle()`/`performAction()` dispatcher (`3825-3855`) all of which
  every `team-*` action (including the new `team-interject`) funnels
  through. None of these `do*()` functions, nor `toggle()` itself, ever set
  `.disabled` on any button or textarea keyed by `name`. The claim "matches
  every other team-* action button" is accurate, not just plausible.
- Per this pipeline's own convention (spec is authoritative for scope
  questions the spec has already explicitly settled; design translates
  spec into UI shape), implementing per the spec here is correct, and the
  deviation was disclosed prominently rather than silently resolved. No
  finding.

**`TEAM_INTERJECT_MAX_CHARS_CLIENT` drift risk (explicitly asked to verify).**
Confirmed the client hardcodes `2000` (`app/app.py:2716`) and the server
defaults to the same value via `os.environ.get("TEAM_INTERJECT_MAX_CHARS",
"2000")` (`app/teams.py:199`) -- they match today. If the env var is ever
overridden away from `2000`, the two constants drift, and a message the
client shows as "sendable" (under its own stale 2000-char guard) could still
draw a late server-side 400. Checked what actually happens in that case: the
route's own 400 (`app/app.py:5555-5558`) is caught by the *generic*
`team-interject` branch in `handleActionResult()` (this diff, `app/app.py`
~3748-3773), which renders `"✕ Error: <server message>"` in the existing
`.team-msg` slot and preserves the draft for editing/retry -- test #9 above
exercises this exact code path end-to-end (a 400 for a status-based
rejection; a length-based 400 would hit the identical branch). This is not
a crash, a silent failure, or data loss -- it degrades gracefully to the
same inline-error UX every other action's server-side rejection already
gets, and the drift is explicitly disclosed in both `docs/spec.md`
("Non-goals"/"Open questions") and `docs/implementation.md` ("Known
limitations"), consistent with the pre-existing pattern
`TEAM_RESOLVE_MAX_CHARS`'s own hardcoded-2000 client guard
(`doTeamResolve()`) already has. **Not a new should-fix** -- already
adequately handled and already disclosed; no code or doc change needed.

## Independently recomputed WCAG contrast claims (this cycle's `docs/design.md` section)
Recomputed relative-luminance contrast from the literal hex values myself
rather than trusting the stated ratios:
- `#4da6ff` (new left-border accent) on `#1c1c1c`: **6.666:1** -- matches
  the design doc's claimed "6.67:1", correctly passes the 3:1 graphical-
  element threshold. Accurate.
- `#888888` (counter text) on `#1c1c1c`: **4.808:1**, not the design doc's
  claimed "6.14:1" (which is actually the `#ff6b6b` over-limit ratio,
  apparently duplicated onto this line by copy/paste). The stated
  conclusion ("passes WCAG AA", 4.5:1 threshold for normal text) still
  holds since 4.808 ≥ 4.5 -- a documentation-accuracy nit, not a real
  compliance failure.
- `#ff6b6b` (over-limit counter text) on `#1c1c1c`: **6.141:1** -- matches
  the design doc's claimed "6.14:1" (correctly stated on its own line, just
  misapplied to the `#888` line above it too). Accurate.
- **`#ffffff` Send button text on `#34c759` button background: 2.217:1,
  not the design doc's claimed "5.05:1"** -- and 2.217:1 **fails WCAG AA**
  outright (needs ≥4.5:1 at 14px; the button text isn't large enough to
  qualify for the 3:1 large-text allowance either way). See "Findings"
  below -- this is real, but pre-existing and out of scope for this cycle's
  diff, not a new issue this cycle introduced.

## Regression check
`node tests/test_team_frontend.js` -> **ALL PASS (94/94)** -- 80
pre-existing + 14 new, matching `docs/implementation.md`'s reported count
exactly.

Full existing suite: `python3 -m unittest discover -s tests` -> **Ran 1034
tests in 145.054s -- OK** (zero failures; matches part 1's own baseline
count exactly, consistent with this cycle being frontend-only and adding no
new Python tests, as `docs/implementation.md` claims).

`python3 -m py_compile app/app.py app/teams.py` -> clean, no syntax errors.

No lint/type-check tooling exists in this project (no `Makefile`,
`pyproject.toml`, `.flake8`, or CI config referencing one) -- not
applicable, consistent with part 1's own review.

## Defects found
None. Testing pass is clean -- proceeding to the review pass.

---

## Spec coverage
Every acceptance criterion in `docs/spec.md`'s "Acceptance criteria" list
(11 items, all checkbox entries) maps to at least one automated test that
was actually run this session and passed (see test-case table above,
#1-#15). No criterion is implemented-but-untested or
tested-but-not-actually-implemented.

Edge cases from `docs/spec.md` not restated as acceptance criteria but
independently checked: the client/server char-limit drift risk (verified
above, degrades gracefully); the "no double-submit protection" claim
(verified above, accurate and matches sibling buttons); the two-tabs-
concurrent-interject edge case (backend-only, already proven safe in part
1's own review, correctly not re-litigated here); the accessibility
contract (`role="log"`/`aria-live="polite"`) genuinely untouched (diff-
checked, not just claimed).

## Findings (most severe first)

### 1. `docs/design.md`'s Send-button contrast claim is wrong, and the actual pairing fails WCAG AA -- should-fix (follow-up, non-blocking for this cycle)
- File: `docs/design.md:920` (this cycle's own "Accessibility & platform
  notes" section; the same claim originates earlier at `docs/design.md:513`,
  backlog item 16's design section, already merged)
- Issue: the doc states *"Send button text (#fff) on button background
  (#34c759): 5.05:1 (passes WCAG AA for large button text; existing token,
  already audited elsewhere in this design)."* Recomputing WCAG relative
  luminance from the literal hex values (`.team-btn`'s actual CSS,
  `app/app.py:2160-2162`: `background: #34c759; color: #fff;`, 14px
  font-weight 600) gives **2.217:1**, not 5.05:1 -- and 2.217:1 fails WCAG
  AA outright (needs ≥4.5:1 for normal text; 14px doesn't meet the
  ≥18.66px/14pt threshold to qualify for the more lenient 3:1 large-text
  allowance even as bold).
- Failure scenario: a low-vision or color-deficient operator using this
  page's dark theme may struggle to read "Send" (and "Start", "Stop team",
  "Approve"/"Reject", "Deploy", every other `.team-btn`/`.deploy-btn`) --
  this is a real, currently-shipping accessibility gap across the whole
  app, not a hypothetical.
- Why this doesn't block this cycle: the color pairing itself is 100%
  pre-existing and unmodified by this diff (`.team-btn`'s CSS is untouched;
  the new Send button is a pure reuse per the spec's own explicit "No new
  colors introduced" directive). The same wrong claim was already present,
  unchallenged, in an earlier merged design doc (backlog item 16,
  `docs/design.md:513`) -- this cycle only repeats/references it
  ("already audited elsewhere"). Fixing `.team-btn`/`.deploy-btn`'s button
  styling app-wide is a cross-cutting change well outside this narrow
  frontend cycle's scope (and outside `docs/spec.md`'s own non-goals).
- Recommendation: open a small, dedicated follow-up backlog item to fix
  `.team-btn`/`.deploy-btn` text/background contrast app-wide (e.g. a
  darker green or black button text), and correct the two now-known-wrong
  contrast claims across `docs/design.md` while at it.

### 2. Nit: `docs/design.md`'s `#888` counter-text contrast ratio is also mis-stated (copy/paste), though the conclusion happens to still be correct
- File: `docs/design.md:921` (and the duplicate at `:970` "Accessibility
  audit" list is not affected, only the numeric claim at the "Color
  contrast" bullet)
- Issue: states "Character counter text (#888) on row background (#1c1c1c):
  6.14:1" -- recomputed actual value is 4.808:1 (the 6.14:1 figure is the
  `#ff6b6b` over-limit variant's correct ratio, apparently duplicated onto
  this line). 4.808 ≥ 4.5 so the stated conclusion ("passes WCAG AA")
  still holds -- this is a documentation-accuracy nit only, not a
  compliance failure.
- Recommendation: fix alongside finding #1 if a doc-correction pass is
  ever done; not worth a dedicated follow-up on its own.

## Follow-ups (non-blocking)
- Open a dedicated accessibility backlog item: fix `.team-btn`/
  `.deploy-btn` white-on-`#34c759` button text contrast app-wide (currently
  ~2.2:1, fails WCAG AA), and correct the two related mis-stated contrast
  claims in `docs/design.md` (Send button 5.05:1 -> actual ~2.22:1; counter
  text 6.14:1 -> actual ~4.81:1, conclusion unaffected).
- Consider adding one explicit XSS-payload test (`<script>`/`<img onerror>`
  in a human-authored message's text) to `tests/test_team_frontend.js` or
  the broader feed-rendering suite -- today this is verified only by code
  reading (the shared, unmodified `esc()`/`teamFeedEventBody()` path), not
  by a dedicated regression test for any feed event kind, human or
  otherwise. Not new to this cycle; a pre-existing gap in test coverage.

## Overall verdict
**Approve.** All 11 acceptance criteria in `docs/spec.md` are implemented
and covered by automated tests I personally ran this session (94/94 new
JS suite, 1034/1034 full Python suite, both clean). The compose box's
visibility gate was independently traced against `teams.interject()`'s own
status check and the `/status` collapse map, not just trusted to match; the
`role="log"`/`aria-live="polite"` accessibility contract was diff-checked
and confirmed genuinely untouched; human-message text escaping was
confirmed to go through the same `esc()` path every other feed entry uses;
and the developer's one disclosed deviation (no in-flight Send-disable) was
independently confirmed accurate against both the spec's literal wording
and every sibling button's actual code. One should-fix finding was
surfaced -- `docs/design.md`'s Send-button WCAG contrast claim is wrong and
the actual pairing fails WCAG AA -- but it is a pre-existing, cross-cutting,
already-merged issue (inherited from backlog item 16's own design doc, not
introduced by this diff, and out of scope for this cycle's own non-goals),
so it is filed as a non-blocking follow-up rather than a blocker. No
must-fix issues found.

# Test & Review: Backlog item 20 -- `.team-btn`/`.deploy-btn` WCAG AA contrast fix

## Scope
Covers all three acceptance criteria in `docs/spec.md` for this cycle: the
`.deploy-btn, .team-btn` shared CSS rule's text/background contrast ratio,
no regressions in any test that references these selectors, and correction
of `docs/design.md`'s two known-wrong contrast claims for this pairing
(this is the follow-up filed at the end of the item 19 part 2 review above).
Branch: `backlog/team-btn-contrast-20`. Single-line CSS change
(`app/app.py`) plus two `docs/design.md` text corrections. Nothing
committed by the developer; nothing committed by this review.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `.deploy-btn, .team-btn`'s shipped rule now reads `color: #111` (not `#fff`), `background: #34c759` and every other property unchanged | automated (grep + diff read) | pass | `git diff app/app.py` shows exactly the one-line change; `grep -n "deploy-btn, .team-btn" app/app.py` confirms shipped rule |
| 2 | Independently recomputed contrast ratio for `#111` text on `#34c759` background meets/exceeds WCAG AA's 4.5:1 | automated, hand-verified against WCAG relative-luminance formula from raw hex values (not trusting the developer's stated 8.51:1) | pass | Python script computing `lin()`/`lum()`/contrast per W3C formula: `L(#111)=0.005605`, `L(#34c759)=0.42298`, ratio = **8.506:1** (rounds to 8.51:1, matches implementation.md's claim exactly) -- passes AA (4.5:1) and AAA (7:1) |
| 3 | Old `#fff`-on-`#34c759` pairing actually failed AA (confirms the bug was real, not a false alarm) | automated, same script | pass | ratio = **2.220:1**, well under 4.5:1 -- matches implementation.md's "~2.2:1" claim |
| 4 | No other background variant of `.team-btn`/`.deploy-btn` (hover/disabled/state) exists that `#111` text would now fail against | manual code read | pass | `grep -n "team-btn\|deploy-btn"` across `app/app.py`: only the one shared rule (line 2160) sets `background`/`color` for these classes; the only `:disabled` CSS rule in the file (`.clone-form input:disabled, .clone-form button:disabled`) does not target `.team-btn`/`.deploy-btn`, and no `:hover`/`:active`/`:focus` rule targets them either -- disabled team/deploy buttons render with the same explicit `background:#34c759; color:#111`, same 8.51:1 ratio |
| 5 | `docs/design.md`'s two corrected claims (item 16 and item 19 part 2 sections) state the accurate color/ratio | automated, same script | pass | Both corrected lines claim `#111` / **8.51:1** -- matches the independently recomputed 8.506:1; both also correctly note the old `#fff`/~2.2:1 figure as failing AA, matching the independently recomputed 2.220:1 |
| 6 | No existing test hardcodes the old `#fff` color for `.team-btn`/`.deploy-btn` (so nothing needed updating, and nothing now silently mismatches) | automated | pass | `grep -n "deploy-btn\|color" tests/test_deploy_frontend.js`: only two assertions, both check class-string presence/absence, not color; full-text search of `tests/*.js`/`tests/*.py` for any `#fff` tied to these selectors found none |
| 7 | Full existing JS frontend suite unaffected | automated | pass | `node tests/test_team_frontend.js` -- ALL PASS (94/94) |
| 8 | Full existing Python backend suite unaffected | automated | pass, on 2nd run (see Regression check) | `python3 -m unittest discover -s tests` -- 1034/1034 |
| 9 | `app/app.py` still parses as valid Python (inline HTML/CSS string, no syntax break) | automated | pass | `python3 -m py_compile app/app.py` -- OK |

## Regression check
Full existing suite run twice:
- `node tests/test_team_frontend.js` -- ALL PASS (94/94), one run.
- `python3 -m unittest discover -s tests` -- **run 1: 1034 tests, 1 failure**
  (`RealTmuxHeadlessTests.test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask`,
  `AssertionError: False is not true` on `results["r"]["ok"]`), preceded in
  the log by repeated `duplicate session: team-sessionrace-p<pid>` lines from
  unrelated tests in the same run. Investigated before accepting as
  pre-existing flakiness rather than shrugging it off:
  - Re-ran that single test in isolation: passed (`Ran 1 test ... OK`).
  - Re-ran the whole `tests/test_teams_headless.py` file alone (91 tests):
    all passed, no failure.
  - Re-ran the **entire** `python3 -m unittest discover -s tests` suite a
    second time, byte-for-byte the same code: **1034 tests, 0 failures,
    OK** -- the failure did not reproduce.
  - `git log -- tests/test_teams_headless.py` surfaces commit `dfac08c`
    ("Scope real-tmux test sessions per process...") on this same branch
    history, which explicitly documents this exact class of failure as a
    known, partially-fixed, real-tmux cross-process session-race flake
    ("had been failing roughly 2 runs in 17 while always passing in
    isolation... attributed to unrelated flakiness for four review
    cycles").
  - Conclusion: this is the same known pre-existing environmental flake,
    not a regression introduced by this cycle's diff -- the diff touches
    only a CSS color literal in `app/app.py`'s inline template string and
    has no code path anywhere near `teams.py`'s tmux session handling.
    Not filed as a defect against this cycle; not blocking.
- `python3 -m py_compile app/app.py` -- OK.

## Spec coverage
| Acceptance criterion (docs/spec.md) | Implemented? | Tested? | Gap? |
|---|---|---|---|
| 1. `.deploy-btn, .team-btn` pairing computes to >=4.5:1, verified by real calculation | Yes (`color: #111`) | Yes -- independently recomputed by hand from raw hex, not trusted from the developer's figure (case 2) | None |
| 2. No existing test breaks; any hardcoded old-color assertion updated | Yes (none existed to update, confirmed by search) | Yes (case 6, and full suite regression run) | None |
| 3. `docs/design.md`'s known-wrong contrast claims corrected | Yes, both sections | Yes -- both corrected figures independently recomputed and confirmed accurate (case 5) | None |

All three acceptance criteria implemented and independently verified. No
criterion left uncovered.

## Findings (most severe first)
None. No must-fix, should-fix, or nit findings from this review pass.

- Correctness: the one-line change is exactly what the spec/implementation
  describe; no other property in the shared rule was touched; no other
  selector pairs this green with white text anywhere in `app/app.py`
  (verified by grep across the full file for `#34c759` -- every other use
  already pairs with `#111` or is a non-text usage like `.deploy-msg.success`'s
  own text-on-dark-background color, unrelated to this button pairing).
- Security: none applicable -- pure CSS literal, no user input, no new
  attack surface.
- Simplicity/scope: minimal, in-scope diff -- one CSS value plus two
  factual doc corrections, matching the spec's explicit "no new color value
  needed, single shared rule" framing. No speculative generality, no dead
  code, no scope creep.
- The developer's own choice to correct the item-16 design.md claim as well
  (even though that specific button, `.new-project-row button`, never
  actually shipped with the buggy `#fff` value) is consistent with the
  spec's literal wording ("the two (at least) known-wrong contrast claims
  for this pairing," not "the one tied to the shipped bug") and independently
  verified as the right call -- leaving it uncorrected would have left a
  second wrong number for a future design pass to copy.

## Follow-ups (non-blocking)
- The real-tmux session-race flake documented in commit `dfac08c` still
  reproduces occasionally under full-suite concurrent runs (this session:
  1 failure in 2 full runs) despite that commit's fix. Not this cycle's
  scope (no `teams.py`/tmux code touched here), but worth a dedicated
  look given it's now been observed failing in at least two separate
  review sessions.

## Overall verdict
**Approve.** All three acceptance criteria in `docs/spec.md` are
implemented and independently verified this session: the shipped CSS
change was confirmed byte-for-byte via `git diff`, the 8.51:1 contrast
figure was recomputed from raw hex values using the WCAG relative-luminance
formula (not trusted from the developer's claim) and matched to three
decimal places, the old failing ~2.2:1 figure was independently confirmed
as well, both `docs/design.md` corrections were checked against the same
independently-computed numbers and are accurate, no other `.team-btn`/
`.deploy-btn` state or background variant exists that would make `#111`
text fail elsewhere, and no test hardcodes the old color. The one
Python-suite failure encountered was investigated (isolated re-run, full
file re-run, full-suite re-run) and traced to a known, already-documented,
pre-existing real-tmux session-race flake unrelated to this diff, not a
regression -- filed as a non-blocking follow-up rather than a blocker. No
must-fix or should-fix issues found in the diff itself.
