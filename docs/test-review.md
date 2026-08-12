# Test & Review: PUBLISH_MODE as an interactive install-time prompt

## Scope
`install.sh`, `ct/create.sh`, `config/switchboard.env.example`, `README.md`
(the four functional/doc files in the diff), tested against all 8
acceptance criteria in `docs/spec.md`. No `app/app.py` changes in this
diff, per spec's own non-goals — confirmed by `git diff --stat`, which
shows only `README.md`, `config/switchboard.env.example`, `ct/create.sh`,
`install.sh`, plus `docs/BACKLOG.md`/`docs/implementation.md`/`docs/spec.md`
themselves.

All testing below was performed hands-on this session, against the actual
diff's own lines (extracted verbatim by line range and confirmed
byte-identical against the live files before use, not reimplemented):
real `read -rp </dev/tty` prompts driven through a real pty (`script`) for
`install.sh`'s new block, and — going beyond the developer's own documented
fallback — real keystroke-driven `whiptail` dialogs for `ct/create.sh`'s
new `menu()`/`ask()` block, driven through `tmux` (a pty with a real fixed
window size and a `send-keys`/`capture-pane` interface), which got past the
sandbox wall the developer hit with `script`/`pty.fork()`. This is useful
independent confirmation that the wall really was a `script`/`pty.fork()`
tooling limitation in this sandbox and not a code defect — `tmux` drove the
identical real `whiptail` binary against the identical real code
successfully.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| AC1 | `install.sh`, TTY attached, no `--yes`: publishing prompt asks with default `none`; typing `tailscale` triggers a `BASE_URL` follow-up | manual, real pty (`script`) driving the real extracted `interactive`/`prompt`/`set_env`/`get_env` functions + real install.sh:179-185 block | pass | case1/case2 runs below; case1 typed `tailscale`→`foo.ts.net`, follow-up prompt appeared; case2 pressed Enter on default, no follow-up prompt shown, `PUBLISH_MODE=none` |
| AC2 | `PUBLISH_MODE=tailscale` + `BASE_URL` entered → `switchboard.env` gets real `PUBLISH_MODE=tailscale`/`BASE_URL=<value>` lines via `set_env` upsert, no manual edit | manual, same pty run (case1) | pass | resulting env file: `PUBLISH_MODE=tailscale` / `LISTEN_PORT=8333` / `BASE_URL=foo.ts.net` — written entirely by the real `set_env`/`get_env` functions, no hand-editing |
| AC3 | `install.sh --yes`: does not block; `PUBLISH_MODE` ends up whatever was already in `switchboard.env` (or `none` on first install) | manual, real pty, `YES=1`, `</dev/null` stdin, `timeout 5` | pass | case3 (first install, seeded `PUBLISH_MODE=none`): completed instantly, unchanged; case5 (re-run, seeded `PUBLISH_MODE=tailscale`/`BASE_URL=foo.ts.net`): completed instantly, values preserved exactly — confirms `--yes` never blocks even when an existing `tailscale` value is present |
| AC4 | Re-run with existing `PUBLISH_MODE=tailscale`/`BASE_URL=foo.ts.net`: prompt defaults show those values; Enter on both preserves them | manual, real pty, typed `\n\n` (Enter, Enter) | pass | case4: prompts rendered `[tailscale]` and `[foo.ts.net]` as the bracketed defaults; resulting env file unchanged: `PUBLISH_MODE=tailscale`, `BASE_URL=foo.ts.net` |
| AC5 | `ct/create.sh` interactive: menu asks publish mode; choosing `tailscale` prompts for a tailnet hostname (blank allowed); pushed `switchboard.env` gets the chosen `PUBLISH_MODE`/`BASE_URL` instead of the old hardcoded `PUBLISH_MODE=none` | manual, **real** `whiptail` dialogs (not stubbed) driven via `tmux send-keys`/`capture-pane` against the real extracted `menu()`/`ask()` helpers + real ct/create.sh:66-73 block; heredoc/SUMMARY interpolation checked separately via real ct/create.sh:103-137 extracted verbatim | pass | Default-cursor case: Enter immediately → `PUBLISH_MODE=none`, no `ask()` dialog shown, `BASE_URL=` (empty). Down+Enter (select tailscale) → real inputbox appeared with the real prompt text; typed `foo.ts.net`+Enter → `PUBLISH_MODE=tailscale`/`BASE_URL=foo.ts.net`. Repeated with tailscale selected + **blank** Enter on the inputbox → `PUBLISH_MODE=tailscale`/`BASE_URL=` (blank allowed, no crash). Heredoc interpolation (separate harness, real ct/create.sh:103-118 lines): `PUBLISH_MODE=${PUBLISH_MODE}`/`BASE_URL=${BASE_URL}` lines render correctly for both cases, replacing the old hardcoded `PUBLISH_MODE=none` |
| AC6 | `PUBLISH_MODE=tailscale` in either script → final printed summary includes the manual `tailscale serve` reminder | manual | pass | `install.sh` "Done" block (case1/case4/case5 pty runs): `Publish mode: tailscale ... still run 'tailscale serve --bg https+insecure://127.0.0.1:8333' yourself ...` printed. `ct/create.sh` `SUMMARY` (real string extracted+interpolated): same reminder text present in both the `whiptail --msgbox` popup and the `echo -e "$SUMMARY"` terminal output that follows it (see Finding 1 below re: popup truncation of *unrelated* later content in this same string) |
| AC7 | `PUBLISH_MODE=none` (default) → final printed summary unchanged from today's, no new tailscale text | manual, diffed against pre-diff behavior | pass | `install.sh`: case3/case2 output has no `Publish mode:` line. `ct/create.sh`: extracted `SUMMARY` string with `PUBLISH_MODE=none`/`BASE_URL=""` is structurally identical to `git show HEAD:ct/create.sh`'s pre-diff `SUMMARY` (verified `PUBLISH_NOTE=""` contributes zero text) |
| AC8 | `README.md`'s tailscale bullet + `config/switchboard.env.example`'s `PUBLISH_MODE`/`BASE_URL` comments reflect install-time prompts, not required manual editing | manual, direct read | pass | `README.md:120-126` now reads "`install.sh`/`ct/create.sh` already prompt for `PUBLISH_MODE`/`BASE_URL` at setup time ... edit `switchboard.env` and restart only if you want to change that choice later." `config/switchboard.env.example:57-62` adds the equivalent note; the real `PUBLISH_MODE=none` default line and commented `#BASE_URL=` placeholder are untouched (comment-only edit, matching spec) |
| Edge | Syntax check | automated | pass | `bash -n install.sh && bash -n ct/create.sh` — both clean |
| Edge | Extraction fidelity (my harnesses test the *real* shipped lines, not a reimplementation) | automated (`diff`/`grep -F` against live files) | pass | every extracted block (`install.sh:72-93`, `:179-185`, `:274-279`; `ct/create.sh:25-29`, `:66-73`, `:103-118`, `:132-137`) confirmed byte-identical to the current working-tree files before use |

