# Spec: BACKLOG item 17 part 2 -- GitHub poll-loop wiring + item 8 host-agnostic dispatch

## Summary
Wires part 1's inert GitHub client (`detect_project_origin`, `_github_api`/
`_github_api_raw`, `github_list_open_prs`/`github_pr_diff`/
`github_list_branches`/`github_post_pr_comment`) into two things that turn
out to be **the same mechanism**: a `GITHUB_POLL_INTERVAL_SECONDS`-throttled
poll piggybacked on `/status` (mirroring `_gitea_poll_if_due()`'s shape),
and item 8's AI merge-request reviewer becoming host-agnostic (Gitea vs.
GitHub, dispatched off `detect_project_origin()`). No new UI.

## Settled scope decisions (recorded here per this session's direction)
1. **The GitHub poll-loop IS item 8's dispatch -- there is no second,
   separate "surface PR/branch/comment data" job.** Checked what
   `_gitea_poll_one()` actually populates today: `gitea_sync: {state, at}`
   on `/status`, purely about fast-forwarding the *local checkout* when a
   push lands -- part 1's own Non-goals already rule that out for GitHub
   ("No auto-fast-forward-sync of the local checkout for GitHub-origin
   projects... the backlog's own ask is 'PRs, comments, branches...
   remotely fetchable,' not 'keep the local clone in sync'"). Gitea's own
   PR/branch/comment *listing* is never surfaced via `/status` either --
   `github_list_open_prs`/`github_list_branches` (part 1) and their Gitea
   equivalents remain on-demand, callable functions, not something a poll
   pushes into the UI. The **only** thing that actually needs periodic
   background polling (as opposed to on-demand fetch) is item 8's
   label-watching, because a label being added is an *event* a poll has to
   notice, not a query a human/script triggers on demand. So "poll-loop
   wiring" and "item 8's host-agnostic dispatch" collapse into one new
   function, `_github_poll_if_due()`, exactly mirroring
   `_gitea_poll_if_due()`'s throttle-lock-loop shape but calling the
   (now host-agnostic) `_ai_reviewer_poll_repo()` as its only per-repo
   work. See "Proposed approach" for why this isn't under-scoping the
   ask -- it's the honest answer to "what does Gitea's own poll actually
   populate," checked directly rather than assumed.
2. **No new web UI.** Same reasoning item 8/14/16 already used: the
   review's only user-visible output is the PR comment itself, visible
   directly in GitHub's own UI. Since decision 1 above means this part adds
   no new `/status` field either (no PR list, no branch list -- those stay
   on-demand, unused by any caller yet), there's nothing to design. No
   ux-designer dispatch for this cycle.
3. **GitHub-origin AI review needs its own explicit per-repo opt-in list,
   separate from `AI_REVIEWER_ENABLED` -- a real, reasoned deviation from
   "just extend Gitea's behavior unchanged."** This is NOT re-litigating
   item 17's already-settled "no extra confirmation gate on the write
   itself" decision (`docs/BACKLOG.md` item 17, `docs/spec.md`'s part 1
   "Settled scope decision" -- posting a comment still happens
   synchronously, no propose-then-approve step, unchanged). It's a
   narrower, different question: **which repos are even in scope for
   automatic review at all.** Every Gitea repo `AI_REVIEWER_ENABLED=1`
   already auto-reviews is, by construction, a repo *this switchboard
   itself created* via `create_project()`'s Gitea flow (2b) -- the
   operator owns and administers every single one. A GitHub-origin
   project has no such guarantee: item 16's clone-by-URL flow will happily
   clone *any* public or SSH-accessible repo, including a third-party
   upstream the operator only contributes to, or a dependency they cloned
   read-only to reference. Blanket-extending `AI_REVIEWER_ENABLED=1` to
   "every local project whose origin happens to resolve to github.com"
   would mean the switchboard starts posting AI-generated bot comments on
   PRs against infrastructure the operator doesn't fully control, the
   moment they clone something and happen to have a `ready for review`
   label lying around on some open PR there -- surprising, and a
   meaningfully different trust boundary than Gitea's fully-operator-owned
   case. **Decision: a new hand-edited, operator-maintained allowlist file,
   `AI_REVIEWER_GITHUB_REPOS_FILE`** (default
   `/etc/ai-dev-switchboard/ai-reviewer-github-repos.json`, same
   `/etc/ai-dev-switchboard/` placement and "app.py only ever reads it,
   never writes it" contract `DEPLOY_MAP_FILE` already established for
   exactly this class of decision -- an operator-controlled allowlist
   gating an automated per-project action). Content: a plain JSON array of
   `"owner/repo"` strings, e.g. `["myorg/myrepo"]`. A GitHub-origin project
   is only polled/reviewed when `AI_REVIEWER_ENABLED=1`, `GITHUB_TOKEN` is
   set, AND its `owner/repo` appears in this file -- all three, not any one
   alone. Missing/malformed/empty file -> `[]` (nothing opted in), the same
   "never crash, safe-degrade" idiom every loader in this file already
   follows. This only gates the label-watching decision to fire a review
   at all, not the write itself once fired -- decision 1's own settled
   "no confirmation gate on the comment post" stays exactly as-is.

