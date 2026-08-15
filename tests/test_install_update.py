#!/usr/bin/env python3
"""
Tests for install.sh's `--update`/`--upgrade` flag (docs/BACKLOG.md item 14
-- docs/spec.md): fast-forwards the local $REPO_DIR checkout to
origin/$REPO_BRANCH, re-runs the script's existing (already idempotent)
copy/config steps against that fresh checkout, and -- only if no RUN_USER
tmux session is currently live -- restarts ai-dev-switchboard. Also covers
the RUN_USER/SVC_USER default-value bug fix folded into the same spec
(non-interactive re-run must preserve an already-configured RUN_USER/
SVC_USER, not silently reset to "dev"/"switchboard-svc").

Four independently-testable pieces, each extracted verbatim from install.sh
(never reimplemented -- same "extract between two literal markers"
technique as tests/test_deploy_target.py's _build_deploy_target_block_harness
and tests/test_install_ollama.py's _build_ollama_block_harness, so a future
edit to install.sh's real source would fail these tests rather than
silently drift):

1. Flag parsing / doc comment -- plain source greps, no execution needed.
2. RUN_USER/SVC_USER default-value fix -- the "-- Users --" section, driven
   via a real pty (install.sh's own prompt() reads from /dev/tty, not
   stdin -- same technique as test_deploy_target.py's _run_with_pty).
3. The update-pull block (dirty/branch/divergence checks, fetch,
   `merge --ff-only`) -- run against real throwaway git repos (a "local"
   clone with an "origin" remote pointing at a second bare-ish repo on the
   same machine), no root/sudo needed.
4. The guarded-restart block -- run with fake `sudo`/`tmux`/`systemctl`
   stand-ins on a fake PATH (same fake-PATH-stub-binaries technique
   test_deploy_target.py's WrapperBranchingTests already uses), no root
   needed.

No real root/sudo/systemd/tmux server is required for any test in this
file -- everything privileged install.sh's --update actually touches on a
live box (RUN_USER's real tmux server, the real ai-dev-switchboard.service)
is stood in for with fakes here, same spirit as this project's other
install.sh block tests.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_update.py
"""
import os
import pty
import select
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


class _PtyResult:
    """subprocess.CompletedProcess-alike for _run_with_pty -- a pty merges
    stdout/stderr into one stream, so both attributes point at the same
    captured text. Copied verbatim from tests/test_deploy_target.py's own
    _PtyResult (same shape, same reasoning)."""
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.stdout = output
        self.stderr = output


def _run_with_pty(cmd, answers, timeout=20):
    """Runs `cmd` with a real pty as its controlling terminal, writing each
    of `answers` in turn -- needed because install.sh's own `prompt()`
    reads from /dev/tty specifically, not stdin. Copied verbatim from
    tests/test_deploy_target.py's own `_run_with_pty` (same technique, same
    reasoning); duplicated here (not imported) to keep this test file
    self-contained, matching tests/test_install_ollama.py's own precedent
    of copying rather than importing this helper."""
    pid, master_fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
        os._exit(127)  # pragma: no cover -- only reached if execvp fails

    output = b""
    for ans in answers:
        time.sleep(0.4)  # let the script reach its next `read -rp` first
        try:
            os.write(master_fd, (ans + "\n").encode())
        except OSError:
            break

    deadline = time.time() + timeout
    reaped = None
    while time.time() < deadline:
        r, _, _ = select.select([master_fd], [], [], 0.5)
        if master_fd in r:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break  # pty closed
            if not chunk:
                break
            output += chunk
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            reaped = status
            break
    else:
        os.kill(pid, 9)

    if reaped is None:
        wpid, status = os.waitpid(pid, 0)
        reaped = status
    try:
        os.close(master_fd)
    except OSError:
        pass

    rc = os.WEXITSTATUS(reaped) if os.WIFEXITED(reaped) else -1
    text = output.decode(errors="replace")
    if time.time() >= deadline and rc == -1:
        text += "\n[test harness: timed out waiting for the child to exit]"
    return _PtyResult(rc, text)


