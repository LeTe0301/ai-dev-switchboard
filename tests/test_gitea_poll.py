#!/usr/bin/env python3
"""
Tests for poll-based sync-on-push (backlog item 2c, part 1 -- docs/spec.md):
the repo-map file, the throttled poll pass (_gitea_poll_if_due/
_gitea_poll_one), and the actual sync-attempt/repo-map-update logic
(_gitea_sync_run). _gitea_api and subprocess.run are both monkeypatched --
no real Docker/network/Gitea-server calls anywhere in this file, same
convention tests/test_gitea.py's own GiteaApiTests/CreateProjectGiteaTests
already use.

_gitea_poll_one's own SHA-compare/dispatch logic is tested with
_gitea_sync_bg mocked out entirely (so these tests only ever assert *that*
it was called, with what args -- never what it does). _gitea_sync_run
(the function _gitea_sync_bg's own background thread actually calls) is
tested separately and directly, with subprocess.run mocked, to verify the
real fetch-outcome -> repo-map-update behavior the acceptance criteria
care about, without needing to deal with real threading/locking timing.

Run with:
    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-gitea-poll-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))
os.environ.setdefault("GITEA_ENABLED", "1")
os.environ.setdefault("GITEA_LABEL", "Gitea")
os.environ.setdefault("GITEA_PORT", "3124")

import app as appmod  # noqa: E402


class GiteaRepoMapTests(unittest.TestCase):
    """_load_gitea_repo_map / _save_gitea_repo_map_entry -- the tmp-file-
    then-os.replace() idiom, same shape as _save_desc_cache."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-repo-map-")
        self._orig = appmod.GITEA_REPO_MAP_FILE
        appmod.GITEA_REPO_MAP_FILE = os.path.join(self.tmp, "nested", "gitea-repo-map.json")

    def tearDown(self):
        appmod.GITEA_REPO_MAP_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_loads_as_empty_dict(self):
        self.assertEqual(appmod._load_gitea_repo_map(), {})

    def test_save_then_load_round_trips(self):
        appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main")
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m, {"admin/proj": {"name": "proj", "branch": "main",
                                            "sync_state": None, "sync_at": None,
                                            "remote_sha": None}})

    def test_save_creates_parent_directory(self):
        appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main")
        self.assertTrue(os.path.isfile(appmod.GITEA_REPO_MAP_FILE))

    def test_save_overwrites_only_the_named_entry(self):
        appmod._save_gitea_repo_map_entry("admin/one", "one", "main")
        appmod._save_gitea_repo_map_entry("admin/two", "two", "main")
        m = appmod._load_gitea_repo_map()
        self.assertEqual(set(m.keys()), {"admin/one", "admin/two"})

    def test_save_with_sync_fields_persists_them(self):
        appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main",
                                          sync_state="synced", sync_at=123.0,
                                          remote_sha="abc123")
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m["admin/proj"]["sync_state"], "synced")
        self.assertEqual(m["admin/proj"]["sync_at"], 123.0)
        self.assertEqual(m["admin/proj"]["remote_sha"], "abc123")

    def test_corrupt_file_loads_as_empty_dict_not_raise(self):
        os.makedirs(os.path.dirname(appmod.GITEA_REPO_MAP_FILE))
        with open(appmod.GITEA_REPO_MAP_FILE, "w") as f:
            f.write("not json{{{")
        self.assertEqual(appmod._load_gitea_repo_map(), {})


