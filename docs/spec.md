# Spec: 6f part 2 — the Teams page (live event feed, per-agent filter, status strip, escalation inbox)

## Summary
Extend the existing per-project team row's non-idle branch (`app/app.py`'s
`teamRow()`, `team.status !== 'idle'` case) with a live, colour-coded,
filterable merged event feed, a clearer status strip (idle / working /
blocked / waiting-on-you), and a structured escalation-answer panel — built
entirely against 6f part 1's already-shipped, already-tested route
contracts (`GET .../team/events`, `GET .../team/inbox`,
`POST .../team/resolve`, `/status`'s `waiting_on_you`). No backend changes.

## Goals
- Replace the current static `Status: [blocked]` line + one-line
  `"Lead is waiting for input · check tmux attach"` sub with a status strip
  that always shows one of four states — **Idle / Working / Blocked /
  Waiting on you** — and makes "waiting on you" impossible to miss.
- Render a merged, chronologically-sorted, colour-coded-per-agent timeline
  over the lead's transcript and every teammate's own event log, polled
  live via `GET .../team/events`'s cursor protocol.
- A per-agent filter (All / lead / each teammate by name) over that feed.
- `fact_check` calls render their claim and, on the matching result, the
  passage text and `file:line` the lead was actually shown — not a raw
  JSON blob.
- An escalation (`waiting_on_you === true`) renders the pending question,
  its header, and 2–4 pickable options, **plus a free-text "Other" input
  that is always present** regardless of how the lead framed the question.
  Submitting resolves it via `POST .../team/resolve`, TOTP-gated exactly
  like every other state-changing action in this UI (reusing the existing
  `toggle()` / code-overlay plumbing, not a new gate).
- The feed reloads its full available history on a page reload (starting
  from `cursor={}`) rather than depending on any in-memory client state —
  satisfies "survives a page reload" without a persistence layer.
- Feed polling stays bounded and responsive on a long-running team: never
  read a file whole, always resume from the last cursor, and drain a
  `truncated: true` file promptly rather than lagging behind.

## Non-goals
- Any backend/route change. `app/app.py`'s three `team/*` routes and
  `/status`'s `waiting_on_you` field are consumed exactly as shipped in 6f
  part 1 (see "Background" below for the exact contracts).
- `docs/BACKLOG.md` item 11(b) — `run_id` not validated against path
  traversal on the three `team/*` routes. Unrelated to this UI work
  (an input-validation gap on routes this cycle only calls, never adds to),
  stays in the backlog for a future cycle.
- A full log-browser/search experience. The feed is a live tail with a
  bounded client-side rolling window (see "Proposed approach"), not an
  archive viewer with scrollback search — a long-running team's full
  history remains inspectable via `tmux attach` or the raw `.jsonl` files
  on disk, same as today.
- Any new page/route/URL. This stays a single-page app; the feed lives
  inside the existing `teamRow()` render, matching every prior sub-spec's
  own correction of "Teams page" wording (see "Background").
- Automatically merging or discarding teammate worktrees. Unrelated to
  this cycle (§3 open question in `docs/story.md`, still deferred).
- Multi-select composition of the resolve answer beyond simple
  label-joining (see "Proposed approach" — one reasonable, stated
  convention, not a rich answer-composition UI).

## Background / current state
**Single-page app, not multi-page.** `app/app.py`'s `Handler.do_GET` only
ever serves one HTML document at `self.path == "/"` (`PAGE_TEMPLATE`,
~line 1636). There is no per-page routing anywhere in this codebase.
Story.md's "Teams page" wording is, per 6d/6e/6f part 1's own precedent,
an expansion of the existing per-project `teamRow()` render
(`app/app.py`, currently ~line 2237), not a new URL.

**Where this goes.** `teamRow()`'s `team.status !== 'idle'` branch
(currently the tail of the function, ~line 2272-2283) renders:
```js
const sub = team.status === 'blocked' ?
  '<div class="team-sub">Lead is waiting for input · check tmux attach</div>' : '';
return '<div class="team-row">' +
  '<div class="team-status status-' + esc(team.status) + '">Status: [' + esc(label) + ']' +
  (team.run_id ? '&nbsp;&nbsp;&nbsp;ID: ' + esc(team.run_id) : '') + '</div>' +
  sub +
  '<div class="team-actions"><button class="team-btn" onclick="doTeamStop(...)">Stop team</button></div>' +
  msgSlot + '</div>';
```
This is exactly where the status strip, feed, and escalation panel replace
the current `sub` line and add new content below the status line.

**Route contracts already shipped and tested (no backend change needed):**
- `GET /projects/<name>/team/events?run_id=&cursor=` →
  `{"run_id", "events": [...], "cursors": {"<agent>": <byte_offset>},
  "truncated": {"<agent>": true}}` (`app/app.py:3637-3666`). `cursor` is a
  URL-encoded JSON object `{"<agent>": <byte_offset>}`
  (`_parse_events_cursor()`, `app/app.py:1388`); a malformed cursor
  degrades to `{}`, never a 400. Each event is the §4.1 envelope
  `{ts, agent, seq, kind, text, meta}`, `kind` ∈
  `message|tool_use|tool_result|status|error|handoff`. Bounded per file
  per poll by `teams.TEAM_EVENTS_MAX_BYTES_PER_FILE_PER_POLL` (64 KB
  default) — a `truncated[agent] === true` response means that file has
  more data waiting; the client must re-poll with the returned cursor to
  drain it. `agent` is `"lead"` for the lead's own transcript, or one of
  `state["members"]`'s plain name strings for a teammate.
- `GET /projects/<name>/team/inbox?run_id=` → `{"pending": false}` or
  `{"pending": true, "run_id", "question", "header", "options":
  [{"label","description"}...], "multi_select"}` (`app/app.py:3668-3701`).
  Always has a non-empty `question`, even when `inbox.json` itself is
  unreadable (a safe fallback string is substituted server-side).
- `POST /projects/<name>/team/resolve` body `{"run_id"?, "answer",
  "code"}` (TOTP-gated, same mechanism as `team-start`/`team-stop`) →
  `{"ok": true, "run_id"}` or `{"error": "..."}` — 400 on no pending
  question, or an empty/over-`TEAM_ASK_USER_ANSWER_MAX_CHARS` (2000
  default) answer (`app/app.py:3873-3920`). The body takes a single
  `answer` string, not a structured selection — see "Proposed approach"
  for how a picked option becomes that string.
- `GET /status`'s per-project `team` object already carries
  `{"status", "run_id", "composition", "waiting_on_you"}`
  (`app/app.py:3558-3560`). `waiting_on_you` is `true` iff
  `run["status"] == "blocked_ask_user"` — the cheapest signal for the
  status strip's fourth state; **do not poll `/team/inbox` just to light
  that indicator**, only fetch it once `waiting_on_you` is actually true
  and the question isn't already loaded. `composition.lead` is
  `{"kind","name"}`, `composition.members` is a plain list of name
  strings — this is already the exact agent-name list the per-agent
  filter needs, with no extra request.
- **`team.status` (the coarse UI bucket) collapses two backend statuses
  into `"blocked"`**: `run["status"] == "blocked_ask_user"` (resolvable —
  `waiting_on_you` is true) and `run["status"] == "escalated_max_rounds"`
  (a **terminal** status, `TEAM_MAX_ROUNDS` exhausted, no `inbox.json`,
  nothing to resume — `waiting_on_you` stays false). The status strip and
  escalation panel must distinguish these (see "Edge cases").

**Existing TOTP action plumbing to reuse, not reinvent** (`app/app.py`
~line 2310-2469): `actionPath(kind, name, on)`, `actionBody(kind, name, on,
code)`, `handleActionResult(r, ctx)`, and `toggle(kind, name, on,
checkboxEl)` already implement the "attempt without a code, 428 means show
the overlay, 403 means wrong code, 401 means session expired" flow used by
every existing action (`team-start`, `team-stop`, `deploy`, etc.). A new
`kind === 'team-resolve'` branch follows the same three functions' existing
if/else-chain shape exactly (see `team-start`'s own branches for the
pattern to copy), with its own inline result slot in
`handleActionResult()` (mirroring `team-start`/`team-stop`'s own
`if (kind === 'team-start' || kind === 'team-stop') { ... }` block).

**Existing dark-theme tokens to reuse** (`app/app.py` CSS, ~line
1717-1744): `.team-status.status-running` `#4da6ff`, `.status-blocked`
`#ffb648`, `.status-finished` `#34c759`, `.status-error` `#ff6b6b`;
`.team-sub` `#888` on `#1c1c1c` (documented AA-only, 4.81:1, per 6d part 2
carried-forward note); page background `#111`, card background `#1c1c1c`,
body text `#eee`. `.wizard-card`'s `max-height: 85vh; overflow-y: auto`
(~line 1783) is the existing precedent for a scrollable panel — reuse that
pattern for the feed's own scroll container rather than inventing a new
one. There is no existing monospace/log-styling precedent in this
codebase; the feed is the first scrollable log-like panel, so
ux-designer should pick a font/line-height for event text that's legibly
distinct from the rest of the page's `-apple-system, sans-serif` body
copy without introducing a whole new typography system.

**`fact_check` events have no explicit tool name in their envelope** —
found during this cycle's archaeology, not previously documented. The
`meta` shapes that DO unambiguously identify an event's origin:
- `kind: "handoff"`, `meta: {"agent": "<name>"}` — a delegation being
  handed to a teammate; the paired `kind: "tool_result"`,
  `meta: {"agent", "ok", "log_path"}` is that delegation's result.
- `kind: "tool_result"`, `meta: {"found": true|false}` — a `fact_check`
  result. `text` is a JSON string: `{"claim", "found",
  "matches": [{"label","path","relpath","line","file_line","text",
  "end_line"}, ...]}` (`app/teams.py`'s `fact_check()`, ~line 1736-1769).
  `matches[i].file_line` is the pre-formatted `"<label>:<line>"` string;
  `matches[i].text` is the passage itself.
- `kind: "tool_result"`, `meta: {"resolved": true}` — a human's accepted
  answer to a resolved `ask_user` (6f part 1b's fix: this now only ever
  appears for the winning resolve, never a stray loser).
- `kind: "tool_use"`, `meta: {"header": "..."}` — an `ask_user` question
  being raised.
- `kind: "error"`, `meta: {"forced": true}` — a forced escalation (retry
  budget exhausted).
- `kind: "status"`, `meta: {"forced": true, "final_status": "..."}` — the
  terminal `TEAM_MAX_ROUNDS`-exhausted escalation.
- `kind: "tool_use"`, `meta: {}` (empty) is genuinely ambiguous between a
  `fact_check` claim and a `finish` summary — **both produce this exact
  shape** (`app/teams.py` ~line 2811 vs ~2823). Disambiguate positionally,
  not by content: if the *immediately following* event (next `seq` for
  `agent: "lead"`) is `kind: "tool_result"` with `meta.found` present,
  render this pair as a fact_check block (claim + result). Otherwise (no
  following lead event — this is necessarily the run's last transcript
  entry, since `finish` ends the loop) render it as the run's finish
  summary. This is deterministic given the backend's current, unchanging
  behaviour (finish always terminates the loop immediately).
- Every other `kind: "status"`/`kind: "message"` event (native per-engine
  stream translation — `native_type` in `meta`, e.g. Claude's `system`/
  `init` events, Codex's `thread.started`, etc.) is lower-signal
  bookkeeping, not one of the four lead tools — render generically by
  `kind`, de-emphasized relative to `message`/`tool_result`/`error`/
  `handoff`, no special-casing needed per `native_type`.

## Proposed approach
1. **Status strip.** Replace the current `sub` computation in the
   non-idle branch with a 4-state strip:
   - `idle` — unreachable in this branch (idle has its own render path);
     not applicable here.
   - `running` → **Working**.
   - `blocked` + `team.waiting_on_you === true` → **Waiting on you**
     (highest-priority visual — this is the state the acceptance
     criteria call out as "impossible to miss"; escalation panel below
     the strip is where the actual question renders).
   - `blocked` + `team.waiting_on_you === false` → **Blocked** with
     distinct copy, e.g. "Escalated — max rounds reached, no pending
     question to answer. Review the feed below or `Stop team` and start a
     new run." (this is `escalated_max_rounds`, terminal — see "Edge
     cases"). Do not show the answer form for this case.
   - `finished` → **Finished**, `error` → **Error** (existing labels/
     colours, unchanged).
2. **Escalation panel.** Rendered only when `team.waiting_on_you ===
   true`. On first render for a given `run_id` (or whenever the panel
   would otherwise be empty), fetch `GET .../team/inbox?run_id=` once and
   cache the result client-side keyed by `run_id`, not re-fetched on every
   poll tick. Render `question`, `header` (as a small chip), each
   `options[i]` as a radio (single_select) or checkbox (`multi_select`)
   with its `label`/`description`, **plus a free-text "Other" input
   always present** below the options regardless of `multi_select`.
   Submitting: build `answer` as follows — free-text "Other" filled in
   takes precedence and is sent verbatim; otherwise, for `multi_select:
   false` send the chosen option's `label`; for `multi_select: true`,
   join the chosen options' `label`s with `", "`. This is a deliberate,
   stated convention (flagged in "Open questions" as a reasonable default
   under `/team/resolve`'s plain-string contract, not a blocking
   decision). Submit via a new `kind === 'team-resolve'` case through the
   existing `actionPath`/`actionBody`/`toggle`/`handleActionResult` chain
   (TOTP-gated identically to `team-start`/`team-stop`); on success, clear
   the cached inbox question and let the next poll pick up the new
   `team.status`.
3. **Merged event feed.** A collapsible panel (same `team-configure-btn`/
   toggle-link idiom as 6e's "Configure team..." — e.g. "Show live
   feed"/"Hide live feed") below the status strip, expanded by default
   whenever `team.status !== 'idle'` (this is now the primary way to
   observe a running team; `tmux attach` remains available but is no
   longer the first thing an operator reaches for) and collapsible to
   reduce clutter on a page with several projects running teams at once.
   - Client-side per-project state: `teamFeedOpen[name]`,
     `teamFeedCursor[name]` (the `{agent: offset}` object from the last
     poll response, starts at `{}` on first open and on every full page
     load — this alone satisfies "survives a page reload, rehydrated from
     files"), `teamFeedEvents[name]` (rolling buffer, see below),
     `teamFeedFilter[name]` (`"all"` or an agent name).
   - Polling: folded into the existing `refresh()` 4-second cycle (no new
     `setInterval`) — for each project whose `team.status !== 'idle'`
     AND whose feed panel is open, call `GET .../team/events` with the
     cached cursor. Append returned `events` (already server-sorted) to
     the client buffer, keyed and re-sorted by `(ts, agent, seq)` to
     merge safely even if two agents' events arrive slightly out of
     `ts` order across polls. Update the cursor to the response's
     `cursors`. **If any `truncated[agent] === true`, immediately issue
     another `/team/events` call for that project with the updated
     cursor** (don't wait for the next 4s tick) — loop until no file
     reports truncated, so a burst of activity drains within about a
     poll round-trip, not up to 4 seconds behind.
   - Rolling window: keep at most the most recent 500 events per project
     in the client buffer (oldest trimmed once the cap is exceeded); this
     is a live tail, not a full-history browser (see "Non-goals"). Cursor
     tracking is unaffected by trimming — trimming only affects what's
     rendered, never what's been fetched.
   - Per-agent filter: a small tab/pill row — "All", then one entry per
     `["lead"].concat(team.composition.members)` (already available from
     `/status`, no extra request) — filters the rendered (not fetched)
     buffer by `event.agent`.
   - Per-event rendering by `kind`+`meta` per the disambiguation rules in
     "Background" above. Colour-code by `event.agent` (one qualitative
     colour per agent, stable across polls/reloads — e.g. hash the agent
     name to a small fixed palette — kept visually distinct from the
     existing semantic status colours `#4da6ff`/`#ffb648`/`#34c759`/
     `#ff6b6b` so agent identity is never confused with run status).
4. **`fact_check` rendering**, per the disambiguation rule above: render
   the claim (`tool_use.text`) as a labeled line ("fact_check: <claim>"),
   then on the paired `tool_result`, if `found`, list each match as
   `file_line — text` (the passage, truncated to a reasonable on-screen
   length with the full text available on hover/expand if
   ux-designer prefers); if not found, render "no supporting passage
   found" (mirrors 6b's own `fact_check()` contract — matches this
   codebase's existing "explicit non-match text, never a silent empty
   result" convention).

## Affected areas
- `app/app.py` — `teamRow()`'s non-idle branch (feed/status-strip/
  escalation render), `actionPath`/`actionBody`/`handleActionResult`/
  `toggle` (new `team-resolve` kind), new client-side JS state
  (`teamFeedOpen`/`teamFeedCursor`/`teamFeedEvents`/`teamFeedFilter`,
  a `pollTeamFeed(name)` helper called from `refresh()`), new CSS classes
  for the feed panel/status strip/escalation form (no new CSS classes
  beyond what's needed to render these — following the existing BEM-lite
  naming 6e's design.md established, e.g. `.team-feed`, `.team-feed-event`,
  `.team-escalation`).
- No `app/teams.py` change, no route change, no new tests needed in
  `tests/test_team_routes.py` (unchanged backend) — new/changed coverage
  belongs in `tests/test_team_frontend.js` (the existing frontend test
  file, per 6e's own precedent for JS-side logic).
- `docs/design.md` — a new "Overwatch feed + escalation inbox (sub-spec
  6f part 2)" section, appended (matching every prior sub-spec's own
  append-not-rewrite convention for this file).

## Edge cases
- **`blocked_ask_user` vs `escalated_max_rounds`** (both bucketed as
  `team.status === "blocked"` by `/status`): only the former has
  `waiting_on_you === true` and a live `inbox.json` to answer. The latter
  is terminal — the escalation panel must not render an answer form for
  it, and the status strip's copy must say so explicitly (see "Proposed
  approach" step 1) rather than showing a generic "blocked" label that
  implies answering will help.
- **`GET .../team/inbox` returns a fallback question when `inbox.json` is
  unreadable** (`app/app.py:3696-3699`, already server-handled) — the
  panel renders this fallback text with zero `options` and just the
  free-text "Other" input, which the UI already supports unconditionally.
  No special-casing needed on the frontend.
- **A team with zero events yet** (just started, nothing logged) — feed
  panel shows "No events yet" rather than an empty scroll area.
- **Feed panel closed while a team keeps running** — no polling happens
  for that project's events (per "Proposed approach" step 3's gating);
  reopening it starts from cursor `{}` again (a fresh full-history replay,
  bounded/paginated the same way a reload is) rather than trying to
  resume a stale cursor from before it was closed — simpler, and
  consistent with the reload behaviour already required.
- **Two tabs/windows open on the same project** — each has its own
  independent cursor state; both converge to the same rendered content
  over a few polls, same as the rest of this app's existing no-shared-
  client-state design (e.g. `pendingToggle`).
- **Switching the per-agent filter does not reset or refetch** — it only
  changes which already-fetched events are rendered; the cursor and
  polling are filter-independent.
- **A team stops (`team.status` flips to `idle`)** — the feed/escalation
  panel and their client-side state (`teamFeedEvents[name]` etc.) are
  cleared when the row re-renders back into the idle branch, consistent
  with 6d/6e's own precedent that the idle branch is a different render
  path with no team-run-specific state carried over.
- **Malformed/oversized answer submission** — the existing 400 handling
  path (`answer must be non-empty and at most 2000 characters`) surfaces
  in the panel's own inline message slot (mirroring `team-msg`'s existing
  error styling), not a generic alert.
- **`run_id` mismatch / stale run** — `POST .../team/resolve` without a
  `run_id` always resolves the project's current latest run
  (`app/app.py:3890-3894`); the panel never needs to track/send `run_id`
  itself as long as it only renders when `team.waiting_on_you` (which is
  always about the current run) is true.

## Acceptance criteria
- [ ] Given a team whose status is `running`, when the row renders, then
      the status strip shows **Working** (not the old static
      "Status: [running]" wording alone).
- [ ] Given a team whose `run["status"] == "blocked_ask_user"`
      (`waiting_on_you: true`), when the row renders, then the status
      strip shows **Waiting on you** and the escalation panel renders the
      question, header, options (radio for `multi_select: false`,
      checkboxes for `true`), and a free-text "Other" input — always,
      even when `options` is empty.
  - [ ] Given a team whose `run["status"] == "escalated_max_rounds"`
      (`waiting_on_you: false`), when the row renders, then the status
      strip shows a distinct **Blocked** state with terminal-escalation
      copy, and no answer form is rendered.
- [ ] Given a running team with events in both the lead's transcript and
      at least one teammate's log, when the feed panel is open, then
      events from both appear merged in a single chronologically-ordered
      list, each visually colour-coded by `agent`.
- [ ] Given the per-agent filter set to a specific teammate's name, when
      the feed re-renders, then only that agent's events are shown; "All"
      restores the merged view.
- [ ] Given a `fact_check` tool_use/tool_result pair in the transcript,
      when rendered, then the claim text and, for a `found: true` result,
      each match's `file_line` and passage text (`matches[i].text`) are
      visibly shown — not a raw JSON string.
- [ ] Given a `fact_check` with `found: false`, when rendered, then the
      feed shows an explicit "no supporting passage found", not an empty
      or blank entry.
- [ ] Given the operator submits an answer (an option, multiple options
      for a `multi_select` question, or free-text "Other"), when
      `POST .../team/resolve` returns `{"ok": true}`, then within one
      subsequent poll interval the status strip transitions away from
      "Waiting on you" and the escalation panel is dismissed.
- [ ] Given a reload of the page mid-run, when the feed panel is reopened,
      then it repopulates from `cursor={}` and shows the same events as
      before the reload (bounded by however many polls it takes to drain
      any `truncated` files), not an empty panel.
- [ ] Given a poll response with `truncated[agent] === true` for any
      agent, when the client handles that response, then it issues an
      immediate follow-up `/team/events` call (not waiting for the next
      4-second tick) until no file reports truncated.
- [ ] Given a `POST .../team/resolve` requiring TOTP that hasn't been
      cleared this session, when submitted, then the existing code-overlay
      flow (428 → overlay → retry) fires identically to `team-start`/
      `team-stop`'s own existing behaviour — no new gating mechanism.
- [ ] Given a project whose team is `idle`, when the row renders, then no
      feed/status-strip/escalation UI is rendered (unchanged from 6d/6e's
      idle-branch behaviour).
- [ ] Given more than 500 events accumulate in one project's feed buffer
      client-side, when the buffer is trimmed, then the cursor/polling
      continue unaffected (verified by trimming not causing duplicate or
      skipped events on the next poll).

## Open questions
- **How a picked option (or several, for `multi_select`) becomes the
  single `answer` string `POST .../team/resolve` expects.** Proceeding
  under the assumption stated in "Proposed approach" step 2: free text
  wins if filled in, otherwise the chosen option's `label`(s) joined with
  `", "` for multi-select. This is a UI-only convention with no backend
  implication either way (the lead just receives whatever string is
  sent); flag if a different join/format is preferred, but it isn't a
  blocking decision.
- **Exact per-agent colour palette and feed panel typography** — left to
  ux-designer's `ui-ux-pro-max` pass; this spec only constrains that
  agent-identity colours must be visually distinct from the four existing
  semantic status colours (`#4da6ff`/`#ffb648`/`#34c759`/`#ff6b6b`) and
  that WCAG AA contrast against the existing `#1c1c1c` card background is
  maintained for all new text (matching every prior sub-spec's own bar —
  `docs/design.md`'s carried-forward note on `.team-sub`'s 4.81:1 is the
  one existing near-miss to be careful not to repeat).
- **Feed panel default expanded-vs-collapsed state** — proceeding under
  "expanded by default whenever a team is non-idle" (stated in "Proposed
  approach" step 3) since the feed is this cycle's headline deliverable
  and the acceptance criteria treat live visibility as the point; a
  reasonable alternative (collapsed by default, matching 6e's "Configure
  team..." precedent) is not blocking and can be swapped by
  ux-designer/developer if there's a strong reason, since it's a pure
  rendering default with no data-shape implication.

## Risk / rollback notes
Purely additive frontend change on top of an unmodified, already-tested
backend — no data model, no route, no route contract changes. Risk is
concentrated in the feed's JS state management (cursor tracking, rolling
buffer, truncation-drain loop) and the fact_check disambiguation rule,
both client-side only. Rollback is reverting the `app/app.py` template
diff; nothing persisted server-side changes shape, so no migration or
data cleanup is needed either way.
