# Spec: 6f part 1b — losing concurrent `/team/resolve` call must not leave a stale transcript entry

## Summary
`teams.resolve_ask_user()` writes its `ask_user_resolved` transcript entry to
`transcript.jsonl` *before* deciding whether this caller actually won the
race to resolve the pending question, so a **losing** concurrent resolve
(two tabs, a double-submit) permanently leaves a spurious `tool_result`
entry in the run's transcript even though its answer was never accepted —
fix it by moving that write to after the win/lose decision, so only the
winner's answer is ever recorded.

## Goals
- `resolve_ask_user()` only calls `_append_history()` (and therefore only
  writes to `transcript.jsonl` / `state["history"]`) for the call that
  actually wins the race to resolve a pending `ask_user`.
- A losing call's behavior is otherwise unchanged: it still returns
  `{"ok": False, "error": "..."}` with the same three possible reason
  strings the route/CLI already handle, and it still never touches or
  persists `state`.
- Close this before 6f part 2 (Teams page UI) starts, so the merged event
  feed that part 2 renders never has to special-case a known-buggy,
  misleading artifact — it can trust that every `ask_user_resolved` entry
  in the transcript was actually accepted.

## Non-goals
- Backlog item 11(b) (`run_id` not validated against path traversal on the
  three `team/*` routes). Unrelated to this bug (it's an input-validation
  gap, not a race), doesn't affect the UI part 2 is about to build, and
  stays in `docs/BACKLOG.md` item 11 for a future cycle.
- Adding a lock/mutex around `resolve_ask_user()`. `docs/spec.md`'s prior
  (6f part 1) "Edge cases" section already accepts "the first to persist
  wins, not lock-guarded" as the deliberate design — this fix doesn't
  relitigate that, it only makes sure the *loser* of that already-accepted
  race never has an observable side effect.