class GiteaPollIfDueTests(unittest.TestCase):
    """Throttling + gating (docs/spec.md "The poll mechanism")."""

    def setUp(self):
        self._orig_enabled = appmod.GITEA_ENABLED
        self._orig_last_at = appmod._gitea_poll_last_at
        self._orig_api = appmod._gitea_api
        self._orig_map_file = appmod.GITEA_REPO_MAP_FILE
        self.tmp = tempfile.mkdtemp(prefix="switchboard-poll-due-")
        appmod.GITEA_REPO_MAP_FILE = os.path.join(self.tmp, "gitea-repo-map.json")
        appmod.GITEA_ENABLED = True
        self.api_calls = []
        appmod._gitea_api = lambda method, path, body=None: \
            self.api_calls.append((method, path)) or (200, {"commit": {"id": "sha1"}})

    def tearDown(self):
        appmod.GITEA_ENABLED = self._orig_enabled
        appmod._gitea_poll_last_at = self._orig_last_at
        appmod._gitea_api = self._orig_api
        appmod.GITEA_REPO_MAP_FILE = self._orig_map_file
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_one_entry(self):
        appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main")

    def test_gitea_disabled_makes_no_calls_regardless_of_throttle(self):
        appmod.GITEA_ENABLED = False
        appmod._gitea_poll_last_at = 0.0
        self._seed_one_entry()
        appmod._gitea_poll_if_due(True)
        self.assertEqual(self.api_calls, [])

    def test_gitea_on_false_makes_no_calls(self):
        appmod._gitea_poll_last_at = 0.0
        self._seed_one_entry()
        appmod._gitea_poll_if_due(False)
        self.assertEqual(self.api_calls, [])

    def test_due_interval_polls_every_repo_map_entry_once(self):
        appmod._gitea_poll_last_at = 0.0
        appmod._save_gitea_repo_map_entry("admin/one", "one", "main")
        appmod._save_gitea_repo_map_entry("admin/two", "two", "main")
        appmod._gitea_poll_if_due(True)
        self.assertEqual(len(self.api_calls), 2)
        paths = {p for _, p in self.api_calls}
        self.assertEqual(paths, {"/repos/admin/one/branches/main",
                                 "/repos/admin/two/branches/main"})

    def test_not_yet_due_makes_no_additional_calls(self):
        appmod._gitea_poll_last_at = time.time()  # just polled
        self._seed_one_entry()
        appmod._gitea_poll_if_due(True)
        self.assertEqual(self.api_calls, [])

    def test_second_call_after_interval_elapses_polls_again(self):
        appmod._gitea_poll_last_at = time.time() - appmod.GITEA_POLL_INTERVAL_SECONDS - 1
        self._seed_one_entry()
        appmod._gitea_poll_if_due(True)
        self.assertEqual(len(self.api_calls), 1)

    def test_empty_repo_map_makes_no_calls_but_still_updates_last_at(self):
        appmod._gitea_poll_last_at = 0.0
        appmod._gitea_poll_if_due(True)
        self.assertEqual(self.api_calls, [])
        self.assertGreater(appmod._gitea_poll_last_at, 0.0)

    def test_one_malformed_entry_does_not_stop_the_rest_of_the_pass(self):
        # Regression for the reviewer-found bug: one entry raising must not
        # prevent every subsequent entry in the same pass from being polled.
        appmod._gitea_poll_last_at = 0.0
        appmod._save_gitea_repo_map_entry("admin/bad", "bad", "main")
        appmod._save_gitea_repo_map_entry("admin/good", "good", "main")

        def flaky_api(method, path, body=None):
            self.api_calls.append((method, path))
            if "admin/bad" in path:
                return 200, ["not", "a", "dict"]  # triggers the old AttributeError
            return 200, {"commit": {"id": "sha1"}}
        appmod._gitea_api = flaky_api

        appmod._gitea_poll_if_due(True)  # must not raise
        paths = {p for _, p in self.api_calls}
        self.assertEqual(paths, {"/repos/admin/bad/branches/main",
                                 "/repos/admin/good/branches/main"})


