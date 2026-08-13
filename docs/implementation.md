# Implementation: switchboard-side deploy dispatch (2c part 2b)

## Summary
Adds a hand-edited `deploy-map.json` (project name → target host/port/user/
deploy_path/service/key), a new `deploy_run(name)` dispatch function in
`app.py` that pushes `PROJECTS_DIR/<name>` to a 2c-2a `deploy-target`
receiver via `rsync` and then triggers its restart over SSH, and a
per-project "Deploy" button in the web UI (visible only when a project has
a map entry) gated behind a native `confirm()` dialog. Deploy is
manual-only: nothing added this cycle calls `deploy_run()` from 2c part 1's
poll/sync path — a push landing on Gitea never triggers a deploy by itself.

## Root cause
N/A — new feature, not a bugfix.

## Post-review bugfix
The reviewer's boundary testing found that `_load_deploy_map()`
(`app/app.py`) coerced `entry.get("port")` with a bare `int(...)`, which
raised an uncaught `ValueError` — crashing `/status` for *every* project,
not just the malformed one — when an operator hand-edited a non-numeric
`port` value into one `deploy-map.json` entry. This violated the spec's
explicit "one malformed hand-edited entry must not take down every other
project's Deploy button" requirement (the rest of `_load_deploy_map()`
already had this tolerant per-entry drop-and-continue discipline; this one
coercion was missed). Fixed by wrapping the `int()` coercion in
`try/except (TypeError, ValueError): continue`, same as every other
per-entry validation failure in that function (`app/app.py`,
`_load_deploy_map()`).

Added regression tests:
- `tests/test_deploy_dispatch.py`
  `DeployMapLoadTests.test_entry_with_non_numeric_port_is_dropped_not_raised`
  and `.test_one_entry_with_non_numeric_port_does_not_affect_others` (unit
  level — loader behavior).
