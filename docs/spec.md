# Spec: Local git hosting UI + CI/CD (Gitea) — part 2b: repo creation via Gitea's API + retiring the old flow

## Summary
Rewire `create_project()` in `app/app.py` to create real repos through Gitea's
own REST API (installed and toggleable since 2a, commit `dcc582b`) instead of
the legacy bare-repo/rsync scripts, and retire those six legacy scripts and
their sudoers lines in this same cycle once the new flow is verified working
— **not** CI/CD auto-deploy or auto-sync-on-external-push, both explicitly
deferred to 2c (see Non-goals).

## Goals
- The web UI's "+ New project" button (`create_project()`) creates a real,
  private Gitea repository via `POST /user/repos` (Gitea's REST API), then
  clones it into `PROJECTS_DIR/<name>` — replacing today's
  `NEW_PROJECT_SCRIPT` → `new-project.sh` → `new-repo.sh` +
  `new-dev-instance.sh` chain end to end.
- A one-time, non-interactive **token-bootstrap script**
  (`scripts/gitea-configure-api.sh`), following `taiga-configure-push.sh`'s
  "one-time interactive setup → small config file" shape, but generating a
  scoped Gitea Personal Access Token via Gitea's own CLI (no admin password
  ever prompted or stored) and writing it to `switchboard.env` — the
  SVC_USER-owned config file `create_project()` itself already reads from,
  since (unlike `taiga_push_spec.py`, invoked by a human/agent as `RUN_USER`)
  the code that consumes this credential is `app.py` running as `SVC_USER`.
- A new privileged, root-run, zero-secret-in-argv registration script
  (`scripts/new-project-from-gitea.sh`) that does the one thing that
  genuinely needs root — placing a correctly-owned clone under
  `PROJECTS_DIR/<name>` as `RUN_USER` — following the exact mechanical,
  narrow shape of `scripts/new-project-from-upload.sh`.
- **Retire the six legacy scripts** (`git-hosting-setup.sh`, `new-repo.sh`,
  `new-dev-instance.sh`, `new-project.sh`, `project-sync.sh`,
  `target-setup.sh`) and their sudoers lines from `install.sh`'s
  `--with-git-hosting` block, once the new flow is verified working
  end-to-end — see "Proposed approach: sequencing" and "Open questions" for
  what "verified" means given this environment's known Docker limitation.
- Git access for the new flow rides the **existing loopback +
  `tailscale serve` path** (`PUBLISH_MODE`/`_publish()`/`_unpublish()`) that
  2a already set up for Gitea's own web UI link — no new SSH exposure, no
  new exposure primitive of any kind.
- `docs/GIT_HOSTING.md` rewritten to describe the new flow; `README.md`'s
  git-hosting mentions updated to match.

## Non-goals
- **CI/CD auto-deploy** (Gitea Actions / webhooks replacing the old
  `project-sync.sh` + `post-receive` deploy hook) — explicitly 2c.
- **Auto-sync of `PROJECTS_DIR/<name>` on a push that originates somewhere
  other than that same working copy** (e.g. someone pushes directly via
  Gitea's own web UI, or a PR gets merged there) — the old flow's
  `post-receive` → `project-sync.sh` hook gave this "for free"; the new flow
  deliberately does **not** rebuild an equivalent in 2b. See "Proposed
  approach: sync-on-push — deferred to 2c, not built here" for the reasoning
  this was a close call, and why the default resolved here is "defer."
- **Exposing Gitea's git-over-SSH port** beyond loopback. Per the user's
  confirmed default, all real git traffic (API calls and the actual
  clone/push) goes over HTTPS through the same loopback + `tailscale serve`
  mechanism already used everywhere else in this project.
- **Migration for existing `--with-git-hosting` users.** Already settled by
  2a as new-installs-only/no-auto-migration — not reopened here. Concretely:
  retiring the six scripts means *new* `install.sh --with-git-hosting` runs
  stop creating the legacy `git` system user / real-sshd exposure at all;
  an existing box that already has that `git` user, `authorized_keys`, and
  sudoers rule from a prior install keeps them untouched — no active
  teardown script for pre-2b installs.
- **Multiple Gitea orgs/owners, or per-developer Gitea accounts.** Repos are
  created under the single admin account created manually in 2a's own
  install summary step (`POST /user/repos`, not `/orgs/{org}/repos`) — see
  "Proposed approach: owner model" for why this is the resolved default, not
  an oversight.
- **A credential helper, SSH agent, or any mechanism for `RUN_USER` to use a
  *different* identity than the one bootstrap token when pushing from a
  project's working copy.** One token, reused for both the API call and the
  embedded-in-remote-URL clone credential — see "Proposed approach: token
  reuse and where it lives" for the explicit reasoning and the trust-model
  precedent this follows.
- **Changing `AUTH_MODE`/TOTP, the `/status` Gitea toggle contract, or
  anything else 2a already shipped and got reviewed** for the toggle itself
  — 2b only changes what `create_project()` does when Gitea is enabled and
  on; the singleton toggle row, `/gitea/{on,off}`, and `gitea_run()` are
  unchanged.
- **Automating the 2a admin-account-creation step itself** — still a manual,
  one-time `docker exec --user git ... gitea admin user create ...` per 2a's
  already-shipped Non-goals; 2b's own bootstrap script is a *second*,
  separate one-time step that must run after that one.

## Background / current state

### What the legacy flow actually does today (being replaced)
Traced through `scripts/new-project.sh` → `new-repo.sh` + `new-dev-instance.sh`:
1. `new-repo.sh <name>`: `git init --bare` under `$GIT_ROOT/repos/<name>.git`
   as the restricted `git` system user, with an optional auto-deploy
   `post-receive` hook block (rsync to a target machine — this is 2c's
   territory, not touched here).
2. `new-dev-instance.sh <name>`: `git clone` that bare repo into
   `PROJECTS_DIR/<name>` as `RUN_USER` (this is what makes it show up in the
   web UI — `instance_names()` just lists `PROJECTS_DIR` subdirectories),
   and appends a *second* `post-receive` hook block that runs
   `project-sync.sh <name>` (as `RUN_USER`, via a sudoers rule granted to
   the `git` user) on every push to `main` — `git fetch && git reset --hard
   origin/main` against the working copy. This is the "keeps
   `PROJECTS_DIR/<name>` synced forever" feature `docs/GIT_HOSTING.md`
   advertises.
3. All of this rides the box's **real system sshd** (port 22, public-key
   auth against `$GIT_ROOT/.ssh/authorized_keys`) — entirely outside
   `PUBLISH_MODE`/`app.py`'s control, per 2a's own spec.
