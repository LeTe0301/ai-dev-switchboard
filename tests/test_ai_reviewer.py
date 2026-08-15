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
            reviewed_at="2026-01-01T00:00:00Z", last_error=None, episode=1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"], {
            "label_present": True, "attempts": 0,
            "reviewed_at": "2026-01-01T00:00:00Z", "last_error": None, "episode": 1})

    def test_save_overwrites_only_the_named_entry(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/one#1", label_present=True, attempts=0, reviewed_at=None, last_error=None,
            episode=1)
        appmod._save_ai_reviewer_state_entry(
            "admin/two#2", label_present=False, attempts=1, reviewed_at=None, last_error="x",
            episode=1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(set(s.keys()), {"admin/one#1", "admin/two#2"})


class AiReviewerPrKeyTests(unittest.TestCase):
    """_ai_reviewer_pr_key() -- backlog item 17 part 2: Gitea's key format is
    unchanged, GitHub's gets a collision-proof "github:" prefix."""

    def test_gitea_key_is_unprefixed_owner_repo_hash_number(self):
        self.assertEqual(appmod._ai_reviewer_pr_key("gitea", "admin/proj", 1), "admin/proj#1")

    def test_github_key_is_prefixed(self):
        self.assertEqual(
            appmod._ai_reviewer_pr_key("github", "acme/widget", 7), "github:acme/widget#7")

    def test_gitea_and_github_keys_for_the_same_owner_repo_number_never_collide(self):
        gitea_key = appmod._ai_reviewer_pr_key("gitea", "acme/widget", 7)
        github_key = appmod._ai_reviewer_pr_key("github", "acme/widget", 7)
        self.assertNotEqual(gitea_key, github_key)


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
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(calls, [])

    def test_unseen_pr_without_label_records_absent_no_dispatch(self):
        self._fake_pulls([self._pr(1)])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["label_present"], False)

    def test_label_add_edge_triggers_dispatch_and_synchronous_state_write(self):
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)
        host, owner_repo, entry, pr, episode = self.bg_calls[0]
        self.assertEqual(host, "gitea")
        self.assertEqual(owner_repo, "admin/proj")
        self.assertEqual(pr["number"], 1)
        self.assertEqual(episode, 1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"], {
            "label_present": True, "attempts": 0, "reviewed_at": None, "last_error": None,
            "episode": 1})

    def test_label_still_present_after_a_successful_review_does_not_redispatch(self):
        # Simulates the state a completed, successful review leaves behind
        # (docs/implementation.md "Deviations from spec" -- attempts=0,
        # last_error=None after success).
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=0,
            reviewed_at="2026-01-01T00:00:00Z", last_error=None, episode=1)
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

    def test_label_still_present_after_a_failed_attempt_redispatches(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=1,
            reviewed_at=None, last_error="diff fetch failed (status 500)", episode=1)
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)

    def test_attempts_exhausted_gives_up_silently(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=3,
            reviewed_at=None, last_error="still failing", episode=1)
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

    def test_label_removed_then_readded_is_exactly_one_new_episode(self):
        # Poll 1: label present -> fresh trigger, then simulate a completed
        # success (attempts reset to 0, last_error None).
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=0,
            reviewed_at="2026-01-01T00:00:00Z", last_error=None, episode=1)
        self.bg_calls.clear()

        # Poll 2: label removed.
        self._fake_pulls([self._pr(1, labels=[])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["label_present"], False)
        self.assertEqual(s["admin/proj#1"]["attempts"], 0)
        self.assertEqual(s["admin/proj#1"]["episode"], 1)  # carried forward, not bumped

        # Poll 3: label re-added -> exactly one new trigger, episode bumped.
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)
        self.assertEqual(self.bg_calls[0][4], 2)

    def test_pre_existing_state_entry_missing_episode_key_is_backward_compatible(self):
        # AC5 (docs/spec.md item 8): a state entry persisted before this
        # episode-tracking fix shipped has no "episode" key at all -- every
        # read of it must default via .get("episode", 0), not crash
        # (docs/test-review.md minor gap -- this was previously only
        # verified manually, not via a dedicated regression test). Written
        # directly as raw JSON, deliberately bypassing
        # _save_ai_reviewer_state_entry() (which always includes "episode"
        # now), to faithfully simulate a pre-fix file on disk.
        with open(appmod.AI_REVIEWER_STATE_FILE, "w") as f:
            json.dump({"admin/proj#1": {
                "label_present": True, "attempts": 0,
                "reviewed_at": "2026-01-01T00:00:00Z", "last_error": None}}, f)

        # No-op poll path: label still present, already successfully
        # reviewed -> must not crash and must not redispatch.
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

        # Label-removed path: must not crash, and the missing "episode" key
        # is carried forward as the defaulted 0, not dropped or corrupted.
        self._fake_pulls([self._pr(1, labels=[])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["label_present"], False)
        self.assertEqual(s["admin/proj#1"]["episode"], 0)

        # Label re-added -> trigger edge bumps the defaulted 0 to episode 1.
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(len(self.bg_calls), 1)
        self.assertEqual(self.bg_calls[0][4], 1)

    def test_label_matched_by_exact_equality_not_substring(self):
        self._fake_pulls([self._pr(1, labels=["not ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})
        self.assertEqual(self.bg_calls, [])

    def test_non_200_status_skipped_without_raising(self):
        appmod._gitea_api = lambda method, path, body=None: (500, {})
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_non_list_response_skipped_without_raising(self):
        appmod._gitea_api = lambda method, path, body=None: (200, {"not": "a list"})
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_malformed_pr_entry_in_list_is_skipped_not_fatal(self):
        self._fake_pulls(["not-a-dict", self._pr(2, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})  # must not raise
        self.assertEqual(len(self.bg_calls), 1)
        self.assertEqual(self.bg_calls[0][3]["number"], 2)


class AiReviewerPollRepoGithubTests(unittest.TestCase):
    """_ai_reviewer_poll_repo("github", ...) -- same label-edge-detection/
    dispatch-gating logic as AiReviewerPollRepoTests above, proven identical
    for the GitHub path; only the PR-list-fetch call (github_list_open_prs
    instead of _gitea_api) and the pr_key prefix differ."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-poll-gh-")
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_enabled = appmod.AI_REVIEWER_ENABLED
        self._orig_label = appmod.AI_REVIEWER_LABEL
        self._orig_token = appmod.GITHUB_TOKEN
        self._orig_list_prs = appmod.github_list_open_prs
        self._orig_bg = appmod._ai_reviewer_review_bg
        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "state.json")
        appmod.AI_REVIEWER_ENABLED = True
        appmod.AI_REVIEWER_LABEL = "ready for review"
        appmod.GITHUB_TOKEN = "test-gh-token"
        self.bg_calls = []
        appmod._ai_reviewer_review_bg = lambda *a: self.bg_calls.append(a)

    def tearDown(self):
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod.AI_REVIEWER_ENABLED = self._orig_enabled
        appmod.AI_REVIEWER_LABEL = self._orig_label
        appmod.GITHUB_TOKEN = self._orig_token
        appmod.github_list_open_prs = self._orig_list_prs
        appmod._ai_reviewer_review_bg = self._orig_bg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_pulls(self, prs):
        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": prs}

    def _pr(self, number, labels=()):
        return {"number": number, "title": "t", "body": "b",
                "labels": [{"name": n} for n in labels]}

    def test_missing_token_makes_no_calls(self):
        appmod.GITHUB_TOKEN = ""
        calls = []
        appmod.github_list_open_prs = lambda owner, repo: calls.append((owner, repo)) or \
            {"ok": True, "prs": []}
        appmod._ai_reviewer_poll_repo("github", "acme/widget", {"name": "widget"})
        self.assertEqual(calls, [])

    def test_label_add_edge_triggers_dispatch_with_github_prefixed_state_key(self):
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("github", "acme/widget", {"name": "widget"})
        self.assertEqual(len(self.bg_calls), 1)
        host, owner_repo, entry, pr, episode = self.bg_calls[0]
        self.assertEqual(host, "github")
        self.assertEqual(owner_repo, "acme/widget")
        self.assertEqual(pr["number"], 1)
        s = appmod._load_ai_reviewer_state()
        self.assertIn("github:acme/widget#1", s)
        self.assertNotIn("acme/widget#1", s)

    def test_label_still_present_after_success_does_not_redispatch(self):
        appmod._save_ai_reviewer_state_entry(
            "github:acme/widget#1", label_present=True, attempts=0,
            reviewed_at="2026-01-01T00:00:00Z", last_error=None, episode=1)
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("github", "acme/widget", {"name": "widget"})
        self.assertEqual(self.bg_calls, [])

    def test_result_not_ok_skipped_without_raising(self):
        appmod.github_list_open_prs = lambda owner, repo: {"ok": False, "error": "boom"}
        appmod._ai_reviewer_poll_repo("github", "acme/widget", {"name": "widget"})  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_allowlist_gating_is_not_this_functions_job(self):
        # _ai_reviewer_poll_repo() itself has no allowlist concept -- that
        # gate lives entirely in _github_poll_if_due() (below), which never
        # even calls this function for a non-allowlisted repo. Directly
        # calling this function bypasses that gate by design (mirrors how
        # _ai_reviewer_poll_repo("gitea", ...) has no GITEA_REPO_MAP_FILE
        # membership check of its own either -- the caller already filtered).
        self._fake_pulls([self._pr(1, labels=["ready for review"])])
        appmod._ai_reviewer_poll_repo("github", "not-allowlisted/repo", {"name": "x"})
        self.assertEqual(len(self.bg_calls), 1)


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
            "admin/proj#1", label_present=True, attempts=0, reviewed_at=None, last_error=None,
            episode=1)

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
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        self.assertEqual(comment_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)
        self.assertIn("404", s["admin/proj#1"]["last_error"])
        self.assertTrue(s["admin/proj#1"]["label_present"])

    def test_diff_fetch_connection_error_records_failure(self):
        def raise_conn(method, path):
            raise ConnectionError("gitea unreachable")
        appmod._gitea_api_raw = raise_conn
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)  # must not raise
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_empty_but_200_diff_is_still_reviewed_not_an_error(self):
        appmod._gitea_api_raw = lambda method, path: (200, "")
        posted = {}
        appmod._gitea_api = lambda method, path, body=None: posted.update(
            path=path, body=body) or (201, {})
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
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
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
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
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        self.assertFalse(self.review_calls[0][5])
        self.assertNotIn("truncated", posted["body"]["body"])

    def test_model_not_in_roster_records_failure_no_diff_fetch(self):
        appmod.AI_REVIEWER_MODEL = "ollama:does-not-exist"
        diff_calls = []
        appmod._gitea_api_raw = lambda method, path: diff_calls.append(path) or (200, "diff")
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        s = appmod._load_ai_reviewer_state()
        self.assertIn("not found in roster", s["admin/proj#1"]["last_error"])
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_model_unset_records_failure(self):
        appmod.AI_REVIEWER_MODEL = ""
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_ollama_tag_containing_colon_is_split_on_first_colon_only(self):
        appmod.AI_REVIEWER_MODEL = "ollama:qwen3:8b"
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (200, {})
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
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
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        self.assertEqual(self.review_calls[0][0]["kind"], "ollama")

    def test_review_generation_failure_records_error_no_comment_post(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod.teams.review_pr_diff = lambda **kw: {"ok": False, "error": "model unreachable"}
        comment_calls = []
        appmod._gitea_api = lambda method, path, body=None: comment_calls.append(path) or (200, {})

        def fake_review(model, workdir, pr_title, pr_body, diff_text, diff_truncated):
            return {"ok": False, "error": "model unreachable"}
        appmod.teams.review_pr_diff = fake_review
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        self.assertEqual(comment_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["last_error"], "model unreachable")
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_comment_post_non_2xx_records_failure(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (403, {})
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)
        self.assertIn("403", s["admin/proj#1"]["last_error"])

    def test_comment_post_connection_error_records_failure(self):
        appmod._gitea_api_raw = lambda method, path: (200, "diff")

        def raise_conn(method, path, body=None):
            raise ConnectionError("gitea unreachable")
        appmod._gitea_api = raise_conn
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)  # must not raise
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["attempts"], 1)

    def test_success_resets_attempts_and_records_reviewed_at(self):
        appmod._save_ai_reviewer_state_entry(
            "admin/proj#1", label_present=True, attempts=2, reviewed_at=None,
            last_error="prior failure", episode=1)
        appmod._gitea_api_raw = lambda method, path: (200, "diff")
        appmod._gitea_api = lambda method, path, body=None: (200, {})
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
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
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
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
        appmod._ai_reviewer_review_run("gitea", "admin/proj", {"name": "proj"}, self._pr(), 1)
        for method, path in calls:
            self.assertNotIn("merge", path)
            self.assertNotIn("approve", path)
            self.assertNotIn("/branches/", path)


class AiReviewerReviewRunGithubTests(unittest.TestCase):
    """_ai_reviewer_review_run("github", ...) -- same pipeline as
    AiReviewerReviewRunTests above, proven identical for the GitHub path;
    only the diff-fetch (github_pr_diff) and comment-post
    (github_post_pr_comment) calls, and the "github:"-prefixed pr_key,
    differ."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-run-gh-")
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_max_diff = appmod.AI_REVIEWER_MAX_DIFF_BYTES
        self._orig_model = appmod.AI_REVIEWER_MODEL
        self._orig_pr_diff = appmod.github_pr_diff
        self._orig_post_comment = appmod.github_post_pr_comment
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

        appmod._save_ai_reviewer_state_entry(
            "github:acme/widget#1", label_present=True, attempts=0,
            reviewed_at=None, last_error=None, episode=1)

    def tearDown(self):
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = self._orig_max_diff
        appmod.AI_REVIEWER_MODEL = self._orig_model
        appmod.github_pr_diff = self._orig_pr_diff
        appmod.github_post_pr_comment = self._orig_post_comment
        appmod.teams.roster = self._orig_roster
        appmod.teams.review_pr_diff = self._orig_review
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pr(self, number=1, title="add feature", body="does a thing"):
        return {"number": number, "title": title, "body": body}

    def test_diff_fetch_failure_records_failure_and_posts_no_comment(self):
        appmod.github_pr_diff = lambda owner, repo, number: {
            "ok": False, "error": "diff fetch failed (status 404)"}
        comment_calls = []
        appmod.github_post_pr_comment = lambda owner, repo, number, body: \
            comment_calls.append((owner, repo, number, body)) or {"ok": True}
        appmod._ai_reviewer_review_run("github", "acme/widget", {"name": "widget"}, self._pr(), 1)
        self.assertEqual(comment_calls, [])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["github:acme/widget#1"]["attempts"], 1)
        self.assertIn("404", s["github:acme/widget#1"]["last_error"])
        self.assertTrue(s["github:acme/widget#1"]["label_present"])

    def test_success_posts_comment_and_resets_state(self):
        appmod.github_pr_diff = lambda owner, repo, number: {"ok": True, "diff": "diff text"}
        posted = {}

        def fake_post(owner, repo, number, body):
            posted.update(owner=owner, repo=repo, number=number, body=body)
            return {"ok": True}
        appmod.github_post_pr_comment = fake_post
        appmod._ai_reviewer_review_run("github", "acme/widget", {"name": "widget"}, self._pr(), 1)
        self.assertEqual(posted["owner"], "acme")
        self.assertEqual(posted["repo"], "widget")
        self.assertEqual(posted["number"], 1)
        self.assertIn("Looks fine.", posted["body"])
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["github:acme/widget#1"]["attempts"], 0)
        self.assertIsNone(s["github:acme/widget#1"]["last_error"])
        self.assertIsNotNone(s["github:acme/widget#1"]["reviewed_at"])

    def test_comment_post_failure_records_failure(self):
        appmod.github_pr_diff = lambda owner, repo, number: {"ok": True, "diff": "diff text"}
        appmod.github_post_pr_comment = lambda owner, repo, number, body: \
            {"ok": False, "error": "comment post failed (status 403)"}
        appmod._ai_reviewer_review_run("github", "acme/widget", {"name": "widget"}, self._pr(), 1)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["github:acme/widget#1"]["attempts"], 1)
        self.assertIn("403", s["github:acme/widget#1"]["last_error"])

    def test_diff_exceeding_cap_is_truncated_and_comment_notes_it(self):
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = 10
        appmod.github_pr_diff = lambda owner, repo, number: {
            "ok": True, "diff": "0123456789ABCDEF"}
        posted = {}

        def fake_post(owner, repo, number, body):
            posted["body"] = body
            return {"ok": True}
        appmod.github_post_pr_comment = fake_post
        appmod._ai_reviewer_review_run("github", "acme/widget", {"name": "widget"}, self._pr(), 1)
        sent_diff = self.review_calls[0][4]
        self.assertEqual(len(sent_diff.encode("utf-8")), 10)
        self.assertTrue(self.review_calls[0][5])  # diff_truncated
        self.assertIn("truncated", posted["body"])


class AiReviewerReviewBgConcurrencyTests(unittest.TestCase):
    """_ai_reviewer_review_bg()'s per-PR non-blocking lock -- a second
    dispatch for the same PR while one is still running is dropped, not
    queued (mirrors tests/test_gitea_poll.py's GiteaSyncBgConcurrencyTests)."""

    def test_second_dispatch_while_first_still_holds_the_lock_is_dropped(self):
        pr_key = "admin/lockedproj#7"
        lock_key = (pr_key, 1)  # backlog item 8: lock is keyed on (pr_key, episode)
        lock = appmod._ai_reviewer_pr_lock_for(lock_key)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            orig = appmod._ai_reviewer_review_run
            calls = []
            appmod._ai_reviewer_review_run = lambda *a: calls.append(a)
            try:
                appmod._ai_reviewer_review_bg(
                    "gitea", "admin/lockedproj", {"name": "p"}, {"number": 7}, 1)
                time.sleep(0.05)
                self.assertEqual(calls, [])
            finally:
                appmod._ai_reviewer_review_run = orig
        finally:
            lock.release()

    def test_lock_is_released_after_completion_so_next_dispatch_can_run(self):
        pr_key = "admin/freeproj#9"
        lock_key = (pr_key, 1)
        orig = appmod._ai_reviewer_review_run
        calls = []
        appmod._ai_reviewer_review_run = lambda *a: calls.append(a)
        try:
            appmod._ai_reviewer_review_bg("gitea", "admin/freeproj", {"name": "p"}, {"number": 9}, 1)
            for _ in range(50):
                if calls:
                    break
                time.sleep(0.02)
            self.assertEqual(len(calls), 1)
            # backlog item 8: the (pr_key, episode) lock entry is cleaned up
            # once the thread finishes, so _ai_reviewer_pr_locks does not
            # grow by one entry per label-toggle cycle forever.
            for _ in range(50):
                with appmod._ai_reviewer_pr_locks_guard:
                    if lock_key not in appmod._ai_reviewer_pr_locks:
                        break
                time.sleep(0.02)
            with appmod._ai_reviewer_pr_locks_guard:
                self.assertNotIn(lock_key, appmod._ai_reviewer_pr_locks)
        finally:
            appmod._ai_reviewer_review_run = orig

    def test_github_and_gitea_dispatch_for_same_owner_repo_use_different_locks(self):
        # host-prefixed pr_key (_ai_reviewer_pr_key) means a Gitea and a
        # GitHub dispatch for the numerically-identical owner/repo#number
        # never contend on the same lock (docs/spec.md item 17 part 2).
        gitea_key = appmod._ai_reviewer_pr_key("gitea", "acme/widget", 3)
        github_key = appmod._ai_reviewer_pr_key("github", "acme/widget", 3)
        self.assertNotEqual(gitea_key, github_key)
        gitea_lock = appmod._ai_reviewer_pr_lock_for(gitea_key)
        github_lock = appmod._ai_reviewer_pr_lock_for(github_key)
        self.assertIsNot(gitea_lock, github_lock)


class AiReviewerEpisodeRaceTests(unittest.TestCase):
    """Backlog item 8's race scenario, simulated end to end (not mocked
    piecewise): a label removed-and-re-added while the *previous* episode's
    review thread is still in flight must dispatch a NEW thread (not drop
    it), and the stale thread's eventual completion must be a no-op against
    the newer episode's state (docs/spec.md "Acceptance criteria").

    Drives the real _ai_reviewer_poll_repo -> _ai_reviewer_review_bg ->
    _ai_reviewer_review_run chain (none of the three mocked out), only the
    lowest-level I/O (_gitea_api/_gitea_api_raw, teams.roster/
    teams.review_pr_diff) is faked -- same "prove it end to end" approach
    GithubPollEndToEndReviewTests above already uses for the ordinary
    (non-racing) path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-race-")
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_enabled = appmod.AI_REVIEWER_ENABLED
        self._orig_label = appmod.AI_REVIEWER_LABEL
        self._orig_max_attempts = appmod.AI_REVIEWER_MAX_ATTEMPTS
        self._orig_max_diff = appmod.AI_REVIEWER_MAX_DIFF_BYTES
        self._orig_model = appmod.AI_REVIEWER_MODEL
        self._orig_api = appmod._gitea_api
        self._orig_api_raw = appmod._gitea_api_raw
        self._orig_roster = appmod.teams.roster
        self._orig_review = appmod.teams.review_pr_diff

        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "state.json")
        appmod.AI_REVIEWER_ENABLED = True
        appmod.AI_REVIEWER_LABEL = "ready for review"
        appmod.AI_REVIEWER_MAX_ATTEMPTS = 3
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = 40000
        appmod.AI_REVIEWER_MODEL = "ollama:qwen3:8b"
        appmod.teams.roster = lambda: [
            {"name": "qwen3:8b", "kind": "ollama", "label": "qwen3:8b", "tier": 1,
             "delegate_capable": False}]
        appmod.teams.review_pr_diff = lambda model, **kw: {"ok": True, "text": "Looks fine."}

        self.pr_labels = []
        self.diff_calls = []       # one entry appended per diff-fetch call, in order
        self.posted_comments = []
        self.stale_started = threading.Event()   # set once the stale (1st) diff fetch begins
        self.stale_release = threading.Event()   # test sets this to let the stale run proceed

        def fake_api(method, path, body=None):
            if method == "GET" and path.endswith("/pulls?state=open"):
                return 200, [{"number": 1, "title": "t", "body": "b",
                              "labels": [{"name": n} for n in self.pr_labels]}]
            if method == "POST" and "/comments" in path:
                self.posted_comments.append(body)
                return 201, {}
            raise AssertionError(f"unexpected _gitea_api call: {method} {path}")
        appmod._gitea_api = fake_api

        def fake_api_raw(method, path):
            self.diff_calls.append(path)
            if len(self.diff_calls) == 1:
                # This is the stale (episode 1) run -- block until the test
                # explicitly releases it, simulating "still in flight".
                self.stale_started.set()
                self.stale_release.wait(5)
            return 200, "diff --git a/x b/x\n"
        appmod._gitea_api_raw = fake_api_raw

    def tearDown(self):
        self.stale_release.set()  # never leave a blocked thread behind
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod.AI_REVIEWER_ENABLED = self._orig_enabled
        appmod.AI_REVIEWER_LABEL = self._orig_label
        appmod.AI_REVIEWER_MAX_ATTEMPTS = self._orig_max_attempts
        appmod.AI_REVIEWER_MAX_DIFF_BYTES = self._orig_max_diff
        appmod.AI_REVIEWER_MODEL = self._orig_model
        appmod._gitea_api = self._orig_api
        appmod._gitea_api_raw = self._orig_api_raw
        appmod.teams.roster = self._orig_roster
        appmod.teams.review_pr_diff = self._orig_review
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _poll(self):
        appmod._ai_reviewer_poll_repo("gitea", "admin/proj", {"name": "proj"})

    def _wait_for(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_new_episode_is_dispatched_not_dropped_while_stale_episode_in_flight(self):
        # Poll 1: label present -> episode 1 triggers, dispatches a
        # background thread that immediately blocks in its diff fetch
        # (simulating "still running").
        self.pr_labels = ["ready for review"]
        self._poll()
        self.assertTrue(self._wait_for(lambda: self.stale_started.is_set()),
                         "stale (episode 1) run never started")
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["episode"], 1)

        # Poll 2: label removed while episode 1's thread is still blocked.
        self.pr_labels = []
        self._poll()
        s = appmod._load_ai_reviewer_state()
        self.assertFalse(s["admin/proj#1"]["label_present"])
        self.assertEqual(s["admin/proj#1"]["episode"], 1)  # carried forward, not bumped

        # Poll 3: label re-added -> a FRESH trigger edge (episode 2), even
        # though episode 1's review thread is still holding the stale
        # (pr_key, 1) lock. Before the fix this dispatch would be silently
        # dropped (docs/spec.md "Problem"); after the fix it must run.
        self.pr_labels = ["ready for review"]
        self._poll()
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["episode"], 2)

        # Prove the new episode's dispatch actually ran (a second diff fetch
        # happened), not just that the state file says episode 2.
        self.assertTrue(self._wait_for(lambda: len(self.diff_calls) >= 2),
                         "episode 2's review thread was never dispatched -- "
                         "dropped by the stale (pr_key, episode-1) lock")

        # Episode 2 completes (its diff fetch does not block).
        self.assertTrue(self._wait_for(
            lambda: appmod._load_ai_reviewer_state()["admin/proj#1"].get("reviewed_at") is not None))
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["episode"], 2)
        self.assertEqual(s["admin/proj#1"]["attempts"], 0)
        self.assertIsNone(s["admin/proj#1"]["last_error"])
        episode2_reviewed_at = s["admin/proj#1"]["reviewed_at"]
        self.assertEqual(len(self.posted_comments), 1)

        # Now let the stale episode-1 thread finish. Its completion write
        # must be a no-op -- state must still reflect episode 2's own
        # outcome, not get clobbered by the stale thread's own (also
        # "successful") completion.
        self.stale_release.set()
        # Give the stale thread a moment to actually finish and attempt its
        # (now-guarded) state write.
        self.assertTrue(self._wait_for(lambda: len(self.diff_calls) >= 1))
        time.sleep(0.2)
        s = appmod._load_ai_reviewer_state()
        self.assertEqual(s["admin/proj#1"]["episode"], 2)
        self.assertEqual(s["admin/proj#1"]["reviewed_at"], episode2_reviewed_at)
        self.assertEqual(s["admin/proj#1"]["attempts"], 0)
        self.assertIsNone(s["admin/proj#1"]["last_error"])

    def test_locks_dict_does_not_grow_unbounded_across_episodes(self):
        self.pr_labels = ["ready for review"]
        self._poll()
        self.assertTrue(self._wait_for(lambda: self.stale_started.is_set()))
        self.stale_release.set()
        # episode 1's (pr_key, 1) lock entry is removed once its thread ends.
        self.assertTrue(self._wait_for(
            lambda: ("admin/proj#1", 1) not in appmod._ai_reviewer_pr_locks))
        with appmod._ai_reviewer_pr_locks_guard:
            self.assertNotIn(("admin/proj#1", 1), appmod._ai_reviewer_pr_locks)


class AiReviewerEpisodeAtomicWriteRaceTests(unittest.TestCase):
    """docs/test-review.md Defect 1: _ai_reviewer_record_failure()/the
    success-write guard used to check `episode` currency via an UNLOCKED
    read, then separately call _save_ai_reviewer_state_entry() for the
    actual write -- a concurrent trigger-edge write could land in the gap
    between that check and that write, so the stale thread's own
    already-passed check went on to clobber the newer episode's state
    anyway, moving `episode` backwards. _ai_reviewer_save_if_current_
    episode() now performs the read, the check, and the write all inside
    ONE _ai_reviewer_state_lock critical section, closing the gap
    structurally rather than just narrowing it.

    This reproduces the reviewer's own repro technique directly against the
    fix: hooks _load_ai_reviewer_state() (the exact read whose locked/
    unlocked status Defect 1 was about) so that a concurrent, fully
    independent fresh trigger-edge write is injected right where the old
    unlocked check's own read used to return -- proving that write can no
    longer land inside the currency check's own critical section, and that
    the final state is never clobbered backwards to the stale episode.
    Verified against the actual pre-fix code shape too (temporarily
    reverting _ai_reviewer_save_if_current_episode() to the old unlocked-
    check-then-separate-write pattern and confirming this test fails with
    exactly the clobber Defect 1 describes; see docs/implementation.md)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-atomic-race-")
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_load = appmod._load_ai_reviewer_state
        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod._load_ai_reviewer_state = self._orig_load
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_trigger_edge_write_cannot_land_inside_stale_threads_guarded_write(self):
        # Reproduces docs/test-review.md Defect 1's own repro technique:
        # hook the read so that, immediately after it returns a value to
        # its caller, an independent, unsynchronized concurrent trigger-edge
        # write (episode 2) is injected -- then let the stale (episode 1)
        # call finish and check whether it clobbered the fresh write.
        pr_key = "admin/proj#1"
        appmod._save_ai_reviewer_state_entry(
            pr_key, label_present=True, attempts=1, reviewed_at=None,
            last_error="prior failure", episode=1)

        fresh_write_started = threading.Event()
        fresh_write_done = threading.Event()
        landed_before_read_returned = []
        fresh_thread = []
        already_triggered = threading.Event()

        def fresh_trigger_write():
            fresh_write_started.set()
            # A completely independent, unsynchronized caller -- exactly
            # what a real trigger-edge write from a concurrent poll pass is
            # (docs/spec.md "The poll extension").
            appmod._save_ai_reviewer_state_entry(
                pr_key, label_present=True, attempts=0, reviewed_at=None,
                last_error=None, episode=2)
            fresh_write_done.set()

        orig_load = appmod._load_ai_reviewer_state

        def hooked_load():
            if already_triggered.is_set():
                return orig_load()
            already_triggered.set()
            # This is the FIRST _load_ai_reviewer_state() call made while
            # processing the stale (episode 1) completion -- the exact read
            # whose unlocked/locked status is what Defect 1 was about.
            # _ai_reviewer_save_if_current_episode() calls this from INSIDE
            # its own _ai_reviewer_state_lock critical section (the fix);
            # the pre-fix shape called an equivalent read from OUTSIDE any
            # lock. Capture the result now, then try to land the fresh
            # trigger-edge write before returning it -- if this read is
            # unlocked, the fresh write completes here, unblocked; if it's
            # inside the lock (the fix), the fresh write blocks trying to
            # acquire the same lock and cannot complete until this whole
            # call (and the lock it holds) finishes.
            result = orig_load()
            t = threading.Thread(target=fresh_trigger_write, daemon=True)
            fresh_thread.append(t)
            t.start()
            fresh_write_started.wait(1)
            landed_before_read_returned.append(fresh_write_done.wait(1))
            return result

        appmod._load_ai_reviewer_state = hooked_load

        # Calls _ai_reviewer_save_if_current_episode() directly (rather than
        # via _ai_reviewer_record_failure(), which does its own unrelated
        # _load_ai_reviewer_state() call first just to compute `attempts`/
        # `reviewed_at` for its arguments) so hooked_load's FIRST call is
        # unambiguously the one that matters: the actual currency check.
        appmod._ai_reviewer_save_if_current_episode(
            pr_key, 1, label_present=True, attempts=2,
            reviewed_at=None, last_error="stale failure from episode 1")

        # Let the fresh trigger-edge write finish either way (immediately,
        # if it already landed above; or now, if it was blocked on the lock
        # and the stale call's own write has since released it).
        fresh_thread[0].join(2)
        self.assertTrue(fresh_write_done.is_set(), "fresh trigger-edge write never completed")

        s = appmod._load_ai_reviewer_state()
        self.assertEqual(
            s[pr_key]["episode"], 2,
            "the stale (episode 1) completion clobbered the newer episode 2's state "
            f"(fresh write landed inside the stale read's own critical section: "
            f"{landed_before_read_returned})")
        self.assertIsNone(s[pr_key]["last_error"])
        self.assertEqual(s[pr_key]["attempts"], 0)


class AiReviewerGithubReposAllowlistTests(unittest.TestCase):
    """_load_ai_reviewer_github_repos() -- hand-edited JSON array loader,
    same never-crash contract as _load_deploy_map()/_load_gitea_repo_map()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-ai-reviewer-gh-repos-")
        self._orig = appmod.AI_REVIEWER_GITHUB_REPOS_FILE
        appmod.AI_REVIEWER_GITHUB_REPOS_FILE = os.path.join(self.tmp, "repos.json")

    def tearDown(self):
        appmod.AI_REVIEWER_GITHUB_REPOS_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(appmod._load_ai_reviewer_github_repos(), set())

    def test_valid_array_returns_set_of_strings(self):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump(["acme/widget", "acme/gizmo"], f)
        self.assertEqual(appmod._load_ai_reviewer_github_repos(), {"acme/widget", "acme/gizmo"})

    def test_malformed_json_returns_empty_set_not_raise(self):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            f.write("not json{{{")
        self.assertEqual(appmod._load_ai_reviewer_github_repos(), set())

    def test_not_a_list_returns_empty_set(self):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump({"acme/widget": True}, f)
        self.assertEqual(appmod._load_ai_reviewer_github_repos(), set())

    def test_non_string_entries_are_dropped(self):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump(["acme/widget", 42, "", None, ["nested"]], f)
        self.assertEqual(appmod._load_ai_reviewer_github_repos(), {"acme/widget"})

    def test_empty_array_returns_empty_set(self):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump([], f)
        self.assertEqual(appmod._load_ai_reviewer_github_repos(), set())


class GithubPollIfDueTests(unittest.TestCase):
    """_github_poll_if_due() -- throttle/lock shape mirroring
    tests/test_gitea_poll.py's GiteaPollIfDueTests, plus the allowlist/
    token/enabled gating specific to this poll (docs/spec.md "Proposed
    approach" #3)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-github-poll-due-")
        self._orig_enabled = appmod.AI_REVIEWER_ENABLED
        self._orig_token = appmod.GITHUB_TOKEN
        self._orig_last_at = appmod._github_poll_last_at
        self._orig_repos_file = appmod.AI_REVIEWER_GITHUB_REPOS_FILE
        self._orig_instance_names = appmod.instance_names
        self._orig_detect_origin = appmod.detect_project_origin
        self._orig_poll_repo = appmod._ai_reviewer_poll_repo
        appmod.AI_REVIEWER_GITHUB_REPOS_FILE = os.path.join(self.tmp, "repos.json")
        appmod.AI_REVIEWER_ENABLED = True
        appmod.GITHUB_TOKEN = "test-gh-token"
        appmod._github_poll_last_at = 0.0
        self.poll_calls = []
        appmod._ai_reviewer_poll_repo = lambda *a: self.poll_calls.append(a)

    def tearDown(self):
        appmod.AI_REVIEWER_ENABLED = self._orig_enabled
        appmod.GITHUB_TOKEN = self._orig_token
        appmod._github_poll_last_at = self._orig_last_at
        appmod.AI_REVIEWER_GITHUB_REPOS_FILE = self._orig_repos_file
        appmod.instance_names = self._orig_instance_names
        appmod.detect_project_origin = self._orig_detect_origin
        appmod._ai_reviewer_poll_repo = self._orig_poll_repo
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_allowlist(self, repos):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump(repos, f)

    def test_ai_reviewer_disabled_makes_no_calls(self):
        appmod.AI_REVIEWER_ENABLED = False
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": "widget"}
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])

    def test_missing_github_token_makes_no_calls(self):
        appmod.GITHUB_TOKEN = ""
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])

    def test_empty_allowlist_makes_no_calls_and_skips_instance_walk(self):
        walked = []
        appmod.instance_names = lambda: walked.append(1) or ["widget"]
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])
        self.assertEqual(walked, [])  # short-circuits before even listing projects

    def test_allowlisted_github_origin_project_is_polled(self):
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": "widget"}
        appmod._github_poll_if_due()
        self.assertEqual(len(self.poll_calls), 1)
        host, owner_repo, entry = self.poll_calls[0]
        self.assertEqual(host, "github")
        self.assertEqual(owner_repo, "acme/widget")
        self.assertEqual(entry["name"], "widget")

    def test_non_allowlisted_github_origin_project_is_never_polled(self):
        self._write_allowlist(["acme/other"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": "widget"}
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])

    def test_non_github_origin_project_is_skipped(self):
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "local", "owner": None, "repo": None}
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])

    def test_origin_detection_raising_for_one_project_does_not_stop_the_rest(self):
        self._write_allowlist(["acme/widget", "acme/gizmo"])
        appmod.instance_names = lambda: ["bad", "good"]

        def flaky_origin(name):
            if name == "bad":
                raise OSError("boom")
            return {"kind": "github", "owner": "acme", "repo": "gizmo"}
        appmod.detect_project_origin = flaky_origin
        appmod._github_poll_if_due()  # must not raise
        self.assertEqual(len(self.poll_calls), 1)
        self.assertEqual(self.poll_calls[0][1], "acme/gizmo")

    def test_poll_repo_raising_for_one_project_does_not_stop_the_rest(self):
        self._write_allowlist(["acme/one", "acme/two"])
        appmod.instance_names = lambda: ["one", "two"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": name}

        def flaky_poll(host, owner_repo, entry):
            self.poll_calls.append((host, owner_repo, entry))
            if owner_repo == "acme/one":
                raise RuntimeError("boom")
        appmod._ai_reviewer_poll_repo = flaky_poll
        appmod._github_poll_if_due()  # must not raise
        self.assertEqual(len(self.poll_calls), 2)

    def test_not_yet_due_makes_no_calls(self):
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": "widget"}
        appmod._github_poll_last_at = time.time()  # just polled
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])

    def test_second_call_after_interval_elapses_polls_again(self):
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": "widget"}
        appmod._github_poll_last_at = time.time() - appmod.GITHUB_POLL_INTERVAL_SECONDS - 1
        appmod._github_poll_if_due()
        self.assertEqual(len(self.poll_calls), 1)

    def test_project_with_missing_owner_or_repo_is_skipped(self):
        self._write_allowlist(["acme/widget"])
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": None, "repo": None}
        appmod._github_poll_if_due()
        self.assertEqual(self.poll_calls, [])


