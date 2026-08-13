# Test & Review: Local backlog tracker (Taiga) — part 1b: push a spec into Taiga

## Scope
Second pass, following the first pass's "Blocked" verdict (Defect 1: a
blank/malformed `TAIGA_URL` bypassed the clean-error-message contract via an
uncaught `ValueError` from `urllib.request.Request(...)`). This pass:
(1) hands-on re-verifies the developer's fix against the original repro plus
several new malformation variants, specifically checking whether the fix is
*generic* (catches the exception class regardless of cause) or a narrow
patch of only the one exact string first found; (2) re-confirms the
non-blocking follow-up (unmapped HTTP status during project lookup) the
developer also fixed alongside it; (3) since this is the first time this
cycle's testing pass has come back clean, performs the full independent
review pass (spec/AC traceability, correctness, security, simplicity) for
the complete feature, not just the fix — including re-checking (not just
assuming unaffected) the credential-handling/permissions/injection-safety
findings from the first pass against the current code, since parts of the
file changed.

Full file contents read directly (`scripts/taiga_push_spec.py`,
`scripts/taiga-configure-push.sh`, `tests/test_taiga_push.py`,
`docs/spec.md`, `docs/implementation.md`). No live Taiga instance is
reachable in this environment (same confirmed gap as pass 1 and the
developer's own account — `docker compose version` still not available);
this pass relies on pass 1's own from-scratch fake-HTTP-server verification
(already independently built and validated this session/project, per
"proportional verification depth" — not rebuilt here since none of the code
paths it covered were touched by this fix cycle) plus fresh direct
function-level and real-subprocess checks targeted at what *did* change.

## Re-verification of Defect 1's fix

**Root cause recap**: `urllib.request.Request(url, ...)` was constructed
*before* `_taiga_request`'s `try:` block, so a bare `ValueError` from
`Request.__init__`'s own URL parsing (blank/scheme-less/unparseable
`base_url`) propagated past every `TaigaPushError` handler as a raw
traceback.

**Fix as landed** (`scripts/taiga_push_spec.py:94-112`): `req =
urllib.request.Request(...)` is now the first statement inside the existing
`try:` block, so any `ValueError` it raises is caught by the same `except
(urllib.error.URLError, OSError, ValueError) as e:` handler that already
converts connection failures into `TaigaConnectionError`. This is a
*generic* fix (it catches the exception class, not a specific string) —
confirmed below by testing several malformation shapes beyond the original
repro, all handled by the same one-line code change.

