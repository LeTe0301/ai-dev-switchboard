# Spec: Backlog item 7 part 2 — web UI for approving/rejecting board_write proposals

## Summary
Extend the Teams page's already-shipped `blocked_ask_user` web UI (status
pill, escalation panel, resolve action, Stop button) to also handle
`blocked_board_write` runs — the second escalation kind part 1 (`app/
teams.py`, already merged) introduced — reusing part 1's backend
(`resolve_board_write()`, the generalized `inbox.json` `kind` discriminator)
exactly where its shapes already match `blocked_ask_user`'s, and extending
presentation only where a board-write proposal's own shape (Taiga verb,
target userstory `ref`, proposed value, current value) needs it.

## Goals
- `/status` and `GET .../team/inbox` report a `blocked_board_write` run as
  pending/waiting-on-you, distinguishable from `blocked_ask_user` (today
  `/team/inbox` is `ask_user`-only and silently reports `{"pending": false}`
  for a `board_write` block).
- A human can approve or reject a pending board-write proposal from the
  Teams page, TOTP-gated the same way `doTeamResolve()`/`POST .../team/
  resolve` already gate the `ask_user` answer flow, calling part 1's
  already-shipped `resolve_board_write(run_id, action)` on the backend.
- `POST .../team/stop` actually stops a run blocked on `blocked_board_write`
  (currently a silent no-op — disclosed gap in `docs/implementation.md`
  "Known limitations").
- The escalation panel and the merged event feed render each of the three
  `board_write` verbs (`set_status`, `amend_description`, `append_comment`)
  with verb-specific, human-readable copy — not a generic "a change is
  pending" box — using the fields part 1's proposal already carries
  (`verb`, `ref`, `value`, `note`, `current_value`).

## Non-goals
- Any change to `app/teams.py`'s tool contract, `resolve_board_write()`'s
  approve/reject mechanics, `app/taiga_board.py`'s client, the
  `team-board-resolve` CLI, or the `blocked_board_write` status itself —
  all already shipped and reviewed in part 1. This spec only adds
  `app/app.py` routes/JS on top.
- Inline editing of a pending proposal's `value` before approving — part
  1's own explicit non-goal, unchanged. Approve or reject are the only two
  actions; there is no free-text "Other" field for `board_write` the way
  `ask_user`'s panel has one (`resolve_board_write()` takes no free-text
  argument at all).
- A general "browse the Taiga board" screen — `board_read` is a lead-only
  tool; no new human-facing board browser is being added. The only new
  human-visible surface is the pending-proposal panel itself.
- A dedicated audit-log page for past board-write decisions. Every
  `board_write`/`board_write_resolved` history and transcript entry is
  already persisted (part 1) and already flows through 6f's existing
  merged event feed once rendered with the verb-specific copy this spec
  adds (see "Proposed approach" §4) — no new persistence or a second view.
- Changing `ask_user`'s existing UI behavior, copy, or field names. Every
  `ask_user` code path in `app/app.py` referenced below is read for
  precedent, not modified, except where a shared helper (the status map,
  the inbox route, the stop route, `actionPath`/`actionBody`/
  `handleActionResult`) must branch to add the new kind alongside the
  existing one.
- Batching or queuing multiple simultaneous proposals — still exactly one
  pending escalation (of either kind) per run, enforced by part 1's
  backend; this spec doesn't add any new concurrency handling beyond
  surfacing the backend's existing race-safe behavior in the UI (see
  "Edge cases").

## Background / current state

### What part 1 already shipped (backend, CLI, no web UI) — see `docs/spec.md`/`docs/implementation.md` on `backlog/lead-kanban-write-7`, merged into this branch
- `inbox.json` has a `"kind"` discriminator: `"ask_user"` (existing shape)
  or `"board_write"`:
  ```json
  {"kind": "board_write", "verb": "set_status", "ref": 42,
   "value": "In progress", "note": "moving to in-progress per delegate result",
   "current_value": {"status_id": 1, "status_name": "New"},
   "proposed_at": "2026-08-14T12:00:00Z"}
  ```
  `current_value`'s shape depends on `verb`: `{"status_id", "status_name"}`
  for `set_status`, `{"description"}` for `amend_description`, `{}` (empty
  — nothing to snapshot) for `append_comment`. `note` may be `null`.
  **`current_value` does not include the userstory's `subject`/title** —
  only a status/description snapshot relevant to the verb — see "Proposed
  approach" §2 for how this spec surfaces the card's subject anyway.