class GithubPollEndToEndReviewTests(unittest.TestCase):
    """A single full pass through _github_poll_if_due() -> _ai_reviewer_
    poll_repo() -> _ai_reviewer_review_bg()/_run() with only the lowest-level
    GitHub client functions mocked (github_list_open_prs/github_pr_diff/
    github_post_pr_comment) -- proves the acceptance criteria end to end
    rather than piecewise, mirroring how the Gitea path is already proven
    piecewise above (this is the one case worth the extra integration-style
    coverage, since it's the exact shape docs/spec.md's own acceptance
    criteria describe)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-github-poll-e2e-")
        self._orig_enabled = appmod.AI_REVIEWER_ENABLED
        self._orig_label = appmod.AI_REVIEWER_LABEL
        self._orig_token = appmod.GITHUB_TOKEN
        self._orig_last_at = appmod._github_poll_last_at
        self._orig_repos_file = appmod.AI_REVIEWER_GITHUB_REPOS_FILE
        self._orig_state_file = appmod.AI_REVIEWER_STATE_FILE
        self._orig_model = appmod.AI_REVIEWER_MODEL
        self._orig_instance_names = appmod.instance_names
        self._orig_detect_origin = appmod.detect_project_origin
        self._orig_list_prs = appmod.github_list_open_prs
        self._orig_pr_diff = appmod.github_pr_diff
        self._orig_post_comment = appmod.github_post_pr_comment
        self._orig_roster = appmod.teams.roster
        self._orig_review = appmod.teams.review_pr_diff

        appmod.AI_REVIEWER_ENABLED = True
        appmod.AI_REVIEWER_LABEL = "ready for review"
        appmod.GITHUB_TOKEN = "test-gh-token"
        appmod._github_poll_last_at = 0.0
        appmod.AI_REVIEWER_GITHUB_REPOS_FILE = os.path.join(self.tmp, "repos.json")
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump(["acme/widget"], f)
        appmod.AI_REVIEWER_STATE_FILE = os.path.join(self.tmp, "state.json")
        appmod.AI_REVIEWER_MODEL = "ollama:qwen3:8b"
        appmod.instance_names = lambda: ["widget"]
        appmod.detect_project_origin = lambda name: {
            "kind": "github", "owner": "acme", "repo": "widget"}
        appmod.teams.roster = lambda: [
            {"name": "qwen3:8b", "kind": "ollama", "label": "qwen3:8b", "tier": 1,
             "delegate_capable": False}]
        appmod.teams.review_pr_diff = lambda model, **kw: {"ok": True, "text": "Looks fine."}
        appmod.github_pr_diff = lambda owner, repo, number: {"ok": True, "diff": "diff text"}
        self.posted = []
        appmod.github_post_pr_comment = lambda owner, repo, number, body: \
            self.posted.append((owner, repo, number, body)) or {"ok": True}

    def tearDown(self):
        appmod.AI_REVIEWER_ENABLED = self._orig_enabled
        appmod.AI_REVIEWER_LABEL = self._orig_label
        appmod.GITHUB_TOKEN = self._orig_token
        appmod._github_poll_last_at = self._orig_last_at
        appmod.AI_REVIEWER_GITHUB_REPOS_FILE = self._orig_repos_file
        appmod.AI_REVIEWER_STATE_FILE = self._orig_state_file
        appmod.AI_REVIEWER_MODEL = self._orig_model
        appmod.instance_names = self._orig_instance_names
        appmod.detect_project_origin = self._orig_detect_origin
        appmod.github_list_open_prs = self._orig_list_prs
        appmod.github_pr_diff = self._orig_pr_diff
        appmod.github_post_pr_comment = self._orig_post_comment
        appmod.teams.roster = self._orig_roster
        appmod.teams.review_pr_diff = self._orig_review
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_for_state_key(self, key, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = appmod._load_ai_reviewer_state()
            if key in s and s[key].get("reviewed_at") is not None:
                return s
            time.sleep(0.02)
        return appmod._load_ai_reviewer_state()

    def test_label_add_posts_exactly_one_comment_and_records_state(self):
        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b",
             "labels": [{"name": "ready for review"}]}]}
        appmod._github_poll_if_due()
        s = self._wait_for_state_key("github:acme/widget#1")
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(s["github:acme/widget#1"]["attempts"], 0)
        self.assertIsNone(s["github:acme/widget#1"]["last_error"])

    def test_label_still_present_on_repeated_poll_posts_no_second_comment(self):
        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b",
             "labels": [{"name": "ready for review"}]}]}
        appmod._github_poll_if_due()
        self._wait_for_state_key("github:acme/widget#1")
        appmod._github_poll_last_at = 0.0  # force the throttle open for a second pass
        appmod._github_poll_if_due()
        time.sleep(0.1)
        self.assertEqual(len(self.posted), 1)

    def test_label_removed_then_readded_is_a_fresh_episode(self):
        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b",
             "labels": [{"name": "ready for review"}]}]}
        appmod._github_poll_if_due()
        self._wait_for_state_key("github:acme/widget#1")

        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b", "labels": []}]}
        appmod._github_poll_last_at = 0.0
        appmod._github_poll_if_due()
        s = appmod._load_ai_reviewer_state()
        self.assertFalse(s["github:acme/widget#1"]["label_present"])

        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b",
             "labels": [{"name": "ready for review"}]}]}
        appmod._github_poll_last_at = 0.0
        appmod._github_poll_if_due()
        time.sleep(0.1)
        self.assertEqual(len(self.posted), 2)

    def test_non_allowlisted_repo_never_reviewed_even_with_label(self):
        with open(appmod.AI_REVIEWER_GITHUB_REPOS_FILE, "w") as f:
            json.dump(["someone-else/other-repo"], f)
        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b",
             "labels": [{"name": "ready for review"}]}]}
        appmod._github_poll_if_due()
        time.sleep(0.1)
        self.assertEqual(self.posted, [])
        s = appmod._load_ai_reviewer_state()
        self.assertNotIn("github:acme/widget#1", s)

    def test_diff_fetch_failure_records_failure_no_comment(self):
        appmod.github_pr_diff = lambda owner, repo, number: {
            "ok": False, "error": "diff fetch failed (status 404)"}
        appmod.github_list_open_prs = lambda owner, repo: {"ok": True, "prs": [
            {"number": 1, "title": "t", "body": "b",
             "labels": [{"name": "ready for review"}]}]}
        appmod._github_poll_if_due()

        def _wait_for_error():
            deadline = time.time() + 2.0
            while time.time() < deadline:
                s = appmod._load_ai_reviewer_state()
                if s.get("github:acme/widget#1", {}).get("last_error"):
                    return s
                time.sleep(0.02)
            return appmod._load_ai_reviewer_state()

        s = _wait_for_error()
        self.assertEqual(self.posted, [])
        self.assertIn("404", s["github:acme/widget#1"]["last_error"])


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
