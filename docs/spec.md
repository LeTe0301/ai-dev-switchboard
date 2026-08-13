# Spec: Grounding — discovery, digest, and `fact_check` (backlog item 6, sub-spec 6b)

## Summary
Give the (not-yet-built) team lead something real to plan and verify claims
against: a new grounding section in `app/teams.py` that auto-discovers a
project's own documentation, builds a hard-byte-capped digest for a system
prompt, and answers `fact_check(claim, grounding)` with matching passages
(`file:line`) or an explicit "no supporting passage found". Pure functions
over files — no LLM, no process spawning, no tmux, no UI, no write path.
This is a hard dependency of 6c's lead loop; splitting it out keeps 6c from
carrying both grounding logic and tool-calling adapters in one pass.

## Goals
- `discover_grounding_files(workdir)` — finds `docs/ARCHITECTURE.md`,
  `docs/BACKLOG.md`, `CLAUDE.md`/`AGENTS.md` (one, first found), `README.md`
  (mirroring `_gather_project_context()`'s casing variants) under a project
  directory. Each optional; missing/unusable files are skipped silently. A
  project with none of them still produces a usable, non-empty result — the
  caller (and eventually the lead) can tell grounding is empty rather than
  silently getting nothing.
- `load_grounding(workdir)` — reads each discovered file once, bounded, and
  returns one immutable-in-spirit snapshot dict holding per-file content,
  extracted headings, and a pre-built digest string.
- `build_digest(files, max_bytes)` — pure function assembling headings +
  per-file snippets into one text blob, **hard-truncated** to `max_bytes` of
  UTF-8-encoded output, regardless of how large the source files are.
- `fact_check(claim, grounding)` — deterministic, precision-biased textual
  match against the *full* per-file content (not the truncated digest).
  Returns matching passages with `file:line`, or an explicit
  `found: False` — never a best-effort/nearest-weak-match fallback, and
  never an exception for any claim or grounding shape.
- Read-only by construction, and provably so: no function in this module's
  new grounding section calls `open()` in a write/append mode or any
  mutating `os`/`shutil` function, verified by both a monkeypatch-based
  runtime test and a static AST scan of the source.
- Survive adversarial file *shape*, not just size: malformed UTF-8, a
  200 MB file, a symlink loop, a symlink pointing outside the project
  directory, a file that's actually a directory, a binary file named
  `README.md`. None of these may raise; each has a defined, tested outcome.

## Non-goals
- The lead loop, its tools, and exposing `fact_check` as an LLM-callable
  tool (JSON schema, tool-call wiring) — **6c**. Here it is a plain Python
  function; 6c decides how/when the lead calls it.
- The roster, tmux team sessions, per-teammate worktrees, `install.sh
  --with-ollama` — **6d**.
- Any web UI, page, or button — **6e**, **6f**. A CLI entry point (see
  "Proposed approach" §5) is the only human-facing surface, matching 6a's
  own precedent that a CLI is not a UI.
- Semantic/LLM-assisted matching in `fact_check`. It is deliberately a
  dumb, deterministic, single-line substring matcher — see "Precision over
  recall" below for why, and "Open questions" for the follow-up this implies
  if 6c's real usage shows it's too conservative.
- Fact-checking against the project's *code* (grep the worktree), per
  `docs/story.md` §7's own resolution of that open question — teammates
  already read code directly; this module only ever looks at the four
  discovered doc files.
- Any caching/staleness/file-watching policy across multiple
  `load_grounding()` calls, or any notion of "the team's grounding" as a
  long-lived object — 6b only provides the single-snapshot primitive; when
  and how often to reload it across a team's lifetime is 6c's/6d's decision.
- Any change to `app/app.py`. `_read_head()` (already there) is reused
  as-is; nothing about engines, sessions, or privilege boundaries changes.
- Any new sudoers surface or privilege boundary. Grounding files are read
  by `SVC_USER` directly, exactly as `_gather_project_context()` already
  does today for the same `PROJECTS_DIR/<name>/` tree — no `RUN_USER`
  involvement at all.
- Multi-line/paragraph-spanning matches in `fact_check` — single-line
  granularity only (see "Precision over recall").

## Background / current state
- `app/app.py:419` `_gather_project_context(workdir)` is the existing
  discovery precedent: checks `README.md`/`Readme.md`/`readme.md`/`README`
  (first found wins), then `CLAUDE.md`/`AGENTS.md` (first found wins),
  including the one-line `@`-indirection case (`app/app.py:436`: if the
  file's stripped content is a single line starting with `@`, the *target*
  file's content is used instead). It also reads `package.json`/
  `requirements.txt`/`pyproject.toml`, none of which are part of 6b's
  grounding set — that function is a different concern (a one-shot LLM
  project description, capped at 6000 combined bytes) and is **not** called
  by this module; 6b mirrors its *matching rules* for the four docs it
  cares about, not its output shape or its unrelated file types.
- `app/app.py:411` `_read_head(path, limit) -> str` is the existing
  bounded-read helper: opens in text mode with `errors="ignore"`, reads at
  most `limit` bytes, and returns `""` on **any** `OSError` (missing file,
  permission denied, is-a-directory, too-many-symlink-levels — all `OSError`
  subclasses). This module reuses it directly rather than duplicating a
  bounded-read helper — `from app import TMUX, tmux_has, load_engines,
  _read_head` (extending `app/teams.py`'s existing import line at line 48;
  `app/app.py` itself is untouched).
- `app/teams.py` (6a, `e7deade`) already establishes the conventions this
  spec follows: a `# ─── config ──` block of `TEAM_*` env vars read once at
  module level with `os.environ.get(...)` defaults (`app/teams.py:51-60`);
  section-marker comments (`# ─── ... ──`) separating pure-function groups
  from I/O-performing ones; pure builder functions that take explicit
  parameters rather than reading module globals internally, so tests can
  override them directly (`_validate_prompt_size`, `_build_headless_argv`,
  `_build_script`); a CLI dispatch table in `_parse_args()`/`main()`
  (`app/teams.py:813,:837`) reusing `argparse` subcommands
  (`run`/`list-engines`), matching `scripts/taiga_push_spec.py`'s shape.
- `docs/story.md` §6 already reserves `TEAM_GROUNDING_MAX_BYTES` as the
  config name for the digest's hard cap.
- **This repo is itself the primary test fixture.** `docs/ARCHITECTURE.md`
  (7,013 bytes, headings only, no fenced code blocks), `docs/BACKLOG.md`
  (20,088 bytes — a genuine oversize case against any reasonable digest cap,
  headings only, no fenced code blocks), `README.md` (11,263 bytes, 4
  fenced code blocks — none currently containing a `#`-prefixed line, so the
  fenced-code-block-aware heading extraction below is exercised by a
  synthetic fixture, not live by this repo's current content), and **no**
  `CLAUDE.md`/`AGENTS.md` (exercises the skip path for real).

## Proposed approach

### 1. Config — one new operator-facing knob, two deliberately-not-tunable ones
```python
# ─── grounding (docs/story.md §4.3; docs/spec.md 6b) ─────────────────────
TEAM_GROUNDING_MAX_BYTES = int(os.environ.get("TEAM_GROUNDING_MAX_BYTES", "8000"))
```
This measures **the UTF-8-encoded byte length of `build_digest()`'s
returned string** — the text actually seeded into the lead's system prompt
— after headings and per-file snippets are assembled and joined, not the
size of any input file. Default 8000 (~2000 tokens): enough room for
headings + a meaningful snippet from up to four files while leaving most of
a small local model's context window for the actual task and tool schemas.

Two more constants exist but are **deliberately not environment-configurable**
— a lesson carried over from 6a's own review history, where a tuned magic
number was the wrong fix twice before the third fix made the bad case
structurally unreachable instead:
```python
_GROUNDING_READ_CAP_BYTES = 2 * 1024 * 1024   # hard backstop, see below
_GROUNDING_MAX_HEADINGS_PER_FILE = 20         # defensive, see §3
_GROUNDING_FACT_CHECK_MAX_MATCHES = 5
```
`_GROUNDING_READ_CAP_BYTES` measures **bytes read from a single grounding
file off disk, before any digest transformation** — the largest chunk any
one `_read_head()` call for this module will ever request, independent of
`TEAM_GROUNDING_MAX_BYTES`. This is what makes the 200 MB-file case safe
regardless of how `TEAM_GROUNDING_MAX_BYTES` is configured: even if an
operator sets the digest cap absurdly high, no single file read is ever
larger than 2 MiB (comfortably larger than this repo's own 20 KB
`BACKLOG.md`, small enough to bound memory for an adversarial input). Making
this a fixed constant rather than another env var is the point — it is not
a behavior an operator should be tuning, it is a safety ceiling that must
hold regardless of what they configure elsewhere.

`_GROUNDING_NO_FILES_DIGEST` is the literal string used when nothing was
discovered (see §2/§3) — a specific, greppable sentence, not an empty
string a caller could mistake for "not yet loaded":
```python
_GROUNDING_NO_FILES_DIGEST = (
    "No grounding files were found for this project (checked "
    "docs/ARCHITECTURE.md, docs/BACKLOG.md, CLAUDE.md/AGENTS.md, README.md)."
)
```

`config/switchboard.env.example` gains one new commented-out line,
`TEAM_GROUNDING_MAX_BYTES`, in the same documented-but-commented-out style
as the existing `TEAM_HEADLESS_*` block — with a one-line comment stating
exactly what it measures (the assembled digest's own byte length, not any
source file's size).

### 2. `discover_grounding_files(workdir) -> list[dict]`
Returns entries (in this fixed order) for whichever of the four sources are
present *and usable*:
```python
[{"label": "docs/ARCHITECTURE.md", "path": "<resolved path>"},
 {"label": "docs/BACKLOG.md",      "path": "<resolved path>"},
 {"label": "CLAUDE.md",            "path": "<resolved path>"},  # or "AGENTS.md"
 {"label": "README.md",            "path": "<resolved path>"}]
```
Matching rules, mirroring `_gather_project_context()`'s in full (not just
the `@`-indirection case) for consistency between the two discovery
functions living in the same codebase:
- `README.md`/`Readme.md`/`readme.md`/`README` — first found wins.
- `CLAUDE.md`/`AGENTS.md` — first found wins. If its stripped content is a
  single line starting with `@`, the path after `@` (resolved relative to
  `workdir`, exactly as `app/app.py:437` does) is treated as the real
  source — **the returned `path` is the resolved target**, not the literal
  `CLAUDE.md`/`AGENTS.md` path, because that target is what's actually read
  and what any `fact_check` `file:line` must point a human at. The `label`
  stays `"CLAUDE.md"`/`"AGENTS.md"` (what the project author wrote), so the
  digest still reads naturally even though `path` differs.
- `docs/ARCHITECTURE.md`, `docs/BACKLOG.md` — exact names only, no casing
  variants (matching `docs/story.md`'s literal naming).

**"Usable" is one unifying rule, not five special-cased branches**: a
candidate path is included only if, after the checks below, reading it
yields non-empty content. A missing file, an empty file, a permission-denied
file, a directory-instead-of-a-file, and a symlink-loop file all reduce to
the same outcome (`_read_head()` already returns `""` for every one of
these — all are `OSError` subclasses it already catches) and are therefore
*indistinguishable from "not present"* by design — simpler than tracking
five different skip reasons, and product-wise correct: an empty `CLAUDE.md`
is exactly as useless as a missing one.

Two checks run **before** any read is attempted, because relying on
"empty content ⇒ skip" is not sufficient for these two — a readable file in
the wrong place must never be read at all, not read-then-discarded:
- **Symlink containment.** `os.path.realpath()` the candidate path (and,
  for the `@`-indirection case, the resolved target path too — the second,
  in-band pointer mechanism is exactly as much of an information-disclosure
  risk as a filesystem symlink and gets the identical check). If the real
  path does not stay under `os.path.realpath(workdir)`, the candidate is
  skipped **without being opened at all**. This exists so a project
  containing `README.md -> /etc/hostname` (or a `CLAUDE.md` whose one line
  is `@../../../etc/hostname`) can never leak arbitrary host filesystem
  content into a digest or a `fact_check` response, independent of whatever
  file permissions happen to allow — belt-and-suspenders, not a
  currently-exploitable hole, but a real disclosure path worth closing
  before it exists, per the brief.
- **Binary sniff.** Read the first 512 bytes in `"rb"` mode; if a `NUL`
  byte is present, skip — a `.png` renamed to `README.md` decodes "fine"
  under `errors="ignore"` (bytes are silently dropped, not replaced) but
  produces useless-to-garbage text that pollutes the digest and wastes
  `fact_check`'s scan; catching it structurally here is cheaper than trying
  to make the digest/heading logic robust to arbitrary garbage later.

Genuinely malformed UTF-8 (not a binary file — real text with a few invalid
byte sequences) is **not** a skip condition — `_read_head()`'s own
`errors="ignore"` already handles it (drops the invalid bytes, keeps the
rest, never raises), matching `_gather_project_context()`'s existing
tolerance for the same case.

### 3. `load_grounding(workdir, *, max_bytes=TEAM_GROUNDING_MAX_BYTES, read_cap=_GROUNDING_READ_CAP_BYTES) -> dict`
```python
{
  "workdir": workdir,
  "loaded_at": _now_iso(),
  "files": [
    {"label": "docs/ARCHITECTURE.md", "path": "...", "relpath": "docs/ARCHITECTURE.md",
     "headings": [...], "content": "<up to read_cap bytes, text>", "byte_count": N},
    ...
  ],
  "digest": "<build_digest(files, max_bytes)>",
  "empty": bool,   # True iff files == []
}
```
For each discovered entry, reads `content = _read_head(path, read_cap)`
**once** — this single read is both the source for headings/digest
*and* the corpus `fact_check` searches; nothing re-reads the file for a
later `fact_check` call against this same `grounding` object (see
"Snapshot semantics" below).

**Heading extraction** (`_extract_headings(content) -> list[str]`, pure,
independently testable): scans lines matching `^#{1,6}\s+.+` (ATX
headings), **skipping any line while inside a fenced code block** — toggled
by a line starting with ` ``` ` or `~~~` — so a shell comment or a Python
shebang inside a fenced example is never mistaken for a heading. Capped at
`_GROUNDING_MAX_HEADINGS_PER_FILE` (20) per file — a defensive bound on the
intermediate heading list itself, independent of the final byte truncation
below, so a pathological file with thousands of `#`-prefixed lines can't
blow up an intermediate list before truncation ever gets a chance to run.

**Snapshot semantics.** `load_grounding()` reads every file exactly once,
at call time. The returned dict is a snapshot: if `docs/BACKLOG.md` changes
on disk a second after `load_grounding()` returns, that snapshot's
`content`/`digest`/subsequent `fact_check()` calls against it do **not**
see the change — only a fresh `load_grounding()` call does. This is
deliberate, not an oversight: within one round, the lead's digest and its
own `fact_check` calls should agree with each other (a claim `fact_check`
confirms should match what the digest already told the lead), which
requires them to read the same snapshot; picking up a concurrent edit
mid-round would let the two disagree with no way for the lead to tell why.
6c/6d own the decision of *when* to call `load_grounding()` again across a
team's lifetime (a new round, a restart, a fixed interval) — this module
only guarantees that one snapshot is internally consistent for as long as
it's held.

### 4. `build_digest(files, max_bytes=TEAM_GROUNDING_MAX_BYTES) -> str`
Pure function, no disk I/O — takes the `files` list `load_grounding()`
already built (or a synthetic one a test constructs directly, with no
project directory needed at all).

- `files == []` → returns `_GROUNDING_NO_FILES_DIGEST` verbatim (already
  well under any reasonable `max_bytes`; no truncation logic needed for
  this branch).
- Otherwise: for each file, emit a section — its `label`, its heading list,
  and a body snippet — sized against a fair per-file share of the budget
  (`per_file_budget = max(200, max_bytes // len(files))`, so 1–4 files each
  get a reasonable slice rather than the first file starving the rest), then
  join all sections in discovery order.
- **The per-file share is a fairness heuristic, not the safety mechanism.**
  The actual guarantee — the thing the acceptance criteria hold it to — is
  a final, unconditional step: encode the assembled text to UTF-8, slice to
  the first `max_bytes` bytes, decode with `errors="ignore"` (cleanly drops
  a trailing partial multi-byte sequence, same discipline `_read_head()`
  already uses). This runs **every time**, regardless of file count or
  size, so the cap holds even if the per-file math above is wrong, changed
  later, or fed something unexpected — a structural backstop, not a tuned
  number trusted to always be sufficient by itself — the safety property
  doesn't depend on the fairness heuristic being correct.
- `max_bytes <= 0` → returns `""` (no exception; an operator misconfiguring
  this to zero gets an empty-but-valid digest, not a crash).

### 5. `fact_check(claim, grounding, *, max_matches=_GROUNDING_FACT_CHECK_MAX_MATCHES) -> dict`
```python
{"claim": claim, "found": bool,
 "matches": [{"label": "...", "path": "...", "relpath": "...",
              "line": 42, "file_line": "docs/ARCHITECTURE.md:42",
              "text": "<the matching line, stripped>"}, ...]}
```
Searches `grounding["files"][*]["content"]` — the **full** per-file
content read by `load_grounding()` (bounded at `read_cap`, i.e. up to
2 MiB), **not** `grounding["digest"]` (bounded much smaller, at
`max_bytes`, i.e. 8 KB by default). A passage that got truncated out of the
digest but is still present in the full content must still be found — the
digest is a *summary for the prompt*; `fact_check` is a *lookup against the
real source*, and conflating the two would silently cripple recall for
exactly the oversized-file case this module exists to handle gracefully.

**Precision over recall — the whole point of the feature.** Per
`docs/story.md` §4.3, a claim `fact_check` wrongly confirms is worse than
one it wrongly rejects, because the lead treats a returned match as
confirmation. The matcher is therefore deliberately conservative, with no
scoring/ranking of "close enough" candidates at all — the absence of any
such fallback is what makes "return the nearest weak match" structurally
impossible, not a rule the algorithm has to remember to apply:

1. Normalize `claim`: lowercase, tokenize on `[A-Za-z0-9_']+`, drop a small
   built-in stopword list (`a, an, the, is, are, was, were, be, to, of, in,
   on, and, or, that, this, it, for, with, as, ...`), drop single-character
   tokens. Call the result `terms`.
2. `terms == []` (empty/whitespace-only claim, or a claim built entirely
   from stopwords) → `found=False`, `matches=[]` immediately. Nothing
   meaningful to search for; not an error.
3. Otherwise, scan every file's content line by line (1-indexed). A line is
   a match **iff every term in `terms` appears as a case-insensitive
   substring of that line** — a strict conjunctive match, no partial
   credit, no edit-distance/fuzzy scoring, no minimum-fraction-of-terms
   threshold. A claim whose terms are scattered across two lines of a
   wrapped paragraph will not match even if the claim is true — an
   intentional recall loss in exchange for the precision guarantee; see
   "Open questions" for the possible follow-up if 6c's real usage shows
   this is too conservative.
4. Collect matches in file-discovery-then-line order, capped at
   `max_matches` (default 5). `found = bool(matches)`.

Claim text is only ever matched with plain `str.lower()` substring
containment against document lines — never compiled as a regex or used to
build one — so an adversarial claim full of regex metacharacters carries no
ReDoS or injection risk. (The `re.findall()` call in step 1 uses a
fixed pattern *we* wrote to tokenize the claim; it never treats claim text
as a pattern itself.)

`grounding["empty"] is True` needs no special case: `terms` scanned against
zero files' worth of lines naturally yields `matches=[]` either way.

### 6. Read-only guarantee — asserted, not just intended
Every function above only ever calls `open()` in a default/`"r"`/`"rb"`
mode and never calls any of `os.remove`/`os.rename`/`os.replace`/
`os.unlink`/`os.truncate`/`os.mkdir`/`os.makedirs`/`os.chmod`/
`shutil.rmtree`/`shutil.move`/`shutil.copy*` — no function in this section
accepts a "content to write" parameter at all, so there is structurally
nothing *to* write even if a bug tried. Verified two ways in the test plan:
a runtime monkeypatch test (each mutating call raises `AssertionError` if
hit, then the full public surface is exercised against a real fixture
project including edge cases) and a static `ast`-based scan of the
module's grounding section (catches a call in a branch the runtime test
didn't happen to exercise — the stronger, literal reading of "the module
exposes no write path at all").

### 7. CLI additions (not a UI — matches 6a's own precedent)
Two new `argparse` subcommands in the existing `_parse_args()`/`main()`
dispatch (`app/teams.py:813,:837`), for manual verification without
writing a throwaway script:
```
python3 app/teams.py grounding <workdir>              # prints load_grounding() as JSON
python3 app/teams.py fact-check <workdir> "<claim>"    # prints fact_check() as JSON
```

## Affected areas
- `app/teams.py` — extended (new grounding section, new import
  `_read_head` from `app`; no existing function in this file changed). No
  `app/app.py` change.
- `config/switchboard.env.example` — one new commented-out line,
  `TEAM_GROUNDING_MAX_BYTES`.
- `tests/test_teams_grounding.py` — new.
- No schema/data-model changes, no HTTP API changes, no `install.sh`
  change, no `docs/ARCHITECTURE.md` change (no new privilege boundary —
  same `SVC_USER`-reads-`PROJECTS_DIR`-directly pattern
  `_gather_project_context()` already uses), no `docs/ADDING_AN_ENGINE.md`
  change (that doc is about `HEADLESS_*` engine keys, not grounding).

## Edge cases
- No grounding files at all → `files=[]`, `empty=True`,
  `digest=_GROUNDING_NO_FILES_DIGEST`, `fact_check()` always `found=False`.
- `CLAUDE.md` and `AGENTS.md` both present → `CLAUDE.md` wins, `AGENTS.md`
  ignored (matches `_gather_project_context()`'s `break` semantics).
- `CLAUDE.md`'s one-line `@target` resolves outside `workdir` → skipped,
  never opened.
- Symlinked grounding file resolving outside `workdir` → skipped, never
  opened.
- Symlink loop → `_read_head()`'s `OSError` catch returns `""` → skipped
  via the unified "empty content ⇒ not present" rule.
- File replaced by a directory of the same name → `IsADirectoryError` (an
  `OSError`) → same unified skip.
- Binary file (NUL byte in first 512 bytes) named like a grounding file →
  skipped before any content is read for digest/heading purposes.
- Malformed UTF-8 mixed with valid text → not a skip condition; valid
  portions still usable, no exception.
- 200 MB grounding file → only `_GROUNDING_READ_CAP_BYTES` (2 MiB) is ever
  read into memory, regardless of the file's real size on disk; a passage
  past that boundary is a documented, accepted recall limitation for
  adversarially huge files, not a crash.
- `docs/BACKLOG.md`-sized (20 KB, real) or larger input vs. a small/default
  `TEAM_GROUNDING_MAX_BYTES` → digest still hard-truncated to the cap; full
  content still fully searchable by `fact_check()` regardless of the
  digest's own truncation.
- Heading line inside a fenced code block → not extracted as a heading.
- Empty claim / all-stopword claim → `found=False`, no exception.
- Claim with partial (not full) term overlap on every line → `found=False`
  — no nearest-weak-match fallback.
- `TEAM_GROUNDING_MAX_BYTES` configured to `0` or negative →
  `build_digest()` returns `""`, no exception.
- Two concurrent `load_grounding()`/`fact_check()` calls for different (or
  the same) project — pure reads against no shared mutable state beyond
  module-level config constants; safe by construction, no locking needed.
- Mid-run edit to a grounding file between two `load_grounding()` calls —
  the earlier snapshot does not see it (§3 "Snapshot semantics"); a new
  `load_grounding()` call does.
- `docs/` subdirectory missing entirely → `os.path.join` on a nonexistent
  intermediate directory still raises the same `FileNotFoundError`
  `_read_head()` already catches; no special-case needed.
- Platform: already Linux-only (matches the rest of `app/teams.py`); no new
  cross-platform concern (no shell/tmux involvement at all in this module).

## Acceptance criteria
- [ ] Given this repo's own project directory (real `docs/ARCHITECTURE.md`,
      `docs/BACKLOG.md`, `README.md`; no `CLAUDE.md`/`AGENTS.md`), when
      `discover_grounding_files(workdir)` runs, then it returns exactly
      those three entries, correctly labeled, with no error for the two
      absent sources.
- [ ] Given a project directory with none of the four sources, when
      `load_grounding(workdir)` runs, then `files == []`, `empty is True`,
      and `digest == _GROUNDING_NO_FILES_DIGEST` (not `""`, not `None`) —
      the team still "starts" (in the sense that this call succeeds) with
      grounding the caller can tell is empty.
- [ ] Given a `CLAUDE.md` whose one-line content is `@docs/OTHER.md`
      pointing at a real file inside the project, when discovery runs, then
      the returned entry's `path` is the resolved target (not the literal
      `CLAUDE.md` path), matching `_gather_project_context()`'s behavior at
      `app/app.py:436`.
- [ ] Given that same `@`-pointer resolves **outside** the project
      directory, when discovery runs, then the entry is skipped entirely —
      never opened (verified via a monkeypatched/wrapped `open()` that
      fails the test if called on the out-of-bounds path).
- [ ] Given a real filesystem symlink for one of the four candidate paths
      that resolves outside the project directory, when discovery runs,
      then it is skipped and never appears in `files` or the digest —
      verified against an actual symlink, not just path-string reasoning.
- [ ] Given a symlink loop at one of the four candidate paths, when
      discovery runs, then no exception propagates and the entry is
      skipped.
- [ ] Given one of the four candidate paths is actually a directory, when
      discovery runs, then no exception propagates and the entry is
      skipped.
- [ ] Given a binary file (containing a NUL byte in its first 512 bytes)
      named `README.md`, when discovery runs, then it is skipped and never
      contributes to `files`, `digest`, or a `fact_check` match.
- [ ] Given a grounding file with a genuinely invalid UTF-8 byte sequence
      mixed into otherwise-valid text, when it is loaded, then no exception
      propagates and the valid surrounding text is still present in
      `content`/headings/digest.
- [ ] Given this repo's real 20,088-byte `docs/BACKLOG.md`, when
      `build_digest()` runs with `TEAM_GROUNDING_MAX_BYTES` at its default
      **and** at a deliberately tiny override (e.g. 500), then in both
      cases the returned digest's UTF-8-encoded length is `<= max_bytes` —
      proven at more than one cap size against a real oversized file, not
      just the default.
- [ ] Given a synthetic sparse file well over 100 MB as a grounding
      candidate, when `load_grounding()` reads it, then the returned
      `content`'s length never exceeds `_GROUNDING_READ_CAP_BYTES`, and the
      call completes quickly (bounded read, not a full-file read).
- [ ] Given a claim built from an exact sentence fragment that genuinely
      appears in one of this repo's own discovered grounding files, when
      `fact_check(claim, grounding)` runs, then `found is True` and at
      least one match's `file_line` correctly names the real file and the
      real 1-indexed line number of that text (independently verified by
      reading that line from the source file).
- [ ] Given a claim whose significant terms are only *partially* present on
      any single line of the grounding set (a genuine weak/partial overlap,
      not a full match), when `fact_check()` runs, then `found is False`
      and `matches == []` — proving there is no nearest-weak-match
      fallback.
- [ ] Given an empty, whitespace-only, or all-stopword claim, when
      `fact_check()` runs, then `found is False` with no exception.
- [ ] Given `grounding["empty"] is True`, when `fact_check()` runs for any
      claim, then `found is False` with no exception.
- [ ] Given a passage that is present in a file's full content but falls
      outside where `build_digest()` truncated that file's snippet, when
      `fact_check()` runs against it, then it is still found — proving
      `fact_check` searches full content, not the truncated digest.
- [ ] Given a markdown file with a fenced code block containing a
      `#`-prefixed line, when headings are extracted, then that line is not
      included in the heading list (synthetic fixture; this repo's own
      files don't currently exercise this, so the fixture is required).
- [ ] Given the full grounding public surface (`discover_grounding_files`,
      `load_grounding`, `build_digest`, `fact_check`) run against a real
      fixture project including at least one edge case above, when
      `builtins.open` and every listed mutating `os`/`shutil` function are
      monkeypatched to raise if called, then none of them fire.
- [ ] Given a static `ast` scan of the grounding section of
      `app/teams.py`, then no `open()` call uses a write/append/create
      mode and no call targets any of the mutating functions listed in §6
      — independent of runtime test coverage.
- [ ] Given `load_grounding()` called twice for the same project with a
      grounding file modified on disk between the two calls, then the
      first call's own snapshot does not reflect the change; a fresh
      `load_grounding()` call does.
- [ ] `python3 app/teams.py grounding <workdir>` and
      `python3 app/teams.py fact-check <workdir> "<claim>"` both work
      against a real project directory with no server running and no other
      part of the app touched.
- [ ] All 372 pre-existing tests continue to pass unmodified; no
      pre-existing test file is touched.

## Test plan
**Tier 1 — pure unit, no disk I/O at all** (`tests/test_teams_grounding.py`):
`build_digest()` fed synthetic `files` lists (empty, one file, four files,
a file whose content is longer than its per-file share) asserting the exact
truncation guarantee at multiple `max_bytes` values; `_extract_headings()`
fed synthetic markdown strings including a fenced-code-block case and a
`_GROUNDING_MAX_HEADINGS_PER_FILE`-exceeding case; `_significant_terms()`
fed empty/whitespace/all-stopword/mixed-case/punctuation-heavy claims;
`fact_check()` fed a synthetic `grounding` dict (no project directory
needed) covering full-match, partial-match, empty-claim, and
`empty=True` cases.

**Tier 2 — real filesystem, real fixture projects** (same file): this
repo's own working tree used directly as the primary realistic fixture
(exercises the real 20 KB `BACKLOG.md` oversize case and the real
no-`CLAUDE.md` skip path with zero synthesized content); a `tempfile`-based
scratch project tree for every edge case that needs constructing on
purpose — real symlink pointing outside the tree, a real symlink loop
(`os.symlink(a, b); os.symlink(b, a)`-style), a directory named like a
grounding file, a binary fixture (a few KB of non-UTF-8/NUL-containing
bytes) named `README.md`, a file with deliberately invalid UTF-8 bytes
spliced into valid text, a `CLAUDE.md` with an in-bounds and an
out-of-bounds `@`-pointer, and a sparse multi-hundred-MB file
(`os.ftruncate`/seek-based creation, not actually written byte-by-byte, so
the test stays fast) for the read-cap proof.

**Read-only assertions**: one test monkeypatches `builtins.open` to raise
`AssertionError` on any non-read mode and runs the full public surface
against the Tier 2 fixtures; a second monkeypatches each mutating
`os`/`shutil` function to raise if called, same run; a third does a static
`ast.parse()` of `app/teams.py`, walks the grounding section's function
defs, and asserts no `Call` node targets `open()` with a write-mode literal
or any of the mutating functions — independent of what the runtime tests
happened to exercise.

**CLI**: both new subcommands run directly against a real scratch project
and against this repo's own tree, output inspected as JSON.

Run with: `python3 -m unittest discover -s tests -v` (existing convention;
new file follows `tests/test_teams_headless.py`'s `sys.path.insert`/
`os.environ.setdefault`-before-import shape).

## Open questions
- **`TEAM_GROUNDING_MAX_BYTES` default (8000)** — a reasonable-sounding
  starting point (~2000 tokens), not measured against a real local model's
  actual context budget yet. Proceeding under this assumption; worth
  revisiting once 6c has a real Ollama model's context window and tool-
  schema overhead to size it against.
- **README casing-variant matching** — `docs/story.md` §4.3 literally says
  `README.md`; this spec extends discovery to also match
  `Readme.md`/`readme.md`/`README`, mirroring `_gather_project_context()`
  in full for consistency between the two sibling discovery functions.
  Flagging this as a deliberate, low-risk extension beyond the story's
  literal wording rather than a silent deviation — easy to narrow back to
  the literal name only if that consistency isn't wanted.
- **Single-line-only `fact_check` matching** — deliberately conservative
  (see "Precision over recall"). If 6c's real usage against a real lead
  shows too many true claims coming back `found=False` because their
  support spans two adjacent lines of a wrapped paragraph, a follow-up
  could join adjacent non-blank lines into one matchable "paragraph" unit
  without changing the underlying precision rule (still no fuzzy/nearest
  matching, just a wider matching unit). Not built now — no evidence yet
  that it's needed, and the brief explicitly asks for precision bias.
- **`_GROUNDING_READ_CAP_BYTES` (2 MiB, not configurable)** — sized to
  comfortably exceed every real file in this repo's own grounding set
  (largest is 20 KB) while bounding memory for an adversarial input.
  Proceeding under this value as a reasonable structural ceiling; open to
  adjustment if a real project's genuine `BACKLOG.md` turns out to need
  more.

## Risk / rollback notes
Purely additive: a new self-contained section in `app/teams.py` with no
call site anywhere else in the codebase yet (6c is what will import and
call it from the lead loop) and no change to `app/app.py`. Nothing in this
sub-spec is wired into any HTTP route, the CLI's existing `run`/
`list-engines` subcommands, or the existing single-engine session path.
Rollback is reverting the commit; no migration, no state, no privilege
surface to unwind. The only shared surface touched is `app/teams.py`'s
import line (`_read_head` added) and `config/switchboard.env.example`
(one new commented-out line) — both trivially revertible and inert until
6c actually calls into this module.
