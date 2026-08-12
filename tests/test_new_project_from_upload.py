#!/usr/bin/env python3
"""
Tests for scripts/new-project-from-upload.sh — the folder-upload wizard's
privileged hand-off script (see docs/spec.md "Crossing the privilege
boundary"). Invokes the real script via subprocess (no mocking of bash
itself), matching this repo's own "verify it for real" convention from
tests/test_upload.py's zip-writer verification pass.

Argument-validation cases (invalid name, missing source dir) run without
any elevated privilege — they fail before the script's mkdir/chown/su
steps. The rest need root (mkdir under an arbitrary PROJECTS_DIR + chown +
`su "$RUN_USER"` all behave like the real deployment only when actually
run as root) — gated on passwordless sudo being available and skipped
otherwise, same "skip cleanly rather than fail on an environment that
can't support it" spirit as this repo's other environment-dependent checks.

Run with:
    python3 -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
SCRIPT = os.path.join(REPO_ROOT, "scripts", "new-project-from-upload.sh")

HAVE_PASSWORDLESS_SUDO = subprocess.run(
    ["sudo", "-n", "true"], capture_output=True).returncode == 0
RUN_USER = os.environ.get("USER") or subprocess.run(
    ["id", "-un"], capture_output=True, text=True).stdout.strip()


def run_unprivileged(args, **kwargs):
    """Runs the script directly (no sudo) — only valid for the
    argument-validation paths that fail before touching PROJECTS_DIR."""
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, **kwargs)


def run_privileged(args, projects_dir, run_user=RUN_USER, path_override=None):
    """
    Runs the script as root via sudo (matching real deployment), with
    RUN_USER/PROJECTS_DIR passed through `env` (sudo resets the
    environment by default, so plain os.environ overrides wouldn't reach
    the child) — mirrors exactly how app.py's own
    _register_via_privileged_script invokes it, just with test-controlled
    RUN_USER/PROJECTS_DIR instead of the real deployment's.
    """
    env_args = [f"RUN_USER={run_user}", f"PROJECTS_DIR={projects_dir}"]
    if path_override is not None:
        env_args.append(f"PATH={path_override}")
    return subprocess.run(["sudo", "env", *env_args, "bash", SCRIPT, *args],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


class ArgumentValidationTests(unittest.TestCase):
    """These fail before any privileged operation, so they run as-is."""

    def test_wrong_arg_count_rejected(self):
        r = run_unprivileged(["only-one-arg"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Usage:", r.stderr)

    def test_invalid_name_rejected(self):
        tmp = tempfile.mkdtemp(prefix="npfu-src-")
        try:
            r = run_unprivileged([tmp, "../escape"])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Invalid project name", r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_name_starting_with_symbol_rejected(self):
        tmp = tempfile.mkdtemp(prefix="npfu-src-")
        try:
            r = run_unprivileged([tmp, "-rf"])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Invalid project name", r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_source_dir_rejected(self):
        r = run_unprivileged(["/no/such/source/dir/anywhere", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No such source directory", r.stderr)


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to exercise the real "
                     "mkdir/cp/chown/su privileged hand-off")
class PrivilegedRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="npfu-")
        self.addCleanup(lambda: subprocess.run(["sudo", "rm", "-rf", self.tmp]))
        self.source = os.path.join(self.tmp, "src")
        self.projects_dir = os.path.join(self.tmp, "projects")
        os.makedirs(self.source)

    def test_copies_files_chowns_and_git_inits(self):
        os.makedirs(os.path.join(self.source, "sub"))
        with open(os.path.join(self.source, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.source, "sub", "b.txt"), "w") as f:
            f.write("world")

        r = run_privileged([self.source, "myproj"], self.projects_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ready:", r.stdout)

        dest = os.path.join(self.projects_dir, "myproj")
        self.assertTrue(os.path.isdir(dest))
        with open(os.path.join(dest, "a.txt")) as f:
            self.assertEqual(f.read(), "hello")
        with open(os.path.join(dest, "sub", "b.txt")) as f:
            self.assertEqual(f.read(), "world")

        st = os.stat(dest)
        self.assertEqual(st.st_uid, os.stat(self.tmp).st_uid)

        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))
        log = subprocess.run(["git", "-C", dest, "log", "--oneline"],
                             capture_output=True, text=True)
        self.assertEqual(log.returncode, 0)
        self.assertIn("Initial import via ai-dev-switchboard upload", log.stdout)

    def test_source_left_in_place_not_moved(self):
        with open(os.path.join(self.source, "a.txt"), "w") as f:
            f.write("hello")
        r = run_privileged([self.source, "myproj"], self.projects_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        # cp -a, not mv (docs/spec.md) — the caller (app.py) cleans up
        # staging separately; this script must never delete the source.
        self.assertTrue(os.path.isfile(os.path.join(self.source, "a.txt")))

    def test_already_existing_target_fails_atomically_no_dash_p(self):
        with open(os.path.join(self.source, "a.txt"), "w") as f:
            f.write("first")
        r1 = run_privileged([self.source, "dup"], self.projects_dir)
        self.assertEqual(r1.returncode, 0, r1.stderr)

        source2 = os.path.join(self.tmp, "src2")
        os.makedirs(source2)
        with open(os.path.join(source2, "b.txt"), "w") as f:
            f.write("second")
        r2 = run_privileged([source2, "dup"], self.projects_dir)
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("Already exists", r2.stderr)

        # The first registration's content is untouched by the failed
        # second attempt (docs/spec.md "Partial-failure semantics").
        dest = os.path.join(self.projects_dir, "dup")
        self.assertTrue(os.path.isfile(os.path.join(dest, "a.txt")))
        self.assertFalse(os.path.isfile(os.path.join(dest, "b.txt")))

    def test_existing_git_repo_is_not_reinitialized(self):
        subprocess.run(["git", "init", "-q", self.source], check=True)
        subprocess.run(["git", "-C", self.source, "-c", "user.name=x",
                       "-c", "user.email=x@x", "commit", "--allow-empty",
                       "-q", "-m", "pre-existing commit"], check=True)

        r = run_privileged([self.source, "hasgit"], self.projects_dir)
        self.assertEqual(r.returncode, 0, r.stderr)

        dest = os.path.join(self.projects_dir, "hasgit")
        log = subprocess.run(["git", "-C", dest, "log", "--oneline"],
                             capture_output=True, text=True)
        self.assertEqual(log.returncode, 0)
        self.assertIn("pre-existing commit", log.stdout)
        self.assertNotIn("Initial import via ai-dev-switchboard upload", log.stdout)

    def test_name_with_space_and_hyphen_accepted(self):
        with open(os.path.join(self.source, "a.txt"), "w") as f:
            f.write("hi")
        r = run_privileged([self.source, "my project-1"], self.projects_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.projects_dir, "my project-1")))

    def test_missing_git_binary_skips_init_silently(self):
        # Build a PATH with symlinks to everything the script needs
        # EXCEPT git, proving the "git not installed" degrade-gracefully
        # path (docs/spec.md edge cases) doesn't fail the whole
        # registration — it just skips the init/commit step.
        with open(os.path.join(self.source, "a.txt"), "w") as f:
            f.write("hi")
        bindir = tempfile.mkdtemp(prefix="npfu-bin-")
        self.addCleanup(lambda: shutil.rmtree(bindir, ignore_errors=True))
        for tool in ("mkdir", "cp", "chown", "su", "bash", "env", "sh",
                    "cat", "rm", "mv", "test", "["):
            real = shutil.which(tool)
            if real:
                try:
                    os.symlink(real, os.path.join(bindir, tool))
                except FileExistsError:
                    pass
        r = run_privileged([self.source, "nogit"], self.projects_dir,
                           path_override=bindir)
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = os.path.join(self.projects_dir, "nogit")
        self.assertTrue(os.path.isfile(os.path.join(dest, "a.txt")))
        self.assertFalse(os.path.isdir(os.path.join(dest, ".git")))


if __name__ == "__main__":
    unittest.main()
