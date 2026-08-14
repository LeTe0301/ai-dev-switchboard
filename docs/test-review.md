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

---

# Test & Review: Backlog item 21 part 1 — grow a running team with an added teammate (backend/CLI only)

## Scope
`teams.add_team_member(run_id, agent)`, its `POST /projects/<name>/team/
add-member` route, its `team-add-member` CLI subcommand, the new
`membership.jsonl` side-channel + `team_step()` drain checkpoint (ordered
before the existing `human.jsonl` drain), and the new `TEAM_MAX_MEMBERS`
cap enforced at three call sites (`add_team_member()`,
`validate_composition()`, `default_team_composition()`) — all per
`docs/spec.md`'s "Proposed approach" §1–§6 and its Acceptance criteria.
Backend/CLI only; no UI (explicitly out of scope, part 2).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `add_team_member()` on a running team creates worktree + tmux window in the **live** `team-<project>` session, returns `{"ok": True, "agent", "worktree"}` | Automated, real tmux/git | pass | `tests/test_teams_lifecycle.py::AddTeamMemberRealTmuxTests::test_add_member_creates_worktree_and_window_queues_event` — ran, asserted `sorted(_tmux_windows(session)) == ["aider","codex","lead"]` and worktree dir exists; also asserted `run.json` is NOT touched (only `membership.jsonl` gains the envelope) |
| 2 | `team_step()`'s next round drains the queued event into `state["members"]`/`state["worktrees"]`, advances `membership_cursor`, appends a `tool="team_member_joined"` history entry, and does **not** call the lead that round | Automated | pass | `tests/test_teams_lead.py::TeamStepDrainMembershipTests::test_drain_appends_member_to_state_and_never_calls_the_lead` (asserted via a `_call_lead` stub that fails the test if invoked) |
| 3 | The round after that, `_lead_tools()`/`_validate_lead_action()` accept the new agent as a `delegate` target with zero code change in either function | Automated, real tmux/git | pass | `AddTeamMemberRealTmuxTests::test_drain_at_next_round_boundary_makes_agent_delegate_eligible` |
| 4 | At `TEAM_MAX_MEMBERS`, `add_team_member()` rejects with the count/max named, no worktree/window/queued-event created | Automated | pass | `tests/test_teams_lead.py::AddTeamMemberValidationTests::test_at_cap_rejected_naming_the_max_no_side_effects` — asserts neither the worktree path nor `membership.jsonl` exist afterward |
| 5 | `validate_composition()` rejects an explicit picker composition over `TEAM_MAX_MEMBERS` | Automated | pass | `tests/test_teams_composition.py::ValidateCompositionTests::test_composition_over_the_cap_rejected_naming_count_and_max` (and `_at_the_cap_accepted` boundary case) |
| 6 | `default_team_composition()` deterministically truncates to exactly `TEAM_MAX_MEMBERS` | Automated | pass | `tests/test_teams_composition.py::DefaultTeamCompositionTruncationTests` (3 cases: truncated, deterministic across calls, under-cap unaffected) |
| 7 | No run for project P → `POST .../team/add-member` returns 400, no side effects | Automated, real HTTP | pass | `tests/test_team_routes.py::TeamAddMemberEndpointTests::test_no_run_at_all_400_specific_reason`, `test_unknown_project_404` |
| 8 | Run in `blocked_ask_user`: `add_team_member()` succeeds immediately (real worktree+window), queued event only drained once resumed | Manual, executed live against real tmux/git this session (see note below) | pass | See "Manually-executed verification" below — behavior confirmed correct; **no automated test exists for this specific criterion in the shipped diff** (flagged as a should-fix) |
| 9 | `team-add-member` CLI parity: same effect/output conventions as the route, no web server needed | Automated, real subprocess CLI | pass | `tests/test_teams_lifecycle.py::CliTeamAddMemberSubprocessTests` (2 cases, real `team-launch` then `team-add-member` as separate processes) |
| 10 | Existing team test suites (6c/6d/6f/19/7/8) continue to pass unmodified | Automated | pass | full suite run, see Regression check |
| 11 | Membership drain runs BEFORE human drain in `team_step()`'s own checkpoint order | Automated | pass | `tests/test_teams_lead.py::TeamStepDrainMembershipTests::test_membership_drain_runs_before_human_drain_same_round_poll` — queues both a `member_joined` and a human message, calls `team_step()` twice, asserts round 1 is `team_member_joined` and round 2 is `human_interject` |
| 12 | Membership drain is idempotent against a stale-cursor replay | Automated | pass | `TeamStepDrainMembershipTests::test_idempotent_against_a_stale_cursor_replay` |
| 13 | tmux session gone between status check and `new-window` call → worktree rolled back, clean error, no orphan | Automated, real tmux/git | pass | `AddTeamMemberRealTmuxTests::test_tmux_session_gone_rolls_back_worktree` — killed the session out from under the call, asserted `"no longer running"` error, worktree dir absent, `membership.jsonl` absent |
| 14 | Roster/validity rejections (unknown engine, Ollama entry, agent==lead, already-a-member, unknown run_id, terminal statuses) never create side effects | Automated | pass | `tests/test_teams_lead.py::AddTeamMemberValidationTests` (8 cases) |
| 15 | HTTP route: run_id resolution (explicit/omitted/cross-project/path-traversal/malformed), empty-agent 400, over-the-cap 400 | Automated, real HTTP | pass | `tests/test_team_routes.py::TeamAddMemberEndpointTests` (10 cases total) |

