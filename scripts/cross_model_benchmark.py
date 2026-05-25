#!/usr/bin/env python3
"""Cross-model benchmark: baseline vs vocab across many repos.
Auto-discovers test pairs and documents when naming convention is the real reason for correctness.
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

def classify_pair(src, test):
    """Classify if test pair follows naming convention or requires structural knowledge."""
    s = Path(src)
    t = Path(test)
    # Same directory -> naming convention
    if s.parent == t.parent:
        return "same-dir (naming convention)"
    # Parallel directory (src/ -> tests/)
    if (str(s.parent).endswith("/src") or "/src/" in str(s.parent)) \
       and (str(t.parent).endswith("/tests") or "/tests/" in str(t.parent)):
        return "cross-dir (structural)"
    # Other
    return f"cross-dir ({s.parent} -> {t.parent})"

def find_test_pairs_verbose(repo_path, limit=6):
    """Auto-discover source→test file pairs, prioritizing structural-cross-dir pairs."""
    repo_path = Path(repo_path).resolve()
    all_files = []
    try:
        r = subprocess.run(["git", "ls-files"], cwd=repo_path, capture_output=True, text=True, timeout=30)
        all_files = r.stdout.strip().split("\n")
    except:
        return []

    files = [f for f in all_files if f and not f.startswith("vendor/")
             and not f.startswith("node_modules/") and not f.startswith(".")
             and "/third_party/" not in f and "/testdata/" not in f]

    # Check for cross-dir test patterns
    patterns_list = [
        # TypeScript: src/X.ts -> tests/X.test.ts or src/__tests__/X.ts
        (r'\.ts$', [r'\1.test\2', r'tests/\1.test\2']),
        # Python: src/X.py -> tests/test_X.py or src/test_X.py
        (r'\.py$', [r'tests/test_\1\2', r'test_\1\2']),
        # Rust: src/X.rs -> tests/X.rs
        (r'\.rs$', [r'tests/\1\2']),
        # Go: cmd/X/main.go -> cmd/X/main_test.go (same dir convention)
        (r'\.go$', [r'\1_test\2']),
    ]

    file_set = set(files)
    pairs = []

    # First pass: look for cross-dir structural pairs
    for f in files:
        p = Path(f)
        ext = p.suffix
        stem = p.stem
        if stem.endswith("_test") or stem.endswith("_spec") or ".test." in str(p):
            continue
        if not re.search(r'\.(py|go|rs|ts|js|rb)$', f):
            continue

        # Cross-directory patterns
        cross_candidates = []
        if ext == ".ts":
            # src/X.ts -> tests/X.test.ts
            src_dir = str(p.parent)
            if "src" in src_dir.split("/"):
                cross_candidates.append(f.replace("/src/", "/tests/").replace(ext, f".test{ext}"))
                cross_candidates.append(f.replace("/src/", "/tests/").replace(ext, f"_test{ext}"))
                cross_candidates.append(str(p.parent / f"{stem}.test{ext}"))
                cross_candidates.append(str(p.parent / f"__tests__/{stem}{ext}"))
        elif ext == ".py":
            # src/X.py -> tests/test_X.py
            if "src" in src_dir.split("/"):
                cross_candidates.append(f.replace("/src/", "/tests/").replace(f"/{stem}{ext}", f"/test_{stem}{ext}"))
                cross_candidates.append(str(p.parent / f"test_{stem}{ext}"))

        for c in cross_candidates:
            if c in file_set:
                pairs.append((f, c, "cross-dir (structural)"))
                break

    # Second pass: same-dir naming convention pairs
    for f in files:
        if len(pairs) >= limit:
            break
        p = Path(f)
        ext = p.suffix
        stem = p.stem
        if not re.search(r'\.(py|go|rs|ts|js|rb)$', f):
            continue
        if stem.endswith("_test") or stem.endswith("_spec") or ".test." in str(p):
            continue

        same_dir = []
        if ext == ".go":
            same_dir.append(str(p.parent / f"{stem}_test.go"))
        elif ext == ".py":
            same_dir.append(str(p.parent / f"test_{stem}.py"))
            same_dir.append(str(p.parent / f"{stem}_test.py"))
        elif ext == ".ts" or ext == ".js":
            same_dir.append(str(p.parent / f"{stem}.test{ext}"))
            same_dir.append(str(p.parent / f"{stem}_test{ext}"))
        elif ext == ".rs":
            same_dir.append(str(p.parent / f"{stem}_test.rs"))
        elif ext == ".rb":
            same_dir.append(str(p.parent / f"{stem}_spec.rb"))

        for c in same_dir:
            if c in file_set and not any(c == t for _, t, _ in pairs):
                pairs.append((f, c, "same-dir (naming convention)"))
                break

    return pairs[:limit]

REPOS = [
    # Private repos (test pairs from prior knowledge)
    ("autopsylab-agent.ts", "/home/user/src/autopsylab-agent"),
    ("autopsylab.go", "/home/user/src/autopsylab"),
    ("llm-semantic-transport.py", "/home/user/src/llm-semantic-transport"),
    ("vocab.py", "/home/user/src/vocab"),
    # Likely-seen public
    ("flask.py", "/tmp/corpus-sweep/flask"),
    ("prometheus.go", "/tmp/corpus-sweep/prometheus"),
    ("serde.rs", "/tmp/corpus-sweep/serde"),
    ("pandas.py", "/tmp/corpus-sweep/pandas"),
    # Weird language
    ("nginx.c", "/tmp/corpus-sweep/nginx"),
    ("nim", "/tmp/corpus-sweep/Nim"),
    ("otp.erl", "/tmp/corpus-sweep/otp"),
]

def main():
    results = []
    stats = {"same_dir_baseline_ok": 0, "same_dir_total": 0,
             "cross_dir_baseline_ok": 0, "cross_dir_total": 0}

    for repo_name, repo_path in REPOS:
        if not os.path.isdir(repo_path) or not os.path.isdir(os.path.join(repo_path, ".git")):
            print(f"\nSKIP {repo_name}: not a git repo")
            continue

        pairs = find_test_pairs_verbose(repo_path)
        if not pairs:
            print(f"\nSKIP {repo_name}: no test pairs found")
            continue

        print(f"\n{'='*70}")
        print(f"REPO: {repo_name}")
        for src, test, pair_type in pairs[:3]:
            print(f"  {src:50s} → {test:45s} [{pair_type}]")
        print(f"{'='*70}")

        for src_file, gt_test, pair_type in pairs[:3]:
            task = f"modify {Path(src_file).name}"
            print(f"\n  [{pair_type}] {src_file}")
            print(f"    GT: {gt_test}")

            # Cache vocab per file (NOT per repo — each file has different candidates)
            vp_out = run_vocab(["verify-packet", "--files", src_file, "--task", task, "--format", "json"], repo_path)
            try:
                vpd = json.loads(vp_out) if vp_out.strip() else {}
            except json.JSONDecodeError:
                vpd = {}
            candidates = [str(c) for c in vpd.get("verification_candidates", [])]
            deter = vpd.get("deterministic_verify", {})
            vtier = vpd.get("tier", "?")

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
                    match = gt_test == pred or gt_test in pred
                    total_tok = usage.get("total_tokens", 0)
                    prompt_tok = usage.get("prompt_tokens", 0)
                    output_tok = usage.get("completion_tokens", 0)
                    status = "✓" if match else "✗"

                    if condition == "baseline":
                        if pair_type.startswith("same-dir"):
                            stats["same_dir_total"] += 1
                            if match: stats["same_dir_baseline_ok"] += 1
                        else:
                            stats["cross_dir_total"] += 1
                            if match: stats["cross_dir_baseline_ok"] += 1

                    print(f"    {short:25s} [{condition:8s}] {pred:50s} {status}  p={prompt_tok} o={output_tok} tot={total_tok}")
                    results.append({"repo": repo_name, "file": src_file, "gt": gt_test,
                        "pair_type": pair_type, "model": model, "condition": condition,
                        "prediction": pred, "correct": match,
                        "prompt_tokens": prompt_tok, "output_tokens": output_tok, "total_tokens": total_tok,
                        "vocab_tier": vtier, "vocab_candidates": str(candidates[:4])})
                time.sleep(0.3)

    # Summary
    print(f"\n{'='*60}")
    print("OVERALL BY MODEL")
    print(f"{'='*60}")
    for model in MODELS:
        mr = [r for r in results if r["model"] == model]
        for ct in ["cross-dir", "same-dir"]:
            cr = [r for r in mr if ct in r["pair_type"]]
            if not cr:
                continue
            base_ok = sum(1 for r in cr if r["condition"]=="baseline" and r["correct"])
            base_tot = sum(1 for r in cr if r["condition"]=="baseline")
            voc_ok = sum(1 for r in cr if r["condition"]=="vocab" and r["correct"])
            voc_tot = sum(1 for r in cr if r["condition"]=="vocab")
            base_pct = base_ok/base_tot*100 if base_tot else 0
            voc_pct = voc_ok/voc_tot*100 if voc_tot else 0
            s = f"{base_ok}/{base_tot} ({base_pct:.0f}%)"
            vs = f"{voc_ok}/{voc_tot} ({voc_pct:.0f}%)"
            print(f"  {model.split('/')[-1][:30]:30s} {ct:20s}  base={s:15s}  vocab={vs}")

    print(f"\n{'='*60}")
    print("STRUCTURAL HYPOTHESIS")
    print(f"{'='*60}")
    if stats["same_dir_total"] > 0:
        sd_pct = stats["same_dir_baseline_ok"]/stats["same_dir_total"]*100
        print(f"  Same-dir naming conventions: baseline correct {stats['same_dir_baseline_ok']}/{stats['same_dir_total']} ({sd_pct:.0f}%)")
    if stats["cross_dir_total"] > 0:
        cd_pct = stats["cross_dir_baseline_ok"]/stats["cross_dir_total"]*100
        print(f"  Cross-dir structural:        baseline correct {stats['cross_dir_baseline_ok']}/{stats['cross_dir_total']} ({cd_pct:.0f}%)")
    print(f"  Hypothesis: vocab helps most on cross-dir patterns where naming convention alone is insufficient")

    # Save
    out_path = "/tmp/vocab-cross-model-v2.json"
    with open(out_path, "w") as f:
        json.dump({"results": results, "stats": stats}, f, indent=2)
    print(f"\nSaved to {out_path}")

if __name__ == "__main__":
    main()
