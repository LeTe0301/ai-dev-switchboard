# Test & Review: E2E round 8 — item 43's real fix (lazy nginx upstream resolution) + honest taiga-up.sh fallback reporting

## Scope
Verifies docs/spec.md's acceptance criteria against the actual uncommitted diff on `backlog/e2e-fixes-round6`: `install.sh`'s new `docker-compose.override.taiga-gateway.conf` heredoc (lazy DNS resolution nginx conf) + extended `docker-compose.override.yml` `volumes:` entry, and `scripts/taiga-up.sh`'s fallback settle-and-recheck fix, plus two new tests in `tests/test_taiga_up_retry.py`. Special focus per the dispatch brief: independently verify (not just trust) the live DNS-race repro and the Compose `volumes:`-merge-by-target-path claim, and confirm this genuinely honors the item-30 "never patch the pinned checkout" constraint.

## Test cases

| # | Criterion / case | Method | Result | Evidence |
|---|---|---|---|---|
| 1 | AC1: `$TAIGA_DIR/taiga-gateway/taiga.conf` (pinned checkout's own file) byte-for-byte unchanged after install.sh's Taiga section runs | automated static extraction — extracted install.sh's real heredoc-writing lines (504-609) verbatim into a synthetic `$TAIGA_DIR`, ran them, diffed the placeholder base file before/after | pass | `diff <(echo PLACEHOLDER-DO-NOT-TOUCH) "$SCRATCH/taiga-gateway/taiga.conf"` → no diff. Also confirmed via `grep -n 'TAIGA_DIR/taiga-gateway/taiga.conf' install.sh` → zero matches; install.sh never opens that path for writing anywhere |
| 2 | AC2: `docker-compose.override.taiga-gateway.conf` exists, contains `resolver 127.0.0.11 valid=10s;`, and all 4 previously-bare-hostname `proxy_pass` locations now use `set $upstream_...` | same harness | pass | `grep -n 'resolver\|upstream_'` output shows the resolver line + 4 `set $upstream_x`/`proxy_pass http://$upstream_x` pairs for `/`, `/api/`, `/admin/`, `/media/`, `/events` |
| 3 | AC3: `docker-compose.override.taiga-gateway.conf` content byte-identical across two consecutive install.sh runs | same harness, re-ran heredoc extraction a second time against the same synthetic `$TAIGA_DIR` | pass | `diff run1.conf run2.conf` and `diff run1.yml run2.yml` both empty |
| 4 | AC4: `docker compose config`'s `taiga-gateway` mount source is `docker-compose.override.taiga-gateway.conf` at target `/etc/nginx/conf.d/default.conf`, and `taiga-static-data`/`taiga-media-data` mounts unchanged | real `docker compose -f docker-compose.yml -f docker-compose.override.yml config` (Compose v5.4.0) against a synthetic base file built from the real upstream `docker-compose.yml`'s `taiga-gateway:` block (fetched live from GitHub, see case 9) | pass | resolved config's `volumes:` list shows exactly 3 entries: bind `.../docker-compose.override.taiga-gateway.conf` → `/etc/nginx/conf.d/default.conf`, volume `taiga-static-data` → `/taiga/static`, volume `taiga-media-data` → `/taiga/media` — the override replaced only the target it named |
| 5 | AC5 (E2E-scoped per spec, not unit-testable): real DNS race — nginx starts successfully under the fixed conf when the upstream isn't registered yet | live Docker repro, independently reproduced (not trusting the implementation report) — real `nginx:1.19-alpine`, throwaway network, no `taiga-front` container present | pass | Original bare-hostname conf: `docker inspect` → `exited`/`ExitCode: 1`, logs show `nginx: [emerg] host not found in upstream "taiga-front" in /etc/nginx/conf.d/default.conf:7` — exact reported bug reproduced. Fixed lazy-resolver conf under the identical condition: `docker inspect` → `running`/`ExitCode: 0`. After a stand-in `taiga-front` joined the network, `wget` through the fixed gateway returned a real response (proxying works end-to-end, not just "doesn't crash") |
| 6 | AC6: fallback's `up -d` reports `running` but dies within the settle window → script exits 1, not 0 | automated, `tests/test_taiga_up_retry.py::test_fallback_settle_window_recheck_catches_gateway_that_dies_before_settling` | pass | ran green; reverted `scripts/taiga-up.sh` only (`git stash`) and re-ran the same test → fails (`AssertionError: 0 != 1`), confirming the test genuinely exercises the fix, not a vacuous assertion |
| 7 | AC7: fallback reports `running` and stays running through the settle window → still exits 0 (no regression) | automated, `tests/test_taiga_up_retry.py::test_fallback_settle_window_recheck_still_honors_genuine_success` | pass | ran green both before and after the `taiga-up.sh` revert (correctly unaffected either way) |
| 8 | AC8: `python3 -m unittest discover -s tests` still passes in full | automated, ran twice | pass | Run 1: 1266 tests, 4 failures (3 pre-existing `test_teams_grounding.py`, unrelated to this diff, plus 1 flaky `test_deploy_target.py` case). Run 2 (clean re-run, no code changes): 1266 tests, exactly the 3 pre-existing `test_teams_grounding.py` failures, 1 skip — matches implementation.md's reported baseline exactly. The 4th failure did not reproduce on rerun, and passed both standalone and as part of its own module both before and after the diff (see "Regression check") — confirmed flaky/order-dependent, not a regression |
| 9 | Open Question #1: upstream `taiga-gateway/taiga.conf` content used in the heredoc matches the real `taigaio/taiga-docker` `stable` branch, and the diff is exactly the two documented categories of change | independent live fetch — `curl` the real file from `raw.githubusercontent.com/taigaio/taiga-docker/stable/taiga-gateway/taiga.conf` (network was available in this sandbox, unlike the developer's), `diff` against the generated conf | pass, Open Question #1 fully closed | `diff` shows exactly: the added `resolver 127.0.0.11 valid=10s;` line, and the 4 `set $upstream_x`/`proxy_pass` rewrites — nothing else differs. Also fetched the real `docker-compose.yml`'s `taiga-gateway:` block and confirmed the base mount path (`./taiga-gateway/taiga.conf:/etc/nginx/conf.d/default.conf`) matches what the override targets |
| 10 | shell syntax / lint | `bash -n install.sh`, `bash -n scripts/taiga-up.sh`, `shellcheck scripts/taiga-up.sh`, `shellcheck install.sh` diffed before/after the change | pass | both `bash -n` clean; `shellcheck taiga-up.sh` zero findings; `shellcheck install.sh` before/after diff shows only a line-number shift for one pre-existing unrelated warning — no new findings introduced |
| 11 | Item-30 constraint: pinned checkout's own git-tracked files never opened for writing | code read of full diff + case 1's extraction | pass | Confirmed no `cat >`/`>`/`sed -i` etc. targets anything under `$TAIGA_DIR/taiga-gateway/` or `$TAIGA_DIR/docker-compose.yml` (the pinned checkout's own files) anywhere in the diff — only the two repo-owned override files are written |

## Regression check
Full existing suite run twice: `python3 -m unittest discover -s tests` — 1266 tests both times. First run: 4 failures (3 pre-existing + 1 apparent new one). Second clean run: 3 failures, matching the pre-existing baseline exactly.

Investigated the apparent 4th failure (`test_deploy_target.InstallScriptDeployTargetBlockTests.test_blank_pubkey_leaves_authorized_keys_untouched_prints_instructions`, unrelated `--with-deploy-target` area, not touched by this diff) via `systematic-debugging`:
- Ran it standalone on the diff'd tree → passes.
- Ran it standalone on the pre-diff tree (`git stash` of the 3 changed files) → passes.
- Ran its whole module (`test_deploy_target.py`, 32 tests) on the diff'd tree → all 32 pass.
- Re-ran the full suite a second time with no changes → the failure did not reproduce; only the 3 known-pre-existing `test_teams_grounding.py` failures remained.
- The test's own `run_block()` extracts install.sh content by marker string (`# ── Optional: deploy-target receiver` ... `# Guarded restart`), not line number, and this diff's insertion (lines 470-611, inside the Taiga section) sits entirely outside both that range and the `interactive() {` / `random_token() {` helper range the harness also extracts — the diff cannot structurally affect this test's harness.

Conclusion: pre-existing test-order/state flake in the full-suite run (most likely cross-test leakage around the real `deploy` system user this test class provisions), not a regression introduced by this change. Confirmed no new failures caused by this diff.

## Spec coverage
| Acceptance criterion (docs/spec.md) | Implemented | Tested |
|---|---|---|
| AC1 — pinned `taiga.conf` byte-for-byte unchanged | yes | yes (case 1) |
| AC2 — override conf has resolver + 4 `set $upstream_...` rewrites | yes | yes (case 2) |
| AC3 — deterministic regeneration across runs | yes | yes (case 3) |
| AC4 — `docker compose config` mount source + preserved static/media mounts | yes | yes (case 4) |
| AC5 — real DNS race: nginx starts successfully under the fix (E2E-scoped per spec) | yes | yes, at the isolated nginx-mechanism level (case 5); full live Taiga-stack E2E still explicitly deferred to the next real retest round, exactly as spec's own wording scopes it |
| AC6 — fallback settle-recheck catches die-before-settle, exits 1 | yes | yes (case 6, with revert-and-fail confirmation) |
| AC7 — fallback still honors genuine success, exits 0 | yes | yes (case 7) |
| AC8 — full suite still passes | yes | yes (case 8) |

No gaps. Both open questions in docs/spec.md (#1: upstream conf content fidelity, #2: Compose volumes-merge-by-target-path behavior) are fully closed by this pass's independent verification (cases 4 and 9) — this sandbox had network access the developer's apparently didn't, closing a gap the implementation report explicitly flagged as unresolved.

## Findings (most severe first)

None must-fix or should-fix. The diff is a faithful, verified implementation of docs/spec.md's proposed approach with no deviations.

### 1. No lightweight (non-Docker) automated regression test for AC1-AC3's heredoc content — nit / optional follow-up
- File: `tests/test_taiga_up_retry.py` (no new file), spec's own "Tests" section (docs/spec.md line ~197) explicitly scoped this out
- Issue: AC1-AC3 (base file untouched, resolver + `set $upstream_x` rewrites present, deterministic regeneration) are pure text/heredoc-extraction assertions that need no live Docker daemon — the same class of check `tests/test_deploy_target.py`'s `_build_deploy_target_block_harness()` already does for install.sh's `--with-deploy-target` block (extract-by-marker-string, run in isolation, assert on output). Right now these three ACs are only verified manually (by this review pass and by the implementation's own "How to verify locally" script), with no automated regression guard if a future change to this install.sh section silently breaks them.
- Failure scenario: a later, unrelated install.sh edit accidentally reintroduces a bare hostname into one of the 4 `proxy_pass` locations, or breaks the heredoc's determinism (e.g. accidentally interpolating `$TAIGA_PORT` unintentionally). Nothing in `python3 -m unittest discover -s tests` would catch it; it would only surface on the next hands-on E2E retest round or manual review.
- Not blocking: docs/spec.md's own "Tests" section explicitly declined to require this (conflating it with the DNS-behavior-needs-live-Docker non-goal, which is a separate and correctly-scoped exclusion). Worth a follow-up ticket, not a reason to send this back.

## Follow-ups (non-blocking)
- Consider a lightweight unit test (no Docker needed) asserting AC1-AC3 automatically, using the existing `test_deploy_target.py` extract-and-assert technique, as durable regression protection for this install.sh section (see Finding 1).
- Per docs/spec.md Open Question #1's own instruction, this still needs a hands-on diff against the actual `$TAIGA_DIR` on whatever host round-8's live retest runs against, in case that host's pinned checkout predates the `stable` branch content fetched here today (2026-08-16) — low residual risk (this reviewer independently confirmed the fetched content is current), but the spec's own caveat about a specific already-installed host's pin still applies literally.
- AC5's full live-Taiga-stack confirmation (real `taiga-front`/`taiga-back`/`taiga-events`/`taiga-protected` containers, real `install.sh` run, real startup ordering) is explicitly out of this repo's testable scope per spec — carry forward to the next E2E retest round as planned.

## Overall verdict
Approve