- `state["status"]` is `"blocked_board_write"` while a proposal is pending,
  parallel to `"blocked_ask_user"`.
- `teams.resolve_board_write(run_id, action)` (`app/teams.py` line 4085),
  `action` is `"approve"` or `"reject"` (no free-text argument, unlike
  `resolve_ask_user()`). Reuses `resolve_ask_user()`'s exact race-safety
  shape (state always reloaded fresh from disk; `os.replace(inbox_path,
  inbox_resolved_path)` is the sole arbiter of a concurrent-resolve race;
  history/transcript entries appended only after that replace succeeds).
  Returns `{"ok": True, "state": state}` on success (identical shape to
  `resolve_ask_user()`) or `{"ok": False, "error": str}` — two error
  causes: `status != "blocked_board_write"`, or `action` not one of
  `approve`/`reject`. On `"approve"`, resolves `value` (a human-readable
  status name for `set_status`) to a numeric status id via
  `taiga_board.list_userstory_statuses()`, fetches a **fresh** `version`
  (never `current_value`'s proposal-time snapshot), calls the matching
  `taiga_board.set_status`/`amend_description`/`append_comment`. A Taiga-
  side failure (conflict, network error, vanished `ref`, unknown status
  name) does **not** leave the inbox unresolved — history records
  `"approved but Taiga rejected the write: {detail}"` and `status` still
  returns to `"running"`.
  - History entries: `tool="board_write"` (proposal, `outcome_summary=
    "blocked, awaiting board-write approval"`) and `tool=
    "board_write_resolved"` (`outcome_summary` one of `"rejected by
    human"`, `"approved and applied"`, or `"approved but Taiga rejected
    the write: ..."`).
  - Transcript entries: the proposal is `("tool_use", "board_write(verb,
    ref=N)", {"verb": verb, "ref": ref})`; the resolution is
    `("tool_result", full_result_text, {"resolved": True, "approved":
    bool})` — **note `meta.resolved` is `True` on both approve and reject**
    (see "Proposed approach" §4 for why this collides with the existing
    feed's `ask_user`-resolution rendering and needs a new, more specific
    check ahead of it).
- New `team-board-resolve --run-id --action {approve,reject}` CLI
  subcommand (`app/teams.py`'s `_cli_team_board_resolve()`), the CLI-only
  equivalent of what this spec adds as a web route.
- `app/app.py` is **entirely untouched** by part 1 — this spec is the
  first change to `app/app.py` for this backlog item.

### The `blocked_ask_user` web UI this must mirror (`app/app.py`)
- `/status`'s per-project `team` object (line ~3989): `team_status` map
  `{"running": "running", "blocked_ask_user": "blocked",
  "escalated_max_rounds": "blocked", "finished": "finished", "error":
  "error", "stopped": "idle"}` (falls through to `"idle"` for any status
  not listed — **this is exactly why a `blocked_board_write` run shows as
  `"idle"` today**, per `docs/implementation.md`'s disclosed gap).
  `waiting_on_you = run is not None and run["status"] == "blocked_ask_user"`
  (line 4046).
- `GET /projects/<name>/team/inbox` (`_handle_team_inbox`, line 4167):
  `{"pending": False}` unless `status == "blocked_ask_user"`; else reads
  `inbox.json` and replies `{"pending": True, "run_id", "question",
  "header", "options", "multi_select"}`, with a safe fallback question if
  `inbox.json` is unreadable/malformed despite the status match.
- `POST /projects/<name>/team/resolve` (line 4372): TOTP-gated (the shared
  `session_totp_ok`/428/403 flow at line 4230, common to every mutating
  route); validates `run_id` via `teams._RUN_ID_RE` and project ownership;
  400s if `status != "blocked_ask_user"`; validates `answer` length; calls
  `teams.resolve_ask_user()`; on success, asserts no live thread already
  exists for the project then starts `_run_team_in_background()` on a new
  thread (mirrors `/team/start`'s own non-blocking dispatch).
- `POST /projects/<name>/team/stop` (line 4356): `if run is None or
  run["status"] not in ("running", "blocked_ask_user"): return {"ok":
  True, "message": "no team currently running..."}` — **this is the
  literal no-op gap**: a `blocked_board_write` run falls into this
  early-return branch and nothing happens, though the CLI's `team-stop`
  still works directly against `teams.stop_team()`.
- Frontend (`app/app.py`'s inline JS): `renderTeamStatusStrip(team)` (line
  2346, 4-state strip: running/blocked+waiting-on-you/blocked+escalated/
  finished/error), `fetchTeamInbox()`/`teamInboxCache` (line 2365, fetch-
  once-per-`run_id`, client-cached), `renderEscalationPanel(name, team)`
  (line 2408, gated on `team.waiting_on_you`, renders a `<fieldset>`/radio-
  or-checkbox form from `inbox.options[]` plus a free-text "Other" field),
  `computeTeamResolveAnswer()` (line 2396, shared by the panel's client
  validation and `actionBody()`), `doTeamResolve()` (line 2670, calls
  `toggle('team-resolve', name, true, null)` — the shared TOTP-
  retry/code-overlay plumbing every mutating action reuses), `teamRow()`
  (line 2683, the non-idle branch: status strip + escalation panel (if
  `waiting_on_you`) + feed toggle + feed panel + Stop button),
  `actionPath()`/`actionBody()`/`handleActionResult()` (lines 2766–2860,
  the shared per-`kind` dispatch table every mutating action — `team-
  start`/`team-stop`/`team-resolve` today — plugs into for its route path,
  request body, and 428/403/200 handling, including the TOTP overlay's
  per-`kind` label text).
- The merged event feed (line 2495 `teamFeedEventKindClass()`, line 2523
  `teamFeedEventBody()`) classifies each transcript event by `(kind,
  meta)` shape into a CSS class + human-readable line. Relevant existing
  branches: `tool_result` with `meta.resolved` truthy → class `'resolved'`
  → `'Answer: ' + text`. **This branch will incorrectly also match a
  `board_write_resolved` transcript entry** (its `meta.resolved` is also
  `True` on both approve and reject) unless a more specific check for
  `meta.approved !== undefined` is added ahead of it — see "Proposed
  approach" §4.

## Proposed approach

### 1. `/status`: distinguish `blocked_board_write`
- Extend the `team_status` map (line ~3991) with `"blocked_board_write":
  "blocked"` — same coarse bucket as `blocked_ask_user` (status strip's
  outer state is "blocked" either way; what differs is what's inside the
  panel).
- Extend `waiting_on_you` to `run is not None and run["status"] in
  ("blocked_ask_user", "blocked_board_write")`.
- Add a new field, `escalation_kind`, to the `team` object: `"ask_user"` if
  `run["status"] == "blocked_ask_user"`, `"board_write"` if
  `run["status"] == "blocked_board_write"`, else `None`. This is a direct
  string comparison against `run["status"]` (already loaded for this same
  computation) — no extra file read, no Taiga call. Computing it here
  (rather than making the frontend wait for `GET .../team/inbox` to learn
  the kind) lets the status strip render distinct copy/an icon
  immediately, without a load flicker, the same instant it learns
  `waiting_on_you`.
- `renderTeamStatusStrip()` branches on `team.escalation_kind` when
  `team.status === 'blocked' && team.waiting_on_you` to show distinct copy
  for the two kinds (e.g. "⚠ Waiting on you" for `ask_user` vs "⚠ Board
  write pending approval" for `board_write`) — exact wording/iconography
  is the ux-designer's call; the functional requirement is that the two
  are visually distinguishable without opening the panel.

### 2. `GET .../team/inbox`: board_write branch
`_handle_team_inbox()` currently returns `{"pending": False}` for anything
other than `status == "blocked_ask_user"`. Add a second branch:
- `status == "blocked_board_write"`: read `inbox.json` (same
  `teams._inbox_path(run_id)` helper, same try/except-malformed-fallback
  discipline as the existing branch) and reply `{"pending": True, "kind":
  "board_write", "run_id", "verb", "ref", "value", "note",
  "current_value", "proposed_at"}` — every field read directly off
  `inbox.json`, no new persistence.
- **Card subject enrichment (new, additive read)**: since `current_value`
  never carries the userstory's `subject`/title (see "Background"), the
  route does one best-effort `taiga_board.get_userstory(...)` call (same
  session-resolution helper `team_step()`/`resolve_board_write()` already
  use — `taiga_board.resolve_session()` then `get_userstory(base_url,
  token, project_id, ref)`) to attach `"subject"` to the response. This is
  a **read**, not a proposal-affecting call, and mirrors part 1's own
  `board_read` tool's read semantics — it never touches `version` or
  applies anything. On any `taiga_board.TaigaPushError` (Taiga
  unreachable, card deleted since proposal) this degrades gracefully:
  `"subject"` is simply omitted from the response (`None`/absent), the
  rest of the fields (verb/ref/value/etc., already known from `inbox.json`
  itself) are still returned, and the Approve/Reject buttons remain fully
  functional — the subject is decoration for the panel, never a
  precondition. Fetched once per `run_id` (the frontend already caches
  `/team/inbox`'s response client-side keyed by `run_id`, so this is one
  extra Taiga call per pending proposal shown, not one per poll).
- `status == "blocked_ask_user"` branch: **entirely unchanged** — same
  code path, same response shape (a client that predates this change and
  never sends/reads `kind` still works, since `kind` is additive).
- Anything else: `{"pending": False}`, unchanged.

### 3. `POST /projects/<name>/team/board-resolve` (new route)
A **new, dedicated route** — not an overload of the existing `POST
.../team/resolve`'s `{run_id, answer}` contract — because
`resolve_board_write()`'s own input shape (`run_id` + `action` enum, no
free text) is different enough from `resolve_ask_user()`'s (`run_id` +
free-text `answer`) that branching one route on two different body shapes
would add a conditional split to an already-dense handler for no real
benefit. This mirrors the CLI's own naming exactly (`team-board-resolve`
vs `team-resolve`) — a deliberate, disclosed naming/routing decision, not
left open.
- Reached through the same shared TOTP gate every mutating POST route
  already goes through (`session_totp_ok`/428/403, `do_POST`'s common
  preamble) — no new gating code.
- Body: `{"run_id": str, "action": "approve"|"reject", "code": str
  (optional, TOTP)}`.
- Validation, mirroring `/team/resolve`'s existing shape exactly:
  - `name not in instance_names()` → 404.
  - `run_id` empty → resolve via `teams.latest_run_for_project(name)`
    (same "resolve the latest run if none given" fallback `/team/resolve`
    already has); non-empty → validate via `teams._RUN_ID_RE` (same
    path-traversal guard, backlog item 11(b)'s fix, already shared
    infrastructure — no new regex), load state, check
    `state["project_name"] == name`.
  - `state.get("status") != "blocked_board_write"` → 400, `{"error": "no
    pending board write for this project"}` (same wording convention as
    `/team/resolve`'s own `"no pending question for this project"`).
  - `action not in ("approve", "reject")` → 400, `{"error": "action must
    be 'approve' or 'reject'"}` — validated before calling
    `resolve_board_write()` (defense in depth; `resolve_board_write()`
    itself also validates this and would return the same shape of
    failure, but rejecting client-side-obviously-invalid input before any
    state mutation matches this file's existing discipline elsewhere).
  - Calls `teams.resolve_board_write(run_id, action)`; `result["ok"] ==
    False` → 400 `{"error": result["error"]}`.
  - Same defensive "a team thread is already running for this project" 400
    check `/team/resolve` already has (line 4420) before starting the
    background thread — should be unreachable by the same reasoning
    documented there, cheap to keep consistent.
  - On success: starts `_run_team_in_background()` on a new thread exactly
    like `/team/resolve` does (same `_team_threads_set()` call), replies
    `{"ok": True, "run_id": run_id}`.

### 4. `POST .../team/stop`: close the no-op gap
Change line 4364's tuple from `("running", "blocked_ask_user")` to
`("running", "blocked_ask_user", "blocked_board_write")` — the entire fix,
one literal added to an existing tuple. `stop_team()` itself already
handles any non-idle status generically (per `docs/implementation.md`'s
own note that `stop_team()`'s terminal-status check needed no part-1
change); this route-level tuple was the only place still missing the new
status.

### 5. Frontend: escalation panel branches on `escalation_kind`
- `renderEscalationPanel(name, team)` (currently unconditionally renders
  the `ask_user` question/options form once `team.waiting_on_you` is true)
  gains a branch on `team.escalation_kind`:
  - `"ask_user"`: **entirely unchanged** — same function body, same
    fetch/cache/render path.
  - `"board_write"`: fetches/caches the same way (`fetchTeamInbox()`,
    `teamInboxCache[runId]`, same `undefined`/`'pending'`/`null`/loaded
    states, same "already resolved" race branch — see "Edge cases"), but
    renders a **board-write proposal panel** instead of a question form:
    - A verb-specific summary line per the three verbs (exact copy is the
      ux-designer's call; the semantic content each needs is listed
      below).
    - `set_status`: "Move **{subject or `#ref`}** from **{current_value.
      status_name}** to **{value}**."
    - `amend_description`: "Replace **{subject or `#ref`}**'s description"
      — with the current (`current_value.description`) and proposed
      (`value`) text shown for comparison (e.g. a two-pane or before/after
      block — long text, see "Edge cases" for length handling).
    - `append_comment`: "Add a comment to **{subject or `#ref`}**:" —
      shows only the proposed `value` (no current-value comparison; `
      current_value` is `{}` for this verb by design).
    - `note` (if non-null): rendered as the lead's own stated reason,
      visually secondary to the verb summary (e.g. "Lead's note: ...").
    - Two buttons, **Approve** and **Reject** — no free-text field (unlike
      `ask_user`'s panel), since `resolve_board_write()` takes no free-text
      argument.
- New `doTeamBoardResolve(name, action)` (parallel to `doTeamResolve()`):
  clears the row's message slot, calls `toggle('team-board-resolve', name,
  true, {action})` — reusing the same TOTP-retry/code-overlay plumbing.
  (`toggle()`'s existing signature/`pendingToggle` shape already carries
  arbitrary extra data through a retry; the developer should confirm the
  exact mechanism `doTeamStart()`'s task-text retry already uses — see
  `actionBody()`'s `team-start` branch reading from a live DOM element as
  a fallback — and pick the matching approach for `action` rather than
  inventing a third one.)
- `actionPath()` gains `if (kind === 'team-board-resolve') return
  '/projects/' + encodeURIComponent(name) + '/team/board-resolve';`.
- `actionBody()` gains `if (kind === 'team-board-resolve') body.action =
  <the approve/reject choice>;` (sourced the same way `team-resolve`
  already sources its answer — from a small client-side map keyed by
  `name`, analogous to `teamEscalationSelected`/`teamEscalationOther`, or
  read directly off the retry context — developer's call on the exact
  plumbing, not architecturally significant).
- `handleActionResult()`'s 428-overlay label switch gains a `kind ===
  'team-board-resolve'` case (e.g. `'Resolving board write: ' + (name ||
  'this')`), following the existing pattern exactly.

### 6. Merged event feed: verb-specific rendering for board_write history
`teamFeedEventKindClass()`/`teamFeedEventBody()` need two new, more
specific branches, checked **before** the existing generic `tool_result`+
`meta.resolved` → `'resolved'` branch (which would otherwise also match a
`board_write_resolved` entry, since its `meta.resolved` is also `True` —
see "Background"):
- `tool_use` with `meta.verb !== undefined` → new class
  `'board-write-proposal'` → body e.g. `'board_write (' + meta.verb + '):
  ref #' + meta.ref + ' — ' + esc(e.text)` (reusing the transcript's own
  `args_summary` text, already verb/ref-specific).
- `tool_result` with `meta.approved !== undefined` (present on both
  approve and reject outcomes, absent from `ask_user`'s own resolution
  meta which only ever sets `resolved`) → new class
  `'board-write-resolved'` → body distinguishes the three real outcomes by
  parsing `e.text`'s known prefixes (`"rejected by human"`, `"approved and
  applied"`, `"approved but Taiga rejected the write: ..."` — these are
  `resolve_board_write()`'s own literal `outcome_summary`/
  `full_result_text` strings, stable enough to branch on) rather than
  reusing the generic `'Answer: ' + text` copy.
- The existing `meta.resolved` → `'resolved'` branch is otherwise
  unchanged and keeps matching `ask_user`'s own resolution entries exactly
  as before (the new checks above are strictly narrower and only match
  when `meta.verb`/`meta.approved` are present, which `ask_user`'s own
  transcript entries never set).

## Affected areas
- `app/app.py`:
  - `/status` handler (`team_status` map, `waiting_on_you`, new
    `escalation_kind` field) — §1.
  - `_handle_team_inbox()` — new `blocked_board_write` branch, subject
    enrichment via `app.taiga_board` — §2.
  - New `POST /projects/<name>/team/board-resolve` route in `do_POST()` —
    §3.
  - `POST .../team/stop` route — one-tuple fix — §4.
  - Frontend JS: `renderTeamStatusStrip()`, `renderEscalationPanel()`, new
    `doTeamBoardResolve()`, `actionPath()`/`actionBody()`/
    `handleActionResult()`, `teamFeedEventKindClass()`/
    `teamFeedEventBody()` — §5, §6.
- Not touched: `app/teams.py`, `app/taiga_board.py` (imported/called only,
  not modified — the inbox route's subject-enrichment call in §2 is a new
  **call site**, not a new function), `scripts/taiga_push_spec.py`.
- New/extended tests:
  - `tests/test_team_routes.py` — `/status`'s new `escalation_kind`
    field and updated `team_status`/`waiting_on_you` for
    `blocked_board_write`; `_handle_team_inbox()`'s new branch (including
    the subject-enrichment success/degraded-failure cases); the new
    `POST .../team/board-resolve` route (success, wrong status, invalid
    action, missing/invalid `run_id`, TOTP gating, already-running-thread
    defensive check); `POST .../team/stop`'s fix, mirroring the existing
    `blocked_ask_user` stop test.
  - `tests/test_team_frontend.js` — `renderEscalationPanel()`'s new
    `board_write` branch for all three verbs plus the "already resolved"
    race case (mirroring the existing `!cached.pending` test for
    `ask_user`, per backlog item 12's precedent); `teamFeedEventKindClass()`/
    `teamFeedEventBody()`'s two new branches, including a regression test
    that a `board_write_resolved` entry no longer falls into the generic
    `'resolved'`/`'Answer: ...'` branch.

## Edge cases
- **`inbox.json` missing/unreadable despite `status ==
  "blocked_board_write"`** (crash between proposal and resolve, or a
  corrupted write): mirrors the existing `ask_user` fallback — still
  `"pending": True`, with `verb`/`ref`/`value` absent/`None` and a safe
  placeholder the panel can render without crashing (e.g. "The team is
  waiting on a board-write approval, but the details could not be read —
  check `tmux attach` or use the CLI's `team-board-resolve` to
  approve/reject blind"). Never a 500.
- **Taiga unreachable when `/team/inbox` tries the subject-enrichment
  read**: degrade to no `subject` field, every other field still present,
  Approve/Reject remain fully functional (the actual apply-time fetch in
  `resolve_board_write()` is independent and already handles this per
  part 1).
- **Two tabs, one already resolved the proposal**: the second tab's
  cached `/team/inbox` response still says `pending: true`; clicking
  Approve/Reject there hits the new route, which reloads state fresh and
  gets `status != "blocked_board_write"` (already `"running"` again) →
  400 → surfaced in the row's `team-msg` slot as an error (same
  `handleActionResult()` 400-branch pattern `doTeamResolve()` already
  uses) — no crash, no silent double-apply. Optionally (developer's call,
  not required for acceptance): mirror the existing `!cached.pending`
  "This question was already answered" render for the `board_write`
  panel too, for the narrower case where a stale cached inbox itself
  already reports resolution before the click — not load-bearing since
  the 400 path alone is sufficient and already covered.
- **Two genuinely concurrent Approve clicks (double-click, or two tabs
  clicking within the same event loop tick)**: backend race safety is
  already part 1's job (`os.replace()` as sole arbiter) — the UI's only
  obligation is to not crash on the loser's 400 and to not double-count
  it as a second real approval. No new backend work; verify the existing
  race test's outcome (one winner, one clean 400) surfaces correctly
  through the new route's error path.
- **`append_comment`'s `current_value` is `{}`**: the panel must render
  without a "current value" comparison for this verb specifically (by
  design — a comment has no prior state to diff against), not show a
  broken/empty comparison block.
- **Very long `value`/`note`/`current_value.description` text** (server
  caps `value` at `TEAM_BOARD_WRITE_VALUE_MAX_CHARS` = 8000 chars; `note`
  and `current_value.description` are not separately capped by this
  spec): the panel must not overflow the row — truncate/scroll, same
  general long-text handling the existing grounding-file listing and
  fact-check match snippets already use elsewhere in this file (200-char
  truncation with an ellipsis is the existing precedent, e.g.
  `teamFeedEventBody()`'s fact-check-result rendering) — exact truncation
  length is a design detail, not an architecture decision.
- **`ref` was deleted from Taiga between proposal and approval**: already
  handled by the backend ("approved but Taiga rejected the write:
  ..."`— §6 covers rendering that outcome legibly in the feed; no new
  handling needed in the panel itself, since the panel's own job (submit
  approve/reject) is already done by the time this surfaces.
- **A run stopped (via the newly-fixed `/team/stop`) while a
  `board_write` is pending**: `stop_team()`'s own existing generic
  terminal-status handling applies unchanged (part 1/`docs/
  implementation.md` already confirmed no `blocked_board_write`-specific
  branch is needed there) — the pending proposal is simply abandoned
  (never applied), same as stopping a run mid-`blocked_ask_user` today.
- **A pre-existing client (a browser tab loaded before this change ships,
  mid-poll)**: `/status`'s new `escalation_kind` field is additive; an old
  cached JS bundle simply never reads it and continues rendering
  `blocked_board_write` as generically "blocked" with no escalation panel
  (today's actual behavior) until the page is reloaded — no crash, no
  data loss, matches this project's existing "additive fields never break
  an old client" discipline (see part 1's own `inbox.json` `kind` field
  reasoning).

## Acceptance criteria
- [ ] Given a run with `state["status"] == "blocked_board_write"`, `GET
      /status` reports that project's `team.status === "blocked"`,
      `team.waiting_on_you === true`, and `team.escalation_kind ===
      "board_write"`.
- [ ] Given a run with `state["status"] == "blocked_ask_user"`, `GET
      /status` reports `team.escalation_kind === "ask_user"` (regression
      check — must not change from today's behavior beyond the new field
      itself).
- [ ] Given a run that is `running`, `finished`, `error`, or has no run at
      all, `team.escalation_kind` is `null`/absent.
- [ ] Given a run blocked on `blocked_board_write`, `GET .../team/
      inbox?run_id=<id>` returns `{"pending": true, "kind": "board_write",
      "run_id", "verb", "ref", "value", "note", "current_value",
      "proposed_at"}` matching `inbox.json`'s own content, plus `subject`
      when the Taiga read succeeds.
- [ ] Given the same run but Taiga unreachable for the subject-enrichment
      read, `GET .../team/inbox` still returns `pending: true` with every
      `inbox.json`-sourced field present and `subject` omitted — not a 500.
- [ ] Given a run blocked on `blocked_ask_user`, `GET .../team/inbox`'s
      response shape is byte-for-byte unchanged from today (regression).
- [ ] Given a run blocked on `blocked_board_write`, `POST /projects/<name>/
      team/board-resolve` with `{"run_id": id, "action": "approve", "code":
      <valid TOTP>}` returns 200 `{"ok": true, "run_id": id}`, calls
      `teams.resolve_board_write(run_id, "approve")` exactly once, and
      starts the background driving thread (mirroring `/team/resolve`'s
      own dispatch — verified via the same thread-registration check
      `test_team_routes.py`'s existing `/team/resolve` tests use).
- [ ] Same for `"action": "reject"`.
- [ ] Given a run whose status is not `blocked_board_write` when `POST
      .../team/board-resolve` is called, the route responds 400 with a
      clear error, makes zero calls to `resolve_board_write()`, and starts
      no thread.
- [ ] Given `"action"` is neither `"approve"` nor `"reject"`, the route
      responds 400 before calling `resolve_board_write()`.
- [ ] Given a session that hasn't cleared the TOTP gate yet, `POST .../
      team/board-resolve` (like every other mutating route) responds 428
      with no `code`, and 403 with a wrong `code` — same shared gate,
      verified once for this new route the same way `/team/resolve`'s own
      TOTP tests are structured.
- [ ] Given a run blocked on `blocked_board_write`, `POST .../team/stop`
      now actually stops it: the project's cancel event (if a live thread
      is registered) is set, `teams.stop_team()` is called, and the
      response is `{"ok": true, "session_removed", "worktrees"}` — not the
      former no-op `{"ok": true, "message": "no team currently running..."}`.
      Existing `blocked_ask_user`/`running` stop behavior is unchanged
      (regression check).
- [ ] `renderEscalationPanel()`, given `team.escalation_kind ===
      'board_write'` and a cached `set_status`/`amend_description`/
      `append_comment` inbox response, renders a panel containing the
      verb-appropriate current-vs-proposed content (or, for
      `append_comment`, just the proposed comment text) and Approve/Reject
      controls — no free-text field. Given `team.escalation_kind ===
      'ask_user'`, `renderEscalationPanel()`'s output is unchanged from
      today (regression).
- [ ] `doTeamBoardResolve(name, 'approve')`/`doTeamBoardResolve(name,
      'reject')` each dispatch a `POST .../team/board-resolve` with the
      corresponding `action`, reusing the same TOTP-retry/code-overlay
      flow `doTeamResolve()` already uses (verified via the same kind of
      428-then-retry-with-code test `test_team_frontend.js` already has
      for `team-resolve`/`team-start`).
- [ ] `teamFeedEventKindClass()`, given a transcript event with `kind ===
      'tool_use'` and `meta.verb` present, returns a distinct class (not
      `'tool_use'`/generic fallback); given `kind === 'tool_result'` and
      `meta.approved` present, returns a distinct class (not `'resolved'`)
      — and a regression test confirms an `ask_user`-shaped `tool_result`
      with only `meta.resolved` (no `meta.approved`) still returns
      `'resolved'` exactly as before.
- [ ] Full existing suite (`python3 -m unittest discover -s tests` plus
      the JS test runner for `test_team_frontend.js`) remains green.

## Open questions
- **Exact copy/iconography for the status strip and panel per verb.**
  Deliberately left to the ux-designer (`docs/design.md`) — this spec
  specifies the *information* each verb's panel must convey (current vs.
  proposed value, or just proposed for `append_comment`; the lead's
  `note`; the two action buttons) but not wording, layout, or color,
  consistent with how 6f part 2's own spec left `ask_user`'s panel copy
  to design.
- **Whether `doTeamBoardResolve()`'s TOTP-retry plumbing needs `toggle()`
  itself extended, or can piggyback entirely on the existing
  `pendingToggle`/`actionBody()` mechanism.** Flagged in "Proposed
  approach" §5 as a developer judgment call during implementation — the
  existing `team-start` kind already threads an extra body field
  (`task`/`lead`/`members`) through a 428 retry via a DOM/client-side-map
  read rather than `toggle()`'s own call signature, and `action` should
  follow whichever of those two patterns turns out simpler once the
  developer is looking at the actual retry code path — not an
  architecture decision that needs a human call first.
- **Exact truncation length for long `value`/`note`/`description` text in
  the panel.** Proposed default: reuse the existing 200-char precedent
  from `teamFeedEventBody()`'s fact-check-match rendering, unless the
  ux-designer has a reason to size it differently for a proposal panel
  (which arguably deserves to show more context than a feed line, being a
  one-at-a-time approval decision rather than a scrolling log). Not a
  blocker either way.

## Risk / rollback notes
- Every backend change here is either purely additive (`escalation_kind`
  field, new `blocked_board_write` branch in `_handle_team_inbox()`, new
  `board-resolve` route) or a one-tuple extension to an existing status
  check (`/team/stop`'s fix) — no existing `ask_user` code path is
  modified except where explicitly noted (§6's event-feed classification
  order, which only narrows two existing checks, never widens or removes
  them).
- The new Taiga read in `_handle_team_inbox()` (§2, subject enrichment)
  is the one genuinely new network call this spec introduces on the web
  side. It's read-only, best-effort, and failure-tolerant by design (see
  "Edge cases") — worst case on a Taiga outage is a missing card title in
  the panel, never a broken approval flow (approval itself doesn't depend
  on this call at all — `resolve_board_write()` does its own fresh fetch
  independently).
- Rollback: revert the `app/app.py` diff. No data migration — `inbox.json`
  and `run.json`'s `blocked_board_write` status already exist and are
  already being written by part 1 regardless of whether this part ships;
  reverting this part only removes the web-visible surface, not the
  underlying capability (the CLI's `team-board-resolve` keeps working
  either way, per part 1's own design).
