# Test & Review: Local git hosting UI + CI/CD (Gitea) — part 2b: repo creation via Gitea's API + retiring the old flow

## Scope

Round 2 of the testing + review pass for backlog item 2b (`docs/spec.md`).
Round 1 (preserved below under "Round 1 history") went **blocked** on one
must-fix defect (Defect 1: a same-second token-name collision in
`scripts/gitea-configure-api.sh`'s "safe to re-run" fix). This round
re-verifies the developer's fix for that defect hands-on against a real
Gitea instance (not by trusting `docs/implementation.md`'s account), checks
the two previously-flagged doc nits, and — since the testing pass came back
clean — proceeds to the full independent review pass round 1 never reached.

Docker Compose is available in this session; stood up a fresh real Gitea
1.27.1 + Postgres 14 stack via Docker Compose specifically to re-run the
adversarial repro, independent of the developer's own account of theirs.

## Test cases (round 2 — re-verification + full pass)

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Full suite regression (173 tests) | Automated: `python3 -m unittest discover -s tests -v` | pass | 173/173, run before and after this round's live testing |
| 2 | `bash -n`/syntax check on all shell scripts + `install.sh`; `app.py` parses | Automated | pass | all clean, including the modified `scripts/gitea-configure-api.sh` |
| 3 | Defect 1 fix — reviewer's exact original 5-in-a-row same-second repro, reproduced hands-on against a fresh real Gitea 1.27.1 instance | **Manual, live**, exact repro from round 1: `for i in 1 2 3 4 5; do printf 'admin\nai-dev-switchboard-gitea\n' \| sudo bash scripts/gitea-configure-api.sh; done`, no artificial delay | pass | all 5 runs printed "Setup verified", exit 0 |
| 4 | Defect 1 fix — stricter adversarial loop, 15 back-to-back runs, checked programmatically (not eyeballed) | **Manual, live** | pass | 15/15 runs, all "Setup verified", exit 0 (first attempt at this hit an unrelated systemd `StartLimitBurst` artifact from my own stub unit, not the script under test — fixed the stub and re-ran cleanly; see notes below) |
| 5 | Defect 1 fix — independently confirmed via Gitea's own token-list API that same-second runs never collide, not inferring from run outcomes alone | **Manual, live**: `GET /api/v1/users/admin/tokens?limit=100` (Basic Auth), diffed all 35 minted names (5+15+15 across this round's three loops) | pass | 35/35 unique names. Same-second collisions actually observed repeatedly: 7 distinct wall-clock seconds had 2–8 runs land in them (max 8 runs sharing one exact Unix second), and every one of those groups still produced 8 distinct names because of the random suffix |
| 6 | Doc nit 1: `config/switchboard.env.example`'s `GITEA_API_TOKEN` comment scope | Read | fixed | line 150-151 now reads "scope write:repository,write:user" |
| 7 | Doc nit 2: `docs/BACKLOG.md` tense for 2b | Read | fixed | line 71 now reads "2b built, pending final reviewer confirmation" (past/present tense, not "are still open") |
| 8 | Credential-handling/privileged-script surfaces re-confirmed (not assumed unaffected by a narrowly-scoped fix) | Read `app/app.py`'s `create_project()`/`_gitea_api()` diff, `scripts/new-project-from-gitea.sh` in full, `install.sh`'s diff | pass, unchanged from round 1's findings | token never in argv (`sudo ... owner repo_name name`, no token); `new-project-from-gitea.sh` sources the token from the 600 `switchboard.env` itself; owner/repo/name regexes unchanged and still close injection into the `su -c "git clone '$CLONE_URL' '$DEST'"` call; atomic `mkdir` (no `-p`) TOCTOU pattern unchanged; redaction of `GITEA_API_TOKEN` on clone failure unchanged |
| 9 | Retirement completeness still holds after this round's changes | `grep -rn` for the six deleted scripts + `NEW_PROJECT_SCRIPT` across `*.sh`/`*.py` | pass | only comment-only references remain (3, same as round 1) |
| 10 | Random-suffix generation sanity (does `head -c 8 /dev/urandom \| base64 \| tr -dc 'A-Za-z0-9' \| head -c 8` reliably produce 8 usable characters) | Manual: ran the exact pipeline 20 times standalone | pass (with a noted, pre-existing, non-blocking caveat) | all 20 samples were exactly 8 chars; the theoretical edge case (a run producing fewer than 8 alnum chars if `base64`'s `+`/`/`/`=` happen to dominate) is astronomically unlikely and is the same idiom already used by `install.sh`'s own `random_token()` — not a new risk introduced by this fix, and it doesn't threaten uniqueness (a shorter-but-still-random suffix is still random), just theoretically the width of the collision window vs. a fixed 8 chars. Not filed as a finding — see "Findings" for why |
| 11 | No leftover state from this round's live testing | Teardown: `docker compose down -v`, stub systemd unit + `/etc/ai-dev-switchboard` removed, `git status` clean, full suite re-run | pass | `git status` shows only the pre-existing uncommitted working tree (no new/leftover files), 173/173 tests pass post-teardown |

Round 1's own test cases (#1–19 in that pass, covering the happy path, clone,
push, Gitea-side collision, partial-failure cleanup, token scope, TOCTOU
race, injection validation, and retirement completeness against a live
Gitea instance) are not re-run verbatim here since this round's diff is
narrowly scoped to token-name generation plus two doc nits and none of that
surface was touched — see "What was independently verified as correct" in
round 1's history below, now reconfirmed via #8–9 above rather than assumed.

## Regression check
Full existing suite: `python3 -m unittest discover -s tests -v` — **173/173
pass**, run at the start and end of this round's session (confirming no
leftover state from this round's live Gitea testing leaked into the suite).

## Defects found (this round)
None. Defect 1 from round 1 is closed — see test case #3–5 above.

---

## Spec coverage

All acceptance criteria from `docs/spec.md` are implemented and covered by
either automated tests or live manual verification (round 1's table maps
each one to a specific test case; nothing in round 1's Defect 1 fix or this
round's changes touches any of that mapping except the "safe to re-run" edge
case itself, which is now closed):

- "+ New project" creates a real private Gitea repo + clone, `RUN_USER`-owned, shows up immediately — round 1, live-verified.
- `git push` from the fresh clone with zero extra credential prompt — round 1, live-verified.
- Gitea not installed → clear message, no legacy-script reference — round 1, automated + this round's retirement-completeness re-check.
- Gitea installed but off → "toggle it on first" — round 1, automated + live.
- Token not configured → message pointing at `gitea-configure-api.sh` — round 1, automated.
- Gitea-side slug collision → specific message, no orphaned `PROJECTS_DIR` dir — round 1, automated + live.
- Partial failure (clone script fails) → best-effort repo cleanup, original error surfaced — round 1, automated + live.
- `gitea-configure-api.sh` completes → token in `switchboard.env`, service restarted, `GET /user` verifies — round 1, live.
- **`gitea-configure-api.sh` safe to re-run** — round 1 found this **failing** (Defect 1); this round independently re-verified the fix closes it, including deliberately re-hitting the exact same-second failure condition and confirming it no longer collides (test cases #3–5 above).
- `install.sh --with-git-hosting` fresh run installs no legacy scripts/`git` user — round 1, static check; this round re-confirmed no drift (test case #9).
- Full test suite passes with new coverage, no real Docker/network calls in automated tests — round 1 + this round, 173/173 both times.

No gaps found. Every criterion in `docs/spec.md`'s "Acceptance criteria"
section maps to at least one test case that was actually run this round or
round 1, not merely read.

## Findings (most severe first)

No must-fix or should-fix findings.

### 1. Fixed-width random suffix could theoretically be less than 8 characters — nit
- File: `scripts/gitea-configure-api.sh:94` (`head -c 8 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 8`)
- Issue: if `base64`'s output for a given 8-byte sample happens to contain
  fewer than 8 alphanumeric characters after `+`/`/`/`=` are stripped, the
  resulting suffix is shorter than 8 characters (still random, just
  narrower collision-resistance for that one run). Astronomically unlikely
  in practice (confirmed all-8 across 20 standalone samples plus 35 real
  script runs this round), and this is the exact same idiom already used
  by `install.sh`'s own `random_token()` helper elsewhere in this codebase
  — not a new risk this fix introduces, and not something that threatens
  correctness (uniqueness doesn't depend on a fixed width, just on
  randomness).
- Failure scenario: none observed or plausible enough to justify a code
  change; noted for completeness, not filed as a follow-up.

## Follow-ups (non-blocking)
- None beyond what round 1 already documented as accepted tradeoffs
  (deferred sync-on-push, no auto-cleanup of an abandoned `PROJECTS_DIR`
  directory, untested `tailscale serve --set-path` against a real Tailscale
  node — all explicit, resolved defaults in `docs/spec.md`, not gaps).

## Overall verdict
**Approve.** Defect 1 is closed — independently reproduced the original
failing repro against a fresh real Gitea instance and confirmed it no
longer collides, including deliberately re-hitting the exact same-second
condition that caused the original failure and verifying via Gitea's own
token-list API that every same-second run still produced a unique name (35
runs, up to 8 sharing one exact Unix second, 0 collisions). Both doc nits
are fixed. Full regression suite (173/173) and syntax checks pass. The
credential-handling and privileged-script surfaces scrutinized in round 1
were re-confirmed unaffected by this round's narrowly-scoped change. The
independent review pass found no must-fix or should-fix issues — spec
coverage is complete, the diff matches `docs/spec.md`'s proposed approach
closely with no scope creep, and no correctness/security issues were found
in `create_project()`, `_gitea_api()`, `scripts/gitea-configure-api.sh`, or
`scripts/new-project-from-gitea.sh`. Backlog item 2b is ready to ship.

---

## Round 1 history (preserved verbatim for context)

### Round 1 scope
Testing + review pass for backlog item 2b (`docs/spec.md`, current
uncommitted working tree): `create_project()` rewired to Gitea's REST API,
new `scripts/gitea-configure-api.sh` (token bootstrap) and
`scripts/new-project-from-gitea.sh` (privileged clone hand-off), and
retirement of the six legacy git-hosting scripts + `config/git-hosting.env.example`
from `install.sh`.

Docker Compose is genuinely available in this session (confirmed directly,
same sandbox quirk the developer described: the Compose CLI plugin is only
under the `dev` user's own `~/.docker/cli-plugins/`, not root's — worked
around the same way, by copying it into `/usr/lib/docker/cli-plugins/` for
the duration of testing and removing it afterward). Per `docs/spec.md`'s
Open Question 1, this means the bar here is a full live round trip against
a real running Gitea, not mocked tests alone — performed independently
below, not by trusting the developer's own account of theirs.

### Round 1 test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | Full suite regression (173 tests, incl. new `GiteaSlugTests`/`GiteaApiTests`/`CreateProjectGiteaTests`/`test_new_project_from_gitea.py`) | Automated: `python3 -m unittest discover -s tests -v` | pass | 173/173, ran twice (before and after live round trip) — no leftover-state interference |
| 2 | `_gitea_slug()` mapping (spaces→hyphens, collapse, strip, output matches Gitea's char class) | Automated (`GiteaSlugTests`, 6 tests) | pass | test run above |
| 3 | `_gitea_api()` request shape, 2xx/4xx/5xx handling, connection-failure → `ConnectionError` | Automated (`GiteaApiTests`, 7 tests, monkeypatched `urlopen`) | pass | test run above |
| 4 | Happy path: Gitea on + token configured → real private repo created (`auto_init`, `default_branch=main`), cloned into `PROJECTS_DIR/<name>`, owned by `RUN_USER`, shows up immediately | **Manual, live**: real Gitea 1.27.1 + Postgres 14 via Docker Compose, real admin account, real `gitea-configure-api.sh` run, `app.create_project()` called directly (real code, not reimplemented) | pass | `create_project("Live Round Trip 1")` → `(True, '')`; `Live Round Trip 1/.git` present, `stat` shows `dev:dev` ownership; repo visible via `GET /api/v1/repos/admin/Live-Round-Trip-1` |
| 5 | `git push` from the fresh clone succeeds with zero extra credential prompt | **Manual, live** | pass | committed + `git push origin main` → `cc1b404..3eaa802 main -> main`, no prompt, no extra setup |
| 6 | Gitea not installed → clear message, no `NEW_PROJECT_SCRIPT` reference | Automated (`test_gitea_not_installed_returns_clear_message_no_legacy_reference`) | pass | asserts `--with-git-hosting` in msg, `NEW_PROJECT_SCRIPT` not in msg |
| 7 | Gitea installed but off → "toggle it on first," no API call attempted | Automated + **live** (`gitea_run("status")` returned `off` before toggle, `create_project()` returned that exact message) | pass | see transcript above |
| 8 | Token not configured → message pointing at `gitea-configure-api.sh`, no API call | Automated (`test_missing_token_returns_message_pointing_at_bootstrap_script`) | pass | |
| 9 | Gitea-side slug collision (different local name, same slug) → specific "already exists on Gitea" message, no `PROJECTS_DIR` dir left behind | Automated (409/422 cases) + **live** (`"Live Round Trip 1 "` → slug `Live-Round-Trip-1`, already existed from case 4) | pass | live: `create_project()` → `(False, "A Gitea repository named 'Live-Round-Trip-1' already exists...")`, no dir created |
| 10 | Privileged hand-off fails → best-effort `DELETE /repos/{owner}/{repo}` cleanup, no orphaned repo, original error surfaced (truncated to 300 chars) | Automated (3 tests) + **live** (pointed `NEW_PROJECT_FROM_GITEA_SCRIPT` at `/bin/false` after a real repo existed) | pass | live: `GET /api/v1/repos/admin/Cleanup-Test-Project` → `404` after the forced failure |
| 11 | `gitea-configure-api.sh` completes → `GITEA_API_TOKEN` in `switchboard.env`, service restarted, `GET /user` verifies | **Manual, live** (real `switchboard.env`, stub systemd unit, real `docker exec ... generate-access-token`) | pass | script printed "Setup verified -- authenticated as 'admin'" |
| 12 | Token scope: spec assumed `write:repository` alone sufficient for `POST /user/repos`; developer's fix claims `write:repository,write:user` is actually required | **Independently reproduced, live**, not just re-read the developer's account | confirmed correct | minted a fresh `write:repository`-only token directly via `gitea admin user generate-access-token`; `POST /user/repos` → `403 required=[write:user]`; `GET /user` → `403 required=[read:user]`. Also independently confirmed the *other* half: a `write:repository`-only token successfully cloned **and pushed** to an existing repo (no `write:user` needed for that half) |
| 13 | `gitea-configure-api.sh` "safe to re-run" (edge case + acceptance criterion) — developer's fix: timestamped token name (`ai-dev-switchboard-$(date +%s)`), claimed "collision-proof" in `docs/implementation.md` | **Manual, live**, adversarial (rapid back-to-back re-runs, not a single "ran it twice with a pause" check) | **FAIL — see Defect 1** | 5 consecutive runs in a tight loop: runs 1–2 succeeded (crossed a second boundary), runs 3–5 all failed with `Command error: access token name has been used already` because `date +%s` has 1-second resolution and all 3 ran within the same second |
| 14 | Atomic `mkdir` (no `-p`) TOCTOU-closing pattern | **Manual, live** race: two `new-project-from-gitea.sh` invocations targeting the identical `<name>` launched concurrently via `sudo` | pass | one process got `Ready: ...`, the other got `mkdir: cannot create directory ... File exists` / `Already exists: ...` — exactly one winner, no corruption, no silent merge |
| 15 | `GITEA_API_TOKEN` never travels via argv/`ps` | Static check: `app.py`'s `subprocess.run(["sudo", NEW_PROJECT_FROM_GITEA_SCRIPT, owner, repo_name, name], ...)` and the script's own `CONFIG=/etc/.../switchboard.env; source "$CONFIG"` | pass | only `owner`/`repo_name`/`name` appear in the sudo argv; token is read from the sourced 600 file, never passed as an argument |
| 16 | `new-project-from-gitea.sh` output redaction on clone failure (`${CLONE_OUTPUT//$GITEA_API_TOKEN/REDACTED}`) | Automated (`test_clone_failure_leaves_dest_and_never_leaks_token`, `test_redaction_replaces_token_if_git_ever_echoes_it`, `test_wrong_token_rejected_by_server_and_redacted`) + manual (`git clone` against a closed port to force a different failure shape — "Failed to connect") | pass | this git version (2.47.3) strips embedded credentials from every failure message shape tried (404, 401, connection-refused) before the script's own redaction even runs; redaction substitution itself independently verified correct against a fabricated leak. Gitea's own PATs are 40-char lowercase-hex (confirmed on the real minted token), so the bash `${var//pattern/repl}` glob-pattern substitution has no glob-metacharacter edge case to worry about in practice |
| 17 | `NAME`/`OWNER`/`REPO` regex validation actually closes injection into the `su "$RUN_USER" -c "git clone '$CLONE_URL' '$DEST'"` call, including `NAME` containing spaces | Automated (`ArgumentValidationTests`, ✕6) + **live** (created a project named `"my project-1"` through the real privileged script) | pass | `NAME_RE`/owner/repo regexes admit no quote, backtick, `$`, or `;` characters, so no value that passes validation can break out of the single-quoted context; live test with spaces succeeded and produced the correctly-named directory |
| 18 | Retirement completeness: no functional reference to the six deleted scripts/`git-hosting.env` anywhere outside comments/docs; no legacy `git` system user created by `install.sh` | `grep -rn` across `*.sh`/`*.py`; read `install.sh`'s user-creation block | pass | only 3 comment-only matches (explaining what was retired); `install.sh` only ever creates `RUN_USER`/`SVC_USER`, never a `git` system user (that was solely `git-hosting-setup.sh`'s job, now deleted) |
| 19 | `bash -n`/syntax check on all shell scripts + `install.sh`; `app.py` parses | Automated | pass | all clean |

### Round 1 regression check
Full existing suite: `python3 -m unittest discover -s tests -v` — **173/173 pass**, run before and after the live round trip (confirming no leftover state from live testing leaked into the test suite).

### Round 1 defects found

#### Defect 1: `gitea-configure-api.sh`'s "safe to re-run" fix has an unclosed same-second collision window — **closed in round 2, see above**
- **Repro** (exact commands, reproduced independently in this session, not inferred from the developer's account):
  ```
  for i in 1 2 3 4 5; do
    printf 'admin\nai-dev-switchboard-gitea\n' | sudo bash scripts/gitea-configure-api.sh
  done
  ```
  against a real running Gitea + already-existing admin account.
- **Expected**: per `docs/spec.md`'s Edge Cases ("`gitea-configure-api.sh` run a second time — must be safe to re-run... not a special 'already configured' refusal") and Acceptance Criteria, every run should succeed and mint a fresh, distinct token.
- **Actual**: runs 1–2 succeeded (happened to land in different wall-clock seconds); runs 3, 4, and 5 — all issued back-to-back with no artificial delay — failed outright:
  ```
  Failed to generate a token. Output was:
  Command error: access token name has been used already
  ```
  because `TOKEN_NAME="ai-dev-switchboard-$(date +%s)"` has 1-second resolution, and `gitea-configure-api.sh` itself only takes a fraction of a second to run (mint token + write file + restart a stub unit + curl-verify) — well under 1 second on real hardware, meaning two runs issued in quick succession (a human hitting up-arrow+enter twice, a provisioning script that calls this for idempotency, a retry after a transient failure) land in the same second and collide.
- **Why this matters beyond a corner case**: `docs/implementation.md` explicitly claims this fix is "collision-proof" and states it was "Verified live: ran the script twice in a row, both succeeded" — that verification happened to not hit the collision window (there was almost certainly a human-typing-driven gap between runs), so the claim of "collision-proof" in the shipped documentation is not accurate; the actual property delivered is "collision-*resistant*, with a real ~1-second collision window." Given `docs/spec.md`'s rotation use case ("useful if an operator wants to rotate the token") is exactly the kind of thing someone might script or retry quickly, this isn't a purely theoretical edge case — I reproduced it on the very first adversarial (non-manually-paced) attempt.
- **Severity**: must-fix. Directly violates a spec'd edge case / acceptance criterion ("safe to re-run"), is trivially and reliably reproducible, and the failure mode when it does trigger is a raw Gitea CLI error surfaced to the operator's terminal rather than even a clean failure message.
- **Suggested fix shape** (not prescriptive — developer's call): higher-resolution suffix (`date +%s%N` or `date +%s`-plus-random, e.g. `$RANDOM` or a few bytes of `/dev/urandom` hex), or loop-with-retry-on-name-collision, or query existing token names first and pick a guaranteed-unique one.
- **Resolution (round 2)**: developer appended 8 random alphanumeric characters after the timestamp. Independently re-verified in round 2 — see test cases #3–5 above. Closed.

### Round 1 verdict (superseded)
Blocked. Superseded by round 2's Approve above.

### Round 1 doc nits (both fixed, see round 2 test cases #6–7)
- `config/switchboard.env.example` line ~150: the `GITEA_API_TOKEN` comment said "a Gitea Personal Access Token (scope `write:repository`)" — stale relative to the actual, correctly-live-verified scope `write:repository,write:user`.
- `docs/BACKLOG.md`'s "Local git hosting UI + CI/CD (Gitea)" entry read "2b ... are still open" (future tense) — stale since 2b had actually landed.

### What was independently verified as correct in round 1 (reconfirmed unaffected in round 2, not re-litigated from scratch)
- The two deviations from spec the developer reported (token scope requiring `write:user`; token-name-must-be-unique-per-run) are both real, independently reproduced against a real Gitea 1.27.1 instance — not workarounds for some other misconfiguration.
- Token handling end-to-end (never in argv/`ps`, redaction on clone failure, 40-char-hex token format making the bash glob-substitution redaction safe in practice).
- Input validation on the privileged script closes injection into the `su -c "git clone ..."` call, including the space-in-name case.
- The atomic `mkdir` TOCTOU pattern, verified under a real concurrent race.
- Retirement completeness (no functional stale references to the six deleted scripts; no legacy `git` system user created).
- The full live create → clone → push → collision → cleanup round trip.
