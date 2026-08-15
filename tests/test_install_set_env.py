#!/usr/bin/env python3
"""
Tests for install.sh's `set_env()`/`get_env()` helpers (docs/BACKLOG.md item
10, docs/spec.md "Backend hardening — set_env() sed-injection fix + team
run_id path-traversal validation"): the sed-based idempotent-upsert in
`set_env()` interpolates `$val` unescaped into a sed `s///` expression using
`|` as the delimiter -- a value containing a literal `|` breaks the sed
expression and aborts the whole install.sh run on a re-run (the first write,
via `>>`, never goes through sed); a value containing a literal `&` is
silently corrupted via sed's whole-match backreference.

Technique: extracts `set_env`/`get_env` verbatim from install.sh's own
source using the same `_extract_between()`-style harness tests/test_install_
ollama.py establishes, then runs a small driver script through a plain
`subprocess.run` -- no pty/`prompt()` needed since neither helper ever reads
from a terminal (docs/spec.md "Affected areas").

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_set_env.py
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


def _bq(value):
    """Bash single-quotes `value` correctly (unlike Python's repr(), which
    escapes backslashes to match Python string-literal syntax, not bash's --
    a naive repr()-based embed would double every backslash in a test value
    before it ever reaches set_env). Standard 'close quote, escaped literal
    quote, reopen quote' trick for embedding a literal single quote."""
    return "'" + value.replace("'", "'\\''") + "'"


def _extract_set_env_and_get_env():
    """Pulls `set_env()` and `get_env()` verbatim out of install.sh's real
    source, unmodified -- so these tests exercise install.sh's actual
    upsert logic, not a reimplementation that could silently drift from the
    real file."""
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(source, "set_env() {", "random_token() {")


class SetEnvHelperTests(unittest.TestCase):
    """Runs install.sh's real set_env()/get_env() (extracted verbatim, see
    _extract_set_env_and_get_env above) against a scratch env file, proving
    every set_env-related acceptance criterion in docs/spec.md."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-set-env-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env_file = os.path.join(self.tmp, "switchboard.env")
        open(self.env_file, "w").close()
        self.helpers = _extract_set_env_and_get_env()

    def _run(self, script_body, timeout=10):
        """Runs `set -euo pipefail` + the extracted helpers + script_body as
        a standalone bash script -- a non-zero exit here means the block
        itself aborted (the exact defect this cycle fixes for a `|`-bearing
        value on a re-run)."""
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            {self.helpers}
            {script_body}
            """)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=timeout)

    def _set_then_get(self, value, key="MYKEY", second_value=None):
        """Upserts `value` for `key` twice (first write via the `>>` append
        path, second via the sed-upsert path -- the second call is the one
        that previously broke) and returns (returncode, stdout-from-get_env,
        combined stderr)."""
        second = second_value if second_value is not None else value
        script = textwrap.dedent(f"""\
            set_env {_bq(self.env_file)} {key} {_bq(value)}
            set_env {_bq(self.env_file)} {key} {_bq(second)}
            get_env {_bq(self.env_file)} {key}
            """)
        r = self._run(script)
        return r.returncode, r.stdout.rstrip("\n"), r.stderr

    # ── the specific defect: `|` aborts a re-run upsert ─────────────────

    def test_pipe_in_value_does_not_abort_reupsert_and_round_trips(self):
        value = "a|b|c"
        rc, got, stderr = self._set_then_get(value)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(got, value)

    # ── the specific defect: `&` silently corrupts via backreference ────

    def test_ampersand_in_value_round_trips_byte_for_byte(self):
        value = "foo&bar&baz"
        rc, got, stderr = self._set_then_get(value)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(got, value)

    # ── backslash, and combinations/repetitions of all three ────────────

    def test_backslash_in_value_round_trips_byte_for_byte(self):
        value = r"a\b\c"
        rc, got, stderr = self._set_then_get(value)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(got, value)

    def test_combination_of_pipe_ampersand_backslash_round_trips(self):
        value = r"tok|en&with\slash|and&more\\stuff"
        rc, got, stderr = self._set_then_get(value)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(got, value)

    # ── empty value must still upsert cleanly (regression) ──────────────

    def test_empty_value_upserts_cleanly(self):
        rc, got, stderr = self._set_then_get("")
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(got, "")

    # ── plain values with no special characters (regression) ────────────

    def test_plain_value_unchanged_behavior(self):
        value = "example.tailnet.ts.net"
        rc, got, stderr = self._set_then_get(value)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(got, value)

    # ── the first write (>> append path) is untouched by this fix ───────

    def test_first_write_append_path_handles_all_special_chars_already(self):
        # Only ONE set_env call -- the >> append path, never touching sed.
        value = "a|b&c\\d"
        script = textwrap.dedent(f"""\
            set_env {_bq(self.env_file)} MYKEY {_bq(value)}
            get_env {_bq(self.env_file)} MYKEY
            """)
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.rstrip("\n"), value)

    # ── changing an already-`|`-bearing value to a new value on a second
    #    re-run (three set_env calls total) -- proves the upsert genuinely
    #    keeps working across repeated re-runs, not just once ────────────

    def test_value_can_be_changed_again_after_a_pipe_bearing_value(self):
        script = textwrap.dedent(f"""\
            set_env {_bq(self.env_file)} MYKEY {_bq("first|value")}
            set_env {_bq(self.env_file)} MYKEY {_bq("second|value")}
            set_env {_bq(self.env_file)} MYKEY {_bq("third&value")}
            get_env {_bq(self.env_file)} MYKEY
            """)
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.rstrip("\n"), "third&value")
        # No accumulation -- exactly one line for this key.
        with open(self.env_file) as f:
            content = f.read()
        self.assertEqual(content.count("MYKEY="), 1)


if __name__ == "__main__":
    unittest.main()
