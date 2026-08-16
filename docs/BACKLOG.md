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

**Settled 2026-08-14 (asked directly this session): this is a new
`install.sh --update`/`--upgrade` flag, parallel to the existing
`--with-*` optional-feature flags — not a separate script.** The rest of
this item's scoping (what needs migrating, whether an update can ever
restart a running session) is in `docs/spec.md`.

---

## 15. Install wizard UI

**Status: shipped in full (2026-08-15), parts 1-3 covering all six shaped
pieces below (5: part 1; 1: part 2; 2-4: part 3; 6 stays an explicit
non-goal).** `ct/create.sh`'s Advanced branch now has an optional-feature
checklist, a Default/Advanced entry fork, live storage/bridge enumeration,
and hard-block CTID/hostname validation — reviewer should confirm before
this is treated as fully closed, per the normal approval gate.

**Added 2026-08-14**, user-requested: "install should be an install wizard
like this picture" — the referenced image was not attached at the time.
**Reference supplied 2026-08-15**: screenshots + a blog post
(bjoerns-techblog.de) of the **Proxmox VE Community Scripts** helper-script
wizard (`community-scripts/ProxmoxVE`), plus its `ollama.sh` one-liner as a
concrete example. Deep research done this session (fetched and read the
actual `misc/build.func` source, not just the blog description) — this
item is now unblocked and scopeable.

**How the community-scripts wizard actually works (grounded in
`misc/build.func`, ~7300 lines, fetched 2026-08-15):**
- **Invocation**: a single `bash -c "$(curl -fsSL .../ct/ollama.sh)"`
  one-liner. Each per-app script (e.g. `ct/ollama.sh`, 65 lines) is a thin
  shim: it sets `APP`, `var_tags`, `var_cpu`, `var_ram`, `var_disk`,
  `var_os`, `var_version`, etc., defines an app-specific `update_script()`,
  then sources the shared framework —
  `source <(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/build.func)`
  — and calls `start`, `build_container`, `description`. All the wizard
  logic lives once in `build.func`/`install.func`, not duplicated per app.
- **Entry menu** (`install_script()`, `build.func:3391`): after
  preflight checks (root, PVE version, arch, SSH-key sanity), shows a
  `whiptail --menu` with **Default Install / Advanced Install / User
  Defaults / App Defaults (if saved) / Settings** — not just a bare
  yes/no. Selecting "Default" calls `base_settings()` and proceeds
  immediately; "Advanced" calls `base_settings()` then
  `advanced_settings()`.
- **`advanced_settings()`** (`build.func:2002`) is an explicit **step
  state-machine** (`STEP=1`, `((STEP++))`/`((STEP--))` to go
  forward/back, cancel-at-step-1 exits, cancel-later steps back one) that
  walks: container type (priv/unpriv) → root password → container ID
  (`validate_container_id`, auto-bumps on collision) → hostname
  (`validate_hostname`, RFC1123) → disk size → CPU cores → RAM → network
  bridge (dynamically enumerated from real host bridges/SDN vnets) → IPv4
  mode (DHCP/static/range-scan) → IPv6 mode → MTU/DNS/MAC/VLAN → tags.
  Every field has real-time validation functions
  (`validate_hostname`/`validate_mac_address`/`validate_vlan_tag`/
  `validate_gateway_in_subnet`/…) rather than accepting anything typed.
- **Storage pool selection**: enumerated live via
  `pvesm status -content rootdir` (or `vztmpl` for templates) — silently
  auto-picks if only one pool exists, otherwise shows a `select_storage()`
  menu; `validate_storage_space()` checks the chosen pool actually has
  room before proceeding.
- **Build step** (`create_lxc_container()`, `build.func:6110`): resolves/
  downloads the OS template via `pveam`, assembles `PCT_OPTIONS` (hostname,
  features, storage, net, etc.) as a multi-line string, then runs
  `pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" $PCT_OPTIONS`,
  with automatic **fallback to an older OS template version** and a
  verbose-log-on-failure whiptail prompt if `pct create` fails, then
  `pct start`. Optionally offers to save the just-used values as
  reusable **app defaults** (`maybe_offer_save_app_defaults()`) so a
  re-run can skip the wizard entirely next time.
- **Net effect**: same 5 primitives throughout — `whiptail`
  `--menu`/`--radiolist`/`--inputbox`/`--passwordbox`/`--yesno`, real
  per-field validators, live enumeration of host resources (bridges,
  storage pools, templates) instead of free-text guessing, and a
  Default/Advanced fork so a first-time user needs one keypress while an
  operator who cares still gets full control.

