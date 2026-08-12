# Spec: VS Code (code-server) dark mode by default

## Summary
Seed a shared `settings.json` with `"workbench.colorTheme": "Default Dark+"` into `RUN_USER`'s code-server user-data directory during `install.sh --with-code-server`, so code-server opens in dark mode the first time anyone uses it, without ever touching a file the user has already customized.

## Goals
- A fresh install run with `--with-code-server` (or an existing install re-run later to add that flag) leaves `RUN_USER`'s code-server instance defaulting to `Default Dark+` instead of code-server's stock light theme, with zero manual steps.
- Never overwrite `settings.json` if it already exists, under any circumstance (fresh customization, re-run, flag added after the fact) — idempotent across arbitrarily many `install.sh` re-runs.

## Non-goals
- Per-project theming or a UI theme picker. `_code_start()` (`app/app.py` line ~484) spawns `code-server` with no `--user-data-dir` flag, so every project under one `RUN_USER` already shares one global code-server user-data directory — this seeds that one shared file, not per-project state.
- A configurable theme knob (e.g. a new `CODE_SERVER_THEME` variable in `config/switchboard.env.example`). Hardcode `Default Dark+` per the backlog's own suggestion; making it user-choosable is a separate future item if ever requested.
- Any change to `app/app.py` / `_code_start()`. See "Proposed approach" for why this is install-time-only, not lazy-on-launch.
- Seeding any settings beyond the one theme key (no font size, no extension list, etc.) — stay minimal.
- A runtime gate in `app.py` on whether `--with-code-server` was ever passed (there isn't one today — `code_running`/`_code_start` just try to spawn `CODE_SERVER_BIN` unconditionally whenever the UI's VS Code toggle is hit, regardless of how code-server got installed). Out of scope for this item; unchanged either way.

