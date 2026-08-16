# Implementation: Team launcher fixes — broken Start button, lead/teammate exclusivity, undiscoverable chat UI, unexplained Smoke check

## Summary
Fixed the confirmed code defect in `onTeamLeadChange()` that let a
previously-checked teammate silently remain in `teamPickerMembers` after
becoming Lead (the root cause of the reported "Start team does nothing"
bug), changed the Teammates picker to render the current Lead's own engine
disabled-in-place instead of hiding it, and added two short static
discoverability/explanation hints (idle-state chat hint, Smoke check helper
text). No backend (`app/teams.py`) changes — end-to-end reproduction found
no second root cause behind issue #2 (see "Root cause" below).

## Root cause
Issue #2 ("Start team does nothing") traces to a single defect in issue #3:
`onTeamLeadChange()` (`app/app.py`) updated `teamPickerLead[name]` on every
Lead change but never removed the newly-selected lead's own engine name
from `teamPickerMembers[name]` if it had previously been checked as a
teammate. `renderTeamPicker()`'s old behavior *filtered* the current lead
out of the rendered teammate checkboxes, so the stale entry became
invisible in the UI while still present in the underlying `Set`.
`teamCompositionError()`/the server's `validate_composition()` then both
correctly rejected the composition with "Lead cannot also be a teammate,"
but that rejection only ever surfaced as small (12px, `#888`) gray text
under the Start button — easy to miss, especially on mobile, which matches
the "button appears to do nothing" report. The exact screenshot composition
(Lead: aider, Teammate: claude) is reproducible by checking either engine
as a teammate at any point before it becomes Lead.

**Issue #2's own required end-to-end reproduction** (task text + a clean
composition, Lead ≠ any checked teammate, no prior conflicting checkbox
history) was run against the real backend path — `POST /team/start` →
`validate_composition()` → `launch_team()` → tmux session creation + state
persistence — via
`tests/test_team_routes.py::TeamStartEndpointTests::test_happy_path_tier2_default_lead_persisted_correctly`
(a pre-existing test exercising exactly this path with a real `tmux`
session and a real git worktree). It passes cleanly (`status 200`, tmux
session present, `run.json` persisted with the expected lead/members).
**Finding: no second, independent backend bug exists.** The #3 stale-Set
scenario is the sole cause of issue #2's reported failure. (This test, and
`tests/test_teams_lead.py`/`tests/test_teams_lifecycle.py`, only run
cleanly in this sandbox once `git commit`'s author identity is supplied via
`GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` env vars — the sandbox has no global
git identity configured. This is a pre-existing environment gap unrelated
to this cycle's code, confirmed identical before and after this change via
`git stash`. The remaining handful of `test_team_routes.py`/
`test_teams_lead.py`/`test_teams_lifecycle.py` failures — two
`blocked_ask_user` vs. `finished` timing-sensitive assertions and two
`sudo`-requiring CLI subprocess tests — were independently confirmed
identical on the unmodified baseline via the same `git stash` comparison,
so none of them are regressions from this cycle.)

