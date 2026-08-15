# Implementation: three Proxmox E2E round-2 bug fixes (backlog items 28, 29, 33)

## Summary
Three independent fixes from `docs/spec.md`: (1) `app/teams.py`'s `rundir`
was created `0o711` but the command inside it actually runs as a different
UID (`RUN_USER` via `sudo -u`), which has no write bit at all on `0o711` —
changed to `0o733`, and the two `tmux new-session` calls that create those
sessions now capture stderr and thread it into each function's existing
failure path instead of silently falling through to a generic "vanished"
message; (2) `app/taiga_board.py`'s `DEFAULT_CONFIG_PATH` used
`os.path.expanduser("~/...")`, which resolves against whichever user's
*process* evaluates it (`SVC_USER` for the board tools) instead of the
`RUN_USER` home the setup script actually writes to — now computed
explicitly from `RUN_USER`, replicated independently (not imported) to
avoid a circular import; (3) `/team/interject`'s 400 error message said
"message must be non-empty..." instead of "text must be non-empty...",
matching the actual field name (`body.get("text")`) the route reads.

## Root cause
- **Fix 1 (item 28)**: `os.chmod(rundir, 0o711)` grants `SVC_USER` (owner)
  full access and everyone else (including `RUN_USER`, the account the
  actual command runs as via `sudo -u`) read+execute but no write bit.
  `RUN_USER`'s own script (`... >out 2>err & echo $! >pid; ...`) fails at
  its very first redirect before it can even background itself, so nothing
  is ever written to `rundir`, and the generic "vanished with no rc"
  fallback fires — indistinguishable from a dozen other causes without the
  added stderr surfacing.
- **Fix 2 (item 29)**: `~` in `os.path.expanduser("~/...")` expands
  relative to the *current process's* user, not a fixed identity. The board
  tools run inside `ai-dev-switchboard.service` as `SVC_USER`, while
  `scripts/taiga-configure-push.sh`'s documented usage is run once by
  `RUN_USER` — two different users, two different resolved paths, so the
  config file the setup script writes is never the one `taiga_board.py`
  reads.
- **Fix 3 (item 33)**: pure copy/paste-era wording mismatch — the route
  always read the correct field (`body.get("text")`), only the error
  message named the wrong one.

## Changes by file
- `app/teams.py`
  - `agent_run()` (the function containing the `rundir` setup used by
    `_run_headless_session()`, ~line 1095): `os.chmod(rundir, 0o711)` →
    `os.chmod(rundir, 0o733)`.
  - `agent_run()`'s `tmux new-session` call (~line 1139): added
    `capture_output=True, text=True`; when `returncode != 0`, now returns
    the existing `_result(ok=False, ...)` failure shape with
    `error=f"failed to start headless session: {tmux_result.stderr.strip()}"`
    instead of silently proceeding to `_run_headless_session()`, which
    would previously always hit the generic "vanished" fallback for this
    failure mode.
  - `_run_run_user_command()` (~line 3462): same `0o711` → `0o733` change
    on its own `rundir`.
  - `_run_run_user_command()`'s `tmux new-session` call (~line 3473): same
    `capture_output=True, text=True` addition; on non-zero returncode,
    returns the function's existing `{"ok": False, "rc": None, "stdout":
    "", "stderr": "", "timed_out": False, "error": ...}` shape with
    `error=f"failed to start command: {tmux_result.stderr.strip()}"`.
- `app/taiga_board.py`
  - Replaced `DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/...")`
    with an explicit `RUN_USER = os.environ.get("RUN_USER", "dev")`
    (byte-for-byte matching `app/app.py:69`'s own resolution, replicated
    independently — not imported, to avoid the `app → teams →
    taiga_board → app` circular import the spec identifies) followed by
    `DEFAULT_CONFIG_PATH = f"/home/{RUN_USER}/.config/ai-dev-switchboard/taiga-push.env"`.
- `app/app.py`
  - `/team/interject` route's 400 error string: `"message must be
    non-empty and at most ..."` → `"text must be non-empty and at most
    ..."`.

## Key decisions / tradeoffs
- Kept both `tmux new-session` failure-surfacing additions inside each
  function's *existing* return shape (`_result(...)` for
  `agent_run()`/`_run_headless_session()`, the plain dict for
  `_run_run_user_command()`) rather than introducing any new error field or
  return contract, per the spec's explicit instruction.
- `_run_run_user_command()`'s new failure branch mirrors the existing
  `except OSError` branch immediately above it (same dict shape, same
  `"failed to start command: ..."` message prefix) for consistency within
  that one function, rather than reusing `agent_run()`'s wording.
- Left `scripts/taiga_push_spec.py`'s own `DEFAULT_CONFIG_PATH` untouched
  (spec's explicit non-goal — it already runs as `RUN_USER`, so its
  `~`-relative resolution is already correct).

## Deviations from spec
None. All three fixes match the spec's exact before/after code and line
locations. (One line-number note: the spec describes the item 28 `rundir`
`chmod` at `teams.py:1095` as being "inside `_run_headless_session()`" — in
the actual code that chmod is inside `agent_run()`, the function that
prepares `rundir` before calling `_run_headless_session()`. The line number
given in the spec is exact and unambiguous, so the fix was applied there
regardless of this minor function-name mislabeling in the spec's prose.)

## Known limitations
- Fix 1's core scenario (a real `sudo -u RUN_USER` process attempting to
  write into a directory owned by a different `SVC_USER`) cannot be
  exercised by this repo's test suite, which runs single-user with no real
  UID boundary — this was called out as a known constraint in the task
  itself. Verified instead via: `python3 -m py_compile` on all three
  touched files (passes), the full existing test suite (1198 tests, `OK`,
  no regressions), and manual review confirming `0o733` (owner rwx,
  group -wx, other -wx) grants write+execute to any UID other than the
  directory owner, which resolves the exact permission wall described.
- The new stderr-surfacing branches in `agent_run()` and
  `_run_run_user_command()` are also not covered by a new automated test:
  reproducing a real `tmux new-session ... bash -l <script>` non-zero exit
  in this sandbox would require either a broken `tmux` binary or an actual
  permission failure (the same missing UID boundary as above) to trigger
  it realistically; no existing test in `tests/test_teams_headless.py`
  mocks `subprocess.run` for the `tmux new-session` call itself (existing
  tests mock at a different layer — `pid_path`/`rc_path` file contents —
  to drive `_run_headless_session()`'s state machine). Confirmed by
  reading `tests/test_teams_headless.py` in full for any existing
  `subprocess.run`-mocking pattern for this specific call site; none
  exists to extend, and introducing a new mocking seam for one two-line
  branch was judged out of scope for this narrow fix cycle.

## How to verify locally
1. Compile check: `python3 -m py_compile app/teams.py app/taiga_board.py app/app.py`
2. Fix 2's acceptance criterion, verbatim from the spec:
   `RUN_USER=dev python3 -c "import sys; sys.path.insert(0, 'app'); import taiga_board; print(taiga_board.DEFAULT_CONFIG_PATH)"`
   → prints `/home/dev/.config/ai-dev-switchboard/taiga-push.env`.
3. Full test suite: `python3 -m unittest discover -s tests` → `Ran 1198
   tests ... OK` (no regressions).
4. Focused re-run of the two files most related to these fixes:
   `python3 -m unittest tests.test_teams_headless tests.test_team_routes`
   → `Ran 217 tests ... OK`.
5. Manual code read confirms no existing test asserted on the old `0o711`
   mode or the old `"message must be non-empty..."` error string, so no
   test updates were needed for either.
