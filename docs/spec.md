# Spec: `fact_check` recall — bounded block matching (sub-spec 6b.1)

## Summary

`fact_check()` shipped in 6b (`926eff0`) with a measured **2-of-6** hit rate on
realistic true claims about this repo. It never returns a false positive — the
design priority, and it holds — but recall is too weak for 6c's lead loop to
depend on. This sub-spec raises recall by changing the matching *unit* from a
single line to a bounded block, without weakening the precision guarantee.

Small, focused, one function plus two logged follow-ups in the same module.
No new architecture, no new dependency, no UI.

## Why single-line matching fails

The current matcher (`app/teams.py`, `fact_check`) requires every significant
term of a claim to appear on **one physical line**:

```python
for lineno, line in enumerate(f["content"].splitlines(), start=1):
    if all(term in line.lower() for term in terms):
```

The reviewer measured this against six claims a lead would plausibly make
about this repo. Four of the six failures had nothing to do with the claims
being false, or with the terms being absent — they were `docs/ARCHITECTURE.md`'s
own hard-wrapped bullet prose splitting one sentence across two lines:

```
- **`app/app.py`** runs as `SVC_USER` (default `switchboard-svc`), an
  unprivileged system account with no login shell of its own.
```

A claim about `app.py` running as an unprivileged system account is fully
supported by that sentence and cannot match it, because no single line
contains all the terms.

**The semantic intent was never "same line".** It was "these terms co-occur in
a small contiguous region of the document" — close enough together that
co-occurrence is meaningful rather than coincidental. A physical line is a
crude, layout-dependent proxy for that region, and this repo's wrap width
happens to break it.

## Goals

- Match against a **bounded block** — consecutive non-blank lines joined into
  one matchable unit — instead of a single physical line.
- Keep the strict conjunctive all-terms rule exactly as-is. No scoring, no
  fuzzy matching, no nearest-weak-match fallback, no partial credit.
- Keep precision at 100% on the existing adversarial claim set.
- Report a useful `file_line` — the line where the block starts — plus enough
  surrounding text for the caller to judge the match.
- **Measurably** improve recall on the reviewer's six-claim benchmark.

## Non-goals

- Semantic or embedding-based matching. This stays a deterministic,
  dependency-free substring matcher.
- Negation handling. A line reading "X never does Y" still matches a claim
  asserting Y. Pre-accepted in 6b, unchanged here, still documented as a known
  limitation for 6c.
- Cross-block or whole-file matching. A claim whose support genuinely spans
  two separate paragraphs remains unmatched — that is correct conservative
  behaviour, not a gap to close.
- Any change to discovery, the digest, the caps, or the read path. Those were
  reviewed and approved in 6b; this touches matching only.
- Any change to `app/app.py`.

## Design

### Block construction

A **block** is a run of consecutive non-blank lines, delimited by blank lines —
i.e. a Markdown paragraph or a single bullet including its wrapped
continuation lines. Blocks are built per file from the same full content
`fact_check` already reads.

> **Revised after round 1 (see "Round-1 correction" below). The original
> bounds — 12 lines / 1500 chars — were far too wide and produced five
> precision regressions. The values below are the corrected ones.**

A block is a **wrap-joined unit**, not an arbitrary run of lines. The defect
being fixed is one sentence split across two lines by hard wrapping; the unit
needs to be just wide enough to reunite that, and no wider.

A block ends at **any** of:

- a blank line;
- a line whose stripped text ends in sentence-terminal punctuation
  (`.`, `!`, `?`, and `:` before a list);
- the **start of a new structural element** — a Markdown heading (`#`), a list
  item marker (`-`, `*`, `+`, or `N.`), a block quote (`>`), or a table row
  (`|`). A structural marker starts a new block even mid-run;
