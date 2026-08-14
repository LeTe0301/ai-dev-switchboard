# Test & Review: Backlog item 17 part 2 — host-agnostic AI reviewer dispatch + GitHub poll loop

## Scope
Verifies `docs/spec.md`'s acceptance criteria for making item 8's AI
merge-request reviewer host-agnostic (Gitea vs. GitHub) and wiring in
`_github_poll_if_due()` as its GitHub-side dispatch trigger, plus the new
`AI_REVIEWER_GITHUB_REPOS_FILE` opt-in allowlist. All changes are in
`app/app.py`, `config/switchboard.env.example`, `tests/test_ai_reviewer.py`,
and the new `config/ai-reviewer-github-repos.json.example`.

Note on process: during hands-on verification I ran a mutation
("sabotage") check against the allowlist gate that required temporarily
editing `app/app.py`, and then incorrectly used `git checkout --
app/app.py` to undo it — since none of the developer's changes to that
file were staged, this reverted the entire uncommitted diff, not just my
edit. I reconstructed the file exactly from the full diff captured earlier
in this same session and confirmed the restoration is correct: `git diff
--stat HEAD` matches the pre-incident stat line exactly
(`app/app.py | 288 ++++++++++++++-----`), `python3 -m py_compile` succeeds,
and `tests.test_ai_reviewer`/`tests.test_gitea_poll`/`tests.test_github_api`
(149 tests) and the full 1106-test regression run below both pass with the
same, single, pre-existing-and-unrelated flaky failure the developer's own
`docs/implementation.md` already discloses. Flagging this for transparency
since it's a real near-miss, even though the end state is verified correct
and nothing was lost.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Gitea path unaffected by `_github_poll_if_due()`; existing Gitea tests pass with zero assertion changes | Automated — full `tests/test_gitea_poll.py` + `AiReviewerPollRepoTests`/`AiReviewerReviewRunTests` (Gitea-host classes) in `tests/test_ai_reviewer.py` | pass | `python3 -m unittest tests.test_ai_reviewer tests.test_gitea_poll -v` → 149 incl. all Gitea classes, `OK`. Diff of `_gitea_poll_if_due()` shows exactly one changed line (`"gitea"` literal added) |
| 2 | GitHub label-add → exactly one comment posted, state key `"github:owner/repo#N"`, `attempts=0`, `last_error=None` | Automated end-to-end | pass | `GithubPollEndToEndReviewTests.test_label_add_posts_exactly_one_comment_and_records_state` |
| 3 | Label still present on repeated poll → no second comment | Automated | pass | `GithubPollEndToEndReviewTests.test_label_still_present_on_repeated_poll_posts_no_second_comment` |
| 4 | Label removed then re-added → exactly one new episode | Automated | pass | `GithubPollEndToEndReviewTests.test_label_removed_then_readded_is_a_fresh_episode` (asserts `len(self.posted) == 2` across the full sequence) |
| 5 | Repo NOT in allowlist → never reviewed even with label + `AI_REVIEWER_ENABLED=1` + `GITHUB_TOKEN` set | Automated + hands-on mutation check | pass | `GithubPollIfDueTests.test_non_allowlisted_github_origin_project_is_never_polled`, `GithubPollEndToEndReviewTests.test_non_allowlisted_repo_never_reviewed_even_with_label`. Mutation check: replacing the `if owner_repo not in allowed: continue` guard with a no-op caused both tests to fail (see evidence below) — confirms the gate is real, not vacuous |
| 6 | `GITHUB_TOKEN` unset → zero `github_*` calls even with `AI_REVIEWER_ENABLED=1` + non-empty allowlist | Automated | pass | `GithubPollIfDueTests.test_missing_github_token_makes_no_calls`, `AiReviewerPollRepoGithubTests.test_missing_token_makes_no_calls` |
| 7 | `AI_REVIEWER_ENABLED=0` → zero behavior change regardless of token/allowlist | Automated | pass | `GithubPollIfDueTests.test_ai_reviewer_disabled_makes_no_calls` |
| 8 | `GITHUB_POLL_INTERVAL_SECONDS` throttle — no network calls before due, polls again after | Automated | pass | `GithubPollIfDueTests.test_not_yet_due_makes_no_calls`, `test_second_call_after_interval_elapses_polls_again` |
| 9 | GitHub diff-fetch failure → `_ai_reviewer_record_failure()` called, no comment posted | Automated | pass | `AiReviewerReviewRunGithubTests.test_diff_fetch_failure_records_failure_and_posts_no_comment`, `GithubPollEndToEndReviewTests.test_diff_fetch_failure_records_failure_no_comment` |
| 10 | No new route / no HTML/JS template change | Manual grep, hands-on | pass | `git diff app/app.py \| grep -nE '^\+.*(@app\.route\|def do_GET\|def do_POST\|<script\|<html\|<div)'` → no output |
| 11 | `"github:"` prefix structurally can't collide with a Gitea key | Automated + manual reasoning | pass | `AiReviewerPrKeyTests` (3 tests); manually confirmed `owner_repo` for both hosts is always `f"{owner}/{repo}"` sourced from the respective platform's own owner/repo naming, neither of which permits a colon (`_load_gitea_repo_map`/`create_project()`'s Gitea flow, `detect_project_origin()`'s GitHub flow) — same reasoning part 1 already established, re-verified rather than re-assumed |
| 12 | Full existing suite: zero regressions | Automated | pass | see "Regression check" below |
| 13 | Self-caught test-bug class (mock signature vs. real positional-arg contract) not lurking elsewhere | Manual code read across every new/modified test class in `tests/test_ai_reviewer.py` | pass | every `fake_review`/lambda mocking `teams.review_pr_diff` uses parameter names matching the real call's keyword arguments (`workdir=`, `pr_title=`, `pr_body=`, `diff_text=`, `diff_truncated=`); every `github_pr_diff`/`github_post_pr_comment`/`github_list_open_prs` mock matches the real functions' `(owner, repo[, number[, body]])` positional signatures (`app/app.py:1133,1149,1178`) |

