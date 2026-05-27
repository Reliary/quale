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
            [sys.executable, "-m", "quale.cli", *args],
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
        result = self.run_vocab("core", "--help")
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
            result = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "fix upload handler", "--format", "json")
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

    def test_checklist_with_task(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            result = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "fix upload handler", "--format", "checklist")
            self.assertIn("EXECUTABLE CHECKLIST", result.stdout)
            self.assertIn("[1] READ", result.stdout)
            self.assertIn("[2]", result.stdout)
            for label in ("READ", "EDIT", "VERIFY", "PREREQ"):
                if label in ("PREREQ",):
                    continue
                self.assertIn(f"[", result.stdout)

    def test_checklist_no_task(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            result = self.run_vocab("core", "agent-bootstrap", str(repo), "--format", "checklist")
            self.assertIn("EXECUTABLE CHECKLIST", result.stdout)
            self.assertIn("CONTEXT", result.stdout)
            self.assertNotIn("CCONTEXT", result.stdout)  # no stray artifact

    def test_checklist_json_structure(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            json_result = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "fix upload handler", "--format", "json")
            data = json.loads(json_result.stdout)
            self.assertIn("binding_concepts", data)
            self.assertIn("schema_version", data)
            self.assertIn("task_plan", data)

    def test_ci_report_json_schema_and_gate_exit_codes(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            result = self.run_vocab("core", "ci-report", "HEAD~1", "HEAD", "--path", str(repo), "--format", "json")
            data = json.loads(result.stdout)
            for field in (
                "mirror_gap_ratio",
                "max_blast_tier",
                "stable_touched_count",
                "blast_tier_counts",
            ):
                self.assertIn(field, data)

            mirror_fail = self.run_vocab("core", "ci-report", "HEAD~1", "HEAD", "--path", str(repo),
                "--fail-on-mirror-gap", "2.0", "--summary",
                check=False,
            )
            self.assertEqual(mirror_fail.returncode, 4)
            self.assertIn("FAIL", mirror_fail.stdout)

            blast_fail = self.run_vocab("core", "ci-report", "HEAD~1", "HEAD", "--path", str(repo),
                "--fail-on-blast-tier", "local", "--summary",
                check=False,
            )
            self.assertEqual(blast_fail.returncode, 2)
            self.assertIn("FAIL", blast_fail.stdout)

            invalid_ref = self.run_vocab("core", "ci-report", "NO_SUCH_REF", "HEAD", "--path", str(repo), "--format", "json",
                check=False,
            )
            self.assertEqual(invalid_ref.returncode, 1)
            self.assertIn("Unknown git ref", invalid_ref.stderr)

    def test_restored_commands_have_basic_behavior(self) -> None:
        with self.make_repo() as tmp:
            repo = Path(tmp)
            self.assertIn("schema_version", self.run_vocab("core", "modules", str(repo), "--format", "json").stdout)
            self.assertIn("alignment", self.run_vocab("core", "compare", str(repo), str(repo), "--format", "json").stdout)
            self.assertIn("history", self.run_vocab("core", "provenance", "UploadHandler", "--path", str(repo), "--format", "json").stdout)
            self.assertIn("Fingerprint:", self.run_vocab("core", "fingerprint", str(repo / "src/upload.ts")).stdout)
            self.assertIn("commands", self.run_vocab("core", "help-agent", "fix upload handler").stdout)
            self.assertEqual(self.run_vocab("core", "pr-report", "HEAD~1", "HEAD", "--path", str(repo)).returncode, 0)

    def test_working_tree_scan_does_not_follow_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            outside = Path(tmp) / "outside.ts"
            outside.write_text("export const OutsideSecretNeedle = true\n", encoding="utf-8")
            (repo / "link.ts").symlink_to(outside)
            self.write(repo, "src/normal.ts", "export const NormalThing = true\n")
            self.commit(repo, "symlink")

            result = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "OutsideSecretNeedle", "--format", "json"
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["related_files_for_task"], [])

    def test_ref_scan_does_not_read_symlink_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            outside = Path(tmp) / "outside.ts"
            outside.write_text("export const HistoricalOutsideNeedle = true\n", encoding="utf-8")
            (repo / "link.ts").symlink_to(outside)
            self.write(repo, "src/normal.ts", "export const NormalThing = true\n")
            self.commit(repo, "historical symlink")

            result = self.run_vocab("core", "analyze", str(repo), "--ref", "HEAD", "--format", "json")
            self.assertNotIn("HistoricalOutsideNeedle", result.stdout)
            self.assertNotIn(str(outside), result.stdout)

    def test_working_tree_scan_includes_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            self.write(repo, "src/a.ts", "export const CleanNeedle = true\n")
            self.commit(repo, "initial")
            self.write(repo, "src/dirty.ts", "export const DirtyNeedle = true\n")

            result = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "DirtyNeedle", "--format", "json"
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["related_files_for_task"][0]["file"], "src/dirty.ts")

    def test_newline_filenames_survive_git_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            self.write(repo, "src/upload\nnewline.ts", "export const NewlineNeedle = true\n")
            self.commit(repo, "newline filename")

            result = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "NewlineNeedle", "--format", "json"
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["related_files_for_task"][0]["file"], "src/upload\nnewline.ts")

    def test_control_and_leading_space_filenames_survive_git_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            self.write(repo, " leading.ts", "export const LeadingSpaceNeedle = true\n")
            self.write(repo, "src/upload\rcr.ts", "export const CarriageNeedle = true\n")
            self.commit(repo, "odd filenames")

            leading = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "LeadingSpaceNeedle", "--format", "json"
            )
            leading_data = json.loads(leading.stdout)
            self.assertEqual(leading_data["related_files_for_task"][0]["file"], " leading.ts")

            carriage = self.run_vocab("core", "agent-bootstrap", str(repo), "--task", "CarriageNeedle", "--format", "json"
            )
            carriage_data = json.loads(carriage.stdout)
            self.assertEqual(carriage_data["related_files_for_task"][0]["file"], "src/upload\rcr.ts")

    def test_invalid_refs_fail_for_read_commands(self) -> None:
        tmp = self.make_repo()
        repo = tmp.name
        for args in (
            ("core", "ci-report", "HEAD~100", "HEAD", "--path", str(repo), "--format", "json"),
            ("core", "pr-report", "HEAD", "HEAD~100", "--path", str(repo)),
            ("diff", "HEAD~100", "HEAD", "--path", str(repo), "--format", "json"),
        ):
            result = self.run_vocab(*args, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Unknown git ref", result.stderr)

    def test_bare_repositories_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "bare.git"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
            for args in (
                ("core", "agent-bootstrap", str(bare), "--format", "json"),
                ("core", "analyze", str(bare), "--format", "json"),
                ("core", "ci-report", "HEAD", "HEAD", "--path", str(bare), "--format", "json"),
            ):
                result = self.run_vocab(*args, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Not a git repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
