# Implementation: Headless engine invocation (backlog item 6, sub-spec 6a)

## Summary
Added `app/teams.py` — a new, stdlib-only module with `agent_run(engine,
workdir, prompt, session_id=None, timeout=..., log_path=None)`, which spawns
one bounded, non-interactive turn of a named engine inside a throwaway tmux
session (as `RUN_USER`, via the *existing* `TMUX` sudoers rule — zero new
privilege surface), translates its native NDJSON/plain-text output into the
normalized event envelope from `docs/story.md` §4.1, appends it to a durable
`.jsonl` log, and returns a normalized result dict. A small `argparse` CLI
(`python3 app/teams.py run|list-engines`) exercises the same code path with
no server and no UI. `app/app.py`'s `Engine`/`_parse_engine_file()` gained
four optional `HEADLESS_*` keys plus a reserved `switchboard` engine-name
prefix; `engines.d/claude.engine`, `codex.engine`, `aider.engine` each gained
working `HEADLESS_*` lines, with per-engine verification status documented
below (claude: full live verification; codex: partial — CLI installed but
not authenticated in this environment; aider: unverified — not installed).
Three review rounds found and fixed three blocking/must-fix defects
post-initial-build — see "Post-review fixes" below: round 1 (an uncaught
`OSError` + run-directory leak for a quote-heavy `arg`-mode prompt within
the documented cap), round 2 (the round-1 fix's own byte cap modeled the
wrong ceiling — Linux's `MAX_ARG_STRLEN` instead of tmux's own, much
smaller, internal command-passing-protocol limit — fixed by writing the
generated script to a file instead of passing it inline as a tmux command
argument), and round 3 (the two translator functions crashed uncaught on
syntactically-valid-JSON-but-unexpected-shape native events — fixed with a
boundary guard around the translator call, not per-site defensiveness).
Round 3 also fixed two should-fix findings from the pipeline's first
completed review pass (stale operator-facing docs, explicit `chmod` on
`run.sh`/the prompt file instead of relying on ambient umask).

## Root cause
N/A — new capability, not a bugfix.

## Changes by file
- `app/app.py`
  - `Engine.__slots__` gained four fields (`headless_cmd`, `headless_format`,
    `headless_prompt`, `headless_resume`), all optional, all defaulting to
    `None`, plus a read-only `headless_enabled` property
    (`bool(headless_cmd and headless_format and headless_prompt)`).
  - `_parse_engine_file()` (~line 294): parses the four new keys; if
    `HEADLESS_CMD` is present but `HEADLESS_FORMAT`/`HEADLESS_PROMPT` are
    missing or not one of the recognized values (`_HEADLESS_FORMATS` =
    `{claude-stream-json, codex-jsonl, plain}`, `_HEADLESS_PROMPT_MODES` =
    `{arg, stdin, file}`, both new module-level sets), the rest of the file
    still parses normally and the headless fields are left `None` — never an
    exception, matching the function's existing best-effort philosophy.
  - Added the reserved-prefix check: any `.engine` file whose filename stem
    starts with `switchboard` is ignored (`return None`), same treatment
    `.engine.example` gets — this is what makes the tmux session-naming
    scheme in `app/teams.py` structurally collision-proof (see docs/spec.md
    "Session naming" for the exact non-obvious collision this closes).
  - No other line in `app.py` touched — `TMUX`, `tmux_has()`,
    `instance_start()`/`instance_stop()`/`_reap_dead_state()` are all
    untouched, read-only imports for `teams.py`.