- `tests/test_deploy_dispatch.py`
  `DeployEndpointTests.test_status_survives_non_numeric_port_and_keeps_other_projects_intact`
  (endpoint level — reproduces the reviewer's exact live repro: `/status`
  returns 200 with the malformed project's `deploy` field simply absent and
  the other project's `deploy` field intact).

Also folded in the reviewer's two non-blocking follow-ups, since both were
cheap:
- `tests/test_deploy_dispatch.py`
  `DeployNeverCalledFromPollSyncTests.test_sha_change_drives_sync_but_never_deploy_run`
  — AC10 now has an automated regression guard: drives the real
  `_gitea_poll_one` → `_gitea_sync_bg` chain through a SHA change (subprocess
  and `_gitea_api` mocked, `deploy_run` monkeypatched to a call-recording
  stub) and asserts `deploy_run` is never invoked. Uses the same
  "poll the per-owner_repo lock until the background thread releases it"
  wait technique `tests/test_gitea_poll.py`'s
  `GiteaSyncBgConcurrencyTests` already established.
- `tests/test_deploy_frontend.js` — new test "a quote-containing
  host/service value renders safely and still dispatches to the right
  target": asserts a `"`/`'`-containing `host`/`service` never appears in
  the rendered row HTML at all (confirming `DEPLOY_TARGETS`'s JSON-in,
  never-touches-`innerHTML` design is injection-safe regardless of field
  content) and that `doDeploy` still dispatches to the correct project.

Full suite re-run after these changes: `python3 -m unittest discover -s
tests -v` → **287/287 pass** (283 baseline + 4 new: 3 for the port-crash
fix, 1 for AC10). `node tests/test_deploy_frontend.js` → **9/9 pass** (8
baseline + 1 quote-safety test). `node
tests/test_singleton_toggle_frontend.js` → 15/15 pass (unrelated regression
check, still green).

## Changes by file

- `app/app.py`
  - New env vars `DEPLOY_MAP_FILE` (default
    `/etc/ai-dev-switchboard/deploy-map.json`) and `DEPLOY_KEYS_DIR`
    (default `/etc/ai-dev-switchboard/deploy-keys`), declared next to
    `GITEA_REPO_MAP_FILE`.
  - New `_load_deploy_map()`: mirrors `_load_gitea_repo_map()`'s
    "missing/malformed file → `{}`, never crash" tolerance, plus per-entry
    validation — drops (never raises on) any entry missing a required key
    (`host`/`deploy_path`/`service`/`key`) or whose `key` path resolves
    (via `os.path.realpath`) outside `DEPLOY_KEYS_DIR`. Applies
    `port` (default `22`) and `user` (default `"deploy"`) defaults. No
    caching — re-read on every call, same as spec's "Loading" section
    requires.
  - New `_deploy_locks`/`_deploy_lock_for(name)`: per-project non-blocking
    `threading.Lock` dict, same guarded-dict idiom as
    `_gitea_sync_lock_for`.
  - New `deploy_run(name) -> (int, str)`: looks up the map entry (404 if
    absent), acquires the per-project lock non-blocking (409 if already
    held), runs `rsync -e "ssh -i <key> ... -p <port>" -a
    PROJECTS_DIR/<name>/ <user>@<host>:` (bare destination, per
    `deploy-target/README.md`'s protocol contract), and on success runs
    `ssh -i <key> ... <user>@<host> deploy-restart`. Returns `(502, "push
    failed: ...")`, `(502, "push succeeded but restart failed: ...")`, or
    `(200, "deployed")`. Both subprocess calls are wrapped in
    `try/except (subprocess.SubprocessError, OSError)` so a hang/timeout/
    missing-binary surfaces as a clean 502 instead of an unhandled
    exception (a security/correctness hygiene addition beyond the spec's
    literal pseudocode, to avoid ever returning a raw 500 with a stack
    trace to the client). Lock always released in `finally`.
  - New `do_POST` branch: `POST /instance/<name>/deploy` — 404 for an
    unknown instance, otherwise `self._json({"ok": status == 200,
    "message": msg}, status)`. Added after the existing TOTP gate, same as
    every other mutating route.
  - `/status`: `_load_deploy_map()` read once per call; each instance gets
    `inst["deploy"] = {"host", "deploy_path", "service"}` when a valid map
    entry exists (`key`/`port`/`user` deliberately excluded from the
    client payload).
  - `PAGE_TEMPLATE` (embedded HTML/CSS/JS):
    - CSS: `.deploy-row`, `.deploy-btn` (green `#34c759`, reuses
      `.new-project-row button`'s shape), `.deploy-msg` +
      `.deploy-msg.success`/`.deploy-msg.error` (`#34c759`/`#ff6b6b`).
    - New `let DEPLOY_TARGETS = {}` (project name → `{host, deploy_path,
      service}`), refreshed every `refresh()` call.
    - New `deployRow(name, deploy)`: renders the button + an empty
      `.deploy-msg` slot when `deploy` is present, nothing otherwise.
    - `row()` gained a 13th `deploy` parameter; `refresh()` passes
      `inst.deploy` through and populates `DEPLOY_TARGETS`.
    - `actionPath()` gained a `'deploy'` case
      (`/instance/<name>/deploy`).
    - `handleActionResult()` gained a `kind === 'deploy'` branch: writes
      the response's `message` into that row's `.deploy-msg` element
      (`success`/`error` class), and — unlike every other kind — never
      calls `setTimeout(refresh, 1500)`, since that would wipe the message
      almost immediately instead of leaving it in place until the next
      *natural* `/status` poll (spec's "gone on next refresh()"). The
      428 code-overlay label also gets a `Deploying: <name>` variant.
    - New `doDeploy(name)`: looks the project's target info up in
      `DEPLOY_TARGETS`, shows the `confirm()` dialog, sets the message
      slot to "Deploying…", then calls `toggle('deploy', name, true,
      null)` — a thin wrapper around the exact same
      `toggle`/`performAction`/`handleActionResult`/`pendingToggle`
      machinery every other action already uses, so the shared TOTP
      code-overlay retry path (a 428 mid-flow) works with no new auth
      logic, per the design doc's own note.

- `config/deploy-map.json.example` (new) — the exact single-entry schema
  from `docs/spec.md`.
- `config/switchboard.env.example` — new commented-out
  `#DEPLOY_MAP_FILE=...` / `#DEPLOY_KEYS_DIR=...` section, same style as
  the existing `#GITEA_REPO_MAP_FILE=...` line.
- `install.sh` — two small, unconditional (no new `--with-*` flag)
  additions:
  - Near the `SVC_USER`/`PROJECTS_DIR` setup: `mkdir -p
    "$CONFIG_DIR/deploy-keys"; chmod 700 ...; chown "$SVC_USER:$SVC_USER"
    ...` — reasserted every run.
  - Near the `ENV_FILE` writing block: `[ -f
    "$CONFIG_DIR/deploy-map.json" ] || echo '{}' >
    "$CONFIG_DIR/deploy-map.json"` (copy-if-absent **only** — never
    touched again on re-run, unlike every `set_env`-patched value in this
    file) plus `set_env ... DEPLOY_MAP_FILE ...` /
    `set_env ... DEPLOY_KEYS_DIR ...`.
- `deploy-target/README.md` — updated the stale "switchboard-side wiring
  doesn't exist yet" language in the intro and "What this cycle doesn't
  do", and added a new "Switchboard-side caller (2c part 2b)" section
  pointing at `deploy-map.json`/`config/deploy-map.json.example` and
  restating the known-hosts trust bootstrapping step. The "Protocol
  contract" section itself is untouched, per the spec's own note that it
  was "already written to be exactly what this cycle needed."
- `README.md` — repo-layout tree note for `config/deploy-map.json.example`
  and that `deploy-target/`'s receiver now has a real caller; "Security
  notes" bullet updated to describe `DEPLOY_KEYS_DIR` and the manual-only
  dispatch (previously said "no switchboard UI consumer yet").
- `docs/BACKLOG.md` — item 2's status note and two "Shape of the work"
  bullets updated to reflect that 2c part 2 shipped in two sub-parts
  (2a receiver, 2b this cycle's caller) and that deploy is manual-only,
  not the original auto-deploy-off-the-poll framing.
- `tests/test_deploy_dispatch.py` (new) — `DeployMapLoadTests` (loader
  validation), `DeployRunTests` (dispatch logic, `subprocess.run` mocked),
  `DeployEndpointTests` (real `ThreadingHTTPServer`, login/TOTP flow,
  `/status` + `/instance/<name>/deploy`), `InstallShDeployMapBlockTests`
  (install.sh's two new blocks extracted verbatim and run standalone, no
  sudo needed), and `PrivilegedDeployRunEndToEndTests` (gated on
  passwordless sudo + local sshd, like `test_deploy_target.py`'s own
  `PrivilegedEndToEndTests`) — provisions a real throwaway
  `deploy-target` receiver and calls the real `deploy_run()`, with
  `subprocess.run` **not** mocked, over real `ssh`/`rsync` against
  `127.0.0.1`. 41 new Python tests total (38 + 3 added in the post-review
  bugfix pass) plus a new `DeployNeverCalledFromPollSyncTests` class (1
  more test) for the AC10 follow-up — 42 in this file overall.
- `tests/test_deploy_frontend.js` (new) — extracts the real rendered
  `<script>` from `render_page()` (same technique as
  `tests/test_singleton_toggle_frontend.js`) and drives `doDeploy`/
  `deployRow`/`row`/`handleActionResult` against stubbed `document`/
  `fetch`/`confirm`: button visibility, the `confirm()` cancel path, all
  four result-message shapes (success/push-failed/restart-failed/409),
  and the 428→code-overlay→retry flow. 8 tests (9 after the post-review
  quote-safety test was added).

## Key decisions / tradeoffs

- **`doDeploy(name)` takes only a name, not `(name, deploy)`.** The design
  doc's own sketch was `doDeploy(name, deploy)` with the target object
  inlined into the rendered `onclick="..."` attribute (e.g.
  `onclick="doDeploy('proj', {"host":...})"`). `deploy.host`/
  `deploy.service` come from an operator-hand-edited JSON file and the
  design doc's own "Notes for the developer" flags that they "could
  theoretically contain quotes." Embedding an arbitrary JSON blob into an
  HTML attribute correctly (surviving both HTML-attribute-quoting and
  JS-string-literal-quoting at once) is exactly the kind of thing this
  codebase's existing `esc()` helper does *not* do (it only escapes
  `&`/`<`/`>` for text-node content, not attribute quotes) — so instead of
  hand-rolling a second escaping scheme, `doDeploy` takes just the
  (already-charset-validated) project `name` and looks the target info up
  in a small module-level `DEPLOY_TARGETS` map populated straight from the
  already-JSON-parsed `/status` response, the same pattern `ENGINE_LABELS`
  already uses. Functionally identical UX; strictly safer. Documented here
  since it's a literal signature deviation from the design doc.
- **`handleActionResult`'s `kind === 'deploy'` branch skips
  `setTimeout(refresh, 1500)`.** Every other action calls this to
  optimistically re-render soon after a successful mutation. For deploy,
  doing that would clear the just-written result message in ~1.5s instead
  of the spec's "persists until next `/status` refresh." Deliberately
  left to the existing 4-second poll interval instead.
- **Deploy is wired through the existing `toggle()`/`performAction()`
  machinery** rather than a bespoke fetch+overlay implementation, so the
  TOTP code-overlay retry path (428 → prompt → retry with `code`) works
  for free, exactly as the design doc's "Notes for the developer" asked
  for ("No new auth logic needed").
- **Both `subprocess.run` calls in `deploy_run()` are wrapped in
  `try/except`**, even though the spec's own pseudocode doesn't show this
  — `BatchMode=yes`/`ConnectTimeout` cover most failure shapes, but a
  missing `rsync`/`ssh` binary, or a timeout past `ConnectTimeout` (DNS
  resolution on some `ssh` builds isn't covered by `ConnectTimeout`),
  would otherwise raise and produce a raw 500 with no JSON body — a
  security/correctness hygiene fix beyond the literal spec text, folded in
  without a separate discussion since it's strictly a robustness
  improvement with no behavior change on the happy or already-specified
  error paths.

## Deviations from spec
- `doDeploy`'s signature (`(name)` instead of `(name, deploy)`) — see "Key
  decisions" above. Behavior matches the spec/design exactly; only the
  internal JS function signature differs.
- No other deviations. `deploy_run()`'s rsync/ssh argv, the map schema and
  its validation rules, the route shape, the `/status` field shape, and
  `install.sh`'s two blocks all match `docs/spec.md`'s "Proposed approach"
  verbatim (confirmed by `DeployRunTests.
  test_success_returns_200_and_runs_push_then_restart_with_exact_argv`,
  which asserts the exact argv list, and
  `InstallShDeployMapBlockTests`/`InstallShTemplateTests`, which run/grep
  `install.sh`'s own real source rather than a re-implementation of it).

## Known limitations
- **Known-hosts trust bootstrapping is a real one-time manual step**, not
  automated (spec's own "Open questions", carried over from `host_run()`'s
  existing precedent): the operator must get `SVC_USER`'s `known_hosts` to
  trust each target host before the first click, or the first deploy fails
  fast with a clear 502 (not a hang, since `BatchMode=yes` refuses an
  interactive host-key prompt). Documented in
  `deploy-target/README.md`'s new section.
- **No interlock with 2c part 1's in-flight Gitea sync** — a deploy click
  racing a background `git fetch`/`merge --ff-only` is not defended
  against this cycle (spec's own accepted low-probability/low-impact
  assumption).
- **`deploy_path`/`service` in the map are display-only** — never
  cross-checked against the target's own real `deploy-target.env` values,
  so a mismatch between what the map says and what's actually configured
  on the target is possible and silent (spec's own flagged, accepted
  limitation).
- **No persisted deploy history/status** — the UI's only feedback is the
  one synchronous POST's own result, gone on the next `/status` refresh
  (spec non-goal, not a limitation introduced by this implementation).

## How to verify locally

Backend + install.sh tests (Python, stdlib `unittest`):
```
python3 -m unittest discover -s tests -v
```
This runs the full suite (287 tests as of this cycle including the
post-review bugfix pass, all passing), including
`tests/test_deploy_dispatch.py`. The privileged classes inside
it (`PrivilegedDeployRunEndToEndTests`, and `test_deploy_target.py`'s own
`InstallScriptDeployTargetBlockTests`/`PrivilegedEndToEndTests`) need
passwordless `sudo` and a local `sshd` on `127.0.0.1:22` — they
self-skip cleanly if either is unavailable. To run only this cycle's own
file:
```
python3 -m unittest tests.test_deploy_dispatch -v
```

Frontend tests (Node, stdlib `vm`, no dependencies):
```
node tests/test_deploy_frontend.js
node tests/test_singleton_toggle_frontend.js   # regression check — unrelated to this cycle, still green
```

Manual end-to-end check (mirrors the acceptance criteria):
1. On a second machine: `sudo ./install.sh --with-deploy-target`
   (see `deploy-target/README.md`).
2. On the switchboard box: `sudo ./install.sh` (re-run is safe/idempotent)
   — creates `/etc/ai-dev-switchboard/deploy-keys/` (mode 700) and an
   empty `deploy-map.json` if one doesn't already exist.
3. Generate a keypair, place the private half under `deploy-keys/`, the
   public half in the target's `authorized_keys` (per
   `deploy-target/README.md` step 2/3).
4. `sudo -u <SVC_USER> ssh -i <key> deploy@<target> true` once, to seed
   `known_hosts` (see "Known limitations" above).
5. Hand-edit `/etc/ai-dev-switchboard/deploy-map.json` with a real entry
   for one of your projects, pointing `key` at the file placed in step 3.
6. Reload the web UI — the project's row should now show a "Deploy"
   button. Click it, confirm the dialog, and watch the inline result
   message (success, or a specific failure reason).
