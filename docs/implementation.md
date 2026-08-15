# Implementation: Upload wizard polish (backlog item 3's deferred follow-ups)

## Summary
Closed out the three small, low-risk polish items backlog item 3 explicitly
deferred at ship time: `UPLOAD_MAX_ENTRIES` is now a real `switchboard.env`
knob (was a bare Python constant), step 5's single/split mode choice renders
as pill-styled labels (CSS-only, real `<input type="radio">` kept underneath)
matching `engineRow`/`codeRow`'s existing pill look, and step 5's "Back"
button is now only rendered in the ambiguous sub-case.

## Root cause
N/A — polish/config item, not a bugfix.

## Changes by file
- `app/app.py`
  - `UPLOAD_MAX_ENTRIES` (~line 84): changed from a bare `20000` constant to
    `int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))`, exact same pattern
    as `UPLOAD_STAGING_TTL_SECONDS`/`GITEA_POLL_INTERVAL_SECONDS`. No
    try/except (matches sibling precedent — a malformed value fails fast and
    loudly at import time, not silently at request time).
  - CSS block (near `.wizard-check-row .info .sub`): added
    `.wizard-check-row.pill-choice` and
    `.wizard-check-row.pill-choice:has(input:checked)`, matching `.pill`/
    `.pill.active`'s padding/border-radius/colors per `docs/design.md`.
  - `renderStep5()`: added the `pill-choice` class to the two mode-choice
    `<label class="wizard-check-row">` elements only (the split-candidate
    checkboxes below stay plain `wizard-check-row`, unstyled).
  - `renderStep5Actions()` → `renderStep5Actions(d)`: now takes the
    `detectResult` object and only emits the "Back" button's HTML when
    `d.ambiguous` is true; "Confirm" is always emitted.
  - `renderWizard()`'s step-5 branch: updated the one call site to
    `renderStep5Actions(wizardState.detectResult)`.
- `config/switchboard.env.example`: replaced the "this is a hardcoded
  constant, setting it here does nothing" comment block with a real
  commented-out `#UPLOAD_MAX_ENTRIES=20000` line plus a one-line description
  of the many-tiny-files zip DoS it guards against, matching
  `#GITEA_POLL_INTERVAL_SECONDS=45`'s style elsewhere in the same file.
- `docs/BACKLOG.md`: struck through item 3's three deferred-polish bullets
  with a note that they shipped in this pass.
- `tests/test_upload.py`: added `UploadMaxEntriesEnvVarTests` (two cases —
  env var set overrides the default, env var unset keeps `20000`). Imports
  `app.py` in a fresh subprocess per case (`sys.executable -c ...`) rather
  than mutating the already-imported `appmod` shared by every other test in
  the module, since the thing under test is specifically the module-import-
  time `os.environ.get(...)` read.
- `tests/test_upload_frontend.js` (new): frontend tests for step 5's pill
  styling and conditional Back button, following
  `tests/test_deploy_frontend.js`'s established pattern — extracts the real
  rendered `<script>` from `app.render_page()` via a Python subprocess and
  runs it in a Node `vm` context against minimal `document`/`fetch` stubs.
  8 tests: pill-choice class present on exactly the 2 mode-choice labels;
  split-candidate checkboxes stay unstyled; checked state follows
  `wizardState.mode` and re-renders correctly after `setWizardMode()`;
  `setWizardMode()` still updates `wizardState.mode` (no regression); the
  mode choice still uses real, focusable `<input type="radio">` (not a bare
  `<span class="pill">`); unambiguous case renders Confirm-only; ambiguous
  case renders Back+Confirm with Back's `resetWizardState(); renderWizard();`
  onclick unchanged; and `renderStep5Actions(d)`'s new signature returns the
  right HTML directly for both `d.ambiguous` values.

## Key decisions / tradeoffs
- Kept the native `<input type="radio">` per spec/design — the pill look is
  purely a `<label>`-level CSS restyle (`pill-choice` class), not a
  `engineRow`/`codeRow`-style bare-span replacement. This preserves Tab/
  arrow-key/Enter/Space native radio semantics and screen-reader
  announcement, matching the spec's explicit accessibility requirement.
- Used CSS `:has()` for the checked-state pill styling, per design.md's
  primary recommendation — no compatibility issue found (this is a modern,
  single-page app with no stated legacy-browser policy, and `:has()` is
  well-supported in the browsers this app already implicitly targets), so
  the `onchange`-driven `classList.toggle` fallback design.md offered as a
  backup was not needed.
- `UPLOAD_MAX_ENTRIES` parsing intentionally has no try/except, matching its
  siblings' fail-fast-at-import behavior rather than adding new tolerant
  parsing just for this one variable.
- For the `UPLOAD_MAX_ENTRIES` env-var test, chose a subprocess-per-case
  import over `importlib.reload()` or monkeypatching the shared `appmod`
  object, since `test_upload.py` imports `app` once at module load and is
  shared by every other test class in the file — reloading in-process risked
  leaking a mutated environment/module state into unrelated tests run later
  in the same process.

## Deviations from spec
None. Implemented per `docs/spec.md`'s "Proposed approach" and
`docs/design.md`'s exact CSS/HTML/JS specifications (class name
`wizard-check-row.pill-choice`, `renderStep5Actions(d)` signature, call site
passing `wizardState.detectResult`). The `:has()` vs. `onchange`-fallback
open question in spec.md resolved to `:has()` (no contrary browser-support
signal was found anywhere in the repo).

## Known limitations
- The `:has()` CSS rule's actual visual rendering (green pill when checked)
  cannot be exercised by the Node-based frontend test (no real browser/CSS
  engine in that harness) — the test instead asserts on the DOM-observable
  proxy for correctness: the `checked` attribute is present on the right
  `<input>` after each state change, and the `pill-choice` class is present
  on both labels. Actual visual/contrast correctness was verified by reading
  the CSS values directly against design.md's stated palette (`#2a2a2a`/
  `#aaa` idle, `#34c759`/`#111` checked — identical to the already-shipped
  `.pill`/`.pill.active` values elsewhere in the same file).
- No change to what "Back" does when clicked (still a full wizard reset via
  `resetWizardState()`) — explicitly out of scope per spec's non-goals.

## How to verify locally
```bash
# Backend: UPLOAD_MAX_ENTRIES env-var read + full existing upload suite
python3 -m unittest tests.test_upload -v

# Full existing python suite (nothing else touched, but a good sanity pass)
python3 -m unittest discover -s tests -v

# Frontend: step 5 pill styling + conditional Back button
node tests/test_upload_frontend.js

# Other frontend suites, to confirm no regression from the renderWizard()
# call-site edit or CSS block addition
node tests/test_deploy_frontend.js
node tests/test_singleton_toggle_frontend.js

# Manual/visual check (optional): start the app, open the upload wizard,
# upload a folder that yields an ambiguous detection result (a root with a
# .git plus nested repos, or a root with no .git and multiple subfolders) to
# see the pill-styled single/split choice and the Back+Confirm buttons; a
# root with exactly one project to register shows Confirm only.
```

# Implementation: Backlog item 21 part 1 -- grow a running team with an added teammate (backend)

