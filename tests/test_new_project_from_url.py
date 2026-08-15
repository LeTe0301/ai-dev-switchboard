#!/usr/bin/env python3
"""
Tests for scripts/new-project-from-url.sh -- clone_project_from_url()'s
privileged hand-off (backlog item 16, docs/spec.md). Mirrors
tests/test_new_project_from_gitea.py's own approach closely (same real
`git http-backend`-backed local HTTP server technique, same
unprivileged-vs-sudo-gated split), adapted for this script's own
differences: no embedded credential (a plain public-repo clone), the
DEVIATION 1 "always remove DEST on failure" rollback behavior (the sibling
script's own test suite asserts the opposite -- DEST left in place), and
the new post-clone CLONE_MAX_BYTES size-cap rollback.

Argument-validation cases (wrong arg count, invalid name/URL) run without
any elevated privilege -- they fail before the script's mkdir/chown/su/
git-clone steps. The rest need root (mkdir under an arbitrary PROJECTS_DIR +
chown + `su "$RUN_USER"` all behave like the real deployment only when
actually run as root) -- gated on passwordless sudo being available and
skipped otherwise, same "skip cleanly rather than fail on an environment
that can't support it" spirit as the sibling test file.

Run with:
    python3 -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
SCRIPT = os.path.join(REPO_ROOT, "scripts", "new-project-from-url.sh")

HAVE_PASSWORDLESS_SUDO = subprocess.run(
    ["sudo", "-n", "true"], capture_output=True).returncode == 0
HAVE_GIT = shutil.which("git") is not None
RUN_USER = os.environ.get("USER") or subprocess.run(
    ["id", "-un"], capture_output=True, text=True).stdout.strip()

# Generous but bounded -- if the script's own GIT_TERMINAL_PROMPT=0/
# GIT_ASKPASS=/bin/false/BatchMode=yes fast-fail plumbing ever regresses
# into an interactive hang, these tests fail loudly instead of hanging the
# whole suite indefinitely.
PRIVILEGED_TEST_TIMEOUT = 30


def run_unprivileged(args, **kwargs):
    """Runs the script directly (no sudo) -- only valid for the
    argument-validation paths that fail before touching PROJECTS_DIR."""
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=PRIVILEGED_TEST_TIMEOUT, **kwargs)


def run_privileged(args, projects_dir, run_user=RUN_USER, clone_max_bytes=None):
    """Runs the script as root via sudo (matching real deployment), with
    RUN_USER/PROJECTS_DIR/CLONE_MAX_BYTES passed through `env` (sudo resets
    the environment by default, so plain os.environ overrides wouldn't
    reach the child) -- mirrors run_privileged() in
    test_new_project_from_gitea.py."""
    env_args = [f"RUN_USER={run_user}", f"PROJECTS_DIR={projects_dir}"]
    if clone_max_bytes is not None:
        env_args.append(f"CLONE_MAX_BYTES={clone_max_bytes}")
    return subprocess.run(["sudo", "env", *env_args, "bash", SCRIPT, *args],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=PRIVILEGED_TEST_TIMEOUT)


class _GitHttpBackendHandler(BaseHTTPRequestHandler):
    """Real smart-HTTP git server for exactly one bare repo, implemented by
    shelling out to the actual `git http-backend` CGI program for every
    request -- same technique test_new_project_from_gitea.py's own handler
    uses, minus the Basic-auth check (this script's clone URL never carries
    a credential -- see docs/spec.md's own note on that)."""
    project_root = None

    def _run_backend(self, method):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        path, _, query = self.path.partition("?")
        env = dict(os.environ)
        env.update({
            "GIT_PROJECT_ROOT": self.project_root,
            "GIT_HTTP_EXPORT_ALL": "1",
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(length),
        })
        result = subprocess.run(["git", "http-backend"], input=body, env=env,
                                capture_output=True)
        head, sep, rest = result.stdout.partition(b"\r\n\r\n")
        if not sep:
            head, sep, rest = result.stdout.partition(b"\n\n")
        status = 200
        headers = []
        for line in head.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            k, _, v = line.partition(b":")
            k, v = k.strip().decode(), v.strip().decode()
            if k.lower() == "status":
                status = int(v.split()[0])
            else:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(rest)))
        self.end_headers()
        self.wfile.write(rest)

    def do_GET(self):
        self._run_backend("GET")

    def do_POST(self):
        self._run_backend("POST")

    def log_message(self, fmt, *args):
        pass  # keep test output quiet


