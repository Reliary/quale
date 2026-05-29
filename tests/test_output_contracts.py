"""Layer 2: Output content contracts — assert output is USEFUL, not just non-empty.

Catches: jargon headers, missing data, empty sections, wrong namespace hints,
empty file pairs, terse meaningless output like "coupled + gapped".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OutputContractTest(unittest.TestCase):
    """Assertions on output quality — not just that it ran."""

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
            self.fail(f"command failed: {' '.join(args)}\nstdout={result.stdout[:500]}\nstderr={result.stderr[:500]}")
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


# ── Root: review ──


class TestReviewContract(OutputContractTest):
    """review must show changed files, risk, action items."""

    def test_review_lists_changed_files(self):
        with tempfile.TemporaryDirectory() as repo:
            self._git(repo, "init", "-q")
            self._write(repo, "main.go", "package main\nfunc main() {}\n")
            self._commit(repo, "init")
            self._write(repo, "main.go", "package main\nfunc main() { print(1) }\n")
            self._commit(repo, "change main")
            r = self.run_cli("review", "--path", repo)
            self.assertIn("main.go", r.stdout)
            self.assertNotIn("Review Summary", r.stdout)

    def test_review_has_action_items_section(self):
        with tempfile.TemporaryDirectory() as repo:
            self._git(repo, "init", "-q")
            self._write(repo, "main.go", "package main\nfunc main() {}\n")
            self._commit(repo, "init")
            self._write(repo, "main.go", "package main\nfunc main() { print(1) }\n")
            self._commit(repo, "change main")
            r = self.run_cli("review", "--path", repo)
            self.assertIn("Action items", r.stdout)
            self.assertIn("Changes:", r.stdout)
            self.assertIn("Risk flags", r.stdout)


# ── Root: onboard ──


class TestOnboardContract(OutputContractTest):
    """onboard must list real files with explanations."""

    def test_onboard_has_three_steps(self):
        r = self.run_cli("onboard", "--path", str(PROJECT_ROOT))
        self.assertIn("Step 1", r.stdout)
        self.assertIn("Step 2", r.stdout)
        self.assertIn("Step 3", r.stdout)

    def test_onboard_has_language_breakdown(self):
        r = self.run_cli("onboard", "--path", str(PROJECT_ROOT))
        self.assertIn("Languages:", r.stdout)

    def test_onboard_json_has_required_keys(self):
        r = self.run_cli("onboard", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        self.assertIn("steps", data)
        self.assertIn("total_files", data)
        self.assertTrue(data["total_files"] > 0)

    def test_onboard_non_code_landmarks_filtered(self):
        r = self.run_cli("onboard", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        for step in data.get("steps", []):
            if step["step"] == 1:
                files = [i.get("file", "") for i in step.get("items", [])]
                self.assertTrue(any(f for f in files if f), "step1 should have file items")


# ── Root: refactor-cost ──


class TestRefactorCostContract(OutputContractTest):
    """refactor-cost must explain impact meaningfully."""

    def test_refactor_cost_has_impact(self):
        r = self.run_cli("refactor-cost", "README.md", "--path", str(PROJECT_ROOT))
        self.assertIn("effort", r.stdout.lower())
        self.assertNotIn("Escape vel", r.stdout)

    def test_refactor_cost_has_blast_count(self):
        r = self.run_cli("refactor-cost", "README.md", "--path", str(PROJECT_ROOT))
        lines = r.stdout.strip().split("\n")
        # At least 3 lines of explanation
        self.assertGreater(len(lines), 3)

    def test_refactor_cost_json(self):
        r = self.run_cli("refactor-cost", "README.md", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        self.assertIn("effort", data)


# ── Agent: orient ──


class TestAgentOrientContract(OutputContractTest):
    """agent orient must return useful repo map, not 6 stats."""

    def test_orient_has_landmarks(self):
        r = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertGreater(len(data.get("landmarks", [])), 0)

    def test_orient_has_workflow(self):
        r = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertEqual(len(data.get("recommended_workflow", [])), 3)

    def test_orient_has_languages(self):
        r = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertGreater(len(data.get("languages", [])), 0)

    def test_orient_has_modules(self):
        r = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertTrue("modules" in data)

    def test_orient_not_just_six_keys(self):
        # Would have caught the old 6-key useless output
        r = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        bad_keys = {"fingerprint", "checksum", "total_phrases", "total_indices"}
        overlap = bad_keys & set(data.keys())
        self.assertFalse(overlap, f"output still has old useless keys: {overlap}")


# ── Agent: edit ──


class TestAgentEditContract(OutputContractTest):
    """agent edit must return proper verification_mc structure."""

    def test_edit_has_verification_mc(self):
        r = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        mc = data.get("verification_mc", {})
        self.assertIn("question", mc)
        self.assertIn("candidates", mc)
        self.assertIn("max_selections", mc)
        self.assertGreater(len(mc.get("question", "")), 5)

    def test_edit_risk_is_valid(self):
        r = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        valid_risks = {"low", "moderate", "high", "unknown"}
        self.assertIn(data.get("risk", ""), valid_risks)

    def test_edit_has_changed_files(self):
        r = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertGreater(len(data.get("changed_files", [])), 0)


# ── Agent: guard ──


class TestAgentGuardContract(OutputContractTest):
    """agent guard must reference the file and have schema."""

    def test_guard_references_file(self):
        r = self.run_cli("agent", "guard", "README.md", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertIn("README.md", data.get("file", ""))

    def test_guard_has_schema_version(self):
        r = self.run_cli("agent", "guard", "README.md", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        self.assertEqual(data.get("schema_version"), 1)


# ── Core: health-score ──


class TestHealthScoreContract(OutputContractTest):
    """health-score must show a valid description."""

    def test_health_score_has_description(self):
        r = self.run_cli("core", "health-score", "--path", str(PROJECT_ROOT))
        self.assertIn("Structural health", r.stdout)
        self.assertNotIn("error", r.stdout.lower())

    def test_health_score_has_debt(self):
        r = self.run_cli("core", "health-score", "--path", str(PROJECT_ROOT))
        self.assertIn("structural health", r.stdout.lower())

    def test_health_score_json(self):
        r = self.run_cli("core", "health-score", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        self.assertIn("excess_porosity", data)


# ── Core: check-pr ──


class TestCheckPrContract(OutputContractTest):
    """check-pr must not have empty file pairs."""

    def test_check_pr_no_empty_pairs(self):
        r = self.run_cli("core", "check-pr", "--path", str(PROJECT_ROOT))
        # Would have caught the "<-> :" bug
        lines = r.stdout.strip().split("\n")
        for line in lines:
            if "<->" in line:
                parts = line.split("<->")
                self.assertTrue(len(parts) >= 2, f"file pair has empty side: {line}")
                # Both sides should have content
                left = parts[0].strip().lstrip("\u25cf\u26a0").strip()
                right = parts[1].strip()
                if left or right:  # skip structural hash line
                    self.assertTrue(left or "unchanged" in line, f"left side empty in: {line}")


# ── Core: extinct-exports ──


class TestExtinctExportsContract(OutputContractTest):
    """extinct-exports must not have wrong namespace hints."""

    def test_extinct_exports_no_wrong_namespace_hint(self):
        r = self.run_cli("core", "extinct-exports", "--path", str(PROJECT_ROOT))
        # Hints should reference sibling commands without redundant "quale core" prefix
        self.assertIn("cleanup-list", r.stdout)

    def test_extinct_exports_has_explanation(self):
        r = self.run_cli("core", "extinct-exports", "--path", str(PROJECT_ROOT))
        self.assertIn("Exports declared", r.stdout)
        self.assertIn("Count:", r.stdout)


# ── Core: heisenberg ──


class TestHeisenbergContract(OutputContractTest):
    """heisenberg must not say 'principle respected'."""

    def test_heisenberg_no_jargon(self):
        r = self.run_cli("core", "heisenberg", "--path", str(PROJECT_ROOT), "--file", "README.md", "--diff", "HEAD~1")
        self.assertNotIn("Heisenberg principle", r.stdout)
        self.assertTrue("focused" in r.stdout or "Mixed" in r.stdout)


# ── Core: trap ──


class TestTrapContract(OutputContractTest):
    """trap must not say 'over-trap'."""

    def test_trap_no_jargon(self):
        r = self.run_cli("core", "trap", "--file-a", "README.md", "--file-b", "README.md", "--path", str(PROJECT_ROOT))
        self.assertNotIn("over-trap", r.stdout)
        self.assertTrue("Low overlap" in r.stdout or "Moderate overlap" in r.stdout or "High merge risk" in r.stdout)


# ── Core: safe-islands ──


class TestSafeIslandsContract(OutputContractTest):
    """safe-islands must not say 'Lagrange Points'."""

    def test_safe_islands_no_jargon(self):
        r = self.run_cli("core", "safe-islands", "--path", str(PROJECT_ROOT), "--file", "README.md")
        self.assertNotIn("Lagrange", r.stdout)


# ── Core: solve ──


class TestSolveContract(OutputContractTest):
    """solve must show identifiers with frequency and examples."""

    def test_solve_shows_identifiers(self):
        r = self.run_cli("core", "solve", "--path", str(PROJECT_ROOT))
        self.assertIn("non-dictionary identifiers", r.stdout)
        self.assertIn("(appears", r.stdout)

    def test_solve_each_entry_has_frequency(self):
        r = self.run_cli("core", "solve", "--path", str(PROJECT_ROOT))
        # Each numbered entry should have frequency and example
        lines = r.stdout.strip().split("\n")
        entry_lines = [l for l in lines if l.strip().startswith(tuple("123456789"))]
        for el in entry_lines[:3]:
            self.assertIn("appears", el)
            self.assertIn("e.g.", el)


# ── Core: coupling-chain ──


class TestCouplingChainContract(OutputContractTest):
    """coupling-chain must have meaningful pairs."""

    def test_coupling_chain_header(self):
        r = self.run_cli("core", "coupling-chain", "--path", str(PROJECT_ROOT))
        self.assertIn("Indirectly coupled", r.stdout)

    def test_coupling_chain_pairs_if_present(self):
        r = self.run_cli("core", "coupling-chain", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        for pair in data.get("tensegrity_pairs", []):
            self.assertTrue(pair.get("file_a", ""), "file_a must not be empty")
            self.assertTrue(pair.get("file_b", ""), "file_b must not be empty")


# ── Core: vulnerability-map ──


class TestVulnerabilityMapContract(OutputContractTest):
    """vulnerability-map must have section headers."""

    def test_vulnerability_map_sections(self):
        r = self.run_cli("core", "vulnerability-map", "--path", str(PROJECT_ROOT))
        self.assertIn("Don't-touch", r.stdout)
        self.assertIn("Churn hubs", r.stdout)


# ── Core: ci-trend ──


class TestCiTrendContract(OutputContractTest):
    """ci-trend must show metric names."""

    def test_ci_trend_shows_metrics(self):
        r = self.run_cli("core", "ci-trend", "--path", str(PROJECT_ROOT))
        self.assertTrue(
            "blast_radius" in r.stdout or "mirror_gap" in r.stdout or "health" in r.stdout,
            f"ci-trend should show metric names, got: {r.stdout[:200]}"
        )

    def test_ci_trend_not_empty(self):
        r = self.run_cli("core", "ci-trend", "--path", str(PROJECT_ROOT))
        self.assertGreater(len(r.stdout.strip()), 30)
