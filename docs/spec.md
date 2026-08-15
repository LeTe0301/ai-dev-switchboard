# Spec: Backend hardening — `set_env()` sed-injection fix + team `run_id` path-traversal validation (backlog #10 + #11(b))

## Summary
Fix two independently-discovered, already-reproduced defects in shared low-level helpers — `install.sh`'s `set_env()` upsert (backlog item 10) and `app/teams.py`'s unvalidated `run_id` path construction (backlog item 11, part (b) only; part (a), the stale-transcript-entry defect, already shipped in 6f part 1b) — bundled into one build cycle because both are small, self-contained, backend-only validation gaps in unrelated helpers with no UI surface, found by the reviewer as non-blocking should-fix items during 6d part 2b and 6f part 1 respectively and deliberately deferred rather than fixed in-cycle at the time.

## Goals
- `set_env()` in `install.sh` must safely upsert any operator-supplied value — including one containing a literal `|`, `&`, or `\` — without aborting the whole `install.sh` run and without corrupting the written config line.
- `run_id` values reaching `app/teams.py`'s lead-run filesystem path helpers (`_run_dir()` and everything built on it: `_run_json_path`, `_transcript_path`, `_inbox_path`, `_inbox_resolved_path`, `_agent_log_path`) must be validated against the exact shape `_run_id()` actually generates before ever being joined into a path, so a malformed/traversal `run_id` (e.g. `../../outside/evilrun`) is rejected before any file outside `_leads_root()` can be opened.
- Both fixes land once in the shared helper/choke point, not per-callsite, so every existing and future caller benefits automatically.

## Non-goals
- Not rewriting `set_env()`'s overall mechanism (e.g. a full switch to a python3/awk line-rewriter) — the sed-escaping fix is smaller, keeps the existing signature and behavior for every current caller identical, and is the more surgical change to a helper used by every `--with-*` block. Not adopted here, but noted as a viable alternative if a future defect in this helper class recurs.
- Not changing `_run_id()`'s own generation format (`f"{int(time.time())}-{secrets.token_hex(6)}"`) — validation is written to match the current format exactly; if that format ever changes, the validation regex must change with it (called out as a maintenance note, not fixed automatically).
- Not touching `_team_session_run_id()` (the tmux-option-based session-ownership stamp) — unrelated subsystem, not a filesystem path.
- Not re-validating `project_name` (already covered by `app/app.py`'s existing `NAME_RE`) or touching the `state.get("project_name") != project_name` ownership check in `load_state_for_project()`/the inline POST-resolve check — those are correct today and out of scope.
- Not addressing backlog items 9, 12, 13, or the #7/#8 scope-gated items — separate build cycles (see product-manager's routing summary).

## Background / current state
**`set_env()` (`install.sh:112-119`):**
```bash
set_env() {  # set_env <file> <KEY> <value> — idempotent upsert
    local file="$1" key="$2" val="$3"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}
```
`$val` is interpolated unescaped into the replacement side of a sed `s///` expression using `|` as the delimiter. Reproduced live (per backlog item 10): a value containing a literal `|` breaks the sed expression (`sed: unknown option to 's'`, rc=1) and aborts the whole `install.sh` run on a re-run (the first write, via `>>`, never goes through sed — only a re-run upsert does) — violating the "skip only this block, never abort the whole run" discipline every other optional block follows. A value containing a literal `&` is silently corrupted (sed's replacement-side backreference for the whole match). `key` itself is always a hardcoded literal at every call site in `install.sh` today (never operator-controlled), so only `$val` needs escaping.

**`run_id` path validation (`app/teams.py`):**
```python
def _leads_root() -> str:
    return os.path.join(TEAM_STATE_DIR, "leads")

def _run_dir(run_id: str) -> str:
    return os.path.join(_leads_root(), run_id)          # <- no validation

def _run_json_path(run_id: str) -> str: ...              # all built on _run_dir()
def _transcript_path(run_id: str) -> str: ...
def _inbox_path(run_id: str) -> str: ...
def _inbox_resolved_path(run_id: str) -> str: ...
def _agent_log_path(run_id: str, agent: str) -> str: ...

def _load_state(run_id: str) -> dict:
    with open(_run_json_path(run_id)) as f:
        return json.load(f)
```
Three routes in `app/app.py` accept a client-supplied `run_id` and flow it, unvalidated, into these helpers:
- `GET /projects/<name>/team/events` and `GET /projects/<name>/team/inbox` — both go through the shared `_team_events_run_and_ownership()` (`app/app.py:4059`), which reads `run_id = (query.get("run_id") or [None])[0]` (line 4073) and, if present, calls `teams.load_state_for_project(run_id, name)`, which calls `_load_state(run_id)` inside a `try/except (OSError, ValueError): return None`.
- `POST /projects/<name>/team/resolve` (`app/app.py:4326`) reads `run_id = (body.get("run_id") or "").strip() or None` and, if present, calls `teams._load_state(run_id)` directly (line 4329) inside its own `try/except (OSError, ValueError): return self._json({"error": "no run found for this project"}, 400)`.