### Mutation-check evidence for #5 (allowlist gate)
```
# Temporarily replaced the allowlist membership check with `if False: continue`
First list contains 1 additional elements.
First extra element 0: ('acme', 'widget', 1, '**AI code review**...')
...
FAILED (failures=2)
```
Confirms `test_non_allowlisted_github_origin_project_is_never_polled` and
`test_non_allowlisted_repo_never_reviewed_even_with_label` both genuinely
detect a missing gate, not just passing by construction. File was restored
immediately after (see reconstruction note above; `git diff --stat`
confirmed identical to pre-mutation state).

## Regression check
```
python3 -m unittest tests.test_ai_reviewer tests.test_gitea_poll tests.test_github_api \
  tests.test_gitea tests.test_clone tests.test_deploy_dispatch tests.test_gitea_sync_project \
  tests.test_install_set_env tests.test_install_update tests.test_new_project_from_gitea \
  tests.test_new_project_from_upload tests.test_new_project_from_url tests.test_smoke_check \
  tests.test_taiga_push tests.test_taiga tests.test_team_routes tests.test_teams_board \
  tests.test_teams_cancel tests.test_teams_composition tests.test_teams_grounding \
  tests.test_teams_headless tests.test_teams_lead tests.test_teams_lifecycle tests.test_upload
# Ran 1106 tests in 68.9s — FAILED (failures=1)
```
The one failure, `TeamStartEndpointTests.test_two_near_simultaneous_starts_
exactly_one_succeeds` (`tests/test_team_routes.py`), is a pre-existing
timing-sensitive concurrency test, unrelated to this diff (no `app.py`
symbol it exercises overlaps with this cycle's changes). Confirmed passing
in isolation:
```
python3 -m unittest tests.test_team_routes.TeamStartEndpointTests.test_two_near_simultaneous_starts_exactly_one_succeeds -v
# OK
```
This exactly matches `docs/implementation.md`'s own disclosed "How to
verify locally" result — independently reproduced, not taken on trust.

Also ran, matching the developer's own targeted non-privileged subset:
```
python3 -m unittest tests.test_deploy_target.WrapperBranchingTests \
  tests.test_deploy_target.RestartValidationTests tests.test_deploy_target.InstallShTemplateTests \
  tests.test_deploy_target.DeployTargetOrphanDetectionTests \
  tests.test_deploy_target.DeployTargetTearDownBackstopTests
# Ran 18 tests ... OK
```
`python3 -m py_compile app/app.py` — clean. No lint config exists in this
repo (confirmed, nothing to run beyond `py_compile`).

## Defects found
None — testing pass is clean, proceeding to review.

---

## Spec coverage
Every acceptance criterion in `docs/spec.md` maps to an implemented code
path and at least one automated test (see test-case table above,
1:1 against the spec's checklist):
- Gitea path unaffected — implemented (one-line call-site change) and
  tested (full existing suite unmodified).
- GitHub label-add → one comment, correct state key — implemented and
  tested end-to-end.
- No re-post while label stays present — implemented (shared retry-gating
  logic, unchanged) and tested for both hosts.
- Label-cycle → fresh episode — implemented and tested.
- Allowlist hard-gates GitHub review — implemented in `_github_poll_if_due()`
  only (deliberately not in `_ai_reviewer_poll_repo()`, per
  `docs/implementation.md`'s own documented design decision) and tested,
  including a real mutation check.
- `GITHUB_TOKEN` unset → no-op — implemented (checked in both
  `_github_poll_if_due()` and `_ai_reviewer_poll_repo("github", ...)`) and
  tested.
- `AI_REVIEWER_ENABLED=0` → no-op — implemented and tested.
- Throttle — implemented (mirrors `_gitea_poll_if_due()`'s double-checked
  lock shape) and tested.
- GitHub diff-fetch failure handling — implemented and tested.
- No new route/UI — confirmed via grep, matches spec's Non-goal #2.
- Full suite, zero regressions — confirmed independently.

No gaps found. No acceptance criterion is implemented without a
corresponding test, and no test asserts behavior the implementation
doesn't actually provide.

## Findings (most severe first)
No must-fix or should-fix findings.

### Nit: allowlist entries with no `/` are silently accepted by the loader
- File: `app/app.py:1595` (`_load_ai_reviewer_github_repos`)
- Issue: `{x for x in data if isinstance(x, str) and x}` accepts any
  non-empty string, including one without a `/` (e.g. `"widget"` instead of
  `"acme/widget"`). Such an entry can never match `owner_repo =
  f"{owner}/{repo}"` in `_github_poll_if_due()`, so it's a harmless dead
  entry in practice (never grants scope to anything), not a security or
  correctness bug — the operator just gets confusingly-silent non-matching
  instead of a validation error.
- Not spec'd (spec only asks for "never crash on malformed input", which is
  satisfied) and not worth a follow-up given the file is hand-edited by the
  same operator who'd immediately notice a repo never gets reviewed.

## Follow-ups (non-blocking)
- None beyond the above nit and the already-disclosed, already-deferred
  episode/lock race (`docs/implementation.md` "Known limitations" —
  explicitly out of scope per `docs/spec.md`'s own "Non-goals", inherited
  unchanged, not worsened, by this cycle).
- The developer's own disclosed "no live GitHub poll pass exercised against
  a real `api.github.com`" gap remains open; low-risk given the thoroughness
  of the mocked end-to-end coverage, but worth a manual smoke test (steps
  already documented in `docs/implementation.md` "How to verify locally")
  before this ships to an operator who actually sets `AI_REVIEWER_GITHUB_
  REPOS_FILE` for the first time.

## Overall verdict
Approve.
