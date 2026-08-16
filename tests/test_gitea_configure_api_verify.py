#!/usr/bin/env python3
"""
Tests for scripts/gitea-configure-api.sh's token-verification branching
(docs/BACKLOG.md item 40, docs/spec.md "Item 40 — Gitea admin bootstrap
403"): the `GET /user` verification call used to be a bare `curl -fsS` that
lost the response body on any non-2xx, so a 403 caused by Gitea's own
"you must change your password" flag (the account was created without
`--must-change-password=false` -- see install.sh's printed step 1) was
reported as an opaque curl failure with no indication of the real cause or
fix. The fix drops `-f`, captures the HTTP status via `-w`, and special-
cases a 403 whose body mentions "must change" with a targeted message
pointing at `gitea admin user change-password --must-change-password=false`.

Reviewer-added: this branch had no automated coverage at review time --
implementation.md's own "How to verify locally" only ran `bash -n` (syntax
check) for item 40, which can't exercise the runtime status-code/body
branching this fix actually depends on. This closes that gap using the
same extract-the-real-block-verbatim-and-stub-the-external-command
technique already established by tests/test_install_auth_mode_default.py
and tests/test_install_code_server_path.py for install.sh.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_gitea_configure_api_verify.py
"""
import os
import subprocess
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
SCRIPT = os.path.join(REPO_ROOT, "scripts", "gitea-configure-api.sh")


def _extract_verify_block():
    """Pulls the real verification block (the curl call through the
    branching if/else that prints either the targeted 403 message or the
    generic failure output) verbatim out of the shipped script, unmodified
    -- so this test exercises the actual runtime logic, not a
    reimplementation that could silently drift from the real file."""
    with open(SCRIPT) as f:
        source = f.read()
    start = source.index("VERIFY_RAW=$(curl")
    end = source.index("VERIFIED_LOGIN=$(python3")
    return source[start:end]


class GiteaConfigureApiVerifyTests(unittest.TestCase):
    def setUp(self):
        self.block = _extract_verify_block()

    def _run(self, curl_status, curl_body, timeout=10):
        """Runs the real extracted verification block with `curl` stubbed
        as a shell function that reports a controlled HTTP status/body,
        matching the real script's own `curl -sS -w '\\n%{http_code}'`
        output shape (body, newline, then the bare status code) -- no
        network or real Gitea instance needed."""
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            GITEA_ADMIN_USER=admin
            GITEA_CONTAINER=ai-dev-switchboard-gitea
            GITEA_PORT=3000
            GITEA_API_TOKEN=faketoken
            curl() {{ printf '%s\\n%s' {curl_body!r} {curl_status!r}; }}
            {self.block}
            echo "REACHED_PAST_VERIFICATION"
            """)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=timeout)

    def test_403_must_change_password_names_real_cause_and_fix(self):
        r = self._run(
            "403",
            '{"message":"You must change your password. Please change your password first."}',
        )
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("REACHED_PAST_VERIFICATION", r.stdout)
        self.assertIn("must-change-password flag set", r.stderr)
        self.assertIn(
            "gitea admin user change-password", r.stderr)
        self.assertIn("--must-change-password=false", r.stderr)
        self.assertIn("--username admin", r.stderr)

    def test_403_without_must_change_body_falls_back_to_generic_output(self):
        # A 403 for some other reason (e.g. insufficient token scope) must
        # not get the must-change-password message -- only the real cause
        # (docs/spec.md edge case: diagnostic improvement for *whatever*
        # account state produces the error, not a blanket assumption).
        r = self._run("403", '{"message":"insufficient scope"}')
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("must-change-password flag set", r.stderr)
        self.assertIn("Output was:", r.stderr)
        self.assertIn("insufficient scope", r.stderr)

    def test_other_non_200_status_prints_generic_output_not_bare_curl_error(self):
        r = self._run("401", '{"message":"token does not have required scope"}')
        self.assertEqual(r.returncode, 1)
        self.assertIn("HTTP 401", r.stderr)
        self.assertIn("token does not have required scope", r.stderr)

    def test_200_success_falls_through_past_verification(self):
        r = self._run("200", '{"login":"admin"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REACHED_PAST_VERIFICATION", r.stdout)


if __name__ == "__main__":
    unittest.main()
