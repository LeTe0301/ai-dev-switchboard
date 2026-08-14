# Spec: Backlog item 7 part 1 — board_read/board_write on the lead loop (backend)

## Summary
Give the team lead in `app/teams.py` two new tools, `board_read` and
`board_write`, that let it read the project's Taiga kanban board and propose
changes to it; `board_write` never calls Taiga directly — it queues a
proposal in a generalized version of 6f's escalation inbox, blocking the run
(a new `blocked_board_write` status, parallel to `blocked_ask_user`) until a
human approves or rejects it via a new CLI/route entry point, mirroring how
`resolve_ask_user()` already gates `ask_user`. This is **part 1 of 2**
(backend + CLI only, no web UI yet) — see "Why this is split" below.

## Why this is split into two parts
"Affected areas" for the full backlog item spans four architectural layers:
(1) the lead-loop tool contract in `app/teams.py`, (2) the escalation-inbox
data model (`inbox.json`'s shape, a new run status, a new resolve function),
(3) a brand-new Taiga API client module, and (4) `app/app.py`'s web routes
plus the Teams page approval UI. That's the same shape skill 11's
load-balancing rule exists for, and this project has already solved the
identical problem once before for the *existing* escalation inbox: 6f part 1
shipped (1)+(2)+(3)-equivalent (events/inbox backend, CLI-drivable, zero web
UI) and only then, in a **separate follow-up cycle**, 6f part 2 added the
web routes and Teams page rendering on top of the already-shipped-and-
reviewed backend shape. This spec follows that exact precedent: part 1 here
delivers a fully working, CLI-testable board proposal/approval loop (no
`app.py` changes at all); part 2 (a future product-manager cycle, written
after this one is built and reviewed, the same way 6f part 2's spec was
written only after 6f part 1 shipped) adds `POST .../team/board-resolve`,
extends `GET .../team/inbox`, and builds the Teams page approval panel.
Writing part 2's spec now would mean guessing at part 1's exact shipped
field names/JSON shape instead of pointing at them directly — the same
reason 6f didn't spec both parts up front.

## Goals
- A lead can call `board_read(ref=None, query=None)` to look up Taiga
  userstories on the project's configured board — either one specific card
  by its human-facing `ref` number, or a bounded list matching a substring
  query, or (no args) the most recently modified cards — so it has real
  board state to reason about before proposing anything.
- A lead can call `board_write(verb, ref, value, note=None)` to propose one
  narrow, named change to one existing card. This call never reaches Taiga's
  API itself — it writes a proposal into the run's `inbox.json` (extended
  with a `kind` discriminator) and sets `state["status"] =
  "blocked_board_write"`, halting the run exactly the way `ask_user`
  already halts it on `blocked_ask_user`.
- A human (today: the CLI; the web UI is part 2) can approve or reject a
  pending board-write proposal. Approval is the **only** code path that
  actually calls Taiga's write API; rejection or approval both resume the
  lead loop, feeding the outcome (including any Taiga-side failure or
  conflict) back into the round history so the lead can react to it next
  round, the same way a failed `delegate` or a `found: false` `fact_check`
  already flow back as ordinary round outcomes rather than crashes.
- Credentials and target-project resolution reuse item 1b's existing
  `~/.config/ai-dev-switchboard/taiga-push.env` (see "Taiga credentials and
  project resolution" below) — no new credentials file, no duplicate setup
  step for an operator who already ran `scripts/taiga-configure-push.sh`.
- Grounding's existing read-only guards (6b's runtime monkeypatch + the
  static AST scan in `tests/test_teams_grounding.py`) are untouched. Board
  access is implemented as entirely new functions, never added to
  `_GROUNDING_FUNCS` or called from inside any grounding-section function —
  see "Keeping grounding and board access as separate paths" below.

## Non-goals
- Web routes and the Teams page approval UI (part 2, future cycle).
- Inline editing of a pending proposal before approval — approval is
  literally one click (approve) or one click (reject); to get a different
  value onto the board, a human either rejects and lets the lead re-propose,
  or edits the card directly in Taiga's own UI. (The backlog item calls this
  "one-click approval" — an edit affordance would need its own form and is
  a bigger surface than that phrase implies.)
- Batching multiple pending board-write proposals in one run. Exactly one
  pending item (an `ask_user` *or* a `board_write`) at a time, per run —
  the same invariant `launch_team()`/`team_run()` already enforce for
  `ask_user` (a run can only ever have one active escalation), just now
  covering two escalation *kinds* instead of one.
- A general "call any Taiga endpoint" passthrough. Exactly three write
  verbs (see "Verb set" below) plus one read tool.
