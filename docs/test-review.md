# Test & Review: Grounding — discovery, digest, and `fact_check` (backlog item 6, sub-spec 6b)

## Scope
Round 2. Round 1 (testing pass) found two blocking defects (a named-pipe
hang, a TOCTOU race defeating symlink containment) plus two non-blocking
should-fix items (negated-line false confirmation, byte-vs-character read
cap). The developer's round-1 fix restructures file access around a single
`os.open(O_RDONLY|O_NOFOLLOW|O_NONBLOCK)` → `os.fstat()` → fd-pinned
`/proc/self/fd` containment re-check → single bounded read, replacing the
original `app.py`-`_read_head()`-based double-read design. This round
re-verifies both fixes at the class level (not just the original two
repros), re-runs the full suite, confirms the 9 new tests are non-vacuous,
answers the coordinator's in-bounds-symlink question, then — since testing
came back clean — completes the review pass deferred from round 1.

## Test cases — re-verification of round-1 fixes

| # | Item | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Regression suite | `pytest tests/ -q`, 3 separate runs this round | pass, no flake | `437 passed` every time (372 + 65) |
| 2 | No pre-existing test modified, `app/app.py` untouched | `git diff --stat -- tests/`, `git diff -- app/app.py` | pass | tests/ diff empty (only the new/untracked file); app.py 0 diff lines |
| 3 | 9 new tests genuinely fail against the pre-fix (round-1) module | swapped `app/teams.py` for the saved pre-fix version, ran the new/changed tests, restored | pass — non-vacuous | 7 explicit failures (the 2 not expected to fail as regressions — `test_defect2_intermediate_symlinked_directory_is_also_rejected` and `test_defect3_negated_line...` — correctly did **not** fail, since both already held/were already-accepted pre-fix, exactly as the developer's own writeup claims); the two FIFO-hang tests failed *cleanly* via their bounded `join(timeout=5)`, not by hanging the test run — confirms that design choice actually works |
| 4 | Defect 1 fix (`O_NONBLOCK`) is load-bearing | my own revert-and-watch-it-fail: removed `O_NONBLOCK` only, re-ran the 3 Defect-1 tests | fails exactly as expected (FIFO hangs again, all 3 fail) | restored, byte-diff clean |
| 5 | Defect 2 fix, `O_NOFOLLOW` + fd-recheck redundancy claim | my own revert-and-watch-it-fail: removed **both** `O_NOFOLLOW` and the `/proc/self/fd` recheck together | fails and reproduces the exact original leak (`content == "TOP SECRET HOST CONTENT THAT MUST NEVER LEAK"`) | restored, byte-diff clean, confirms the test is non-vacuous and at least one mechanism is load-bearing, matching the developer's own claim |
| 6 | Defect 4 (byte vs. character read cap) fix | my own script, independent of the new test | pass | 3,000,000×`€` (3 bytes each) → `content` byte length `2,097,150` ≤ cap `2,097,152` (was `6,291,436` — ~3x over — against the pre-fix code in round 1) |
| 7 | *(beyond the two original repros)* symlink-to-a-FIFO at a candidate path | my own script (real `os.mkfifo` + `os.symlink` to it), bounded thread join | pass — rejected via `O_NOFOLLOW` before the FIFO's blocking behavior is ever reached; no hang | `hung=False`, `empty=True` |
| 8 | *(beyond spec)* Unix domain socket at a candidate path | my own script (`socket.bind()`), bounded thread join | pass — rejected via the `S_ISREG` `fstat` check; no hang | `hung=False`, `empty=True` |
| 9 | *(beyond spec)* device node at a candidate path | not tested — creating a real device node requires `CAP_MKNOD`/root, unavailable in this sandbox | not directly tested, but same codepath as the socket case (`fstat` → `S_ISREG` check) would reject it the identical way; low-risk gap, noted, not blocking |
| 10 | *(beyond spec)* symlink whose parent directory is itself a symlink | developer's own `test_defect2_intermediate_symlinked_directory_is_also_rejected`, independently re-read and reasoned through (caught by `_under_workdir`'s whole-path `realpath()`, not the fd check) | pass | ran as part of the full suite; reasoning double-checked against the code, correct |
| 11 | *(beyond spec)* symlink created in the window **between `os.open()` succeeding and `os.fstat()` running** (narrower than the pre-check→open window Defect 2 fixed) | my own script: monkeypatched `os.fstat` to swap the path to a symlink-to-secret as a side effect on its first call, before delegating to the real `fstat` | pass — **not exploitable**, correctly returns the *original* file's content, not the swapped target's, confirming the file descriptor is pinned to the already-open inode and cannot be affected by a later path-level change (this is the Unix guarantee the fix's whole approach rests on) | `content` == the original real content, not `"SECRET"` |
| 12 | *(beyond spec)* `/proc/self/fd` unavailable (e.g. a container without procfs mounted) | my own script: monkeypatched `os.path.realpath` to return the literal (unresolved) string for any `/proc/self/fd/...` argument, simulating an unresolvable procfs entry | **degrades silently to permanently-empty grounding for every project, no exception, no signal** — see Finding 3 below | `entries == []`, `g["empty"] == True` for a genuinely valid in-bounds file |

## Regression check
`/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` — **437 passed**, 3 separate runs this round, no flake. `git diff --stat -- tests/` and `git diff -- app/app.py` both confirm no pre-existing file touched.

## Answer to the coordinator's in-bounds-symlink question
**Silent skipping is defensible but not the best outcome, and I'd call it a should-fix, not a blocker.** The module already treats "empty/missing/unusable" as one unified, deliberately-unobservable-in-detail category by design (this predates the fix — the original spec explicitly unifies missing/empty/permission-denied/directory/symlink-loop into the same outcome). Extending that same unification to "rejected because it's a symlink, even an in-bounds one" is consistent with that existing precedent and isn't a spec violation. But the module's own design already cares about *some* observability — `empty: bool` and `_GROUNDING_NO_FILES_DIGEST` exist specifically so "genuinely nothing found" is legible to a caller rather than silently indistinguishable from "not yet loaded." A symlinked `README.md` is a plausible real setup (a monorepo, a shared-docs template) and its rejection is invisible at every level: not in `files`, not in `empty`'s reasoning, no separate `skipped`/`rejected` list, no log line. An operator debugging "why is grounding empty for this project" has no path to discovering the cause without already knowing to suspect a symlink and going and checking by hand. Recommend a lightweight follow-up (not blocking this round): either a `skipped: [{"label":..., "reason": "symlink"}]` entry in the returned dict, or at minimum a one-line log/stderr note — something that turns "grounding is mysteriously empty" into "grounding found a symlinked README.md but can't use it." This is already honestly documented in `docs/implementation.md`'s "Known limitations," which is the right thing to have done even before deciding whether to build the visibility improvement.

## Spec coverage
Re-checked against `docs/spec.md`'s full acceptance-criteria list (all 22 items from round 1, unchanged by this round's fix): every criterion still traces to a passing automated test, all still hold under the restructured implementation. No acceptance criterion requires in-bounds symlink support (confirmed by re-reading the criteria list directly, not just trusting the developer's own claim), so the deliberate behavior narrowing doesn't create a spec gap. No acceptance criterion covers named pipes, sockets, or the TOCTOU race either — both defects were found via testing beyond the literal list, as flagged in the original brief, and both are now closed with dedicated regression tests.

## Findings (most severe first)

### 1. `fact_check`'s single-line matching is a real, not just theoretical, usefulness constraint for this repo's actual documentation style — should-fix / strong recommendation, non-blocking
- Tried 6 realistic claims a lead might plausibly generate against this
  repo's own `docs/ARCHITECTURE.md` (a close paraphrase of an actual
  sentence, several natural paraphrases of true architectural facts):
  only 2/6 returned `found: True`, and one of those only because its
  particular supporting text happened to fit on one unwrapped line. The
  other 4 failures were not because the claims were false or the terms
  didn't appear — every failure was `docs/ARCHITECTURE.md`'s own wrapped
  bullet-point prose splitting the supporting text across two lines (e.g.
  "runs as `SVC_USER`... an" / "unprivileged system account..." — a single
  sentence, two lines), which single-line-only matching structurally
  cannot join.
