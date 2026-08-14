# Backlog

Forward-looking feature ideas, captured for a future session — not yet
speced, designed, or built. Each item below has enough shape to start a
`docs/spec.md` from when it's picked up, plus the scope decisions already
settled so that session doesn't have to re-litigate them.

Suggested rough order: items 4 and 5 are small, low-risk, and independent —
good warm-up work. Items 1 and 2 (Taiga, Gitea) are same-shape "new optional
self-hosted service with its own container page," best done back-to-back
since they share a pattern. Item 3 (folder upload) is a self-contained
`app.py` feature. Item 6 (multi-agent orchestration) is the largest and most
architecturally novel — do it last, and give it a full spec of its own
before touching code.

Items 7 and 8 both **depend on item 6 being substantially done** and should
not be started before it: item 7 extends 6c's lead loop with board tools,
and item 8 reuses 6's roster for its "selectable model". Both also widen
what an agent may change without a human in the loop, so both carry a scope
decision to put to the user before building rather than after.

---

## 1. Local backlog tracker (Taiga), tracked by Claude

**Status: shipped in full (2026-08-12/13), both 1a and 1b.**
- **1a**: `install.sh --with-taiga` (Taiga via its official `taiga-docker`
  Docker Compose stack, this codebase's first-ever Docker dependency, off
  by default) plus a singleton toggle row in the web UI. Reviewer-approved
  after three review rounds that caught and fixed two real races in the
  frontend toggle-off state machine plus one follow-up leak fix.
- **1b**: `scripts/taiga_push_spec.py` + `scripts/taiga-configure-push.sh`
  — a one-way (spec → Taiga) CLI tool any agent with shell access can
  invoke to push a local spec into a Taiga backlog item as a userstory.
  Reviewer-approved clean on the second pass (one must-fix uncaught-
  exception defect found and fixed on the first pass; two cosmetic nits
  left unfixed, non-blocking). Wiring this into `product-manager`'s own
  standing instructions (the cross-repo `D:\Entwicklung\.claude` side) is
  explicitly out of scope — this repo only delivers the mechanism.
- Full spec/implementation/test-review for **1b specifically** (the current
  state of these files) in `docs/spec.md` / `docs/implementation.md` /
  `docs/test-review.md`; 1a's own versions of those files are preserved in
  git history at commit `ed84d73`.

**Decision:** Taiga (agile/scrum, kanban + sprints), not Atlassian Jira —
self-hosted Jira now requires a paid Data Center license, which doesn't fit
this project's "small, self-hosted, free" model. Taiga is the closer match
to what "local Jira" was reaching for.

**Shape of the work:**
- New optional install flag, e.g. `install.sh --with-taiga`, following the
  same opt-in pattern as `--with-git-hosting` and `--with-code-server`.
- Taiga runs as its own set of containers/services (it's a multi-service
  app: Django backend, RabbitMQ, Postgres, frontend) — needs a resource-cost
  callout in the setup prompt, similar in spirit to the GitLab-vs-Gitea
  tradeoff below.
- **"Claude should track it"**: Taiga has a REST API. The natural
  integration point is a new MCP server or a small script wrapper the
  product-manager agent can call to read/write backlog items, so specs
  written by `product-manager` (per the global 4-subagent workflow) can
  originate from, or sync back to, real Taiga tickets instead of living only
  in `docs/spec.md`. Needs a decision later on direction of sync
  (Taiga → spec, spec → Taiga, or both) and auth (API token storage,
  presumably in `switchboard.env`-style config).
- **"Own page in Container"**: gets a row in the web UI the same way
  code-server does — on/off toggle + link, not a full project-per-row like
  the engine sessions.

**Open for the future session:** sync direction between Taiga and
`docs/spec.md`; whether one shared Taiga project covers all switchboard
projects or one per project folder.

---

## 2. Local git hosting UI + CI/CD (Gitea)

