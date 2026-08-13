#!/usr/bin/env python3
"""
Tests for scripts/new-project-from-gitea.sh -- create_project()'s privileged
hand-off once Gitea's own API has already created the repo (see
docs/spec.md "backlog item 2b" -- "The privileged registration script").
Invokes the real script via subprocess (no mocking of bash itself), same
"verify it for real" convention tests/test_new_project_from_upload.py
already uses for its own privileged script.

Argument-validation cases (wrong arg count, invalid name/owner/repo, missing
GITEA_API_TOKEN) run without any elevated privilege -- they fail before the
script's mkdir/chown/su/git-clone steps. The rest need root (mkdir under an
arbitrary PROJECTS_DIR + chown + `su "$RUN_USER"` all behave like the real
deployment only when actually run as root) -- gated on passwordless sudo
being available and skipped otherwise, same "skip cleanly rather than fail
on an environment that can't support it" spirit as
test_new_project_from_upload.py. GITEA_API_TOKEN is passed through
`sudo env` exactly like RUN_USER/PROJECTS_DIR already are (not via a real
/etc/ai-dev-switchboard/switchboard.env, which may not exist on the machine
running these tests) -- the script's `${GITEA_API_TOKEN:-}` read doesn't
care whether the value arrived via the sourced CONFIG file or via an
already-set environment variable that CONFIG (absent here) never
overwrites, so this exercises the real read path.

The privileged tests stand up a tiny real local HTTP server (stdlib-only,
no Docker/Gitea needed) that shells out to the real `git http-backend` CGI
program for every request, so `git clone
http://oauth2:<token>@127.0.0.1:<port>/o/r.git` speaks the real smart HTTP
protocol against real git on both ends, not a mock or a hand-rolled dumb-
protocol stand-in (modern git clients no longer fall back to the dumb HTTP
protocol on a 404, so a plain static-file server isn't enough here) --
following this repo's "no real Docker/network calls in tests" convention
while still proving the clone/credential-embedding/error-redaction logic
against real git.

Run with:
    python3 -m unittest discover -s tests -v
"""
import base64
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
SCRIPT = os.path.join(REPO_ROOT, "scripts", "new-project-from-gitea.sh")

HAVE_PASSWORDLESS_SUDO = subprocess.run(
    ["sudo", "-n", "true"], capture_output=True).returncode == 0
HAVE_GIT = shutil.which("git") is not None
RUN_USER = os.environ.get("USER") or subprocess.run(
    ["id", "-un"], capture_output=True, text=True).stdout.strip()


def run_unprivileged(args, gitea_api_token=None, **kwargs):
    """Runs the script directly (no sudo) -- only valid for the
    argument-validation paths that fail before touching PROJECTS_DIR."""
    env = dict(os.environ)
    if gitea_api_token is not None:
        env["GITEA_API_TOKEN"] = gitea_api_token
    else:
        env.pop("GITEA_API_TOKEN", None)
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=env, **kwargs)


