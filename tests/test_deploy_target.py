#!/usr/bin/env python3
"""
Tests for deploy-target/deploy-wrapper.sh and deploy-target/deploy-restart.sh
-- the receiver-side pieces of backlog item 2c, part 2a (docs/spec.md).

Two tiers, matching this repo's own convention
(tests/test_new_project_from_upload.py, tests/test_new_project_from_gitea.py):

1. Logic/branching tests that need no root at all -- deploy-restart.sh's
   argument validation runs (and fails) before ever touching systemd, and
   deploy-wrapper.sh's `case "$SSH_ORIGINAL_COMMAND"` branching is exercised
   directly (no real sshd involved) with fake `rrsync`/`sudo` stand-ins on
   PATH that just record their own argv -- the same "fake PATH" technique
   test_new_project_from_upload.py already uses for its own "git not
   installed" case, applied here to prove exactly which command each branch
   execs, without needing a real privileged rrsync/sudo call to observe it.

2. Real end-to-end SSH tests against this machine's own real sshd (already
   running -- see `systemctl status ssh`), using a throwaway system user
   (NOT literally "deploy", to avoid ever colliding with a real deploy
   account on whatever box runs this suite) wired up with the exact
   authorized_keys/sudoers line shapes install.sh's own
   --with-deploy-target block generates (cross-checked against install.sh's
   literal source below, so a future edit to those templates would fail
   this test rather than silently drift). Gated on passwordless sudo (this
   suite needs real useradd/sudoers/systemd) and skipped cleanly otherwise,
   same spirit as every other privileged-script test in this repo.

3. Real runs of install.sh's own --with-deploy-target block (extracted
   verbatim from install.sh's source, not reimplemented -- see
   _build_deploy_target_block_harness), proving install.sh's own
   provisioning logic (not just the two scripts it installs). install.sh's
   `prompt()` deliberately reads answers from /dev/tty rather than stdin
   (so it still works when curl-piped into `bash -c`), so a plain
   subprocess with piped stdin can't drive it -- these tests instead use a
   real pty (`pty.fork()`) as the child's controlling terminal, exactly
   like a human typing answers at a real terminal, via _run_with_pty below.

Run with:
    python3 -m unittest discover -s tests -v
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
DEPLOY_DIR = os.path.join(REPO_ROOT, "deploy-target")
WRAPPER = os.path.join(DEPLOY_DIR, "deploy-wrapper.sh")
RESTART = os.path.join(DEPLOY_DIR, "deploy-restart.sh")
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")

HAVE_PASSWORDLESS_SUDO = subprocess.run(
    ["sudo", "-n", "true"], capture_output=True).returncode == 0
HAVE_SSHD_ON_LOCALHOST = subprocess.run(
    ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/22"],
    capture_output=True).returncode == 0

CONFIG_FILE = "/etc/ai-dev-switchboard/deploy-target.env"


def _make_fake_bin(bindir, name, body):
    """Writes a fake executable `name` into `bindir` that records its own
    argv into a file next to it (NAME.invoked) and exits 0 -- the same
    fake-PATH technique test_new_project_from_upload.py's
    test_missing_git_binary_skips_init_silently already uses, just
    recording invocation instead of skipping one tool."""
    path = os.path.join(bindir, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return path


class WrapperBranchingTests(unittest.TestCase):
    """Exercises deploy-wrapper.sh's `case "$SSH_ORIGINAL_COMMAND"` directly
    -- no real sshd or root needed. Real /bin/sh-style PATH tools (cat, sed,
    sudo passthrough, etc.) still resolve normally since bindir is
    prepended, not exclusive."""

    def setUp(self):
        self.bindir = tempfile.mkdtemp(prefix="dwrap-bin-")
        self.addCleanup(lambda: shutil.rmtree(self.bindir, ignore_errors=True))
        self.recorded = os.path.join(self.bindir, "recorded.txt")
        # Fake rrsync -- records argv, then exits 0 without touching
        # anything real.
        _make_fake_bin(self.bindir, "rrsync", textwrap.dedent(f"""\
            #!/bin/sh
            echo "rrsync $*" > {self.recorded}
            exit 0
            """))
        # Fake sudo -- records argv (so we can prove deploy-restart.sh was
        # asked for via `sudo -n <path>`), then exits 0.
        _make_fake_bin(self.bindir, "sudo", textwrap.dedent(f"""\
            #!/bin/sh
            echo "sudo $*" > {self.recorded}
            exit 0
            """))

    def run_wrapper(self, ssh_original_command=None, deploy_path=None):
        env = dict(os.environ)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        if ssh_original_command is not None:
            env["SSH_ORIGINAL_COMMAND"] = ssh_original_command
        else:
            env.pop("SSH_ORIGINAL_COMMAND", None)
        # deploy-wrapper.sh only reads DEPLOY_PATH via sourcing
        # /etc/ai-dev-switchboard/deploy-target.env, which won't exist (or
        # won't matter) in this unprivileged test -- but the real
        # deployment sources a root-owned file we can't safely fake as a
        # non-root test user pointing at the real path here, so we prove
        # the "unset" fail-closed case for real (CONFIG absent -> DEPLOY_
        # PATH unset) and prove the "valid path" case by exporting
        # DEPLOY_PATH directly, which is exactly what CONFIG being sourced
        # would also produce -- `source "$CONFIG"` and an already-exported
        # env var reach the same `${DEPLOY_PATH:-}` read inside the
        # script.
        if deploy_path is not None:
            env["DEPLOY_PATH"] = deploy_path
        else:
            env.pop("DEPLOY_PATH", None)
        return subprocess.run(["bash", WRAPPER], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=env)

    def test_no_command_rejected(self):
        r = self.run_wrapper(ssh_original_command=None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not permitted", r.stderr)
        self.assertFalse(os.path.exists(self.recorded))

    def test_arbitrary_command_rejected(self):
        r = self.run_wrapper(ssh_original_command="whoami")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not permitted", r.stderr)
        self.assertFalse(os.path.exists(self.recorded))

    def test_rsync_server_with_unset_deploy_path_fails_closed(self):
        r = self.run_wrapper(ssh_original_command="rsync --server -wo . .",
                             deploy_path=None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DEPLOY_PATH", r.stderr)
        self.assertFalse(os.path.exists(self.recorded),
                         "must never fall through to calling rrsync with an unset path")

    def test_rsync_server_with_relative_deploy_path_fails_closed(self):
        r = self.run_wrapper(ssh_original_command="rsync --server -wo . .",
                             deploy_path="relative/path")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DEPLOY_PATH", r.stderr)
        self.assertFalse(os.path.exists(self.recorded))

    @unittest.skipUnless(os.path.exists("/usr/bin/rrsync"),
                         "wrapper execs the hardcoded absolute path "
                         "/usr/bin/rrsync, not a PATH-resolved one -- this "
                         "test needs the real binary present to prove "
                         "delegation happened")
    def test_rsync_server_with_valid_path_delegates_to_real_rrsync(self):
        # The wrapper execs the hardcoded absolute path /usr/bin/rrsync
        # (deliberately not PATH-resolved -- see the script's own comment),
        # so the fake rrsync on our PATH is never reached for this branch;
        # that's exactly what this test proves indirectly, by checking
        # validation passed (no fail-closed message) and control reached
        # the real rrsync (which then fails on its own for protocol
        # reasons, since no real rsync handshake was sent on stdin --
        # a full real push is exercised end-to-end in
        # PrivilegedEndToEndTests.test_push_lands_under_deploy_path_owned_by_receiver_user).
        realdir = tempfile.mkdtemp(prefix="dwrap-deploypath-")
        self.addCleanup(lambda: shutil.rmtree(realdir, ignore_errors=True))
        r = self.run_wrapper(ssh_original_command="rsync --server -wo . .",
                             deploy_path=realdir)
        self.assertFalse(os.path.exists(self.recorded),
                         "the fake rrsync on PATH must never be reached -- "
                         "the wrapper always execs the hardcoded absolute path")
        self.assertNotIn("DEPLOY_PATH is unset or not an absolute path", r.stderr)
        self.assertNotIn("not permitted", r.stderr)

    def test_deploy_restart_execs_sudo_with_exact_script_path(self):
        r = self.run_wrapper(ssh_original_command="deploy-restart")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.recorded) as f:
            self.assertEqual(
                f.read().strip(),
                "sudo -n /usr/local/bin/ai-dev-switchboard-deploy-restart.sh")

    def test_deploy_restart_with_trailing_junk_rejected(self):
        # Exact string match only -- "deploy-restart foo" must NOT match
        # the "deploy-restart" branch (that would be a passthrough-argument
        # hole into a restricted zero-argument grant).
        r = self.run_wrapper(ssh_original_command="deploy-restart foo")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not permitted", r.stderr)
        self.assertFalse(os.path.exists(self.recorded))

    def test_never_evals_original_command(self):
        # A classic injection probe: if the wrapper ever eval'd or
        # re-interpreted $SSH_ORIGINAL_COMMAND instead of doing a literal
        # case-match, this would execute `touch`. It must not.
        marker = os.path.join(self.bindir, "pwned")
        r = self.run_wrapper(ssh_original_command=f"; touch {marker}; ")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(marker))


class RestartValidationTests(unittest.TestCase):
    """deploy-restart.sh's own defense-in-depth validation -- these fail
    before ever calling `systemctl restart`, so no root is needed."""

    def run_restart(self, deploy_service_name=None):
        env = dict(os.environ)
        if deploy_service_name is not None:
            env["DEPLOY_SERVICE_NAME"] = deploy_service_name
        else:
            env.pop("DEPLOY_SERVICE_NAME", None)
        return subprocess.run(["bash", RESTART], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=env)

    def test_unset_service_name_rejected(self):
        r = self.run_restart(deploy_service_name=None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DEPLOY_SERVICE_NAME", r.stderr)

    def test_service_name_with_shell_metacharacters_rejected(self):
        r = self.run_restart(deploy_service_name="x; rm -rf /")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid DEPLOY_SERVICE_NAME", r.stderr)

    def test_service_name_with_space_rejected(self):
        r = self.run_restart(deploy_service_name="my service")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid DEPLOY_SERVICE_NAME", r.stderr)

    def test_service_name_with_path_traversal_rejected(self):
        r = self.run_restart(deploy_service_name="../../etc/passwd")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid DEPLOY_SERVICE_NAME", r.stderr)

    def test_valid_service_name_shape_accepted_by_validation(self):
        # Doesn't assert success (restarting a real unit needs root and a
        # real unit -- covered by PrivilegedEndToEndTests below); only
        # proves the regex itself doesn't reject a legitimate name before
        # set -e takes over at the systemctl call.
        r = self.run_restart(deploy_service_name="myapp.service")
        self.assertNotIn("Invalid", r.stderr)
        self.assertNotIn("DEPLOY_SERVICE_NAME isn't set", r.stderr)


class InstallShTemplateTests(unittest.TestCase):
    """Cross-checks the exact authorized_keys / sudoers line templates
    install.sh's --with-deploy-target block generates against the literal
    strings docs/spec.md mandates -- so a future edit to install.sh's
    format drifting from spec would fail here, and so
    PrivilegedEndToEndTests below can exercise the *actual* strings
    install.sh would produce without needing to run install.sh's full
    interactive flow (apt-get/useradd/systemd-for-the-whole-switchboard) in
    a test."""

    def setUp(self):
        with open(INSTALL_SH) as f:
            self.source = f.read()

    def test_authorized_keys_line_matches_spec(self):
        expected = ('command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh"'
                   ',no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty')
        self.assertIn(expected, self.source)

    def test_sudoers_line_matches_spec(self):
        expected = ("deploy ALL=(root) NOPASSWD: "
                   "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh")
        self.assertIn(expected, self.source)
        # Zero arguments -- no trailing " *" passthrough, unlike the
        # RUN_USER-scoped rules elsewhere in this file.
        self.assertNotIn(expected + " *", self.source)

    def test_flag_is_wired_up(self):
        self.assertIn('--with-deploy-target', self.source)
        self.assertIn('WITH_DEPLOY_TARGET=1', self.source)


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


class _PtyResult:
    """subprocess.CompletedProcess-alike for _run_with_pty -- a pty merges
    stdout/stderr into one stream, so both attributes point at the same
    captured text (good enough for assertIn checks and for including
    diagnostic output in an assertEqual failure message)."""
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.stdout = output
        self.stderr = output


def _run_with_pty(cmd, answers, timeout=20):
    """Runs `cmd` with a real pty as its controlling terminal (like a human
    typing at a real terminal), writing each of `answers` in turn -- needed
    because install.sh's own `prompt()` reads from /dev/tty specifically
    (see its own comment: this is deliberate, so a curl-piped install still
    prompts interactively), not from stdin, so a plain piped-stdin
    subprocess can never drive it. `sudo`'s own `use_pty` Defaults (already
    set on this machine -- see /etc/sudoers) transparently relays this same
    pty through to the child bash process, so `[ -t 0 ]` and `/dev/tty`
    both resolve correctly inside the script being tested."""
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

    # Drain output until the pty closes (child exited and its fds were
    # released) or we time out -- never call waitpid more than once, to
    # avoid ECHILD on a pid the loop already reaped.
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


def _build_deploy_target_block_harness(config_dir):
    """Assembles a standalone runnable script out of install.sh's own
    helper functions (interactive/prompt/set_env/get_env) plus the literal
    --with-deploy-target block, unmodified, extracted straight from
    install.sh's real source -- so this test exercises install.sh's actual
    provisioning logic (loops, quoting, conditionals) rather than a
    reimplementation of it that could silently drift from the real file.
    Only CONFIG_DIR/REPO_DIR/WITH_DEPLOY_TARGET are supplied as harness
    inputs (env vars), exactly like install.sh's own top-of-file variables
    -- the block's other paths (/home/deploy, /usr/local/bin/ai-dev-
    switchboard-deploy-*.sh, /etc/sudoers.d/ai-dev-switchboard-deploy-
    target) are intentionally left as the real hardcoded absolute paths a
    real install would use, since that's what "deploy" being a real system
    user backed by a real forced-command SSH restriction actually requires
    -- verified for real in PrivilegedEndToEndTests below, just under a
    different (non-colliding) username."""
    with open(INSTALL_SH) as f:
        source = f.read()
    helpers = _extract_between(source, "interactive() {", "random_token() {")
    block = _extract_between(
        source,
        "# ── Optional: deploy-target receiver",
        'echo "== Done =="')
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        YES=0
        CONFIG_DIR={config_dir}
        REPO_DIR={REPO_ROOT}
        WITH_DEPLOY_TARGET=1
        {helpers}
        {block}
        """)


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to run install.sh's own "
                     "--with-deploy-target block for real (useradd deploy, "
                     "sudoers, /usr/local/bin installs)")
