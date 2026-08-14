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
