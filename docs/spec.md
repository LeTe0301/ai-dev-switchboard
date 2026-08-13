# Spec: Local git hosting UI + CI/CD (Gitea) — part 2c, part 2b: switchboard-side deploy dispatch

## Summary
Add a hand-edited project→target map (`deploy-map.json`) plus a per-target
SSH private key store, a new `deploy_run()` dispatch function in `app.py`
that pushes `PROJECTS_DIR/<name>` to a 2c-2a `deploy-target` receiver and
triggers its restart, and a manual "Deploy" button in the web UI that calls
it — wiring the switchboard side of CI/CD auto-deploy up to (but stopping
short of) actually automating the trigger.

**Deliberate deviation from `docs/BACKLOG.md`'s original framing**: item 2's
"Shape of the work" describes deploy as automatic ("push to `main` → rsync/
deploy to target → target restarts its service", chained directly off the
poll). Per the user's explicit decision this cycle, that auto-trigger is
**not** built. A push landing (2c part 1's `_gitea_poll_one`/`_gitea_sync_bg`
detecting a new SHA and syncing it into `PROJECTS_DIR/<name>`) never itself
calls `deploy_run()`. The only way a deploy fires is a human clicking
"Deploy" in the UI. `docs/BACKLOG.md` should be updated to reflect this once
this cycle ships (see Affected areas).

## Goals
- A new hand-edited, operator-maintained map file (default
  `/etc/ai-dev-switchboard/deploy-map.json`) naming, per project, which
  `deploy-target` receiver it deploys to and where that target's private
  key lives — not UI-editable, mirroring `deploy-target/README.md`'s own
  "you generate/place the SSH key yourself" precedent.
- SSH private keys stored under a dedicated, mode-700 directory (default
  `/etc/ai-dev-switchboard/deploy-keys/`), owned by `SVC_USER` (the user
  `app.py` already runs as — no new privilege boundary, no new sudoers
  rule).
- A `deploy_run(name)` function in `app.py` that, given a project name with
  a map entry, pushes `PROJECTS_DIR/<name>` to the target via `rsync` and
  then triggers `deploy-restart` over SSH, following
  `deploy-target/README.md`'s "Protocol contract" exactly (bare destination
  path, literal `deploy-restart` command, non-zero restart exit surfaced —
  not swallowed).
- A "Deploy" button per project row in the web UI, shown only for projects
  with a map entry, that calls this dispatch synchronously and shows the
  result (success/failure) inline — a manual, one-click, human-confirmed
  action, never automatic.
- Per-project concurrency guard: two overlapping deploy dispatches for the
  same project never fire at once (mirrors 2c-2a README's "a future caller
  must serialize invocations for the same target" and 2c part 1's
  `_gitea_sync_lock_for` idiom).

## Non-goals
- **No auto-trigger off the poll.** `_gitea_poll_one`/`_gitea_sync_bg`
  (2c part 1) are unchanged — they still only fast-forward
  `PROJECTS_DIR/<name>`. Nothing added this cycle calls `deploy_run()` from
  that path. This is the single biggest scope cut vs. `docs/BACKLOG.md`'s
  original description — see Summary.
- **No status/history view.** No persisted deploy log, no "last deployed
  at/by/state" display, no polling of in-flight deploy state. The UI's only
  feedback is the synchronous POST's own success/failure result, shown
  once, inline, and gone on next `refresh()`. A future cycle can add
  history if it proves useful.
- **No UI for authoring `deploy-map.json` or placing keys.** Both are
  hand-edited/hand-placed by the operator, exactly like `deploy-target`'s
  own keypair-placement step. `app.py` only reads this file; it never
  writes to it.
- **No new `install.sh` flag / interactive prompt** for configuring a
  target mapping (unlike `--with-deploy-target` itself, which provisions
  the *receiver*). This cycle's `install.sh` changes are limited to
  unconditionally creating `DEPLOY_KEYS_DIR` with the right permissions and
  copying a `.example` map file if none exists yet — see Proposed approach.
- **No multi-target-per-project, no target load balancing.** One project
  name maps to exactly one target entry, matching 2c-2a's own "one `deploy`
  user, one path, one service per target machine" scope.
