# Spec: Local backlog tracker (Taiga) — part 1b: push a spec into Taiga

## Summary
Add a small, standalone, unprivileged command-line tool
(`scripts/taiga_push_spec.py` + a one-time setup helper,
`scripts/taiga-configure-push.sh`) that pushes the content of a local
`docs/spec.md` into a running Taiga instance (installed via 1a's
`install.sh --with-taiga`) as a new userstory — one direction only
(spec → Taiga), invocable manually or by any agent with shell access
(including `product-manager`, per the global Entwicklung workflow) via a
single command, with no new web UI surface.

## Goals
- A script, runnable by any unprivileged user with a shell (in practice
  `RUN_USER`, the account real coding-agent work — and `product-manager`'s
  own Claude Code session — already runs as), that takes a local
  `docs/spec.md` and creates one new userstory in a specified, pre-existing
  Taiga project via Taiga's REST API.
- A one-time interactive setup step that collects and stores the Taiga
  credentials/target project this script needs, following this project's
  existing "plain value in a small env-style file, mode 600, owned by the
  user that reads it" pattern (`TOTP_SECRET`'s storage — see "Background"
  for why this needs a *different file* than `switchboard.env`, not the
  same one).
- Clear, actionable failure output (not a raw traceback) for the failure
  modes an operator will actually hit: Taiga unreachable, bad credentials,
  unknown project, missing/empty spec file.
- A `--dry-run` mode that does everything except the final `POST` — lets
  someone (or an agent) verify subject/description formatting and
  connectivity/auth/project-lookup without creating real backlog noise.

## Non-goals
- **Taiga → spec sync, or any two-way sync.** This is spec → Taiga only,
  per this cycle's confirmed direction. Nothing reads back from Taiga into
  `docs/spec.md`.
- **Update-in-place / de-duplication.** Every invocation creates a brand
  new userstory. Running this script twice against the same `docs/spec.md`
  creates two backlog items. Tracking "this spec already became userstory
  #N, update it instead of creating a new one" would require persisting a
  Taiga ID back into `docs/spec.md` or a side mapping file — that's real
  sync-state machinery, explicitly out of scope for a one-way v1 (flagged
  under "Open questions", not silently assumed).
- **Automating Taiga project creation.** The target Taiga project must
  already exist (created once, manually, through Taiga's own web UI, the
  same "log in and do it yourself" precedent 1a already set for first-admin
  creation). The script looks the project up by slug and fails clearly if
  it doesn't exist; it never creates one.
