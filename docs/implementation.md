# Implementation: Roster + lead loop, all three adapter tiers (sub-spec 6c)

## Summary

`app/teams.py` gains a **roster** (`roster()`), all **three lead adapters**
(tier 1 native tool-calling / tier 2 schema-constrained / tier 3 prose-parse),
the **four-tool lead loop** (`delegate`/`fact_check`/`ask_user`/`finish`), and
five new CLI subcommands (`roster`, `team-start`, `team-status`,
`team-resolve`, `team-resume`) — all driven from the CLI only, no web route,
no UI, per scope. `app/app.py` gets exactly the two additive `Engine` fields
the spec named (`headless_lead_format`, `headless_schema_flag`) and nothing
else — confirmed by `git diff --stat -- app/app.py` (23 insertions, 3
deletions, both purely the two new fields and their parsing).

**This document now covers three build rounds.** Round 1 implemented the
spec as originally written and ran all three tiers for real. That run
**found a genuine spec-level defect** (Claude Code's `--json-schema` wants
inline JSON text, not a file path — the spec had assumed one substitution
form for both `claude`/`codex`) and one live model-judgment finding (a real
`qwen3:8b` run delegated the same task twice). The coordinator corrected
`docs/spec.md` (`16d79b6`) for both. Round 2 implemented and reverified both
corrections for real. **Round 3** (this round) responds to the reviewer's
`docs/test-review.md` changes-requested verdict: one must-fix (a real,
independently-confirmed correctness bug in the *round-2* `{schema}`/
`{schema_file}` substitution code itself — sequential `str.replace()` passes
letting a later pass rescan and corrupt an earlier one's output) and one
should-fix (a documentation gap, not a functional one).

- **Round 1 → Fix 1 (two schema placeholders, `{schema}`/`{schema_file}`)**
  — real tier-2 lead run against the real, logged-in `claude` CLI reaches
  `status: finished` cleanly. See "Fix 1: real tier-2 verification" below.
- **Round 1 → Fix 2 (explicit/salient delegation history + a new prompt-
  level mitigation clause)** — three fresh real tier-1 runs against the same
  live endpoint, same task, all three completed in exactly 2 rounds (one
  `delegate`, one `finish`) with no repeat. See "Fix 2: real reverification"
  below — reported honestly as "did not recur in 3/3 runs", not "fixed for
  good", since this is a probabilistic model and 3 runs is a real but modest
  sample. **The reviewer independently checked this exact framing and
  explicitly approved it as-is, with the instruction not to strengthen it —
  left unchanged this round.**
- **Round 3 → Finding #1 (must-fix): `_build_headless_argv()`'s sequential
  substitution defect** — replaced with a single-pass, simultaneous
  substitution (`_substitute_headless_tokens()`). Both reviewer-confirmed
  repros (schema JSON containing a literal `{prompt_file}` substring; a
  `session_id` containing a literal `{schema}` substring) are now permanent
  regression tests and independently reverified manually. See "Round 3,
  Finding #1" below.
- **Round 3 → Finding #2 (should-fix): AC #2's literal chained sequence
  undocumented** — closed by running the exact `delegate → fact_check →
  delegate → finish` sequence for real and recording it. See "Round 3,
  Finding #2" below.

## Changes by file

- **`app/app.py`** — unchanged since round 1: `Engine.__slots__`/
  `__init__`/`_parse_engine_file()` gain `headless_lead_format`,
  `headless_schema_flag` (both `None`-default, additive, no enum
  validation). Nothing else in this file changed (verified: `git diff
  --stat -- app/app.py`, still 23 insertions / 3 deletions after round 2).

- **`app/teams.py`** (1539 → round 1: 2586 → round 2: 2698 → round 3: 2769
  lines):
  - **Round 3, Finding #1 (must-fix)**:
    - `_HEADLESS_CMD_TOKENS` / `_HEADLESS_CMD_TOKEN_RE` — the fixed set of
      three literal tokens `_build_headless_argv()` ever substitutes,
      compiled into one alternation.
    - `_substitute_headless_tokens(cmd, mapping)` — the actual fix: ONE
      `re.sub()` pass over the *original* `cmd` text with a replacement
      function, so a value already substituted for one token (e.g. a
      resume fragment, a schema's JSON text) is never rescanned for
      another token's literal substring. A key absent from `mapping` (e.g.
      `{prompt_file}` outside `HEADLESS_PROMPT=file`) leaves that token
      untouched if it happens to appear — explicit, not incidental, per
      the reviewer's own instruction.
    - `_build_headless_argv()` rewritten to resolve all three substitution
      values into a `mapping` dict FIRST, then call
      `_substitute_headless_tokens()` once, replacing the previous three
      sequential `str.replace()` calls over the same growing string.
  - **Config** (§1): unchanged from round 1 — `TEAM_LLM_BASE_URL`,
    `TEAM_LLM_MODEL`, `TEAM_LLM_TIMEOUT_SECONDS`,
    `TEAM_LLM_TRANSPORT_RETRY_BUDGET`, `TEAM_MAX_ROUNDS`,
    `TEAM_LEAD_MALFORMED_RETRY_BUDGET`, `TEAM_DELEGATE_RESULT_MAX_CHARS`,
    `TEAM_LEAD_PROMPT_MAX_CHARS`.
  - **Round 2, Fix 1 — two schema placeholders**:
    - `_schema_placeholder_kind(headless_schema_flag)` — `"file"` if
      `{schema_file}` is present, `"inline"` if `{schema}` is (checked in
      that order), `None` if neither (the configuration-error case).
    - `_schema_flag_config_error(e)` — human-readable message for an
      engine whose `HEADLESS_SCHEMA_FLAG` declares neither placeholder,
      called by `roster()` (new `schema_flag_error` field on every
      `"engine"`-kind entry) and `_cli_team_start()` (early rejection of a
      misconfigured `--lead`, before anything runs) — both satisfy the
      spec's own "reported at roster-build time, not at the first tier-2
      lead call" requirement.
    - `_resolve_schema_fragment(headless_schema_flag, schema, schema_path)`
      — builds the actual flag+value fragment: `{schema_file}` substitutes
      the path; `{schema}` substitutes the schema's own JSON text,
      `shlex.quote()`'d as a single argv element.
    - `_build_headless_argv()` — now takes `schema` (the dict, for the
      inline form) in addition to `schema_path` (for the file form);
      delegates the actual substitution to `_resolve_schema_fragment()`.
    - `_validate_prompt_size()` gains a `schema_text` parameter: when the
      schema is delivered inline, its own `shlex.quote()`'d length is
      **summed with the prompt's** in `arg` mode (both share the same "how
      much are we willing to cram into argv" budget), and checked
      **independently** in `stdin`/`file` mode (the prompt isn't in argv
      there, but an inline schema always is, regardless of
      `HEADLESS_PROMPT`). Existing callers (no `schema_text` passed) are
      byte-for-byte unaffected.
    - `agent_run()` — raises `ValueError` before spawning anything, in
      addition to the existing "no schema flag at all" check, if the
      declared flag has neither recognized placeholder. Always writes
      `rundir/schema.json` (harmless even when the delivery mode is
      inline; same `finally: shutil.rmtree(rundir, ...)` cleanup either
      way).
  - **Round 2, Fix 2 — repeated-delegation mitigation**:
    - `_DELEGATION_HISTORY_MITIGATION` — a new required-verbatim clause,
      always included in `_system_framing()` for every tier, alongside
      `_FACT_CHECK_MITIGATION` (same pattern, same precedent).
    - `team_step()`'s `delegate` branch — `args_summary`/`outcome_summary`
      now state the agent, a task preview, and `SUCCEEDED`/`FAILED`
      explicitly (e.g. `delegate(agent=claude, task="Reply with exactly
      one word...")` → `SUCCEEDED, 4 chars (see log)`), replacing round 1's
      terser `delegate(agent=claude)` → `ok, 4 chars (see log)`, which left
      the task text and success/failure to be inferred from the prose
      "see log" pointer rather than stated in the round history itself.
  - Everything else (roster, four-tool schema, prompt assembly, tier 1/3
    adapters, shared validation, persistence, the loop, CLI) is unchanged
    from round 1 in shape — see round 1's own summary below for the full
    list of what each piece is.

- **`engines.d/claude.engine`** — `HEADLESS_SCHEMA_FLAG=--json-schema
  {schema}` is **unchanged text**, but now correct: `{schema}` means
  "inline JSON text" under the round-2 two-placeholder design, which is
  exactly what Claude Code's own flag wants. Comment updated to record the
  original wrong reading, the live failure it produced, and the fix.
- **`engines.d/codex.engine`** — `HEADLESS_SCHEMA_FLAG` changed from
  `--output-schema {schema}` to `--output-schema {schema_file}` (Codex's
  own flag is a real file-path flag, so it needed the *other* new
  placeholder).
- **`engines.d/aider.engine`** — unchanged, as specified.
- **`config/switchboard.env.example`** — unchanged since round 1 (no new
  env vars needed for the fixes; both are `.engine`-file-level and code-
  level changes only).
- **`docs/ADDING_AN_ENGINE.md`** — the "Lead-adapter hints" section
  rewritten for the two-placeholder design and the roster-build-time
  config-error surfacing; the round-1 "known limitation" writeup replaced
  with a "real, verified finding" writeup (the defect existed, was found,
  and is now fixed and reverified — not a standing limitation anymore).
- **`tests/test_teams_lead.py`** (89 → round 2: 105 → round 3: 112 tests) —
  see "Verification status" below for what's new this round.
- **`tests/fixtures/headless/tier3_stub_*.sh`** — unchanged from round 1.
- **`docs/test-review.md`** — the reviewer's own artifact, not touched by
  the developer; referenced throughout this round's writeup.

## Key decisions / tradeoffs

- **`HEADLESS_CMD`'s own `{schema}` token is a separate thing from
  `HEADLESS_SCHEMA_FLAG`'s own internal placeholder, even though they can
  share the literal string `{schema}`.** `HEADLESS_CMD`'s token is always
  "where does the resolved flag+value fragment go" (unchanged in name from
  round 1); `HEADLESS_SCHEMA_FLAG`'s own placeholder (`{schema}` or
  `{schema_file}`) is "which form does THIS engine's flag actually take".
  These are two independent `str.replace()` passes over two different
  strings (`_resolve_schema_fragment()` resolves the flag's own template
  first, fully, with no placeholder tokens left in its output; only then
  does that fully-resolved string get spliced into `HEADLESS_CMD`'s own
  `{schema}` occurrence) — there is no risk of the two stages colliding or
  double-substituting, verified directly by
  `ResolveSchemaFragmentTests`/`RealTmuxSchemaTests`.
- **The inline schema is checked against the arg-mode cap independent of
  `HEADLESS_PROMPT`.** An inline schema is always its own argv element (via
  `HEADLESS_CMD`'s `{schema}` token), regardless of how the *prompt* is
  delivered (`arg`/`stdin`/`file`) — so `_validate_prompt_size()` checks it
  either summed with the prompt (arg mode, sharing one budget) or on its
  own (stdin/file mode, where the prompt isn't in argv but the schema
  still is). Both directions are tested directly
  (`ValidatePromptSizeSchemaInteractionTests`).
- **`schema.json` is still always written to `rundir`, even when the
  engine's own delivery mode is inline and never reads that file.**
  Simpler than conditionally skipping the write, harmless (a few hundred
  bytes, same unconditional `rundir` cleanup either way), and keeps
  `agent_run()`'s own internal logic from branching on "will this actually
  be used" — the file's mere existence costs nothing.
- **`roster()`'s `schema_flag_error` field is only present on `"engine"`-
  kind entries**, not `"ollama"` entries — an Ollama roster entry has no
  `Engine` object and structurally cannot have this specific
  misconfiguration, so a spurious `null` there would be noise, not signal.
- **`_cli_team_start()`'s early rejection re-derives the tier from
  `_lead_tier_for_engine()` rather than trusting a cached value** — matches
  the existing "always re-read, never cache" philosophy `load_engines()`
  and this whole config system already commit to.
- Everything from round 1's own "Key decisions" section (tier 1's system/
  user split, `_new_state()`/`_persist()` being `run.json` itself,
  transcript granularity, tier 2's schema dict being passed fresh every
  round) is unchanged this round.

## Deviations from spec

All five of round 1's own deviations are **unchanged and reaffirmed** —
the coordinator's message explicitly confirmed all three judgment calls
(the `escalated_max_rounds`/`inbox.json` reading, folding a non-JSON tier-1
body into transport-retry, and the trimmed transcript granularity) as
correct and asked that they be kept, documented. Repeated here verbatim
rather than re-argued:

1. **`escalated_max_rounds` does not write `inbox.json`; only
   `blocked_ask_user` does** — resolving a genuine tension between §10's
   pseudocode (one shared `_force_ask_user()` name for both cases) and
   §11's more specific persistence text (the two are materially different
   status values, and inbox.json is explicitly tied to only one of them)
   toward the more specific/authoritative section. Confirmed correct by
   the coordinator.
2. **A response body that fails `json.loads()` at all is folded into the
   tier-1 transport-retry category**, not given a separate code path — no
   other bucket is defined for it in the spec, and it is not a model-tool-
   choice problem. Confirmed correct by the coordinator ("right, it's a
   transport-layer symptom").
3. **Transcript granularity is a documented, deliberate trim** of §11's own
   more maximal-reading language. Confirmed fine by the coordinator.
4. **No `HEADLESS_ROLE_FLAG` this round** — the spec's *own* named,
   explicit deviation, carried through unmodified.
5. **`ask_user`'s 2–4-option count is not enforced by
   `_validate_lead_action()`** — no business-rule category or acceptance
   criterion covers it; `_write_inbox()` writes whatever shape-valid
   `options` list it was given, same as `header`'s own "silently
   truncated" treatment. Not addressed by the coordinator's message either
   way; still flagged as a minor, low-confidence reading.

**Round 2's own new deviation, disclosed:** none. Fix 1 and Fix 2 were both
specified by the coordinator's own corrected `docs/spec.md` in enough
detail (the two placeholder names, the summed-vs-independent size check,
the "make prior delegations explicit and salient" instruction) that this
round is a faithful implementation of the corrected spec, not a fresh
judgment call of its own.

## Fix 1: real tier-2 verification

**Round 1's failure, reproduced for the record** (the original,
single-placeholder design, `{schema}` meaning "a file path" for both
engines):

```bash
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "Verify (using fact_check) that this project uses SQLAlchemy, then finish with a one-sentence summary." \
  --lead claude --members ""
```
```
{"kind": "error", "text": "the lead's action is not a JSON object", ...}
{"kind": "error", "text": "the lead's action is not a JSON object", ...}
{"kind": "error", "text": "The lead's output could not be parsed after 3 attempts. Raw text:
  Error: --json-schema is not valid JSON: JSON Parse error: Unrecognized token '/'", "forced": true}
"status": "blocked_ask_user"
```

**Round 2, same command, same task, same real logged-in `claude` CLI, after
Fix 1** (`claude.engine`'s `HEADLESS_SCHEMA_FLAG=--json-schema {schema}` is
unchanged text but now means "inline" under the corrected design):

```bash
unset TEAM_LLM_BASE_URL TEAM_LLM_MODEL
python3 app/teams.py roster   # confirms: claude tier 2, schema_flag_error: null
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "Verify (using fact_check) that this project uses SQLAlchemy, then finish with a one-sentence summary." \
  --lead claude --members ""
```
```json
{
  "status": "finished",
  "round": 2,
  "action_count": 1,
  "history": [
    {"round": 1, "tool": "fact_check",
     "args_summary": "fact_check(\"using SQLAlchemy as the ORM\")",
     "outcome_summary": "found=True", ...},
    {"round": 2, "tool": "finish", "outcome_summary": "finished",
     "full_result_text": "Confirmed via fact_check that the project uses SQLAlchemy as its ORM, per docs/ARCHITECTURE.md."}
  ],
  "summary": "Confirmed via fact_check that the project uses SQLAlchemy as its ORM, per docs/ARCHITECTURE.md."
}
```

Clean, real, end-to-end tier-2 lead run: `fact_check` (found the real
supporting passage in `docs/ARCHITECTURE.md`) → `finish`, zero malformed
retries, ~9 seconds wall time. `codex` remains unauthenticated in this
environment (401 from `api.openai.com`, same limitation 6a already
disclosed for `codex.engine`), so the `{schema_file}` (path) form is
verified real end to end only via `RealTmuxSchemaTests`' test-authored
stand-in engine, not the real `codex` CLI — disclosed, not fabricated.

## Fix 2: real reverification of the repeated-delegation finding

Same task, same real remote `qwen3:8b`, run three times:

```bash
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "Delegate to the teammate named claude: ask them to reply with exactly one word describing what kind of app this is, based on the project docs. Then finish with that word in your summary." \
  --lead-ollama --members "claude"
```

| Run | Rounds | Sequence | Recurred? |
|---|---|---|---|
| 1 | 2 | `delegate(claude)` → `SUCCEEDED` → `finish` | No |
| 2 | 2 | `delegate(claude)` → `SUCCEEDED` → `finish` | No |
| 3 | 2 | `delegate(claude)` → `SUCCEEDED` → `finish` | No |

All three completed in exactly 2 rounds (the minimum possible for this
task), versus round 1's own live finding of 3 rounds (two `delegate` calls
to the identical task before `finish`). The round-history line for round 1
of each of these runs now reads, verbatim (round 2 shown as an example):

```
delegate(agent=claude, task="Reply with exactly one word describing what kind of app this is, based on the project docs.") -> SUCCEEDED, 4 chars (see log)
```

— explicit agent, explicit task text, explicit `SUCCEEDED`, exactly the
"agent, task, succeeded or not" salience the correction asked for, plus the
new `_DELEGATION_HISTORY_MITIGATION` clause instructing the lead not to
re-delegate a task that already succeeded.

**Honest framing, not overclaiming**: 3/3 clean runs on the *same* task
prompt is real, positive evidence that the mitigation works, not proof the
behavior can never recur. `qwen3:8b` is a small model and this remains a
probabilistic judgment call, the same category as the spike's own single
`wrong_tool` miss (9/10, not 10/10) — a different task, a longer round
history, or simple variance could still produce a repeat. This is recorded
as "did not recur in 3/3 real runs after the mitigation", not "structurally
impossible now", and any future cycle revisiting tier-1 prompt tuning
should treat it as an ongoing, monitored judgment-quality question rather
than a closed ticket. **The reviewer independently checked this exact
document's framing this round and explicitly approved it as-is** — no
change made here in round 3.

## Round 3, Finding #1 (must-fix): `_build_headless_argv()`'s sequential substitution defect

`docs/test-review.md` found a real, independently-confirmed correctness bug
in round 2's own `{schema}`/`{schema_file}` substitution code: three
sequential `str.replace()` calls over the same growing `cmd` string (lines
349–357 as of round 2) meant text inserted by an EARLIER pass got rescanned
— and potentially corrupted — by a LATER one. Two confirmed repros, both
now permanent regression tests:

1. **Schema JSON containing a literal `{prompt_file}` substring**, on an
   engine with `HEADLESS_PROMPT=file` and a `HEADLESS_SCHEMA_FLAG` (the one
   reachable combination no shipped engine happens to use, but
   `docs/ADDING_AN_ENGINE.md` explicitly invites operators to add their
   own engines with exactly this shape). The later `{prompt_file}` pass
   silently rewrote the schema's own JSON text, splicing the real rundir
   prompt-file path into what should have been inert schema content.
2. **A `session_id` containing a literal `{schema}` substring.**
   `session_id` is sourced from an engine CLI's own JSON output (Claude
   Code's/Codex's own `session_id`/`thread_id`) — semi-trusted, not
   developer-controlled. The later `{schema}` pass rescanned the resume
   fragment built from it, splitting the resume argv and splicing schema
   JSON into the middle of it.

**Fix: single-pass, simultaneous substitution, not a reordering.** The
reviewer verified directly that reordering cannot fix this — with N
sequential passes over one shared string, whichever token is substituted
FIRST is vulnerable to every pass after it, and since more than one token's
own value (schema JSON, `session_id`) can plausibly contain another token's
literal substring, no ordering protects all of them. `_substitute_headless_
tokens()` (new) resolves all three substitution values into a `mapping`
dict FIRST, then does ONE `re.sub()` pass over the *original*
`engine.headless_cmd` text with a replacement function — `re.sub()` finds
every match against the original string in one linear scan and splices in
each match's replacement verbatim; it never re-enters or rescans a
replacement value for further matches, which is what actually closes this
class of bug rather than picking a "safer" order for the same sequential
shape.

**`{prompt_file}` stays explicitly, not incidentally, mode-gated**: it is
only ever placed into the `mapping` dict when `engine.headless_prompt ==
"file"`. In `arg`/`stdin` mode, `{prompt_file}` is simply absent from the
mapping, so `_substitute_headless_tokens()`'s own replacement function
(`mapping.get(token, token)`) leaves a literal `{prompt_file}`, if one
happens to appear in `HEADLESS_CMD`, completely untouched — verified by a
dedicated test (`test_prompt_file_token_left_untouched_when_not_in_file_
mode`).

**Severity, confirmed correctly scoped**: a correctness bug, not a
privilege/containment break — argv elements stay individually quoted going
into `subprocess`/tmux, so this was never shell injection. The fix is
correspondingly a correctness fix (remove the class of bug structurally),
not a security-shaped change.

**Both repros independently re-verified manually, then encoded as permanent
tests** (`tests/test_teams_lead.py`, `BuildHeadlessArgvSinglePassSubstitutionTests`):
- `test_schema_text_containing_literal_prompt_file_token_is_not_rewritten`
  — repro #1, asserts the schema round-trips byte-for-byte and the real
  rundir path never leaks into it.
- `test_resume_fragment_containing_literal_schema_token_is_not_rewritten`
  — repro #2, asserts the `session_id` arrives in argv exactly as given.
- `test_prompt_file_token_left_untouched_when_not_in_file_mode` — the
  explicit-not-incidental mode-gating requirement.
- `test_ordinary_case_all_three_tokens_substituted_correctly` — all three
  tokens still resolve correctly together when there's nothing adversarial
  going on (no regression in the ordinary path).
- `SubstituteHeadlessTokensPureTests` (3 tests) — the substitution
  primitive itself, directly: single pass doesn't rescan a replacement
  value shaped like another token, an absent mapping key leaves its token
  untouched, no-tokens-present is a no-op.

Manually reproduced both repros directly against the fixed code before
writing the permanent tests (not just trusting the tests to prove it):

```python
# Repro 1 -- schema text containing a literal {prompt_file} substring
argv = _build_headless_argv(engine_file_mode_with_schema_flag, prompt="ignored",
    session_id=None, prompt_path="/run/abc/prompt.txt",
    schema={"...": {"description": "see {prompt_file}"}}, schema_path="...")
# argv[2] == the schema's own unmodified JSON text, "{prompt_file}" still
# literally present inside it, "/run/abc/prompt.txt" NOT leaked in
```
```python
# Repro 2 -- session_id containing a literal {schema} substring
argv = _build_headless_argv(engine_arg_mode_with_resume_and_schema,
    prompt="hello", session_id="weird-{schema}-id", ...)
# argv == [..., "--resume", "weird-{schema}-id", "--json-schema", '{"a": 1}', ...]
# the session id arrives byte-for-byte, unmangled, as its own argv element
```

**Nothing else in `app/teams.py` has this bug pattern.** Checked directly
(`grep -n "\.replace(" app/teams.py`): the other two substitution sites
(`_resume_fragment()`'s `{session_id}` substitution into `HEADLESS_RESUME`;
`_resolve_schema_fragment()`'s `{schema}`/`{schema_file}` substitution into
`HEADLESS_SCHEMA_FLAG`) are each a SINGLE token substituted ONCE into their
own short, separate, developer-authored template string — not chained with
any other substitution over the same growing string, so they don't share
this bug. The class of bug is specifically "sequential substitutions over
one shared, growing string, where an earlier substitution's OUTPUT becomes
part of what a later substitution's PATTERN can match" — `_build_headless_
argv()` was the only place in this file doing that, and it no longer does.

## Round 3, Finding #2 (should-fix): the literal AC #2 sequence, documented

The reviewer independently ran the exact acceptance-criterion #2 sequence
(`delegate → fact_check → delegate → finish`) live and it completed
correctly, but `docs/implementation.md` (rounds 1–2) only ever documented
`fact_check → finish` and `delegate → finish` as two *separate* real runs,
never the literal 4-step chain. Closed by running it for real:

```bash
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "First delegate to the teammate named claude: ask them to name the project's database technology in one word, based on the project docs. Then use fact_check to verify that this project uses SQLAlchemy. Then delegate to claude again: ask them to name the web framework in one word. Then finish with a one-sentence summary covering both answers." \
  --lead-ollama --members "claude"
