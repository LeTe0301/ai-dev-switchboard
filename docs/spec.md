# Spec: Round 6 — Taiga gateway startup-ordering crash-loop, ACL-aware push-spec security check, /status terminal-state staleness

## Summary
Three independent, already-diagnosed bugfixes from round-5 Proxmox verification (docs/BACKLOG.md items 30/37/38): gate `taiga-gateway`'s startup on `taiga-front` actually being resolvable instead of retrying a doomed recreate loop, make `taiga_push_spec.py`'s permission check ACL-aware so it stops recommending a `chmod` that undoes item 29's fix, and make `GET /status` report a run's terminal state from the same source `/team/stop` already uses instead of leaving `escalated_max_rounds` runs stuck looking `blocked` forever.

## Goals
- **Item 30**: `taiga-up.sh` reliably brings `taiga-gateway` up on a fresh `--with-taiga` install without hitting the nginx-can't-resolve-`taiga-front`-yet crash-loop, and its success check confirms the container stays up past a short settle window instead of trusting a single point-in-time read.
- **Item 37**: `taiga_push_spec.py`'s config-permission check correctly recognizes a narrowly-ACL'd (item 29 style) config file as safe, and never prints a `chmod` remediation that would collapse its ACL mask.
- **Item 38**: `GET /status` exposes an unambiguous, correct terminal/non-terminal signal for a team run, sourced from the same status set `stop_team()` already treats as terminal, so a poller waiting on run completion doesn't hang forever on `escalated_max_rounds`. The `"project": null` observation is investigated and either explained/fixed or confirmed not to reproduce with current code.

