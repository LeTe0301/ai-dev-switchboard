# Spec: Folder upload → auto-detect repo(s)

**Revision note:** this is a revision of the original spec after user review.
Three of six Open Questions were overturned (monorepo handling, upload
transport, UI flow), one gained a new sub-requirement (size cap), one was
confirmed as-is (auto git init), one was deferred and is now treated as
resolved-by-default (TOTP-via-query-string). See "Revision history" at the
bottom for a compact diff of what changed and why. This revision changes the
shape of the feature substantially: it is now a client-side-zip, two-phase,
step-by-step wizard rather than a single-shot "already have a .zip → upload →
immediate register-or-reject."

## Summary
Add a second way to create a project — picking a local folder (or an
already-made `.zip`) through the web UI, via a step-by-step wizard that zips
it client-side, uploads it with a visible progress bar, lets the user review
the server-detected repo structure and choose how to split it (one project,
or break specific nested/sub repos out as their own projects), then
registers accordingly — alongside the existing git-hosting-based "+ New
project" button, so people without `--with-git-hosting` (or with a folder
that has no git remote at all) can still get a project registered under
`PROJECTS_DIR` without touching a shell.

## Goals
- A step-by-step ("stepper") upload wizard in the web UI: pick a folder (or
  a `.zip`) → review/exclude known heavy directories → zip client-side with
  a progress bar → upload with a progress bar → review the server-detected
  structure → choose single-project vs. split-out-selected-subrepos →
  confirm → register. Every step is a real UI step, not a single-shot
  fire-and-forget action.
- Works standalone — does **not** require `--with-git-hosting` to be
  installed (this is explicitly the alternative for people who don't
  have/want that).
- Detects whether the picked tree is a single repo, a folder of independent
  subrepos, or a monorepo with embedded/vendored repos, and **surfaces that
  structure to the user for an explicit choice** rather than silently
  picking a default — same review-and-select UI for both the
  nested-`.git`-inside-a-root-repo case and the no-root-`.git`,
  multiple-subfolders case (exact rules below).
- Hardened against zip-slip and oversized/zip-bomb uploads, matching the
  seriousness the backlog calls for.
- Any registered project that isn't already a git repo gets one,
  automatically, so every row in the UI is backed by version control.
- Client-side zip building and the upload transfer are both independently
  trackable with a progress bar — no silent multi-second/multi-minute dead
  air on either step.

## Non-goals
- No `DEFLATE`/compression in the client-side zip writer — **store mode
  only** (uncompressed entries, method 0). This trades upload size for a
  vastly smaller and safer amount of vanilla JS (no compression algorithm
  to implement, just a CRC-32 checksum and binary record writing — see
  "Client-side zip writer" below). If someone wants a compressed upload,
  they can still pick an already-made `.zip` file directly in step 1 (the
  wizard accepts either a live folder or a pre-made `.zip` — see UI below),
  which skips client-side zipping entirely and is uploaded as-is, whatever
  compression it was made with.
- No `.tar.gz` or other archive formats accepted as the wire format — `.zip`
  only (whether built client-side or picked pre-made).
- No resumable/chunked upload, no progress-resumption after a dropped
  connection, for either of the two requests the wizard makes (upload,
  confirm) — each is one atomic request, size-capped (see below). The
  wizard as a whole now spans two requests instead of one (see "Two-phase
  protocol" below), but neither individual request resumes mid-transfer.
  Large binary-heavy repos need a different tool (git-lfs, etc.); out of
  scope.
- No manual rename/retype of a derived project name before registering —
  names are still always derived from folder names and sanitized against
  the existing `NAME_RE`, exactly as before. The review step lets the user
  choose *which folders* become projects, not *what they're called*.
- Client-side exclusion (see "Known-heavy-directory exclusion" below) is a
  **fixed list of well-known package-manager/build-output directory names**,
  checked client-side before zipping — **not** a `.gitignore` parser, not
  per-file selection, and not a destructive filesystem operation. See the
  explicit interpretation call-out under "Known-heavy-directory exclusion."
  Server-side, whatever ends up inside the uploaded zip is exactly what gets
  staged and (if selected) registered — no additional server-side filtering
  is added.
- No deduplication when a root project and a split-out nested-repo
  subproject physically overlap (see "Detection and the two-phase protocol"
  below) — splitting a nested `.git` out creates a **second, independent
  on-disk copy**; the root project's copy is left exactly as uploaded,
  untouched. No symlink/hardlink/copy-on-write trickery to avoid the
  duplication in v1.
- Doesn't touch or change the existing `/projects/new` (`create_project()`,
  `NEW_PROJECT_SCRIPT`) flow at all — fully additive and parallel.
- No support for a `.git` **file** (gitlink, as used by git worktrees and
  some submodule checkouts) counting as "this is a repo" — only a `.git`
  **directory** is detected. An uploaded worktree/submodule-style checkout
  will be mis-classified (most likely as "no `.git` anywhere" for that
  particular subtree). Known limitation, not handled.
- No server-side persistence of *which choice the user is mid-way through
  making* beyond the staged files themselves — the server only ever
  re-derives structure by re-walking the staging directory (see "Detection"
  below); it doesn't remember a client's in-flight wizard state in memory.

## Background / current state
- `app/app.py` is a single stdlib-only Python file. Projects are
  auto-discovered, not registered: `instance_names()` (line ~360) just lists
  non-hidden directories under `PROJECTS_DIR`. There is no database or
  manifest — a directory existing under `PROJECTS_DIR` *is* the
  registration.
- The existing "+ New project" flow (`create_project()`, line ~478, wired to
  `POST /projects/new`) is entirely git-hosting-based: it shells out to
  `NEW_PROJECT_SCRIPT` (default `/usr/local/bin/ai-dev-switchboard-new-project.sh`,
  only installed when `install.sh --with-git-hosting` is used — see
  `install.sh` lines ~192-234 and `scripts/new-project.sh`/`new-repo.sh`/
  `new-dev-instance.sh`). Without git hosting installed, `create_project()`
  just tells the user to `git init` a folder themselves. This is exactly the
  gap the backlog item is closing.
