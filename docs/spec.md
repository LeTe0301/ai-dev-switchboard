# Spec: E2E regression-verification follow-ups, round 5 (items 29-v2, 30-v2, 34, 35)

## Summary
A real Proxmox regression-verification pass (fresh container, CTID 901,
against `main` with all four round-1-4 fixes merged) confirmed 10 of the
original 12 findings genuinely fixed, but found: item 29's fix closed the
*path*-mismatch but exposed a *permission* gap that reproduces the exact
same user-visible symptom; item 30's fix doesn't actually recover the
race on this host, and a second, distinct failure mode (nginx/DNS) was
found; and two new bugs (items 34, 35) were found incidentally while
setting up the verification container. All four are precisely diagnosed
with real repro evidence — see `docs/BACKLOG.md`'s "Items 22-33 regression
verification" section and new items 34/35 for full detail; this spec adds
the fix design on top of that already-complete diagnosis.

## Orchestrator note
No product-manager/ux-designer dispatch — same "fully-diagnosed follow-up"
precedent as rounds 1-4. One of the four (item 30's Docker-restart
fallback) involves a real, stated design judgment call (see Fix 2) worth
a second look at review time, not a rubber-stamp.

---

## Fix 1 — Item 29 (v2): grant `switchboard-svc` a narrow read ACL on the Taiga push config, and stop conflating "missing" with "unreadable"

**Where**: `scripts/taiga-configure-push.sh`, `app/taiga_board.py:129-153`
(`load_config()`), `install.sh` (the `runtime.env` write from round 1's
item-25 fix, and the apt-get dependency line).

**Problem**: the original path-mismatch bug is genuinely fixed — both
sides now resolve to `/home/dev/.config/ai-dev-switchboard/taiga-push.env`.
But that file is deliberately `600`-mode, `RUN_USER`-owned (it holds a
real Taiga password — this is a correct, intentional security choice, not
a bug). `switchboard-svc` (who actually runs `board_read`/`board_write`)
has no read access to it, so `load_config()`'s `open()` call raises
`PermissionError` — and the function's bare `except OSError:` at line 149
gives the exact same "Taiga isn't configured" message it gives for a
genuinely-missing file, hiding the real cause completely.

**Fix, two layers — a real grant, plus an honest failure message if the
grant isn't in place:**

### 1a. `install.sh` — make `SVC_USER`'s name discoverable, and ensure `setfacl` exists
Add `SVC_USER=$SVC_USER` as a third line to the `runtime.env` file this
project's own item-25 fix already writes (world-readable, non-secret
values only — `SVC_USER`'s literal username is not a secret, same
category as `RUN_USER`/`PROJECTS_DIR` already in that file). Add `acl` to
the existing `apt-get install` line (`install.sh:214`) so `setfacl`/
`getfacl` are available — currently not installed.

### 1b. `scripts/taiga-configure-push.sh` — grant the ACL right after writing the file
After the existing `chmod 600 "$CONFIG_FILE"` line, add:
```bash
# Item 29 (v2): switchboard-svc (running app.py/teams.py) needs read
# access to this file for board_read/board_write, but the file correctly
# stays 600/RUN_USER-owned -- never loosened to group/world-readable
# (this holds a live Taiga password). Grant a narrow, single-user POSIX
# ACL instead. Best-effort: if setfacl is unavailable or the filesystem
# doesn't support ACLs, warn clearly rather than silently leaving
# board_read/board_write broken with no signal -- app/taiga_board.py's own
# load_config() (see its own fix, same cycle) gives a distinct error in
# that case too, so this isn't the only signal an operator gets.
RUNTIME_ENV=/etc/ai-dev-switchboard/runtime.env
SVC_USER_NAME="switchboard-svc"
[ -f "$RUNTIME_ENV" ] && SVC_USER_NAME="$(grep '^SVC_USER=' "$RUNTIME_ENV" 2>/dev/null | tail -1 | cut -d= -f2-)"
[ -n "$SVC_USER_NAME" ] || SVC_USER_NAME="switchboard-svc"
if command -v setfacl >/dev/null 2>&1; then
    if setfacl -m "u:${SVC_USER_NAME}:r" "$CONFIG_FILE" 2>/dev/null; then
        echo "Granted $SVC_USER_NAME read access to $CONFIG_FILE (ACL)."
    else
        echo "WARNING: could not grant $SVC_USER_NAME read access to $CONFIG_FILE (setfacl failed -- does this filesystem support POSIX ACLs?). board_read/board_write will not work until this is granted manually: sudo setfacl -m u:${SVC_USER_NAME}:r $CONFIG_FILE" >&2
    fi
else
    echo "WARNING: 'setfacl' not found -- $SVC_USER_NAME cannot read $CONFIG_FILE, so board_read/board_write will not work until this is granted manually. Install the 'acl' package and re-run this script, or: sudo setfacl -m u:${SVC_USER_NAME}:r $CONFIG_FILE" >&2
fi
```

