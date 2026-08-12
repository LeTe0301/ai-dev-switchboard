# Implementation: VS Code (code-server) dark mode by default

Seeds a shared `settings.json` (`{"workbench.colorTheme": "Default Dark+"}`)
into `RUN_USER`'s code-server user-data directory during
`install.sh --with-code-server`, so code-server opens in dark mode the first
time anyone uses it. Never overwrites an existing `settings.json`. No
`docs/design.md` for this cycle — pure install-script change with no UI
surface, same right-sizing call as the prior `PUBLISH_MODE`-prompt cycle.

## What changed, by file

### `install.sh`

New block inserted right after the existing `PROJECTS_DIR` setup (now at
lines 144-166) and before the `-- App + engines --` section, gated on the
existing `WITH_CODE_SERVER` flag — originally shipped verbatim from the
spec's "Proposed approach," then hardened per a reviewer-found security
finding (see "Fix: symlink-following arbitrary-file-write-as-root" below).
Current shape:

```bash
path_has_symlink() {  # path_has_symlink <abs path> -> true if any component is a symlink
    local p="$1" check="" part
    local IFS=/
    for part in $p; do
        [ -n "$part" ] || continue
        check="$check/$part"
        [ -L "$check" ] && return 0
    done
    return 1
}
...
if [ "$WITH_CODE_SERVER" -eq 1 ]; then
    echo "-- code-server default theme --"
    CODE_SERVER_DIR="/home/$RUN_USER/.local/share/code-server"
    CODE_SERVER_USER_DIR="$CODE_SERVER_DIR/User"
    CODE_SERVER_SETTINGS="$CODE_SERVER_USER_DIR/settings.json"
    if path_has_symlink "$CODE_SERVER_SETTINGS"; then
        echo "Skipping code-server theme seed: a symlink exists under $CODE_SERVER_DIR — not following it." >&2
    else
        mkdir -p "$CODE_SERVER_USER_DIR"
        if [ ! -f "$CODE_SERVER_SETTINGS" ] && [ ! -L "$CODE_SERVER_SETTINGS" ]; then
            cat > "$CODE_SERVER_SETTINGS" <<'JSON'
{
  "workbench.colorTheme": "Default Dark+"
}
JSON
        fi
        chown -R "$RUN_USER:$RUN_USER" "$CODE_SERVER_DIR"
    fi
fi
```

Placed after `RUN_USER` is created/exists (this block runs after the
`useradd`/`PROJECTS_DIR` block, well before code-server's own binary-install
step at line 118 would matter for this — `RUN_USER` already existing is the
only precondition this block needs). The `[ ! -f settings.json ]` guard is
still the never-clobber contract for a genuine pre-existing file; no
`--force` path added. `chown -R` is scoped to `.../code-server` only (not
the whole `.local` tree) and runs unconditionally inside the flag block
(when the symlink guard doesn't skip it), matching the spec's implementer
notes.

#### Fix: symlink-following arbitrary-file-write-as-root (post-review)

`docs/test-review.md`'s Finding 1 (must-fix, reproduced two ways) found the
original block would follow a symlink `RUN_USER` — an unprivileged but
fully legitimate, shell-having tenant account, not a hypothetical attacker
— planted anywhere under their own home directory before a routine
`install.sh --with-code-server` re-run: (1) a dangling `settings.json`
symlink pointing at e.g. `/etc/cron.d/pwned` slipped past the `[ ! -f ... ]`
guard (a dangling symlink makes `-f` evaluate false) and root's `cat >`
wrote the fixed JSON straight into the attacker-chosen target; (2)
replacing the `code-server` directory itself with a symlink to an arbitrary
directory made root's `mkdir -p`/write create a new file tree inside that
directory instead.

Fix: added a small `path_has_symlink <path>` helper (walks every path
component from `/` down, `[ -L ]`-testing each one) and gate the whole
seed-and-chown branch on it — if any component from `/home/$RUN_USER` down
through `.local`, `.local/share`, `code-server`, `User`, and
`settings.json` itself is a symlink (dangling or not), the block logs a
one-line skip message to stderr and does nothing further: no `mkdir -p`,
no write, no `chown -R`. This treats "something is already there, even a
symlink" the same way the pre-existing `[ ! -f ... ]` guard already treats
"a real file is already there" — don't touch it — extended to cover the
symlink case the original guard missed. Kept a redundant `[ ! -L ... ]` in
the inner write condition as a second, cheap guard directly at the point of
the `cat >` write (belt-and-suspenders, not load-bearing on its own since
`path_has_symlink` already covers the leaf).

Not attempted: a fully TOCTOU-proof implementation (e.g. atomic
open-with-`O_NOFOLLOW`-per-component, or write-to-tempfile-then-`mv`
tricks) — the reviewer's own fix direction explicitly said this "does not
need to be elaborate," and closing the specific window between this
check and `install.sh`'s single-pass, non-concurrent execution of this
block would require dropping into a compiled helper or `python3 -c`
snippet for real `O_NOFOLLOW` semantics, disproportionate to the risk this
script is meant to mitigate (a local account racing root's write during
the few-millisecond gap of one `install.sh` run, vs. the two static,
easily-timed exploits actually reproduced and now blocked).

### `README.md`

The existing VS Code bullet (was line 98-99, now the same two lines) gained
one trailing clause:

```
- **VS Code in the browser**, independent on/off per project
  (`code-server`, `--with-code-server`; opens in a dark theme by default).
```

No other docs touched — no dedicated code-server doc page exists, and
`config/switchboard.env.example` gets no new variable (spec's explicit
non-goal: no `CODE_SERVER_THEME` knob).

## Key decisions

- Followed the spec's exact block and placement — no deviation in shape,
  content, or the `[ ! -f ... ]` idempotency mechanism.
- No new dependency, no new config variable, no changes to `app/app.py` or
  the sudoers block — matches the spec's non-goals precisely.

## Deviations from spec / design

The spec's "Proposed approach" block was shipped byte-for-byte in the first
pass (block content, insertion point relative to `PROJECTS_DIR`, README
wording); line numbers shifted slightly from the spec's citations (spec
cited "after line 132"; landed at line 134 at the time, then 144 after the
symlink-guard fix added a new helper function above it) — expected/harmless
positional drift, not a content deviation.

