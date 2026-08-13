# Implementation: deploy-target receiver (backlog item 2c, part 2, cycle 2a)

## Summary
Added a new `deploy-target/` directory and an `install.sh --with-deploy-target`
flag that together provision a receiver-only deploy target on a *separate*
machine from the switchboard: a low-priv `deploy` system user whose SSH key
can only write-only-rsync into one configured path (via `rrsync`) or trigger
one fixed, sudoers-scoped `systemctl restart` of one named service — nothing
else, no shell, ever. No switchboard-side wiring exists yet (that's a future
cycle, 2c-2b); this cycle is standalone-testable with nothing but a manually
generated SSH keypair and the `ssh`/`rsync` CLIs, and was exercised for real
end-to-end (real sshd, real throwaway system users, real sudo, real systemd
units) rather than only unit-tested.

## Root cause
N/A — new feature, not a bugfix.

## Changes by file
- `deploy-target/deploy-target.env.example` (new) — documents `DEPLOY_PATH`
  and `DEPLOY_SERVICE_NAME`, mirrors `host-agent/host.env.example`'s shape.
- `deploy-target/deploy-wrapper.sh` (new) — the `authorized_keys` forced
  command. Branches on `$SSH_ORIGINAL_COMMAND` via a literal `case` match
  only (never `eval`s it): `rsync --server*` → validates `DEPLOY_PATH` is
  set and absolute, then `exec /usr/bin/rrsync -wo "$DEPLOY_PATH"` (hardcoded
  absolute path, not PATH-resolved); the literal string `deploy-restart` →
  `exec sudo -n /usr/local/bin/ai-dev-switchboard-deploy-restart.sh`;
  anything else (including no command) → error to stderr, exit 1.
- `deploy-target/deploy-restart.sh` (new) — the one script `deploy` may run
  as root. Re-validates `DEPLOY_SERVICE_NAME` against
  `^[A-Za-z0-9@_.-]+$` (defense in depth against a hand-edited config), then
  `systemctl restart -- "$DEPLOY_SERVICE_NAME"` under `set -euo pipefail` —
  a restart failure propagates as a non-zero SSH exit, never swallowed.
- `deploy-target/README.md` (new) — setup steps, the `/bin/sh`-not-`nologin`
  rationale, a manual verification walkthrough, the protocol contract for a
  future 2c-2b caller, and removal/rollback steps.
- `install.sh` — new `--with-deploy-target` flag (flag-list comment,
  `WITH_DEPLOY_TARGET` variable, parsing case arm) and a new provisioning
  block placed immediately after the existing `WITH_HOST_CONTROL` block:
  prompts for `DEPLOY_PATH`/`DEPLOY_SERVICE_NAME`/`DEPLOY_PUBKEY`, verifies
  `/usr/bin/rrsync` exists (skips only this block, not the whole run, if
  missing), idempotently creates the `deploy` user (`/bin/sh` shell),
  creates/chowns `DEPLOY_PATH` if supplied, installs
  `/etc/ai-dev-switchboard/deploy-target.env` (copy-once-then-`set_env`,
  only overwriting keys actually supplied this run), installs the two
  scripts to `/usr/local/bin/ai-dev-switchboard-deploy-{wrapper,restart}.sh`,
  writes `authorized_keys` deterministically if a pubkey was supplied (else
  prints hand-add instructions), writes and `visudo -cf`-validates
  `/etc/sudoers.d/ai-dev-switchboard-deploy-target` (zero-argument
  `NOPASSWD` grant), and prints a summary.
- `README.md` (top-level) — added `deploy-target/` to the repo-layout tree
  and one new security-notes bullet describing the SSH-key restriction,
  explicitly noting this is receiver-only infrastructure with no
  switchboard UI consumer yet.
- `tests/test_deploy_target.py` (new) — see "How to verify locally".

## Key decisions / tradeoffs
- **Followed `host-agent`'s shape, not the deleted `target-setup.sh`'s**, per
  the spec: dedicated low-priv user, forced-command/no-shell SSH
  restriction, a fixed zero-argument sudoers rule — no systemd `.path`-unit
  inotify trigger.
