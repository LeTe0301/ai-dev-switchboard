# Spec: Backlog item 19 part 1 — interject a free-form message into a running team (backend)

## Summary
Give a human a third lever on a live team run — alongside answering a
pending `ask_user`/`board_write` and stopping the team outright — to inject
an unsolicited free-text message to the **lead** while it keeps running,
delivered on the same round-boundary checkpoint the existing `cancel_event`
mechanism already uses, recorded through a new per-run `human.jsonl` file
merged into the existing `GET .../team/events` feed exactly like a
teammate's own log is today. This is backend + CLI only — the chat-bubble
UI (a compose box, "human" as a feed identity/filter pill) is a separate,
sequential part 2 feature cycle once this lands (see "Non-goals").

## Goals
- A new `teams.interject(run_id, text) -> dict` function, following
  `resolve_ask_user()`'s exact `{"ok": True, ...}` /
  `{"ok": False, "error": ...}` shape, that queues a human message for a
  run without touching `run.json` from the calling (request) thread.
- The lead loop (`team_step()`) drains any queued messages as the very
  first thing it does each round, before calling the lead at all — cheaper
  than the existing `cancel_event` checkpoint, and delivered to the lead as
  the round history's own `last_entry` (full text shown), reusing
  `_round_context()` unchanged.
- `POST /projects/<name>/team/interject` (web route) and `team-interject`
  (CLI subcommand), both thin wrappers, mirroring `/team/resolve` /
  `team-resolve`'s existing shape.
- The message appears in `GET .../team/events` tagged `"agent": "human"`,
  `"kind": "message"` — a kind already rendered generically by the existing
  frontend (`teamFeedEventBody()`'s `e.kind === 'message'` branch,
  `app/app.py`) even before part 2's dedicated styling lands.
- Never races with, or is silently dropped by, the background driving
  thread's own `_persist(state)` calls — the concrete correctness concern
  the backlog flags, resolved architecturally (see "Proposed approach"),
  not by adding locking around the existing single-writer `run.json`.

## Non-goals
- **Chat-bubble UI** (compose box, dedicated "human" filter pill, bubble
  styling/alignment) — part 2, a separate feature cycle once this backend
  lands. `docs/BACKLOG.md`'s own "purely visual restyling... or a
  differently-shaped event envelope?" question is answered here: **no new
  envelope shape** — `kind="message"`/`agent="human"` reuses 6f's existing
  `{ts, agent, seq, kind, text, meta}` shape and cursor-polling mechanism
  verbatim (see "Proposed approach").
- **Messaging a specific teammate directly**, bypassing the lead. The
  four-tool lead loop has no concept of an inbound message at all today,
  delegation is a synchronous blocking call the lead itself initiates
  (`agent_run()` in `team_step()`'s `delegate` branch), and none of the
  roster's engines are wired for genuine two-way mid-turn stdio here (6d's
  own research doc noted Claude Code's `--input-format stream-json` COULD
  support this, but nothing in this codebase uses it). Every interjection
  goes to the lead only, exactly like `ask_user`/`board_write` are
  lead-only concepts. A future increment could revisit direct-to-teammate
  messaging; not attempted here.
- **True mid-tool-call interruption.** "Interrupt at any point" is
  delivered at the same granularity `cancel_event` (stop) already uses
  today — the top of `team_step()`, before the lead is called, and (for
  `cancel_event` specifically, unchanged by this spec) also right after the
  lead call returns and right after a `delegate` call returns. There is no
  mechanism to inject text into an **in-flight** `agent_run()` subprocess
  (a delegate call already underway, or the lead's own call already in
  flight) — a message posted mid-delegate sits queued and is delivered at
  the very next round boundary, after that delegate call finishes. See
  "Open questions" for why this is the deliberate scope, not a shortfall.
- Editing/withdrawing an already-queued-but-not-yet-delivered interjection.
- Any change to `ask_user`/`board_write`/`stop_team()`'s own existing
  behavior.

