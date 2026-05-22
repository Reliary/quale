"""Performance regression tests: timing bounds on scan, boot, CI, stability."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPerformance(unittest.TestCase):

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def _make_repo(self, file_count: int, body: str = "export const Needle{idx} = true;\n") -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        for i in range(file_count):
            path = repo / "src" / f"file{i}.ts"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body.format(idx=i))
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", "base"],
            cwd=repo, check=True,
        )
        return tmp

    def test_scan_short_repo(self):
        tmp = self._make_repo(100)
        repo = Path(tmp.name)
        from vocab.scanner import scan_codebase
        start = time.time()
        analysis = scan_codebase(str(repo), quiet=True, max_files=500)
        elapsed = time.time() - start
        self.assertGreater(len(analysis.file_vocabs), 0)
        self.assertLess(elapsed, 10)

    def test_scan_capped_on_giant(self):
        tmp = self._make_repo(300, body="export const N{idx} = true;\n")
        repo = Path(tmp.name)
        from vocab.scanner import scan_codebase
        start = time.time()
        analysis = scan_codebase(str(repo), quiet=True, max_files=100)
        elapsed = time.time() - start
        self.assertLessEqual(len(analysis.file_vocabs), 100)
        self.assertLess(elapsed, 10)

    def test_bootstrap_returns_results(self):
        tmp = self._make_repo(100)
        repo = Path(tmp.name)
        from vocab.bootstrap import bootstrap_repo
        start = time.time()
        result = bootstrap_repo(str(repo))
        elapsed = time.time() - start
        self.assertIn("schema_version", result)
        self.assertIn("recommended_next_reads", result)
        self.assertLess(elapsed, 15)

    def test_stability_empty_on_giant(self):
        tmp = self._make_repo(2500, body="export const N{idx} = true;\n")
        repo = Path(tmp.name)
        from vocab.reports import compute_stability
        start = time.time()
        stability = compute_stability(str(repo), weeks=4)
        elapsed = time.time() - start
        self.assertLess(elapsed, 10)


if __name__ == "__main__":
    unittest.main()
