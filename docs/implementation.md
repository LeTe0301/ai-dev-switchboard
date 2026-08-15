# Implementation: Backlog item 17 part 2 -- GitHub poll-loop wiring + item 8 host-agnostic dispatch

## Summary
Wires part 1's inert GitHub client into item 8's AI merge-request reviewer,
making it host-agnostic (Gitea vs. GitHub) and adding the one new poll loop
that both surfaces GitHub PR/label activity and doubles as item 8's GitHub
dispatch trigger. No new UI, no new route (per `docs/spec.md`'s own settled
scope decisions #1/#2):
- **`_ai_reviewer_poll_repo()` / `_ai_reviewer_review_run()` / `_ai_reviewer_
  review_bg()`** (`app/app.py`, item 8) all gain a leading `host: "gitea" |
  "github"` parameter. Every line of label-edge-detection, per-PR locking,
  retry-gating, and state-persistence logic is unchanged; only the
  PR-list-fetch, diff-fetch, and comment-post calls branch per host (Gitea
  via the existing `_gitea_api`/`_gitea_api_raw`, GitHub via part 1's
  `github_list_open_prs`/`github_pr_diff`/`github_post_pr_comment`).
- **`_ai_reviewer_pr_key(host, owner_repo, number)`** -- new. Gitea's key
  format is byte-for-byte unchanged (`"owner/repo#number"`, no prefix) --
  backward-compatible with every already-persisted `AI_REVIEWER_STATE_FILE`
  entry. GitHub gets a `"github:"`-prefixed key that structurally cannot
  collide with a Gitea key (no colon allowed in `owner/repo`).
- **`_github_poll_if_due()`** -- new. Throttled (`GITHUB_POLL_INTERVAL_
  SECONDS`, default 120), lock-guarded, same double-checked-lock shape as
  `_gitea_poll_if_due()`. Its only per-repo work is calling
  `_ai_reviewer_poll_repo("github", ...)` for every local project whose
  `detect_project_origin()` resolves to `github.com` AND whose `owner/repo`
  is listed in the new `AI_REVIEWER_GITHUB_REPOS_FILE` allowlist. Called
  from `/status`, right after the existing `_gitea_poll_if_due(gitea_on)`
  call.
- **`AI_REVIEWER_GITHUB_REPOS_FILE`** -- new, hand-edited, operator-
  maintained JSON array of `"owner/repo"` strings (default `/etc/ai-dev-
  switchboard/ai-reviewer-github-repos.json`), same `DEPLOY_MAP_FILE`
  "app.py only ever reads it" contract. A GitHub-origin project is only
  polled/reviewed when `AI_REVIEWER_ENABLED=1`, `GITHUB_TOKEN` is set, AND
  its `owner/repo` is in this file -- all three, not any one alone. This is
  the one deliberate deviation from "just extend Gitea's behavior
  unchanged": every Gitea repo is automatically in scope once enabled
  (this switchboard created it), but a GitHub-origin project can be
  arbitrary third-party infrastructure (item 16's clone-by-URL), a
  materially different trust boundary.
- `teams.review_pr_diff()` (`app/teams.py`) is unchanged -- already
  host-agnostic, confirmed by this cycle rather than assumed.

## Root cause
Not applicable (new feature/wiring, not a bugfix).

## Changes by file
- `app/app.py`:
  - New constants, next to the existing `AI_REVIEWER_*` block:
    `AI_REVIEWER_GITHUB_REPOS_FILE` (default `/etc/ai-dev-switchboard/
    ai-reviewer-github-repos.json`), `GITHUB_POLL_INTERVAL_SECONDS`
    (default 120).
  - `AI_REVIEWER_ENABLED`'s doc comment updated (no longer says
    "Gitea-only... GitHub is item 17, not yet built") to describe the
    host-agnostic behavior and the allowlist gate.
  - `_ai_reviewer_pr_key(host, owner_repo, number)` -- new, described above.
  - `_ai_reviewer_review_run(host, owner_repo, entry, pr)` -- gains the
    leading `host` param; internal branching per host at exactly three
    points (diff fetch, comment post, and `pr_key` construction via the new
    helper); every other line (truncation, `AI_REVIEWER_MODEL` resolution
    against `teams.roster()`, the `teams.review_pr_diff()` call, comment-
    body construction, state-persistence, the outer defense-in-depth
    `except Exception`) is untouched.
  - `_ai_reviewer_review_bg(host, owner_repo, entry, pr)` -- gains the
    leading `host` param, threaded through to `_ai_reviewer_review_run()`
    and into the now-host-prefixed `pr_key` used for the per-PR lock.
  - `_ai_reviewer_poll_repo(host, owner_repo, entry)` -- gains the leading
    `host` param; the PR-list-fetch branches (Gitea: `_gitea_api("GET",
    ".../pulls?state=open")`, unchanged; GitHub: `github_list_open_prs()`,
    gated on `GITHUB_TOKEN` first), everything else (label-edge detection,
    the documented `last_error is not None` retry-gating deviation from
    item 8's own literal spec text, the "arm the next add as a fresh
    episode" branch) is unchanged.
  - `_gitea_poll_if_due()`'s one call site updated:
    `_ai_reviewer_poll_repo(owner_repo, entry)` ->
    `_ai_reviewer_poll_repo("gitea", owner_repo, entry)` -- the only change
    to that function.
  - `_load_ai_reviewer_github_repos()` -- new, next to `_load_deploy_map()`'s
    own idiom: reads `AI_REVIEWER_GITHUB_REPOS_FILE`, returns a `set` of
    non-empty strings from a JSON array, or an empty `set()` on any
    missing/malformed/wrong-shape input -- never raises.
  - `_github_poll_lock`/`_github_poll_last_at`/`_github_poll_if_due()` --
    new, described above. Top guard: `if not AI_REVIEWER_ENABLED or not
    GITHUB_TOKEN: return`. Loads the allowlist and returns immediately if
    empty (skips even the `instance_names()` walk). For each local project:
    `detect_project_origin()` wrapped in its own `try/except Exception:
    continue` (one project's detection failure doesn't stop the rest, same
    discipline `_gitea_poll_if_due()`'s per-repo `try/except` already
    established), filtered to `kind == "github"` with both `owner`/`repo`
    present, filtered again to allowlist membership, then
    `_ai_reviewer_poll_repo("github", owner_repo, {"name": name})` wrapped
    in its own `try/except Exception: pass`.
  - `/status` handler (`do_GET()`): one new line,
    `_github_poll_if_due()`, directly after the existing
    `_gitea_poll_if_due(gitea_on)` call. No new route.
- `config/switchboard.env.example`:
  - `GITHUB_TOKEN`'s comment block updated (no longer "no poll loop... has
    no visible effect until a later part") to describe that it's now wired
    into item 8.
  - `AI_REVIEWER_ENABLED`'s surrounding comment block updated to describe
    the host-agnostic behavior and the Gitea-automatic-vs-GitHub-allowlisted
    scope distinction.
  - New `AI_REVIEWER_GITHUB_REPOS_FILE` and `GITHUB_POLL_INTERVAL_SECONDS`
    documented, commented-out entries next to `AI_REVIEWER_STATE_FILE`.
- New `config/ai-reviewer-github-repos.json.example` -- a one-line example
  (`["myorg/myrepo"]`), mirroring `config/deploy-map.json.example`'s own
  precedent for a hand-edited, operator-authored JSON allowlist.
- `tests/test_ai_reviewer.py` (extended, all in Part A):
  - Every existing Gitea-path test's call site updated to pass `"gitea"` as
    the new leading argument (`_ai_reviewer_poll_repo`/`_ai_reviewer_
    review_run`/`_ai_reviewer_review_bg`) -- zero assertion changes, proving
    the refactor is behavior-preserving for the existing Gitea path.
  - New `AiReviewerPrKeyTests` (3 tests) -- key format/prefix/collision-proof.
  - New `AiReviewerPollRepoGithubTests` (5 tests) -- mirrors
    `AiReviewerPollRepoTests` for `host="github"`: missing-token no-op,
    label-add-edge dispatch with the `"github:"`-prefixed state key,
    no-redispatch-after-success, non-ok `github_list_open_prs()` result
    skipped without raising, and a note that allowlist gating is
    deliberately NOT this function's job (that's `_github_poll_if_due()`'s).
  - New `AiReviewerReviewRunGithubTests` (4 tests) -- mirrors
    `AiReviewerReviewRunTests` for `host="github"`: diff-fetch failure,
    success (comment posted + state reset), comment-post failure, diff
    truncation.
  - New assertion in `AiReviewerReviewBgConcurrencyTests` -- a Gitea and a
    GitHub dispatch for the identical `owner/repo#number` use different
    per-PR locks (host-prefixed `pr_key`).
  - New `AiReviewerGithubReposAllowlistTests` (6 tests) -- the loader's
    never-crash contract (missing file, malformed JSON, non-list JSON,
    non-string entries dropped, empty array).
  - New `GithubPollIfDueTests` (11 tests) -- throttle/lock shape (mirroring
    `tests/test_gitea_poll.py`'s `GiteaPollIfDueTests`), plus
    `AI_REVIEWER_ENABLED`/`GITHUB_TOKEN`/allowlist gating, per-project
    isolation on both `detect_project_origin()` and `_ai_reviewer_poll_repo()`
    raising, and the "missing owner/repo" edge case.
  - New `GithubPollEndToEndReviewTests` (6 tests) -- a single full pass
    through `_github_poll_if_due()` -> `_ai_reviewer_poll_repo()` ->
    `_ai_reviewer_review_bg()`/`_run()` with only the lowest-level GitHub
    client functions mocked (`github_list_open_prs`/`github_pr_diff`/
    `github_post_pr_comment`), proving every acceptance criterion
    end-to-end rather than piecewise: label-add posts exactly one comment,
    a repeated poll with the label still present posts no second comment,
    label-removed-then-readded is exactly one new episode (not zero, not
    two), a non-allowlisted repo is never reviewed even with the label
    present, and a diff-fetch failure records the error with no comment
    posted.

No `app/teams.py` change. No new Flask route, no new HTML/JS template
change, no schema/data-model change, no new privileged script, no new
sudoers entry.

## Key decisions / tradeoffs
- **The GitHub poll loop's only job is calling `_ai_reviewer_poll_repo()`**
  -- confirmed directly (not assumed) that Gitea's own poll pass doesn't
  surface anything else via `/status` either (`gitea_sync` is purely local-
  checkout-sync bookkeeping, out of scope for GitHub per part 1's own
  non-goals; PR/branch listing has never been poll-driven for either host).
  This keeps `_github_poll_if_due()`'s scope narrow and exactly matched to
  what actually needs periodic background work: a label being added is an
  event a poll has to notice, everything else here is on-demand.
  See `docs/spec.md`'s own "Settled scope decisions" #1 for the full
  reasoning this cycle inherited verbatim.
- **`_ai_reviewer_poll_repo("github", ...)` itself has no allowlist check.**
  The allowlist gate lives entirely in `_github_poll_if_due()`, the only
  caller that ever passes `host="github"` in production. This mirrors how
  `_ai_reviewer_poll_repo("gitea", ...)` has never had a `GITEA_REPO_MAP_
  FILE` membership check of its own either -- the caller (`_gitea_poll_if_
  due()`) already filtered to registered repos before calling it. Keeping
  the gate at the caller, not duplicated inside the shared function, avoids
  two independent "is this repo in scope" code paths that could drift.
  `AiReviewerPollRepoGithubTests.test_allowlist_gating_is_not_this_
  functions_job` makes this boundary explicit rather than leaving it
  implicit.
- **The allowlist is checked, and the empty case returns, BEFORE
  `instance_names()` is even called.** A no-allowlist install (the shipped
  default) pays zero cost beyond one failed/empty file read per poll
  interval -- no directory walk, no `detect_project_origin()` subprocess
  calls, confirmed by `GithubPollIfDueTests.test_empty_allowlist_makes_no_
  calls_and_skips_instance_walk` asserting `instance_names()` itself was
  never invoked.
- **`_github_poll_if_due()` has no `_on`/enabled-toggle parameter**, unlike
  `_gitea_poll_if_due(gitea_on)`. Gitea needs one because it's a locally-run
  Docker service that can be stopped independently of `GITEA_ENABLED`;
  GitHub isn't a service this switchboard runs at all, so
  `AI_REVIEWER_ENABLED`/`GITHUB_TOKEN`/the allowlist are the complete and
  only gates, all checked inside the function itself -- matches
  `docs/spec.md`'s own "Proposed approach" #3 exactly.
- **Reused `test_ai_reviewer.py` for every new test class rather than
  splitting GitHub-path coverage into `test_github_api.py`.** `docs/spec.md`
  left this as the developer's call. Every new class here is fundamentally
  about item 8's dispatch/state-machine logic (which `test_ai_reviewer.py`
  already owns and whose `teams.roster()`/`teams.review_pr_diff()`
  monkeypatch conventions the GitHub-path review-run tests need), not about
  the GitHub HTTP client itself (which `test_github_api.py` already owns
  and leaves untouched -- no changes needed there since none of its
  existing functions changed shape).

## Deviations from spec
None. `_ai_reviewer_pr_key()`, the host-branching inside `_ai_reviewer_
poll_repo()`/`_ai_reviewer_review_run()`/`_ai_reviewer_review_bg()`, the
allowlist loader, and `_github_poll_if_due()`'s throttle/gating shape all
match `docs/spec.md`'s "Proposed approach" pseudocode and "Acceptance
criteria" as written -- no ambiguity requiring a judgment call was hit
while implementing this part. The one pre-existing, disclosed deviation
from item 8's own original spec text (`_ai_reviewer_poll_repo()`'s retry
branch gated on `last_error is not None`, not merely `attempts <
AI_REVIEWER_MAX_ATTEMPTS`) is preserved verbatim for both hosts, per this
cycle's own explicit instruction not to reintroduce the literal-but-broken
reading.

## Known limitations
- **The pre-existing, already-disclosed episode/lock race is inherited
  identically for GitHub, not fixed** (`docs/BACKLOG.md` item 8's own
  status note: the per-PR lock is keyed only on `pr_key`, not episode --
  narrow but real). The host-prefixed `pr_key` means a Gitea and a GitHub
  dispatch for the same nominal PR number can never contend on each other's
  lock, but within a single host+PR, the same narrow race item 8 originally
  disclosed still applies unchanged. Explicitly out of scope for this cycle
  per `docs/spec.md`'s own "Non-goals".
- **No live GitHub poll pass was exercised against a real `api.github.com`
  or a real Gitea instance in this session** -- every test in this cycle's
  additions monkeypatches `github_list_open_prs`/`github_pr_diff`/
  `github_post_pr_comment` (or, for the throttle/gating-only tests,
  `_ai_reviewer_poll_repo` itself), following this file's own established
  "no real network/Docker call in this file" convention. A real end-to-end
  smoke test (a real `GITHUB_TOKEN`, a real repo listed in a real
  `AI_REVIEWER_GITHUB_REPOS_FILE`, a real label added to a real open PR,
  `/status` polled until the poll interval elapses) was not performed --
  see "How to verify locally" below for the manual steps an operator with
  real credentials could run to close this gap by hand.
- **`_github_poll_if_due()`'s per-project `detect_project_origin()` call
  runs once per allowlisted-repo-check per poll interval, over every local
  project (via `instance_names()`), not just allowlisted ones** -- this
  mirrors part 1's own accepted cost characterization (one unprivileged
  `git remote get-url origin` subprocess per project, cheap, no new
  privilege boundary), and is bounded by `GITHUB_POLL_INTERVAL_SECONDS`
  (120s default) exactly like Gitea's own per-registered-project walk is
  bounded by `GITEA_POLL_INTERVAL_SECONDS`. Not a new limitation introduced
  by this cycle -- `docs/spec.md`'s own "Background / current state"
  already characterized this cost as "confirmed sufficient."

