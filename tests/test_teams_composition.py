"""
Tests for roster & composition UI's backend half (sub-spec 6e --
docs/spec.md): teams.validate_composition(), teams.load_compositions(),
teams.save_composition(). Mirrors tests/test_teams_lead.py's RosterTests
setUp/tearDown shape (a scratch ENGINES_DIR, roster() re-read live) plus
tests/test_teams_grounding.py's tempfile-scratch-dir style for the
TEAM_STATE_DIR-backed persistence half.

Run with:
    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-composition-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import app as appmod  # noqa: E402
import teams as teamsmod  # noqa: E402


def _write_engine_file(dirpath, filename, body):
    with open(os.path.join(dirpath, filename), "w") as f:
        f.write(textwrap.dedent(body))


# ─── validate_composition() ────────────────────────────────────────────────
class ValidateCompositionTests(unittest.TestCase):
    """A roster of: 'lead2' (tier 2, healthy), 'broken2' (tier 2, misconfigured
    schema flag), 'prose3' (tier 3), plus an Ollama tier-1 entry -- enough
    combinations to exercise every rule in docs/spec.md 'Proposed approach'
    §1 without needing a real engine CLI anywhere."""

    def setUp(self):
        self.engines_dir = tempfile.mkdtemp(prefix="switchboard-composition-engines-")
        self._orig_engines_dir = appmod.ENGINES_DIR
        self._orig_base = teamsmod.TEAM_LLM_BASE_URL
        self._orig_model = teamsmod.TEAM_LLM_MODEL
        appmod.ENGINES_DIR = self.engines_dir
        teamsmod.TEAM_LLM_BASE_URL = "http://x:11434/v1"
        teamsmod.TEAM_LLM_MODEL = "qwen3:8b"
        _write_engine_file(self.engines_dir, "lead2.engine", """\
            LABEL=Lead2
            CMD=unused
            HEADLESS_CMD=lead2 -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema {schema}
            """)
        _write_engine_file(self.engines_dir, "broken2.engine", """\
            LABEL=Broken2
            CMD=unused
            HEADLESS_CMD=broken2 -p {resume} {schema}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            HEADLESS_SCHEMA_FLAG=--schema literally-hardcoded-no-placeholder
            """)
        _write_engine_file(self.engines_dir, "prose3.engine", """\
            LABEL=Prose3
            CMD=unused
            HEADLESS_CMD=prose3 -p {resume}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)
        _write_engine_file(self.engines_dir, "helper.engine", """\
            LABEL=Helper
            CMD=unused
            HEADLESS_CMD=helper -p {resume}
            HEADLESS_FORMAT=plain
            HEADLESS_PROMPT=arg
            """)

    def tearDown(self):
        appmod.ENGINES_DIR = self._orig_engines_dir
        teamsmod.TEAM_LLM_BASE_URL = self._orig_base
        teamsmod.TEAM_LLM_MODEL = self._orig_model
        shutil.rmtree(self.engines_dir, ignore_errors=True)

    def _members(self, *names):
        return [{"kind": "engine", "name": n} for n in names]

    def test_valid_composition_returns_none(self):
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "lead2"}, self._members("helper"))
        self.assertIsNone(err)

    def test_valid_ollama_lead_returns_none(self):
        err = teamsmod.validate_composition(
            {"kind": "ollama", "name": "qwen3:8b"}, self._members("helper"))
        self.assertIsNone(err)

    def test_tier3_lead_allowed_never_blocked(self):
        # docs/spec.md goal: tier 3 "is a real option, not a token one" --
        # the ONE place a naive validator might wrongly add a tier check.
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "prose3"}, self._members("helper"))
        self.assertIsNone(err)

    def test_missing_lead_rejected(self):
        err = teamsmod.validate_composition(None, self._members("helper"))
        self.assertIsNotNone(err)
        err2 = teamsmod.validate_composition({}, self._members("helper"))
        self.assertIsNotNone(err2)

    def test_unknown_lead_name_rejected_naming_it(self):
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "nosuchengine"}, self._members("helper"))
        self.assertIsNotNone(err)
        self.assertIn("nosuchengine", err)

    def test_lead_kind_ollama_but_no_ollama_configured_rejected(self):
        # Edge case: Ollama named as lead but TEAM_LLM_BASE_URL/MODEL unset
        # since the composition was saved -- roster() simply won't contain
        # an "ollama" entry to match against.
        teamsmod.TEAM_LLM_BASE_URL = None
        teamsmod.TEAM_LLM_MODEL = None
        err = teamsmod.validate_composition(
            {"kind": "ollama", "name": "qwen3:8b"}, self._members("helper"))
        self.assertIsNotNone(err)

    def test_misconfigured_tier2_lead_rejected_with_schema_reason(self):
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "broken2"}, self._members("helper"))
        self.assertIsNotNone(err)
        self.assertIn("broken2", err)

    def test_empty_members_rejected(self):
        err = teamsmod.validate_composition({"kind": "engine", "name": "lead2"}, [])
        self.assertIsNotNone(err)

    def test_duplicate_member_rejected(self):
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "lead2"}, self._members("helper", "helper"))
        self.assertIsNotNone(err)

    def test_unknown_member_rejected_naming_it(self):
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "lead2"}, self._members("nosuchengine"))
        self.assertIsNotNone(err)
        self.assertIn("nosuchengine", err)

    def test_ollama_named_as_a_member_rejected_not_delegate_capable(self):
        # The Ollama roster entry is never delegate_capable -- naming it as
        # a teammate must be rejected, not silently accepted/ignored.
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "lead2"}, self._members("qwen3:8b"))
        self.assertIsNotNone(err)

    def test_lead_also_in_members_rejected(self):
        err = teamsmod.validate_composition(
            {"kind": "engine", "name": "lead2"}, self._members("lead2", "helper"))
        self.assertIsNotNone(err)

    def test_ollama_lead_may_have_every_engine_as_member_none_excluded(self):
        # An Ollama lead isn't itself an engines.d entry, so nothing is
        # excluded from members purely because it's the lead.
        err = teamsmod.validate_composition(
            {"kind": "ollama", "name": "qwen3:8b"}, self._members("lead2", "helper"))
        self.assertIsNone(err)


