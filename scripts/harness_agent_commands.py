#!/usr/bin/env python3
"""Harness: measure effectiveness of agent-facing vocab commands.

Tests each agent command against known ground truth across multiple repos.
No LLM calls — purely structural: runs vocab commands and verifies output
contains the expected file paths, risk labels, contract IDs, etc.

Returns pass/fail per command with a summary score.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

VOCAB_ROOT = Path(__file__).resolve().parents[1]
LLAMA = Path("/home/user/src/llama.cpp")
AGENT = Path("/home/user/src/autopsylab-agent")


def run_vocab(args: list[str], timeout: int = 120) -> tuple[str, str, int]:
    """Run a vocab CLI command."""
    cmd = [sys.executable, "-m", "quale.cli"] + args
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(VOCAB_ROOT)
    )
    return proc.stdout, proc.stderr, proc.returncode


def check(obj: Any, pattern: str) -> bool:
    """Recursive substring match."""
    if isinstance(obj, str):
        return pattern.lower() in obj.lower()
    if isinstance(obj, dict):
        return any(check(v, pattern) for v in obj.values())
    if isinstance(obj, list):
        return any(check(i, pattern) for i in obj)
    return False


def harness() -> int:
    results: list[dict] = []
    passed = 0
    total = 0

    def test(name: str, success: bool, detail: str = ""):
        nonlocal passed, total
        total += 1
        results.append({"name": name, "passed": success, "detail": detail})
        if success:
            passed += 1
            print(f"  \033[32mPASS\033[0m {name}")
        else:
            print(f"  \033[31mFAIL\033[0m {name} \033[90m{detail}\033[0m")

    # ===== edit-context =====
    # Test 1: basic preflight — expects read_first and verification_candidates
    print("\n\033[1m=== edit-context ===\033[0m")
    o, e, rc = run_vocab([
        "edit-context", "--path", str(AGENT),
        "--files", "packages/core/src/typed-evidence.ts",
        "--task", "add a CacheEvidence typed envelope",
        "--format", "json",
    ])
    if rc == 0:
        d = json.loads(o)
        test("edit-context (agent) output contains read_first fields",
             "read_first" in d or "file_classifications" in d)
        test("edit-context (agent) mentions typed-evidence",
             check(d, "typed-evidence"))
        test("edit-context (agent) mentions test candidates",
             check(d, "test") and check(d, ".ts"))
    else:
        test("edit-context (agent) exits 0", False, f"exit {rc}: {e[:120]}")

    o, e, rc = run_vocab([
        "edit-context", "--path", str(VOCAB_ROOT),
        "--files", "quale/cli.py",
        "--task", "add a new CLI command",
        "--format", "json",
    ])
    if rc == 0:
        d = json.loads(o)
        test("edit-context (vocab) output valid JSON",
             isinstance(d, dict))
        test("edit-context (vocab) mentions cli.py", check(d, "cli.py"))
    else:
        test("edit-context (vocab) exits 0", False, f"exit {rc}: {e[:120]}")

    # ===== guard =====
    print("\n\033[1m=== guard ===\033[0m")
    for label, path, file in [
        ("guard (agent spool.ts)", AGENT, "packages/core/src/spool.ts"),
        ("guard (agent typed-evidence.ts)", AGENT, "packages/core/src/typed-evidence.ts"),
        ("guard (vocab reports.py)", VOCAB_ROOT, "quale/reports.py"),
    ]:
        o, e, rc = run_vocab([
            "guard", "--path", str(path), "--file", file, "--format", "json",
        ])
        if rc == 0:
            d = json.loads(o)
            test(label, "trompe" in d and "criticality" in d)
        else:
            test(label, False, f"exit {rc}")

    # ===== contract =====
    print("\n\033[1m=== contract ===\033[0m")
    for label, path, files in [
        ("contract (vocab reports.py)", VOCAB_ROOT, "quale/reports.py"),
        ("contract (agent typed-evidence)", AGENT, "packages/core/src/typed-evidence.ts"),
    ]:
        o, e, rc = run_vocab([
            "contract", "--path", str(path), "--files", files, "--format", "json",
        ])
        if rc == 0:
            d = json.loads(o)
            test(label, "contract_id" in d and "allowed_edit" in d)
        else:
            test(label, False, f"exit {rc}")

    # ===== check-plan =====
    print("\n\033[1m=== check-plan ===\033[0m")
    # Generate a valid contract first, then test check-plan against it
    o, e, rc = run_vocab([
        "contract", "--path", str(VOCAB_ROOT),
        "--files", "quale/reports.py",
        "--format", "json",
    ])
    if rc == 0:
        contract = json.loads(o)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(contract, f)
            contract_path = f.name
        # Valid proposal
        valid_proposal = {"read": ["quale/reports.py"], "edit": ["quale/reports.py"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(valid_proposal, f)
            valid_path = f.name
        o2, e2, rc2 = run_vocab([
            "check-plan", "--contract", contract_path,
            "--proposal", valid_path,
            "--format", "json",
        ])
        test("check-plan valid proposal exits 0", rc2 == 0,
             f"exit {rc2}" if rc2 else "")
        if rc2 == 0:
            d2 = json.loads(o2)
            test("check-plan valid proposal result has 'valid'",
                 "valid" in d2.get("result", "").lower() if isinstance(d2.get("result"), str) else True)

        # Hallucinated proposal
        bad_proposal = {"read": ["quale/nonexistent.py"], "edit": ["quale/reports.py"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad_proposal, f)
            bad_path = f.name
        o3, e3, rc3 = run_vocab([
            "check-plan", "--contract", contract_path,
            "--proposal", bad_path,
            "--format", "json",
        ])
        if rc3 == 0:
            d3 = json.loads(o3)
            test("check-plan hallucinated path detected",
                 "viol" in str(d3).lower() or "invalid" in str(d3).lower())
        else:
            test("check-plan hallucinated path exits 0", False, f"exit {rc3}")

        # Scope expansion proposal
        expand_proposal = {"read": ["quale/reports.py", "quale/cli.py"],
                           "edit": ["quale/reports.py"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(expand_proposal, f)
            expand_path = f.name
        o4, e4, rc4 = run_vocab([
            "check-plan", "--contract", contract_path,
            "--proposal", expand_path,
            "--format", "json",
        ])
        test("check-plan scope expansion handles gracefully",
             rc4 == 0, f"exit {rc4}" if rc4 else "")
    else:
        test("check-plan contract generation", False, f"exit {rc}")

    # ===== help-agent =====
    print("\n\033[1m=== help-agent ===\033[0m")
    for label, task in [
        ("help-agent 'find tests for a file'", "find tests for a file"),
        ("help-agent 'add new feature'", "add a new feature to an existing file"),
        ("help-agent 'explore codebase'", "explore codebase structure"),
    ]:
        o, e, rc = run_vocab(["help-agent", task])
        if rc == 0:
            test(label, len(o.strip()) > 50,
                 f"{len(o.strip())} chars")
        else:
            test(label, False, f"exit {rc}")

    # ===== route =====
    print("\n\033[1m=== route ===\033[0m")
    for label, path, task in [
        ("route (vocab generic)", VOCAB_ROOT, "fix a bug in reports.py"),
        ("route (vocab trivial)", VOCAB_ROOT, "fix a typo"),
    ]:
        o, e, rc = run_vocab([
            "route", "--path", str(path), "--task", task, "--format", "json",
        ])
        if rc == 0:
            d = json.loads(o)
            test(label, "action" in d and "route_reason" in d)
        else:
            test(label, False, f"exit {rc}")

    # ===== verify =====
    print("\n\033[1m=== verify ===\033[0m")
    for label, path, files in [
        ("verify (vocab reports.py)", VOCAB_ROOT, "quale/reports.py"),
        ("verify (agent typed-evidence)", AGENT, "packages/core/src/typed-evidence.ts"),
    ]:
        o, e, rc = run_vocab([
            "verify", "--path", str(path), "--files", files, "--format", "json",
        ])
        # verify may return no candidates (data-driven) but should not crash
        test(label, rc in (0, 1), f"exit {rc}" if rc not in (0, 1) else "")

    # ===== check-diff =====
    print("\n\033[1m=== check-diff ===\033[0m")
    o, e, rc = run_vocab([
        "check-diff", "--path", str(VOCAB_ROOT),
        "--diff", "HEAD~1", "--format", "json",
    ])
    if rc == 0:
        d = json.loads(o)
        test("check-diff (vocab HEAD~1) exits 0", True)
        test("check-diff has defect info", "defects" in d and "max_severity" in d)
    else:
        test("check-diff (vocab HEAD~1)", False, f"exit {rc}")


    # ===== Summary =====
    print(f"\n{'='*60}")
    print(f"  \033[1mHARNESS: {passed}/{total} passed\033[0m "
          + (f"\033[32m({passed/total*100:.0f}%)\033[0m" if passed == total else f"\033[33m({passed/total*100:.0f}%)\033[0m"))
    print(f"{'='*60}")

    with open("/tmp/vocab-agent-harness.json", "w") as f:
        json.dump({"passed": passed, "total": total, "summary": f"{passed}/{total}",
                   "results": results}, f, indent=2)
    print(f"Results: /tmp/vocab-agent-harness.json")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(harness())
