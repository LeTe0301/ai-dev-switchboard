# Implementation: Grounding — discovery, digest, and `fact_check` (backlog item 6, sub-spec 6b)

## Summary
Extended `app/teams.py` with a new, self-contained "grounding" section:
`discover_grounding_files(workdir)` finds a project's `docs/ARCHITECTURE.md`,
`docs/BACKLOG.md`, `CLAUDE.md`/`AGENTS.md` (with `@target` indirection),
`README.md` (casing variants), mirroring `_gather_project_context()`'s own
matching rules; `load_grounding(workdir)` reads each real file exactly once
(bounded at a fixed 2 MiB per file) into one snapshot dict with per-file
headings and a pre-built digest; `build_digest(files, max_bytes)` is a pure,
hard-byte-capped digest assembler; `fact_check(claim, grounding)` is a
deterministic, precision-biased single-line substring matcher against each
file's full content, returning `file:line` matches or an explicit
`found=False` — never a nearest-weak-match fallback. Two new `argparse` CLI
subcommands (`grounding`, `fact-check`) exercise the same code with no
server and no UI, matching 6a's own CLI precedent. `app/app.py` is
untouched.

**Round 1's testing pass found two blocking defects** (a named-pipe hang,
and a TOCTOU race defeating the symlink-containment guarantee) plus two
non-blocking should-fix items, all in `docs/test-review.md`. **This is the
round-1 fix pass** — see "Post-review fixes" below for the full root
cause/fix/regression-test writeup of each. The fix restructures file access
around a single `os.open()`/`fstat()`/read/`os.close()` per real file
(replacing the original design's reuse of `app.py`'s `_read_head()`, which
is what made the double-read/TOCTOU shape possible in the first place) —
this is the one substantive architectural change from the original build,
documented in full under "Deviations from spec" below.

## Root cause
N/A for the original feature — new capability, not a bugfix. See
"Post-review fixes" below for the two defects' own root causes.

## Changes by file
- `app/teams.py`
  - Import line: `from app import TMUX, tmux_has, load_engines` — **no
    longer imports `_read_head`** (round-1 fix; see "Deviations from
    spec"). Added `import re` (heading extraction, claim tokenizer) and
    `import stat` (regular-file check on an open fd).
  - One new operator-facing config constant in the existing module-level
    config block: `TEAM_GROUNDING_MAX_BYTES` (default `8000`), read once at
    import time from `os.environ`, matching the `TEAM_*` pattern 6a already
    established.
  - New `# ─── grounding ──` section, added just before the existing
    `# ─── CLI ──` marker:
    - `_under_workdir(path, workdir_real)` — cheap realpath-based
      containment pre-check (pure path arithmetic, no open).
    - `_open_grounding_candidate(path, workdir_real)` (round-1 fix) — the
      single validated `os.open()` per real file: `O_RDONLY | O_NOFOLLOW |
      O_NONBLOCK`, `os.fstat()` to confirm a regular file, then a second
      containment check against the **fd's own** resolved path (via
      `/proc/self/fd/<fd>`) before returning the fd. See "Post-review
      fixes" for why this replaces the original two-checks-then-two-reads
      design.
    - `_read_grounding_candidate(path, workdir_real, read_cap)` (round-1
      fix) — opens via the above, reads up to `read_cap` raw **bytes**
      (not characters) off the fd in a loop, sniffs the first 512 bytes of
      what was read for a NUL (the binary check, now free — no separate
      open), decodes UTF-8 with `errors="ignore"`, closes the fd. Returns
      `""` for any unusable candidate.
    - `_discover_and_read(workdir, read_cap)` (round-1 fix) — the one place
      that both decides which of the four candidates are present *and*
      fetches their content; every real file is opened and read exactly
      once here. `discover_grounding_files()` and `load_grounding()` are
      both thin wrappers around it.
    - `discover_grounding_files(workdir)` — same public shape as before
      (`[{"label":..., "path":...}]`), now built from
      `_discover_and_read()`.
    - `_extract_headings(content)` — unchanged: ATX heading scan, fenced-
      code-block-aware, capped at `_GROUNDING_MAX_HEADINGS_PER_FILE` (20).
    - `load_grounding(workdir, *, max_bytes=..., read_cap=...)` — now calls
      `_discover_and_read()` directly (not `discover_grounding_files()`
      followed by a second read) — exactly one open+read per real file for
      the whole snapshot build, closing the round-1 TOCTOU gap by
      construction (see "Post-review fixes").
    - `build_digest(files, max_bytes=...)` — unchanged: pure, no disk I/O;
      the empty-files sentinel branch, the `max_bytes <= 0` branch, the
      fairness-heuristic per-file budget, and the unconditional final
      encode/slice/decode truncation that's the actual safety guarantee.
    - `_significant_terms(claim)` / `fact_check(claim, grounding, *,
      max_matches=...)` — unchanged: the tokenizer/stopword filter and the
      strict conjunctive, single-line, no-fallback matcher.
    - Three module-level constants deliberately **not**
      environment-configurable: `_GROUNDING_READ_CAP_BYTES` (2 MiB, now a
      genuine byte bound — see Defect 4 below), `_GROUNDING_MAX_HEADINGS_PER_FILE`
      (20), `_GROUNDING_FACT_CHECK_MAX_MATCHES` (5); plus
      `_GROUNDING_NO_FILES_DIGEST` (the sentinel string) and
      `_GROUNDING_STOPWORDS`/`_GROUNDING_TOKEN_RE`.
  - CLI: `_cli_grounding()`, `_cli_fact_check()`, two new `sub.add_parser()`
    entries (`grounding <workdir>`, `fact-check <workdir> <claim>`) in
    `_parse_args()`, and `main()`'s dispatch generalized from a two-way
    `if/else` to an explicit `if/elif` chain covering all four subcommands
    (`run`/`list-engines`/`grounding`/`fact-check`). Unchanged this round.
  - No existing (pre-6b) function in this file changed.
