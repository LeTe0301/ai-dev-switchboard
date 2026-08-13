# Test & Review: `fact_check` recall via bounded block matching (sub-spec 6b.1)

## Scope
Testing pass only. A blocking precision defect was found — new, genuine
false positives introduced by the block-widening this sub-spec exists to
ship — so per process this stops at the testing pass and does not proceed
to the independent review pass (spec-to-code traceability / correctness /
security / simplicity). Route back to the developer.

## What I independently re-verified from the coordinator's own checks
All of the following were re-run by me this session, not taken on trust:

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Grounding test file alone | `pytest tests/test_teams_grounding.py -q` | **89 passed** |
| 2 | Full suite, 3 consecutive runs | `uv run --with pytest python -m pytest tests/ -q` | **461 passed**, all 3 runs, no flake (could not reproduce the `test_teams_headless.py` flake the developer/coordinator saw either) |
| 3 | `app/app.py` untouched | `git diff --stat -- app/app.py` | empty diff |
| 4 | Exactly one pre-existing test file touched, and exactly two pre-existing test *bodies* | `git diff -- tests/test_teams_grounding.py` (read in full) | confirmed: `import io`, two modified assertions (`test_matches_capped_at_max_matches`, `test_fact_check_finds_passage_present_in_full_content_but_truncated_out_of_digest`), five new test classes appended |
| 5 | The "63 passed, 2 failed" pre-change baseline claim | Restored the **unmodified** old test file (from `4925c49`'s parent) verbatim against the *new* `app/teams.py`, ran it, restored the working tree | **Reproduced exactly**: 63 passed, 2 failed, the identical two tests, for the identical reason (block-first-line semantics vs. the old hardcoded line/count expectations). This independently confirms the two test-body edits are a faithful adaptation to spec's own redefined "Result shape," not a weakened assertion. |
| 6 | No path-based `realpath()` fallback for `/proc`-unavailable | `grep -n realpath app/teams.py`, read every call site | Confirmed: only `_under_workdir`'s pre-check (pre-existing, always followed by the fd-based re-check), the fd-based `/proc/self/fd` re-check itself, and `workdir_real` setup. The `/proc`-unavailable branch returns `(None, "proc_unavailable")` and stops — no fallback re-derives containment from the path string. |
| 7 | `skipped` list / `/proc`-unavailable follow-ups | ran as part of the full 89-test suite (`GroundingSkippedListTests`, `GroundingProcUnavailableTests`) | pass |

## My own six-claim (extended to seven) recall benchmark
Per the brief, I did not reuse the developer's reconstruction. I wrote seven
fresh true claims about this repo, independent of both the developer's and
the coordinator's wording, and ran them against `load_grounding(REPO_ROOT)` +
`fact_check()` directly:

| Claim | Found |
|---|---|
| "app.py can run tmux, ttyd, and code-server as RUN_USER via narrow sudoers rules" | True |
| "the folder upload wizard's hand-off script does an atomic mkdir, cp -a, and chown" | True |
| "RUN_USER holds the engine credentials including claude's own login" | False |
| "in tailscale mode per-project terminals are published via tailscale serve --set-path" | True |
| "the host-control row's dedicated SSH key can run exactly three whitelisted scripts" | False |
| "run_startup_watch always writes or clears URL_FILE when it's done, success or timeout" | False |
| "a failed confirm on a name collision leaves staging in place so Back to review can retry" | True |

**4/7.** This lands close to the coordinator's own 4/7, not the developer's
self-authored 6/6 — I agree with the coordinator's read that 6/6 is weak
evidence (a benchmark authored by the same person who then measures their
own implementation against it). Diagnosis of the three misses:
- **"RUN_USER holds... including claude's own login"** — vocabulary
  mismatch (`_significant_terms` tokenizes `"including"`/`"holds"`, neither
  literal word appears in the source, which says "is where... engine
  credentials (e.g. `claude`'s own login)"). Same category the developer
  already identified (`configuration`/`config`).
- **"the host-control row's dedicated SSH key..."** — near-exact match to
  the source sentence, but fails because `_significant_terms` tokenizes the
  possessive `"row's"` as one token (the token regex includes `'`), which
  never appears as a substring of the source's `"row talks"`. This is a
  **pre-existing tokenization quirk in `_significant_terms()`**, unrelated
  to this cycle's diff — not caused by 6b.1, just newly visible because
  block matching makes near-misses like this the dominant failure mode now.
- **"run_startup_watch always writes or clears URL_FILE when it's done..."**
  — genuinely structural, and worth flagging even though non-blocking: the
  real supporting bullet in `docs/ARCHITECTURE.md` (the host-control
  in-memory-state paragraph, lines 65–81 as of this writing) is **17
  physical lines of one single bullet** with no sentence-terminal
  punctuation until near the end, so it exceeds
  `_GROUNDING_BLOCK_MAX_LINES = 12` and is split mid-sentence, right
  between `"run_startup_watch() ... is the fix:"` and `"it captures
  opportunistically..."`. This is one long paragraph, not two — a case
  spec's own non-goals section ("a claim whose support genuinely spans two
  separate paragraphs remains unmatched — that is correct conservative
  behaviour") doesn't quite cover, since this is one paragraph split only
  by the fixed bound. Not blocking (the bound is deliberately fixed and the
  spec's actual bar is the 5/6 benchmark, not "every claim"), but it is
  concrete evidence that `12`/`1500` are already binding in this repo's own
  real prose, not just a theoretical concern.

Both 4/7 numbers (mine and the coordinator's) clear the spec's own ">= 5 of
6" bar only if you round generously; taken literally as a fraction, 4/7 ≈
0.57 vs. the spec's implicit ≥ 0.83. I'd flag the **"6/6" headline in
`docs/implementation.md`'s Summary as overstated** relative to what two
independent fresh benchmarks measured — not blocking by itself (the encoded
test uses a `>= 5` threshold against its *own* reconstructed claims, which
does pass, and a self-authored benchmark passing is still weak-but-real
signal), but the coordinator's instinct to distrust the self-authored 6/6
was correct and should be reflected in the record rather than the 6/6
number standing as the reported result.

## Blocking finding: new false positives from block-widening (must-fix)

Per the brief's explicit direction ("Attack it: claims whose terms scatter
across a genuinely unrelated block, claims matching a heading rather than
substance, claims matching inside fenced code blocks, terms co-occurring in
a bullet list of unrelated items. Any false positive is blocking"), I wrote
five adversarial attacks targeting exactly those four categories, using the
same `_sf`/`_synthetic_grounding` test helpers this project's own suite
uses. **All five produced a false `found: True`.** I verified each is a
genuine regression (not pre-existing 6b behavior) by also running each
through 6b's old single-line matcher, reproduced verbatim — all five
correctly returned `False` under the old matcher.

Test code: `/tmp/claude-1001/-home-dev-projects-ai-dev-switchboard/4f087ac5-0b6c-490d-9c0b-c9f5049e0818/scratchpad/test_reviewer_adversarial.py`
(scratch, not committed — role is not to fix, only to report). Run with:
`python3 -m pytest test_reviewer_adversarial.py -v` (needs `APP_DIR` on
`sys.path`, see the file's own header, copied from
`tests/test_teams_grounding.py`'s own import setup).

| # | Attack | Synthetic content | Claim | `_iter_grounding_blocks` verdict | Old (6b) matcher | New (6b.1) matcher |
|---|---|---|---|---|---|---|
| 1 | Heading (no terminal punctuation) merges with unrelated body | `"## Widget rotation subsystem\nThe gadget storage subsystem handles persistence for unrelated items."` | "widget rotation gadget storage" | one block, lines 1-2 | `False` (correct) | **`True` (false positive)** |
| 2 | Tight bullet list, no terminal punctuation on any item, three unrelated topics | `"- widget rotation config\n- gadget storage config\n- unrelated topic zebra migration\n"` | "widget rotation zebra migration" | one block, lines 1-3 | `False` (correct) | **`True` (false positive)** |
| 3 | Fenced code block adjoining unrelated prose, no blank line | ```` "The deploy script does the following\n```\nrun_as_root()\ngrant_secret_access()\n```\nfor the unrelated widget subsystem\n" ```` | "deploy script grant_secret_access widget subsystem" | one block, lines 1-6 | `False` (correct) | **`True` (false positive)** |
| 4 | Terms 12 lines apart in wholly unrelated filler, at exactly the line-count bound | 12 lines of `"filler line N with no punctuation at end"`, `"alpha marker..."` at line 1, `"omega marker..."` at line 12 | "alpha marker omega marker" | one block, lines 1-12 | `False` (correct) | **`True` (false positive)** |
| 5 | Heading-only claim matches an unrelated following paragraph | `"## Setup database configuration\nUnrelated content about deployment follows here without periods\n"` | "setup database configuration" | one block, lines 1-2 | `False` (correct) | **`True` (false positive)** |

### Root cause
The sentence-terminal-punctuation rule (`_GROUNDING_SENTENCE_END_RE`,
"Deviations from spec" #1 in `docs/implementation.md`) only ends a block
when the **previous line's own text** happens to end in `.`/`!`/`?`. It was
built and measured against exactly one shape: full-sentence prose lines
that do end in periods (6b's regression test, and this repo's own
`docs/ARCHITECTURE.md` bullets, which the developer verified all happen to
end in periods). It provides **no protection at all** against the much
larger class of real Markdown constructs that don't end lines in terminal
punctuation: headings (`## Foo`), terse/non-sentence bullet items (`- foo
config`), and fenced code blocks (whose lines are code, not prose, and
essentially never end in `.`/`!`/`?`). For all of these, the *only* thing
stopping unrelated adjacent content from merging is a blank line — which
`docs/spec.md`'s own literal "delimited by blank lines" rule already
assumed would be sufficient, and which the developer's own testing (rightly)
found isn't sufficient for the *specific* case of sentence-ending prose
without a blank line, but the fix doesn't generalize to these other three
common shapes. Attack #4 additionally shows the raw bound is exploitable on
its own even with zero markdown structure involved: 12 lines of otherwise
totally unrelated filler is still well within `_GROUNDING_BLOCK_MAX_LINES`,
so two markers with no relationship to each other "confirm" a claim that
they're related, purely because they're within the fixed window.

This is exactly the risk `docs/spec.md`'s own "precision/recall tradeoff"
section names ("Widening the unit does make accidental co-occurrence more
likely... If any adversarial claim starts matching, the bounds are wrong
and the fix must not ship on a recall improvement alone") and exactly what
the coordinator's brief asked me to attack. A lead using this tool would see
`found: True` and treat a claim as **verified** — for `fact_check`'s stated
purpose (guarding a lead loop against confidently wrong claims), a false
confirmation triggered by a heading, a terse bullet list, or a code fence is
not a hypothetical edge case; headings, terse lists, and fenced code blocks
are extremely common in real project documentation (README files, this
project's own `AGENTS.md`/`CLAUDE.md` indirection target, any project 6c
will eventually be pointed at) — this is not confined to contrived prose.

### Why this blocks
Per the brief: "Precision is the blocking property... Any false positive is
blocking." All five attacks are false positives, all five are demonstrated
regressions introduced by this cycle's own diff (not pre-existing 6b
behavior), and all five sit squarely inside the four attack categories the
coordinator explicitly asked me to test. This is a must-fix, not a nit or
should-fix.

## Test-case table (spec.md acceptance criteria)

| # | Acceptance criterion | Result | Evidence |
|---|---|---|---|
| 1 | Six-claim benchmark encoded, ≥5/6 | Encoded test passes (`>= 5` against developer's own reconstructed claims) | but see "My own benchmark" above — real recall on two independent fresh benchmarks is 4/7, not 6/6; encoded test itself is not wrong, its headline framing in `docs/implementation.md` is optimistic |
| 2 | 6b's existing adversarial claims still `found: False` | **Pass** | 89/89 grounding tests pass, includes `FactCheckPureTests`/`PostReviewRegressionTests` unmodified |
| 2b | *(beyond spec, per this cycle's brief)* New adversarial attacks (unrelated block, heading, code fence, unrelated bullet list) | **FAIL — blocking** | 5/5 of my own attacks produce `found: True`; see table above |
| 3 | Wrap-boundary claim on `docs/ARCHITECTURE.md` matches, joined text shown | Pass | `FactCheckBlockMatchingTests`, plus my own CLI re-run |
| 4 | Cross-paragraph claim does not match | Pass | `test_claim_spanning_two_different_paragraphs_does_not_match` |
| 5 | Run > `_GROUNDING_BLOCK_MAX_LINES` split, proven by straddling claim | Pass | `test_claim_straddling_a_max_lines_split_does_not_match`; independently reproduced the real-world version of this (the `run_startup_watch` paragraph) — see benchmark diagnosis above |
| 6 | Wrap-boundary space insertion (`anunprivileged` fails, `an unprivileged` matches) | Pass | `test_wrap_boundary_space_insertion_fused_token_fails_natural_phrase_matches` |
| 7 | `line`/`file_line`/`end_line` correctness | Pass | `test_line_file_line_and_end_line_point_at_the_blocks_first_and_last_lines` |
| 8 | `skipped` populated for in-bounds symlink, empty for clean project | Pass | `GroundingSkippedListTests`, part of 89-test run |
| 9 | `/proc`-unavailable detected/surfaced, no path-based fallback | Pass | `GroundingProcUnavailableTests`; independently confirmed no fallback exists by reading every `realpath()` call site |
| 10 | Full suite green, several runs; no pre-existing test modified beyond what's forced; `app/app.py` untouched | Pass (with the two forced test-body edits independently re-verified as forced, not a loosening) | 461/461 × 3 runs; `git diff --stat -- app/app.py` empty; old-test-file-against-new-matcher reproduction (63 passed/2 failed, identical two) |

## Regression check
`uv run --with pytest python -m pytest tests/ -q` — 461 passed, 3
consecutive runs this session, no flake observed (including
`test_teams_headless.py`, which both the developer and coordinator flagged
as an occasional unrelated flake — clean all 3 times for me).

## Overall verdict: **Blocked**

Must-fix before this can proceed to a review pass:

1. **Block-boundary rule doesn't generalize past "ends in a period."**
   Headings, terse/non-sentence bullets, and fenced code blocks all merge
   with unrelated adjacent content because none of them reliably end a line
   in `.`/`!`/`?`, and the bound alone (12 lines / 1500 chars) is not tight
   enough to prevent this on its own (attack #4 needs no markdown structure
   at all). This needs either: additional block-ending triggers (heading
   lines, fenced-code-block boundaries, list-item-start lines) alongside the
   existing sentence-terminal rule, or a different mechanism entirely.
   `docs/spec.md`'s own "Open questions" already anticipates this
   possibility: *"If both cannot hold simultaneously, stop and report —
   that would mean the block approach is wrong, and it should not be
   papered over by loosening one of the bounds."* I'm not asserting the
   block approach itself is wrong — narrowing the delimiting rule further
   (rather than loosening a bound) looks like the right next move — but the
   current single heuristic is demonstrably incomplete against exactly the
   attack surface this round's brief asked me to test, and per spec's own
   words this is a "stop and report," not a ship-and-follow-up.
2. Once a fix is in place, precision must be **re-attacked** with these
   same five cases (or equivalents) plus anything else in the same spirit
   before this round can be re-submitted — a fix narrowly targeting these
   five literal inputs without addressing the underlying gap (headings /
   terse lists / code fences / bare bound) would not actually close it.

Non-blocking, carry forward once the above is fixed:
- The `docs/implementation.md` "6/6" headline should be corrected or
  caveated — two independent fresh benchmarks (mine, the coordinator's)
  both measured 4/7, not 6/6, on freshly-written claims. The encoded test's
  `>= 5` threshold is fine and shouldn't change; the narrative claim around
  it is optimistic.
- The `"row's"` apostrophe-tokenization miss suggests `_significant_terms()`
  might be worth a lightweight look in a future cycle (stripping trailing
  `'s`), but this is pre-existing 6b behavior, not part of this diff, and
  outside this cycle's scope — noted for the record, not a finding against
  this diff.
- `_GROUNDING_BLOCK_MAX_LINES = 12` is already binding against this repo's
  own real prose in at least one case (the `run_startup_watch` paragraph) —
  worth keeping in mind when 6c does its own real-usage recall check, per
  spec's own open question about what to do if the bounds and recall can't
  both hold.

Full review pass (spec-to-code traceability beyond the above, correctness
read-through of the diff for logic/security/simplicity) deliberately not
done this round — per process, a blocked testing pass routes straight back
to the developer without spending review effort on known-broken work. Once
the block-boundary rule is fixed and re-attacks with an adversarial set at
least as broad as this round's pass clean, resubmit for both passes.