- `app/teams.py` (new, ~850 lines) — `agent_run()` plus:
  - Pure, independently-tested helpers: `_resume_fragment()` (the `{resume}`
    substitution + the "session_id without resume support" `ValueError`),
    `_validate_prompt_size()` (the two byte caps), `_build_headless_argv()`
    (renders `HEADLESS_CMD` into an argv list via `str.replace()`, never
    `str.format()`), `_build_script()` (the script text: redirect(s),
    background, `$!` capture, `wait`, `echo $?` — written to `RUNDIR/run.sh`
    and run via `bash -l <path>` by `agent_run()`, not passed inline; see
    "Post-review fixes → Round 2" below).
  - `_translate_claude()` / `_translate_codex()` — the native-event →
    `(kind, text, meta)` translation tables (see "Key decisions" below for
    what's real vs. inferred). Both are deliberately left free to raise on
    an unexpected native-event shape (no per-branch defensive guards); the
    no-raise guarantee lives one level up.
  - `_translate_safely()` — the boundary guard around a translator call
    (round 3, Finding 1): catches any exception either translator raises
    and returns `(events, error_message)` instead of propagating it.
  - `_Tailer` — incremental byte-offset tailing, malformed-line/stream-cap/
    shape-crash handling (via `_translate_safely()`), per-format
    final-text extraction, session-id capture.
  - `_run_headless_session()` — startup confirmation (poll `out.pid` up to
    5s), the tail/poll loop, completion-ordering (`rc_path` before
    `tmux_has()`), and the `SIGTERM → grace → SIGKILL → grace →
    kill-session` escalation state machine.
  - `_sweep_stale_runs()` — the opportunistic sweep at the top of every
    `agent_run()` call.
  - CLI: `_parse_args()`/`_cli_run()`/`_cli_list_engines()`/`main()`,
    matching `scripts/taiga_push_spec.py`'s shape. `run` streams translated
    events to stderr live (a background thread tails the `.jsonl` log file
    while `agent_run()` blocks in the foreground) and prints the final
    result dict as JSON to stdout.
- `engines.d/claude.engine`, `engines.d/codex.engine`, `engines.d/aider.engine`
  — each gained a `HEADLESS_*` block (see "Verification status" below for
  what's confirmed vs. believed-correct-but-unrun per engine).
- `config/switchboard.env.example` — new commented-out section:
  `TEAM_STATE_DIR` plus the nine `TEAM_HEADLESS_*` tuning knobs, same
  documented-but-commented-out style as `GITEA_POLL_INTERVAL_SECONDS`.
- `docs/ADDING_AN_ENGINE.md` — new "Headless invocation" section documenting
  the four keys, the two placeholder mechanics, the reserved-prefix rule,
  the `HEADLESS_ROLE_FLAG`/`HEADLESS_SCHEMA_FLAG`/`HEADLESS_LEAD_FORMAT`
  reserved-for-6c note, and the per-engine verification status.
- `tests/test_teams_headless.py` (new, 83 tests — 60 at initial build, 12
  added in the round-1 fix pass, 2 added in the round-2 fix pass, 9 added
  in the round-3 fix pass, see "Post-review fixes" below) — Tier 1 (engine
  parsing, pure argv/script builders including the `shlex.quote()`-aware
  `arg`-mode cap, envelope translation against fixtures, the
  `_translate_safely()` boundary guard's own unit tests, `_Tailer`
  malformed-line/truncation-cap/shape-crash behavior, `agent_run()`
  validation-only paths with `subprocess.run`/`tmux_has` monkeypatched to
  fail the test if called, the `OSError`-from-spawn defense-in-depth path)
  and Tier 2 (real `tmux`, test-authored Python helper "engines", no sudo —
  success stream end-to-end, stdin prompt delivery, `SIGTERM`-only stop,
  `SIGTERM`-ignored escalation to `SIGKILL`, externally-sent `SIGTERM`
  classified `cancel_reason="external"`, forced `tmux kill-session` mid-run
  classified as `cancelled`/no exit code, resume-unsupported-engine raises
  before any session is created, permission-denied state file, stale-run
  sweep, ordinary 20KB/17KB plain-text `arg`-mode prompts actually running
  through real tmux, a shape-crash line surviving the full real `agent_run()`
  path, `run.sh`/prompt-file world-readability under a strict umask).
- `tests/fixtures/headless/` (new) — `claude_stream.jsonl` (real capture),
  `codex_stream_authfail.jsonl` (real capture, auth-failure path),
  `codex_stream_success.jsonl` (synthesized), `aider_output.txt`
  (synthesized), `README.md` documenting exactly which is which.

## Key decisions / tradeoffs
- **`_build_headless_argv()`/`_build_script()` are the actual functions
  `agent_run()` calls**, not parallel logic — Tier 1 tests exercise the
  identical code path `agent_run()` uses, so there's no risk of the tested
  behavior drifting from the real behavior.
- **The claude-stream-json/codex-jsonl translation tables were designed
  fresh for this implementation**, not copied from a pre-existing table —
  `docs/spec.md` says the §4.1 translation table is "unchanged from the
  prior version," but that prior version's exact table wasn't available to
  read; the closed `kind` set (`message|tool_use|tool_result|status|error|
  handoff`) from `docs/story.md` §4.1 was the actual contract, and the
  per-native-type mapping was built against real captured output (see
  below) plus `docs/story.md` §2.1's documented event types. Claude's
  `assistant` content-block types actually observed live include `thinking`
  (deliberately mapped to `status`, not `message`, so it can never leak into
  the assistant-text fallback used for the run's own final `text`) and
  `rate_limit_event` as a top-level type outside the four documented ones —
  both handled, with any further unrecognized-but-valid type passed through
  as a `status` event rather than silently dropped.
- **Two-tier byte caps** (`TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES` vs.
  `TEAM_HEADLESS_PROMPT_MAX_BYTES`) implemented exactly as specified —
  `arg` mode's cap exists because the whole generated script string becomes
  one `bash -lc` argv element, bounded by `MAX_ARG_STRLEN`.
- **`cancel_reason="external"` classification bug found and fixed during
  self-testing**: the first implementation only ever set `cancel_reason` when
  `agent_run()`'s own timeout escalation fired, leaving it `None` for a
  signal-shaped exit code sent by something else entirely (a future "stop
  team" action, an operator's own `kill`). Caught by writing
  `test_externally_sent_sigterm_is_classified_as_cancelled_external` (sends
  `SIGTERM` directly to the discovered engine PID, bypassing `agent_run()`'s
  escalation state machine) — fixed with `reason = cancel_reason or
  ("external" if cancelled else None)`.
- **`_HeadlessReadPermissionError`** — a dedicated internal exception
  distinguishing "file not there yet" (`FileNotFoundError`, keep polling)
  from "file there but unreadable" (`PermissionError`, a real umask problem)
  in every state-file read (`out.pid`, `out.rc`, `out.jsonl`, `out.err`),
  surfaced as a specific, non-hanging `ok=False` error rather than either an
  unbounded wait or an uncaught crash (docs/spec.md "Edge cases").
- **Tier 2 tests use real `tmux`** (this sandbox's own user, `dev`, can run
  bare `tmux` directly — no `sudo`/`RUN_USER` account needed), with
  test-authored Python helper scripts standing in for "the engine" exactly
  as `docs/spec.md`'s test plan describes, including a real, live-confirmed
  `SIGTERM` → `128+15` and `SIGTERM`-ignored → `SIGKILL` → `128+9`
  escalation, not just an assumption from POSIX docs.
- **CLI event-streaming implementation**: since `agent_run()`'s signature has
  no callback parameter (matching `docs/spec.md`'s literal signature), the
  CLI's `run` subcommand streams events by tailing the `.jsonl` log file from
  a background thread while `agent_run()` blocks in the foreground, rather
  than adding a non-spec parameter to `agent_run()` itself.

## Post-review fixes (docs/test-review.md, testing pass — blocked twice)

## Round 1

### Defect 1 (must-fix, blocking) — uncaught `OSError` + run-directory leak
**Root cause**: `_validate_prompt_size()` capped the *raw* `arg`-mode prompt
byte count against `TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES`, but the value that
actually has to fit under Linux's `MAX_ARG_STRLEN` (131072 bytes) is the
**post-`shlex.quote()`** length of that same prompt once it's folded into
`_build_script()`'s generated script — and `shlex.quote()` replaces every
`'` with `'"'"'`, up to a 5x length blowup for quote-heavy content. A
30,000-byte prompt of mostly single quotes (well under half the old raw
cap) expands to a ~150,000-byte script element, over the kernel limit,
raising `OSError: [Errno 7] Argument list too long` straight out of
`subprocess.run()` at the `tmux new-session` call — which sat *outside*
`agent_run()`'s own `try/finally`, so `rundir` was never cleaned up either.
Reviewer-verified live; reproduced independently in this pass too (see
below).

**Fix**:
1. `_validate_prompt_size()` (`app/teams.py`) now measures
   `len(shlex.quote(prompt).encode("utf-8"))` for `arg` mode, not the raw
   prompt length — the check reflects what's actually about to be spawned.
   The effective cap is `min(TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES,
   _MAX_ARG_STRLEN - _ARG_SCRIPT_OVERHEAD_BYTES)` (two new module
   constants: `_MAX_ARG_STRLEN = 131072`, `_ARG_SCRIPT_OVERHEAD_BYTES =
   4096` reserved for the rest of the generated script). `stdin`/`file`
   mode's cap is unchanged (raw byte count — the prompt never enters any
   argv element in those modes).
2. `agent_run()` restructured so **everything** fallible from `rundir`
   creation through the `tmux new-session` spawn now lives inside the same
   `try/finally` that owns `shutil.rmtree(rundir, ...)` — not just the
   `_run_headless_session()` call that already had that protection. This
   closes the leak class generally, not just Defect 1's one instance (the
   reviewer specifically asked for this).
3. The `tmux new-session` `subprocess.run()` call is now wrapped in its own
   `try/except OSError`, translated into the same well-formed `ok=False`
   result shape the "bash -lc fails before backgrounding anything" edge
   case already produces (`error="failed to start headless session: ..."`)
   — defense in depth beyond the tightened cap, for any input that somehow
   still slips past validation (e.g. an unusually long `workdir`/
   `TEAM_STATE_DIR` path contributing to the same `MAX_ARG_STRLEN` ceiling).
   `agent_run()` no longer raises `OSError` under any input this pass could
   construct, so the CLI's `main()` (which only ever caught `ValueError`)
   no longer needs to change either.

**Regression tests added** (`tests/test_teams_headless.py`):
`test_quote_heavy_prompt_well_under_old_raw_cap_now_rejected` /
`test_plain_text_of_the_same_raw_length_is_still_accepted` /
`test_quoted_length_exactly_at_the_cap_is_accepted` /
`test_quoted_length_one_over_the_cap_is_rejected` (pure, exercise the
fixed validator directly — the first one uses `"'" * 30000`, the reviewer's
exact repro shape; a same-length plain-text prompt is asserted to *pass*,
proving the fix targets the quoting blowup specifically, not just a smaller
number), `test_quote_heavy_arg_prompt_raises_value_error_not_os_error_nothing_spawned`
(integration-level, `agent_run()` itself, with `subprocess.run`/`tmux_has`
forbidden — proves the rejection happens before anything is spawned and no
`rundir` is left behind), and
`test_os_error_from_new_session_spawn_returns_clean_result_and_cleans_up_rundir`
(`subprocess.run` mocked to raise `OSError` specifically on the
`new-session` call, proving the defense-in-depth catch + cleanup work
independently of the validator). Also reproduced live against real `tmux`
outside the test suite with the reviewer's exact repro (30,000-byte
all-quotes prompt) — now `ValueError`, zero directories created (previously
an uncaught `OSError`, one leaked directory per call).

### Finding A (non-blocking, fixed) — `_translate_claude()` `user`-branch asymmetry
The `assistant` branch and the top-level unknown-`type` fallback both
already followed "nothing is lost" (`docs/story.md` §4.1) by emitting a
`status` event for anything unrecognized; the `user` branch didn't — a
`user`-type event whose content block wasn't `tool_result` (e.g. a plain
`text` block) produced zero events. Fixed to match the same pattern: an
unrecognized-but-present block type now emits `("status", "",
{"native_type": "user", "block_type": ..., "session_id": ...})`, same as
the `assistant` branch's own `else` clause. Non-dict blocks are still
silently skipped, matching the `assistant` branch's existing (also
symmetric) defensive handling there. Tests added:
`test_user_event_non_tool_result_block_is_not_silently_dropped`,
`test_user_event_with_no_recognized_content_still_returns_empty_list_not_none`.