## Summary
Adds `teams.add_team_member(run_id, agent)` (+ `POST /projects/<name>/team/
add-member` and a `team-add-member <run_id> <agent>` CLI subcommand) so a
human can add one more teammate engine to an already-launched, still-live
team run -- new git worktree, new tmux dashboard window in the already-live
`team-<project>` session, and a queued announcement the lead picks up at its
next round boundary, exactly per `docs/spec.md`. Also adds a new
`TEAM_MAX_MEMBERS` cap (default 6), enforced in three places: the new
`add_team_member()`, `validate_composition()` (explicit picker composition,
hard rejection), and `default_team_composition()` (auto-picked default,
deterministic truncation). Backend + CLI only, per the spec's own explicit
scope -- the "+" button UI is a separate part 2. No design doc (this cycle
skipped ux-designer, per the spec's own framing).

## Changes by file
- `app/teams.py`:
  - `TEAM_MAX_MEMBERS` -- new constant, next to `TEAM_MAX_ROUNDS`:
    `int(os.environ.get("TEAM_MAX_MEMBERS", "6"))`.
  - `_membership_log_path(run_id)` -- new, next to `_human_log_path()`:
    `<rundir>/membership.jsonl`, deliberately a NEW/separate file from
    `human.jsonl` (see "Key decisions" below).
  - `_new_state()` -- adds `"membership_cursor": 0` (additive; existing
    persisted runs read it back as `0` via
    `state.get("membership_cursor", 0)`, same precedent `human_cursor`
    itself established).
  - `_next_membership_seq(run_id)` -- new, next to `_next_human_seq()`, same
    "count existing lines" idiom scoped to `membership.jsonl`.
  - `add_team_member(run_id, agent)` -- new, placed immediately after
    `stop_team()`. Loads state fresh; rejects a non-`running`/
    `blocked_ask_user`/`blocked_board_write` status (same three-status set
    `interject()` already accepts); validates `agent` against `roster()`
    (must be a `kind="engine"` entry -- rejects unknown names and the Ollama
    lead entry the same way `validate_composition()` already does), rejects
    if `agent` equals the current engine lead, rejects if already a member,
    rejects at `TEAM_MAX_MEMBERS`. On success: `_create_worktree()`
    (unchanged, reused verbatim), pre-touches + chmods the new agent's log
    file (same ordering `launch_team()` uses -- before the window, so no
    `tail -F` ever races file creation), `tmux new-window` into the
    already-live `team-<project>` session (byte-for-byte the same per-member
    window command `_create_team_session()`'s own loop uses), rolling back
    the worktree via `_remove_worktree()` if the session is gone. Appends
    one `{"ts", "agent", "seq", "kind": "member_joined", "worktree"}`
    envelope to `membership.jsonl` -- the only persisted-state write this
    function ever makes; never calls `_persist()`, never touches `run.json`
    directly, mirroring `interject()`'s own race-avoidance design (its own
    docstring documents why). Returns `{"ok": True, "agent", "worktree"}` /
    `{"ok": False, "error"}`.
  - `team_step()` -- new membership drain checkpoint, structured identically
    to the existing `human.jsonl` drain, placed BEFORE it (membership drain
    runs first, then human -- each check happens on its OWN call: draining
    membership returns immediately if anything was queued, so a round that
    has both a queued member and a queued human message drains one event
    kind per `team_step()` call, same as today's single-file drain
    behavior). For each drained `member_joined` event, appends the agent to
    `state["members"]`/`state["worktrees"]` (guarded by `agent not in
    state["members"]`, idempotent against a theoretical double-drain --
    same defensive shape `_recover_in_progress()` elsewhere in this module
    already favors) plus one `tool="team_member_joined"` history entry
    (`transcript_entries=[]`, same "already durably recorded in its own
    file" reasoning the human drain uses); advances and persists
    `membership_cursor`; returns without calling `_call_lead()`. Docstring
    extended in place to document both drains together.
  - `validate_composition()` -- one new check, after the existing
    duplicate/lead-in-members checks: `if len(names) > TEAM_MAX_MEMBERS:
    return f"too many teammates: {len(names)} exceeds the configured
    maximum of {TEAM_MAX_MEMBERS}"`.
  - `default_team_composition()` -- `members` list truncated to
    `members[:TEAM_MAX_MEMBERS]` (already sorted by name via `roster()`,
    deterministic) right before returning; docstring extended to document
    the truncation (never a refusal, unlike `validate_composition()`'s hard
    rejection of an explicit oversized pick).
  - `_cli_team_add_member(args)` -- new, next to `_cli_team_interject()`.
    Calls `add_team_member()`; prints `added '<agent>' to run <run_id>
    (worktree: <path>)` and exits 0 on success, `error: <reason>` to stderr
    and exit 1 on failure. Does NOT call `_drive_and_report()` -- same
    reasoning `_cli_team_interject()` already documents (there may already
    be a live driver elsewhere).
  - `team-add-member <run_id> <agent>` subparser (two positionals) +
    dispatch arm in `main()`, next to `team-interject`'s own.
- `app/app.py`:
  - New `POST /projects/<name>/team/add-member` branch in `do_POST`,
    immediately after `/team/interject`, same shape/order: unknown-project
    404, `run_id` resolution (explicit body value validated against
    `teams._RUN_ID_RE` before any load/path-join per item 11(b), or
    `latest_run_for_project()` when omitted), cross-project-ownership 400,
    `agent = (body.get("agent") or "").strip()` with a 400 if empty, then
    `teams.add_team_member(run_id, agent)` -- `{"error": ...}, 400` on
    failure, else `{"ok": True, "run_id": run_id, "agent": agent}`. Status
    checking is delegated entirely to `add_team_member()` itself (not
    duplicated at the route layer, since the allowed-status set here is
    identical to `interject()`'s own). No background thread spun up --
    same reasoning `/team/interject` already documents (this never resumes
    a stopped loop). Reached through the same shared TOTP gate every other
    `/team/*` route already sits behind.
- `config/switchboard.env.example`: new commented `#TEAM_MAX_MEMBERS=6`
  line right after the existing `#TEAM_MAX_ROUNDS=8`.
- New tests:
  - `tests/test_teams_composition.py`: `ValidateCompositionTests` extended
    with 2 new cases (at-the-cap accepted, over-the-cap rejected naming
    count and max); new `DefaultTeamCompositionTruncationTests` (3) --
    truncated-to-the-cap, deterministic across calls, under-the-cap
    unaffected.
  - `tests/test_teams_lead.py`: new `_AddTeamMemberTestCase` (shared
    fixture: `_StateTestCase`'s own projdir/state_dir scratch + a scratch
    `ENGINES_DIR`, same combined-fixture technique
    `ValidateCompositionTests` establishes); `AddTeamMemberValidationTests`
    (8) -- every rejection path that never needs real git/tmux (unknown
    run_id, terminal statuses, unknown engine name, Ollama entry rejected,
    agent-equals-lead, already-a-member, at-cap with no side effects,
    `blocked_ask_user`/`blocked_board_write` proven to reach a LATER
    validation error rather than the status gate); `TeamStepDrainMembershipTests`
    (3) -- drain appends to state and never calls the lead, membership
    drains before human in the same round-poll, idempotent against a stale
    cursor replay; `CliTeamAddMemberTests` (3) -- argparse parsing, unknown
    run_id exit code, rejection prints to stderr with no side effect.
  - `tests/test_teams_lifecycle.py`: new `_AddTeamMemberRealTmuxTestCase`
    (extends `_RealTmuxTeamLifecycleTestCase` with a scratch `ENGINES_DIR`,
    needed because -- unlike `launch_team()`'s own `--members`, which are
    never checked against `roster()` -- `add_team_member()` DOES validate
    the requested agent against `roster()`); `AddTeamMemberRealTmuxTests`
    (3) -- real worktree+window creation with the queued envelope asserted,
    the drain-at-next-round-boundary acceptance criterion (proves
    `_lead_tools()`/`_validate_lead_action()` both accept the new agent
    with zero code change in either), and the tmux-session-gone rollback
    path; `CliTeamAddMemberSubprocessTests` (2) -- real, separate-process
    `team-launch` then `team-add-member` via the actual CLI (mirrors
    `CliTeamLifecycleSubprocessTests`'s own technique -- no TMUX
    monkeypatch, relies on the real `sudo -u $RUN_USER tmux` path that
    class's own docstring already proved works in this environment).
  - `tests/test_team_routes.py`: new `TeamAddMemberEndpointTests` (10),
    placed right after `TeamInterjectEndpointTests` -- mirrors that class's
    own structure closely (unknown project, no run at all, cross-project
    run_id, path-traversal run_id with the planted-file-never-opened proof,
    malformed non-traversal run_id, empty agent with a call-count double on
    `teams.add_team_member`, terminal status rejected, `run_id` omitted
    defaults to `latest_run_for_project`, the success path asserting the
    real worktree/queued envelope with no background thread started, and
    the over-the-cap 400 asserting no worktree was created).

## Key decisions / tradeoffs
- **`add_team_member()` never calls `_persist(state)` and never mutates
  `run.json` directly** -- exactly the same race-avoidance reasoning
  `interject()`'s own docstring documents: a naive "load state, append to
  `state['members']`, persist" implementation would very likely be
  clobbered by the driving thread's own next round-end `_persist(state)`
  call. Writing only to `membership.jsonl` (a file the driving thread never
  otherwise touches mid-round) leaves nothing for that last-writer-wins
  race to clobber; `team_step()`'s own drain is what actually delivers the
  new member into `state["members"]`, on the driving thread itself, at the
  next round boundary.
- **`membership.jsonl` is a new, separate file from `human.jsonl`**, even
  though the drain mechanics are byte-for-byte identical --
  `_membership_log_path()`'s own docstring records why: every event source
  in this module already gets its own file (`transcript.jsonl`, one
  `<agent>.jsonl` per teammate, `human.jsonl` for human chat), and item 19
  part 2's already-shipped UI hard-codes "human filter pill = human.jsonl,
  agent='human'" -- conflating a system-generated `member_joined` event
  into that file/agent value would be a foot-gun for that UI, not a reuse
  of the module's own one-file-per-source convention.
- **`TEAM_MAX_MEMBERS` is enforced differently at the three call sites, on
  purpose**: `add_team_member()` and `validate_composition()` both hard-
  reject (an explicit human action -- growing a running team, or picking an
  explicit composition -- gets a clear refusal, never a silent
  substitution), while `default_team_composition()` truncates
  deterministically instead of refusing, consistent with that function's
  own pre-existing character as a best-effort auto-pick, never a hard
  refusal for a situation the human didn't explicitly create.
- **Membership drains before human in `team_step()`'s own checkpoint
  order** -- arbitrary but deterministic, per docs/spec.md: a new teammate
  becoming available is the more "structural" of the two events to surface
  first if both are queued in the same round-poll. Each drain still fully
  owns its own `team_step()` call (returns immediately after draining, same
  as the pre-existing human drain) -- a round with BOTH a queued member and
  a queued human message drains one event kind per call, exactly the same
  "one file per call, next call gets the other" behavior a second
  sequential drain-only file already implies.
- **Reused `roster()`/`by_key` lookup verbatim from `validate_composition()`'s
  own shape** (`(kind, name)` tuple keys, `kind="engine"` required, Ollama
  entry excluded by construction) rather than inventing a second roster-
  lookup helper -- `add_team_member()`'s own validation is a proper subset
  of `validate_composition()`'s rules (single agent, not a full
  lead+members pair), so it reads the same `entries`/`by_key` pattern
  directly rather than factoring out a shared helper neither call site
  actually needs beyond this reuse.

## Deviations from spec
None. Implemented per `docs/spec.md`'s own literal function/route/CLI
shapes, error strings, and constant default -- `add_team_member()`'s return
shape and every rejection message, the route's validation order and error
strings, the CLI's exact `added '<agent>' to run <run_id> (worktree: <path>)`
output, the `TEAM_MAX_MEMBERS` default (6) and its three enforcement points,
and `team_step()`'s membership-drain-before-human ordering are all copied
verbatim from the spec's "Proposed approach".

## Known limitations
Every "Non-goal"/"Edge case" `docs/spec.md` itself already documents as an
accepted, narrow tradeoff is carried forward unchanged (not re-litigated
here): the "+" button UI is out of scope (part 2); shape (2), independent
non-team parallel instances, is explicitly rejected, not deferred; shrinking
a running team is not built; concurrent/parallel delegation to multiple
teammates is unrelated and unchanged; an already-running team started
before `TEAM_MAX_MEMBERS` existed is never retroactively trimmed; a
`member_joined` event is delivered only at the next round boundary, never
mid-in-flight-tool-call (same tradeoff item 19 part 1 already accepted for
human interjects); two concurrent `add_team_member()` calls for the exact
same requested agent name can produce one false-positive "still has
uncommitted changes" error for the second, legitimately-losing caller (the
spec's own accepted, narrow first-mover race, same class
`_create_team_session()`'s own documented session-name race already
carries) -- not exercised by an automated test here (would require two
genuinely concurrent `add_team_member()` calls racing on the exact same new
worktree path, the same class of test this codebase's own precedent
(`SessionCreationRaceRealTmuxTests`) shows is possible to build but wasn't
asked for by this spec's acceptance criteria, which cover the single-caller
success/rejection paths and the tmux-session-gone rollback instead). No new
limitation was introduced beyond what `docs/spec.md` already scoped.

## How to verify locally
```
# This cycle's new backend tests:
python3 -m unittest tests.test_teams_composition.ValidateCompositionTests \
  tests.test_teams_composition.DefaultTeamCompositionTruncationTests \
  tests.test_teams_lead.AddTeamMemberValidationTests \
  tests.test_teams_lead.TeamStepDrainMembershipTests \
  tests.test_teams_lead.CliTeamAddMemberTests \
  tests.test_teams_lifecycle.AddTeamMemberRealTmuxTests \
  tests.test_teams_lifecycle.CliTeamAddMemberSubprocessTests \
  tests.test_team_routes.TeamAddMemberEndpointTests -v
# Ran 47 tests ... OK

# Full test_teams_composition.py / test_teams_lead.py / test_teams_lifecycle.py
# / test_team_routes.py, including this cycle's new tests:
python3 -m unittest tests.test_teams_composition tests.test_teams_lead \
  tests.test_teams_lifecycle tests.test_team_routes
# Ran 356 tests ... OK

# Full existing suite:
python3 -m unittest discover -s tests
# Ran 1188 tests in 158.937s ... OK

# Manual smoke test against a real project (no lead/teammate subprocess
# needed for any of these):
#   1. Start the app.py server, log in, start a team run against a project
#      with at least one teammate not already on the team.
#   2. `curl` (with a valid session cookie + TOTP code)
#      `-d '{"agent": "codex", "code": "<code>"}'
#      /projects/<name>/team/add-member` -> {"ok": true, "run_id": "...",
#      "agent": "codex"}.
#   3. `tmux list-windows -t team-<project>` shows a new "codex" window;
#      `git -C <project> worktree list` shows a new `<project>.teams/codex`
#      entry.
#   4. `python3 app/teams.py team-status <run_id>` -- once the driving
#      thread completes its current round, a new "team_member_joined" entry
#      appears in state["history"] and "codex" is now in state["members"].
#   5. `python3 app/teams.py team-add-member <run_id> <agent>` -- prints
#      `added '<agent>' to run <run_id> (worktree: <path>)`, exits 0, does
#      not block.
```

---

# Implementation: BACKLOG item 21 part 1 follow-up -- close `blocked_ask_user` test-coverage gap

## Summary
Closes the sole non-blocking follow-up from `docs/test-review.md`'s "Test &
Review: Backlog item 21 part 1" section (verdict: Approve with follow-ups,
Finding 1). The reviewer's testing pass confirmed the behavior is already
correct (via a throwaway test written, run, and discarded that session) but
found no automated test in the shipped diff actually reaches the real
worktree/window/drain path for `add_team_member()` while a run is
`blocked_ask_user` -- the existing
`AddTeamMemberValidationTests::test_blocked_ask_user_and_blocked_board_
write_do_not_hit_the_status_check` (`tests/test_teams_lead.py`) only proves
the status *gate* accepts this status, by using a deliberately-unknown
engine name so it fails at a later, unrelated check, without ever calling
`_create_worktree()`/`tmux new-window`. No production code changed; this is
a permanent regression test added to close that gap.

## Changes by file
- `tests/test_teams_lifecycle.py`: added
  `AddTeamMemberRealTmuxTests::test_add_member_while_blocked_ask_user_
  succeeds_immediately_and_drain_waits_for_resume`, real-tmux/real-git,
  mirroring the class's own existing
  `test_add_member_creates_worktree_and_window_queues_event` almost
  exactly per the reviewer's own recommendation. Launches a team, forces
  `state["status"] = "blocked_ask_user"` and persists it, then calls
  `add_team_member(run_id, "aider")` and asserts: the call succeeds with
  the same `{"ok": True, "agent", "worktree"}` shape; the worktree exists
  on disk and shows up in `git worktree list`; the tmux window exists
  alongside `lead`/`codex` in the live session; and one `member_joined`
  envelope is queued to `membership.jsonl`. Then reloads state fresh and
  asserts `"aider"` is NOT yet in `state["members"]`/`state["worktrees"]`
  while the run is still `blocked_ask_user`. Finally simulates a resume
  (flips `status` back to `"running"` on the in-memory state) and calls
  `team_step()` once -- stubbing `_call_lead()` to fail the test if invoked,
  same technique the class's own
  `test_drain_at_next_round_boundary_makes_agent_delegate_eligible` already
  uses -- and asserts `"aider"` is now in `state["members"]` and
  `state["worktrees"]["aider"]` is the expected path, proving the queued
  event only drains on/after resume, never before.

## Key decisions / tradeoffs
- Placed the new test in `tests/test_teams_lifecycle.py`'s
  `AddTeamMemberRealTmuxTests` (not `test_teams_lead.py`) because the
  criterion under test is specifically the *success* path (real worktree +
  window creation), which requires the same real-tmux/real-git fixture
  (`_AddTeamMemberRealTmuxTestCase`) the class's other tests already use --
  `test_teams_lead.py`'s `AddTeamMemberValidationTests` deliberately avoids
  real tmux/git by using validation failures that short-circuit before any
  side effect, which is exactly the gap being closed here.
- Set `state["status"]` directly and persisted it before calling
  `add_team_member()`, matching the exact pattern
  `test_teams_lead.py`'s own `test_terminal_statuses_rejected` and
  `test_blocked_ask_user_and_blocked_board_write_do_not_hit_the_status_
  check` already use, rather than reaching for `_force_ask_user()` (a
  heavier helper meant for the driving thread's own ask_user framing, not
  needed here since the test only cares about the status value itself).
- Reused the existing `_call_lead`-stub-that-fails-the-test technique from
  `test_drain_at_next_round_boundary_makes_agent_delegate_eligible` for the
  resume step, rather than inventing a new assertion style, so a future
  regression where the drain accidentally called the lead on a
  drain-only round would be caught the same way it already is for the
  unblocked case.

## Deviations from spec / design
None -- this is a test-only addition per the reviewer's own non-blocking
follow-up recommendation, not new product behavior.

## Known limitations
None new. The reviewer's other follow-up (the two-concurrent-callers race)
remains explicitly out of scope, unchanged from the prior cycle.

## How to verify locally
```
python3 -m unittest tests.test_teams_lifecycle.AddTeamMemberRealTmuxTests -v
# Ran 4 tests ... OK

python3 -m unittest tests.test_teams_composition tests.test_teams_lead \
  tests.test_teams_lifecycle tests.test_team_routes
# Ran 357 tests ... OK

python3 -m unittest discover -s tests
# Ran 1189 tests in 158.9s ... OK
```

# Implementation: Backlog item 21 part 2 -- the "+" button UI for growing a running team

## Summary
Ships the human-facing "+" control on top of part 1's already-merged backend
(`teams.add_team_member()`, `POST /projects/<name>/team/add-member`,
`TEAM_MAX_MEMBERS`): a native `<select>` + "+ Add" button on an already-
running team's row, visible under exactly the three statuses
`add_team_member()` itself accepts (reusing `teamAcceptsInterject(team)` as
the visibility gate verbatim), populated with eligible roster engines
(excludes the current engine lead and anyone already on the live team), with
two distinct disabled-reason states (at-cap vs. no-eligible-engines) and an
honest "will join... at its next round" success message. Two small, additive
backend fields make this possible: `/status`'s `inst.team.members`/
`inst.team.lead` (the run's live roster/lead, not the stale saved-picker
`composition`) and a `member_joined` event now merged into `GET .../team/
events` from `membership.jsonl`. Frontend + two backend field/merge
additions, entirely within `app/app.py` -- no `app/teams.py` change, no new
route, per docs/spec.md's own explicit scope.

## Changes by file
- `app/app.py`:
  - `/status` handler: `inst["team"]` gains `"members"` (`run.get("members",
    []) if run is not None else []`) and `"lead"` (`run.get("lead") if run
    is not None else None`), read straight off the run's own persisted
    state, never re-derived from `composition` (the saved/default picker
    preference `add_team_member()` never touches). Top-level response gains
    `"team_max_members": teams.TEAM_MAX_MEMBERS`, same "computed once,
    shipped once per call" treatment `"roster"` already gets.
  - `_handle_team_events()`: the `files` list gains `("membership",
    teams._membership_log_path(run_id))` alongside the existing lead/human
    sources. The `"membership"` label is only used for the malformed-line
    fallback and the `cursors` dict key -- it does not override the `agent`
    field already embedded in each `membership.jsonl` line by part 1's
    `add_team_member()`, so a `member_joined` event surfaces tagged with the
    newly-joined agent's own name/color, not a generic pseudo-agent.
  - New CSS (near the existing `.team-interject-*` rules): `.team-add-member`
    (flex row, reuses `.team-interject-row`'s own gap), `.team-add-member
    select` (byte-for-byte `.team-lead-picker select`'s declaration block),
    `.team-add-member-reason` (byte-for-byte `.team-sub`'s muted-text
    tokens), `.team-feed-event.kind-member-joined` (`border-left: 3px solid
    currentColor`, matching `.kind-human-message`'s own left-border-accent
    shape but dynamic per agent instead of a fixed blue).
  - New JS state: `teamAddMemberChoice` (name -> selected agent, same
    "survives a mid-flow re-render/428 retry" idiom as `teamInterjectText`),
    `TEAM_MAX_MEMBERS_CLIENT` (a `let`, not `const` -- hardcoded default `6`
    matching the server's own default, overwritten from `s.team_max_members`
    on every `refresh()` poll, same idiom `ROSTER` itself uses for its own
    live override).
  - New JS functions: `teamAddMemberEligible(team)` (pure; filters `ROSTER`
    to `kind === 'engine'` entries not already in `team.members` and not the
    current engine lead), `renderTeamAddMemberControl(name, team)` (visible
    iff `teamAcceptsInterject(team)`; renders the disabled at-cap reason, the
    disabled no-eligible-engines reason, or the live `<select>` + button),
    `doTeamAddMember(name)` (saves the selection to `teamAddMemberChoice`
    before dispatching, mirrors `doTeamInterject()`'s shape).
  - `refresh()`: one new line, `if (s.team_max_members)
    TEAM_MAX_MEMBERS_CLIENT = s.team_max_members;`, placed right after the
    existing `ROSTER = s.roster || [];` line.
  - `actionPath()`: one new `kind === 'team-add-member'` branch, POSTs to the
    already-shipped `/projects/<name>/team/add-member`.
  - `actionBody()`: one new `kind === 'team-add-member'` branch, `body.agent
    = teamAddMemberChoice[name]`.
  - `handleActionResult()`: one new 428-label switch entry (`'Adding
    teammate: ' + (name || 'this')`) and one new `kind === 'team-add-member'`
    branch, placed before the generic-400 fallback, mirroring
    `team-interject`'s own branch shape. Success message is exactly `"✓
    '<agent>' will join the team at its next round"` (never "has joined"),
    using the server's own returned `data.agent`; the selection mirror is
    deleted on success, kept on failure so a retry doesn't require re-
    picking.
  - `teamFeedEventKindClass()`: one new early-return branch, `if (e.kind ===
    'member_joined') return 'member-joined';`. `teamFeedEventBody()`: one
    new branch, `if (cls === 'member-joined') return '→ joined the team';`
    (the agent name itself is already rendered by the existing
    `.team-feed-agent` span, so it's not repeated here).
  - `renderTeamFeed()`: the filter-pill agent list source changed from
    `(team.composition && team.composition.members) || []` (a saved/default
    picker preference, never updated by `add_team_member()`) to `team.members
    || []` (the live `/status` field this cycle adds) -- fixes a real
    staleness bug flagged in docs/spec.md's own "Background": a newly-added
    teammate's events were already reachable under the `all` filter (their
    log file was already merged into `/team/events` before this part) but
    never got their own clickable pill.
  - `teamRow(name, team)`: one new `addMemberControl` variable
    (`renderTeamAddMemberControl(name, team)`), inserted into the non-idle
    render order between `interjectBox` and `feedToggle`.
- `tests/test_team_routes.py`:
  - `test_status_idle_when_no_run_ever_started` (exact-dict-equality test)
    updated to include the two new additive keys (`"members": []`,
    `"lead": None`) -- the one existing test docs/spec.md's acceptance
    criteria flagged as needing this.
  - `StatusRosterAndCompositionTests` gains three new tests:
    `test_team_max_members_top_level_field`,
    `test_members_and_lead_reflect_live_roster_not_the_saved_composition`
    (launches a real team with one composition saved that deliberately
    differs from the live run, proving `members`/`lead` come from the run,
    not from `composition`), and
    `test_members_grows_once_add_team_member_drains_at_the_next_round`
    (calls `add_team_member()` directly, asserts `/status` still reports the
    OLD roster immediately after, then simulates the drain and asserts the
    NEW roster appears).
  - `TeamEventsEndpointTests` gains two new tests:
    `test_membership_jsonl_merged_tagged_with_the_joined_agents_own_name`
    (writes a raw `member_joined` envelope to `membership.jsonl`, asserts it
    surfaces in the merged feed tagged `agent: "aider"`, and that
    `cursors["membership"]` is present) and
    `test_no_membership_jsonl_yet_degrades_to_no_membership_events_not_an_error`
    (a run that never called `add_team_member()` -- confirms the existing
    `tail_jsonl_events()` `FileNotFoundError` handling already covers this).
- `tests/test_team_frontend.js`:
  - Two existing tests (`'per-agent filter pills carry aria-pressed...'` and
    `'renderTeamFeed() lists filter pills in order All, lead, human,
    <member1>...'`) updated to set `members: ['helper']` directly on the
    `team` object instead of `composition: { lead: null, members:
    ['helper'] }` -- these tests assert the rendered pill list, which now
    reads the live `team.members` field, not the stale
    `team.composition.members` (see the `renderTeamFeed()` change above).
  - A new "'+' add-teammate control" test section (9 new tests), placed
    right after the chat-UI compose surface's own tests and before the
    "Past team branches panel" section: exact eligible-option filtering
    (already-a-member and the current engine lead both excluded, an Ollama-
    kind lead has nothing to exclude); the POST dispatch shape (`{agent}`)
    and 428-retry label/resend of the SAME agent; the exact success message
    text (asserts it never contains "has joined"); a server-side 400 leaves
    the select/button usable for retry; the at-cap disabled state (exact
    text, no select/button rendered); the distinct under-cap-but-no-eligible
    -engines disabled state; visibility across `running` /
    `blocked_ask_user` / `blocked_board_write` (shown) vs. `idle` /
    `finished` / `error` / `blocked` without `waiting_on_you` (hidden); and
    `teamFeedEventKindClass()`/`teamFeedEventBody()` returning
    `'member-joined'`/`'→ joined the team'` for a `member_joined` event, plus
    the rendered row carrying `kind-member-joined`.

## Key decisions / tradeoffs
- **`TEAM_MAX_MEMBERS_CLIENT` is a `let` with a live `/status` override**,
  unlike `TEAM_INTERJECT_MAX_CHARS_CLIENT` (a `const`, hardcoded only, never
  overridden from any poll). docs/spec.md's own "Proposed approach" §5
  explicitly asked for this live override and cites
  `TEAM_INTERJECT_MAX_CHARS_CLIENT` as "the exact same precedent" -- reading
  the actual code, that precedent is only half right (it's the
  hardcoded-default half; there is no live-override code path for it
  anywhere in this codebase today). Implemented per the spec's literal,
  explicit instruction (a genuinely new field this cycle adds, cheap to
  fetch, and directly gates a control's disabled state rather than being
  advisory copy) rather than deviating to match the imperfect analogy — see
  "Deviations from spec / design" below for why this isn't flagged as a
  deviation from the *spec* (it isn't; the spec's own directive is what was
  followed) but is flagged here as a factual correction to the spec's own
  characterization of the precedent.
- **`.team-feed-event.kind-member-joined`'s `border-left: 3px solid
  currentColor` resolves to the ROW's own inherited `color` (`#eee`, from
  the base `.team-feed-event` rule), not the joined agent's own color.**
  `currentColor` in CSS resolves against the element's OWN computed `color`
  property, not a descendant's -- the agent color is set via an inline
  `style="color:...` on the nested `.team-feed-agent` `<span>` only, which
  does not propagate to an ancestor's own `color` for border-color purposes.
  This is implemented byte-for-byte per docs/design.md's own "Implementation
  notes for the developer" §6 CSS snippet and its accompanying comment
  (which asserts the opposite). The net visual effect is a plain
  light-gray/white left border for every `member_joined` row rather than a
  per-agent-colored one; the acceptance criterion this affects
  ("...rendered... in aider's own established color") is still met by the
  UNCHANGED `.team-feed-agent` span mechanism, which already colors the
  agent name text correctly and is not touched by this border rule. Left
  as specified rather than silently "fixed" with an inline style the
  design doc didn't ask for -- flagged here for the reviewer to weigh
  whether the border's own color is worth a follow-up.
- **Visibility gate reuses `teamAcceptsInterject(team)` verbatim, no
  rename.** Confirmed reading the code (not just docs/spec.md's claim) that
  `interject()` and `add_team_member()` accept the identical three-status
  set server-side, so this is a safe, intentional reuse, not an incidental
  coupling that could silently diverge later.
- **`renderTeamAddMemberControl()` checks the at-cap condition before the
  no-eligible-engines condition**, matching docs/spec.md's own ordering (and
  covering the edge case where a team is simultaneously at cap AND has no
  further eligible engines -- the at-cap message wins, since it's the more
  actionable of the two: adding roster engines wouldn't help until the cap
  itself is addressed).

## Deviations from spec / design
None in substance -- every acceptance criterion in docs/spec.md is
implemented as specified, and docs/design.md's "Implementation notes for the
developer" section was followed near-literally (exact function
signatures/CSS values/copy strings). The two items above are documented
factual corrections/observations about the spec's/design's own reasoning,
not behavioral deviations: `TEAM_MAX_MEMBERS_CLIENT`'s live-override
behavior matches what the spec explicitly asked for (the spec's own analogy
to an existing precedent just doesn't hold up under inspection), and the
`kind-member-joined` border color is implemented exactly per the design
doc's own CSS snippet (the design doc's accompanying rationale for why that
CSS would pick up the agent's color is what doesn't hold up under
inspection).

