#!/usr/bin/env python3
"""Cross-model benchmark v2.
Focuses on the structural gap: cross-directory test placement vs same-dir naming conventions.
Tests 4 models across curated pairs that represent the actual structural challenge.
"""
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
]

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
        return msg.get("content", ""), usage
    except Exception as e:
        return f"ERROR: {e}", {}

def run_vocab(args, cwd):
    env = {**os.environ, "PYTHONPATH": VOCAB_ROOT}
    r = subprocess.run([sys.executable, "-m", "vocab.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
    return r.stdout.strip()

def extract_path(text):
    m = re.search(r'"verify"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else text.strip()[:120]

# Curated pairs: cross-dir test placement (the hard structural problem)
CROSS_DIR_PAIRS = [
    # agent repo: src/ -> tests/ (TypeScript)
    ("agent (cross-dir)", "/home/user/src/autopsylab-agent",
     "packages/core/src/spool.ts", "packages/core/tests/spool.test.ts",
     "add retry with backoff to spool upload"),
    ("agent (cross-dir)", "/home/user/src/autopsylab-agent",
     "packages/core/src/redaction.ts", "packages/core/tests/redaction.test.ts",
     "fix PII redaction in error messages"),
    ("agent (cross-dir)", "/home/user/src/autopsylab-agent",
     "packages/opencode/src/plugin.ts", "packages/opencode/tests/plugin.test.ts",
     "add event handler for session end"),
    # server repo: internal/ -> tests/ (Go cross-package)
    ("server (cross-dir)", "/home/user/src/autopsylab",
     "internal/handlers/fingerprint_read.go",
     "tests/functional/api_fingerprints_test.go",
     "add handler for listing fingerprints by source"),
    ("server (cross-dir)", "/home/user/src/autopsylab",
     "internal/handlers/auth.go",
     "tests/unit/handlers/auth_test.go",
     "validate API key authentication"),
    # server: internal/ -> tests/functional/
    ("server (cross-dir)", "/home/user/src/autopsylab",
     "internal/services/runbook_factory.go",
     "tests/functional/api_playbooks_test.go",
     "modify runbook generation logic"),
]

# Curated pairs: same-dir naming convention (easy test)
SAME_DIR_PAIRS = [
    ("agent (same-dir)", "/home/user/src/autopsylab-agent",
     "packages/go-sdk/client.go", "packages/go-sdk/client_test.go",
     "add upload retry to Go SDK client"),
    ("server (same-dir)", "/home/user/src/autopsylab",
     "cmd/corpus-intake/main.go", "cmd/corpus-intake/main_test.go",
     "modify corpus intake CLI flags"),
    ("server (same-dir)", "/home/user/src/autopsylab",
     "cmd/corpus-source-lanes/main.go", "cmd/corpus-source-lanes/main_test.go",
     "add new source lane for public repos"),
    ("prometheus (same-dir)", "/tmp/corpus-sweep/prometheus",
     "cmd/promtool/analyze.go", "cmd/promtool/analyze_test.go",
     "add new promtool analysis rule"),
    ("prometheus (same-dir)", "/tmp/corpus-sweep/prometheus",
     "cmd/promtool/backfill.go", "cmd/promtool/backfill_test.go",
     "add backfill support for remote write"),
    ("prometheus (same-dir)", "/tmp/corpus-sweep/prometheus",
     "cmd/prometheus/main.go", "cmd/prometheus/main_test.go",
     "update prometheus server config parsing"),
]

ALL_PAIRS = CROSS_DIR_PAIRS + SAME_DIR_PAIRS

def main():
    results = []
    pair_results = {"same-dir_total": 0, "same-dir_baseline_ok": 0,
                    "cross-dir_total": 0, "cross-dir_baseline_ok": 0,
                    "same-dir_vocab_ok": 0, "cross-dir_vocab_ok": 0}

    for pair_name, repo_path, src_file, gt_test, task in ALL_PAIRS:
        pair_type = "cross-dir" if "cross-dir" in pair_name else "same-dir"
        print(f"\n{'='*60}")
        print(f"[{pair_type}] {pair_name}: {src_file}")
        print(f"  GT test: {gt_test}")
        print(f"{'='*60}")

        # Run vocab once
        vp_out = run_vocab(["verify-packet", "--files", src_file, "--task", task, "--format", "json"], repo_path)
        try:
            vpd = json.loads(vp_out) if vp_out.strip() else {}
        except json.JSONDecodeError:
            vpd = {}
        vtier = vpd.get("tier", "no_vocab")
        candidates = [str(c) for c in vpd.get("verification_candidates", [])]
        deter = vpd.get("deterministic_verify", {})
        print(f"  Vocab tier: {vtier}, candidates: {candidates[:4]}, deterministic: {deter.get('file','')}")

        for model in MODELS:
            short = model.split("/")[-1][:25]
            for condition, prompt_fn in [
                ("baseline", lambda: f"Task: {task}. Edit file: {src_file}. "
                                      f'Which test file verifies this change? Reply JSON: {{"verify": "<path>"}}'),
                ("vocab", lambda: f"Edit file: {src_file}. "
                    f"Vocab ({vtier}): candidates={candidates[:4]} "
                    f"deterministic={deter.get('file','')}. "
                    f'Which test verifies this change? Reply JSON: {{"verify": "<path>"}}'),
            ]:
                content, usage = llm(model, prompt_fn())
                pred = extract_path(content)
                match = gt_test in pred
                prompt_tok = usage.get("prompt_tokens", 0)
                output_tok = usage.get("completion_tokens", 0)
                total_tok = usage.get("total_tokens", 0)
                status = "✓" if match else "✗"
                print(f"  {short:25s} [{condition:8s}] {pred:45s} {status}  p={prompt_tok} o={output_tok} t={total_tok}")

                results.append({"repo": pair_name, "file": src_file, "gt": gt_test,
                    "pair_type": pair_type, "model": model, "condition": condition,
                    "prediction": pred, "correct": match,
                    "prompt_tokens": prompt_tok, "output_tokens": output_tok,
                    "total_tokens": total_tok, "vocab_tier": vtier})

                if condition == "baseline" and match:
                    pair_results[f"{pair_type}_baseline_ok"] += 1
                elif condition == "vocab" and match:
                    pair_results[f"{pair_type}_vocab_ok"] += 1
            time.sleep(0.3)

    pair_results["same-dir_total"] = sum(1 for r in results if r["pair_type"] == "same-dir" and r["condition"] == "baseline")
    pair_results["cross-dir_total"] = sum(1 for r in results if r["pair_type"] == "cross-dir" and r["condition"] == "baseline")

    # Summary
    print(f"\n{'='*60}")
    print("STRUCTURAL HYPOTHESIS TEST")
    print(f"{'='*60}")
    for pt in ["cross-dir", "same-dir"]:
        total = pair_results[f"{pt}_total"]
        base_ok = pair_results[f"{pt}_baseline_ok"]
        voc_ok = pair_results[f"{pt}_vocab_ok"]
        base_pct = base_ok/total*100 if total else 0
        voc_pct = voc_ok/total*100 if total else 0
        print(f"  {pt:15s}: baseline correct {base_ok}/{total} ({base_pct:.0f}%)  "
              f"vocab correct {voc_ok}/{total} ({voc_pct:.0f}%)")
    print(f"\n  Diff (baseline - vocab):")
    for pt in ["cross-dir", "same-dir"]:
        total = pair_results[f"{pt}_total"]
        base_ok = pair_results[f"{pt}_baseline_ok"]
        voc_ok = pair_results[f"{pt}_vocab_ok"]
        diff = (voc_ok - base_ok)/total*100 if total else 0
        print(f"    {pt:15s}: {diff:+.0f} percentage points")

    print(f"\n{'='*60}")
    print("BY MODEL")
    print(f"{'='*60}")
    for model in MODELS:
        for pt in ["cross-dir", "same-dir"]:
            mr = [r for r in results if r["model"] == model and pt in r["pair_type"]]
            if not mr:
                continue
            base_ok = sum(1 for r in mr if r["condition"]=="baseline" and r["correct"])
            base_tot = sum(1 for r in mr if r["condition"]=="baseline")
            voc_ok = sum(1 for r in mr if r["condition"]=="vocab" and r["correct"])
            voc_tot = sum(1 for r in mr if r["condition"]=="vocab")
            print(f"  {model.split('/')[-1][:30]:30s} {pt:15s}  base={base_ok}/{base_tot}  vocab={voc_ok}/{voc_tot}")

    # Save
    out = {"results": results, "stats": pair_results}
    with open("/tmp/vocab-structural-benchmark.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to /tmp/vocab-structural-benchmark.json")

if __name__ == "__main__":
    main()