### Coverage gaps closed (non-blocking, both previously verified only by hand)
- `_HeadlessReadPermissionError` handling —
  `test_permission_denied_state_file_returns_clean_error_not_a_hang`
  (real tmux, `chmod 000` on `out.jsonl` mid-run from a second thread while
  `agent_run()` is blocked in its poll loop; as this test suite always runs
  as a non-root user, `chmod 000` genuinely denies even the owning
  process's own `open()`, reproducing the "RUN_USER's umask is unusually
  strict" edge case for real, not simulated). Asserts `ok=False`,
  `cancelled=False`, a specific umask-mentioning error message, no hang,
  and clean session/rundir teardown.
- `_sweep_stale_runs()` — three new tests:
  `test_sweep_removes_aged_dir_with_no_live_session`,
  `test_sweep_leaves_a_fresh_dir_untouched_and_spawns_nothing_for_it`
  (confirms the age-check short-circuits *before* any `tmux`/`subprocess`
  call at all), `test_sweep_kills_live_session_for_an_aged_dir_and_removes_it`
  (creates a real live tmux session matching the aged directory's name,
  confirms the sweep's defensive `kill-session` actually tears it down and
  the now-session-less directory is then removed).

All of the above lives in the same three test classes the original
implementation added (`HeadlessArgvBuildingTests`, a new
`AgentRunOSErrorHandlingTests`, `AgentRunValidationNoSpawnTests`,
`ClaudeTranslationTests`, `RealTmuxHeadlessTests`) — no new test file, no
pre-existing test touched.

## Round 2

### Defect 2 (must-fix, blocking) — the round-1 byte cap defended against the wrong ceiling
**Root cause**: round 1's fix correctly identified that `HEADLESS_PROMPT=arg`
needs a byte cap on the *shell-escaped* prompt length, and correctly modeled
that check against Linux's `MAX_ARG_STRLEN` (131072 bytes) — the kernel's
ceiling on a single `execve()` argv element. But that wasn't the ceiling
that actually binds first. The generated script (including the
shell-escaped prompt) was passed as a single argv element to `bash -lc
<script>`, which was itself spawned via `tmux new-session ... bash -lc
<script>` — and **tmux's own client→server command-passing protocol** has
an internal length limit of its own, completely unrelated to any kernel
constant. Measured directly against the stock Debian-packaged `tmux 3.5a`
using a `_build_script()`-shaped command: tmux accepts up to ~16,318 bytes
of total script length and rejects anything past that with `command too
long`/`failed to send command`, exiting 1 **before ever creating the
session** — roughly 1/8th of `_MAX_ARG_STRLEN`, and about 4x smaller than
the round-1 default cap (65536 bytes). A completely ordinary, non-adversarial
20,000-byte plain-text prompt (no quotes, no shell metacharacters — e.g. a
pasted diff or a multi-paragraph task description, squarely normal usage
for `HEADLESS_PROMPT=arg`, which is what both real shipped engines
`claude.engine`/`codex.engine` use) passed round 1's validator cleanly and
then silently failed with `agent_run()`'s generic `"headless session failed
to start"` message — no crash, no leak (round 1's fixes held), but the run
never happened at all, for entirely ordinary input well inside the
documented "safe" cap.

**Fix — structural, not a smaller magic number.** Per the reviewer's own
explicit direction: tuning the cap down to ~16KB would trade one
version/build-dependent wrong number for another (tmux's own internal
buffer size is an implementation detail, not a portable constant) and would
leave `arg` mode too small for realistic delegation prompts regardless. The
actual fix stops passing the script as a tmux command argument at all:

1. `agent_run()` now writes the generated script to `RUNDIR/run.sh` (the
   same `SVC_USER`-owned, `0711` directory the `prompt_path` file for
   `stdin`/`file` mode already lived in — no new privileged path, no new
   sudoers surface) instead of handing it to `subprocess.run()` as one huge
   argv element.
2. The tmux invocation changed from
   `TMUX + ["new-session", "-d", "-s", session, "-c", workdir, "bash", "-lc", script]`
   to
   `TMUX + ["new-session", "-d", "-s", session, "-c", workdir, "bash", "-l", script_path]`.
   The tmux command line is now small and **constant-length regardless of
   prompt size**, so its own protocol limit stops being reachable at all —
   not just moved further out. Confirmed live: the exact same
   `_build_script()`-shaped command that failed inline past ~16.3KB now
   succeeds through tmux at 16.4KB, 20KB, 65536 bytes, and 130000 bytes,
   all via the file-based invocation.
3. **`bash -l <script_path>` preserves the same login-shell startup-file
   sourcing `bash -lc` had** (`/etc/profile`, `~/.bash_profile`/`~/.profile`)
   — this matters because that's what makes `RUN_USER`'s own `PATH`
   extensions (nvm/pipx/etc.) findable at all (docs/spec.md "Background").
   Confirmed live, byte-for-byte identical `$PATH` output between
   `bash -lc "echo $PATH"` and `bash -l <file containing echo $PATH>` for
   this environment's own shell config, before relying on it.
4. `_validate_prompt_size()`'s own logic is **unchanged** — still validates
   `shlex.quote()`'d prompt length. The reviewer explicitly called this out
   as still correct, just now modeling a different (and much less
   frequently binding) ceiling: once the script lives in a file, bash's own
   quote-removal recovers the raw prompt before it forks+execs the real
   engine binary, so the argv element that actually reaches *that* exec()
   is the raw (unescaped) prompt, not the shell-escaped form — but
   `shlex.quote()`'d length is always ≥ raw length, so the existing check
   remains a sound (if occasionally conservative) proxy for it. Only
   `_ARG_SCRIPT_OVERHEAD_BYTES` changed, from `4096` (sized to cover an
   entire generated script sharing one argv element with the prompt) down
   to `1024` (now just a small safety margin, since the prompt is the
   engine's own isolated argv element with nothing else of this module's
   concatenated onto it).
5. The script file lives inside `rundir` and is cleaned up by the exact
   same `try/finally`/`shutil.rmtree(rundir, ...)` round 1 already
   established — no new leak surface introduced.

**Regression tests added** (`tests/test_teams_headless.py`,
`RealTmuxHeadlessTests`, real tmux, no sudo): per the reviewer's explicit
instruction, both use **ordinary plain text**, not quote-heavy content (a
quote-heavy test passes even while this exact bug is fully live, which is
exactly how it survived round 1's own quote-heavy-only regression tests) —
`test_ordinary_20kb_plain_text_prompt_actually_runs_arg_mode` (a genuine
~20KB "The quick brown fox..." prompt, the reviewer's own repro size,
round-tripped byte-for-byte through a real engine's argv and back) and
`test_prompt_just_over_tmuxs_old_16kb_limit_still_runs` (17,000 bytes,
directly at the old failure boundary). Both spawn a real `python3` helper
"engine" through real tmux and assert `ok=True`/`exit_code=0` and the
prompt round-tripping exactly through `sys.argv[1]` on the far side — not
just that validation passed.

**Verified live, independent of the test suite**: re-ran the reviewer's own
binary-search shape directly against real tmux — inline `bash -lc <script>`
fails past ~16.3KB exactly as reported; the same script content written to
a file and run via `bash -l <path>` succeeds at every size tried up to
130,000 bytes.

## Round 3

Round 3 was the first round to reach the actual **review pass** (spec
coverage / correctness / security / simplicity read of the diff) rather
than being deferred again by a new testing-pass defect — round 2's fix was
independently re-verified as genuinely closed (including a fresh live
`claude` resume round-trip through the current `bash -l <file>` code path)
before the review pass began. It found one must-fix (Finding 1) plus two
should-fix items (Findings 2–3) and one non-blocking consolidation note (Q2
in `docs/test-review.md`), all addressed in this pass.

### Finding 1 (must-fix, blocking) — translators crash on syntactically-valid-JSON-but-unexpected-shape events
**Root cause**: `_translate_claude()`/`_translate_codex()` assume specific
field shapes (e.g. `native["message"]` is itself a dict) with no defensive
guard. A native event that's valid JSON but doesn't match that assumed
shape — `{"type": "assistant", "message": "not an object"}`,
`{"type": "item.completed", "item": "not an object"}`, and similar — raises
`AttributeError`/`TypeError` from inside the translator, with no
surrounding `try/except` in `_Tailer._handle_line()` to catch it (unlike
the adjacent `json.loads()` failure, which *is* handled). This is a live,
externally-versioned risk, not a hypothetical one: the translators'
own comments already record one real instance of the actual CLI output
diverging from documented shape during this sub-spec's own Tier 3
verification (`rate_limit_event`, `thinking` content blocks — both
*unrecognized-but-still-dict-shaped*, which the existing fallbacks already
handled; this defect is about the shape assumption breaking entirely, not
just an unrecognized value).

**Fix — a boundary guard, not per-site defensiveness.** Adding
`isinstance()`/`.get()` chains at each of the three flagged call sites
would fix today's three shapes and leave the class open for the next one.
Instead, a new `_translate_safely(translate_fn, native)` wraps the
translator call itself: returns `(events, None)` on success or `([],
error_message)` if the translator raises *any* exception. `_Tailer.
_handle_line()` now calls translators only through this wrapper, and on a
non-`None` error appends exactly one `kind="error"` envelope (mirroring the
existing `json.loads()`-failure handling) before moving on to the next
line — the run continues, `agent_run()` never raises. The raw translator
functions themselves are deliberately left free to raise on a bad shape
(no per-branch guards added) — the boundary wrapper is where the guarantee
lives, not scattered defensive checks that would need to be re-added for
every future shape surprise.

**Regression tests added** (`tests/test_teams_headless.py`, new
`TranslatorShapeSafetyTests` class, plus one real-tmux test in
`RealTmuxHeadlessTests`): pure tests confirming the raw translators still
raise on the reviewer's exact repro shapes (proving the wrapper is
load-bearing, not a no-op) and that `_translate_safely()` catches each
cleanly; `_Tailer`-level tests for both formats confirming exactly one
error event is appended and translation of subsequent lines continues
(`final_text()` still reflects the later, well-formed `result`/
`agent_message` event); and
`test_shape_crash_line_through_the_real_agent_run_path_does_not_raise` —
the reviewer's own repro shape, but through the *full* real-tmux
`agent_run()` path with a synthetic engine emitting a bad-shape line among
otherwise-normal ones, asserting `agent_run()` itself doesn't raise and the
run completes with `ok=True`.

### Finding 3 (should-fix, fixed) — `run.sh`/prompt file readability relied on ambient umask
`run.sh` (every mode, since round 2 made it universal) and the `stdin`/
`file`-mode prompt file (since round 1) were written with `open(path,
"w")`/`open(path, "wb")` and no explicit `chmod`, so `RUN_USER`'s ability
to *read* them depended entirely on `SVC_USER`'s ambient process umask
leaving the world-read bit set — confirmed live that a `0o077` umask
produces mode `0o600`, unreadable by anyone but the owner, silently
breaking every headless run under such a umask (degrading gracefully into
the existing "headless session failed to start" path, per the reviewer's
own testing — not a crash, but an unhelpful, umask-blind error message).
Fixed with an explicit `os.chmod(prompt_path, 0o644)` /
`os.chmod(script_path, 0o644)` right after each file is written, mirroring
`rundir`'s own explicit `os.chmod(rundir, 0o711)` — same reasoning, same
pattern, no new privilege. **Regression test added**:
`test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask` sets
`os.umask(0o077)` for the whole test process (real tmux, a `file`-mode
engine so both files get written) and asserts both end up world-readable
regardless, plus that the run itself still succeeds.

### Finding 2 (should-fix, fixed) — stale documentation of the `arg`-mode cap's rationale
`docs/ADDING_AN_ENGINE.md` and `config/switchboard.env.example` both still
described the pre-round-2 mechanism ("the whole generated shell script
becomes one argv element to `bash -lc`"), which stopped being true once
round 2 moved the script to a file. Both updated to describe the current,
narrower mechanism (the prompt is its own argv element on the *engine's*
own exec, once bash's quote-removal recovers it from the script file) —
`app/teams.py`'s own internal comments already said this correctly since
round 2; only the two operator-facing docs were stale. Also updated this
file's own "Deviations from spec" section (below), which previously
described the round-2 architecture change as folded into "none
substantive" — the reviewer correctly flagged that framing as underselling
a real, deliberate deviation from `docs/spec.md` §2's literal `bash -lc
<script>` invocation shape.

### Q2 (non-blocking, addressed) — trim incident-narrative prose in source comments
Three rounds of fixes had left `_validate_prompt_size()`'s docstring,
`_MAX_ARG_STRLEN`'s/`_ARG_SCRIPT_OVERHEAD_BYTES`'s comments,
`_build_script()`'s docstring, and part of `agent_run()`'s own inline
comments narrating "round 1 did X because Y, round 2 changed Z because W,
see docs/test-review.md Defect N" — useful archaeology, but duplicating
what this file's own "Post-review fixes" sections already record
permanently and in more detail. Trimmed all of the above to state the
*current* constraint/rationale concisely (e.g. `_MAX_ARG_STRLEN`'s comment
now just says what it models and that it's per-argv-element, not a
retelling of how that came to be); the one remaining round/finding
citation left in source (`_translate_claude()`'s `user`-branch comment) is
a single-line pointer, not narrative, and was left as a cheap way to locate
the corresponding `docs/implementation.md` writeup if a future reader needs
more context.

## Deviations from spec
- **`_build_script()`'s generated text reaches `bash` via a file, not
  inline.** `docs/spec.md` §2's literal invocation shape is
  `TMUX + ["new-session", "-d", "-s", session, "-c", workdir, "bash", "-lc", script]`
  — the round-2 fix for Defect 2 (see "Post-review fixes" below) changed
  this to write `script` to `RUNDIR/run.sh` and invoke
  `["bash", "-l", script_path]` instead, because the literal spec shape hits
  tmux's own internal command-passing-protocol limit (~16KB, unrelated to
  any kernel constant) well within the spec's own documented "safe"
  `TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES` default. `_build_script()`'s own
  *output* (the script text itself — redirects, backgrounding, `$!`
  capture, `wait`, `echo $?`) is unchanged from the spec's shape; only how
  that text reaches `bash` changed. Confirmed `bash -l <file>` sources the
  same login-shell startup files as `bash -lc` did (RUN_USER's own PATH
  extensions stay findable) before relying on it. See "Post-review fixes
  → Round 2" for the full defect/fix writeup.
- Otherwise none substantive. Implemented per `docs/spec.md`'s "Proposed
  approach" sections 1–6 as written, including the literal `agent_run()`
  signature, the exact validation order, and the exact completion-ordering/
  cancellation-escalation rules. The envelope translation table itself
  (§4.1) is a from-scratch, spec-faithful design rather than a literal
  carry-over — a filling-in of an intentionally-unspecified implementation
  detail, not a deviation from anything the spec actually pinned down.

## Known limitations
- **One isolated, non-reproduced test-suite flake observed during the
  round-2 fix pass**: a single `python3 -m unittest discover -s tests` run
  (out of ~10 full-suite runs across both fix passes) failed one of the two
  new Defect 2 regression tests with `ok=False`/`error=None` (the shape
  `_finish()` produces for the `cancelled=True` "missing rc"/signal-exit
  paths, not a logic failure in the new code itself). Immediately preceded
  by several minutes of manual, ad-hoc `tmux new-session`/binary-search
  probing directly against the same tmux server from this same shell
  session while diagnosing Defect 2 — the most likely explanation is
  transient tmux-server/OS scheduling contention from that manual activity,
  not a defect in the fix. Not reproduced across 10 isolated repeats of the
  two new tests alone, nor across 9 further full-suite runs (6 before this
  note was written, 3 more immediately after) via both `unittest` and
  `pytest`. Flagged here rather than silently omitted; if this resurfaces
  under real concurrent load (relevant once 6c/6d run multiple `agent_run()`
  calls at once), the missing-rc/signal-exit classification path is exactly
  where to look first.
- **`aider.engine`'s `HEADLESS_*` keys are unverified** — `aider` is not
  installed in this environment (`sudo -u dev which aider` → not found).
  `HEADLESS_CMD=aider --message-file {prompt_file} --yes-always` is believed
  correct per aider's own documented CLI flags as of 2026-08-13, but has not
  been run end-to-end. Flagged explicitly in the `.engine` file's own
  comment, `docs/ADDING_AN_ENGINE.md`, and here — not silently marked done.
- **`codex.engine`'s `HEADLESS_*` keys are partially verified.** `codex-cli
  0.147.0` is installed and was run for real, headless, through the actual
  generated shell wrapper (`codex exec --json --skip-git-repo-check
  "<prompt>"`) — this confirmed the NDJSON event shapes for
  `thread.started` (the session-id-bearing event), `turn.started`, `error`,
  and `turn.failed`, and confirmed a real nonzero exit code (1) for a
  fast-failing engine (docs/spec.md's "engine binary not on PATH, or not
  logged in" edge case). The account `codex` ran under in this environment
  was **not logged in** (`401 Unauthorized` from `api.openai.com`), so a
  *successful* (exit 0) turn, the `item.completed`/`agent_message` event
  shape, and the `resume <SESSION_ID>` subcommand-swap syntax specifically
  remain unconfirmed against a real successful run — `tests/fixtures/
  headless/codex_stream_success.jsonl` is synthesized (see that directory's
  README for exactly how), not a real capture.
- **`claude.engine`'s `HEADLESS_*` keys are fully verified**, including
  resume: `claude -p {resume} --output-format stream-json --verbose` run
  live twice (first turn: "remember the number 42, reply ok"; second turn,
  `--resume <session_id>`: "what number did I ask you to remember" →
  answered "42", confirming the turn was genuinely continued, not just that
  the flag was accepted) — real exit 0 both times, real `session_id`
  extracted, real `SIGTERM` → exit 143 confirmed live via a hand-built
  version of the same background/`wait`/`echo $?` shell shape `_build_script()`
  generates.
- **6a intentionally ships no HTTP route, no UI, no lead loop, no grounding**
  — all deferred per spec's own non-goals; `app/teams.py` is not imported by
  `app.py` at all in this cycle.

## Verification status (per docs/spec.md's three-tier test plan)
| Tier | What | Result |
|---|---|---|
| 1 — pure unit | Engine parsing, reserved prefix, argv/script builders (incl. Defect 1's `shlex.quote()`-aware byte cap and Defect 2's `_ARG_SCRIPT_OVERHEAD_BYTES` re-derivation), envelope translation (incl. Finding A's `user`-branch symmetry), the `_translate_safely()` boundary guard (Finding 1), `_Tailer` malformed/truncation/shape-crash handling, validation-only `agent_run()` paths, OSError-from-spawn defense-in-depth | 67 tests, all passing, `subprocess.run`/`tmux_has` proven never called on any validation failure |
| 2 — real tmux, test-authored process, no sudo | Success stream end-to-end, stdin delivery, `SIGTERM`-only stop, `SIGTERM`-ignored → `SIGKILL` escalation, external `SIGTERM` classification, forced-kill missing-rc classification, resume-unsupported pre-flight, active_engine() session-name collision, permission-denied state file, stale-run sweep (removes/leaves-alone/kills-live-session), ordinary 20KB/17KB plain-text `arg`-mode prompts actually running (Defect 2 regression), a shape-crash line surviving the full real `agent_run()` path (Finding 1 regression), `run.sh`/prompt-file world-readability under a strict umask (Finding 3 regression) | 16 tests, all passing, using real `tmux` (this sandbox's own user, no `RUN_USER`/sudo needed) |
| 3 — real CLI (manual) | `claude`: full live run + resume + real SIGTERM/143, via direct shell testing, the CLI entry point, and (round 3) the reviewer's own independent re-verification of a fresh resume round-trip through the current code path. `codex`: real process/NDJSON/exit-code plumbing confirmed; not logged in, so a successful turn is unconfirmed. `aider`: not installed, entirely unverified. Defect 1's exact repro (30,000-byte all-quotes prompt) re-run live post-fix: clean `ValueError`, zero leaked directories. Defect 2's exact repro (real tmux, 15,000–130,000-byte commands, inline vs. file-based) re-run live post-fix: inline still fails past ~16.3KB (that path is no longer used); file-based succeeds at every size. Finding 1's exact repro (four wrong-shape native events) re-run live post-fix: all four now return `(events, error_message)` cleanly via `_translate_safely()` instead of raising | See "Known limitations" above |

`tests/test_teams_headless.py`: **83 tests** (67 Tier 1 + 16 Tier 2), up
from 60 at the initial build — 12 added in the round-1 fix pass, 2 added in
the round-2 fix pass, 9 added in the round-3 fix pass (5 pure translator-
shape-safety tests, 2 `_Tailer`-level shape-crash tests, 1 real-`agent_run()`
shape-crash test, 1 strict-umask readability test).

Full existing suite: **372/372 tests pass**, verified both ways —
`python3 -m unittest discover -s tests` (9 consecutive clean runs across
all three fix passes; one isolated flake during round 2's diagnosis,
documented below, not reproduced since) and the reviewer's own
`/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q`
(3 consecutive clean runs this pass, `372 passed in ~34s` each) — up from
289 pre-existing + 83 in `tests/test_teams_headless.py`. No pre-existing
test was modified in any pass.

## How to verify locally
```bash
# New headless-invocation test file only
python3 -m unittest tests.test_teams_headless -v

# Full existing suite (nothing pre-existing touched, but a good sanity pass)
python3 -m unittest discover -s tests -v

# Same, via pytest (the exact command the reviewer's testing pass used)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# CLI, against the real engines.d + a scratch project (no server, no UI):
export TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x
export ENGINES_DIR=$(pwd)/engines.d PROJECTS_DIR=/tmp/scratch-projects
mkdir -p /tmp/scratch-projects/demo
python3 app/teams.py list-engines
python3 app/teams.py run claude /tmp/scratch-projects/demo --prompt "reply with one word"
# (requires `claude` installed and logged in as RUN_USER; substitute codex/aider similarly)
```
All commands above were run during implementation and again during all
three post-review fix passes; the full suite passes 372/372 both ways
(`unittest` and `pytest`), and the CLI was run live end-to-end against the
real `claude` CLI (list-engines, a first turn, and a `--session-id`-resumed
second turn that only makes sense with turn-1 context) in every pass,
including once more by the reviewer independently in round 3.