## Known limitations
- ~~The `kind-member-joined` left-border accent renders in the feed's base
  text color, not the joined agent's own color~~ -- **fixed**, see "Post-review
  fix" below; the outer row div now carries its own inline
  `border-left-color` matching the agent-name span's color.
- Row re-renders mid-selection reset any unsubmitted `<select>` pick (not
  mirrored client-side pre-submit, matching the composition picker's own
  "team-mate checkbox" precedent) -- this is docs/spec.md's own accepted
  edge case ("Row re-renders mid-selection"), not a defect.
- No CLI convenience flag for browsing eligible roster engines
  (`docs/spec.md`'s own "Open questions" flagged this as a possible small
  follow-up, explicitly out of scope for this part).

## How to verify locally
```
# Backend: /status's new members/lead/team_max_members fields, and the
# membership.jsonl merge into GET .../team/events.
python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests \
  tests.test_team_routes.TeamEventsEndpointTests -v
# Ran 18 tests ... OK

# Full backend team-route suite (confirms the two new additive fields don't
# break any existing exact-dict-equality assertion).
python3 -m unittest tests.test_team_routes -v
# Ran 126 tests ... OK

# Frontend: the "+" control, its two disabled states, the 428/success/error
# flows, the member_joined feed classification, and the filter-pill fix --
# run against the real rendered <script> extracted from render_page(),
# same technique as every other tests/test_team_frontend.js test.
TOTP_SECRET=JBSWY3DPEHPK3PXP node tests/test_team_frontend.js
# ALL PASS (103/103)

# Manual check: start a team from the web UI, add a teammate via the new
# "+" control, confirm the success message reads "will join the team at
# its next round" (not "has joined"), then watch the live feed for a
# "→ joined the team" line in that agent's own color within ~4s, and the
# filter-pill row gain that agent's pill on the following /status poll.
```

