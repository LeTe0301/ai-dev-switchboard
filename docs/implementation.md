# Implementation: Backend hardening — `set_env()` sed-injection fix + team `run_id` path-traversal validation (backlog #10 + #11(b))

## Summary
Fixed two independently-reported, already-reproduced backend defects: `install.sh`'s `set_env()` shelled an operator-supplied value unescaped through a sed `s///` expression (a literal `|` aborted a re-run, a literal `&` silently corrupted the write); and a client-supplied `run_id` on three `team/*` web routes reached a filesystem path-join with no format validation, allowing path traversal (`run_id=../../outside/evilrun`). Both are fixed at a single choke point each; the `run_id` fix lands in `app/app.py` (a deliberate, documented deviation from the spec's proposed `app/teams.py:_run_dir()` location — see "Deviations from spec").

## Root cause
- **`set_env()`**: `$val` was interpolated directly into the replacement side of a sed `s|pattern|replacement|` expression. Sed treats `|` (the chosen delimiter) and `&` (whole-match backreference) specially on that side; an unescaped operator-supplied value containing either broke the expression (`|`, aborting `install.sh` on the next re-run since the first write goes through a plain `printf >>`, only a re-run touches sed) or silently mangled the written value (`&`).
- **`run_id` path traversal**: `app/teams.py`'s `_run_dir(run_id)` joined `run_id` into a path with `os.path.join()` and no validation. A client-supplied `run_id` reaches this via `GET .../team/events`, `GET .../team/inbox` (both through `_team_events_run_and_ownership()`), and `POST .../team/resolve` in `app/app.py`. A value like `"../../outside/evilrun"` let `open()` read a file outside `_leads_root()`; the existing `state.get("project_name") != project_name` ownership check runs only *after* that file is already opened and parsed.

