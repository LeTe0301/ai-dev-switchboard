# Spec: app/teams.py + app/taiga_board.py + app/app.py fixes from Proxmox E2E test round 2 (items 28, 29, 33)

## Summary
Three more bugs from the same Proxmox E2E test (`docs/BACKLOG.md` items 28,
29, 33). Item 28 is the second of the two bugs that together made
multi-agent teams completely non-functional on a fresh install (item 27,
fixed in PR #27, closes the first blocker; this closes the second — with
both fixed, teams actually work). Item 29 breaks item 7's `board_read`/
`board_write` tools identically on every fresh install, even after
following the documented setup exactly. Item 33 is a one-line cosmetic
error-message fix. All three are fully diagnosed with verified-locally-
working fixes already established by the E2E tester.

## Orchestrator note
No product-manager/ux-designer dispatch — same "fully-diagnosed follow-up"
precedent as round 1 (PR #27). All three fixes below have exact before/
after code.

---

## Fix 1 — Item 28: `rundir` permission wall blocks every worktree creation and every headless engine turn

**Where**: `app/teams.py:1095` (`_run_headless_session()`) and
`app/teams.py:3462` (`_run_run_user_command()`).

**Problem**: both functions create `rundir` as `SVC_USER` via
`os.makedirs(rundir, exist_ok=True); os.chmod(rundir, 0o711)`, but the
actual command inside it is dispatched via `sudo -u RUN_USER tmux
new-session ...` — i.e. it runs as a *different* user, which has no write
bit on `0o711` (owner rwx, group/other read+execute only, no write for
anyone but the owner) at all. The command's own redirect-and-background
line (`... >out 2>err & echo $! >pid; wait $!; echo $? >rc`) fails at its
very first redirect, before even backgrounding — nothing is ever written,
so the generic "vanished with no rc" fallback fires, looking exactly like
a fast-command race but actually a 100%-reproducible permission wall.

**Fix**: change both `os.chmod(rundir, 0o711)` calls to
`os.chmod(rundir, 0o733)` (owner rwx, group -wx, other -wx — i.e. everyone
gets write+execute, matching what `RUN_USER` actually needs to create
files in this directory). Verified by the E2E tester: this exact change,
applied at both sites, makes team-start, worktree creation, and headless
delegation all work correctly. Files written into `rundir` by `RUN_USER`
inherit a normal `022`-umask mode (world-readable), so `SVC_USER` reading
them back afterward is unaffected — only the directory's own write bit for
"other" was ever the problem.

```python
# app/teams.py:1095, inside _run_headless_session()
os.chmod(rundir, 0o733)   # was 0o711

# app/teams.py:3462, inside _run_run_user_command()
os.chmod(rundir, 0o733)   # was 0o711
```

**Also worth adding (same fix, diagnostic improvement, not required for
correctness but directly requested by the E2E tester)**: the
`subprocess.run(TMUX + ["new-session", ...])` calls at both these sites
(`app/teams.py:1139` inside `_run_headless_session()`, and
`app/teams.py:3473` inside `_run_run_user_command()`) don't capture or
check their own return code/stderr at all — this is exactly what made
diagnosing item 28 slow (the generic "vanished" message is
indistinguishable from a dozen other real causes). Add
`capture_output=True, text=True` to both calls, and if the returncode is
non-zero, fold `result.stderr` into the existing failure-path error
message at each site instead of silently proceeding to the vanished-
session fallback. Read each function's surrounding code first to thread
this through correctly — do not just add the kwarg without using the
result, and do not change either function's existing return contract
(`{"ok": bool, ...}` shape) beyond making the error message more specific
when this particular failure mode occurs.

**Acceptance**: after this fix (and item 27's from PR #27), a real
`POST /projects/<name>/team/start` succeeds, creates real worktrees, and a
real headless delegate call to a teammate completes — not just "doesn't
throw," an actual file edit lands on disk (matching the E2E report's own
positive-result confirmation).

---

## Fix 2 — Item 29: `board_read`/`board_write` always report "Taiga isn't configured" — `~` expands per-process, not per-install

**Where**: `app/taiga_board.py:33` (`DEFAULT_CONFIG_PATH`).

**Problem**: both `scripts/taiga_push_spec.py` (invoked as `RUN_USER` via
the CLI) and `app/taiga_board.py` (invoked as `SVC_USER`, from inside the
lead loop running as part of `ai-dev-switchboard.service`) independently
compute:
```python
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/ai-dev-switchboard/taiga-push.env")
```
`~` expands relative to *whichever user's process evaluates it* —
`/home/dev/...` for the CLI script, `/home/switchboard-svc/...` for the
board tools. `scripts/taiga-configure-push.sh`'s own documented usage
("Run once, by RUN_USER") writes the file to `/home/dev/...`, but
`taiga_board.py` reads from `/home/switchboard-svc/...` — two entirely
separate files. Every team lead that ever calls `board_read`/
`board_write` on a fresh install reports "not configured," even after
following the docs exactly.

**Fix**: resolve the path relative to `RUN_USER`'s home explicitly in
`taiga_board.py`, matching where the setup script and its own docs
already point, instead of each side independently calling
`os.path.expanduser("~/...")` in its own process context.
`taiga_board.py` needs to know `RUN_USER`'s value — the canonical
resolution is `app/app.py:69`: `RUN_USER = os.environ.get("RUN_USER",
"dev")`. **Do not import this from `app.py`** — `taiga_board.py` is
imported by `teams.py` (`import taiga_board`, `app/teams.py:55`), which is
in turn imported by `app.py` partway through its own module body
(`import teams`, confirmed as the exact line that crashed in item 22's
bug report); `taiga_board.py` importing `app` back would create a real
circular import (`app → teams → taiga_board → app`) that breaks at module
load time, not just a style nit. `taiga_board.py`'s own docstring already
states its design goal of not importing anything privileged of its own —
keep that property. Instead, replicate the same `os.environ.get(...)`
read independently, matching `app.py:69`'s exact default value:
```python
RUN_USER = os.environ.get("RUN_USER", "dev")
DEFAULT_CONFIG_PATH = f"/home/{RUN_USER}/.config/ai-dev-switchboard/taiga-push.env"
```

**Non-goal**: do not change `scripts/taiga_push_spec.py`'s own
`DEFAULT_CONFIG_PATH` (it's correctly `~`-relative already, since it
always runs as `RUN_USER` via the CLI — this bug is specific to
`taiga_board.py` being invoked from a different user context). Do not
attempt to fix the underlying read-permission question (the E2E tester's
own report notes `taiga-configure-push.sh` writes 600-mode/`RUN_USER`-
owned, so `SVC_USER` reading the *correct* path still needs either a
narrowly-scoped read grant or `install.sh` creating the file's directory
with shared permissions up front) — that's a separate, not-yet-fully-
specified follow-up the E2E report itself flags as needing a decision,
not something to guess at in this cycle. This fix only corrects *which
path* is computed; if `SVC_USER` still can't read a `RUN_USER`-600-owned
file at that corrected path, that's the next thing to fix, tracked
separately.

**Acceptance**: `python3 -c "import sys; sys.path.insert(0, 'app'); import
taiga_board; print(taiga_board.DEFAULT_CONFIG_PATH)"` (with `RUN_USER=dev`
in the environment) prints `/home/dev/.config/ai-dev-switchboard/taiga-push.env`
— matching exactly where `taiga-configure-push.sh`'s own documented usage
writes the file.

---

## Fix 3 — Item 33: `/team/interject`'s error message names the wrong field

**Where**: `app/app.py:6472-6476`.

**Current**:
```python
text = (body.get("text") or "").strip()
if not text or len(text) > teams.TEAM_INTERJECT_MAX_CHARS:
    return self._json(
        {"error": f"message must be non-empty and at most "
                  f"{teams.TEAM_INTERJECT_MAX_CHARS} characters"}, 400)
```
The route reads `body.get("text")` (correct — this is genuinely the field
name the frontend sends and the field name documented in `docs/spec.md`)
but the error string says "message," not "text" — misleading anyone
constructing the request by hand or from the error text alone (the E2E
tester's own first, reasonable guess based on the error string and the
feature's name — "interject a message" — was `{"message": "..."}`, which
the route silently treats as absent and returns this same misleading
error).

**Fix**: correct the error string to name the real field:
```python
text = (body.get("text") or "").strip()
if not text or len(text) > teams.TEAM_INTERJECT_MAX_CHARS:
    return self._json(
        {"error": f"text must be non-empty and at most "
                  f"{teams.TEAM_INTERJECT_MAX_CHARS} characters"}, 400)
```
Check `tests/test_team_routes.py` (or wherever this route's tests live)
for any existing assertion on the literal old error string and update it
to match.

## Affected areas
`app/teams.py` (fix 1), `app/taiga_board.py` (fix 2), `app/app.py` (fix 3).
No frontend/JS changes needed for any of these three.

## Risk / rollback notes
Fix 1 is the highest-value fix in this cycle (unblocks the entire
multi-agent-teams feature) but is a narrow, well-understood permission-bit
change with a verified-working fix already established — low risk. Fix 2
is a path-computation change with no behavior change for any already-
working case (the config was never being found correctly before this fix,
so there's no working case to regress). Fix 3 is a one-line string
change. Plain `git revert` on `app/teams.py`/`app/taiga_board.py`/
`app/app.py` if anything regresses.
