# Implementation: Local git hosting UI + CI/CD (Gitea) — part 2c, part 1: poll-based sync-on-push

(2a/2b's own implementation notes are preserved in git history and in this
project's `docs/BACKLOG.md` — `git show dcc582b:docs/implementation.md` for
2a, the previous commit's `docs/implementation.md` for 2b. This file now
documents 2c part 1 only, per this cycle's `docs/spec.md` — the *revised*,
polling-based version, not the earlier webhook design the user rejected.)

## Summary

Added a throttled Gitea-API poll (`_gitea_poll_if_due`/`_gitea_poll_one`,
piggybacked on the existing `/status` handler, independently throttled to
`GITEA_POLL_INTERVAL_SECONDS`, default 45s) that detects when a push has
landed on a Gitea-backed project's repo from somewhere else, plus a new
low-privilege script (`scripts/gitea-sync-project.sh`, run as `RUN_USER`,
never root) that safely fast-forwards `PROJECTS_DIR/<name>` — fetch, then
`git merge --ff-only` only if the working copy is clean *and* local `HEAD`
is an ancestor of the new remote ref, otherwise skip and record why. No new
listener, no new secret, no Docker networking changes — everything reuses
2b's existing `_gitea_api()` helper and the existing loopback
`127.0.0.1:$GITEA_PORT` path. A new `GITEA_REPO_MAP_FILE` (JSON,
`SVC_USER`-owned, same directory as `DESC_CACHE_FILE`) resolves
`owner/repo` -> local project name/branch/sync-state, written by
`create_project()` (best-effort, non-fatal) and read/updated by the poll
machinery.

Followed the spec's "Proposed approach" code shapes essentially verbatim —
`_gitea_poll_if_due`/`_gitea_poll_one` match the spec's own pseudocode
almost line for line. The one structural addition beyond what the spec's
pseudocode literally showed is `_gitea_sync_run` (a synchronous, directly
testable function that does the actual `subprocess.run` + repo-map update),
factored out from `_gitea_sync_bg` (which now just acquires the per-project
lock and spawns `_gitea_sync_run` on a background thread) — see "Key
decisions / tradeoffs" for why.

## Changes by file

