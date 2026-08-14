#!/usr/bin/env python3
"""
Tests for install.sh's `--with-ollama` block (sub-spec 6d, part 2b —
docs/spec.md): LINKS an existing, already-running remote Ollama for
multi-agent team leads. Installs nothing locally (no package, no model
pull, no container, no systemd unit) -- it prompts for an endpoint URL and
model name, validates both against the exact OpenAI-compatible `/models`
path the lead adapter (app/teams.py's `_tier1_call()`) really calls, and
writes TEAM_LLM_BASE_URL/TEAM_LLM_MODEL only when that validation succeeds.

Technique: install.sh's own `prompt()` deliberately reads answers from
/dev/tty (not stdin), same as tests/test_deploy_target.py's own
_run_with_pty documents -- so a real pty is used here too
(`_run_with_pty`, copied from that file's own implementation, same
docstring). The `--with-ollama` block itself needs no root/sudo/system-user
provisioning at all (no useradd, no sudoers, no privileged path) -- unlike
--with-deploy-target, so none of these tests are gated on passwordless
sudo, matching tests/test_deploy_dispatch.py's own InstallShDeployMapBlockTests
precedent for an install.sh block that never calls `prompt`... except this
block DOES call `prompt` (twice), which is why the pty technique from
test_deploy_target.py is reused here rather than that simpler plain-
subprocess one.

Test isolation (docs/spec.md "Test-isolation requirement", docs/BACKLOG.md
item 9): every stub HTTP server here binds an ephemeral port (`("127.0.0.1",
0)`, then reads back `server.server_address[1]`), never a fixed one. Every
temp dir/env file is created fresh per test and cleaned up in tearDown.
Nothing here ever touches CONFIG_DIR, /home/deploy, a system user, or
sudoers -- ENV_FILE points at a throwaway file this test process itself
created.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_ollama.py
"""
import json
import os
import pty
import select
import shutil
import socket
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    captured text (good enough for assertIn checks and for including
    diagnostic output in an assertEqual failure message)."""
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.stdout = output
        self.stderr = output


def _run_with_pty(cmd, answers, timeout=25):
    """Runs `cmd` with a real pty as its controlling terminal (like a human
    typing at a real terminal), writing each of `answers` in turn -- needed
    because install.sh's own `prompt()` reads from /dev/tty specifically
    (see its own comment: this is deliberate, so a curl-piped install still
    prompts interactively), not from stdin, so a plain piped-stdin
    subprocess can never drive it. Copied verbatim from
    tests/test_deploy_target.py's own `_run_with_pty` (same technique, same
    reasoning) -- that file runs its own command via `sudo -n bash -c ...`
    since --with-deploy-target needs root; --with-ollama needs no
    privilege at all, so callers here just pass a plain `["bash", "-c",
    script]`."""
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


def _build_ollama_block_harness(env_file, with_ollama=1):
    """Assembles a standalone runnable script out of install.sh's own
    helper functions (interactive/prompt/set_env/get_env) plus the literal
    --with-ollama block, unmodified, extracted straight from install.sh's
    real source -- so these tests exercise install.sh's actual provisioning
    logic (prompts, curl call, python3 parsing, set_env writes) rather than
    a reimplementation that could silently drift from the real file. Same
    "extract verbatim between two literal markers" technique as
    tests/test_deploy_target.py's own _build_deploy_target_block_harness.
    Only ENV_FILE/WITH_OLLAMA are supplied as harness inputs, exactly like
    install.sh's own top-of-file variables -- ENV_FILE is always a
    throwaway file under a per-test temp dir, never CONFIG_DIR or any real
    path."""
    with open(INSTALL_SH) as f:
        source = f.read()
    helpers = _extract_between(source, "interactive() {", "random_token() {")
    # Ends at the START of the next block (deploy-target), not at the
    # shared "echo == Done ==" summary marker -- --with-ollama sits
    # immediately before --with-deploy-target in install.sh, so ending at
    # the summary would also scoop up that whole unrelated block (and this
    # harness never sets WITH_DEPLOY_TARGET, breaking under `set -u`).
    block = _extract_between(
        source,
        "# ── Optional: link an existing remote Ollama",
        "# ── Optional: deploy-target receiver")
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        YES=0
        ENV_FILE={env_file}
        WITH_OLLAMA={with_ollama}
        {helpers}
        {block}
        """)


class _StubOllamaHandler(BaseHTTPRequestHandler):
    """Fake OpenAI-compatible /models endpoint standing in for a reachable
    Ollama. `model_ids` (list of `id` strings), `as_html` (serve a 200 HTML
    body instead of JSON, simulating a captive portal / proxy login page),
    and `stall` (accept the connection, never respond -- simulating a dead
    host behind a firewall that eats the SYN-ACK's follow-up) are set per
    test via subclassing, same pattern
    tests/test_new_project_from_gitea.py's _GitHttpBackendHandler uses.
    `seen_paths` (class-level list) records every request path actually
    made, so a test can assert no "//models" double-slash request ever
    went out."""
    model_ids = ()
    as_html = False
    stall = False
    seen_paths = None

    def do_GET(self):
        if self.seen_paths is not None:
            self.seen_paths.append(self.path)
        if self.path != "/v1/models":
            # Only the exact OpenAI-compatible path the lead adapter really
            # calls is served -- any other path (a base URL missing /v1, or
            # a rewritten "//models") 404s, so the "validated as given, not
            # silently rewritten" and "no double slash" tests actually
            # prove something rather than passing by accident.
            self.send_response(404)
            self.end_headers()
            return
        if self.stall:
            time.sleep(60)
            return
        if self.as_html:
            body = b"<html><body>please log in</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        payload = {"data": [{"id": m} for m in self.model_ids]}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep test output quiet