### 1c. `app/taiga_board.py`'s `load_config()` — distinguish permission-denied from missing
```python
    if not os.path.isfile(path):
        raise TaigaPushError(
            f"Taiga isn't configured — run scripts/taiga-configure-push.sh first "
            f"(expected config at {path}).")
    _check_config_permissions(path)
    cfg = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip()
    except PermissionError:
        raise TaigaPushError(
            f"Found {path} but couldn't read it (permission denied) — the account "
            f"running this service needs read access. Re-run "
            f"scripts/taiga-configure-push.sh (it now grants this automatically), "
            f"or grant it manually: sudo setfacl -m u:<service-user>:r {path}.")
    except OSError:
        raise TaigaPushError(
            f"Taiga isn't configured — run scripts/taiga-configure-push.sh first "
            f"(expected config at {path}).")
    return cfg
```
(`except PermissionError:` must come *before* the existing `except
OSError:` — `PermissionError` is an `OSError` subclass, so Python's
except-clause ordering matters here: the more specific clause needs to be
listed first or it will never be reached.)

**Non-goal**: does not touch `_check_config_permissions()` (the existing
group/other-readable *warning*, unrelated to this fix) or
`scripts/taiga_push_spec.py`'s own config-loading path (that script always
runs as `RUN_USER`, the file's own owner — never hits this permission
gap).

**Acceptance criteria**:
- [ ] Fresh `taiga-configure-push.sh` run grants `switchboard-svc` (or
      whatever `SVC_USER` actually is) read access via ACL, confirmed via
      `getfacl` showing the grant.
- [ ] `sudo -u <svc_user> cat <config_path>` succeeds after running the
      script (currently: permission denied).
- [ ] A team lead's `board_read` call succeeds (not "Taiga isn't
      configured") without any manual permission fix needed.
- [ ] If `setfacl` is missing or fails, the operator sees a specific,
      actionable warning at config-setup time, AND a specific (not
      generic "not configured") error at actual use time — never total
      silence about the real cause.

---

## Fix 2 — Item 30 (v2): longer/smarter retry covers both observed failure modes; Docker-daemon-restart fallback is opt-in, not automatic

**Where**: `scripts/taiga-up.sh`.

**Problem**: the round-4 fix's 3-attempt/flat-2s retry doesn't recover
the port-bind race on the verification host — real recovery needed
"tens of seconds to a couple of minutes," or a full `systemctl restart
docker` (100% reliable in testing, but restarts *every* Docker container
on the host, not just Taiga's — a genuinely broad blast radius). A
second, distinct failure mode was also found live: `taiga-gateway`'s
nginx resolves `taiga-front` via Docker's embedded DNS at container
startup with no retry, and exits immediately if that DNS entry hasn't
propagated yet. This project doesn't own that nginx config (it's baked
into the upstream `taigaio/taiga-docker` image), so the fix has to be at
the orchestration layer, not a config edit.

