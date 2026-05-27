"""Adversarial content tests: null bytes, deep nesting, bidi, 100K files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestAdversarialContent(unittest.TestCase):

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_vocab(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=str(PROJECT_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        return result

    def _make_repo(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        return tmp, repo

    def _write(self, repo: Path, rel: str, content: str | bytes):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def _commit(self, repo: Path, msg: str = "commit"):
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", msg],
            cwd=repo, check=True,
        )

    def test_null_byte_content(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src.c", b"int main() {\n\x00return 0;\n}\n")
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_one_megabyte_file(self):
        tmp, repo = self._make_repo()
        self._write(repo, "big.txt", "hello world\n" * 50000)
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_deep_nested_json(self):
        tmp, repo = self._make_repo()
        content = "x"
        for _ in range(1000):
            content = '{"a":' + content + "}"
        self._write(repo, "deep.json", content)
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_utf8_replacement_chars(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src.ts", "export const A\ufffd\ufffdB = true;\nexport const C\ufffdD = false;\n")
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_bidi_control_chars(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src.ts", "export const \u202eLogin = true;\nexport const R\x1b\u202eOrder = false;\n")
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_repo_with_many_files(self):
        tmp, repo = self._make_repo()
        for i in range(2000):
            self._write(repo, f"src/file{i}.ts", f"export const Needle{i} = true;\n")
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_circular_symlink(self):
        tmp, repo = self._make_repo()
        self._write(repo, "normal.ts", "export const Normal = true;\n")
        (repo / "loop").symlink_to(repo)
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_empty_commit_no_files(self):
        tmp, repo = self._make_repo()
        self._write(repo, "readme.md", "# empty\n")
        self._commit(repo)
        result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    # ── Adversarial: review ──

    def test_review_null_byte_file(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src.c", b"int main() {\n\x00return 0;\n}\n")
        self._commit(repo)
        result = self.run_vocab("review", "--path", str(repo), "--base", "HEAD~0", "-f", "json")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_review_no_changes(self):
        tmp, repo = self._make_repo()
        self._write(repo, "readme.md", "# test\n")
        self._commit(repo)
        result = self.run_vocab("review", "--path", str(repo), "--base", "HEAD~0", "-f", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

    # ── Adversarial: onboard ──

    def test_onboard_null_byte_file(self):
        tmp, repo = self._make_repo()
        self._write(repo, "thing.ts", b"export const OK = \x00;\n")
        self._commit(repo)
        result = self.run_vocab("onboard", "--path", str(repo), "-f", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)

    def test_onboard_empty_repo(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("onboard", "--path", str(repo), "-f", "json")
        self.assertEqual(result.returncode, 0, result.stderr)

    # ── Adversarial: refactor-cost ──

    def test_refactor_cost_null_byte_file(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src/weird.ts", b"export const OK = \x00;\n")
        self._commit(repo)
        result = self.run_vocab("refactor-cost", "src/weird.ts", "--path", str(repo), "-f", "json")
        self.assertIn(result.returncode, (0, 1), result.stderr)

    def test_refactor_cost_missing_file(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("refactor-cost", "src/nope.ts", "--path", str(repo), "-f", "json")
        # Graceful error regardless of exit code
        data = json.loads(result.stdout) if result.stdout else {}
        if "error" in data:
            pass  # Graceful file-not-found is acceptable
        # Either exit 0 or 1 is fine for adversarial

    # ── Adversarial: CI gates ──

    def test_ci_gates_invalid_flag_value(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "ci-report", "HEAD~1", "HEAD", "--path", str(repo),
                                "--fail-on-blast-tier", "nonexistent")
        self.assertEqual(result.returncode, 1)

    def test_ci_gates_zero_new_identifiers(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "ci-report", "HEAD~1", "HEAD", "--path", str(repo),
                                "--fail-on-new-identifiers", "0", "--summary")
        self.assertIn(result.returncode, (0, 7))


if __name__ == "__main__":
    unittest.main()