## Changes by file
- `install.sh` (`set_env()`, ~line 112) — before the sed-upsert branch, `$val` is now escaped via `printf '%s' "$val" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g'` (backslash first, then `&`, then the `|` delimiter — order matters so earlier escaping isn't itself re-escaped) and the escaped value is used in the `s|...|...|` replacement. The `>>` first-write append path is untouched (never went through sed, already correct for any character). `get_env()` unchanged.
- `app/teams.py`:
  - Added `_RUN_ID_RE = re.compile(r"^[0-9]+-[0-9a-f]{12}$")` next to `_run_id()` (~line 192), matching `_run_id()`'s own generation shape exactly.
  - `_run_dir()` (~line 2452) is **unchanged in behavior** — still an unvalidated `os.path.join()`. A comment explains why validation was deliberately NOT added here (see "Deviations from spec").
- `app/app.py`:
  - `_team_events_run_and_ownership()` (~line 4059, shared by `GET .../team/events` and `GET .../team/inbox`): when an explicit `run_id` query param is present, it's checked against `teams._RUN_ID_RE` before calling `teams.load_state_for_project()`; a mismatch returns the same `({"error": "unknown run_id for this project"}, 404)` the existing "no such run" path already returns.
  - `POST .../team/resolve` handler (~line 4323): when an explicit `run_id` is present in the body, it's checked against `teams._RUN_ID_RE` before calling `teams._load_state()`; a mismatch returns the same `{"error": "no run found for this project"}` 400 the existing `except (OSError, ValueError)` clause already returns for a nonexistent run.
- `tests/test_install_set_env.py` (new) — extracts `set_env`/`get_env` verbatim from `install.sh` (same `_extract_between()` technique as `tests/test_install_ollama.py`) and runs them via plain `subprocess.run` (no pty needed, neither helper reads a terminal). 8 tests: `|`/`&`/`\`/combination round-trip, empty value, plain-value regression, first-write (`>>`) path untouched, repeated re-run with a value change after a `|`-bearing one.
- `tests/test_teams_lifecycle.py` — replaced the originally-written `RunDirValidationTests` (which asserted `_run_dir()` itself raises `ValueError`) with `RunIdRegexValidationTests`, which tests `teams._RUN_ID_RE` directly (the actual validation primitive used at the app.py intake points) plus one explicit regression test that `_run_dir()` itself still joins any string unvalidated. 9 tests.
- `tests/test_team_routes.py`:
  - New module-level helpers `TRAVERSAL_RUN_ID`, `_plant_traversal_target()`, `_get_forbidding_open_of()` (the latter mirrors `tests/test_teams_grounding.py`'s `GroundingReadOnlyRuntimeTests` monkeypatched-`builtins.open` technique) to prove a planted file outside `_leads_root()` is never opened, not just that the response status/shape looks right.
  - `TeamEventsEndpointTests`: +4 tests (path-traversal 404 + never-opened, URL-encoded-traversal 404, malformed-non-traversal 404 incl. uppercase-hex, NUL-byte 404).
  - `TeamInboxEndpointTests`: +2 tests (path-traversal 404 + never-opened, malformed-non-traversal 404).
  - `TeamResolveEndpointTests`: +2 tests (path-traversal 400 + never-opened + no thread started, malformed-non-traversal 400).
  - `_scope_run_ids()`/`_RUN_ID_SCOPE` (test-only process-scoping helper used by this file's real-HTTP test harness to avoid concurrent test processes colliding on tmux session names) reworked so the scoped `run_id` it produces stays exactly `_RUN_ID_RE`-shaped — see "Deviations from spec".

## Key decisions / tradeoffs
- **`set_env()`**: kept the sed-escaping patch (spec's stated preference) rather than switching to a python3/awk rewrite — smaller blast radius across every `--with-*` block, identical signature/behavior for the common case.
- **`_get_forbidding_open_of()`** (test helper): monkeypatches `builtins.open` globally for the duration of one synchronous HTTP request rather than trying to assert on file-access-time (unreliable under `relatime`/`noatime` mounts) — this mirrors an existing, working precedent in this repo (`tests/test_teams_grounding.py`) rather than inventing a new technique.

## Deviations from spec
**The `run_id` validation was moved from `app/teams.py:_run_dir()` (as the spec's "Proposed approach" specified) to the two `run_id`-intake points in `app/app.py`.** This was discovered necessary, not a style preference:

`_run_dir()` is a shared internal path helper used by every caller in `teams.py`, not just the three externally-reachable web routes — including the CLI (`team-status`/`team-stop`/`team-reap`), `sweep_dead_teams()`, and a large number of pre-existing pure-unit tests across `tests/test_teams_lifecycle.py`, `tests/test_teams_headless.py`, and `tests/test_teams_cancel.py` that construct synthetic `run_id` values (`"r1"`, `"r2"`, arbitrary CLI-typed strings, etc.) that were never intended to match `_run_id()`'s exact generation shape. The spec's own assumption — "Internal callers... always pass a `run_id` either freshly generated by `_run_id()` or read back from a `run.json` this process itself wrote, so they will always match `_RUN_ID_RE`" — turned out to be false in practice:

1. Validating inside `_run_dir()` broke 42 pre-existing tests (confirmed by running the change and observing the failures before reverting) that call `_persist()`/`_load_state()`/`sweep_dead_teams()` directly with synthetic run_ids.
2. It also changed the CLI's error message for an unknown `run_id` (`team-stop no-such-run-id`) from `"no such run_id"` to `"invalid run_id: ..."` — a real, unwanted UX regression for a surface (the CLI) that is locally-trusted and was never part of the reported vulnerability (only the three web routes were named as attacker-reachable).
3. A recently-landed, unrelated test-isolation mechanism (`tests/test_team_routes.py`'s `_scope_run_ids()`, from a prior hardening cycle for docs/BACKLOG.md item 9) monkeypatches `teams._run_id()` to prefix every test-generated `run_id` with this process's own pid, so that concurrent test processes' real-tmux sessions never collide — those "real, legitimately-issued" test run_ids also didn't match `_RUN_ID_RE`'s exact shape, which would have broken the spec's own acceptance criterion ("a real, legitimately-issued `run_id`... behavior is identical to before this fix").

Moving the check to `app/app.py` — right where a client-supplied `run_id` is first read from the query string / POST body, before any `teams.*` call that could reach a path-join — fully satisfies the actual security goal stated in the spec ("`run_id` values... must be validated... before ever being joined into a path, so a malformed/traversal `run_id`... is rejected before any file outside `_leads_root()` can be opened") without touching the shared internal helper or the CLI. The response shapes/status codes are byte-identical to what the spec's own choke-point design would have produced (same 404/400, same error strings), since both intake points return the exact same "unknown run_id" / "no run found" response the existing not-found path already used.

To keep `test_team_routes.py`'s real-HTTP tests passing under this app.py-level check (its `_scope_run_ids()` wrapper needed to keep producing `_RUN_ID_RE`-shaped ids), its scoping technique was changed from a `"p<pid>-"` hyphen-delimited prefix segment to a fixed-width (8-digit), all-digit prefix concatenated directly onto the real epoch digits — this keeps the overall shape `^[0-9]+-[0-9a-f]{12}$` intact while remaining an unambiguous `startswith` prefix for the existing tmux-session cleanup/leftover-detection logic (fixed-width means no two distinct pids' scope tokens can accidentally prefix-match one another). `test_teams_headless.py` and `test_teams_cancel.py` were confirmed (via `grep` for `ThreadingHTTPServer`/`urllib.request`) to never drive a `run_id` through a real HTTP route, so their copies of the same scoping helper were left untouched — they're unaffected by this change and didn't need it.

`_RUN_ID_RE` itself is unchanged from the spec's proposed pattern and still lives in `app/teams.py` next to `_run_id()`, as the spec required, and `app/app.py` references it directly (`teams._RUN_ID_RE`) — this repo's existing convention (`app.py` already calls `teams._load_state()`, `teams._persist()`, `teams._inbox_path()` etc. directly) rather than adding a new wrapper function in `teams.py`.

## Known limitations
- `_RUN_ID_RE` is tied to `_run_id()`'s current generation shape (as the spec intended); if that shape ever changes, both the regex and the two `app.py` intake checks must be updated together. This is now slightly more exposed than the spec's single-choke-point design (two call sites in `app.py` instead of one in `teams.py`), though both are directly adjacent to the existing `run_id` intake/ownership-check code they extend, and both are covered by explicit regression tests.
- `_run_dir()` and every other internal `teams.py` caller remain unvalidated by design (see "Deviations from spec") — this is intentional and matches this cycle's actual attack surface (the three web routes), not an oversight.

## How to verify locally
```
# set_env() fix
python3 -m unittest tests.test_install_set_env -v
python3 -m unittest tests.test_install_ollama -v   # regression: set_env() used indirectly

# run_id validation fix
python3 -m unittest tests.test_teams_lifecycle.RunIdRegexValidationTests -v
python3 -m unittest tests.test_team_routes.TeamEventsEndpointTests tests.test_team_routes.TeamInboxEndpointTests tests.test_team_routes.TeamResolveEndpointTests -v

# full regression sweep
python3 -m unittest discover -s tests -v          # 790 tests, all pass (765 baseline + 25 new)
node tests/test_team_frontend.js && node tests/test_deploy_frontend.js && node tests/test_singleton_toggle_frontend.js && node tests/test_upload_frontend.js   # 84/84, unaffected (no Node-facing code touched)

bash -n install.sh                                  # syntax check
python3 -m py_compile app/teams.py app/app.py        # syntax check
```