## Background / current state
- `_code_start(name, workdir)` in `app/app.py` (line 484) spawns code-server per project as `sudo -u RUN_USER CODE_SERVER_BIN --bind-addr 127.0.0.1:<port> --auth none <workdir>` — no `--user-data-dir`. Confirmed by reading the function directly: this means code-server falls back to its own default user-data directory, which (per code-server's docs) is `~/.local/share/code-server` (or `$XDG_DATA_HOME/code-server` if that's set — it isn't anywhere in this repo). Since `_code_start` is invoked via `sudo -u RUN_USER`, and plain `sudo -u <user> cmd` resets `HOME` to the target user's home by default (Debian/Ubuntu default `env_reset` sudoers policy), that default resolves to `/home/$RUN_USER/.local/share/code-server` — one directory shared by every project under that `RUN_USER`, confirming the prior triage's read.
- code-server (following VS Code's own layout) stores user settings at `<user-data-dir>/User/settings.json`, i.e. `/home/$RUN_USER/.local/share/code-server/User/settings.json`. This file does not exist until code-server's first-ever run for that `RUN_USER` (code-server creates it lazily, empty of any theme override, at which point the theme falls back to VS Code's built-in default, `Default Dark+`... except code-server's actual shipped default is light — see next paragraph).
- Verified via web search: VS Code/code-server's built-in dark theme's exact settings id is `"Default Dark+"` (not e.g. `"Dark+ (default dark)"` or a numbered variant) — confirms the backlog's own guess was correct.
- `install.sh --with-code-server` today: installs the `code-server` binary via the upstream install script *if not already present* (line 118-121) — **before** `RUN_USER` is prompted for and created (lines 124-128). This ordering means `/home/$RUN_USER` does not exist yet at the point the binary-install block runs, so seeding cannot be bolted onto that existing block as-is; it needs its own block placed after user creation.
- `install.sh`'s header comment already states the whole script is "safe to re-run: every step here either checks for existing state first or overwrites deterministically-generated files ... never clobbers ... values that are already set" — the existing pattern for this (e.g. line 145, `[ -f "$ENV_FILE" ] || cp ...`) is exactly the idempotency shape this feature needs.
- **Permissions constraint that settles the install-time-vs-lazy question:** `app.py` runs as `SVC_USER`, an unprivileged service account. The sudoers rule installed at `/etc/sudoers.d/ai-dev-switchboard` (install.sh line 203-208) scopes `SVC_USER`'s `sudo -u RUN_USER` rights to exactly three binaries — `/usr/bin/tmux`, `/usr/local/bin/ttyd`, `/usr/local/bin/code-server` — nothing else. `SVC_USER` has no filesystem write access into `RUN_USER`'s home directory outside of invoking one of those three commands, and none of them offer a way to write an arbitrary file. So `_code_start()` in `app.py` (running as `SVC_USER`) **cannot** create `/home/$RUN_USER/.local/share/code-server/User/settings.json` itself without either (a) widening the sudoers rule to a new provisioning command, which cuts against the codebase's explicit "narrowly-scoped sudeors rule" security posture (see the comment at `app.py` line 122-125 and `docs/ARCHITECTURE.md`), or (b) shelling out through `code-server` itself, which has no CLI flag for writing settings. `install.sh`, by contrast, already runs as root and can `mkdir`/`chown` freely — it's the only place in this codebase that can seed the file cleanly without touching the sudoers surface.

## Proposed approach
Seed the file at **install time**, in `install.sh`, gated on `WITH_CODE_SERVER` and placed *after* `RUN_USER` is created (i.e. after line 132's `PROJECTS_DIR` setup, before the "App + engines" section at line 134) — not lazily in `_code_start()`. Reasoning: (1) it must run as root, which only `install.sh` can do (see permissions constraint above); (2) `install.sh` already handles the "flag added on a later re-run" case for free, since re-running with `--with-code-server` newly set will install the code-server binary (line 118-121, unchanged) *and* now also run this new block, with `RUN_USER` already existing from the original install.

Add a new block right after the existing `PROJECTS_DIR` setup (after `install.sh` line 132):

```bash
if [ "$WITH_CODE_SERVER" -eq 1 ]; then
    echo "-- code-server default theme --"
    CODE_SERVER_USER_DIR="/home/$RUN_USER/.local/share/code-server/User"
    mkdir -p "$CODE_SERVER_USER_DIR"
    if [ ! -f "$CODE_SERVER_USER_DIR/settings.json" ]; then
        cat > "$CODE_SERVER_USER_DIR/settings.json" <<'JSON'
{
  "workbench.colorTheme": "Default Dark+"
}
JSON
    fi
    chown -R "$RUN_USER:$RUN_USER" "/home/$RUN_USER/.local/share/code-server"
fi
```

Notes for the implementer:
- The `chown -R` runs unconditionally inside the `WITH_CODE_SERVER` block (not just when the file was freshly created) so that if `mkdir -p` had to create any part of the `.local/share/code-server` tree as root, ownership is corrected back to `RUN_USER` every time — cheap and harmless to repeat on a re-run where it's already correct. Scope the `chown -R` to `.../code-server` specifically (not the whole `.local`), since that's the exact directory this feature owns and it shouldn't touch unrelated dotfiles under `RUN_USER`'s home.
- The `[ ! -f settings.json ]` guard is the entire "never clobber" contract — do not add a `--force` path or any other way to re-seed over an existing file as part of this item.
- Follow the existing script's style: `echo "-- ... --"` section header, same indentation (4 spaces) as neighboring blocks.
- One-line `README.md` touch-up: the existing bullet at line 98-99 ("**VS Code in the browser**, independent on/off per project (`code-server`, `--with-code-server`).") gets a short clause appended noting it ships with a dark theme by default, e.g. "... (`code-server`, `--with-code-server`; opens in a dark theme by default)." No other docs need updating — there's no dedicated code-server doc page, and `config/switchboard.env.example` gets no new variable (see Non-goals).

## Affected areas
- `install.sh` — one new ~12-line block, gated on the existing `WITH_CODE_SERVER` flag, placed after `RUN_USER` creation. No changes to any existing line's behavior.
- `README.md` — one bullet's wording, no structural change.
- Nothing in `app/app.py`, `config/switchboard.env.example`, or the sudoers block changes.

This is a single-file (plus a one-line doc touch-up), single-layer change — no load-balanced decomposition needed.

## Edge cases
- **`--with-code-server` never used at all:** the whole new block is skipped (same `WITH_CODE_SERVER` gate as the existing binary-install step) — no `.local/share/code-server` directory is created, no behavior change from today.
- **Re-install where `settings.json` already exists** (whether pre-seeded by an earlier run of this same feature, or hand-customized by the user via code-server's own theme picker, which rewrites the whole file): the `[ ! -f ... ]` guard means it is never touched, byte-for-byte, on any subsequent `install.sh` run.
- **`--with-code-server` added on a re-run after an initial install without it:** `RUN_USER` already exists (created on the original run), so the new block runs successfully the first time the flag is set; `code-server`'s binary-install block (line 118-121, unchanged) also runs for the first time in the same pass. Both land together, settings seeded correctly.
- **code-server binary already present on the box from outside `install.sh`** (e.g. hand-installed) but `--with-code-server` is never passed to `install.sh`: seeding is skipped, since it's gated on the flag, not on binary presence. This matches today's existing binary-install check's own gating (line 118 checks `WITH_CODE_SERVER` *and* binary absence) and is an acceptable, narrow edge this item doesn't need to solve — `--with-code-server` is the documented way to opt in.
- **Permission/ownership boundary:** covered in depth under "Background" — this is the load-bearing reason the fix lives in `install.sh` (root) rather than `app.py`/`_code_start()` (unprivileged `SVC_USER`, sudoers-restricted to three specific binaries).
- **Platform:** Linux-only, consistent with the rest of the repo (no cross-platform branch needed).
- **Concurrency/duplicate runs:** `install.sh` isn't expected to run concurrently with itself; `mkdir -p` + the file-existence guard are naturally idempotent even if it somehow were.

## Acceptance criteria
- [ ] Given a fresh box, when `install.sh --with-code-server` completes, then `/home/$RUN_USER/.local/share/code-server/User/settings.json` exists, is owned by `$RUN_USER:$RUN_USER`, and contains `"workbench.colorTheme": "Default Dark+"`.
- [ ] Given a fresh box, when `install.sh` completes **without** `--with-code-server`, then no `/home/$RUN_USER/.local/share/code-server` directory is created.
- [ ] Given an install that already has a code-server `settings.json` (whether pre-seeded by this feature or hand-edited by the user, e.g. with a different theme or extra keys), when `install.sh` (with or without `--with-code-server`) is re-run, then that file's contents are unchanged (verify by content hash before/after).
- [ ] Given an install originally done **without** `--with-code-server`, when `install.sh --with-code-server` is re-run later, then both the `code-server` binary gets installed **and** `settings.json` gets seeded with the dark theme in that same run.
- [ ] Given the seeded `settings.json`, when a user opens `/code/<any project>` in the browser for the first time, then the editor loads already in the `Default Dark+` theme with no manual switch needed.
- [ ] `README.md`'s VS Code bullet mentions the dark-by-default behavior.

## Open questions
None blocking. One resolved design call, stated for the record: seeding happens in `install.sh` (root, one-time, after `RUN_USER` exists) rather than lazily in `_code_start()`, specifically because `app.py` runs as an unprivileged `SVC_USER` whose `sudo -u RUN_USER` rights are scoped by sudoers to exactly `tmux`/`ttyd`/`code-server` and nothing else — it has no way to write a file into `RUN_USER`'s home without either widening that sudoers surface (a real security-posture change, out of scope for a "tiny" item) or going through `install.sh`, which already runs as root. Proceeding under this assumption; flag if there's a reason the sudoers surface should be widened instead — it isn't apparent from the code.

## Risk / rollback notes
Extremely low risk: additive-only, single new `install.sh` block behind an existing flag, no changes to any code path that runs on every request (unlike a lazy `_code_start()` check, this never executes at runtime). Failure mode if something's wrong (e.g. wrong path) is simply "theme stays default light," not a broken install — the block only ever creates/chowns a directory and conditionally writes one small JSON file; it cannot fail the rest of `install.sh` since `set -euo pipefail` would just halt the script early with a clear error at that line, same as any other step. Rollback, if ever needed, is deleting the one file (`rm /home/$RUN_USER/.local/share/code-server/User/settings.json`) or reverting the `install.sh`/`README.md` diff — no data migration, no other file touched.
