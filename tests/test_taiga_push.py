#!/usr/bin/env python3
"""
Tests for scripts/taiga_push_spec.py (backlog item 1b -- see docs/spec.md
"Local backlog tracker (Taiga) -- part 1b: push a spec into Taiga").

No live Taiga instance is reachable in this environment (no Docker Compose
plugin -- see docs/implementation.md "What could and couldn't be verified"),
so every test here monkeypatches taiga_push_spec._taiga_request -- the one
function that actually calls urllib.request -- exactly as instructed by
docs/spec.md ("giving the test suite one seam to monkeypatch") and exactly
the same technique tests/test_taiga.py already uses for appmod.taiga_run.
A handful of _taiga_request-level tests instead monkeypatch
urllib.request.urlopen directly, to cover that one function's own
connection-error/HTTP-error/success translation.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_taiga_push.py
"""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import taiga_push_spec as tps  # noqa: E402


def write_config(path, **overrides):
    values = {
        "TAIGA_URL": "http://127.0.0.1:9000",
        "TAIGA_USERNAME": "taiga-bot",
        "TAIGA_PASSWORD": "correct-horse-battery-staple",
        "TAIGA_PROJECT_SLUG": "my-project",
    }
    values.update(overrides)
    with open(path, "w") as f:
        for k, v in values.items():
            if v is None:
                continue
            f.write(f"{k}={v}\n")
    os.chmod(path, 0o600)
    return path


def write_spec(path, text="# Spec: My Feature\n\nSome body text.\n"):
    with open(path, "w") as f:
        f.write(text)
    return path


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taiga-push-test-")
        self.config_path = os.path.join(self.tmp, "taiga-push.env")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        self._orig_taiga_request = tps._taiga_request

    def tearDown(self):
        tps._taiga_request = self._orig_taiga_request


# ─── _load_config ──────────────────────────────────────────────────────────
class LoadConfigTests(_TmpDirCase):
    def test_missing_file_raises_with_exact_message(self):
        missing = os.path.join(self.tmp, "does-not-exist.env")
        with self.assertRaises(tps.TaigaPushError) as ctx:
            tps._load_config(missing)
        self.assertEqual(
            str(ctx.exception),
            f"No Taiga push config found at {missing} — run scripts/taiga-configure-push.sh first.")

    def test_parses_key_value_lines_ignoring_comments_and_blanks(self):
        write_config(self.config_path)
        with open(self.config_path, "a") as f:
            f.write("\n# a comment\n   \n")
        cfg = tps._load_config(self.config_path)
        self.assertEqual(cfg["TAIGA_URL"], "http://127.0.0.1:9000")
        self.assertEqual(cfg["TAIGA_USERNAME"], "taiga-bot")
        self.assertEqual(cfg["TAIGA_PASSWORD"], "correct-horse-battery-staple")
        self.assertEqual(cfg["TAIGA_PROJECT_SLUG"], "my-project")

    def test_incomplete_config_never_raises_keyerror(self):
        write_config(self.config_path, TAIGA_PASSWORD="")
        cfg = tps._load_config(self.config_path)
        self.assertEqual(cfg.get("TAIGA_PASSWORD", "sentinel-not-used"), "")


