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
