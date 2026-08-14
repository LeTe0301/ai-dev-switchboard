# Implementation: Backlog item 13 -- surviving team branch discoverability

## Summary
Read-only discoverability for `team-*` git branches that survive a stopped
team run's `stop_team()`-driven worktree removal (`_create_worktree()`
creates each teammate's branch as `team-{run_id}-{agent}`, and the branch is
deliberately never deleted once its worktree is removed -- the existing
safety property this cycle surfaces, not changes). Three new surfaces, all
read-only, backed by one new function:
- `teams.list_team_branches(project_workdir)` -- one `git branch --list
  'team-*'` subprocess call, parsed into `{branch, run_id, agent, commit,
  subject, committer_date}` dicts, `run_id`/`agent` parsed best-effort from
  the naming convention, `[]` on any failure (never raises).
- `team-branches <project_workdir>` CLI subcommand -- prints the same list
  as JSON.
- `GET /projects/<name>/team/branches` web route, same auth/project-scoping
  guard as every other `/team/*` route, plus a "Past team branches" panel on
  the Teams page (list-only, no action buttons, fetched once per project
  page-load, not joined to the 4s `/status` poll).

No new git operations beyond the one read-only `git branch --list` call; no
merge/delete UI action, per scope.

## Root cause
Not applicable (new feature/discoverability polish, not a bugfix) --
`docs/BACKLOG.md` item 13 already diagnosed the gap at the multi-agent-teams
story's completion triage: the safety property (branches are never deleted)
was already implemented; nothing surfaced that those branches exist once
their worktree entry is dropped from `state["worktrees"]`.

