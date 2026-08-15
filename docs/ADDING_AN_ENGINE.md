# Adding an engine

An "engine" is just a CLI coding agent — Claude Code, aider, OpenAI's Codex
CLI, or anything else that runs interactively in a terminal. Adding support
for one is a config file, not a code change: drop a `<name>.engine` file
into `ENGINES_DIR` (`/etc/ai-dev-switchboard/engines.d` by default) and it
shows up as a new option in the web UI's engine picker immediately — no
restart required, `app.py` re-reads the directory on every status poll and
every session start.

## The format

Plain `KEY=value` lines, one per line. `#` starts a comment; blank lines are
ignored. Values are never shell-evaluated on either the Python or the bash
side — an engine file is data, not code, on purpose.

```
LABEL=Display name shown in the UI
CMD=the-cli-command {name}
URL_REGEX=optional regex
STARTUP_MATCH_1=optional text to watch for
STARTUP_SEND_1=keys to send when STARTUP_MATCH_1 appears
STARTUP_MATCH_2=...
STARTUP_SEND_2=...
```

- **`LABEL`** — what the engine picker shows. Defaults to the filename
  (without `.engine`) if omitted.
- **`CMD`** *(required)* — the shell command to run. `{name}` is replaced
  with the project name (or, for the optional host-control row, whatever
  `HOST_SESSION_NAME` is set to).
- **`URL_REGEX`** *(optional)* — if the engine prints its own hosted
  "remote control" link on startup (like Claude Code's
  `claude.ai/code/session_...` URL), set this to a regex that matches it.
  The switchboard watches the session's terminal output for a match right
  after launch and surfaces it as the "open" link in the UI.
- **`STARTUP_MATCH_N` / `STARTUP_SEND_N`** *(optional, repeatable)* — a
  scripted one-time interaction: when the pane's output contains
  `STARTUP_MATCH_N`, `STARTUP_SEND_N` is sent as a keystroke (followed by
  Enter), and the watcher moves on to `N+1`. Used by `claude.engine` to
  clear Claude Code's one-time "do you trust this folder" prompt. Numbering
  starts at 1 and must be contiguous — the loop stops at the first missing
  `N`.

## The two patterns

**Engine has its own hosted link** (set `URL_REGEX`): the switchboard
surfaces that engine's own link directly. This is what `claude.engine`
does — see it for a complete working example, trust-prompt handling
included.

**Engine has no hosted link** (leave `URL_REGEX` unset): the switchboard
falls back to its own built-in ttyd web terminal automatically, sharing the
exact tmux pane the engine is running in. This is the safe default for any
CLI tool — you get a working "open" link with zero extra config, whether or
not the tool has (or even could have) a hosted remote-session feature of its
own. `aider.engine` relies on exactly this fallback.

## Codex

`engines.d/codex.engine` ships working, verified against codex-cli 0.147.0
by actually running it through this switchboard end to end — not guessed.
Worth knowing what that verification found, since it shapes how any new
engine's file should look:

- Codex's positional argument is an initial chat prompt, not a session
  label — unlike `claude.engine`, `CMD` deliberately does **not** use
  `{name}` here. Passing the project name there would feed it to the agent
  as if you'd typed it as your first message.
- Codex has its own one-time-per-directory "Do you trust the contents of
  this directory?" prompt, functionally identical to Claude Code's — same
  `STARTUP_MATCH_1`/`STARTUP_SEND_1` mechanism handles it.
- Codex does have a `codex remote-control` subcommand, but it's an
  `[experimental]` raw websocket/unix-socket protocol meant for a *custom*
  TUI client, not a hosted browser link — there's nothing to point
  `URL_REGEX` at, so it isn't set, and Codex runs via the ttyd fallback
  instead. That fallback gives you a real interactive terminal, so any of
  Codex's own per-command approval prompts still work exactly as they would
  over SSH.
