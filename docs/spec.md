# Spec: Install wizard UI — part 1: optional-feature checklist (BACKLOG item 15, piece 5)

## Summary
Replace `ct/create.sh`'s two standalone `yesno` prompts (git-hosting,
code-server) with a single `whiptail --checklist` multi-select covering all
four switchboard-box-installable `install.sh` flags
(`--with-git-hosting`/`--with-code-server`/`--with-taiga`/`--with-ollama`),
plus the two follow-ups the backlog attaches to that checklist: a
resource-cost callout when taiga is checked, and a validated endpoint/model
prompt when ollama is checked.

## Routing note
Workflow: `workflows/feature.md`. This is a refinement of one existing
script (`ct/create.sh`), single file, single architectural layer (a
`whiptail` TUI plus the `INSTALL_FLAGS`/`switchboard.env` assembly it already
does) — no schema, API, or multi-screen UI layer involved, so this does not
need further splitting under the load-balanced-decomposition rule. It does,
however, route through **ux-designer** first even though it's a TUI, not a
web UI: ux-designer's normal `ui-ux-pro-max` visual-design tooling
(colors/tokens/fonts) does not apply here. What ux-designer should actually
produce for this item is dialog **flow, copy, and validation-message
wording** — checklist row text, taiga callout wording, the ollama
retry-vs-skip prompt's exact copy — refining what's drafted below, not
inventing new visual design.

## Goals
- Replace the two `yesno` prompts at `ct/create.sh:56-64`
  (`WITH_GIT_HOSTING`, `WITH_CODE_SERVER`) with one `whiptail --checklist`
  screen listing all four switchboard-box-installable flags: git-hosting,
  code-server, taiga, ollama.
- Preserve today's off-by-default posture — every checklist row starts
  unchecked (`OFF`), matching `WITH_GIT_HOSTING=0`/`WITH_CODE_SERVER=0`
  today. No item is force-selected.
- Each row's label is a one-line condensed version of that flag's existing
  `yesno`/`install.sh` header-comment description (see "Proposed approach").
- When **taiga** is checked, show a follow-up `msgbox` (single
  acknowledgment, not a blocking `yesno`) carrying the same resource-cost
  callout `install.sh`'s own end-of-run summary already gives at
  `install.sh:917-926` — 9 containers, several GB of RAM, real disk for
  Postgres/RabbitMQ volumes.
- When **ollama** is checked, walk a separate follow-up step (not a
  checklist row — the checklist can't capture two free-text fields) that:
  1. Prompts for an endpoint URL and model name, same wording/defaults as
     `install.sh:753-754`'s existing `prompt()` calls
     (`http://127.0.0.1:11434/v1`, `qwen3:8b`).
  2. Validates reachability using the **same logic** `install.sh`'s own
     `--with-ollama` block already uses at `install.sh:761-805` (normalize
     trailing slash, `curl -fsS --max-time 10 "$URL/models"`, parse via the
     same `python3` heredoc doing exact model-id comparison against the
     `data[].id` list — never a substring/grep match).
  3. On failure (unreachable, model absent, unparseable response), shows a
     `whiptail --yesno` offering "try a different URL/model" vs "continue
     without linking Ollama" — using the same wording
     `install.sh:772/816/818/822` already prints, adapted from `echo ... >&2`
     to a `whiptail --msgbox` shown just before the yes/no choice.
  4. On success, the validated URL/model are written directly into the
     `switchboard.env` `TMP_ENV` heredoc `ct/create.sh` already assembles
     (`ct/create.sh:105-118`), as `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL`.
- Selected+validated checklist items map to `INSTALL_FLAGS` exactly the way
  `WITH_GIT_HOSTING`/`WITH_CODE_SERVER` already do today
  (`ct/create.sh:124-125`) — one conditional `--with-*` append per item, no
  new dispatch mechanism.
- Real-time feedback for ollama at wizard time on the Proxmox host, instead
  of relying on `install.sh`'s own interactive prompt — which, called via
  `ct/create.sh:128` with `INSTALL_FLAGS="--yes"` always present
  (`ct/create.sh:123`), never actually prompts (`prompt()`'s `interactive()`
  guard is false under `--yes`) and would silently fall back to its
  hardcoded defaults instead of asking anything.

## Non-goals
- **Pieces 1-4 of BACKLOG item 15 are explicitly deferred to a later
  part**: the Default-vs-Advanced entry-menu fork (piece 1), live
  storage-pool enumeration via `pvesm status -content rootdir` (piece 2),
  live network-bridge enumeration (piece 3), and CTID/hostname validation
  before `pct create` (piece 4). None of these are touched by this spec.
  See "Deferred to a later part" below for what carries over.
- `--with-host-control` / `--with-deploy-target` — excluded per the
  backlog's own settled 2026-08-15 decision (`install.sh`'s own header
  comments: these install on a *separate* machine, never the switchboard
  box itself). They remain CLI-only flags on `install.sh`, untouched.
