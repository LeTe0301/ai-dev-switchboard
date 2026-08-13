# Headless stream fixtures (backlog item 6a)

Provenance of each fixture, per `docs/ADDING_AN_ENGINE.md`'s standing rule
("verified by actually running it, not guessed") — see
`docs/implementation.md` for the full verification writeup.

- **`claude_stream.jsonl`** — REAL. Captured verbatim from
  `claude -p --output-format stream-json --verbose "Remember this secret
  number: 42. Reply with just: ok"`, run as `RUN_USER` (`dev`), logged in,
  exit code 0. Exercises `system` (including `hook_started`/`hook_response`
  subtypes), `assistant` (`thinking` + `text` content blocks), and the final
  `result` line.

- **`codex_stream_authfail.jsonl`** — REAL. Captured verbatim from
  `codex exec --json --skip-git-repo-check "say hi"`, run as `RUN_USER`,
  **not logged in** (401 from `api.openai.com`), exit code 1. Exercises
  `thread.started` (the session-id-bearing event), `turn.started`,
  `error`, and `turn.failed` — a genuine nonzero-exit engine failure, not a
  synthetic one.

- **`codex_stream_success.jsonl`** — SYNTHESIZED, not independently
  observed. `codex` was installed but not authenticated in the environment
  this sub-spec was built in (see docs/implementation.md), so a real
  successful turn's `item.started`/`item.completed` (`agent_message`) /
  `turn.completed` shape could not be captured live. Built from
  `docs/story.md` §2.1's documented Codex event types plus the one
  `item.completed` shape actually observed inside the real auth-failure
  transcript above (`item.type == "error"`, field name `message` not
  `text`) to infer the sibling `agent_message` item's field name (`text`).

- **`aider_output.txt`** — SYNTHESIZED, not independently observed. `aider`
  was not installed in the environment this sub-spec was built in. Plausible
  plain-stdout shape for `--message-file ... --yes-always`; not a real
  capture.

- **`tier3_stub_two_step.sh`**, **`tier3_stub_no_fence.sh`**,
  **`tier3_stub_malformed_fence.sh`** — backlog item 6c (`docs/spec.md` §8).
  Executable shell-script stand-ins for a real tier-3 (prose-parse) lead,
  since `aider` is not installed here either. Each reads the round's own
  assembled prompt (arg mode, `$1`) and replies deterministically: the
  two-step fixture emits a well-formed `fact_check` call on "Round 1 of..."
  and a well-formed `finish` call on any later round (drives a real,
  complete `team_run()` end to end); the other two always reply with no
  fence at all, or a fence whose contents don't parse as JSON, respectively
  — both exercise the shared malformed-retry-then-`ask_user`-escalation
  path. Wired through a scratch `.engine` file in
  `tests/test_teams_lead.py`, same real-tmux/no-real-engine-CLI technique
  `tests/test_teams_headless.py`'s own `RealTmuxHeadlessTests` already
  established. `aider.engine`'s own real-CLI tier-3 status remains
  UNVERIFIED (see `docs/implementation.md`) — these fixtures verify the
  lead LOOP's tier-3 code paths, not `aider` itself.
