# Spec: BACKLOG item 13 — surviving team branch discoverability

Orchestrator-authored (not a full product-manager dispatch) per the
Entwicklung workflow's right-sizing rule 1: `docs/BACKLOG.md` item 13
already fully diagnosed this at the multi-agent-teams story's completion
triage — the safety property (nothing is ever silently lost) is already
implemented, the gap is pure discoverability, and the "shape of a future
fix" paragraph in the backlog is detailed enough to build from directly.
No architecture judgment call remains to make.

## Problem

`app/teams.py`'s `stop_team()` removes a stopped/finished run's worktree
directories (`git worktree remove`, no `--force` — refuses on dirty state)
and, on success, drops that agent's entry from `state["worktrees"]`. The
branch itself (`team-{run_id}-{agent}`, see `_create_worktree()` at
`app/teams.py:3428`) is never deleted — nothing an agent commits is ever
lost — but once the worktree entry is dropped, the switchboard itself no
longer tracks that the branch exists. There is no UI panel, CLI field, or
docs pointing an operator at `git branch --list 'team-*'` as the way to
find and review/merge/discard a past run's teammate work.

## Scope

Read-only discoverability only. No new git operations beyond `git branch
--list` (read-only, no state mutation). Explicitly NOT in scope: any
merge/delete UI action (item 13's own triage already settled "left for the
human, permanently" — a list view, not an action panel).

## Changes

### `app/teams.py`

New function, placed near `_remove_worktree()`/`stop_team()`:

```python
def list_team_branches(project_workdir: str) -> list[dict]:
    """
    Read-only: `git branch --list 'team-*'` against project_workdir.
    Returns one dict per matching branch:
      {"branch": str, "run_id": str|None, "agent": str|None,
       "commit": str, "subject": str, "committer_date": str}
    run_id/agent are parsed from the "team-{run_id}-{agent}" naming
    convention (_create_worktree()'s own format) on a best-effort basis --
    None if a branch name doesn't match (e.g. hand-created branches that
    happen to start with "team-"), never raises on a parse miss.
    Returns [] if project_workdir isn't a git repo or the command fails --
    this is a read-only convenience listing, not load-bearing for any
    run's own state, so it degrades silently rather than raising.
    """
```

Implementation: one `subprocess.run(["git", "-C", project_workdir,
"branch", "--list", "team-*", "--format=%(refname:short)\t%(objectname)\t
%(committerdate:iso-strict)\t%(subject)"], ...)` call, tab-split per line.
Reuses this codebase's existing plain-`git`-subprocess convention (not
`_run_run_user_command()` — this reads the switchboard's own project
checkout directly, the same way existing project-list `git log`/`git
status` calls already do; no privilege boundary crossed).

### CLI

New subcommand `team-branches <project_workdir>`, printing
`list_team_branches()`'s result as JSON (`_cli_team_branches()`, follows
`_cli_team_status()`'s exact shape: one function, registered in the
`argparse` subparser block, no run_id argument since this is scoped to a
project, not a run).

### `app/app.py` (web UI)

New route `GET /projects/<name>/team/branches` returning
`list_team_branches()`'s JSON (same auth/project-scoping guard every other
`/team/*` route already applies). Teams page gains a small read-only
"Past team branches" panel (collapsed/expandable, or a simple list below
the existing team status panel) listing branch name, short commit hash,
commit subject, and relative commit date — fetched once on page load
(this data changes only when a team run stops, not continuously, so it
does NOT need to join the existing 4s `/status` poll cycle). No new
interaction model: reuses the page's existing list/row typography, no new
component. `ux-designer` is skipped for this cycle — this is a passive
read-only list appended to an existing panel, not a new interaction
pattern, consistent with the right-sizing rule for changes that don't
introduce new UI surface shape.

### Docs

Short new section in `docs/ARCHITECTURE.md` ("Reviewing a team's work
after it stops"), documenting the plain `git log team-<run_id>-<agent>`,
`git merge team-<run_id>-<agent>`, `git branch -D team-<run_id>-<agent>`
commands an operator uses once they've found a branch via the new UI
panel or `team-branches` CLI output — per the backlog's own suggested doc
shape.

## Acceptance criteria

1. `list_team_branches()` returns every `team-*` branch in a project's
   repo with correct commit metadata, parses `run_id`/`agent` out of the
   naming convention where it matches, and returns `[]` (not an
   exception) for a non-git directory or a repo with zero matching
   branches.
2. `team-branches <project_workdir>` CLI subcommand prints the same data
   as JSON; exits 0 even when the list is empty.
3. `GET /projects/<name>/team/branches` is reachable through the existing
   project-scoping/auth guard and returns the same JSON shape.
4. The Teams page shows a "Past team branches" panel populated from that
   route, with no action buttons (list-only, per scope).
5. `docs/ARCHITECTURE.md` gains the new section with the three commands
   named above.
6. No existing test regresses; new tests cover `list_team_branches()`
   (multiple branches, zero branches, non-git dir, a branch name that
   doesn't match the naming convention) and the new CLI subcommand and
   route.

## Open questions

None — this is discoverability polish over an already-settled safety
decision, no further user input needed.