One real deviation, made post-review: the spec's exact block (mkdir -p +
`[ ! -f ... ]` + `cat >` + `chown -R`, no symlink handling) turned out to
have a security defect the spec didn't anticipate (`docs/test-review.md`
Finding 1 — see "Fix: symlink-following arbitrary-file-write-as-root"
above). The write mechanism was hardened with a `path_has_symlink` guard;
the spec's actual *goals* (idempotent seed, never clobber a real file,
gated on `WITH_CODE_SERVER`, placed after `RUN_USER` exists) are all still
met exactly as written — this only closes a gap in the spec's literal
mechanism, not a change in scope or intent.

## Known limitations

- **AC5 ("opens already in the Default Dark+ theme with no manual switch
  needed") could not be fully proven end-to-end via a real browser** — no
  headless browser (Chromium/Playwright/Puppeteer) is available in this
  sandbox (`playwright` Python module absent, no `pip3`, no
  chromium/chromium-browser binary, no global `puppeteer`). What *was*
  actually tried and confirmed instead (real `code-server` 4.131.0 binary,
  not mocked — see "Verification performed" below): started a real
  code-server process as the throwaway test user with the seeded
  `settings.json` in place, confirmed via its own startup log that it
  resolved `Using user-data-dir /home/cstest/.local/share/code-server` —
  exactly the path this block writes into — and confirmed via the served
  HTML's `vscode-workbench-web-configuration` meta tag that
  `userDataPath` matches that same path server-side. code-server 4.x's
  workbench applies `workbench.colorTheme` from that directory's
  `User/settings.json` entirely client-side (inside the bundled
  `workbench.js`, loaded as a `<script type="module">`), so the initial
  HTML response itself carries no theme marker to grep for — that's the
  real wall hit, not an unexamined assumption. The settings file
  format/key (`workbench.colorTheme`: `"Default Dark+"`) is standard,
  off-the-shelf VS Code/code-server user-settings syntax, already verified
  via web search per the spec's own "Background" section — this cycle's
  diff only writes that one file; it doesn't touch how code-server
  consumes it.
- No CI/shellcheck/bats harness exists in this repo (confirmed absent,
  consistent with prior shell-script cycles), so all verification below is
  a real-binary harness, not a mocked unit-test suite.

## Verification performed

1. **Syntax**: `bash -n install.sh` — passes.
2. **New block, run for real** (not mocked) against a throwaway Linux user
   (`cstest`, created via `useradd -m`, deleted via `userdel -r` after —
   left no residue) so `chown -R $RUN_USER:$RUN_USER` exercises real
   ownership changes exactly as `install.sh` would on a real box. The
   actual lines 134-146 were extracted verbatim (`sed -n '134,146p'
   install.sh`) and sourced with `RUN_USER=cstest` set, for each scenario:
   - **AC1** (fresh box, `--with-code-server`): after running,
     `/home/cstest/.local/share/code-server/User/settings.json` exists,
     owned `cstest:cstest`, content is exactly
     `{"workbench.colorTheme": "Default Dark+"}` (pretty-printed).
   - **AC2** (fresh box, no `--with-code-server`): `WITH_CODE_SERVER=0` run
     — no `.local/share/code-server` directory created at all.
   - **AC3** (existing `settings.json`, hand-edited content
     `{"workbench.colorTheme":"Custom Purple","editor.fontSize":42}`),
     re-run twice (once with the flag, once without) — SHA-256 of the file
     taken before and after each re-run: identical
     (`304b4a91708fd3673254dbc064f6a8b25e5c37554f6f920bf269aef1fb47f123`)
     across all three hashes. Byte-for-byte untouched.
   - **AC4** (flag added on a later re-run): first ran with
     `WITH_CODE_SERVER=0` (confirmed nothing seeded), then re-ran the same
     extracted block with `WITH_CODE_SERVER=1` — `settings.json` seeded
     correctly on that later run, owned `cstest:cstest`.
   - **AC6** (README wording): diffed directly — bullet now reads "...
     opens in a dark theme by default)."
