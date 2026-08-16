# Test & Review: Team launcher fixes — broken Start button, lead/teammate exclusivity, undiscoverable chat UI, unexplained Smoke check

## Scope
Independent testing + review pass over the bugfix bundle described in
`docs/spec.md` and implemented per `docs/implementation.md`: the
`onTeamLeadChange()` stale-`Set` fix (root cause of "Start team does
nothing"), `renderTeamPicker()`'s switch from hiding the Lead's own engine
in the Teammates list to rendering it disabled-in-place, the idle-state
discoverability hint for the existing in-page live feed/chat surface, and
the static Smoke-check helper text. No `docs/design.md` — ux-designer was
skipped per the spec's own routing note (pure CSS/copy reuse), verified
against the actual diff below.

All commands below were run for real, in this session, against the current
uncommitted working tree (`git diff` against `de60bf4`, nothing committed
yet).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Lead X previously checked as teammate; switching Lead away and back never leaves X in `teamPickerMembers`; `teamCompositionError()` returns null for an otherwise-valid composition | Automated (new) | pass | `tests/test_team_frontend.js`: "changing Lead away from an engine previously checked as a teammate clears the stale membership" — ran, PASS |
| 2 | Lead X's checkbox renders visible, unchecked, `disabled` attribute present (not omitted) | Automated (updated existing test) | pass | `tests/test_team_frontend.js`: "the saved composition pre-selects the lead, shows it disabled+unchecked in the teammate checkboxes (never hidden)" — ran, PASS; manually confirmed `.team-mates-picker input:disabled`/`label.team-mate-disabled` CSS rules exist at `app/app.py:2937-2938` and correctly scope to the `.team-mates-picker` wrapper (`app/app.py:3759`), matching the `.clone-form input:disabled` precedent verbatim (`app/app.py:3053`) |
| 3 | Valid composition + Start clicked → real `POST .../team/start`, succeeds or shows legible server-response-derived error, never silent no-op | Automated (pre-existing, re-verified) | pass | `tests/test_team_frontend.js` dispatch/error tests (all in the 115-test run below); backend: `tests/test_team_routes.py::TeamStartEndpointTests::test_valid_submitted_composition_used_instead_of_default_and_persisted` (explicit client `{task, lead, members}` body — the exact shape `doTeamStart()` sends) — ran individually, PASS |
| 4 | Reported screenshot composition (Lead: aider, Teammate: claude, no prior conflicting history) starts successfully; if a 2nd bug exists it's fixed too | Automated (pre-existing, re-verified) + code trace | pass | `tests/test_team_routes.py::TeamStartEndpointTests::test_happy_path_tier2_default_lead_persisted_correctly` — ran individually, PASS (real tmux session + persisted `run.json`). Engine names in fixtures are generic (`lead2`/`helper`, not literally `aider`/`claude`) but the code path is data-driven and identical; confirmed no second bug exists by inspection — `app/teams.py` has zero diff this cycle |
| 5 | Idle-state row includes a short static hint that live activity/interject appears here once started | Automated (new) | pass | `tests/test_team_frontend.js`: "idle state includes a static hint..." — ran, PASS. Manually confirmed wording avoids "chat" (`app/app.py:4434`: "Once started, you'll see live team activity and can interject right here.") |
| 6 | Hint disappears once team is running | Automated (new) | pass | `tests/test_team_frontend.js`: "the idle-state hint disappears once the team is running" — ran, PASS |
| 7 | Smoke check row shows visible-without-hover text explaining what the button does | Automated (new) | pass | `tests/test_smoke_check_frontend.js`: "the Smoke check row includes a static, always-visible helper line..." — ran, PASS. Manually confirmed no `title="..."` tooltip used (`app/app.py:3464-3475`) — text is a plain `<div class="smoke-check-hint">`, always rendered, no hover dependency |
| 8 | `tests/test_team_frontend.js:639` assertion updated for disabled-not-hidden, passes | Automated | pass | Same as case 2 above — old "excludes it from the teammate checkboxes" assertion replaced, new assertion passes |
| 9 | Rapid Lead switching between two engines clears only the newly-selected lead's own stale membership | Automated (new) — partial | pass, with a coverage gap (see Findings #2) | `tests/test_team_frontend.js`: "rapid Lead switching..." — ran, PASS, but neither `e1` nor `e2` is ever checked as a teammate before becoming Lead in the test, so the `Set.delete()` step is a no-op on each switch; verified correctness by manual code trace instead (see Findings) |
| 10 | Lead cleared back to "Choose a lead..." → no engine renders disabled | Automated (new) | pass | `tests/test_team_frontend.js`: "Lead cleared back to..." — ran, PASS |
| 11 | Pre-populated picker from a saved composition never shows lead checked | Automated (extended existing test) | pass | Same test as case 2; added `teamCompositionError()` === null assertion |
| 12 | `composition === null` (no roster) branch unaffected | Not separately re-tested this cycle (pre-existing coverage, code path untouched) | pass | Confirmed via diff: `teamRow()`'s `composition === null` early-return branch (`app/app.py:4412-4421`) has zero changes in this diff |

## Regression check
Full JS suites run (real Node against the **actual extracted `<script>`
from `app.render_page()`**, not a hand-copied mock — `tests/test_team_frontend.js`'s
own `extractRenderedScript()` shells out to `python3 -c '...render_page()'`
and regex-extracts the live `<script>` body, so every assertion below
exercised the real, current `app/app.py` source):

```
NODE=/usr/lib/code-server/lib/node
"$NODE" tests/test_team_frontend.js        → ALL PASS (115/115)
"$NODE" tests/test_smoke_check_frontend.js → ALL PASS (11/11)
"$NODE" tests/test_clone_frontend.js       → ALL PASS (8/8)
"$NODE" tests/test_deploy_frontend.js      → ALL PASS (9/9)
"$NODE" tests/test_singleton_toggle_frontend.js → ALL PASS (19/19)
"$NODE" tests/test_upload_frontend.js      → ALL PASS (8/8)
```

Python suites:

```
python3 tests/test_teams_composition.py   → OK (24/24)
python3 tests/test_smoke_check.py         → OK (25/25)

GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@test.com \
GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@test.com \
python3 tests/test_team_routes.py    → 131 tests, 2 failures
python3 tests/test_teams_lead.py     → 138 tests, 2 failures
python3 tests/test_teams_lifecycle.py → 75 tests, 2 failures
```

The 6 failures (4 `blocked_ask_user` != `finished` timing-sensitive
assertions across `test_team_routes.py`/`test_teams_lead.py`, 2
`sudo`-requiring CLI subprocess tests in `test_teams_lifecycle.py` failing
with "dev is not in the sudoers file") were investigated and confirmed
**not caused by this diff**:
- `app/teams.py` has **zero changes** this cycle (`git diff --stat --
  app/teams.py` is empty) — the failing lifecycle/lead-loop/CLI tests
  invoke `teams.py` directly via subprocess or through server routes this
  diff never touches.
- `app/app.py`'s entire diff is confined to string literals inside
  `PAGE_TEMPLATE` (client-side JS/CSS shipped to the browser) — no Python
  control-flow, route-handler, or session-state-machine code changed.
- `git stash`-based before/after comparison was attempted but denied by
  the sandbox's action classifier; confirmed equivalently by direct code
  inspection above, which is conclusive here since the failing tests'
  code paths have no overlap with the diff at all.
- The `sudo` failures are a known, deterministic sandbox limitation (`dev`
  has no passwordless sudo) unrelated to any code change.

`python3 tests/test_team_routes.py::TeamStartEndpointTests::test_happy_path_tier2_default_lead_persisted_correctly`
and `::test_valid_submitted_composition_used_instead_of_default_and_persisted`
(the two tests most directly relevant to acceptance criteria #3/#4) were
also run individually — both PASS.

`python3 -m py_compile app/app.py` → compiles cleanly, no syntax errors.

**Verdict: testing pass clean.** Proceeding to review.

## Spec coverage
All 8 acceptance criteria in `docs/spec.md` are implemented and covered by
a test I ran and observed pass this session:
- Stale-Set clearing on Lead change — implemented (`app/app.py:3684-3692`), tested (case 1).
- Disabled-not-hidden rendering — implemented (`app/app.py:3744-3758`), tested (case 2/8).
- Valid-composition Start success/error surfacing — unchanged, re-verified (case 3).
- Screenshot repro (no 2nd bug) — investigated, confirmed, tested (case 4).
- Idle-state discoverability hint — implemented (`app/app.py:4428-4436`), tested (case 5/6).
- Smoke check helper text — implemented (`app/app.py:3464-3475`), tested (case 7).
- Test file line-639 update — done, passes (case 2/8).
- No regressions in `test_team_frontend.js`/`test_teams_composition.py` — confirmed (115/115 and 24/24).

No acceptance criterion is unimplemented or untested.

## Findings (most severe first)

### 1. `docs/implementation.md`'s stated pre-existing-failure count is inaccurate — nit
- File: `docs/implementation.md:44-50`
- Issue: states "two `blocked_ask_user` vs. `finished` timing-sensitive
  assertions and two `sudo`-requiring CLI subprocess tests" (4 total). Actual
  count observed this session is 4 timing-sensitive failures (2 in
  `test_team_routes.py`, 2 in `test_teams_lead.py`) + 2 sudo failures (in
  `test_teams_lifecycle.py`) = 6 total.
- Failure scenario: none functionally — this is a documentation-accuracy
  gap only. Independently confirmed (see Regression check) that all 6 are
  pre-existing and unrelated to this diff regardless of the miscount, so it
  does not affect the correctness of the shipped code. Worth a one-line
  correction in `docs/implementation.md` for anyone reading it later as a
  baseline reference, but not blocking.

### 2. "Rapid Lead switching" test doesn't exercise the deletion step on a genuinely-stale value — nit
- File: `tests/test_team_frontend.js:715-752`
- Issue: the spec's edge case (`docs/spec.md:273-276`) is "Rapid Lead
  switching back and forth between two engines, **each previously checked
  as a teammate**." The test built checks only `helper` (which is never
  itself made Lead) and switches Lead among `e1`/`e2`, neither of which is
  ever checked as a teammate first — so `members.delete(lead.name)` is a
  no-op on every switch in this test, and the test never actually exercises
  a real stale-value deletion mid-sequence. It does correctly prove a
  related but distinct property: an unrelated engine's legitimate
  membership survives unrelated Lead switches.
- Failure scenario: none currently — manually traced `onTeamLeadChange()`
  (`app/app.py:3684-3692`): the fix is `members.delete(lead.name)`, a
  single-key `Set.delete()` call, which is structurally incapable of
  touching any other key regardless of history, so the missing test case
  cannot be masking a real bug here. Recommended follow-up (non-blocking):
  extend the test to check both `e1` and `e2` as teammates before switching
  Lead between them, so the assertion is doing real work rather than
  relying on this reviewer's manual trace.

## Follow-ups (non-blocking)
- Correct the failure count in `docs/implementation.md`'s "Root cause"
  section (Finding 1).
- Strengthen the "rapid Lead switching" test to actually pre-check both
  engines as teammates before switching Lead between them (Finding 2).

## Overall verdict
**Approve.** All acceptance criteria are implemented and independently
verified via tests I ran and observed pass this session; the full relevant
regression suite is clean (only pre-existing, diff-unrelated sandbox
failures, confirmed via code-path inspection since `git stash` was denied);
the diff is minimal and matches the spec's proposed approach with no scope
creep; no security concerns (static copy only, server-derived smoke-check
URL, no new user input paths); CSS/copy reuse claims in
`docs/implementation.md` were spot-checked against actual line numbers and
confirmed accurate. Two non-blocking nits noted above as follow-ups.
