#!/usr/bin/env python3
"""Blind structural benchmark: baseline vs vocab, shuffled.

For each case, runs 2 models × 2 conditions.
Outputs a file with shuffled A/B labels — the user judges correctness.
"""
import json, os, subprocess, sys, urllib.request, re, time, random
from pathlib import Path

DEEPINFRA = "https://api.deepinfra.com/v1/openai/chat/completions"
API_KEY = "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
VOCAB_ROOT = "/home/user/src/vocab"
OUT_DIR = "/home/user/src/vocab/tmp"

os.makedirs(OUT_DIR, exist_ok=True)

MODELS = ["Qwen/Qwen3-Max-Thinking", "XiaomiMiMo/MiMo-V2.5-Pro"]

CASES = [
    # (label, repo, source, task, known_tests)
    ("OTP: binary.c → Erlang SUITE", "/tmp/corpus-sweep/otp",
     "erts/emulator/beam/binary.c", "optimise binary matching in emulator",
     ["erts/emulator/test/binary_SUITE.erl"]),

    ("Redis: t_string.c → Tcl test", "/tmp/corpus-sweep/redis",
     "src/t_string.c", "optimise string type operations",
     ["tests/unit/type/string.tcl"]),

    ("Redis: cluster.c → 3 Tcl test dirs", "/tmp/corpus-sweep/redis",
     "src/cluster.c", "fix cluster failover logic",
     ["tests/cluster/tests/02-failover.tcl",
      "tests/unit/cluster/03-failover-loop.tcl",
      "tests/integration/redis-cli.tcl"]),

    ("Nim: compiler/ast.nim → tests/ast/", "/tmp/corpus-sweep/Nim",
     "compiler/ast.nim", "add new AST node type",
     ["tests/ast/"]),

    ("Agent: claim.ts (flattened subdir)", "/home/user/src/autopsylab-agent",
     "packages/cli/src/commands/claim.ts", "add new CLI claim subcommand",
     ["packages/cli/tests/claim.test.ts"]),

    ("Agent: opencode.ts → opencode-tool.test.ts", "/home/user/src/autopsylab-agent",
     "packages/cli/src/tools/opencode.ts", "add tool registration for OpenCode tools",
     ["packages/cli/tests/opencode-tool.test.ts"]),

    ("Server: ingest.go (dual location)", "/home/user/src/autopsylab",
     "internal/handlers/ingest.go", "validate ingest payload HMAC signature",
     ["internal/handlers/ingest_test.go"]),

    ("Server: diagnosis.go → tests/unit/", "/home/user/src/autopsylab",
     "internal/services/diagnosis.go", "add new diagnosis rule for slow queries",
     ["tests/unit/services/diagnosis_test.go"]),

    ("serde: lib.rs → test_suite", "/tmp/corpus-sweep/serde",
     "serde/src/lib.rs", "add new derive macro for deserialization",
     ["test_suite/tests/test_annotations.rs",
      "test_suite/tests/regression.rs",
      "test_suite/tests/compiletest.rs"]),

    ("Prometheus: rules/manager.go", "/tmp/corpus-sweep/prometheus",
     "rules/manager.go", "add new alerting rule type",
     ["rules/alerting_test.go",
      "rules/manager_test.go",
      "rules/group_test.go"]),
]

def llm(model, prompt, max_tokens=300):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.1}).encode()
    req = urllib.request.Request(DEEPINFRA, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read())
        choice = d.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = d.get("usage", {}) or {}
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
    if m:
        return m.group(1)
    return text.strip()[:120]