def _make_fake_bin(bindir, name, body):
    """Writes a fake executable `name` into `bindir` that records its own
    invocation -- same fake-PATH technique tests/test_deploy_target.py's
    _make_fake_bin already uses."""
    path = os.path.join(bindir, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return path


# ── source-level checks: flag parsing / doc comment ─────────────────────

class FlagAndDocTests(unittest.TestCase):
    def setUp(self):
        with open(INSTALL_SH) as f:
            self.source = f.read()

    def test_flag_defaults_to_off_and_both_spellings_wired_up(self):
        self.assertIn("UPDATE=0", self.source)
        self.assertIn('--update|--upgrade) UPDATE=1 ;;', self.source)

    def test_usage_header_documents_the_flag(self):
        header = self.source[:self.source.index("set -euo pipefail")]
        self.assertIn("--update", header)
        self.assertIn("--upgrade", header)

    def test_no_reset_hard_anywhere_in_update_machinery(self):
        # docs/spec.md: never a destructive `git reset --hard` anywhere in
        # this flag's own logic.
        update_pull_block = _extract_between(
            self.source, "if [ \"$UPDATE\" -eq 1 ]; then\n    echo \"-- Update",
            "interactive() {")
        self.assertNotIn("reset --hard", update_pull_block)

    def test_guarded_restart_has_no_force_override(self):
        # No actual --force flag/FORCE variable wired up anywhere in this
        # script's flag parsing or the restart block itself -- the prose
        # in the restart block's own comment documenting the ABSENCE of
        # one doesn't count.
        self.assertNotIn('"--force"', self.source)
        self.assertNotIn("FORCE=1", self.source)
        self.assertNotIn("$FORCE", self.source)


# ── RUN_USER/SVC_USER default-value fix ──────────────────────────────────

def _build_users_block_harness(env_file, yes=1):
    """Assembles a standalone runnable script out of install.sh's own
    helper functions plus the literal "-- Users --" section, unmodified,
    extracted straight from install.sh's real source -- same technique as
    tests/test_install_ollama.py's _build_ollama_block_harness. `yes=1`
    (YES=1, the --yes/non-interactive path -- what --update --yes uses)
    makes prompt() just echo its default straight back with no /dev/tty
    read; `yes=0` drives it interactively via a real pty instead."""
    with open(INSTALL_SH) as f:
        source = f.read()
    helpers = _extract_between(source, "interactive() {", "random_token() {")
    block = _extract_between(
        source, 'echo "-- Users --"',
        'id "$RUN_USER" &>/dev/null')
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        YES={yes}
        ENV_FILE={env_file}
        {helpers}
        {block}
        echo "RUN_USER=$RUN_USER"
        echo "SVC_USER=$SVC_USER"
        """)


class RunUserSvcUserDefaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-update-users-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env_file = os.path.join(self.tmp, "switchboard.env")

    def run_block(self, answers, yes=1):
        script = _build_users_block_harness(self.env_file, yes=yes)
        return _run_with_pty(["bash", "-c", script], answers)

    def test_fresh_install_no_env_file_defaults_to_dev_and_switchboard_svc(self):
        # First-ever install: ENV_FILE doesn't exist yet -- get_env against
        # a not-yet-existing file returns empty cleanly, falling back to
        # the literal defaults exactly as before this fix.
        r = self.run_block(["", ""])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RUN_USER=dev", r.stdout)
        self.assertIn("SVC_USER=switchboard-svc", r.stdout)

    def test_non_interactive_rerun_preserves_already_configured_run_user(self):
        # The bug this spec fixes: a non-interactive re-run (--yes, YES=1
        # here) on a box whose switchboard.env already has RUN_USER=
        # someuser must keep "someuser" -- not silently reset to "dev".
        with open(self.env_file, "w") as f:
            f.write("RUN_USER=someuser\nSVC_USER=someuser-svc\n")
        r = self.run_block(["", ""])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RUN_USER=someuser", r.stdout)
        self.assertIn("SVC_USER=someuser-svc", r.stdout)
        self.assertNotIn("RUN_USER=dev", r.stdout)
        self.assertNotIn("SVC_USER=switchboard-svc", r.stdout)

    def test_interactive_answer_still_overrides_configured_value(self):
        with open(self.env_file, "w") as f:
            f.write("RUN_USER=someuser\nSVC_USER=someuser-svc\n")
        r = self.run_block(["typeduser", "typedsvc"], yes=0)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RUN_USER=typeduser", r.stdout)
        self.assertIn("SVC_USER=typedsvc", r.stdout)


# ── update-pull block: dirty / branch / divergence checks, fetch, ff-only ─

def _build_update_pull_harness(repo_dir, repo_branch="main", update=1):
    with open(INSTALL_SH) as f:
        source = f.read()
    block = _extract_between(
        source, "if [ \"$UPDATE\" -eq 1 ]; then\n    echo \"-- Update",
        "interactive() {")
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        UPDATE={update}
        REPO_DIR={repo_dir}
        REPO_BRANCH={repo_branch}
        REPO_URL=https://example.invalid/ai-dev-switchboard.git
        {block}
        """)


