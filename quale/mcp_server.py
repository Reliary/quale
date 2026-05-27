"""MCP server for quale — JSON-RPC over stdio, zero deps."""

from __future__ import annotations

import json
import os
import sys

from quale.reports import cartridge_report, orient_report, preflight_report


class MCPServer:
    def __init__(self):
        self.tools = {
            "edit_context": {
                "description": "Before editing any file. Returns risk, verification candidates, scope guard. 75% accuracy on 1,100 trials.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "File to edit (comma-separated for multiple)"},
                        "task": {"type": "string", "description": "Description of the edit task"},
                        "path": {"type": "string", "description": "Path to the repo (default: current dir)"},
                    },
                    "required": ["file"],
                },
                "handler": self._handle_edit_context,
            },
            "verify_packet": {
                "description": "After editing. Returns verification candidates with co-change signal. 80% accuracy, best cost/benefit.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Files changed (comma-separated)"},
                        "diff": {"type": "string", "description": "Git ref to diff against"},
                        "path": {"type": "string", "description": "Path to the repo (default: current dir)"},
                    },
                    "required": ["file"],
                },
                "handler": self._handle_verify_packet,
            },
            "orient": {
                "description": "First encounter with a repo. Returns module map, landmark files, language breakdown.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the repo (default: current dir)"},
                    },
                },
                "handler": self._handle_orient,
            },
        }

    def run(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            req = None
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                self._respond(None, error=str(e))
                continue
            method = req.get("method", "")
            req_id = req.get("id")
            if method == "tools/list":
                self._respond(req_id, result={
                    "tools": [
                        {
                            "name": name,
                            "description": t["description"],
                            "inputSchema": t["inputSchema"],
                        }
                        for name, t in self.tools.items()
                    ]
                })
            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name", "")
                args = params.get("arguments", {})
                tool = self.tools.get(name)
                if not tool:
                    self._respond(req_id, error=f"Unknown tool: {name}")
                    continue
                try:
                    result = tool["handler"](args)
                    self._respond(req_id, result={"content": [{"type": "text", "text": json.dumps(result)}]})
                except Exception as e:
                    self._respond(req_id, error=str(e))
            elif method == "initialized":
                pass
            else:
                self._respond(req_id, error=f"Unknown method: {method}")

    def _respond(self, req_id, result=None, error=None):
        resp = {"jsonrpc": "2.0", "id": req_id}
        if error:
            resp["error"] = {"code": -1, "message": str(error)}
        else:
            resp["result"] = result
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()

    def _handle_edit_context(self, args):
        file = args.get("file", "")
        task = args.get("task", "")
        path = os.path.abspath(args.get("path", "."))
        files = [f.strip() for f in file.split(",") if f.strip()]
        data = preflight_report(path=path, files=files, diff_ref=None, task=task)
        if "error" in data:
            return data
        verify_candidates = data.get("verification_candidates", data.get("verify_with", []))
        ver_confidence = data.get("verification_confidence", {})
        scope_creep = data.get("scope_creep_guard", {})
        wa = scope_creep.get("warnings", [])
        qs = [w.get("question_extras", "").strip() for w in wa if w.get("question_extras")]
        sci = "Before broadening scope, verify each extra file: " + "; ".join(qs) if qs else "Do not propose extra_edits unless the task explicitly requires them."
        scope_creep_instruction = sci
        vtypes = {}
        for c in verify_candidates[:5] if verify_candidates else data.get("changed_files", []):
            if c in data.get("changed_files", []):
                vtypes[c] = "source"
        for c in verify_candidates[:5] if verify_candidates else []:
            base = os.path.splitext(c)[0]
            if any(not d.get(c) == "source" for d in [vtypes]):
                vtypes[c] = "unit" if "_test" in c or ".test." in c else "integration"
        return {
            "schema_version": 1,
            "risk": data.get("risk", "unknown"),
            "confidence": data.get("confidence", "unknown"),
            "reason": data.get("reason", ""),
            "changed_files": data.get("changed_files", []),
            "read_first": data.get("read_first", data.get("fused_first", [])),
            "verification_mc": {
                "question": "Which file would verify this change?",
                "candidates": verify_candidates[:5] if verify_candidates else [],
                "max_selections": 1,
                "types": vtypes,
            },
            "verification_confidence": ver_confidence,
            "scope_creep_guard": {
                "allow_changed_files": data.get("changed_files", []),
                "stable_anchors_touched": scope_creep.get("stable_anchors_touched", []),
                "instruction": scope_creep_instruction,
            },
            "expansion_risk": data.get("expansion_risk", data.get("avoid_expanding_into", [])),
            "desert_warning": "Verification confidence is " + ver_confidence.get("level", "unknown") + "; structurally conservative.",
        }

    def _handle_verify_packet(self, args):
        file = args.get("file", "")
        diff = args.get("diff")
        path = os.path.abspath(args.get("path", "."))
        files = [f.strip() for f in file.split(",") if f.strip()]
        data = cartridge_report(path=path, files=files, diff_ref=diff, task="")
        if "error" in data:
            return data
        return {
            "schema_version": 1,
            "tier": data.get("tier", "unknown"),
            "confidence": data.get("confidence", ""),
            "verification_candidates": data.get("verification_candidates", []),
            "entangled_candidates": data.get("entangled_candidates", []),
            "deterministic_verify": data.get("deterministic_verify"),
            "desert_note": data.get("desert_note"),
            "verification_confidence": data.get("verification_confidence", {}),
        }

    def _handle_orient(self, args):
        path = os.path.abspath(args.get("path", "."))
        data = orient_report(path)
        if "error" in data:
            return data
        return data


def run():
    MCPServer().run()


if __name__ == "__main__":
    run()
