#!/usr/bin/env python3
"""Experiment 4: can vocab help on repos the model has never seen?

Uses our private repos: autopsylab-agent, autopsylab, vocab, llm-semantic-transport.
These are NOT in Mistral-7B training data. Vocab should provide unique value.
"""

import json, os, subprocess, sys, urllib.request, urllib.error
from pathlib import Path

DEEPINFRA_KEY = os.environ.get("DEEPINFRA_API_KEY") or "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
TRIALS = 4
VOCAB_DIR = Path.home() / "src/vocab"

FIXTURES = [
    ("/home/user/src/autopsylab-agent", "add a new typed evidence envelope called CacheEvidence that extends TypedEvidenceEnvelope",
     "packages/core/src/typed-evidence.ts",
     ["packages/core/src/typed-evidence.ts", "packages/core/src/types.ts", "packages/core/src/redaction.ts"]),
    ("/home/user/src/autopsylab", "add a new handler for listing fingerprints by source",
     "internal/handlers/fingerprint_read.go",
     ["internal/handlers/fingerprint_read.go", "internal/handlers/app.go", "internal/services/fingerprint.go"]),
    ("/home/user/src/vocab", "add a new command called workspace-diff that compares vocabulary across two repos",
     "vocab/cli.py",
     ["vocab/cli.py", "vocab/scanner.py", "vocab/bootstrap.py"]),
    ("/home/user/src/llm-semantic-transport", "add a new compression backend called minhash for fuzzy duplicate detection",
     "app/compression/__init__.py",
     ["app/compression/__init__.py", "app/compression/crispr_v2_backend.py", "app/compression/base.py"]),
]

def deepinfra_call(messages, temperature=0.2):
    body = json.dumps({"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": 800}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {DEEPINFRA_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}

def get_source_files(repo_path):
    exts = {'.py', '.go', '.ts', '.js', '.rs'}
    repo = Path(repo_path)
    files = []
    for f in sorted(repo.rglob("*")):
        if f.suffix in exts and f.is_file():
            rel = str(f.relative_to(repo))
            parts = rel.split("/")
            if any(d in parts for d in ('.git','node_modules','vendor','target','build','dist','__pycache__','artifacts')):
                continue
            files.append(rel)
    return files[:80]

def vocab_guidance(repo_path, task, fmt="summary"):
    flag = "--summary" if fmt == "summary" else "--format checklist"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "vocab.cli", "agent-bootstrap", repo_path, "--task", task, flag],
            cwd=VOCAB_DIR, capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(VOCAB_DIR)},
        )
        if result.returncode == 0:
            return f"\n=== VOCAB ANALYSIS ===\n{result.stdout.strip()}\n=== END ANALYSIS ===\n"
        return f"\n=== VOCAB ERROR ===\n{result.stderr[:200]}\n"
    except Exception as e:
        return f"\n=== VOCAB ERROR: {e} ===\n"

def score_response(response, gt_pattern, gt_reads):
    r = response.lower()
    pattern = gt_pattern.lower() in r
    reads = sum(1 for f in gt_reads if f.lower() in r)
    return {"pattern_found": pattern, "reads_found": reads, "reads_ratio": reads / max(len(gt_reads), 1)}

def run_trial(repo_path, task, gt_pattern, gt_reads, condition, trial):
    files = get_source_files(repo_path)
    file_listing = "\n".join(files)
    repo_name = Path(repo_path).name

    prompt = f"""Repository: {repo_name}
Task: {task}

Source files in the repository:
{file_listing}

This is a private codebase NOT in your training data. Use the file names and structure to reason carefully.

Answer in this exact format:
PATTERN: <filename of the existing file to follow as a pattern>
READ: <3 files to read first>
EDIT: <1 file to edit>
"""
    system = "You are a senior engineer analyzing an unfamiliar private codebase."
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    if condition != "baseline":
        guidance = vocab_guidance(repo_path, task, fmt=condition)
        messages[0]["content"] = system + "\n\n" + guidance

    result = deepinfra_call(messages, temperature=0.2)
    if "error" in result:
        return {"condition": condition, "trial": trial, "repo": repo_name, "error": result["error"]}
    response = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    scores = score_response(response, gt_pattern, gt_reads)
    return {"condition": condition, "trial": trial, "repo": repo_name, "pattern_found": scores["pattern_found"],
            "reads_found": scores["reads_found"], "reads_ratio": scores["reads_ratio"],
            "response": response, "input_tokens": usage.get("prompt_tokens", 0)}

def main():
    all_results = []
    print(f"Experiment 4: Private repos — {len(FIXTURES)} × {TRIALS} × 3 = {len(FIXTURES)*TRIALS*3} calls\n")
    for repo_path, task, gt_pattern, gt_reads in FIXTURES:
        rn = Path(repo_path).name
        print(f"── {rn}: {task[:50]}... ──")
        for cond in ["baseline", "summary", "checklist"]:
            for t in range(1, TRIALS+1):
                r = run_trial(repo_path, task, gt_pattern, gt_reads, cond, t)
                all_results.append(r)
                tag = "✓" if r.get("pattern_found") else "✗"
                print(f"  [{rn[:8]} {cond} t{t}] {tag} pat={r.get('pattern_found',0)} reads={r.get('reads_found',0)}")

    print(f"\n{'='*70}")
    print(f"  PRIVATE REPO RESULTS")
    print(f"{'='*70}")
    for cond in ["baseline", "summary", "checklist"]:
        cr = [r for r in all_results if r["condition"] == cond and "error" not in r]
        pat_ok = sum(1 for r in cr if r.get("pattern_found"))
        avg_r = sum(r.get("reads_found", 0) for r in cr) / len(cr) if cr else 0
        print(f"  {cond:<10}: pattern_acc={pat_ok}/{len(cr)} ({pat_ok/max(len(cr),1)*100:.0f}%) avg_reads={avg_r:.1f}")

    # Per-repo detail
    print(f"\n{'─'*70}")
    for rn in sorted(set(r["repo"] for r in all_results if "error" not in r)):
        print(f"\n── {rn} ──")
        for cond in ["baseline", "summary", "checklist"]:
            cr = [r for r in all_results if r["repo"] == rn and r["condition"] == cond and "error" not in r]
            if not cr: continue
            pat_ok = sum(1 for r in cr if r.get("pattern_found"))
            print(f"  {cond:<10}: pattern_acc={pat_ok}/{len(cr)}")

    Path("/tmp/discovery-experiment/experiment4_results.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved to /tmp/discovery-experiment/experiment4_results.json")

if __name__ == "__main__":
    main()