class UpdatePullBlockTests(unittest.TestCase):
    """Exercises the fetch/merge --ff-only logic against real throwaway git
    repos -- a "local" clone (REPO_DIR) with a real "origin" remote
    (ORIGIN_DIR, a second real repo on the same machine) -- no root/sudo
    needed, matching the "genuinely can't be faked" bar only for things
    that actually need root (RUN_USER's tmux server, systemctl) elsewhere
    in this file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-update-pull-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.origin_dir = os.path.join(self.tmp, "origin")
        self.repo_dir = os.path.join(self.tmp, "repo")
        self._git_init(self.origin_dir)
        self._commit(self.origin_dir, "first.txt", "one")
        subprocess.run(["git", "clone", "-q", "--branch", "main",
                        self.origin_dir, self.repo_dir], check=True,
                       env=self._env())

    def _env(self):
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = "Test"
        env["GIT_AUTHOR_EMAIL"] = "test@example.com"
        env["GIT_COMMITTER_NAME"] = "Test"
        env["GIT_COMMITTER_EMAIL"] = "test@example.com"
        return env

    def _git_init(self, path):
        os.makedirs(path, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", path], check=True,
                       env=self._env())

    def _commit(self, repo, filename, content, msg=None):
        with open(os.path.join(repo, filename), "w") as f:
            f.write(content)
        subprocess.run(["git", "-C", repo, "add", filename], check=True, env=self._env())
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", msg or f"add {filename}"],
                       check=True, env=self._env())

    def run_pull(self, repo_dir=None, repo_branch="main", update=1):
        script = _build_update_pull_harness(repo_dir or self.repo_dir, repo_branch, update)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env=self._env())

    def test_update_flag_off_is_a_noop(self):
        r = self.run_pull(update=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Update", r.stdout)

    def test_clean_behind_origin_fast_forwards(self):
        self._commit(self.origin_dir, "second.txt", "two")
        before = subprocess.run(["git", "-C", self.repo_dir, "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        origin_head = subprocess.run(["git", "-C", self.origin_dir, "rev-parse", "HEAD"],
                                     capture_output=True, text=True, check=True).stdout.strip()
        self.assertNotEqual(before, origin_head)

        r = self.run_pull()
        self.assertEqual(r.returncode, 0, r.stderr)
        after = subprocess.run(["git", "-C", self.repo_dir, "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(after, origin_head)
        self.assertTrue(os.path.exists(os.path.join(self.repo_dir, "second.txt")))

    def test_already_up_to_date_is_a_harmless_noop(self):
        r = self.run_pull()
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = self.run_pull()
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_dirty_working_copy_refused_before_fetch(self):
        with open(os.path.join(self.repo_dir, "first.txt"), "a") as f:
            f.write("uncommitted change\n")
        self._commit(self.origin_dir, "second.txt", "two")

        r = self.run_pull()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uncommitted local changes", r.stderr)
        # Never fetched -- refused before touching the remote at all.
        after = subprocess.run(["git", "-C", self.repo_dir, "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
        before = subprocess.run(["git", "-C", self.origin_dir, "rev-parse", "HEAD~1"],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(after, before)

    def test_wrong_branch_checked_out_refused_before_fetch(self):
        subprocess.run(["git", "-C", self.repo_dir, "checkout", "-q", "-b", "other"],
                       check=True, env=self._env())
        r = self.run_pull(repo_branch="main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("REPO_BRANCH", r.stderr)
        self.assertIn("other", r.stderr)

    def test_detached_head_refused_before_fetch(self):
        head = subprocess.run(["git", "-C", self.repo_dir, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", self.repo_dir, "checkout", "-q", head],
                       check=True, env=self._env())
        r = self.run_pull(repo_branch="main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("detached HEAD", r.stderr)

    def test_diverged_local_branch_refused_after_fetch_before_merge(self):
        # Real divergence: origin gets a new commit, AND the local repo
        # gets its own independent commit not present upstream.
        self._commit(self.origin_dir, "second.txt", "two")
        self._commit(self.repo_dir, "local-only.txt", "local", msg="local-only commit")

        origin_head = subprocess.run(["git", "-C", self.origin_dir, "rev-parse", "HEAD"],
                                     capture_output=True, text=True, check=True).stdout.strip()

        r = self.run_pull()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("diverged", r.stderr)
        # Fetch DID happen -- the local repo's own remote-tracking ref now
        # matches origin's real HEAD -- but no merge was performed: HEAD
        # itself still only has the local-only commit on top of the
        # original, not origin's second.txt.
        fetched_ref = subprocess.run(
            ["git", "-C", self.repo_dir, "rev-parse", "refs/remotes/origin/main"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(fetched_ref, origin_head,
                         "fetch must have happened -- origin/main should be known locally")
        self.assertFalse(os.path.exists(os.path.join(self.repo_dir, "second.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.repo_dir, "local-only.txt")))

    def test_not_a_git_repo_refused_immediately(self):
        plain_dir = os.path.join(self.tmp, "not-a-repo")
        os.makedirs(plain_dir)
        r = self.run_pull(repo_dir=plain_dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a git checkout", r.stderr)

    def test_no_network_reachable_aborts_before_touching_anything(self):
        # A REPO_DIR whose origin points at a nonexistent path -- `git
        # fetch` fails, and `set -euo pipefail` must abort the whole
        # script right there (docs/spec.md "Edge cases").
        bad_origin = os.path.join(self.tmp, "does-not-exist")
        subprocess.run(["git", "-C", self.repo_dir, "remote", "set-url", "origin", bad_origin],
                       check=True, env=self._env())
        r = self.run_pull()
        self.assertNotEqual(r.returncode, 0)


# ── guarded-restart block: live-session detection ────────────────────────

def _build_restart_block_harness(run_user, install_dir):
    with open(INSTALL_SH) as f:
        source = f.read()
    block = _extract_between(
        source, "# ── Update (--update/--upgrade): guarded restart",
        'if [ "$WITH_GIT_HOSTING" -eq 1 ]; then')
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        UPDATE=1
        RUN_USER={run_user}
        INSTALL_DIR={install_dir}
        {block}
        """)