**Status: 2a and 2b both shipped (2026-08-13).** 2a: `install.sh --with-git-hosting` now also installs
Gitea (Postgres-backed, per user confirmation) via a directly-authored
Docker Compose stack, off by default, plus a singleton toggle row in the
web UI reusing (now generalized) the same toggle-state machine 1a's Taiga
row hardened across three review rounds. Reviewer-approved after one fix
round (a printed admin-account-creation command that failed as originally
written). 2b: `create_project()` in `app.py` now creates real repos
through Gitea's REST API (`POST /user/repos`), backed by a new one-time
token-bootstrap script (`scripts/gitea-configure-api.sh`) and a new
privileged clone hand-off (`scripts/new-project-from-gitea.sh`); the six
legacy git-hosting scripts (`git-hosting-setup.sh`, `new-repo.sh`,
`new-dev-instance.sh`, `new-project.sh`, `project-sync.sh`,
`target-setup.sh`) and `config/git-hosting.env.example` have been retired
from `install.sh`. Reviewer's live testing against a real Gitea 1.27.1
instance caught one must-fix defect (a same-second token-name collision in
`gitea-configure-api.sh`'s re-run safety) — fixed with a random suffix and
approved on re-review (verified live across 35 runs with zero collisions,
including runs that genuinely shared the same Unix second). Full
spec/implementation/test-review for **2a specifically**
preserved in git history at commit `dcc582b`; **2b specifically** at commit
`5a59d21`.

**2c part 1 shipped (2026-08-13)** — `app.py` now polls Gitea's REST API
(piggybacked on `/status`, throttled to its own `GITEA_POLL_INTERVAL_SECONDS`
interval, default 45s — no webhook, no new listener, no Docker networking
changes; an earlier webhook-based design was rejected by the user for
introducing exactly that kind of new attack surface) and, when a
Gitea-backed project's default branch moves, safely fast-forwards
`PROJECTS_DIR/<name>` — never destructive: skips (and records why) on a
dirty working copy or diverged local history, never `git reset --hard`.
Reviewer approved with one should-fix follow-up (a malformed API response
could silently kill polling for every other project in that pass) — fixed
directly, verified load-bearing, 215/215 tests pass. Full
spec/implementation/test-review for **2c part 1 specifically** in the
current `docs/spec.md` / `docs/implementation.md` / `docs/test-review.md`.
**2c part 2 shipped (2026-08-13), in two sub-parts, both manual-trigger —
NOT the auto-deploy-off-the-poll shape "Shape of the work" below originally
described.** Per an explicit user decision made when part 2 was picked up:
a push landing (part 1's poll/sync) never itself deploys anything; deploy
only ever fires from a human clicking a button. **2c part 2a**
(`deploy-target/`) is a receiver-only install (`install.sh
--with-deploy-target`, run on a *separate* target machine): a narrowly
scoped `deploy` system user whose forced-command SSH key can only
write-only-`rsync` into one pre-configured path or trigger one fixed
`systemctl restart` of one named service — no shell, ever. **2c part 2b**
(`app.py`) is the switchboard-side caller against that receiver: a
hand-edited, operator-maintained `deploy-map.json` (project name → target
host/path/service/key, never written by `app.py`, only read), a
`deploy_run()` dispatch function that pushes `PROJECTS_DIR/<name>` via
`rsync` and triggers the target's restart over a second SSH connection
(synchronous, request-thread, mirrors `host_run()`'s own shape), and a
per-project "Deploy" button in the web UI gated behind a native
`confirm()` dialog. Full spec/implementation/test-review for **2c part 2b
specifically** in the current `docs/spec.md` / `docs/implementation.md` /
`docs/test-review.md`; 2c part 2a's own versions of those files (plus
`deploy-target/README.md`) are preserved in git history at commit
`63db0a7`.

**Decision:** Gitea, not full GitLab CE. GitLab's resource footprint (own
Postgres, Redis, multiple worker processes, several GB RAM minimum) cuts
against this project's stated philosophy of staying small and self-hosted
on modest homelab hardware. Gitea gives the same practical wins — real web
UI, PRs/MRs, built-in Actions-compatible CI — at a much lighter footprint,
and is a more natural sibling to the existing `scripts/git-hosting-setup.sh`
approach than a GitLab swap would be.

**Decision:** this **replaces** the current lightweight git-shell +
bare-repo + rsync setup (`docs/GIT_HOSTING.md`), not an addition alongside
it — avoids maintaining two parallel git-hosting stories.

**Shape of the work:**
- Gitea install step folds into (or replaces) `scripts/git-hosting-setup.sh`
  under the existing `install.sh --with-git-hosting` flag.
- ~~Auto-deploy: Gitea Actions (or a `post-receive`-equivalent webhook) does
  the job the current `project-sync.sh` + `post-receive` hook combo does
  today — push to `main` → rsync/deploy to target → target restarts its
  service.~~ **Superseded by 2c part 2's actual shipped shape (see status
  note above): deploy is manual-only, triggered by a web UI button click,
  never automatic off a push/poll/webhook.** The old
  `new-repo.sh`/`new-dev-instance.sh`/`new-project.sh`/`project-sync.sh`/
  `target-setup.sh` script combo this bullet originally pointed at was
  retired outright by 2b (backlog item 2, above), not adapted.
- The web UI's "+ New project" button (`create_project()` in `app.py`)
  needs to call whatever replaces `new-project.sh`.
- **"Own page in Container"**: Gitea's own web UI gets a row/link the same
  way code-server does today.