**Design decision, stated explicitly for review**: both failure modes
manifest identically from `taiga-up.sh`'s own vantage point (`taiga-
gateway` not reaching `running` state after `up -d`), and both appear to
be transient docker-internal-state conditions that clear given enough
time. So a single, longer/smarter generic retry (not failure-mode-
specific detection) should recover from either. The `systemctl restart
docker` fallback is real and effective, but restarting the whole Docker
daemon as an *automatic, unattended* side effect of a Taiga toggle-on
click is a broader action than this project takes anywhere else without
an explicit operator decision (manual-click-only deploy, propose-then-
approve board writes, etc.) — **default this fallback to OFF**, opt-in
via an env var, rather than making it the automatic behavior after
bounded retries exhaust. State this reasoning in the script's own
comment, not just here.

**Fix**:
```bash
TAIGA_UP_MAX_ATTEMPTS="${TAIGA_UP_MAX_ATTEMPTS:-5}"
TAIGA_UP_RETRY_BACKOFF_SECONDS="${TAIGA_UP_RETRY_BACKOFF_SECONDS:-10}"
# Item 30 (v2): a full `systemctl restart docker` was the only 100%-
# reliable recovery found on the verification host, but it restarts
# EVERY Docker container on this machine, not just Taiga's -- a real,
# host-wide side effect this project doesn't take automatically/
# unattended anywhere else without an explicit operator decision.
# Default OFF; an operator who's confirmed this is safe on their own host
# (e.g. nothing else Docker-based shares it) can opt in.
TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION="${TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION:-0}"

attempt=1
backoff="$TAIGA_UP_RETRY_BACKOFF_SECONDS"
while [ "$attempt" -le "$TAIGA_UP_MAX_ATTEMPTS" ]; do
    "${COMPOSE[@]}" up -d
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    if [ "$state" = "running" ]; then
        exit 0
    fi
    echo "taiga-up: taiga-gateway didn't come up cleanly (state: ${state:-<none>}), attempt $attempt/$TAIGA_UP_MAX_ATTEMPTS" >&2
    if [ "$attempt" -lt "$TAIGA_UP_MAX_ATTEMPTS" ]; then
        "${COMPOSE[@]}" rm -f taiga-gateway >/dev/null 2>&1 || true
        sleep "$backoff"
        backoff=$((backoff * 2))
    fi
    attempt=$((attempt + 1))
done

if [ "$TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION" -eq 1 ]; then
    echo "taiga-up: all $TAIGA_UP_MAX_ATTEMPTS attempts exhausted -- TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION=1, restarting the Docker daemon itself (affects every container on this host) and trying once more" >&2
    systemctl restart docker
    sleep 5
    "${COMPOSE[@]}" up -d
    state=$("${COMPOSE[@]}" ps taiga-gateway --format '{{.State}}' 2>/dev/null)
    if [ "$state" = "running" ]; then
        exit 0
    fi
fi

