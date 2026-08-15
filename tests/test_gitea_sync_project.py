#!/usr/bin/env python3
"""
Tests for scripts/gitea-sync-project.sh -- the poll-triggered sync-on-push
logic (docs/spec.md part 2c part 1, "The sync decision"). Exercises the
REAL script against REAL temporary local git repos (no Docker/network/Gitea
server involved -- a local "origin" repo stands in for Gitea, reached via a
plain filesystem path, exactly the way git itself treats any other remote).

Unlike scripts/new-project-from-gitea.sh's own tests, none of this needs
root/sudo: this script never crosses a privilege boundary internally (it's
invoked as RUN_USER externally, via sudoers, but performs no chown/su/mkdir
of its own) -- so it's run directly here as whatever user runs the test
suite, same "skip cleanly only when something is genuinely unavailable"
spirit as test_new_project_from_gitea.py, just with nothing to skip.

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
SCRIPT = os.path.join(REPO_ROOT, "scripts", "gitea-sync-project.sh")

HAVE_GIT = shutil.which("git") is not None


def run_script(args, projects_dir):
    env = dict(os.environ)
    env["PROJECTS_DIR"] = projects_dir
    # No /etc/ai-dev-switchboard/switchboard.env on the test box -- the
    # script's `[ -f "$CONFIG" ] && source` is a harmless no-op, same as
    # test_new_project_from_gitea.py's own unprivileged-case convention.
    return subprocess.run(["bash", SCRIPT] + list(args), capture_output=True,
                          text=True, env=env, timeout=30)


def git(args, cwd, env=None):
    full_env = dict(os.environ)
    full_env.setdefault("GIT_AUTHOR_NAME", "Test")
    full_env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    full_env.setdefault("GIT_COMMITTER_NAME", "Test")
    full_env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    if env:
        full_env.update(env)
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True, env=full_env, timeout=30)
    assert r.returncode == 0, f"git {args} failed: {r.stdout}\n{r.stderr}"
    return r.stdout


def write(path, content):
    with open(path, "w") as f:
        f.write(content)


@unittest.skipUnless(HAVE_GIT, "git not available")
class GiteaSyncProjectArgValidationTests(unittest.TestCase):
    """Argument-count/shape validation -- doesn't need a real repo at all."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-sync-args-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wrong_arg_count_rejected(self):
        r = run_script(["onlyname"], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stderr)

    def test_invalid_name_rejected(self):
        r = run_script(["../escape", "main"], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Invalid project name", r.stderr)

    def test_invalid_branch_rejected(self):
        r = run_script(["myproj", "main; rm -rf /"], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Invalid branch name", r.stderr)


@unittest.skipUnless(HAVE_GIT, "git not available")
class GiteaSyncProjectRealGitTests(unittest.TestCase):
    """Real git repos throughout -- no mocking of git itself."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-sync-real-")
        self.projects_dir = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects_dir)
        # "origin" stands in for the Gitea-hosted repo -- a plain local
        # path is enough; git treats it identically to any other remote
        # for fetch/merge-base purposes, which is all this script does.
        self.origin = os.path.join(self.tmp, "origin.git")
        os.makedirs(self.origin)
        git(["init", "-b", "main"], cwd=self.origin)
        write(os.path.join(self.origin, "README.md"), "hello\n")
        git(["add", "."], cwd=self.origin)
        git(["commit", "-m", "initial"], cwd=self.origin)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _clone(self, name="proj"):
        dest = os.path.join(self.projects_dir, name)
        git(["clone", self.origin, dest], cwd=self.projects_dir)
        return dest

    def test_missing_destination_reports_no_such_project(self):
        r = run_script(["nope", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "no-such-project")

    def test_destination_exists_but_is_not_a_git_repo_reports_no_such_project(self):
        dest = os.path.join(self.projects_dir, "notgit")
        os.makedirs(dest)
        write(os.path.join(dest, "x.txt"), "hi\n")
        r = run_script(["notgit", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "no-such-project")

    def test_clean_fast_forwardable_syncs_and_updates_working_tree(self):
        dest = self._clone()
        # Simulate a push from elsewhere: a new commit lands on origin,
        # dest's working copy never sees it until this script fetches.
        write(os.path.join(self.origin, "new-file.txt"), "pushed elsewhere\n")
        git(["add", "."], cwd=self.origin)
        git(["commit", "-m", "pushed elsewhere"], cwd=self.origin)

        r = run_script(["proj", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "synced")
        self.assertTrue(os.path.exists(os.path.join(dest, "new-file.txt")))
        head_dest = git(["rev-parse", "HEAD"], cwd=dest).strip()
        head_origin = git(["rev-parse", "HEAD"], cwd=self.origin).strip()
        self.assertEqual(head_dest, head_origin)

    def test_already_up_to_date_reports_synced_as_a_no_op(self):
        self._clone()
        # No new commits anywhere -- merge --ff-only is a guaranteed no-op.
        r = run_script(["proj", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "synced")

    def test_dirty_working_tree_is_skipped_and_left_byte_for_byte_intact(self):
        dest = self._clone()
        write(os.path.join(self.origin, "new-file.txt"), "pushed elsewhere\n")
        git(["add", "."], cwd=self.origin)
        git(["commit", "-m", "pushed elsewhere"], cwd=self.origin)

        # Uncommitted local change in the working copy.
        uncommitted_path = os.path.join(dest, "README.md")
        write(uncommitted_path, "local uncommitted edit\n")

        r = run_script(["proj", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "skipped-dirty")
        # Untouched: the uncommitted edit is still exactly what was written,
        # and the new remote file was never merged in.
        with open(uncommitted_path) as f:
            self.assertEqual(f.read(), "local uncommitted edit\n")
        self.assertFalse(os.path.exists(os.path.join(dest, "new-file.txt")))

    def test_diverged_local_commit_is_skipped_and_not_lost(self):
        dest = self._clone()
        # origin moves forward independently...
        write(os.path.join(self.origin, "new-file.txt"), "pushed elsewhere\n")
        git(["add", "."], cwd=self.origin)
        git(["commit", "-m", "pushed elsewhere"], cwd=self.origin)

        # ...while dest also gets its own local commit never pushed. HEAD is
        # then not an ancestor of the freshly-fetched origin/main (both
        # diverged from their common base).
        write(os.path.join(dest, "local-only.txt"), "local work in progress\n")
        git(["add", "."], cwd=dest)
        git(["commit", "-m", "local work in progress"], cwd=dest)
        local_head = git(["rev-parse", "HEAD"], cwd=dest).strip()

        r = run_script(["proj", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "skipped-diverged")
        # No history rewrite, no lost commit -- HEAD is exactly where it was,
        # and that commit is still reachable from HEAD.
        self.assertEqual(git(["rev-parse", "HEAD"], cwd=dest).strip(), local_head)
        self.assertFalse(os.path.exists(os.path.join(dest, "new-file.txt")))

    def test_local_ahead_only_no_remote_changes_is_also_diverged_not_synced(self):
        # A local commit not yet pushed, with origin unchanged, still fails
        # the ancestor check the other direction -- HEAD isn't an ancestor
        # of origin/main is trivially false when HEAD is strictly ahead...
        # actually HEAD *is* reachable from itself, so verify the real
        # semantics: is-ancestor HEAD origin/main asks whether origin/main
        # descends from HEAD, which is false when HEAD has unpushed commits
        # origin/main lacks.
        dest = self._clone()
        write(os.path.join(dest, "local-only.txt"), "local work in progress\n")
        git(["add", "."], cwd=dest)
        git(["commit", "-m", "local work in progress"], cwd=dest)
        local_head = git(["rev-parse", "HEAD"], cwd=dest).strip()

        r = run_script(["proj", "main"], self.projects_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "skipped-diverged")
        self.assertEqual(git(["rev-parse", "HEAD"], cwd=dest).strip(), local_head)


if __name__ == "__main__":
    unittest.main()