## Regression check
Full existing suite run: `python3 -m unittest discover -s tests` → **75/75
pass**, 0 failures/errors (unchanged from before this cycle, expected since
`app/app.py` was not touched by this diff). No CI/shellcheck configured in
this repo (confirmed: no `.github/workflows/`, no `shellcheck`/`bats`
binary available in this environment either) — consistent with the
developer's own note and the previous shell-script change in this repo's
history.

## Spec coverage
All 8 acceptance criteria implemented and independently hands-on tested
(table above) — no gaps. Both non-goals (`app.py` untouched,
`docs/ARCHITECTURE.md` untouched) and all four documented edge cases
(non-interactive paths never block; re-run preserves existing value; blank
`BASE_URL` allowed; unrecognized `PUBLISH_MODE` text accepted with no
re-prompt, matching `AUTH_MODE`'s existing precedent) were verified in the
process of testing the 8 ACs, not just read.

## Findings (most severe first)

### 1. `ct/create.sh`'s `whiptail --msgbox` truncates the TOTP secret and login instructions when `PUBLISH_MODE=tailscale` — should-fix
- File: `ct/create.sh:132-139` (`PUBLISH_NOTE` spliced into `SUMMARY`, then `whiptail --title "ai-dev-switchboard" --msgbox "$SUMMARY" 24 78`)
- Issue: the fixed `24 78` msgbox dimensions were sized for the pre-diff `SUMMARY` text, which fits with ~3 blank rows to spare (confirmed via `git show HEAD:ct/create.sh` — no `PUBLISH_NOTE` existed before this diff). The new `PUBLISH_NOTE` block (~5-6 wrapped lines) pushes the tailscale-case `SUMMARY` past the box's visible interior height. `whiptail --msgbox` does not scroll (confirmed by sending `Down`/`PageDown` keystrokes at the real dialog — no effect) — the overflow is simply clipped from view.
- Failure scenario: operator picks `tailscale` in `ct/create.sh`, sees the popup, reads down to "Publish mode: tailscale ..." (which *is* visible, so AC6 as literally worded is met), and dismisses the dialog — never seeing the TOTP secret or the "log in inside the container..." instructions that come after it in the same string, because those lines are the ones pushed off the bottom of the fixed-height box. The information isn't fully lost (the same `SUMMARY` is `echo -e`'d to the terminal right after, `ct/create.sh:140`, and is visible there in full) but the popup — the first and most prominent thing shown — silently drops it for exactly the new code path this feature adds.
- Confirmed via real, keystroke-driven `whiptail` rendering (not simulated): reproduced with the tailscale-case `SUMMARY` string at the real `24 78` dimensions inside a `tmux` pty; the `none`-case `SUMMARY` (pre-existing, unaffected by this diff) fits cleanly with room to spare in the same-size box.
- Not a blocker: no acceptance criterion covers whiptail-popup completeness specifically (AC6 says "final printed summary," which the terminal `echo -e` satisfies), and the underlying data is recoverable from terminal scrollback. Recommend a follow-up: bump the msgbox height (e.g. `24`→`30`) or trim/shorten `PUBLISH_NOTE`'s wording.

