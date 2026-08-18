#!/usr/bin/env python3
"""
Regression tests for backlog item 52: `install.sh --update` fast-forwards
`$REPO_DIR` -- which *is* the running script -- as its first step, but bash
keeps reading the pre-merge inode it already has open. So every step below
the update block ran the OLD logic against the NEW checkout: the pull landed
on disk, the run that pulled it wrote pre-update output, and the whole thing
exited 0 looking successful.

Observed live on CT110 (2026-08-18) while landing the items 47/48 follow-up:
a `--update` run printed `Pulled a5c84df (main).` and `$REPO_DIR/install.sh`
genuinely contained the new config-writing logic afterwards -- yet the
`$TAIGA_DIR/.env` and taiga-gateway nginx conf written during that same run
were still the pre-fix versions. A second plain run produced the corrected
output. Fixed by re-exec'ing at the newly-pulled revision.

Two layers, matching this repo's own tool-availability-gated pattern
(`tests/test_gitea_sync_project.py`, `test_install_taiga_gateway_root_location.py`):

1. `ReexecBlockTests` -- always-run static checks on the real update block
   extracted verbatim from `install.sh`. Catches an accidental revert, and
   pins both loop guards.
2. `ReexecBehaviourTests` (`@unittest.skipUnless(HAVE_GIT, ...)`) -- takes
   the REAL update block out of `install.sh`, drops it into a minimal
   harness inside a scratch git repo, and checks that a genuine
   fast-forward hands control to the pulled version. A static check alone
   cannot catch this class of bug -- which is exactly why it shipped.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_update_reexec.py
"""
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL_SH = os.path.join(os.path.dirname(HERE), "install.sh")
HAVE_GIT = shutil.which("git") is not None


def _install_sh() -> str:
    with open(INSTALL_SH, encoding="utf-8") as fh:
        return fh.read()


def _extract_update_block(text: str) -> str:
    """The real `if [ "$UPDATE" -eq 1 ]; then ... fi` block, verbatim."""
    start = text.index('if [ "$UPDATE" -eq 1 ]; then')
    # First line that is exactly `fi` at column 0 closes the block.
    end = text.index("\nfi\n", start) + len("\nfi\n")
    return text[start:end]


class ReexecBlockTests(unittest.TestCase):
    def setUp(self):
        self.block = _extract_update_block(_install_sh())

    def test_reexecs_after_the_fast_forward(self):
        self.assertIn('exec bash "$UPDATE_SELF" "$@"', self.block,
                      "--update must hand the rest of the run to the "
                      "revision it just pulled; without this bash keeps "
                      "executing the pre-merge inode (item 52).")

    def test_reexec_targets_the_running_file_not_a_hardcoded_name(self):
        # The update block is also exercised standalone via `bash -c` (see
        # tests/test_install_update.py), where no script file exists. An
        # earlier version hardcoded "$REPO_DIR/install.sh" and aborted that
        # whole path with exit 127 (exec: no such file).
        self.assertIn('UPDATE_SELF="${BASH_SOURCE[0]:-}"', self.block)
        self.assertNotIn('exec bash "$REPO_DIR/install.sh"', self.block)

    def test_missing_script_file_warns_instead_of_failing(self):
        self.assertIn('[ -n "$UPDATE_SELF" ] && [ -r "$UPDATE_SELF" ]', self.block)
        self.assertIn("could not re-exec", self.block)

    def test_reexec_happens_after_the_merge_not_before(self):
        merge_at = self.block.index('merge --ff-only')
        exec_at = self.block.index('exec bash')
        self.assertLess(merge_at, exec_at,
                        "re-exec must follow the ff-merge, otherwise it "
                        "would re-exec the very code it is replacing")

    def test_guarded_against_looping(self):
        # Guard 1: only when the merge actually moved HEAD.
        self.assertIn('[ "$UPDATE_OLD_HEAD" != "$UPDATE_NEW_HEAD" ]', self.block)
        # Guard 2: only once per invocation.
        self.assertIn("AI_DEV_SWITCHBOARD_UPDATE_REEXEC", self.block)
        self.assertIn('[ -z "${AI_DEV_SWITCHBOARD_UPDATE_REEXEC:-}" ]', self.block)

    def test_still_never_resets_hard(self):
        # The update path's existing safety posture must survive this change.
        self.assertNotIn("reset --hard", self.block)
        self.assertIn("merge --ff-only", self.block)


