# Implementation: Local git hosting UI + CI/CD (Gitea) — part 2b: repo creation via Gitea's API + retiring the old flow

(2a's own implementation notes — installing Gitea + the singleton toggle row
— are preserved in git history: `git show dcc582b:docs/implementation.md`.
This file now documents 2b only, per this cycle's `docs/spec.md`.)

## Summary

Rewired `create_project()` in `app/app.py` to create real repos through
Gitea's own REST API (`POST /user/repos`) instead of the legacy
bare-repo/rsync scripts, added a one-time non-interactive token-bootstrap
script (`scripts/gitea-configure-api.sh`) and a new privileged clone
hand-off (`scripts/new-project-from-gitea.sh`), and retired the six legacy
git-hosting scripts + their sudoers lines + `config/git-hosting.env.example`
from `install.sh`. Docker Compose was actually available in this session
(unlike 2a's own implementation session), so this cycle got a full live
round trip against a real Gitea 1.27.1 instance — repo creation, clone,
push, name-collision handling, and best-effort orphan-repo cleanup were all
exercised for real, not just mocked. That live testing surfaced and fixed
**two real defects the spec's own research had missed** (see "Deviations
from spec" — most importantly, the spec's assumed token scope
(`write:repository` alone) turns out to be insufficient for `POST
/user/repos` itself against real Gitea).

**Round 2 (this fix):** the reviewer's own adversarial live testing found
that round 1's fix for the second deviation (token-name uniqueness) had an
unclosed same-second collision window (`date +%s` alone has only 1-second
resolution). Fixed by appending 8 random alphanumeric characters after the
timestamp; re-verified live against a fresh real Gitea instance with the
reviewer's exact repro plus a stricter 10-run loop, 0 collisions across 20
total runs. See "Fixes from review" and "Deviations from spec" below.

## Changes by file

- **`app/app.py`**:
  - New config reads: `GITEA_API_TOKEN`, `NEW_PROJECT_FROM_GITEA_SCRIPT`
    (alongside the existing `GITEA_*` block). `NEW_PROJECT_SCRIPT` and its
    old `create_project()` body removed entirely — no fallback to the
    legacy script.
  - New `_gitea_slug(name)` — maps a `NAME_RE`-valid local name to a
    Gitea-valid repo name (spaces → hyphens, `\s+` collapse, `.strip()`),
    exactly the spec's pseudocode.
  - New `_gitea_api(method, path, body)` — a small `urllib`-based helper
    matching the exact idiom `pve_login()` already uses elsewhere in this
    file; returns `(status, parsed_json_or_{})`, never raises for a non-2xx
    status, raises `ConnectionError` only for an actual connection failure.
  - `create_project()` rewritten to: validate name → check local
    uniqueness → check `GITEA_ENABLED` → check `GITEA_API_TOKEN` → check
    `gitea_run("status") == "on"` → `POST /user/repos` (mapping 409/422 to
    a specific "already exists on Gitea" message) → hand off to
    `NEW_PROJECT_FROM_GITEA_SCRIPT` via `sudo` → best-effort `DELETE
    /repos/{owner}/{repo}` cleanup if the hand-off fails. Matches the
    spec's pseudocode almost verbatim; the one behavioral addition is
    wrapping the `_gitea_api("POST", ...)` call in a `try/except
    ConnectionError` (the spec's own pseudocode didn't show this, but its
    prose explicitly calls for converting a connection failure to a
    "Gitea isn't reachable" message — this closes that gap).
- **`scripts/gitea-configure-api.sh`** (new) — one-time root-run token
  bootstrap. Follows `taiga-configure-push.sh`'s prompt/verify shape, but
  runs as root (not `RUN_USER`) and never touches a password: mints a
  Personal Access Token via `docker exec --user git <container> gitea admin
  user generate-access-token`, writes `GITEA_API_TOKEN` into
  `switchboard.env` via the same `set_env` idiom `install.sh` uses,
  restarts `ai-dev-switchboard`, then verifies with `GET /user`.
  **Token scope is `write:repository,write:user`**, not the spec's assumed
  `write:repository` alone — see "Deviations from spec". **Token name
  includes a timestamp plus a random suffix**
  (`ai-dev-switchboard-<unix-epoch>-<8 random alnum chars>`), not the
  spec's static `ai-dev-switchboard` — also see "Deviations from spec".
- **`scripts/new-project-from-gitea.sh`** (new) — the privileged clone
  hand-off, same mechanical shape as `scripts/new-project-from-upload.sh`.
  Sources `switchboard.env` for `RUN_USER`/`PROJECTS_DIR`/`GITEA_PORT`/
  `GITEA_API_TOKEN` (token never travels via argv), re-validates
  `<name>`/`<owner>`/`<gitea-repo-name>`, atomic `mkdir` + `chown` +
  `su "$RUN_USER" -c "git clone ..."`. One addition beyond the spec's
  literal pseudocode: `git clone`'s combined output is captured and has the
  token substring redacted before being printed to stderr on failure (see
  "Deviations from spec" / "Key decisions").
- **`install.sh`**:
  - `--with-git-hosting` block: removed all `install -m 755` lines for the
    six legacy scripts, the `git-hosting-setup.sh` invocation, the
    `$GH_ENV`/`git-hosting.env` setup, and the old
    `NEW_PROJECT_SCRIPT` env write.
  - Added `install -m 755 .../new-project-from-gitea.sh
    /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh` (system-
    wide, unlike `gitea-configure-api.sh`, which stays a one-time operator
    tool run from the repo checkout, same as `taiga-configure-push.sh`).
  - Sudoers: removed the old `new-project.sh` rule; added
    `$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-new-project-from-gitea.sh *`,
    gated on `WITH_GIT_HOSTING` alongside the existing
    `gitea-{up,down,status}.sh` rules.
  - `switchboard.env` writes: added `NEW_PROJECT_FROM_GITEA_SCRIPT`;
    `GITEA_API_TOKEN` deliberately not written here (doesn't exist until
    the operator runs `gitea-configure-api.sh`).
  - Top-of-file `--with-git-hosting` flag comment and the final install
    summary block rewritten to describe the new two-step manual setup
    (admin account, then `gitea-configure-api.sh`) instead of the old
    "the `git` user, `new-repo.sh`..." wording.
- **`scripts/git-hosting-setup.sh`, `new-repo.sh`, `new-dev-instance.sh`,
  `new-project.sh`, `project-sync.sh`, `target-setup.sh`** — deleted.
- **`config/git-hosting.env.example`** — deleted.
- **`config/switchboard.env.example`** — new `NEW_PROJECT_FROM_GITEA_SCRIPT`
  line; `GITEA_API_TOKEN` documented as a commented-out placeholder (same
  pattern as `SIMPLE_PASSWORD`/`TOTP_SECRET`) pointing at
  `gitea-configure-api.sh`; Gitea section's comment updated to describe the
  now-live repo-creation flow instead of "inert infrastructure only."
- **`docs/GIT_HOSTING.md`** — full rewrite describing the new flow: how it
  fits together, the two one-time manual setup steps in order, everyday
  "+ New project" use, how an external client reaches a repo
  (`tailscale serve`-published `/gitea` vs. loopback-only), what's
  explicitly not included yet (auto-sync-on-external-push, CI/CD
  auto-deploy, multi-org), and a troubleshooting section covering every
  error message `create_project()` can return.
- **`README.md`** — "Use cases" and "What you get" git-hosting bullets
  updated to describe the new Gitea-backed flow; no more mentions of a
  restricted SSH-only `git` system user.
- **`docs/ARCHITECTURE.md`** — "Processes and privilege boundaries" section
  updated: the dangling reference to the now-deleted `new-project.sh` (used
  as a "follows the same shape as..." precedent) replaced with a direct
  description of `scripts/new-project-from-gitea.sh`'s own privilege
  boundary.
- **`scripts/new-project-from-upload.sh`** — one-line comment fix: it
  referenced `scripts/new-dev-instance.sh` (now deleted) as a "same
  discipline" precedent; repointed at `scripts/new-project-from-gitea.sh`.
- **`tests/test_gitea.py`** — extended with `GiteaSlugTests` (6 tests),
  `GiteaApiTests` (7 tests, monkeypatching `urllib.request.urlopen`
  directly, following `test_taiga_push.py`'s established convention for
  this project's HTTP-calling code), and `CreateProjectGiteaTests` (14
  tests, monkeypatching `_gitea_api`/`gitea_run`/`subprocess.run`, with a
  real temp `PROJECTS_DIR` so `instance_names()`'s filesystem check runs
  for real — matching `test_upload.py`'s own convention). Written first
  (TDD): confirmed all 27 new tests failed with `AttributeError: module
  'app' has no attribute '_gitea_slug'`/`'_gitea_api'` before touching
  `app.py`, then watched them go green once the implementation landed.
- **`tests/test_new_project_from_gitea.py`** (new) — mirrors
  `test_new_project_from_upload.py`'s structure: `ArgumentValidationTests`
  (6 tests, unprivileged) + `PrivilegedRegistrationTests` (6 tests, gated
  on passwordless sudo). The privileged tests needed something more than a
  static file server to exercise a real `git clone` — modern git no longer
  falls back to the dumb HTTP protocol on a 404 (discovered live during
  this session; see "Deviations from spec"), so the test file's own
  `_GitHttpBackendHandler` shells out to the real `git http-backend` CGI
  program for every request (the same program real git hosting, including
  Gitea, uses under the hood) rather than hand-rolling a protocol
  implementation.

## Fixes from review

**Round 1 → round 2 (this fix):** Reviewer's `docs/test-review.md` found one
must-fix defect (Defect 1) via adversarial live testing (5 back-to-back
`gitea-configure-api.sh` runs with no artificial delay, no manually-paced
gap between them): the round-1 fix for "safe to re-run" —
`TOKEN_NAME="ai-dev-switchboard-$(date +%s)"` — only has 1-second
resolution, and the script itself completes in well under a second, so
runs 3–5 of the reviewer's 5-run loop landed in the same wall-clock second
and collided (`Command error: access token name has been used already`).
The round-1 `docs/implementation.md` claim that this was "collision-proof"
and "verified live: ran the script twice in a row, both succeeded" was not
an accurate verification — that manual two-run check happened to not hit
the same-second window (see the reviewer's own analysis in
`docs/test-review.md`'s Defect 1 write-up).

**Fix**: append 8 random alphanumeric characters (same `/dev/urandom`
piped through `base64`/`tr` idiom as `install.sh`'s own `random_token()`
helper — inlined here since this script doesn't source `install.sh`) after
the timestamp: `TOKEN_NAME="ai-dev-switchboard-$(date +%s)-$(head -c 8
/dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 8)"`. This closes the
collision window regardless of how fast the script runs, rather than
depending on wall-clock second boundaries at all. See "Deviations from
spec" below for how this was actually re-verified (not just re-asserted)
this round, and "How to verify locally" for the exact repro.

Also fixed while in there (reviewer's two non-blocking doc nits, same
round): `config/switchboard.env.example`'s `GITEA_API_TOKEN` comment now
says `write:repository,write:user` (was still `write:repository` alone);
`docs/BACKLOG.md`'s Gitea entry now describes 2b as built (pending this
fix + final reviewer confirmation) instead of future tense.

## Key decisions / tradeoffs

- **Token scope live-verified against real Gitea, not left as the spec's
  assumed `write:repository`** — see "Deviations from spec" for the full
  story. This is the single most load-bearing correction from this
  session's live testing: without it, `create_project()` would 403 on
  every single call to `POST /user/repos` against a real Gitea instance,
  making the entire feature non-functional as originally speced.
- **`new-project-from-gitea.sh` captures and redacts `git clone`'s combined
  output before printing it on failure** (rather than streaming stderr
  raw, as the spec's literal pseudocode implies). Verified live that this
  git version (2.47.3) already strips embedded credentials from its own
  "unable to access"/"repository not found" failure messages, so this
  redaction is defense-in-depth for a message shape that doesn't currently
  need it in this environment — not proven load-bearing against a real
  failure here, but cheap insurance against a different git version or
  failure mode that does echo the URL. `tests/test_new_project_from_gitea.py`
  covers both: a real clone-failure scenario (proving no leak occurs with
  this git version) and a direct test of the redaction substitution itself
  (proving the mechanism works, independent of whether this environment's
  git needs it).
- **`_gitea_api`'s `ConnectionError` is caught around the `POST
  /user/repos` call specifically**, converted to a "Couldn't reach Gitea"
  message — the spec's pseudocode for `create_project()` didn't show a
  `try/except` here even though its prose calls for exactly this
  conversion; added for consistency with the `gitea_run("status")`
  pre-flight check that already handles the "Gitea unreachable" case one
  step earlier.
- **Test file for the privileged script shells out to the real `git
  http-backend` CGI program**, not a hand-rolled protocol stand-in — a
  static-file "dumb HTTP" server (which is what a first draft of this test
  file used, closer to `test_new_project_from_upload.py`'s own simplicity)
  turned out not to work against a real git client once verified live:
  modern git refuses to fall back to the dumb protocol on a 404. Using the
  real CGI program means the test still needs zero new dependencies (git
  ships `git http-backend`), stays "no real Docker/network calls" (no
  Gitea container, no Docker at all), and exercises the real smart-HTTP
  wire protocol both ends actually speak.

## Deviations from spec

Two deviations were found and fixed via this session's live testing
against a real Gitea 1.27.1 instance (Docker Compose was actually available
here, unlike 2a's own session) — both are corrections to specifics the
spec's own research got wrong, not scope changes:

- **Token scope: `write:repository,write:user`, not `write:repository`
  alone.** The spec's "Gitea's repo-creation API and token-generation CLI
  (verified, not assumed)" section states `write:repository` "is
  sufficient for both repo creation and the git-http push/pull" — this
  turned out to be wrong for the repo-*creation* half specifically.
  Live-verified against a real running Gitea 1.27.1: a token minted with
  `--scopes write:repository` gets a clean `403` from `POST /user/repos`
  itself (`"token does not have at least one of required scope(s),
  required=[write:user], token scope=write:repository"`), and separately
  from `GET /user` (`gitea-configure-api.sh`'s own verification call,
  `required=[read:user]`). Adding `write:user` to the scope list (which
  Gitea's own CLI help text uses as an example combination —
  `"write:repository,write:user"` is literally the example shown by
  `gitea admin user generate-access-token --help`) fixed both: `POST
  /user/repos` returned `201` and `GET /user` returned `200`. Re-verified
  the *other* half of the spec's claim — that `write:repository` alone
  suffices for the actual git-http clone/push — is correct: cloned and
  pushed successfully with a `write:repository`-only token against a repo
  that already existed. `write:repository,write:user` is what
  `scripts/gitea-configure-api.sh` now mints; `docs/GIT_HOSTING.md` and its
  own inline comment explain why. Still meaningfully narrower than `all`.
- **Token name includes a timestamp plus a random suffix, not the spec's
  static `"ai-dev-switchboard"`.** The spec's edge cases explicitly require
  `gitea-configure-api.sh` to be "safe to re-run" (rotation use case).
  Live-verified this was **not actually true as speced**: a second run
  with the same static `--token-name` fails with `"Command error: access
  token name has been used already"` (Gitea has no CLI to delete/rotate a
  token by name without the account's own password, which this script
  deliberately never handles). This round's history: round 1 fixed it with
  `ai-dev-switchboard-$(date +%s)` alone and claimed (wrongly — see "Fixes
  from review") that this was collision-proof; the reviewer's adversarial
  testing (5 back-to-back runs, no artificial delay) reproduced a real
  same-second collision on runs 3–5, since `date +%s` only has 1-second
  resolution and the script completes in well under a second. Round 2
  (this fix) appends 8 random alphanumeric characters after the timestamp
  (`ai-dev-switchboard-$(date +%s)-$(head -c 8 /dev/urandom | base64 | tr
  -dc 'A-Za-z0-9' | head -c 8)`, the same idiom as `install.sh`'s own
  `random_token()` helper), which closes the collision window regardless
  of timing rather than depending on it. No password reintroduced;
  `GITEA_API_TOKEN` in `switchboard.env` still gets overwritten with the
  newest token every run exactly as speced. **Verified live this round**
  (not just "ran it once/twice and it happened to work"): stood up a real
  Gitea 1.27.1 + Postgres 14 stack via Docker Compose, reproduced the
  reviewer's exact adversarial repro (5 back-to-back runs, `for i in 1 2 3
  4 5; do printf 'admin\nai-dev-switchboard-gitea\n' | sudo bash
  scripts/gitea-configure-api.sh; done`, no artificial delay) — all 5
  minted distinct tokens and completed cleanly; then ran a second, tighter
  10-in-a-row loop with the same script — 10/10 clean. Queried Gitea's own
  `GET /users/admin/tokens` afterward and confirmed all 20 token names
  minted across both loops are unique, including 4 that share the exact
  same Unix second and 5 that share a different exact same Unix second
  (i.e. the reviewer's precise failure condition — multiple runs landing
  in the same wall-clock second — was actually hit multiple times in this
  verification, and every one of those same-second collisions still
  produced a distinct name). Zero collisions across 20 total runs.

Everything else in `docs/spec.md`'s "Proposed approach" was followed
literally — exact function shapes, error-message content, sequencing, and
file-by-file diff shape all match.

## Known limitations

- **Sync-on-push is genuinely not built** (per the spec's own resolved
  default) — an agent or operator must `git pull` manually if something
  else pushes to the same Gitea repo from elsewhere. `docs/GIT_HOSTING.md`
  says this plainly.
- **No auto-cleanup of a failed-then-abandoned `PROJECTS_DIR/<name>`
  directory** (spec's Open Question 5, left as a developer's call, not
  built) — a failed clone leaves an empty directory behind; a same-named
  retry needs a manual `rmdir` first. `docs/GIT_HOSTING.md`'s
  troubleshooting section says so.
- **`tailscale serve --set-path=/gitea`'s prefix-stripping** (spec's Open
  Question 2) still hasn't been exercised against a real Tailscale node —
  no Tailscale available in this sandbox either. The loopback path (what
  `create_project()`/the privileged script actually use, always
  `127.0.0.1:$GITEA_PORT`) is unaffected either way and *was* fully
  live-verified this session.
- **No detection of a pre-2b box's stale legacy `git` system user/sudoers
  rule** on re-running `install.sh --with-git-hosting` (spec's Edge Cases,
  explicitly called a developer's-call/not-blocking item) — not built.

## What could and couldn't be verified end-to-end

Unlike 2a's own implementation session (no working Compose plugin), this
session's sandbox had real `docker` + `docker compose` (v5.4.0) available,
so the spec's Open Question 1 default ("do a full live round trip if
Compose actually works") applied. Concretely, in this session:

**Verified live, against a real running Gitea 1.27.1 + Postgres 14 stack**
(the actual `config/gitea-docker-compose.yml`, unmodified, with a `.env`
matching `install.sh`'s own derivation logic, on loopback ports
13000/13022 to avoid colliding with anything already running):

- `docker exec --user git <container> gitea admin user create ...` — real
  admin account created.
- `scripts/gitea-configure-api.sh`, run twice in a row (re-run safety) —
  both runs minted a real token, wrote it into a real
  `/etc/ai-dev-switchboard/switchboard.env`, restarted a (stub, for this
  test) `ai-dev-switchboard` systemd unit, and verified successfully
  (`GET /user` → `200`, authenticated as `admin`). This is what surfaced
  both defects described in "Deviations from spec" above — the first run
  actually failed (403) before the scope fix, and a second run failed
  (token-name collision) before the timestamp fix; both were fixed and
  re-verified live in the same session.
- `app.py`'s real `_gitea_api()`/`create_project()`/`_gitea_slug()` code
  (imported and called directly, not reimplemented for the test) against
  the real Gitea instance:
  - Real repo creation (`POST /user/repos`, `201`), real clone landing in
    a real `PROJECTS_DIR`, owned by `RUN_USER` (`dev` in this sandbox),
    with the token embedded in `origin`'s URL.
  - A real `git commit` + `git push` from that clone succeeded with zero
    additional credential prompt/setup — the acceptance criterion this
    project's own spec calls out explicitly.
  - Name-collision handling: a second local name that slugifies to the
    same already-existing Gitea repo name got the specific "already
    exists on Gitea" message, with no `PROJECTS_DIR` directory left behind
    — the two-namespace edge case docs/spec.md calls out.
  - Best-effort orphan-repo cleanup: pointed `NEW_PROJECT_FROM_GITEA_SCRIPT`
    at `/bin/false` to force a hand-off failure after a real repo had
    already been created — confirmed the repo was actually deleted from
    Gitea afterward (`GET /repos/admin/<name>` → `404`).
- `gitea_run("status")` against the real wrapper script and real running
  containers (not monkeypatched) — required working around a sandbox-
  specific quirk (Docker Compose's CLI plugin was only installed under the
  `dev` user's own `~/.docker/cli-plugins/`, not system-wide, so `sudo
  docker compose` failed for root even though the plugin itself works;
  this is an artifact of how Compose happened to be installed in this
  particular sandbox, not something `install.sh`'s own `ensure_docker()`
  — which installs via `get.docker.com`'s official convenience script —
  would produce on a real target box). Copied the plugin into
  `/usr/lib/docker/cli-plugins/` for the duration of this test, removed it
  afterward.
- All live-testing artifacts were torn down after verification: the Gitea
  Compose stack (`docker compose down -v`), the stub systemd unit, the
  real `/etc/ai-dev-switchboard/switchboard.env`, the installed
  `/usr/local/bin/ai-dev-switchboard-{gitea-status,new-project-from-gitea}.sh`
  copies, and the root Compose-plugin workaround — confirmed via a clean
  re-run of the full test suite afterward (173/173, no leftover-state
  interference).

**Round 2 (this fix, re-verifying Defect 1's fix specifically) — also live,
against a fresh real Gitea 1.27.1 + Postgres 14 stack** (same ports
13000/13022, this session's sandbox had `docker`/`docker compose` directly
usable by the non-root dev user without the root cli-plugins workaround
round 1 needed — a real admin account was created the same way as round 1):

- Reproduced the reviewer's exact `docs/test-review.md` Defect 1 repro
  verbatim: `for i in 1 2 3 4 5; do printf
  'admin\nai-dev-switchboard-gitea\n' | sudo bash
  scripts/gitea-configure-api.sh; done` — all 5 runs completed with `exit
  0` and printed "Setup verified", no collisions.
- Followed with a second, tighter loop of 10 back-to-back runs (`for i in
  $(seq 1 10); do ...; done`, checking each run's exit code and output for
  "Setup verified" programmatically rather than eyeballing it) — 10/10
  clean, 0 failures.
- Queried Gitea's own `GET /users/admin/tokens` (Basic Auth, since the
  token-list endpoint doesn't accept token auth) after both loops and
  diffed the 20 returned token names against a Python `set()` — all 20
  unique. Critically, several of those 20 share the *exact same Unix
  second* (confirmed by inspecting the timestamp portion of each name: 4
  names share one second, 5 names share a different second) — i.e. this
  verification actually hit the reviewer's precise failure condition
  (same-second runs) multiple times, not just avoided it by chance the way
  round 1's "ran it twice" claim did, and every same-second collision
  still produced a distinct name because of the random suffix.
- Teardown confirmed clean afterward: `docker compose down -v`, stub
  systemd unit removed (`systemctl daemon-reload` + `reset-failed`),
  `/etc/ai-dev-switchboard` removed, then a fresh `python3 -m unittest
  discover -s tests` run (173/173, same as before this round's live
  testing — no leftover-state interference).

**Also verified live** (`tests/test_new_project_from_gitea.py`'s own
`PrivilegedRegistrationTests`, real `sudo` + real `git clone` against a
tiny local `git http-backend`-backed server, no Docker/Gitea needed for
these): atomic `mkdir` + TOCTOU-safe collision handling, `RUN_USER`
ownership, token staying embedded in the clone's own remote, and the
token-redaction-on-failure behavior.

**Could not be verified:**
- `tailscale serve --set-path=/gitea` against a real Tailscale node (see
  "Known limitations" — no Tailscale in this sandbox; spec's own Open
  Question 2, not blocking).
- `install.sh --with-git-hosting` run completely end-to-end on a fresh box
  (new `RUN_USER`/`SVC_USER` creation, a real generated sudoers file
  validated with `visudo`, the full systemd unit as `install.sh` itself
  would author it) — not run in this session; `bash -n install.sh` (clean)
  plus close reading against the existing, already-working
  `--with-git-hosting` block it modifies is the verification performed
  here. The *pieces* `install.sh` assembles (the compose stack, the token
  bootstrap, the privileged clone script, `create_project()`'s own logic)
  were each verified live individually, as detailed above.

## How to verify locally

```bash
# Backend (fast, no Docker needed):
python3 -m unittest discover -s tests -v

# install.sh syntax:
bash -n install.sh
for f in scripts/*.sh; do bash -n "$f"; done

# On a real box with Docker + the Compose plugin available (system-wide,
# including for root -- see "Known limitations" above re: this sandbox's
# own per-user-only quirk), to reproduce this session's live round trip:
sudo ./install.sh --with-git-hosting
#   flip the "Gitea" row's toggle on in the web UI, then:
docker exec -it --user git ai-dev-switchboard-gitea gitea admin user create \
  --admin --username admin --password <password> --email <email>
sudo scripts/gitea-configure-api.sh
#   now use the web UI's "+ New project" button -- a real repo should be
#   created in Gitea and cloned into PROJECTS_DIR/<name>, ready to `git push`
#   immediately from that working copy.

# To specifically re-verify the "safe to re-run" fix (Defect 1) is closed:
for i in 1 2 3 4 5; do
    printf 'admin\nai-dev-switchboard-gitea\n' | sudo bash scripts/gitea-configure-api.sh
done
#   all 5 should print "Setup verified" with no
#   "access token name has been used already" error, even back-to-back with
#   no delay between runs.
```
