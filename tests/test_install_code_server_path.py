#!/usr/bin/env python3
"""
Tests for install.sh's code-server binary path resolution (docs/BACKLOG.md
item 41, docs/spec.md "Item 41 -- code-server hardcoded path"): a fresh
Debian 12 box has code-server.dev's installer put the real binary at
/usr/bin/code-server, not /usr/local/bin/code-server -- a hardcoded
/usr/local/bin/ path made the WITH_CODE_SERVER idempotency check
re-trigger the install every run, and (via app.py's CODE_SERVER_BIN
default) made the toggle silently no-op. The fix resolves the real path
once via `command -v code-server` and keeps the idempotency check, the
persisted CODE_SERVER_BIN env var, and the sudoers rule all in sync with
that one resolved value.

Technique: extracts `set_env`/`get_env` plus install.sh's real
WITH_CODE_SERVER block and its sudoers `CODE_SERVER_BIN` line verbatim out
of install.sh's own source (same `_extract_between()`-style harness
tests/test_install_set_env.py already establishes), then runs them as a
standalone bash script with `command`/`curl` stubbed so no real network
call or PATH lookup against this test host's own environment is needed.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_code_server_path.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_snippets():
    with open(INSTALL_SH) as f:
        source = f.read()
    helpers = _extract_between(source, "set_env() {", "random_token() {")
    resolve_block = _extract_between(
        source, "# Item 41 (docs/BACKLOG.md):",
        'echo "-- Users --"')
    sudoers_line = _extract_between(
        source,
        'echo "$SVC_USER ALL=($RUN_USER) NOPASSWD: $CODE_SERVER_BIN *"',
        "\n").strip()
    return helpers, resolve_block, sudoers_line


class CodeServerPathResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-code-server-path-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env_file = os.path.join(self.tmp, "switchboard.env")
        open(self.env_file, "w").close()
        self.helpers, self.resolve_block, self.sudoers_line = _extract_snippets()

    def _run(self, code_server_on_path, with_code_server=1, timeout=10):
        """Runs the real extracted WITH_CODE_SERVER block + sudoers line
        against a scratch env file. `command` is stubbed so `command -v
        code-server` reports a fake path only when `code_server_on_path` is
        given (never touching this test host's own real PATH); `curl` is
        stubbed to just log that it was invoked (proving/disproving the
        idempotency check re-triggered an install) instead of hitting the
        real network."""
        curl_log = os.path.join(self.tmp, "curl-calls")
        fake_path = code_server_on_path or ""
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            ENV_FILE={self.env_file!r}
            WITH_CODE_SERVER={with_code_server}
            CURL_LOG={curl_log!r}
            FAKE_CODE_SERVER_PATH={fake_path!r}
            SVC_USER=switchboard-svc
            RUN_USER=dev
            command() {{
                if [ "$1" = "-v" ] && [ "$2" = "code-server" ]; then
                    if [ -n "$FAKE_CODE_SERVER_PATH" ]; then
                        echo "$FAKE_CODE_SERVER_PATH"
                        return 0
                    fi
                    return 1
                fi
                builtin command "$@"
            }}
            curl() {{ echo called >> "$CURL_LOG"; }}
            {self.helpers}
            {self.resolve_block}
            {self.sudoers_line}
            echo "RESOLVED=$CODE_SERVER_BIN"
            echo "PERSISTED=$(get_env "$ENV_FILE" CODE_SERVER_BIN)"
            """)
        result = subprocess.run(["bash", "-c", script], capture_output=True,
                                text=True, timeout=timeout)
        curl_calls = 0
        if os.path.exists(curl_log):
            with open(curl_log) as f:
                curl_calls = len(f.read().splitlines())
        return result, curl_calls

    def test_already_installed_at_nonstandard_path_skips_reinstall(self):
        # The exact real-world repro: binary lives at /usr/bin/code-server,
        # not /usr/local/bin/ -- idempotency check must find it via `command
        # -v` and not re-run the installer.
        result, curl_calls = self._run(code_server_on_path="/usr/bin/code-server")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(curl_calls, 0)
        self.assertIn("RESOLVED=/usr/bin/code-server", result.stdout)
        self.assertIn("PERSISTED=/usr/bin/code-server", result.stdout)

    def test_not_on_path_triggers_install_and_falls_back_to_default(self):
        # No code-server anywhere on PATH -- must still proceed to install
        # (not treat "not found" as an error), and the resolved value must
        # fall back to the pre-existing /usr/local/bin/ literal for a case
        # where the freshly-run installer isn't actually stubbed to create
        # a real binary in this test.
        result, curl_calls = self._run(code_server_on_path=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(curl_calls, 1)
        self.assertIn("RESOLVED=/usr/local/bin/code-server", result.stdout)
        self.assertIn("PERSISTED=/usr/local/bin/code-server", result.stdout)

    def test_sudoers_rule_matches_resolved_path_exactly(self):
        # The sudoers rule and CODE_SERVER_BIN must always agree on the
        # exact same path (docs/spec.md Edge cases item 41) -- a mismatch
        # would trade "silent no-op" for "permission denied", still broken.
        result, _curl_calls = self._run(code_server_on_path="/usr/bin/code-server")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "ALL=(dev) NOPASSWD: /usr/bin/code-server *", result.stdout)

    def test_with_code_server_disabled_still_resolves_and_persists_default(self):
        # WITH_CODE_SERVER=0 must never trigger an install, but
        # CODE_SERVER_BIN still gets resolved/persisted (falls back to the
        # literal default when nothing's on PATH) so app.py's own env
        # lookup always has a value to read, same today-vs-tomorrow
        # consistency the spec's Open questions section discusses.
        result, curl_calls = self._run(code_server_on_path=None, with_code_server=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(curl_calls, 0)
        self.assertIn("RESOLVED=/usr/local/bin/code-server", result.stdout)


if __name__ == "__main__":
    unittest.main()
