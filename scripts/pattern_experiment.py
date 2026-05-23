#!/usr/bin/env python3
"""Experiment 3: can structural signals help find pattern files?

The key insight from experiment 2: keyword matching can't find pattern files
(auth.go contains "BasicAuth" not "compression"). Models also fail from filenames.

This experiment tests: does vocab's structural output help a model identify
the PATTERN FILE to imitate, not just the EDIT target?

Conditions:
1. Baseline: model sees filenames only
2. Vocab summary: model sees vocab agent-bootstrap --summary output
3. Vocab checklist: model sees vocab agent-bootstrap --format checklist output

Key change: task asks for READ/EDIT AND "which existing file shows the pattern to follow"
"""

import json, os, re, subprocess, sys, urllib.request, urllib.error
from pathlib import Path

DEEPINFRA_KEY = os.environ.get("DEEPINFRA_API_KEY") or "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
TRIALS = 3
VOCAB_DIR = Path.home() / "src/vocab"

# (repo_path, task, ground_truth_pattern_file, ground_truth_reads)
FIXTURES = [
    ("/tmp/corpus-sweep/gin", "add request logging middleware",
     "logger.go",        # existing logger middleware to follow
     ["logger.go", "context.go", "gin.go"]),
    ("/tmp/corpus-sweep/gin", "add auth middleware for JWT tokens",
     "auth.go",          # existing auth middleware to follow
     ["auth.go", "context.go", "gin.go"]),
    ("/tmp/corpus-sweep/flask", "add middleware to measure request durations",
     "src/flask/views.py",  # existing class-based view pattern
     ["src/flask/app.py", "src/flask/views.py", "src/flask/helpers.py"]),
    ("/tmp/corpus-sweep/serde", "add enum flattened derive variant (following untagged pattern)",
     "serde_derive/src/de/enum_untagged.rs",  # existing untagged pattern
     ["serde_derive/src/de/enum_untagged.rs", "serde_derive/src/de.rs", "serde_derive/src/lib.rs"]),
]

def deepinfra_call(messages, temperature=0.2):
    body = json.dumps({"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": 1024}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPINFRA_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}

def get_source_files(repo_path):
    exts = {'.py', '.go', '.rs', '.ts', '.js'}
    repo = Path(repo_path)
    files = []
    for f in sorted(repo.rglob("*")):
        if f.suffix in exts and f.is_file():
            rel = str(f.relative_to(repo))
            if any(d in rel.split("/") for d in ('.git', 'node_modules', 'vendor', 'target', 'build', 'dist')):
                continue
            files.append(rel)
    return files[:100]

def vocab_guidance(repo_path, task, fmt="summary"):
    flag = "--summary" if fmt == "summary" else "--format checklist"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "vocab.cli", "agent-bootstrap", repo_path, "--task", task, flag],
            cwd=VOCAB_DIR, capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(VOCAB_DIR)},
        )
        if result.returncode == 0:
            return f"\n=== STRUCTURAL GUIDANCE ===\n{result.stdout.strip()}\n=== END GUIDANCE ===\n"
        return f"\n=== VOCAB ERROR ===\n{result.stderr[:200]}\n"
    except Exception as e:
        return f"\n=== VOCAB ERROR: {e} ===\n"

def score_response(response, gt_pattern, gt_reads):
    r_lower = response.lower()
    pattern_found = gt_pattern.lower() in r_lower
    reads_found = sum(1 for f in gt_reads if f.lower() in r_lower)
    return {"pattern_found": pattern_found, "reads_found": reads_found, "reads_ratio": reads_found / max(len(gt_reads), 1)}

def run_trial(repo_path, task, gt_pattern, gt_reads, condition, trial):
    files = get_source_files(repo_path)
    file_listing = "\n".join(files)

    prompt = f"""Repository: {Path(repo_path).name}
Task: {task}

Files:
{file_listing}

I need to understand which file to use as a PATTERN for the new implementation.

Answer in this exact format:
PATTERN: <filename of existing file to follow as pattern>
READ: <file1>, <file2>, <file3>
EDIT: <filename of new file to create>
"""
    system = "You are a senior engineer."
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    if condition != "baseline":
        guidance = vocab_guidance(repo_path, task, fmt=condition)
        messages[0]["content"] = system + "\n\n" + guidance

    result = deepinfra_call(messages, temperature=0.2)
    if "error" in result:
        return {"condition": condition, "trial": trial, "repo": Path(repo_path).name, "task": task, "error": result["error"]}

    response = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    scores = score_response(response, gt_pattern, gt_reads)
    return {
        "condition": condition, "trial": trial, "repo": Path(repo_path).name,
        "task": task, "pattern_found": scores["pattern_found"],
        "reads_found": scores["reads_found"], "reads_ratio": scores["reads_ratio"],
        "response": response, "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "gt_pattern": gt_pattern,
    }

def main():
    all_results = []
    print(f"Experiment 3: Pattern discovery — {len(FIXTURES)} configs × {TRIALS} × 3 = {len(FIXTURES)*TRIALS*3} runs\n")
    for repo_path, task, gt_pattern, gt_reads in FIXTURES:
        repo_name = Path(repo_path).name
        task_short = task[:40]
        print(f"── {repo_name}: {task_short} ──")
        for cond in ["baseline", "summary", "checklist"]:
            for t in range(1, TRIALS + 1):
                r = run_trial(repo_path, task, gt_pattern, gt_reads, cond, t)
                all_results.append(r)
                status = "✓" if r.get("pattern_found") else "✗"
                print(f"  [{repo_name} {cond} t{t}] {status} pattern={r.get('pattern_found',0)} reads={r.get('reads_found',0)}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  PATTERN DISCOVERY RESULTS")
    print(f"{'='*70}")
    for cond in ["baseline", "summary", "checklist"]:
        cr = [r for r in all_results if r["condition"] == cond and "error" not in r]
        pattern_ok = sum(1 for r in cr if r.get("pattern_found"))
        avg_reads = sum(r.get("reads_found", 0) for r in cr) / len(cr)
        print(f"  {cond:<10}: pattern_acc={pattern_ok}/{len(cr)} ({pattern_ok/max(len(cr),1)*100:.0f}%) avg_reads={avg_reads:.1f}")

    Path("/tmp/discovery-experiment/experiment3_results.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nResults saved to /tmp/discovery-experiment/experiment3_results.json")

if __name__ == "__main__":
    main()
