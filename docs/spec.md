# Spec: Item 21 part 2 — the "+" button UI for growing a running team

## Summary
Ship the actual "+"-button UI backlog item 21's original request asked for,
on top of part 1's already-merged backend (`teams.add_team_member()`,
`POST /projects/<name>/team/add-member`, `team-add-member` CLI, the
`TEAM_MAX_MEMBERS` cap). A new inline control on an already-running team's
row lets a human pick one roster engine (via a native `<select>`, mirroring
the existing lead-picker's own control) and add it to the live team, reusing
`toggle()`'s existing TOTP-retry plumbing exactly like every other team
action. This also requires two small, necessary backend additions
`docs/spec.md` (part 1) explicitly deferred/left open: exposing the run's
*live* roster (`state["members"]`/`state["lead"]`) via `/status`, and
merging `membership.jsonl` into `GET .../team/events` so a `member_joined`
event is actually visible in the feed — without both, the UI this part adds
would have no correct data source to build the picker from, and no feed
evidence the add succeeded.

## Goals
- A "+"-style control on a `running`/`blocked_ask_user`/`blocked_board_write`
  team's row (the exact three statuses `add_team_member()` already accepts
  server-side — no new status set to invent) that lets a human pick ONE
  roster engine not already on the team and add it.
- The picker is a native `<select>`, populated only with eligible
  candidates (roster engines, excluding the current lead if the lead is an
  engine, excluding anyone already in the live roster) — mirrors the
  existing composition picker's lead-`<select>` exactly, not a new picker
  widget.
- When `TEAM_MAX_MEMBERS` is reached (or no eligible roster engine remains
  for a smaller reason), the control is disabled with an explicit, distinct
  reason shown inline — never hidden outright, and never a silent failed
  click.
