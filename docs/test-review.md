# Test & Review: Headless engine invocation (backlog item 6, sub-spec 6a)

## Scope
Covers `docs/spec.md`'s full acceptance-criteria list for `app/teams.py`'s
`agent_run()` + CLI, `Engine`/`_parse_engine_file()`'s four new `HEADLESS_*`
keys and the reserved `switchboard` name prefix, the tmux-hosted spawn/
tail/cancel/cleanup machinery, and the `engines.d/*.engine` verification
status. This is the fourth and final testing/review round. Round 1 found
and fixed Defect 1 (uncaught `OSError` + rundir leak). Round 2 found and
fixed Defect 2 (wrong ceiling modeled for the `arg`-mode byte cap; fixed by
writing the script to a file). Round 3's review pass found Finding 1
(uncaught exception on malformed-shape translator input), Finding 2 (stale
mechanism docs), and Finding 3 (missing explicit `chmod` on `SVC_USER`-
written files `RUN_USER` must read), plus a Q2 consolidation ask (trim
incident-narrative source comments). **All four are now independently
verified fixed.** No new blocking issue found this round despite deliberate
adversarial re-testing beyond what was asked. **Verdict: approved.**

## Re-verification of round-3 fixes

| # | Item | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Regression suite | `uv run --with pytest python -m pytest tests/ -q`, repeated | pass (see flake note below) | `372 passed` in 5 of 6 full-suite attempts (one flake, investigated — see below; one run cut off by an unrelated tool-harness timeout with zero leftover processes, not a hang) |
| 2 | No pre-existing test modified | `git status --porcelain tests/`, `git diff --stat -- tests/*.py`, `git diff -- app/app.py` re-read in full | pass | only `tests/test_teams_headless.py`/`tests/fixtures/` new/untracked; `app/app.py`'s diff is byte-identical to round 1 (unchanged this round) |
| 3 | **Finding 1 closed at the class level**, not just the 4 original shapes | wrote and ran a 47-case fuzz set against both `_translate_claude`/`_translate_codex` through `_translate_safely()`: deeply nested wrong types (dict-in-list-in-dict with `None`s), wrong scalar types (`int`/`float`/`bool`/`None` where a dict/list was expected), absent keys entirely, empty dicts/lists, unexpected top-level `type` values (`None`, int, list, dict, a made-up string), and the codex-side equivalents (`item`/`error`/`message` fields each tried as `None`/string/list/int) | pass | **0/94** boundary-wrapper calls raised (47 cases × 2 translators); the *raw* `_translate_claude`/`_translate_codex` are still allowed to raise (confirmed several do, by design — the guarantee lives at the boundary, not per-branch) |
| 4 | Finding 1's fix is genuinely load-bearing (revert-and-watch-it-fail) | temporarily reverted `_translate_safely()` to a bare passthrough (no try/except), re-ran the developer's own `test_shape_crash_line_through_the_real_agent_run_path_does_not_raise` (the real-`agent_run()`-path regression test) | fails cleanly without the fix, passes with it restored | reverted version raised the exact `AttributeError` at `app/teams.py:212` through the full real-tmux path, confirming the test isn't vacuous; restored code confirmed byte-identical to pre-revert (`diff` clean) and full suite re-passed (372) afterward |
| 5 | Judge the blanket `except Exception` at the boundary | read `_translate_safely()`'s full contract; checked (a) whether it can swallow a signal that should propagate, (b) whether it can mask *our own* bug vs. the engine's output, (c) whether it affects `ok`/`exit_code` | sound, no changes needed | (a) `except Exception` does not catch `SystemExit`/`KeyboardInterrupt`/`GeneratorExit` (all `BaseException`-only in Python) — real interrupts still propagate correctly; (b) the caught exception's `type(e).__name__: {e}` is preserved verbatim in `error_message` and durably appended to the `.jsonl` log as an `error` event (`docs/story.md`'s own "nothing lost" principle) — a bug in *our* code (e.g. a stray `NameError`) would still show up in the log with a recognizably different signature (`NameError: name 'x' is not defined`) than a shape-mismatch (`AttributeError: 'str' object has no attribute 'get'`), so diagnosability survives the broad catch even though the two aren't type-distinguished in code; (c) `ok`/`exit_code` are sourced entirely from `rc_path` (the wrapping shell's real exit code), never from translation success — a swallowed translation failure degrades event/text quality for that one line only, never silently misreports whether the run itself succeeded |
| 6 | Findings 2/3 verified fixed | (Finding 2) `grep`-confirmed `docs/ADDING_AN_ENGINE.md`/`config/switchboard.env.example` no longer describe the "whole script as one argv element to `bash -lc`" mechanism, now correctly describe the engine's own final `exec()`; `docs/implementation.md`'s "Deviations from spec" section now explicitly names the file-based invocation as a deliberate deviation from spec §2's literal shape. (Finding 3) confirmed `os.chmod(prompt_path, 0o644)`/`os.chmod(script_path, 0o644)` present at the two write sites | pass | both fixed as described |
| 7 | **The strict-umask test genuinely exercises the failure it claims to** | revert-and-watch-it-fail: temporarily removed both new `os.chmod()` calls, re-ran `test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask` in isolation | fails cleanly without the fix (`AssertionError: 0 is not true : run.sh not world-readable: 0o600`), passes with it restored | confirmed non-vacuous; restored code verified byte-identical to the pre-revert file via `diff` |
| 8 | Q2 — is the consolidation sufficient? | re-read `_MAX_ARG_STRLEN`/`_ARG_SCRIPT_OVERHEAD_BYTES`'s comments, `_validate_prompt_size()`'s docstring, `_build_script()`'s docstring, and `agent_run()`'s script-writing block in full | sufficient | all now state current rationale concisely (what's modeled, why it's per-argv-element, why the check is a sound proxy) with no "round 1 did X, round 2 did Y" narrative left in the hot-path code; one single-line pointer remains in `_translate_claude()`'s `user`-branch comment (`docs/test-review.md Finding A`) as a deliberate, cheap breadcrumb to the fuller writeup, not narrative — reasonable to keep. No other patch-on-patch structure found: no dead code, no orphaned parameters, no duplicate/parallel logic paths from earlier rounds. |
| 9 | Verification labels still accurate | independently re-ran `python3 app/teams.py run claude ...` against the **current** code (after Finding 1/3's changes) | pass | `claude` still runs end-to-end cleanly (`ok=True`, real event stream, no error) — Finding 1/3's changes (translation-boundary robustness, explicit chmod) don't touch engine invocation commands or the happy path, as expected; no label needs updating |

### The one flake, investigated further
One of the 6 full-suite attempts this round failed
`test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask` on its
final `results["r"]["ok"]` assertion (the file-permission assertions
earlier in the same test passed even in that run — the fix itself wasn't
in question). I did not accept the developer's "transient contention,
not reproduced" characterization at face value and instead: (a) ran the
same test in isolation 5× via pytest — clean every time; (b) extracted the
test's exact logic into a standalone script and ran it in a tight loop 25×
outside the full suite — **0/25 failures**; (c) re-ran the full suite 3
more times afterward — clean every time (372 passed each). This pattern
(never reproducible in isolation, only ever seen once amid a full ~34s,
80+ real-tmux-session suite) is consistent with genuine environmental
contention, not a logic defect — and this sandbox specifically has other,
unrelated `tmux`/Claude sessions from other projects visibly running
concurrently on the same box (`claude-birdiely`, `claude-ai-dev-switchboard`
sessions, confirmed via `tmux list-sessions` and `ps aux` during this
review), which is a plausible independent source of scheduling contention
this module has no control over. **Non-blocking finding for the writeup**:
`docs/implementation.md`'s "Known limitations" entry attributes the
*original* round-2 flake specifically to "manual, ad-hoc tmux probing... in
this same shell session" — my own observation of the same *symptom class*
happened without any such manual probing on my end, so the note's causal
attribution is narrower than the evidence now supports. Worth broadening
the wording to "environmental/shared-resource contention" generally rather
than the one specific cause identified during round 2's diagnosis — a
wording nit, not something that changes the accepted, non-blocking
disposition of the flake itself.

## Answers to the coordinator's five questions
1. **Finding 1 closed at the class level** — yes, per items 3–4 above: a 47-shape fuzz sweep (94 boundary-wrapper calls) produced zero crashes, and the fix's own regression test was confirmed non-vacuous via revert-and-watch-it-fail.
2. **The blanket `except Exception` judgment** — sound as designed; see item 5's three-part reasoning (doesn't swallow real signals, preserves enough diagnostic detail to distinguish an internal bug from external shape drift, doesn't affect the run's actual success/failure classification).
3. **Findings 2/3 verified, including that the strict-umask test really exercises the failure** — yes, both confirmed fixed; the umask test's genuineness confirmed via revert-and-watch-it-fail (item 7), not just re-reading its assertions.
4. **Is the Q2 consolidation sufficient?** — yes; no further patch-on-patch structure found worth addressing before 6b builds on this module.
5. **Verification labels still accurate** — yes, independently re-confirmed live against the current code this round (item 9); no update needed.

## Spec coverage
Unchanged from round 3's assessment (all acceptance criteria traced to a
passing automated test or an independently-verified manual step) — this
round added no new spec surface, only fixed the three findings and trimmed
comments, all covered by the re-verification above.

## Overall verdict
**Approved.** Four rounds in: Defect 1 and Defect 2 (blocking, testing-pass
failures) and Findings 1–3 plus the Q2 ask (review-pass findings) are all
independently re-confirmed fixed this round, not just re-read against the
developer's own summary — every fix that could reasonably be verified via a
revert-and-watch-it-fail check was verified that way, and Finding 1's
closure was additionally stress-tested against 43 shapes beyond the four
originally found. The one test flake observed is real but non-reproducible
in isolation (0/25) and consistent with genuine shared-environment
contention rather than a defect in this diff; it doesn't warrant another
round, though the "Known limitations" wording could be broadened slightly
(non-blocking, noted above). No further issues found despite deliberately
adversarial re-testing beyond the coordinator's own checklist. This build
cycle is done — hand control back to the product-manager agent for the next
iteration.