- This is explicitly the spec's own known, accepted limitation ("Open
  questions": join adjacent non-blank lines into one matchable unit, if
  6c's real usage shows this is too conservative) — not a defect, and not
  something I'm asking this round to fix. But my testing turns "if 6c's
  real usage shows this" from a hypothetical into a concrete, repo-specific
  measurement: for *this* project's actual prose style, single-line-only
  matching fails the *majority* of natural true claims I tried, not a rare
  edge case. Recommend the product-manager treat the paragraph-joining
  follow-up as a near-term (not someday-maybe) item for 6c, and that 6c's
  own acceptance testing include a similar realistic-claims exercise before
  concluding the tool is useful enough to wire into the lead loop.

### 2. In-bounds symlink rejection is invisible to the caller — should-fix, non-blocking
See "Answer to the coordinator's in-bounds-symlink question" above.
Recommend a `skipped`/reason list or a log line as a lightweight follow-up;
not required by any acceptance criterion, already honestly documented as a
known limitation.

### 3. `/proc/self/fd` unavailability degrades to silent, total, unexplained emptiness — nit, non-blocking
If `/proc` isn't mounted (some minimal containers, some restricted
namespaces), `os.path.realpath("/proc/self/fd/<fd>")` returns the literal,
unresolved string, which never matches `workdir_real`, so **every**
candidate for **every** project gets rejected as "out of bounds" — silently,
with no exception and no distinguishing signal from "this project genuinely
has no docs." Given the module (and this whole app) is already
Linux-only and `/proc` is virtually always present on Linux, this is low
practical risk, but it's a real, defined-but-surprising failure mode worth
a one-line comment or a startup sanity check (`os.path.exists("/proc/self/fd")`)
producing a clearer signal than universal silent emptiness, especially
since the switchboard's own install/deploy story already involves
containerized/service contexts elsewhere in this repo.

