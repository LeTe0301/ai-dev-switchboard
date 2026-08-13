# Spec: Roster + lead loop, all three adapter tiers (sub-spec 6c)

## Summary

`app/teams.py` gains a **roster** (every headless-eligible `engines.d` entry
plus one configured Ollama model, each tagged with a lead-adapter tier), all
**three lead adapters** from `docs/story.md` §4.2, and the **four-tool lead
loop** (`delegate` / `fact_check` / `ask_user` / `finish`) from §4.4 — driven
entirely from a new CLI surface (`python3 app/teams.py roster|team-start|
team-resolve|team-resume|team-status`), no web route, no UI. This is the
piece that turns 6a's `agent_run()` and 6b/6b.1's grounding into an actual
team: something that plans, delegates, verifies, and knows when to stop or
ask a human.

## Goals

- A `roster()` function listing every teammate-eligible engine (from
  `load_engines()`) plus the configured Ollama model, each tagged with its
  lead-adapter tier (1/2/3), auto-detected with an explicit override escape
  hatch (`HEADLESS_LEAD_FORMAT`).
- All three lead adapters actually work, proven against real transports
  where a real transport exists (tier 1: the real remote Ollama from the
  spike; tier 2: a real `claude`/`codex` login, same as 6a's own
  verification standard) and against an honest stand-in where it doesn't
  (tier 3 / aider — not installed here, same disclosed-limitation pattern
  6a already set for `aider.engine`).
- The four-tool loop runs end to end: `delegate` (calling 6a's
  `agent_run()`), `fact_check` (calling 6b/6b.1's `fact_check()`),
  `ask_user` (blocking, writing a structured `inbox.json`, resumable by
  hand), `finish` (ending the run with a summary).
- **No crash from anything the lead's own output can shape.** The lead's
  output is untrusted model output — the same discipline `agent_run()`
  already applies to an engine's stdout stream applies here to the lead's
  tool calls: malformed input degrades to a defined, bounded outcome, never
  an exception, never a silent hang.
- **A delegation's result re-entering the lead's context is explicitly
  bounded**, by construction, not by convention — see "Context bounding"
  below.
- **The `fact_check` recall problem is mitigated at the prompt level**, per
  6b.1's own closing recommendation (`3e79cb0`) — the lead is instructed to
  quote exact phrases, and told explicitly that `found: false` means
  *unverified*, not *false*.
- The lead's own conversation is durably persisted so a crashed/restarted
  process can resume a blocked or in-progress team from disk, not memory.

## Non-goals

- **tmux team sessions, per-teammate git worktrees, `install.sh
  --with-ollama`** — all 6d. 6c calls `agent_run()` directly against the
  project's own working directory; there is no per-teammate worktree yet,
  so two teammates delegated to concurrently can still collide on the same
  files. That is 6d's problem to solve (worktrees), not silently patched
  over here.
- **The roster/composition UI** (picking a team, saving it, showing tiers
  in a settings screen) — 6e. `roster()` is a plain function/CLI command;
  nothing here renders in the browser.
- **The overwatch feed and escalation inbox UI** — 6f. `inbox.json` is
  written in the exact §4.5 shape so 6f can render it later, but nothing
  here serves it over HTTP or polls it from a browser.
- **Any change to `app/app.py`.** Per the coordinator's brief, `app.py`
  should not grow for this cycle. The one piece of engine-definition
  parsing this spec needs (two new optional `Engine` fields) is additive,
  in the same file/pattern 6a already established there — see "Engine-file
  extension" below — everything else lives in `app/teams.py`.
- **TOTP/auth gating.** This is a local CLI tool, the same trust boundary
  `run`/`grounding`/`fact-check` already have. Auth is 6f's job once this
  is wired into the web UI.
- **`HEADLESS_ROLE_FLAG`.** Considered and explicitly declined this round —
  see "Deviation: no `HEADLESS_ROLE_FLAG` this round" below.
- **Cross-round engine session continuity for the *lead*.** The lead never
  uses `--resume` between rounds, on any tier — see "Why the lead never
  resumes its own session" below. (Delegation *to a teammate* does still
  resume, when the same teammate is delegated to twice in one run — that's
  unrelated and unchanged from 6a.)
- Locking/collision prevention between two teams started against the same
  project directory at once. State files don't corrupt (each run gets its
  own `run_id`-scoped directory), but nothing stops two teams' teammates
  from editing the same files — inherited from the no-worktrees-yet state
  this whole cycle is in, not a regression.
- Rejecting an empty `--members` list with a dedicated check. See "Edge
  cases" — it's handled for free by the same mechanism that rejects
  `delegate` to a non-member, and the UI-level hard rejection with "a clear
  reason" is explicitly 6e's own acceptance criterion, not 6c's.

## Background / current state

`app/teams.py` (1538 lines before this cycle) already has, from 6a/6b/6b.1:

- `agent_run(engine, workdir, prompt, *, session_id=None, timeout=..., log_path=None)`
  at `:669` — spawns one bounded, non-interactive turn of a named
  `engines.d` engine inside a throwaway tmux session, translates its native
  stream into the normalized envelope (`docs/story.md` §4.1), and returns
  `{ok, text, session_id, exit_code, cancelled, cancel_reason, event_count,
  truncated, log_path, stderr_tail, error}`. Never raises after the tmux
  session exists; raises `ValueError` only for validation failures caught
  *before* anything is spawned.
- `load_grounding(workdir)` (`:1206`), `build_digest(files, max_bytes)`
  (`:1242`), `fact_check(claim, grounding, *, max_matches=5)` (`:1392`) —
  auto-discovery + a hard-capped digest + a precision-biased,
  low-recall-and-permanently-so block matcher (`3e79cb0`'s own commit
  message: *"this did NOT deliver the recall improvement it was created
  for... 6c will handle recall at the prompt level instead, by instructing
  the lead to make short claims quoting exact phrases"* — this spec is
  where that promise is kept).
- `Engine` (`app/app.py:291`) / `_parse_engine_file()` (`app/app.py:319`) —
  `__slots__ = (..., "headless_cmd", "headless_format", "headless_prompt",
  "headless_resume")`, all additive, `headless_enabled` derived from the
  first three being present and recognized.
- CLI dispatch (`_parse_args()`/`main()` at `:1490`/`:1522`) with `run`,
  `list-engines`, `grounding`, `fact-check` subcommands, each a thin
  wrapper that never lets a `ValueError` escape as a traceback.
- `docs/ADDING_AN_ENGINE.md` already explicitly reserves
  `HEADLESS_ROLE_FLAG`, `HEADLESS_SCHEMA_FLAG`, `HEADLESS_LEAD_FORMAT` "for
  a later sub-spec (6c — choosing and constraining a *lead*)... not yet
  parsed or consumed by anything in this codebase." This spec is that
  sub-spec, for two of the three (see the role-flag deviation below).
