"""
Tests for backlog item 7 part 1 -- board_read/board_write on the lead loop
(docs/spec.md "Spec: Backlog item 7 part 1"). Two halves, mirroring the
spec's own two affected modules:

Part A (`app/taiga_board.py`) -- pure unit tests following tests/
test_taiga_push.py's own "monkeypatch the one shared HTTP request function"
seam exactly (no live Taiga instance is reachable in this environment,
same as that file's own header comment explains): every test here
monkeypatches taiga_board._taiga_request, the one function that actually
calls urllib.request.

Part B (`app/teams.py`) -- the lead-loop tool contract additions
(_LEAD_TOOL_NAMES/_LEAD_TOOL_REQUIRED_ARGS/_validate_lead_action()/
team_step()'s two new branches), the generalized inbox
(_write_board_inbox()), and resolve_board_write() (including its own
concurrent-resolve race test, mirroring tests/test_team_routes.py's
TeamResolveEndpointTests.test_two_concurrent_resolves_exactly_one_succeeds
but driven directly against resolve_board_write() rather than through an
HTTP route, since part 1 has no route at all). Every Taiga call from this
half is stubbed by monkeypatching teams.taiga_board's own module-level
functions (resolve_session/get_userstory/list_userstories/
list_userstory_statuses/set_status/amend_description/append_comment) --
Part A already covers taiga_board.py's own internal correctness, so Part B
treats it as a black box, the same layering CallLeadDispatchTests already
uses for _call_lead() vs. its own tier adapters in tests/test_teams_lead.py.

Run with:
    python3 -m unittest discover -s tests -v
"""
import builtins
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-board-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import teams as teamsmod  # noqa: E402
import taiga_board as tb  # noqa: E402


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


# ═══════════════════════════ Part A: app/taiga_board.py ════════════════════
class _TaigaRequestPatchCase(unittest.TestCase):
    def setUp(self):
        self._orig = tb._taiga_request

    def tearDown(self):
        tb._taiga_request = self._orig