## Post-review fix (should-fix from `docs/test-review.md`'s BACKLOG item 21
part 2 review, Finding 1)
The reviewer's diagnosis was correct: `.team-feed-event.kind-member-joined`'s
`border-left: 3px solid currentColor` resolves against the OUTER
`.team-feed-event` `<div>`'s own computed `color` (inherited/unset → the
feed's base `#eee`), not the nested `.team-feed-agent` `<span>`'s inline
`color` — CSS `currentColor` never looks at a descendant. The design doc's
own accompanying rationale for that CSS (that it would "pick up" the agent
color) doesn't hold up; the CSS itself was implemented exactly as specified,
per this file's original "Key decisions" note above.

Fixed in `app/app.py`'s `renderTeamFeedEvent()`: for `kind === 'member-joined'`
specifically, the same `color` value already computed via `teamAgentColor(e.agent)`
(previously only applied to the `.team-feed-agent` span) is now also applied
as an inline `style="border-left-color:..."` on the OUTER
`<div class="team-feed-event kind-member-joined">` itself, overriding the
CSS's own (now-irrelevant for this kind) `currentColor` value via normal
inline-style specificity. No CSS rule was removed — `border-left: 3px solid
currentColor` is left in place as the harmless base declaration for every
other kind (matching `kind-human-message`'s own pattern of a fixed,
non-`currentColor` border color, which never had this bug). Every other
event kind is untouched (`borderStyle` is only computed/emitted for
`member-joined`).

