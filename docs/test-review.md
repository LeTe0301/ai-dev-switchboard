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