- ~~CI/CD auto-deploy as an explicit **setup step**: surfaced as an install
  prompt (target machine, target path, service name) rather than a manual
  `target-setup.sh` invocation after the fact.~~ **Partially superseded:**
  2c part 2a's `install.sh --with-deploy-target` *does* prompt for target
  path/service/pubkey, but that only provisions the receiver — the
  project→target *mapping* (`deploy-map.json`) is deliberately
  hand-edited/hand-placed by the operator, not an install-time prompt (2c
  part 2b's explicit non-goal — see status note above).

**Open for the future session:** whether existing git-hosting users get a
migration path or this is additive-only for new installs; how much of the
sudoers/restricted-shell security model (`docs/ARCHITECTURE.md`) carries
over to Gitea's own auth model.

---

## 3. Folder upload → auto-detect repo(s)

**Status: shipped (2026-08-12), commit `893840c`.** Reviewer-approved with
follow-ups. Full spec/design/implementation/test-review in `docs/spec.md` /
`docs/design.md` / `docs/implementation.md` / `docs/test-review.md`. The
three deferred polish items below shipped in a follow-up pass ("Upload
wizard polish", see current `docs/spec.md` / `docs/design.md` /
`docs/implementation.md`):
- ~~`UPLOAD_MAX_ENTRIES` is documented in `config/switchboard.env.example` as
  a comment only, not a real settable line.~~ Done — now a real
  `int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000"))` read, same pattern as
  its siblings.
- ~~Step 5 (Review)'s single/split choice uses native `<input
  type="radio">` rather than design.md's described pill-button styling.~~
  Done — restyled as pills (`.wizard-check-row.pill-choice`) while keeping
  the underlying native radio inputs.
- ~~Step 5's "Back" button ... is shown even for the unambiguous sub-case
  where there's nothing to go back and change.~~ Done — "Back" now only
  renders when `d.ambiguous === true`; the "Back always fully resets the
  wizard rather than a partial step-back" behavior itself is unchanged
  (explicit non-goal of the follow-up pass).

**Shape of the work (this one had no real ambiguity, so it's specced a
little further than the others):**
- New UI action alongside "+ New project": upload a folder (zipped
  client-side or server-side after multipart upload) instead of requiring a
  git remote.
- Server side (`app.py`): receive the zip, write it under a staging path,
  unzip.
- Detection logic: walk the unzipped tree looking for `.git` directories.
  - Single `.git` at the root → register as one project under
    `PROJECTS_DIR`, same as any git-hosted project today.
  - No `.git` anywhere, but multiple subfolders that each look like
    projects → treat as a folder of subrepos; likely register each as its
    own `PROJECTS_DIR` entry (one row per subrepo), not one row for the
    whole upload — consistent with "one row per project folder" in the
    current README.
  - A folder with `.git` at the root *and* nested `.git` subfolders
    (monorepo with vendored/embedded repos) — needs a decision: register
    just the root, or the root plus each nested repo as separate rows?
- Needs sane limits: max upload size, path traversal protection when
  unzipping (zip-slip is a real vulnerability class here — validate every
  extracted path stays under the staging dir before writing).

**Open for the future session:** the monorepo-with-nested-repos case above;
whether uploaded (non-git) projects should get a local git repo `init`'d
automatically so they're at least version-controlled going forward.

---

## 4. VS Code dark mode by default

**Status: shipped (2026-08-12).** Reviewer-approved with non-blocking
follow-ups (a residual TOCTOU gap between the symlink check and the
subsequent write, judged low-severity given fixed, non-attacker-controlled
content — see docs/test-review.md). Full spec/implementation/test-review in
`docs/spec.md` / `docs/implementation.md` / `docs/test-review.md`.

**Shape of the work:** small. code-server (`--with-code-server`) currently
launches with whatever default theme it ships with. Provision a default
`settings.json` (`"workbench.colorTheme": "Default Dark+"` or similar) into
each new code-server instance's user data directory at creation time, in
the same place `new-dev-instance.sh`-equivalent provisioning happens today.
Low risk, no open questions — safe to pick up any time.

---

## 5. Tailscale vs. LAN-only as an explicit setup choice

**Status: shipped (2026-08-12).** Reviewer-approved with follow-ups (one
should-fix whiptail msgbox height fix, applied directly). Full
spec/implementation/test-review in `docs/spec.md` / `docs/implementation.md`
/ `docs/test-review.md`.

**Shape of the work:** `PUBLISH_MODE` (tailscale vs. none, per
`config/switchboard.env.example`) already exists as a config value read at
runtime — this item is about surfacing the choice **at install time** as an
interactive prompt in `install.sh` (and `ct/create.sh` for the Proxmox
path), the same way those scripts already prompt for container ID,
resources, and auth mode, rather than requiring a manual edit of
`switchboard.env` + `systemctl restart` afterward. Low risk, no open
questions.

---

## 6. Multi-agent orchestration (customizable per-session teams)

**Decision:** the "local AI" project lead is a **locally-hosted open LLM**
(e.g. via Ollama), separate from Claude Code — not a Claude Code session
playing the lead role. This is the biggest and least-settled item; treat
the below as a starting shape, not a spec.

**Intent, as stated (updated):** not a fixed lead-plus-fixed-team wiring,
but a **generic, customizable roster** — any engine the switchboard knows
about (local LLM via Ollama, Claude Code, Codex, aider, future ones) should
be linkable in the UI, and for *each session* you build whichever team you
want from that roster, picking one as lead. This must work for anyone who
ran the standard Proxmox container setup, not just the original homelab —
i.e. it's a general feature of the install, not a one-off wiring for one
person's setup. The lead questions its teammates first and only escalates
to the user (in the web UI) when something stays genuinely unresolved.

**tmux — yes, but only for half the problem:**
- **Hosting multiple concurrent agents: tmux is the right fit, and mostly
  already there.** The switchboard already gives every project its own
  tmux session with one engine process in it. A "team" session is a
  straightforward generalization: one tmux session per project, one
  **window** per team member (lead's window, plus one window per teammate
  engine), reusing the exact `engines.d/*.engine` startup/URL-watching
  machinery already built for single-engine sessions — no new process model
  needed, just more windows per session and a UI for picking which engines
  populate them. This also gives you observability for free: attach to any
  teammate's window and watch it work, exactly like today's single-engine
  terminal/hosted-link view.
- **Agent-to-agent communication: tmux alone is the weak link.** The only
  way to "talk" to another pane over tmux is `send-keys` (type text into
  its stdin) + `capture-pane` (screen-scrape its output) — that's driving a
  live interactive terminal, not a message protocol. It's workable for
  simple hand-offs but fragile in practice: no structured message format,
  no reliable "is it done responding yet" signal (you're polling captured
  screen text), and brittle against any prompt-format change in the engine
  CLI. **Prefer each engine's own non-interactive/scriptable invocation
  mode where one exists** (e.g. a one-shot "print mode" with
  machine-readable output) for the actual lead↔teammate messages, and treat
  tmux `send-keys`/`capture-pane` as the fallback transport for engines
  that only support interactive use — not the default mechanism. This
  needs a pass through `docs/ADDING_AN_ENGINE.md`'s engine-definition
  format to see which shipped engines (Claude Code, aider, Codex) already
  support a scriptable mode versus which would need the scrape fallback.
- **When this item is picked up, `product-manager` must do deep research
  specifically on the communication mechanism before committing to one in
  `docs/spec.md`** — don't just default to the tmux `send-keys`/
  `capture-pane` fallback described above because it's the path of least
  resistance. Survey what's actually available (each engine's own
  non-interactive modes, structured-output/JSON flags, existing multi-agent
  orchestration patterns, a small local message-bus, etc.) and weigh them
  properly. **If that research concludes tmux should be dropped entirely
  as the transport (not just supplemented) in favor of something else,
  `product-manager` must check with the user before finalizing the spec
  that way** — that's a real architecture change to a project whose core
  primitive has been "everything is a tmux session" since the start, not a
  call to make unilaterally inside a spec draft.

**How this might fit the current architecture:**
- Still likely needs a new engine *type* (a "team" composition wrapping N
  regular engine entries), not a new individual engine definition — read
  the "why engines are config, not code" section of `docs/ARCHITECTURE.md`
  before designing this, since a team session strains the current
  one-engine-per-project assumption in a few places (the single captured
  hosted URL per project, `_session_urls`, would need to become
  per-window).
- The **UI "linking" step**: a settings screen where every configured
  engine (existing `engines.d/*.engine` entries, plus new Ollama
  model entries once local-LLM support exists) is listed as a selectable
  roster member, independent of any one project — then, per project
  session, pick lead + teammates from that roster before starting.
- Needs a decision on how "ask the user" surfaces in the web UI — a
  notification on the project's row, a dedicated inbox, etc.
- Worth checking whether this should route through the same
  product-manager → ux-designer → developer → reviewer pipeline described
  in the global Entwicklung workflow (this *is* a story-sized piece of
  work, likely `workflows/story.md`), given its size, rather than being
  built as one big feature.

**Open for the future session:** which local model/runtime (Ollama +
which model) ships as the default lead option; which shipped engines
support a non-interactive scriptable mode versus needing the tmux-scrape
fallback; how deep "question the team first" goes before escalating (one
round-trip? N attempts? a timeout?); whether a team is scoped to one
project at a time or can coordinate across projects; how per-window hosted
URLs get surfaced in the UI once a project can have more than one.

---

## 7. Project lead gets read-write access to the kanban board

**Intent, as stated:** the project lead should have **read-write** access to
the kanban board and be able to adjust the backlog itself — not just consume
a spec handed to it, but move cards, update status, and amend backlog items
as work actually progresses.

**Directly extends two things that already exist**, so this is a widening of
a working mechanism rather than a greenfield feature:
- Item 1b shipped `scripts/taiga_push_spec.py` — deliberately **one-way
  (spec → Taiga)**, pushing a local spec into a Taiga backlog item as a
  userstory. This item is what turns that into a two-way channel.
- Item 6's "lead" is now a real thing (6c shipped the roster + four-tool
  lead loop: `delegate` / `fact_check` / `ask_user` / `finish`). "Project
  lead" here should mean *that* lead, and board access is most naturally a
  **fifth tool** alongside the existing four, not a side channel.
- It also answers item 1's own open question ("sync direction between Taiga
  and `docs/spec.md`") in the read-write direction.

**Shape of the work:**
- Taiga's REST API already covers what's needed (userstories, statuses,
  milestones). The token-storage pattern is settled: `switchboard.env`-style
  config, same as every other credential in this project.
- Most likely a `board_read` / `board_write` tool pair on the lead loop,
  with `board_write` narrowly scoped to specific verbs (move card, set
  status, append a comment, amend a description) rather than a general
  "call any Taiga endpoint" escape hatch — the four existing tools are all
  narrow and specific, and a broad passthrough would be the odd one out.
- The lead's grounding (6b) is currently **strictly read-only** with two
  guards (a runtime monkeypatch and a static AST scan). Board write access
  must NOT be implemented by loosening those — grounding is the project's
  own docs, the board is a separate system. Keep them separate paths, and
  expect the AST scan to need an explicit, narrow allowance rather than a
  removal.

**Scope decision — settled (2026-08-14):** option (c). The lead's board
writes are proposals only — queued in 6f's existing escalation inbox and
applied to Taiga only after a one-click human approval, never written
directly. This is the user's explicit choice, made when this item came up
for scoping, consistent with every other unattended-write decision in this
project (manual-click-only deploy, no unattended writes to the project's
own source of truth). `board_write`'s tool contract should therefore return
a *pending proposal*, not perform the Taiga call itself — the actual API
write happens only from the approval action, mirroring how `resolve_ask_user()`
gates a lead's `ask_user` block on a human response today.

**Open for the future session:** whether board writes are audited to a log
the human can review after the fact; whether one shared board covers all
switchboard projects or one board per project (item 1's own open question,
still unanswered); what happens when a card the lead is mid-edit was
changed by the human concurrently.

---

## 8. AI merge-request reviewer, triggered by a Gitea tag (and GitHub)

**Intent, as stated:** an AI reviewer for merge requests, checking **code
consistency**, with a **selectable model**, firing **as soon as a "ready for
review" tag/label is set** on the MR. **Updated 2026-08-14: also wanted for
GitHub-hosted repos, not just local Gitea** — see item 17 below, which
covers tracking a project's real (non-Gitea) origin generally; this item's
reviewer should work against whichever host a project's origin actually is
once 17 exists. Until 17 lands, this item's own scope is still Gitea-only,
per the shipped polling precedent below.

**Already-settled decision this must respect — do not relitigate it:**
**no webhook.** Item 2c part 1 originally proposed a webhook-based design
and the user rejected it explicitly for introducing new attack surface. The
shipped pattern is **polling**: `app.py` polls Gitea's REST API piggybacked
on `/status`, throttled by its own `GITEA_POLL_INTERVAL_SECONDS` (default
45s), with no new listener and no Docker networking changes. A tag-triggered
reviewer should extend that existing poll — watch for the label appearing on
open PRs — rather than opening a port. "As soon as the tag is set" therefore
means "within one poll interval", which is a deliberate tradeoff to state
plainly rather than an implementation shortfall.

**Shape of the work:**
- Poll Gitea for open PRs carrying the configured label (name configurable;
  `ready for review` is the default, not a hardcoded constant).
- Fetch the diff via Gitea's REST API, run the selected model against it,
  post the review back as a PR comment. Posting a comment is additive and
  reversible, which makes it a good first write verb — distinct from
  approving, merging, or pushing changes, which are not.
- **Selectable model** maps onto machinery that already exists: item 6's
  roster is exactly "every engine the switchboard knows about, tagged with
  its capability tier" (`engines.d/*.engine` entries plus a configured
  Ollama model). Reuse the roster rather than inventing a second, parallel
  model-selection mechanism. Note the roster's tiers were built for
  *lead* capability (native tool-calling / constrained output / prose
  parse); review is a plainer task and may not need the same tiering — check
  before assuming it transfers.
- "Code consistency" needs sharpening into something checkable: consistency
  with what? House conventions in `CLAUDE.md`/`docs/ARCHITECTURE.md`, the
  surrounding file's own idiom, or the project's existing patterns? 6b's
  grounding (auto-discovered project docs + `fact_check`) is the obvious
  substrate for "consistent with the project's own documented conventions"
  and should be reused rather than rebuilt.

**Scope decisions — settled (2026-08-14):** comment-only — the reviewer
never blocks, approves, or merges, matching "deploy is manual-click only."
Re-review fires only when the "ready for review" tag is explicitly removed
and re-added (not on every new commit while the tag is present) — a
deliberate, low-cost trigger the operator controls. Still open: what
happens on a large diff that exceeds the selected model's context.

**Open for the future session:** whether this shares the escalation inbox
(item 6/6f) or gets its own surface; whether review output is persisted
locally as well as posted to Gitea; token/rate cost of reviewing every
tagged PR against a hosted model versus the local Ollama one.

**Status: shipped, Gitea-only (2026-08-14).** Standalone poll-triggered
mechanism (not a lead-loop tool) hooked into item 2c's existing Gitea poll.
Reviewer-approved with one non-blocking follow-up, not yet fixed: the
per-PR lock is keyed only on `pr_key`, not episode, so if a label is
removed and re-added while the *previous* episode's review is still
in-flight, the old thread's later completion can overwrite state as if the
new episode's own review had run, silently dropping the new trigger with
no error surfaced. Narrow (needs a review to still be running when a
human re-toggles the label) but real. Shape of the fix: key the lock (or
the state file's in-flight marker) on `(pr_key, episode)` so a stale
thread's completion can't clobber a newer episode's state. GitHub support
remains deferred to item 17, per this item's original scope note.

---

## 9. Privileged tests mutate real host state and can't run concurrently

**Found 2026-08-14**, while diagnosing a long-running "unrelated flake" during
the multi-agent story (item 6). Not a product feature — a test-infrastructure
debt item, recorded because it cost several cycles of misattributed failures
and produced one genuine near-miss.

**What's wrong:**
- `tests/test_deploy_target.py` and `tests/test_deploy_dispatch.py` run
  privileged real-SSH/rsync tests that mutate **real host state**: they
  create and delete a system `deploy` user, write `/etc/sudoers.d/`
  entries, provision `/home/deploy/.ssh/authorized_keys`, and contend on a
  real `127.0.0.1` deploy target. They are guarded (`setUp` skips if a real
  `deploy` user already exists, `tearDown` runs `userdel -r`), so the design
  intent is sound — but the guards only hold on a *clean* exit.
- **An interrupted run orphans state that then breaks later runs.** Observed
  concretely: an interrupted test loop left `/home/deploy` behind
  (root-owned, its `.ssh` owned by a bare numeric uid with no account), while
  `userdel` had already removed the user. `setUp`'s "does a real deploy user
  exist?" guard then finds none, proceeds, and the run fails on a stale
  `authorized_keys` — presenting as a mysterious failure with no connection
  to its cause. Recovery is a manual `rm -rf /home/deploy`.
- **They cannot run concurrently**, with themselves or anything else, since
  the contended resources are singleton host objects, not per-process ones.

**The near-miss worth recording:** while diagnosing this, an agent attempted
`sudo truncate -s 0 /home/deploy/.ssh/authorized_keys` to clear the stale
state. A sandbox classifier blocked it. On a host where `/home/deploy` *is*
real deploy infrastructure rather than orphaned scaffolding, that command
wipes a live target's SSH access. The path alone doesn't tell you which case
you're in — the distinguishing evidence was that no `deploy` account existed
and the directory was created minutes earlier by the test run itself.

**Shape of the work:**
- Provision an isolated fixture (a uniquely-named throwaway user and home
  directory per test process) instead of the literal `deploy` account and
  `/home/deploy`, so nothing contends and an interrupted run orphans only
  namespaced state.
- Failing that, at minimum make the guard detect orphaned state, not just a
  live account — `setUp` should skip when `/home/deploy` exists at all, not
  only when the user does.

**Related, same class, found at the same time** (tmux session naming in the
teams tests): `tests/test_teams_lifecycle.py` and `tests/test_team_routes.py`
build `team-<project>` session names from **fixed literal project names**
(`proj`, `atomicdemo`, `failchain`, `sessionrace`), and
`tests/test_teams_lifecycle.py` also uses an unscoped
`switchboard-worktree-op-` prefix plus a broad `tearDown` sweep. These clash
directly across concurrent test processes, the same way the
`switchboard-headless-*` sweep did before it was scoped per-process
(commit for item 6d part 2a's follow-up). Fixing those is the same one-line-
per-site change: scope the name to the process.

**Open for the future session:** whether the privileged deploy tests should
run in CI at all, or be marked as an explicitly opt-in local-only suite.

---

## 10. `set_env()`'s unescaped `sed` upsert can abort install.sh or corrupt config

Found by the reviewer during 6d part 2b (`install.sh --with-ollama`), logged
as a non-blocking should-fix rather than fixed in that cycle since the bug
is pre-existing in a shared helper the cycle didn't otherwise touch.

`install.sh`'s `set_env()` does an idempotent upsert via
`sed -i "s|^${key}=.*|${key}=${val}|"`. Neither `sed`'s `|` delimiter nor its
`&`/backreference metacharacters are escaped in `$val` before interpolation.
Reproduced live:
- A value containing a literal `|` (e.g. a `TEAM_LLM_BASE_URL` or model tag
  that happens to include one) breaks the `sed` expression
  (`sed: unknown option to 's'`, rc=1) and **aborts the whole `install.sh`
  run** on a re-run — violating the "skip only this block, never abort the
  whole run" discipline every other optional block follows.
- A value containing a literal `&` is silently **corrupted** in the written
  config line (sed's replacement-side backreference), rather than erroring.

Any `--with-ollama`, `--with-deploy-target`, or other optional block that
calls `set_env()` with an operator-supplied value can trigger this on a
*re-run* specifically (the first write via `>>`/append doesn't go through
`sed`). The realistic trigger surface is narrow — most values here are
hostnames/paths/tokens unlikely to contain `|`/`&` — but it is real and
reproducible, not hypothetical.

**Shape of the fix:** either escape `$val` for `sed`'s replacement side
before interpolating (e.g. `val_escaped=$(printf '%s' "$val" | sed
's/[&|\\]/\\&/g')`), or switch `set_env()` to an approach that never
shells out user-controlled text through `sed`'s pattern language at all
(e.g. an awk/python3 line-rewrite, matching this project's existing
"parse with python3, not grep/sed" precedent from 6d part 2b's own
`/models` response handling). Fix once in the shared helper — every
`--with-*` block that writes an operator-supplied value benefits, no
per-block workaround needed.

---

## 11. 6f part 1 follow-ups: stale transcript entry on losing resolve, `run_id` path validation

Both found by the reviewer during 6f part 1 (overwatch event feed +
escalation inbox), approved as non-blocking rather than fixed in that
cycle.

**Stale transcript entry.** `app/teams.py`'s `resolve_ask_user()` calls
`_append_history()`/`_append_transcript()` unconditionally, before the
win/lose decision point. A losing caller in a genuine concurrent-resolve
race therefore leaves a permanent, spurious `tool_result` entry in
`transcript.jsonl` — visible via the new `GET .../team/events` feed this
sub-spec introduced — even though its answer was never accepted into
`run.json`'s history. Doesn't affect the lead's own decision-making
(reads `state["history"]`, not the transcript file), but it's a
misleading artifact in the overwatch feed 6f part 2's UI will render.
Shape of the fix: move the transcript/history append to after the
win/lose decision, so only the winner's answer is ever recorded.

**`run_id` path validation.** The three new routes (`GET .../team/events`,
`GET .../team/inbox`, `POST .../team/resolve`) accept a client-supplied
`run_id` and use it directly in filesystem path construction
(`app/teams.py`'s path helpers, called from `app/app.py:3629,3882`) with
no format/containment validation — reproduced directly: a `run_id` of
`"../../outside/evilrun"` successfully read a planted file outside
`_leads_root()`. Real project data stays correctly gated by the existing
`project_name` check on these routes, and exploiting this meaningfully
would need pre-existing filesystem write access elsewhere, so this was
judged narrow rather than blocking — but it's a real, cheap-to-fix gap.
Shape of the fix: validate `run_id` the same way other user-supplied
identifiers in this codebase already are (reject path separators / `..`
segments, or match against a known-safe character set) before it ever
reaches a path-join.

---

## 12. 6f part 2 follow-ups: escalation race test coverage, ARIA attributes, fact_check/finish poll-boundary edge case

Three non-blocking should-fix/nit items from the reviewer's approval of
6f part 2 (Teams page UI), none blocking.

**Untested "already answered" race branch.** `renderEscalationPanel()`'s
`!cached.pending` branch (rendered when a cached `/status` snapshot still
says `waiting_on_you` but a freshly-fetched `/team/inbox` already reports
`pending: false`) was added by the developer beyond `docs/design.md`, is
reachable and correct (reviewer confirmed with a targeted test), but has
zero coverage in `tests/test_team_frontend.js`. Add a permanent regression
test for it.

**Missing ARIA attributes.** `docs/design.md`'s "Accessibility & platform
notes" (`role="log"`/`aria-live="polite"` on the event list,
`aria-pressed`/`aria-checked` on filter pills, `<fieldset>`/`<legend>` for
the escalation option group) weren't implemented in `app/app.py`, and the
omission wasn't called out in `docs/implementation.md`'s "Deviations from
spec." Basic keyboard operability is intact (native `<button>`/
`<input type="radio/checkbox">`/`<label>` throughout), so this doesn't
block, but it's the first scrollable log-like/live-region panel in this
codebase and sets a precedent either way — worth a follow-up pass to add
the recommended attributes.

**fact_check/finish poll-boundary misclassification (self-healing).** The
positional disambiguation `docs/spec.md` itself specifies (a `tool_use`
event is a fact_check claim if immediately followed by a `tool_result`
with `meta.found`, otherwise treated as the finish summary) can transiently
misclassify a fact_check claim as a finish summary if its `tool_use` event
lands in the client's buffer before the paired `tool_result` — e.g. split
across two `/team/events` polls. Practically unreachable (both transcript
entries are written in one synchronous call server-side, sub-millisecond
apart relative to the 4s poll cadence) and self-corrects within one more
poll, so not a live bug — but worth a spec refinement if a future cycle
touches this area: render an explicit transient state for a `tool_use`
event that's the buffer's own last lead event while `team.status` is
still `running`, rather than assuming finish.

**Status: A and B shipped, C partially shipped (2026-08-14).** A permanent
regression test now covers the "already answered" escalation race branch;
the ARIA attributes `docs/design.md` already specified (`role="log"`/
`aria-live="polite"` on the event feed, `aria-pressed` on filter pills,
`<fieldset>`/`<legend>` on the escalation options) are implemented and
verified against real rendered markup. The transient-classification
rendering for C was added but scoped narrowly to `team.status ===
"running"`, matching this backlog item's own literal wording — the
reviewer found and confirmed (adversarially tested, not just read) a
structurally similar poll-boundary gap for `team.status === "blocked"`
(a trailing empty-meta `tool_use` from the lead, no paired `tool_result`
yet, while a **different** in-flight round's `ask_user` escalation has
already flipped status to `blocked`): it still falls through to
`'finish'`, the exact assumed-finish bug this cycle was meant to
eliminate, just for a status this cycle didn't cover. Non-blocking (same
"deliberately narrow scope, not an implementation defect" reasoning as
the original C). **Shape of the follow-up fix:** widen the transient gate
from `status === 'running'` to a general non-terminal check (e.g.
`status !== 'finished' && status !== 'error'`) in a future cycle.

---

## 13. No in-app discoverability for a finished team's committed-but-unmerged branches

Raised at the multi-agent-teams story's completion triage (2026-08-14),
resolving `docs/story.md` §3/§7's long-open "should a teammate's worktree
be merged back automatically, or left for the human to review and merge?"
question. **Decision: left for the human, permanently — not deferred,
settled.** Consistent with every other manual-review-only precedent this
story set (read-only grounding, no auto-deploy of AI work): the switchboard
never merges or discards a teammate's work unattended.

The safety property this needs is **already implemented**, not missing:
`app/teams.py`'s `_remove_worktree()` calls `git worktree remove` with NO
`--force`, so git itself refuses to remove a worktree with uncommitted or
untracked changes (`stop_team()` records that outcome as `"dirty"` and
leaves the directory exactly as-is for a human to find via `team-status`).
No code path ever runs `git branch -D` on a `team-{run_id}-{agent}` branch,
so even a *clean* worktree removal (working directory deleted, git's own
internal metadata cleaned up) leaves the branch itself intact in the
project's repo indefinitely, carrying whatever the teammate committed.
Nothing an agent commits is ever silently lost.

**The actual gap: discoverability.** Once a worktree directory is removed,
`stop_team()` also drops that agent's entry from the run's own persisted
`state["worktrees"]` map (by design — see that function's docstring on why
a stale path entry would be unsafe for a later run to inherit), so the
switchboard itself no longer tracks that the branch exists. An operator
who wants to review or merge a past run's teammate work has to already
know to run `git -C <project> branch --list 'team-*'` by hand; there is no
UI panel, `team-status` field, or documentation pointing at this. Shape of
a future fix, if this becomes a real pain point: list surviving
`team-*` branches for a project (a `git branch --list` call, cheap) in the
Teams page or `team-status` CLI output, and add a short "reviewing a
team's work after it stops" section to `docs/ARCHITECTURE.md` or
`README.md` documenting the plain `git log`/`git merge`/`git branch -D`
commands to review, merge, or discard one. Non-blocking: no data-loss risk
exists today, this is pure discoverability polish.

---

## 14. Update path for an already-installed container when `main` moves

**Added 2026-08-14**, user-requested: "migration scripts of container
already exists and a new update to master is there." Read as: an operator
who already ran `install.sh` on a container needs a way to pull in a newer
switchboard release from `main`/`master` — including any config or state
migration a given update requires — rather than the current implicit
assumption that `install.sh` is a one-time, fresh-install-only operation.

**Not yet scoped** — needs a real product-manager pass before building.
Open questions to settle then, not now:
- Is this a new `install.sh --update`/`--upgrade` flag (parallel to the
  existing `--with-*` optional-feature flags), or a separate script?
- What actually needs migrating between versions? So far this project has
  added new `switchboard.env` keys (additive, already tolerant of being
  unset) and new optional Docker Compose stacks (Taiga, Gitea) — a real
  breaking schema change hasn't happened yet, so "migration scripts...
  already exists" may be understating what's actually needed, or may be
  referring to something the user has in mind that isn't yet visible in
  this repo. Confirm with the user what "migration scripts" refers to
  concretely before scoping further.
- Does an update ever restart already-running engine sessions or team
  runs? If so, this needs the same "never destructive, never surprise a
  running session" discipline the rest of this project already holds to
  (2c part 1's fast-forward-only sync, deploy being manual-click-only).

---

## 15. Install wizard UI

**Added 2026-08-14**, user-requested: "install should be an install wizard
like this picture" — **the referenced image was not attached to the
request**. Needs the actual reference before this can be scoped at all;
follow up with the user for the image (or a link/description of the
wizard UI they have in mind) before writing a spec.

**What's known without the image:** today, `install.sh` is a single
non-interactive-by-default script (`--yes` flag) with a series of
`prompt()`-driven optional `--with-*` blocks, run over SSH/terminal — there
is no browser-based or step-by-step graphical installer. If the picture
turns out to depict a web-based multi-step wizard (name/logo suggests
something in the shape of a typical "welcome → configure → confirm →
install" flow), this would be a materially different delivery mechanism
than the current shell script and deserves its own architecture
discussion — don't assume it's a small tweak to `install.sh` until the
picture clarifies what's actually wanted.

---

## 16. Create a new project by `git clone <url>` directly

**Added 2026-08-14**, user-requested: "container should be possible to
create new projects with git clone and url."

**Context — what exists today:** item 3 (folder upload) covers uploading
an existing local folder and auto-detecting repo(s) inside it. Item 2b
covers creating a *new*, empty repo through Gitea's own API
(`create_project()`). Neither covers the third case this item asks for:
handing the switchboard a URL to an **existing remote repo** (which may or
may not already be on this switchboard's own Gitea) and having it clone
that URL directly into `PROJECTS_DIR` as a new project — no upload, no
Gitea-side repo creation, just "here's a URL, clone it and add it to my
project list."

**Shape of the work (not yet fully scoped):**
- A new "add project from URL" entry point in the web UI, parallel to the
  existing "new project" and "upload" flows.
- Needs the same kind of privileged clone hand-off item 2b already built
  for Gitea-originated clones (`scripts/new-project-from-gitea.sh`) — a
  script that runs as (or hands off to) the correct system user, not the
  request-handling process directly, following that established pattern
  rather than reinventing clone privilege-separation.
- Directly related to item 17 below: a project cloned from an arbitrary
  URL is exactly the case where the project's real origin is *not* this
  switchboard's own local Gitea, so this item and item 17 should likely be
  scoped and built together, or in the order 16 → 17.
- Open question: authentication for cloning a private remote repo (SSH
  key, token) — needs a decision on where that credential lives and how
  it's scoped, following this project's existing `switchboard.env`-style
  credential-storage convention.

**Status: shipped (2026-08-14).** `POST /projects/clone` + a new
privileged `scripts/new-project-from-url.sh`, following item 2b's
established clone-privilege-separation pattern. Private-repo auth
settled, not left open: SSH-based private clones work at zero new cost
(ride `RUN_USER`'s own pre-existing SSH access); HTTPS+token auth is
explicitly deferred to a fast-follow, since a `switchboard.env`-style
single-secret convention doesn't map cleanly onto "arbitrary host,
project doesn't exist yet."

Reviewer-approved after **three** review rounds on the same must-fix — an
argument-injection vector via a crafted URL (e.g.
`ssh://-oProxyCommand=...`) reaching the `RUN_USER`-privileged clone
subprocess. Round 1 found the gap (regex allowlist alone didn't close it,
only the installed git's own CVE-2017-1000117 hardening did). Round 2's
fix (regex negative-lookaheads anchored right after the scheme/`@`) was
itself proven bypassable via `user@-oProxyCommand=...` and scp-shorthand
`user@host:-oProxyCommand=...`, both of which hide the malicious host
behind an optional grammar segment the lookahead didn't cover. Round 3
replaced lookahead-anchoring entirely with real host-component isolation
(`urllib.parse.urlsplit().hostname` for `scheme://` URLs; explicit
last-`@`/first-`:` parsing for scp-shorthand, empirically verified against
git's/OpenSSH's actual double-`@` splitting behavior, not assumed) plus a
dedicated `_clone_url_host_is_safe()` charset/IPv6 check, mirrored in
`scripts/new-project-from-url.sh`. Approved after independent adversarial
testing (double-`@`, bracketed IPv6, empty host/port, trailing colon) via
real `sudo` runs against the privileged script.

**Should-fix follow-up, not yet fixed:** neither validation layer checks
that a `scheme://host:port` URL's port component is actually numeric
(e.g. `ssh://127.0.0.1:-oProxyCommand=...` is currently accepted).
Verified not currently exploitable — git doesn't split a non-numeric port
into its own argv token, so this doesn't reach the actual
leading-`-`-as-argv-token mechanism the must-fix targeted, and OpenSSH
itself rejects the malformed combined hostname before any connection
attempt — but it's the same "relying on downstream hardening" pattern
this fix arc was meant to eliminate. Shape of the fix: validate the port
substring (if present) is `^[0-9]+$` at both layers.

---

## 17. Track and remotely interact with a project's real (non-Gitea) origin

**Added 2026-08-14**, user-requested: "if there is a cloned GitHub repo
which is not a local Gitea repo it should be traced to origin. All the
pull requests, comments, branches should be remotely fetchable."

**Context:** items 2b/2c built deep integration with this switchboard's
*own*, locally-hosted Gitea instance — repo creation via Gitea's REST API,
polling Gitea for push/PR activity. This item asks for the same class of
remote-repo-awareness (PRs, comments, branches, fetchable/visible from the
switchboard UI) but for a project whose actual `origin` remote is
somewhere else entirely — GitHub is the concrete example given, but the
underlying need is "detect what origin actually is, and if it's not our
own Gitea, talk to that host's own API instead."

**Shape of the work (not yet fully scoped):**
- Detect, per project, whether `origin` points at this switchboard's own
  Gitea instance or somewhere external — `git remote get-url origin` plus
  a hostname comparison is the obvious mechanism.
- For an external origin, this needs its own API client per host type
  (GitHub REST/GraphQL API to start, since that's the concrete case
  named) — parallel to, not a replacement for, the existing Gitea client
  code from 2c.
  This is real new integration surface, not a small extension: auth
  (a GitHub token, following the existing credential-storage convention),
  rate-limit handling (GitHub's API has real limits unlike a
  self-hosted Gitea instance), and a decision on whether polling (2c's
  established no-webhook precedent) is the right model here too or
  whether GitHub's own webhook support changes that calculus — needs a
  real discussion, don't assume the Gitea polling precedent transfers
  unmodified.
- Directly feeds item 8's now-broadened GitHub scope (an AI reviewer
  reacting to a "ready for review" label on a GitHub PR needs exactly
  this remote-fetch capability to read the diff/comments) and item 16
  (a project cloned from an arbitrary URL is the primary case this
  applies to).
- **Scope decision to put to the user before building:** should
  read-write actions (posting a comment, per item 8) ever be allowed
  against a project's *external* origin the same way they're allowed
  against the switchboard's own Gitea, or does touching someone else's
  GitHub repo warrant an extra confirmation step given it's not
  infrastructure this switchboard operator fully controls?

---

## 18. Cross-agent capability parity — investigate `garrytan/gstack`

**Added 2026-08-14**, user-requested: "possible to add these possibilities
for all agents not just Claude Code on my LXC:
https://github.com/garrytan/gstack" — the user wants whatever capability
`gstack` provides extended to every engine/agent this switchboard already
supports (Claude Code, Codex, aider, etc.), not just Claude Code
specifically.

**Not yet investigated.** This repo hasn't been read yet — a future
session picking this up should start by actually reading
`https://github.com/garrytan/gstack` (README, structure) to understand
what capability is actually being requested before scoping anything,
rather than guessing from the name alone. Once that's understood, check
whether it's a per-engine capability (fits this project's existing
`engines.d/*.engine` per-engine-config pattern) or a switchboard-level
capability (fits the roster/lead-loop machinery from item 6) before
proposing a shape.

---

## 19. Interactive chat UI for the AI team — watch, interrupt anytime, approve inline

**Added 2026-08-14**, user-requested: "build a chatbot UI for the AI team
so you can watch them talk to each other, interrupt at any point, approve
ask_user questions."

**Context — what already exists (item 6f, shipped):** a merged, per-agent-
colored, cursor-polled live event feed with a status strip, and an
escalation inbox that lets a human answer a blocked lead's `ask_user`
call. This already covers "watch them talk" and "approve ask_user
questions" in a read-mostly, log-styled form.

**What this item asks for beyond 6f — genuinely new capability:**
**"interrupt at any point"** is not the same as answering a pending
`ask_user` block. Today, a human's only two levers on a running team are
(a) wait for the lead to itself call `ask_user` and then answer it, or (b)
stop the whole team outright (`stop_team()`). There is no way to inject a
free-form message into a *running* team without either of those — no
"interject/redirect" capability exists in `app/teams.py`'s lead loop today.
This is the real scope of this item, not just a UI restyle from log-feed
to chat-bubble.

**Not yet scoped — real architecture questions for a future
product-manager pass:**
- Does an interjected message go to the lead only, or can a human message
  a specific teammate directly? The four-tool lead loop (`delegate`/
  `fact_check`/`ask_user`/`finish`) has no concept of an unsolicited
  inbound human message today — this needs real design, not just a new
  route.
- Does interjecting pause the lead's current in-flight tool call, or queue
  the message for the lead's next turn? Silently dropping/racing with an
  in-flight call would be a real correctness bug in the same class 6f's
  own concurrent-resolve races were.
- Is "chatbot UI" purely a visual/interaction-model change on top of 6f's
  existing event feed (chat bubbles instead of a log list), or does it
  also imply a differently-shaped event envelope? Reuse 6f's existing
  `{ts, agent, seq, kind, text, meta}` envelope and cursor-polling
  mechanism rather than inventing a second live-feed mechanism, unless a
  concrete gap is found.
- Relationship to item 7 (kanban write access) and item 8 (AI reviewer):
  both of those already settled on a propose-then-approve /
  comment-only model specifically to avoid unattended writes — an
  "interrupt at any point" capability is a *human*-initiated write into a
  running agent's context, which is a different trust direction (human →
  agent, not agent → external system) and likely doesn't need the same
  caution, but state that explicitly in the eventual spec rather than
  assuming it transfers.