- **Privilege boundary (docs/ARCHITECTURE.md)**: `app.py` runs as
  `SVC_USER`, an unprivileged account whose *only* elevated capability is a
  narrow sudoers rule to run `tmux`/`ttyd`/`code-server` as `RUN_USER`, plus
  — only when git hosting is installed — `sudo (root) NEW_PROJECT_SCRIPT`.
  `SVC_USER` cannot write into `PROJECTS_DIR` directly (owned by
  `RUN_USER`). The existing scripts establish the working pattern for
  crossing this boundary: `new-project.sh` runs as root via a whitelisted
  sudoers entry and internally does
  `su "$RUN_USER" -s /bin/bash -c "..."` for the actual file operations.
  This feature needs the same shape of privileged hand-off, generalized to
  be usable **without** git hosting.
- `do_POST` (line ~985) currently reads a JSON body **unconditionally, for
  every POST route, before any routing decision is made**:
  `body = self._read_json_body()` at line 998, immediately followed by the
  shared once-per-session TOTP gate (`session_totp_ok`, lines ~1004-1009)
  which reads the confirmation code from `body["code"]`. `_read_json_body()`
  (line 941) reads exactly `Content-Length` bytes off `self.rfile` and
  `json.loads()`s them, swallowing any parse failure and returning `{}` —
  critically, **it still consumes those bytes off the socket even when
  they aren't JSON**, so a raw-binary upload endpoint can't just let this
  shared line run unmodified: doing so would silently discard the uploaded
  zip's bytes (read into `body`, thrown away as an unparseable `{}`) before
  the upload handler ever sees them. The phase-1 upload route needs its own
  early branch in `do_POST`, *before* line 998's shared call, that reads the
  raw bytes itself. See "Wire format and endpoints" below.
- `_reap_dead_state()` (line 573, called from `do_GET`'s `/status` handler
  at line 960) is the existing precedent for "opportunistic cleanup on a
  request that already happens often" rather than a dedicated background
  thread — this feature's staging-directory TTL cleanup reuses that same
  shape (see "Staging" below) instead of introducing new background-thread
  machinery.
- `NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$"` (line 475) is the
  existing project-name validation, already reused by `create_project()`;
  this feature should reuse it too rather than inventing new naming rules.
- The front-end is inlined in `PAGE_TEMPLATE` (from line 614) — plain
  vanilla JS/HTML/CSS, no build step, no external libraries.
  `startNewProject()` (859)/`toggle()` (851)/`performAction()` (807)/
  `handleActionResult()` (818) are the existing pattern for a mutating UI
  action, including the shared TOTP-code overlay flow (~lines 790-890).
  This feature's wizard is a materially bigger piece of new client-side
  code than any existing UI flow in this file (client-side zip writer +
  multi-step state machine) — still inlined in `PAGE_TEMPLATE` per the
  file's existing no-build-step convention, just larger than anything
  currently there.

## Proposed approach

### Two-phase protocol (why, and what changed from the original design)
The original (single-shot) design collapsed "receive bytes → stage → detect
→ name → register" into one request, cleaned up staging in a `finally`
block every time. A stepper wizard needs the user to actually *see* the
detected structure and make a choice before anything is registered — so
staging now has to survive **between** two separate requests:

1. **`POST /projects/upload`** — client sends zip bytes (built client-side,
   or picked pre-made — see UI below). Server validates, stages, unwraps,
   and **detects structure only** — it does **not** register anything.
   Response is a description of what was found, plus a `token` identifying
   this staged upload.
