# Spec: `install.sh --update`/`--upgrade` — update path for an already-installed box

## Summary
Add `--update` (and its synonym `--upgrade`) as a new flag to `install.sh`, parallel to the existing `--with-*` optional-feature flags, that fast-forwards the local source checkout to `origin/$REPO_BRANCH`, re-runs the script's existing (already idempotent) copy/config steps against that fresh checkout, and — only if no `RUN_USER` tmux session is currently live — restarts the `ai-dev-switchboard` service so the new code actually takes effect.

## Goals
- A single documented command (`sudo ./install.sh --update` or `--upgrade`) that pulls in whatever changed on `main` since the box was last installed/updated, for both ways `install.sh` is normally obtained (curl-piped, or an existing local clone).
- Never restart the switchboard service — the one action in this flow that can interrupt a running engine/team session — while any `RUN_USER` tmux session is live; make the deferred state (code updated, service not yet restarted) obvious and give a clear, safe path to finish.
- Fix the one real correctness gap this flag's non-interactive use surfaces: `RUN_USER`/`SVC_USER` prompts silently reset to their literal `"dev"`/`"switchboard-svc"` defaults on a non-interactive (`--yes`) re-run today, instead of defaulting to whatever's already configured — see "Background" below. `--update` is the use case that turns this from a latent inconsistency into something an operator will actually hit.

## Non-goals
- **No migration-runner / schema-versioning mechanism.** See "Background" — every `switchboard.env` key added across this project's whole history is additive and already read with a Python-level fallback default (`os.environ.get`/`.get(..., default)`), and no key has ever been removed or renamed. There is nothing to migrate today. If a genuinely breaking change ever lands, build a real migration step then, against the concrete thing that needs migrating — not speculatively now.
- **No new web UI.** This is `install.sh`/CLI-only — see "ux-designer" note below.
- **Does not restart the Taiga/Gitea Docker Compose stacks**, even if `--update` is combined with `--with-taiga`/`--with-git-hosting` in the same invocation and those stacks are currently toggled on. Those blocks already only re-pull images/re-derive config (existing behavior, unchanged); toggling the stacks themselves stays a separate, explicit web UI action, same as today.
- **Does not add any refresh mechanism for `engines.d/*.engine` files that already exist locally** — `install.sh` already only copies an engine file in if the destination is absent (`[ -e "$dest" ] || cp ...`, line ~239), which is the existing "operator may have hand-customized this" treatment. `--update` preserves that: a brand-new upstream engine file lands automatically; a change to an *existing* one does not. An operator who wants a specific engine definition refreshed diffs/copies it from `$REPO_DIR/engines.d/` by hand.
- **Does not add any locking/concurrency protection.** `install.sh` has none anywhere today (two operators/processes racing any flag, including `--update`, is already undefined behavior); not introduced or fixed here.
- **Does not change or bypass `install.sh`'s existing prompt flow.** RUN_USER/SVC_USER/AUTH_MODE/PUBLISH_MODE prompts still run exactly as they do today (with the one default-value fix above); an operator who wants a fully unattended `--update` already has `--yes` for that, same as any other flag combination.
- **Does not fix the underlying systemd process-isolation gap** (see "Background" — the suspected `KillMode=control-group` interaction). This spec works around it (refuse to restart while sessions are live) rather than re-architecting the unit/spawn model, which is a separate, larger change.

## ux-designer
**Skip this cycle.** `install.sh` is a root-run shell script with no web-UI-visible surface — there is nothing for a UI/UX pass to design. Go straight from this spec to the developer.

## Background / current state

