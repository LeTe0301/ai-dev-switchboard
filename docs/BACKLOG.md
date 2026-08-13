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