@unittest.skipUnless(HAVE_GIT, "git not available")
class ReexecBehaviourTests(unittest.TestCase):
    """Runs the REAL extracted update block against a real git repo.

    The harness stands in for install.sh: it embeds the real block, then
    records which version of itself actually reached the end of the run.
    v1 records "1", v2 records "2". A correct --update must record "2" --
    the pulled version -- not "1".
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reexec-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.block = _extract_update_block(_install_sh())
        self.marker = os.path.join(self.tmp, "which-version-ran")

    def _git(self, *args, cwd):
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
        })
        subprocess.run(("git",) + args, cwd=cwd, env=env, check=True,
                       capture_output=True)

    def _harness(self, version: str) -> str:
        return textwrap.dedent('''\
            #!/usr/bin/env bash
            set -euo pipefail
            UPDATE=0
            for arg in "$@"; do
                case "$arg" in --update|--upgrade) UPDATE=1 ;; esac
            done
            REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
            REPO_BRANCH=main
            __VERSION__=%s
        ''') % version + self.block + textwrap.dedent('''\
            printf '%s\\n' "$__VERSION__" > "$MARKER_FILE"
        ''')

    def _write_harness(self, repo, version):
        path = os.path.join(repo, "install.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self._harness(version))
        os.chmod(path, 0o755)

    def test_update_run_applies_the_pulled_version_not_the_old_one(self):
        origin = os.path.join(self.tmp, "origin")
        clone = os.path.join(self.tmp, "clone")
        os.makedirs(origin)

        self._git("init", "-q", "-b", "main", cwd=origin)
        self._write_harness(origin, "1")
        self._git("add", "install.sh", cwd=origin)
        self._git("commit", "-qm", "v1", cwd=origin)

        subprocess.run(["git", "clone", "-q", origin, clone], check=True,
                       capture_output=True)

        # origin moves to v2; the clone is now one behind.
        self._write_harness(origin, "2")
        self._git("add", "install.sh", cwd=origin)
        self._git("commit", "-qm", "v2", cwd=origin)

        env = dict(os.environ)
        env["MARKER_FILE"] = self.marker
        env.pop("AI_DEV_SWITCHBOARD_UPDATE_REEXEC", None)
        res = subprocess.run([os.path.join(clone, "install.sh"), "--update"],
                             cwd=clone, env=env, capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(0, res.returncode, res.stderr)

        with open(self.marker, encoding="utf-8") as fh:
            ran = fh.read().strip()
        self.assertEqual(
            "2", ran,
            "the --update run must apply the version it pulled. Got version "
            "%r, i.e. bash kept executing the pre-merge file -- exactly the "
            "item 52 defect.\\nstdout:\\n%s" % (ran, res.stdout))

    def test_no_reexec_loop_when_already_up_to_date(self):
        # A --update that pulls nothing must not re-exec (and must still run).
        origin = os.path.join(self.tmp, "origin2")
        clone = os.path.join(self.tmp, "clone2")
        os.makedirs(origin)
        self._git("init", "-q", "-b", "main", cwd=origin)
        self._write_harness(origin, "1")
        self._git("add", "install.sh", cwd=origin)
        self._git("commit", "-qm", "v1", cwd=origin)
        subprocess.run(["git", "clone", "-q", origin, clone], check=True,
                       capture_output=True)

        env = dict(os.environ)
        env["MARKER_FILE"] = self.marker
        env.pop("AI_DEV_SWITCHBOARD_UPDATE_REEXEC", None)
        res = subprocess.run([os.path.join(clone, "install.sh"), "--update"],
                             cwd=clone, env=env, capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertNotIn("Re-executing", res.stdout,
                         "nothing was pulled, so there is nothing to re-exec")
        with open(self.marker, encoding="utf-8") as fh:
            self.assertEqual("1", fh.read().strip())


if __name__ == "__main__":
    unittest.main()