**What this project already has — closer to this pattern than the
original backlog note assumed:** `ct/create.sh` (already in this repo,
referenced by item 5) is a **smaller instance of the exact same shape**:
a `bash -c "$(curl -fsSL .../ct/create.sh)"` one-liner, `command -v
pct`/`whiptail` preflight, a `msg`/`ask`/`askpw`/`yesno`/`menu` helper
set built directly on `whiptail`, sequential prompts for CTID/hostname/
storage/disk/cores/memory/bridge/IP, then `pct create` + `pct start` +
in-container bootstrap (clone repo, write `switchboard.env`, run
`install.sh`). It is **not** using the community-scripts framework (no
shared `build.func` — this project deliberately keeps `ct/create.sh`
self-contained per its own header comment, "no shared framework, just
pct and whiptail") and is missing, relative to the researched pattern:
  - No Default-vs-Advanced fork — every field is always asked, flat,
    in one pass (no "press Enter three times and you're done" path).
  - No live storage-pool enumeration (`pvesm status`) or bridge
    enumeration — `STORAGE`/`BRIDGE` are free-text inputs with a string
    default, not a menu built from what's actually on the host.
  - No per-field validation beyond what `install.sh` itself later
    enforces — a malformed CTID/hostname/IP isn't caught until `pct
    create` fails.
  - No app-defaults save/reuse, no step-back navigation (each `ask` is a
    one-shot prompt; whiptail's own per-dialog Back button exists but
    isn't wired to a step machine — cancelling anywhere aborts the whole
    script per `set -euo pipefail`, matching this project's own
    documented "abort the run" default *outside* the optional `--with-*`
    blocks, but unlike community-scripts' per-step back navigation).

**Shape of the work for a future spec:** this is a **refinement of
`ct/create.sh`** adopting the concrete, load-bearing pieces of the
researched pattern — not a from-scratch rebuild, and not a pivot to a
browser-based wizard (nothing in the reference material depicts one; it's
a terminal/`whiptail` TUI end to end, run over an SSH/console session on
the Proxmox host itself, before there's even a container to serve a web
UI from). Concretely scopeable pieces, roughly in the order they'd add
value:
  1. Default-vs-Advanced entry menu, mirroring `install_script()`'s
     `whiptail --menu` fork — Default runs today's current defaults with
     zero prompts beyond confirmation; Advanced walks the existing
     `ask`/`menu`/`yesno` sequence unchanged.
  2. Live storage-pool enumeration (`pvesm status -content rootdir`,
     already single-line-safe the way item 3's zip-slip validation is)
     feeding a `whiptail --menu` instead of `ask`'s free-text default —
     directly prevents a mistyped storage pool from failing `pct create`
     partway through.
  3. Live bridge enumeration (`ip link show type bridge` or Proxmox's own
     `/etc/network/interfaces` parse, whichever `build.func` is shown to
     rely on) feeding the same menu treatment as bridge/storage.
  4. CTID/hostname validation before `pct create` is attempted, reusing
     the same regex/range checks `build.func`'s `validate_container_id`/
     `validate_hostname` apply, adapted to this script's existing
     `ask()` helper rather than importing the whole framework.
  5. **Optional-feature checklist menu** — settled decision, 2026-08-15
     (see below): replace the current two standalone `yesno` prompts
     (`WITH_GIT_HOSTING`, `WITH_CODE_SERVER`) with one `whiptail
     --checklist` multi-select covering all switchboard-box-installable
     `install.sh` flags, mirroring community-scripts' own
     multi-select-style menus rather than one `yesno` per flag.
  6. **Explicitly out of scope for this item**: the app-defaults
     save/reuse file, IPv6/MTU/VLAN/SDN-vnet fields (this project's own
     `IPCONFIG` free-text field already covers the static/DHCP cases this
     project actually needs), and adopting `build.func` itself as a
     dependency — `ct/create.sh`'s own header comment's "no shared
     framework" decision stands; borrow the *pattern*, not the *code*.

**Scope decision — settled (2026-08-15):** the checklist (item 5 above)
covers exactly the four `--with-*` flags `install.sh` documents as
running **on the switchboard box itself** —
`--with-git-hosting`/`--with-code-server`/`--with-taiga`/`--with-ollama`.
`--with-host-control` and `--with-deploy-target` are explicitly
**excluded** from `ct/create.sh`'s wizard: `install.sh`'s own header
comments say these are "usually installed on a *different* machine than
the web UI" / "run on a *separate* target machine, never the switchboard
box itself" — surfacing them in the container-creation wizard would
invite enabling infrastructure-receiver features on the wrong box. They
remain CLI-only flags on `install.sh`, unchanged, run by hand on whatever
machine actually needs them.

**Shape of the checklist item specifically:**
- One `whiptail --checklist` screen, default-unchecked (matching
  `WITH_GIT_HOSTING=0`/`WITH_CODE_SERVER=0`'s current off-by-default
  posture), listing: git-hosting, code-server, taiga, ollama — each with
  the same one-line description `ask()`/`yesno()`'s current prompt text
  already gives, condensed to fit a checklist row.
- Checking **taiga** should carry the same resource-cost callout item 1's
  own install prompt gives (multi-service: Django + RabbitMQ + Postgres +
  frontend) — either inline in the checklist row text or a follow-up
  `msgbox` shown only if taiga is checked, not a blocking confirmation.
- Checking **ollama** needs a follow-up step the checklist itself can't
  capture: `--with-ollama` requires an endpoint URL + model name,
  validated as actually reachable (`install.sh`'s own documented
  behavior — "refuses to write config it can't verify"). This must stay
  a **separate `ask()`/validation step shown only when ollama is
  checked**, not folded into the checklist itself.
- Selected items map to `INSTALL_FLAGS` the same way `WITH_GIT_HOSTING`/
  `WITH_CODE_SERVER` already do today (`[ "$WITH_GIT_HOSTING" -eq 1 ] &&
  INSTALL_FLAGS="$INSTALL_FLAGS --with-git-hosting"`) — one conditional
  append per checked item, no new dispatch mechanism needed.

~~**Open for the future session:** whether step 1 (Default/Advanced fork)
alone satisfies "install wizard" well enough to ship alone, or whether the
user wants storage/bridge live-enumeration (steps 2–3) and the checklist
(step 5) in the same pass; whether validation (step 4) should hard-block
on failure or just warn and let `pct create`'s own error surface,
consistent with this project's existing preference for real errors over
guessed validation.~~ **Resolved (2026-08-15, part 3):** all of steps 1-3/5
shipped together across parts 1-3, not step 1 alone; validation (step 4)
is a hard block with no "continue anyway" escape hatch, per part 1's own
settled reasoning (checking CTID/hostname before `pct create` surfaces the
same rule `pct create` would enforce anyway, just earlier and more
clearly). Reviewer-confirmed during part 3's test-review pass — see
`docs/test-review.md` for that cycle.

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

**Scope decision — settled (2026-08-14, asked directly this session):**
read-write actions against a project's external origin (e.g. posting a
comment on a real GitHub PR) are allowed the SAME WAY they're already
allowed against the switchboard's own local Gitea — no extra confirmation
gate. This resolves the open scope question above; item 8's own
comment-only, non-blocking write verb (posting a PR review as a comment)
extends to GitHub unchanged once this item's GitHub client exists — no
separate propose-then-approve step (that pattern belongs to item 7's board
writes specifically, a different write target with its own settled
reasoning, not a general policy this item needed to re-litigate).

**Status: part 1 speced (2026-08-14), not yet built.** Given the size (new
external API integration + auth + rate-limiting + host-detection + item 8
integration spans what would otherwise be one oversized cycle), this item
is split the same way items 6d/19/2c were — **part 1** (this session's
buildable `docs/spec.md`): unprivileged per-project origin detection
(`git remote get-url origin` + loopback-vs-`github.com`-vs-other host
classification, no new privilege boundary needed) plus a GitHub REST API
client (`_github_api`/`_github_api_raw`, mirroring `_gitea_api`'s own
contract) covering list-open-PRs/PR-diff/list-branches/post-PR-comment,
real concrete rate-limit handling (a global in-memory cooldown gate driven
by `X-RateLimit-Remaining`/`X-RateLimit-Reset`/`Retry-After`), and a real
(not deferred) polling-vs-webhook decision: **polling, no webhook** — the
original no-webhook reasoning (2c part 1, reaffirmed by item 8) was never
about implementation convenience, it was "no new inbound listener," which
applies at least as strongly to a GitHub webhook (a genuinely
internet-facing endpoint, not a LAN-local Gitea container) as it did to
the original proposal. Part 1 deliberately ships no poll-loop wiring, no
UI, and no item 8 integration — those, plus `GITHUB_POLL_INTERVAL_SECONDS`
and the host-agnostic dispatch layer for item 8's
`_ai_reviewer_poll_repo()`, are **part 2**, to be speced once part 1 has
shipped and been reviewed.

---

## 18. Cross-agent capability parity — investigate `garrytan/gstack`

**Added 2026-08-14**, user-requested: "possible to add these possibilities
for all agents not just Claude Code on my LXC:
https://github.com/garrytan/gstack" — the user wants whatever capability
`gstack` provides extended to every engine/agent this switchboard already
supports (Claude Code, Codex, aider, etc.), not just Claude Code
specifically.

**Investigated (2026-08-14) — blocked pending user input, not buildable
without it.** `gstack` turns out to be a Markdown slash-command skill
library (23 skills + 5 standalone CLIs) that only runs inside a Claude
Code session via that tool's own skill-loading mechanism — it is not a
service, protocol, or per-engine config surface, and neither `aider` nor
a raw Ollama chat loop has an equivalent skills/custom-command extension
point. "Extend it to every engine" therefore doesn't map onto this
project's `engines.d/*.engine` pattern at all. It also overlaps heavily
with capability this project already shipped, built to be genuinely
engine-agnostic from day one: items 6/6c/6d/6f/7/8's roster + three-tier
lead-loop (`delegate`/`fact_check`/`ask_user`/`finish`) + kanban
read/write + AI PR reviewer + escalation inbox. `gstack` also brings new
runtime dependencies (Bun, Chromium for browser automation, optionally
ngrok/Supabase) that aren't currently part of this project's install
surface.

**Questions only the user can answer before this can be scoped:**
1. Which specific `gstack` capability is actually wanted — the whole
   workflow, or a named subset (browser QA, cross-model review, security
   audit, "office hours" scoping)?
2. Given the overlap with items 6/6c/6f/7/8, is this really "close one
   specific named gap" rather than "port gstack wholesale"?
3. Is it acceptable that `aider`/local-LLM engines can only ever get this
   via the existing lead-loop abstraction, never `gstack`'s literal
   slash-command form?
4. Are `gstack`'s own new runtime dependencies acceptable to add to the
   LXC, and via what install mechanism?

**Answered directly by the user (2026-08-14):**
1. All three named capabilities are wanted: browser QA/testing
   automation, cross-model code review, AND a security audit skill (not
   "whole workflow," not "not sure," not a single pick).
2. gstack's own new runtime dependencies (Bun runtime, Chromium for
   browser automation, optionally ngrok/Supabase) are **not** acceptable
   to add to the LXC.

**Reconciled (2026-08-14) — resolved per-capability, not treated as one
bundled decision, since the two answers pull in different directions for
different pieces of the ask:**

- **Cross-model code review: already fully shipped, no new code needed.**
  Verified directly against the actual implementation: `app/teams.py`'s
  `review_pr_diff()` (backlog item 8) already takes any roster
  model/engine — an Ollama model or any `engines.d/*.engine` entry, i.e.
  genuinely cross-model and cross-engine, not Claude-Code-specific —
  grounds it in the project's own documented conventions
  (`load_grounding()`), runs it against a PR diff, and the result gets
  posted as a PR comment via `app/app.py`'s `AI_REVIEWER_*` poll
  mechanism. This is a closer, more integrated match to "cross-model code
  review" than anything `gstack` itself offers (gstack has no equivalent
  cross-model selection — it runs whatever single model the invoking
  Claude Code session happens to be). The user's request almost certainly
  predates knowing item 8 — built earlier in this very session — already
  delivers this. **No second, parallel review mechanism should be built.**
  Nothing to do here beyond this note.

- **Security audit skill: already covered, at a different layer, no new
  code needed in this repo.** Verified: the `claude-security` plugin
  (`claude-plugins-official` marketplace, already installed in the local
  Claude Code plugin registry — `~/.claude/plugins/marketplaces/
  claude-plugins-official/plugins/claude-security/`) ships a
  `skills/claude-security/SKILL.md` with concrete jobs
  (`scan-codebase.md`, `scan-changes.md`) plus scan/patch/verify
  sub-agents and report rendering. This is exactly the kind of
  review-only, audit-only capability this project's own CLAUDE.md already
  says to route directly to a matching skill rather than build inside a
  project's own pipeline ("Use a dedicated skill instead of the full
  pipeline for review-only or audit-only work... Route these to the
  matching skill (e.g. `security-review`) directly"). It's invocable
  against `ai-dev-switchboard`'s own codebase, or any other project on
  this box, today, with zero new code in this repo. Same
  Claude-Code-only limitation as gstack itself (it's a plugin skill, not
  a per-engine hook) — but that's an inherent property of "skill" as an
  extension mechanism, not something worth rebuilding a parallel in-repo
  audit tool to work around, especially given the user just declined new
  install-surface dependencies. **Nothing to build here either.**

- **Browser QA/testing automation: the one genuinely novel ask, and
  genuinely in tension with answer 2.** Real browser QA (JS execution,
  DOM rendering, click/type interaction, screenshots) fundamentally needs
  a headless browser engine — there is no way around that; it is not an
  implementation-detail choice. The user declined exactly that dependency
  class. **Decision: real browser QA is blocked, full stop, and is not
  being built as a diminished or misleadingly-named substitute.** What
  IS honestly buildable without any new dependency: an **HTTP-level smoke
  check** — status code, response timing, an optional response-body
  substring assertion — against a project's already-running dev server.
  Confirmed buildable with zero new install-surface cost: `curl` and
  `python3` are already installed by `install.sh`'s existing baseline
  `apt-get install` line (`install.sh:214`), and this codebase's
  established in-process HTTP convention is already stdlib
  `urllib.request` (`_gitea_api()`, `_github_api()`, the login/
  description-LLM calls), so this needs no `curl` subprocess and no
  third-party library either — genuinely free to add. **This is
  explicitly NOT "browser QA"** and is named and documented as a smoke
  check throughout, precisely so it is never confused with, or presented
  as satisfying, the actual browser-QA ask. Speced this session:
  `docs/spec.md` — a per-project "Smoke check" button (rendered only when
  `/status`'s already-captured `url` field is present for that project),
  one GET request, reports status code + elapsed ms + optional substring
  match, manual-trigger only, no persisted history, no new runtime
  dependency.

**Status: reconciled 2026-08-14.** Two of the three requested capabilities
(cross-model review, security audit) require **no new code in this repo**
— both are already fully covered, one by this project's own item 8, one
by an existing Claude Code plugin at a different layer entirely. The
third (real browser QA) remains genuinely blocked by the user's own
no-new-runtime-dependencies answer, and is not being faked. The one real,
honest, buildable increment this reconciliation produced — an HTTP-level
smoke check, deliberately NOT branded as browser QA — is speced in the
current `docs/spec.md`, ready to build next.

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

**Status: shipped, in two parts (2026-08-14).** **Part 1** (backend +
CLI): `teams.interject(run_id, message)` appends to a new per-run,
append-only `human.jsonl` — never touches `run.json` directly, so it
can't race the driving thread's own in-memory state / round-end
`_persist()` overwrite. `team_step()` drains it via a persisted
`human_cursor` at the top of its own round, folding messages into the
lead's next round via a new `_INTERJECT_MITIGATION` prompt clause —
delivery is at the next round boundary, not mid-in-flight-call (an
explicit, reasoned non-goal). New `POST /team/interject` route +
`team-interject` CLI subcommand, non-blocking (the team keeps running).
`human.jsonl` merges into the existing event feed as
`agent="human"`/`kind="message"`, reusing the existing envelope — no new
shape, resolving the backlog's own third open question above. Lead-only,
not addressable to a specific teammate, resolving the first open question
above. Trust direction resolved (second-to-last open question above):
human→agent injection has no external side effect, doesn't need items
7/8's propose-then-approve caution. Reviewer independently verified the
concurrency-safety design with real multi-threaded repro scripts (mid-
round stall + concurrent interject; crash/restart cursor persistence),
not just a design read. **Part 2** (chat UI): a compose box on the Teams
page, visible whenever a team is `running` or `blocked` and
`waiting_on_you` (exact mirror of what part 1 accepts server-side); a
deliberate decision AGAINST a full chat-bubble redesign of the feed
(reasoning: ~10 structurally different event kinds across more than two
participants doesn't fit a two-party bubble layout, and a redesign risked
breaking 6f part 2's `role="log"`/`aria-live="polite"` accessibility
contract for no functional gain) — human messages instead get a new
`.kind-human-message` row style within the existing log-list, plus a new
`human` filter pill. Live character counter + proactively-disabled Send
for the 2000-char limit. Both parts reviewer-approved with no must-fix
findings.

**Should-fix follow-up surfaced twice now, not yet fixed — see new item
20 below**: `.team-btn`'s white-text-on-`#34c759`-green styling fails
WCAG AA contrast (actual ~2.2:1, not the ~5:1 `docs/design.md` has
claimed across at least two separate design-doc sections). Pre-existing,
not introduced by either part of this item, but flagged again here since
it's now been independently confirmed twice.

---

## 20. `.team-btn` fails WCAG AA contrast (white text on `#34c759` green)

**Found independently by the reviewer twice** — first during item 16's
review (`docs/test-review.md`), again during item 19 part 2's review —
both times marked non-blocking since the styling is pre-existing and
unmodified by the diff under review at the time. Recording as its own
item now since two independent confirmations is enough to stop treating
it as a footnote.

**What's wrong:** `.team-btn`'s white (`#fff`) text on its green
(`#34c759`) background computes to **≈2.2:1** contrast — well under
WCAG AA's 4.5:1 minimum for normal text (3:1 even for large/bold text).
`docs/design.md` has, across at least two separate sections written by
two different ux-designer dispatches, claimed this pairing passes AA at
figures like 5.05:1/9.15:1 — both wrong when recomputed from the actual
hex values, and worth correcting in the doc alongside the real fix so a
future design pass doesn't inherit the same wrong number a third time.

**Shape of the fix:** darken the green (or lighten/bolden the text) until
the pairing actually clears 4.5:1 — e.g. a darker `#1e7e34`-class green
with white text typically clears AA; verify the actual chosen pair with a
real contrast calculation, not a plausible-sounding guess, given this
exact class of doc-vs-reality drift is what caused the problem twice
already. `.team-btn` is used widely (team start/stop/resolve/board-
resolve/interject, deploy) — a single shared CSS rule change fixes every
call site at once, no per-button rework needed.

**Open for the future session:** whether other button/control color
pairings in `app/app.py` have the same undetected drift — a quick
contrast audit of the page's full CSS palette might be worth doing in the
same pass rather than fixing `.team-btn` in isolation and finding a third
instance later.

---

## 21. Spawn an arbitrary number of AI instances per project via a "+" button

**Added 2026-08-14**, user-requested: "add to backlog that it should be
possible to spawn any amount of ai instances via a plus button in the
repos."

**Context — what exists today:** a project currently runs at most one
engine session at a time (`app/app.py`'s `_session_urls` is keyed by
project name — one tmux session, one engine, one hosted URL per project;
item 6's own backlog text flags this exact single-engine assumption).
Item 6's team feature generalizes this to N tmux **windows** inside one
team session, but the roster/composition is picked once at team-start
time and is otherwise fixed for that run's lifetime — there is no "add
one more" control today, only start-with-a-fixed-roster or stop-the-whole-
team. Item 19 added the ability to interject a message into an already-
running team, but not to grow the team itself mid-run.

**Not yet scoped — the request is genuinely ambiguous between two
different shapes, and picking the right one is a real product-manager
judgment call for a future session, not something to guess at here:**
1. **Grow a running team**: a "+" button on an already-started team adds
   one more teammate engine to the live roster (a new tmux window, a new
   `agent_run()` participant the lead can `delegate` to) — extends item
   6/6c's roster machinery rather than replacing it.
2. **Independent parallel instances, no team framework**: a "+" button
   spins up additional free-standing, non-team engine sessions against
   the same project working copy (or separate worktrees, following item
   6's own worktree-per-agent precedent) — each one a human drives
   directly, no lead, no delegation, closer to "open another terminal
   tab" than to growing a team.

**Open questions for whichever shape is picked:**
- If (1): does the lead need to be told a new teammate just joined
  mid-round, and does that reuse item 19 part 1's `human.jsonl`-style
  drain-at-round-boundary delivery mechanism, or does it need its own
  path?
- If (2): does each spawned instance get its own git worktree (avoiding
  concurrent-write conflicts on the same checkout, per item 6's existing
  per-agent-worktree precedent) or share the project's single working
  copy?
- Either way: any real ceiling on "any amount" — host resource limits
  (CPU/RAM per tmux pane, concurrent engine processes) probably need a
  configured cap, not literally unbounded spawning from a single button.
- UI: where does the "+" live — on the existing Teams page, the plain
  per-project row, or both, depending on which shape (1 vs 2) is chosen?

---

## Items 22-33: found by a real Proxmox end-to-end test (2026-08-15)

Every item below was found by a separate Claude Code session running
directly on a real Proxmox VE host, given a mission to create a genuinely
new LXC container via `ct/create.sh` and hands-on test every feature —
first real end-to-end validation of the items-1-21 consolidation, as
opposed to the mocked/unit test suite and code review that had been the
only prior verification. Items 22, 24, 25, 26, 27, 28, 29 mean **a fresh
install is currently broken** in ways the existing 1198-test suite has no
way to catch (wrong file ownership, wrong user context, a missing `cp`
line) — none of these are logic bugs the mocked tests could ever have
exercised, since the tests never run a real `useradd`/`chown`/multi-user
`sudo -u` boundary the way an actual install does.

---

## 22. Fresh install crash-loops immediately — `install.sh` never copies `app/taiga_board.py`

`ai-dev-switchboard.service` fails to start at all, on every single fresh
install, regardless of which optional features are selected:

```
× ai-dev-switchboard.service - ai-dev-switchboard
     Active: failed (Result: exit-code)
    Process: 9380 ExecStart=/usr/bin/python3 /opt/ai-dev-switchboard/app.py (code=exited, status=1/FAILURE)
```
```
ModuleNotFoundError: No module named 'taiga_board'
  File "/opt/ai-dev-switchboard/app.py", line 2399, in <module>
    import teams
  File "/opt/ai-dev-switchboard/teams.py", line 55, in <module>
    import taiga_board
```

`teams.py` unconditionally imports `taiga_board` at module load (not gated
behind Taiga being enabled), and `app.py` unconditionally imports `teams`
— so this crashes the whole service even for someone who never touched
`--with-taiga`. Root cause: `install.sh`'s app-copy step only copies two
of the three files that live in `app/`:

```bash
cp "$REPO_DIR/app/app.py" "$INSTALL_DIR/app.py"
cp "$REPO_DIR/app/teams.py" "$INSTALL_DIR/teams.py"
```

`app/taiga_board.py` (added for item 7) was never added to this list.

**Shape of the fix**: add `cp "$REPO_DIR/app/taiga_board.py"
"$INSTALL_DIR/taiga_board.py"` next to the other two `cp` lines. Worth a
quick grep of `app/` for any other `.py` file that isn't `app.py`/
`teams.py` before calling this done, since this is exactly the kind of gap
that silently reappears the next time a new module gets added there.

---

## 23. `install.sh`'s own printed Gitea-admin-bootstrap command fails non-interactively

After `--with-git-hosting`, `install.sh` prints this as the required first
one-time step:

```
docker exec -it --user git ai-dev-switchboard-gitea gitea admin user create \
  --admin --username <name> --password <password> --email <email>
```

Run exactly as printed, without an attached TTY (e.g. via `pct exec` or
any provisioning script):

```
cannot attach stdin to a TTY-enabled container because stdin is not a terminal
```

The command already passes every value as a flag — nothing about it is
actually interactive — so `-it` serves no purpose and is the only thing
breaking it. Dropping `-it` succeeds immediately.

**Shape of the fix**: drop `-it` from the printed command in `install.sh`
(the string around line 937).

---

## 24. `/var/lib/ai-dev-switchboard` is root-owned — silently breaks Gitea poll-sync and the description cache

`create_project()`'s own repo-map write (`_save_gitea_repo_map_entry`) is
documented as best-effort and swallows `OSError` — which is exactly what
happens, every time, on a fresh install: `GITEA_REPO_MAP_FILE`
(`/var/lib/ai-dev-switchboard/gitea-repo-map.json`) and `DESC_CACHE_FILE`
can never be created, because `switchboard-svc` has no write access to
their parent directory:

```
$ ls -la /var/lib/ai-dev-switchboard/
drwxr-xr-x  3 root            root            4096 ... .
drwxr-xr-x  2 switchboard-svc switchboard-svc 4096 ... uploads
```

Because the write is silently swallowed, a brand-new Gitea-hosted
project's repo-map entry never gets written, so poll-sync (item 2c) has
nothing to poll for it — no error anywhere, in logs or the UI. Root cause
in `install.sh`:

```bash
STATE_DIR=/var/lib/ai-dev-switchboard
mkdir -p "$CONFIG_DIR" "$INSTALL_DIR" "$STATE_DIR"   # created root:root, mode 755
...
mkdir -p "$STATE_DIR/uploads"
chown "$SVC_USER:$SVC_USER" "$STATE_DIR/uploads"      # only this one subdir gets chowned
```

`$STATE_DIR` itself is never chowned — only the one subdirectory
install.sh happens to pre-create. Same defect class as item 26 below.

**Shape of the fix**: `chown "$SVC_USER:$SVC_USER" "$STATE_DIR"` right
after creating it, before any of the more specific subdirectory chowns.
Confirmed sufficient by direct test.

---

## 25. Gitea poll-sync is completely non-functional even after fixing item 24 — `gitea-sync-project.sh` runs as `RUN_USER` but can't read `switchboard.env`

Even with item 24 fixed, sync still always fails silently (the poll's own
non-zero-exit handling deliberately just retries next interval, never
surfaces the failure), so a push to a Gitea-hosted project's repo never
fast-forwards `PROJECTS_DIR`. Reproducing the actual dispatched subprocess
call directly:

```
$ sudo -u dev /usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh e2e-sync-test main
/usr/local/bin/ai-dev-switchboard-gitea-sync-project.sh: line 38: /etc/ai-dev-switchboard/switchboard.env: Permission denied
```

`gitea-sync-project.sh` is explicitly documented and dispatched to run as
`RUN_USER` (`dev`), not root, but its first real statement sources
`/etc/ai-dev-switchboard/switchboard.env` — mode 600,
`switchboard-svc`-owned. `dev` can't read it, `source` fails, and
`set -euo pipefail` exits the whole script before doing anything.

Verified the diagnosis by temporarily loosening the file to 644 (reverted
immediately — this exposes `GITEA_API_TOKEN`/`SIMPLE_PASSWORD`/
`TOTP_SECRET` to every local user): the exact same command then genuinely
fast-forwards the repo and exits 0. The sync logic itself is correct —
only the credential-file read is broken.

**Shape of the fix**: `gitea-sync-project.sh` only actually needs
`RUN_USER`/`PROJECTS_DIR` — both static per-install values already known
at `install.sh` time. Have `install.sh` write those two specific values
into a small, world-readable file the script sources instead of the same
600 file that also holds live secrets. **Do not** just loosen
`switchboard.env` to 644 — that leaks real credentials to every account on
the box, including `RUN_USER`'s own coding-agent sessions.

---

## 26. `install.sh`'s code-server step leaves `~RUN_USER/.local` root-owned — blocks `pip --user`/`pipx`/any XDG user-install

With `--with-code-server`, `dev` (RUN_USER) can never write directly under
their own `~/.local`, blocking `pipx install`, `pip install --user`, and
anything else following the XDG base-dir convention:

```
$ ls -la /home/dev/
drwxr-xr-x 3 root root 4096 ... .local        <- root-owned!
$ sudo -u dev mkdir /home/dev/.local/testdir
mkdir: cannot create directory '/home/dev/.local/testdir': Permission denied
```

Root cause, `install.sh`:

```bash
CODE_SERVER_DIR="/home/$RUN_USER/.local/share/code-server"
...
mkdir -p "$CODE_SERVER_USER_DIR"          # creates .local, .local/share, etc — ALL as root
...
chown -R "$RUN_USER:$RUN_USER" "$CODE_SERVER_DIR"   # only chowns .local/share/code-server and below
```

Same defect class as item 24: `mkdir -p` run as root creates every
intermediate directory as root:root, and the following `chown -R` only
reaches the specific subtree the code already knew the name of — `.local`
and `.local/share` themselves, two levels up, are never touched.

**Shape of the fix**: chown the top-level `/home/$RUN_USER/.local`
recursively instead of starting the recursive chown two levels deeper at
`$CODE_SERVER_DIR`.

---

## 27. Multi-agent teams cannot start at all — git's "dubious ownership" check blocks `switchboard-svc` on every project, with a misleading error

**The most severe finding in this round**: item 6 (multi-agent
orchestration), the project's largest and most architecturally novel
feature, cannot be exercised at all on a fresh install, against any
project.

```
POST /projects/proj-a/team/start {"task": "..."}
→ 400 {"error": "not a git repository"}
```

...against a project that unquestionably is a clean git repo. The error is
flatly wrong and actively misleading. Root cause: `_check_git_repo_state()`
runs `git -C workdir rev-parse --is-inside-work-tree` as `SVC_USER`
(`switchboard-svc`), read-only, no `sudo -u dev` (a deliberate design
choice — "no privilege crossing needed"). But every project directory is
owned by `RUN_USER` (`dev`), a different user — and git ≥2.35.2's "dubious
ownership" protection (CVE-2022-24765) refuses to operate on a repo owned
by someone other than the calling user unless that path is in the
caller's own `safe.directory` list:

```
$ sudo -u switchboard-svc git -C /home/dev/projects/proj-a rev-parse --is-inside-work-tree
fatal: detected dubious ownership in repository at '/home/dev/projects/proj-a'
```

`_check_git_repo_state()`'s own error-mapping treats any non-"true" result
as `"not a git repository"` — it can't distinguish "genuinely not a repo"
from "blocked by a safety check," so the real cause is fully hidden.
`install.sh` never configures `safe.directory` for `SVC_USER` at all — a
total gap, not a partial one.

**Shape of the fix**: `install.sh` should run, once, as `$SVC_USER`:
```bash
sudo -u "$SVC_USER" git config --global --add safe.directory '*'
```
Verified live that the literal `*` (git's documented "trust every
directory" escape hatch) is what's actually needed — a glob does **not**
work (git's `safe.directory` only matches literal paths or `*`), and a
fixed list of literal paths can't work since projects are created
dynamically after install. Real, bounded security trade-off worth being
explicit about: `switchboard-svc` only ever runs read-only inspection git
commands (this check) plus RUN_USER-crossing operations that already go
through `sudo -u RUN_USER` for anything that writes — so trusting all
directories for git operations run as `switchboard-svc` specifically
doesn't hand out any new privilege beyond what the account already
effectively has via those sudo rules. Worth a line in
`docs/ARCHITECTURE.md`'s privilege-boundary writeup.

---

## 28. Every worktree creation and every headless engine turn fails — team-lifecycle scratch dirs are `switchboard-svc`-owned but written into by `RUN_USER`

Second critical blocker for item 6, found immediately after fixing item
27 — with both fixed, teams actually work end to end.

```
POST /projects/proj-a/team/start {...}
→ 400 {"error": "failed to create worktree for 'aider': command session ended unexpectedly"}
```

That message is `_run_run_user_command()`'s own fallback for "the tmux
session vanished and no rc-file was ever written" — a real, documented
edge case for a fast command racing the poll loop, but that's not what's
actually happening. The `tmux new-session` call itself succeeds cleanly,
but the `git worktree add` command inside it can never actually run,
because the directory it's asked to redirect its own stdout/stderr/pid/rc
into is owned by a different user with no write access for anyone else:

```
$ ls -la /var/lib/ai-dev-switchboard/teams/_worktree_ops/
drwx--x--x 2 switchboard-svc switchboard-svc ... <op_id>/
```

`rundir` is created by `app.py` (running as `switchboard-svc`) via
`os.makedirs(rundir); os.chmod(rundir, 0o711)` — but the actual command
inside it is dispatched via `sudo -u dev tmux new-session ...`, i.e. it
runs as `RUN_USER`, which has no write bit on `0o711` at all. The whole
redirect-and-background line fails at its very first redirect — nothing
ever gets written, and the generic "vanished with no rc" fallback fires.
Looks exactly like a fast-command race but is actually a hard,
100%-reproducible permission wall.

The exact same bug, independently, blocks every headless engine turn —
every actual delegation to a teammate — via the sibling
`_run_headless_session()`'s own `rundir`. Same ownership mismatch, same
silent symptom.

**Shape of the fix**: `os.chmod(rundir, 0o733)` (owner rwx, world -wx) at
both call sites — `_run_run_user_command()` and `_run_headless_session()`
— instead of `0o711`. Verified this exact change makes team-start,
worktree creation, and headless delegation all work correctly. Files
written into `rundir` by `RUN_USER` inherit a normal `022`-umask mode
(world-readable), so `switchboard-svc` reading them back afterward is
unaffected — only the directory's write bit for "other" was ever the
problem. Also worth adding: the `subprocess.run(TMUX + ["new-session",
...])` calls at both these sites (and a third at app.py:1139) don't
capture/check their own return code or stderr at all — that's exactly
what made this bug hard to diagnose from the outside. Capturing
`capture_output=True` there and threading a real error message through
would make any future failure of this kind far faster to diagnose.

---

## 29. `board_read`/`board_write` always report "Taiga isn't configured" — the shared config path resolves differently for the writer and the reader

Item 7's core mechanism, broken by the exact documented setup flow.
Following `scripts/taiga-configure-push.sh`'s own documented usage
exactly ("Run once, by RUN_USER") writes
`~/.config/ai-dev-switchboard/taiga-push.env`. Starting a team whose lead
calls `board_read`:

```
{"kind": "tool_result", "text": "Taiga error: Taiga isn't configured — run scripts/taiga-configure-push.sh first (expected config at /home/switchboard-svc/.config/ai-dev-switchboard/taiga-push.env).", ...}
```

Note the path: `/home/switchboard-svc/...`, not `/home/dev/...` where the
setup script actually wrote it. Root cause: both `taiga_push_spec.py`
(invoked as `RUN_USER`) and `app/taiga_board.py` (invoked as `SVC_USER`,
from inside the lead loop) independently compute the exact same-looking
default:

```python
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/ai-dev-switchboard/taiga-push.env")
```

...but `~` expands relative to whichever user's process evaluates it —
two entirely separate files, and the one place the setup instructions
tell you to create it is never the one the board tools read. Every team
lead that ever calls `board_read`/`board_write` on a fresh install will
report "not configured," even after a user follows the docs exactly.

Confirmed the fix by copying the file to `switchboard-svc`'s own home,
chowned correctly — after which `board_read`/`board_write` both worked
correctly, including a real propose → approve landing on the real Taiga
card, and a clean propose → reject path.

**Shape of the fix**: pick one location and have both consumers agree on
it explicitly — resolve the path relative to `RUN_USER`'s home
(`f"/home/{RUN_USER}/.config/..."`, matching where the setup script and
its own docs already point) in `taiga_board.py` too, rather than each side
independently calling `os.path.expanduser("~/...")` in its own process
context. `taiga-configure-push.sh` already writes 600-mode/RUN_USER-owned,
so `app.py` (running as a different user) reading it will need either a
narrowly-scoped read grant or for `install.sh` to create the file's
directory with the right shared permissions up front.

---

## 30. Taiga's gateway container reliably fails to come up via `docker compose up` — no retry, and one failure can wedge it long-term

Toggling Taiga on left the actual public entrypoint (`taiga-gateway`, port
9000) failing every time, while the other 8 containers came up fine:

```
Error response from daemon: failed to set up container networking: driver failed
programming external connectivity on endpoint ...taiga-gateway-1: failed to bind
host port 127.0.0.1:9000/tcp: address already in use
```

Nothing was actually listening on 9000 at the moment of failure (checked
directly, several ways) — looks like a genuine, brief Docker-internal
port-bind race rather than a real conflicting process, but the container
is left in a broken state by it (`NetworkSettings.Networks` comes back
`{}`, and a full `docker rm -f` + recreate reliably reproduces the
identical empty-network state, 8 consecutive attempts). A plain `docker
run` with `--network`/`-p` given together at creation time attaches the
network correctly on the first try, unlike Compose's separate
create→connect→publish sequence — suggesting a real ordering bug in this
specific Docker/Compose version combination when the port-bind step hits
even a transient EADDRINUSE.

Root cause not fully pinned down — flagging explicitly rather than
guessing further. Best-supported theory (not confirmed): this container's
root disk filled completely mid-`docker compose up` (item 31) right before
this first appeared, and Docker's own internal state around that attempt
may never have fully recovered even once disk space was restored. A
genuinely fresh install with adequate disk from the start may not hit this
at all — untested separately.

**Shape of the fix**: regardless of root cause, `taiga-up.sh` has zero
resilience to this — one bad `docker compose up -d` pass leaves the whole
feature silently, indefinitely broken (correctly reported as "off" by
`taiga-status.sh`, not a lying UI — but with no path back to "on" without
manual `docker` surgery). Worth having `taiga-up.sh` detect a gateway
container stuck in `Created`/`Exited` after the compose call and retry a
`docker rm -f` + re-`up` a bounded number of times before giving up
loudly.

---

## 31. Default 8G container disk is under-provisioned once git-hosting + code-server + Taiga are all enabled — fills completely, breaks Postgres with a misleading error

Following Advanced Install exactly with the installer's own default disk
size (`DEFAULT_DISK_GB="8"`) fills the container's entire root disk:

```
$ df -h /
Filesystem                        Size  Used Avail Use% Mounted on
/dev/mapper/pve-vm--900--disk--0   7.8G  7.4G     0 100% /
```

(base Debian 12 + apt packages ≈2.2G, Docker images/containers/volumes for
Gitea+Taiga ≈3.9G, aider's own pipx venv ≈683M, plus the rest.) The actual
symptom a real user would see is `taiga-db`'s Postgres instance failing to
start with `FATAL: could not write init file` — no "no space left on
device," nothing that points at `df`. Someone hitting this with the
default install would have no reason to suspect disk space at all.

**Shape of the fix**: raise `DEFAULT_DISK_GB` (`ct/create.sh`) — 8G is
tight even without Taiga; with all four optional features it's not viable
at all. A default closer to 20–24G (host permitting — the storage-pool
step already shows free space live, so it could size its own suggestion
off that) would give real headroom. Independent of the default,
`taiga-up.sh`/`gitea-up.sh` could cheaply `df` the target filesystem
before calling `docker compose up` and refuse with a clear message instead
of letting Postgres's own opaque error be the first sign of trouble.

---

## 32. Live-enumerated network-bridge menu includes per-container firewall bridges, not just real uplinks

Advanced Install's bridge-selection menu, populated via `ip -o link show
type bridge`, also lists the auto-created `fwbrXXXiY` bridges Proxmox
creates for other containers' firewall rules on a host with per-container
firewalling enabled — not just real switch/uplink bridges a new container
should attach to:

```
Network bridge:
  vmbr0      kernel bridge
  fwbr101i0  kernel bridge
  fwbr106i0  kernel bridge
  fwbr107i0  kernel bridge
```

Picking one of the `fwbrXXXiY` entries by mistake would create a
container with effectively no working uplink — a plausible misclick on a
host that's been running a while, since nothing in the menu distinguishes
them from a real bridge.

**Shape of the fix**: filter `_enumerate_bridges()`'s `ip -o link show
type bridge` output to exclude the `fwbrNNNiM` naming pattern (Proxmox's
own fixed convention for these).

---

## 33. `/team/interject`'s error message names the wrong field

Minor/cosmetic. The interject route's actual JSON body key is `text`, but
its validation error says `"message must be non-empty and at most 2000
characters"` — nothing tells you the key it actually wants is `text`, not
`message`, and the mission's own wording ("interject a message") makes
`message` the natural first guess.

**Shape of the fix**: either rename the error text to say `text`
(matching the real field), or accept both key names. One-line fix either
way.
  per-project row, or both, depending on which shape (1 vs 2) is chosen?
---

## Items 22-33 regression verification (2026-08-15)

Fresh, isolated end-to-end verification of the 12 fixes above, against
current `main` (`53c3012`, all four E2E fix-round PRs merged). New
container from scratch via `ct/create.sh`, Advanced Install, all four
optional features on: CTID 901, hostname
`ai-dev-switchboard-e2e-verify`, IP `192.168.178.227`. The old test
container (CTID 900) was left untouched and not reused, per the mission's
own instruction (it had several of these fixes hand-patched locally on
top of the old broken code).

**Verdict summary** (all 12 tracker items; two additional bugs found
along the way are recorded separately below as items 34-35):

| Item | Verdict |
|---|---|
| 22 (crash-loop on fresh install) | **Confirmed fixed** |
| 23 (Gitea admin-bootstrap TTY) | **Confirmed fixed** |
| 24 (`/var/lib/ai-dev-switchboard` root-owned) | **Confirmed fixed** |
| 25 (Gitea poll-sync non-functional) | **Confirmed fixed** |
| 26 (`~RUN_USER/.local` root-owned) | **Confirmed fixed** |
| 27 (dubious-ownership blocks teams) | **Confirmed fixed** |
| 28 (worktree/headless-turn failure) | **Confirmed fixed** |
| 29 (Taiga "isn't configured") | **Still broken in practice** (path bug fixed, new blocker found) |
| 30 (gateway port-bind race) | **Still broken in practice** (race reliably reproduced; shipped retry doesn't recover it here) |
| 31 (8G disk fills up) | **Confirmed fixed** |
| 32 (firewall bridges in menu) | **Confirmed fixed** |
| 33 (interject error field name) | **Confirmed fixed** |

### Confirmed fixed (22-28, 31-33) — evidence

- **22**: `systemctl status ai-dev-switchboard` was `active (running)`
  immediately after `create.sh` finished, and again after every later
  restart. No `ModuleNotFoundError`.
- **23**: `docker exec --user git ai-dev-switchboard-gitea gitea admin
  user create --admin --username admin --password '...' --email ...`,
  run via `pct exec 901 -- bash -c '...'` (no TTY), succeeded: "New user
  'admin' has been successfully created!"
- **24**: `/var/lib/ai-dev-switchboard` is `switchboard-svc`-owned;
  `sudo -u switchboard-svc touch .../testwrite` succeeded. Created a
  Gitea-hosted project (`testproj`) and confirmed
  `gitea-repo-map.json` got a real entry (`admin/testproj`, branch
  `main`).
- **25**: `sudo -u dev cat /etc/ai-dev-switchboard/runtime.env` succeeded,
  showing `RUN_USER=dev` / `PROJECTS_DIR=/home/dev/projects`. Pushed a
  real commit to the Gitea repo from an external clone, polled `/status`
  past `GITEA_POLL_INTERVAL_SECONDS` (default 45s), confirmed
  `PROJECTS_DIR/testproj` actually fast-forwarded to the pushed SHA and
  `gitea-repo-map.json`'s `sync_state` went to `"synced"`.
- **26**: `sudo -u dev mkdir /home/dev/.local/testdir` succeeded;
  `/home/dev/.local` is `dev`-owned (code-server + pipx both installed
  cleanly under it during this same session).
- **27**: `sudo -u switchboard-svc git -C .../testproj rev-parse
  --is-inside-work-tree` → `true`. `POST /projects/testproj/team/start`
  did not 400 with "not a git repository" — it launched a real team run.
- **28**: A real team run (ollama lead on `qwen2.5:7b` + `aider` as a
  real, installed, Ollama-backed member — `claude`/`codex` were left
  uninstalled/unauthenticated in this sandbox and skipped, noted below)
  created real git worktrees at `testproj.teams/{aider,claude,codex}`
  and completed multiple real headless `aider` delegate calls end to
  end — actual file edits, actual commits (`docs: append HELLO-TEAM to
  README.md`, etc.), not the original "command session ended
  unexpectedly" failure.
- **31**: `grep DEFAULT_DISK_GB ct/create.sh` → `20`. With all four
  optional features enabled and exercised (Gitea, Taiga, code-server,
  Ollama-link) plus aider installed, disk peaked around 42% of the 20G
  disk (`df -h /`), never filled.
- **32**: The live Advanced Install bridge menu, captured from the real
  whiptail session, showed only `vmbr0` — the host had three real
  `fwbrNNNiM`-pattern interfaces present at the time (`fwbr101i0`,
  `fwbr106i0`, `fwbr107i0`, confirmed via `ip -o link show type bridge`
  run separately) and none of them appeared in the menu.
- **33**: `POST /team/interject` with an empty/missing `text` field now
  returns `{"error": "text must be non-empty and at most 2000
  characters"}`.

### 29 — still broken in practice: the path-mismatch is fixed, but a new permission gap blocks the same symptom

The original bug (fixed in `6089b04`) was RUN_USER's setup script and
SVC_USER's read path resolving `~` to two different homes. That part is
genuinely fixed — confirmed the resolved path is now
`/home/dev/.config/ai-dev-switchboard/taiga-push.env` on both sides.

But running `scripts/taiga-configure-push.sh` exactly as documented
writes that file `600`-mode, `dev`-owned (its own explicit, intentional
security choice — see the script's own comments). `switchboard-svc` (the
user `app.py`/`teams.py` actually run as) is not in any group that file
belongs to and has no ACL grant, so its `open()` call raises
`PermissionError` — a subclass of `OSError`. `taiga_board.py`'s
`load_config()` catches bare `OSError` around that `open()` and reports
the exact same message as the *missing-file* case:

```
Taiga isn't configured — run scripts/taiga-configure-push.sh first
(expected config at /home/dev/.config/ai-dev-switchboard/taiga-push.env).
```

So a real user who runs the documented setup script exactly as
instructed still gets told "Taiga isn't configured" from `board_read`/
`board_write` — the user-visible symptom this item set out to fix is
unchanged, just from a different root cause. Confirmed by reproducing
live: `sudo -u switchboard-svc cat .../taiga-push.env` → `Permission
denied`; a team lead's `board_read` call failed with the message above.

This exact risk was already named, unimplemented, in item 29's own
original "Shape of the fix" write-up above ("`app.py` ... reading it will
need either a narrowly-scoped read grant or for `install.sh` to create
the file's directory with the right shared permissions up front") — the
path fix shipped, that follow-up did not.

Verified the rest of the mechanism works correctly once this specific
gap is closed: manually widened the file to `640`/`dev:switchboard-svc`,
re-ran the same team run — `board_read` returned real story data, a
`board_write(append_comment)` call correctly blocked as
`blocked_board_write`, `POST .../team/board-resolve` with
`{"action":"approve"}` resolved it, and the comment ("Hello from the AI
team -- this confirms Taiga integration.") is now really on the Taiga
story's history via a direct API check. The mechanism itself (item 7's
propose/approve flow) is sound; only the out-of-the-box file-permission
gap blocks it.

### 30 — still broken in practice: the race reproduces reliably here, and the shipped retry doesn't recover it

Toggling Taiga on genuinely reproduced the original race — repeatedly,
not a one-off:

```
Error response from daemon: failed to set up container networking: driver failed
programming external connectivity on endpoint ...taiga-gateway-1: failed to bind
host port 127.0.0.1:9000/tcp: address already in use
```

`taiga-status.sh` correctly reports `off` (not lying) every time this
happens — that part of the item's own scope holds. But the shipped fix
(`taiga-up.sh`'s bounded 3-attempt `rm -f` + retry loop, confirmed
present and running exactly as designed via `bash -x`) did **not**
recover the gateway in any of several real attempts: all 3 quick
retries (~2s apart) hit the identical "address already in use" error.
`ss -ltnp` confirmed nothing is actually listening on port 9000 at the
OS level while this is happening — the failure is Docker-daemon-internal
port-allocator state, not a real conflicting process. It only cleared
after either an unpredictable longer wait (tens of seconds to a couple
of minutes, confirmed twice) or a full `systemctl restart docker`
(confirmed once, reliably).

A **second, distinct** crash mode was also caught live, immediately
after a `docker compose up -d` that otherwise looked clean:

```
2026/08/15 18:35:22 [emerg] 1#1: host not found in upstream "taiga-front" in /etc/nginx/conf.d/default.conf:9
nginx: [emerg] host not found in upstream "taiga-front" in /etc/nginx/conf.d/default.conf:9
```

— `taiga-gateway`'s nginx resolves `taiga-front` via Docker's embedded
DNS at startup and doesn't retry; if it starts before `taiga-front`'s DNS
entry has propagated, it exits immediately (exit 1) and stays exited.
Same user-visible "off" symptom, different mechanism, also not covered
by the current retry (which only guards the port-bind failure shape, not
this one).

Per the original item's own framing ("if you can't force the original
race to occur at all, say so plainly") — the opposite happened here: the
race was forced repeatedly and reliably, which is new, useful signal the
first report didn't have. **Recommended follow-up**: the bounded retry's
timing (~2s between attempts) is too short for whatever this host's
Docker actually needs to clear its internal state; either lengthen the
retry backoff substantially (tens of seconds, exponential), or have
`taiga-up.sh` fall back to `systemctl restart docker` after N failed
attempts, which was the only 100%-reliable recovery found here. The
nginx/DNS race is a second, separate failure mode worth its own fix
(e.g. nginx `resolver` directive with retry, or a short sleep/health-wait
on `taiga-front` before starting `taiga-gateway`).

---

## 34. Fresh Advanced Install with git-hosting never picks up `GITEA_ENABLED` — service starts before Gitea's config block is appended

Found while setting up this verification container, not part of the
original 12. `install.sh` calls `systemctl enable --now
ai-dev-switchboard` (starting the service, which reads
`switchboard.env` once via `EnvironmentFile=`) *before* the
`--with-git-hosting` block later in the same script appends
`GITEA_ENABLED`/`GITEA_PORT`/etc. to that same file — and never restarts
the service afterward (unlike `--update`, which does restart, guarded).

Result: on a genuinely fresh `--yes --with-git-hosting --with-taiga
--with-ollama` install, `TAIGA_ENABLED` and `TEAM_LLM_BASE_URL`/`MODEL`
(written before the service start) work immediately, but
`GITEA_ENABLED` is simply absent from the running process's environment
— `POST /gitea/on` returns `{"error": "gitea disabled"}` even though
`switchboard.env` on disk clearly has `GITEA_ENABLED=1`. Confirmed via
`/proc/<pid>/environ`: no `GITEA_*` keys at all in the first process,
all of them present after a manual `systemctl restart
ai-dev-switchboard`.

**Shape of the fix**: move `systemctl enable --now ai-dev-switchboard`
to after all four optional-feature config blocks have finished writing
to `switchboard.env` (or add an unconditional restart at the very end of
the script, mirroring what `--update` already does).

---

## 35. A team run that ends in `escalated_max_rounds` (or normal `finished`) leaves its tmux session and worktrees behind, blocking the next team start

Also found incidentally, not part of the original 12. Once a team run
reaches ANY terminal status — including ordinary success
(`status: "finished"`), not just escalation — its `team-<project>` tmux
session and per-agent worktrees (`<project>.teams/<agent>`) are left in
place. `POST .../team/stop` no-ops for a non-`running`/`blocked_*`
status ("no team currently running for this project"), so it never
cleans them up either. The next `POST .../team/start` then refuses
outright:

```
{"error": "a team is already running for 'testproj' (team-testproj) -- stop it first"}
```

— because `launch_team()`'s own "already running" check looks for the
tmux session's existence, not the run's actual status. Reproduced this
on every team run in this session (both an `escalated_max_rounds` run
and a normal `finished` run); each time, starting the next run required
manually `tmux kill-server` plus `git worktree remove --force` on all
three agent worktrees plus deleting their branches, none of which the
web UI's own `/team/stop` (or anything else in the API) offers a way to
do.

**Shape of the fix**: either have `/team/stop` recognize terminal
statuses too (clean up the tmux session/worktrees for a `finished` or
`escalated_max_rounds` run, not just an active one), or have
`launch_team()`'s pre-flight check look at the run's actual status
instead of raw tmux-session existence, so a terminal run's leftover
session doesn't block a fresh one.

---

## Round 5 fixes (2026-08-15): items 29-v2, 30-v2, 34, 35

Closes the two gaps the regression-verification pass (above) found still
open after rounds 1-4, plus items 34 and 35 found incidentally during
that same verification pass.

- **29-v2** (permission gap on the now-correctly-pathed Taiga config):
  `scripts/taiga-configure-push.sh` now grants `switchboard-svc` a
  narrowly-scoped POSIX ACL read grant (`setfacl -m u:<svc>:r`) on the
  config file after writing it, sourcing the service-user name from
  `runtime.env` rather than hardcoding it. `taiga_board.py`'s
  `load_config()` gains a distinct `except PermissionError:` (ordered
  before the existing `except OSError:`, since `PermissionError` is a
  subclass) so a real permission failure is reported differently from a
  genuinely-missing file.
- **30-v2** (retry insufficient to survive the real-world recovery
  window): `taiga-up.sh`'s retry loop goes from 3 flat 2s attempts to 5
  attempts with exponential backoff (10/20/40/80s, ~150s worst-case). A
  new opt-in-only `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` (default `0`)
  falls back to `systemctl restart docker` after all attempts are
  exhausted — deliberately not automatic, given the host-wide blast
  radius of restarting the Docker daemon. `taiga_run()`'s backend
  timeout for the `"up"` action was raised from 90s to 180s to give the
  longer retry loop room to actually finish (150s of sleep alone already
  exceeded the old 90s ceiling), and the frontend's matching
  `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` was raised in lockstep to
  preserve the two timeouts' existing keep-in-sync invariant. The
  Taiga toggle checkbox is now disabled for the duration of the
  "starting…" state to prevent an operator from firing a second
  concurrent `taiga_run("up")` during a long retry.
- **34**: the guarded-restart block that previously only ran on the
  `--update` path now runs unconditionally at the very end of
  `install.sh`, after all four optional-feature config blocks have
  finished writing to `switchboard.env` — so a fresh install picks up
  `GITEA_ENABLED` (and everything else written late) without requiring
  a manual `systemctl restart` afterward.
- **35**: `/team/stop`'s status gate narrowed to just `if run is None`
  (was also refusing to no-op-clean a terminal `finished`/
  `escalated_max_rounds` run) — `stop_team()` itself was already correct
  for every status, the bug was isolated to this one redundant check in
  the route.

Reviewed via a full developer→reviewer cycle including one fix-back
round (see `docs/test-review.md`); full test suite passes at 1213 tests
with the same 3 pre-existing failures (caused by an untracked `CLAUDE.md`
in the repo root, unrelated to this round, confirmed via `git stash`).

---

## 36. Taiga toggle's "off" path has the same double-submission race the "on" path had (item 30-v2), never closed

Found by the reviewer while re-verifying the round 5 fix for item 30-v2's
on-path double-submission guard (above). The fix there only disables the
checkbox during the "starting…" (on) transition. The "off" transition
shows `'stopped'` optimistically and immediately, with the checkbox never
disabled, while `taiga_run("down")` (up to 90s) may still genuinely be in
flight server-side — an operator could click back on during that window
and fire a concurrent `taiga_run("up")` against the same Docker Compose
stack the in-flight `"down"` call is still tearing down. Not a regression
from round 5 (the existing `offPendingCount` mechanism was always a
UI-consistency guard, not a backend-concurrency one) — pre-existing,
carried forward as its own item since it's the same class of bug as
30-v2 and worth closing the same way.

**Shape of the fix**: mirror round 5's on-path fix — disable the toggle
for the duration of the off-dispatch's in-flight window too, re-enabling
on the terminal response (or timeout).

---

## Round 5 regression verification (2026-08-15)

Verified against a fresh container (CTID 902, `ai-dev-switchboard-e2e-verify2`)
built from `main` @ `89aeb83`, by the same Proxmox verification session as
the earlier passes. `ct/create.sh` couldn't be driven interactively from
that session (whiptail-only, no non-interactive flag), so its `pct
create`/`install.sh` invocation was replicated verbatim instead — all
four round-5 items live in `install.sh`/the app, so this doesn't weaken
the verdicts below, but wizard-specific paths (item 32's bridge menu)
were not re-exercised this pass.

- **29 — confirmed fixed.** `setfacl` grant lands narrowly
  (`user:switchboard-svc:r--`, `group::---`, `other::---`); `sudo -u
  switchboard-svc cat` succeeds where it previously got Permission
  denied; `load_config()` returns all 4 keys as that user, and the two
  error paths are properly distinct. A real team lead's `board_write`
  now fails with a genuine connectivity error ("Could not reach Taiga at
  http://127.0.0.1:9000") instead of "isn't configured" — exactly the
  distinction the fix was for. The full propose→approve-onto-a-real-card
  cycle couldn't be re-run because item 30 kept the gateway down the
  whole session. **See item 37: this fix silently reverts under normal
  operator behavior.**
- **34 — confirmed fixed.** All `GITEA_*` keys present in the first
  process's environment with no manual restart; `POST /gitea/on` → `200
  {"ok": true}`.
- **35 — confirmed fixed.** Drove a real run to `escalated_max_rounds`,
  then `/team/stop` → `{"session_removed": true, "worktrees": {"aider":
  "removed"}}` (previously a silent no-op), and the next `/team/start`
  succeeded with a fresh `run_id`.
- **30 — still broken.** The race reproduced on the very first `POST
  /taiga/on` of a fresh install; the round-5 backoff ran to full
  exhaustion (164s) without recovering.

  New evidence changes the diagnosis: while Docker reports `address
  already in use` for port 9000, nothing is actually bound to it
  (`ss -ltnap` empty, no docker-proxy, `/proc/net/tcp{,6}` clean).
  Meanwhile `taiga-front` is up and resolvable from a sibling container
  — Docker DNS itself is fine. The gateway container ends up `Created`
  with `NetworkSettings.Networks == {}` — never actually attached to the
  network.

  Read as one causal chain, not two independent races: the gateway
  binds 9000 → nginx dies immediately because it can't resolve the
  `taiga-front` upstream yet (a startup-ordering issue, not a DNS
  outage) → the exited container retains the port reservation → every
  remove+recreate collides on that retained port → the one attempt that
  gets past the collision lands network-unattached → `docker start`
  can't reattach it → nginx fails on the same unresolved upstream again.
  If this is right, **the retry can never win** — every attempt
  recreates straight back into the same nginx crash, because the
  fix targets the port-bind symptom, which is downstream of the real
  cause.

  Attempted recoveries, all failed: backoff exhaustion; manual `rm -f` +
  `up -d`; full `down` (network removed) + `up -d`; `systemctl restart
  docker` + `up -d` (started, then exited 1 on the same DNS failure
  seconds later); remove + 45s wait + recreate. The opt-in
  `TAIGA_UP_DOCKER_RESTART_ON_EXHAUSTION` fallback was not meaningfully
  exercised — the one run with it set happened to succeed on its first
  attempt (the port had cleared on its own by then), so this pass has no
  evidence either way on whether it actually recovers a wedged state.

  Separately (not the root cause, but worth folding into the same fix):
  `taiga-up.sh`'s success check is a single point-in-time `ps` state
  check. One run showed the gateway `Up` for under a second before
  crashing, so the script exited 0 and `taiga-status.sh` briefly
  reported `on` for a stack whose only public entrypoint was already
  dead — needs a settle/health confirmation, not just an initial state
  read.

  **Revised shape of the fix**: point the fix at nginx's upstream
  resolution instead of the port-bind symptom — e.g. an nginx `resolver`
  directive with its own retry/backoff so it doesn't crash-loop while
  waiting for `taiga-front` to become resolvable, or gate the gateway's
  own startup on `taiga-front` health before nginx ever starts. Also
  strengthen `taiga-up.sh`'s success check to confirm the container
  stays up past some settle window, not just that it was up at the
  moment of the check.

---

## 37. Item 29's fix silently reverts — the security-hygiene check that recommends `chmod 600` collapses the ACL grant that item 29 depends on

Found during round-5 regression verification, live. Setting the item-29
ACL grant (`setfacl -m u:switchboard-svc:r`) moves the group
permission bits into the ACL mask entry, so `stat` on the file now
reports mode `0640` even though the real *effective* permissions are
still narrow. `taiga_push_spec.py`'s existing security check reads that
raw `st_mode & 0o077` and, seeing group-readable bits, treats the file
as loosely permissioned and prints `Run: chmod 600 <path>` on every
`board_read`/`board_write` call.

Following that printed advice — the obvious thing an operator would do,
since it's presented as a fix — runs `chmod 600`, which recomputes the
ACL mask down to `mask::---`. That makes the `switchboard-svc` grant's
*effective* permission `---` even though the ACL entry itself is still
listed, and `switchboard-svc` goes back to Permission denied. Item 29
is silently undone by the tool's own advice, with no indication to the
operator that anything changed.

**Shape of the fix**: make the security check ACL-aware — read the
actual effective permissions (e.g. via `os.access()` for the specific
concern, or parse `getfacl`'s mask/effective annotations) rather than
raw `st_mode`, so a narrowly-ACL'd-but-`0640`-looking file isn't flagged
as loose, and the printed remediation never suggests an action that
breaks a legitimate cross-user grant.

---

## 38. `/status` reports a finished/escalated team run as `blocked` indefinitely — any poller waiting for completion hangs forever

Found during round-5 regression verification. A test run that had
already reached `escalated_max_rounds` in `run.json` (and which
`/team/stop` correctly treated as terminal, per the item-35 fix) was
still reported as `blocked` by `/status` 17+ minutes later. Anything
polling `/status` to detect completion — including this verification
pass's own poller — waits forever, since `blocked` is never resolved to
a terminal state on that endpoint. That run's `run.json` also carries
`"project": null` despite being correctly associated with `testproj`
everywhere else it's referenced; may share a root cause with the status
staleness.

**Shape of the fix**: find wherever `/status` derives its reported state
and make sure it's reading the same terminal-status source `/team/stop`
already uses (per item 35), not a stale/separately-computed value.
Worth checking why `run.json`'s `project` field ends up `null` in the
same run as a first step, since it may point at the same underlying
write path being incomplete or racy.

---

## Round 6 fixes (2026-08-15): items 30, 37, 38

Closes the two remaining crash/staleness bugs from round-5 verification
(item 30's crash-loop root cause, item 38's stuck `/status`) plus the
security-hygiene regression item 37 found (the tool that was supposed to
warn about a loose config file instead silently reverting item 29's ACL
fix).

- **30**: root cause was startup-ordering, not a transient port-bind
  race — `taiga-gateway`'s bundled nginx resolves `taiga-front` once at
  container-start with no `resolver` directive, so if `taiga-front` isn't
  attached to the network yet it exits immediately and its port
  reservation wedges every subsequent recreate. `install.sh`'s
  `docker-compose.override.yml` now gives `taiga-front` a healthcheck
  (`wget --spider http://127.0.0.1/` — not `localhost`, confirmed
  hands-on that BusyBox wget on this image resolves `localhost` to `::1`
  first and gets connection-refused) and upgrades `taiga-gateway`'s
  `depends_on` on it to `condition: service_healthy`, entirely inside the
  override file this repo already regenerates every run — no changes
  inside the pinned third-party `taiga-docker` checkout. `taiga-up.sh`
  also gained a settle-window recheck (`TAIGA_UP_SETTLE_SECONDS`,
  default 5s): a gateway reporting `running` that dies before the window
  elapses is now treated as a failed attempt rather than a false-positive
  success. `app.py`'s `TAIGA_UP_SCRIPT` timeout was raised 180s→220s (and
  the paired frontend `SINGLETON_TOGGLE_CONFIG.taiga.timeoutMs` in
  lockstep) to keep comfortable margin over the new worst-case retry
  arithmetic (175s of pure sleep across up to 5 attempts).
- **37**: `_check_config_permissions` in `scripts/taiga_push_spec.py` is
  now ACL-aware — when an extended ACL is present it reads `getfacl`'s
  `other::` entry (the one bit that's still a genuine leak regardless of
  named-user grants) instead of misreading the recomputed ACL mask in
  `st_mode`'s group bits as a loose group permission. It no longer prints
  a `chmod`-based remediation for an ACL'd file (which would collapse the
  item-29 grant); a genuinely loose ACL'd file now gets `setfacl`-based
  remediation instead. Falls back to the original plain `st_mode` check
  when `getfacl` is unavailable or the file has no extended ACL, so
  today's behavior is unchanged for the un-migrated case.
- **38**: added a single `TEAM_TERMINAL_STATUSES` constant to
  `app/teams.py`, replacing three previously-duplicated inline literal
  tuples (`stop_team()`, `sweep_dead_teams()`, `interject()`). `/status`
  now exposes an additive `team.terminal` boolean sourced from that same
  constant, so a poller can detect `escalated_max_rounds`/`finished`/
  `error`/`stopped` completion directly instead of inferring it from the
  coarser `status`/`waiting_on_you` fields (which is what let a poller
  hang indefinitely on an `escalated_max_rounds` run). The `"project":
  null` observation from item 38's original report was investigated
  fresh against current code and does not reproduce — no code path
  anywhere writes a literal `"project"` key (only `"project_name"`);
  treated as resolved-by-explanation, most likely a terminology slip in
  the original report.

Reviewed via a full developer→reviewer cycle, approved with one
non-blocking follow-up (this entry). Full test suite passes at 1232
tests with the same 3 pre-existing failures (untracked `CLAUDE.md` in
the repo root, unrelated to this round, confirmed via `git stash`).

---

## In-progress process (2026-08-15): full E2E verification loop + container migration

**Status of round 6**: implemented, reviewed (approved), committed as
`140a2ae` on branch `backlog/e2e-fixes-round6`, pushed to both remotes,
opened as [PR #33](https://github.com/LeTe0301/ai-dev-switchboard/pull/33)
— **still open/unmerged** as of this writing.

### The plan, in order

1. **Full hands-on E2E test pass** on a fresh Proxmox LXC container,
   covering every feature and every UI part (not just smoke-testing) —
   run by a separate Claude Code session connected via Remote Control
   (peer session, reachable through `ListAgents`/`SendMessage`, not a
   subagent of this session). That session creates its own fresh test
   container, installs `ai-dev-switchboard` with every optional feature
   flag on, exercises the full feature surface hands-on, writes a
   BACKLOG-style report (real repro evidence, not guesses), sends the
   report back here, then **destroys its own test container** (cleanup
   rule applies only to its own throwaway test containers, never to
   anything else on the host).
2. **On receiving a report with issues**: run the normal
   product-manager→ux-designer(if needed)→developer→reviewer pipeline
   (same as every round above) to fix them, commit, then re-brief the
   E2E session to test again against a new fresh container. Repeat until
   a report comes back clean.
3. **Once a report comes back fully clean**: this session (the
   orchestrator, working out of the `dev` sandbox) performs a **backup of
   all repos first** — explicitly stated as priority #1, no data loss —
   then creates a **new container** that is an exact clone of this whole
   environment, **including the Claude/agent configuration**
   (`~/.claude/CLAUDE.md`, agent definitions, memory files, settings —
   not just the raw git repos), transfers everything over, and confirms
   `ai-dev-switchboard` is available and working on the new container.
4. **Only after that backup+transfer is confirmed complete**, the E2E
   session (the pve-side Remote Control peer) shuts down *this*
   orchestrator session — not the other way around, and not itself.
5. **The new destination container is never deleted** — the
   destroy-after-use cleanup habit from step 1 applies only to the E2E
   session's own disposable test containers, never to the real migration
   target.

### State as of this writing (not yet actioned further)

- An E2E testing brief (full text: create fresh container → install with
  all features on → hands-on test every feature/UI part → write a
  BACKLOG-style report → send back → destroy own test container → wait)
  was sent twice to two different Remote-Control-connected peer sessions
  on the pve host, in sequence: first to `pve-wiggly-cosmos`, which died
  before reporting back (any container it left behind should be checked
  for and cleaned up by whichever session picks this up next); then,
  after re-checking `ListAgents`, to its replacement `pve-lively-hopper`
  (also briefed to check for and clean up anything `pve-wiggly-cosmos`
  left orphaned). **As of this writing, no report has been received
  back yet from either.**
- Sessions on the pve host have died/reconnected under new names before
  (`pve-wiggly-cosmos` → `pve-lively-hopper`) — if picking this up again,
  check `ListAgents` first; if the name has changed again, re-send the
  same E2E brief to whichever peer is currently listed (it has no memory
  of prior instructions — the brief must be resent in full, not assumed
  known).
- **Host-control SSH channel not yet usable from the `dev` sandbox**:
  this repo's optional `host-agent` feature (`host-agent/README.md`) uses
  a narrowly-scoped SSH key (`~/.ssh/host_control_ed25519` in this
  sandbox, public half `ssh-ed25519
  AAAAC3NzaC1lZDI1NTE5AAAAIDrTBlfcslf03bbcmtYAa80pSy9j0mrfVIlrsRxB9y67
  dev-ct-host-control`) to run exactly
  `sudo ai-dev-switchboard-host-{start,stop,status}.sh` on the pve host
  (`192.168.178.100`, port 8006 = Proxmox web UI, port 22 = SSH). Tried
  as both `root@` and `switchboard@` — both rejected
  ("Permission denied (publickey,password)"), meaning this key's public
  half is not yet authorized on either account on the real host. The
  user asked to add it to `root`'s `authorized_keys` but did not have
  host access at the time to do so. This channel was a secondary
  path (not currently load-bearing for the E2E loop, which runs over the
  already-connected Remote Control peer sessions instead) — only revisit
  if the Remote Control path stalls and direct host-agent control becomes
  necessary.

### Update (2026-08-16)

- `ListAgents` showed the pve-side peer under yet another new name,
  `pve-sparkling-meadow` — neither `pve-wiggly-cosmos` nor
  `pve-lively-hopper` is listed anymore, and no report had arrived from
  either. Re-sent the full E2E brief (create fresh container → install
  with all optional feature flags → hands-on test every feature/UI part
  → write a BACKLOG-style report → send back → destroy own test
  container → wait) to `pve-sparkling-meadow`, including the
  cleanup-check instruction to find/destroy any orphaned container left
  behind by the two prior dead sessions. **Report received back the same
  day** — see "Items 39-43" below. Not a clean report, so per step 2 of
  "The plan, in order" above, looping back into the fix pipeline
  (product-manager → developer → reviewer) rather than proceeding to
  backup+migration. `pve-sparkling-meadow` destroyed its own test
  container (CT110) after sending the report; no orphaned containers from
  the two prior dead sessions were found on the host.

### Round 7 fix cycle: complete (2026-08-16)

Items 39-43 were fixed via the normal product-manager → developer →
reviewer pipeline (no ux-designer needed — backend/install-script only),
approved with 2 non-blocking nits, committed as `5292112` on
`backlog/e2e-fixes-round6`, pushed to both remotes, still under PR #33.
Re-briefed `pve-sparkling-meadow` (same peer as round 6) for another
fresh-container retest round, specifically targeting all 5 fixed items
plus anything skipped last round (upload-from-folder, GitHub-origin
AI-reviewer path).

### Round 7 retest report (2026-08-16): 4/5 confirmed fixed, item 43 still broken (new mechanism, root cause now pinned down)

Tested `a6991c2` on a second fresh CT110 (destroyed after report), with
`AUTH_MODE=pve` + `PVE_HOST` pre-seeded before `install.sh --yes`.

- **#39 (AUTH_MODE)** — confirmed fixed. `switchboard.env` correctly kept
  `AUTH_MODE=pve`; verified `pve_login()` genuinely hits the real PVE
  ticket API and the login page/401 path both behave correctly. (A real
  end-to-end login with live PVE credentials wasn't completed — creating
  even a throwaway PVE-realm test account was blocked by the tester's own
  session permission classifier as a production-auth-state change, and it
  correctly didn't try to work around that. Verified via code-path
  inspection instead, which the tester flagged explicitly as a
  lower-confidence substitute for a real login round-trip.)
- **#40 (Gitea 403)** — confirmed fixed. Reproduced the exact original
  trigger (throwaway admin created first, then the real admin) —
  `gitea-configure-api.sh` succeeded cleanly on the first try.
- **#41 (code-server path)** — confirmed fixed. `CODE_SERVER_BIN`
  correctly resolved to `/bin/code-server`; toggling "Code" on now starts
  a real process, `/status` shows `code_on: true`, URL returns real `302`.
- **#42 (silent ok:true)** — confirmed fixed. `POST /host/on` with no
  target configured now returns `502` with real `stderr`
  ("...ssh: Could not resolve hostname...").
- **#43 (taiga-gateway race)** — **still broken, different mechanism**.
  The new plain-`up -d` fallback (added in round 7) now reports
  `200 {"ok": true}` after ~2m45s, but `taiga-gateway` crashes seconds
  later: `nginx: [emerg] host not found in upstream "taiga-front"`.
  `/status` eventually shows `taiga: false` correctly, but the toggle
  response itself lies again, via a new mechanism (the fallback's own
  code comment says "no settle-window recheck on this one extra attempt —
  keep it simple", so it can catch the container in a brief pre-crash
  "running" window).

  **Root cause now pinned down** (round 6 left this open): `taiga-gateway`
  own bundled nginx does `proxy_pass http://taiga-front/;` — a bare
  hostname resolved once at config-load/startup, not lazily. If Docker's
  embedded DNS (127.0.0.11) hasn't registered `taiga-front` yet when nginx
  starts, config load fails and nginx exits(1) immediately, no internal
  retry — this is a narrow, deterministic startup-time DNS race, not
  something blind retrying can fully close (only narrow). Matches why a
  manual `docker compose up -d taiga-gateway` run well after the automated
  attempts have been retrying for 2+ minutes reliably succeeds (the race
  window has long closed by then).

  Standard nginx fix suggested: lazy DNS resolution instead of
  startup-time — add `resolver 127.0.0.11 valid=10s;` and reference the
  upstream via a variable (`set $upstream_front taiga-front; proxy_pass
  http://$upstream_front/;`) instead of a literal hostname. This would
  need to land in `taiga-gateway`'s nginx config, which lives inside the
  pinned third-party `taigaio/taiga-docker` checkout at `$TAIGA_DIR` —
  **this directly conflicts with the explicit item-30 architecture
  decision** (install.sh ~line 452-455) to *not* patch
  taiga.conf/docker-compose.yml inside that pinned checkout, and instead
  only health-gate via the repo-owned `docker-compose.override.yml`. That
  decision predates this round's finding that the health-gate approach
  doesn't actually close the race — worth revisiting, possibly via a
  volume-mounted custom `taiga.conf` from *our* override file (same
  spirit as the existing override — never editing the pinned checkout's
  own files directly) rather than reversing the decision outright.
  Cheap stopgap in the meantime, called out by the tester: have the
  item-43 fallback reuse the same settle-and-recheck the main retry loop
  already has, so it stops reporting `{"ok": true}` for a container about
  to crash — doesn't close the race, just stops the toggle lying about it.

Everything else (upload-from-folder, GitHub-origin AI-reviewer, real
engine CLI sessions) — not re-tested this round, same as round 6.

### Round 8 fix cycle: complete (2026-08-16)

Item 43's real fix (nginx lazy DNS resolution via a repo-owned,
bind-mounted `taiga.conf` — never touching the pinned `taiga-docker`
checkout, preserving item 30's original constraint) plus a
settle-and-recheck stopgap for the round-7 fallback, via the normal
product-manager → developer → reviewer pipeline (no ux-designer —
backend/infra only). Approved with one non-blocking nit, committed as
`edb4619` on `backlog/e2e-fixes-round6`, pushed to both remotes, still
under PR #33. Reviewer independently live-reproduced the original DNS
crash and confirmed the fix conf stays up under the same condition,
using real Docker containers rather than trusting the implementation
report. Re-briefed `pve-sparkling-meadow`, item-43-focused this round
(asked for several `/taiga/on` runs across fresh installs, since the
original race was timing-dependent).

### Round 8 retest report (2026-08-16): DNS race genuinely fixed (0/4 crashes), but a new item — 44 — found: port publishing silently fails

Tested `4cb4946` (includes `edb4619`) on a fresh CT110, `install.sh --yes
--with-taiga`. Ran 4 separate `POST /taiga/on` trials (2 toggle-cycles on
an already-up stack, 1 from a completely fresh state) plus a live
runtime-resilience check (stopped `taiga-front` out from under an
already-running gateway — it stayed up, confirming resolution really is
lazy/runtime, not just a race window that happened to close in time).

**Item 43 confirmed genuinely fixed**: 0/4 trials hit the old `nginx:
[emerg] host not found in upstream "taiga-front"` crash. Confirmed the new
`resolver`/variable-indirection config is live in the running container.
No retry-loop messages fired at all in any trial — clean first-attempt
success every time, ~2m50s being genuine Taiga stack startup time, not
retry overhead.

### 44. `taiga-gateway`'s host port never actually gets published — Taiga is 100% unreachable in every trial despite `{"ok": true}` and `/status` reporting `taiga: true`

Repro (deterministic, hit 4/4, not timing-dependent): after any `POST
/taiga/on` that reports success, `curl http://127.0.0.1:9000/` →
`curl: (7) Failed to connect`, and `docker port
...taiga-gateway-1` prints nothing. Manually re-running the exact `up -d`
surfaces the real error, never seen or checked by `taiga-up.sh`/app.py:
`Error response from daemon: failed to set up container networking: ...
failed to bind host port 127.0.0.1:9000/tcp: address already in use`.

Root cause: the item-30 `docker-compose.override.yml` (see install.sh's
own comment at ~line 436, present since round 6) adds `ports: -
"127.0.0.1:${TAIGA_PORT}:80"` for `taiga-gateway` specifically *because*
the pinned upstream `docker-compose.yml` already publishes an
unrestricted `ports: - "9000:80"` (`0.0.0.0`) for that same service, which
conflicts with this project's "everything binds 127.0.0.1 only" rule.
Unlike `volumes:` (which Compose merges by target path across `-f`
files — confirmed and relied on by round 8's own taiga.conf fix), `ports:`
is a plain list field that Compose **concatenates**, not de-dupes/replaces
across files. So the merged config ends up with two competing bindings
for host port 9000; Docker fails the second bind but doesn't crash the
container over it, so `taiga-gateway` ends up **running with no port
published at all**, silently. `taiga-up.sh`'s own success check (`docker
compose ps` state == `running`) is blind to this.

This bug has existed since round 6 (item 30) but was never caught before
because `taiga-gateway` was crash-looping from the DNS race (item 43)
every time, so nobody got far enough to notice the port never published
either — round 8 fixing item 43 is what made this visible for the first
time. Arguably worse than the pre-round-8 state: previously the failure
was obvious (crash-loop); now everything reports healthy while being
completely unreachable.

Verified (not just theorized): removing the override's conflicting
`ports:` block and recreating just `taiga-gateway` immediately fixes
reachability (`docker port` shows `80/tcp -> 0.0.0.0:9000`, `curl` → `200`)
— but that falls back to the base file's original *unrestricted*
`0.0.0.0:9000` binding, reopening the wider exposure the override's
`ports:` line was clearly added to close in the first place. Compose has
no "replace" semantics for list fields like `ports` across override
files (unlike `volumes`, which does support target-path override) — so
actually tightening the binding to loopback-only needs a different
mechanism than the override-file idiom that worked for item 43's
`taiga.conf` fix. Two candidate directions, neither applied yet: (a)
scripted `sed`-patching of the pinned checkout's own `docker-compose.yml`
`ports:` line at install time (mirrors "generate config via script, never
hand-edit the shipped file" — but note this would touch the pinned
checkout's `docker-compose.yml` directly, which item 30's original
architecture decision explicitly ruled out doing for *both*
`taiga.conf`/`docker-compose.yml`, since the override-file idiom was
believed sufficient for everything, before this round's finding that it
mechanically can't work for `ports:` specifically); or (b) drop the
override's `ports:` line and accept the base file's `0.0.0.0:9000`
exposure (matches whatever posture other services on this host already
have — worth checking whether any of them already deal with this same
class of "third-party image publishes to 0.0.0.0" problem). Whether
loopback-only binding for Taiga is a hard requirement or a nice-to-have is
worth confirming before picking a direction, since (a) revisits the
item-30 architecture decision a second time in as many rounds and (b) is
a real (if narrow) security posture regression.

### Round 9 fix (2026-08-16): direction (a) — narrow, idempotent `sed`-patch of the pinned checkout's own `ports:` line

Implemented per `docs/spec.md`/`docs/implementation.md`: `install.sh` now
grep-gates-then-`sed`-patches `$TAIGA_DIR/docker-compose.yml`'s
`taiga-gateway` `ports: - "9000:80"` line to
`"127.0.0.1:${TAIGA_PORT}:80"` directly, unconditionally on every run (so
a pre-fix install gets patched on its next re-run too), and warns loudly
instead of silently no-op-ing if the expected line isn't found. The
now-redundant, actively-conflicting `ports:` entry is dropped from
`docker-compose.override.yml`, which keeps only `volumes:`/`depends_on:`
for `taiga-gateway` (item 43's mechanism, untouched). Verified against the
real upstream `taigaio/taiga-docker` `stable` branch `docker-compose.yml`
content and via `docker compose config` that the merged result has
exactly one `ports:` entry, bound to `127.0.0.1:9000` — see
`docs/implementation.md` for the full verification method and its limits
(no live `$TAIGA_DIR` install available in this sandbox; still needs a
hands-on pve-peer retest to confirm against a real install, same caveat
pattern round 8 used). Not yet retest-confirmed — do not mark item 44
"confirmed fixed" until a retest report comes back clean.

**Round 9 fix cycle: complete.** Reviewed (independently re-derived the
Compose merge-semantics claim from real `docker compose config` runs
rather than trusting the spec, confirmed idempotency, confirmed item 43's
mechanism untouched), approved with one non-blocking nit, committed as
`1de9710` on `backlog/e2e-fixes-round6`, pushed to both remotes, still
under PR #33. Re-briefed `pve-sparkling-meadow`, item-44-focused (real
reachability check, item-43 non-regression check, and an idempotency
check via re-running `install.sh` on the same box rather than only a
fresh one).

### Round 9 retest report (2026-08-16): clean — all three asks confirmed, no new issues

Tested `1de9710` on a fresh CT110. All three asks came back clean:

1. **Port binding fixed, real reachability confirmed**: `docker port` shows
   `80/tcp -> 127.0.0.1:9000` (genuinely loopback-only, not falling back to
   `0.0.0.0`), `curl` → real `200`. Sed patch landed cleanly on the real
   upstream file with no warning fired (pattern matched first try); the
   dead `ports:` block is gone from the override.
2. **Item 43 stays fixed**: 4 `/taiga/on` events total (fresh install + 3
   toggle cycles), zero DNS crashes. Bonus signal: each toggle now
   completes in ~13s instead of the ~2m50s every prior round took — no
   retry-loop/fallback firing at all anymore, independent corroboration
   the underlying race is actually closed, not just no-longer-crashing.
3. **Re-run idempotency confirmed against a real re-run** (not just a
   fresh install): ran `install.sh --yes --with-taiga` a second time on
   the already-patched box — clean exit, `docker-compose.yml` still has
   exactly one `ports:` entry, not doubled/corrupted, gateway stayed
   reachable throughout.

No new issues found this round. **All of items 39-44 are now confirmed
fixed** (39-42 in round 7, 43 in round 8, 44 in round 9) — three rounds
in a row of targeted retests, zero regressions.

**Not yet a basis for "fully clean" under step 3 of "The plan, in
order"**, though: rounds 7-9 were all targeted retests scoped to specific
items, not a full hands-on pass across the whole feature surface the way
round 6 was. Round 6's own "Explicitly skipped / not confirmed" list is
still open: no real browser/UI pass, upload-from-folder project creation,
GitHub-origin AI-reviewer path, and a genuine `AUTH_MODE=pve` login
round-trip with real credentials (round 7 verified this via code-path
inspection only, explicitly flagged as lower-confidence). Given this
loop's own track record — every round so far has surfaced at least one
real issue, including one (item 44) that was completely invisible until
a *different* bug got fixed out from under it — proceeding straight to
backup+migration off three narrowly-scoped clean reports would be
premature. Requested one more full-scope pass (round 10, matching round
6's original breadth) before treating the loop as done.

### Round 10 report (2026-08-16): genuine full-scope pass, clean except one honestly-flagged, non-confirmed observation

Tested `4128fc1` on a fresh CT110, `AUTH_MODE=pve` pre-seeded again, all
six flags. Not another targeted retest — every prior round's clean path
re-exercised on top of items 39-44's fixes, plus the three gaps every
prior round had left open:

- **`AUTH_MODE=pve` real login**: still couldn't complete one — re-tried
  creating a throwaway PVE-realm test account specifically to
  re-confirm rather than assume, still blocked by the tester's own
  session permission classifier, same as round 7. Fell back to the same
  code-path verification (persists correctly, `pve_login()` genuinely
  wired, bogus-credential 401s through the real PVE ticket API).
- **Upload-from-folder**: exercised for the first time this loop, full
  two-phase protocol, worked cleanly end-to-end.
- **GitHub-origin AI-reviewer path**: still skipped, no PAT available —
  confirmed the constraint rather than silently omitting it.
- **Real browser pass**: still not available, checked explicitly this
  round rather than assumed.
- Items 22-27, 39-44 all re-confirmed clean, zero regressions. "+New
  project"→Gitea, clone-from-URL, deploy-target, team `stop` cleanup —
  all clean.

### 45. Team run that fails to delegate to any available engine reports `status: "finished"` with no error — indistinguishable from a real success in `/status`

Not confirmed as a code regression — flagged plainly by the tester as
possibly local-8B-model (`qwen3:8b`) tool-choice variance rather than an
app.py change, since round 6 hit the identical dead end (aider then
claude both unavailable) and got a real `ask_user` escalation instead.
This round, the lead instead emitted a `tool_use` reporting "Failed to
delegate task to unavailable agents... Please check agent availability or
use manual intervention", and the run ended `status: "finished"`,
`terminal: true`, `error: None` — no LICENSE file created (the task was
"add a LICENSE file"). `/status` shows this identically to a genuinely
successful finish; nothing distinguishes it except manually opening
`run.json` to read the `summary` field, which plainly describes the
failure.

Real gap regardless of root cause: nothing in `/status`'s team block lets
a user watching the dashboard tell "actually done" apart from "gave up
and said so in a summary nobody's shown." Two directions suggested, not
scoped/decided yet: (a) a distinct `give_up`/`error` tool for the lead,
separate from a success-reporting `finish`; (b) surface `summary` in
`/status`'s team block for every terminal status, not just
escalated/error ones. **Not treated as blocking** — single occurrence,
plausibly model-dependent, distinct in kind from items 39-44 (which were
all deterministic, reproducible, root-caused code bugs in the install/
service-toggle path). Worth a future round, not a chase-it-now regression.

**Verdict**: genuinely clean full-scope pass. The orchestrator is pausing
here to confirm with the user before proceeding into "The plan, in
order" steps 3-5 (backup of all repos, new container, full environment
transfer, this session's own shutdown) — that phase is high-stakes and
hard to reverse, warranting explicit sign-off rather than autonomous
continuation even though a clean report was the originally-stated
trigger for it.

### To resume if this session is interrupted mid-loop

1. Check `ListAgents` for the current pve-side peer session name (may
   have changed if it died again).
2. Round 10 came back clean (see report above) — item 45 is logged but
   not blocking. If the user hasn't yet confirmed proceeding to
   backup+migration: ask them before doing anything under "The plan, in
   order" steps 3-5 (creating a new container, transferring the
   environment, and this session being shut down afterward are all
   hard-to-reverse/high-blast-radius — don't infer approval from the
   clean report alone).
3. Once the user confirms: proceed to backup+migration steps 3-5 above.
4. If the user instead wants item 45 investigated first, or wants another
   E2E round for any reason: treat it like any other found issue — loop
   back into the fix pipeline (product-manager → developer → reviewer) if
   it's to be fixed, or just re-brief the peer for another round if it's
   to be re-tested first.

### Item 45: investigated and fixed (2026-08-16)

User chose to investigate item 45 before deciding on backup+migration.
product-manager read the actual code (`app/teams.py`, `app/app.py`)
rather than theorizing from the E2E report, and confirmed this is a
real, root-cause-independent gap: the lead's `finish` tool has no
success/failure distinction, `team_step()` unconditionally sets
`status: "finished"` when called, and `summary` was already being
captured on every `finish` call but never surfaced anywhere in `/status`
or the frontend — the app's own status model conflates "completed" and
"gave up and said so" regardless of which round's specific tool choice
triggered it. Rejected inventing a new `give_up`/error tool (same
unreliable-model-judgment-call problem as choosing `finish` vs
`ask_user` today, and would still need this same surfacing fix on top);
fixed by surfacing `summary` universally in `/status`'s team block and
adding a small frontend display (reusing the existing `escalatedNote`/
`.team-sub` pattern) — explicitly scoped as additive display only, not
a failure classifier, and explicitly not extended to `error` status
(non-goal, flagged as a possible cheap follow-up later). Full
product-manager → developer → reviewer pipeline (no ux-designer — reused
an existing frontend pattern 1:1). Reviewer confirmed the escaping is
safe against injection via a real `<script>`-content test (the field now
renders model-generated text into the DOM). Approved, no findings.
Committed as `391865c` on `backlog/e2e-fixes-round6`, pushed to both
remotes, still under PR #33.

User then chose one more live E2E round (round 11) before migration,
consistent with how every other fix in this loop got a hands-on
confirmation. Re-briefed `pve-sparkling-meadow`: reproduce the same
no-engine-CLI dead end that triggered item 45, confirm `/status`/
dashboard now surface `summary` when the lead calls `finish` with one, a
light general sanity pass on top (not a full 39-44 re-walk, given three
straight clean rounds already). If clean, proceed straight to
backup+migration (steps 3-5 under "The plan, in order") without asking
again — the user has already indicated that's the trigger.

### To resume if this session is interrupted mid-loop (round 11)

1. Check `ListAgents` for the current pve-side peer session name (may
   have changed if it died again).
2. If no round-11 report has arrived yet: wait, or re-send the round-11
   brief (see note above) to whichever peer is listed.
3. If a report *has* arrived: read it. Clean → proceed directly to
   backup+migration steps 3-5 under "The plan, in order" (already
   user-approved, no need to ask again). Issues found → loop back into
   the fix pipeline, commit, push, re-brief, repeat.

---

## Items 39-43: found by E2E round 6 real test on fresh CT110 (2026-08-16)

Found by `pve-sparkling-meadow` (Remote Control peer session on the pve
host) testing branch `backlog/e2e-fixes-round6` @ `140a2ae` (PR #33) on a
genuinely fresh Proxmox LXC (CT110, Debian 12, 4 vCPU/4GB/32GB), installed
via `install.sh --yes` with all six optional feature flags
(`--with-git-hosting --with-code-server --with-host-control
--with-deploy-target --with-taiga --with-ollama`), linked against a real
LAN Ollama endpoint. Items 22-27 from the previous round were reconfirmed
still fixed. Full report kept verbatim below; CT110 was destroyed after
the report was sent, no orphaned containers left on the host.

### 39. `install.sh --yes` ignores a pre-seeded `AUTH_MODE` — always installs in `simple` auth, never `pve`

`ct/create.sh`'s automated path writes `AUTH_MODE=pve` + `PVE_HOST` into
`switchboard.env` before invoking `install.sh --yes`, expecting it
honored (the printed summary even promises "Web UI login: your existing
Proxmox VE credentials"). It never is. Repro: pre-seed `switchboard.env`
with `AUTH_MODE=pve` + `PVE_HOST=192.168.178.100`, run `install.sh --yes
...`. Result: `AUTH_MODE=simple`, auto-generated password — pre-seeded
value silently discarded.

Root cause: `RUN_USER`/`SVC_USER` correctly default from `get_env
"$ENV_FILE" ...` (survives `--yes`), but `AUTH_MODE`'s default is the
literal string `"simple"`:
```bash
AUTH_MODE=$(prompt "Auth mode: simple (username+password) or pve (Proxmox VE login)" "simple")
```
(install.sh ~line 327). Under `--yes`, `prompt()` always returns that
hardcoded default, never consulting `switchboard.env`.

Shape of the fix: default to `$(get_env "$ENV_FILE" AUTH_MODE)`, falling
back to `"simple"` only when empty — same pattern `RUN_USER_DEFAULT`
already uses two lines above it.

### 40. Gitea admin bootstrap: any admin account that isn't literally Gitea's first-ever user gets `must_change_password=true`, blocking all API access with an unhelpful 403

`install.sh`'s printed step: `docker exec --user git
ai-dev-switchboard-gitea gitea admin user create --admin --username
<name> --password <password> --email <email>`, followed by
`scripts/gitea-configure-api.sh`. Repro: run the printed command exactly
as documented, but not as Gitea's first-ever user (a prior throwaway
account, a retry after a typo). `gitea-configure-api.sh` fails
verification:
```
Verifying the token actually works (GET /user)...
Verification failed -- Gitea didn't accept the new token. Output was:
curl: (22) The requested URL returned error: 403
```
Manually hitting the API with the token shows the real reason, never
surfaced by the script: `{"message":"You must change your password.
Change it at: .../user/change_password"}`.

Root cause: Gitea's CLI defaults `--must-change-password` to `true` for
every user "except the first one" (`gitea admin user create --help`) —
and that account is not guaranteed to be Gitea's first user in practice.
Confirmed live: clearing the flag via `gitea admin user change-password
--must-change-password=false` immediately fixed it, same token, same
script, no other change.

Shape of the fix: add `--must-change-password=false` to the command
install.sh prints. Consider having `gitea-configure-api.sh`'s
verification special-case this exact 403 with a pointer to the real fix.

### 41. `--with-code-server` is completely non-functional out of the box — wrong hardcoded binary path, fails silently on every toggle

Repro: fresh install with `--with-code-server`, then toggle "Code" on for
any project. Response is `{"ok": true}`, but no process ever starts,
`code_on` stays `false`.

Root cause: code-server.dev's installer (what install.sh itself runs)
installs a real `.deb` on Debian 12, landing the binary at
`/usr/bin/code-server` (confirmed: `dpkg -l` shows `code-server 4.132.0`,
binary present/executable there, symlinked from `/bin/code-server`). But
both install.sh's idempotency check (`[ ! -x /usr/local/bin/code-server
]`) and app.py's hardcoded default (`CODE_SERVER_BIN =
os.environ.get("CODE_SERVER_BIN", "/usr/local/bin/code-server")`) look
in `/usr/local/bin/`, which never exists. Confirmed directly: `sudo -u
dev /usr/local/bin/code-server ...` → `sudo: /usr/local/bin/code-server:
command not found`. `_code_start()` launches it via
`subprocess.Popen(..., stdout=DEVNULL, stderr=DEVNULL)` with nothing
surfaced, so the toggle silently no-ops every time — and install.sh
re-downloads/reinstalls code-server on every single run since its own
existence-check is also looking in the wrong place.

Shape of the fix: point both checks at the real install location. Safest
fix is resolving via `command -v code-server` at runtime in both places
rather than hardcoding a path that already changed once upstream.

### 42. `POST /host/on`, `/host/off` (and `/taiga/on` when its retry budget is exhausted) report `{"ok": true}` unconditionally, regardless of whether the underlying action actually succeeded

`host_run()` (app.py ~line 2677) runs `subprocess.run([...ssh...],
capture_output=True, ...)` and returns `r.stdout.strip()` — never checks
`r.returncode`, never surfaces `r.stderr`. The POST handler ignores the
return value: `host_run("start" if ... else "stop"); self._json({"ok":
True})`. Repro: with no `HOST_CONTROL_KEY`/target configured (the normal
state right after `--with-host-control`, which only provisions the
receiving end), `POST /host/on` returns `200 {"ok": true}` instantly, no
ssh attempt visible in the journal. `GET /status` (which calls
`host_run("status")` fresh every poll) correctly reports `host: false` a
moment later — the toggle response itself is the only thing lying.

Same pattern hits Taiga harder: `POST /taiga/on` blocked for 2m44s
(taiga-up.sh's full 5-attempt/150s-backoff budget), the script exited 1
with `"taiga-gateway failed to come up after 5 attempts"` on stderr — HTTP
response was still `{"ok": true}`. See item 43 for the underlying race
this exposed live.

Shape of the fix: check `returncode`/exit status in both `host_run`'s and
`taiga_run`/`gitea_run`'s callers; return a real error + captured stderr
instead of an unconditional `{"ok": true}` on failure.

### 43. Round 6's taiga-gateway health-gate retry fix (item 30) is still flaky — reproduced live, all 5 attempts exhausted, while a plain manual `docker compose up -d taiga-gateway` immediately afterward succeeded in 3 seconds with no error at all

Repro: fresh `--with-taiga` install, `POST /taiga/on`. All 8 other
containers (db, back, async(+rabbitmq), events(+rabbitmq), front,
protected) came up healthy; `taiga-gateway` — "the stack's only public
entrypoint" per taiga-up.sh's own comment — never got past Docker's
`Created` state through all 5 retries (`up -d` + `rm -f taiga-gateway` +
backoff 10/20/40/80s). Script exits 1 to stderr as designed, but per item
42 that never reaches the caller. Manually running the exact same command
the script already retries (`docker compose up -d taiga-gateway`, no
flags, no `rm -f` first) right afterward succeeded in ~3s, completely
clean nginx logs.

Whatever the real race is (item 30's own comment says "root cause wasn't
pinned down"), round 6's retry strategy isn't actually closing it — a bare
extra `up -d` after the loop gives up would likely have succeeded, going
by this one live repro. Treating item 30 as still open; worth trying "one
more plain `up -d`, no `rm -f`" as the very last step before declaring
failure.

### What worked cleanly (round 6 re-verification)

- Login (simple auth) + session cookie + once-per-session TOTP gate
  (`428` → `403` on wrong code → `200` on correct code) all exactly as
  documented.
- "+ New project" → real Gitea repo → local clone → `gitea_sync: synced`
  worked end-to-end once item 40 was worked around.
- Clone-from-URL worked cleanly against a real public GitHub repo
  (octocat/Hello-World).
- `--with-ollama` against a real LAN Ollama endpoint linked correctly;
  roster/composition reflected it as a real tier-1 lead candidate.
- Deploy-target provisioning matched its documented end state exactly.
- The multi-agent team lifecycle genuinely works end-to-end, driven by a
  real linked Ollama lead: `team/start` → real `qwen3:8b` lead delegated
  to `aider` (recorded failure — CLI not installed in this throwaway
  sandbox, expected) → delegated to `claude` (same) → correctly issued a
  real `ask_user` tool call → `/status` correctly showed `status:
  "blocked"`, `waiting_on_you: true`, `escalation_kind: "ask_user"`,
  right run_id/project → `team/resolve` accepted an answer →
  `team/stop` cleanly tore down the tmux session and all three worktrees.
  (Note: `/status`'s `instances` array is alphabetically sorted, not
  most-recent-first — a red herring during testing, not a bug.)
- Items 22-27 all reconfirmed still fixed on this fresh install.

### Explicitly skipped / not confirmed this round

- No real Claude Code / aider / Codex CLI session exercised (none of the
  three engine CLIs are installed by install.sh itself, left to the
  operator) — the Ollama-lead team path was exercised instead.
- No real browser available in this headless peer session — everything
  above drove the same JSON API the frontend calls, not the rendered
  HTML/JS/CSS. Visual/layout correctness not assessed this round.
- Upload-from-folder project creation not exercised (time-boxed).
- GitHub-origin AI-reviewer path not tested (needs a real GitHub PAT +
  target repo).
- `AUTH_MODE=pve` itself never actually exercised end-to-end, precisely
  because of item 39 — tested via the auto-generated simple-mode
  credentials instead.
