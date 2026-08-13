# Story: Multi-agent orchestration (customizable per-session teams)

**Backlog item 6.** Picked up 2026-08-13. This document is the story-level
breakdown: the settled architecture, the research that grounds it, and an
ordered list of six sub-specs. Each sub-spec gets its own full
`product-manager → ux-designer → developer → reviewer` cycle with its own
`docs/spec.md`; this file is the thing that survives across all of them.

---

## 1. Intent

Not a fixed lead-plus-fixed-team wiring, but a **generic, customizable
roster**. Every engine the switchboard knows about (`engines.d/*.engine`
entries, plus Ollama models once local-LLM support exists) is a selectable
roster member, independent of any one project. Per session, you build
whichever team you want from that roster and pick one member as lead.

The lead delegates to its teammates and only escalates to the human — in the
web UI — when something stays genuinely unresolved.

This must work for anyone who ran the standard Proxmox/LXC container setup,
not just the original homelab. It is a general feature of the install, not a
one-off wiring.

---

## 2. Research findings (2026-08-13)

The backlog required deep research on the agent-to-agent communication
mechanism **before** committing to one, and specifically warned against
defaulting to tmux `send-keys`/`capture-pane` because it is the path of least
resistance. The research concluded it is no longer even that.

### 2.1 Every shipped engine has a structured non-interactive mode

| Engine | Non-interactive entry | Structured output | Multi-turn resume |
|---|---|---|---|
| **Claude Code** | `claude -p "<prompt>"` | `--output-format stream-json` → NDJSON, one self-contained JSON object per line. `--input-format stream-json` gives **bidirectional** structured stdio. `--json-schema` constrains the result to a JSON Schema (lands in `structured_output`). | `--resume <session-id>` (locates the session in any project on the machine since v2.1.223), or `--continue` for the most recent |
| **Codex CLI** | `codex exec "<prompt>"`, or `cat prompt.txt \| codex exec -` | `--json` → JSONL event stream. `--output-schema ./schema.json` enforces a response shape. `-o <path>` also writes the final message to a file. | `codex exec resume <SESSION_ID>` / `codex exec resume --last` |
| **aider** | `aider --message "<prompt>"` / `--message-file <path>`, with `--yes-always` | No structured stream — exit code plus final stdout. Also has a real Python API (`Coder.create(main_model=…, io=InputOutput(yes=True), fnames=[…]).run(msg)`). | Process-per-turn; no session ID |

**Claude Code stream event types:** `system` (with `subtype` `init` /
`api_retry` / `plugin_install`), `assistant`, `user`, `stream_event` (partial
deltas, needs `--include-partial-messages`), and a final `result` message
carrying the response text, `total_cost_usd`, and session metadata.
Sub-agent messages carry `parent_tool_use_id` set to the ID of the tool call
that spawned them (`null` for the main conversation), so a lead-and-team
trace is reconstructable from a single stream — including nested subagents.

**Codex event types:** `thread.started`, `turn.started`, `turn.completed`,
`turn.failed`, `item.started`, `item.completed` (messages, commands, file
changes, MCP calls), `error`. Progress goes to stderr; the final agent
message goes to stdout.

**Scoping and safety flags that matter for unattended teammates:**
Claude Code — `--allowedTools "Bash,Read,Edit"`, `--permission-mode dontAsk`
(denies anything outside `permissions.allow` and the read-only command set),
`--append-system-prompt` for per-role instructions, `--bare` for a
reproducible run that skips host hooks/plugins/`CLAUDE.md` auto-discovery.
Codex — `--sandbox workspace-write`, `--skip-git-repo-check`,
`--ignore-user-config`.

