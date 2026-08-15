# Spec: install.sh fixes from Proxmox E2E test round 1 (items 22-27)

## Summary
Six real, precisely-diagnosed bugs found by a live Proxmox end-to-end test
(`docs/BACKLOG.md` items 22-27), all in `install.sh` (item 25 also touches
`scripts/gitea-sync-project.sh`). Bundled into one cycle since all six are
small, independent, install.sh-only fixes with exact repro steps and
verified-locally-working fixes already established by the E2E tester — no
open design questions remain. Two of the six (22, 27) mean the product is
currently completely broken (service won't start at all / flagship
multi-agent-teams feature can't start at all) — this is the highest-priority
fix cycle from that report.

## Orchestrator note
No product-manager/ux-designer dispatch — every fix's shape is already
fully diagnosed with exact repro, exact root cause, and (for 22/24/25/26/27)
a verified-locally-working fix, by the E2E tester. Matches this project's
own "skip full triage for a fully-diagnosed follow-up" precedent used
repeatedly this session.

---

## Fix 1 — Item 22: copy `app/taiga_board.py` during install

**Where**: `install.sh:283-285` (the "-- App + engines --" step).

**Current**:
```bash
echo "-- App + engines --"
cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"
cp "$REPO_DIR/app/teams.py" "$INSTALL_DIR/teams.py"
```

**Fix**: add the missing third file. Confirmed via `ls app/*.py` that
`app/` contains exactly three `.py` files (`app.py`, `taiga_board.py`,
`teams.py`) — no other stragglers to find.
```bash
echo "-- App + engines --"
cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"
cp "$REPO_DIR/app/teams.py" "$INSTALL_DIR/teams.py"
cp "$REPO_DIR/app/taiga_board.py" "$INSTALL_DIR/taiga_board.py"
```

**Acceptance**: after a fresh install, `ai-dev-switchboard.service` starts
successfully (no `ModuleNotFoundError: No module named 'taiga_board'` in
`journalctl -u ai-dev-switchboard`).

---

## Fix 2 — Item 23: drop `-it` from the printed Gitea admin-bootstrap command

**Where**: `install.sh:937` (inside the end-of-run `--with-git-hosting`
summary block).

**Current**:
```bash
echo "       docker exec -it --user git ai-dev-switchboard-gitea gitea admin user create \\"
```

**Fix**: remove `-it` (the command passes every value as a flag, nothing
about it is actually interactive — `-it` is the only thing that breaks it
when run without an attached TTY, e.g. via `pct exec` or a provisioning
script).
```bash
echo "       docker exec --user git ai-dev-switchboard-gitea gitea admin user create \\"
```

**Acceptance**: the printed command, copy-pasted verbatim and run via
`pct exec <ctid> -- bash -c '<command>'` (no TTY), succeeds.

---

## Fix 3 — Item 24: chown `$STATE_DIR` itself, not just its `uploads` subdirectory

**Where**: `install.sh:112-113` (STATE_DIR creation) and `install.sh:455-456`
(the existing narrower chown).

**Current** (`install.sh:112-113`):
```bash
STATE_DIR=/var/lib/ai-dev-switchboard
mkdir -p "$CONFIG_DIR" "$INSTALL_DIR" "$STATE_DIR"
```
(no chown anywhere for `$STATE_DIR` itself — only `install.sh:456`'s
`chown "$SVC_USER:$SVC_USER" "$STATE_DIR/uploads"` touches one specific
subdirectory, created later in the run.)

**Fix**: add a chown for `$STATE_DIR` itself, right after it's created.
Since `$SVC_USER` doesn't exist yet at line 113 (it's created later, at
`install.sh:243`), this specific chown must be added *after* that point —
place it immediately after `install.sh:243`'s `useradd` line, alongside
this fix's own comment:
```bash
id "$SVC_USER" &>/dev/null || { useradd -r -m -d "/home/$SVC_USER" -s /usr/sbin/nologin "$SVC_USER"; echo "Created $SVC_USER"; }
chown "$SVC_USER:$SVC_USER" "$STATE_DIR"
```
The existing narrower `chown "$SVC_USER:$SVC_USER" "$STATE_DIR/uploads"`
at line 456 stays — harmless, idempotent, and `uploads` doesn't exist yet
at line 243 (it's `mkdir -p`'d later at line 455) so it still needs its
own explicit chown once created.