4. `create_project()` (`app/app.py` line 531) today: validates the name,
   checks `NEW_PROJECT_SCRIPT` exists, and does
   `subprocess.run(["sudo", NEW_PROJECT_SCRIPT, name], ...)` — a single
   root-run call with no return payload beyond stdout/stderr.

The **direction of sync is inverted** from what 2b's new flow gives you: in
the old flow, a *remote* git client (a developer's laptop, or CI) is
expected to be the primary pusher, and `PROJECTS_DIR/<name>` is a
passively-kept-current mirror an agent works against read-mostly. In the new
Gitea-backed flow (see "Proposed approach"), `PROJECTS_DIR/<name>` **is**
the primary working copy an agent session commits in, and it pushes *to*
Gitea, not the other way round — this inversion is exactly why "keep
`PROJECTS_DIR/<name>` synced with pushes from elsewhere" is a meaningfully
different, smaller-audience feature under the new model than it was under
the old one (see the sync-on-push discussion below).

### 2a's current state (what already exists to build on)
- Gitea (`server` + `db`/Postgres) installed via `install.sh
  --with-git-hosting`, off by default, toggled on/off from the web UI
  (`GITEA_ENABLED`, `gitea_run()`, `/gitea/{on,off}`, singleton row —
  `app/app.py` ~lines 130-140, 990-ish for `/status`/`do_POST`).
  `GITEA_URL_PATH = "/gitea"`, and `GITEA__server__ROOT_URL` is already set
  to `${BASE_URL}${GITEA_URL_PATH}` at install time — i.e. Gitea already
  believes it's being served from the `/gitea` sub-path.
- `GITEA_PORT` (default `3000`, loopback-only) and `GITEA_SSH_PORT`
  (default `2222`, loopback-only, unused by anything yet) both exist;
  `_publish(GITEA_URL_PATH, GITEA_PORT)` / `_unpublish(...)` already run on
  toggle-on/off, exactly like every other per-feature URL in this project.
- No Gitea admin account exists until the operator runs the one-time
  `docker exec --user git <container> gitea admin user create ...` command
  `install.sh`'s summary prints (2a, fixed post-review to include
  `--user git` — see `docs/test-review.md`'s history at `dcc582b`). 2b's own
  bootstrap script (below) is a **second**, separate manual step that must
  run after that one.
- No repo has ever been created through Gitea. `create_project()` is
  completely untouched by 2a.

### Verifying `tailscale serve --set-path`'s prefix-stripping against Gitea's own sub-path model (researched, not assumed)
This matters because it's the load-bearing mechanism behind the user's
confirmed "HTTPS via the existing loopback + `tailscale serve` path"
decision, and 2a's own Non-goals explicitly flagged it as undecided
("`GITEA__server__SSH_PORT` intentionally left at Gitea's own default... SSH
exposure/clone-URL correctness is explicitly out of scope for 2a...
Flagged explicitly for 2b, which does need to get this right").

- **`tailscale serve --set-path=/foo <backend>` strips the `/foo` prefix
  before forwarding** — confirmed directly against Tailscale's own CLI
  reference: "the mount point is trimmed from the request URL path before
  sending it to the reverse proxy, so proxied services receive requests as
  if they were running at the root path." This is exactly the behavior the
  existing `_ttyd_start()` comment in `app.py` already documents for ttyd.
- **Gitea's own documented reverse-proxy-under-a-subpath model expects
  exactly this**: `ROOT_URL` includes the subpath (for correct link
  generation in Gitea's own UI/API responses/git output), and the proxy in
  front of it strips that same subpath before forwarding the request to
  Gitea's backend port (confirmed against `docs.gitea.com`'s reverse-proxy
  docs and Gitea's own community nginx sub-path write-ups, which need an
  explicit `rewrite`/strip rule to get this because nginx's `proxy_pass`
  doesn't do it automatically the way `tailscale serve --set-path` does).
- **Conclusion: 2a's existing `ROOT_URL` setup and `tailscale serve
  --set-path=/gitea` are already the *correct*, matching shapes for Gitea's
  own sub-path deployment model** — no contradiction, nothing to redesign.
  This means, in `PUBLISH_MODE=tailscale`, a git clone/push URL of the form
  `${BASE_URL}/gitea/<owner>/<repo>.git` is expected to work through the
  exact same published mapping the Gitea row's own link already uses today.
  This has **not** been verified against a real running Gitea +
  `tailscale serve` in this environment (no Compose plugin here — see 2a's
  own documented gap, unchanged); it's verified by close reading against
  both projects' own docs, which is the strongest verification available
  short of a live box. Flagged again under "Open questions" as something
  worth a real smoke test the first time this runs somewhere with working
  Docker + Tailscale.
- In `PUBLISH_MODE=none`, per `_publish()`'s existing fallback, the URL is
  simply `http://127.0.0.1:$GITEA_PORT/<owner>/<repo>.git` — reachable only
  from the box itself, exactly like every other loopback URL this project
  already returns in that mode.

### Gitea's repo-creation API and token-generation CLI (verified, not assumed)
Checked directly against Gitea's own API usage docs and CLI reference
(same verification discipline 1a/2a applied to their own external
dependencies), not carried over from 1b's Taiga API shape:

- **Repo creation: `POST /user/repos`** (creates the repo under the
  *authenticated* user/token's own account — not `POST /orgs/{org}/repos`,
  since there is no org in this project's model; see "Proposed approach:
  owner model"). Body (`CreateRepoOption`), all fields used here: `name`
  (string, required), `private` (bool), `auto_init` (bool),
  `default_branch` (string). A **409/422**-shaped response on a name
  collision is Gitea's own signal for "a repo with this name already exists
  under this owner" — see "Edge cases: Gitea-side name collision."
- **Auth for the API call: a Personal Access Token**, sent as
  `Authorization: token <token>` (Gitea's own header form, not Taiga's
  `Bearer`-prefixed one — verified directly, not assumed to match 1b's
  shape).
- **Token generation, CLI, non-interactive, no password ever needed**:
  `gitea admin user generate-access-token --username <admin> --token-name
  <name> --scopes <scopes> --raw` — prints *only* the raw token to stdout
  when `--raw` is given. Same `mustNotRunAsRoot()` restriction 2a's own
  fixed defect already found for `gitea admin user create` applies to every
  `gitea` CLI subcommand run inside the container, so this must also be
  invoked via `docker exec --user git <container> gitea admin user
  generate-access-token ...`, exactly like 2a's corrected admin-creation
  command. **This is a meaningfully better shape than 1b's Taiga flow**: no
  admin password is ever prompted for or stored anywhere by this project —
  the bootstrap script only ever needs `docker exec` access to the
  container (which the operator already has) to mint a token directly.
- **Scope: `write:repository`** (Gitea's scope format is
  `<read|write>:<category>`; `write` implies read for that category) is
  sufficient for both repo creation and the git-http push/pull the cloned
  working copy will do afterward — not `all`, following this project's
  general least-privilege bias for generated credentials.
- **Git-over-HTTP auth for clone/push**: Gitea accepts the token embedded
  in the URL as `http://<token>@host/...` or `http://oauth2:<token>@host/...`
  (either form authenticates); this spec uses the `oauth2:<token>@` form,
  which is the form Gitea's own docs use in examples and reads unambiguously
  as "this is a token, not a username."
- **Repo name character rules differ from this project's own `NAME_RE`**:
  Gitea repo names must match roughly `[A-Za-z0-9_.-]+` — **no spaces** —
  whereas `app.py`'s `NAME_RE` (`^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$`)
  explicitly allows spaces in the local project/folder name. This is a real
  mapping problem the old flow never had (bare-repo directory names on disk
  have no such restriction) — see "Proposed approach: local name → Gitea
  slug mapping."

## Proposed approach

### Sequencing — retire in this cycle, not deferred again (resolved default, per user sign-off)
Unlike 2a's "purely additive" sequencing, this cycle's confirmed scope is to
actually retire `git-hosting-setup.sh`, `new-repo.sh`, `new-dev-instance.sh`,
`new-project.sh`, `project-sync.sh`, `target-setup.sh` and their sudoers
lines from `install.sh`'s `--with-git-hosting` block, once the new flow is
verified working. Concretely, inside the existing
`if [ "$WITH_GIT_HOSTING" -eq 1 ]; then ... fi` block in `install.sh`:
- **Removed**: the `install -m 755 ...` lines for all six legacy scripts,
  the `git-hosting-setup.sh` invocation, the `$GH_ENV`/`git-hosting.env`
  setup, and `set_env "$ENV_FILE" NEW_PROJECT_SCRIPT ...`.
- **Removed**: `config/git-hosting.env.example` (nothing reads it once the
  six scripts are gone) and the `GH_ENV`-related lines in `install.sh`.
- **Added**: `install -m 755 .../gitea-configure-api.sh` isn't installed
  system-wide the way the wrapper scripts are (it's a one-time operator
  tool, same as `taiga-configure-push.sh`, which also isn't `install -m
  755`'d to `/usr/local/bin` — it stays in the repo checkout, run via
  `scripts/gitea-configure-api.sh` directly). `install -m 755
  .../new-project-from-gitea.sh /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh`
  **is** installed system-wide (it's `create_project()`'s own privileged
  hand-off, same as `new-project-from-upload.sh`).
- **Sudoers**: the old `new-project.sh` rule is removed; a new
  `$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh *`
  rule is added, gated on `WITH_GIT_HOSTING` alongside the existing
  `gitea-{up,down,status}.sh` rules 2a already added there.
- The final install summary block's git-hosting section is rewritten:
  drops the "the `git` user, `new-repo.sh`..." wording, and adds a pointer
  to the new one-time step: "after creating Gitea's admin account (above),
  run `scripts/gitea-configure-api.sh` once to let the web UI's '+ New
  project' button create real repos."
- **What "verified working" means given this environment's known
  limitation**: 2a's own `docs/implementation.md` documents that this
  sandbox has `docker` but not a working Compose plugin, so a real
  `docker compose up` → API call → clone round trip could not be exercised
  live in that session. This spec proceeds on the basis that "verified
  working end-to-end" means *the strongest verification actually achievable
  in whatever environment the reviewer's session has* — a full live round
  trip (toggle Gitea on for real, run the bootstrap script against a real
  container, create a project through the web UI, confirm a real clone
  lands in `PROJECTS_DIR`, confirm a commit pushes back) if Docker + Compose
  actually work there, or thorough monkeypatched-`urllib`/subprocess tests
  plus close reading against Gitea's live docs (same technique this spec's
  own research used) if they don't — honestly documented either way in
  `docs/test-review.md`, not silently downgraded. See "Open questions."

### Owner model: single admin account, no orgs (resolved default)
2a's Non-goals already settled that only one manually-created admin account
exists. 2b's repos are created under that same account via `POST
/user/repos` — no org-creation step, no per-developer Gitea accounts. This
mirrors the old flow's own single shared `git` system-user model (nobody had
their own git-hosting identity there either), so it's not a new
simplification relative to what existed — just carried forward under
Gitea's terms. Flagged explicitly as a resolved default, not an oversight,
in case a future cycle wants real per-developer identity.

### Token reuse and where it lives (resolved default, per user sign-off)
One Gitea Personal Access Token, generated once by
`scripts/gitea-configure-api.sh`, used for two purposes:
1. **The `POST /user/repos` API call itself** — held by `app.py`
   (`SVC_USER`), read from `switchboard.env` as `GITEA_API_TOKEN`.
2. **Authenticating the initial `git clone`**, and everything that working
   copy's `origin` remote does afterward (`git push`/`git pull` by whatever
   agent session runs as `RUN_USER` in that project directory) — the token
   stays embedded in the clone URL Gitea returns to the working copy's
   `.git/config`, deliberately not stripped out after cloning.

This differs from `taiga-configure-push.sh`'s storage/ownership shape for a
concrete, stated reason (per the user's explicit requirement that this not
be a copy-paste): **`taiga_push_spec.py` is invoked by a human or agent, as
`RUN_USER`, from an interactive shell** — its config file
(`~/.config/ai-dev-switchboard/taiga-push.env`, 600, `RUN_USER`-owned) lives
in the account that actually runs it. **`create_project()` is code inside
`app.py`, which runs continuously as `SVC_USER`** (the systemd service
account) — there is no `RUN_USER`-owned file `app.py` could read without
crossing a privilege/account boundary just to fetch a credential, and
`switchboard.env` (`/etc/ai-dev-switchboard/switchboard.env`, already
`SVC_USER`-owned, mode 600, already read by `app.py` at process start via
`os.environ`) is exactly the file this project already uses for every other
`SVC_USER`-consumed secret (`TOTP_SECRET`, `HOST_CONTROL_KEY`). No new
storage mechanism is introduced — `GITEA_API_TOKEN` is one more key in a
file that already exists for this exact purpose.

Persisting the same token into each project's own `.git/config` (rather
than stripping it after clone and requiring some separate ongoing
credential mechanism for `RUN_USER`) is a deliberate simplification, not an
oversight: `RUN_USER` already holds live credentials for everything else it
touches (per `app.py`'s own docstring: "the spawned coding sessions...
keep whatever access that account has for real agentic work"), the token is
scope-limited to `write:repository` (not admin/full), and `PROJECTS_DIR`
lives under `RUN_USER`'s own home directory, not readable by `SVC_USER` or
other accounts. Building a credential-helper/rotation mechanism instead
would be new complexity with no corresponding new security boundary, given
this project's existing trust model — flagged as a Non-goal, not silently
skipped.

**Passing the token to the privileged script never happens via argv** —
see "The privileged registration script" below for why, and how it's
avoided (the script reads it from `switchboard.env` itself, root can always
read that file regardless of its 600/`SVC_USER` mode).

### `scripts/gitea-configure-api.sh` (new) — one-time token bootstrap
Run once, as **root** (`sudo scripts/gitea-configure-api.sh`), after 2a's
own manual admin-account-creation step. Root, not `RUN_USER` (unlike
`taiga-configure-push.sh`) — it needs `docker exec` access to the Gitea
container and needs to write into `/etc/ai-dev-switchboard/switchboard.env`,
which is not group/other-readable.
```
== Gitea API token bootstrap ==
Run once, after Gitea's own admin account already exists (see install.sh's
own printed instructions if you haven't done that yet).

Gitea admin username [admin]: <prompt, same prompt()/prompt_secret() idiom
                                install.sh itself uses — no password prompt,
                                see below>
Gitea container name [ai-dev-switchboard-gitea]: <prompt>
```
1. Runs `docker exec --user git <container> gitea admin user
   generate-access-token --username <admin> --token-name
   ai-dev-switchboard --scopes write:repository --raw` and captures stdout
   (the raw token, nothing else) — **no password is ever asked for or
   handled by this script.**
2. Writes `GITEA_API_TOKEN=<token>` into
   `/etc/ai-dev-switchboard/switchboard.env` via the same `set_env`-shaped
   idempotent upsert idiom `install.sh` itself uses (this script is
   self-contained, does not source `install.sh`, but follows its exact
   idiom, same precedent `taiga-configure-push.sh` set for not sourcing
   `install.sh` either). File permissions are already 600/`SVC_USER`-owned
   from `install.sh`'s own earlier `chown`/`chmod` — this script must not
   loosen them (verify-and-warn if they're somehow already wrong, same
   `_check_config_permissions`-shaped defense `taiga_push_spec.py` already
   has for its own config file, adapted to warn-not-block since this file
   holds more than just this one secret).
3. Restarts the systemd service (`systemctl restart ai-dev-switchboard`) so
   the new environment variable is actually picked up — `EnvironmentFile=`
   is read once at process start, not live; skip this if Gitea's toggle
   would need to be re-flipped anyway (developer's call whether to warn or
   just do it — this is a one-time operator script, a service bounce is
   fine to make automatic, matching this project's general "safe to re-run,
   minimal manual steps" bias).
4. **Verifies** the token actually works: a `GET /user` API call against
   Gitea with the new token (same idiom as `taiga_push_spec.py --verify`),
   printing the authenticated username on success or a clear failure
   message (wrong container name, Gitea not running, `docker exec` failed,
   etc.) — mirrors `taiga-configure-push.sh`'s own verify-before-declaring-
   success discipline.

### `app/app.py` changes

New config reads (alongside the existing `GITEA_*` block, ~line 135):
```python
GITEA_API_TOKEN = os.environ.get("GITEA_API_TOKEN", "")
NEW_PROJECT_FROM_GITEA_SCRIPT = os.environ.get(
    "NEW_PROJECT_FROM_GITEA_SCRIPT",
    "/usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh")
```
`NEW_PROJECT_SCRIPT` and the old `create_project()` body are removed
entirely — no fallback to the legacy script.

**Local name → Gitea slug mapping** (new — the character-set mismatch
`NAME_RE` vs. Gitea's own repo-name rules, see "Background"):
```python
def _gitea_slug(name: str) -> str:
    # NAME_RE already guarantees name starts with an alnum and is otherwise
    # [A-Za-z0-9 _-]{0,59} -- the only translation Gitea's own
    # [A-Za-z0-9_.-]+ rules require is turning spaces into '-'.
    return re.sub(r"\s+", "-", name.strip())
```
(Kept intentionally minimal — `NAME_RE`'s own character class is already a
subset of Gitea's beyond the space issue, so nothing else needs stripping;
verify this at implementation time against Gitea's actual validation, not
just assumed — see "Open questions.")

**The Gitea API call** — a small `urllib`-based helper following the exact
idiom `pve_login()`/`_generate_description_bg()` already use elsewhere in
this file (not a new dependency, not a copy of `taiga_push_spec.py`'s
richer exception hierarchy, which is overkill for one call site with one
caller):
```python
def _gitea_api(method: str, path: str, body: dict = None) -> tuple[int, dict]:
    """Returns (status, parsed_json_or_{}). Never raises for a non-2xx
    HTTP status (the caller inspects `status`, e.g. 409 for a name
    collision) -- only for a connection failure, which callers convert to
    the same 'Gitea isn't reachable' message as the pre-flight status
    check below."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{GITEA_PORT}/api/v1{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 "Authorization": f"token {GITEA_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ConnectionError("gitea unreachable")
```
Always `127.0.0.1:$GITEA_PORT` — this call is intra-box (`app.py` and
Gitea's container both run on the same host), so it never goes through
`tailscale serve`/`BASE_URL` at all, regardless of `PUBLISH_MODE`. Only the
*clone URL* embedded for the working copy's own future pushes needs the
`PUBLISH_MODE`-aware form (see "Proposed approach" for
`_gitea_clone_url()` below) — actually, on reflection, the *clone done by
the privileged script* is also intra-box (root/`RUN_USER` cloning on the
same machine Gitea runs on), so it too always uses
`http://127.0.0.1:$GITEA_PORT/...`, never `BASE_URL`. `BASE_URL`/
`tailscale serve` only matters for an **external** git client (a developer's
own laptop) reaching in — which is a real, supported case (see
"Background"'s `tailscale serve` verification above) but isn't anything
`create_project()` itself constructs; it's simply the same published
`/gitea` mapping already visible in the Gitea row's own link. No code
change needed to support it — it falls out of 2a's existing `_publish()`
call for the Gitea toggle, plus Gitea's own web UI showing each repo's own
clone URL once you're logged into it.

**Rewritten `create_project()`**:
```python
def create_project(name: str) -> tuple[bool, str]:
    if not NAME_RE.match(name or ""):
        return False, "Use letters, numbers, spaces, - or _ (must start with a letter/number)."
    if name in instance_names():
        return False, f"'{name}' already exists."
    if not GITEA_ENABLED:
        return False, ("Gitea isn't installed on this box (install.sh --with-git-hosting) "
                       "-- or create " + f"{PROJECTS_DIR}/{name}" + " yourself (e.g. `git init`) "
                       "and it'll show up here.")
    if not GITEA_API_TOKEN:
        return False, ("Gitea API token isn't configured yet -- run "
                       "scripts/gitea-configure-api.sh once (see docs/GIT_HOSTING.md).")
    status_out = gitea_run("status").splitlines()
    if not status_out or status_out[0] != "on":
        return False, "Gitea is installed but not running -- toggle it on first."

    slug = _gitea_slug(name)
    status, resp = _gitea_api("POST", "/user/repos",
                               {"name": slug, "private": True, "auto_init": True,
                                "default_branch": "main"})
    if status in (409, 422):
        return False, f"A Gitea repository named '{slug}' already exists -- pick a different name."
    if status not in (200, 201):
        return False, f"Gitea rejected the repo creation (HTTP {status})."
    owner = resp.get("owner", {}).get("login", "")
    repo_name = resp.get("name", slug)
    if not owner:
        return False, "Gitea's response didn't include an owner -- can't continue."

    r = subprocess.run(["sudo", NEW_PROJECT_FROM_GITEA_SCRIPT, owner, repo_name, name],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        # Best-effort cleanup: the Gitea repo now exists but nothing landed
        # in PROJECTS_DIR -- don't leave an orphaned repo behind silently.
        # Failure of the cleanup itself is swallowed; the original error is
        # what the user needs to see.
        _gitea_api("DELETE", f"/repos/{owner}/{repo_name}")
        return False, (r.stderr or r.stdout or "registration script failed").strip()[:300]
    return True, ""
```
(Exact error-message wording, exception-vs-return-value plumbing for the
`ConnectionError` case, and the `timeout=30` value are developer's call,
consistent with existing style; the pseudocode above fixes the *shape and
order of operations*, not every literal string.)

### The privileged registration script (`scripts/new-project-from-gitea.sh`, new)
Same mechanical, narrow shape as `scripts/new-project-from-upload.sh` — no
new pattern introduced:
```
Usage: new-project-from-gitea.sh <owner> <gitea-repo-name> <name>
```
1. Sources `/etc/ai-dev-switchboard/switchboard.env` for `RUN_USER`,
   `PROJECTS_DIR`, `GITEA_PORT`, `GITEA_API_TOKEN` — **this is why the token
   never needs to travel via argv/`ps`-visible command line**: the script
   runs as root (via sudoers), and root can always read a 600 file
   regardless of its owning user, exactly the same sourcing pattern
   `new-project-from-upload.sh` already uses for `RUN_USER`/`PROJECTS_DIR`
   from this same file.
2. Re-validates `<name>` against the same `NAME_RE`-shaped regex
   `new-project-from-upload.sh` already re-validates with (defense in
   depth — never trust the caller, even though `app.py` already checked).
   Also validates `<owner>`/`<gitea-repo-name>` are non-empty and contain
   only `[A-Za-z0-9_.-]` characters (cheap sanity check against a
   compromised/buggy caller constructing a malicious clone target — this
   script is about to run `git clone` as root-elevated-to-`RUN_USER`
   against a URL it builds from these two arguments).
3. `DEST="${PROJECTS_DIR}/${NAME}"`; atomic `mkdir "$DEST"` (fails loudly,
   not silently-merges, if it already exists — same TOCTOU-closing idiom as
   `new-project-from-upload.sh`), then `chown "$RUN_USER:$RUN_USER" "$DEST"`
   immediately (so the clone below, run as `RUN_USER`, can actually write
   into it).
4. `CLONE_URL="http://oauth2:${GITEA_API_TOKEN}@127.0.0.1:${GITEA_PORT}/${OWNER}/${REPO}.git"`
   (built here, never passed in — see above), then
   `su "$RUN_USER" -s /bin/bash -c "git clone '$CLONE_URL' '$DEST'"` —
   clones directly as `RUN_USER` into the now-`RUN_USER`-owned empty `DEST`
   (no separate `cp -a` + recursive `chown` pass needed the way
   `new-project-from-upload.sh` needs one, since `su` already writes as the
   right user from the start).
5. Prints `Ready: $DEST — will show up in the web UI now.` (same closing
   line as both existing privileged scripts, for consistency).

### Sync-on-push — deferred to 2c, not built here (resolved default; flagged as a real close call)
This is the one part of this cycle's brief explicitly flagged as needing a
reasoned call, not a default assumption. Considered both ways:
- **Argument for building it now**: `docs/GIT_HOSTING.md` advertises
  "keeps the working copy synced on every future push, forever" as a
  feature of `--with-git-hosting` today, and losing it silently would be a
  regression for anyone relying on it.
- **Argument for deferring (the resolved default)**: the old flow's version
  of this feature exists because the *old* model expects the primary pusher
  to be someone/something **other than** `PROJECTS_DIR/<name>` itself (see
  "Background" — a developer's laptop, or CI, pushing in). Under the new
  Gitea-backed model, `PROJECTS_DIR/<name>` **is** the primary working copy
  an agent session commits and pushes from directly — the scenario the old
  hook solved (an external push needs to be reflected locally) is now the
  *minority* case (someone/something pushes to the same repo from
  somewhere that isn't this working copy — another contributor via Gitea's
  own web UI, a merged PR, a second agent session elsewhere), not the
  common one. Building that specifically requires **new inbound surface
  area** this project doesn't have yet: a webhook receiver endpoint in
  `app.py` (Gitea's own webhook system, not a `post-receive` hook this
  project controls directly the way the old bare-repo flow did), webhook
  secret verification, and a decision about how to react to a webhook
  concurrently with an agent possibly mid-edit in that same working copy
  (a `git reset --hard` racing a live coding session is a materially
  different risk than the old flow's read-mostly mirror). That's
  meaningfully more architectural surface than "one more root-run wrapper
  script," and it's the *same kind* of webhook infrastructure 2c's own
  CI/CD auto-deploy needs to build anyway (2c already has to solve "Gitea
  push happened, now do something automatically"). Building a one-off,
  narrower version of that infrastructure in 2b just for this one hook,
  ahead of 2c designing the general mechanism, risks two incompatible
  webhook-handling code paths.

**Resolved default: defer. 2b ships without any auto-sync-on-external-push
mechanism.** An agent or operator can always `git pull` manually in the
working copy in the meantime. `docs/GIT_HOSTING.md`'s rewrite (see
"Affected areas") must say this plainly rather than silently dropping the
old claim. Flagged here explicitly per this cycle's brief, and again under
"Open questions," in case the close call should go the other way.

### `install.sh` / config docs — see "Sequencing" above for the concrete diff shape.
`docs/GIT_HOSTING.md` gets a full rewrite (not a patch) describing: the new
flow (create via web UI → Gitea API creates the repo → privileged script
clones it into `PROJECTS_DIR`), the two one-time manual steps in order
(2a's admin-account creation, then 2b's `gitea-configure-api.sh`), how an
external git client reaches a repo (`tailscale serve`-published `/gitea`
path, or loopback-only in `PUBLISH_MODE=none`), and an explicit callout that
auto-sync-on-external-push is not (yet) a feature of the new flow (see
above). `README.md`'s existing git-hosting mentions (the `git` user, "push a
new project" framing) get updated to match — no more mentions of a
restricted SSH-only `git` system user for new installs.

## Affected areas
- `app/app.py` — `create_project()` rewritten; new `GITEA_API_TOKEN`/
  `NEW_PROJECT_FROM_GITEA_SCRIPT` config reads; new `_gitea_slug()`,
  `_gitea_api()` helpers. `NEW_PROJECT_SCRIPT` and its old code path
  removed. No frontend/JS changes — the "+ New project" UI is unchanged
  (same name input, same button, same `POST /projects` call shape).
- `scripts/gitea-configure-api.sh` (new) — one-time token bootstrap.
- `scripts/new-project-from-gitea.sh` (new) — privileged registration
  script.
- `scripts/git-hosting-setup.sh`, `scripts/new-repo.sh`,
  `scripts/new-dev-instance.sh`, `scripts/new-project.sh`,
  `scripts/project-sync.sh`, `scripts/target-setup.sh` — **deleted**, once
  the new flow is verified (see "Sequencing").
- `config/git-hosting.env.example` — **deleted** (nothing reads it once the
  six scripts above are gone).
- `install.sh` — `--with-git-hosting` block: legacy script installs +
  `git-hosting-setup.sh` call + `git-hosting.env` setup + old sudoers rule
  removed; new script install + sudoers rule + updated summary text added.
- `config/switchboard.env.example` — new `NEW_PROJECT_FROM_GITEA_SCRIPT`
  line; `GITEA_API_TOKEN` documented as a comment only (like other secrets
  this file documents but never ships a real value for) with a pointer to
  `gitea-configure-api.sh`; old `NEW_PROJECT_SCRIPT` references removed.
- `docs/GIT_HOSTING.md` — full rewrite (see above).
- `README.md` — mentions updated (see above).
- `tests/test_gitea.py` — extended with tests for `_gitea_slug()`,
  `_gitea_api()`, and the new `create_project()` body (mocking
  `_gitea_api`/`gitea_run`/`subprocess.run`, following the same
  monkeypatch-the-seam convention `test_taiga.py`/`test_gitea.py` already
  use — not a real Docker/network call).
- A new `tests/test_new_project_from_gitea.py` (or extending
  `tests/test_new_project_from_upload.py`'s structure) covering the
  privileged script's own argument validation and the mechanical
  mkdir/chown/clone sequence, mirroring
  `PrivilegedRegistrationTests`/`ArgumentValidationTests`'s shape in the
  existing upload-script test file.
- No data model / schema changes. One existing endpoint's *behavior*
  changes (`POST /projects` → `create_project()`), not its request/response
  shape.

## Edge cases
- **Gitea installed but not currently toggled on** — `create_project()`
  must check `gitea_run("status")` before attempting the API call and
  return a clear "toggle it on first" message, not a raw connection
  failure (see the rewritten `create_project()` above).
- **Bootstrap script never run (`GITEA_API_TOKEN` empty)** — clear,
  specific error message pointing at `gitea-configure-api.sh`, distinct
  from "Gitea isn't installed at all."
- **Gitea-side name collision that local-uniqueness didn't catch** — a
  local name whose derived slug (`_gitea_slug()`) collides with an
  *existing* Gitea repo even though no `PROJECTS_DIR` folder of that exact
  name exists yet (e.g. two different local names both slugify to the same
  string, or a repo was created directly through Gitea's own web UI
  bypassing this project entirely) — handled via Gitea's own 409/422
  response, not a separate pre-check (avoids a second TOCTOU race).
- **Partial failure: Gitea repo created, but the privileged clone script
  fails** (root out of disk, `PROJECTS_DIR` permissions wrong, `RUN_USER`
  doesn't exist, network hiccup mid-clone) — best-effort `DELETE
  /repos/{owner}/{repo}` cleanup so a failed "+ New project" click doesn't
  leave an orphaned repo behind; the cleanup's own failure is swallowed
  (logged, not surfaced) since the original error is what matters to the
  user.
- **Partial failure: clone script's `mkdir "$DEST"` succeeds but the
  `git clone` itself fails** (e.g. Gitea became unreachable between the API
  call and the clone) — `DEST` is left behind empty rather than auto-
  removed; `create_project()`'s own Gitea-repo cleanup above still fires.
  An empty leftover directory under `PROJECTS_DIR` blocks a same-named
  retry (the existing `name in instance_names()` check would now say
  "already exists" for a `create_project()` call that actually failed) —
  worth a one-line note in `docs/GIT_HOSTING.md`'s troubleshooting section
  that a failed "+ New project" attempt may need a manual `rmdir` before
  retrying with the same name; not worth adding retry/cleanup logic to the
  script itself for what should be a rare failure mode.
- **`instance_names()` local-uniqueness check vs. Gitea-repo uniqueness are
  two different namespaces** — see the collision case above; this is a
  structural property of the new flow (the old flow had exactly one
  namespace, the bare-repo directory name, that both checks implicitly
  shared) worth calling out explicitly since it's new.
- **Local name containing characters `NAME_RE` allows but that need mapping
  for Gitea** (spaces, specifically) — handled by `_gitea_slug()`; verify at
  implementation time that the mapping's output can never itself violate
  Gitea's rules (e.g. two adjacent spaces collapsing correctly, a name that
  is *only* whitespace-separated characters after `NAME_RE`'s own leading-
  alnum requirement — should be structurally impossible given `NAME_RE`,
  but worth an explicit test).
- **`gitea-configure-api.sh` run before Gitea is toggled on, or before the
  admin account exists, or against the wrong container name** — each must
  fail with a specific, actionable message (`docker exec` itself failing
  with "no such container," Gitea's own CLI failing because no admin user
  exists yet, etc.) — same discipline `taiga-configure-push.sh`/
  `taiga_push_spec.py --verify` already apply to their own failure modes.
- **`gitea-configure-api.sh` run a second time** — must be safe to re-run
  (generates a *new* token, overwrites `GITEA_API_TOKEN` via the same
  idempotent-upsert idiom, restarts the service again) — useful if an
  operator wants to rotate the token; not a special "already configured"
  refusal.
- **Re-running `install.sh --with-git-hosting` on a box that still has the
  legacy `git` system user / sudoers rule from before 2b** (an existing
  install upgrading in place) — per the Non-goals, nothing actively tears
  that down; the new run simply stops re-asserting the old scripts/sudoers
  lines it used to (they're gone from this codebase's `install.sh` now), so
  they go stale but aren't forcibly removed. Worth a one-line note in the
  install summary if the legacy `git-hosting-setup.sh`-created state is
  detected (developer's call whether this is worth the extra detection
  logic for this cycle, or a documentation-only note — not blocking).
- **`PUBLISH_MODE=none`** — the clone URL an external client would use is
  `http://127.0.0.1:$GITEA_PORT/...`, reachable only from the box itself,
  same honesty-about-reachability precedent `_publish()` already documents
  for every other feature in this mode.

## Acceptance criteria
- [ ] Given Gitea is installed, toggled on, and `gitea-configure-api.sh` has
  been run successfully, when the web UI's "+ New project" button is used
  with a valid name, then a new private Gitea repository is created via the
  API (`auto_init: true`, `default_branch: "main"`), and
  `PROJECTS_DIR/<name>` is populated with a working clone of it, owned by
  `RUN_USER`, showing up in the web UI immediately.
- [ ] Given that same project, when a commit is made and `git push` is run
  from `PROJECTS_DIR/<name>` (as `RUN_USER`), then it succeeds without any
  additional credential prompt or setup, authenticating via the token
  already embedded in that clone's `origin` remote.
- [ ] Given Gitea is not installed on this box, when "+ New project" is
  used, then `create_project()` returns a clear message saying so (not a
  crash, not a reference to the old `NEW_PROJECT_SCRIPT`).
- [ ] Given Gitea is installed but currently toggled off, when "+ New
  project" is used, then it returns a clear "toggle it on first" message,
  no Gitea API call is attempted.
- [ ] Given Gitea is installed and on, but `gitea-configure-api.sh` has
  never been run, when "+ New project" is used, then it returns a clear
  message pointing at that script, no Gitea API call is attempted.
- [ ] Given a name whose derived Gitea slug already exists as a repo (even
  if the local `PROJECTS_DIR` name is available), when "+ New project" is
  used, then it fails with a specific "already exists on Gitea" message,
  and no `PROJECTS_DIR` directory is left behind.
- [ ] Given the Gitea API call succeeds but the privileged clone script
  fails for any reason, when that happens, then the just-created Gitea repo
  is deleted (best-effort) and the original failure is returned to the
  caller — no orphaned Gitea repo, no silently-swallowed top-level error.
- [ ] Given `scripts/gitea-configure-api.sh` is run once, when it completes
  successfully, then `switchboard.env` contains a `GITEA_API_TOKEN` line,
  the service has been restarted, and a `GET /user` call against Gitea with
  that token succeeds (the script's own `--verify`-shaped check).
- [ ] Given `install.sh --with-git-hosting` is run fresh (after this
  cycle's changes), when it completes, then none of the six legacy scripts,
  `config/git-hosting.env`, or the legacy `git` system user are created —
  only Gitea (per 2a) plus `new-project-from-gitea.sh` and its sudoers rule
  are installed.
- [ ] Given the full test suite (`python3 -m unittest discover -s tests`),
  when it runs after this cycle's changes, then all tests pass, including
  new coverage for `_gitea_slug()`, `_gitea_api()`, the rewritten
  `create_project()`, and the new privileged script's argument validation —
  with no real Docker/network calls made by any test (same monkeypatched-
  seam convention as `test_taiga.py`/`test_gitea.py`).

## Open questions
1. **What "verified working end-to-end" actually means for this session's
   environment.** If the build/review cycle for 2b runs in an environment
   with a real working Docker Compose plugin (unlike 2a's own session),
   the reviewer should do a full live round trip before the six legacy
   scripts are deleted, per the user's explicit requirement. If it doesn't
   (same documented gap as 2a), retirement should still proceed based on
   thorough mocked-test coverage plus close reading against Gitea's live
   docs — this spec's own default — but `docs/test-review.md` must say so
   plainly, the same way 2a's own implementation notes did, rather than
   silently claiming a live verification that didn't happen. Flagging this
   explicitly rather than assuming the review environment will differ from
   2a's.
2. **`tailscale serve --set-path=/gitea`'s prefix-stripping against Gitea's
   own sub-path expectations** — researched and believed correct (see
   "Background"), but never exercised against a real running Gitea +
   Tailscale in this project's own environment. Worth an explicit manual
   smoke test (clone through the published `/gitea` URL from an actual
   external client) the first time this runs somewhere that has both
   Docker and a real Tailscale node available — not blocking 2b's ship,
   since the loopback-only path (`PUBLISH_MODE=none`, and `app.py`'s own
   `create_project()`/clone script, which never route through
   `tailscale serve` at all) is unaffected either way.
3. **`_gitea_slug()`'s mapping is intentionally minimal** (spaces → `-`
   only) on the assumption `NAME_RE`'s character class is otherwise a
   subset of Gitea's. Worth a direct check against Gitea's actual repo-name
   validation regex in its source (not just its docs) at implementation
   time, in case there's a narrower restriction this spec's research
   missed (e.g. reserved names, a max length shorter than `NAME_RE`'s 60).
4. **Sync-on-push deferral (see "Proposed approach")** — flagged again here
   since the brief explicitly called this a possible close call. If the
   answer should be "no, build a narrow version now," the cheapest version
   to reconsider would be: Gitea's own `post-receive`-equivalent (a
   built-in "Mirror" push, or a minimal webhook receiver limited to exactly
   this one project's repo) rather than waiting for 2c's general mechanism
   — flagged as the fallback shape if this default is overridden.
5. **Whether a failed-then-abandoned `PROJECTS_DIR/<name>` directory (see
   "Edge cases") should be auto-cleaned by the privileged script itself**
   rather than left for manual `rmdir`. Left as a developer's call / small
   follow-up rather than specified here, since it's a narrow robustness
   improvement, not a correctness requirement.

## Risk / rollback notes
- The riskiest new piece is the privilege boundary: `GITEA_API_TOKEN` now
  flows through two places that didn't need any secret before
  (`switchboard.env` already handled secrets; the new privileged script now
  also reads one). Mitigated by: never accepting the token via argv, always
  reading it from the same already-600/`SVC_USER`-owned file `install.sh`
  already protects, and scoping the token itself to `write:repository`
  (not `all`) at generation time.
- Retiring the six legacy scripts is the one irreversible-feeling part of
  this cycle for *new* installs (existing installs are untouched per
  Non-goals) — rollback if something's wrong post-ship: `git revert` the
  commit that removed them from `install.sh`/deleted the script files (all
  preserved in git history), no data loss, since nothing about `RUN_USER`'s
  own project files is touched by this cycle either way.
- If `gitea-configure-api.sh` or the API-based `create_project()` has a
  bug post-ship, the fallback for an operator in the meantime is exactly
  what `create_project()`'s own error messages already point at: create
  `PROJECTS_DIR/<name>` by hand (`git init` or a manual clone) — same
  fallback the pre-2b code already offered when `NEW_PROJECT_SCRIPT` wasn't
  installed, preserved in spirit even though the exact old wording changes.
- Best-effort Gitea-repo cleanup on partial failure (see "Edge cases") is
  explicitly best-effort, not guaranteed — a truly wedged Gitea (e.g.
  network partition right after the create call) could still leave an
  orphaned repo; acceptable given this is a low-frequency, operator-visible
  failure mode (the repo is visible in Gitea's own UI and can be deleted by
  hand), not a security or data-loss issue.
