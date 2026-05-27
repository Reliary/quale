"""Layer 5: JSON schema validation for agent tool format outputs.

Ensures agent-facing commands always produce valid, complete JSON
structures that LLMs can consume without errors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "tests" / "schemas"


def _load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name) as f:
        return json.load(f)


class AgentEditSchemaTest(unittest.TestCase):
    """agent edit must conform to the tool format schema."""

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        self.schema = _load_schema("agent_edit_schema.json")

    def run_cli(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=str(PROJECT_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr[:300]}")
        return json.loads(result.stdout)

    def test_agent_edit_has_required_keys(self):
        data = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        required = ["schema_version", "risk", "confidence", "changed_files", "verification_mc", "scope_creep_guard"]
        missing = [k for k in required if k not in data]
        self.assertFalse(missing, f"missing required keys: {missing}")

    def test_agent_edit_risk_valid(self):
        data = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        valid = {"low", "moderate", "high", "unknown"}
        self.assertIn(data.get("risk", ""), valid)

    def test_agent_edit_verification_mc_has_question(self):
        data = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        mc = data.get("verification_mc", {})
        self.assertGreater(len(mc.get("question", "")), 5)

    def test_agent_edit_scope_creep_has_instruction(self):
        data = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        guard = data.get("scope_creep_guard", {})
        self.assertIn("instruction", guard)
        self.assertIn("allow_changed_files", guard)

    def test_agent_edit_changed_files_not_empty(self):
        data = self.run_cli("agent", "edit", "README.md", "--path", str(PROJECT_ROOT))
        self.assertGreater(len(data.get("changed_files", [])), 0)


class AgentOrientSchemaTest(unittest.TestCase):
    """agent orient must conform to the orient schema."""

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        self.schema = _load_schema("agent_orient_schema.json")

    def run_cli(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=str(PROJECT_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr[:300]}")
        return json.loads(result.stdout)

    def test_orient_has_required_keys(self):
        data = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        required = ["total_files", "languages", "landmarks", "modules", "recommended_workflow"]
        missing = [k for k in required if k not in data]
        self.assertFalse(missing, f"missing required keys: {missing}")

    def test_orient_has_non_empty_landmarks(self):
        data = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        self.assertGreater(len(data.get("landmarks", [])), 0)
        for l in data["landmarks"]:
            self.assertIn("file", l)
            self.assertIn("why", l)

    def test_orient_has_three_workflow_steps(self):
        data = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        self.assertEqual(len(data.get("recommended_workflow", [])), 3)

    def test_orient_languages_non_empty(self):
        data = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        self.assertGreater(len(data.get("languages", [])), 0)

    def test_orient_total_files_positive(self):
        data = self.run_cli("agent", "orient", "--path", str(PROJECT_ROOT))
        self.assertGreater(data.get("total_files", 0), 0)


class AgentGuardSchemaTest(unittest.TestCase):
    """agent guard must have correct structure."""

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    def run_cli(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=str(PROJECT_ROOT),
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr[:300]}")
        return json.loads(result.stdout)

    def test_guard_has_schema_version(self):
        data = self.run_cli("agent", "guard", "README.md", "--path", str(PROJECT_ROOT))
        self.assertEqual(data.get("schema_version"), 1)

    def test_guard_references_file(self):
        data = self.run_cli("agent", "guard", "README.md", "--path", str(PROJECT_ROOT))
        self.assertIn("README.md", data.get("file", ""))