## How to verify locally
```
# This cycle's new/updated tests (81, including every pre-existing
# Gitea-path test with its call site updated to the new host parameter):
python3 -m unittest tests.test_ai_reviewer -v
# Ran 81 tests ... OK

# No regressions in the closest-precedent existing suites:
python3 -m unittest tests.test_gitea_poll tests.test_github_api tests.test_gitea -v
# Ran 111 tests ... OK

# Broad targeted run across every test file except the two known slow/
# privileged end-to-end suites (tests/test_install_ollama.py,
# tests/test_deploy_target.py's own PrivilegedEndToEndTests -- see this
# file's item 20 entry for why a full `unittest discover` doesn't complete
# in reasonable wall-clock time in this environment):
python3 -m unittest tests.test_ai_reviewer tests.test_gitea_poll tests.test_github_api \
  tests.test_gitea tests.test_clone tests.test_deploy_dispatch tests.test_gitea_sync_project \
  tests.test_install_set_env tests.test_install_update tests.test_new_project_from_gitea \
  tests.test_new_project_from_upload tests.test_new_project_from_url tests.test_smoke_check \
  tests.test_taiga_push tests.test_taiga tests.test_team_routes tests.test_teams_board \
  tests.test_teams_cancel tests.test_teams_composition tests.test_teams_grounding \
  tests.test_teams_headless tests.test_teams_lead tests.test_teams_lifecycle tests.test_upload
# Ran 1106 tests ... FAILED (failures=1) -- the one failure
# (TeamStartEndpointTests.test_two_near_simultaneous_starts_exactly_one_succeeds,
# unrelated to this diff, a timing-sensitive concurrency test in
# tests/test_team_routes.py) passes in isolation:
python3 -m unittest tests.test_team_routes.TeamStartEndpointTests.test_two_near_simultaneous_starts_exactly_one_succeeds -v
# Ran 1 test ... OK

# The non-privileged deploy-target test classes (excluding
# PrivilegedEndToEndTests, unrelated to this diff, sudo/systemd-provisioning):
python3 -m unittest tests.test_deploy_target.WrapperBranchingTests \
  tests.test_deploy_target.RestartValidationTests tests.test_deploy_target.InstallShTemplateTests \
  tests.test_deploy_target.DeployTargetOrphanDetectionTests \
  tests.test_deploy_target.DeployTargetTearDownBackstopTests
# Ran 18 tests ... OK

# Syntax/compile check:
python3 -m py_compile app/app.py

# Verifies "no new route, no HTML/JS template change" (docs/spec.md
# acceptance criteria):
git diff app/app.py | grep -nE '^\+.*(@app\.route|def do_GET|def do_POST|<script|<html|<div)'
# -> no output

# Manual end-to-end smoke test against a real GitHub repo + a real running
# switchboard (requires a real GITHUB_TOKEN with `repo` scope, a real
# owner/repo listed in AI_REVIEWER_GITHUB_REPOS_FILE, AI_REVIEWER_ENABLED=1,
# and an open PR with AI_REVIEWER_LABEL added -- not performed in this
# session, see "Known limitations"):
echo '["<owner>/<repo>"]' > /etc/ai-dev-switchboard/ai-reviewer-github-repos.json
# set GITHUB_TOKEN, AI_REVIEWER_ENABLED=1, AI_REVIEWER_MODEL in switchboard.env,
# restart the service, add AI_REVIEWER_LABEL to an open PR on that repo,
# wait up to GITHUB_POLL_INTERVAL_SECONDS (120s default) after the next
# /status poll, confirm a new AI-generated review comment appears on the PR
# and AI_REVIEWER_STATE_FILE gains a "github:<owner>/<repo>#<number>" entry.
```

---

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

# Implementation: Backlog item 16 -- clone a project by `git clone <url>` directly

## Summary
Added a third "add a project" entry point, `POST /projects/clone`, that
takes an arbitrary remote git URL (`http://`/`https://`/`ssh://`/scp-like
`user@host:path`) plus an optional project-name override and clones it
directly into `PROJECTS_DIR/<name>`, following item 2b's own privilege-
separation shape: everything (URL/name validation, collision checking) runs
unprivileged in `app.py`, and only the final `mkdir`/`chown`/`git clone`
crosses into root via a new, narrowly-scoped, unconditionally-installed
privileged script (`scripts/new-project-from-url.sh`). A third "Clone from
URL" button was added to the web UI next to "+ New project" and "Upload
folder / .zip", following the "+ New project" inline-form pattern per
`docs/design.md`.

## Changes by file
- `app/app.py`
  - New globals `NEW_PROJECT_FROM_URL_SCRIPT` / `CLONE_TIMEOUT_SECONDS`
    (defaults `/usr/local/bin/ai-dev-switchboard-new-project-from-url.sh`
    / `180`), placed alongside `NEW_PROJECT_FROM_GITEA_SCRIPT`.
  - `CLONE_URL_MAX_LEN`, `_CLONE_URL_SCHEME_RE`, `_CLONE_URL_SCP_RE`,
    `_validate_clone_url()` — allowlist-only URL validation (accepts
    `http(s)://`, `ssh://`, or git's scp-like `user@host:path` shorthand;
    rejects everything else, including `file://`, `git://`,
    `ext::`/`fd::` transport helpers, bare/relative local paths, and
    `-oProxyCommand=...`-shaped argument-injection attempts, since every
    accepted pattern requires a fixed non-`-` prefix). Placed near
    `NAME_RE`.
  - `_last_path_segment_from_clone_url()` — naming-only heuristic
    ("what's the repo's own name"), placed directly after `create_project()`.
  - `_derive_project_name()` extended with an optional `fallback_prefix`
    parameter (default `"upload"`, preserving the upload wizard's existing
    behavior byte-for-byte); `clone_project_from_url()` passes
    `fallback_prefix="clone"`.
  - `clone_project_from_url(url, name_override)` — the new orchestration
    function: validates the URL, derives/validates the name, checks
    collision against `instance_names()`, then dispatches
    `["sudo", NEW_PROJECT_FROM_URL_SCRIPT, url, name]` with
    `timeout=CLONE_TIMEOUT_SECONDS`, wrapped in
    `try/except (subprocess.SubprocessError, OSError)` (catches
    `TimeoutExpired` too) per `deploy_run()`'s own precedent. Reads neither
    `GITEA_ENABLED` nor `GITEA_API_TOKEN`. Placed directly after
    `create_project()`, before the folder-upload section.
  - New `POST /projects/clone` branch in `do_POST`, alongside `/projects/new`
    — an ordinary JSON-body POST, so it goes through the existing shared
    TOTP gate unchanged.
  - Frontend: new CSS (`.clone-form`, `.clone-form-label`, `.clone-err`,
    `.clone-status`), a "Clone from URL" button + inline expandable form
    (URL input, optional name input, Clone button, error/status slot) next
    to "Upload folder / .zip"; new JS functions `openCloneForm()`,
    `closeCloneForm()`, `setCloneFormBusy()`, `startClone()`; `actionPath()`/
    `actionBody()` extended with a `kind === 'clone'` case (the body reads
    the URL/name straight from the live inputs, same "survives a TOTP
    retry" discipline `team-start`'s own task field already uses, rather
    than threading a second string through `toggle()`'s own
    name/on/checkboxEl parameters); `handleActionResult()` extended with
    its own `kind === 'clone'` branch (own error/status slot, clears and
    hides the form on success, re-enables it on failure) placed before the
    generic 400 handler, same pattern `team-start`/`deploy` already use;
    `cancelActionCode()` extended to re-enable the clone form if a TOTP
    retry is cancelled mid-flight; the TOTP code-overlay label switch
    extended with a `kind === 'clone'` case.
- `scripts/new-project-from-url.sh` — new file, installed unconditionally
  (no `--with-git-hosting` dependency). Re-validates `<name>`/`<url>` in
  bash (defense in depth), atomically `mkdir`s `PROJECTS_DIR/<name>` (no
  `-p`, closing the same TOCTOU race the sibling scripts close), `chown`s
  it to `RUN_USER`, then clones as `RUN_USER` via
  `su "$RUN_USER" -s /bin/bash -c '...git clone -- "$1" "$2"' _ "$URL" "$DEST"`
  — the URL is passed as `su`'s own trailing positional argument (`$1`
  inside the invoked shell), never interpolated into a shell string, per
  `docs/spec.md` §5 "DEVIATION 2". `GIT_TERMINAL_PROMPT=0`/
  `GIT_ASKPASS=/bin/false`/`GIT_SSH_COMMAND=...BatchMode=yes` make an
  auth-required clone fail fast instead of hanging.
  `GIT_ALLOW_PROTOCOL="http:https:ssh"` is a second, git-side allowlist
  enforcement. On any failure (including the post-clone `du -sb` size-cap
  check against `CLONE_MAX_BYTES`, default 500 MiB), `DEST` is always
  removed — a deliberate deviation from `new-project-from-gitea.sh`'s
  "leave a partial clone for manual cleanup" precedent (§5 "DEVIATION 1"),
  since an arbitrary external clone is the one creation path genuinely
  likely to fail partway through a large transfer.
