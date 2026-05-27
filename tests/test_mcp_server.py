"""Tests for the MCP server (JSON-RPC over stdio)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest


MCP_SERVER = os.path.join(os.path.dirname(__file__), "..", "quale", "mcp_server.py")


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo, check=True,
        )
        src = os.path.join(self.repo, "main.go")
        test = os.path.join(self.repo, "main_test.go")
        with open(src, "w") as f:
            f.write("package main\nfunc Hello() string { return \"hello\" }\n")
        with open(test, "w") as f:
            f.write("package main\nimport \"testing\"\nfunc TestHello(t *testing.T) {}\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.repo, check=True)

    def _call(self, request):
        proc = subprocess.run(
            [sys.executable, MCP_SERVER],
            input=json.dumps(request),
            capture_output=True, text=True, timeout=15,
        )
        return json.loads(proc.stdout)

    def test_tools_list(self):
        resp = self._call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(resp["id"], 1)
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("edit_context", names)
        self.assertIn("verify_packet", names)
        self.assertIn("orient", names)
        self.assertEqual(len(tools), 3)

    def test_edit_context_returns_expected_keys(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {
                "name": "edit_context",
                "arguments": {"file": "main.go", "path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("risk", result)
        self.assertIn("verification_mc", result)
        self.assertIn("changed_files", result)
        self.assertIn("scope_creep_guard", result)
        self.assertIn("schema_version", result)
        self.assertEqual(result["schema_version"], 1)

    def test_edit_context_identifies_test_file(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 3,
            "method": "tools/call",
            "params": {
                "name": "edit_context",
                "arguments": {"file": "main.go", "path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        candidates = result["verification_mc"]["candidates"]
        self.assertTrue(
            any("_test" in c for c in candidates),
            f"Expected test file in candidates, got {candidates}",
        )

    def test_verify_packet_returns_candidates(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 4,
            "method": "tools/call",
            "params": {
                "name": "verify_packet",
                "arguments": {"file": "main.go", "path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("tier", result)
        self.assertIn("verification_candidates", result)
        self.assertTrue(
            any("_test" in c for c in result["verification_candidates"]),
        )

    def test_orient_returns_repo_map(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 5,
            "method": "tools/call",
            "params": {
                "name": "orient",
                "arguments": {"path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("total_files", result)
        self.assertIn("languages", result)
        self.assertIn("landmarks", result)
        self.assertIn("modules", result)

    def test_unknown_tool_returns_error(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 6,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        self.assertIn("error", resp)

    def test_edit_context_handles_task_param(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 7,
            "method": "tools/call",
            "params": {
                "name": "edit_context",
                "arguments": {
                    "file": "main.go",
                    "task": "fix the function",
                    "path": self.repo,
                },
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("risk", result)

    def test_edit_context_missing_file_returns_empty_candidates(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 8,
            "method": "tools/call",
            "params": {
                "name": "edit_context",
                "arguments": {"file": "nonexistent.go", "path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["verification_mc"]["candidates"], [])

    def test_verification_mc_has_question_and_max_selections(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 9,
            "method": "tools/call",
            "params": {
                "name": "edit_context",
                "arguments": {"file": "main.go", "path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        vmc = result["verification_mc"]
        self.assertIn("question", vmc)
        self.assertIn("max_selections", vmc)
        self.assertEqual(vmc["max_selections"], 1)

    def test_scope_creep_guard_has_instruction(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 10,
            "method": "tools/call",
            "params": {
                "name": "edit_context",
                "arguments": {"file": "main.go", "path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        guard = result["scope_creep_guard"]
        self.assertIn("allow_changed_files", guard)
        self.assertIn("instruction", guard)
        self.assertIn("stable_anchors_touched", guard)

    def test_orient_has_worked_without_path_defaults_to_cwd(self):
        resp = self._call({
            "jsonrpc": "2.0", "id": 11,
            "method": "tools/call",
            "params": {
                "name": "orient",
                "arguments": {"path": self.repo},
            },
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertGreaterEqual(result["total_files"], 2)

    def test_jsonrpc_invalid_json_handled(self):
        proc = subprocess.run(
            [sys.executable, MCP_SERVER],
            input="not valid json\n",
            capture_output=True, text=True, timeout=15,
        )
        resp = json.loads(proc.stdout)
        self.assertIn("error", resp)
        self.assertIn("code", resp["error"])


if __name__ == "__main__":
    unittest.main()
