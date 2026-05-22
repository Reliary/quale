"""Test coverage for diff, search, stable, inspect commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestCommandCoverage(unittest.TestCase):

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_vocab(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "vocab.cli", *args],
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

    def _make_repo(self, commits: int = 2) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        self._write(repo, "src/core.ts", "export function CoreHandler() { return 1; }\n")
        self._write(repo, "src/active.ts", "export function ActiveThing() { return 2; }\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        self._commit(repo, "initial")
        if commits >= 2:
            self._write(repo, "src/core.ts", "export function CoreHandler() { return 1; }\nexport function CoreNew() { return 3; }\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            self._commit(repo, "second")
        return tmp, repo

    def test_diff_detects_changes(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("diff", "HEAD~1", "HEAD", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("new", data)

    def test_diff_no_changes(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("diff", "HEAD", "HEAD", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data.get("new", []), [])

    def test_search_finds_phrase(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("search", "CoreHandler", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertTrue(len(data) >= 1)

    def test_search_missing_phrase(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("search", "v0.0.0-non-existent", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data.get("results", []), [])

    def test_stable_returns_results(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("stable", "--path", str(repo), "--format", "json", check=False)
        self.assertIn(result.returncode, (0, 1))

    def test_stable_shallow_repo(self):
        tmp, repo = self._make_repo(commits=1)
        result = self.run_vocab("stable", "--path", str(repo), "--format", "json", check=False)
        self.assertIn(result.returncode, (0, 1))

    def test_inspect_returns_overview(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("inspect", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        for key in ("schema_version", "explore", "modules", "binding_concepts", "timeline", "avg_concept_age_weeks"):
            self.assertIn(key, data)
        self.assertEqual(data["schema_version"], 1)

    def test_inspect_bare_repo(self):
        tmp = tempfile.TemporaryDirectory()
        bare = Path(tmp.name) / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
        result = subprocess.run(
            [sys.executable, "-m", "vocab.cli", "inspect", str(bare), "--format", "json"],
            cwd=str(PROJECT_ROOT), env=self.env, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Not a git repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
