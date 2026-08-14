#!/usr/bin/env python3
"""
Tests for the AI merge-request reviewer (backlog item 8, docs/spec.md):
watching a configurable Gitea PR label via the existing poll pass, and
generating + posting a review comment. Two halves, mirroring the spec's own
two affected modules:

Part A (`app/app.py`) -- the Gitea-specific polling/API/state-file/dispatch
logic (_gitea_api_raw, the state file, _ai_reviewer_poll_repo/_bg/_run).
Follows tests/test_gitea_poll.py's own convention exactly: _gitea_api/
_gitea_api_raw are monkeypatched, no real Docker/network/Gitea calls
anywhere in this file. teams.roster()/teams.review_pr_diff() are
monkeypatched too -- Part B below already covers review_pr_diff()'s own
internal correctness, so Part A treats it as a black box, the same
layering tests/test_teams_board.py's own Part A/Part B split already uses.

Part B (`app/teams.py`) -- review_pr_diff()/_build_review_prompt(), in the
style of tests/test_teams_headless.py/tests/test_teams_grounding.py:
_tier1_call_with_retry()/agent_run()/_run_run_user_command()/
load_grounding() are all monkeypatched (same "orig = teamsmod.X; ...;
finally: teamsmod.X = orig" idiom tests/test_teams_lead.py's own
Tier1CallWithRetryTests already establishes).

Run with:
    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))
os.environ.setdefault("GITEA_ENABLED", "1")
os.environ.setdefault("GITEA_LABEL", "Gitea")
os.environ.setdefault("GITEA_PORT", "3125")

import app as appmod  # noqa: E402
import teams as teamsmod  # noqa: E402


# ═══════════════════════════ Part A: app/app.py ═════════════════════════════
class GiteaApiRawTests(unittest.TestCase):
    """_gitea_api_raw() -- returns (status, text) without json.loads-ing the
    body, unlike _gitea_api()."""

    def setUp(self):
        self._orig_urlopen = appmod.urllib.request.urlopen

    def tearDown(self):
        appmod.urllib.request.urlopen = self._orig_urlopen

    def test_2xx_returns_status_and_raw_text(self):
        class _Resp:
            status = 200

            def read(self):
                return b"diff --git a/x b/x\n"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        appmod.urllib.request.urlopen = lambda req, timeout=None: _Resp()
        status, text = appmod._gitea_api_raw("GET", "/repos/a/b/pulls/1.diff")
        self.assertEqual(status, 200)
        self.assertEqual(text, "diff --git a/x b/x\n")

    def test_http_error_returns_status_and_body_text_not_json_parsed(self):
        def raise_http_error(req, timeout=None):
            raise appmod.urllib.error.HTTPError(
                "url", 404, "Not Found", {}, io_stub())
        appmod.urllib.request.urlopen = raise_http_error
        status, text = appmod._gitea_api_raw("GET", "/repos/a/b/pulls/1.diff")
        self.assertEqual(status, 404)
        self.assertEqual(text, "not json{{{")

    def test_url_error_raises_connection_error(self):
        def raise_url_error(req, timeout=None):
            raise appmod.urllib.error.URLError("boom")
        appmod.urllib.request.urlopen = raise_url_error
        with self.assertRaises(ConnectionError):
            appmod._gitea_api_raw("GET", "/repos/a/b/pulls/1.diff")


def io_stub():
    import io
    return io.BytesIO(b"not json{{{")


class AiReviewerStateFileTests(unittest.TestCase):
    """_load_ai_reviewer_state()/_save_ai_reviewer_state_entry() -- same
    tmp-file-then-os.replace() idiom as _save_gitea_repo_map_entry."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-state-")
        self._orig = appmod.AI_REVIEWER_STATE_FILE
        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "nested", "state.json")

    def tearDown(self):
        appmod.AI_REVIEWER_STATE_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_loads_as_empty_dict(self):
        self.assertEqual(appmod._load_ai_reviewer_state(), {})

    def test_corrupt_file_loads_as_empty_dict_not_raise(self):
        os.makedirs(os.path.dirname(appmod.AI_REVIEWER_STATE_FILE))
        with open(appmod.AI_REVIEWER_STATE_FILE, "w") as f:
            f.write("not json{{{")
        self.assertEqual(appmod._load_ai_reviewer_state(), {})

    def test_save_then_load_round_trips(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=0,
            reviewed_at="2026-01-01T00:00:00Z", last_error=None)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"], {
            "label_present": True, "attempts": 0,
            "reviewed_at": "2026-01-01T00:00:00Z", "last_error": None})

    def test_save_overwrites_only_the_named_entry(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/one#1", label_present=True, attempts=0, reviewed_at=None, last_error=None)
        appmod._save_ai_reviewer_state_entry(
            "admin/two#2", label_present=False, attempts=1, reviewed_at=None, last_error="x")
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(set(s.keys()), {"admin/one#1", "admin/two#2"})


class AiReviewerPollRepoTests(unittest.TestCase):
    """_ai_reviewer_poll_repo() -- label-edge detection + dispatch gating.
    _ai_reviewer_review_bg is mocked out entirely (a separate class below
    tests what actually happens once dispatched), same layering
    tests/test_gitea_poll.py's GiteaPollOneTests already uses for
    _gitea_sync_bg."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-poll-")
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_enabled = appmod.AI_REVIEWER_ENABLED
        self._orig_label = appmod.AI_REVIEWER_LABEL
        self._orig_max_attempts = appmod.AI_REVIEWER_MAX_ATTEMPTS
        self._orig_api = appmod._gitea_api
        self._orig_bg = appmod._ai_reviewer_review_bg
        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "state.json")
        appmod.AI_REVIEWER_ENABLED = True
        appmod.AI_REVIEWER_LABEL = "ready for review"
        appmod.AI_REVIEWER_MAX_ATTEMPTS = 3
        self.bg_calls = []
        appmod._ai_reviewer_review_bg = lambda *a: self.bg_calls.append(a)

    def tearDown(self):
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod.AI_REVIEWER_ENABLED = self._orig_enabled
        appmod.AI_REVIEWER_LABEL = self._orig_label
        appmod.AI_REVIEWER_MAX_ATTEMPTS = self._orig_max_attempts
        appmod._gitea_api = self._orig_api
        appmod._ai_reviewer_review_bg = self._orig_bg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_pulls(self, prs):
        appmod._gitea_api = lambda method, path, body=None: (200, prs)

    def _pr(self, number, labels=()):
        return {"number": number, "title": "t", "body": "b",
                "labels": [{"name": n} for n in labels]}

    def test_disabled_makes_no_calls(self):
        appmod.AI_REVIEWER_ENABLED = False
        calls = []
        appmod._gitea_api = lambda method, path, body=None: calls.append(path) or (200, [])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(calls, [])

    def test_unseen_pr_without_label_records_absent_no_dispatch(self):
        self._fake_pulls([self._pr(1)])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["label_present"], False)

    def test_label_add_edge_triggers_dispatch_and_synchronous_state_write(self):
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)
        owner_repo, entry, pr = self.bg_calls[0]
        self.assertEqual(owner_repo, "admin/proj")
        self.assertEqual(pr["number"], 1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"], {
            "label_present": True, "attempts": 0, "reviewed_at": None, "last_error": None})

    def test_label_still_present_after_a_successful_review_does_not_redispatch(self):
        # Simulates the state a completed, successful review leaves behind
        # (docs/implementation.md "Deviations from spec" -- attempts=0,
        # last_error=None after success).
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=0,
            reviewed_at="2026-01-01T00:00:00Z", last_error=None)
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

    def test_label_still_present_after_a_failed_attempt_redispatches(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=1,
            reviewed_at=None, last_error="diff fetch failed (status 500)")
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)

    def test_attempts_exhausted_gives_up_silently(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=3,
            reviewed_at=None, last_error="still failing")
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

    def test_label_removed_then_readded_is_exactly_one_new_episode(self):
        # Poll 1: label present -> fresh trigger, then simulate a completed
        # success (attempts reset to 0, last_error None).
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=0,
            reviewed_at="2026-01-01T00:00:00Z", last_error=None)
        self.bg_calls.clear()

        # Poll 2: label removed.
        self._fake_pulls([self._pr(1, labels=[])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["label_present"], False)
        self.assertEqual(s["admin/proj#1"]["attempts"], 0)

        # Poll 3: label re-added -> exactly one new trigger.
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)

    def test_label_matched_by_exact_equality_not_substring(self):
        self._fake_pulls([self._pr(1, labels=["not ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

    def test_non_200_status_skipped_without_raising(self):
        appmod._gitea_api = lambda method, path, body=None: (500, {})
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_non_list_response_skipped_without_raising(self):
        appmod._gitea_api = lambda method, path, body=None: (200, {"not": "a list"})
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_malformed_pr_entry_in_list_is_skipped_not_fatal(self):
        self._fake_pulls(["not-a-dict", self._pr(2, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("admin/proj", {"name": "proj"})  # must not raise
        self.assertEqual(len(self.bg_calls), 1)
        self.assertEqual(self.bg_calls[0][2]["number"], 2)


class AiReviewerReviewRunTests(unittest.TestCase):
    """_ai_reviewer_review_run() -- the actual diff-fetch -> model-call ->
    comment-post pipeline. teams.roster()/teams.review_pr_diff() are
    monkeypatched; Part B below covers review_pr_diff()'s own internals."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-run-")
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_max_diff = appmod.AI_REVIEWER_MAX_DIFF_BYTES
        self._orig_model = appmod.AI_REVIEWER_MODEL
        self._orig_api = appmod._gitea_api
        self._orig_api_raw = appmod._gitea_api_raw
        self._orig_roster = appmod.teams.roster
        self._orig_review = appmod.teams.review_pr_diff
        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "state.json")
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = 40000
        appmod.AI_REVIEWER_MODEL = "ollama:qwen3:8b"
        appmod.teams.roster = lambda: [
            {"name": "qwen3:8b", "kind": "ollama", "label": "qwen3:8b", "tier": 1,
             "delegate_capable": False}]
        self.review_calls = []

        def fake_review(model, workdir, pr_title, pr_body, diff_text, diff_truncated):
            self.review_calls.append((model, workdir, pr_title, pr_body, diff_text, diff_truncated))
            return {"ok": True, "text": "Looks fine."}
        appmod.teams.review_pr_diff = fake_review

        # Seed the trigger-edge state a real poll pass would already have
        # written synchronously before dispatching this run.
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=0, reviewed_at=None, last_error=None)

    def tearDown(self):
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = self._orig_max_diff
        appmod.AI_REVIEWER_MODEL = self._orig_model
        appmod._gitea_api = self._orig_api
        appmod._gitea_api_raw = self._orig_api_raw
        appmod.teams.roster = self._orig_roster
        appmod.teams.review_pr_diff = self._orig_review
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pr(self, number=1, title="add feature", body="does a thing"):
        return {"number": number, "title": title, "body": body}

    def test_diff_fetch_non_200_records_failure_and_posts_no_comment(self):
        appmod._gitea_api_raw = lambda method, path: (404, "")
        comment_calls = []
        appmod._gitea_api = lambda method, path, body=None: comment_calls.append(path) or (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        self.assertEqual(comment_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)
        self.assertIn("404", s["admin/proj#1"]["last_error"])
        self.assertTrue(s["admin/proj#1"]["label_present"])

    def test_diff_fetch_connection_error_records_failure(self):
        def raise_conn(method, path):
            raise ConnectionError("gitea unreachable")
        appmod._gitea_api_raw = raise_conn
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())  # must not raise
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_empty_but_200_diff_is_still_reviewed_not_an_error(self):
        appmod._gitea_api_raw = lambda method, path: (200, "")
        posted = {}
        appmod._gitea_api = lambda method, path, body=None: posted.update(
            path=path, body=body) or (201, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        self.assertEqual(len(self.review_calls), 1)
        self.assertEqual(self.review_calls[0][4], "")  # diff_text
        self.assertEqual(posted["path"], "/repos/admin/proj/issues/1/comments")
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 0)
        self.assertIsNone(s["admin/proj#1"]["last_error"])

    def test_diff_exceeding_cap_is_truncated_and_comment_notes_it(self):
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = 10
        appmod._gitea_api_raw = lambda method, path: (200, "0123456789ABCDEF")
        posted = {}
        appmod._gitea_api = lambda method, path, body=None: posted.update(
            path=path, body=body) or (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        sent_diff = self.review_calls[0][4]
        self.assertEqual(len(sent_diff.encode("utf-8")), 10)
        self.assertTrue(self.review_calls[0][5])  # diff_truncated
        self.assertIn("truncated", posted["body"]["body"])
        self.assertIn("10", posted["body"]["body"])

    def test_diff_within_cap_not_truncated_no_note_in_comment(self):
        appmod._gitea_api_raw = lambda method, path: (200, "short diff")
        posted = {}
        appmod._gitea_api = lambda method, path, body=None: posted.update(
            path=path, body=body) or (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        self.assertFalse(self.review_calls[0][5])
        self.assertNotIn("truncated", posted["body"]["body"])

    def test_model_not_in_roster_records_failure_no_diff_fetch(self):
        appmod.AI_REVIEWER_MODEL = "ollama:does-not-exist"
        diff_calls = []
        appmod._gitea_api_raw = lambda method, path: diff_calls.append(path) or (200, "diff")
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        s = appmod._load_ai_reviewer_state()
        self.assertIn("not found in roster", s["admin/proj#1"]["last_error"])
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_model_unset_records_failure(self):
        appmod.AI_REVIEWER_MODEL = ""
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_ollama_tag_containing_colon_is_split_on_first_colon_only(self):
        appmod.AI_REVIEWER_MODEL = "ollama:qwen3:8b"
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        model_used = self.review_calls[0][0]
        self.assertEqual(model_used["kind"], "ollama")
        self.assertEqual(model_used["name"], "qwen3:8b")

    def test_two_roster_entries_same_name_different_kind_disambiguated(self):
        appmod.teams.roster = lambda: [
            {"name": "shared-name", "kind": "engine", "label": "e", "tier": 3,
             "delegate_capable": True},
            {"name": "shared-name", "kind": "ollama", "label": "o", "tier": 1,
             "delegate_capable": False},
        ]
        appmod.AI_REVIEWER_MODEL = "ollama:shared-name"
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        self.assertEqual(self.review_calls[0][0]["kind"], "ollama")

    def test_review_generation_failure_records_error_no_comment_post(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod.teams.review_pr_diff = lambda **kw: {"ok": False, "error": "model unreachable"}
        comment_calls = []
        appmod._gitea_api = lambda method, path, body=None: comment_calls.append(path) or (200, {})

        def fake_review(model, workdir, pr_title, pr_body, diff_text, diff_truncated):
            return {"ok": False, "error": "model unreachable"}
        appmod.teams.review_pr_diff = fake_review
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        self.assertEqual(comment_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["last_error"], "model unreachable")
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_comment_post_non_2xx_records_failure(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (403, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)
        self.assertIn("403", s["admin/proj#1"]["last_error"])

    def test_comment_post_connection_error_records_failure(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")

        def raise_conn(method, path, body=None):
            raise ConnectionError("gitea unreachable")
        appmod._gitea_api = raise_conn
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())  # must not raise
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_success_resets_attempts_and_records_reviewed_at(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=2, reviewed_at=None,
            last_error="prior failure")
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 0)
        self.assertIsNone(s["admin/proj#1"]["last_error"])
        self.assertIsNotNone(s["admin/proj#1"]["reviewed_at"])
        self.assertTrue(s["admin/proj#1"]["label_present"])

    def test_comment_body_carries_model_name_and_review_text_never_approve_or_merge_wording(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        posted = {}
        appmod._gitea_api = lambda method, path, body=None: posted.update(
            path=path, body=body) or (200, {})
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        body = posted["body"]["body"]
        self.assertIn("ollama:qwen3:8b", body)
        self.assertIn("Looks fine.", body)
        self.assertIn("never blocks, approves, or merges", body)

    def test_no_call_ever_targets_a_merge_or_approve_endpoint(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        calls = []

        def spy_api(method, path, body=None):
            calls.append((method, path))
            return (200, {})
        appmod._gitea_api = spy_api
        appmod._ai_reviewer_review_run("admin/proj", {"name": "proj"}, self._pr())
        for method, path in calls:
            self.assertNotIn("merge", path)
            self.assertNotIn("approve", path)
            self.assertNotIn("/branches/", path)


class AiReviewerReviewBgConcurrencyTests(unittest.TestCase):
    """_ai_reviewer_review_bg()'s per-PR non-blocking lock -- a second
    dispatch for the same PR while one is still running is dropped, not
    queued (mirrors tests/test_gitea_poll.py's GiteaSyncBgConcurrencyTests)."""

    def test_second_dispatch_while_first_still_holds_the_lock_is_dropped(self):
        pr_key = "admin/lockedproj#7"
        lock = appmod._ai_reviewer_pr_lock_for(pr_key)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            orig = appmod._ai_reviewer_review_run
            calls = []
            appmod._ai_reviewer_review_run = lambda *a: calls.append(a)
            try:
                appmod._ai_reviewer_review_bg("admin/lockedproj", {"name": "p"}, {"number": 7})
                time.sleep(0.05)
                self.assertEqual(calls, [])
            finally:
                appmod._ai_reviewer_review_run = orig
        finally:
            lock.release()

    def test_lock_is_released_after_completion_so_next_dispatch_can_run(self):
        pr_key = "admin/freeproj#9"
        orig = appmod._ai_reviewer_review_run
        calls = []
        appmod._ai_reviewer_review_run = lambda *a: calls.append(a)
        try:
            appmod._ai_reviewer_review_bg("admin/freeproj", {"name": "p"}, {"number": 9})
            for _ in range(50):
                if appmod._ai_reviewer_pr_lock_for(pr_key).acquire(blocking=False):
                    appmod._ai_reviewer_pr_lock_for(pr_key).release()
                    break
                time.sleep(0.02)
            self.assertEqual(len(calls), 1)
        finally:
            appmod._ai_reviewer_review_run = orig


# ═══════════════════════════ Part B: app/teams.py ═══════════════════════════
class BuildReviewPromptTests(unittest.TestCase):
    """_build_review_prompt() -- pure, no I/O."""

    def test_contains_grounding_digest_verbatim(self):
        prompt = teamsmod._build_review_prompt(
            "My PR", "does a thing", "diff --git a/x b/x", False, "DIGEST-MARKER-TEXT")
        self.assertIn("DIGEST-MARKER-TEXT", prompt)

    def test_contains_pr_title_and_diff_text(self):
        prompt = teamsmod._build_review_prompt(
            "Fix the thing", "body text", "diff --git a/y b/y\n+added line", False, "digest")
        self.assertIn("Fix the thing", prompt)
        self.assertIn("+added line", prompt)

    def test_truncation_note_present_only_when_truncated(self):
        truncated = teamsmod._build_review_prompt("t", "b", "d", True, "digest")
        not_truncated = teamsmod._build_review_prompt("t", "b", "d", False, "digest")
        self.assertIn("truncated", truncated)
        self.assertNotIn("truncated", not_truncated)

    def test_never_grants_blocking_or_merge_authority(self):
        prompt = teamsmod._build_review_prompt("t", "b", "d", False, "digest")
        self.assertIn("no authority", prompt)


class ReviewPrDiffOllamaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-review-ollama-")
        self._orig_call = teamsmod._tier1_call_with_retry
        self._orig_grounding = teamsmod.load_grounding
        teamsmod.load_grounding = lambda workdir, **kw: {"digest": "GROUNDING-DIGEST"}

    def tearDown(self):
        teamsmod._tier1_call_with_retry = self._orig_call
        teamsmod.load_grounding = self._orig_grounding
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_calls_tier1_with_empty_tools_list_and_returns_text(self):
        captured = {}

        def fake_call(base_url, model, system, user, tools, *, timeout, retry_budget):
            captured.update(base_url=base_url, model=model, system=system, user=user,
                            tools=tools, timeout=timeout, retry_budget=retry_budget)
            return {"choices": [{"message": {"content": "review text"}}]}, None
        teamsmod._tier1_call_with_retry = fake_call

        result = teamsmod.review_pr_diff(
            {"kind": "ollama", "name": "qwen3:8b"}, self.tmp, "title", "body",
            "diff text", False)
        self.assertEqual(result, {"ok": True, "text": "review text"})
        self.assertEqual(captured["tools"], [])
        self.assertEqual(captured["model"], "qwen3:8b")
        self.assertIn("GROUNDING-DIGEST", captured["user"])
        self.assertIn("diff text", captured["user"])

    def test_transport_failure_returns_error(self):
        teamsmod._tier1_call_with_retry = lambda *a, **kw: (None, "unreachable")
        result = teamsmod.review_pr_diff(
            {"kind": "ollama", "name": "qwen3:8b"}, self.tmp, "t", "b", "d", False)
        self.assertEqual(result, {"ok": False, "error": "unreachable"})


class ReviewPrDiffEngineTests(unittest.TestCase):
    """The load-bearing safety property: an engine-kind review NEVER runs
    against the project's real workdir, only a freshly created, always-
    removed scratch directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-review-engine-")
        self.real_workdir = os.path.join(self.tmp, "real-project")
        os.makedirs(self.real_workdir)
        self._orig_grounding = teamsmod.load_grounding
        self._orig_agent_run = teamsmod.agent_run
        self._orig_run_user_cmd = teamsmod._run_run_user_command
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = os.path.join(self.tmp, "state")
        teamsmod.load_grounding = lambda workdir, **kw: {"digest": "GROUNDING-DIGEST"}
        self.run_user_calls = []
        self.created_dirs = set()

        def fake_run_user_command(argv, cwd, timeout=None):
            self.run_user_calls.append((list(argv), cwd))
            if argv[0] == "mkdir":
                path = argv[-1]
                os.makedirs(path, exist_ok=True)
                self.created_dirs.add(path)
            elif argv[0] == "rm":
                path = argv[-1]
                shutil.rmtree(path, ignore_errors=True)
                self.created_dirs.discard(path)
            return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "timed_out": False,
                    "error": None}
        teamsmod._run_run_user_command = fake_run_user_command

    def tearDown(self):
        teamsmod.load_grounding = self._orig_grounding
        teamsmod.agent_run = self._orig_agent_run
        teamsmod._run_run_user_command = self._orig_run_user_cmd
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_agent_run_never_receives_the_real_project_workdir(self):
        captured = {}

        def fake_agent_run(engine, workdir, prompt, *, timeout=None, **kw):
            captured.update(engine=engine, workdir=workdir, prompt=prompt)
            return {"ok": True, "text": "engine review text", "error": None}
        teamsmod.agent_run = fake_agent_run

        result = teamsmod.review_pr_diff(
            {"kind": "engine", "name": "claude"}, self.real_workdir, "t", "b", "d", False)
        self.assertEqual(result, {"ok": True, "text": "engine review text"})
        self.assertNotEqual(captured["workdir"], self.real_workdir)
        self.assertIn("_ai_reviewer_scratch", captured["workdir"])
        self.assertEqual(captured["engine"], "claude")

    def test_scratch_directory_is_removed_after_success(self):
        scratch_path = {}

        def fake_agent_run(engine, workdir, prompt, *, timeout=None, **kw):
            scratch_path["path"] = workdir
            self.assertTrue(os.path.isdir(workdir))
            return {"ok": True, "text": "ok", "error": None}
        teamsmod.agent_run = fake_agent_run

        teamsmod.review_pr_diff(
            {"kind": "engine", "name": "claude"}, self.real_workdir, "t", "b", "d", False)
        self.assertFalse(os.path.exists(scratch_path["path"]))

    def test_scratch_directory_is_removed_even_on_agent_run_failure(self):
        scratch_path = {}

        def fake_agent_run(engine, workdir, prompt, *, timeout=None, **kw):
            scratch_path["path"] = workdir
            return {"ok": False, "text": "", "error": "engine crashed"}
        teamsmod.agent_run = fake_agent_run

        result = teamsmod.review_pr_diff(
            {"kind": "engine", "name": "claude"}, self.real_workdir, "t", "b", "d", False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "engine crashed")
        self.assertFalse(os.path.exists(scratch_path["path"]))

    def test_scratch_directory_is_removed_even_when_agent_run_raises_value_error(self):
        scratch_path = {}

        def fake_agent_run(engine, workdir, prompt, *, timeout=None, **kw):
            scratch_path["path"] = workdir
            raise ValueError("engine is unknown or not headless-enabled")
        teamsmod.agent_run = fake_agent_run

        result = teamsmod.review_pr_diff(
            {"kind": "engine", "name": "bogus"}, self.real_workdir, "t", "b", "d", False)
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(scratch_path["path"]))

    def test_scratch_creation_failure_returns_error_without_calling_agent_run(self):
        def failing_run_user_command(argv, cwd, timeout=None):
            return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                    "timed_out": False, "error": "failed to start command: boom"}
        teamsmod._run_run_user_command = failing_run_user_command
        agent_run_calls = []
        teamsmod.agent_run = lambda *a, **kw: agent_run_calls.append(a) or {"ok": True, "text": ""}

        result = teamsmod.review_pr_diff(
            {"kind": "engine", "name": "claude"}, self.real_workdir, "t", "b", "d", False)
        self.assertFalse(result["ok"])
        self.assertEqual(agent_run_calls, [])


if __name__ == "__main__":
    unittest.main()