echo "taiga-up: taiga-gateway failed to come up after $TAIGA_UP_MAX_ATTEMPTS attempts -- manual intervention needed (check 'docker compose logs taiga-gateway' in $TAIGA_DIR, 'docker network ls', available disk space, or set TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION=1 to let this script restart the Docker daemon itself as a last resort next time)." >&2
exit 1
```
(`backoff=$((backoff * 2))` gives 10s, 20s, 40s, 80s across 5 attempts —
comfortably inside `taiga_run()`'s existing 90s `subprocess.run` timeout
in `app/app.py`? **Check this explicitly** — 10+20+40+80 = 150s of sleep
alone already exceeds 90s. Either raise `taiga_run()`'s own timeout for
the `"up"` action specifically, or tune `TAIGA_UP_MAX_ATTEMPTS`/
`TAIGA_UP_RETRY_BACKOFF_SECONDS`'s defaults down so the total stays under
90s — this spec deliberately leaves the exact numbers to whoever
implements it, but the arithmetic must actually be checked against the
real caller-side timeout, not just look reasonable in isolation.)

**Acceptance criteria**:
- [ ] Total worst-case retry time (all attempts, without the opt-in
      Docker-restart fallback) is verified against `taiga_run()`'s actual
      timeout for the `"up"` action — either fits inside it, or that
      timeout is raised alongside this change. Not left unchecked.
- [ ] `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` defaults to `0`/off.
- [ ] `bash -n`/`shellcheck` clean.

---

## Fix 3 — Item 34: don't start the service until every optional-feature config block has finished writing

**Where**: `install.sh` — the `systemctl daemon-reload` /
`systemctl enable --now ai-dev-switchboard` pair (currently
`install.sh:580-581`), the existing `--update`-gated guarded-restart
block that immediately follows it (currently `install.sh:583-599`), and
the very end of the script (currently `install.sh:939` region, right
before the `echo "== Done =="` summary).

**Problem**: `enable --now` starts the service immediately after the
systemd unit is generated — before the `--with-git-hosting` block (which
writes `GITEA_ENABLED` at `install.sh:731`) and the other `--with-*`
blocks even run. `EnvironmentFile=` is read once at process start, so a
fresh install with `--with-git-hosting` ends up running with
`GITEA_ENABLED` simply absent from the process environment, even though
`switchboard.env` on disk correctly has it — confirmed via
`/proc/<pid>/environ`. The existing `--update`-path guarded restart has
the same latent ordering bug if `--update` is ever combined with a
`--with-*` flag in one invocation (less common, same root cause, not
separately reported but worth closing at the same time since the fix is
identical).

**Fix**: keep `enable --now` where it is (still useful for a plain
install with no `--with-*` flags at all — the service comes up
immediately in that case, which is correct behavior, not a bug). Move
the existing guarded-restart block from right after it to the very end of
the script (after every `--with-*` block, immediately before the
`echo "== Done =="` summary), and make it run **unconditionally** instead
of only when `$UPDATE -eq 1` — a fresh install needs exactly the same
"pick up everything that got written since the process started" restart
an update does; there's nothing update-specific about the fix. Reuse the
existing live-session detection unchanged (same
`sudo -u "$RUN_USER" tmux list-sessions`-based guard, same defer-with-a-
clear-message behavior if any session is live):

```bash
# (delete the existing block from right after `systemctl enable --now`)
# (insert this, unconditionally, right before `echo "== Done =="`)

# Guarded restart -- refuses to restart (not just warns) whenever
# RUN_USER has ANY live tmux session, no --force override (item 13's own
# no-`--force` precedent). Runs unconditionally, not just for --update:
# `systemctl enable --now` above starts the service before any --with-*
# block below it has finished writing its own config to switchboard.env
# (item 34) -- EnvironmentFile= is read once at process start, so without
# this final restart, a fresh install's own optional-feature flags never
# actually take effect in the running process, only on disk.
LIVE_SESSIONS="$(sudo -u "$RUN_USER" tmux list-sessions -F '#{session_name}' 2>/dev/null || true)"
if [ -n "$LIVE_SESSIONS" ]; then
    echo "WARNING: $RUN_USER has live tmux session(s):" >&2
    echo "$LIVE_SESSIONS" | sed 's/^/  - /' >&2
    echo "ai-dev-switchboard was NOT restarted -- restarting now would very likely interrupt these (see docs/ARCHITECTURE.md). Stop them (or wait for them to finish), then run: sudo systemctl restart ai-dev-switchboard -- to pick up every config value written during this install/update run." >&2
else
    echo "-- Restarting ai-dev-switchboard to pick up this run's full configuration --"
    systemctl restart ai-dev-switchboard