- Accurate, non-oversold feedback: a successful add shows a message that
  states the new teammate will join **at the next round boundary** (part
  1's own delivery semantics), not that it is immediately available to
  delegate to. The `membership.jsonl` `member_joined` event becomes visible
  in the merged event feed on the very next `/team/events` poll (same ~4s
  cadence as everything else in that feed) — this is the honest "how do I
  know it worked" signal, not a fabricated instant-success UI state.
- The event feed's per-agent filter pills and the newly-added teammate's own
  events become reachable once the lead actually delegates to them, without
  requiring a full page reload.

## Non-goals
- **Removing/shrinking a team** — unrelated, not part of this backlog item.
- **A live terminal/window view of any teammate (original or newly added)**
  in the web UI — no such view exists for ANY teammate today (only the
  merged transcript-style event feed does); out of scope for this part,
  which only needs to reuse that existing feed, not build a new observation
  surface.
- **A confirm() dialog before adding.** This project's own established
  convention (`doDeploy()`'s and `doTeamStop()`'s comments, `app/app.py`)
  reserves `confirm()` for actions that mutate a remote target or kill/
  destroy in-flight work. Adding a teammate is additive and has no such
  effect — `doTeamStart()`/`doTeamInterject()`/`doTeamBoardResolve()` (the
  closest precedents) have no `confirm()` either.
- **Multi-select / adding more than one teammate per click** — the backlog
  item's own open question already framed this as "pick WHICH agent," not
  "pick several"; one `<select>` + one action, matching `add_team_member()`'s
  own single-`agent`-argument contract.
- **Renaming/relabeling `membership.jsonl`'s already-shipped envelope
  shape.** Part 1 persisted `{"agent": <joined-agent-name>, ...}` (the
  literal spec text it implemented), not `agent: "system"` — part 1's own
  "Open questions" floated `agent="system"` as a *possible* future
  rendering choice, not a commitment. This part reuses the already-shipped
  shape as-is (see "Proposed approach" §3 for why that's actually the
  better choice, not a compromise).
- **A client-side or server-side warning about resource cost beyond the
  existing `TEAM_MAX_MEMBERS` cap.** Part 1 already settled that; this part
  only surfaces the cap in the UI, it doesn't re-litigate its value.
- **Retrofitting `renderTeamFeed()`'s filter-pill list is broader than just
  this feature would need** — see "Proposed approach" §4: the fix (switch
  from the stale `team.composition.members` to the new live `team.members`)
  is scoped narrowly to what's needed for a newly-added teammate to get a
  filter pill at all; no other filter/feed behavior changes.

## Background / current state
- Part 1 (merged into this branch) shipped `teams.add_team_member(run_id,
  agent)`, `POST /projects/<name>/team/add-member` (`app/app.py`, right
  after `/team/interject`), and `team-add-member <run_id> <agent>` (CLI).
  Accepts exactly the same three statuses `interject()` does: `running`,
  `blocked_ask_user`, `blocked_board_write`. Returns `{"ok": true, "agent":
  ..., "worktree": ...}` / `{"error": ...}` (400) — confirmed directly from
  `docs/implementation.md`'s "item 21 part 1" section and the route code
  (`app/app.py`, `/team/add-member` branch).
- `TEAM_MAX_MEMBERS` (`app/teams.py`, default `6`) is enforced in
  `add_team_member()` itself — the route surfaces its rejection message
  verbatim as a 400, no separate check needed at the route or UI layer for
  correctness (the UI's own cap-awareness below is purely a UX
  convenience/reason-surfacing layer, not a second source of truth).
- **`/status`'s `inst["team"]` object does NOT currently expose the run's
  live roster.** Confirmed by reading `app/app.py`'s `/status` handler
  directly (~line 5600-5666): `composition` is read from
  `teams.load_compositions()` (a per-project *saved picker preference*,
  used to pre-fill the Start-time picker) — it is computed and returned
  unconditionally, including for an already-running team, but it is
  **never updated by `add_team_member()`** (which deliberately never
  touches `run.json`/`state["members"]` directly — see part 1's own "Key
  decisions"). For a grown team, `team.composition.members` is therefore
  stale the moment a teammate is added. `run` (the value
  `teams.latest_run_for_project()` returns) IS the full persisted state
  dict and already has `run["members"]`/`run["lead"]` — just not currently
  copied into the JSON response.
- **`renderTeamFeed()`'s per-agent filter-pill list is built from
  `team.composition.members`** (`app/app.py`, ~line 3877:
  `const agents = ['lead', 'human'].concat((team.composition &&
  team.composition.members) || []);`), the same stale field. A newly-added
  teammate's events would already appear in the merged feed under `filter
  === 'all'` once the lead delegates to them (their own `<agent>.jsonl` is
  already included via `state.get("members", [])` inside
  `_handle_team_events()`, which itself IS live/fresh every poll — only the
  *pill list* is stale), but they would have no dedicated filter pill to
  click, and would never appear in the pill list at all if the operator
  never happens to see one of their `all`-filtered events go by.
- **`GET .../team/events` (`_handle_team_events()`, `app/app.py` ~line
  5763) does not merge `membership.jsonl` into its file list today** — only
  `transcript.jsonl` ("lead"), `human.jsonl` ("human"), and one file per
  `state.get("members", [])`. This means the one durable, already-persisted
  record that an add succeeded (`membership.jsonl`'s `member_joined`
  envelope, written synchronously inside `add_team_member()`) is currently
  invisible in the web UI's own event feed — part 1's own "Open questions"
  flagged this exact gap and deferred it here.
- The composition picker (`renderTeamPicker()`, `app/app.py` ~line 3495) is
  this project's own established "pick one engine via native `<select>`"
  precedent — its lead-`<select>` is the direct model for this part's new
  control (one dropdown, `ROSTER` entries as `<option>`s, `tierLabel()` for
  the visible label). Item 19 part 2's compose box
  (`renderTeamInterjectBox()`, `app/app.py` ~line 4039) is the direct model
  for "a new control that posts to a team-scoped route, visible under the
  same status conditions `add_team_member()` itself accepts" — in fact its
  own `teamAcceptsInterject(team)` helper computes EXACTLY the visibility
  condition this part needs too (same three statuses), since `interject()`
  and `add_team_member()` were both built to accept the identical status
  set.

## Proposed approach

### 1. `/status` gains the live roster (`app/app.py`, `/status` handler)
Add two fields:
- `inst["team"]["members"]`: `run.get("members", []) if run is not None else
  []` — the live, currently-drained roster (grows the moment `team_step()`'s
  membership drain runs, i.e. one round-poll after `add_team_member()`
  succeeds — never earlier, matching part 1's own delivery semantics).
- `inst["team"]["lead"]`: `run.get("lead") if run is not None else None` —
  the run's actual lead (`{"kind", "name"}` or `None`), read directly from
  the live state rather than re-deriving it from the picker's saved/default
  `composition.lead`, which could in principle differ if an operator saved a
  new preference after this run's own team-start.

Also add one new top-level field, alongside the existing `"roster": roster`:
`"team_max_members": teams.TEAM_MAX_MEMBERS` — a single global constant,
same "computed once, shipped once per `/status` call" treatment `roster`
itself already gets (not per-project, since the cap is one process-wide
config value).

Both additions are purely additive to the JSON shape — no existing field
changes meaning, no existing test asserting exact-dict-equality on `/status`
should need more than adding the two new keys to its expected shape (per
this codebase's own established "additive JSON field" precedent, e.g. how
`escalation_kind` was added in item 7 part 2).

### 2. `GET .../team/events` merges `membership.jsonl` too (`app/app.py`,
`_handle_team_events()`)
One new line in the `files` list, alongside the existing three sources:
```python
files = [("lead", teams._transcript_path(run_id)), ("human", teams._human_log_path(run_id)),
         ("membership", teams._membership_log_path(run_id))]
files += [(m, teams._agent_log_path(run_id, m)) for m in state.get("members", [])]
```
The `agent` label passed to `tail_jsonl_events()` here (`"membership"`) is
only used for the malformed-line fallback and the `cursors` dict's own key
— it does NOT override the `agent` field already embedded in each
`membership.jsonl` line by `add_team_member()` (`{"agent": <joined-agent-
name>, ...}`, part 1's own shipped shape — `tail_jsonl_events()` only
substitutes its own `agent` label when a line fails to parse as JSON at
all). So a `member_joined` event surfaces in the merged feed tagged with the
NEWLY-JOINED agent's own name (e.g. `"aider"`), not a generic `"system"`
pseudo-agent.

**This is a deliberate choice, not an oversight** (see "Non-goals" above):
tagging the event with the joined agent's own name means it renders in that
agent's own established color (`teamAgentColor()`), visually tying "this is
the moment this color/agent became available" together — arguably more
useful than a flat gray system line, and it requires zero change to
`_handle_team_events()`'s per-file `agent` parameter contract or to
`tail_jsonl_events()` itself. `cursors["membership"]` is a new key in the
response; the frontend's cursor-merge logic (`app/app.py`, the
`pollTeamFeed()`-equivalent client function) already treats `cursors` as an
open dict keyed by whatever agent labels the server returns (proven by how
it already handles `"human"` today, added by item 19 part 1 with zero
frontend cursor-logic change) — no frontend cursor-handling change needed
beyond what "Proposed approach" §5 below already describes for rendering.

### 3. New frontend event classification for `member_joined`
(`app/app.py`, `teamFeedEventKindClass()`/`teamFeedEventBody()`)

One new branch in `teamFeedEventKindClass()`, placed alongside the other
`kind`-based checks (order doesn't matter here — `kind === 'member_joined'`
is structurally disjoint from every existing branch, none of which check
that `kind` value):
```javascript
if (e.kind === 'member_joined') return 'member-joined';
```
One new branch in `teamFeedEventBody()`:
```javascript
if (cls === 'member-joined') return '→ joined the team';
```
(The agent name itself is already rendered by `renderTeamFeedEvent()`'s
existing `<span class="team-feed-agent">` — no need to repeat it in the
body text.) New CSS: `.team-feed-event.kind-member-joined` — a left-border
accent using that event's own agent color inline style already applied via
`renderTeamFeedEvent()`'s existing `style="color:' + color + '"` mechanism;
no new color token needed, reuses `TEAM_AGENT_PALETTE`.

### 4. Filter-pill list now reads the live roster, not the stale composition
(`app/app.py`, `renderTeamFeed()`, ~line 3877)
```javascript
const agents = ['lead', 'human'].concat(team.members || []);
```
replacing `(team.composition && team.composition.members) || []`. This is
the one necessary correction identified in "Background" above — without it,
a newly-added teammate's events are reachable under the `all` filter (their
own log file was already merged by `_handle_team_events()` before this
part) but never get their own clickable pill. `team.members` is the new
`/status` field from §1; every existing caller of `renderTeamFeed()` already
receives the full `team` object, so no signature change.

### 5. The "+" control itself (`app/app.py`, new functions alongside
`renderTeamInterjectBox()`/`doTeamInterject()`)

**Eligibility helper**, pure, no I/O:
```javascript
function teamAddMemberEligible(team) {
  const already = new Set(team.members || []);
  const leadName = team.lead && team.lead.kind === 'engine' ? team.lead.name : null;
  return ROSTER.filter(e => e.kind === 'engine' && e.name !== leadName && !already.has(e.name));
}
```
(`e.kind === 'engine'` mirrors `add_team_member()`'s own server-side
rejection of the Ollama lead-only roster entry — same rule, restated
client-side for fast feedback, exactly the same "client mirrors server,
server stays authoritative" discipline `teamCompositionError()` already
documents for the Start-time picker.)

**Visibility gate**: reuse `teamAcceptsInterject(team)` as-is (see
"Background" — the status set is identical by construction). No rename;
add a one-line comment at the new call site noting the reuse is
intentional, not incidental.

**Render function**, placed directly below `renderTeamInterjectBox()` in
`teamRow()`'s non-idle branch (own visual block, between the interject box
and the feed toggle — matches "coexists with other controls" without
crowding the `.team-actions` row, which stays reserved for the single
"Stop team" button per existing convention):
```javascript
function renderTeamAddMemberControl(name, team) {
  if (!teamAcceptsInterject(team)) return '';
  const members = team.members || [];
  const atCap = members.length >= (TEAM_MAX_MEMBERS_CLIENT || 6);
  const eligible = teamAddMemberEligible(team);
  if (atCap) {
    return '<div class="team-add-member"><span class="team-add-member-reason">' +
      'Team is at the maximum of ' + (TEAM_MAX_MEMBERS_CLIENT || 6) + ' teammates.</span></div>';
  }
  if (eligible.length === 0) {
    return '<div class="team-add-member"><span class="team-add-member-reason">' +
      'No more roster engines available to add.</span></div>';
  }
  const options = eligible.map(e =>
    '<option value="' + esc(e.name) + '">' + esc(e.name) + ' (' + tierLabel(e.tier) + ')</option>').join('');
  return '<div class="team-add-member">' +
    '<select id="team-add-member-select-' + esc(name) + '">' + options + '</select>' +
    '<button class="team-btn" onclick="doTeamAddMember(' + "'" + name + "'" + ')">+ Add</button></div>';
}
```
`TEAM_MAX_MEMBERS_CLIENT`: a new client-side global, hardcoded default `6`
(matching the server default), overwritten from `s.team_max_members` on
every `/status` poll — exact same "hardcoded default + live override"
precedent `TEAM_INTERJECT_MAX_CHARS_CLIENT` already establishes (item 19
part 2), not a new pattern.

**Dispatch function**, mirrors `doTeamInterject()`'s shape:
```javascript
let teamAddMemberChoice = {};  // name -> agent name, set before toggle() fires (survives a 428 retry)
function doTeamAddMember(name) {
  const sel = document.getElementById('team-add-member-select-' + name);
  if (!sel || !sel.value) return;
  teamAddMemberChoice[name] = sel.value;
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  toggle('team-add-member', name, true, null);
}
```

**Wiring into the existing shared plumbing** (`app/app.py`):
- `actionPath()`: `if (kind === 'team-add-member') return '/projects/' +
  encodeURIComponent(name) + '/team/add-member';`
- `actionBody()`: `if (kind === 'team-add-member') body.agent =
  teamAddMemberChoice[name];`
- `handleActionResult()`: new branch, same "own inline result slot, handled
  before the generic 400 branch" pattern `team-interject`/
  `team-board-resolve` already use:
  ```javascript
  if (kind === 'team-add-member') {
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (r.ok && data.ok) {
        msgEl.textContent = '✓ \'' + esc(data.agent) + '\' will join the team at its next round';
        msgEl.className = 'team-msg success';
        delete teamAddMemberChoice[name];
      } else {
        msgEl.textContent = '✕ Error: ' + (data.error || 'could not add teammate');
        msgEl.className = 'team-msg error';
      }
    }
    return;
  }
  ```
  (Deliberately says "will join... at its next round," never "has joined" —
  directly satisfies this spec's own "accurate, non-oversold feedback"
  goal.)
- The 428 code-overlay label ternary (`handleActionResult()`, the existing
  chain of `kind === '...' ? '...' :`) gains: `kind === 'team-add-member' ?
  'Adding teammate: ' + (name || 'this') :`.
- `teamRow()`'s non-idle branch: insert
  `renderTeamAddMemberControl(name, team)` between `interjectBox` and
  `feedToggle`.

**New CSS**: `.team-add-member { display: flex; gap: 8px; align-items:
center; margin-top: 4px; }`, `.team-add-member select { font-size: 13px;
padding: 6px 8px; border-radius: 8px; ... }` (reusing
`.team-lead-picker select`'s existing declaration block's values, not
inventing new tokens), `.team-add-member-reason { font-size: 12px; color:
#888; }` (same muted-informational-text token `.team-branches` already
uses for its own "unavailable"/"none" states).

## Affected areas
- `app/app.py`:
  - `/status` handler: two new fields on `inst["team"]` (`members`, `lead`),
    one new top-level field (`team_max_members`).
  - `_handle_team_events()`: `membership.jsonl` added to the merged file
    list.
  - Frontend JS: `teamAddMemberEligible()`, `renderTeamAddMemberControl()`,
    `doTeamAddMember()`, `teamAddMemberChoice` (new); `actionPath()`/
    `actionBody()`/`handleActionResult()` gain `'team-add-member'` branches;
    `teamFeedEventKindClass()`/`teamFeedEventBody()` gain the
    `member_joined` branch; `renderTeamFeed()`'s `agents` line changed to
    read `team.members`; `teamRow()` gains one new call; new
    `TEAM_MAX_MEMBERS_CLIENT` global, updated from `/status`.
  - New CSS: `.team-add-member`/`.team-add-member select`/
    `.team-add-member-reason`; `.team-feed-event.kind-member-joined`.
- No `app/teams.py` change — part 1's backend is reused entirely as-is; no
  new function, no new route beyond what part 1 already shipped, no schema
  change to `run.json`/`membership.jsonl`.
- No new route. `/status` and `GET .../team/events` are both existing
  routes gaining additive fields/merged sources, not new endpoints.

## Edge cases
- **Team at `TEAM_MAX_MEMBERS` already** — control renders disabled with
  the "Team is at the maximum of N teammates" reason, computed from the
  live `team.members.length` (not a snapshot) every `/status` poll, so it
  flips to enabled again automatically if the operator (or a future
  "shrink" feature, not built here) ever reduces the count — no manual
  refresh needed.
- **Fewer roster engines configured than `TEAM_MAX_MEMBERS`, and every one
  is already on the team** — distinct reason text ("No more roster engines
  available to add") from the at-cap case, so an operator isn't told to
  wait for a cap that was never actually the blocker.
- **Server rejects anyway despite client-side eligibility passing** (a race:
  another operator/tab added the same or a different teammate between this
  tab's last `/status` poll and this click, pushing the count to the cap in
  between) — the existing generic 400-handling path in `handleActionResult()`
  surfaces `data.error` verbatim in the row's `team-msg` slot; the server
  (`add_team_member()`) remains the sole source of truth, exactly as every
  other client-side-mirrored validation in this codebase already documents
  (`teamCompositionError()`'s own comment).
- **Team transitions out of an accepted status between render and click**
  (e.g. it finishes or errors while the control is visible) — `toggle()`'s
  own existing POST still fires; `add_team_member()`'s own status check
  rejects with a clear error, surfaced the same way as any other race here
  — no new handling needed, same class of race `doTeamInterject()` already
  accepts unhandled-beyond-the-server-check.
- **Row re-renders mid-selection** (a `/status` poll lands while the
  `<select>` is open) — `refresh()`'s existing full-row re-render already
  resets any unsubmitted, unmirrored `<select>` choice on every poll for
  every kind of control in this codebase that isn't backed by a client-side
  text mirror (e.g. the lead-picker's own `<select>` IS mirrored via
  `teamPickerLead`/survives redraws; this new `<select>`'s value is NOT
  separately mirrored client-side pre-submit, matching `team-mate` checkbox
  behavior's own precedent of "state lives in the DOM until submitted, not
  a JS mirror" — acceptable because, unlike the interject textarea, there's
  no risk of losing meaningful typed text here, only a re-picked dropdown
  selection, which is cheap to redo).
- **`membership.jsonl` doesn't exist yet for a run that predates part 1**
  (impossible in practice on this branch, since part 1 is already merged
  and no run persists across a deploy that removed the feature, but as a
  defensive matter) — `tail_jsonl_events()` already returns `([], offset,
  False)` on `FileNotFoundError`, so this degrades to "no membership events
  ever appear," not an error.
- **Two operators in two browser tabs both add a teammate concurrently** —
  covered entirely by part 1's own already-documented concurrent
  `add_team_member()` race handling (independent worktree/window/queued
  envelope per distinct agent; a narrow, accepted false-positive "still has
  uncommitted changes" error only for the exact-same-agent-name race). No
  new handling needed at the UI layer beyond surfacing whatever error the
  server returns.

## Acceptance criteria
- [ ] Given a `running` team with members `["codex"]` and roster
      `["codex", "aider", "claude"]` (lead = a separate Ollama entry), when
      the row renders, then the "+" control shows a `<select>` with exactly
      `aider` and `claude` as options (not `codex`, already a member; not
      the Ollama lead entry).
- [ ] Given the same team, when `aider` is selected and "+ Add" is clicked,
      then `POST /projects/<name>/team/add-member` is sent with
      `{"agent": "aider"}`, and on success the row's message slot shows
      "✓ 'aider' will join the team at its next round" (not "has joined" /
      "is now available").
- [ ] Given the add above succeeded, when the next `/team/events` poll
      lands, then a `member_joined`-kind event tagged `agent: "aider"`
      appears in the merged feed (rendered as "→ joined the team" in
      `aider`'s own established color), even before the lead's next round
      has run.
- [ ] Given the same add, when the run's next `team_step()` round runs (a
      subsequent `/status` poll after that), then `team.members` in the
      `/status` response includes `"aider"`, and the feed's filter pills now
      include an `aider` pill.
- [ ] Given a team already at `TEAM_MAX_MEMBERS` (`team.members.length ===
      team_max_members` from `/status`), when the row renders, then the "+"
      control is disabled/replaced with "Team is at the maximum of N
      teammates." and no `<select>`/button is clickable.
- [ ] Given a team with every roster engine already a member (but under the
      numeric cap), when the row renders, then the control shows "No more
      roster engines available to add." — distinct text from the at-cap
      case.
- [ ] Given a team in `blocked_ask_user` or `blocked_board_write`, when the
      row renders, then the "+" control is visible and enabled (same
      condition `teamAcceptsInterject()` already computes for the compose
      box) — and given a team in `escalated_max_rounds`/`finished`/`error`/
      `idle`, then it is not rendered at all.
- [ ] Given a server-side rejection (e.g. a genuine concurrent-add race
      pushing the team over the cap between poll and click), when the POST
      returns 400, then the row's message slot shows "✕ Error: <the
      server's exact message>", and the `<select>`/button remain usable for
      a retry.
- [ ] Given a 428 TOTP challenge mid-flow, when the code is submitted, then
      the retry resends the SAME `agent` value originally selected (via
      `teamAddMemberChoice[name]`, not a re-read of a possibly-already-
      cleared `<select>`).
- [ ] All existing frontend tests (`tests/test_team_frontend.js`) and
      backend team-route tests continue to pass with the two new additive
      `/status`/`/team/events` fields — no existing exact-dict-equality test
      should need more than adding the new keys to its expected shape.

## Open questions
- **`ux-designer` IS needed this cycle.** This is a genuinely new UI
  control (not a copy of an existing one wired to a new endpoint) —
  reviewing/refining the exact placement (between interject box and feed
  toggle vs. inside `.team-actions`), spacing, and the two distinct
  disabled-reason copy strings against `docs/design.md`'s own established
  visual language (status-strip colors, `.team-escalation`/`.team-interject`
  spacing tokens) is real design work, not a mechanical extension. Unlike
  item 13's "Past team branches" panel (which the product-manager judged
  didn't need a design pass, being a plain read-only list with an obvious
  single layout), this control has real states (enabled / two distinct
  disabled reasons / mid-request) worth a design pass. **Assumption
  proceeding under**: `ux-designer` runs next, using this spec's "Proposed
  approach" as its functional baseline — the exact CSS values above are a
  developer-ready starting point, not a locked-in final design.
- **Whether `member_joined`'s feed row should additionally trigger any kind
  of transient visual highlight** (e.g. briefly flashing the new filter
  pill) — no such affordance is specced here; flagging in case product
  intent wants stronger immediate feedback than a feed line + message-slot
  text. Assumption: not needed: the existing feed's own scroll/append
  behavior is already the established "something happened" signal for
  every other event kind.
- **Whether the CLI (`team-add-member`) should also gain a `--roster` /
  list-eligible-first convenience flag** now that a UI exists to browse the
  roster visually — out of scope here (this part is UI-only, no CLI
  change); flagging as a possible small follow-up, not blocking.

## Risk / rollback notes
- Fully additive on both the backend-field and frontend-control level: no
  existing `/status`/`/team/events` field is removed or reinterpreted, no
  existing route's request/response shape for any OTHER action changes. A
  revert of this commit removes the control and the two additive fields
  with zero data-model migration (nothing new is persisted server-side by
  this part at all — `membership.jsonl`'s shape and write path are entirely
  part 1's, untouched here).
- Worst-case failure mode if the `/status`/`/team/events` additions are
  buggy: the "+" control renders with an empty/wrong option list, or the
  feed misses/misrenders a `member_joined` line — cosmetic degradation, not
  a functional regression, since `add_team_member()`'s own server-side
  validation (unchanged, part 1) remains the sole correctness gate no
  matter what the UI shows or omits.
- No new privileged code path, no new sudoers entry, no new tmux/subprocess
  call anywhere in this part — every mutation still flows through part 1's
  already-reviewed `add_team_member()`.