Reproduced directly (per backlog item 11(b)): a `run_id` of `"../../outside/evilrun"` successfully reads a planted file outside `_leads_root()`. Real project data stays gated by the existing `state.get("project_name") != project_name` check that runs *after* the file is already opened and parsed — the traversal itself, not just the ownership check, is the gap. Judged narrow (not blocking) at the time because exploiting this meaningfully needs pre-existing filesystem write access elsewhere, but it's real and cheap to close.

**Why this is a clean, root-cause fix location, not a per-callsite patch:** both GET-route call sites and the POST-resolve call site *already* catch `(OSError, ValueError)` around their `_load_state()`/`load_state_for_project()` calls and already turn that into the correct 404/400. Making `_run_dir()` itself raise `ValueError` on an invalid `run_id` — before ever calling `os.path.join()` — means every existing caller's exception handling already does the right thing with zero changes needed at the route level. This also automatically covers every other path helper built on `_run_dir()` (agent log paths, inbox paths, etc.), not just the three routes named above.

## Proposed approach

**1. `set_env()` — escape the sed replacement side.**
```bash
set_env() {  # set_env <file> <KEY> <value> — idempotent upsert
    local file="$1" key="$2" val="$3" val_escaped
    val_escaped=$(printf '%s' "$val" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val_escaped}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}
```
Escaping order matters: backslash first (so the escaping added for `&`/`|` isn't itself re-escaped), then `&`, then the `|` delimiter. The `>>` append path (first-write case) is untouched — it never goes through sed, so it already handles any character correctly; only the sed-upsert path needs the fix.

**2. `run_id` validation — one regex, one choke point.**
Add near `_run_id()` (`app/teams.py:192`):
```python
# Matches _run_id()'s own generation format exactly (int(time.time()) +
# "-" + secrets.token_hex(6)) -- any run_id not shaped like this cannot be
# a real run this process ever created, so rejecting it outright is both
# safe (never a false negative against a real run_id) and correct (never
# lets a client-supplied run_id reach a path-join unvalidated). If
# _run_id()'s own format ever changes, this must change with it.
_RUN_ID_RE = re.compile(r"^[0-9]+-[0-9a-f]{12}$")
```
Change `_run_dir()` to validate before joining:
```python
def _run_dir(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"invalid run_id: {run_id!r}")
    return os.path.join(_leads_root(), run_id)
```
No other code changes needed — `_run_json_path`/`_transcript_path`/`_inbox_path`/`_inbox_resolved_path`/`_agent_log_path` all call `_run_dir()` and will propagate the `ValueError`; `_load_state()` propagates it further; `load_state_for_project()`'s existing `except (OSError, ValueError): return None` and the POST-resolve route's existing `except (OSError, ValueError): return self._json({"error": "no run found for this project"}, 400)` already turn that into the correct response. Internal callers (`launch_team`, `_persist`, `stop_team`, CLI `team-status`/`team-stop`/`team-reap`, etc.) always pass a `run_id` either freshly generated by `_run_id()` or read back from a `run.json` this process itself wrote, so they will always match `_RUN_ID_RE` and see no behavior change.

## Affected areas
- `install.sh` — `set_env()` (~line 112-119). No other function changes; `get_env()` is read-only and already safe.
- `app/teams.py` — new `_RUN_ID_RE` constant near `_run_id()` (~line 192); `_run_dir()` (~line 2452) gains the validation guard. `import re` — confirm already imported at top of file (used elsewhere in this file already for other patterns; if not, add it).
- `app/app.py` — no code changes expected (the fix is designed so existing exception handling at `_team_events_run_and_ownership()` (~line 4059-4078) and the POST `/team/resolve` handler (~line 4323-4338) already does the right thing) — reviewer should confirm this holds rather than assume it.
- Tests:
  - New `tests/test_install_set_env.py` (or a new test class appended to an existing install.sh-block test file) — extracts `set_env`/`get_env` verbatim from `install.sh` using the same `_extract_between()`-style harness `tests/test_install_ollama.py:56,135-168` already establishes (no pty/`prompt()` needed here — `set_env`/`get_env` never read from a terminal — so this can be a plain `subprocess.run(["bash", "-c", script])`, simpler than the ollama harness).
  - New test coverage in `tests/test_team_routes.py` (alongside the existing `TeamEventsEndpointTests`/`TeamInboxEndpointTests`/`TeamResolveEndpointTests` classes) for a path-traversal `run_id` on all three routes, plus a direct unit test of `teams._run_dir()`/`_RUN_ID_RE` in `tests/test_teams_lifecycle.py` (or wherever that module's other `teams.py`-internals-only tests already live).

## Edge cases
- **`set_env()`:**
  - Value containing `|`, `&`, `\`, or a combination/repetition of all three in one value — each must upsert correctly and read back byte-for-byte via `get_env()`.
  - Value that is empty string — must still upsert to `KEY=` cleanly (existing behavior, must not regress).
  - Existing plain-value callers (hostnames, paths, tokens with no special characters) — must behave identically to before (regression check, not just new-case coverage).
  - The **first write** (key not yet present, `>>` append path) — confirm it's genuinely untouched by this fix and already handles all these same characters correctly (it does today, since it never goes through sed).
- **`run_id` validation:**
  - `run_id=""` (empty string) on the GET routes — already short-circuits to "no explicit override, use latest run" *before* reaching `_run_dir()` (existing `if run_id:` falsy-check), so this must continue to behave as "no run_id supplied," not as a validation failure. Confirm this path is unaffected.
  - A syntactically-plausible but non-existent `run_id` (matches `_RUN_ID_RE` but no such run was ever created) — must continue to 404/400 exactly as it does today (via `FileNotFoundError`, an `OSError` subclass, from the failed `open()`), not conflate with the new invalid-format case.
  - URL-encoded traversal (`%2e%2e%2f`) — `urllib.parse.parse_qs` already decodes percent-encoding before the route ever sees the string, so this collapses to the same literal `../` case the regex already rejects; add a test making this explicit rather than assuming it.
  - `run_id` with uppercase hex digits, or a hex portion of the wrong length — must be rejected (never generated by `_run_id()`, so a mismatch here is always either a bug or an attacker).
  - `run_id` containing a NUL byte — Python's `re.match` handles NUL fine as an ordinary rejected character (not in the allowed class); no special-case needed, but worth one explicit test given path/filesystem NUL-byte handling is a classic edge case.

## Acceptance criteria
- [ ] Given `set_env()` upserting a value containing a literal `|` on a re-run, when `install.sh`'s block runs a second time, then it exits 0 (does not abort) and the written config line's value is exactly the original value, `|` included.
- [ ] Given `set_env()` upserting a value containing a literal `&`, when read back via `get_env()`, then the value is byte-for-byte identical to the original (no backreference-style corruption).
- [ ] Given `set_env()` upserting a value containing a literal `\`, when read back via `get_env()`, then the value is byte-for-byte identical to the original.
- [ ] Given `set_env()` upserting a plain value with no special characters (e.g. a hostname), when read back, then behavior is unchanged from before this fix (regression).
- [ ] Given a `GET /projects/<name>/team/events?run_id=../../outside/evilrun` request (with a real file planted at a path that traversal would reach), when requested, then the response is 404 with `{"error": "unknown run_id for this project"}` and the planted file is never opened (verify via a monkeypatched `open`/`os.path.join` spy, or by asserting the file's access time is unchanged).
- [ ] Same for `GET /projects/<name>/team/inbox?run_id=../../outside/evilrun` — 404, planted file never opened.
- [ ] Given a `POST /projects/<name>/team/resolve` with `run_id: "../../outside/evilrun"` in the body, when requested, then the response is 400 with `{"error": "no run found for this project"}` and no state is mutated.
- [ ] Given a real, legitimately-issued `run_id` (from an actual `/team/start`), when used on all three routes, then behavior is identical to before this fix (regression — the existing `TeamEventsEndpointTests`/`TeamInboxEndpointTests`/`TeamResolveEndpointTests` suites must all still pass unmodified).
- [ ] Given a malformed-but-non-traversal `run_id` (e.g. `"not-a-real-run"`, or one with uppercase hex), when used on any of the three routes, then it 404s/400s the same clean way a syntactically-valid-but-nonexistent `run_id` already does today (no new error shape, no 500).
- [ ] Full existing test suite (`python3 -m unittest discover -s tests`) passes with no regressions.

## Open questions
None blocking — both fixes are narrow, already-reproduced, already-scoped by the reviewer who found them, and involve no product/architecture decision. Two non-blocking notes carried forward for the developer:
- The `set_env()` fix is written as a sed-escaping patch rather than a switch to a python3/awk rewrite (this project's precedent for "don't shell user-controlled text through sed's pattern language" from 6d part 2b's `/models` response parsing) — assumption: the smaller, behavior-preserving patch is preferable here since `set_env()` is a widely-shared helper and a mechanism switch has a larger blast radius across every `--with-*` block for a marginal robustness gain. Flag to the user only if the developer discovers the escaping approach doesn't hold up under testing.
- `_RUN_ID_RE`'s exact-format match is deliberately strict (tied to `_run_id()`'s current generation shape) rather than a looser "no `/`, no `..`" character-class check — assumption: since `run_id` is never user-chosen (always server-generated), tying validation to the exact known-good shape is strictly safer and no more brittle in practice, since both `_run_id()` and `_RUN_ID_RE` live in the same file and would be changed together.

## Risk / rollback notes
Both changes are small, additive guards in shared helpers with clear existing regression coverage (`test_install_ollama.py` and friends exercise `set_env()` indirectly today; `test_team_routes.py`'s three endpoint test classes exercise the routes touched here). Rollback is a one-commit revert in either case — neither change touches persisted data formats, on-disk file layout, or any public API shape (error messages/status codes for the already-existing "not found" cases are unchanged; only the *newly rejected* input shapes get responses, and they get the same shape "not found" responses that already exist for other invalid inputs). Main risk is the `_RUN_ID_RE` regex being stricter than intended and rejecting a legitimate `run_id` — mitigated by explicit regression acceptance criteria above requiring the full existing team-routes test suite to keep passing unmodified.
