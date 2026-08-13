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
