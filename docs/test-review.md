# Test & Review: Backend hardening — `set_env()` sed-injection fix + team `run_id` path-traversal validation (backlog #10 + #11(b))

## Scope
Covers `docs/spec.md`'s full acceptance-criteria list: `install.sh`'s
`set_env()` sed-escaping fix (backlog item 10) and `app/teams.py`/`app/app.py`'s
`run_id` path-traversal validation (backlog item 11(b)). Both fixes reviewed
against the actual diff (`git diff` on `app/app.py`, `app/teams.py`,
`install.sh`, `tests/test_team_routes.py`, `tests/test_teams_lifecycle.py`,
plus new `tests/test_install_set_env.py`), with particular scrutiny on the
developer's documented deviation from the spec's proposed fix location for
item 11(b) (validation moved from `app/teams.py:_run_dir()` to two intake
points in `app/app.py`, per `docs/implementation.md` "Deviations from spec").

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `set_env()` upsert of a `\|`-bearing value on re-run: exit 0, value round-trips | Automated (`tests/test_install_set_env.py`) + my own adversarial bash harness (12 values incl. `\|`, `\&`, `\\`, `a\|b&c`, bare `\|`/`\|`/`&`) | pass | `python3 -m unittest tests.test_install_set_env -v` → 8/8 ok; my own `run_case.sh` sweep, all 12 cases PASS |
| 2 | `set_env()` `&`-bearing value round-trips byte-for-byte (no backreference corruption) | Automated + my own harness | pass | same as above |
| 3 | `set_env()` `\`-bearing value round-trips byte-for-byte | Automated + my own harness | pass | same as above |
| 4 | Plain value (no special chars) unchanged regression | Automated (`test_plain_value_unchanged_behavior`) + my own | pass | same as above |
| 5 | Empty value upserts cleanly | Automated (`test_empty_value_upserts_cleanly`) + my own | pass | same as above |
| 6 | First-write (`>>`) path untouched, handles all special chars | Automated (`test_first_write_append_path_handles_all_special_chars_already`) | pass | included in the 8/8 run above |
| 7 | `GET .../team/events?run_id=../../outside/evilrun` → 404, planted file never opened | Automated (`_get_forbidding_open_of` monkeypatch) + my own standalone live-server repro (with `_leads_root()` pre-created to make the traversal actually OS-resolvable — see Findings) | pass | full suite run; my own `manual_repro.py`, Part 1 |
| 8 | Same for `GET .../team/inbox` | Automated + my own live repro | pass | same as above |
| 9 | `POST .../team/resolve` with traversal `run_id` → 400, no state mutated (`_team_threads_get` stays None) | Automated (asserts `appmod._team_threads_get("proj")` is None) + my own live repro | pass | same as above |
| 10 | Real, `_run_id()`-generated `run_id` works identically through all three routes (regression) | Automated (full existing `TeamEventsEndpointTests`/`TeamInboxEndpointTests`/`TeamResolveEndpointTests` pass unmodified) + my own live repro using a real `teams.launch_team()` run (real tmux session, not a synthetic id) | pass | 790/790 suite; my own `manual_repro.py`, Part 2 (200/200/400-ordinary-business-logic across all 3 routes) |
| 11 | Malformed-but-non-traversal `run_id` (`"not-a-real-run"`, uppercase hex) → clean 404/400, no 500 | Automated (`test_malformed_non_traversal_run_id_*` in all 3 endpoint test classes + `RunIdRegexValidationTests` unit tests) | pass | full suite run |
| 12 | Edge cases: empty `run_id` still "no override" (not validation failure); URL-encoded traversal; wrong-length hex; NUL byte | Automated (`RunIdRegexValidationTests`, `test_url_encoded_traversal_run_id_404`, `test_run_id_with_nul_byte_404_no_500`) | pass | full suite run |
| 13 | `_run_dir()` itself remains unvalidated by design, only intake points gained the check | Automated (`test_run_dir_itself_does_not_validate_shared_internal_helper_unchanged`) + my own trace of every caller of `_run_dir`/`_run_json_path`/`_transcript_path`/`_inbox_path`/`_agent_log_path` in both `teams.py` and `app.py` | pass | grep trace below (Findings); confirmed only `state["run_id"]`-derived (already-validated) values reach these helpers from `app.py`, and `teams.main()`'s CLI subcommands are not attacker-reachable (no HTTP route spawns them) |
| 14 | Full existing test suite passes with no regressions | Automated | pass | `python3 -m unittest discover -s tests` → **790/790 OK** (matches developer's claim) |
| 15 | Node frontend suite unaffected | Automated | pass | `node tests/test_team_frontend.js && ... test_deploy_frontend.js && ... test_singleton_toggle_frontend.js && ... test_upload_frontend.js` → **84/84 (52+9+15+8) all PASS** |
| 16 | `bash -n install.sh` / `py_compile app.py teams.py` clean | Automated | pass | both ran clean |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` — **790 tests, 136s, OK** (765 baseline + 25 new, matches developer's stated count). Ran personally this session, not inferred from the developer's report. Node suite (`test_team_frontend.js`, `test_deploy_frontend.js`, `test_singleton_toggle_frontend.js`, `test_upload_frontend.js`) also run personally — 84/84 pass, no Node-facing code touched by this diff so this is a pure confirmation, not expected to have changed.

## Deep-dive: the spec deviation (item 11(b) validation location)

The developer moved validation from the spec's proposed single choke point
(`app/teams.py:_run_dir()`) to two intake points in `app/app.py`
(`_team_events_run_and_ownership()` and the `POST .../team/resolve` handler),
documented in `docs/implementation.md` "Deviations from spec" as necessitated
by 42 pre-existing test failures and a CLI UX regression when validating
inside `_run_dir()` itself.

I did not just accept this reasoning — I independently verified it:

1. **Traced every caller of `_run_dir()`/`_run_json_path()`/`_transcript_path()`/`_inbox_path()`/`_inbox_resolved_path()`/`_agent_log_path()`** in both `app/teams.py` and `app/app.py` via grep. Confirmed: the only places `app/app.py` passes an *unvalidated, client-supplied* `run_id` into any of these are the two now-patched intake points; every other reference in `app/app.py` (lines 4107–4139) uses `run_id = state["run_id"]`, where `state` was already resolved through one of the two validated intake points (or is the internally-derived "latest run" with no client input at all). No third route, and no CLI-adjacent path, reaches these helpers with attacker-controlled input — `teams.main()`'s `team-status`/`team-stop`/`team-reap` CLI subcommands are a separate `argparse` entry point never invoked by the running web server.
2. **Reproduced the original path-traversal exploit against a real running server myself**, independent of the test suite — see `manual_repro.py` below. Confirmed all three routes reject `run_id=../../outside/evilrun` (404/404/400) and a planted file's secret marker never appears in any response.
3. **Found and closed a gap in my own first attempt**: my first repro ran the traversal check *before* any team had ever been launched, meaning `_leads_root()` (`TEAM_STATE_DIR/leads`) didn't exist yet on disk. On Linux, path resolution through `..` requires every intermediate directory component to physically exist — so a `FileNotFoundError` in that state proves nothing about whether the fix's `_RUN_ID_RE` check is doing any work, only that the parent directory doesn't exist yet. I confirmed this by bypassing `teams._RUN_ID_RE` (patching it to `re.compile(r".*")`, simulating "no validation") with `_leads_root()` *not* pre-created, and got the identical `FileNotFoundError` — i.e., a false-negative-shaped "pass" that would occur even without the fix. I then re-ran with `_leads_root()` pre-created (realistic: true in any install after its first team run) with the bypass still in place, and confirmed the vulnerability **is** real under that realistic condition: `teams._load_state("../../outside/evilrun")` returned the planted file's full contents unmodified. Finally I reverted the bypass and reran against the real, shipped fix with `_leads_root()` pre-created — confirmed the fix still blocks it cleanly (404/404/400, no leak). This mirrors the project's own established "verify the technique itself discriminates, don't just trust a clean pass" discipline (seen elsewhere in this codebase, e.g. `TeamThreadsLockTests`'s own naive-vs-fixed comparison).
   - Separately, I confirmed the *existing* test suite's own traversal tests (`_get_forbidding_open_of`, which monkeypatches `builtins.open` to assert on the call itself rather than relying on real filesystem resolution) are immune to this caveat — they detect an attempted `open()` call regardless of whether `_leads_root()` exists on disk, so their "pass" was never subject to the false-negative risk my first naive repro attempt was.
4. **Confirmed the "real `run_id` still works" side of the criterion with an actual `teams.launch_team()`-issued run** (real tmux session via `tmux`, not a synthetic test value) through all three routes on a real live server — 200/200/400(ordinary "no pending question", not a rejection).
5. **Confirmed the `_scope_run_ids()` test-helper rework doesn't regress toward BACKLOG item 9's collision class.** The new fixed-width, 8-digit, all-digit `_RUN_ID_SCOPE = f"{os.getpid():08d}"` prefix is same-length for every process, so no two distinct pids' scope tokens can ever be a `startswith`-prefix of one another (a fixed-length string can only be a prefix of another string of equal length if they're identical) — this is a mathematically sound replacement for the old `"p<pid>-"` delimiter-terminated scheme, which achieved the same collision-freedom property via its own trailing `-` delimiter rather than fixed width. One minor, non-blocking caveat noted in Findings below: the zero-padding only *guarantees* fixed width if `os.getpid()` never exceeds 8 digits, which is true for every real-world Linux `pid_max` configuration but isn't enforced by the code itself.

