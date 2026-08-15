# Implementation: install.sh fixes from Proxmox E2E test round 1 (items 22-27)

## Summary
Applied all six precisely-diagnosed fixes from `docs/spec.md` to `install.sh`
(fix 4 also touches `scripts/gitea-sync-project.sh`): a missing `cp` for
`app/taiga_board.py` that crash-loops every fresh install, a `-it` flag in a
printed non-interactive docker command, a missing `chown` of `$STATE_DIR`
itself, a world-readable `runtime.env` sibling file so `gitea-sync-project.sh`
(run as `RUN_USER`) can read `RUN_USER`/`PROJECTS_DIR` without needing access
to the 600-mode secrets file, a `chown` of the top-level `~RUN_USER/.local`
instead of just the code-server subtree, and a `git config --global
safe.directory '*'` for `SVC_USER` so its read-only git inspection calls
against `RUN_USER`-owned project directories don't get blocked by git's
"dubious ownership" protection.

## Root cause
Each of the six bugs stems from the same class of gap: the existing test
suite mocks `useradd`/`chown`/multi-user boundaries rather than exercising
them for real, so none of these could have been caught without a real
Proxmox install. Specifics:
- **Item 22**: `app/taiga_board.py` was added to the app but never added to
  the "-- App + engines --" `cp` block in `install.sh`, so the installed
  `/opt/ai-dev-switchboard/` tree is missing a module `app.py` imports —
  `ai-dev-switchboard.service` fails immediately with `ModuleNotFoundError`.
- **Item 23**: the printed Gitea admin-bootstrap `docker exec` command
  carried `-it` even though every value is passed as a flag (nothing about
  the command is interactive) — `-it` requires an attached TTY and fails
  when copy-pasted into a non-interactive shell (e.g. `pct exec`).
- **Item 24**: `install.sh` created `$STATE_DIR` but only ever chowned its
  later-created `uploads/` subdirectory, never `$STATE_DIR` itself, so
  `SVC_USER` couldn't write directly under it (e.g. `GITEA_REPO_MAP_FILE`).
- **Item 25**: `gitea-sync-project.sh` runs as `RUN_USER` (via `sudo -u`)
  but sourced `/etc/ai-dev-switchboard/switchboard.env`, which is
  `600`/`SVC_USER`-owned — `RUN_USER` can't read it, `source` fails under
  `set -euo pipefail`, and the script exits 1 silently every poll interval.
- **Item 26**: the code-server chown only covered
  `/home/$RUN_USER/.local/share/code-server`, two levels below `.local`
  itself; `.local` and `.local/share` were created by `mkdir -p` as
  `root:root` and never reassigned, so anything else writing under
  `~/.local` (e.g. `pipx`/`pip install --user`) hits `PermissionError`.
- **Item 27**: `_check_git_repo_state()` (`app/teams.py`) runs
  `git -C workdir rev-parse --is-inside-work-tree` as `SVC_USER` against
  `RUN_USER`-owned project directories. Git ≥2.35.2's "dubious ownership"
  protection (CVE-2022-24765 mitigation) refuses to operate across that
  user boundary unless the path is in the caller's `safe.directory` list,
  which `install.sh` never configured — every `team/start` call failed with
  a flatly wrong `"not a git repository"` error.

## Changes by file
- `install.sh`:
  - Added `cp "$REPO_DIR/app/taiga_board.py" "$INSTALL_DIR/taiga_board.py"`
    to the "-- App + engines --" step (fix 1 / item 22).
  - Removed `-it` from the printed `docker exec` admin-bootstrap command in
    the `--with-git-hosting` summary block (fix 2 / item 23).
  - Added `chown "$SVC_USER:$SVC_USER" "$STATE_DIR"` immediately after the
    `SVC_USER` `useradd` line (fix 3 / item 24).
  - Added a `RUNTIME_ENV_FILE="$CONFIG_DIR/runtime.env"` write (mode 644,
    containing only `RUN_USER`/`PROJECTS_DIR`) right after `$ENV_FILE`'s
    own `chown`/`chmod 600` (fix 4 / item 25).
  - Changed the code-server chown target from `$CODE_SERVER_DIR` to
    `/home/$RUN_USER/.local` (fix 5 / item 26).
  - Added `sudo -u "$SVC_USER" git config --global --add safe.directory
    '*'` right after the `SVC_USER` `useradd` line, alongside fix 3 (fix 6
    / item 27).
- `scripts/gitea-sync-project.sh`: changed `CONFIG` from
  `/etc/ai-dev-switchboard/switchboard.env` to
  `/etc/ai-dev-switchboard/runtime.env` (fix 4 / item 25) — same
  fallback-default shape retained, so a box that hasn't re-run `install.sh`
  yet still degrades to the existing `dev`/`/home/dev/projects` defaults
  rather than a hard failure.

All six fixes match the spec's exact "Proposed approach" code verbatim;
live line numbers had shifted only slightly from the spec's references
(off by a handful of lines), confirmed by reading the live file before
editing — no other drift found.

