# Spec: Local git hosting UI + CI/CD (Gitea) — part 2c, part 1: poll-based sync-on-push
### (revised — polling instead of webhook; see "Revision note" below)

## Revision note
The previous version of this spec proposed a Gitea webhook receiver (`POST
/gitea-webhook`, HMAC-verified) reached from inside Gitea's own Docker
container via a pinned bridge-network subnet/gateway and a second
`ThreadingHTTPServer` listener in `app.py`. **Rejected by the user**, with
this reasoning: it's a genuinely new, real inbound attack surface (even
though signature-verified) plus real Docker networking complexity (pinning a
subnet, running a second listener bound to a bridge gateway address) — and
the previous spec's own Open Questions/Risk sections already flagged both the
subnet choice and the whole networking resolution as unverified-live and
somewhat arbitrary. **Decision: switch to polling.** `app.py` periodically
asks Gitea's own REST API (already reachable over the existing, already-
published loopback `GITEA_PORT` — the same direction 2b's `_gitea_api()`
already uses and already works) whether a project's default branch has moved,
and only then runs the exact same safe-sync logic this spec already worked
out. Everything below is rewritten around that; the sync-safety logic itself
(fetch, fast-forward-only if clean+ancestor, otherwise skip) is **unchanged**
from the previous version — only *how `app.py` learns a push happened*
changes.

## Summary
Add periodic Gitea-API polling to `app/app.py` (no new route, no new
listener, no new secret, no Docker networking changes) plus the same
low-privilege sync script the previous version of this spec already designed,
so that when a push lands on a repo `create_project()` created (2b) from
**somewhere other than that repo's own `PROJECTS_DIR/<name>` working copy** —
another contributor via Gitea's web UI, a merged PR, a second agent session
elsewhere — that working copy is kept in sync automatically, safely (never
destroying uncommitted or unpushed local work). This is explicitly **part 1
of 2c only**: the polling mechanism and the sync mechanism itself. CI/CD
auto-deploy to a separate target machine (part 2) is a later cycle that
reuses this cycle's poll-detected-a-change dispatch point, not its own thing.