- a code-fence delimiter (` ``` ` or `~~~`). **Fenced code content is excluded
  from matching entirely** — it is not prose and cannot support a claim;
- `_GROUNDING_BLOCK_MAX_LINES` (**3**) lines, or
  `_GROUNDING_BLOCK_MAX_CHARS` (**400**) characters.

Three lines and 400 characters are sized to the real job: a hard-wrapped
sentence at typical widths spans two lines, occasionally three. Anything
beyond that is a different sentence, and joining it is how unrelated terms
start co-occurring.

Both fixed module constants, deliberately **not** env-configurable: they are
the precision guarantee, not an operator preference. An operator who could
raise them could silently turn co-occurrence into coincidence.

When joining lines for matching, join with a single space so that a term
broken across the wrap boundary does not accidentally concatenate with its
neighbour (`...an` + `unprivileged...` must become `an unprivileged`, never
`anunprivileged`).

### Matching

Unchanged in every other respect: lowercase substring, **every** significant
term must be present in the joined block, same `_significant_terms()` and same
stopword list. Only the haystack changes.

### Result shape

Each match keeps its existing keys, with `line` and `file_line` now referring
to the **first line of the matching block**, plus:

- `text` — the joined block, truncated to `_GROUNDING_BLOCK_MAX_CHARS`.
- `end_line` — the block's last line, so a caller can show the full region.

`max_matches` capping behaviour is unchanged. Blocks, not lines, are counted.

### The precision/recall tradeoff, stated plainly

Widening the unit *does* make accidental co-occurrence more likely — that is
the real cost, and it is why both bounds exist and are not tunable. The
acceptance criteria below therefore require precision to be re-proven, not
assumed: every existing adversarial claim must still return `found: False`.
If any adversarial claim starts matching, the bounds are wrong and the fix
must not ship on a recall improvement alone.

## Folded-in follow-ups

Both logged by the reviewer in 6b, both non-blocking, both in this same
module — folded here rather than spawning a cycle for two small changes.

1. **In-bounds symlink rejection is invisible.** `load_grounding()` silently
   skips a candidate rejected by `O_NOFOLLOW`. Add a `skipped` list to the
   returned snapshot: `{label, relpath, reason}`, with reasons at least
   `symlink`, `not_regular_file`, `out_of_bounds`, `unreadable`. Callers can
   ignore it; 6c and 6f can surface it.
2. **`/proc/self/fd` unavailability degrades to silent total emptiness.** If
   `/proc` is not mounted, `realpath("/proc/self/fd/N")` returns the literal
   unresolved string, which never matches `workdir_real`, so **every** file in
   **every** project is rejected as out-of-bounds with no signal. Detect this
   case explicitly and surface it — a distinct `reason` value and a clear
   one-time indication that containment checking is unavailable. Do **not**
   fall back to path-based `realpath()`: that would silently reopen the TOCTOU
   hole 6b closed. Failing closed is correct; failing closed *silently* is not.

## Acceptance criteria

- [ ] The reviewer's six-claim benchmark from `docs/test-review.md` is encoded
      as a test. **At least 5 of 6 must now match.** The exact claims and the
      before/after counts are recorded in `docs/implementation.md`.
- [ ] Every adversarial/false claim already covered by 6b's tests still
      returns `found: False`. Precision regressions are blocking.
- [ ] A claim whose terms are split across a wrap boundary in
      `docs/ARCHITECTURE.md` matches, and its `text` shows the joined sentence.
- [ ] A claim whose terms appear in two **different** paragraphs does **not**
      match.
- [ ] A run of more than `_GROUNDING_BLOCK_MAX_LINES` lines is split, proven
      by a claim whose terms straddle the split failing to match.
- [ ] Wrap-boundary joining inserts a space: a claim relying on
      `anunprivileged` does not match; one relying on `an unprivileged` does.
- [ ] `line`/`file_line` point at the block's first line; `end_line` at its
      last; both verified against a known fixture.
- [ ] `skipped` is populated with an in-bounds symlink at a candidate path,
      and empty for a clean project.
- [ ] `/proc`-unavailable is detected and surfaced distinctly, verified by
      simulating the unresolved-path condition. No path-based fallback exists.
- [ ] Full suite green, several consecutive runs. No pre-existing test
      modified. `app/app.py` untouched.

## Test plan

Tier 1 (pure, no filesystem): block construction against synthetic content —
wrapped bullets, over-long runs, blank-line delimiting, a single unwrapped
long line, empty files, files of only blank lines.

Tier 2 (real filesystem, this repo as fixture): the six-claim benchmark
against the real `docs/ARCHITECTURE.md`, the adversarial claim set, and the
`skipped`-list cases with real symlinks.

The `/proc` case is simulated rather than by unmounting `/proc`.

## Round-1 correction (2026-08-13)

Round 1 implemented the original spec faithfully and was **blocked** with five
precision regressions, each confirmed to be introduced by the change (6b's
single-line matcher rejected all five correctly). Reproductions are in
`docs/test-review.md`; the clearest:

```
- widget rotation config
- gadget storage config
- unrelated topic zebra migration
```

joined into one block, so a claim about "widget storage config" matched — three
unrelated bullets presented to the lead as verification.

**The fault was in this spec, not the implementation.** Two errors:

1. **The window was ~6x too wide.** The defect being fixed is a single
   sentence wrapped across two lines. Twelve lines is wide enough that
   co-occurrence carries no information — demonstrated by the reviewer's
   `terms_12_lines_apart_in_unrelated_filler` case, which has no Markdown
   structure at all and therefore cannot be fixed by better structure parsing.
2. **Blank lines and sentence-terminal punctuation are not the only semantic
   boundaries.** Headings, list items, block quotes, table rows and code
   fences are all boundaries, and terse bullets frequently carry no terminal
   punctuation at all — which is exactly the shape that broke.

Round 1's sentence-terminal rule was a sound addition and is retained; it was
just insufficient on its own. The corrections are the tighter bounds and the
structural boundary set above.

**A recall figure below the 5/6 target is an acceptable outcome now.**
Precision is the property that must hold. If the corrected bounds cannot reach
useful recall without a precision regression, stop and report — the fallback
is to keep 6b's single-line matcher and raise recall in 6c at the prompt
level, by instructing the lead to make short claims quoting exact phrases.
That path costs no code and carries no precision risk.

## Open questions

- `3` lines / `400` chars are reasoned from typical wrap widths, not measured
  across a corpus. They are bounded above by precision and below by recall.
- Whether the structural-boundary set is complete for Markdown as it appears
  in real project docs. Setext headings (`===`/`---` underlines) and indented
  code blocks are not handled; both are judged rare enough in this context to
  defer, and both fail *closed* (they split rather than merge).