- App-defaults save/reuse file, IPv6/MTU/VLAN/SDN-vnet fields — out of
  scope per item 6's exclusions, unchanged.
- Any change to `install.sh` itself. Its existing `--with-ollama` block
  (`install.sh:745-826`) is read and its validation logic is mirrored in
  `ct/create.sh`, not modified, refactored, or shared via a sourced file —
  `ct/create.sh`'s own "no shared framework" header comment stands; a small
  amount of duplicated `curl`+`python3` logic between the two scripts is an
  accepted, deliberate tradeoff (see "Open questions" #2), not an oversight.
- Any change to `ct/create.sh`'s final whiptail `SUMMARY` msgbox
  (`ct/create.sh:130-146`). Ollama linkage success/failure feedback happens
  at the point of the follow-up step itself (see Goals); the summary box is
  not extended to repeat it, matching today's precedent where the summary
  also doesn't call out `WITH_GIT_HOSTING`/`WITH_CODE_SERVER` explicitly.

## Deferred to a later part
Pieces 1-4 (entry-menu fork, storage/bridge live enumeration, CTID/hostname
validation) are all still-live backlog work, just not in this cycle — the
checklist (piece 5) has no code dependency on any of them and is fully
usable against today's flat, always-ask `ct/create.sh` structure. When a
future product-manager pass picks up piece 4 specifically, one open
question in the backlog is already resolved here so it doesn't need
re-litigating: **hard-block, not warn-and-let-`pct create`-fail.** Reasoning
— this project's "real errors over guessed validation" precedent (item 3's
zip-slip check, item 10's `set_env()` fix) is about preferring checks that
are *definitive* over ones that are *heuristic guesses*, not about avoiding
pre-validation altogether. CTID uniqueness (checkable exactly via `pct
status "$CTID"`/`pvesh get /cluster/resources`) and RFC1123 hostname syntax
are exact rules `pct create` itself enforces, not guesses — checking them
before `pct create` and giving a clear whiptail error is strictly better
than surfacing the same rule as a raw `pct create` stack trace later, and
loops the operator back to re-enter the field rather than aborting the
whole run. This reasoning is recorded here so the future spec for piece 4
can build directly on it.

## Background / current state
`ct/create.sh` (147 lines) is a `whiptail`-based TUI, invoked as a
`bash -c "$(curl -fsSL .../ct/create.sh)"` one-liner on a Proxmox VE host.
It has no shared framework dependency by deliberate design (its own header
comment: "no shared framework, just pct and whiptail").

Today, two optional `install.sh` features are each asked as a standalone
`yesno` (`ct/create.sh:56-64`):
```bash
WITH_GIT_HOSTING=0
if yesno "Enable git-hosting on this container too?\n\n..."; then
    WITH_GIT_HOSTING=1
fi

WITH_CODE_SERVER=0
if yesno "Enable code-server (VS Code in the browser) per project?"; then
    WITH_CODE_SERVER=1
fi
```
These feed `INSTALL_FLAGS` (`ct/create.sh:123-128`):
```bash
INSTALL_FLAGS="--yes"
[ "$WITH_GIT_HOSTING" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-git-hosting"
[ "$WITH_CODE_SERVER" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-code-server"
# shellcheck disable=SC2086
pct exec "$CTID" -- bash /opt/ai-dev-switchboard-src/install.sh $INSTALL_FLAGS
```
`install.sh` documents four switchboard-box-installable optional flags in
its own header comment (`install.sh:15-43`):
`--with-git-hosting`, `--with-code-server`, `--with-taiga`, `--with-ollama`
(two more, `--with-host-control`/`--with-deploy-target`, are explicitly
documented as running on a *different* machine and are excluded from this
wizard per the backlog's settled decision).

`install.sh`'s own `--with-taiga` block prints a resource-cost callout at
the end of a successful run (`install.sh:917-926`):
> "Taiga: installed but left OFF... Runs 9 containers and can use several
> GB of RAM (and real disk, for Postgres/RabbitMQ data volumes) once turned
> on; toggling it back off frees that RAM again right away."

`install.sh`'s own `--with-ollama` block (`install.sh:745-826`) prompts for
an endpoint URL + model name via its `prompt()` helper (falls back to
existing `switchboard.env` values, then hardcoded defaults
`http://127.0.0.1:11434/v1` / `qwen3:8b`), normalizes a trailing slash,
`curl`s `$URL/models` with a 10s timeout, and parses the JSON response with
an inline `python3` heredoc doing an **exact** `id` match against the
`data[]` list (never a substring/grep match — a `qwen3:8` false-positive
against `qwen3:8b` is explicitly called out in the source as the reason).
On success it writes `TEAM_LLM_BASE_URL`/`TEAM_LLM_MODEL` via `set_env()`;
on any failure (unreachable, model absent, unparseable JSON) it prints a
specific `echo ... >&2` message and writes nothing — never aborts the
script (`--with-ollama`, like every other optional block, only skips its
own step on failure).

Critically, `ct/create.sh` always calls `install.sh` with `--yes`
(`ct/create.sh:123`), which sets `install.sh`'s `YES=1` and makes its
`prompt()` helper's `interactive()` guard false — so if `--with-ollama`
were simply appended to `INSTALL_FLAGS` today without any change in
`ct/create.sh` itself, `install.sh`'s own endpoint/model prompt would
**never actually ask anything**; it would silently validate its own
hardcoded default (`http://127.0.0.1:11434/v1`), which is almost certainly
unreachable from inside a freshly created container, and quietly write
nothing. This is exactly why the backlog calls for a **separate** step in
`ct/create.sh` itself, not just a flag append.

## Proposed approach

### 1. Checklist screen (replaces `ct/create.sh:56-64`)
```bash
FEATURES=$(whiptail --title "ai-dev-switchboard" --checklist \
    "Optional features to enable on this container (Space to toggle, Enter to confirm):" \
    18 78 4 \
    "git-hosting" "Private repos over SSH + \"+ New project\" button" OFF \
    "code-server" "VS Code in the browser, per project" OFF \
    "taiga"       "Self-hosted Taiga backlog/kanban tracker" OFF \
    "ollama"      "Link a remote Ollama for multi-agent team leads" OFF \
    3>&1 1>&2 2>&3)

WITH_GIT_HOSTING=0
WITH_CODE_SERVER=0
WITH_TAIGA=0
WITH_OLLAMA=0
for _item in $FEATURES; do
    _item="${_item%\"}"; _item="${_item#\"}"
    case "$_item" in
        git-hosting) WITH_GIT_HOSTING=1 ;;
        code-server) WITH_CODE_SERVER=1 ;;
        taiga)       WITH_TAIGA=1 ;;
        ollama)      WITH_OLLAMA=1 ;;
    esac
done
```
(Tag-quote stripping is the standard safe idiom for parsing `whiptail
--checklist` output when tags contain no internal spaces — no `eval`
needed.) Row labels are condensed one-liners of each flag's existing
`yesno`/header-comment description, per the backlog's own instruction.

### 2. Taiga follow-up (only if `WITH_TAIGA=1`)
```bash
if [ "$WITH_TAIGA" -eq 1 ]; then
    msg "Taiga runs 9 containers and can use several GB of RAM (and real disk, for Postgres/RabbitMQ data volumes) once turned on in the web UI; toggling it back off frees that RAM again right away."
fi
```
Reuses `install.sh:920-922`'s exact wording. `msg()` is already `whiptail
--msgbox` — single acknowledgment, not blocking.

### 3. Ollama follow-up (only if `WITH_OLLAMA=1`)
Needs `python3` on the Proxmox host (not guaranteed the way `curl` is,
since `curl` is required just to fetch `ct/create.sh` itself). Add, near
the existing `whiptail` preflight (`ct/create.sh:23`):
```bash
command -v python3 >/dev/null 2>&1 || apt-get install -y -qq python3
```
Then a retry loop, reusing `install.sh:761-805`'s exact `curl`+`python3`
logic verbatim (same heredoc script, same argv-based exact-match check —
copy, don't reinvent):
```bash
OLLAMA_BASE_URL_NORM=""
OLLAMA_MODEL_INPUT=""
if [ "$WITH_OLLAMA" -eq 1 ]; then
    _ollama_url_default="http://127.0.0.1:11434/v1"
    _ollama_model_default="qwen3:8b"
    while :; do
        _ollama_url_input=$(ask "Ollama endpoint URL (OpenAI-compatible, e.g. an existing remote Ollama's /v1)" "$_ollama_url_default")
        _ollama_model_input=$(ask "Model name" "$_ollama_model_default")
        _ollama_url_norm="${_ollama_url_input%/}"
        _ollama_models_json=$(curl -fsS --max-time 10 "$_ollama_url_norm/models" 2>/dev/null || true)
        if [ -z "$_ollama_models_json" ]; then
            _ollama_fail_msg="Could not reach $_ollama_url_norm/models (unreachable, no response, or an HTTP error)."
        else
            _ollama_check=$(printf '%s' "$_ollama_models_json" | python3 -c "$OLLAMA_MODEL_CHECK_SCRIPT" "$_ollama_model_input")
            case "$_ollama_check" in
                OK)
                    OLLAMA_BASE_URL_NORM="$_ollama_url_norm"
                    OLLAMA_MODEL_INPUT="$_ollama_model_input"
                    break
                    ;;
                MODEL_ABSENT:*)
                    _ollama_available="${_ollama_check#MODEL_ABSENT:}"
                    if [ -z "$_ollama_available" ]; then
                        _ollama_fail_msg="Reached $_ollama_url_norm but it has no models available."
                    else
                        _ollama_fail_msg="Reached $_ollama_url_norm but model '$_ollama_model_input' is not available there. Available: $_ollama_available"
                    fi
                    ;;
                *)
                    _ollama_fail_msg="Reached $_ollama_url_norm/models but its response could not be parsed as JSON."
                    ;;
            esac
        fi
        if yesno "$_ollama_fail_msg\n\nTry a different URL/model?"; then
            _ollama_url_default="$_ollama_url_input"
            _ollama_model_default="$_ollama_model_input"
            continue
        else
            WITH_OLLAMA=0
            msg "Continuing without linking Ollama. You can re-run 'install.sh --with-ollama' inside the container later once the endpoint is reachable."
            break
        fi
    done