class GiteaPollOneTests(unittest.TestCase):
    """_gitea_poll_one's own SHA-compare/dispatch logic -- _gitea_sync_bg is
    mocked out entirely here (a separate class below tests what actually
    happens once dispatched)."""

    def setUp(self):
        self._orig_api = appmod._gitea_api
        self._orig_bg = appmod._gitea_sync_bg
        self.bg_calls = []
        appmod._gitea_sync_bg = lambda *a: self.bg_calls.append(a)

    def tearDown(self):
        appmod._gitea_api = self._orig_api
        appmod._gitea_sync_bg = self._orig_bg

    def _fake_api(self, status, resp_body):
        appmod._gitea_api = lambda method, path, body=None: (status, resp_body)

    def test_sha_matches_stored_remote_sha_no_dispatch(self):
        self._fake_api(200, {"commit": {"id": "same-sha"}})
        entry = {"name": "proj", "branch": "main", "remote_sha": "same-sha"}
        appmod._gitea_poll_one("admin/proj", entry)
        self.assertEqual(self.bg_calls, [])

    def test_sha_differs_dispatches_sync_with_right_args(self):
        self._fake_api(200, {"commit": {"id": "new-sha"}})
        entry = {"name": "proj", "branch": "main", "remote_sha": "old-sha"}
        appmod._gitea_poll_one("admin/proj", entry)
        self.assertEqual(self.bg_calls, [("proj", "main", "admin/proj", "new-sha")])

    def test_null_stored_sha_always_dispatches(self):
        self._fake_api(200, {"commit": {"id": "any-sha"}})
        entry = {"name": "proj", "branch": "main", "remote_sha": None}
        appmod._gitea_poll_one("admin/proj", entry)
        self.assertEqual(len(self.bg_calls), 1)

    def test_defaults_branch_to_main_when_entry_omits_it(self):
        captured = {}

        def fake(method, path, body=None):
            captured["path"] = path
            return 200, {"commit": {"id": "x"}}
        appmod._gitea_api = fake
        appmod._gitea_poll_one("admin/proj", {"name": "proj", "remote_sha": None})
        self.assertEqual(captured["path"], "/repos/admin/proj/branches/main")

    def test_non_200_status_skipped_without_raising(self):
        self._fake_api(404, {})
        entry = {"name": "proj", "branch": "main", "remote_sha": "old-sha"}
        appmod._gitea_poll_one("admin/proj", entry)  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_connection_error_skipped_without_raising(self):
        def raise_conn(method, path, body=None):
            raise ConnectionError("gitea unreachable")
        appmod._gitea_api = raise_conn
        entry = {"name": "proj", "branch": "main", "remote_sha": "old-sha"}
        appmod._gitea_poll_one("admin/proj", entry)  # must not raise
        self.assertEqual(self.bg_calls, [])

    def test_missing_commit_id_in_response_skipped(self):
        self._fake_api(200, {"commit": {}})
        entry = {"name": "proj", "branch": "main", "remote_sha": "old-sha"}
        appmod._gitea_poll_one("admin/proj", entry)
        self.assertEqual(self.bg_calls, [])

    def test_non_dict_200_response_skipped_without_raising(self):
        # Reviewer-found bug: a 200 whose body isn't a JSON object (e.g. a
        # bare list or string) used to raise AttributeError from
        # resp.get("commit"), which -- with no per-entry isolation in the
        # calling loop -- silently killed polling for every other project in
        # that pass, not just the malformed one.
        self._fake_api(200, ["not", "a", "dict"])
        entry = {"name": "proj", "branch": "main", "remote_sha": "old-sha"}
        appmod._gitea_poll_one("admin/proj", entry)  # must not raise
        self.assertEqual(self.bg_calls, [])


