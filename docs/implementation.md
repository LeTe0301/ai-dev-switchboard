# Implementation: PUBLISH_MODE as an interactive install-time prompt

Surfaces the existing `PUBLISH_MODE` (`tailscale`/`none`) and `BASE_URL`
runtime config as interactive prompts in `install.sh` and `ct/create.sh`,
reusing each script's existing prompt machinery, so choosing tailscale
publishing no longer requires a manual `switchboard.env` edit + restart. No
UI/design phase for this cycle (pure install-time shell scripting, per the
dispatch's own right-sizing call) and no `app/app.py` changes — this is
purely a setup-time UX change surfacing an already-correct runtime config
path.

## What changed, by file

### `install.sh`

- New `-- Publishing --` block inserted right after the `AUTH_MODE`
  if/else block and before the `WITH_HOST_CONTROL` check (now at lines
  179-186), following the exact shape of the existing `AUTH_MODE`/`PVE_HOST`
  precedent:
  ```bash
  echo "-- Publishing --"
  PUBLISH_MODE=$(prompt "..." "$(get_env "$ENV_FILE" PUBLISH_MODE)")
  set_env "$ENV_FILE" PUBLISH_MODE "$PUBLISH_MODE"
  if [ "$PUBLISH_MODE" = "tailscale" ]; then
      BASE_URL=$(prompt "..." "$(get_env "$ENV_FILE" BASE_URL)")
      set_env "$ENV_FILE" BASE_URL "$BASE_URL"
  fi
  ```
  Both `prompt` calls default to whatever's already in `$ENV_FILE` (`none`
  on a first install, since `$ENV_FILE` is copied from
  `config/switchboard.env.example` — whose `PUBLISH_MODE=none` line is
  uncommented — before this block runs). Under `--yes`/non-interactive,
  `prompt()`'s existing `interactive()` gate (`[ "$YES" -eq 0 ] && [ -t 0 ]`)
  makes both calls fall straight through to that default with no blocking,
  unchanged from `AUTH_MODE`'s existing behavior.