# ─── _check_config_permissions ─────────────────────────────────────────────
class ConfigPermissionsTests(_TmpDirCase):
    def test_mode_600_prints_no_warning(self):
        write_config(self.config_path)
        os.chmod(self.config_path, 0o600)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)
        self.assertEqual(buf.getvalue(), "")

    def test_looser_mode_prints_a_loud_warning_but_does_not_raise(self):
        write_config(self.config_path)
        os.chmod(self.config_path, 0o644)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)  # must not raise
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn(self.config_path, buf.getvalue())

    def test_missing_file_is_silently_ignored_here(self):
        # _load_config is what's responsible for the missing-file error
        # message; the permissions check alone must not blow up on a path
        # that doesn't exist yet.
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(os.path.join(self.tmp, "nope.env"))
        self.assertEqual(buf.getvalue(), "")

    # ── item 37: ACL-aware -- an item-29-style narrow ACL grant must not ──
    # ── be misread as a loose group permission via the recomputed mask.   ──
    # ── _read_getfacl is the one seam these tests monkeypatch, same style ──
    # ── as _taiga_request above (docs/spec.md's own note). ────────────────

    def setUp(self):
        super().setUp()
        self._orig_read_getfacl = tps._read_getfacl

    def tearDown(self):
        tps._read_getfacl = self._orig_read_getfacl
        super().tearDown()

    def test_item29_style_acl_grant_prints_no_warning(self):
        # mode 600 + `setfacl -m u:switchboard-svc:r` recomputes the ACL
        # mask to `r--`, so stat() now reports mode 0640 even though the
        # file's real other:: exposure is still `---`.
        write_config(self.config_path)
        os.chmod(self.config_path, 0o640)
        getfacl_output = (
            "# file: taiga-push.env\n"
            "# owner: root\n"
            "# group: root\n"
            "user::rw-\n"
            "user:switchboard-svc:r--\n"
            "group::---\n"
            "mask::r--\n"
            "other::---\n"
        )
        tps._read_getfacl = lambda path: getfacl_output
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)
        self.assertEqual(buf.getvalue(), "")

    def test_item29_style_acl_grant_output_never_contains_chmod_600(self):
        write_config(self.config_path)
        os.chmod(self.config_path, 0o640)
        getfacl_output = (
            "user::rw-\nuser:switchboard-svc:r--\ngroup::---\nmask::r--\nother::---\n"
        )
        tps._read_getfacl = lambda path: getfacl_output
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)
        self.assertNotIn("chmod 600", buf.getvalue())

    def test_genuinely_loose_acl_warns_with_setfacl_remediation_not_chmod(self):
        write_config(self.config_path)
        os.chmod(self.config_path, 0o644)
        getfacl_output = (
            "user::rw-\nuser:switchboard-svc:r--\ngroup::---\nmask::r--\nother::r--\n"
        )
        tps._read_getfacl = lambda path: getfacl_output
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)
        out = buf.getvalue()
        self.assertIn("WARNING", out)
        self.assertIn("Run: setfacl", out)
        # The remediation itself is setfacl-based; "chmod" may still appear
        # as part of an explicit "do NOT run chmod" safety note, but never
        # as the recommended command.
        self.assertNotIn("Run: chmod", out)

    def test_no_acl_at_all_behaves_exactly_as_before(self):
        # getfacl output with no extended ACL (no mask:: line) -- the plain
        # st_mode path applies unchanged.
        write_config(self.config_path)
        os.chmod(self.config_path, 0o644)
        getfacl_output = "user::rw-\ngroup::r--\nother::r--\n"
        tps._read_getfacl = lambda path: getfacl_output
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)
        self.assertIn("chmod 600", buf.getvalue())

    def test_getfacl_unavailable_falls_back_to_plain_st_mode_check(self):
        write_config(self.config_path)
        os.chmod(self.config_path, 0o644)
        tps._read_getfacl = lambda path: None
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)  # must not raise
        self.assertIn("chmod 600", buf.getvalue())

    def test_getfacl_unavailable_mode_600_prints_no_warning(self):
        write_config(self.config_path)
        os.chmod(self.config_path, 0o600)
        tps._read_getfacl = lambda path: None
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tps._check_config_permissions(self.config_path)
        self.assertEqual(buf.getvalue(), "")


# ─── _parse_acl_other_bits / _read_getfacl ──────────────────────────────────
class ParseAclOtherBitsTests(unittest.TestCase):
    def test_no_mask_line_returns_none(self):
        self.assertIsNone(tps._parse_acl_other_bits("user::rw-\ngroup::r--\nother::r--\n"))

    def test_mask_present_extracts_other_bits(self):
        out = tps._parse_acl_other_bits(
            "user::rw-\nuser:svc:r--\ngroup::---\nmask::r--\nother::---\n"
        )
        self.assertEqual(out, {"other": 0, "other_str": "---"})

    def test_mask_present_other_rw_extracts_nonzero_bits(self):
        out = tps._parse_acl_other_bits(
            "user::rw-\nuser:svc:r--\ngroup::---\nmask::rw-\nother::rw-\n"
        )
        self.assertEqual(out, {"other": 6, "other_str": "rw-"})

    def test_mask_present_but_no_other_line_returns_none(self):
        self.assertIsNone(tps._parse_acl_other_bits("user::rw-\nmask::r--\n"))


