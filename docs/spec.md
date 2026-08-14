# Spec: Backlog item 19 part 2 — chat-UI-facing surface for interjecting into a running team

## Summary
Add the human-facing presentation and interaction layer on top of item 19
part 1's already-shipped backend (`teams.interject()`, `POST
/projects/<name>/team/interject`, `human.jsonl` merged into `GET
.../team/events` as `agent="human"`/`kind="message"`): a per-project compose
box + Send control on the Teams row, a distinct (not full chat-bubble) row
treatment for human messages in the existing merged event feed, and explicit,
reasoned handling of the compose box's interaction with a live
`blocked_ask_user`/`blocked_board_write` escalation panel and with part 1's
two length/byte constants. This is front-end-only (`app/app.py`'s inline
HTML/CSS/JS) — no `app/teams.py` change, no new route, no new data shape.

## Goals
- A textarea + "Send" control on the Teams page, per project, that posts a
  free-text message via the already-shipped `POST .../team/interject`,
  visible exactly when the backend would actually accept it (`running`,
  `blocked_ask_user`, `blocked_board_write` — not `idle`/`finished`/
  `error`/`escalated_max_rounds`), reusing the existing TOTP-retry (`toggle`/
  `actionPath`/`actionBody`/`handleActionResult`) machinery `team-resolve`/
  `team-board-resolve` already established, not a new gating mechanism.
- Human (`agent==="human"`, `kind==="message"`) events get their own,
  visually distinct row treatment in the existing merged log-list feed — a
  new CSS classification, not a redesign of the feed into chat bubbles (see
  "Proposed approach" §2 for why).
- The compose box and the escalation panel (when `waiting_on_you`) render
  and function simultaneously — matches part 1's own explicit "allowed while
  blocked" design, restated and made visible in the UI rather than assumed.
- `TEAM_INTERJECT_MAX_CHARS` (2000) is enforced client-side before the
  request is even sent: a live character counter, and Send disabled while
  empty or over limit — mirroring, and going one step further than,
  `team-resolve`'s existing submit-time-only check.
- Answer backlog item 19's own "purely visual restyling... or a chat-bubble
  reskin?" question concretely, in this spec, not left for `ux-designer` to
  re-litigate (see "Proposed approach" §2 and "Open questions").

## Non-goals
- **No `app/teams.py` or route change of any kind.** All backend plumbing
  (`interject()`, the route, `human.jsonl`, the `GET .../team/events` merge)
  already shipped in part 1 (`backlog/team-chat-interrupt-19`, already
  merged into this branch). This part touches `app/app.py`'s front-end code
  only.
- **No full chat-bubble redesign of the merged event feed.** Decided, not
  deferred (see "Proposed approach" §2): the feed carries structured,
  multi-party, mostly non-conversational content (fact-check match arrays,
  board-write diffs, terminal-escalation banners, delegate handoffs) across
  more than two participants (lead + N teammates + human) — a bubble/
  alignment layout doesn't fit that shape and would put 6f part 2's existing
  `role="log"`/`aria-live="polite"` accessibility contract at risk for no
  functional gain. Human messages get a distinct row style within the
  existing log-list instead.
- **No Enter-to-send keybinding.** Every other multi-line free-text input in
  this app (`task-<name>` textarea, the escalation "Other" textarea) is
  submitted only via an explicit button click; adding chat-style
  Enter-to-send here alone would be a one-off inconsistency, not a real
  requirement of the backlog item.
- **No UI control tied to `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND`.** Decided,
  not an oversight (see "Proposed approach" §4) — it bounds a server-side
  per-round *drain* of already-queued messages into the lead's visible
  history, not any single message's size, and has no effect on when a
  posted message becomes visible in the human-facing feed (`human.jsonl` is
  merged into `GET .../team/events` directly and independently of
  `team_step()`'s own drain timing). There is nothing meaningful to
  pre-validate against client-side.
- **No live server-exposed value for `TEAM_INTERJECT_MAX_CHARS`.** The
  client hardcodes `2000` (the documented default), exactly mirroring
  `team-resolve`'s own existing hardcoded-`2000` precedent
  (`doTeamResolve()`, `app/app.py:3339`) rather than introducing a new
  config-fetch mechanism nothing else in this app has. If the env var is
  ever overridden, the client-side guard drifts out of sync with the
  server's actual limit — an existing gap class this spec is consistent
  with, not a new one, and not fixed here (server-side validation is still
  authoritative and always wins).
- **No double-submit / in-flight Send-disable protection beyond what other
  actions in this app already have.** No existing action button (Submit
  answer, Approve/Reject, Stop team) disables itself while its own POST is
  in flight; `teams.interject()`'s append-only semantics make a duplicate
  rapid double-click benign (two queued messages, no corruption) — not
  introducing a new pattern for this one control alone.
- **No messaging a specific teammate directly, editing/withdrawing a sent
  message, or true mid-tool-call interruption** — all already out of scope
  per part 1's own "Non-goals"; restated here only because they're the kind
  of thing "chat UI" framing could otherwise imply. The UI has exactly one
  compose box, addressed to the lead only, matching the one channel
  (`human.jsonl`) that exists.

## Background / current state
- `app/teams.py:4241` `interject(run_id, text)` accepts while `status` is
  `"running"`, `"blocked_ask_user"`, or `"blocked_board_write"`; rejects
  (`{"ok": False, "error": f"run {run_id} is not accepting messages
  (status={status})"}`) for `"finished"`, `"escalated_max_rounds"`,
  `"error"`, `"stopped"`.
- `app/app.py:4776-4847` (`/status`-family payload builder) collapses that
  six-value backend status space into the frontend's `team.status` ∈
  `{idle, running, blocked, finished, error}` plus a separate
  `team.waiting_on_you` boolean, true only for `blocked_ask_user`/
  `blocked_board_write` (the exact two backend statuses, alongside
  `running`, that `interject()` accepts). `escalated_max_rounds` also maps
  to frontend `blocked`, but with `waiting_on_you === false` — already the
  live discriminator `renderTeamStatusStrip()` (`app/app.py:2920-2932`) and
  the `escalatedNote` computation (`app/app.py:3407`) use to tell "blocked,
  waiting on you" from "blocked, escalated on max rounds" apart. This means
  `team.status === "running" || (team.status === "blocked" &&
  team.waiting_on_you)` is already, today, an exact frontend mirror of
  `interject()`'s own accept set — no new backend field or endpoint is
  needed to compute compose-box visibility correctly.
- `app/app.py:5367-5407` `POST /projects/<name>/team/interject` — validates
  `run_id` (via `_RUN_ID_RE`, defaulting to `latest_run_for_project`),
  validates `text` non-empty and `<= teams.TEAM_INTERJECT_MAX_CHARS` at the
  route layer, calls `teams.interject()`, returns `{"ok": true, "run_id":
  ...}` or `{"error": ...}` (400). No background thread is spun up (see part
  1 spec §5) — this route only queues.
- `app/app.py:4964-4965` `_handle_team_events()`'s `files` list already
  includes `("human", teams._human_log_path(run_id))` — a posted
  interjection is visible via the very next `GET .../team/events` poll, with
  no dependency on when/whether `team_step()` has drained it into round
  history yet.
- Client-side live-feed state (`app/app.py:2680-2684`): `teamFeedOpen`,
  `teamFeedCursor`, `teamFeedEvents`, `teamFeedFilter`, `teamFeedPolling` —
  all keyed by project `name`. `clearTeamFeedState(name)`
  (`app/app.py:2718-2725`) resets all of them (plus escalation-form state)
  whenever a row falls back to the `idle` branch of `teamRow()`.
- `teamRow(name, team)` (`app/app.py:3361-3418`): the `idle` branch (task
  textarea + Start button) short-circuits at the top; the non-idle branch
  renders, in order, `statusStrip` → `escalatedNote` → `escalationPanel` (if
  `waiting_on_you`) → `feedToggle` → `feedPanel` → a `Stop team` button →
  the shared `.team-msg` result slot → `renderTeamBranches()`.
- `renderTeamFeed()` (`app/app.py:3229-3255`) builds the per-agent filter
  pill row from `['lead'].concat(team.composition.members || [])` — no
  `'human'` entry exists today. `teamFeedEventKindClass()`
  (`app/app.py:3130-3167`) and `teamFeedEventBody()`
  (`app/app.py:3168-3218`) classify/render each event; the existing
  catch-all `if (e.kind === 'message' || e.kind === 'status') return
  esc(e.text || '');` (`app/app.py:3216`) already renders a human message's
  *text* correctly today (part 1 shipped this "renders generically even
  before part 2's styling lands" as one of its own goals) — what's missing
  is only a distinct CSS *classification*, not correct text rendering.
  `teamAgentColor(agentName)` (`app/app.py:2704-2709`) is a generic
  string-hash → palette-index function, already producing a distinct,
  stable color for `agent: "human"` with zero code change.
- `renderTeamStatusStrip`/`renderEscalationPanel`/free-text inputs
  (`teamTaskText`, `teamEscalationOther`) all follow the same "client-side
  mirror map survives a full-row `refresh()` re-render and a 428 TOTP retry;
  read the live DOM element first, fall back to the mirror" idiom — most
  explicitly commented at `app/app.py:3472-3479` (`team-start`'s own task
  text). `updateTeamStartButton(name)` (`app/app.py:2895-2902`) is the
  precedent for "recompute a button's `disabled` state via a direct,
  narrow DOM write on `oninput`, not a full `refresh()`" — `refresh()`
  replaces the row's `innerHTML`, which would drop focus/cursor position on
  every keystroke.
- `toggle(kind, name, on, checkboxEl)` (`app/app.py:3670-3700`) →
  `performAction()` → `handleActionResult()` is the fully generic
  TOTP-optimistic-then-428-retry dispatch path every existing action
  (`team-start`, `team-stop`, `team-resolve`, `team-board-resolve`, ...)
  already uses; `actionPath(kind, ...)` (`app/app.py:3444-3456`) and
  `actionBody(kind, ...)` (`app/app.py:3457-3506`) are the two `switch`-like
  functions that need one new `kind` branch each; `handleActionResult()`
  needs one new `kind === 'team-interject'` branch (own `.team-msg` result
  slot), placed before its generic 400 fallback, exactly mirroring
  `team-resolve`'s own branch (`app/app.py:3575-3598`). `submitActionCode()`
  (`app/app.py:3836+`) is fully generic over `pendingToggle.kind` — needs no
  change.

## Proposed approach

### 1. Compose box: visibility, state, wiring
Add one client-side draft-text map, alongside the existing per-project
mirrors:
```js
let teamInterjectText = {};  // name -> string draft
const TEAM_INTERJECT_MAX_CHARS_CLIENT = 2000;  // mirrors teams.TEAM_INTERJECT_MAX_CHARS's default -- see "Non-goals"
```
A shared visibility predicate (single source of truth, used both for
rendering and for clearing stale drafts — avoids the same kind of
duplicated-logic drift `computeTeamResolveAnswer()` was introduced to
prevent):
```js
function teamAcceptsInterject(team) {
  return !!team && (team.status === 'running' ||
                     (team.status === 'blocked' && team.waiting_on_you));
}
```
Render function (new), returning `''` — and proactively discarding any
stale draft — whenever the current status doesn't accept one:
```js
function renderTeamInterjectBox(name, team) {
  if (!teamAcceptsInterject(team)) { delete teamInterjectText[name]; return ''; }
  const text = teamInterjectText[name] || '';
  const len = text.length;
  const over = len > TEAM_INTERJECT_MAX_CHARS_CLIENT;
  const disabled = !text.trim() || over;
  const placeholder = team.waiting_on_you ?
    'Send a message to the team (this will not answer the pending question above)…' :
    'Send a message to the team…';
  return '<div class="team-interject">' +
    '<div class="team-interject-row">' +
    '<textarea class="team-interject-textarea" id="interject-' + esc(name) + '" rows="2" ' +
    'placeholder="' + esc(placeholder) + '" oninput="teamInterjectText[' + "'" + name + "'" +
    '] = this.value; updateTeamInterjectControls(' + "'" + name + "'" + ');">' + esc(text) + '</textarea>' +
    '<button class="team-btn" id="interject-send-' + esc(name) + '"' + (disabled ? ' disabled' : '') +
    ' onclick="doTeamInterject(' + "'" + name + "'" + ')">Send</button>' +
    '</div>' +
    '<div class="team-interject-counter' + (over ? ' over-limit' : '') + '" id="interject-counter-' + esc(name) + '">' +
    len + '/' + TEAM_INTERJECT_MAX_CHARS_CLIENT + '</div></div>';
}
```
`updateTeamInterjectControls(name)` — a narrow direct-DOM update on
`oninput`, matching `updateTeamStartButton()`'s exact idiom (no `refresh()`
call, so typing never re-renders the row or loses cursor position):
```js
function updateTeamInterjectControls(name) {
  const btn = document.getElementById('interject-send-' + name);
  if (!btn) return;
  const text = teamInterjectText[name] || '';
  const len = text.length;
  const over = len > TEAM_INTERJECT_MAX_CHARS_CLIENT;
  btn.disabled = !text.trim() || over;
  const counterEl = document.getElementById('interject-counter-' + name);
  if (counterEl) {
    counterEl.textContent = len + '/' + TEAM_INTERJECT_MAX_CHARS_CLIENT;
    counterEl.className = 'team-interject-counter' + (over ? ' over-limit' : '');
  }
}
```
Dispatch (mirrors `doTeamResolve()` exactly):
```js
function doTeamInterject(name) {
  const msgEl = document.getElementById('team-msg-' + name);
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'team-msg'; }
  const text = (teamInterjectText[name] || '').trim();
  if (!text || text.length > TEAM_INTERJECT_MAX_CHARS_CLIENT) {
    if (msgEl) {
      msgEl.textContent = 'Message must be non-empty and at most ' + TEAM_INTERJECT_MAX_CHARS_CLIENT + ' characters';
      msgEl.className = 'team-msg error';
    }
    return;
  }
  toggle('team-interject', name, true, null);
}
```
Three small additions to the existing generic dispatch functions:
- `actionPath()`: `if (kind === 'team-interject') return '/projects/' + encodeURIComponent(name) + '/team/interject';`
- `actionBody()`: reads the live textarea first, falls back to the mirror —
  same "survives a mid-flow re-render/428 retry" reasoning as `team-start`'s
  own task-text field:
  ```js
  if (kind === 'team-interject') {
    const el = document.getElementById('interject-' + name);
    body.text = (el ? el.value : (teamInterjectText[name] || '')).trim();
  }
  ```
- `handleActionResult()`'s 428 label switch: add
  `kind === 'team-interject' ? 'Sending message: ' + (name || 'this') :`
- `handleActionResult()`: new branch, placed before the generic-400
  fallback, exactly mirroring the `team-resolve` branch's shape
  (`app/app.py:3575-3598`):
  ```js
  if (kind === 'team-interject') {
    hideCodeOverlay();
    const data = await r.json().catch(() => ({}));
    const msgEl = document.getElementById('team-msg-' + name);
    if (msgEl) {
      if (r.ok && data.ok) {
        msgEl.textContent = '✓ Message sent';
        msgEl.className = 'team-msg success';
        delete teamInterjectText[name];
        const ta = document.getElementById('interject-' + name);
        if (ta) ta.value = '';
        updateTeamInterjectControls(name);
      } else {
        // Draft text is deliberately NOT cleared on failure -- the
        // operator can fix and resend without retyping.
        msgEl.textContent = '✕ Error: ' + (data.error || 'could not send message');
        msgEl.className = 'team-msg error';
      }
    }
    return;
  }
  ```
`clearTeamFeedState()` (`app/app.py:2718-2725`) gets one more line —
`delete teamInterjectText[name];` — for the `idle`-transition case (falling
back to the idle branch skips `renderTeamInterjectBox()` entirely, so its
own stale-draft cleanup never runs for that particular transition).

`teamRow()`'s non-idle render order (`app/app.py:3413`) gains exactly one
new piece, inserted between `escalationPanel` and `feedToggle`:
```js
const interjectBox = renderTeamInterjectBox(name, team);
return '<div class="team-row">' + statusStrip + escalatedNote + escalationPanel +
  interjectBox + feedToggle + feedPanel + ...
```
Rationale for this position: escalation resolution (answering a specific
pending question) is the higher-priority, often-blocking action and stays
visually first when present; the always-available free-form channel sits
directly below it; both sit above the passive, scrollable log feed.

### 2. Feed styling for human messages — a distinct row, not a bubble reskin
Decision (answering backlog item 19's own open question): **no chat-bubble
redesign.** The merged feed already renders ~10 structurally different kinds
(board-write proposals/resolutions, fact-check claims/results with match
arrays, ask_user prompts, delegate handoffs, terminal-escalation banners, in
addition to plain messages) from more than two participants (lead + every
teammate + human) — bubble/alignment layout is built for two-party, purely
textual back-and-forth, which this isn't, and reworking it would risk 6f
part 2's own `role="log"`/`aria-live="polite"` accessibility contract for no
functional gain. Instead, human messages get a new, additive CSS
classification within the existing log-list:
```js
// In teamFeedEventKindClass(), right after the existing `if (e.kind ===
// 'error') return 'error';` check (app/app.py:3132) -- kind==='message'
// never matches any of the other branches below it, so this is safe to add
// anywhere before the final `return e.kind;` fallback; placed early to read
// as "most specific first", matching this function's existing style.
if (e.kind === 'message' && e.agent === 'human') return 'human-message';
```
`teamFeedEventBody()` needs **no change** — its existing catch-all
(`app/app.py:3216`, `if (e.kind === 'message' || e.kind === 'status') return
esc(e.text || '');`) already renders a human message's full text correctly;
the classification above only changes which CSS class
`renderTeamFeedEvent()` (`app/app.py:3219-3228`) attaches
(`'team-feed-event kind-human-message'`). No text prefix/decoration is
added — the row's existing `.team-feed-agent` span already colors `"human"`
distinctly via `teamAgentColor()` with zero code change; the new CSS class
adds a second, row-level visual cue (background tint or left-border accent,
following the same weight `.kind-error`/`.kind-terminal-escalation` already
have — exact color TBD by `ux-designer`/`docs/design.md`, using a hue that
doesn't collide with the existing red (`error`/`nomatch`) or orange
(`terminal-escalation`), e.g. this app's existing accent blue `#4da6ff`
already used for active filter pills).

`renderTeamFeed()`'s filter-pill agent list (`app/app.py:3233`) gains
`'human'`, positioned right after `'lead'`:
```js
const agents = ['lead', 'human'].concat((team.composition && team.composition.members) || []);
```
No new filtering code is needed — the existing generic `filter === 'all' ?
events : events.filter(e => e.agent === filter)` (`app/app.py:3241`) already
isolates exactly the human messages when `filter === 'human'`, since
`human.jsonl` only ever contains `agent: "human"` envelopes. The `human`
pill is shown unconditionally (even before any interjection has been sent
for this run), matching `lead`'s own existing "always present" behavior.

### 3. Escalation-panel coexistence
Already settled server-side in part 1 (`teams.interject()` explicitly
accepts `blocked_ask_user`/`blocked_board_write`); this part's only job is
to not contradict that in the UI. `teamAcceptsInterject()` (§1) already
returns `true` whenever `team.waiting_on_you` is true, so the compose box
and `renderEscalationPanel()`'s output render together, unconditionally,
whenever a question/proposal is pending — no additional gating, warning, or
confirmation step is added (the trust-direction question was already
settled in part 1: human → agent, immediate, no approval gate). The only
UI-level nod to the two boxes coexisting is the placeholder copy variant in
§1 (`"...this will not answer the pending question above"`), a soft nudge
rather than a hard gate, since typing an answer into the wrong box would
otherwise silently do the wrong (delayed, not immediately-actionable-as-an-
answer) thing.

### 4. Character/byte limits
Two constants exist server-side; they get different treatment here, and the
difference is a deliberate finding, not an inconsistency:
- **`TEAM_INTERJECT_MAX_CHARS`** (2000, a per-message character cap enforced
  at the route layer) — this is the one real, meaningful, client-
  pre-validatable constraint on THIS control. Surfaced via the live counter
  and disabled-Send logic in §1.
- **`TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND`** (64 KiB, a per-`team_step()`-round
  server-side drain cap — see `app/teams.py:2998-3018`) — investigated and
  found to have **no meaningful client-facing surface**: it bounds how many
  already-queued bytes one `team_step()` call reads out of `human.jsonl`
  into the lead's own round history in one go, not any single message's
  size (2000 chars is tiny relative to 64 KiB regardless), and it has zero
  effect on when a posted message becomes visible to the human in the feed
  (`_handle_team_events()` merges `human.jsonl` directly, independent of
  drain timing — §"Background"). Its only real-world effect is a narrow,
  invisible-to-the-UI delay (a message's *delivery to the lead* — not its
  visibility to the human — could, in an extreme multi-message pile-up,
  land one round later than usual); `tail_jsonl_events()` never truncates
  mid-message. No control is added for it (see "Non-goals").

## Affected areas
- `app/app.py` only — all within the existing inline `<style>`/`<script>`
  blocks:
  - New CSS: `.team-interject`, `.team-interject-row`,
    `.team-interject-textarea`, `.team-interject-counter` (+
    `.over-limit` modifier), `.team-feed-event.kind-human-message` — added
    near the existing `.team-escalation-*`/`.team-feed-*` rules
    (`app/app.py:2215-2277`), following their naming/sizing conventions.
  - New JS: `teamInterjectText` (state map), `TEAM_INTERJECT_MAX_CHARS_CLIENT`
    (constant), `teamAcceptsInterject()`, `renderTeamInterjectBox()`,
    `updateTeamInterjectControls()`, `doTeamInterject()`.
  - Modified JS: `actionPath()`, `actionBody()`, `handleActionResult()`
    (new `team-interject` kind branch + 428-label switch entry),
    `clearTeamFeedState()` (one new `delete`), `teamRow()` (one new
    `interjectBox` variable + insertion point), `teamFeedEventKindClass()`
    (one new early-return branch), `renderTeamFeed()` (one-token change to
    the `agents` array).
- No `app/teams.py` change. No new/changed route. No new data shape,
  migration, or config constant.

## Edge cases
- **Empty/whitespace-only or over-limit draft**: Send stays disabled
  client-side; if ever bypassed (e.g. a stale/hand-crafted request), the
  existing server-side 400 (`app/app.py:5399-5403`) renders in the same
  `.team-msg` slot via the generic 400 fallback, same as any other route.
- **Run transitions out of a compose-eligible status between polls** (e.g.
  finishes, is stopped, or is answered by another tab such that
  `waiting_on_you` flips false) while a draft is unsent: the next
  `teamRow()` re-render (every 4s poll) re-evaluates `teamAcceptsInterject()`
  fresh against the current `team` snapshot, so the compose box disappears
  and the draft is discarded — no attempt to persist/restore it, matching
  the existing behavior of every other per-status-scoped input in this app.
  If a brand-new run later starts for the same project, `teamInterjectText`
  has already been cleared, so no stale draft can reappear.
- **A message posted right as `waiting_on_you` flips (e.g., another
  tab/operator resolves the same escalation between this tab's polls)**:
  never turns a UI-permitted send into a server rejection —
  `teams.interject()` accepts `running` and both `blocked_*` statuses
  uniformly, and the compose box's own eligibility set is exactly that same
  set.
- **Two tabs/operators interjecting concurrently**: already proven safe
  server-side (part 1: independent, `PIPE_BUF`-safe file appends, no data
  loss); no client-side special handling is needed — each tab's own next
  poll picks up both messages, correctly ordered by the existing
  `(ts, agent, seq)` merge-sort regardless of which tab's poll observes them
  first.
- **A maximal-length (2000-char) human message in the feed**: no truncation
  — human messages get exactly the same no-length-cap rendering every other
  `message`/`status`-kind row already has; long text simply wraps within
  `.team-feed-list`'s existing scrollable container.
- **`human` filter pill clicked before any interjection has ever been sent
  for a run**: shows the existing "No events yet." empty state, same as any
  other filter with zero matching events — no special-case needed.
- **Double-click / rapid re-click on Send**: not newly protected against
  (see "Non-goals") — at most two independent, harmless queued messages;
  consistent with every other action button in this app today.

## Acceptance criteria
- [ ] Given `team.status === 'running'`, when `teamRow(name, team)` renders,
      then the compose box (`#interject-<name>` textarea + `#interject-send-
      <name>` button) is present in the output.
- [ ] Given `team.status === 'blocked'` and `team.waiting_on_you === true`
      (either `blocked_ask_user` or `blocked_board_write`), when
      `teamRow()` renders, then BOTH `renderEscalationPanel()`'s output and
      the compose box are present simultaneously.
- [ ] Given `team.status` is `'idle'`, `'finished'`, `'error'`, or
      `'blocked'` with `team.waiting_on_you === false` (escalated on max
      rounds), when `teamRow()` renders, then the compose box is absent —
      verified for each of these four cases individually.
- [ ] Given an empty or whitespace-only draft in `teamInterjectText[name]`,
      when `renderTeamInterjectBox()`/`updateTeamInterjectControls()` runs,
      then `#interject-send-<name>` has `disabled` set; given a draft longer
      than 2000 characters, then Send is also disabled and
      `#interject-counter-<name>` carries the `over-limit` class.
- [ ] Given a non-empty, ≤2000-character draft, when the operator clicks
      Send, then `doTeamInterject()` calls `toggle('team-interject', name,
      true, null)`, which POSTs `{"text": "<trimmed draft>"[, "code": ...]}`
      to `/projects/<name>/team/interject` via the same 428-retry flow
      `team-resolve` uses, with the code-overlay label reading `"Sending
      message: <name>"` during a retry.
- [ ] Given a `{"ok": true, ...}` response, when `handleActionResult()`
      processes it, then `#team-msg-<name>` shows `"✓ Message sent"`
      (`success` class), `#interject-<name>`'s value and
      `teamInterjectText[name]` are both cleared, and Send becomes disabled
      again.
- [ ] Given an `{"error": "..."}`, 400 response, when `handleActionResult()`
      processes it, then `#team-msg-<name>` shows `"✕ Error: <server
      message>"` (`error` class) and the draft text in
      `teamInterjectText[name]`/the textarea is preserved, not cleared.
- [ ] Given a successful send, when the next `pollTeamFeed()` cycle runs,
      then the new event (`agent: "human"`, `kind: "message"`) appears in
      `teamFeedEvents[name]` and `teamFeedEventKindClass()` returns
      `'human-message'` for it (verified by unit-level call, and by the
      rendered row carrying CSS class `team-feed-event kind-human-message`).
- [ ] Given a project with `team.composition.members` non-empty, when
      `renderTeamFeed()` builds its filter pills, then the pill order is
      `All, lead, human, <member1>, <member2>, ...`, and clicking the
      `human` pill filters `teamFeedEvents` to exactly the events with
      `agent === 'human'` via the existing generic filter (no new filtering
      code path exercised).
- [ ] Given an unsent draft and a status transition to a
      compose-ineligible status (e.g. `running` → `finished`) on the next
      poll, when `teamRow()` re-renders, then the compose box disappears and
      `teamInterjectText[name]` is deleted (verified: does not reappear with
      stale content if a new run later starts for the same project).
- [ ] `git diff` for this cycle touches only `app/app.py` — no
      `app/teams.py`, route, config, or data-shape change.

## Open questions
- **Chat-bubble vs. distinct-row-style (settled, not left open):** decided
  against a bubble reskin; see "Proposed approach" §2 for the full
  reasoning (multi-party, structurally heterogeneous content, existing
  accessibility contract). Exact color/border treatment for
  `kind-human-message` is left to `ux-designer`'s `docs/design.md` pass —
  the functional classification (which events get the new class) is settled
  here.
- **Escalation-panel coexistence (settled, not left open):** compose box and
  escalation panel always render together when `waiting_on_you` is true, no
  hard gate, only a softer placeholder-copy nudge — see "Proposed approach"
  §3.
- **`TEAM_INTERJECT_MAX_CHARS_CLIENT` hardcoded to 2000 (settled, flagged,
  not blocking):** mirrors `team-resolve`'s own existing pattern; will
  silently drift if the server-side env var is ever overridden away from its
  default. Pre-existing gap class, not newly introduced, not fixed here —
  server-side validation remains authoritative regardless.
- **No UI surface for `TEAM_HUMAN_MSG_MAX_BYTES_PER_ROUND` (settled, not
  left open):** investigated and found to have no meaningful per-message,
  client-validatable constraint to surface — see "Proposed approach" §4.
- **No Enter-to-send (settled, not left open):** consistency with every
  other multi-line free-text input in this app; see "Non-goals".
- **Relationship to items 7/8's trust model:** restating part 1's own
  already-settled conclusion for completeness at the UI layer — this
  remains human → agent (not agent → external system), so no approval/
  confirmation step is added on top of the compose box's own Send action,
  unlike items 7/8's propose-then-approve model.

## Risk / rollback notes
- Purely additive, front-end-only change confined to `app/app.py`'s existing
  inline `<style>`/`<script>` blocks — no schema, migration, route, or
  backend-behavior change. A project that never uses the compose box is
  visually and behaviorally unaffected except for the always-present
  (but functionally inert until clicked) `human` filter pill.
- Rollback is reverting the `app/app.py` diff; nothing to migrate, no data
  written by this part that outlives a single render cycle (the compose
  box's own draft state is entirely client-side and ephemeral).
- Worst-case failure modes: (a) the compose box renders in a status where
  the server would reject it — still safely caught by `teams.interject()`'s
  own existing status check (part 1), surfacing as an inline 400, not a
  crash or data issue; (b) it fails to render in a status where sending
  should be allowed — a missed capability for that render, not a
  correctness or data-safety bug, and self-heals on the very next poll once
  `team.status`/`waiting_on_you` next matches `teamAcceptsInterject()`.