- `config/switchboard.env.example` — one new commented-out block
  (`TEAM_GROUNDING_MAX_BYTES`, default `8000`). Unchanged this round.
- `tests/test_teams_grounding.py` (65 tests as of this round, up from 56 at
  the original build) — see "How to verify locally" below. Round-1 changes:
  two existing "never opened" tests switched from wrapping `builtins.open`
  to wrapping `os.open` (the function actually called now — wrapping the
  no-longer-called `builtins.open` would have made those tests silently
  vacuous); the runtime read-only test and the static AST scan both
  extended to also cover `os.open()`'s flags argument, not just
  `builtins.open()`'s mode string; the AST scan's function-name list
  updated to the new function set; 9 new tests (regressions for both
  blocking defects plus the two should-fix items, one documenting the
  deliberate in-bounds-symlink behavior change, and one covering a distinct
  adversarial shape — an intermediate symlinked directory — found while
  verifying the fix's own layering — see "Post-review fixes").

## Key decisions / tradeoffs
- **`README.md`'s returned `label` is always the literal string
  `"README.md"`**, regardless of which casing variant
  (`Readme.md`/`readme.md`/`README`) was actually matched — the spec's own
  fixed-entries example shows a single `"README.md"` label in the output
  shape, and (unlike `CLAUDE.md`/`AGENTS.md`, where the spec explicitly
  calls out that the label should reflect "what the project author wrote")
  says nothing about preserving README's casing in the label. `relpath`
  still reflects the actual on-disk name.
- **The static AST scan (`GroundingStaticASTScanTests`) walks a fixed list
  of function names**, not "everything after a certain line number" — more
  precise (survives reordering), and the test asserts its own name list
  isn't stale so a future function added to the section without updating
  the test's list fails loudly rather than being silently unscanned. Kept
  and extended (not replaced) this round.
- **The sparse-200MB-file fixture pads its real (non-hole) prefix past 512
  bytes deliberately** — an all-zero sparse file's first 512 bytes are NUL
  (part of the hole), which the binary sniff is designed to reject; a naive
  fixture would get classified as binary and skipped, silently defeating
  the read-cap test's own purpose.
- **Discovery's own standalone read is still a separate operation from
  `load_grounding()`'s.** Calling `discover_grounding_files(workdir)` and
  then separately calling `load_grounding(workdir)` still reads each real
  file twice, once per call — this is *not* the bug Defect 2 was about.
  Each of those two calls does its own single, validated, TOCTOU-safe
  open+read; nothing chains one call's result into a second, unvalidated
  read of the same path the way the original design did *within*
  `load_grounding()`'s own single call. Two independent, safe reads across
  two separate top-level calls is an accepted, spec-consistent cost (the
  spec's own snapshot-semantics section already treats each `load_grounding()`
  call as an independent read of current disk state); one call silently
  re-reading a path it already validated, without re-validating, is what
  had to be eliminated.

## Post-review fixes (docs/test-review.md, round 1 — blocked)

### Defect 1 (must-fix, blocking) — a named pipe at any candidate path hung discovery indefinitely
**Root cause**: the original `_looks_binary()` called `open(path, "rb")`
unconditionally as part of the pre-read checks. `open()` on a FIFO in
read-only mode blocks the calling thread until a writer connects — which
never happens for an adversarial project directory containing a bare
`os.mkfifo()`'d file at any of the four candidate paths. No timeout existed
anywhere in the call chain.

**Fix**: folded into the same structural fix as Defect 2 (see below) —
`_open_grounding_candidate()`'s single `os.open()` call now includes
`O_NONBLOCK`. For a FIFO opened read-only with `O_NONBLOCK` set, POSIX
guarantees `open()` succeeds immediately even with no writer present; the
immediately-following `os.fstat()` check then sees `stat.S_ISFIFO` (not
`S_ISREG`) and the candidate is closed and rejected *before* any read is
attempted — no blocking read is ever reached either. Verified live: `mkfifo
$workdir/README.md` (no writer) then `timeout 6 python3 app/teams.py
grounding $workdir` used to hang until the timeout killed it; now returns
`{"empty": true, ...}` immediately.

**Regression tests added** (`tests/test_teams_grounding.py`,
`PostReviewRegressionTests`): `test_defect1_named_pipe_at_candidate_path_does_not_hang`
(a real `os.mkfifo()`, called in a background thread with a bounded
`join(timeout=5)` — the reviewer's own verification technique — so a
regression fails cleanly instead of hanging the test process),
`test_defect1_fifo_at_docs_architecture_md_does_not_hang` (confirms the fix
isn't specific to the README.md slot), `test_defect1_fifo_via_real_cli_subprocess_bounded`
(the reviewer's exact reproduction shape, through the real CLI subprocess
with a hard external `timeout=8` as a backstop).

### Defect 2 (must-fix, blocking) — TOCTOU between discovery's probe read and load_grounding()'s second read defeated symlink containment
**Root cause**: `discover_grounding_files()` validated containment via
`os.path.realpath()` against the *literal candidate path string*, then
returned that same literal path in its result. `load_grounding()` then
called `_read_head()` (reused from `app.py`) a second time against that
same literal path, with no containment re-check — a real window existed
between "checked" and "read (for keeps)" in which the file could be
replaced (e.g. swapped for a symlink pointing outside the project, by
something else editing the project tree — a real possibility given this
repo's own Gitea sync-on-push feature) and the swapped-in content would be
read and trusted anyway. The reviewer reproduced this deterministically by
forcing the swap to land exactly between the two `_read_head()` calls.

**Fix — structural, not a faster re-check.** A faster or repeated
`realpath()`-then-`open()` pattern narrows the window but can never close
it (any two separate syscalls have a gap between them, however small).
The actual fix, per explicit review direction: validate the *file
descriptor*, not the path string, and never read the same real file
through more than one `open()` call within a single `load_grounding()`
build. `_open_grounding_candidate()` (see "Changes by file" above)
opens once with `O_NOFOLLOW` (so a final-path-component symlink — exactly
what a same-path swap would introduce — makes the `open()` call itself
fail with `ELOOP`, rather than silently following it) and then verifies
containment against the **opened fd's own resolved path**
(`os.path.realpath("/proc/self/fd/<fd>")`), which is pinned to the specific
inode that's now open and cannot be affected by anything happening to the
path string afterward. `_discover_and_read()` (see "Changes by file")
collapses discovery and content-fetching into one pass so `load_grounding()`
never performs a second, independent read of a path it (or an earlier
call within the same build) already validated.

**Deliberate behavior change, called out explicitly**: because `O_NOFOLLOW`
rejects *any* final-component symlink (not just an out-of-bounds one), a
real filesystem symlink at a candidate path — even one that legitimately
resolves in-bounds — is now categorically unusable as grounding input. The
original design (matching the spec's literal prose) would have followed
and used an in-bounds symlink. No acceptance criterion in `docs/spec.md`
requires an in-bounds symlink to be honored (the spec's own criteria only
test the out-of-bounds case), so this narrows the accepted input shape
without violating anything the spec pins down — and it's the direct,
necessary consequence of closing the TOCTOU race the reviewer identified:
supporting "usable if it happens to point in-bounds" requires a
check-then-open pattern, which is exactly the vulnerable shape being
eliminated.

**Regression test added**: `test_defect2_symlink_swap_in_the_open_window_does_not_leak_outside_content`
forces the real race (not a simulation of "two reads far apart") by
monkeypatching `os.open()` itself to perform the swap — replacing an
in-bounds `docs/ARCHITECTURE.md` with a symlink to an out-of-bounds secret
file — as a side effect immediately before delegating to the real
`os.open()`, landing exactly in the window between
`_open_grounding_candidate()`'s own `_under_workdir()` pre-check and its
`os.open()` call. Asserts the swapped file is excluded from `files` and
that the outside content never appears in the digest or any file's
content. Also added `test_in_bounds_symlink_candidate_is_now_categorically_unusable`
(pinning the deliberate behavior change above) and
`test_defect2_intermediate_symlinked_directory_is_also_rejected` (a
distinct file *shape* — a symlinked `docs/` directory with an ordinary
regular file at the final path component — found worth covering
separately while verifying the fix's layering, below). The two
pre-existing "never opened" tests
(`test_claude_md_at_indirection_out_of_bounds_is_skipped_and_never_opened`,
`test_real_symlink_resolving_outside_workdir_is_skipped_never_opened`)
were updated to wrap `os.open` (the function actually called post-fix)
instead of `builtins.open`.

**Revert-and-watch-it-fail, done for both defects before considering them
closed** (matching 6a's own review discipline, applied here proactively
rather than waiting for the reviewer's second pass to ask for it):
- Removing only `O_NONBLOCK` → `test_defect1_named_pipe_at_candidate_path_does_not_hang`
  fails exactly as expected (`AssertionError: True is not false` — the
  background thread is still alive after the 5s join, i.e. the FIFO open
  hangs again).
- Removing only `O_NOFOLLOW` → `test_in_bounds_symlink_candidate_is_now_categorically_unusable`
  fails (the in-bounds symlink is now followed and used, as it would have
  been pre-fix) — confirms that test is genuinely pinned to `O_NOFOLLOW`
  specifically, not some other check.
- Removing **either** `O_NOFOLLOW` alone or the post-open `/proc/self/fd`
  containment check alone → `test_defect2_symlink_swap_in_the_open_window_does_not_leak_outside_content`
  still passes in both cases: for this specific attack shape (the final
  path component becoming a symlink during the race), the two checks are
  genuinely redundant defense-in-depth, each independently sufficient.
  Removing **both** together → the test fails and reproduces the original
  leak verbatim (`content == "TOP SECRET HOST CONTENT THAT MUST NEVER
  LEAK"`), confirming the test is non-vacuous and that at least one of the
  two mechanisms is load-bearing.
- `test_defect2_intermediate_symlinked_directory_is_also_rejected` was
  added *because* this exploration surfaced that it's actually the cheap
  `_under_workdir()` pre-check (whose `os.path.realpath()` call resolves
  an entire path, intermediate components included, not just the final
  one) that rejects a symlinked intermediate directory in the current,
  non-racing case — not the post-open fd-based check as an earlier
  docstring draft (now corrected, see `_open_grounding_candidate()`'s
  in-source comment) overstated. Kept as its own test regardless, as a
  distinct real adversarial shape worth covering on its own terms.
- All reverts above were applied to a scratch copy of `app/teams.py`
  outside of git (the tracked file was restored byte-identical after each,
  confirmed via `diff`), never committed or left in place.

### Defect 3 (non-blocking, should-fix — documented, not built) — negated lines produce a misleading `found=True`
Per the reviewer's own assessment: this is inherent to a deliberately
"dumb," non-semantic conjunctive substring matcher, which is exactly what
the spec's "Non-goals" section asks for (semantic/LLM-assisted matching is
explicitly out of scope) — not treated as a defect to fix. Documented
below under "Known limitations" and pinned by a new test,
`test_defect3_negated_line_is_a_known_false_confirmation_documented_not_fixed`,
so a future change to the matcher doesn't silently alter this
already-known-and-accepted behavior without the limitations note being
revisited too.

### Defect 4 (non-blocking, should-fix — fixed as a consequence of the Defect 1/2 restructuring) — `_GROUNDING_READ_CAP_BYTES` didn't measure bytes for multi-byte-heavy content
**Root cause**: `_read_head()` (reused from `app.py`, opened in text mode)
reads up to `limit` *characters* via `f.read(limit)`, not bytes.
`_GROUNDING_READ_CAP_BYTES` was passed straight through as that character
limit, so a file of entirely multi-byte UTF-8 characters (e.g. 3-byte-each
`€` signs) could be read up to ~3-4x past the stated byte cap.

**Fix**: `_read_grounding_candidate()`'s replacement of `_read_head()`
reads raw bytes directly off the validated fd (`os.read(fd, ...)` in a
bounded loop, decoding to `str` only at the very end) — this was already
required by the Defect 1/2 restructuring (moving off `_read_head()`
entirely), and it fixes Defect 4 as a direct consequence: the byte count
read off disk is now genuinely bounded at `_GROUNDING_READ_CAP_BYTES`
regardless of character width. No separate patch was needed.

**Regression test added**: `test_defect4_read_cap_is_bytes_not_characters_for_multibyte_heavy_content`
— a file of `_GROUNDING_READ_CAP_BYTES` repetitions of `€` (3 bytes each in
UTF-8, so the file is ~3x the cap in real bytes) — asserts the returned
content's UTF-8-encoded length never exceeds the cap.

## Deviations from spec
- **No longer imports or reuses `app.py`'s `_read_head()`.** `docs/spec.md`'s
  "Background" section explicitly directs reusing `_read_head()` "rather
  than duplicating a bounded-read helper." This was followed in the
  original build. The round-1 review found that `_read_head()`'s own
  semantics — a fresh `open()` by path string, with no way to tie a
  containment check to the specific file that gets read — is structurally
  what made both Defect 1 (no `O_NONBLOCK`, so a FIFO blocks the open
  itself) and Defect 2 (validating a path and then re-opening that same
  path string later, with no way to bind the validation to the actual
  open, leaves an unavoidable TOCTOU window) possible. Closing both
  required moving to `os.open()` with explicit flags
  (`O_NOFOLLOW|O_NONBLOCK`) and fd-based validation
  (`os.fstat()`/`/proc/self/fd`), which `_read_head()`'s
  `open(path, "r", errors="ignore")` interface cannot express. This is a
  deliberate, review-directed deviation from the spec's literal reuse
  instruction, not an oversight — `_read_head()` is unaffected and remains
  exactly as-is in `app.py` (still used elsewhere, e.g.
  `_gather_project_context()`); only this module stopped calling it.
- **A real filesystem symlink at any candidate path is now unusable
  regardless of where it points**, not just when it resolves out-of-bounds
  — see Defect 2's writeup above for why this is the direct, necessary
  consequence of the TOCTOU fix, and why it doesn't violate any literal
  spec acceptance criterion.
- Otherwise none substantive beyond the original build's own "none
  substantive" assessment (exact function signatures, dict shapes, constant
  values/configurability split, CLI subcommand shapes all still match
  `docs/spec.md`'s "Proposed approach" as written).

## Known limitations
- **`TEAM_GROUNDING_MAX_BYTES`'s default (8000) is unmeasured against a
  real local model's context budget**, per the spec's own "Open questions"
  — carried forward unchanged.
- **Single-line-only `fact_check` matching** is deliberately conservative
  per the spec (precision over recall) — a true claim whose support spans
  two adjacent lines of a wrapped paragraph will not match. Per spec's own
  "Open questions," not built now.
- **Negated lines produce a misleading `found=True` confirmation**
  (docs/test-review.md Defect 3, non-blocking, deliberately not fixed): a
  grounding line reading e.g. "The lead never writes directly to
  `docs/BACKLOG.md`" will register as `found=True` "support" for the claim
  "the lead writes directly to `docs/BACKLOG.md`" — the exact opposite of
  what the line actually says. This is inherent to the spec's own
  deliberately dumb, non-semantic conjunctive substring matcher (semantic
  matching is an explicit non-goal) and is not being fixed, but it directly
  undercuts the stated precision bias for a very common real documentation
  pattern ("X never...", "X does not..."). **6c's lead-loop side, which
  will eventually treat a `fact_check` `found=True` result as confirmation,
  should be aware of this failure mode** — flagging it here as a candidate
  follow-up open question for that sub-spec, the same way the
  single-line-only limitation already is. Pinned by
  `test_defect3_negated_line_is_a_known_false_confirmation_documented_not_fixed`.
- **A real filesystem symlink at any of the four candidate paths is now
  unusable, even one that legitimately resolves in-bounds** (deliberate
  behavior change, see "Deviations from spec" and Defect 2 above) — a
  project author who genuinely wants (say) `README.md` to be a symlink to
  a file elsewhere in the same repo will find it silently excluded from
  grounding, the same as an out-of-bounds one. No acceptance criterion
  requires the in-bounds case to work, but flagging this explicitly as a
  real, if narrow, usability cost of the security fix.
- **6b intentionally ships no HTTP route, no UI, no lead loop, no
  `fact_check`-as-LLM-tool wiring** — all deferred to 6c/6d/6e/6f per the
  spec's own non-goals.

## Verification status
| Check | Command | Result |
|---|---|---|
| New grounding test file alone | `python -m pytest tests/test_teams_grounding.py -q` | 65 passed |
| Full suite, 8 total runs across this round (5 at the 64-test intermediate state, 3 at the final 65-test state, after the last regression test was added) | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` | `436`/`437 passed` respectively (372 pre-existing + 64 or 65 new) every time, ~34-35s each, no flake observed |
| Full suite, alternate harness | `python3 -m unittest discover -s tests` | `Ran 437 tests ... OK` |
| No pre-existing file touched | `git diff --stat -- tests/`, `git diff -- app/app.py` | both as expected — only `tests/test_teams_grounding.py` changed among test files (new/untracked, not a modification of an existing file), `app/app.py` byte-identical (0 diff lines) |
| Syntax/compile | `python3 -m py_compile app/teams.py tests/test_teams_grounding.py` | clean |
| CLI, real repo tree | `python3 app/teams.py grounding "$(pwd)"` | real JSON: discovers `docs/ARCHITECTURE.md`, `docs/BACKLOG.md`, `README.md`, digest capped at `TEAM_GROUNDING_MAX_BYTES` |
| CLI, `fact_check`, real repo tree | `python3 app/teams.py fact-check "$(pwd)" "Nothing else. A bug in this stdlib-only app is not an instant path"` | `found: true`, `docs/ARCHITECTURE.md:12` |
| CLI, FIFO (Defect 1 live repro) | `mkfifo $tmp/README.md; time timeout 6 python3 app/teams.py grounding $tmp` | returns in `0.05s` with `empty: true` (previously hung until an external `timeout` killed it) |

One earlier full-suite attempt this round appeared to stall at ~16% when
run as one of five sequential invocations inside a single 2-minute-capped
shell call — investigated via a separate, unbounded background run of the
identical command: it completed cleanly in the normal ~35s with all 436
tests passing. The apparent stall was the outer tool call's own 120-second
default timeout being exceeded by five sequential ~35s runs (5×35s > 120s),
not a hang in the code or the test suite — noted here since it's exactly
the kind of thing worth not silently omitting, but it is not a real
flake: every full-suite run that was allowed to actually finish, finished
clean.

No project lint/CI config exists in this repo — the test suite above is
this project's own bar, matching 6a's own verification approach.

## How to verify locally
```bash
# New grounding test file only
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_teams_grounding.py -v

# Full existing suite (nothing pre-existing touched, but a good sanity pass)
python3 -m unittest discover -s tests -v

# Same, via pytest
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# CLI, against this repo's own tree (no server, no UI):
export TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x
export ENGINES_DIR=$(pwd)/engines.d PROJECTS_DIR=/tmp/scratch-projects-6b
python3 app/teams.py grounding "$(pwd)"
python3 app/teams.py fact-check "$(pwd)" "some phrase you know appears in README.md/docs/ARCHITECTURE.md/docs/BACKLOG.md"

# Defect 1's live repro (should return immediately, not hang):
mkdir -p /tmp/fifotest && rm -f /tmp/fifotest/README.md && mkfifo /tmp/fifotest/README.md
timeout 6 python3 app/teams.py grounding /tmp/fifotest
```