3. **AC5** (real code-server, opens in dark theme by default): see "Known
   limitations" above for exactly what was proven vs. the real wall hit
   (no headless browser in this sandbox to assert on rendered CSS/DOM).
   `code-server --version` → `4.131.0`; confirmed real binary present at
   `/usr/local/bin/code-server` in this sandbox and actually exercised.
4. **Existing automated suite unaffected**: `python3 -m unittest discover
   -s tests -v` → 75/75 pass, both before and after this change (expected
   — `app/app.py` untouched).
5. **Cleanup verified**: throwaway `cstest` user removed
   (`userdel -r cstest`; `id cstest` afterward → "no such user"), spawned
   code-server process killed, no residue left under `/home/cstest` or
   `/tmp`.

## Post-review fix verification (Finding 1 — symlink guard)

Re-tested hands-on against the live block, same technique as the original
pass and the reviewer's own reproduction: real Linux users via `useradd
-m` (no mocking), the actual current lines extracted verbatim from
`install.sh` (`path_has_symlink` at 95-104 + the code-server block at
144-166, `diff`-confirmed byte-identical to the live file before use), run
as root via `sudo bash <extracted script> <RUN_USER>`. All throwaway
users/files removed afterward, verified via `id <user>` → "no such user"
and `ls /home` showing no leftover home dirs.

1. **Reviewer's exploit 1 reproduced against the fix (dangling file
   symlink)**: as unprivileged user `exploit1`, planted
   `~/.local/share/code-server/User/settings.json -> /etc/cron.d/pwned`
   (confirmed `/etc/cron.d/pwned` did not exist beforehand — cleaned up one
   stale copy left over from the reviewer's own pre-fix session first). Ran
   the fixed block as root with `RUN_USER=exploit1`. Result: block printed
   `Skipping code-server theme seed: a symlink exists under
   /home/exploit1/.local/share/code-server — not following it.`, exited 0,
   and **`/etc/cron.d/pwned` was never created**. `settings.json` is still
   the same untouched dangling symlink afterward (`readlink` confirms).
2. **Reviewer's exploit 2 reproduced against the fix (directory-level
   symlink)**: as unprivileged user `exploit2`, planted
   `~/.local/share/code-server -> /tmp/sensitive-target2` (confirmed target
   didn't exist beforehand). Ran the fixed block as root with
   `RUN_USER=exploit2`. Result: same skip message, exit 0, **and
   `/tmp/sensitive-target2` was never created** — root never followed the
   symlink into `mkdir -p` or the write. The symlink itself is untouched
   afterward.
3. **All 6 spec acceptance criteria re-run against the fixed block**
   (fresh throwaway users each time, none reused across cases):
   - **AC1** (fresh box, `--with-code-server`): `settings.json` created,
     owned `actest1:actest1`, mode `644`, content exactly
     `{"workbench.colorTheme": "Default Dark+"}` — pass.
   - **AC2** (fresh box, no `--with-code-server`): neither
     `.local/share/code-server` nor `.local` itself created — pass.
   - **AC3** (hand-edited `settings.json`
     `{"workbench.colorTheme":"Custom Purple","editor.fontSize":42,"note":"hand-edited by user"}`,
     read via `sudo sha256sum`/`sudo cat` since the throwaway user's home is
     mode 700): SHA-256 identical
     (`c70e3fe1cf6fe126f7b4c70fb3338d273d8a6172cb45338e158621946b97aee0`)
     before, after a re-run **with** the flag, and after a further re-run
     **without** the flag — byte-for-byte untouched across both — pass.
   - **AC4** (installed originally without the flag, flag added on a later
     re-run): first run with `WITH_CODE_SERVER=0` confirmed nothing
     created; second run with `WITH_CODE_SERVER=1` seeded `settings.json`
     correctly, owned `actest4:actest4` — pass.
   - **AC5** (seeded file → dark theme with no manual switch): this fix's
     diff changes only the symlink-guard logic, not the JSON content or
     destination path written on a normal (non-symlink) run — confirmed
     identical to the pre-fix content in the AC1 check above. Re-confirmed
     the destination path independently: started a real code-server 4.131.0
     process (`code-server --bind-addr 127.0.0.1:18099 --auth none
     /home/actest1`) as `actest1` (who already had the AC1-seeded
     `settings.json` in place) and its own startup log resolved `Using
     user-data-dir /home/actest1/.local/share/code-server` — the exact path
     this block writes into. The reviewer's own prior session already did
     the deeper real-headless-Chromium DOM/CSS assertion against this exact
     content+path combination and confirmed the dark theme actually
     renders (`docs/test-review.md` AC5 row) — not re-run here since
     nothing this fix touches could affect that result.
   - **AC6** (README wording): `grep` confirms the bullet still reads
     "... opens in a dark theme by default)." — untouched by this fix,
     pass.
   - **Edge — `chown -R` scope stays narrow**: planted root-owned sibling
     `~/.local/share/other-root-owned-dir` and `~/.bashrc_extra` under a
     fresh user (`actest5`) before running the block; both remained
     `root:root` afterward, only `.local/share/code-server` became
     `actest5:actest5` — pass, matches the pre-fix behavior (variable
     rename only, same scope).
4. **Syntax**: `bash -n install.sh` — passes.
5. **Existing automated suite unaffected**: `python3 -m unittest discover
   -s tests -v` → 75/75 pass (`app/app.py` untouched by this fix).

## How to verify locally

```bash
cd /home/dev/projects/ai-dev-switchboard
bash -n install.sh
python3 -m unittest discover -s tests -v
```

Real end-to-end smoke test (closest to the literal acceptance criteria):

```bash
sudo ./install.sh --with-code-server
sudo cat /home/$RUN_USER/.local/share/code-server/User/settings.json
# → {"workbench.colorTheme": "Default Dark+"}
sudo stat -c '%U:%G' /home/$RUN_USER/.local/share/code-server/User/settings.json
# → $RUN_USER:$RUN_USER

