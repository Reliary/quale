#!/usr/bin/env python3
"""Blind structural benchmark: 3 models × 2 conditions × 10 hard cases = 60 trials.

Models: Qwen3-Max (efficient/tidy), DeepSeek-V4-Pro (fast/reliable), phi-4 (budget)
"""
import json, os, subprocess, sys, urllib.request, re, time, random

DEEPINFRA = "https://api.deepinfra.com/v1/openai/chat/completions"
KEY = "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
VOCAB_ROOT = "/home/user/src/vocab"
OUT_DIR = "/home/user/src/vocab/tmp"
os.makedirs(OUT_DIR, exist_ok=True)

MODELS = [
    "Qwen/Qwen3-Max",
    "deepseek-ai/DeepSeek-V4-Pro",
    "microsoft/phi-4",
]

CASES = [
    ("OTP: binary.c → Erlang SUITE", "/tmp/corpus-sweep/otp",
     "erts/emulator/beam/binary.c", "optimise binary matching in emulator",
     ["erts/emulator/test/binary_SUITE.erl"]),

    ("Redis: t_string.c → Tcl test", "/tmp/corpus-sweep/redis",
     "src/t_string.c", "optimise string type operations",
     ["tests/unit/type/string.tcl"]),

    ("Redis: cluster.c → 3 Tcl test dirs", "/tmp/corpus-sweep/redis",
     "src/cluster.c", "fix cluster failover logic",
     ["tests/cluster/tests/02-failover.tcl",
      "tests/unit/cluster/03-failover-loop.tcl"]),

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
      "test_suite/tests/regression.rs"]),

    ("Prometheus: rules/manager.go", "/tmp/corpus-sweep/prometheus",
     "rules/manager.go", "add new alerting rule type",
     ["rules/alerting_test.go", "rules/manager_test.go"]),
]