class GuardedRestartBlockTests(unittest.TestCase):
    def setUp(self):
        self.bindir = tempfile.mkdtemp(prefix="aidswb-update-restart-bin-")
        self.addCleanup(lambda: shutil.rmtree(self.bindir, ignore_errors=True))
        self.systemctl_log = os.path.join(self.bindir, "systemctl.invoked")

    def _fake_tmux(self, session_names):
        """A fake `tmux` that answers `list-sessions -F ...` with the given
        session names (one per line), or exits 1 (no server running) if
        the list is empty -- mirroring real tmux's own "no server running
        on <socket>" exit-1 behavior, which install.sh's own `|| true`
        already treats as "no sessions"."""
        if session_names:
            body = "#!/bin/sh\n" + "\n".join(
                f'echo "{n}"' for n in session_names) + "\nexit 0\n"
        else:
            body = "#!/bin/sh\nexit 1\n"
        _make_fake_bin(self.bindir, "tmux", body)

    def _fake_sudo(self):
        # Real `sudo -u user tmux ...` -- fake sudo just strips the "-u
        # user" and execs the rest, so the fake tmux above is what
        # actually answers.
        _make_fake_bin(self.bindir, "sudo", textwrap.dedent("""\
            #!/bin/sh
            shift 2  # drop "-u" "user"
            exec "$@"
            """))

    def _fake_systemctl(self):
        _make_fake_bin(self.bindir, "systemctl", textwrap.dedent(f"""\
            #!/bin/sh
            echo "systemctl $*" >> {self.systemctl_log}
            exit 0
            """))

    def run_block(self, run_user="dev", install_dir="/opt/ai-dev-switchboard"):
        script = _build_restart_block_harness(run_user, install_dir)
        env = dict(os.environ)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env=env)

    def test_no_live_sessions_restarts_service(self):
        self._fake_tmux([])
        self._fake_sudo()
        self._fake_systemctl()
        r = self.run_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.systemctl_log) as f:
            self.assertEqual(f.read().strip(), "systemctl restart ai-dev-switchboard")
        self.assertIn("restarting ai-dev-switchboard", r.stdout)

    def test_live_session_defers_restart_names_it_in_stderr(self):
        self._fake_tmux(["team-myproject"])
        self._fake_sudo()
        self._fake_systemctl()
        r = self.run_block(run_user="dev", install_dir="/opt/ai-dev-switchboard")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.systemctl_log),
                         "systemctl restart must never be invoked while a session is live")
        self.assertIn("dev has live tmux session", r.stderr)
        self.assertIn("team-myproject", r.stderr)
        self.assertIn("/opt/ai-dev-switchboard", r.stderr)
        self.assertIn("sudo systemctl restart ai-dev-switchboard", r.stderr)
        self.assertNotIn("--force", r.stderr)

    def test_multiple_live_sessions_all_named(self):
        self._fake_tmux(["team-a", "claude-b", "team-c"])
        self._fake_sudo()
        self._fake_systemctl()
        r = self.run_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.systemctl_log))
        for name in ("team-a", "claude-b", "team-c"):
            self.assertIn(name, r.stderr)

    def test_unrelated_personal_session_still_counted_as_live(self):
        # docs/spec.md "Edge cases": a coarse, deliberate false-positive --
        # ANY live RUN_USER tmux session defers the restart, not just
        # switchboard-owned ones.
        self._fake_tmux(["my-personal-shell"])
        self._fake_sudo()
        self._fake_systemctl()
        r = self.run_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.systemctl_log))
        self.assertIn("my-personal-shell", r.stderr)


if __name__ == "__main__":
    unittest.main()