- `_build_headless_argv()`'s own docstring (`app/teams.py:162`) already
  anticipates this cycle by name: *"{resume}... and {prompt_file} are
  substituted with plain str.replace() -- never str.format() -- so a
  future HEADLESS_SCHEMA_FLAG carrying a literal JSON Schema (full of
  {/}) can't break this (6c; docs/spec.md §1)."*
- `_summarize_project()` (`app/app.py:461`) is the existing, working
  precedent for an OpenAI-compatible `/v1/chat/completions` call over
  stdlib `urllib`, including the exact error-swallowing shape
  (`URLError`/`HTTPError`/`ValueError`/`KeyError`/`IndexError` all
  collapse to one safe outcome). The tier-1 adapter is this same shape
  plus a `tools` array — no new dependency.
- `scripts/spike-lead-toolcalling.py` (124 lines, run before this spec —
  `docs/story.md` §2.5) is the **proven** tier-1 call shape and the
  **proven** four-tool schema (`delegate`/`fact_check`/`ask_user`/
  `finish`), measured at 9/10 well-formed correct calls against a real
  `qwen3:8b` over a real remote Ollama, 7.4s mean / 20.8s max latency, zero
  prose fallbacks, zero malformed args. This spec reuses that schema and
  that call shape verbatim rather than inventing a new one; the one
  deliberate change is that `delegate.agent`'s `enum` is now built from the
  *actual team's* `--members` list at run time, not hardcoded to
  `["claude", "codex", "aider"]` the way the spike's throwaway harness was.

## Proposed approach

### 1. Config — `TEAM_LLM_*`, deliberately separate from `DESC_LLM_*`

```python
TEAM_LLM_BASE_URL = os.environ.get("TEAM_LLM_BASE_URL") or None
TEAM_LLM_MODEL = os.environ.get("TEAM_LLM_MODEL", "")
TEAM_LLM_TIMEOUT_SECONDS = float(os.environ.get("TEAM_LLM_TIMEOUT_SECONDS", "120"))
TEAM_LLM_TRANSPORT_RETRY_BUDGET = int(os.environ.get("TEAM_LLM_TRANSPORT_RETRY_BUDGET", "2"))
TEAM_MAX_ROUNDS = int(os.environ.get("TEAM_MAX_ROUNDS", "8"))
TEAM_LEAD_MALFORMED_RETRY_BUDGET = int(os.environ.get("TEAM_LEAD_MALFORMED_RETRY_BUDGET", "2"))
TEAM_DELEGATE_RESULT_MAX_CHARS = int(os.environ.get("TEAM_DELEGATE_RESULT_MAX_CHARS", "4000"))
TEAM_LEAD_PROMPT_MAX_CHARS = int(os.environ.get("TEAM_LEAD_PROMPT_MAX_CHARS", "20000"))
```

Same declare-once-at-module-level convention as the existing `TEAM_HEADLESS_*`/
`TEAM_GROUNDING_MAX_BYTES` block right above where this section is inserted
(`app/teams.py:54-68`).

Deliberately a **sibling of, not shared with, `DESC_LLM_*`**
(`app/app.py:111-112`): a model good at a one-line project description is
not necessarily a good tool-caller, and pointing both at the same env vars
would silently couple two independent tuning decisions. `TEAM_LLM_BASE_URL`
unset means the roster simply has no tier-1 (Ollama) member — an
`engines.d`-based lead (tier 2/3) still works with zero Ollama config at
all, matching the spike's own finding that no tool-capable model can run
*on* the switchboard container itself (`docs/story.md` §2.5) — the whole
feature cannot be gated on it being set.

**What each constant measures, and its proof obligation** (per the pattern
that broke three times already this story — every magic constant here must
be justified against a real oversize case, not a typical one; see
"Acceptance criteria" and "Test plan" for the actual proofs):

| Constant | Measures | Default reasoning |
|---|---|---|
| `TEAM_LLM_TIMEOUT_SECONDS` | Per-HTTP-call timeout to the tier-1 endpoint | Spike measured mean 7.4s, max 20.8s over 10 calls against an **idle** endpoint. `120` is ~6x the observed max, not the max itself — 10 samples on an idle endpoint is not a load test, and this is deliberately generous rather than tight (a too-tight default turns "the endpoint is a little slow right now" into a spurious transport-retry). |
| `TEAM_LLM_TRANSPORT_RETRY_BUDGET` | Retries for a *transport*-layer failure only (`URLError`/timeout/HTTP 5xx) talking to the tier-1 endpoint — never a model-output-quality problem | `2` (3 attempts total). Distinct from `TEAM_LEAD_MALFORMED_RETRY_BUDGET` on purpose: an unreachable endpoint and a model that returns garbage are different failure classes with different correct responses (surface a clear operational error vs. retry-then-escalate-to-a-human). |
| `TEAM_MAX_ROUNDS` | Hard round ceiling across the *whole* lead loop, all tiers, inherited from `docs/story.md` §3's already-settled default | `8`. Not re-derived here — already the story's settled decision — but still proven against a real pathological case in this cycle's own tests (a stub lead that never calls `finish`), not just inherited on faith. |
| `TEAM_LEAD_MALFORMED_RETRY_BUDGET` | Retries for a lead action that **cannot be turned into a valid tool call at all**, any tier — unknown tool name, missing/wrong-typed required args, unparsable tier-3 JSON | `2` (3 attempts total). One shared budget across all three tiers, not three near-identical ones — see "Shared action validation" below for why unifying is deliberate. |
| `TEAM_DELEGATE_RESULT_MAX_CHARS` | Cap on the **most recent** delegation's raw `agent_run()` `text`, folded verbatim into the *next* round's prompt | `4000` (~1000 tokens). Proven against a synthetic delegation result sized like a real oversize case — a full file dump/diff-shaped payload well past this cap, not a typical short answer. |
| `TEAM_LEAD_PROMPT_MAX_CHARS` | **Final, unconditional** cap on the entire assembled per-round prompt (system framing + grounding digest + round history + most-recent capped result), applied last regardless of whether every smaller budget above it was individually respected | `20000`. Sized with headroom over the sum of its own parts at default config (`TEAM_GROUNDING_MAX_BYTES` 8000 + 8 rounds × ~100-char summary lines + one 4000-char capped result + ~2000 chars of framing/instructions ≈ 15,000), proven by a test that forces *every* sub-budget to its own maximum simultaneously. |

### 2. Roster

```python
def roster() -> list:
    """
    [{name, kind: "engine"|"ollama", label, tier: 1|2|3, delegate_capable}]
    kind="ollama" entries are lead-only (delegate_capable=False -- there is
    no agent_run() path for an Ollama chat-completion model, only for a
    headless-eligible engines.d entry). Re-reads load_engines() live, same
    no-cache philosophy load_engines() itself already documents (engines.d
    is meant to be edited without a restart).
    """
```

