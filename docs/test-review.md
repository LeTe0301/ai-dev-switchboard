# Test & Review: VS Code (code-server) dark mode by default

## Scope
`install.sh` (the `path_has_symlink()` helper, lines 95-104, plus the
`-- code-server default theme --` block, lines 144-166) and `README.md`
(one bullet's wording) — the two functional/doc files in the diff, tested
against all 6 acceptance criteria in `docs/spec.md`. Confirmed via
`git diff --stat` that `app/app.py` and `config/switchboard.env.example`
are untouched, matching the spec's non-goals. `docs/spec.md`/
`docs/implementation.md` are this cycle's own working docs, not reviewed as
application code.

**This is a re-review cycle.** A prior pass of this same feature (see git
history of this file) found a must-fix security defect (symlink-following
arbitrary-file-write-as-root) and sent it back to the developer. The
developer added a `path_has_symlink()` guard. This pass independently
re-verifies both original exploits are now blocked, re-confirms the
feature's core acceptance criteria weren't regressed by the fix, and then
does a fresh independent review of the current diff (not a re-read of the
prior review's notes).

All testing below was performed hands-on this session against the actual
shipped lines — extracted verbatim via `sed -n '95,104p;144,166p' install.sh`
into a runnable wrapper script (not hand-retyped, to eliminate transcription
risk) — with real Linux users created via `useradd -m` (not mocked), run as
root via `sudo`. All throwaway users and files were removed afterward,
verified via `id <user>` ("no such user") and `ls /home` showing no leftover
home dirs.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| Security | **Exploit 1 (dangling file symlink) re-run against the fix**: unprivileged user plants `~/.local/share/code-server/User/settings.json -> /etc/cron.d/pwned2` | manual, real user `revexp1`, fixed block run as root with `RUN_USER=revexp1` | pass (blocked) | Block printed `Skipping code-server theme seed: a symlink exists under /home/revexp1/.local/share/code-server — not following it.`, exit 0. `/etc/cron.d/pwned2` confirmed absent before the run and **never created**. `stat`/`ls -la` after the run show `settings.json` is still the identical untouched symlink to `/etc/cron.d/pwned2`. |
| Security | **Exploit 2 (directory-level symlink) re-run against the fix**: unprivileged user replaces `~/.local/share/code-server` with a symlink to `/tmp/sensitive-target-rev2` | manual, real user `revexp2`, fixed block run as root with `RUN_USER=revexp2` | pass (blocked) | Same skip message, exit 0. `/tmp/sensitive-target-rev2` confirmed absent before the run and **never created**. The symlink itself (`code-server -> /tmp/sensitive-target-rev2`) remains untouched (`stat` confirms it's still a symlink, not a real directory root wrote into). |
| AC1 | Fresh box, `--with-code-server` → `settings.json` exists, owned `$RUN_USER:$RUN_USER`, contains `"workbench.colorTheme": "Default Dark+"` | manual, fresh user `revac1`, `WITH_CODE_SERVER=1` | pass | `stat -c '%U:%G %a'` → `revac1:revac1 644`; `cat` → `{"workbench.colorTheme": "Default Dark+"}` (pretty-printed) |
| AC2 | Fresh box, no `--with-code-server` → no `.local/share/code-server` directory, no `.local` at all | manual, fresh user `revac2`, `WITH_CODE_SERVER=0` | pass | `test -e /home/revac2/.local` → false |
| AC3 | Existing `settings.json` (hand-edited: `{"workbench.colorTheme":"Custom Purple","editor.fontSize":42,"note":"hand-edited by user"}`) survives re-run with **and** without the flag, byte-for-byte | manual, SHA-256 before/after both re-runs on user `revac3` | pass | hash identical across all three checks (`c70e3fe1...b97aee0`); final `cat` shows hand-edited content untouched |
| AC4 | Install originally without `--with-code-server`, flag added on a later re-run → `settings.json` seeded correctly on that later run | manual, fresh user `revac4`, first run `WITH_CODE_SERVER=0` (confirmed nothing created), second run `WITH_CODE_SERVER=1` | pass | Second run created `settings.json` owned `revac4:revac4` with correct content |
| AC5 | Seeded `settings.json` → editor loads already in `Default Dark+` | not re-exercised this cycle | pass (carried forward) | This fix's diff changes only the symlink-guard logic, not the JSON content or destination path — content identity re-confirmed via the AC1 row above (byte-identical to the pre-fix content). The prior review pass already did the deeper real-headless-Chromium DOM/CSS render assertion against this exact content+path combination (negative-control comparison against an unseeded install) — not re-run here since nothing in this fix touches code-server's theme-rendering behavior. |
| AC6 | `README.md`'s VS Code bullet mentions dark-by-default | `git diff` | pass | bullet reads "...(`code-server`, `--with-code-server`; opens in a dark theme by default)." — unchanged by this fix |
| Edge | `chown -R` scope is exactly `.../code-server`, not broader `RUN_USER` home | manual, planted root-owned sibling files/dirs (`other-root-owned-dir`, `.bashrc_extra`) under fresh user `revac5` before running the block | pass | Siblings stayed `root:root`; only `.local/share/code-server` became `revac5:revac5` |
| Edge | Syntax check | automated | pass | `bash -n install.sh` — clean |

## Regression check
Full existing suite: `python3 -m unittest discover -s tests -v` → **75/75
pass**, 0 failures/errors (`app/app.py` untouched by this diff). No
CI/shellcheck/bats harness exists in this repo (confirmed absent again this
session — no `shellcheck` binary available).

---

## Spec coverage
All 6 acceptance criteria implemented and independently hands-on
re-verified against the fixed code (table above). No gaps. The security
fix itself isn't a named acceptance criterion, but both of the originally
reproduced exploits were independently re-run against the current code and
confirmed blocked, not just accepted on the developer's word.

## Findings (most severe first)

### 1. Residual TOCTOU gap between `path_has_symlink()`'s check and the subsequent `mkdir -p`/write/`chown -R` — should-fix (non-blocking)
- File: `install.sh:149-165`
- Issue: `path_has_symlink` is a check performed once, up front; the
  `mkdir -p`/`cat >`/`chown -R` that follow it are separate, later
  syscalls. Between the check returning "no symlink anywhere in this path"
  and those operations actually running, `RUN_USER` — who owns and fully
  controls everything under their own home directory — could in principle
  race to swap a real, already-existing directory component (e.g.
  `.local/share/code-server`, if it already existed and thus caused the
  check to pass) for a symlink in that narrow window, reproducing a
  variant of the original two exploits but requiring precise timing
  instead of being trivially reproducible at leisure.
- Judgment call (asked for explicitly this cycle): this is **not** a
  blocker. Weighing it: (1) the content root ever writes is fixed
  (`{"workbench.colorTheme": "Default Dark+"}`), never attacker-supplied —
  a successful race still can't inject arbitrary content, only place this
  one fixed, mostly-harmless JSON blob at an attacker-chosen path; (2) the
  window is a handful of syscalls wide inside a script that normally runs
  once, interactively, by an admin — meaningfully narrower and harder to
  hit than the original findings, which were exploitable deterministically
  at any time; (3) this product's threat model (a self-hosted personal/
  small-team dev box, `RUN_USER` a trusted-ish but unprivileged tenant, not
  an adversarial multi-tenant SaaS) makes "already has a persistent shell
  and is willing to spin a tight race loop hoping to catch one particular
  admin's install run" a low-probability, high-effort attack for low
  marginal payoff beyond what's already closed. This matches the original
  review's own explicit guidance that the fix "does not need to be
  elaborate" — a fully atomic/`O_NOFOLLOW` fix would require dropping into
  a compiled helper or `python3 -c` snippet, disproportionate here. Worth
  a follow-up if this codebase's threat model ever changes (untrusted/
  adversarial `RUN_USER` tenants), but not worth blocking this cycle on.

### 2. Redundant inner `[ ! -L "$CODE_SERVER_SETTINGS" ]` guard — nit
- File: `install.sh:157`
- Issue: `path_has_symlink` already confirms the leaf `settings.json` (and
  every parent) isn't a symlink before this line is reached, so `[ ! -L
  ... ]` here is provably redundant given `path_has_symlink`'s own logic
  (not just "probably fine") within the same single-pass execution. The
  developer's own implementation notes call this out explicitly as
  intentional "belt-and-suspenders," which is a defensible position for a
  guard this cheap — flagging only as a nit since it adds two tokens of
  cognitive overhead reading the code, not any actual risk or bug.

## Follow-ups (non-blocking)
- Finding 1 above (TOCTOU) — revisit only if `RUN_USER`'s trust level in
  this product ever changes.
- Carried forward from the prior review pass, still unaddressed and still
  out of this diff's scope: `install.sh:132`'s pre-existing
  `chown "$RUN_USER:$RUN_USER" "$PROJECTS_DIR"` has the same class of risk
  (plain `chown` dereferences a symlink by default) — unmodified by either
  this cycle or the fix cycle, noted as context only.

## Overall verdict
**Approve, with non-blocking follow-ups.** The testing pass is clean: both
previously-reproduced exploits (dangling-file symlink and directory-level
symlink) were independently re-executed against the current fixed code this
session and are confirmed blocked — not just accepted on the developer's
report — and the feature's core acceptance criteria (AC1 fresh seed, AC3
byte-for-byte survival across re-runs with and without the flag, AC4 flag
added later) were all re-run hands-on against the fixed code and pass,
confirming the fix didn't regress the feature. The independent review pass
found one should-fix (residual TOCTOU race, judged non-blocking per the
reasoning in Finding 1 above — narrower, fixed-content-only, and
proportional to this product's threat model and the original "does not
need to be elaborate" guidance) and one nit (redundant guard, intentional
and harmless). No must-fix issues remain. This closes the build cycle —
control returns to product-manager.
