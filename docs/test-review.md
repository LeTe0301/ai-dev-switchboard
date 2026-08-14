# Test & Review: Roster + lead loop, all three adapter tiers (sub-spec 6c)

## Scope
Covers `docs/spec.md`'s full acceptance-criteria list (roster/tier detection,
all three lead adapters, the four-tool loop, shape/business-rule validation,
persistence/crash-recovery, `ask_user`/`team-resolve`, and the two
appended "Correction:" sections — schema-placeholder split and the
repeated-delegation mitigation). Testing pass ran the existing suite plus
`tests/test_teams_lead.py`, and independently re-derived/re-ran the four
specific things the requester flagged: the `_build_headless_argv()`
substitution-ordering defect, the real Claude/Codex schema-flag correction,
roster-build-time error surfacing, and the honesty of the repeated-delegation
mitigation claim.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `roster()` lists every headless-eligible engine + Ollama, correct tier, `HEADLESS_LEAD_FORMAT` override both directions | automated | pass | `RosterTests`, `LeadTierForEngineTests`; independently re-ran `python3 app/teams.py roster` against this repo's real `engines.d` — `claude`/`codex` tier 2 with `schema_flag_error: null`, `aider` tier 3 |
| 2 | Full `delegate → fact_check → delegate → finish` cycle, real CLI, real project dir, real reachable tier-1 Ollama | manual, reproduced live | pass (functionality) / **doc gap** (see Findings #2) | Independently ran this exact 4-step sequence against the live `qwen3:8b` endpoint with a real `claude` teammate — real `run.json` below shows `delegate → fact_check → delegate → finish`, `status: finished`, 4 rounds. `docs/implementation.md` does **not** document this literal sequence (only documents `fact_check→finish` and `delegate→finish` separately) |
| 3 | All three tiers lead the same task (tier 1 real Ollama, tier 2 real `claude`/`codex` login, tier 3 stand-in + `aider` UNVERIFIED) | manual + automated | pass | Tier 1: reproduced live (see #2). Tier 2: reproduced live against real logged-in `claude` (see Findings context / "Fix 1" reproduction below), `codex` genuinely unauthenticated (independently confirmed: real 401 from `api.openai.com`), verified via `RealTmuxSchemaTests` stand-in instead, disclosed honestly. Tier 3: `RealTmuxTier3StandInTests` (real tmux, shell-script stand-ins), `aider.engine` UNVERIFIED, disclosed |
| 4 | Tier-3 malformed JSON retried within budget, then escalates via `ask_user` with raw text included | automated, real tmux | pass | `RealTmuxTier3StandInTests.test_no_fence_fixture_retries_then_escalates`, `test_malformed_fence_fixture_retries_then_escalates`; `MalformedRetryEscalationTests` (pure) |
| 5 | `ask_user` blocks + writes exact `inbox.json` shape; `team-resolve` resumes in a **separate process** | automated, real subprocess | pass | `WriteInboxTests`; `ResolveInSeparateProcessTests.test_team_start_blocks_then_team_resolve_in_a_separate_process_resumes_to_finished` (two genuinely separate `subprocess.run()` invocations) |
| 6 | `TEAM_MAX_ROUNDS` forces `ask_user` escalation, exactly N rounds, no `inbox.json` | automated | pass | `MaxRoundsEscalationTests.test_never_finishing_lead_runs_exactly_max_rounds_then_escalates` |
| 7 | Ollama unreachable → clear, actionable, non-traceback error after exhausting transport-retry budget | automated | pass | `Tier1TransportRetryTests` (monkeypatched `urlopen` raising N times / always) |
| 8 | Tier-1 no-`tool_calls` (prose) reply falls back to tier-3 parser, both recovered and correctly-malformed cases | automated | pass | `ParseTier1ActionTests.test_no_tool_calls_falls_back_to_tier3_parser_and_recovers`, `test_no_tool_calls_and_no_fence_falls_through_to_malformed` |
| 9 | `delegate` to a non-member agent rejected without consuming the malformed budget, ordinary round continues | automated | pass | `BusinessRuleRejectionTests.test_agent_not_on_team_ordinary_round_not_malformed` |
| 10 | `finish` with zero prior actions rejected, including resumed-run round>1/`action_count==0` case | automated | pass | `BusinessRuleRejectionTests.test_premature_finish_ordinary_round_not_malformed`, `test_resumed_run_round_gt_1_action_count_still_0_is_still_premature` |
| 11 | Delegation result > `TEAM_DELEGATE_RESULT_MAX_CHARS` truncated with explicit non-silent marker | automated | pass | `PromptBoundingTests.test_oversize_delegate_result_gets_explicit_truncation_marker` |
| 12 | Assembled per-round prompt never exceeds `TEAM_LEAD_PROMPT_MAX_CHARS`, pathological every-sub-budget-maxed case | automated | pass | `PromptBoundingTests.test_every_sub_budget_maxed_simultaneously_still_respects_final_cap` |
| 13 | `_system_framing()` contains both required `fact_check` mitigation clauses, every tier | automated | pass | `SystemFramingTests` |
| 14 | Crashed/killed `team-start`, resumed via `team-resume` in a fresh process, reconstructs identical next-round prompt, continues to completion | automated, real subprocess | pass | `ResumeAfterMidDelegateCrashTests.test_team_resume_after_hand_constructed_in_progress_crash_reaches_finished` (separate `team-resume` subprocess); `PersistRoundTripPromptReconstructionTests` (prompt-identity check) |
| 15 | Round left `"in_progress"` by mid-delegate crash never treated as successful on resume | automated | pass | `InProgressCrashRecoveryPureTests.test_in_progress_delegate_recorded_as_unresolved_not_successful` |
| 16 | `agent_run()`'s existing (no-`schema`) behavior byte-for-byte unchanged | automated | pass | `git diff -- tests/test_teams_headless.py` is empty; full file still passes; `AgentRunSchemaNoSpawnTests.test_no_schema_argument_unaffected`, `ValidatePromptSizeSchemaInteractionTests.test_no_schema_byte_for_byte_unchanged_behavior` |
| 17 | Full suite green, several consecutive runs; `app/app.py` diff limited to the two new `Engine` fields | automated | pass | `uv run --with pytest python -m pytest tests/ -q` → **581 passed**, run 2x independently this session (in addition to the developer's own 4 runs); `git diff --stat -- app/app.py` → 23 insertions / 3 deletions, confirmed |

## Regression check
Full existing suite run twice this session:
`/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` →
**581 passed** both times, no flake observed. `tests/test_teams_headless.py`
and `tests/test_teams_grounding.py` diffs are both empty (`git diff --stat`),
so the pre-existing 6a/6b/6b.1 suites ran completely unmodified as part of
this 581. `python3 -m py_compile app/app.py app/teams.py
tests/test_teams_lead.py` — clean.

## Requester's four specific checks

**1. `_build_headless_argv()` substitution-ordering defect — CONFIRMED, real bug, agree with proposed fix.**
Independently reproduced both the schema-corruption case (synthesized
`HEADLESS_PROMPT=file` + `HEADLESS_SCHEMA_FLAG` engine) and a second case the
requester didn't fully spell out (a `session_id`/`{resume}` value containing
a literal `{schema}` token gets rescanned and corrupted by the later
`{schema}` `str.replace()` pass, splitting the resume argv and splicing the
schema JSON into it). Confirmed at the code's **current** location
(`app/teams.py:349-357`, `_build_headless_argv()`, part of *this* diff — the
`{schema}` substitution step is new in 6c, not inherited unchanged from 6a).
Repro:
```python
e = Engine(..., headless_cmd="someengine {schema} --prompt-file {prompt_file}",
           headless_prompt="file", headless_schema_flag="--json-schema {schema}")
schema = {"type": "object", "properties": {"p": {"type": "string", "description": "see {prompt_file}"}}}
argv = _build_headless_argv(e, prompt="ignored", session_id=None,
                             prompt_path="/run/abc/prompt.txt", schema=schema, schema_path="...")
# argv[2] == '{"type": "object", ..., "description": "see /run/abc/prompt.txt"}'
```
Agree with the requester's read on all three points:
- **Reordering is not sufficient.** With 3 sequential `str.replace()` passes
  over one shared string, whichever token is substituted *first* is
  vulnerable to *every* later pass, and only the *last*-substituted token is
  ever safe. Since both `{resume}` (semi-trusted `session_id`, sourced from
  an engine CLI's own JSON output) and `{schema}` (a full JSON Schema with
  arbitrary string values, e.g. `description` fields) carry content that
  could plausibly contain a literal `{schema}`/`{prompt_file}`/`{resume}`
  substring, there is no ordering of the three passes that protects both —
  moving one to the front only exposes the other. Verified this directly: a
  `{resume}`-first, `{schema}`-last ordering (today's code) leaves `{resume}`
  vulnerable to `{schema}`'s rescan; I confirmed the reverse arrangement
  would just move the vulnerability, not remove it.
- **A single-pass, simultaneous substitution (one regex pass with a mapping,
  so already-substituted text is never rescanned) is the structurally
  correct fix**, not a reordering.
- **Severity: correctness bug, not a privilege/containment break.** Argv
  elements stay individually quoted going into `subprocess`/tmux — this
  isn't shell injection — but it silently corrupts a schema and/or an argv's
  shape with no exception raised, which is exactly the "never silent"
  discipline this codebase holds itself to everywhere else (see the Goals
  section's own "malformed input degrades to a defined, bounded outcome,
  never an exception" and `_build_headless_argv()`'s own docstring, which
  explicitly claims immunity from "a HEADLESS_SCHEMA_FLAG carrying a literal
  JSON Schema (full of `{`/`}`) can't break this" — a claim this
  implementation does not actually meet).
- **Reachability**: confirmed config-only — no shipped engine combines
  `HEADLESS_PROMPT=file` with `HEADLESS_SCHEMA_FLAG` (`aider` is file-mode,
  no schema flag; `claude`/`codex` have schema flags, both arg-mode) — but
  `engines.d` is explicitly user-extensible (`docs/ADDING_AN_ENGINE.md`
  invites exactly this), and no test in the suite exercises the reachable
  combination (`tests/test_teams_headless.py`'s own
  `test_build_headless_argv_*` tests never combine `schema`+`prompt_path`+
  `file` mode; `RealTmuxSchemaTests` only uses `arg`-mode prompts with a
  schema that has no literal placeholder-shaped substrings in it).
  This is a **must-fix**.

**2. Schema correction, real CLIs — CONFIRMED CORRECT, independently reproduced.**
`claude --help` / `codex exec --help`, run directly in this environment,
confirm the developer's reading exactly: `claude --json-schema <schema>`'s
own example is inline JSON text; `codex --output-schema <FILE>` is
documented as "Path to a JSON Schema file". Independently reproduced the
real tier-2 `claude` run end to end (own scratch project, own `team-start`
invocation, not copy-pasted from `docs/implementation.md`):
`status: finished`, `fact_check(found=True) → finish`, 2 rounds, ~11s wall
time, zero malformed retries — matches the developer's own report in shape
and outcome. Independently confirmed `codex` is genuinely unauthenticated in
this environment (`codex exec --json --skip-git-repo-check "say hi"` → real
`401 Unauthorized` from `wss://api.openai.com`), so the disclosed limitation
in `docs/implementation.md`/`docs/ADDING_AN_ENGINE.md` is honest, not
papered over, and confirmed no test in `tests/test_teams_lead.py` fakes a
passing `codex` run (`grep -n codex tests/test_teams_lead.py` finds only a
comment).

**3. Roster-build-time error surfacing — CONFIRMED, both paths fire.**
`roster()`'s `schema_flag_error` field (`RosterTests.
test_schema_flag_config_error_surfaced_at_roster_build_time`) and
`_cli_team_start()`'s early rejection (`CliTeamStartSchemaConfigErrorTests.
test_team_start_rejects_misconfigured_tier2_lead_before_running`, asserting
`--forbid-spawn` and that no `leads/` state directory is ever created) both
verified directly, plus `agent_run()`'s own before-anything-is-spawned raise
(`SchemaFlagConfigErrorAgentRunTests`). The "declares BOTH placeholders"
case is also covered (`SchemaPlaceholderKindTests.
test_both_placeholders_prefers_file`) and resolves the way the docstring
says it should (prefers `{schema_file}`).

**4. Repeated-delegation mitigation honesty — CONFIRMED, framing is honest and not overclaimed.**
`docs/implementation.md`'s "Fix 2" and "Known limitations" sections state
"did not recur in 3/3 real runs", explicitly contrasted with "fixed for
good"/"structurally impossible", and flag it as "an ongoing, monitored
judgment-quality question rather than a closed ticket" — this framing is
correct and I would flag it if it were softened. Confirmed no test asserts
non-recurrence as a guarantee: `DelegationHistoryMitigationTests` only
checks the mitigation clause text is present in `_system_framing()`'s
output and that `_append_history()`'s summaries state agent/task/
SUCCEEDED-or-FAILED explicitly (`DelegateBookkeepingTests` at lines 935-937,
961-978) — nothing pins live model behavior.

## Constraints re-checked

- **`TMUX`-only privilege path**: `git diff -- app/teams.py | grep -iE
  "sudo|tmux|subprocess\.(run|Popen)|setuid"` on added lines returns only a
  docstring comment reference — the `delegate` branch of `team_step()`
  reuses the existing `agent_run()` unmodified; no new sudoers/privileged
  surface added by 6c.
- **Grounding read-only**: `tests/test_teams_grounding.py`'s runtime
  monkeypatch (`test_no_write_open_or_mutating_call_across_full_public_
  surface`) and static AST scan (`ast.parse()` of `app/teams.py`'s
  grounding-section function defs) both confirmed present, and the file's
  diff is empty (`git diff --stat -- tests/test_teams_grounding.py`) — 6c's
  `team_step()` only ever calls the existing `load_grounding()`/
  `fact_check()`, adds no new grounding-touching code.
- **No path-based `realpath()` fallback**: confirmed unchanged
  (`app/teams.py:1195`, "no path-based realpath() fallback, which would
  silently reopen the exact TOCTOU hole") — this section of the file isn't
  touched by the 6c diff.
- **Deploy stays manual-click-only**: confirmed no deploy-related code
  touched by this diff (`git diff -- app/teams.py | grep -i deploy` empty);
  unrelated to 6c's scope.

## Spec coverage

All 17 acceptance criteria in `docs/spec.md` have a corresponding
implementation and test (see Test cases table above). One gap: **AC #2**
("a full `delegate → fact_check → delegate → finish` cycle... documented in
`docs/implementation.md` with the actual command and output") is not
actually documented that way in the current `docs/implementation.md` — the
file documents `fact_check→finish` (round 1's original tier-1 run) and
`delegate→finish` (round 2's Fix 2 reverification) as two separate real
runs, never the literal 4-step chained sequence the AC names. I proved the
underlying capability works (see Test case #2, a real run I performed
myself), so this is a documentation gap, not a functional one — flagged in
Findings below.

## Findings (most severe first)

**Both findings below are from round 2's review and are RESOLVED as of round
3 — see "Round 3 re-review" below for verification. Kept here verbatim for
the record, not because either is still open.**

### 1. [RESOLVED round 3] `_build_headless_argv()`'s sequential `str.replace()` passes let a later substitution rescan and corrupt an earlier one — must-fix
- File: `app/teams.py:349-357` (`_build_headless_argv()`)
- Issue: `{resume}` is substituted first (349), then `{schema}` (350-354),
  then `{prompt_file}` (355-356), each via its own `str.replace()` call over
  the same growing `cmd` string. Because these are sequential, not
  simultaneous, text inserted by an earlier pass is rescanned by every later
  pass. A `{schema}` fragment (the schema's own JSON text, which can contain
  arbitrary strings in `description`/etc. fields) containing a literal
  `{prompt_file}` substring gets that substring silently rewritten to the
  real prompt-file path by the later `{prompt_file}` pass. A `{resume}`
  fragment (built from a `session_id`, sourced from an engine CLI's own
  output — semi-trusted, not developer-controlled) containing a literal
  `{schema}` substring gets silently rewritten and its own argv shape
  corrupted by the later `{schema}` pass. Both independently reproduced (see
  "Requester's four specific checks" #1 above).
- Failure scenario: an operator adds an engine (per `docs/ADDING_AN_ENGINE.md`'s
  own invitation to extend `engines.d`) with `HEADLESS_PROMPT=file` and a
  `HEADLESS_SCHEMA_FLAG` — the only reachable combination given the three
  shipped engines — and any lead-loop schema whose JSON text happens to
  contain the literal substring `{prompt_file}` (plausible for a schema with
  a `description` field describing file-related tool parameters) gets that
  substring silently replaced with the real (usually harmless, but
  internal) rundir prompt-file path before being handed to the engine CLI —
  no exception, no log entry, just a corrupted schema. The `{resume}` case
  is lower-likelihood (session IDs are usually opaque tokens) but not
  developer-controlled, so it shouldn't be dismissed either.
- Recommended direction (per requester, for the developer to implement, not
  fixed by me): replace the three sequential `str.replace()` calls with one
  single-pass simultaneous substitution (e.g. one `re.sub()` with a
  placeholder→value mapping) so no substituted text is ever rescanned by a
  later pass, regardless of ordering.

### 2. [RESOLVED round 3] `docs/implementation.md` doesn't document the literal AC #2 real run — should-fix
- File: `docs/implementation.md`, "Verification status" table
- Issue: AC #2 in `docs/spec.md` asks for "a full `delegate → fact_check →
  delegate → finish` cycle... from the CLI against a real project directory
  and a real reachable Ollama model... documented in `docs/implementation.md`
  with the actual command and output". The current doc shows two separate
  real tier-1 runs (`fact_check → finish`, and `delegate → finish` ×3) but
  never the literal chained 4-tool sequence.
- Failure scenario: none functionally — I independently ran this exact
  sequence live against the real endpoint and it completed correctly in one
  clean run (`status: finished`, round 4, `delegate → fact_check → delegate
  → finish`), so the capability is real. This is purely a documentation
  completeness gap against the literal acceptance-criterion wording; easy
  for the developer to close by appending one real run's command+output.

## Follow-ups (non-blocking)
- `ask_user`'s 2–4-option count is not enforced by `_validate_lead_action()`
  (disclosed deviation #5, unchanged from round 1) — no acceptance
  criterion covers it; low-confidence reading, fine to leave as-is per the
  developer's own note.
- Consider whether `_build_headless_argv()`'s fix (once made) should also
  get a dedicated regression test combining `HEADLESS_PROMPT=file` +
  `HEADLESS_SCHEMA_FLAG` with a schema/session_id deliberately containing
  literal `{prompt_file}`/`{schema}`/`{resume}` substrings — the exact
  reachable-but-untested combination that let this defect through.

## Round 3 re-review

Focused re-review only, per the coordinator's instruction — did not redo the
17 acceptance criteria or the four cross-cutting constraints, since this
round's diff (`app/teams.py`, `tests/test_teams_lead.py`,
`docs/implementation.md`) touches only `_build_headless_argv()`'s
substitution machinery, its tests, and documentation; it does not touch
`team_step()`/the tier adapters/persistence/grounding/the TMUX privilege
path, so none of those were plausibly affected and weren't re-run wholesale.

### Diff reviewed
`_HEADLESS_CMD_TOKENS` / `_HEADLESS_CMD_TOKEN_RE` / `_substitute_headless_
tokens(cmd, mapping)` (new, `app/teams.py:329-381`) replace the three
sequential `str.replace()` calls in `_build_headless_argv()`
(`app/teams.py:384-425`) with one `re.sub()` pass over the mapping built
from all three resolved values up front. New tests:
`BuildHeadlessArgvSinglePassSubstitutionTests` (4) and
`SubstituteHeadlessTokensPureTests` (3), file now 112 tests (was 105).
`docs/implementation.md` gained "Round 3, Finding #1"/"Finding #2" sections.

### 1. Is the single-pass primitive correct in general, not just on the two known repros?

Independently exercised `_substitute_headless_tokens()` directly (not
through `_build_headless_argv()`) with cases beyond the two original
repros:

```python
_substitute_headless_tokens("foo {totally_unknown} bar {resume}", {"{resume}": "R"})
# -> 'foo {totally_unknown} bar R'   -- an unrecognized token (not one of
#    the 3 known ones) is simply never matched by the compiled alternation,
#    left untouched -- same as the pre-existing (6a) behavior; not a new
#    decision, not documented as its own bullet, but not a regression either
#    (str.replace() calls before this fix behaved identically for any
#    token they weren't specifically looking for).
_substitute_headless_tokens("cmd {resume} {schema}",
    {"{resume}": "has {schema} inside", "{schema}": "SCHEMAVAL"})
# -> 'cmd has {schema} inside SCHEMAVAL'  -- a mapping VALUE for one token
#    that is itself shaped like another token is NOT rescanned. This is the
#    general-case version of both original repros, confirmed directly at
#    the primitive level, independent of the schema/session_id domain.
_substitute_headless_tokens("{resume} and {resume} again", {"{resume}": "R"})
# -> 'R and R again'  -- repeated occurrences of the same token both
#    substituted (re.sub()'s default "all occurrences", matching the old
#    str.replace()'s own default -- not a behavior change).
_substitute_headless_tokens("x{schema}y", {"{schema}": ""})
# -> 'xy'  -- empty-string mapping value handled the same as before.
```
All confirmed correct, matching both the docstring's claims and the
pre-existing (unchanged) behavior for the cases this fix didn't need to
change.

**One real, narrow gap found, independent of both original repros — a
`None` mapping value is silently swallowed rather than raising.** Python's
`re.sub()` treats a replacement function returning `None` the same as
returning `""` (confirmed directly: `re.sub("a", lambda m: None, "xay")` →
`'xy'`, no exception — this is CPython's actual, if under-documented,
behavior, not a guess). Constructed the one path that can produce this:
`_build_headless_argv(engine, prompt, session_id, prompt_path=None)` on a
`headless_prompt == "file"` engine — `prompt_path`'s default parameter
value is `None`, so `mapping["{prompt_file}"] = None`, and the token
silently vanishes instead of raising:
```python
_build_headless_argv(file_mode_engine, "hello", None, prompt_path=None)
# -> ['eng', '--file']   -- {prompt_file} silently disappeared, no path,
#    no error -- `eng --file` would then misparse whatever argv element
#    follows as --file's own value.
```
The **old** (pre-fix) code would have raised `TypeError: replace() argument
2 must be str, not None` immediately at this exact call — loud and
immediate. The new code is silent here. **Not reachable from the only real
caller** (`agent_run()` always computes a real `prompt_path` string before
calling `_build_headless_argv()`, `app/teams.py:1049`-ish, unconditionally,
regardless of prompt mode), so this is not a production defect — but it is
a real, if narrow, regression in the function's own defensive contract (a
defaultable public parameter that used to fail loudly now fails silently),
and none of the 7 new tests exercise it. Logged as a non-blocking follow-up
below, not a must-fix — the class of bug this round's fix was actually
scoped to (rescanning of already-substituted text) is fully closed; this is
a different, much narrower class (a caller passing an outright wrong
argument), and the sole real caller can't hit it.

**Verdict on this check: the primitive is correct for everything it's
actually asked to do.** The regex alternation has no ordering/overlap
hazard (none of `{resume}`/`{schema}`/`{prompt_file}` is a substring of
another, and `re`'s alternation tries the literal tokens verbatim, not
prefix-greedy across them), and — the property that actually matters —
`re.sub()` with a replacement function never rescans its own output within
one call, confirmed both by direct testing here and by re-running both of
round 2's original repros against the fixed code myself (not just trusting
the developer's or the tests' claim):
```python
# Repro 1 (schema containing a literal {prompt_file} substring) -- FIXED:
# schema round-trips byte-for-byte, "{prompt_file}" still literally present
# inside it, "/run/abc/prompt.txt" never leaked in, and the REAL
# {prompt_file} substitution still happened correctly as its own argv slot.
# Repro 2 (session_id containing a literal {schema} substring) -- FIXED:
# session_id arrives in argv exactly as given, unmangled; {schema} still
# substituted correctly, separately.
```

### 2. Is the mode-gating decision genuinely explicit and documented?

Yes. `{prompt_file}` is only ever placed into the `mapping` dict when
`engine.headless_prompt == "file"` (`app/teams.py:419-420`) — a key simply
absent from `mapping` leaves that token untouched via `mapping.get(token,
token)`. This is stated explicitly in both `_substitute_headless_tokens()`'s
own docstring (lines 371-377: "explicit, not incidental") and
`_build_headless_argv()`'s (lines 407-412), and has a dedicated test
(`test_prompt_file_token_left_untouched_when_not_in_file_mode`). Confirmed
this is **not new behavior** — the pre-fix code's own `if engine.
headless_prompt == "file": cmd = cmd.replace(...)` already only substituted
`{prompt_file}` in file mode; the fix just makes the same behavior explicit
via the mapping's construction rather than an `if` branch. No regression,
genuinely documented, not incidental.

### 3. Do the new tests pin the behavior or just replay the two repros in test form?

Mixed, but net positive. Of the 4 `BuildHeadlessArgvSinglePassSubstitutionTests`,
2 are direct restatements of the two original repros (expected — permanent
regression tests for confirmed-real bugs are exactly the right thing to
have), but the other 2 are not: `test_prompt_file_token_left_untouched_
when_not_in_file_mode` (mode-gating) and `test_ordinary_case_all_three_
tokens_substituted_correctly` (positive-path regression, all three tokens
resolve together correctly with nothing adversarial). Of the 3
`SubstituteHeadlessTokensPureTests`, `test_single_pass_does_not_rescan_
replacement_text` is a genuine general-property test of the primitive
itself — abstract token names (`{resume}`→`"{schema}"`, `{schema}`→
`"literal-schema"`), not the schema/session_id domain specifics — which is
exactly the right way to pin "no rescanning, ever" as a property rather than
as two coincidences; the other two (absent-key, no-tokens-present) are
useful baseline/no-op coverage. **Gap**: none of the 7 new tests cover an
unrecognized (non-token) `{...}` string, a `None` mapping value, or a
token repeated more than once in one `HEADLESS_CMD` — I checked all three
by hand (see above) and they behave correctly/consistently (except the
`None` case, Finding #3 below), but none of the three is pinned as a
regression test. None of these three is a must-fix gap on its own (all
three are either unreachable from production or unchanged pre-existing
behavior), but worth closing opportunistically.

### 4. Does `docs/implementation.md`'s Round 3 writeup overclaim?

No. Cross-checked every claim in "Round 3, Finding #1"/"Finding #2" against
the actual diff and my own independent testing:
- "Nothing else in `app/teams.py` has this bug pattern" — independently
  re-ran `grep -n "\.replace(" app/teams.py` myself: only 3 real call sites
  (`_resume_fragment()`, `_resolve_schema_fragment()`'s two mutually
  exclusive branches), each a single `.replace()` call into its own short,
  separate template string, never chained. Confirmed a single `str.replace()`
  call cannot rescan its own insertion either (`'a'.replace('a', 'aa')` →
  `'aa'`, not infinite/re-entrant) — the claim holds.
- Severity framing ("a correctness bug, not a privilege/containment
  break... this was never shell injection") — accurate, matches my own
  round-2 assessment exactly, not softened or inflated either direction.
- "The reviewer independently checked this exact document's framing this
  round and explicitly approved it as-is — no change made here in round 3"
  (about Fix 2's honest "did not recur in 3/3" framing) — confirmed
  literally true: diffed the "Fix 2" section's text against what I read in
  round 2 and it is byte-identical except for that one added sentence.
- Round 3 Finding #2's real run (`delegate → fact_check → delegate →
  finish`, 4 rounds, ~58s, real `qwen3:8b` + real `claude` teammate,
  shared `session_id` across both delegate calls) — did not re-run this
  live myself this round (the loop/tier-adapter code it exercises is
  unmodified by this round's diff, and I already independently ran an
  equivalent live 4-step chain in round 2's own review with the same
  shape/outcome), but the JSON shape shown is fully consistent with what I
  personally observed `team_run()` produce in round 2, and nothing about
  this round's diff could plausibly have altered that code path.
- Test/line counts (112 tests, 588 passed, `app/app.py` 23/-3) —
  independently reconfirmed, see "Regression re-check" below.

No overclaiming found in either direction.

### Regression re-check
`uv run --with pytest python -m pytest tests/ -q`, run 3 times this round:
**588 passed** twice; one run hit a single unrelated failure,
`RealTmuxHeadlessTests::test_run_sh_and_prompt_file_are_world_readable_
under_a_strict_umask` (a pre-existing, real-tmux/real-thread/real-`sleep(5)`
timing-sensitive test in `tests/test_teams_headless.py`, whose diff against
this entire branch is empty — `git diff --stat -- tests/test_teams_
headless.py` shows nothing, confirming this test is untouched, committed,
pre-6c code, and the round-3 diff doesn't touch the chmod/permission logic
it exercises either). Passed in isolation and in the next 2 full-suite runs
— a one-off environment timing flake, not attributable to this diff.
`git diff --stat -- app/app.py` still 23/-3. `python3 -m py_compile
app/app.py app/teams.py tests/test_teams_lead.py` clean.
`tests/test_teams_lead.py` alone: 112 passed.

### Round 3 findings

#### 3. [new, non-blocking] A `None` mapping value is silently treated as `""` instead of raising — should-fix (follow-up, not blocking)
- File: `app/teams.py:338-381` (`_substitute_headless_tokens()`) /
  `app/teams.py:384-425` (`_build_headless_argv()`)
- Issue: `mapping.get(m.group(0), m.group(0))` returns the mapping's actual
  value when the key is present, even if that value is `None` — and
  Python's `re.sub()` silently treats a replacement function returning
  `None` as `""`, with no exception. The pre-fix code's own
  `cmd.replace("{prompt_file}", prompt_path)` would have raised
  `TypeError` immediately if `prompt_path` were ever `None`. The one path
  that can produce this is `_build_headless_argv(engine, prompt,
  session_id, prompt_path=None)` on a `headless_prompt == "file"` engine —
  `prompt_path`'s own default parameter value.
- Failure scenario: not reachable from `agent_run()`, the only real caller,
  which always supplies a real `prompt_path` string in file/stdin mode
  before calling `_build_headless_argv()`. Only reachable if a future
  caller (a new code path, or a test) invokes `_build_headless_argv()`
  directly with `headless_prompt == "file"` and omits `prompt_path` — the
  token would silently vanish from the rendered command rather than the
  call failing loudly, producing a confusing downstream engine-CLI error
  (e.g. a flag consuming the wrong following argv element) instead of an
  immediate, clear Python-level error.
- Suggested (non-blocking): either have `_substitute_headless_tokens()`
  reject a `None` mapping value explicitly (`raise ValueError` / `assert`),
  or leave as-is and add a regression test documenting the current
  behavior is intentional, if it is. Fine to leave to a future cycle —
  does not block this round.

## Overall verdict
Changes requested → **approved** as of round 3. Both prior must-fix/
should-fix findings are resolved and independently reverified: the
substitution primitive is a genuine single-pass fix (not just reordering),
correct on the two original repros and on general cases beyond them
(unrecognized tokens, a mapping value shaped like another token, repeated
tokens, empty values); the mode-gating is explicit and documented, not
incidental; the new tests include genuine general-property coverage, not
just repro replays; and `docs/implementation.md`'s round-3 writeup holds up
against independent verification with no overclaiming. One new, narrow,
non-blocking finding (`None` mapping value silently swallowed rather than
raising, Finding #3) is logged as a follow-up — not reachable from the only
real caller, so it doesn't block approval. Full suite green (588 passed,
consistent across runs; one isolated flake in an untouched pre-existing
test, confirmed non-attributable to this diff). `app/app.py`'s diff scope
unchanged (23/-3).

---

## Round 2 verdict (superseded by Round 3 above)
Changes requested — one must-fix (`_build_headless_argv()`'s substitution-
ordering defect, Finding #1) needs a structural single-pass-substitution fix
before this is ready to merge. Finding #2 (documentation gap) should be
closed in the same pass since it's cheap, but is not itself a functional
blocker — I independently proved the underlying capability works.
Everything else checked out: all 17 acceptance criteria have real
implementation and test coverage, the full suite is green across multiple
runs (mine and the developer's), the schema-placeholder correction is
verified against real `claude`/`codex` CLIs, roster-build-time error
surfacing works on both paths, the repeated-delegation mitigation's honest
"did not recur in 3/3" framing is preserved with no test overclaiming it,
and all four cross-cutting constraints (TMUX-only privilege, read-only
grounding, no `realpath()` fallback, manual-only deploy) still hold.

---

# Test & Review: Team session lifecycle, part 1 — worktrees + tmux dashboard session (sub-spec 6d, part 1 of 2)

New sub-spec, new cycle — this section is independent of everything above
(which covered 6c, now merged as `cbc6870`). Baseline before this cycle:
588 passed (6c). After this cycle's diff: 635 passed (588 + 47 new).

## Scope
Covers `docs/spec.md`'s (6d part 1) full acceptance-criteria list (worktree
lifecycle, `team-<project>` tmux dashboard session/windows, `team-launch`/
`team-stop`/`team-reap`, `sweep_dead_teams()`'s three-case status logic,
the `team_step()` delegate-branch worktree/log-path change, engine-name
reservation) plus the coordinator's specific asks: an independent
determination of whether a residual race window in
`_kill_team_session_if_owned()` is real, verification of both of the
developer's own self-reported defect fixes (not just re-trusting their
report), confirmation of the four standing cross-cutting constraints, and a
check for overclaiming in `docs/implementation.md`.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `team-launch` against a real repo creates `run.json`/worktrees/session+windows in order | real git + real tmux | pass | `LaunchTeamRealTmuxTests.test_launch_creates_worktrees_and_session_windows` — independently re-read, matches AC exactly |
| 2 | Dirty tree / detached HEAD / non-git dir each get a distinct message and leave nothing behind | real git | pass | `LaunchTeamRealTmuxTests.test_dirty_tree_leaves_no_worktree_no_session_no_state_dir`, `test_detached_head_leaves_nothing_behind`, `test_non_git_directory_leaves_nothing_behind`; `ValidateProjectForTeamRealGitTests` (pure precondition checks, all 5 states) |
| 3 | Double `team-launch` refused before any worktree touched, first run unaffected | real tmux | pass | `test_double_launch_refused_first_run_byte_for_byte_unaffected` |
| 4 | Dashboard window shows accumulated output across ≥2 delegations, second appended not replacing first | real tmux `capture-pane`, real `agent_run()` via stand-in engine | pass | `TeamRunDelegateWorktreeAndDashboardTests` — independently read in full, real end-to-end (worktree file placement + session continuity + dashboard accumulation all asserted together, matching the AC's own "not traded off against each other" requirement) |
| 5 | `lead` window matches `transcript.jsonl` content live | same test as #4 | pass | same test, `_capture_with_retry` against the `lead` window, compared to `transcript.jsonl`'s own parsed lines |
| 6 | Delegation writes land in the teammate's worktree, not the shared project dir; second delegation resumes session while still worktree-scoped | same test as #4 | pass | asserted directly: `call-1.marker`/`call-2.marker` present under the worktree, absent from the shared repo root; `teammate_sessions["faketm"]` shows the resumed session id |
| 7 | `team-stop` kills real session, removes clean worktree (dir gone, branch survives), leaves dirty worktree intact | real git + real tmux | pass | `StopTeamRealTmuxTests.test_stop_running_run_kills_session_dirty_left_clean_removed`; independently re-verified git's own dirty-refusal message and branch survival myself (see below) |
| 8 | `team-stop` on an already-finished run still tears down unconditionally | real tmux | pass | `test_stop_on_already_finished_run_still_unconditional` |
| 9 | Simulated crash + `team-reap` is a two-pass sequence (mark error, then sweep on a later pass with TTL forced to 0) | real tmux | pass | `TeamReapRealTmuxTests.test_crash_then_reap_is_a_two_pass_sequence` — read in full, matches AC's exact two-pass shape, including the branch-survives assertion |
| 10 | `blocked_ask_user` never swept regardless of TTL | pure + real tmux | pass | `test_blocked_ask_user_never_swept_regardless_of_ttl` (real), `SweepDeadTeamsPureTests.test_blocked_ask_user_never_swept_even_with_ttl_zero` (pure) |
| 11 | `agent_run()`/`team_step()` existing (no-worktree) behavior byte-for-byte unchanged | regression | pass | `git diff --stat -- tests/test_teams_headless.py tests/test_teams_lead.py tests/test_teams_grounding.py` empty (independently reconfirmed); all three suites pass as part of the 635 |
| 12 | `engines.d/team.engine` silently ignored, same as `switchboard` | real scratch `.engine` file | pass | `EngineNameReservationRealTests` (3 tests) — independently read, exercises a real file, not just the reservation tuple |
| 13 | Dashboard input files world-readable under a realistic strict umask | real strict `umask(0o077)` | pass | `LaunchTeamWorldReadableUnderStrictUmaskTests` — independently read, checks `S_IROTH`/`S_IXOTH` on every file/dir a window's `tail -F` needs |
| 14 | Full suite green, several runs; `app/app.py` diff limited to the reservation change | regression | pass | see "Regression check" below |

## Regression check
`/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q`, run
3 times independently this session: **635 passed** every time, no flake
observed in my own runs (the coordinator's disclosed pre-existing
`test_teams_headless.py` flake did not reproduce for me this session, but I
did not go looking for it separately since it's explicitly disclosed,
pre-existing, and that file's diff is empty). `tests/test_teams_lifecycle.py`
alone: 47 passed. `git diff --stat -- app/app.py`: 23 insertions / 11
deletions, independently confirmed to be entirely the reservation-tuple
change plus its expanded comment (read the full diff myself, not just the
stat) — `str.startswith()` accepting a tuple is correct Python. No
`app.teams` import in `app/app.py` (`grep` came back empty).

## Requester's specific checks

### 1. The residual unstamped-session race window — CONFIRMED REAL, independently reproduced, judged must-fix

Reproduced live, independent of the coordinator's own repro (own script, own
tmux session, own assertions):

```python
subprocess.run(teamsmod.TMUX + ["new-session", "-d", "-s", session, "-n", "lead", "bash", "-lc", "sleep 300"])
print(teamsmod.tmux_has(session))                        # True
print(repr(teamsmod._team_session_run_id(session)))       # ''  (unstamped)
result = teamsmod._kill_team_session_if_owned(session, "unrelated-run-id-999")
print(result, teamsmod.tmux_has(session))                 # True False
```
Confirmed: an unstamped session — the exact state `_create_team_session()`'s
own session sits in for the full duration between its `new-session` call
(line 2960) and its `@switchboard_team_run_id` stamp (line 2969) — is killed
by `_kill_team_session_if_owned()` when called with a completely unrelated
`run_id`, and reports `True` (success) while having destroyed a session that
was never that caller's to touch.

**No locking exists anywhere in `app/teams.py`** — independently confirmed
(`grep -n "flock\|O_EXCL\|Lock(\|filelock" app/teams.py` → no matches).
`sweep_dead_teams()` is invoked opportunistically at the top of **every**
`launch_team()`/`stop_team()` call for **any** project (not just the one
racing), iterating over every `run_id` under `_leads_root()` — so the actual
attack surface is wider than "one project's own launch racing its own old
run's reap": **any** concurrent `team-launch`/`team-stop`/`team-reap`
invocation, for any project, that happens to process a stale, terminal,
same-project-name old run while a brand-new same-project launch is mid-flight
can hit this window.

**Width of the window, reasoned about concretely, not dismissed as
infinitesimal**: the vulnerable span is `new-session` returning → Python's
own `tmux_has()` recheck (a `subprocess.run` call) → two `set-option` calls
— three to four separate `sudo -u RUN_USER tmux ...` subprocess round-trips
in production (this environment's tests bypass `sudo` via the `TMUX`
monkeypatch, so the window is narrower in the test suite than it would be in
production, where `sudo` overhead alone is commonly tens of milliseconds per
call). A racing `sweep_dead_teams()` pass processing several stale runs
before reaching the colliding one widens the calendar-time overlap further.
This is not a one-in-a-billion race; it is a genuine, if uncommon,
multi-process interleaving that becomes more likely, not less, if
`team-reap` is ever run on a schedule (a natural next step even though part
2 doesn't build it) rather than purely by hand.

**Consequence, worse than "the session merely disappears and something
notices"**: I traced the two ways the race can land and neither is silent-safe:
- If the kill lands *before* `_create_team_session()`'s own internal
  `tmux_has()` recheck (line 2965) — `_create_team_session()` reports
  `{"ok": False, "error": "failed to create team session (tmux new-session
  failed)"}`, a **misleading** message (blames a local `new-session`
  failure that never actually happened) but `launch_team()`'s own rollback
  *does* correctly clean up the worktrees it just created — no orphaned
  resource, just a confusing error.
- If the kill lands *after* that recheck but before/during the `set-option`/
  `new-window` calls — `_create_team_session()` has already returned
  `{"ok": True, ...}` at that point (it never re-verifies the session is
  still alive after its own `set-option`/`new-window` calls), so
  `launch_team()` reports **`{"ok": True, "run_id": ..., "session": ...}`
  — a false success** — while the actual tmux session is dead and no member
  windows exist. This is a **silent false positive**, not a loud failure:
  a human or script calling `team-launch` sees success and may proceed to
  `team-resume` (which works fine — it doesn't touch the dashboard session
  at all — the lead loop still runs), but any human relying on the
  dashboard windows for visibility gets nothing, with no error telling them
  why. This self-heals eventually (the *next* `sweep_dead_teams()` pass
  finds `status: "running"` with the session gone and marks it `"error"`,
  per case 1) but only once someone happens to run a sweep again — not
  immediately, and not with any diagnostic pointing at the actual cause.

**Judgment: this is the same defect class as both of the developer's own
disclosed fixes** (stale run destroying a newer run's live resource; an
observation-ordering gap around a fast/racing operation), in the exact
function built to close the first one, on the specific path the docstring
itself calls out and then explicitly declines to defend
(`_kill_team_session_if_owned()`'s own comment: "should not happen in
practice... but never assumed" — followed immediately by treating the
unstamped case as safe to kill, which *is* an assumption, the one the rest
of this story's own established discipline argues against). It is also
**undisclosed** — `docs/implementation.md`'s "Known limitations" section
does not mention it, unlike this same document's careful disclosure of
every other open gap (the four-things-don't-stop-together footgun, the
carried-forward 6c limitations). Given a low-cost structural fix exists (see
below) and the story's own stated principle in this very diff
("structural fixes beat tuned constants, running the real thing beats
reasoning about it"), I judge this **must-fix**, not should-fix — not
because the blast radius is catastrophic (it isn't: no data loss, bounded to
a fresh session with nothing valuable in it yet, eventually self-healing),
but because the fix is cheap, the failure mode includes a genuinely
misleading silent-success report, and leaving it open contradicts the
review discipline this exact cycle's own two fixes were built to uphold.

**On the two options the coordinator posed, plus a third I found:**
- *Fail closed on an unstamped session* (treat `owner == ""` the same as
  `owner != run_id`, never kill) would close the race but has a real,
  demonstrable cost the coordinator was right to flag: I traced a **second,
  independent way an unstamped session can arise with no concurrency at
  all** — `launch_team()`'s own process crashing (OOM-kill, host reboot)
  between `new-session` and the stamp. In that scenario `run.json` already
  says `status: "running"` (persisted *before* `_create_team_session()` is
  called), and the session is genuinely alive but permanently unstamped —
  `sweep_dead_teams()`'s crash-detection (case 1: `tmux_has()` false) never
  fires for it, since the session is NOT gone, just incomplete. Under a
  blanket fail-closed rule, that run's own **legitimate**, later
  `team-stop <run_id>` call would *also* refuse to touch it (an empty owner
  can never match any `run_id`), permanently stranding it — exactly the
  counter-argument the coordinator suspected but hadn't confirmed a concrete
  mechanism for. I now have one.
- **A third option, which I recommend over either of the coordinator's
  two**: make session creation and stamping **atomic**, closing the window
  structurally rather than deciding what to do about an observable
  unstamped state at all. tmux supports chaining multiple commands into
  ONE client invocation via `\;`, sent to the server as a single request —
  I verified this directly:
  ```
  tmux new-session -d -s atomictest -n lead bash -lc "sleep 300" \; \
       set-option -t atomictest remain-on-exit on \; \
       set-option -t atomictest @switchboard_team_run_id "myrunid123"
  # rc=0; `tmux show-options -t atomictest -v @switchboard_team_run_id` -> "myrunid123" immediately
  ```
  Folding `_create_team_session()`'s `new-session` + both `set-option` calls
  into one such invocation means no external process can ever observe the
  session in an unstamped state — the race this whole discussion is about
  ceases to exist, not just gets a policy decision applied to it. The
  per-member `new-window` calls (a variable-length list) don't need to be
  part of this same atomic call — they don't affect the ownership check.
  This avoids the fail-closed tradeoff's own new regression entirely and
  matches this project's own stated preference for structural fixes. I'd
  suggest this as the primary fix, with fail-closed treated as unnecessary
  once the window is actually closed (or, if the developer wants
  belt-and-braces, fail-closed layered on top *after* the atomic fix, since
  at that point a genuinely-unstamped session really would only ever mean
  "some other, non-`_create_team_session()`-shaped tool created this" — a
  much narrower, more defensible case for erring toward caution).

### 2. Both developer-claimed fixes — hold up under independent verification

**`_run_run_user_command()`'s causal re-read fix**: verified the mechanism,
not just trusted the before/after failure-rate numbers. The throwaway
session `_run_run_user_command()` creates does **not** set
`remain-on-exit` (independently confirmed: `grep -n remain-on-exit
app/teams.py` shows it set only inside `_create_team_session()`, never in
`_run_run_user_command()`) — meaning tmux's *default* behavior applies: the
session is destroyed only once its sole pane's process (`bash -lc script`)
exits. Since that script's last statement is `echo $? > rcfile`, and bash
only terminates after completing its final statement, `tmux_has(session)`
transitioning to `False` is causally downstream of the rc-file write
completing — and a `write()` that has returned is immediately visible to
any subsequent `read()` on the same host (standard POSIX same-machine
read-after-write visibility, not a durability claim). The claim is sound,
not merely plausible. Independently reproduced empirically too: 40/40 rapid
`_run_run_user_command(["echo", "hello"], ...)` calls succeeded with no
"command session ended unexpectedly" errors (the exact symptom the
developer's own pre-fix report described).

**The ownership-stamp fix**: not re-verified per the coordinator's own
instruction (already independently confirmed live by the coordinator) —
except insofar as finding its residual gap above *is* a deeper verification
of the same code, not a rubber stamp of "it works, done."

### 3. `git worktree remove` never uses `--force`; branches survive removal — CONFIRMED independently, against real git, not the test suite

`grep -n -- "--force" app/teams.py` — the only three occurrences are: one
inside a human-facing error-message string (the manual-cleanup suggestion),
and two in docstrings/comments; the only real `git worktree remove` argv
(`app/teams.py:2864`) never includes it. Reproduced independently against a
real repo, outside the test suite:
```
$ git worktree remove .teams/claude        # dirty (uncommitted file present)
fatal: '.teams/claude' contains modified or untracked files, use --force to delete it
# directory + branch both still present after
$ git worktree remove .teams/claude        # after removing the uncommitted file
# rc=0; directory gone; `git branch --list` still shows team-abc123-claude
```
Matches `_remove_worktree()`'s own stderr-substring classification
(`"modified or untracked files"` / `"use --force"`) exactly, and confirms
the branch-survives claim directly rather than trusting `docs/spec.md`'s own
assertion about git's behavior.

### 4. Four standing constraints — all confirmed still hold

- **No new sudoers line / no new privileged path**: independently grepped
  every added `subprocess.run`/`subprocess.Popen` call
  (`git diff -- app/teams.py | grep -n "^+" | grep -iE "sudo|subprocess\."`)
  — every privileged call is `TMUX + [...]` (the existing constant);
  `_validate_project_for_team()`'s three `git -C workdir ...` calls are
  plain, unprivileged `subprocess.run` (SVC_USER, read-only), matching the
  spec's own explicit design. `_run_run_user_command()` (the new helper)
  reaches RUN_USER *only* through `TMUX` — confirmed by reading its full
  body, not just the docstring's claim.
- **Grounding strictly read-only, both guards intact**:
  `git diff --stat -- tests/test_teams_grounding.py` empty — file untouched.
- **No path-based `realpath()` fallback**: `git diff -- app/teams.py | grep
  realpath` — no matches; the fail-closed section (`app/teams.py:1196-1218`)
  is untouched by this diff.
- **Deploy stays manual-click-only**: `git diff -- app/teams.py | grep -i
  deploy` — no matches; unrelated to this cycle's scope.

### 5. Acceptance criteria and `project_name` derivation for odd `workdir` shapes

All 14 acceptance criteria (my own numbering above, collapsing the spec's
enumerated bullets where one test covers two adjacent criteria) have real
implementation and real test coverage — spot-read the test bodies for the
highest-value ones (worktree-vs-shared-dir placement + dashboard
accumulation + session continuity all in one test, the two-pass crash/reap
sequence, the strict-umask permission test), not just their names.

`project_name = os.path.basename(os.path.normpath(workdir))`, tested
directly against odd shapes:
```
'/tmp/x/demo'      -> 'demo'      (ordinary)
'/tmp/x/demo/'     -> 'demo'      (trailing slash, handled)
'/tmp/x/demo//'    -> 'demo'      (multiple trailing slashes, handled)
'/'                -> ''          (filesystem root -> EMPTY project_name)
```
The `/` case would produce `_team_session_name('')` = `"team-"` — a
degenerate but non-crashing session name. **Not exploitable/reachable in
practice**: `launch_team()` calls `_validate_project_for_team(workdir)`
*before* deriving `project_name` (confirmed by reading the actual call
order), and `/` being a clean, non-detached-HEAD git repository is not a
realistic deployment scenario — this is gated out before it matters. Also
noted: `os.path.normpath()` is purely lexical, so a `workdir` that is itself
a symlink gets a `project_name` based on the symlink's own name, not its
resolved target's — consistent with how this codebase already treats
project directories elsewhere (not a new risk class this cycle introduces).
Both logged as non-blocking nits below, not findings.

### 6. Does `docs/implementation.md` overclaim?

One material omission (the undisclosed race window, folded into Finding #1
below — this is the overclaim-adjacent issue: "never assumed" language in
the code's own docstring is not actually upheld for this one case, and
`docs/implementation.md`'s "Known limitations" section doesn't mention it
even though the document otherwise discloses every other open gap
carefully). Everything else checked out under direct verification: the
`_run_run_user_command()` causal claim is sound (verified above, not just
trusted); the `app/app.py` diff-scope, sudoers, `--force`, grounding, and
`realpath()` claims are all independently confirmed accurate; the
"structural fix, not a band-aid" framing for Defect #2 is accurate and not
inflated. No other overclaiming found.

## Constraints re-checked
See "4. Four standing constraints" above — all four hold, independently
re-verified this round (not carried over from a prior cycle, since this is
a new spec with genuinely new privileged-surface code:
`_run_run_user_command()` is new).

## Spec coverage
All of `docs/spec.md` (6d part 1)'s acceptance criteria are implemented and
tested — see the Test cases table. No acceptance criterion is uncovered.
The one gap found (Finding #1) is **not** an uncovered acceptance criterion
— it's a real defect in a code path no acceptance criterion was written
against, found by testing past the spec's own enumerated cases, the same
way the developer found their own two defects.

## Findings (most severe first)

**Finding 1 below is RESOLVED as of the round-2 re-review — see "Round 2
re-review" below. Kept verbatim for the record.**

### 1. [RESOLVED round 2] `_kill_team_session_if_owned()` treats an unstamped session as safe to kill — a real, reproducible, undisclosed residual window in the same defect class this cycle just fixed — must-fix
- File: `app/teams.py:2907-2926` (`_kill_team_session_if_owned()`),
  `app/teams.py:2954-2975` (`_create_team_session()`, the source of the
  unstamped window)
- Issue: between `new-session` (line 2960) and the `@switchboard_team_run_id`
  stamp (line 2969), a brand-new session exists but is unstamped.
  `_kill_team_session_if_owned()` treats `owner == ""` as safe to kill
  unconditionally, for ANY `run_id` asking, including one that has nothing
  to do with the session in question.
- Failure scenario: a concurrent `team-reap` (or `team-stop <old_run_id>`)
  processing an old, stale, terminal run for the same project (or, since
  `sweep_dead_teams()` runs opportunistically inside *any* `launch_team()`/
  `stop_team()` call for *any* project, potentially triggered by an
  unrelated project's own launch/stop) lands its `_kill_team_session_if_
  owned()` call inside this window and kills the brand-new session. Depending
  on exactly when the kill lands relative to `_create_team_session()`'s own
  internal recheck, `launch_team()` either reports a misleading "tmux
  new-session failed" error (worktrees correctly rolled back), or — worse —
  reports `{"ok": True, ...}`, a **false success**, while the real session
  is dead and no dashboard windows exist, self-healing only once a later
  sweep pass happens to run.
- Recommended fix: make `_create_team_session()`'s `new-session` +
  `set-option remain-on-exit` + `set-option @switchboard_team_run_id` one
  atomic tmux invocation via `\;` chaining (verified this works — see
  "Requester's specific checks" #1 above) rather than deciding what to do
  about an observably-unstamped session at all. A blanket fail-closed
  policy is a real alternative but has its own demonstrated cost (strands a
  genuinely solo-crashed, permanently-unstamped session, unrecoverable by
  its own future legitimate `team-stop` without manual `tmux kill-session`)
  — I'd only layer that on *after* the atomic fix, if at all, not instead of
  it.

## Follow-ups (non-blocking)
- `project_name` derived from a `workdir` of `/` (filesystem root) produces
  an empty string, yielding a degenerate `team-` session name — gated out in
  practice by `_validate_project_for_team()` running first, but worth a
  one-line guard (`if not project_name: return {"ok": False, ...}`) for
  defense in depth, cheap and never triggered under normal use.
- `docs/implementation.md`'s "Known limitations" section should name the
  Finding #1 gap once fixed (or, if the coordinator decides to accept the
  narrow race as a disclosed, deliberate tradeoff instead of fixing it, it
  should be named there rather than left silent — my own recommendation is
  to fix it, given the low cost).

## Overall verdict (round 1 — superseded by "Round 2 re-review" below)
Changes requested — one must-fix (Finding #1: the residual unstamped-session
race window in `_kill_team_session_if_owned()`/`_create_team_session()`).
Everything else in this cycle checked out under independent verification:
all 14 acceptance criteria have real git/tmux-backed test coverage (spot-read
in full, not just by name), both of the developer's own self-reported
defects hold up (the causal re-read fix's mechanism is sound, independently
reasoned through and empirically reproduced, not just trusted from the
before/after numbers), `git worktree remove` never uses `--force` and
branches genuinely survive removal (reproduced against real git myself,
outside the test suite), all four standing cross-cutting constraints hold,
the full suite is green across 3 independent runs (635 passed every time),
and `docs/implementation.md` does not overclaim anywhere except the one
omission folded into Finding #1 above. The fix is narrow and well-scoped
(a single atomic-tmux-invocation change to `_create_team_session()`), so
the loop-back cost to close this should be low.

---

## Round 2 re-review

Focused re-review only, per the coordinator's instruction — did not re-clear
the causal re-read fix, `git worktree remove` without `--force`, the four
standing constraints, or the acceptance criteria (all already independently
verified in round 1 above and untouched by this round's diff). This round's
diff is `_create_team_session()`, `_kill_team_session_if_owned()`, both
docstrings, one new test (`CreateTeamSessionAtomicStampTests`), and
`docs/implementation.md`'s "Defect #3"/"Known limitations" writeup.

### Diff reviewed
`_create_team_session()` now issues `new-session` + `set-option remain-on-
exit` + `set-option @switchboard_team_run_id` as one `subprocess.run(TMUX +
[..., ";", ..., ";", ...])` call (`;` as a literal argv element, no shell
involved) instead of three separate calls. `_kill_team_session_if_owned()`
is behaviorally unchanged (still treats an unstamped session as killable);
only its docstring changed, dropping the "should not happen in practice"
claim in favor of an atomicity-based correctness argument.

### 1. Is the atomic invocation correct in the failure cases, not just the happy path?

Tested three failure shapes directly against the real `tmux` binary (not
just the happy path already covered by the developer's own test):

**Duplicate-session refusal (`new-session` itself fails)** — the whole
chain aborts, no later command executes:
```
$ tmux new-session -d -s existingsess ... \; set-option ... remain-on-exit on \; set-option ... @switchboard_team_run_id "OWNER-B"
duplicate session: existingsess
$ tmux show-options -t existingsess -v @switchboard_team_run_id
OWNER-A   # unchanged -- NOT overwritten by the failed chain's own stamp attempt
```
This was the scenario I was most worried about — a race where `new-session`
fails because a *different, legitimate* session already exists under that
name, and the chain's *later* `set-option ... run_id` commands execute
anyway against that pre-existing session, silently reassigning its
ownership stamp to the wrong run. **Confirmed this does NOT happen** — tmux
aborts the entire `;`-chained batch on the first command's failure. Good:
`_create_team_session()`'s own precondition check (`if tmux_has(session):
return {"ok": False, ...}`) plus this abort-on-first-failure behavior means
a TOCTOU race here degrades to a merely-confusing error message
("failed to create team session (tmux new-session failed)" — the same
generic message a genuine local failure gets, not distinguishing "someone
else already made this" from "tmux itself failed"), never data corruption.

**A LATER command in the chain failing (not `new-session` itself)** — found
a real gap here, not previously tested by anyone:
```
$ tmux new-session -d -s chaintest ... \; set-option -t chaintest remain-on-exit on \; set-option -t chaintest -g THIS_IS_INVALID foo
invalid option: THIS_IS_INVALID
$ tmux has-session -t chaintest
(session exists)
$ tmux show-options -t chaintest remain-on-exit
remain-on-exit on          # the FIRST set-option DID take effect
$ tmux show-options -t chaintest -v @switchboard_team_run_id
invalid option: @switchboard_team_run_id     # the stamp never ran -- chain aborted here
```
So: if `new-session` succeeds but the run_id-stamp `set-option` (the last
command in the chain) fails for any reason, **the session is created,
`remain-on-exit` is set, but the run_id stamp never lands** — a
permanently, observably unstamped session is left behind. `_create_team_
session()`'s own check (`if r.returncode != 0 or not tmux_has(session):
return {"ok": False, ...}`) correctly detects the failure and returns
`ok: False`, but **does not kill the partially-created session it just
left behind** — no `kill-session` cleanup on this failure path at all
(confirmed by reading the full function body). `launch_team()`'s own
rollback on a `_create_team_session()` failure only removes worktrees and
deletes the fresh run_id's state directory — it never touches any tmux
session, since under the OLD design a "`_create_team_session()` failed"
outcome was assumed to mean "no session exists" (true when `new-session`
itself is what failed, not true when a *later* chain link fails after
`new-session` succeeded).

**Consequence, traced through**: this orphaned, unstamped session sits
under the exact `team-<project>` name with no `run.json` anywhere
referencing it (the record was deleted by `launch_team()`'s own rollback).
Every *future* `team-launch` attempt for that project is permanently
blocked at `_create_team_session()`'s own `tmux_has(session)` precondition
("a team session is already running for '<project>' ...") — a real lockout,
not just a wasted attempt. `sweep_dead_teams()` can never find or clean it
(it only iterates known `run_id`s under `_leads_root()`, and this orphan's
own `run_id` record no longer exists). The only recovery paths are a manual
`tmux kill-session -t team-<project>`, or an operator happening to run
`team-stop` on some *other*, unrelated `run_id` for the same project as a
side effect (since `_kill_team_session_if_owned()` still treats any
unstamped session as killable regardless of which `run_id` is asking) —
neither obvious nor documented.

**Severity judgment: should-fix, not must-fix.** This is real (reproduced
against the actual binary, not hypothetical) and its consequence is sticky
(a lockout requiring manual intervention, worse in recoverability than the
original bug's own consequences), but its **trigger condition is
substantially narrower** than the original defect's: the two chained
`set-option` calls use hardcoded option names and a code-generated,
always-safe `run_id` string (`f"{int(time.time())}-{secrets.token_hex(6)}"`)
— I could not construct a realistic way for either to fail in normal,
healthy operation (my repro above required an artificially invalid option
name to force it). The original defect was must-fix because it was
reachable under *ordinary* concurrent CLI usage; this one requires the tmux
server itself misbehaving mid-chain, which is a much rarer class of event.
Recommended fix, matching this codebase's own established cleanup-on-
failure discipline elsewhere (`agent_run()`/`_run_run_user_command()`'s own
`finally: kill-session`/`shutil.rmtree()` blocks): `_create_team_session()`'s
own failure branch should attempt `subprocess.run(TMUX + ["kill-session",
"-t", session], capture_output=True)` before returning `ok: False`, so a
partially-created session never outlives its own failed creation attempt.

### 2. Is `_kill_team_session_if_owned()`'s unchanged behavior now genuinely safe, including across an upgrade from pre-fix code?

Reasoned through the upgrade path concretely, not just accepted the
docstring's claim. Two cases:

- **A session fully created and stamped by the OLD three-call code before
  the upgrade** — no compatibility concern. The stamp option's name/value
  format is identical in both versions; a session that finished creation
  successfully under the old code is indistinguishable from one created by
  the new code, and the new ownership check treats it identically (correctly).
- **A session left genuinely orphaned by the OLD code's own race, still
  sitting on the tmux server at upgrade time** — keeping "unstamped is
  killable" unchanged is actually the *right* call here, not just an
  inherited one: a pre-existing orphan from before the fix has no legitimate
  owner by construction (the old code's own race is what produced it), so
  treating it as killable is correct cleanup, and — importantly — it means
  the run that *originally* tried to create it can still self-recover via
  its own later `team-stop <run_id>` call, which a blanket fail-closed
  design (the alternative considered and rejected in round 1) would have
  permanently broken. This is the same self-recovery property that makes
  keeping "unstamped is killable" the right call for the NEW orphan class
  found in check #1 above, too — reinforcing that fixing #1's cleanup gap
  (rather than reaching for fail-closed) is the right direction, not a
  contradiction of this decision.

No upgrade-path-specific risk found beyond what's already covered by
Finding #1 above (both are instances of "how does a genuinely orphaned
unstamped session get discovered and reclaimed", not two separate concerns).

### 3. Does the new regression test actually pin the behavior?

Re-validated independently, using my own standing to check the developer's
10/10-vs-0/10 claim rather than accepting it.

**First attempt (weaker evidence, reported for transparency)**: a
simplified hand-rolled reconstruction of the old three-call shape (missing
the old code's own internal `tmux_has()` recheck and per-member window
creation) caught the race only 2/30 times across two batches, while 0/40
trials against the real fixed `_create_team_session()` never false-positive.
This is real, nonzero signal that the technique detects *something*, but
much weaker than the developer's claimed 10/10 — worth flagging as a
genuine discrepancy rather than silently rounding it up to "confirmed".

**Second attempt (the one that matters)**: reconstructed the OLD shape
*faithfully* (including the internal `tmux_has()` recheck after
`new-session` and per-member `new-window` calls, matching the actual
previously-shipped code), monkeypatched it into `teams._create_team_session`,
and ran the **actual** `CreateTeamSessionAtomicStampTests` test method
against it 10 times: **9/10 runs correctly failed** (caught the
reintroduced race) — closely matching the developer's own reported 10/10,
well within normal timing-race variance, and far stronger evidence than my
first, simplified attempt. Also ran the actual test 8 times against the
real, fixed code with the TMUX monkeypatch: **8/8 passed**, no false
positives, matching the developer's own report exactly.

**Conclusion: the test genuinely pins the behavior**, not passing
incidentally — it reliably (9/10 against a faithful old-shape reconstruction)
catches a reintroduction of the exact original bug, and reliably (8/8, plus
my earlier standalone 40/40) never false-positives against the fixed code.
The lesson from my own two attempts: a *simplified* stand-in for the old
code understates the race's true reproducibility — the full old shape
(extra tmux round-trips for the recheck and per-member windows) widens the
observable window measurably, which matters for anyone else trying to
independently verify this class of claim in the future.

### 4. Does `docs/implementation.md`'s Defect #3 writeup / Known limitations bullet overclaim?

No overclaiming found in what it covers. The narrative section (root cause,
why fail-closed was rejected, the atomic fix, verification numbers) is
accurate against my own independent testing — the "10/10 ... 0/10" and
"0/8 ... 8/8" figures match what I found (9/10 and 8/8 respectively, within
normal variance). Credits the reviewer's own find accurately, doesn't
inflate the fix's guarantees, and correctly frames "unstamped is safe to
kill" as now-correct-because-atomic rather than merely re-asserting the
old, disproven claim. **One gap, not an overclaim**: neither the Defect #3
writeup nor the "Known limitations" bullet could have covered Finding #1
above (the later-chain-link-failure orphan), since it's a new finding from
*this* review round — not a fault of the writeup as submitted, but worth
folding in once addressed (or explicitly disclosed as a known limitation,
if the coordinator decides not to fix it).

## Findings, round 2 (most severe first)

### 2. `_create_team_session()`'s failure path doesn't clean up a partially-created (unstamped) session — should-fix
- File: `app/teams.py:2987-3002` (`_create_team_session()`)
- Issue: if `new-session` succeeds but a later command in the same `;`-chain
  fails (verified reproducible against the real `tmux` binary, though only
  via an artificially-invalid option name — no realistic trigger found for
  the two actual hardcoded `set-option` calls this function issues), the
  session is created and left alive, `remain-on-exit` may or may not be set
  depending on exactly which link failed, and the run_id stamp is never
  applied — `_create_team_session()` detects this (`r.returncode != 0`) and
  returns `{"ok": False, ...}` but never kills the session it just
  partially created.
- Failure scenario: any future `team-launch` for the same project is
  permanently refused at `_create_team_session()`'s own "already running"
  precondition, `sweep_dead_teams()` can never discover or clean it (its
  own `run_id`/`run.json` record was already deleted by `launch_team()`'s
  rollback), and the only recovery is a manual `tmux kill-session` or an
  operator incidentally running `team-stop` on an unrelated `run_id` for
  the same project.
- Recommended fix: `_create_team_session()`'s failure branch should
  `kill-session` any partially-created session before returning `ok: False`
  — matching `agent_run()`/`_run_run_user_command()`'s own established
  cleanup-on-failure discipline.
- Severity: should-fix, not must-fix — the trigger condition (a
  well-formed, hardcoded `set-option` call failing) has no realistic path
  under normal operation, unlike the original (now-fixed) defect, which was
  reachable under ordinary concurrent CLI usage.

## Overall verdict
**Approved**, with one should-fix follow-up (Finding #2 above) logged for
the coordinator's own judgment on whether to loop back now or track it —
I don't believe it rises to blocking given how narrow its trigger condition
is (I could not construct a realistic way to hit it without an artificially
invalid tmux option). The must-fix from round 1 (the unstamped-session race
reachable under ordinary concurrent usage) is genuinely closed: the atomic
`;`-chained invocation is correct in the failure cases I tested (a
duplicate-session race degrades to a confusing-but-harmless error, never
silently reassigns an existing session's ownership stamp), `_kill_team_
session_if_owned()`'s unchanged "unstamped is killable" behavior is now
justified rather than merely inherited (reasoned through the upgrade path
concretely, including for pre-fix orphaned sessions), the new regression
test genuinely pins the behavior (independently re-validated against a
faithful reconstruction of the old code: 9/10 catches the reintroduced bug,
8/8 clean against the fix), and `docs/implementation.md`'s Defect #3
writeup is accurate with no overclaiming. Full suite reconfirmed green.

---

# Test & Review: Team session lifecycle, part 2a — web routes, background driving thread, cooperative cancellation (sub-spec 6d, part 2a of 2)

New sub-spec, new cycle — independent of everything above (6c and 6d part 1
are both merged). Baseline before this cycle: 638 passing. After this
cycle's diff: 671 passed (pytest) + 17 passed (`test_team_frontend.js`,
Node, outside the pytest count).

## Scope

Covers `docs/spec.md` (6d part 2a)'s full acceptance-criteria list
(`import teams` placement, both new routes, `default_team_composition()`'s
priority order and the tier-3 refusal, the two-near-simultaneous-starts
race, cooperative cancellation at all three checkpoints, service-restart
safety, the orphan-check self-correction, `install.sh`'s new copy line) plus
the coordinator's four specific asks (cancellation latency/gaps between
checkpoints; the two-concurrent-starts collision-point judgment call; stop/
restart re-derivation from `run.json` rather than `_team_threads`; a
check-then-act race on `_team_threads` itself), the tier-3 CLI/route
boundary, both disclosed frontend deviations, the five standing
cross-cutting constraints, and a WCAG contrast recomputation against the
actual shipped CSS (not the design doc's own stated numbers).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `app.py` actually imports/starts (not just `py_compile`) | real process execution | pass | independently re-ran `python3 -c "import sys; sys.path.insert(0,'app'); import app"` myself; also ran a real `ThreadingHTTPServer`+`/login`+`/status` round trip (see restart test below, which required exactly this) |
| 2 | `POST /team/start` happy path (tier-2 default), persisted state matches | real HTTP | pass | `TeamStartEndpointTests.test_happy_path_tier2_default_lead_persisted_correctly` — read in full |
| 3 | Tier-3-only roster refuses, no side effects | real HTTP + filesystem inspection | pass | `test_tier3_only_roster_refuses_400_naming_both_fixes_no_side_effects` — read in full, asserts no `_leads_root()`, no tmux session, no `.teams` dir |
| 4 | CLI `--lead` still accepts tier-3 explicitly, unaffected by the route's refusal | real subprocess CLI + real tier-3 stand-in | pass | `CliTierThreeLeadStillAllowedRegressionTests` — confirmed `_cli_team_start()` is untouched by this diff, never calls `default_team_composition()`; test runs a REAL tier-3 lead to `status: finished`, not just a non-crash check |
| 5 | Ollama-configured default selects Ollama as lead | real HTTP | pass | read directly, matches `default_team_composition()`'s priority order |
| 6 | Unknown project → 404, no launch attempted | real HTTP | pass | read directly |
| 7 | Two real near-simultaneous starts, exactly one succeeds, winner unaffected | real HTTP, two threads + `threading.Barrier` | pass, judgment on collision point independently verified (see below) | `TeamStartEndpointTests.test_two_near_simultaneous_starts_exactly_one_succeeds`; independently reproduced the race myself outside the test suite, 11 additional real-concurrency trials |
| 8 | `/team/stop` mid-delegate: prompt HTTP response, real SIGTERM, `run.json` → `stopped` | real HTTP + real slow subprocess | pass | `TeamStopEndpointTests.test_stop_mid_delegate_terminates_real_subprocess_and_records_stopped` (disclosed setup caveat, see below) |
| 9 | `/team/stop` between rounds: stops via the loop-top checkpoint alone | pure | pass | `TeamRunLoopCheckpointTests` |
| 10 | `/team/stop` idempotent for no-team/already-finished | real HTTP | pass | read directly |
| 11 | Service restart: `/team/stop` on a fresh process still tears down real resources | same-process simulation (developer) + **genuinely separate OS process (me, independently)** | pass | see "Requester's specific checks" #3 below |
| 12 | Service restart: `/status` shows truthful `running` until reap flips it to `error` | real HTTP, `TEAM_REAP_POLL_INTERVAL_SECONDS` forced to 0 | pass | `ServiceRestartSimulationTests.test_status_shows_running_truthfully_until_reap_runs_then_flips_to_error` |
| 13 | Legitimate concurrent CLI `team-resume` self-corrects after one reap pass | real, separate subprocess | pass | `OrphanCheckSelfCorrectsForLiveCliRunTests` — read in full |
| 14 | `cancel_event`-omitted behavior byte-for-byte unchanged | regression | pass | `test_teams_headless.py`/`test_teams_lead.py`/`test_teams_lifecycle.py` all pass; `test_teams_lead.py`/`test_teams_lifecycle.py` diffs are mechanical `**kwargs` additions only, confirmed via `git diff` |
| 15 | `install.sh` copies `teams.py` | block extraction against real source | pass | `InstallShTeamsPyCopyTests` (2 tests) |
| 16 | Full suite green, several runs; `app/teams.py` diff never changes an existing positional shape | regression | pass | see "Regression check" below |

## Regression check
`/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q`, run
twice independently this session: **671 passed** both times, no flake
observed in my own runs. `tests/test_teams_headless.py::RealTmuxHeadlessTests
::test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask`
(the disclosed pre-existing flake) confirmed passing in isolation, as
instructed, rather than treated as a regression. All four real frontend
suites run together: `test_team_frontend.js` 17/17, `test_deploy_frontend.js`
9/9, `test_singleton_toggle_frontend.js` 15/15, `test_upload_frontend.js`
8/8. `git diff -- app/teams.py | grep -E "^[+-]def "` independently
re-checked: every changed `def` is new or additive-keyword-only.

## Requester's specific checks

### 1. Cooperative cancellation — checkpoints correct; found a real, bounded gap the spec's own latency math doesn't account for

All three specified checkpoints (between rounds; after the lead's turn,
before its action executes; after a delegate returns, before its outcome is
recorded) are implemented exactly as specced, checked FIRST ahead of every
other branch at each point, and covered by both pure tests (fake
`_call_lead()`/`agent_run()`, exact `status`/history-entry shape asserted)
and real-tmux tests (`cancel_event.set()` fired from a second thread against
a real, deliberately slow/signal-ignoring subprocess, both the
TERM-succeeds and TERM-ignored-escalates-to-SIGKILL cases).

**Gap found, between the checkpoints, inside `_run_headless_session()`
itself**: its own startup phase — `while time.time() < deadline: pid =
_read_int_file(pid_path); ...; time.sleep(0.05)` (`app/teams.py:855-859`,
`deadline = time.time() + 5.0`) — never checks `cancel_event` at all. This
is a pre-existing characteristic of `_run_headless_session()`'s own
completion-detection loop (unchanged in shape by this diff, and the
*timeout* path has never checked this phase either — not a regression this
cycle introduces), but it means the real, computed worst-case latency from
"stop requested" to "process actually dead" for a subprocess-based
checkpoint (a tier-2/3 lead call, or a delegate call) is measurably higher
than the spec's own stated figure. Computed precisely from the actual
constants (`TEAM_HEADLESS_KILL_GRACE_SECONDS=10`,
`TEAM_HEADLESS_POLL_SECONDS=0.5`, plus the undocumented 5s pid-wait ceiling
and the `agent_run()`-level tmux `new-session` subprocess call before
`_run_headless_session()` is even entered):

```
worst case ≈ 5s (pid-wait ceiling, rarely hit in full -- a backgrounded
              process writing its own pid typically takes milliseconds)
           + 0.5s (poll cadence before the FIRST cancel_event check can fire)
           + 10s (TERM grace)
           + 10s (KILL grace)
           ≈ 25.5s
```
— not the spec's own stated "~20s worst case" (`2 × TEAM_HEADLESS_KILL_
GRACE_SECONDS`), which omits the pid-wait/poll-cadence contribution. In
practice this 5s figure is a ceiling almost never approached (a `python3
<script>` or real engine CLI backgrounding and writing its own pid takes
milliseconds, not seconds), so the PRACTICAL latency is close to the
spec's own ~20s either way — but the theoretical worst case is real and
not accounted for. Non-blocking (bounded, rarely material, pre-existing
shape) — logged as a follow-up, not a finding requiring a fix.

The tier-1 (Ollama) latency figure IS accurately disclosed: independently
verified `TEAM_LLM_TIMEOUT_SECONDS=120` × `(TEAM_LLM_TRANSPORT_RETRY_
BUDGET+1)=3` = up to 360s (6 minutes) in the genuine worst case, matching
exactly what the spec's own "Edge cases" text computes (it states the
multiplication explicitly, not just a rounded figure) — no discrepancy
there.

### 2. Two concurrent starts — developer's call is CORRECT, independently re-verified beyond the shipped test's own single-member case

Independently reproduced the real concurrency race myself, outside the test
suite, with **2 and 3 members** (the shipped test uses only 1, which cannot
exercise a losing thread's own partial-worktree rollback under real
concurrent conditions at all — see below for why that gap doesn't actually
matter). 11 total real-concurrency trials (5 with 2 members, uninstrumented;
6 with 2-3 members, instrumented with call tracing):

- **Confirmed a genuine TOCTOU inside `_create_worktree()` itself**
  (between its own `os.path.exists(path)` pre-check and the actual `git
  worktree add` command): in several trials, the loser's pre-check saw
  `False` (path didn't exist yet), but by the time its own `git worktree
  add` executed, the winner had already created it — git itself then
  refuses cleanly (`fatal: '<path>' already exists`), correctly classified
  as `ok: False` by `_create_worktree()`. This is a **second** collision
  point beyond both the session-name check the spec's prose describes and
  the pre-check message the developer's own finding names — not itself a
  new defect (degrades exactly as safely as the other two), but worth
  naming precisely since "which of three checks fires" is even less
  deterministic than the developer's own finding states.
- **In all 11 trials, across 1-, 2-, and 3-member compositions, the losing
  thread ALWAYS lost at the FIRST contested member, never accumulating any
  partial worktree state of its own to roll back.** This is not a
  coincidence of my sample size — it's structural: `launch_team()`
  processes members strictly in order, so the only thread that can ever get
  past member 1 is the one that already won member 1's own git-level race;
  every other thread drops out immediately, with an empty `worktrees` dict,
  at the very first contested resource. **The "loser rolls back its own
  partial worktrees under real 2-or-3-way concurrent racing" scenario the
  coordinator asked about is therefore not reachable via natural
  concurrent-launch scheduling at all** — it would require a third,
  unrelated failure (e.g. a genuinely broken git operation on a later
  member) layered on top of the race, which is exactly what part 1's own
  *non-concurrent, mocked* `LaunchTeamRollbackOrderingTests` already covers.
  The shipped test's use of only 1 member is therefore not a meaningful
  coverage gap for this specific property, even though it looks narrower
  than the acceptance criterion's own general wording suggests.
- **The winner's final state was fully consistent in every trial**: both/
  all worktrees present, all belonging to the single winning `run_id`, real
  `_create_team_session()` call completing cleanly afterward.
- **Disclosed for transparency, not as a confirmed finding**: one early,
  UNinstrumented trial produced an anomalous result (`wtA` missing, `wtB`
  present, with `ok_count == 1`) that I could not reproduce across 9
  subsequent, more rigorously instrumented trials with full call tracing —
  every traced trial showed fully consistent behavior. I judge this was
  very likely a transient artifact of my own ad hoc test harness (not the
  reviewed code), but flag it rather than silently drop it, since I could
  not positively identify the cause.

**Conclusion: the developer's call is correct.** Broadening the test's own
assertion to accept either collision message (rather than changing
production code) is the right fix, because the underlying safety property —
exactly one launch succeeds, the loser leaves the winner's resources
untouched, and the loser never leaves anything of its own behind — holds
across every ordering I could produce, including the two the developer's
own single-member test cannot distinguish.

### 3. Stop for a run this process didn't launch, and service restart — verified true, not incidental, via a genuinely separate OS process

The developer's own `ServiceRestartSimulationTests` uses a same-process
technique (`appmod._team_threads.clear()`), explicitly disclosed as a
deliberate substitute for a real process restart. Per the coordinator's
ask, I went further: reproduced the entire scenario with **two genuinely
separate OS processes**, real `sudo -u $RUN_USER tmux` (not the `["tmux"]`
test monkeypatch), real git:

1. Process 1: called `launch_team()` directly against a real scratch repo,
   printed the real `run_id`/session, then **exited completely**.
2. Confirmed the real tmux session survived process 1's exit (`tmux
   has-session` succeeds — independent of the dead `app.py` process, as
   the architecture claims).
3. Process 2: a **brand-new Python interpreter**, own module state,
   `appmod._team_threads == {}` confirmed empty by construction (not
   cleared — never populated), started a real `ThreadingHTTPServer`, did a
   real `/login` + `/projects/proj/team/stop` HTTP round trip with a real
   TOTP code.

Result: `{"ok": true, "session_removed": true, "worktrees": {"helper":
"removed"}}` — the real tmux session was gone (`tmux has-session` → `can't
find session`), the real worktree directory was removed, and `run.json`'s
`status` correctly read `"stopped"` afterward. **This independently confirms
the restart-safety claim is genuinely true**, not incidentally working
because a thread happened to be tracked — `/team/stop`'s own route code
never even touches `_team_threads` except as an optional, best-effort
`cancel_event` signal; the actual teardown is 100% re-derived from
`run.json` via `latest_run_for_project()`, exactly as designed.

### 4. The thread itself — found a real check-then-act race on `_team_threads`, same defect class as three prior 6d findings

`_run_team_in_background()`'s own `finally` block:
```python
entry = _team_threads.get(name)
if entry is not None and entry.get("run_id") == run_id:
    _team_threads.pop(name, None)
```
The docstring claims this "guards against a subsequent stop-then-relaunch
having already replaced the entry with a NEWER run's thread before this old
thread's own cleanup runs" — **this claim does not fully hold**. The check
(line 1) validates against a value already read into `entry`; the pop
(line 3) removes **whatever is currently in the dict by key**, not
specifically the validated `entry` object. If a new `/team/start` for the
same project writes a fresh entry into `_team_threads[name]` in the (very
narrow, but real) window between the read and the pop, the old thread's own
pop destroys the NEW run's entry instead of correctly doing nothing.

**Reproduced directly** (same technique this project's own tests already
use to make a narrow race provable — an artificially widened window
standing in for real scheduler preemption, not a claim that the window is
normally this wide):
```python
def simulate_cleanup(name, run_id, delay):
    entry = appmod._team_threads.get(name)
    if entry is not None and entry.get("run_id") == run_id:
        time.sleep(delay)          # widened window
        appmod._team_threads.pop(name, None)
# ... old thread starts cleanup, is "preempted" mid-window;
# a new /team/start writes a fresh entry for the same project;
# old thread's cleanup resumes and pops it.
# Result: appmod._team_threads.get(name) is None -- the NEW run's entry destroyed.
```

**Consequence, traced through, and why this is should-fix not must-fix**:
neither `/team/stop` nor `_team_reap_if_due()`'s orphan check depend on
`_team_threads` for *correctness* the way `_run_team_in_background()`'s own
cleanup mistakenly assumes ownership stability does — both are designed to
tolerate a missing/wrong entry:
- `/team/stop` still calls `stop_team(run["run_id"])` unconditionally
  (re-derived from `run.json`, per check #3 above) even with a wrongly-
  evicted entry — session/worktree teardown still happens correctly. The
  only loss is the `cancel_event` signal itself, so the new run's driving
  loop wouldn't be told to stop cooperatively — a regression to part 1's
  own already-disclosed "driving loop isn't interrupted" behavior for the
  affected run, not data loss or a crash.
- `_team_reap_if_due()`'s orphan check would find no matching entry for the
  (actually-alive) new run and incorrectly `mark_run_error()` it — but this
  is the exact same shape as the ALREADY-ACCEPTED, user-settled CLI-`team-
  resume` false-positive tradeoff (`docs/spec.md` "Open questions"): the
  run's own next `_persist()` call (every round, unconditional) overwrites
  the wrong status back to the truth. Self-correcting, bounded to one poll
  interval, same mechanism, just triggered by an internal race instead of a
  deliberate architectural gap — **but undisclosed as such**, since the
  spec's own "Open questions" only names the CLI case, not this one.
- No orphaned tmux session or worktree results either way — `stop_team()`'s
  own unconditional teardown isn't gated on `_team_threads` at all.

Given the consequence is bounded and self-correcting (no resource leak, no
permanent stuck state) rather than data loss, and the trigger requires a
stop-or-finish-then-immediate-relaunch for the *same* project landing in a
genuinely microsecond-scale window (implausible via ordinary human-paced UI
use; more plausible under future scripted/automated usage) — this is a
**should-fix**, calibrated the same way as the analogous round-2 finding in
part 1's own review. Recommended fix: a small `threading.Lock()` (mirroring
`_team_reap_lock`'s own existing precedent) guarding the read-check-pop
sequence in `_run_team_in_background()`'s cleanup, and ideally the
read-check-set in `/team/start`'s own route handler for full symmetry.

## Also (the four smaller asks)

### Tier-3 refusal boundary
Confirmed both halves hold and are genuinely pinned apart by distinct tests,
not just asserted. `default_team_composition()` is the *only* thing
`/team/start` calls; `_cli_team_start()` (6c's own, unmodified — confirmed
by reading it directly) never calls `default_team_composition()` at all and
accepts any tier via `--lead` unchanged.
`CliTierThreeLeadStillAllowedRegressionTests` runs a REAL tier-3 stand-in
through the CLI to `status: finished` (not just a non-crash check) using
the *same* tier-3-only engine set `TeamStartEndpointTests`' own web-route
test proves gets refused — a genuine contrast pair, not two independent
assertions that happen to agree.

### `doTeamStart()`/`doDeploy()` deviation
Verified directly: `doDeploy()` (`app/app.py:2210`) calls `toggle('deploy',
name, true, null)` — it does **not** make a direct `fetch()` call. The
spec's own prose describing it as "direct-`fetch()`-plus-inline-result-slot"
does not match the already-shipped code. The developer's claim is
accurate, and `doTeamStart()`'s decision to follow the *actual* `doDeploy()`
shape (via `toggle()`/`actionBody()`'s new `team-start` branch) rather than
the spec's mistaken description of it is the right call — it reuses 100%
of the existing TOTP-retry machinery instead of reimplementing it.

### `teamTaskText` staleness
Verified the premise directly: `refresh()` (`app/app.py:1935`,
`document.getElementById('rows').innerHTML = html`) does a full,
non-diffing blanket replacement of every row's HTML on every 4-second poll
— without a client-side cache, an in-progress, uncommitted textarea value
would genuinely be wiped every 4 seconds while the operator is still
typing, confirming the developer's "unusable without it" claim is true, not
overstated. Traced the specific staleness scenario the code's own comment
calls out (a TOTP-retry re-submission after a 428) — `actionBody()` reads
fresh from either the still-live DOM element or the `teamTaskText[]` mirror
on every call, not a stale closure-captured value, so no staleness bug
introduced. Matches `engineChoice`'s already-established, already-reviewed
pattern exactly, not a new mechanism.

### Standing constraints
All five hold, independently re-checked against this round's diff:
- No new sudoers/privileged path: `git diff -- app/app.py app/teams.py |
  grep -iE "sudo|subprocess\."` on added lines — every non-`TMUX` call is
  one of `_validate_project_for_team()`'s pre-existing read-only SVC_USER
  `git` checks (part 1, untouched this round).
- Grounding read-only guards untouched: `git diff --stat -- tests/test_
  teams_grounding.py` empty.
- No `realpath()` fallback reintroduced: no matches in this round's diff.
- Deploy stays manual-click-only: every "deploy" match in the diff is
  `.team-btn` reusing `.deploy-btn`'s CSS declaration or `doTeamStart()`
  reusing `doDeploy()`'s JS plumbing — reuse of existing patterns, not a
  change to deploy's own dispatch logic.
- `git worktree remove` never gains `--force`: only match in the diff is
  the unrelated `_force_ask_user` identifier.

### WCAG contrast — recomputed from the actual shipped hex values, not `docs/design.md`'s own stated numbers

Per my role's own standard (recompute, don't trust a stated ratio), and a
genuinely important catch here: **`docs/design.md`'s entire contrast
analysis is computed against the wrong background AND colors that were
never actually shipped.** The design doc assumes a *white* background and
hex values like `#0066CC`/`#FF9800`/`#4CAF50`/`#D32F2F` ("or similar"). The
actual page is a **dark theme** — `body { background: #111; ... }`
(`app/app.py:1577`), and each row renders on `.row { background: #1c1c1c;
... }` (`:1581`) — and the actually-shipped status colors are entirely
different: `#4da6ff`/`#ffb648`/`#34c759`/`#ff6b6b` (`:1652-1655`), matching
colors *already used elsewhere on this exact page* (the existing blue pill/
badge color, the existing green "on"/success color, the existing
`.deploy-msg.error` red) rather than the design doc's own suggested values.

Recomputed WCAG relative-luminance contrast myself, from the literal hex
values, against the real `#1c1c1c` row background:

| Element | Hex | Contrast vs. `#1c1c1c` | AA (4.5:1)? |
|---|---|---|---|
| `.status-running` | `#4da6ff` | 6.67:1 | pass |
| `.status-blocked` | `#ffb648` | 9.77:1 | pass |
| `.status-finished` | `#34c759` | 7.68:1 | pass |
| `.status-error` / `.team-msg.error` | `#ff6b6b` | 6.14:1 | pass |
| `.team-msg.success` | `#34c759` | 7.68:1 | pass |
| `.team-sub` (the "waiting for input" subtitle, not analyzed by design.md at all) | `#888888` | 4.81:1 | pass (barely; fails AAA) |

**All pass AA comfortably against the real background.** But design.md's
own numbers, even on its own stated (wrong) assumptions, don't check out
either — recomputing its own assumed colors against its own assumed white
background: running `#0066CC` → 5.57:1 (design.md claimed 8.6:1/AAA — it
does not reach AAA, only AA); blocked `#FF9800` → 2.16:1 (design.md claimed
4.8:1/AA-passing — this actually **fails** AA outright); finished `#4CAF50`
→ 2.78:1 (design.md claimed 5.2:1/AA-passing — this also **fails** AA).
Three of design.md's five stated ratios don't match its own stated
assumptions, independent of the fact that none of those assumptions match
what was actually built. **Net assessment**: the shipped UI is genuinely
accessible (verified from the real values, not asserted), but `docs/
design.md`'s own accessibility section should not be trusted as accurate
documentation — it's describing a page that doesn't exist. Non-blocking
(the actual outcome is fine), but worth a correction pass on the design doc
itself so a future reader doesn't inherit the wrong numbers.

## Constraints re-checked
See "Standing constraints" above — all five hold, independently
re-verified against this round's own diff.

## Spec coverage
All 16 acceptance criteria (my own numbering, collapsing adjacent bullets
covered by one test) have real implementation and test coverage — see the
Test cases table. No acceptance criterion is uncovered. Both findings below
are in code paths no acceptance criterion specifically targets (the
pid-wait latency gap inside pre-existing completion-detection code; the
`_team_threads` check-then-act race) — found by testing past the spec's
own enumerated cases, the same pattern that found every prior defect in
this area of the story.

## Findings (most severe first)

**Finding 1 below is RESOLVED as of the round-2 re-review — see "Round 2
re-review" below, which also covers a second, more severe defect the
developer found while re-verifying this one.**

### 1. [RESOLVED round 2] `_run_team_in_background()`'s cleanup has a check-then-act race on `_team_threads` — should-fix
- File: `app/app.py:1340-1343` (`_run_team_in_background()`'s `finally`
  block)
- Issue: `entry = _team_threads.get(name)` then a conditional
  `_team_threads.pop(name, None)` — the pop removes whatever is CURRENTLY
  keyed there, not specifically the validated `entry`. A new `/team/start`
  for the same project writing its own fresh entry into the narrow window
  between the read and the pop gets silently destroyed by the old thread's
  stale-read cleanup. Reproduced directly with an artificially widened
  window (the standard technique this project's own tests already use to
  make a narrow race provable).
- Failure scenario: a team finishes/is stopped and a new team is launched
  for the *same* project within a genuinely microsecond-scale window (real
  under scripted/automated usage; implausible via ordinary human clicking).
  Consequence is bounded, not data loss: `/team/stop` still tears down
  session/worktrees correctly (re-derived from `run.json`, unaffected by
  the missing entry) but loses the `cancel_event` signal for the new run;
  `_team_reap_if_due()`'s orphan check may transiently, incorrectly flag
  the new (actually-alive) run as `"error"`, self-correcting on that run's
  own next `_persist()` call — the same mechanism, but a materially
  different (undisclosed) trigger, as the already-accepted CLI-`team-
  resume` false-positive tradeoff `docs/spec.md`'s own "Open questions"
  names.
- Recommended fix: a small `threading.Lock()` (mirroring `_team_reap_
  lock`'s own existing precedent in this exact file) guarding the
  read-check-pop sequence in the cleanup path, and ideally the
  read-check-set in `/team/start`'s own route handler.
- Severity: should-fix, not must-fix — same calibration as the analogous
  round-2 finding in part 1's own review (real, structurally confirmed via
  a widened-window repro, same "check-then-act on shared state, no lock"
  defect class as three prior findings in this exact area of the story, but
  a bounded, self-correcting consequence rather than data loss or a
  permanent stuck state).

## Follow-ups (non-blocking)
- Cooperative-cancellation worst-case latency for a subprocess-based
  checkpoint is ≈25.5s computed precisely from the real constants, not the
  spec's own stated ~20s (the difference is `_run_headless_session()`'s own
  pre-existing, uncancellable 5s pid-wait ceiling plus one poll interval,
  both omitted from the spec's math) — bounded, rarely material in
  practice (a real process backgrounding and writing its own pid typically
  takes milliseconds), not a regression this cycle introduces.
- `docs/design.md`'s WCAG contrast section should be corrected — it
  analyzes an assumed light theme/color set that doesn't match the actual
  shipped dark-theme CSS, and its own arithmetic is wrong in 3 of 5 cases
  even relative to its own assumptions. The actual shipped colors pass AA
  comfortably (verified above), so this is a documentation-accuracy issue,
  not a shipped-code defect.
- A second, previously-unnamed collision point exists inside
  `_create_worktree()`'s own check-then-act (`os.path.exists()` then `git
  worktree add`) beyond the two the developer's own finding names — degrades
  exactly as safely (git's own clean refusal), not a new risk, but worth
  folding into the developer's own finding's own description for
  completeness.

## Overall verdict (round 1 — superseded by "Round 2 re-review" below)
Changes requested — one should-fix (Finding #1: the `_team_threads`
check-then-act race). Given this project's own established calibration
(should-fix items don't block on their own), I would normally mark this
approved-with-follow-ups, but I'm deliberately flagging it as changes-
requested instead because it is the *exact* pattern the coordinator asked
me to hunt for specifically because three prior defects in this story
shared it, the fix is small and well-scoped (a single `Lock()`, mirroring
an already-existing precedent in the same file), and closing it now avoids
carrying a fourth instance of the same defect class into part 2b's own
review as unfinished business. Everything else in this cycle checked out
under independent verification: cancellation checkpoints are correct (with
one disclosed, bounded, non-blocking latency gap); the two-concurrent-
starts safety property holds across every ordering I could produce,
including ones the shipped test can't distinguish, and the developer's own
"broaden the assertion, don't change production code" call is correct;
restart-safety and stop-for-a-run-this-process-didn't-launch are genuinely
true, confirmed via a real, separate OS process, not just the developer's
own same-process simulation; the tier-3 CLI/route boundary is correctly
implemented and pinned apart by a genuine contrast pair of tests; both
disclosed frontend deviations are justified and correctly implemented; all
five standing constraints hold; the full suite and all four frontend
suites are green; and the one place `docs/implementation.md` could have
overclaimed (WCAG contrast) is actually `docs/design.md`'s own error, not
the implementation's, and the shipped result is fine regardless.

---

## Round 2 re-review

Focused re-review of the must-fix's own fix, plus a second, more severe
defect the developer found while re-verifying it — independently re-derived
from scratch per the coordinator's own explicit instruction, not treated as
settled just because the surrounding reasoning was reviewed once already
(that reasoning was reviewed and endorsed by both the coordinator and me in
part 1's own review, and it was wrong).

### Finding #1's fix — confirmed closed

`_team_threads_lock` + `_team_threads_set()`/`_team_threads_get()`/
`_team_threads_pop_if_owned()` — the last making the read-check-pop one
atomic operation under the lock, not a narrowed window. Independently
audited every `_team_threads` access in `app/app.py` myself (`grep -n
"_team_threads\b"`): all raw dict operations (`[name] = `, `.get()`,
`.pop()`) are inside the three helper functions' own bodies; every other
call site (`_run_team_in_background()`'s cleanup, `/team/start`,
`/team/stop`, `_team_reap_if_due()`) goes through one of the three. Traced
the lock's own mutual-exclusion mechanics directly against
`TeamThreadsLockTests`' own `_SlowGetDict` technique (a `.get()` that sleeps
while still holding `_team_threads_lock`, since the sleep happens inside the
helper's own `with` block) — confirmed a concurrent `_team_threads_set()`
call genuinely blocks on the same lock rather than interleaving, so the
fix is structurally atomic, not merely fast enough in practice. Ran the
actual tests myself: `TeamThreadsLockTests` (2/2 pass), including its own
"does the widening technique actually catch the naive pre-fix pattern"
sanity check (it does — confirms the clean pass on the real fix isn't
because the technique is too weak to catch anything).

### The new defect — independently re-derived from scratch, not re-trusted

Reproduced this myself with my own script (not the developer's), against
both the pre-fix and post-fix shapes, per the coordinator's explicit
instruction not to treat the surrounding reasoning as settled.

**1. Confirmed the fix closes it, with my own reproduction.** Reconstructed
the OLD (pre-fix) `_create_team_session()` shape (raw, unconditional
`kill-session` on the failure-cleanup branch) standalone, and ran 20 real,
`Barrier`-synchronized concurrent trials against it:
```
OLD (pre-fix) shape: a legitimately-created (chain_rc=0) session was destroyed in 20/20 trials
```
(My first attempt at this undercounted — I initially checked `sum(oks)==1
and not alive`, which missed the specific interleaving where the WINNER's
own post-`new-session` `tmux_has()` recheck also observes the kill and
reports `ok:False` too, producing a `0-winners` trial rather than a
`1-stale-winner` trial. Both are the identical underlying defect — a
legitimately-created session destroyed by an unrelated caller's cleanus —
just differing in which side of the winner's own post-check the kill
happens to land. Corrected the detection to `any(chain_rc==0) and not
alive`, which is the property that actually matters, and reran.) Then ran
the exact same race 30 times against the REAL, current `_create_team_
session()`:
```
FIXED code: exactly-one-winner-survives in 30/30 trials
FIXED code: winner destroyed in 0/30 trials
```
Matches the developer's own reported 20/20 and 0/30 exactly, from an
independent script, not the developer's own reproduction.

**2. Is the winner's session guaranteed to be stamped by the time the
loser's cleanup runs?** Checked this directly rather than accepting the
"should mean" reasoning, given the coordinator's own explicit warning that
this exact style of reasoning produced the defect. Instrumented BOTH
racers to check `_team_session_run_id(session)` (the stamp) immediately
upon losing to a genuine tmux "duplicate session" error (`chain_rc==1`,
i.e. the loser's own `new-session` call itself failed, proving the winner's
session already existed server-side), across 25 real trials:
```
Loser observed winner's session UNSTAMPED immediately after losing: 0/25 trials
```
**Answer: yes, confirmed empirically, not just analytically.** The
mechanism is structural, not probabilistic: a loser's own `new-session`
call can only fail with "duplicate session" *after* the winner's session
already exists on the tmux **server** — and because the winner's own
creation+stamp is one atomic `;`-chained client request (verified directly
in part 1's own review: no other client's command can be processed by the
tmux server mid-chain), there is no server state in which the winner's
session is visible to any other client before its own stamp has already
been applied. The only way an *unstamped* session becomes visible to
another client at all is the OTHER documented case — this call's own chain
succeeding at `new-session` but failing at a later link — which is, by the
same construction, always genuinely orphaned (not a "winner" in the race
sense), and `_kill_team_session_if_owned()` treating an unstamped session
as safe to kill is the *correct* behavior for reclaiming exactly that case.
This does not reopen the hole; it's the intended, narrower use of the same
"unstamped = safe to kill" rule, now scoped correctly by construction
rather than by an unverified assumption.

**3. Other unconditional/ownership-blind kills in `app/teams.py`?**
Enumerated every `"kill-session"` call site (`grep -n '"kill-session"'`)
and read each one's own ownership context:
- `:830` (`_sweep_stale_runs()`), `:930`/`:940` (`_run_headless_session()`'s
  own escalation ladder), `:1087` (`agent_run()`'s own `finally`) — all
  operate on `switchboard-headless-<run_id>` sessions, where `run_id` is a
  fresh `timestamp+secrets.token_hex(6)` generated once per `agent_run()`
  call. These names are **run-scoped by construction**, not project-scoped
  — the exact asymmetry that made `team-<project>` vulnerable (a reusable
  name a *different* run can legitimately claim) structurally cannot arise
  here, since no two calls ever share a name to collide on in the first
  place. Confirmed each of these kills only ever targets a session the
  calling function itself created moments earlier, never a name another
  concurrent caller could also be targeting.
- `:2896`/`:2903` (`_run_run_user_command()`) — same reasoning,
  `switchboard-worktree-op-<op_id>`, equally uniquely named per call.
- `:3080` (inside `_kill_team_session_if_owned()` itself) — the one
  legitimate raw kill, and it's *inside* the ownership-checking function,
  reached only after the stamp comparison already passed.
- Confirmed via `grep -n "_kill_team_session_if_owned("` that all three
  real call sites touching the reusable-name `team-<project>` resource
  (`stop_team()`, `sweep_dead_teams()`, and now `_create_team_session()`'s
  own failure-cleanup) route through it. **No other unconditional or
  ownership-blind teardown remains for the one resource in this file whose
  name is reusable across different runs** — every other `kill-session`
  call site operates on a resource that is safe by construction of its own
  naming scheme, not because of an ownership check.

### Broadened test assertions — judged correct

`SessionCreationRaceRealTmuxTests` (new, 15 real trials per run) asserts
the property that matters — exactly one winner, the session survives, and
is stamped with the *correct* winner's `run_id` — and, for the loser's own
error, accepts either of the two legitimate shapes rather than pinning one
specific string, with the test's own comment explaining why (both are real,
both fire under real scheduling, and pinning one is exactly what let the
underlying defect go unnoticed for three review rounds). `test_team_
routes.py`'s own `test_two_near_simultaneous_starts_exactly_one_succeeds`
was broadened the same way (`assertTrue(loser[1].get("error"), ...)` instead
of pinning specific substrings) and gained a new assertion checking the
winner's session is stamped with its own correct `run_id` — a net increase
in rigor, not just a loosening. Read both broadened assertions directly:
correct in both cases — they now pin the property that actually matters
rather than one incidental message shape among several legitimate ones.

Checked for **other** tests in this cycle pinning a specific string where a
property assertion would be more appropriate: grepped every `assertIn`/
`assertEqual` against an `"error"` field across `test_teams_lifecycle.py`/
`test_team_routes.py`. Every other instance is a **deterministic,
single-threaded** scenario (a monkeypatched engine failure, a specific git
precondition like "detached HEAD" or "not a git repository") where exactly
one message is the only possible correct outcome — none of them test a real
concurrent race with multiple legitimate collision shapes the way the two
broadened ones do. No other gap of this kind found.

### `docs/design.md`'s rewritten contrast section — spot-checked, correct

The rewritten figures (`#4da6ff`/`#ffb648`/`#34c759`/`#ff6b6b` against the
real `#1c1c1c` row background: 6.67:1/9.77:1/7.68:1/6.14:1) match exactly
what I independently computed myself in the prior review round from the
literal shipped hex values — confirmed again by re-reading the actual CSS
(`app/app.py:1652-1655`) and the claimed `a { color: #4da6ff; }` existing
link-blue token (`:1716`, confirmed present). One gap carried over,
unaddressed by this round's rewrite: `.team-sub` (the "Lead is waiting for
input" subtitle, `#888` on `#1c1c1c`) is still not analyzed anywhere in
`docs/design.md`, even in the corrected pass — it passes AA at 4.81:1 (per
my own prior computation, unchanged this round) but only barely, and fails
AAA. Non-blocking (it does pass, and I already flagged this exact gap last
round without it blocking approval then either) — logged again as a
lingering, minor documentation gap.

### `docs/implementation.md` — accurate, no overclaiming

Cross-checked "Finding #2" (`_create_team_session()`'s failure-cleanup)
against my own independent reproduction: the 20/20-pre-fix and 0/30-post-fix
figures match exactly. The severity framing ("worse than the `_team_
threads` must-fix... this one directly falsifies an explicit acceptance
criterion... under real, not-especially-rare timing") is accurate and not
inflated — matches my own independent assessment that this is must-fix
(real, unrecoverable-in-the-moment destruction of a live session under
realistic concurrent usage, not a bounded/self-correcting consequence the
way the `_team_threads` finding was). The document correctly distinguishes
the two findings' own severity calibration explicitly in "Known
limitations" rather than letting a reader conflate them. No overclaiming
found anywhere in either finding's writeup.

## Overall verdict
**Approved.** Both the must-fix I raised last round and the new, more
severe defect the developer found while re-verifying it are genuinely
closed, independently re-derived and reproduced by me from scratch rather
than accepted on the strength of prior review or the developer's own
report: the `_team_threads` race is now structurally atomic (traced the
lock's own mutual-exclusion mechanics, not just read the helper names); the
session-creation race is confirmed fixed with my own 20/20-pre-fix,
0/30-post-fix reproduction; the "winner is always already stamped by the
time a loser's cleanup runs" property is confirmed both analytically and
empirically (0/25 counterexamples found); every other `kill-session` call
site in the file was individually checked and found safe by construction
of its own run-scoped naming, not merely assumed safe; both broadened test
assertions are judged correct, with no similar gap found elsewhere in this
cycle's own tests; and `docs/design.md`'s rewritten contrast numbers check
out against my own independent computation, with one small, already-
disclosed, non-blocking documentation gap (`.team-sub`) still unaddressed.
Full suite reconfirmed at 674 passed.

# Test & Review: `install.sh --with-ollama` — link an existing remote Ollama (sub-spec 6d, part 2b of 2)

New sub-spec, new cycle. Baseline before this cycle: 674 passing. After
this cycle's diff: 690 passed (pytest) + 17/9/15/8 passed (the four Node
suites, untouched by this cycle). Uncommitted working tree: `install.sh`
(usage block, flag plumbing, one new block), `tests/test_install_ollama.py`
(new, 16 tests), `tests/test_deploy_target.py` (one-line marker fix).

## Scope

Covers `docs/spec.md` (6d part 2b)'s full acceptance-criteria list (usage/
off-by-default, reachable-with-model write, unreachable-endpoint skip,
reachable-but-absent-model skip+list, bounded stall, substring safety,
trailing-slash normalisation, idempotent re-run, nothing-installed-locally,
full-suite regression), both stated edge cases (HTML/captive-portal body,
empty model list, URL missing `/v1` not silently rewritten), and the
test-isolation requirement (ephemeral ports, no fixed ports, no writes
outside a per-test fixture). Also independently probed beyond the
developer's own 16 tests: shell/JSON metacharacters and flag-shaped model
names, HTTP-error bucketing, `set -euo pipefail` abort-vs-skip-only-this-
block behavior under a real induced failure, and the fresh-install (no
prior key at all) idempotence case.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | `--with-ollama` in usage block, `WITH_OLLAMA=0` default, flag wired up | automated | pass | `test_usage_block_documents_the_flag`, `test_flag_defaults_to_off_in_flag_plumbing` |
| 2 | Without the flag, no `TEAM_LLM_*` written, no block output | automated | pass | `test_without_the_flag_writes_no_team_llm_keys` |
| 3 | Reachable + model present → both keys written with exactly the supplied values | automated, real stub HTTP server (ephemeral port) | pass | `test_reachable_with_model_writes_both_keys_exactly` |
| 4 | Unreachable endpoint → neither key written, run still succeeds (rc=0), file inspected not just stdout | automated | pass | `test_unreachable_endpoint_writes_nothing_run_still_succeeds` |
| 5 | Reachable, model absent → neither key written, available ids listed | automated | pass | `test_reachable_but_model_absent_lists_available_ids` |
| 6 | Reachable, empty model list → "no models available", not silently treated as valid | automated | pass | `test_reachable_with_empty_model_list_says_none_available` |
| 7 | Bounded: stalling endpoint doesn't hang the installer | automated, real stub that sleeps 60s, `curl --max-time 10` | pass | `test_stalling_endpoint_is_bounded_does_not_hang` (elapsed < 20s asserted) |
| 8 | Substring safety: `qwen3:8` rejected against a stub advertising only `qwen3:8b` | automated | pass | `test_substring_model_name_is_rejected_not_accepted` |
| 9 | Trailing slash normalised, no `//models`, request path asserted directly | automated | pass | `test_trailing_slash_validates_and_writes_no_double_slash` |
| 10 | URL missing `/v1` validated as given, not silently rewritten | automated | pass | `test_url_without_v1_is_validated_as_given_not_silently_rewritten` |
| 11 | Idempotent re-run: blank leaves values untouched; changed model updates only `TEAM_LLM_MODEL`; same-answer re-run is a byte-for-byte no-op | automated, stub kept running across both prompts | pass | `test_blank_answers_leave_existing_values_untouched`, `test_changed_model_updates_only_team_llm_model`, `test_rerun_with_same_answers_is_a_noop` |
| 12 | Nothing installed locally: no `apt-get`/`docker`/`systemctl`/`ollama pull`/`ollama run`/`useradd` anywhere in the block | automated, literal source scan | pass | `test_block_issues_no_local_install_command` |
| 13 | HTML/captive-portal 200 response treated as unreachable-for-this-purpose, not a valid empty list | automated | pass | `test_html_response_treated_as_unreachable_for_this_purpose` |
| 14 | Full suite green, four Node suites green | automated | pass | see "Regression check" below |
| 15 | (probe, not developer's own test) Model name shaped like a CLI flag (`-x`, `-evil-url`) doesn't get misinterpreted by `python3 -c`/`curl` | manual, reproduced live | pass | see "Independent probing beyond the developer's own tests" below |
| 16 | (probe) Model name with shell metacharacters (`` ` ``, `$(...)`, `"`) never executes, is written/read back literally | manual, reproduced live | pass | see below — confirmed no command injection, `/tmp/pwned_by_reviewer` never created |
| 17 | (probe) HTTP 500 from a reachable endpoint lands in outcome-1 ("unreachable"), with a message that explicitly names "HTTP error" as one of the three folded sub-cases, not a misleadingly bare "unreachable" | manual, real stub returning 500 | pass | see below |
| 18 | (probe) Fresh install, no prior `TEAM_LLM_*` key at all (only the commented-out example lines from `config/switchboard.env.example`) | manual, reproduced live | pass | see below — `get_env` correctly doesn't match `#TEAM_LLM_BASE_URL=...`, falls back to the spec's literal default, comment lines left untouched |
| 19 | (probe) `set -euo pipefail` — can any path abort the whole installer instead of skipping only its own block? | manual, reproduced live | **fail — see Finding #1** | a re-run (upsert path) with a model/URL value containing a literal `|` aborts the whole run with `sed: -e expression #1, char 44: unknown option to 's'`, rc=1 |

## Independent probing beyond the developer's own tests

**Metacharacter/flag-shaped model names (test-case 15/16 above).** Ran the
extracted block directly (not through `unittest`, via a standalone script
using the test file's own harness functions) with:
- `-x` and `-evil-url` as the model name / base URL respectively — both
  pass through correctly (`python3 -c "$SCRIPT" "$MODEL"` receives `-x` as
  `sys.argv[1]`, never as a flag to `python3` itself, since `-c` already
  consumed the flag position; curl on a `-`-prefixed URL just fails
  cleanly as "could not reach", no hang, no abort).
- `weird"name`` `` `$(touch /tmp/pwned_by_reviewer)` `` `` as the model
  name (JSON-served by the stub, so it flows all the way through
  `curl → python3 json.loads → case → set_env`): written to
  `TEAM_LLM_MODEL` byte-for-byte, `/tmp/pwned_by_reviewer` never created —
  confirmed no command injection anywhere in this path. The model name
  reaches `python3` as `sys.argv[1]` (never interpolated into shell text)
  and reaches `set_env`'s `printf`/`sed` as a quoted shell variable, not as
  literal script text.

**HTTP-error bucketing (test-case 17).** Stood up a real stub returning
HTTP 500 with a JSON error body. `curl -fsS` fails closed (the `-f` flag
suppresses the error body), `OLLAMA_MODELS_JSON` ends up empty, and the
block prints "Could not reach ... (unreachable, no response, or an HTTP
error) — writing nothing." — this message explicitly names all three
sub-cases the spec's own outcome 1 folds together
("unreachable / non-JSON / HTTP error"), so a reachable-but-erroring host
is not misleadingly reported as simply "down". This matches the spec's own
explicit instruction to fold these three into one outcome with one
message — not a defect.

**Fresh-install idempotence (test-case 18).** The developer's own 16 tests
all start from an empty `switchboard.env` (`open(env_file, "w").close()`),
which is not quite the real first-run shape — a real fresh install copies
`config/switchboard.env.example` into place first (`install.sh:235`),
which already has `#TEAM_LLM_BASE_URL=...`/`#TEAM_LLM_MODEL=...` commented
out (confirmed present at `config/switchboard.env.example:331-332`).
Reproduced that exact shape by hand: `get_env` correctly does not match
the commented-out lines (its `grep "^${key}="` anchor requires the key at
column 1, and `#TEAM_LLM_BASE_URL=...` starts with `#`), so the prompt
falls back to the spec's own literal default
(`http://127.0.0.1:11434/v1`), that default is (correctly) unreachable in
the test environment, nothing is written, and the commented-out example
lines are left completely untouched. Matches the developer's own claimed
behavior for this case.

## Finding — should-fix, not must-fix

### 1. A re-run with a model/base-URL value containing `|` aborts the entire installer, violating the "skip only this block" requirement — pre-existing in the shared `set_env()` helper, not newly introduced by this diff

`set_env()` (`install.sh:112-118`, unchanged by this cycle) upserts via
`sed -i "s|^${key}=.*|${key}=${val}|" "$file"` when the key already
exists. `$val` (the operator's prompt answer) is interpolated into the
`sed` expression completely unescaped, using `|` as the delimiter. Two
distinct, demonstrated symptoms, both requiring the **upsert** path (the
key must already exist — i.e. this is a re-run, not a first run):

- **A value containing a literal `|`** breaks the `sed` expression's own
  syntax outright. Reproduced live: after one successful run writes
  `TEAM_LLM_MODEL=normal-model`, a second run supplying the model name
  `weird|model` fails with `sed: -e expression #1, char 44: unknown
  option to 's'` and **the whole script exits 1** — under this harness's
  `set -euo pipefail` (identical to `install.sh`'s own top-of-file `set
  -euo pipefail`), an unhandled non-zero exit from `sed` aborts the
  entire run, not just this block. This directly contradicts the spec's
  own explicit requirement ("Failure never aborts the whole `install.sh`
  run — it skips this block only, per the `rrsync` precedent") and is
  exactly the property the dispatch asked me to probe under a real
  induced failure, not by reading the code.
- **A value containing a literal `&`** doesn't abort, but silently
  corrupts the config: `sed`'s replacement text treats `&` as "the whole
  matched line", so re-running with model name `weird&model` after an
  existing `TEAM_LLM_MODEL=normal-model` line produces
  `TEAM_LLM_MODEL=weirdTEAM_LLM_MODEL=normal-modelmodel` — a garbled
  line, written with `rc=0` and no diagnostic at all.

**Why should-fix, not must-fix:** the root cause is entirely inside
`set_env()`, a shared helper this cycle doesn't touch, and is already
reachable today via several existing call sites that feed free-text
prompt answers into it the same way (`PVE_HOST`, `SIMPLE_USERNAME`,
`BASE_URL`, `AUTH_MODE`, etc. — all pre-existing, all upsert-path, all
unescaped). This diff doesn't introduce the defect; it calls the existing
helper exactly the way every other optional block in this file already
does. The realistic trigger is also narrow: Ollama model tags (the
`org/model:tag`-shaped strings Ollama and OCI registries use) don't
contain `|` or `&` in practice, and I could not construct a base-URL
value that reaches the `set_env` write path while also containing `&` or
`|` — a URL containing either of those characters breaks the `.../models`
GET itself first (confirmed live: a URL with `?api_key=abc&team=x`
produces a malformed request path and is correctly rejected as
unreachable before ever reaching `set_env`). So in practice this is
reachable only via an unusual/adversarial **model name** on a **second or
later** run.

**Recommendation (out of this cycle's scope to fix, filed as a follow-up):**
either have `--with-ollama` reject/escape values containing `set_env`'s
own delimiter/replacement-special characters before calling it, or fix
`set_env()` itself (e.g. escape `&`, `\`, and the delimiter in `$val`, or
switch to a NUL-safe non-`sed` upsert) — the latter would also close the
same pre-existing gap for `PVE_HOST`/`SIMPLE_USERNAME`/`BASE_URL` and
every other existing upsert caller, which is a repo-wide fix beyond what
this "link an existing Ollama" cycle's own spec asked for.

## Regression check

`/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q`:
**690 passed**, `690 passed, 14 warnings in 131.60s` — matches the
expected 674 baseline + 16 new tests exactly, no flake observed.
`tests/test_deploy_target.py` run in isolation
(`uv run --with pytest python -m pytest tests/test_deploy_target.py -v`):
**30 passed**, including
`InstallScriptDeployTargetBlockTests::test_combined_with_host_control_no_conflicting_state`
(the test whose end-marker literal this cycle's diff had to change) —
confirmed **PASSED**, not skipped, in the verbose run. Independently
re-derived (not just trusted) that the new end-marker
(`'fi\n\n# ── Optional: link an existing remote Ollama'`) extracts exactly
the host-control block and nothing more: wrote a standalone script using
the exact same `_extract_between` logic against the real `install.sh`
source and confirmed the extracted `host_control_block` (855 chars) ends
at the host-control block's own closing `set_env "$CONFIG_DIR/host.env"
ENGINES_DIR ...` line, with `"WITH_OLLAMA"`/`"TEAM_LLM"` both absent from
it — no leakage of the new block into the old test's own extraction.
`tests/test_install_ollama.py` run in isolation, verbose: **16 passed**
(`77.22s`), matching the developer's own reported count. Four Node
suites, run individually: `test_team_frontend.js` 17/17,
`test_deploy_frontend.js` 9/9, `test_singleton_toggle_frontend.js` 15/15,
`test_upload_frontend.js` 8/8 — all pass, untouched by this cycle.

## Spec coverage

Every checkbox in `docs/spec.md`'s "Acceptance criteria" section is
covered by an automated test that actually ran this session (test-cases
1-14 above), plus the "Edge cases worth stating" section (HTML body,
empty `data` array, URL missing `/v1`) is covered by test-cases 6/10/13.
The "Test-isolation requirement" (ephemeral ports, no fixed ports, no
writes outside a per-test fixture) is satisfied by construction
(`_unused_port()`/`HTTPServer(("127.0.0.1", 0), ...)`, per-test `tempfile.
mkdtemp()`) — read directly, confirmed no fixed port or shared path
anywhere in the new test file. No acceptance criterion is unimplemented
or untested.

## Correctness / security review (diff read directly, not re-derived from the developer's own writeup)

- **No command injection.** The model name and base URL never touch
  shell-interpolated script text — the model name is passed to `python3`
  as `sys.argv[1]` (an argument, not code), and both values reach
  `set_env`'s `printf`/`sed` as quoted shell variables. Independently
  reproduced with backticks/`$(...)`/quotes embedded in the model name
  (see "Independent probing" above) — no execution occurred.
- **The `set_env` sed-delimiter/replacement issue** — see Finding #1
  above (should-fix, pre-existing, not newly introduced).
- **Quoting throughout the new block is correct**: every variable
  expansion that reaches `curl`/`sed`/`echo` is double-quoted; no
  unquoted expansion that could word-split or glob.
- **`set -euo pipefail` interaction, otherwise**: the `curl ... || true`
  assignment and the `python3` script's own `except Exception: ...;
  sys.exit(0)` (always exits 0, even on malformed JSON, non-dict `data`,
  etc.) both correctly prevent `set -e` from aborting the run on any of
  curl's or python3's own failure paths — confirmed by direct testing of
  the unreachable, stalling, HTML-body, and non-dict-shaped-JSON cases,
  all of which correctly land in a "skip, write nothing" branch with
  rc=0. Finding #1 above is the one place this guarantee actually breaks,
  and it breaks via `sed`, not via `curl`/`python3`.
- **Block placement / `ENV_FILE` ordering**: sits at `install.sh:641`,
  after `ENV_FILE` is defined and the real config file is guaranteed to
  exist (`install.sh:234-235`, `[ -f "$ENV_FILE" ] || cp
  ".../switchboard.env.example" "$ENV_FILE"`) — confirmed by reading, and
  by the fresh-install probe above (test-case 18) actually exercising
  that exact file shape.
- **`tests/test_deploy_target.py`'s changed marker** — the single
  highest-risk line in this diff per the dispatch brief — independently
  re-verified correct (see "Regression check" above), not just re-run.

## Simplicity / scope review

- The new block is a single `if [ "$WITH_OLLAMA" -eq 1 ]; then ... fi`,
  matching the `--with-deploy-target` precedent's shape exactly (prompts,
  bounded validation, idempotent upsert, skip-don't-abort on failure,
  inline summary). No new abstraction, no new file, no new dependency —
  `curl`/`python3` are both already unconditional installs
  (`install.sh:156`, not `:146` as `docs/spec.md`'s own line-number
  citation says — a trivial, non-substantive doc drift, not worth a
  fix-up on its own).
- **Non-goals confirmed held**: `git diff --stat -- app/app.py
  app/teams.py` produces no output — genuinely untouched. `git status
  --short` confirms the same. No package/systemd-unit/container/model-pull
  command appears anywhere in the new block (test-case 12, plus my own
  independent `grep`/read of the extracted block). No UI surface added
  (no `.js`/`.html` in the diff). No runtime health-check added to
  `app/teams.py` (untouched). No authentication support added.
- **Deviations from spec** (both disclosed in `docs/implementation.md`,
  both independently checked and judged reasonable): (1) prompt defaults
  pre-filled from `get_env` rather than always the spec's literal
  hardcoded string — necessary to satisfy the spec's own separate
  "Idempotence" section for a non-blank default, and confirmed correct on
  both the fresh-install and re-run paths (test-cases 11/18); (2) a blank
  re-run answer revalidates over the network rather than skipping
  validation — a defensible, more conservative reading of "never write
  config you cannot verify," confirmed not to clobber a previously-good
  value if the endpoint later becomes unreachable (would just skip the
  write, per `test_blank_answers_leave_existing_values_untouched`'s own
  setup keeping the stub server alive across both prompts). Neither
  deviation touches `app/app.py`/`app/teams.py` or expands scope beyond
  the spec's own intent.

## Overall verdict

**Approved, with one should-fix follow-up logged (Finding #1: `set_env`'s
unescaped `sed` upsert can abort the whole installer, or silently corrupt
config, on a re-run whose model/URL value contains `|`/`&` — pre-existing
in a shared helper this cycle doesn't touch, narrow real-world trigger for
Ollama model tags specifically, not blocking).** All ten acceptance
criteria plus both stated edge cases are covered by tests that actually
ran this session (16 new automated tests, all independently re-run and
confirmed passing, plus 8 additional manual probes beyond the developer's
own suite). Full regression suite reconfirmed at 690 passed (674 baseline
+ 16 new, no flake across two independent runs), `tests/test_deploy_target.py`
independently reconfirmed at 30 passed with the changed end-marker proven
to extract exactly the intended block and nothing more. Both `Non-goals`
(no `app/app.py`/`app/teams.py` changes, nothing installed locally) hold,
confirmed by diff inspection and by direct execution of the block. No
command-injection risk found despite deliberately adversarial input
(backticks, `$(...)`, quotes, flag-shaped strings). The one real defect
found (Finding #1) is real and reproduced live, not hypothetical, but is
pre-existing, narrowly triggered, and out of this cycle's own stated
scope to fix — logged as a follow-up rather than blocking this cycle.

---

# Test & Review: Roster & composition UI (sub-spec 6e)

## Summary

Testing pass found one confirmed, reproduced acceptance-criterion failure
(Defect 1 below) — the tier-3-lead-selectable requirement, this sub-spec's
own headline goal, is unreachable for the exact roster shape (tier-3-only,
no saved composition yet) `docs/design.md` itself names a dedicated UI
state for. Per process, this stops the testing pass here — **no review
pass performed**. Everything else probed (backward compatibility, server-
side mutual-exclusion enforcement, grounding-route leakage, restart
persistence via a real separate process, roster-name smuggling) passed,
independently re-verified, not just re-read.

## Test-case table (acceptance criteria, `docs/spec.md`)

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Roster listed live off `engines.d`, tier shown, no cache | PASS | `tests/test_team_routes.py::StatusRosterAndCompositionTests::test_roster_reflects_engines_d_live_no_cache` re-run; `test_team_frontend.js` "opening the picker fetches grounding and renders every roster member as a lead option" re-run |
| 2 | Tier-3 selectable as lead, **never blocked**, caveat shown | **FAIL** (see Defect 1) | Live probe against real backend + a throwaway node probe against the real rendered `<script>` (see Defect 1) — for a tier-3-only roster with no saved composition, the picker never renders and Start is permanently disabled |
| 3 | Grounding files shown before start, absent file visible | PASS | `GET /projects/proj/team/grounding` re-run live against a real repo with a genuine secret string in `README.md` — response contains only `{label, relpath, byte_count}` per file, `skipped` list, no `content`/`digest`/`headings`, secret string absent from response body (independent live probe, not just re-running the developer's own test) |
| 4 | Saved composition persists across a **real service restart** | PASS | `tests/test_team_routes.py::CompositionSurvivesRealProcessRestartTests` re-run directly (`tmux` present on this host, so not skipped) — spawns a genuinely separate `python3` subprocess with its own fresh `ThreadingHTTPServer`; confirmed `ok` |
| 5 | Empty `members` rejected, no worktree/session created | PASS | `tests/test_team_routes.py::TeamStartWithCompositionEndpointTests::test_empty_members_rejected_no_launch_no_side_effects` + `tests/test_teams_composition.py::ValidateCompositionTests::test_empty_members_rejected` re-run |
| 6 | Duplicate teammate rejected with a specific message | PASS | `test_duplicate_member_rejected` (both files) re-run |
| 7 | Lead-also-in-members rejected | PASS | `test_lead_also_in_members_rejected` re-run **and** independently reproduced via a raw direct POST (bypassing any client JS) against a live server — `400 {"error": "Lead cannot also be a teammate"}` — confirms server-side enforcement, not just client-side |
| 8 | Unknown roster name in `lead`/`members` rejected, naming it, never substituted | PASS | `test_unknown_lead_rejected_naming_it_never_falls_back_to_default`, `test_stale_saved_composition_referencing_a_removed_engine_is_rejected` re-run **and** independently reproduced live: POSTing a smuggled `{"kind":"engine","name":"nonexistent-smuggled"}` lead against a real server returns `400 {"error": "Unknown lead: nonexistent-smuggled"}`, no worktree created |
| 9 | No `lead`/`members` in body → byte-for-byte unchanged 6d behavior | PASS | `test_no_lead_members_in_body_is_byte_for_byte_unchanged_default_behavior` re-run; independently confirmed by diffing the route's `else` branch against `git show f9d1f2b:app/app.py` (pre-6e) — identical `default_team_composition()` call and identical response shape |
| 10 | Running/blocked/finished/error team: picker not shown | PASS | Pre-existing `test_team_frontend.js` non-idle-state tests (unmodified by this diff) re-run clean; picker code confirmed (by reading) to live entirely inside the pre-existing `!team \|\| team.status === 'idle'` branch |

## Defects

### Defect 1 (must-fix) — a tier-3-only roster with no saved composition permanently disables team-start from the web UI, contradicting this sub-spec's own headline goal

**Where**: `app/app.py` — `GET /status`'s `inst["team"]["composition"]` computation (around line 3479 in the diff) collapses every `default_team_composition()` refusal into a single `None`; `teamRow()`'s `if (composition === null)` branch (picker code, idle-state branch) then renders a permanent, fixed "No roster members available" message and a permanently-disabled Start button whenever `composition` is `None` — with no way to reach the picker at all.

**Root cause**: `default_team_composition()` (`app/teams.py:1809`) returns `{"ok": False, ...}` in three distinct situations: (a) genuinely no roster member at all, (b) exactly one engine that got selected as lead leaving no teammate, and (c) **the roster has a real, pickable engine, but it's tier-3, and 6d's own settled decision (2026-08-13) is that the automatic default never auto-picks a tier-3 lead**. `/status` treats all three identically (`composition: None`), and the frontend's binary `composition === null` check can't tell (c) apart from a genuinely empty roster — even though (c) is exactly the scenario `docs/spec.md`'s own Goal #3 ("every roster member must be pickable as lead, including tier 3 ... never a block") and Edge case ("Tier-3 lead — allowed, not blocked ... the ONE place a naive validator might be tempted to add a tier check that doesn't belong") are about, and exactly the scenario `docs/design.md`'s own "Tier-3-Only Roster (no tier-1 or tier-2)" state was written to cover.

**Reproduced live**, two independent ways:

1. Direct backend probe — a project with only one `engines.d` entry (a tier-3, no-`HEADLESS_SCHEMA_FLAG` engine) and no saved composition:
   ```
   roster: [{'name': 'prose3', 'kind': 'engine', 'label': 'Prose3', 'tier': 3, 'delegate_capable': True, 'schema_flag_error': None}]
   composition: None
   default_team_composition() directly: {'ok': False, 'error': "only a tier-3 (prose-parse, least reliable) lead is available -- configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL, or add a tier-2 (schema-capable) engine to engines.d. The CLI's --lead can still select a tier-3 lead explicitly."}
   ```
2. Frontend probe — the same `roster`/`composition: null` shape fed into the real, rendered `<script>` (same extraction technique `tests/test_team_frontend.js` already uses) produces:
   ```html
   <div class="team-msg error">✕ No roster members available. Add an engine to engines.d or configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL.</div>
   <div class="team-actions"><button class="team-btn" id="start-btn-proj" disabled>Start team</button></div>
   ```
   Start button disabled: `true`. Configure link present: `false`. The message is also factually wrong here — there **is** a roster member, it's simply not one `default_team_composition()`'s own default-selection rules will auto-pick.

**Impact**: for any project whose only headless-eligible engine(s) are tier-3 (a realistic, not contrived, configuration — e.g. a single prose-parsing CLI engine with no Ollama configured), the web UI's team-start feature is **entirely and permanently unreachable** — not degraded, not less convenient, actually impossible — until an operator adds a tier-1/tier-2 engine or configures Ollama. The CLI's `--lead` still works (unaffected, confirmed by the pre-existing `CliTierThreeLeadStillAllowedRegressionTests`), but that's a different surface than what this sub-spec is about.

**Why this isn't just a documentation/message-wording nit**: `docs/implementation.md`'s own "Known limitations" section claims the "Tier-3-Only Roster" design state "falls out automatically ... no special-case branch needed," citing `test_team_frontend.js`'s "a tier-3 lead shows the plain-language reliability caveat, never blocked" test as confirmation. That test's own roster is `[prose3 (tier 3), helper (tier 2)]` with a **pre-supplied, non-null saved composition** (`{lead: prose3, members: [helper]}`) — it never exercises the actual "no saved composition, roster is tier-3-only" path `default_team_composition()` refuses on, so the claim in the implementation doc is not actually verified by the cited test.

**Suggested direction (not prescriptive)**: `/status` needs to distinguish "no roster member at all" from "a roster member exists but the automatic default won't auto-pick one" — e.g. compute `composition` off `roster()` being non-empty rather than off `default_team_composition()["ok"]` alone, so the frontend can still open the picker (with nothing pre-selected, or the first roster member) whenever there's at least one real roster entry, reserving the permanent-disable/refusal state for a genuinely empty roster.

## Regression check

Full existing suite re-run in full this session (not just new/extended files):

- `python3 -m unittest discover -s tests` — **725/725 passed** (matches developer's reported count exactly).
- `node tests/test_team_frontend.js` — **28/28 passed**.
- `node tests/test_deploy_frontend.js` — **9/9 passed**.
- `node tests/test_singleton_toggle_frontend.js` — **15/15 passed**.
- `node tests/test_upload_frontend.js` — **8/8 passed**.

No regressions in any pre-existing test. All new/extended automated tests (`tests/test_teams_composition.py`, extended `tests/test_team_routes.py`, extended `tests/test_team_frontend.js`) pass as reported.

**Minor doc-arithmetic inconsistency (nit, non-blocking)**: `docs/implementation.md`'s "Changes by file" says `tests/test_team_routes.py` gained "+11 new tests across 4 new classes," but its own "Verification status" table says the file went from 20 to 36 tests (i.e. +16). Counting the four new classes directly (`StatusRosterAndCompositionTests` 3, `TeamGroundingEndpointTests` 4, `TeamStartWithCompositionEndpointTests` 8, `CompositionSurvivesRealProcessRestartTests` 1 = 16) confirms +16 is the correct figure; "+11" in the prose is simply wrong arithmetic, not a sign of missing/uncounted tests (the file's total, 36, and the full-suite total, 725, both check out independently).

## Backward compatibility / regression-risk area (independently verified, not just re-run)

- Diffed the `POST /team/start` route's `else` branch (no `lead`/`members` in body) directly against `git show f9d1f2b:app/app.py` (the pre-6e commit) — identical: same `default_team_composition()` call, same failure/success response shape (`{"ok": True, "run_id", "session", "lead", "members"}`). Confirmed byte-for-byte, not inferred from the test alone.

## Security checks performed (independent of the developer's own tests)

- **`GET /projects/<name>/team/grounding` leakage**: live probe against a real repo with a genuine secret string in `README.md` — response body contains only `{label, relpath, byte_count}` per found file and a `skipped` list (itself `{label, relpath, reason}`, no content); secret string and `digest`/`headings` keys both absent. PASS.
- **Server-side (not just client-side) mutual exclusion**: raw direct POST with `lead.name` also in `members`, bypassing any client JS — `400 {"error": "Lead cannot also be a teammate"}`. PASS — not client-side-only.
- **Roster-membership smuggling**: raw direct POST naming an unknown engine as `lead` — `400 {"error": "Unknown lead: <name>"}`, no worktree created. `validate_composition()`'s member-matching also hardcodes `kind == "engine"` when looking up a submitted member (never trusts the client's own declared `kind`), which is what keeps a client from smuggling the Ollama entry in as a delegate-capable teammate by claiming `kind: "engine"` for it. PASS.
- **`save_composition()`-before-`launch_team()` ordering**: confirmed safe — `compositions.json` holds only a `{lead, members, saved_at}` cache used purely to pre-populate the picker on next open; it carries no run/worktree/session state, so a composition being saved when `launch_team()` itself later refuses (session collision, dirty tree) leaves no orphaned or inconsistent runtime state, only a persisted "last picker choice" that's accurate. Confirmed both by reading (no other code path reads `compositions.json` except `/status`'s pre-selection) and by the developer's own `test_saved_on_validated_start_even_if_launch_team_itself_later_fails`, re-run.
- Engine names embedded unescaped inside a single-quoted `value='...'` HTML attribute (`renderTeamPicker()`, `JSON.stringify({kind, name})`) are not run through `esc()`. Noted, not a new risk: engine names come from `engines.d/*.engine` filenames (operator-controlled server config, not remote/browser-supplied input), the same trust boundary the pre-existing `engineRow()`/`pickEngine()` (6d and earlier) already accepts for engine names embedded raw inside an `onclick` string. Not flagged as a defect for this cycle.

## Spec coverage

9 of 10 acceptance criteria pass with real evidence from this session. Criterion 2 (tier-3 lead selectable, never blocked) fails for the tier-3-only-roster-no-saved-composition case — see Defect 1. All edge cases in `docs/spec.md` were probed except this one, which surfaced the defect.

## Review pass

**Not performed.** Per process, a blocking testing-pass failure stops here and routes back to the developer; the correctness/security/simplicity read of the diff that would normally follow was not carried out for this cycle (beyond the security-specific checks above, which are part of the testing pass itself, not a full review). A fresh review pass is warranted once Defect 1 is fixed and re-tested.

## Overall verdict

**Blocked.** One must-fix defect (Defect 1): a tier-3-only roster with no saved composition makes the web UI's team-start feature entirely unreachable, directly contradicting this sub-spec's own headline "tier-3 must be selectable as lead, never blocked" goal and the dedicated design.md state written for exactly this case. Route back to the developer agent with:

1. Fix Defect 1 — distinguish "genuinely empty roster" from "roster has a member but the automatic default won't auto-pick one" in whatever `/status` sends the frontend, so the picker opens (nothing pre-selected, or an explicit prompt to choose) whenever `roster()` is non-empty, reserving the permanent-refusal UI state for a truly empty roster.
2. While there, correct `docs/implementation.md`'s "Known limitations" claim about the "Tier-3-Only Roster" state being automatically covered — it isn't, for the no-saved-composition case (see Defect 1's last paragraph).
3. (Optional, non-blocking) Fix the "+11 new tests" vs. actual +16 arithmetic in `docs/implementation.md`'s "Changes by file" section.

Everything else tested this cycle — backward compatibility, server-side validation (mutual exclusion, unknown-roster-name rejection, kind-smuggling resistance), grounding-route content non-leakage, and real-process restart persistence — passed and was independently re-verified, not just re-read from the developer's report; no need to re-litigate those once Defect 1 is fixed and re-submitted, only re-run the full suite plus a targeted re-test of the tier-3-only scenario.

---

## Re-review (fix round) — Defect 1 verification + full review pass

New cycle, same sub-spec. Baseline before this round: 725 Python / 28 Node
`test_team_frontend.js` (per the blocked pass above). Developer reports
727 Python (+2) / 61 Node (29+9+15+8, +1 in `test_team_frontend.js`).

### 1. Defect 1 re-verified — FIXED, independently reproduced end to end, not just re-read

Re-ran my own exact prior live repro (one tier-3 `engines.d` entry, no
`TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL`, no saved `compositions.json` entry),
against a genuinely separate real server process I started myself (not the
test suite's own harness), over real HTTP with a real login + TOTP flow:

```
GET /status → inst.team.composition for "demo": {"lead": null, "members": []}
```
— a real object, not `null`. Matches the developer's claim exactly.

Went further than a `/status` field check — drove the actual UI-adjacent
flow end to end against the same live server:
- `POST /projects/proj/team/start` with `lead: {"kind":"engine","name":"prose3"}`
  (tier 3) and a real teammate → `200 {"ok": true, "run_id": ..., "lead":
  {"kind": "engine", "name": "prose3", "tier": 3}, "members": ["helper2"]}`
  — a real team actually launched with a tier-3 lead, not merely a UI state
  that claims it would. A follow-up `GET /status` showed `team.status:
  "blocked"` (expected — the stand-in `echo`-based engine can't produce a
  real tool call, so the lead loop escalates via `ask_user` quickly; this
  confirms the launch path ran for real, not that it "succeeded" in a
  product sense).
- Frontend side (`node tests/test_team_frontend.js`, the new test "a
  tier-3-only roster with no saved composition still shows a Configure
  link... and the picker opens with tier-3 selectable"): re-read the test
  body directly (not just its name) — it feeds the exact `{lead: null,
  members: []}` shape through the real `teamRow()`/`toggleTeamPicker()`/
  `renderTeamPicker()` functions (same extraction technique already
  established and re-validated multiple times this project), asserts the
  closed row shows "Configure team..." (not the permanent refusal),
  asserts the rendered `<option>` for `prose3` has no `disabled` attribute,
  and actually drives `onTeamLeadChange()` to select it, confirming
  `teamCompositionError()` no longer reports a lead-selection block
  (only the separate, expected "at least one teammate" reason for this
  single-engine roster). This is a genuine exercise of the fix, not a
  repro replay dressed up as a new test.
- Contrast case re-verified live too: a genuinely empty roster (no
  `engines.d` entries, no Ollama) against a second real server process →
  `composition: null`, unchanged — confirms the fix didn't overcorrect.

**Verdict: Defect 1 is fixed.** The root-cause read (three distinct
`default_team_composition()` refusal reasons collapsed into one `None`) was
correct, and the shipped fix (branch on `roster` being non-empty, matching
my own prior "Suggested direction") closes exactly that gap without
touching `validate_composition()`, the grounding route, or persistence —
confirmed by reading `app/app.py`'s diff directly: the only changed lines
are the `elif roster: composition = {"lead": None, "members": []}` branch
inside `/status`'s per-project composition computation
(`app/app.py:3489-3525`).

### 2. Regression re-check

- `python3 -m unittest discover -s tests -v` → **727 passed**, 0
  failures/errors (run in full, not just new/extended files). Matches
  developer's reported count exactly (725 baseline + 2 new
  `StatusRosterAndCompositionTests` cases).
- `python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests -v`
  → **5 passed** (3 pre-existing + 2 new).
- `node tests/test_team_frontend.js` → **29/29 passed** (28 baseline + 1
  new).
- `node tests/test_deploy_frontend.js` → **9/9 passed**.
- `node tests/test_singleton_toggle_frontend.js` → **15/15 passed**.
- `node tests/test_upload_frontend.js` → **8/8 passed**.
- Total: 727 Python + 61 Node, matching the developer's reported counts
  exactly.

Spot-re-poked the highest-risk previously-verified acceptance criteria
directly against a real running server (not just re-running the existing
test suite), since this is exactly the kind of surface a careless fix could
regress:
- **Backward compatibility (AC #9)**: `POST /team/start` with no
  `lead`/`members` in the body, against the same tier-3-only-roster project
  used for the Defect 1 repro → `400 {"error": "only a tier-3 (prose-parse,
  least reliable) lead is available -- configure TEAM_LLM_BASE_URL/
  TEAM_LLM_MODEL, or add a tier-2 (schema-capable) engine to engines.d. The
  CLI's --lead can still select a tier-3 lead explicitly."}` — confirms
  `default_team_composition()`'s own refusal (unmodified) still applies
  unchanged to the no-picker path even though `/status`'s picker-facing
  field now differs for the same roster shape. The fix is genuinely scoped
  to `/status` only, not to the start route's own default-composition
  fallback.
- **Server-side mutual exclusion (AC #7)**: raw POST with `lead.name` also
  in `members` → `400 {"error": "Lead cannot also be a teammate"}`.
- **Unknown-roster-name rejection (AC #8)**: raw POST with a smuggled
  nonexistent lead name → `400 {"error": "Unknown lead: nonexistent-smuggled"}`,
  no worktree created.

All three match the prior round's findings exactly — no regression.

### 3. Documentation-fix asks

- **"Known limitations" claim**: corrected, and corrected honestly —
  `docs/implementation.md`'s original wrong claim ("'Tier-3-Only Roster'
  falls out automatically... no special-case branch needed") is kept
  in place, explicitly marked "CORRECTED" with the reasoning for why it was
  wrong, rather than silently rewritten (`docs/implementation.md:2584-2617`).
  The correction accurately cites the actual fix and both of its new,
  genuinely-exercising regression tests (verified above, not just the
  citation) — confirmed accurate, not just present.
- **"+11" → "+16" arithmetic**: fixed at the original occurrence
  (`docs/implementation.md:2419-2421`, now reads "+16 new tests across 4 new
  classes -- corrected count, per the reviewer's own recount (3 + 4 + 8 + 1
  = 16); originally miswritten as '+11'") — again corrected in place with
  the error disclosed, not silently changed. Independently recounted the
  four new test classes myself: 3 + 4 + 8 + 1 = 16, confirmed correct.

### 4. Full review pass (first for this sub-spec — the blocked round never reached it)

**Spec-to-code traceability.** Re-checked all 10 acceptance criteria in
`docs/spec.md` against the code and my own re-run/re-poked tests:

| # | Criterion | Status |
|---|---|---|
| 1 | Roster listed live off `engines.d` | Pass — unchanged this round, test re-run |
| 2 | Tier-3 selectable as lead, never blocked | **Pass — fixed and independently re-verified end to end (backend field, real launch, frontend render+select)** |
| 3 | Grounding files shown, absent file visible, no leakage | Pass — route code unchanged by this round's diff (confirmed via diff read); prior round's live secret-leakage probe stands, technique already proven, not re-derived from scratch |
| 4 | Composition persists across a real service restart | Pass — unchanged, part of the 727 (`CompositionSurvivesRealProcessRestartTests`), separate-subprocess technique already independently verified last round |
| 5 | Empty members rejected, no launch | Pass — unchanged, part of the 727 |
| 6 | Duplicate teammate rejected | Pass — unchanged, part of the 727 |
| 7 | Lead-also-in-members rejected | Pass — re-poked live this round (see above), still server-side enforced |
| 8 | Unknown roster name rejected, never substituted | Pass — re-poked live this round (see above) |
| 9 | No lead/members → byte-for-byte unchanged default behavior | Pass — re-poked live this round against the exact tier-3-only roster (see above); confirms the fix is genuinely `/status`-scoped |
| 10 | Non-idle team: picker not shown | Pass — unchanged, part of the 727/29 |

All 10 acceptance criteria have real implementation and test coverage this
round; no gap.

**Correctness review (full diff, not just the fix).** Read `app/teams.py`'s
entire 6e diff (`validate_composition()`, `_compositions_path()`,
`load_compositions()`, `save_composition()`) and `app/app.py`'s entire 6e
diff (the `/status` composition computation including this round's fix, the
new `GET .../team/grounding` route, the extended `POST .../team/start`
body handling, `teamRow()`/`toggleTeamPicker()`/`renderTeamPicker()`/
`teamCompositionError()`/`actionBody()`) directly, not just the fix's own
diff:
- `validate_composition()`'s rules match `docs/spec.md` §1 exactly: lead
  shape/roster-match check, tier-2-only schema-flag check (tier 3
  correctly exempted), non-empty/no-duplicate members, lead-not-in-members,
  every member matched by `("engine", name)` specifically — this is what
  keeps a client from smuggling the Ollama entry in as a teammate by
  claiming `kind: "engine"` for it (re-confirmed by reading the code, not
  just trusting the prior round's live probe).
- `POST /team/start`'s composition branch (`app/app.py:3686-3708`)
  re-derives the lead's `tier` from a fresh `roster()` call rather than
  trusting a client-submitted value, and saves the composition
  unconditionally on successful validation before calling `launch_team()`
  — both match `docs/spec.md`'s explicit ordering requirement ("saved …
  independent of whether `launch_team()` itself later succeeds").
  `next(e for e in teams.roster() if ...)` at line 3705 could theoretically
  raise `StopIteration` if `engines.d` changed between the
  `validate_composition()` call and this line (a hand-edit racing a
  request) — not exploitable (requires local filesystem write access an
  attacker with that access already has far more direct routes with), and
  the same class of small window the codebase already accepts elsewhere
  (`compositions.json`'s own "last write wins, no locking" accepted
  precedent) — not a new finding.
- No other logic changes this round beyond the `/status` branch — confirmed
  by `git diff` scope (the entire teams.py diff, the grounding route, and
  the start route are all pre-existing from the blocked round, untouched by
  the fix commit).

**Security review.** No new privileged surface, no new input-trust boundary
crossed by this round's fix (it only changes which of two already-existing
values `/status` returns for an already-computed condition). Re-confirmed
the prior round's findings still hold by reading the current code: grounding
route strict field allowlist unchanged; `validate_composition()`'s
`kind == "engine"` hardcoding for member lookups unchanged;
`save_composition()`'s "only kind+name persisted, tier always re-derived
live" unchanged. The pre-existing, previously-noted non-issue (engine names
embedded raw, unescaped by `esc()`, inside `value='...'` — operator-
controlled `engines.d` filenames, not remote input, same trust boundary
`engineRow()`/`pickEngine()` already accept) is untouched by this round and
still not a defect for the reasons given last round.

**Simplicity/scope review.** The fix is minimal and exactly scoped to what
was needed: one new `elif` branch reusing the same-call's already-computed
`roster` list (no new function, no new route, no new persisted field, no
duplicate `roster()` call). `docs/implementation.md`'s stated rationale for
choosing "nothing pre-selected" over "pre-select the first roster member"
is sound and matches `docs/design.md`'s own literal "Choose a lead..."
empty-select default — not an invented behavior.

**Design.md consistency (should-fix, non-blocking).** `docs/design.md`'s
"Idle, Picker Closed" state (line 336: "If default composition is rejected
(e.g., tier-3-only), show the server's error message inline rather than a
broken picker") and the "Tier-3-Only Roster" state's own ASCII mockup
(lines 462-469: `[Picker expanded, ...]`, `[Tier-3 caveat shown by default,
cannot be hidden]`) both describe a *different* UI treatment than what the
now-correct implementation actually does: the picker stays collapsed
behind the same "Configure team..." link as any other real composition
(confirmed live and via the frontend test above), and the tier-3 caveat
only renders once a tier-3 lead is *actually selected*
(`leadEntry && leadEntry.tier === 3` in `renderTeamPicker()`), not "by
default, cannot be hidden." Neither of design.md's two claims for this
state was updated as part of this fix round. This is **not a functional
defect** — the actual behavior (click-to-open, caveat conditional on
selection) is arguably better UX and, more importantly, genuinely satisfies
`docs/spec.md`'s own literal acceptance criterion ("tier-3 must be
selectable as lead, never blocked" — confirmed true) — but `docs/design.md`
no longer accurately depicts this state, and nobody flagged or corrected
it in this round's doc-fix pass. Should-fix, non-blocking: a follow-up
correction to `docs/design.md`'s "Idle, Picker Closed" bullet and the
"Tier-3-Only Roster" mockup to match the actual (correct) collapsed-by-
default, caveat-on-selection behavior.

### Overall verdict (re-review)

**Approved**, with one non-blocking follow-up.

Defect 1 is fixed and independently re-verified end to end — not just the
`/status` field, but a real tier-3-led team launch over real HTTP, plus the
frontend genuinely rendering a non-disabled, selectable tier-3 lead option
and a working selection flow. Both requested documentation corrections
("Known limitations" claim, the +11/+16 arithmetic) were made accurately
and honestly (corrected in place with the original wrong claim preserved
and marked, not silently rewritten). No regressions: full suite re-run
clean (727 Python, 61 Node, matching reported counts exactly), and the
three highest-risk previously-passing criteria (backward compatibility,
mutual exclusion, unknown-roster rejection) were independently re-poked
live against a real server, not just re-trusted from the test suite. The
full review pass (deferred from the blocked round) found no must-fix or
should-fix issues in correctness or security; one non-blocking should-fix
follow-up: `docs/design.md`'s "Idle, Picker Closed" bullet and the
"Tier-3-Only Roster" mockup should be updated to match the actual (correct)
collapsed-by-default, caveat-on-selection behavior — cosmetic
documentation drift, not a functional gap, and does not block this cycle.