```

```json
{
  "status": "finished",
  "round": 4,
  "action_count": 3,
  "teammate_sessions": {"claude": "edd861ff-883d-4eb4-a5c2-e9bb10bfe9fb"},
  "history": [
    {"round": 1, "tool": "delegate",
     "args_summary": "delegate(agent=claude, task=\"Name the project's database technology in one word, based on the project docs.\")",
     "outcome_summary": "SUCCEEDED, 6 chars (see log)", "full_result_text": "SQLite"},
    {"round": 2, "tool": "fact_check",
     "args_summary": "fact_check(\"using SQLAlchemy as the ORM\")",
     "outcome_summary": "found=True"},
    {"round": 3, "tool": "delegate",
     "args_summary": "delegate(agent=claude, task=\"Name the web framework in one word based on the project docs.\")",
     "outcome_summary": "SUCCEEDED, 5 chars (see log)", "full_result_text": "Flask"},
    {"round": 4, "tool": "finish", "outcome_summary": "finished",
     "full_result_text": "The project uses SQLite as the database technology and Flask as the web framework."}
  ],
  "summary": "The project uses SQLite as the database technology and Flask as the web framework."
}
```

Real remote `qwen3:8b` lead, real logged-in `claude` teammate, literal
`delegate → fact_check → delegate → finish` order, 4 rounds, ~58 seconds
wall time, zero malformed retries, no repeated delegation. `teammate_
sessions` shows one `session_id` shared across both `delegate` calls,
confirming the second delegation correctly resumed the first's session
(the same continuity mechanism `docs/spec.md` §12 describes, unchanged
from 6a).

## Known limitations

- **`codex` remains unauthenticated in this environment** — 401 from
  `api.openai.com`, same disclosed limitation 6a already carried. Both the
  `{schema_file}` tier-2 delivery form and `codex.engine`'s own headless
  plumbing beyond process-spawn/NDJSON-capture/exit-code are unverified
  against the real CLI; verified instead via `RealTmuxSchemaTests`' stand-
  in engine and 6a's own existing coverage respectively.
- **`aider`'s tier-3 status remains UNVERIFIED against the real CLI** —
  `aider` is not installed here, same disclosed limitation 6a already
  carried for `aider.engine`. Tier 3's *loop* logic is verified real, end
  to end, through real tmux, against shell-script stand-ins.
- **`ask_user`'s 2–4-option count is not enforced** — unchanged from round
  1, see "Deviations from spec" #5.
- **No locking between two teams started against the same project
  directory at once** — inherited, explicit non-goal (6d's worktrees are
  the real fix).
- **The repeated-delegation mitigation is a prompt-level nudge on a small
  model, not a hard guarantee** — see "Fix 2" above's own honest framing.
- Round 3 introduced no new known limitations — Finding #1 was a
  correctness bug now fixed and regression-tested; Finding #2 was a
  documentation gap now closed.

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax/compile | `python3 -m py_compile app/app.py app/teams.py tests/test_teams_lead.py` | clean |
| `app/app.py` diff scope | `git diff --stat -- app/app.py` | 23 insertions / 3 deletions, unchanged since round 1 |
| Full suite, 3 consecutive clean runs this round | `uv run --with pytest python -m pytest tests/ -q` | **588 passed** every run (476 pre-existing + 112 in `test_teams_lead.py`), no flake |
| Pre-existing headless suite untouched | `git diff -- tests/test_teams_headless.py` | empty diff; `test_teams_headless.py`'s own `_build_headless_argv()` tests (arg/resume/file-mode cases, none passing `schema=`) still pass unmodified against the new single-pass code |
| New test file alone | `uv run --with pytest python -m pytest tests/test_teams_lead.py -q` | 112 passed |
| **Finding #1: both reviewer repros, manually reproduced against the fix, then encoded as permanent regression tests** | see "Round 3, Finding #1" above | manual repro: schema JSON round-trips untouched, no path leak; `session_id` arrives unmangled. Automated: `BuildHeadlessArgvSinglePassSubstitutionTests` (4 tests), `SubstituteHeadlessTokensPureTests` (3 tests) — all pass |
| **Finding #1: no other instance of the bug pattern in `app/teams.py`** | `grep -n "\.replace(" app/teams.py`, read every call site | confirmed: only `_build_headless_argv()` chained multiple substitutions over one growing string; `_resume_fragment()`/`_resolve_schema_fragment()` are each a single token substituted once into their own separate template |
| **Finding #2: literal AC #2 sequence, real run** | see "Round 3, Finding #2" above | `status: finished`, literal `delegate → fact_check → delegate → finish`, 4 rounds, ~58s, zero malformed retries, teammate session continuity confirmed across both delegate calls |
| **Fix 1 (round 2): real tier-2 lead run, real logged-in `claude` CLI** | see "Fix 1" above | `status: finished`, `fact_check` → `finish`, 2 rounds, ~9s, zero malformed retries |
| **Fix 1: `roster()` surfaces a misconfigured schema flag at build time** | `RosterTests.test_schema_flag_config_error_surfaced_at_roster_build_time`, `SchemaFlagConfigErrorAgentRunTests`, `CliTeamStartSchemaConfigErrorTests` | all pass; `agent_run()` raises before spawning, `team-start` rejects before running, `roster()`'s own `schema_flag_error` field is populated |
| **Fix 1: inline-schema arg-size interaction** | `ValidatePromptSizeSchemaInteractionTests` (3 tests) | summed correctly in `arg` mode, checked independently in `stdin`/`file` mode, existing (no-schema) behavior byte-for-byte unchanged |
| **Fix 1: real tmux, both placeholder forms** | `RealTmuxSchemaTests` (3 tests) | `{schema_file}` writes a real file with `0o644`/correct content, cleaned up after; `{schema}` arrives as one correctly shell-quoted argv element that round-trips to the exact schema dict |
| **Fix 2 (round 2): real reverification, 3 live runs** | see "Fix 2" above | 3/3 completed in exactly 2 rounds, no repeat (round 1's own live run needed 3 rounds with a repeat) |
| **Fix 2: mitigation clause + explicit summary, unit-tested** | `DelegationHistoryMitigationTests` (2 tests), updated `DelegateBookkeepingTests` | clause present every tier; `args_summary`/`outcome_summary` state agent/task/SUCCEEDED-or-FAILED explicitly |
| Real tier 1 (unchanged from round 1) — `fact_check → finish` | round 1's own command, re-runnable | still `status: finished`, unaffected |
| Real tier 3, stand-in fixtures, real tmux (unchanged from round 1) | `RealTmuxTier3StandInTests` (3 tests) | unaffected, still pass |
| `team-resolve`/`team-resume` in a separate process (unchanged from round 1) | `ResolveInSeparateProcessTests`, `ResumeAfterMidDelegateCrashTests` | unaffected, still pass |

## How to verify locally

```bash
# Full suite (run more than once)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# New test file alone
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_teams_lead.py -v

# Confirm app/app.py's diff is limited to the two new Engine fields
git diff --stat -- app/app.py

# Roster, against this repo's own engines.d -- confirm claude/codex are
# tier 2 with schema_flag_error: null
export TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x
export ENGINES_DIR=$(pwd)/engines.d PROJECTS_DIR=/tmp/scratch-projects-6c
python3 app/teams.py roster

# Real tier-2 run against a real, logged-in claude CLI (Fix 1)
mkdir -p /tmp/scratch-projects-6c/demo/docs
printf '# Architecture\n\nUses SQLAlchemy as the ORM.\n' > /tmp/scratch-projects-6c/demo/docs/ARCHITECTURE.md
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "Verify (using fact_check) that this project uses SQLAlchemy, then finish with a one-sentence summary." \
  --lead claude --members ""

# Real tier-1 run against the operator's tailnet Ollama, with a real
# delegate to claude (Fix 2)
export TEAM_STATE_DIR=/tmp/scratch-team-state-6c
export TEAM_LLM_BASE_URL=http://100.70.98.74:11434/v1 TEAM_LLM_MODEL=qwen3:8b
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "Delegate to the teammate named claude: ask them to reply with exactly one word describing what kind of app this is, based on the project docs. Then finish with that word in your summary." \
  --lead-ollama --members "claude"

# Inspect a run afterward
python3 app/teams.py team-status <run_id>

# The literal AC #2 chained sequence (Finding #2) -- delegate -> fact_check
# -> delegate -> finish in one real run
python3 app/teams.py team-start /tmp/scratch-projects-6c/demo \
  --task "First delegate to the teammate named claude: ask them to name the project's database technology in one word, based on the project docs. Then use fact_check to verify that this project uses SQLAlchemy. Then delegate to claude again: ask them to name the web framework in one word. Then finish with a one-sentence summary covering both answers." \
  --lead-ollama --members "claude"
```

---

# Implementation: Team session lifecycle, part 1 -- worktrees + tmux dashboard session (sub-spec 6d, part 1 of 2)

## Summary

`app/teams.py` gains the full worktree + tmux-dashboard lifecycle described
in `docs/spec.md`: `_run_run_user_command()` (the TMUX-only synchronous
RUN_USER helper git worktree operations go through), `_validate_project_for_
team()` (SVC_USER, read-only preconditions), `_worktree_path()`/
`_create_worktree()`/`_remove_worktree()`, `_agent_log_path()`,
`_team_session_name()`/`_create_team_session()`, `launch_team()`/
`stop_team()`/`sweep_dead_teams()`, three new CLI subcommands (`team-launch`,
`team-stop`, `team-reap`), `_new_state()`'s two additive fields
(`project_name`, `worktrees`), and `team_step()`'s `delegate` branch now
targeting a teammate's own worktree with a stable, append-mode log path.
`app/app.py` gains exactly the one conceptual change the spec named: the
`switchboard`-only engine-name reservation becomes `(switchboard, team)`
(`_RESERVED_ENGINE_NAME_PREFIXES`) -- `app.py` still does not import
`app.teams` anywhere, confirmed by grep.

Four real, previously-undisclosed defects were found and fixed while
building/reviewing this, all by exercising real git + real tmux sequences
past the spec's own enumerated acceptance criteria (per this story's own
established pattern) -- two found by the developer, two (both in the
developer's own successive fixes for the first) found by the reviewer --
see "Defects found and fixed" below. All hard constraints (no new sudoers
line, `git worktree remove` never uses `--force`, grounding/`realpath()`
guards untouched) hold, verified directly, not assumed.

## Changes by file

- **`app/teams.py`** (2859 -> 3661 lines, including round 2's atomic-
  session-creation fix and round 3's failing-link cleanup fix, plus both
  rounds' docstring updates):
  - Two new config constants: `TEAM_WORKTREE_OP_TIMEOUT_SECONDS` (30s
    default), `TEAM_SESSION_STALE_TTL_SECONDS` (86400s default) -- same
    declare-once-at-module-level convention as every other `TEAM_*`
    constant, plus `_WORKTREE_OP_KILL_GRACE_SECONDS` (fixed, not an env
    var, same rationale as `_GROUNDING_READ_CAP_BYTES`).
  - `_run_run_user_command(argv, cwd, timeout=None)` -- the TMUX-only
    synchronous RUN_USER helper (spec §1). Spawns a throwaway
    `switchboard-worktree-op-<id>` tmux session running `argv...; echo $? >
    rcfile` as a background job (mirrors `_build_script()`'s own
    background-then-`wait`-then-record-rc idiom), polls for the rc file the
    same way `_run_headless_session()` polls for `out.rc`, and escalates
    TERM-then-`kill-session` (a single stage, not the full multi-stage
    ladder) if the command outlives `timeout`. Never raises.
  - `_validate_project_for_team(workdir)` -- SVC_USER, plain
    `subprocess.run(["git", "-C", workdir, ...])`, three ordered checks
    (not-a-repo / detached HEAD / dirty tree via `git status --porcelain`
    non-empty, tracked-or-untracked per the user's settled decision), each
    with its own specific message.
  - `_worktree_path()`/`_create_worktree()`/`_remove_worktree()` -- verbatim
    per spec §3, including the specific "a previous team run's worktree for
    '<agent>' still has uncommitted changes at <path>..." leftover message
    and the three-way `_remove_worktree()` outcome (`"removed"`/`"dirty"`/
    `"error"`), classified from git's own real stderr text (`"modified or
    untracked files"`/`"use --force"`), verified against the real binary,
    not assumed.
  - `_agent_log_path()`, `_team_session_name()`, `_create_team_session()`
    (dashboard windows: `lead` + one per member, each `tail -n +1 -F
    {log_path} || sleep infinity`, `remain-on-exit on`).
  - `_TEAM_SESSION_RUN_ID_OPTION`/`_team_session_run_id()`/
    `_kill_team_session_if_owned()` -- **not named in the spec**, added to
    fix a real defect found during testing; see "Defects found and fixed"
    below.
  - `launch_team()`/`stop_team()`/`sweep_dead_teams()` -- per spec §7-§9,
    with one additional structural fix each (dropping a reclaimed
    worktree's entry from the persisted `worktrees` map) -- see "Defects
    found and fixed".
  - `_new_state()` -- two additive keyword-only fields, `None`/`{}` default,
    every existing positional caller (6c's own `_cli_team_start()`, every
    existing test) unaffected.
  - `team_step()`'s `delegate` branch -- the one behavioral change to
    existing lead-loop code, exactly as specced: `worktree =
    state.get("worktrees", {}).get(agent)`, `agent_run(agent, worktree or
    state["workdir"], task, ..., log_path=_agent_log_path(...))`. A run with
    no `worktrees` entry (every 6c test, any bare `team-start`) falls back
    to `state["workdir"]`, byte-for-byte unchanged.
  - Three new CLI subcommands: `team-launch` (copy-pasted `--lead`/
    `--lead-ollama`/`--members` validation from `team-start`, per spec's own
    explicit sanction of duplication over refactoring the existing,
    reviewed `_cli_team_start()`), `team-stop`, `team-reap`.
  - `import calendar` added (for `_iso_to_epoch()`, used by
    `sweep_dead_teams()`'s TTL age calculation).
- **`app/app.py`** -- `_RESERVED_ENGINE_NAME_PREFIXES = ("switchboard",
  "team")` (new module constant) and `_parse_engine_file()`'s single
  `if name.startswith(...)` check now uses it. `git diff --stat -- app/
  app.py`: 23 insertions / 11 deletions -- larger than a literal one line
  because the surrounding comment explaining the collision was expanded to
  cover both prefixes (the spec's own "Summary" describes this as "one
  small, additive **change**", i.e. one conceptual change, not a literal
  one-line diff -- 6c's own AC requiring a literal minimal diff was scoped
  to 6c, not repeated verbatim as an AC in this spec). No route, no
  template/JS, no new import of `app.teams` -- confirmed by grep.
- **`config/switchboard.env.example`** -- `TEAM_WORKTREE_OP_TIMEOUT_
  SECONDS`, `TEAM_SESSION_STALE_TTL_SECONDS`, same commented-out-with-
  explanation style as every existing `TEAM_*` block.
- **`docs/ADDING_AN_ENGINE.md`** -- "Reserved name" section renamed
  "Reserved name**s**" and extended to name both prefixes and both
  collision shapes they guard against.
- **New test file**: `tests/test_teams_lifecycle.py` (50 tests -- 47 from
  the first pass, `CreateTeamSessionAtomicStampTests` (round 2) and
  `CreateTeamSessionFailingLinkCleanupTests` (round 3, 2 tests) added in
  response to review) -- see "Verification status" below.

## Key decisions / tradeoffs

- **`_run_run_user_command()`'s rundir/session naming/cleanup mirrors
  `agent_run()`'s own shape exactly** (a scratch dir under
  `TEAM_STATE_DIR/_worktree_ops/<id>/`, `out`/`err`/`pid`/`rc` files, a
  `finally: shutil.rmtree(...)` + best-effort `kill-session`) -- reused, not
  reinvented, even though the function itself is much smaller (no NDJSON
  translation, no multi-stage cancellation ladder), per the spec's own
  explicit instruction.
- **`_remove_worktree()`'s "dirty" classification is a substring match
  against git's own real stderr** (`"modified or untracked files"` /
  `"use --force"`), verified directly against the real `git` binary (both
  the tracked-and-uncommitted and untracked-only cases) rather than assumed
  from documentation -- see `tests/test_teams_lifecycle.py`'s
  `CreateRemoveWorktreeRealGitTests`.
- **`launch_team()`'s permission pre-touching order matches the spec's own
  reasoning exactly**: `_run_dir()`/`agents/` chmod'd and every log file
  pre-touched+chmod'd 0644 *before* `_create_team_session()` is called, so
  no dashboard window's `tail -F` ever races file creation. Verified for
  real under a strict `umask(0o077)` (`LaunchTeamWorldReadableUnderStrict
  UmaskTests`), reusing 6a's own fixture technique.
- **`_cli_team_launch()` duplicates `_cli_team_start()`'s lead-resolution
  block rather than extracting a shared helper** -- the spec's own words
  ("copy-pasted validation ... reused, not reinvented") read as an explicit
  instruction to duplicate rather than touch the existing, reviewed
  `_cli_team_start()` at all; matches minimal-diff discipline for existing
  code.

## Defects found and fixed (not in the spec's own enumerated cases)

Per this story's own established pattern ("every defect so far was found by
probing past the spec's enumerated cases... structural fixes beat tuned
constants, running the real thing beats reasoning about it"), building this
part surfaced two real defects, both found by exercising real git + real
tmux sequences beyond the acceptance criteria's own literal cases, both
fixed structurally rather than patched around.

### 1. A stale, already-swept run's own state could destroy a NEWER run's live resources at the same name/path

**How it was found**: manually exercising the exact "crash, reap twice,
then launch again for the same project" sequence the acceptance criteria
describe -- but continuing one step further than the ACs literally ask for
(re-running `sweep_dead_teams()`/`stop_team()` a THIRD time, against the
OLD run_id, after a brand new run had already been launched for the same
project).

**Root cause**: `_team_session_name(project_name)` and
`_worktree_path(project_workdir, agent)` are BOTH pure functions of
*project*, not of any one `run_id` (this is correct and matches the spec
exactly -- `docs/story.md` §4's own architecture diagram names them this
way). Once an old run's own session/worktree genuinely disappears (crashed,
or cleanly reclaimed by an earlier sweep/stop pass), a completely
different, later run for the SAME project can legitimately create a new
session/worktree under the IDENTICAL name/path. The old run's own
`run.json` still records that name/path, though. A LATER
`sweep_dead_teams()`/`stop_team()` pass re-processing the OLD (still
terminal, still past its TTL) run's record would find something now
sitting at that name/path -- the NEW run's live session or worktree -- and
destroy it, genuinely believing it was reclaiming its own leftover
resource.

**Fix, in two parts, both structural**:
1. **Worktree side**: `stop_team()`/`sweep_dead_teams()` now drop a
   `"removed"`/`"absent"` agent entry from the run's own persisted
   `state["worktrees"]` map immediately after processing it (keeping only
   `"dirty"`/`"error"` entries, which are self-protected anyway --
   `_create_worktree()`'s own "path already exists" check already refuses
   to let a future launch silently overwrite a directory that's still
   genuinely there). Once dropped, that run's own future sweep passes never
   look at that path again.
2. **Session side**: this alone wasn't enough -- a session's *name* carries
   no persisted "have I already reclaimed this" state the way a worktree
   map entry does. Added `_TEAM_SESSION_RUN_ID_OPTION` (a tmux user option,
   `@switchboard_team_run_id`, stamped onto the session at creation time in
   `_create_team_session()`) and `_kill_team_session_if_owned(session,
   run_id)`, which queries that option (`tmux show-options -t session -v
   @switchboard_team_run_id`) before ever killing a session on a run's
   behalf, and leaves it completely alone if it's stamped with a
   *different* run_id. `stop_team()`/`sweep_dead_teams()`'s TTL branch both
   now go through this instead of a raw `kill-session`. No new privileged
   path -- `show-options`/`set-option` go through the same `TMUX` constant
   every other tmux call in this module already uses.

**Severity**: real resource destruction (a live team's tmux session or git
worktree silently killed out from under it by an unrelated sweep pass), not
a cosmetic issue -- but requires the TTL window to have genuinely elapsed
for an abandoned old run while a new run for the same project is live,
which needs real elapsed time (default 24h) at production defaults, not an
immediate race. Fixed structurally (ownership is now checked, not assumed)
rather than by tuning the TTL or adding a warning.

**Regression coverage**: `SessionOwnershipCollisionRealTmuxTests` (2 tests,
real git + real tmux) -- one exercising the collision via
`sweep_dead_teams()`, one via an explicit `stop_team()` call on the old
`run_id`, both confirming the new run's session and worktree survive
untouched.

### 2. `_run_run_user_command()`'s completion check raced a fast command's own session teardown

