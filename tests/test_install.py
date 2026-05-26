"""Smoke tests for installation and import surface."""
from __future__ import annotations

import subprocess
import sys
import unittest


class TestInstallSmoke(unittest.TestCase):

    def test_import_vocab(self):
        import vocab
        self.assertTrue(hasattr(vocab, "__version__"))

    def test_version_string(self):
        from vocab import __version__
        self.assertIsInstance(__version__, str)
        self.assertGreater(len(__version__), 0)

    def test_cli_imports(self):
        from vocab.cli import cli
        self.assertIsNotNone(cli)

    def test_core_imports(self):
        from vocab.segmenter import segment
        from vocab.vocabulary import build_vocabulary
        from vocab.index import encode_indices, decode_indices
        from vocab.scanner import scan_codebase
        self.assertTrue(callable(segment))

    def test_reports_import(self):
        from vocab.reports import preflight_report, ci_report, compute_stability
        self.assertTrue(callable(compute_stability))

    def test_bootstrap_import(self):
        from vocab.bootstrap import bootstrap_repo, explore_repo, compute_modules
        self.assertTrue(callable(explore_repo))

    def test_git_import(self):
        from vocab import git as vgit
        self.assertTrue(callable(vgit.is_repo))

    def test_cli_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "vocab", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("vocab-cli", result.stdout)

    def test_cli_help_all_no_errors(self):
        result = subprocess.run(
            [sys.executable, "-m", "vocab", "--help-all"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Getting Started", result.stdout)
