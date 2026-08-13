# Implementation: `fact_check` recall via bounded block matching (sub-spec 6b.1)

## Summary
`fact_check()` (`app/teams.py`) matches claims against a **bounded block**
instead of a single physical line. **This is round 2.** Round 1 (12
lines/1500 chars, blank-line + sentence-terminal delimiting only) shipped
with a measured 6/6 self-authored recall benchmark and was blocked in
testing: the reviewer's own five adversarial attacks (heading + unrelated
body, terse tight-list bullets, fenced code adjoining prose, terms 12 lines
apart in unrelated filler, a heading-only claim) all produced a false
`found: True`. The coordinator's own correction (`docs/spec.md` "Round-1
correction", `bbf2316`) identified two root causes — the window was ~6x too
wide, and blank lines/sentence-terminal punctuation aren't the only
structural boundaries — and relaxed the recall target ("A recall figure
below the 5/6 target is an acceptable outcome now. Precision is the
property that must hold.").

**This round**: bounds tightened to `_GROUNDING_BLOCK_MAX_LINES = 3`,
`_GROUNDING_BLOCK_MAX_CHARS = 400`; a block now also ends at the start of a
heading, list item, block quote, or table row (even mid-run), and fenced
code content is excluded from matching entirely. All five of the
reviewer's adversarial cases now return `found: False` (verified by
running their own scratch script directly, then porting all five verbatim
into the permanent suite as `ReviewerAdversarialBlockPrecisionTests`).
Recall is honestly re-measured on two batches of six claims each, written
and committed to **before being run even once** — see "Six-claim
benchmark, round 2" below for the real numbers, which are materially lower
than round 1's self-measured 6/6 (as the coordinator predicted). The
original motivating defect (a single sentence hard-wrapped across two
lines, `docs/ARCHITECTURE.md`'s own `SVC_USER` example) is still fixed.

**Judgment call made and reported, not silently decided**: recall did not
reach zero, precision holds against every adversarial case tried
(reviewer's five plus 6b's own full suite), and the original motivating
defect is still solved — so this round does **not** invoke the "stop and
report, fall back to 6b's single-line matcher" escape hatch. That
determination is reported explicitly here, with the honest numbers, rather
than assumed; see "Should this have been a stop-and-report instead?" below.

## Root cause (round-1 regression)
The round-1 sentence-terminal-punctuation rule only ends a block when the
**previous line's own text** happens to end in `.`/`!`/`?`. It was built
and verified against exactly one shape (full-sentence prose that does end
in periods — 6b's own regression test, and this repo's own
`docs/ARCHITECTURE.md` bullets, which happen to all end in periods) and
provided no protection against the much larger class of real Markdown
constructs that don't: headings (`## Foo`), terse/non-sentence bullet items
(`- foo config`), and fenced code (code is not prose and essentially never
ends a line in terminal punctuation). Combined with a 12-line/1500-char
bound wide enough to carry no co-occurrence signal at all (the reviewer's
`terms_12_lines_apart_in_unrelated_filler` case has no Markdown structure
whatsoever and cannot be fixed by better structure parsing alone), this let
genuinely unrelated adjacent content merge into one matchable block and
produce false confirmations. Full analysis: `docs/test-review.md`
("Blocking finding"), `docs/spec.md` ("Round-1 correction").

## Changes by file
- `app/teams.py` (grounding section only, `app/app.py` untouched):
  - `_GROUNDING_BLOCK_MAX_LINES`: `12` → **`3`**. `_GROUNDING_BLOCK_MAX_CHARS`:
    `1500` → **`400`**. Still fixed, non-env-configurable.
  - `_GROUNDING_SENTENCE_END_RE` now also treats `:` as sentence-terminal
    (`docs/spec.md` "Round-1 correction"), in addition to `.`/`!`/`?`.
  - New `_GROUNDING_HEADING_RE` (`^#{1,6}(\s|$)`), `_GROUNDING_LIST_MARKER_RE`
    (`^(?:[-*+]|\d+\.)\s`), and `_grounding_structural_kind(stripped)` —
    classifies a non-blank, non-fence line as `"heading"`, `"list"`,
    `"quote"` (`>` prefix), `"table"` (`|` prefix), or `None`.
  - `_iter_grounding_blocks(content)` — rewritten to add, on top of the
    unchanged blank-line and sentence-terminal rules:
    - **Structural markers are a hard boundary "even mid-run"**: a list
      item, block quote, or table row line always flushes whatever came
      before it and starts a fresh block — but that fresh block can still
      accumulate its own non-marker wrapped-continuation lines via the
      existing rules (so a bulleted sentence's own wrap still joins).
    - **Headings are additionally excluded from matching entirely**, not
      just prevented from merging forward — see "Deviations from spec"
      below for why this goes beyond spec's literal text and was still
      necessary.
    - **Fenced code (` ``` `/`~~~`) delimiters are a hard boundary in both
      directions**, and every line between a pair of delimiters (including
      an unclosed fence running to EOF) is skipped entirely, never added
      to any block — mirrors `_extract_headings()`'s own pre-existing
      fence-toggle style for consistency.
  - `fact_check()` itself: unchanged this round (still iterates
    `_iter_grounding_blocks()`, same conjunctive substring rule).
- `tests/test_teams_grounding.py`:
  - 8 new Tier-1 pure tests in `GroundingBlockConstructionTests` covering
    heading exclusion, list/quote/table hard-boundary behavior, a list
    item's own wrapped continuation still joining, fenced-code exclusion,
    and an unclosed fence.
  - 1 new real-file test in `FactCheckBlockMatchingTests`
    (`test_wrap_boundary_claim_matches_the_real_docs_architecture_md`) —
    the spec's own wrap-boundary acceptance criterion, verified against
    the real file, not just a synthetic mirror of it.
  - `SixClaimBenchmarkTests` **replaced** — see "Six-claim benchmark,
    round 2" below.
  - New `ReviewerAdversarialBlockPrecisionTests` — the reviewer's five
    adversarial cases, ported verbatim (same content, same claims, same
    assertions) from their scratch script into the permanent suite, per
    the coordinator's explicit instruction.
  - No test from round 1 that was already reviewed/approved (the two
    forced test-body edits, the round-1 Tier-1/skipped/proc-unavailable
    tests) was touched again this round.

## Six-claim benchmark, round 2
Per the coordinator's explicit instruction, this round's benchmark was
**written and committed to before being run even once**, in two batches of
six, both drawn from `docs/ARCHITECTURE.md` sections round 1's own
(self-authored, after-the-fact) benchmark hadn't drawn from:

**Batch A — vocabulary drift** (natural paraphrase, swapping the source's
own key nouns for synonyms):

| Claim (paraphrase) | Old (6b) | New (6b.1, round 2) |
|---|---|---|
| "`_reap_dead_state` runs on every `/status` call and **heals** session state..." | False | False |
| "the host-start.sh URL file used to only get written after the full startup sequence succeeded..." | False | False |
| "`run_startup_watch` always writes or clears `URL_FILE` when it finishes..." | False | False |
| "a failed confirm on a name collision leaves the upload staging directory in place..." | False | False |
| "generalizing engine handling into `engines.d` engine files collapsed two separate implementations..." | False | False |
| "per-project terminals always bind only to **loopback** no matter what `PUBLISH_MODE` is set to" | False | False |

**0/6 old, 0/6 new.** Every miss here is a **vocabulary** mismatch
(`"heals"` vs. the source's `"self-heals"`, `"loopback"` vs. `"127.0.0.1"`,
etc.), not a line-boundary problem — `fact_check()` has never done fuzzy or
synonym matching (explicit 6b non-goal) and block-widening cannot change
that. Kept in the permanent suite specifically so this stays a clearly
separate, already-understood limitation rather than being re-litigated as
a block-boundary regression later.

**Batch B — same vocabulary** (paraphrased grammar, but the source's own
distinctive identifiers kept verbatim — closer to the original reviewer
benchmark's own style):

| Claim (paraphrase) | Old (6b) | New (6b.1, round 2) |
|---|---|---|
| "`run_startup_watch` always writes-or-clears `URL_FILE` when it's done, success or timeout" | False | False |
| "the already running fast path checks whether the cached URL predates the session it's attached to and drops it if so" | False | **True** |
| "a failed confirm leaves staging in place so the Back to review button can retry the same token" | False | False |
| "generalizing engine handling into `engines.d` collapsed two separate implementations of handle a startup prompt then look for a URL into one shared tested behavior" | False | False |
| "`_reap_dead_state` is called on every `/status` and this self-heals as soon as the underlying tmux session actually ends" | False | False |
| "per-project terminals bind to `127.0.0.1` only regardless of `PUBLISH_MODE`" | False | **True** |

**0/6 old, 2/6 new.** A real, honest improvement over 6b's single-line
matcher on these exact six claims — just far more modest than round 1's
self-measured 6/6. The three longest-paragraph claims (`run_startup_watch`,
the failed-confirm one, the engines-config one) each need support spanning
more than 3 lines / 400 chars of real prose and are correctly *not*
matched under the tightened, precision-first bounds — this is the
deliberate, disclosed cost of the round-1 correction, not a bug.

**Combined: 0/12 old, 2/12 new.** Both counts are pinned via `assertEqual`
in `SixClaimBenchmarkTests` (not a `>=` threshold) so a future change to
the matcher has to consciously update this record rather than silently
drift it either direction.

**The one thing that must still work, and does**: the actual motivating
defect this whole sub-spec exists to fix — one sentence hard-wrapped across
two lines, `docs/ARCHITECTURE.md`'s own `SVC_USER` bullet — still matches
against the real file
(`test_wrap_boundary_claim_matches_the_real_docs_architecture_md`), with
its `text` showing the correctly space-joined sentence.

### Should this have been a stop-and-report instead?
`docs/spec.md`'s own "Round-1 correction" and the coordinator's message
both offer an explicit fallback: keep 6b's single-line matcher, raise
recall in 6c at the prompt level instead. I considered this and did **not**
take it, for a specific, reportable reason: the corrected design (a)
robustly closes the round-1 regression (all five reviewer attacks plus 6b's
full existing adversarial suite hold `found: False`, verified by running
the reviewer's own script directly, not just re-deriving it), (b) still
demonstrably solves the one defect the sub-spec was written to fix (the
two-line hard-wrap case, against the real file, not a synthetic stand-in),
and (c) measurably improves recall on the honestly-authored benchmark (0/6
→ 2/6 on Batch B), even though the improvement is modest. None of the
three conditions that would make this "the block approach is wrong" hold:
precision doesn't regress, the original defect isn't left unfixed, and
recall isn't literally zero. If the coordinator's own bar for "useful
recall" is stricter than "measurably better than zero, without any
precision cost," that's a legitimate call to make — but it's the
coordinator's call to make with the honest numbers in front of them, not
something to decide unilaterally by either shipping an inflated headline
or silently reverting the whole feature.

## Key decisions / tradeoffs
- **Headings are excluded from matching entirely, not merely prevented
  from merging forward** — this is a deliberate extension beyond
  `docs/spec.md`'s own literal text (which lists a heading only as one of
  four *boundary*-triggering structural elements, distinct from the
  fenced-code case it explicitly calls "excluded from matching entirely").
  It was still necessary: the reviewer's own `test_heading_only_claim_matches_unrelated_following_paragraph`
  attack uses a claim that is fully satisfied by the **heading's own text
  alone** (`"## Setup database configuration"` contains every term of the
  claim `"setup database configuration"` by itself) — merely keeping the
  heading from merging *forward* with the next paragraph doesn't stop the
  heading's own one-line block from independently satisfying the claim.
  Full exclusion does, and is semantically defensible on the same grounds
  spec already uses for fenced code: an ATX heading is a title, not a
  prose assertion, and (unlike a list item or block quote) never has a
  legitimate "wrapped continuation" to preserve by staying matchable.
  **Flagging one factual discrepancy found while verifying this**:
  `docs/test-review.md`'s own attack table records the *old* (6b,
  single-line) matcher's result for this exact case as `False (correct)`;
  independently re-measuring 6b's original single-line matcher against
  this exact content and claim gives `True` (the heading line alone
  satisfies all three terms under simple per-line matching too, with no
  block logic involved at all) — reproduced live, not assumed; see
  "Independent re-verification" below. This doesn't change what needed to
  be built (the coordinator's requirement that all five return `False` is
  unconditional, and heading-exclusion is the correct fix either way), but
  it means this specific case isn't purely a *new* 6b.1 regression the way
  the other four are — it's a pre-existing 6b characteristic (a claim
  quoting a heading verbatim was already "confirmable" by the heading
  alone) that this round also happens to close as a side effect of the
  fix required for the other four.
- **List items, block quotes, and table rows are boundaries but stay
  matchable** (unlike headings) — a marker line's own non-marker
  continuation lines still accumulate normally, which is what keeps a
  wrapped bullet's own recall win (the original `SVC_USER` motivating
  example) working. Only the *next* marker line is a hard stop, matching
  spec's literal "starts a new block even mid-run" language exactly.
- **`:` added to `_GROUNDING_SENTENCE_END_RE` unconditionally**, not only
  "before a list" as spec's parenthetical suggests — the list-marker rule
  already independently ends a block whenever the *next* line is itself a
  marker, regardless of what the *previous* line ended with, so a
  conditional "only before a list" reading would be redundant with that
  rule in the cases it's meant to cover, while an unconditional reading is
  strictly more conservative (ends blocks more eagerly) in the cases it
  isn't. Precision-safe by construction; simpler than adding lookahead.
- **Fence handling mirrors `_extract_headings()`'s own existing
  toggle-on-three-char-prefix style** (`stripped[:3] in ("```", "~~~")`)
  rather than inventing a new convention — matches this module's own
  established precedent for the identical problem (fenced-code detection),
  per this role's own "convention matching" discipline.

## Deviations from spec
1. **Heading full-exclusion** (see "Key decisions" above) — spec's literal
   text treats headings as boundary-only, on par with list items/quotes/
   tables; empirically, boundary-only treatment is insufficient to satisfy
   the reviewer's own required-to-pass attack #5, so headings additionally
   get the same "excluded from matching entirely" treatment spec already
   specifies for fenced code. Reported here rather than silently decided.
2. **`:` as an unconditional (not list-conditional) terminal-punctuation
   trigger** — see "Key decisions" above; a conservative simplification,
   not a loosening.
3. Round 1's own deviation record (the sentence-terminal-punctuation rule
   itself, and the two forced pre-existing test-body edits) is unchanged
   and carried forward — both were independently re-verified as legitimate
   by the coordinator and the reviewer this round (`docs/test-review.md`
   check #5: the reviewer reproduced the "63 passed, 2 failed" baseline
   exactly against a clean copy of the pre-6b.1 test file). Per the
   coordinator's explicit instruction, they are kept, unmodified again
   this round.
4. Everything else is unchanged from round 1's own "Deviations from spec"
   #3 (discovery, digest, caps, read path, `app/app.py` all untouched).

## Known limitations
- **Recall is now genuinely modest for claims whose support spans more
  than ~3 lines / 400 characters of real prose** — a deliberate,
  reported-not-hidden cost of the round-1 correction. `docs/test-review.md`
  already flagged the real `run_startup_watch` paragraph in
  `docs/ARCHITECTURE.md` (17 physical lines, one bullet) as an example that
  won't be recoverable under these bounds; this round's own Batch B
  confirms the same pattern on three of six fresh claims. 6c's own
  real-usage recall check (per `docs/spec.md`'s "Open questions") should
  go in with this expectation set correctly, not assuming round 1's
  since-corrected 6/6.
- **Vocabulary mismatch is untouched and unaffected by this sub-spec** —
  `fact_check()` remains a literal, non-fuzzy substring matcher (explicit
  6b non-goal); Batch A's 0/6 is evidence of this pre-existing limitation,
  not a regression.
- **`_significant_terms()`'s possessive-apostrophe tokenization quirk**
  (flagged by the reviewer: `"row's"` tokenizes as one token that never
  matches the source's `"row talks"`) is pre-existing 6b behavior, out of
  this cycle's scope, not touched.
- 6b's own already-documented limitations (negated lines producing a
  misleading `found=True`; a real filesystem symlink at any candidate path
  being categorically unusable even in-bounds) are unchanged.
- Setext headings (`===`/`---` underlines) and indented code blocks are
  not specially handled (`docs/spec.md`'s own "Open questions" judges both
  rare enough to defer; both fail *closed* — they split rather than merge,
  which is the safe direction).

## Independent re-verification performed this round
- Ran the reviewer's own scratch script
  (`/tmp/claude-1001/.../scratchpad/test_reviewer_adversarial.py`)
  directly, unmodified, against the round-1 code first (reproduced all
  five failures exactly, confirming the starting point) and then against
  the round-2 fix (all five pass).
- Independently re-measured 6b's original single-line matcher against the
  reviewer's own attack #5 content/claim (see "Key decisions" above) —
  found a discrepancy with `docs/test-review.md`'s own table for that one
  row, reported rather than silently corrected or ignored.
- Wrote both benchmark batches to a scratch script and ran each exactly
  once before recording results in this document or in the test file.

## Verification status
| Check | Command | Result |
|---|---|---|
| Reviewer's scratch adversarial script, against round-1 code (baseline) | `pytest .../test_reviewer_adversarial.py -v` | 5 failed (reproduced exactly) |
| Reviewer's scratch adversarial script, against round-2 code | `pytest .../test_reviewer_adversarial.py -v` | **5 passed** |
| Grounding test file alone | `pytest tests/test_teams_grounding.py -q` | 104 passed |
| Full suite, 5 consecutive runs this round | `uv run --with pytest python -m pytest tests/ -q` | **476 passed**, all 5 runs, no flake this round |
| `app/app.py` untouched | `git diff --stat -- app/app.py` | empty diff |
| Pre-existing test bodies touched | `git diff -- tests/test_teams_grounding.py` | only the two round-1-forced edits (independently re-verified legitimate by the coordinator/reviewer) remain from round 1; this round only adds new test classes/methods, doesn't modify any existing assertion |
| Syntax/compile | `python3 -m py_compile app/teams.py tests/test_teams_grounding.py` | clean |
| CLI, real wrap-boundary claim, real repo tree | `python3 app/teams.py fact-check "$(pwd)" "app.py runs as SVC_USER, an unprivileged system account with no login shell"` | `found: true`, `docs/ARCHITECTURE.md:5`, joined text shown |

## How to verify locally
```bash
# Grounding test file only
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_teams_grounding.py -v

# Full suite (run more than once)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# The reviewer's own adversarial script, directly:
/home/dev/.local/bin/uv run --with pytest python -m pytest \
  /tmp/claude-1001/-home-dev-projects-ai-dev-switchboard/4f087ac5-0b6c-490d-9c0b-c9f5049e0818/scratchpad/test_reviewer_adversarial.py -v

# Confirm app/app.py is untouched:
git diff --stat -- app/app.py

# CLI, against this repo's own tree:
export TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x
export ENGINES_DIR=$(pwd)/engines.d PROJECTS_DIR=/tmp/scratch-projects-6b1
python3 app/teams.py fact-check "$(pwd)" "app.py runs as SVC_USER, an unprivileged system account with no login shell"
```