**Lifecycle facts worth designing around:** `claude -p` exits 0 on success,
non-zero on failure, and **143 on SIGTERM** after aborting the turn, killing
the process tree of any running Bash command, and running `SessionEnd` hooks
— so a clean team stop is `SIGTERM` plus a wait, not `kill -9`. Piped stdin is
capped at 10 MB. Background subagents are waited for, capped at ten minutes by
default (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`).

### 2.2 Decision: tmux is displaced as the transport, retained as the host

**Settled with the user this session** (the backlog required explicit sign-off
before a spec could drop tmux as the transport):

> **Hybrid.** Each teammate is a headless process (`claude -p --output-format
> stream-json`, `codex exec --json`, `aider --message-file … --yes-always`),
> but spawned inside its own **tmux window** so a human can still attach and
> watch raw output live. Lead↔teammate messages travel as NDJSON over
> stdio and per-agent `.jsonl` logs. `capture-pane` is **never** used to read
> an agent's answer.

This keeps "everything is a tmux session" — the project's core primitive since
the start — intact, while the actual message passing gets a real format, a
real completion signal, and immunity to CLI prompt-format changes.

**Correction (6d spec, 2026-08-13): "spawned inside its own tmux window" must
NOT be read as "the window's own command is the headless CLI".** That literal
reading is incompatible with two settled constraints at once. It breaks 6a's
design, where `agent_run()` spawns one throwaway session per call and
continuity comes from `--resume {session_id}` rather than a persistent
window; and it breaks the no-new-sudoers constraint, since a process already
running as `RUN_USER` cannot itself invoke `sudo -u RUN_USER tmux` without a
new sudoers rule — the exact privilege escalation §3 forbids.

The window is therefore a **dashboard, not the process**: each persistent
per-agent window `tail -F`s that agent's own stable append-mode `.jsonl` log
(`agent_run()`'s `log_path`, already append-mode). A human attaching still
watches live raw output, which is the whole point of the hybrid decision —
but the agent process itself continues to be spawned exactly as 6a spawns it.
Same visibility, no new privileged path. 6e and 6f should inherit this
reading, not the sentence above it.

### 2.3 What the ecosystem already does (and what to take from it)

Surveyed: **Vibe Kanban** (cross-platform CLI + web UI, Kanban board,
worktree-per-task, supports Claude Code / Codex / Gemini / Cursor / Amp —
Bloop shut down 2026-04-10, now community-maintained), **Conductor**
(macOS-only, parallel worktrees, diff-first review), **Claude Squad**
(terminal-native), **Omnara** (phone steering), **Microsoft Conductor**
(YAML workflows + web dashboard).

None is a substitute for this switchboard: none carries the Proxmox/LXC
install story, TOTP auth, `PUBLISH_MODE`, Gitea integration, or the deploy
receiver. **Decision: build in this repo, not a separate one** (settled with
the user) — a separate repo would duplicate auth, install, publish-mode and
project discovery, and create a two-installer maintenance story.

**The one pattern worth stealing, which every surveyed tool converged on
independently: one git worktree per agent.** That is the universal answer to
concurrent agents not clobbering each other, and it maps onto superpowers'
`using-git-worktrees` skill the pipeline already uses.

### 2.4 Local-LLM precedent already in the codebase

`app/app.py:111-112, 419-421` already calls an OpenAI-compatible endpoint with
stdlib `urllib` for optional per-project descriptions:

```
#DESC_LLM_BASE_URL=http://127.0.0.1:11434/v1
#DESC_LLM_MODEL=qwen3:8b
```

The Ollama lead is that same call shape plus a `tools` array — no new
dependency, no break from "stdlib-only Python, one file". Ollama's
OpenAI-compatible `/v1/chat/completions` supports `tools` (function calling)
natively on tool-capable models (qwen3, llama4:scout, mistral, gemma).

### 2.5 Spike: the lead runs remotely, and tier 1 works (2026-08-13)

Run before specifying 6c, because its central premise — that a small local
model can drive the four-tool loop — had never been tested.

**The lead cannot run on the switchboard container.** Measured on the standard
LXC container this project targets:

```
Mem:  total=2048MB  used=1332MB  available=715MB
Swap: total=512MB   free=0MB          (already exhausted)
      2 cores, no GPU
```

`qwen3:8b` needs roughly 5.5 GB — about 8x what is free. Even `llama3.2:1b`
(~1 GB) does not fit. **No tool-capable model can run here**, so the earlier
"`install.sh --with-ollama` installs Ollama natively" decision is dead.

This is a deployment problem, not an architecture problem. `TEAM_LLM_BASE_URL`
is a URL and was always allowed to point anywhere; `127.0.0.1:11434` was only
the example value. `--with-ollama` therefore becomes a **link** step: prompt
for an existing endpoint and model, validate reachability, write `TEAM_LLM_*`.
Nothing is installed. This also mirrors the existing precedent for work that
lives outside the container — `host-agent/` reaches the Proxmox host over a
scoped SSH key rather than doing the work locally.

**Tier 1 tool-calling works.** Against a real remote Ollama (a separate
container on the tailnet) with `qwen3:8b`, driving the actual four-tool schema
(`delegate` / `fact_check` / `ask_user` / `finish`) over
`/v1/chat/completions`:

```
RESULT   9/10 well-formed correct tool calls (90%)
         1x wrong_tool  (chose fact_check where ask_user was right)
         0x prose_fallback   0x malformed_args   0x transport_error
latency  mean 7.4s   max 20.8s
```

Zero prose fallbacks and zero malformed arguments across ten varied prompts.
The single miss was a **judgment** error — a well-formed call to the wrong
tool — not a format failure, so it is prompt-tunable rather than an
adapter-level problem.

Two things this pins down for 6c: the tier-1 adapter is viable as designed,
and per-delegation latency is seconds, not sub-second — so the lead loop must
be asynchronous and the UI must show a working state rather than blocking.

---

## 3. Settled decisions

| Question | Decision | Rationale |
|---|---|---|
| Transport | Hybrid — tmux hosts, NDJSON carries messages | §2.2 |
| Where it lives | This repo, `app/teams.py` + a Teams page | `app.py` is already 3012 lines; a new repo duplicates auth/install/discovery |
| Overwatch surface | Unified live event feed across all agents, per-agent filter, status strip | Users asked to "simplify the overwatch"; a terminal grid is unreadable past ~3 agents |
| Who can lead | **Every** roster member, via three adapter tiers (§4.2). Ollama-backed local model is the default | The generic-roster intent supersedes the earlier "lead is a local LLM" wording, which becomes the *default* rather than the only option |
| Grounding source | Auto-discovered project docs — `docs/ARCHITECTURE.md`, `docs/BACKLOG.md`, `CLAUDE.md`/`AGENTS.md`, `README.md` | Zero config, works on any project, reuses `_gather_project_context()`'s existing discovery shape |
| Grounding mechanism | Bounded digest in the lead's system prompt **and** an on-demand `fact_check(claim)` tool | Bounds context for a local model while keeping specific claims verifiable with `file:line` evidence |
| Grounding writes | **Read-only.** The lead never writes to the backlog or any grounding file | Same reasoning as deploy-is-manual-only: no agent mutates the project's source of truth unattended |
| Escalation gate | Lead calls `ask_user` on its own judgment; `TEAM_MAX_ROUNDS` (default 8) forces escalation as a backstop | Adaptive where it matters, bounded against runaway spend |
| Escalation shape | Structured question + 2–4 pickable options (§4.5), never a bare text prompt | Matches the pipeline's own `AskUserQuestion`; a good question with options is answerable in one tap from a phone |
| Ollama install | **Superseded 2026-08-13 — see §2.5.** `install.sh --with-ollama` **links** an existing remote Ollama; it does not install one locally | The standard container has 2 GB RAM with ~715 MB free and swap exhausted. No tool-capable model fits — `qwen3:8b` needs ~8x that. `TEAM_LLM_BASE_URL` is a URL, so remote works with zero code change |
| Isolation | One git worktree per teammate | §2.3 |

### Non-goals for this story

- Cross-project teams. A team is scoped to exactly one project folder.
- Replacing the existing single-engine per-project rows. Teams are additive;
  the current toggle behaviour is untouched.
- Any change to the `product-manager → ux-designer → developer → reviewer`
  pipeline in `D:\Entwicklung\.claude`. This repo delivers the mechanism only.
- Automatic deploy off a team's work. Deploy stays manual-click-only
  (backlog item 2c part 2's explicit decision).

---

## 4. Architecture

```
tmux session: team-<project>
├── win 0  lead      Ollama loop (or an engine acting as lead)
│                    tools: delegate(agent, task) / ask_user(q) / finish(summary)
├── win 1  claude    claude -p --output-format stream-json --input-format stream-json
├── win 2  codex     codex exec --json    (resume <SESSION_ID> for turn 2+)
└── win 3  aider     aider --message-file <f> --yes-always

worktrees   PROJECTS_DIR/<name>.teams/<agent>/     one git worktree per teammate
grounding   PROJECTS_DIR/<name>/{docs/ARCHITECTURE.md, docs/BACKLOG.md,
                                 CLAUDE.md|AGENTS.md, README.md}   READ-ONLY
state dir   TEAM_STATE_DIR/<name>/
              team.json          composition, lead, per-agent status, round count
              <agent>.jsonl      normalized event log, append-only
              inbox.json         pending ask_user escalations (question + options)
transport   NDJSON over stdio + those .jsonl files — never capture-pane
```

### 4.1 Normalized event shape

Every engine's native stream is translated into one internal envelope so the
overwatch feed and the lead both read a single format:

```json
{"ts": "2026-08-13T12:04:31Z", "agent": "claude", "seq": 17,
 "kind": "message|tool_use|tool_result|status|error|handoff",
 "text": "…", "meta": {"native_type": "assistant", "session_id": "…"}}
```

`kind` is the closed set the UI renders. `meta` carries whatever the engine
gave us verbatim, so nothing is lost and new engines need no UI change.

### 4.2 Engine-definition extension

`engines.d/*.engine` gains optional keys. An engine without them is
teammate-ineligible and simply doesn't appear in the roster — existing files
keep working untouched, consistent with "why engines are config, not code"
(`docs/ARCHITECTURE.md`).

```
HEADLESS_CMD=claude -p --output-format stream-json --verbose
HEADLESS_FORMAT=claude-stream-json      # claude-stream-json | codex-jsonl | plain
HEADLESS_RESUME=--resume {session_id}
HEADLESS_PROMPT=arg                     # arg | stdin | file
HEADLESS_ROLE_FLAG=--append-system-prompt {role}
HEADLESS_LEAD_FORMAT=schema             # optional override; schema | prose
HEADLESS_SCHEMA_FLAG=--json-schema {schema}
```

**On `--input-format stream-json`.** It is deliberately *not* in the default
`HEADLESS_CMD`. It requires the prompt itself to be a JSON message rather than
plain text, and it only earns its cost for a *persistent, multi-turn* process
held open across delegations. A delegation in this design is one bounded task
per invocation, resumed by session ID — so `-p "<prompt>"` with
`--output-format stream-json` out, `--resume` for turn 2+, is both simpler and
sufficient. Keeping a long-lived bidirectional process per teammate is a
possible later optimization (it would avoid re-establishing context each turn)
and is explicitly out of scope for this story.

**Every roster member can lead.** There is no lead-capable/ineligible split.
What differs is *how* a lead's tool choice is extracted, and the reliability
that follows. Three adapter tiers, picked automatically per member:

| Tier | Applies to | Mechanism | Reliability |
|---|---|---|---|
| **1 — native tool-calling** | Ollama models via `/v1/chat/completions` | `tools` array; model returns `tool_calls` | Highest |
| **2 — constrained output** | Claude Code (`--json-schema`), Codex (`--output-schema`) | Schema-enforced JSON result per turn | High |
| **3 — prose parse** | aider, and any engine with no structured-output flag | Lead is instructed to emit a single fenced ```json block; tolerant parser, bounded retry on malformed output, then escalate | Lowest |

A tier-3 lead is a real option, not a token one — but it *will* be flakier, so
the roster UI (6d) labels each member with its tier rather than hiding the
tradeoff. `HEADLESS_LEAD_FORMAT=schema|prose` in an engine definition overrides
the auto-detected tier when an engine gains or loses a structured-output flag.

### 4.3 Grounding — what the lead fact-checks against

The lead is never allowed to plan against nothing. At team start, `app/teams.py`
**auto-discovers** the project's own documentation — no configuration, no new
artifact to author:

```
PROJECTS_DIR/<name>/
├── docs/ARCHITECTURE.md    ┐
├── docs/BACKLOG.md         │  grounding set
├── CLAUDE.md | AGENTS.md   │  (each optional — missing files skipped)
└── README.md               ┘
```

This reuses the discovery shape `_gather_project_context()` in `app.py`
already uses for per-project descriptions, including its `@`-indirection
handling for a one-line `CLAUDE.md` that just points at another file.

The grounding set is used **two ways**:

1. **Digest at start.** A bounded summary (headings plus the first N bytes per
   file, hard-capped so it can't blow a local model's context window) is
   seeded into the lead's system prompt, so its very first plan is made against
   the project's real architecture and backlog.
2. **`fact_check(claim)` tool.** On demand, the lead can verify a specific
   claim — its own or a teammate's reported result — against the grounding set.
   Returns the matching passages with `file:line`, or an explicit
   "no supporting passage found", which is itself a useful signal.

**Grounding is strictly read-only.** The lead cannot write to
`docs/BACKLOG.md` or any other grounding file. Work the team discovers surfaces
in the run summary and the overwatch feed for a human to promote — consistent
with the deploy-is-manual-only precedent: no agent mutates the project's own
source of truth unattended.

### 4.4 The lead loop

```python
for round in range(TEAM_MAX_ROUNDS):
    action = lead.next()               # tier 1/2/3 adapter — see §4.2
    if action.name == "delegate":
        result = agent_run(action.agent, action.task)   # blocks; NDJSON to .jsonl
        lead.feed(result)
    elif action.name == "fact_check":
        lead.feed(grounding.lookup(action.claim))       # read-only, file:line
    elif action.name == "ask_user":
        block_and_write_inbox(action)                   # resolved from the UI
    elif action.name == "finish":
        break
else:
    force_ask_user("Team did not converge after TEAM_MAX_ROUNDS rounds.")
```

### 4.5 `ask_user` is a structured question, not a text box

When something is genuinely unclear the lead must ask the human with concrete,
pickable options — the same shape the pipeline's own `AskUserQuestion` uses —
rather than emitting an open-ended prompt the user has to compose an answer to:

```json
{"name": "ask_user",
 "question": "Should the retry live in the client or the caller?",
 "header": "Retry",
 "options": [
   {"label": "In the client", "description": "Every caller inherits it; matches how host_run() already behaves."},
   {"label": "In the caller",  "description": "Explicit per call site; more code, no hidden behaviour."}],
 "multi_select": false}
```

Rules: 2–4 options, each with a one-line consequence; `header` ≤ 12 chars for
the UI chip; the UI always offers a free-text "Other" alongside them, so the
lead constraining the options can never trap the user. A tier-3 lead that
emits a malformed question is retried once, then the raw text is surfaced as a
plain question rather than dropping the escalation.

---

## 5. Sub-specs

Ordered so each ships something independently usable and no single developer
pass carries a disproportionate share.

### 6a — Headless engine invocation

**Deliverable.** The `HEADLESS_*` keys above, parsed by `_parse_engine_file()`.
A new `app/teams.py` with `agent_run(engine, workdir, prompt, session_id=None)`
that spawns the headless process, translates its native stream into the §4.1
envelope, appends to a `.jsonl` log, and returns a normalized result
(`{ok, text, session_id, exit_code}`). A small CLI entry point so it is
runnable and testable without any UI.

**Acceptance criteria.**
- Each of `claude.engine`, `codex.engine`, `aider.engine` gains working
  `HEADLESS_*` keys, **verified by actually running each one** through
  `agent_run()` against a scratch project — not guessed
  (`docs/ADDING_AN_ENGINE.md`'s standing rule).
- A malformed or partial line in an engine's stream is skipped with an
  `error`-kind event, never kills the run.
- `SIGTERM` to a running agent produces a clean stop; Claude Code's exit 143
  is reported as a cancellation, not a failure.
- An engine with no `HEADLESS_CMD` is silently teammate-ineligible; existing
  three-engine single-session behaviour is byte-for-byte unchanged.
- `docs/ADDING_AN_ENGINE.md` documents the new keys.

### 6b — Grounding: discovery, digest, and `fact_check`

Deliberately its own cycle: it is pure functions over files — no LLM, no
process spawning — so it is fast to build, trivial to test exhaustively, and it
is a hard dependency of the lead loop. Splitting it keeps 6c from carrying
both the grounding logic and the tool-calling adapters in one pass.

**Deliverable.** A grounding module in `app/teams.py`: auto-discovery of the
§4.3 file set, a bounded digest builder, and `fact_check(claim)` returning
matching passages with `file:line`. Read-only by construction — the module
exposes no write path at all.

**Acceptance criteria.**
- Discovers each of `docs/ARCHITECTURE.md`, `docs/BACKLOG.md`,
  `CLAUDE.md`/`AGENTS.md`, `README.md` when present; missing files are skipped
  silently, and a project with **none** of them still starts a team (with the
  lead told explicitly that it has no grounding).
- Handles the one-line `@`-indirection `CLAUDE.md` case, matching
  `_gather_project_context()`'s existing behaviour.
- The digest is hard-capped in bytes and cannot exceed the cap regardless of
  input size — verified against a deliberately oversized `BACKLOG.md`
  (this repo's own is 20 KB, a realistic case, not a synthetic one).
- `fact_check` on a claim with no support returns an explicit
  "no supporting passage found" rather than an empty string or the nearest
  weak match.
- No code path in the module can write to, truncate, or create a file inside
  the grounding set — asserted by a test, not just by inspection.

### 6c — Roster + lead loop (all three adapter tiers)

**Deliverable.** `TEAM_LLM_BASE_URL` / `TEAM_LLM_MODEL` config (siblings of
`DESC_LLM_*`, deliberately separate — a model good at one-line descriptions
may be a poor tool-caller). A roster assembled from `engines.d` entries plus
configured Ollama models, each tagged with its lead-adapter tier. The lead loop
of §4.4 with its **four** tools (`delegate`, `fact_check`, `ask_user`,
`finish`) and all three §4.2 adapters. Standalone script entry point; still
no UI.

**Acceptance criteria.**
- A full delegate → fact_check → delegate → finish cycle runs from the shell
  against a real project with a real Ollama model.
- **All three tiers demonstrated leading the same task**: an Ollama model
  (tier 1), Claude Code or Codex via constrained output (tier 2), and aider
  via prose parse (tier 3). Tier 3 is the one to prove, not assume.
- A tier-3 lead emitting malformed JSON is retried within a bounded budget,
  then escalates — it never loops and never silently stalls.
- `ask_user` blocks the loop and writes `inbox.json` in the §4.5 structured
  shape (question, header, 2–4 options); answering it by hand resumes the loop.
- `TEAM_MAX_ROUNDS` forces an escalation rather than looping forever.
- Ollama unreachable → a clear, actionable error, not a traceback.
- A tier-1 model that ignores the `tools` array and replies in prose is
  detected and falls back to tier-3 parsing rather than silently doing nothing.

**Status: done** (reviewer-approved round 3). All 17 acceptance criteria have
real implementation and test coverage; 588 tests green. Carried forward, none
blocking:

- **`codex` tier 2 is unverified end to end.** `codex` is unauthenticated in
  the build environment (confirmed: a real 401 from `api.openai.com`), so
  `--output-schema {schema_file}` is correct per `codex exec --help` but has
  never been exercised against the live binary. Deliberately *not* faked by
  any test. First thing to verify wherever an authenticated `codex` exists.
- **Repeated delegation is mitigated, not fixed.** `qwen3:8b` was observed
  delegating one task twice before finishing. Round-history summaries now
  state agent/task/SUCCEEDED-or-FAILED explicitly; it did not recur in 3/3
  live runs. That is a probabilistic small-model behaviour, and no test
  asserts non-recurrence — such a test would be flaky by construction.
- **`re.sub()` maps a `None` replacement to `""` silently.** After the
  single-pass substitution fix, calling `_build_headless_argv()` on a
  file-mode engine with `prompt_path=None` now silently drops `{prompt_file}`
  where the old chained-`str.replace()` code raised `TypeError`. Unreachable
  from the only real caller (`agent_run()` always supplies a path), so it is
  a latent sharp edge rather than a live bug — but it trades a loud failure
  for a quiet one, which is against this codebase's grain.
- **One timing-sensitive test flakes.** A real-tmux/real-thread `sleep(5)`
  test in `tests/test_teams_headless.py` failed once in ~8 full-suite runs
  and passed in isolation and on rerun. Not attributable to 6c (that file's
  diff is empty), but it is a real flake and should not be rediscovered from
  scratch later.

### 6d — Team session lifecycle

**Split into two build cycles** (product-manager's call, user-confirmed
2026-08-13): part 1 is worktree + tmux dashboard lifecycle, CLI/backend only;
part 2 is web routes, the background driving thread, and
`install.sh --with-ollama`. Part 1 alone is real-git + real-tmux + a new
self-heal contract — comparable in size and risk to any one of 6a/6b/6c — and
folding HTTP routes plus threading into the same dispatch is the shape that
produced this story's largest defects.

**Deliverable.** `team-<project>` tmux session, one **dashboard** window per
agent (see §2.2's correction — the window tails the agent's log, it does not
host the agent process). One git worktree per teammate under
`PROJECTS_DIR/<name>.teams/<agent>/`, created on start and cleaned up on stop.
Start/stop wired into the web UI as a per-project "Start team" control
(part 2). `_reap_dead_state()` extended to sweep dead team sessions, orphaned
worktrees, and stale state dirs.

**Resolved, not deferred:** `_session_urls` does **not** need generalizing
from per-project to per-window. Headless invocations never produce a hosted
URL, so there is nothing to generalize. This closes the story's own §5 note.

**Part 1 status: done** (reviewer-approved, commit `7a3e0eb`). 638 tests.
Four defects found and fixed, all in one area — tmux session lifecycle under
partial failure or concurrency. Carried forward:

- **This area is the story's hardest.** Nine defects total across 6a–6d;
  four of them are here, and each was found only by exercising real tmux and
  real git past the spec's enumerated cases. Part 2 puts HTTP routes and a
  background thread directly on top of this machinery — budget review effort
  accordingly rather than assuming part 1 stabilised it.
- **A filesystem-root `workdir` yields an empty `project_name`.** Currently
  unreachable: every caller supplies an already-`NAME_RE`-validated
  `PROJECTS_DIR/<name>`, and `_validate_project_for_team()` gates first.
  Deliberately left unhardened — **revisit in part 2**, whose HTTP route is
  the first thing that could plausibly accept an operator-supplied path.
- **One pre-existing unrelated flake.** A real-tmux test in
  `tests/test_teams_headless.py`
  (`test_run_sh_and_prompt_file_are_world_readable_under_a_strict_umask`)
  failed 2 of 17 full-suite runs and passes in isolation. That file's diff is
  empty, so it is not attributable to 6d, but it is real and should not be
  rediscovered from scratch.

**Acceptance criteria.**
- Starting a team creates the session, the windows, and the worktrees;
  stopping removes all three, including on an unclean stop.
- A team session and a plain single-engine session on the *same* project do
  not collide (distinct tmux names, distinct working trees).
- Attaching to any teammate's window shows that agent's live raw output.
- Service restart while a team runs: the UI self-heals to the true state,
  same discipline as the existing `_reap_dead_state()` contract.
- Worktree creation failure (dirty tree, detached HEAD, non-git project)
  fails the start with a specific message and leaves nothing behind.
- `install.sh --with-ollama` implemented, off by default, as a **link** step
  (§2.5): prompts for an existing endpoint URL and model name, validates the
  endpoint is reachable and the model is present, writes `TEAM_LLM_*`.
  Installs nothing locally, and refuses to point at an endpoint it cannot
  reach rather than writing config that fails later at team start.

### 6e — Roster & composition UI

**Deliverable.** A settings screen listing every roster member — `engines.d`
entries and Ollama models — each labelled with its lead-adapter tier. Per
project, a picker to choose lead + teammates before starting a team. Small,
and the only sub-spec with no new backend concepts.

**Acceptance criteria.**
- Roster reflects `engines.d` live (re-read per request, matching
  `load_engines()`'s existing no-cache rule).
- **Every** member is selectable as lead; each shows its tier and, for tier 3,
  a plain-language note that its reliability is lower. The tradeoff is
  surfaced, never hidden and never blocked.
- Which grounding files were discovered for this project is shown before
  start, so an absent `ARCHITECTURE.md` is visible rather than silent.
- A saved composition persists across service restarts.
- Starting with an empty teammate list is rejected with a clear reason.

### 6f — Overwatch feed + escalation inbox

**Deliverable.** The Teams page: one merged, filterable timeline over all
agents' `.jsonl` logs, colour-coded per agent, with a compact status strip
(idle / working / blocked / waiting-on-you). The structured-question inbox
(§4.5) that resolves a pending `ask_user` and unblocks the lead.

**Acceptance criteria.**
- The feed updates live while a team runs and survives a page reload
  (rehydrated from the `.jsonl` files, not from memory).
- Per-agent filter, and a "waiting on you" state that is impossible to miss.
- An escalation renders as its question plus 2–4 pickable options, **always
  with a free-text "Other"** so a badly-framed question can't trap the user.
- `fact_check` calls appear in the feed with the passage and `file:line` the
  lead was shown — the grounding is auditable, not a black box.
- Answering an escalation resumes the lead within one poll interval.
- A long-running team's feed stays responsive — logs are tailed with a bound,
  not read whole on every poll.
- TOTP gating matches every other state-changing action in the UI.

---

## 6. Affected areas

- `app/app.py` — engine parsing (`_parse_engine_file`), `_session_urls`
  generalization, `_reap_dead_state()`, `/status`, new routes, new page.
- `app/teams.py` — **new**; agent invocation, grounding, lead loop, lifecycle.
- `engines.d/*.engine` — new optional `HEADLESS_*` keys, all three engines.
- `config/switchboard.env.example` — `TEAM_LLM_BASE_URL`, `TEAM_LLM_MODEL`,
  `TEAM_STATE_DIR`, `TEAM_MAX_ROUNDS`, `TEAM_GROUNDING_MAX_BYTES`.
- `install.sh` — `--with-ollama`.
- `docs/ADDING_AN_ENGINE.md`, `docs/ARCHITECTURE.md`, `README.md`.

## 7. Open questions for the sub-spec cycles

- Which Ollama model ships as the documented default? `qwen3:8b` matches the
  existing `DESC_LLM_MODEL` example and is tool-capable, but this should be
  confirmed against actual tool-calling reliability during 6c.
- Should a teammate's worktree be merged back automatically on team finish, or
  left for the human to review and merge? Leaning: left for review, consistent
  with deploy being manual-click-only.
- Does the lead need read access to teammates' `.jsonl` logs, or only their
  returned results? Leaning: results only — `fact_check` against the grounding
  set covers the verification need without unbounding the lead's context.
- Per-teammate `--allowedTools` / `--sandbox` scoping: configured per roster
  member, per team, or fixed conservative defaults? Deferred to 6e.
- Should `fact_check` also cover the *code*, not just the docs (e.g. grep the
  worktree)? Out of scope as specified — teammates already read code directly,
  and it would unbound the lead's context. Revisit if 6c shows the lead
  fact-checking claims the docs genuinely can't settle.