class ReadGetfaclTests(unittest.TestCase):
    """Monkeypatches the module-level subprocess (imported by
    taiga_push_spec.py itself), same restore-in-tearDown technique
    TaigaRunTests in tests/test_taiga.py uses for appmod's subprocess.run."""

    def setUp(self):
        self._orig_run = tps.subprocess.run

    def tearDown(self):
        tps.subprocess.run = self._orig_run

    def test_missing_getfacl_binary_returns_none(self):
        def fake_run(*a, **k):
            raise FileNotFoundError()
        tps.subprocess.run = fake_run
        self.assertIsNone(tps._read_getfacl("/some/path"))

    def test_getfacl_timeout_returns_none(self):
        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="getfacl", timeout=5)
        tps.subprocess.run = fake_run
        self.assertIsNone(tps._read_getfacl("/some/path"))

    def test_nonzero_returncode_returns_none(self):
        fake = subprocess.CompletedProcess(["getfacl"], 1, stdout="", stderr="err")
        tps.subprocess.run = lambda *a, **k: fake
        self.assertIsNone(tps._read_getfacl("/some/path"))

    def test_success_returns_stdout(self):
        fake = subprocess.CompletedProcess(["getfacl"], 0, stdout="user::rw-\n", stderr="")
        tps.subprocess.run = lambda *a, **k: fake
        self.assertEqual(tps._read_getfacl("/some/path"), "user::rw-\n")


# ─── _build_subject_and_description ────────────────────────────────────────
class BuildSubjectAndDescriptionTests(unittest.TestCase):
    def test_spec_heading_convention_strips_prefix(self):
        subject, description = tps._build_subject_and_description(
            "# Spec: Widget factory\n\nBody.\n", "docs/spec.md")
        self.assertEqual(subject, "Widget factory")
        self.assertIn("Body.", description)
        self.assertIn("# Spec: Widget factory", description)  # full raw text preserved

    def test_falls_back_to_first_non_blank_line(self):
        subject, _ = tps._build_subject_and_description(
            "\n\nJust a plain first line\nmore text\n", "docs/spec.md")
        self.assertEqual(subject, "Just a plain first line")

    def test_falls_back_to_filename_if_nothing_usable(self):
        subject, _ = tps._build_subject_and_description("   \n\n  \n", "docs/spec.md")
        self.assertEqual(subject, "spec.md")

    def test_description_contains_full_body_and_traceability_footer(self):
        _, description = tps._build_subject_and_description(
            "# Spec: X\n\nline one\nline two\n", "docs/spec.md")
        self.assertIn("line one", description)
        self.assertIn("line two", description)
        self.assertIn("docs/spec.md", description)
        self.assertIn("UTC", description)


