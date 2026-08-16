#!/usr/bin/env python3
"""
Tests for install.sh's AUTH_MODE prompt default (docs/BACKLOG.md item 39,
docs/spec.md "Item 39 — AUTH_MODE ignored under --yes"): `prompt()` always
returns its literal default argument whenever stdin isn't a TTY or `--yes`
is set (see `interactive()`), so the AUTH_MODE prompt needs to seed that
default from any pre-existing `switchboard.env` value first, exactly like
`RUN_USER_DEFAULT`/`SVC_USER_DEFAULT` already do two lines above it -- ct/
create.sh's automated provisioning path pre-seeds AUTH_MODE=pve before
calling `install.sh --yes` and expects it to survive.

Technique: extracts `prompt()`/`interactive()`/`get_env()`/`set_env()` plus
the AUTH_MODE prompt block verbatim out of install.sh's real source, using
the same `_extract_between()`-style harness tests/test_install_set_env.py
already establishes, then runs a small driver script through a plain
`subprocess.run` with YES=1 (mirrors `install.sh --yes`, i.e. non-
interactive) -- no pty needed since `interactive()` is false whenever YES=1.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_auth_mode_default.py
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
    """Pulls the prompt/env helpers and the real AUTH_MODE prompt block
    verbatim out of install.sh's own source, unmodified -- so this test
    exercises the actual shipped logic, not a reimplementation that could
    silently drift from the real file."""
    with open(INSTALL_SH) as f:
        source = f.read()
    helpers = _extract_between(source, "interactive() {", "random_token() {")
    auth_block = _extract_between(
        source, "AUTH_MODE_DEFAULT=",
        'set_env "$ENV_FILE" AUTH_MODE "$AUTH_MODE"\n')
    auth_block += 'set_env "$ENV_FILE" AUTH_MODE "$AUTH_MODE"\n'
    return helpers, auth_block


class AuthModeDefaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-auth-mode-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env_file = os.path.join(self.tmp, "switchboard.env")
        open(self.env_file, "w").close()
        self.helpers, self.auth_block = _extract_snippets()

    def _run(self, pre_seed=None, timeout=10):
        """Runs the extracted AUTH_MODE prompt block non-interactively
        (YES=1, mirroring `install.sh --yes`) against a scratch env file,
        optionally pre-seeded with an AUTH_MODE value first -- exactly the
        scenario ct/create.sh's own automated provisioning path relies on.
        Prints the resulting AUTH_MODE and the env file's own persisted
        value so both halves of the fix (default-seeding AND persistence)
        are checked in one pass."""
        seed = f'set_env {self.env_file!r} AUTH_MODE {pre_seed!r}\n' if pre_seed else ""
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            YES=1
            ENV_FILE={self.env_file!r}
            {self.helpers}
            {seed}
            {self.auth_block}
            echo "AUTH_MODE=$AUTH_MODE"
            echo "PERSISTED=$(get_env "$ENV_FILE" AUTH_MODE)"
            """)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=timeout)

    def test_preseeded_pve_survives_yes_install(self):
        r = self._run(pre_seed="pve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AUTH_MODE=pve", r.stdout)
        self.assertIn("PERSISTED=pve", r.stdout)

    def test_no_preseed_still_defaults_to_simple(self):
        r = self._run(pre_seed=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AUTH_MODE=simple", r.stdout)
        self.assertIn("PERSISTED=simple", r.stdout)


if __name__ == "__main__":
    unittest.main()