# ─── load_compositions() / save_composition() ──────────────────────────────
class CompositionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="switchboard-composition-state-")
        self._orig_state_dir = teamsmod.TEAM_STATE_DIR
        teamsmod.TEAM_STATE_DIR = self.state_dir

    def tearDown(self):
        teamsmod.TEAM_STATE_DIR = self._orig_state_dir
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_load_compositions_missing_file_returns_empty_dict(self):
        self.assertEqual(teamsmod.load_compositions(), {})

    def test_load_compositions_corrupt_file_returns_empty_dict_not_500(self):
        path = teamsmod._compositions_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{ not valid json")
        self.assertEqual(teamsmod.load_compositions(), {})

    def test_save_then_load_round_trips(self):
        teamsmod.save_composition(
            "proj", {"kind": "engine", "name": "lead2"},
            [{"kind": "engine", "name": "helper"}, {"kind": "engine", "name": "aider"}])
        comps = teamsmod.load_compositions()
        self.assertIn("proj", comps)
        entry = comps["proj"]
        self.assertEqual(entry["lead"], {"kind": "engine", "name": "lead2"})
        self.assertEqual(sorted(entry["members"]), ["aider", "helper"])
        self.assertIn("saved_at", entry)

    def test_save_composition_strips_tier_and_schema_flag_error_from_lead(self):
        # docs/spec.md: "Stores only kind+name for lead (never tier/
        # schema_flag_error) -- those are always re-derived live from
        # roster() at read time."
        teamsmod.save_composition(
            "proj", {"kind": "engine", "name": "lead2", "tier": 2, "schema_flag_error": None},
            [{"kind": "engine", "name": "helper"}])
        entry = teamsmod.load_compositions()["proj"]
        self.assertEqual(entry["lead"], {"kind": "engine", "name": "lead2"})

    def test_save_composition_upserts_one_project_leaves_others_untouched(self):
        teamsmod.save_composition("proj1", {"kind": "engine", "name": "a"},
                                  [{"kind": "engine", "name": "b"}])
        teamsmod.save_composition("proj2", {"kind": "engine", "name": "c"},
                                  [{"kind": "engine", "name": "d"}])
        teamsmod.save_composition("proj1", {"kind": "engine", "name": "x"},
                                  [{"kind": "engine", "name": "y"}])
        comps = teamsmod.load_compositions()
        self.assertEqual(comps["proj1"]["lead"], {"kind": "engine", "name": "x"})
        self.assertEqual(comps["proj2"]["lead"], {"kind": "engine", "name": "c"})

    def test_atomic_write_no_tmp_file_left_behind(self):
        teamsmod.save_composition("proj", {"kind": "engine", "name": "a"},
                                  [{"kind": "engine", "name": "b"}])
        path = teamsmod._compositions_path()
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(os.path.isfile(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
