# Implementation: Round 6 — Taiga gateway startup-ordering crash-loop, ACL-aware push-spec security check, /status terminal-state staleness (items 30, 37, 38)

## Summary
Three independent fixes from `docs/spec.md` ("Round 6"): `install.sh` now
health-gates `taiga-gateway`'s startup on `taiga-front` actually being
resolvable (already applied before this session started) and
`scripts/taiga-up.sh` gained a settle-window recheck so a gateway that
briefly reports `running` before dying is treated as a failed attempt, not
a false success; `scripts/taiga_push_spec.py`'s config-permission check is
now ACL-aware so it stops misreading an item-29-style narrow ACL grant's
recomputed mask as a loose group permission and recommending a
mask-collapsing `chmod`; and `GET /status` now exposes an additive
`team.terminal` boolean, sourced from the same `TEAM_TERMINAL_STATUSES`
constant `stop_team()`/`sweep_dead_teams()`/`interject()` already used
independently (as three duplicated inline literals), so a poller no longer
has to infer completion from the coarser `status`/`waiting_on_you` fields
and hang forever on `escalated_max_rounds`.

## Root cause

### Item 30 (settle-window half)
`scripts/taiga-up.sh`'s success check was a single point-in-time
`docker compose ps taiga-gateway --format '{{.State}}'` read immediately
after `up -d` returns. Round 5 observed the gateway report `Up` for under a
second before crashing, so the script could exit 0 while the public
entrypoint was already dead. The `docker-compose.override.yml` health-gate
(already applied to `install.sh` before this session — see "Deviations
from spec" below) addresses the specific `taiga-front`-not-yet-resolvable
race; the settle-window recheck in this session's work is defense in depth
against *any* other transient early-exit, per the spec's own framing.

### Item 37
`_check_config_permissions` read `stat.S_IMODE(os.stat(path).st_mode)` and
warned whenever `mode & 0o077` was nonzero. Once `taiga-configure-push.sh`
(item 29) runs `setfacl -m u:switchboard-svc:r` on the mode-600 config
file, `setfacl` recomputes the file's ACL *mask* to the union of the
owning group's permission and every named ACL entry — here, `r`. `stat()`
then reports that mask (not the real group-class exposure) in the
group-class bits, so the file "looks" like mode `0640` even though its
actual `other::` exposure is still `---`. The old check saw `0640 & 0o077
!= 0`, called it loose, and recommended `chmod 600` — which, if followed,
recomputes the mask down to `mask::---` and silently makes the
`switchboard-svc` grant's *effective* permission `---`, reverting item 29
with no indication anything changed.

### Item 38
`app/teams.py` already had a correct, single 4-status terminal check
(`("finished", "escalated_max_rounds", "error", "stopped")`), but it was
duplicated verbatim as an inline literal in three separate places
(`stop_team()`, `sweep_dead_teams()`, `interject()`) and `/status`'s own,
independently-written `team_status` bucketing in `app/app.py` never reused
it — `escalated_max_rounds` deliberately stays under the coarser
`"blocked"` `team_status` bucket (see the adjacent, unchanged
`waiting_on_you` comment), so a caller polling `/status` had no
unambiguous "is this run actually done" signal and could hang indefinitely
on an `escalated_max_rounds` run.

## Changes by file

- `scripts/taiga-up.sh` — added `TAIGA_UP_SETTLE_SECONDS` (default 5,
  same override-with-env-var convention as `TAIGA_UP_MAX_ATTEMPTS`/
  `TAIGA_UP_RETRY_BACKOFF_SECONDS`). After the loop's initial
  `state = ... running` check succeeds, sleeps `TAIGA_UP_SETTLE_SECONDS`
  and re-runs the same `docker compose ps` check; only `exit 0` if it's
  still `running`. A die-before-settled falls through into the existing
  "didn't come up cleanly" branch (message, `rm -f`, backoff, loop) and
  consumes one of `TAIGA_UP_MAX_ATTEMPTS` like any other failed attempt.
- `install.sh` — no changes this session (the item-30
  `docker-compose.override.yml` healthcheck/`depends_on` heredoc and its
  explanatory comment were already applied to the working tree before
  this session started; verified it matches the spec's proposed approach,
  with the one deliberate `127.0.0.1`-vs-`localhost` deviation the comment
  itself documents — see "Deviations from spec").
- `app/app.py`:
  - `taiga_run()`'s `"up"` timeout raised from 180s to 220s, and its
    margin-arithmetic comment updated, to cover the new worst-case pure
    sleep (150s backoff + up to `TAIGA_UP_MAX_ATTEMPTS` ×
    `TAIGA_UP_SETTLE_SECONDS` = 175s) plus the extra up-to-5 `ps` calls
    the settle-window recheck can add per full retry run.
  - `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` raised from `180000` to
    `220000` to stay `>=` the backend's own new timeout (existing
    documented invariant from round 5's own Finding 1).
  - `/status`'s per-instance `inst["team"]` dict gained an additive
    `"terminal"` field: `run is not None and run["status"] in
    teams.TEAM_TERMINAL_STATUSES`. `team_status`, `waiting_on_you`, and
    `escalation_kind` are unchanged.
- `app/teams.py`:
  - Added module-level `TEAM_TERMINAL_STATUSES = ("finished",
    "escalated_max_rounds", "error", "stopped")` near
    `TEAM_SESSION_STALE_TTL_SECONDS`.
  - `stop_team()`, `sweep_dead_teams()`, `interject()` now reference
    `TEAM_TERMINAL_STATUSES` instead of their own inline literal tuple
    (pure refactor, no behavior change).
- `scripts/taiga_push_spec.py`:
  - Added `import subprocess`.
  - Added `_parse_acl_other_bits(getfacl_output)` — parses `getfacl -p`
    output; returns `None` if there's no `mask::` line (no extended ACL,
    plain `st_mode` check still applies), else `{"other": int,
    "other_str": str}` from the `other::` line.
  - Added `_read_getfacl(path)` — the one new seam, shells out to
    `getfacl -p <path>` with a 5s timeout; returns `None` (never raises)
    on a missing binary, timeout, or nonzero exit, matching
    `taiga-configure-push.sh`'s own best-effort `command -v setfacl`
    fallback precedent.
  - Rewrote `_check_config_permissions`: when `_read_getfacl` returns
    ACL data with a `mask::` line, the warning (if any) is driven by
    `other::` alone (`setfacl`-based remediation, explicit "do NOT run
    chmod" note) instead of the recomputed-mask `st_mode` bits; when no
    ACL is present (or `getfacl` is unavailable), behavior is byte-for-
    byte unchanged from before this round.
- `tests/test_taiga_up_retry.py` — `_run()` extended with `settle_die_at`/
  `settle_seconds` params; the `docker` stub now tracks a per-attempt
  `ps`-call count so it can simulate "running" on the settle-window
  recheck's *first* call but "exited" on its *second* call for a specific
  attempt. `sleep` is now logged (still a no-op) so tests can assert the
  settle-seconds value actually reached the script. Added
  `test_settle_window_recheck_catches_gateway_that_dies_before_settling`,
  `test_settle_seconds_env_override_is_honored`,
  `test_default_settle_seconds_is_5`. Existing tests updated for the new
  4-tuple `_run()` return signature only (no behavior assertions changed).
- `tests/test_taiga.py` — `test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop`
  updated to assert `220` instead of `180`.
- `tests/test_singleton_toggle_frontend.js` — the deliberately-duplicated
  `TIMEOUT_MS_CONFIG.taiga` constant (see its own comment: duplicated, not
  imported, so a real app.py regression still shows up as a mismatch
  instead of trivially passing) updated from `180000` to `220000`.
- `tests/test_taiga_push.py` — extended `ConfigPermissionsTests` with a
  `setUp`/`tearDown` pair that monkeypatches `tps._read_getfacl` (same
  seam-mocking convention the file's own `_taiga_request` tests already
  use), and 6 new cases covering the item-29-style-grant/no-warning,
  never-`chmod-600`, genuinely-loose-ACL/`setfacl`-remediation,
  no-ACL-unchanged, and `getfacl`-unavailable-fallback (both mode-644 and
  mode-600) scenarios. Added `ParseAclOtherBitsTests` and
  `ReadGetfaclTests` (the latter monkeypatches `tps.subprocess.run`
  directly, same restore-in-`tearDown` technique
  `tests/test_taiga.py::TaigaRunTests` already uses for `appmod`'s
  `subprocess.run`).
- `tests/test_team_routes.py`:
  - Added `test_terminal_field` (parametrized over every status named in
    the spec's acceptance criteria) and
    `test_terminal_field_false_when_no_run_ever_started` to
    `StatusRosterAndCompositionTests`, mirroring
    `test_waiting_on_you_true_only_for_blocked_ask_user_never_for_escalated_max_rounds`/
    `test_escalation_kind_field`'s own style.
  - `test_status_idle_when_no_run_ever_started`'s full-dict exact-match
    assertion updated to include the new `"terminal": False` key (this is
    the one other place in the suite that pins `inst["team"]`'s exact
    shape; a repo-wide grep confirmed no other test does an exact-dict
    match against it).

## Key decisions / tradeoffs
- **Item 30 timeout bump to 220s (not exactly 175s + the spec's own
  suggested margin)**: chose 220s (45s of margin over the 175s worst-case
  pure sleep) to keep roughly the same per-subprocess-call margin ratio
  the existing 180s/150s (30s margin, 14 calls, ~2.1s/call) budget had,
  scaled up for the settle-window recheck's extra up-to-5 `ps` calls
  (19 calls total, ~2.4s/call at 220s). This is a documented judgment
  call, not a value derived from the spec's own arithmetic (which only
  gave the 175s floor); flagging for the reviewer's own margin check.
- **ACL detection is "warn if uncertain, not silently pass"**: per the
  spec's own risk note, `_parse_acl_other_bits` only suppresses the
  warning when it can positively confirm `other::` is clean from a
  well-formed `getfacl` parse with a `mask::` line; any parse failure
  (missing `other::` line) falls through to `None` (treated as "no ACL"),
  which lets the plain `st_mode` check drive the outcome instead of
  silently trusting a malformed ACL dump.
- **Tests match this repo's established manual-monkeypatch convention,
  not `unittest.mock`**: grepped the existing suite first and found no
  file imports `unittest.mock` — every seam (`_taiga_request`,
  `appmod.taiga_run`'s `subprocess.run`) is monkeypatched by direct
  attribute assignment with `setUp`/`tearDown` save-restore. Followed that
  convention for `_read_getfacl` and `subprocess.run` rather than
  introducing `unittest.mock` as a new pattern.

## Deviations from spec
- **Item 30, `install.sh`**: the healthcheck test command uses
  `http://127.0.0.1/`, not the spec's own literal heredoc example of
  `http://localhost/`. This was already applied to the working tree
  before this session started (per the task's own framing) and is kept
  as-is — the file's own comment documents the hands-on finding that
  `taigaio/taiga-front:latest`'s bundled nginx only listens on
  `0.0.0.0:80` (no IPv6 listener), so BusyBox `wget` resolving
  `"localhost"` to `::1` first gets `Connection refused` even though the
  server is up. This resolves the spec's first "Open questions" item.
- No other deviations. Item 37 and item 38 were implemented exactly per
  the spec's own code blocks/proposed approach.

## Known limitations
- The hands-on acceptance criteria in `docs/spec.md` (fresh Proxmox
  `--with-taiga` install toggling on/off repeatedly; `taiga-configure-
  push.sh` + a live `board_read`/`board_write` push; driving a real run to
  `escalated_max_rounds` and polling `/status`) were **not** exercised in
  this session — no Docker Compose plugin, no `getfacl`/`setfacl`
  binaries, and no live Taiga/team infrastructure are available in this
  sandbox (same constraint prior rounds' own implementation docs already
  note). Everything verifiable without that infrastructure (unit tests,
  the settle-window's stubbed-shell-function retry logic, the ACL-parsing
  logic against synthetic `getfacl` output, `/status`'s `terminal` field
  against real `teams.py` state transitions) was exercised and passes.
- `getfacl`/`setfacl` are not installed in this sandbox, which was
  actually useful for one thing: it means the *unmodified* pre-existing
  `ConfigPermissionsTests` cases (`test_mode_600_prints_no_warning`,
  `test_looser_mode_prints_a_loud_warning_but_does_not_raise`) exercise
  the real, unpatched `_read_getfacl` and its real
  `getfacl`-not-installed fallback path end to end, not just a
  monkeypatched stand-in — confirming that fallback path works against a
  real "binary missing" `FileNotFoundError`, not just a simulated one.

## The `"project": null` investigation (item 38 §3)
Reproduced the spec's own archaeology fresh against the current code
(post this session's changes): grepped `app/`, `scripts/`, and `tests/`
for any literal `"project":` key assignment touching team run state — the
only match is `scripts/taiga_push_spec.py`'s unrelated Taiga API request
body (`_create_userstory`'s `body={"project": project_id, ...}`), a
completely separate subsystem. Read `app/teams.py`'s `_new_state()`
(now around line 2802) and `_persist()` (now around line 2833) in full:
`_new_state()` only ever sets a `"project_name"` key (`None` for a bare
`team-start` CLI run that skipped `team-launch`, by its own comment,
intentionally); `_persist()` writes exactly the `state` dict via
`json.dump`, adding no extra keys. Also ran the full test suite, which
exercises real `_new_state()`/`_persist()` output for both the
`team-launch` and bare `team-start` (`run-cli`) paths — every `run.json`
this produces (spot-checked several in the test run's own captured
output) contains `"project_name"`, never a top-level `"project"` key,
consistent with the code read.

**Finding: resolved-by-explanation, not reproduced.** No code path in the
current codebase ever writes a literal `"project"` key into a run's
`run.json`. This is consistent with the spec's own observation that the
finding is also inconsistent with `latest_run_for_project()`'s strict
`state.get("project_name") != project_name` filter (which would have
skipped, never associated, a run whose `project_name` was `None` — yet
the reported run was correctly associated with its project everywhere
else). Most likely explanation, per the spec's own framing: a
terminology slip in the original report (meaning `project_name`, which
per the filtering above would then have had to be non-null for that run
to have been found at all), or stale data from a schema predating this
round. No fix was made for this — per "Non-goals", not building a fix for
an observation that doesn't reproduce under current code. A hands-on
repro against a real terminal run's actual `run.json` (per the spec's
acceptance criteria) is still the only way to fully close this out if the
observation recurs.

## How to verify locally
```bash
# Full suite (1232 tests; 3 pre-existing failures in
# tests/test_teams_grounding.py are unrelated to this round -- they fail
# identically on the unmodified tree, caused by a stray CLAUDE.md file
# present in this sandbox's working directory that the grounding-discovery
# test's fixed expected-file-list doesn't account for).
python3 -m unittest discover -s tests

# This round's changed areas specifically:
python3 -m unittest tests.test_taiga tests.test_taiga_push \
    tests.test_taiga_up_retry tests.test_team_routes -v

# Frontend (Node, no deps):
node tests/test_singleton_toggle_frontend.js

# Shell syntax/lint:
bash -n scripts/taiga-up.sh && bash -n install.sh
shellcheck scripts/taiga-up.sh

# Settle-window behavior in isolation:
python3 -m unittest tests.test_taiga_up_retry.TaigaUpRetryTests.test_settle_window_recheck_catches_gateway_that_dies_before_settling -v

# ACL-aware permission check in isolation:
python3 -m unittest tests.test_taiga_push.ConfigPermissionsTests -v

# /status terminal field in isolation:
python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests.test_terminal_field -v
```

Hands-on-only acceptance criteria (need a real Proxmox `--with-taiga`
host with Docker Compose, `getfacl`/`setfacl`, and a live team run) are
listed in `docs/spec.md`'s own "Acceptance criteria" sections and were not
exercised in this sandbox — see "Known limitations" above.