## Non-goals
- **Item 30**: not patching `taiga.conf` or any other file inside the `taigaio/taiga-docker` checkout at `$TAIGA_DIR` directly (see "Proposed approach" for why); not re-investigating or fixing the original, still-unconfirmed root cause of the underlying Docker port-bind race itself, only the crash-loop it triggers; not changing the opt-in `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` escape hatch; not adding disk-space preflight checks (that's the separate, already-filed item 31).
- **Item 37**: not building a general-purpose ACL auditor (e.g. flagging unexpected extra named user/group grants beyond the specific `other::`-exposure check); not touching `taiga-configure-push.sh`'s own `setfacl` grant logic (already correct, per item 29); not adding an auto-remediate/`--fix` flag — only correcting the diagnosis and the printed remediation text.
- **Item 38**: not reworking the existing coarse `team.status` UI enum (`idle`/`running`/`blocked`/`finished`/`error`) the frontend already renders off, or `waiting_on_you`/`escalation_kind` semantics — the fix is additive only. Not building a fix for the `"project": null` observation if it turns out not to reproduce under current code (see "Open questions").

## Background / current state

### Item 30 — `taiga-gateway` crash-loops on startup-ordering, not a real DNS outage
`scripts/taiga-up.sh` (installed as `/usr/local/bin/ai-dev-switchboard-taiga-up.sh`, root-run via sudoers, invoked by `app.py`'s `taiga_run("up")` with a 180s timeout — `tests/test_taiga.py::test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop`) already has a round-5 retry/backoff loop (5 attempts, 10/20/40/80s exponential backoff, `rm -f taiga-gateway` between attempts) built on the theory that the failure was a transient Docker port-bind race. Round-5 verification (docs/BACKLOG.md "Round 5 regression verification", item 30 entry) reproduced the failure on the very first `POST /taiga/on` of a fresh install and ran the backoff to full exhaustion without recovering, then found the real cause: `taiga-gateway` is `nginx:1.19-alpine`, and its shipped config (`taigaio/taiga-docker`'s `taiga-gateway/taiga.conf`, mounted read-only into the container) does `proxy_pass http://taiga-front/;` with **no `resolver` directive and no `upstream` block** — nginx resolves that hostname once, at config-load/startup time, using the system resolver. If `taiga-front` isn't yet attached to the network (a real, ordinary startup-ordering race — Compose's `depends_on: [taiga-front, taiga-back, taiga-events]` on `taiga-gateway` in `taigaio/taiga-docker`'s own `docker-compose.yml` only waits for those containers to *start*, not to actually be ready to resolve/serve), nginx fails to start and the container exits immediately. The exited container retains its `127.0.0.1:9000` port reservation, so every remove+recreate collides on that same port, reproducing the identical failure — no amount of retrying `docker compose up`/`rm -f`/recreate can win, because each attempt recreates straight back into the same crash. This matches every symptom round 5 observed: `address already in use` with nothing actually bound to port 9000, the gateway ending up `Created` with `NetworkSettings.Networks == {}`, and `docker start` never reattaching it.

Confirmed via GitHub (`taigaio/taiga-docker` `stable` branch):
- `taiga-gateway` service: `image: nginx:1.19-alpine`, `volumes: [./taiga-gateway/taiga.conf:/etc/nginx/conf.d/default.conf, ...]`, `depends_on: [taiga-front, taiga-back, taiga-events]` (plain list form — Compose's default `service_started` condition, not `service_healthy`).
- `taiga-gateway/taiga.conf`: `proxy_pass http://taiga-front/;` (frontend), `http://taiga-back:8000/...` (API/admin), `http://taiga-events:8888/events` (websockets) — no `resolver`, no `upstream` block anywhere.
- `taiga-front` service: `image: taigaio/taiga-front:latest` — **no healthcheck defined** upstream (only `taiga-db` has one, which is what lets `taiga-back` already use `depends_on: {taiga-db: {condition: service_healthy}, ...}` in the *same* file — i.e. this exact long-form `depends_on`/`condition` syntax is already proven to work against this Compose/file combination, it's just not used for `taiga-front`→`taiga-gateway`).
- `taigaio/taiga-front`'s own Dockerfile: `FROM nginx:1.23-alpine`, plus `apk add bash` — no `curl`, but Alpine's base BusyBox ships a minimal `wget` without needing an explicit `apk add` (the standard reason `wget --spider` is nginx:alpine's idiomatic healthcheck-without-extra-install choice); needs hands-on confirmation on the real image (see "Open questions").

`install.sh` (`--with-taiga` block, lines ~363-451) clones `taiga-docker` **pinned** at whatever commit is first checked out (`git clone --branch stable --depth 1 ...`, never `git pull`'d on re-run, per its own comment at line 377) into `$TAIGA_DIR=/opt/ai-dev-switchboard-taiga`, then — separately, and this is the load-bearing precedent for this fix — **regenerates `$TAIGA_DIR/docker-compose.override.yml` deterministically on every install run** (lines 417-432) specifically so Compose's file-merge behavior can apply this project's own customizations (currently just the loopback-only port bind) "without ever conflicting with a future manual `git pull` in $TAIGA_DIR" (the comment's own words). This override file is the one piece of Taiga's stack this repo already treats as safely, repeatedly regeneratable and deliberately never touches `taiga.conf`/`docker-compose.yml` inside the checkout itself.

`scripts/taiga-up.sh`'s success check (lines 42-46) is a single `docker compose ps taiga-gateway --format '{{.State}}'` read immediately after `up -d` returns — round 5 saw the gateway report `Up` for under a second before crashing, so the script exited 0 while `taiga-status.sh` briefly reported `on` for an already-dead public entrypoint.

### Item 37 — `_check_config_permissions` misreads an ACL mask as loose group permissions
`scripts/taiga_push_spec.py:145-159` (`_check_config_permissions`) reads `stat.S_IMODE(os.stat(path).st_mode)` and warns + prints `chmod 600 <path>` whenever `mode & 0o077` is nonzero. `scripts/taiga-configure-push.sh` (lines 40-77) creates the config at mode 600, then (item 29) runs `setfacl -m u:${SVC_USER_NAME}:r "$CONFIG_FILE"` to grant `switchboard-svc` narrow read access. Setting that named-user ACL entry makes `setfacl` recompute the file's **ACL mask** to the union of the owning group's permission and every named user/group entry — here, `r`. Once a file carries an extended ACL, `stat`/`ls -l` report the **mask**, not the traditional group permission, in the group-class bits — so the file now reports mode `0640` even though its real *effective* group-class exposure is still nothing beyond the one explicit `switchboard-svc:r--` grant, and `other::` is still `---`. `_check_config_permissions` sees `0640 & 0o077 == 0o040`, calls it loose, and prints `Run: chmod 600 <path>` — which, if followed, runs `chmod 600` and recomputes the ACL mask down to `mask::---`, making the `switchboard-svc` grant's *effective* permission `---` (the ACL entry itself is still listed by `getfacl`, but is now meaningless) and silently reverting item 29 with no indication anything changed. `_check_config_permissions(args.config)` is called unconditionally at the top of `_run()` (`scripts/taiga_push_spec.py:322-323`), so this fires on every `board_read`/`board_write`/CLI push.

Existing tests: `tests/test_taiga_push.py::ConfigPermissionsTests` (lines 101-126) — `test_mode_600_prints_no_warning`, `test_looser_mode_prints_a_loud_warning_but_does_not_raise`, `test_missing_file_is_silently_ignored_here`. These only exercise plain `os.chmod` modes, never an ACL'd file, so the current suite doesn't catch this.

`taiga_push_spec.py` is explicitly stdlib-only (its own module docstring: "Stdlib-only ... no python-dotenv, no requests"), and `subprocess` is not currently imported. `getfacl`/`setfacl` (the `acl` package) is already an established, best-effort dependency of this exact feature area (`taiga-configure-push.sh` already checks `command -v setfacl` and warns rather than requiring it) — shelling out to `getfacl` here is consistent with that precedent rather than a new one, and avoids adding a third-party ACL binding (`pylibacl`) that would break the stdlib-only convention.

### Item 38 — `/status` never resolves a terminal run to a terminal-looking state; `run.json`'s `"project"` field
`app/app.py:5869-5874` (inside the `/status` handler's per-instance loop) computes a coarse `team_status` for the frontend:
```python
run = teams.latest_run_for_project(n)
team_status = ("idle" if run is None else
              {"running": "running", "blocked_ask_user": "blocked",
               "blocked_board_write": "blocked",
               "escalated_max_rounds": "blocked", "finished": "finished",
               "error": "error", "stopped": "idle"}.get(run["status"], "idle"))
```
This bucketing is deliberate for the frontend's rendering purposes (the adjacent comment at lines 5919-5926 explains `escalated_max_rounds` is intentionally grouped under the coarser `"blocked"` bucket, distinguished only by `waiting_on_you`/`escalation_kind`, both already correctly `False`/`None` for it). But nothing in the `/status` response gives a caller an explicit, unambiguous **terminal** signal — a poller has to infer "is this actually done" from `status === 'blocked' && waiting_on_you === false`, which is exactly what round 5's own verification poller (and presumably the reported real-world poller) failed to do, per docs/BACKLOG.md item 38: a run that had already reached `escalated_max_rounds` — which `app/teams.py:4089`'s `stop_team()` (`if state["status"] not in ("finished", "escalated_max_rounds", "error", "stopped"): state["status"] = "stopped"`, item 35's fix) already correctly treats as terminal and leaves alone — was still reported `blocked` by `/status` 17+ minutes later. `teams.py` has this exact 4-status terminal tuple duplicated verbatim as an inline literal in three places (`stop_team()` line 4089, `sweep_dead_teams()` line 4334, `interject()` line 4506) but `/status`'s own mapping in `app.py` is a *fourth*, independently-written mapping that doesn't reuse it and doesn't expose the distinction at all.

Separately, item 38 also reports that the same run's `run.json` carried `"project": null`. Investigated: `app/teams.py`'s `_new_state()` (lines 2793-2821) only ever sets a `"project_name"` key (`"project_name": project_name`, defaulting to `None` only for a bare `team-start` CLI run that skipped `team-launch` — per its own comment, intentional for that path), never a key literally named `"project"`. `_persist()` (lines 2824-2838) writes exactly the `state` dict via `json.dump`, adding no extra keys. A grep of the entire repo (`app/`, `scripts/`, `tests/`) and of git history for a literal `"project":` key assignment touching team state found none — the only other `"project"` key in the codebase is `scripts/taiga_push_spec.py`'s unrelated Taiga API request body (`_create_userstory`, line 196), a completely separate subsystem. It's also inconsistent with `latest_run_for_project()` (lines 4223-4260), which filters strictly on `state.get("project_name") != project_name` and would have **skipped** (never associated) any run whose `project_name` was `None` — yet item 38 says this run *was* correctly associated with `testproj` everywhere else, including presumably via `/status`. This means the `"project": null` observation cannot currently be explained by anything in the code as read; see "Open questions" — it needs a fresh hands-on repro against current code before deciding whether it's a live bug, a terminology slip in the original report (meaning `project_name`, which then would have to have been *not* null for this run given the above), or leftover data from an older schema.

## Proposed approach

### Item 30
**Architecture decision (flagged per the requested explicit call-out): gate `taiga-gateway`'s startup on `taiga-front` health via `docker-compose.override.yml`, not an nginx `resolver` directive patch.** Both options from the backlog's "Revised shape of the fix" were evaluated:
- *Rejected*: patching `taiga.conf` (adding a `resolver 127.0.0.11 valid=10s;` plus rewriting `proxy_pass http://taiga-front/;` to use a resolver-backed variable so nginx re-resolves per-request instead of failing at startup). This file lives inside the git-cloned, pinned-commit `taigaio/taiga-docker` checkout at `$TAIGA_DIR` — a third-party release train this repo does not own and, per `install.sh`'s own documented intent, deliberately never mutates in place (only the separately-regenerated `docker-compose.override.yml` is treated as safe to touch repeatedly). Patching `taiga.conf` directly would mean either hand-editing a file inside a git checkout on every install run (fragile — silently no-ops or breaks if upstream ever changes that file's content) or forking it entirely (diverges from Taiga's own release train, defeating the point of tracking `stable`).
- *Adopted*: extend `$TAIGA_DIR/docker-compose.override.yml` — the file `install.sh` already regenerates deterministically every run specifically so it "never conflicts with a future manual `git pull` in $TAIGA_DIR" — to (a) give `taiga-front` a healthcheck (upstream doesn't define one) and (b) change `taiga-gateway`'s `depends_on` entry for `taiga-front` from the default `service_started` condition to `service_healthy`. This uses the exact `depends_on: {service: {condition: ...}}` long-form syntax `taigaio/taiga-docker`'s own `docker-compose.yml` already uses for `taiga-back`→`taiga-db` in the same file (proven compatible with this stack), and Compose merges `depends_on` across `-f` files by service key — the override's `taiga-gateway.depends_on` mapping takes precedence per-key over the base file's short-form list (normalized internally to the same shape), so listing all three existing dependencies (`taiga-front`, `taiga-back`, `taiga-events`) in the override is both correct and self-documenting even though only `taiga-front` gets an upgraded condition. Compose will not start `taiga-gateway` until `taiga-front` is genuinely marked healthy — which only happens once it's actually attached to the network and serving — eliminating the startup-ordering race nginx currently loses to, entirely within the file this repo already owns and safely regenerates.

Add to the heredoc at `install.sh` lines 427-432 (single-quoted heredoc stays single-quoted — `${TAIGA_PORT}` must remain literal for Compose's own substitution, unchanged from today):
```yaml
services:
  taiga-front:
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost/ || exit 1"]
      interval: 2s
      timeout: 3s
      retries: 30
      start_period: 5s
  taiga-gateway:
    ports:
      - "127.0.0.1:${TAIGA_PORT}:80"
    depends_on:
      taiga-front:
        condition: service_healthy
      taiga-back:
        condition: service_started
      taiga-events:
        condition: service_started
```
(The `wget --spider` test command needs hands-on confirmation against the real `taigaio/taiga-front:latest` image — see "Open questions" for the fallback if it's missing; the interval/timeout/retries/start_period values are reasonable starting defaults, not measured against the real host's actual `taiga-front` boot time, and should be tuned during hands-on verification if needed.)

Separately, strengthen `scripts/taiga-up.sh`'s success check with a settle window (docs/BACKLOG.md's explicit ask, independent of the root-cause fix above — defense in depth against *any* remaining transient early-exit, not just this one). Add a `TAIGA_UP_SETTLE_SECONDS="${TAIGA_UP_SETTLE_SECONDS:-5}"` env var (same override-with-env-var convention `TAIGA_UP_MAX_ATTEMPTS`/`TAIGA_UP_RETRY_BACKOFF_SECONDS` already use). After the loop's existing initial `state = ... running` check succeeds, `sleep "$TAIGA_UP_SETTLE_SECONDS"` and re-run the same `docker compose ps taiga-gateway --format '{{.State}}'` check; only `exit 0` if it's *still* `running` at that second read. If it isn't, fall through into the existing "didn't come up cleanly" branch (message, `rm -f`, backoff, loop) rather than a separate code path — this attempt is simply treated as failed and consumes one of `TAIGA_UP_MAX_ATTEMPTS` like any other.

Because the settle-window sleeps add to the script's total worst-case runtime, re-check the arithmetic behind `app.py`'s `TAIGA_UP_SCRIPT` timeout (currently 180s, covering 5 attempts × 10/20/40/80s backoff = 150s) against the new worst case (+ up to `TAIGA_UP_MAX_ATTEMPTS` × `TAIGA_UP_SETTLE_SECONDS` = +25s at defaults = 175s) and bump the timeout (and `tests/test_taiga.py::test_up_uses_even_longer_timeout_to_cover_its_own_retry_loop`) if it no longer leaves comfortable margin.

### Item 37
Add a small ACL-aware helper to `scripts/taiga_push_spec.py` (add `import subprocess` to the existing stdlib-only import block) and change `_check_config_permissions` to use it:

```python
def _parse_acl_other_bits(getfacl_output: str) -> dict | None:
    """Parses `getfacl -p <path>` output. Returns None if the file has only
    a minimal ACL (no `mask::` line -- st_mode's group/other bits are then
    accurate and the plain check below applies unchanged). Returns
    {"other": 0-7 int, "other_str": "r--"} from the `other::` line when an
    extended ACL is present -- the only entry that reflects genuine
    world-exposure once a mask entry exists (item 37: the group-class bits
    stat() reports become the ACL mask, not real group permissions, once
    any named user/group ACL entry -- e.g. item 29's switchboard-svc:r --
    is present, so they must never drive this warning)."""
    lines = getfacl_output.splitlines()
    if not any(l.startswith("mask::") for l in lines):
        return None
    other_line = next((l for l in lines if l.startswith("other::")), None)
    if other_line is None:
        return None
    other_str = other_line.split("::", 1)[1].strip()
    bits = (4 if "r" in other_str else 0) | (2 if "w" in other_str else 0) | (1 if "x" in other_str else 0)
    return {"other": bits, "other_str": other_str}


def _read_getfacl(path: str) -> str | None:
    """The one seam this ACL check monkeypatches in tests (mirrors
    _taiga_request's own "one seam per shelled-out call" convention).
    Returns None (not a raised exception) if `getfacl` isn't installed or
    the call otherwise fails -- best-effort, same as taiga-configure-
    push.sh's own `command -v setfacl` fallback -- so a host without the
    'acl' package degrades to the plain st_mode check below, not a crash."""
    try:
        result = subprocess.run(["getfacl", "-p", path], capture_output=True,
                                 text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _check_config_permissions(path: str) -> None:
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return
    raw = _read_getfacl(path)
    acl = _parse_acl_other_bits(raw) if raw is not None else None
    if acl is not None:
        if acl["other"] != 0:
            print(
                f"WARNING: {path} is readable/writable by 'other' via its ACL "
                f"(other::{acl['other_str']}) — it holds a live Taiga password. "
                f"Run: setfacl -m o::--- {path}  (do NOT run chmod -- with an ACL "
                f"present, chmod recomputes the ACL mask and can silently break "
                f"a legitimate named grant, e.g. a service account's read access).",
                file=sys.stderr,
            )
        return  # ACL present and other:: clean -- narrowly-ACL'd, not loose
    if mode & 0o077:
        print(  # unchanged from today
            f"WARNING: {path} is readable by group/other (mode {oct(mode)}) — it holds a "
            f"live Taiga password. Run: chmod 600 {path}",
            file=sys.stderr,
        )
```
Scope is deliberately narrow: the only new signal is `other::` from `getfacl`, which is the one bit that's still a genuine, unambiguous leak regardless of what named ACL entries exist. This does not attempt to validate that a file's named-entry grants are the "correct"/expected ones (e.g. flag an unexpected `user:someone-else:r` entry) — out of scope per "Non-goals".

### Item 38
1. In `app/teams.py`, add a module-level constant near the other `TEAM_*` constants (e.g. after `TEAM_SESSION_STALE_TTL_SECONDS`, line 169):
   ```python
   # The 4 terminal run statuses -- a run in one of these is done, one way
   # or another, and nothing further will ever drive it. Single source of
   # truth for stop_team()/sweep_dead_teams()/interject()'s existing inline
   # checks (item 38: previously duplicated verbatim in three places, and
   # NOT what /status's own separately-written team_status mapping used,
   # which is the root cause of escalated_max_rounds runs reporting
   # "blocked" forever with no terminal signal at all).
   TEAM_TERMINAL_STATUSES = ("finished", "escalated_max_rounds", "error", "stopped")
   ```
   Replace the three inline literal tuples (`stop_team()` line 4089, `sweep_dead_teams()` line 4334, `interject()` line 4506) with references to `TEAM_TERMINAL_STATUSES` — pure refactor, no behavior change at those three call sites.
2. In `app/app.py`'s `/status` handler, add an additive `"terminal"` field to the `inst["team"]` dict (alongside `waiting_on_you`/`escalation_kind`, same "additive only" precedent already used for both — lines 5939-5941):
   ```python
   "terminal": run is not None and run["status"] in teams.TEAM_TERMINAL_STATUSES,
   ```
   `team_status`, `waiting_on_you`, and `escalation_kind` are all unchanged — this is a new field only, so no existing frontend rendering logic (the `team.status === 'blocked'` branches noted in "Background") needs to change. While implementing, check whether any existing client-side JS polls `/status` waiting for a run to finish by inferring completion from `status`/`waiting_on_you` rather than a dedicated signal — if so, point it at the new `terminal` field instead; this pass found no such polling loop in `app.py`'s JS but flags it for the developer's own check since it wasn't exhaustively verified.
3. For the `"project": null` observation: reproduce first, don't fix blind. Launch a real team run (via the normal `/team/launch` path, matching how the round-5 run was almost certainly started) through to `escalated_max_rounds` (or grab an existing terminal run.json from a prior verification pass if one is still on a test host) and inspect the raw `run.json` with current code. If no top-level `"project"` key appears (expected, per the "Background" archaeology above — only `"project_name"` is ever written), record that finding in `docs/implementation.md` and treat it as resolved-by-explanation (most likely the original report meant `project_name`, or was looking at stale data from before some earlier schema/rename that predates this round). If a literal `"project": null` key genuinely does reproduce, root-cause it from scratch at that point (do not guess the fix now) and note it as a real second defect, separate from the `/status` staleness fix above.

## Affected areas
- **Item 30**: `install.sh` (`docker-compose.override.yml` heredoc, `--with-taiga` block), `scripts/taiga-up.sh` (settle-window recheck), `app/app.py` (`TAIGA_UP_SCRIPT` timeout, only if the new worst-case arithmetic requires it), `tests/test_taiga_up_retry.py` (new settle-window test), `tests/test_taiga.py` (timeout test, only if changed). No changes inside the `taigaio/taiga-docker` checkout itself.
- **Item 37**: `scripts/taiga_push_spec.py` (`_check_config_permissions` + two new helpers, `import subprocess`), `tests/test_taiga_push.py` (`ConfigPermissionsTests`, extended with ACL-aware cases).
- **Item 38**: `app/teams.py` (`TEAM_TERMINAL_STATUSES` constant + 3 call-site refactors), `app/app.py` (`/status` handler, `terminal` field), `tests/test_team_routes.py` (new parametrized test mirroring the existing `test_waiting_on_you_true_only_for_blocked_ask_user_never_for_escalated_max_rounds`/`test_escalation_kind_field` style at lines 964-1011).

## Edge cases
- **Item 30**: `taiga-front` never becomes healthy at all (e.g. a genuinely broken image) — `docker compose up -d` for `taiga-gateway` should fail/skip starting it (Compose's own `service_healthy` semantics), which the existing retry loop's post-`up -d` state check already handles by treating "not running" as a failed attempt; verify hands-on that this doesn't produce a different, unhandled failure mode (e.g. a nonzero `up -d` exit the script doesn't currently branch on — it doesn't need to, since it inspects gateway state afterward regardless, but confirm this holds). `taiga-front` reports healthy but then dies immediately after (a first-cousin of the original bug, one layer removed) — the settle-window recheck in `taiga-up.sh` is the backstop for this case, not the healthcheck. Repeated toggle on/off/on cycles (matches round 5's own repeated-attempts verification methodology) must not regress once the fix is in place.
- **Item 37**: `getfacl` not installed (best-effort fallback to the plain `st_mode` check, matching `taiga-configure-push.sh`'s own precedent) — must not crash. A file with an extended ACL where `other::` is *not* clean (a genuinely loose ACL'd file) must still warn, just with ACL-safe remediation text, not `chmod`. A file with no ACL at all (today's un-migrated case, or a fresh install before `taiga-configure-push.sh` ever runs `setfacl`) must behave exactly as before (unchanged `mode & 0o077` path). Missing file — unchanged (`_check_config_permissions` returns silently; `_load_config` is what raises for that).
- **Item 38**: `run is None` (no run ever launched for this project) — `terminal` must be `False`, not raise. A run mid-flight (`status == "running"`) — `terminal` is `False`. Every status value in `TEAM_TERMINAL_STATUSES` reachable via `/status`, not just `escalated_max_rounds` — `finished`/`error`/`stopped` must also report `terminal: True` for completeness/consistency even though `escalated_max_rounds` is the one item 38 specifically reported broken (the other three already "look" terminal today via their coarse `status` values, so this is about consistency of the new field, not fixing a second observed symptom).

## Acceptance criteria

### Item 30
- [ ] `docker-compose.override.yml` generated by `install.sh` includes the `taiga-front` healthcheck and `taiga-gateway`'s upgraded `depends_on` shown above; no file inside the `taiga-docker` git checkout itself is modified.
- [ ] Hands-on, on a fresh (or equivalently reset) Proxmox `--with-taiga` install: `POST /taiga/on` succeeds on the first `taiga-up.sh` attempt (no `rm -f`/backoff needed) where it previously crash-looped to full exhaustion.
- [ ] `docker compose ps taiga-gateway` reports `State: running` and a non-empty `NetworkSettings.Networks` after toggle-on; `docker compose logs taiga-gateway` shows no "host not found in upstream" crash.
- [ ] Repeated toggle off/on cycles (at least 3, matching round-5's own methodology) all succeed cleanly.
- [ ] `taiga-up.sh`'s settle-window recheck: a gateway that reports `running` on the first check but dies before the settle window elapses is treated as a failed attempt (consumes an attempt, triggers `rm -f` + backoff), verified both by a new/updated `tests/test_taiga_up_retry.py` case (using the existing stubbed-`docker`-shell-function technique) and, if reproducible, hands-on.
- [ ] `app.py`'s `TAIGA_UP_SCRIPT` timeout and its test are re-verified against the new worst-case arithmetic (backoff + settle-window sleeps × max attempts) and bumped if the existing 180s no longer leaves comfortable margin.
- [ ] Full existing test suite (`python3 -m unittest discover -s tests`) passes.

### Item 37
- [ ] Given a config file at mode 600 with an item-29-style `setfacl -m u:switchboard-svc:r` grant (mask now `r--`, `stat` mode `0640`), `_check_config_permissions` prints **no** warning.
- [ ] Given the same ACL'd file, the printed output never contains the string `chmod 600` for that file.
- [ ] Given a config file with an extended ACL where `other::` is *not* `---` (a genuinely loose ACL'd file), `_check_config_permissions` prints a warning whose remediation is `setfacl`-based, not `chmod`.
- [ ] Given a config file with no ACL at all (plain `st_mode`), behavior is unchanged from today: mode 600 → no warning; mode 644 → warning with `chmod 600` remediation (existing `ConfigPermissionsTests` cases keep passing unmodified).
- [ ] Given `getfacl` is unavailable (simulated in a test), falls back to the plain `st_mode` check without raising.
- [ ] Hands-on: on a real host, run `taiga-configure-push.sh` (which sets up the item-29 ACL), then trigger a `board_read`/`board_write` (or run `taiga_push_spec.py` directly) and confirm no `chmod`-collapsing warning appears, and that following any warning that *does* appear (in the genuinely-loose case) does not break the `switchboard-svc` grant.

### Item 38
- [ ] Given `run["status"] == "escalated_max_rounds"`, `/status`'s `team.terminal` is `True` (with `team.status` staying `"blocked"`, `waiting_on_you` staying `False`, unchanged from today).
- [ ] Given `run["status"] in ("blocked_ask_user", "blocked_board_write")`, `team.terminal` is `False`.
- [ ] Given `run["status"] in ("finished", "error", "stopped")`, `team.terminal` is `True`.
- [ ] Given `run["status"] == "running"`, `team.terminal` is `False`.
- [ ] Given no run at all for a project, `team.terminal` is `False`.
- [ ] `app/teams.py` has a single `TEAM_TERMINAL_STATUSES` constant; `stop_team()`, `sweep_dead_teams()`, `interject()`, and `/status`'s new `terminal` computation all reference it (grep confirms no remaining duplicate inline literal tuple).
- [ ] Hands-on: drive a real run to `escalated_max_rounds` (same repro item 35/round-5 used), poll `GET /status` repeatedly, and confirm `team.terminal` flips to `True` — a poller checking `terminal === true` instead of the coarse `status` field correctly detects completion instead of hanging.
- [ ] The `"project": null` observation is investigated per "Proposed approach" §3 and the finding (reproduces vs. doesn't, and if it does, the root cause) is documented in `docs/implementation.md`.
- [ ] Full existing test suite passes, including the existing `test_waiting_on_you_true_only_for_blocked_ask_user_never_for_escalated_max_rounds`/`test_escalation_kind_field` tests unchanged.

## Open questions
- **Item 30 (the real architectural call)**: decided above — health-gate via `docker-compose.override.yml` over an nginx `resolver` patch inside the third-party `taiga-docker` checkout, on the grounds that only the former is a file this repo already owns and safely regenerates every install run. Flagging explicitly per this round's request rather than treating it as settled without comment; open to being overridden if there's a reason to prefer the resolver-directive approach despite the divergence-from-upstream concern.
- **Item 30**: `wget --spider` as the `taiga-front` healthcheck test command is a strong guess (Alpine BusyBox `wget` is present without an explicit `apk add`, and the upstream Dockerfile doesn't remove it) but not hands-on confirmed against the real `taigaio/taiga-front:latest` image — verify with `docker exec <container> wget --version` (or equivalent) during implementation; if missing, fall back to a bash `/dev/tcp` check (`bash` is confirmed present via the Dockerfile's `apk add bash`), e.g. `CMD-SHELL exec 3<>/dev/tcp/localhost/80 && echo -e 'GET / HTTP/1.0\r\n\r\n' >&3 && head -1 <&3 | grep -q '^HTTP/'`.
- **Item 30**: the healthcheck's `interval`/`timeout`/`retries`/`start_period` and `TAIGA_UP_SETTLE_SECONDS` are reasonable starting defaults, not measured against real observed `taiga-front` boot time on the actual host — tune based on hands-on timing during verification (round 5's own evidence — "Up for under a second before crashing" — suggests the settle window mainly needs to beat sub-second failures, so 5s has real margin, but confirm rather than assume).
- **Item 38**: the `"project": null` observation could not be explained by any current code path (see "Background" archaeology) — genuinely open whether it reproduces at all under current code, is a terminology slip in the original report (meaning `project_name`, which per `latest_run_for_project()`'s filtering would then have to be non-null for this run to have been findable in the first place, which is itself odd), or is leftover data from before some earlier schema change. Proceeding under the assumption that it needs a fresh hands-on repro before any code change is justified, per "Proposed approach" §3 — do not guess a fix for a write path that doesn't appear to exist.

## Risk / rollback notes
- **Item 30**: the `docker-compose.override.yml` change is regenerated fresh by `install.sh` every run and only affects `--with-taiga` installs; rollback is reverting the heredoc content (or simply not re-running `install.sh` with the new version) — no migration/state to undo. The `taiga-up.sh` settle-window change adds latency (up to `TAIGA_UP_SETTLE_SECONDS` per attempt) to every successful toggle-on, not just failures — acceptable given item 30's severity, but worth noting if toggle-on latency becomes a complaint. If the healthcheck-gate approach turns out not to fully resolve the crash-loop on hands-on verification, the existing retry/backoff loop and `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` escape hatch remain as fallbacks — nothing is removed.
- **Item 37**: purely a diagnostic/warning-text change with a `getfacl`-unavailable fallback to today's exact behavior — low risk. Worst case if the ACL detection has a bug: reverts to over-warning (annoying but safe) rather than under-warning (a real file left loose without notice) as long as the fallback path is exercised whenever parsing is uncertain; the implementation above only suppresses the warning when it can positively confirm `other::` is clean, not merely when ACL parsing fails.
- **Item 38**: additive-only field on `/status`; no rollback concerns beyond reverting the added field and the `TEAM_TERMINAL_STATUSES` refactor (which is behavior-preserving by construction — same 4 literal values, just centralized).
