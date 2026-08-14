# Spec: Roster & composition UI (sub-spec 6e)

## Summary
Add a lead/teammate picker to the existing per-project "team" control so a
user chooses who leads and who's on the team (instead of the hardcoded
`teams.default_team_composition()` the current `/team/start` route always
uses), shows each roster member's lead-adapter tier and which grounding
files were found for the project, and remembers the last composition per
project across service restarts.

## Goals
- Show every roster member (`teams.roster()`: `engines.d` entries +
  the configured Ollama model) with its lead-adapter tier, before a team is
  started.
- Let the user pick lead + teammates per project, from that roster, instead
  of always using `default_team_composition()`'s automatic pick.
- Every roster member must be pickable as lead, including tier 3 — with a
  plain-language reliability caveat shown for tier 3, never a block.
- Show which of the four grounding files (`docs/ARCHITECTURE.md`,
  `docs/BACKLOG.md`, `CLAUDE.md`/`AGENTS.md`, `README.md`) were actually
  found for this project before starting, so an absent file is visible, not
  silently absorbed into the digest.
- Persist the last composition used per project so it survives a service
  restart and pre-populates the picker next time.
- Reject an empty teammate list, a duplicate teammate, or a composition
  naming an engine/model that isn't a real roster member — each with a
  specific, actionable message, both client-side (fast feedback) and
  server-side (source of truth).

## Non-goals
- No changes to the lead loop itself, its four tools, or any adapter tier
  (6c, unchanged). This is a picker for who's on a team, not how the team
  behaves once running.
- No overwatch feed or escalation inbox — that's 6f, next in the story.
- No per-teammate `--allowedTools`/`--sandbox` scoping UI. `docs/story.md`
  §7 deferred this exact question to 6e; resolving it now: scoping stays
  where it already lives today — entirely in each engine's own
  `HEADLESS_CMD` in `engines.d/*.engine` (`docs/ADDING_AN_ENGINE.md`), with
  no per-team or per-composition override. Introducing a real per-team
  scoping mechanism would be a new backend concept (nothing like it exists
  today — `HEADLESS_CMD` is static per engine file), which contradicts this
  sub-spec's own "no new backend concepts" framing in `docs/story.md` §5.
  Flagged under "Open questions" in case the user wants it pulled forward.
- No editing a composition while that project's team is already running.
  The picker only replaces the current idle-state UI
  (`teamRow()`'s `!team || team.status === 'idle'` branch) — a running/
  blocked/finished/error row is unchanged (status + Stop button).
- No new dedicated "settings page" / route. This codebase has no
  multi-page concept — `render_page()` serves one static shell and
  everything is rendered client-side from `/status` (see "Background"
  below). "Settings screen" in `docs/story.md`'s wording is read as "a
  roster view, wherever the ux-designer places it in the existing single
  page" (most likely inside the per-project idle-state row, since roster
  membership and grounding are only actionable in the context of *a*
  project's picker) — not a literal second page. Flagged under "Open
  questions" as a UX-shape call, not mine to make.
- No change to `default_team_composition()`'s own selection rules — it
  remains exactly what a project with no saved composition yet falls back
  to (see "Proposed approach").

## Background / current state
- `app/teams.py`'s `roster()` (line ~1777) already returns
  `[{name, kind: "engine"|"ollama", label, tier: 1|2|3, delegate_capable,
  schema_flag_error}]`, live off `load_engines()` (no cache) plus
  `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL`. Nothing in `app.py` calls it yet —
  there is no GET route exposing it to the browser at all today.
- `default_team_composition()` (line ~1809) picks a lead (Ollama first, else
  the first tier-2 engine with no `schema_flag_error`, else refuse) and
  members (every other headless-eligible engine). It is the **only** thing
  `POST /projects/<name>/team/start` (`app/app.py` line ~3387) uses today —
  there is no way for a user to pick a different composition.
- `launch_team(workdir, task, lead, members, max_rounds=None)`
  (`app/teams.py` line ~3220) already takes an arbitrary `lead`/`members`
  pair — it has no dependency on `default_team_composition()` specifically.
  The CLI's `team-start`/`team-launch` subcommands (`_cli_team_start()`,
  line ~3643) already build an explicit composition from `--lead`/
  `--lead-ollama`/`--members` flags and validate it inline (unknown/non-
  headless engine, misconfigured tier-2 schema flag). That validation is
  duplicated across the two CLI subcommands by design (`docs/spec.md` for
  6d part 1: "copy-pasted validation … reused, not reinvented") — this spec
  adds a third, HTTP-facing call site and does not touch the CLI ones.
