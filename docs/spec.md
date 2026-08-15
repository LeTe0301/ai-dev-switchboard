# Spec: BACKLOG item 17 part 1 -- external-origin detection + GitHub REST client

## Summary
Give `app.py` two new, purely-backend capabilities: (1) detect, per project,
whether its `origin` remote is this switchboard's own local Gitea or an
external host (GitHub, to start), and (2) a GitHub REST API client
(auth, rate-limit handling, PR/branch/comment read+write calls) parallel to
the existing `_gitea_api`/`_gitea_api_raw` pair -- laying the groundwork for
item 8's GitHub support and item 16's arbitrary-URL clones, without yet
wiring either into a poll loop or the UI (that's item 17 part 2).

## Settled scope decision (recorded here per this session's direction)
**Read-write actions against a project's external origin (e.g. posting a
comment on a real GitHub PR) are allowed the SAME WAY they're already
allowed against the switchboard's own local Gitea -- no extra confirmation
gate.** This resolves BACKLOG item 17's own "scope decision to put to the
user before building" bullet; `docs/BACKLOG.md` item 17 is updated in this
same commit to record it. Concretely: `github_post_pr_comment()` (below)
posts directly, synchronously, the same way `_gitea_api("POST",
".../issues/{n}/comments", ...)` already does inside `_ai_reviewer_review_run()`
-- no propose-then-approve inbox step (that pattern is item 7's board-write
decision, a deliberately different, unrelated call: item 7's writes are
*agent*-initiated against the operator's own backlog tracker; item 8/17's
PR-comment write is also agent-initiated, and the operator already
explicitly approved posting comments as this project's chosen non-blocking,
reversible write verb when item 8 shipped Gitea-only -- extending the same
verb to GitHub doesn't reopen that question).

## Goals
- Detect, for any project under `PROJECTS_DIR`, whether its `origin` remote
  points at this switchboard's own local Gitea, at `github.com`, or
  somewhere else -- unprivileged, on demand, no new sudoers entry.
- A GitHub REST API client (`_github_api`/`_github_api_raw`, mirroring
  `_gitea_api`/`_gitea_api_raw`'s exact contract) with real auth
  (`GITHUB_TOKEN`, `switchboard.env`-style credential convention) and real,
  concrete rate-limit handling (not left abstract).
- Read+write convenience functions on top of that client covering exactly
  what the backlog asked for and what item 8 will need next: list a repo's
  open PRs (with labels), fetch a PR's diff, list branches, post a PR
  comment.
- A real, reasoned polling-vs-webhook decision recorded here (see below),
  even though part 1 doesn't yet implement the polling loop itself.

## Non-goals (this part specifically)
- **No poll-loop wiring.** No GitHub-equivalent of `_gitea_poll_if_due()`/
  `_ai_reviewer_poll_repo()` yet -- these new functions aren't called from
  anywhere in this part. That's item 17 part 2, once this client layer
  exists to wire against.
- **No UI changes.** No `/status` JSON field, no HTML/JS, no ux-designer
  pass -- this part has no user-visible dimension (pure backend/data
  capability), per `workflows/feature.md` step 1's explicit skip condition.
- **No item 8 integration.** `_ai_reviewer_poll_repo()`/
  `_ai_reviewer_review_run()` are untouched in this part -- making them
  host-agnostic (dispatch to Gitea vs. GitHub based on detected origin,
  reusing `teams.review_pr_diff()`) is item 17 part 2's job, item 5 in the
  original scoping list.
- **No auto-fast-forward-sync of the local checkout for GitHub-origin
  projects.** 2c part 1's "poll Gitea, fetch + fast-forward
  `PROJECTS_DIR/<name>` when the remote branch moves" behavior is
  Gitea-specific and was never asked for here -- the backlog's own ask is
  "PRs, comments, branches... remotely fetchable," not "keep the local
  clone in sync." A future item could propose this explicitly; not part of
  17 at all.
- **No GitHub Enterprise / other external hosts.** Only `github.com` is
  recognized as a supported external kind. Any other non-loopback host
  (a different self-hosted Gitea, GitLab, Bitbucket, GitHub Enterprise
  Server at a custom hostname) classifies as `"external"` with no client
  built for it -- a documented, silent no-op for now, same tolerance
  `AI_REVIEWER_MODEL` naming a nonexistent roster entry already gets.
- **No webhook** -- see "Polling vs. webhook" below.
- **No token-bootstrap script.** Unlike `scripts/gitea-configure-api.sh`
  (which mints a token via Gitea's own admin API, because Gitea is
  self-hosted by this switchboard), a GitHub PAT is created by the operator
  directly on github.com and pasted into `switchboard.env` -- same
  "documented secret, not auto-provisioned" treatment `SIMPLE_PASSWORD`/
  `TOTP_SECRET` already get.
- **No `GITHUB_ENABLED` toggle.** Host detection needs no token and is
  always available (same "no dependency" positioning item 16 established
  for clone-from-URL); the GitHub API calls themselves are gated purely on
  whether `GITHUB_TOKEN` is set, exactly how `GITEA_API_TOKEN` alone (no
  separate boolean) already gates `create_project()`'s Gitea calls beyond
  the `GITEA_ENABLED` toggle Gitea needs for an entirely different reason
  (it's a locally-run optional service that has to be started).
- **No persisted state file / repo-map equivalent.** Nothing in this part
  writes anything to disk -- detection re-runs `git remote get-url origin`
  fresh each call (cheap, local, no network) and the GitHub client is
  stateless apart from the in-memory rate-limit cooldown gate. A
  `GITEA_REPO_MAP_FILE`-style persisted mapping is only useful once
  something polls repeatedly, i.e. part 2's decision to make.

## Background / current state
- `app/app.py`'s Gitea integration (`_gitea_api`/`_gitea_api_raw`, backlog
  2b/2c/8) is the direct model: `_gitea_api()` returns
  `(status, parsed_json_or_{})`, raising `ConnectionError` only on a real
  transport failure, never on a non-2xx HTTP status; `_gitea_api_raw()` is
  the same contract for a non-JSON (diff) body. `_ai_reviewer_review_run()`
  (item 8) already fetches a PR diff via `_gitea_api_raw("GET",
  ".../pulls/{n}.diff")` and posts a review via `_gitea_api("POST",
  ".../issues/{n}/comments", {"body": ...})` -- GitHub's own REST API has a
  near-identical shape for both calls (see "Proposed approach").
- `_validate_clone_url()`/`_clone_url_host_is_safe()` (item 16) parse a
  URL's real host component (via `urllib.parse.urlsplit().hostname` for
  `scheme://` URLs, manual last-`@`/first-`:` splitting for git's scp-like
  shorthand) to defend a *privileged, argv-sensitive* `git clone`
  subprocess against injection. Item 17's host detection reuses the same
  *parsing technique* (urlsplit for scheme URLs, the same scp-shorthand
  split) but for a materially different, lower-stakes purpose: classifying
  an **already-existing** `origin` remote (not a value about to be handed
  to a privileged subprocess as an argv token) into local/github/external.
  No new privilege boundary is crossed, so item 16's injection-safety
  regexes (`_CLONE_URL_SCHEME_RE`/`_CLONE_URL_SCP_RE`/`_SAFE_HOST_RE`) are
  not reused directly -- a new, simpler, read-only, never-raising parser is
  written instead (see below).
- Confirmed by reading `scripts/new-project-from-gitea.sh`: every
  Gitea-created project's `origin` is literally
  `http://oauth2:<token>@127.0.0.1:<GITEA_PORT>/<owner>/<repo>.git` --
  **always the loopback address**, regardless of `GITEA_PORT`. This is the
  actual, load-bearing signature of "this switchboard's own Gitea," not a
  configured hostname to compare against.
- Confirmed by reading `app/teams.py`'s `load_grounding()`/
  `_discover_and_read()`: `app.py` (running as `SVC_USER`) already reads
  arbitrary files directly under `PROJECTS_DIR/<name>` in-process, with no
  `sudo -u RUN_USER` hand-off -- `install.sh` only `chown`s
  `PROJECTS_DIR` to `RUN_USER`, it never restricts group/other read+execute
  permissions, so `SVC_USER` has ambient *read* (never write) access to
  project working copies today. This means `git -C PROJECTS_DIR/<name>
  remote get-url origin` can run directly and unprivileged from `app.py`,
  with no new sudoers entry and no privileged script -- confirmed
  sufficient, not just assumed (see "Detection mechanism" below).
- `config/switchboard.env.example`'s `GITEA_API_TOKEN`/`AI_REVIEWER_*`
  block is the credential-and-config-documentation convention to follow:
  a commented-out, documented, never-shipped-a-value secret (same treatment
  as `SIMPLE_PASSWORD`/`TOTP_SECRET`).
- `AI_REVIEWER_ENABLED`'s own comment already says "Gitea-only... GitHub is
  item 17, not yet built" -- that comment stays accurate after this part
  (item 8 itself is untouched); part 2 is what will finally update it.

## Proposed approach

### 1. Detection mechanism
`git remote get-url origin` + loopback-vs-not host comparison is
**sufficient**, run unprivileged, no new script:

```python
def _project_origin_url(name: str) -> str | None:
    """Unprivileged `git remote get-url origin` against
    PROJECTS_DIR/<name> -- same "SVC_USER already has ambient read access
    under PROJECTS_DIR" basis load_grounding() already relies on. Returns
    None (never raises) for: not a git repo, no `origin` remote configured,
    or any subprocess/timeout failure -- all three are ordinary, expected
    states (a local-only `git init` project, an upload-wizard project with
    no remote at all), not errors."""
    try:
        r = subprocess.run(
            ["git", "-C", os.path.join(PROJECTS_DIR, name), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    return url or None
```

```python
def _classify_origin_url(url: str) -> dict:
    """Never raises. Returns {"kind": "local"|"github"|"external",
    "owner": str|None, "repo": str|None}. "local" = origin's host parses as
    a loopback IP (ipaddress.ip_address(host).is_loopback) -- covers every
    origin this switchboard itself has ever generated (always literally
    127.0.0.1, see "Background" above) and is robust to a bracketed ::1
    too, without hardcoding the string "127.0.0.1". "github" = host
    case-insensitively equals "github.com", with owner/repo parsed from the
    path (both scheme:// and user@host:path forms) and a trailing ".git"
    stripped. Anything else (unparseable, or a real but non-github,
    non-loopback host) is "external" with owner/repo left None -- no
    client exists for it in this part."""
```

Parsing detail: try `urllib.parse.urlsplit(url).hostname` first (handles
`https://github.com/owner/repo.git`, `ssh://git@github.com/owner/repo.git`,
bracketed IPv6 loopback); if that yields no scheme-shaped match, fall back
to a plain scp-shorthand split (`user@host:path`, splitting on the first
`@` then the first `:` after it -- same technique
`_last_path_segment_from_clone_url()` already uses, but written fresh here
since this is a read-only classification, not a security-validation path
that needs item 16's regex allowlist). Wrap the whole thing in a bare
`try/except Exception: return {"kind": "external", "owner": None, "repo":
None}` -- classification must never crash a caller over a malformed
`origin` some unrelated process created.

`detect_project_origin(name: str) -> dict` composes the two functions above
into the one public entry point (`_classify_origin_url(_project_origin_url(name)
or "")`, with a `"none"` kind returned when there's no origin at all).

### 2. GitHub API client shape
REST, matching the backlog's own suggestion and Gitea's REST-only
precedent. New module-level constants (placed near the existing
`GITEA_*`/`AI_REVIEWER_*` config block, same inline-comment convention):

```python
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_BASE = "https://api.github.com"        # fixed -- GitHub, unlike
                                                    # self-hosted Gitea, has
                                                    # no configurable port/host
GITHUB_API_TIMEOUT_SECONDS = 15                    # matches _gitea_api's
                                                    # own hardcoded timeout
GITHUB_RATE_LIMIT_FALLBACK_SECONDS = 60            # see "Rate-limit handling"
```

`config/switchboard.env.example` gets a new commented block parallel to
`GITEA_API_TOKEN`'s:

```
# GITHUB_TOKEN: a GitHub Personal Access Token (classic PAT, `repo` scope
# covers everything item 17/8 need: reading PRs/branches, posting PR
# comments). Created by you directly on github.com (Settings -> Developer
# settings -> Personal access tokens) -- unlike GITEA_API_TOKEN there is no
# bootstrap script, since GitHub isn't a service this switchboard runs.
# Left commented here the same way every other secret this file
# documents-but-never-ships-a-value-for is (SIMPLE_PASSWORD,
# GITEA_API_TOKEN) -- until it's set, github_*() calls return a clear
# "GITHUB_TOKEN isn't configured" error instead of an unauthenticated
# 401.
#GITHUB_TOKEN=
```

Client functions, mirroring `_gitea_api`/`_gitea_api_raw`'s exact contract
(same "raise ConnectionError only on a real transport failure, never on a
non-2xx status" rule):

```python
def _github_api(method: str, path: str, body: dict = None) -> tuple:
    """(status, parsed_json_or_{}). GITHUB_API_BASE + path. Headers:
    Authorization: Bearer <GITHUB_TOKEN>, Accept: application/vnd.github+json,
    X-GitHub-Api-Version: 2022-11-28, User-Agent: ai-dev-switchboard
    (GitHub's API rejects requests with no User-Agent at all -- a real,
    documented GitHub-specific requirement Gitea doesn't have). Checks the
    rate-limit cooldown gate BEFORE building the request (see below); if
    still cooling down, returns (429, {"error": "rate limited, retry
    later"}) without making an HTTP call at all. After any real response
    (success or HTTPError), calls _github_note_rate_limit() with the
    response headers + status before returning."""

def _github_api_raw(method: str, path: str, accept: str = None) -> tuple:
    """Like _github_api() but returns (status, text) -- for the PR-diff
    call, which needs Accept: application/vnd.github.v3.diff instead of
    the default JSON accept header. Same rate-limit-gate-then-note
    handling as _github_api()."""
```

Read+write convenience functions on top (all: check `GITHUB_TOKEN` first,
return `{"ok": False, "error": "GITHUB_TOKEN isn't configured -- see
switchboard.env"}` without any network call if unset; catch
`ConnectionError` from the underlying client call and turn it into the same
`{"ok": False, "error": ...}` shape rather than propagating -- these are
meant to be directly consumable by whatever part 2 or a future test/CLI
calls them, not just an internal implementation detail):

```python
def github_list_open_prs(owner: str, repo: str) -> dict:
    """GET /repos/{owner}/{repo}/pulls?state=open. {"ok": True,
    "prs": [...]} -- each item keeps GitHub's own shape (number, title,
    body, labels: [{"name": ...}, ...]), same "don't reshape the upstream
    response, let callers read the fields they need" choice _gitea_api's
    own callers already make."""

def github_pr_diff(owner: str, repo: str, number: int) -> dict:
    """GET /repos/{owner}/{repo}/pulls/{number} with the diff Accept
    header (_github_api_raw). {"ok": True, "diff": <text>}."""

def github_list_branches(owner: str, repo: str) -> dict:
    """GET /repos/{owner}/{repo}/branches. {"ok": True, "branches": [...]}."""

def github_post_pr_comment(owner: str, repo: str, number: int, body: str) -> dict:
    """POST /repos/{owner}/{repo}/issues/{number}/comments, {"body": body}
    -- GitHub, like Gitea, treats a PR's comments as issue comments; this
    is the write action the settled scope decision above covers, posted
    directly and synchronously, same as _gitea_api's own POST call in
    _ai_reviewer_review_run(). {"ok": True} on a 2xx status."""
```

### 3. Rate-limit handling -- concrete, not abstract
GitHub's REST API returns `X-RateLimit-Remaining` and `X-RateLimit-Reset`
(epoch seconds) on every response, and a `Retry-After` header (seconds) on
a 403/429 abuse-rate-limit response. A single, **global** (not per-repo --
GitHub's rate limit is per-token, shared across every repo that token
touches, unlike Gitea's own per-project sync/review locks which exist for
a different reason, concurrency-safety, not rate limiting) in-memory
cooldown gate:

```python
_github_rate_limit_lock = threading.Lock()
_github_rate_limited_until = 0.0

def _github_note_rate_limit(headers, status: int) -> None:
    """Called after every real GitHub HTTP response (success or
    HTTPError). Sets _github_rate_limited_until (never lowers an existing,
    still-active cooldown) when:
    - status in (403, 429) and a Retry-After header is present -> now +
      int(Retry-After) seconds (the most authoritative signal GitHub gives).
    - status in (403, 429) and X-RateLimit-Remaining == "0" (no
      Retry-After) -> X-RateLimit-Reset epoch seconds, if present and
      parses as an int; else now + GITHUB_RATE_LIMIT_FALLBACK_SECONDS as a
      conservative default rather than not backing off at all.
    - Otherwise (a normal 2xx/4xx with remaining quota) -> no-op. A
      malformed/non-numeric header value is tolerated (falls through to
      the fallback default, never raises)."""
```

`_github_api`/`_github_api_raw` check `time.time() <
_github_rate_limited_until` (under the lock) **before** building any
request; if still cooling down, they short-circuit with a `(429, {"error":
"rate limited, retry after <n>s"})`-shaped return and make zero HTTP calls
-- this is what acceptance criterion 10 below verifies directly (mocked
`urlopen` call count).

### 4. Polling vs. webhook -- the actual call, not a default
**Decision: polling, no webhook, consistent with the existing precedent --
and this is a real, reasoned call, not just following the path of least
resistance.** GitHub's webhook ergonomics genuinely are better than
Gitea's own (no poll-interval latency, push-driven), but the *reason* 2c
part 1's webhook design was originally rejected was never "polling is
easier to implement" -- it was that a webhook needs a **new, real
listening endpoint reachable from outside this box**, which is new attack
surface this project has twice now explicitly decided isn't worth it (2c
part 1's original rejection; item 8's reuse of polling rather than
revisiting that). That reasoning is host-agnostic: a GitHub webhook would
need to reach the switchboard from the public internet (github.com's own
servers, not a LAN-local Gitea container), which is if anything a *larger*
new attack surface than the original Gitea-webhook proposal was, not a
smaller one -- signature verification (HMAC over `X-Hub-Signature-256`)
mitigates spoofing but doesn't remove the new open port itself. GitHub's
API rate limit (5,000 req/hour per token, shared across every repo that
token touches) also argues for a real interval, not the loopback-cheap 45s
default `GITEA_POLL_INTERVAL_SECONDS` uses today -- part 2, when it builds
the actual poll loop, should use its own separate, more conservative
default (a `GITHUB_POLL_INTERVAL_SECONDS` in the 120s+ range is a
reasonable starting point, left as part 2's own decision to finalize
against real usage). Recorded here now, per this item's explicit
instruction to make the real call during scoping rather than leave it
abstract for part 2 to re-litigate.

### 5. Item 8 integration
**Deferred to part 2**, as scoped above. This part's job is only to make
the dispatch layer possible: once `detect_project_origin(name)` and the
`github_*()` functions exist, part 2's actual integration is a thin
dispatch in `_ai_reviewer_poll_repo()`/`_ai_reviewer_review_run()` --
branch on the project's detected origin kind and call either the existing
`_gitea_api`-based path or the new `github_*()` functions, still funneling
through the same `teams.review_pr_diff()` for the actual review generation
and the same comment-posting shape either way. Not designed further here
to avoid re-deciding it twice; recorded so part 2 doesn't have to
rediscover this framing from scratch.

## Affected areas
- `app/app.py` -- new functions/constants only (`_project_origin_url`,
  `_classify_origin_url`, `detect_project_origin`, `_github_api`,
  `_github_api_raw`, `_github_note_rate_limit`, `github_list_open_prs`,
  `github_pr_diff`, `github_list_branches`, `github_post_pr_comment`, plus
  the `GITHUB_*` module-level constants). No existing function is modified.
  No new Flask route.
- `config/switchboard.env.example` -- new `GITHUB_TOKEN` documented,
  commented-out block, placed near `GITEA_API_TOKEN`'s own block.
- New test file, `tests/test_github_api.py`, following
  `tests/test_gitea.py`'s established convention exactly: monkeypatch
  `urllib.request.urlopen` directly to test `_github_api`/`_github_api_raw`
  (mirrors that file's own `GiteaApiTests`), then monkeypatch those two
  functions to test the higher-level `github_*()` wrappers, plus a
  dedicated class for `_project_origin_url`/`_classify_origin_url`/
  `detect_project_origin` that monkeypatches `subprocess.run` (mirroring
  how other tests in this codebase already fake `subprocess.run` for
  git/script calls) rather than touching a real git repo.
- No schema/data-model change, no new privileged script, no new sudoers
  entry, no UI/template change.

## Edge cases
- No `origin` remote configured at all (local-only `git init` project, or
  an upload-wizard project with no remote) -- `_project_origin_url()`
  returns `None`; `detect_project_origin()` reports `kind: "none"`, no
  exception anywhere in the chain.
- `PROJECTS_DIR/<name>` isn't a git repository at all -- same as above
  (`git remote get-url` exits non-zero either way; the caller doesn't need
  to distinguish "not a repo" from "repo, no origin").
- `origin` URL is syntactically malformed / not parseable by either the
  scheme or scp-shorthand path -- `_classify_origin_url()` catches this and
  returns `kind: "external"`, `owner`/`repo`: `None`, never raises.
- Host comparison is case-insensitive (`GitHub.COM` classifies as
  `"github"`) and loopback comparison is IP-semantic, not a bare string
  compare (`::1` also classifies as `"local"`, not just the literal string
  `"127.0.0.1"`).
- Both GitHub URL forms item 16's own clone flow can currently produce for
  a private repo (SSH-only today, per item 16's own shipped state) are
  covered: `git@github.com:owner/repo.git` (scp-shorthand) and
  `ssh://git@github.com/owner/repo.git` (scheme form) both resolve to the
  same `owner`/`repo`, independent of which form a given clone happened to
  use.
- `GITHUB_TOKEN` unset -- every `github_*()` convenience function returns a
  clear `{"ok": False, "error": ...}` without ever sending an unauthenticated
  request, mirroring `create_project()`'s own `GITEA_API_TOKEN` check.
- GitHub API rate-limited mid-burst -- the cooldown gate is global (not
  per-repo), so once tripped, every `github_*()` call across every project
  short-circuits until the cooldown clears, rather than each project's
  calls independently re-discovering the same 403 and re-triggering their
  own separate backoffs.
- Malformed/non-numeric rate-limit headers -- tolerated, falls through to
  `GITHUB_RATE_LIMIT_FALLBACK_SECONDS`, never raises or crashes the caller.
- Concurrent calls from multiple threads (once part 2 exists, several
  projects' poll passes could call `github_*()` around the same time) --
  the rate-limit gate read/write is lock-guarded (`_github_rate_limit_lock`),
  matching the `_gitea_map_lock`/`_ai_reviewer_state_lock` precedent for
  shared in-memory/on-disk state elsewhere in this file.

## Acceptance criteria
- [ ] Given a project whose `origin` resolves to
      `http://oauth2:<token>@127.0.0.1:<port>/owner/repo.git` (any loopback
      host, any port), `detect_project_origin()` returns `kind: "local"`.
- [ ] Given a project whose `origin` is `https://github.com/owner/repo.git`,
      `detect_project_origin()` returns `kind: "github"`, `owner: "owner"`,
      `repo: "repo"` (`.git` suffix stripped).
- [ ] Given `origin` = `git@github.com:owner/repo.git` (scp-shorthand),
      `detect_project_origin()` returns the same result as the HTTPS case.
- [ ] Given `origin` = `ssh://git@github.com/owner/repo.git`,
      `detect_project_origin()` returns the same result as the HTTPS case.
- [ ] Given a project with no `origin` remote (or not a git repo at all),
      `detect_project_origin()` returns `kind: "none"` and raises nothing.
- [ ] Given `origin` pointing at a non-loopback, non-github.com host (e.g.
      `git.example.com`), `detect_project_origin()` returns `kind:
      "external"`, `owner`/`repo`: `None`.
- [ ] Host matching is case-insensitive: `origin` = `https://GitHub.COM/o/r`
      still classifies as `"github"`.
- [ ] `_github_api`/`_github_api_raw`, given a mocked `urllib.request.urlopen`
      returning a 200 response, return `(200, <parsed body>)` and never
      raise for a non-2xx status (mirrors `_gitea_api`'s own contract,
      exercised via `urllib.error.HTTPError`); only a mocked
      `URLError`/`TimeoutError` raises `ConnectionError`.
- [ ] Every request `_github_api`/`_github_api_raw` build includes
      `Authorization: Bearer <GITHUB_TOKEN>`, a non-empty `User-Agent`, and
      `X-GitHub-Api-Version` -- asserted directly against the captured
      `urllib.request.Request` object in the mock.
- [ ] Given a mocked response with `X-RateLimit-Remaining: 0` and a
      `X-RateLimit-Reset` epoch, a subsequent `_github_api`/`_github_api_raw`
      call made (in the test, by monkeypatching `time.time()`) before that
      epoch short-circuits to `(429, ...)` **without invoking the mocked
      `urlopen` at all** (assert on the mock's call count); a call made
      after that epoch proceeds normally and does invoke `urlopen`.
- [ ] Given a mocked 403 response with a `Retry-After: 30` header (no
      rate-limit headers), the same short-circuit-then-recover behavior
      holds using the `Retry-After` value.
- [ ] `github_list_open_prs`/`github_pr_diff`/`github_list_branches`/
      `github_post_pr_comment` each build the correct method+path (and
      body, for the comment call) against a monkeypatched `_github_api`/
      `_github_api_raw`, and return `{"ok": True, ...}` shaped results
      parallel to their Gitea-integration counterparts.
- [ ] Given `GITHUB_TOKEN` unset (monkeypatched to `""`), every `github_*()`
      convenience function returns `{"ok": False, ...}` and the mocked
      `urlopen`/`_github_api` is never called.
- [ ] `git grep -n "Flask\|@app.route\|def do_GET\|def do_POST"` (or
      equivalent structural check) on the diff shows no new route -- and no
      HTML/JS template in `app/app.py` changes as part of this commit
      (verifies the "no UI in this part" non-goal held).
- [ ] `git -C PROJECTS_DIR/<name> remote get-url origin` runs directly, with
      no `sudo`/`RUN_USER` hand-off anywhere in the new code (verifies the
      "no new privilege boundary" claim in "Proposed approach" §1).

## Open questions
- **Fallback rate-limit cooldown value** (`GITHUB_RATE_LIMIT_FALLBACK_SECONDS
  = 60`): a reasonable, conservative default for the case where GitHub
  returns a 403/429 with neither `Retry-After` nor a parseable
  `X-RateLimit-Reset` -- not something GitHub's real API is expected to do
  often, so the exact number is a low-stakes assumption, adjustable later
  without any design change.
- **`GITHUB_TOKEN` scope recommendation**: this spec recommends a classic
  PAT with `repo` scope in the config comment (simplest, matches how
  `GITEA_API_TOKEN`'s own comment recommends a specific scope string) --
  whether to also document the finer-grained fine-grained-PAT permission
  set (`Pull requests: read and write`, `Contents: read`) is left to
  whoever writes the config comment's exact wording; not a design decision
  that affects any code path.
- **Whether/where to add prose documentation** beyond the
  `switchboard.env.example` comment (e.g. a short new section in
  `docs/GIT_HOSTING.md`, or leaving it purely to the config comment plus
  this spec) -- `docs/GIT_HOSTING.md`'s title is Gitea-specific, so folding
  GitHub content into it may or may not read naturally; left to the
  developer's judgment, non-blocking either way.
- **Part 2's own scope** (poll-loop wiring, `GITHUB_POLL_INTERVAL_SECONDS`,
  item 8 host-agnostic dispatch, and whatever UI surface becomes warranted
  once GitHub-origin projects are actually pollable) is intentionally not
  designed in detail here -- see "Proposed approach" §4/§5 for the framing
  decisions already made so part 2 doesn't have to re-derive them, but the
  concrete implementation (state file shape if any, exact dispatch code)
  is part 2's own spec to write once this part has shipped.

## Risk / rollback notes
- Purely additive: no existing function is modified, no existing route or
  template changes, no schema change, `GITHUB_TOKEN` defaults to empty
  (every new code path degrades to a clear error, never a crash, when
  unset) -- functionally a no-op for every existing installation until an
  operator both sets `GITHUB_TOKEN` and something (part 2, later) actually
  calls these functions. Safe to revert by deleting the new functions/
  constants/config block/test file; nothing else in the codebase depends
  on them yet.
- The one real external-network-facing code path this introduces
  (`_github_api`/`_github_api_raw` calling `https://api.github.com`) is
  outbound-only, request-thread-synchronous (same shape `_gitea_api`
  already has), gated behind `GITHUB_TOKEN` being set -- no new inbound
  attack surface, consistent with the no-webhook decision above.
