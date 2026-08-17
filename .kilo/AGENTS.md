<!-- GENERATED from agents/_conventions.md by scripts/sync-agent-conventions.sh.
     Do not edit this copy: the next sync overwrites it. Edit the source. -->

# Shared conventions

**The canonical instructions for every agent, whichever engine runs it.**

This file is the single source. It is injected into every pipeline dispatch,
and synced out to each tool's own expected location by
`scripts/sync-agent-conventions.sh` — so Claude Code, Kilo, Codex, aider and a
local model all work from the same rules. Edit it here; never edit a copy.

---

## 1. Who you are working with

**Leo** — building and running things across a homelab and several projects.

- **Show the reasoning and the rejected alternatives, not just the
  conclusion.** Name the trade-off explicitly. The goal is judgement, not a
  pile of features.
- *"Are you saying X?"* is a request for **precision**, not agreement. Split
  bundled questions into distinct answers.
- When a factual claim is challenged, **verify against the live system** — the
  database, the logs, the running endpoint — rather than re-arguing from code
  reading.
- A correction is worth more than agreement. If something looks wrong, say so
  before building on it.

---

## 2. Establish the stack before you touch it

**Nothing here assumes a language, framework, or company.** An agent that
arrives with borrowed assumptions writes code that does not belong. Find out
what this repository actually is, in this order, stopping as soon as you have
what you need:

1. **`graphify query "<question>"`** when `graphify-out/graph.json` exists — a
   scoped subgraph at a fraction of the cost of reading files. Also
   `graphify path "<A>" "<B>"` and `graphify explain "<concept>"`. Run
   `graphify update .` after changing code.
2. **The repository's own convention documents** — `CLAUDE.md`, `AGENTS.md`,
   `.agents/skills/`, `README`, `docs/`. **If the project states a convention,
   it wins over your preference, always.**
3. **Two or three neighbouring files of the same kind** as the thing you are
   about to write. Match their structure, naming, and error handling.

If you cannot establish the conventions, **say so and ask**. Do not guess a
stack, and do not import a pattern from a different project.

---

## 3. How work gets done

**Reasoning, then approval, then code.** Deliver the analysis with verifiable
evidence — `file:line`, commits, live queries — before implementing. If
something gets rolled back, re-confirm scope before trying again.

**Root cause, never the symptom.** Trace the chain from origin to symptom and
fix at the origin, presenting it as `file:line`. A raw error is usually a
symptom of wrong control flow upstream: before guarding the throwing line, ask
*why is this path executing at all?* A guard that silences an error while
leaving the wrong branch reachable gets reverted.

**Use what is already there.** Before writing any helper, hook, component or
service method, grep for the existing one. Reuse audit and convention audit are
different passes — a convention grep will happily pass a module that hand-rolls
what the libraries already provide.

**Prefer zero new surface.** Reuse existing shapes, columns, enum values and
error paths over new fields and flags. Mirror the neighbouring feature exactly.

**Minimal diff.** Change only what the task requires. No drive-by refactors, no
unrelated cleanup, no speculative abstraction. **Never rename or re-scope
pre-existing code as part of a feature** — call it, wrap it, or file it
separately.

---

## 4. Handing off

**Name exact paths.** When your output feeds another agent, give exact file
paths, with line numbers or symbol names where they help — never "the login
component". The next stage is a fresh agent with no context, and a vague
handoff makes it repeat the search you already did.

**Decide, then hand off a finished thing.** When something needs a human, do
the analysis first and present a finished, ready-to-execute result for
confirmation — not an open question that makes the work restart once answered.
Reserve a genuinely open question for when no reasonable default exists.

**Report honestly.** Say what you actually ran and actually saw. Never describe
a test as passing without having run it in this session. If you could not
verify something, say which part and why — an explicit gap is useful, a
confident guess is not.

**Stay inside your role.** Do not fix what you were asked to review, redesign
what you were asked to build, or expand scope because you noticed something
adjacent. Note it for the next stage instead.

**Do not add to the tree what you cannot remove.** No scratch files, no probe
scripts, not even ones you intend to delete — your sandbox may not be able to
remove what it creates, and an untracked file blocks a clean commit. Inspect
runtime behaviour with something that touches nothing on disk.

**Do not retry an identically-denied tool call.** In a headless run there is
nobody to approve it, so a retry spends a turn to receive the same refusal.
Fall back, and report the gap.

---

## 5. Commits and branches

Commits are small and single-concern; formatting-only changes go separately.
The message explains *why*, not what the diff already shows.

**Never mention Claude, Anthropic, AI, Kilo or Codex in a commit message** — no
`Co-Authored-By` trailer, no "generated with" marker, no robot emoji.

```
feature/{ab}-{ticket}/{description}
hotfix/{ab}-{ticket}/{description}
release/{version}
```

`{ab}` is the two-character repo abbreviation — first letter of each of the
first two words (`ai-dev-switchboard` → `ad`), or the first two letters of a
single-word repo (`streakline` → `st`). `{ticket}` is the **Taiga** reference
number, digits only — Taiga is the source of truth, not Gitea or GitHub issue
numbers. `{description}` is the ticket's own title, lowercased and kebab-cased,
trimmed to the words that carry meaning (aim ≤5). `release/` takes the version
alone.

If a branch genuinely has no ticket behind it, **ask before inventing a
number** — a missing ticket usually means the work is not tracked yet, not that
the convention should bend.

---

## 6. Writing on Leo's behalf

When drafting a message he will paste to someone else, write **in his voice**:
lowercase starts, casual punctuation, short conversational sentences strung
with commas, plain words, no headings, no bullets, no bold. Technical accuracy
preserved, formal scaffolding stripped. Save structured markdown for when he is
asking *you* to teach *him*.

---

## 7. Habits that generalise

- **An HTTP client that auto-retries will silently duplicate a non-idempotent
  call.** Check the retry policy before blaming the backend for a duplicate.
- **A pre-built library does not rebuild because you edited its source.** Find
  the build step, or you are testing the last good bundle.
- **Types crossing a wire boundary get coerced.** A route parameter is a string
  until something makes it otherwise.
- **Migrations that have already run are not editable.** Add a new one.
- **Watch piped exit codes** — a piped build can report success while the
  compile failed.
- Ask before running long or destructive commands on a machine Leo is using.