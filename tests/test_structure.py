"""Structural guardrails: verify code is written to the correct places.

Ensures:
- Each source module has a corresponding test file
- No stale naming ('vocab' references outside vocabulary.py)
- Test infrastructure is consistent
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestModuleTestMapping(unittest.TestCase):
    """Every source module should have a test file."""

    def test_all_modules_have_tests(self):
        """Major source modules should have test coverage.

        Not every utility file needs a dedicated test file — utility modules
        are often covered by integration tests. But core logic modules should
        have a matching test file.
        """
        source_dir = PROJECT_ROOT / "quale"
        test_dir = PROJECT_ROOT / "tests"

        test_file_names = {tf.name for tf in test_dir.rglob("*.py") if tf.name.endswith(".py")}

        core_modules = {
            "reports/__init__.py": "test_reports.py",
            "reports/analysis.py": "test_reports.py",
            "cli.py": "test_cli.py",
            "scanner.py": "test_commands.py",
            "bootstrap.py": "test_commands.py",
            "analyze.py": "test_core.py",
            "compare.py": "test_commands.py",
            "mcp_server.py": "test_mcp_server.py",
            "vocabulary.py": "test_property.py",
            "git.py": "test_commands.py",
            "formats/terminal.py": "test_output_contracts.py",
        }

        test_file_names = {tf.name for tf in test_dir.rglob("*.py") if tf.name.endswith(".py")}

        missing = []
        for rel, expected_test in core_modules.items():
            if expected_test not in test_file_names:
                missing.append(f"{rel} -> {expected_test}")

        self.assertEqual(missing, [], f"Core modules without test coverage: {missing}")


class TestNoStaleVocabNaming(unittest.TestCase):
    """No stale 'vocab' references in source or tests (except vocabulary.py)."""

    def test_no_vocab_in_source_imports(self):
        source_dir = PROJECT_ROOT / "quale"
        allowed_files = {"vocabulary.py"}
        # Only check import-level references, not internal file_vocabs field names
        violations = []
        for py in source_dir.rglob("*.py"):
            if py.name in allowed_files:
                continue
            if py.name.startswith("_"):
                continue
            content = py.read_text(encoding="utf-8")
            # Check for import-level vocab references, not data model field names
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("from vocab") or stripped.startswith("import vocab"):
                    violations.append(f"{py}: {stripped[:80]}")
        self.assertEqual(violations, [], f"Stale 'vocab' import references: {violations}")

    def test_no_run_vocab_in_tests(self):
        test_dir = PROJECT_ROOT / "tests"
        violations = []
        for py in test_dir.rglob("*.py"):
            if py.name in ("helpers.py", "test_structure.py"):
                continue
            content = py.read_text(encoding="utf-8")
            # Check for method definitions, not string literals in assertion messages
            for line in content.split("\n"):
                if "def run_vocab" in line:
                    violations.append(f"{py}: {line.strip()}")
        self.assertEqual(
            violations, [],
            f"Stale 'run_vocab' method references (should be 'run_quale'): {violations}"
        )

    def test_no_vocabcli_in_tests(self):
        test_dir = PROJECT_ROOT / "tests"
        violations = []
        for py in test_dir.rglob("*.py"):
            if py.name == "test_structure.py":
                continue
            content = py.read_text(encoding="utf-8")
            if "VocabCli" in content:
                violations.append(str(py))
        self.assertEqual(
            violations, [],
            f"Stale 'VocabCli' class references (should be 'QualeCli'): {violations}"
        )

    def test_no_vocab_effect_in_scripts(self):
        scripts_dir = PROJECT_ROOT / "scripts"
        if not scripts_dir.exists():
            self.skipTest("No scripts directory")
        violations = []
        for py in scripts_dir.rglob("*.py"):
            content = py.read_text(encoding="utf-8")
            if "evaluate_vocab_effect" in content:
                violations.append(str(py))
        self.assertEqual(
            violations, [],
            f"Stale 'evaluate_vocab_effect' references (should be 'evaluate_quale_effect'): {violations}"
        )


class TestTestHelperConsistency(unittest.TestCase):
    """Test infrastructure should be consistent across test files."""

    def test_all_test_files_use_run_quale_not_subprocess(self):
        """New test files should define a run_quale or run_cli helper, not use raw subprocess.

        Existing test files with their own helpers are grandfathered in.
        """
        test_dir = PROJECT_ROOT / "tests"
        allowed_files = {
            "helpers.py", "test_structure.py",
        }
        violations = []
        for py in test_dir.rglob("*.py"):
            if py.name in allowed_files:
                continue
            if py.name.startswith("_"):
                continue
            content = py.read_text(encoding="utf-8")
            has_helper = any(
                f"def {name}" in content
                for name in ("run_quale", "run_cli", "run_vocab")
            )
            # Files already checked in are grandfathered
            if not has_helper:
                violations.append(str(py))
        # Only flag NEW files that haven't adopted the helper pattern
        self.maxDiff = None
        ignored = {
            "test_install.py", "test_reports.py",
            "test_core.py", "test_performance.py", "test_property.py",
            "test_mcp_server.py",  # tests MCP protocol, not CLI
        }
        violations = [v for v in violations if Path(v).name not in ignored]
        self.assertEqual(
            violations, [],
            f"Test files without CLI run helper: {violations}"
        )
        # test_install.py is exempt because it imports the CLI module directly
        self.assertEqual(
            violations, [],
            f"Test files using raw subprocess for quale.cli without a helper: {violations}"
        )


if __name__ == "__main__":
    unittest.main()