- One `kind="ollama"` entry iff both `TEAM_LLM_BASE_URL` and
  `TEAM_LLM_MODEL` are set, `name` = the model string itself
  (`TEAM_LLM_MODEL`), `tier=1`.
- One `kind="engine"` entry per `load_engines()` value with
  `headless_enabled`, `tier` from `_lead_tier_for_engine()` below,
  `delegate_capable=True`.
- New CLI subcommand `roster` prints this as JSON — same shape/spirit as
  the existing `list-engines`.

### 3. Engine-file extension — two new keys, additive only

```
HEADLESS_SCHEMA_FLAG=--json-schema {schema}   # optional; presence => tier 2
HEADLESS_LEAD_FORMAT=schema                   # optional override: schema | prose
```

`Engine.__slots__` gains `headless_lead_format`, `headless_schema_flag`
(both `None`-default, both parsed in `_parse_engine_file()` exactly the way
the four `HEADLESS_*` fields already are — read, default `None` if absent,
no validation beyond "is it a non-empty string" since, unlike
`HEADLESS_FORMAT`/`HEADLESS_PROMPT`, there's no fixed enum to check against
for a flag template). This is the one `app/app.py` change in this whole
sub-spec — a few lines, same file, same pattern, not a new architectural
layer.

`HEADLESS_CMD` gains a third optional placeholder, `{schema}`, substituted
by `_build_headless_argv()` with `engine.headless_schema_flag.replace(
"{schema}", schema_path)` when a schema is supplied, or with `""` when it
isn't — same `str.replace()`-never-`str.format()` discipline the function's
own docstring already commits to, and the same empty-string-by-default
pattern `{resume}` already uses. `schema_path` is a file
`agent_run()`/its lead-mode caller writes under the run's own `rundir`
(mirrors `{prompt_file}`'s existing handling exactly: written by `SVC_USER`,
`chmod 0o644` so `RUN_USER`'s tmux pane can read it, thrown away in
`agent_run()`'s existing `finally: shutil.rmtree(rundir, ...)`).

`claude.engine` and `codex.engine` gain:

```
HEADLESS_CMD=claude -p {resume} {schema} --output-format stream-json --verbose
HEADLESS_SCHEMA_FLAG=--json-schema {schema}
```
```
HEADLESS_CMD=codex exec {resume} {schema} --json --skip-git-repo-check
HEADLESS_SCHEMA_FLAG=--output-schema {schema}
```

(Per `docs/story.md` §2.1's own table: Claude Code's `--json-schema`
"constrains the result to a JSON Schema (lands in `structured_output`)";
Codex's `--output-schema ./schema.json` "enforces a response shape".)
`aider.engine` is **unchanged** — no schema flag exists for it, so it stays
tier 3 by the auto-detection default with zero new keys, matching "an
engine without them is teammate-ineligible and simply doesn't appear" —
here, "doesn't appear at tier 2".

**Tier auto-detection**, `_lead_tier_for_engine(e)`:
```python
if e.headless_lead_format == "schema": return 2
if e.headless_lead_format == "prose": return 3
if e.headless_schema_flag: return 2
return 3
```
Matches `docs/story.md` §4.2's literal wording: "`HEADLESS_LEAD_FORMAT=
schema|prose` in an engine definition overrides the auto-detected tier
when an engine gains or loses a structured-output flag."

`docs/ADDING_AN_ENGINE.md`'s "reserved, not yet consumed" note is updated
for `HEADLESS_SCHEMA_FLAG`/`HEADLESS_LEAD_FORMAT` (now documented, same
section structure as the existing `HEADLESS_*` write-up) — `HEADLESS_ROLE_FLAG`
stays reserved; see the deviation note below for why.

### Deviation: no `HEADLESS_ROLE_FLAG` this round

`docs/story.md` §4.2 lists `HEADLESS_ROLE_FLAG=--append-system-prompt
{role}` alongside the schema flag as part of "the engine-definition
extension." This spec deliberately does **not** implement it, and folds
the lead's entire system framing (grounding digest, tool descriptions, the
`fact_check` precision instructions) into the **prompt text itself** for
every tier, including tier 2 — reported here as a deviation, not silently
dropped.

Reasoning: `--append-system-prompt`-equivalent flags exist for Claude Code
but the story's own §2.1 engine survey does not document an equivalent for
Codex, and every tier already needs a working "put instructions in front of
the model" path anyway (tier 1's system message, tier 3's plain prompt) —
adding a *fourth*, engine-specific, partially-available channel for the
identical content is complexity without a corresponding reliability win
this cycle, and it would make tier 2's prompt assembly diverge from tier 1
and 3's in a way nothing here actually needs. `HEADLESS_ROLE_FLAG` stays
genuinely reserved for whichever later cycle has an actual reason to prefer
a native system-prompt channel over prompt-text framing (e.g. wanting the
framing excluded from the engine's own context-window accounting, which
`--append-system-prompt` may do differently than a plain prompt prefix —
untested, not this cycle's problem).

### 4. The four-tool schema

Reused verbatim in shape from `scripts/spike-lead-toolcalling.py`, with one
change: `delegate.agent`'s `enum` is built from the running team's actual
`--members` list, not hardcoded.

```python
def _lead_tools(team_members: list) -> list:
    return [
        {"type": "function", "function": {
            "name": "delegate",
            "description": "Give one self-contained task to a named teammate agent.",
            "parameters": {"type": "object", "properties": {
                "agent": {"type": "string", "enum": list(team_members)},
                "task": {"type": "string", "description": "The full self-contained task."}},
                "required": ["agent", "task"]}}},
        {"type": "function", "function": {
            "name": "fact_check",
            "description": "Verify a claim against the project's own documentation.",
            "parameters": {"type": "object", "properties": {
                "claim": {"type": "string"}}, "required": ["claim"]}}},
        {"type": "function", "function": {
            "name": "ask_user",
            "description": "Ask the human a question when something is genuinely unresolved.",
            "parameters": {"type": "object", "properties": {
                "question": {"type": "string"},
                "header": {"type": "string"},
                "options": {"type": "array", "items": {"type": "object", "properties": {
                    "label": {"type": "string"}, "description": {"type": "string"}}}},
                "multi_select": {"type": "boolean"}},
                "required": ["question", "options"]}}},
        {"type": "function", "function": {
            "name": "finish",
            "description": "Conclude the task with a summary.",
            "parameters": {"type": "object", "properties": {
                "summary": {"type": "string"}}, "required": ["summary"]}}},
    ]
```

Tier 1 passes this directly as the `tools` array. Tier 2's schema file
wraps it as one object: `{"type": "object", "properties": {"tool":
{"type": "string", "enum": [...four names...]}, "args": {"type":
"object"}}, "required": ["tool", "args"]}` (a JSON Schema constrains
*shape*, not a discriminated-union-of-four-shapes the way a native `tools`
array does — the shared validator in §9 below is what actually checks
`args` matches whichever `tool` was named). Tier 3's prompt spells out the
same four tools and the same required-fenced-`json`-block shape in prose.

### 5. Prompt assembly and context bounding

One function builds the two pieces every tier needs, called **fresh every
round** (not cached — matches `load_engines()`'s/`load_grounding()`'s own
"always re-read" philosophy, and means a project's docs edited mid-run are
picked up, and a crash-recovered run rebuilds the identical prompt a live
run would have):

```python
def _system_framing(team_members: list, tier: int) -> str: ...
def _round_context(task, history, last_result, round_n, max_rounds) -> str: ...
```

**`_system_framing()`** — role framing, the grounding digest
(`load_grounding(workdir)["digest"]`, already capped by 6b/6b.1's own
`TEAM_GROUNDING_MAX_BYTES`), and, tier 2/3 only, prose tool descriptions
(tier 1 gets these for free from the native `tools` array and doesn't need
them restated). **Always** includes, verbatim, the two required
`fact_check`-precision-mitigation clauses (see "Prompt-level recall
mitigation" below) regardless of tier.

**`_round_context()`** — the part that grows, and the part that's bounded:

```
Task: <the run's original task text, unbounded -- given once by the
       operator at team-start, never re-truncated>

Round history:
  round 1: delegate(agent=claude) -> ok, 214 chars (see log)
  round 2: fact_check("...") -> found=False (unverified)
  ...                                          <- one line per PRIOR round,
                                                   compact, never full text
Most recent result (round <n-1>, <tool>):
<the most recent tool's raw result text, hard-capped at
 TEAM_DELEGATE_RESULT_MAX_CHARS, with a "...[truncated, N more chars,
 full text in <log_path>]" suffix appended iff truncation actually
 happened -- never a bare, silent cut>

Round <n> of <TEAM_MAX_ROUNDS>. What do you do next?
```

Then the **whole assembled prompt** (`_system_framing()` +
`_round_context()`, joined) is passed through one final, unconditional
`text.encode("utf-8")[:TEAM_LEAD_PROMPT_MAX_CHARS].decode("utf-8",
errors="ignore")` — the exact same "the heuristic above it doesn't have to
be right, because the final step always runs" pattern `build_digest()`
already uses (`app/teams.py:1242`), reused here rather than a new
mechanism invented for the same problem.

Per-round bookkeeping needed to build the above: a small in-memory (and
persisted, see §11) list of `{round, tool, args_summary, outcome_summary,
full_result_text, log_path}` dicts — `outcome_summary` and
`args_summary` are what the one-line history entries render from;
`full_result_text` (only ever the *previous* round's) is what
`TEAM_DELEGATE_RESULT_MAX_CHARS` bounds.

### Why the lead never resumes its own session

Tier 1 (`/v1/chat/completions`) has no session concept at all — every call
is `messages = [system, user]`, exactly two elements, regardless of round
count; all growth funnels through the one bounded user message described
above. For consistency (and so all three tiers share one prompt-assembly
function, one bounding strategy, and one crash-recovery story), tiers 2/3
are deliberately **not** given `session_id`/`--resume` continuity for their
own lead calls either — each round's `agent_run()`-equivalent call is a
fresh process, self-contained, built from the persisted transcript, not
from an engine's own in-process memory of earlier turns. This is a
considered simplification, not an oversight: it means the switchboard is
the single owner of the lead's state on every tier (not "sometimes an
engine's own session, sometimes our own bookkeeping"), and it is what
makes crash recovery in §11 essentially free rather than tier-dependent.

(Delegation *to a teammate* is unaffected and unchanged: a second
`delegate` call to the same agent within one run still passes that
engine's own cached `session_id` to `agent_run()`, when the engine supports
resume — see §12.)

### 6. Tier 1 adapter (Ollama, `/v1/chat/completions`)

```python
def _lead_tier1_call(base_url, model, system, user, tools, *, timeout) -> dict:
    """Same urllib shape as app.py's _summarize_project(), plus `tools`.
    Raises on transport failure (caught by the retry wrapper in §9), never
    on a well-formed-but-wrong-shaped response body -- that's the parser's
    job below, not this function's."""
```

Body: `{"model": model, "messages": [{"role": "system", "content": system},
{"role": "user", "content": user}], "tools": tools, "stream": False,
"temperature": 0}` — identical to the spike's own `call()`, deliberately
not adding `tool_choice` or any other parameter the spike didn't actually
measure with.

Parsing (`_parse_tier1_action(payload)`): reads
`payload["choices"][0]["message"]`. `tool_calls` empty/absent → **prose
fallback** — attempt the tier-3 fenced-`json`-block parser (§8) against
`message.get("content")` before giving up (this is the acceptance
criterion "a tier-1 model that ignores the tools array... falls back to
tier-3 parsing rather than silently doing nothing" — implemented as one
literal function call, not a separate code path). `tool_calls` non-empty →
only `tool_calls[0]` is honored; any further entries are logged (`kind:
"status"`, text noting how many were discarded) but never acted on —
matches the spike's own `classify()`, which only ever inspects
`calls[0]`. `arguments` that fails `json.loads()`, or that parses to
anything other than a `dict`, is a malformed action (§9).

### 7. Tier 2 adapter (schema-constrained)

`agent_run()` gains one new optional keyword-only parameter,
`schema: dict = None`, default `None` — existing callers (the `run` CLI
subcommand, every 6a test) are byte-for-byte unaffected. When given:

- Raises `ValueError` **before spawning anything** if `eng.headless_schema_flag`
  is unset (same before-anything-is-spawned discipline `_resume_fragment()`
  already has for an unsupported `session_id`) — an engine can't be used as
  a tier-2 lead if it never declared a schema flag.
- Writes `json.dumps(schema)` to `rundir/schema.json`, `chmod 0o644`,
  exactly parallel to the existing prompt-file handling.
- `_build_headless_argv()` substitutes `{schema}` as described in §3.

Parsing: `json.loads(result["text"])`, expect `{"tool": ..., "args":
...}`. A `json.loads()` failure, or a result that isn't a `dict` with both
keys, is a malformed action (§9) — schema-constrained output is high
reliability, not zero-risk (a truncated/cancelled run, or an engine CLI
version that doesn't actually enforce the schema despite accepting the
flag, are both real possibilities and must degrade the same way tier 3's
failures do, not raise).

### 8. Tier 3 adapter (prose-parse)

No new engine-file keys needed (see `aider.engine`, unchanged). The prompt
(`_system_framing()` for tier 3) instructs the lead to reply with **exactly
one** fenced ```` ```json ```` block shaped `{"tool": ..., "args": ...}`,
nothing else outside the fence.

```python
_TIER3_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)

def _parse_tier3_action(text: str) -> dict | None:
    """First fenced block that parses as a dict with 'tool'+'args' keys;
    None (never an exception) if no fence is found, more than one
    candidate is ambiguous-but-only-the-first-is-tried (mirrors tier 1's
    calls[0]-only rule), or nothing inside any fence parses as JSON."""
```

A `None` result is a malformed action (§9) — same shared retry-then-escalate
path tier 1/2's malformed cases use. This is also the exact function
tier 1's prose-fallback path calls (§6) — one parser, two callers.

**Testing tier 3 without `aider` installed.** A tiny stand-in test-only
engine (a shell script fixture under `tests/fixtures/headless/`, wired
through a scratch `.engine` file the test suite constructs in a temp
`ENGINES_DIR` — same technique 6a's own Tier-2 tests already use for a
"real tmux, real test-authored helper process, no real engine CLI"
verification) that echoes a **canned prose response** given a prompt
containing a marker string: one fixture emitting a well-formed fenced
block, one emitting prose with no fence at all, one emitting a
malformed/truncated fence. Exercises the retry-then-escalate budget for
real, end to end, without needing `aider` itself. `aider.engine`'s own
tier-3 status is labeled the same honest way 6a labeled its `HEADLESS_*`
keys: **UNVERIFIED against the real `aider` CLI** (not installed in this
environment), verified only via the stand-in above — recorded in
`docs/implementation.md`, not glossed over.

### 9. Shared action validation — one pipeline, three "get me a dict" adapters

```python
def _validate_lead_action(raw: dict, team_members: list, action_count: int) -> dict:
    """
    Returns {"ok": True, "tool": ..., "args": ...} or
    {"ok": False, "reason": "<category>", "detail": "..."}.
    Never raises -- raw is untrusted model output, the least trustworthy
    input in this system, same standing as agent_run()'s own untrusted
    engine-stdout input.

    Categories (docs/spec.md "Shape robustness"):
      unknown_tool        -- raw["tool"] not one of the four names
      missing_args         -- a tool-required key absent from raw["args"]
      wrong_type            -- an arg present but the wrong JSON type
                              (e.g. ask_user.options not a list)
      not_a_dict            -- raw itself, or raw["args"], isn't an object
      agent_not_on_team    -- delegate.agent not in team_members (valid
                              shape, invalid *value* -- NOT a malformed
                              action, see below)
      premature_finish     -- finish with action_count == 0 (valid shape,
                              rejected on a business rule -- NOT malformed)
    """
```

**Two different outcome families, and they are handled differently on
purpose** (lesson from this story's own "precision over eagerness"):

1. **Malformed shape** (`unknown_tool`, `missing_args`, `wrong_type`,
   `not_a_dict`, plus a tier-1/3 parse failure that never produced a `raw`
   dict at all) — counts against `TEAM_LEAD_MALFORMED_RETRY_BUDGET`. The
   lead is re-prompted with the specific reason ("your last reply named an
   unknown tool 'X'; valid tools are delegate/fact_check/ask_user/finish")
   appended to that round's context. Budget exhausted → forced `ask_user`
   escalation, question = "the lead's output could not be parsed after
   `TEAM_LEAD_MALFORMED_RETRY_BUDGET + 1` attempts", the raw unparsable
   text included so a human can see what actually happened (never dropped
   — matches §4.5's tier-3-malformed-question rule, generalized to any
   malformed *action*, not just a malformed `ask_user`).
2. **Valid shape, rejected on a business rule** (`agent_not_on_team`,
   `premature_finish`) — **does not** consume the malformed budget. Fed
   back to the lead as an ordinary tool-result-shaped error ("agent
   'gemini' is not on this team. Team members: claude, aider.") and
   consumes one ordinary round, exactly like a `fact_check` miss does —
   this is a judgment error the lead should self-correct from (the spike's
   own single miss was exactly this class: well-formed, wrong choice, not
   a format failure), not a defect that should burn the format-defect
   budget.

`premature_finish`: `action_count` is "how many prior `delegate`/
`fact_check` calls this run has made" — **not** "is this round 1", because
a run resumed after an `ask_user` (§11) can reach round 2+ with
`action_count` still 0 if the very first action was the escalation itself.
`ask_user` and `finish`-after-real-work are both exempt; a bare `finish`
with zero prior grounding/delegation work is the one thing rejected
structurally, per the explicit design directive that a wrong `finish` is
worse than an unnecessary `ask_user`.

### 10. The lead loop

```python
def team_step(state: dict) -> dict:
    """One round. Pure-ish: takes/returns the run's own state dict (see
    §11 for its persisted shape); the only I/O is one lead-adapter call,
    zero or one agent_run()/fact_check() call, and the state write at the
    end. Never raises for anything shaped wrong -- see §9."""

def team_run(state: dict) -> dict:
    """Drives team_step() in a loop until finish / ask_user / TEAM_MAX_ROUNDS.
    This is the function the CLI's team-start/team-resume/team-resolve
    subcommands call; nothing here assumes a foreground TTY, so 6d can
    later run it off a background thread with zero change."""
```

```python
for round in range(1, TEAM_MAX_ROUNDS + 1):
    raw = _tier_call(state)                              # §6/7/8, with the
                                                           # transport-retry
                                                           # wrapper (§1)
    action = _validate_lead_action(raw, state["members"], state["action_count"])
    if not action["ok"]:
        if action["reason"] in ("agent_not_on_team", "premature_finish"):
            _record_and_continue(state, error=action)     # ordinary round
            continue
        if _malformed_retries_exhausted(state):
            _force_ask_user(state, reason="unparsable lead output")
            break
        _record_malformed_retry(state, action)
        continue
    if action["tool"] == "delegate":
        result = agent_run(action["args"]["agent"], state["workdir"],
                           action["args"]["task"],
                           session_id=state["teammate_sessions"].get(action["args"]["agent"]))
        state["teammate_sessions"][action["args"]["agent"]] = result["session_id"]
        _record(state, "delegate", result)
        state["action_count"] += 1
    elif action["tool"] == "fact_check":
        result = fact_check(action["args"]["claim"], load_grounding(state["workdir"]))
        _record(state, "fact_check", result)
        state["action_count"] += 1
    elif action["tool"] == "ask_user":
        _write_inbox(state, action["args"])
        state["status"] = "blocked_ask_user"
        break
    elif action["tool"] == "finish":
        state["status"] = "finished"
        state["summary"] = action["args"]["summary"]
        break
    _persist(state)                                        # every round,
                                                             # not just at end
else:
    _force_ask_user(state, reason=f"did not converge after {TEAM_MAX_ROUNDS} rounds")
```

### 11. Persistence, `ask_user`, and crash recovery

State lives under `TEAM_STATE_DIR/leads/<run_id>/` (`run_id` generated the
same way `agent_run()`'s own `_run_id()` already does — timestamp +
`secrets.token_hex`, directly reused):

- `run.json` — `{run_id, workdir, lead: {kind, name, tier}, members,
  task, status, round, action_count, max_rounds, teammate_sessions,
  history, created_at, updated_at}`. `status` ∈ `running`,
  `blocked_ask_user`, `finished`, `escalated_max_rounds`, `error`.
  Written after **every** round, not just at completion — this is what
  makes crash recovery possible: because the per-round prompt is always
  *rebuilt fresh* from this file (§5), a `team-resume <run_id>` after a
  crash reconstructs the exact prompt a live process would have built next,
  with no dependency on any engine's own session memory.
- `transcript.jsonl` — one envelope per round, same `{ts, agent="lead",
  seq, kind, text, meta}` shape §4.1 already defines (`kind="tool_use"` for
  the lead's own chosen action, `kind="handoff"` specifically for a
  `delegate` call — the one existing `kind` value in the closed set that
  was clearly reserved for exactly this, `kind="tool_result"` for what
  came back, `kind="status"`/`"error"` for round markers and malformed
  attempts) — durable audit trail, unrelated to and unbounded by the
  per-round *prompt* bounding in §5 (bounded implicitly: at most
  `TEAM_MAX_ROUNDS` entries per run).
- `inbox.json` — present only while `status == "blocked_ask_user"`, exact
  §4.5 shape (`question`, `header`, `options` 2-4 each `{label,
  description}`, `multi_select`). `header` longer than 12 chars is
  silently truncated (cosmetic, never worth spending retry budget on);
  `question`/`options` missing or malformed *is* a malformed action (§9).
  Removed (renamed `inbox.resolved.json`, not deleted — keeps the record)
  once answered.

**Mid-delegate crash.** If the process dies while `agent_run()` is
in-flight, the round's own tmux session may still be running or may have
completed independently (tmux sessions are not children of the Python
process). `run.json` marks a round `"in_progress"` *before* the blocking
`agent_run()` call and `"complete"` after. On `team-resume`, a round left
`"in_progress"` is **never** assumed to have succeeded — recorded as an
`error`-kind event ("delegation possibly interrupted by a restart, outcome
unknown") and fed to the lead as an unresolved result, exactly the same
"never optimistically assumed clean" discipline `agent_run()`'s own
`_run_headless_session()` already applies to a vanished tmux session
(`app/teams.py:635-641`).

New CLI subcommands:

```
python3 app/teams.py roster
python3 app/teams.py team-start <workdir> --task "..." \
    (--lead <engine-name> | --lead-ollama) --members a,b,c
python3 app/teams.py team-status <run_id>
python3 app/teams.py team-resolve <run_id> --answer "<label or free text>"
python3 app/teams.py team-resume <run_id>
```

`team-start`/`team-resolve`/`team-resume` all call `team_run()` and block
in the foreground until `finished`/`blocked_ask_user`/
`escalated_max_rounds`/`error`, tailing round-by-round progress to stderr
the same way `_cli_run()` already tails an `agent_run()` log (`:1442`) —
appropriate for a CLI, and `team_run()` itself makes no foreground/TTY
assumption, so 6d can call it from a background thread later with no
change (the spike's "must be asynchronous" finding is about not blocking a
*web request thread*, which doesn't exist yet in 6c's scope).

`--lead <name>` / `--lead-ollama` are mutually exclusive
(`argparse.add_mutually_exclusive_group(required=True)`) rather than one
overloaded `--lead` string — avoids any ambiguity between an Ollama model
name and an `engines.d` filename stem that happen to collide, structurally,
instead of trying to disambiguate one string two ways.

### 12. Delegation session continuity (unchanged behavior, called out for clarity)

A `delegate` call targeting an agent already delegated to earlier in the
*same* run passes that agent's own cached `session_id` (`state
["teammate_sessions"][agent]`) to `agent_run()`, so the teammate's own
context carries across repeated delegations — this is exactly 6a's
existing `session_id`/`--resume` mechanism, used the way it was already
designed to be used; no new code beyond the one dict. For an engine with no
resume concept (`aider`), the cached value stays `None` forever and every
delegation is a fresh, unresumed call — `agent_run()` already handles a
`None` `session_id` as "first turn", so this needs no special-casing.

### 13. Prompt-level `fact_check` recall mitigation

Required verbatim (or materially equivalent — the two obligations, not the
exact prose, are what's load-bearing) in `_system_framing()`, every tier:

> When you use `fact_check`, phrase the claim as a short quotation of exact
> wording from the project's own docs — a distinctive phrase or term copied
> verbatim — rather than paraphrasing the idea in your own words.
> `fact_check` is a literal substring matcher; it has no synonym or fuzzy
> matching, so an exact quotation is far more likely to be found even when
> the underlying claim is true.
>
> If `fact_check` returns `found: false`, treat the claim as **unverified**,
> not **false**. This tool failing to find supporting text does not mean
> the claim is wrong — only that this specific tool could not locate it.
> An unverified claim may still be true. Use your own judgment, a
> teammate's own report, or `ask_user` if something is genuinely
> unresolved — never conclude a claim is false solely because
> `fact_check` returned `found: false`.

## Affected areas

- `app/teams.py` — new sections: config (§1), `roster()` (§2), tier
  adapters (§6/7/8), shared validation (§9), the lead loop (§10),
  persistence/CLI (§11). One new optional keyword parameter on
  `agent_run()` (`schema`, default `None`, existing behavior unchanged).
- `app/app.py` — `Engine.__slots__`/`_parse_engine_file()` gain
  `headless_lead_format`/`headless_schema_flag`, additive, same pattern as
  6a's own four fields. Nothing else in this file changes.
- `engines.d/claude.engine`, `engines.d/codex.engine` — `HEADLESS_CMD`
  gains `{schema}`; `HEADLESS_SCHEMA_FLAG` added. `engines.d/aider.engine`
  — unchanged.
- `config/switchboard.env.example` — new `## Optional: team lead loop (6c)`
  section, `TEAM_LLM_BASE_URL`, `TEAM_LLM_MODEL`, `TEAM_LLM_TIMEOUT_SECONDS`,
  `TEAM_LLM_TRANSPORT_RETRY_BUDGET`, `TEAM_MAX_ROUNDS`,
  `TEAM_LEAD_MALFORMED_RETRY_BUDGET`, `TEAM_DELEGATE_RESULT_MAX_CHARS`,
  `TEAM_LEAD_PROMPT_MAX_CHARS`, same commented-out-with-explanation style as
  the existing `TEAM_HEADLESS_*`/`TEAM_GROUNDING_MAX_BYTES` blocks.
- `docs/ADDING_AN_ENGINE.md` — documents `HEADLESS_SCHEMA_FLAG`/
  `HEADLESS_LEAD_FORMAT` (no longer "reserved"); `HEADLESS_ROLE_FLAG`
  stays reserved, with a one-line pointer to this spec's deviation note.
- New tests: `tests/test_teams_lead.py` (new file, following
  `tests/test_teams_headless.py`'s own three-tier test-file structure and
  header-comment convention) plus new fixture files under
  `tests/fixtures/headless/` for the tier-3 stand-in (§8).
- No `app/app.py` HTTP routes, no templates, no JS — 6c is CLI-only per
  scope.

## Edge cases

- **`delegate` for an agent not on the team** — §9 `agent_not_on_team`,
  valid shape/invalid value, ordinary round, not the malformed budget.
- **`finish` with zero prior grounding/delegation actions** — §9
  `premature_finish`, rejected structurally regardless of round number
  (round-based, not action-count-based, would miss the resumed-run case —
  see §9).
- **Empty `--members`** — no dedicated rejection in 6c (see Non-goals);
  `delegate`'s `enum` is simply empty, so any `delegate` call is
  automatically `agent_not_on_team` — the lead is naturally steered toward
  `fact_check`/`ask_user`/`finish` only, no special-case code needed. The
  UI-level hard rejection "with a clear reason" is 6e's own acceptance
  criterion.
- **Tier-1 model ignores `tools`, replies in prose** — §6, prose fallback
  attempts the tier-3 parser before counting as malformed.
- **Tier-1 model returns multiple `tool_calls` in one response** — only
  `[0]` is honored; the rest logged, never acted on (matches the spike's
  own `classify()`).
- **`arguments`/tier-2 result that's syntactically valid JSON but not an
  object** (e.g. a bare list or string) — `not_a_dict`, malformed, retried.
- **Ollama unreachable / times out / 5xx** — transport-retry budget
  (`TEAM_LLM_TRANSPORT_RETRY_BUDGET`), then a clear, actionable
  `team-start` exit (non-zero, message naming the endpoint and the last
  transport error), never a raw traceback, never routed through
  `ask_user` (a human answering a question can't fix an unreachable LLM
  backend — this is an operational failure, not a content judgment call).
- **A tier-3/malformed-JSON `ask_user`** — retried once inside the shared
  malformed-budget path; if still malformed when the budget is exhausted,
  the raw text is surfaced as the escalation's `question` verbatim (never
  dropped), matching §4.5's literal rule, generalized by §9 to the shared
  path rather than reimplemented specially for `ask_user`.
- **Mid-delegate process crash** — §11, round left `"in_progress"` is never
  assumed successful on resume.
- **Two teams started against the same project directory concurrently** —
  state doesn't corrupt (separate `run_id` directories); nothing prevents
  their teammates from colliding on the same files (inherited, not new —
  see Non-goals; 6d's worktrees are the real fix).
- **`fact_check` genuinely finding nothing for a true claim** (the known,
  permanent 6b.1 limitation) — mitigated, not eliminated, by §13's prompt
  instructions; the lead is expected to still occasionally treat a true
  claim as merely unverified and proceed on other evidence or escalate,
  which is the intended, safer failure mode (precision over eagerness).
- **A delegation result larger than `TEAM_DELEGATE_RESULT_MAX_CHARS`** —
  truncated with an explicit, non-silent marker (§5), never a bare cut.
- **The grounding set changes mid-run** (a file edited while the team is
  active) — picked up on the very next round, since `load_grounding()` is
  called fresh every round, not cached at team-start.

## Acceptance criteria

- [ ] `roster()` lists every headless-eligible `engines.d` entry plus the
      configured Ollama model (when `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL`
      are set), each with the correct tier — verified against real
      `claude.engine`/`codex.engine`/`aider.engine` plus a stubbed Ollama
      config, and against `HEADLESS_LEAD_FORMAT`'s override in both
      directions (forcing an otherwise-tier-2 engine to tier 3 and
      vice versa).
- [ ] A full `delegate → fact_check → delegate → finish` cycle runs from
      the CLI against a real project directory and a real reachable Ollama
      model (tier 1) — the spike's own endpoint/model, or an equivalent
      the developer has access to, documented in `docs/implementation.md`
      with the actual command and output, not asserted from memory.
- [ ] **All three tiers demonstrated leading the same task**: tier 1 (real
      Ollama), tier 2 (`claude` or `codex`, real login, same verification
      bar 6a already set), tier 3 (the stand-in fixture from §8, with
      `aider.engine`'s own real-CLI status labeled UNVERIFIED, matching
      6a's own precedent for the same engine).
- [ ] A tier-3 lead emitting malformed JSON is retried within
      `TEAM_LEAD_MALFORMED_RETRY_BUDGET`, then escalates via `ask_user`
      with the raw unparsable text included — never loops, never silently
      stalls, proven by a stub that always returns malformed output.
- [ ] `ask_user` blocks the loop and writes `inbox.json` in the exact §4.5
      shape (`question`, `header` ≤ 12 chars, 2-4 `options` each with
      `label`+`description`, `multi_select`); `team-resolve` answers it and
      the loop resumes from the persisted state, not from memory (proven
      by resolving in a **separate process invocation** from the one that
      blocked).
- [ ] `TEAM_MAX_ROUNDS` forces `ask_user` escalation rather than looping
      forever, proven by a stub lead that always calls `delegate`/
      `fact_check` and never `finish` — exactly `TEAM_MAX_ROUNDS` rounds
      run, not more, not fewer.
- [ ] Ollama unreachable produces a clear, actionable, non-traceback error
      after exhausting `TEAM_LLM_TRANSPORT_RETRY_BUDGET` — proven against a
      stub `urlopen` that always raises `URLError`.
- [ ] A tier-1 model that returns no `tool_calls` (prose reply) is detected
      and the prose is run through the tier-3 parser before being treated
      as malformed — proven both for a prose reply that *does* happen to
      contain a valid fenced block (recovered, not escalated) and one that
      doesn't (correctly falls through to the malformed path).
- [ ] `delegate` to an agent not in `--members` is rejected without
      consuming the malformed-retry budget, fed back as an ordinary
      tool-result-shaped error, and the loop continues.
- [ ] `finish` with zero prior `delegate`/`fact_check` calls in the run is
      rejected the same way, including in a run **resumed** after an
      `ask_user` where the round number is already > 1 but
      `action_count` is still 0.
- [ ] A delegation result larger than `TEAM_DELEGATE_RESULT_MAX_CHARS` is
      truncated with the explicit non-silent marker, proven against a
      synthetic oversize (file-dump-shaped) result, not a typical one.
- [ ] The fully-assembled per-round prompt never exceeds
      `TEAM_LEAD_PROMPT_MAX_CHARS`, proven against a pathological case that
      maxes out grounding digest size, round-history length (a full
      `TEAM_MAX_ROUNDS`-round history), and the most-recent-result cap
      simultaneously.
- [ ] `_system_framing()`'s output contains both required `fact_check`
      mitigation clauses (quote-exact-phrases; `found: false` means
      unverified, not false), for every tier, asserted on the literal
      rendered text.
- [ ] A crashed/killed `team-start` process, restarted via `team-resume
      <run_id>` in a fresh process, reconstructs the identical next-round
      prompt a non-crashed run would have built, and continues to
      completion — proven end to end, not just at the state-file level.
- [ ] A round left `"in_progress"` by a mid-delegate crash is never treated
      as successful on resume — surfaced as an unresolved result to the
      lead, proven against a `run.json` hand-constructed in that state.
- [ ] `agent_run()`'s existing behavior (no `schema` argument passed) is
      byte-for-byte unchanged — the full `tests/test_teams_headless.py`
      suite still passes with zero modifications.
- [ ] Full test suite green, several consecutive runs. `app/app.py`'s diff
      is limited to the two new `Engine` fields plus their parsing — no
      other line in that file changes.

## Test plan

Mirrors `tests/test_teams_headless.py`'s own three-tier structure
(explicitly reused, not reinvented — see that file's own header comment):

**Tier 1 (bulk of the new file) — pure unit, no subprocess, no tmux, no
network:** `_lead_tier_for_engine()` against every combination of
`headless_schema_flag`/`headless_lead_format`; `_lead_tools()`'s enum
reflecting `--members`; `_validate_lead_action()` against every category in
§9 (unknown tool, missing arg, wrong type, not-a-dict, agent-not-on-team,
premature-finish, and the resumed-run `action_count==0`-at-round>1 case
specifically); `_parse_tier1_action()`/`_parse_tier3_action()` against
well-formed, malformed, multiple-tool-calls, non-dict-arguments, and
no-fence-found fixtures; `_round_context()`/the final
`TEAM_LEAD_PROMPT_MAX_CHARS` truncation against the pathological
max-every-sub-budget-at-once case; the transport-retry wrapper against a
monkeypatched `urlopen` that raises N times then succeeds, and one that
always raises; `_write_inbox()`'s exact shape and `header` truncation;
`_persist()`/crash-recovery reconstruction (hand-construct an
`"in_progress"` `run.json`, assert `team-resume`'s next prompt matches).

**Tier 2 — real tmux, test-authored helper processes, no real engine CLI,
no sudo:** the `{schema}` substitution into a real (test-fixture) `.engine`
file's `HEADLESS_CMD`, `agent_run(..., schema=...)`'s `rundir/schema.json`
write/permissions/cleanup, and the tier-3 stand-in fixtures from §8 driving
a full `team_run()` end to end (malformed → retry → escalate; well-formed →
finish), same `TMUX` monkeypatch technique
(`tests/test_teams_headless.py`'s `_patch_tmux()`) already established.

**Tier 3 (manual, developer stage, not in the automated file) —** the real
tier-1 Ollama run (spike's own endpoint/model or equivalent), and whichever
of `claude`/`codex` login is available for a real tier-2 run — both
recorded in `docs/implementation.md` with actual commands/output, exactly
as 6a recorded its own manual verification. `aider`'s real-CLI tier-3
status is explicitly **not** claimed beyond the stand-in fixture, same
disclosure 6a already gave `aider.engine` itself.

## Correction: `{schema}` is inline for Claude, a file for Codex (2026-08-13)

Found by running the real `claude` CLI, not by re-reading docs. The spec's
single `HEADLESS_SCHEMA_FLAG=--json-schema {schema}` path-substitution design
is wrong: the two engines take **different forms**, confirmed from their own
`--help`:

```
claude  --json-schema <schema>   inline JSON Schema text
codex   --output-schema <FILE>   path to a JSON Schema file
```

**Two placeholders, not one** — mirroring the `{prompt}` / `{prompt_file}`
distinction 6a already established for `HEADLESS_PROMPT=arg|file`, so this is
an existing pattern rather than a new mechanism:

- `{schema}` — substituted with the schema's **JSON text**, `shlex.quote()`d
  as a single argv element like any other prompt-shaped value.
- `{schema_file}` — substituted with a **path** to the schema written into the
  run directory, alongside the prompt file, covered by the same
  `try/finally` cleanup.

Engine definitions become:

```
claude.engine   HEADLESS_SCHEMA_FLAG=--json-schema {schema}
codex.engine    HEADLESS_SCHEMA_FLAG=--output-schema {schema_file}
```

A definition using neither placeholder is a configuration error and must be
reported as such at roster-build time, not at the first tier-2 lead call.

Note the inline form interacts with `TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES`:
the schema now occupies argv space alongside the prompt. The four-tool schema
is well under any cap, but the size check must account for both rather than
the prompt alone.

## Correction: repeated delegation of an already-completed task

A live `qwen3:8b` run delegated the *same* task to `claude` twice before
calling `finish`, with a correct, well-formed prior result already in context.
Not a crash, and not a spec violation — a judgment miss, the same class as the
spike's single `wrong_tool`.

Mitigate at the prompt level, where the other tier-1 judgment issues are
handled: the round-history summary must make prior delegations and their
outcomes **explicit and salient** — agent, task, and whether it succeeded —
rather than leaving them to be inferred from a prose transcript. Cheap, and
consistent with how `fact_check`'s recall gap is handled.

## Open questions

- **Should a second `delegate` call to the *lead's own engine name* (when
  the lead is tier 2/3 and that same engine is also in `--members`) be
  disallowed?** Assumption: no special-case — if an operator configures it
  that way, "get codex to also do this subtask itself" is a legitimate
  thing to want, and disallowing it would be presumptuous with no stated
  reason to. Flagging in case the coordinator disagrees.
- **`TEAM_LLM_TIMEOUT_SECONDS=120` and the retry budget together mean a
  single stuck round can take up to ~6 minutes (3 attempts × 120s) before
  falling through to the malformed/escalation path.** Reasonable for a CLI
  tool; worth revisiting once 6d wires this behind a web request/poll loop
  where a human is actively waiting on a status page.
- **Whether `_round_context()`'s one-line history summaries should include
  the *teammate's* own `session_id`**, so a human reading `transcript.jsonl`
  later could `--resume` that teammate by hand outside the team loop.
  Leaning: yes, cheap to add, but not required by any acceptance criterion
  above — left to the developer's judgment during implementation rather
  than specified as a hard requirement.
- **Whether tier 2's schema file should be reused across rounds (written
  once, referenced by path every round) rather than rewritten every round.**
  It's static per run (the tool schema doesn't change round to round), so
  rewriting it identically every round is wasted I/O, not a correctness
  risk — leaning: write once at team-start, reuse the path for every
  subsequent round's `agent_run(..., schema=...)` call. Left as an
  implementation detail rather than a hard requirement, since either way
  is observably correct.

## Risk / rollback notes

Everything here is new code behind new CLI subcommands and one new
optional `agent_run()` keyword argument that defaults to `None` (existing
call sites unaffected). Nothing in `app/app.py`'s HTTP surface, session
lifecycle, or any existing route changes. The two new `Engine` fields are
additive and `None` by default, so every existing `.engine` file — and
every existing test that constructs an `Engine` without them — is
unaffected. Rollback is `git revert` of this cycle's commit(s); no schema/
data migration, no running state to unwind beyond deleting
`TEAM_STATE_DIR/leads/` if a partial run's directories are considered
worth cleaning up (they're inert otherwise — nothing reads them except the
CLI commands this spec adds).
