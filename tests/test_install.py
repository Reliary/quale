"""Smoke tests for installation and import surface."""
from __future__ import annotations

import subprocess
import sys
import unittest


class TestInstallSmoke(unittest.TestCase):

    def test_import_quale(self):
        import quale
        self.assertTrue(hasattr(quale, "__version__"))

    def test_version_string(self):
        from quale import __version__
        self.assertIsInstance(__version__, str)
        self.assertGreater(len(__version__), 0)

    def test_cli_imports(self):
        from quale.cli import cli
        self.assertIsNotNone(cli)

    def test_core_imports(self):
        from quale.segmenter import segment
        from quale.vocabulary import build_vocabulary
        from quale.index import encode_indices, decode_indices
        from quale.scanner import scan_codebase
        self.assertTrue(callable(segment))

    def test_reports_import(self):
        from quale.reports import preflight_report, ci_report, compute_stability
        self.assertTrue(callable(compute_stability))

    def test_bootstrap_import(self):
        from quale.bootstrap import bootstrap_repo, explore_repo, compute_modules
        self.assertTrue(callable(explore_repo))

    def test_git_import(self):
        from quale import git as vgit
        self.assertTrue(callable(vgit.is_repo))

    def test_cli_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "quale", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("quale-cli", result.stdout)

    def test_cli_help_all_no_errors(self):
        result = subprocess.run(
            [sys.executable, "-m", "quale", "--help-all"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Getting Started", result.stdout)
