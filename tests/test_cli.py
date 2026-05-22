from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VocabCliTests(unittest.TestCase):
    def run_vocab(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "vocab.cli", *args],
            cwd=cwd or PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def git(self, repo: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            self.fail(f"git failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")

    def commit(self, repo: Path, message: str) -> None:
        self.git(repo, "add", ".")
        self.git(
            repo,
            "-c", "user.name=Vocab Test",
            "-c", "user.email=vocab@example.test",
            "commit", "-q", "-m", message,
        )

    def write(self, repo: Path, relative: str, content: str) -> None:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def make_repo(self) -> tempfile.TemporaryDirectory[str]:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        self.git(repo, "init", "-q")
        self.write(repo, "src/upload.ts", "export function UploadHandler() { return 'upload'; }\n")
        self.write(repo, "src/consumer.ts", "import { UploadHandler } from './upload';\nexport const UploadEvidenceConsumer = UploadHandler;\n")
        self.write(repo, "scripts/upload-helper.ts", "export function UploadScript() { return 'upload'; }\n")
        self.write(repo, "examples/upload-example.ts", "export function UploadExample() { return 'upload'; }\n")
        self.write(repo, "tests/upload.test.ts", "import { UploadHandler } from '../src/upload';\ntest('upload', () => UploadHandler());\n")
        self.commit(repo, "initial")
        self.write(repo, "src/upload.ts", "export function UploadHandler() { return 'upload-v2'; }\nexport const UploadEvidenceConsumer = 'gate';\n")
        self.write(repo, "tests/upload.test.ts", "import { UploadHandler } from '../src/upload';\ntest('upload evidence', () => UploadHandler());\n")
        self.commit(repo, "change upload")
        return tmp

    def test_command_registration_keeps_core_surface(self) -> None:
        result = self.run_vocab("--help")
        for command in (
            "agent-bootstrap",
            "ci-report",
            "compare",
            "provenance",
            "fingerprint",
            "modules",
            "help-agent",
            "pr-report",
        ):
            self.assertIn(command, result.stdout)

    def test_agent_bootstrap_prioritizes_source_and_preserves_tests(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            result = self.run_vocab("agent-bootstrap", str(repo), "--task", "fix upload handler", "--format", "json")
            data = json.loads(result.stdout)
            related = data["related_files_for_task"]
            self.assertEqual(related[0]["role"], "source")
            self.assertEqual(related[0]["file"], "src/upload.ts")
            roles = [item["role"] for item in related]
            self.assertIn("script", roles)
            self.assertIn("example", roles)
            self.assertIn("test", roles)
            self.assertEqual(data["task_plan"]["likely_edit_files"][0], "src/upload.ts")
            self.assertIn("task_relevance_score", data)
            self.assertIn("verified_files", data)
            self.assertIn("unverified_files", data)

    def test_ci_report_json_schema_and_gate_exit_codes(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            result = self.run_vocab("ci-report", "HEAD~1", "HEAD", "--path", str(repo), "--format", "json")
            data = json.loads(result.stdout)
            for field in (
                "mirror_gap_ratio",
                "max_blast_tier",
                "stable_touched_count",
                "blast_tier_counts",
            ):
                self.assertIn(field, data)

            mirror_fail = self.run_vocab(
                "ci-report", "HEAD~1", "HEAD", "--path", str(repo),
                "--fail-on-mirror-gap", "2.0", "--summary",
                check=False,
            )
            self.assertEqual(mirror_fail.returncode, 1)
            self.assertIn("FAIL", mirror_fail.stdout)

            blast_fail = self.run_vocab(
                "ci-report", "HEAD~1", "HEAD", "--path", str(repo),
                "--fail-on-blast-tier", "local", "--summary",
                check=False,
            )
            self.assertEqual(blast_fail.returncode, 2)
            self.assertIn("FAIL", blast_fail.stdout)

    def test_restored_commands_have_basic_behavior(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            self.assertIn("schema_version", self.run_vocab("modules", str(repo), "--format", "json").stdout)
            self.assertIn("alignment", self.run_vocab("compare", str(repo), str(repo), "--format", "json").stdout)
            self.assertIn("history", self.run_vocab("provenance", "UploadHandler", "--path", str(repo), "--format", "json").stdout)
            self.assertIn("Fingerprint:", self.run_vocab("fingerprint", str(repo / "src/upload.ts")).stdout)
            self.assertIn("commands", self.run_vocab("help-agent", "fix upload handler").stdout)
            self.assertEqual(self.run_vocab("pr-report", "HEAD~1", "HEAD", "--path", str(repo)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
