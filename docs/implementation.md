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
