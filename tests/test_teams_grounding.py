"""
Tests for grounding: discovery, digest, and fact_check (backlog item 6,
sub-spec 6b -- docs/spec.md). Follows tests/test_teams_headless.py's shape
(sys.path.insert/os.environ.setdefault-before-import, plain unittest).

Tier 1 -- pure unit, no disk I/O at all: build_digest(), _extract_headings(),
_significant_terms(), fact_check() fed synthetic structures.

Tier 2 -- real filesystem: this repo's own working tree used directly as
the primary fixture (real docs/ARCHITECTURE.md, docs/BACKLOG.md (20,088
real bytes), README.md, and no CLAUDE.md/AGENTS.md -- exercises the real
skip path with zero synthesized content); tempfile-based scratch project
trees for every adversarial-shape edge case (out-of-bounds symlink,
symlink loop, directory-instead-of-file, binary file, malformed UTF-8, a
sparse 200MB file).

Read-only assertions: a runtime monkeypatch of builtins.open (rejects any
non-read mode) plus every mutating os/shutil function (raises if called),
run across the full public surface against Tier 2 fixtures; a static
ast.parse() scan of app/teams.py's grounding-section function defs,
independent of what the runtime tests happened to exercise.

CLI: both new subcommands run directly against a real project directory.

Run with:
    python3 -m unittest discover -s tests -v
"""
import ast
import builtins
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

_TMP_ROOT = tempfile.mkdtemp(prefix="switchboard-teams-grounding-test-")
os.environ.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("AUTH_MODE", "simple")
os.environ.setdefault("SIMPLE_USERNAME", "testuser")
os.environ.setdefault("SIMPLE_PASSWORD", "testpass")
os.environ.setdefault("PROJECTS_DIR", os.path.join(_TMP_ROOT, "projects"))
os.environ.setdefault("UPLOAD_STAGING_DIR", os.path.join(_TMP_ROOT, "uploads"))

import teams as teamsmod  # noqa: E402

discover_grounding_files = teamsmod.discover_grounding_files
load_grounding = teamsmod.load_grounding
build_digest = teamsmod.build_digest
fact_check = teamsmod.fact_check
_extract_headings = teamsmod._extract_headings
_significant_terms = teamsmod._significant_terms


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


@contextlib.contextmanager
def _guard_os_open(*forbidden_paths):
    """Fails the test if os.open() -- the syscall-level call the grounding
    section actually uses (post-review fix, docs/test-review.md Defect 1/2)
    -- is ever invoked on any of `forbidden_paths`. Restores the real
    os.open() on exit regardless of outcome."""
    real_os_open = os.open
    forbidden_abs = {os.path.abspath(p) for p in forbidden_paths}

    def _guarded(path, flags, *a, **kw):
        if os.path.abspath(path) in forbidden_abs:
            raise AssertionError(f"os.open() was called on a forbidden path: {path}")
        return real_os_open(path, flags, *a, **kw)

    os.open = _guarded
    try:
        yield
    finally:
        os.open = real_os_open


def _sf(label, content, path="/fake/path", relpath=None):
    """Builds one synthetic `files` entry, the shape load_grounding()
    produces, without touching disk -- for Tier 1 pure tests."""
    return {
        "label": label,
        "path": path,
        "relpath": relpath or label,
        "headings": _extract_headings(content),
        "content": content,
        "byte_count": len(content.encode("utf-8")),
    }


def _synthetic_grounding(files):
    return {"workdir": "/fake", "loaded_at": "2026-01-01T00:00:00Z",
            "files": files, "digest": build_digest(files), "empty": files == []}


# ─── Tier 1: build_digest() -- pure, no disk I/O ──────────────────────────
class BuildDigestTests(unittest.TestCase):
    def test_no_files_returns_the_sentinel_verbatim(self):
        self.assertEqual(build_digest([]), teamsmod._GROUNDING_NO_FILES_DIGEST)

    def test_no_files_sentinel_is_returned_regardless_of_max_bytes(self):
        self.assertEqual(build_digest([], max_bytes=0), teamsmod._GROUNDING_NO_FILES_DIGEST)
        self.assertEqual(build_digest([], max_bytes=-5), teamsmod._GROUNDING_NO_FILES_DIGEST)

    def test_zero_max_bytes_with_files_returns_empty_string_not_exception(self):
        files = [_sf("README.md", "# Hello\n\nSome body text.")]
        self.assertEqual(build_digest(files, max_bytes=0), "")

    def test_negative_max_bytes_returns_empty_string(self):
        files = [_sf("README.md", "# Hello\n\nSome body text.")]
        self.assertEqual(build_digest(files, max_bytes=-100), "")

    def test_single_file_digest_contains_label_and_heading(self):
        files = [_sf("README.md", "# Hello\n\nSome body text here.")]
        digest = build_digest(files, max_bytes=8000)
        self.assertIn("README.md", digest)
        self.assertIn("# Hello", digest)
        self.assertIn("Some body text here.", digest)

    def test_digest_never_exceeds_max_bytes_small_cap_one_file(self):
        big = "word " * 5000  # ~25000 bytes, way over any small cap
        files = [_sf("docs/BACKLOG.md", big)]
        for cap in (50, 200, 500, 1000):
            digest = build_digest(files, max_bytes=cap)
            self.assertLessEqual(len(digest.encode("utf-8")), cap)

    def test_digest_never_exceeds_max_bytes_four_files_uneven_sizes(self):
        files = [
            _sf("docs/ARCHITECTURE.md", "# A\n" + "alpha " * 3000),
            _sf("docs/BACKLOG.md", "# B\n" + "beta " * 3000),
            _sf("CLAUDE.md", "gamma"),  # tiny, well under its own fair share
            _sf("README.md", "# R\n" + "delta " * 3000),
        ]
        for cap in (500, 2000, 8000):
            digest = build_digest(files, max_bytes=cap)
            self.assertLessEqual(len(digest.encode("utf-8")), cap)

    def test_per_file_budget_is_a_fairness_heuristic_not_a_hard_per_file_cap(self):
        # The unconditional final truncation is the actual guarantee -- it
        # must hold even when one file's content dwarfs the others (the
        # exact "per-file math wrong/changed later" case the spec calls
        # out as not being what the cap actually depends on).
        files = [
            _sf("docs/BACKLOG.md", "z " * 100000),  # ~200KB, way oversized
            _sf("README.md", "short"),
        ]
        digest = build_digest(files, max_bytes=300)
        self.assertLessEqual(len(digest.encode("utf-8")), 300)

    def test_digest_is_a_str_not_bytes(self):
        files = [_sf("README.md", "hello")]
        self.assertIsInstance(build_digest(files), str)

    def test_truncation_cleanly_drops_a_trailing_partial_multibyte_sequence(self):
        # A multi-byte UTF-8 character (e.g. an emoji or accented letter)
        # placed right at a truncation boundary must not raise or leave a
        # decode error -- errors="ignore" on the final decode step handles
        # this (docs/spec.md 6b §4).
        files = [_sf("README.md", "x" * 49 + "café " * 200)]
        for cap in range(48, 60):
            digest = build_digest(files, max_bytes=cap)  # must not raise
            self.assertLessEqual(len(digest.encode("utf-8")), cap)


# ─── Tier 1: _extract_headings() -- pure ──────────────────────────────────
class ExtractHeadingsTests(unittest.TestCase):
    def test_basic_atx_headings_extracted_in_order(self):
        content = "# Title\n\nSome text.\n\n## Subsection\n\nMore text.\n"
        self.assertEqual(_extract_headings(content), ["# Title", "## Subsection"])

    def test_non_heading_hash_usage_not_extracted(self):
        content = "text #not-a-heading here\n#also-not-one-no-space\n"
        self.assertEqual(_extract_headings(content), [])

    def test_heading_inside_fenced_code_block_backtick_style_is_skipped(self):
        content = (
            "# Real Heading\n\n"
            "```bash\n"
            "#!/bin/bash\n"
            "# a shell comment that looks like a heading\n"
            "```\n\n"
            "## Another Real Heading\n"
        )
        self.assertEqual(_extract_headings(content), ["# Real Heading", "## Another Real Heading"])

    def test_heading_inside_fenced_code_block_tilde_style_is_skipped(self):
        content = "# Real\n\n~~~\n# fake\n~~~\n\n## Also Real\n"
        self.assertEqual(_extract_headings(content), ["# Real", "## Also Real"])

    def test_headings_capped_at_max_per_file(self):
        content = "\n".join(f"# Heading {i}" for i in range(50))
        headings = _extract_headings(content)
        self.assertEqual(len(headings), teamsmod._GROUNDING_MAX_HEADINGS_PER_FILE)
        self.assertEqual(headings[0], "# Heading 0")

    def test_empty_content_returns_empty_list(self):
        self.assertEqual(_extract_headings(""), [])


