#!/usr/bin/env python3
"""File discovery experiment: does vocab guidance help a 7B model find the right files?

Design:
  - 6 repo × task pairs across multiple languages
  - Each pair has a known ground-truth file (the one that would actually be edited)
  - Model sees: all filenames + (optionally) vocab guidance
  - Model answers: which 3 files to read, which 1 to edit
  - Score: position of ground-truth in model's ranking

Repos:
  1. flask (Python) — task: "add new route decorator"
  2. gin (Go) — task: "add response compression middleware"
  3. serde_derive (Rust) — task: "add new derive macro for display"
  4. neovim/api (C) — task: "add new API function for buffer metadata"
  5. prometheus (Go) — task: "add new metric type for request duration"
  6. mermaid (TS) — task: "add new diagram type for user flow"

Ground truth determined by: vocab analyze + manual inspection.
"""

import json, os, re, subprocess, sys, urllib.request, urllib.error
from pathlib import Path

# ── config ──────────────────────────────────────────────────────────────
DEEPINFRA_KEY = os.environ.get("DEEPINFRA_API_KEY") or "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
TRIALS = 3

VOCAB_DIR = Path.home() / "src/vocab"
WORK_DIR = Path("/tmp/discovery-experiment")
WORK_DIR.mkdir(parents=True, exist_ok=True)

# ── fixtures ────────────────────────────────────────────────────────────
# Each entry: (repo_path, task, ground_truth_edit, ground_truth_reads)
FIXTURES = [
    ("/tmp/corpus-sweep/flask", "add new route decorator called @cache_route that caches responses from the route handler",
     "src/flask/app.py",
     ["src/flask/app.py", "src/flask/sansio/app.py", "src/flask/helpers.py"]),
    ("/tmp/corpus-sweep/gin", "add response compression middleware that gzips HTTP responses before sending",
     "auth.go",
     ["gin.go", "context.go", "auth.go"]),
    ("/tmp/corpus-sweep/serde", "add a new serde derive macro for displaying enum variants",
     "serde_derive/src/lib.rs",
     ["serde_derive/src/lib.rs", "serde_derive/src/de.rs", "serde_derive/src/internals/attr.rs"]),
    ("/tmp/corpus-sweep/neovim", "add new API function nvim_buf_get_lines for getting buffer lines by range",
     "src/nvim/api/buffer.c",
     ["src/nvim/api/buffer.c", "src/nvim/api/private/handle.h", "include/nvim/buffer.h"]),
    ("/tmp/corpus-sweep/django", "add new middleware that logs all HTTP request timings",
     "django/middleware/common.py",
     ["django/middleware/common.py", "django/middleware/csrf.py", "django/utils/deprecation.py"]),
    ("/tmp/corpus-sweep/mermaid", "add a new flowchart sub-type for user journey diagrams with custom styling",
     "packages/mermaid/src/diagrams/flowchart/flowDb.ts",
     ["packages/mermaid/src/diagrams/flowchart/flowDb.ts",
      "packages/mermaid/src/diagrams/flowchart/flowRenderer-v3-unified.ts",
      "packages/mermaid/src/diagrams/flowchart/types.ts"]),
]


# ── helpers ─────────────────────────────────────────────────────────────

def deepinfra_call(messages: list[dict], temperature: float = 0.1) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1024,
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPINFRA_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def get_source_files(repo_path: str) -> list[str]:
    """Get all source code file paths (relative to repo)."""
    exts = {'.py', '.go', '.rs', '.c', '.h', '.ts', '.js', '.tsx', '.jsx'}
    repo = Path(repo_path)
    files = []
    for f in sorted(repo.rglob("*")):
        if f.suffix in exts and f.is_file():
            rel = str(f.relative_to(repo))
            parts = rel.split("/")
            if any(d in parts for d in ('.git', 'node_modules', 'vendor', 'target', 'build', 'dist')):
                continue
            files.append(rel)
    return files[:100]  # cap for context window


def vocab_guidance(repo_path: str, task: str, fmt: str = "summary") -> str:
    """Run vocab and return guidance text."""
    flag = "--summary" if fmt == "summary" else "--format checklist"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "vocab.cli", "agent-bootstrap", repo_path, "--task", task, flag],
            cwd=VOCAB_DIR,
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(VOCAB_DIR)},
        )
        if result.returncode == 0:
            return f"\n=== VOCAB STRUCTURAL GUIDANCE ===\n{result.stdout.strip()}\n=== END GUIDANCE ===\n"
        return f"\n=== VOCAB ERROR: {result.stderr[:200]} ===\n"
    except Exception as e:
        return f"\n=== VOCAB ERROR: {e} ===\n"