**How it was found**: not a hypothetical -- the very first real, in-process
smoke test of `_run_run_user_command()` (`echo "hello world"`) failed
intermittently, and `tests/test_teams_lifecycle.py` as a whole flaked in
6 of 10 full-file runs before the fix (well above the ~1-in-8 rate this
story's one other disclosed flake carries), each time in a different real
git/tmux test, always with the identical symptom: `{"ok": false, "error":
"command session ended unexpectedly"}`.

**Root cause**: the poll loop read `rc_path`, and -- only if that read came
back empty -- checked `tmux_has(session)` to decide "still running" vs.
"vanished without an exit code". For a command that completes in
microseconds (`echo`, `git status` against a small repo), the entire
spawn-run-write-rc-exit-teardown sequence can complete within a single
polling iteration, faster than the loop's own two separate observations
(the rc read, then the has-session check) could keep up with -- the rc file
write is always causally complete before the session tears down (bash's own
last statement is `echo $? > rcfile`, and the pane's process only exits
after that), but our OWN two reads of that fact are not atomic with each
other.

**Fix**: when `tmux_has(session)` returns `False`, re-read `rc_path` ONE
more time before concluding "ended unexpectedly" -- since the write is
causally guaranteed to have already happened by the time the session is
observably gone, this re-read always finds it if the command actually
completed. This is the same class of "the underlying invariant was never
violated, only our own observation ordering was" fix as `_build_headless_
argv()`'s round-3 single-pass-substitution fix earlier in this story
(structural, not a retry/sleep/tuned-timeout band-aid). `_run_headless_
session()` (6a, unmodified, untouched by this diff) has the theoretical
same gap but is practically unreachable there -- real engine invocations
take seconds, never microseconds, so the race window is never wide enough
to hit in practice; `_run_run_user_command()` is specifically for fast git
commands, where it hits routinely.

**Verification**: 15 consecutive clean runs of `tests/test_teams_
lifecycle.py` after the fix (0 failures, versus 6/10 before); the full
suite run 7 times this session with only the one, separately-confirmed,
pre-existing, unrelated flake (see "Verification status" below).

### 3. `_create_team_session()` left the session unstamped for a real window -- found by the reviewer, in the fix for defect #1

**Not found by the developer** -- caught by the reviewer's own testing
pass (and independently reproduced here before accepting it), in the code
defect #1's own fix introduced this same round: a third instance of the
identical class ("the underlying invariant can be observed inconsistently
by a second party mid-update"), this time in the FIX rather than in
original code.

**Root cause**: `_create_team_session()` sequenced three separate tmux
client invocations -- `new-session` (create), `set-option remain-on-exit`,
`set-option @switchboard_team_run_id <run_id>` (the stamp defect #1's fix
added). Between the first and third, the session existed on the server but
was NOT yet stamped, and `_kill_team_session_if_owned()` treats an
unstamped session as safe to kill for any `run_id`. The developer's own
original docstring claimed this "should not happen in practice... every
session is stamped before this function could ever see it" -- that claim
was false, confirmed live by both the reviewer and independently
re-confirmed here (see "Verification" below). Three things make the window
worse than a narrow theoretical gap: (1) there is no locking anywhere in
`app/teams.py` -- no `flock`, no `O_EXCL`, no lock file; (2)
`sweep_dead_teams()` runs opportunistically inside EVERY `launch_team()`/
`stop_team()` call for ANY project, not just the one being launched, so the
window is reachable from a solo crash of the creating process with no
concurrency on the SAME project required at all; (3) the consequence is not
only a killed session -- depending on timing, `launch_team()` could return
a false `{"ok": True}` for an already-dead session with no member windows,
self-healing only whenever some later sweep happened to run.

**Fix, explicitly NOT fail-closed**: the reviewer traced a concrete
solo-crash mechanism where a blanket "treat unstamped as not-safe-to-kill"
rule would strand a genuinely orphaned unstamped session permanently
(needing manual `tmux kill-session` cleanup) -- trading one failure mode
for a worse one. The correct fix removes the OBSERVABLE inconsistent state
entirely: `_create_team_session()` now creates the session, sets
`remain-on-exit`, and stamps its run_id in ONE atomic tmux client
invocation, chained via tmux's own `;` command separator (`new-session ...
; set-option ... remain-on-exit on ; set-option ... <run_id>`, `;` as its
own argv element -- no shell involved, so no escaping needed). tmux
processes an entire `;`-chained batch from one client connection without
yielding to any other client's request in between, so there is never a
moment where the session exists but isn't stamped, observable or not.
`_kill_team_session_if_owned()`'s own "unstamped is safe to kill" behavior
is kept exactly as it was -- correct now that creation is atomic (an
unstamped session is a genuine orphan), whereas it was papering over a real
race before.

**Verification**: independently re-confirmed the race BEFORE trusting the
reviewer's report, via a standalone reproduction of the old three-call
shape run alongside a tight concurrent poller watching for "session exists,
stamp absent" -- 10/10 trials observed the window against the old shape,
0/10 against the fixed one-call shape. The same property is now a permanent
regression test, `CreateTeamSessionAtomicStampTests.test_session_is_
never_observable_unstamped_during_creation` -- confirmed this test fails
reliably (0/8 trials would pass) against the old three-call shape and
passes reliably (8/8) against the real, fixed `_create_team_session()`,
not merely asserting the stamp is present after the call returns (which
the old, racy code also satisfied, just not atomically). The reviewer
independently re-ran this same test against its own faithful reconstruction
of the old three-call shape and got 9/10 (matching the developer's 10/10
within normal timing variance) plus 8/8 clean against the fix -- the
reviewer's own verdict: "the test genuinely pins the behaviour", approved.

### 4. `_create_team_session()`'s own atomic-creation fix (defect #3) never cleaned up a session that partially failed to create -- found by the reviewer

**Not found by the developer** -- logged as a should-fix by the reviewer
against round 2's own atomic-chain fix, chosen by the coordinator to close
in this same round rather than carry as a disclosed limitation.

**Root cause**: tmux's `;`-chain is atomic against a concurrent OBSERVER
(defect #3's fix; nothing else can ever see a half-stamped session) but
NOT against a failure PARTWAY THROUGH the chain itself -- these are two
different guarantees. Confirmed live, both directions: if `new-session`
itself fails (e.g. a genuine duplicate-session race), tmux aborts the WHOLE
chain before any `set-option` runs -- the good case, and what makes defect
#3's fix correct. But if `new-session` SUCCEEDS and a LATER link fails (in
practice only the run_id `set-option` realistically could, and only if
something made the option name itself invalid -- neither the hardcoded
`remain-on-exit on` nor an `@`-prefixed user option's freeform string value
is expected to fail under normal operation), tmux does NOT roll back the
already-successful `new-session`. Confirmed directly against the real
binary: `tmux new-session -d -s S ... \; set-option -t S remain-on-exit on
\; set-option -t S not-a-real-option val` exits nonzero, but `S` is left
alive, `remain-on-exit`d, unstamped. `_create_team_session()`'s own failure
branch detected this (`r.returncode != 0`) and returned an error, but never
killed the session it had just partially created.

**Consequence, exactly as the coordinator described**: a lockout with no
self-heal path -- every future `launch_team()` for that project refused by
the upfront `tmux_has()` precondition; `sweep_dead_teams()` structurally
unable to ever find it (`launch_team()` never persists a `run.json` on this
failure path, and `sweep_dead_teams()` only ever iterates recorded runs);
recovery via manual `tmux kill-session` only. The same shape as 6a's own
first defect (a fallible call sitting outside its own cleanup, leaking a
resource) -- there, a rundir; here, a tmux session. Rated should-fix, not
must-fix (a well-formed hardcoded `set-option` failing has no realistic
trigger under normal operation), but fixed anyway per the coordinator's own
reasoning: "recoverable only by manual intervention" is exactly the sharp
edge this codebase keeps engineering out, and the same defect CLASS
deserves the same structural treatment it already got in 6a, not a
narrower patch scoped to "this one unlikely trigger".

**Fix, and the ownership question worked through explicitly (per the
coordinator's own instruction to think it through, not just patch it)**:
the failure branch now kills whatever session it finds at `session` --
`_kill_team_session_if_owned()` is the WRONG tool here, because it
identifies ownership from the STAMP, and the stamp is precisely what may
never have landed on this failure path. Ownership is instead established
structurally, from the calling context itself, not from any tmux-side
state: this function's own upfront `tmux_has(session)` check already
confirmed no session with this exact name existed the instant this call
started, and (confirmed above) a failing `new-session` aborts before any
`set-option` runs, so a concurrent second caller for the SAME project can
never reach this cleanup branch holding someone ELSE's session at this
name -- either `new-session` failed for THIS call (nothing exists to clean
up) or it succeeded (only this call created whatever exists at this name).
So `tmux_has(session)` being true on this failure path can only mean "this
exact call's own partially-created session" -- a direct, unconditional
`kill-session` is correct and safe without consulting a stamp that may not
exist. Both function docstrings (`_create_team_session()`'s own, and a
forward-reference from `_kill_team_session_if_owned()`'s) spell out this
reasoning so it isn't re-derived from scratch by a future reader.

**Regression coverage**: `CreateTeamSessionFailingLinkCleanupTests` (2
tests) -- reproduces the failing-link path FOR REAL (not mocked) by
monkeypatching `_TEAM_SESSION_RUN_ID_OPTION` to a non-`"@"`-prefixed name,
which makes the real tmux `set-option` call itself genuinely fail with
"invalid option", exercising the actual code path. One test asserts no
session survives the failure; the other proves the lockout consequence
directly -- a SECOND `_create_team_session()` call for the same project
succeeds cleanly right after the first one's failure, which it could not
have done against the old (leaking) code. Independently verified the first
test fails against a reverted (no-cleanup) copy of the function before
trusting it as a regression test, mirroring the same discipline used for
defect #3's own test.

## Deviations from spec

None found to be genuine deviations from the letter of `docs/spec.md` --
the two items above are **additions** (new functions/logic not named by the
spec) fixing defects the spec's own design didn't anticipate, not
departures from what the spec specified. Every function signature, message
string, and control-flow shape named explicitly in "Proposed approach" (§1-
§11) was implemented as written. One assumption made where the spec is
silent: **`launch_team()`'s `project_name` is derived as
`os.path.basename(os.path.normpath(workdir))`** -- the spec never states
this derivation explicitly (only that `_worktree_path()`/session names are
keyed off "the project"), but it is the only reading consistent with
`app.py`'s own existing `active_engine()`/`instance_start()` convention
(`workdir = PROJECTS_DIR/<name>`, session `f"{engine}-{name}"`), and is what
makes `docs/spec.md`'s acceptance criterion ("a `team-<project>` tmux
session") concrete.

## Known limitations

- **A real unstamped-session race window existed in this cycle's own first
  pass and is now closed** -- see "Defects found and fixed" #3. Disclosed
  here explicitly (not just in that section) because the window itself was
  never called out anywhere before the reviewer found it, and this section
  is supposed to be the complete, honest list of what's still true about
  the shipped code: as of the fix, it is NOT still true (the atomic-
  creation fix + its regression test close it), but the omission itself is
  worth naming so it isn't silently dropped from this cycle's own record.
- **`launch_team()`'s `project_name = os.path.basename(os.path.normpath
  (workdir))` yields an empty string for a filesystem-root `workdir`**
  (`os.path.basename(os.path.normpath("/")) == ""`), which would produce a
  session literally named `"team-"` -- a real, if extremely unlikely, edge
  case (non-blocking nit, logged by the reviewer). **Decision: left
  as-is, not hardened.** In every real path that reaches `launch_team()`
  (this cycle's own CLI, and part 2's future HTTP route), `workdir` is
  always `PROJECTS_DIR/<name>` where `<name>` already passed this
  codebase's own `NAME_RE` validation elsewhere -- it is never a bare `/`.
  The only way to hit this at all is calling `launch_team("/", ...)`
  directly as a library function with a project actually rooted at the
  filesystem root (and even then, `_validate_project_for_team("/")` would
  need to actually pass -- a real git repo living at `/` with a clean,
  non-detached HEAD -- which is already an exceptionally unusual host
  configuration in its own right, gating this out in practice before
  `project_name` is ever computed). Hardening it (e.g. rejecting an empty
  `project_name` explicitly) would be a one-line, low-risk addition, but
  adding a check with no reachable, real-world caller to protect against
  is exactly the kind of speculative hardening this codebase's own
  "minimal diff, no drive-by additions" discipline argues against; revisit
  if part 2's HTTP route ever accepts an operator-supplied path rather than
  a `PROJECTS_DIR`-relative name.
- **Part 2's own scope is untouched, as specced**: no HTTP routes, no
  `app.py` import of `app.teams`, no background driving thread, no
  `_reap_dead_state()` wiring, no `install.sh --with-ollama`. A human still
  drives a launched team via `team-resume <run_id>` from a shell.
- **The four-things-don't-stop-together footgun disclosed in the spec is
  real and unchanged**: running `team-stop` while a `team-start`/
  `team-resume` process for the same `run_id` is active in another terminal
  can still race that process's own in-flight `agent_run()` call, exactly
  as `docs/spec.md` "What 'stopping a team' does and does not stop" already
  discloses. Not addressed here -- explicitly part 2's job (a real
  cancellation channel the driving thread can observe).
- **6c's own three carried-forward limitations** (codex tier 2 unverified
  end to end; repeated delegation mitigated, not fixed; the `None`-mapping-
  value follow-up) remain untouched, as the spec itself states this cycle
  never touches the lead loop/tier adapters/`_build_headless_argv()` (true
  except for the one, unrelated, `_run_run_user_command()` fix above, which
  is new code in a new function, not a change to any of those three areas).
- **The tier-1/tier-2/tier-3 real-model verification this file's 6c section
  documents was not re-run this cycle** -- 6d part 1 never touches
  `_call_lead()`/the adapters, and the one delegate-branch change (worktree
  + `log_path`) is exercised for real in `TeamRunDelegateWorktreeAndDashboard
  Tests` with a scripted stand-in engine (real tmux, real `agent_run()`
  call, real file-placement/session-continuity check), not against a real
  `claude`/`codex`/Ollama endpoint -- disclosed here rather than silently
  reused from 6c's own verification.

## What was mocked vs. exercised for real

Per this role's own "not testable is a claim to verify" discipline: nothing
in this cycle's core logic was left mocked without first trying the real
thing.

- **Real, not mocked**: every git operation (`git init`/`worktree add`/
  `worktree remove`/`status`/`symbolic-ref`/`branch --list`) against real
  temp repositories; every tmux operation (`new-session`/`new-window`/
  `list-windows`/`capture-pane`/`kill-session`/`show-options`/`set-option`)
  against a real, locally-running tmux server (TMUX patched from `["sudo",
  "-u", RUN_USER, "/usr/bin/tmux"]` down to `["tmux"]`, same technique
  `tests/test_teams_headless.py` already established -- no sudo needed for
  the test suite; the CLI-subprocess tests deliberately do NOT patch this,
  relying on the same real `sudo -u $RUN_USER tmux` path 6c's own
  `ResolveInSeparateProcessTests` already proved works in this environment);
  a real strict `umask(0o077)` for the permission test.
- **A scripted stand-in engine (`fake_teammate.py`), not the real `claude`/
  `codex`/`aider` CLI**, for the delegate-worktree-continuity test --
  matches 6a's own `RealTmuxHeadlessTests` precedent (script-based stand-ins
  for the actual engine binary, real tmux/real `agent_run()` around them).
  The real `claude`/`codex` binaries were not re-exercised through the
  worktree path this cycle (no new claim is made that they were); this is
  the same class of disclosed gap 6c already carries for `codex`/`aider`.
- **A stub, unreachable `TEAM_LLM_BASE_URL`** for the CLI subprocess tests
  (`team-launch --lead-ollama`) -- `launch_team()` never actually calls the
  lead (it doesn't drive the loop, per spec), so the endpoint is validated
  for presence only, never dialed. Disclosed rather than silently assumed
  equivalent to a real endpoint.

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax/compile | `python3 -m py_compile app/app.py app/teams.py` | clean |
| `app.py` diff scope | `git diff --stat -- app/app.py` | 23 insertions / 11 deletions, entirely the reservation-tuple change + its comment |
| `app.py` imports `app.teams`? | `grep -n "import teams\|app\.teams" app/app.py` | no matches -- confirmed absent, per spec |
| Pre-existing suites byte-for-byte unmodified | `git diff --stat -- tests/test_teams_headless.py tests/test_teams_lead.py tests/test_teams_grounding.py` | empty -- all three untouched |
| New test file alone, 29 consecutive runs across three rounds | `pytest tests/test_teams_lifecycle.py -q` x15 (round 1, post defect #2 fix, 47 tests) + x8 (round 2, post defect #3 fix, 48 tests) + x6 (round 3, post defect #4 fix, 50 tests) | passed every single run, 0 flakes any round |
| Full suite, 17 runs across three rounds | `pytest tests/ -q` | round 1: 6/7 clean at 635 passed; round 2: 4/5 clean at 636 passed; round 3: 5/5 clean at **638 passed** (588 baseline + 50). 2 runs total (both rounds 1/2) hit the pre-existing, disclosed `test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask` flake in `test_teams_headless.py` (untouched by this diff -- `git diff --stat -- tests/test_teams_headless.py` empty), independently confirmed to pass in isolation immediately after every time -- not attributable to this change; 0 such flakes in round 3's own 5 runs |
| No new sudoers/privileged path | `git diff -- app/teams.py \| grep -iE "sudo\|subprocess\.(run\|Popen)"` on added lines, manually reviewed | every call outside `TMUX + [...]` is one of the three read-only SVC_USER `git -C workdir ...` precondition checks in `_validate_project_for_team()` -- confirmed, no new crossing |
| `git worktree remove` never uses `--force` | `grep -n '"remove"' app/teams.py`, `grep -n force app/teams.py` | only reachable use of `--force` is inside a human-facing error MESSAGE (the manual-cleanup suggestion), never in an actual argv passed to git |
| Grounding read-only guards untouched | `git diff --stat -- tests/test_teams_grounding.py` | empty |
| `realpath()` fallback section untouched | `grep -n realpath app/teams.py`, compared against pre-diff | unchanged, this diff doesn't touch that section |
| Deploy stays manual-click-only | `git diff -- app/teams.py \| grep -i deploy` | no matches -- unrelated to this diff |
| Real git `git worktree remove` dirty-message text | manual repro (see report), `CreateRemoveWorktreeRealGitTests` | confirmed live: `"contains modified or untracked files, use --force to delete it"`, both tracked-and-uncommitted and untracked-only cases |
| Defect #1 (session/worktree ownership collision) | `SessionOwnershipCollisionRealTmuxTests` (2 tests), manually reproduced before the fix and re-verified after | fixed, regression-tested |
| Defect #2 (`_run_run_user_command()` fast-command race) | 15x `test_teams_lifecycle.py` full-file reruns, manual repro before/after | fixed, 0/15 flakes after the fix vs. 6/10 before |
| Defect #3 (unstamped-session race, reviewer-found) | `CreateTeamSessionAtomicStampTests` (1 test, concurrent-poller technique); independently reproduced via a standalone old-vs-new comparison before trusting the report; reviewer's own independent re-run against its own reconstruction: 9/10 old-shape catches, 8/8 clean vs. the fix | fixed, reviewer-approved |
| Defect #4 (failing-link cleanup, reviewer-found) | `CreateTeamSessionFailingLinkCleanupTests` (2 tests), a REAL (not mocked) failing-link repro via a monkeypatched invalid tmux option name; independently confirmed the first test fails against a reverted (no-cleanup) copy of the function before trusting it | fixed; real repro confirms a leftover, unstamped session under the old code, and its absence plus a successful subsequent launch under the fixed code |

## How to verify locally

```bash
# Full suite (run several times -- one pre-existing, disclosed flake in
# test_teams_headless.py, unrelated to this diff, appears roughly 1 run in 8)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# New test file alone
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_teams_lifecycle.py -v

# Confirm app/app.py's diff is limited to the reservation-tuple change
git diff --stat -- app/app.py
grep -n "import teams\|app\.teams" app/app.py   # expect: no matches

# Real end-to-end smoke test against a real scratch git repo (no sudo --
# run as whichever user has a locally-running tmux; substitute a real
# RUN_USER-privileged environment to exercise the real sudoers crossing)
mkdir -p /tmp/scratch-6d/demo && cd /tmp/scratch-6d/demo
git init -q && echo hi > README.md && git add README.md && git commit -q -m init
python3 /home/dev/projects/ai-dev-switchboard/app/teams.py team-launch \
  /tmp/scratch-6d/demo --task "do the thing" --lead-ollama --members "claude,codex"
tmux list-windows -t team-demo
git worktree list
python3 /home/dev/projects/ai-dev-switchboard/app/teams.py team-stop <run_id>
python3 /home/dev/projects/ai-dev-switchboard/app/teams.py team-reap
```

---

# Implementation: Team session lifecycle, part 2a -- web routes, background driving thread, cooperative cancellation (sub-spec 6d, part 2a of 2)

## Summary

`app/app.py` now imports `app/teams.py` for the first time (`import teams`,
placed immediately after `active_engine()`'s definition and before
`_session_urls`, per `docs/spec.md` §1's exact anchor point). Two new POST
routes, `/projects/<name>/team/start` and `/projects/<name>/team/stop`, wire
part 1's `launch_team()`/`stop_team()` into the web UI: `start` computes a
deterministic **default** team composition (`teams.default_team_
composition()`, new) and runs `team_run()` on a daemon `threading.Thread`,
tracked in a new in-memory table (`_team_threads`); `stop` synchronously
calls `stop_team()` and, if a live thread is tracked, sets a
`threading.Event` so an in-flight round is interrupted rather than waited
out. `agent_run()`, `_run_headless_session()`, `_call_lead()`, `team_step()`,
and `team_run()` each gained one additive, default-`None`, keyword-only
`cancel_event` kwarg -- the actual cooperative-cancellation mechanism.
`_reap_dead_state()` gained a throttled call to a new `_team_reap_if_due()`,
which runs `sweep_dead_teams()` and a new orphan check (a run recorded
`"running"` with no matching, alive thread in `_team_threads` is marked
`"error"` via a new `teams.mark_run_error()`). A minimal per-project
`teamRow()` control (task textarea, Start/Stop buttons, coarse status label)
was added to the embedded page script, styled after `deployRow()`. `install.
sh` now also copies `teams.py` to `$INSTALL_DIR` -- previously it copied only
`app.py`, which would have made a real install crash on startup the moment
`app.py` gained `import teams`.

Two real, previously-undisclosed findings surfaced while building this, both
by exercising the real thing rather than trusting the spec's/an existing
test's own stated assumption -- see "Findings" below. Neither required a
design change; both were fixed structurally (a CSS class rename, a test
assertion broadened to accept either of two legitimately-launch_team()-owned
error messages) rather than patched around.

## Changes by file

- **`app/app.py`**:
  - `TEAM_REAP_POLL_INTERVAL_SECONDS` (new constant, default `60`), placed
    next to `GITEA_POLL_INTERVAL_SECONDS` per spec, same
    declare-once-at-module-level convention.
  - `import teams` (`app.py`'s own first import of `app.teams`), `_team_
    threads` (new in-memory table), `_run_team_in_background()`, `_team_
    reap_lock`/`_team_reap_last_at`/`_team_reap_if_due()` -- all placed
    immediately after `active_engine()`, before `_session_urls`, per spec §1
    (the exact neighborhood the spec says both the import and the new
    in-memory state belong in).
  - `_reap_dead_state()` gains one call to `_team_reap_if_due()`.
  - `do_GET`'s `/status` handler: each per-instance dict gains an
    always-present `team` field (`{"status": ..., "run_id": ...}`), built
    from `teams.latest_run_for_project(n)`, unthrottled (per spec §5 --
    freshness matters there, only the sweep itself is throttled).
  - `do_POST` gains two new branches: `/projects/<name>/team/start` and
    `/projects/<name>/team/stop`, matching the spec's own pseudocode
    verbatim (§5).
  - Embedded `<style>`: `.team-row`/`.team-textarea`/`.team-actions`/
    `.team-status` (+ four `.status-*` color variants)/`.team-sub`/
    `.team-msg` (+ `.success`/`.error` variants), plus a **new** `.team-btn`
    class (see "Findings" #1 below for why this is NOT a literal reuse of
    `.deploy-btn`, despite the design doc's "same class/styling as Deploy
    button" wording).
  - Embedded `<script>`: `teamTaskText` (new per-project client-state
    object, survives `refresh()`'s own full-row re-render the same way
    `engineChoice` already does), `teamRow(name, team)` (called from
    `row()` for `kind === 'inst'`, after `deployRow()`), `doTeamStart(name)`,
    `doTeamStop(name)`; `actionPath()` gains `team-start`/`team-stop`
    branches; `actionBody()` gains a `team-start` branch reading the task
    textarea's current value; `handleActionResult()` gains a combined
    `team-start`/`team-stop` branch (placed BEFORE the generic `r.status
    === 400` branch, since `/team/start`'s own validation failures return
    400 and must land in the team row's own message slot, not the
    unrelated `new-project-err` field).
  - `git diff --stat -- app/app.py`: 311 insertions / 0 deletions (this
    file gains no removals -- every existing line is untouched).

- **`app/teams.py`**:
  - `default_team_composition() -> dict` (new) -- built entirely on
    `roster()`/`_lead_tier_for_engine()`/`_schema_flag_config_error()`, per
    spec §2. Implements the user-settled priority order (Ollama if
    configured -> first sorted tier-2-with-no-schema-error engine -> refuse
    if only tier-3 engines exist -> refuse if no roster member at all) and
    the "only one engine, it became lead" empty-members refusal. See "Key
    decisions" for the one small extension beyond the spec's literal words
    (a misconfigured-tier-2-only roster, no genuine tier-3 present, folds
    into the generic "no roster member is available" message rather than
    the tier-3-specific one).
  - `latest_run_for_project(project_name) -> dict | None` (new) -- O(all
    runs) scan per spec §4, skips unreadable/corrupt run.json and runs with
    no `project_name`, same discipline `sweep_dead_teams()` already uses.
  - `mark_run_error(run_id, message) -> None` (new) -- unconditional status/
    error overwrite + persist, used by both `app.py`'s background-thread
    wrapper and `_team_reap_if_due()`'s orphan check.
  - `_run_headless_session(..., cancel_event=None)`, `agent_run(...,
    cancel_event=None)`: one new `if` branch in the existing escalation
    chain (`cancel_event.is_set()` as a second trigger for the identical
    TERM->KILL->kill-session ladder the timeout path already drives,
    `cancel_reason="stopped"`), passed straight through.
  - `_call_lead(state, system, round_context, *, cancel_event=None)`:
    threaded into the tier-2/tier-3 `agent_run()` call sites; tier 1
    accepts but ignores it (no subprocess to signal).
  - `team_step(state, *, cancel_event=None)`: two new checkpoints, both
    additive -- immediately after `_call_lead()` returns (checked FIRST,
    ahead of every existing branch) and immediately after the delegate
    branch's own `agent_run()` call returns (before the SUCCEEDED/FAILED
    framing).
  - `team_run(state, *, cancel_event=None)`: one new checkpoint at the top
    of the loop, alongside the existing `max_rounds` check.
  - `launch_team()`: carried-forward one-line guard from part 1 (`docs/
    spec.md` §8) -- refuses with `{"ok": False, "error": "workdir has no
    derivable project name"}` if `os.path.basename(os.path.normpath
    (workdir))` is empty.
  - `git diff --stat -- app/teams.py`: 239 insertions / 16 deletions (the
    16 deletions are entirely the widened function signatures shown below
    -- no existing line of *logic* was removed or reordered).
  - `git diff -- app/teams.py | grep -E "^[+-]def "` confirms: every
    changed `def` is either a brand-new function or an existing one gaining
    only additive, keyword-only parameters -- no positional-argument shape
    changed for any existing function.

- **`install.sh`**: one new unconditional line, `cp "$REPO_DIR/app/teams.py"
  "$INSTALL_DIR/teams.py"`, immediately after the existing `app.py` copy in
  the "-- App + engines --" block (§6).

- **`config/switchboard.env.example`**: new "team session lifecycle -- web
  routes + driving thread (6d pt 2a)" section, `TEAM_REAP_POLL_INTERVAL_
  SECONDS`, same commented-out-with-explanation style as every existing
  `TEAM_*` block.

- **New test files**:
  - `tests/test_teams_cancel.py` (15 tests) -- pure (`team_step()`'s two
    checkpoints, `team_run()`'s loop checkpoint, `_call_lead()`'s tier-1
    ignore-but-accept behavior, each also with an explicit default-`None`
    byte-for-byte-unchanged case) + real tmux/subprocess (`agent_run(...,
    cancel_event=...)` against a real slow stand-in, both the TERM-succeeds
    and TERM-ignored-escalates-to-SIGKILL cases; `team_run()`'s own
    checkpoints exercised end to end with `cancel_event.set()` fired from a
    SECOND thread mid-call, for both a mid-delegate stop and a mid-lead-call
    stop).
  - `tests/test_team_routes.py` (18 tests) -- real `ThreadingHTTPServer` +
    real `urllib.request.urlopen()`, mirroring `DeployEndpointTests`'
    technique: both routes' happy paths (tier-2 default, Ollama-configured
    default), the tier-3-only refusal (naming both fixes, no side effects),
    unknown-project/missing-task 400s, the two-near-simultaneous-starts real
    concurrency race, the mid-delegate-stop timing case (real, slow
    stand-in engine, real SIGTERM), `/status`'s `team` field mapped across
    every run status, the service-restart simulation (both the
    still-tears-down-real-resources case and the running-then-error
    `/status` case), the legitimate-concurrent-CLI-`team-resume`
    self-correction case (a REAL, separate subprocess), the explicit
    CLI-`--lead`-still-accepts-tier-3 regression guard, and `install.sh`'s
    new `teams.py` copy line (block-extraction technique, mirrors
    `InstallShDeployMapBlockTests`).
  - `tests/test_team_frontend.js` (17 tests, plain Node, not part of the
    `pytest` count) -- mirrors `tests/test_deploy_frontend.js`'s own
    technique verbatim (real, rendered `<script>` extracted from `app.
    render_page()`, `vm`-sandboxed DOM/fetch/confirm/timer stubs):
    `teamRow()`'s four states, the empty-task client-side validation, the
    real dispatch body shape, inline success/error message rendering (both
    client- and server-side 400s), the Stop confirmation dialog's exact
    text, the TOTP 428-retry path for both actions, and an HTML-injection
    safety check for the in-progress task text.
  - `tests/test_teams_lead.py`/`tests/test_teams_lifecycle.py`: the 12
    `fake_call_lead(state, system, round_context)` test-double signatures
    (11 in the former, 1 in the latter) each gained a trailing `**kwargs` --
    mechanical, no assertion changed. Required because `team_step()` now
    unconditionally passes `cancel_event=cancel_event` into `_call_lead()`
    (identical behavior to omitting it when `cancel_event` is `None`, but a
    literal kwarg the old, narrower fake signatures didn't accept). See
    "Key decisions" for why this was the chosen fix over threading
    `cancel_event` conditionally.

## Key decisions / tradeoffs

- **`team_step()`/`team_run()` always pass `cancel_event=cancel_event`
  through to `_call_lead()`/`agent_run()`, even when it's `None`, rather
  than conditionally omitting the kwarg.** This is what caused the 12
  existing test-double signature updates above. Considered the alternative
  (only pass the kwarg when `cancel_event is not None`, so a `None`-default
  call site is a byte-for-byte identical call to before and touches zero
  existing test files) but rejected it: the acceptance criterion's own
  wording is "zero modification to their own **assertions**" (not "zero
  modification to the file at all"), and the spec's own "Test plan" text
  explicitly anticipates "`tests/test_teams_lead.py`/`tests/test_teams_
  lifecycle.py` gain a small number of additive cases ... not a rewrite" --
  a `**kwargs` addition to a test double's signature is exactly that
  category of change, not a rewrite, and keeps the *production* code
  (`_call_lead(state, system, round_context, cancel_event=cancel_event)`)
  simple and uniform rather than branching on "is this parameter worth
  passing" for cosmetic reasons.
- **`default_team_composition()`'s handling of a roster that is
  misconfigured-tier-2-only (no genuine tier-3 entry, but the only tier-2
  candidate has a `schema_flag_error`)** is not explicitly named by the
  spec's own priority-order text (which only distinguishes "genuinely tier
  3" from "nothing at all"). Folded into the generic `"no roster member is
  available to lead a team..."` refusal rather than the tier-3-specific
  message, since the tier-3-specific wording ("only a tier-3 (prose-parse,
  least reliable) lead is available") would be factually wrong for this
  combination. `roster()`'s own `schema_flag_error` field already surfaces
  the specific misconfiguration to a caller inspecting the roster directly.
  Not separately unit-tested (no acceptance criterion covers this specific
  combination) -- disclosed here as a small, reasonable gap-fill judgment
  call, not a deviation from anything the spec explicitly specified.
- **`ServiceRestartSimulationTests` uses the same-process technique
  (clearing `_team_threads` directly), not a genuinely separate OS
  process**, per the spec's own "Test plan" note leaving this "to be
  resolved by the developer, documented either way". Reasoning: `_team_
  threads` is the ONLY in-memory state a real process restart would
  actually lose from the specific angle `stop_team()`'s/`latest_run_for_
  project()`'s own restart-safety claim is about -- the tmux dashboard
  session and git worktrees are OS/filesystem-level and survive a killed
  `app.py` process unaffected either way, and `run.json` is durable on
  disk. Clearing exactly that one dict is therefore functionally
  equivalent to a real restart for everything this cycle's own acceptance
  criteria actually assert against (real `tmux_has()`/worktree-path
  checks, real `/status` truthfulness), without the added complexity and
  slowness of managing a second real `ThreadingHTTPServer` process.
- **`TeamStopEndpointTests.test_stop_mid_delegate_terminates_real_
  subprocess_promptly` wires `_run_team_in_background()`/`_team_threads`
  directly (with `_call_lead()` monkeypatched to force a deterministic,
  fast `delegate` action) rather than going through the real `/team/start`
  HTTP route for its own setup phase.** The route always resolves the
  REAL `default_team_composition()`/real, unmocked `_call_lead()` -- there
  is no way to inject a controllable fake lead response through the HTTP
  layer itself without a real, slow LLM/CLI round-trip. The STOP action
  itself (the actual subject of the test) is exercised for real, through
  the real HTTP route, against the real cancellation mechanism, real
  tmux, and a real (deliberately slow) subprocess. Disclosed here, not
  silently presented as "the route was tested end to end for this case".

## Findings (real, previously-undisclosed, found by exercising the real thing)

1. **`teamRow()`'s two buttons literally reusing the `.deploy-btn` CSS
   class broke an existing, reviewer-approved frontend test.** Design's own
   wording ("styled to match other action buttons on the page (same
   class/styling as 'Deploy' button)") reads naturally as "share the exact
   class name", which is what was implemented first -- but `tests/test_
   deploy_frontend.js`'s own `'project without a deploy-map entry renders
   no Deploy button at all'` test asserts `!html.includes('deploy-btn')`
   for ANY project with no `deploy` entry, and `teamRow()` is rendered
   unconditionally for every project (unlike `deployRow()`, which is only
   rendered when `deploy` is configured) -- so every project without a
   deploy target now failed that assertion, since its own "Start team"
   button carried `class="deploy-btn"` too. **Found by actually running
   the existing, already-passing frontend test suite after this cycle's
   own change**, not by reasoning about it -- the cross-feature interaction
   (a shared CSS class name doubling as an implicit "is deploy configured"
   signal for an unrelated test) was not something either this spec or the
   design doc anticipated. **Fixed structurally**: a new `.team-btn` class,
   sharing the exact same CSS declaration as `.deploy-btn` via a combined
   selector (`.deploy-btn, .team-btn { ... }`) so the VISUAL styling really
   is byte-for-byte identical (honoring the design's actual intent), but a
   distinct class name so the two features' own markup never collides
   again. `tests/test_deploy_frontend.js` (9/9), `tests/test_team_
   frontend.js` (17/17), `tests/test_singleton_toggle_frontend.js` (15/15),
   and `tests/test_upload_frontend.js` (8/8) all pass together after the
   fix -- verified by running all four real frontend suites, not just the
   new one.
2. **Two near-simultaneous `/team/start` requests for the same project
   don't always collide on the session-name check the spec's own "Edge
   cases" text describes.** `docs/spec.md`: "the second's `launch_team()`
   call hits the exact same session-name collision refusal part 1 already
   built and tested (`tmux_has(session)` check, before any worktree is
   touched)". Under REAL concurrency (two threads fired via a
   `threading.Barrier`, not sequential calls), the actual collision point
   observed was `_create_worktree()`'s own "path already exists" refusal
   instead -- both requests can pass the `tmux_has(session)` pre-check
   before either has actually created a session or worktree yet, so
   whichever creates its first worktree "wins" that specific check, and
   the loser fails there instead. **Not a defect**: both are legitimately
   `launch_team()`'s own collision errors (matching the acceptance
   criterion's own more general wording, "the other gets `launch_team()`'s
   own collision error" -- it doesn't name one specific message), and the
   property that actually matters -- exactly one request succeeds, the
   loser's failure leaves the winner's worktrees/session/thread completely
   untouched -- holds either way, verified directly (`state["worktrees"]`
   and `tmux_has()` checked against the winner after the race). Fixed by
   broadening the test's own assertion to accept either message rather
   than by changing any production code -- the underlying safety property
   was never violated, only the spec's own prose description of exactly
   which check fires first was imprecise under real scheduling. Confirmed
   stable across 5 consecutive runs of this specific test after the fix,
   0 flakes.

## Deviations from spec

- **`doTeamStart()` goes through the shared `toggle()`/`performAction()`/
  `handleActionResult()`/TOTP-retry plumbing (with `actionBody()`'s new
  `team-start` branch supplying `{task: ...}`), not a standalone direct
  `fetch()` call the way `docs/spec.md`'s "Proposed approach" §9 literally
  describes ("`doTeamStart()` follows `doDeploy()`'s own direct-
  `fetch()`-plus-inline-result-slot shape instead").** On inspection,
  `doDeploy()` itself does NOT make a direct `fetch()` call either -- it
  calls `toggle('deploy', name, true, null)`, going through the exact same
  shared plumbing `doTeamStart()` now also uses. The spec's own prose
  describing `doDeploy()`'s shape as "direct fetch" doesn't match
  `doDeploy()`'s actual, already-shipped implementation. Given that,
  reusing `toggle()`/`actionBody()`/`handleActionResult()` (extended with
  one new `kind==='team-start'` body-construction branch and one new
  combined `kind==='team-start'||kind==='team-stop'` result-handling
  branch, mirroring the existing `kind==='deploy'` branch's own shape
  exactly) is the smaller, more consistent diff, reuses 100% of the
  existing TOTP-retry/code-overlay machinery instead of reimplementing it,
  and produces byte-for-byte the same user-visible behavior the design doc
  specifies (inline error/success messages, empty-task client validation,
  the Stop confirmation dialog, the exact copy). `doTeamStop()` matches the
  spec's own text for it verbatim (`toggle()`-based, matching `doDeploy()`).
  Verified end to end via `tests/test_team_frontend.js`, including the
  TOTP 428-retry path for `team-start` resending the current task text.
- **`teamTaskText` (a new per-project client-state object preserving the
  operator's in-progress, not-yet-submitted task text across `refresh()`'s
  own full-row re-render) is not named anywhere in `docs/spec.md`.** Without
  it, `refresh()`'s existing 4-second poll would silently wipe whatever the
  operator was mid-typing into the task textarea on every tick (the row's
  entire HTML, including the textarea, is regenerated from scratch each
  poll) -- a real usability defect the spec's own "no new poll-interval
  timer" text doesn't address, since it's about polling cadence, not about
  DOM-replacement side effects on in-progress input. Fixed the same way
  `engineChoice[name]` already solves the identical problem for the engine
  picker (a plain per-project JS object, read at render time, written by
  the textarea's own `oninput` handler) -- reusing an existing pattern in
  this file, not inventing a new one. Verified via `tests/test_team_
  frontend.js`'s own dedicated round-trip/HTML-injection-safety test.
- All other line items in "Proposed approach" (§1-§9) were implemented as
  written: the exact `import teams` anchor point, `default_team_
  composition()`'s exact priority order and error messages, the exact route
  pseudocode (§5), the exact `_team_reap_if_due()` shape, the exact
  `cancel_event` checkpoints and their exact recorded outcome text (§7),
  and the exact `install.sh` insertion point (§6).

## Known limitations

- **No new limitation introduced by this cycle beyond what's disclosed
  above.** All four of part 1's own carried-forward limitations (the
  now-closed empty-`project_name` guard; the still-real "no locking between
  two teams for the same project directory" non-goal, mitigated but not
  eliminated by this cycle's own real-concurrency test; 6c's own carried
  limitations; the tier-1/qwen3 probabilistic-judgment caveat) are
  unaffected by this diff.
- **`default_team_composition()`'s misconfigured-tier-2-only edge case**
  (see "Key decisions") is handled by a reasonable, disclosed judgment call
  but has no dedicated acceptance criterion or test -- low-severity (an
  operator with a genuinely broken `HEADLESS_SCHEMA_FLAG` gets a slightly
  less specific error message, still actionable via `roster()`'s own
  `schema_flag_error` field), not revisited further this cycle.
- **The orphan check's transient false positive for a genuinely still-live
  CLI-driven run is accepted as specified** (user-settled, `docs/spec.md`
  "Open questions") -- verified for real in `OrphanCheckSelfCorrectsForLive
  CliRunTests`, not just asserted from the design.
- **`teamTaskText`/`engineChoice` are both plain, unbounded, page-lifetime
  JS objects** -- a project removed from `PROJECTS_DIR` while its stale
  entry lingers in `teamTaskText` is harmless (the entry is simply never
  read again, since `instance_names()` no longer includes that project),
  same non-issue class `engineChoice` already carries; not a new concern
  this cycle introduces.

## What was mocked vs. exercised for real

Per this role's own "not testable is a claim to verify" discipline:

- **Real, not mocked**: every route (`/team/start`/`/team/stop`) against a
  real `ThreadingHTTPServer` + real `urllib.request.urlopen()`; every tmux
  operation against a real, locally-running tmux server (TMUX patched down
  to `["tmux"]`, same technique every prior 6d test file already
  establishes); real git worktree creation/removal; a real background
  `threading.Thread` actually driving `team_run()`; real SIGTERM/SIGKILL
  escalation against real, deliberately slow/signal-ignoring Python stand-in
  subprocesses; a real, SEPARATE `team-resume` subprocess (not a thread) for
  the orphan-self-correction test; real `app.py` process start (both a bare
  `import app` and a real `ThreadingHTTPServer` bound to a real socket,
  handling a real `/status` request) for the acceptance criterion this
  spec's own "Summary" flags as the one place a mistake would be immediately
  visible; all four real frontend `<script>`-extraction Node test suites,
  run together, not just the new one.
- **Mocked**: `_call_lead()` in the pure unit tests (`tests/test_teams_
  cancel.py`'s Tier-1 section, and `TeamStopEndpointTests`'s own
  mid-delegate-stop test's SETUP phase) -- standard for this story's own
  established "pure logic first, real tmux/subprocess second" split; every
  real-tmux/real-HTTP test above does NOT mock it. No real `claude`/`codex`/
  Ollama endpoint was exercised this cycle (this cycle touches no adapter
  logic, per its own explicit non-goals) -- unchanged from 6c's/part 1's own
  disclosed gaps.

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax/compile | `python3 -m py_compile app/app.py app/teams.py tests/test_teams_cancel.py tests/test_team_routes.py` | clean |
| **`app.py` actually imports and starts, not just compiles** (the spec's own named acceptance criterion) | `python3 -c "import sys; sys.path.insert(0, 'app'); import app"`; separately, a real `ThreadingHTTPServer` bound to a real socket, real `/login` + real `/status` request/response cycle | both succeed; `/status` returns `{"instances": [], ...}` with no error |
| `git diff --stat -- app/teams.py` shows no positional-shape change | `git diff -- app/teams.py \| grep -E "^[+-]def "` | every changed `def` is new or additive-keyword-only, confirmed by manual read |
| No new sudoers/privileged path | `git diff -- app/app.py app/teams.py \| grep -E "^\+.*subprocess\.(run\|Popen)\|^\+.*sudo"` | no matches in either file's diff |
| Grounding read-only guards untouched | `git diff --stat -- tests/test_teams_grounding.py` | empty |
| `git worktree remove` never gains `--force` | `git diff -- app/teams.py \| grep -i force` | only match is the unrelated `_force_ask_user` identifier |
| Deploy stays manual-click-only, untouched | `git diff -- app/teams.py \| grep -i deploy` | no matches |
| Full suite, 6 consecutive clean runs (one earlier interrupted 5-run batch hit an unrelated, leftover-system-user pollution issue in `tests/test_deploy_target.py::PrivilegedEndToEndTests` from a PRIOR run's own tearDown never completing after this environment's 2-minute command timeout killed it mid-batch -- confirmed unrelated to this diff (`test_deploy_target.py` untouched), the leftover `aidswbtest` system user removed by hand, and the file re-confirmed clean (30/30) immediately after) | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` | **671 passed** every clean run (638 baseline + 33 new: 15 in `test_teams_cancel.py`, 18 in `test_team_routes.py`), 0 flakes, 0 attributable failures |
| New test files alone | `pytest tests/test_teams_cancel.py tests/test_team_routes.py -q`, 3 consecutive runs | 33 passed every run |
| Frontend JS, all four real suites together | `node tests/test_team_frontend.js && node tests/test_deploy_frontend.js && node tests/test_singleton_toggle_frontend.js && node tests/test_upload_frontend.js` | 17/17, 9/9, 15/15, 8/8 -- all pass |
| Two-near-simultaneous-starts race, isolated re-runs | `pytest tests/test_team_routes.py::TeamStartEndpointTests::test_two_near_simultaneous_starts_exactly_one_succeeds -q`, 5 consecutive runs | passed every run |
| `install.sh` copies `teams.py` | `InstallShTeamsPyCopyTests` (2 tests) -- extracts the real "-- App + engines --" block and runs it against a scratch `$INSTALL_DIR` | `teams.py` lands there, byte-identical to the source |
| Pre-existing suites (`test_teams_headless.py`/`test_teams_lead.py`/`test_teams_lifecycle.py`) pass with zero assertion changes | `git diff -- tests/test_teams_headless.py` (empty); `test_teams_lead.py`/`test_teams_lifecycle.py` diffs are 12 mechanical `**kwargs` additions to test-double signatures only, no assertion touched | all three suites pass unmodified in behavior |

## How to verify locally

```bash
# Full suite (run several times)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# New test files alone
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_teams_cancel.py tests/test_team_routes.py -v

# Frontend JS (plain Node, no dependencies)
node tests/test_team_frontend.js
node tests/test_deploy_frontend.js   # regression-check: shares .team-btn's CSS declaration
node tests/test_singleton_toggle_frontend.js
node tests/test_upload_frontend.js

# app.py actually starts (not just compiles) -- the spec's own named
# acceptance criterion, see docs/spec.md "Proposed approach" §1
python3 -c "import sys; sys.path.insert(0, 'app'); import app; print('import OK')"

# Confirm app/teams.py's diff never changes an existing function's
# positional-argument shape
git diff -- app/teams.py | grep -E "^[+-]def "

# install.sh copies teams.py
grep -n 'cp "\$REPO_DIR/app/teams.py"' install.sh

# Real end-to-end smoke test against a real scratch git repo + real tmux
mkdir -p /tmp/scratch-6d2a/demo && cd /tmp/scratch-6d2a/demo
git init -q && echo hi > README.md && git add README.md && git commit -q -m init
export TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x
export PROJECTS_DIR=/tmp/scratch-6d2a ENGINES_DIR=$(pwd)/../../engines.d
python3 -c "
import sys; sys.path.insert(0, 'app'); import app
from http.server import ThreadingHTTPServer
import threading, json, urllib.request
server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
req = urllib.request.Request(f'http://127.0.0.1:{port}/login', method='POST',
    data=json.dumps({'username':'x','password':'x'}).encode(), headers={'Content-Type':'application/json'})
cookie = urllib.request.urlopen(req).headers.get('Set-Cookie').split(';')[0]
req2 = urllib.request.Request(f'http://127.0.0.1:{port}/status', headers={'Cookie': cookie})
print(json.loads(urllib.request.urlopen(req2).read()))
"
```

---

# Implementation: Team session lifecycle, part 2a -- review round 2 (must-fix + a second, more severe finding surfaced while verifying it)

## Summary

Round 2 responds to the reviewer's changes-requested verdict: one must-fix
(a check-then-act race on `app.py`'s `_team_threads` dict) and one doc-only
fix (`docs/design.md`'s contrast analysis assumed a light theme that was
never built). Fixing and re-verifying the must-fix surfaced a **second,
more severe, previously-undisclosed defect** in `app/teams.py`'s own
`_create_team_session()` (part 1 code, already reviewer-approved three
rounds ago) -- a real, reliably-reproducible race where a losing concurrent
`/team/start` attempt's own failure-cleanup could destroy the WINNING
attempt's real, live tmux session. This was not something the reviewer
asked me to look for; it surfaced because re-running this cycle's own
two-concurrent-starts test many times (chasing down why my must-fix's own
verification loop occasionally still flaked) exposed a third loser-error
shape the test's own assertion hadn't accounted for, and chasing THAT down
led directly to the real bug. Fixed both, plus two of my own round-1 tests
whose own over-narrow error-message assertions had been silently masking
part of this.

## Changes by file

- **`app/app.py`**:
  - `_team_threads_lock` (new `threading.Lock()`) + three helper functions
    -- `_team_threads_set(name, entry)`, `_team_threads_get(name)`,
    `_team_threads_pop_if_owned(name, run_id)` -- placed immediately after
    `_team_threads`'s own declaration. `_team_threads_pop_if_owned()`
    replaces `_run_team_in_background()`'s own former check-then-act
    (`entry = _team_threads.get(name)` ... later, separately,
    `_team_threads.pop(name, None)`) with ONE atomic read-check-pop under
    the lock. Every other accessor of `_team_threads` in this file --
    `_team_reap_if_due()`'s own read, and both the `/team/start`/
    `/team/stop` route handlers -- was audited and switched onto one of
    these three functions; confirmed by grep that no raw `_team_threads[...]`/
    `.get()`/`.pop()` call remains anywhere outside the three helpers
    themselves.
  - `_run_team_in_background()`'s own docstring corrected -- it previously
    asserted a guarantee ("guards against a subsequent stop-then-relaunch
    having already replaced the entry") the unlocked code did not actually
    provide; now accurate, since the guarantee is real under the lock.

- **`app/teams.py`**:
  - `_create_team_session()`'s failure-cleanup branch: `_kill_team_session_
    if_owned(session, run_id)` replaces a raw, unconditional `kill-session`.
    Docstring corrected -- it previously argued (incorrectly) that a
    concurrent second caller could never reach this branch holding someone
    else's session, and that `_kill_team_session_if_owned()` was "the wrong
    tool" for this specific cleanup; both claims were false, and the defect
    they produced was real, not theoretical (see "Findings" below).

- **`docs/design.md`**: "Color contrast" section (and the matching "Color
  tokens" implementation note) rewritten against the app's real dark theme
  (`#111`/`#1c1c1c`) and the real shipped hex values, with contrast ratios
  recomputed via the standard WCAG relative-luminance formula rather than
  estimated. All four status colors pass AA comfortably against the real
  background; the original analysis's own arithmetic was wrong in 3 of 5
  cases even against the light background it incorrectly assumed.

- **`tests/test_team_routes.py`**: new `TeamThreadsLockTests` (2 tests) --
  the must-fix's own regression test, using a `_SlowGetDict` (a dict
  subclass whose `.get()` for one key sleeps briefly) to widen the window
  between a caller's read and its later write/pop, the same "artificially
  widened window standing in for real scheduler preemption" technique this
  project's own `CreateTeamSessionAtomicStampTests` (part 1) already
  established. One test proves the real, shipped `_team_threads_pop_if_
  owned()`/`_team_threads_set()` survive it; a second, using the identical
  technique against a faithful reproduction of the pre-fix shape, confirms
  the technique itself is discriminating (so the first test's clean pass
  isn't merely because the technique is too weak to ever catch anything).
  Independently re-verified outside the permanent test suite too: manually
  monkeypatched the real functions back to the pre-fix, unlocked shape and
  confirmed the exact same widened-window scenario loses the entry every
  time (see "Verification status").
  - `TeamStartEndpointTests.test_two_near_simultaneous_starts_exactly_one_
    succeeds`'s own loser-error assertion was narrowed to two specific
    message substrings in round 1; broadened to "any real, non-empty
    error" plus an added assertion that the winner's session is correctly
    *stamped* with its own run_id (not just alive) -- see "Findings" for
    why the narrower assertion was itself part of what let the real defect
    go unnoticed in round 1.

- **`tests/test_teams_lifecycle.py`**: new `SessionCreationRaceRealTmuxTests`
  (1 test, 15 real, Barrier-synchronized concurrent-`_create_team_session()`
  trials) -- the severe finding's own regression test. Independently
  confirmed it fails against the pre-fix code (reverted the fix in a scratch
  copy of `app/teams.py`, ran the test, watched it fail on trial 0, restored
  the real fix, confirmed the restored file is byte-identical to the fixed
  version via `diff`) before trusting it.

## Findings

### 1. Must-fix, confirmed and fixed as directed: `_team_threads` check-then-act race

Exactly as the reviewer described. Fixed with `_team_threads_lock`,
verified both that the real fix survives an artificially widened window and
that the same widening technique reliably catches the pre-fix shape (2
tests, `TeamThreadsLockTests`), and independently re-confirmed by manually
monkeypatching the real functions back to the pre-fix shape and watching
the identical scenario lose data every time.

### 2. NOT asked for, found anyway: `_create_team_session()`'s failure-cleanup could destroy a winning concurrent launch's real session

**How this was found**: chasing down why the must-fix's own re-verification
loop still occasionally showed `test_two_near_simultaneous_starts_exactly_
one_succeeds` failing under full-suite load (not in isolation), even after
the `_team_threads` fix. The failure was an assertion mismatch on the
loser's own error message ("failed to create worktree for 'helper':
Preparing worktree ... fatal: '...' already exists" and, separately, "failed
to create team session (tmux new-session failed)") -- neither matched
round 1's own narrower assertion (`"already running" in ... or "already
exists" in ...`). Investigating the SECOND of those two new message shapes
-- one this cycle's own test had never actually observed before, in dozens
of prior runs -- led directly to a real, severe defect in `_create_team_
session()` (part 1 code, already reviewer-approved three rounds ago, never
touched by this cycle's own diff until now).

**The defect, precisely**: `_create_team_session()`'s own upfront
`tmux_has(session)` check can pass as `False` for TWO different concurrent
callers launching the same project at once (neither has created a session
yet). Exactly one of their real `tmux new-session -s <same name> ...`
calls can actually succeed server-side; tmux itself makes the LOSER's own
`new-session` fail outright with a real "duplicate session" error, before
any of its own `set-option` calls run -- so the loser's own call creates
nothing. But the loser's OLD failure-cleanup code read `tmux_has(session)`
being `True` at that point as "my own upfront check said nothing existed
when I started, so whatever's here now must be MY OWN partially-created
leftover" and issued an unconditional `kill-session` -- destroying the
WINNER's real, live, already-fully-stamped session instead.

**Reproduced directly, deterministically, not by luck**: a real,
`threading.Barrier`-synchronized pair of concurrent `_create_team_session()`
calls for the same project, run 20 times: **20/20 trials destroyed the
winner's session** against the pre-fix code. Same script, same trial count,
against the fixed code: **0/30**. See "How to verify locally" for the exact
reproduction script.

**Severity**: real resource destruction of a legitimate, currently-running
team's tmux dashboard session (all of its member windows) by a losing,
otherwise-harmless concurrent launch attempt for the same project -- worse
than the `_team_threads` must-fix (which the reviewer correctly rated
should-fix precisely because nothing it affected was unrecoverable or
un-self-correcting). This one directly falsifies an explicit acceptance
criterion of this cycle's own spec ("the successful run's worktrees/
session/thread are byte-for-byte unaffected by the losing attempt") under
real, not-especially-rare timing (~10-15% of trials under light concurrent
system load in this session's own observations, reliably 100% under a
tight `Barrier`-synchronized race). Also worth naming plainly: my own
round-1 test for the two-concurrent-starts acceptance criterion did NOT
catch this the first time, because its own loser-error assertion was
narrower than the actual set of legitimate collision shapes -- it happened
to pass every time it observed one of the two shapes it checked for, and
simply never ran enough trials in round 1 to hit the third, rarer shape
that would have exposed the real problem underneath it. This is exactly
the "over-narrow assertion masks a real defect" failure mode the story's
own reviewer keeps finding in this exact subsystem, just self-inflicted
this time in my own test rather than in production code.

**Fix, reusing existing, already-battle-tested machinery**:
`_kill_team_session_if_owned(session, run_id)` -- the SAME ownership-stamp
helper `stop_team()`/`sweep_dead_teams()` already use for the analogous
part-1 defect (Defect #1, docs/implementation.md's own part-1 section) --
replaces the raw `kill-session`. This is correct specifically because a
winner's session, once observable to ANY concurrent caller at all, is
ALWAYS already fully stamped with the winner's own run_id (part 1's own
atomic-creation guarantee, `;`-chained in one tmux client call) -- so a
losing caller's own (different) run_id never matches it, and `_kill_team_
session_if_owned()` correctly leaves it alone. This call's own genuine
partial-creation leftover (new-session succeeds, the LATER stamp link
fails) is, by the same construction, always UNSTAMPED -- which `_kill_
team_session_if_owned()` already treats as safe to kill for any run_id,
correctly reclaiming it. No new mechanism, no new privileged surface --
the exact tool this codebase already built for exactly this class of
problem, previously not applied to this one call site because the
docstring's own prior reasoning (now corrected) argued, incorrectly, that
it didn't apply here.

**Why this doesn't touch the standing constraints**: `_kill_team_session_
if_owned()` already goes through the existing `TMUX` constant (unchanged);
no new subprocess call, no new sudoers surface, no new privileged path --
confirmed by `git diff` (no new `subprocess.run`/`sudo` call in either
file's diff this round).

## Deviations from spec

None. Both fixes are structural corrections to already-shipped code (part 1
in the second case), not changes to this cycle's own spec/design surface.
`docs/design.md`'s color-token wording is corrected, not redesigned -- the
actual states, copy, layout, and component reuse are all unchanged.

## Known limitations

- Unchanged from round 1, plus: the severity calibration the reviewer
  applied to the `_team_threads` finding (should-fix, bounded/self-
  correcting) does NOT apply to Finding #2 above (must-fix, real
  destruction) -- recorded here so a future reader doesn't conflate the
  two just because they surfaced in the same review round.

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax/compile | `python3 -m py_compile app/app.py app/teams.py tests/test_team_routes.py tests/test_teams_lifecycle.py` | clean |
| Every `_team_threads` access goes through the lock-guarded helpers | `grep -n "_team_threads\b" app/app.py` then manual read of every match | confirmed -- every raw `[name]=`/`.get()`/`.pop()` call is inside `_team_threads_set()`/`_team_threads_get()`/`_team_threads_pop_if_owned()` only |
| Must-fix regression test, fails against pre-fix code | manually monkeypatched `_team_threads_pop_if_owned`/`_team_threads_set` back to the unlocked shape, re-ran the exact widened-window scenario | `result: None` -- confirmed data loss reproduced against the pre-fix shape; passes cleanly against the real fixed code |
| Must-fix regression test stability | `pytest tests/test_team_routes.py::TeamThreadsLockTests -q`, 5 consecutive runs | 2 passed every run |
| Finding #2, standalone reproduction before trusting it as a regression test | manual `threading.Barrier`-synchronized script, 20 trials pre-fix / 30 trials post-fix | 20/20 destroyed pre-fix, 0/30 post-fix |
| Finding #2 regression test fails against pre-fix code | reverted `_create_team_session()`'s fix in a scratch copy of `app/teams.py`, ran `SessionCreationRaceRealTmuxTests`, restored the real fix (confirmed byte-identical via `diff` afterward) | failed on trial 0 pre-fix (`AssertionError: ... the WINNER's session was destroyed`); passes cleanly post-fix |
| Finding #2 regression test stability | `pytest tests/test_teams_lifecycle.py::SessionCreationRaceRealTmuxTests -q`, 20 consecutive runs | 1 passed every run |
| `test_two_near_simultaneous_starts_exactly_one_succeeds` stability after broadening its own assertion | isolated: 25/25 and 40/40 clean across two separate batches; also clean across 5 consecutive full-suite runs | passed every time |
| Full suite, 5 consecutive clean runs after both fixes (plus 2 earlier runs that caught the two over-narrow test assertions above, both since fixed) | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` | **674 passed** every clean run (673 prior + 1 new: `SessionCreationRaceRealTmuxTests`; `TeamThreadsLockTests`' own 2 tests were already counted at 673) |
| Frontend JS, all four suites (unaffected by this round -- Python/test-only changes) | `node tests/test_team_frontend.js && node tests/test_deploy_frontend.js && node tests/test_singleton_toggle_frontend.js && node tests/test_upload_frontend.js` | 17/17, 9/9, 15/15, 8/8 |
| No new sudoers/privileged path this round | `git diff -- app/app.py app/teams.py \| grep -E "^\+.*subprocess\.(run\|Popen)\|^\+.*sudo"` | no matches |
| Grounding read-only guards untouched | `git diff --stat -- tests/test_teams_grounding.py` | empty |
| `git worktree remove` still never gains `--force` | `git diff -- app/teams.py \| grep -iE "\-\-force"` | no matches |
| Deploy stays manual-click-only | `git diff -- app/app.py app/teams.py \| grep -i deploy` | only pre-existing round-1 references (the shared `.deploy-btn`/`.team-btn` CSS declaration, comment cross-references) -- `deploy_run()`/the `/instance/.../deploy` route are untouched this round |

## How to verify locally

```bash
# Full suite (run several times)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# Both new/updated regression tests alone
/home/dev/.local/bin/uv run --with pytest python -m pytest \
  tests/test_team_routes.py::TeamThreadsLockTests \
  tests/test_teams_lifecycle.py::SessionCreationRaceRealTmuxTests -v

# Finding #2's own standalone reproduction script (20 trials, real tmux,
# no sudo needed -- TMUX patched to plain `tmux`)
TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x python3 -c "
import sys, threading, tempfile, subprocess
sys.path.insert(0, 'app')
import app as appmod, teams as teamsmod
appmod.TMUX = teamsmod.TMUX = ['tmux']
teamsmod.TEAM_STATE_DIR = tempfile.mkdtemp()
session = teamsmod._team_session_name('proj')
results = {}
lost = 0
for i in range(20):
    subprocess.run(['tmux', 'kill-session', '-t', session], capture_output=True)
    results.clear()
    barrier = threading.Barrier(2)
    def _launch(run_id, key):
        barrier.wait()
        results[key] = teamsmod._create_team_session('proj', run_id, [])
    t1 = threading.Thread(target=_launch, args=(f'run-a-{i}', 'a'))
    t2 = threading.Thread(target=_launch, args=(f'run-b-{i}', 'b'))
    t1.start(); t2.start(); t1.join(); t2.join()
    if not teamsmod.tmux_has(session):
        lost += 1
subprocess.run(['tmux', 'kill-session', '-t', session], capture_output=True)
print(f'{lost}/20 trials: winner session destroyed (expect 0 against the fixed code)')
"
```

---

# Implementation: Test-isolation fix for real-tmux `switchboard-headless-*` tests (story/multi-agent-teams)

## Summary

Test-only change. `run_id` (and therefore every `switchboard-headless-<run_id>`
tmux session name derived from it) was an unscoped value — a global machine
property no single test process owns. Two concurrent test processes (or a
foreign process holding an unrelated `switchboard-headless-*` session) could
trip each other's "no leftover session" assertions, and worse, `tearDown`'s
own belt-and-braces sweep in several real-tmux test classes killed *any*
matching session name, including another concurrent process's still-live
one. Fixed by scoping every `run_id` this test suite generates with a
per-process token (`p<pid>`) and filtering/sweeping only on that scoped
prefix, following a diagnosis and fix shape supplied by the requester.

## Root cause

Not a production defect — `app/teams.py`'s own `_run_id()` and
`switchboard-headless-{run_id}` session-naming are correct and untouched.
The bug was entirely in the test suite's own assertions/sweeps operating on
the bare `"switchboard-headless-"` prefix, a namespace shared by every
concurrent process on the machine (a second real `python3 -m unittest`
invocation of the same file, or literally any other tmux user). This is why
`test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask` had
been failing roughly 2 runs in 17 while always passing in isolation, and was
misattributed as mysterious unrelated flakiness for four review cycles: a
concurrently-running second test process's `tearDown` (or the belt-and-
braces sweep in `RealTmuxHeadlessTests.tearDown`) would kill that test's
still-live tmux session out from under it mid-run, well before that test's
own assertions ever got a chance to run cleanly. It looked like an
unrelated, hard-to-reproduce flake in *that* test specifically only because
the umask test happens to hold its tmux session open the longest (a 5s
`sleep` script plus explicit mid-run file assertions), giving a concurrent
sweep the widest window to land a kill. Confirmed on this pass: tmux
teardown latency is not a factor (not re-litigated here, per the requester's
own prior 0/40 finding for both `kill-session` and fast natural exit) — this
is a namespace-collision/cross-process-sabotage bug, not a timing bug.

## Changes by file

- **`tests/test_teams_headless.py`** — added `_RUN_ID_SCOPE = f"p{os.getpid()}"`,
  `_SESSION_PREFIX = f"switchboard-headless-{_RUN_ID_SCOPE}"`, and a
  `_scope_run_ids(testcase)` helper that wraps `teamsmod._run_id` so every
  run_id produced for the duration of a test carries this process's scope
  token. `RealTmuxHeadlessTests.setUp` now calls it. Both the belt-and-braces
  sweep and `_no_leftover_sessions()` in that class now filter on
  `_SESSION_PREFIX` instead of the bare prefix. The four tests that
  monkeypatch `teamsmod._run_id` directly with a fixed id (bypassing the
  wrapper) now embed the scope token in the fixed id itself:
  `fixedid-forcekill`, `fixedid-external-term`, `fixedid-umasktest`,
  `fixedid-permtest` all become `f"{_RUN_ID_SCOPE}-fixedid-..."`.
  Two additional sites were found beyond the specified list, in this same
  file, using the identical `switchboard-headless-<literal>` naming scheme
  but never going through `_run_id()` at all (so the scoping wrapper alone
  didn't reach them) — fixed the same way, by embedding `_RUN_ID_SCOPE` into
  the literal id: `ActiveEngineHeadlessCollisionTests.
  test_live_headless_session_never_reported_as_project_engine`'s
  `run_id = "R"`, and `RealTmuxHeadlessTests.
  test_sweep_kills_live_session_for_an_aged_dir_and_removes_it`'s
  `stale_run_id = "stale-with-live-session"`. Both create a real tmux
  session with that literal name directly (`tmux new-session -s
  switchboard-headless-<literal>`), which collides outright across two
  concurrent processes (a real name clash, not a sweep/assertion issue) —
  confirmed by reproducing it (see "Concurrent-run evidence" below).

- **`tests/test_teams_cancel.py`** — same `_RUN_ID_SCOPE`/`_SESSION_PREFIX`/
  `_scope_run_ids()` helpers added (duplicated per this project's own
  established per-file-helper convention, not factored into a shared
  module). `_scope_run_ids(self)` called from
  `RealTmuxCancelEventAgentRunTests.setUp` and
  `RealTmuxMidDelegateAndMidLeadCallCancelTests.setUp`. The module-level
  `_no_leftover_headless_sessions()` helper, both classes' `tearDown` kill
  sweeps, and the two inline leftover-session checks inside
  `test_stop_mid_delegate_terminates_real_subprocess_and_records_stopped`/
  `test_stop_mid_lead_call_terminates_real_subprocess_and_records_stopped`
  all now filter on `_SESSION_PREFIX`.

- **`tests/test_team_routes.py`** — same helpers added.
  `_scope_run_ids(self)` called from `_RealHTTPTeamTestCase.setUp` (the
  shared base class for `TeamStartEndpointTests`, `TeamStopEndpointTests`,
  `ServiceRestartSimulationTests`), covering every `switchboard-headless-*`
  session any of those classes create via `agent_run()`'s own internal
  `_run_id()` call. The inline leftover-session filter in
  `test_stop_mid_delegate_terminates_real_subprocess_promptly` now uses
  `_SESSION_PREFIX`. Also narrowed the module-level
  `_kill_leftover_team_sessions()` helper's `"switchboard-"` branch (a
  broader literal than the `"switchboard-headless-"` the requester's grep
  targeted, so it wasn't in the original 8-site list, but it is the same
  sabotage pattern for the same session family, reachable from every
  `_RealHTTPTeamTestCase` subclass's `tearDown`) to `_SESSION_PREFIX`. Its
  separate `"team-"` branch (see "Known limitations") was left unchanged —
  fixing it is out of scope, see below.

## Deviations from spec

None from the requester's diagnosis for the specified 8 sites — implemented
as given. Two categories of deviation from the *stated scope* (not the
diagnosis itself, which was correct as far as it went):

1. **Additive, in-family, in-scope-file fixes beyond the listed 8 sites**
   (documented above): two literal `switchboard-headless-<id>` sessions in
   `tests/test_teams_headless.py` that don't go through `_run_id()` at all,
   and narrowing `_kill_leftover_team_sessions()`'s `"switchboard-"` branch
   in `tests/test_team_routes.py`. All three are the exact same bug
   (unscoped `switchboard-headless-*` naming), in one of the three assigned
   files, and were necessary for the "two full suites concurrently, zero
   `switchboard-headless-*`-related failures" property to actually hold —
   without them, concurrent runs of `test_teams_headless.py` alone still
   failed. See "Concurrent-run evidence".
2. **NOT fixed — out of scope, reported instead of silently done or silently
   ignored**: see "Known limitations".

## Known limitations

Running two full `python3 -m unittest discover -s tests` processes
concurrently is **not fully green**, even after this fix — but every
remaining failure is attributable to bugs entirely outside the diagnosed
`switchboard-headless-*` mechanism and outside the specified 3 files:

1. **`team-<project_name>` tmux session name collisions** — a *different*
   session-naming scheme (`teamsmod._team_session_name()`, `f"team-
   {project_name}"`), not derived from `run_id` at all. Many tests across
   `tests/test_team_routes.py` (fixed project name `"proj"`) and
   `tests/test_teams_lifecycle.py` (fixed project names `"atomicdemo"`,
   `"failchain"`, `"failchain2"`, `"sessionrace"`, `"myproj"`) use the exact
   same literal project name, so two concurrent processes race to create a
   tmux session with the identical literal name — a real name clash, not a
   sweep/assertion defect, and not fixable by scoping `run_id`. Reproduced
   directly: `test_team_routes.ServiceRestartSimulationTests.
   test_stop_after_simulated_restart_still_tears_down_real_session_and_worktrees`
   and `test_team_routes.TeamStartEndpointTests.
   test_happy_path_tier2_default_lead_persisted_correctly` both failed this
   way under real concurrency (see evidence below).
2. **`tests/test_teams_lifecycle.py` was not part of the specified scope**,
   but has the identical `"team-"`/bare-`"switchboard-"` broad `tearDown`
   sweep pattern (its own module-level helper, around line 127-130) plus a
   third, unrelated naming scheme (`"switchboard-worktree-op-"`, around line
   460) that is also unscoped. Left untouched — fixing it would mean
   touching a fourth file and a different naming family not in the
   requester's diagnosis, and (per `team-<project_name>`, above) would still
   need a project-name-scoping fix that's a substantially larger change than
   this task's stated scope.
3. **`tests/test_deploy_target.py`/`tests/test_deploy_dispatch.py`'s
   `PrivilegedEndToEndTests`/`PrivilegedDeployRunEndToEndTests`** fail under
   concurrency too, but for a reason with no relationship to tmux/teams at
   all — real SSH/rsync against a shared `127.0.0.1` deploy target,
   contended when two processes hit it at once. Unrelated subsystem, not
   investigated further (out of scope for a tmux test-isolation fix).

None of the above are new defects introduced by this change — they were
already latent in the suite; concurrent execution simply was not previously
attempted by anyone as a way to surface them. They're recorded here because
the task asked for an honest "two full suites concurrently both green" demo,
and that demo does not fully succeed for reasons outside this fix's
diagnosed scope.

**Weakening check**: no assertion was weakened. Every "no leftover session"
check still asserts an empty list; the list is now correctly scoped to
sessions this process itself could have created, which is a stricter,
more correct definition of "leftover" than the previous accidental
machine-wide one (previously a foreign session made the assertion
over-broad in one direction — a false failure — while simultaneously
letting `tearDown` under-check in the other — killing a session it had no
business touching).

## How to verify locally

Baseline (matches the requester's cited counts):
```
python3 -m unittest discover -s tests            # 674 tests, OK
node tests/test_team_frontend.js                 # 17/17 ALL PASS
node tests/test_deploy_frontend.js                # 9/9 ALL PASS
node tests/test_singleton_toggle_frontend.js      # 15/15 ALL PASS
node tests/test_upload_frontend.js                # 8/8 ALL PASS
```

Targeted before/after reproduction of the exact diagnosed bug (foreign
session both trips the assertion AND gets killed by `tearDown`, pre-fix;
neither happens, post-fix):
```
tmux new-session -d -s switchboard-headless-foreignproc-test123 -- sleep 300
python3 -m unittest tests.test_teams_headless.RealTmuxHeadlessTests.test_success_stream_end_to_end -v
tmux list-sessions | grep foreignproc   # still alive post-fix; gone (killed) pre-fix
```

Concurrent-run evidence (the property actually being fixed):
```
# The two files fully contained to the diagnosed switchboard-headless-*
# mechanism -- 3/3 iterations clean, zero leftover sessions:
python3 -m unittest tests.test_teams_headless tests.test_teams_cancel &
python3 -m unittest tests.test_teams_headless tests.test_teams_cancel &
wait

# Full suite, both processes -- NOT fully green (see "Known limitations"),
# but zero failures are switchboard-headless-*-related:
python3 -m unittest discover -s tests &
python3 -m unittest discover -s tests &
wait
```

---

# Implementation: `install.sh --with-ollama` -- link an existing Ollama (sub-spec 6d, part 2b of 2)

## Summary

`install.sh` gains one new optional flag, `--with-ollama`: prompts for an
existing, already-running Ollama's OpenAI-compatible endpoint URL and a
model name, validates both against the **exact** path the tier-1 lead
adapter really calls (`GET "$BASE/models"`, not Ollama's own native
`/api/tags`), and writes `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` only when
that validation succeeds. This **links** a remote Ollama -- it installs
nothing locally (no package, no model pull, no container, no systemd
unit), per docs/story.md §2.5's own finding that the standard switchboard
container has ~715MB free RAM with swap already exhausted, so no
tool-capable model fits there. Off by default, matching every other
`--with-*` flag. `app/app.py` and `app/teams.py` are untouched -- this
cycle is `install.sh` + tests + docs only, per spec.

## Changes by file

- **`install.sh`**:
  - Usage-block comment (near the top, alongside every other `--with-*`
    flag's own paragraph) documents `--with-ollama`.
  - Flag plumbing: `WITH_OLLAMA=0` default alongside the other `WITH_*`
    defaults; `--with-ollama) WITH_OLLAMA=1 ;;` case alongside the others.
  - New block, placed **immediately before** the `--with-deploy-target`
    block (both sit after `ENV_FILE` is defined at the top of the "Config"
    section) -- see "Key decisions" below for why this exact placement,
    not simply appended after deploy-target, mattered:
    - Two prompts: endpoint URL (default `http://127.0.0.1:11434/v1`,
      shown for shape only -- a remote endpoint is the expected real
      answer) and model name (default `qwen3:8b`), each pre-filled from
      any existing `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` in `switchboard.env`
      so a blank re-run answer resubmits the *previous* value rather than
      the shape-only default (see "Idempotence" below).
    - Trailing-slash normalisation (`${OLLAMA_BASE_URL_INPUT%/}`) before
      validating or writing -- never appends `/v1` if the operator left it
      off; only strips a redundant trailing slash so `/models` is never
      requested as `//models`.
    - Validation: `curl -fsS --max-time 10 "$BASE_NORM/models"`, bounded so
      an unreachable or stalling host can never hang the installer. The
      response is parsed with a `python3` script (built into a shell
      variable via a heredoc, then invoked as `python3 -c "$VAR"` with the
      JSON piped separately -- see "Key decisions" for why `python3 -
      <<PYEOF` doesn't work when the script also needs piped stdin data),
      never `grep`, comparing the wanted model name against each `id` in
      `{"data": [{"id": ...}, ...]}` by **exact equality**.
    - Three distinct outcomes: unreachable/non-JSON/HTTP-error (skip,
      write nothing, explain the tier-2 fallback), reachable-but-model-
      absent (skip, write nothing, **list the available model ids**), and
      both-fine (`set_env` both keys, print a summary that says plainly
      nothing was installed locally).
- **`tests/test_install_ollama.py`** (new, 16 tests, `InstallShOllamaBlockTests`)
  -- see "Verification status" below.
- **`tests/test_deploy_target.py`** (one-line fix, not a new test): the
  existing `test_combined_with_host_control_no_conflicting_state`'s own
  `host_control_block` extraction end-marker (`'fi\n\n# ── Optional:
  deploy-target'`) was a literal string that happened to match the text
  immediately following `--with-host-control`'s closing `fi` *only because
  nothing sat between it and the deploy-target block's own comment before
  this cycle*. Inserting `--with-ollama` between them made that marker
  match much further down the file instead (right after MY new block's own
  closing `fi`), silently absorbing the entire `--with-ollama` block into
  `host_control_block` and producing a bash syntax error two commands
  later. Fixed by updating the literal marker to the new correct text
  (`'fi\n\n# ── Optional: link an existing remote Ollama'`) -- a real,
  demonstrated regression this cycle's structural change caused in an
  *existing* test, not a speculative touch-up. See "Key decisions" below
  for the same underlying issue in this cycle's own new test file, fixed
  before it ever shipped rather than discovered afterward.
- **`config/switchboard.env.example`**: unchanged. `TEAM_LLM_BASE_URL`/
  `TEAM_LLM_MODEL` were already documented there in 6c, including a
  comment that already names `install.sh --with-ollama` by its final name
  -- confirmed by inspection before starting, not assumed.
- **`app/app.py`, `app/teams.py`**: unchanged. Confirmed by `git diff
  --stat -- app/app.py app/teams.py` showing no output for this cycle's
  diff.

## Key decisions / tradeoffs

- **Block placement: immediately BEFORE `--with-deploy-target`, not
  appended after it.** The first attempt placed the new block *after*
  `--with-deploy-target` and before the `"== Done =="` summary. That broke
  4 existing tests in `tests/test_deploy_target.py`: their own block
  extraction (`_extract_between(source, "# ── Optional: deploy-target
  receiver", 'echo "== Done =="')`) is a literal-text slice, and inserting
  new content between the deploy-target block and that shared end marker
  silently pulled the entire new `--with-ollama` block into what those
  tests thought was just the deploy-target block -- producing `bash: line
  119: WITH_OLLAMA: unbound variable` under `set -u` (that harness never
  sets `WITH_OLLAMA`). Moving the new block to sit *before*
  `--with-deploy-target` instead restores the original adjacency between
  deploy-target's own block and the summary exactly as it was, so those
  existing extractions need no change at all. This is a real, general
  lesson for this codebase's "extract a literal block from install.sh's
  own source" test technique: appending a new optional block anywhere
  between an existing block and *its own* end marker is unsafe regardless
  of which existing block is involved; inserting before it (or as the new
  last block before the final marker) is not. One further, unavoidable
  fix was still needed even after choosing the safer placement:
  `test_combined_with_host_control_no_conflicting_state`'s own separate
  extraction (a literal string ending exactly at what used to be
  immediately after `--with-host-control`'s closing `fi`) still needed its
  end-marker literal updated, since something now genuinely sits between
  host-control and deploy-target that didn't before -- see "Changes by
  file" above.
- **The new test file's own two internal `_extract_between` calls use
  `"# ── Optional: deploy-target receiver"` as the end marker, not `'echo
  "== Done =="'`** -- for the identical reason: with `--with-ollama` now
  sitting immediately before `--with-deploy-target`, ending the extraction
  at the shared summary marker would just reproduce the same class of bug
  in the *new* file that had to be fixed in the existing one. Caught and
  fixed here before ever running the full suite, by reasoning through the
  file's new structure rather than waiting to discover it as a failure.
- **`python3 -c "$VAR"` with piped stdin, not `python3 - <<PYEOF ... JSON
  piped in too`.** The first draft tried `printf '%s' "$JSON" | python3 -
  "$MODEL" <<'PYEOF' ... PYEOF` -- piping the response JSON into the same
  command whose stdin a heredoc also redirects. Verified directly (not
  assumed) that this is broken: `bash -c 'printf "PIPED-JSON" | python3 -
  <<PYEOF
sys.stdin.read()
PYEOF'` prints `''`, not `'PIPED-JSON'` -- the
  heredoc always wins that fd, so the JSON payload would never reach the
  script. Fixed by building the script text into a shell variable via a
  heredoc (a `cat <<'PYEOF' ... PYEOF` with no pipe involved, so no
  conflict), then invoking `python3 -c "$VAR" "$MODEL"` with the JSON
  piped in **separately** -- confirmed working for both the exact-match
  and substring-safety cases by hand before writing it into install.sh.
- **A blank prompt answer resubmits the *previous* value (via `get_env`
  pre-fill), not an empty string guarded by `[ -n "$X" ] && set_env`.**
  docs/spec.md's own "Proposed approach" shows a literal
  `prompt "..." "http://127.0.0.1:11434/v1"` (hardcoded shape-only
  default), while its separate "Idempotence" section requires that a
  blank answer leave any previous value untouched, matching the
  deploy-target block's own `[ -n "$X" ] && set_env` idiom. These two are
  in real tension for a value whose default is non-blank: deploy-target's
  own idiom works because its own defaults are all empty strings, so
  "blank" and "the default" are the same thing there. For `--with-ollama`
  they are not. Resolved by following this same file's OWN older, more
  common convention for exactly this shape (`AUTH_MODE`/`PVE_HOST`/
  `SIMPLE_USERNAME`/`PUBLISH_MODE`/`BASE_URL` all pre-fill their `prompt`
  default from `get_env "$ENV_FILE" <KEY>`) rather than the deploy-target
  block's blank-default idiom, which doesn't fit here: `OLLAMA_BASE_URL_
  DEFAULT="$(get_env ...)"`, then `prompt "..."
  "${OLLAMA_BASE_URL_DEFAULT:-http://127.0.0.1:11434/v1}"`. A blank answer
  on a fresh install still shows the spec's own literal shape-only
  default (satisfying "Proposed approach" for the first-run case); a
  blank answer on a re-run resubmits the exact previous value, which then
  gets *revalidated* (not just left alone) -- if that revalidation
  succeeds, the write is a byte-for-byte no-op; if the previously-working
  endpoint has since gone unreachable, nothing gets clobbered either
  (the block just writes nothing, same "refuse to write unverifiable
  config" discipline as every other outcome). Verified directly by test
  (`test_blank_answers_leave_existing_values_untouched`,
  `test_changed_model_updates_only_team_llm_model`,
  `test_rerun_with_same_answers_is_a_noop`), with the stub server kept
  running across both prompts in each test so the revalidation path is
  exercised for real, not assumed away.
- **The written `TEAM_LLM_BASE_URL` is the normalised (trailing-slash-
  stripped) form, not the operator's raw input verbatim.** The spec's own
  acceptance criterion ("validates and writes correctly") doesn't pin down
  which exact string gets written for the trailing-slash case; the
  normalised form was chosen because it matches `_tier1_call()`'s own
  `f"{base_url}/chat/completions"` convention (no trailing slash, mirroring
  `DESC_LLM_BASE_URL`'s existing shape) -- confirmed by reading
  `app/teams.py` before deciding, not assumed.
- **`--with-ollama` needs no root-privileged system state at all** (no
  `useradd`, no sudoers file, no `/usr/local/bin` install) unlike
  `--with-deploy-target`/`--with-host-control` -- so, unlike
  `InstallScriptDeployTargetBlockTests`, none of `test_install_ollama.py`'s
  tests are gated on `HAVE_PASSWORDLESS_SUDO`; they run unconditionally.
  The pty-driven technique (`_run_with_pty`, copied verbatim from
  `tests/test_deploy_target.py` with the same docstring, since
  install.sh's own `prompt()` deliberately reads from `/dev/tty` rather
  than stdin) is still required, though, since this block calls `prompt`
  twice -- so `InstallShDeployMapBlockTests`' simpler plain-subprocess
  technique (valid there because that file's two blocks never call
  `prompt` at all) doesn't apply here.

## Deviations from spec

1. **Prompt defaults are pre-filled from any existing config (`get_env`),
   not always the spec's own literal hardcoded default string.** See "Key
   decisions" above for the full reasoning -- this resolves a real tension
   between the spec's own "Proposed approach" code sample and its separate
   "Idempotence" section, in favor of satisfying the section with an actual
   testable acceptance criterion attached, while still showing the spec's
   own literal shape-only defaults on a genuinely fresh install (no prior
   `TEAM_LLM_*` in `switchboard.env`).
2. **A blank re-run answer causes the block to *revalidate* the previous
   value over the network, not simply skip validation and leave the file
   untouched by construction.** The spec's own "Idempotence" section states
   the *outcome* ("a blank answer must leave any previous value
   untouched"), which this satisfies, but doesn't specify *how*. The
   alternative (skip re-validation entirely on a blank answer) would let a
   config value silently go stale if the endpoint became unreachable
   between runs, without ever alerting the operator -- reverifying on every
   run, including blank re-runs, is the more conservative reading of "never
   write config you cannot verify" (a "Settled before this cycle" line),
   and it composes correctly with the "changed model" test case (which by
   construction must still hit the network to check the new model name is
   present).
3. **The written `TEAM_LLM_BASE_URL` is the trailing-slash-normalised
   form.** See "Key decisions" above -- the spec's own acceptance criterion
   doesn't pin this down explicitly; the choice matches the existing
   `DESC_LLM_BASE_URL`/`_tier1_call()` no-trailing-slash convention.

No other deviations. `app/app.py`/`app/teams.py` are genuinely untouched
(confirmed by `git diff --stat`), matching the spec's explicit non-goal;
nothing surfaced during this cycle that looked like it required a code
change.

## Known limitations

- **No locking/atomicity between a concurrent `--with-ollama` run and any
  other process writing `switchboard.env` at the same instant.** Same
  category of gap as every other `set_env` call in `install.sh` -- this
  cycle doesn't introduce it and doesn't claim to fix it (install.sh has
  never had cross-process file locking for `switchboard.env`).
- **A model that ollama's `/v1/models` lists but that isn't actually
  loaded/runnable (e.g., insufficient VRAM at inference time, a corrupted
  pull) is accepted at install time.** The spec's own non-goals explicitly
  exclude runtime health-checking ("Health-checking the endpoint at
  runtime, on a timer, or at team start" is out of scope) -- this is that
  same boundary, restated for the install-time case: presence in the
  `/models` listing is the full extent of what install-time verification
  can mean here, by design.
- **The two `curl`/`python3` version constraints are whatever ships with
  Debian 12+'s `apt-get install python3 curl`** (both already installed
  unconditionally per `install.sh:146`, per the spec's own precondition) --
  not independently version-pinned or checked by this block.

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax | `bash -n install.sh` | clean |
| Manual, real stub server, all 3 outcomes + trailing slash + substring safety | see below | all correct, confirmed by hand before writing the automated tests |
| Manual, real stalling stub, bounded | `time bash <extracted block>` against a `time.sleep(60)` handler | `real 0m10.015s` -- bounded by `curl --max-time 10`, never hangs |
| New test file alone | `uv run --with pytest python -m pytest tests/test_install_ollama.py -q` | **16 passed** |
| `tests/test_deploy_target.py` (regression check after the placement fix) | `uv run --with pytest python -m pytest tests/test_deploy_target.py -q` | **30 passed** (was 4 failing before the placement + marker fixes, see "Key decisions") |
| Full suite, 2 consecutive clean runs this cycle | `uv run --with pytest python -m pytest tests/ -q` | **690 passed** both runs (674 baseline + 16 new), no flake |
| Four Node suites | `node tests/test_{team,deploy,singleton_toggle,upload}_frontend.js` | 17/17, 9/9, 15/15, 8/8 -- all pass, untouched by this cycle |
| `app/app.py`/`app/teams.py` diff scope | `git diff --stat -- app/app.py app/teams.py` | empty -- no output, confirming the non-goal held |

Manual verification, real stub server, before any automated test was
written (`python3 -m http.server`-style script serving canned JSON on a
real port, extracted block run via `bash` with hand-supplied
`OLLAMA_BASE_URL_INPUT`/`OLLAMA_MODEL_INPUT` and a pre-populated
`switchboard.env`):
```
$ bash /tmp/full_test.sh          # reachable, model present
Linked remote Ollama: http://127.0.0.1:18999/v1, model qwen3:8b.
Nothing was installed locally -- this endpoint runs elsewhere.

$ bash /tmp/full_test2.sh         # substring safety: asked for "qwen3:8"
                                   # against a stub advertising "qwen3:8b"
Reached http://127.0.0.1:18999/v1 but model 'qwen3:8' is not available
there -- writing nothing. Available models: qwen3:8b,llama3.2:1b

$ bash /tmp/full_test3.sh         # trailing slash in stored config
Linked remote Ollama: http://127.0.0.1:18999/v1, model qwen3:8b.
# (TEAM_LLM_BASE_URL written WITHOUT the trailing slash)

$ time bash /tmp/full_test4.sh    # unreachable (nothing listening)
Could not reach http://127.0.0.1:1/v1/models ... -- writing nothing.
real 0m0.011s
```

## How to verify locally

```bash
# Syntax
bash -n install.sh

# New test file
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_install_ollama.py -v

# Regression check on the existing deploy-target block tests (needed a
# one-line marker fix this cycle, see "Key decisions")
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_deploy_target.py -q

# Full suite (run more than once)
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# Node suites (untouched by this cycle, run for completeness)
node tests/test_team_frontend.js
node tests/test_deploy_frontend.js
node tests/test_singleton_toggle_frontend.js
node tests/test_upload_frontend.js

# Confirm app/app.py and app/teams.py are untouched
git diff --stat -- app/app.py app/teams.py

# Manual end-to-end sanity check against a real stub server (no pty needed
# if you pre-populate switchboard.env and use YES=1 -- see
# tests/test_install_ollama.py's own InstallShOllamaBlockTests for the
# pty-driven version that exercises the real interactive prompts)
python3 -m http.server --bind 127.0.0.1 0   # or any script serving
                                             # {"data":[{"id":"qwen3:8b"}]}
                                             # at /v1/models
```

---

# Implementation: Roster & composition UI (sub-spec 6e)

## Summary

Adds a lead/teammate picker inside the existing per-project idle-state team
row (docs/design.md), backed by three new `app/teams.py` functions
(`validate_composition()`, `load_compositions()`/`save_composition()`) and
three additive `app/app.py` route changes: `GET /status` gains a top-level
`roster` field and a per-instance `inst.team.composition` field, a new
read-only `GET /projects/<name>/team/grounding` route exposes discovery
metadata only, and `POST /projects/<name>/team/start` gains two optional
body keys (`lead`/`members`) that are validated, persisted to
`TEAM_STATE_DIR/compositions.json`, and used in place of
`default_team_composition()`'s pick -- byte-for-byte unchanged when both
keys are absent. `default_team_composition()`, `roster()`, and
`load_grounding()` are all reused unmodified; no new backend concepts.

## Changes by file

- **`app/teams.py`** (3 new functions, ~100 lines, everything else reused
  unmodified):
  - `validate_composition(lead, members) -> str | None` -- operates
    directly on the raw wire-format objects a POST body carries (`lead`:
    `{"kind", "name"}`, each `members` entry: `{"kind", "name"}`), calls
    `roster()` once internally, and checks, in order: lead is a dict naming
    a real `(kind, name)` roster entry; if that entry is tier 2, its
    `schema_flag_error` must be falsy (same protection `_cli_team_start()`
    already gives the CLI, now shared); `members` is a non-empty list, each
    entry naming a real roster entry with `kind == "engine"` (this is what
    keeps the Ollama entry, if any, from ever being accepted as a
    teammate -- it's never `delegate_capable`); no duplicate names; the
    lead's name is not also a member's name. Never checks tier against a
    hardcoded threshold -- a tier-3 lead is accepted exactly like tier 1/2
    (docs/spec.md goal: "a real option, not a token one").
  - `_compositions_path()`, `load_compositions()`, `save_composition()` --
    same `.tmp` + `os.replace()` atomic-write shape as `app.py`'s own
    `_load_desc_cache()`/`_save_desc_cache()`, at
    `TEAM_STATE_DIR/compositions.json` (no new env var). `load_compositions()`
    returns `{}` on a missing or corrupt file (`try/except (OSError,
    ValueError)`), never a 500. `save_composition()` stores only
    `{"kind", "name"}` for the lead (never `tier`/`schema_flag_error`,
    always re-derived live from `roster()` at read time) and stores
    `members` as a plain list of engine-name strings -- the same shape
    `default_team_composition()` already returns for `members`, so
    `/status`'s `inst.team.composition.members` is homogeneous regardless
    of whether it came from a save or a default fallback (see "Key
    decisions").
- **`app/app.py`**:
  - `GET /status`: `roster = teams.roster()` computed once per poll
    (top-level `"roster"` key in the response, alongside the existing
    `engines` field); `compositions = teams.load_compositions()` read once
    per poll (same "read once, avoid staleness" precedent `_load_deploy_map()`
    already establishes); per-instance `inst["team"]["composition"]` is the
    saved composition if one exists for that project, else
    `default_team_composition()`'s own pick if `ok`, else `None` -- computed
    unconditionally for every project regardless of `status`, matching
    `team`'s own existing "always present" treatment.
  - `GET /projects/<name>/team/grounding` (new): inserted into `do_GET`'s
    existing `else` branch (the "not `/status`" fallthrough), 404 if `name`
    isn't in `instance_names()`, else calls `teams.load_grounding()` and
    returns only `{"files": [{"label", "relpath", "byte_count"}, ...],
    "skipped": [...]}` -- never `content`/`digest`/`headings`. No TOTP
    check (matches `/status`'s own `_authed()`-only gating, already
    satisfied by the time this branch is reached).
  - `POST /projects/<name>/team/start`: if `"lead" in body and "members" in
    body`, validates via `teams.validate_composition()`, saves via
    `teams.save_composition()` on success (**before** calling
    `launch_team()`, so a later launch failure -- e.g. a dirty tree or
    session collision -- never discards the operator's picker choice),
    re-derives the lead's live `tier` from a second `teams.roster()` call
    (the wire format never carries `tier` -- see "Key decisions"), and
    extracts plain member-name strings for `launch_team()`. If either key
    is absent, behavior is byte-for-byte unchanged from 6d:
    `default_team_composition()` is used, `compositions.json` is neither
    read nor written. The response always includes `lead`/`members` in the
    same shape either path produces (lead with `tier`, members as a plain
    string list).
  - New CSS: `.team-configure-row`, `.team-configure-btn`,
    `.team-picker`, `.team-lead-picker`, `.team-mates-picker`,
    `.team-grounding`, `.team-tier-3-caveat`, `.team-validation-error` --
    reuses every existing color token (`#4da6ff`, `#ffb648`, `#34c759`,
    `#ff6b6b`) docs/design.md's own contrast analysis already covers; no
    new tokens.
  - New JS: `ROSTER`/`TEAM_BY_NAME` globals (populated in `refresh()`,
    mirroring `ENGINE_LABELS`'s own pattern); `teamPickerOpen`/
    `teamPickerInitialized`/`teamPickerLead`/`teamPickerMembers`/
    `teamGroundingCache` per-project client state (mirroring `teamTaskText`'s
    own "survives `refresh()`'s full-row re-render" pattern);
    `teamCompositionError()` (client-side mirror of
    `validate_composition()`'s rules); `toggleTeamPicker()`,
    `onTeamLeadChange()`, `onTeamMateToggle()`, `fetchTeamGrounding()`,
    `renderTeamGrounding()`, `renderTeamPicker()`, `updateTeamStartButton()`
    (new); `teamRow()` extended with the composition-aware idle branch;
    `actionBody()`'s `kind === 'team-start'` branch extended to include
    `lead`/`members` only when the picker is open with a currently-valid
    composition; `doTeamStart()` extended to run the client-side
    composition check before dispatch.
- **`tests/test_teams_composition.py`** (new, 19 tests) -- `validate_composition()`
  against every rule (valid tier-1/2/3 lead, missing/unknown/misconfigured
  lead, empty/duplicate/unknown/non-delegate-capable members, lead-also-a-
  member, an Ollama lead excluding nothing from members since it isn't
  itself an `engines.d` entry) and `load_compositions()`/`save_composition()`
  (missing-file, corrupt-file, round-trip, tier/schema_flag_error stripped
  from a persisted lead, multi-project upsert, no leftover `.tmp` file).
- **`tests/test_team_routes.py`** (extended, +16 new tests across 4 new
  classes -- corrected count, per the reviewer's own recount (3 + 4 + 8 + 1
  = 16); originally miswritten as "+11" -- 1 existing assertion updated for
  the additive `composition` field): `StatusRosterAndCompositionTests`
  (3 tests: roster reflects `engines.d`
  live with no cache; composition falls back to default when nothing is
  saved; composition prefers a saved value over the default),
  `TeamGroundingEndpointTests` (4 tests: 404 on an unknown project; an
  empty-repo project returns `files: []`, not an error; a found file's
  response shape is exactly `{label, relpath, byte_count}`, and the
  response body never contains the file's real content or a `digest` key;
  no TOTP prompt), `TeamStartWithCompositionEndpointTests` (8 tests: a
  submitted composition wins over the default and is persisted; empty
  members/duplicate members/lead-also-a-member/unknown-lead are each
  rejected with no worktree or session created; a stale saved composition
  referencing a since-removed engine is rejected, never silently
  substituted; omitting `lead`/`members` is byte-for-byte the unchanged 6d
  default path, confirmed by asserting `compositions.json` is neither read
  into nor written by that call; a composition is saved even when
  `launch_team()` itself later refuses because a team is already running
  for that project), `CompositionSurvivesRealProcessRestartTests` (1 test;
  see "Key decisions" for why this spawns a genuinely separate `python3`
  subprocess rather than just re-calling `load_compositions()`
  in-process).
- **`tests/test_team_frontend.js`** (extended, +11 new tests, plus
  `statusWith()`/`setupCase()` gained an optional `roster` parameter and two
  new test-harness helpers, `waitForFetch()`/`openPicker()`, for the
  picker's two-fetch open sequence -- see "Key decisions"): a saved
  composition renders the "Configure team..." link with the panel still
  closed; `composition === null` renders the refusal text, omits the
  configure link, and disables Start with no picker at all; opening the
  picker fetches grounding and lists every roster member as a lead option;
  the saved composition pre-selects the lead and excludes it from the
  teammate checkboxes; a tier-3 lead shows the caveat and is never blocked;
  the Ollama roster entry never appears as a teammate checkbox; grounding
  renders as a fixed four-slot checklist with an absent file explicitly
  marked "not found"; deselecting the last teammate shows the validation
  error and disables Start; a valid open composition is included in the
  POST body; a closed picker omits `lead`/`members` entirely (unchanged 6d
  body shape); an invalid open composition blocks dispatch client-side with
  the specific reason shown inline.
- **`docs/design.md`**: not edited by the developer stage -- already
  contained the ux-designer's 6e section (colors/copy/markup/state diagram)
  at the start of this cycle; implemented as written, no deviations to the
  visual spec itself (see "Deviations from spec" for the one behavioral gap
  the design itself left unresolved).

## Key decisions / tradeoffs

- **`inst.team.composition.members` is always a plain list of engine-name
  strings, never the wire format's list-of-dicts.** The spec's own
  "Proposed approach" doesn't pin the exact shape `load_compositions()`
  stores for `members` (only that `lead` stores `kind`+`name`), and the
  design doc's "API shape for composition in JSON" section describes the
  *outgoing POST body* shape (`[{"kind": "engine", "name": str}, ...]`),
  not the `/status` read-back shape. Rather than inventing a second,
  inconsistent members shape for persisted/default-fallback state, both
  `save_composition()`'s stored `members` and `default_team_composition()`'s
  pre-existing `members` (list of plain strings) now agree, so the
  frontend's pre-selection logic (`new Set(comp.members)` /
  `members.includes(lead.name)`) never has to branch on where the
  composition came from. The wire format the browser actually POSTs
  (`{"kind": "engine", "name": str}` per member) is preserved exactly as
  designed -- this only affects the read-back/persisted shape, which the
  design doc doesn't specify either way.
- **The `POST /team/start` route re-derives the lead's live `tier` via a
  second `teams.roster()` call, rather than trusting the client's own
  submitted composition or reusing `validate_composition()`'s internal
  roster snapshot.** `state["lead"]["tier"]` is read directly by
  `_call_lead()`'s tier-dispatch (`app/teams.py` line ~2504/2558) -- a
  `lead` dict without a `tier` key would raise `KeyError` the first time
  the driving thread ran. The wire format's `lead` object deliberately
  carries no `tier` (docs/spec.md: composition storage strips it, "always
  re-derived live from `roster()`"), so the route must resolve it itself,
  the same way `_cli_team_start()`/`_cli_team_launch()` already resolve a
  CLI-supplied `--lead`'s tier from `_lead_tier_for_engine()` before
  building their own `lead` dict. This is a second `roster()` call
  (`validate_composition()`'s own internal one, plus this one) -- the same
  "one extra directory scan, not worth threading a pre-loaded roster
  through for" cost the spec already accepts for `/status`'s `roster` field
  next to `default_team_composition()`'s own internal call.
- **`CompositionSurvivesRealProcessRestartTests` spawns a real, separate
  `python3` subprocess running its own fresh `ThreadingHTTPServer`,
  pointed at the same `TEAM_STATE_DIR`/`PROJECTS_DIR`/`ENGINES_DIR`.**
  `load_compositions()` has no in-memory cache at all -- it re-reads
  `compositions.json` from disk on every single call, so there's nothing a
  same-process re-call could get subtly wrong that a fresh process
  wouldn't also get right *today*. The acceptance criterion is explicit
  that this be "verified by restarting the process in a test, not just
  re-calling the function in-process" -- read as a guard against a FUTURE
  regression (an in-memory cache added later with no invalidation), so the
  test spawns a genuinely independent OS process rather than taking the
  weaker in-process shortcut, going further than `ServiceRestartSimulationTests`'
  own documented "impractical to literally restart, so prove the property
  that actually matters via the closest equivalent" compromise for the
  analogous `_team_threads` case (that test's own docstring explicitly
  flags a real separate-process restart as impractical for THAT case,
  which involves a live tmux session + background thread this test's own
  case doesn't -- compositions.json is a plain file, so a real subprocess
  boundary is both achievable and the more faithful proof here).
- **The picker's two-fetch open sequence (`fetchTeamGrounding()` then its
  own trailing `refresh()`) needed a `waitForFetch()` test helper, not a
  fixed tick count.** `toggleTeamPicker()` is not itself `async` (it calls
  `fetchTeamGrounding()` fire-and-forget so the picker can render
  immediately with a "Loading grounding files…" placeholder rather than
  blocking); a test awaiting `c.call('toggleTeamPicker', name)` therefore
  awaits nothing real. `waitForFetch()` polls `pendingFetches` with `tick()`
  until the wanted URL actually appears, rather than guessing how many
  microtask ticks deep the real chain resolves to -- the same generally
  more robust pattern already used once padding was needed, now named and
  reused across every new test that opens the picker (`openPicker()`).
- **The DOM stub's `.disabled` property is not a reliable post-`refresh()`
  assertion target.** `refresh()` replaces `#rows.innerHTML` with a raw
  HTML string; the test harness's `document.innerHTML` setter (shared with
  `tests/test_deploy_frontend.js`) stores that string verbatim without
  parsing it into stub elements, so a `document.getElementById('start-btn-
  proj')` call afterward returns an unrelated, never-touched stub whose
  `.disabled` is still its default `false` -- not what the rendered markup
  actually says. Fixed by slicing the button's own markup out of the
  rendered HTML string and asserting on the literal `disabled` attribute's
  presence, the same technique the pre-existing "idle (team null)... a
  disabled Start team button" test already used; only spots that DIRECTLY
  set `.disabled` via `getElementById` (`updateTeamStartButton()`,
  `handleActionResult()`) can be asserted through the stub itself.
- **The `composition === null` ("no usable roster member at all") case
  reuses the exact fixed message from docs/design.md's "No Roster
  Available" state, not the real `default_team_composition()` error text.**
  `/status`'s `composition` field is `None` whenever `default_team_
  composition()` returns `{"ok": False, ...}` -- its actual `error` string
  is discarded, per spec's own "Proposed approach" wording ("else
  `default_team_composition()`'s result if `ok`, else `None`"), which never
  says to thread the error text itself through `/status`. The spec's
  "Proposed approach" §3 separately says the frontend should "render its
  error text in place of the picker," which isn't literally satisfiable
  from a bare `None` -- design.md resolves this gap with a fixed, generic
  message ("No roster members available. Add an engine to engines.d or
  configure TEAM_LLM_BASE_URL/TEAM_LLM_MODEL."), which is what's
  implemented here (see "Deviations from spec"). The real, specific
  `default_team_composition()` error text is still reachable in practice:
  it's the composition===null case's own Start button that's omitted
  (matching the design), but the plain 6d `POST .../team/start` path
  (`lead`/`members` both absent) still surfaces the exact backend message
  through the existing `team-msg` slot if a caller ever POSTs one directly
  (e.g. a stale client) -- this cycle didn't need to add a mechanism for
  that since 6d's own error-message plumbing already covers it.

## Deviations from spec

- **`composition === null`'s frontend message is a fixed, generic string,
  not the real `default_team_composition()` error text the spec's own
  "Proposed approach" §3 literally asks for ("render its error text").**
  See "Key decisions" above for why this is unsatisfiable as literally
  written (the error text is never threaded through `/status`) and why
  design.md's own resolution (a fixed message, matching its "No Roster
  Available" state) is what's implemented. This is a faithful reading of
  the design doc, which is more specific than the spec on this exact
  point and was written after the spec to resolve open questions like this
  one -- flagging it here rather than silently picking one interpretation,
  since the spec's own prose and the design's own rendered mockup disagree
  on it.
- **No other deviations.** Every route shape, validation rule, error
  message wording, and picker markup/class name follows docs/spec.md
  §"Proposed approach" and docs/design.md's own "Implementation notes for
  the developer" section directly.

## Known limitations

- **CORRECTED (reviewer fix round -- see the appended section at the end of
  this document): the claim below, as originally written, was wrong about
  the "Tier-3-Only Roster" state and is kept here struck through/replaced
  rather than silently rewritten, since it was the reviewer's own must-fix
  finding.** Original claim: "'Tier-3-Only Roster' falls out automatically
  ... no special-case branch needed ... confirmed by `test_team_frontend.js`'s
  'a tier-3 lead shows the plain-language reliability caveat, never
  blocked' test." That test's own roster was `[prose3 (tier 3), helper
  (tier 2)]` with a pre-supplied, already-non-null saved composition
  (`comp = {lead: {kind: 'engine', name: 'prose3'}, members: ['helper']}`)
  -- it only ever exercised `renderTeamPicker()`'s per-member rendering
  once the picker was already reachable, never the actual no-saved-
  composition, roster-is-real-but-tier-3-only path `docs/design.md`'s
  "Tier-3-Only Roster" state is written for. That path routed through
  `GET /status`'s own `composition` computation, which DID need a real,
  new branch: before the fix, `default_team_composition()` refusing (which
  it always does for a tier-3-only roster, per 6d part 2's settled "never
  auto-pick a tier-3 lead as the default" rule) collapsed straight to
  `composition = None`, indistinguishable from a genuinely empty roster,
  which permanently disabled the Start button with no way to ever open the
  picker at all -- confirmed live (reviewer's own repro, independently
  reproduced) and now fixed; see the appended section for the actual fix
  and its own regression tests (`StatusRosterAndCompositionTests.
  test_composition_not_none_for_tier3_only_roster_with_no_saved_composition`
  in `tests/test_team_routes.py`, and `test_team_frontend.js`'s "a
  tier-3-only roster with no saved composition still shows a Configure
  link... and the picker opens with tier-3 selectable" test). Once past
  that `/status` branch, the rendered *picker itself* (`renderTeamPicker()`/
  `teamRow()`) genuinely is the same generic per-member-tier logic with no
  dedicated all-tier-3 code path -- that part of the original claim holds;
  it was the upstream `/status` gate that was wrong, not the picker's own
  render logic.
- **"Composition Saved" (the green "✓ Composition saved and team started"
  message) is the same `handleActionResult()`/`.team-msg.success` path 6d
  already built for every successful start, unchanged by 6e**, since a 200
  response from `POST .../team/start` looks identical to the frontend
  whether or not the body included a submitted composition. (This part of
  the original claim was correct and is unaffected by the fix above.)
- **No dedicated "save composition without starting" route.** Exactly as
  scoped by the spec's own "Open questions" -- a composition is only ever
  saved as a side effect of a validated `POST .../team/start` call.
- **Per-teammate `--allowedTools`/`--sandbox` scoping remains out of
  scope**, per the spec's own "Non-goals" -- untouched by this cycle.
- **The picker's client-side `teamCompositionError()` intentionally has no
  duplicate-teammate check** -- structurally impossible via checkboxes (a
  `Set`), matching docs/design.md's own explicit note. The server's
  `validate_composition()` still checks it (reachable only via a
  hand-crafted request or a stale saved composition, never via the real
  UI).

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax | `python3 -c "import ast; ast.parse(open('app/app.py').read())"` / same for `teams.py` | clean |
| New backend test file alone | `python3 -m unittest tests.test_teams_composition -v` | **19 passed** |
| Extended route test file alone | `python3 -m unittest tests.test_team_routes -v` | **36 passed** (was 20 before this cycle) |
| Backend regression sweep (composition + routes + lead + lifecycle + grounding + headless + cancel) | `python3 -m unittest tests.test_team_routes tests.test_teams_composition tests.test_teams_lead tests.test_teams_lifecycle tests.test_teams_grounding tests.test_teams_headless tests.test_teams_cancel -v` | **420 passed** |
| Full Python suite, twice (once via `unittest discover`, once via `pytest`) | `python3 -m unittest discover -s tests` / `uv run --with pytest python -m pytest tests/ -q` | **725 passed** both runs (was 674 before 6d part 2b's `--with-ollama` cycle added 16; unchanged by 6e itself since only new/extended files, no removals) |
| Extended frontend suite alone | `node tests/test_team_frontend.js` | **28/28 passed** (was 17 before this cycle) |
| Full Node suite sweep (confirm no cross-feature regression) | `node tests/test_{team,deploy,singleton_toggle,upload}_frontend.js` | 28/28, 9/9, 15/15, 8/8 -- all pass |

## How to verify locally

```bash
# Syntax
python3 -c "import ast; ast.parse(open('app/app.py').read())"
python3 -c "import ast; ast.parse(open('app/teams.py').read())"

# New backend test file alone
python3 -m unittest tests.test_teams_composition -v

# Extended route test file alone (roster/composition /status fields, the
# new grounding route, the extended /team/start body handling, and the
# real-separate-process restart-persistence test)
python3 -m unittest tests.test_team_routes -v

# Full Python suite (either runner works; both were run this cycle)
python3 -m unittest discover -s tests
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q

# Extended frontend suite alone
node tests/test_team_frontend.js

# Full Node suite (confirm no cross-feature regression)
node tests/test_team_frontend.js
node tests/test_deploy_frontend.js
node tests/test_singleton_toggle_frontend.js
node tests/test_upload_frontend.js
```

---

# Implementation: Roster & composition UI (sub-spec 6e) -- reviewer fix round (`composition === null` over-collapse)

## Summary

Fixes the reviewer's must-fix finding against the 6e cycle above:
`GET /status`'s `inst.team.composition` computation collapsed THREE
distinct `default_team_composition()` refusal reasons (a genuinely empty
roster, a single already-picked-lead engine with nothing left to delegate
to, and a real-but-tier-3-only roster -- the case 6d part 2 settled must
never be auto-picked as the automatic DEFAULT lead) into the same bare
`composition = None`. The frontend's `composition === null` check can't
tell these apart, so a project with one real tier-3 `engines.d` entry and
no saved composition rendered the permanent "No roster members available"
refusal with a disabled Start button and no way to ever open the picker --
directly breaking this sub-spec's own headline acceptance criterion
("tier-3 must be selectable as lead, never blocked") for exactly the
scenario `docs/design.md`'s own "Tier-3-Only Roster" state was written to
cover. Fixed by computing `composition` off `roster()` being non-empty
(the reviewer's own suggested direction) rather than off
`default_team_composition()["ok"]` alone: when the roster has at least one
real member but the automatic default declined to pick one, `/status` now
returns `composition = {"lead": None, "members": []}` -- a real, non-null
object with nothing pre-selected, mirroring `docs/design.md`'s own "Choose
a lead..." empty-select default -- so the frontend still opens the picker
and lets the operator pick explicitly. `composition` stays `None` only for
a genuinely empty roster (no `engines.d` entries at all, no Ollama
configured), where the "No roster members available" permanent-refusal
state is still correct.

## Changes by file

- **`app/app.py`** (`GET /status`, the `inst["team"]["composition"]`
  computation, ~line 3489 as of the 6e cycle above): the `else` branch (no
  saved composition) now branches three ways instead of two --
  `default_team_composition()["ok"]` (unchanged: use its pick),
  `elif roster:` (**new** -- `roster` is this same `/status` call's own
  already-computed top-level list, the authoritative "does a real,
  pickable member exist at all" signal, independent of whether the
  automatic default could use one) set `composition = {"lead": None,
  "members": []}`, `else` (genuinely empty roster, unchanged behavior)
  `composition = None`. No other route, no validation rule, no persistence
  mechanism touched -- `validate_composition()`, the grounding route, and
  `save_composition()`/`load_compositions()` are exactly as the 6e cycle
  above left them, per the reviewer's own explicit "don't re-touch" scope
  for this fix.
- **`tests/test_team_routes.py`** (+2 tests in
  `StatusRosterAndCompositionTests`, now 5 tests total in that class --
  16 + 2 = 18 new tests total across the 4 classes 6e's "Changes by file"
  section above names):
  - `test_composition_not_none_for_tier3_only_roster_with_no_saved_composition`
    -- the actual regression test for the reviewer's own live repro: one
    tier-3 `engines.d` entry, no Ollama, no saved composition. Asserts
    `s["roster"]` still has the one real entry (`roster()` itself was
    never broken) and `inst.team.composition == {"lead": None, "members":
    []}` (not `None`).
  - `test_composition_stays_none_for_a_genuinely_empty_roster` -- the
    contrast case, proving the fix didn't overcorrect: no `engines.d`
    entries, no Ollama, `s["roster"] == []`, and `composition` is still
    `None`.
- **`tests/test_team_frontend.js`** (+1 test, now 29 tests total in this
  file, up from the 28 the 6e cycle above reports): "a tier-3-only roster
  with no saved composition still shows a Configure link (not the
  permanent refusal), and the picker opens with tier-3 selectable" --
  drives the exact `composition = {lead: null, members: []}` shape
  `/status` now sends through `teamRow()`/`toggleTeamPicker()`/
  `renderTeamPicker()` directly (same "call the exported function, no
  renderer needed" technique this file's own header describes), asserting
  (1) the closed-picker row shows "Configure team..." and NOT "No roster
  members available", (2) opening the picker renders `prose3` as a
  selectable, non-`disabled` lead `<option>`, and (3) actually selecting
  it as lead succeeds (`teamCompositionError()` returns the separate
  "At least one teammate is required" reason, not a lead-selection block --
  this single-tier-3-engine roster has no OTHER engine left to be a
  teammate, so a real start still isn't reachable here, but the lead pick
  itself is never blocked, which is the property this fix restores).
- **`docs/implementation.md`** (this file): corrected the 6e cycle's own
  "Known limitations" claim about the "Tier-3-Only Roster" design state
  "falling out automatically... no special-case branch needed" -- that
  claim's own cited proof (`test_team_frontend.js`'s pre-existing tier-3-
  caveat test) used a roster of `[prose3 (tier 3), helper (tier 2)]` with
  an already-non-null, pre-supplied saved composition, and never actually
  exercised the no-saved-composition path that was broken. Left the
  original wrong claim in place, marked corrected, rather than silently
  rewriting history (see the "Known limitations" section above). Also
  corrected "Changes by file"'s arithmetic error: `tests/test_team_routes.py`
  gained +16 new tests across the 4 new classes this cycle (3 + 4 + 8 + 1,
  the reviewer's own recount), not the originally-written "+11".

## Key decisions / tradeoffs

- **Nothing pre-selected (`lead: None, members: []`), not the roster's
  first entry, for the "real roster, no automatic pick" case.** The
  reviewer's finding left this as the developer's own call ("nothing
  pre-selected, or the first roster member pre-selected -- your call on
  which reads better against docs/design.md's existing state"). Nothing-
  pre-selected was chosen because it's a direct, literal read of
  `docs/design.md`'s own "Idle, Picker Expanded" state, which already
  specifies `<option value="">Choose a lead...</option>` as the default
  when there's no saved composition and no automatic default to seed from
  -- auto-selecting the first roster entry would be inventing a NEW
  implicit default the design doc never describes (and would risk
  silently picking a tier-3 lead on the operator's behalf without them
  ever having chosen it, which cuts against the same "never auto-pick
  tier-3" principle 6d part 2 settled for the non-picker default path).
- **The fix branches on `roster` (the `/status` handler's own
  already-computed top-level list), not on a fresh `teams.roster()`
  call.** `roster()` has no cache and is meant to be re-read every call
  (`engines.d` can be hand-edited without a restart), but `/status` already
  computes it exactly once per poll for the top-level `"roster"` field --
  reusing that same list for the per-project branch avoids a second,
  redundant directory scan inside the per-instance loop, the same "don't
  thread a pre-loaded value through, but don't call the scanning function
  twice in the same handler either" balance the 6e cycle above already
  struck for `default_team_composition()`'s own internal `roster()` call.
- **No change to `default_team_composition()`, `validate_composition()`,
  or any persistence function.** The fix is entirely local to `/status`'s
  own read-side computation of what to show the picker -- the three
  refusal reasons `default_team_composition()` itself distinguishes
  internally (empty roster / single-engine-already-lead / tier-3-only) are
  unchanged; `/status` just stopped discarding the distinction between
  "roster is empty" and "roster is real but the default declined."

## Deviations from spec / design

None. `docs/spec.md`'s own "Proposed approach" §2 already says `/status`'s
composition field is "`default_team_composition()`'s result if `ok`, else
`None`" -- read literally, this fix is a correction of an under-specified
edge case in that same sentence (the spec doesn't separately address what
should happen when the roster is real-but-declined vs. genuinely empty),
resolved in the direction the spec's own Goal #3 ("tier-3 must be
selectable as lead, never blocked") and the design's own dedicated
"Tier-3-Only Roster" state both require. Not a new deviation beyond what
the 6e cycle above already discloses.

## Known limitations

No new known limitations introduced by this fix. The single-tier-3-engine
scenario this fix's own regression tests exercise (one tier-3 engine, no
Ollama, no other engine) still cannot actually START a team -- picking that
engine as lead leaves zero possible teammates, so `teamCompositionError()`
always reports "At least one teammate is required" for that specific
roster shape. This is not a defect: it's the same structural requirement
`validate_composition()`/`default_team_composition()` already enforce (a
team needs a lead AND at least one teammate) -- the fix's own scope is
"the picker must be reachable and the tier-3 lead pick itself must never
be blocked," not "every possible roster shape can complete a start,"
which was never true even before this defect (a single-engine roster of
ANY tier has always had this same limit, per `default_team_composition()`'s
own pre-existing "only one headless-eligible engine... selected as lead"
refusal message).

## Verification status

| Check | Command | Result |
|---|---|---|
| Fix-scoped route tests | `python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests -v` | **5 passed** (3 pre-existing + 2 new) |
| Extended route test file alone | `python3 -m unittest tests.test_team_routes -v` | **38 passed** (was 36 before this fix round) |
| Extended frontend suite alone | `node tests/test_team_frontend.js` | **29/29 passed** (was 28 before this fix round) |
| Full Node suite sweep (confirm no cross-feature regression) | `node tests/test_{team,deploy,singleton_toggle,upload}_frontend.js` | 29/29, 9/9, 15/15, 8/8 -- all pass (61 total) |
| Full Python suite | `python3 -m unittest discover -s tests -v` | **727 passed**, 0 failures/errors (was 725 before this fix round; +2 for the two new `StatusRosterAndCompositionTests` cases) |

## How to verify locally

```bash
# The fix-scoped regression tests (both languages)
python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests -v
node tests/test_team_frontend.js

# Full suites
python3 -m unittest discover -s tests -v
node tests/test_team_frontend.js
node tests/test_deploy_frontend.js
node tests/test_singleton_toggle_frontend.js
node tests/test_upload_frontend.js

# Manual reproduction of the fixed scenario (same shape as the reviewer's
# own live repro): a project with one tier-3 engines.d entry, no Ollama,
# no saved composition -- confirm GET /status now returns a real
# composition object (not null) with nothing pre-selected.
export TOTP_SECRET=JBSWY3DPEHPK3PXP AUTH_MODE=simple SIMPLE_USERNAME=x SIMPLE_PASSWORD=x
export ENGINES_DIR=/tmp/scratch-engines-6e-fix PROJECTS_DIR=/tmp/scratch-projects-6e-fix
mkdir -p "$ENGINES_DIR" "$PROJECTS_DIR/demo"
cat > "$ENGINES_DIR/prose.engine" <<'EOF'
LABEL=Prose
CMD=unused
HEADLESS_CMD=echo hi
HEADLESS_FORMAT=plain
HEADLESS_PROMPT=arg
EOF
python3 app/teams.py roster   # confirms: prose, tier 3
# (start the web app, log in, then) curl the authenticated session's
# GET /status and confirm inst.team.composition for "demo" is
# {"lead": null, "members": []}, not null.
```

---

# Implementation: Overwatch feed + escalation inbox -- backend API (sub-spec 6f part 1)

## Summary

Adds the three read/write HTTP routes and one additive `/status` field
`docs/spec.md` scopes for this part -- no HTML/CSS/JS, no `docs/design.md`
section, same precedent as 6d part 1: `GET /projects/<name>/team/events`
(cursor-based, per-file byte-capped, merges `transcript.jsonl` + every
teammate's own `agents/<agent>.jsonl` into one chronological stream), `GET
/projects/<name>/team/inbox` ("is there a pending question right now"), and
`POST /projects/<name>/team/resolve` (answers a pending `ask_user` and
resumes the lead loop on a background thread, mirroring `/team/start`'s own
non-blocking discipline). `_cli_team_resolve()`'s resolve-and-resume logic
is extracted into a new shared `teams.resolve_ask_user()` so the CLI and the
new route call the same code, verified identical rather than assumed.

Testing this for real (a genuinely-concurrent, zero-synchronization
double-`POST /team/resolve` test) surfaced a real, previously-nonexistent
crash bug in the extracted `resolve_ask_user()` -- an unhandled
`FileNotFoundError` from a lost `os.replace()` race that reset the client's
HTTP connection instead of returning a clean `400`. Fixed with a narrow
`try/except OSError` around the move-then-persist step (see "Key
decisions") -- not a new lock, just converting an already-anticipated race's
loser into the shaped result the design already intended for it.

## Changes by file

- **`app/teams.py`** (new "Overwatch feed + escalation inbox" section,
  inserted between `sweep_dead_teams()` and the `# ─── CLI ───` marker;
  `_cli_team_resolve()` rewritten in place):
  - `load_state_for_project(run_id, project_name) -> dict | None` --
    `_load_state(run_id)` plus an ownership check
    (`state["project_name"] == project_name`), collapsing "no such run" and
    "wrong project" into one `None` outcome; used by both new `GET` routes
    (which reply `404` either way). `POST /team/resolve` needs the two
    reasons told apart for its own error message (docs/spec.md §3), so
    that route calls `_load_state()` directly instead.
  - `tail_jsonl_events(path, offset, max_bytes, agent=None) -> (events,
    new_offset, truncated)` -- the stricter, per-poll-bounded cousin of
    `_tail_log_once()` docs/spec.md §1 specifies: reads at most `max_bytes
    + 1` bytes past `offset` (the `+1` solely to detect `truncated`, never
    itself parsed), holds a trailing partial line across calls (never
    parses it, walks `new_offset` back to the last complete `\n`), and
    turns a malformed/non-dict JSON line into one synthetic `kind: "error"`
    envelope rather than raising or dropping the rest of the file. The
    `agent` keyword (not in the spec's own 3-arg prose description) is a
    small, additive convenience used only to label the synthetic
    malformed-line envelope's `text`/`agent` fields more usefully than a
    bare file basename would; omitting it still works (falls back to
    `os.path.basename(path)`).
  - `resolve_ask_user(run_id, answer) -> {"ok": True, "state": state} |
    {"ok": False, "error": str}` -- extracted from `_cli_team_resolve()`'s
    own body (docs/spec.md §3): always reloads state fresh via
    `_load_state(run_id)` itself (never accepts a caller-supplied state
    dict), so a concurrent resolve for the same `run_id` always re-checks
    status against what's actually on disk at call time. The move-then-
    persist step is wrapped in its own `try/except OSError` -- see "Key
    decisions" for why this was added beyond the spec's own literal
    pseudocode.
  - Two new config constants: `TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL`
    (default 65536, unmeasured per docs/spec.md's own "Open questions" --
    same reasoning as that section gives) and
    `TEAM_ASK_USER_ANSWER_MAX_CHARS` (default 2000 -- not pinned by the
    spec itself, chosen as "generous for a free-text answer, still catches
    an obviously-wrong paste," same order of magnitude as this module's
    other short-text caps).
  - `_cli_team_resolve()` is now a thin wrapper: calls `resolve_ask_user()`,
    prints `f"error: {result['error']}"` and returns `1` on failure, else
    calls `_drive_and_report(result["state"])` -- zero change to the
    subcommand's own observable behavior (verified by the pre-existing,
    unmodified `ResolveInSeparateProcessTests`, still green, plus this
    cycle's own CLI-vs-route identical-persisted-state test).
- **`app/app.py`**:
  - `do_GET`'s `else` branch (the "not `/status`" fallthrough) now computes
    `split = urllib.parse.urlsplit(self.path)` and `query =
    urllib.parse.parse_qs(split.query)` once, routes on `split.path`
    instead of the bare `self.path` (the `/team/grounding` branch is
    otherwise unchanged) -- the first GET route in this file to carry a
    query string (`?run_id=`/`?cursor=`).
  - `_handle_team_events(name, query)` / `_handle_team_inbox(name, query)`
    (new handler methods) plus a shared `_team_events_run_and_ownership()`
    helper for the "which run, does the caller own it, defaults to
    `latest_run_for_project()`" resolution both routes need identically.
    `_handle_team_events` merges `[("lead", teams._transcript_path(run_id))]
    + [(m, teams._agent_log_path(run_id, m)) for m in state["members"]]`
    via one `teams.tail_jsonl_events()` call per file, sorts by `(ts, agent,
    seq)`, and returns `{"run_id", "events", "cursors", "truncated"}`
    exactly as docs/spec.md §1 shapes it. `_handle_team_inbox` reads
    `inbox.json` directly for the `blocked_ask_user` case, falling back to
    the spec's own fixed "check `tmux attach`" question on any
    `OSError`/`ValueError` (missing/corrupt file) -- always `pending: true`
    in that state, never a false `pending: false`.
  - New module-level `_parse_events_cursor(raw) -> dict` -- a malformed
    `?cursor=` value (not JSON, not an object, a non-int/negative offset)
    degrades to `{}`, never a `400`.
  - `POST /projects/<name>/team/resolve` (new route, inserted after
    `/team/stop`): ownership + `run_id`-defaults-to-latest (via
    `teams._load_state()` directly, not `load_state_for_project()`, so the
    three distinct error reasons docs/spec.md §3 names -- "no run found for
    this project" / "this run belongs to a different project" / "no
    pending question for this project" -- stay distinguishable), then
    `answer.strip()` non-empty/≤`TEAM_ASK_USER_ANSWER_MAX_CHARS` validation
    *before* calling `teams.resolve_ask_user()`, then (on `{"ok": True}`) a
    defensive `_team_threads_get(name) is not None` check before spawning a
    **new** `cancel_event` + `threading.Thread(target=
    _run_team_in_background, ...)` and returning `200 {"ok": true,
    "run_id"}` immediately -- reuses `_team_threads_set()`/
    `_run_team_in_background()` verbatim, no new bookkeeping.
  - `GET /status`'s per-instance loop: one new line, `waiting_on_you = run
    is not None and run["status"] == "blocked_ask_user"`, added to the
    existing `inst["team"]` dict -- every other key/value untouched.
- **`config/switchboard.env.example`** -- new "Optional: overwatch feed +
  escalation inbox (6f part 1)" section documenting both new env vars,
  following the file's existing per-subsystem section convention.
- **`tests/test_teams_headless.py`** (+8 tests, new `TailJsonlEventsTests`
  class inserted right after `TailerTests`, its own direct precedent):
  pure unit tests, no HTTP/tmux -- missing file; reads every complete line
  from a fresh start; a second call with the returned offset sees only new
  events (no event returned twice); a trailing partial (torn-write) line is
  held across polls and picked up whole once its newline arrives; a
  malformed line becomes one `error` event with processing continuing for
  the rest of the file; a non-dict-JSON line is treated the same way; an
  oversized file (six same-width lines, an exact 3-line byte cap) truncates
  on a clean line boundary and a bounded sequence of follow-up polls
  recovers the full, non-duplicated sequence; a large file is never read in
  one call past `max_bytes + 1`.
- **`tests/test_team_routes.py`** (+27 tests across 3 new classes, +1 in
  `StatusRosterAndCompositionTests`, +1 existing full-dict `assertEqual`
  updated for the additive `waiting_on_you` field; module gains `import
  urllib.parse` and two shared helpers, `_envelope()`/`_append_jsonl()`):
  - `TeamEventsEndpointTests` (10) -- unknown project 404; no run ever
    started returns the exact clean empty-state shape; lead + teammate
    events merged and sorted chronologically (fixed `ts` values, not
    wall-clock-dependent); a member with no log file yet is present but
    empty; a repeat poll with the returned cursor returns zero events, no
    duplication; a malformed `?cursor=` value falls back to a full replay,
    not a `400`; a truncated agent reports the flag and a follow-up poll
    with the returned cursor drains the remainder with no gap/duplicate; a
    malformed log line becomes one `error` event, not a `500`; a
    cross-project `run_id` is `404` with no data leaked; a `finished` run
    (no thread running) still returns its full history.
  - `TeamInboxEndpointTests` (7) -- unknown project 404; no run
    `pending: false`; a non-blocked run `pending: false`; a genuinely
    blocked run returns the exact persisted question/header/options/
    multi_select shape; a missing `inbox.json` and a malformed one both
    still report `pending: true` with a non-empty fallback question and
    empty options; cross-project `run_id` 404.
  - `TeamResolveEndpointTests` (9) -- unknown project 404; not-blocked
    returns the specific "no pending question" `400` with no state
    mutation; no run at all returns "no run found"; an explicit `run_id`
    for a different project returns "this run belongs to a different
    project"; empty/oversized `answer` are rejected `400` before any
    mutation (`inbox.json` still present, status still
    `blocked_ask_user`); a genuinely blocked run with a valid answer
    resolves and returns in well under 3s, moves `inbox.json` →
    `inbox.resolved.json`, flips `status` off `blocked_ask_user`, and
    records one `ask_user_resolved` history entry with the submitted text;
    two genuinely-simultaneous (zero-synchronization) concurrent resolves
    always produce exactly one `200` and one `400` (see "Key decisions" for
    why the loser's exact reason is intentionally not pinned to one
    string); the CLI's `resolve_ask_user()` call and the route's own call
    produce identical `status`/`history` for the same `run_id`/`answer`
    input (the route's own background drive is neutralized via a no-op
    monkeypatch of `_run_team_in_background` so it can't race the
    comparison -- see "Key decisions").
  - `StatusRosterAndCompositionTests` +1 --
    `test_waiting_on_you_true_only_for_blocked_ask_user_never_for_
    escalated_max_rounds` walks all six run statuses, asserting
    `waiting_on_you` is `True` for exactly `blocked_ask_user`.
  - `TeamStopEndpointTests.test_status_idle_when_no_run_ever_started`'s
    pre-existing full-dict `assertEqual` updated to include
    `"waiting_on_you": False` -- the only pre-existing test in the repo
    asserting `inst["team"]`'s *entire* shape by exact equality (found via
    `grep -rn '\["team"\]' tests/`); every other pre-existing team-status
    test asserts individual keys and needed no change.

## Key decisions / tradeoffs

- **The genuinely-crashing race, found live, is fixed with a narrow
  `try/except OSError`, not a lock.** docs/spec.md "Edge cases" explicitly
  anticipates two concurrent `POST /team/resolve` calls and explicitly
  declines to lock-guard the load-check-persist sequence ("the same
  single-writer assumption every other run.json mutator in this codebase
  already carries... not a new risk introduced by this spec"). Testing
  that exact scenario with two genuinely simultaneous threads (no
  synchronization at all -- tighter than a realistic double-click) found
  something the spec's own prose didn't anticipate: the status-check race
  window is narrow but real, and the LOSER's `os.replace(inbox_path,
  _inbox_resolved_path(run_id))` call can hit a `FileNotFoundError` (its
  own target already renamed away by the winner) that propagated
  unhandled all the way out through `do_POST`, resetting the client's TCP
  connection instead of returning a clean `400`. This is squarely "fix it
  yourself, don't leave it for the reviewer to catch" (an unhandled
  exception reaching a request handler is a real robustness gap, newly and
  plausibly reachable via two browser tabs, that the pure-CLI-only
  predecessor never exposed this concretely). The fix wraps only the
  move-then-persist step in `try/except OSError`, converting the loser
  into the exact `{"ok": False, "error": "...is not blocked on ask_user
  (status=...)"}` shape the design already intended for "someone else got
  there first" -- it adds no new locking primitive and does not change WHO
  wins the race, only ensures the loser never crashes the connection.
  `TeamResolveEndpointTests.test_two_concurrent_resolves_exactly_one_
  succeeds` was run 20/20 clean after the fix (was crashing or failing on
  the majority of runs before it, both via the raw exception and via a
  test assertion that was initially too narrow about which of three
  legitimate 400 reasons the loser could get -- see that test's own
  in-line comment for all three).
- **`_team_events_run_and_ownership()` returns `(state, error_response)`
  rather than raising or returning a bare `None`.** Both `_handle_team_
  events`/`_handle_team_inbox` need to tell apart three outcomes (a real
  error to return immediately, "no run at all" -- not an error, an empty/
  `pending: false` response, or a real resolved state) with different
  follow-up handling per route; a two-tuple with an explicit `error_
  response` slot keeps both call sites a simple `if err is not None:
  return self._json(*err)` one-liner rather than a bespoke exception type
  for a route-local concern.
- **`POST /team/resolve`'s three distinct error strings required NOT
  reusing `load_state_for_project()`.** That helper (built for the two GET
  routes, which reply `404` for either "no such run" or "wrong project"
  identically) collapses both into `None`. The POST route's own spec text
  names three different strings for three different causes, so it calls
  `teams._load_state()` directly (same already-precedented pattern
  `_run_team_in_background()` itself uses, `app/app.py:1377`) and checks
  `state.get("project_name") != name` itself, rather than stretching one
  shared helper to serve two routes with different error-shape contracts.
- **The two-concurrent-resolves test neutralizes `_run_team_in_background`
  for the identical-persisted-state test, not for the concurrency test
  itself.** The concurrency test deliberately lets the real background
  thread run (an "ollama" lead with no `TEAM_LLM_BASE_URL` configured
  fails fast, `status` → `"error"`) since that's the realistic end-to-end
  behavior being verified; only the identical-persisted-state comparison
  (which needs to isolate `resolve_ask_user()`'s own effect from the
  UNRELATED, deliberately-async `team_run()` drive that follows it) patches
  `_run_team_in_background` to a no-op, via the same monkeypatch idiom
  `_patch_tmux()` already establishes in this file.

## Deviations from spec

- **`tail_jsonl_events()` gained an optional 4th `agent` keyword** beyond
  the spec's own literal 3-argument description (`path, offset, max_bytes`)
  -- used only to produce a more informative malformed-line message
  (`"malformed line in <agent>'s log..."`, matching the spec's own prose
  example verbatim) than a bare `os.path.basename(path)` fallback would.
  Omitting it still works exactly as the spec describes; this is additive,
  not a behavior change to anything the spec pins down.
- **The move-then-persist `try/except OSError` inside `resolve_ask_user()`**
  (see "Key decisions") is not in the spec's own "Proposed approach"
  pseudocode, which only describes the load-check-append-move-flip-persist
  sequence in the happy path plus a bare "the first to persist wins" for
  the race. Added because real concurrent testing found the race's loser
  could crash the request thread, not just lose gracefully as the spec's
  prose assumed -- flagged here rather than silently expanding scope,
  since it's a genuine (if narrow) behavior change from the spec's literal
  pseudocode, even though it doesn't change which caller "wins."
- **No other deviations.** Every route shape, status code, error-message
  wording, response field, and the `waiting_on_you` semantics (`true` only
  for `blocked_ask_user`, never `escalated_max_rounds`, per the spec's own
  settled "Open questions" reading) follow `docs/spec.md` "Proposed
  approach" directly. No `docs/design.md` section exists for this cycle,
  per spec (matching 6d part 1's precedent) -- no frontend code was
  written.

## Known limitations

- **The exact `400` reason a losing concurrent `POST /team/resolve` call
  gets is timing-dependent** (any of three legitimate strings -- see "Key
  decisions"/the test's own in-line comment) -- the spec's own acceptance
  criterion only pins "exactly one succeeds, the other gets an ordinary
  400," which this satisfies; it does not pin one specific string, and
  this implementation doesn't force one either (that would require the new
  locking the spec explicitly declines to add).
- **No merged-timeline rendering, filter UI, status strip, or escalation
  panel** -- explicitly out of scope for this part (6f part 2, next in the
  story, per `docs/spec.md` "Non-goals").
- **The `TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL` (65536) and
  `TEAM_ASK_USER_ANSWER_MAX_CHARS` (2000) defaults are both unmeasured**,
  same as the spec's own "Open questions" already flags for the byte cap;
  the answer-length default has no spec-pinned number at all and was
  chosen using the same "round, conservative, same order of magnitude as
  sibling caps" reasoning, not a measured real-traffic case -- both are
  plain env vars, tunable once 6f part 2's frontend shows real traffic.

## Verification status

| Check | Command | Result |
|---|---|---|
| Syntax | `python3 -c "import ast; ast.parse(open('app/app.py').read())"` / same for `teams.py` | clean |
| New `tail_jsonl_events()` unit tests alone | `python3 -m unittest tests.test_teams_headless.TailJsonlEventsTests -v` | **8 passed** |
| New route test classes alone | `python3 -m unittest tests.test_team_routes.TeamEventsEndpointTests tests.test_team_routes.TeamInboxEndpointTests tests.test_team_routes.TeamResolveEndpointTests -v` | **26 passed** |
| Concurrency test alone, repeated | `for i in $(seq 1 20); do python3 -m unittest tests.test_team_routes.TeamResolveEndpointTests.test_two_concurrent_resolves_exactly_one_succeeds; done` | **20/20 passed** (post-fix; was crashing/failing on most runs pre-fix) |
| Extended route test file alone | `python3 -m unittest tests.test_team_routes -v` | **65 passed** (was 38 before this cycle) |
| Pre-existing CLI-resolve regression (unmodified) | `python3 -m unittest tests.test_teams_lead.ResolveInSeparateProcessTests -v` | **2 passed**, unchanged |
| `StatusRosterAndCompositionTests` unmodified pass (per spec's own requirement) | `python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests -v` | **6 passed** (5 pre-existing + 1 new `waiting_on_you` test) |
| Full Python suite | `python3 -m unittest discover -s tests -v` | **762 passed**, 0 failures/errors (was 727 before this cycle; +35 new tests, 0 removed) |

## How to verify locally

```bash
# Syntax
python3 -c "import ast; ast.parse(open('app/app.py').read())"
python3 -c "import ast; ast.parse(open('app/teams.py').read())"

# This cycle's own new tests
python3 -m unittest tests.test_teams_headless.TailJsonlEventsTests -v
python3 -m unittest tests.test_team_routes.TeamEventsEndpointTests \
  tests.test_team_routes.TeamInboxEndpointTests \
  tests.test_team_routes.TeamResolveEndpointTests -v

# The concurrency fix, repeated (flaky before the fix; deterministic after)
for i in $(seq 1 20); do \
  python3 -m unittest tests.test_team_routes.TeamResolveEndpointTests.test_two_concurrent_resolves_exactly_one_succeeds; \
done

# Confirm the CLI's team-resolve is byte-for-byte unchanged
python3 -m unittest tests.test_teams_lead.ResolveInSeparateProcessTests -v

# Confirm /status's pre-existing fields are untouched
python3 -m unittest tests.test_team_routes.StatusRosterAndCompositionTests -v

# Full suite
python3 -m unittest discover -s tests -v
```
```

# Implementation: Overwatch feed + escalation inbox -- backend API (sub-spec 6f part 1) -- reviewer fix round (loser's `os.path.exists()` check racing past the winner's rename)

## Summary

Fixes the reviewer's must-fix finding against the 6f part 1 cycle above:
the developer's own prior fix wrapped `resolve_ask_user()`'s
move-then-persist step in `try/except OSError` around `os.replace()`,
which correctly turned the LOSER's own colliding `os.replace()` call into
a clean `{"ok": False, ...}` instead of an unhandled `FileNotFoundError`
reaching all the way out through `do_POST`. That fix left a second,
narrower instance of the same underlying check-then-act race unclosed:
the loser's own `if os.path.exists(inbox_path):` guard is itself a
separate check-then-act window, independent of the `os.replace()` call it
gates. A loser whose `os.path.exists()` call happens to land AFTER the
winner has already renamed the inbox file away simply observes `False` --
no exception anywhere, `os.replace()` is never even attempted -- and falls
straight through to flipping its own STALE in-memory `state["status"]` to
`"running"` and persisting it, silently clobbering the winner's
already-persisted `ask_user_resolved` history entry, and reporting
`{"ok": True}` for an answer that was never actually recorded. Fixed by
deleting the separate `os.path.exists()` guard entirely and calling
`os.replace()` unconditionally, so `os.replace()`'s own atomicity (via
`FileNotFoundError` on whichever caller loses) is the SOLE arbiter of "did
I win" -- collapsing the two independent check-then-act windows into one,
exactly as the already-fixed crash case relies on. `state["status"]` is
now only flipped and persisted AFTER `os.replace()` has already succeeded,
so a losing caller never touches (let alone persists) `state` at all.

## Changes by file

- **`app/teams.py`** (`resolve_ask_user()`, ~line 3752): removed the
  `if os.path.exists(inbox_path):` guard around the `os.replace()` call.
  `os.replace(inbox_path, _inbox_resolved_path(run_id))` is now called
  unconditionally inside the existing `try/except OSError` block; on
  success, `state["status"] = "running"` and `_persist(state)` now run
  AFTER the `try` block (previously both ran unconditionally inside it,
  reachable even when the `if` guard's own `os.path.exists()` check was
  `False` and no rename was attempted at all). On `OSError` (now including
  the case that used to silently fall through the `if` guard, since there
  is no longer a separate `if` to fall through), the function returns
  `{"ok": False, ...}` exactly as it already did for the previously-fixed
  crash case, without touching `state`. Docstring extended with a new
  paragraph documenting this second race and its fix, alongside the
  existing paragraph documenting the first (crash) fix.
- **`tests/test_team_routes.py`**
  (`TeamResolveEndpointTests::test_loser_whose_exists_check_lands_after_winner_already_renamed_does_not_report_ok`):
  this is the reviewer's own deterministic (hook-based, not
  thread-timing-dependent) repro for the race above, added during the
  reviewer's testing pass and deliberately asserting the BUGGY behavior at
  the time so it stayed green as a documented repro pending the fix.
  Inverted its final two assertions to prove the FIXED behavior instead:
  `loser_result["ok"]` is now asserted `False` (was `True`), and the
  final persisted state's sole `ask_user_resolved` history entry is now
  asserted to be the WINNER's own answer (`"winner answer"`, was asserting
  the loser's `"loser answer"` had clobbered it). The in-line comment block
  above the assertions, and the docstring-style comment at the top of the
  test describing the repro mechanism, were updated to describe the fix
  and mark this test as the permanent regression guard for it, rather than
  a live bug report. The hook mechanism itself (patching `_load_state()`
  so a real winning `resolve_ask_user()` call runs to completion between
  the loser's own state read and its subsequent move/persist step) is
  unchanged.

## Key decisions / tradeoffs

- **Deleted the `os.path.exists()` guard rather than re-checking `state`
  against a freshly-reloaded value before acting.** The reviewer's finding
  offered both as acceptable shapes ("your call, as long as the two
  independent check-then-act windows collapse into one atomic decision
  point"). Deleting the guard was chosen because it is the smaller,
  more local change -- `os.replace()` was already present and already the
  actual arbiter for the first (crash) race; making it the arbiter for
  this second race too needed no new read, no new comparison, and no new
  failure mode to reason about, just removing a redundant, racy check in
  front of an operation that is already atomic on its own. A fresh
  `_load_state()` re-check immediately before acting would have
  reintroduced its own (much narrower, but structurally identical)
  check-then-act gap between that reload and the subsequent
  `os.replace()` call -- strictly worse for "one atomic decision point,"
  not better.
- **`state["status"] = "running"; _persist(state)` moved to after the
  `try/except` block, not left inside it.** Previously both statements ran
  unconditionally inside the `try` (reachable via the `if` guard's `False`
  branch, which is exactly how the bug manifested); now they only run on
  the success path, after `os.replace()` has already returned without
  raising -- so there is no code path left where `state` is mutated or
  persisted without a corresponding successful rename immediately
  preceding it.
- **No change to the shape of the returned dict, the error-message text,
  or the CLI's own observable behavior.** `_cli_team_resolve()` calls this
  same function and is unaffected -- its own regression test
  (`tests.test_teams_lead.ResolveInSeparateProcessTests`) is a single,
  non-concurrent caller and never exercises either race, so this fix
  changes nothing it can observe.

## Deviations from spec / design

None beyond what the 6f part 1 cycle above already discloses under its own
"Deviations from spec" (the `try/except OSError` itself being a
narrow, undisclosed-in-the-original-pseudocode addition, already flagged
there). This fix closes a second window of that same already-disclosed,
already-accepted "the first to persist wins, not lock-guarded" race --
it does not change who wins, does not add new locking, and does not
introduce any behavior the spec's own "Edge cases" section doesn't already
describe (the loser now actually gets the ordinary 400 that section says
it always intended for it, in every timing, not just the ones the first
fix round already covered).

## Known limitations

Same as the 6f part 1 cycle above -- no new limitations introduced by this
fix. The exact reason string a losing concurrent `POST /team/resolve` call
receives is still timing-dependent (the same three legitimate strings);
this fix changes which of those three the previously-unclosed timing
window now produces (the `os.replace()`-raised "is not blocked on
ask_user" branch, same as the already-covered crash case), not whether the
loser gets a clean, shaped result at all.

## Verification status

| Check | Command | Result |
|---|---|---|
| Fixed function's own test class alone | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_team_routes.py::TeamResolveEndpointTests -q`, 5 consecutive runs | **10 passed** every run |
| The reviewer's repro, inverted assertions | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_team_routes.py::TeamResolveEndpointTests::test_loser_whose_exists_check_lands_after_winner_already_renamed_does_not_report_ok -q` | 1 passed -- confirms `loser_result["ok"] is False` and the winner's `ask_user_resolved` history entry survives |
| Genuine two-thread concurrent test, isolated re-runs | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/test_team_routes.py::TeamResolveEndpointTests::test_two_concurrent_resolves_exactly_one_succeeds -q`, 20 consecutive runs | **20/20 passed**, no regression from collapsing the two check-then-act windows into one |
| Full Python suite, before this fix round | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` | 763 passed, 1 failed (`RealTmuxHeadlessTests::test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask` -- pre-existing, disclosed flake in `tests/test_teams_headless.py`, a file untouched by this fix round's own diff; confirmed to pass in isolation immediately after) |
| Full Python suite, re-run after this fix round | `/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q` | **764 passed**, 0 failures/errors (764 = the reviewer's own pre-fix baseline count, unchanged -- this round edits existing code and one existing test's assertions, it adds no new test) |

## How to verify locally

```bash
# The fix-scoped regression tests
/home/dev/.local/bin/uv run --with pytest python -m pytest \
  tests/test_team_routes.py::TeamResolveEndpointTests -q

# The reviewer's own repro, now proving the FIXED behavior
/home/dev/.local/bin/uv run --with pytest python -m pytest \
  tests/test_team_routes.py::TeamResolveEndpointTests::test_loser_whose_exists_check_lands_after_winner_already_renamed_does_not_report_ok -v

# The genuine two-thread concurrent race, repeated
for i in $(seq 1 20); do \
  /home/dev/.local/bin/uv run --with pytest python -m pytest \
    tests/test_team_routes.py::TeamResolveEndpointTests::test_two_concurrent_resolves_exactly_one_succeeds -q; \
done

# Full suite
/home/dev/.local/bin/uv run --with pytest python -m pytest tests/ -q
```