### Hands-on verification of the four flagged safety-critical areas
1. **Drain order (membership before human) and its consequences** — confirmed directly in the shipped code (not just the developer's claim): the membership drain block is positioned before the pre-existing `human.jsonl` drain in `team_step()` (`app/teams.py`), and both blocks `return state` immediately after their own `_persist()`, so exactly one file's queue drains per `team_step()` call. Reasoned through the "could a message be misdelivered" question: neither queue routes anything to a specific teammate (both just become broadcast history rounds the lead reads), and `state` is a single in-memory dict reused across `team_run()`'s own loop (not reloaded from disk each round), so there is no window where `state["members"]` changes underneath either drain step in a way that violates an invariant. Order is cosmetic/deterministic exactly as documented, not load-bearing for correctness. Test #11 above proves the order in the shipped code, not just the developer's account.
2. **Worktree creation failure/rollback path** — traced `_create_worktree()`: on a pre-existing path it returns immediately before ever calling `git worktree add`, so a losing concurrent caller for the *same* agent name gets a clean, side-effect-free false-positive error (no log file touched, no tmux window, no `membership.jsonl` append) — the developer's disclosed limitation is accurate and the failure mode is clean, confirmed by code inspection of `_create_worktree()`'s early-return branch. The tmux-session-gone path (a *different*, exercised failure mode) is proven live by test #13: worktree is actually created then actually rolled back via `_remove_worktree()`, verified by a real `git worktree list`-equivalent existence check after killing the real tmux session mid-call.
3. **`TEAM_MAX_MEMBERS` at all three call sites** — each has its own dedicated, passing test (tests #4, #5, #6 above), each constructed to genuinely exceed a lowered cap and assert the specific enforcement shape (hard rejection for the two explicit-action call sites, deterministic truncation for the auto-pick default) — not just one site tested and the others assumed.
4. **tmux window creation in the live session, not a new session** — `add_team_member()`'s `tmux new-window` call targets `_team_session_name(state["project_name"])`, the *same* session `_create_team_session()`'s own per-member loop targets, with a byte-for-byte identical window command (confirmed via direct diff comparison, `app/teams.py`). Test #1 proves this live: after `add_team_member()`, `tmux list-windows` on the pre-existing `team-<project>` session shows all three windows (`lead`, `codex`, `aider`) in one session — not two sessions.

### Manually-executed verification (acceptance criterion #8, `blocked_ask_user`)
No test in the shipped diff exercises "`add_team_member()` succeeds immediately (real worktree+window) while status is `blocked_ask_user`, and the queued event is only drained once resumed" end-to-end — the closest shipped test (`AddTeamMemberValidationTests::test_blocked_ask_user_and_blocked_board_write_do_not_hit_the_status_check`) only proves the status *gate* accepts these two statuses (by using a deliberately-unknown engine name that fails at a *later* check), never exercises the real worktree/window success path or the delayed-drain half of the criterion under these statuses.

I wrote and ran a throwaway test this session (subclassing the shipped `_AddTeamMemberRealTmuxTestCase` fixture, deleted afterward — not part of the diff) that: launches a real team, forces `status="blocked_ask_user"`, calls `add_team_member()`, asserts the worktree exists immediately, asserts `state["members"]` on reload does **not** yet include the new agent, then simulates a resume (`status="running"` + one `team_step()` call) and asserts the agent **is** now in `state["members"]`. Result: **pass** — the implementation is correct; this is a test-coverage gap, not a functional defect.

## Regression check
Full existing suite: `python3 -m unittest discover -s tests` — **1188 tests, 158.6s, OK** (also independently re-run this session; matches `docs/implementation.md`'s own claimed count/timing). Combined new + pre-existing team-module suites (`test_teams_composition`, `test_teams_lead`, `test_teams_lifecycle`, `test_team_routes`): **356 tests, OK**. New tests alone (the 8 listed commands in `docs/implementation.md` "How to verify locally"): **47 tests, OK**. `python3 -m py_compile app/teams.py app/app.py`: clean (no separate lint/type-check tooling exists in this project).

## Defects found
None — the testing pass is clean.

---
The sections below are only filled in once the testing pass above is fully clean (it is).

## Spec coverage
Every bullet in `docs/spec.md`'s "Acceptance criteria" is implemented and covered by at least one automated test, with one exception:

- The `blocked_ask_user` end-to-end bullet ("succeeds... worktree + window created immediately... drained once resumed") is implemented correctly (manually verified live this session, see above) but has **no automated test** in the shipped diff — the shipped test for this status only proves the status gate, not the full success+delayed-drain path. See Finding 1.

All other criteria (worktree+window creation, drain-at-next-round-boundary, delegate-eligibility with zero code change, at-cap rejection at all three call sites, no-run-found 400, CLI parity, full regression) are each backed by a real, executed, passing test — traced individually in the Test cases table above.

## Findings (most severe first)

### 1. `blocked_ask_user` acceptance criterion has no automated test — should-fix
- File: `tests/test_teams_lead.py` (`AddTeamMemberValidationTests`) / `tests/test_teams_lifecycle.py` (`AddTeamMemberRealTmuxTests`)
- Issue: `docs/spec.md`'s Acceptance criteria explicitly lists "Given a run in status `blocked_ask_user`, when `add_team_member()` is called with a valid new agent, then it succeeds (worktree + window created immediately) and the queued `member_joined` event is only drained once the run is later resumed... not before." The shipped `test_blocked_ask_user_and_blocked_board_write_do_not_hit_the_status_check` only proves the status *gate* passes for this status (by using an intentionally-unknown engine name so it fails at a later, unrelated check) — it never reaches `_create_worktree()`/`tmux new-window`, and no test anywhere asserts the delayed-drain half of the criterion.
- Failure scenario: none observed — I personally ran an equivalent real-tmux test this session (see "Manually-executed verification" above) and it passed, so this is not a live bug. It is a coverage gap: if a future change to `add_team_member()` or `team_step()`'s drain accidentally special-cased or broke behavior specifically for blocked statuses, nothing in this suite would catch it.
- Recommendation: add one real-tmux test (mirrors `AddTeamMemberRealTmuxTests::test_add_member_creates_worktree_and_window_queues_event` almost exactly, with `state["status"] = "blocked_ask_user"` forced before the call, plus an assertion that `state["members"]` on reload does not yet include the agent until a subsequent `team_step()` call runs). Non-blocking for this cycle since the behavior is verified correct, but should land before or alongside part 2.

## Follow-ups (non-blocking)
- Add the missing `blocked_ask_user`/`blocked_board_write` real-tmux success+delayed-drain test (Finding 1).
- The two-concurrent-callers-same-agent-name race is disclosed and accepted by the spec as a narrow, non-corrupting first-mover race (confirmed clean via code inspection of `_create_worktree()`'s early-return-on-existing-path branch); no automated concurrency test exists for it, consistent with the spec's own acceptance of this as an out-of-scope edge case for this part.

## Overall verdict
Approve with follow-ups.
