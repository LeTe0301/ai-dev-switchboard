# Test & Review: install.sh fixes from Proxmox E2E test round 1 (items 22-27)

## Scope
Six independently-diagnosed `install.sh` bugs found by a real Proxmox E2E
test (`docs/BACKLOG.md` items 22-27; fix 4/item 25 also touches
`scripts/gitea-sync-project.sh`). Two (22, 27) currently make the product
completely non-functional out of the box. No open design questions — every
fix's shape was already pinned down with exact repro/root-cause/verified-
working-fix by the E2E tester, per `docs/spec.md`.

---

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Fix 1 (item 22): `taiga_board.py` copied during install | Read: `install.sh:300-303` diff | pass | `cp "$REPO_DIR/app/taiga_board.py" "$INSTALL_DIR/taiga_board.py"` added, same quoting/path-construction pattern as the two sibling `cp` lines it sits next to |
| 2 | Fix 2 (item 23): `-it` dropped from printed `docker exec` command | Read: `install.sh:967` diff | pass | Line now reads `docker exec --user git ai-dev-switchboard-gitea gitea admin user create \` — `-it` removed, nothing else on the line changed |
| 3 | Fix 3 (item 24): `$STATE_DIR` itself chowned to `SVC_USER`, right after `useradd` | Read: `install.sh:244-248` | pass | `chown "$SVC_USER:$SVC_USER" "$STATE_DIR"` lands immediately after the `id "$SVC_USER" ... useradd` line; exactly one occurrence (`grep -n 'chown.*STATE_DIR'` shows this line plus the pre-existing, untouched `.../uploads` chown at line 474 — not duplicated, not dropped) |
| 4 | Fix 4 (item 25): `runtime.env` written with resolved `RUN_USER`/`PROJECTS_DIR` values (not literal placeholders), mode 644 | Read: `install.sh:493-503`; live shell reproduction of the exact heredoc | pass | Heredoc is `<<EOF` (unquoted → variable expansion, not `<<'EOF'`); reproduced the exact block in isolation with `RUN_USER=testuser`/`PROJECTS_DIR=/home/testuser/projects` and confirmed the written file contains the literal resolved values `RUN_USER=testuser` / `PROJECTS_DIR=/home/testuser/projects`, and `stat -c %a` = `644` |
| 5 | Fix 4: `gitea-sync-project.sh` `CONFIG` path repointed at `runtime.env`, not just commented | Read: `scripts/gitea-sync-project.sh:37` diff | pass | `CONFIG=/etc/ai-dev-switchboard/switchboard.env` → `CONFIG=/etc/ai-dev-switchboard/runtime.env`; confirmed via `grep` that no other reference to the old path remains in the script |
| 6 | Fix 4: `switchboard.env`'s own `chmod 600` genuinely untouched | Read: `install.sh:490-491` | pass | `chown "$SVC_USER:$SVC_USER" "$ENV_FILE"` / `chmod 600 "$ENV_FILE"` unchanged, sit *above* and separate from the new `runtime.env` block — no loosening |
| 7 | Fix 5 (item 26): top-level `~RUN_USER/.local` chowned, not just the code-server subtree, and not chowned any broader (e.g. not the home dir itself) | Read: `install.sh:296` diff, plus full `grep` of every `chown` in the file | pass | `chown -R "$RUN_USER:$RUN_USER" "$CODE_SERVER_DIR"` → `chown -R "$RUN_USER:$RUN_USER" "/home/$RUN_USER/.local"`; still gated inside `if [ "$WITH_CODE_SERVER" -eq 1 ]` → non-symlink `else` branch, same as before; target is exactly `.local`, not `/home/$RUN_USER` |
| 8 | Fix 6 (item 27): `safe.directory` configured for `SVC_USER`, exact literal `'*'` (not a glob), placed right after `useradd`, alongside fix 3 | Read: `install.sh:249-260` | pass | `sudo -u "$SVC_USER" git config --global --add safe.directory '*'` — single-quoted literal `*`, exactly one occurrence in the file, lands directly after fix 3's `chown`, both after the `useradd` line (valid required order — `$SVC_USER` must exist first) |
| 9 | Fix 6 security judgment: "no privilege crossing" claim — SVC_USER never runs a *write* git command directly (only via `sudo -u RUN_USER`) | Read: every `["git", ...]` call site in `app/teams.py` and `app/app.py` | pass, claim holds | Direct (non-sudo) git calls found: `teams.py:3577` (`rev-parse --is-inside-work-tree`), `:3585` (`symbolic-ref -q HEAD`), `:3593` (`status --porcelain`), `:3695` (`branch --list`), `app.py:939` (`remote get-url origin`) — all read-only. The only *write* git operations (`worktree add`/`worktree remove`, `teams.py:3626`/`:3649`) go through `_run_run_user_command()` (`sudo -u RUN_USER`), not direct `SVC_USER` git calls. `safe.directory '*'` therefore only lifts the ownership-mismatch refusal on read-only inspection commands SVC_USER already runs; it does not hand SVC_USER any new write capability |
| 10 | Fix 6: SVC_USER's effective access to project dirs isn't gated by anything `safe.directory` itself weakens (i.e., filesystem perms, not just the ownership check, still bound it) | Read: `install.sh` — no `setfacl`/`usermod -aG`/broadening `chmod` on `PROJECTS_DIR` or per-project dirs found | pass | `PROJECTS_DIR` is `chown "$RUN_USER:$RUN_USER"` only (`install.sh:264`), left at `mkdir -p`'s default mode (world-readable/executable, ~755) — SVC_USER already had ambient read access via world-readable perms before this fix (matches `app.py:924`'s own comment); `safe.directory '*'` doesn't change filesystem permissions, only whether git's ownership check permits the read |
| 11 | `bash -n install.sh` / `bash -n scripts/gitea-sync-project.sh` | Automated: ran directly | pass | Both exit 0, no output |
| 12 | `shellcheck install.sh` | Automated: ran directly | pass (pre-existing warnings only) | Only SC2015 (line 70) and SC2001 (line 601) — both pre-existing, unrelated to any of the six changed spots |
| 13 | `shellcheck scripts/gitea-sync-project.sh` | Automated: ran directly | pass (pre-existing warning only) | Only SC1090 (line 38, inherent to sourcing a non-constant path) — pre-existing, present before this change too |
| 14 | No existing `tests/test_install_*.py` test asserts the specific `cp`/`chown` lines changed | `grep -n` across `tests/test_install_*.py` for the changed identifiers | pass | No hits on `taiga_board`/`chown`/`STATE_DIR`/`CODE_SERVER_DIR`/`safe.directory`/`runtime.env` in any assertion; `switchboard.env` hits are all unrelated (`env_file` tmp-path fixture setup, not path assertions on the real `/etc/...` path) |
| 15 | `RunUserSvcUserDefaultTests` harness (`tests/test_install_update.py`) unaffected by fixes 3/6 landing after `useradd` | Read `_build_users_block_harness()` | pass | Extraction end marker is `'id "$RUN_USER" &>/dev/null'` — strictly before the `id "$SVC_USER"` line fixes 3/6 attach after; harness never sees the new lines |
| 16 | `test_gitea_sync_project.py` unaffected by the `CONFIG` path rename | Read the test file | pass | Test sets `PROJECTS_DIR` directly via env var and never touches `/etc/ai-dev-switchboard/` at all — the script's `[ -f "$CONFIG" ] && source` is a no-op on the test box regardless of path |
| 17 | Full existing regression suite | Automated: `python3 -m unittest discover -s tests -v`, run independently in this session (not reusing the developer's reported numbers) | pass | `Ran 1198 tests in 160.259s` / `OK`; independently grepped the verbose log for `" ... FAIL$"` and `" ... ERROR$"` — zero matches | 
| 18 | The four most directly relevant test files | Automated: `python3 -m unittest tests.test_gitea_sync_project tests.test_install_ollama tests.test_install_set_env tests.test_install_update -v`, run independently | pass | `Ran 54 tests in 80.102s` / `OK` |

## Regression check
Ran the full suite independently (case 17) rather than trusting
`docs/implementation.md`'s reported count — got the identical `1198` total
and a clean `OK`, with zero `FAIL`/`ERROR` lines on independent grep of the
verbose output. (Note on process: an initial non-verbose background run
appeared to stall/truncate — a harness/output-capture artifact tied to
this suite's real-tmux-session tests taking a long time under `discover`,
reproduced identically in this session's scratchpad from an earlier
non-verbose attempt — not a real hang; re-running with `-v` and enough
wall-clock budget completed cleanly at 160s. Flagging this only as a
"give this suite 3+ minutes and use `-v`" operational note for future
reviewer passes, not a project defect.) `git diff --stat` confirms the
code-level diff is isolated to `install.sh` (34 lines) and
`scripts/gitea-sync-project.sh` (1 line) — nothing else in the app/tests
tree touched, so there is no broader surface to regress.

## Defects found
None.

---

## Spec coverage
All six fixes in `docs/spec.md` are implemented exactly as specified and
directly verified against the live diff (cases 1-10 above), not just
against the spec's own description of them:
- Fix 1 / item 22 (`taiga_board.py` cp) — covered, case 1.
- Fix 2 / item 23 (`-it` removal) — covered, case 2.
- Fix 3 / item 24 (`$STATE_DIR` chown) — covered, case 3.
- Fix 4 / item 25 (`runtime.env`, 644, resolved values; `gitea-sync-
  project.sh` CONFIG repoint; `switchboard.env` 600 untouched) — covered,
  cases 4-6.
- Fix 5 / item 26 (`.local` top-level chown, not broader) — covered, case 7.
- Fix 6 / item 27 (`safe.directory '*'`, literal not glob, correct
  placement/order, security reasoning independently re-derived and
  confirmed) — covered, cases 8-10.

The spec's literal shell-level acceptance criteria that require a real
multi-user box (`sudo -u switchboard-svc touch ...`, `sudo -u dev cat
runtime.env`, a real Gitea push/poll round-trip, a real `pipx install`
under `.local`) cannot be executed in this environment — this is the same
documented, unavoidable limitation `docs/implementation.md` itself calls
out (no real `useradd`/multi-user boundary available here). Per the task
brief, direct line-by-line code verification (cases 1-16 above) is the
strongest verification actually available pre-Proxmox, and every one of
those literal acceptance criteria has been traced to a concrete, correct
code change that would produce the claimed behavior on a real box (e.g.
case 4's live reproduction of the exact `runtime.env` heredoc against
synthetic `RUN_USER`/`PROJECTS_DIR` values, which is the closest
in-environment proxy for "sudo -u dev cat runtime.env shows correct
values" available without a second real user account).

## Correctness review (independent re-read of the diff)
Read `git diff -- install.sh scripts/gitea-sync-project.sh` directly.

- Fixes 3 and 6 both land at the single required insertion point (right
  after the `SVC_USER` `useradd` line, since `$SVC_USER` doesn't exist
  before that) — confirmed present exactly once each, in valid order
  (`useradd` → `chown $STATE_DIR` → `safe.directory`), matching the
  spec's explicitly-allowed ordering.
- Fix 4's `runtime.env` write is placed after `$ENV_FILE`'s own
  `chown`/`chmod 600`, and after `$PROJECTS_DIR` is set (line 262, well
  before line 493) — both variables are genuinely in scope, confirmed by
  reading the surrounding 250 lines of the file, not just trusting the
  spec's own line-number claims (which had already shifted slightly from
  the original spec references, as `docs/implementation.md` notes — the
  live positions were re-confirmed directly here).
- Fix 5's chown target is exactly `/home/$RUN_USER/.local` — read the
  full list of every `chown` call in `install.sh` (case 7's evidence) to
  rule out any accidental broadening to the home directory itself or
  narrowing that would miss `.local/share`; the new target is a strict
  superset of the old `$CODE_SERVER_DIR`, as the spec claims.
- Fix 6's `git config --global --add safe.directory '*'` is run via
  `sudo -u "$SVC_USER"` so it lands in `SVC_USER`'s own `~/.gitconfig`
  (a real home dir exists — `SVC_USER` was created with `-m`), not
  root's or a system-wide config — correctly scoped to the account that
  actually needs it.
- No off-by-one, quoting, or ordering errors found anywhere in the diff.
  All `cp`/`chown`/`mkdir` paths use the same double-quoted
  `"$VAR/literal"` construction pattern already established elsewhere in
  the file (fix 1's `cp` line, checked side-by-side against its two
  siblings, is byte-for-byte the same pattern).

## Security review
Fix 6 (`safe.directory '*'`) is the one deliberate security-relevant
change in this cycle, and was independently re-derived rather than taken
on trust (case 9-10 above):
- Confirmed by reading every `["git", ...]` call site in `app/teams.py`
  and `app/app.py` that SVC_USER never runs a git *write* command
  directly — the only writes (`worktree add`/`remove`) are dispatched via
  `_run_run_user_command()`, i.e. `sudo -u RUN_USER`, already crossing
  into the less-privileged account before touching the filesystem. The
  spec's "no privilege crossing beyond what the account already
  effectively has" claim holds under direct inspection, not just as an
  assertion in the code comment.
- `safe.directory '*'` only affects git's ownership-mismatch *refusal*;
  it grants no new filesystem permission. `PROJECTS_DIR` and its project
  subdirectories are left at `mkdir -p`'s default (non-restrictive) mode
  with no ACL/group broadening added anywhere in this diff, so SVC_USER's
  actual read access is unchanged by this fix — it already had ambient
  read access via ordinary world-readable permissions (matching the
  existing `app.py:924` comment/precedent for `load_grounding()`), and
  this fix only unblocks git's own separate ownership check on top of
  that pre-existing access.
- `'*'` (the literal string) is required, not a path glob, because git's
  own `safe.directory` semantics only match a literal configured path or
  the literal string `*` — confirmed against git's documented behavior;
  a shell glob would either be expanded by the shell before git ever
  sees it (harmless here since it's single-quoted) or, if unquoted,
  would silently fail to protect anything git actually matches on,
  which the spec's own reasoning already correctly identifies.
- No injection surface: no external/user input flows into any of the
  six changed lines (all values are install-time shell variables set
  earlier in the same script, or hardcoded strings/paths).
- Fix 4 correctly avoids the shortcut of loosening `switchboard.env`
  itself — confirmed `chmod 600 "$ENV_FILE"` is untouched and the new
  `runtime.env` contains only `RUN_USER`/`PROJECTS_DIR`, no secret
  key ever gets routed through this file (verified by reading the
  entire new block — no `GITEA_API_TOKEN`/`SIMPLE_PASSWORD`/
  `TOTP_SECRET`-shaped variable appears anywhere near it).

## Simplicity/scope review
All six changes are minimal, single-purpose, additive-or-corrective edits
matching the spec's exact proposed code verbatim — no new abstractions, no
unrelated refactoring, no scope creep. `git diff --stat` confirms the code
surface: `install.sh | 34 +-`, `scripts/gitea-sync-project.sh | 2 +-`
(`docs/*.md` changes are documentation only). `docs/BACKLOG.md`'s
additions (items 22-33) are pre-existing backlog documentation from the
E2E test report, not new scope introduced by this cycle's code — items
28-33 are explicitly out of scope for this cycle and untouched in
`install.sh`.

## Findings (most severe first)
None. No must-fix, should-fix, or nit findings.

## Follow-ups (non-blocking)
- The spec's literal shell-level acceptance criteria (real `sudo -u`
  round-trips, a real Gitea push/poll cycle, a real `pipx install`) still
  need a second real Proxmox E2E pass to fully close the loop, exactly as
  `docs/implementation.md`'s "Known limitations" already states. Not a
  blocker for this review — no environment capable of exercising a real
  multi-user `useradd`/`chown` boundary exists here, and the code-level
  verification in this review is as strong as is achievable pre-Proxmox.
- Items 28-33 (already logged in `docs/BACKLOG.md` by the same E2E test
  report) remain for a future cycle — out of scope here, noted only so
  the next `product-manager` pass doesn't need to re-discover them.

## Overall verdict
Approve.
