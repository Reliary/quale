#!/usr/bin/env python3
"""Controlled experiment: does vocab guidance improve a 7B model's code output?

Design:
  Factor: Guidance type (none / summary / checklist)
  Reps: 3 per condition (9 total)
  Model: mistralai/Mistral-7B-Instruct-v0.3 (DeepInfra)
  Task: "Add RequestLogger middleware to gin"
  Repo: gin-gonic/gin (Go, ~60 files, clear middleware pattern)

For ALL conditions the model receives:
  1. Listing of all Go source files
  2. Contents of gin.go (types) + auth.go (pattern)

For guidance conditions, vocab output is prepended.

Scoring:
  - Compiles (go vet)          binary
  - Tests pass (go test)       binary 0-1
  - Pattern match score        0-3 (HandlerFunc sig, Context, returns func, uses Next)
  - Files touched              count
  - Token usage                input + output tokens
"""

import json, os, re, shutil, subprocess, sys, time, tempfile
from pathlib import Path

# ── config ──────────────────────────────────────────────────────────────
GIN_SOURCE = Path("/tmp/corpus-sweep/gin")
TRIALS = 3
MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
TASK = "Add a new middleware called RequestLogger to gin. The middleware logs every incoming HTTP request with method, path, and status code, then passes control to the next handler."

# DeepInfra
DEEPINFRA_KEY = os.environ.get("DEEPINFRA_API_KEY") or "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"

WORK_DIR = Path("/tmp/gin-experiment")
WORK_DIR.mkdir(parents=True, exist_ok=True)

# ── helpers ─────────────────────────────────────────────────────────────

def deepinfra_call(messages: list[dict], temperature: float = 0.1) -> dict:
    import urllib.request, urllib.error

    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2048,
    }).encode()

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPINFRA_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def copy_repo(tag: str, trial: int) -> Path:
    dest = WORK_DIR / f"gin-{tag}-{trial}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(GIN_SOURCE, dest, symlinks=False)
    return dest


def go_files(repo: Path) -> list[Path]:
    files = sorted(repo.rglob("*.go"))
    return [f for f in files if f.is_file()]


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _go_bin() -> str:
    for p in [
        "/home/user/.local/share/mise/installs/go/1.25.9/bin/go",
        "/home/user/.local/share/mise/installs/go/1.25/bin/go",
        "/usr/local/go/bin/go",
    ]:
        if os.path.isfile(p):
            return p
    return "go"


