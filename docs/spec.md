# Spec: fix pre-existing `tests/test_deploy_frontend.js` regression from item 13

Orchestrator-authored (not a full product-manager dispatch) per the
Entwicklung workflow's right-sizing rule 1: this is a mechanical repeat of
a technique already proven in this same session (item 18's
`tests/test_smoke_check_frontend.js::setupCase()` already drains an
equivalent unconditional fetch the same way), already fully diagnosed and
root-caused by item 18's reviewer, no new product/design decision
required.

## Problem

BACKLOG item 13 (surviving team-branch discoverability, PR #8) added an
unconditional `fetchTeamBranches()` call inside `teamRow()`'s render path.
`tests/test_deploy_frontend.js`'s own `setupCase()` helper doesn't know
about this fetch and doesn't drain it, so 4 of its 9 test cases now fail —
confirmed by item 18's reviewer via `git stash` against this branch's base
commit (pre-dates item 18's own diff entirely, item 13's fault).

## Fix

Mirror `tests/test_smoke_check_frontend.js::setupCase()`'s own technique
(already proven working this session) in `tests/test_deploy_frontend.js`:
after triggering whatever bootstraps a team row, drain the extra
`team/branches` fetch the same way before making assertions, so the
existing 9 test cases pass without being weakened.

## Acceptance criteria

1. All 9 cases in `tests/test_deploy_frontend.js` pass.
2. No existing assertion is loosened or removed to make this pass — the
   fix drains the extra fetch, it doesn't stop testing what the file
   already tested.
3. No other test file regresses.

## Open questions

None — mechanical, already root-caused, technique already proven in this
session on a sibling file.
