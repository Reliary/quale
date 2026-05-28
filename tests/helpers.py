"""Shared test helpers for quale CLI tests."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class QualeTestCase(unittest.TestCase):
    """Base class for CLI integration tests."""

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_quale(
        self, *args: str, check: bool = True, cwd: str | None = None
    ) -> subprocess.CompletedProcess:
        """Run `quale` CLI with given args and return CompletedProcess."""
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

    def git(self, repo: Path, *args: str) -> None:
        """Run git command in repo."""
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            self.fail(f"git failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")

    def commit(self, repo: Path, msg: str, author: str = "T") -> None:
        """Stage all and commit in repo."""
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", f"user.name={author}", "-c", f"user.email={author.lower()}@quale.test",
             "commit", "-q", "-m", msg],
            cwd=repo, check=True, capture_output=True,
        )

    def write(self, repo: Path, rel: str, content: str) -> None:
        """Write file in repo."""
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def make_repo(
        self, files: list[tuple[str, str]] | None = None, commits: int = 2
    ) -> tuple[Path, str]:
        """Create a temporary git repo with optional files.

        Returns (repo_path, tmpdir_name) where repo_path is a Path.
        The tmpdir is cleaned up on test tear-down if added via addCleanup.
        """
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        self.addCleanup(tmp.cleanup)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
        if files:
            for rel, content in files:
                self.write(repo, rel, content)
        else:
            self.write(repo, "src/core.ts", "export function CoreHandler() { return 1; }\n")
            self.write(repo, "src/active.ts", "export function ActiveThing() { return 2; }\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        self.commit(repo, "initial")
        if commits >= 2:
            self.write(repo, "src/core.ts", "export function CoreHandler() { return 1; }\nexport function CoreNew() { return 3; }\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            self.commit(repo, "second")
        return repo, tmp.name