- `load_grounding(workdir)` (`app/teams.py` line ~1526) already returns
  `{workdir, loaded_at, files: [{label, path, relpath, headings, content,
  byte_count}], skipped: [...], digest, empty}` — discovery is already
  built, just not reachable from the browser.
- The whole app is **one page**: `render_page()` (`app/app.py` line ~3067)
  serves a static shell; every row (including the per-project "team"
  control, `teamRow()` in the page's inline JS, line ~2045) is rendered
  client-side off the polled `GET /status` response
  (`app/app.py` line ~3202), which already attaches an always-present
  `inst.team = {status, run_id}` per project (added in 6d part 2a).
  `POST /projects/<name>/team/start` currently takes only `{task}` in its
  body; `POST /projects/<name>/team/stop` takes no body. Every mutating
  route is gated by the shared TOTP-once-per-session check at the top of
  `do_POST()` (line ~3313) — nothing route-specific needed for that.
- Precedent for a small, app-written, atomically-updated per-something JSON
  cache already exists: `_load_desc_cache()`/`_save_desc_cache()`
  (`app/app.py` line ~436), a `json.load`/`json.dump` pair with a
  `.tmp` + `os.replace()` write, at a path from an env var
  (`DESC_CACHE_FILE`, default under `/var/lib/ai-dev-switchboard/`). This
  spec's composition cache follows that exact shape rather than inventing a
  new persistence style.

## Proposed approach

### 1. `app/teams.py` — composition validation + persistence (no new
   concepts, built entirely on functions that already exist)