class TaigaRequestSeamTests(unittest.TestCase):
    def setUp(self):
        self._orig_urlopen = tb.urllib.request.urlopen

    def tearDown(self):
        tb.urllib.request.urlopen = self._orig_urlopen

    def test_success_returns_parsed_json(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"ok": True}).encode()

        tb.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        self.assertEqual(tb._taiga_request("http://x", "GET", "/api/v1/foo"), {"ok": True})

    def test_http_error_raises_taiga_http_error_with_status(self):
        def raise_http_error(req, timeout=None):
            raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, io.BytesIO(b""))

        tb.urllib.request.urlopen = raise_http_error
        with self.assertRaises(tb.TaigaHTTPError) as ctx:
            tb._taiga_request("http://x", "GET", "/api/v1/foo")
        self.assertEqual(ctx.exception.status, 404)

    def test_connection_failure_raises_taiga_connection_error(self):
        def raise_url_error(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        tb.urllib.request.urlopen = raise_url_error
        with self.assertRaises(tb.TaigaConnectionError):
            tb._taiga_request("http://x", "GET", "/api/v1/foo")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taiga-board-config-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_raises_clear_taiga_push_error(self):
        missing = os.path.join(self.tmp, "nope.env")
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.load_config(missing)
        self.assertIn("Taiga isn't configured", str(ctx.exception))
        self.assertIn("taiga-configure-push.sh", str(ctx.exception))

    def test_parses_key_value_lines(self):
        path = write_config(os.path.join(self.tmp, "taiga-push.env"))
        cfg = tb.load_config(path)
        self.assertEqual(cfg["TAIGA_URL"], "http://127.0.0.1:9000")
        self.assertEqual(cfg["TAIGA_PROJECT_SLUG"], "my-project")


class LoadConfigPermissionTests(unittest.TestCase):
    """Item 29 (v2): load_config()'s open() call must distinguish
    "file exists but can't be read" (PermissionError) from "file genuinely
    missing" (FileNotFoundError) and from any other OSError -- three
    distinct outcomes, and specifically proves the except-clause ORDERING
    fix actually takes effect (PermissionError is an OSError subclass, so
    a bare `except OSError:` ahead of it -- the pre-fix shape -- would
    silently swallow it into the generic "isn't configured" message and
    the more specific `except PermissionError:` clause below it would be
    unreachable dead code). Not reproducible via a real multi-user ACL
    setup in this sandbox (root runs everything here, so a real chmod/
    setfacl denial wouldn't actually deny root's own open() call) --
    monkeypatches builtins.open itself instead, same "mock the boundary
    the real OS/user separation would otherwise provide" technique tests/
    test_team_routes.py's own _get_forbidding_open_of() already
    establishes, scoped here to raise instead of forbid."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taiga-board-config-perm-")
        self.path = os.path.join(self.tmp, "taiga-push.env")
        with open(self.path, "w") as f:
            f.write("TAIGA_URL=http://x\n")
        os.chmod(self.path, 0o600)  # avoid _check_config_permissions()'s own unrelated warning
        self._real_open = builtins.open
        self.addCleanup(self._restore_open)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _restore_open(self):
        builtins.open = self._real_open

    def _patch_open_raises(self, exc):
        target = os.path.abspath(self.path)

        def _fake_open(file, *a, **kw):
            if os.path.abspath(str(file)) == target:
                raise exc
            return self._real_open(file, *a, **kw)
        builtins.open = _fake_open

    def test_permission_error_raises_distinct_actionable_message(self):
        self._patch_open_raises(PermissionError(13, "Permission denied"))
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.load_config(self.path)
        msg = str(ctx.exception)
        self.assertIn("couldn't read it (permission denied)", msg)
        self.assertIn("setfacl", msg)
        self.assertIn("taiga-configure-push.sh", msg)
        # Must NOT be conflated with the genuinely-missing-file message --
        # this is the exact bug item 29 (v2) fixes.
        self.assertNotIn("Taiga isn't configured", msg)

    def test_other_oserror_during_read_still_falls_back_to_not_configured_message(self):
        # A non-permission OSError raised mid-open (e.g. a transient I/O
        # error) must still fall into the pre-existing generic branch --
        # confirms the new `except PermissionError:` clause is genuinely
        # more specific, not a wholesale replacement of the fallback.
        self._patch_open_raises(OSError(5, "Input/output error"))
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.load_config(self.path)
        msg = str(ctx.exception)
        self.assertIn("Taiga isn't configured", msg)
        self.assertIn("taiga-configure-push.sh", msg)
        self.assertNotIn("permission denied", msg)

    def test_genuinely_missing_file_still_gives_the_same_not_configured_message(self):
        # Contrast case, unaffected by this fix -- os.path.isfile() itself
        # (not open()) is what raises this one, so it's checked before
        # open() is ever reached at all.
        missing = os.path.join(self.tmp, "does-not-exist.env")
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.load_config(missing)
        msg = str(ctx.exception)
        self.assertIn("Taiga isn't configured", msg)
        self.assertNotIn("permission denied", msg)


class AuthenticateTests(_TaigaRequestPatchCase):
    def test_returns_token_on_success(self):
        tb._taiga_request = lambda *a, **k: {"auth_token": "tok-1"}
        self.assertEqual(tb.authenticate("http://x", "u", "p"), "tok-1")

    def test_http_error_raises_clear_bad_credentials_message(self):
        def fake(*a, **k):
            raise tb.TaigaHTTPError(400, "")

        tb._taiga_request = fake
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.authenticate("http://x", "u", "p")
        self.assertIn("rejected the configured username/password", str(ctx.exception))

    def test_connection_error_raises_clear_unreachable_message(self):
        def fake(*a, **k):
            raise tb.TaigaConnectionError("refused")

        tb._taiga_request = fake
        with self.assertRaises(tb.TaigaConnectionError) as ctx:
            tb.authenticate("http://x", "u", "p")
        self.assertIn("Could not reach Taiga", str(ctx.exception))

    def test_missing_token_in_response_raises(self):
        tb._taiga_request = lambda *a, **k: {}
        with self.assertRaises(tb.TaigaPushError):
            tb.authenticate("http://x", "u", "p")


class LookupProjectTests(_TaigaRequestPatchCase):
    def test_returns_id_on_success(self):
        tb._taiga_request = lambda *a, **k: {"id": 7, "slug": "my-project"}
        self.assertEqual(tb.lookup_project("http://x", "tok", "my-project"), 7)

    def test_404_raises_no_such_project_message(self):
        def fake(*a, **k):
            raise tb.TaigaHTTPError(404, "")

        tb._taiga_request = fake
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.lookup_project("http://x", "tok", "nope")
        self.assertIn("no Taiga project found", str(ctx.exception))


class ResolveSessionTests(_TaigaRequestPatchCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="taiga-board-session-")
        self.config_path = write_config(os.path.join(self.tmp, "taiga-push.env"))

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_happy_path_returns_base_url_token_project_id(self):
        def fake(base_url, method, path, token=None, body=None):
            if path == "/api/v1/auth":
                return {"auth_token": "tok-1"}
            if path.startswith("/api/v1/projects/by_slug"):
                return {"id": 7}
            raise AssertionError(f"unexpected call: {path}")

        tb._taiga_request = fake
        base_url, token, project_id = tb.resolve_session(self.config_path)
        self.assertEqual(base_url, "http://127.0.0.1:9000")
        self.assertEqual(token, "tok-1")
        self.assertEqual(project_id, 7)

    def test_missing_config_raises_before_any_network_call(self):
        called = []
        tb._taiga_request = lambda *a, **k: called.append(1) or {}
        with self.assertRaises(tb.TaigaPushError):
            tb.resolve_session(os.path.join(self.tmp, "does-not-exist.env"))
        self.assertEqual(called, [])


class GetUserstoryTests(_TaigaRequestPatchCase):
    def test_normalizes_status_id_and_name(self):
        tb._taiga_request = lambda *a, **k: {
            "id": 99, "ref": 42, "subject": "Fix the thing", "description": "desc text",
            "version": 3, "status": 2, "status_extra_info": {"name": "In progress"}}
        result = tb.get_userstory("http://x", "tok", 7, 42)
        self.assertEqual(result, {"id": 99, "ref": 42, "subject": "Fix the thing",
                                  "description": "desc text", "version": 3,
                                  "status_id": 2, "status_name": "In progress"})

    def test_404_raises_no_such_userstory_message(self):
        def fake(*a, **k):
            raise tb.TaigaHTTPError(404, "")

        tb._taiga_request = fake
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.get_userstory("http://x", "tok", 7, 999)
        self.assertIn("no such userstory", str(ctx.exception))
        self.assertIn("999", str(ctx.exception))


class ListUserstoriesTests(_TaigaRequestPatchCase):
    def _fake_data(self):
        return [
            {"id": 1, "ref": 1, "subject": "Fix login bug", "modified_date": "2026-01-01T00:00:00Z",
             "status": 1, "status_extra_info": {"name": "New"}},
            {"id": 2, "ref": 2, "subject": "Add dark mode", "modified_date": "2026-01-03T00:00:00Z",
             "status": 2, "status_extra_info": {"name": "In progress"}},
            {"id": 3, "ref": 3, "subject": "Fix logout bug", "modified_date": "2026-01-02T00:00:00Z",
             "status": 1, "status_extra_info": {"name": "New"}},
        ]

    def test_no_query_sorts_by_modified_date_descending(self):
        tb._taiga_request = lambda *a, **k: self._fake_data()
        result = tb.list_userstories("http://x", "tok", 7)
        self.assertEqual([r["ref"] for r in result], [2, 3, 1])

    def test_query_filters_by_case_insensitive_subject_substring(self):
        tb._taiga_request = lambda *a, **k: self._fake_data()
        result = tb.list_userstories("http://x", "tok", 7, query="fix")
        self.assertEqual(sorted(r["ref"] for r in result), [1, 3])

    def test_limit_bounds_result_count(self):
        tb._taiga_request = lambda *a, **k: self._fake_data()
        result = tb.list_userstories("http://x", "tok", 7, limit=1)
        self.assertEqual(len(result), 1)


class ListUserstoryStatusesTests(_TaigaRequestPatchCase):
    def test_returns_id_name_pairs(self):
        tb._taiga_request = lambda *a, **k: [{"id": 1, "name": "New"}, {"id": 2, "name": "In progress"}]
        result = tb.list_userstory_statuses("http://x", "tok", 7)
        self.assertEqual(result, [{"id": 1, "name": "New"}, {"id": 2, "name": "In progress"}])


class WriteVerbsTests(_TaigaRequestPatchCase):
    def test_set_status_sends_version_and_status_id(self):
        captured = {}

        def fake(base_url, method, path, token=None, body=None):
            captured["method"], captured["path"], captured["body"] = method, path, body
            return {"id": 99, "ref": 42, "version": 4, "status": 3,
                   "status_extra_info": {"name": "Done"}}

        tb._taiga_request = fake
        result = tb.set_status("http://x", "tok", 99, 3, 3)
        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(captured["path"], "/api/v1/userstories/99")
        self.assertEqual(captured["body"], {"version": 3, "status": 3})
        self.assertEqual(result["status_name"], "Done")

    def test_amend_description_sends_version_and_description(self):
        captured = {}

        def fake(base_url, method, path, token=None, body=None):
            captured["body"] = body
            return {"id": 99, "version": 4}

        tb._taiga_request = fake
        tb.amend_description("http://x", "tok", 99, 3, "new description")
        self.assertEqual(captured["body"], {"version": 3, "description": "new description"})

    def test_append_comment_sends_version_and_comment(self):
        captured = {}

        def fake(base_url, method, path, token=None, body=None):
            captured["body"] = body
            return {"id": 99, "version": 4}

        tb._taiga_request = fake
        tb.append_comment("http://x", "tok", 99, 3, "a comment")
        self.assertEqual(captured["body"], {"version": 3, "comment": "a comment"})

    def test_version_conflict_409_raises_clear_conflict_message(self):
        def fake(*a, **k):
            raise tb.TaigaHTTPError(409, "")

        tb._taiga_request = fake
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.set_status("http://x", "tok", 99, 1, 3)
        self.assertIn("changed concurrently", str(ctx.exception))

    def test_vanished_ref_404_raises_clear_message(self):
        def fake(*a, **k):
            raise tb.TaigaHTTPError(404, "")

        tb._taiga_request = fake
        with self.assertRaises(tb.TaigaPushError) as ctx:
            tb.amend_description("http://x", "tok", 99, 1, "x")
        self.assertIn("no such userstory", str(ctx.exception))


# ═══════════════════════════ Part B: app/teams.py ═══════════════════════════
class _StateTestCase(unittest.TestCase):
    def setUp(self):
        self.projdir = tempfile.mkdtemp(prefix="switchboard-board-proj-")
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-board-state-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.projdir, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _lead(self, tier=1, kind="ollama", name="m"):
        return {"kind": kind, "name": name, "tier": tier}


def _patch_taiga_board(testcase, **fakes):
    """Monkeypatches the named teamsmod.taiga_board attributes for the
    duration of one test, restoring the originals in tearDown -- same
    "monkeypatch the module the caller imports it through" convention
    DelegateBookkeepingTests already uses for teamsmod.agent_run."""
    originals = {}
    for name, fn in fakes.items():
        originals[name] = getattr(teamsmod.taiga_board, name)
        setattr(teamsmod.taiga_board, name, fn)

    def _restore():
        for name, fn in originals.items():
            setattr(teamsmod.taiga_board, name, fn)

    testcase.addCleanup(_restore)


# ─── _LEAD_TOOL_REQUIRED_ARGS / _validate_lead_action() board categories ──
class ValidateLeadActionBoardTests(unittest.TestCase):
    def test_board_read_no_args_ok(self):
        r = teamsmod._validate_lead_action({"tool": "board_read", "args": {}}, [], 0)
        self.assertTrue(r["ok"])

    def test_board_read_ref_wrong_type(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_read", "args": {"ref": "42"}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_board_read_query_wrong_type(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_read", "args": {"query": 123}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_board_read_valid_ref_ok(self):
        r = teamsmod._validate_lead_action({"tool": "board_read", "args": {"ref": 42}}, [], 0)
        self.assertTrue(r["ok"])

    def test_board_write_missing_required_arg(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_write", "args": {"verb": "set_status", "ref": 1}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "missing_args"))

    def test_board_write_unknown_verb(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_write", "args": {"verb": "move_card", "ref": 1, "value": "x"}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "unknown_verb"))
        self.assertIn("set_status", r["detail"])
        self.assertIn("amend_description", r["detail"])
        self.assertIn("append_comment", r["detail"])

    def test_board_write_non_positive_ref(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_write", "args": {"verb": "set_status", "ref": 0, "value": "x"}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_board_write_value_too_long(self):
        orig = teamsmod.TEAM_BOARD_WRITE_VALUE_MAX_CHARS
        teamsmod.TEAM_BOARD_WRITE_VALUE_MAX_CHARS = 5
        try:
            r = teamsmod._validate_lead_action(
                {"tool": "board_write",
                 "args": {"verb": "amend_description", "ref": 1, "value": "way too long"}}, [], 0)
        finally:
            teamsmod.TEAM_BOARD_WRITE_VALUE_MAX_CHARS = orig
        self.assertEqual((r["ok"], r["reason"]), (False, "value_too_long"))

    def test_board_write_note_wrong_type(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_write",
             "args": {"verb": "set_status", "ref": 1, "value": "x", "note": 123}}, [], 0)
        self.assertEqual((r["ok"], r["reason"]), (False, "wrong_type"))

    def test_board_write_valid_shape_ok(self):
        r = teamsmod._validate_lead_action(
            {"tool": "board_write",
             "args": {"verb": "set_status", "ref": 42, "value": "In progress",
                     "note": "why"}}, [], 0)
        self.assertTrue(r["ok"])

    def test_unknown_tool_message_names_all_six_tools(self):
        r = teamsmod._validate_lead_action({"tool": "delete_repo", "args": {}}, [], 0)
        for name in ("delegate", "fact_check", "ask_user", "board_read", "board_write", "finish"):
            self.assertIn(name, r["detail"])


# ─── team_step()'s board_read branch ────────────────────────────────────
class TeamStepBoardReadTests(_StateTestCase):
    def test_board_read_by_ref_returns_result_same_round_no_block(self):
        state = teamsmod._new_state("run-bread-ref", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_read", "args": {"ref": 42}}, None, "..."

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "subject": "Fix it",
                                           "description": "desc", "version": 3,
                                           "status_id": 1, "status_name": "New"})
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["action_count"], 1)
        self.assertIn("Fix it", state["history"][-1]["full_result_text"])
        self.assertIn("New", state["history"][-1]["full_result_text"])

    def test_board_read_no_args_lists_recent(self):
        state = teamsmod._new_state("run-bread-list", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_read", "args": {}}, None, "..."

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            list_userstories=lambda *a, **k: [{"ref": 1, "subject": "A"}])
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["action_count"], 1)
        self.assertIn("\"ref\": 1", state["history"][-1]["full_result_text"])

    def test_board_read_taiga_error_is_ordinary_outcome_not_crash(self):
        state = teamsmod._new_state("run-bread-err", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_read", "args": {"ref": 999}}, None, "..."

        def raise_error(*a, **k):
            raise teamsmod.taiga_board.TaigaPushError("no such userstory on this board: ref 999.")

        _patch_taiga_board(self, resolve_session=lambda **k: ("http://x", "tok", 7),
                           get_userstory=raise_error)
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["action_count"], 1)
        self.assertIn("Taiga error", state["history"][-1]["outcome_summary"])

    def test_board_read_not_configured_is_ordinary_outcome(self):
        state = teamsmod._new_state("run-bread-noconfig", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_read", "args": {"ref": 1}}, None, "..."

        def raise_not_configured(**k):
            raise teamsmod.taiga_board.TaigaPushError("Taiga isn't configured — run x first.")

        _patch_taiga_board(self, resolve_session=raise_not_configured)
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertIn("isn't configured", state["history"][-1]["outcome_summary"])


# ─── team_step()'s board_write branch ───────────────────────────────────
class TeamStepBoardWriteTests(_StateTestCase):
    def test_board_write_blocks_and_writes_inbox_no_taiga_write_call(self):
        state = teamsmod._new_state("run-bwrite", self.projdir, self._lead(), [], "task")
        patch_calls = []

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_write",
                   "args": {"verb": "set_status", "ref": 42, "value": "In progress"}}, None, "..."

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "subject": "Fix it",
                                           "description": "desc", "version": 3,
                                           "status_id": 1, "status_name": "New"},
            set_status=lambda *a, **k: patch_calls.append(("set_status", a, k)))
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "blocked_board_write")
        self.assertEqual(patch_calls, [])  # no Taiga write call made at proposal time
        with open(teamsmod._inbox_path("run-bwrite")) as f:
            inbox = json.load(f)
        self.assertEqual(inbox["kind"], "board_write")
        self.assertEqual(inbox["verb"], "set_status")
        self.assertEqual(inbox["ref"], 42)
        self.assertEqual(inbox["value"], "In progress")
        self.assertEqual(inbox["current_value"], {"status_id": 1, "status_name": "New"})

    def test_board_write_snapshot_failure_never_queues_proposal(self):
        state = teamsmod._new_state("run-bwrite-fail", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_write",
                   "args": {"verb": "set_status", "ref": 999, "value": "In progress"}}, None, "..."

        def raise_error(*a, **k):
            raise teamsmod.taiga_board.TaigaPushError("no such userstory on this board: ref 999.")

        _patch_taiga_board(self, resolve_session=lambda **k: ("http://x", "tok", 7),
                           get_userstory=raise_error)
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["status"], "running")
        self.assertFalse(os.path.exists(teamsmod._inbox_path("run-bwrite-fail")))
        self.assertIn("Taiga error", state["history"][-1]["outcome_summary"])

    def test_board_write_while_already_blocked_is_business_rule_not_malformed(self):
        state = teamsmod._new_state("run-bwrite-pending", self.projdir, self._lead(), [], "task")
        state["status"] = "blocked_board_write"

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_write",
                   "args": {"verb": "set_status", "ref": 1, "value": "x"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertEqual(state["malformed_retries"], 0)
        self.assertIn("rejected", state["history"][-1]["outcome_summary"])
        self.assertIn("already pending", state["history"][-1]["outcome_summary"])

    def test_board_write_while_blocked_ask_user_also_rejected(self):
        state = teamsmod._new_state("run-bwrite-pending2", self.projdir, self._lead(), [], "task")
        state["status"] = "blocked_ask_user"

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_write",
                   "args": {"verb": "set_status", "ref": 1, "value": "x"}}, None, "..."

        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        self.assertIn("already pending", state["history"][-1]["outcome_summary"])

    def test_amend_description_current_value_snapshot(self):
        state = teamsmod._new_state("run-bwrite-amend", self.projdir, self._lead(), [], "task")

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "board_write",
                   "args": {"verb": "amend_description", "ref": 42, "value": "new text"}}, None, "..."

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "subject": "Fix it",
                                           "description": "old text", "version": 3,
                                           "status_id": 1, "status_name": "New"})
        orig = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        try:
            teamsmod.team_step(state)
        finally:
            teamsmod._call_lead = orig
        with open(teamsmod._inbox_path("run-bwrite-amend")) as f:
            inbox = json.load(f)
        self.assertEqual(inbox["current_value"], {"description": "old text"})


# ─── resolve_board_write() ──────────────────────────────────────────────
class ResolveBoardWriteTests(_StateTestCase):
    def _blocked_run(self, run_id="run-resolve", verb="set_status", ref=42, value="In progress"):
        state = teamsmod._new_state(run_id, self.projdir, self._lead(), [], "task")
        current_value = {"status_id": 1, "status_name": "New"}
        teamsmod._write_board_inbox(state, verb, ref, value, "a note", current_value)
        state["status"] = "blocked_board_write"
        teamsmod._persist(state)
        return run_id

    def test_no_such_run_id(self):
        result = teamsmod.resolve_board_write("does-not-exist", "approve")
        self.assertFalse(result["ok"])
        self.assertIn("no such run_id", result["error"])

    def test_not_blocked_on_board_write(self):
        state = teamsmod._new_state("run-not-blocked", self.projdir, self._lead(), [], "task")
        teamsmod._persist(state)
        result = teamsmod.resolve_board_write("run-not-blocked", "approve")
        self.assertFalse(result["ok"])
        self.assertIn("is not blocked on board_write", result["error"])

    def test_invalid_action(self):
        run_id = self._blocked_run()
        result = teamsmod.resolve_board_write(run_id, "maybe")
        self.assertFalse(result["ok"])
        self.assertIn("invalid action", result["error"])
        self.assertEqual(teamsmod._load_state(run_id)["status"], "blocked_board_write")

    def test_reject_makes_zero_taiga_calls_and_resumes(self):
        run_id = self._blocked_run()
        calls = []
        _patch_taiga_board(self, resolve_session=lambda **k: calls.append("session") or ("u", "t", 1))
        result = teamsmod.resolve_board_write(run_id, "reject")
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [])
        self.assertEqual(result["state"]["status"], "running")
        self.assertFalse(os.path.isfile(teamsmod._inbox_path(run_id)))
        self.assertTrue(os.path.isfile(teamsmod._inbox_resolved_path(run_id)))
        entry = result["state"]["history"][-1]
        self.assertEqual(entry["tool"], "board_write_resolved")
        self.assertEqual(entry["outcome_summary"], "rejected by human")

    def test_approve_set_status_makes_exactly_one_patch_with_fresh_version(self):
        run_id = self._blocked_run(verb="set_status", value="In progress")
        patch_calls = []

        def fake_set_status(base_url, token, us_id, version, status_id, **k):
            patch_calls.append((us_id, version, status_id))
            return {"id": us_id, "ref": 42, "version": version + 1, "status": status_id,
                   "status_name": "In progress"}

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            # version here (99) is deliberately DIFFERENT from the
            # proposal-time snapshot's current_value (which never recorded
            # a version at all) -- proves resolve_board_write() fetches a
            # FRESH version, not a stale one.
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 99,
                                           "status_id": 1, "status_name": "New"},
            list_userstory_statuses=lambda *a, **k: [{"id": 1, "name": "New"},
                                                     {"id": 2, "name": "In progress"}],
            set_status=fake_set_status)

        result = teamsmod.resolve_board_write(run_id, "approve")
        self.assertTrue(result["ok"])
        self.assertEqual(len(patch_calls), 1)
        self.assertEqual(patch_calls[0], (1, 99, 2))
        entry = result["state"]["history"][-1]
        self.assertEqual(entry["outcome_summary"], "approved and applied")
        self.assertEqual(result["state"]["status"], "running")

    def test_approve_amend_description_calls_amend_description_once(self):
        run_id = self._blocked_run(verb="amend_description", value="new description")
        calls = []

        def fake_amend(base_url, token, us_id, version, description, **k):
            calls.append((us_id, version, description))
            return {"id": us_id, "version": version + 1, "description": description}

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 5},
            amend_description=fake_amend)

        result = teamsmod.resolve_board_write(run_id, "approve")
        self.assertEqual(calls, [(1, 5, "new description")])
        self.assertEqual(result["state"]["history"][-1]["outcome_summary"], "approved and applied")

    def test_approve_append_comment_calls_append_comment_once(self):
        run_id = self._blocked_run(verb="append_comment", value="a comment")
        calls = []

        def fake_comment(base_url, token, us_id, version, comment, **k):
            calls.append((us_id, version, comment))
            return {"id": us_id, "version": version + 1}

        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 5},
            append_comment=fake_comment)

        result = teamsmod.resolve_board_write(run_id, "approve")
        self.assertEqual(calls, [(1, 5, "a comment")])

    def test_approve_unknown_status_name_is_recorded_as_taiga_rejected_not_stuck(self):
        run_id = self._blocked_run(verb="set_status", value="Nonexistent Status")
        _patch_taiga_board(
            self, resolve_session=lambda **k: ("http://x", "tok", 7),
            get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 5},
            list_userstory_statuses=lambda *a, **k: [{"id": 1, "name": "New"}])
        result = teamsmod.resolve_board_write(run_id, "approve")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["status"], "running")
        entry = result["state"]["history"][-1]
        self.assertIn("approved but Taiga rejected the write", entry["outcome_summary"])
        self.assertIn("Nonexistent Status", entry["outcome_summary"])

    def test_approve_taiga_failure_still_resolves_and_resumes(self):
        run_id = self._blocked_run(verb="amend_description", value="x")

        def raise_conflict(*a, **k):
            raise teamsmod.taiga_board.TaigaPushError(
                "Taiga rejected the write -- this card was likely changed concurrently "
                "(version conflict, HTTP 409).")

        _patch_taiga_board(self, resolve_session=lambda **k: ("http://x", "tok", 7),
                           get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 5},
                           amend_description=raise_conflict)

        result = teamsmod.resolve_board_write(run_id, "approve")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["status"], "running")
        self.assertFalse(os.path.isfile(teamsmod._inbox_path(run_id)))
        self.assertTrue(os.path.isfile(teamsmod._inbox_resolved_path(run_id)))
        entry = result["state"]["history"][-1]
        self.assertIn("approved but Taiga rejected the write", entry["outcome_summary"])
        self.assertIn("version conflict", entry["outcome_summary"])

    def test_two_concurrent_resolves_exactly_one_succeeds_no_taiga_call_for_loser(self):
        run_id = self._blocked_run(verb="amend_description", value="x")
        call_log = []
        lock = threading.Lock()

        def fake_amend(base_url, token, us_id, version, description, **k):
            with lock:
                call_log.append(1)
            return {"id": us_id, "version": version + 1}

        _patch_taiga_board(self, resolve_session=lambda **k: ("http://x", "tok", 7),
                           get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 5},
                           amend_description=fake_amend)

        results = []

        def _do_resolve():
            results.append(teamsmod.resolve_board_write(run_id, "approve"))

        t1 = threading.Thread(target=_do_resolve)
        t2 = threading.Thread(target=_do_resolve)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        oks = [r["ok"] for r in results]
        self.assertEqual(sorted(oks), [False, True])
        self.assertEqual(len(call_log), 1)  # loser never reached the Taiga call at all
        loser = next(r for r in results if not r["ok"])
        self.assertIn("is not blocked on board_write", loser["error"])
        winner_state = teamsmod._load_state(run_id)
        self.assertEqual(winner_state["status"], "running")
        resolved_entries = [h for h in winner_state["history"] if h["tool"] == "board_write_resolved"]
        self.assertEqual(len(resolved_entries), 1)


# ─── CLI: team-board-resolve ─────────────────────────────────────────────
class CliTeamBoardResolveTests(_StateTestCase):
    def _blocked_run(self, run_id="run-cli"):
        state = teamsmod._new_state(run_id, self.projdir, self._lead(), [], "task")
        # action_count bumped to 1 up front, same reasoning tests/fixtures/
        # headless/tier3_stub_resolve_flow.sh's own comment gives (board_write,
        # like ask_user, never increments action_count itself -- a finish
        # immediately after resolving would otherwise be rejected forever as
        # premature_finish, same as that fixture's own "Round 1" fact_check
        # step exists purely to avoid).
        state["action_count"] = 1
        teamsmod._write_board_inbox(state, "amend_description", 42, "new text", None,
                                    {"description": "old text"})
        state["status"] = "blocked_board_write"
        teamsmod._persist(state)
        return run_id

    def test_parses_run_id_and_action_flags(self):
        args = teamsmod._parse_args(["team-board-resolve", "--run-id", "abc", "--action", "approve"])
        self.assertEqual(args.command, "team-board-resolve")
        self.assertEqual(args.run_id, "abc")
        self.assertEqual(args.action, "approve")

    def test_invalid_action_choice_rejected_by_argparse(self):
        with self.assertRaises(SystemExit):
            teamsmod._parse_args(["team-board-resolve", "--run-id", "abc", "--action", "bogus"])

    def test_approve_end_to_end_drives_to_finished(self):
        run_id = self._blocked_run()

        def fake_amend(base_url, token, us_id, version, description, **k):
            return {"id": us_id, "version": version + 1}

        _patch_taiga_board(self, resolve_session=lambda **k: ("http://x", "tok", 7),
                           get_userstory=lambda *a, **k: {"id": 1, "ref": 42, "version": 5},
                           amend_description=fake_amend)

        # Drive the resumed loop to a clean stop without a real lead call.
        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig_call_lead = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        self.addCleanup(setattr, teamsmod, "_call_lead", orig_call_lead)

        rc = teamsmod.main(["team-board-resolve", "--run-id", run_id, "--action", "approve"])
        self.assertEqual(rc, 0)
        self.assertEqual(teamsmod._load_state(run_id)["status"], "finished")

    def test_reject_end_to_end_drives_to_finished(self):
        run_id = self._blocked_run()

        def fake_call_lead(state, system, round_context, **kwargs):
            return {"tool": "finish", "args": {"summary": "done"}}, None, "..."

        orig_call_lead = teamsmod._call_lead
        teamsmod._call_lead = fake_call_lead
        self.addCleanup(setattr, teamsmod, "_call_lead", orig_call_lead)

        rc = teamsmod.main(["team-board-resolve", "--run-id", run_id, "--action", "reject"])
        self.assertEqual(rc, 0)
        self.assertEqual(teamsmod._load_state(run_id)["status"], "finished")

    def test_unresolvable_run_id_exits_nonzero(self):
        rc = teamsmod.main(["team-board-resolve", "--run-id", "no-such-run", "--action", "approve"])
        self.assertEqual(rc, 1)


# ─── sweep_dead_teams()/_team_exit_code() status audit (docs/spec.md §4) ──
class BlockedBoardWriteStatusAuditTests(_StateTestCase):
    def test_team_exit_code_treats_blocked_board_write_as_success(self):
        self.assertEqual(teamsmod._team_exit_code("blocked_board_write"), 0)

    def test_sweep_marks_crashed_session_error_when_blocked_on_board_write(self):
        state = teamsmod._new_state("run-sweep-bw", self.projdir, self._lead(), [], "task",
                                    project_name="sweepproj")
        state["status"] = "blocked_board_write"
        teamsmod._persist(state)
        results = teamsmod.sweep_dead_teams()
        entry = next(r for r in results if r["run_id"] == "run-sweep-bw")
        self.assertEqual(entry["action"], "marked_error")
        self.assertEqual(teamsmod._load_state("run-sweep-bw")["status"], "error")


if __name__ == "__main__":
    unittest.main()