### 2. `docs/BACKLOG.md` item 5 ("Tailscale vs. LAN-only as an explicit setup choice") has no status-line update reflecting this cycle's build — nit
- File: `docs/BACKLOG.md:149-158`
- Issue: the only change in this diff's `docs/BACKLOG.md` hunk is item 3's (Folder upload — an already-committed, unrelated previous cycle being marked "shipped"). Item 5, which is what this cycle actually built, still reads exactly as it did as a backlog entry with no "Status: built/pending review" annotation, unlike the convention item 3 uses.
- Not a spec violation — `docs/spec.md`'s acceptance criteria don't mention `BACKLOG.md` — but worth a quick consistency follow-up so the backlog reflects this item is done.

## Follow-ups (non-blocking)
- Bump `ct/create.sh`'s summary `whiptail --msgbox` height (or shorten `PUBLISH_NOTE`) so the tailscale-case popup doesn't clip the TOTP secret/login instructions (Finding 1).
- Add a "Status: built (2026-08-12), pending review" line to `docs/BACKLOG.md` item 5, matching item 3's convention (Finding 2).

## Overall verdict
**Approve with follow-ups.** All 8 acceptance criteria hands-on verified
against the real code (not just read/inferred) — including getting past
the developer's own documented `whiptail`-via-pty tooling wall using `tmux`
instead, so `ct/create.sh`'s interactive path is now verified with real
keystrokes end-to-end rather than the stub fallback the previous pass
relied on. Full existing suite (75/75) unaffected. One should-fix (msgbox
truncation, real but non-blocking — doesn't violate any acceptance
criterion and the data is recoverable from terminal output) and one nit
(BACKLOG status-line) — neither blocks this cycle.
