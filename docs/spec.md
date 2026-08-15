# Spec: Item 21 part 1 — grow a running team with an added teammate (backend)

## Summary
Resolve backlog item 21's shape ambiguity as **"grow a running team"**: add
`teams.add_team_member()` (+ a `POST /projects/<name>/team/add-member` route
and a `team-add-member` CLI subcommand) that adds one more teammate engine to
an already-launched, still-live team run — new git worktree, new tmux
dashboard window, and a queued announcement the lead picks up at its next
round boundary — plus a new `TEAM_MAX_MEMBERS` cap enforced both here and at
initial team-start. This is backend-only (no "+" button yet); the UI is a
separate part 2.

## Decision: shape (1), "grow a running team" — not shape (2)

Backlog item 21 named two candidate shapes. Picking shape (1), for reasons
that follow directly from reading the actual code (`app/app.py`, `app/teams.py`):

- **A project is genuinely capped at one non-team engine session today** —
  confirmed directly: `instance_start()` (`app/app.py:2513`) refuses outright
  if `active_engine(name) is not None`, and `_session_urls` (`app/app.py:2483`)
  is keyed by project name alone, one entry per project, full stop. Shape (2)
  ("independent parallel non-team instances") would have to invent a wholly
  new N-sessions-per-project addressing scheme from scratch — a new key shape
  for `_session_urls`-equivalent state, new per-instance tmux session naming,
  a new UI list-of-sessions-per-project (today's UI assumes exactly one
  engine row per project) — none of which exists anywhere in this codebase
  today, for team sessions or otherwise.
- **Team sessions already generalized past one-session-per-project in the
  one specific way shape (1) needs, and no further**: `docs/ARCHITECTURE.md`
  and item 6d's own history describe a team as one tmux session per project
  with **N windows**, one per member, reusing the single-engine
  `engines.d/*.engine` startup/URL machinery per window. That generalization
  is already built, reviewed, and live (`_create_team_session()`,
  `app/teams.py:3693`). Adding one more member to a *running* team is adding
  one more window to an *already-multi-window* session — the natural next
  increment of a pattern that already exists — not a new pattern.
- **The roster the lead can `delegate` to is already re-read fresh from
  `state["members"]` every single round, not snapshotted once at launch.**
  Verified directly: `_call_lead()` (`app/teams.py:2917`) builds `_lead_tools`
  from `state["members"]` on every call, `_system_framing()`
  (`app/teams.py:2322`, called from `team_step()` at `app/teams.py:3024`) does
  the same, and `_validate_lead_action()`'s `agent_not_on_team` check
  (`app/teams.py:3048`) reads `state["members"]` fresh too. This means the
  lead-loop's own core logic needs **zero changes** to support a grown
  roster — it was already written to tolerate `state["members"]` changing
  between rounds; only appending to that list (safely, off the driving
  thread — see below) and standing up the new teammate's window/worktree is
  new work. This is a materially smaller, lower-risk change than shape (2).
- **Item 19 part 1's `human.jsonl` mechanism is directly reusable machinery**
  for the one real hazard here: a request thread (handling the "+" click)
  must never touch `run.json` directly, because the driving thread's own
  end-of-round `_persist(state)` last-writer-wins overwrite would race it
  (`interject()`'s docstring, `app/teams.py:4241`, documents this exact
  hazard and its fix). The same append-only-side-file + drain-at-round-
  boundary pattern item 19 part 1 built for human messages is the correct,
  already-proven fix for "tell the lead a new teammate joined" too (see
  "Proposed approach" below) — this directly answers the backlog's own
  first open question under item 21.

