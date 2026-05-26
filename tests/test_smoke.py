"""Smoke tests for blast, explore, lifecycle, landmarks, timeline, clone commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestSmoke(unittest.TestCase):

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_vocab(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=str(PROJECT_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def _write(self, repo: Path, rel: str, content: str):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, repo: Path, msg: str):
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", msg],
                       cwd=repo, check=True)

    def _make_repo(self, weeks: int = 12) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        for w in range(weeks):
            for i in range(3):
                self._write(repo, f"src/file{w}_{i}.ts", f"export const W{w}F{i} = {w + i};\n")
            self._write(repo, "src/stable.ts", "export const Stable = 1;\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            self._commit(repo, f"week {w}")
            subprocess.run(
                ["git", "-c", "user.name=T", "-c", "user.email=t@t.test",
                 "commit", "--allow-empty", "-q", "-m", f"marker {w}",
                 "--date", f"{w + 1}.days.ago"],
                cwd=repo, check=True,
            )
        return tmp, repo

    def test_blast_returns_results(self):
        tmp, repo = self._make_repo(weeks=2)
        result = self.run_vocab("blast", "HEAD~1", "HEAD", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("impacts", data)

    def test_explore_returns_results(self):
        tmp, repo = self._make_repo(weeks=2)
        result = self.run_vocab("explore", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("files", data)

    def test_lifecycle_returns_results(self):
        tmp, repo = self._make_repo(weeks=12)
        result = self.run_vocab("lifecycle", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)

    def test_landmarks_returns_results(self):
        tmp, repo = self._make_repo(weeks=2)
        result = self.run_vocab("landmarks", str(repo), check=False)
        self.assertIn(result.returncode, (0, 1))

    def test_lifecycle_returns_results(self):
        tmp, repo = self._make_repo(weeks=12)
        result = self.run_vocab("lifecycle", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("signals", data)

    def test_timeline_returns_results(self):
        tmp, repo = self._make_repo(weeks=12)
        result = self.run_vocab("timeline", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("timeline", data)

    def test_agent_bootstrap_summary(self):
        tmp, repo = self._make_repo(weeks=2)
        result = self.run_vocab("agent-bootstrap", str(repo), "--summary")
        self.assertIn("AGENT BOOTSTRAP", result.stdout)

    def test_agent_bootstrap_no_task(self):
        tmp, repo = self._make_repo(weeks=2)
        result = self.run_vocab("agent-bootstrap", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["task_relevance_score"], 0)
        self.assertEqual(data["related_files_for_task"], [])


if __name__ == "__main__":
    unittest.main()