- One new conditional line in the final "Done" summary (after the `Web UI:`
  line), shown only when `PUBLISH_MODE=tailscale`, reminding the operator
  to run `tailscale serve --bg https+insecure://127.0.0.1:$LISTEN_PORT`
  themselves for the main UI (this item deliberately does not run that
  command — see "Deviations" / spec's "Open questions").

### `ct/create.sh`

- New block inserted after the `WITH_CODE_SERVER` `yesno()` prompt and
  before `TOTP_SECRET` generation:
  ```bash
  PUBLISH_MODE=$(menu "..." "none" "..." "tailscale" "...")
  BASE_URL=""
  if [ "$PUBLISH_MODE" = "tailscale" ]; then
      BASE_URL=$(ask "..." "")
  fi
  ```
  Default cursor lands on `"none"` (first `menu()` item), matching spec.
- The `TMP_ENV` heredoc's hardcoded `PUBLISH_MODE=none` line is now
  `PUBLISH_MODE=${PUBLISH_MODE}`, with a new `BASE_URL=${BASE_URL}` line
  added right after it (previously no `BASE_URL=` line existed in the
  pushed `switchboard.env` at all).
- `SUMMARY` gained a new `PUBLISH_NOTE` variable, non-empty only when
  `PUBLISH_MODE=tailscale`, spliced into the existing `SUMMARY` string right
  after the "Web UI ..." block — same reminder-of-the-manual-step content
  as `install.sh`'s, adapted for "inside the container."

### `config/switchboard.env.example`

Added one short comment block right above the existing `PUBLISH_MODE`/
`BASE_URL` documentation, noting both are now asked interactively by
`install.sh`/`ct/create.sh`, and this file only needs hand-editing (+
`systemctl restart ai-dev-switchboard`) to *change* the choice afterward.
No functional/schema line changed — `PUBLISH_MODE=none` stays the real
default line, `#BASE_URL=` stays commented out, exactly as spec required.

### `README.md`

"Reaching the UI" → the Tailscale bullet now says `install.sh`/
`ct/create.sh` already prompt for `PUBLISH_MODE`/`BASE_URL` at setup time
(pick `tailscale`, enter the tailnet hostname from `tailscale status`),
with manual `switchboard.env` editing + restart described as how to
*change* that choice later, not the only way to set it. The main UI's own
still-manual `tailscale serve` command (a separate, unchanged step) is
untouched. The SSH-tunnel bullet's own mention of `PUBLISH_MODE=tailscale`
was left as-is (still accurate — it just names the config value, not how
it's set).

## Key decisions

- **Config-only for `tailscale`, no automatic `tailscale serve`** — per the
  spec's explicit, already-reasoned call under "Open questions." Both
  scripts only ever set `PUBLISH_MODE`/`BASE_URL` and print a reminder;
  neither installs, authenticates, or invokes `tailscale` itself.
- **No new input validation** — `PUBLISH_MODE` accepts free text with no
  re-prompt loop, matching `AUTH_MODE`'s existing precedent exactly (spec's
  explicit non-goal).
- **`BASE_URL` may be left blank** — allowed in both scripts, matching
  today's already-tolerant handling of an unset `BASE_URL` at runtime.

## Deviations from spec / design

None. The implementation follows the spec's "Proposed approach" section
directly — same helper functions, same insertion points (adjusted only for
line-number drift from earlier edits in the same file, which the spec
itself flagged as approximate: "around today's install.sh:177-179"), same
heredoc line swap, same doc-wording asks. No design.md existed for this
cycle (correctly skipped per the dispatch's right-sizing note — this has no
user-visible UI dimension beyond terminal prompts already following an
established pattern).

## Known limitations

- **Live `whiptail` keystroke automation was attempted and hit a real
  wall in this sandbox**: driving `ct/create.sh`'s real `menu()`/`ask()`
  whiptail dialogs through a pty (`script -qec ...`, then a custom
  `pty.fork()`-based Python driver with settle-delay keystroke injection)
  either hung waiting for input that visibly never got delivered/processed,
  or produced erratic redraw/selection-toggling behavior — most likely a
  terminal-size/timing quirk of `whiptail`'s ncurses redraw loop inside this
  particular nested-pty sandbox, not a defect in the code under test (the
  *rendering* of both dialogs, with the real title/prompt/item text from
  the new code, was confirmed correct via a non-interactive `< /dev/null`
  run — see "Verification performed" below — it's specifically live
  keystroke delivery that didn't work here). Given that wall, `menu()`/
  `ask()` (pre-existing helpers, already relied on today for `AUTH_MODE`
  and unchanged by this cycle) were stubbed with canned-answer fakes for
  the control-flow/interpolation test described below — the same
  mock-the-non-pure-dependency technique the previous cycle used for
  `_register_via_privileged_script`. Real interactive use is otherwise
  unaffected: `menu()`/`ask()` themselves are untouched code, and the new
  block's actual whiptail invocations were confirmed to build valid
  commands (see below).
- No CI/shellcheck configured in this repo (confirmed by checking for
  `.github/workflows/`, `shellcheck`, `bats` — none exist), consistent with
  the previous shell-script change in this repo's history, which also had
  no prior test harness and built one narrowly scoped to what needed
  proving.

## Verification performed

1. **Syntax**: `bash -n install.sh` and `bash -n ct/create.sh` — both pass.
2. **`install.sh`'s new block, run for real** (not mocked): a test harness
   extracted the actual `interactive`/`prompt`/`set_env`/`get_env`
   functions and the actual new `-- Publishing --` block verbatim (by line
   range) from the real file, and drove them through a real pty
   (`script -qec ...`) with real keystrokes — proving the interactive
   `read -rp ... </dev/tty` path actually works, not just its logic in
   isolation. Four cases, all matching acceptance criteria exactly:
   - Interactive, type `tailscale` then `foo.ts.net` → resulting
     `switchboard.env`-equivalent file has real `PUBLISH_MODE=tailscale`
     and `BASE_URL=foo.ts.net` lines (AC 1, 2).
   - Interactive, press Enter on the default → `PUBLISH_MODE=none`, no
     `BASE_URL` prompt (AC 1).
   - `YES=1` (non-interactive), first install (`ENV_FILE` seeded with
     `PUBLISH_MODE=none`, matching what `switchboard.env.example` actually
     ships) → no blocking, `PUBLISH_MODE=none`, identical to today (AC 3).
   - Re-run with an existing `PUBLISH_MODE=tailscale`/`BASE_URL=foo.ts.net`,
     pressing Enter on both prompts → both values preserved exactly, not
     reset to `none`/blank (AC 4).
3. **`install.sh`'s new "Done" summary line**: extracted verbatim and run
   against both `PUBLISH_MODE=tailscale` and `PUBLISH_MODE=none` — the
   reminder line appears only in the `tailscale` case, with the real
   `LISTEN_PORT` interpolated; the `none` case's output is byte-for-byte
   identical to before this change (AC 6, 7).
4. **`ct/create.sh`'s new block + `TMP_ENV` heredoc + `SUMMARY`**: extracted
   verbatim, run with `menu()`/`ask()` stubbed to canned answers (see
   "Known limitations" above for why real keystroke-driven whiptail
   automation wasn't used here). Two cases:
   - `menu` answers `none` → `BASE_URL` never prompted, real heredoc
     content shows `PUBLISH_MODE=none`/`BASE_URL=` (was previously always
     `PUBLISH_MODE=none` with no `BASE_URL=` line at all), `SUMMARY`
     unchanged from before this change (AC 5, 7).
   - `menu` answers `tailscale`, `ask` answers `foo.ts.net` → real heredoc
     content shows `PUBLISH_MODE=tailscale`/`BASE_URL=foo.ts.net`, real
     `SUMMARY` string includes the tailscale reminder line (AC 5, 6).
5. **Real `whiptail` command validity** (the part item 4's mocking
   deliberately doesn't cover): invoked the actual `menu()`/`ask()` calls
   from the new code, unmocked, with stdin closed (`< /dev/null`) and a
   timeout — both dialogs render correctly (title, prompt text, and both
   `"none"`/`"tailscale"` tag/description pairs visible in the captured
   terminal output) before exiting 255 (whiptail's own immediate-EOF/cancel
   code, expected with no input available) — confirms no argument-count/
   quoting error in the real command, not just the stubbed version.
6. **Existing automated suite unaffected**: `python3 -m unittest discover
   -s tests -v` → 75/75 pass (unchanged from before this cycle — expected,
   since `app/app.py` was not touched).

## How to verify locally

Syntax + full harness re-run:
```bash
cd /home/dev/projects/ai-dev-switchboard
bash -n install.sh && bash -n ct/create.sh
python3 -m unittest discover -s tests -v
```

Manual interactive smoke test (recommended before treating this as fully
verified end-to-end on a real box — the harnesses above prove the new logic
but a real `sudo ./install.sh` run is the closest thing to the actual
acceptance criteria as literally worded):
```bash
sudo ./install.sh
# ... walk through the prompts; when asked "Publish per-project terminals
# via tailscale serve, or keep them loopback-only?", type `tailscale`,
# then a hostname when prompted for BASE_URL.
sudo grep -E '^(PUBLISH_MODE|BASE_URL)=' /etc/ai-dev-switchboard/switchboard.env
# → PUBLISH_MODE=tailscale
#   BASE_URL=<what you typed>
sudo ./install.sh   # re-run — press Enter on both prompts, confirm the
                     # same two values are shown as defaults and unchanged.
sudo ./install.sh --yes   # confirm it does not block and leaves the file
                           # unchanged from the previous run.
```
`ct/create.sh` needs a real Proxmox VE host (`pct`, `pveam`) to run
end-to-end, which wasn't available in this sandbox — its new logic was
verified via the extracted-block harness described above instead (see
"Known limitations").