## Goals
- A new, throttled background check — piggybacked on the existing `/status`
  request handler (same "opportunistic work on an already-frequent request"
  precedent `_reap_dead_state()` already established, see "Proposed
  approach") but internally rate-limited to its own longer interval
  (`GITEA_POLL_INTERVAL_SECONDS`, default 45s), independent of the frontend's
  fast 4-second `/status` poll cycle — asks Gitea's REST API, for every
  project `create_project()` has ever registered a repo-map entry for,
  whether that project's default branch has moved since the last time it was
  checked.
- **No new HTTP-calling code**: reuses 2b's existing `_gitea_api()` helper
  (same `127.0.0.1:$GITEA_PORT`, same `Authorization: token $GITEA_API_TOKEN`
  header) against `GET /repos/{owner}/{repo}/branches/{branch}` — verified
  against Gitea's real source (not assumed) to return the branch tip's commit
  SHA directly in a single call (`resp["commit"]["id"]`), so no second
  "look up the default branch first" call is needed. See "Background /
  current state" for the verification.
- **Cheap-check-gates-expensive-work**: the remembered last-seen remote SHA
  (stored per project in `GITEA_REPO_MAP_FILE`) is compared against the
  freshly polled SHA; the actual `git fetch` + safety checks (a `sudo -u
  $RUN_USER` subprocess call) only run when they differ. Checking N projects'
  SHAs is one cheap loopback HTTP GET each; it must not turn into N git
  fetches every 45 seconds when nothing has changed.
- The exact same resolved answer the previous version of this spec already
  worked out for "what does a detected push actually do to
  `PROJECTS_DIR/<name>`, given a live agent session might have uncommitted
  work there": **fetch, then fast-forward-only if (a) the working tree is
  clean and (b) local HEAD is a strict/equal ancestor of the new remote ref —
  otherwise skip entirely, record why, and surface it in the UI.** Never
  `git reset --hard`. Unchanged from before; see "Proposed approach: the sync
  decision."
- **No new secret, no new listener, no Docker networking changes at all** —
  the previous version's `GITEA_WEBHOOK_SECRET`/`GITEA_WEBHOOK_BIND_ADDR`,
  the pinned Compose bridge subnet, and the second `ThreadingHTTPServer` are
  all gone; `GITEA_API_TOKEN` (already exists, from 2b) is the only
  credential this cycle needs, and it's already flowing through `_gitea_api()`
  today.
- `docs/GIT_HOSTING.md`'s "What's NOT included (yet)" section updated to move
  sync-on-push out of the gap list and describe the new (polling-based)
  behavior honestly, including both its skip cases (dirty/diverged) and its
  latency (up to `GITEA_POLL_INTERVAL_SECONDS`, not instant).

## Non-goals
- **CI/CD auto-deploy to a separate target machine** (2c part 2) — a later
  cycle. It should be able to add itself as a second action at the point
  where a poll cycle notices a project's remote SHA moved (see "Proposed
  approach: leaving 2c part 2 a clean extension point"), not build its own
  detection mechanism from scratch.
- **Syncing anything other than the project's default branch (`main`)** —
  `create_project()` already always creates repos with `default_branch:
  "main"`; the poll only ever queries the one branch recorded in that
  project's repo-map entry. No per-project configurable "which branch to
  track," and — a structural side-effect of polling a named branch instead of
  receiving a payload for whatever ref was pushed — there is no "push to the
  wrong branch" case to filter at runtime the way a webhook payload would
  have needed to.
- **Any conflict resolution, merge UI, or forced overwrite path.** When a
  fast-forward isn't safely possible, the working copy is left exactly as it
  was and the operator/agent is expected to `git pull`/resolve by hand — same
  fallback `docs/GIT_HOSTING.md` already documents for "no auto-sync at all"
  today, just now scoped to the smaller set of cases where it's actually
  unsafe rather than always. Unchanged from before.
- **Retroactively adding a repo-map entry for projects not created through
  this Gitea flow** — a manually `git init`'d project, or an uploaded project
  (`docs/spec.md`'s folder-upload feature) with a hand-added Gitea remote, has
  no owner/repo → name mapping and is not covered by polling in this cycle. A
  future "link an existing Gitea repo to a project" flow could add one; out
  of scope here.
- **Near-real-time sync.** Polling means sync latency after a real push is
  now bounded by `GITEA_POLL_INTERVAL_SECONDS` (up to ~45s by default), not
  sub-second the way a webhook would have been. This is an explicit, accepted
  tradeoff for removing the webhook's inbound attack surface and Docker
  networking complexity entirely — not an oversight. No "poll immediately on
  demand" UI action is added this cycle either (e.g. a manual "check now"
  button) — see "Open questions" if that's judged worth adding.
- **A general "ask the user" notification inbox.** Skipped-sync state is
  surfaced as one more small field on the existing per-project `/status`
  row (a badge/tooltip, exact visual left to `docs/design.md`) — not a new
  notification subsystem. That's backlog item 6's territory. Unchanged from
  before.
- **Deleting the repo-map entry when a project is later removed.** This
  codebase has no "delete project" feature at all yet (checked: no such route
  exists in `do_POST`); nothing new to clean up here beyond what's already
  true. A stale mapping entry pointing at a since-removed
  `PROJECTS_DIR/<name>` is handled as a harmless no-op (see "Edge cases").
  Unchanged from before.

## Background / current state

### 2b, already shipped and live in `app/app.py` (verified by reading the real code, not the historical spec)
`create_project()` (`app/app.py:576`) already: validates the name, checks
`GITEA_ENABLED`/`GITEA_API_TOKEN`/Gitea's running status, calls `POST
/user/repos` via `_gitea_api()` (`app/app.py:549`), then hands off to the
privileged, root-run `scripts/new-project-from-gitea.sh` (installed to
`/usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh`, sudoers-scoped)
to clone the new repo into `PROJECTS_DIR/<name>` as `RUN_USER`. On a clone
failure it best-effort `DELETE`s the just-created repo. This cycle's new code
slots in right after that flow's existing success path — no changes to the
failure/cleanup branch.

2b's own spec (`docs/spec.md` at the time, now preserved at commit `5a59d21`)
explicitly deferred sync-on-push here, with this reasoning (quoted from that
file's "Sync-on-push — deferred to 2c" section, not re-derived): the *old*
git-shell/bare-repo flow's `post-receive` → `project-sync.sh` hook existed
because that flow expected the primary pusher to be something other than
`PROJECTS_DIR/<name>` itself (a developer's laptop, or CI); under the new
Gitea-backed model `PROJECTS_DIR/<name>` **is** the primary working copy an
agent session commits and pushes from directly, so "someone else pushed to
this repo" is now the minority case, not the common one. This reasoning is
unaffected by the webhook→polling pivot — it's about *whether* sync-on-push
is needed at all, not *how* a push is detected.

### The deleted `project-sync.sh` / `new-dev-instance.sh` hook (read via `git show dcc582b:...`, not guessed)
The old flow's version of this feature, for reference — what it did and didn't
have to worry about:
- `new-dev-instance.sh` installed a `post-receive` hook on the **bare** repo
  (git-side, synchronous, in-process on every push) that ran `sudo -u
  $RUN_USER /usr/local/bin/ai-dev-switchboard-project-sync.sh <name>` for any
  push to `main`.
- `project-sync.sh` itself: `git fetch origin main && git reset --hard
  origin/main` — unconditional, no dirty-check, no ancestor-check. Safe *only*
  because that flow's own model made `PROJECTS_DIR/<name>` a passively-synced
  mirror nobody was expected to edit directly — exactly the assumption this
  cycle's Gitea-backed model breaks (see 2b's own spec quote above). A blind
  `git reset --hard` today would silently destroy any uncommitted work a live
  coding-agent session has sitting in that working copy — this is the "real,
  hard product/technical question" this spec has to resolve, not carry
  forward unmodified.
- This project's own new mechanism is a considered continuation, not a
  ground-up reinvention: same two-step shape (root-adjacent low-privilege
  script does `git fetch` against `origin/<branch>` in the working copy), same
  "safe to rerun" framing — the actual novelty is entirely in *when* it's safe
  to apply what was fetched (see "Proposed approach: the sync decision") —
  and, as of this revision, also in *how the script gets invoked at all*
  (polled, not hook-triggered).

### `docs/GIT_HOSTING.md`'s current "What's NOT included (yet)" (user-facing gap description)
> Auto-sync of `PROJECTS_DIR/<name>` when someone pushes to the same repo from
> somewhere else (another contributor via Gitea's own web UI, a merged PR, a
> second agent session elsewhere)... For now: `git pull` manually in the
> working copy if you know something else pushed to it.

This is precisely the gap this cycle closes — for the common/safe case. The
doc rewrite (see "Affected areas") needs to say plainly that (a) the *unsafe*
case (dirty working copy, or local commits not yet pushed) still requires a
manual `git pull`/resolve, and (b) sync now happens within
`GITEA_POLL_INTERVAL_SECONDS` of the push landing, not instantly — neither
silently overclaimed.

### Gitea's real branch-lookup API shape (verified against Gitea's own source, not assumed)
- **`GET /repos/{owner}/{repo}/branches/{branch}` returns the branch tip's
  commit SHA directly, no second call needed.** Confirmed against
  `go-gitea/gitea`'s own `modules/structs/repo_branch.go`: the `Branch`
  struct's `Commit` field is a `*PayloadCommit` (from
  `modules/structs/hook.go`), whose `ID string` field (JSON tag `"id"`,
  documented as "sha1 hash of the commit") holds the SHA. So the response
  shape is `{"name": "main", "commit": {"id": "<sha>", ...}, "protected":
  ..., ...}` — this spec reads `resp["commit"]["id"]`. This is the same
  struct Gitea's push-webhook payload would have used for its own commit SHA
  fields (`before`/`after`), just reached by polling a branch instead of
  receiving a payload.
- **Auth and transport are identical to every other `_gitea_api()` call
  2b already makes** — `Authorization: token $GITEA_API_TOKEN`,
  `127.0.0.1:$GITEA_PORT`, same status/error handling (`ConnectionError` on
  unreachable, raw `(status, json)` tuple otherwise). No new HTTP-calling
  code is needed for this cycle at all — just a new call site using the
  helper that already exists.
- **A missing/renamed/deleted repo or branch on the Gitea side** returns a
  non-2xx status (404 in the common case) — handled as "skip this entry this
  cycle, retry next interval," not a crash or a fatal error (see "Edge
  cases").

### The previous version's "reaching `app.py` from inside Gitea's container" problem no longer exists
The rejected webhook approach needed Gitea's own container to reach *out* to
`app.py` — a direction that does not work by default (a Docker bridge
network's `127.0.0.1` is the container's own loopback, not the host's;
`app.py`'s `LISTEN_HOST=127.0.0.1` binding is unreachable from a bridge
network by design) and that the previous spec resolved with a pinned
subnet/gateway and a second listener socket, the entire piece of complexity
the user rejected. Polling only ever uses the **opposite, already-working**
direction: `app.py` (running on the host) calling *into* Gitea's container
over `127.0.0.1:$GITEA_PORT` — which already works today, unchanged, because
`config/gitea-docker-compose.yml` publishes that port to the host's loopback
(`"127.0.0.1:${GITEA_PORT}:3000"`) and 2b's `_gitea_api()`/clone script
already rely on exactly this path. This isn't a smaller version of the
previous networking problem — it's a different direction that was already
solved and already shipped in 2b, so there is nothing left to design or pin
here at all.

## Proposed approach

### The poll mechanism — piggybacked on `/status`, throttled on its own interval
Modeled on `_reap_dead_state()`'s already-established precedent ("opportunistic
work on an already-frequent request" rather than a dedicated background
thread) — but with one addition `_reap_dead_state()` itself doesn't need:
`_reap_dead_state()`'s own work is cheap in-memory bookkeeping safe to redo on
literally every 4-second `/status` tick, whereas polling Gitea's API is a real
network call per registered project, which would be wasteful (and, with
enough projects, slow) to redo every 4 seconds. So this gets its own,
independent throttle:

```python
GITEA_POLL_INTERVAL_SECONDS = int(os.environ.get("GITEA_POLL_INTERVAL_SECONDS", "45"))
# Same "env-overridable constant, not written by install.sh unless there's a
# real reason to" style as UPLOAD_STAGING_TTL_SECONDS (app/app.py:89) -- not
# every GITEA_* knob needs a switchboard.env.example line item; see "Open
# questions" for the alternative.

_gitea_poll_lock = threading.Lock()
_gitea_poll_last_at = 0.0

def _gitea_poll_if_due(gitea_on: bool) -> None:
    global _gitea_poll_last_at
    if not GITEA_ENABLED or not gitea_on:
        return  # feature off, or Gitea itself isn't currently running --
                 # don't hammer _gitea_api with ConnectionErrors
    if time.time() - _gitea_poll_last_at < GITEA_POLL_INTERVAL_SECONDS:
        return
    if not _gitea_poll_lock.acquire(blocking=False):
        return  # another /status request is already mid-poll-pass
    try:
        if time.time() - _gitea_poll_last_at < GITEA_POLL_INTERVAL_SECONDS:
            return  # lost the race -- someone else just finished a pass
        _gitea_poll_last_at = time.time()
        for owner_repo, entry in _load_gitea_repo_map().items():
            _gitea_poll_one(owner_repo, entry)
    finally:
        _gitea_poll_lock.release()

def _gitea_poll_one(owner_repo: str, entry: dict) -> None:
    branch = entry.get("branch", "main")
    try:
        status, resp = _gitea_api("GET", f"/repos/{owner_repo}/branches/{branch}")
    except ConnectionError:
        return  # transient; retried next interval
    if status != 200:
        return  # repo/branch renamed or deleted at the Gitea side; retried
                 # next interval in case it comes back (e.g. Gitea mid-restart)
    remote_sha = (resp.get("commit") or {}).get("id", "")
    if not remote_sha or remote_sha == entry.get("remote_sha"):
        return  # nothing new since the last time this was checked
    _gitea_sync_bg(entry["name"], branch, owner_repo, remote_sha)
```

- Called from `do_GET`'s `/status` handler, right after `gitea_on` is
  computed (`app/app.py:2456`-ish) — reuses that already-computed value
  rather than issuing its own separate `gitea_run("status")` call.
- `_gitea_poll_one`'s early return when `remote_sha == entry.get("remote_sha")`
  is what keeps this from turning into a `git fetch` per project every poll
  cycle: the (cheap) HTTP GET always runs every due cycle for every
  registered project, but the (comparatively expensive) `sudo -u $RUN_USER`
  fetch-and-check subprocess only runs when something actually changed.
- `entry["remote_sha"]` is written back (to whatever SHA was just observed)
  after **every** sync attempt `_gitea_sync_bg` makes — whether it resulted
  in `synced`, `skipped-dirty`, or `skipped-diverged` — not only on a
  successful sync. This is deliberate: once a push has been *seen and acted
  on* (even if the action was "skip, and say why"), there's no reason to
  re-attempt a `git fetch` against the same unchanged remote SHA every 45
  seconds; the next *new* push is what should trigger the next attempt. A
  skipped project only gets re-checked for real once a genuinely new push
  moves the SHA again — matching the same "safe to rerun, but no reason to
  rerun needlessly" framing the deleted `project-sync.sh` already had.
- **A real but harmless race**: the SHA `_gitea_poll_one` observes and the
  SHA `gitea-sync-project.sh`'s own later `git fetch` actually lands on can
  differ, if another push happens in between. This is accepted, not treated
  as a bug: the fetch always picks up whatever is actually current regardless
  of which SHA triggered it, and if a *further* push landed in that window,
  the next poll cycle will notice the (now further-moved) remote SHA still
  doesn't match what got recorded and will trigger another attempt — this
  converges within one more poll interval, never loses a push, and never
  double-applies anything unsafely (each attempt still goes through the full
  dirty-check/ancestor-check independently). See "Open questions."

### The sync decision — what actually happens to `PROJECTS_DIR/<name>` (unchanged from the previous version; the hard question this cycle exists to answer)
A low-privilege script, `scripts/gitea-sync-project.sh`, run via `sudo -u
$RUN_USER` (not root — nothing here needs to `chown`/create a new directory;
the working copy and its ownership already exist). Usage:
`gitea-sync-project.sh <name> <branch>`. **This logic is identical to the
previous version of this spec — only its caller changed (a poll dispatch
instead of a webhook handler).**

1. Re-validates `<name>` against the same `NAME_RE`-shaped pattern every other
   privileged script re-validates (defense in depth), and `<branch>` against
   `^[A-Za-z0-9._/-]+$` (cheap sanity check; it's about to be interpolated
   into `git` arguments).
2. `DEST="$PROJECTS_DIR/$NAME"`; if it doesn't exist or isn't a git working
   copy, print `no-such-project` and exit 0 (not an error — the mapping can go
   stale if a project directory is later removed by hand; see "Edge cases").
3. `cd "$DEST" && git fetch origin "$branch"` (no `--force`, plain fetch —
   never touches the working tree or `HEAD`, only updates
   `refs/remotes/origin/$branch`).
4. **Dirty check**: `git status --porcelain` non-empty → print
   `skipped-dirty` and exit 0. Stop here — nothing is touched.
5. **Fast-forward-safety check**: `git merge-base --is-ancestor HEAD
   "origin/$branch"`. If this fails (local `HEAD` is not an ancestor of the
   newly fetched ref — either genuinely diverged, or local `HEAD` is itself
   ahead with commits not yet pushed) → print `skipped-diverged` and exit 0.
   Stop here — nothing is touched, no history is rewritten, no commits are
   ever lost. Combined with the dirty-check above rather than as an
   either/or, since they're independent risks (a clean tree can still be
   diverged; an ancestor tree can still be dirty).
6. Otherwise: `git merge --ff-only origin/"$branch"` (a guaranteed no-op or
   clean fast-forward, given step 5 already confirmed it's possible — chosen
   over `git reset --hard` specifically *because* it's a no-op/refuses loudly
   rather than silently discarding anything). Print `synced` and exit 0.
7. **Concurrency**: a per-project, non-blocking lock in `app.py` (a
   `threading.Lock()` per name, `try_lock`/skip-if-busy — mirrors the
   `_desc_pending` per-name-set idiom already used for description
   generation) ensures two poll-triggered sync attempts for the same project
   never run this script concurrently against the same working copy (e.g. a
   slow fetch on a large repo still running when the next 45s interval fires
   again); a skipped-because-busy attempt is harmless since the *next* poll
   (or the next manual `git pull`) converges to the same end state — same
   "safe to rerun" framing the deleted `project-sync.sh` already had.

**Not surfaced as a "notification" beyond the existing per-project row**:
`skipped-dirty`/`skipped-diverged` are recorded (see "Repo-map + sync-state
file" below) and exposed as one more optional field on that project's
existing `/status` entry — a badge/tooltip is a `docs/design.md` decision, not
specced here. No new inbox/notification subsystem (Non-goals).

### Repo-map + sync-state file (still needed — same reason as before: resolving `owner/repo` → `PROJECTS_DIR/<name>`)
Polling Gitea's API per project needs to know *which* `owner/repo`s to even
ask about, and which local project each maps to — `create_project()` is the
only thing positioned to create that mapping (see Non-goals: no retroactive
linking for non-Gitea-flow projects). Rather than have `app.py` (running as
`SVC_USER`) read into `RUN_USER`'s home directory to discover this by
inspecting each project's own `.git/config` — `SVC_USER` has no general read
access there by this project's own design (every other crossing of that
boundary goes through an explicit, narrowly-scoped privileged script, never
an ambient filesystem read) — a small JSON file under the same directory
`DESC_CACHE_FILE` already lives in is the consistent, no-new-privilege-
crossing choice, same as the previous version of this spec:

`GITEA_REPO_MAP_FILE` (new config, default
`/var/lib/ai-dev-switchboard/gitea-repo-map.json`, `SVC_USER`-owned, same
directory/ownership `DESC_CACHE_FILE` already has):
```json
{
  "owner/repo-slug": {"name": "local project name", "branch": "main",
                       "sync_state": "synced", "sync_at": 1770912000.0,
                       "remote_sha": "abc123..."}
}
```
(**`remote_sha` is new in this revision** — the last remote branch-tip SHA
`app.py` has observed/acted on for this project; everything else is
unchanged from the previous version.)
- Written by `create_project()` right after a successful clone (same
  read-modify-write-via-tmp-file-then-`os.replace()` idiom `_save_desc_cache`
  already uses for `DESC_CACHE_FILE`), with `sync_state`/`sync_at`/`remote_sha`
  initially `null`. A `null` `remote_sha` is deliberately treated by
  `_gitea_poll_one` as "always different from whatever's polled" — so the
  very first poll after creation always attempts a sync (a harmless no-op in
  the common case, since the just-cloned working copy already matches
  origin; genuinely useful in the rare case where something was pushed to the
  new repo in the gap between clone and first poll).
- Updated by `_gitea_sync_bg` (spawned from `_gitea_poll_one`, off the
  request thread — mirrors the existing `_generate_description_bg` "return
  fast, do the real work off the request thread" idiom already in this file,
  since a `git fetch` shouldn't run synchronously inside a `/status` request)
  after each `gitea-sync-project.sh` run, keyed by the same
  `owner/repo-slug`.
- Read by `_gitea_poll_if_due`/`_gitea_poll_one` to know which repos to poll
  and what SHA they were last seen at; read by `/status`'s handler
  (reverse-indexed by `name`, small N, same "just iterate it" style
  `instance_names()` already uses) to attach `sync_state`/`sync_at` to that
  project's row, when present.
- An entry whose `name` no longer exists under `PROJECTS_DIR` (project removed
  by hand) is left in place, harmlessly — the sync script itself already
  handles a missing `DEST` as a clean no-op (step 2 above); the poll would
  keep asking Gitea about it forever otherwise, but that's just one more
  cheap GET per interval, not worth adding a reaper for (see "Edge cases").

### Repo-map write in `create_project()` (best-effort, non-fatal — resolved default, much smaller than the previous version)
Inserted right after the existing privileged-clone success path
(`app/app.py:608`-614, the `r.returncode != 0` check), before returning
`True, ""`:
```python
_save_gitea_repo_map_entry(f"{owner}/{repo_name}", name, "main")
```
(The previous version's webhook-registration `_gitea_api("POST", ".../hooks",
...)` call is gone entirely — there is no webhook to register anymore. This
is now a **pure local JSON file write**, not a Gitea API call, so it's also
lower-stakes than before: it can really only fail on a disk/permission
problem, not a Gitea-reachability one.)

Still best-effort/non-fatal: a failure (e.g. a permissions issue on
`GITEA_REPO_MAP_FILE`'s directory) is logged, not returned as a
`create_project()` failure — the primary outcome (a real repo, cloned and
working) already succeeded, and losing only the auto-sync nicety is the same
degrade-gracefully tradeoff already accepted for the best-effort
cleanup-on-clone-failure path right above it.

### Leaving 2c part 2 (CI/CD auto-deploy) a clean extension point
Not building anything of part 2's own here, but keeping the seam clean:
`_gitea_poll_one`'s "remote SHA changed" branch (the same place that
currently only calls `_gitea_sync_bg`) is the natural place for part 2 to add
a second action (e.g. "also run a deploy script") once a new push is
detected, without touching this cycle's polling, throttling, or the
sync-decision logic at all. No code is added *for* that now — this is a
design-for-extension note, not a hook/interface actually built this cycle.

### `install.sh` changes
Inside the existing `if [ "$WITH_GIT_HOSTING" -eq 1 ]; then ... fi` block
(`install.sh:436`-onward) — **substantially smaller than the previous
version**, since there's no pinned network, no webhook secret, and no bind
address to generate anymore:
- `install -m 755 .../gitea-sync-project.sh
  /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh` (system-wide,
  alongside the other Gitea wrapper installs) — unchanged from before.
- New sudoers line, grouped with the other `ALL=($RUN_USER)` rules
  (`install.sh:380-382`, not the `ALL=(root)` ones — this script never needs
  root):
  ```
  $SVC_USER ALL=($RUN_USER) NOPASSWD: /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh *
  ```
  gated on `WITH_GIT_HOSTING`, same as the existing Gitea-specific `ALL=(root)`
  rules — unchanged from before.
- `set_env "$ENV_FILE" GITEA_SYNC_SCRIPT
  /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh`,
  `GITEA_REPO_MAP_FILE "$STATE_DIR/gitea-repo-map.json"` (same `$STATE_DIR`
  `DESC_CACHE_FILE` already uses — `install.sh:214`) — unchanged from before,
  minus the now-deleted `GITEA_WEBHOOK_BIND_ADDR`/`GITEA_WEBHOOK_SECRET`
  lines.
- **Removed entirely from this revision**: the pinned
  `networks.gitea.ipam` block in `config/gitea-docker-compose.yml`,
  `GITEA_WEBHOOK_BIND_ADDR` generation, `GITEA_WEBHOOK_SECRET` generation.
  `config/gitea-docker-compose.yml` is not touched by this cycle at all.
- `GITEA_POLL_INTERVAL_SECONDS` is **not** written by `install.sh` — it has a
  built-in default (`45`) in `app.py` itself and is documented as an optional
  override in `config/switchboard.env.example`, same style as
  `GITEA_PORT`/`GITEA_LABEL` etc. (commented-out, "override if you need to").
  See "Open questions" for the alternative (writing it explicitly, like
  `UPLOAD_STAGING_TTL_SECONDS` is).

## Affected areas
- `app/app.py`:
  - New config reads: `GITEA_POLL_INTERVAL_SECONDS` (constant, env-overridable,
    default `45`), `GITEA_SYNC_SCRIPT`, `GITEA_REPO_MAP_FILE`. **Removed**:
    `GITEA_WEBHOOK_BIND_ADDR`, `GITEA_WEBHOOK_SECRET`, `GITEA_WEBHOOK_MAX_BYTES`.
  - `create_project()`: one new best-effort call after the existing
    successful-clone path (repo-map write only — no webhook registration).
  - New: `_gitea_poll_if_due`, `_gitea_poll_one`, `_gitea_sync_bg` (same shape
    as the previous version's, now dispatched from the poll instead of a
    webhook handler), `_load_gitea_repo_map`/`_save_gitea_repo_map_entry`
    (tmp+`os.replace`, matching `_save_desc_cache`), a per-name lock dict for
    sync concurrency, a module-level `_gitea_poll_lock`/`_gitea_poll_last_at`
    pair for poll-pass throttling.
  - **Removed**: `_handle_gitea_webhook`, `_handle_gitea_push`,
    `WebhookOnlyHandler`, the second `ThreadingHTTPServer` startup in
    `__main__`, the `/gitea-webhook` early-branch check in `do_POST`.
  - `do_GET`'s `/status` handler: calls `_gitea_poll_if_due(gitea_on)` right
    after `gitea_on` is computed; instance rows get the same optional
    `gitea_sync: {"state": ..., "at": ...}` field, sourced from the repo-map
    file, as the previous version planned — unchanged.
- `scripts/gitea-sync-project.sh` (new) — the low-privilege (`RUN_USER`, not
  root) sync script, identical logic to the previous version.
- `config/gitea-docker-compose.yml` — **no changes** (the previous version's
  pinned `networks.gitea.ipam` block is dropped entirely).
- `install.sh` — see "`install.sh` changes" above (substantially smaller diff
  than the previous version).
- `config/switchboard.env.example` — document `GITEA_SYNC_SCRIPT`,
  `GITEA_REPO_MAP_FILE`, `GITEA_POLL_INTERVAL_SECONDS` (comment-only,
  optional override), following the existing `GITEA_*` block's documentation
  style. **Removed**: `GITEA_WEBHOOK_BIND_ADDR`, `GITEA_WEBHOOK_SECRET`.
- `docs/GIT_HOSTING.md` — "What's NOT included (yet)" loses the sync-on-push
  bullet; a new section describes the new (polling-based) behavior plainly,
  including its honest skip cases (dirty / diverged) and its latency (up to
  `GITEA_POLL_INTERVAL_SECONDS`), matching this project's existing "don't
  silently overclaim" documentation discipline.
- `README.md` — its brief git-hosting mention, if it references the old "no
  auto-sync" gap, updated to match.
- Tests (no real Docker/network/Gitea-server calls anywhere, same convention
  as `tests/test_gitea.py`/`tests/test_new_project_from_gitea.py`):
  - `tests/test_gitea.py` (extended) — `create_project()`'s new best-effort
    repo-map-write call (assert it's attempted on success and that its
    failure doesn't fail `create_project()`'s own return value).
  - `tests/test_gitea_poll.py` (new) — `_gitea_poll_if_due`/`_gitea_poll_one`
    logic via direct calls with a mocked `_gitea_api` and a mocked
    `_gitea_sync_bg`: throttling (a second call before the interval elapses
    does not call `_gitea_api` again), `gitea_on=False`/`GITEA_ENABLED=False`
    gating (no calls at all), SHA-unchanged skip (no `_gitea_sync_bg` call),
    SHA-changed dispatch (`_gitea_sync_bg` called with the right args), and a
    non-200 branch-lookup response (skipped gracefully, not raised).
  - `tests/test_gitea_sync_project.py` (new) — exercises the **real**
    `gitea-sync-project.sh` against real temporary git repos (clean,
    fast-forwardable → `synced`; dirty working tree → `skipped-dirty`;
    diverged local commits → `skipped-diverged`; already up to date →
    `synced`/no-op; missing `PROJECTS_DIR/<name>` → clean no-op) — **no
    `sudo`/root needed for these tests**, unlike
    `test_new_project_from_gitea.py`'s privileged cases: this script never
    changes user or ownership itself (it's invoked as `RUN_USER` externally,
    via sudoers, but performs no privilege-crossing internally), so it can
    just be run directly as whatever user runs the test suite.

## Edge cases
- **Dirty working copy at poll time** — sync is skipped entirely (step 4 of
  the sync decision); recorded as `skipped-dirty`; no data loss.
- **Local commits not yet pushed (diverged/ahead)** — sync is skipped
  entirely (step 5); recorded as `skipped-diverged`; no data loss, no rewritten
  history.
- **Branch deleted at the Gitea side** — `GET
  /repos/{owner}/{repo}/branches/{branch}` 404s; `_gitea_poll_one` skips that
  entry this cycle and retries next interval (no special-casing needed beyond
  the generic non-200 handling); the stale repo-map entry is otherwise
  harmless (see next bullet).
- **Repo-map entry exists but `PROJECTS_DIR/<name>` no longer does** (removed
  by hand) — the sync script's own step 2 already handles this as a clean
  no-op; the poll keeps asking Gitea about it (one cheap GET per interval,
  forever) but this is judged not worth a reaper given how inert it is (see
  Non-goals — no delete-project feature exists to trigger cleanup from
  anyway).
- **Repo-map entry exists but the Gitea repo itself is gone/renamed** —
  same 404 handling as branch deletion above; retried indefinitely at no
  real cost, logged, not surfaced as an error anywhere.
- **Two poll-triggered sync attempts for the same project overlapping** (a
  slow fetch on a large repo still running when the next interval fires) —
  serialized via the per-project non-blocking lock; an attempt that finds the
  lock held is simply dropped (the next poll converges to the same state
  regardless — same "safe to rerun" property the deleted `project-sync.sh`
  already had).
- **Two overlapping `/status` requests both deciding a poll pass is due at
  the same instant** — the module-level `_gitea_poll_lock` (non-blocking
  acquire) ensures only one of them actually runs the pass; the other simply
  returns immediately, having contributed nothing (harmless — the winner's
  pass covers the same ground).
- **`GITEA_ENABLED` false, or Gitea toggled off** (`gitea_on=False`) — the
  poll no-ops entirely for that cycle; resumes automatically once Gitea is
  toggled back on and the next due interval arrives. No error surfaced.
- **Repo-map write fails but the repo+clone already succeeded** —
  `create_project()` still returns success; the project just doesn't get
  auto-sync (same as before this cycle existed) — logged, not surfaced to the
  UI as an error for that "+ New project" click.
- **Two consecutive poll cycles observe the same remote SHA** (nothing
  changed) — the second cycle doesn't merely no-op *safely*, it doesn't even
  attempt a sync at all (the SHA-compare in `_gitea_poll_one` short-circuits
  before any subprocess call) — stronger than "idempotent if rerun," it's
  simply not rerun.
- **A push lands between `_gitea_poll_one`'s SHA snapshot and
  `gitea-sync-project.sh`'s own later `git fetch`** — accepted, self-healing
  race (see "Proposed approach: the poll mechanism"); converges within one
  more poll interval, never loses or double-applies a push.
- **`PUBLISH_MODE`** — irrelevant to this entire feature; every call this
  cycle makes (`_gitea_api`'s branch lookup, the sync script's own `git
  fetch`) is host-loopback-internal (`app.py`/`RUN_USER`'s shell to
  `127.0.0.1:$GITEA_PORT`), never routed through `tailscale serve`/`BASE_URL`,
  same reasoning 2b's own spec already established for `_gitea_api()` and the
  clone script — and, unlike the rejected webhook version, there's no reverse
  (Gitea-container-to-host) direction to reason about here at all.

## Acceptance criteria
- [ ] Given a project created via "+ New project" (Gitea flow) completes
  successfully, when that finishes, then a `GITEA_REPO_MAP_FILE` entry exists
  mapping `owner/repo` → that project's local name and branch (`"main"`),
  with `sync_state`/`sync_at`/`remote_sha` all `null` — verified without a
  live Gitea instance.
- [ ] Given that repo-map write fails for any reason, when `create_project()`
  runs, then the overall call still returns success (the repo+clone already
  succeeded) — no regression to 2b's existing behavior.
- [ ] Given `GITEA_POLL_INTERVAL_SECONDS` has elapsed since the last poll
  pass, `GITEA_ENABLED` is true, and Gitea is reported running, when
  `/status` is called, then `_gitea_api` is called once (`GET
  /repos/{owner}/{repo}/branches/{branch}`) for each `GITEA_REPO_MAP_FILE`
  entry.
- [ ] Given the interval has **not** yet elapsed, when `/status` is called
  again, then no additional `_gitea_api` calls are made for polling (the
  throttle holds) — verified via a mocked clock or a mocked
  `_gitea_poll_last_at`.
- [ ] Given `GITEA_ENABLED` is false, or Gitea is reported not running, when
  `/status` is called, then no polling `_gitea_api` calls are made at all.
- [ ] Given a polled branch's commit SHA matches the repo-map entry's stored
  `remote_sha`, when polled, then no sync attempt (`_gitea_sync_bg`/subprocess
  call) is made for that project.
- [ ] Given a polled branch's commit SHA differs from the stored `remote_sha`,
  a clean working copy, and local `HEAD` a strict ancestor of the new SHA,
  when polled, then the working copy is fast-forwarded to the new SHA
  (verified via real git operations in `tests/test_gitea_sync_project.py`;
  verified via a mocked subprocess call in `tests/test_gitea_poll.py`), and
  the repo-map entry's `remote_sha`/`sync_state`/`sync_at` are updated
  afterward.
- [ ] Given the same setup but the working copy has uncommitted changes, when
  polled, then no `git` operation beyond `fetch` touches the working
  tree — the uncommitted changes are byte-for-byte intact afterward, the
  project's repo-map entry records `skipped-dirty`, and `remote_sha` is still
  updated to the newly-observed SHA (so this same push isn't re-attempted
  every subsequent interval).
- [ ] Given the same setup but local `HEAD` has commits not present in the
  polled ref, when processed, then no destructive operation occurs (verified:
  the pre-existing local commit is still reachable from `HEAD` afterward), and
  the repo-map entry records `skipped-diverged` (and `remote_sha` updated, as
  above).
- [ ] Given a branch lookup returns a non-200 status (e.g. repo/branch
  deleted or renamed), when polled, then that entry is skipped without
  raising, and no repo-map fields for that entry are modified.
- [ ] Given the full test suite (`python3 -m unittest discover -s tests`),
  when it runs after this cycle's changes, then all tests pass, with no real
  Docker/network/Gitea-server calls anywhere in the new test files, and
  `tests/test_gitea_sync_project.py`'s cases run without requiring root/sudo.
- [ ] Given `/status` is called for a project with a repo-map entry, when the
  response is inspected, then it includes that entry's current
  `sync_state`/`sync_at`; given a project with no such entry (not created via
  the Gitea flow, or Gitea disabled), then that field is simply absent, not
  present-but-null (developer's call on the exact JSON shape for "absent" vs.
  `null`, consistent with this file's existing style elsewhere in `/status`).

## Open questions
1. **`GITEA_POLL_INTERVAL_SECONDS` default (45s) is an arbitrary-but-reasonable
   pick** within the 30–60s range this revision's own instructions suggested
   — not empirically load-tested against "how many Gitea-backed projects is
   too many for one poll pass to comfortably finish before the interval
   elapses again." Flagged, not blocking; easy to retune without any other
   design change if it turns out too slow/too chatty in practice.
2. **The snapshot race between the poll's SHA read and the sync script's own
   later `git fetch`** (a further push can land in between) is accepted as
   self-healing (converges within one more poll interval — see "Proposed
   approach" and "Edge cases") rather than treated as a bug to eliminate.
   Flagged in case a stronger consistency guarantee is wanted later (e.g.
   having the sync script itself report back the SHA it actually landed on,
   rather than trusting the poll's earlier snapshot).
3. **Whether `skipped-dirty`/`skipped-diverged` needs any UI treatment beyond
   a small `/status` field** (e.g., should it actively surface as a warning
   the *next* time someone opens that project, versus a passive badge someone
   might not notice) is left to `docs/design.md` to decide, not resolved here
   — carried over unchanged from the previous version of this spec.
4. **Per-project non-blocking lock vs. one global lock** for sync
   concurrency (resolved default: per-project) — flagged as an assumption; a
   single global lock would be simpler but would serialize unrelated
   projects' syncs against each other for no reason, which seems like the
   wrong tradeoff given this project's already-established "each project
   session is independent" model elsewhere (tmux sessions, ttyd instances).
   Carried over unchanged from the previous version of this spec.
5. **Whether `GITEA_POLL_INTERVAL_SECONDS` should instead be an
   install.sh-written, switchboard.env.example-documented default** (like
   `UPLOAD_STAGING_TTL_SECONDS` is) **rather than an undocumented-in-the-env-
   file built-in constant** (resolved default above: the latter) — a minor
   convention choice, not a design blocker either way.
6. **No "sync now" manual trigger is added this cycle** (see Non-goals) —
   flagged in case waiting up to `GITEA_POLL_INTERVAL_SECONDS` after a known
   push is judged annoying enough in practice to warrant a small manual
   "check now" affordance in a later cycle; not building it speculatively
   here.

## Risk / rollback notes
- **The single biggest risk category from the previous version of this spec
  (a new unauthenticated inbound listener, a new secret flowing through
  `switchboard.env` and into every repo's webhook config, a pinned Docker
  subnet) is gone entirely by construction under this revision** — not
  mitigated, eliminated. This is the main benefit of the pivot, worth stating
  plainly rather than just implied by the diff.
- **New, much smaller risk**: polling adds one Gitea REST call per
  Gitea-backed project every `GITEA_POLL_INTERVAL_SECONDS`, on top of the
  `gitea_run("status")` shellout `/status` already does on every single
  4-second tick. This is a real, if modest, new steady-state load on both
  `app.py` and Gitea's own API — bounded by (a) the SHA-diff gate (a cheap
  GET, never a `git fetch`, when nothing changed), (b) the independent
  throttle (default 45s, decoupled from the fast UI poll), and (c) the
  already-small expected N (a homelab-scale number of Gitea-backed projects,
  same "small N, just iterate it" assumption the repo-map's own read path
  already makes).
- The sync decision (fetch, fast-forward-only-if-safe, otherwise skip) is
  deliberately conservative — the worst-case failure mode of a bug here is
  "sync doesn't happen when it safely could have" (an availability nit,
  recoverable with a manual `git pull`), never "local work gets destroyed."
  This asymmetry is the core design choice of this whole cycle, unchanged by
  the webhook→polling pivot, and is worth preserving in any future revision
  of this mechanism.
- **Rollback is simpler than the previous version's would have been**: there
  is no listener to unbind and no pinned network config to revert. Reverting
  this cycle's diff entirely returns `app.py` to exactly 2b's already-shipped
  behavior (manual `git pull` fallback, as documented today). Short of a full
  revert, a much softer disable is also available with no code change at
  all: set `GITEA_POLL_INTERVAL_SECONDS` to a very large number.
