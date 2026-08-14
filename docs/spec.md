# Spec: Overwatch feed + escalation inbox — part 1: backend API (sub-spec 6f part 1)

## Summary
Add the three read/write HTTP endpoints and one additive `/status` field
that a future overwatch-feed UI (part 2) needs: a bounded, cursor-based
"give me new events since last time" route merging a run's lead transcript
and every teammate's own event log into one chronological stream; a
lightweight "is there a pending question right now" route; and a route that
answers a blocked `ask_user` and resumes the lead loop off-thread. No
HTML/CSS/JS in this cycle — see `docs/story.md` §6f for why it's split.

## Goals
- `GET /projects/<name>/team/events` returns every normalized §4.1 event
  (`docs/story.md` §4.1) written so far for a project's team run — merged
  across the lead's own `transcript.jsonl` and each teammate's
  `agents/<agent>.jsonl` — in chronological order, and on a follow-up call
  with the previous response's cursors, returns *only* newly-appended
  events, never re-reading a file from its start.
- Each file's per-poll read is capped in bytes so a long-running, chatty
  team's feed stays responsive — never a whole-file read on every poll.
- `GET /projects/<name>/team/inbox` reports whether the project's current
  run is blocked on `ask_user` and, if so, the exact structured question
  (§4.5 shape: question/header/options/multi_select) a UI needs to render
  it — without the caller having to scan the merged event feed for the
  latest `ask_user` entry itself.
- `POST /projects/<name>/team/resolve` answers a pending `ask_user` (free
  text — the same field a UI's "Other" input and its option buttons both
  ultimately submit) and resumes the lead loop on a background thread,
  mirroring `/team/start`'s existing non-blocking discipline: the HTTP
  response returns immediately, the run continues in the background.
- `GET /status`'s per-project `team` object gains one new field,
  `waiting_on_you: bool` — true iff that project's latest run is exactly
  `blocked_ask_user` — so a future status strip can distinguish "needs your
  answer, resolvable" from "blocked" (`escalated_max_rounds`, terminal, no
  inbox to answer). Additive only; every existing `/status` field and value
  is byte-for-byte unchanged.
- `_cli_team_resolve`'s existing resolve-and-append logic is extracted into
  one shared `teams.py` function both the CLI and the new route call —
  not duplicated — so the two entry points can never drift.

