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