class InstallScriptDeployTargetBlockTests(unittest.TestCase):
    """Runs install.sh's actual --with-deploy-target block (extracted
    verbatim, see _build_deploy_target_block_harness above) as root, with
    controlled pty-fed answers (see _run_with_pty) -- proving the acceptance criteria that are
    specifically about *install.sh's own provisioning*, not just the two
    scripts it installs: the real "deploy" user/path/env-file/authorized_
    keys/sudoers shape, blank-pubkey handling, and re-run-with-different-
    values not accumulating stale entries. Uses the literal "deploy"
    username (matching docs/spec.md's acceptance criteria wording exactly)
    -- safe here because setUp asserts the account doesn't already exist
    before creating it, and tearDown always removes it again.
    """

    def setUp(self):
        if subprocess.run(["id", "deploy"], capture_output=True).returncode == 0:
            self.skipTest("a real 'deploy' user already exists on this "
                          "machine -- refusing to touch it")
        self.config_dir = tempfile.mkdtemp(prefix="aidswb-cfgdir-")
        self.addCleanup(lambda: shutil.rmtree(self.config_dir, ignore_errors=True))
        self.deploy_paths = []

    def tearDown(self):
        subprocess.run(["sudo", "pkill", "-9", "-u", "deploy"], capture_output=True)
        subprocess.run(["sudo", "userdel", "-r", "deploy"], capture_output=True)
        subprocess.run(["sudo", "rm", "-f",
                       "/etc/sudoers.d/ai-dev-switchboard-deploy-target"])
        subprocess.run(["sudo", "rm", "-f",
                       "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",
                       "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh"])
        for p in self.deploy_paths:
            subprocess.run(["sudo", "rm", "-rf", p])

    def run_block(self, config_dir, path="", service="", pubkey=""):
        script = _build_deploy_target_block_harness(config_dir)
        return _run_with_pty(["sudo", "-n", "bash", "-c", script], [path, service, pubkey])

    def test_full_run_provisions_user_path_env_keys_sudoers(self):
        deploy_path = tempfile.mkdtemp(prefix="aidswb-deploypath-")
        os.chmod(deploy_path, 0o755)
        self.deploy_paths.append(deploy_path)
        keypath = tempfile.mktemp(prefix="aidswb-key-")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", keypath, "-N", "", "-q"],
                       check=True)
        with open(keypath + ".pub") as f:
            pubkey = f.read().strip()
        self.addCleanup(lambda: (os.remove(keypath), os.remove(keypath + ".pub")))

        r = self.run_block(self.config_dir, path=deploy_path,
                           service="myapp.service", pubkey=pubkey)
        self.assertEqual(r.returncode, 0, r.stderr)

        # A "deploy" system user with shell /bin/sh.
        pw = subprocess.run(["getent", "passwd", "deploy"], capture_output=True,
                            text=True, check=True).stdout.strip()
        self.assertTrue(pw.endswith(":/bin/sh"), pw)

        # DEPLOY_PATH exists, owned by deploy:deploy.
        st = subprocess.run(["stat", "-c", "%U:%G", deploy_path], capture_output=True,
                            text=True, check=True).stdout.strip()
        self.assertEqual(st, "deploy:deploy")

        # deploy-target.env has both keys.
        env_content = subprocess.run(["sudo", "cat",
                                     os.path.join(self.config_dir, "deploy-target.env")],
                                     capture_output=True, text=True, check=True).stdout
        self.assertIn(f"DEPLOY_PATH={deploy_path}", env_content)
        self.assertIn("DEPLOY_SERVICE_NAME=myapp.service", env_content)

        # authorized_keys has exactly one line, the exact forced-command
        # restriction (docs/spec.md, verbatim).
        ak = subprocess.run(["sudo", "cat", "/home/deploy/.ssh/authorized_keys"],
                            capture_output=True, text=True, check=True).stdout
        lines = [l for l in ak.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, ak)
        self.assertEqual(
            lines[0],
            'command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",'
            f'no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty {pubkey}')
        ak_mode = subprocess.run(["sudo", "stat", "-c", "%a", "/home/deploy/.ssh/authorized_keys"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(ak_mode, "600")

        # sudoers: exactly the deploy-restart.sh grant, zero arguments,
        # passes visudo -cf.
        sudoers_path = "/etc/sudoers.d/ai-dev-switchboard-deploy-target"
        v = subprocess.run(["sudo", "visudo", "-cf", sudoers_path],
                           capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stderr)
        sudoers_content = subprocess.run(["sudo", "cat", sudoers_path],
                                        capture_output=True, text=True,
                                        check=True).stdout
        self.assertEqual(
            sudoers_content.strip(),
            "deploy ALL=(root) NOPASSWD: "
            "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh")

    def test_blank_pubkey_leaves_authorized_keys_untouched_prints_instructions(self):
        deploy_path = tempfile.mkdtemp(prefix="aidswb-deploypath-")
        os.chmod(deploy_path, 0o755)
        self.deploy_paths.append(deploy_path)

        r = self.run_block(self.config_dir, path=deploy_path,
                           service="myapp.service", pubkey="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("add one by hand later", r.stdout.lower())

        # user/path/scripts/sudoers still fully provisioned...
        self.assertEqual(
            subprocess.run(["id", "deploy"], capture_output=True).returncode, 0)
        self.assertTrue(os.path.exists(
            "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh"))
        self.assertTrue(os.path.exists(
            "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh"))
        self.assertEqual(subprocess.run(
            ["sudo", "visudo", "-cf", "/etc/sudoers.d/ai-dev-switchboard-deploy-target"],
            capture_output=True).returncode, 0)

        # ...but authorized_keys is untouched (empty on a fresh install).
        r2 = subprocess.run(["sudo", "test", "-s", "/home/deploy/.ssh/authorized_keys"])
        self.assertNotEqual(r2.returncode, 0,
                            "authorized_keys must stay empty when no pubkey is supplied")

    def test_rerun_with_different_values_overwrites_not_accumulates(self):
        deploy_path_1 = tempfile.mkdtemp(prefix="aidswb-deploypath1-")
        deploy_path_2 = tempfile.mkdtemp(prefix="aidswb-deploypath2-")
        os.chmod(deploy_path_1, 0o755)
        os.chmod(deploy_path_2, 0o755)
        self.deploy_paths += [deploy_path_1, deploy_path_2]

        key1 = tempfile.mktemp(prefix="aidswb-key1-")
        key2 = tempfile.mktemp(prefix="aidswb-key2-")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key1, "-N", "", "-q"], check=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key2, "-N", "", "-q"], check=True)
        with open(key1 + ".pub") as f:
            pubkey1 = f.read().strip()
        with open(key2 + ".pub") as f:
            pubkey2 = f.read().strip()
        self.addCleanup(lambda: [os.remove(p) for p in
                                (key1, key1 + ".pub", key2, key2 + ".pub")])

        r1 = self.run_block(self.config_dir, path=deploy_path_1,
                            service="first.service", pubkey=pubkey1)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = self.run_block(self.config_dir, path=deploy_path_2,
                            service="second.service", pubkey=pubkey2)
        self.assertEqual(r2.returncode, 0, r2.stderr)

        env_content = subprocess.run(["sudo", "cat",
                                     os.path.join(self.config_dir, "deploy-target.env")],
                                     capture_output=True, text=True, check=True).stdout
        self.assertIn(f"DEPLOY_PATH={deploy_path_2}", env_content)
        self.assertIn("DEPLOY_SERVICE_NAME=second.service", env_content)
        self.assertNotIn(deploy_path_1, env_content)
        self.assertNotIn("first.service", env_content)
        # Only one DEPLOY_PATH= line -- no accumulation.
        self.assertEqual(
            len([l for l in env_content.splitlines() if l.startswith("DEPLOY_PATH=")]), 1)

        ak = subprocess.run(["sudo", "cat", "/home/deploy/.ssh/authorized_keys"],
                            capture_output=True, text=True, check=True).stdout
        lines = [l for l in ak.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, ak)
        self.assertIn(pubkey2, lines[0])
        self.assertNotIn(pubkey1, lines[0])

    def test_combined_with_host_control_no_conflicting_state(self):
        # docs/spec.md acceptance criteria: --with-host-control and
        # --with-deploy-target in the same invocation must both provision
        # successfully with no conflicting user/file/sudoers state. The two
        # blocks touch entirely disjoint files (different user, different
        # config file, different sudoers file -- see install.sh's own
        # comment above the deploy-target block), so this proves that by
        # running the real host-control block's own file-copy steps
        # alongside the deploy-target block in the same script and
        # confirming both leave distinct, uncorrupted state.
        deploy_path = tempfile.mkdtemp(prefix="aidswb-deploypath-")
        os.chmod(deploy_path, 0o755)
        self.deploy_paths.append(deploy_path)
        install_dir = tempfile.mkdtemp(prefix="aidswb-installdir-")
        self.addCleanup(lambda: subprocess.run(["sudo", "rm", "-rf", install_dir]))
        # The host-control block symlinks these into /usr/local/bin
        # unconditionally (see install.sh's own WITH_HOST_CONTROL block) --
        # clean them up too, same as the deploy-target scripts' own
        # cleanup in tearDown.
        self.addCleanup(lambda: subprocess.run([
            "sudo", "rm", "-f",
            "/usr/local/bin/ai-dev-switchboard-host-start.sh",
            "/usr/local/bin/ai-dev-switchboard-host-stop.sh",
            "/usr/local/bin/ai-dev-switchboard-host-status.sh"]))

        with open(INSTALL_SH) as f:
            source = f.read()
        host_control_block = _extract_between(
            source, 'echo "-- Host-control agent', 'fi\n\n# ── Optional: deploy-target')
        helpers = _extract_between(source, "interactive() {", "random_token() {")
        deploy_block = _extract_between(
            source, "# ── Optional: deploy-target receiver", 'echo "== Done =="')

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            YES=0
            CONFIG_DIR={self.config_dir}
            REPO_DIR={REPO_ROOT}
            INSTALL_DIR={install_dir}
            WITH_HOST_CONTROL=1
            WITH_DEPLOY_TARGET=1
            {helpers}
            mkdir -p "$CONFIG_DIR"
            {host_control_block}
            {deploy_block}
            """)
        r = _run_with_pty(["sudo", "-n", "bash", "-c", script],
                         [deploy_path, "myapp.service", ""])
        self.assertEqual(r.returncode, 0, r.stderr)

        # host-control's own files landed, untouched by the deploy-target
        # block that ran right after it in the same script.
        self.assertTrue(os.path.exists(
            os.path.join(install_dir, "host-agent", "host-start.sh")))
        self.assertTrue(os.path.exists(os.path.join(self.config_dir, "host.env")))
        # deploy-target's own files landed too, untouched by host-control.
        self.assertEqual(
            subprocess.run(["id", "deploy"], capture_output=True).returncode, 0)
        self.assertTrue(os.path.exists(
            "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh"))


AUTHORIZED_KEYS_TEMPLATE = (
    'command="/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",'
    'no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty {pubkey}\n')
SUDOERS_TEMPLATE = ("{user} ALL=(root) NOPASSWD: "
                    "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh\n")


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to provision a real throwaway "
                     "system user, sudoers rule, and systemd unit")
@unittest.skipUnless(HAVE_SSHD_ON_LOCALHOST,
                     "needs a real sshd reachable on 127.0.0.1:22 to exercise "
                     "the forced-command restriction for real")
class PrivilegedEndToEndTests(unittest.TestCase):
    """Provisions a real (throwaway) receiver on this machine -- a system
    user, the two installed scripts, a config file, a sudoers rule, and a
    real systemd unit to restart -- using the exact line shapes install.sh
    generates (see InstallShTemplateTests), then exercises it entirely over
    real `ssh`/`rsync` against 127.0.0.1, matching docs/spec.md's
    "Acceptance criteria" one for one. Never uses the literal username
    "deploy" (to avoid colliding with a real deploy account on whatever box
    runs this suite); the scripts' own logic doesn't care what the
    account's name is, only DEPLOY_PATH/DEPLOY_SERVICE_NAME and the fixed
    /usr/local/bin paths, which this class wires up identically to a real
    install.

    Class-scoped (not per-test) setUp/tearDown: provisioning a system user
    + sshd round trips is comparatively expensive, and none of these tests
    mutate the receiver's own identity (user/keys/scripts/sudoers) --
    only DEPLOY_PATH's contents and deploy-target.env's values, which each
    test resets itself.
    """

    TEST_USER = "aidswbtest"

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="aidswb-deploy-target-test-")
        os.chmod(cls.base, 0o755)  # must be traversable by TEST_USER too
        cls.deploy_path = os.path.join(cls.base, "dest")
        cls.key = os.path.join(cls.base, "id_ed25519")
        cls.service_name = "aidswbtest.service"
        cls.bad_service_name = "aidswbtest-nonexistent.service"

        subprocess.run(["sudo", "useradd", "-m", "-d", f"/home/{cls.TEST_USER}",
                       "-s", "/bin/sh", cls.TEST_USER], check=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", cls.key, "-N", "", "-q"],
                       check=True)
        with open(cls.key + ".pub") as f:
            cls.pubkey = f.read().strip()

        subprocess.run(["sudo", "mkdir", "-p", cls.deploy_path], check=True)
        subprocess.run(["sudo", "chown", f"{cls.TEST_USER}:{cls.TEST_USER}",
                       cls.deploy_path], check=True)

        subprocess.run(["sudo", "mkdir", "-p", f"/home/{cls.TEST_USER}/.ssh"], check=True)
        subprocess.run(["sudo", "bash", "-c",
                       f"cat > /home/{cls.TEST_USER}/.ssh/authorized_keys"],
                       input=AUTHORIZED_KEYS_TEMPLATE.format(pubkey=cls.pubkey),
                       text=True, check=True)
        subprocess.run(["sudo", "chown", "-R", f"{cls.TEST_USER}:{cls.TEST_USER}",
                       f"/home/{cls.TEST_USER}/.ssh"], check=True)
        subprocess.run(["sudo", "chmod", "700", f"/home/{cls.TEST_USER}/.ssh"], check=True)
        subprocess.run(["sudo", "chmod", "600",
                       f"/home/{cls.TEST_USER}/.ssh/authorized_keys"], check=True)

        subprocess.run(["sudo", "install", "-m", "755", WRAPPER,
                       "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh"], check=True)
        subprocess.run(["sudo", "install", "-m", "755", RESTART,
                       "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh"], check=True)

        subprocess.run(["sudo", "mkdir", "-p", "/etc/ai-dev-switchboard"], check=True)
        cls._write_config(cls.deploy_path, cls.service_name)

        cls.sudoers_file = "/etc/sudoers.d/ai-dev-switchboard-deploy-target-test"
        subprocess.run(["sudo", "bash", "-c", f"cat > {cls.sudoers_file}"],
                       input=SUDOERS_TEMPLATE.format(user=cls.TEST_USER),
                       text=True, check=True)
        subprocess.run(["sudo", "chmod", "440", cls.sudoers_file], check=True)
        v = subprocess.run(["sudo", "visudo", "-cf", cls.sudoers_file],
                           capture_output=True, text=True)
        assert v.returncode == 0, v.stderr

        cls.unit_file = f"/etc/systemd/system/{cls.service_name}"
        subprocess.run(["sudo", "bash", "-c", f"cat > {cls.unit_file}"], input=(
            "[Unit]\nDescription=ai-dev-switchboard deploy-target test service\n\n"
            "[Service]\nType=simple\nExecStart=/bin/sleep infinity\n\n"
            "[Install]\nWantedBy=multi-user.target\n"), text=True, check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "start", cls.service_name], check=True)

    @classmethod
    def _write_config(cls, deploy_path, service_name):
        lines = []
        if deploy_path is not None:
            lines.append(f"DEPLOY_PATH={deploy_path}")
        if service_name is not None:
            lines.append(f"DEPLOY_SERVICE_NAME={service_name}")
        subprocess.run(["sudo", "bash", "-c", f"cat > {CONFIG_FILE}"],
                       input="\n".join(lines) + "\n", text=True, check=True)

    def setUp(self):
        # Reset config to the known-good values before every test, in case
        # an earlier test in this class deliberately broke it.
        self._write_config(self.deploy_path, self.service_name)
        subprocess.run(["sudo", "bash", "-c", f"rm -rf {self.deploy_path}/*"], check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["sudo", "systemctl", "stop", cls.service_name],
                       capture_output=True)
        subprocess.run(["sudo", "rm", "-f", cls.unit_file])
        subprocess.run(["sudo", "systemctl", "daemon-reload"])
        subprocess.run(["sudo", "rm", "-f", cls.sudoers_file])
        subprocess.run(["sudo", "rm", "-rf", "/etc/ai-dev-switchboard"])
        subprocess.run(["sudo", "rm", "-f",
                       "/usr/local/bin/ai-dev-switchboard-deploy-wrapper.sh",
                       "/usr/local/bin/ai-dev-switchboard-deploy-restart.sh"])
        # Kill any lingering sessions before userdel (a still-open ssh
        # ControlMaster/session pins the account and makes userdel -r fail).
        subprocess.run(["sudo", "pkill", "-9", "-u", cls.TEST_USER], capture_output=True)
        time.sleep(0.3)
        subprocess.run(["sudo", "userdel", "-r", cls.TEST_USER], capture_output=True)
        shutil.rmtree(cls.base, ignore_errors=True)

    def _ssh(self, *command):
        base = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "BatchMode=yes", "-i", self.key,
               f"{self.TEST_USER}@127.0.0.1"]
        return subprocess.run([*base, *command], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=20)

    def _rsync(self, source, dest_suffix=""):
        return subprocess.run(
            ["rsync", "-e",
             f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             f"-o BatchMode=yes -i {self.key}",
             "-a", source, f"{self.TEST_USER}@127.0.0.1:{dest_suffix}"],
            capture_output=True, text=True, timeout=20)

    # -- Acceptance criterion: push lands under DEPLOY_PATH, owned by the
    # receiver user, no shell/tty ever allocated.
    def test_push_lands_under_deploy_path_owned_by_receiver_user(self):
        src = os.path.join(self.base, "somefile")
        with open(src, "w") as f:
            f.write("hello\n")
        r = self._rsync(src)
        self.assertEqual(r.returncode, 0, r.stderr)

        dest = os.path.join(self.deploy_path, "somefile")
        st = subprocess.run(["sudo", "stat", "-c", "%U", dest],
                            capture_output=True, text=True, check=True)
        self.assertEqual(st.stdout.strip(), self.TEST_USER)
        content = subprocess.run(["sudo", "cat", dest], capture_output=True,
                                 text=True, check=True)
        self.assertEqual(content.stdout, "hello\n")

    # -- Acceptance criterion: escape attempt outside DEPLOY_PATH is
    # refused, nothing is written outside it.
    def test_path_escape_attempt_rejected_no_file_written_outside(self):
        # rrsync's actual defense isn't a hard error here: any destination
        # arg starting with "/" gets re-rooted underneath DEPLOY_PATH
        # (rrsync prepends its restricted dir rather than treating the
        # client-supplied absolute path as real) -- so this rsync call
        # itself reports success, but the security property that actually
        # matters -- nothing is EVER written outside DEPLOY_PATH -- holds:
        # the file lands at DEPLOY_PATH/etc/escapefile, never at the real
        # /etc/escapefile. Verified directly against real rrsync (not
        # asserted from reading its source) -- see docs/implementation.md
        # "Deviations from spec" for why this test doesn't assert a
        # non-zero exit the way the literal acceptance-criteria wording
        # ("rrsync refuses") might suggest.
        src = os.path.join(self.base, "escapefile")
        with open(src, "w") as f:
            f.write("nope\n")
        r = self._rsync(src, "/etc/")
        self.assertFalse(os.path.exists("/etc/escapefile"),
                         "must never land at the real /etc/ -- this is the "
                         "actual security property, regardless of rsync's exit code")
        # Confirm it landed safely re-rooted inside DEPLOY_PATH instead of
        # silently vanishing -- proves rrsync's remapping is what happened,
        # not some other unrelated failure that coincidentally didn't
        # write to /etc/.
        reroot = subprocess.run(["sudo", "test", "-f",
                                os.path.join(self.deploy_path, "etc", "escapefile")])
        self.assertEqual(reroot.returncode, 0,
                         "expected the file to land re-rooted at "
                         "DEPLOY_PATH/etc/escapefile")

    # -- Acceptance criterion: any command other than the rsync protocol
    # invocation or the literal "deploy-restart" is rejected -- no shell,
    # no output, no pty.
    def test_arbitrary_command_rejected_no_shell(self):
        r = self._ssh("whoami")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(self.TEST_USER, r.stdout)

    def test_bare_interactive_attempt_rejected_no_shell(self):
        r = self._ssh()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    # -- Acceptance criterion: deploy-restart genuinely restarts the named
    # service, with no sudo password prompt.
    def test_deploy_restart_actually_restarts_service_no_password_prompt(self):
        before = subprocess.run(
            ["sudo", "systemctl", "show", "-p", "ActiveEnterTimestamp", self.service_name],
            capture_output=True, text=True, check=True).stdout.strip()
        time.sleep(1.1)  # ActiveEnterTimestamp has 1-second resolution
        r = self._ssh("deploy-restart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("password", r.stderr.lower())
        after = subprocess.run(
            ["sudo", "systemctl", "show", "-p", "ActiveEnterTimestamp", self.service_name],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertNotEqual(before, after)

    # -- Acceptance criterion: a nonexistent/misconfigured service surfaces
    # a non-zero exit over SSH, not swallowed.
    def test_restart_of_nonexistent_service_fails_loudly(self):
        self._write_config(self.deploy_path, self.bad_service_name)
        try:
            r = self._ssh("deploy-restart")
            self.assertNotEqual(r.returncode, 0)
        finally:
            self._write_config(self.deploy_path, self.service_name)

    # -- Acceptance criterion: unset/malformed DEPLOY_PATH fails closed
    # rather than falling through to an unrestricted rrsync call.
    def test_unset_deploy_path_fails_closed_over_real_ssh(self):
        self._write_config(None, self.service_name)
        try:
            src = os.path.join(self.base, "shouldnotland")
            with open(src, "w") as f:
                f.write("x\n")
            r = self._rsync(src)
            self.assertNotEqual(r.returncode, 0)
        finally:
            self._write_config(self.deploy_path, self.service_name)

    def test_malformed_relative_deploy_path_fails_closed_over_real_ssh(self):
        self._write_config("relative/path", self.service_name)
        try:
            src = os.path.join(self.base, "shouldnotland2")
            with open(src, "w") as f:
                f.write("x\n")
            r = self._rsync(src)
            self.assertNotEqual(r.returncode, 0)
        finally:
            self._write_config(self.deploy_path, self.service_name)

    # -- Acceptance criterion: the sudoers grant can't be used to run
    # anything else -- exactly one NOPASSWD entry, naming exactly this
    # script, no arguments.
    def test_sudoers_grant_scoped_to_exactly_one_zero_arg_script(self):
        r = subprocess.run(["sudo", "-n", "-u", self.TEST_USER, "sudo", "-n", "-l"],
                           capture_output=True, text=True, check=True)
        self.assertIn(
            "(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-deploy-restart.sh",
            r.stdout)
        # No other NOPASSWD grant for this user.
        nopasswd_lines = [l for l in r.stdout.splitlines() if "NOPASSWD" in l]
        self.assertEqual(len(nopasswd_lines), 1, r.stdout)

    def test_sudo_cannot_be_used_to_run_arbitrary_root_command(self):
        r = subprocess.run(["sudo", "-n", "-u", self.TEST_USER, "sudo", "-n", "whoami"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
