# Spec: `install.sh --with-ollama` — link an existing Ollama (sub-spec 6d, part 2b of 2)

## Why this is a small, self-contained cycle

Part 2a's own "Split rationale" separated this out: `--with-ollama` is
bash-only, has **zero code dependency** on part 2a's routes/threading, and
only ever writes two env vars that `default_team_composition()` already
reads at request time. The two cycles compose across that existing env-var
contract and share no other surface. This repo has direct precedent for
splitting exactly this shape apart — `2c part 2a` (application code) vs.
`2c part 2b` (`install.sh --with-deploy-target`, an installer flag) were
two separate cycles for the identical reason.

This spec was written by the orchestrator rather than by a full
product-manager pass, because every product decision it depends on is
already settled (see below) and the design is a direct application of an
existing precedent block. If anything below turns out to require a real
scoping or architecture judgment call, that is a signal to stop and run
the full pass rather than to improvise.

## Settled before this cycle — do not reopen

- **This LINKS a remote Ollama. It never installs one.** Explicit user
  instruction: their Ollama already runs on ct102 and must not live in the
  switchboard container. Also forced by hardware — `docs/story.md` §2.5
  records the standard container has ~715MB free RAM with swap exhausted,
  and no tool-capable model fits locally. **Nothing in this cycle installs
  a model, a runtime, a container, or a systemd unit.**
- **Off by default**, like every other `--with-*` flag.
- **Refuses to write config it cannot verify** — same "fail the start,
  don't write config that fails later" discipline `launch_team()` and
  `default_team_composition()` already apply.

## Background — the precedent to follow

- `install.sh:631-704`, the `--with-deploy-target` block: the closest
  existing shape. An optional flag block using `prompt()` (`:92`), the
  idempotent `set_env()` upsert (`:102`), a dependency check that **skips
  only its own block rather than aborting the whole run** (the `rrsync`
  check at `:644`, itself following the ttyd-arch-not-found precedent),
  and a final printed summary.
- Flag plumbing: a `WITH_OLLAMA=0` default alongside `:67-68`, a
  `--with-ollama) WITH_OLLAMA=1 ;;` case alongside `:72-76`, and a usage
  line alongside `:15-31`.
- `ENV_FILE="$CONFIG_DIR/switchboard.env"` is defined at `:224`, so this
  block must sit **after** that line.
- `curl` (`:146`) and `python3` (`:146`) are both installed
  **unconditionally**, so both are available without a new dependency.
- `TEAM_LLM_BASE_URL` / `TEAM_LLM_MODEL` are already documented in
  `config/switchboard.env.example` (added in 6c) — this cycle writes them,
  it does not introduce them.

## Proposed approach

### 1. Prompts

```
OLLAMA_BASE_URL_INPUT=$(prompt "Ollama endpoint URL (OpenAI-compatible, e.g. an existing remote Ollama's /v1)" "http://127.0.0.1:11434/v1")
OLLAMA_MODEL_INPUT=$(prompt "Model name" "qwen3:8b")
```

The default is shown for shape only; a remote endpoint is the expected
answer, and the printed summary must say so rather than implying a local
install happened.

### 2. Validation — against the URL the application will actually use

Validate with `GET "$OLLAMA_BASE_URL_INPUT/models"`, **not** Ollama's
native `/api/tags`. Rationale: `TEAM_LLM_BASE_URL` is the
OpenAI-compatible base the lead adapter really calls, so validating that
exact base is what proves the config works. `/api/tags` lives outside the
`/v1` prefix and would validate a *different* URL than the one being
written — the class of mistake 6c's schema-flag defect already cost this
story once.

Requirements:
- **Bounded**: `curl --max-time` (10s connect+total is ample for a `/models`
  listing). The installer must never hang on an unreachable host.
- **Parse with `python3`, not `grep`.** `python3` is unconditionally
  installed. A naive `grep "$MODEL"` false-positives on substrings
  (`qwen3:8` matching `qwen3:8b`) and on the model appearing anywhere in
  unrelated JSON. Parse `{"data":[{"id":...}]}` and compare `id` values
  exactly.
- Trailing slashes in the supplied URL must not produce `//models`.
  Normalise before use.

Three distinct outcomes, three distinct messages:
1. **Endpoint unreachable / non-JSON / HTTP error** → skip the block,
   write nothing, explain that no `TEAM_LLM_*` was written and the team
   lead will fall back to an `engines.d` tier-2 engine.
