#!/usr/bin/env python3
"""
Tests for scripts/taiga-up.sh's retry/failure logic (docs/BACKLOG.md item
30, docs/spec.md "taiga-up.sh resilience from Proxmox E2E test round 4"): a
real, reproduced Docker Compose port-bind race can leave taiga-gateway --
the stack's only public entrypoint -- created but never network-attached,
and the script previously had no way to detect this or retry, silently and
indefinitely leaving the feature "off". The fix retries a bounded number of
times (removing just the wedged container between attempts, via the same
`docker compose ps taiga-gateway --format '{{.State}}'` detection idiom
scripts/taiga-status.sh already uses) before failing loudly.

Technique: runs the real scripts/taiga-up.sh (unmodified, read verbatim
from disk -- not a reimplementation that could silently drift from the
real file) as a bash subprocess with `docker` stubbed as a shell function
that reports taiga-gateway's state as "running" starting on a configurable
Nth `up -d` call -- same fake-command-on-PATH-via-shell-function technique
tests/test_create_enumerate_bridges.py already established for exercising
real shell-script logic without a real Docker daemon. `sleep` is also
stubbed to a no-op so the exhaustion case doesn't pay the real 2s
inter-attempt delay.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_taiga_up_retry.py
"""
import os
import subprocess
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
TAIGA_UP_SH = os.path.join(REPO_ROOT, "scripts", "taiga-up.sh")


class TaigaUpRetryTests(unittest.TestCase):
    """Runs the real scripts/taiga-up.sh (read verbatim from disk, see
    module docstring) against a stubbed `docker` command, proving
    docs/spec.md's item-30 acceptance criteria: succeeds immediately with
    no retry on the common case, retries with a targeted `rm -f
    taiga-gateway` when the gateway doesn't come up, and fails loudly with
    a specific stderr message once TAIGA_UP_MAX_ATTEMPTS is exhausted."""

    def setUp(self):
        with open(TAIGA_UP_SH) as f:
            self.script_source = f.read()
        self.tmpdir = tempfile.mkdtemp(prefix="switchboard-taiga-up-test-")
        self.addCleanup(self._rmtree)

    def _rmtree(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, success_at, max_attempts=3, timeout=10):
        """Runs the real taiga-up.sh with `docker` stubbed as a shell
        function (shell functions take priority over PATH executables, so
        no real `docker` binary is needed). The stub tracks how many times
        `docker compose ... up -d` has been called (via a counter file, so
        the count survives the command-substitution subshell `ps`/`state=$(
        ...)` forks into) and reports taiga-gateway as "running" only once
        that count reaches `success_at`; `docker compose ... rm -f
        taiga-gateway` calls are logged to a separate file for assertion.
        `sleep` is stubbed to a no-op to keep the exhaustion case fast.
        `TAIGA_DIR` points at an empty temp directory (never inspected by
        the stub -- only `cd` needs it to exist)."""
        counter_file = os.path.join(self.tmpdir, "up-calls")
        rm_log = os.path.join(self.tmpdir, "rm-calls")
        taiga_dir = os.path.join(self.tmpdir, "taiga-dir")
        os.makedirs(taiga_dir, exist_ok=True)

        wrapper = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            COUNTER_FILE={counter_file!r}
            RM_LOG={rm_log!r}
            SUCCESS_AT={success_at}
            docker() {{
                case "$*" in
                    *"up -d"*)
                        n=$(( $(cat "$COUNTER_FILE" 2>/dev/null || echo 0) + 1 ))
                        echo "$n" > "$COUNTER_FILE"
                        ;;
                    *"ps taiga-gateway"*)
                        n=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
                        if [ "$n" -ge "$SUCCESS_AT" ]; then
                            echo "running"
                        else
                            echo "created"
                        fi
                        ;;
                    *"rm -f taiga-gateway"*)
                        echo called >> "$RM_LOG"
                        ;;
                esac
            }}
            sleep() {{ :; }}
            {self.script_source}
            """)
        env = dict(os.environ)
        env["TAIGA_DIR"] = taiga_dir
        env["TAIGA_UP_MAX_ATTEMPTS"] = str(max_attempts)
        result = subprocess.run(
            ["bash", "-c", wrapper], capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        up_calls = 0
        if os.path.exists(counter_file):
            with open(counter_file) as f:
                up_calls = int(f.read().strip() or 0)
        rm_calls = 0
        if os.path.exists(rm_log):
            with open(rm_log) as f:
                rm_calls = len(f.read().splitlines())
        return result, up_calls, rm_calls

    # ── common case: running on the first attempt -> exit 0, no retry ───

    def test_succeeds_on_first_attempt_no_retry(self):
        result, up_calls, rm_calls = self._run(success_at=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(up_calls, 1)
        self.assertEqual(rm_calls, 0)
        self.assertNotIn("didn't come up cleanly", result.stderr)

    # ── recovers on the second attempt after one targeted rm+retry ──────

    def test_succeeds_on_second_attempt_after_one_retry(self):
        result, up_calls, rm_calls = self._run(success_at=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(up_calls, 2)
        self.assertEqual(rm_calls, 1)
        self.assertIn(
            "taiga-up: taiga-gateway didn't come up cleanly", result.stderr
        )
        self.assertIn("attempt 1/3", result.stderr)

    # ── exhausts all attempts -> non-zero exit, specific stderr message ─

    def test_exhausts_all_attempts_and_fails_loudly(self):
        result, up_calls, rm_calls = self._run(success_at=99, max_attempts=3)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(up_calls, 3)
        # rm -f is only run *between* attempts, never after the last one.
        self.assertEqual(rm_calls, 2)
        self.assertIn(
            "taiga-up: taiga-gateway failed to come up after 3 attempts",
            result.stderr,
        )
        self.assertIn("manual intervention needed", result.stderr)
        self.assertIn("docker compose logs taiga-gateway", result.stderr)
        self.assertIn("docker network ls", result.stderr)
        self.assertIn("disk space", result.stderr)

    # ── TAIGA_UP_MAX_ATTEMPTS is honored when overridden ────────────────

    def test_max_attempts_env_override_is_honored(self):
        result, up_calls, rm_calls = self._run(success_at=99, max_attempts=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(up_calls, 5)
        self.assertEqual(rm_calls, 4)
        self.assertIn("after 5 attempts", result.stderr)


if __name__ == "__main__":
    unittest.main()