**Acceptance**: `sudo -u switchboard-svc touch
/var/lib/ai-dev-switchboard/testwrite` succeeds after a fresh install
(then remove the test file). A new Gitea-hosted project's repo-map entry
(`GITEA_REPO_MAP_FILE`) is actually written on `create_project()`.

---

## Fix 4 — Item 25: `gitea-sync-project.sh` reads a world-readable runtime file, not the 600-mode secrets file

**Where**: `install.sh` (new file write, near where `$ENV_FILE` itself is
finalized — `install.sh:472-473`) and `scripts/gitea-sync-project.sh:37-40`.

**Problem**: `gitea-sync-project.sh` runs as `RUN_USER` (dispatched via
`sudo -u $RUN_USER`, confirmed in the script's own header comment) but
sources `/etc/ai-dev-switchboard/switchboard.env`, which is
`600`/`SVC_USER`-owned (`install.sh:472-473`). `dev` can't read it,
`source` fails under `set -euo pipefail`, and the whole script exits 1
silently (the poll's own non-zero-exit handling just retries next
interval forever, never surfacing the failure).

**Fix**: the script only actually needs two static, non-secret values —
`RUN_USER` and `PROJECTS_DIR` — both already known at `install.sh` time.
Write them into a small, dedicated, world-readable file instead of routing
through the secrets file.

In `install.sh`, right after `install.sh:472-473`'s existing
`chown`/`chmod 600` on `$ENV_FILE`, add a second, deliberately
world-readable file:
```bash
chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Item 25 fix: a small, deliberately world-readable (644) sibling file
# holding ONLY non-secret, install-time-static values -- gitea-sync-
# project.sh runs as RUN_USER (not SVC_USER) and cannot read the 600
# switchboard.env, but needs RUN_USER/PROJECTS_DIR to locate the project
# it's syncing. Never write a secret into this file.
RUNTIME_ENV_FILE="$CONFIG_DIR/runtime.env"
cat > "$RUNTIME_ENV_FILE" <<EOF
RUN_USER=$RUN_USER
PROJECTS_DIR=$PROJECTS_DIR
EOF
chmod 644 "$RUNTIME_ENV_FILE"
```
(Placed after `$PROJECTS_DIR` is set — confirm `$PROJECTS_DIR` is already
in scope at this point in the file; it's set at `install.sh:245`, well
before line 472, so it is.)

In `scripts/gitea-sync-project.sh:37-40`, change:
```bash
CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"
```
to:
```bash
CONFIG=/etc/ai-dev-switchboard/runtime.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"
```
(Same fallback-default shape, just pointed at the new file — a missing
`runtime.env` on an old install that hasn't re-run `install.sh` yet still
degrades to the same `dev`/`/home/dev/projects` defaults it has today, not
a hard failure.)

**Non-goal**: do not loosen `switchboard.env` itself to 644 — that would
leak `GITEA_API_TOKEN`/`SIMPLE_PASSWORD`/`TOTP_SECRET` to every local
account, including `RUN_USER`'s own coding-agent sessions. The whole point
of this fix is a separate, deliberately-narrow file.

**Acceptance**: `sudo -u dev cat /etc/ai-dev-switchboard/runtime.env`
succeeds and shows correct `RUN_USER`/`PROJECTS_DIR` values. A real push to
a Gitea-hosted project's repo fast-forwards `PROJECTS_DIR/<name>` within
one poll interval (`GITEA_POLL_INTERVAL_SECONDS`).

---

## Fix 5 — Item 26: chown the top-level `~RUN_USER/.local`, not just the code-server subtree

**Where**: `install.sh:279` (inside the `WITH_CODE_SERVER` block).

**Current**:
```bash
        chown -R "$RUN_USER:$RUN_USER" "$CODE_SERVER_DIR"
```
(`$CODE_SERVER_DIR` = `/home/$RUN_USER/.local/share/code-server` —
two levels below `.local` itself. `mkdir -p` at line 271 created every
intermediate directory, including `.local` and `.local/share`, as
`root:root`, and this chown never reaches back up to them.)

**Fix**: chown the top-level `.local` directory instead, recursively (it
covers `.local/share/code-server` as a subset, so this is strictly a
superset fix, not a narrower one):
```bash
        chown -R "$RUN_USER:$RUN_USER" "/home/$RUN_USER/.local"
```

**Acceptance**: after a fresh `--with-code-server` install,
`sudo -u dev mkdir /home/dev/.local/testdir` succeeds (then remove it), and
`sudo -u dev pipx install --quiet some-real-package` (or `pip install
--user`) no longer fails with a `PermissionError` under `.local`.

---

## Fix 6 — Item 27: configure `safe.directory` for `SVC_USER` (the most severe fix in this cycle)

**Where**: `install.sh`, right after `install.sh:243`'s `SVC_USER` creation
(same insertion point as Fix 3 above — both land in the same spot, so
implement them together, in this order: `useradd` → `chown $STATE_DIR` →
`safe.directory` config, or any order between the two additions, as long
as both come after the `useradd` line).

**Problem**: `_check_git_repo_state()` (`app/teams.py`) runs
`git -C workdir rev-parse --is-inside-work-tree` as `SVC_USER`, read-only.
Every project directory is `RUN_USER`-owned — a different user — and git
≥2.35.2's "dubious ownership" protection (CVE-2022-24765 mitigation)
refuses to operate on a repo owned by a different user unless that exact
path is in the caller's own `safe.directory` list. `install.sh` never
configures this at all, so `team/start` fails on every single project with
a flatly wrong `"not a git repository"` error (the check's own
non-"true"-means-not-a-repo error mapping can't distinguish "genuinely not
a repo" from "blocked by this safety check").

**Fix**: add, once, right after `SVC_USER` is created:
```bash
id "$SVC_USER" &>/dev/null || { useradd -r -m -d "/home/$SVC_USER" -s /usr/sbin/nologin "$SVC_USER"; echo "Created $SVC_USER"; }
# Item 27 fix: SVC_USER (running app.py) needs to run read-only git
# inspection commands (_check_git_repo_state()) against every RUN_USER-
# owned project directory. Git's "dubious ownership" protection
# (CVE-2022-24765) refuses this by default across a user boundary. `*`
# (not a glob against a fixed path) is required -- git's own
# safe.directory only matches literal paths or the literal string `*`,
# and projects are created dynamically after install, so a fixed list of
# literal paths can't work here. Bounded, deliberate trade-off: SVC_USER
# only ever runs read-only inspection git commands directly (writes
# already cross into RUN_USER via sudo -u), so this doesn't hand out any
# privilege beyond what the account already effectively has.
sudo -u "$SVC_USER" git config --global --add safe.directory '*'
```

**Acceptance**: `sudo -u switchboard-svc git -C <any RUN_USER-owned
project dir> rev-parse --is-inside-work-tree` prints `true` (not the
"dubious ownership" fatal error) after a fresh install. `POST
/projects/<name>/team/start` no longer 400s with `"not a git repository"`
against a genuinely valid git repo.

## Affected areas
`install.sh` (all six fixes), `scripts/gitea-sync-project.sh` (fix 4 only).
No Python/JS code touched, no test suite changes expected (these are
install-time shell logic — the existing `tests/test_install_*.py` files
test `install.sh`'s bash logic directly via subprocess; check whether any
existing test asserts the exact `cp`/`chown` lines being changed here and
update it if so, but do not invent new install.sh test infrastructure
beyond what already exists in this repo for similar prior fixes — e.g.
`tests/test_install_set_env.py` from item 10 is the precedent for how this
project tests install.sh logic).

## Risk / rollback notes
All six changes are small, additive-or-corrective single-purpose edits to
already-idempotent install.sh steps (every one of them is safe to re-run).
None touch application logic. Plain `git revert` on `install.sh`/
`scripts/gitea-sync-project.sh` if anything regresses. Fix 6's `*`
safe.directory trade-off is the one worth a second look at review time
given it's a real (if bounded, per the reasoning above) security-relevant
change — not a rubber-stamp.