def runs_ok(repo: Path) -> dict:
    """Score: compile check + test pass."""
    result = {"compiles": False, "tests_pass": False, "vet_stderr": "", "test_stderr": ""}
    go_bin = _go_bin()

    vet = subprocess.run(
        [go_bin, "vet", "./..."],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    result["compiles"] = vet.returncode == 0
    result["vet_stderr"] = vet.stderr[:200] if vet.stderr else ""

    test = subprocess.run(
        [go_bin, "test", "./..."],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    result["tests_pass"] = test.returncode == 0
    result["test_stderr"] = test.stderr[:200] if test.stderr else ""
    return result


def pattern_score(code: str) -> int:
    """Score how well the code follows gin middleware conventions. 0-3."""
    score = 0
    if re.search(r'gin\.HandlerFunc', code) or re.search(r'HandlerFunc', code):
        score += 1
    if re.search(r'func\(.*c\s*\*gin\.Context\)', code) or re.search(r'func\(.*\*gin\.Context\)', code):
        score += 1
    if re.search(r'c\.Next\(\)', code) or re.search(r'\.Next\(\)', code):
        score += 1
    return score


def extract_code(response_text: str) -> str:
    """Extract Go code from model response — prefers diff, falls back to code blocks."""
    # Try ```go ... ``` block
    m = re.search(r'```(?:go)?\s*\n(.*?)```', response_text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Try diff block
    m = re.search(r'```(?:diff)?\s*\n(.*?)```', response_text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Take everything
    return response_text.strip()


def apply_code(repo: Path, code: str) -> list[str]:
    """Try to apply extracted code. Returns list of errors."""
    errors = []

    # Try as unified diff
    if any(line.startswith('--- ') for line in code.split('\n')[:5]):
        patch_file = repo / "_patch.diff"
        patch_file.write_text(code)
        result = subprocess.run(
            ["git", "apply", "--ignore-space-change", str(patch_file)],
            cwd=repo, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            patch_file.unlink()
            errors.append(f"patch applied: {result.stderr[:100]}")
            return errors
        patch_file.unlink()

    # Try to find new file or edit
    # Check if there's a filename in the response
    filename = None
    for line in code.split('\n')[:10]:
        m = re.match(r'^(?:---\s+a/)?(.+\.go):', line) or re.match(r'^//\s*(\S+\.go)', line) or re.match(r'^#\s*(\S+\.go)', line)
        if m:
            filename = m.group(1)
            break

    if not filename:
        errors.append("no filename detected")
        return errors

    # Check if it's a new file or existing
    target = repo / filename
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        errors.append("filename doesn't match existing layout")
    target.write_text(code)
    return errors


def run_trial(condition: str, trial: int) -> dict:
    repo = copy_repo(condition, trial)
    start = time.time()

    # Build prompt
    files = go_files(repo)
    file_listing = "\n".join(str(f.relative_to(repo)) for f in sorted(files))

    user_msg = f"""Task: {TASK}

Repository file listing:
{file_listing}

Instructions:
1. Create a new file `middleware_logger.go` with the RequestLogger middleware.
2. The middleware must log method, path, and status code of each request (use fmt.Printf or log.Printf).
3. Return ONLY the new file contents in a ```go code block.
4. Do NOT modify any existing files.
"""

    messages = [{"role": "user", "content": user_msg}]

    # Add guidance based on condition
    if condition in ("summary", "checklist"):
        fmt = "--summary" if condition == "summary" else "--format checklist"
        try:
            vocab_result = subprocess.run(
                [sys.executable, "-m", "vocab.cli", "agent-bootstrap", str(repo), "--task", TASK, fmt],
                cwd=Path.home() / "src/vocab",
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "PYTHONPATH": str(Path.home() / "src/vocab")},
            )
            guidance = vocab_result.stdout if vocab_result.returncode == 0 else f"(vocab error: {vocab_result.stderr[:100]})"
        except Exception as e:
            guidance = f"(vocab error: {e})"

        messages.insert(0, {
            "role": "system",
            "content": f"Here is structural analysis of the codebase to help you:\n\n{guidance}"
        })

    # Call model
    result = deepinfra_call(messages, temperature=0.1)
    elapsed = time.time() - start

    if "error" in result:
        return {"condition": condition, "trial": trial, "error": result["error"], "elapsed": elapsed}

    response_text = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})

    # Extract and apply code
    code = extract_code(response_text)
    apply_errors = apply_code(repo, code)

    # Score
    run_result = runs_ok(repo)
    pat = pattern_score(code)

    return {
        "condition": condition,
        "trial": trial,
        "elapsed": round(elapsed, 1),
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "compiles": run_result["compiles"],
        "tests_pass": run_result["tests_pass"],
        "pattern_score": pat,
        "vet_stderr": run_result["vet_stderr"],
        "test_stderr": run_result["test_stderr"],
        "response_preview": response_text[:300],
        "code_extracted": code[:500],
        "apply_errors": apply_errors,
    }


# ── main ────────────────────────────────────────────────────────────────

def main():
    conditions = ["baseline", "summary", "checklist"]
    results = []

    print(f"gin experiment: {TRIALS} trials × 3 conditions = {TRIALS * 3} runs")
    print(f"model: {MODEL}")
    print(f"source: {GIN_SOURCE}")
    print()

    for cond in conditions:
        for t in range(1, TRIALS + 1):
            print(f"  [{cond} trial {t}/{TRIALS}] ...", end=" ", flush=True)
            r = run_trial(cond, t)
            results.append(r)
            status = "✓" if r.get("compiles") else "✗"
            extra = f" pattern={r.get('pattern_score','?')} tok={r.get('input_tokens',0)}+{r.get('output_tokens',0)} {r.get('elapsed',0)}s"
            print(f"{status}{extra}")

    print(f"\n{'=' * 70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Condition':<12} {'Comp':<6} {'Test':<6} {'Pat':<6} {'InTok':<8} {'OutTok':<8} {'Time':<8}")
    print(f"{'-'*12} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")
    for r in results:
        c_tag = "✓" if r.get("compiles") else "✗"
        t_tag = "✓" if r.get("tests_pass") else "✗"
        print(f"{r['condition']:<12} {c_tag:<6} {t_tag:<6} {r.get('pattern_score',0):<6} {r.get('input_tokens',0):<8} {r.get('output_tokens',0):<8} {r.get('elapsed',0):<8}")

    # Summarize
    print(f"\n{'─' * 50}")
    for cond in conditions:
        cond_results = [r for r in results if r["condition"] == cond]
        compile_ok = sum(1 for r in cond_results if r.get("compiles"))
        test_ok = sum(1 for r in cond_results if r.get("tests_pass"))
        avg_pat = sum(r.get("pattern_score", 0) for r in cond_results) / len(cond_results)
        avg_in = sum(r.get("input_tokens", 0) for r in cond_results) / len(cond_results)
        avg_out = sum(r.get("output_tokens", 0) for r in cond_results) / len(cond_results)
        avg_elapsed = sum(r.get("elapsed", 0) for r in cond_results) / len(cond_results)
        print(f"  {cond}: compile={compile_ok}/{TRIALS} test={test_ok}/{TRIALS} pat={avg_pat:.1f} tok={avg_in:.0f}+{avg_out:.0f} time={avg_elapsed:.0f}s")

    # Save full results
    report_path = WORK_DIR / "results.json"
    report_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results saved to {report_path}")


if __name__ == "__main__":
    main()