**Hands-on re-run, original repro** (blank `TAIGA_URL`, real subprocess,
fresh config file, matching pass 1's exact repro):
```
$ python3 scripts/taiga_push_spec.py --config /tmp/taiga-repro-scratch/cfg.env --spec /tmp/taiga-repro-scratch/spec.md
error: Could not reach Taiga at  — make sure it's toggled on in the ai-dev-switchboard web UI, or check TAIGA_URL in /tmp/taiga-repro-scratch/cfg.env.
exit=1
```
Clean message, no traceback, exit 1 — matches the designed contract.

**Additional malformation variants tried** (real subprocess each time, same
harness), specifically hunting for a shape that might slip past a narrow
fix:

| `TAIGA_URL` value | Result |
|---|---|
| `` (blank) | clean message, exit 1 |
| `not-a-url-at-all` (no scheme) | clean message, exit 1 |
| `   ` (whitespace-only) | clean message, exit 1 |
| `://missing-scheme.example.com` | clean message, exit 1 |
| `http://127.0.0.1:999999` (invalid port) | clean message, exit 1 |
| `ht!tp://bad chars in host/path` | clean message, exit 1 |
| `http://` (scheme only, no host) | clean message, exit 1 |
| `ftp://127.0.0.1:9000` (unsupported scheme) | clean message, exit 1 |

All eight produced the single-line `error: Could not reach Taiga at ...`
message with no traceback and exit code 1 — no variant slipped past the
fix. This confirms the fix closes the exception-class gap generically
(everything `Request.__init__`/`urlopen` can raise as `ValueError`/
`URLError`/`OSError`), not just the one exact string (`""` or
`"not-a-url-at-all"`) originally found.

**Revert-and-watch-it-fail check** (verifying the two new regression tests
actually exercise this fix, not just assert something coincidentally true):
temporarily moved `Request(...)` back outside the `try:` block (reproducing
the original bug byte-for-byte) and re-ran exactly
`test_blank_taiga_url_exits_nonzero_with_unreachable_message_no_traceback`
and
`test_malformed_taiga_url_exits_nonzero_with_unreachable_message_no_traceback`.
Both failed with the exact original traceback shape (`ValueError: unknown
url type: '/api/v1/auth'` and `'not-a-url-at-all/api/v1/auth'`,
respectively, raised from `Request.__init__` inside `_taiga_request`).
Restored the fix (`diff` against the pre-edit backup confirmed byte-
identical restoration); full suite re-run clean (122/122). This confirms
both new tests are real regression tests for this exact bug, not
tautologies.

**Follow-up fix re-verification** (unmapped HTTP status during project
lookup, e.g. `500`): direct call to `_lookup_project_or_raise` with a
monkeypatched `_taiga_request` raising `TaigaHTTPError(500, "... body with
a SECRET_PASSWORD_LEAK ...")` → caught cleanly as `TaigaPushError("Taiga
rejected the project lookup (HTTP 500).")`, confirmed the raw response body
is never included in the message (`"SECRET_PASSWORD_LEAK" in str(e)` →
`False`). Matches the developer's account; no traceback, no leaked data,
degrades safely for any status not already mapped to a specific message.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `taiga-configure-push.sh` creates config at mode 600, `--verify` succeeds | Automated (`ConfigureScriptTests`, real subprocess) + pass 1's own real fake-HTTP-server run (unchanged code path, not re-run this pass) | pass | `test_writes_mode_600_config_and_propagates_verify_failure` passes; pass 1's fake-server run independently confirmed a real `--verify` success end-to-end |
| 2 | Valid config + spec → exactly one userstory, correct subject/description, ref+URL printed, exit 0 | Automated (`test_normal_run_creates_one_userstory_and_prints_ref_and_url`) + pass 1's fake-server run | pass | Ran this session: `python3 -m unittest tests.test_taiga_push -v` → 34/34 pass |
| 3 | `--dry-run` sends no POST, prints preview, exit 0 | Automated (`test_dry_run_sends_no_post_and_prints_preview`) | pass | Ran this session, part of the 34/34 |
| 4 | `--verify` only auth+lookup, no POST, exit 0 | Automated (`test_verify_only_authenticates_and_looks_up_project`) | pass | Ran this session |
| 5 | Taiga unreachable → clean message, no traceback, incl. wrong `TAIGA_URL` | Automated (3 tests) + manual real-subprocess re-run this session, 8 URL variants (see table above) | **pass (was fail — Defect 1 now fixed)** | See "Re-verification of Defect 1's fix" above |
| 6 | Bad credentials → exact rejection message, no userstory created | Automated (`test_bad_credentials_exits_nonzero_with_exact_message_and_creates_nothing`) + pass 1's real-password fake-server run | pass | Ran this session |
| 7 | Unknown project slug → exact not-found message | Automated (`test_unknown_project_slug_exits_nonzero_with_exact_message`) | pass | Ran this session |
| 8 | Missing/empty spec → clear message, no network call | Automated (2 tests) | pass | Ran this session |
| 9 | Config mode looser than 600 → loud warning, still proceeds | Automated (`test_loose_config_permissions_warns_but_still_proceeds`) + pass 1's real `chmod 644` run | pass | Ran this session |
| 10 | `--project other-slug` overrides config for one invocation, file unmodified | Automated (`test_explicit_project_flag_overrides_config_without_modifying_file`) | pass | Ran this session |
| 11 | Password never appears in stdout/stderr, even on bad-credentials failure | Automated (`test_password_never_appears_in_stdout_or_stderr_even_on_bad_credentials`) + fresh direct check this session on `_build_subject_and_description`'s JSON round-trip and `_load_config` | pass | Ran this session; also re-confirmed manually: `TaigaHTTPError.body` (which could theoretically echo request data) is never interpolated into any user-facing message anywhere in the current code |
| — | Manual `KEY=value` parser: value containing `=` | Manual, direct function call, this session | pass | `TAIGA_PASSWORD=pass=word=with=equals` → parses to the full value (still `str.partition("=")`, unchanged code) |
| — | Injection safety: spec body with quotes/backslashes/embedded-JSON as description | Manual, direct function call + `json.dumps`/`json.loads` round-trip, this session | pass | Round-trips exactly unchanged — sent as an inert JSON string body, no injection surface (unchanged code) |
| — | `(umask 077; ...) + chmod 600` "no window" | Not re-run this pass — file unmodified by this fix cycle (confirmed: only `scripts/taiga_push_spec.py` changed); pass 1 already verified this with real execution | pass (carried over) | See pass 1's `docs/test-review.md` history for the original real-execution evidence |
| — | 401 on userstory creation (post-token) surfaced as bad-credentials | Automated (`test_401_on_userstory_creation_is_surfaced_as_bad_credentials`) | pass | Ran this session |
| — | Unmapped HTTP status (500) during project lookup → safe generic message | Manual, direct function call with a canary string in the fake response body, this session | pass | See "Follow-up fix re-verification" above — no traceback, no body leak |

## Regression check
`python3 -m unittest discover -s tests -v` — **122/122 pass** this session
(88 pre-existing + 34 in `tests/test_taiga_push.py`, matching the
developer's claimed count exactly). `python3 -m py_compile
scripts/taiga_push_spec.py` and `bash -n scripts/taiga-configure-push.sh`
both clean, run this session.

## Defects found
None this pass. Defect 1 from pass 1 is fixed and re-verified generically
(8 malformation variants, all handled); the non-blocking follow-up noted
alongside it is also fixed and re-verified.

---

## Spec coverage
All 11 checkbox items under `docs/spec.md` "Acceptance criteria" map to a
passing automated test (see table above, # 1-11) plus, for the four
network-dependent ones (1, 2, 6, 7), pass 1's independent real-fake-HTTP-
server verification. No gaps found. "Edge cases" section's items are each
covered: unreachable Taiga (incl. the URL-malformation sub-case this pass
focused on), bad credentials, unknown project, missing/unreadable config,
incomplete config (blank password falls through to bad-credentials shape,
covered by `test_incomplete_config_never_raises_keyerror` +
`test_bad_credentials_...`), missing/empty spec, loose permissions,
re-running creates a second userstory (by design, not tested as a failure
mode — correctly not treated as an error), `--dry-run`+`--verify` together
(covered), and the defensive 401-on-userstory-creation check (covered).

## Findings (most severe first)

None must-fix or should-fix. Two optional nits:

### 1. No dedicated automated test for the unmapped-HTTP-status (500) project-lookup message — nit
- File: `tests/test_taiga_push.py` (no corresponding test added alongside `scripts/taiga_push_spec.py:271-278`)
- Issue: the new `except TaigaHTTPError as e:` branch in `_lookup_project_or_raise` (the non-blocking follow-up fixed alongside Defect 1) has no regression test asserting its exact wording or that it doesn't leak `e.body`. I confirmed the behavior directly this session (see "Follow-up fix re-verification"), so it's not a coverage gap against any acceptance criterion (the spec has no named wording for this status), just a missing regression guard for a real code path.
- Failure scenario: none currently — purely a "would be nice to lock this in" note, since a future refactor could silently regress this branch with nothing to catch it.

### 2. `http://` (scheme-only, no host) collapses to `http:` in the printed message — nit
- File: `scripts/taiga_push_spec.py:325` (`base_url = cfg.get("TAIGA_URL", "").rstrip("/")`)
- Issue: `TAIGA_URL=http://` gets `rstrip("/")`'d down to `http:` before being embedded in the "Could not reach Taiga at http:" message — a cosmetic oddity (pre-existing `.rstrip("/")` behavior, not touched by this fix cycle) for an already-unlikely input. Still produces a clean, non-crashing, non-misleading-enough message; not worth a fix on its own.

## Follow-ups (non-blocking)
- Consider adding one regression test for the 500-during-project-lookup case, mirroring the existing 404/401/403 tests in `EndpointFunctionTests`/`MainIntegrationTests`, next time this file is touched for an unrelated reason.

## Overall verdict
**Approve.** Defect 1 is fixed and independently re-verified as a generic
fix (not a narrow patch of the one originally-found string) against eight
distinct URL-malformation variants, with a revert-and-watch-it-fail check
confirming the two new regression tests actually exercise it. The
non-blocking follow-up (unmapped HTTP status during project lookup) is also
fixed and re-verified as safe (no traceback, no leaked response body). Full
regression suite is clean (122/122), and the credential-handling/
permissions/injection-safety findings from pass 1 still hold against the
current code (re-checked directly this session, not assumed). All 11
acceptance criteria in `docs/spec.md` are implemented and covered by
passing tests. No must-fix or should-fix findings — two optional nits noted
above, neither blocking. Handing control back to the product-manager agent
for the next iteration.