- Signing in (ChatGPT / device code / API key) is a separate, one-time,
  account-level step, deliberately left out of `STARTUP_MATCH`/`SEND` —
  same as Claude Code's own login, that's a credential decision for a human
  to make once, not something to script.

## Adding another engine

Follow the same process: run the CLI by hand first, in a scratch directory,
and actually watch what it does on a truly first run — don't guess at
prompt text or assume a tool has (or lacks) a hosted-link feature. If it
turns out to have its own first-run prompts, add
`STARTUP_MATCH_N`/`STARTUP_SEND_N` pairs the same way `claude.engine` and
`codex.engine` do. If it turns out to have its own hosted session-link
feature, add `URL_REGEX` to use that instead of the ttyd fallback — which
remains the safe, correct default in the meantime, and forever for tools
that simply don't have one.

The same `.engine` file format is read by both `app/app.py` (per-project
sessions) and `host-agent/lib/engine-lib.sh` (the optional single
persistent host session) — write it once, both use it.

## Headless invocation (`HEADLESS_*`, backlog item 6a)

Four more optional `KEY=value` lines, read by `app/teams.py`'s
`agent_run()` — a *separate* code path from everything above. They're about
one bounded, non-interactive turn with structured output (for a future
multi-agent team to delegate to), not the interactive tmux session the rest
of this doc covers. An engine file with none of these four keys is
completely unaffected — parses and runs exactly as it does today.

```
HEADLESS_CMD=claude -p {resume} --output-format stream-json --verbose
HEADLESS_FORMAT=claude-stream-json      # claude-stream-json | codex-jsonl | plain
HEADLESS_PROMPT=arg                     # arg | stdin | file
HEADLESS_RESUME=--resume {session_id}   # optional -- omit entirely if the engine has no resume concept
```