class _AuthRequiredHandler(BaseHTTPRequestHandler):
    """Always demands Basic auth and never accepts any credential -- stands
    in for a private HTTPS repo this cycle deliberately doesn't support
    (docs/spec.md "Private-repo auth"), to prove the script fails fast
    instead of hanging on an interactive prompt."""

    def _reject(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="git"')
        self.end_headers()

    def do_GET(self):
        self._reject()

    def do_POST(self):
        self._reject()

    def log_message(self, fmt, *args):
        pass


class ArgumentValidationTests(unittest.TestCase):
    """These fail before any privileged operation, so they run as-is."""

    def test_wrong_arg_count_rejected(self):
        r = run_unprivileged(["only-one"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Usage:", r.stderr)

    def test_invalid_name_rejected(self):
        r = run_unprivileged(["https://example.com/repo.git", "../escape"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Invalid project name", r.stderr)

    def test_name_starting_with_symbol_rejected(self):
        r = run_unprivileged(["https://example.com/repo.git", "-rf"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Invalid project name", r.stderr)

    def test_file_scheme_url_rejected(self):
        r = run_unprivileged(["file:///etc/passwd", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_git_scheme_url_rejected(self):
        r = run_unprivileged(["git://example.com/repo.git", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_argument_injection_shaped_url_rejected(self):
        r = run_unprivileged(["-oProxyCommand=touch /tmp/pwned", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_ssh_scheme_argument_injection_shaped_url_rejected(self):
        # test-review.md item 16 must-fix: an ssh://-oProxyCommand=... host
        # must be rejected by this script's own bash-regex re-validation,
        # not left to depend on installed git's own hostname-shape guard.
        r = run_unprivileged(["ssh://-oProxyCommand=id", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_scp_like_argument_injection_shaped_url_rejected(self):
        r = run_unprivileged(["user@-oProxyCommand=id:path", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_ssh_scheme_userat_prefix_hides_malicious_host_rejected(self):
        # docs/test-review.md item 16 re-review, repro 1 (exact URL): a
        # 'user@' prefix hiding a malicious host after 'ssh://' must be
        # rejected by this script's own bash pattern-matching, not left to
        # depend on installed git's own hostname-shape guard.
        r = run_unprivileged(
            ["ssh://user@-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_scp_like_malicious_path_after_colon_rejected(self):
        # docs/test-review.md item 16 re-review, repro 2 (exact URL): a
        # leading '-' right after the ':' path separator (host itself is
        # benign) must also be rejected.
        r = run_unprivileged(
            ["user@127.0.0.1:-oProxyCommand=touch${IFS}/tmp/npfu-bypass-marker", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_ssh_scheme_double_at_hides_malicious_host_rejected(self):
        r = run_unprivileged(
            ["ssh://real@decoy@-oProxyCommand=touch${IFS}/tmp/x/repo", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_scp_like_empty_host_rejected(self):
        r = run_unprivileged(["user@:path", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)

    def test_bracketed_ipv6_host_accepted_by_validation(self):
        r = run_unprivileged(["ssh://user@[::1]:22/repo", "validname"],
                             env={**os.environ, "PROJECTS_DIR": "/nonexistent/root/only"})
        self.assertNotIn("Unsupported URL", r.stderr)

    def test_https_url_accepted_by_validation(self):
        # Doesn't actually clone (no sudo here) -- fails later at mkdir
        # under an unwritable PROJECTS_DIR default, but must get PAST the
        # URL-validation check specifically, proving that check itself
        # accepts a well-formed https:// URL.
        r = run_unprivileged(["https://example.com/repo.git", "validname"],
                             env={**os.environ, "PROJECTS_DIR": "/nonexistent/root/only"})
        self.assertNotIn("Unsupported URL", r.stderr)

    def test_scp_like_url_accepted_by_validation(self):
        r = run_unprivileged(["git@example.com:user/repo.git", "validname"],
                             env={**os.environ, "PROJECTS_DIR": "/nonexistent/root/only"})
        self.assertNotIn("Unsupported URL", r.stderr)


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to exercise the real "
                     "mkdir/chown/su/git-clone privileged hand-off")
@unittest.skipUnless(HAVE_GIT, "needs a real `git` binary")
class PrivilegedCloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="npfu-")
        self.addCleanup(lambda: subprocess.run(["sudo", "rm", "-rf", self.tmp]))
        self.projects_dir = os.path.join(self.tmp, "projects")
        self.project_root = os.path.join(self.tmp, "git-root")
        self.bare_repo = os.path.join(self.project_root, "o", "r.git")

        # A real bare repo with one commit, served by the real
        # `git http-backend` CGI wired up above -- exercises a genuine
        # `git clone` over smart HTTP, not a mock.
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        with open(os.path.join(src, "README.md"), "w") as f:
            f.write("hello from url clone\n")
        subprocess.run(["git", "init", "-q", "-b", "main", src], check=True)
        subprocess.run(["git", "-C", src, "-c", "user.name=x", "-c", "user.email=x@x",
                       "add", "-A"], check=True)
        subprocess.run(["git", "-C", src, "-c", "user.name=x", "-c", "user.email=x@x",
                       "commit", "-q", "-m", "initial"], check=True)
        os.makedirs(os.path.dirname(self.bare_repo))
        subprocess.run(["git", "clone", "-q", "--bare", src, self.bare_repo], check=True)

        handler = type("Handler", (_GitHttpBackendHandler,), {
            "project_root": self.project_root,
        })
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}/o/r.git"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_clones_real_public_repo_owned_by_run_user(self):
        r = run_privileged([self.url, "myproj"], self.projects_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ready:", r.stdout)

        dest = os.path.join(self.projects_dir, "myproj")
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))
        with open(os.path.join(dest, "README.md")) as f:
            self.assertEqual(f.read(), "hello from url clone\n")

        st = os.stat(dest)
        self.assertEqual(st.st_uid, os.stat(self.tmp).st_uid)

    def test_already_existing_target_fails_atomically_no_dash_p(self):
        r1 = run_privileged([self.url, "dup"], self.projects_dir)
        self.assertEqual(r1.returncode, 0, r1.stderr)

        r2 = run_privileged([self.url, "dup"], self.projects_dir)
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("Already exists", r2.stderr)

    def test_name_with_space_and_hyphen_accepted(self):
        r = run_privileged([self.url, "my project-1"], self.projects_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.projects_dir, "my project-1")))

    def test_ssh_argument_injection_shape_rejected_before_any_subprocess(self):
        # test-review.md item 16 must-fix, verified end to end via the real
        # sudo-privileged path: a crafted ssh://-oProxyCommand=... URL must
        # be rejected by this script's own bash-regex re-validation, never
        # reach `git clone`/`ssh` as an argv token, and must never create
        # PROJECTS_DIR/<name> at all (not even a partial one for the
        # cleanup trap to remove).
        marker = os.path.join(self.tmp, "pwned-marker")
        url = "ssh://-oProxyCommand=touch${IFS}" + marker
        r = run_privileged([url, "advtest"], self.projects_dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.isdir(os.path.join(self.projects_dir, "advtest")))

    def test_scp_like_malicious_path_after_colon_rejected_before_any_subprocess(self):
        # docs/test-review.md item 16 re-review, repro 2, verified end to
        # end via the real sudo-privileged path (exact URL from the
        # re-review, benign host / malicious path immediately after ':').
        marker = os.path.join(self.tmp, "pwned-marker-scp")
        url = "user@127.0.0.1:-oProxyCommand=touch${IFS}" + marker
        r = run_privileged([url, "advtest2"], self.projects_dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.isdir(os.path.join(self.projects_dir, "advtest2")))

    def test_ssh_userat_prefix_hides_malicious_host_rejected_before_any_subprocess(self):
        # docs/test-review.md item 16 re-review, repro 1, verified end to
        # end (exact URL from the re-review: 'user@' prefix hiding the
        # malicious host after 'ssh://').
        marker = os.path.join(self.tmp, "pwned-marker-userat")
        url = "ssh://user@-oProxyCommand=touch${IFS}" + marker
        r = run_privileged([url, "advtest3"], self.projects_dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.isdir(os.path.join(self.projects_dir, "advtest3")))

    def test_double_at_hides_malicious_host_rejected_before_any_subprocess(self):
        # Independently-constructed adversarial variant: a second '@'
        # inside the userinfo segment must not let a malicious host slip
        # past as if it were still "the userinfo".
        marker = os.path.join(self.tmp, "pwned-marker-doubleat")
        url = "ssh://real@decoy@-oProxyCommand=touch${IFS}" + marker
        r = run_privileged([url, "advtest4"], self.projects_dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported URL", r.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.isdir(os.path.join(self.projects_dir, "advtest4")))

    def test_clone_failure_removes_dest_deviation_from_gitea_sibling(self):
        # DEVIATION 1 (docs/spec.md): unlike new-project-from-gitea.sh
        # (which leaves a failed/partial DEST behind for manual cleanup),
        # this script always removes DEST on any failure below the mkdir.
        bad_url = f"http://127.0.0.1:{self.port}/o/nosuchrepo.git"
        r = run_privileged([bad_url, "failedclone"], self.projects_dir)
        self.assertNotEqual(r.returncode, 0)
        dest = os.path.join(self.projects_dir, "failedclone")
        self.assertFalse(os.path.isdir(dest))

    def test_oversized_clone_rolled_back_and_removed(self):
        # New (backlog item 16): post-clone du -sb check against
        # CLONE_MAX_BYTES -- the one-commit test repo above is tiny, so a
        # deliberately tiny CLONE_MAX_BYTES forces the rollback path.
        r = run_privileged([self.url, "toobig"], self.projects_dir, clone_max_bytes=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("byte limit", r.stderr)
        dest = os.path.join(self.projects_dir, "toobig")
        self.assertFalse(os.path.isdir(dest))

    def test_within_size_cap_succeeds(self):
        r = run_privileged([self.url, "smallenough"], self.projects_dir,
                           clone_max_bytes=524288000)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.projects_dir, "smallenough")))


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to exercise the real "
                     "mkdir/chown/su/git-clone privileged hand-off")
@unittest.skipUnless(HAVE_GIT, "needs a real `git` binary")
class PrivilegedAuthRequiredTests(unittest.TestCase):
    """Proves the non-hanging-failure-mode acceptance criterion: a private
    HTTPS repo (auth required, unsupported this cycle) fails fast instead
    of blocking on an interactive prompt (docs/spec.md "Edge cases")."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="npfu-auth-")
        self.addCleanup(lambda: subprocess.run(["sudo", "rm", "-rf", self.tmp]))
        self.projects_dir = os.path.join(self.tmp, "projects")
        self.server = HTTPServer(("127.0.0.1", 0), _AuthRequiredHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_auth_required_repo_fails_fast_not_hangs(self):
        url = f"http://127.0.0.1:{self.port}/private/repo.git"
        r = run_privileged([url, "privaterepo"], self.projects_dir)
        # The bounded PRIVILEGED_TEST_TIMEOUT above is itself part of this
        # assertion: if GIT_TERMINAL_PROMPT=0/GIT_ASKPASS=/bin/false ever
        # regressed, subprocess.run(..., timeout=...) would raise
        # TimeoutExpired instead of returning normally here.
        self.assertNotEqual(r.returncode, 0)
        dest = os.path.join(self.projects_dir, "privaterepo")
        self.assertFalse(os.path.isdir(dest))


if __name__ == "__main__":
    unittest.main()