Sample of the manual repro's real output (from my own script, run against the unmodified, shipped diff):
```
GET /team/events?run_id='../../outside/evilrun' -> status=404 payload={'error': 'unknown run_id for this project'}
GET /team/inbox?run_id='../../outside/evilrun' -> status=404 payload={'error': 'unknown run_id for this project'}
POST /team/resolve run_id='../../outside/evilrun' -> status=400 payload={'error': 'no run found for this project'}
*** PART 1 PASSED: all three routes reject the traversal run_id; planted file's secret content never appears in any response. ***

launch_team() result: ok=True run_id='1786706880-7737846340f4'
GET /team/events?run_id=1786706880-7737846340f4 -> status=200 run_id_in_payload=1786706880-7737846340f4
GET /team/inbox?run_id=1786706880-7737846340f4 -> status=200 payload={'pending': False}
POST /team/resolve run_id=1786706880-7737846340f4 -> status=400 payload={'error': 'no pending question for this project'}
*** PART 2 PASSED: a real, launch_team()-generated run_id works end-to-end through all three routes -- no regression. ***
```

## Defects found
None. Testing pass is clean; proceeding to review.

---

## Spec coverage
All 9 acceptance criteria in `docs/spec.md` are implemented and covered by an automated test I personally ran, plus my own independent manual verification for the two highest-risk ones (traversal blocked on all 3 routes; real run_id unaffected):

