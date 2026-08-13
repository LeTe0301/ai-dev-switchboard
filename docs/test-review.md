# Test & Review: switchboard-side deploy dispatch (2c part 2b)

## Scope
Covers `docs/spec.md`'s 10 acceptance criteria for the manual-confirmation
deploy dispatch feature: `deploy-map.json` loading/validation, `deploy_run()`
push+restart dispatch, the `POST /instance/<name>/deploy` route, `/status`'s
new `deploy` field, the "Deploy" button UI, and `install.sh`'s two new
unconditional blocks. **This is the re-review pass after a changes-requested
loop-back**; it supersedes the prior draft of this file. Per the dispatch
instructions, the original spec/design/route-auth/concurrency-lock/`esc()`
injection reasoning (all previously verified sound and unchanged in this
diff) was not re-litigated — only the fix and the two follow-up additions
were independently re-verified, plus a fresh full regression run.

## Prior finding and fix verification

### Defect 1 (must-fix, from prior pass): non-numeric `port` crashed `/status`
- Read `app/app.py:761-787` (`_load_deploy_map()`) directly. The `int(entry.get("port") or 22)`
  coercion is now wrapped:
  ```python
  try:
      port = int(entry.get("port") or 22)
  except (TypeError, ValueError):
      continue  # non-numeric "port" -- treat as absent, not a crash
  ```
  consistent with every other per-entry validation drop-and-continue in that
  function.
- **Revert-and-watch-it-fail check performed**: temporarily reverted this
  exact hunk back to the bare `port = int(entry.get("port") or 22)` (no
  try/except) and re-ran the three new regression tests. All three failed,
  and `DeployEndpointTests.test_status_survives_non_numeric_port_and_keeps_other_projects_intact`
  reproduced the *exact* original live symptom (`http.client.RemoteDisconnected:
  Remote end closed connection without response`) rather than a generic
  assertion failure — confirming the test genuinely exercises this bug, not
  a coincidental pass. Restored the fix afterward; `git diff --stat app/app.py`
  confirmed no residual changes, and the full suite was re-run clean (below).
- **Verdict: fix confirmed real and correct.**

### Follow-up 1 (AC10 automated regression guard)
- `tests/test_deploy_dispatch.py::DeployNeverCalledFromPollSyncTests::test_sha_change_drives_sync_but_never_deploy_run`
  drives the real, unmocked `_gitea_poll_one` → `_gitea_sync_bg` → `_gitea_sync_run`
  chain (only `_gitea_api` and `subprocess.run` are stubbed, matching
  `test_gitea_poll.py`'s established technique) through a SHA change, with
  `appmod.deploy_run` monkeypatched to a call-recording stub, and asserts it
  is never invoked. Confirmed via direct code read (`grep -n
  "_gitea_poll_one\|_gitea_sync_bg\|_gitea_sync_run" app/app.py`) that this
  is the real call chain, not a stand-in. **Verdict: genuine regression
  guard, not a shallow mock that would pass regardless.**

### Follow-up 2 (frontend quote-injection test)
- `tests/test_deploy_frontend.js` "a quote-containing host/service value
  renders safely and still dispatches to the right target" constructs a
  `deploy.host`/`deploy.service` containing `"`/`'`/`onclick=` payloads,
  asserts the rendered row HTML never contains those raw strings at all and
  that `onclick` stays exactly `doDeploy('proj')`, then drives an actual
  `doDeploy('proj')` call and asserts it still dispatches a POST to
  `/instance/proj/deploy`. This is a real, meaningful assertion (not just
  "doesn't throw") and matches the reasoning in the prior review's Finding
  #3. **Verdict: genuine test.**

## Regression run (actually executed this session)
- `python3 -m unittest discover -s tests -v` → **287 tests, all pass.**
- `python3 -m unittest tests.test_deploy_dispatch -v` → **42 tests, all pass**
  (confirmed the specific new tests ran: `test_entry_with_non_numeric_port_is_dropped_not_raised`,
  `test_one_entry_with_non_numeric_port_does_not_affect_others`,
  `test_status_survives_non_numeric_port_and_keeps_other_projects_intact`,
  `test_sha_change_drives_sync_but_never_deploy_run`).
- `node tests/test_deploy_frontend.js` → **9/9 pass.**
- `node tests/test_singleton_toggle_frontend.js` → **15/15 pass** (unrelated
  regression check, still green).

All numbers match the developer's report exactly.

## Test cases (carried forward from prior pass, all still hold)

| # | Criterion / case | Method | Result |
|---|---|---|---|
| 1 | Valid map entry → `/status` includes `deploy{host,deploy_path,service}`, no `key` | automated | pass |
| 2 | No map entry → `/status` omits `deploy` field | automated | pass |
| 3 | Reachable target: Deploy click → push lands + restarts + UI success | automated (real ssh/rsync/systemd) | pass |
| 4 | Unreachable target → 502, no hang, detailed message | automated (real) | pass |
| 5 | Push OK, restart fails → distinct message | automated (real) | pass |
| 6 | Overlapping dispatch → second gets 409, no second subprocess pair | automated | pass |
| 7 | No map entry, direct POST → 404, no crash, no subprocess | automated | pass |
| 8 | `key` resolves outside `DEPLOY_KEYS_DIR` → treated as absent | automated | pass |
| 9 | `install.sh` re-run leaves hand-edited map/keys untouched | automated | pass |
| 10 | Poll/sync path never calls `deploy_run()` | **now automated** | pass (was manual-inspection-only; closed this cycle) |
| 11 (edge) | Malformed individual map entry must not take down other projects/`/status` | automated, plus live revert-and-fail check this session | **pass (was FAIL, now fixed and verified)** |
| 12 | `doDeploy`/`esc()` HTML-injection reasoning | independent code read (prior pass) + **new automated quote-character test** (this pass) | confirmed sound, now with a regression guard |

## Diff scope check
`git diff app/app.py` shows the same hunks as the prior pass (map loading,
lock, `deploy_run`, route, `/status` field, `PAGE_TEMPLATE` JS/CSS) plus the
single try/except addition inside `_load_deploy_map()`. No unrelated code
changed. `tests/test_deploy_dispatch.py` and `tests/test_deploy_frontend.js`
gained only the new test classes/cases described in `docs/implementation.md`'s
"Post-review bugfix" section — verified by reading both files directly.

## Findings
None outstanding. Both non-blocking follow-ups from the prior pass
(AC10 regression guard, quote-injection frontend test) were addressed and
independently verified as genuine, not just claimed. The must-fix defect is
fixed and verified with an actual revert-and-fail check, not just a code
read.

## Spec coverage
All 10 literal acceptance criteria plus the general "one malformed entry
must not take down others" validation contract (`docs/spec.md`'s "Proposed
approach" #2 / edge cases) are now implemented and covered by at least one
automated test that was independently confirmed to fail against the
pre-fix code.

## Overall verdict
**Approved.** The must-fix defect from the prior pass (`app/app.py`'s
unguarded `int(entry.get("port") or 22)` crashing `/status` on a non-numeric
`port`) is fixed correctly and the fix was verified with a live
revert-and-watch-it-fail check against the developer's new regression tests,
not just a code read. Both previously-noted non-blocking follow-ups (AC10
automated regression guard, frontend quote-injection test) were also
addressed and independently confirmed to be genuine, non-vacuous tests. Full
regression suite re-run clean this session: 287/287 Python, 9/9 + 15/15
Node. No new findings. This closes the 2c part 2b build cycle — hand back to
product-manager.