## Key decisions / tradeoffs
- Fixes 3 and 6 both had to land after the `SVC_USER` `useradd` line
  (since `$SVC_USER` doesn't exist until then) — implemented them
  back-to-back at that single insertion point, per the spec's own
  instruction that either order is fine as long as both come after
  `useradd`.
- Fix 6's `safe.directory '*'` is a real, if bounded, security-relevant
  change (flagged as such by the spec itself for extra review scrutiny):
  `SVC_USER` only ever runs read-only git inspection commands directly;
  all git writes already cross into `RUN_USER` via `sudo -u`, so this
  doesn't hand out any privilege the account didn't already effectively
  have. `*` (not a path glob) is required because git's `safe.directory`
  only matches literal paths or the literal string `*`, and projects are
  created dynamically after install so a fixed literal-path list can't
  work here.
- Fix 4 deliberately keeps `switchboard.env` at 600 and introduces a
  separate, narrow, world-readable `runtime.env` holding only two
  non-secret values, rather than loosening the secrets file itself (which
  would leak `GITEA_API_TOKEN`/`SIMPLE_PASSWORD`/`TOTP_SECRET` to every
  local account, including `RUN_USER`'s own coding-agent sessions).

## Deviations from spec
None. All six fixes were applied exactly as specified in
`docs/spec.md`'s "Proposed approach"/code-block sections, at the (slightly
shifted but easily located) equivalent points in the live file.

## Known limitations
- As called out in the task brief and the spec itself: no existing test
  suite can exercise a real fresh `install.sh` run against real
  `useradd`/`chown`/multi-user boundaries — that is exactly the class of
  bug this whole round exists because the mocked test suite couldn't catch
  it. Verification for this cycle is therefore necessarily indirect (see
  below); a second real Proxmox E2E pass, not available in this
  environment, is the only way to fully close the loop on all six
  acceptance criteria as literally written in the spec (e.g. "`sudo -u
  switchboard-svc touch /var/lib/ai-dev-switchboard/testwrite` succeeds
  after a fresh install").
- No existing `tests/test_install_*.py` test asserted on the specific
  `cp`/`chown` lines being changed (confirmed by inspection — see
  "How to verify locally" below), so none needed updating, and none was
  added: the spec/task brief and this project's own precedent
  (`tests/test_install_set_env.py`) scope new install.sh test
  infrastructure to what already exists, and building a real
  useradd/chown harness was explicitly out of scope for this cycle.

## How to verify locally
1. **Syntax**: `bash -n install.sh && bash -n scripts/gitea-sync-project.sh`
   — both clean.
2. **Lint**: `shellcheck install.sh` and `shellcheck scripts/gitea-sync-project.sh`
   — only pre-existing warnings remain (`install.sh:70` SC2015, an
   unrelated `A && B || C` note; `install.sh:601` SC2001, an unrelated
   `sed` style note; `gitea-sync-project.sh:38` SC1090, inherent to
   `source "$CONFIG"` with a non-constant/config-derived path, present
   before this change too) — nothing flagged in any line touched by this
   cycle.
3. **Diff review**: `git diff -- install.sh scripts/gitea-sync-project.sh`
   confirms all six changes land exactly as specified.
4. **Existing test assertions on changed lines**: confirmed no
   `tests/test_install_*.py` test asserts the specific `cp`/`chown` lines
   changed here (`grep -n "taiga_board\|teams.py\|app.py\|chown\|STATE_DIR\|CODE_SERVER_DIR\|safe.directory\|switchboard.env\|runtime.env" tests/test_install_*.py`).
   Confirmed `tests/test_install_update.py`'s `RunUserSvcUserDefaultTests`
   harness (which extracts and runs the literal "-- Users --" block from
   `install.sh`) extracts only up to (exclusive of) the `id "$RUN_USER"`
   line — well before this cycle's insertion point after `id "$SVC_USER"`
   — so it's unaffected by fixes 3/6 landing there.
5. **Old-CONFIG-path regression check for fix 4**: grepped `tests/` for
   any test asserting the old `/etc/ai-dev-switchboard/switchboard.env`
   path in `gitea-sync-project.sh`'s context; `tests/test_gitea_sync_project.py`
   sets `PROJECTS_DIR` directly via env var and never touches
   `/etc/ai-dev-switchboard/` at all (its own comment already documents
   this — "No /etc/ai-dev-switchboard/switchboard.env on the test box"),
   so the `CONFIG=` path rename doesn't affect it.
6. **Full regression run**: `python3 -m unittest discover -s tests` — all
   1198 tests pass (`Ran 1198 tests ... OK`). Also ran the four most
   directly relevant files individually for a clean, uncluttered result:
   `python3 -m unittest tests.test_gitea_sync_project tests.test_install_ollama tests.test_install_set_env tests.test_install_update -v`
   — 54 tests, all `ok`.