**What actually needs migrating (open question #1, now settled): nothing yet.**
`git log --oneline -- config/switchboard.env.example` shows 17 commits touching that file across this project's entire history; `git log -p` over the same path shows every one of them only ever *adding* a `KEY=value` line — grepping for removed (`^-KEY=`) lines returns nothing. `app/app.py` and `app/teams.py` read every one of those keys via `os.environ.get(...)`/`.get(..., default)` (57 and 26 call sites respectively) — always with a working fallback. Backlog item 14's own text already predicted this ("a real breaking schema change hasn't happened yet"); this confirms it directly. Decision: **`--update` is "pull the latest source, no destructive state migration needed" for this first version.** If a real breaking change happens later (a key rename, a required-value change, a Compose stack's on-disk layout changing), add a real migration step *then*, scoped to that concrete change — not speculatively now.

**Where the switchboard's own source lives, and what "pull latest main" concretely means.**
`install.sh` (top of file, "Two ways to run it") already has two invocation shapes, and both leave a git checkout of this repo sitting at a fixed, discoverable path:
1. **Piped via curl** (`bash -c "$(curl ... /install.sh)"`): the bootstrap block (lines ~54-67) detects there's no `app/app.py` next to the running script, clones this repo to `$SRC_DIR` (default `/opt/ai-dev-switchboard-src`, override via env var) if it's not already there, or `git -C "$SRC_DIR" pull --ff-only` if it is, then `exec`s `install.sh` from that real checkout. Everything after that point runs with `REPO_DIR="$SRC_DIR"`.
2. **From an existing local clone** (`sudo ./install.sh [flags]`, run from inside a `git clone` of this repo done by hand): `REPO_DIR` is wherever that clone lives — whatever directory the operator put it in, not a fixed path.

Either way, by the time the rest of the script runs, `REPO_DIR` is a real git working copy with a real `origin` remote (cloned from `$REPO_URL`, or whatever the operator's own manual clone points at) — that's the thing `--update` pulls. `$INSTALL_DIR` (`/opt/ai-dev-switchboard`, where `app.py`/`teams.py` are actually `cp`'d to and the systemd unit's `ExecStart` points at) is a **plain, non-git-tracked copy** — it is never itself a git checkout, so "pull latest main" always means "pull `$REPO_DIR`, then let the script's own existing copy step (`cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"`, already unconditional, line ~233) carry it into `$INSTALL_DIR`" — no new copy logic needed, just make sure `$REPO_DIR` is fresh *before* that copy step runs.

Note: the curl-pipe bootstrap block above (lines ~54-67) already does an unconditional, unguarded `git -C "$SRC_DIR" pull --ff-only` on **every** curl-piped invocation today, completely independent of any `--update` flag — this is pre-existing behavior, not introduced by this spec, and it's already safe today only because nothing after it ever restarts the live service on a plain re-run (see next paragraph). This spec does not touch that block; it adds the *same kind* of pull, with proper dirty/branch/divergence checks and clear error messages, for **both** invocation shapes uniformly, ahead of everything else `install.sh` does.

**Does an update ever restart a running session? (open question #2, now settled): yes, and it's a bigger blast radius than it looks.**
`app/teams.py`'s driving loop for a team run is a `threading.Thread` living **inside the `ai-dev-switchboard` service's own Python process** (`app/teams.py` line ~4546/4608, `threading.Thread(target=_tail_loop, daemon=True)`, and the lead loop itself) — restarting that process ends that thread outright, mid-run, with no resume. That alone is enough to require a guard.
It's actually worse than just the driving thread: the generated systemd unit (`install.sh` lines ~483-497, mirrored in `systemd/ai-dev-switchboard.service`) sets no `KillMode` at all, so systemd's default (`KillMode=control-group`) applies — on `systemctl restart`, systemd sends `SIGTERM`/`SIGKILL` to **every process still in that unit's cgroup**, not just the unit's own direct child. Every per-project engine session and every team session is started via `sudo -u $RUN_USER tmux new-session -d ...` (`app.py` line ~3337, and `run_startup_watch`) as a descendant of the service process, and nothing in this codebase does anything (`systemd-run --scope`, an explicit cgroup move, `Delegate=yes`) to move that spawned `tmux` server out of the service's own cgroup first. The practical implication: **restarting `ai-dev-switchboard.service` while any session is running very likely takes down the entire `RUN_USER` tmux server, not just the switchboard's own web process** — every open project session, not only team runs. This is inferred from documented `systemd.kill(5)` default behavior, not confirmed against a live box in this session; flagged under "Risk / rollback notes" below as worth a quick empirical check during review, but treated as the working assumption either way since being over-cautious here costs nothing and being wrong about it is exactly the kind of "silently kills a running session" outcome this project's own discipline (2c part 1's fast-forward-only sync, deploy being manual-click-only, item 13's worktree-removal-refuses-on-dirty-state) exists to prevent.
Decision, matching that same discipline: **`--update` refuses to restart the service (not just "warns and offers to proceed") whenever `RUN_USER` has any live tmux session**, full stop — no `--force` override baked into this flag, mirroring `_remove_worktree()`'s own no-`--force` `git worktree remove` precedent (item 13) where the human resolves the blocking state directly rather than the tool offering a shortcut around it. An operator who's certain it's safe can always run `sudo systemctl restart ai-dev-switchboard` themselves — that's not something this feature needs to gate.

**A found, load-bearing bug this flag's own safety story depends on fixing.**
`install.sh`'s `AUTH_MODE`/`PVE_HOST`/`SIMPLE_USERNAME`/`PUBLISH_MODE` prompts (lines ~257-282) already default to whatever's currently in `switchboard.env` via `get_env`, e.g. `PUBLISH_MODE=$(prompt "..." "$(get_env "$ENV_FILE" PUBLISH_MODE)")` — so a non-interactive re-run (`--yes`, which makes `prompt()` just echo its default straight back, no read) correctly preserves the existing configured value. **`RUN_USER`/`SVC_USER` (lines ~188-189) are the one place that doesn't follow this pattern** — they default to the literal strings `"dev"`/`"switchboard-svc"` instead of `get_env "$ENV_FILE" RUN_USER`/`SVC_USER`. On today's install.sh this is a low-visibility latent bug (a first-time interactive install always sets these correctly; a non-interactive re-run reusing `RUN_USER=dev` already matches most operators' actual setup). `--update` is the use case that turns this into something an operator will actually hit: it's specifically meant to be re-run with `--yes` on an already-configured, already-running box, and if that box's `RUN_USER` isn't literally `"dev"`, a non-interactive `--update` would silently reset `RUN_USER`/`SVC_USER`/derived `PROJECTS_DIR` back to the defaults and overwrite `switchboard.env` with the wrong values via the `set_env` calls that immediately follow (lines ~246-248). Fixing this two-line default (source from `get_env "$ENV_FILE" RUN_USER`/`SVC_USER`, falling back to `"dev"`/`"switchboard-svc"` only when that's empty, i.e. first install) is folded into this spec rather than punted, because `--update`'s entire safety claim ("safe to re-run") is false without it.

## Proposed approach

All changes are in `install.sh` (see "Affected areas" for the exact line anchors — line numbers below are against the current file and will drift slightly as edits land; the developer should use the referenced code/comments as the actual anchor, not the exact line number).

1. **Flag parsing** (existing `for arg in "$@"` loop, ~line 78-89): add
   ```sh
   --update|--upgrade) UPDATE=1 ;;
   ```
   next to the other `WITH_*` flags, with `UPDATE=0` declared alongside them (~line 71-77). Both spellings are exact synonyms — same internal flag, no behavior difference.

2. **`RUN_USER`/`SVC_USER` default fix** (~lines 188-189): change
   ```sh
   RUN_USER=$(prompt "Unprivileged user to run coding sessions as" "dev")
   SVC_USER=$(prompt "Unprivileged user to run the web UI process as" "switchboard-svc")
   ```
   to source the default from the existing `switchboard.env` first (same idiom `PVE_HOST`/`SIMPLE_USERNAME`/`PUBLISH_MODE` already use just below), e.g.:
   ```sh
   RUN_USER_DEFAULT="$(get_env "$ENV_FILE" RUN_USER)"; RUN_USER_DEFAULT="${RUN_USER_DEFAULT:-dev}"
   SVC_USER_DEFAULT="$(get_env "$ENV_FILE" SVC_USER)"; SVC_USER_DEFAULT="${SVC_USER_DEFAULT:-switchboard-svc}"
   RUN_USER=$(prompt "Unprivileged user to run coding sessions as" "$RUN_USER_DEFAULT")
   SVC_USER=$(prompt "Unprivileged user to run the web UI process as" "$SVC_USER_DEFAULT")
   ```
   Note `ENV_FILE` isn't assigned until later in the file today (~line 243) — this fix needs `ENV_FILE="$CONFIG_DIR/switchboard.env"` computed before this point too (it's a plain path derived from `$CONFIG_DIR`, already set at line ~96, so just hoist the one assignment line up; `get_env` against a not-yet-existing file already returns empty cleanly via its own `2>/dev/null`, so this is correct for a genuine first install too, where it falls back to `"dev"`/`"switchboard-svc"` exactly as before).

3. **New "update" section**, placed right after the existing root check (~line 94) and before `CONFIG_DIR=...` (~line 96) — i.e. before anything else in the script reads from `$REPO_DIR`:
   ```sh
   if [ "$UPDATE" -eq 1 ]; then
       echo "-- Update (--update/--upgrade): pulling $REPO_BRANCH into $REPO_DIR --"
       if [ ! -d "$REPO_DIR/.git" ]; then
           echo "ERROR: $REPO_DIR is not a git checkout, so --update has nothing to pull. Re-run via the curl-pipe installer (which clones \$SRC_DIR itself), or 'git clone $REPO_URL' and run install.sh --update from inside that clone." >&2
           exit 1
       fi
       if [ -n "$(git -C "$REPO_DIR" status --porcelain)" ]; then
           echo "ERROR: $REPO_DIR has uncommitted local changes -- refusing to pull over them. Commit, stash, or discard them, then re-run --update." >&2
           exit 1
       fi
       CURRENT_BRANCH="$(git -C "$REPO_DIR" symbolic-ref --short -q HEAD || true)"
       if [ "$CURRENT_BRANCH" != "$REPO_BRANCH" ]; then
           echo "ERROR: $REPO_DIR is checked out on '${CURRENT_BRANCH:-<detached HEAD>}', not \$REPO_BRANCH ('$REPO_BRANCH') -- switch to $REPO_BRANCH yourself (or set REPO_BRANCH to match what's checked out) before re-running --update." >&2
           exit 1
       fi
       git -C "$REPO_DIR" fetch origin "$REPO_BRANCH"
       if ! git -C "$REPO_DIR" merge-base --is-ancestor HEAD "origin/$REPO_BRANCH"; then
           echo "ERROR: $REPO_DIR's local $REPO_BRANCH has diverged from origin/$REPO_BRANCH -- refusing to merge. Resolve by hand, then re-run --update." >&2
           exit 1
       fi
       git -C "$REPO_DIR" merge --ff-only "origin/$REPO_BRANCH"
       echo "Pulled $(git -C "$REPO_DIR" rev-parse --short HEAD) ($REPO_BRANCH)."
   fi
   ```
   This mirrors `scripts/gitea-sync-project.sh`'s own fetch-then-`merge --ff-only` shape (dirty check, ancestor check, never `reset --hard`) rather than a bare `git pull`. Everything below this point in the script (the `cp`/`set_env`/`--with-*` blocks) already re-reads from `$REPO_DIR`/`$ENV_FILE` on every run — no further "update-specific" copy logic is needed; `--update` alone (no other flags) just makes the existing idempotent re-run pick up fresh code.

4. **Guarded restart**, placed right after the existing `systemctl daemon-reload; systemctl enable --now ai-dev-switchboard` (~lines 498-499) — this is after `RUN_USER` is resolved and after `app.py`/`teams.py` have already been re-copied to `$INSTALL_DIR`:
   ```sh
   if [ "$UPDATE" -eq 1 ]; then
       LIVE_SESSIONS="$(sudo -u "$RUN_USER" tmux list-sessions -F '#{session_name}' 2>/dev/null || true)"
       if [ -n "$LIVE_SESSIONS" ]; then
           echo "WARNING: $RUN_USER has live tmux session(s):" >&2
           echo "$LIVE_SESSIONS" | sed 's/^/  - /' >&2
           echo "New code was copied to $INSTALL_DIR, but ai-dev-switchboard was NOT restarted -- restarting now would very likely interrupt these (see docs/ARCHITECTURE.md). Stop them (or wait for them to finish), then re-run 'install.sh --update' -- it will be a fast no-op pull, just the restart. Or, if you're sure it's safe: sudo systemctl restart ai-dev-switchboard." >&2
       else
           echo "-- Update: no live $RUN_USER sessions -- restarting ai-dev-switchboard to pick up the update --"
           systemctl restart ai-dev-switchboard
       fi
   fi
   ```
   Root can `sudo -u "$RUN_USER" tmux list-sessions` directly (no password needed as root); this deliberately treats **any** live `RUN_USER` tmux session as blocking, not just ones matching the reserved `{engine}-{project}`/`team-{project}`/`switchboard-headless-{run_id}` naming — see "Edge cases".

5. **Top-of-file flag documentation** (~lines 12-44): add a `--update`/`--upgrade` bullet in the same style/verbosity as the existing `--with-ollama` entry, and update the "Safe to re-run" comment (~line 45-47) to mention that `--update` additionally pulls `$REPO_DIR` first and may defer its own service restart.

## Affected areas
- `install.sh` — flag parsing, `RUN_USER`/`SVC_USER` default fix, new update-pull section, guarded-restart section, top-of-file doc comment. All changes are additive/localized; no existing flag's behavior changes when `--update` is absent.
- `README.md` — add a short "Updating" mention (Quickstart or its own small section) documenting `sudo ./install.sh --update` (from the existing checkout at `/opt/ai-dev-switchboard-src` for curl-based installs, or from wherever the operator's own clone lives) and that it defers its own restart around live sessions.
- `docs/ARCHITECTURE.md` — add a short note (same documented-finding style as the existing host-agent `URL_FILE` writeup) about the suspected `KillMode=control-group`-takes-the-whole-tmux-server-down-on-restart behavior, and that this is why `--update` refuses to restart while sessions are live.
- `docs/BACKLOG.md` item 14 — already updated this session to record the settled "flag, not a separate script" decision (see this file's own diff).
- `tests/` — a new `tests/test_install_update.py`, following `tests/test_deploy_target.py`'s `InstallShTemplateTests`/fake-PATH-stub-binaries precedent (`_extract_between` to pull just the new update section/flag-parsing/restart-guard code out of `install.sh` for isolated testing, stub `git`/`tmux`/`systemctl` on a fake `PATH` the same way that file already stubs `apt-get`/`useradd`/`systemd`) — see "Acceptance criteria" for what needs covering. No real root/sudo/systemd/tmux server should be required for the bulk of these; reserve a `PrivilegedEndToEndTests`-style class (skipped cleanly without root, matching that file's own precedent) only for anything that genuinely can't be faked.

## Edge cases
- **Fresh box, first-ever install, `--update` included in the flags** (e.g. `curl ... | bash -s -- --update --yes`): harmless no-op — the bootstrap clone just placed `$SRC_DIR` at the tip of `$REPO_BRANCH`, so the fetch/ff-only-merge in step 3 has nothing to do; the rest of the script proceeds as a normal first install. No special-casing needed.
- **No network reachable when `--update` runs**: `git fetch origin "$REPO_BRANCH"` fails, `set -euo pipefail` aborts the whole run before touching `$INSTALL_DIR`/the service — correct, since silently continuing with stale source would defeat the entire point of the flag.
- **`RUN_USER` has a live tmux session unrelated to the switchboard** (personal use — README explicitly allows this: "This account needs whatever access your real agentic work needs — the switchboard doesn't constrain that"): still coarsely counted as "live" by the plain `tmux list-sessions` check in step 4, so the restart is deferred even though nothing switchboard-owned is actually at risk. Deliberate, documented false-positive tradeoff for this first version — precise `{engine}-{project}`/`team-{project}`/`switchboard-headless-{run_id}` filtering (cross-referencing `PROJECTS_DIR` and `ENGINES_DIR`, mirroring `active_engine()`'s own logic in `app.py`) is a reasonable future refinement, not required now.
- **`--update` combined with `--with-taiga`/`--with-git-hosting`/etc. in one invocation**: works uniformly — the pull in step 3 runs before any `--with-*` block, so every block that reads from `$REPO_DIR` (e.g. `install -m 755 "$REPO_DIR/scripts/taiga-up.sh" ...`) already sees the fresh checkout with no special-casing needed.
- **Two `--update` runs back-to-back with sessions still live both times**: second run's pull is a fast no-op ("already up to date"), restart is deferred again with the same message — safe to retry indefinitely.
- **Operator manually runs `systemctl restart ai-dev-switchboard` themselves while `--update` deferred it**: outside this flag's control entirely, exactly as intended — the deferral is advisory-by-default-refusal, not an enforced lock.

## Acceptance criteria
- [ ] Given a clean `$REPO_DIR` git checkout on `$REPO_BRANCH` behind `origin/$REPO_BRANCH`, and no live `RUN_USER` tmux sessions, when `sudo ./install.sh --update` runs, then `$REPO_DIR` is fast-forwarded, `app.py`/`teams.py` are re-copied to `$INSTALL_DIR`, and `ai-dev-switchboard.service` is restarted.
- [ ] Given the same setup but with at least one live `RUN_USER` tmux session, when `--update` runs, then `$REPO_DIR`/`$INSTALL_DIR` are still updated, `systemctl restart` is never invoked, and stderr names the live session(s) plus the retry/manual-override instructions.
- [ ] Given `$REPO_DIR` has uncommitted changes, when `--update` runs, then the script exits non-zero before fetching or touching `$INSTALL_DIR`/the service, naming the dirty state.
- [ ] Given `$REPO_DIR`'s checked-out branch differs from `$REPO_BRANCH` (or HEAD is detached), when `--update` runs, then the script exits non-zero before fetching, naming the mismatch.
- [ ] Given `$REPO_DIR`'s local `$REPO_BRANCH` has diverged from `origin/$REPO_BRANCH` (a real divergence, not just being behind), when `--update` runs, then the script exits non-zero after the fetch but before merging, naming the divergence.
- [ ] Given `$REPO_DIR` is not a git repository at all, when `--update` runs, then the script exits non-zero immediately with an actionable message, before any other step in the script runs.
- [ ] `--update` and `--upgrade` are accepted as exact synonyms.
- [ ] Given a box whose `switchboard.env` already has `RUN_USER=someuser` (not `"dev"`), when `sudo ./install.sh --update --yes` runs, then `RUN_USER`/`SVC_USER`/derived `PROJECTS_DIR` remain `someuser`-derived in both the running script's variables and the rewritten `switchboard.env` — not silently reset to `"dev"`/`"switchboard-svc"`.
- [ ] Given a plain `install.sh` invocation with neither `--update` nor `--upgrade`, when run, then behavior is unchanged from before this feature — no pull, no restart-guard logic invoked, no behavior change to any existing flag.
- [ ] `--update` combined with `--with-taiga` (or any other `--with-*` flag) in one invocation installs/refreshes that feature using the freshly-pulled `$REPO_DIR`, with no stale-source artifacts.

## Open questions
None blocking — both open questions from the original backlog entry are resolved above (settled by the user for the flag-vs-script question; resolved by codebase archaeology for the migration and running-session questions). One non-blocking note carried forward: the `KillMode=control-group`-takes-down-the-whole-tmux-server inference in "Background" is reasoned from `systemd.kill(5)`'s documented default, not verified against a live box in this session — worth a quick empirical confirmation during the reviewer's testing pass (spin up a tmux session as `RUN_USER`, `systemctl restart ai-dev-switchboard`, check whether the session survives), but the spec's design (hard-refuse-while-live-sessions-exist) is correct either way, so this doesn't block implementation.

## Risk / rollback notes
- The one genuinely destructive action introduced here is the guarded `systemctl restart` — everything else (git fetch/merge, file copies, config upserts) is exactly as safe as any other `install.sh` re-run already is today.
- If an update goes wrong: `$REPO_DIR` is an ordinary git checkout — `git -C "$REPO_DIR" log`/`git -C "$REPO_DIR" reset --hard <previous-sha>` (a human-driven, deliberate action, not something this feature automates) followed by a plain `sudo ./install.sh` re-run (no `--update` needed) restores the previous code to `$INSTALL_DIR` and restarts the service the same way any other re-run does today.
- No data migration risk exists in this version (see "Background" — nothing is migrated yet), so there's no config-corruption rollback scenario to plan for beyond the code-level one above.
- If the reviewer's empirical check (see "Open questions") finds that a service restart does *not* actually take down `RUN_USER`'s tmux server (i.e. the cgroup inference above is wrong), the guard in step 4 is still correct to keep — it's still the only thing standing between an update and interrupting an in-process team driving thread, which restarting genuinely does end unconditionally either way.
