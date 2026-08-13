# Test & Review: Local git hosting UI + CI/CD (Gitea) — part 2a: install + container toggle

## Scope
Re-verification pass following the developer's fixes for the two defects
this same role found in the previous pass (Defect 1: printed Gitea
admin-account creation command missing `--user git`; Defect 2: no
automated test coverage for per-kind badge text/class), followed by the
full independent review pass that a "blocked" verdict skipped last time.
Covers all 10 of `docs/spec.md`'s acceptance criteria plus its "Edge
cases" section, against `install.sh`, `app/app.py`,
`config/gitea-docker-compose.yml`, `scripts/gitea-{up,down,status}.sh`,
`tests/test_gitea.py`, `tests/test_singleton_toggle_frontend.js` (replacing
`tests/test_taiga_frontend.js`), and the config/README additions.

This sandbox again had working `sudo` + Docker + network access. Per the
orchestrator's explicit brief, both fixes were re-verified hands-on against
a real, live Gitea container rather than trusted from the developer's own
account, and a fresh sabotage variant (distinct from the one already fixed)
was run against the badge regression test to confirm it catches
related-but-different regressions, not just the exact one already patched.

## Re-verification of the two prior defects

### Defect 1 (must-fix) — `--user git` fix
`install.sh`'s printed command (line 598) now reads:
```
docker exec -it --user git ai-dev-switchboard-gitea gitea admin user create \
    --admin --username <name> --password <password> --email <email>
```
Stood up the real, unmodified `config/gitea-docker-compose.yml` against a
matching hand-authored `.env` (same shape `install.sh` writes), real
`docker.gitea.com/gitea:1.27.1` + `postgres:14` images, waited for "ORM
engine initialization successful", then:
- Re-ran the **pre-fix** command shape (`docker exec ai-dev-switchboard-gitea
  gitea admin user create ...`, no `--user git`) — reproduced the exact same
  `mustNotRunAsRoot()` failure found in the previous pass.
- Ran the **exact fixed** command as printed
  (`docker exec --user git ai-dev-switchboard-gitea gitea admin user create
  --admin --username testadmin --password TestPass1234! --email
  testadmin@example.com`) — succeeded: `New user 'testadmin' has been
  successfully created!` (exit 0).
- Confirmed `curl http://127.0.0.1:3000/user/login` returns `200` with
  `<title>Sign In - Gitea...</title>` (login page, not the public install
  wizard) both before and after creating the admin account, matching AC4.
- Confirmed `docker compose down` actually stops the stack (`docker compose
  ps server --format '{{.State}}'` empty afterward), matching AC5.
- Grepped the rest of the repo for other live copies of this command —
  only `install.sh` (fixed), plus elided/rationale mentions in
  `docs/spec.md`/`docs/implementation.md` that don't reproduce the command
  verbatim. No other live script needs the same fix.

**Confirmed genuinely fixed**, not just claimed fixed.

### Defect 2 (should-fix) — badge regression coverage
`tests/test_singleton_toggle_frontend.js` gained a
`[<kind>] resource badge shows this kind's own text/class while running,
not another kind's` test per kind (15 tests total, up from 13), asserting
both the correct badge class/text for that kind and the *absence* of the
other kind's badge class/text.

Reran the exact original sabotage (`row()`'s
`const cfg = SINGLETON_TOGGLE_CONFIG[kind];` hardcoded to `'taiga'`) —
confirmed only the `[gitea]` badge test fails now, all others pass; reverted.

Then, per the brief's explicit ask for a **fresh, distinct** sabotage variant
(not the one already fixed): temporarily changed `gitea`'s `timeoutMs` from
`90000` to `120000` in `SINGLETON_TOGGLE_CONFIG` (`app/app.py` ~line 1247) —
a realistic "wrong number carried over/typo'd" mistake in the generalized
per-kind config, unrelated to the badge fix. Result:
```
FAIL - [gitea] unexpected stop while running that never recovers still surfaces error after 90s
AssertionError: [gitea] expected "error" after a 90s non-recovering failure, got: ...starting…...
```
Exactly 1 of 15 tests failed, with a precise diagnostic. Reverted; reran the
full suite — 15/15 clean again, `grep -n SABOTAGE app/app.py` → no matches,
`git diff app/app.py` shows only the developer's own shipped diff (no sabotage
residue).