- Moving a card between **milestones/sprints** (Taiga's `milestone` field).
  Only `status` (which is what actually moves a card between Kanban
  columns), `description`, and comments are in scope — see "Verb set".
- Audit logging beyond what already exists. See "Settling the backlog
  item's open questions" — the existing per-run history/transcript already
  covers this; no new logging mechanism.
- Any change to `scripts/taiga_push_spec.py` (item 1b) itself. That script
  stays exactly what it is today, a one-way one-shot CLI push; this spec's
  new Taiga client is a separate, importable module used by `app/teams.py`
  and (part 2) `app/app.py`, not a refactor of the existing script.
- Concurrent-team-per-project or cross-project board access. A team run is
  still scoped to exactly one project folder (`docs/story.md` §3's
  standing non-goal), and `board_write`'s target Taiga *project* resolves
  the same way for every run of that switchboard project (see "project
  resolution" below) — this does not add any new cross-project capability.

## Background / current state

### The existing four-tool lead loop (`app/teams.py`)
- `_LEAD_TOOL_NAMES = ("delegate", "fact_check", "ask_user", "finish")`
  (line 1790) is the single source of truth for valid tool names, read by
  the tier-2 JSON schema (`_TIER2_LEAD_SCHEMA`, line 2051) and by
  `_validate_lead_action()`.
- `_lead_tools(team_members)` (line 2015) builds tier 1's native
  OpenAI-style `tools` array — one `{"type": "function", "function": {...}}`
  entry per tool, JSON-schema-typed parameters.
- `_tool_prose(team_members)` (line 2106) builds tier 2/3's prose tool list
  (tier 1 gets its descriptions for free from the native `tools` array and
  doesn't need this restated — see its docstring).
- `_LEAD_TOOL_REQUIRED_ARGS` (line 2387) maps each tool name to its
  required `(key, type)` pairs, consumed by `_validate_lead_action()` (line
  2395), which returns a shaped `{"ok": True/False, ...}` result — never
  raises for malformed model output, since `raw` is untrusted. Two outcome
  families: a malformed shape counts against
  `TEAM_LEAD_MALFORMED_RETRY_BUDGET`; a valid shape rejected on a business
  rule (e.g. `agent_not_on_team`) does not.
- `team_step()` (line 2691) is the one-round dispatcher: after validation,
  it branches on `tool` (`delegate` / `fact_check` / `ask_user` / `finish`),
  each branch calling the underlying mechanism, appending a round-history
  entry via `_append_history()`, and persisting state via `_persist()`.
- The `ask_user` branch (line 2842) is the direct precedent for
  `board_write`'s blocking shape: it calls `_write_inbox(state, args)`
  (line 2570, writes `inbox.json` via a tmp-file + `os.replace()`), then
  sets `state["status"] = "blocked_ask_user"`. `team_run()`'s own loop
  (line 2884) simply stops driving further rounds once `status` leaves
  `"running"` — no separate "stop" signal needed, `board_write` gets this
  for free by setting a new terminal-for-now status the same way.
- `resolve_ask_user(run_id, answer)` (line 3777) is the resume half: reloads
  state fresh from disk (never trusts a caller-supplied state dict, so two
  concurrent resolves for the same run race safely), atomically
  `os.replace()`s `inbox.json` → `inbox.resolved.json` (the sole arbiter of
  "who won" the race — see its docstring for two previous races found and
  fixed here across 6f part 1 and 6f part 1b), appends a history/transcript
  entry **only after** the replace has already succeeded (so a losing
  concurrent caller leaves zero trace), flips `status` back to `"running"`,
  and persists. `POST /projects/<name>/team/resolve` in `app/app.py`
  (line 4372) is the thin route wrapper: validates `run_id`/status/answer
  length, calls `resolve_ask_user()`, then starts a background thread
  running `_run_team_in_background()` to actually resume driving the loop
  (mirrors `/team/start`'s own non-blocking dispatch).
- `_system_framing()` (line 2125) builds the lead's system prompt every
  round (grounding digest + role framing + the two required prompt-level
  mitigation clauses, `_FACT_CHECK_MITIGATION` and
  `_DELEGATION_HISTORY_MITIGATION`) — this is where a new required
  board-write framing clause goes (see "Proposed approach").

### 6f's escalation inbox machinery (already backend + UI, reused here)
- `inbox.json` today (`_write_inbox`, line 2570) is exactly one shape:
  `{"question": str, "header": str (<=12 chars), "options": [{"label",
  "description"}], "multi_select": bool}`. Present on disk **iff**
  `state["status"] == "blocked_ask_user"` (per §11's persistence
  discipline, noted in `_force_ask_user()`'s docstring, line 2589).
- `GET /projects/<name>/team/inbox` (`_handle_team_inbox`, `app/app.py`
  line 4167) replies `{"pending": false}` unless status is
  `blocked_ask_user`, else reads `inbox.json` and replies `{"pending": true,
  "run_id", "question", "header", "options", "multi_select"}`.
- `POST /projects/<name>/team/resolve` (`app/app.py` line 4372) validates
  `run_id` (via `teams._RUN_ID_RE`, closing backlog item 11(b)'s
  path-traversal gap), loads state, checks `status == "blocked_ask_user"`,
  validates the answer's length against
  `teams.TEAM_ASK_USER_ANSWER_MAX_CHARS`, calls `teams.resolve_ask_user()`,
  then starts the background driving thread.
- The Teams page (`app/app.py`'s JS, `teamRow()` line 2683,
  `renderEscalationPanel()` line 2408) fetches `/team/inbox` once per
  `run_id` and caches it client-side (`teamInboxCache`), renders a
  `<fieldset>`/radio-or-checkbox panel from `options[]`, and posts the
  chosen answer via `doTeamResolve()` → `actionBody('team-resolve', ...)`.
  None of this is touched by part 1 of this spec — part 2 extends it.
- **`inbox.json`'s current shape is not generic enough for a board-write
  proposal** (a proposal needs a target card, a proposed change, and the
  value being replaced — none of which fit "question + pickable options").
  It needs a `kind` discriminator and a second, board-write-shaped sibling
  of the existing fields — see "Proposed approach".

### Item 1b's Taiga REST API pattern (`scripts/taiga_push_spec.py`)
- Stdlib-only (`urllib.request`), no `requests` dependency.
- One shared request helper, `_taiga_request(base_url, method, path, token,
  body)` (line 72), raising `TaigaHTTPError`/`TaigaConnectionError` on
  failure — the single seam the test suite (`tests/test_taiga_push.py`)
  monkeypatches.
- Auth: `POST /api/v1/auth {"type": "normal", "username", "password"} ->
  auth_token`; a fresh token is exchanged on every invocation, no caching
  (`_authenticate`, line 162).
- Project resolution: `GET /api/v1/projects/by_slug?slug=<slug> ->
  {"id": ...}` (`_lookup_project`, line 177).
- Write: `POST /api/v1/userstories {"project": id, "subject", "description"}
  -> {"id", "ref", ...}` (`_create_userstory`, line 192).
- Credentials live in `~/.config/ai-dev-switchboard/taiga-push.env`
  (`DEFAULT_CONFIG_PATH`, line 32), a plain `KEY=value` file created
  interactively by `scripts/taiga-configure-push.sh`, containing
  `TAIGA_URL`, `TAIGA_USERNAME`, `TAIGA_PASSWORD`, `TAIGA_PROJECT_SLUG`.
  `_load_config()` (line 121) parses it manually (same
  `engines.d/*.engine`-style KEY=value convention as
  `_parse_engine_file()` in `app.py`, not `configparser`). File permission
  is checked and warned on (not hard-blocked) if looser than `600`
  (`_check_config_permissions`, line 145).
- **This file already holds exactly the credentials + target-project slug
  board access needs.** See "Taiga credentials and project resolution"
  below for why this spec reuses it directly instead of introducing a
  second credentials file.

### 6b's grounding read-only guards
- Runtime: `builtins.open` is monkeypatched during grounding tests to
  reject any non-read mode; every mutating `os`/`shutil` function is
  monkeypatched to raise if called, exercised against real and adversarial
  fixtures (`tests/test_teams_grounding.py`).
- Static: `GroundingStaticASTScanTests` (`tests/test_teams_grounding.py`
  line 841) `ast.parse()`s `app/teams.py` and, for a **fixed, named list**
  of grounding-section function defs (`_GROUNDING_FUNCS`, line 842 — 12
  named functions, e.g. `_discover_and_read`, `load_grounding`,
  `fact_check`), asserts no `open()` call inside them uses a write-capable
  mode literal, no call targets a mutating `os`/`shutil` function, and no
  `os.open()` call requests a write-capable flag.
- **This scan is scoped to a fixed function-name allowlist, not "every
  function in `app/teams.py`".** Board access is implemented as entirely
  new functions with new names, never added to `_GROUNDING_FUNCS` and never
  called from inside any of the 12 listed functions — so the existing scan
  simply doesn't apply to them, with **zero changes needed to the guard
  itself**. This is the "keep them as separate paths" requirement, already
  satisfied by the guard's own existing scoping — see "Proposed approach".

## Proposed approach

### 1. New Taiga client module: `app/taiga_board.py`
A new, importable module (not a CLI script — needed both from
`team_step()`, synchronously, mid-request-thread, and later from part 2's
web route) providing the narrow surface board access needs, built on the
exact same `urllib.request` pattern as `scripts/taiga_push_spec.py` (a
fresh token per call, no caching, same `TaigaPushError`/`TaigaHTTPError`/
`TaigaConnectionError` exception shapes — copy, don't import, from
`scripts/taiga_push_spec.py`, since that script is deliberately
self-contained/stdlib-only and this module needs to live under `app/` for
`app/teams.py` to import it without a `sys.path` reach into `scripts/`).

```python
def load_config(path=DEFAULT_CONFIG_PATH) -> dict           # same KEY=value parse as taiga_push_spec._load_config
def authenticate(base_url, username, password) -> str        # POST /api/v1/auth -> token
def lookup_project(base_url, token, slug) -> int              # GET .../by_slug -> id
def get_userstory(base_url, token, project_id, ref) -> dict   # GET /api/v1/userstories/by_ref?ref=&project=
def list_userstories(base_url, token, project_id, query=None, limit=10) -> list
def set_status(base_url, token, us_id, version, status_id) -> dict   # PATCH userstories/{id}
def amend_description(base_url, token, us_id, version, description) -> dict  # PATCH userstories/{id}
def append_comment(base_url, token, us_id, version, comment) -> dict  # PATCH userstories/{id} {"comment": ...}
```

Taiga's REST API requires the object's current `version` field on every
`PATCH`/`PUT` to a userstory, specifically for optimistic-concurrency
control — a `PATCH` carrying a stale `version` is rejected rather than
silently overwriting a concurrent edit. **The developer must confirm this
exact mechanic against Taiga's own REST API docs (or a live instance)
during implementation** — it's the intended mechanism for "what happens
when a card the lead is mid-edit was changed by the human concurrently"
(see "Settling the backlog item's open questions" below), but this spec
does not have a live Taiga instance to verify the precise error shape
against, so treat the *exact* rejection status/body as an implementation
detail to confirm, not a settled fact. Every `set_status`/
`amend_description`/`append_comment` call above takes `version` as a
required argument, fetched fresh (via `get_userstory`) **immediately
before** the call — never reused from an earlier `board_read` or from the
original `board_write` proposal, so genuine concurrent edits are actually
caught rather than rendered moot by a stale snapshot.

`status_id` for `set_status`: Taiga's kanban columns are themselves
`status` values (a project's configured workflow statuses), each with a
numeric id — `list_userstories`/`get_userstory` should surface each
userstory's status as both its numeric id and its human-readable name
(e.g. `{"status_id": 3, "status_name": "In progress"}`) so `board_write`'s
proposal can be recorded/displayed in the human-readable form while the
apply step uses the id.

### 2. Taiga credentials and project resolution
Reuses `~/.config/ai-dev-switchboard/taiga-push.env` byte-for-byte — same
path, same four keys (`TAIGA_URL`, `TAIGA_USERNAME`, `TAIGA_PASSWORD`,
`TAIGA_PROJECT_SLUG`), same `scripts/taiga-configure-push.sh` setup step.
No new credentials file, no new setup script. If that file doesn't exist
(Taiga push was never configured), `board_read`/`board_write` fail with a
clear "Taiga isn't configured — run scripts/taiga-configure-push.sh first"
result fed back to the lead as an ordinary tool failure (same shape as a
`fact_check` miss — see "Edge cases"), not a crash.

This directly resolves item 1's own long-open question ("does one Taiga
project cover all switchboard projects, or one per project folder") **for
board access specifically**: it inherits 1b's already-shipped default —
one shared `TAIGA_PROJECT_SLUG` from the config file, used for every
switchboard project's board access identically. This is a deliberate,
minimal-surface choice, not a new architectural decision: it's the
resolution 1b's own shipped code already performs today, just read from
`app/teams.py` instead of only from the CLI script. A future session could
still add a per-switchboard-project override (the same way
`taiga_push_spec.py`'s own `--project` flag already overrides the config
default for one invocation) if a real user hits the need for per-project
boards — flagged below under "Open questions" as still-open at that finer
grain, not re-opened here.

### 3. Lead-loop tool contract additions (`app/teams.py`)
- `_LEAD_TOOL_NAMES = ("delegate", "fact_check", "ask_user", "board_read",
  "board_write", "finish")`.
- `_LEAD_TOOL_REQUIRED_ARGS` gains:
  ```python
  "board_read": (),  # every arg optional; special-cased in _validate_lead_action
  "board_write": (("verb", str), ("ref", int), ("value", str)),
  ```
  `board_read`'s args are all optional (`ref: int`, `query: str`), so it
  needs the same kind of light special-casing `ask_user` already gets for
  its optional `header`/`multi_select` (see `_validate_lead_action()` line
  2438) rather than forcing a required key that has no natural default.
- `_validate_lead_action()` gains:
  - `board_write.verb` must be one of `{"set_status", "amend_description",
    "append_comment"}` — else `unknown_tool`-shaped-but-distinct reason
    `"unknown_verb"` (new category, same "never raises" discipline).
  - `board_write.ref` must be a positive int.
  - `board_read`'s optional `ref`/`query`, if present, type-checked the
    same way `ask_user`'s optional `header`/`multi_select` already are.
- `_lead_tools()` gains two `{"type": "function", ...}` entries (tier 1's
  native schema) and `_tool_prose()` gains two matching prose lines (tier
  2/3), following the exact structure of the existing four.
- `_system_framing()` gains a new required clause (same "verbatim or
  materially equivalent" discipline as `_FACT_CHECK_MITIGATION`/
  `_DELEGATION_HISTORY_MITIGATION`, both defined right above `_tool_prose`,
  lines 2065/2095):
  ```
  You also have read/write access to this project's Taiga kanban board via
  board_read and board_write. board_write does NOT apply your change --
  every board_write call is queued as a pending proposal and takes effect
  ONLY after a human explicitly approves it. Its tool_result tells you the
  proposal was queued, never that the board was actually changed -- do not
  assume a board_write call has succeeded, and do not repeat an identical
  board_write call while a prior one is still pending (a run can only have
  one board write pending approval at a time; a second board_write call
  while one is already pending will be rejected).
  ```
  This mirrors `_DELEGATION_HISTORY_MITIGATION`'s own precedent: state the
  thing the model must not assume explicitly and saliently, don't leave it
  to be inferred.
- `team_step()` gains two new branches:
  - **`board_read`**: calls `taiga_board.get_userstory(...)` (if `ref`
    given) or `taiga_board.list_userstories(...)` (if `query` given or no
    args), catching `TaigaPushError` subclasses and turning them into an
    ordinary round outcome (`outcome_summary=f"Taiga error: {e}"`,
    `action_count` still incremented) — never raises. Executes and
    persists in the same round, exactly like `fact_check` (no blocking).
  - **`board_write`**: builds the proposal (verb, ref, value, note, plus a
    `current_value` snapshot read via one `get_userstory()` call made at
    proposal time — used only for **display** in the eventual approval UI,
    never reused as the `version` passed to the actual write, per the
    optimistic-concurrency point above), calls a new `_write_board_inbox()`
    (parallel to `_write_inbox()`), sets `state["status"] =
    "blocked_board_write"`, appends a history entry
    (`outcome_summary="blocked, awaiting board-write approval"`), persists,
    returns. If `state["status"]` is already `blocked_board_write` or
    `blocked_ask_user` (should be unreachable given `team_run()`'s own
    loop-exit-on-non-running discipline, but cheap to assert defensively —
    same posture as the "at most one non-terminal run" check in
    `app/app.py`'s `/team/resolve` route) reject with the business-rule
    (not malformed) outcome family, matching `agent_not_on_team`'s
    precedent.
  - If a Taiga API error occurs while building the `current_value` snapshot
    for a `board_write` proposal (network down, bad `ref`), the proposal is
    never queued — fed back as an ordinary round outcome (like a
    `fact_check` miss) so the lead can retry or `ask_user` instead, exactly
    the same "a tool failing doesn't mean the loop escalates on its own"
    posture the rest of this loop already has.

### 4. Escalation inbox generalization (`app/teams.py`)
- `inbox.json` gains a `"kind"` field: `"ask_user"` (existing shape,
  `kind` added but every other field byte-for-byte unchanged) or
  `"board_write"` (new shape):
  ```json
  {"kind": "board_write", "verb": "set_status", "ref": 42,
   "value": "In progress", "note": "moving to in-progress per delegate result",
   "current_value": {"status_name": "New", "status_id": 1},
   "proposed_at": "2026-08-14T12:00:00Z"}
  ```
  A `GET .../team/inbox` reader that predates this change (none exist yet
  in production, but as a matter of forward compatibility) treats a
  missing `kind` as `"ask_user"` — the existing shape is a strict subset
  of the new one.
- `_write_inbox()` is renamed `_write_ask_user_inbox()` (adds
  `"kind": "ask_user"` to its written dict; every other field unchanged) —
  update its two existing call sites (`_force_ask_user`, `team_step`'s
  `ask_user` branch). A new sibling `_write_board_inbox(state, verb, ref,
  value, note, current_value)` writes the second shape above, same
  tmp-file + `os.replace()` pattern, same `_inbox_path()`.
- New status value `"blocked_board_write"`, parallel to
  `"blocked_ask_user"` in every place status is enumerated/checked
  (`team_run()`'s loop-exit condition already checks `status != "running"`
  generically, so it needs no change; `_recover_in_progress()`,
  `latest_run_for_project()`, and any other status-aware helper that
  currently special-cases `"blocked_ask_user"` by name needs an audit —
  the developer should `grep -n 'blocked_ask_user' app/teams.py app/app.py`
  before starting and update every hit that's status-shape-specific rather
  than generically "not running").
- New `resolve_board_write(run_id, action) -> dict` (`action` is
  `"approve"` or `"reject"`), living right next to `resolve_ask_user()` and
  reusing its exact race-safety shape (reload state fresh from disk, never
  trust a caller-supplied dict; atomic `os.replace(inbox_path,
  inbox_resolved_path)` as the sole arbiter of who won a concurrent-resolve
  race; append the history/transcript entry only **after** the replace has
  already succeeded, so a losing caller leaves zero trace — see
  `resolve_ask_user()`'s docstring, line 3777, for exactly why each of
  these three properties matters and what broke before they were added).
  - `status != "blocked_board_write"` → `{"ok": False, "error": ...}`,
    same shape/wording convention as `resolve_ask_user`'s own two error
    messages.
  - `action == "reject"`: no Taiga call. History entry:
    `tool="board_write_resolved"`, `outcome_summary="rejected by human"`.
  - `action == "approve"`: re-reads the inbox's `verb`/`ref`/`value` (from
    the now-atomically-claimed `inbox.json`, before it's moved), calls
    `taiga_board.get_userstory()` for a **fresh** `version` (never the
    proposal-time snapshot), then the matching `taiga_board.set_status`/
    `amend_description`/`append_comment` call. On success: history entry
    `outcome_summary="approved and applied"`, `full_result_text` includes
    the Taiga response. On a Taiga-side failure (network error, stale
    `version`/conflict, vanished `ref`): history entry
    `outcome_summary=f"approved but Taiga rejected the write: {detail}"`
    — **the inbox is still resolved and the run still resumes** (never
    left permanently stuck on a Taiga outage or a conflict a human already
    approved past) — the lead sees the failure next round and can decide
    to `board_read` again and re-propose, `ask_user`, or move on, the same
    posture a failed `delegate` already gets.
  - Either branch: `state["status"] = "running"`, persist, return
    `{"ok": True, "state": state}` — identical return shape to
    `resolve_ask_user()` so a future caller (part 2's route, or a CLI
    driver) can reuse the exact same "resume driving the loop" dispatch
    both already share.

### 5. CLI (`app/teams.py`'s `_parse_args`/`_cli_*`)
New `team-board-resolve` subcommand, parallel to the existing
`team-resolve` (`_cli_team_resolve`, line 4023): `--run-id`, `--action
{approve,reject}`. Calls `resolve_board_write()`, then (mirroring
`_cli_team_resolve`'s own "block in the foreground and drive the resumed
run to its next stopping point" behavior, per `_drive_and_report()`'s
docstring at line 3952) drives the resumed run the same way. This is what
makes part 1 fully testable end-to-end with zero `app.py`/web changes,
exactly how 6f part 1's own `team-resolve`/`GET .../team/inbox` CLI-first
shape was verified before 6f part 2 added the web routes on top.

## Affected areas
- `app/taiga_board.py` — new module (Taiga client: auth, project/userstory
  lookup, three write calls, all `version`-aware).
- `app/teams.py`:
  - `_LEAD_TOOL_NAMES`, `_LEAD_TOOL_REQUIRED_ARGS`, `_lead_tools()`,
    `_tool_prose()`, `_system_framing()`, `_validate_lead_action()`,
    `team_step()` — tool contract additions (§3 above).
  - `_write_inbox()` → `_write_ask_user_inbox()` (rename + `kind` field),
    new `_write_board_inbox()`, new `"blocked_board_write"` status value
    and every place `"blocked_ask_user"` is currently checked by literal
    string (audit needed — see §4 above), new `resolve_board_write()`.
  - `_parse_args()`/new `_cli_team_board_resolve()` — new
    `team-board-resolve` CLI subcommand.
  - New config constants near the existing `TEAM_*` block (line ~177,
    next to `TEAM_ASK_USER_ANSWER_MAX_CHARS`): none strictly required for
    part 1 (verb set and value-length bounds can be plain constants near
    the new functions, following `_GROUNDING_BLOCK_MAX_LINES`-style local
    placement) — developer's call whether any deserve an env-configurable
    `TEAM_*` constant of their own; not load-bearing either way.
- `tests/test_teams_grounding.py` — **no change expected** (see "Keeping
  grounding and board access as separate paths"); the developer should
  re-run `GroundingStaticASTScanTests` unmodified as a regression check
  that the new functions were never added to `_GROUNDING_FUNCS`.
- New test file, e.g. `tests/test_teams_board.py`, following
  `tests/test_taiga_push.py`'s own "monkeypatch the one shared HTTP
  request function" seam for `app/taiga_board.py`, plus
  `tests/test_teams_grounding.py`/`tests/test_team_routes.py`'s existing
  patterns for exercising `team_step()`/`resolve_board_write()`.
- Not touched: `app/app.py` (no route/UI changes — part 2),
  `scripts/taiga_push_spec.py` (untouched, not reused as a dependency —
  only its patterns are followed, per Non-goals).

## Edge cases
- **No Taiga configured at all** (`taiga-push.env` missing): `board_read`/
  `board_write` fail with a clear, lead-facing error
  ("Taiga isn't configured..."), fed back as an ordinary round outcome —
  never a crash, never silently treated as "no board access tool exists."
- **`board_write` called while one is already pending**: rejected as a
  business-rule outcome (not malformed), same family as
  `agent_not_on_team` — does not consume the malformed-retry budget.
- **`ref` doesn't exist on the board**: `board_read`/the `board_write`
  proposal-time snapshot both surface this as an ordinary tool failure
  (Taiga's own 404), fed back to the lead, no proposal queued.
- **Two concurrent `resolve_board_write()` calls for the same run**
  (double-submit, two tabs — relevant once part 2 exists, but the backend
  must be race-safe from day one since the CLI itself can be invoked
  twice): identical race shape to `resolve_ask_user()`'s own, same fix —
  `os.replace()` is the sole arbiter, loser gets a shaped `{"ok": False}`,
  never an unhandled exception, never a spurious transcript entry.
  Requires a dedicated concurrent-resolve test, mirroring
  `TeamResolveEndpointTests`'s existing two-genuinely-simultaneous-callers
  test for `ask_user`.
- **Taiga rejects the write at approval time** (stale `version`, network
  failure, `ref` deleted since the proposal was made): the inbox still
  resolves, the run still resumes, the failure is recorded in history —
  never leaves a run permanently stuck. See §4.
- **A crash between "proposal queued" and "human resolves it"**: no new
  risk — this is exactly `blocked_ask_user`'s existing crash-recovery
  story (state persisted before returning; `_recover_in_progress()`'s
  general "never assume in-flight work succeeded" discipline already
  covers any status other than a clean resume). Confirm
  `_recover_in_progress()` doesn't need a `blocked_board_write`-specific
  branch (it shouldn't, by the same reasoning `blocked_ask_user` doesn't
  need one today — nothing was "in progress" mid-tool-call, the round
  already finished cleanly before blocking).
- **`value` for `amend_description`/`append_comment` is very large**: cap
  at a fixed max length (e.g. mirror `TEAM_ASK_USER_ANSWER_MAX_CHARS`'s
  2000-char precedent) and reject oversized values the same way
  `_validate_prompt_size()` rejects an oversized prompt elsewhere in this
  file — malformed-shape category, not a crash.
- **`verb` is syntactically valid JSON but not one of the three known
  verbs** (e.g. the lead invents `"move_card"` literally, since that's the
  backlog item's own English phrasing): rejected with a clear
  `unknown_verb` detail that names the three valid verbs, same "clear,
  actionable, re-promptable" discipline every other malformed-shape
  rejection already has — see "Verb set naming" below for why `set_status`
  is the correct verb for what the backlog calls "move card."

## Verb set naming (resolving the backlog's "confirm the narrow set" ask)
The backlog item lists four candidate verbs: "move card, set status,
append a comment, amend a description." In Taiga's actual data model, a
Kanban board's columns **are** a userstory's `status` values — moving a
card between columns and changing its status are the same underlying API
call (`PATCH` the `status` field). Listing both as separate verbs would
mean two tool names that do the identical Taiga write, which this
project's existing four-tool loop deliberately avoids (every tool maps to
exactly one distinct action). This spec therefore settles on **three**
verbs — `set_status` (covers "move card"), `amend_description`,
`append_comment` — and documents `set_status` in the lead-facing tool
prose as covering both ("moves the card between Kanban columns by
changing its status"), so the lead doesn't need to know this is a Taiga
data-model detail. This is a resolved design decision, not left open,
because it's grounded directly in Taiga's own object model rather than a
product preference that needs a human call.

## Acceptance criteria
- [ ] Given a run with Taiga configured, when the lead calls
      `board_read(ref=42)`, then `team_step()` returns within the same
      round with that userstory's subject/status/description in
      `full_result_text`, `action_count` incremented, and `state["status"]`
      unchanged (`"running"`).
- [ ] Given a run with Taiga configured, when the lead calls
      `board_write(verb="set_status", ref=42, value="In progress")`, then
      `team_step()` sets `state["status"] = "blocked_board_write"`,
      `inbox.json` contains `{"kind": "board_write", "verb": "set_status",
      "ref": 42, "value": "In progress", ...}`, and no Taiga API write call
      has been made (verified via the test's monkeypatched request seam
      recording zero PATCH calls).
- [ ] Given a run blocked on `blocked_board_write`, when
      `resolve_board_write(run_id, "approve")` is called, then exactly one
      Taiga `PATCH` call is made with a freshly-fetched `version` (not the
      proposal-time snapshot), `inbox.json` is moved to
      `inbox.resolved.json`, `state["status"]` returns to `"running"`, and
      a history entry records the outcome.
- [ ] Given a run blocked on `blocked_board_write`, when
      `resolve_board_write(run_id, "reject")` is called, then zero Taiga
      API calls are made, the inbox is still resolved, and
      `state["status"]` returns to `"running"`.
- [ ] Given a run blocked on `blocked_board_write`, when Taiga's PATCH call
      fails (monkeypatched to raise `TaigaHTTPError`) during an "approve"
      resolution, then the inbox still resolves, `state["status"]` still
      returns to `"running"`, and the failure detail is present in the new
      history entry's `outcome_summary`.
- [ ] Given two genuinely simultaneous `resolve_board_write()` calls for
      the same `run_id` (thread-based test, mirroring
      `TeamResolveEndpointTests`'s existing `ask_user` race test), then
      exactly one succeeds, the other returns `{"ok": False, ...}` with no
      unhandled exception and no transcript entry written for the loser.
- [ ] Given a run already blocked on `blocked_board_write` (or
      `blocked_ask_user`), when the lead's next call is another
      `board_write`, then it is rejected as a business-rule outcome (not
      malformed, does not consume the malformed-retry budget) — this
      should be unreachable in practice (the loop doesn't advance while
      blocked) but is defensively tested the same way
      `agent_not_on_team` is.
- [ ] `python3 -m unittest tests.test_teams_grounding
      .GroundingStaticASTScanTests` still passes unmodified — confirms no
      new board-access function was added to `_GROUNDING_FUNCS` and no
      grounding-section function was changed to call into
      `app/taiga_board.py`.
- [ ] `python3 -m app.teams team-board-resolve --run-id <id> --action
      approve` (and `--action reject`) work end-to-end against a real or
      test-double Taiga instance with zero `app.py` involvement, matching
      part 1's "CLI-testable, no web UI yet" scope.
- [ ] Full existing suite (`python3 -m unittest discover -s tests`)
      remains green — no regression to the existing four-tool loop's
      behavior (verified by running the existing `test_team_*.py` files
      unmodified where they don't concern the new tools).

## Open questions
- **Per-switchboard-project Taiga board mapping.** This spec settles
  board access on 1b's existing single-shared-`TAIGA_PROJECT_SLUG`
  default (see "Taiga credentials and project resolution"). Whether a
  future session should add a real per-switchboard-project override (a
  small config extension, not a redesign) is still open — genuinely needs
  a user to say whether they actually run multiple switchboard projects
  against *different* Taiga projects before it's worth building. Proceeding
  under the assumption that the shared-default is fine for now, since
  that's what 1b already shipped and no user has asked for per-project
  boards yet.
- **Taiga's exact optimistic-concurrency error shape.** Assumed (based on
  Taiga's documented `version`-field convention) that a stale `version` on
  `PATCH` is rejected rather than silently overwritten — the developer
  should confirm the precise HTTP status/error body against Taiga's own
  API docs or a live instance early in implementation, since
  `resolve_board_write()`'s "Taiga rejected the write" branch depends on
  being able to actually detect this case, not just theoretically handle
  it.
- **Comment posting mechanics.** `append_comment` is modeled above as a
  `PATCH .../userstories/{id} {"comment": ..., "version": ...}` call,
  matching Taiga's documented comment-via-PATCH convention for this
  resource type. If a live Taiga instance turns out to need a distinct
  endpoint/shape for comments specifically, that's an implementation
  detail to adjust within `app/taiga_board.py`'s `append_comment()` —
  doesn't change this spec's tool contract or acceptance criteria.
- **Value-length cap for `amend_description`.** Proposed 2000 chars
  (matching `TEAM_ASK_USER_ANSWER_MAX_CHARS`'s existing precedent) as a
  starting default; a real userstory description could reasonably be
  longer. Not a blocker — the developer can pick a larger bound (e.g.
  8000, matching `TEAM_GROUNDING_MAX_BYTES`) if 2000 feels too tight
  during implementation; this is a tuning detail, not an architecture
  decision.

## Settling the backlog item's open questions (from `docs/BACKLOG.md` item 7)
- **"Whether board writes are audited to a log the human can review after
  the fact."** Settled: yes, via the mechanism this project already uses
  for everything else in a team run — no new logging mechanism needed.
  Every `board_write` proposal and its `resolve_board_write()` outcome
  (approved+applied, approved-but-Taiga-rejected, or rejected) is recorded
  as a round-history entry (`state["history"]`, persisted in `run.json`)
  and a transcript event (`transcript.jsonl`, already the substrate 6f's
  own event feed renders). A human reviewing a run's full transcript
  already sees every board-write proposal and its resolution, in order,
  the same way they already see every `ask_user`/`ask_user_resolved` pair
  today. Part 2 can surface this in the Teams page event feed with zero
  new backend work, the same way 6f part 2 rendered the existing
  `ask_user` history without needing new persistence.
- **"Whether one shared board covers all switchboard projects or one
  board per project."** Answered for board *access* specifically (not
  reopened for item 1's original push-only case): one shared board,
  inheriting 1b's existing default — see "Taiga credentials and project
  resolution" above and the corresponding "Open questions" entry for the
  narrower still-open follow-up (a per-project override).
- **"What happens when a card the lead is mid-edit was changed by the
  human concurrently."** Answered: Taiga's own `version`-based optimistic
  concurrency control is the mechanism (fetch a fresh `version`
  immediately before every write, never reuse a stale snapshot); a
  conflicting concurrent edit surfaces as a normal "Taiga rejected the
  write" outcome fed back to the lead, not a silent overwrite and not a
  permanently stuck run. See "Open questions" above for the one remaining
  implementation-detail confirmation this needs against a live instance.

## Risk / rollback notes
- Every change here is additive to `app/teams.py` (new tool names, new
  status value, new functions) plus one brand-new module
  (`app/taiga_board.py`) — the existing `delegate`/`fact_check`/
  `ask_user`/`finish` tool behavior is untouched, and every existing test
  in `tests/test_team_*.py`/`tests/test_teams_grounding.py` should keep
  passing unmodified except for the one rename
  (`_write_inbox` → `_write_ask_user_inbox`, if any existing test imports
  it directly by name — grep for that before renaming).
- No web-facing surface changes at all in this part — `app/app.py` is
  untouched, so there is zero user-visible change until part 2 ships. A
  team run that never calls `board_read`/`board_write` behaves
  byte-for-byte as it does today.
- If Taiga is misconfigured or unreachable, the new tools degrade to
  ordinary round failures (never a crash, never a stuck run) — same
  blast radius as a misconfigured `fact_check` grounding set today, not a
  new failure class.
- Rollback: revert the `app/teams.py` diff and delete
  `app/taiga_board.py`; no data migration needed since `inbox.json`'s
  `kind` field is additive and no run in flight today has ever had one
  written with the old shape that would need reinterpreting.
