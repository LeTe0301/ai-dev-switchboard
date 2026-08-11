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
