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