class GiteaSyncRunTests(unittest.TestCase):
    """_gitea_sync_run -- runs GITEA_SYNC_SCRIPT (mocked subprocess.run) and
    records the outcome in the repo-map. Called directly here (not through
    _gitea_sync_bg's own thread/lock spawn) so these assertions run
    synchronously and deterministically."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-sync-run-")
        self._orig_map_file = appmod.GITEA_REPO_MAP_FILE
        appmod.GITEA_REPO_MAP_FILE = os.path.join(self.tmp, "gitea-repo-map.json")
        self._orig_subprocess_run = subprocess.run
        self._orig_run_user = appmod.RUN_USER
        self._orig_script = appmod.GITEA_SYNC_SCRIPT

    def tearDown(self):
        appmod.GITEA_REPO_MAP_FILE = self._orig_map_file
        subprocess.run = self._orig_subprocess_run
        appmod.RUN_USER = self._orig_run_user
        appmod.GITEA_SYNC_SCRIPT = self._orig_script
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_subprocess(self, returncode, stdout):
        calls = []

        def _fake(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        subprocess.run = _fake
        return calls

    def test_synced_outcome_updates_repo_map(self):
        calls = self._fake_subprocess(0, "synced\n")
        appmod._gitea_sync_run("proj", "main", "admin/proj", "new-sha")
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m["admin/proj"]["sync_state"], "synced")
        self.assertEqual(m["admin/proj"]["remote_sha"], "new-sha")
        self.assertIsNotNone(m["admin/proj"]["sync_at"])
        cmd, kwargs = calls[0]
        self.assertEqual(cmd, ["sudo", "-u", appmod.RUN_USER, appmod.GITEA_SYNC_SCRIPT,
                               "proj", "main"])
        self.assertEqual(kwargs.get("timeout"), 60)

    def test_skipped_dirty_outcome_updates_repo_map_with_that_state(self):
        self._fake_subprocess(0, "skipped-dirty\n")
        appmod._gitea_sync_run("proj", "main", "admin/proj", "new-sha")
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m["admin/proj"]["sync_state"], "skipped-dirty")
        # remote_sha is still updated even on a skip -- so this same push
        # isn't re-attempted every subsequent interval (docs/spec.md).
        self.assertEqual(m["admin/proj"]["remote_sha"], "new-sha")

    def test_skipped_diverged_outcome_updates_repo_map_with_that_state(self):
        self._fake_subprocess(0, "skipped-diverged\n")
        appmod._gitea_sync_run("proj", "main", "admin/proj", "new-sha")
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m["admin/proj"]["sync_state"], "skipped-diverged")
        self.assertEqual(m["admin/proj"]["remote_sha"], "new-sha")

    def test_nonzero_exit_does_not_touch_repo_map(self):
        appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main",
                                          sync_state="synced", sync_at=1.0,
                                          remote_sha="old-sha")
        self._fake_subprocess(1, "")
        appmod._gitea_sync_run("proj", "main", "admin/proj", "new-sha")
        m = appmod._load_gitea_repo_map()
        # Untouched -- remote_sha stays "old-sha" so the next poll interval
        # still sees a diff and retries, rather than silently giving up.
        self.assertEqual(m["admin/proj"]["remote_sha"], "old-sha")
        self.assertEqual(m["admin/proj"]["sync_state"], "synced")

    def test_subprocess_raising_is_swallowed_and_does_not_touch_repo_map(self):
        appmod._save_gitea_repo_map_entry("admin/proj", "proj", "main",
                                          remote_sha="old-sha")

        def _raise(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)
        subprocess.run = _raise
        appmod._gitea_sync_run("proj", "main", "admin/proj", "new-sha")  # must not raise
        m = appmod._load_gitea_repo_map()
        self.assertEqual(m["admin/proj"]["remote_sha"], "old-sha")


class GiteaSyncBgConcurrencyTests(unittest.TestCase):
    """_gitea_sync_bg's per-project non-blocking lock (docs/spec.md
    "Concurrency") -- a second dispatch for the same owner_repo while one is
    still running is dropped, not queued."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-sync-bg-")
        self._orig_map_file = appmod.GITEA_REPO_MAP_FILE
        appmod.GITEA_REPO_MAP_FILE = os.path.join(self.tmp, "gitea-repo-map.json")
        self._orig_subprocess_run = subprocess.run

    def tearDown(self):
        appmod.GITEA_REPO_MAP_FILE = self._orig_map_file
        subprocess.run = self._orig_subprocess_run
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_dispatch_while_first_still_holds_the_lock_is_dropped(self):
        owner_repo = "admin/lockedproj"
        lock = appmod._gitea_sync_lock_for(owner_repo)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            calls = []
            subprocess.run = lambda cmd, **kw: calls.append(cmd) or \
                subprocess.CompletedProcess(cmd, 0, "synced\n", "")
            appmod._gitea_sync_bg("proj", "main", owner_repo, "sha1")
            time.sleep(0.05)  # give a (non-existent, since lock held) thread a chance
            self.assertEqual(calls, [])
        finally:
            lock.release()

    def test_lock_is_released_after_a_completed_sync_so_the_next_dispatch_can_run(self):
        owner_repo = "admin/freeproj"
        calls = []
        subprocess.run = lambda cmd, **kw: calls.append(cmd) or \
            subprocess.CompletedProcess(cmd, 0, "synced\n", "")
        appmod._gitea_sync_bg("proj", "main", owner_repo, "sha1")
        # Wait for the background thread to actually run and release the lock.
        for _ in range(50):
            if appmod._gitea_sync_lock_for(owner_repo).acquire(blocking=False):
                appmod._gitea_sync_lock_for(owner_repo).release()
                break
            time.sleep(0.02)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
