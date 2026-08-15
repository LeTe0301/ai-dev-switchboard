# Test & Review: BACKLOG item 15 part 2, piece 1 — Default/Advanced entry fork in `ct/create.sh`

## Scope
Independent re-verification of the developer's five claims in
`docs/implementation.md`, plus fresh testing/review against every
acceptance criterion and edge case in `docs/spec.md`. `ct/create.sh` is a
whiptail TUI that only runs interactively on real Proxmox hardware — no CI
harness exists for it (same constraint part 1's review operated under), so
all verification here is either (a) a standalone extracted-and-stubbed
harness sourcing the actual shipped file, (b) mechanical diff/grep against
`git show HEAD:ct/create.sh`, or (c) hand-traced bash semantics confirmed
with throwaway `set -euo pipefail` repros — never a read-and-assume.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Default: no ask/menu/checklist dialog shown for CTID..publish-mode; only entry menu + 1 msgbox | Read `ct/create.sh:34-80`; harness captured `whiptail` call count | pass | Exactly 1 `whiptail` call captured by the stub inside the Default branch body (the msgbox); entry menu is the only other dialog, outside the fork |
| 2 | Default: AUTH_MODE=pve, all WITH_*=0, OLLAMA_*="" , PUBLISH_MODE=none, BASE_URL="", RUN_USER=dev, all DEFAULT_* fields match | Rebuilt harness that `source`s the *actual* `ct/create.sh` lines 40-80 (not a re-typed copy), stubs `whiptail`/`pvesh`, asserts all 20 vars | pass | `ALL CHECKS PASSED (against REAL extracted ct/create.sh source)`, all 20 assertions matched |
| 3 | Advanced: identical prompt sequence, order, text, defaults vs. pre-change `ct/create.sh:34-158` | De-indented Advanced (`else`) branch body (`ct/create.sh:82-206`), diffed against `git show HEAD:ct/create.sh:34-158` | pass | `diff -u` shows only the 9 documented literal→`$DEFAULT_*`/`default_ctid()` substitutions; zero other differences; heredoc body (`OLLAMA_MODEL_CHECK_SCRIPT`, lines 139-153) byte-identical, confirmed not re-indented |
| 4 | Both branches converge on identical variable-name set; nothing conditionally undefined | `grep -oE '^\s*[A-Z_][A-Z0-9_]*='` on both branch bodies, diffed; grepped shared code (lines 209-288) for use of the two Advanced-only names | pass | Diff shows exactly 2 extra names in Advanced (`FEATURES`, `OLLAMA_MODEL_CHECK_SCRIPT`), both purely internal to Advanced's own dialog logic; grep confirms neither is referenced anywhere in the shared `TOTP_SECRET=...`-onward code |
| 5 | Default confirmation msgbox lists all 9 resolved-value fields before `pct create` | Harness asserted all 9 substrings present in the captured msgbox text; read code confirms msgbox precedes `TOTP_SECRET=...`/`pct create` | pass | All 9 field substrings found in captured `whiptail --msgbox` call text |
| 6 | `bash -n`/`shellcheck` clean, no new warnings vs. pre-change | Ran both against current file and against `git show HEAD:ct/create.sh` | pass | `bash -n`: exit 0, no output. `shellcheck`: exit 0 on both current and pre-change file (zero-warning baseline preserved) |
| 7 | Entry-menu + msgbox copy exactly matches `docs/design.md`'s finalized wording | Line-range diff of design.md's fenced code blocks vs. the shipped lines | pass | Entry menu: `diff` empty (byte-identical). Msgbox: extracted both message strings via regex, `design_msg == create_msg: True` |
| 8 | Edge case: Cancel/Esc at entry menu aborts under `set -euo pipefail` | Throwaway repro: `INSTALL_MODE=$(fake_cmd_returning_1)` under `set -euo pipefail` | pass | Script exits with status 1 before reaching the line after the assignment — confirmed the assignment-form command substitution is *not* exempt from `errexit` here |
| 9 | Edge case: Cancel/Esc at Default confirmation msgbox aborts before `pct create` | Throwaway repro: bare command returning 1 under `set -euo pipefail` (mirrors the bare `whiptail --msgbox ...` call, not wrapped in a conditional) | pass | Script exits with status 1 immediately, before the following line runs |
| 10 | Edge case (adversarial, not in spec but asked for by the task): `INSTALL_MODE` empty or containing an unexpected value — does the `if`/`else` fall into Advanced (safe) or something worse? | Repro'd `if [ "$INSTALL_MODE" = "default" ]; then...else...fi` with `""`, `"garbage"`, `"Default"`, `"ADVANCED"`, `"default "` | pass | All 5 non-exact-match inputs fall into the `else` (Advanced) branch — the safe, pre-existing, fully-prompted flow. Never falls through to neither branch. (Noted as effectively unreachable in real operation since real `whiptail --menu` can only return `"default"`, `"advanced"`, or fail nonzero on Cancel — but the code degrades safely even if it somehow were reached.) |
| 11 | Non-goal: no changes to `install.sh`/`app/`/`config/` | `git diff --stat` | pass | Only `ct/create.sh` (code) + `docs/*.md` (pipeline scratch docs) changed |
| 12 | Regression: full existing test suite unaffected | `python3 -m unittest discover -s tests` | pass | `Ran 289 tests ... OK` |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` — **OK, 289
tests, no failures.** (`ct/create.sh` has no dedicated test file in `tests/`,
matching part 1's own established precedent — confirmed by `grep -rn
"create.sh" tests/` returning nothing, and this is an accepted, spec'd-in-
advance gap per "Risk / rollback notes": no CI dependency on this file.)

## Defects found
None. The testing pass is fully clean — proceeding to the review pass.

---

## Spec coverage
Every checkbox in `docs/spec.md`'s "Acceptance criteria" section:

| Acceptance criterion | Implemented? | Tested? |
|---|---|---|
| Default: zero dialogs beyond entry menu + 1 confirmation msgbox | Yes (`ct/create.sh:34-80`) | Yes (test #1) |
| Default: all 20 named variables + msgbox summary fields correct | Yes | Yes (test #2, #5) |
| Advanced: identical to pre-change flow | Yes | Yes (test #3) |
| Both paths converge on identical variable set, nothing conditionally undefined | Yes | Yes (test #4) |
| `bash -n`/`shellcheck` clean, no new warnings | Yes | Yes (test #6) |

All five acceptance criteria are implemented and independently re-verified,
not just re-read from `docs/implementation.md`'s own claims. Every edge case
`docs/spec.md`'s "Edge cases" section lists is covered by either code
inspection (fallback-to-900 reuses the pre-existing, unmodified
`default_ctid()` logic; unvalidated CTID/storage failing at `pct
create`/`pveam` time is explicitly deferred to pieces 2/4 and unchanged from
today's pre-existing behavior) or the Cancel-abort repros above (tests #8,
#9). No acceptance criterion or edge case was left unimplemented or
untested.

## Findings (most severe first)
None. No must-fix, should-fix, or nit findings — the diff is a clean,
minimal relocation exactly matching `docs/spec.md`'s "Proposed approach" and
`docs/design.md`'s finalized copy, with no drift found anywhere in the
Advanced branch (the specific risk the spec's own "Risk / rollback notes"
flagged as the main concern for this cycle).

Notes from the correctness/security/simplicity pass, none rising to a
finding:
- No new external input or injection surface: the entry menu and
  confirmation msgbox introduce no new user-controlled string interpolated
  unsanitized into a shell command — `INSTALL_MODE` is only ever compared
  with `[ "$INSTALL_MODE" = "default" ]` (string equality, not `eval`'d or
  interpolated into a command), and every value shown in the new msgbox is
  either a static `DEFAULT_*` literal or the same `pvesh`-derived `CTID`
  the pre-existing Advanced flow already computed and used unsanitized.
- No scope creep: the diff touches only the fork mechanism, the nine
  `DEFAULT_*` constants, and `default_ctid()` — nothing beyond
  `docs/spec.md`'s "Proposed approach". The stale non-interactive
  `CT_*`/`SWB_*` header-comment issue (spec's own "Open questions" #3) is
  correctly left untouched, as scoped.
- Re-indentation of the Advanced branch (one level, for the new `else`
  wrapper) is mechanically necessary and was verified not to have touched
  the `OLLAMA_MODEL_CHECK_SCRIPT` heredoc body, which would have been a
  real (silent, hard-to-spot) correctness bug if it had.

## Follow-ups (non-blocking)
None.

## Overall verdict
**Approve.**

All five of the developer's own verification claims were independently
reproduced from scratch this session (not re-read and trusted) and all
five held exactly: the Advanced-branch diff against pre-change
`ct/create.sh` shows only the documented 9 literal-to-variable
substitutions; a freshly-built harness sourcing the actual shipped file
(not a re-typed copy) confirms all 20 Default-branch variables and all 9
msgbox summary fields; both branches converge on the identical
downstream-consumed variable set; `bash -n`/`shellcheck` are clean on both
the current and pre-change file; and the entry-menu/msgbox copy is
byte-identical to `docs/design.md`'s finalized wording. The adversarial
Cancel/empty-`INSTALL_MODE` checks the task specifically asked for both
confirm safe behavior (Cancel aborts under `set -euo pipefail`; any
non-`"default"` value falls safely into the pre-existing, fully-tested
Advanced flow, never into an undefined third state). No must-fix,
should-fix, or nit findings.