## Non-goals
- No HTML/CSS/JS, no `docs/design.md` section, no ux-designer step. This
  cycle has no user-visible surface at all — it ships an API contract for
  part 2 to build against. (Same precedent as 6d part 1, which also had no
  design.md section — see `docs/design.md`'s first heading, "sub-spec 6d
  part 2a", i.e. part 1 never got one.)
- No merged-timeline rendering, colour-coding, per-agent filter UI, status
  strip UI, or escalation panel UI. That is 6f part 2, next in the story,
  built once this part's routes exist and are reviewer-approved.
- No change to the lead loop, its four tools, or any adapter tier (6c,
  unchanged) — `POST /team/resolve` only appends the human's answer and
  flips `status` back to `running`; the lead sees the answer in its next
  round's context exactly the way `_cli_team_resolve` already makes it see
  one today, unchanged.
- No change to grounding, roster, or the composition picker (6b/6e,
  unchanged).
- No websocket/SSE push. `/status` itself has always been polled, not
  pushed (4-second interval, `docs/design.md`'s existing "Status is polled,
  can be stale" note) — the new events/inbox routes are designed to be
  polled by part 2's frontend the same way, not a new transport.
- No general "browse any past run for this project" history UI. `run_id`
  is accepted as an *optional* override on the events/inbox routes solely
  to cover one specific edge case (an already-open tab keeps watching an
  older, now-finished run's history after a newer run starts for the same
  project) — not as a general run-picker. A real history browser is future
  work if the user wants one later.
- No retention/pruning of old run directories. Reading an arbitrarily-old
  finished run's full event history is in scope (reload rehydration);
  deleting it is `sweep_dead_teams()`'s existing, unchanged scope.
- No change to `install.sh`, `engines.d/*.engine`, or any config file
  format beyond two new `TEAM_*` env vars (see "Affected areas"). The
  `set_env()` sed-escaping bug (`docs/BACKLOG.md` item 10) is **not**
  touched here — this cycle never calls `install.sh`'s `set_env()`, and the
  bug's trigger surface (`--with-ollama`/`--with-deploy-target`-style
  operator-supplied values written by `install.sh`) is unrelated to this
  spec's config additions, which are plain `switchboard.env.example`
  entries with programmer-chosen defaults, never operator input threaded
  through `sed`. Stays parked in the backlog.

## Background / current state
- **Every event source this route needs already exists and is already
  normalized.** `agent_run()`'s `_Tailer` (`app/teams.py:630-768`) writes
  one `{ts, agent, seq, kind, text, meta}` envelope per line
  (`docs/story.md` §4.1) to each teammate's stable log path,
  `_agent_log_path(run_id, agent)` = `<run_dir>/agents/<agent>.jsonl`
  (`app/teams.py:3111`). The lead's own actions are logged the same way by
  `_append_transcript()` (`app/teams.py:2496-2506`) to
  `_transcript_path(run_id)` = `<run_dir>/transcript.jsonl`
  (`app/teams.py:2436`), `agent` always `"lead"`. Nothing merges these
  files today — nothing web-facing reads them at all.
- **A run's own member list is already in its persisted state.**
  `_load_state(run_id)` (`app/teams.py:2483`) returns `state["members"]`
  (list of agent names) and `state["lead"]` — exactly the set of log files
  to merge for that specific run, not the live roster (a run started
  earlier keeps its own composition even if `engines.d` changes later).
- **`latest_run_for_project(project_name)`** (`app/teams.py:3497-3533`)
  already finds a project's most-recently-updated run by scanning
  `_leads_root()` and matching `state["project_name"]`; used unchanged as
  the "no explicit `run_id` given" default for both new GET routes.
- **The inbox is already written and already has the exact §4.5 shape.**
  `_write_inbox()` (`app/teams.py:2521-2537`) persists
  `{question, header, options, multi_select}` to `_inbox_path(run_id)`
  whenever `team_step()` executes `ask_user` (line 2794) or
  `_force_ask_user()` escalates on a malformed-retry-budget exhaustion
  (line 2568); resolved inboxes move to `_inbox_resolved_path(run_id)`.
- **The resolve-and-resume logic already exists, CLI-only.**
  `_cli_team_resolve()` (`app/teams.py:3788-3809`) loads state, rejects if
  `status != "blocked_ask_user"`, appends an `ask_user_resolved` history
  entry, moves `inbox.json` → `inbox.resolved.json`, sets
  `status = "running"`, persists, then calls `_drive_and_report(state)`
  (line 3716) — which blocks the CLI process in the foreground driving
  `team_run()` to completion. There is no non-blocking equivalent and no
  HTTP route.
- **The non-blocking "start work, return fast, keep going on a daemon
  thread" pattern already exists** for exactly this shape of problem.
  `POST /team/start` (`app/app.py:3673-3724`) calls `launch_team()`
  synchronously (fast — no LLM call, just worktree/tmux setup), then spawns
  `_run_team_in_background(name, run_id, cancel_event)`
  (`app/app.py:1354-1385`) on a daemon `threading.Thread`, registers it via
  `_team_threads_set(name, {...})` (line 1321), and returns immediately.
  `_run_team_in_background()` itself is already generic — it loads a fresh
  state by `run_id` and calls `teams.team_run(state, cancel_event=...)` —
  so it needs **no change** to also drive a *resumed* run; `team_run()`'s
  own `while state["status"] == "running"` loop (`app/teams.py:2849`) picks
  up wherever the freshly-persisted state says to.
  `team_run()`'s loop **exits and the thread self-terminates** (via
  `_team_threads_pop_if_owned()`, line 1331) the moment `team_step()` sets
  `status` to `blocked_ask_user` — so by the time a human answers, there is
  no live thread for that project; resolving must spawn a **new** thread,
  it cannot resume an old one.
- **`GET /projects/<name>/team/grounding`** (`app/app.py:3552-3567`) is the
  existing precedent for a small, read-only, project-scoped GET route
  needing only `_authed()` (no TOTP) — the new `events`/`inbox` routes
  follow the identical gating and `parts = [...]` routing style.
- **TOTP gating is structural, not per-route.** Every POST route reaches
  its own `elif parts[0] == ...` branch (`app/app.py:3607` onward) only
  after the shared `session_totp_ok(sid)` check just above it
  (`app/app.py:3599-3605`) — `POST /team/resolve` inherits this for free,
  the same way `/team/start`/`/team/stop` already do; no new gating code.