fi
```
`OLLAMA_MODEL_CHECK_SCRIPT` is the exact same heredoc-built Python snippet
at `install.sh:786-802`, copied verbatim into `ct/create.sh` (defined once,
above this block, same as `install.sh` does it).

### 4. `switchboard.env` (`TMP_ENV` heredoc, `ct/create.sh:105-118`)
Append conditionally, only when validation succeeded:
```bash
if [ "$WITH_OLLAMA" -eq 1 ]; then
    {
        echo "TEAM_LLM_BASE_URL=${OLLAMA_BASE_URL_NORM}"
        echo "TEAM_LLM_MODEL=${OLLAMA_MODEL_INPUT}"
    } >> "$TMP_ENV"
fi
```
(placed after the existing heredoc's `cat > "$TMP_ENV" <<EOF ... EOF` block,
before `pct push`).

### 5. `INSTALL_FLAGS` (`ct/create.sh:124-125`)
```bash
[ "$WITH_GIT_HOSTING" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-git-hosting"
[ "$WITH_CODE_SERVER" -eq 1 ] && INSTALL_FLAGS="$INSTALL_FLAGS --with-code-server"
[ "$WITH_TAIGA" -eq 1 ]       && INSTALL_FLAGS="$INSTALL_FLAGS --with-taiga"
[ "$WITH_OLLAMA" -eq 1 ]      && INSTALL_FLAGS="$INSTALL_FLAGS --with-ollama"
```
`--with-ollama` is still appended (even though `TEAM_LLM_*` is already
written) so `install.sh`'s own container-side block still runs — it will
re-validate from the container's actual network path and either confirm
the same values or (rarely, e.g. a container-specific firewall rule) print
its own warning and leave the already-written values alone, since
`set_env()` upserts are idempotent and its own failure path only skips
writing, never un-writes an existing value. This matches `install.sh`'s own
documented "safe to re-run" contract.

## Affected areas
- `ct/create.sh` only — single file, single section (the optional-feature
  block, `ct/create.sh:56-64` and the `INSTALL_FLAGS`/`TMP_ENV` assembly
  around it). No schema, API, or additional UI screens involved.
- No changes to `install.sh`, `app/`, or any web UI code.

## Edge cases
- **Nothing checked**: `FEATURES` is empty, the `for` loop over an empty
  string performs zero iterations, all four `WITH_*` stay `0`,
  `INSTALL_FLAGS` stays `"--yes"` only — identical to today's both-declined
  behavior.
- **Only taiga checked**: resource-cost `msgbox` shown once; no ollama
  follow-up shown (guarded by `WITH_OLLAMA` staying `0`).
- **Ollama checked, blank URL/model submitted**: treated as any other
  unreachable/invalid input — `curl` to an empty/malformed URL fails, the
  retry-or-skip loop offers the same choice as a genuinely unreachable
  endpoint. No separate blank-input check needed.
- **Ollama checked, operator declines to retry after a failure**:
  `WITH_OLLAMA` is reset to `0`, `TEAM_LLM_*` lines are never appended to
  `TMP_ENV`, `--with-ollama` is never appended to `INSTALL_FLAGS` — falls
  back cleanly to "not enabled," matching the checklist's own
  default-unchecked posture.
- **Ollama checked, validation succeeds, but the container's own network
  path can't reach the same endpoint** (e.g. a bridge/firewall difference
  between host and container): `install.sh`'s own container-side
  `--with-ollama` re-check fails and prints its own warning to `pct exec`'s
  console output, but does not un-write the `TEAM_LLM_*` values `ct/create.sh`
  already wrote from the host-side check — an edge case inherent to
  double-validation from two different network vantage points, accepted
  as-is (see "Open questions" #2) rather than solved in this cycle.
- **Cancel pressed on any new dialog** (checklist, taiga msgbox, ollama
  `ask`/`yesno`): aborts the whole run, identical to every existing
  `ct/create.sh` dialog today (`set -euo pipefail` + unguarded command
  substitution) — no new behavior introduced, no special-casing added.
- **`python3` missing on the Proxmox host**: installed via the same
  `command -v ... || apt-get install -y -qq ...` idiom `whiptail` itself
  already uses at `ct/create.sh:23`, run unconditionally near it (not
  gated behind `WITH_OLLAMA`, so the install happens before the checklist
  screen even asks — avoids a mid-flow apt-get after the operator has
  already committed to checking ollama).

## Acceptance criteria
- [ ] Given the optional-features screen, when it first appears, then all
      four rows (git-hosting, code-server, taiga, ollama) are unchecked by
      default.
- [ ] Given no items checked, when the wizard proceeds, then
      `INSTALL_FLAGS` contains only `--yes` (no `--with-*` flags), matching
      today's both-declined behavior byte-for-byte.
- [ ] Given git-hosting and code-server checked (taiga/ollama left
      unchecked), when the wizard proceeds, then `INSTALL_FLAGS` contains
      exactly `--with-git-hosting --with-code-server` (order matching the
      existing append order) and no taiga/ollama follow-up screens appear.
- [ ] Given taiga checked, when the checklist is confirmed, then a single
      `msgbox` appears containing the "9 containers... several GB of
      RAM... Postgres/RabbitMQ" wording before the wizard continues to the
      next step.
- [ ] Given ollama checked and a reachable endpoint + present model
      entered, when validation runs, then `TEAM_LLM_BASE_URL`/
      `TEAM_LLM_MODEL` are written into the pushed `switchboard.env` with
      the exact (trailing-slash-normalized) URL and model name entered,
      and `INSTALL_FLAGS` includes `--with-ollama`.
- [ ] Given ollama checked and an unreachable endpoint entered, when
      validation runs, then a `whiptail` message names the specific
      failure reason (unreachable vs. model-absent vs. unparseable —
      matching `install.sh`'s own three distinct messages) and offers a
      retry-or-skip choice.
- [ ] Given ollama checked, validation failed, and the operator chooses
      not to retry, when the wizard proceeds, then `TEAM_LLM_*` are absent
      from the pushed `switchboard.env` and `INSTALL_FLAGS` does not
      include `--with-ollama`.
- [ ] Given ollama checked, validation failed, and the operator chooses to
      retry, when they re-enter a working endpoint/model, then the loop
      exits successfully with the newly entered values (not the original
      failed ones).
- [ ] Given the model-id comparison, when the endpoint advertises
      `qwen3:8b` and the operator enters `qwen3:8`, then validation reports
      model-absent (exact match only, no substring/prefix match) —
      mirrors `install.sh`'s own documented behavior and its explicit
      false-positive concern.

## Open questions
1. **Piece 4's hard-block-vs-warn question is resolved above** ("Deferred
   to a later part") for whichever future spec builds it — not a blocker
   for this cycle, recorded here so it isn't re-litigated later.
2. **Is host-side (Proxmox) ollama validation worth duplicating
   `install.sh`'s container-side logic, vs. just collecting the two values
   with no host-side check and letting `install.sh`'s own container-side
   block silently do the real check?** Proceeding under the assumption
   that real-time whiptail feedback at wizard time is worth the ~40 lines
   of mirrored logic, since `install.sh` under `--yes` would otherwise
   never surface a validation failure interactively at all — the operator
   would only see it in `pct exec`'s scrollback console text, easy to
   miss. This is a genuine design call (not a re-litigation of a settled
   backlog decision) — flagging for confirmation before/while building.
3. **No hard cap on ollama retry attempts** — the loop is operator-driven
   with no attempt limit; `Cancel` at any point still aborts the whole run
   per the existing whiptail-Cancel convention, so there's already an
   escape hatch. Proceeding without a cap; flag if a max-retry-count is
   actually wanted.

## Risk / rollback notes
Single-file change to a script that only runs interactively, at
container-creation time, on a Proxmox host — no running switchboard
container is affected by a bug here (worst case: a botched wizard run
fails before `pct create`/`pct start`, or writes an
`INSTALL_FLAGS`/`switchboard.env` combination that just doesn't enable a
feature the operator wanted, recoverable by re-running `install.sh
--with-<flag>` by hand inside the already-created container afterward,
exactly as `install.sh`'s own "safe to re-run" contract already promises).
Rollback is `git revert` on this one file; no data migration, no schema,
no other script depends on the new `WITH_TAIGA`/`WITH_OLLAMA` variable
names or the checklist's tag strings.
