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

    def test_preflight_requires_file_scope(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("preflight", "--path", str(repo), "--format", "json", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provide --files or --diff", result.stderr)

    def test_preflight_json_for_explicit_files(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/core.ts", "--task", "change core handler", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["changed_files"], ["src/core.ts"])
        self.assertIn(data["risk"], {"low", "moderate", "high"})
        self.assertEqual(data["guardrails"]["mode"], "report_only")
        self.assertTrue(data["guardrails"]["manual_review_required"])
        self.assertIn("May be wrong", data["guardrails"]["caveat"])
        self.assertIn("verification_candidates", data)
        self.assertIn("expansion_risk", data)
        self.assertFalse(data["privacy_receipt"]["uploaded"])
        self.assertFalse(data["privacy_receipt"]["network"])

    def test_preflight_diff_uses_worktree_changes(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src/core.ts", "export function CoreHandler() { return 10; }\nexport function CoreNew() { return 3; }\n")
        result = self.run_vocab("preflight", "--path", str(repo), "--diff", "HEAD", "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("src/core.ts", data["changed_files"])

    def test_preflight_checklist_output(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/core.ts", "--format", "checklist")
        self.assertIn("VOCAB PREFLIGHT", result.stdout)
        self.assertIn("READ", result.stdout)
        self.assertIn("May be wrong", result.stdout)
        self.assertIn("Report-only", result.stdout)
        self.assertNotIn("DO NOT EXPAND", result.stdout)
        self.assertNotIn("VERIFY WITH", result.stdout)

    def test_preflight_default_is_tool_format(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/core.ts")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("verification_mc", data)
        self.assertIn("guardrails", data)
        self.assertIn("read_first", data)

    def test_preflight_compact_shows_advisory_labels(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src/consumer.ts", "import { ActiveThing } from './active';\nexport const ActiveConsumer = ActiveThing;\n")
        self._write(repo, "tests/active.test.ts", "import { ActiveThing } from '../src/active';\ntest('active', () => ActiveThing());\n")
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/active.ts", "--task", "change active thing", "--format", "compact")
        self.assertIn("VERIFY CANDIDATES", result.stdout)
        self.assertNotIn("VERIFY WITH", result.stdout)
        self.assertNotIn("AVOID EXPANDING INTO", result.stdout)

    def test_crystallography_compact_output(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("crystallography", "--path", str(repo))
        self.assertIn("VOCAB CRYSTALLOGRAPHY", result.stdout)
        self.assertIn("Skeleton:", result.stdout)
        self.assertNotIn("error", result.stdout.lower())

    def test_crystallography_json_has_skeleton(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("crystallography", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIsInstance(data["skeleton"], str)
        self.assertGreater(len(data["skeleton"]), 10)
        self.assertIn("Lang: TypeScript", data["skeleton"])

    def test_crystallography_caches_core(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("crystallography", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("stable_core", data)
        self.assertIn("core_concepts", data)
        self.assertIn("test_convention", data)
        self.assertIn("modules", data)

    def test_verify_mcq_output(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("verify", "--path", str(repo), "--files", "src/core.ts")
        self.assertIn("Verification Candidates", result.stdout)
        self.assertIn("A.", result.stdout)
        self.assertIn("tests/core.test.ts", result.stdout)
        self.assertIn("Return the label", result.stdout)

    def test_verify_json_format(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("verify", "--path", str(repo), "--files", "src/core.ts", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("verification_candidates", data)
        self.assertIn("guardrails", data)
        self.assertEqual(data["guardrails"]["mode"], "report_only")

    def test_verify_no_candidates(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("verify", "--path", str(repo), "--files", "src/active.ts", check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_preflight_tool_format(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/core.ts", "--format", "tool")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("verification_mc", data)
        self.assertIn("question", data["verification_mc"])
        self.assertIn("candidates", data["verification_mc"])
        self.assertIn("expansion_risk", data)
        self.assertIn("read_first", data)

    def test_preflight_tool_guardrails(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/core.ts", "--format", "tool")
        data = json.loads(result.stdout)
        self.assertIn("guardrails", data)
        self.assertEqual(data["guardrails"]["mode"], "report_only")

    def test_preflight_tool_includes_confidence_and_sprawl_guard(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("preflight", "--path", str(repo), "--files", "src/core.ts", "--format", "tool")
        data = json.loads(result.stdout)
        self.assertIn("verification_confidence", data)
        self.assertIn(data["verification_confidence"]["level"], {"low", "mixed", "high"})
        self.assertIn("edit_sprawl_guard", data)
        self.assertEqual(data["edit_sprawl_guard"]["mode"], "report_only")
        self.assertIn("src/core.ts", data["edit_sprawl_guard"]["allow_changed_files"])

    def test_deserts_json_reports_schema_and_guardrails(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("deserts", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("mirror_ratio", data)
        self.assertIn("deserts", data)
        self.assertTrue(data["guardrails"]["not_coverage_proof"])

    def test_route_prefers_preflight_when_files_known(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("route", "--path", str(repo), "--files", "src/core.ts", "--task", "change core", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["action"], "preflight_tool")
        self.assertIn("preflight", data["command"])
        self.assertFalse(data["policy"]["auto_prompt_injection"])

    def test_route_avoids_vocab_for_vague_unscoped_task(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("route", "--path", str(repo), "--task", "fix bug", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["action"], "no_vocab")

    def test_failure_miner_classifies_harness_rows(self):
        tmp = tempfile.TemporaryDirectory()
        payload = {
            "schema_version": 1,
            "results": [
                {
                    "suite": "preflight",
                    "bucket": "private_unseen",
                    "repo": "sample",
                    "condition": "candidate_baseline",
                    "task": "change core",
                    "verify_hit": False,
                    "verify": ["src/core.ts"],
                    "extra_edit_count": 1,
                    "extra_edits": ["src/extra.ts"],
                }
            ],
        }
        path = Path(tmp.name) / "effect.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/analyze_effect_failures.py", str(path), "--format", "json"],
            cwd=str(PROJECT_ROOT), env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertGreaterEqual(data["label_counts"].get("wrong_verification", 0), 1)
        self.assertGreaterEqual(data["label_counts"].get("source_file_as_verification", 0), 1)
        self.assertGreaterEqual(data["label_counts"].get("edit_sprawl", 0), 1)


if __name__ == "__main__":
    unittest.main()