- **No SSH connection multiplexing (`ControlMaster`/`ControlPersist`).**
  `deploy-target/README.md`'s protocol contract *recommends* it as an
  optimization, not a requirement. This cycle does two independent SSH
  connections (`rsync -e ssh ...` push, then a separate `ssh ... deploy-
  restart`) — simpler, no socket-file directory to create/clean up/handle
  staleness for, and the extra handshake is trivial next to a human's
  own click-to-click latency. Flagged as a deliberate simplification, not
  an oversight.
- **No changes to `deploy-target/`'s own scripts** (2c-2a, already shipped)
  — this cycle is a pure caller against that already-frozen receiver
  contract.
- **No automatic SSH known-hosts handling.** Same implicit precedent
  `host_run()` (host-control, already shipped) already carries: the
  operator must have connected once (or `ssh-keyscan`'d) so `SVC_USER`'s
  `known_hosts` already trusts each target host before the first deploy
  click — not solved differently here, and not silently downgraded to
  `StrictHostKeyChecking=no` either (see Open questions).

## Background / current state
- **2c part 1** (`app/app.py`, "poll-based sync-on-push"): `_gitea_poll_if_due`
  (called from `/status`) throttled-polls Gitea's API per registered repo,
  `_gitea_poll_one` compares the fetched branch SHA against
  `GITEA_REPO_MAP_FILE`'s recorded `remote_sha`, and on a diff spawns
  `_gitea_sync_bg` → `_gitea_sync_run`, which runs
  `sudo -u RUN_USER GITEA_SYNC_SCRIPT <name> <branch>`
  (`scripts/gitea-sync-project.sh`) and records the outcome
  (`synced`/`skipped-dirty`/`skipped-diverged`) back into the map, keyed by
  `owner/repo`. This is the seam this cycle's dispatch point sits next to —
  **not** chained off it (see Non-goals).
- **2c part 2a** (`deploy-target/`, already shipped): a `deploy-target`
  receiver — `deploy-wrapper.sh` (the `authorized_keys` forced command)
  branches on `$SSH_ORIGINAL_COMMAND`: a string starting `rsync --server`
  → `exec rrsync -wo "$DEPLOY_PATH"`; the literal string `deploy-restart` →
  `exec sudo -n deploy-restart.sh`, which validates `DEPLOY_SERVICE_NAME`
  against `^[A-Za-z0-9@_.-]+$` and runs
  `systemctl restart -- "$DEPLOY_SERVICE_NAME"`. Per
  `deploy-target/README.md`'s "Protocol contract": push with
  `rsync -e "ssh -i <key> ..." -a <source>/ deploy@<target>:` (destination
  left **bare** — `rrsync` has already fixed it server-side to
  `DEPLOY_PATH`, and repeating the path here would nest a level deeper or
  error, not point at the real absolute path); then, over the same key,
  `ssh -i <key> deploy@<target> deploy-restart`; a non-zero exit from that
  means the restart itself failed and must be surfaced, not swallowed; the
  receiver adds no locking of its own, so a caller (this cycle) must
  serialize invocations per target.
- **`host_run()`** (`app/app.py`, host-control, already shipped) is the
  closest existing precedent for "dispatch one SSH action synchronously
  from a request thread and return its result": `subprocess.run(["ssh",
  "-i", HOST_CONTROL_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
  f"{HOST_CONTROL_USER}@{HOST_IP}", "sudo .../ai-dev-switchboard-host-
  {action}.sh"], capture_output=True, text=True, timeout=30)`. This cycle's
  `deploy_run()` follows the same shape (synchronous, request-thread,
  `BatchMode=yes` so an unexpected host-key/password prompt fails fast
  instead of hanging) rather than 2c part 1's background-thread-plus-poll
  shape, because a manually clicked one-shot action can and should just
  block the request and return a real result — there's no "list of
  projects to sweep every interval" the way polling has.
- The routing layer is a hand-rolled `BaseHTTPRequestHandler` (no Flask):
  `do_POST` splits `self.path` into `parts` and matches on
  `parts[0]`/`len(parts)`/`parts[n]` (e.g. `parts[0]=="instance" and
  len(parts)==3 and parts[2] in ("on","off")`). New routes follow this
  exact idiom, not a framework decorator.
- `GITEA_REPO_MAP_FILE` (`_load_gitea_repo_map`/`_save_gitea_repo_map_entry`,
  `app/app.py` lines ~604-627) is the direct precedent for a JSON map file
  read with "missing/malformed → `{}`, never crash" tolerance, and
  `_gitea_sync_lock_for` (lines ~639-645) is the direct precedent for a
  per-key, non-blocking `threading.Lock` dict used to drop (not queue) a
  concurrent duplicate dispatch.
- `config/switchboard.env.example` and `install.sh`'s `set_env` idiom is
  how every other optional path/script env var (`GITEA_REPO_MAP_FILE`,
  `HOST_CONTROL_KEY`, etc.) is documented and (for `install.sh`-managed
  ones) written.

## Proposed approach

### 1. `deploy-map.json` schema
New file, default path `/etc/ai-dev-switchboard/deploy-map.json`
(overridable via new `DEPLOY_MAP_FILE` env var, same pattern as
`GITEA_REPO_MAP_FILE`). JSON object keyed by **project name** — the exact
string used elsewhere as `PROJECTS_DIR/<name>` and `instance_names()`'s own
identifier (not `owner/repo` — this map is deliberately independent of
Gitea; a project doesn't need `GITEA_ENABLED` or a Gitea repo-map entry to
have a deploy target, since `deploy_run()` only ever reads
`PROJECTS_DIR/<name>` off disk, whatever put it there):

```json
{
  "my-project": {
    "host": "10.0.0.5",
    "port": 22,
    "user": "deploy",
    "deploy_path": "/opt/myapp",
    "service": "myapp.service",
    "key": "/etc/ai-dev-switchboard/deploy-keys/myapp_ed25519"
  }
}
```

- `host` (required, string) — target machine's address.
- `port` (optional, int, default `22`).
- `user` (optional, string, default `"deploy"` — matches
  `deploy-target/install.sh`'s hardcoded account name, but kept
  configurable rather than hardcoded in `app.py` since nothing in the
  protocol itself requires that exact username).
- `deploy_path` (required, string) — **display/documentation only.** Not
  sent over the wire (the rsync destination is always left bare per the
  protocol contract — `rrsync` has already fixed it server-side). Carried
  in the map purely so a human (confirm dialog, error message, future log
  line) can see where a click actually lands without cross-referencing the
  target's own `deploy-target.env`. Required per explicit product decision
  even though `deploy_run()` doesn't functionally need it.
  Recorded here for the same reason.
- `service` (required, string) — same display-only status as `deploy_path`
  (the restart is always the fixed `deploy-restart` keyword; the target
  resolves the actual service name from its own local
  `deploy-target.env`).
- `key` (required, string, absolute path) — must resolve under
  `DEPLOY_KEYS_DIR` (new env var, default
  `/etc/ai-dev-switchboard/deploy-keys/`); rejected (entry skipped, not a
  hard crash) if it points outside that directory — defense in depth
  against a hand-edited map pointing somewhere unintended, same discipline
  `scripts/gitea-sync-project.sh` already applies to its own arguments.

`config/deploy-map.json.example` ships in the repo as a fully-worked
single-entry template (JSON can't carry `#` comments the way `.env.example`
files do — field-by-field documentation instead lives in
`deploy-target/README.md`'s "Protocol contract" section, cross-referenced
from a short new section there, plus the schema above copied into this
spec).

### 2. Loading (`app/app.py`)
New `_load_deploy_map() -> dict`, mirroring `_load_gitea_repo_map()`
exactly: `open(DEPLOY_MAP_FILE)` / `json.load`, `(OSError, ValueError) →
{}`. Additionally validates each entry (required keys present, `key` path
resolves under `DEPLOY_KEYS_DIR` via `os.path.realpath` prefix-check) and
**drops** (doesn't raise on) any entry that fails validation — one
malformed hand-edited entry must not take down every other project's
Deploy button, same "skip and move on" tolerance `_gitea_poll_if_due`
already applies per-entry. No caching — re-read on every `/status` and
every deploy dispatch (the file is tiny, hand-edited rarely, and this
avoids any staleness question after an operator edits it).

### 3. Dispatch (`app/app.py`)
```python
_deploy_locks_guard = threading.Lock()
_deploy_locks = {}  # name -> threading.Lock, same idiom as _gitea_sync_locks

def deploy_run(name: str) -> tuple[int, str]:
    # Returns (http_status, message) -- 404 no target configured,
    # 409 already in progress, 502 push or restart failed, 200 success.
```
- Look up `name` in `_load_deploy_map()`; missing → `(404, "no deploy
  target configured for this project")`.
- Acquire this project's lock non-blocking (dict keyed by `name`, same
  `_gitea_sync_lock_for`-style guarded-dict idiom); already held →
  `(409, "a deploy for this project is already in progress")`.
- Push:
  `subprocess.run(["rsync", "-e", f"ssh -i {key} -o BatchMode=yes -o
  ConnectTimeout=10 -p {port}", "-a", f"{PROJECTS_DIR}/{name}/",
  f"{user}@{host}:"], capture_output=True, text=True, timeout=60)`
  — trailing slash on source (copy contents, not the directory itself),
  bare destination per the protocol contract. Non-zero exit →
  `(502, f"push failed: {stderr tail}")`; **do not proceed to restart.**
- Restart (only if push succeeded):
  `subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", "-o",
  "ConnectTimeout=10", "-p", str(port), f"{user}@{host}", "deploy-
  restart"], capture_output=True, text=True, timeout=30)`. Non-zero exit →
  `(502, f"push succeeded but restart failed: {stderr tail}")` — surfaced
  distinctly, per the protocol contract's "a non-zero exit means the
  restart itself failed... surface that, don't swallow it."
- Both succeed → `(200, "deployed")`.
- Lock released in a `finally`, always.

### 4. New route
`POST /instance/<name>/deploy` — new branch in `do_POST`, same `parts`
idiom as every other route:
```python
elif parts[0] == "instance" and len(parts) == 3 and parts[2] == "deploy":
    name = parts[1]
    if name not in instance_names():
        return self._json({"error": "unknown instance"}, 404)
    status, msg = deploy_run(name)
    self._json({"ok": status == 200, "message": msg}, status)
```
Goes through the same session/TOTP gate every other mutating POST already
does (`session_totp_ok` check earlier in `do_POST` — unchanged, this route
is added after that gate, not before it).

### 5. `/status` response addition
`_load_deploy_map()` read once per `/status` call (mirrors
`gitea_sync_by_name`'s existing reverse-index pattern at line ~2653). Per
instance, when a map entry exists:
```python
inst["deploy"] = {"host": entry["host"], "deploy_path": entry["deploy_path"],
                  "service": entry["service"]}
```
Deliberately **excludes** `key` (private key path never needs to reach the
client) and `port`/`user` (not needed for the confirm-dialog text below;
omit rather than expose more than the UI uses).

### 6. UI (`app/app.py`'s embedded `PAGE_TEMPLATE`/JS)
- `row()` gains an optional `deploy` param (object or `null`/`undefined`),
  same style as the existing `gitSync` param.
- New `deployRow(name, deploy)`: when `deploy` is present, renders a pill
  button "Deploy" plus an empty `<div class="deploy-msg">` placeholder
  (cleared/filled by the click handler below) — same "plain DOM text node,
  no toast library" idiom `new-project-err`/`err-code` already use
  elsewhere in this file, not a new UI primitive.
- New `async function doDeploy(name, deploy)`:
  1. `if (!confirm('Deploy latest ' + name + ' to ' + deploy.host + ' and
     restart ' + deploy.service + '?')) return;` — a deliberate, lightweight
     confirmation step. Per the user's explicit reasoning for making this
     manual in the first place ("auto-restarting a live remote service off
     an unreviewed push is judged too risky"), a bare click with zero
     confirmation would partly undercut that intent; a native `confirm()`
     costs nothing architecturally and directly serves it.
  2. `fetch('/instance/' + encodeURIComponent(name) + '/deploy', {method:
     'POST', headers: {...}, body: JSON.stringify({code: ...})})` — same
     `code`-carrying shape `performAction`/`actionBody` already use for the
     shared TOTP gate (a 428 mid-flow reuses the existing code-overlay
     path, not a new one).
  3. On resolve, write the result text into that row's `.deploy-msg` (e.g.
     "Deployed" / "Deploy failed: push failed: ...") — no toast, no
     history, gone on next `refresh()` (`refresh()` re-renders the whole
     row and doesn't carry the message forward, matching the "no status/
     history" non-goal).
- `refresh()`'s per-instance `row(...)` call passes `inst.deploy` (or
  `undefined` when absent) as the new argument.

### 7. `install.sh`
Two small, **unconditional** (no new flag/prompt) additions, placed near
the existing `SVC_USER`/`STATE_DIR` setup (around lines 170-180) and the
`ENV_FILE` writing block (around line 379):
- `mkdir -p "$CONFIG_DIR/deploy-keys"; chmod 700 "$CONFIG_DIR/deploy-keys";
  chown "$SVC_USER:$SVC_USER" "$CONFIG_DIR/deploy-keys"` — creates an empty,
  correctly-permissioned directory ready for the operator to hand-place
  key files into; never auto-generates or touches key content.
- `[ -f "$CONFIG_DIR/deploy-map.json" ] || echo '{}' >
  "$CONFIG_DIR/deploy-map.json"` — a safe, functionally-empty default (no
  project shows a Deploy button until the operator hand-edits real entries
  in), **copy-if-absent only**, never touched again on re-run (unlike
  `host.env`'s `set_env`-patched `KEY=VALUE` lines, this is a JSON object
  keyed per-project with no single-key patch semantics — re-running
  `install.sh` must never overwrite an operator's real hand-edited map).
- `set_env "$ENV_FILE" DEPLOY_MAP_FILE "$CONFIG_DIR/deploy-map.json"` and
  `set_env "$ENV_FILE" DEPLOY_KEYS_DIR "$CONFIG_DIR/deploy-keys"` — written
  unconditionally (not gated behind any `WITH_*` flag), matching that this
  feature has no install-time on/off switch, only a data-presence gate.

## Affected areas
- `app/app.py`: `_load_deploy_map`, `_deploy_locks`/`_deploy_lock_for`,
  `deploy_run`, new `DEPLOY_MAP_FILE`/`DEPLOY_KEYS_DIR` env reads (near the
  existing `GITEA_REPO_MAP_FILE` env read), new `/instance/<name>/deploy`
  branch in `do_POST`, `deploy` field added to `/status`'s per-instance
  payload, `PAGE_TEMPLATE` JS (`row`, `deployRow`, `doDeploy`, `refresh`).
- New: `config/deploy-map.json.example`.
- `config/switchboard.env.example`: document `DEPLOY_MAP_FILE`,
  `DEPLOY_KEYS_DIR` (both optional, sane built-in defaults — same style as
  the existing `#GITEA_REPO_MAP_FILE=...` commented example line).
- `install.sh`: unconditional `deploy-keys/` dir creation +
  `deploy-map.json` copy-if-absent + two `set_env` lines (see Proposed
  approach #7). No new `WITH_*` flag.
- `deploy-target/README.md`: add a short new section pointing at this
  cycle's map schema/protocol usage now that a real caller exists (small
  addition — the "Protocol contract" section itself is unchanged, it was
  already written to be exactly what this cycle needed).
- `README.md`: add `config/deploy-map.json.example` awareness where
  relevant (repo-layout tree already lists `deploy-target/`), and one
  bullet under "Security notes" describing the new key-storage directory
  and that dispatch is manual-only.
- `docs/BACKLOG.md`: update item 2's "Shape of the work" auto-deploy
  language to reflect the manual-trigger decision once this cycle ships
  (does not block writing/reviewing this cycle's code, but should not be
  left silently contradicting shipped behavior — flagged here so it isn't
  forgotten at commit time).
- No changes to `deploy-target/*.sh` themselves, or to 2c part 1's
  poll/sync functions beyond adding the unrelated new `/status` field.

This spans backend config-loading + dispatch logic and a UI trigger, but
all of it sits in the same two files (`app/app.py`'s Python half and its
own embedded JS half) plus small, mechanical `install.sh`/example-file
additions — comparable in shape and size to 2c part 1 (which was also one
backend-logic-plus-status-field cycle, not split). **Kept as one spec/one
build cycle**, not split further.

## Edge cases
- **No map entry for a project** — no Deploy button rendered at all (the
  `/status` payload simply omits `deploy` for that instance); if somehow
  POSTed directly anyway (route hit with no entry), `404` with a clear
  message, not a crash.
- **Malformed/incomplete entry** (missing required key, `key` path escaping
  `DEPLOY_KEYS_DIR`) — dropped by `_load_deploy_map`'s per-entry validation;
  behaves exactly like "no map entry" above for that project, other
  projects' entries unaffected.
- **Double-click / overlapping dispatch for the same project** — second
  request while the first is in flight gets `409`; frontend shows "already
  in progress" via the same inline message slot, no queueing.
- **Push succeeds, restart fails** — surfaced as its own distinct message
  (`502`, "push succeeded but restart failed: ..."), not conflated with a
  push failure — an operator reading this needs to know the new code is
  already on the target even though the service didn't pick it up.
- **Unreachable target / bad key / wrong host key** — `BatchMode=yes` on
  both SSH invocations means these fail fast (no interactive
  password/host-key prompt hangs the request thread) and surface as a
  `502` with rsync/ssh's own stderr tail.
- **A deploy click racing 2c part 1's background sync** — `git fetch` never
  touches the working tree (`scripts/gitea-sync-project.sh`), so the only
  real race window is the brief `git merge --ff-only` itself; not
  interlocked this cycle (see Open questions) — flagged as a low-probability,
  low-impact assumption, not blocking.
- **Project directory doesn't exist / was never a real project** (stale map
  entry pointing at a deleted `PROJECTS_DIR/<name>`) — `rsync` fails
  (source path missing) → surfaces as an ordinary `502` push failure, same
  path as any other push failure; no special-casing needed.
- **`install.sh` re-run** — `deploy-keys/` permissions/ownership are
  reasserted (harmless, idempotent); `deploy-map.json` is left completely
  untouched if it already exists (see Proposed approach #7 — this is the
  one place this cycle must NOT follow `host.env`'s patch-in-place idiom).

## Acceptance criteria
- [ ] Given a project with a valid `deploy-map.json` entry, when `/status`
  is polled, then that instance's payload includes a `deploy` object with
  `host`/`deploy_path`/`service` and no `key`.
- [ ] Given a project with no `deploy-map.json` entry, when `/status` is
  polled, then that instance's payload has no `deploy` field.
- [ ] Given a project with a valid entry and a reachable target running
  2c-2a's receiver, when "Deploy" is clicked and confirmed, then
  `PROJECTS_DIR/<name>`'s contents land under the target's `DEPLOY_PATH`
  and `DEPLOY_SERVICE_NAME` restarts, and the UI shows a success message.
- [ ] Given the same setup but the target unreachable (wrong host/port, or
  service down), when "Deploy" is clicked, then the request returns `502`
  within roughly the configured timeouts (no indefinite hang) and the UI
  shows a failure message including some detail, not a blank/generic error.
- [ ] Given a target where the push succeeds but `deploy-restart` exits
  non-zero (e.g. misconfigured `DEPLOY_SERVICE_NAME` on the target), when
  "Deploy" is clicked, then the UI's failure message distinguishes "push
  succeeded, restart failed" from a push failure.
- [ ] Given a Deploy dispatch already in flight for a project, when a
  second Deploy click (or direct second POST) arrives for the same
  project before the first resolves, then the second gets `409` and no
  second concurrent rsync/ssh pair is started.
- [ ] Given a project with no map entry, when `POST
  /instance/<name>/deploy` is sent directly, then the response is `404`
  with a clear error message, and no subprocess is spawned.
- [ ] Given a `deploy-map.json` entry whose `key` path resolves outside
  `DEPLOY_KEYS_DIR`, when `/status` or a deploy dispatch reads the map,
  then that entry is treated as absent (no button, `404` on direct POST)
  — not a crash, not a path traversal.
- [ ] Given `install.sh` is re-run after an operator has already hand-edited
  a real `deploy-map.json`, then that file's contents are unchanged after
  the run (byte-for-byte), while `deploy-keys/`'s permissions/ownership are
  still correctly asserted.
- [ ] Given 2c part 1's poll/sync machinery runs (a Gitea push lands and
  syncs), then `deploy_run()` is never called as a result — confirmed by
  the poll/sync code path containing no reference to the new dispatch
  function (the "manual only" deviation actually holds, not just in
  documentation).

## Open questions
- **Known-hosts trust bootstrapping.** Assumption: same as `host_run()`
  today — the operator must get `SVC_USER`'s `known_hosts` to already trust
  each target host before the first click (e.g. `sudo -u <SVC_USER> ssh
  -i <key> deploy@<target> true` once by hand), documented as a setup step
  in `deploy-target/README.md`'s new section. Not solved differently or
  more automatically here; flagged because it's a real one-time manual step
  an operator could otherwise be surprised by (first deploy click fails
  with a host-key prompt that `BatchMode=yes` turns into an immediate
  error rather than a hang, which at least fails safely/loudly).
- **No interlock with 2c part 1's in-flight sync.** Assumption: acceptable
  given `git fetch` alone never touches the working tree and `merge
  --ff-only` is near-instant — see Edge cases. If this proves to actually
  bite in practice, a cheap follow-up is having `deploy_run()` also try
  (non-blocking) the relevant `_gitea_sync_lock_for(owner_repo)` lock before
  proceeding — not added preemptively.
- **`deploy_path`/`service` are carried in the map purely for display**,
  never used to construct the actual push/restart commands (which are
  fixed by the protocol contract regardless of what's recorded here).
  Assumption: this is fine and intentional, but worth flagging since it
  means a mismatch between the map's `deploy_path`/`service` and the
  target's real `deploy-target.env` values is possible and **silent** —
  the map's copies are documentation, not verified against the target.
  Not solved this cycle (would need a new remote "describe yourself" verb
  the receiver doesn't currently expose); if this becomes a real footgun,
  a future cycle could add a lightweight verification step.
- **Two SSH connections, not connection multiplexing.** Confirmed
  deliberate simplification (Non-goals) — flagging again here in case the
  user wants multiplexing added preemptively rather than only if latency
  or connection overhead ever proves to matter in practice.

## Risk / rollback notes
- **New secret-adjacent surface**: private keys readable by `SVC_USER` at
  `DEPLOY_KEYS_DIR` (mode 700, `SVC_USER`-owned — no new privilege
  boundary, since `SVC_USER` is already the process that reads
  `HOST_CONTROL_KEY` today). `deploy-map.json` itself carries no secrets
  (hostnames, paths, service names, a *path* to a key — never the key
  material).
- **Blast radius of a bad map entry**: bounded by the receiver's own
  already-reviewed 2c-2a restriction (write-only `rrsync` into exactly one
  path, one fixed restart command) — even a maximally wrong `host`/`key`
  pairing can only do what that specific target's receiver already allows,
  not something new invented by this cycle.
- **Manual-only trigger is itself the main risk mitigation** for this
  cycle vs. the original auto-deploy framing: nothing in this cycle can run
  unattended against a live remote service; every dispatch traces back to
  one explicit, confirmed UI click by an authenticated, TOTP-verified
  session.
- **Rollback**: revert the `app.py`/`install.sh`/example-file changes;
  delete `/etc/ai-dev-switchboard/deploy-map.json` and
  `/etc/ai-dev-switchboard/deploy-keys/` on any box that picked them up via
  `install.sh` re-run — none of this touches `PROJECTS_DIR` or any existing
  Gitea/2c-part-1 state, and 2c-2a's receiver-side install is entirely
  unaffected (this cycle only ever acts as a caller against it).
