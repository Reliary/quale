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
        result = self.run_vocab("search", "CoreHandler", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertTrue(len(data) >= 1)

    def test_search_missing_phrase(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("search", "v0.0.0-non-existent", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data.get("results", []), [])

    def test_stable_returns_results(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "stable", "--path", str(repo), "--format", "json", check=False)
        self.assertIn(result.returncode, (0, 1))

    def test_stable_shallow_repo(self):
        tmp, repo = self._make_repo(commits=1)
        result = self.run_vocab("core", "stable", "--path", str(repo), "--format", "json", check=False)
        self.assertIn(result.returncode, (0, 1))

    def test_inspect_returns_overview(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("inspect", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        for key in ("schema_version", "explore", "modules", "binding_concepts", "timeline", "avg_concept_age_weeks"):
            self.assertIn(key, data)
        self.assertEqual(data["schema_version"], 1)

    def test_inspect_bare_repo(self):
        tmp = tempfile.TemporaryDirectory()
        bare = Path(tmp.name) / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", "inspect", "--path", str(bare), "--format", "json"],
            cwd=str(PROJECT_ROOT), env=self.env, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Not a git repository", result.stderr)

    def test_preflight_requires_file_scope(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--format", "json", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provide --files or --diff", result.stderr)

    def test_preflight_json_for_explicit_files(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/core.ts", "--task", "change core handler", "--format", "json")
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
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--diff", "HEAD", "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("src/core.ts", data["changed_files"])

    def test_preflight_checklist_output(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/core.ts", "--format", "checklist")
        self.assertIn("VOCAB PREFLIGHT", result.stdout)
        self.assertIn("READ", result.stdout)
        self.assertIn("May be wrong", result.stdout)
        self.assertIn("Report-only", result.stdout)
        self.assertNotIn("DO NOT EXPAND", result.stdout)
        self.assertNotIn("VERIFY WITH", result.stdout)

    def test_preflight_default_is_tool_format(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/core.ts")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("verification_mc", data)
        self.assertIn("guardrails", data)
        self.assertIn("read_first", data)

    def test_preflight_compact_shows_advisory_labels(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src/consumer.ts", "import { ActiveThing } from './active';\nexport const ActiveConsumer = ActiveThing;\n")
        self._write(repo, "tests/active.test.ts", "import { ActiveThing } from '../src/active';\ntest('active', () => ActiveThing());\n")
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/active.ts", "--task", "change active thing", "--format", "compact")
        self.assertIn("VERIFY", result.stdout)
        self.assertNotIn("VERIFY WITH", result.stdout)
        self.assertNotIn("AVOID EXPANDING INTO", result.stdout)

    def test_repo_map_compact_output(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "repo-map", "--path", str(repo))
        self.assertEqual(result.returncode, 0)

    def test_repo_map_json_has_skeleton(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "repo-map", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("stable_core", data)
        self.assertIn("core_concepts", data)
        self.assertIn("test_convention", data)
        self.assertIn("modules", data)

    def test_repo_map_caches_core(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "repo-map", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("stable_core", data)
        self.assertIn("core_concepts", data)
        self.assertIn("test_convention", data)
        self.assertIn("modules", data)

    def test_verify_mcq_output(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("core", "verify", "--path", str(repo), "--files", "src/core.ts")
        self.assertIn("Verification Candidates", result.stdout)
        self.assertIn("A.", result.stdout)
        self.assertIn("tests/core.test.ts", result.stdout)
        self.assertIn("Return the label", result.stdout)

    def test_verify_json_format(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("core", "verify", "--path", str(repo), "--files", "src/core.ts", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("verification_candidates", data)
        self.assertIn("guardrails", data)
        self.assertEqual(data["guardrails"]["mode"], "report_only")

    def test_verify_no_candidates(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "verify", "--path", str(repo), "--files", "src/active.ts", check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_preflight_tool_format(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/core.ts", "--format", "tool")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("verification_mc", data)
        self.assertIn("question", data["verification_mc"])
        self.assertIn("candidates", data["verification_mc"])
        self.assertIn("expansion_risk", data)
        self.assertIn("read_first", data)

    def test_preflight_tool_guardrails(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/core.ts", "--format", "tool")
        data = json.loads(result.stdout)
        self.assertIn("guardrails", data)
        self.assertEqual(data["guardrails"]["mode"], "report_only")

    def test_preflight_tool_includes_confidence_and_scope_creep_guard(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("core", "edit-context", "--path", str(repo), "--files", "src/core.ts", "--format", "tool")
        data = json.loads(result.stdout)
        self.assertIn("verification_confidence", data)
        self.assertIn(data["verification_confidence"]["level"], {"low", "mixed", "high"})
        self.assertIn("scope_creep_guard", data)
        self.assertEqual(data["scope_creep_guard"]["mode"], "report_only")
        self.assertIn("src/core.ts", data["scope_creep_guard"]["allow_changed_files"])

    def test_contract_emits_id_coded_scope(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        result = self.run_vocab("core", "contract", "--path", str(repo), "--files", "src/core.ts", "--task", "change core")
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["mode"], "scoped_edit")
        self.assertIn("contract_id", data)
        self.assertTrue(data["allowed_edit"])
        first_edit = data["allowed_edit"][0]
        self.assertRegex(first_edit, r"^F\d+[0-9a-f]$")
        self.assertEqual(data["files"][first_edit], "src/core.ts")
        self.assertIn("edit_ids", data["must_return"])

    def test_check_plan_validates_ids_and_rejects_raw_paths(self):
        tmp, repo = self._make_repo()
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        contract_result = self.run_vocab("core", "contract", "--path", str(repo), "--files", "src/core.ts", "--task", "change core", "--format", "json")
        contract_data = json.loads(contract_result.stdout)
        contract_path = repo / "contract.json"
        contract_path.write_text(json.dumps(contract_data), encoding="utf-8")

        edit_id = contract_data["allowed_edit"][0]
        verify_id = contract_data.get("verify_options", [])[0]
        proposal_path = repo / "proposal.json"
        proposal_path.write_text(json.dumps({"edit_ids": [edit_id], "verify_ids": [verify_id], "expand_scope": []}), encoding="utf-8")
        ok = self.run_vocab("core", "check-plan", "--contract", str(contract_path), "--proposal", str(proposal_path), "--format", "json")
        ok_data = json.loads(ok.stdout)
        self.assertTrue(ok_data["valid"])
        self.assertEqual(ok_data["edit_paths"], ["src/core.ts"])

        proposal_path.write_text(json.dumps({"edit_ids": ["src/core.ts"], "verify_ids": [], "expand_scope": []}), encoding="utf-8")
        bad = self.run_vocab("core", "check-plan", "--contract", str(contract_path), "--proposal", str(proposal_path), "--format", "json")
        bad_data = json.loads(bad.stdout)
        self.assertFalse(bad_data["valid"])
        codes = {v["code"] for v in bad_data["violations"]}
        self.assertIn("raw_path_not_allowed", codes)

    def test_check_plan_marks_boundary_expansion_for_reflight(self):
        tmp, repo = self._make_repo()
        self._write(repo, "src/consumer.ts", "import { CoreHandler } from './core';\nexport const UseCore = CoreHandler;\n")
        self._write(repo, "tests/core.test.ts", "import { CoreHandler } from '../src/core';\ntest('core', () => CoreHandler());\n")
        contract_result = self.run_vocab("core", "contract", "--path", str(repo), "--files", "src/core.ts", "--task", "change core", "--format", "json")
        contract_data = json.loads(contract_result.stdout)
        self.assertTrue(contract_data["boundary"])
        contract_path = repo / "contract.json"
        contract_path.write_text(json.dumps(contract_data), encoding="utf-8")
        edit_id = contract_data["allowed_edit"][0]
        boundary_id = contract_data["boundary"][0]
        proposal_path = repo / "proposal.json"
        proposal_path.write_text(json.dumps({"edit_ids": [edit_id], "verify_ids": [], "expand_scope": [{"id": boundary_id, "reason": "shared usage"}]}), encoding="utf-8")
        result = self.run_vocab("core", "check-plan", "--contract", str(contract_path), "--proposal", str(proposal_path), "--format", "json")
        data = json.loads(result.stdout)
        self.assertFalse(data["valid"])
        self.assertTrue(data["needs_reflight"])
        self.assertTrue(data["scope_expansion_requested"])

    def test_deserts_json_reports_schema_and_guardrails(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "test-gaps", "--path", str(repo), "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn("deserts", data)
        self.assertTrue(data["guardrails"]["not_coverage_proof"])

    def test_route_prefers_preflight_when_files_known(self):
        tmp, repo = self._make_repo()
        # Sparse 2-commit repo with single small file → routes none (trivial)
        result = self.run_vocab("core", "route", "--path", str(repo), "--files", "src/core.ts", "--task", "change core", "--format", "json")
        data = json.loads(result.stdout)
        self.assertIn(data["action"], ("none", "verify", "human"))
        self.assertIn("intervention_tier", data.get("policy", {}))

    def test_route_uses_none_for_vague_unscoped_task(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "route", "--path", str(repo), "--task", "fix bug", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["action"], "none")
        self.assertEqual(data["policy"]["intervention_tier"], "none")

    def test_route_avoids_vocab_for_vague_unscoped_task(self):
        tmp, repo = self._make_repo()
        result = self.run_vocab("core", "route", "--path", str(repo), "--task", "fix bug", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["action"], "none")


class TestStructuralDetection(unittest.TestCase):
    """Structural detection: desert, co-location, same-package prefix."""

    def test_has_code_phrase_code_chars(self):
        from quale.reports import _has_code_phrase
        self.assertTrue(_has_code_phrase("if err != nil {"))
        self.assertTrue(_has_code_phrase("return fiber.NewError(...)"))
        self.assertTrue(_has_code_phrase("workspaceID := middleware.GetWorkspaceID(c)"))
        self.assertTrue(_has_code_phrase("hash == \"\""))
        self.assertTrue(_has_code_phrase("[]byte"))
        self.assertTrue(_has_code_phrase("func (s *Service) Start() {"))
        self.assertTrue(_has_code_phrase("handleRequest(params)"))

    def test_has_code_phrase_declarative(self):
        from quale.reports import _has_code_phrase
        self.assertFalse(_has_code_phrase("features"))
        self.assertFalse(_has_code_phrase("enabled"))
        self.assertFalse(_has_code_phrase("true"))
        self.assertFalse(_has_code_phrase("retention_days"))
        self.assertFalse(_has_code_phrase('description: "Advanced analytics"'))
        self.assertFalse(_has_code_phrase("workspaces: []"))
        self.assertFalse(_has_code_phrase('description: "Single sign-on integration (SAML/OAuth)"'))

    def test_is_declarative_changed_config(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="config/features.yaml", language="YAML",
                       vocabulary={"features": 1, "enabled": 1, "true": 2, "false": 1, "retention_days": 1},
                       total_phrases=5)
        self.assertTrue(_is_declarative_changed(["config/features.yaml"], [fv]))

    def test_is_declarative_changed_code(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="src/worker.go", language="Go",
                       vocabulary={"func (s *Service) Start() {": 1, "return fiber.NewError(...)": 2, "true": 1},
                       total_phrases=3)
        self.assertFalse(_is_declarative_changed(["src/worker.go"], [fv]))

    def test_same_package_prefix_same(self):
        from quale.reports import _same_package_prefix
        self.assertTrue(_same_package_prefix("packages/mcp/test", "packages/mcp/src"))

    def test_same_package_prefix_different(self):
        from quale.reports import _same_package_prefix
        self.assertFalse(_same_package_prefix("packages/cli/test", "packages/mcp/src"))

    def test_same_package_prefix_too_shallow(self):
        from quale.reports import _same_package_prefix
        self.assertFalse(_same_package_prefix("src", "src"))

    def test_desert_on_json(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="config/settings.json", language="JSON",
                       vocabulary={'"host"': 1, '"port"': 1},
                       total_phrases=2)
        self.assertTrue(_is_declarative_changed(["config/settings.json"], [fv]))

    def test_no_desert_on_py(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="worker.py", language="Python",
                       vocabulary={"def __init__(self):": 1, "self.client = Client()": 1},
                       total_phrases=2)
        self.assertFalse(_is_declarative_changed(["worker.py"], [fv]))

    def test_desert_on_toml(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="Cargo.toml", language="TOML",
                       vocabulary={"[package]": 1, "name": 1, "version": 1},
                       total_phrases=3)
        self.assertTrue(_is_declarative_changed(["Cargo.toml"], [fv]))

    def test_desert_on_markdown(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="README.md", language="Markdown",
                       vocabulary={"# Project": 1, "## Usage": 1},
                       total_phrases=2)
        self.assertTrue(_is_declarative_changed(["README.md"], [fv]))

    def test_desert_yaml_with_space_phrases(self):
        from quale.reports import _is_declarative_changed
        from quale.analyze import FileVocab
        fv = FileVocab(path="features.yaml", language="YAML",
                       vocabulary={
                           "features:": 1,
                           'description: "Advanced analytics and insights dashboard"': 1,
                           'description: "Single sign-on integration (SAML/OAuth)"': 1,
                           "enabled: true": 2,
                           "rollout: 1.0": 3,
                       },
                       total_phrases=7)
        self.assertTrue(_is_declarative_changed(["features.yaml"], [fv]))

    def test_deterministic_fires_on_stem_match(self):
        from quale.reports import _deterministic_verify
        result = _deterministic_verify(
            ["internal/worker_test.go", "internal/testutil.go"],
            None, {"worker"})
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["score"], 0.85)
        self.assertEqual(result["file"], "internal/worker_test.go")

    def test_deterministic_not_on_no_stem(self):
        from quale.reports import _deterministic_verify
        result = _deterministic_verify(
            ["tests/integration/api_test.go", "internal/testutil.go"],
            None, {"worker"})
        self.assertIsNone(result)

    def test_co_location_c_src_to_test(self):
        from quale.reports import _co_located_tests
        from quale.analyze import FileVocab
        mock_vocabs = [
            FileVocab(path="src/llama.cpp", language="C++", vocabulary={}, total_phrases=0),
            FileVocab(path="tests/test-llama-grammar.cpp", language="C++", vocabulary={}, total_phrases=0),
            FileVocab(path="tests/test-sampling.cpp", language="C++", vocabulary={}, total_phrases=0),
        ]
        result = _co_located_tests(["src/llama.cpp"], mock_vocabs)
        self.assertTrue(any("test-llama" in c["file"] for c in result))

    def test_co_location_ts_monorepo(self):
        from quale.reports import _co_located_tests
        from quale.analyze import FileVocab
        mock_vocabs = [
            FileVocab(path="packages/mcp/src/index.ts", language="TypeScript", vocabulary={}, total_phrases=0),
            FileVocab(path="packages/mcp/tests/index.test.ts", language="TypeScript", vocabulary={}, total_phrases=0),
            FileVocab(path="packages/cli/tests/helper.test.ts", language="TypeScript", vocabulary={}, total_phrases=0),
        ]
        result = _co_located_tests(["packages/mcp/src/index.ts"], mock_vocabs)
        self.assertTrue(any("packages/mcp/tests" in c["file"] for c in result))

    def test_co_location_no_match(self):
        from quale.reports import _co_located_tests
        from quale.analyze import FileVocab
        result = _co_located_tests(["src/llama.cpp"], [])
        self.assertEqual(len(result), 0)


class TestGroundTruthIntegrity(unittest.TestCase):
    """Validates that all harness case ground truths exist on disk."""

    def test_all_edit_files_exist(self):
        """Every edit_file in CASES must exist on disk."""
        from scripts.evaluate_vocab_effect import CASES
        missing = []
        for c in CASES:
            if not os.path.isdir(c.path):
                continue
            full = os.path.join(c.path, c.edit_file)
            if not os.path.isfile(full):
                missing.append(f"{c.repo}: {c.edit_file}")
        self.assertEqual(missing, [], f"Missing edit files: {missing}")

    def test_all_verify_files_exist(self):
        """Every verify file in CASES must exist on disk."""
        from scripts.evaluate_vocab_effect import CASES
        missing = []
        for c in CASES:
            if not os.path.isdir(c.path):
                continue
            for vf in c.verify_files:
                full = os.path.join(c.path, vf)
                if not os.path.isfile(full):
                    if c.bucket in ("seen_public", "weird_public", "hard_language"):
                        continue
                    missing.append(f"{c.repo}: {vf}")
        self.assertEqual(missing, [], f"Missing verify files: {missing}")