# ─── _taiga_request itself (the one seam) ──────────────────────────────────
class TaigaRequestTests(unittest.TestCase):
    def setUp(self):
        self._orig_urlopen = tps.urllib.request.urlopen

    def tearDown(self):
        tps.urllib.request.urlopen = self._orig_urlopen

    def test_success_returns_parsed_json(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"ok": True}).encode()

        tps.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        result = tps._taiga_request("http://x", "GET", "/api/v1/foo")
        self.assertEqual(result, {"ok": True})

    def test_http_error_raises_taiga_http_error_with_status(self):
        def raise_http_error(req, timeout=None):
            raise urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, io.BytesIO(b""))

        tps.urllib.request.urlopen = raise_http_error
        with self.assertRaises(tps.TaigaHTTPError) as ctx:
            tps._taiga_request("http://x", "POST", "/api/v1/auth")
        self.assertEqual(ctx.exception.status, 401)

    def test_connection_failure_raises_taiga_connection_error(self):
        def raise_url_error(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        tps.urllib.request.urlopen = raise_url_error
        with self.assertRaises(tps.TaigaConnectionError):
            tps._taiga_request("http://x", "POST", "/api/v1/auth")


# ─── _authenticate / _lookup_project / _create_userstory ──────────────────
class EndpointFunctionTests(unittest.TestCase):
    def setUp(self):
        self._orig = tps._taiga_request

    def tearDown(self):
        tps._taiga_request = self._orig

    def test_authenticate_returns_token(self):
        tps._taiga_request = lambda *a, **k: {"auth_token": "tok123"}
        self.assertEqual(tps._authenticate("http://x", "u", "p"), "tok123")

    def test_authenticate_raises_auth_rejected_on_http_error(self):
        def fake(*a, **k):
            raise tps.TaigaHTTPError(400, "")

        tps._taiga_request = fake
        with self.assertRaises(tps._AuthRejected):
            tps._authenticate("http://x", "u", "p")

    def test_lookup_project_returns_id(self):
        tps._taiga_request = lambda *a, **k: {"id": 42, "slug": "my-project"}
        self.assertEqual(tps._lookup_project("http://x", "tok", "my-project"), 42)

    def test_lookup_project_404_raises_project_not_found(self):
        def fake(*a, **k):
            raise tps.TaigaHTTPError(404, "")

        tps._taiga_request = fake
        with self.assertRaises(tps._ProjectNotFound):
            tps._lookup_project("http://x", "tok", "nope")

    def test_create_userstory_passes_through_result(self):
        captured = {}

        def fake(base_url, method, path, token=None, body=None):
            captured["body"] = body
            return {"id": 1, "ref": 42}

        tps._taiga_request = fake
        result = tps._create_userstory("http://x", "tok", 7, "Subject", "Desc")
        self.assertEqual(result, {"id": 1, "ref": 42})
        self.assertEqual(captured["body"], {"project": 7, "subject": "Subject", "description": "Desc"})


# ─── main() / _run() integration tests (acceptance criteria) ──────────────
class MainIntegrationTests(_TmpDirCase):
    def setUp(self):
        super().setUp()
        write_config(self.config_path)
        write_spec(self.spec_path)
        self.calls = []

    def _fake_request_success_chain(self, userstory_ref=42):
        def fake(base_url, method, path, token=None, body=None):
            self.calls.append((method, path, token, body))
            if path == "/api/v1/auth":
                return {"auth_token": "tok-abc"}
            if path.startswith("/api/v1/projects/by_slug"):
                return {"id": 7}
            if path == "/api/v1/userstories":
                return {"id": 99, "ref": userstory_ref}
            raise AssertionError(f"unexpected call: {path}")

        tps._taiga_request = fake

    def _run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tps.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_normal_run_creates_one_userstory_and_prints_ref_and_url(self):
        self._fake_request_success_chain(userstory_ref=42)
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("#42", out)
        self.assertIn("http://127.0.0.1:9000/project/my-project/us/42", out)
        post_calls = [c for c in self.calls if c[1] == "/api/v1/userstories"]
        self.assertEqual(len(post_calls), 1)
        _, _, _, body = post_calls[0]
        self.assertEqual(body["subject"], "My Feature")
        self.assertIn("Some body text.", body["description"])

    def test_dry_run_sends_no_post_and_prints_preview(self):
        self._fake_request_success_chain()
        code, out, err = self._run_main(
            ["--config", self.config_path, "--spec", self.spec_path, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertNotIn("/api/v1/userstories", [c[1] for c in self.calls])
        self.assertIn("My Feature", out)
        self.assertIn("Some body text.", out)
        self.assertIn("Dry run", out)

    def test_verify_only_authenticates_and_looks_up_project(self):
        self._fake_request_success_chain()
        code, out, err = self._run_main(["--config", self.config_path, "--verify"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        paths_called = [c[1] for c in self.calls]
        self.assertIn("/api/v1/auth", paths_called)
        self.assertTrue(any(p.startswith("/api/v1/projects/by_slug") for p in paths_called))
        self.assertNotIn("/api/v1/userstories", paths_called)

    def test_verify_wins_over_dry_run_when_both_passed(self):
        self._fake_request_success_chain()
        code, out, err = self._run_main(
            ["--config", self.config_path, "--spec", self.spec_path, "--dry-run", "--verify"])
        self.assertEqual(code, 0)
        # --verify's narrower behavior: no spec-derived preview text at all.
        self.assertNotIn("Dry run", out)
        self.assertNotIn("Some body text.", out)

    def test_taiga_unreachable_exits_nonzero_with_exact_message_no_traceback(self):
        def fake(*a, **k):
            raise tps.TaigaConnectionError("connection refused")

        tps._taiga_request = fake
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertEqual(
            err.strip(),
            f"error: Could not reach Taiga at http://127.0.0.1:9000 — make sure it's toggled "
            f"on in the ai-dev-switchboard web UI, or check TAIGA_URL in {self.config_path}.")
        self.assertNotIn("Traceback", err)

    def test_blank_taiga_url_exits_nonzero_with_unreachable_message_no_traceback(self):
        # docs/test-review.md Defect 1: a blank/malformed TAIGA_URL used to
        # make urllib.request.Request(...) raise a bare ValueError *before*
        # _taiga_request's try: block even started, bypassing every
        # TaigaPushError handler and reaching main() as a raw traceback.
        # Deliberately does NOT monkeypatch tps._taiga_request here (unlike
        # every other test in this class) -- the real _taiga_request must
        # run so that the real urllib.request.Request(...) call is what's
        # exercised, matching the fix location exactly.
        write_config(self.config_path, TAIGA_URL="")
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertEqual(
            err.strip(),
            f"error: Could not reach Taiga at  — make sure it's toggled "
            f"on in the ai-dev-switchboard web UI, or check TAIGA_URL in {self.config_path}.")
        self.assertNotIn("Traceback", err)

    def test_malformed_taiga_url_exits_nonzero_with_unreachable_message_no_traceback(self):
        # Same underlying bug, different trigger shape: a non-empty but
        # scheme-less URL (e.g. a copy-paste mistake) raises the exact same
        # bare ValueError out of Request.__init__.
        write_config(self.config_path, TAIGA_URL="not-a-url-at-all")
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertIn("error: Could not reach Taiga at not-a-url-at-all", err)
        self.assertNotIn("Traceback", err)

    def test_bad_credentials_exits_nonzero_with_exact_message_and_creates_nothing(self):
        def fake(base_url, method, path, token=None, body=None):
            self.calls.append((method, path, token, body))
            raise tps.TaigaHTTPError(400, "")

        tps._taiga_request = fake
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertEqual(
            err.strip(),
            f"error: Taiga rejected the configured username/password — check TAIGA_USERNAME/"
            f"TAIGA_PASSWORD in {self.config_path}, or re-run scripts/taiga-configure-push.sh.")
        self.assertFalse(any(c[1] == "/api/v1/userstories" for c in self.calls))

    def test_unknown_project_slug_exits_nonzero_with_exact_message(self):
        def fake(base_url, method, path, token=None, body=None):
            self.calls.append((method, path, token, body))
            if path == "/api/v1/auth":
                return {"auth_token": "tok"}
            raise tps.TaigaHTTPError(404, "")

        tps._taiga_request = fake
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertEqual(
            err.strip(),
            "error: No Taiga project found with slug 'my-project' — create it first in "
            "Taiga's own web UI, or check TAIGA_PROJECT_SLUG / --project.")
        self.assertFalse(any(c[1] == "/api/v1/userstories" for c in self.calls))

    def test_missing_spec_exits_nonzero_before_any_network_call(self):
        missing = os.path.join(self.tmp, "no-such-spec.md")
        called = []
        tps._taiga_request = lambda *a, **k: called.append(1) or {}
        code, out, err = self._run_main(["--config", self.config_path, "--spec", missing])
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), f"error: No spec found at {missing} (or it's empty) — nothing to push.")
        self.assertEqual(called, [])

    def test_empty_spec_exits_nonzero_before_any_network_call(self):
        empty = os.path.join(self.tmp, "empty-spec.md")
        open(empty, "w").close()
        called = []
        tps._taiga_request = lambda *a, **k: called.append(1) or {}
        code, out, err = self._run_main(["--config", self.config_path, "--spec", empty])
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), f"error: No spec found at {empty} (or it's empty) — nothing to push.")
        self.assertEqual(called, [])

    def test_loose_config_permissions_warns_but_still_proceeds(self):
        os.chmod(self.config_path, 0o644)
        self._fake_request_success_chain()
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 0)
        self.assertIn("WARNING", err)
        self.assertIn(self.config_path, err)

    def test_explicit_project_flag_overrides_config_without_modifying_file(self):
        with open(self.config_path) as f:
            before = f.read()
        seen_slugs = []

        def fake(base_url, method, path, token=None, body=None):
            self.calls.append((method, path, token, body))
            if path == "/api/v1/auth":
                return {"auth_token": "tok"}
            if path.startswith("/api/v1/projects/by_slug"):
                seen_slugs.append(path.split("=", 1)[1])
                return {"id": 7}
            if path == "/api/v1/userstories":
                return {"id": 1, "ref": 1}

        tps._taiga_request = fake
        code, out, err = self._run_main(
            ["--config", self.config_path, "--spec", self.spec_path, "--project", "other-slug"])
        self.assertEqual(code, 0)
        self.assertEqual(seen_slugs, ["other-slug"])
        with open(self.config_path) as f:
            after = f.read()
        self.assertEqual(before, after)  # config file itself never modified

    def test_password_never_appears_in_stdout_or_stderr_even_on_bad_credentials(self):
        write_config(self.config_path, TAIGA_PASSWORD="sUpErSecReT-pw-42")

        def fake(*a, **k):
            raise tps.TaigaHTTPError(401, "")

        tps._taiga_request = fake
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertNotIn("sUpErSecReT-pw-42", out)
        self.assertNotIn("sUpErSecReT-pw-42", err)

    def test_401_on_userstory_creation_is_surfaced_as_bad_credentials(self):
        def fake(base_url, method, path, token=None, body=None):
            if path == "/api/v1/auth":
                return {"auth_token": "tok"}
            if path.startswith("/api/v1/projects/by_slug"):
                return {"id": 7}
            raise tps.TaigaHTTPError(401, "")

        tps._taiga_request = fake
        code, out, err = self._run_main(["--config", self.config_path, "--spec", self.spec_path])
        self.assertEqual(code, 1)
        self.assertIn("rejected the configured username/password", err)