- **A reusable byte-offset incremental-read precedent already exists**,
  though CLI-only and not directly reusable as-is: `_tail_log_once()`
  (`app/teams.py:3643-3653`) seeks to a byte offset, reads to EOF, and
  processes only complete lines (via `splitlines()`, which silently drops
  any trailing partial line — fine for its own use, printing to stderr on a
  0.2s loop, but this spec's own bound-per-poll and don't-lose-a-partial-
  line requirements need a stricter version — see "Proposed approach").
- **Existing test infrastructure to build on:** `tests/test_team_routes.py`
  already has a reusable `_RealHTTPTeamTestCase` base class
  (line 224) that spins up a real `ThreadingHTTPServer` instance and
  authenticates a real session — the natural home for this cycle's new
  route tests (a new test class per route, following the file's existing
  one-class-per-route-or-concern convention: `TeamStartEndpointTests`,
  `TeamStopEndpointTests`, `TeamGroundingEndpointTests`, etc.).

## Proposed approach

### 1. `GET /projects/<name>/team/events`
Query params (all optional): `run_id` (defaults to
`latest_run_for_project(name)`), `cursor` (URL-encoded JSON object,
`{"<agent>": <byte_offset>, ...}`, defaults to `{}` meaning "from the
start"). Routed via `urllib.parse.urlsplit(self.path)` (the existing
precedent for a GET/POST route that carries a query string is
`/projects/upload`, `app/app.py:3585-3587` — extended here to a GET route
for the first time) plus `urllib.parse.parse_qs` (existing precedent:
`app/app.py:3351`, TOTP `?code=` parsing).

- No run ever started for this project (`latest_run_for_project()` returns
  `None` and no explicit `run_id` given): `200 {"run_id": null, "events":
  [], "cursors": {}}` — not an error; a freshly-created project's Teams
  panel must render an empty state cleanly.
- Explicit `run_id` given: load via a new small helper,
  `teams.load_state_for_project(run_id, project_name)`, that calls
  `_load_state(run_id)` and returns `None` (route replies `404`) if the
  run doesn't exist *or* `state["project_name"] != project_name` — an
  ownership boundary, same discipline every other per-project route already
  applies via `instance_names()` membership checks, extended here to
  prevent one project's operator from reading another project's run data
  by guessing/incrementing a `run_id`.
- For the resolved run, the set of files to merge is `["lead"] +
  state["members"]`, mapped to `_transcript_path(run_id)` (for `"lead"`)
  and `_agent_log_path(run_id, agent)` (for each teammate) respectively. A
  file that doesn't exist yet (a teammate never delegated to) is treated as
  present-but-empty, offset 0 — not an error.
- New function `teams.tail_jsonl_events(path, offset, max_bytes)` (a
  stricter cousin of `_tail_log_once()`, see "Background") →
  `(events: list[dict], new_offset: int, truncated: bool)`:
  - Seeks to `offset`, reads at most `max_bytes + 1` bytes past it (the
    `+1` only to detect "there was more" for the `truncated` flag).
  - Splits on `b"\n"`; the **last** element (a possibly-partial trailing
    line) is *never* included in this call's events *and* the returned
    `new_offset` is walked back to just after the last complete `\n` seen
    — the same "hold a partial line across polls" discipline `_Tailer`
    already uses (`app/teams.py:676-679`), so a line split exactly at the
    byte cap is never parsed truncated and is picked up whole next poll.
  - Each complete line is `json.loads()`'d; a line that fails to parse (or
    parses to a non-dict) becomes one synthetic `{"kind": "error", "text":
    "malformed line in <agent>'s log (json.loads failed)", "meta":
    {"raw_bytes": len(line)}, ...}` event — mirrors `_Tailer._handle_line`'s
    own malformed-line discipline (`app/teams.py:691-698`) — never raises,
    never drops the rest of the poll.
  - `truncated=True` iff more complete-line data remained beyond
    `max_bytes` after this call's own read; `new_offset` in that case still
    lands on a clean line boundary (never mid-line), so the *next* poll
    with the returned cursor picks up exactly where this one stopped, byte-
    for-byte, no gap and no duplicate.