- **`app/app.py`**:
  - New config: `GITEA_SYNC_SCRIPT`, `GITEA_REPO_MAP_FILE`,
    `GITEA_POLL_INTERVAL_SECONDS` (env-overridable constant, default `45`,
    not written by `install.sh` — see "Deviations from spec", Open
    Question #5 resolved as specced).
  - New `_load_gitea_repo_map()` / `_save_gitea_repo_map_entry()` — same
    tmp-file-then-`os.replace()` idiom as `_save_desc_cache`, guarded by a
    new `_gitea_map_lock` (needed here, unlike `_save_desc_cache`, because
    multiple threads — `create_project()` and every in-flight sync attempt —
    can call this concurrently for *different* projects).
  - New `_gitea_sync_lock_for(owner_repo)` — a per-project non-blocking
    lock dict (`_gitea_sync_locks`/`_gitea_sync_locks_guard`), mirroring the
    `_desc_pending` per-name-set idiom.
  - New `_gitea_sync_run(name, branch, owner_repo, observed_sha)` — runs
    `GITEA_SYNC_SCRIPT` via `sudo -u $RUN_USER`, records the outcome
    (`synced`/`skipped-dirty`/`skipped-diverged`/`no-such-project`) plus
    `sync_at`/`remote_sha` into the repo-map on success (exit 0); a
    non-zero exit or a subprocess-level exception leaves the repo-map
    untouched entirely (see "Key decisions").
  - New `_gitea_sync_bg(name, branch, owner_repo, observed_sha)` — the
    exact call site `_gitea_poll_one` uses (matches the spec's pseudocode
    verbatim); acquires the per-project lock non-blocking, and if acquired,
    spawns `_gitea_sync_run` on a daemon thread and returns immediately
    (mirrors `_generate_description_bg`'s "return fast" idiom).
  - New `_gitea_poll_lock`/`_gitea_poll_last_at`, `_gitea_poll_if_due`,
    `_gitea_poll_one` — verbatim to the spec's own pseudocode.
  - `create_project()`: one new best-effort call
    (`_save_gitea_repo_map_entry(f"{owner}/{repo_name}", name, "main")`)
    right after the existing successful-clone path, before `return True,
    ""` — wrapped in `try/except OSError: pass`.
  - `do_GET`'s `/status` handler: calls `_gitea_poll_if_due(gitea_on)`
    right after `gitea_on` is computed; loads the repo-map once per
    request and reverse-indexes it by `name` to attach an optional
    `gitea_sync: {"state": ..., "at": ...}` field to each instance row
    when a repo-map entry exists for that project (absent, not
    present-but-null, otherwise).
  - Frontend `<script>` (`render_page()`): new `gitSyncSuffix(gitSync)`
    helper and a new `gitSync` parameter on `row()` (appended as the last
    positional arg, so every existing call site except the instance-row one
    is unaffected) — appends a small suffix to the row's existing `.sub`
    text (` · sync skipped: local changes` / ` · sync skipped: local
    commits ahead`) only for the two skip states; `synced`/absent add
    nothing. No new badge/icon system, per the spec's own UI note.
- **`scripts/gitea-sync-project.sh`** (new) — the low-privilege (`RUN_USER`,
  not root) sync script: re-validates `<name>`/`<branch>`, `git fetch
  origin "$branch"`, dirty-check (`git status --porcelain`), fast-forward-
  safety check (`git merge-base --is-ancestor HEAD "origin/$branch"`),
  otherwise `git merge --ff-only`. Prints exactly one of `synced` /
  `skipped-dirty` / `skipped-diverged` / `no-such-project` and always exits
  0 on a defined outcome; exits 1 (argv/config validation failure) or lets
  `git fetch` itself fail loudly (`set -e`) on an undefined one.
- **`install.sh`**:
  - New `ALL=($RUN_USER)` sudoers line for
    `ai-dev-switchboard-gitea-sync-project.sh` (gated on
    `WITH_GIT_HOSTING`), grouped with the other `ALL=($RUN_USER)` rules —
    not `ALL=(root)`, since this script never needs root.
  - `install -m 755 .../gitea-sync-project.sh
    /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh` inside the
    `WITH_GIT_HOSTING` block.
  - Two new `set_env` calls: `GITEA_SYNC_SCRIPT`, `GITEA_REPO_MAP_FILE`
    (`$STATE_DIR/gitea-repo-map.json`, same directory `DESC_CACHE_FILE`
    already uses). `GITEA_POLL_INTERVAL_SECONDS` deliberately **not**
    written (see "Open questions" #5 in `docs/spec.md`) — it has a
    built-in default in `app.py` and is documented as an optional
    commented-out override in `config/switchboard.env.example` instead.
  - No changes to `config/gitea-docker-compose.yml` or any Docker
    networking — none needed under the revised (polling) design.
- **`config/switchboard.env.example`** — new commented block documenting
  `GITEA_SYNC_SCRIPT`, `GITEA_REPO_MAP_FILE`, `GITEA_POLL_INTERVAL_SECONDS`,
  matching the existing `GITEA_*` block's style.
- **`docs/GIT_HOSTING.md`** — replaced the old "Auto-sync... [not
  included]" bullet under "What's NOT included (yet)" with a new "Auto-sync
  of `PROJECTS_DIR/<name>` when someone pushes from elsewhere" section
  describing the polling mechanism, the exact safety steps, and both honest
  caveats (latency up to `GITEA_POLL_INTERVAL_SECONDS`; the two skip cases
  still need a manual `git pull`). Also added a "Projects not created
  through the '+ New project' flow" bullet to "What's NOT included (yet)"
  (repo-map has no retroactive-linking mechanism — matches the spec's
  Non-goals) and a forward pointer to 2c part 2 (CI/CD auto-deploy).
- **`README.md`** — **not changed**. Its existing Gitea mention doesn't
  reference the old "no auto-sync" gap at all (checked directly), so
  there's nothing there to update per the spec's own conditional ("if it
  references the old gap, updated to match").
- **Tests** (no real Docker/network/Gitea-server calls anywhere):
  - `tests/test_gitea_poll.py` (new, 26 tests) —
    `GiteaRepoMapTests` (load/save round-trip, tmp-file idiom, corrupt-file
    handling), `GiteaPollIfDueTests` (throttling via a mocked
    `_gitea_poll_last_at`, `GITEA_ENABLED`/`gitea_on` gating, one
    `_gitea_api` call per repo-map entry when due), `GiteaPollOneTests`
    (SHA-match skip, SHA-diff dispatch with exact args, non-200/
    `ConnectionError`/missing-`commit.id` all skipped without raising),
    `GiteaSyncRunTests` (mocked `subprocess.run` — `synced`/
    `skipped-dirty`/`skipped-diverged` all update the repo-map including
    `remote_sha`; a non-zero exit or a raised exception leaves the
    repo-map untouched), `GiteaSyncBgConcurrencyTests` (a second dispatch
    while the per-project lock is held is dropped; the lock is released
    after a completed run so the next dispatch can proceed).
  - `tests/test_gitea_sync_project.py` (new, 10 tests) — exercises the
    *real* `gitea-sync-project.sh` against real temporary local git repos
    (a plain local-path "origin" stands in for Gitea): clean
    fast-forwardable -> `synced` (file lands, HEAD matches origin exactly),
    already-up-to-date -> `synced` as a no-op, dirty working tree ->
    `skipped-dirty` (byte-for-byte-intact uncommitted edit verified),
    diverged local commit -> `skipped-diverged` (commit still reachable
    from `HEAD` afterward, verified two ways — a genuinely diverged history
    and a local-ahead-only case), missing/non-git destination ->
    `no-such-project`, plus arg-validation cases (wrong count, invalid
    name, invalid branch). No `sudo`/root needed, as the spec predicted —
    the script never crosses a privilege boundary internally.
  - `tests/test_gitea.py` (extended) — two new tests in
    `CreateProjectGiteaTests` (`test_happy_path_writes_repo_map_entry_with_null_sync_fields`,
    `test_repo_map_write_failure_does_not_fail_create_project`) plus two new
    tests in `GiteaEndpointTests`
    (`test_status_includes_gitea_sync_for_a_project_with_a_repo_map_entry`,
    `test_status_omits_gitea_sync_for_a_project_without_a_repo_map_entry`)
    covering the exact presence/absence JSON-shape acceptance criterion.
    `setUp`/`tearDown` in `CreateProjectGiteaTests` now also redirect
    `GITEA_REPO_MAP_FILE` to a per-test temp path.

## Key decisions / tradeoffs

- **`_gitea_sync_run` factored out of `_gitea_sync_bg`, beyond what the
  spec's pseudocode literally showed.** The spec's own "Proposed approach"
  code block calls `_gitea_sync_bg(...)` directly from `_gitea_poll_one`
  with no visible `Thread(...)` wrapper, while its prose says `_gitea_sync_bg`
  is itself "spawned from `_gitea_poll_one`, off the request thread." Read
  literally together, this means the *call site* stays exactly as specced
  (a plain call, easy to mock and assert-called-with in
  `GiteaPollOneTests`), while `_gitea_sync_bg` internally owns spawning the
  actual background thread. To keep the acceptance criterion "verified via
  a mocked subprocess call in `tests/test_gitea_poll.py`" testable
  *synchronously* (no thread-join races in test code), the part that
  actually calls `subprocess.run` and updates the repo-map is its own
  function, `_gitea_sync_run`, which `_gitea_sync_bg` spawns on a thread
  after acquiring the per-project lock. This is an implementation-detail
  addition, not a deviation from the spec's actual behavior — every
  acceptance criterion and every piece of the spec's own prose ("per-project
  non-blocking lock", "spawned... off the request thread", "returns fast")
  is satisfied exactly as described.
- **A non-zero exit / raised exception from `gitea-sync-project.sh` leaves
  the repo-map's `remote_sha` untouched**, rather than recording some
  generic "error" state with the observed SHA. The spec's own script steps
  only define three "touched the repo-map" outcomes (`synced`,
  `skipped-dirty`, `skipped-diverged`) plus `no-such-project`, all normal
  exit-0 outcomes; a script-level failure (bad args, or `git fetch` itself
  failing — e.g. Gitea restarting mid-poll) isn't one of those. Recording
  `remote_sha` on a failure would have silently suppressed all future retry
  attempts for that push (since `_gitea_poll_one`'s SHA-compare would then
  see no diff); leaving it untouched means the next poll interval still
  sees the same diff and retries automatically — the same "safe to rerun,
  self-healing" framing the spec applies everywhere else (the SHA-snapshot
  race in "Open questions" #2, the lock-busy-drop case).
- **`gitea_sync_by_name` reverse-index rebuilt on every `/status` call**
  (one JSON file read, small N) rather than cached in memory — matches the
  spec's own "just iterate it" framing for `instance_names()`'s own
  filesystem scan, and keeps `/status` simple (no cache-invalidation logic
  needed when `_gitea_sync_run` updates the file from a different thread).

## Deviations from spec

None. Every piece of the spec's "Proposed approach" (poll mechanism,
throttle, repo-map file shape and write/read points, the sync script's
5-step safety logic, per-project lock, `install.sh` diff shape) was
implemented as specified; the one addition (`_gitea_sync_run`) is an
internal factoring, not a behavioral or interface deviation — see "Key
decisions" above. All 10 acceptance criteria were implemented and are
covered by the tests listed above. `docs/BACKLOG.md` was intentionally left
unchanged — the spec's own "Affected areas" list doesn't include it, and
tracking backlog-item status across cycles is product-manager's file to
update, not developer's.

## Post-review fix: per-entry poll isolation (should-fix from docs/test-review.md)

The reviewer found that `_gitea_poll_one` raised an uncaught `AttributeError`
when Gitea returned a 200 whose body wasn't a JSON object (`resp.get("commit")`
on a non-dict `resp`), and that the calling loop in `_gitea_poll_if_due` had
no per-entry exception handling — so one malformed response silently killed
polling for every other registered project in that pass, not just the
malformed one. Fixed two ways: `_gitea_poll_one` now checks
`isinstance(resp, dict)` before touching `resp.get(...)` (closes the specific
reported cause), and `_gitea_poll_if_due`'s loop now wraps each
`_gitea_poll_one` call in its own `try/except Exception: pass` (closes the
class of bug generally, not just this one instance of it) — matching this
feature's own accepted "availability nit, never a correctness/safety issue"
risk tolerance from `docs/spec.md`'s Risk notes.

Two new regression tests in `tests/test_gitea_poll.py`:
`test_non_dict_200_response_skipped_without_raising` (direct repro) and
`test_one_malformed_entry_does_not_stop_the_rest_of_the_pass` (asserts a
second, healthy repo-map entry still gets polled in the same pass despite the
first one's malformed response). Verified load-bearing: reverted both fix
edits, confirmed both new tests fail with the exact reported
`AttributeError`, restored the fix, confirmed the full suite (215/215) passes
clean.

## Known limitations

- Same latency/skip-case tradeoffs the spec explicitly accepts as
  non-goals: sync latency is bounded by `GITEA_POLL_INTERVAL_SECONDS` (up
  to ~45s), not instant; a dirty or diverged working copy still needs a
  manual `git pull`; there's no manual "check now" UI action this cycle.
- A repo-map entry for a project later removed by hand (`PROJECTS_DIR/<name>`
  deleted outside this app) is never cleaned up — the sync script's own
  `no-such-project` no-op makes this harmless, just a small steady-state
  extra GET per poll interval forever, exactly as the spec's "Edge cases"
  section accepts.
- `install.sh`'s pre-existing `STATE_DIR` (`/var/lib/ai-dev-switchboard`)
  ownership pattern is unchanged by this cycle (matches `DESC_CACHE_FILE`'s
  own existing treatment exactly, per the spec's explicit instruction) — not
  a new issue introduced here, and out of this cycle's scope to fix.

## How to verify locally

```bash
# Full suite (Python)
python3 -m unittest discover -s tests -v

# Just this cycle's new/changed test files
python3 -m unittest tests.test_gitea_poll tests.test_gitea_sync_project tests.test_gitea -v

# Frontend (existing regression suite — verifies row()/refresh() changes
# didn't break the singleton-toggle state machine)
node tests/test_singleton_toggle_frontend.js

# Script syntax
bash -n scripts/gitea-sync-project.sh
bash -n install.sh
```

All of the above were run in this implementation session: 213/213 Python
tests pass (173 pre-existing + 40 new/extended in this cycle),
15/15 existing frontend tests still pass unmodified, both scripts parse
cleanly, and `python3 -m py_compile app/app.py` succeeds.

To see the new UI treatment without a live Gitea instance, the rendered
`<script>` was extracted from `render_page()` and exercised directly (Node
`vm`, no DOM): `row('proj', false, null, 'inst', 'proj', '', null, false,
null, undefined, undefined, {state:'skipped-dirty', at: 123})` renders
`<div class="sub">stopped · sync skipped: local changes</div>`; the same
call with `state:'synced'` or `gitSync` omitted renders the plain
`running`/`stopped` text unchanged.

For a full live round trip (not done in this session — would need a real
Gitea instance): create a project via "+ New project", push a commit to
its repo from a second clone or Gitea's own web UI, wait up to
`GITEA_POLL_INTERVAL_SECONDS`, and confirm `PROJECTS_DIR/<name>` picks up
the change and the project's `/status` row's small `sync skipped: ...`
suffix appears/disappears correctly across dirty/diverged/clean scenarios.