def _unused_port():
    """An ephemeral port nothing is listening on right now, for the
    "unreachable endpoint" tests -- bind-then-close rather than a fixed
    port, matching this cycle's own test-isolation requirement (never a
    fixed port, docs/BACKLOG.md item 9)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class InstallShOllamaBlockTests(unittest.TestCase):
    """Runs install.sh's actual --with-ollama block (extracted verbatim,
    see _build_ollama_block_harness above) with controlled pty-fed answers,
    proving every acceptance criterion in docs/spec.md that is specifically
    about install.sh's own provisioning logic."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-ollama-block-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env_file = os.path.join(self.tmp, "switchboard.env")
        open(self.env_file, "w").close()
        self._servers = []

    def tearDown(self):
        for server, thread in self._servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def _start_stub(self, model_ids=(), as_html=False, stall=False):
        seen_paths = []
        handler = type("Handler", (_StubOllamaHandler,), {
            "model_ids": model_ids, "as_html": as_html, "stall": stall,
            "seen_paths": seen_paths,
        })
        server = HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        return port, seen_paths

    def _read_env(self):
        with open(self.env_file) as f:
            return f.read()

    def run_block(self, answers, with_ollama=1, timeout=25):
        script = _build_ollama_block_harness(self.env_file, with_ollama=with_ollama)
        return _run_with_pty(["bash", "-c", script], answers, timeout=timeout)

    # ── usage / off-by-default ──────────────────────────────────────────

    def test_usage_block_documents_the_flag(self):
        with open(INSTALL_SH) as f:
            source = f.read()
        header = source[:source.index("set -euo pipefail")]
        self.assertIn("--with-ollama", header)

    def test_flag_defaults_to_off_in_flag_plumbing(self):
        with open(INSTALL_SH) as f:
            source = f.read()
        self.assertIn("WITH_OLLAMA=0", source)
        self.assertIn('--with-ollama) WITH_OLLAMA=1 ;;', source)

    def test_without_the_flag_writes_no_team_llm_keys(self):
        r = self.run_block([], with_ollama=0)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("TEAM_LLM_", self._read_env())
        self.assertNotIn("Link an existing Ollama", r.stdout)

    def test_block_issues_no_local_install_command(self):
        # Nothing local is installed -- no package, no systemd unit, no
        # container, no model pull (docs/spec.md acceptance criteria).
        with open(INSTALL_SH) as f:
            source = f.read()
        block = _extract_between(
            source, "# ── Optional: link an existing remote Ollama",
            "# ── Optional: deploy-target receiver")
        for forbidden in ("apt-get", "apt install", "docker", "systemctl",
                          "ollama pull", "ollama run", "useradd"):
            self.assertNotIn(forbidden, block,
                             f"--with-ollama block must never run {forbidden!r}")

    # ── outcome 1: reachable, model present ─────────────────────────────

    def test_reachable_with_model_writes_both_keys_exactly(self):
        port, _ = self._start_stub(model_ids=["qwen3:8b", "llama3.2:1b"])
        url = f"http://127.0.0.1:{port}/v1"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertIn(f"TEAM_LLM_BASE_URL={url}\n", env)
        self.assertIn("TEAM_LLM_MODEL=qwen3:8b\n", env)
        self.assertIn("Nothing was installed locally", r.stdout)

    # ── outcome: unreachable endpoint ───────────────────────────────────

    def test_unreachable_endpoint_writes_nothing_run_still_succeeds(self):
        port = _unused_port()
        url = f"http://127.0.0.1:{port}/v1"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)
        self.assertNotIn("TEAM_LLM_MODEL", env)
        self.assertIn("Could not reach", r.stdout)
        self.assertIn("fall back", r.stdout)

    # ── outcome: reachable, model absent ────────────────────────────────

    def test_reachable_but_model_absent_lists_available_ids(self):
        port, _ = self._start_stub(model_ids=["llama3.2:1b", "mistral:7b"])
        url = f"http://127.0.0.1:{port}/v1"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)
        self.assertNotIn("TEAM_LLM_MODEL", env)
        self.assertIn("not available there", r.stdout)
        self.assertIn("llama3.2:1b", r.stdout)
        self.assertIn("mistral:7b", r.stdout)

    def test_reachable_with_empty_model_list_says_none_available(self):
        port, _ = self._start_stub(model_ids=[])
        url = f"http://127.0.0.1:{port}/v1"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)
        self.assertIn("no models available", r.stdout)

    # ── outcome: 200 with non-JSON (HTML) body ──────────────────────────

    def test_html_response_treated_as_unreachable_for_this_purpose(self):
        port, _ = self._start_stub(as_html=True)
        url = f"http://127.0.0.1:{port}/v1"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)
        self.assertIn("could not be parsed as JSON", r.stdout)

    # ── bounded: a stalling endpoint must not hang the installer ────────

    def test_stalling_endpoint_is_bounded_does_not_hang(self):
        port, _ = self._start_stub(stall=True)
        url = f"http://127.0.0.1:{port}/v1"
        started = time.monotonic()
        r = self.run_block([url, "qwen3:8b"], timeout=25)
        elapsed = time.monotonic() - started
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertLess(elapsed, 20,
                        "the installer must never hang on an unreachable/"
                        "stalled host -- curl --max-time bounds this to 10s")
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)

    # ── substring safety: the specific grep-shaped defect ───────────────

    def test_substring_model_name_is_rejected_not_accepted(self):
        # A stub advertising only "qwen3:8b" must REJECT "qwen3:8" -- the
        # exact defect a grep-based (rather than exact-id) check would ship.
        port, _ = self._start_stub(model_ids=["qwen3:8b"])
        url = f"http://127.0.0.1:{port}/v1"
        r = self.run_block([url, "qwen3:8"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)
        self.assertNotIn("TEAM_LLM_MODEL", env)
        self.assertIn("not available there", r.stdout)
        self.assertIn("qwen3:8b", r.stdout)

    # ── trailing slash normalisation ────────────────────────────────────

    def test_trailing_slash_validates_and_writes_no_double_slash(self):
        port, seen_paths = self._start_stub(model_ids=["qwen3:8b"])
        url = f"http://127.0.0.1:{port}/v1/"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertIn(f"TEAM_LLM_BASE_URL=http://127.0.0.1:{port}/v1\n", env)
        self.assertIn("TEAM_LLM_MODEL=qwen3:8b\n", env)
        self.assertTrue(seen_paths, "the stub never saw a request")
        for path in seen_paths:
            self.assertNotIn("//models", path)
        self.assertIn("/v1/models", seen_paths[0])

    def test_url_without_v1_is_validated_as_given_not_silently_rewritten(self):
        # docs/spec.md "Edge cases": an operator-supplied URL missing /v1
        # must be validated exactly as given, never silently corrected --
        # the stub here only serves /v1/models, so a bare-root URL must
        # fail as unreachable-for-this-purpose, not be quietly fixed up.
        port, seen_paths = self._start_stub(model_ids=["qwen3:8b"])
        url = f"http://127.0.0.1:{port}"
        r = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r.returncode, 0, r.stdout)
        env = self._read_env()
        self.assertNotIn("TEAM_LLM_BASE_URL", env)
        for path in seen_paths:
            self.assertNotIn("/v1/models", path)

    # ── idempotence ──────────────────────────────────────────────────────

    def test_blank_answers_leave_existing_values_untouched(self):
        port, _ = self._start_stub(model_ids=["qwen3:8b"])
        url = f"http://127.0.0.1:{port}/v1"
        r1 = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r1.returncode, 0, r1.stdout)
        env_after_first = self._read_env()

        r2 = self.run_block(["", ""])
        self.assertEqual(r2.returncode, 0, r2.stdout)
        env_after_second = self._read_env()

        self.assertIn(f"TEAM_LLM_BASE_URL={url}\n", env_after_second)
        self.assertIn("TEAM_LLM_MODEL=qwen3:8b\n", env_after_second)
        self.assertEqual(env_after_first, env_after_second)

    def test_changed_model_updates_only_team_llm_model(self):
        port, _ = self._start_stub(model_ids=["qwen3:8b", "llama3.2:1b"])
        url = f"http://127.0.0.1:{port}/v1"
        r1 = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r1.returncode, 0, r1.stdout)

        r2 = self.run_block(["", "llama3.2:1b"])
        self.assertEqual(r2.returncode, 0, r2.stdout)
        env = self._read_env()
        self.assertIn(f"TEAM_LLM_BASE_URL={url}\n", env)
        self.assertIn("TEAM_LLM_MODEL=llama3.2:1b\n", env)
        self.assertNotIn("TEAM_LLM_MODEL=qwen3:8b\n", env)
        # No accumulation -- exactly one line per key.
        self.assertEqual(env.count("TEAM_LLM_BASE_URL="), 1)
        self.assertEqual(env.count("TEAM_LLM_MODEL="), 1)

    def test_rerun_with_same_answers_is_a_noop(self):
        port, _ = self._start_stub(model_ids=["qwen3:8b"])
        url = f"http://127.0.0.1:{port}/v1"
        r1 = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r1.returncode, 0, r1.stdout)
        env1 = self._read_env()

        r2 = self.run_block([url, "qwen3:8b"])
        self.assertEqual(r2.returncode, 0, r2.stdout)
        env2 = self._read_env()
        self.assertEqual(env1, env2)


if __name__ == "__main__":
    unittest.main()