2. **Endpoint reachable, model absent** → skip the block, write nothing,
   and **list the model ids that ARE available** so the operator can fix
   the typo without a second round-trip.
3. **Both fine** → `set_env "$ENV_FILE" TEAM_LLM_BASE_URL "..."` and
   `set_env "$ENV_FILE" TEAM_LLM_MODEL "..."`, then print a summary that
   states plainly that nothing was installed locally and the endpoint is
   remote.

Failure never aborts the whole `install.sh` run — it skips this block
only, per the `rrsync` precedent.

### 3. Idempotence

A blank answer must leave any previous value untouched rather than
clearing it, matching the deploy-target block's
`[ -n "$X" ] && set_env ...` idiom. Re-running with the same answers is a
no-op. Re-running with a new model against the same endpoint updates only
`TEAM_LLM_MODEL`.

## Non-goals

- Installing Ollama, a model, a container, or a systemd unit. Settled.
- Any change to `app/app.py` or `app/teams.py`. This cycle is
  `install.sh` + tests + docs only. If a code change appears necessary,
  that is a spec defect — report it rather than making it.
- Any UI. There is no UI surface in this cycle.
- Health-checking the endpoint at runtime, on a timer, or at team start.
  `launch_team()` deliberately never dials it (part 1, unchanged).
- Authentication to the Ollama endpoint (tokens, TLS client certs). Not
  needed for the settled deployment shape; adding it speculatively is out
  of scope.

## Acceptance criteria

Each must be verifiable by running something, not by reading the diff.

- [ ] `--with-ollama` appears in the usage block, defaults to off, and a
      run **without** the flag writes no `TEAM_LLM_*` keys at all.
- [ ] Against a **stubbed local HTTP server** standing in for a reachable
      Ollama (the established block-extraction technique — real `bash -c`,
      no VM, as `TAIGA_ENV`/`GITEA_ENV` setup tests already do): a run with
      a model the stub reports writes both `TEAM_LLM_BASE_URL` and
      `TEAM_LLM_MODEL` into the env file with exactly the supplied values.
- [ ] **Unreachable endpoint** → neither key is written. Verified by
      inspecting the resulting env file, not just the exit status or
      stdout. The run as a whole still succeeds.
- [ ] **Reachable endpoint, absent model** → neither key is written, and
      the output lists the available model ids.
- [ ] **Bounded**: an endpoint that accepts the connection then never
      responds does not hang the installer. Assert an upper bound on
      elapsed time, against a stub that deliberately stalls.
- [ ] **Substring safety**: a stub advertising only `qwen3:8b` must
      **reject** the model name `qwen3:8`. This is the specific defect a
      `grep`-based check would ship.
- [ ] **Trailing slash**: a base URL supplied as
      `http://host:11434/v1/` validates and writes correctly — no `//models`.
- [ ] **Idempotent re-run**: blank answers leave existing `TEAM_LLM_*`
      values untouched; a changed model updates only `TEAM_LLM_MODEL`.
- [ ] Nothing local is installed: no new package, no systemd unit, no
      container, no model pull. Assert the block issues no such command.
- [ ] The full suite still passes (baseline **674**) and the four Node
      suites still pass (17/9/15/8).

## Edge cases worth stating

- **An endpoint that returns 200 with HTML** (a proxy login page, a
  captive portal) must be treated as unreachable-for-this-purpose, not as
  a valid empty model list. Parse failure is a failure.
- **A `/models` response with an empty `data` array** is reachable but has
  no models — outcome 2, listing "none".
- **The operator supplies a base URL without `/v1`.** Do not silently
  append it — validate what was given and fail with a message naming the
  likely cause, since silently rewriting an operator's URL is how the
  wrong endpoint gets validated.

## Test-isolation requirement (new, learned this session)

The stub HTTP server must bind an **ephemeral port** (`:0`, then read the
assigned port), never a fixed one. `docs/BACKLOG.md` item 9 records that
this suite already has tests contending on singleton host resources, which
produced several cycles of misattributed failures. Do not add another.

Likewise, any temp dirs must be per-process, and the test must not write
to `$CONFIG_DIR` or any real path outside its own fixture.

## Risk / rollback

Everything is inside one new `if [ "$WITH_OLLAMA" -eq 1 ]` block plus flag
plumbing. With the flag absent the diff is inert. Rollback is deleting the
block. No production Python changes, so no runtime risk to an existing
install; the worst failure mode is an install-time message.
