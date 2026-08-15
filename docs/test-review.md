# Test & Review: BACKLOG item 15, piece 5 — `ct/create.sh` optional-feature checklist + taiga/ollama follow-ups

## Scope
Independent verification of `ct/create.sh`'s replacement of two standalone
`yesno` prompts with a `whiptail --checklist` (git-hosting/code-server/
taiga/ollama), the taiga resource-cost `msgbox`, and the ollama endpoint/
model validation retry loop, against every acceptance criterion in
`docs/spec.md` and every dialog-flow/copy detail in `docs/design.md`. This
script only runs interactively on a real Proxmox VE host — there is no CI
harness for it. I did not just re-read the developer's summary: I
independently reran every harness the developer described (syntax check,
shellcheck, the verbatim-heredoc diff, the checklist/`INSTALL_FLAGS`
harness, the exact-match python heredoc), added several of my own
(non-dict/missing-`data`-key/non-list-`data` python inputs, and an
empirical `set -e` probe of the exact `[ ... ] && ...` and `if yesno; then
... else ... fi` idioms used in the file), and read the full diff and the
full resulting file line by line against both `docs/spec.md` and
`docs/design.md`.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | All four checklist rows unchecked by default | Code inspection (requires real TTY to see rendered) | pass | `ct/create.sh:60-63`: all four rows end in literal `OFF` |
| 2 | Nothing checked → `INSTALL_FLAGS` is `--yes` only | Automated (rerun of extracted harness) | pass | `nothing checked => INSTALL_FLAGS=[--yes]` |
| 3 | git-hosting + code-server checked → exact flags, order preserved, no taiga/ollama follow-ups | Automated harness + code inspection | pass | `git-hosting + code-server => INSTALL_FLAGS=[--yes --with-git-hosting --with-code-server]`; taiga/ollama blocks gated on `WITH_TAIGA`/`WITH_OLLAMA` which stay 0 |
| 4 | Taiga checked → single msgbox with exact resource-cost wording before continuing | Code inspection + byte comparison vs. `docs/design.md` | pass | `ct/create.sh:81` text is byte-identical to `docs/design.md`'s finalized copy (incl. "in the web UI", which spec's own draft lacked) |
| 5 | Ollama checked, reachable endpoint + present model → `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` written with exact normalized URL/model, `INSTALL_FLAGS` includes `--with-ollama` | Automated (python heredoc rerun) + code tracing | pass | `OK` for `{"data":[{"id":"qwen3:8b"}, ...]}` + `qwen3:8b`; `OLLAMA_BASE_URL_NORM`/`OLLAMA_MODEL_INPUT` only set on `OK`, appended via `>>` at `ct/create.sh:204-209` |
| 6 | Ollama checked, unreachable/model-absent/unparseable → whiptail names the specific reason, then retry-or-skip | Code inspection vs. `docs/design.md` §3c/3d | pass | Three distinct `_ollama_fail_msg` strings match `install.sh:772/816/818/822` and `docs/design.md` verbatim |
| 7 | Validation failed, operator declines retry → `TEAM_LLM_*` absent, no `--with-ollama` | Code tracing | pass | `WITH_OLLAMA=0` set in the "No" branch before `break`; `OLLAMA_BASE_URL_NORM`/`OLLAMA_MODEL_INPUT` never touched outside the `OK` case |
| 8 | Validation failed, operator retries → loop exits with the newly entered values, not stale ones | Code tracing | pass | `_ollama_url_input`/`_ollama_model_input` are reassigned via `ask()` at the top of every iteration; `OK` branch always captures the current iteration's values |
| 9 | `qwen3:8b` advertised, `qwen3:8` entered → model-absent, no substring match | Automated (rerun of python heredoc, independently, not just trusted) | pass | `MODEL_ABSENT:qwen3:8b` for input `qwen3:8` against `{"data":[{"id":"qwen3:8b"}]}` |
| — | Extra: non-dict list entries, missing `data` key, non-list `data`, non-JSON, `null`, bare `42` | Automated (my own additions, not in developer's harness) | pass | All six inputs exit 0 and degrade to `PARSE_ERROR` or `MODEL_ABSENT:` — never crash, never false-positive |
| — | `set -e` adversarial check: `[ "$WITH_X" -eq 1 ] && INSTALL_FLAGS=...` with all four false | Automated (empirical stub test) | pass | Script survives, `exit=0`, `INSTALL_FLAGS=[--yes]` — confirms the pre-existing idiom (already used for git-hosting/code-server) is safe when reused for taiga/ollama |
| — | `set -e` adversarial check: empty `FEATURES` `for` loop | Automated (empirical stub test) | pass | Zero-iteration loop over an empty unquoted variable does not trip `set -e` |
| — | `set -e` adversarial check: `curl \|\| true` + python heredoc pipeline always exits 0 | Automated (empirical, 13 total input variants across both harnesses) | pass | Every case returns exit 0; no path exists where `_ollama_check=$(...)` assignment could trip `set -e` |
| — | Byte-for-byte copy of ollama python heredoc vs. `install.sh` | Automated (`diff`, rerun independently) | pass | `diff <(sed -n '787,802p' install.sh) <(sed -n '91,106p' ct/create.sh)` → empty |
| — | Syntax check | Automated | pass | `bash -n ct/create.sh` → exit 0, no output |
| — | Shellcheck, full file | Automated | pass | `shellcheck ct/create.sh` → zero warnings |
| — | `--with-taiga`/`--with-ollama` are real, recognized `install.sh` flags | Code inspection | pass | `install.sh:98-99` case-arm parser recognizes both |
| — | `TMP_ENV` append point: after heredoc `EOF`, before `pct push`, uses `>>` not overwrite | Code inspection | pass | `ct/create.sh:190-210`: append block sits between the heredoc's closing `EOF` (203) and `pct push` (210), uses `>>` |
| — | `INSTALL_FLAGS` still gets `--yes` first, unconditionally, before any new flag | Code inspection | pass | `ct/create.sh:214-218`: `INSTALL_FLAGS="--yes"` is the unconditional first assignment; all four `--with-*` appends are conditional and come after |
| — | Nothing else in the file (CTID/hostname/storage prompts, final `SUMMARY` msgbox) was altered | `git diff --stat` / full diff read | pass | Diff shows exactly 3 hunks (python3 preflight, checklist+follow-ups replacing the two `yesno` blocks, `TMP_ENV`/`INSTALL_FLAGS` additions); `SUMMARY` msgbox (lines 225-239) untouched |
| — | Only `ct/create.sh` changed among code files (no `install.sh`/`app/`/`config/` changes) | `git diff --stat -- ct/create.sh install.sh app/ config/` | pass | Only `ct/create.sh` listed |
| — | `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` env var names match what `install.sh`/`config/switchboard.env.example` already expect | `grep` cross-check | pass | Identical names used in all three files |

## Regression check
No automated test file in `tests/` references `ct/create.sh` (`grep -rl
"create.sh" tests/` → no output) — expected, per `docs/spec.md`'s own
framing ("no CI harness exists for this file"). No Python/`app/` files were
touched by this diff (`git diff --stat -- ct/create.sh install.sh app/
config/` shows only `ct/create.sh`), so there is no existing automated
suite this change could regress. `install.sh` itself is unmodified —
confirmed byte-for-byte via the diff stat above, not just assumed.

## Defects found
None.

---

## Spec coverage
All 9 numbered acceptance criteria in `docs/spec.md` are implemented and
verified above (criteria 1/8 required code-inspection since they need a
real whiptail TTY to observe directly — this matches the task's own framing
that no such environment exists here; the underlying logic each depends on
was traced by hand and, where it has no whiptail dependency, executed
directly). No criterion was skipped or left untested.

All eight "Edge cases" in `docs/spec.md` are also covered by the diff and
were traced: nothing-checked (criterion 2), taiga-only (criterion 3-style
harness), blank URL/model (falls into the same unreachable-endpoint path,
confirmed by code reading — `curl` against an empty/malformed URL fails the
same way), decline-retry (criterion 7), the container-side double-validation
edge case (explicitly accepted as-is per spec, not something this script
could fix), Cancel-aborts (see Finding 2 below for a nuance), and the
`python3`-preflight-runs-unconditionally-before-the-checklist ordering
(confirmed: `ct/create.sh:24` sits before the checklist at line 57).

## Findings (most severe first)

### 1. `docs/design.md`'s git-hosting row-label table cell has a garbled quote placement — nit, not a code defect
- File: `docs/design.md:28` vs. `ct/create.sh:60`
- Issue: `docs/design.md`'s table literally reads `Private repos over SSH +
  "+" New project button` (quotes wrapped around just `+`, with `New
  project button` trailing unquoted) — this differs, character-for-character,
  from what was implemented: `Private repos over SSH + "+ New project"
  button` (quotes wrapped around the phrase `+ New project`, matching the
  UI's actual button label).
- Why this isn't a must-fix: `docs/design.md`'s own "Row-label rationale"
  section, two lines below the table, explicitly describes the intent as
  "the UI integration point (`"+ New project"`)" — i.e. the design doc's
  own prose disagrees with its own table and agrees with what was
  implemented. `docs/spec.md`'s original draft code also already used the
  implemented quoting verbatim. This reads as a markdown-table rendering
  glitch in `docs/design.md`, not a considered design decision the
  implementation deviated from. No action needed on the code; if anyone
  touches `docs/design.md` again, the table cell is worth fixing to match
  its own rationale text.

### 2. `docs/spec.md`'s "Cancel aborts the whole run" edge-case claim is imprecise for the ollama retry-vs-skip `yesno` specifically — informational, not a code defect
- File: `docs/spec.md`'s "Edge cases" section vs. `ct/create.sh:139-148`
- Issue: `docs/spec.md` states "Cancel pressed on any new dialog
  (checklist, taiga msgbox, ollama `ask`/`yesno`): aborts the whole run...
  no new behavior introduced." I verified empirically (a Cancel-returning
  stub under `set -euo pipefail`) that this is true for the checklist and
  both `ask()` (inputbox) prompts — Cancel there is an unguarded command
  substitution assignment, and `set -e` correctly aborts. It is **not**
  true for the ollama retry-vs-skip `yesno("Try a different URL/model?")`:
  that call is used as `if yesno ...; then ... else ... fi`, and bash's
  `set -e` explicitly exempts the controlling command of an `if`/`while`
  from triggering an abort — so whiptail's Cancel exit code (255) and "No"
  (exit 1) are indistinguishable and both land in the same `else` branch
  (skip ollama, identical to explicitly declining to retry). The run is
  *not* aborted in that specific case.
- Why this isn't a must-fix: this is not new behavior introduced by this
  diff — it is the exact same `if yesno(...); then ...; fi` idiom the file
  already used, unmodified, for the pre-existing git-hosting/code-server
  prompts before this change. The degraded outcome (treat Cancel as "skip
  ollama") is safe — no crash, no partial/corrupt state, no acceptance
  criterion tests Cancel-vs-No distinction on this dialog. This is
  `docs/spec.md`'s own documentation being slightly imprecise about the
  exact mechanism for one dialog type among several, not a bug in
  `ct/create.sh`.

## Follow-ups (non-blocking)
- Optionally correct `docs/design.md:28`'s git-hosting table cell to match
  its own rationale text (Finding 1).
- Optionally tighten `docs/spec.md`'s "Cancel aborts the whole run" edge
  case to note the one `yesno`-specific exception (Finding 2), if
  `docs/spec.md` is revisited for a future piece of item 15.
- Genuinely unverifiable without a real Proxmox host: an actual rendered
  `whiptail --checklist` keystroke sequence in a TTY, a real `pct
  create`/`pct exec` round-trip, and a real Ollama endpoint's actual
  `/v1/models` HTTP response over the network (only its JSON body shape was
  exercised). Carried over from the developer's own disclosed limitation,
  independently confirmed to be a genuine environment gap, not something
  this pass could close either.

## Overall verdict
Approve.