def call_llm(model, prompt, max_tokens=300):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.1}).encode()
    req = urllib.request.Request(DEEPINFRA, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
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
    """Extract verify path from model response, handling JSON, markdown, and reasoning wrappers."""
    if not text or text.startswith("ERROR"):
        return ""
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text.strip(), flags=re.MULTILINE)
    # Strip reasoning tags (Gemini, DeepSeek thinking)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<reasoning>.*?</reasoning>', '', text, flags=re.DOTALL)
    # Try JSON extraction
    m = re.search(r'"verify"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    return ""

def fmt_tt(u):
    return u.get("total_tokens", "?")

def main():
    random.seed(42)
    all_results = []
    totals = {"api_calls": 0, "errors": 0, "tokens": 0}

    for case_idx, (label, repo_path, src_file, task, known_tests) in enumerate(CASES):
        if not os.path.isdir(repo_path):
            print(f"\nSKIP {case_idx+1}: {repo_path} not found")
            continue

        # Vocab output (shared per case)
        vp_out = run_vocab(["verify-packet", "--files", src_file, "--task", task, "--format", "json"], repo_path)
        try:
            vpd = json.loads(vp_out) if vp_out.strip() else {}
        except:
            vpd = {}
        vtier = vpd.get("tier", "no_vocab")
        deter = vpd.get("deterministic_verify") or {}
        det_file = deter.get("file", "")
        candidates = [str(c) for c in vpd.get("verification_candidates", [])]

        for model in MODELS:
            slug = model.split("/")[-1]
            case_cid = len(all_results)

            # Build prompts
            base_prompt = (
                f"Which test file verifies this change?\n"
                f"Source file: {src_file}\n"
                f"Task: {task}\n"
                f'Reply with ONLY a JSON object: {{"verify": "<path>"}}. '
                f"Respond with the JSON and nothing else."
            )
            vocab_prompt = (
                f"Which test file verifies this change?\n"
                f"Source file: {src_file}\n"
                f"Task: {task}\n"
                f"Vocab structural analysis: tier={vtier}"
                + (f", candidates={candidates[:4]}" if candidates else "")
                + (f", deterministic={det_file}" if det_file else "")
                + "\n"
                f'Reply with ONLY a JSON object: {{"verify": "<path>"}}. '
                f"Respond with the JSON and nothing else."
            )

            # Run baseline
            base_raw, base_u = call_llm(model, base_prompt)
            totals["api_calls"] += 1
            if base_raw.startswith("ERROR"):
                totals["errors"] += 1
            base_pred = extract_path(base_raw)
            base_tt = base_u.get("total_tokens", 0)
            totals["tokens"] += base_tt or 0

            time.sleep(0.5)

            # Run vocab
            vocab_raw, vocab_u = call_llm(model, vocab_prompt)
            totals["api_calls"] += 1
            if vocab_raw.startswith("ERROR"):
                totals["errors"] += 1
            vocab_pred = extract_path(vocab_raw)
            vocab_tt = vocab_u.get("total_tokens", 0)
            totals["tokens"] += vocab_tt or 0

            time.sleep(0.5)

            # Shuffle A/B
            flip = bool(random.getrandbits(1))
            if flip:
                out_a, out_b = base_pred, vocab_pred
                a_cond, b_cond = "baseline", "vocab"
            else:
                out_a, out_b = vocab_pred, base_pred
                a_cond, b_cond = "vocab", "baseline"

            all_results.append({
                "case_id": case_cid,
                "case_num": case_idx + 1,
                "label": label,
                "model_slug": slug,
                "model": model,
                "known_tests": known_tests,
                "vocab_tier": vtier,
                "A": out_a, "A_cond": a_cond,
                "B": out_b, "B_cond": b_cond,
                "token_base": base_tt,
                "token_vocab": vocab_tt,
                "base_raw": base_raw[:80],
                "vocab_raw": vocab_raw[:80],
            })

            sep = "✓" if any(k in (out_a if flip else out_b) for k in known_tests) else "✗"
            print(f"  [{case_idx+1:2d}/{slug:25s}] A({a_cond[:4]}): {out_a[:45]:45s} B({b_cond[:4]}): {out_b[:45]:45s} tok=({base_tt},{vocab_tt})")

    print(f"\n{'='*70}")
    print(f"API calls: {totals['api_calls']}, errors: {totals['errors']}, tokens: {totals['tokens']}")
    print(f"{'='*70}")

    # Write blind output (no condition labels)
    blind_path = os.path.join(OUT_DIR, "blind_benchmark_v2.txt")
    with open(blind_path, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("BLIND STRUCTURAL BENCHMARK v2\n")
        f.write("3 models × 10 cases = 60 trials (shuffled A/B)\n")
        f.write('Judge each: which output is the CORRECT test file?\n')
        f.write('Reply: "A", "B", "BOTH", or "NEITHER"\n')
        f.write("=" * 70 + "\n\n")
        for r in all_results:
            f.write(f"--- Case {r['case_id']+1} ---\n")
            f.write(f"Source: {r['label']}\n")
            f.write(f"Model: {r['model_slug']}\n")
            f.write(f"Tokens: base={r['token_base']}, vocab={r['token_vocab']}\n")
            if r['known_tests']:
                f.write(f"Known test locations: {r['known_tests'][:3]}\n")
            f.write(f"\n  Output A: {r['A']}\n")
            f.write(f"  Output B: {r['B']}\n")
            f.write(f"\n  Your judgment (A/B/BOTH/NEITHER): ____\n")
            f.write(f"\n  Notes: ___________________________\n")
            f.write("\n" + "-" * 50 + "\n\n")
        f.write("\n" + "=" * 70 + "\nEND\n" + "=" * 70 + "\n")
    print(f"Blind test written to {blind_path}")

    # Save key
    key_path = os.path.join(OUT_DIR, "blind_benchmark_v2_key.json")
    with open(key_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Key saved to {key_path}")

    # Quick auto-score
    print(f"\n{'='*70}")
    print("  AUTO-SCORE (known_test matching)")
    print(f"{'='*70}")
    for model in MODELS:
        slug = model.split("/")[-1]
        rows = [r for r in all_results if r["model"] == model]
        base_ok = sum(1 for r in rows if r["known_tests"] and any(
            (r["A_cond"]=="baseline" and k in r["A"]) or (r["B_cond"]=="baseline" and k in r["B"])
            for k in r["known_tests"]
        ))
        vocab_ok = sum(1 for r in rows if r["known_tests"] and any(
            (r["A_cond"]=="vocab" and k in r["A"]) or (r["B_cond"]=="vocab" and k in r["B"])
            for k in r["known_tests"]
        ))
        n = len(rows)
        base_tt = sum(r.get("token_base",0) or 0 for r in rows)
        vocab_tt = sum(r.get("token_vocab",0) or 0 for r in rows)
        both = sum(1 for r in rows if r["known_tests"] and any(
            ((r["A_cond"]=="baseline" and k in r["A"]) or (r["B_cond"]=="baseline" and k in r["B"]))
            and ((r["A_cond"]=="vocab" and k in r["A"]) or (r["B_cond"]=="vocab" and k in r["B"]))
            for k in r["known_tests"]
        ))
        print(f"\n  {slug}:")
        print(f"    Baseline: {base_ok}/{n} ({base_ok/n*100:.0f}%)  tok={base_tt}")
        print(f"    Vocab:    {vocab_ok}/{n} ({vocab_ok/n*100:.0f}%)  tok={vocab_tt}")
        print(f"    Both OK:  {both}/{n}")
        print(f"    Vocab fix: {vocab_ok - base_ok}/{n - both}")
        print(f"    Vocab regress: {base_ok - both}/{n - both}")

if __name__ == "__main__":
    main()