# ─── scripts/taiga-configure-push.sh (bash) ────────────────────────────────
class ConfigureScriptTests(unittest.TestCase):
    """
    Real end-to-end verification against a live Taiga instance isn't
    possible in this environment (see docs/implementation.md), so this only
    verifies the parts that don't require Taiga to actually be running: the
    file gets created with the entered values at mode 600, with no window
    where it's more permissive, and the script correctly propagates
    taiga_push_spec.py --verify's exit code (which will legitimately fail
    here since nothing is listening on the dummy URL).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taiga-configure-test-")
        self.script = os.path.join(REPO_ROOT, "scripts", "taiga-configure-push.sh")

    def test_writes_mode_600_config_and_propagates_verify_failure(self):
        env = dict(os.environ, HOME=self.tmp)
        answers = "http://127.0.0.1:1\ntaiga-bot\nsecret-pw\nmy-project\n"
        result = subprocess.run(
            ["bash", self.script], input=answers, capture_output=True, text=True,
            env=env, timeout=30)
        config_path = os.path.join(self.tmp, ".config", "ai-dev-switchboard", "taiga-push.env")
        self.assertTrue(os.path.isfile(config_path))
        # NB: assert what actually matters -- that nobody but the owner and
        # the explicitly-ACL'd service user can read this file (it holds a
        # live Taiga password) -- rather than a literal 0o600.
        #
        # The script deliberately follows its `chmod 600` with
        # `setfacl -m u:<svc>:r`, and a named-user ACL entry makes stat()
        # report the ACL *mask* in the group triad. So on any filesystem
        # where that setfacl succeeds the mode reads 0o640 even though
        # getfacl still shows `group::---` and `other::---`. Asserting
        # 0o600 therefore failed on exactly the systems where the ACL was
        # working as intended.
        mode = stat.S_IMODE(os.stat(config_path).st_mode)
        self.assertEqual(0o600, mode & 0o606,
                         "owner must keep rw and 'other' must have no access "
                         "at all (got %o)" % mode)
        acl = shutil.which("getfacl")
        if acl:
            facl = subprocess.run([acl, "-cE", config_path], capture_output=True,
                                  text=True).stdout
            self.assertIn("group::---", facl,
                          "real group access must stay empty; the group bits "
                          "in stat() are the ACL mask, not group permission")
            self.assertIn("other::---", facl)
        with open(config_path) as f:
            content = f.read()
        self.assertIn("TAIGA_URL=http://127.0.0.1:1", content)
        self.assertIn("TAIGA_USERNAME=taiga-bot", content)
        self.assertIn("TAIGA_PASSWORD=secret-pw", content)
        self.assertIn("TAIGA_PROJECT_SLUG=my-project", content)
        # Nothing is listening on 127.0.0.1:1 in this sandbox, so --verify
        # legitimately fails here -- the script must propagate that failure.
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secret-pw", result.stdout)


if __name__ == "__main__":
    unittest.main()