- Any change to `_write_inbox()`, `os.replace()`'s own role as the sole
  win/lose arbiter (already fixed in the prior round-2 fix, see
  `docs/implementation.md`'s "reviewer fix round" section), or the route
  layer (`app/app.py`'s `POST .../team/resolve` handler). This is a
  single-function, single-file fix.

## Background / current state
`app/teams.py`'s `resolve_ask_user(run_id, answer)` (currently ~line
3752-3834) is the shared function `POST /team/resolve` (`app/app.py`
~line 3902) and the CLI's `_cli_team_resolve()` both call. Its current
order of operations:

1. `state = _load_state(run_id)` (reload fresh from disk — never trusts a
   caller-supplied state, by design).
2. If `state["status"] != "blocked_ask_user"`, return `{"ok": False, ...}`
   immediately (no write at all — this early-exit case is already correct
   and out of scope for this fix).
3. **`_append_history(state, round_n, tool="ask_user_resolved", ...,
   transcript_entries=[("tool_result", answer, {"resolved": True})])`**
   — this is the bug. `_append_history()` (line 2533) does two things
   unconditionally: appends to `state["history"]` (in-memory only, at this
   point) **and** calls `_append_transcript()` (line 2520), which opens
   `transcript.jsonl` in append mode and writes the entry to disk
   immediately, synchronously, with no rollback path.
4. `inbox_path = _inbox_path(run_id)`; `try: os.replace(inbox_path,
   _inbox_resolved_path(run_id))` — this is the actual win/lose decision
   point. The winner's `os.replace()` succeeds; a loser's raises
   `FileNotFoundError` (its target was already renamed away) and is caught,
   returning `{"ok": False, "error": "... is not blocked on ask_user
   (status=...)"}`.
5. Only on the winning path: `state["status"] = "running"; _persist(state)`.

Because step 3 runs *before* step 4's decision, a losing call's in-memory
`state["history"]` mutation is correctly discarded (step 5 never runs for
it, so `_persist()` never writes it) — but its `_append_transcript()` call
in step 3 already hit disk in step 3, unconditionally, and there is no
corresponding cleanup on the loss path. Confirmed by reading
`_append_history()`/`_append_transcript()` directly (`app/teams.py:2520-
2542`): `_append_transcript()` has no caller-visible "undo" and is called
synchronously inside step 3, strictly before step 4 ever runs.

This is `docs/BACKLOG.md` item 11's first bullet ("Stale transcript
entry"), found by the 6f part 1 reviewer and recorded as non-blocking at
the time (it doesn't affect the lead's own decision-making, which reads
`state["history"]`, not the transcript file) but flagged as something that
*would* affect 6f part 2's UI, which renders `transcript.jsonl` verbatim
through `GET .../team/events`. Reviewed now, before part 2 is spec'd, per
that item's own note and the product-manager's explicit call this
iteration: fix it as a small prerequisite rather than have the UI design
around a known, already-diagnosed, cheaply-fixable bug.

**Precedent for the exact repro technique to reuse**: this is the *second*
race found in this same function this story (see
`docs/implementation.md`'s "reviewer fix round" section for the first,
the `os.path.exists()` check-then-act race). Both the deterministic
one-shot-hook repro (`tests/test_team_routes.py`
`test_loser_whose_exists_check_lands_after_winner_already_renamed_does_not_report_ok`,
line 1202) and the genuine two-thread repro (`test_two_concurrent_resolves_
exactly_one_succeeds`, line 1160) are directly reusable patterns — this fix
needs the same hook-based deterministic technique, just asserting a
different final condition (transcript line count / content, not
`state["history"]` content).

## Proposed approach
In `app/teams.py`'s `resolve_ask_user()`, reorder so the history/transcript
write only happens after `os.replace()` has already succeeded:

1. Keep steps 1-2 (load, status check) exactly as they are.
2. Move the `round_n = len(state["history"]) + 1` computation and the
   `_append_history(...)` call to **after** the `try: os.replace(...)
   except OSError: ...` block succeeds, and **before** `state["status"] =
   "running"`. Concretely: the loss path (the `except OSError:` branch)
   returns exactly as it does today, without ever calling
   `_append_history()`; the win path now runs `_append_history()` then
   `state["status"] = "running"` then `_persist(state)`, in that order, all
   after `os.replace()` has already returned without raising.
3. No change to `_append_history()`'s or `_append_transcript()`'s own
   signature or behavior — this is purely a call-site reordering within
   `resolve_ask_user()`.
4. Extend `resolve_ask_user()`'s docstring with a short paragraph
   documenting this fix, matching the style of the two paragraphs already
   there for the prior two races in this function (append, don't rewrite
   the existing two).

This is a same-file, same-function, no-new-locking change — `os.replace()`
remains the sole win/lose arbiter (already true after the prior fix round);
this fix only moves a side effect from before that arbiter's decision to
after it.

## Affected areas
- `app/teams.py` — `resolve_ask_user()` (~line 3752-3834): reorder the
  `_append_history()` call relative to the `os.replace()` block; docstring
  addition. No other function in this file needs to change.
- `tests/test_team_routes.py` — `TeamResolveEndpointTests` (starts line
  1062): new regression test using the same deterministic one-shot-hook
  technique as `test_loser_whose_exists_check_lands_after_winner_already_
  renamed_does_not_report_ok` (line 1202), reused verbatim in mechanism —
  hook `teams._load_state()` so a real winning `resolve_ask_user()` call
  runs to completion between the loser's own state read and its subsequent
  move step, then assert the loser's call did NOT add a transcript entry.
- No API/route/wire-format change — `app/app.py`'s `POST .../team/resolve`
  handler is untouched, and this fix is invisible to a well-behaved
  (non-racing) caller.

## Edge cases
- **Genuinely simultaneous callers (both threads racing, no hook)**: still
  covered by the existing `test_two_concurrent_resolves_exactly_one_
  succeeds` (line 1160) — that test doesn't assert transcript content
  today and should keep passing unmodified; it's the deterministic hook
  test that needs the new assertion, since only that one pins down which
  caller is genuinely the loser.
- **A loser caught at the route layer's own "already running" check**
  (`app/app.py`'s defensive `if _team_threads_get(name) is not None`)
  never even reaches `resolve_ask_user()` a second time — unaffected by
  this fix, already returns before any write.
- **A loser caught at `resolve_ask_user()`'s own upfront status check**
  (step 2, `state["status"] != "blocked_ask_user"`) already returns before
  reaching the `_append_history()` call in today's code too — this fix
  doesn't change that path's behavior, only the path where the caller gets
  past step 2 and loses at step 4's `os.replace()`.
- **The winner's transcript entry and `state["history"]` entry must be
  byte-for-byte unchanged** from what today's code produces on the winning
  path — this fix only removes a write from the *losing* path, it doesn't
  alter the winning path's own output at all (verify by diffing a winning
  call's persisted `state["history"][-1]` and its transcript line before
  and after the change).

## Acceptance criteria
- [ ] Given two genuinely concurrent `POST /team/resolve` calls for the
      same pending `ask_user` (real-thread race, existing
      `test_two_concurrent_resolves_exactly_one_succeeds`), when both
      complete, then exactly one `ask_user_resolved` entry exists in
      `transcript.jsonl` for that run (today: can be two).
- [ ] Given the deterministic hook-based repro (loser's `_load_state()`
      lands after a real winner has already completed), when the loser's
      `resolve_ask_user()` call returns `{"ok": False, ...}`, then
      `transcript.jsonl` contains exactly one `ask_user_resolved`
      (`tool_result`) entry, and it is the winner's answer text — not two
      entries, and not the loser's text.
- [ ] Given a single, non-racing `POST /team/resolve` call (the ordinary
      case), when it succeeds, then the persisted `state["history"]` and
      `transcript.jsonl` entries are unchanged in shape and content from
      today's behavior (no regression to the happy path).
- [ ] The full test suite passes, including the two pre-existing
      `TeamResolveEndpointTests` races (`test_two_concurrent_resolves_
      exactly_one_succeeds`, `test_loser_whose_exists_check_lands_after_
      winner_already_renamed_does_not_report_ok`) and
      `tests.test_teams_lead.ResolveInSeparateProcessTests` (the CLI's own
      non-concurrent regression test, which must stay byte-for-byte
      unaffected since it never exercises the loss path).

## Open questions
None — this is a small, fully-diagnosed, single-function fix with a
directly reusable test pattern already in the codebase. Proceeding without
further sign-off.

## Risk / rollback notes
Low risk: one function, no new locking, no wire-format change, no route
change. If it regresses the happy path, revert the single call-site
reorder in `resolve_ask_user()` — the diff is small enough that `git
revert` on the one commit is a complete rollback.

---

## Note for the next product-manager iteration (6f part 2, deferred)

This cycle deliberately does **not** contain 6f part 2 (the Teams page UI:
merged event feed, per-agent filter, status strip, escalation inbox). Once
this bugfix lands and is reviewer-approved, the very next product-manager
turn should write that spec fresh — the archaeology for it is already done
and should be reused directly rather than re-derived:

- **This is a single-page app, not a multi-page one.** `app/app.py`'s
  `Handler.do_GET` only ever serves one HTML document at `self.path == "/"`
  (`PAGE_TEMPLATE`, ~line 1636, with inline `<style>`/`<script>`) — there is
  no route-per-page mechanism anywhere in this codebase. Story.md's "Teams
  page" wording should be read the same way 6e's "settings screen" wording
  was already corrected: **not** a new URL/route, but an expansion of the
  existing per-project `teamRow()` render (`app/app.py` ~line 2237-2284).
  Specifically, the `team.status !== 'idle'` branch (line 2272-2283) —
  which today renders a static `Status: [blocked]` line plus a one-line
  `"Lead is waiting for input · check tmux attach"` sub — is exactly where
  the live merged feed, status strip, and escalation panel belong, using
  the same expand/collapse idiom (`team-configure-row` / `toggleTeamPicker`
  at line 2262-2265) already established for 6e's lead/teammate picker.
- **Route contracts already shipped and tested, ready to build against
  as-is** (no backend changes needed for part 2 beyond this cycle's fix):
  - `GET /projects/<name>/team/events?run_id=&cursor=` → `{"run_id",
    "events": [...], "cursors": {"<agent>": <byte_offset>}, "truncated":
    {"<agent>": true}}` (`app/app.py:3637-3666`). `cursor` is a
    URL-encoded JSON object `{"<agent>": <byte_offset>}`
    (`_parse_events_cursor()`, `app/app.py:1388`); a malformed cursor
    degrades to `{}` rather than a 400. Each event is the §4.1 envelope
    (`{ts, agent, seq, kind, text, meta}`), `kind` ∈
    `message|tool_use|tool_result|status|error|handoff`. Bounded per file
    per poll by `teams.TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL` — the
    client must keep polling with the returned `cursors` to drain a
    `truncated: true` file rather than treating one poll as complete.
  - `GET /projects/<name>/team/inbox?run_id=` →
    `{"pending": false}` or `{"pending": true, "run_id", "question",
    "header", "options": [{"label","description"}...], "multi_select"}`
    (`app/app.py:3668-3701`). Always has a non-empty `question` even if
    `inbox.json` itself is unreadable (safe fallback text).
  - `POST /projects/<name>/team/resolve` body `{"run_id"?, "answer",
    "code"}` (TOTP-gated, same as every other state-changing action) →
    `{"ok": true, "run_id"}` or `{"error": "..."}`, 400 on no pending
    question / empty or over-`TEAM_ASK_USER_ANSWER_MAX_CHARS` answer
    (`app/app.py:3873-3920`).
  - `GET /status`'s per-project `team` object already carries an additive
    `waiting_on_you` boolean (`app/app.py:3558`) — true iff
    `run["status"] == "blocked_ask_user"`. This is the cheapest signal for
    the status strip's "waiting on you" state; no need to poll `/team/
    inbox` just to light that indicator.
- **`fact_check` auditability**: the acceptance criterion "`fact_check`
  calls appear in the feed with the passage and `file:line`" is already
  satisfiable from the existing envelope shape — `fact_check` results are
  written via `_append_history()`'s `transcript_entries` (see
  `app/teams.py:2811` and grounding's own `file:line` return shape, 6b) —
  part 2 just needs to render `meta` for that `kind` appropriately, no new
  backend field.
- **This fix (1b) removes the one known reason a "resolved" entry in the
  feed could be misleading** — part 2's spec should NOT need an "ignore
  stray resolve entries" edge case once this lands.
- Item 11(b) (`run_id` path traversal on the three `team/*` routes) is
  still open and unrelated to part 2's UI — worth a one-line mention in
  part 2's own "Non-goals" so it isn't silently forgotten a second time,
  but it does not block or shape the UI work.
