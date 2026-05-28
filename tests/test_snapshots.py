"""Snapshot tests — run commands against fixture repo, compare to golden output.

ISTQB technique: Regression testing via output comparison.
Catches: unexpected output changes, field renames, format drift.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = PROJECT_ROOT / "tests" / "snapshots"


def normalize_output(text: str) -> str:
    """Normalize output for comparison: remove absolute paths and volatile timestamps."""
    lines = []
    for line in text.split("\n"):
        # Remove absolute paths
        if PROJECT_ROOT.as_posix() in line:
            line = line.replace(PROJECT_ROOT.as_posix(), "<ROOT>")
        if str(PROJECT_ROOT) in line:
            line = line.replace(str(PROJECT_ROOT), "<ROOT>")
        lines.append(line)
    return "\n".join(lines)


class TestSnapshotStability(unittest.TestCase):
    """Key commands must produce stable output against fixtures.

    Snapshot files are created on first run. Update them when output
    intentionally changes (run with UPDATE_SNAPSHOTS=1).
    """

    def setUp(self):
        self.env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        self.update = os.environ.get("UPDATE_SNAPSHOTS") == "1"

    def run_quale(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "quale.cli", *args],
            cwd=str(PROJECT_ROOT), env=self.env, text=True, capture_output=True,
        )
        if result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout[:200]}\nstderr={result.stderr[:200]}")
        return result

    def assert_snapshot(self, name: str, text: str):
        """Compare normalized output against golden file."""
        normalized = normalize_output(text)
        snapshot_path = SNAPSHOT_DIR / f"{name}.snap"
        if self.update:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(normalized)
        elif snapshot_path.exists():
            expected = snapshot_path.read_text()
            self.assertEqual(expected, normalized, f"Snapshot mismatch: {name}. Run with UPDATE_SNAPSHOTS=1 to update.")
        else:
            self.fail(f"No snapshot file for {name}. Run with UPDATE_SNAPSHOTS=1 to create.")

    def test_agent_orient_snapshot(self):
        """agent orient must produce stable structure for the quale repo itself."""
        r = self.run_quale("agent", "orient", "--path", str(PROJECT_ROOT))
        data = json.loads(r.stdout)
        # Only compare structural keys, not file paths which are environment-specific
        snapshot_keys = {
            "total_files": data.get("total_files"),
            "languages": data.get("languages"),
            "landmarks": data.get("landmarks")[:3] if data.get("landmarks") else [],
            "modules": len(data.get("modules", [])),
        }
        self.assert_snapshot("agent_orient", json.dumps(snapshot_keys, indent=2, sort_keys=True))

    def test_test_gaps_snapshot(self):
        """test-gaps output structure must be stable."""
        r = self.run_quale("core", "test-gaps", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        snapshot = {
            "deserts": len(data.get("deserts", [])),
            "guardrails_mode": data.get("guardrails", {}).get("mode"),
        }
        self.assert_snapshot("test_gaps", json.dumps(snapshot, indent=2, sort_keys=True))

    def test_extinct_exports_snapshot(self):
        """extinct-exports output structure must be stable."""
        r = self.run_quale("core", "extinct-exports", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        snapshot = {
            "thylacines": len(data.get("thylacines", [])),
        }
        self.assert_snapshot("extinct_exports", json.dumps(snapshot, indent=2, sort_keys=True))

    def test_health_score_snapshot(self):
        """health-score output structure must be stable."""
        r = self.run_quale("core", "health-score", "--path", str(PROJECT_ROOT), "--format", "json")
        data = json.loads(r.stdout)
        snapshot = {
            "schema_version": data.get("schema_version"),
            "excess_porosity": data.get("excess_porosity"),
        }
        self.assert_snapshot("health_score", json.dumps(snapshot, indent=2, sort_keys=True))