def score_guess(guess: str, ground_truth_file: str, ground_truth_reads: list[str]) -> dict:
    """Score how close the model's guess is to ground truth."""
    guess_lower = guess.lower()

    edit_in_top = 1 if ground_truth_file.lower() in guess_lower else 0
    reads_in_answer = sum(1 for f in ground_truth_reads if f.lower() in guess_lower)
    wrong_files = len([line for line in guess.split("\n") if line.strip() and ".go" in line or ".py" in line or ".rs" in line or ".c" in line or ".ts" in line])

    # Check if the guess is in the right DIRECTION even if not exact file
    gt_dir = os.path.dirname(ground_truth_file)
    dir_match = 1 if gt_dir and gt_dir.lower() in guess_lower else 0

    return {
        "edit_exact_match": edit_in_top,
        "reads_matched": reads_in_answer / max(len(ground_truth_reads), 1),
        "reads_count": reads_in_answer,
        "directory_match": dir_match,
        "guess_preview": guess[:300],
    }


def run_trial(repo_path: str, task: str, gt_file: str, gt_reads: list[str],
              condition: str, trial: int) -> dict:
    files = get_source_files(repo_path)
    file_listing = "\n".join(files)

    prompt = f"""I need to understand a codebase to implement a task. Here is what I know:

Task: {task}

All files in the repository:
{file_listing}

Based on the file names alone:
1. Which 3 files should I READ first to understand the code structure for this task?
2. Which 1 file should I EDIT to implement this task?

Answer in this exact format:
READ: <file1>, <file2>, <file3>
EDIT: <file>
"""

    system_msg = "You are a senior engineer analyzing an unfamiliar codebase."
    messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]

    if condition in ("summary", "checklist"):
        fmt = "summary" if condition == "summary" else "checklist"
        guidance = vocab_guidance(repo_path, task, fmt=fmt)
        # Insert guidance as additional system context
        messages[0]["content"] = system_msg + f"\n\nHere is structural analysis of the codebase:\n{guidance}"

    result = deepinfra_call(messages, temperature=0.3)

    if "error" in result:
        return {"condition": condition, "trial": trial, "repo": Path(repo_path).name, "error": result["error"]}

    response = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    return {
        "condition": condition,
        "trial": trial,
        "repo": Path(repo_path).name,
        "response": response,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        **score_guess(response, gt_file, gt_reads),
    }


# ── main ────────────────────────────────────────────────────────────────
def main():
    conditions = ["baseline", "summary", "checklist"]
    all_results = []

    print(f"File discovery experiment: {len(FIXTURES)} repos × {TRIALS} trials × 3 conditions = {len(FIXTURES)*TRIALS*3} runs")
    print(f"Model: {MODEL}\n")

    for repo_path, task, gt_file, gt_reads in FIXTURES:
        repo_name = Path(repo_path).name
        print(f"── {repo_name}: {task[:50]}... ──")

        for cond in conditions:
            for t in range(1, TRIALS + 1):
                print(f"  [{repo_name} {cond} trial {t}/{TRIALS}] ...", end=" ", flush=True)
                r = run_trial(repo_path, task, gt_file, gt_reads, cond, t)
                all_results.append(r)
                if "error" in r:
                    print(f"ERROR: {r['error'][:60]}")
                else:
                    status = "✓" if r.get("edit_exact_match") else "✗"
                    print(f"{status} (edit_match={r.get('edit_exact_match',0)}, reads={r.get('reads_count',0)}/{len(gt_reads)}, input={r.get('input_tokens',0)})")

    # Summary
    print(f"\n{'='*80}")
    print(f"  FILE DISCOVERY RESULTS")
    print(f"{'='*80}")

    for repo_name in sorted(set(r["repo"] for r in all_results if "error" not in r)):
        print(f"\n── {repo_name} ──")
        for cond in conditions:
            cr = [r for r in all_results if r.get("repo") == repo_name and r["condition"] == cond and "error" not in r]
            if not cr:
                continue
            edit_acc = sum(r.get("edit_exact_match", 0) for r in cr) / len(cr)
            avg_reads = sum(r.get("reads_count", 0) for r in cr) / len(cr)
            dir_acc = sum(r.get("directory_match", 0) for r in cr) / len(cr)
            avg_in = sum(r.get("input_tokens", 0) for r in cr) / len(cr)
            avg_out = sum(r.get("output_tokens", 0) for r in cr) / len(cr)
            print(f"  {cond:<10}: edit_acc={edit_acc:.0%} avg_reads_match={avg_reads:.1f} dir_acc={dir_acc:.0%} tok={avg_in:.0f}+{avg_out:.0f}")

    # Aggregate
    print(f"\n── AGGREGATE (all repos) ──")
    for cond in conditions:
        cr = [r for r in all_results if r["condition"] == cond and "error" not in r]
        edit_acc = sum(r.get("edit_exact_match", 0) for r in cr) / len(cr)
        avg_reads = sum(r.get("reads_count", 0) for r in cr) / len(cr)
        dir_acc = sum(r.get("directory_match", 0) for r in cr) / len(cr)
        print(f"  {cond:<10}: edit_acc={edit_acc:.0%} avg_reads_match={avg_reads:.1f} dir_acc={dir_acc:.0%}")

    # Save
    report_path = WORK_DIR / "discovery_results.json"
    report_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nFull results saved to {report_path}")


if __name__ == "__main__":
    main()