## Changes by file
- `app/app.py`
  - `onTeamLeadChange()` (~line 3684): after updating `teamPickerLead[name]`,
    deletes the new lead's `name` from `teamPickerMembers[name]` if present
    — the actual bug fix. This structurally prevents the stale/invisible
    "lead is also teammate" state from ever being constructed.
  - `renderTeamPicker()` (~line 3741): stopped filtering the current lead's
    engine out of the teammate checkbox list (`mateOptions`). Now every
    `delegate_capable` roster entry renders; the entry matching the current
    lead gets the `disabled` attribute, is never rendered checked (guaranteed
    by the `onTeamLeadChange()` fix above), and its `<label>` gets a
    `team-mate-disabled` class for the dimmed-text treatment.
  - `<style>` (~line 2933): added `.team-mates-picker input:disabled` /
    `.team-mates-picker label.team-mate-disabled` rules, reusing the
    `.clone-form input:disabled { opacity: 0.6; cursor: not-allowed; }`
    pattern already established at `app/app.py:3042`.
  - `teamRow()` idle branch (~line 4425): added a one-line static hint
    ("Once started, you'll see live team activity and can interject right
    here.") reusing the already-established `.team-sub` muted-text class
    (already used elsewhere in this same function for `escalatedNote`/
    `finishedSummary`) — deliberately avoids the word "chat" per the spec's
    own caveat against overstating item 19's actual scope.
  - `smokeCheckRow()` (~line 3464) + `<style>` (~line 2886): added a static,
    always-visible `.smoke-check-hint` line above the row explaining the
    button makes a single HTTP request to the project's session URL and
    optionally checks the response text — visible without hover, per the
    spec's touch/mobile caveat (no `title="..."` tooltip used). New
    `.smoke-check-hint` rule reuses `.smoke-check-msg`'s own font-size/color
    tokens.
- `tests/test_team_frontend.js`
  - Updated the line-639 assertion ("...excludes it from the teammate
    checkboxes") to instead assert the lead's own checkbox is present,
    unchecked, and carries the `disabled` attribute.
  - Added `hasCheckedAttr()` test helper — a `\schecked(\s|>)` regex check,
    needed because a checkbox's own `onchange="...this.checked)"` attribute
    string contains the literal substring `"checked"` and would otherwise
    false-positive a naive `.includes('checked')` assertion.
  - Added: stale-membership clearing on lead change (the core #3 regression
    test), disabled-checkbox rendering (extended into the pre-existing
    "saved composition" test), rapid Lead-switching (proves only the
    newly-selected lead's own entry is cleared, an unrelated checked
    teammate is untouched), Lead cleared back to "Choose a lead..." (no
    engine stays disabled), idle-state chat hint present/absent by status.
- `tests/test_smoke_check_frontend.js`
  - Added a test asserting the new `.smoke-check-hint` element is present
    and its text explains the single-HTTP-request behavior.
- `docs/implementation.md` — this file.

No changes to `app/teams.py` — issue #2's reproduction found no second
backend bug (see "Root cause").

## Key decisions / tradeoffs
- Reused `.team-sub` (already used twice elsewhere inside `teamRow()`) for
  the idle-state chat hint instead of introducing the `.team-lead-picker
  label`/`.team-grounding` treatment the spec suggested as an example —
  `.team-sub` is the closer, already-in-scope precedent within this exact
  function and carries the identical 12px/`#888` muted styling, so no new
  CSS rule was needed for this hint at all.
- Left `.team-validation-error`'s "Lead cannot also be a teammate" message
  and the server's `validate_composition()` check untouched (per spec
  Non-goals) — both are now unreachable via normal UI interaction but stay
  as defense-in-depth.
- Did not touch `.team-msg`'s error-color styling (spec's proposed approach
  #2's conditional follow-up) — the end-to-end reproduction found the
  existing `.team-msg.error { color: #ff6b6b; }` rule already applies
  correctly on a real rejection (verified by reading `handleActionResult()`
  logic, which sets `className = 'team-msg error'` on any non-2xx
  response); the gray-on-dark base style was never actually winning for an
  error case, so no speculative styling change was made.

## Deviations from spec
None. All four fixes match the spec's "Proposed approach" as written.

## Known limitations
- The `blocked_ask_user`/`finished` timing-sensitive test failures and the
  `sudo`-requiring CLI subprocess test failures in `test_team_routes.py` /
  `test_teams_lead.py` / `test_teams_lifecycle.py` are pre-existing sandbox
  limitations (no passwordless sudo for the `dev` user; some lead-loop
  convergence timing), confirmed identical before and after this change.
  Not addressed here — out of scope for this cycle.

## How to verify locally
```
# Frontend suites (requires a node binary; this sandbox has none on PATH,
# but code-server ships one that works fine):
NODE=/usr/lib/code-server/lib/node   # or just `node` if present on PATH
"$NODE" tests/test_team_frontend.js
"$NODE" tests/test_smoke_check_frontend.js
"$NODE" tests/test_clone_frontend.js
"$NODE" tests/test_deploy_frontend.js
"$NODE" tests/test_singleton_toggle_frontend.js
"$NODE" tests/test_upload_frontend.js

# Composition/smoke-check backend unit tests:
python3 tests/test_teams_composition.py
python3 tests/test_smoke_check.py

# Full HTTP-route/lead-loop/lifecycle suites (need a configured git
# identity for their temp-repo fixtures -- provide via env, do not touch
# global git config):
GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@test.com \
GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@test.com \
python3 tests/test_team_routes.py
GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@test.com \
GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@test.com \
python3 tests/test_teams_lead.py
```
Manual check: open a project's row, click "Configure team...", pick an
engine as Lead, check that same engine's box as a teammate is impossible
(it renders dimmed/disabled), switch Lead to a different engine that was
previously checked as a teammate, and confirm Start becomes enabled for an
otherwise-valid composition. The idle-state hint line and the Smoke check
helper line should both be visible without hovering.
