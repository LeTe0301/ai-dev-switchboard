# Implementation: Local backlog tracker (Taiga) — part 1b: push a spec into Taiga

(1a's implementation notes — the `install.sh --with-taiga` flag + singleton
UI toggle row — are preserved in git history at commit `ed84d73` via
`git show ed84d73:docs/implementation.md`. This file now documents 1b only,
per this cycle's `docs/spec.md`.)

## Summary
Added two new, standalone files — `scripts/taiga_push_spec.py` (Python 3,
stdlib-only) and `scripts/taiga-configure-push.sh` (bash, interactive
one-time setup) — that push the content of a local spec file into a running
Taiga instance as a new userstory, one direction only (spec → Taiga), with
no changes to any existing file's runtime behavior beyond three new README
bullets. No web UI surface, matching the spec's explicit non-goal.

## Changes by file

- **`scripts/taiga_push_spec.py`** (new) — the CLI tool. Structure matches
  `docs/spec.md`'s exact function breakdown:
  - `_taiga_request(base_url, method, path, token=None, body=None)` — the
    one `urllib.request` wrapper; raises `TaigaConnectionError` (network-
    level failure) or `TaigaHTTPError` (non-2xx response, carries `.status`)
    on failure, returns parsed JSON on success. This is the one function
    `tests/test_taiga_push.py` monkeypatches for every higher-level test,
    exactly as instructed (mirrors `tests/test_taiga.py`'s
    `appmod.taiga_run` monkeypatch).
  - `_load_config(path) -> dict` — manual `KEY=value` parsing (no
    `configparser`, no `python-dotenv`), matching `app/app.py`'s
    `_parse_engine_file()` convention for `engines.d/*.engine` files. Uses
    `dict.get(..., "")` everywhere downstream so a missing/blank key never
    raises `KeyError` — an incomplete config file naturally falls through to
    Taiga rejecting the (blank) credentials, producing the same
    bad-credentials-shaped message rather than a traceback.
  - `_authenticate`, `_lookup_project`, `_create_userstory` — one function
    per Taiga REST call (`POST /api/v1/auth`, `GET
    /api/v1/projects/by_slug?slug=...`, `POST /api/v1/userstories`), each
    translating a `TaigaHTTPError` into an internal marker exception
    (`_AuthRejected` / `_ProjectNotFound`) that a thin wrapper
    (`_authenticate_or_raise` etc., see "Key decisions") turns into the
    exact edge-case wording from `docs/spec.md`, with the config path
    embedded.
  - `_build_subject_and_description(spec_text, spec_path)` — subject is the
    `# Spec: ...` first line with the prefix stripped, falling back to the
    file's first non-blank line, then to the filename; description is the
    full raw spec text plus a traceability footer (origin repo dirname +
    spec path + UTC timestamp).
  - `main(argv=None)` — argument parsing (`--spec`, `--project`, `--config`,
    `--dry-run`, `--verify`), catches `TaigaPushError` at the top level and
    prints one clear `stderr` line (no traceback), non-zero exit.
- **`scripts/taiga-configure-push.sh`** (new) — self-contained (does not
  source `install.sh`), re-implements `install.sh`'s own `prompt()`/
  `prompt_secret()` idiom locally. Prompts for Taiga URL (default
  `http://127.0.0.1:9000`), username, password (`read -rsp`, never echoed),
  and project slug; writes `~/.config/ai-dev-switchboard/taiga-push.env`
  under a `(umask 077; ... > file)` subshell (so the file is never created
  world/group-readable even for the instant before the explicit `chmod 600`
  that follows — belt-and-suspenders against the spec's "no window" rule),
  then runs `python3 scripts/taiga_push_spec.py --verify` and reports
  pass/fail via its own exit code.
- **`tests/test_taiga_push.py`** (new, since amended — see "Fix cycle: Defect
  1" below) — 34 `unittest` assertions across 8 `TestCase` classes; see "How
  to verify locally" and "Known limitations" below.
- **`README.md`** — three additions per `docs/spec.md` "Documentation":
  one bullet under "What you get", one line under "Repo layout", one bullet
  under "Security notes" using the exact `AUTH_MODE=simple` phrasing
  pattern the spec quotes.
- **No changes** to `install.sh`, `app/app.py`,
  `config/switchboard.env.example`, or any sudoers/systemd/frontend code —
  confirmed by `git status` before/after: only the four files above (plus
  this doc and `docs/spec.md`, the latter already written by
  `product-manager` before this cycle started) are touched.

## Key decisions / tradeoffs

- **`_taiga_request`'s own raised message vs. the edge-case wording split
  across two layers.** `docs/spec.md` says `_taiga_request` itself "raises
  TaigaPushError with a clear message on any non-2xx status or connection
  failure," but the *exact* wording for "bad credentials" vs. "unknown
  project" differs per endpoint and needs the config path embedded (which
  `_taiga_request` doesn't know). Read this as: `_taiga_request` raises a
  generic, endpoint-agnostic `TaigaConnectionError`/`TaigaHTTPError` (its
  literal "one clear message" duty, and the one seam tests monkeypatch);
  three small `_..._or_raise` wrapper functions in `_run()` translate those
  into the exact, config-path-bearing wording from `docs/spec.md` "Edge
  cases". This keeps `_authenticate`/`_lookup_project`/`_create_userstory`
  matching the spec's literal 3-arg signatures (no `config_path` parameter
  bolted on) while still producing the exact required message text.
- **Never include Taiga's raw HTTP response body in any printed message.**
  `TaigaHTTPError.body` is captured (useful for future debugging/logging)
  but deliberately never interpolated into a user-facing message anywhere —
  this is what makes "the password must never appear in stdout/stderr, even
  in a bad-credentials error" true unconditionally, rather than true only
  because Taiga's real API happens not to echo request bodies back today.
- **Missing/empty spec file is checked before any network call**, for both
  the normal push path and `--dry-run` (which still needs real spec text to
  preview) — but not `--verify`, which returns before that check runs at
  all, per the spec's stated precedence.
- **Manual `KEY=value` parsing, not `configparser`** — matches this
  project's one existing precedent for parsing an env-style file in Python
  (`app/app.py`'s `_parse_engine_file()` for `engines.d/*.engine`), not
  `configparser`'s INI-section model, which doesn't fit a flat `KEY=value`
  file anyway.

## Deviations from spec

None substantive. One minor, non-behavioral naming liberty: `docs/spec.md`
sketches `_authenticate`/`_lookup_project`/`_create_userstory` as the only
three endpoint functions; I added three thin `_..._or_raise` wrappers around
them (see "Key decisions" above) purely to keep the exact required
edge-case wording (which needs the `--config` path) out of the three
spec-named functions' own bodies. The three spec-named functions exist with
exactly the signatures the spec gives and do the exact HTTP call each is
named for; the wrappers are additive, not a replacement.

## Fix cycle: Defect 1 (blank/malformed `TAIGA_URL` raw traceback)

`docs/test-review.md` found one must-fix defect after its first testing
pass: in `_taiga_request` (`scripts/taiga_push_spec.py`), the
`urllib.request.Request(url, ...)` construction happened *before* the
surrounding `try:` block. `Request.__init__` itself parses the URL and
raises a bare `ValueError` for anything unparseable (blank string, or a
string with no recognizable scheme) — since that construction was outside
the `try:`, the existing `except (urllib.error.URLError, OSError,
ValueError)` handler never saw it, and the `ValueError` propagated all the
way past `main()`'s `except TaigaPushError` catch as a raw Python
traceback. This directly violated acceptance criterion 5 (Taiga
unreachable → clean message, no traceback), specifically for the
"wrong `TAIGA_URL`" trigger the spec names explicitly.

**Fix**: moved `req = urllib.request.Request(url, data=data, method=method,
headers=headers)` to be the first statement inside `_taiga_request`'s
existing `try:` block (`scripts/taiga_push_spec.py`, `_taiga_request`), so
a `ValueError` raised during request construction is caught by the exact
same `except (urllib.error.URLError, OSError, ValueError)` handler that
already converts connection failures into `TaigaConnectionError` — no new
exception type, no new handler, just closing the gap between where the
object is built and where the `try:` started. A blank/malformed
`TAIGA_URL` now produces the same `error: Could not reach Taiga at
<url> — make sure it's toggled on in the ai-dev-switchboard web UI, or
check TAIGA_URL in <config path>.` message and exit code 1 as any other
unreachable-Taiga case.

**Regression tests added** (`tests/test_taiga_push.py`,
`MainIntegrationTests`): `test_blank_taiga_url_exits_nonzero_with_
unreachable_message_no_traceback` (empty `TAIGA_URL`) and
`test_malformed_taiga_url_exits_nonzero_with_unreachable_message_no_
traceback` (`TAIGA_URL=not-a-url-at-all`, a scheme-less value). Both
deliberately do **not** monkeypatch `_taiga_request` (unlike every other
test in the file) so the real `urllib.request.Request(...)` call is what's
actually exercised — monkeypatching it would have hidden this exact bug.
Verified TDD-style: temporarily reverted the fix and confirmed both new
tests fail with the exact reported traceback (`ValueError: unknown url
type: ...` surfacing all the way out of `tps.main()`), then restored the
fix and confirmed both pass.

**Non-blocking follow-up also fixed alongside this** (per the reviewer's
note, small and obvious): `_lookup_project_or_raise` previously let any
`TaigaHTTPError` status other than `404`/`401`/`403` (e.g. a `500`)
propagate unchanged, surfacing as the generic, unstated `error: Taiga
returned HTTP 500`. Added an explicit `except TaigaHTTPError as e:` branch
there that raises `TaigaPushError(f"Taiga rejected the project lookup
(HTTP {e.status}).")` instead — mirrors the existing fallback wording
pattern `_create_userstory_or_raise` already uses for its own unmapped
statuses (`"Taiga rejected the new userstory (HTTP {e.status})."`), and
still never includes `e.body` (the raw response) in the message, preserving
the no-leaked-data guarantee. No test asserted the old wording, so this
didn't require a test change, but the existing `_lookup_project_404_raises_
project_not_found` and `test_unknown_project_slug_...` tests still cover
the 404 path unchanged.

## Known limitations

- **No live Taiga instance was reachable in this environment** (same gap
  1a's own `docs/implementation.md` — commit `ed84d73` — already
  documented: `docker compose` — the plugin — isn't installed here, only
  the bare `dockerd`; confirmed again this cycle with `docker compose
  version` → `'compose' is not a docker command`, and `systemctl status
  ai-dev-switchboard` → unit not found, i.e. 1a was never actually deployed
  in this sandbox either). Real Taiga-hosted auth/project-lookup/userstory-
  creation behavior against a genuine Taiga backend was **not** verified
  here.
  - What **was** verified instead, to close that gap as much as possible
    without a live Taiga: a small standalone fake HTTP server
    (`http.server.HTTPServer`, not part of the shipped code — a throwaway
    script written only for this verification pass, not committed) that
    implements the three endpoints' documented shapes (`POST /api/v1/auth`,
    `GET /api/v1/projects/by_slug`, `POST /api/v1/userstories`) exactly as
    `docs/spec.md` "Background" describes them. Against that real HTTP
    server (real `urllib.request` calls over a real socket, nothing
    monkeypatched): `--verify` succeeded, a normal run created one
    "userstory" and printed the correct `#ref` + URL, `--dry-run` printed
    the correct preview with no POST sent, a wrong password produced the
    exact bad-credentials message via a real 400 response, and an unknown
    project slug produced the exact not-found message via a real 404
    response. `scripts/taiga-configure-push.sh` was also run end-to-end
    against this same fake server with piped stdin answers — wrote a real
    `mode 600` config file with the entered values, then correctly reported
    setup success.
  - This confirms the real network/HTTP/JSON-serialization code path works
    correctly end-to-end and that the three request shapes match
    `taiga-doc`'s documented API — but it does **not** confirm Taiga's own
    real backend behaves exactly like the fake server past what's
    documented (e.g. exact error body shape on a 400, any auth-token
    expiry/format quirks, Taiga version-specific field-name drift the spec
    itself flags as a risk under "Open questions"). If a real Taiga
    instance becomes reachable, the fastest smoke test is: `bash
    scripts/taiga-configure-push.sh` against it, then `python3
    scripts/taiga_push_spec.py` with no flags and check the printed
    userstory shows up in Taiga's own web UI.
- **`tests/test_taiga_push.py`'s `ConfigureScriptTests` class** runs the
  real bash script end-to-end but necessarily points it at
  `http://127.0.0.1:1` (nothing listens there in this sandbox), so it only
  confirms file-creation/permissions/exit-code-propagation, not a
  successful verify — the fake-HTTP-server pass described above (not
  committed, ad hoc) is what covered the successful path.

## How to verify locally

```bash
cd /home/dev/projects/ai-dev-switchboard
python3 -m py_compile scripts/taiga_push_spec.py
bash -n scripts/taiga-configure-push.sh
python3 -m unittest discover -s tests -v   # expect 122/122 pass (88 pre-existing + 34 in test_taiga_push.py)
# or just the new suite:
python3 tests/test_taiga_push.py -v        # expect 34/34 pass
```

Manual smoke test against a real Taiga instance (needs 1a's stack actually
running — not available in this sandbox, see "Known limitations"):

```bash
# one-time setup, after a Taiga user + target project already exist:
bash scripts/taiga-configure-push.sh
# → prompts for URL/username/password/project slug, writes
#   ~/.config/ai-dev-switchboard/taiga-push.env (mode 600), then runs
#   --verify automatically and reports pass/fail.

# dry-run first, to sanity-check formatting without creating backlog noise:
python3 scripts/taiga_push_spec.py --dry-run

# the real push:
python3 scripts/taiga_push_spec.py
# → prints "Created userstory #<ref>: <url>" and exits 0; open the URL to
#   confirm it landed in Taiga with the right subject/description.

# error paths (each should print one clear line to stderr, no traceback,
# and exit non-zero):
python3 scripts/taiga_push_spec.py --config /nonexistent/path.env
python3 scripts/taiga_push_spec.py --project this-project-does-not-exist
# temporarily toggle Taiga off in the ai-dev-switchboard web UI, then:
python3 scripts/taiga_push_spec.py --dry-run
```