- The route calls this once per file with
  `TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL` as `max_bytes`, merges every
  returned event across all files, sorts by `(ts, agent, seq)` (`ts` is a
  fixed-format ISO-8601 string, lexicographically sortable;
  `agent`+`seq` is the tie-break for same-second events from different
  agents), and returns:
  ```json
  {"run_id": "<id>", "events": [ {...envelope...}, ... ],
   "cursors": {"lead": 1234, "claude": 5678, ...},
   "truncated": {"claude": true}}
  ```
  `cursors` always includes every file's *new* offset (even ones with zero
  new events, so a client's cursor map stays complete across polls without
  it having to merge partial responses itself). `truncated` only lists
  agents that hit the per-file cap this call (omitted/empty otherwise) — a
  client polling immediately again drains the rest.
- A malformed `cursor` query value (not valid JSON, not an object, an
  offset that isn't a non-negative int) is treated as `{}` (start from
  the beginning) rather than a `400` — a stale/hand-crafted cursor should
  degrade to "re-fetch everything", never break the poll loop.

### 2. `GET /projects/<name>/team/inbox`
Same `run_id`-defaults-to-latest, same ownership check as above.
- No run, or resolved run's `status != "blocked_ask_user"`:
  `200 {"pending": false}`.
- `status == "blocked_ask_user"`: read `_inbox_path(run_id)`.
  - Success: `200 {"pending": true, "run_id": "<id>", "question": "...",
    "header": "...", "options": [...], "multi_select": false}` — the exact
    persisted shape, passed through unchanged.
  - `inbox.json` missing or unreadable/malformed despite `status` being
    `blocked_ask_user` (a real-world corruption/race window, however
    narrow — e.g. read lands between `_force_ask_user`'s status-set and its
    `_write_inbox` call in a pathological interleaving, or hand-edited
    state): **still** `"pending": true`, with a safe synthesized fallback
    `question` ("The team is waiting for input, but the original question
    could not be read — check `tmux attach` or answer with any text to
    unblock it.") and empty `options` — never silently reports `"pending":
    false"` while the run is genuinely stuck; the whole point of this
    route is "impossible to miss" (`docs/story.md` §6f AC), and a
    corrupted inbox file must degrade to a generic-but-still-visible
    escalation, not an invisible one.

### 3. `POST /projects/<name>/team/resolve`
Reached through the existing shared TOTP gate (`app/app.py:3589-3605`,
unchanged) — no new gating code, same as `/team/start`/`/team/stop`.
Body: `{"answer": "<text>", "run_id": "<optional>"}`.
- `run_id` (or `latest_run_for_project(name)` if omitted) must resolve to a
  run with `project_name == name` (ownership check, same as the GET
  routes) and `status == "blocked_ask_user"` — else `400 {"error":
  "<specific reason>"}` (`"no run found for this project"` /
  `"this run belongs to a different project"` /
  `"no pending question for this project"`), **never** a silent no-op —
  unlike `/team/stop`'s own "nothing to do" `200`, an answer a caller
  believes was recorded but wasn't must be a loud error, not swallowed.
- `answer.strip()` must be non-empty and ≤
  `TEAM_ASK_USER_ANSWER_MAX_CHARS` chars — else `400`, before any state
  mutation (mirrors `_validate_prompt_size`'s "validate before touching
  anything" discipline, `app/teams.py:213-268`, applied to a new field
  rather than reusing that function directly, since its bound and error
  shape are tuned for a *prompt*, not a short human answer).
- New shared function `teams.resolve_ask_user(run_id: str, answer: str) ->
  dict` extracted from `_cli_team_resolve()`'s body (lines 3798-3808):
  performs the load/status-check/append-history/inbox-move/status-flip/
  persist sequence, returns `{"ok": True}` or `{"ok": False, "error":
  "<reason>"}` (never raises for an ordinary "wrong state" case — same
  "return a shaped result, don't make the caller catch" convention
  `validate_composition()`/`launch_team()` already use). `_cli_team_resolve()`
  is rewritten to call it, then still calls `_drive_and_report(state)` for
  its own foreground-blocking CLI behavior — **zero change** to
  `team-resolve`'s observable CLI behavior (exit codes, blocking, output).
- On `{"ok": True}`: the route spawns a **new** `cancel_event` +
  `threading.Thread(target=_run_team_in_background, args=(name, run_id,
  cancel_event))`, registers it via the existing `_team_threads_set(name,
  {...})` (`app/app.py:3721`, reused verbatim — no new bookkeeping
  function needed, `_run_team_in_background` is already generic enough to
  resume as well as start), starts it, and returns `200 {"ok": true,
  "run_id": run_id}` immediately — the response never waits for the lead's
  next round, matching `/team/start`'s own non-blocking contract.
  - Defensive check before spawning: if `_team_threads_get(name)` already
    shows a live entry, refuse with `400` ("a team thread is already
    running for this project") instead of spawning a second driver.
    Should be unreachable in practice — `latest_run_for_project()`'s own
    invariant (at most one non-terminal run per project,
    `app/teams.py:3501-3503`) combined with `team_run()`'s loop already
    exiting (and its thread already popped) the instant a run becomes
    `blocked_ask_user` means no live thread should exist for a project
    whose latest run is genuinely `blocked_ask_user` — but cheap to assert
    rather than trust, and turns an already-impossible race into a clear
    error instead of two threads driving one run.
- On `{"ok": False}` (a concurrent resolve already flipped the status
  between this request's own status check and its persist — see "Edge
  cases"): `400 {"error": result["error"]}`, no thread spawned.

### 4. `/status`'s additive `waiting_on_you` field
One-line change inside the existing per-instance loop
(`app/app.py:3477-3527`): alongside the existing `team_status` computation,
add `waiting_on_you = (run is not None and run["status"] ==
"blocked_ask_user")`, and include it in `inst["team"]` as a new key.
Every existing key/value in that dict (`status`, `run_id`, `composition`)
is untouched — purely additive, so 6e's own `StatusRosterAndCompositionTests`
must still pass unmodified (verifies no regression).

## Affected areas
- `app/teams.py` — new `tail_jsonl_events()`, `load_state_for_project()`,
  `resolve_ask_user()` (extracted from `_cli_team_resolve()`); two new
  config constants (see below); `_cli_team_resolve()` refactored to call
  the new shared function (behavior unchanged).
- `app/app.py` — three new routes (`GET .../team/events`, `GET
  .../team/inbox`, `POST .../team/resolve`); one new import
  (`urllib.parse.parse_qs`, already imported elsewhere in the file) used
  for the first time on a GET route; one new field on `/status`'s
  per-project `team` object.
- `config/switchboard.env.example` — `TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL`,
  `TEAM_ASK_USER_ANSWER_MAX_CHARS`.
- `tests/test_team_routes.py` — new test classes for the three routes
  (real-HTTP, via the existing `_RealHTTPTeamTestCase` base) plus one
  addition to `StatusRosterAndCompositionTests` (or a sibling class)
  asserting `waiting_on_you`'s two values and that every pre-existing
  `/status` field is unchanged.
- No changes to `docs/ADDING_AN_ENGINE.md` or `docs/ARCHITECTURE.md` — no
  new engine-facing concept, no new architectural primitive (three routes
  + one field on an existing dict is additive plumbing, not a new
  subsystem).

## Edge cases
- **No run ever started for a project.** Both GET routes return a clean
  empty/`pending:false` response, not `404`/`500` — a project that has
  never run a team must still render a sane empty Teams panel later.
- **Cross-project `run_id` guessing.** An explicit `run_id` belonging to a
  *different* project is `404` on both GET routes and `400` on the POST
  route — never serves or mutates another project's run.
- **Stale tab watching an old run after a new one starts.** Because
  `run_id` is accepted as an explicit override (not just "always latest"),
  an already-open tab can keep polling a finished run's full history even
  after `latest_run_for_project()` would now return a different, newer
  run — it simply won't see the newer run's events unless the client
  explicitly asks for its `run_id` instead.
- **A file with more new data than one poll's byte budget.** Returns
  `truncated: true` for that agent and a cursor on a clean line boundary;
  the client's very next poll (even immediately) continues seamlessly —
  no event is ever skipped or duplicated across a truncation boundary.
- **A malformed line in an agent's log** (partial write torn by a crash,
  a future engine's translator bug slipping one bad line through) becomes
  one `kind: "error"` synthetic event in the merged feed for that position,
  never a `500` for the whole poll and never silently dropped.
- **`inbox.json` missing while `status == "blocked_ask_user"`.** Covered
  above — `GET .../team/inbox` still reports `pending: true` with a safe
  fallback question rather than under-reporting a real block.
- **Two concurrent `POST .../team/resolve` calls for the same run**
  (double-submit, two tabs). Both re-check `status == "blocked_ask_user"`
  as part of `resolve_ask_user()`'s own load-check-persist sequence; the
  first to persist wins (status becomes `"running"`), the second's own
  freshly-reloaded state (loaded inside `resolve_ask_user()`, never a
  stale value threaded in from the route) sees `status != "blocked_ask_user"`
  and returns the same `400` ordinary callers get for "nothing pending" —
  exactly one thread is ever spawned for one answer. (This is check-then-
  act over two file operations, not lock-guarded — the same single-writer
  assumption every other run.json mutator in this codebase already carries;
  not a new risk introduced by this spec, and no worse than the pre-
  existing CLI `team-resolve` racing a hypothetical second CLI invocation
  today.)
- **A resolve racing a concurrent `/team/stop`.** If `/team/stop` tears
  down the tmux session/worktrees between `resolve_ask_user()`'s persist
  and the newly-spawned thread's first `agent_run()` call, that call fails
  the same way any in-flight delegation against a killed session already
  does today (pre-existing `_run_headless_session()` behavior, unrelated
  to this spec) — not a new failure mode, not hardened further here.
- **Oversized or empty `answer`.** Rejected `400` before any file is
  touched — the pending question is still there, unanswered, and the
  caller gets a specific reason rather than a generic failure.
- **A run with zero teammates ever delegated to.** `state["members"]`
  still lists them (composition, not "who was actually used") — their log
  files don't exist yet, treated as empty per the "missing file = present,
  empty, offset 0" rule above, not an error.

## Acceptance criteria
- [ ] Given a project that has never started a team, when `GET
      .../team/events` is called with no `run_id`, then it returns `200`
      with `{"run_id": null, "events": [], "cursors": {}}`.
- [ ] Given a project's latest run has written events to its lead
      transcript and two teammates' logs, when `GET .../team/events` is
      called with an empty cursor, then the response contains every event
      from all three files, chronologically merged.
- [ ] Given the cursors returned by the call above, when `GET
      .../team/events` is called again with those cursors and no new
      events have been written, then the response's `events` list is
      empty (no event is ever returned twice).
- [ ] Given one agent's log has more new bytes pending than
      `TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL`, when that file is polled,
      then the response marks that agent `truncated: true`, returns a
      cursor on a clean line boundary, and a follow-up poll with that
      cursor returns the remaining events with no gap or duplicate.
- [ ] Given a deliberately malformed line written into an agent's log
      file, when that file is polled, then the response includes one
      `kind: "error"` event for that position and processing continues
      for the rest of that file and every other file (no exception, no
      500).
- [ ] Given a `run_id` that exists but belongs to a different project,
      when either GET route is called with it for the wrong project name,
      then the response is `404` and no data from that run is returned.
- [ ] Given a run whose status is `finished`/`error`/`stopped` (no thread
      running), when `GET .../team/events` is called, then it still
      returns that run's full historical event set.
- [ ] Given a project with no run, or whose latest run's status isn't
      `blocked_ask_user`, when `GET .../team/inbox` is called, then it
      returns `{"pending": false}`.
- [ ] Given a run genuinely blocked on `ask_user`, when `GET
      .../team/inbox` is called, then it returns `pending: true` plus the
      exact persisted question/header/options/multi_select.
- [ ] Given a run blocked on `ask_user` whose `inbox.json` is deleted out
      from under it, when `GET .../team/inbox` is called, then it still
      returns `pending: true` with a non-empty fallback question, never
      `pending: false`.
- [ ] Given a run genuinely blocked on `ask_user`, when `POST
      .../team/resolve` is called with a valid non-empty `answer` and a
      valid TOTP-gated session, then the response returns immediately
      (before the lead's next round completes), `inbox.json` is moved to
      `inbox.resolved.json`, `status` becomes `running`, and the lead's
      next round context includes the submitted answer text.
- [ ] Given a project whose latest run is not blocked on `ask_user`
      (idle, running, finished, or no run at all), when `POST
      .../team/resolve` is called, then it returns `400` with a specific
      reason and mutates no state.
- [ ] Given an empty/whitespace-only `answer` or one exceeding
      `TEAM_ASK_USER_ANSWER_MAX_CHARS`, when `POST .../team/resolve` is
      called, then it returns `400` before any state is mutated.
- [ ] Given two concurrent `POST .../team/resolve` calls for the same
      blocked run, when both are issued back-to-back, then exactly one
      succeeds (spawns exactly one resume thread) and the other receives
      the same `400` an ordinary "nothing pending" caller would get.
- [ ] Given any project, when `GET /status` is polled, then each
      project's `team` object includes a `waiting_on_you` boolean that is
      `true` iff that project's latest run status is exactly
      `blocked_ask_user`, and every field/value `/status` already
      returned before this change is byte-for-byte unchanged (existing
      `StatusRosterAndCompositionTests` still pass unmodified).
- [ ] Given the CLI's `team-resolve` subcommand, when it is invoked the
      same way it is today, then its observable behavior (blocking,
      output, exit code) is unchanged, and its resolve-and-append logic is
      no longer duplicated — verified by both the CLI path and the new
      route producing identical persisted `run.json` state for the same
      `run_id`/`answer` input.

## Open questions
- **Cursor wire format.** Proceeding with a single URL-encoded JSON object
  query param (`?cursor={"lead":120,"claude":340}`) rather than one query
  param per agent, since the agent set is variable per team and this keeps
  the route's own parsing simple. This is an internal contract between
  this route and part 2's own frontend (not consumed by the CLI or any
  other caller), so it's cheap to change later if part 2's ux-designer or
  developer finds a shape that's easier to drive from `fetch()`. Flagging,
  not blocking.
- **`TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL` default (proposing 65536,
  64 KiB).** Unlike 6b's grounding cap (validated against this repo's own
  20 KB `BACKLOG.md`, a real measured case), there's no existing "how
  chatty is one agent's event stream per poll interval" data point yet.
  Proceeding with a round, conservative default in the same order of
  magnitude as this module's other per-poll caps
  (`TEAM_HEADLESS_STDERR_TAIL_BYTES=4096` is much smaller but serves a
  different purpose — a one-shot tail, not a per-poll budget); it's an env
  var, so a real deployment can tune it once part 2 shows actual traffic.
- **`waiting_on_you` semantics for `escalated_max_rounds`.** Proceeding
  with `waiting_on_you = True` **only** for `blocked_ask_user` (the
  resolvable case — an `ask_user` this route can actually answer).
  `escalated_max_rounds` is a *terminal* status with no `inbox.json` and
  nothing to resume (`_force_ask_user()`'s own docstring, `app/teams.py:
  2540-2554`, is explicit that these are materially different statuses) —
  so it stays under whatever part 2's status strip calls its general
  "blocked" bucket, not "waiting on you". Flagging this reading now so
  part 2's ux-designer doesn't have to re-derive it from scratch.
- **Should `POST /team/resolve` also accept a `run_id` explicitly, or
  only ever act on the project's current latest run?** Proceeding with
  accepting an optional `run_id` (defaulting to latest) purely for
  request/response symmetry with the two GET routes and because the
  ownership check is already needed there regardless — but in practice a
  resolve for anything other than the current latest run is always
  rejected anyway (`status != "blocked_ask_user"` for any non-latest run,
  by the same at-most-one-non-terminal-run invariant). Low-stakes either
  way; noting the reasoning rather than asking.

## Risk / rollback notes
- Every change here is additive: three new routes nothing currently calls,
  one new field on an existing response object, and a refactor (extract,
  don't rewrite) of already-reviewer-approved CLI logic with an explicit
  "identical persisted state" acceptance criterion guarding against
  behavior drift. Reverting is deleting the new routes/field and the
  `_cli_team_resolve()` refactor's extraction (restoring its inline body)
  — no migration, no data format change, no effect on any already-running
  team if this is rolled back mid-run (the routes are pure additions; a
  run in progress doesn't depend on them existing).
- The main risk is the new per-file byte-cap-with-clean-line-boundary
  logic (`tail_jsonl_events()`) — a subtly wrong offset calculation would
  either drop or duplicate events across a truncation boundary. Mitigated
  by dedicated tests exercising a deliberately-oversized log file (mirrors
  6b's own "test against a realistic oversized file, not a synthetic
  one" precedent) and asserting the full recovered sequence across
  multiple bounded polls exactly matches one unbounded read.