Shape (2) is not being built, now or as a future part of this item — it
would require a fundamentally different, currently-nonexistent
multi-session-per-project architecture with no team/lead framework wrapped
around it at all, at genuinely higher implementation and review cost than
shape (1), for a request ("spawn any amount of AI instances... in the
repos") that reads at least as naturally as "grow the team working on this
repo" as it does "open N unrelated terminals against the same repo."

## Goals
- A human can add one more teammate engine to an **already-running** team
  (status `running`, `blocked_ask_user`, or `blocked_board_write` — the same
  three non-terminal statuses `interject()` already accepts) without
  stopping and restarting the run.
- The new teammate gets its own git worktree (mirrors `launch_team()`'s
  per-member worktree, item 6d part 1's precedent) and its own tmux
  dashboard window in the already-live `team-<project>` session (mirrors
  `_create_team_session()`'s per-member window loop), so it is observable
  and delegate-able exactly the same way an original teammate is.
- The lead is told, at its next round boundary, that a new teammate is
  available — delivered via a new dedicated append-only side-channel file
  (NOT `human.jsonl` — see "Proposed approach" §3 for why these stay
  separate), drained by `team_step()` the same way `human.jsonl` already is.
- A real, configurable ceiling on team size: new `TEAM_MAX_MEMBERS` env var
  (default `6`), enforced both here (growing a running team) and at initial
  team-start (`validate_composition()` for an explicit picker composition;
  `default_team_composition()` truncates its own auto-picked list to the
  cap deterministically rather than refusing) — closing the backlog's own
  "any real ceiling on 'any amount'" open question for the whole feature,
  not just the new growth path.
- CLI parity (`team-add-member`), following every other team mutation's
  existing CLI-first precedent (`team-interject`, `team-resolve`, etc.), so
  this is independently testable/scriptable without any UI.

## Non-goals
- **The "+" button UI itself** — this part ships no new button, no picker,
  no Teams-page change at all. That is part 2 (needs `ux-designer`; see
  "Open questions").
- **Shape (2) (independent, non-team, human-driven parallel instances)** —
  explicitly rejected above, not deferred, not a "part 3."
- **Removing a teammate from a running team** ("shrink," the inverse of this
  feature) — not asked for by the backlog item, not built here.
- **Concurrent/parallel delegation to multiple teammates at once** — unrelated
  to this item; the lead already delegates to one agent per round today, and
  that is completely unchanged by growing the roster it can choose from.
- **Retroactively capping an already-running team that was started before
  `TEAM_MAX_MEMBERS` existed** — the cap is enforced only at team-start
  (already covers unbounded initial composition) and at each individual
  add-member call (covers growth); no migration/trim of any pre-existing
  persisted run.
- **Telling the lead a teammate joined mid-in-flight-tool-call** — same
  non-goal item 19 part 1 already accepted for human interjects: delivery is
  at the next round boundary only, never mid-call.

## Background / current state
- `app/app.py`'s `_session_urls` (`app/app.py:2483`) and `instance_start()`
  (`app/app.py:2513`)/`instance_stop()` (`app/app.py:2539`) implement the
  plain, non-team, one-engine-per-project session — confirmed capped at one
  by `active_engine(name) is not None` in `instance_start()`. Not touched by
  this spec at all; team sessions are a fully separate code path.
- `app/teams.py`'s team machinery (item 6/6c/6d/6f/19): `roster()`
  (`app/teams.py:1946`) lists every headless-eligible `engines.d` entry plus
  the configured Ollama tier-1 model; `default_team_composition()`
  (`app/teams.py:1978`) picks a deterministic default lead+members;
  `validate_composition()` (`app/teams.py:2045`) validates an explicit
  picker-supplied lead+members pair; `launch_team()` (`app/teams.py:3829`)
  creates per-member worktrees, persists a fresh `run.json`
  (`_new_state()`, `app/teams.py:2732`), then creates the tmux session +
  one window per member (`_create_team_session()`, `app/teams.py:3693`).
  `team_step()` (`app/teams.py:2978`) drives one round: first drains
  `human.jsonl` (item 19 part 1) into a non-lead-calling history round if
  anything is queued, else calls the lead via `_call_lead()`
  (`app/teams.py:2917`), which rebuilds tool schema/system framing from
  `state["members"]` fresh every call.
- **No cap exists anywhere today** on team size — `default_team_composition()`
  includes every eligible engine as a member with no limit;
  `validate_composition()` accepts any non-empty, non-duplicate member list.
  This is the backlog's own flagged open question, unresolved until now.
- `docs/ARCHITECTURE.md` documents the "per-project engine session"/"every
  team session" model; team sessions are the one place this project already
  generalized past strictly one-session-per-project, via windows, not via
  additional sessions.

## Proposed approach

### 1. `teams.add_team_member(run_id, agent)` — the core function
New function, `app/teams.py`, placed near `launch_team()`/`stop_team()`.
`{"ok": True, "agent": ..., "worktree": ...}` or `{"ok": False, "error": ...}`,
matching every other team-mutation function's return shape.

Steps, all synchronous on the calling (request) thread — mirrors
`launch_team()`'s own synchronous worktree+window setup, never touches the
driving thread:

1. Load state (`_load_state(run_id)`; `FileNotFoundError` → `{"ok": False,
   "error": f"no such run_id: {run_id}"}`).
2. Status check: reject (same message shape as `interject()`) unless status
   is one of `"running"`, `"blocked_ask_user"`, `"blocked_board_write"`.
3. Roster/validity checks, reusing `roster()` the same way
   `validate_composition()` already does — reject with a clear message if:
   - `agent` is not a `kind="engine"` roster entry (rejects unknown names
     and rejects the Ollama lead entry the same way `validate_composition()`
     already excludes it — an engine's `delegate_capable` is always `True`
     by construction, so no separate check needed there).
   - `agent == state["lead"]["name"]` and `state["lead"]["kind"] ==
     "engine"` ("lead cannot also be a teammate", mirrors
     `validate_composition()`'s identical check).
   - `agent in state["members"]` ("already a teammate on this team").
   - `len(state["members"]) >= TEAM_MAX_MEMBERS` — reject with
     `f"team already has the maximum of {TEAM_MAX_MEMBERS} teammates"`.
4. `_create_worktree(state["workdir"], agent, run_id)` (existing function,
   unchanged) — same rollback-free single-item semantics `launch_team()`'s
   own per-member loop already has; on failure, return its error verbatim
   (nothing to roll back yet — no window, no queued event created).
5. Pre-touch + chmod the new agent's log file exactly like `launch_team()`
   does for its own initial members (`app/teams.py:3896-3899`):
   `open(_agent_log_path(run_id, agent), "a").close()`, `os.chmod(..., 0o644)`.
   Done **before** the window is created, same ordering reason
   `launch_team()`'s own comment gives (no `tail -F` may ever race file
   creation).
6. Create the new tmux window in the **already-live** session:
   `subprocess.run(TMUX + ["new-window", "-t", _team_session_name(state["project_name"]),
   "-n", agent, "bash", "-lc", f"tail -n +1 -F {shlex.quote(log_path)} || sleep infinity"])`
   — byte-for-byte the same per-member window command
   `_create_team_session()`'s own loop already uses
   (`app/teams.py:3822-3825`). If `tmux new-window` fails (session gone —
   team died between the status check and here), call
   `_remove_worktree(state["workdir"], path)` to undo step 4 and return
   `{"ok": False, "error": "team session is no longer running"}`.
7. Append one envelope to a **new** `_membership_log_path(run_id)` file
   (see §3 below) — `{"ts": ..., "agent": agent, "seq": ...,
   "kind": "member_joined", "worktree": path}` — the only thing this
   function ever writes to persisted run state. Never touches `run.json`.
8. Return `{"ok": True, "agent": agent, "worktree": path}`.

Note step 4-6 leave a worktree+window "ahead of" `run.json` for at most one
round (until `team_step()`'s drain picks it up) — this is intentional and
safe: the worktree and window are inert until the lead actually delegates to
this agent, and `state["members"]` (the ONLY thing `_validate_lead_action()`
checks before allowing a `delegate` call to this agent) isn't updated until
the drain runs, so the lead genuinely cannot reach the new agent one instant
early. No race with the driving thread's own in-flight round is possible
either: the driving thread only ever reads `state["members"]` at the top of
each round build, never mid-round.

### 2. `team_step()`'s new drain step
Add a second drain, structured identically to the existing `human.jsonl`
drain at the top of `team_step()` (`app/teams.py:3006-3020`), reusing
`tail_jsonl_events()` unchanged. Order: **membership drain runs before the
human drain** (arbitrary but deterministic — new teammates becoming
available is the more "structural" change of the two to surface first if
both happen to be queued in the same round-poll). Each drained
`member_joined` event becomes its own history round (consistent with
"several queued human messages, several history rounds" — this item's own
open question about round-budget cost is being answered the same way
item 19 part 1 already answered it for human messages: yes, it costs one
`TEAM_MAX_ROUNDS` slot, an accepted, already-precedented tradeoff, not a new
one):

```python
new_member_events, new_membership_cursor, _ = tail_jsonl_events(
    _membership_log_path(state["run_id"]), state.get("membership_cursor", 0),
    TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND, agent="system")
if new_member_events:
    for ev in new_member_events:
        agent = ev.get("agent")
        if agent and agent not in state["members"]:
            state["members"].append(agent)
            if ev.get("worktree"):
                state.setdefault("worktrees", {})[agent] = ev["worktree"]
            drain_round_n = len(state["history"]) + 1
            _append_history(state, drain_round_n, tool="team_member_joined",
                            args_summary=f'team_member_joined("{agent}")',
                            outcome_summary=f"'{agent}' joined the team and is now available to delegate to",
                            full_result_text=f"New teammate '{agent}' joined the team.",
                            log_path=None, transcript_entries=[])
    state["membership_cursor"] = new_membership_cursor
    _persist(state)
    return state
```
(then the existing `human.jsonl` drain block, unchanged, runs next). The
`agent not in state["members"]` guard makes this idempotent against a
theoretical double-drain (crash/resume replaying from a stale
`membership_cursor` — same defensive shape `_recover_in_progress()`
elsewhere in this module already favors).

`_new_state()` (`app/teams.py:2732`) gets one new additive field:
`"membership_cursor": 0` — same "missing key defaults to 0" precedent
`human_cursor` itself established when it was added; every existing
persisted `run.json` and every existing test reading `state.get(
"membership_cursor", 0)` is unaffected.

### 3. New side-channel file, deliberately separate from `human.jsonl`
New `_membership_log_path(run_id)` → `<rundir>/membership.jsonl`, alongside
`_human_log_path()`/`_transcript_path()`/`_inbox_path()`. **Not** written
into `human.jsonl` itself, even though the drain mechanics are identical,
because:
- Every event source in this module already gets its own file
  (`transcript.jsonl` for the lead, one `<agent>.jsonl` per teammate,
  `human.jsonl` for human chat) — conflating a system-generated
  "roster changed" event into the human-authored file would be the one
  exception to that convention, not a reuse of it.
- `GET .../team/events`'s merged feed (item 6f) renders `human.jsonl`
  entries as `agent="human"`; item 19 part 2's UI gives "human" its own
  filter pill and `.kind-human-message` row style specifically for
  free-text human chat. A `member_joined` event is a different kind of
  thing (system-generated, not human-authored) and should get its own
  `agent="system"` / `kind="member_joined"` envelope shape so a future
  UI pass (part 2 or later) can render/filter it distinctly, not lumped in
  with human chat bubbles.
- The failure mode of conflating them is real, not hypothetical: item 19
  part 2 was built understanding "human filter pill = human.jsonl", and
  silently adding non-human entries to that same file/agent value would be
  a foot-gun for that already-shipped UI.

### 4. `TEAM_MAX_MEMBERS` cap — resolves the backlog's ceiling question
New env var, same declaration style as every other `TEAM_MAX_*`/`TEAM_*`
constant (`app/teams.py`, near `TEAM_MAX_ROUNDS` at line 106):
```python
TEAM_MAX_MEMBERS = int(os.environ.get("TEAM_MAX_MEMBERS", "6"))
```
Document in `config/switchboard.env.example` alongside the existing
commented-out `#TEAM_MAX_ROUNDS=8` line (same section, same `#KEY=default`
commented style), e.g. `#TEAM_MAX_MEMBERS=6`.

Enforced in **three** places, closing the gap for both growth and initial
start, not just the new path:
- `add_team_member()` (§1 above): reject if already at the cap.
- `validate_composition()` (`app/teams.py:2045`, the explicit-picker path):
  add `if len(names) > TEAM_MAX_MEMBERS: return f"too many teammates: {len(names)} "
  f"exceeds the configured maximum of {TEAM_MAX_MEMBERS}"` — a human who
  explicitly picked an oversized roster gets a clear rejection, not a
  silent truncation of their own explicit choice.
- `default_team_composition()` (`app/teams.py:1978`): the auto-picked
  `members` list is **deterministically truncated** to the first
  `TEAM_MAX_MEMBERS` entries (already sorted by name via `roster()`), not
  refused — consistent with this function's own existing character (a
  best-effort deterministic default, never a hard refusal for a situation
  the human didn't explicitly create). Note this in the function's
  docstring so a future reader isn't surprised a large `engines.d` doesn't
  make every engine a default teammate.

Default `6`: a running team's real per-teammate cost is one persistent
`tail -F` tmux window (cheap) plus one git worktree checkout (cheap, disk
only — delegated work is still strictly sequential, one `agent_run()` call
per round, never N teammates running concurrently) plus one more tool
choice + one more line of `_tool_prose()` in the lead's own system prompt
per round (the real, less-cheap cost: context budget). 6 is comfortably
above any realistic current `engines.d` roster size in this project while
still being a real, configured ceiling rather than "any amount."

### 5. `POST /projects/<name>/team/add-member` route
New branch in `app/app.py`'s POST dispatch, alongside `/team/interject`
(`app/app.py:6186-6226`), same shape/order (unknown-project 404 → run_id
resolution via `run_id` in body or `latest_run_for_project()` → status
check delegated to `add_team_member()` itself, not duplicated at the route
layer, since unlike `/team/resolve`'s status check the allowed-status set
here is identical to `interject()`'s own and is more naturally owned by the
one function both entry points call):
```python
elif (parts[0] == "projects" and len(parts) == 4 and parts[2] == "team"
      and parts[3] == "add-member"):
    name = parts[1]
    if name not in instance_names():
        return self._json({"error": "unknown project"}, 404)
    run_id = (body.get("run_id") or "").strip() or None
    if run_id:
        if not teams._RUN_ID_RE.match(run_id):
            return self._json({"error": "no run found for this project"}, 400)
        try:
            state = teams._load_state(run_id)
        except (OSError, ValueError):
            return self._json({"error": "no run found for this project"}, 400)
        if state.get("project_name") != name:
            return self._json({"error": "this run belongs to a different project"}, 400)
    else:
        state = teams.latest_run_for_project(name)
        if state is None:
            return self._json({"error": "no run found for this project"}, 400)
        run_id = state["run_id"]
    agent = (body.get("agent") or "").strip()
    if not agent:
        return self._json({"error": "agent is required"}, 400)
    result = teams.add_team_member(run_id, agent)
    if not result["ok"]:
        return self._json({"error": result["error"]}, 400)
    self._json({"ok": True, "run_id": run_id, "agent": agent})
```
No background thread spun up here (unlike `/team/resolve`/`/team/board-
resolve`) — same reasoning `/team/interject` already documents: this never
resumes a stopped loop, so there is nothing to (re-)drive. Reached through
the same shared TOTP gate every other `/team/*` route already sits behind
— no new gating code.

### 6. CLI: `team-add-member <run_id> <agent>`
Thin wrapper mirroring `_cli_team_interject()` exactly (`app/teams.py:4697`)
— queues/executes and returns, does **not** call `_drive_and_report()`
(same reasoning: there may already be a live driver elsewhere, and
double-driving one `run_id` is the bug class `/team/resolve`'s own
`_team_threads_get` check exists to prevent):
```python
def _cli_team_add_member(args: argparse.Namespace) -> int:
    result = add_team_member(args.run_id, args.agent)
    if not result["ok"]:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    print(f"added '{result['agent']}' to run {args.run_id} (worktree: {result['worktree']})")
    return 0
```
New `argparse` subparser next to `p_team_interject`
(`app/teams.py:4855-4859`):
```python
p_team_add_member = sub.add_parser("team-add-member", help="Add one more "
                                   "teammate engine to an already-running "
                                   "team (backlog item 21 part 1).")
p_team_add_member.add_argument("run_id")
p_team_add_member.add_argument("agent", help="engines.d engine name to add as a new teammate.")
```
Wired into `main()`'s dispatch (`app/teams.py:4896` region) the same way
every other `team-*` subcommand already is.

## Affected areas
- `app/teams.py`: new `TEAM_MAX_MEMBERS` constant; new
  `_membership_log_path()`; new `add_team_member()`; new drain block in
  `team_step()`; new `"membership_cursor": 0` field in `_new_state()`;
  `TEAM_MAX_MEMBERS` check added to `validate_composition()`; truncation
  added to `default_team_composition()` (+ docstring update); new
  `_cli_team_add_member()` + argparse subparser + `main()` dispatch line.
- `app/app.py`: new `POST /projects/<name>/team/add-member` route branch.
- `config/switchboard.env.example`: new commented `#TEAM_MAX_MEMBERS=6`
  line next to `#TEAM_MAX_ROUNDS=8`.
- No data model changes to `run.json`'s existing fields — one new additive
  key (`membership_cursor`), same pattern `human_cursor`/`worktrees`/
  `project_name` already established as safe/backward-compatible additions.
- No changes to `app/app.py`'s frontend JS, `_session_urls`, or the plain
  non-team engine session path — entirely untouched by this spec.

## Edge cases
- **No team running for this project at all** — `latest_run_for_project()`
  returns `None` → `400 "no run found for this project"`, same as
  `/team/resolve`.
- **Team just finished/errored/stopped between page load and the add-member
  click** — `add_team_member()`'s own status check catches this
  (`finished`/`error`/`stopped`/`escalated_max_rounds` all rejected), same
  set `interject()` already rejects, same error-message shape.
- **Requested agent is already a teammate** — rejected with a specific
  message, not a silent no-op or a duplicate window.
- **Requested agent is the current lead** — rejected (mirrors
  `validate_composition()`'s "lead cannot also be a teammate").
- **Requested agent doesn't exist in `engines.d` at all, or is the Ollama
  entry** — rejected via the `roster()` lookup, same message style
  `validate_composition()`'s "Unknown teammate" already uses.
- **At the `TEAM_MAX_MEMBERS` cap already** — rejected with the count and
  configured max named explicitly.
- **Stale worktree leftover at the target path from a previous run** —
  `_create_worktree()`'s own existing "still has uncommitted changes"
  message surfaces unchanged; no new handling needed, this function already
  has it.
- **tmux session died between the status check and the `new-window` call**
  (team crashed/was killed out-of-band mid-request) — worktree created in
  step 4 is rolled back via `_remove_worktree()` before returning the error,
  so no orphaned worktree is left behind for this failure path.
- **Two concurrent add-member calls for the same run** — each independently
  validates against its own freshly-loaded `state["members"]` snapshot; the
  worst case is two different, valid new agents both getting worktrees/
  windows/queued events (no conflict — different worktree paths, different
  window names, independent envelope appends to `membership.jsonl`, same
  "one independent bounded `open(...,'a')` + `write()` under `PIPE_BUF`"
  safety `interject()`'s own docstring already establishes for concurrent
  writers to one append-only file). The narrow exception: two concurrent
  calls for the **same** requested `agent` name — the second one's
  `_create_worktree()` call will see the first's now-existing path and
  return its normal "still has uncommitted changes"-shaped error (a false
  positive in this specific race, since the first call is legitimate, not a
  leftover) — acceptable: the human retries, sees one teammate already
  added, and doesn't re-request the same name. Not fixed further here (same
  narrowness judgment call this project already made for comparable
  first-mover races, e.g. `_create_team_session()`'s own documented
  session-name race).
- **Run is `blocked_ask_user`/`blocked_board_write` when add-member is
  called** — allowed (matches `interject()`'s own accepted statuses); the
  worktree/window are created immediately, but the `membership_cursor`
  drain (and thus the lead actually learning about it) only happens once
  the run resumes and `team_step()` runs again — same "sits queued until
  resumed" behavior `interject()`'s own docstring already documents for a
  human message posted while blocked.

## Acceptance criteria
- [ ] Given a running team (status `running`) for project P with members
      `["codex"]`, when `add_team_member(run_id, "aider")` is called, then
      the call returns `{"ok": True, "agent": "aider", "worktree": <path>}`,
      a new git worktree exists at `<P>.teams/aider`, and a new tmux window
      named `aider` exists in the `team-P` session.
- [ ] Given the same call above, when the run's next `team_step()` round
      runs, then `state["members"]` includes `"aider"`, `state["worktrees"]`
      includes an `"aider"` entry, `state["membership_cursor"]` has advanced
      past the queued event, and one new history entry with
      `tool="team_member_joined"` was appended — and the lead was NOT called
      that round (mirrors `human.jsonl`'s own "drain-only round" behavior).
- [ ] Given the round after that, when the lead is called, then its tool
      schema/system framing include `aider` as a valid `delegate` target
      (verified via `_lead_tools(state["members"])`/`_validate_lead_action()`
      both now accepting `agent="aider"`), with no code change required in
      either of those two functions.
- [ ] Given a team already at `TEAM_MAX_MEMBERS` teammates, when
      `add_team_member()` is called with a new, otherwise-valid agent name,
      then it returns `{"ok": False, "error": ...}` naming the configured
      max, and no worktree/window/queued-event is created.
- [ ] Given an explicit picker composition (`validate_composition()`) with
      more than `TEAM_MAX_MEMBERS` teammate names, when team-start is
      attempted, then it is rejected with a clear "too many teammates"
      message before any worktree is created.
- [ ] Given `engines.d` configured with more than `TEAM_MAX_MEMBERS`
      headless-eligible engines and no explicit picker composition, when
      `default_team_composition()` is called, then its `members` list is
      truncated to exactly `TEAM_MAX_MEMBERS` entries, deterministically
      (same set every call, given the same `engines.d`).
- [ ] Given a team NOT currently running for project P (no run at all, or
      the latest run is terminal), when `POST /projects/P/team/add-member`
      is called, then it returns HTTP 400 with a clear "no run found"/status
      error and no side effects.
- [ ] Given a run in status `blocked_ask_user`, when `add_team_member()` is
      called with a valid new agent, then it succeeds (worktree + window
      created immediately) and the queued `member_joined` event is only
      drained once the run is later resumed (via `/team/resolve` or
      `team-resume`), not before.
- [ ] `team-add-member <run_id> <agent>` (CLI) produces the same effect and
      the same success/failure text conventions as the HTTP route, without
      needing any web server running.
- [ ] All existing team tests (6c/6d/6f/19/7/8's own suites) continue to
      pass unmodified — `membership_cursor`'s additive default (`0`) and the
      unchanged shape of every pre-existing `run.json` field mean no
      existing persisted-state fixture needs updating.

## Open questions
- **Part 2 (the actual "+" button UI) is not scoped here at all** — a
  separate `docs/spec.md` pass, once this part is built and reviewed, will
  cover: where the "+" lives on the Teams page (most likely inside the
  existing non-idle `teamRow()` render, near the "Stop team" action), how
  the target engine is picked (a small dropdown of `roster()` engine
  entries not already on the team, reusing 6e's picker-list rendering
  conventions rather than inventing a new picker), visibility rules (shown
  only when `team.status` is `running`/`blocked_ask_user`/
  `blocked_board_write`, hidden once at `TEAM_MAX_MEMBERS`), and how a
  `member_joined` event renders in the existing merged event feed (its own
  `agent="system"`/`kind="member_joined"` row style, distinct from both
  lead and human rows). **Assumption proceeding under**: this part ships
  with zero UI, verified via the CLI/route directly — flag if a UI
  affordance is actually wanted in the same cycle instead of split.
- **`TEAM_MAX_MEMBERS` default of 6`** is a judgment call, not a measured
  number (no host resource benchmarking was run) — reasonable given the
  per-teammate cost analysis in "Proposed approach" §4, but callable out
  as adjustable if a real deployment finds it too tight or too loose.
- **Whether a grown team's new teammate should get any different framing in
  its own dashboard window's very first log lines** (e.g., a short
  "you were just added mid-run" banner) — no such banner is added; the
  window behaves identically to an original teammate's from the moment
  it's created. Flagging in case product intent differs, but the backlog
  item's own text doesn't ask for this and there's no signal it's wanted.

## Risk / rollback notes
- Fully additive: no existing function's signature changes in a
  backward-incompatible way (`validate_composition()`/
  `default_team_composition()` gain new internal checks, not new required
  parameters; `_new_state()` gains one new dict key with a safe default).
  Reverting is a straightforward revert of this commit — no data migration
  needed since `membership_cursor` is read with `.get(..., 0)` and no
  existing `run.json` on disk needs it.
- Worst-case failure mode if `TEAM_MAX_MEMBERS` is misconfigured to `0` or a
  negative number: every add-member call (and possibly every team-start,
  once `validate_composition()`'s check applies) is rejected — a loud,
  immediate error, not a silent bypass; no separate guard needed against a
  pathological config value beyond the existing `int(os.environ.get(...))`
  parse (a non-numeric value already raises at import time, matching every
  other `TEAM_*` int env var's existing behavior in this file).
- The one genuinely new failure surface is the tmux `new-window` call
  against a session that might have just died — handled explicitly (§1 step
  6's rollback), not left as an unhandled exception path.
