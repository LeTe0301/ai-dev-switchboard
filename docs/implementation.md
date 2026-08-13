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