## Goals
- A new `_github_poll_if_due()`, called from the `/status` handler
  alongside `_gitea_poll_if_due()`, throttled by its own
  `GITHUB_POLL_INTERVAL_SECONDS` (default 120, per part 1's own "120s+ is a
  reasonable starting point" -- GitHub's 5,000 req/hour token-wide rate
  limit argues for a materially more conservative interval than Gitea's
  loopback-cheap 45s default).
- `_ai_reviewer_poll_repo()` / `_ai_reviewer_review_run()` become
  host-agnostic (`host: "gitea" | "github"` parameter), reusing every piece
  of the existing label-edge-detection, per-PR locking, retry-gating, and
  state-persistence logic unchanged -- only the diff-fetch and comment-post
  calls (and the PR-list fetch) branch per host, exactly as the dispatch
  task specifies.
- `teams.review_pr_diff()` is reused completely as-is (already
  host-agnostic -- it only ever sees `pr_title`/`pr_body`/`diff_text`, a
  plain string, never a host-specific object).
- The new opt-in allowlist mechanism (see "Settled scope decisions" #3)
  gating which GitHub repos are ever polled at all.
- `AI_REVIEWER_STATE_FILE`'s existing entries and behavior for Gitea PRs
  are byte-for-byte unchanged -- this is purely additive.

## Non-goals
- **No new web UI, no new `/status` field** -- see "Settled scope
  decisions" #2.
- **No auto-fast-forward-sync of the local checkout for GitHub-origin
  projects** -- reaffirming part 1's own non-goal; this part doesn't touch
  it either.
- **No PR/branch/comment *listing* surfaced anywhere new** -- part 1's
  `github_list_open_prs`/`github_list_branches` remain callable, unused
  functions after this part too, same as their status today. Nothing in
  this backlog item has ever asked for a PR list widget; the "remotely
  fetchable" ask is satisfied by the functions existing and being callable
  (from a future CLI, or a `python3 -c` one-liner, exactly as part 1's own
  "How to verify locally" already demonstrates), not by an automatic
  background sync into the UI.
- **No GitHub Enterprise / other external hosts** -- unchanged from part 1;
  `detect_project_origin()` still only recognizes `github.com`.
- **No change to Gitea's own poll behavior, state-file key format, or
  acceptance criteria.** `_ai_reviewer_poll_repo("gitea", ...)`'s pr_key
  stays the exact unprefixed `f"{owner_repo}#{number}"` string it is
  today -- see "Proposed approach" for why (backward compatibility with
  already-persisted `AI_REVIEWER_STATE_FILE` entries on any live install).
- **Not fixing the pre-existing, already-disclosed episode/lock race**
  (`docs/BACKLOG.md` item 8's own status note: "the per-PR lock is keyed
  only on `pr_key`, not episode... narrow but real"). This cycle's
  host-agnostic refactor inherits that exact same narrow race identically
  for the GitHub path (same lock/state mechanism, just host-prefixed keys)
  -- not worsened, not fixed, out of scope for this cycle; flagged again
  under "Known limitations" for the reviewer's awareness.
- **No token-scope bootstrap/validation for `GITHUB_TOKEN`** -- unchanged
  from part 1; a `repo`-scoped PAT is still the operator's own
  responsibility, no new script.
- **`AI_REVIEWER_GITHUB_REPOS_FILE` is never authored by `install.sh` or
  any UI** -- hand-edited only, matching `DEPLOY_MAP_FILE`'s own explicit
  "no UI for authoring this" precedent (2c part 2b).

## Background / current state
- **What `_gitea_poll_if_due()` actually does** (`app/app.py`, item 2c
  part 1 + item 8): throttle-lock-loop over `_load_gitea_repo_map().items()`
  (an operator/`create_project()`-populated `owner/repo -> {name, branch,
  sync_state, sync_at, remote_sha}` registry), calling `_gitea_poll_one()`
  (branch-SHA-drift -> local fast-forward sync) and
  `_ai_reviewer_poll_repo(owner_repo, entry)` (label-watching) per entry,
  each independently wrapped in its own `try/except Exception: pass` --
  the should-fix item 2c part 1's own review caught and fixed (a malformed
  response for one repo must never kill polling for every other repo in
  the same pass). Called once per `/status` request, itself throttled to
  `GITEA_POLL_INTERVAL_SECONDS` internally via a double-checked
  lock+timestamp (`app/app.py:1267-1302`).
- **`_ai_reviewer_poll_repo(owner_repo, entry)`** (`app/app.py:1488-1547`,
  item 8): gated on `AI_REVIEWER_ENABLED`; `GET
  /repos/{owner_repo}/pulls?state=open`; per-PR label-edge detection keyed
  by `pr_key = f"{owner_repo}#{number}"` against `AI_REVIEWER_STATE_FILE`
  (`{pr_key: {label_present, attempts, reviewed_at, last_error}}`). On the
  absent->present edge, writes the trigger synchronously (closes the
  double-post race) then dispatches `_ai_reviewer_review_bg()`. On
  present-and-was-present, retries only if `last_error is not None and
  attempts < AI_REVIEWER_MAX_ATTEMPTS` (the developer's own disclosed,
  necessary correction to the literal spec text -- see
  `docs/implementation.md`'s item 8 "Deviations from spec" #1 -- keep this
  behavior verbatim, don't reintroduce the literal-but-broken reading).
- **`_ai_reviewer_review_run(owner_repo, entry, pr)`**
  (`app/app.py:1400-1467`): fetches the diff via `_gitea_api_raw("GET",
  f"/repos/{owner_repo}/pulls/{number}.diff")` (raw `(status, text)`
  tuple), truncates to `AI_REVIEWER_MAX_DIFF_BYTES`, resolves
  `AI_REVIEWER_MODEL` against `teams.roster()`, calls
  `teams.review_pr_diff(model_entry, workdir=os.path.join(PROJECTS_DIR,
  entry["name"]), pr_title=..., pr_body=..., diff_text=...,
  diff_truncated=...)` (already fully host-agnostic -- never touches
  Gitea/GitHub directly, only grounding + the model call), builds the
  comment via `_ai_reviewer_comment_body()`, posts via `_gitea_api("POST",
  f"/repos/{owner_repo}/issues/{number}/comments", {"body": comment})`,
  and on success resets state (`attempts=0, last_error=None`). Every
  failure path calls `_ai_reviewer_record_failure()`. Runs off the request
  thread via `_ai_reviewer_review_bg()`'s per-PR non-blocking lock
  (`_ai_reviewer_pr_lock_for`).
- **`docs/BACKLOG.md` item 8's real review history** (checked directly,
  correcting this dispatch's own initial framing): **one** review round,
  reviewer-approved with **one** non-blocking follow-up not yet fixed (the
  episode/lock race noted under "Non-goals" above) -- not three rounds.
  Item 16 is the item with the three-round review arc (an unrelated
  URL-injection fix); this item's own history is a single clean pass plus
  one disclosed, deferred should-fix.
- **Part 1's deliverables** (`app/app.py`, all inert until this part --
  see `docs/implementation.md`'s item 17 part 1 section): `detect_project_origin(name)
  -> {"kind": "local"|"github"|"external"|"none", "owner": str|None, "repo":
  str|None}`; `_github_api`/`_github_api_raw` (mirroring `_gitea_api`'s
  exact `(status, body)` contract); `github_list_open_prs(owner, repo) ->
  {"ok": True, "prs": [...]}`; `github_pr_diff(owner, repo, number) ->
  {"ok": True, "diff": text}` (via the diff `Accept` header, not a `.diff`
  URL suffix -- GitHub's own shape, different from Gitea's); `github_list_branches`;
  `github_post_pr_comment(owner, repo, number, body) -> {"ok": True}`.
  These four all check `GITHUB_TOKEN` first (no network call if unset) and
  catch `ConnectionError` internally, returning `{"ok": False, "error":
  ...}` rather than propagating or raising -- a materially different
  return contract from `_gitea_api`/`_gitea_api_raw`'s raw `(status, body)`
  tuple, which this part's dispatch code has to normalize against (see
  "Proposed approach").
- **`instance_names()`** (`app/app.py:617-621`): the existing, cheap
  (`os.listdir` + `isdir` filter) enumeration of every project under
  `PROJECTS_DIR` -- already called once per `/status` request for the main
  `instances` loop. `detect_project_origin(name)` runs one unprivileged
  `git remote get-url origin` subprocess per project (confirmed sufficient,
  no new privilege boundary, per part 1) -- re-running this once per
  poll-due interval (not per `/status` tick) over every local project is
  cheap and requires no new persisted repo-map/registry file, unlike
  Gitea's `GITEA_REPO_MAP_FILE` (which exists because Gitea repos are
  registered by `create_project()`'s own flow, a different data source
  than "walk `PROJECTS_DIR`").

## Proposed approach

### 1. Host-agnostic `_ai_reviewer_poll_repo()` / `_ai_reviewer_review_run()`
Both gain a `host: str` first parameter (`"gitea"` or `"github"`). All
label-edge-detection, locking, and state-persistence logic is IDENTICAL
between hosts -- only three call sites branch:

```python
def _ai_reviewer_pr_key(host: str, owner_repo: str, number) -> str:
    # Gitea's key format is UNCHANGED ("owner/repo#number", no prefix) --
    # backward-compatible with every already-persisted AI_REVIEWER_STATE_FILE
    # entry on a live install. GitHub gets a "github:" prefix -- a string
    # Gitea's own owner/repo naming can never produce (no colon allowed),
    # so collision is structurally impossible, not just unlikely.
    return f"{owner_repo}#{number}" if host == "gitea" else f"github:{owner_repo}#{number}"


def _ai_reviewer_poll_repo(host: str, owner_repo: str, entry: dict) -> None:
    if not AI_REVIEWER_ENABLED:
        return
    if host == "gitea":
        status, resp = _gitea_api("GET", f"/repos/{owner_repo}/pulls?state=open")
        if status != 200 or not isinstance(resp, list):
            return
        prs = resp
    else:  # github
        if not GITHUB_TOKEN:
            return
        owner, _sep, repo = owner_repo.partition("/")
        result = github_list_open_prs(owner, repo)
        if not result.get("ok"):
            return
        prs = result["prs"]

    state = _load_ai_reviewer_state()
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if number is None:
            continue
        pr_key = _ai_reviewer_pr_key(host, owner_repo, number)
        # ... EVERYTHING below this line is byte-for-byte the existing
        # label_present/was_present edge-detection logic, unchanged --
        # only the pr_key construction above and the dispatch call below
        # (which now also passes `host` through) differ.
        ...
        _ai_reviewer_review_bg(host, owner_repo, entry, pr)
```

```python
def _ai_reviewer_review_run(host: str, owner_repo: str, entry: dict, pr: dict) -> None:
    number = pr.get("number")
    pr_key = _ai_reviewer_pr_key(host, owner_repo, number)
    try:
        if host == "gitea":
            try:
                status, diff_text = _gitea_api_raw(
                    "GET", f"/repos/{owner_repo}/pulls/{number}.diff")
            except ConnectionError as e:
                _ai_reviewer_record_failure(pr_key, str(e)); return
            if status != 200:
                _ai_reviewer_record_failure(pr_key, f"diff fetch failed (status {status})")
                return
        else:  # github -- reuses part 1's own convenience function directly,
               # normalizing its {"ok": bool, ...} contract against the
               # rest of this function's status-code-based control flow.
            owner, _sep, repo = owner_repo.partition("/")
            result = github_pr_diff(owner, repo, number)
            if not result.get("ok"):
                _ai_reviewer_record_failure(pr_key, result.get("error") or "diff fetch failed")
                return
            diff_text = result["diff"]

        # ... diff truncation, AI_REVIEWER_MODEL resolution, teams.review_pr_diff()
        # call, comment-body build: ALL UNCHANGED, host-agnostic already.

        if host == "gitea":
            try:
                status, _resp = _gitea_api(
                    "POST", f"/repos/{owner_repo}/issues/{number}/comments", {"body": comment})
            except ConnectionError as e:
                _ai_reviewer_record_failure(pr_key, str(e)); return
            if status // 100 != 2:
                _ai_reviewer_record_failure(pr_key, f"comment post failed (status {status})")
                return
        else:
            owner, _sep, repo = owner_repo.partition("/")
            result = github_post_pr_comment(owner, repo, number, comment)
            if not result.get("ok"):
                _ai_reviewer_record_failure(pr_key, result.get("error") or "comment post failed")
                return

        _save_ai_reviewer_state_entry(pr_key, label_present=True, attempts=0,
                                      reviewed_at=teams._now_iso(), last_error=None)
    except Exception as e:
        _ai_reviewer_record_failure(pr_key, f"{type(e).__name__}: {e}")
```

`_ai_reviewer_review_bg(host, owner_repo, entry, pr)` gains the same
leading `host` parameter, threading it through to `_ai_reviewer_review_run`
-- its own per-PR lock (`_ai_reviewer_pr_lock_for(pr_key)`) already uses
the now-host-prefixed `pr_key`, so Gitea and GitHub reviews for
differently-named PRs can never contend on the same lock.

**Existing Gitea call site update**: inside `_gitea_poll_if_due()`'s loop,
`_ai_reviewer_poll_repo(owner_repo, entry)` becomes
`_ai_reviewer_poll_repo("gitea", owner_repo, entry)` -- the only change to
that function.

### 2. `AI_REVIEWER_GITHUB_REPOS_FILE` allowlist loader
New, next to `_load_deploy_map()`'s own idiom (read-only, hand-edited,
never-crash):

```python
AI_REVIEWER_GITHUB_REPOS_FILE = os.environ.get(
    "AI_REVIEWER_GITHUB_REPOS_FILE",
    "/etc/ai-dev-switchboard/ai-reviewer-github-repos.json")


def _load_ai_reviewer_github_repos() -> set:
    """Hand-edited JSON array of "owner/repo" strings -- app.py only ever
    reads this file, same DEPLOY_MAP_FILE contract. Missing/malformed/
    not-a-list-of-strings -> empty set (nothing opted in), never raises."""
    try:
        with open(AI_REVIEWER_GITHUB_REPOS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {x for x in data if isinstance(x, str) and x}
```

### 3. `_github_poll_if_due()` -- the new poll, doubling as item 8's dispatch trigger
```python
_github_poll_lock = threading.Lock()
_github_poll_last_at = 0.0


def _github_poll_if_due() -> None:
    global _github_poll_last_at
    if not AI_REVIEWER_ENABLED or not GITHUB_TOKEN:
        return  # nothing this poll exists to do would run anyway -- see
                 # "Settled scope decisions" #1: this poll has no purpose
                 # independent of item 8's label-watching.
    if time.time() - _github_poll_last_at < GITHUB_POLL_INTERVAL_SECONDS:
        return
    if not _github_poll_lock.acquire(blocking=False):
        return
    try:
        if time.time() - _github_poll_last_at < GITHUB_POLL_INTERVAL_SECONDS:
            return
        _github_poll_last_at = time.time()
        allowed = _load_ai_reviewer_github_repos()
        if not allowed:
            return
        for name in instance_names():
            try:
                origin = detect_project_origin(name)
            except Exception:
                continue  # same per-project isolation discipline as
                           # _gitea_poll_if_due()'s own per-repo try/except
            if origin.get("kind") != "github":
                continue
            owner, repo = origin.get("owner"), origin.get("repo")
            if not owner or not repo:
                continue
            owner_repo = f"{owner}/{repo}"
            if owner_repo not in allowed:
                continue
            try:
                _ai_reviewer_poll_repo("github", owner_repo, {"name": name})
            except Exception:
                pass
    finally:
        _github_poll_lock.release()
```

New module-level constant next to `GITEA_POLL_INTERVAL_SECONDS`:
```python
GITHUB_POLL_INTERVAL_SECONDS = int(os.environ.get("GITHUB_POLL_INTERVAL_SECONDS", "120"))
```

**Call site**: inside `do_GET()`'s `/status` branch, directly after the
existing `_gitea_poll_if_due(gitea_on)` call (`app/app.py:5404`):
```python
_gitea_poll_if_due(gitea_on)
_github_poll_if_due()
```
No `_on`/enabled-toggle parameter needed (unlike Gitea, GitHub isn't a
locally-run service with an on/off container state) -- gating is entirely
via `AI_REVIEWER_ENABLED`/`GITHUB_TOKEN`/the allowlist, all checked inside
the function itself.

### 4. `AI_REVIEWER_ENABLED`'s comment updated
The existing config comment ("Gitea-only... GitHub is item 17, not yet
built") is now stale -- update it in both `app/app.py`'s inline comment
(`app/app.py:207-214`) and `config/switchboard.env.example`'s matching
block to describe the host-agnostic behavior and the new
`AI_REVIEWER_GITHUB_REPOS_FILE`/`GITHUB_POLL_INTERVAL_SECONDS` knobs.

## Affected areas
- `app/app.py` only -- no new route, no schema change, no new sudoers
  entry, no new privileged script:
  - New: `_ai_reviewer_pr_key()`, `_load_ai_reviewer_github_repos()`,
    `_github_poll_if_due()`, `AI_REVIEWER_GITHUB_REPOS_FILE`,
    `GITHUB_POLL_INTERVAL_SECONDS` constants.
  - Modified (signature + internal branching only, all existing
    Gitea-path behavior byte-for-byte unchanged): `_ai_reviewer_poll_repo()`,
    `_ai_reviewer_review_run()`, `_ai_reviewer_review_bg()` (all gain a
    leading `host` parameter); the one Gitea call site inside
    `_gitea_poll_if_due()`; the `/status` handler (one new call line);
    `AI_REVIEWER_ENABLED`'s doc comment.
- `config/switchboard.env.example` -- new documented
  `GITHUB_POLL_INTERVAL_SECONDS`/`AI_REVIEWER_GITHUB_REPOS_FILE` entries
  near the existing `GITEA_POLL_INTERVAL_SECONDS`/`AI_REVIEWER_*` block;
  `AI_REVIEWER_ENABLED`'s comment updated per §4 above.
- New `config/ai-reviewer-github-repos.json.example` -- a two-line example
  file (`["owner/repo"]`), matching `config/deploy-map.json.example`'s own
  precedent for a hand-edited, operator-authored JSON allowlist.
- Test files: extend `tests/test_ai_reviewer.py` (host-agnostic
  `_ai_reviewer_poll_repo`/`_ai_reviewer_review_run`/`_ai_reviewer_review_bg`
  coverage, including a full regression pass proving every existing
  Gitea-path test still passes unchanged with the new `host="gitea"`
  argument threaded through) and/or `tests/test_github_api.py` (new
  `_github_poll_if_due()`/`_load_ai_reviewer_github_repos()`/
  `_ai_reviewer_pr_key()` coverage) -- developer's call on which file each
  new test class belongs in, following whichever existing file's
  monkeypatch conventions (`_gitea_api`/`_github_api` mocking) a given test
  needs.
- **No** `app/teams.py` change -- `review_pr_diff()` is reused completely
  unmodified, confirming part 1's own framing that it's "already
  host-agnostic."

## Edge cases
- **`AI_REVIEWER_GITHUB_REPOS_FILE` missing, empty array, or malformed** --
  `_load_ai_reviewer_github_repos()` returns `set()`; `_github_poll_if_due()`
  returns immediately after loading it (before even calling
  `instance_names()`) -- zero GitHub repos ever reviewed, the safe default.
- **A project's `owner/repo` is in the allowlist, but its local `origin`
  no longer resolves to that same GitHub repo** (renamed, remote changed,
  or the operator repurposed the local folder) -- `detect_project_origin()`
  simply won't return that `owner/repo` for that project name anymore, so
  it's silently skipped; the allowlist entry becomes a harmless no-op,
  never an error. No project-name-to-allowlist-entry validation is needed
  or attempted.
- **Two different local projects both have `origin`s pointing at the same
  `owner/repo`** (a project cloned twice under different local names) --
  both are polled independently (`instance_names()` iterates by local
  name, not by resolved `owner_repo`), but they share the exact same
  `pr_key` in `AI_REVIEWER_STATE_FILE` (state is keyed by the remote repo,
  not the local project name) -- whichever one's poll pass wins the
  trigger-edge write reviews it; the other sees `was_present=True` already
  on its own next poll and doesn't double-review. This mirrors how two
  Gitea repo-map entries pointing at the same `owner_repo` would already
  behave today (an existing, accepted property of this state shape, not
  new).
- **`GITHUB_TOKEN` unset while `AI_REVIEWER_ENABLED=1` and the allowlist is
  non-empty** -- `_github_poll_if_due()`'s own top guard returns
  immediately; no partial/inconsistent state, no error surfaced (matches
  every other "missing credential -> silent no-op" precedent in this
  file).
- **GitHub API rate-limited mid-poll-pass** (several allowlisted repos
  polled in one `_github_poll_if_due()` call) -- the existing global
  `_github_rate_limited()` cooldown gate (part 1) short-circuits every
  subsequent `github_list_open_prs`/`github_pr_diff`/`github_post_pr_comment`
  call within the same pass and future passes until it clears; each
  per-repo call already degrades to `{"ok": False, ...}` -> a normal
  recorded failure, retried next interval per the existing
  `attempts`/`last_error` gating, never a crash.
- **A GitHub PR closed/merged between label-detection and the background
  review actually running** -- `github_pr_diff()` returns a non-2xx-derived
  `{"ok": False, ...}`, recorded as an ordinary failure, same as Gitea's
  existing 404-on-diff-fetch handling.
- **Concurrent `_gitea_poll_if_due()` and `_github_poll_if_due()` passes**
  (two different `/status` requests landing close together, one winning
  each lock) -- fully independent locks/timestamps
  (`_gitea_poll_lock`/`_github_poll_lock`), no shared mutable state between
  them except `AI_REVIEWER_STATE_FILE` itself, which is already
  lock-guarded (`_ai_reviewer_state_lock`) and keyed collision-free per
  §1's `_ai_reviewer_pr_key()`.
- **An `engine`-kind `AI_REVIEWER_MODEL` reviewing a GitHub-origin PR** --
  `teams.review_pr_diff()`'s existing scratch-directory isolation
  (`_ai_reviewer_scratch/<token>`, never the real project working copy) is
  unmodified and applies identically regardless of which host the diff
  came from.

## Acceptance criteria
- [ ] Given a Gitea-origin project's PR gets its `ready for review` label
      added, when `_github_poll_if_due()` runs (with `AI_REVIEWER_ENABLED=1`
      and a non-empty allowlist), then it has no effect on that PR at all
      (Gitea path is driven exclusively by `_gitea_poll_if_due()`, as
      today) -- and `_gitea_poll_if_due()`'s own existing behavior/tests
      are unaffected by this cycle's changes (full existing
      `tests/test_ai_reviewer.py`/`tests/test_gitea_poll.py` suites still
      pass unmodified in assertions, only call-site signatures updated).
- [ ] Given a project whose `origin` resolves to `github.com` (via
      `detect_project_origin`), its `owner/repo` is listed in
      `AI_REVIEWER_GITHUB_REPOS_FILE`, `AI_REVIEWER_ENABLED=1`,
      `GITHUB_TOKEN` is set, and one of its open PRs has the
      `AI_REVIEWER_LABEL` label added, when `_github_poll_if_due()` runs,
      then exactly one PR comment is posted via a mocked
      `github_post_pr_comment`/`_github_api`, built from
      `teams.review_pr_diff()`'s output, and `AI_REVIEWER_STATE_FILE` gains
      an entry keyed `"github:owner/repo#<number>"` with `attempts=0,
      last_error=None`.
- [ ] Given the same PR polled again with the label still present (label
      never removed in between), when polled repeatedly, then no second
      comment-post call happens (same retry-gating logic as Gitea,
      confirmed to apply identically for `host="github"`).
- [ ] Given the label is removed and re-added, when next polled, then
      exactly one new comment is posted (a fresh episode) -- not zero, not
      two.
- [ ] Given a GitHub-origin project whose `owner/repo` is **NOT** in
      `AI_REVIEWER_GITHUB_REPOS_FILE` (or the file is missing/empty), when
      its PR gets the label added and `_github_poll_if_due()` runs, then
      no review is ever triggered for it, indefinitely, even though
      `AI_REVIEWER_ENABLED=1` and `GITHUB_TOKEN` is set (proves the
      allowlist is a hard gate, not advisory).
- [ ] Given `GITHUB_TOKEN` is unset, when `_github_poll_if_due()` is called
      (even with `AI_REVIEWER_ENABLED=1` and a non-empty allowlist), then
      it returns immediately and makes zero `github_*`/`_github_api*`
      calls (assert on a mock's call count).
- [ ] Given `AI_REVIEWER_ENABLED=0` (the shipped default), when
      `_github_poll_if_due()` is called, then it returns immediately
      regardless of `GITHUB_TOKEN`/allowlist state -- zero behavior change
      for any existing install that hasn't opted in.
- [ ] Given `GITHUB_POLL_INTERVAL_SECONDS` hasn't elapsed since the last
      poll pass, when `/status` is requested again, then
      `_github_poll_if_due()` makes no network calls at all (same
      throttle-gate proof `_gitea_poll_if_due()`'s own existing tests
      already establish for Gitea).
- [ ] Given a mocked GitHub diff-fetch failure (`github_pr_diff` returning
      `{"ok": False, "error": ...}`) mid-review, then
      `_ai_reviewer_record_failure()` is called with that error and no
      comment is posted -- mirrors the existing
      `test_diff_fetch_non_200_records_failure_and_posts_no_comment`-style
      Gitea test, adapted for the GitHub dict-contract shape.
- [ ] `git grep -n "@app.route\|def do_GET\|def do_POST"` (or equivalent
      structural check) on the diff shows no new route, and no HTML/JS
      template in `app/app.py` changes as part of this commit (verifies
      the "no UI" non-goal held).
- [ ] Full existing test suite (`python3 -m unittest discover -s tests`)
      passes with zero regressions, including every pre-existing
      `tests/test_ai_reviewer.py`/`tests/test_gitea_poll.py`/
      `tests/test_github_api.py` test (updated for the new `host`
      parameter where it's a direct call-site change, never a behavior
      change).

## Open questions
- **`GITHUB_POLL_INTERVAL_SECONDS` default (120)** and
  **`AI_REVIEWER_GITHUB_REPOS_FILE`'s exact default path**
  (`/etc/ai-dev-switchboard/ai-reviewer-github-repos.json`) -- both
  reasonable, low-stakes defaults consistent with this codebase's existing
  conventions (`DEPLOY_MAP_FILE`'s own `/etc/ai-dev-switchboard/`
  placement for a hand-edited allowlist/map); adjustable later without any
  design change, not a blocker.
- **Whether `_ai_reviewer_poll_repo`'s Gitea-path PR-list fetch should
  also be routed through a `github_list_open_prs`-style convenience
  wrapper for symmetry** -- deliberately left calling `_gitea_api` directly
  (matching the existing, unmodified Gitea code), since introducing a new
  Gitea convenience-wrapper layer this cycle wasn't asked for and would be
  an unrequested refactor of working, tested code; only the GitHub side
  needed a decision here, and it reuses part 1's `github_list_open_prs()`
  directly.
- **`config/ai-reviewer-github-repos.json.example`'s exact placement/name**
  (proposed to mirror `config/deploy-map.json.example`) -- non-blocking,
  developer's call on the literal filename as long as it's referenced
  correctly from `config/switchboard.env.example`'s new comment block.

## Risk / rollback notes
- Purely additive for any install that hasn't set `GITHUB_TOKEN` and
  created `AI_REVIEWER_GITHUB_REPOS_FILE`: `_github_poll_if_due()`'s own
  top-level guard (`AI_REVIEWER_ENABLED` and `GITHUB_TOKEN`) makes it a
  guaranteed no-op, and the Gitea path's behavior is unchanged (only its
  two call sites gained a `"gitea"` literal argument, no logic change) --
  every existing acceptance criterion from item 8's own spec must still
  hold identically after this refactor, which is why this spec requires
  the full existing Gitea test suite to pass with zero assertion changes.
  Safe to revert by reverting the `host`-parameter refactor and deleting
  the new poll/loader functions/constants; nothing else in the codebase
  depends on them yet (same "no UI wired to it" isolation part 1 already
  established).
- The one behavior change for an operator who **does** opt in
  (`AI_REVIEWER_ENABLED=1` + `GITHUB_TOKEN` set + a non-empty
  `AI_REVIEWER_GITHUB_REPOS_FILE`) is exactly the intended one: their
  listed GitHub repos start getting the same label-triggered AI review
  Gitea repos already get, posting comments directly to GitHub (per the
  already-settled, unchanged write-verb decision). Rollback for a single
  repo is a one-line edit to the allowlist file, no restart required
  (loaded fresh every poll pass, same "no caching" precedent
  `_load_deploy_map()`/`_load_gitea_repo_map()` already establish).