### 4. Device-node candidate not directly tested — nit, non-blocking
Couldn't construct a real device node in this sandbox (needs
`CAP_MKNOD`/root). The `fstat`-based `S_ISREG` check is the same mechanism
already verified against a FIFO, a directory, and a Unix domain socket
(3 distinct non-regular `st_mode` values, all correctly rejected without
hanging), so I have high confidence a device node is handled identically,
but it's not something I directly observed this round.

## Follow-ups (non-blocking)
- Findings 1–4 above, roughly in priority order for a near-term pass.
- Defect 3 (negated-line false confirmation, from round 1) remains
  deliberately unfixed and documented — re-confirmed still correctly pinned
  by `test_defect3_negated_line_is_a_known_false_confirmation_documented_not_fixed`
  and still listed in `docs/implementation.md`'s "Known limitations." No
  change requested; carrying forward as a 6c-facing heads-up, same as the
  developer already flagged it.

## Complexity / simplicity assessment
**Earns its complexity.** The three-function layering
(`_open_grounding_candidate` → `_read_grounding_candidate` →
`_discover_and_read`) is exactly what fd-based TOCTOU elimination requires
— there's no simpler shape that closes the actual race (a faster
`realpath()`-then-`open()` narrows but never eliminates the window, as the
developer's own writeup correctly reasons through). No speculative
generality found: no unused parameters, no hooks anticipating 6c's
tool-calling shape, no configuration surface beyond what round 1 already
had. **No patch-on-patch structure**: the old `_looks_binary`/
`_candidate_usable`/`_has_grounding_content` helpers are fully removed
(`grep` confirms zero remaining references outside historical-context
comments/docstrings), not left behind as dead code alongside the new path;
the AST scan's function-name list and the read-only runtime test were both
properly updated to the new function set rather than patched around;
`app.py`'s own `_read_head()` is untouched and still used by
`_gather_project_context()` elsewhere, so dropping it from this module's
own import line was a clean removal, not a fork. `discover_grounding_files()`
is a genuinely thin (one-line) wrapper around `_discover_and_read()`, not a
second implementation.

## Overall verdict
**Approve with follow-ups.** Both round-1 blocking defects are independently
re-verified fixed at the class level, not just against their original two
repros — I additionally exercised a symlink-to-FIFO, a Unix domain socket,
the open()-to-fstat() race window specifically (confirmed the fd-pinning
property that the whole fix rests on actually holds), and the /proc-
unavailable degradation mode, none of which were in the original two
repros. The 9 new tests are confirmed non-vacuous (fail against the pre-fix
module, including the FIFO tests failing *cleanly* via their own bounded
join rather than hanging the suite). Regression suite is clean across 3
consecutive runs (437/437, no flake). Spec coverage is complete — no
acceptance criterion is unimplemented or untested, and the deliberate
in-bounds-symlink behavior narrowing doesn't violate anything the spec
pins down. The diff itself is clean: no dead code, no patch-on-patch
structure, no unjustified complexity, `app/app.py` genuinely untouched.

Four non-blocking findings are carried forward as follow-ups (most notable:
Finding 1, which gives the product-manager/6c concrete, repo-specific
evidence that single-line-only matching may need its near-term follow-up
sooner rather than later for the feature to be *useful*, not just safe).
None of the four block this round — none violate a spec requirement, and
each is either already honestly self-documented by the developer or a low-
practical-risk edge case.

This build cycle is done — hand control back to the product-manager agent
for the next iteration.
