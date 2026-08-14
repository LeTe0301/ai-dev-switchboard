# Spec: Backlog item 16 — create a new project by `git clone <url>` directly

## Summary
Add a third "add a project" entry point alongside "+ New project" (item 2b,
Gitea-API-backed) and "Upload folder / .zip" (item 3): a "Clone from URL"
action that takes an arbitrary existing remote git repo URL and clones it
directly into `PROJECTS_DIR/<name>` via the same privileged clone hand-off
pattern item 2b already established, with public-repo cloning fully
supported this cycle and HTTPS/token private-repo auth explicitly deferred
(SSH-based private clones already work via `RUN_USER`'s own ambient SSH
setup — see "Private-repo auth" below).

## Goals
- A new web UI action, `POST /projects/clone`, that clones `{url}` (with an
  optional `{name}` override) into `PROJECTS_DIR/<name>` and registers it
  exactly like any other project — no Gitea involvement, no upload/zip
  step.
- Strict allowlist URL validation that rejects anything that isn't a
  plausible `http(s)://` or `ssh://` (including git's `user@host:path`
  scp-like shorthand) remote, closing off the known git
  argument-injection / `ext::`/`fd::` transport-helper RCE shapes, local
  paths, and `file://`.
- A sensible default project name derived from the URL's last path
  segment, with the same collision handling `create_project()` already
  uses (reject, don't auto-uniquify).
- The same clone-privilege-separation shape as item 2b
  (`scripts/new-project-from-gitea.sh`): all validation/naming/collision
  checks run unprivileged in `app.py`; only the final
  `mkdir`/`chown`/`git clone` crosses into root via a new, narrowly-scoped
  privileged script.
- Bounded, non-hanging, non-crashing failure handling for a bad/unreachable
  URL, an auth-requiring private repo (this cycle: a fast, clear failure,
  not a hang), a timeout, or an oversized clone.

## Non-goals
- **Private-repo authentication via a switchboard-managed HTTPS token or a
  switchboard-managed SSH key — deferred to a fast-follow.** See "Private-repo
  auth" below for the reasoning and the concrete future shape. This cycle
  ships public-repo cloning (`http://`/`https://` with no credentials) plus
  `ssh://`/scp-like cloning that succeeds only if `RUN_USER` already has
  working SSH access to the target host (its own pre-existing keys/
  `~/.ssh/config`/`known_hosts` — nothing switchboard-specific).
- No `git ls-remote` (or any other) unprivileged reachability pre-check
  before crossing the privilege boundary. Both existing sibling paths do
  meaningful unprivileged work first (Gitea API call; zip stage + detect),
  but their equivalent here — URL/name validation and the collision check —
  already happens unprivileged before dispatch. Adding a second network
  round-trip (with its own `GIT_TERMINAL_PROMPT`/`GIT_SSH_COMMAND`/timeout
  plumbing duplicated) to "pre-verify" a URL that the privileged script is
  about to try anyway isn't worth the complexity; the privileged clone
  attempt itself is the single source of truth for "does this URL work",
  same as this call being synchronous end-to-end.
- No `GITEA_REPO_MAP_FILE` entry and no wiring into item 2c's poll-based
  sync — that map/poll is specifically for projects backed by *this
  switchboard's own* Gitea instance. A URL-cloned project's `origin` may or
  may not even be a Gitea host. Tracking and remotely interacting with an
  arbitrary non-Gitea origin (GitHub PRs/comments/branches) is item 17's
  job, not this one — this item only gets the repo onto disk with a normal
  `origin` remote pointing at whatever URL was given, which is exactly the
  substrate item 17 needs later.
- No progress streaming / background-thread polling for the clone itself.
  Synchronous request/response, same as `create_project()` and
  `confirm_upload()` — not the team-run-style background-thread-plus-poll
  shape (a team run is a genuinely long multi-round agentic loop; a clone
  is a single bounded operation, same class as those two).
- No SSH key generation, import, or management UI. If `ssh://`/scp-like
  cloning is used, it relies entirely on whatever SSH setup `RUN_USER`
  already has — the switchboard doesn't create, store, or manage that key
  material, same "the switchboard doesn't constrain [RUN_USER's] access, by
  design" principle `docs/ARCHITECTURE.md` already states for that account.
- No lifecycle management (rename, re-point origin, remove) beyond initial
  registration — same scope boundary items 2b/3 already accepted.

## Background / current state
- **Item 2b** (`create_project()`, `app/app.py:1232`): validates `name`
  against `NAME_RE`, checks collision against `instance_names()`, creates
  the actual repo via Gitea's own REST API (`POST /user/repos`) as
  `SVC_USER`, then crosses the privilege boundary via
  `subprocess.run(["sudo", NEW_PROJECT_FROM_GITEA_SCRIPT, owner, repo_name, name], timeout=30)`.
  `scripts/new-project-from-gitea.sh` re-validates `name`/`owner`/`repo` in
  bash (defense in depth), atomically `mkdir`s `PROJECTS_DIR/<name>` (no
  `-p`, so it fails outright rather than merging into an existing
  directory — closes the TOCTOU race between `app.py`'s collision check and
  the script actually running), `chown`s it to `RUN_USER`, then clones
  directly as `RUN_USER` via `su "$RUN_USER" -s /bin/bash -c "git clone '$CLONE_URL' '$DEST'"`.
  Its sudoers entry (`install.sh` ~line 453,
  `$SVC_USER ALL=(root) NOPASSWD: .../ai-dev-switchboard-new-project-from-gitea.sh *`)
  is gated behind `--with-git-hosting`.
- **Item 3** (folder upload, `app/app.py` ~1298-1530): two-phase
  (detect-unprivileged / confirm-and-register), with its own privileged
  hand-off `scripts/new-project-from-upload.sh`, installed **unconditionally**
  (base `install.sh` block, ~line 401/440) — "this feature is explicitly the
  project-registration path for people *without* git hosting"
  (`docs/ARCHITECTURE.md`). `_derive_project_name()` (`app/app.py:1422`)
  sanitizes an arbitrary raw string into a `NAME_RE`-valid name, falling
  back to `"upload-<8 hex chars>"` if nothing usable survives. Size limits
  (`UPLOAD_MAX_BYTES`, `UPLOAD_MAX_ENTRIES`) and zip-slip protection
  (`_zip_entry_target()`) are this item's precedent for "sane limits
  against a malicious/oversized upload".
- `NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$")` (`app/app.py:688`)
  is the one project-name validity rule every creation path already shares.
- `deploy_run()` (`app/app.py:1180`) is this codebase's precedent for a
  synchronous, request-thread subprocess call wrapped in
  `try/except (subprocess.SubprocessError, OSError)` (which also catches
  `subprocess.TimeoutExpired`, a `SubprocessError` subclass) — the pattern
  this item's own `clone_project_from_url()` should follow, since neither
  `create_project()` nor `confirm_upload()` currently guard their own
  `subprocess.run(..., timeout=...)` calls against a timeout exception at
  all (a pre-existing gap in both, out of scope to fix here, but not one
  this new code should repeat).
- Item 16's own backlog text names one open question: where a private-repo
  clone credential lives, "following this project's existing
  `switchboard.env`-style credential-storage convention." See "Private-repo
  auth" below for how this spec settles it.

## Proposed approach

### 1. URL validation — allowlist, not denylist
New constants/function near `NAME_RE` (`app/app.py:688`):

```python
CLONE_URL_MAX_LEN = 2048
_CLONE_URL_SCHEME_RE = re.compile(r"^(https?|ssh)://\S+$", re.IGNORECASE)
_CLONE_URL_SCP_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:\S.*$")

def _validate_clone_url(url: str) -> str | None:
    """Returns an error message if url is rejected, None if it passes.
    Allowlist-only: only http(s)://, ssh://, or git's scp-like
    user@host:path shorthand are accepted. Everything else — file://,
    git://, ext::/fd:: transport helpers (a known git RCE shape when an
    attacker controls the clone URL), a bare/relative local filesystem
    path, or a string with no recognizable scheme at all — is rejected
    before any subprocess is ever spawned. This allowlist is also what
    blocks git's own known argument-injection shape (a "URL" that's
    actually a `-oProxyCommand=...`-style flag): every accepted pattern
    above requires a fixed non-'-' prefix (a scheme name, or a bare
    username matching [A-Za-z0-9_.-]+), so a leading '-' can never match
    either regex.
    """
    if not url or not isinstance(url, str):
        return "a URL is required"
    if len(url) > CLONE_URL_MAX_LEN:
        return f"URL is too long (max {CLONE_URL_MAX_LEN} characters)"
    if any(ord(c) < 0x20 or c == "\x7f" for c in url):
        return "URL contains control characters"
    if _CLONE_URL_SCHEME_RE.match(url) or _CLONE_URL_SCP_RE.match(url):
        return None
    return ("unsupported URL — use http://, https://, ssh://, or "
            "user@host:path (git's own shorthand)")
```

`scripts/new-project-from-url.sh` (see §3) re-checks the same allowlist in
bash before touching disk — same "never trust the caller blindly, this
script carries a broad root grant" discipline both sibling scripts already
state explicitly in their own headers.

### 2. Name derivation and collision handling
Extend the existing `_derive_project_name()` (`app/app.py:1422`) with an
optional prefix parameter (default preserves the upload wizard's existing
behavior unchanged):

```python
def _derive_project_name(raw: str, fallback_prefix: str = "upload") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", raw or "")
    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", cleaned)[:60]
    if NAME_RE.match(cleaned):
        return cleaned
    return f"{fallback_prefix}-{secrets.token_hex(4)}"
```

New helper for the "what's the repo's own name" heuristic (naming only —
`_validate_clone_url()` has already run by the time this is called, so this
never needs to itself reject anything):

```python
def _last_path_segment_from_clone_url(url: str) -> str:
    m = _CLONE_URL_SCP_RE.match(url)
    path = url.split(":", 1)[1] if m else urllib.parse.urlsplit(url).path
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if last.endswith(".git"):
        last = last[:-4]
    return last
```

`clone_project_from_url()` (see §3) calls
`_derive_project_name(_last_path_segment_from_clone_url(url), fallback_prefix="clone")`
when no explicit `name` override is given, so an unusable/empty segment
(e.g. a URL ending in `/`) falls back to `clone-<8 hex chars>` rather than
an empty or invalid name. An explicit `name` override is validated against
`NAME_RE` directly, exactly like `create_project()`'s own `name` handling.
Collision check: `if name in instance_names(): return False, f"'{name}' already exists."`
— same message shape as `create_project()`, no auto-uniquify (consistent
with every other creation path in this codebase).

### 3. `clone_project_from_url()` — app.py orchestration
New function placed directly after `create_project()` (`app/app.py:1232-1289`),
before the folder-upload section starts — grouping the three "create a
project" paths together:

```python
def clone_project_from_url(url: str, name_override: str) -> tuple[bool, str]:
    url = (url or "").strip()
    err = _validate_clone_url(url)
    if err:
        return False, err

    name = (name_override or "").strip()
    if name:
        if not NAME_RE.match(name):
            return False, "Use letters, numbers, spaces, - or _ (must start with a letter/number)."
    else:
        name = _derive_project_name(_last_path_segment_from_clone_url(url), fallback_prefix="clone")

    if name in instance_names():
        return False, f"'{name}' already exists."

    try:
        r = subprocess.run(["sudo", NEW_PROJECT_FROM_URL_SCRIPT, url, name],
                           capture_output=True, text=True, timeout=CLONE_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"clone failed: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "clone script failed").strip()[:300]
    return True, ""
```

New globals (placed alongside `NEW_PROJECT_FROM_GITEA_SCRIPT`,
`app/app.py` ~149):

```python
NEW_PROJECT_FROM_URL_SCRIPT = os.environ.get(
    "NEW_PROJECT_FROM_URL_SCRIPT",
    "/usr/local/bin/ai-dev-switchboard-new-project-from-url.sh")
# Generous relative to the 30s/60s timeouts create_project()/confirm_upload()
# use for their own privileged scripts — an arbitrary external repo's
# history can legitimately take a while to transfer, unlike a Gitea repo
# this switchboard just created itself (2b) or a local cp -a (3).
CLONE_TIMEOUT_SECONDS = int(os.environ.get("CLONE_TIMEOUT_SECONDS", "180"))
```

Note `clone_project_from_url()` reads neither `GITEA_ENABLED` nor
`GITEA_API_TOKEN` — this feature works whether or not `--with-git-hosting`
was ever installed, same "general-purpose entry point, no dependency"
positioning as the upload wizard.

### 4. New route — `POST /projects/clone`
In `do_POST` (`app/app.py:4792` onward), alongside the existing
`parts[0] == "projects" and len(parts) == 2 and parts[1] == "new"` branch:

```python
elif parts[0] == "projects" and len(parts) == 2 and parts[1] == "clone":
    ok, err = clone_project_from_url(body.get("url") or "", (body.get("name") or "").strip())
    if not ok:
        return self._json({"error": err}, 400)
    self._json({"ok": True})
```

This is an ordinary JSON-body POST (`{url, name?, code?}`) — no special
early-branch the way `/projects/upload` needs for its raw-bytes body — so
it goes through the existing shared TOTP gate exactly like `/projects/new`
and `/projects/upload/confirm` already do.

### 5. New privileged script — `scripts/new-project-from-url.sh`
Installed **unconditionally**, in the base `install.sh` block alongside
`new-project-from-upload.sh` (~line 401 for the `install -m 755` step,
~line 440 for the sudoers line) — cloning an arbitrary external URL never
depends on `--with-git-hosting`. Add:
```
install -m 755 "$REPO_DIR/scripts/new-project-from-url.sh" \
    /usr/local/bin/ai-dev-switchboard-new-project-from-url.sh
...
echo "$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-url.sh *"
```

Script body (mirrors `new-project-from-gitea.sh`'s mechanical shape —
`mkdir`/`chown`/clone-as-`RUN_USER`, nothing else — with three deliberate,
explicitly-flagged deviations noted inline below):

```bash
#!/usr/bin/env bash
# Privileged hand-off for clone_project_from_url() (backlog item 16,
# docs/spec.md). Installed UNCONDITIONALLY (like new-project-from-upload.sh)
# -- clone-from-URL never depends on --with-git-hosting.
#
# Usage: new-project-from-url.sh <url> <name>
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"
CLONE_MAX_BYTES="${CLONE_MAX_BYTES:-524288000}"

if [ $# -ne 2 ]; then
    echo "Usage: $0 <url> <name>" >&2
    exit 1
fi
URL="$1"
NAME="$2"

# Defense in depth -- same discipline as the two sibling scripts.
if ! [[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9\ _-]{0,59}$ ]]; then
    echo "Invalid project name: $NAME" >&2
    exit 1
fi
# Same allowlist app.py's _validate_clone_url() already enforced --
# re-checked here in bash's own regex engine, never trusted blindly.
if ! [[ "$URL" =~ ^(https?|ssh)://[^[:space:]]+$ ]] && \
   ! [[ "$URL" =~ ^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:[^[:space:]].*$ ]]; then
    echo "Unsupported URL: $URL" >&2
    exit 1
fi

mkdir -p "$PROJECTS_DIR"
DEST="${PROJECTS_DIR}/${NAME}"

# Atomic, no -p -- closes the same TOCTOU race the sibling scripts close.
if ! mkdir "$DEST"; then
    echo "Already exists: $DEST" >&2
    exit 1
fi
chown "$RUN_USER:$RUN_USER" "$DEST"

# DEVIATION 1 from new-project-from-gitea.sh's "leave a partial clone in
# place for manual cleanup" precedent: always remove DEST on any failure
# below. Reasoning: unlike a Gitea repo this switchboard just created
# itself (tiny, essentially always succeeds) or a local cp -a (fast,
# near-atomic), an arbitrary external clone is the one creation path
# genuinely likely to fail non-trivially partway through a large transfer
# (network drop, timeout, oversized) -- leaving a large partial .git
# directory behind by default is worse here than the sibling scripts'
# assumption holds for.
cleanup() { rm -rf "$DEST"; }
trap cleanup ERR

# DEVIATION 2: the clone URL is arbitrary, attacker-influenced input --
# unlike new-project-from-gitea.sh's $CLONE_URL (built server-side from
# already-regex-constrained $OWNER/$REPO), it must NEVER be interpolated
# into a string that gets re-parsed by a shell. This idiom -- the entire
# -c script is single-quoted (fully literal, zero interpolation by THIS
# shell), and $URL/$DEST are passed as su's own trailing positional
# arguments, which su forwards to `bash -c` where they become that
# invocation's own $1/$2 -- is the standard safe way to hand untrusted
# values to `sh -c`/`su -c` without ever building a shell string out of
# them. (Confirmed against su(1): "When user is specified, additional
# arguments can be supplied, in which case they are passed to the shell.")
#
# GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=/bin/false: never block waiting on an
# interactive username/password prompt -- this cycle deliberately doesn't
# support HTTPS+token auth (docs/spec.md "Private-repo auth"), so a private
# HTTPS repo must fail fast and clearly, not hang until CLONE_TIMEOUT_SECONDS.
# GIT_SSH_COMMAND's BatchMode=yes does the same for an SSH password/
# passphrase prompt AND an unknown-host-key confirmation prompt
# (accept-new = trust-on-first-use instead of an interactive yes/no).
# GIT_ALLOW_PROTOCOL is a second, git-side allowlist enforcement --
# redundant with the regex checks above, but cheap insurance against
# ext::/fd:: transport-helper-style RCE shapes even if a future refactor
# ever loosens the regex without noticing this comment.
CLONE_OUTPUT=$(su "$RUN_USER" -s /bin/bash -c \
    'GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false \
     GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15" \
     GIT_ALLOW_PROTOCOL="http:https:ssh" \
     git clone -- "$1" "$2"' _ "$URL" "$DEST" 2>&1) || {
    echo "git clone failed:" >&2
    echo "$CLONE_OUTPUT" >&2
    exit 1
}

# DEVIATION 3 / new: post-clone size cap. Git has no way to cap a clone's
# size up front -- checked AFTER the fact instead, same "checked, and
# rejected after the fact" idea as UPLOAD_MAX_BYTES's own post-decompression
# check (docs/spec.md "Size limits" in item 3's own spec history).
SIZE=$(du -sb "$DEST" 2>/dev/null | cut -f1)
if [ -n "$SIZE" ] && [ "$SIZE" -gt "$CLONE_MAX_BYTES" ]; then
    echo "Cloned repository is ${SIZE} bytes, over the ${CLONE_MAX_BYTES}-byte limit -- removed." >&2
    exit 1
fi

trap - ERR
echo "Ready: $DEST — will show up in the web UI now."
```

Note this script has nothing to redact from `CLONE_OUTPUT` before printing
it (unlike `new-project-from-gitea.sh`'s `GITEA_API_TOKEN` redaction) —
this cycle's URL never carries a switchboard-managed secret. **The
private-repo-auth fast-follow below MUST reintroduce that same redaction
discipline** once a credential is embedded in the clone URL again.

### 6. `config/switchboard.env.example` additions
New section after the existing "Folder-upload wizard" block:
```
# ── Clone project from URL (backlog item 16) ─────────────────────────────
# Lets you register a project by pasting an existing remote repo URL
# through the web UI instead of creating a repo via Gitea (2b) or
# uploading a local folder (3) — see docs/spec.md. Works standalone, same
# as the upload wizard — install.sh installs and configures all of this
# for you unconditionally, no --with-git-hosting needed.
NEW_PROJECT_FROM_URL_SCRIPT=/usr/local/bin/ai-dev-switchboard-new-project-from-url.sh

# Bounds a single clone invocation (network + git object transfer time) --
# more generous than the 30s/60s timeouts the Gitea/upload creation paths
# use, since a real external repo's history can legitimately take a while.
#CLONE_TIMEOUT_SECONDS=180

# Post-clone size cap, checked via `du -sb` inside the privileged script
# AFTER the clone completes (git has no way to cap a clone's size up
# front). A clone exceeding this is removed and the request fails.
#CLONE_MAX_BYTES=524288000
```

### 7. Web UI entry point
A third button, "Clone from URL", next to "+ New project" and "Upload
folder / .zip" (`app/app.py` ~2178-2184). Given this is a single-step
action (URL in, project on disk out) rather than upload's multi-step
detect/review/confirm wizard, it should follow "+ New project"'s simpler
inline shape rather than the upload overlay: an expandable row with a URL
input (required) and an optional project-name-override input, a "Clone"
button, and an error slot — reusing the existing `actionPath()`/
`actionBody()`/TOTP-retry plumbing (`kind: 'clone'` → `POST /projects/clone`,
body `{url, name}`) the same way `kind: 'newproject'` already does for
`/projects/new`. Exact visual treatment (inline vs. a small overlay,
loading-state copy for a clone that can legitimately take up to
`CLONE_TIMEOUT_SECONDS`) is `docs/design.md`'s call, not this spec's — flag
for ux-designer: mention explicitly that this action can take noticeably
longer than "+ New project" (up to 180s by default) and the UI should set
expectations accordingly (a "cloning… this can take a while for a large
repository" state), not look hung.

## Private-repo auth — settled, not left open
**Decision: ship public-repo cloning only this cycle. HTTPS+token
private-repo auth is a deliberate fast-follow, not built now.**

Reasoning:
- **SSH-based private cloning needs no new switchboard-side credential
  storage at all.** `scripts/new-project-from-url.sh` clones as `RUN_USER`
  (`su "$RUN_USER" ...`, exactly like item 2b's own script) — if `RUN_USER`
  already has a working SSH key/config for a given host (the same account
  that runs every actual coding session, and per `docs/ARCHITECTURE.md`
  "needs whatever access your real agentic work needs — the switchboard
  doesn't constrain that, by design"), an `ssh://`/scp-like clone of a
  private repo on that host **already works this cycle**, with zero new
  code and zero new secret storage. This meaningfully narrows what's
  actually being deferred: only the HTTPS-token case.
- **The `switchboard.env`-style convention this backlog item points at
  doesn't map cleanly onto "arbitrary host."** Every existing
  `switchboard.env` secret is scoped to one known, singleton target this
  switchboard itself administers: `GITEA_API_TOKEN` is one token for one
  local Gitea instance; `DEPLOY_KEYS_DIR` holds one key per already-known,
  hand-mapped project (`DEPLOY_MAP_FILE`, keyed by *project name*, entries
  added by the operator only after the project already exists). A
  clone-from-URL credential has neither property: the project doesn't
  exist yet at request time (it's what's being created), and the target
  host is whatever the pasted URL happens to point at — GitHub, GitLab,
  a private Gitea elsewhere, anything. Doing this properly needs its own
  design pass (one token per host? per clone, entered in the form and
  never persisted? a hand-edited host→token map, `deploy-map.json`-style?
  TTL/rotation? redacting that credential from `CLONE_OUTPUT` the way
  `GITEA_API_TOKEN` is already redacted in the sibling script?) — exactly
  the kind of real architecture decision this pipeline's own "Open
  questions" discipline says shouldn't be guessed at inside one spec.
- This keeps this cycle's "Affected areas" to one architectural layer (a
  web route + a validation function + one new privileged script), rather
  than forcing a credential-storage subsystem into the same cycle — see
  skill 11 (load-balanced decomposition) in the product-manager role
  definition.
- Consistent with this project's own precedent of deferring a
  not-yet-well-scoped piece rather than bolting on a half-considered
  mechanism (item 17 deferred outright; item 6 deferred model choice;
  2c part 1's webhook design was rejected in favor of the simpler
  poll-based mechanism actually shipped).

**Concrete shape for the fast-follow, so a future session doesn't start
from zero:** most likely a `token` field on the same clone form, embedded
into the URL for that one clone invocation only exactly the way
`create_project()`/`new-project-from-gitea.sh` already do
(`http://oauth2:${TOKEN}@host/...`-shaped), redacted from `CLONE_OUTPUT`
before it's ever printed/logged/returned to the client, and **never**
written to `switchboard.env` or any other persisted switchboard-side
file — it lives only in the resulting clone's own `.git/config` origin
remote (RUN_USER-owned, same trust boundary as any other credential that
account already holds), matching 2b's own explicit "token reuse and where
it lives" precedent for its Gitea token. This avoids inventing a new
switchboard-wide secret-storage shape at all — it reuses the "embed in
the URL for one clone, redact from output, land in `.git/config`" pattern
already proven, rather than a new `switchboard.env` key.

## Affected areas
- `app/app.py`: `_validate_clone_url()`, `_last_path_segment_from_clone_url()`,
  `_derive_project_name()` (extended with `fallback_prefix` param),
  `clone_project_from_url()`, new globals (`NEW_PROJECT_FROM_URL_SCRIPT`,
  `CLONE_TIMEOUT_SECONDS`), new `POST /projects/clone` branch in `do_POST`,
  new "Clone from URL" button/row + `actionPath()`/`actionBody()` wiring in
  the frontend JS.
- `scripts/new-project-from-url.sh` — new file.
- `install.sh` — install step + sudoers line for the new script, in the
  base (always-installed) block alongside `new-project-from-upload.sh`.
- `config/switchboard.env.example` — new documented section.
- `docs/ARCHITECTURE.md` — add this script to the "Processes and privilege
  boundaries" list alongside the upload wizard's and Gitea's own hand-offs
  (one short paragraph, same shape as the existing two).
- Tests (new): `tests/test_new_project_from_url.py` (privileged script,
  mirroring `tests/test_new_project_from_gitea.py`'s approach exactly — a
  real local `git http-backend`-backed HTTP server for the http(s) path,
  sudo-gated real-root cases skipped cleanly when passwordless sudo isn't
  available, unprivileged argument-validation cases run unconditionally)
  and `tests/test_clone.py` (app-level: `_validate_clone_url()`,
  `_last_path_segment_from_clone_url()`, `clone_project_from_url()`'s
  collision/name-override/subprocess-failure branches, and the
  `POST /projects/clone` route, mirroring `tests/test_gitea.py`'s/
  `tests/test_upload.py`'s split between script-level and app-level tests).

## Edge cases
- Empty/whitespace-only URL → 400 "a URL is required", no subprocess spawned.
- Disallowed scheme (`file://`, `git://`, `ext::sh -c id`, `ftp://`, no
  scheme at all) → 400 before any subprocess, no directory created.
- A "URL" shaped like a bare/relative local path (`/etc/passwd`,
  `../../etc`) → rejected by the same allowlist (matches neither the
  scheme regex nor the scp-like regex, since it has no `@host:`).
- A "URL" shaped like a git argument-injection attempt
  (`-oProxyCommand=...`) → rejected (can't match either regex, since both
  require a fixed non-`-` prefix).
- URL exceeding `CLONE_URL_MAX_LEN` (2048) → 400, no subprocess spawned.
- URL containing control characters/NUL → 400, no subprocess spawned.
- Explicit `name` override that fails `NAME_RE` → 400, same message
  `create_project()` already uses.
- Derived name from a URL with an empty/unusable last path segment (e.g.
  trailing `/`) → falls back to `clone-<8 hex chars>` via
  `_derive_project_name(..., fallback_prefix="clone")`.
- Name collision (explicit or derived) with an existing project → 400
  `"'<name>' already exists."`, checked before the privileged script runs;
  the atomic `mkdir` (no `-p`) in the script itself closes the residual
  TOCTOU race the same way both sibling scripts already do.
- Two concurrent clone requests deriving/targeting the same name → exactly
  one succeeds (first `mkdir` wins); the other fails cleanly with "Already
  exists", no corrupted/partial project, no crash.
- Unreachable host / DNS failure / connection refused → git fails fast
  (well under `CLONE_TIMEOUT_SECONDS`), script exits 1 with the captured
  (non-secret) output, `clone_project_from_url()` returns a clipped
  (`[:300]`) error, `DEST` is removed by the `ERR` trap. No crash, no
  orphaned directory.
- Genuinely slow/stalled transfer → bounded by
  `subprocess.run(..., timeout=CLONE_TIMEOUT_SECONDS)`; `TimeoutExpired`
  (a `subprocess.SubprocessError` subclass) is caught, returns a clean 400
  — same pattern `deploy_run()` already establishes. The now-orphaned `su`/
  `git` process tree under the killed script is a known, accepted gap
  (see "Risk / rollback notes" — same class of gap this codebase already
  accepts for `deploy_run()`'s own subprocess timeouts, not new here).
- Private HTTPS repo (auth required, unsupported this cycle) → git fails
  fast thanks to `GIT_TERMINAL_PROMPT=0`/`GIT_ASKPASS=/bin/false` (no hang
  until timeout); the resulting stderr (something like "could not read
  Username... terminal prompts disabled") is clipped and returned as the
  400 error — not a polished "this feature isn't supported yet" message
  this cycle, but a real, prompt, non-hanging failure. Acceptable for this
  cycle's scope; a future fast-follow could pattern-match this specific
  git error text to surface a friendlier message pointing at the
  (not-yet-built) token field.
- Private SSH repo where `RUN_USER` has no working key for that host →
  same fast-fail shape via `BatchMode=yes` (no interactive password/
  passphrase/host-key prompt); clipped error surfaced the same way.
- Successfully-cloned repo whose on-disk size exceeds `CLONE_MAX_BYTES` →
  script removes `DEST` and exits 1 with a clear message; the project never
  appears in the project list.
- Empty remote repo (freshly created, no commits) → clones fine to an
  empty-except-`.git` directory; no special-casing needed, same as
  cloning any other legitimate empty repo.
- `PROJECTS_DIR` itself missing → `mkdir -p "$PROJECTS_DIR"` in the script,
  same as both sibling scripts.
- SSRF-shaped target (`http://127.0.0.1:<port>/...`, a cloud metadata IP,
  etc.) — **deliberately not blocked.** git will simply attempt the smart
  HTTP/SSH handshake against whatever host is given, which fails harmlessly
  against anything that doesn't actually speak the git protocol; this is
  the same "an authenticated switchboard operator is already trusted to
  target arbitrary hosts" trust level `DEPLOY_MAP_FILE`'s hand-edited
  host field and the host-control row's own `HOST_IP` already carry.
  Called out here explicitly as an accepted, non-blocking risk rather than
  an oversight.
- `install.sh` re-run on an already-installed box → the new install/sudoers
  lines are idempotent, same deterministic-overwrite discipline every other
  line in that block already follows.

## Acceptance criteria
- [ ] Given a valid public `https://` URL and no `name` override, when
      `POST /projects/clone` is submitted on a TOTP-verified session, then
      a new project appears under `PROJECTS_DIR/<derived-name>` with a
      working `.git` directory owned by `RUN_USER`, and shows up in the
      web UI's project list without a service restart.
- [ ] Given the same request but with an explicit `name` field, then the
      project registers under that exact name instead of a derived one.
- [ ] Given a URL with a disallowed scheme (`file://`, `ext::sh -c id`,
      `git://`, or a bare local path), when submitted, then the request is
      rejected with 400 before any subprocess is spawned and no directory
      is created under `PROJECTS_DIR`.
- [ ] Given a URL shaped like `-oProxyCommand=...` (no scheme, leading
      `-`), when submitted, then it is rejected the same way — never
      reaches `git clone` as an argv token.
- [ ] Given an explicit `name` that fails `NAME_RE`, when submitted, then
      rejected 400 with the same message `create_project()`'s own
      `name` validation already uses.
- [ ] Given a URL or resolved name that collides with an existing project,
      when submitted, then rejected 400 `"'<name>' already exists."` and
      the existing project is untouched.
- [ ] Given two concurrent clone requests that resolve to the same name,
      when both are submitted, then exactly one project is created; the
      other request fails cleanly with no partial/corrupted state and no
      server crash.
- [ ] Given a URL to an unreachable/nonexistent host, when submitted, then
      the request returns 400 with a clipped, non-crashing error message
      well before `CLONE_TIMEOUT_SECONDS` elapses, and no directory is left
      behind under `PROJECTS_DIR`.
- [ ] Given a URL to a private HTTPS repo requiring credentials this cycle
      doesn't supply, when submitted, then the request fails fast (well
      under `CLONE_TIMEOUT_SECONDS`, thanks to
      `GIT_TERMINAL_PROMPT=0`/`GIT_ASKPASS=/bin/false`) rather than hanging.
- [ ] Given a successfully-cloned repository whose size on disk exceeds
      `CLONE_MAX_BYTES`, when the clone completes, then the destination
      directory is removed, the request returns 400, and the project never
      appears in the project list.
- [ ] Given the switchboard installed without `--with-git-hosting` at all
      (`GITEA_ENABLED=0`, no `GITEA_API_TOKEN`), when a clone-from-URL
      request is submitted, then it still succeeds — this feature has no
      Gitea dependency, unlike `create_project()`.
- [ ] Given an `ssh://` or scp-like URL to a host `RUN_USER` already has
      working SSH access to, when submitted, then the clone succeeds using
      that pre-existing access, with no switchboard-side credential
      involved at all.
- [ ] `scripts/new-project-from-url.sh`, run directly with a wrong argument
      count or an invalid `<name>`/`<url>`, exits 1 with a clear message
      and touches no filesystem state (same defense-in-depth re-validation
      contract the two sibling scripts already have their own tests for).

## Open questions
None blocking. The one open question the backlog text itself flagged
(private-repo credential storage) is resolved above under "Private-repo
auth" — deferred as a scoped, reasoned fast-follow rather than left open.
Two small non-blocking implementation notes for whoever picks this up:
- **`git clone -- <url> <dest>`'s `--` end-of-options marker**: assumed
  supported by the target environment's installed git (a reasonable
  assumption for any git new enough to be in active use, but not verified
  against a specific minimum version here) — cheap extra defense on top of
  the allowlist's own host/path validation, not load-bearing on its own.
  Correction (docs/test-review.md's item 16 re-review): an earlier version
  of this note claimed the allowlist alone "fully closes" the
  argument-injection shape regardless of `--` support; that was
  demonstrably false against the allowlist's first (lookahead-based)
  revision, which two independently-constructed adversarial URLs bypassed
  in real `sudo` runs. The allowlist was subsequently rewritten to parse
  and validate the real host (and, for scp-like shorthand, the real path)
  component directly (`_clone_url_host_is_safe()` in `app/app.py`) rather
  than guarding only the character immediately after a fixed anchor — this
  revision is believed to close the class, but `--` remains real,
  independent defense-in-depth rather than a redundant belt-and-suspenders
  claim on top of an infallible regex.
- The frontend copy/loading-state for a clone that can legitimately take up
  to `CLONE_TIMEOUT_SECONDS` (180s default) is left to `docs/design.md` —
  flagged under "Web UI entry point" above for the ux-designer to make an
  explicit call on rather than defaulting to the same instant-feedback
  affordance "+ New project" uses today.

## Risk / rollback notes
- Entirely additive: one new route, one new function, one new script, one
  new unconditionally-installed sudoers line. No existing route, function,
  or script is modified in a way that changes its own behavior (only
  `_derive_project_name()` gains an optional parameter with a
  backward-compatible default).
- Rollback is a plain revert of this change set — no data migration, no
  schema, no state file. A project already cloned via this feature before
  a rollback remains on disk as an ordinary `PROJECTS_DIR` entry exactly
  like any other project (nothing about it is special/tagged) — it simply
  loses the entry point that created it, not the entry itself.
- The privileged script carries a broad root grant (same class of risk item
  2b's and item 3's own scripts already carry) — mitigated the same way
  those are: narrow argument count, defense-in-depth re-validation of both
  arguments, mechanical-only body (`mkdir`/`chown`/clone/`du`, nothing
  else), no shell-interpolation of the attacker-influenced URL anywhere
  (see §5 "DEVIATION 2").
- A killed-on-timeout `git clone` subprocess tree (see "Edge cases" above)
  can leave an orphaned `git`/transport child process running briefly past
  the request's own return — the same accepted, not-new gap
  `deploy_run()`'s own timeout already carries in this codebase; not a
  regression introduced by this item.