- `validate_composition(lead: dict, members: list) -> str | None` — returns
  `None` if valid, else a human-readable reason (same style as
  `default_team_composition()`'s own error strings). Built on `roster()`
  (called once internally), not on `load_engines()` directly:
  - `lead` must be `{"kind": "engine"|"ollama", "name": str}` matching a
    real `roster()` entry by `(kind, name)`.
  - If `lead["kind"] == "engine"`, that roster entry's `schema_flag_error`
    must be falsy **if its tier is 2** (a tier-3 lead has no schema flag to
    be wrong about) — same protection `_cli_team_start()` already gives,
    now shared.
  - `members` must be non-empty, with no duplicate names.
  - Every name in `members` must match a roster entry with
    `kind == "engine"` and `delegate_capable == True` (this already
    excludes the Ollama entry — it's never `delegate_capable`).
  - `lead`'s name must not also appear in `members` (see "Open questions" —
    this mirrors `default_team_composition()`'s own exclusion of its picked
    engine-lead from `members`, applied here as a rule rather than an
    accident of how the default is built).
- Composition persistence, same shape as `_load_desc_cache()`/
  `_save_desc_cache()`:
  - `_compositions_path()` → `os.path.join(TEAM_STATE_DIR,
    "compositions.json")` (no new env var — reuses the directory
    `TEAM_STATE_DIR` already names, created the same way
    `_run_dir()`/other `TEAM_STATE_DIR`-relative paths already ensure it
    exists).
  - `load_compositions() -> dict` — `{project_name: {"lead": {...},
    "members": [...], "saved_at": iso}}`. `try/except (OSError,
    ValueError): return {}` on a missing/corrupt file, same as
    `_load_desc_cache()`.
  - `save_composition(project_name: str, lead: dict, members: list) ->
    None` — upserts one entry, writes via `.tmp` + `os.replace()`. Stores
    only `kind`+`name` for `lead` (never `tier`/`schema_flag_error` —
    those are always re-derived live from `roster()` at read time, so a
    later `engines.d` edit can't leave a stale tier displayed or trusted).
- `default_team_composition()` is unchanged — it remains exactly what a
  project with no saved composition yet falls back to.

### 2. `app/app.py` — routes

- **`GET /status`** (existing route, extended, not new):
  - Top-level: add `"roster": teams.roster()` — global, not per-project,
    computed once per poll next to the existing `engines = load_engines()`
    call. (`roster()` re-reads `load_engines()` itself; this duplicates one
    directory scan per poll, same accepted cost `default_team_composition()`
    already carries by calling `roster()` internally — not worth
    threading a pre-loaded `engines` dict through for.)
  - Per-instance `inst["team"]`: add a `"composition"` key —
    `teams.load_compositions().get(name)` if present, else
    `teams.default_team_composition()`'s result if `ok`, else `None`. This
    is what the picker pre-selects; only meaningful when `status == "idle"`
    but cheap enough to compute unconditionally (consistent with `team`'s
    existing "always present" treatment).
- **`GET /projects/<name>/team/grounding`** (new): 404 if `name` isn't a
  known project (`instance_names()`). Calls
  `teams.load_grounding(os.path.join(PROJECTS_DIR, name))` and returns
  **only** `{"files": [{"label", "relpath", "byte_count"}, ...],
  "skipped": [...]}` — never `content`/`digest`/`headings`, which would
  ship a project's full doc text to the browser for what's meant to be a
  before-you-start discovery summary, not a viewer. Read-only, no TOTP
  needed (matches `/status`'s own gating — `_authed()` only).
- **`POST /projects/<name>/team/start`** (existing route, extended): body
  gains two optional keys, `lead`/`members`. If both are present:
  1. `err = teams.validate_composition(body["lead"], body["members"])`; if
     set, `400 {"error": err}`.
  2. `teams.save_composition(name, body["lead"], body["members"])` — saved
     on successful validation, **independent of whether `launch_team()`
     itself later succeeds** (a dirty-tree/session-collision failure below
     shouldn't discard the user's picker choice).
  3. Use `body["lead"]`/`body["members"]` in place of
     `default_team_composition()`'s result for the `launch_team()` call.

  If neither key is present, behavior is **byte-for-byte unchanged** from
  today: `default_team_composition()` is used, nothing is read from or
  written to `compositions.json`. This keeps the route backward compatible
  and covers the very first start for a project with no saved composition
  and no picker interaction yet (e.g. a stale client, or a future non-UI
  caller).

### 3. Frontend — picker (ux-designer's call on exact layout; functional
   requirements only)

- Replace the current idle-state textarea-only row (`teamRow()`'s
  `!team || team.status === 'idle'` branch, `app/app.py` line ~2047) with:
  task text (unchanged), a lead picker (one of the roster's members, tier
  shown as a chip, tier 3 additionally captioned with a plain-language
  reliability note per `docs/story.md` §4.2's own table), a teammate picker
  (checkboxes over the roster, excluding whichever entry is currently
  selected as lead, `delegate_capable` entries only — the Ollama entry, if
  present, is never offered as a teammate checkbox), and the grounding
  summary (fetched from the new `GET .../team/grounding` route — which of
  the four files were found vs. not, e.g. reusing `skipped`'s reasons).
- Pre-select from `inst.team.composition` (the `/status` addition above) —
  the saved composition if one exists, else `default_team_composition()`'s
  pick, else nothing (see below).
- If `inst.team.composition` is `None` (no roster member at all can lead —
  `default_team_composition()`'s own "nothing usable" refusal, no saved
  composition either), render its `error` text in place of the picker and
  omit the Start button entirely, rather than rendering an empty/broken
  picker.
- Client-side validation before submit, mirroring `validate_composition()`'s
  rules (empty members, duplicate — structurally impossible via checkboxes
  so no dedicated UI for it, lead-also-a-teammate) — same inline
  `team-msg`/disabled-button pattern `doTeamStart()` already uses for the
  empty-task case. This is a fast-feedback mirror of the server check, not
  a replacement for it — the server call in `POST .../team/start` remains
  the source of truth.
- `doTeamStart(name)` (line ~2268) is extended to also read the picker's
  current lead/members selection and include them in the POST body.

## Affected areas
- `app/teams.py` — `validate_composition()`, `_compositions_path()`,
  `load_compositions()`, `save_composition()` (all new); everything else
  (`roster()`, `default_team_composition()`, `load_grounding()`,
  `launch_team()`) is reused unmodified.
- `app/app.py` — `GET /status` (two additive fields), new
  `GET /projects/<name>/team/grounding` route, `POST
  /projects/<name>/team/start` (two optional body keys, else unchanged),
  `teamRow()`/`doTeamStart()` and surrounding inline JS/CSS (picker
  markup + styling — ux-designer's design.md governs the specifics).
- No schema/migration changes (`compositions.json` is a new file, not a
  new table). No changes to `config/switchboard.env.example` — no new env
  var (composition storage reuses `TEAM_STATE_DIR`).
- Tests: extend `tests/test_team_routes.py` (new route, extended
  `/team/start` body handling, `/status` additions), add composition/
  validation coverage (new tests in `app/teams.py`'s existing test
  conventions — likely a new `tests/test_teams_composition.py` alongside
  the existing `test_teams_grounding.py`/`test_teams_lead.py` split by
  concern), extend `tests/test_team_frontend.js` for the picker's render/
  validation logic.

## Edge cases
- **Empty members** — rejected by `validate_composition()` with a specific
  message; mirrors `default_team_composition()`'s own existing empty-
  members refusal text style.
- **Duplicate teammate name** in a submitted `members` list — rejected
  explicitly by `validate_composition()` rather than left to surface as a
  confusing `_create_worktree()` "path already exists" failure deep inside
  `launch_team()`.
- **Lead's name also present in `members`** — rejected (see "Open
  questions" for the assumption this encodes).
- **A name in `lead`/`members` that isn't a real roster entry** (stale
  saved composition after an `engines.d` file was edited/removed since it
  was saved, or a hand-crafted request) — rejected with a specific message
  naming the unknown entry; **never silently substituted** with
  `default_team_composition()`'s pick instead — a user who picked X should
  either get X or a clear reason they can't, not a different Y they didn't
  choose.
- **Ollama named as `lead` but `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` are no
  longer both set** (unset since the composition was saved) — same
  rejection path as above; `roster()` simply won't contain an `"ollama"`
  entry to match against.
- **Tier-3 lead** — allowed, not blocked (goal, restated as an edge case
  because it's the one place a naive validator might be tempted to add a
  tier check that doesn't belong there — `docs/story.md` §4.2 is explicit
  that tier 3 "is a real option, not a token one").
- **No usable roster member at all** (`default_team_composition()`'s own
  "nothing usable" refusal, and no saved composition to fall back to
  either) — picker area shows the refusal text, Start button omitted, not
  a broken/empty picker (see "Proposed approach" §3).
- **`compositions.json` missing or corrupt** — `load_compositions()`
  returns `{}`, same as a project with no saved composition; never a 500.
- **Two browser tabs saving different compositions for the same project at
  ~the same time** — last `os.replace()` wins, same accepted precedent
  `_save_desc_cache()` already carries; no locking added, none exists for
  the analogous case today.
- **A running/blocked/finished/error team** — the picker is not rendered at
  all (only the idle branch changes); `inst.team.composition` is still
  computed and returned by `/status` regardless of status (cheap, and the
  frontend simply doesn't render it in non-idle branches).
- **Grounding route called for a project with none of the four files** —
  `load_grounding()` already returns `files: [], empty: True`;
  `GET .../team/grounding` returns `{"files": [], "skipped": [...]}` and
  the picker shows "no grounding files found" rather than an error.

## Acceptance criteria
- [ ] Given a project with `engines.d` entries and (optionally) a
      configured Ollama model, when the picker is opened, then every
      roster member (`GET /status`'s new `roster` field) is listed with its
      tier, live off `engines.d` (re-read per `/status` poll, no cache —
      an `engines.d` edit is reflected on the next poll without a restart).
- [ ] Given the picker, when a tier-3 member is selected as lead, then the
      selection succeeds (never blocked) and a plain-language note that its
      reliability is lower is shown alongside it.
- [ ] Given a project, when the picker is opened before a team is started,
      then which of the four grounding files were found is shown (an
      absent `docs/ARCHITECTURE.md`, e.g., is visible, not silently
      skipped) via `GET /projects/<name>/team/grounding`.
- [ ] Given a composition submitted via `POST /projects/<name>/team/start`,
      when validation passes, then it is persisted to
      `TEAM_STATE_DIR/compositions.json` and, after a service restart, the
      next `GET /status` for that project reflects it in
      `inst.team.composition` (verified by restarting the process in a
      test, not just re-calling the function in-process).
- [ ] Given `POST /projects/<name>/team/start` with `members: []` (or
      omitted-but-empty after client validation is bypassed), when the
      server validates it, then it is rejected with a clear reason and no
      worktrees/session are created (`launch_team()` never called).
- [ ] Given `POST /projects/<name>/team/start` with a duplicate name in
      `members`, then it is rejected with a specific "duplicate teammate"
      message, not a generic worktree-creation failure.
- [ ] Given `POST /projects/<name>/team/start` with `lead.name` also
      present in `members`, then it is rejected with a specific message.
- [ ] Given `POST /projects/<name>/team/start` with a `lead`/`members` name
      that doesn't match any current `roster()` entry (including a stale
      saved composition after the referenced engine was removed from
      `engines.d`), then it is rejected with a message naming the specific
      unknown entry, and nothing is silently substituted.
- [ ] Given no `lead`/`members` in the `POST .../team/start` body, then
      behavior is unchanged from before this spec
      (`default_team_composition()` is used; `compositions.json` is
      neither read into the decision nor written).
- [ ] Given a project whose team is currently running/blocked/finished/
      error, then the picker is not shown (existing non-idle row is
      unaffected).

## Open questions
- **Lead's name also disallowed in `members`** — proceeding under this
  assumption (mirrors `default_team_composition()`'s own exclusion of its
  picked engine-lead from `members`). A real use case exists for the
  opposite (delegating a sub-task to a fresh worktree of the *same* engine
  that's leading, from the main workdir) — if that's wanted, it's a
  one-line relaxation in `validate_composition()`, but I'm not assuming it
  without a decision, since it changes what "delegate to X" can mean while
  X is simultaneously the lead.
- **"Settings screen" wording vs. this app's one-page architecture** —
  proceeding under the assumption that the roster listing lives inside the
  per-project idle-state row (where the picker already needs it), not a
  separate page/route — see "Non-goals". Flag for the ux-designer to
  confirm rather than something I should lock down here.
- **No separate "save composition without starting" action** — a
  composition is only ever saved as a side effect of a (validated) `POST
  .../team/start` call, not via its own save button/route. This keeps the
  route surface minimal per `docs/story.md`'s "no new backend concepts"
  framing for 6e. If a user wants to configure and save a composition
  ahead of actually starting a team, that's not covered here — flagging in
  case that's wanted; the fix is a small additive route
  (`POST .../team/composition`) reusing `validate_composition()`/
  `save_composition()` directly, not a redesign.
- **Per-teammate `--allowedTools`/`--sandbox` scoping** (`docs/story.md`
  §7) — resolved as out of scope for 6e (see "Non-goals"); flagging again
  here in case the user wants it pulled into this cycle rather than left
  for later, since the story explicitly named 6e as where this question
  would be revisited.

## Risk / rollback notes
- Every change here is additive: two new `/status` fields, one new GET
  route, two new optional POST body keys with unchanged behavior when
  absent, one new JSON file under `TEAM_STATE_DIR`. Reverting is deleting
  `TEAM_STATE_DIR/compositions.json` (worst case: every project falls back
  to `default_team_composition()`, exactly today's behavior) plus a normal
  git revert — no migration, no destructive step.
- The one behavioral risk worth calling out: if `validate_composition()`'s
  rules (specifically the lead-not-in-members rule) turn out to be wrong
  per the open question above, tightening it later is a compatible change
  (rejects strictly less); loosening it later is also compatible (rejects
  strictly less) — neither direction requires a data migration since
  nothing invalid can have been persisted under the stricter rule.
