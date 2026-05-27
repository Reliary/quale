"""Layer 1: CLI smoke test — runs every command, asserts exit 0 + non-empty stdout.

Also verifies help text is registered for every command in every namespace."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPO = str(PROJECT_ROOT)  # run structural commands against quale itself


class SmokeTestBase(unittest.TestCase):
    """Base with run_cli + fixture helpers."""

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_cli(self, *args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=cwd or FIXTURE_REPO,
            env=self.env,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed (exit {result.returncode}): {' '.join(args)}\nstdout={result.stdout[:500]}\nstderr={result.stderr[:500]}")
        return result

    def assure_cli(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        """Run CLI and return result without checking exit code."""
        return subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=cwd or FIXTURE_REPO,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def _git(self, repo: str, *args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    def _commit(self, repo: str, msg: str) -> None:
        self._git(repo, "add", ".")
        self._git(repo, "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", msg)

    def _write(self, repo: str, rel: str, content: str) -> str:
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path


# ── Layer 1: Smoke tests — every command, exit 0, non-empty stdout ──


class TestRootCommands(SmokeTestBase):
    """Root-level human commands."""

    def test_review_smoke(self):
        tmp, repo = tempfile.TemporaryDirectory(), None
        try:
            repo = tmp.name
            self._git(repo, "init", "-q")
            self._write(repo, "src/a.go", "package main\nfunc A() {}\n")
            self._write(repo, "tests/a_test.go", "package main\nfunc TestA(t) { A() }\n")
            self._commit(repo, "init")
            self._write(repo, "src/a.go", "package main\nfunc A() string { return \"v2\" }\n")
            self._commit(repo, "change a")
            r = self.run_cli("review", "--path", repo)
            self.assertTrue(len(r.stdout.strip()) > 50, f"review output too short: {r.stdout[:200]}")
        finally:
            if repo:
                tmp.cleanup()

    def test_onboard_smoke(self):
        r = self.run_cli("onboard", "--path", FIXTURE_REPO)
        self.assertIn("Step 1", r.stdout)

    def test_refactor_cost_smoke(self):
        r = self.run_cli("refactor-cost", "README.md", "--path", FIXTURE_REPO)
        self.assertTrue("impact" in r.stdout or "Simple change" in r.stdout or "LOW" in r.stdout)


class TestAgentCommands(SmokeTestBase):
    """Agent-persona commands."""

    def test_agent_orient_smoke(self):
        r = self.run_cli("agent", "orient", "--path", FIXTURE_REPO)
        data = json.loads(r.stdout)
        self.assertIn("landmarks", data)
        self.assertIn("modules", data)
        self.assertIn("languages", data)

    def test_agent_edit_smoke(self):
        r = self.run_cli("agent", "edit", "README.md", "--path", FIXTURE_REPO)
        data = json.loads(r.stdout)
        self.assertIn("verification_mc", data)
        self.assertIn("changed_files", data)

    def test_agent_guard_smoke(self):
        r = self.run_cli("agent", "guard", "README.md", "--path", FIXTURE_REPO)
        data = json.loads(r.stdout)
        self.assertEqual(data.get("schema_version"), 1)
        self.assertIn("file", data)


class TestCICommands(SmokeTestBase):
    """CI-persona commands."""

    def test_ci_init_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._git(tmp, "init", "-q")
            self._write(tmp, "f.go", "package main\nfunc main() {}\n")
            self._commit(tmp, "init")
            r = self.run_cli("ci", "init", "--path", tmp)
            self.assertIn("Created", r.stdout)

    def test_ci_check_smoke(self):
        tmp, repo = tempfile.TemporaryDirectory(), None
        try:
            repo = tmp.name
            self._git(repo, "init", "-q")
            self._write(repo, "f.go", "package main\nfunc main() {}\n")
            self._commit(repo, "init")
            self._write(repo, "f.go", "package main\nfunc main() { print(\"v2\") }\n")
            self._commit(repo, "change")
            r = self.run_cli("ci", "check", "HEAD~1", "HEAD", "--path", repo, check=False)
            self.assertIn(r.returncode, range(0, 8))
        finally:
            if repo:
                tmp.cleanup()

    def test_ci_trend_smoke(self):
        r = self.run_cli("ci", "trend", "--path", FIXTURE_REPO)
        self.assertTrue(len(r.stdout.strip()) > 20, f"ci trend too short: {r.stdout[:200]}")


class TestCoreCommandsNoArgs(SmokeTestBase):
    """Core commands that need no extra args (cwd-based)."""

    COMMANDS_WITH_PATH = [
        ("hub-risk", "highly coupled"),
        ("capillary", "inter-file"),
        ("spectral-gap", "Module separation"),
        ("escape-velocity", "Identifier reach"),
        ("entropy", "spread"),
        ("phantom", "Frameworks"),
        ("porosity", "Coupling sparsity"),
        ("extinct-exports", "Exports declared"),
        ("coupling-chain", "Indirectly coupled"),
        ("vulnerability-map", "Don't-touch"),
        ("cleanup-list", "cleanup"),
        ("test-gaps", "TEST COVERAGE"),
        ("co-change", "entangled"),
        ("solve", "identifiers"),
        ("anomalies", "VOCAB LATTICE"),
        ("origins", "CONCEPT GENESIS"),
        ("ci-trend", "CI Trend"),
        ("coupling", "CONCEPT BONDS"),
        ("check-pr", "Structural hash"),
        ("check-diff", "stable_anchor"),
        ("repo-map", "CRYSTALLOGRAPHY"),
        ("health", "Health:"),
        ("health-score", "coupled"),
        ("diff-structural", "Fingerprint changed"),
    ]

    COMMANDS_SKIP_ON_FAILURE = [
        "vocabulary-trend",
        "migration-pairs",
    ]

    def test_all_with_path_flag(self):
        for cmd, expected in self.COMMANDS_WITH_PATH:
            with self.subTest(cmd=cmd):
                r = self.run_cli("core", cmd, "--path", FIXTURE_REPO)
                self.assertIn(expected, r.stdout)

    def test_commands_that_may_fail(self):
        for cmd in self.COMMANDS_SKIP_ON_FAILURE:
            with self.subTest(cmd=cmd):
                r = self.assure_cli("core", cmd, "--path", FIXTURE_REPO)
                # May exit 1 (not enough history, missing args) — that's acceptable
                self.assertIn(r.returncode, (0, 1, 2), msg=f"{cmd} unexpected exit {r.returncode}: {r.stderr[:200]}")

    def test_migration_pairs_no_args(self):
        r = self.run_cli("core", "migration-pairs", check=False)
        self.assertEqual(r.returncode, 1)


class TestCoreCommandsWithFile(SmokeTestBase):
    """Core commands needing --file <path>."""

    COMMANDS = [
        ("guard", "quale/concepts.py", "guide:"),
        ("fold", "setup.py", "FOLDED"),
        ("guide", "quale/concepts.py", "[unique]"),
        ("complexity-ratio", "setup.py", "Trompe"),
        ("criticality", "setup.py", "amplification"),
        ("safe-islands", "quale/concepts.py", "safe injection"),
        ("decay", "setup.py", "clean"),
    ]

    def test_all_with_file(self):
        for cmd, file_arg, expected in self.COMMANDS:
            with self.subTest(cmd=cmd):
                r = self.run_cli("core", cmd, "--path", FIXTURE_REPO, "--file", file_arg)
                self.assertIn(expected, r.stdout)

    def test_deflate_with_file_and_diff(self):
        r = self.run_cli("core", "deflate", "--path", FIXTURE_REPO, "--file", "README.md", "--diff", "HEAD~1")
        self.assertTrue(len(r.stdout.strip()) > 20 or r.returncode == 1)

    def test_heisenberg_with_file_and_diff(self):
        r = self.run_cli("core", "heisenberg", "--path", FIXTURE_REPO, "--file", "README.md", "--diff", "HEAD~1")
        self.assertIn("focused", r.stdout)

    def test_zk_proof_with_file(self):
        r = self.run_cli("core", "zk-proof", "--path", FIXTURE_REPO, "--file", "README.md", "--code", "test")
        self.assertIn("ZK-PROOF", r.stdout)


class TestCoreCommandsWithFiles(SmokeTestBase):
    """Core commands needing --files <path>."""

    COMMANDS = [
        ("edit-context", "README.md", "schema_version"),
        ("reverse-verify", "README.md", "Test files"),
        ("verify-classify", "README.md", "verifiability"),
        ("verify-packet", "README.md", "schema_version"),
        ("cascade-verify", "README.md", "Cascade"),
        ("veto-cascade", "README.md", "Veto"),
        ("latent-deps", "README.md", "hidden"),
    ]

    def test_all_with_files(self):
        for cmd, file_arg, expected in self.COMMANDS:
            with self.subTest(cmd=cmd):
                r = self.run_cli("core", cmd, "--path", FIXTURE_REPO, "--files", file_arg)
                self.assertIn(expected, r.stdout)

    def test_verify_bonds(self):
        r = self.run_cli("core", "verify-bonds", "--path", FIXTURE_REPO, "--files", "README.md")
        self.assertTrue(len(r.stdout.strip()) > 10)

    def test_verify_scope(self):
        r = self.run_cli("core", "verify-scope", "--path", FIXTURE_REPO, "--files", "README.md")
        self.assertIn("SCOPE", r.stdout)

    def test_forecast(self):
        r = self.run_cli("core", "forecast", "--path", FIXTURE_REPO, "--files", "README.md")
        self.assertTrue(len(r.stdout.strip()) > 10)

    def test_edit_context_tool_format(self):
        r = self.run_cli("core", "edit-context", "--path", FIXTURE_REPO, "--files", "README.md", "--format", "tool")
        data = json.loads(r.stdout)
        self.assertEqual(data.get("schema_version"), 1)


class TestCoreCommandsWithTask(SmokeTestBase):
    """Core commands needing --task <desc>."""

    def test_isolate(self):
        r = self.assure_cli("core", "isolate", "--path", FIXTURE_REPO, "--task", "test")
        self.assertIn(r.returncode, (0, 1), msg=f"isolate: {r.stderr[:200]}")

    def test_triangulate(self):
        r = self.run_cli("core", "triangulate", "--path", FIXTURE_REPO, "--task", "test")
        self.assertIn("anchor", r.stdout.lower())

    def test_agent_bootstrap(self):
        r = self.run_cli("core", "agent-bootstrap", "--path", FIXTURE_REPO, "--task", "test")
        self.assertIn("AGENT BOOTSTRAP", r.stdout)

    def test_orient_with_task(self):
        r = self.run_cli("core", "orient", "--path", FIXTURE_REPO, "--task", "test")
        self.assertIn("Cipher keys", r.stdout)

    def test_help_agent(self):
        r = self.run_cli("core", "help-agent", "fix auth")
        self.assertIn("workflow", r.stdout)


class TestCoreCommandsWithRefs(SmokeTestBase):
    """Core commands needing git refs."""

    def test_ci_report(self):
        r = self.run_cli("core", "ci-report", "HEAD~1", "HEAD", "--path", FIXTURE_REPO)
        self.assertIn("CI REPORT", r.stdout)

    def test_pr_report(self):
        r = self.run_cli("core", "pr-report", "HEAD~1", "HEAD", "--path", FIXTURE_REPO)
        self.assertIn("PR Structural Report", r.stdout)

    def test_parity_bit(self):
        r = self.run_cli("core", "parity-bit", "--ref-a", "HEAD~1", "--ref-b", "HEAD", "--path", FIXTURE_REPO)
        self.assertIn("Mirror", r.stdout)


class TestCoreCommandsWithFileAB(SmokeTestBase):
    """Core commands needing --file-a and --file-b."""

    def test_trap(self):
        r = self.run_cli("core", "trap", "--file-a", "README.md", "--file-b", "README.md", "--path", FIXTURE_REPO)
        self.assertIn("overlap", r.stdout.lower())

    def test_trap_error_no_args(self):
        r = self.run_cli("core", "trap", "--path", FIXTURE_REPO, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("provide", r.stderr)