def main():
    random.seed(42)
    all_results = []
    seed = 0

    for label, repo_path, src_file, task, known_tests in CASES:
        if not os.path.isdir(repo_path):
            print(f"SKIP {label}: repo {repo_path} not found")
            continue

        # Get vocab output once per case
        full_path = os.path.join(repo_path, src_file) if not os.path.isabs(src_file) else src_file
        vp_out = run_vocab(["verify-packet", "--files", src_file, "--task", task, "--format", "json"], repo_path)
        try:
            vpd = json.loads(vp_out) if vp_out.strip() else {}
        except:
            vpd = {}
        vtier = vpd.get("tier", "no_vocab")
        candidates = [str(c) for c in vpd.get("verification_candidates", [])]
        deter = vpd.get("deterministic_verify") or {}
        det_file = deter.get("file", "")
        print(f"\n  [{label}]")
        print(f"    Vocab: tier={vtier} det={det_file} cand={candidates[:3]}")

        for model in MODELS:
            model_slug = model.split("/")[-1]
            baseline_prompt = (
                f"Task: {task}. Edit file: {src_file} in a Go/TypeScript/Python/C repo.\n"
                f'Which test file verifies this change? Reply with ONLY a JSON object: {{"verify": "<path>"}}. '
                f"Respond with the JSON and nothing else."
            )
            vocab_prompt = (
                f"Edit file: {src_file}.\n"
                f"Task: {task}.\n"
                f"Vocab structural analysis (tier={vtier}): verification candidates={candidates[:4]} "
                f"deterministic_verify={det_file}.\n"
                f'Which test file verifies this change? Reply with ONLY a JSON object: {{"verify": "<path>"}}. '
                f"Respond with the JSON and nothing else."
            )

            # Run both conditions
            base_content, base_usage = llm(model, baseline_prompt, max_tokens=300)
            base_pred = extract_path(base_content)
            base_pt = base_usage.get("prompt_tokens", 0)
            base_ot = base_usage.get("completion_tokens", 0)
            base_tt = base_usage.get("total_tokens", 0)

            time.sleep(0.3)

            vocab_content, vocab_usage = llm(model, vocab_prompt, max_tokens=300)
            vocab_pred = extract_path(vocab_content)
            vocab_pt = vocab_usage.get("prompt_tokens", 0)
            vocab_ot = vocab_usage.get("completion_tokens", 0)
            vocab_tt = vocab_usage.get("total_tokens", 0)

            time.sleep(0.3)

            # Shuffle: randomly assign A/B
            flip = bool(random.getrandbits(1))
            if flip:
                out_a, out_b = base_pred, vocab_pred
                a_cond, b_cond = "baseline", "vocab"
            else:
                out_a, out_b = vocab_pred, base_pred
                a_cond, b_cond = "vocab", "baseline"

            all_results.append({
                "case_id": len(all_results),
                "label": label,
                "model": model_slug,
                "known_tests": known_tests,
                "vocab_tier": vtier,
                "A": out_a, "A_cond": a_cond,
                "B": out_b, "B_cond": b_cond,
                "token_base": base_tt,
                "token_vocab": vocab_tt,
            })
            print(f"    [{model_slug:25s}] A({a_cond[:4]}): {out_a[:50]:50s}  B({b_cond[:4]}): {out_b[:50]:50s}")

    # Write blind output (no labels)
    blind = []
    for r in all_results:
        blind.append({
            "case": f"Case {r['case_id']+1}",
            "label": r["label"],
            "model": r["model"],
            "known_tests": r["known_tests"],
            "vocab_tier": r["vocab_tier"],
            "A": r["A"],
            "B": r["B"],
        })

    blind_path = os.path.join(OUT_DIR, "blind_benchmark.txt")
    with open(blind_path, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("BLIND STRUCTURAL BENCHMARK\n")
        f.write("Judge each case: which output is the CORRECT test file?\n")
        f.write('Reply: "A", "B", "BOTH", or "NEITHER"\n')
        f.write("=" * 70 + "\n\n")

        for b in blind:
            f.write(f"--- Case {b['case']} ---\n")
            f.write(f"Source: {b['label']}\n")
            f.write(f"Model: {b['model']}\n")
            if b['known_tests']:
                f.write(f"Known test directories: {b['known_tests'][:3]}\n")
            f.write(f"\n  Output A: {b['A']}\n")
            f.write(f"  Output B: {b['B']}\n")
            f.write(f"\n  Your judgment (A/B/BOTH/NEITHER): ____\n")
            f.write(f"\n  Notes: ___________________________\n")
            f.write("\n" + "-" * 50 + "\n\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("END OF BLIND TEST\n")
        f.write("=" * 70 + "\n")

    print(f"\n=== Blind test written to {blind_path} ===")
    print(f"=== {len(blind)} cases across {len(MODELS)} models ===")

    # Save full results with labels for later scoring
    full_path = os.path.join(OUT_DIR, "blind_benchmark_key.json")
    with open(full_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"=== Key saved to {full_path} ===")


if __name__ == "__main__":
    main()
