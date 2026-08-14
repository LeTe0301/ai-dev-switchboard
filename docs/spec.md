# Spec: test-infrastructure isolation (BACKLOG item 9)

## Why this is a small, orchestrator-authored cycle

Test-infrastructure debt, not a product feature — no user-facing behavior
changes, no new product/design decision. The shape was fully triaged and
decided during this session's backlog review: reuse the exact per-process
scoping technique already proven on `switchboard-headless-*` sessions
(`tests/test_teams_headless.py`'s `_RUN_ID_SCOPE`/`_SESSION_PREFIX`,
commit for item 6d part 2a's own follow-up) rather than inventing a new
approach. Per this pipeline's own efficiency rule, a full product-manager
pass isn't needed for "same technique, different files, no new judgment
call" — this spec is written directly from `docs/BACKLOG.md` item 9's
already-complete diagnosis. No UI surface — ux-designer is skipped.

## Background

Two unrelated but same-class problems, found together while diagnosing a
long-running "unrelated flake" during the multi-agent-teams story:

**A. `tests/test_deploy_target.py` mutates real host state with no orphan
recovery.** It creates/deletes a real system `deploy` user, writes
`/etc/sudoers.d/` entries, provisions `/home/deploy/.ssh/authorized_keys`.
`setUp` skips only if a `deploy` **user** currently exists; it does not
detect an orphaned `/home/deploy` directory left by an interrupted prior
run (observed concretely: `userdel` had already removed the user, but
`/home/deploy` and a stale `authorized_keys` survived, causing later runs
to fail mysteriously). Recovery today is a manual `rm -rf /home/deploy` —
risky to automate carelessly, since the same command on a host where
`/home/deploy` is *real* deploy infrastructure would wipe live SSH access
(this is the "near-miss" the backlog item records: a sandbox classifier
blocked exactly that broad command mid-diagnosis).

**B. `team-<project>` tmux session names and one worktree-op sweep prefix
are unscoped across concurrent test processes.** `tests/test_teams_lifecycle.py`
and `tests/test_team_routes.py` build real tmux sessions named
`team-<literal-project-name>` (`team-demo`, `team-proj`, `team-atomicdemo`,
`team-failchain`, `team-sessionrace`, `team-clidemo`, etc. — real `tmux`
binary, not mocked). `tests/test_teams_lifecycle.py` also sweeps a
`switchboard-worktree-op-` prefix unscoped in `tearDown`. Two concurrent
test-suite runs (or a suite run alongside a real switchboard session)
collide directly. Contrast: `switchboard-headless-*` session names were
already fixed this exact way for 6d part 2a's own follow-up — `_RUN_ID_SCOPE
= f"p{os.getpid()}"`, `_SESSION_PREFIX = f"switchboard-headless-{_RUN_ID_SCOPE}"`
(`tests/test_teams_headless.py:80-81`).

## Non-goals

- No production code changes. Both fixes are entirely inside `tests/`.
- Not attempting the "uniquely-named throwaway deploy user" shape floated
  as an alternative in the backlog item — it would conflict with item 2c
  part 2a's own pinned acceptance criterion that the receiver's system
  username is literally `deploy` (fixed, not templated). The orphan-
  detection fallback the backlog item itself names as sufficient is what
  this cycle builds.
- Not deciding whether the privileged deploy tests should run in CI or be
  marked local-only opt-in — the backlog item leaves that open explicitly;
  out of scope here.
- Not touching `tests/test_deploy_dispatch.py` beyond confirming (in
  testing) that it's unaffected — the backlog item names it as sharing the
  same risk class but the concrete orphan bug and its fix are specific to
  `test_deploy_target.py`'s `setUp`/`tearDown`.

## Proposed approach

### A. `tests/test_deploy_target.py` orphan detection + backstop cleanup

- `setUp`'s guard changes from "does a `deploy` **user** exist?" to "does
  `/home/deploy` exist **at all**?" — skip the privileged test class in
  either case (live account or orphaned directory), since both mean the
  fixture isn't safely available.
- `tearDown` gains an unconditional backstop: after the existing `userdel
  -r deploy` (which silently no-ops if the account is already gone),
  explicitly `sudo rm -rf /home/deploy` so a partial failure never leaves
  the directory behind for the next run to trip over. This is safe to make
  unconditional specifically because it only runs at the end of a test
  that itself created `/home/deploy` in this same `setUp`/test lifecycle —
  never a blind sweep against pre-existing state.

### B. Per-process scoping for `team-<project>` sessions and the worktree-op sweep

- Add the same `_RUN_ID_SCOPE = f"p{os.getpid()}"`-style constant to
  `tests/test_teams_lifecycle.py` and `tests/test_team_routes.py` (or
  import/reuse `test_teams_headless.py`'s if these files already share
  fixtures — developer's call on the cleanest wiring), and scope every
  project name that becomes part of a real `team-<project>` tmux session
  name: `"demo"` → `f"demo-{scope}"`, etc., across every test in both
  files that creates a real session (not synthetic/mocked ones).
  `tests/test_team_routes.py`'s `_project(name="proj")` default should
  scope its own default too.
- Scope `tests/test_teams_lifecycle.py`'s `switchboard-worktree-op-` prefix
  the same way, and scope its `tearDown` sweep to match (only sweep
  sessions/prefixes this process itself could have created).
- Do not change any production naming logic (`teams.py`'s
  `_team_session_name()` itself is correct and untouched) — this is a
  test-fixture-only change, mirroring the `switchboard-headless-*` fix's
  own scope exactly.

## Acceptance criteria

Each must be verifiable by running something, not by reading the diff.

- [ ] A fresh `tests/test_deploy_target.py` run with a genuinely orphaned
      `/home/deploy` (no `deploy` user, directory present, created by a
      simulated interrupted prior run) is now **skipped**, not run against
      stale state. Verify by constructing exactly this orphan condition in
      a test and confirming the class is skipped, not that it merely
      passes coincidentally.
- [ ] `tearDown`'s backstop actually removes `/home/deploy` even when
      simulating a failure partway through the privileged test body (so
      the *next* run's `setUp` sees a clean state) — test this by forcing
      an exception after the fixture is created but before normal cleanup
      would run, then asserting `/home/deploy` is gone afterward.
- [ ] Two full runs of `tests/test_team_routes.py`'s and
      `tests/test_teams_lifecycle.py`'s real-tmux test classes, launched
      **concurrently** (two separate processes, not sequential), both pass
      with zero session-name collisions. This is the actual regression
      test for the reported problem — a sequential re-run passing twice
      does not prove concurrency safety.
- [ ] No test in either file still creates a session literally named
      `team-demo`, `team-proj`, `team-atomicdemo`, `team-failchain`,
      `team-sessionrace`, or `team-clidemo` (grep the diff for these
      literal strings post-fix — should find none used as a real session
      name, only as documentation/comments if at all).
- [ ] `switchboard-worktree-op-` sweep in `tests/test_teams_lifecycle.py`'s
      `tearDown` only ever targets this process's own scoped prefix — a
      concurrent process's own worktree-op sessions must survive
      untouched. Verify directly: start a session under a different
      process's scope, run this file's tests, confirm that other
      session is still alive afterward.
- [ ] Full existing suite (Python: 790 baseline going into this cycle,
      Node: 84) still passes with no regression.

## Risk / rollback

Test-only diff. Rollback is reverting the test files; no production
behavior changes at all. The highest-risk part is the concurrency
acceptance criterion itself, since it requires actually running two
processes at once rather than trusting the scoping logic by inspection —
budget real wall-clock time for it in the developer/reviewer cycles.