- [x] `set_env()` `\|` on re-run: exit 0, value preserved — `test_pipe_in_value_does_not_abort_reupsert_and_round_trips`, `test_value_can_be_changed_again_after_a_pipe_bearing_value`
- [x] `set_env()` `&` byte-for-byte — `test_ampersand_in_value_round_trips_byte_for_byte`
- [x] `set_env()` `\` byte-for-byte — `test_backslash_in_value_round_trips_byte_for_byte`
- [x] `set_env()` plain-value regression — `test_plain_value_unchanged_behavior`
- [x] GET `/team/events` traversal → 404, file never opened — `test_path_traversal_run_id_404_planted_file_never_opened` + my own live repro
- [x] GET `/team/inbox` traversal → 404, file never opened — same in `TeamInboxEndpointTests` + my own live repro
- [x] POST `/team/resolve` traversal → 400, no state mutated — same in `TeamResolveEndpointTests` (asserts no thread started) + my own live repro
- [x] Real run_id unaffected on all 3 routes (regression) — full existing endpoint test classes pass unmodified + my own live `launch_team()` repro
- [x] Malformed-non-traversal run_id → clean 404/400, no 500 — `test_malformed_non_traversal_run_id_404_no_500` (×3 classes) + `RunIdRegexValidationTests`
- [x] Full suite passes — 790/790, run personally

No gaps found. The spec's "Affected areas" note ("reviewer should confirm this holds rather than assume it" re: `app.py`'s existing exception handling doing the right thing with zero route-level changes) is moot as written, since the developer's deviation *did* add route-level changes — but I independently confirmed the deviation's actual security property (validated at intake, before any path-join) fully satisfies the spec's stated goal in "Proposed approach" §2, and that the response shapes are byte-identical to what already existed for the "not found" case, matching the spec's own "Risk / rollback notes" claim.

## Findings (most severe first)

### 1. `_RUN_ID_SCOPE`'s fixed-width assumption is not enforced by the code — nit
- File: `tests/test_team_routes.py:195` (`_RUN_ID_SCOPE = f"{os.getpid():08d}"`)
- Issue: Python's `:08d` format spec is a *minimum* width, not a truncating fixed width — if `os.getpid()` ever exceeded 8 digits (99,999,999), the resulting scope token would be 9+ digits and the "same-length strings can't prefix each other" collision-freedom property this relies on would no longer hold for a pid straddling that boundary (e.g., pid `12345678` and pid `123456780` would produce prefix-colliding tokens).
- Failure scenario: This requires a Linux `pid_max` configured above 99,999,999, which is far outside any default or realistic production configuration (`pid_max` traditionally caps at 4,194,304 on 64-bit Linux) — this is test-infrastructure-only code, not attacker-reachable, and the risk is effectively theoretical. Not blocking; the comment in the code itself already correctly identifies this as "comfortably covering any real pid" rather than claiming an absolute guarantee.

### 2. Two near-identical validation blocks in `app/app.py`, not factored into a shared helper — nit
- File: `app/app.py:4084-4085` and `app/app.py:4345-4346`
- Issue: `if not teams._RUN_ID_RE.match(run_id): return ...` is duplicated (with different error shapes/status codes) at both intake points rather than factored into one small helper.
- Failure scenario: None — this is a simplicity observation, not a correctness or security issue. The two call sites already had structurally similar-but-distinct exception handling before this cycle (different error messages/status codes for GET vs POST), so this duplication is consistent with the pre-existing pattern rather than newly introduced sprawl. The developer's stated rationale for not adding a new `teams.py` wrapper function (matching this repo's existing convention of `app.py` calling `teams._load_state()`/`teams._persist()`/`teams._inbox_path()` directly) is reasonable and documented. Not worth blocking on.

No must-fix or should-fix findings. Correctness, security, and simplicity all check out against the diff.

## Follow-ups (non-blocking)
- None required. The two nits above are optional and don't need a dedicated follow-up cycle.

## Overall verdict
**Approve.**

Both fixes are correctly implemented, fully covered by automated tests I personally ran (790/790 Python, 84/84 Node, both fresh test files clean), and independently verified against a real running server using my own from-scratch repro scripts (not just re-reading the developer's or the test suite's own assertions) — including catching and resolving a subtlety in my own first repro attempt (the `_leads_root()`-must-pre-exist caveat) before trusting a "pass." The developer's documented deviation from the spec's proposed fix location (validation at the two `app.py` intake points instead of inside `teams.py:_run_dir()`) is sound, narrower in blast radius than the spec's original proposal, and I traced every caller of the path-join helpers myself to confirm no other route or CLI-adjacent path is left exposed. No must-fix or should-fix issues found; two optional nits noted above, neither blocking.