- `install.sh` — `install -m 755` step + `NEW_PROJECT_FROM_URL_SCRIPT`
  `set_env` call, placed in the base (always-installed) block right after
  the deploy-dispatch config section (see "Deviations from spec" below for
  why it isn't directly adjacent to the upload-wizard block despite the
  spec's own suggested placement); one new unconditional sudoers line
  (`.../ai-dev-switchboard-new-project-from-url.sh *`) alongside the
  upload wizard's own unconditional rule.
- `config/switchboard.env.example` — new "Clone project from URL" section
  (`NEW_PROJECT_FROM_URL_SCRIPT` uncommented/set; `CLONE_TIMEOUT_SECONDS`/
  `CLONE_MAX_BYTES` commented-out optional overrides, matching
  `UPLOAD_MAX_BYTES`/`UPLOAD_MAX_ENTRIES`'s own treatment).
- `docs/ARCHITECTURE.md` — "Processes and privilege boundaries" extended
  with one new paragraph for the clone-from-URL hand-off, plus a mention in
  the opening `app/app.py` bullet.
- `tests/test_clone.py` — new file: `_validate_clone_url()`,
  `_last_path_segment_from_clone_url()`, `_derive_project_name()`'s new
  `fallback_prefix` parameter, `clone_project_from_url()`'s
  collision/name-override/subprocess-failure/timeout branches (subprocess
  mocked), and an end-to-end `POST /projects/clone` route test class
  against a real `ThreadingHTTPServer` (TOTP gate, success, failure,
  name-override passthrough) — 41 tests total.
- `tests/test_new_project_from_url.py` — new file, mirroring
  `tests/test_new_project_from_gitea.py`'s structure exactly: unprivileged
  argument-validation tests (run unconditionally) plus privileged tests
  (sudo-gated, skipped cleanly without passwordless sudo) against a real
  local `git http-backend`-backed HTTP server — covers a real public-repo
  clone, the atomic-`mkdir`-no-`-p` collision race, the
  always-remove-DEST-on-failure deviation, the `CLONE_MAX_BYTES`
  size-cap rollback, and (via a second, always-401 HTTP handler) the
  auth-required-repo-fails-fast-not-hangs acceptance criterion — 15 tests
  total.
- `tests/test_clone_frontend.js` — new file, mirroring
  `tests/test_deploy_frontend.js`'s technique (extracts and runs the real
  rendered `<script>` from `render_page()` in a Node `vm` sandbox with
  stub DOM/`fetch`): open/close toggle, empty-URL validation, the
  disabled/"Cloning…" loading state, success (clears + hides the form),
  400 failure (shows the server error, form stays open/editable), the 428
  TOTP-retry path (correct code-overlay label, retry succeeds), and
  cancelling the code overlay re-enabling the form — 8 tests total.

## Key decisions / tradeoffs
- **Followed the spec's URL-allowlist-as-injection-defense reasoning
  exactly, no separate leading-`-` check** — every accepted pattern in
  `_CLONE_URL_SCHEME_RE`/`_CLONE_URL_SCP_RE` (and their bash-regex
  equivalents in the script) requires a fixed non-`-` prefix, so a
  `-oProxyCommand=...`-shaped "URL" can never match either regex and never
  reaches `git clone` as an argv token.
- **`clone_project_from_url()` wraps its `subprocess.run(...)` call in
  `try/except (subprocess.SubprocessError, OSError)`**, per the spec's own
  citation of `deploy_run()` as this codebase's precedent — unlike
  `create_project()`'s/`confirm_upload()`'s own privileged-script calls
  (a pre-existing, out-of-scope gap the spec explicitly says not to
  repeat).
- **Frontend reads `url`/`name` straight from the live DOM inputs inside
  `actionBody()`**, rather than trying to thread a second string through
  `toggle()`'s existing `(kind, name, on, checkboxEl)` signature — mirrors
  `team-start`'s own established "read from the still-live element, not a
  stale closure snapshot" discipline for surviving a TOTP retry.
- **Frontend disables the URL/name inputs and the Clone button, and swaps
  the button label to "Cloning…", for the duration of the request** (per
  `docs/design.md`'s "Loading State"), and re-enables them again in three
  places: `handleActionResult()`'s own `kind === 'clone'` branch (success
  or failure), and `cancelActionCode()` (if a TOTP retry is cancelled
  mid-flight) — otherwise a cancelled retry would leave the form stuck
  disabled with no way to retry.

## Deviations from spec
- **Fixed a real bug in the spec's own privileged-script body**: the
  spec's `scripts/new-project-from-url.sh` code block uses
  `trap cleanup ERR` to guarantee `DEST` is removed on any failure. Testing
  this for real (via `tests/test_new_project_from_url.py`'s privileged
  tests) showed the `ERR` trap silently never fires for this script's own
  failure shape, because every failure branch exits via an explicit
  `exit 1` (inside `... || { ...; exit 1; }`, or after the size-cap `if`),
  and bash's `ERR` trap does **not** fire for an explicit `exit` builtin —
  only for a command whose own nonzero status would itself trigger
  `set -e`. Verified directly with a minimal bash reproduction before
  fixing. Changed to `trap cleanup EXIT` / `trap - EXIT` (EXIT fires on
  every shell exit regardless of cause, and is cleared right before the
  final success echo, same shape the original `trap - ERR` line used).
  Confirmed via `test_clone_failure_removes_dest_deviation_from_gitea_sibling`
  and `test_oversized_clone_rolled_back_and_removed`, both of which failed
  against the spec's literal `ERR`-trap version and pass against the fix.
  This is a correctness fix, not a scope change — the observable behavior
  (DEST always removed on failure) matches the spec's own "DEVIATION 1"
  intent and the acceptance criteria ("script removes DEST and exits 1",
  "no orphaned directory") exactly; only the trap signal name changed.
- **`install.sh`'s new install/`set_env`/sudoers lines for the clone
  script are placed right after the deploy-dispatch config block (after
  `chown "$SVC_USER:$SVC_USER" "$ENV_FILE"` / `chmod 600 "$ENV_FILE"`),
  not immediately after the upload-wizard block** as the spec's own
  illustrative line-number references suggested. Reason: `tests/
  test_deploy_dispatch.py`'s `InstallShDeployMapBlockTests` extracts the
  deploy-map config block from `install.sh` via an exact-substring marker
  match anchored on `'set_env "$ENV_FILE" UPLOAD_STAGING_TTL_SECONDS
  "1800"\n\n# Switchboard-side deploy dispatch'` immediately followed by
  `'chown "$SVC_USER:$SVC_USER" "$ENV_FILE"'` — inserting the new clone
  block directly between those two lines (as first attempted) broke that
  marker match and failed 5 existing tests. Moving the new block to just
  after that chown/chmod pair keeps both original markers adjacent again
  while still installing/configuring the script unconditionally in the
  same base block, with no behavioral difference (ordering among
  independent `set_env`/`install` calls in this section doesn't matter).
  The sudoers-line placement (alongside the upload wizard's own
  unconditional rule) is unchanged from the spec.
- The `CLONE_TIMEOUT_SECONDS`/`CLONE_MAX_BYTES` `switchboard.env.example`
  entries are commented-out optional overrides (per the spec's own example
  block), so `install.sh` does not `set_env` them explicitly — only
  `NEW_PROJECT_FROM_URL_SCRIPT` is force-set, matching
  `UPLOAD_MAX_BYTES`/`UPLOAD_MAX_ENTRIES`'s existing precedent of staying
  as plain example-file defaults rather than install-time-patched values.

## Known limitations
- Everything the spec's own "Non-goals" section already scopes out
  (HTTPS+token private-repo auth, `git ls-remote` pre-checks, Gitea
  repo-map/poll-sync integration, progress streaming, SSH key management,
  lifecycle management) is out of scope here too, unchanged.
- SSH-based private cloning (a URL to a host `RUN_USER` already has
  working SSH access to) is exercised only indirectly by this cycle's
  tests — there's no live SSH server in the test environment to clone
  against, so `tests/test_new_project_from_url.py` covers the http(s) path
  end-to-end (real `git http-backend`) plus the auth-required-fails-fast
  case, and covers the `ssh://`/scp-like *validation* regex directly, but
  not a live SSH clone. This matches the precedent `test_new_project_from_
  gitea.py` already set (it also only exercises the HTTP path end-to-end).
- The killed-on-timeout orphaned `su`/`git` process tree gap the spec's
  own "Risk / rollback notes" calls out as accepted and pre-existing
  (shared with `deploy_run()`) is unchanged — not attempted here.
- `du -sb`'s post-clone size check (and every other bash arithmetic
  comparison in the script) assumes a GNU-coreutils-flavored `du`
  supporting `-sb`; unverified against non-GNU `du` (same unverified-
  minimum-version caveat the spec's own "Open questions" already flags for
  `git clone --`).

## How to verify locally
```
# Full existing suite, all green (988 tests):
python3 -m unittest discover -s tests -v

# Just this cycle's new/changed tests:
python3 -m unittest tests.test_clone -v                    # 41 tests
python3 -m unittest tests.test_new_project_from_url -v     # 15 tests (needs
                                                             # passwordless
                                                             # sudo + git for
                                                             # the privileged
                                                             # half; the rest
                                                             # run regardless)
node tests/test_clone_frontend.js                          # 8 tests

# install.sh's own marker-based regression suite, confirming the
# deploy-dispatch block extraction still matches after the reshuffle:
python3 -m unittest tests.test_deploy_dispatch -v

# Manual end-to-end check (requires a running instance + passwordless sudo
# configured per install.sh):
# 1. ./install.sh   (installs scripts/new-project-from-url.sh unconditionally)
# 2. Open the web UI, click "Clone from URL", paste a public repo URL
#    (e.g. https://github.com/octocat/Hello-World.git), click Clone.
# 3. Confirm the project appears in the list within ~4s of completion
#    (no service restart needed), and that
#    PROJECTS_DIR/Hello-World/.git exists, owned by RUN_USER.
```

## Post-review fix (must-fix from `docs/test-review.md`'s item 16 review)
The reviewer's own hands-on adversarial-URL exercise found that
`_validate_clone_url()` (`app/app.py:715`) and its bash mirror
(`scripts/new-project-from-url.sh:29`) both let a URL like
`ssh://-oProxyCommand=id` through — the scheme-fixed-prefix reasoning in the
original code comments and `docs/spec.md`'s "Open questions" was true of the
*whole string* but not of the *host component* right after `://` (or right
after `@` for the scp-like shorthand), which is the part `ssh`/`git`
actually treats as an option when it starts with `-`
(CVE-2017-1000117-shaped argument injection). The only thing that was
actually blocking this in the review's own sandbox was installed git's own
upstream hostname-shape hardening (present since git 2.14.1), not this
codebase's allowlist, so any older git remained exploitable.

Fixed both regexes to also reject a `-` immediately after the scheme (or
after `@`):
- `app/app.py`: `_CLONE_URL_SCHEME_RE` → `r"^(https?|ssh)://(?!-)\S+$"`;
  `_CLONE_URL_SCP_RE` → `r"^[A-Za-z0-9_.-]+@(?!-)[A-Za-z0-9_.-]+:\S.*$"`.
- `scripts/new-project-from-url.sh`: the same two-branch bash-regex
  re-validation tightened to `^(https?|ssh)://[^-[:space:]][^[:space:]]*$`
  and `^[A-Za-z0-9_.-]+@[^-[:space:]][A-Za-z0-9_.-]*:[^[:space:]].*$`.

Verified every existing accepted shape (`https://`, `http://`, `ssh://`,
scp-like `user@host:path`, case-insensitive scheme) still matches both
tightened regexes, and every adversarial shape the reviewer constructed
(`ssh://-oProxyCommand=id`, `https://-something`, `user@-oProxyCommand=...
:path`, `user@-host:path`) is now rejected at both layers — confirmed with a
standalone regex probe before touching the source, then by running the real
test suites. Added adversarial cases to both test files:
`tests/test_clone.py::ValidateCloneUrlTests` (3 new: ssh-scheme, https-scheme,
scp-like — 44 tests total, up from 41) and
`tests/test_new_project_from_url.py::ArgumentValidationTests` (2 new:
ssh-scheme, scp-like) plus a new real-`sudo` end-to-end adversarial test in
`PrivilegedCloneTests` (`test_ssh_argument_injection_shape_rejected_before_
any_subprocess`) that runs the actual privileged script against
`ssh://-oProxyCommand=touch${IFS}<marker>` and asserts the marker file is
never created and no `PROJECTS_DIR/<name>` directory is left behind — 18
tests total in that file, up from 15. Also independently re-ran the
reviewer's own manual `sudo` repro by hand: the crafted URL is now rejected
by the script's own bash-regex re-validation before `git clone`/`ssh` is
ever invoked (`Unsupported URL: ...`, exit 1), rather than depending on
installed git's own hostname guard to catch it downstream.

Full suite: `python3 -m unittest discover -s tests` → `Ran 994 tests` / `OK`
(988 baseline + 6 new adversarial cases across the two files). Node:
`node tests/test_clone_frontend.js` → 8/8, unaffected (this fix touched no
frontend code).

The should-fix (WCAG contrast, `docs/design.md`) was left untouched per the
reviewer's own explicit instruction that it's non-blocking and out of scope
for this fix-and-reapprove round.

## Second post-review fix (re-review Finding 1, still a must-fix)
The re-review (`docs/test-review.md`'s "Re-review: Backlog item 16 — Finding
1 fix-and-reapprove round") proved the lookahead tightening above was still
bypassable with two independently-constructed adversarial URLs, both
verified end to end with real `sudo` runs of the privileged script:
- `ssh://user@-oProxyCommand=touch${IFS}/tmp/marker` — the `(?!-)` in
  `_CLONE_URL_SCHEME_RE` only checked the character immediately after
  `://`; an innocuous `user@` prefix hides the real (malicious) host, which
  starts right after `@`, not right after `://`.
- `user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/marker` (scp-like
  shorthand) — `_CLONE_URL_SCP_RE`'s `(?!-)` only checked the character
  immediately after `@` (the host); nothing checked the character
  immediately after the `:` (the path git hands to the remote
  `git-upload-pack` invocation).

Both reached a genuine `git clone` subprocess as a real argv token in the
reviewer's own `sudo` runs, stopped only by installed git's own
(version-dependent) downstream hardening — the exact residual-risk shape
the original must-fix asked to close at this codebase's own validation
layer.

**Root cause**: guarding "the character right after a fixed anchor" doesn't
work when the accepted grammar allows an optional segment (`user@` for
`scheme://`, `:path` for the scp-like shorthand) between that anchor and
the component that actually matters to ssh/git. No amount of moving the
lookahead to a different fixed anchor closes the class; the fix has to
parse out the real component and validate it directly.

**Fix — replaced lookahead-anchored regexes with actual component
isolation + validation**:
- `app/app.py`: `_CLONE_URL_SCHEME_RE`/`_CLONE_URL_SCP_RE` are now only a
  coarse "does this look like the right grammar" pre-filter (no more
  `(?!-)`). The real check is a new `_clone_url_host_is_safe(host)`
  helper, applied to a host string isolated two different ways depending
  on the accepted grammar:
  - `scheme://...`: `urllib.parse.urlsplit(url).hostname` — the standard
    library's own RFC 3986 authority parser, which already correctly
    ignores an optional `user@` prefix, unwraps a bracketed IPv6 literal,
    and strips a `:port` suffix, rather than hand-rolled slicing.
  - scp-like `user@host:path`: split on the first `@` (the user segment's
    own charset already excludes `@`/`:`, so the first `@` is
    unambiguous), then the first `:` after that. Both the isolated `host`
    *and* the isolated `path` are validated here (unlike the scheme case)
    — `path` is what becomes a real argv token to the remote
    `git-upload-pack` invocation, so a leading `-` on it is rejected the
    same as a leading `-` on the host.
  - `_clone_url_host_is_safe()` itself: rejects empty/`None`; for a host
    containing `:` (only ever legitimate for an IPv6 literal), delegates to
    `ipaddress.ip_address()` — a strict, already-tested stdlib parser that
    also accepts a `%<scope-id>` suffix — rather than a hand-written IPv6
    regex; otherwise requires the host to start and end with an
    alphanumeric character and contain only `[A-Za-z0-9._-]` in between
    (never a leading `-`, and never a stray smuggled `@`/`:`).
- `scripts/new-project-from-url.sh`: bash has no `urllib.parse`, so the
  equivalent isolation is done with parameter expansion/pattern matching,
  mirroring the same decision the Python side makes (not just its regex
  shape): `${rest%%/*}` isolates the authority, `${authority##*@}` keeps
  only what follows the *last* `@` (same RFC 3986 userinfo/host boundary
  urlsplit() uses), then either unwraps a `[...]` bracketed IPv6 literal or
  strips a trailing `:port` via `${hostport%:*}`. A new `_host_is_safe()`
  bash function mirrors `_clone_url_host_is_safe()` exactly (IPv6 charset
  check via `[0-9A-Fa-f:]` + optional `%scope-id`, else the same
  alnum-start/alnum-end hostname charset). For the scp-like branch, `${rest%%:*}`/`${rest#*:}`
  isolate host/path the same way Python's `partition(":")` does, and both
  are checked (host via `_host_is_safe`, path via `${path:0:1} != "-"`).

**Verification — both exact reviewer repro URLs, real `sudo` runs**:
```
$ sudo env RUN_USER=$(id -un) PROJECTS_DIR=/tmp/npfu-bypass-test2/projects \
    bash scripts/new-project-from-url.sh \
    'ssh://user@-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker1' 'bypasstest1'
Unsupported URL: ssh://user@-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker1
# exit 1, marker not created, no PROJECTS_DIR/bypasstest1 created

$ sudo env RUN_USER=$(id -un) PROJECTS_DIR=/tmp/npfu-bypass-test2/projects \
    bash scripts/new-project-from-url.sh \
    'user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker2' 'bypasstest2'
Unsupported URL: user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker2
# exit 1, marker not created, no PROJECTS_DIR/bypasstest2 created
```
Neither prints `Cloning into...` — both are now rejected before any
subprocess, unlike the prior round.

**Additional self-constructed adversarial variants, also verified with real
`sudo` runs** (none created a marker file or a `PROJECTS_DIR/<name>`
directory): a `user:password@-host` scheme-form userinfo hiding a malicious
host (`ssh://user:password@-oProxyCommand=.../repo`); a double-`@` scheme
form where a decoy username precedes the real malicious host
(`ssh://real@decoy@-oProxyCommand=.../repo` — confirms the "last `@`"
RFC 3986 semantics correctly find the *real* host, not the first-looking
one); the same double-`@` shape in scp form
(`user@decoy@-oProxyCommand=...:path`); an empty host after `@` in both
scheme (`ssh://user@/repo`) and scp (`user@:path`) form; and a
malicious leading-dash *path* in scp form behind an otherwise-benign host
(`user@github.com:-oProxyCommand=...`). A legitimate bracketed IPv6 host
(`ssh://user@[::1]:22/repo`) was also confirmed to still pass validation
(proceeds to a real connection attempt, only failing later on auth/host
key, never on `Unsupported URL`) — the tightened validation doesn't
collaterally reject a real IPv6 URL shape. One deliberate asymmetry,
confirmed intentional and safe: a malicious leading-dash *path segment* in
the *scheme* form (`https://github.com/-oProxyCommand=.../marker`) is
accepted by validation and does reach `git clone` — but only as part of a
single URL string argument that git itself parses, never as a separate
argv token the way the scp-form path is, so there is nothing for it to
inject into; confirmed with a real `sudo` run that this fails only with
`remote: Not Found` / `repository ... not found`, never creates the marker.

**New permanent regression tests** (18 new, all passing; every prior
legitimate-URL case also still passes unchanged):
- `tests/test_clone.py::ValidateCloneUrlTests` — 10 new: both exact
  reviewer repro shapes (`user@`-hidden host, `:`-hidden path), a
  `user:password@-host` variant, two double-`@` variants (scheme and scp),
  an empty-host case in each grammar, an empty-path scp case, a legitimate
  bracketed-IPv6-accepted case, and an invalid-bracketed-host-rejected
  sanity case. 54 tests total in that file, up from 44.
- `tests/test_new_project_from_url.py::ArgumentValidationTests` — 5 new
  unprivileged cases (both exact repro shapes, the double-`@` scheme
  variant, an empty-host scp case, and the legitimate bracketed-IPv6
  case). `PrivilegedCloneTests` — 3 new real-`sudo` end-to-end cases (both
  exact repro shapes, and the double-`@` scheme variant), each asserting
  no marker file and no `PROJECTS_DIR/<name>` directory are ever created.
  26 tests total in that file, up from 18.
- Combined: `python3 -m unittest tests.test_clone
  tests.test_new_project_from_url -v` → `Ran 80 tests` / `OK` (62 baseline
  + 18 new).

**Full suite**: `python3 -m unittest discover -s tests` → `Ran 1012 tests` /
`OK`, 0 failures (994 baseline + 18 new adversarial cases across the two
clone test files), run cleanly and non-concurrently. Node:
`node tests/test_clone_frontend.js` → 8/8, unaffected (this fix touches no
frontend code).

**Doc-accuracy correction** (folded in per the reviewer's non-blocking
note): `docs/spec.md`'s "Open questions" section previously claimed the
allowlist regex alone "fully closes" the argument-injection shape
regardless of `--` support — already false against the first (lookahead)
revision and doubly so given this round's two additional bypasses.
Corrected to describe `--` as independent defense-in-depth rather than a
redundant claim layered on an already-infallible regex, and to note the
allowlist was rewritten to parse/validate the real host and path
components directly.

The WCAG-contrast should-fix remains untouched — out of scope for this
round per the reviewer's own instruction.

# Implementation: Backlog item 19 part 1 -- interject a free-form message into a running team (backend)

## Summary
A human's third lever on a live team run, alongside answering a pending
`ask_user`/`board_write` and stopping the run outright: an unsolicited
free-text message queued for the **lead**, delivered at the top of its own
next round. Backend + CLI only, per docs/spec.md's own explicit scope --
the chat-bubble UI is a separate part 2 cycle.

- `teams.interject(run_id, text)` -- appends one envelope to a new per-run,
  append-only `human.jsonl` file; never touches `run.json`, so there is
  nothing for the driving thread's own end-of-round `_persist(state)` call
  to race or clobber.
- `team_step()` drains `human.jsonl` via a persisted `human_cursor` at the
  very top of its own round (before the lead is ever called) and appends
  one `human_interject` history entry per queued message.
- A new `_INTERJECT_MITIGATION` prompt clause, present in `_system_framing()`
  for all three tiers.
- `POST /projects/<name>/team/interject` and `team-interject <run_id>
  <text>` (CLI) -- thin wrappers; neither starts/resumes a driving thread.
- `human.jsonl` merges into `GET .../team/events` as one more `files` list
  entry, tagged `agent="human"`, `kind="message"` -- the existing
  `{ts, agent, seq, kind, text, meta}` envelope shape, unchanged.

## Changes by file
- `app/teams.py`:
  - `_human_log_path(run_id)` -- new, next to `_transcript_path()`.
  - `TEAM_INTERJECT_MAX_CHARS` (default 2000) and
    `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND` (default 65536) -- new constants,
    next to `TEAM_ASK_USER_ANSWER_MAX_CHARS`/`TEAM_BOARD_WRITE_VALUE_MAX_CHARS`.
  - `_new_state()` -- adds `"human_cursor": 0` (additive; existing runs
    persisted before this field existed read it back as `0` via
    `state.get("human_cursor", 0)`).
  - `_next_human_seq(run_id)` -- new, next to `_next_transcript_seq()`, same
    "count existing lines" idiom scoped to `human.jsonl`.
  - `interject(run_id, text)` -- new, placed immediately before
    `resolve_ask_user()`. Reloads state fresh, rejects a terminal status
    (`finished`/`error`/`escalated_max_rounds`/`stopped`, the same tuple
    `stop_team()`/`sweep_dead_teams()` already treat as terminal) with
    `f"run {run_id} is not accepting messages (status={status})"`, else
    appends `{"ts", "agent": "human", "seq", "kind": "message", "text",
    "meta": {}}` to `human.jsonl` via `os.makedirs` + `open(path, "a")` +
    one `write()` call. Returns `{"ok": True, "run_id": run_id}` /
    `{"ok": False, "error": ...}`. Never calls `_persist()`.
  - `team_step()` -- new drain checkpoint at the very top (before
    `round_n`/`system`/`round_context` are computed, earlier than the
    existing `cancel_event` checkpoints): calls `tail_jsonl_events()`
    against `human.jsonl` from `state.get("human_cursor", 0)`, capped at
    `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND`; for each new event, appends a
    `tool="human_interject"` history entry (`transcript_entries=[]` --
    already durably recorded in `human.jsonl` itself, no second copy in
    `transcript.jsonl`), advances and persists `human_cursor`, and returns
    without ever calling `_call_lead()`. Docstring extended in place; no
    other branch of the function changed.
  - `_INTERJECT_MITIGATION` -- new constant, next to
    `_BOARD_WRITE_MITIGATION`, required-verbatim text describing what a
    `human_interject` round means and does not mean. Appended to
    `_system_framing()`'s `parts` list, every tier.
  - `_cli_team_interject(args)` -- new, next to `_cli_team_board_resolve()`.
    Calls `interject()`; prints `queued for run <run_id>` and exits 0 on
    success, `error: <reason>` to stderr and exit 1 on failure. Does NOT
    call `_drive_and_report()` -- queuing a message is not the same action
    as resuming a stopped run.
  - `team-interject <run_id> <text>` subparser (two positionals, matching
    docs/spec.md's own literal CLI shape) + dispatch arm in `main()`.
- `app/app.py`:
  - `_handle_team_events()` -- `files` list extended by exactly one tuple,
    `("human", teams._human_log_path(run_id))`. No other change; the
    existing per-file cursor/byte-cap/truncation-flag/merge-sort logic is
    already generic over the file list.
  - New `POST /projects/<name>/team/interject` branch in `do_POST`,
    alongside `/team/resolve`/`/team/board-resolve`, same shared TOTP gate.
    `run_id` validated against `teams._RUN_ID_RE` before any load/path-join
    (item 11(b)); loads state and checks
    `state.get("project_name") == name`; defaults to
    `teams.latest_run_for_project(name)` when `run_id` is omitted. Length/
    emptiness validation (`text = (body.get("text") or "").strip()`, 400 if
    empty or over `TEAM_INTERJECT_MAX_CHARS`) happens at this route layer,
    mirroring `/team/resolve`'s own `TEAM_ASK_USER_ANSWER_MAX_CHARS` check
    exactly -- `teams.interject()` is never reached for either rejection.
    Calls `teams.interject(run_id, text)`; `{"error": ...}, 400` on
    failure, else `{"ok": True, "run_id": run_id}`. Deliberately does NOT
    spin up a background thread (unlike `/team/resolve`/`/team/board-
    resolve`, which resume an already-exited loop) -- a running team
    already has a live driving thread that will pick the message up on its
    own next round; a blocked run's message waits for whichever future
    resolve action restarts driving.
- `config/switchboard.env.example`: documents `TEAM_INTERJECT_MAX_CHARS`
  and `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND`, new "interject a message into a
  running team" section right after the existing `TEAM_ASK_USER_ANSWER_MAX_CHARS`
  entry.
- New tests:
  - `tests/test_teams_lead.py`: `InterjectTests` (4), `TeamStepDrainInterjectTests`
    (3), `CliTeamInterjectTests` (3) -- 10 new pure-unit tests, placed right
    after `CallLeadDispatchTests` (same section as the rest of the
    `_call_lead()`/`team_step()` pure-unit coverage). `SystemFramingTests
    .test_mitigation_clauses_present_every_tier` extended with one more
    assertion for `_INTERJECT_MITIGATION`'s own distinctive phrase (see
    "Deviations from spec" below for why this test, not a new dedicated
    class, was extended).
  - `tests/test_team_routes.py`: `TeamInterjectEndpointTests` (12) --
    real-HTTP-server tests mirroring `TeamResolveEndpointTests`'s own
    structure/naming (unknown project, no run at all, cross-project
    ownership, path-traversal `run_id`, malformed non-traversal `run_id`,
    empty/oversized text with a call-count double on `teams.interject`,
    the success path with no thread started, `run_id` omitted defaults to
    `latest_run_for_project`, `blocked_ask_user` accepted, terminal status
    rejected, and the message appearing in a real `GET .../team/events`
    poll) -- placed right after `TeamResolveEndpointTests`.

## Key decisions / tradeoffs
- **`interject()` never calls `_persist(state)` and never mutates
  `state["history"]`** -- this is the entire fix for the race docs/spec.md
  "Background" describes. A naive "load state, append to history, persist"
  implementation would very likely be clobbered by the driving thread's own
  next round-end `_persist(state)` call (this codebase's already-accepted
  "no lock on `run.json`, last writer wins" tradeoff). Writing to a file
  the driving thread does not otherwise touch during a round leaves
  nothing for that race to clobber; `team_step()`'s own drain checkpoint is
  what actually delivers the message into `state["history"]`, on the
  driving thread itself, at the next round boundary.
- **The drain checkpoint sits before `cancel_event`'s own checkpoints, not
  after** -- matching docs/spec.md's own "cheapest possible checkpoint"
  rationale (team_run()'s max-rounds check already follows the same
  principle): draining can short-circuit an entire round (no lead call at
  all) before any other work happens.
- **`team_step()`'s loop variable inside the drain branch is named
  `drain_round_n`, not `round_n`** -- a small, deliberate naming choice
  (not in docs/spec.md's own pseudocode, which reused `round_n`) to avoid
  any risk of the drain loop's local binding being confused with the
  function's own `round_n` computed later in the non-drain path; the two
  never coexist in the same execution (the function returns early from the
  drain branch), but the distinct name makes that non-overlap obvious on
  read without relying on control-flow tracing.
- **Reused the existing `("finished", "escalated_max_rounds", "error",
  "stopped")` terminal-status tuple** (already used verbatim by
  `stop_team()`/`sweep_dead_teams()`) rather than introducing a new literal
  or a shared module-level constant -- matches this codebase's existing
  precedent of repeating the tuple at each call site rather than factoring
  it out; not introduced as a new abstraction for this one additional use.

## Deviations from spec
- **`_BOARD_WRITE_MITIGATION` acceptance criterion, extended interpretation:**
  docs/spec.md's acceptance criteria say to "extend the existing per-tier
  framing test the same way `_BOARD_WRITE_MITIGATION` was covered."
  Checked first (per skill 2, "not testable is a claim to verify"): no
  dedicated test class or assertion actually exists anywhere in the test
  suite asserting `_BOARD_WRITE_MITIGATION`'s own text is present in
  `_system_framing()`'s output -- only `_FACT_CHECK_MITIGATION`'s two
  distinctive phrases are checked, in `SystemFramingTests
  .test_mitigation_clauses_present_every_tier`, and
  `_DELEGATION_HISTORY_MITIGATION` has its own dedicated
  `DelegationHistoryMitigationTests.test_mitigation_clause_present_every_tier`.
  Read this as the spec author intending "cover it the same general way
  every other required-verbatim clause is covered" rather than pointing at
  a literal precedent that turned out not to exist for board_write
  specifically -- extended the existing `test_mitigation_clauses_present_
  every_tier` (the more general, still per-tier-looped test) with one more
  `assertIn` for `_INTERJECT_MITIGATION`'s own distinctive phrase, rather
  than inventing a new dedicated class this repo's own precedent doesn't
  actually establish for the sibling clause the spec named.
- Everything else implemented per docs/spec.md's own literal
  function/route/CLI shapes, error strings, and constant defaults --
  `interject()`'s error text, the route's validation order and error
  strings, the CLI's exact `queued for run <run_id>` output, and the
  `_INTERJECT_MITIGATION` clause text are all copied verbatim from
  docs/spec.md "Proposed approach".

## Known limitations
Every "Non-goal"/"Edge case" docs/spec.md itself already documents as an
accepted, narrow tradeoff is carried forward unchanged by this
implementation (not re-litigated here): round-boundary-only delivery (no
true mid-tool-call interruption), lead-only addressing (no direct-to-
teammate messaging), no edit/withdraw of an already-queued message, a
message posted in the exact instant a run exhausts `max_rounds` is
stranded (never drained, not data-destructive), and two genuinely
simultaneous posts could in principle compute the same cosmetic `seq`
once (display/sort tiebreaker only, `ts` plus insertion order in the
merged feed is still correct). No new limitation was introduced beyond
what docs/spec.md already scoped.

## How to verify locally
```
# This cycle's new backend tests:
python3 -m unittest tests.test_teams_lead.InterjectTests \
  tests.test_teams_lead.TeamStepDrainInterjectTests \
  tests.test_teams_lead.CliTeamInterjectTests \
  tests.test_team_routes.TeamInterjectEndpointTests -v
# Ran 22 tests ... OK

# Full test_teams_lead.py / test_team_routes.py / test_teams_board.py,
# including this cycle's new tests and the extended mitigation-clause
# assertion:
python3 -m unittest tests.test_teams_lead tests.test_team_routes tests.test_teams_board
# Ran 296 tests ... OK

# Full existing suite:
python3 -m unittest discover -s tests
# Ran 1034 tests (1012 baseline + 22 new) ... OK, except one pre-existing,
# unrelated flaky concurrency test
# (TeamStartEndpointTests.test_two_near_simultaneous_starts_exactly_one_succeeds,
# a real-tmux two-thread timing race under full-suite load) -- reproduced
# only under the full 1034-test run, passes in isolation and in every
# targeted run above; not touched by this change (no file this cycle
# edited is imported by that test path beyond app.py/teams.py themselves,
# and it fails/passes identically with this cycle's changes stashed out).

# Manual smoke test against a real project (no lead/teammate subprocess
# needed for any of these):
#   1. Start the app.py server, log in, start a team run against a project.
#   2. `curl` (with a valid session cookie + TOTP code)
#      `-d '{"text": "focus on X first", "code": "<code>"}'
#      /projects/<name>/team/interject` -> {"ok": true, "run_id": "..."}.
#   3. `python3 app/teams.py team-status <run_id>` -- once the driving
#      thread completes its current round, a new "human_interject" entry
#      appears in state["history"] with the posted text.
#   4. `curl /projects/<name>/team/events?run_id=<run_id>` -- the same
#      message appears as one event, agent="human", kind="message".
#   5. `python3 app/teams.py team-interject <run_id> "another message"` --
#      prints "queued for run <run_id>", exits 0, does not block.
```

# Implementation: Backlog item 19 part 2 -- chat-UI-facing surface for interjecting into a running team (frontend)

## Summary
The human-facing presentation layer on top of item 19 part 1's already-shipped
backend (`teams.interject()`, `POST /projects/<name>/team/interject`,
`human.jsonl` merged into `GET .../team/events`): a per-project compose box
(textarea + Send) on the Teams row, visible exactly when
`teams.interject()` would accept a message server-side (`running`,
`blocked_ask_user`, `blocked_board_write` -- i.e. frontend
`team.status === 'running' || (team.status === 'blocked' &&
team.waiting_on_you)`), coexisting with the existing escalation panel via a
context-aware placeholder, a new `.kind-human-message` left-border-accent
row class + `human` filter pill in the existing merged event feed, and a
client-side 2000-char counter/guard mirroring `TEAM_INTERJECT_MAX_CHARS`.
Frontend-only, entirely within `app/app.py`'s inline `<style>`/`<script>`
blocks -- no `app/teams.py`, route, or data-shape change, per docs/spec.md's
own explicit non-goal.

## Changes by file
- `app/app.py`:
  - New CSS (near the existing `.team-escalation-*`/`.team-feed-*` rules):
    `.team-interject` (wrapper, reuses `.team-escalation`'s own
    padding/border/background shape), `.team-interject-row`,
    `.team-interject-textarea` (byte-for-byte `.team-textarea`'s shape,
    `flex: 1` added so it shares the row with the Send button),
    `.team-interject-counter` (+ `.over-limit` modifier, reusing the
    existing `#ff6b6b` error token), `.team-feed-event.kind-human-message`
    (`border-left: 3px solid #4da6ff; padding-left: 12px`).
  - New JS state: `teamInterjectText` (name -> draft string, same
    survives-a-refresh()/428-retry idiom as `teamTaskText`),
    `TEAM_INTERJECT_MAX_CHARS_CLIENT = 2000` (hardcoded, mirroring
    `doTeamResolve()`'s own existing hardcoded-2000 precedent -- see
    "Deviations from spec" below, none needed here, this is spec-directed).
  - New JS functions: `teamAcceptsInterject(team)` (single shared visibility
    predicate), `renderTeamInterjectBox(name, team)`,
    `updateTeamInterjectControls(name)` (narrow direct-DOM update on
    `oninput`, matching `updateTeamStartButton()`'s own idiom -- no
    `refresh()` call, so typing never loses focus/cursor position),
    `doTeamInterject(name)` (client-side validation, then
    `toggle('team-interject', name, true, null)`).
  - `teamFeedEventKindClass()`: one new early-return branch,
    `if (e.kind === 'message' && e.agent === 'human') return
    'human-message';`, placed right after the existing `kind === 'error'`
    check. `teamFeedEventBody()` needed no change -- its existing
    `message`/`status` catch-all already rendered a human message's text
    correctly (part 1's own "renders generically even before part 2's
    styling lands" property).
  - `renderTeamFeed()`: filter-pill agent list extended from `['lead']` to
    `['lead', 'human']`.
  - `actionPath()`: one new `kind === 'team-interject'` branch, POSTs to the
    already-shipped `/projects/<name>/team/interject`.
  - `actionBody()`: one new `kind === 'team-interject'` branch, reads the
    live textarea first, falls back to the `teamInterjectText[]` mirror
    (same "survives a 428 retry" discipline `team-start`'s own task-text
    field uses).
  - `handleActionResult()`: one new 428-label switch entry (`'Sending
    message: ' + (name || 'this')`) and one new `kind === 'team-interject'`
    branch, placed before the generic-400 fallback, mirroring
    `team-resolve`'s own branch shape (success clears the textarea and
    draft mirror and re-disables Send; failure preserves the draft).
  - `clearTeamFeedState(name)`: one new line, `delete
    teamInterjectText[name];`.
  - `teamRow(name, team)`: one new `interjectBox` variable
    (`renderTeamInterjectBox(name, team)`), inserted into the non-idle
    render order between `escalationPanel` and `feedToggle`.
- `tests/test_team_frontend.js`:
  - Two new `createCase()` helpers: `setTeamInterjectText(name, text)`
    (same vm-lexical-scope reasoning as the existing `setTeamTaskText()`)
    and three new element accessors (`interjectEl`, `interjectSendBtnEl`,
    `interjectCounterEl`), mirroring `taskEl`/`startBtnEl`.
  - A new "Chat-UI compose surface" test section (14 new tests), placed
    right before the existing "Past team branches panel" section (i.e.
    after the rest of the live-event-feed coverage): visibility across all
    of `running` / `blocked+waiting_on_you` (coexisting with the escalation
    panel) / the four ineligible statuses; empty/whitespace/over-limit
    disabled-Send + `over-limit` counter class; the POST dispatch shape and
    428-retry label/resend; client-side-rejected empty send; success
    (clears textarea + draft, re-disables Send) and failure (preserves
    draft) result rendering; `teamFeedEventKindClass()` returning
    `'human-message'` and the rendered row carrying
    `kind-human-message`; the filter-pill order (`All, lead, human,
    <members...>`) and that clicking `human` filters via the existing
    generic agent filter; the `human` pill's unconditional presence; and
    that an unsent draft is discarded on a status transition away from
    compose-eligible (and does not resurrect for a later run on the same
    project, or on the project going idle).

## Key decisions / tradeoffs
- **No disabling of the textarea/Send button while the POST is in flight.**
  docs/design.md's own wireframe for the "Sending in Progress" state shows
  both disabled, but docs/spec.md's "Non-goals" explicitly rules this out
  ("No double-submit / in-flight Send-disable protection beyond what other
  actions in this app already have... not introducing a new pattern for
  this one control alone") and no other action button in this app
  (`team-start`, `team-stop`, `team-resolve`, `team-board-resolve`) disables
  itself mid-flight either. Implemented per the spec (authoritative over
  the design doc's own wireframe detail) -- see "Deviations from spec /
  design" below.
- **`renderTeamInterjectBox()` proactively deletes a stale draft the moment
  `teamAcceptsInterject()` returns false**, rather than leaving the delete
  only to `clearTeamFeedState()`'s idle-transition case -- this is what
  makes the "running -> finished" acceptance criterion self-contained: the
  draft is gone the very next time the row renders in a non-idle,
  non-eligible status (e.g. `finished`/`error`), not only when it falls all
  the way back to `idle`. `clearTeamFeedState()`'s own added line remains
  necessary for the idle-transition case specifically, since
  `renderTeamInterjectBox()` is never called at all once `teamRow()` takes
  its idle branch.
- **`teamFeedEventBody()` left untouched**, exactly as docs/spec.md's
  "Proposed approach" §2 specified -- the existing generic `kind ===
  'message' || kind === 'status'` catch-all already renders a human
  message's text correctly; only the CSS *classification* needed a new
  branch.

## Deviations from spec / design
- **In-flight disabling (design doc says disabled, spec's "Non-goals" says
  not to add new disable protection)**: implemented per the spec, which is
  authoritative for scope decisions per this pipeline's own convention
  (design translates spec into UI shape; where the two conflict on a scope
  question the spec already explicitly settled, the spec wins). Flagged
  here rather than silently resolved, since a reviewer reading only
  docs/design.md's wireframe could otherwise expect disabled controls
  during the POST that this implementation does not provide.
- Everything else implemented per docs/spec.md's own literal
  pseudocode/DOM-ID shapes ("Proposed approach" §§1-4) and docs/design.md's
  copy/styling choices (placeholder text, `#4da6ff` left-border accent,
  `.team-interject-*` class names) -- no other functional deviation.

## Known limitations
Every "Non-goal"/"Edge case" docs/spec.md already documents as an accepted,
narrow tradeoff is carried forward unchanged (not re-litigated here):
`TEAM_INTERJECT_MAX_CHARS_CLIENT` hardcoded to 2000 (drifts silently if the
server env var is ever overridden -- server-side validation remains
authoritative regardless), no UI surface for
`TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND`, no Enter-to-send, no double-submit
protection, no messaging a specific teammate directly, no
edit/withdraw of an already-sent message. No new limitation was introduced
beyond what docs/spec.md already scoped.

## How to verify locally
```
# This cycle's new frontend tests, plus the full existing frontend suite
# (both run from the real, rendered <script> extracted from
# app.render_page(), same technique as every other test in this file):
node tests/test_team_frontend.js
# ALL PASS (94/94) -- 80 pre-existing + 14 new

# Backend route tests (untouched by this cycle, confirmed unaffected --
# frontend-only change, no app/teams.py or route edit):
python3 -m unittest tests.test_team_routes -v
# Ran 111 tests ... OK

# Full existing suite:
python3 -m unittest discover -s tests
# Ran 1034 tests in 145.7s ... OK (unchanged from part 1's own baseline --
# this cycle is frontend-only and adds no new Python tests)

python3 -m py_compile app/app.py   # syntax check on the modified file -- OK
```

`git diff --stat` for this cycle: `app/app.py` (+159/-0-ish, additive) and
`tests/test_team_frontend.js` (+236 new tests/helpers) are the only files
this developer cycle touched; `docs/spec.md`/`docs/design.md` were already
written by product-manager/ux-designer before this cycle started, per the
task brief.

# Implementation: Backlog item 20 -- `.team-btn`/`.deploy-btn` WCAG AA contrast fix

## Summary
`.deploy-btn, .team-btn`'s shared CSS rule paired white text (`color:
#fff`) on the `#34c759` green background it also shares with several
already-passing rules elsewhere in the same file (`.pill.active`,
`.wizard-check-row.pill-choice:has(input:checked)`,
`.wizard-actions .primary`, `.new-project-row button`) -- all of which use
dark text (`color: #111`) instead. White-on-`#34c759` computes to ~2.2:1,
well under WCAG AA's 4.5:1 minimum for normal text; dark-on-`#34c759`
computes to 8.51:1 (verified below), comfortably passing AAA. Single
one-line fix, one shared rule, fixes every call site at once (team
start/stop/resolve/board-resolve/interject buttons, the Deploy button).
No JS, route, or test-logic change -- purely visual.

## Changes by file
- `app/app.py`: `.deploy-btn, .team-btn` rule -- `color: #fff` -> `color:
  #111` (the one line changed; `background: #34c759` and every other
  property in the rule untouched).
- `docs/design.md`: corrected two known-wrong contrast claims for this
  exact white-on-`#34c759` pairing, both dated 2026-08-14:
  - Backlog item 16 section ("Clone a project from a remote repository
    URL"), Accessibility & platform notes -- claimed "Button text (#fff)
    on button background (#34c759): 5.05:1"; this button (`.new-project-row
    button`, which the "Clone" button's styling was specified to reuse)
    already used `color: #111` in the actual implementation, not `#fff` --
    the claim was wrong on both the color and the resulting ratio. Corrected
    to `#111` / 8.51:1, with a note that white-on-`#34c759` (never actually
    shipped for this button) would have been ~2.2:1 and failed AA.
  - Backlog item 19 part 2 section ("Chat-UI compose surface"),
    Accessibility & platform notes -- claimed "Send button text (#fff) on
    button background (#34c759): 5.05:1" for the Send button, which the
    same section explicitly identifies as `.team-btn` two lines above.
    This was the one selector that genuinely did ship with `color: #fff`
    until this cycle's fix. Corrected to `#111` / 8.51:1, with the same
    ~2.2:1-fails-AA note about the old value.
- No test file changed -- searched `tests/*.js`/`tests/*.py` for any
  `#fff`/color-string assertion tied to `.team-btn`/`.deploy-btn`; found
  none (`tests/test_deploy_frontend.js`'s two `.deploy-btn` assertions only
  check for the class string's presence/absence, not any color), so
  nothing needed updating there.

## Key decisions / tradeoffs
- **Verified the 8.51:1 figure by computing WCAG relative luminance by
  hand** (both colors are equal in every channel for `#111`, so its
  luminance reduces to a single gamma-corrected term; `#34c759`'s three
  channels computed and weighted 0.2126/0.7152/0.0722 per the standard
  formula) rather than trusting the spec's stated figure at face value --
  it matched (8.507, rounds to 8.51:1), so no discrepancy to flag.
- **Corrected both known-wrong design.md claims, not just the one that
  matches the shipped `.team-btn` selector.** The item 16 claim technically
  describes a button (`.new-project-row button`'s styling, reused by
  "Clone") that was already `#111`/8.51:1 in the real implementation --
  it never had the bug -- but the design doc's own math for "white text on
  this green" was independently wrong there too (5.05:1 claimed, ~2.2:1
  real), and the spec explicitly called out fixing "the two (at least)
  known-wrong contrast claims for this pairing," not just the one tied to
  the actual bug. Left uncorrected, it would keep misleading a future
  design pass that copies this figure for a new white-on-`#34c759` button.

## Deviations from spec / design
None. Single-line CSS fix plus the two design.md corrections, exactly as
scoped; no test changes were needed (searched, found none affected).

## Known limitations
None beyond what the spec already scoped -- this is a contrast-only fix;
no other visual property of `.deploy-btn`/`.team-btn` changed.

## How to verify locally
```
# Contrast math (WCAG relative luminance, #111 on #34c759):
# L(#111) = 0.005607, L(#34c759) = 0.42305
# ratio = (0.42305 + 0.05) / (0.005607 + 0.05) = 8.507 : 1  (passes AAA)
# Previous #fff on #34c759: ratio ~= 2.2 : 1  (fails AA's 4.5:1 minimum)

grep -n "deploy-btn, .team-btn" app/app.py
# .deploy-btn, .team-btn { ... background: #34c759; color: #111; ... }

# Full frontend suite (unaffected -- no test asserted the old color):
node tests/test_team_frontend.js
# ALL PASS (94/94)

# Full backend suite (unaffected -- no app/teams.py or route change):
python3 -m unittest discover -s tests
# Ran 1034 tests in 145.543s ... OK (same count as the item 19 part 2
# baseline -- no regressions, no new Python tests since this is CSS-only)

python3 -m py_compile app/app.py   # syntax check -- OK
```

Manual visual check: open the Teams page, confirm every green button
(Start/Stop/Resolve/Board-resolve/Send/Deploy) now renders dark (#111)
text on the green background instead of white.

---

# Implementation: Backlog item 14 -- `install.sh --update`/`--upgrade`, an update path for an already-installed box

## Summary
Added `--update` (and its exact synonym `--upgrade`) as a new flag to
`install.sh`, parallel to the existing `--with-*` optional-feature flags.
It:
1. Fast-forwards the local `$REPO_DIR` git checkout to `origin/$REPO_BRANCH`
   -- fetch, then `merge --ff-only` (never a destructive `git reset
   --hard`), refusing on uncommitted local changes, a checked-out branch
   other than `$REPO_BRANCH` (including detached HEAD), or a real
   divergence from origin -- before anything else in the script reads from
   `$REPO_DIR`.
2. Lets the script's existing (already idempotent) copy/config steps re-run
   against that now-fresh checkout -- no new copy logic needed, the
   unconditional `cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"` (etc.)
   already does this on every run.
3. Restarts `ai-dev-switchboard.service` to pick up the new code -- but
   only if `RUN_USER` has no live tmux session right now (checked via
   `sudo -u "$RUN_USER" tmux list-sessions`). If one is live, the restart is
   refused outright (no `--force` override), with the live session names
   and next-step instructions printed to stderr; the code update itself
   still happened.

Also folded in the one real correctness bug the spec identified as
load-bearing for this flag's own safety story: `RUN_USER`/`SVC_USER`
prompts previously defaulted to the literal strings `"dev"`/
`"switchboard-svc"` instead of reading the already-configured value from
`switchboard.env` (unlike `PVE_HOST`/`SIMPLE_USERNAME`/`PUBLISH_MODE`,
which already did this correctly) -- fixed the same way those other
prompts already do it.

## Root cause
Not applicable (new capability, not a bugfix) for the `--update` flag
itself. The folded-in `RUN_USER`/`SVC_USER` default fix *is* a real
pre-existing bug: on a non-interactive re-run (`--yes`, which makes
`prompt()` just echo its default with no `/dev/tty` read), `RUN_USER`/
`SVC_USER`/derived `PROJECTS_DIR` would silently reset to `"dev"`/
`"switchboard-svc"` regardless of what was actually configured, and
`switchboard.env` would be overwritten with those wrong values via the
`set_env` calls right after. Latent until now because a first-time
interactive install always sets these correctly and most operators'
non-interactive re-runs happened to already use `RUN_USER=dev`; `--update`
is specifically meant to be re-run non-interactively on an
already-configured, already-running box, which is exactly the case this
bug bites.

## Changes by file
- `install.sh`:
  - Flag parsing: `UPDATE=0` declared alongside the other `WITH_*` flags;
    `--update|--upgrade) UPDATE=1 ;;` added to the `case` statement -- both
    spellings set the same internal flag, no behavior difference.
  - `RUN_USER`/`SVC_USER` default fix (`-- Users --` section): now read
    `RUN_USER_DEFAULT="$(get_env "$ENV_FILE" RUN_USER)"; RUN_USER_DEFAULT="${RUN_USER_DEFAULT:-dev}"`
    (and the `SVC_USER` equivalent) before prompting, same idiom
    `PVE_HOST`/`SIMPLE_USERNAME`/`PUBLISH_MODE` already use. Required
    hoisting `ENV_FILE="$CONFIG_DIR/switchboard.env"` up from the `--
    Config --` step (where it's still referenced, now via a comment
    pointing at the hoisted assignment) to right after `CONFIG_DIR` is set
    (`get_env` against a not-yet-existing file already returns empty
    cleanly via its own `2>/dev/null`, so a genuine first install still
    falls back to `"dev"`/`"switchboard-svc"` exactly as before).
  - New update-pull section, placed right after the root check and the
    `ENV_FILE` hoist, before any helper function or the rest of the script
    reads from `$REPO_DIR`: the dirty/branch/divergence-checked
    fetch-then-`merge --ff-only`, exactly as specced (mirrors
    `scripts/gitea-sync-project.sh`'s own shape).
  - New guarded-restart section, placed right after the existing
    `systemctl daemon-reload; systemctl enable --now ai-dev-switchboard`
    (after `RUN_USER` is resolved and `app.py`/`teams.py` are already
    re-copied to `$INSTALL_DIR`): checks `sudo -u "$RUN_USER" tmux
    list-sessions`, restarts only if empty, otherwise warns to stderr and
    leaves the service running the old code in memory (new code already on
    disk).
  - Top-of-file flag documentation: added a `--update, --upgrade` bullet
    matching the existing entries' style/verbosity, and extended the
    "Safe to re-run" comment to mention `--update`'s extra pull step and
    possible deferred restart.
- `README.md`: new "Updating" section (between "Configuration" and "Repo
  layout") documenting `sudo ./install.sh --update`/`--upgrade` and the
  deferred-restart-around-live-sessions behavior, pointing at
  `docs/ARCHITECTURE.md` for why.
- `docs/ARCHITECTURE.md`: new section ("A restart can very likely take down
  every RUN_USER tmux session, not just the switchboard's own process")
  documenting the `KillMode=control-group` inference from the spec's
  "Background" (the generated systemd unit sets no `KillMode`, so
  systemd's default applies -- a restart signals every process in the
  unit's cgroup, and nothing moves spawned `tmux` sessions to a different
  cgroup first) plus the independent in-process driving-thread risk
  (`app/teams.py`'s `_tail_loop`/lead loop), and notes `--update`'s guard
  is built around this finding.
- `tests/test_install_update.py` (new): see "How to verify locally" below.

## Key decisions / tradeoffs
- **Extracted the real `install.sh` blocks verbatim for testing, never
  reimplemented them** -- same `_extract_between`-on-literal-markers
  technique `tests/test_deploy_target.py`/`tests/test_install_ollama.py`
  already use, so a future edit to `install.sh`'s real source that drifts
  from what these tests assume would fail the test, not silently pass
  against a stale reimplementation. Four separate harnesses, one per
  independently-testable piece (flag/doc greps, the `-- Users --` section,
  the update-pull block, the guarded-restart block) -- deliberately not one
  harness driving a full top-to-bottom `install.sh` run, since a full run
  needs real `apt-get`/`useradd`/Docker/`systemctl` machinery this repo's
  own test precedent (`test_deploy_target.py`'s `InstallScriptDeployTargetBlockTests`)
  already reserves for a `HAVE_PASSWORDLESS_SUDO`-gated privileged class,
  which this cycle didn't need since none of the four pieces here require
  root to exercise meaningfully.
- **Update-pull block tested against real throwaway git repos** (a real
  local clone with a real `origin` remote, both on-disk under a per-test
  temp dir), not fakes -- git's own dirty/branch/divergence/ff-only
  semantics are exactly the thing under test, and none of it needs root.
  Confirmed the divergence test actually proves what it claims (fetch
  happened, merge didn't) by asserting `refs/remotes/origin/main` matches
  origin's real HEAD post-run, not just checking which files landed.
- **Guarded-restart block tested with fake `sudo`/`tmux`/`systemctl` on a
  fake `PATH`** (same fake-PATH-stub-binaries technique
  `test_deploy_target.py`'s `WrapperBranchingTests` already uses) --
  proving exactly which command gets invoked (or doesn't) without a real
  tmux server or real root-owned systemd unit, which this repo's own
  `HAVE_PASSWORDLESS_SUDO` gating precedent reserves for genuinely
  privileged end-to-end coverage this cycle doesn't need.
- **Deliberately did not build a full end-to-end test that runs the entire
  `install.sh --update` script top to bottom** (acceptance criterion 1/2's
  literal wording -- "when `sudo ./install.sh --update` runs..."). Doing so
  for real would mean provisioning a real `ai-dev-switchboard.service`,
  real `RUN_USER`, and a real `$INSTALL_DIR` on the test machine -- the kind
  of privileged, slow, higher-blast-radius setup this repo's own
  `PrivilegedEndToEndTests`/`InstallScriptDeployTargetBlockTests` classes
  reserve specifically for things that "genuinely can't be faked" (per
  `docs/spec.md`'s own "Affected areas" guidance for this item). Each piece
  of the acceptance criteria that a full run would exercise (fetch/merge
  correctness, the `RUN_USER`/`SVC_USER` default fix, the live-session
  restart guard, `--update`/`--with-*` running before any `--with-*` block
  reads `$REPO_DIR` -- true by construction, since the update-pull section
  is now the very first thing in the script that reads from it) is instead
  covered at the block level above. Flagging this explicitly rather than
  claiming full literal coverage of criteria 1/2/10's wording.
- **Verified the tests actually catch a regression**, not just that they
  pass against the real code: temporarily reverted the `RUN_USER_DEFAULT`
  fallback to the old hardcoded `"dev"` and re-ran
  `RunUserSvcUserDefaultTests` -- `test_non_interactive_rerun_preserves_already_configured_run_user`
  failed exactly as expected, confirming the test isn't a tautology.
  Restored the real fix afterward (see "How to verify locally" for the
  exact command).

## Deviations from spec / design
None from `docs/spec.md`'s "Proposed approach" -- the flag parsing,
`RUN_USER`/`SVC_USER` default fix, update-pull section, and guarded-restart
section all match the spec's proposed code essentially verbatim (comments
expanded slightly for context, matching this file's own prose density
elsewhere). No design doc for this cycle, per spec's own "ux-designer: Skip
this cycle" note (no web-UI-visible surface).

## Known limitations
- **No full end-to-end test of `install.sh --update` running top to
  bottom** -- see "Key decisions" above for why, and what's covered instead
  at the block level.
- **The `KillMode=control-group`-takes-down-the-whole-tmux-server
  inference this guard is built around has since been empirically
  confirmed** by the reviewer's testing pass (real throwaway systemd
  unit + tmux session, `systemctl restart` took down the entire tmux
  server) -- see `docs/ARCHITECTURE.md`'s "A restart can very likely take
  down every RUN_USER tmux session" section, updated accordingly. The
  guard was already correct either way per the spec's own reasoning (the
  in-process driving thread is unconditionally ended by any restart
  regardless of the cgroup question).
- **`--update` combined with a `--with-*` flag in the same invocation is
  correct by construction** (the update-pull section runs before any
  `--with-*` block; `CONFIG_DIR`/`INSTALL_DIR`/`STATE_DIR` are already
  `mkdir -p`'d by that point, which the update-pull block doesn't touch
  either way) but not exercised by a combined test run for the reasons
  above.

## How to verify locally
```
bash -n install.sh                              # syntax check -- OK

python3 -m unittest tests.test_install_update -v
# Ran 20 tests in ~2.6s ... OK

# Confirms the RUN_USER-default test isn't a tautology (regression-catch
# check performed during this cycle, not part of the committed test run):
#   sed -i 's/RUN_USER_DEFAULT="\${RUN_USER_DEFAULT:-dev}"/RUN_USER_DEFAULT="dev"/' install.sh
#   python3 -m unittest tests.test_install_update.RunUserSvcUserDefaultTests -v
#   -> test_non_interactive_rerun_preserves_already_configured_run_user FAILS as expected
#   git checkout -- install.sh   # restore

# No regressions in adjacent install.sh-touching suites (unaffected by
# this cycle's changes, run to confirm):
python3 -m unittest tests.test_install_ollama tests.test_install_set_env -v
# Ran 24 tests ... OK

python3 -m unittest tests.test_deploy_target.WrapperBranchingTests \
    tests.test_deploy_target.RestartValidationTests \
    tests.test_deploy_target.InstallShTemplateTests -v
# Ran 16 tests ... OK
```

Manual verification on a real box (not performed in this session -- needs
a real systemd/tmux environment): `sudo ./install.sh --update --yes` on an
already-installed box with `RUN_USER` set to something other than `"dev"`,
confirm `switchboard.env`'s `RUN_USER` is unchanged; start a tmux session
as `RUN_USER`, re-run `--update`, confirm the restart is deferred and the
session survives; stop it, re-run again, confirm the service restarts.

# Implementation: Backlog item 17 part 1 -- external-origin detection + GitHub REST client

## Summary
Two new, purely-backend capabilities in `app/app.py`, neither wired into
any poll loop, route, or item 8's existing Gitea-only reviewer yet (that's
item 17 part 2):
- **External-origin detection** -- `detect_project_origin(name)` runs an
  unprivileged `git remote get-url origin` against
  `PROJECTS_DIR/<name>` and classifies the result as `"local"` (this
  switchboard's own Gitea -- detected by loopback-IP semantics, not a
  hardcoded host string), `"github"` (host is `github.com`, with
  `owner`/`repo` parsed from the path), `"external"` (any other real host),
  or `"none"` (no `origin` configured / not a git repo at all). Never
  raises, no new sudoers entry -- `SVC_USER` already has ambient read
  access under `PROJECTS_DIR` (same basis `teams.load_grounding()` already
  relies on).
- **GitHub REST API client** -- `_github_api`/`_github_api_raw`, mirroring
  `_gitea_api`/`_gitea_api_raw`'s exact `(status, body)` contract (raise
  `ConnectionError` only on a real transport failure, never on a non-2xx
  HTTP status), authenticated via a new `GITHUB_TOKEN` config var
  (`switchboard.env`-style, no bootstrap script since GitHub isn't
  self-hosted here), plus a concrete global in-memory rate-limit cooldown
  gate driven by `X-RateLimit-Remaining`/`X-RateLimit-Reset`/`Retry-After`
  response headers. Four read+write convenience functions on top:
  `github_list_open_prs`, `github_pr_diff`, `github_list_branches`,
  `github_post_pr_comment` -- the last one posts directly and
  synchronously, the same way `_gitea_api`'s own `POST .../comments` call
  already does inside item 8's `_ai_reviewer_review_run()`, per this
  cycle's settled scope decision (no propose-then-approve gate; recorded
  in `docs/BACKLOG.md` item 17 and `docs/spec.md`).

## Root cause
Not applicable (new feature/groundwork, not a bugfix).

## Changes by file
- `app/app.py`:
  - New config block, placed directly after the existing
    `GITEA_API_TOKEN`/`NEW_PROJECT_FROM_GITEA_SCRIPT` block: `GITHUB_TOKEN`
    (default `""`), `GITHUB_API_BASE` (fixed
    `"https://api.github.com"`), `GITHUB_API_TIMEOUT_SECONDS` (15, matches
    `_gitea_api`'s own hardcoded timeout), `GITHUB_RATE_LIMIT_FALLBACK_
    SECONDS` (60).
  - New "external-origin detection + GitHub REST client" section, placed
    right after `_gitea_api_raw()` and before the "poll-based sync-on-push"
    section:
    - `_project_origin_url(name)` -- the one `subprocess.run(["git", "-C",
      ..., "remote", "get-url", "origin"], timeout=10)` call. Returns
      `None` (never raises) for a non-git-repo, no-origin, or any
      subprocess/timeout failure.
    - `_classify_origin_url(url)` -- pure, never-raising classifier. Tries
      `urllib.parse.urlsplit(url).hostname` first (covers `https://`/
      `ssh://` scheme forms and bracketed IPv6 loopback); if that yields no
      host (git's scp-shorthand has no scheme for `urlsplit` to parse),
      falls back to a plain `user@host:path` split. Loopback classification
      uses `ipaddress.ip_address(host).is_loopback`, not a string compare
      against `"127.0.0.1"` -- also matches `::1`. `github.com` matching is
      case-insensitive (`.lower()`); `owner`/`repo` are parsed from the
      path with a trailing `.git` stripped. Wrapped in a top-level
      `try/except Exception` as defense in depth (falls through to
      `"external"`, `owner`/`repo`: `None`) -- classification must never
      crash a caller over a malformed `origin`.
    - `detect_project_origin(name)` -- the one public entry point,
      composing the two functions above (`_classify_origin_url
      (_project_origin_url(name) or "")`).
    - `_github_rate_limit_lock`/`_github_rate_limited_until`/
      `_github_rate_limited()` -- the global (not per-repo -- GitHub's rate
      limit is per-token, shared across every repo that token touches)
      in-memory cooldown gate.
    - `_github_note_rate_limit(headers, status)` -- called after every real
      GitHub HTTP response (success or `HTTPError`). Only acts on
      `status in (403, 429)`: a present `Retry-After` header wins outright
      (`now + int(Retry-After)`, or `now + GITHUB_RATE_LIMIT_FALLBACK_
      SECONDS` if it's non-numeric); otherwise, if `X-RateLimit-Remaining
      == "0"`, uses `X-RateLimit-Reset` (falling back to the same default
      if missing/non-numeric); otherwise (a 403/429 with neither signal,
      or a normal 2xx/4xx with remaining quota) is a no-op. Never lowers an
      already-active cooldown.
    - `_github_request_headers(accept=None)` -- the shared header set
      (`Authorization: Bearer <GITHUB_TOKEN>`, `Accept`,
      `X-GitHub-Api-Version: 2022-11-28`, `User-Agent: ai-dev-switchboard`
      -- GitHub's API rejects requests with no `User-Agent` at all, unlike
      Gitea).
    - `_github_api(method, path, body=None)` / `_github_api_raw(method,
      path, accept=None)` -- check `_github_rate_limited()` **before**
      building any request (short-circuit to `(429, {"error": "rate
      limited, retry later"})` / `(429, "rate limited, retry later")` with
      zero HTTP calls made); otherwise build and send the request, call
      `_github_note_rate_limit()` with the real response's headers +
      status, and return `(status, parsed_json_or_{})` /
      `(status, text)`. Same `except HTTPError` (never raises) / `except
      (URLError, TimeoutError[, ValueError])` (raises `ConnectionError`)
      split as `_gitea_api`/`_gitea_api_raw`.
    - `_github_token_missing_error()` -- the shared `{"ok": False,
      "error": "GITHUB_TOKEN isn't configured -- see switchboard.env"}`
      shape.
    - `github_list_open_prs(owner, repo)` / `github_pr_diff(owner, repo,
      number)` / `github_list_branches(owner, repo)` /
      `github_post_pr_comment(owner, repo, number, body)` -- each checks
      `GITHUB_TOKEN` first (returns the missing-token error with zero
      network calls if unset), calls the underlying client function,
      catches `ConnectionError` and turns it into `{"ok": False, "error":
      ...}` rather than propagating, and returns GitHub's own response
      shape unmodified (`{"ok": True, "prs": [...]}` etc.) -- same
      "don't reshape the upstream response" choice `_gitea_api`'s own
      callers already make.
- `config/switchboard.env.example` -- new documented, commented-out
  `GITHUB_TOKEN` block, placed directly before the existing `GITEA_DIR`/
  `GITEA_SSH_PORT` comment (i.e. right after `GITEA_API_TOKEN`'s own
  block), same "documented secret, not auto-provisioned" treatment as
  `SIMPLE_PASSWORD`/`GITEA_API_TOKEN`.
- New `tests/test_github_api.py` (40 tests) -- see "How to verify locally"
  below.

No existing function was modified. No new Flask route, no new HTML/JS
template change, no schema/data-model change, no new privileged script, no
new sudoers entry.

## Key decisions / tradeoffs
- **`_classify_origin_url()` deliberately does not reuse item 16's
  `_CLONE_URL_SCHEME_RE`/`_CLONE_URL_SCP_RE`/`_SAFE_HOST_RE`/
  `_clone_url_host_is_safe()`.** Those exist to defend a *privileged,
  argv-sensitive* `git clone` subprocess against injection; this function
  classifies an **already-existing** `origin` remote for a materially
  lower-stakes, read-only purpose (no privileged subprocess argv is ever
  built from its output). A new, simpler parser -- written per
  `docs/spec.md`'s own explicit direction -- keeps the two concerns
  separate rather than overloading item 16's security-validation regexes
  for an unrelated purpose.
- **The rate-limit gate is global, not per-repo.** GitHub's rate limit is
  per-token, shared across every repo that token touches (unlike Gitea's
  per-project sync/review locks, which exist for concurrency-safety, a
  different reason entirely) -- one cooldown, guarded by one lock,
  correctly reflects that a 403/429 on any call means every other
  `github_*()` call across every project should also back off, not just
  the one that happened to trip it.
- **`_github_api`/`_github_api_raw`'s short-circuit rate-limit response
  reuses `status == 429`**, the same code GitHub itself would return for a
  secondary-rate-limit abuse response -- callers built on top of these two
  functions (the `github_*()` convenience functions today, item 17 part
  2's poll loop later) don't need to special-case "gate tripped locally"
  differently from "GitHub itself just told us to back off," since both
  already mean the same thing to a caller: don't retry yet.
- **No `GITHUB_ENABLED` toggle** -- `detect_project_origin()` needs no
  token and always runs; the `github_*()` calls themselves are gated
  purely on `GITHUB_TOKEN` being set, matching how `GITEA_API_TOKEN` alone
  (no separate boolean) already gates `create_project()`'s Gitea calls
  beyond the `GITEA_ENABLED` toggle Gitea needs for an unrelated reason
  (it's a locally-run service that has to be started).

## Deviations from spec
None. Every function signature, contract, and edge case in this
implementation matches `docs/spec.md`'s "Proposed approach" and
"Acceptance criteria" as written -- no ambiguity requiring a judgment call
was hit while implementing this part.

## Known limitations
- **No live GitHub API call was exercised in this session** -- every test
  in `tests/test_github_api.py` monkeypatches `urllib.request.urlopen`
  (`GithubApiTests`) or `_github_api`/`_github_api_raw`
  (`GithubConvenienceTests`), following `tests/test_gitea.py`'s own
  established "no real network/Docker call in this file" convention. A
  real end-to-end smoke test against `api.github.com` (a real
  `GITHUB_TOKEN`, a real repo, `github_list_open_prs`/`github_pr_diff`/
  `github_list_branches`/`github_post_pr_comment` called directly from a
  `python3 -c` one-liner) was not performed -- no scope in this part calls
  these functions from anywhere the operator could exercise via the UI or
  CLI yet (that's item 17 part 2). The "How to verify locally" section
  below includes the manual one-liners an operator with a real
  `GITHUB_TOKEN` could run to close this gap by hand.
- **`_classify_origin_url()`'s scp-shorthand fallback is a plain
  `user@host:path` split**, not `_CLONE_URL_SCP_RE`-validated -- by design
  (see "Key decisions" above), but it means a syntactically-unusual
  `origin` some other tool wrote (e.g. a bare `host:path` with no `user@`
  prefix) falls through to `"external"` with `owner`/`repo`: `None` rather
  than being further parsed. This matches every acceptance criterion and
  edge case `docs/spec.md` actually lists (all of which use a `user@`
  prefix or a `scheme://` form); a repo whose `origin` genuinely uses that
  unusual bare form is classified correctly as "some non-github,
  non-loopback host" (still `"external"`), just without `owner`/`repo`
  populated -- and part 1 never promised those two fields outside the
  `"github"` case.
- **This part is inert in a running install.** Nothing in `app.py` calls
  `detect_project_origin()` or any `github_*()` function from anywhere
  reachable via the web UI, `/status`, or item 8's existing poll loop --
  by design (`docs/spec.md` "Non-goals"). Setting `GITHUB_TOKEN` in a real
  `switchboard.env` today has no observable effect until part 2 wires
  these functions in.

## How to verify locally
```
# This cycle's new tests (40):
python3 tests/test_github_api.py -v
# Ran 40 tests ... OK

# No regressions in the existing Gitea/AI-reviewer suites this new code
# sits directly alongside (item 17 part 1 touches no existing function in
# either area, but both are the closest-precedent code paths):
python3 -m unittest tests.test_gitea tests.test_gitea_poll tests.test_ai_reviewer tests.test_github_api -v
# Ran 157 tests ... OK

# Full existing suite (including this cycle's new tests):
python3 -m unittest discover -s tests
# Ran 1094 tests in 148.466s ... OK

# Syntax/compile check:
python3 -m py_compile app/app.py

# Verifies "no new route" (docs/spec.md acceptance criteria):
git diff app/app.py | grep -i "Flask\|@app.route\|def do_GET\|def do_POST"
# -> no output (no new route/handler lines in the diff)

# Manual smoke test against a real GitHub repo (requires a real
# GITHUB_TOKEN with `repo` scope, not performed in this session -- see
# "Known limitations"):
cd app && GITHUB_TOKEN=<a real PAT> python3 -c "
import app
print(app.github_list_open_prs('<owner>', '<repo>'))
print(app.github_list_branches('<owner>', '<repo>'))
"
# Confirm {'ok': True, 'prs': [...]} / {'ok': True, 'branches': [...]}
# against a real repo's actual open PRs/branches.

# Manual origin-detection smoke test against a real local project:
cd app && PROJECTS_DIR=/path/to/projects python3 -c "
import app
print(app.detect_project_origin('<a project name under PROJECTS_DIR>'))
"
# Confirm 'local' for a Gitea-created project, 'github' (with owner/repo)
# for one cloned from github.com, 'none' for a project with no origin.
```

---

# Implementation: Backlog item 18 -- HTTP-level smoke check ("Smoke check" button)

## Summary
Added a manual, per-project "Smoke check" button that makes a single
in-process `urllib.request` GET against that project's own already-captured
hosted dev-server URL (`_session_urls`, the same source `/status`'s own
`url` field already reads) and reports status code, elapsed time in
milliseconds, and an optional response-body substring check -- an honest,
dependency-free HTTP-level health check, explicitly not real browser
QA/testing automation (no JS execution, no rendering, no DOM interaction).
This is the buildable increment of backlog item 18, following the
reconciliation `docs/spec.md`'s "Background" section documents (cross-model
review already shipped as item 8; a security-audit skill already exists as
the `claude-security` plugin; real browser QA stays blocked pending a
Chromium/Bun decision the user explicitly declined this round).

## Changes by file
- `app/app.py`:
  - Two new env-configurable constants, `SMOKE_CHECK_TIMEOUT_SECONDS`
    (default `10`) and `SMOKE_CHECK_MAX_BODY_BYTES` (default `65536`),
    added next to `AI_REVIEWER_STATE_FILE` following the existing
    `int(os.environ.get(...))` pattern.
  - `_smoke_check_locks`/`_smoke_check_lock_for()` -- a per-project
    non-blocking `threading.Lock` guard, identical shape to
    `_deploy_locks`/`_deploy_lock_for()`, placed directly after
    `deploy_run()`.
  - `smoke_check_run(name, expect_contains) -> dict` -- the dispatch
    function itself (docstring covers the full return-shape contract).
    Looks up `_session_urls.get(name)` (returns a clean `ok: False` dict
    immediately if absent, before the lock is even touched); acquires the
    per-project lock non-blocking (returns a dict with an internal-only
    `"locked": True` marker on contention -- see "Key decisions" below);
    times a `urllib.request.urlopen(url, timeout=SMOKE_CHECK_TIMEOUT_SECONDS)`
    call with `time.monotonic()`; reads at most
    `SMOKE_CHECK_MAX_BODY_BYTES` of the body; treats `HTTPError` as a
    *completed* check (the target's real status code, not a mechanism
    failure); catches `URLError`/bare `TimeoutError`/`ConnectionRefusedError`
    (both wrapped-in-`URLError.reason` and bare shapes -- see "Key
    decisions") as a clean transport failure; decodes the body
    `errors="ignore"` before the substring check, so `content_ok` is
    `None` (never `False`) when `expect_contains` is empty.
  - New route `POST /projects/<name>/smoke-check`, added right before the
    final `else: 404` fallback in the POST dispatch chain, after
    `/projects/<name>/team/interject`. Validates the project name (404 if
    unknown), reads and trims `expect_contains` from the body (coerced to
    `""` if missing or non-string), calls `smoke_check_run()`, pops the
    internal `"locked"` marker to decide 409 vs. 200.
  - Frontend (inline `<script>`/`<style>` inside `render_page()`):
    `smokeCheckExpect` (per-project client-state map, survives `refresh()`
    re-renders, mirrors `teamTaskText`), `smokeCheckRow(name, url)` (mirrors
    `deployRow()`'s "return '' if not present" shape, gated on `url` not
    `deploy`), wired into `row()` right after `codeRow()`/before
    `deployRow()`; `doSmokeCheck(name)` (mirrors `doDeploy()`'s
    `toggle()`/`performAction()`/`handleActionResult()` plumbing --
    including the shared TOTP code-overlay retry path -- but with **no**
    `confirm()` call); `actionPath()`/`actionBody()` gained a
    `'smoke-check'` branch each; `handleActionResult()` gained a
    `kind === 'smoke-check'` branch (renders into `.smoke-check-msg`,
    reading `data.ok` not `r.ok` -- see "Key decisions") and a
    `kind === 'smoke-check'` case in the 428 code-overlay label ternary.
    New CSS: `.smoke-check-row`, `.smoke-check-row input`, `.smoke-btn`
    (own class, `#4da6ff`/`#111` -- see "Key decisions"), `.smoke-check-msg`
    (+ `.success`/`.error` variants, same shape as `.deploy-msg`).
- `config/switchboard.env.example`: new "HTTP-level smoke check (backlog
  item 18)" section documenting both new env vars, placed right after the
  existing deploy-dispatch section's `DEPLOY_KEYS_DIR` block.
- `tests/test_smoke_check.py` (new): `SmokeCheckLockTests`
  (`_smoke_check_lock_for()`'s per-project identity), `SmokeCheckRunTests`
  (`smoke_check_run()` exercised against *real* local `http.server`
  instances and raw sockets -- no `urllib` mocking anywhere in this class,
  matching `tests/test_deploy_dispatch.py`'s own "provision something real"
  philosophy scaled down to a local server/socket since no privileged
  receiver is needed here), `SmokeCheckEndpointTests` (route-level 404/428/
  409/200 contract against a real `ThreadingHTTPServer`, `smoke_check_run`
  monkeypatched, same technique as `DeployEndpointTests`). 25 tests total.
- `tests/test_smoke_check_frontend.js` (new): button visibility, the no-
  `confirm()` dispatch path, `expect_contains` surviving a `refresh()`
  re-render, all three result-rendering shapes (success/no-check,
  content-found, content-not-found-but-still-200), a connection-refused
  failure, 409 lock contention, and the 428 TOTP-retry path -- same
  extract-the-real-`<script>`-and-run-it-in-`vm` technique as
  `tests/test_deploy_frontend.js`. 10 tests total.

## Key decisions / tradeoffs
- **`smoke_check_run()`'s lock-contention return uses an internal-only
  `"locked": True` dict key, not a `(status, dict)` tuple.** `docs/spec.md`
  types the function as `-> dict` (unlike `deploy_run()`'s own `-> tuple`)
  but also says "the route returns 409" on contention -- the function
  itself has no HTTP-status concept in its return type. Resolved by having
  the route `pop("locked", False)` before ever sending the dict to the
  client, so the marker never leaks over the wire (covered by
  `test_smoke_check_post_lock_contention_surfaces_as_409`'s explicit
  `assertNotIn("locked", payload)`). This is my own reading of a spec detail
  the text left slightly underspecified, not a deviation from anything the
  spec explicitly required.
- **Two exception shapes for the same underlying failure, both handled.**
  Verified empirically (not assumed) that `urlopen()` raises a *bare*
  `TimeoutError` for a connect/header-phase timeout on this Python version,
  while a connection refusal comes wrapped as
  `URLError(reason=ConnectionRefusedError(...))` -- see the two `python3 -c`
  probes referenced in "How to verify locally" below. `smoke_check_run()`
  unwraps `e.reason` only when `e` is a `URLError`, else treats the caught
  exception itself as the reason, so both shapes map to the same two
  error-message branches ("timed out after Ns" / "connection refused").
  `test_timeout_returns_within_roughly_the_configured_bound` uses a raw
  listening socket that accepts but never writes a byte back (not a mock)
  specifically because the spec's own risk note called out testing the
  real timeout path, not trusting the stdlib parameter alone.
- **`.smoke-btn` reuses `#4da6ff`/`#111`** (the same pairing
  `.pill.code-pill.active` already ships), not `.deploy-btn`/`.team-btn`'s
  green -- independently computed via WCAG relative luminance (see below)
  at **7.39:1**, comfortably passing AA's 4.5:1 minimum, rather than
  assumed safe by association with item 20's already-fixed green pairing.
  A brand-new class was chosen deliberately (not a reuse of `.deploy-btn`)
  so this control's color is never silently coupled to a future change to
  the deploy/team green.
- **`handleActionResult()`'s `kind === 'smoke-check'` branch reads
  `data.ok`, not `r.ok`.** The route answers HTTP 200 for both a
  successfully *completed* check (target reachable, any status code) and a
  target-side failure (connection refused/timeout) alike -- only 404
  (unknown project) and 409 (lock contention) are non-200. Rendering off
  `data.ok` (falling back to `data.error`) handles all four shapes
  correctly with one branch, covered by
  `test_smoke_check_post_target_side_failure_still_returns_http_200` (Python)
  and the frontend's own "connection refused" test.
- **Display text is `"<code> · <ms>ms"`, not the spec's illustrative
  `"200 OK · 84ms"`.** The backend dict contract (docs/spec.md "Proposed
  approach") only specifies `status_code`/`elapsed_ms`/`content_ok` keys --
  no HTTP reason-phrase field -- so rendering a literal `"OK"` word would
  require either inventing a status-code-to-reason-phrase table client-side
  or adding an unscoped new field server-side just for cosmetic text. Kept
  the dict contract exactly as specified and treated the spec's exact
  wording as an illustrative example, not a literal format requirement
  (acceptance criteria only requires "displays the status code and an
  elapsed time in milliseconds", which this satisfies). Noted here rather
  than silently reinterpreted.
- **`doSmokeCheck()` routes through the existing `toggle()`/`actionPath()`/
  `actionBody()`/`handleActionResult()` machinery, not a standalone
  `fetch()`.** This was not just style-matching: EVERY mutating POST route
  in this app (including ones with no real on/off concept, like
  `team-interject`/`team-resolve`) passes through a single shared
  once-per-session TOTP gate in `do_POST()` before the route dispatch chain
  runs. A hand-rolled `fetch()` bypassing `toggle()` would get a bare 428
  on a session's first click with no code-overlay retry logic to recover
  from it -- a real functional bug, not a stylistic shortcut. Verified by
  the frontend test's own 428-retry case.

## Deviations from spec
- **`smoke_check_run()`'s internal `"locked"` dict key** (see "Key
  decisions" above) -- a concrete resolution of an underspecified detail
  (the spec names the function's return type as `dict` while also
  describing a 409-mapping "the route" performs), not a change to any
  stated behavior. The client-visible contract (dict shapes, HTTP status
  codes) matches `docs/spec.md`'s "Proposed approach" and every listed
  acceptance criterion exactly.
- **Display text omits the literal word "OK"** from the spec's own
  illustrative `"200 OK · 84ms"` example (see "Key decisions" above) --
  cosmetic only; the acceptance criteria this maps to ("displays the status
  code and an elapsed time in milliseconds") are satisfied either way.
- No other deviations. Redirect-following, non-UTF-8 body decoding, the
  body-size cap's "truncated prefix only" substring-check limitation, and
  every other edge case `docs/spec.md` lists are implemented and tested
  exactly as described.

## Known limitations
- **Pre-existing, unrelated regression found (not introduced, not fixed)
  in `tests/test_deploy_frontend.js`.** Backlog item 13 (surviving team
  branch discoverability, already shipped before this cycle) added an
  unconditional one-time-per-project `/projects/<name>/team/branches` fetch
  as a side effect of rendering ANY `kind='inst'` row
  (`renderTeamBranches()`, called unconditionally from `teamRow()`).
  `tests/test_deploy_frontend.js` was written before that side effect
  existed and never drains it, so 4 of its 9 cases now fail with an extra
  unexpected pending fetch -- confirmed via `git stash` that this failure
  exists identically at this branch's base commit, before any change this
  cycle made. This cycle's own new `tests/test_smoke_check_frontend.js`
  drains that same fetch explicitly in its `setupCase()` helper (see its
  own header comment) so it isn't affected. Fixing
  `test_deploy_frontend.js` itself is out of scope for backlog item 18 (a
  different feature's test file, no code-behavior change needed) --
  flagged here for a follow-up rather than silently left for the reviewer
  to rediscover.
- No configurable HTTP method/headers/auth/body, no scheduling, no
  persisted history -- all explicit non-goals in `docs/spec.md`, confirmed
  not built.

## How to verify locally
```
# New backend tests (25 tests, real local http.server/sockets, no urllib
# mocking):
python3 tests/test_smoke_check.py -v
# Ran 25 tests ... OK

# New frontend tests (10 tests, real rendered <script> extracted and run
# in a vm context, no framework):
node tests/test_smoke_check_frontend.js
# ALL PASS (10/10)

# No regression in the closest-precedent existing suites (deploy dispatch,
# AI reviewer, upload -- run alongside this cycle's own new tests):
python3 -m unittest tests.test_smoke_check tests.test_deploy_dispatch tests.test_ai_reviewer tests.test_upload -v
# Ran 180 tests ... OK

# All existing frontend suites, unaffected:
node tests/test_team_frontend.js               # ALL PASS (94/94)
node tests/test_singleton_toggle_frontend.js    # ALL PASS (15/15)
node tests/test_clone_frontend.js               # ALL PASS (8/8)
node tests/test_upload_frontend.js              # ALL PASS (8/8)

# Syntax/compile check:
python3 -m py_compile app/app.py

# Full `python3 -m unittest discover -s tests` was attempted but not run to
# completion in this session: this repo's full suite includes several
# genuinely slow, unrelated real-timeout/e2e-style tests (e.g.
# tests/test_install_ollama.py's own test_stalling_endpoint_is_bounded_
# does_not_hang, tests/test_deploy_target.py's PrivilegedEndToEndTests --
# real sudo/systemd/sshd provisioning) that push total wall-clock time well
# past 10 minutes in this environment, independent of this cycle's change.
# Every test observed to complete during two attempted full runs passed
# (no failure anywhere outside the pre-existing tests/test_deploy_frontend.js
# regression noted above, which is unrelated to this diff). The targeted
# runs above are this cycle's actual verification evidence.

# The two empirical exception-shape probes referenced in "Key decisions"
# above (bare TimeoutError vs. URLError-wrapped ConnectionRefusedError):
python3 -c "
import urllib.request
try:
    urllib.request.urlopen('http://127.0.0.1:1/', timeout=2)
except Exception as e:
    print(type(e), repr(e))
"
# -> URLError(ConnectionRefusedError(111, 'Connection refused'))

# Manual smoke test against a real running project's dev server:
cd app && python3 -c "
import app
app._session_urls['some-project'] = 'http://127.0.0.1:3000/'
print(app.smoke_check_run('some-project', 'expected text'))
"
# Confirm {'ok': True, 'status_code': 200, 'elapsed_ms': <int>,
# 'content_ok': True|False} against a real locally running dev server.
```

# Implementation: fix pre-existing `tests/test_deploy_frontend.js` regression from item 13

## Summary
`tests/test_deploy_frontend.js`'s `setupCase()` now drains the unconditional
`/projects/<name>/team/branches` fetch that backlog item 13's
`renderTeamBranches()` fires as a side effect of rendering any
`kind='inst'` row, mirroring the exact technique already proven this
session in `tests/test_smoke_check_frontend.js::setupCase()` (added during
item 18). This clears the "Known limitations" item flagged in item 18's
own implementation entry above — 4/9 previously-failing cases now pass,
with no assertion loosened.

## Root cause
Backlog item 13 (PR #8) added an unconditional, one-time-per-project
`fetchTeamBranches()` call inside `teamRow()`'s render path, fired for
every `kind='inst'` row regardless of that project's own `on`/`url`/
`deploy` fields. `tests/test_deploy_frontend.js`'s `setupCase()` helper
predates that change and never drains the resulting
`/projects/<name>/team/branches` fetch, so it sat in `pendingFetches`
polluting every subsequent `assert.strictEqual(c.pendingFetches.length, ...)`
assertion in the 4 dispatch-flow tests that check exact pending-fetch
counts/URLs right after `doDeploy(...)`.

## Changes by file
- `tests/test_deploy_frontend.js` — `setupCase()`: after the real
  `refresh()` call resolves, drain any pending
  `/projects/<name>/team/branches` fetch per instance (resolving it with
  an empty array, matching the same team-branches shape used elsewhere),
  before returning the case to the calling test. Comment mirrors
  `test_smoke_check_frontend.js::setupCase()`'s own explanation verbatim
  in substance.

## Key decisions / tradeoffs
- Copied the sibling file's technique exactly (same drain loop shape,
  same comment content) rather than inventing a different fix, per the
  spec's explicit instruction and the orchestrator's right-sizing rule
  (mechanical repeat of an already-proven technique).
- Kept the added `await tick()` calls immediately before and after the
  drain loop, matching `test_smoke_check_frontend.js`'s own ordering,
  since the fetch is dispatched synchronously during `teamRow()`'s render
  pass triggered by `refresh()`'s promise but the pending-fetch array
  should be quiesced before returning control to each test.

## Deviations from spec
None. The fix is scoped exactly as the spec described: drain the extra
fetch in `setupCase()`, don't touch any of the 9 existing test bodies or
their assertions.

## Known limitations
None new. This closes out the "Known limitations" entry recorded in the
item 18 implementation section above.

## How to verify locally
```
node tests/test_deploy_frontend.js
# ALL PASS (9/9)

node tests/test_team_frontend.js
# ALL PASS (94/94)

node tests/test_smoke_check_frontend.js
# ALL PASS (10/10)

node tests/test_clone_frontend.js
# ALL PASS (8/8)

node tests/test_singleton_toggle_frontend.js
# ALL PASS (15/15)

node tests/test_upload_frontend.js
# ALL PASS (8/8)
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