- **`HEADLESS_CMD`** — the non-interactive command template. Two
  placeholders, substituted with plain string replacement (never
  `str.format()`, so a literal `{`/`}` elsewhere in the command — e.g. a
  future JSON Schema flag — can't break it):
  - **`{resume}`** — the empty string on a first turn, or `HEADLESS_RESUME`
    (with `{session_id}` substituted into *that* first) when resuming.
    Sits inline because some engines take resume as a flag (Claude Code:
    `--resume <id>`) and others as a subcommand swap (Codex:
    `exec resume <id>`, not `exec ... --resume <id>`).
  - **`{prompt_file}`** — only meaningful when `HEADLESS_PROMPT=file`;
    substituted with the path of a file `agent_run()` already wrote the
    prompt into.
- **`HEADLESS_FORMAT`** *(required if `HEADLESS_CMD` is set)* — one of
  `claude-stream-json`, `codex-jsonl`, `plain`. Tells `agent_run()` how to
  translate the engine's native stdout into the normalized event envelope
  (`docs/story.md` §4.1). `plain` means "no structured stream at all" (e.g.
  aider) — the whole captured stdout becomes one `message` event.
- **`HEADLESS_PROMPT`** *(required if `HEADLESS_CMD` is set)* — how the
  prompt is delivered: `arg` (appended as its own argv element, e.g. Claude
  Code's positional query), `stdin` (piped in as the process's actual
  stdin), or `file` (written to `{prompt_file}`, e.g. aider's
  `--message-file`). `arg` mode has a materially tighter byte cap than
  `stdin`/`file` — see `TEAM_HEADLESS_ARG_PROMPT_MAX_BYTES` in
  `config/switchboard.env.example` — since the prompt ends up as its own
  argv element when the engine binary itself is exec'd, capped by Linux's
  own per-argv-element `MAX_ARG_STRLEN`. `stdin`/`file` mode has no such
  constraint — the prompt never appears in any argv at all.
- **`HEADLESS_RESUME`** *(optional)* — omit entirely for an engine with no
  resume/session concept at all (aider). If a `session_id` is ever passed to
  `agent_run()` for such an engine, it raises `ValueError` before spawning
  anything, rather than silently ignoring it.

If `HEADLESS_CMD` is present but `HEADLESS_FORMAT`/`HEADLESS_PROMPT` are
missing or not one of the recognized values above, the rest of the file
still parses normally (`LABEL`/`CMD`/`URL_REGEX`/`STARTUP_*` all still
work) — the engine is just left headless-ineligible
(`Engine.headless_enabled == False`), never a `load_engines()` failure.

**Reserved names: the whole `switchboard` and `team` prefixes.** Any
`.engine` file whose filename stem (with `.engine` stripped) *starts with*
either `switchboard` or `team` is ignored by `load_engines()` — same
"intentionally inert" treatment `.engine.example` templates get. `switchboard`
is what keeps a headless run's own throwaway tmux session
(`switchboard-headless-<run_id>`) structurally unable to collide with any
real project's own `<engine>-<project>` session name, including the
non-obvious case of an engine literally named `switchboard` combined with a
project directory named `headless-<run_id>`. `team` (backlog item 6d part 1)
guards the identical collision shape for a **team**'s own
`team-<project>` tmux dashboard session (`app/teams.py`) — an engine
literally named `team` (or `team-anything`) would otherwise produce a
single-engine session name `f"{engine}-{project}"` == `f"team-{project}"`
for any project. Don't name your own engine file starting with `switchboard`
or `team`.

**`HEADLESS_ROLE_FLAG`** (mentioned in `docs/story.md` §4.2) stays
**reserved** — 6c deliberately did not implement it (see `docs/spec.md`
"Deviation: no `HEADLESS_ROLE_FLAG` this round" for why: every lead tier
already needs a working "put instructions in front of the model" path
anyway, so a fourth, engine-specific, partially-available channel for the
identical content isn't worth its complexity yet). Not yet parsed or
consumed by anything in this codebase.

## Lead-adapter hints (`HEADLESS_SCHEMA_FLAG`/`HEADLESS_LEAD_FORMAT`, backlog item 6c)

Two more optional `KEY=value` lines, read by `app/teams.py`'s
`roster()`/`_lead_tier_for_engine()` and consumed by `agent_run()`'s
`schema=` keyword argument. Together they decide which of the three lead
adapters (`docs/story.md` §4.2) an engine uses when it's picked as a team's
lead — unrelated to whether it can be *delegated to*, which only ever
depends on the four `HEADLESS_*` keys above.

```
HEADLESS_SCHEMA_FLAG=--json-schema {schema}        # optional; inline JSON text
HEADLESS_SCHEMA_FLAG=--output-schema {schema_file} # optional; a file path
HEADLESS_LEAD_FORMAT=schema                        # optional override: schema | prose
```

- **`HEADLESS_SCHEMA_FLAG`** — a command-line flag TEMPLATE for asking the
  engine to constrain its output to a JSON Schema. **Two placeholders, not
  one** — mirroring the `{prompt}`/`{prompt_file}` distinction
  `HEADLESS_PROMPT=arg|file` already established, an existing pattern
  rather than a new mechanism (this replaced an earlier, single-placeholder
  design that turned out to be wrong for one of the two shipped engines —
  see "Real, verified finding" below):
  - `{schema}` — substituted with the schema's own **JSON text**,
    `shlex.quote()`'d as a single argv element (Claude Code's own
    `--json-schema <schema>`, whose own `--help` example is inline JSON,
    not a path).
  - `{schema_file}` — substituted with the **path** of a `schema.json`
    file `agent_run(..., schema=...)` writes under the run's own throwaway
    rundir (mirrors `{prompt_file}`'s existing handling exactly: written by
    `SVC_USER`, chmod `0o644` so `RUN_USER`'s tmux pane can read it, thrown
    away in `agent_run()`'s existing rundir cleanup) — Codex's own
    `--output-schema <FILE>`.

  Presence of `HEADLESS_SCHEMA_FLAG` at all is what makes an engine
  auto-detect as **tier 2** (schema-constrained) rather than **tier 3**
  (prose-parse) — see `HEADLESS_LEAD_FORMAT` below for the explicit
  override. `HEADLESS_CMD` must itself contain a `{schema}` token
  somewhere (this is `HEADLESS_CMD`'s OWN insertion point for the whole
  resolved flag+value fragment — a separate thing from which placeholder
  `HEADLESS_SCHEMA_FLAG` uses internally) for this to have any effect; when
  `agent_run()` is called without `schema=`, that token is substituted with
  the empty string, same as `{resume}`'s own first-turn behavior.

  **A `HEADLESS_SCHEMA_FLAG` declaring neither `{schema}` nor
  `{schema_file}` is a configuration error**, surfaced at **roster-build
  time** (`roster()`'s own `schema_flag_error` field, and `team-start`'s
  own early rejection of a misconfigured `--lead`) rather than only once
  the first real tier-2 lead call fails.
- **`HEADLESS_LEAD_FORMAT`** — explicit override for the auto-detected
  tier: `schema` forces tier 2 even without a `HEADLESS_SCHEMA_FLAG`;
  `prose` forces tier 3 even *with* one. Any other value (or its absence)
  falls through to auto-detection based on whether
  `HEADLESS_SCHEMA_FLAG` is set. No enum is enforced at parse time — there
  is nothing to validate beyond "is it a non-empty string", since an
  unrecognized value simply behaves the same as leaving the key unset.

**Real, verified finding: Claude Code's own `--json-schema` flag does NOT
take a file path.** It takes the schema's JSON text INLINE (`claude -p
--help`'s own example: `--json-schema {"type":"object",...}`), not a
`--json-schema <path-to-file>` form the way Codex's `--output-schema
<FILE>` does. This was discovered by actually running a real, logged-in
`claude` CLI as a tier-2 lead during this sub-spec's own build — the
original, single-placeholder design (`{schema}` meaning "a file path",
mirroring `{prompt_file}` too literally) failed live with `Error:
--json-schema is not valid JSON: JSON Parse error: Unrecognized token
'/'`. The failure itself degraded correctly (two malformed retries, then a
clean `ask_user` escalation with the raw error text included, per
`docs/spec.md` §9) — no crash — but tier 2 was not actually usable for
`claude.engine` as originally configured. Corrected to the two-placeholder
design above; see `docs/implementation.md` for both the original failing
run and the corrected, reverified one.

**Verification status of the three shipped engines' `HEADLESS_*` keys** (see
`docs/implementation.md` for the full writeup): `claude.engine` is verified
end to end, including `--resume`, against a real logged-in `claude` CLI.
`codex.engine`'s plumbing (process spawn, NDJSON capture, real exit code) is
confirmed against a real `codex` CLI, but that CLI was not logged in during
verification, so a *successful* turn and the `resume <SESSION_ID>` syntax
specifically remain unconfirmed. `aider.engine` is **unverified** — `aider`
was not installed in the environment this sub-spec was built in; believed
correct per aider's own documented CLI flags as of 2026-08-13, not yet run
end-to-end.

**Verification status of the lead-adapter tiers (backlog item 6c):** tier 1
(Ollama `/v1/chat/completions`) is verified end to end against a real
remote `qwen3:8b`, including a real `delegate` handoff to a real logged-in
`claude` teammate. Tier 2 is verified end to end against the same real,
logged-in `claude` CLI, using the corrected inline `{schema}` form — see
`docs/implementation.md` for the exact command/output. `codex` remains
unauthenticated in this environment (same limitation 6a already disclosed
for `codex.engine`), so tier 2's `{schema_file}` (path) form is verified
only against a test-authored stand-in engine, not the real `codex` CLI.
Tier 3 is verified via shell-script stand-ins
(`tests/fixtures/headless/tier3_stub_*.sh`) driving a real `team_run()` end
to end through real tmux — `aider` itself remains **UNVERIFIED**, same
disclosure as above.