**Confirmed the fix is load-bearing against more than just the exact
regression it was written for** — the generalized `SINGLETON_TOGGLE_CONFIG`
now has real regression coverage across its badge, timeout, error-class, and
spinner-class fields (the `errClass` assertions this sabotage happened to
also depend on are themselves separately exercised at lines 359/400/418/425/
446/485 of the test file, independent of the new badge test).

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | AC1: fresh install configures Gitea (2 services, loopback ports, pre-pulled, left stopped) | Manual: real `docker compose up -d` against the actual authored compose file + a matching `.env`, real Gitea 1.27.1 + Postgres 14 images | PASS | Containers reached `running`; "ORM engine initialization successful" logged; `docker compose ps server --format '{{.State}}'` → `running` while up |
| 2 | AC2: re-running `install.sh --with-git-hosting` doesn't regenerate secrets / restart stopped containers / duplicate sudoers | Code reading (unchanged from prior pass — this code path wasn't touched by either fix) | PASS | `install.sh` `get_env`-gated secret writes, no `up` call in the Gitea block, sudoers file fully regenerated (`> "$SUDOERS"`) every run |
| 3 | AC3: singleton Gitea row appears, off by default, same visual family as Taiga | Automated: `tests/test_singleton_toggle_frontend.js` renders the real `<script>` and asserts row content/classes | PASS | `node tests/test_singleton_toggle_frontend.js` → 15/15 |
| 4 | AC4: toggle-on eventually reports `gitea:true` + working `gitea_url`; opening it reaches a login page, not the public "finish install" wizard | Automated (`tests/test_gitea.py`) **plus** manual: real stack, `curl http://127.0.0.1:3000/user/login` | PASS | Backend: `GiteaEndpointTests` all pass. Live: `curl` → 200, `<title>Sign In - Gitea...</title>`, no install-wizard fields |
| 5 | AC5: toggle-off reports `gitea:false`, containers actually stopped | Automated + manual: `docker compose down` against the live stack | PASS | `docker compose ps server --format '{{.State}}'` empty after `down` |
| 6 | AC6: `app.py`/service restart doesn't lose state | Code reading: `gitea_run("status")` called fresh every `/status` poll, never cached | PASS | `app/app.py` `/status` handler, line ~2380 |
| 7 | AC7: rapid/overlapping toggle-off race class, for both `taiga` and `gitea` | Automated: 15-test suite (6 race scenarios × 2 kinds + 1 badge test × 2 kinds + 1 cross-kind isolation test) | PASS | 15/15; both my sabotage probes (badge lookup, timeoutMs) each caught by exactly the expected single test |
| 8 | AC8: existing git-hosting flow completely unchanged | `git diff`/`git status` — none of `scripts/git-hosting-setup.sh`, `new-repo.sh`, `new-dev-instance.sh`, `new-project.sh`, `project-sync.sh`, `target-setup.sh` modified | PASS | `git status --porcelain` shows only the files listed in "Scope" |
| 9 | AC9: `--with-taiga` + `--with-git-hosting` together — Docker installed once, no port collisions | Code reading: shared `ensure_docker()`, `TAIGA_PORT=9000` vs `GITEA_PORT=3000`/`GITEA_SSH_PORT=2222` | PASS | `install.sh` lines 107-121 (`ensure_docker`), 265-266 (Taiga call site), 458 (Gitea call site) |
| 10 | AC10: TOTP gate applies to `/gitea/{on,off}` with no special-casing | Automated: `test_toggle_on_without_code_returns_428`, `test_toggle_on_with_wrong_code_returns_403`; code placement confirmed after the shared gate | PASS | `python3 -m unittest` (135/135); `do_POST` gate at line 2434, `gitea` branch at line 2458, identical shape to `taiga`'s |
| 11 | Edge case: `ensure_docker()` refactor is a pure refactor for the Taiga call site | Diff read | PASS | `TAIGA_COMPOSE_OK=$DOCKER_COMPOSE_OK`, identical control flow, only the stderr wording generalized (cosmetic) |
| 12 | Edge case: compose file is valid, real YAML | `docker compose config` against the real authored file | PASS | Resolved config printed cleanly, both ports loopback-scoped |
| 13 | Edge case: badge contrast (`#66d9ff`/`#16324a`) correctly reused for Gitea | Independent WCAG relative-luminance recomputation from the literal hex values | PASS | ≈8.136:1 (L_text≈0.5967, L_bg≈0.02949) — well above 4.5:1 AA threshold for text |
| 14 | Edge case: printed admin-account creation command works as documented (Defect 1 fix) | Manual: exact fixed command against a real running container, plus a re-repro of the pre-fix failure | PASS | See "Re-verification of the two prior defects" above |
| 15 | Edge case: per-kind badge text/class has automated coverage (Defect 2 fix) | Sabotage: original probe + one fresh, distinct variant (`gitea`'s `timeoutMs`) | PASS | Both caught cleanly, one test each, precise diagnostics; see above |

## Regression check
Full existing suite: `python3 -m unittest discover -s tests -v` — **135/135 pass**, no regressions.
`node tests/test_singleton_toggle_frontend.js` — **15/15 pass** (13 pre-existing + 2 new badge tests, one per kind).
`bash -n install.sh` — clean.

Both re-verification sabotage probes (the original badge-lookup hardcode
and the fresh `timeoutMs` variant) were applied to `app/app.py`, confirmed to
fail exactly the expected test(s), then reverted; `grep -n SABOTAGE app/app.py`
returns no matches and `git diff app/app.py` shows only the developer's own
shipped diff.

## Defects found
None outstanding. Both defects from the prior pass are confirmed fixed by
hands-on re-verification (see above), not just re-read from the developer's
account.

---

## Spec coverage
All 10 acceptance criteria in `docs/spec.md` are implemented and covered by
either automated tests or a live manual repro performed this session (see
test-case table above — none are "implemented but untested" or "tested by
code-reading alone" for anything with an available live-verification path).
All "Edge cases" bullets are addressed: re-run idempotency (code read, same
idiom already accepted for Taiga/TOTP_SECRET), both-flags-together port/Docker
sharing, Docker/Compose-missing warn-and-continue (reused verbatim via
`ensure_docker()`), app.py-restart state survival, the toggle race class
(re-verified for both kinds plus two independent sabotage probes), the
old-sshd/Gitea-SSH port coexistence (different ports, no code path shared),
fixed-port-collision (documented, accepted limitation, same as `TAIGA_PORT`),
and the pre-admin-account "no wizard exposed" window (`INSTALL_LOCK=true`
confirmed live via `curl`).

## Findings (most severe first)
No must-fix or should-fix findings from this review pass.

### Nit: `docs/implementation.md`'s "Known limitations" bullet repeats the fixed command verbatim, now correctly
Already corrected by the developer (per their own "Fixes from review" section)
to include `--user git` — confirmed current text at line ~268 matches the
fixed command. No action needed; noting only because this is exactly the
kind of place a fix like this could have been missed on a second copy and
wasn't.

## Follow-ups (non-blocking)
- `docs/spec.md` Open Question 3 (whether to eventually automate Gitea's
  admin-account creation, unlike Taiga's) is explicitly left for a future
  cycle by design — not a gap in 2a.
- `docs/spec.md` Open Questions 1 (Postgres vs. SQLite) and 6 (whether an
  on/off toggle is the right long-term UX for Gitea) are both explicitly
  flagged by the spec itself for later cycles, not 2a's job to resolve.

## Overall verdict
**Approve.** Both defects from the previous pass are confirmed fixed by
hands-on re-verification against a real, live Gitea container (not just
re-read from the developer's account): the admin-account creation command
now genuinely succeeds, and the badge regression test is confirmed
load-bearing against both the original sabotage and a fresh, distinct
variant. The full independent review pass (spec coverage, correctness,
security, simplicity) that the prior "blocked" verdict skipped found no
must-fix or should-fix issues — `install.sh`'s Docker/Compose refactor,
`INSTALL_LOCK`/loopback-binding handling, and the frontend state-machine
generalization all hold up against the current code exactly as found in the
prior pass's positive findings. This build cycle is done; hand back to
product-manager for the next iteration.
