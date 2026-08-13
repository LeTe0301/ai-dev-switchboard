#!/usr/bin/env python3
"""
Tests for the folder-upload wizard's phase-1 backend (POST /projects/upload
— see docs/spec.md "Folder upload -> auto-detect repo(s)"). Covers the pure
detection/zip-slip helpers directly, plus an end-to-end HTTP pass against a
real ThreadingHTTPServer instance of app.Handler.

Stdlib-only (unittest), matching this repo's own zero-dependency ethos —
no pytest, no third-party test runner.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_upload.py
"""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))
os.environ.setdefault("UPLOAD_MAX_BYTES", str(2 * 1024 * 1024))  # 2 MiB, small for fast tests

import app as appmod  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def make_zip(entries: dict) -> bytes:
    """entries: {path-in-zip: bytes-content-or-None (None = directory entry)}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for path, content in entries.items():
            if content is None:
                zi = zipfile.ZipInfo(path if path.endswith("/") else path + "/")
                zf.writestr(zi, b"")
            else:
                zf.writestr(path, content)
    return buf.getvalue()


class ZipEntryTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zt-")
        self.staging = os.path.join(self.tmp, "stage")
        self.staging_real = os.path.realpath(self.staging)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normal_entry_ok(self):
        zi = zipfile.ZipInfo("sub/file.txt")
        target = appmod._zip_entry_target(self.staging_real, zi)
        self.assertEqual(target, os.path.join(self.staging_real, "sub", "file.txt"))

    def test_absolute_path_rejected(self):
        zi = zipfile.ZipInfo("/etc/passwd")
        with self.assertRaises(appmod.UploadRejected):
            appmod._zip_entry_target(self.staging_real, zi)

    def test_dotdot_rejected(self):
        zi = zipfile.ZipInfo("../../etc/passwd")
        with self.assertRaises(appmod.UploadRejected):
            appmod._zip_entry_target(self.staging_real, zi)

    def test_nested_dotdot_rejected(self):
        zi = zipfile.ZipInfo("a/b/../../../escape.txt")
        with self.assertRaises(appmod.UploadRejected):
            appmod._zip_entry_target(self.staging_real, zi)

    def test_nul_byte_rejected(self):
        # zipfile.ZipInfo's own constructor already truncates at the first
        # NUL byte on recent CPython versions (a stdlib hardening not
        # guaranteed on every Python 3 this app might run under) — set
        # .filename directly after construction so this test exercises our
        # own check regardless of what the local zipfile module already
        # does, matching docs/spec.md's explicit NUL-byte requirement.
        zi = zipfile.ZipInfo("placeholder.txt")
        zi.filename = "evil\x00.txt"
        with self.assertRaises(appmod.UploadRejected):
            appmod._zip_entry_target(self.staging_real, zi)

    def test_symlink_entry_rejected(self):
        import stat as statmod
        zi = zipfile.ZipInfo("link")
        zi.external_attr = (statmod.S_IFLNK | 0o777) << 16
        with self.assertRaises(appmod.UploadRejected):
            appmod._zip_entry_target(self.staging_real, zi)

    def test_regular_file_mode_not_treated_as_symlink(self):
        import stat as statmod
        zi = zipfile.ZipInfo("normal.txt")
        zi.external_attr = (statmod.S_IFREG | 0o644) << 16
        target = appmod._zip_entry_target(self.staging_real, zi)
        self.assertTrue(target.endswith("normal.txt"))


class ExtractZipSafelyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ex-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extracts_files_and_dirs(self):
        data = make_zip({"a.txt": b"hello", "sub/b.txt": b"world", "emptydir/": None})
        staging = os.path.join(self.tmp, "tok1")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            appmod._extract_zip_safely(zf, staging)
        with open(os.path.join(staging, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello")
        with open(os.path.join(staging, "sub", "b.txt"), "rb") as f:
            self.assertEqual(f.read(), b"world")
        self.assertTrue(os.path.isdir(os.path.join(staging, "emptydir")))

    def test_bad_entry_leaves_nothing_extracted(self):
        # Build a zip with one good entry and one zip-slip entry; the whole
        # extraction must abort with nothing left behind (aside from the
        # created — now-orphaned — staging dir itself, which the caller is
        # responsible for rmtree-ing on UploadRejected).
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("good.txt", b"ok")
            zf.writestr("../evil.txt", b"bad")
        staging = os.path.join(self.tmp, "tok2")
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
            with self.assertRaises(appmod.UploadRejected):
                appmod._extract_zip_safely(zf, staging)
        # Validation happens before any directory is created.
        self.assertFalse(os.path.exists(staging))


class UnwrapWrapperFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uw-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_wrapper_unwrapped(self):
        os.makedirs(os.path.join(self.tmp, "myrepo-main", "src"))
        open(os.path.join(self.tmp, "myrepo-main", "README.md"), "w").close()
        effective = appmod._unwrap_single_wrapper_folder(self.tmp)
        self.assertEqual(effective, os.path.join(self.tmp, "myrepo-main"))

    def test_hidden_entry_does_not_prevent_unwrap(self):
        os.makedirs(os.path.join(self.tmp, "myrepo-main"))
        open(os.path.join(self.tmp, ".DS_Store"), "w").close()
        effective = appmod._unwrap_single_wrapper_folder(self.tmp)
        self.assertEqual(effective, os.path.join(self.tmp, "myrepo-main"))

    def test_multiple_top_level_entries_not_unwrapped(self):
        os.makedirs(os.path.join(self.tmp, "a"))
        os.makedirs(os.path.join(self.tmp, "b"))
        effective = appmod._unwrap_single_wrapper_folder(self.tmp)
        self.assertEqual(effective, self.tmp)

    def test_single_top_level_file_not_unwrapped(self):
        open(os.path.join(self.tmp, "onlyfile.txt"), "w").close()
        effective = appmod._unwrap_single_wrapper_folder(self.tmp)
        self.assertEqual(effective, self.tmp)

    def test_not_applied_recursively(self):
        os.makedirs(os.path.join(self.tmp, "outer", "inner"))
        effective = appmod._unwrap_single_wrapper_folder(self.tmp)
        self.assertEqual(effective, os.path.join(self.tmp, "outer"))
        self.assertNotEqual(effective, os.path.join(self.tmp, "outer", "inner"))


class DetectStructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ds-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_root_git_no_nested(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        open(os.path.join(self.tmp, "file.py"), "w").close()
        result = appmod.detect_structure(self.tmp)
        self.assertTrue(result["root_has_git"])
        self.assertEqual(result["nested_git_paths"], [])
        self.assertFalse(result["ambiguous"])

    def test_root_git_plus_nested(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        os.makedirs(os.path.join(self.tmp, "vendor", "thing", ".git"))
        os.makedirs(os.path.join(self.tmp, "packages", "foo", ".git"))
        result = appmod.detect_structure(self.tmp)
        self.assertTrue(result["root_has_git"])
        self.assertEqual(sorted(result["nested_git_paths"]),
                         sorted(["vendor/thing", "packages/foo"]))
        self.assertTrue(result["ambiguous"])

    def test_no_root_git_multiple_subdirs(self):
        os.makedirs(os.path.join(self.tmp, "repo-a"))
        os.makedirs(os.path.join(self.tmp, "repo-b"))
        open(os.path.join(self.tmp, "loose.txt"), "w").close()
        result = appmod.detect_structure(self.tmp)
        self.assertFalse(result["root_has_git"])
        self.assertEqual(sorted(result["top_level_subdirs"]), ["repo-a", "repo-b"])
        self.assertEqual(result["loose_top_level_files"], 1)
        self.assertTrue(result["ambiguous"])

    def test_no_root_git_single_subdir_not_ambiguous(self):
        os.makedirs(os.path.join(self.tmp, "onlyone"))
        result = appmod.detect_structure(self.tmp)
        self.assertFalse(result["ambiguous"])

    def test_hidden_top_level_dirs_excluded_from_subdirs_list(self):
        os.makedirs(os.path.join(self.tmp, ".hidden"))
        os.makedirs(os.path.join(self.tmp, "visible"))
        result = appmod.detect_structure(self.tmp)
        self.assertEqual(result["top_level_subdirs"], ["visible"])

    def test_does_not_descend_into_git_internals(self):
        # A .git directory can itself contain directories that are not
        # named .git — make sure the walk doesn't misidentify anything
        # inside .git as a nested repo, and doesn't choke walking it.
        os.makedirs(os.path.join(self.tmp, ".git", "objects", "pack"))
        result = appmod.detect_structure(self.tmp)
        self.assertTrue(result["root_has_git"])
        self.assertEqual(result["nested_git_paths"], [])

    def test_registers_nothing(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        appmod.detect_structure(self.tmp)
        self.assertFalse(os.path.isdir(appmod.PROJECTS_DIR) and
                         os.listdir(appmod.PROJECTS_DIR))

    def test_deeply_nested_git_inside_a_nested_repos_own_tree_is_reported(self):
        # Deliberate decision (see docs/implementation.md "Deep .git
        # nesting" — cycle 1 flagged this, cycle 2 confirms it): the walk
        # only prunes ".git" itself out of os.walk's dirnames, not its
        # parent directory's whole subtree. So a repo nested inside another
        # already-discovered nested repo's own working tree (as opposed to
        # inside that repo's .git *internals*, which IS pruned — see
        # test_does_not_descend_into_git_internals above) is still reported
        # as its own candidate. Genuinely deep nesting, repo-in-repo-in-repo.
        os.makedirs(os.path.join(self.tmp, ".git"))
        os.makedirs(os.path.join(self.tmp, "vendor", "thing", ".git"))
        os.makedirs(os.path.join(self.tmp, "vendor", "thing", "subvendor", ".git"))
        result = appmod.detect_structure(self.tmp)
        self.assertEqual(sorted(result["nested_git_paths"]),
                         sorted(["vendor/thing", "vendor/thing/subvendor"]))


class UploadEndpointTests(unittest.TestCase):
    """
    End-to-end HTTP tests against a real ThreadingHTTPServer running
    app.Handler, covering docs/spec.md's phase-1 acceptance criteria.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        appmod.SESSIONS.clear()
        os.makedirs(appmod.UPLOAD_STAGING_DIR, exist_ok=True)

    def _login(self):
        req = urllib.request.Request(
            f"{self.base}/login", method="POST",
            data=json.dumps({"username": "testuser", "password": "testpass"}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        cookie = resp.headers.get("Set-Cookie").split(";")[0]
        return cookie

    def _totp_code(self):
        return appmod.totp_at(appmod.TOTP_SECRET, __import__("time").time())

    def _upload(self, cookie, data, code=None, content_length_override=None):
        url = f"{self.base}/projects/upload"
        if code:
            url += f"?code={code}"
        req = urllib.request.Request(
            url, method="POST", data=data,
            headers={"Content-Type": "application/zip", "Cookie": cookie})
        if content_length_override is not None:
            req.add_header("Content-Length", str(content_length_override))
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body)
            except ValueError:
                return e.code, {}

    def test_upload_without_code_returns_428(self):
        cookie = self._login()
        data = make_zip({".git/HEAD": b"ref: refs/heads/main\n", "readme.txt": b"hi"})
        status, payload = self._upload(cookie, data)
        self.assertEqual(status, 428)

    def test_upload_with_wrong_code_returns_403(self):
        cookie = self._login()
        data = make_zip({".git/HEAD": b"ref\n"})
        status, payload = self._upload(cookie, data, code="000000")
        self.assertEqual(status, 403)

    def test_upload_with_correct_code_stages_and_detects(self):
        cookie = self._login()
        data = make_zip({
            "myrepo/.git/HEAD": b"ref: refs/heads/main\n",
            "myrepo/README.md": b"hello",
            "myrepo/vendor/thing/.git/HEAD": b"ref\n",
            "myrepo/vendor/thing/file.txt": b"vendored",
        })
        status, payload = self._upload(cookie, data, code=self._totp_code())
        self.assertEqual(status, 200)
        self.assertTrue(payload["root_has_git"])
        self.assertEqual(payload["nested_git_paths"], ["vendor/thing"])
        self.assertTrue(payload["ambiguous"])
        self.assertEqual(payload["root_name"], "myrepo")
        self.assertIn("token", payload)
        # Staged, not registered:
        self.assertFalse(payload["root_name"] in appmod.instance_names())
        staged = os.path.join(appmod.UPLOAD_STAGING_DIR, payload["token"])
        self.assertTrue(os.path.isdir(staged))

        # Cleanup for the next test run of this same process.
        shutil.rmtree(staged, ignore_errors=True)

    def test_second_action_in_session_does_not_reprompt_totp(self):
        cookie = self._login()
        data = make_zip({"a/.git/HEAD": b"ref\n"})
        status, payload = self._upload(cookie, data, code=self._totp_code())
        self.assertEqual(status, 200)
        shutil.rmtree(os.path.join(appmod.UPLOAD_STAGING_DIR, payload["token"]),
                      ignore_errors=True)

        data2 = make_zip({"b/.git/HEAD": b"ref\n"})
        status2, payload2 = self._upload(cookie, data2)  # no code this time
        self.assertEqual(status2, 200)
        shutil.rmtree(os.path.join(appmod.UPLOAD_STAGING_DIR, payload2["token"]),
                      ignore_errors=True)

    def test_zero_length_body_rejected_413(self):
        cookie = self._login()
        status, payload = self._upload(cookie, b"", code=self._totp_code())
        self.assertEqual(status, 413)

    def test_oversized_content_length_rejected_413(self):
        # Proves the "before reading the body at all" half of the size
        # check (docs/spec.md "Size limits") — a raw socket sends a
        # Content-Length far over the cap but only a few actual bytes, and
        # the server must still respond 413 without waiting to read
        # anywhere near that many bytes off the wire.
        import socket
        cookie = self._login()
        code = self._totp_code()
        huge_length = appmod.UPLOAD_MAX_BYTES + 1
        request = (
            f"POST /projects/upload?code={code} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Content-Type: application/zip\r\n"
            f"Cookie: {cookie}\r\n"
            f"Content-Length: {huge_length}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(request)
            try:
                sock.sendall(b"x" * 16)  # far short of huge_length
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                # The server may have already responded and closed its side
                # of the connection by the time we get here (it never reads
                # the body at all in this rejection path) — a benign race,
                # not a test failure; the response is read below regardless.
                pass
            response = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode()
        self.assertIn("413", status_line)

    def test_corrupt_zip_rejected_400_and_nothing_staged(self):
        cookie = self._login()
        before = set(os.listdir(appmod.UPLOAD_STAGING_DIR)) \
            if os.path.isdir(appmod.UPLOAD_STAGING_DIR) else set()
        status, payload = self._upload(cookie, b"not a zip file at all",
                                       code=self._totp_code())
        self.assertEqual(status, 400)
        after = set(os.listdir(appmod.UPLOAD_STAGING_DIR)) \
            if os.path.isdir(appmod.UPLOAD_STAGING_DIR) else set()
        self.assertEqual(before, after)

    def test_empty_zip_rejected_400(self):
        cookie = self._login()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        status, payload = self._upload(cookie, buf.getvalue(), code=self._totp_code())
        self.assertEqual(status, 400)

    def test_zip_slip_entry_rejected_400_nothing_staged(self):
        cookie = self._login()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("good.txt", b"ok")
            zf.writestr("../../evil.txt", b"bad")
        before = set(os.listdir(appmod.UPLOAD_STAGING_DIR)) \
            if os.path.isdir(appmod.UPLOAD_STAGING_DIR) else set()
        status, payload = self._upload(cookie, buf.getvalue(), code=self._totp_code())
        self.assertEqual(status, 400)
        after = set(os.listdir(appmod.UPLOAD_STAGING_DIR)) \
            if os.path.isdir(appmod.UPLOAD_STAGING_DIR) else set()
        self.assertEqual(before, after)

    def test_uncompressed_total_over_cap_rejected_413(self):
        cookie = self._login()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Highly compressible content so the compressed upload stays
            # under UPLOAD_MAX_BYTES while the *uncompressed* total exceeds
            # it — the zip-bomb-shaped mismatch check 2 exists for.
            zf.writestr("big.bin", b"\x00" * (appmod.UPLOAD_MAX_BYTES + 1024))
        data = buf.getvalue()
        self.assertLess(len(data), appmod.UPLOAD_MAX_BYTES)
        status, payload = self._upload(cookie, data, code=self._totp_code())
        self.assertEqual(status, 413)

    def test_unauthenticated_upload_rejected_401(self):
        data = make_zip({"a/.git/HEAD": b"ref\n"})
        status, payload = self._upload("session=bogus", data)
        self.assertEqual(status, 401)


class DeriveProjectNameTests(unittest.TestCase):
    """_derive_project_name — docs/spec.md step 5 (naming/sanitizing)."""

    def test_clean_name_passthrough(self):
        self.assertEqual(appmod._derive_project_name("myrepo"), "myrepo")

    def test_strips_disallowed_characters(self):
        self.assertEqual(appmod._derive_project_name("my@repo!.git"), "myrepogit")

    def test_strips_leading_non_alnum(self):
        self.assertEqual(appmod._derive_project_name("---leading"), "leading")

    def test_falls_back_when_nothing_survives(self):
        name = appmod._derive_project_name("@@@###")
        self.assertTrue(name.startswith("upload-"))
        self.assertTrue(appmod.NAME_RE.match(name))

    def test_empty_raw_falls_back(self):
        name = appmod._derive_project_name("")
        self.assertTrue(name.startswith("upload-"))

    def test_truncated_to_60_chars(self):
        name = appmod._derive_project_name("a" * 100)
        self.assertLessEqual(len(name), 60)
        self.assertTrue(appmod.NAME_RE.match(name))


class CreateProjectsFromSelectionTests(unittest.TestCase):
    """
    Phase 2's core logic (docs/spec.md "Detection and the two-phase
    protocol" phase 2 + "Partial-failure semantics"), tested directly
    against a staged tree. _register_via_privileged_script (the one line
    that shells out to `sudo` + the real script) is monkeypatched to a fake
    that copies without sudo — the real privileged script itself is covered
    end-to-end by tests/test_new_project_from_upload.py.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cpfs-")
        self._orig_register = appmod._register_via_privileged_script
        self._orig_projects_dir = appmod.PROJECTS_DIR
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(appmod.PROJECTS_DIR)

    def tearDown(self):
        appmod._register_via_privileged_script = self._orig_register
        appmod.PROJECTS_DIR = self._orig_projects_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_register(self, fail_names=frozenset()):
        def _fake(source, name):
            if name in fail_names:
                return subprocess.CompletedProcess([], 1, "", "simulated TOCTOU race")
            shutil.copytree(source, os.path.join(appmod.PROJECTS_DIR, name))
            return subprocess.CompletedProcess([], 0, "ok", "")
        appmod._register_via_privileged_script = _fake

    def test_single_mode_registers_root_only(self):
        staging = os.path.join(self.tmp, "stage1")
        os.makedirs(os.path.join(staging, ".git"))
        open(os.path.join(staging, "file.txt"), "w").close()
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(staging, "single", [])
        self.assertTrue(ok, err)
        self.assertEqual(registered, [os.path.basename(staging)])
        self.assertEqual(skipped, 0)
        self.assertTrue(os.path.isdir(os.path.join(appmod.PROJECTS_DIR, os.path.basename(staging))))

    def test_split_monorepo_registers_root_plus_selected_nested_and_duplicates(self):
        staging = os.path.join(self.tmp, "monorepo")
        os.makedirs(os.path.join(staging, ".git"))
        os.makedirs(os.path.join(staging, "vendor", "thing", ".git"))
        open(os.path.join(staging, "vendor", "thing", "f.txt"), "w").close()
        os.makedirs(os.path.join(staging, "packages", "foo", ".git"))
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(
            staging, "split", ["vendor/thing"])
        self.assertTrue(ok, err)
        self.assertEqual(sorted(registered), sorted([os.path.basename(staging), "thing"]))
        self.assertEqual(skipped, 1)  # packages/foo not selected
        # Root's own copy still contains the split-out subfolder's files
        # too — deliberate duplication (docs/spec.md Non-goals).
        self.assertTrue(os.path.isfile(os.path.join(
            appmod.PROJECTS_DIR, os.path.basename(staging), "vendor", "thing", "f.txt")))
        self.assertTrue(os.path.isfile(os.path.join(appmod.PROJECTS_DIR, "thing", "f.txt")))

    def test_split_monorepo_zero_selected_equivalent_to_single(self):
        staging = os.path.join(self.tmp, "monorepo2")
        os.makedirs(os.path.join(staging, ".git"))
        os.makedirs(os.path.join(staging, "vendor", "thing", ".git"))
        # A second top-level entry, so this fixture isn't itself mistaken
        # for a single-wrapper-folder shape (_unwrap_single_wrapper_folder
        # would otherwise unwrap straight into "vendor", losing the root's
        # own .git from this test's perspective — not realistic for what a
        # real staged upload looks like, which always has more at its top
        # level than just one already-nested-repo directory).
        open(os.path.join(staging, "README.md"), "w").close()
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(staging, "split", [])
        self.assertTrue(ok, err)
        self.assertEqual(registered, [os.path.basename(staging)])
        self.assertEqual(skipped, 1)

    def test_split_no_root_git_registers_only_selected(self):
        staging = os.path.join(self.tmp, "folderofrepos")
        os.makedirs(os.path.join(staging, "repo-a"))
        os.makedirs(os.path.join(staging, "repo-b"))
        os.makedirs(os.path.join(staging, "repo-c"))
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(
            staging, "split", ["repo-a", "repo-c"])
        self.assertTrue(ok, err)
        self.assertEqual(sorted(registered), ["repo-a", "repo-c"])
        self.assertEqual(skipped, 1)
        self.assertNotIn("repo-b", appmod.instance_names())

    def test_split_no_root_git_zero_selected_rejected(self):
        staging = os.path.join(self.tmp, "folderofrepos2")
        os.makedirs(os.path.join(staging, "repo-a"))
        os.makedirs(os.path.join(staging, "repo-b"))
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(staging, "split", [])
        self.assertFalse(ok)
        self.assertIn("select at least one", err)
        self.assertEqual(registered, [])

    def test_stale_selected_path_rejected(self):
        staging = os.path.join(self.tmp, "folderofrepos3")
        os.makedirs(os.path.join(staging, "repo-a"))
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(
            staging, "split", ["not-a-real-candidate"])
        self.assertFalse(ok)
        self.assertIn("stale or tampered", err)
        self.assertEqual(registered, [])

    def test_collision_with_existing_project_rejects_whole_call(self):
        staging = os.path.join(self.tmp, "myrepo")
        os.makedirs(os.path.join(staging, ".git"))
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "myrepo"))
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(staging, "single", [])
        self.assertFalse(ok)
        self.assertIn("collision", err)
        self.assertEqual(registered, [])

    def test_collision_between_root_and_split_subfolder_rejects_whole_call(self):
        staging = os.path.join(self.tmp, "dup")
        os.makedirs(os.path.join(staging, ".git"))
        os.makedirs(os.path.join(staging, "sub", "dup", ".git"))
        open(os.path.join(staging, "README.md"), "w").close()  # avoid single-wrapper unwrap
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(
            staging, "split", ["sub/dup"])
        self.assertFalse(ok)
        self.assertIn("collision", err)
        self.assertEqual(registered, [])

    def test_toctou_race_one_project_fails_siblings_not_rolled_back(self):
        staging = os.path.join(self.tmp, "monorepo3")
        os.makedirs(os.path.join(staging, ".git"))
        os.makedirs(os.path.join(staging, "vendor", "thing", ".git"))
        open(os.path.join(staging, "README.md"), "w").close()  # avoid single-wrapper unwrap
        self._fake_register(fail_names={"thing"})
        ok, err, registered, skipped = appmod.create_projects_from_selection(
            staging, "split", ["vendor/thing"])
        self.assertFalse(ok)
        self.assertIn("thing", err)
        # The root sibling registered before the failing one is NOT rolled
        # back (docs/spec.md "Partial-failure semantics").
        self.assertEqual(registered, [os.path.basename(staging)])
        self.assertTrue(os.path.isdir(os.path.join(appmod.PROJECTS_DIR, os.path.basename(staging))))

    def test_invalid_mode_rejected(self):
        staging = os.path.join(self.tmp, "x")
        os.makedirs(staging)
        self._fake_register()
        ok, err, registered, skipped = appmod.create_projects_from_selection(staging, "bogus", [])
        self.assertFalse(ok)
        self.assertEqual(registered, [])


class ConfirmUploadTests(unittest.TestCase):
    """confirm_upload() — token validation + confirm-triggered cleanup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cu-")
        self._orig_register = appmod._register_via_privileged_script
        self._orig_projects_dir = appmod.PROJECTS_DIR
        self._orig_staging_dir = appmod.UPLOAD_STAGING_DIR
        appmod.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        appmod.UPLOAD_STAGING_DIR = os.path.join(self.tmp, "uploads")
        os.makedirs(appmod.PROJECTS_DIR)
        os.makedirs(appmod.UPLOAD_STAGING_DIR)

    def tearDown(self):
        appmod._register_via_privileged_script = self._orig_register
        appmod.PROJECTS_DIR = self._orig_projects_dir
        appmod.UPLOAD_STAGING_DIR = self._orig_staging_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_register(self):
        def _fake(source, name):
            shutil.copytree(source, os.path.join(appmod.PROJECTS_DIR, name))
            return subprocess.CompletedProcess([], 0, "ok", "")
        appmod._register_via_privileged_script = _fake

    def test_unknown_token_returns_404(self):
        status, payload = appmod.confirm_upload("0" * 32, "single", [])
        self.assertEqual(status, 404)

    def test_malformed_token_rejected_without_touching_filesystem(self):
        # The token is used directly as a filesystem path component —
        # anything not the exact secrets.token_hex(16) shape must be
        # rejected outright, same defensive posture as zip-slip protection.
        status, payload = appmod.confirm_upload("../../etc", "single", [])
        self.assertEqual(status, 404)

    def test_successful_confirm_cleans_up_staging(self):
        token = "a" * 32
        staging = os.path.join(appmod.UPLOAD_STAGING_DIR, token)
        os.makedirs(os.path.join(staging, ".git"))
        self._fake_register()
        status, payload = appmod.confirm_upload(token, "single", [])
        self.assertEqual(status, 200)
        self.assertEqual(payload["registered"], [token])
        self.assertFalse(os.path.exists(staging))

    def test_failed_confirm_leaves_staging_in_place_for_retry(self):
        # Reviewer Defect 1: a failed confirm must NOT delete staging, so
        # the UI's "Back to review" button can retry the same token.
        token = "b" * 32
        staging = os.path.join(appmod.UPLOAD_STAGING_DIR, token)
        os.makedirs(os.path.join(staging, "onlyone"))
        self._fake_register()
        status, payload = appmod.confirm_upload(token, "split", ["bogus"])
        self.assertEqual(status, 400)
        self.assertTrue(os.path.exists(staging))

    def test_retry_on_same_token_after_failed_confirm_evaluated_fresh(self):
        # Reviewer Defect 1 repro: a first confirm call fails (stale
        # selection here, standing in for e.g. a name collision), staging
        # survives, and a second confirm call on the *same* token with a
        # different mode/selected is evaluated fresh against the
        # still-staged tree rather than 404ing with "upload expired."
        token = "c" * 32
        staging = os.path.join(appmod.UPLOAD_STAGING_DIR, token)
        os.makedirs(os.path.join(staging, "repo-a"))
        os.makedirs(os.path.join(staging, "repo-b"))
        self._fake_register()
        status1, payload1 = appmod.confirm_upload(token, "split", ["bogus"])
        self.assertEqual(status1, 400)
        self.assertTrue(os.path.exists(staging))
        status2, payload2 = appmod.confirm_upload(token, "split", ["repo-a"])
        self.assertEqual(status2, 200)
        self.assertEqual(payload2["registered"], ["repo-a"])
        self.assertFalse(os.path.exists(staging))


class ConfirmUploadEndpointTests(unittest.TestCase):
    """
    End-to-end HTTP tests against POST /projects/upload/confirm, covering
    docs/spec.md's phase-2 acceptance criteria. _register_via_privileged_script
    is monkeypatched (see CreateProjectsFromSelectionTests' docstring) —
    the real script is covered by tests/test_new_project_from_upload.py.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        appmod.SESSIONS.clear()
        os.makedirs(appmod.UPLOAD_STAGING_DIR, exist_ok=True)
        self._orig_register = appmod._register_via_privileged_script

        def _fake(source, name):
            shutil.copytree(source, os.path.join(appmod.PROJECTS_DIR, name))
            return subprocess.CompletedProcess([], 0, "ok", "")
        appmod._register_via_privileged_script = _fake

    def tearDown(self):
        appmod._register_via_privileged_script = self._orig_register

    def _login(self):
        req = urllib.request.Request(
            f"{self.base}/login", method="POST",
            data=json.dumps({"username": "testuser", "password": "testpass"}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        return resp.headers.get("Set-Cookie").split(";")[0]

    def _totp_code(self):
        return appmod.totp_at(appmod.TOTP_SECRET, time.time())

    def _upload_and_stage(self, cookie, entries):
        data = make_zip(entries)
        req = urllib.request.Request(
            f"{self.base}/projects/upload?code={self._totp_code()}", method="POST",
            data=data, headers={"Content-Type": "application/zip", "Cookie": cookie})
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())

    def _confirm(self, cookie, token, mode, selected, code=None):
        body = {"token": token, "mode": mode, "selected": selected}
        if code:
            body["code"] = code
        req = urllib.request.Request(
            f"{self.base}/projects/upload/confirm", method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {}

    def test_single_mode_registers_root_and_shows_up_in_instance_names(self):
        cookie = self._login()
        detection = self._upload_and_stage(cookie, {
            "myrepo/.git/HEAD": b"ref\n", "myrepo/README.md": b"hi",
        })
        status, payload = self._confirm(cookie, detection["token"], "single", [])
        self.assertEqual(status, 200)
        self.assertEqual(payload["registered"], ["myrepo"])
        self.assertIn("myrepo", appmod.instance_names())
        self.assertFalse(os.path.isdir(os.path.join(appmod.UPLOAD_STAGING_DIR, detection["token"])))
        shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, "myrepo"), ignore_errors=True)

    def test_split_no_root_git_skips_unselected(self):
        cookie = self._login()
        detection = self._upload_and_stage(cookie, {
            "up/repo-a/f.txt": b"1", "up/repo-b/f.txt": b"2", "up/repo-c/f.txt": b"3",
        })
        status, payload = self._confirm(cookie, detection["token"], "split", ["repo-a", "repo-c"])
        self.assertEqual(status, 200)
        self.assertEqual(sorted(payload["registered"]), ["repo-a", "repo-c"])
        self.assertEqual(payload["skipped"], 1)
        self.assertNotIn("repo-b", appmod.instance_names())
        for n in ("repo-a", "repo-c"):
            shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, n), ignore_errors=True)

    def test_zero_selected_split_no_root_git_rejected(self):
        cookie = self._login()
        detection = self._upload_and_stage(cookie, {
            "up2/repo-a/f.txt": b"1", "up2/repo-b/f.txt": b"2",
        })
        status, payload = self._confirm(cookie, detection["token"], "split", [])
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_expired_or_unknown_token_returns_404(self):
        cookie = self._login()
        status, payload = self._confirm(cookie, "0" * 32, "single", [], code=self._totp_code())
        self.assertEqual(status, 404)

    def test_stale_selected_path_returns_400(self):
        cookie = self._login()
        detection = self._upload_and_stage(cookie, {
            "up3/repo-a/f.txt": b"1", "up3/repo-b/f.txt": b"2",
        })
        status, payload = self._confirm(cookie, detection["token"], "split", ["not-real"])
        self.assertEqual(status, 400)

    def test_collision_rejects_and_registers_nothing(self):
        cookie = self._login()
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "already-exists"), exist_ok=True)
        detection = self._upload_and_stage(cookie, {"already-exists/.git/HEAD": b"ref\n"})
        status, payload = self._confirm(cookie, detection["token"], "single", [])
        self.assertEqual(status, 400)
        self.assertIn("collision", payload["error"])
        shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, "already-exists"), ignore_errors=True)

    def test_status_shows_registered_project_no_separate_code_path(self):
        cookie = self._login()
        detection = self._upload_and_stage(cookie, {"statusrepo/.git/HEAD": b"ref\n"})
        status, payload = self._confirm(cookie, detection["token"], "single", [])
        self.assertEqual(status, 200)
        req = urllib.request.Request(f"{self.base}/status", headers={"Cookie": cookie})
        resp = urllib.request.urlopen(req)
        s = json.loads(resp.read())
        names = [i["name"] for i in s["instances"]]
        self.assertIn("statusrepo", names)
        shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, "statusrepo"), ignore_errors=True)

    def test_confirm_prompts_for_totp_via_json_body_when_not_cleared(self):
        cookie = self._login()
        detection = self._upload_and_stage(cookie, {"x/.git/HEAD": b"ref\n"})
        sid = cookie.split("=", 1)[1]
        appmod.SESSIONS[sid]["totp_ok"] = False  # force confirm to need a code again
        status, payload = self._confirm(cookie, detection["token"], "single", [])
        self.assertEqual(status, 428)
        status2, payload2 = self._confirm(cookie, detection["token"], "single", [],
                                          code=self._totp_code())
        self.assertEqual(status2, 200)
        shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, "x"), ignore_errors=True)

    def test_unauthenticated_confirm_rejected_401(self):
        status, payload = self._confirm("session=bogus", "0" * 32, "single", [])
        self.assertEqual(status, 401)

    def test_retry_after_failed_confirm_evaluated_fresh_not_expired(self):
        # Reviewer test-review.md Defect 1 repro, end-to-end over real HTTP:
        # a failed confirm (name collision) must leave staging in place so
        # the "Back to review" retry (same token, tweaked mode/selected) is
        # evaluated fresh on its own merits instead of 404ing with "upload
        # expired" (the bug: staging was deleted unconditionally, so the
        # retry always 404'd even a moment later).
        cookie = self._login()
        os.makedirs(os.path.join(appmod.PROJECTS_DIR, "myrepo"), exist_ok=True)
        detection = self._upload_and_stage(cookie, {
            "myrepo/.git/HEAD": b"ref\n",
            "myrepo/vendor/thing/.git/HEAD": b"ref\n",
            "myrepo/vendor/thing/lib.py": b"x",
        })
        status1, payload1 = self._confirm(cookie, detection["token"], "single", [])
        self.assertEqual(status1, 400)
        self.assertIn("collision", payload1["error"])
        self.assertTrue(
            os.path.isdir(os.path.join(appmod.UPLOAD_STAGING_DIR, detection["token"])),
            "staging must survive a failed confirm so a retry on the same "
            "token can be evaluated fresh")

        # Retry with a tweaked selection, exactly as "Back to review" would
        # do. Root still collides with the pre-existing "myrepo" (this
        # fixture always re-registers root in split mode too, since
        # root_has_git is true), so this retry fails again — but on its own
        # merits (a real collision), not a spurious 404 "upload expired."
        status2, payload2 = self._confirm(
            cookie, detection["token"], "split", ["vendor/thing"])
        self.assertNotEqual(status2, 404)
        self.assertEqual(status2, 400)
        self.assertIn("collision", payload2["error"])
        self.assertTrue(
            os.path.isdir(os.path.join(appmod.UPLOAD_STAGING_DIR, detection["token"])),
            "staging still must survive this second failed confirm too")

        # Now clear the collision and retry a third time on the SAME
        # token — this time it should actually succeed, proving staging
        # was genuinely still usable, not just present-but-stale.
        shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, "myrepo"))
        status3, payload3 = self._confirm(cookie, detection["token"], "single", [])
        self.assertEqual(status3, 200)
        self.assertEqual(payload3["registered"], ["myrepo"])
        self.assertFalse(
            os.path.isdir(os.path.join(appmod.UPLOAD_STAGING_DIR, detection["token"])))
        shutil.rmtree(os.path.join(appmod.PROJECTS_DIR, "myrepo"), ignore_errors=True)


class UploadStagingTTLSweepTests(unittest.TestCase):
    """
    _reap_dead_state()'s TTL sweep for abandoned upload staging dirs
    (docs/spec.md "Two-phase protocol" — reuses the existing
    "opportunistic cleanup on a request that already happens often"
    precedent rather than a dedicated background thread).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ttl-")
        self._orig_staging_dir = appmod.UPLOAD_STAGING_DIR
        self._orig_ttl = appmod.UPLOAD_STAGING_TTL_SECONDS
        appmod.UPLOAD_STAGING_DIR = self.tmp
        appmod.UPLOAD_STAGING_TTL_SECONDS = 5

    def tearDown(self):
        appmod.UPLOAD_STAGING_DIR = self._orig_staging_dir
        appmod.UPLOAD_STAGING_TTL_SECONDS = self._orig_ttl
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_expired_unconfirmed_upload_swept_recent_one_kept(self):
        old_dir = os.path.join(self.tmp, "c" * 32)
        new_dir = os.path.join(self.tmp, "d" * 32)
        os.makedirs(old_dir)
        os.makedirs(new_dir)
        old_time = time.time() - 3600
        os.utime(old_dir, (old_time, old_time))

        appmod._reap_dead_state()

        self.assertFalse(os.path.isdir(old_dir))
        self.assertTrue(os.path.isdir(new_dir))


class UploadMaxEntriesEnvVarTests(unittest.TestCase):
    """
    UPLOAD_MAX_ENTRIES (docs/spec.md "Upload wizard polish", proposed
    approach #1) must be read the same way its siblings
    (UPLOAD_STAGING_TTL_SECONDS, GITEA_POLL_INTERVAL_SECONDS) already are:
    int(os.environ.get("UPLOAD_MAX_ENTRIES", "20000")). Imports app.py in a
    fresh subprocess (rather than mutating the already-imported `appmod`
    shared by every other test in this module) so each case gets a real,
    isolated module-import-time env read -- same technique
    tests/test_deploy_frontend.js uses to get a real rendered <script>.
    """

    def _import_and_read(self, env_overrides):
        env = dict(os.environ)
        env.update(env_overrides)
        env.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
        code = (
            "import sys; sys.path.insert(0, 'app'); import app as appmod; "
            "print(appmod.UPLOAD_MAX_ENTRIES)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True, check=True)
        return int(out.stdout.strip())

    def test_env_var_set_overrides_default(self):
        self.assertEqual(self._import_and_read({"UPLOAD_MAX_ENTRIES": "5"}), 5)

    def test_env_var_unset_keeps_default_20000(self):
        self.assertNotIn("UPLOAD_MAX_ENTRIES", os.environ)
        self.assertEqual(self._import_and_read({}), 20000)


if __name__ == "__main__":
    unittest.main()
