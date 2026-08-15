# Test & Review: carry forward rejected CTID/hostname on retry (ct/create.sh)

## Scope
`ct/create.sh`'s Advanced-path CTID and hostname validation retry loops
(part 3, PR #22) now pre-fill the retry `ask()` box with the just-rejected
value instead of always resetting to the constant default. Covers all four
acceptance criteria in `docs/spec.md`.

1. The **original** testing/review pass (below, unchanged from the first
   sitting) — found Defect 1 (must-fix: a TOCTOU in the episode-currency
   check) and Defect 2 (should-fix: a TOCTOU in lock-dict cleanup), plus two
   minor gaps, and blocked.
2. A **re-review pass** (new section below, this sitting) — independently
   re-verifies the developer's claimed fixes for both defects against the
   actual shipped code (not the developer's summary), reruns the full
   suite, and issues the final verdict for this cycle.

---

## Original pass (first sitting) — test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Invalid CTID rejected → re-prompt pre-fills the just-rejected CTID, not `default_ctid()` | Automated: extracted the literal lines 127-138 from `ct/create.sh` via `sed`, sourced them into a harness with stubbed `ask`/`msg`/`pct` (recording every pre-fill `ask()` received, via disk-backed queues to survive the `$(...)` subshell) and the real `default_ctid`/`_valid_hostname` functions copied verbatim from the file | pass | `harness2.sh`/`harness3.sh` run: 1st CTID prefill `900` (default), 2nd prefill `abc` (the rejected value) — see below |
| 2 | CTID rejected for "already in use" (`pct status` branch) also carries forward | Automated, same harness, 3-value queue (`abc` → format-invalid, `999999999` → in-use, `150` → accepted) | pass | 3rd CTID prefill = `999999999`, the just-rejected in-use id |
| 3 | Invalid hostname rejected → re-prompt pre-fills the just-rejected hostname, not `$DEFAULT_CT_HOSTNAME` | Automated, same harness | pass | 1st hostname prefill `ai-dev-switchboard` (default), 2nd prefill `bad_host!` (the rejected value) |
| 4 | First prompt (nothing entered yet): pre-fill unchanged from today | Automated, same harness, run under `set -euo pipefail` (matching the file's own `set -u`) with `CTID`/`CT_HOSTNAME` unset at harness start | pass | 1st CTID prefill = `900` (`default_ctid()`'s value), 1st hostname prefill = `ai-dev-switchboard` (`$DEFAULT_CT_HOSTNAME`); no `nounset` error |
| 5 | `bash -n` / `shellcheck` clean | Automated: ran both directly against `ct/create.sh` | pass | `bash -n ct/create.sh` — no output, exit 0; `shellcheck ct/create.sh` — no output, exit 0 |
| 6 | `set -u` safety of `"${VAR:-$DEFAULT}"` on an unset var | Automated, part of harness runs (loop entered with `CTID`/`CT_HOSTNAME` unset, script has `set -euo pipefail`) | pass | Harness completed without a "unbound variable" error; also isolated microbench (`test_expansion.sh`) confirmed `${CT_HOSTNAME:-$DEFAULT_CT_HOSTNAME}` on an unset var resolves to the default under `set -u` |
| 7 | `default_ctid()` (live `pvesh` call) now runs once per Advanced session instead of once per CTID retry | Automated, same harness — `default_ctid` stub counts its own invocations | pass | Call count = 1 across a 3-iteration CTID retry sequence |
| 8 | Nothing else in `ct/create.sh` changed | `git diff --stat` / manual read of `git diff ct/create.sh` | pass | `1 file changed, 2 insertions(+), 2 deletions(-)` — exactly the two intended `ask()` pre-fill lines (127→128, 140→141 in new file); no other line touched |

All eight cases directly execute the literal diff lines (extracted verbatim
via `sed` from the current `ct/create.sh`, not a re-typed reimplementation),
so they exercise the actual shipped code, not a stand-in for it.

## Regression check
No existing automated test suite in this repo covers `ct/create.sh` (the
project's Python/JS suite under `tests/` — `test_deploy_*`, `test_gitea_*`,
`test_taiga_*`, `test_upload*`, etc. — targets the web-app backend/frontend,
none of which import or invoke `ct/create.sh`). The established verification
bar for this file, per its own history (part 1/part 3 reviews) and
`docs/implementation.md`'s "How to verify locally", is `bash -n` +
`shellcheck`, both run above and clean. `git diff --stat` confirms the
change is isolated to `ct/create.sh`'s two pre-fill lines plus this cycle's
`docs/spec.md`/`docs/implementation.md`; no other file in the repo is
touched, so there is nothing else to regress.

## Defects found
None.

---

## Spec coverage
All four acceptance criteria in `docs/spec.md` are implemented and directly
tested (see table above):
- CTID retry pre-fill carries forward the rejected value — covered (cases 1, 2).
- Hostname retry pre-fill carries forward the rejected value — covered (case 3).
- First-prompt pre-fill unchanged (`default_ctid()` / `$DEFAULT_CT_HOSTNAME`) — covered (case 4).
- `bash -n`/`shellcheck` clean — covered (case 5).

The spec's stated non-goal ("nothing else in `ct/create.sh` changes,"
ollama loop from part 1 untouched) is confirmed by the diff stat (case 8) —
the ollama retry loop does not appear in the diff at all.

## Correctness review (independent re-read of the diff)
Read `git diff ct/create.sh` directly (not just the spec's/implementation's
description of it):

```diff
-        CTID=$(ask "Container ID (must be free):" "$(default_ctid)")
+        CTID=$(ask "Container ID (must be free):" "${CTID:-$(default_ctid)}")
...
-        CT_HOSTNAME=$(ask "Hostname:" "$DEFAULT_CT_HOSTNAME")
+        CT_HOSTNAME=$(ask "Hostname:" "${CT_HOSTNAME:-$DEFAULT_CT_HOSTNAME}")
```

- `${VAR:-$DEFAULT}` is the correct bash operator for this: it substitutes
  `$DEFAULT` only when `VAR` is unset *or* empty, and does not trip
  `nounset` (`set -u`, active at `ct/create.sh:17`) when `VAR` is unset —
  confirmed empirically, not just by citing the bash manual (test case 6).
- Traced backward from line 128/141 to confirm `CTID`/`CT_HOSTNAME` are
  genuinely unset the first time the Advanced-path `else` branch runs: the
  only other assignments to these two variables in the file are at lines
  100-101, inside the mutually-exclusive `if [ "$INSTALL_MODE" = "default" ]`
  branch — so on the Advanced path the first `ask()` call is a true
  first-use, matching the "first iteration unchanged" claim.
- One incidental edge case worth noting (not a defect): if an operator
  clears the input box to an empty string and submits, `${CTID:-...}` /
  `${CT_HOSTNAME:-...}` treats empty the same as unset and falls back to
  the original default on the next pre-fill rather than re-offering the
  empty string. This is standard/expected `:-` behavior, isn't called out
  as a distinct case in `docs/spec.md`'s acceptance criteria, and arguably
  the more useful behavior (there's nothing to "edit" in an empty field);
  not flagged as a finding.
- Both loops' post-`ask()` validation logic (`_valid_hostname`, the
  numeric-range regex, `pct status`) is byte-for-byte unchanged — only the
  `ask()` pre-fill argument differs.

## Security review
No new external input handling, no new shell injection surface (`ask()`'s
return value is unchanged in how it flows into validation and later
`pct`/`whiptail` calls — this diff only changes the *pre-fill* argument
passed *into* `ask()`, which is always one of: a value already produced by
this same `ask()` call in a prior iteration, or the pre-existing
`default_ctid()`/`$DEFAULT_CT_HOSTNAME`). No new secrets, no new
network/host calls. Not applicable beyond what part 3's original review
already covered for these loops.

## Simplicity/scope review
Matches the spec's proposed approach exactly: two one-line changes, no new
functions, no new abstractions, no unrelated cleanup. `git diff --stat`
confirms scope: `ct/create.sh | 4 +-` (2 lines changed, counted as 2
insertions + 2 deletions in unified diff form) plus this cycle's own
`docs/spec.md`/`docs/implementation.md` updates. Nothing else in the repo
touched.

## Findings (most severe first)
None. No must-fix, should-fix, or nit findings.

## Follow-ups (non-blocking)
None.

## Overall verdict
Approve.
