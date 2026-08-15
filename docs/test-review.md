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

# Test & Review: Backlog item 14 -- `install.sh --update`/`--upgrade`, an update path for an already-installed box

## Scope
Covers all ten acceptance criteria in `docs/spec.md`: flag parsing/synonyms,
the guarded fetch+ff-only-merge update-pull (dirty/branch-mismatch/
divergence/not-a-repo refusals), the guarded restart (defers while any
`RUN_USER` tmux session is live, never destructive, no `--force`), the
`RUN_USER`/`SVC_USER` default-value bug fix, and non-interference with
plain (non-`--update`) invocations and `--with-*` combinations. Branch:
`backlog/install-update-14`. Changes: `install.sh`, `README.md`,
`docs/ARCHITECTURE.md`, `docs/BACKLOG.md`, new `tests/test_install_update.py`
(20 tests). Nothing committed by the developer; nothing committed by this
review.

The developer disclosed running `git checkout -- install.sh` mid-session
(reverting a sabotage-test edit but taking the whole file with it) and
redoing all `install.sh` edits from scratch. Per the dispatch instructions,
this was independently re-verified rather than trusted: the current
`install.sh` was read end-to-end for every changed section (flag parsing,
the hoisted `ENV_FILE`/update-pull block, the `RUN_USER`/`SVC_USER` default
fix, the guarded-restart block) and matches `docs/spec.md`'s "Proposed
approach" essentially verbatim. No leftover sabotage markers, `TODO`s, or
stray diff artifacts found (`grep` for `sabotage|SABOTAGE|XXX|TODO|FIXME`
in `install.sh`/the new test file: no hits). `bash -n install.sh`: OK.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Clean checkout behind origin, no live sessions -> fast-forward + restart | automated (`UpdatePullBlockTests.test_clean_behind_origin_fast_forwards`, `GuardedRestartBlockTests.test_no_live_sessions_restarts_service`, both against the real extracted block) | pass | `python3 -m unittest tests.test_install_update -v` -- both tests ok; app.py/teams.py re-copy itself is pre-existing, unconditional `cp` (unchanged by this diff, confirmed present at install.sh:284-285 and separately asserted by `tests/test_team_routes.py:2601`) -- not independently re-tested end-to-end this cycle (see Spec coverage) |
| 2 | Live session present -> pull still happens, restart never invoked, stderr names session(s) + retry/override instructions | automated (`GuardedRestartBlockTests.test_live_session_defers_restart_names_it_in_stderr`, `test_multiple_live_sessions_all_named`) | pass | asserts `systemctl.invoked` log file never created; stderr contains session name(s), `$INSTALL_DIR`, and `sudo systemctl restart ai-dev-switchboard`; `$REPO_DIR` update itself covered separately by case 1's pull tests (independent code path, runs unconditionally before `RUN_USER` is even resolved) |
| 3 | Dirty `$REPO_DIR` -> exits non-zero before fetching/touching state, names dirty state | automated (`test_dirty_working_copy_refused_before_fetch`) **and independently re-run by hand** against the real (non-extracted) current `install.sh` source in a fresh scratch git repo | pass | test asserts `refs/remotes/origin/main`... no fetch occurred and HEAD unchanged; manual repro: `git status --porcelain` dirty file, ran the literal block extracted fresh from today's `install.sh` -- `ERROR: ... has uncommitted local changes`, exit 1, `git log` on repo unchanged (`4f1d7b7 one`), working tree still shows the uncommitted edit (`M f.txt`) |
| 4 | Branch mismatch / detached HEAD -> exits non-zero before fetching, names mismatch | automated (`test_wrong_branch_checked_out_refused_before_fetch`, `test_detached_head_refused_before_fetch`) | pass | stderr contains `REPO_BRANCH` and branch name / `detached HEAD` |
| 5 | Real divergence -> exits non-zero after fetch, before merge, names divergence | automated (`test_diverged_local_branch_refused_after_fetch_before_merge`) | pass | asserts fetch DID happen (`refs/remotes/origin/main` == origin's real HEAD) but HEAD/working tree still only has the local-only commit, not origin's new file |
| 6 | Not a git repo -> exits non-zero immediately, actionable message | automated (`test_not_a_git_repo_refused_immediately`) | pass | stderr: "not a git checkout" |
| 7 | `--update`/`--upgrade` exact synonyms | automated (`FlagAndDocTests.test_flag_defaults_to_off_and_both_spellings_wired_up`, source-level: single `case` arm `--update\|--upgrade) UPDATE=1 ;;`) | pass | unambiguous bash case-pattern match, sufficient given both spellings set literally the same flag with no branching difference anywhere else in the script |
| 8 | `RUN_USER`/`SVC_USER` preserved (not reset to `dev`/`switchboard-svc`) on non-interactive re-run with existing config | automated (`RunUserSvcUserDefaultTests.test_non_interactive_rerun_preserves_already_configured_run_user`) **and independently re-verified as non-tautological**: reverted the fix (`RUN_USER_DEFAULT="dev"`), re-ran the test -- failed exactly as expected (`'RUN_USER=someuser' not found in ...RUN_USER=dev...`); restored via a pre-saved file copy (deliberately not `git checkout --`, to avoid repeating the developer's own disclosed mistake), confirmed byte-identical restore and all 20 tests green again | pass | see command transcript below |
| 9 | Plain invocation (no `--update`/`--upgrade`) unaffected | automated (`UpdatePullBlockTests.test_update_flag_off_is_a_noop`) plus direct source read (guarded-restart block is a single `if [ "$UPDATE" -eq 1 ]` gate, no other code path changed) | pass | update-pull block emits nothing and exits 0 when `UPDATE=0`; guarded-restart block's gating condition read directly from install.sh:567 -- not independently exercised via `GuardedRestartBlockTests` with `UPDATE=0` (minor gap, see Findings) |
| 10 | `--update` + `--with-taiga` (or any `--with-*`) uses the freshly-pulled checkout, no stale artifacts | manual code read only, no combined automated test (developer-disclosed limitation) | pass (by construction, independently confirmed) | read install.sh top to bottom: update-pull block (lines 120-148) runs before `-- App + engines --` (line 283) and every `--with-*` block (all >= line 579), all of which read from `$REPO_DIR` -- the freshly-pulled checkout is what every later step sees, with no special-casing needed |
| 11 (safety-critical, not a spec AC by number but the spec's core justification) | Does `systemctl restart` on this unit shape (no `KillMode` set) actually take down the entire `RUN_USER` tmux server, not just the switchboard's own process? | **real empirical test** -- built a throwaway systemd unit matching `install.sh`'s generated shape verbatim (`Type=simple`, no `KillMode`), spawned a tmux session as a second throwaway user via `sudo -u <user> tmux new-session -d` from inside the service's own process (mirroring `app.py`'s `TMUX = ["sudo", "-u", RUN_USER, "/usr/bin/tmux"]`), confirmed via `systemctl status` that the tmux server + its child landed in the service's own cgroup, then `systemctl restart`ed and checked | **confirmed true** | before restart: `sudo -u runtestlm tmux list-sessions` -> session present, created at a fixed timestamp. After `systemctl restart killmode-test`: `sudo -u runtestlm tmux list-sessions` -> **"no server running on /tmp/tmux-1003/default"** -- the entire tmux server for that user was killed, not just the one session's client. Journal confirms: `Stopping...`/`Deactivated successfully`/`Started...` bracket the restart, and the post-restart service's own idempotent `tmux has-session` check reports no server at all. Test environment (users, sudoers rule, unit file, `/opt` fakeapp) fully torn down afterward -- confirmed via `id svctestlm`/`id runtestlm` (no such user) and `systemctl list-units \| grep killmode` (none) |

## Regression check
- `python3 -m unittest tests.test_install_update -v` -- 20/20 pass.
- `python3 -m unittest discover -s tests` (full suite) -- **1054 tests, OK**
  (took ~150s; no failures, no errors, no skips beyond the project's own
  existing privileged-test gating).
- `python3 -m unittest tests.test_install_ollama tests.test_install_set_env tests.test_deploy_target.WrapperBranchingTests tests.test_deploy_target.RestartValidationTests tests.test_deploy_target.InstallShTemplateTests -v`
  (the adjacent install.sh-touching suites the developer flagged) -- 40/40
  pass, independently re-run this session (not just re-reading the
  developer's own claimed output).
- `bash -n install.sh` -- syntax OK.

## Spec coverage
| Acceptance criterion (docs/spec.md) | Implemented? | Tested? | Gap? |
|---|---|---|---|
| 1. Clean+no-sessions -> ff + re-copy + restart | Yes | Yes, at block level (pull + restart tested separately); the re-copy step itself is pre-existing/unconditional code, unmodified by this diff | No full single-run E2E (explicitly disclosed by developer) -- acceptable given the re-copy line's unconditional nature and pre-existing coverage via `test_team_routes.py` |
| 2. Live session -> update but no restart, stderr names it | Yes | Yes, at block level | Same "no combined E2E" gap as #1, same reasoning |
| 3. Dirty repo -> refuse before fetch | Yes | Yes, automated + independently hand-verified against the real current file this session | None |
| 4. Branch mismatch/detached -> refuse before fetch | Yes | Yes | None |
| 5. Divergence -> refuse after fetch, before merge | Yes | Yes, including proof the fetch happened | None |
| 6. Not a git repo -> refuse immediately | Yes | Yes | None |
| 7. `--update`/`--upgrade` synonyms | Yes | Yes | None |
| 8. `RUN_USER`/`SVC_USER` default fix | Yes | Yes, automated + independently re-verified as non-tautological this session | None |
| 9. Plain invocation unaffected | Yes | Yes for the update-pull block; guarded-restart block's `UPDATE=0` no-op path is correct by direct source read (single `if` gate) but has no dedicated automated test | Minor test-coverage gap, not a functional gap (see Findings #1) |
| 10. `--update` + `--with-*` combined | Yes | No automated combined test; verified correct by construction via direct source read this session | Disclosed limitation, independently confirmed low-risk |

All ten acceptance criteria are implemented; nine are directly tested
(automated and/or independently hand-verified this session), one (#10) is
verified correct by direct code reading rather than an executable test.
Both disclosed gaps (#1/#2's missing full E2E, #10's missing combined test)
were independently re-derived from the actual current source rather than
taken on the developer's word, and hold up.

## Findings (most severe first)

### 1. `GuardedRestartBlockTests` has no explicit `UPDATE=0` case -- nit
- File: `tests/test_install_update.py` (`GuardedRestartBlockTests`, all four
  test methods hardcode `UPDATE=1` inside `_build_restart_block_harness`'s
  generated script)
- Issue: nothing in this test class exercises the restart-guard block with
  `UPDATE=0` to prove it's a true no-op on a plain invocation. The guarantee
  currently rests on a direct read of `install.sh:567` (`if [ "$UPDATE" -eq
  1 ]; then`) plus `UpdatePullBlockTests.test_update_flag_off_is_a_noop`
  covering the *other* half (the pull block) of the same flag.
- Failure scenario: none realistic today -- the gating condition is a single
  `if` with no other logic outside it, so there's no plausible code change
  that would break this without also breaking the already-tested `UPDATE=1`
  paths. Flagging only because the acceptance criterion ("no behavior
  change to any existing flag") deserves a same-shape explicit test to
  match this file's own stated rigor elsewhere, not because of any observed
  or suspected defect.

### 2. `docs/implementation.md`'s "Known limitations" claim about update-pull ordering is inaccurate -- nit
- File: `docs/implementation.md` line ~2040 ("the update-pull section runs
  before `CONFIG_DIR` is even set, let alone any `--with-*` block")
- Issue: reading the actual current `install.sh`, `CONFIG_DIR`/`INSTALL_DIR`/
  `STATE_DIR` are set and `mkdir -p`'d at line 110-113, *before* the
  update-pull block (line 120-148) runs -- not after, as this sentence
  claims. The developer's own diff summary made the same claim for the
  spec's "before CONFIG_DIR=..." placement, but the actual placement (driven
  by needing `CONFIG_DIR` already set so `ENV_FILE` could be hoisted for the
  `RUN_USER`/`SVC_USER` fix) put the mkdir line first.
- Failure scenario: none -- `mkdir -p` on plain, constant paths is
  idempotent and doesn't read from `$REPO_DIR`, so the property the sentence
  is actually trying to establish ("update-pull runs before anything reads
  from `$REPO_DIR`") still holds and was independently re-confirmed by
  direct source read this session. This is a doc-accuracy nit, not a
  functional issue.

## Follow-ups (non-blocking)
- `docs/ARCHITECTURE.md`'s new section states the `KillMode=control-group`
  blast-radius claim is "inferred... not re-confirmed against a live box in
  this repo's own test suite." That's no longer true as of this review --
  see test case #11 above, which empirically confirmed it against a real
  systemd unit matching this project's generated shape. Worth a follow-up
  doc edit (in `docs/ARCHITECTURE.md` and/or `docs/implementation.md`'s
  "Known limitations") to change "inferred, not yet confirmed" to
  "confirmed" and point at this review, so the next reader doesn't have to
  redo the empirical check.
- Consider the real underlying fix (`systemd-run --scope`/`Delegate=yes` +
  explicit cgroup move for spawned tmux sessions) as a separate, larger
  backlog item now that the blast radius is empirically confirmed rather
  than just suspected -- explicitly out of scope for this spec, not a
  blocker here.

## Overall verdict
**Approve.** All ten acceptance criteria in `docs/spec.md` are implemented;
the current `install.sh` was read end-to-end and independently confirmed to
match the spec's proposed approach (ruling out the disclosed
`git checkout --`-during-redo mistake having left the file in a bad state).
`tests/test_install_update.py`'s 20 tests all pass, the full existing suite
(1054 tests) has no regressions, and this session independently re-derived
(not just re-read) three of the developer's own strongest claims: the
dirty-working-copy refusal (hand repro against a fresh scratch git repo and
the real current source), the RUN_USER-default regression test's
non-tautology (reverted the fix, watched it fail, restored via file copy
rather than `git checkout --`), and — the one item both the product-manager
and developer explicitly flagged as unverified — the spec's core safety
claim, empirically confirmed via a real systemd unit + real tmux server on
this sandbox's genuine systemd (PID 1): a restart with no `KillMode` set
does kill the entire `RUN_USER` tmux server, not just the switchboard's own
process, validating the whole guarded-restart design this feature is built
around. Two nit-level findings (a missing `UPDATE=0` test case in
`GuardedRestartBlockTests`, and a doc-accuracy slip about mkdir/update-pull
ordering in `docs/implementation.md`) -- neither blocks approval, both
optional to pick up in a future cycle.

# Test & Review: Backlog item 17 part 1 -- unprivileged per-project origin detection + GitHub REST API client (backend-only)

## Scope
`docs/spec.md`'s 14 acceptance criteria for item 17 part 1: (1) origin
classification (`_project_origin_url`/`_classify_origin_url`/
`detect_project_origin` -- local/github/external/none), (2) a GitHub REST
client (`_github_api`/`_github_api_raw`) mirroring `_gitea_api`/
`_gitea_api_raw`'s exact contract, (3) a global in-memory rate-limit
cooldown gate driven by `X-RateLimit-Remaining`/`X-RateLimit-Reset`/
`Retry-After`, (4) four read+write convenience functions
(`github_list_open_prs`, `github_pr_diff`, `github_list_branches`,
`github_post_pr_comment`), and (5) confirmation that this cycle is inert
(no route, no poll-loop wiring, no item 8 integration). Per this session's
recorded scope decision (`docs/BACKLOG.md` item 17, `docs/spec.md`
"Settled scope decision"), `github_post_pr_comment()`'s lack of a
propose-then-approve gate is deliberate, authorized design, not reviewed
as a finding here.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Loopback origin (`127.0.0.1`, any port) -> `kind: "local"` | automated | pass | `tests/test_github_api.py::OriginDetectionTests::test_loopback_https_form_classifies_local`, `test_loopback_any_port_classifies_local` |
| 2 | `https://github.com/owner/repo.git` -> `github`, owner/repo, `.git` stripped | automated | pass | `test_github_https_form` |
| 3 | `git@github.com:owner/repo.git` (scp-shorthand) -> same as HTTPS | automated | pass | `test_github_scp_shorthand_form_matches_https` |
| 4 | `ssh://git@github.com/owner/repo.git` -> same as HTTPS | automated | pass | `test_github_ssh_scheme_form_matches_https` |
| 5 | No `origin` / not a git repo -> `kind: "none"`, never raises | automated | pass | `test_detect_project_origin_no_remote_returns_none_kind`, `test_detect_project_origin_never_raises_for_not_a_repo` |
| 6 | Non-loopback, non-github host -> `external`, owner/repo `None` | automated | pass | `test_non_loopback_non_github_host_classifies_external` |
| 7 | Case-insensitive `github.com` match | automated | pass | `test_github_host_case_insensitive` |
| 8 | `_github_api`/`_github_api_raw`: 200 -> parsed body; HTTPError never raises; only URLError/TimeoutError -> `ConnectionError` | automated | pass | `GithubApiTests.test_success_sends_expected_request_and_parses_json`, `test_http_error_with_json_body_returns_status_and_parsed_body_never_raises`, `test_connection_failure_raises_connection_error`, `test_timeout_raises_connection_error`, `test_raw_http_error_returns_status_and_text_never_raises` |
| 9 | Every request includes `Authorization: Bearer`, non-empty `User-Agent`, `X-GitHub-Api-Version` | automated | pass | `test_success_sends_expected_request_and_parses_json` (asserts on captured `Request` headers) |
| 10 | `X-RateLimit-Remaining: 0` + `X-RateLimit-Reset` epoch -> short-circuits (429, zero `urlopen` calls) before epoch, proceeds after | automated | pass | `test_rate_limit_remaining_zero_trips_cooldown_short_circuits_next_call` (asserts `urlopen` call count) |
| 11 | `403` + `Retry-After: 30` -> same short-circuit-then-recover | automated | pass | `test_retry_after_header_trips_cooldown_short_circuits_next_call` |
| 12 | Convenience functions build correct method+path(+body), return `{"ok": True, ...}` | automated | pass | `GithubConvenienceTests.test_list_open_prs_builds_correct_call_and_shape`, `test_pr_diff_builds_correct_call_and_shape`, `test_list_branches_builds_correct_call_and_shape`, `test_post_pr_comment_builds_correct_call_and_body` |
| 13 | `GITHUB_TOKEN` unset -> `{"ok": False, ...}`, zero calls to `_github_api`/`_github_api_raw` | automated | pass | `test_missing_token_short_circuits_every_convenience_function_no_client_call` (fakes raise `AssertionError` if called) |
| 14a | No new Flask route / HTML/JS template change | manual structural check | pass | `git diff app/app.py \| grep -i "Flask\|@app.route\|def do_GET\|def do_POST"` -> no output; `git diff --stat -- 'app/templates/*' 'app/static/*'` -> no output |
| 14b | `git -C PROJECTS_DIR/<name> remote get-url origin` runs unprivileged, no `sudo`/`RUN_USER` hand-off | automated + manual read | pass | `test_project_origin_url_runs_git_remote_get_url_unprivileged` asserts `"sudo" not in cmd`; direct read of `_project_origin_url()` source confirms the only subprocess call is the bare `git -C ...` argv, no `sudo -u` anywhere in the new code |
| 15 | Inert this cycle -- no call site to any new function outside the new test file | manual grep | pass | `grep -rn` for all 10 new function/constant names across `*.py/*.sh/*.html/*.js`, excluding `tests/test_github_api.py` and `docs/` -> only definitions in `app/app.py`, no call sites |
| 16 | Adversarial host classification (beyond dev's own test list) -- lookalike hosts, IPv6, decimal/octal loopback forms, `localhost`, unusual scp forms | manual (own script, `_classify_origin_url` called directly) | pass | See "Adversarial classification testing" below |
| 17 | Token never logged/surfaced in an error message; safe, clear error on unset token for a write call | manual read + grep | pass | `grep -n "GITHUB_TOKEN" app/app.py` -- only appears in the `os.environ.get` default, the `Authorization` header f-string (sent, never returned/logged), and `if not GITHUB_TOKEN` checks; `_github_token_missing_error()` returns a fixed string with no token interpolation |
| 18 | Cooldown never lowered by a stale/racing update | automated | pass | `test_cooldown_never_lowered_by_a_later_smaller_signal` |
| 19 | Malformed/non-numeric rate-limit headers tolerated, fall back to `GITHUB_RATE_LIMIT_FALLBACK_SECONDS` | automated | pass | `test_malformed_rate_limit_reset_falls_back_to_default_cooldown` |

### Adversarial classification testing (item 1 in the reviewer's task, done independently of the developer's own test list)
Ran `_classify_origin_url()` directly against a battery of adversarial
inputs not in `tests/test_github_api.py`:
- Lookalike hosts: `github.com.evil.example`, `not-github.com`,
  `github.com@evil.example` (userinfo-confusion attempt),
  `evil.example/github.com/owner/repo` (path-confusion attempt),
  `xn--github-com` (punycode-lookalike attempt) -- **all correctly
  classify as `"external"`**, never `"github"`.
- Loopback lookalikes/alternate representations: `127.0.0.1.evil.example`
  (subdomain trick), `0177.0.0.1` (octal), `2130706433` (decimal), `[::1]evil`
  (malformed bracket), bare `localhost` (hostname, not a literal IP) --
  **all correctly classify as `"external"`, never `"local"`**. This is the
  safe direction (Python's `ipaddress.ip_address()` only parses literal IP
  address strings, so anything it can't parse falls through to
  `"external"`, not `"local"`) -- being conservative about granting
  `"local"` trust status is the right failure mode, since `"local"` is the
  higher-trust classification.
- Legitimate variants confirmed still correct: `GITHUB.COM` case variants,
  explicit port (`github.com:443`), embedded userinfo
  (`user:pass@github.com/...`), scp-shorthand loopback
  (`user@127.0.0.1:owner/repo.git` -> `"local"`, correctly, via the
  scp-fallback path).
- No case produced a misclassification in the security-relevant direction
  (a hostile/external host being classified as `"local"` or `"github"`) or
  an unhandled exception -- confirms the spec's "Wrap the whole thing in a
  bare `try/except Exception`" requirement and the "never misidentifies a
  hostile host" property this review was specifically asked to verify.

### Rate-limit gate trace (item 2 in the reviewer's task)
- **`Retry-After` parsing**: `int(retry_after)` only -- handles GitHub's
  real, documented behavior (GitHub's REST API always sends `Retry-After`
  as an integer number of seconds, never an HTTP-date; unlike the generic
  HTTP spec, GitHub's own docs specify seconds-only). A non-numeric value
  (or, hypothetically, an HTTP-date string from some intermediary) falls
  through to `except (TypeError, ValueError)` -> the
  `GITHUB_RATE_LIMIT_FALLBACK_SECONDS` default rather than crashing or
  silently not backing off -- confirmed by
  `test_malformed_rate_limit_reset_falls_back_to_default_cooldown`
  (exercises the `X-RateLimit-Reset` fallback path; the `Retry-After`
  fallback path uses the identical `except` pattern, read directly in
  `app/app.py:1017-1021`).
- **Monotonic-forward-only**: `_github_note_rate_limit()` computes `until`
  then only assigns `_github_rate_limited_until = until` if
  `until > _github_rate_limited_until`, under `_github_rate_limit_lock` --
  confirmed both by reading the code and by
  `test_cooldown_never_lowered_by_a_later_smaller_signal` (pre-seeds a
  longer cooldown, sends a response that would compute a shorter one,
  asserts the longer value survives).
- **Gate consulted before the request, not just recorded after**:
  `_github_api`/`_github_api_raw` both call `_github_rate_limited()` as
  their first line, before building the `Request` object -- confirmed by
  reading the code (`app/app.py:1054`, `1083`) and by
  `test_rate_limit_remaining_zero_trips_cooldown_short_circuits_next_call`/
  `test_retry_after_header_trips_cooldown_short_circuits_next_call`, both
  of which assert `urlopen`'s call count stays at 1 across a short-circuited
  second call -- i.e. genuinely zero HTTP calls made while cooling down,
  not merely a note-after-the-fact.
- **Global, not per-repo**: a single module-level
  `_github_rate_limited_until` guarded by one `_github_rate_limit_lock`,
  matching `docs/spec.md`'s explicit design call (GitHub's limit is
  per-token, shared across every repo).

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` --
**1094 tests, `OK`, exit code 0** (includes this cycle's 40 new tests in
`tests/test_github_api.py`; no failures, no errors, no regressions in
`tests/test_gitea.py`/`tests/test_gitea_poll.py`/`tests/test_ai_reviewer.py`
or any other existing suite). Also ran `tests/test_github_api.py` in
isolation (40/40 pass) and `python3 -m py_compile app/app.py` (clean).

No defects found in the testing pass -- proceeding to the review pass.

## Spec coverage
All 14 acceptance criteria in `docs/spec.md` are implemented and covered
by an automated test, a manual structural check, or both (see test-case
table above, criteria 1-15 map onto the spec's checkbox list; criteria
16-19 are this review's own additional verification beyond the spec's
minimum list). No gaps.

- **"No poll-loop wiring / no UI / no item 8 integration"** (non-goals) --
  confirmed: `git diff app/app.py` contains zero changes to
  `_ai_reviewer_poll_repo`/`_ai_reviewer_review_run`/any `@app.route`, and
  a full-repo grep for every new function/constant name (excluding the new
  test file and `docs/`) finds zero call sites. The feature really is
  inert in a running install, as claimed.
- **Settled scope decision** (`github_post_pr_comment` posts directly, no
  propose-then-approve gate) -- confirmed present and correctly worded in
  both `docs/spec.md` and the already-committed `docs/BACKLOG.md` item 17
  ("Scope decision -- settled" block). Per this review's task framing,
  not flagged as a finding.

## Findings (most severe first)
None must-fix. None should-fix.

### 1. Config comment adds an extra sentence beyond `docs/spec.md`'s proposed text -- nit
- File: `config/switchboard.env.example:172-174`
- Issue: the developer appended "Purely additive... setting it has no
  visible effect until a later part wires these functions in" to the
  `GITHUB_TOKEN` comment block, which isn't in `docs/spec.md`'s literal
  proposed comment text (§2 "GitHub API client shape").
- Failure scenario: none -- it's accurate, harmless, arguably useful
  context for an operator wondering why setting the token does nothing
  yet. Purely a documentation addition, not a scope or behavior change.
  Not worth a follow-up.

## Follow-ups (non-blocking)
- None. Part 2 (poll-loop wiring, `GITHUB_POLL_INTERVAL_SECONDS`, item 8
  host-agnostic dispatch) is already correctly scoped out and recorded in
  `docs/spec.md`/`docs/BACKLOG.md` item 17 as future work, not something
  this review needs to re-flag.

## Overall verdict
**Approve.** All 14 acceptance criteria are implemented and tested; the
full existing suite (1094 tests) passes with no regressions; this
review's own independent adversarial testing of `_classify_origin_url()`
(lookalike GitHub hostnames, decimal/octal/subdomain loopback tricks,
`localhost`) found no case where a hostile or unintended host is
misclassified as `"local"` or `"github"` -- the single highest-risk
property this review was asked to verify, given item 16's prior
three-round injection-fix history in adjacent code. The rate-limit gate
was traced end-to-end (not just spot-checked): it is consulted before
every request (zero-`urlopen`-calls proof, not just a header-recording
side effect), never lowers an active cooldown, and degrades safely on
malformed headers. `GITHUB_TOKEN` is never logged or surfaced in an error
message. This cycle is confirmed genuinely inert in a running install (no
route, no call sites, no template changes). One documentation-only nit
(an extra, harmless sentence in the config comment) does not block
approval.

---

# Test & Review: Backlog item 18 -- HTTP-level "Smoke check" button

## Scope
All 12 acceptance criteria in `docs/spec.md` for the HTTP-level smoke
check: button visibility gated on `inst.url`, the GET-with-optional-
substring-check contract (`smoke_check_run()`), the `POST
/projects/<name>/smoke-check` route's 200/404/409 contract, timeout and
connection-refused handling, per-project lock contention/independence,
ephemeral (never-persisted) result rendering, no new `install.sh` step,
and the `.smoke-btn` WCAG AA contrast claim. Branch:
`backlog/gstack-capabilities-18`. Nothing committed by the developer;
nothing committed by this review. Also independently investigates a
regression the developer flagged in `docs/implementation.md` in
`tests/test_deploy_frontend.js`, attributed to backlog item 13 and
disclosed as pre-existing/unrelated to this cycle's own diff.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Button renders when `inst.url` is non-null | automated | pass | `tests/test_smoke_check_frontend.js`: "project with a captured url renders a Smoke check button + input + empty message slot" |
| 2 | Button does not render when `url` is null | automated | pass | `test_smoke_check_frontend.js`: "project without a captured url renders no Smoke check button at all" |
| 3 | Empty field, 200 within timeout -> shows status code + elapsed ms, no content verdict | automated | pass | `test_smoke_check_frontend.js`: "a successful check with no expect_contains shows status + timing, no content verdict"; `tests/test_smoke_check.py::SmokeCheckRunTests::test_success_reports_status_code_and_elapsed_ms_no_content_check` |
| 4 | Substring present -> positive content-match indication + status/timing | automated | pass | `test_smoke_check_frontend.js`: "...substring IS present shows a positive content match"; `test_smoke_check.py::test_expect_contains_present_reports_content_ok_true` |
| 5 | Substring not present -> negative indication, status/timing still shown separately (not collapsed) | automated | pass | `test_smoke_check_frontend.js`: "...substring is NOT present still shows the real status/timing"; `test_smoke_check.py::test_expect_contains_absent_reports_content_ok_false_alongside_real_status` |
| 6 | Target unreachable (connection refused) -> route responds HTTP 200, `ok: false`, human-readable error, never 500 | automated | pass | `test_smoke_check.py::SmokeCheckRunTests::test_connection_refused_returns_clean_failure_not_raise`, `SmokeCheckEndpointTests::test_smoke_check_post_target_side_failure_still_returns_http_200`; frontend "an unreachable target..." case |
| 6b | Connection-refused path, independently reproduced (not just re-reading the dev's test) | manual, own script | pass | See "Independent timeout/connection-refused verification" below |
| 7 | Target doesn't respond within `SMOKE_CHECK_TIMEOUT_SECONDS` -> returns within approx that bound, timeout error reported, thread not hung | automated | pass | `test_smoke_check.py::test_timeout_returns_within_roughly_the_configured_bound` (real listening socket, accepts but never writes) |
| 7b | Timeout path, independently reproduced with my own raw socket (not the dev's test harness) | manual, own script | pass | See "Independent timeout/connection-refused verification" below |
| 8 | Concurrent same-project clicks -> second gets 409 immediately, no second in-flight request | automated | pass | `test_smoke_check.py::SmokeCheckRunTests::test_concurrent_dispatch_for_same_project_reports_locked`, `SmokeCheckEndpointTests::test_smoke_check_post_lock_contention_surfaces_as_409` (asserts `"locked"` key never reaches the client payload); frontend "a second dispatch already in flight (409)..." |
| 9 | Concurrent different-project clicks -> fully independent, no shared lock | automated | pass | `test_smoke_check.py::SmokeCheckRunTests::test_different_projects_do_not_block_each_other`, `SmokeCheckLockTests::test_different_projects_do_not_share_a_lock` |
| 10 | Result is ephemeral -- gone on next page refresh / 4s poll re-render | structural + automated (partial) | pass | `smokeCheckRow()` unconditionally renders a fresh empty `.smoke-check-msg` div on every call (read directly, `app/app.py` diff); no persisted-state file added (`git diff --stat` confirms no new file). Not covered by a dedicated "fill message, call refresh(), assert cleared" test, but this exact gap exists identically in the precedent file `tests/test_deploy_frontend.js` for `.deploy-msg` -- consistent with established project convention, not a new gap this cycle introduces. See finding 3 below (nit). |
| 11 | No new `apt-get`/Docker/binary-download step in `install.sh` | manual diff check | pass | `git diff --stat install.sh` -> no output (zero lines changed) |
| 12 | `.smoke-btn` color pairing passes real WCAG AA contrast (>=4.5:1), independently computed | manual, own script | pass | See "Independent contrast recomputation" below -- **7.386:1**, matches the developer's claimed 7.39:1 |
| 13 | Unknown project name in route path -> 404 | automated | pass | `test_smoke_check.py::SmokeCheckEndpointTests::test_smoke_check_post_unknown_project_returns_404` |
| 14 | No captured URL -> clean `ok: false` dict, lock never touched, no 500 | automated | pass | `test_smoke_check.py::test_no_captured_url_returns_ok_false_without_touching_the_lock` |
| 15 | Non-UTF-8 body decoded `errors="ignore"`, never raises | automated | pass | `test_smoke_check.py::test_non_utf8_body_does_not_raise_and_is_decoded_with_errors_ignored` |
| 16 | Body capped at `SMOKE_CHECK_MAX_BODY_BYTES`; substring match past the cap correctly NOT found | automated | pass | `test_smoke_check.py::test_response_body_truncated_at_max_body_bytes_cap` |
| 17 | Target 4xx/5xx is a completed check (real status code), not a mechanism failure | automated | pass | `test_smoke_check.py::test_target_http_error_status_is_a_completed_check_not_a_failure`, `test_target_5xx_is_also_a_completed_check` |
| 18 | No `confirm()` dialog on smoke-check click (unlike Deploy) | automated | pass | `test_smoke_check_frontend.js`: "clicking Smoke check dispatches immediately with no confirm() dialog" |
| 19 | `expect_contains` text survives a `refresh()` re-render | automated | pass | `test_smoke_check_frontend.js`: "typed expect_contains text survives a refresh() re-render" |
| 20 | 428 mid-dispatch (TOTP code overlay) retried correctly through shared `toggle()`/`handleActionResult()` plumbing | automated | pass | `test_smoke_check_frontend.js`: "a 428 mid-dispatch shows the code overlay labeled for this smoke check, and a correct retry succeeds" |
| 21 | SSRF: no user-supplied URL ever reaches the target -- only server-side `_session_urls[name]`, keyed by a validated project name | manual code read | pass | See "SSRF / read-only verification" below |
| 22 | Smoke check is genuinely read-only against the target (GET, no body/method override) | manual code read | pass | Same section below |

### Independent timeout/connection-refused verification
Ran my own standalone script against the real `app.smoke_check_run()`
(not the developer's test file/harness), using a raw listening socket
that `accept()`s but never writes a byte back:
```
result: {'ok': False, 'status_code': None, 'elapsed_ms': 2024, 'error': 'timed out after 2s'}
wall elapsed (s): 2.0245713079930283
```
with `SMOKE_CHECK_TIMEOUT_SECONDS` set to 2 -- the request returned in
~2.02s, not hung, with the correct error message. Also independently
reproduced the connection-refused path against a closed port
(`http://127.0.0.1:1/`):
```
connection-refused result: {'ok': False, 'status_code': None, 'elapsed_ms': 0, 'error': 'connection refused'}
```
Both match the dict contract `docs/spec.md` specifies. This confirms the
spec's own "Risk / rollback notes" concern (a swallowed timeout causing a
hung request thread) does not manifest.

### Independent contrast recomputation
Given this project's history of a wrong contrast claim on the adjacent
`.deploy-btn`/`.team-btn` pairing (backlog item 20), recomputed WCAG
relative-luminance contrast from the literal hex values in the diff
(`#4da6ff` background / `#111111` text) using the standard sRGB->linear
formula, independently of the developer's own figure:
```
background: #4da6ff text: #111111 -> contrast ratio: 7.386
```
Matches the claimed **7.39:1** (rounding) and comfortably passes AA's
4.5:1 minimum for normal text. `.smoke-btn` is confirmed as its own CSS
class, not a reuse of the still-separately-tracked `.deploy-btn`/
`.team-btn` pairing.

### SSRF / read-only verification
Read `smoke_check_run()` and the new route directly:
- The route (`app/app.py`, new `elif parts[0] == "projects" ... parts[2]
  == "smoke-check"` branch) passes only `name` (from the URL path,
  validated against `instance_names()`) and `expect_contains` (from the
  JSON body, used only for a substring check) into `smoke_check_run()`.
  **No URL of any kind is ever accepted from the client.**
- `smoke_check_run()` itself resolves the target exclusively via
  `_session_urls.get(name)` -- the same server-populated map `/status`'s
  own `url` field already reads (per `docs/spec.md` "Background"). A
  malicious client cannot point a smoke check at an arbitrary external
  host by any parameter this route accepts.
- `urllib.request.urlopen(url, timeout=...)` is called with no `data=`
  argument and no method override, so it is a plain GET; the response is
  only read (`resp.read(...)`), never written to. Genuinely read-only
  against the target.

## Regression check
Full targeted suite run as the developer's own "How to verify locally"
section specifies, executed directly by this review (not re-read from the
developer's report):
```
python3 -m unittest tests.test_smoke_check tests.test_deploy_dispatch tests.test_ai_reviewer tests.test_upload -v
# Ran 180 tests ... OK
python3 -m py_compile app/app.py
# OK (no output)
node tests/test_team_frontend.js               # ALL PASS (94/94)
node tests/test_singleton_toggle_frontend.js    # ALL PASS (15/15)
node tests/test_clone_frontend.js               # ALL PASS (8/8)
node tests/test_upload_frontend.js              # ALL PASS (8/8)
```
This cycle's own new suites, run directly:
```
python3 -m unittest tests.test_smoke_check -v
# Ran 25 tests ... OK
node tests/test_smoke_check_frontend.js
# ALL PASS (10/10)
```
All match the developer's claimed counts exactly.

### Pre-existing `tests/test_deploy_frontend.js` regression -- independently confirmed pre-existing, not caused by this cycle
Ran `node tests/test_deploy_frontend.js` against the working tree as-is:
**4/9 FAIL** (the same four cases the developer's `docs/implementation.md`
names: "clicking Deploy then cancelling the confirm() dialog...", "a
quote-containing host/service value...", "confirmed deploy that
succeeds...", "a 428 mid-dispatch..." -- all failing on an unexpected
extra pending fetch, consistent with `renderTeamBranches()`'s
unconditional `/projects/<name>/team/branches` call landing in backlog
item 13).

To verify this genuinely predates this cycle's diff rather than being
caused or worsened by it: `git stash push -u` on every file this cycle
touches or adds (`app/app.py`, `config/switchboard.env.example`,
`docs/BACKLOG.md`, `docs/implementation.md`, `docs/spec.md`,
`tests/test_smoke_check.py`, `tests/test_smoke_check_frontend.js`),
leaving the tree at exactly the last committed state
(`6008134`, "Implement item 17 part 1..." -- the tip of this branch
before any of this cycle's changes), then re-ran
`node tests/test_deploy_frontend.js` against that state: **identical 4/9
failure**, same four test names, same assertion messages. Confirmed via
`git diff app/app.py | grep -n "renderTeamBranches\|team/branches"` that
this cycle's own diff touches neither `renderTeamBranches()` nor the
`team/branches` route at all. Popped the stash back afterward (clean,
`git status` verified). **This is genuinely pre-existing and unrelated to
backlog item 18's diff** -- the developer's disclosure is accurate, not a
convenient excuse.

**Recommendation: file this as its own small, separate fix-and-PR, not
bundled into item 18's commit.** Reasoning:
- It is a different backlog item's bug (item 13, already shipped and
  merged as PR #8) surfacing in a different feature's test file
  (`test_deploy_frontend.js`, not `test_smoke_check_frontend.js`) --
  fixing it inside item 18's diff would mix two unrelated features' fixes
  into one commit/PR, working against this pipeline's own minimal-diff/
  scope-discipline convention.
- It is genuinely mechanical: either drain the `team/branches` fetch in
  `test_deploy_frontend.js`'s own setup the same way this cycle's new
  `test_smoke_check_frontend.js::setupCase()` already does (a technique
  now proven twice), or gate `renderTeamBranches()`'s call behind
  something the test can control. No design or product decision is
  needed -- this can skip a full product-manager dispatch per this
  project's own "mechanical repeats" token-efficiency rule and go
  straight to a developer cycle with a minimal spec.
- It should not block item 18's approval: item 18's own new frontend
  suite (`test_smoke_check_frontend.js`) is unaffected (it drains the
  fetch itself), and item 18 introduces zero lines touching
  `renderTeamBranches()`/`team/branches`.
- Concretely: open `backlog/deploy-frontend-test-regression-13b` (or
  similar), spec = "drain `team/branches` in
  `test_deploy_frontend.js`'s setup, matching
  `test_smoke_check_frontend.js::setupCase()`'s already-proven technique,"
  run the full suite, commit, done in one small cycle.

No defects found in the testing pass -- proceeding to the review pass.

## Spec coverage
All 12 checkbox acceptance criteria in `docs/spec.md` are implemented and
covered by an automated test, an independently-reproduced manual check,
or both (see test-case table above; criteria 13-22 are this review's own
additional verification of edge cases/non-goals/security properties
beyond the spec's checkbox minimum). No gaps. The two disclosed
deviations (internal `"locked"` dict key; `"<code> · <ms>ms"` display text
omitting the literal word "OK") are both confirmed to still satisfy their
mapped acceptance criteria exactly as written -- neither changes any
client-visible dict-key contract or the underlying HTTP status-code
mapping the spec requires, and the "OK" omission maps to a criterion
("displays the status code and an elapsed time in milliseconds") that
never requires the literal reason phrase.

## Findings (most severe first)
None must-fix. None should-fix (beyond the separately-tracked, pre-
existing `test_deploy_frontend.js` regression above, which is correctly
scoped as its own follow-up, not a finding against this cycle's diff).

### 1. `.smoke-check-msg` "gone on refresh" has no dedicated regression test -- nit
- File: `tests/test_smoke_check_frontend.js` (no test covers this
  specific sequence: fill a result message in, call `refresh()`, assert
  the message is cleared)
- Issue: the behavior is structurally guaranteed (`smokeCheckRow()`
  always emits a fresh empty `.smoke-check-msg` div on every render call,
  confirmed by direct code read) but not exercised end-to-end by an
  automated test the way most other acceptance criteria are.
- Failure scenario: none currently -- this is a coverage gap, not a
  behavioral bug. If a future change accidentally made the message slot
  persist (e.g. conditionally preserving prior content), no test in this
  file would catch the regression. Note: `tests/test_deploy_frontend.js`
  has the identical gap for `.deploy-msg`, so this isn't a new lapse
  introduced by this cycle -- it's consistent with (not worse than)
  existing project convention. Not worth blocking on; a one-line addition
  to either file's `test_*_frontend.js` in a future pass would close it.

### 2. `expect_contains` has no server-side length cap -- nit
- File: `app/app.py`, new `smoke_check_run()` (the
  `expect_contains in body_text` substring check)
- Issue: the client enforces `maxlength="500"` on the `<input>`, but the
  server-side route only `.strip()`s `expect_contains` -- an operator (or
  anyone with a valid session) bypassing the browser could POST an
  arbitrarily large `expect_contains` string. `body_text` itself is
  already bounded by `SMOKE_CHECK_MAX_BODY_BYTES` (65536), so the
  substring search's cost is bounded on one side, and Python's `in`
  operator uses an efficient substring-search algorithm in practice.
- Failure scenario: not practically exploitable as a DoS given the
  already-required session auth (TOTP-gated) and the small bound on the
  other operand; flagged only because every other size-sensitive input in
  this codebase (`UPLOAD_MAX_BYTES`, `AI_REVIEWER_MAX_DIFF_BYTES`) has an
  explicit server-side cap and this one doesn't. Optional follow-up, not
  required for approval.

## Follow-ups (non-blocking)
- File `backlog/deploy-frontend-test-regression-13b` (or similar) to fix
  `tests/test_deploy_frontend.js`'s pre-existing 4/9 failure, using the
  `setupCase()`-drains-the-extra-fetch technique this cycle's own
  `test_smoke_check_frontend.js` already proves works. See "Pre-existing
  `tests/test_deploy_frontend.js` regression" above for the full
  recommendation and reasoning.
- Optional: add a server-side cap on `expect_contains` length (finding 2)
  and/or a dedicated "message clears on refresh" test (finding 1). Neither
  blocks approval.

## Overall verdict
**Approve.** All 12 acceptance criteria in `docs/spec.md` are implemented
and independently verified -- not just re-read from the developer's own
report. This review's own hands-on checks (not delegated to the
developer's test suite alone): the timeout path (a raw socket that
accepts but never responds) and the connection-refused path were each
reproduced from scratch against the real `smoke_check_run()`, confirming
the request thread returns within the configured bound with the correct
error rather than hanging; the `.smoke-btn` contrast ratio was
recomputed from the literal hex values (7.386:1, matching the claimed
7.39:1) given this project's prior history of a wrong contrast claim on
the adjacent `.deploy-btn`/`.team-btn` pairing; and the SSRF-shaped
concern the spec itself calls out was traced end-to-end through the route
and `smoke_check_run()` -- no client-supplied value of any kind can reach
the outbound URL, which is always the server's own trusted
`_session_urls[name]`. The two disclosed deviations (internal `"locked"`
marker; display text omitting the literal word "OK") are both confirmed
cosmetic/mechanism-only and don't change any acceptance criterion's
outcome. The pre-existing `tests/test_deploy_frontend.js` regression was
independently confirmed (via `git stash` back to this branch's base
commit) to genuinely predate this cycle's diff, not be caused or worsened
by it -- recommended as its own small, separate fix-and-PR (see above),
not bundled into this commit. Two nits (no dedicated "message clears on
refresh" test; no server-side cap on `expect_contains` length) do not
block approval.

# Test & Review: fix pre-existing `tests/test_deploy_frontend.js` regression from item 13

## Scope
The single acceptance criterion set in `docs/spec.md` (this cycle):
`tests/test_deploy_frontend.js`'s `setupCase()` now drains the extra,
unconditional `/projects/<name>/team/branches` fetch backlog item 13's
`renderTeamBranches()` fires as a side effect of any `kind='inst'` row
render, so all 9 of the file's own pre-existing test cases pass without
any existing assertion being loosened. Branch:
`backlog/deploy-frontend-test-fix-13b`. Nothing committed by the
developer; nothing committed by this review. This is a mechanical,
already-root-caused fix (root-caused by item 18's reviewer, this same
session, via `git stash` against this branch's base commit) mirroring
`tests/test_smoke_check_frontend.js::setupCase()`'s already-proven drain
technique verbatim -- no new product/design decision, per the
Entwicklung workflow's right-sizing rule 1.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | All 9 cases in `tests/test_deploy_frontend.js` pass | automated, run directly | pass | `node tests/test_deploy_frontend.js` -> `ALL PASS (9/9)`, all 9 individually printed `PASS` |
| 2 | No existing assertion loosened/removed to make this pass | manual diff read | pass | `git diff -- tests/test_deploy_frontend.js`: 15 insertions, 0 deletions -- every one of the file's pre-existing `assert.*` calls is byte-for-byte unchanged; the only new code is the drain loop plus two `tick()` calls inside `setupCase()` |
| 3 | No other test file regresses | automated, run directly | pass | `test_team_frontend.js` 94/94, `test_smoke_check_frontend.js` 10/10, `test_clone_frontend.js` 8/8, `test_singleton_toggle_frontend.js` 15/15, `test_upload_frontend.js` 8/8 -- all `ALL PASS` |
| 4 (edge case) | Drain loop must not error on a case with zero `instances` (no fetch was ever made to drain) | automated, own spot-check test appended temporarily to a copy of the file, then removed | pass | `setupCase([])` returns cleanly with `pendingFetches.length === 0`; the `.some((f) => f.url === url)` guard before each `resolveFetch()` call means an empty (or non-team-branches-triggering) `instances` array is a no-op, not a thrown "no matching pending fetch" error |
| 5 (root-cause confirmation) | The 4/9 pre-fix failure genuinely reproduces and matches the spec's diagnosis, not just trusted secondhand | automated, `git stash` to the pre-fix working tree and re-run | pass | Exactly the same 4 cases fail with the same assertion mismatches described in the spec's "Problem" section (`clicking Deploy then cancelling...`, the quote-containing-host case, `confirmed deploy that succeeds...`, the 428-mid-dispatch case) -- `git stash pop` restored the fix afterward |

## Regression check
Full sibling frontend suite run directly by this review (not re-read from
the developer's own "How to verify locally" claims):
```
node tests/test_deploy_frontend.js          -> ALL PASS (9/9)
node tests/test_team_frontend.js            -> ALL PASS (94/94)
node tests/test_smoke_check_frontend.js     -> ALL PASS (10/10)
node tests/test_clone_frontend.js           -> ALL PASS (8/8)
node tests/test_singleton_toggle_frontend.js -> ALL PASS (15/15)
node tests/test_upload_frontend.js          -> ALL PASS (8/8)
```
All six figures match the developer's `docs/implementation.md` "How to
verify locally" section exactly -- independently confirmed, not trusted.
No Python backend tests are relevant to this change (test-file-only diff,
zero production-code lines touched), so no `test_*.py` sweep was needed
beyond the frontend suites above.

No defects found in the testing pass -- proceeding to the review pass.

## Spec coverage
All 3 acceptance criteria in `docs/spec.md` are implemented and covered
by an automated test or a direct diff read (see test-case table above;
cases 4-5 are this review's own additional verification of the edge case
the dispatch prompt specifically asked about, plus an independent
reproduction of the pre-fix regression). No gaps.

## Findings (most severe first)
None must-fix. None should-fix. One nit.

### 1. Zero-`instances` drain path has no permanent regression test in the file itself -- nit
- File: `tests/test_deploy_frontend.js`, `setupCase()` (the new drain
  loop)
- Issue: this review manually verified `setupCase([])` doesn't error
  (test case 4 above), but that check was done via a temporary throwaway
  copy of the file and was not left behind as a permanent test case in
  `tests/test_deploy_frontend.js` itself. None of the file's real 9 cases
  exercise a zero-instance `setupCase()` call.
- Failure scenario: none currently -- the `.some(...)` guard makes this
  structurally safe regardless of `instances` content, and the identical
  pattern in the proven-good `test_smoke_check_frontend.js` precedent has
  the same property. Purely a "nice to have" coverage note, not a
  correctness concern; not worth blocking a mechanical, already-diagnosed
  fix over.

## Follow-ups (non-blocking)
- Optional: add a `setupCase([])` (or equivalently, a single non-team
  instance whose row never triggers the `team/branches` fetch) case to
  `tests/test_deploy_frontend.js` to permanently pin the zero-drain path
  (finding 1). Not required for approval.

## Overall verdict
**Approve.** All 3 acceptance criteria are implemented and independently
verified by running the actual test suite in this session, not by
trusting the developer's reported counts. The diff is exactly what the
spec asked for and nothing else: 15 added lines inside `setupCase()`,
zero existing assertions touched, zero production code touched. The
pre-fix regression was independently reproduced via `git stash` and
matches the spec's diagnosis exactly (same 4 cases, same assertion
mismatches). The full sibling frontend suite (135 cases across 5 files)
passes with no regression. The one edge case called out in the dispatch
prompt -- a test case with zero `instances` -- was spot-checked directly
against the real drain loop and confirmed safe via the existing
`.some(...)` guard, not merely inferred from reading the code. One nit
(no permanent regression test for the zero-instances path) does not block
approval.