- **Automating Taiga user/account creation.** Same precedent as 1a's
  non-automated `createsuperuser` step: whichever Taiga user's
  username/password the operator puts in the new credentials file is their
  choice (the superuser from 1a's setup, or a separate account created
  through Taiga's own UI) — this cycle doesn't create or provision that
  account.
- **Any change to `install.sh` or 1a's already-shipped
  `--with-taiga`/toggle/`app.py` code.** This is a fully decoupled add-on
  that only needs Taiga to be *reachable* at some URL — it doesn't touch,
  read, or depend on `switchboard.env`, the toggle's on/off state, or any
  `app.py` code path. See "Background" for why.
- **Any new web UI surface.** No new row, no new `/status` field, no new
  `do_POST` branch. This is a CLI tool only — `ux-designer` has nothing to
  design here (flag this explicitly for the orchestrator: this feature has
  no user-visible UI dimension, matching `workflows/feature.md`'s
  documented skip condition for that stage).
- **Teaching the global `product-manager` agent (defined in the separate
  `D:\Entwicklung\.claude` tree) to actually call this script as part of
  its own routine workflow.** Per this cycle's brief, the wrapper lives in
  *this* repo only. This cycle delivers the mechanism and its documentation
  (so any agent *can* invoke it); wiring it into `product-manager`'s own
  standing instructions is a separate, future, cross-repo change.
- **A long-lived/refreshed/cached auth token, or Taiga's "Application
  token" mechanism.** See "Proposed approach" for why a fresh
  username+password → bearer-token exchange per invocation is the right
  fit here, not a stored token.
- **Attachments, custom fields, status/points/assignee/tags, or any other
  userstory field beyond subject + description.** A plain "spec became a
  backlog item" — everything else is something a human can set inside
  Taiga's own UI afterward.
- **Handling arbitrarily large spec files** (Taiga description length
  limits, request size limits) — no truncation/splitting logic. If a spec
  is ever large enough to hit a real limit, that will surface as a clear
  API error, not silent corruption; not engineered around preemptively.

## Background / current state
**1a shipped** (`install.sh --with-taiga`, commit `ed84d73`; full detail in
git history — `git show ed84d73:docs/spec.md`/`docs/implementation.md`):
Taiga runs as a 9-container `taiga-docker` stack, off by default, toggled
via a singleton web UI row. Relevant facts this cycle builds on, confirmed
by re-reading 1a's own `docs/implementation.md`:
- `TAIGA_ENABLED`, `TAIGA_PORT` (default `9000`), `TAIGA_DIR`, and the
  three wrapper-script paths live in `/etc/ai-dev-switchboard/switchboard.env`.
  Taiga's own URL, when on, is `http://127.0.0.1:$TAIGA_PORT` in
  `PUBLISH_MODE=none`, or `$BASE_URL/taiga` in `tailscale` mode
  (`app.py`'s `_taiga_display_url()`).
- **No automated superuser/admin creation** — still true, unchanged by
  this cycle. `install.sh --with-taiga`'s final summary only prints a
  pointer to `taiga-docker`'s own `./taiga-manage.sh createsuperuser`,
  which the operator runs manually, once, after first toggling Taiga on.
  This means: **as of 1a, there is no Taiga user account known to the
  switchboard at all** — 1b's "how does the wrapper authenticate to
  Taiga's own API" is a real sub-problem this cycle has to solve, not
  something it can assume already exists.
- **A Taiga project is not created automatically either** — Taiga's own
  onboarding has you create your first project through its web UI after
  logging in. 1b treats "a target project already exists" as a
  precondition, matching 1a's own established pattern for "one-time,
  interactive, inside Taiga's own UI, not automated."

**Where `TOTP_SECRET` actually lives, and why that matters here**
(`install.sh` lines 193-197, `config/switchboard.env.example` lines 49-55,
`app/app.py` line 59, README.md "Security notes"): a random value,
generated once if not already set, stored as a plain `KEY=value` line in
`/etc/ai-dev-switchboard/switchboard.env`. The *file*, not the individual
key, is what's locked down — `chown "$SVC_USER:$SVC_USER" "$ENV_FILE"` +
`chmod 600 "$ENV_FILE"` (`install.sh` lines 355-356) apply to the whole
file, and every secret in it (`TOTP_SECRET`, `SIMPLE_PASSWORD`, and now
Taiga's own Postgres/RabbitMQ secrets inside `taiga-docker`'s separate
`.env`) inherits that same file-level protection. `app.py` reads it via
`os.environ["TOTP_SECRET"]`, populated by systemd's
`EnvironmentFile=$ENV_FILE` — i.e. this file is readable only by the
process that runs *as* `SVC_USER`.

**Why this cycle cannot just add a `TAIGA_API_...` line to that same
file**, despite the instruction to mirror `TOTP_SECRET`'s storage pattern:
`switchboard.env` is owned by `SVC_USER` and mode `600` — readable by
`SVC_USER` (i.e. `app.py`) and root, nothing else. The principal that
needs to invoke this script is **`RUN_USER`** — that's who owns every
project directory (`PROJECTS_DIR="/home/$RUN_USER/projects"`,
`install.sh` line 147), who every engine session (Claude Code, aider,
Codex) already runs as, and who this very Claude Code session (the one
`product-manager` runs inside, per the global Entwicklung workflow) is
running as (confirmed: `whoami` in this sandbox is `dev`, the default
`RUN_USER`). `RUN_USER` cannot read a `600` file owned by `SVC_USER` — and
loosening `switchboard.env`'s permissions to let it would leak every other
secret in that file (`SIMPLE_PASSWORD`, `PVE_HOST` credentials if set,
Taiga's own Postgres/RabbitMQ secrets) to a broader audience just to expose
one new value. **This spec mirrors the *mechanism* (plain value, small
dedicated env-style file, mode 600, owned by the user that actually reads
it) but in a new file scoped to `RUN_USER`, not inside `switchboard.env`
itself** — see "Proposed approach". This is called out explicitly here,
and again under "Open questions", since it's a literal-instruction
deviation made for a concrete, checkable reason (permission-boundary
mismatch), not a judgment call to slide past silently.

**Taiga's actual REST API auth mechanism, verified today (not assumed)**
against `taigaio/taiga-doc`'s own current docs
(`api/general-notes.adoc`, `api/user-stories/endpoints.adoc`,
`api/projects/endpoints.adoc`):
- `POST /api/v1/auth` with body `{"type": "normal", "username": "...",
  "password": "..."}` returns `{"auth_token": "...", ...}`. There is
  **no simple static long-lived personal API key** in a stock self-hosted
  install — Taiga does have an "Application token" mechanism, but it
  requires registering an "Application" (via Django admin or the API) plus
  a shared encryption key between Taiga and the caller — meaningfully
  heavier setup than this feature's scope justifies for a single trusted
  local script. **This confirms the assumption to proceed under: the
  credentials file holds a Taiga *username + password* pair, not a static
  token** — the script exchanges it for a bearer `auth_token` fresh on
  every invocation (no token persisted to disk, nothing to expire/refresh
  across runs, since each invocation is a short-lived one-shot process).
- The returned token is used as `Authorization: Bearer <auth_token>` on
  subsequent calls. It does expire, but well beyond the lifetime of one
  script invocation, so no refresh logic is needed.
- `GET /api/v1/projects/by_slug?slug=<slug>` resolves a project slug to its
  numeric `id` — needed because `POST /api/v1/userstories` takes a numeric
  `project` id, not a slug.
- `POST /api/v1/userstories` with body `{"project": <id>, "subject":
  "...", "description": "..."}` creates a new userstory; `project` and
  `subject` are the only required fields (confirmed against
  `taiga-doc`'s own field list — `assigned_to`, `status`, `points`, `tags`,
  etc. are all optional and left at Taiga's defaults, per Non-goals). The
  response includes the new userstory's `id` and `ref` (its per-project
  display number, e.g. `#42`); Taiga's web URL for a userstory is
  `<taiga-url>/project/<slug>/us/<ref>`, useful for printing a direct link
  back to the operator on success.
- Exact field names were verified against `taiga-doc`'s live docs today,
  same as 1a did for `taiga-docker`'s `.env` keys — but Taiga (the live
  hosted API *and* self-hosted API surface) is an external project that
  can change field names in a future release. The developer should
  sanity-check these three calls against whatever Taiga version is
  actually running at implementation/test time rather than trusting this
  spec as gospel, matching 1a's own "Open questions" precedent for
  external-repo drift.

**The MCP-vs-script decision.** `docs/BACKLOG.md` item 1 originally framed
this as "a new MCP server or a small script wrapper." This spec's call:
**a small, dependency-free script**, callable via the Bash tool any agent
in this pipeline already has, not a standalone MCP server process. This
project's whole ethos is stdlib-only Python (`app/app.py`'s own top
comment) and "no new dependencies without strong justification" — standing
up and maintaining a long-running MCP server process (its own packaging,
its own auth to Taiga, its own lifecycle to keep alive alongside the
switchboard) is real ongoing infrastructure for a use case that's
naturally a one-shot, on-demand action ("push this spec now"), not a
persistent service. A general-purpose Taiga MCP server does already exist
upstream (`illodev/taiga-mcp`, found during this cycle's research) for
anyone who later wants a fuller two-way Taiga↔agent integration — this
spec deliberately doesn't build or adopt one, since v1's actual
requirement (one-way, one action, one script) doesn't need it. Not flagged
as a blocking open question — the reasoning above is a clean call, same
confidence level 1a had picking `taiga-docker` over hand-packaging Taiga.

## Proposed approach

### Credentials/config file: `~/.config/ai-dev-switchboard/taiga-push.env`
New, `RUN_USER`-owned, `chmod 600` file — a sibling convention to
`switchboard.env`'s `/etc/ai-dev-switchboard/` (root/`SVC_USER`-owned
config) but scoped to `RUN_USER`'s own home, the same "controls everything
under their own home directory" boundary `install.sh` already establishes
for `PROJECTS_DIR`/`CODE_SERVER_DIR` (`install.sh` line ~157's comment).
Plain `KEY=value` lines, sourced by both the bash setup helper and parsed
(not shell-evaluated) by the Python script:
```
TAIGA_URL=http://127.0.0.1:9000
TAIGA_USERNAME=taiga-bot
TAIGA_PASSWORD=...
TAIGA_PROJECT_SLUG=my-project
```
Not written by `install.sh` (Taiga's own user account doesn't exist at
`install.sh --with-taiga` time — see "Background" — so there's nothing
correct to prompt for at that point). Instead:

### `scripts/taiga-configure-push.sh` (new, bash, unprivileged, interactive)
Run once by `RUN_USER`, any time after a Taiga user + target project exist.
Follows `install.sh`'s own `prompt()` idiom (show a default, accept
override) for consistency, but is self-contained (does not source
`install.sh`):
1. Prompts for Taiga URL (default `http://127.0.0.1:9000`, matching 1a's
   own `TAIGA_PORT` default — the operator overrides this if `TAIGA_PORT`
   was customized, or if reaching Taiga through its `tailscale`-mode
   `$BASE_URL/taiga` path instead of loopback), username, password (read
   with `read -rs`, never echoed or logged), and target project slug.
2. Writes `~/.config/ai-dev-switchboard/taiga-push.env`, creating the
   parent directory (`mkdir -p`) if needed.
3. **`chmod 600` immediately after writing** — no window where the file
   with a live password in it is briefly more permissive than its final
   mode (mirrors the spirit of `install.sh`'s own ordering discipline
   elsewhere, e.g. writing secrets then immediately locking down
   `$ENV_FILE`).
4. Runs `python3 scripts/taiga_push_spec.py --verify` (see below) right
   away and reports pass/fail — catches a typo'd password or wrong project
   slug at setup time, not on the first real push.

### `scripts/taiga_push_spec.py` (new, Python 3, stdlib only — `urllib.request`, `json`, `argparse`, `os`, `configparser`-free manual `KEY=value` parsing to match the rest of this project's `.env` handling rather than pulling in `python-dotenv`)
```
usage: taiga_push_spec.py [--spec PATH] [--project SLUG] [--config PATH]
                           [--dry-run] [--verify]
```
- `--spec` (default `docs/spec.md`, resolved relative to CWD — matches how
  every other doc/`docs/*.md` reference in this pipeline is a relative
  path from the project root).
- `--project` (default: `TAIGA_PROJECT_SLUG` from the config file; an
  explicit flag overrides it per-invocation without needing a second
  config file for a second project).
- `--config` (default `~/.config/ai-dev-switchboard/taiga-push.env`).
- `--dry-run`: does everything (load config, read+parse the spec file,
  authenticate, look up the project) except the final `POST
  /api/v1/userstories` — prints the subject/description that *would* be
  sent and exits 0. Lets an agent (or a human) sanity-check formatting and
  connectivity without creating backlog noise.
- `--verify`: does auth + project lookup only (no spec file needed, no
  userstory creation) — used by `taiga-configure-push.sh`'s own step 4.

Structure — one small function per HTTP call, matching 1a's own
`taiga_run()`/`_taiga_display_url()` "one clear function per
responsibility" style and giving the test suite one seam to monkeypatch
(mirrors how `tests/test_taiga.py` monkeypatches `appmod.taiga_run` rather
than mocking `subprocess` globally):
```python
def _taiga_request(base_url, method, path, token=None, body=None):
    """One shared urllib.request wrapper: builds the request, sets
    Authorization if token is given, sends JSON, parses JSON response,
    raises TaigaPushError with a clear message on any non-2xx status or
    connection failure. This is the one function tests monkeypatch."""

def _load_config(path) -> dict: ...       # parses the KEY=value file
def _authenticate(base_url, username, password) -> str: ...  # -> auth_token
def _lookup_project(base_url, token, slug) -> int: ...        # -> project id
def _create_userstory(base_url, token, project_id, subject, description) -> dict: ...
def _build_subject_and_description(spec_text, spec_path) -> tuple[str, str]: ...
```
`_build_subject_and_description`: subject is the spec's own `# Spec: ...`
first line with that prefix stripped (falls back to the file's first
non-blank line, then to the filename itself, if the file doesn't follow
that convention — never errors out over a missing heading); description
is the full raw spec text, with a short auto-generated footer appended
(origin repo/path + UTC timestamp) for traceability, since there is no
back-link the other direction (per Non-goals, this is one-way).

`main()` wires argument parsing to these functions, catches
`TaigaPushError` at the top level and prints a single clear line to
`stderr` (no traceback) with a non-zero exit code — the specific messages
per failure mode are enumerated under "Edge cases" below, since a caller
(a human, or an agent parsing this script's exit code/stderr) needs to be
able to tell *why* it failed without reading source.

On success (non-dry-run): prints the created userstory's ref and full URL
(`<taiga_url>/project/<slug>/us/<ref>`) to stdout, exits 0.

### Documentation
- **README.md**: one new bullet under "What you get" ("push a spec into a
  Taiga backlog item via `scripts/taiga_push_spec.py`, see `docs/spec.md`"
  — or, once this ships, a short standalone note, following how other
  optional features are one-liners there with a doc pointer), one new line
  under "Repo layout" for `scripts/taiga_push_spec.py` +
  `taiga-configure-push.sh`, and one new bullet under "Security notes"
  describing the new credentials file using the *exact* phrasing pattern
  already used there for `switchboard.env`'s `AUTH_MODE=simple` password
  ("... stores its password in plain text in `~/.config/ai-dev-switchboard/taiga-push.env`
  (file mode `600`, owned by `RUN_USER`)").
- No `docs/ARCHITECTURE.md` change needed — this script doesn't cross a
  privilege boundary (no sudoers entry, no root anything, runs entirely as
  whatever unprivileged user invokes it), so "Processes and privilege
  boundaries" doesn't need a new entry; it's simply not part of that
  model, and saying so briefly in the README's Security notes bullet above
  is enough.

## Affected areas
- `scripts/taiga_push_spec.py` — new, Python 3 stdlib only.
- `scripts/taiga-configure-push.sh` — new, bash, unprivileged, interactive.
- `tests/test_taiga_push.py` — new, `unittest`, monkeypatching
  `_taiga_request` the same way `tests/test_taiga.py` monkeypatches
  `taiga_run`.
- `README.md` — three small additions (What you get / Repo layout /
  Security notes), no structural changes.
- No changes to `install.sh`, `app/app.py`, `config/switchboard.env.example`,
  or any existing sudoers/systemd/frontend code. No data model or schema
  changes. This is a single new, self-contained CLI tool — one layer, no
  load-balanced-decomposition split needed (see skill 11's own criteria:
  no schema/API/edge-function/multi-screen spread here, just one script +
  one setup helper + its tests + doc pointers).

## Edge cases
- **Taiga unreachable** (toggled off, wrong `TAIGA_URL`, box down) —
  connection error caught in `_taiga_request`, surfaced as: `"Could not
  reach Taiga at <url> — make sure it's toggled on in the ai-dev-switchboard
  web UI, or check TAIGA_URL in <config path>."`
- **Bad credentials** (`POST /api/v1/auth` returns non-2xx) — `"Taiga
  rejected the configured username/password — check TAIGA_USERNAME/
  TAIGA_PASSWORD in <config path>, or re-run
  scripts/taiga-configure-push.sh."`
- **Unknown project slug** (`by_slug` returns 404) — `"No Taiga project
  found with slug '<slug>' — create it first in Taiga's own web UI, or
  check TAIGA_PROJECT_SLUG / --project."` Never auto-creates.
- **Missing or unreadable config file** — `"No Taiga push config found at
  <path> — run scripts/taiga-configure-push.sh first."`
- **Config file present but incomplete** (e.g. blank password from a
  copy-paste mistake) — same message shape as "bad credentials", not a
  raw `KeyError`.
- **Missing or empty `docs/spec.md`** — `"No spec found at <path> (or it's
  empty) — nothing to push."`, exits non-zero, no request sent at all.
- **Config file permissions looser than 600** — checked once at the start
  of `main()` (`os.stat(...).st_mode`); if group/other-readable, print a
  loud warning (not a hard failure — an operator who deliberately loosened
  it isn't blocked, but should know) before proceeding. This is a small,
  deliberate addition beyond `switchboard.env`'s own precedent (which
  doesn't self-check its permissions at read time) — worth doing here
  specifically because this file holds a live, standalone password with no
  other secret co-located to notice a leak via, unlike `switchboard.env`
  where a permissions slip would be far more likely to be noticed/audited
  given how many other things read/depend on that file.
- **Re-running the script against the same spec** — by design, creates a
  second userstory (see Non-goals' "no update-in-place"). Not treated as
  an error; the operator is expected to know this given the documented
  behavior.
- **`--dry-run` and `--verify` together, or with missing `--spec`** —
  `--verify` doesn't need a spec file at all (auth + project lookup only);
  if both flags are passed, `--verify`'s narrower behavior wins (auth-only,
  no spec read, no dry-run "would send this" preview) — document this
  precedence in the script's own `--help` text rather than leaving it
  ambiguous.
- **Very long-running Taiga auth-token expiry mid-invocation** — not a
  real concern (see "Background": one-shot process, token used
  immediately after being issued), but worth a defensive check: if
  `_create_userstory` itself gets a 401 despite a token just having been
  issued, surface it as the same "bad credentials"-shaped error rather
  than an unhandled exception — cheap insurance against a genuinely
  short-lived token on some future Taiga version.
- **Platform**: pure Python stdlib + `urllib.request`, no platform
  branching needed (unlike `install.sh`'s Docker-install path, this has no
  OS/arch dependency at all).

## Acceptance criteria
- [ ] Given no config file exists, when
      `scripts/taiga-configure-push.sh` is run and valid Taiga URL/
      username/password/project slug are entered, then
      `~/.config/ai-dev-switchboard/taiga-push.env` is created with mode
      `600`, owned by the invoking user, and step 4's built-in
      `--verify` check reports success.
- [ ] Given a valid config file and a `docs/spec.md` starting with
      `# Spec: <title>`, when `python3 scripts/taiga_push_spec.py` is run
      with no flags, then Taiga contains exactly one new userstory in the
      configured project whose subject is `<title>` and whose description
      contains the full spec body, and the script prints that userstory's
      ref + a working URL to stdout and exits 0.
- [ ] Given the same setup, when run with `--dry-run`, then no new
      userstory is created in Taiga (verified via Taiga's own API/UI
      showing no new item), the script still prints the subject/
      description it *would* have sent, and exits 0.
- [ ] Given `--verify` is run with correct credentials and an existing
      project slug, then it exits 0 and prints a success message, having
      made no `POST /api/v1/userstories` call.
- [ ] Given Taiga is unreachable (e.g. toggled off), when the script is
      run (with or without `--dry-run`), then it exits non-zero with the
      "could not reach Taiga" message and no traceback.
- [ ] Given the configured password is wrong, when the script is run,
      then it exits non-zero with the "rejected the configured
      username/password" message, and no userstory is created.
- [ ] Given `TAIGA_PROJECT_SLUG` (or `--project`) names a project that
      doesn't exist in Taiga, then it exits non-zero with the "no project
      found" message, and no userstory is created.
- [ ] Given `docs/spec.md` doesn't exist (or exists but is empty), then
      the script exits non-zero with a clear "no spec / nothing to push"
      message before making any network call.
- [ ] Given the config file's mode is looser than `600` (e.g. `644`), then
      the script prints a loud warning but still proceeds (not a hard
      block).
- [ ] Given `--project other-slug` is passed explicitly, then it overrides
      whatever `TAIGA_PROJECT_SLUG` is in the config file for that one
      invocation, without modifying the config file itself.
- [ ] Given the script succeeds, then `~/.config/ai-dev-switchboard/taiga-push.env`
      is never logged, printed, or included in any error message verbatim
      (the password specifically must never appear in stdout/stderr, even
      in a "bad credentials" error).

## Open questions
- **Credentials file location deviates from a literal reading of "mirror
  `TOTP_SECRET`'s storage in `switchboard.env`"** — flagged prominently
  above (see "Background"). The assumption this spec proceeds under: a
  *new*, `RUN_USER`-owned file mirroring the same *mechanism* (plain
  value, small env-style file, mode 600, owned by its reader) is correct,
  since the actual consuming principal (`RUN_USER`, via any agent's Bash
  access) differs from `switchboard.env`'s owner (`SVC_USER`). This is a
  reasoned architectural call based on a concrete permission-boundary fact
  discovered during this cycle's archaeology, not a preference — flagging
  it explicitly in case there's a reason (not visible from this repo
  alone) to prefer a different location.
- **No update-in-place / no spec↔userstory ID tracking** (Non-goals) —
  assumed acceptable for v1 given the confirmed one-way-only direction;
  flag if a future cycle wants re-runs to update an existing userstory
  instead of creating duplicates (would need a persisted mapping, which
  starts to resemble sync state this cycle deliberately avoids).
- **Which Taiga user's credentials go in the config file** — deliberately
  unprescribed (any valid Taiga user with write access to the target
  project works; could be the same superuser from 1a's manual setup, or a
  dedicated "bot" account created for this purpose). Not a blocker; an
  operator preference, not an architecture decision.
- **Whether `product-manager`'s own standing instructions should
  eventually call this automatically** during its normal spec-writing flow
  — explicitly out of scope for this cycle (Non-goals), left for whoever
  next edits the global `D:\Entwicklung\.claude\agents\product-manager.md`
  definition, informed by this script now existing and working.
- **Taiga API field names/behavior** — verified against `taiga-doc`'s
  current docs today (see "Background"), but as with 1a's `taiga-docker`
  `.env` keys, this is a live external project; the developer should
  re-verify the three endpoints' exact request/response shape against
  whatever Taiga version is actually running before trusting this spec's
  field names as final.

## Risk / rollback notes
- **Blast radius is small and additive**: two new files plus a test file
  plus a few README lines; nothing existing is modified or depends on this
  running. If it's never configured (`taiga-configure-push.sh` never run),
  `taiga_push_spec.py` simply fails its first config-file check and does
  nothing else — zero effect on the rest of the switchboard.
- **No privilege boundary is touched** — no sudoers entry, no root
  anything, no change to `app.py`'s process or its trust model. Worst case
  of a bug here is a malformed/duplicate userstory in Taiga (cheap to
  delete by hand) or a leaked Taiga password if the config file's
  permissions are mishandled (mitigated by the immediate `chmod 600` in
  the setup script and the loose-permissions warning in the push script
  itself).
- **Rollback**: delete `scripts/taiga_push_spec.py`,
  `scripts/taiga-configure-push.sh`, and
  `~/.config/ai-dev-switchboard/taiga-push.env` — no other state anywhere
  in the system references any of this.
- **What could break existing functionality**: nothing — this cycle makes
  zero changes to any file 1a (or any earlier feature) shipped.