## Background / current state
- `app/teams.py`'s lead loop (`team_run()` → `team_step()`) has exactly
  four/six tools the LEAD can call (`_LEAD_TOOL_NAMES`,
  `app/teams.py:1900`); there is no tool or mechanism for the human to push
  something INTO the loop except answering a pending `ask_user`
  (`resolve_ask_user()`, `app/teams.py:4144`) or `board_write`
  (`resolve_board_write()`, `app/teams.py:4244`) — both of which only apply
  when the run is already `blocked_*` (the driving thread has already
  exited `team_run()`'s loop and returned). Both share one hardened
  race-safety shape, evolved across three real, found-live races (see that
  function's own docstring): state is always reloaded fresh from disk,
  `os.replace()` on `inbox.json`/`inbox.resolved.json` is the sole atomic
  arbiter of "who won," and history/transcript are only appended AFTER that
  replace succeeds.
- The one other human lever, `stop_team()` (`app/teams.py:3814`), is
  unconditional and terminal — it doesn't feed anything back into the
  loop, it ends the run.
- `cancel_event` (a `threading.Event`, backlog item 6d part 2a) is the
  existing precedent for "the human did something, and a live background
  driving thread needs to notice it soon, safely." It's checked at three
  points: top of `team_run()`'s loop, right after `_call_lead()` returns
  inside `team_step()`, and right after a `delegate` branch's own
  `agent_run()` call returns — see `team_step()`'s own docstring
  (`app/teams.py:2910`) for why those three points and not others. It sets
  status, appends one history entry, and persists — a genuine stop, not a
  message.
- `_run_team_in_background()` (`app/app.py:1813`) spawns one daemon thread
  per live run, keyed by project name in `_team_threads` (guarded by
  `_team_threads_lock`). That thread holds its own **in-memory** `state`
  dict across an entire round — which, for a `delegate` round, can be
  minutes long (a real `agent_run()` call) — and calls `_persist(state)`
  (a full `run.json` overwrite, `app/teams.py:2703`) only at round
  boundaries. `mark_run_error()`'s own docstring (`app/teams.py:3881`)
  states this project's already-accepted tradeoff plainly: "no lock on
  `run.json`... whichever writer's `_persist()` call lands last wins."
  **This is exactly why a naive "human posts → load state → append to
  `state['history']` → persist" implementation of interject would be
  unsafe**: the request thread's freshly-loaded-and-persisted state would
  very likely be clobbered by the driving thread's own next round-end
  `_persist(state)` call, silently dropping the interjection — the
  concrete race the backlog explicitly calls out as needing the same
  hardening discipline as `resolve_ask_user()`'s own history.
- `GET .../team/events` (`app/app.py:4944`, `_handle_team_events`) already
  merges multiple per-identity `.jsonl` files by building a `files` list —
  `[("lead", transcript_path)] + [(m, agent_log_path(run_id, m)) for m in
  members]` — cursor-polling each independently via
  `tail_jsonl_events()` (`app/teams.py:4081`, byte-offset based, holds a
  partial trailing line across polls, never loses/duplicates a line) and
  merging + sorting the result by `(ts, agent, seq)`. Each event's `agent`
  field comes from what was written into the file, not the file list
  itself — `_append_transcript()` (`app/teams.py:2733`) always stamps
  `"agent": "lead"`.
- `_round_context()` (`app/teams.py:2326`) builds the lead's per-round
  prompt from `state["history"]`: one-line summaries of every prior round,
  plus the FULL `full_result_text` of only the single most recent entry
  (`last_entry`) — capped at `TEAM_DELEGATE_RESULT_MAX_CHARS`. This is the
  exact mechanism `resolve_ask_user()`'s own `ask_user_resolved` entry and
  `resolve_board_write()`'s own `board_write_resolved` entry already ride
  to surface a human-originated event to the lead — neither needed any new
  prompt-building code, they just append an ordinary `_append_history()`
  entry with a non-`None` `tool`.

## Proposed approach

### 1. A dedicated append-only file, not a `run.json` field
Add `_human_log_path(run_id)` (`app/teams.py`, next to `_transcript_path`)
→ `<run_dir>/human.jsonl`. `teams.interject(run_id, text)`:
1. `_load_state(run_id)` (catch `FileNotFoundError` →
   `{"ok": False, "error": f"no such run_id: {run_id}"}`, matching
   `resolve_ask_user()`'s wording convention).
2. Reject if `state["status"]` is terminal (`finished`, `error`,
   `escalated_max_rounds`, `stopped`) →
   `{"ok": False, "error": f"run {run_id} is not accepting messages (status={state['status']})"}`.
   Allowed for `running`, `blocked_ask_user`, AND `blocked_board_write` —
   a message posted while blocked simply sits until the next `team_step()`
   round runs (see "Edge cases").
3. Append ONE envelope — `{"ts": _now_iso(), "agent": "human", "seq":
   <computed the same way `_next_transcript_seq()` does, but scoped to
   `human.jsonl`>, "kind": "message", "text": text, "meta": {}}` — via
   `os.makedirs` + `open(path, "a")` + one `f.write(json.dumps(envelope) +
   "\n")` call, the same idiom `_append_transcript()` already uses.
4. Return `{"ok": True, "run_id": run_id}`.

This function **never calls `_persist(state)`** and never mutates
`state["history"]` — it is a read-only check plus an append to a file
`team_step()`'s own driving thread does not otherwise touch during a round.
This is the whole fix for the race described in "Background": there is no
shared mutable `run.json` write from the request thread at all, so there is
nothing for the driving thread's own end-of-round `_persist()` to clobber.
Multiple concurrent human posts (two tabs) are safe because each is its own
independent `open(...,"a")` + one bounded `write()` call — capped well
under Linux's 4 KiB `PIPE_BUF` by `TEAM_INTERJECT_MAX_CHARS` (below), so
each append is atomic with respect to the others; there is no shared
in-memory buffer to race on the way there would be for a `run.json` field.

### 2. Draining, at the top of `team_step()`, before the lead is ever called
Add `state["human_cursor"]` (int byte offset into `human.jsonl`, default
`0`) to `_new_state()`'s dict — additive, same "missing key defaults to
0/None/{}" precedent `worktrees`/`project_name` already established for
runs persisted before this field existed
(`state.get("human_cursor", 0)` wherever read).

At the very top of `team_step()` (before `round_n`/`system`/
`round_context` are computed — earlier than the existing `cancel_event`
checkpoint, since this one can save an LLM call entirely, matching
`team_run()`'s own "cheapest possible checkpoint" rationale for its
max-rounds check):
```python
new_events, new_cursor, _ = tail_jsonl_events(
    _human_log_path(state["run_id"]), state.get("human_cursor", 0),
    TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND, agent="human")
if new_events:
    for ev in new_events:
        round_n = len(state["history"]) + 1
        text = ev.get("text") or ""
        _append_history(state, round_n, tool="human_interject",
                        args_summary=f'human_interject("{text[:60]}")',
                        outcome_summary=f"human said: {text[:80]}",
                        full_result_text=text, log_path=None,
                        transcript_entries=[])
    state["human_cursor"] = new_cursor
    _persist(state)
    return state
```
`transcript_entries=[]` deliberately — the message is already durably
recorded in `human.jsonl` itself (which `GET .../team/events` merges in
directly, see part 4 below), so no second copy is written to
`transcript.jsonl`. Reusing `tail_jsonl_events()` (already proven:
byte-capped, holds a partial trailing line, never loses/duplicates a line
across polls) instead of a bespoke reader is a deliberate reuse, not a new
parsing path. A new constant, `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND`
(default `65536`, matching `TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL`'s own
"round, conservative default" precedent) bounds one drain call the same
way that constant bounds one poll.

This makes the very next queued interjection the round's `last_entry` once
`team_step()` returns and loops back around — `_round_context()` shows its
`full_result_text` in full under "Most recent result (round N,
human_interject):" with **zero changes to `_round_context()` itself**. If
several messages queued up since the last round (rare, but possible if the
human posts twice while a `delegate` call is in flight), each becomes its
own history round in order; only the last one gets the full-text
treatment, the earlier ones get the same one-line "Round history" summary
any other round does — the same degrade-gracefully behavior a flurry of
any other round type already has, not a new special case.

### 3. Making the lead's next action responsive to a `human_interject` round
Add a new required-verbatim mitigation clause, `_INTERJECT_MITIGATION`, to
`_system_framing()` (next to `_FACT_CHECK_MITIGATION`/
`_DELEGATION_HISTORY_MITIGATION`/`_BOARD_WRITE_MITIGATION`, same "make the
thing the model should notice explicit and salient, don't leave it to be
inferred" discipline all three already establish):
> "A round history entry with tool `human_interject` is an unsolicited
> message the human sent you WHILE you were working — it is not a reply to
> a question you asked (that would be `ask_user_resolved`), and it does not
> mean your current plan is wrong. Read it, and let it inform what you do
> next: it may be new information, a correction, a change of priority, or
> a request to stop and explain your progress. If it changes what you
> should be doing, adjust; if it doesn't, briefly acknowledge it (in your
> reasoning, not a tool call) and continue."

### 4. Wiring into `GET .../team/events`
In `_handle_team_events()` (`app/app.py:4944`), extend the `files` list by
exactly one entry:
```python
files = [("lead", teams._transcript_path(run_id)), ("human", teams._human_log_path(run_id))]
files += [(m, teams._agent_log_path(run_id, m)) for m in state.get("members", [])]
```
No other change to that function — the existing per-file cursor,
byte-cap, truncation-flag, and chronological-merge-sort logic is generic
over the file list already and needs nothing agent-specific added.

### 5. The route and CLI wrappers
`POST /projects/<name>/team/interject` (`app/app.py`, alongside
`/team/resolve`/`/team/board-resolve`, same shared TOTP gate, no new
gating code):
- `run_id = (body.get("run_id") or "").strip() or None`; if given, validate
  against `teams._RUN_ID_RE` BEFORE any load/path-join (backlog item 11(b)
  — the same settled, non-optional intake-point hardening `/team/resolve`
  and `/team/board-resolve` already apply, mirrored here byte-for-byte:
  reject → `{"error": "no run found for this project"}`, 400), then
  `teams._load_state(run_id)` and check `state.get("project_name") ==
  name` (cross-project ownership check, same as the two existing routes).
  If no `run_id` given, `teams.latest_run_for_project(name)`.
- **Length/emptiness validation happens at THIS route layer**, not inside
  `teams.interject()` — mirroring `/team/resolve`'s own
  `TEAM_ASK_USER_ANSWER_MAX_CHARS` check (`app/app.py:5290`) exactly:
  `text = (body.get("text") or "").strip()`; if empty or
  `len(text) > teams.TEAM_INTERJECT_MAX_CHARS`, 400 with
  `f"message must be non-empty and at most {teams.TEAM_INTERJECT_MAX_CHARS} characters"`.
- Calls `teams.interject(run_id, text)`; `{"error": result["error"]}`, 400
  on failure, else `{"ok": True, "run_id": run_id}`.
- **Does NOT spin up a background thread.** Unlike `/team/resolve`/
  `/team/board-resolve` (which resume a loop that has already exited),
  `interject` while `status == "running"` expects a driving thread to
  already be alive (it will pick the message up on its own next round);
  while `blocked_ask_user`/`blocked_board_write`, the message simply waits
  in `human.jsonl` for whichever future resolve action restarts the driving
  thread. Starting a second thread here would violate the existing
  "at most one live driving thread per run" invariant `/team/resolve`
  already asserts defensively (`_team_threads_get(name) is not None` check,
  `app/app.py:5305`) for no benefit.

New constant, `app/teams.py` (next to `TEAM_ASK_USER_ANSWER_MAX_CHARS`,
same file region/style):
```python
# Max length of the free-text `text` POST /team/interject accepts -- a
# short unsolicited human message injected into a RUNNING lead's next round,
# a materially different action from TEAM_ASK_USER_ANSWER_MAX_CHARS's own
# "answer to a question the lead itself asked" even though both are short
# human free text (same "materially different case, kept a separate,
# independently tunable constant" precedent TEAM_BOARD_WRITE_VALUE_MAX_CHARS
# already set against TEAM_ASK_USER_ANSWER_MAX_CHARS). Same 2000-char
# default -- no evidence yet that this needs a different budget.
TEAM_INTERJECT_MAX_CHARS = int(os.environ.get("TEAM_INTERJECT_MAX_CHARS", "2000"))
```

CLI: `team-interject <run_id> <text>` subcommand
(`p_team_interject = sub.add_parser("team-interject", ...)`, alongside
`team-resolve`/`team-board-resolve`), `_cli_team_interject(args)`:
```python
def _cli_team_interject(args: argparse.Namespace) -> int:
    result = interject(args.run_id, args.text)
    if not result["ok"]:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    print(f"queued for run {result['run_id']}")
    return 0
```
Deliberately does **not** call `_drive_and_report()` the way
`_cli_team_resolve()`/`_cli_team_board_resolve()` do — those resume a loop
that had already stopped; `team-interject` never starts or resumes driving
anything (there may already be a live driver, in this process or another
`team-start`/`team-resume` invocation, and double-driving one run_id is
exactly the bug class `/team/resolve`'s own defensive `_team_threads_get`
check exists to prevent at the web layer). It queues and returns,
full stop, matching the design intent: posting a message is not the same
action as resuming a stopped run.

## Affected areas
- `app/teams.py`: `_human_log_path()` (new), `TEAM_INTERJECT_MAX_CHARS`
  (new constant), `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND` (new constant),
  `_new_state()` (add `"human_cursor": 0`), `interject()` (new function,
  next to `resolve_ask_user()`/`resolve_board_write()`), `team_step()`
  (drain check at the top), `_system_framing()` (`_INTERJECT_MITIGATION`
  clause, added to `parts` alongside the other three mitigations, every
  tier), `_cli_team_interject()` (new) + `team-interject` subparser wiring
  in the CLI's `main()`/argparse setup.
- `app/app.py`: `_handle_team_events()` (`files` list, one new tuple),
  `POST /projects/<name>/team/interject` (new route branch in `do_POST`,
  alongside `/team/resolve`/`/team/board-resolve`).
- `config/switchboard.env.example`: document `TEAM_INTERJECT_MAX_CHARS`
  and `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND` alongside the existing
  `TEAM_*` entries, matching how every other `os.environ.get(...)`-backed
  constant here is already documented.
- No data-model change to `run.json`'s existing fields beyond the one
  additive `human_cursor` key; no change to `inbox.json`'s shape.

## Edge cases
- **Interjecting into a `blocked_ask_user`/`blocked_board_write` run**:
  allowed (see "Proposed approach" step 1); delivered as the FIRST
  drained round once a human resolve action restarts the driving thread.
  Order is preserved correctly regardless of wall-clock ordering between
  the interject and the resolve, because both are only ever "applied" (an
  `_append_history()` call) at the moment each is actually processed by
  code that already runs sequentially on one thread — `resolve_ask_user()`
  appends its own `ask_user_resolved` entry synchronously before returning,
  and the resumed `team_run()` loop's very first `team_step()` call drains
  `human.jsonl` before doing anything else, so a message queued before the
  resolve always appears in history AFTER the resolution entry, never
  reordered ahead of it.
- **Interjecting into a terminal run** (`finished`/`error`/
  `escalated_max_rounds`/`stopped`): rejected with a clear error; nothing
  is written to `human.jsonl`.
- **A message posted in the exact instant the run naturally exhausts
  `max_rounds`**: `team_run()`'s own loop checks
  `len(state["history"]) >= state["max_rounds"]` BEFORE calling
  `team_step()` again — so a message written to `human.jsonl` a moment
  after that check already passed (and the loop is about to call
  `_force_ask_user(..., status="escalated_max_rounds")`, a TERMINAL,
  non-resumable-via-`/team/resolve` status) is never drained and is
  effectively stranded. Narrow (needs a post to land inside a small window
  right as a run is already about to escalate on rounds) and not
  data-destructive (the message is still sitting in `human.jsonl`, just
  never delivered to a live lead) — accepted as a documented, narrow
  tradeoff for this increment rather than restructured around, matching
  this project's own precedent for similarly narrow poll/round-boundary
  races (e.g. backlog item 12's part C).
- **Two humans post concurrently (two tabs)**: each is an independent file
  append under `TEAM_INTERJECT_MAX_CHARS`, safely below `PIPE_BUF` — both
  land, in whatever order the OS serializes the two `write()` calls, no
  data loss. `seq` is computed the same "count existing lines" way
  `_next_transcript_seq()` already does for the lead's own file, so a
  genuinely simultaneous pair could in principle compute the same `seq`
  once (same class of narrow, accepted race `_next_transcript_seq()`
  itself already has for the lead's transcript today) — cosmetic only
  (`seq` is a display/sort tiebreaker, `ts` plus insertion order in the
  merged feed is still correct), not a data-loss or misdelivery risk.
- **Empty/whitespace-only message**: rejected at the route layer (400),
  never reaches `teams.interject()`.
- **A run with no live driving thread anywhere** (e.g. the process
  restarted and nothing has resumed this run yet) but `status == "running"`
  on disk: this is `sweep_dead_teams()`'s own existing orphan-detection
  territory (crash/reboot with the tmux session gone → marked `error` on
  the next sweep pass) — unrelated to and unaffected by this change; an
  interjection posted in that narrow window is queued exactly as normal
  and, if the run is later found orphaned and marked `error`, becomes
  permanently undelivered — the same "terminal status, never drained"
  outcome as any other terminal-status case above.

## Acceptance criteria
- [ ] Given a run with `status == "running"`, when `teams.interject(run_id,
      "some text")` is called, then it returns `{"ok": True, "run_id":
      run_id}` and a new line is appended to that run's `human.jsonl`
      shaped `{"ts": ..., "agent": "human", "seq": ..., "kind": "message",
      "text": "some text", "meta": {}}`, and `run.json` itself is
      byte-for-byte unchanged by this call (no `_persist()` call reachable
      from `interject()`).
- [ ] Given a run with `status in ("finished", "error",
      "escalated_max_rounds", "stopped")`, when `teams.interject()` is
      called, then it returns `{"ok": False, "error": ...}` naming the
      run's actual status, and `human.jsonl` is not written to.
- [ ] Given an unknown `run_id`, when `teams.interject()` is called, then
      it returns `{"ok": False, "error": "no such run_id: <run_id>"}`.
- [ ] Given a run with one message already queued in `human.jsonl` (not
      yet drained) and `status == "running"`, when `team_step()` is next
      called, then it appends exactly one `human_interject` history entry
      (not a lead call) whose `full_result_text` equals the queued
      message, advances `state["human_cursor"]` past that message, and
      returns without calling `_call_lead()` — verified via a test that
      injects a message directly into `human.jsonl` between two
      `team_step()` calls against a mocked/fake lead and asserts the lead
      adapter was NOT invoked on the round that drained it.
- [ ] Given a queued message drained in round N, when `team_step()` is
      called again for round N+1, then `_round_context()`'s "Most recent
      result" section contains the message's full text under
      `(round N, human_interject)`.
- [ ] Given two messages queued in `human.jsonl` before a single drain,
      when `team_step()` drains them, then two separate `human_interject`
      history entries are appended, in file order, and `human_cursor`
      advances past both in that same `team_step()` call.
- [ ] `GET /projects/<name>/team/events?run_id=<id>&cursor=<cursor
      including "human">` returns the human message event, tagged
      `"agent": "human"`, `"kind": "message"`, once posted — verified via
      a real `POST .../team/interject` followed by a real `GET
      .../team/events` against a live-but-mocked run (no real lead/teammate
      subprocess needed for this assertion).
- [ ] `POST /projects/<name>/team/interject` with `run_id` omitted resolves
      to `latest_run_for_project(name)`, matching `/team/resolve`'s own
      documented default-run behavior.
- [ ] `POST /projects/<name>/team/interject` with a `run_id` belonging to a
      DIFFERENT project returns 400 ("this run belongs to a different
      project"), matching `/team/resolve`'s existing cross-project check.
- [ ] `POST /projects/<name>/team/interject` with a syntactically invalid
      `run_id` (e.g. containing `../`) is rejected against `_RUN_ID_RE`
      before any file access, returning the same 400 shape
      `/team/resolve` already returns for that case (regression test
      mirroring the existing item-11(b) coverage for the other two
      routes).
- [ ] `POST .../team/interject` with an empty or all-whitespace `text`, or
      `text` longer than `TEAM_INTERJECT_MAX_CHARS`, returns 400 and
      `teams.interject()` is never called (verify via a test double / call
      count, matching how `/team/resolve`'s own length check is tested
      today).
- [ ] `team-interject <run_id> "<text>"` (CLI): on success, prints
      `queued for run <run_id>` and exits 0, WITHOUT driving the run (no
      lead call happens as a side effect of this command) — verified by
      asserting the mocked lead adapter's call count is unchanged after
      the CLI call.
- [ ] Every one of the four required-verbatim mitigation clauses,
      including the new `_INTERJECT_MITIGATION`, is present in
      `_system_framing()`'s output for all three tiers (extend the
      existing per-tier framing test the same way `_BOARD_WRITE_MITIGATION`
      was covered).

## Open questions
- **Trust direction (settled, not left open):** unlike items 7/8 (agent →
  external system writes, both gated behind human approval), this is
  human → agent: the human's own message, injected into the LEAD's own
  next-round context, with no external side effect triggered by the act of
  sending it. The lead can act on it, ignore it, or ask_user for
  clarification — it has exactly the same status as `ask_user`'s own
  answer text already has today (immediate, no separate approval gate).
  Proceeding on this basis; no propose-then-approve step is added.
- **Lead-only, not teammate-direct (settled, not left open):** see
  "Non-goals" — the four/six-tool loop and `agent_run()`'s synchronous call
  shape don't support addressing a specific in-flight teammate today; doing
  so would need genuinely new plumbing (bidirectional stdio to an
  already-running `agent_run()` call) this spec does not attempt.
- **Round-boundary delivery, not true mid-call interruption (settled, not
  left open):** see "Non-goals" — matches the granularity `cancel_event`
  already uses; a message posted mid-delegate is delivered at the next
  round boundary, after that delegate call completes, not injected into
  the live subprocess.
- **`TEAM_INTERJECT_MAX_CHARS` kept as its own constant, not reusing
  `TEAM_ASK_USER_ANSWER_MAX_CHARS`** — same value today (2000), but
  independently tunable later, matching the
  `TEAM_BOARD_WRITE_VALUE_MAX_CHARS`-vs-`TEAM_ASK_USER_ANSWER_MAX_CHARS`
  precedent of keeping similarly-shaped-but-semantically-different human
  text fields on separate constants. Flagging as a judgment call, not a
  blocker — trivial to consolidate later if it never diverges.
- **Part 2 (chat UI)** is intentionally not specced here (see "Non-goals")
  — a fresh product-manager pass once this part is reviewer-approved,
  following the same sequential-parts precedent items 6d, 6f, and 7 already
  used, per skill 11 (load-balanced decomposition).

## Risk / rollback notes
- Purely additive: one new file per run (`human.jsonl`, absent = no
  events, same as any other missing per-agent log `tail_jsonl_events()`
  already tolerates via its own `FileNotFoundError → ([], offset, False)`
  path), one new optional `run.json` key defaulted on read, one new
  route, one new CLI subcommand, one new required prompt clause. No
  existing route, CLI subcommand, tool, or status transition changes
  behavior for a run that never receives an interjection.
- Rollback is deleting the new route/CLI branch and the drain check at the
  top of `team_step()`; no migration needed since `human_cursor`'s absence
  already defaults to `0` and no other code depends on it existing.
- Worst-case failure mode if the drain logic has a bug: a `human_interject`
  history entry could be malformed or duplicated, visible in
  `team-status`/the events feed as a clearly-labeled anomaly, not a crash
  or data loss elsewhere in the run — same "degrade to a visible artifact
  in the feed, not a hard failure" posture backlog item 11's own
  stale-transcript-entry finding was judged by.