- **`/bin/sh`, not `nologin`, for `deploy`'s shell** — `nologin` ignores its
  `-c` argument and never runs the forced command at all; carried forward
  from the deleted `target-setup.sh`'s own documented reasoning.
- **No hand-parsing of the rsync wire protocol** — the wrapper only ever
  does a literal string `case` match on `$SSH_ORIGINAL_COMMAND`; all actual
  path-restriction enforcement is delegated to `rrsync` itself.
- **Real end-to-end testing over unit testing wherever possible** — this is
  security-sensitive surface (SSH forced commands, sudoers scoping,
  `rrsync` path restriction), so `tests/test_deploy_target.py` provisions
  real throwaway system users, a real sudoers rule, and a real systemd unit,
  and drives them over real `ssh`/`rsync` against this machine's own
  already-running sshd, rather than mocking any of that away. See "How to
  verify locally" for the three test tiers.
- **`install.sh`'s own `--with-deploy-target` block is tested by extracting
  and running its literal source**, not by re-implementing equivalent logic
  in Python and not by running the *entire* `install.sh` (which would also
  do apt-get installs, create `RUN_USER`/`SVC_USER`, and start a second real
  switchboard systemd service — too invasive for a test and inconsistent
  with the fact that no other `--with-*` flag has an `install.sh`-level
  test in this repo either). The extraction includes `install.sh`'s own
  `prompt()`/`set_env()`/`get_env()` helpers verbatim, so a future edit to
  the block's logic is exercised for real, not against a stale copy.
- **Discovered `prompt()` reads from `/dev/tty`, not stdin** (deliberate, so
  a `curl | bash` install still prompts interactively) — a plain
  stdin-piped `subprocess.run()` therefore always sees `prompt()`'s
  non-interactive branch and returns only the empty default, regardless of
  what's piped in. `tests/test_deploy_target.py` drives `install.sh`'s block
  through a real pty (`pty.fork()`) instead, exactly like a human typing at
  a terminal — this actually caught the harness silently not exercising
  the values I intended in an early draft of these tests (see below).

## Deviations from spec
- **The spec's own "Protocol contract" / acceptance-criteria example command
  (`rsync -e "ssh -i <key>" -a ./somefile deploy@target:<DEPLOY_PATH>/`) does
  not work as literally written against real `rrsync`.** Verified directly:
  `rrsync` treats any destination arg starting with `/` as relative to its
  own restricted root and prepends that root to it (see `/usr/bin/rrsync`'s
  `validated_arg()`), so repeating the absolute `DEPLOY_PATH` in the client
  command causes rsync to try to `mkdir` a doubled/nested path that doesn't
  exist, and the push fails (`rsync: [Receiver] mkdir "...deploydest/tmp/
  .../deploydest" failed`). The **correct**, verified-working form leaves
  the destination bare (`deploy@target:`, no path at all) — the server has
  already fixed the destination via `command="... rrsync -wo $DEPLOY_PATH"`.
  I corrected the example commands in `deploy-target/README.md` and
  `install.sh`'s own printed summary to the verified-working bare form, and
  left a note in the README explaining why. `docs/spec.md` itself still has
  the original (non-working-as-written) example; flagging it here since I
  can't edit the spec, but the *design intent* (push lands under
  `DEPLOY_PATH`) is unaffected — only the exact command syntax needed
  correcting.
- **The spec's path-escape acceptance criterion says "rrsync refuses"**
  (implying an error) for an escape attempt like `deploy@target:/etc/`.
  In practice `rrsync` doesn't error on this — it silently re-roots the
  absolute path underneath `DEPLOY_PATH` (so `/etc/` becomes
  `DEPLOY_PATH/etc/`), and the rsync call reports success (exit 0). The
  actual, load-bearing security property — **nothing is ever written
  outside `DEPLOY_PATH`** — holds regardless, and that's what
  `tests/test_deploy_target.py`'s
  `test_path_escape_attempt_rejected_no_file_written_outside` asserts
  directly (plus confirming the file landed re-rooted at
  `DEPLOY_PATH/etc/escapefile`, proving this was `rrsync`'s remapping and
  not some unrelated failure). No code changed because of this — it's a
  property of `rrsync` itself, working as upstream-designed — but it's
  worth the reviewer knowing the exit-code framing in `docs/spec.md`
  doesn't match `rrsync`'s actual behavior.
