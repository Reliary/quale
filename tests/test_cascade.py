"""Tests for rho (token cascade detection)."""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from vocab.cascade import scan_cascade, format_rho_report, format_rho_json


class TestRhoFixture(unittest.TestCase):
    """Run against the contrived cascade fixture repo."""

    def setUp(self):
        from tests.fixtures.build_cascade_repo import build_fixture
        self.repo = build_fixture()

    def tearDown(self):
        import subprocess
        subprocess.run(["git", "checkout", "--quiet", "HEAD"],
                       cwd=self.repo, capture_output=True)

    def test_badtoken_detected(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5)
        bad = [e for e in events if e.token == "BadToken"]
        self.assertTrue(len(bad) >= 1, "BadToken should be detected")
        token = bad[0]
        self.assertGreater(token.r0, 1.0, "BadToken should have R₀ > 1.0")
        self.assertIn("auth.ts", token.birth_file)
        self.assertGreaterEqual(len(token.infected), 3,
                                "BadToken should infect ≥3 files")

    def test_badtoken_table_output_includes_r0(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5)
        report = format_rho_report(events, repo=self.repo, since="HEAD~8..HEAD", compact=True)
        self.assertIn("BadToken", report)
        self.assertIn("R₀=", report)

    def test_badtoken_json_output(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5)
        text = format_rho_json(events)
        data = json.loads(text)
        tokens = [d["token"] for d in data]
        self.assertIn("BadToken", tokens)
        bt = next(d for d in data if d["token"] == "BadToken")
        self.assertGreater(bt["r0"], 1.0)
        self.assertGreaterEqual(len(bt["infected"]), 3)

    def test_high_threshold_filters_all(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5)
        high = [e for e in events if e.r0 >= 10.0]
        self.assertEqual(len(high), 0, "No token should have R₀ ≥ 10")

    def test_cascade_event_fields(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5)
        bad = next((e for e in events if e.token == "BadToken"), None)
        self.assertIsNotNone(bad)
        self.assertTrue(len(bad.birth_commit) >= 6)
        self.assertGreater(bad.confidence, 0)
        if bad.infected:
            site = bad.infected[0]
            self.assertTrue(len(site.file) > 0)
            self.assertTrue(len(site.commit) >= 6)
            self.assertGreater(site.generation, 0)

    def test_ci_exit_code(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5, threshold=1.0)
        spreading = [e for e in events if e.r0 >= 1.0]
        self.assertGreater(len(spreading), 0,
                           "Should find spreading tokens at threshold 1.0")

    def test_contained_tokens_have_lower_r0(self):
        events = scan_cascade(self.repo, since="HEAD~8..HEAD", window=5)
        bad = next((e for e in events if e.token == "BadToken"), None)
        self.assertIsNotNone(bad)
        single_file = [e for e in events if len(e.infected) == 0]
        for sf in single_file:
            self.assertEqual(sf.r0, 0.0)


class TestRhoEmpty(unittest.TestCase):
    """Edge case: empty or single-commit range."""

    def test_empty_range(self):
        events = scan_cascade("/tmp", since="HEAD", window=5)
        self.assertEqual(len(events), 0)


if __name__ == "__main__":
    unittest.main()