## Changes by file
- `app/teams.py`:
  - `_TEAM_BRANCH_RE` -- new module-level regex, right before
    `list_team_branches()`, reusing `_RUN_ID_RE`'s own `[0-9]+-[0-9a-f]{12}`
    run_id shape (not a looser `"team-<anything>-<anything>"` split) so a
    hand-created branch merely starting with `"team-"`, or an agent name
    containing its own hyphens, is never misparsed.
  - `list_team_branches(project_workdir)` -- new, placed right after
    `_remove_worktree()` per docs/spec.md. Plain
    `subprocess.run(["git", "-C", project_workdir, "branch", "--list",
    "team-*", "--format=..."], ...)` (the same read-your-own-checkout-
    directly convention `_validate_project_for_team()` already establishes
    above it -- no `_run_run_user_command()`/`RUN_USER` crossing needed).
    Tab-split per line into the five fields docs/spec.md specifies; a
    non-git directory, a missing `git` binary, or zero matching branches
    all degrade to `[]`, never an exception.
  - `_cli_team_branches(args)` -- new, right after `_cli_team_reap()`,
    prints `list_team_branches(args.workdir)` as JSON, always exits 0 (the
    underlying function never raises, so there's no error branch to
    handle, unlike `_cli_team_status()`'s `FileNotFoundError` catch).
  - New `team-branches <workdir>` subparser + dispatch arm in
    `_parse_args()`/`main()`, following the existing subcommand list's
    exact registration shape.
- `app/app.py`:
  - New `GET /projects/<name>/team/branches` branch inside `do_GET()`'s
    existing `urllib.parse.urlsplit()`-routed `else` block, alongside
    `/team/grounding`/`/team/events`/`/team/inbox` -- same "no TOTP,
    `_authed()` only, unknown-project 404" gating as `/team/grounding`.
    Returns `teams.list_team_branches(...)`'s list directly as the JSON
    body (not wrapped in an object), matching docs/spec.md's own "returns
    the same JSON shape" acceptance criterion.
  - New CSS block (`.team-branches`/`.team-branches-title`/
    `.team-branch-row`/`.team-branch-name`/`.team-branch-commit`), reusing
    `.team-grounding`'s own font-size/color tokens -- its closest existing
    precedent (a small, muted, informational list already living in this
    same team panel area).
  - `teamBranchesCache` (new client-side cache, `name -> branch[] | null |
    undefined`, alongside `teamGroundingCache`), `fetchTeamBranches(name)`,
    `renderTeamBranches(name)` -- new functions, placed next to
    `fetchTeamGrounding()`/`renderTeamGrounding()`. `renderTeamBranches()`
    is called from both branches of `teamRow()` (idle -- including the "no
    roster members" early return -- and non-idle), so the panel is always
    present regardless of whether a run is currently active.
- `docs/ARCHITECTURE.md`: new "Reviewing a team's work after it stops"
  section (see docs/spec.md's own required content) -- `git log`/`git
  merge`/`git branch -D` against a `team-<run_id>-<agent>` branch.
- New tests: `tests/test_teams_lifecycle.py` (`ListTeamBranchesRealGitTests`,
  6 tests; `CliTeamBranchesTests`, 2 tests), `tests/test_team_routes.py`
  (`TeamBranchesEndpointTests`, 4 tests), `tests/test_team_frontend.js` (6
  tests) -- see "How to verify locally" below.

## Key decisions / tradeoffs
- **`fetchTeamBranches()` deliberately does NOT call `refresh()` itself
  once resolved**, unlike `fetchTeamGrounding()`/`fetchTeamInbox()` (both
  triggered by a direct operator action -- opening the picker, an
  escalation appearing -- that expects immediate visual feedback). This
  fetch instead fires passively as a side effect of a normal row render
  (every project, every time its row first renders with no cache entry
  yet), and docs/spec.md itself says this data "does NOT need to join the
  existing 4s `/status` poll cycle" -- the already-running
  `setInterval(refresh, 4000)` picks up the now-cached result on its own
  next tick regardless, so forcing an extra immediate `refresh()` here
  would add a redundant render on every page load for every project, for a
  panel whose own freshness requirement docs/spec.md already relaxed. In
  the worst case this means up to ~4s between the fetch resolving and the
  panel visually updating -- accepted as consistent with the spec's own
  stated timing tolerance for this specific panel.
- **`renderTeamBranches()` is called unconditionally from every `teamRow()`
  return path** (idle, the "no roster members" refusal, and non-idle) --
  not gated on `team.status`. A project's past branches are a property of
  its git history, independent of whether a run happens to be active right
  now, so hiding the panel in any one state would be an arbitrary
  restriction docs/spec.md never asked for.
- **The web route returns the bare list, not `{"branches": [...]}`** --
  docs/spec.md's acceptance criterion 3 says the route "returns the same
  JSON shape" as `list_team_branches()`, which itself returns a bare list;
  wrapping it in an object would be an unrequested shape change.

## Deviations from spec
None. Implemented per docs/spec.md's own literal command/shape
specification (`--format=%(refname:short)\t%(objectname)\t
%(committerdate:iso-strict)\t%(subject)`, full (not abbreviated)
`%(objectname)`) -- the Teams-page panel shortens the commit hash to 7
characters client-side for display only (docs/spec.md's own UI description:
"branch name, **short** commit hash, commit subject, and relative commit
date"), the full hash is still the one `list_team_branches()`/the route/the
CLI all return. "Relative commit date" is rendered as the ISO-strict
string's own `YYYY-MM-DD` date portion, not a "3 days ago"-style relative
string -- no relative-time formatting dependency existed anywhere in this
codebase already, and adding one for a small, once-per-load, informational
list was judged out of proportion to the ask; flagged here as the one place
this reading is not perfectly literal.

## Known limitations
- **No relative-time ("3 days ago") formatting** -- see "Deviations from
  spec" above; the panel shows a plain ISO date instead.
- **No live-updating branch list while a project's row stays open** -- by
  design (see "Key decisions" above): the panel reflects whatever was true
  the moment its one-time fetch resolved, refreshed only on this
  process/tab's next full page reload's own first render per project (the
  cache is keyed by project name and never invalidated). A branch created
  or deleted by an operator running plain `git` commands directly (per the
  new `docs/ARCHITECTURE.md` section) will not appear/disappear from an
  already-open tab until it's reloaded.

## How to verify locally
```
# Full existing suite, including this cycle's new tests, all green:
python3 -m unittest discover -s tests
# Ran 932 tests ... OK

# Just this cycle's new backend tests (12):
python3 -m unittest tests.test_teams_lifecycle.ListTeamBranchesRealGitTests \
  tests.test_teams_lifecycle.CliTeamBranchesTests \
  tests.test_team_routes.TeamBranchesEndpointTests -v
# Ran 12 tests ... OK

# Frontend tests (extracts the real, rendered <script> from
# app.render_page() via a Python subprocess, runs it in a Node vm sandbox
# with stub document/fetch/confirm -- no browser, no headless Chrome),
# including this cycle's new 6 tests:
node tests/test_team_frontend.js
# ALL PASS (80/80)

# Manual smoke test against a real project:
#   1. Start the app.py server, log in.
#   2. Start and stop a team run against a project (Start team -> let it
#      run or delegate at least once -> Stop team), so a teammate's
#      worktree gets removed but its branch survives.
#   3. Reload the Teams page -- the project's row should show a "Past team
#      branches" panel listing team-<run_id>-<agent>, a short commit hash,
#      the commit subject, and a YYYY-MM-DD date -- no buttons.
#   4. From a shell: `python3 app/teams.py team-branches <project_dir>`
#      prints the same data as JSON.
#   5. `curl` (with a valid session cookie)
#      `/projects/<name>/team/branches` returns the same JSON as a bare
#      array.
#   6. Follow docs/ARCHITECTURE.md's new "Reviewing a team's work after it
#      stops" section's three commands against that branch -- confirm
#      `git log`/`git merge`/`git branch -D` all work as documented.
```


# Implementation: Backlog item 8 -- AI merge-request reviewer, Gitea-only

## Summary
Extended the existing Gitea poll (`app.py`'s `_gitea_poll_if_due()`, item 2c
part 1) to also watch every switchboard-registered project's open Gitea PRs
for a configurable label (`AI_REVIEWER_LABEL`, default `ready for review`).
On the label-absent -> label-present edge, a background thread fetches the
PR's diff via Gitea's REST API, runs an operator-selected roster model
(item 6c's `teams.roster()`, any tier) against the diff plus the project's
own grounding digest (item 6b's `teams.load_grounding()`, reused read-only,
not rebuilt), and posts the review back as one Gitea PR comment via
`POST /repos/{owner}/{repo}/issues/{number}/comments` -- comment-only,
never a block/approve/merge action. An `engine`-kind reviewer never touches
the project's real working copy: it runs inside a freshly created,
`RUN_USER`-owned throwaway scratch directory (the same
`_run_run_user_command()` primitive `_create_worktree()` already uses),
unconditionally deleted afterward. Standalone/poll-triggered, not a
lead-loop tool -- no changes to `_LEAD_TOOL_NAMES`/`_lead_tools()`/any
lead-loop state machine. Off by default (`AI_REVIEWER_ENABLED=0`).

## Root cause
Not applicable (new feature, not a bugfix).

## Changes by file
- `app/app.py`:
  - New config block (right after `GITEA_POLL_INTERVAL_SECONDS`):
    `AI_REVIEWER_ENABLED` (default off), `AI_REVIEWER_LABEL` (default
    `"ready for review"`), `AI_REVIEWER_MODEL` (`"kind:name"`, unset by
    default), `AI_REVIEWER_MAX_DIFF_BYTES` (default 40000),
    `AI_REVIEWER_MAX_ATTEMPTS` (default 3), `AI_REVIEWER_STATE_FILE`
    (default `/var/lib/ai-dev-switchboard/ai-reviewer-state.json`).
  - `_gitea_api_raw(method, path)` -- new, right after `_gitea_api()`:
    identical shape but returns `(status, text)` without `json.loads`-ing
    the body, since Gitea's `.diff` endpoint returns plain text, which
    `_gitea_api()`'s own `except (..., ValueError)` would otherwise
    misclassify as a `ConnectionError`.
  - New "AI merge-request reviewer" section, right after `_gitea_poll_one()`:
    - `_load_ai_reviewer_state()`/`_save_ai_reviewer_state_entry()` --
      same tmp-file-then-`os.replace()` idiom, guarded by a new
      `_ai_reviewer_state_lock`, as `_load_gitea_repo_map()`/
      `_save_gitea_repo_map_entry()`.
    - `_ai_reviewer_record_failure(pr_key, message)` -- re-reads the
      current entry, `attempts += 1`, sets `last_error`, leaves
      `label_present` untouched (already `True` from the synchronous
      trigger-edge write).
    - `_ai_reviewer_pr_lock_for(pr_key)` -- per-PR non-blocking lock dict,
      same `_gitea_sync_lock_for()` idiom.
    - `_ai_reviewer_comment_body(model_entry, review_text, diff_truncated)`
      -- builds the exact comment format from docs/spec.md.
    - `_ai_reviewer_review_run(owner_repo, entry, pr)` -- the real work:
      fetch diff via `_gitea_api_raw()`, truncate to
      `AI_REVIEWER_MAX_DIFF_BYTES` (encode-slice-decode, same idiom
      `build_digest()` uses), resolve `AI_REVIEWER_MODEL` against a live
      `teams.roster()` lookup keyed on `(kind, name)` (split on the FIRST
      `:` only), call `teams.review_pr_diff()`, and post the comment.
      Every failure path calls `_ai_reviewer_record_failure()`; wrapped in
      a top-level `try/except Exception` as defense in depth, since this
      runs on its own background thread, not inside
      `_gitea_poll_if_due()`'s own per-repo `try/except`.
    - `_ai_reviewer_review_bg(owner_repo, entry, pr)` -- non-blocking
      dispatch, mirrors `_gitea_sync_bg()` exactly (acquire the per-PR
      lock, spawn a daemon thread, drop the dispatch if already held).
    - `_ai_reviewer_poll_repo(owner_repo, entry)` -- gated on
      `AI_REVIEWER_ENABLED`; `GET /repos/{owner_repo}/pulls?state=open`;
      per-PR label-edge detection and dispatch (see "Deviations from
      spec" below for one load-bearing correction to the spec's own
      literal retry-gating description).
  - One new call site inside `_gitea_poll_if_due()`'s existing per-repo
    loop: `_ai_reviewer_poll_repo(owner_repo, entry)` right alongside the
    existing `_gitea_poll_one(owner_repo, entry)` call, wrapped in its own
    `try/except Exception: pass` (same per-repo isolation discipline).
- `app/teams.py`: new "AI merge-request reviewer" section, right after
  `fact_check()`, before the "roster + lead loop" section:
  - `_build_review_prompt(pr_title, pr_body, diff_text, diff_truncated,
    digest)` -- new, pure string-builder, no I/O.
  - `review_pr_diff(model, workdir, pr_title, pr_body, diff_text,
    diff_truncated)` -- new public function. `digest =
    load_grounding(workdir)["digest"]`. `kind == "ollama"`: calls
    `_tier1_call_with_retry()` with `tools=[]` (a plain completion, no
    tool-calling). `kind == "engine"`: creates a scratch directory under
    `TEAM_STATE_DIR/_ai_reviewer_scratch/<token_hex(8)>` via
    `_run_run_user_command(["mkdir", "-p", scratch], cwd=TEAM_STATE_DIR)`
    (with `TEAM_STATE_DIR` itself `os.makedirs`+`chmod(0o711)`'d first so
    `RUN_USER`'s `mkdir -p` can actually `cd` into it -- see "Deviations
    from spec" below), runs `agent_run(model["name"], scratch, prompt,
    timeout=TEAM_HEADLESS_TIMEOUT_SECONDS)` against that scratch dir
    (never `workdir` itself), and unconditionally `rm -rf`s it in a
    `finally`, regardless of success, a returned `ok=False`, or
    `agent_run()` raising `ValueError`.
  - No changes to `_LEAD_TOOL_NAMES`/`_lead_tools()`/any lead-loop state
    machine.
- `scripts/gitea-configure-api.sh`: `--scopes` widened from
  `write:repository,write:user` to
  `write:repository,write:user,read:issue,write:issue`, plus a new comment
  block explaining why (issue-family scope covers PR label reads and
  PR-comment writes in Gitea's data model, since a PR is backed by an
  issue).
- `config/switchboard.env.example`: new documented `AI_REVIEWER_*` block
  (same style as the existing `GITEA_*`/`TEAM_LLM_*` blocks), plus the
  `GITEA_API_TOKEN` comment updated to mention the widened scope.
- `docs/GIT_HOSTING.md`: the token-scope sentence updated to match the
  widened `--scopes` argument.
- New `tests/test_ai_reviewer.py` (46 tests) -- see "How to verify
  locally" below.

## Key decisions / tradeoffs
- **Prompt/comment content lives entirely in `_build_review_prompt()`/
  `_ai_reviewer_comment_body()`**, both pure functions with no I/O, so
  their exact text is unit-testable without a live model call or a live
  Gitea instance -- same "pure string-builder, separately testable" split
  the spec itself calls for.
- **`_ai_reviewer_record_failure()` does a plain read-then-locked-write**
  (not a single atomically-locked read-modify-write) -- acceptable because
  the per-PR `threading.Lock` in `_ai_reviewer_review_bg()` already
  guarantees at most one `_ai_reviewer_review_run()` (and therefore at
  most one `_ai_reviewer_record_failure()` call for that same PR) is ever
  in flight at a time; a second, unrelated PR's concurrent write is still
  race-free via `_save_ai_reviewer_state_entry()`'s own lock.
- **Empty diff/status-only failure check**: `_ai_reviewer_review_run()`
  only treats a non-`200` `.diff` fetch as a failure; a genuinely empty
  (`""`) but `200` diff body proceeds to review generation unchanged. See
  "Deviations from spec" for why this reads the spec's own contradictory
  text in favor of its explicit "Edge cases" decision.

## Deviations from spec
Two load-bearing corrections to the spec's own literal text, both
necessary to satisfy the spec's own acceptance criteria -- not scope
changes, and both disclosed here per this role's "faithful scope
adherence" discipline rather than silently improvised:

1. **Retry-gating in `_ai_reviewer_poll_repo()`'s "already present" branch
   is additionally gated on `last_error is not None`, not merely
   `attempts < AI_REVIEWER_MAX_ATTEMPTS`** as docs/spec.md's "The poll
   extension" step 3 literally states ("Present now, was already present,
   `attempts < AI_REVIEWER_MAX_ATTEMPTS`: ... spawn
   `_ai_reviewer_review_bg()` again (retry)"). Traced through a concrete
   timeline: a *successful* review resets `attempts` to `0` and
   `last_error` to `None` (per the spec's own "record success" bullet).
   Since `0 < AI_REVIEWER_MAX_ATTEMPTS` is always true, the literal
   algorithm would re-dispatch a review -- and re-post a Gitea comment --
   on *every single subsequent poll interval*, forever, for as long as the
   label stays present unchanged. That directly contradicts the spec's own
   acceptance criterion: "Given the same PR polled again with the label
   still present (never removed in between), when polled repeatedly, then
   no second comment-post call happens." Gating the retry additionally on
   `last_error is not None` (i.e. the previous attempt in this episode
   actually failed) reconciles this -- it also matches the bullet's own
   prose ("a previous attempt for this same episode **FAILED** and hasn't
   exhausted its budget"), and it satisfies every other acceptance
   criterion unchanged (a failing episode still retries up to
   `AI_REVIEWER_MAX_ATTEMPTS`, then gives up silently until the label
   cycles). As a bonus, it also closes a narrower double-post race the
   spec's literal text didn't fully close on its own: if a poll interval
   is shorter than one review's own runtime, a second poll landing while
   the first review is still in flight (`attempts` still `0`,
   `last_error` still `None` from the synchronous trigger-edge write)
   would otherwise dispatch a second, redundant review under the literal
   reading -- this gate prevents that too, on top of the per-PR lock's own
   defense-in-depth role.
2. **`_ai_reviewer_review_run()`'s diff-fetch failure check tests only
   `status != 200`, not "non-200 or empty"** as docs/spec.md's "The poll
   extension" step 5 literally states. docs/spec.md's own "Edge cases"
   section directly contradicts that phrasing for exactly this scenario:
   "Empty diff (e.g. a PR with no net changes against its base) — still
   reviewed; the model gets an empty diff and grounding digest and is free
   to say so; not treated as an error." Implemented per the more specific,
   explicitly-settled "Edge cases" decision -- a `200` response with an
   empty body proceeds to review generation with an empty diff string,
   never recorded as a failure; only a non-`200` status (closed/merged PR,
   Gitea error, insufficient token scope) is treated as one.
3. **`TEAM_STATE_DIR` is explicitly `os.makedirs`+`chmod(0o711)`'d inside
   `review_pr_diff()`'s engine-kind branch before the scratch-dir `mkdir
   -p` call**, which docs/spec.md's own "`teams.review_pr_diff()`" section
   doesn't mention. This is the first place in the codebase `TEAM_STATE_DIR`
   itself (not a subdirectory already `chmod`'d by its own creator) is used
   directly as the `cwd` a `RUN_USER`-privileged `_run_run_user_command()`
   call needs to `cd` into -- without a guaranteed-executable `TEAM_STATE_DIR`,
   `RUN_USER`'s `mkdir -p` could fail on a fresh install if `SVC_USER`'s
   ambient umask ever left it non-traversable by other users. Added
   defensively, matching the exact "don't rely on SVC_USER's ambient
   umask, chmod explicitly" discipline `agent_run()`'s own `rundir`/
   prompt-file handling already documents for the identical class of
   problem.

No other deviations. The one open question the spec itself flagged (exact
Gitea token scope) was implemented per the spec's own stated best-informed
assumption (`read:issue,write:issue`) -- not independently re-decided here;
per the spec's own "not a blocker" framing, a wrong scope surfaces as an
ordinary recorded failure (`attempts`/`last_error`), not a crash, which
`test_comment_post_non_2xx_records_failure`/
`test_diff_fetch_non_200_records_failure_and_posts_no_comment` both cover
for the `403` case specifically.

## Known limitations
- **No live Gitea instance was reachable in this sandbox** -- every Gitea
  HTTP call (`_gitea_api`/`_gitea_api_raw`) is monkeypatched in
  `tests/test_ai_reviewer.py`, same convention `tests/test_gitea_poll.py`
  already established for item 2c. The exact token-scope requirement
  (`read:issue,write:issue`) is therefore unverified against a real Gitea
  1.27.1 instance -- flagged by the spec itself as the one piece of this
  design most worth confirming live during the reviewer's own testing
  pass; a wrong scope degrades cleanly to a recorded failure, not a crash,
  per the acceptance criteria.
- **No live model (Ollama or an `engines.d` engine) was run** -- the
  `_tier1_call_with_retry()`/`agent_run()` calls `review_pr_diff()` makes
  are monkeypatched in tests, same "black-box the already-tested lower
  layer" convention `tests/test_teams_board.py`'s own Part A/Part B split
  established for item 7 part 1. `_tier1_call_with_retry()` and
  `agent_run()` each already have their own dedicated test coverage
  elsewhere (`tests/test_teams_lead.py`, `tests/test_teams_headless.py`).
- **No web UI** -- per the spec's own explicit non-goal, the review's only
  visible surface is the Gitea PR comment itself. No switchboard-side
  model/label picker, no reviewed-PRs list, no runtime override of
  `switchboard.env` -- exactly as scoped.
- **`_ai_reviewer_record_failure()`'s read-then-write is not perfectly
  atomic** across two truly concurrent writers for the *same* PR -- see
  "Key decisions" above for why this is acceptable given the per-PR lock
  that already exists one layer up.

## How to verify locally
```
# Full existing suite, including this cycle's new tests, all green:
python3 -m unittest discover -s tests -v
# Ran 920 tests ... OK

# Just this cycle's new tests:
python3 -m unittest tests.test_ai_reviewer -v
# Ran 46 tests ... OK

# Manual smoke test against a real Gitea instance (requires
# install.sh --with-git-hosting, Gitea toggled on, and
# scripts/gitea-configure-api.sh re-run to pick up the widened token
# scope):
#   1. Set AI_REVIEWER_ENABLED=1, AI_REVIEWER_MODEL=<a real roster
#      entry, "kind:name">, restart the service.
#   2. Register a project via Gitea (item 2b's "+ New project" flow) so
#      it's present in GITEA_REPO_MAP_FILE.
#   3. Open a PR against that project's Gitea repo, add the
#      AI_REVIEWER_LABEL label ("ready for review" by default).
#   4. Within GITEA_POLL_INTERVAL_SECONDS (default 45s), confirm a
#      comment appears on the PR starting with "**AI code review**".
#   5. Remove and re-add the label -- confirm exactly one NEW comment
#      appears (not zero, not two).
#   6. Inspect AI_REVIEWER_STATE_FILE directly (no UI) to see
#      label_present/attempts/reviewed_at/last_error per PR.
```

# Implementation: Backlog item 7 part 2 -- web UI for approving/rejecting board_write proposals

## Summary
Extended the Teams page's existing `blocked_ask_user` web UI to also handle
`blocked_board_write` runs, reusing part 1's already-shipped
`resolve_board_write()` and the `inbox.json` `kind` discriminator exactly
where their shapes already match `ask_user`'s, and adding new
verb-specific (`set_status`/`amend_description`/`append_comment`)
presentation only where a board-write proposal's own shape needs it. Backend:
a new `escalation_kind` field on `/status`, a new `blocked_board_write`
branch on `GET .../team/inbox` (with a best-effort Taiga subject-enrichment
read), a new `POST /projects/<name>/team/board-resolve` route, and a
one-tuple fix closing `POST .../team/stop`'s disclosed no-op gap. Frontend:
status-strip copy distinction, a verb-specific proposal panel (Approve/
Reject, no free-text field), `doTeamBoardResolve()` wired through the
existing TOTP-retry machinery, and two new merged-event-feed classifiers
(`board-write-proposal`/`board-write-resolved`) checked ahead of the
existing generic `tool_use`/`meta.resolved` branches per the spec's called-
out ordering requirement.

## Root cause
Not applicable (new feature, not a bugfix).

## Changes by file
- `app/app.py`:
  - `/status` handler -- `team_status` map gains `"blocked_board_write":
    "blocked"`; `waiting_on_you` now also true for `"blocked_board_write"`;
    new `escalation_kind` field (`"ask_user"`/`"board_write"`/`None`), a
    direct string comparison against the already-loaded `run["status"]`,
    no extra read.
  - `_handle_team_inbox()` -- new `blocked_board_write` branch, factored
    into a sibling `_handle_team_inbox_board_write(state)` (mirrors the
    ask_user branch's own missing/malformed-inbox fallback discipline
    exactly), plus a best-effort `teams.taiga_board.resolve_session()` +
    `get_userstory()` subject-enrichment read (catches
    `teams.taiga_board.TaigaPushError`, degrades to omitting `"subject"`
    -- never a 500, never blocks Approve/Reject). Calls through
    `teams.taiga_board`, not a separately-imported module, matching
    `tests/test_teams_board.py`'s own established "monkeypatch the module
    the caller imports it through" convention (`app/teams.py` already
    does `import taiga_board` at module level; `app/app.py` already does
    `import teams`, so `teams.taiga_board` is the one live reference).
  - New `POST /projects/<name>/team/board-resolve` route in `do_POST()`
    -- mirrors `/team/resolve`'s validation shape exactly (unknown
    project 404; empty `run_id` falls back to
    `teams.latest_run_for_project()`; non-empty `run_id` validated via
    `teams._RUN_ID_RE` then ownership-checked; `status !=
    "blocked_board_write"` -> 400; `action not in ("approve", "reject")`
    -> 400 before calling `resolve_board_write()`; the same defensive
    "a team thread is already running" 400 check; on success, starts
    `_run_team_in_background()` on a new thread exactly like
    `/team/resolve` does).
  - `POST .../team/stop` -- one-tuple fix: `("running", "blocked_ask_user")`
    -> `("running", "blocked_ask_user", "blocked_board_write")`, closing
    part 1's disclosed no-op gap.
  - Frontend JS (inline in the page template):
    - `renderTeamStatusStrip()` -- branches on `team.escalation_kind` when
      `blocked && waiting_on_you`: "⚠ Board write pending approval" for
      `board_write`, unchanged "⚠ Waiting on you" for `ask_user`.
    - `renderEscalationPanel()` -- shared fetch/cache/loading/fetch-failure
      preamble unchanged; the "already resolved" race message now branches
      on `escalation_kind` for distinct copy (`board_write`: "This
      proposal was already approved or rejected."; `ask_user`'s own text
      byte-for-byte unchanged); then branches to a new
      `renderBoardWriteEscalationPanel(name, cached)` for `board_write`,
      the existing ask_user form body otherwise unchanged.
    - New `renderBoardWriteEscalationPanel()` -- verb-specific summary line
      (`set_status`: "Move **subject** from **current** to **proposed**.";
      `amend_description`: "Replace **subject**'s description" + Current/
      Proposed `<textarea readonly>` comparison blocks; `append_comment`:
      "Add a comment to **subject**" + a single Comment-text block, no
      current-value comparison), lead's note (truncated to 200 chars,
      existing precedent) if non-null, and Approve/Reject buttons -- no
      free-text field. Subject falls back to `#ref` when the enrichment
      read failed.
    - New `truncateText(text, max)` helper, the 200-char-plus-ellipsis
      precedent extracted from `teamFeedEventBody()`'s own fact-check-match
      rendering (used for the panel's one-line summary/note; the longer
      description/comment comparison blocks instead rely on the scrollable
      `max-height: 200px` box itself per docs/design.md, not hard
      truncation).
    - New `doTeamBoardResolve(name, action)` -- clears the row's message
      slot, records `action` into a new client-side map
      `teamBoardResolveAction[name]` (same "small map keyed by name,
      survives a TOTP retry" pattern `teamEscalationOther` already
      establishes -- see "Key decisions" below for why this was chosen
      over `pendingToggle`), then `toggle('team-board-resolve', name,
      true, null)`.
    - `actionPath()`/`actionBody()`/`handleActionResult()` gain
      `'team-board-resolve'` cases: path
      `/projects/<name>/team/board-resolve`; body `{action:
      teamBoardResolveAction[name]}`; result handling mirrors
      `'team-resolve'`'s own inline-message-slot pattern exactly (success:
      "✓ Board write resolved", clears the inbox cache; failure: "✕ Error:
      ..."). The 428-overlay label switch gains `'Resolving board write: '
      + name`.
    - `teamFeedEventKindClass()`/`teamFeedEventBody()` gain two new checks,
      placed **before** the existing generic `tool_result`+`meta.resolved`
      -> `'resolved'` branch per the spec's called-out ordering
      requirement: `tool_use` + `meta.verb !== undefined` ->
      `'board-write-proposal'`; `tool_result` + `meta.approved !==
      undefined` -> `'board-write-resolved'` (parses
      `resolve_board_write()`'s own literal `outcome_summary`/
      `full_result_text` strings to distinguish "approved and applied" /
      "approved but Taiga rejected: ..." / "rejected by human").
    - New CSS: `.team-escalation-proposal`/`-summary`/`-label`/`-box`/
      `-note`, reusing every existing color/spacing token
      (`.team-escalation`'s own wrapper, `#0a0a0a`/`#333`/`#ccc` for the
      read-only comparison boxes per docs/design.md's own accessibility
      analysis) -- no new components, no new libraries.
- `tests/test_team_routes.py`:
  - `TeamStopEndpointTests`: `test_status_maps_every_run_status_to_the_
    coarse_label` extended with `"blocked_board_write": "blocked"`; new
    `test_stop_on_blocked_board_write_now_actually_stops`.
  - `test_status_idle_when_no_run_ever_started` updated for the new
    `escalation_kind: None` field (exact-dict-equality regression).
  - `StatusRosterAndCompositionTests`: `test_waiting_on_you_true_only_for_
    blocked_ask_user_never_for_escalated_max_rounds` extended with
    `"blocked_board_write": True`; new `test_escalation_kind_field`.
  - `TeamInboxEndpointTests`: new board_write-branch tests (exact persisted
    shape + subject enrichment; Taiga-unreachable graceful degradation;
    missing/malformed inbox.json fallback) plus a regression test pinning
    the ask_user branch's response shape byte-for-byte.
  - New `TeamBoardResolveEndpointTests` class, mirroring
    `TeamResolveEndpointTests`'s own structure/naming (unknown project;
    wrong status; invalid/missing action; cross-project run_id; path-
    traversal run_id; TOTP 428/403; approve/reject each resolving and
    starting the background thread; a genuine two-concurrent-approves race
    proving exactly one winner).
  - New module-level `_patch_taiga_board()` test helper, mirroring
    `tests/test_teams_board.py`'s own helper of the same name/shape exactly
    (monkeypatches `teamsmod.taiga_board`'s named attributes for one test,
    restored via `addCleanup`).
- `tests/test_team_frontend.js`: new tests for the board_write status-strip
  copy; `renderEscalationPanel()`'s three verb-specific layouts (including
  the `#ref` fallback and the distinct "already resolved" race copy);
  `doTeamBoardResolve()`'s approve/reject dispatch, success/error result
  handling, and 428-then-retry (asserting the retried request resends the
  *same* action the operator originally clicked); `teamFeedEventKindClass()`/
  `teamFeedEventBody()`'s two new classifiers for all three resolution
  outcomes, plus a regression test proving an ask_user-shaped
  `tool_result` (`meta.resolved` only, no `meta.approved`) still renders
  the unchanged generic `'resolved'`/`'Answer: ...'` output.

