#!/usr/bin/env python3
"""Quick sweep: baseline vs vocab across 5 models on DeepInfra."""
import json, os, subprocess, sys, urllib.request, re, time
from pathlib import Path

DEEPINFRA = "https://api.deepinfra.com/v1/openai/chat/completions"
API_KEY = "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
VOCAB_ROOT = "/home/user/src/vocab"

MODELS = [
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "google/gemma-4-31B-it",
    "nvidia/Nemotron-3-Nano-30B-A3B",
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    "anthropic/claude-opus-4-7",
]

CASES = [
    ("agent", "/home/user/src/autopsylab-agent",
     "packages/core/src/spool.ts",
     "add retry with backoff to spool upload",
     "packages/core/tests/spool.test.ts"),
    ("server", "/home/user/src/autopsylab",
     "internal/handlers/fingerprint_read.go",
     "add handler for listing fingerprints by source",
     "tests/functional/api_fingerprints_test.go"),
]

CLAUDE_ONLY = [CASES[0]]

def llm(model, prompt, max_tokens=300):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.1}).encode()
    req = urllib.request.Request(DEEPINFRA, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
        choice = d.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = d.get("usage", {})
        reasoning = msg.get("reasoning_content", "") or d.get("reasoning", "") or ""
        return msg.get("content", ""), reasoning, usage
    except Exception as e:
        return "", f"ERROR: {e}", {}

def run_vocab(args, cwd):
    env = {**os.environ, "PYTHONPATH": VOCAB_ROOT}
    r = subprocess.run([sys.executable, "-m", "vocab.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
    return r.stdout.strip()

def extract_path(text):
    m = re.search(r'"verify"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else text.strip()[:100]

def run_sweep():
    results = []
    for model in MODELS:
        cases = CLAUDE_ONLY if "claude" in model else CASES
        print(f'\n{"="*60}')
        print(f"Model: {model}")
        print(f'{"="*60}')
        for name, path, edit_file, task, gt in cases:
            print(f"  Case: {name} ({edit_file})")
            vp_out = run_vocab(["verify-packet", "--files", edit_file, "--task", task, "--format", "json"], path)
            vpd = json.loads(vp_out) if vp_out.strip() else {}
            candidates = vpd.get("verification_candidates", [])
            deter = vpd.get("deterministic_verify", {})

            for condition, prompt_fn in [
                ("baseline", lambda: f"Task: {task}. Edit file: {edit_file}. "
                                     f'Which test file verifies this change? Reply with JSON: {{"verify": "<path>"}}'),
                ("vocab", lambda: f"Edit file: {edit_file}. "
                                  f"Vocab guidance: candidates={candidates[:3]} "
                                  f"deterministic={deter.get('file','')}. "
                                  f'Which test verifies this change? Reply JSON: {{"verify": "<path>"}}'),
            ]:
                prompt = prompt_fn()
                content, reasoning, usage = llm(model, prompt)
                pred = extract_path(content)
                match = gt in pred
                total_tok = usage.get("total_tokens", 0)
                prompt_tok = usage.get("prompt_tokens", 0)
                res_tok = usage.get("completion_tokens", 0)
                status = "✓" if match else "✗"
                print(f"    [{condition:8s}] {pred:55s} {status}  prompt={prompt_tok} output={res_tok} total={total_tok}")
                results.append({"model": model, "case": name, "condition": condition,
                    "prediction": pred, "correct": match, "gt": gt,
                    "prompt_tokens": prompt_tok, "output_tokens": res_tok,
                    "total_tokens": total_tok})
            time.sleep(1)

    print(f'\n{"="*60}')
    print("SUMMARY")
    print(f'{"="*60}')
    print(f'{"Model":45s} {"Case":10s} {"Base":6s} {"Vocab":6s} {"Base_tok":9s} {"Vocab_tok":9s}')
    print("-"*90)
    for model in MODELS:
        cases = CLAUDE_ONLY if "claude" in model else CASES
        for name, *_ in cases:
            base = [r for r in results if r["model"]==model and r["case"]==name and r["condition"]=="baseline"]
            voc = [r for r in results if r["model"]==model and r["case"]==name and r["condition"]=="vocab"]
            if base and voc:
                b = base[0]; v = voc[0]
                bm = "✓" if b["correct"] else "✗"
                vm = "✓" if v["correct"] else "✗"
                print(f"{model:45s} {name:10s} {bm:6s} {vm:6s} {b['total_tokens']:5d}      {v['total_tokens']:5d}")

if __name__ == "__main__":
    run_sweep()
