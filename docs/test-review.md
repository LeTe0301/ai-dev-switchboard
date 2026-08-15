# Test & Review: Round 6 — Taiga gateway startup-ordering crash-loop, ACL-aware push-spec security check, /status terminal-state staleness (items 30, 37, 38)

## Scope
Independent verification of all three fixes against `docs/spec.md`'s
acceptance criteria: item 30's `taiga-up.sh` settle-window recheck (the
`docker-compose.override.yml` health-gate half of item 30 was already
applied to the working tree before this session and is verified here as
part of the diff, not re-litigated as a design decision), item 37's
ACL-aware `_check_config_permissions`, and item 38's `TEAM_TERMINAL_STATUSES`
constant + `/status` `terminal` field. Plus independent verification of the
developer's specific claims flagged for extra scrutiny: the item-30 timeout
arithmetic, item-37 crash-safety when `getfacl` is absent, item-38's
call-site behavior preservation, and the "project": null investigation.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Item 30: `docker-compose.override.yml` heredoc includes `taiga-front` healthcheck + `taiga-gateway`'s upgraded `depends_on`; no file inside the `taiga-docker` checkout touched | Read `install.sh` diff directly | pass | Heredoc adds `taiga-front.healthcheck` (`wget -q --spider http://127.0.0.1/`) and `taiga-gateway.depends_on` (`taiga-front: service_healthy`, `taiga-back`/`taiga-events`: `service_started`); diff touches only `install.sh`, nothing under `$TAIGA_DIR` |
| 2 | Item 30: `127.0.0.1` vs spec's literal `localhost` example is a disclosed, reasoned deviation | Read `install.sh`'s own comment + `docs/implementation.md` "Deviations from spec" | pass | Comment explicitly documents the IPv6-`::1`/no-listener hands-on finding; consistent story in both places |
| 3 | Item 30: `taiga-up.sh` settle-window recheck — a gateway reporting "running" on the first check but "exited" before the settle window elapses is a failed attempt (rm -f + backoff + retry) | `python3 -m unittest tests.test_taiga_up_retry -v` (new `test_settle_window_recheck_catches_gateway_that_dies_before_settling`) | pass | 1 test targeted + full file 8/8 pass |
| 4 | Item 30 test #3 is a genuine regression test, not tautological | Reverted `scripts/taiga-up.sh` only (`git stash push -- scripts/taiga-up.sh`), re-ran the same test, restored | **fails without the fix** | `AssertionError: 1 != 2` (up_calls) — proves the test exercises real settle-window behavior; working tree confirmed byte-for-byte restored after (`git stash pop`, `git status` clean on that file) |
| 5 | Item 30: `TAIGA_UP_SETTLE_SECONDS` env override honored, default is 5 | `python3 -m unittest tests.test_taiga_up_retry.TaigaUpRetryTests.test_settle_seconds_env_override_is_honored tests.test_taiga_up_retry.TaigaUpRetryTests.test_default_settle_seconds_is_5 -v` | pass | both pass |
| 6 | Item 30: `app.py`'s `TAIGA_UP_SCRIPT` "up" timeout arithmetic re-derived independently, not trusted from the comment | Hand-summed from `scripts/taiga-up.sh` directly: backoff 10+20+40+80=150s (attempts 1-4 only, guarded by `attempt -lt MAX`); settle sleeps up to 5×5=25s (paid only when an attempt's initial check reports "running"); worst case 175s. 220−175=45s margin across up to 19 real subprocess calls (5×`up -d` + up to 10×`ps` + 4×`rm -f`) ≈2.4s/call | pass | Matches the comment's own claimed arithmetic exactly — independently re-derived, not just read |
| 7 | Item 30: backend timeout / frontend `timeoutMs` / test assertions all agree on 220 | `grep -n "timeout = 220\|timeoutMs: 220000" app/app.py` + `python3 -m unittest tests.test_taiga.TaigaRunTests.test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop -v` + `node tests/test_singleton_toggle_frontend.js` | pass | backend `timeout = 220`, frontend `taiga: {timeoutMs: 220000, ...}`, Python test asserts `220`, JS `TIMEOUT_MS_CONFIG.taiga = 220000` (deliberately-duplicated per existing convention) — all four in sync |
| 8 | Item 30: `bash -n`/shellcheck clean on both touched shell files | `bash -n scripts/taiga-up.sh install.sh`; `shellcheck scripts/taiga-up.sh install.sh` | pass | both `bash -n` clean; shellcheck clean on `taiga-up.sh`; `install.sh` has 2 pre-existing notes (SC2015 line 70, SC2001 line 972) confirmed present identically on `git show HEAD:install.sh` — unrelated to this diff |
| 9 | Item 37: item-29-style ACL grant (mode 640 via recomputed mask, `other::---`) prints no warning | `python3 -m unittest tests.test_taiga_push.ConfigPermissionsTests.test_item29_style_acl_grant_prints_no_warning -v` | pass | pass, `buf.getvalue() == ""` |
| 10 | Item 37: same case never prints `chmod 600` | `python3 -m unittest tests.test_taiga_push.ConfigPermissionsTests.test_item29_style_acl_grant_output_never_contains_chmod_600 -v` | pass | pass |
| 11 | Item 37 tests #9-10 are genuine regression tests, not tautological | Reverted `scripts/taiga_push_spec.py` only, re-ran, restored | **errors without the fix** | `AttributeError: module 'taiga_push_spec' has no attribute '_read_getfacl'` — proves the seam and the fix are both new and load-bearing; tree restored, `git status` clean on that file |
| 12 | Item 37: genuinely-loose ACL (`other::r--`) warns with `setfacl` remediation, never `Run: chmod` | `python3 -m unittest tests.test_taiga_push.ConfigPermissionsTests.test_genuinely_loose_acl_warns_with_setfacl_remediation_not_chmod -v` | pass | pass; `"Run: setfacl"` present, `"Run: chmod"` absent (the "do NOT run chmod" safety note is allowed to mention the word) |
| 13 | Item 37: no ACL at all → unchanged plain `st_mode` behavior (mode 644 warns+`chmod 600`, mode 600 silent) | `python3 -m unittest tests.test_taiga_push.ConfigPermissionsTests -v` (full class, 9 tests incl. 2 pre-existing) | pass | 9/9 pass |
| 14 | Item 37: `getfacl` unavailable → falls back to plain `st_mode` check, does not crash | `python3 -m unittest tests.test_taiga_push.ConfigPermissionsTests.test_getfacl_unavailable_falls_back_to_plain_st_mode_check tests.test_taiga_push.ConfigPermissionsTests.test_getfacl_unavailable_mode_600_prints_no_warning -v` | pass | both pass |
| 15 | Item 37: `_read_getfacl` never raises on missing binary / timeout / nonzero exit — read the actual exception handling, not the docstring's claim | Read `scripts/taiga_push_spec.py:167-179` directly | pass | `except (OSError, subprocess.TimeoutExpired): return None` — `FileNotFoundError` (missing `getfacl` binary) and `PermissionError` are both `OSError` subclasses, caught; nonzero returncode handled separately via the ternary, not an exception path |
| 16 | Item 37: `_read_getfacl`'s own exception paths (missing binary, timeout, nonzero exit, success) | `python3 -m unittest tests.test_taiga_push.ReadGetfaclTests tests.test_taiga_push.ParseAclOtherBitsTests -v` | pass | 8/8 pass, all four `_read_getfacl` paths + 4 `_parse_acl_other_bits` cases (no-mask, mask+clean, mask+dirty, mask-but-no-other-line) |
| 17 | Item 37: sandbox actually has no `getfacl` — the un-monkeypatched pre-existing tests exercise the real fallback, not a simulated one | `which getfacl; which setfacl` | confirmed | both absent in this sandbox — corroborates the developer's own "Known limitations" claim, and means test #13's mode-600/644 cases ran the real `FileNotFoundError` path end to end |
| 18 | Item 38: `TEAM_TERMINAL_STATUSES` is `True` for `escalated_max_rounds`/`finished`/`error`/`stopped`, `False` for `running`/`blocked_ask_user`/`blocked_board_write`/no-run | `python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests.test_terminal_field tests.test_team_routes.StatusRosterAndCompositionTests.test_terminal_field_false_when_no_run_ever_started -v` | pass | both pass, parametrized over every status named in the acceptance criteria |
| 19 | Item 38 test #18 is a genuine regression test | Reverted `app/app.py` + `app/teams.py`, re-ran `test_terminal_field`, restored | **errors without the fix** | `KeyError: 'terminal'` — proves the field genuinely didn't exist before this diff; tree restored, `git status` clean on both files |
| 20 | Item 38: single source of truth — `stop_team()`, `sweep_dead_teams()`, `interject()`, and `/status`'s `terminal` computation all reference `TEAM_TERMINAL_STATUSES`; no remaining duplicate literal 4-tuple | Read `app/teams.py` diff directly + `grep -n` for the literal tuple pattern across `app/teams.py`/`app/app.py` | pass | all 3 call sites now reference the constant (diff confirms same 4 values, pure substitution); grep finds zero remaining literal-tuple occurrences, only prose comments naming the 4 statuses |
| 21 | Item 38: existing `test_waiting_on_you_true_only_for_blocked_ask_user_never_for_escalated_max_rounds`/`test_escalation_kind_field` unchanged and still pass | `python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests -v` | pass | full class passes, both named tests present and green |
| 22 | Item 38: the one exact-dict-match test elsewhere (`test_status_idle_when_no_run_ever_started`) updated to include `"terminal": False` | Read diff directly + independent grep for other exact-dict matches on `inst["team"]` | pass | diff shows the key added; grep confirms no other test does an exact-dict match on that dict (developer's claim independently reproduced) |
| 23 | Item 38 §3 ("project": null investigation): no code path writes a literal `"project"` key into run state | `grep -rn '"project":' app/ scripts/ tests/` (own independent grep, not trusting the developer's) | pass | only match is `taiga_push_spec.py`'s unrelated Taiga API request body (`_create_userstory`) — confirms the "resolved-by-explanation, not reproduced" finding |
| 24 | Item 38 §3: `_new_state()` only ever sets `project_name`, never `project` | Read `app/teams.py` `_new_state()`/`_persist()` directly | pass | confirmed; also corroborated by the full-suite run's own captured `run.json` output, which shows `"project_name": null` (bare CLI runs) — never a `"project"` key, anywhere |
| 25 | Item 30/37/38: full existing test suite, no regressions | `python3 -m unittest discover -s tests` (own run, this session) | pass | **1232 tests, 3 failures** — all 3 in `tests/test_teams_grounding.py`, independently reproduced identically on `git stash` (unmodified tree), root-caused to the untracked `CLAUDE.md` at repo root (present, confirmed via `ls -la`); tree fully restored via `git stash pop` afterward |
| 26 | New-test count sanity check | Arithmetic: 1232 − 1213 (round 5's count) = 19 new tests. Item 30: 3 (settle-window catch, env override, default). Item 37: 6 `ConfigPermissionsTests` + 4 `ParseAclOtherBitsTests` + 4 `ReadGetfaclTests` = 14. Item 38: 2. 3+14+2=19 | pass | exact match, corroborates nothing was silently skipped or double-counted |
| 27 | `node tests/test_singleton_toggle_frontend.js` | Ran directly, this session | pass | `ALL PASS (19/19)` |
| 28 | `python3 -m py_compile` on all touched Python files | `python3 -m py_compile app/app.py app/teams.py scripts/taiga_push_spec.py` | pass | clean |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` (own run,
this session) — **1232 tests, 3 failures**, all independently confirmed
attributable to an untracked `CLAUDE.md` at the repo root (test case 25
above), not a regression from this round's diff. Targeted re-runs of every
touched test module (`tests.test_taiga`, `tests.test_taiga_push`,
`tests.test_taiga_up_retry`, `tests.test_team_routes`, 197 tests) all pass.
Frontend suite 19/19. `bash -n`/shellcheck clean on both touched shell
files (2 pre-existing, unrelated shellcheck notes in `install.sh`).
`py_compile` clean on all three touched Python files.

## Defects found
None. All automated tests pass (including 3 independent revert-and-watch-
it-fail checks proving the new tests for items 30/37/38 are genuinely wired
to the fixes, not tautological), the full suite regression is clean once
the pre-existing `CLAUDE.md`-caused failures are accounted for (independently
reproduced via `git stash`, not just trusted from `docs/implementation.md`),
and the item-30 timeout arithmetic, item-37 crash-safety, and item-38
call-site behavior preservation all independently re-derive to the same
conclusions the developer's own summary claimed. Proceeding to the review
pass.

---

## Spec coverage
- **Item 30**: fully implemented. `docker-compose.override.yml` health-gate
  and `taiga-up.sh`'s settle-window recheck both match the spec's proposed
  approach; timeout arithmetic independently re-derived and correct (175s
  worst-case sleep, 220s ceiling, 45s margin). The four hands-on acceptance
  criteria (fresh Proxmox install, `docker compose ps`/`logs` inspection,
  repeated toggle cycles) are **not testable in this sandbox** — no Docker
  Compose available — same disclosed, sandbox-wide limitation every prior
  round in this series has carried; not a gap introduced by this round.
- **Item 37**: fully implemented and fully covered by automated tests,
  including the two edge cases flagged for extra scrutiny (crash-safety
  when `getfacl` is absent, genuinely-loose-ACL still warns). The one
  hands-on acceptance criterion (real `taiga-configure-push.sh` run +
  live `board_read`/`board_write`) is not testable in this sandbox
  (`getfacl`/`setfacl` confirmed absent) — disclosed, not blocking.
- **Item 38**: fully implemented and fully covered. Every status value in
  `TEAM_TERMINAL_STATUSES` is exercised by `test_terminal_field`, including
  the specific `escalated_max_rounds` case item 38 was filed over. The
  `"project": null` investigation is documented per the spec's own
  "Proposed approach" §3, and independently re-verified here (test cases
  23-24) rather than taken on faith — genuinely resolved-by-explanation,
  not reproduced under current code. The one hands-on acceptance criterion
  (drive a real run to `escalated_max_rounds`, poll `/status`) is not
  testable in this sandbox — disclosed, not blocking.
- No acceptance criterion in `docs/spec.md` is unimplemented or untested
  among those exercisable without live Docker/Taiga/tmux infrastructure.

## Findings (most severe first)

### 1. `docs/BACKLOG.md` was not updated to record items 30/37/38 as fixed — should-fix, non-blocking
- File: `docs/BACKLOG.md` (unmodified — `git diff --stat` shows no change to this file)
- Issue: every prior round in this series recorded its own fixes directly in `docs/BACKLOG.md` as part of the same commit — e.g. round 5's commit `94f82f8` added a "## Round 5 fixes (2026-08-15): items 29-v2, 30-v2, 34, 35" section summarizing what was closed, in the same diff as the code changes. This round's diff makes no corresponding edit, even though items 37 and 38 were originally filed into `docs/BACKLOG.md` by the immediately-preceding commit (`4b42226`, "Backlog: record round 5 regression-verification results, open items 37 and 38"), and item 30 has an existing entry from round 4.
- Failure scenario: if this diff is committed as-is, `docs/BACKLOG.md` will continue to list items 30, 37, and 38 as open indefinitely (or until a future, unrelated commit happens to touch it), even though the code fix for all three has landed — the backlog ledger silently drifts out of sync with the codebase's actual state, which is exactly the kind of staleness this multi-round series' own record-keeping exists to prevent. Not a functional defect and not something `docs/spec.md`'s own "Affected areas" section asked for (it doesn't list `docs/BACKLOG.md` for any of the three items), so not blocking approval — but worth closing before or alongside the commit for this cycle, matching established convention.

## Follow-ups (non-blocking)
- Consider whether `TAIGA_UP_SETTLE_SECONDS`'s 5s default (and the
  healthcheck's `interval`/`timeout`/`retries`/`start_period` values) need
  tuning once real hands-on timing data from a Proxmox host is available —
  the spec's own "Open questions" already flags this as unmeasured, and
  nothing in this round's sandbox-only verification can confirm or refute
  it either way.
- The four item-30 and one item-37 and one item-38 hands-on acceptance
  criteria (fresh install, live Docker/getfacl/team-run infrastructure)
  remain unverified in any sandbox to date across this entire round-1-6
  series — worth flagging to whoever next has access to the Proxmox
  verification host, same as every prior round's own note.

## Overall verdict
**Approve with follow-ups.** The testing pass is fully clean: every
acceptance criterion in `docs/spec.md` that's exercisable without live
Docker/Taiga/tmux infrastructure is genuinely implemented and covered by
automated tests I ran myself this session, three independent
revert-and-watch-it-fail checks confirm the new tests for items 30/37/38
are wired to the real fixes (not tautological), the full suite is clean
aside from the same pre-existing, independently-reproduced
`CLAUDE.md`-caused failures every prior round in this series has carried,
and the specific arithmetic/crash-safety/behavior-preservation claims
flagged for extra scrutiny all independently re-derive correctly. Finding 1
(the missing `docs/BACKLOG.md` update) is a should-fix, not a must-fix — it
doesn't affect correctness, security, or spec coverage, only this project's
own documentation-consistency convention — so it does not block approval;
recommend closing it in this cycle's commit or immediately after.