def run_privileged(args, projects_dir, gitea_port, gitea_api_token,
                    run_user=RUN_USER):
    """
    Runs the script as root via sudo (matching real deployment), with
    RUN_USER/PROJECTS_DIR/GITEA_PORT/GITEA_API_TOKEN passed through `env`
    (sudo resets the environment by default, so plain os.environ overrides
    wouldn't reach the child) -- mirrors run_privileged() in
    test_new_project_from_upload.py, extended with the two Gitea-specific
    variables this script also reads.
    """
    env_args = [f"RUN_USER={run_user}", f"PROJECTS_DIR={projects_dir}",
               f"GITEA_PORT={gitea_port}", f"GITEA_API_TOKEN={gitea_api_token}"]
    return subprocess.run(["sudo", "env", *env_args, "bash", SCRIPT, *args],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


class _GitHttpBackendHandler(BaseHTTPRequestHandler):
    """Real smart-HTTP git server for exactly one bare repo, implemented by
    shelling out to the actual `git http-backend` CGI program for every
    request -- the same program real git hosting (including Gitea) uses
    under the hood. `project_root` (a directory containing
    `<owner>/<repo>.git`) is set per-test via subclassing, same pattern
    _fake_gitea_run style stateful fakes use elsewhere in this repo's test
    suite, just applied to a subprocess CGI program instead of a Python
    function. Requires the expected HTTP Basic Authorization header,
    matching the oauth2:<token>@ embedded-credential form this script's
    CLONE_URL uses -- checked here, in front of http-backend, not inside
    it."""
    project_root = None
    expected_token = None

    def _check_auth(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode()
        except Exception:
            return False
        return decoded == f"oauth2:{self.expected_token}"

    def _run_backend(self, method):
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.end_headers()
            return
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


class ArgumentValidationTests(unittest.TestCase):
    """These fail before any privileged operation, so they run as-is."""

    def test_wrong_arg_count_rejected(self):
        r = run_unprivileged(["only", "two"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Usage:", r.stderr)

    def test_invalid_name_rejected(self):
        r = run_unprivileged(["owner", "repo", "../escape"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Invalid project name", r.stderr)

    def test_name_starting_with_symbol_rejected(self):
        r = run_unprivileged(["owner", "repo", "-rf"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Invalid project name", r.stderr)

    def test_invalid_owner_rejected(self):
        r = run_unprivileged(["own/er", "repo", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Invalid Gitea owner", r.stderr)

    def test_invalid_repo_rejected(self):
        r = run_unprivileged(["owner", "re;po", "validname"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Invalid Gitea repo name", r.stderr)

    def test_missing_token_rejected(self):
        # Empty/unset GITEA_API_TOKEN must be rejected with a clear,
        # specific message pointing at the bootstrap script -- not a raw
        # clone failure -- matching create_project()'s own equivalent
        # check on the app.py side.
        r = run_unprivileged(["owner", "repo", "validname"], gitea_api_token=None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("GITEA_API_TOKEN", r.stderr)
        self.assertIn("gitea-configure-api.sh", r.stderr)


@unittest.skipUnless(HAVE_PASSWORDLESS_SUDO,
                     "needs passwordless sudo to exercise the real "
                     "mkdir/chown/su/git-clone privileged hand-off")
@unittest.skipUnless(HAVE_GIT, "needs a real `git` binary")
class PrivilegedRegistrationTests(unittest.TestCase):
    TOKEN = "testtoken1234567890"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="npfg-")
        self.addCleanup(lambda: subprocess.run(["sudo", "rm", "-rf", self.tmp]))
        self.projects_dir = os.path.join(self.tmp, "projects")
        self.project_root = os.path.join(self.tmp, "gitea-root")
        self.bare_repo = os.path.join(self.project_root, "testowner", "testrepo.git")

        # A real bare repo with one commit, served by the real
        # `git http-backend` CGI wired up above -- exercises a genuine
        # `git clone` over smart HTTP with embedded oauth2:<token>@
        # credentials, not a mock.
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        with open(os.path.join(src, "README.md"), "w") as f:
            f.write("hello from gitea\n")
        subprocess.run(["git", "init", "-q", "-b", "main", src], check=True)
        subprocess.run(["git", "-C", src, "-c", "user.name=x", "-c", "user.email=x@x",
                       "add", "-A"], check=True)
        subprocess.run(["git", "-C", src, "-c", "user.name=x", "-c", "user.email=x@x",
                       "commit", "-q", "-m", "initial"], check=True)
        os.makedirs(os.path.dirname(self.bare_repo))
        subprocess.run(["git", "clone", "-q", "--bare", src, self.bare_repo], check=True)

        handler = type("Handler", (_GitHttpBackendHandler,), {
            "project_root": self.project_root, "expected_token": self.TOKEN,
        })
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_clones_real_repo_owned_by_run_user(self):
        r = run_privileged(["testowner", "testrepo", "myproj"], self.projects_dir,
                           self.port, self.TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ready:", r.stdout)

        dest = os.path.join(self.projects_dir, "myproj")
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))
        with open(os.path.join(dest, "README.md")) as f:
            self.assertEqual(f.read(), "hello from gitea\n")

        st = os.stat(dest)
        self.assertEqual(st.st_uid, os.stat(self.tmp).st_uid)

        # The token stays embedded in the clone's own remote (docs/spec.md
        # "Token reuse and where it lives") -- not stripped after cloning.
        remote = subprocess.run(["git", "-C", dest, "remote", "get-url", "origin"],
                                capture_output=True, text=True)
        self.assertIn(self.TOKEN, remote.stdout)

    def test_already_existing_target_fails_atomically_no_dash_p(self):
        r1 = run_privileged(["testowner", "testrepo", "dup"], self.projects_dir,
                            self.port, self.TOKEN)
        self.assertEqual(r1.returncode, 0, r1.stderr)

        r2 = run_privileged(["testowner", "testrepo", "dup"], self.projects_dir,
                            self.port, self.TOKEN)
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("Already exists", r2.stderr)

    def test_name_with_space_and_hyphen_accepted(self):
        r = run_privileged(["testowner", "testrepo", "my project-1"], self.projects_dir,
                           self.port, self.TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.projects_dir, "my project-1")))

    def test_clone_failure_leaves_dest_and_never_leaks_token(self):
        # Wrong repo name -> git http-backend 404s -> git clone fails. DEST
        # is left behind (docs/spec.md "Edge cases" -- no auto-cleanup of a
        # failed-then-abandoned PROJECTS_DIR/<name>), and the token must
        # never appear verbatim in stderr. This particular git version
        # already strips credentials from its own "repository ... not
        # found" message before this script's own redaction ever runs on
        # it (verified directly against real git above in this file's
        # design work) -- see test_redaction_replaces_token_if_git_ever_
        # echoes_it below for a direct test of the script's own redaction
        # substitution, independent of whether this git version's error
        # text happens to need it.
        r = run_privileged(["testowner", "nosuchrepo", "failedclone"], self.projects_dir,
                           self.port, self.TOKEN)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(self.TOKEN, r.stderr)
        dest = os.path.join(self.projects_dir, "failedclone")
        self.assertTrue(os.path.isdir(dest))
        self.assertFalse(os.path.isdir(os.path.join(dest, ".git")))

    def test_redaction_replaces_token_if_git_ever_echoes_it(self):
        # Direct test of the exact bash substitution this script uses
        # (`${CLONE_OUTPUT//$GITEA_API_TOKEN/REDACTED}`) against a
        # fabricated CLONE_OUTPUT that does contain the token verbatim --
        # proves the redaction itself is correct even though this
        # environment's real git version doesn't currently produce a clone
        # failure message that needs it (see the test above).
        r = subprocess.run(
            ["bash", "-c",
             'GITEA_API_TOKEN="$1"; CLONE_OUTPUT="fatal: unable to access '
             "'http://oauth2:$1@host/o/r.git/'\"; "
             'echo "${CLONE_OUTPUT//$GITEA_API_TOKEN/REDACTED}"',
             "_", self.TOKEN],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(self.TOKEN, r.stdout)
        self.assertIn("REDACTED", r.stdout)

    def test_wrong_token_rejected_by_server_and_redacted(self):
        r = run_privileged(["testowner", "testrepo", "badtoken"], self.projects_dir,
                           self.port, "wrong-token-value")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("wrong-token-value", r.stderr)


if __name__ == "__main__":
    unittest.main()
