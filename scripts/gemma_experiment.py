#!/usr/bin/env python3
"""Quick Gemma 4 experiment: baseline vs verify_packet, 1 trial.
Avoids the harness overhead — caches vocab scans, tests LLM only.
"""
import json, os, subprocess, sys, urllib.request
from pathlib import Path

LLAMA_URL = "http://127.0.0.1:18080/v1/chat/completions"
CASES = [
    ("autopsylab-agent", "/home/user/src/autopsylab-agent",
     "packages/core/src/spool.ts",
     "add retry with backoff to spool upload",
     "packages/core/tests/spool.test.ts"),
    ("autopsylab", "/home/user/src/autopsylab",
     "internal/handlers/fingerprint_read.go",
     "add handler for listing fingerprints by source",
     "tests/functional/api_fingerprints_test.go"),
    ("llm-semantic-transport", "/home/user/src/llm-semantic-transport",
     "app/compression/__init__.py",
     "add minhash compression backend for fuzzy duplicate detection",
     "tests/test_minhash_index.py"),
    ("llama-cpp", "/home/user/src/llama.cpp",
     "src/llama.cpp",
     "add grammar-guided sampling path with temperature scheduling",
     "tests/test-llama-grammar.cpp"),
]

def run_vocab(args: list[str], path: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "vocab.cli", *args],
        cwd=path, capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    return result.stdout.strip()

def llm_call(prompt: str, system: str = "") -> str:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    body = json.dumps({
        "model": "g4e4b-instruct",
        "messages": msgs,
        "max_tokens": 256,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(LLAMA_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")

def baseline_prompt(task: str, edit_file: str) -> str:
    return (
        f"Task: {task}\n"
        f"Edit file: {edit_file}\n\n"
        "Which test file should be verified after making this change?\n"
        "Return JSON: {\"verify\": \"<test_file_path>\"}"
    )

def vocab_prompt(verify_json: str) -> str:
    return (
        f"Vocab guidance:\n{verify_json}\n\n"
        "Which test file should be verified for this change?\n"
        "Return JSON: {\"verify\": \"<test_file_path>\"}"
    )

def extract_path(response: str) -> str:
    try:
        d = json.loads(response)
        return d.get("verify", "")
    except json.JSONDecodeError:
        for line in response.split("\n"):
            if "verify" in line:
                import re
                m = re.search(r'"verify"\s*:\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    return response.strip()[:120]

results = []
for name, path, edit_file, task, gt_verify in CASES:
    print(f"\n=== {name} ===")
    # Pre-run vocab once (caches scan internally)
    vp_json = run_vocab(["verify-packet", "--files", edit_file, "--task", task, "--format", "json"], path)
    try:
        vp = json.loads(vp_json) if vp_json.strip() else {}
    except json.JSONDecodeError:
        vp = {}
    # Baseline call
    bp = baseline_prompt(task, edit_file)
    baseline_out = llm_call(bp)
    baseline_path = extract_path(baseline_out)
    # Vocab-guided call
    vp_text = json.dumps(vp, indent=2)[:600]
    vocab_out = llm_call(vocab_prompt(vp_text))
    vocab_path = extract_path(vocab_out)
    baseline_hit = "YES" if gt_verify in baseline_path else "no"
    vocab_hit = "YES" if gt_verify in vocab_path else "no"
    results.append({
        "repo": name, "edit": edit_file, "gt": gt_verify,
        "baseline_path": baseline_path,
        "vocab_path": vocab_path,
        "baseline_hit": baseline_hit,
        "vocab_hit": vocab_hit,
    })
    print(f"  Ground truth: {gt_verify}")
    print(f"  Baseline:     {baseline_path}  [{baseline_hit}]")
    print(f"  Vocab:        {vocab_path}  [{vocab_hit}]")

print("\n\n=== SUMMARY ===")
print(f"{'Repo':25s} {'Baseline':10s} {'Vocab':10s}")
print("-" * 50)
for r in results:
    print(f"{r['repo']:25s} {r['baseline_hit']:10s} {r['vocab_hit']:10s}")
baseline_ok = sum(1 for r in results if r['baseline_hit'] == "YES")
vocab_ok = sum(1 for r in results if r['vocab_hit'] == "YES")
print(f"\nBaseline: {baseline_ok}/{len(results)}  Vocab: {vocab_ok}/{len(results)}")