# never-clobber check:
sha256sum /home/$RUN_USER/.local/share/code-server/User/settings.json > /tmp/before.hash
sudo ./install.sh --with-code-server   # re-run
sha256sum /home/$RUN_USER/.local/share/code-server/User/settings.json > /tmp/after.hash
diff /tmp/before.hash /tmp/after.hash   # should be empty

# flag-added-later check (on a fresh box without the flag first):
sudo ./install.sh                      # no --with-code-server
sudo test -d /home/$RUN_USER/.local/share/code-server && echo "unexpected" || echo "OK: not created"
sudo ./install.sh --with-code-server   # flag added on a later run
sudo cat /home/$RUN_USER/.local/share/code-server/User/settings.json

# real browser check (AC5), which this sandbox could not run:
# open http://<host>/code/<any project> in an actual browser after the
# above — the editor should load already in the "Default Dark+" theme.
```

Symlink-rejection check (Finding 1 regression test — run as root, e.g. on
a throwaway VM/user, not against a real `$RUN_USER` you care about):

```bash
# exploit 1: dangling file symlink -> should NOT be followed
sudo -u "$RUN_USER" bash -c 'mkdir -p ~/.local/share/code-server/User && ln -sf /etc/cron.d/pwned ~/.local/share/code-server/User/settings.json'
sudo ./install.sh --with-code-server
sudo test -e /etc/cron.d/pwned && echo "FAIL: exploit succeeded" || echo "OK: not followed"
sudo -u "$RUN_USER" test -L ~/.local/share/code-server/User/settings.json && echo "OK: symlink untouched"

# exploit 2: directory-level symlink -> should NOT be followed
sudo -u "$RUN_USER" bash -c 'rm -rf ~/.local/share/code-server && ln -sf /tmp/sensitive-target ~/.local/share/code-server'
sudo ./install.sh --with-code-server
sudo test -e /tmp/sensitive-target && echo "FAIL: exploit succeeded" || echo "OK: not followed"
```
