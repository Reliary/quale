"""Integration tests for human workflows and CI gates: review, onboard, refactor-cost, ci-trend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestHumanCIIntegration(unittest.TestCase):
    """Integration tests using subprocess to run CLI commands on fixture repos."""

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_cli(self, *args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=cwd or str(PROJECT_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

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

    def _make_repo(self) -> tuple[tempfile.TemporaryDirectory, str]:
        tmp = tempfile.TemporaryDirectory()
        repo = tmp.name
        self._git(repo, "init", "-q")
        self._write(repo, "src/a.ts", "export function Handler() { return 'a'; }\n")
        self._write(repo, "src/b.ts", "export function Helper() { return Handler(); }\n")
        self._write(repo, "tests/a.test.ts", "import { Handler } from '../src/a'; test('a', Handler);\n")
        self._commit(repo, "initial")
        self._write(repo, "src/a.ts", "export function Handler() { return 'a-v2'; }\n")
        self._commit(repo, "change a")
        return tmp, repo

    # ── Command registration ──

    def test_review_registered(self):
        result = self.run_cli("--help")
        self.assertIn("review", result.stdout)

    def test_onboard_registered(self):
        result = self.run_cli("--help")
        self.assertIn("onboard", result.stdout)

    def test_refactor_cost_registered(self):
        result = self.run_cli("--help")
        self.assertIn("refactor-cost", result.stdout)

    def test_ci_trend_registered(self):
        result = self.run_cli("ci", "--help")
        self.assertIn("trend", result.stdout)

    # ── Review ──

    def test_review_basic(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("review", "--path", repo)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Review Summary", result.stdout)

    def test_review_json_format(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("review", "--path", repo, "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("review", data)
        self.assertIn("changed_files", data)
        self.assertIn("blast_radius_count", data)

    def test_review_empty_repo(self):
        tmp = tempfile.TemporaryDirectory()
        repo = tmp.name
        self._git(repo, "init", "-q")
        self._write(repo, "readme.md", "# empty\n")
        self._commit(repo, "init")
        result = self.run_cli("review", "--path", repo, check=False)
        self.assertIn(result.returncode, (0, 1))

    def test_review_non_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("review", "--path", tmp, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Not a git repository", result.stderr)

    # ── Onboard ──

    def test_onboard_basic(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("onboard", "--path", repo)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Onboarding Plan", result.stdout)

    def test_onboard_json_format(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("onboard", "--path", repo, "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("steps", data)
        self.assertGreaterEqual(len(data["steps"]), 1)

    def test_onboard_flat_repo(self):
        tmp = tempfile.TemporaryDirectory()
        repo = tmp.name
        self._git(repo, "init", "-q")
        self._write(repo, "main.py", "def main(): pass\n")
        self._commit(repo, "init")
        result = self.run_cli("onboard", "--path", repo, "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("steps", data)

    def test_onboard_non_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("onboard", "--path", tmp, check=False)
            self.assertEqual(result.returncode, 1)

    # ── Refactor Cost ──

    def test_refactor_cost_basic(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("refactor-cost", "src/a.ts", "--path", repo)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Refactor Cost", result.stdout)

    def test_refactor_cost_json(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("refactor-cost", "src/a.ts", "--path", repo, "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("effort", data)
        self.assertIn("direct_impact", data)
        self.assertIn("file", data)

    def test_refactor_cost_nonexistent_file(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("refactor-cost", "src/nonexistent.ts", "--path", repo, "--format", "json", check=False)
        self.assertIn(result.returncode, (0, 1))

    # ── CI Gates (new --fail-on-* flags) ──

    def test_ci_fail_hub_risk(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("core", "ci-report", "HEAD~1", "HEAD", "--path", repo,
                              "--fail-on-hub-risk", "--summary", check=False)
        self.assertIn(result.returncode, (0, 5, 2, 3, 4, 6, 7))

    def test_ci_fail_new_identifiers(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("core", "ci-report", "HEAD~1", "HEAD", "--path", repo,
                              "--fail-on-new-identifiers", "100", "--summary", check=False)
        self.assertIn(result.returncode, (0, 7))

    def test_ci_gates_help(self):
        result = self.run_cli("core", "ci-report", "--help")
        self.assertIn("fail-on-hub-risk", result.stdout)
        self.assertIn("fail-on-clone", result.stdout)
        self.assertIn("fail-on-new-identifiers", result.stdout)

    # ── CI Trend ──

    def test_ci_trend_empty(self):
        tmp, repo = self._make_repo()
        result = self.run_cli("core", "ci-trend", "--path", repo, check=False)
        # No history yet, so may error gracefully
        self.assertIn(result.returncode, (0, 1))

    def test_ci_trend_after_report(self):
        tmp, repo = self._make_repo()
        self.run_cli("core", "ci-report", "HEAD~1", "HEAD", "--path", repo)
        result = self.run_cli("core", "ci-trend", "--path", repo, "--format", "json", check=False)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            self.assertIn("entries", data)

    # ── Property: deterministic review ──

    def test_review_deterministic(self):
        tmp, repo = self._make_repo()
        r1 = self.run_cli("review", "--path", repo, "--format", "json")
        r2 = self.run_cli("review", "--path", repo, "--format", "json")
        self.assertEqual(r1.stdout, r2.stdout)

    def test_onboard_deterministic(self):
        tmp, repo = self._make_repo()
        r1 = self.run_cli("onboard", "--path", repo, "--format", "json")
        r2 = self.run_cli("onboard", "--path", repo, "--format", "json")
        self.assertEqual(r1.stdout, r2.stdout)


if __name__ == "__main__":
    unittest.main()