- Everything else in the spec's "Proposed approach" (file layout, script
  responsibilities, `install.sh` step-by-step provisioning, sudoers/
  `authorized_keys` exact line shapes, edge cases) was implemented as
  written; no other deviations.

## Known limitations
- Matches the spec's own "Open questions": one `deploy` user / one path /
  one service per target machine (no multiplexing multiple projects onto
  one target machine yet); no locking between overlapping push+restart
  pairs (a future caller must serialize); `deploy-restart` is a fixed
  protocol keyword, not a secret; restart is `systemctl restart` only, not
  a general command-runner. None of these are addressed here — they're
  explicitly out of scope for this cycle per the spec.
- No switchboard-side wiring exists yet (by design — that's 2c-2b). Nothing
  in `app/app.py`, `config/switchboard.env.example`, or the switchboard's
  own sudoers/systemd assets changed.
- `install.sh`'s interactive prompts for `DEPLOY_PATH`/`DEPLOY_SERVICE_NAME`/
  `DEPLOY_PUBKEY` have no non-interactive (`--yes`/env-var) override — same
  as every other prompt-driven value already in `install.sh` (e.g.
  `RUN_USER`, `PVE_HOST`); not a gap introduced by this cycle.

## How to verify locally
```
cd /home/dev/projects/ai-dev-switchboard
python3 -m unittest tests.test_deploy_target -v      # this cycle's suite (30 tests)
python3 -m unittest discover -s tests -v              # full repo suite (245 tests)
bash -n install.sh                                     # syntax check
```
`tests/test_deploy_target.py` has three tiers (see its own module
docstring for the reasoning behind each):
1. `WrapperBranchingTests` / `RestartValidationTests` / `InstallShTemplateTests`
   — no root needed; branching/validation logic exercised directly, with
   fake `rrsync`/`sudo` stand-ins on `PATH` recording their own argv where
   that's meaningful (the wrapper's `rrsync` call uses a hardcoded absolute
   path deliberately, so that branch is instead verified against the real
   `/usr/bin/rrsync`).
2. `PrivilegedEndToEndTests` — gated on passwordless `sudo` and a reachable
   local sshd (both present in this dev sandbox); provisions a real
   throwaway system user (not literally `deploy`, to avoid colliding with a
   genuine `deploy` account on whatever box runs this suite), a real
   sudoers rule, and a real systemd unit, then drives all of it over real
   `ssh`/`rsync` against `127.0.0.1` — covers every "Acceptance criteria"
   bullet in `docs/spec.md` directly (push lands correctly; path-escape
   attempt never lands outside `DEPLOY_PATH`; arbitrary command / bare
   interactive attempt rejected with no shell; `deploy-restart` genuinely
   restarts the service with no password prompt; a nonexistent service
   fails loudly; unset/malformed `DEPLOY_PATH` fails closed; the sudoers
   grant can't be used for anything else).
3. `InstallScriptDeployTargetBlockTests` — gated on passwordless `sudo`;
   extracts and runs `install.sh`'s actual `--with-deploy-target` block
   verbatim (plus its own `prompt()`/`set_env()`/`get_env()` helpers) via a
   real pty, using the literal `deploy` username — covers the
   install-flow-specific acceptance criteria (full provisioning shape,
   blank-pubkey handling, re-run-with-different-values not accumulating,
   and running alongside `--with-host-control` with no conflicting state).

All tests clean up their own throwaway state (`useradd -r`/`rm -f`/
`systemctl stop` + unit-file removal); verified clean after a full run via
`id deploy`, `ls /etc/ai-dev-switchboard`, `ls /usr/local/bin/
ai-dev-switchboard-*`, `ls /etc/sudoers.d/`.

Manual walkthrough (what the tests above automate) is documented in
`deploy-target/README.md`'s "Verifying the restriction" section.