# ─── Tier 1: _significant_terms() -- pure ─────────────────────────────────
class SignificantTermsTests(unittest.TestCase):
    def test_empty_string_returns_empty_list(self):
        self.assertEqual(_significant_terms(""), [])

    def test_whitespace_only_returns_empty_list(self):
        self.assertEqual(_significant_terms("   \n\t  "), [])

    def test_all_stopword_claim_returns_empty_list(self):
        self.assertEqual(_significant_terms("the is a of in on and or"), [])

    def test_mixed_case_and_punctuation_normalized(self):
        terms = _significant_terms("The Engine Config-File, is READY!")
        self.assertIn("engine", terms)
        self.assertIn("config", terms)
        self.assertIn("file", terms)
        self.assertIn("ready", terms)
        self.assertNotIn("the", terms)
        self.assertNotIn("is", terms)

    def test_apostrophe_is_kept_as_part_of_a_token_per_the_token_pattern(self):
        # Spec's tokenizer pattern is [A-Za-z0-9_']+ -- an apostrophe is a
        # token character, not a splitter, so "engine's" is one token.
        terms = _significant_terms("the engine's config")
        self.assertIn("engine's", terms)
        self.assertIn("config", terms)

    def test_single_character_tokens_dropped(self):
        terms = _significant_terms("a x b engine")
        self.assertEqual(terms, ["engine"])


