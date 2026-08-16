#!/usr/bin/env python3
"""
Tests for app.py's CODE_SERVER_BIN default (docs/BACKLOG.md item 41,
docs/spec.md "Item 41 -- code-server hardcoded path"): install.sh now
resolves and persists the real code-server binary path into
CODE_SERVER_BIN via switchboard.env for a fresh/re-run install, but an
existing install upgrading its app.py code without re-running install.sh
would still see no CODE_SERVER_BIN in its process environment at all --
app.py's own default must resolve via shutil.which("code-server") first
(self-healing that case on a plain service restart), falling back to the
pre-existing /usr/local/bin/code-server literal only when code-server
isn't on PATH either.

Technique: since CODE_SERVER_BIN is a module-level global computed once at
import time, each case needs app.py imported fresh in its own process with
a controlled PATH -- same "spawn python3 -c ... in a subprocess with a
custom env" technique tests/test_team_routes.py's own service-restart-
simulation section already establishes, rather than trying to force a
re-import within this same test process.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_code_server_bin_default.py
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")

_IMPORT_SCRIPT = textwrap.dedent(f"""\
    import sys
    sys.path.insert(0, {APP_DIR!r})
    import app as appmod
    sys.stdout.write(appmod.CODE_SERVER_BIN)
    """)


def _base_env(path_dirs):
    env = dict(os.environ)
    env["TOTP_SECRET"] = "JBSWY3DPEHPK3PXP"
    env["AUTH_MODE"] = "simple"
    env["SIMPLE_USERNAME"] = "testuser"
    env["SIMPLE_PASSWORD"] = "testpass"
    env.pop("CODE_SERVER_BIN", None)
    env["PATH"] = os.pathsep.join(path_dirs)
    return env


class CodeServerBinDefaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-code-server-bin-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _make_fake_binary(self, name="code-server"):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def test_resolves_via_shutil_which_when_env_var_unset_and_on_path(self):
        fake = self._make_fake_binary()
        env = _base_env([self.tmp])
        r = subprocess.run([sys.executable, "-c", _IMPORT_SCRIPT],
                           capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, fake)

    def test_falls_back_to_literal_default_when_not_on_path_and_env_unset(self):
        env = _base_env([])  # empty PATH -- nothing resolvable
        r = subprocess.run([sys.executable, "-c", _IMPORT_SCRIPT],
                           capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "/usr/local/bin/code-server")

    def test_explicit_env_var_still_wins_over_shutil_which(self):
        self._make_fake_binary()  # on PATH, but should be ignored
        env = _base_env([self.tmp])
        env["CODE_SERVER_BIN"] = "/some/explicit/path/code-server"
        r = subprocess.run([sys.executable, "-c", _IMPORT_SCRIPT],
                           capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "/some/explicit/path/code-server")


if __name__ == "__main__":
    unittest.main()