2. **`POST /projects/upload/confirm`** — client sends the `token` plus the
   user's choice (`single`, or `split` with a list of which nested/sub
   folders to break out). Server **re-walks the staging tree itself**
   (never trusts the client's JSON blindly — see "Detection" below),
   derives names, checks collisions, and only then crosses the privilege
   boundary to actually register.

Because staging now spans an unknown amount of wall-clock time (a user can
leave the review step open, get distracted, close the tab), the old
"always `shutil.rmtree` in a `finally`" cleanup no longer fits — a request
boundary no longer maps to the whole operation. Replaced with:
- **Cleanup on confirm**: `UPLOAD_STAGING_DIR/<token>/` is removed
  (best-effort `shutil.rmtree`) immediately after `/projects/upload/confirm`
  **succeeds**. On a **failed** confirm (e.g. a name collision), staging is
  deliberately left in place — the review step's "Back to review" button
  lets the user tweak their single/split selection and retry
  `/projects/upload/confirm` on the same token, and that retry needs a
  still-staged tree to be evaluated against. The TTL/idle sweep below is the
  backstop for staging left behind by a failed confirm that's never
  retried.
- **TTL/idle cleanup for abandoned uploads**: new config
  `UPLOAD_STAGING_TTL_SECONDS` (default `1800`, 30 minutes). Any
  `UPLOAD_STAGING_DIR/<token>/` whose directory mtime is older than the TTL
  and was never *successfully* confirmed (whether never attempted, or
  attempted and failed) is pruned. Reusing the existing
  `_reap_dead_state()` precedent rather than inventing new background-thread
  machinery: extend that function (already called on every `/status` poll,
  which the front-end already hits regularly) to also sweep expired upload
  tokens. No dedicated thread/timer needed.

### Wire format and endpoints
**`POST /projects/upload`** (phase 1) — the client sends the raw bytes of a
`.zip` (client-built or picked pre-made) as the request body
(`Content-Type: application/zip` or `application/octet-stream`, not
multipart — same reasoning as before: exactly one file, no field parsing
needed, keeps the server side to `zipfile` from the stdlib). `do_POST`
needs an early branch, **before** its existing line 998
`body = self._read_json_body()` call, that matches this one path and reads
`Content-Length` bytes directly off `self.rfile` itself, bypassing the
shared JSON-body read entirely (see "Background" above for why — the shared
call would otherwise silently consume and discard the raw zip bytes). TOTP
is still gated on this request (staging does consume real server
resources), using the same query-string deviation as the original spec:
`code` is read from `?code=` via
`urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)` instead of
the JSON body, then falls into the same `session_totp_ok`/428/403 logic
every other action already uses.

**`POST /projects/upload/confirm`** (phase 2) — ordinary JSON body, exactly
like every other mutating endpoint:
```json
{"code": "123456", "token": "<hex>", "mode": "single" | "split",
 "selected": ["vendor/thing", "packages/foo"]}
```
`code` only needs to be present if TOTP isn't already cleared this session
— since phase 1 (moments earlier, in the same wizard) almost always clears
it first, phase 2 typically doesn't prompt again in practice, but it goes
through the *standard* JSON-body `code` path with **no special-casing
needed** — the raw-bytes deviation is scoped to phase 1 only. `selected` is
ignored/must be empty when `mode == "single"`.

No project name is submitted by the client at either phase — names are
always derived from the zip's own contents (see Detection below).

### Size limits
Unchanged from the original design, applied entirely within phase 1 (before
anything is staged): new config `UPLOAD_MAX_BYTES` (default `104857600`,
100 MiB), read the same way as other config (`os.environ.get(...)`,
documented in `config/switchboard.env.example`). Applies twice:
1. **Before reading the body at all**: reject if `Content-Length` is
   missing, zero, or exceeds `UPLOAD_MAX_BYTES` (413). This endpoint does
   not support chunked transfer encoding.
2. **Before extracting any bytes**: after opening the uploaded data as a
   `zipfile.ZipFile`, sum `.file_size` (uncompressed) across all entries
   first; if that sum exceeds `UPLOAD_MAX_BYTES`, reject before writing
   anything to disk. This catches a zip-bomb-shaped mismatch (small
   compressed upload, huge decompressed size) that check 1 alone wouldn't —
   note that since the client-side writer is store-mode (no compression),
   this specific mismatch shape can't happen for client-built zips, but it
   still matters for the pick-a-pre-made-`.zip` fallback path, so the check
   stays.

Also cap entry count (e.g. reject if `len(zf.infolist()) > 20000`) as a
cheap additional guard against a many-tiny-files DoS shape.

The client-side "Known-heavy-directory exclusion" step (below) works
*against* this same cap from the other direction — by letting the user
exclude `node_modules`/build output/etc. before zipping, the effective
upload is usually far under 100 MiB even for repos whose working tree
alone would blow past it.

### Client-side zip writer (store mode)
**Decision, revisiting the original spec's non-goal**: the original spec
scoped a client-side zip writer out, reasoning that "a dependency-free
vanilla-JS zip encoder [is] materially more code and risk than the rest of
this feature combined." That reasoning holds for a *general-purpose,
compressing* zip encoder (which needs a DEFLATE implementation — genuinely
substantial and risky to hand-roll). It does **not** hold for a
**store-mode** writer: no compression algorithm is needed at all, only:
- A **CRC-32** checksum per file (~15 lines including the standard
  256-entry lookup table, reflected polynomial `0xEDB88320` — a
  well-documented, widely-copied algorithm, not something being invented
  here).
- **Local file header** + raw file bytes per entry (method `0` = stored,
  so compressed size == uncompressed size — no encoding step at all beyond
  writing the bytes through).
- **Central directory records** (one per entry, written after all local
  entries) + a single **End Of Central Directory** record at the end,
  referencing each entry's offset.
- Filenames encoded as UTF-8 via `TextEncoder`, with the UTF-8 flag bit
  (general-purpose flag bit 11) set on every entry, so non-ASCII paths
  extract correctly everywhere without legacy-codepage guessing.
- **No zip64 support** — deliberately not needed, since `UPLOAD_MAX_BYTES`
  (100 MiB) is far under the 4 GiB local/central-directory-size limit that
  would require it; this is a real simplification the size cap buys, worth
  calling out since it's what keeps the writer small.

This is on the order of 150-200 lines of plain `Uint8Array`/`DataView`
manipulation, no external dependency, consistent with the codebase's
stdlib-only/zero-dependency ethos (`README.md`'s "Repo layout" calls
`app/app.py` "stdlib-only Python, one file"; `docs/ARCHITECTURE.md` reiterates
"this ~900-line stdlib-only app"). The trade-off is upload size (uncompressed
zips are typically 2-5x larger than a compressed equivalent for source code)
— accepted explicitly as a non-goal above, with the pick-a-pre-made-`.zip`
fallback path available for anyone who wants compression.

### Known-heavy-directory exclusion
**Explicit interpretation call-out**: the user's request was "prompt the
user to `rm -rf` all `node_modules`." A browser cannot delete files on the
user's actual local disk from JS — there is no real filesystem access
beyond what's been picked into the `FileList`. The only feasible,
non-destructive reading: scan the picked file list (available synchronously
from `webkitdirectory`, which gives every file's relative path via
`file.webkitRelativePath`) for known heavy/ignorable directory *names*, and
**exclude matching directories from the zip being built** — not delete
anything from disk. If this isn't what was meant, it needs correcting; it's
called out here specifically so it's traceable back to the original wording
rather than silently reinterpreted.

Mechanics: after a folder is picked, group all files by directory
**basename** (not full path — a repo can have `node_modules` at several
depths; group them together as one checklist row, e.g. "`node_modules`
(14 folders, 850 files, ~120 MB)" summing matched files' `.size`), for a
fixed list of well-known package-manager/build-output directory names:
`node_modules`, `.venv`, `venv`, `env`, `__pycache__`, `.pytest_cache`,
`target`, `dist`, `build`, `vendor`, `.tox`, `.next`, `.nuxt`, `.gradle`,
`Pods`, `.cache`. (Deliberately conservative — names left off, like `bin`
or `obj`, are common enough in hand-written source layouts that excluding
them by default risks silently dropping something a user actually wanted;
better to under-exclude than over-exclude.) **`.git` is never offered as an
excludable candidate under any circumstance** — detection depends on it
being present, and excluding it would silently break the whole feature.
This list is a hardcoded JS constant in the wizard's inline script, not a
new `switchboard.env` config surface — a config knob can be added later if
someone actually needs to tune it; not adding one preemptively.

Each matched group defaults to **checked (excluded)**, per the user's
"checked/excluded by default" framing; unchecking a row re-includes those
files when the zip is built. This is client-side only — the server applies
no additional filtering; whatever ends up inside the uploaded zip is
exactly what gets staged (unchanged from the original spec's stance,
reworded above under Non-goals).

As a UX nicety (not a hard requirement), the wizard should sum the
*included* files' sizes after exclusions and warn before zipping if the
total already exceeds `UPLOAD_MAX_BYTES` — cheaper to fail fast in the
browser than to spend time zipping a large tree client-side only to get a
413 after upload.

### Progress tracking (two independently trackable phases, per request)
1. **Zipping.** Building the archive requires reading each included file's
   bytes (`file.arrayBuffer()`, async per file). The wizard updates a
   progress indicator after each file is appended to the in-memory archive
   buffer (tracked as bytes-processed-of-included-total, or files-processed-
   of-included-count) — pure `await`-in-a-loop, zero dependency.
2. **Uploading.** Deliberately uses `XMLHttpRequest` for this one request
   instead of `fetch()`, specifically to listen to
   `xhr.upload.onprogress` (`event.loaded`/`event.total`) — `fetch()`'s
   upload-progress story (a streamed `ReadableStream` request body) has
   real gaps in current browser support (notably Safari), whereas
   `XMLHttpRequest.upload.onprogress` has been universally supported for a
   long time with zero polyfill needed. This is a small, deliberate,
   called-out deviation from the rest of the app's plain-`fetch()`
   convention, scoped to only this one request — same spirit as the
   existing `?code=`-for-this-one-endpoint deviation.

Both indicators are independent — zipping reaching 100% doesn't imply
anything about upload progress, and vice versa.

### Staging
Unchanged in shape from the original design: new config
`UPLOAD_STAGING_DIR` (default `/var/lib/ai-dev-switchboard/uploads`),
created at startup next to the existing `DESC_CACHE_FILE` directory
handling — owned by `SVC_USER`, so all of phase 1 (receiving the body,
opening the zip, validating, extracting, detecting) happens
**unprivileged**, no sudo involved, before anything crosses into
`RUN_USER`'s territory in phase 2.

Each upload gets its own subdirectory: `UPLOAD_STAGING_DIR/<token>/` where
`<token>` is `secrets.token_hex(16)` (avoids collisions between concurrent
uploads and avoids a guessable path). **Cleanup timing changed** from the
original "always in a `finally` after one request" — see "Two-phase
protocol" above for the new confirm-triggered-cleanup-plus-TTL-sweep design.

### Zip-slip protection
Unchanged, applies in phase 1. For every `ZipInfo` in the opened archive,
before extracting:
- Reject the entry (and abort the **whole** upload — no partial extraction)
  if its name is an absolute path, contains a `..` path component, or
  contains a NUL byte.
- Compute
  `target = os.path.realpath(os.path.join(staging_subdir, info.filename))`
  and reject the whole upload unless `target` is `staging_subdir` itself or
  a path under it (`os.path.commonpath([target, staging_subdir]) ==
  staging_subdir`, after resolving `staging_subdir` with `realpath` too).
- Reject (skip, don't materialize) any entry whose Unix mode bits
  (`info.external_attr >> 16`) mark it as a symlink — untrusted zips must
  not be allowed to plant a symlink that later I/O could follow outside the
  staging dir.

Any rejection at this stage aborts the entire upload with a 400 and a
specific error message; nothing partial is left staged.

### Detection and the two-phase protocol
**Phase 1 (`POST /projects/upload`) — detect only, register nothing:**
1. **Unwrap a single top-level wrapper folder.** If the staged root
   contains exactly one non-hidden entry and it's a directory, and no other
   top-level entries exist (the exact shape GitHub/GitLab/Bitbucket's own
   "Download ZIP" always produces, e.g. `myrepo-main/`), treat that
   subdirectory's contents as the effective root for everything below.
   (Hidden top-level entries like a stray `.DS_Store` don't prevent the
   unwrap. Applied once, not recursively.)
2. Look for `.git` (directory) at the effective root, and walk the rest of
   the tree for any other `.git` directories elsewhere (pruning traversal
   once a `.git` dir is found — don't recurse into it). Record each nested
   `.git`'s **parent directory's** path relative to the effective root.
3. If there's no root `.git`, also list every non-hidden top-level
   subdirectory of the effective root, and count any loose top-level files.
4. Respond with the detected shape — no registration happens yet:
   ```json
   {"token": "<hex>", "root_name": "myrepo",
    "root_has_git": true, "nested_git_paths": ["vendor/thing", "packages/foo"],
    "top_level_subdirs": [], "loose_top_level_files": 0,
    "ambiguous": true}
   ```
   (`top_level_subdirs`/`loose_top_level_files` only meaningful when
   `root_has_git` is false; `ambiguous` is a convenience flag — true iff
   root has `.git` plus ≥1 nested `.git`, or root has no `.git` and ≥2
   top-level subdirs — the UI can use it to decide whether the review step
   has a real choice to make or is just a confirm-and-continue.)

**Client review step**, driven by that response — **replaces the original
spec's silent defaults for both ambiguous shapes** (this is the reversal of
decision #1):
- **"Single project"** (always offered): register the effective root as one
  `PROJECTS_DIR` entry, exactly as uploaded/zipped, regardless of any
  nested `.git`s or subdirectories present. This is the old silent default
  for the monorepo case, now an explicit choice rather than the only
  option.
- **"Split"** (offered whenever `nested_git_paths` or `top_level_subdirs`
  is non-empty): a checklist of candidates —
  - Root has `.git` (monorepo case): each `nested_git_paths` entry is a
    checkbox, **default unchecked** (matches the original spec's safer
    default — most nested `.git`s are vendored/submodule content someone
    didn't mean to surface — but now it's a pre-filled default in a real
    choice, not a forced, silent outcome).
  - Root has no `.git` (folder-of-subrepos case): each `top_level_subdirs`
    entry is a checkbox, **default checked** (matches the original spec's
    auto-register-every-subfolder behavior as the default, now overridable).
  - Whichever candidates end up checked at confirm time are broken out as
    their own separate `PROJECTS_DIR` entries, named after their own
    folder name.
  - **Root-has-`.git` + split**: the root is *also* still registered as its
    own project, containing the full original tree unchanged — including
    the split-out subfolder's files, which now exist on disk in two places.
    This is a deliberate, physical duplication (see Non-goals) rather than
    an attempt to carve the nested repo out of the root's copy.
  - **Root-has-no-`.git` + split**: unselected subdirectories and any loose
    top-level files are **not** registered anywhere (matches the original
    spec's "dropped, not copied into any project" behavior for loose files,
    now generalized to also cover unselected subfolders). If the user
    selects zero items in this shape (no root `.git` to fall back to),
    confirm rejects with a clear "select at least one project" error —
    there's nothing to register.

**Phase 2 (`POST /projects/upload/confirm`)**: given `{token, mode,
selected}`, the server **re-walks the staging tree itself** (re-running
the same detection steps 1-3 above) rather than trusting the client's
selection blindly — this closes a real gap (a stale or tampered `selected`
list referencing a path that was never actually a valid candidate). Any
`selected` path that doesn't match a currently-valid candidate from the
fresh walk is rejected with a clear error, nothing registered. Only once
the selection validates does the server move to naming/collision-checking/
privileged hand-off (below).

5. Every resulting project name (root and/or each split-out folder) is
   derived from its own folder name, sanitized against the existing
   `NAME_RE` (strip/collapse disallowed characters; if nothing usable
   survives, fall back to `upload-<8 hex chars>`). All names for this
   confirm call are derived and collision-checked **up front, before any
   privileged script runs** — against existing `PROJECTS_DIR` entries and
   against each other (root name vs. a split-out subfolder's own name, or
   two split-out subfolders sanitizing to the same name). Any collision
   rejects the **entire confirm call** with an error naming the
   collision(s) — no partial registration from this up-front check.

### Partial-failure semantics for a multi-project confirm call (new decision, flagged below)
The original spec's "no partial registration" acceptance criterion assumed
exactly one privileged-script invocation could realistically race a
manually-created folder. A confirm call can now register **more than one**
project (root + one or more split-out subfolders) in a single request, each
still via its own separate privileged-script invocation (unchanged
mechanics, see below) — so a genuine TOCTOU race (something else creates
`PROJECTS_DIR/<name>` between this confirm call's up-front collision check
and that specific invocation actually running) could now defeat *one*
invocation after *sibling* invocations earlier in the same confirm call
already succeeded. Decision: **the up-front collision check stays
all-or-nothing** (unchanged — nothing starts if any name is already known
to collide); but if a genuine race defeats one specific invocation despite
passing that check, that one registration fails and is reported by name,
while any sibling registrations already completed earlier in the *same*
confirm call are **not** rolled back — automatically deleting an
already-succeeded, `chown`'d, `git init`'d real project directory is itself
a destructive operation, and rolling it back blindly is a worse failure
mode than leaving a partially-successful confirm call with a clear error
naming which one failed. Flagged under Open Questions since this is a new
call this revision had to make, not one of the six original ones.

### Auto `git init`
Unchanged in rationale from the original spec, now applied per registered
project (root and/or each split-out subfolder) rather than assuming exactly
one: any project directory registered by this feature that doesn't already
have its own `.git` gets `git init` plus one initial commit of its
contents, run as part of the privileged hand-off below. Skipped silently
(non-fatal to the rest of that project's registration) if `git` isn't
installed on the box at all, matching the project's existing "optional
dependency degrades gracefully" pattern (e.g. `DESC_LLM_BASE_URL`).

### Crossing the privilege boundary
Unchanged in shape from the original design. All detection/naming/
collision-checking runs unprivileged as `SVC_USER`, entirely within
`UPLOAD_STAGING_DIR`. Only the final step — moving a validated, already-
named source directory into `PROJECTS_DIR/<name>` — needs `RUN_USER`'s
privileges, and reuses the exact hand-off shape `new-project.sh` already
established: a new script, `scripts/new-project-from-upload.sh`, installed
to `/usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh`, invoked
via a new config `NEW_PROJECT_FROM_UPLOAD_SCRIPT` as
`subprocess.run(["sudo", NEW_PROJECT_FROM_UPLOAD_SCRIPT, source_dir, name])`,
**once per project being registered in a confirm call** (now potentially:
root + N split-out subfolders, or just N subfolders, or just the root —
was already "N times for the folder-of-subrepos case" in the original
design, unchanged mechanically, just now driven by the confirm call's
validated selection instead of an unconditional detection result).

Unlike `NEW_PROJECT_SCRIPT`, **this script must be installed unconditionally
by `install.sh`** (not gated behind `--with-git-hosting`) — the whole point
of this feature is to give people without git hosting a way to create
projects. Its sudoers line
(`$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-upload.sh *`)
goes in the base, always-installed block of `install.sh`'s sudoers
generation (line ~186-195), not the `WITH_GIT_HOSTING` conditional block.

The script itself is unchanged from the original design, mirroring
`new-dev-instance.sh`'s structure:
```
new-project-from-upload.sh <source-dir> <name>
```
- Re-validates `<name>` against the same pattern shape as `NAME_RE` (defense
  in depth — never trust the caller, even though it's `app.py` itself,
  since this script carries a broad root grant).
- `mkdir "$PROJECTS_DIR/<name>"` **without** `-p` at the leaf level, so it
  fails atomically (not silently merges) if the directory already exists —
  closes the TOCTOU race between app.py's own duplicate check and this
  script actually running (see "Partial-failure semantics" above for how a
  multi-project confirm call now handles one such failure without rolling
  back siblings).
- Copies `<source-dir>`'s contents into it (`cp -a`, not `mv` — staging and
  `PROJECTS_DIR` may be different filesystems, and `mv` can fail
  cross-device; the caller cleans up staging separately either way, and for
  the root-plus-split-out-duplicate case, the *same* staged subtree gets
  `cp -a`'d twice, once as part of the root's copy and once standalone —
  intentional, see Non-goals).
- `chown -R "$RUN_USER:$RUN_USER"` the new directory — staged files were
  written by `SVC_USER`, ownership must flip before `RUN_USER`'s tmux
  sessions/engines can use them. Easy to miss; call it out explicitly as an
  acceptance criterion.
- If `.git` doesn't already exist at its root and `git` is installed:
  `su "$RUN_USER" -s /bin/bash -c "cd '...' && git init -q && git add -A &&
  git -c user.name='ai-dev-switchboard' -c user.email='switchboard@localhost'
  commit -q -m 'Initial import via ai-dev-switchboard upload'"` — the `-c`
  flags are scoped to just this one commit (not written to `RUN_USER`'s
  global git config), so they never clobber an identity `RUN_USER` may
  configure for themselves later.

### UI (stepper)
Replaces the original single file-input+Upload-button control entirely
with a multi-step wizard, reachable from the same place the old control
was, alongside the existing "+ New project" name-input+button row:

1. **Pick.** Two entry points into the same wizard: "Pick a folder"
   (`<input type=file webkitdirectory>` — a live local folder) or "Pick a
   `.zip`" (`<input type=file accept=".zip">` — an already-made archive).
   Picking a `.zip` directly skips straight to step 4 (upload); picking a
   folder proceeds to step 2.
2. **Exclude.** The known-heavy-directory checklist (see above), pre-
   checked per the default list, skippable/auto-advanced if nothing
   matched.
3. **Zip.** Client-side zip build with its own progress indicator (see
   "Progress tracking" above).
4. **Upload.** `POST /projects/upload` with its own progress indicator,
   carrying `?code=` if TOTP isn't cleared yet (same 428→prompt→retry flow
   the rest of the app already uses, reused as much as possible rather than
   duplicated — likely a small wrapper around the existing `performAction`/
   `handleActionResult` state machine that appends `?code=` to the URL on
   retry instead of putting it in a JSON body).
5. **Review.** Shows the phase-1 response's detected structure in plain
   language ("root has its own `.git`; found 2 nested repos inside it" /
   "no `.git` at the root; found 3 subfolders"), with the single-vs-split
   choice and (when split is available) the checklist described above,
   pre-filled with the stated defaults. Always shown, even for unambiguous
   shapes (root `.git` with nothing nested; flat loose files with no
   subdirs) — in those cases there's only one real choice, so it's a
   confirm-and-continue rather than a decision.
6. **Confirm.** `POST /projects/upload/confirm`. On success, show which
   project name(s) got registered (and, for the split-with-unselected-items
   case, how many were skipped); on failure, show the server's error
   message inline, the same way the existing `new-project-err` div does
   today.

Exact visual layout/interaction beyond this functional step sequence
(styling, transitions between steps, drag-and-drop vs. click-to-pick for
step 1, exact wording) is ux-designer's call, not specified here.

## Affected areas
- `app/app.py` — two new routes (`POST /projects/upload`,
  `POST /projects/upload/confirm`); `do_POST`'s new early raw-body branch
  for the upload route (before the shared `_read_json_body()` call);
  `detect_structure(staging_root) -> dict` (phase 1, read-only) and
  `create_projects_from_selection(staging_root, mode, selected) -> tuple[bool, str, list[str], int]`
  (phase 2: ok, error, registered names, skipped count) as the two halves
  of what was one `create_project_from_upload()` in the original design;
  zip-slip/size/naming/collision logic (largely unchanged internals, now
  split across the two phases); staging dir setup at startup; new config
  reads; extending `_reap_dead_state()` with the TTL sweep; new UI
  (stepper markup + JS state machine) and the new client-side zip writer
  (CRC-32 + local/central-directory/EOCD writer) in `PAGE_TEMPLATE`.
- `scripts/new-project-from-upload.sh` (new) — the privileged hand-off
  script, mirroring `scripts/new-dev-instance.sh`'s structure; unchanged in
  its own internal logic from the original design, just invoked from the
  confirm handler instead of a single-phase handler, and potentially
  invoked with a root+split-out-duplicate pair in the monorepo-split case.
- `install.sh` — install the new script unconditionally (not behind
  `--with-git-hosting`), add its sudoers line to the base block, set
  `NEW_PROJECT_FROM_UPLOAD_SCRIPT`/`UPLOAD_STAGING_TTL_SECONDS` in the env
  file, create `UPLOAD_STAGING_DIR`.
- `config/switchboard.env.example` — document `UPLOAD_STAGING_DIR`,
  `UPLOAD_MAX_BYTES`, `UPLOAD_STAGING_TTL_SECONDS` (new),
  `NEW_PROJECT_FROM_UPLOAD_SCRIPT`.
- `README.md` — one more bullet under "What you get" describing the upload
  path (and that, unlike "+ New project," it doesn't need
  `--with-git-hosting`), mentioning it's a folder-pick-and-zip wizard, not
  just a raw `.zip` uploader.
- `docs/ARCHITECTURE.md` — short addition to the privilege-boundary section
  noting the new script and its unconditional (not git-hosting-gated)
  sudoers entry, the staging-dir-owned-by-SVC_USER design that keeps all
  validation unprivileged, and — worth a short new note in the spirit of
  the existing "In-memory state and its one sharp edge" section — that
  upload staging is now state that deliberately outlives a single request
  (between phase 1 and phase 2), with an explicit TTL/idle-cleanup story
  rather than an always-cleanup-in-`finally` one, so a future reader
  doesn't mistake the lingering `UPLOAD_STAGING_DIR/<token>/` directories
  for a bug.

**Recommended build sequencing** (this revision grew the feature enough
that it's worth calling out explicitly, per this pipeline's load-balanced-
decomposition guidance, even though it's still documented here as one
spec): this now has two fairly independent, independently-reviewable
chunks of real complexity — (a) the **client-side zip writer + stepper UI**
(front-end-heavy, testable in isolation: build a zip with it, verify with a
real unzip tool, confirm progress indicators move), and (b) the **two-phase
backend protocol** (detect/confirm split, TTL cleanup, review-step wiring,
multi-project confirm's partial-failure handling). Recommend running these
as two ordered build cycles — part 1 (client zip-writer + stepper UI,
wired against phase 1's detect response) then part 2 (confirm endpoint +
privileged hand-off + TTL cleanup) — rather than one large developer
dispatch, even though both are described in this single `docs/spec.md`.
Flagging this explicitly for the orchestrator's sequencing decision.

## Edge cases
- Corrupt/non-zip upload → `zipfile.BadZipFile` caught, 400 "not a valid
  zip file," staging cleaned up.
- Missing/zero/oversized `Content-Length` on phase 1 → 413 before the body
  is even read.
- Compressed size under the cap but uncompressed total over it (zip-bomb
  shape) → rejected before extracting any bytes. Can't happen for a
  client-built (store-mode) zip, but still checked for the pick-a-pre-made-
  `.zip` fallback path.
- Any zip-slip-shaped entry (absolute path, `..`, symlink, NUL byte, or a
  resolved path outside the staging dir) → whole upload rejected in phase
  1, nothing extracted-and-kept.
- Empty zip (zero entries) → rejected with a clear error in phase 1.
- Two subfolders in the same confirm call sanitize to the same name, or a
  derived name collides with an existing `PROJECTS_DIR` entry → whole
  confirm call rejected up front, colliding name(s) named in the error.
- A genuine race where one specific invocation's atomic `mkdir` (no `-p`)
  fails despite passing the up-front collision check, in a multi-project
  confirm call → that one registration fails and is named in the error;
  sibling registrations already completed earlier in the same confirm call
  are not rolled back (see "Partial-failure semantics" above).
- `PROJECTS_DIR` doesn't exist yet (fresh install) → created as needed,
  same as the existing git-hosting scripts already do.
- `.git` **file** (worktree/submodule gitlink) instead of a directory → not
  recognized as "this is a repo" (documented non-goal/limitation above).
- Split mode, no-root-`.git` shape, user selects zero subfolders → rejected
  with "select at least one project," nothing registered.
- Split mode, root-has-`.git` shape, user selects zero nested paths →
  equivalent to "single" — only the root is registered, exactly as if
  "single" had been chosen (not an error, since the root is always a valid
  fallback in this shape).
- `confirm` called with a `token` that has expired (TTL exceeded and swept)
  or never existed → clear 404/410 "upload expired, start over" error,
  nothing registered.
- `confirm`'s `selected` list contains a path that doesn't match any
  currently-valid candidate from a fresh re-walk of the staging tree (stale
  or tampered client state, or a race) → rejected with a clear error rather
  than trusting the client blindly.
- Known-heavy-directory checklist matches nothing (small, clean repo) →
  step is skipped or shown empty/auto-advanced, not a blocker.
- A heavy-directory name (e.g. `node_modules`) appears at multiple depths →
  grouped into one checklist row by basename; excluding it excludes every
  match at any depth.
- `.git` is never offered as an excludable checklist candidate, under any
  circumstance — enforced in the exclusion-list logic itself, not just by
  convention.
- Total included size (after client-side exclusions) still exceeds
  `UPLOAD_MAX_BYTES` → client-side pre-flight warning before spending time
  zipping (nicety), and still hard-rejected server-side either way (checks
  1 and 2 under "Size limits" still apply, unchanged).
- Browser lacks `webkitdirectory` support → the "pick a `.zip`" fallback
  entry point (step 1b) still works unaffected; folder-live-picking degrades
  gracefully rather than breaking the whole feature.
- Loose top-level files coexisting with multiple subfolders
  (folder-of-subrepos shape) → dropped, not registered anywhere in either
  "single" (they're simply part of the effective root's tree, so *are*
  included in that case) or "split" (dropped, not registered, per original
  behavior generalized above) — worth the parenthetical since "single" vs.
  "split" treat loose files differently, unlike the original single-shot
  design where they were always dropped.
- `git` not installed on the box at all → auto-`git init` step is skipped
  silently per affected project; that project still registers, just
  without version control (mirrors the optional-dependency pattern used
  elsewhere, e.g. `DESC_LLM_BASE_URL`).
- TOTP not yet cleared this session when phase 1 (upload) is attempted →
  same 428→prompt→retry flow as every other action, code carried as
  `?code=` on retry. Phase 2 (confirm) uses the standard JSON-body `code`
  field, prompting only if TOTP somehow still isn't cleared by then.

## Acceptance criteria
- [ ] Given a picked folder, when the exclusion-checklist step is shown,
      then any subdirectory matching a known heavy-directory name is listed
      pre-checked/excluded, grouped by basename across all matching depths,
      and `.git` never appears as a candidate.
- [ ] Given the user unchecks an exclusion row before proceeding, when the
      zip is built, then files under that directory name are included in
      the archive.
- [ ] Given a folder is picked and zipping begins, when zipping is in
      progress, then a progress indicator advances monotonically, reaching
      100% only once every included file has been written into the
      archive.
- [ ] Given zipping has completed, when the upload begins, then a separate
      progress indicator advances based on actual bytes transferred (via
      `XMLHttpRequest.upload.onprogress`), independent of the zipping
      indicator.
- [ ] Given a `.zip` is picked directly (fallback entry point) instead of a
      live folder, when the wizard proceeds, then the exclusion-checklist
      and client-zip-build steps are skipped and the picked bytes are
      uploaded as-is with the same upload-progress indicator.
- [ ] Given phase 1 (`POST /projects/upload`) completes successfully, when
      its response is received, then the client is shown a review step
      describing the detected structure (root `.git` present/absent, any
      nested `.git` paths, any top-level subdirectories) and nothing has
      yet been registered under `PROJECTS_DIR`.
- [ ] Given the review step, when the user chooses "single project" and
      confirms, then exactly one new `PROJECTS_DIR` row appears for the
      (unwrapped) effective root, regardless of any nested `.git`s or
      subdirectories present.
- [ ] Given the review step for a root-`.git`-plus-nested-`.git`(s) upload,
      when the user selects one or more nested paths to split out and
      confirms, then each selected path is registered as its own
      `PROJECTS_DIR` entry (named after its own folder), the root is *also*
      registered as its own entry containing the full original tree
      unchanged (including the split-out subfolder's files), and this
      duplication is present in both registered projects' contents.
- [ ] Given the review step for a no-root-`.git` upload with multiple
      top-level subdirectories, when the user selects a subset and
      confirms, then only the selected subdirectories are registered (each
      as its own `PROJECTS_DIR` entry); unselected subdirectories and any
      loose top-level files are not registered anywhere, and the confirm
      response states how many were skipped.
- [ ] Given a no-root-`.git` review step where the user selects zero items
      in split mode, when confirming, then the request is rejected with a
      "select at least one project" error and nothing is registered.
- [ ] Given a successful phase-1 upload, when `/projects/upload/confirm` is
      called more than `UPLOAD_STAGING_TTL_SECONDS` later, then it fails
      with a clear "upload expired" error and nothing is registered.
- [ ] Given any registered project (root and/or split-out) that didn't
      already have its own `.git`, when registration completes, then it has
      a `.git` directory and one commit containing its contents.
- [ ] Given a zip-slip-shaped entry, oversized `Content-Length`, or
      uncompressed-total-over-cap zip, when phase 1 runs, then the entire
      upload is rejected before extraction, and phase 2 is never reachable
      for that upload (no token issued).
- [ ] Given a derived project name collision (existing `PROJECTS_DIR`
      entry, another name in the same confirm call, or root vs. a
      split-out subfolder's own name), when confirming, then the whole
      confirm call is rejected up front, naming the collision(s), and
      nothing is registered.
- [ ] Given the browser session hasn't cleared TOTP yet, when phase 1
      (upload) is attempted, then the server returns 428, the UI prompts
      for a code, and a correct code (sent via `?code=` on retry) completes
      staging; a wrong code returns 403 with nothing staged-and-kept.
      Confirm (phase 2) uses the standard JSON-body `code` field like every
      other endpoint.
- [ ] Given `confirm` is called with a `selected` path that doesn't match a
      currently-valid candidate from a fresh re-walk of the staging tree,
      then it is rejected with a clear error rather than trusting the
      client-submitted selection.
- [ ] Given a race where `PROJECTS_DIR/<name>` already exists at the moment
      one specific project's privileged script actually runs within a
      multi-project confirm call, then that one registration fails
      atomically and is named in the error, while any sibling
      registrations already completed earlier in the same confirm call
      remain registered (not rolled back).
- [ ] Given a non-zip or corrupt file is uploaded, when phase 1 runs, then
      a clear "not a valid zip file" error is returned and no staging
      directory is left behind.
- [ ] Given this feature is used on an install **without**
      `--with-git-hosting`, when the full wizard is used end-to-end, then
      it still works (the privileged script and its sudoers entry are
      present regardless of the git-hosting flag).
- [ ] Given a successful confirm call, when `/status` is next polled, then
      the new project(s) appear in `instances` with no separate code path
      — indistinguishable from any manually-created `PROJECTS_DIR` folder.

## Open questions
Five of the original six are now resolved (see "Revision history" below).
One new question surfaced by this revision's own scope change, kept open:

- **Partial-failure semantics for a multi-project confirm call**: defaulting
  to *up-front collision check stays all-or-nothing, but a genuine TOCTOU
  race defeating one specific project's atomic `mkdir` after sibling
  projects in the same confirm call already succeeded does not roll those
  siblings back* — reasoning: automatically deleting an already-succeeded,
  `chown`'d, `git init`'d real project directory is itself destructive, and
  a worse failure mode than a partially-successful confirm call with a
  clear per-project error. This is genuinely new (the original single-shot
  design only ever registered from a fixed detection result, never a
  user-selected multi-project set), not one of the original six items.
  Override if full transactional rollback is actually wanted here instead.
- **`UPLOAD_MAX_BYTES` default of 100 MiB**: still a guess, still
  operator-tunable via config — no strong opinion on the number itself,
  carried forward unchanged from the original spec (user confirmed "should
  be sufficient" with the exclusion-checklist mitigation now built in).

Everything else — monorepo/subrepo review-and-select (was open item 1, now
built as the primary flow), auto `git init` default (item 2, confirmed
as-is), client-side zip-and-progress transport (item 3, resolved via the
store-mode writer above), stepper UI (item 4, now the whole design), and
TOTP-via-`?code=` for phase 1 (item 6, kept as spec'd, deferred by the user
as a backend detail) — is resolved and no longer open.

## Risk / rollback notes
- Entirely additive: two new routes, a new script, a new unconditional
  sudoers line, new config with defaults. No existing route, script, or
  config value is changed or removed — `/projects/new` and its git-hosting
  flow are untouched.
- The client-side zip writer is new, non-trivial (relative to everything
  else in this file's front-end) code — worth extra review scrutiny and,
  per the recommended build sequencing above, worth building/reviewing as
  its own step: verify a zip it produces actually opens correctly in a real
  unzip tool (`unzip -t`, or Python's own `zipfile.ZipFile` round-trip) as
  part of that review, not just "the upload succeeded."
- Biggest real risk is still the privileged script
  (`new-project-from-upload.sh`) itself, since it runs as root via sudo —
  keep it as mechanical as possible (no business logic beyond
  mkdir/cp/chown/git-init, all detection/validation/selection logic stays
  in unprivileged Python), and re-validate its own inputs (name pattern,
  target-doesn't-already-exist) rather than trusting the caller.
- New risk introduced by this revision: staging state now genuinely
  outlives a single request (between phase 1 and phase 2). Mitigated by the
  TTL sweep (reusing the existing `_reap_dead_state()` opportunistic-cleanup
  precedent) and by phase 2 always re-walking the staging tree fresh rather
  than trusting any client-submitted description of it.
- Rollback is simple: remove the new sudoers line, remove/disable the two
  routes, and the feature is fully gone with zero effect on anything else —
  `PROJECTS_DIR` discovery, existing projects, and the git-hosting flow
  don't reference any of this feature's state.
- Zip-slip and size-limit checks are still the parts most worth extra
  scrutiny in review (per backlog's explicit security callout) — the
  acceptance criteria above are written to be mechanically testable (craft
  a malicious zip fixture, assert rejection and that no bytes land outside
  staging) rather than just reviewed by eye.

## Revision history
Original six Open Questions and their resolution in this revision:
1. **Monorepo handling** — overturned. Was: silent "register root only,
   ignore nested `.git`s." Now: step-by-step review/select UI, same shape
   used for both the nested-`.git` case and the folder-of-subrepos case.
2. **Auto `git init`** — confirmed as spec'd, no change.
3. **Upload transport** — overturned. Was: "no client-side zip, bring your
   own `.zip`." Now: client-side store-mode zip writer (with a pre-made-
   `.zip` fallback still available), both zipping and uploading trackable
   with independent progress bars.
4. **UI flow** — overturned (same decision as #1). Was: single-shot
   upload → immediate register-or-reject. Now: a multi-step wizard,
   review/confirm step required, not optional.
5. **Size cap** — confirmed at 100 MiB, plus a new requirement: a
   client-side, non-destructive "exclude known heavy directories from the
   zip" checklist (explicitly *not* a real `rm -rf`, since browsers can't
   touch the actual local filesystem — see "Known-heavy-directory
   exclusion" above for why this reading was chosen).
6. **TOTP-via-`?code=`** — no opinion given by the user ("i dont know
   about that"); kept as spec'd, treated as resolved-by-default rather than
   blocking, since it's a backend protocol detail, not a product-judgment
   call.