# ─── Tier 1: fact_check() against synthetic grounding ─────────────────────
class FactCheckPureTests(unittest.TestCase):
    def test_full_conjunctive_match_found(self):
        files = [_sf("README.md", "Setup instructions live in the install script.")]
        g = _synthetic_grounding(files)
        result = fact_check("install script setup", g)
        self.assertTrue(result["found"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["file_line"], "README.md:1")
        self.assertEqual(result["matches"][0]["line"], 1)

    def test_partial_term_overlap_on_every_line_is_not_a_match(self):
        # "setup" and "database" never appear together on one line -- no
        # nearest-weak-match fallback should surface either line.
        files = [_sf("README.md", "Setup instructions live here.\nThe database lives elsewhere.")]
        g = _synthetic_grounding(files)
        result = fact_check("setup database", g)
        self.assertFalse(result["found"])
        self.assertEqual(result["matches"], [])

    def test_empty_claim_found_false_no_exception(self):
        g = _synthetic_grounding([_sf("README.md", "anything at all")])
        result = fact_check("", g)
        self.assertFalse(result["found"])
        self.assertEqual(result["matches"], [])

    def test_whitespace_only_claim_found_false(self):
        g = _synthetic_grounding([_sf("README.md", "anything at all")])
        result = fact_check("   \n  ", g)
        self.assertFalse(result["found"])

    def test_all_stopword_claim_found_false(self):
        g = _synthetic_grounding([_sf("README.md", "the cat sat on the mat")])
        result = fact_check("the on and or", g)
        self.assertFalse(result["found"])

    def test_empty_grounding_found_false_no_exception(self):
        g = _synthetic_grounding([])
        self.assertTrue(g["empty"])
        result = fact_check("anything", g)
        self.assertFalse(result["found"])
        self.assertEqual(result["matches"], [])

    def test_matches_capped_at_max_matches(self):
        # 6b.1 (docs/spec.md): fact_check() now counts and caps BLOCKS, not
        # lines -- "max_matches capping behaviour is unchanged. Blocks, not
        # lines, are counted." A blank line between each synthetic "line"
        # keeps every one its own block (undoing this would collapse them
        # into far fewer than 20 blocks under _GROUNDING_BLOCK_MAX_LINES,
        # which is the fixture's own intent -- proving the cap, not the
        # block-splitting bounds).
        content = "\n\n".join(f"the target term appears on line {i}" for i in range(20))
        files = [_sf("README.md", content)]
        g = _synthetic_grounding(files)
        result = fact_check("target term appears", g, max_matches=3)
        self.assertEqual(len(result["matches"]), 3)

    def test_matching_is_case_insensitive(self):
        files = [_sf("README.md", "The QUICK Brown Fox")]
        g = _synthetic_grounding(files)
        result = fact_check("quick brown", g)
        self.assertTrue(result["found"])

    def test_claim_full_of_regex_metacharacters_is_never_treated_as_a_pattern(self):
        files = [_sf("README.md", "some ordinary text with no special meaning")]
        g = _synthetic_grounding(files)
        # Must not raise re.error and must not accidentally match everything.
        result = fact_check(".*(.+)[a-z]{1,1000}$$$", g)
        self.assertFalse(result["found"])

    def test_result_shape_has_claim_found_matches_keys(self):
        g = _synthetic_grounding([])
        result = fact_check("x", g)
        self.assertEqual(set(result.keys()), {"claim", "found", "matches"})
        self.assertEqual(result["claim"], "x")


# ─── Tier 2: real filesystem -- this repo's own tree ──────────────────────
class DiscoverThisRepoTests(unittest.TestCase):
    def test_discovers_architecture_backlog_readme_no_claude_or_agents(self):
        entries = discover_grounding_files(REPO_ROOT)
        labels = [e["label"] for e in entries]
        self.assertEqual(labels, ["docs/ARCHITECTURE.md", "docs/BACKLOG.md", "README.md"])
        for e in entries:
            self.assertTrue(os.path.isfile(e["path"]))

    def test_load_grounding_against_this_repo_is_non_empty(self):
        g = load_grounding(REPO_ROOT)
        self.assertFalse(g["empty"])
        self.assertEqual(len(g["files"]), 3)
        self.assertNotEqual(g["digest"], teamsmod._GROUNDING_NO_FILES_DIGEST)

    def test_backlog_real_20kb_digest_capped_at_default_and_tiny_override(self):
        g_default = load_grounding(REPO_ROOT)
        g_tiny = load_grounding(REPO_ROOT, max_bytes=500)
        self.assertLessEqual(len(g_default["digest"].encode("utf-8")), teamsmod.TEAM_GROUNDING_MAX_BYTES)
        self.assertLessEqual(len(g_tiny["digest"].encode("utf-8")), 500)
        backlog = next(f for f in g_default["files"] if f["label"] == "docs/BACKLOG.md")
        self.assertGreater(backlog["byte_count"], 20000)  # the real, unbounded-by-digest content

    def test_fact_check_real_claim_against_this_repos_own_architecture_md(self):
        # Independently verify the exact line first, straight from disk.
        arch_path = os.path.join(REPO_ROOT, "docs", "ARCHITECTURE.md")
        with open(arch_path) as f:
            lines = f.readlines()
        target_line_no = None
        for i, line in enumerate(lines, start=1):
            if "not an instant path to" in line:
                target_line_no = i
                break
        self.assertIsNotNone(target_line_no, "fixture assumption changed -- update this test's claim")

        g = load_grounding(REPO_ROOT)
        result = fact_check(
            "Nothing else. A bug in this stdlib-only app is not an instant path", g)
        self.assertTrue(result["found"])
        hit = next(m for m in result["matches"] if m["label"] == "docs/ARCHITECTURE.md")
        self.assertEqual(hit["line"], target_line_no)
        self.assertEqual(hit["file_line"], f"docs/ARCHITECTURE.md:{target_line_no}")

    def test_fact_check_finds_passage_present_in_full_content_but_truncated_out_of_digest(self):
        backlog_path = os.path.join(REPO_ROOT, "docs", "BACKLOG.md")
        with open(backlog_path) as f:
            lines = f.readlines()
        target_line_no = None
        claim = "URLs get surfaced in the UI once a project can have more than one"
        for i, line in enumerate(lines, start=1):
            if "URLs get surfaced in the UI" in line:
                target_line_no = i
                break
        self.assertIsNotNone(target_line_no, "fixture assumption changed -- update this test's claim")

        g_tiny = load_grounding(REPO_ROOT, max_bytes=300)  # digest definitely doesn't reach line 343
        backlog_digest_only = g_tiny["digest"]
        self.assertNotIn("surfaced in the UI", backlog_digest_only)

        result = fact_check(claim, g_tiny)  # fact_check searches full content, not the (tiny) digest
        self.assertTrue(result["found"])
        hit = next(m for m in result["matches"] if m["label"] == "docs/BACKLOG.md")
        # 6b.1 (docs/spec.md "Result shape"): line/file_line now point at
        # the matching BLOCK's first line, not the exact physical line the
        # claim's own text happens to sit on -- this claim's support spans
        # the whole wrapped paragraph (lines 337-343 as of this writing),
        # so the match correctly reports where that paragraph begins.
        # Computed here the same way fact_check() itself does, rather than
        # hardcoding a line number that would go stale on any edit to this
        # paragraph.
        backlog_content = next(f["content"] for f in g_tiny["files"] if f["label"] == "docs/BACKLOG.md")
        enclosing_block = next(b for b in teamsmod._iter_grounding_blocks(backlog_content)
                               if b["start_line"] <= target_line_no <= b["end_line"])
        self.assertEqual(hit["line"], enclosing_block["start_line"])
        self.assertEqual(hit["end_line"], enclosing_block["end_line"])

    def test_docs_subdirectory_missing_entirely_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No docs/ subdirectory at all -- os.path.join on the missing
            # intermediate directory must still be handled cleanly.
            entries = discover_grounding_files(tmp)
            self.assertEqual(entries, [])
            g = load_grounding(tmp)
            self.assertTrue(g["empty"])
            self.assertEqual(g["digest"], teamsmod._GROUNDING_NO_FILES_DIGEST)


# ─── Tier 2: real filesystem -- constructed scratch projects ──────────────
class DiscoverScratchProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-grounding-scratch-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_grounding_files_at_all_still_produces_usable_empty_result(self):
        entries = discover_grounding_files(self.tmp)
        self.assertEqual(entries, [])
        g = load_grounding(self.tmp)
        self.assertEqual(g["files"], [])
        self.assertTrue(g["empty"])
        self.assertEqual(g["digest"], teamsmod._GROUNDING_NO_FILES_DIGEST)
        self.assertNotEqual(g["digest"], "")
        self.assertIsNotNone(g["digest"])

    def test_claude_md_at_indirection_resolves_to_real_in_bounds_target(self):
        _write(os.path.join(self.tmp, "docs", "OTHER.md"), "# Other\n\nReal content lives here.")
        _write(os.path.join(self.tmp, "CLAUDE.md"), "@docs/OTHER.md")
        entries = discover_grounding_files(self.tmp)
        claude_entries = [e for e in entries if e["label"] == "CLAUDE.md"]
        self.assertEqual(len(claude_entries), 1)
        expected_target = os.path.join(self.tmp, "docs", "OTHER.md")
        self.assertEqual(claude_entries[0]["path"], expected_target)

    def test_claude_md_at_indirection_out_of_bounds_is_skipped_and_never_opened(self):
        # Grounding candidates are now opened via os.open() (post-review
        # fix, docs/test-review.md Defect 1/2), not builtins.open() -- the
        # "never opened" guard must wrap the function actually called.
        outside_dir = tempfile.mkdtemp(prefix="switchboard-grounding-outside-")
        try:
            outside_path = os.path.join(outside_dir, "secret.txt")
            _write(outside_path, "top secret host content")
            rel = os.path.relpath(outside_path, self.tmp)
            _write(os.path.join(self.tmp, "CLAUDE.md"), f"@{rel}")

            with _guard_os_open(outside_path):
                entries = discover_grounding_files(self.tmp)

            claude_entries = [e for e in entries if e["label"] == "CLAUDE.md"]
            self.assertEqual(claude_entries, [])
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_claude_and_agents_both_present_claude_wins(self):
        _write(os.path.join(self.tmp, "CLAUDE.md"), "# Claude instructions")
        _write(os.path.join(self.tmp, "AGENTS.md"), "# Agents instructions")
        entries = discover_grounding_files(self.tmp)
        labels = [e["label"] for e in entries]
        self.assertIn("CLAUDE.md", labels)
        self.assertNotIn("AGENTS.md", labels)

    def test_real_symlink_resolving_outside_workdir_is_skipped_never_opened(self):
        outside_dir = tempfile.mkdtemp(prefix="switchboard-grounding-outside-")
        try:
            outside_path = os.path.join(outside_dir, "hostfile.txt")
            _write(outside_path, "arbitrary host content that must never leak")
            link_path = os.path.join(self.tmp, "README.md")
            os.symlink(outside_path, link_path)

            with _guard_os_open(outside_path):
                entries = discover_grounding_files(self.tmp)
                g = load_grounding(self.tmp)

            self.assertEqual([e for e in entries if e["label"] == "README.md"], [])
            self.assertTrue(g["empty"])
            self.assertNotIn("arbitrary host content", g["digest"])
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_in_bounds_symlink_candidate_is_now_categorically_unusable(self):
        # Deliberate post-review behavior change (docs/test-review.md
        # Defect 2's fix): O_NOFOLLOW rejects ANY final-path-component
        # symlink outright, including one that legitimately resolves
        # in-bounds -- not just an out-of-bounds one. This is what makes
        # the fd-based containment check airtight against a same-path
        # swap-for-a-symlink (see the TOCTOU regression test below); the
        # tradeoff is that a real filesystem symlink is no longer usable as
        # grounding input at all, even a harmless in-bounds one. Not
        # required by any spec acceptance criterion (none tests an
        # in-bounds symlink being followed), so this narrows accepted input
        # shape without violating anything the spec pins down.
        _write(os.path.join(self.tmp, "docs", "REAL_README.md"), "# Real\n\nActual content.")
        os.symlink(os.path.join(self.tmp, "docs", "REAL_README.md"),
                   os.path.join(self.tmp, "README.md"))
        entries = discover_grounding_files(self.tmp)
        self.assertEqual([e for e in entries if e["label"] == "README.md"], [])
        g = load_grounding(self.tmp)
        self.assertTrue(g["empty"])

    def test_symlink_loop_is_skipped_no_exception(self):
        a = os.path.join(self.tmp, "a_link")
        b = os.path.join(self.tmp, "README.md")
        os.symlink(b, a)
        os.symlink(a, b)
        entries = discover_grounding_files(self.tmp)  # must not raise
        self.assertEqual(entries, [])
        g = load_grounding(self.tmp)  # must not raise
        self.assertTrue(g["empty"])

    def test_directory_instead_of_file_is_skipped_no_exception(self):
        os.makedirs(os.path.join(self.tmp, "docs"))
        os.makedirs(os.path.join(self.tmp, "docs", "ARCHITECTURE.md"))  # a dir, same name
        entries = discover_grounding_files(self.tmp)  # must not raise
        self.assertEqual([e for e in entries if e["label"] == "docs/ARCHITECTURE.md"], [])

    def test_binary_file_named_readme_is_skipped_no_exception(self):
        binary_bytes = bytes([0, 1, 2, 3, 0, 255, 254] * 100)
        with open(os.path.join(self.tmp, "README.md"), "wb") as f:
            f.write(binary_bytes)
        entries = discover_grounding_files(self.tmp)  # must not raise
        self.assertEqual([e for e in entries if e["label"] == "README.md"], [])
        g = load_grounding(self.tmp)
        self.assertTrue(g["empty"])
        result = fact_check("anything", g)
        self.assertFalse(result["found"])

    def test_malformed_utf8_mixed_with_valid_text_still_usable(self):
        path = os.path.join(self.tmp, "README.md")
        with open(path, "wb") as f:
            f.write(b"# Valid Heading\n\nSome valid text before ")
            f.write(b"\xff\xfe invalid bytes here \xc3\x28")  # genuinely invalid UTF-8
            f.write(b" and valid text after.\n")
        entries = discover_grounding_files(self.tmp)
        self.assertEqual(len(entries), 1)  # not skipped -- not a NUL-byte binary file
        g = load_grounding(self.tmp)  # must not raise
        self.assertFalse(g["empty"])
        f = g["files"][0]
        self.assertIn("Some valid text before", f["content"])
        self.assertIn("and valid text after.", f["content"])
        self.assertIn("# Valid Heading", f["headings"])

    def test_sparse_200mb_file_read_cap_bounded_and_fast(self):
        path = os.path.join(self.tmp, "README.md")
        total_size = 200 * 1024 * 1024
        # Padded well past the 512-byte binary-sniff window with real,
        # non-NUL text -- the hole (implicit zero bytes = NUL) only starts
        # after this, past the sniff window, so the file is correctly
        # classified as non-binary before the bounded read is ever bounded
        # by anything other than _GROUNDING_READ_CAP_BYTES itself.
        head_text = "# Sparse Fixture\n\n" + ("Real text at the start of the file. " * 30) + "\n"
        self.assertGreater(len(head_text), 512)
        with open(path, "wb") as f:
            f.write(head_text.encode("utf-8"))
            f.seek(total_size - 1)
            f.write(b"\x00")  # forces the file's real size without writing the middle
        self.assertEqual(os.path.getsize(path), total_size)

        start = time.time()
        g = load_grounding(self.tmp)
        elapsed = time.time() - start

        self.assertFalse(g["empty"])
        content = g["files"][0]["content"]
        self.assertLessEqual(len(content.encode("utf-8")), teamsmod._GROUNDING_READ_CAP_BYTES)
        self.assertLess(elapsed, 10.0, "load_grounding() should bound the read, not read the whole 200MB file")

    def test_load_grounding_snapshot_does_not_see_a_later_edit(self):
        path = os.path.join(self.tmp, "README.md")
        _write(path, "# Version One\n\nOriginal content.")
        g1 = load_grounding(self.tmp)
        self.assertIn("Original content.", g1["files"][0]["content"])

        _write(path, "# Version Two\n\nChanged content, should not appear in g1.")

        # g1's own snapshot is untouched by the on-disk edit.
        self.assertIn("Original content.", g1["files"][0]["content"])
        self.assertNotIn("Changed content", g1["files"][0]["content"])

        g2 = load_grounding(self.tmp)  # a fresh call does see the change
        self.assertIn("Changed content", g2["files"][0]["content"])


# ─── Post-review regression tests (docs/test-review.md, round 1) ─────────
class PostReviewRegressionTests(unittest.TestCase):
    """Regression tests for the two round-1 blocking defects (a real named
    pipe, an actual mid-flight symlink swap -- not simulated) plus the two
    non-blocking should-fix items found in the same pass."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-grounding-postreview-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defect1_named_pipe_at_candidate_path_does_not_hang(self):
        # docs/test-review.md Defect 1: os.mkfifo() with no writer ever
        # connecting used to hang open()/_looks_binary() forever. Run in a
        # background thread with a bounded join() -- the reviewer's own
        # verification technique -- so a regression fails the test cleanly
        # instead of hanging the whole test process.
        fifo_path = os.path.join(self.tmp, "README.md")
        os.mkfifo(fifo_path)

        result = {}

        def _call():
            result["entries"] = discover_grounding_files(self.tmp)
            result["grounding"] = load_grounding(self.tmp)

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "discover_grounding_files()/load_grounding() hung on a FIFO")
        self.assertEqual([e for e in result["entries"] if e["label"] == "README.md"], [])
        self.assertTrue(result["grounding"]["empty"])

    def test_defect1_fifo_at_docs_architecture_md_does_not_hang(self):
        # The other three candidate slots go through the exact same
        # _open_grounding_candidate() codepath -- confirm at least one more
        # of them directly rather than assuming README.md's coverage
        # generalizes.
        os.makedirs(os.path.join(self.tmp, "docs"))
        os.mkfifo(os.path.join(self.tmp, "docs", "ARCHITECTURE.md"))

        result = {}

        def _call():
            result["grounding"] = load_grounding(self.tmp)

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "load_grounding() hung on a FIFO at docs/ARCHITECTURE.md")
        self.assertTrue(result["grounding"]["empty"])

    def test_defect1_fifo_via_real_cli_subprocess_bounded(self):
        # The reviewer's own exact reproduction shape: through the real
        # CLI subprocess, with a hard external timeout as the backstop in
        # case the in-process thread-join technique above were ever itself
        # somehow insufficient.
        os.mkfifo(os.path.join(self.tmp, "README.md"))
        result = subprocess.run(
            [sys.executable, os.path.join(APP_DIR, "teams.py"), "grounding", self.tmp],
            capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["empty"])

    def test_defect2_symlink_swap_in_the_open_window_does_not_leak_outside_content(self):
        # docs/test-review.md Defect 2: an in-bounds regular file swapped
        # for a symlink to an out-of-bounds target, landing exactly in the
        # window between _open_grounding_candidate()'s own cheap
        # _under_workdir() pre-check and its os.open() call. This forces
        # the real race (not a simulation of "two separate reads far
        # apart") by monkeypatching os.open() itself to perform the swap as
        # a side effect immediately before delegating to the real os.open()
        # -- reproducing the exact window the fix (O_NOFOLLOW, tied to a
        # single open+fstat) has to close.
        outside_dir = tempfile.mkdtemp(prefix="switchboard-grounding-outside-")
        try:
            outside_path = os.path.join(outside_dir, "secret.txt")
            _write(outside_path, "TOP SECRET HOST CONTENT THAT MUST NEVER LEAK")

            arch_dir = os.path.join(self.tmp, "docs")
            os.makedirs(arch_dir)
            arch_path = os.path.join(arch_dir, "ARCHITECTURE.md")
            _write(arch_path, "# Real Architecture\n\nGenuine in-bounds content.")

            real_os_open = os.open
            swapped = {"done": False}

            def _swap_then_open(path, flags, *a, **kw):
                if os.path.abspath(path) == os.path.abspath(arch_path) and not swapped["done"]:
                    swapped["done"] = True
                    os.remove(arch_path)
                    os.symlink(outside_path, arch_path)
                return real_os_open(path, flags, *a, **kw)

            os.open = _swap_then_open
            try:
                g = load_grounding(self.tmp)
            finally:
                os.open = real_os_open

            self.assertTrue(swapped["done"], "test did not actually exercise the swap window")
            arch_entries = [f for f in g["files"] if f["label"] == "docs/ARCHITECTURE.md"]
            self.assertEqual(arch_entries, [])  # swapped-to-symlink is unusable, not silently followed
            self.assertNotIn("TOP SECRET", g["digest"])
            for f in g["files"]:
                self.assertNotIn("TOP SECRET", f["content"])
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_defect2_intermediate_symlinked_directory_is_also_rejected(self):
        # A distinct file *shape* from a final-path-component symlink:
        # `docs/` itself is a symlink to an outside directory, with a
        # perfectly ordinary regular file at the final component
        # (`ARCHITECTURE.md`). O_NOFOLLOW alone would NOT reject this (it
        # only constrains the final component, which here isn't a
        # symlink). In the current (non-racing) single-shot case this is
        # actually caught by `_under_workdir()`'s own realpath()-based
        # pre-check, since `os.path.realpath()` resolves an entire path,
        # intermediate components included, not just the last one --
        # verified directly: this test still passes with the post-open
        # fd-based check removed entirely (only the pre-check + O_NOFOLLOW
        # left), and still passes with O_NOFOLLOW additionally removed
        # (only the pre-check left). The post-open fd-based check's own
        # distinct value is narrower than "intermediate vs. final
        # component" -- it's specifically the TOCTOU window between the
        # pre-check's realpath() call and the open() call itself (see
        # test_defect2_symlink_swap_in_the_open_window_does_not_leak_outside_content,
        # which -- verified the same way -- fails if BOTH O_NOFOLLOW and
        # the post-open check are removed, but passes if either one alone
        # remains, since either independently closes that specific race).
        # Kept as its own test regardless, since it's still a real,
        # distinct adversarial shape worth covering on its own terms.
        outside_dir = tempfile.mkdtemp(prefix="switchboard-grounding-outside-")
        try:
            os.makedirs(os.path.join(outside_dir, "realdocs"))
            outside_arch = os.path.join(outside_dir, "realdocs", "ARCHITECTURE.md")
            _write(outside_arch, "TOP SECRET VIA INTERMEDIATE SYMLINK")
            os.symlink(os.path.join(outside_dir, "realdocs"), os.path.join(self.tmp, "docs"))

            entries = discover_grounding_files(self.tmp)
            self.assertEqual([e for e in entries if e["label"] == "docs/ARCHITECTURE.md"], [])
            g = load_grounding(self.tmp)
            self.assertTrue(g["empty"])
            self.assertNotIn("TOP SECRET", g["digest"])
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_defect4_read_cap_is_bytes_not_characters_for_multibyte_heavy_content(self):
        # docs/test-review.md Defect 4: a file of entirely 3-byte-per-char
        # UTF-8 content (the Euro sign) large enough that a
        # characters-bounded read would return up to 3x _GROUNDING_READ_CAP_BYTES
        # worth of encoded bytes. The post-review fix reads raw bytes off
        # the fd directly, so this must now hold exactly.
        big_char_count = teamsmod._GROUNDING_READ_CAP_BYTES  # 1 char each would already be at the cap
        content = "€" * big_char_count  # each char is 3 bytes in UTF-8
        path = os.path.join(self.tmp, "README.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.assertGreater(os.path.getsize(path), teamsmod._GROUNDING_READ_CAP_BYTES * 2)

        g = load_grounding(self.tmp)
        self.assertFalse(g["empty"])
        result_content = g["files"][0]["content"]
        self.assertLessEqual(len(result_content.encode("utf-8")), teamsmod._GROUNDING_READ_CAP_BYTES)

    def test_defect3_negated_line_is_a_known_false_confirmation_documented_not_fixed(self):
        # docs/test-review.md Defect 3 (non-blocking, should-fix ==
        # "document, don't build a fix"): the dumb conjunctive matcher has
        # no negation awareness by design (spec's own non-goal --
        # semantic/LLM-assisted matching is explicitly out of scope). This
        # test pins the current, documented behavior so a future change
        # doesn't silently alter it without the "Known limitations" note in
        # docs/implementation.md being revisited too.
        files = [_sf("docs/BACKLOG.md", "The lead never writes directly to docs/BACKLOG.md on disk.")]
        g = _synthetic_grounding(files)
        result = fact_check("the lead writes directly to docs/BACKLOG.md on disk", g)
        self.assertTrue(result["found"])  # misleading "confirmation" -- see docs/implementation.md


# ─── Read-only guarantee: runtime monkeypatch across the full surface ─────
class GroundingReadOnlyRuntimeTests(unittest.TestCase):
    """Monkeypatches builtins.open (rejects any non-read mode) and every
    mutating os/shutil function named in docs/spec.md 6b §6 (raises if
    called at all), then exercises the full public grounding surface
    against a mix of Tier 2 fixtures -- proves nothing in this module's
    grounding section ever attempts a write, not just that it "shouldn't"."""

    _MUTATING_OS_FUNCS = ["remove", "rename", "replace", "unlink", "truncate",
                          "mkdir", "makedirs", "chmod"]
    _MUTATING_SHUTIL_FUNCS = ["rmtree", "move", "copy", "copy2", "copyfile", "copytree"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-grounding-readonly-")
        _write(os.path.join(self.tmp, "docs", "ARCHITECTURE.md"), "# Arch\n\nSome architecture text.")
        _write(os.path.join(self.tmp, "docs", "BACKLOG.md"), "# Backlog\n\nSome backlog text.")
        _write(os.path.join(self.tmp, "README.md"), "# Readme\n\nSome readme text.")
        _write(os.path.join(self.tmp, "docs", "OTHER.md"), "# Other\n\nIndirected content.")
        _write(os.path.join(self.tmp, "CLAUDE.md"), "@docs/OTHER.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    _WRITE_CAPABLE_OS_OPEN_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")

    def test_no_write_open_or_mutating_call_across_full_public_surface(self):
        real_open = builtins.open
        real_os_open = os.open

        def _guarded_open(file, mode="r", *a, **kw):
            if any(c in mode for c in "wax+"):
                raise AssertionError(f"open() called in mutating mode {mode!r} for {file!r}")
            return real_open(file, mode, *a, **kw)

        def _guarded_os_open(path, flags, *a, **kw):
            for flag_name in self._WRITE_CAPABLE_OS_OPEN_FLAGS:
                flag_value = getattr(os, flag_name, 0)
                if flag_value and (flags & flag_value):
                    raise AssertionError(f"os.open() called with write-capable flag {flag_name} for {path!r}")
            return real_os_open(path, flags, *a, **kw)

        patched = {}
        for name in self._MUTATING_OS_FUNCS:
            def _forbidden_os(*a, _name=name, **kw):
                raise AssertionError(f"os.{_name}() was called: {a!r} {kw!r}")
            patched[("os", name)] = (getattr(os, name), _forbidden_os)
        for name in self._MUTATING_SHUTIL_FUNCS:
            def _forbidden_shutil(*a, _name=name, **kw):
                raise AssertionError(f"shutil.{_name}() was called: {a!r} {kw!r}")
            patched[("shutil", name)] = (getattr(shutil, name), _forbidden_shutil)

        builtins.open = _guarded_open
        os.open = _guarded_os_open
        for (mod_name, attr), (_orig, forbidden) in patched.items():
            setattr(sys.modules[mod_name], attr, forbidden)
        try:
            entries = discover_grounding_files(self.tmp)
            self.assertTrue(entries)
            g = load_grounding(self.tmp)
            self.assertFalse(g["empty"])
            build_digest(g["files"], max_bytes=500)
            fact_check("some architecture text", g)
            fact_check("", g)
            # Also exercise against this repo's own real tree in the same pass.
            g2 = load_grounding(REPO_ROOT)
            fact_check("real query against the real repo", g2)
        finally:
            builtins.open = real_open
            os.open = real_os_open
            for (mod_name, attr), (orig, _forbidden) in patched.items():
                setattr(sys.modules[mod_name], attr, orig)


# ─── Read-only guarantee: static AST scan, independent of runtime coverage ─
class GroundingStaticASTScanTests(unittest.TestCase):
    _GROUNDING_FUNCS = {
        "_under_workdir", "_open_grounding_candidate", "_read_grounding_candidate",
        "_discover_and_read", "discover_grounding_files", "_extract_headings",
        "load_grounding", "build_digest", "_significant_terms", "fact_check",
        "_cli_grounding", "_cli_fact_check",
    }
    _MUTATING_OS_FUNCS = {"remove", "rename", "replace", "unlink", "truncate",
                          "mkdir", "makedirs", "chmod"}
    _MUTATING_SHUTIL_FUNCS = {"rmtree", "move", "copy", "copy2", "copyfile", "copytree"}
    _WRITE_CAPABLE_OS_OPEN_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"}

    def setUp(self):
        with open(os.path.join(APP_DIR, "teams.py")) as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename="app/teams.py")

    def _grounding_function_nodes(self):
        nodes = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name in self._GROUNDING_FUNCS:
                nodes.append(node)
        found_names = {n.name for n in nodes}
        missing = self._GROUNDING_FUNCS - found_names
        self.assertEqual(missing, set(), "test's own function name list is stale")
        return nodes

    @staticmethod
    def _call_mode_arg(call: ast.Call):
        if len(call.args) >= 2:
            return call.args[1]
        for kw in call.keywords:
            if kw.arg == "mode":
                return kw.value
        return None

    def test_no_open_call_uses_a_write_mode_literal(self):
        for func in self._grounding_function_nodes():
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Name) and node.func.id == "open"):
                    continue
                mode_node = self._call_mode_arg(node)
                if mode_node is None:
                    continue  # default mode is "r" -- read-only
                self.assertIsInstance(
                    mode_node, ast.Constant,
                    f"open() call in {func.name}() has a non-literal mode -- cannot statically verify")
                mode = mode_node.value
                self.assertFalse(
                    any(c in mode for c in "wax+"),
                    f"open() call in {func.name}() uses mutating mode {mode!r}")

    def test_no_call_targets_a_mutating_os_or_shutil_function(self):
        for func in self._grounding_function_nodes():
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute):
                    continue
                if not isinstance(node.func.value, ast.Name):
                    continue
                mod = node.func.value.id
                attr = node.func.attr
                if mod == "os":
                    self.assertNotIn(attr, self._MUTATING_OS_FUNCS,
                                     f"{func.name}() calls forbidden os.{attr}()")
                if mod == "shutil":
                    self.assertNotIn(attr, self._MUTATING_SHUTIL_FUNCS,
                                     f"{func.name}() calls forbidden shutil.{attr}()")

    def test_no_os_open_call_requests_a_write_capable_flag(self):
        # Post-review addition (docs/test-review.md Defect 1/2 fix): the
        # grounding section now opens files via os.open() with an explicit
        # flags expression rather than builtins.open()'s mode string --
        # this scan covers that call shape the same way the mode-literal
        # scan above covers builtins.open().
        found_any_os_open = False
        for func in self._grounding_function_nodes():
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Attribute) and node.func.attr == "open"
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"):
                    continue
                found_any_os_open = True
                flags_node = node.args[1] if len(node.args) >= 2 else None
                self.assertIsNotNone(flags_node, f"os.open() call in {func.name}() has no flags argument")
                flag_names = {
                    n.attr for n in ast.walk(flags_node)
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "os"
                }
                bad = flag_names & self._WRITE_CAPABLE_OS_OPEN_FLAGS
                self.assertEqual(bad, set(),
                                 f"os.open() call in {func.name}() requests write-capable flag(s) {bad}")
        self.assertTrue(found_any_os_open, "test's own os.open() detection found nothing -- check it's not stale")


# ─── CLI ───────────────────────────────────────────────────────────────────
class GroundingCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-grounding-cli-")
        _write(os.path.join(self.tmp, "README.md"), "# CLI Fixture\n\nA specific findable sentence.")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, *args):
        env = dict(os.environ)
        return subprocess.run(
            [sys.executable, os.path.join(APP_DIR, "teams.py"), *args],
            capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=30)

    def test_grounding_subcommand_prints_valid_json_against_scratch_project(self):
        result = self._run_cli("grounding", self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["empty"])
        self.assertEqual(data["files"][0]["label"], "README.md")

    def test_grounding_subcommand_against_this_repos_own_tree(self):
        result = self._run_cli("grounding", REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        labels = [f["label"] for f in data["files"]]
        self.assertEqual(labels, ["docs/ARCHITECTURE.md", "docs/BACKLOG.md", "README.md"])

    def test_fact_check_subcommand_prints_valid_json_with_a_real_match(self):
        result = self._run_cli("fact-check", self.tmp, "a specific findable sentence")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["found"])
        self.assertEqual(data["matches"][0]["file_line"], "README.md:3")

    def test_fact_check_subcommand_with_no_match_still_exits_zero(self):
        result = self._run_cli("fact-check", self.tmp, "totally unrelated claim about spaceships")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["found"])


# ─── Tier 1: _iter_grounding_blocks() -- pure, no disk I/O (docs/spec.md 6b.1) ──
class GroundingBlockConstructionTests(unittest.TestCase):
    def test_empty_content_returns_no_blocks(self):
        self.assertEqual(teamsmod._iter_grounding_blocks(""), [])

    def test_only_blank_lines_returns_no_blocks(self):
        self.assertEqual(teamsmod._iter_grounding_blocks("\n\n   \n\t\n"), [])

    def test_blank_line_delimits_two_blocks(self):
        blocks = teamsmod._iter_grounding_blocks("first paragraph\n\nsecond paragraph")
        self.assertEqual(len(blocks), 2)
        self.assertEqual((blocks[0]["start_line"], blocks[0]["end_line"]), (1, 1))
        self.assertEqual((blocks[1]["start_line"], blocks[1]["end_line"]), (3, 3))

    def test_wrapped_continuation_joins_into_one_block_with_a_single_space(self):
        # The exact shape docs/spec.md calls out by name: a wrap boundary
        # must join with a space, not concatenate bare -- "...an" +
        # "unprivileged..." must become "an unprivileged", never
        # "anunprivileged".
        content = "This line ends mid-sentence with an\nunprivileged continuation right here"
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 1)
        self.assertEqual((blocks[0]["start_line"], blocks[0]["end_line"]), (1, 2))
        self.assertIn("an unprivileged", blocks[0]["text"])
        self.assertNotIn("anunprivileged", blocks[0]["text"])

    def test_sentence_terminal_line_ends_a_block_even_with_no_blank_line(self):
        # Two complete, independent sentences stacked with no blank line
        # between them must NOT become one block -- see
        # _GROUNDING_SENTENCE_END_RE's own module-level comment and
        # docs/implementation.md "Deviations from spec" for exactly why:
        # without this rule, 6b's own existing precision test
        # (test_partial_term_overlap_on_every_line_is_not_a_match) would
        # regress under blank-line-only delimiting.
        content = "Setup instructions live here.\nThe database lives elsewhere."
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["end_line"], 1)
        self.assertEqual(blocks[1]["start_line"], 2)

    def test_run_longer_than_max_lines_is_split(self):
        max_lines = teamsmod._GROUNDING_BLOCK_MAX_LINES
        # No terminal punctuation, no blank lines -- isolates the line-count
        # bound from the sentence-boundary and blank-line rules.
        lines = [f"filler token number {i} without any terminal mark" for i in range(max_lines + 5)]
        blocks = teamsmod._iter_grounding_blocks("\n".join(lines))
        self.assertGreaterEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["start_line"], 1)
        self.assertEqual(blocks[0]["end_line"], max_lines)
        self.assertEqual(blocks[1]["start_line"], max_lines + 1)

    def test_run_exceeding_max_chars_is_split_before_the_line_cap(self):
        max_chars = teamsmod._GROUNDING_BLOCK_MAX_CHARS
        long_line = "x" * 200 + " filler words with no terminal mark at the end"
        num_lines = (max_chars // len(long_line)) + 3
        content = "\n".join(long_line for _ in range(num_lines))
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertGreaterEqual(len(blocks), 2)
        self.assertLessEqual(len(blocks[0]["text"]), max_chars)
        self.assertLess(blocks[0]["end_line"], num_lines)  # split before every line is consumed

    def test_single_line_far_longer_than_max_chars_is_its_own_block_and_truncated(self):
        max_chars = teamsmod._GROUNDING_BLOCK_MAX_CHARS
        blocks = teamsmod._iter_grounding_blocks("a" * (max_chars * 3))
        self.assertEqual(len(blocks), 1)
        self.assertEqual((blocks[0]["start_line"], blocks[0]["end_line"]), (1, 1))
        self.assertEqual(len(blocks[0]["text"]), max_chars)

    def test_two_unrelated_adjacent_bullets_in_a_tight_markdown_list_do_not_merge(self):
        # This repo's own docs/ARCHITECTURE.md uses tight lists (no blank
        # line between sibling bullets) -- reproduced synthetically here so
        # this doesn't depend on that file's own prose staying exactly as
        # it is today. Each bullet ends in terminal punctuation, so the
        # sentence-boundary rule (not a blank line) is what keeps them
        # apart, exactly like the real file (verified directly against
        # docs/ARCHITECTURE.md's own bullets while building this fix).
        content = (
            "- The alpha subsystem handles widget rotation entirely.\n"
            "- The omega subsystem handles gadget storage entirely."
        )
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 2)
        g = _synthetic_grounding([_sf("README.md", content)])
        result = fact_check("widget storage", g)  # one term from each bullet
        self.assertFalse(result["found"])

    # ── Round-1 correction (docs/spec.md "Round-1 correction",
    # docs/test-review.md): structural elements -- headings, list items,
    # block quotes, table rows, code fences -- are boundaries the original
    # blank-line/sentence-terminal rules alone did not cover.

    def test_heading_line_is_excluded_from_every_block(self):
        blocks = teamsmod._iter_grounding_blocks("## A Heading\nOrdinary body text follows")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["start_line"], 2)  # the heading itself never appears
        self.assertNotIn("Heading", blocks[0]["text"])

    def test_heading_only_content_produces_no_blocks_at_all(self):
        self.assertEqual(teamsmod._iter_grounding_blocks("## Just A Heading"), [])

    def test_list_item_marker_is_a_hard_boundary_even_mid_run(self):
        # No terminal punctuation anywhere, no blank lines -- only the
        # marker itself separates these two items.
        content = "- first item continuation words here\n- second item continuation words here"
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["start_line"], 1)
        self.assertEqual(blocks[1]["start_line"], 2)

    def test_list_items_own_wrapped_continuation_still_joins(self):
        # A marker line's own non-marker continuation lines still
        # accumulate normally -- this is what keeps the wrap-boundary
        # recall win working for real bulleted prose.
        content = "- this bullet wraps onto\n  a second line with no period"
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 1)
        self.assertEqual((blocks[0]["start_line"], blocks[0]["end_line"]), (1, 2))
        self.assertIn("wraps onto a second line", blocks[0]["text"])

    def test_block_quote_marker_is_a_hard_boundary(self):
        content = "ordinary prose line with no punctuation\n> a quoted line follows"
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 2)

    def test_table_row_marker_is_a_hard_boundary(self):
        content = "ordinary prose line with no punctuation\n| col1 | col2 |"
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 2)

    def test_fenced_code_content_is_excluded_from_every_block(self):
        content = "prose before the fence\n```\ncode line one\ncode line two\n```\nprose after the fence"
        blocks = teamsmod._iter_grounding_blocks(content)
        texts = [b["text"] for b in blocks]
        self.assertEqual(len(blocks), 2)
        self.assertTrue(all("code line" not in t for t in texts))

    def test_unclosed_fence_excludes_everything_to_end_of_file(self):
        content = "prose before\n```\ncode that never closes\nmore code"
        blocks = teamsmod._iter_grounding_blocks(content)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("code", blocks[0]["text"])


# ─── fact_check() block-matching behavior (docs/spec.md 6b.1) ─────────────
class FactCheckBlockMatchingTests(unittest.TestCase):
    def test_claim_split_across_a_wrap_boundary_matches_and_text_shows_joined_sentence(self):
        content = ("- **`app/app.py`** runs as `SVC_USER` (default `switchboard-svc`), an\n"
                   "  unprivileged system account with no login shell of its own.")
        g = _synthetic_grounding([_sf("docs/ARCHITECTURE.md", content)])
        result = fact_check("app.py runs as SVC_USER, an unprivileged system account", g)
        self.assertTrue(result["found"])
        hit = result["matches"][0]
        self.assertEqual(hit["line"], 1)
        self.assertEqual(hit["end_line"], 2)
        self.assertIn("an unprivileged system account", hit["text"])

    def test_wrap_boundary_claim_matches_the_real_docs_architecture_md(self):
        # docs/spec.md's own acceptance criterion, against the real file
        # (not a synthetic mirror of it) -- the specific defect this whole
        # sub-spec exists to fix: a single sentence hard-wrapped across two
        # physical lines by docs/ARCHITECTURE.md's own prose style.
        g = load_grounding(REPO_ROOT)
        result = fact_check(
            "app.py runs as SVC_USER, an unprivileged system account with no login shell", g)
        self.assertTrue(result["found"])
        hit = next(m for m in result["matches"] if m["label"] == "docs/ARCHITECTURE.md")
        self.assertEqual(hit["line"], 5)
        self.assertIn("an unprivileged system account", hit["text"])

    def test_wrap_boundary_space_insertion_fused_token_fails_natural_phrase_matches(self):
        content = "This line ends mid-sentence with an\nunprivileged continuation right here"
        g = _synthetic_grounding([_sf("README.md", content)])
        fused = fact_check("anunprivileged", g)
        self.assertFalse(fused["found"])
        natural = fact_check("an unprivileged continuation", g)
        self.assertTrue(natural["found"])

    def test_claim_spanning_two_different_paragraphs_does_not_match(self):
        content = "First paragraph mentions apple.\n\nSecond paragraph mentions banana."
        g = _synthetic_grounding([_sf("README.md", content)])
        result = fact_check("apple banana", g)
        self.assertFalse(result["found"])

    def test_claim_straddling_a_max_lines_split_does_not_match(self):
        max_lines = teamsmod._GROUNDING_BLOCK_MAX_LINES
        lines = [f"filler token number {i} without any terminal mark" for i in range(max_lines + 3)]
        lines[0] = "alpha marker filler token without any terminal mark"
        lines[-1] = "omega marker filler token without any terminal mark"
        g = _synthetic_grounding([_sf("README.md", "\n".join(lines))])
        result = fact_check("alpha omega", g)
        self.assertFalse(result["found"])
        # Sanity: each half still matches fine on its own.
        self.assertTrue(fact_check("alpha marker", g)["found"])
        self.assertTrue(fact_check("omega marker", g)["found"])

    def test_line_file_line_and_end_line_point_at_the_blocks_first_and_last_lines(self):
        content = "\n".join([
            "# Heading",
            "",
            "First block line one talks about zebras",
            "First block line two continues about zebras,",
            "and a third line finishes the zebra thought.",
        ])
        g = _synthetic_grounding([_sf("docs/ARCHITECTURE.md", content)])
        result = fact_check("zebras continues finishes", g)
        self.assertTrue(result["found"])
        hit = result["matches"][0]
        self.assertEqual(hit["line"], 3)
        self.assertEqual(hit["file_line"], "docs/ARCHITECTURE.md:3")
        self.assertEqual(hit["end_line"], 5)


# ─── Recall benchmark, round 2 -- corrected bounds (docs/spec.md "Round-1
# correction", docs/test-review.md) ─────────────────────────────────────────
class SixClaimBenchmarkTests(unittest.TestCase):
    """Round 1's own six-claim benchmark was authored by the developer
    AFTER the matcher was already implemented, then measured against that
    same matcher -- both the reviewer (a fresh 7-claim benchmark) and the
    coordinator independently measured a materially lower 4/7 on claims
    they wrote without seeing the matcher's behavior first, and flagged
    round 1's "6/6" headline as optimistic self-measurement. This round
    replaces it with two batches of six claims each, both **written and
    committed to before being run even once** against this repo's real
    docs/ARCHITECTURE.md, under the corrected 3-line/400-char bounds:

    - CLAIMS_VOCAB_DRIFT: natural paraphrases that swap the source's own
      key nouns for synonyms (e.g. "loopback" for "127.0.0.1", "heals" for
      "self-heals"). Scored **0/6**, old matcher and new matcher alike --
      not a block-boundary recall failure: fact_check() has never been
      anything but a literal substring matcher (explicit 6b non-goal: "no
      fuzzy matching"), so a claim using different words than the source
      can never match regardless of how wide the matching unit is. Kept
      and pinned here specifically so "vocabulary mismatch" and "line-wrap
      recall" stay two clearly distinct things, not conflated in a future
      cycle into "the block matcher still doesn't work."
    - CLAIMS_SAME_VOCAB: paraphrases that keep the source's own distinctive
      identifiers (`SVC_USER`, `run_startup_watch`, `URL_FILE`,
      `127.0.0.1`, `PUBLISH_MODE`, ...) verbatim while paraphrasing the
      surrounding grammar -- closer to the original 6b reviewer
      benchmark's own style. Scored **2/6** under the corrected bounds, up
      from **0/6** under 6b's original single-line matcher on the exact
      same six claims -- a real, honest improvement, just a far more
      modest one than round 1's self-measured 6/6.

    Neither count is a target to hit or a gate to pass: per the
    coordinator's own round-1 correction, a recall figure below the spec's
    original "5 of 6" language is now an explicitly acceptable outcome --
    precision (see ReviewerAdversarialBlockPrecisionTests below, and the
    unmodified 6b adversarial suite elsewhere in this file) is the
    property that must hold. Both counts are pinned via assertEqual so a
    future change to the matcher has to consciously update this record
    rather than silently drift either number. Full per-claim results are
    recorded in docs/implementation.md."""

    CLAIMS_VOCAB_DRIFT = [
        "_reap_dead_state runs on every /status call and heals session state "
        "once the underlying tmux session actually ends",
        "the host-start.sh URL file used to only get written after the full "
        "startup sequence succeeded, which could leave a stale file if a step timed out",
        "run_startup_watch always writes or clears URL_FILE when it finishes, "
        "whether the startup succeeded or timed out",
        "a failed confirm on a name collision leaves the upload staging directory "
        "in place so the UI's Back to review button can retry",
        "generalizing engine handling into engines.d engine files collapsed two "
        "separate implementations of the startup prompt then find a URL logic "
        "into one shared tested behavior",
        "per-project terminals always bind only to loopback no matter what "
        "PUBLISH_MODE is set to",
    ]

    CLAIMS_SAME_VOCAB = [
        "run_startup_watch always writes-or-clears URL_FILE when it's done, success or timeout",
        "the already running fast path checks whether the cached URL predates the "
        "session it's attached to and drops it if so",
        "a failed confirm leaves staging in place so the Back to review button can "
        "retry the same token",
        "generalizing engine handling into engines.d collapsed two separate "
        "implementations of handle a startup prompt then look for a URL into one "
        "shared tested behavior",
        "_reap_dead_state is called on every /status and this self-heals as soon "
        "as the underlying tmux session actually ends",
        "per-project terminals bind to 127.0.0.1 only regardless of PUBLISH_MODE",
    ]

    @staticmethod
    def _old_single_line_fact_check_found(claim, grounding):
        # 6b's pre-6b.1 matcher, reproduced verbatim here for measurement
        # only -- not a production code path any more. Every significant
        # term must appear on one physical line.
        terms = teamsmod._significant_terms(claim)
        if not terms:
            return False
        for f in grounding.get("files", []):
            for line in f.get("content", "").splitlines():
                lower = line.lower()
                if all(term in lower for term in terms):
                    return True
        return False

    def test_vocab_drift_batch_scores_zero_regardless_of_matching_unit(self):
        g = load_grounding(REPO_ROOT)
        old_hits = sum(self._old_single_line_fact_check_found(c, g) for c in self.CLAIMS_VOCAB_DRIFT)
        new_hits = sum(fact_check(c, g)["found"] for c in self.CLAIMS_VOCAB_DRIFT)
        self.assertEqual(old_hits, 0)
        self.assertEqual(new_hits, 0)

    def test_same_vocabulary_batch_recall_measured_honestly(self):
        g = load_grounding(REPO_ROOT)
        old_hits = sum(self._old_single_line_fact_check_found(c, g) for c in self.CLAIMS_SAME_VOCAB)
        new_hits = sum(fact_check(c, g)["found"] for c in self.CLAIMS_SAME_VOCAB)
        self.assertEqual(old_hits, 0)
        self.assertEqual(new_hits, 2)

    def test_every_benchmark_match_is_reported_against_the_real_file_it_supports(self):
        g = load_grounding(REPO_ROOT)
        for claim in self.CLAIMS_VOCAB_DRIFT + self.CLAIMS_SAME_VOCAB:
            result = fact_check(claim, g)
            for m in result["matches"]:
                self.assertEqual(m["label"], "docs/ARCHITECTURE.md")
                self.assertGreaterEqual(m["end_line"], m["line"])


# ─── The reviewer's round-1 adversarial precision attacks (docs/test-review.md
# "Blocking finding: new false positives from block-widening"), ported
# permanently ─────────────────────────────────────────────────────────────
class ReviewerAdversarialBlockPrecisionTests(unittest.TestCase):
    """Ported verbatim (same content, same claims) from the reviewer's own
    scratch script
    (test_reviewer_adversarial.py, docs/test-review.md round-1 testing
    pass) into the permanent suite, per the coordinator's explicit
    instruction. All five reproduced a false `found: True` against round
    1's 12-line/1500-char, blank-line + sentence-terminal-only matcher;
    all five are must-fix precision regressions the round-1 diff
    introduced, independently confirmed by the reviewer to return `False`
    under 6b's original single-line matcher. All five now return
    `found: False` under the corrected structural-boundary rules and
    3-line/400-char bounds."""

    def test_heading_with_no_terminal_punctuation_merges_with_unrelated_body(self):
        content = ("## Widget rotation subsystem\n"
                   "The gadget storage subsystem handles persistence for unrelated items.")
        g = _synthetic_grounding([_sf("t.md", content)])
        result = fact_check("widget rotation gadget storage", g)
        self.assertFalse(result["found"],
            f"heading + unrelated body merged into a false match: {result['matches']}")

    def test_tight_bullet_list_with_no_terminal_punctuation_merges_unrelated_bullets(self):
        content = ("- widget rotation config\n"
                   "- gadget storage config\n"
                   "- unrelated topic zebra migration\n")
        g = _synthetic_grounding([_sf("t.md", content)])
        result = fact_check("widget rotation zebra migration", g)
        self.assertFalse(result["found"],
            f"three unrelated terse bullets merged into a false match: {result['matches']}")

    def test_fenced_code_block_merges_with_adjoining_unrelated_prose(self):
        content = ("The deploy script does the following\n"
                   "```\n"
                   "run_as_root()\n"
                   "grant_secret_access()\n"
                   "```\n"
                   "for the unrelated widget subsystem\n")
        g = _synthetic_grounding([_sf("t.md", content)])
        result = fact_check("deploy script grant_secret_access widget subsystem", g)
        self.assertFalse(result["found"],
            f"code fence content merged with surrounding unrelated prose: {result['matches']}")

    def test_terms_12_lines_apart_in_unrelated_filler_still_match(self):
        lines = [f"filler line {i} with no punctuation at end" for i in range(12)]
        lines[0] = "alpha marker appears here with no punctuation at end"
        lines[11] = "omega marker appears here with no punctuation at end"
        content = "\n".join(lines)
        g = _synthetic_grounding([_sf("t.md", content)])
        result = fact_check("alpha marker omega marker", g)
        self.assertFalse(result["found"],
            f"terms 12 lines apart in unrelated filler falsely co-occur: {result['matches']}")

    def test_heading_only_claim_matches_unrelated_following_paragraph(self):
        content = ("## Setup database configuration\n"
                   "Unrelated content about deployment follows here without periods\n")
        g = _synthetic_grounding([_sf("t.md", content)])
        result = fact_check("setup database configuration", g)
        self.assertFalse(result["found"],
            f"heading text falsely co-occurs with unrelated following paragraph: {result['matches']}")


# ─── `skipped` list (docs/spec.md 6b.1 follow-up 1) ────────────────────────
class GroundingSkippedListTests(unittest.TestCase):
    """An in-bounds candidate rejected for any reason is now surfaced in
    load_grounding()'s `skipped` list rather than vanishing with no
    signal -- see docs/test-review.md's "Answer to the coordinator's
    in-bounds-symlink question" for the original ask."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-grounding-skipped-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skipped_empty_for_a_clean_project(self):
        _write(os.path.join(self.tmp, "README.md"), "# Clean\n\nNothing unusual here.")
        g = load_grounding(self.tmp)
        self.assertEqual(g["skipped"], [])

    def test_skipped_empty_when_no_candidates_exist_at_all(self):
        # Simply absent is not "rejected" -- see
        # _open_grounding_candidate()'s own docstring for the distinction.
        g = load_grounding(self.tmp)
        self.assertTrue(g["empty"])
        self.assertEqual(g["skipped"], [])

    def test_skipped_populated_for_an_in_bounds_symlink(self):
        _write(os.path.join(self.tmp, "docs", "REAL_README.md"), "# Real\n\nActual content.")
        os.symlink(os.path.join(self.tmp, "docs", "REAL_README.md"),
                   os.path.join(self.tmp, "README.md"))
        g = load_grounding(self.tmp)
        self.assertTrue(g["empty"])  # still categorically unusable, docs/test-review.md Defect 2
        self.assertEqual(g["skipped"], [
            {"label": "README.md", "relpath": "README.md", "reason": "symlink"},
        ])

    def test_skipped_populated_for_out_of_bounds_indirection_target(self):
        outside_dir = tempfile.mkdtemp(prefix="switchboard-grounding-outside-")
        try:
            outside_path = os.path.join(outside_dir, "secret.txt")
            _write(outside_path, "top secret host content")
            rel = os.path.relpath(outside_path, self.tmp)
            _write(os.path.join(self.tmp, "CLAUDE.md"), f"@{rel}")

            g = load_grounding(self.tmp)
            self.assertTrue(g["empty"])
            self.assertEqual(g["skipped"], [
                {"label": "CLAUDE.md", "relpath": rel, "reason": "out_of_bounds"},
            ])
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_skipped_populated_for_a_directory_where_a_file_is_expected(self):
        os.makedirs(os.path.join(self.tmp, "docs", "ARCHITECTURE.md"))
        g = load_grounding(self.tmp)
        arch_skips = [s for s in g["skipped"] if s["label"] == "docs/ARCHITECTURE.md"]
        self.assertEqual(arch_skips, [
            {"label": "docs/ARCHITECTURE.md", "relpath": "docs/ARCHITECTURE.md",
             "reason": "not_regular_file"},
        ])

    @unittest.skipIf(os.getuid() == 0, "permission bits are not enforced against root")
    def test_skipped_populated_for_a_permission_denied_file(self):
        path = os.path.join(self.tmp, "README.md")
        _write(path, "# Unreadable\n\nContent nobody -- including us -- can read.")
        os.chmod(path, 0o000)
        try:
            g = load_grounding(self.tmp)
            self.assertEqual(g["skipped"], [
                {"label": "README.md", "relpath": "README.md", "reason": "unreadable"},
            ])
        finally:
            os.chmod(path, 0o644)  # so tearDown's rmtree can remove it


# ─── /proc/self/fd unavailability (docs/spec.md 6b.1 follow-up 2) ─────────
class GroundingProcUnavailableTests(unittest.TestCase):
    """If /proc/self/fd doesn't resolve (as if /proc weren't mounted), every
    candidate must be rejected with a distinct, surfaced reason -- not
    silently -- and never by falling back to a path-based realpath() check
    (which would reopen the TOCTOU race docs/test-review.md Defect 2
    closed). Simulated the same way docs/test-review.md's own round-2
    testing pass simulated it (test case #12): monkeypatch
    os.path.realpath to hand back the literal, unresolved string for any
    /proc/self/fd/... argument, delegating to the real implementation for
    everything else -- only the fd-based post-open containment check is
    meant to degrade; workdir/candidate-path resolution must still work
    normally."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchboard-grounding-procsim-")
        self._real_realpath = os.path.realpath
        teamsmod._grounding_proc_warned = False

    def tearDown(self):
        os.path.realpath = self._real_realpath
        teamsmod._grounding_proc_warned = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _simulate_proc_unavailable(self):
        real_realpath = self._real_realpath

        def _fake_realpath(path, *a, **kw):
            if isinstance(path, str) and path.startswith("/proc/self/fd/"):
                return path  # unresolved -- exactly what a missing /proc yields
            return real_realpath(path, *a, **kw)

        os.path.realpath = _fake_realpath

    def test_every_candidate_is_rejected_with_a_distinct_reason_not_silent_emptiness(self):
        _write(os.path.join(self.tmp, "README.md"), "# Genuinely Fine\n\nReal content.")
        self._simulate_proc_unavailable()

        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            g = load_grounding(self.tmp)

        self.assertTrue(g["empty"])
        self.assertEqual(g["files"], [])
        self.assertEqual(g["skipped"], [
            {"label": "README.md", "relpath": "README.md", "reason": "proc_unavailable"},
        ])
        self.assertIn("proc", stderr_capture.getvalue().lower())

    def test_warning_prints_exactly_once_across_multiple_rejected_candidates(self):
        _write(os.path.join(self.tmp, "docs", "ARCHITECTURE.md"), "# A\n\nSome text.")
        _write(os.path.join(self.tmp, "README.md"), "# R\n\nSome text.")
        self._simulate_proc_unavailable()

        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            g = load_grounding(self.tmp)

        self.assertEqual(len(g["skipped"]), 2)
        self.assertEqual(stderr_capture.getvalue().count("did not resolve"), 1)


if __name__ == "__main__":
    unittest.main()