Verified structurally (not just by trusting the diff): added a new frontend
test (`tests/test_team_frontend.js`, "a member_joined feed event's outer row
carries an inline border-left-color matching the joined agent's own
established color") that calls `renderTeamFeedEvent()` directly for TWO
different agents (`aider`, `codex` — deliberately different palette buckets
via `teamAgentColor()`'s hash), and for each asserts (a) the outer
`kind-member-joined` div's own opening tag contains
`style="border-left-color:<that agent's color>"`, and (b) the nested
`.team-feed-agent` span still carries the same color — i.e. the border and
the name text now agree, for more than one agent, not just one. Wrote the
test first, ran it red (`AssertionError ... got attrs: ` — confirming the
outer div carried no style attribute at all before the fix), then made the
minimal `renderTeamFeedEvent()` change above and reran it green.

No new color-value trust concern: `teamAgentColor()`'s return is always one
of six hardcoded hex literals from `TEAM_AGENT_PALETTE` (never
attacker/agent-name-controlled beyond which bucket it hashes into), so
embedding it unescaped into the inline `style` attribute carries the same
(pre-existing, already-accepted) trust profile as the untouched
`.team-feed-agent` span's own identical `style="color:...` usage two lines
below it.

Full suite: `python3 -m unittest discover -s tests` → 1194 tests, OK
(unchanged count — this fix touched no backend/Python code, only the
`renderTeamFeedEvent()` JS function embedded in `app/app.py`'s
`PAGE_TEMPLATE` string). `TOTP_SECRET=... node tests/test_team_frontend.js`
→ 104/104 PASS (103 baseline + 1 new).

# Implementation: BACKLOG item 15 part 5 -- `ct/create.sh` optional-feature checklist + taiga/ollama follow-ups

## Summary
Replaces `ct/create.sh`'s two standalone `yesno` prompts
(`WITH_GIT_HOSTING`, `WITH_CODE_SERVER`) with a single `whiptail --checklist`
covering all four switchboard-box-installable `install.sh` flags
(git-hosting, code-server, taiga, ollama), all unchecked by default. Checking
taiga now shows a single acknowledgment `msgbox` carrying `install.sh`'s own
resource-cost callout; checking ollama now walks a host-side
endpoint/model-name retry loop that mirrors `install.sh:761-805`'s own
`curl`+`python3` exact-match validation logic verbatim (never a
substring/prefix match), since `ct/create.sh` always calls `install.sh` with
`--yes`, under which `install.sh`'s own interactive prompt for these values
never actually asks anything. On success the validated `TEAM_LLM_BASE_URL`/
`TEAM_LLM_MODEL` are written straight into the `switchboard.env` `TMP_ENV`
heredoc already being pushed to the container; `INSTALL_FLAGS` gains two new
conditional `--with-taiga`/`--with-ollama` appends alongside the existing
two. Single-file change, `ct/create.sh` only.

## Changes by file
- `ct/create.sh`:
  - Added an unconditional `command -v python3 >/dev/null 2>&1 ||
    apt-get install -y -qq python3` preflight right after the existing
    `whiptail` preflight (line 23), before the checklist screen even
    appears, so the ollama follow-up's `python3` heredoc is guaranteed
    present regardless of whether the operator ends up checking that row.
  - Replaced the two `yesno` blocks (former lines 56-64) with:
    - A `whiptail --checklist` (18 rows, 78 cols, 4 visible rows) listing
      `git-hosting`/`code-server`/`taiga`/`ollama`, each `OFF` by default,
      with the row-label copy `docs/design.md` finalized.
    - The standard quote-stripping `for _item in $FEATURES` parse loop
      (no `eval`) setting `WITH_GIT_HOSTING`/`WITH_CODE_SERVER`/
      `WITH_TAIGA`/`WITH_OLLAMA` from the returned tags.
    - A `WITH_TAIGA`-gated `msg()` call carrying the exact resource-cost
      wording (9 containers, several GB RAM, real disk for
      Postgres/RabbitMQ volumes) from `install.sh:920-922`, adapted per
      `docs/design.md`.
    - A `WITH_OLLAMA`-gated retry loop: defines `OLLAMA_MODEL_CHECK_SCRIPT`
      once (a byte-for-byte copy of `install.sh:787-802`'s python heredoc —
      diffed against the original during verification, see below),
      prompts for URL/model via the existing `ask()` helper (same
      defaults/wording as `install.sh:753-754`), normalizes the trailing
      slash, `curl`s `$URL/models` with the same 10s timeout, and
      classifies the result into the same three failure modes
      `install.sh` distinguishes (unreachable, model-absent [empty-list or
      not-found variants], unparseable JSON). On success it stores the
      normalized URL/model in `OLLAMA_BASE_URL_NORM`/`OLLAMA_MODEL_INPUT`
      and exits the loop; on failure it shows a `msg()` naming the specific
      reason, then a separate `yesno("Try a different URL/model?")` —
      "Yes" re-prompts with the failed values pre-filled as the new
      defaults, "No" resets `WITH_OLLAMA=0`, shows the skip-acknowledgment
      `msg()`, and breaks out.
  - After the existing `cat > "$TMP_ENV" <<EOF ... EOF` heredoc and before
    `pct push`, added a `WITH_OLLAMA`-gated block appending
    `TEAM_LLM_BASE_URL=${OLLAMA_BASE_URL_NORM}` /
    `TEAM_LLM_MODEL=${OLLAMA_MODEL_INPUT}` to `TMP_ENV`.
  - Added two new conditional `INSTALL_FLAGS` appends
    (`--with-taiga`, `--with-ollama`) alongside the existing
    `--with-git-hosting`/`--with-code-server` ones, same order/idiom.

## Key decisions / tradeoffs
- **Ollama failure feedback uses two separate dialogs (a `msg()` naming the
  failure reason, then a plain `yesno("Try a different URL/model?")`), not
  one combined `yesno` with the reason concatenated into its own body.**
  `docs/spec.md`'s draft bash combined them into a single
  `yesno("$_ollama_fail_msg\n\nTry a different URL/model?")`, but
  `docs/design.md` (§3c/3d) explicitly specifies them as two separate
  screens — a `--msgbox` (error message only) always "immediately followed
  by" a `--yesno` whose prompt text is just "Try a different URL/model?".
  Per this task's instruction to follow `docs/design.md`'s wording/flow
  over the spec's draft copy wherever they differ, I implemented the
  two-dialog flow. Recorded under "Deviations from spec" below since it's
  a real structural difference from the spec's own proposed code, not just
  a copy tweak.
  - `docs/design.md` §3g's "Continuing without linking Ollama..."
    skip-acknowledgment `msgbox` and `docs/design.md`'s ordering of
    "taiga msgbox, then ollama follow-up" (both checked) were implemented
    exactly as both `docs/spec.md` and `docs/design.md` already agreed on.
- **`OLLAMA_MODEL_CHECK_SCRIPT` is defined once, above the `while` loop**
  (per `docs/spec.md`'s explicit "defined once, above this block"
  instruction), rather than redefining the heredoc string on every retry
  iteration the way `install.sh` incidentally does inside its own
  (non-looping) `if/else`. Behaviorally identical either way since the
  heredoc body never changes across iterations; placing it once outside the
  loop just avoids rebuilding the same string on every retry.
- **No shared file/function between `ct/create.sh` and `install.sh`** — the
  python heredoc and the taiga wording are duplicated, matching
  `docs/spec.md`'s explicit non-goal ("no shared framework... a small
  amount of duplicated curl+python3 logic... is an accepted, deliberate
  tradeoff").

## Deviations from spec
- **Ollama failure dialog is two separate whiptail screens (msgbox then
  yesno), not one combined yesno with the failure message folded into its
  body**, per `docs/design.md`'s explicit flow (§3c "Then: Immediately
  followed by a `whiptail --yesno`" / §3d's plain "Try a different
  URL/model?" prompt text) overriding `docs/spec.md`'s own draft bash. This
  is the one place spec and design genuinely disagree on structure, not
  just wording; design's version was implemented. All observable behavior
  the acceptance criteria actually check (which specific failure reason is
  shown, retry-vs-skip choice offered, pre-filled retry defaults, `WITH_OLLAMA`
  reset to 0 on skip) is unaffected by this either way.
- Everything else (checklist screen shape/copy, taiga msgbox wording,
  ollama prompt wording/defaults, `TMP_ENV`/`INSTALL_FLAGS` wiring, the
  python exact-match logic) follows `docs/spec.md`'s proposed approach
  and/or `docs/design.md`'s finalized copy verbatim/near-verbatim, no other
  deviations.

## Known limitations
- Not exercised against a real Proxmox host / real `whiptail` TTY / a real
  Ollama endpoint (see "How to verify locally" for exactly what was and
  wasn't checked, and why).
- Per `docs/spec.md`'s open question #2 (proceeded-under-assumption, not a
  blocker): host-side validation success does not guarantee the
  container's own network path can reach the same endpoint — `install.sh`'s
  own container-side `--with-ollama` re-check is the second, independent
  vantage point, and its failure path (by existing, unmodified `install.sh`
  design) never un-writes the `TEAM_LLM_*` values this cycle's host-side
  check already wrote. This is an accepted, spec'd-in-advance edge case,
  not a bug introduced here.

## How to verify locally
This script only runs interactively on a real Proxmox VE host (`pct`,
`whiptail` in a real TTY) — there is no CI harness for it and none was
added, matching the task's framing. What was actually run this cycle:

1. **Syntax check**: `bash -n ct/create.sh` → passed (no output, exit 0).
2. **Full-file shellcheck** (installed via `sudo apt-get install -y
   shellcheck`, none was present in the sandbox beforehand):
   `shellcheck ct/create.sh` → zero warnings anywhere in the file (not just
   the diff).
3. **Byte-for-byte verbatim copy check** of the ollama python heredoc
   against its `install.sh` source:
   `diff <(sed -n '787,802p' install.sh) <(sed -n '91,106p' ct/create.sh)`
   → empty diff (identical).
4. **Checklist-parsing / `INSTALL_FLAGS` logic**, extracted verbatim from
   the script and exercised standalone (no whiptail/TTY needed — `FEATURES`
   is fed in as whiptail would emit it) against all of the acceptance
   criteria's flag-composition cases:
   ```bash
   run_case() {
       local desc="$1" FEATURES="$2"
       WITH_GIT_HOSTING=0; WITH_CODE_SERVER=0; WITH_TAIGA=0; WITH_OLLAMA=0
       for _item in $FEATURES; do
           _item="${_item%\"}"; _item="${_item#\"}"
           case "$_item" in
               git-hosting) WITH_GIT_HOSTING=1 ;;
               code-server) WITH_CODE_SERVER=1 ;;
               taiga)       WITH_TAIGA=1 ;;
               ollama)      WITH_OLLAMA=1 ;;
           esac
       done
       INSTALL_FLAGS="--yes"
       [ "$WITH_GIT_HOSTING" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-git-hosting"
       [ "$WITH_CODE_SERVER" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-code-server"
       [ "$WITH_TAIGA" -eq 1 ]       && INSTALL_FLAGS="$INSTALL_FLAGS --with-taiga"
       [ "$WITH_OLLAMA" -eq 1 ]      && INSTALL_FLAGS="$INSTALL_FLAGS --with-ollama"
       echo "$desc => INSTALL_FLAGS=[$INSTALL_FLAGS]"
   }
   run_case "nothing checked"              ""
   run_case "git-hosting + code-server"    '"git-hosting" "code-server"'
   run_case "taiga only"                   '"taiga"'
   run_case "all four"                     '"git-hosting" "code-server" "taiga" "ollama"'
   ```
   Results: `nothing checked` → `INSTALL_FLAGS=[--yes]` (byte-for-byte,
   confirming acceptance criterion #2); `git-hosting + code-server` →
   `[--yes --with-git-hosting --with-code-server]` (confirming #3's order);
   `taiga only` → `[--yes --with-taiga]`; `all four` → all four `--with-*`
   flags present in append order.
5. **Ollama exact-match model-check logic**, same
   `OLLAMA_MODEL_CHECK_SCRIPT` heredoc piped real JSON through `python3`
   directly (no curl/network involved):
   - `{"data":[{"id":"qwen3:8b"},{"id":"mistral:latest"}]}` + wanted
     `qwen3:8b` → `OK`.
   - `{"data":[{"id":"qwen3:8b"}]}` + wanted `qwen3:8` → `MODEL_ABSENT:qwen3:8b`
     (confirms acceptance criterion #9: `qwen3:8` does **not** false-positive
     match `qwen3:8b`).
   - `{"data":[]}` + any model → `MODEL_ABSENT:` (empty-list branch, maps to
     the "no models available" message).
   - `not json at all` → `PARSE_ERROR` (maps to the "could not be parsed as
     JSON" message).
6. **Cross-referenced** all four `--with-*` flag names against
   `install.sh`'s own `case` argument parser (`install.sh:94-99`) to confirm
   `--with-taiga`/`--with-ollama` are real, recognized flags, not typos.
7. **Not checked** (genuinely requires the real environment, not something
   mockable without introducing a new harness this task didn't call for):
   an actual `whiptail --checklist` render/keystroke sequence in a TTY, a
   real `pct create`/`pct exec` round-trip, a real Ollama endpoint's actual
   `/v1/models` HTTP response shape end-to-end (only its JSON body shape was
   exercised, via the python check directly), and the retry loop's
   whiptail-level UI (its bash control flow — break/continue targets, which
   branch sets which variable — was traced by hand against the acceptance
   criteria instead, since it has no whiptail dependency once `FEATURES`/
   `$_ollama_check`-equivalent values are fixed).

# Implementation: BACKLOG item 15 part 2, piece 1 -- Default/Advanced entry fork in `ct/create.sh`

## Summary
Inserts a new `whiptail --menu` entry fork ("Default Install" / "Advanced
Install") into `ct/create.sh` immediately after the existing intro `msg()`
call, splitting what was previously one unconditional prompt sequence
(CTID through publish-mode, `ct/create.sh:34-158` before this change) into
an `if [ "$INSTALL_MODE" = "default" ]; then ... else ... fi` fork.
**Advanced** is that same prompt sequence relocated verbatim into the
`else` branch (only the nine repeated field literals become
`$DEFAULT_*`/`default_ctid()` references — no other change). **Default**
is a new branch that asks nothing beyond the entry menu itself: it resolves
`CTID` via the same `pvesh get /cluster/nextid` (fallback `900`) logic,
assigns every other field straight from the new `DEFAULT_*` constants,
sets `AUTH_MODE=pve` (no generated credentials), leaves all four `WITH_*`
feature flags off, sets `PUBLISH_MODE=none`/`BASE_URL=""`, then shows one
final `whiptail --msgbox` summarizing every resolved value before the
shared, unchanged `TOTP_SECRET=...`-onward code (container creation,
bootstrap, summary) runs identically regardless of which branch executed.
Single-file change, `ct/create.sh` only.

## Changes by file
- `ct/create.sh`:
  - Inserted the entry menu (`INSTALL_MODE=$(whiptail --menu ...)`)
    directly after the existing intro `msg()` call (former line 32),
    using `docs/design.md`'s finalized copy verbatim for the title,
    prompt text, and both option descriptions.
  - Added nine `DEFAULT_*` constants (`DEFAULT_CT_HOSTNAME`,
    `DEFAULT_STORAGE`, `DEFAULT_DISK_GB`, `DEFAULT_CORES`,
    `DEFAULT_MEM_MB`, `DEFAULT_BRIDGE`, `DEFAULT_IPCONFIG`,
    `DEFAULT_TEMPLATE_STORAGE`, `DEFAULT_RUN_USER`) holding the exact
    literal values each field already defaulted to before this change,
    plus a `default_ctid()` function extracting the pre-existing
    `pvesh get /cluster/nextid 2>/dev/null || echo 900` one-liner. Both
    are declared once, above the fork, read by both branches.
  - Wrapped the former unconditional prompt sequence
    (`ct/create.sh:34-158` pre-change) in
    `if [ "$INSTALL_MODE" = "default" ]; then ... else ... fi`:
    - **`if` (Default) branch**: assigns `CTID` from `default_ctid()`
      and every other container-spec field straight from its
      `$DEFAULT_*` constant; sets `AUTH_MODE="pve"` with
      `SIMPLE_USERNAME`/`SIMPLE_PASSWORD` left empty; sets all four
      `WITH_*` flags to `0` and `OLLAMA_BASE_URL_NORM`/
      `OLLAMA_MODEL_INPUT` to `""` (no taiga/ollama follow-up shown);
      sets `PUBLISH_MODE="none"`/`BASE_URL=""`; then shows one
      `whiptail --msgbox` (20x74, `docs/design.md`'s finalized copy)
      summarizing CTID, hostname, storage+disk, cores+memory,
      bridge+ipconfig, run-user, "your existing Proxmox VE credentials"
      for login, "none enabled" for optional features, and "loopback
      only" for publishing.
    - **`else` (Advanced) branch**: the pre-change prompt sequence
      relocated verbatim (re-indented one level for the `else` block),
      with only the nine repeated literal pre-fill values swapped for
      their `$DEFAULT_*` counterpart and the CTID pre-fill swapped for
      `$(default_ctid)` — no other change to prompt text, order,
      structure, or the taiga/ollama follow-up logic shipped in part 1.
  - Nothing else in the file changed: `TOTP_SECRET=...` onward (template
    resolution, `pct create`/`pct start`, in-container bootstrap,
    `TMP_ENV`/`INSTALL_FLAGS` wiring, the final `SUMMARY` msgbox) is
    untouched, outside the `if`/`else`, and runs identically for both
    paths.

## Key decisions / tradeoffs
- **Entry menu and `DEFAULT_*`/`default_ctid()` declarations both sit
  above the `if`/`else`, in the order the spec's own "Proposed approach"
  numbered them** (menu, then shared defaults, then the fork) — this is
  purely a within-file ordering choice with no behavioral effect, since
  both are read-only by the time either branch runs.
- **Advanced branch's re-indentation is the only mechanical change beyond
  the literal→`$DEFAULT_*` substitution.** Wrapping the whole block in
  `else ... fi` necessarily adds one level of indentation to every line;
  the nested `OLLAMA_MODEL_CHECK_SCRIPT` heredoc body (`<<'PYEOF' ...
  PYEOF`) was deliberately **not** re-indented, since heredoc content is
  literal text passed to `python3` — adding leading whitespace there would
  silently change the Python source (e.g. breaking `try/except`
  indentation), not just cosmetic bash formatting. Verified byte-identical
  against the pre-change heredoc (see "How to verify locally" #3).
- **Default path's msgbox uses `docs/design.md`'s finalized copy**
  ("Web UI login: your existing Proxmox VE credentials", the exact
  field-by-field wording), not `docs/spec.md`'s draft placeholder
  ("Login: your Proxmox VE credentials") — per this task's explicit
  instruction to prefer design.md's wording wherever the two differ, and
  design.md's own §Traceability table ties this specific wording to
  spec's open question #4.

## Deviations from spec
None. Implemented `docs/spec.md`'s "Proposed approach" bash near-verbatim
(entry menu, `DEFAULT_*` constants, `default_ctid()`, and the full
if/else fork), substituting `docs/design.md`'s finalized entry-menu option
copy and Default-path confirmation-msgbox copy for spec's own draft
placeholder text, exactly as instructed. `docs/spec.md`'s open question #3
(the stale "non-interactive... CT_*/SWB_* env vars" header comment at
`ct/create.sh:13-16`) is left untouched, per spec's own explicit non-goal
and the task's instruction not to fix it here.

## Known limitations
- Same as part 1: not exercised against a real Proxmox host / real
  `whiptail` TTY — there is no CI harness for `ct/create.sh` and none was
  added, matching the task's framing. What was actually run this cycle is
  listed below.
- Per spec's "Deferred to a later part": live storage-pool enumeration,
  live network-bridge enumeration, and CTID/hostname pre-validation
  (pieces 2-4) are not built here — the Default path's `local-lvm`/
  `local`/computed-CTID values can still fail at `pveam`/`pct create` time
  on a host where those don't exist or the CTID collides, with Proxmox's
  own real error, identical to today's pre-existing unvalidated behavior.
  This is spec'd as an accepted, deferred gap, not a bug introduced here.

## How to verify locally
This script only runs interactively on a real Proxmox VE host (`pct`,
`whiptail` in a real TTY) — there is no CI harness for it and none was
added, matching part 1's own testing bar. What was actually run this
cycle:

1. **Syntax check**: `bash -n ct/create.sh` → passed (no output, exit 0).
2. **Full-file shellcheck**: `shellcheck ct/create.sh` → zero
   warnings/errors anywhere in the file (not just the diff) — matches the
   pre-change file's own zero-warning baseline from part 1.
3. **Advanced-branch diff-verification against pre-change `ct/create.sh`**
   (the acceptance criterion this task flagged as the one the reviewer
   will most want independently re-confirmed):
   - Extracted the pre-change prompt block (`git show HEAD:ct/create.sh`,
     lines 34-158) and the post-change Advanced (`else`) branch body
     (lines 82-206 of the new file) — both exactly 125 lines.
   - De-indented the post-change block by one level (4 spaces), **except**
     the heredoc body lines (`OLLAMA_MODEL_CHECK_SCRIPT`'s literal Python
     source), which must stay untouched since heredocs are literal text.
   - `diff -u` between the two: the only differences are the nine
     documented literal→`$DEFAULT_*` substitutions (`CTID`'s pre-fill
     `$(pvesh get /cluster/nextid ...)` → `$(default_ctid)`; the other
     eight fields' literal pre-fills → `"$DEFAULT_*"`) — no reordering, no
     dropped/added prompts, no changed prompt text.
   - Separately confirmed the heredoc body is byte-for-byte identical
     between pre- and post-change (`diff` exit 0), ruling out any
     accidental heredoc re-indentation from the wrapping `else` block.
4. **Default-branch variable-assignment harness** (same "extract the
   logic into a standalone script, stub out non-pure dependencies"
   technique part 1's reviewer used for the checklist-parsing logic):
   extracted `ct/create.sh`'s `DEFAULT_*` constants, `default_ctid()`, and
   the full Default `if`-branch body into a standalone script; stubbed
   `whiptail()` (captures its args instead of opening a TTY) and
   `pvesh()` (returns nonzero, forcing the `900` fallback path) with bash
   functions of the same name; set `INSTALL_MODE="default"`; `eval`'d the
   extracted block; then asserted on every resulting variable named in the
   spec's acceptance criteria:
   - `CTID=900`, `CT_HOSTNAME=ai-dev-switchboard`, `STORAGE=local-lvm`,
     `DISK_GB=8`, `CORES=2`, `MEM_MB=2048`, `BRIDGE=vmbr0`,
     `IPCONFIG=dhcp`, `TEMPLATE_STORAGE=local`, `RUN_USER=dev`,
     `AUTH_MODE=pve`, `SIMPLE_USERNAME=""`, `SIMPLE_PASSWORD=""`,
     `WITH_GIT_HOSTING=0`, `WITH_CODE_SERVER=0`, `WITH_TAIGA=0`,
     `WITH_OLLAMA=0`, `OLLAMA_BASE_URL_NORM=""`, `OLLAMA_MODEL_INPUT=""`,
     `PUBLISH_MODE=none`, `BASE_URL=""` — all 20 matched exactly.
   - Also asserted the captured `whiptail --msgbox` call's message text
     contains every one of the nine summary lines the spec's acceptance
     criteria list (CTID, hostname, storage+disk, cores+memory,
     bridge+ipconfig, run-user, "your existing Proxmox VE credentials",
     "none enabled", "loopback only") — all nine present.
   - All checks passed (`ALL CHECKS PASSED`).
5. **Shared-variable-set convergence check**: extracted the top-level
   uppercase variable-assignment names from the Default `if`-body
   (`ct/create.sh:55-78`) and the Advanced `else`-body
   (`ct/create.sh:82-206`) via `grep -oE '^\s*[A-Z_][A-Z0-9_]*='`, then
   diffed the two sorted lists. Identical for all 20 spec-listed variable
   names; the only two names present in Advanced-but-not-Default are
   `FEATURES` and `OLLAMA_MODEL_CHECK_SCRIPT`, both purely internal
   working variables local to Advanced's own dialog-processing logic
   (never read by the shared `TOTP_SECRET=...`-onward code) — confirming
   no variable the downstream shared code actually consumes is
   conditionally undefined depending on which path ran.
6. **Not checked** (genuinely requires the real environment, same
   reasoning as part 1): an actual `whiptail --menu`/`--msgbox` render in
   a TTY, Cancel/Esc behavior at the new entry menu or the Default
   confirmation msgbox under `set -euo pipefail` (traced by hand against
   the existing Cancel-aborts precedent every other dialog in the file
   already has — `INSTALL_MODE=$(...)`'s assignment fails identically to
   every other `$(whiptail ...)` assignment in the file on Cancel), and a
   real `pvesh get /cluster/nextid` round-trip on an actual clustered
   Proxmox host (only its documented fallback-to-900 behavior was
   exercised, via the stubbed `pvesh` returning nonzero).