fi
```

**Acceptance criteria**:
- [ ] A fresh `install.sh --yes --with-git-hosting` run: `GITEA_ENABLED`
      is present in the running process's environment
      (`/proc/<pid>/environ` or equivalent) without any manual restart.
- [ ] A plain `install.sh --yes` run with no `--with-*` flags: service is
      running and correctly configured, same as today.
- [ ] A live-tmux-session re-run still defers the restart with the
      existing clear warning message, not a behavior change from what
      `--update` already does today.
- [ ] `bash -n`/`shellcheck` clean.

---

## Fix 4 — Item 35: `/team/stop` cleans up a terminal-status run too, not just an active one

**Where**: `app/app.py:6311-6334` (the `/team/stop` route).

**Problem**: `teams.stop_team()` is already correctly unconditional — its
own docstring states it "works regardless of status (running /
blocked_ask_user / finished / error / escalated_max_rounds / stopped)",
and the CLI's `team-stop <run_id>` (which calls it directly) already
works correctly for a terminal run today. The bug is entirely at the web
route layer: `install.sh:6327`'s
`if run is None or run["status"] not in ("running", "blocked_ask_user",
"blocked_board_write"): return {"ok": True, "message": "no team
currently running for this project"}` never calls `stop_team()` at all
for a `finished` or `escalated_max_rounds` run — even though the web
UI's own "Stop team" button is rendered unconditionally for any
non-`idle` status (confirmed by reading `teamRow()`, `app/app.py:4330-
4334` — there's no frontend gating hiding it for a terminal run), so an
operator clicking it on a finished run gets a silent, misleading "no team
currently running" response instead of the cleanup they're asking for.

**Fix**: widen the gate to only exclude the genuine "nothing to do"
case (`run is None`), and let `stop_team()`'s own already-correct,
already-unconditional logic handle every real status itself:
```python
            run = teams.latest_run_for_project(name)
            if run is None:
                return self._json({"ok": True, "message": "no team currently running for this project"})
            entry = _team_threads_get(name)
            if entry is not None and entry.get("run_id") == run["run_id"]:
                entry["cancel_event"].set()
            result = teams.stop_team(run["run_id"])
            self._json({"ok": True, "session_removed": result["session_removed"],
                       "worktrees": result["worktrees"]})
```
(Only the `if` condition changes — drop the `or run["status"] not in
(...)` clause entirely. Nothing else in this route needs to change.)

**Non-goal**: does not touch `launch_team()`'s own pre-flight
`tmux_has(session)` check (`app/teams.py:3980-3983`), which still refuses
to start a new team while a raw tmux session name is taken, regardless of
that session's run's actual status. Deliberate: auto-cleaning up a
terminal run's worktrees as a side effect of trying to start an unrelated
new one would mean a human never got the chance to inspect that finished
run's state first, which conflicts with this project's own "nothing an
agent did is ever silently discarded" precedent (`docs/ARCHITECTURE.md`'s
worktree-cleanup section). The fix here restores the *existing, already-
designed-for* explicit path (a human calling Stop) rather than adding a
new implicit one. If this project decides later that a terminal run
should be auto-reclaimed on the next `team/start` too, that's a separate,
explicitly-scoped decision — not assumed here.

**Acceptance criteria**:
- [ ] `POST /team/stop` against a project with a `finished`-status run:
      actually removes the tmux session and worktrees (previously: silent
      no-op, "no team currently running").
- [ ] Same for `escalated_max_rounds`.
- [ ] `POST /team/start` on that same project now succeeds immediately
      after (previously: blocked by the leftover tmux session name).
- [ ] Existing behavior for `running`/`blocked_ask_user`/
      `blocked_board_write` (already working) is unchanged.
- [ ] `run is None` (no run has ever existed for this project) still
      returns the same "no team currently running" message as today —
      that specific case is genuinely a no-op, correctly.

## Affected areas
`scripts/taiga-configure-push.sh`, `app/taiga_board.py`, `install.sh`
(fixes 1, 3), `scripts/taiga-up.sh` (fix 2), `app/app.py` (fix 4). No
frontend/JS changes needed for any of these four (fix 4's frontend
already correctly shows the Stop button for a terminal run — only the
backend route needed to catch up to it).

## Risk / rollback notes
Fix 4 is the lowest-risk (one `if` condition narrowed, reusing an
already-correct, already-tested underlying function). Fix 3 changes
install-time ordering, not runtime logic — the guard it reuses is
unmodified. Fix 1 adds a new dependency (`acl` package) and a best-effort
ACL grant with an honest failure mode if that grant doesn't take — no
existing behavior is removed. Fix 2's Docker-restart fallback is
deliberately opt-in/off-by-default specifically to keep its risk bounded;
review should specifically check the retry-timing-vs-90s-timeout
arithmetic before approving. Plain `git revert` on any of the four
independently if something regresses — no shared code path between them.