## Key decisions / tradeoffs
- **`doTeamBoardResolve()`'s TOTP-retry plumbing** (spec's own "Open
  questions", left as a developer judgment call): chose the "small
  client-side map keyed by name" pattern (`teamBoardResolveAction[name]`,
  set before `toggle()`'s first optimistic POST) over reading the global
  `pendingToggle` context inside `actionBody()`. Reason: `pendingToggle` is
  only populated once a 428 has actually been seen (`handleActionResult()`
  sets it inside the `r.status === 428` branch) -- `actionBody()` is also
  called on the very FIRST, optimistic (no-code) POST, before any 428 has
  happened, at which point `pendingToggle` is still `null`/stale from a
  previous action. A client-side map keyed by name, set synchronously by
  `doTeamBoardResolve()` before `toggle()` is ever called, is correct on
  both the first attempt and any retry -- exactly the same reasoning
  `team-start`'s own task/lead/members fields already apply (read from a
  live DOM element/client-side mirror, never from `pendingToggle`).
- **Truncation**: used the spec's own 200-char default (matching
  `teamFeedEventBody()`'s existing fact-check-match precedent) for the
  one-line proposal summary and the lead's note, but relied on the
  description/comment comparison blocks' own `max-height: 200px;
  overflow-y: auto` scrollable box (per docs/design.md's explicit
  recommendation) rather than a second, harder text truncation for those
  -- the box itself already bounds the panel's vertical footprint
  regardless of how long the underlying text is, so a second truncation
  layer would only lose information without solving a layout problem the
  scroll box doesn't already solve.
- **Subject enrichment read placement**: implemented as a private sibling
  method `_handle_team_inbox_board_write(state)` rather than inlining a
  second branch into `_handle_team_inbox()` itself -- keeps the existing
  ask_user branch's own code path completely untouched (byte-for-byte,
  confirmed by a dedicated regression test) and keeps the new branch's
  own missing/malformed-inbox fallback logic (which is structurally
  different from ask_user's -- different field names, a different
  fallback shape) legible on its own rather than interleaved with it.
- **Approve/Reject button styling**: reused the existing `.team-btn` class
  verbatim for both buttons (same class the ask_user panel's own "Submit
  answer" button, "Start team", "Stop team", and "Deploy" already use) --
  docs/design.md's own accessibility section speculates about a
  `#4da6ff`/blue button background, but no such button variant exists
  anywhere else on this page; `.team-btn`'s actual shipped
  green/`#34c759` background already satisfies "both primary-style, not
  Approve highlighted and Reject greyed" (both buttons render identically),
  so reusing it exactly (per this project's "match existing conventions,
  don't invent a new component" discipline) was chosen over introducing a
  new button variant color the rest of the page doesn't have.

## Deviations from spec
None substantive. Two of the spec's own explicitly-left-open points were
resolved during implementation, both recorded above under "Key decisions"
rather than left ambiguous:
- `doTeamBoardResolve()`'s exact TOTP-retry plumbing mechanism (spec's
  "Open questions" -- explicitly a developer's call).
- Exact truncation length/strategy for long `value`/`note`/
  `current_value.description` text (spec's "Open questions" -- explicitly
  "not a blocker either way").

One minor, disclosed wording deviation from docs/design.md's own ASCII
mockups: the board-write-proposal event-feed line (§6, `teamFeedEventBody()`)
literally reuses the transcript's own `args_summary` text as its trailing
segment (e.g. `board_write (set_status): ref #42 — board_write(set_status,
ref=42)`), per the spec's own explicit formula (`'board_write (' +
meta.verb + '): ref #' + meta.ref + ' — ' + esc(e.text)`, "reusing the
transcript's own args_summary text, already verb/ref-specific") -- not the
design doc's more polished illustrative example text ("— 'Move to In
progress per delegate'"), which does not correspond to any string the
backend actually persists in this transcript entry's `text` field
(`team_step()`'s own `board_write` branch sets it to exactly
`args_summary`, not the proposal's `note`). Implemented per the spec's
literal, backend-accurate formula rather than the design doc's
illustrative (but not backend-derived) copy.

## Known limitations
- Same live-Taiga-instance caveat part 1 already disclosed: no live Taiga
  server was reachable in this sandbox, so the subject-enrichment read's
  exact behavior against a real board (response shape, timing) is verified
  only via the same monkeypatched-`teams.taiga_board` seam part 1's own
  tests already established, not against a live instance.
- The board-write proposal panel's Approve/Reject buttons are visually
  identical (both `.team-btn`, both green) per docs/design.md's own "both
  primary-style" requirement -- an operator relies on the button *label*,
  not color, to distinguish them. This matches the existing page's own
  established pattern (Start/Stop/Deploy/Submit-answer all share this one
  button style) and was a deliberate "match existing conventions" choice,
  not an oversight, but is worth naming explicitly since docs/design.md's
  accessibility section briefly speculated about a different color.

## How to verify locally
```
# Full existing suite, including this cycle's new/extended tests, all green:
python3 -m unittest discover -s tests -v

# Just this cycle's new/extended backend route tests:
python3 -m unittest tests.test_team_routes -v

# Frontend tests (extracts the real, rendered <script> from
# app.render_page() via a Python subprocess, runs it in a Node vm sandbox
# with stub document/fetch/confirm -- no browser, no headless Chrome):
node tests/test_team_frontend.js

# Manual smoke test against a real run (requires
# ~/.config/ai-dev-switchboard/taiga-push.env for the subject-enrichment
# read to succeed -- omitting it still works, "subject" is simply absent):
#   1. Start the app.py server, log in, clear the TOTP gate once.
#   2. Start a team whose lead calls board_write (or drive one manually via
#      the CLI's team-start, then have the lead call board_write).
#   3. Refresh the Teams page -- status strip should read "⚠ Board write
#      pending approval", not the old "idle" fallback.
#   4. The escalation panel should show the verb-specific proposal
#      (subject from Taiga if reachable, else "#<ref>"), with Approve/
#      Reject buttons and no free-text field.
#   5. Click Approve or Reject -- confirm the TOTP overlay appears on the
#      first mutating action of the session, and that the row's message
#      slot shows "✓ Board write resolved" (or a clear error).
#   6. Click "Stop team" while a board_write proposal is still pending on
#      a different run -- confirm it actually stops (session_removed/
#      worktrees in the response), not the old silent no-op message.
```

## Post-review fix (test-review.md must-fix #1)
`docs/test-review.md`'s item 7 part 2 review found (verdict: changes
requested) that `TeamBoardResolveEndpointTests.test_approve_resolves_and_
starts_background_thread` and `test_reject_resolves_and_starts_background_
thread_no_taiga_call` never actually verified the "starts the background
driving thread" half of their own name/criterion -- every assertion in both
tests was satisfied by `resolve_board_write()`'s own synchronous side
effects (inbox move, history entry) alone, proven via the reviewer's
revert-and-fail (deleting the route's entire `cancel_event`/`Thread`/
`_team_threads_set()`/`t.start()` block left all 12 tests in the class
passing). The route's production code in `app/app.py` was already correct
(confirmed to mirror `/team/resolve`'s dispatch exactly) -- this was a
test-coverage gap only, no production code change.

Fixed per the reviewer's recommended fix (a): both tests now install a
`_record_team_threads()` fixture that rebinds `app.py`'s own module-level
`threading` name (not the shared `threading` module -- rebinding the
module's own attribute globally also intercepted `ThreadingHTTPServer`'s
per-connection threads in an earlier attempt, inflating the recorded count
to 3) to a thin proxy whose `Thread` is a subclass recording `(target,
args)` on every construction before delegating to the real
`threading.Thread`. This lets the background thread still genuinely run
(so the existing inbox/history assertions stay meaningful) while adding an
unambiguous, race-free assertion that `_run_team_in_background` was
constructed with `(name, run_id, cancel_event)` -- stronger than
`TeamResolveEndpointTests`'s existing `elapsed < 3.0` timing proxy, which a
fast stub lead could satisfy even with no thread dispatch at all.

Re-verified with the same revert-and-fail technique the reviewer used:
temporarily removed the route's thread-dispatch block again -- both
strengthened tests failed (`AssertionError: 0 != 1` on the recorded-thread
count) while the other 10 tests in the class stayed green; restored the
block and reran -- all 12 pass again, `git diff --stat app/app.py`
byte-identical to the pre-probe state (+302/-11, matching the reviewer's
own probe). Full suites also reran clean: `python3 -m unittest discover -s
tests` -> `Ran 874 tests` / `OK`; `node tests/test_team_frontend.js` ->
`ALL PASS (74/74)`.

No change to `app/app.py`, `docs/design.md`, or any other file -- this fix
is scoped to `tests/test_team_routes.py` only, per the dispatch's explicit
"do NOT touch the should-fix" instruction (the WCAG contrast finding on
`.team-btn` is unchanged, still open as a non-blocking follow-up).

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
