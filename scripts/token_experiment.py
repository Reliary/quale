#!/usr/bin/env python3
"""Token reduction experiment: does vocab guidance reduce agent exploration cost?

Measures total tokens consumed by deepseek-v4-flash before correctly
identifying the edit target in an unfamiliar private codebase.
"""

import json, os, re, subprocess, sys, urllib.request, urllib.error, time
from pathlib import Path

DEEPSEEK_KEY = json.loads(Path.home().joinpath('.local/share/opencode/auth.json').read_text())['deepseek']['key']
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
TRIALS = 2
MAX_TURNS = 5
MAX_READ_LINES = 100
MAX_GREP_RESULTS = 20
FILE_LISTING_CAP = 120
TEMPERATURE = 0.2
VOCAB_DIR = Path.home() / "src/vocab"
OUTPUT_PATH = Path("/tmp/token_experiment_results.json")

VERIFY_GT_FILES = {
    Path.home() / "src/autopsylab-agent": ("packages/core/src/typed-evidence.ts", "add typed evidence"),
    Path.home() / "src/autopsylab": ("internal/handlers/fingerprint_read.go", "add fingerprint handler"),
    Path.home() / "src/vocab": ("vocab/cli.py", "add workspace-diff command"),
    Path.home() / "src/llm-semantic-transport": ("app/compression/__init__.py", "add minhash backend"),
}

SYSTEM_PROMPT = """You are an agent exploring an unfamiliar codebase. Find which file to edit for the task below. You have exactly {max_turns} turns to decide.

Task: {task}

Commands:
- READ <path> — Read the first {max_read_lines} lines of a file
- DONE <path> — Submit your answer (which file to edit)
- GREP <pattern> — Search for a pattern

Use READ or GREP to explore early turns.
On the final turn, you MUST use DONE. This is your only chance to answer correctly.

Rules:
- One command per response, on its own line
- Use relative paths from repo root
- Final turn must be DONE"""


def run_vocab(repo_path, task, fmt):
    """Run vocab agent-bootstrap --format <fmt> and return output string."""
    try:
        r = subprocess.run(
            [sys.executable, '-m', 'vocab.cli', 'agent-bootstrap', str(repo_path),
             '--task', task, '--format', fmt],
            cwd=str(VOCAB_DIR), env={**os.environ, 'PYTHONPATH': str(VOCAB_DIR)},
            capture_output=True, text=True, timeout=60)
        out = r.stdout.strip()
        return out if out else None
    except Exception as e:
        return None


def deepseek_call(messages, temperature=0.2, max_tokens=800):
    body = json.dumps({"model": MODEL, "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:300] if e.fp else ""
        return {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return {"error": str(e)}


def get_file_listing(repo_path):
    exts = {'.ts', '.go', '.py', '.rs', '.js', '.tsx', '.jsx', '.rb', '.java', '.c', '.h',
            '.cpp', '.hpp', '.cs', '.swift', '.kt', '.zig', '.rs', '.ex', '.exs', '.hs',
            '.jl', '.ml', '.mli', '.nix', '.clj', '.scala', '.r'}
    sources, tests = [], []
    skip_dirs = {'.git', 'node_modules', 'vendor', 'target', 'build', 'dist',
                 '__pycache__', '.reliary', 'artifacts', 'third_party', '.vercel'}
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'ls-files', '-z'],
            capture_output=True, timeout=15)
        if result.returncode != 0:
            return [f"(error: git ls-files returned {result.returncode})"]
        all_files = result.stdout.decode('utf-8', errors='replace').strip('\0').split('\0')
        for rel in all_files:
            if not rel.strip():
                continue
            if Path(rel).suffix not in exts:
                continue
            parts = set(rel.split("/"))
            if skip_dirs & parts:
                continue
            name = rel.rsplit("/", 1)[-1].lower()
            has_test = 'test' in name or 'spec' in name
            is_source = not has_test and not rel.startswith("tests/") and not rel.startswith("spec/") and "test/" not in rel
            (tests if not is_source else sources).append(rel)
        listing = (sources + tests)[:FILE_LISTING_CAP]
        return listing if listing else [f"(no source files found in {Path(repo_path).name})"]
    except subprocess.TimeoutExpired:
        return ["(error: git ls-files timed out)"]
    except Exception as e:
        return [f"(error listing files: {e})"]


def read_file_content(repo_path, file_path):
    try:
        full_path = Path(repo_path) / file_path
        if not full_path.exists() or not full_path.is_file():
            return f"Error: file not found at {file_path}"
        lines = full_path.read_text(encoding='utf-8', errors='replace').splitlines()
        out = [f"{i+1}: {l}" for i, l in enumerate(lines[:MAX_READ_LINES])]
        return "\n".join(out)
    except Exception as e:
        return f"Error reading {file_path}: {e}"


def grep_content(repo_path, pattern):
    try:
        rg_cmd = subprocess.run(
            ['rg', '-n', '--no-heading', pattern, '--type-add', 'code:*.ts,*.go,*.py,*.rs,*.js,*.tsx,*.jsx',
             '-t', 'code', str(repo_path), '-g', '!*.min.*', '-g', '!node_modules',
             '-g', '!vendor', '-g', '!target', '-g', '!build'],
            capture_output=True, text=True, timeout=30)
        if rg_cmd.returncode != 0 and rg_cmd.stderr:
            return f"Error: {rg_cmd.stderr[:200]}"
        matches = [l for l in rg_cmd.stdout.strip().split('\n') if l.strip()]
        out = [m.replace(str(repo_path) + '/', '')[:200] for m in matches[:MAX_GREP_RESULTS]]
        if not out:
            return f"No matches for '{pattern}'"
        result = "\n".join(out)
        if len(matches) > MAX_GREP_RESULTS:
            result += f"\n(... {len(matches) - MAX_GREP_RESULTS} more matches)"
        return result
    except FileNotFoundError:
        return "Error: rg not available (install ripgrep)"
    except subprocess.TimeoutExpired:
        return f"Error: grep timed out"
    except Exception as e:
        return f"Error grepping: {e}"


def extract_command(text):
    for line in text.split('\n'):
        line = line.strip()
        m = re.match(r'(READ|GREP|DONE)\s+(.+)', line, re.IGNORECASE)
        if m:
            return m.group(1).upper(), m.group(2).strip()
    return None, None


def run_trial(repo_path, task, gt_file, condition, trial):
    repo_name = Path(repo_path).name
    files = get_file_listing(repo_path)

    gt_basename = gt_file.rsplit("/", 1)[-1]
    gt_in_listing = any(gt_file in f for f in files) or any(
        f.endswith(gt_basename) for f in files)
    if not gt_in_listing:
        return {"condition": condition, "trial": trial, "repo": repo_name,
                "gt_file": gt_file, "error": f"GT '{gt_basename}' not in listing"}

    sys_prompt = SYSTEM_PROMPT.format(task=task, max_read_lines=MAX_READ_LINES, max_turns=MAX_TURNS)
    listing = "\n".join(files)

    user_msg = f"Source files in {repo_name}:\n\n{listing}\n\nWhich file do you want to read first?"

    if condition != "baseline":
        o = run_vocab(repo_path, task, condition)
        if o:
            user_msg = f"Vocab analysis:\n{o}\n\n---\n\n" + user_msg

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ]

    total_tokens = 0
    files_read = set()
    turn = 0
    done_path = None
    done_line = ""
    turn_times = []
    errors = []

    while turn < MAX_TURNS:
        t0 = time.time()
        result = deepseek_call(messages, TEMPERATURE)
        elapsed = time.time() - t0

        if "error" in result:
            errors.append(result["error"])
            return {"condition": condition, "trial": trial, "repo": repo_name,
                    "gt_file": gt_file, "error": result["error"]}

        usage = result.get("usage", {})
        turn_tokens = usage.get("total_tokens", 0) or (
            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
        total_tokens += turn_tokens

        resp = result["choices"][0]["message"]["content"]
        cmd, arg = extract_command(resp)

        if cmd == "DONE":
            done_path = arg
            done_line = resp
            messages.append({"role": "assistant", "content": resp})
            turn += 1
            turn_times.append(elapsed)
            break

        elif cmd == "READ":
            files_read.add(arg)
            content = read_file_content(repo_path, arg)
            messages.append({"role": "assistant", "content": resp})
            messages.append({"role": "user", "content": content + "\n\nNext command?"})

        elif cmd == "GREP":
            matches = grep_content(repo_path, arg)
            messages.append({"role": "assistant", "content": resp})
            messages.append({"role": "user", "content": matches + "\n\nNext command?"})

        else:
            messages.append({"role": "assistant", "content": resp})
            messages.append({
                "role": "user",
                "content": "Invalid format. Use READ, GREP, or DONE on its own line."})

        turn += 1
        turn_times.append(elapsed)

    read_edit_file = any(gt_basename in rf.replace("\\", "/") for rf in files_read)
    correct = False
    if done_path:
        done_clean = done_path.strip().rstrip(".")
        gt_clean = gt_basename
        correct = (gt_clean in done_clean or done_clean.endswith(gt_clean) or
                   any(gt_clean in d.strip() for d in done_clean.split("/")))

    return {
        "condition": condition,
        "trial": trial,
        "repo": repo_name,
        "gt_file": gt_file,
        "done_path": done_path,
        "correct": correct,
        "read_edit_file": read_edit_file,
        "files_read": sorted(files_read),
        "turns_used": turn,
        "total_tokens": total_tokens,
        "errors": errors,
    }


def main():
    results = []
    conditions = ["baseline", "summary", "checklist"]

    for repo_path, (gt_file, task) in VERIFY_GT_FILES.items():
        repo_name = Path(repo_path).name
        print(f"\n{'='*60}")
        print(f"REPO: {repo_name}")
        print(f"  Task: {task}")
        print(f"  GT file: {gt_file}")
        print(f"{'='*60}")

        for cond in conditions:
            for t in range(1, TRIALS + 1):
                print(f"\n  [{cond} trial {t}/{TRIALS}] ...", end=" ", flush=True)
                try:
                    result = run_trial(repo_path, task, gt_file, cond, t)
                    results.append(result)
                    if "error" in result:
                        print(f"ERROR: {result['error'][:80]}")
                    else:
                        status = "CORRECT" if result["correct"] else "WRONG"
                        print(f"{status} | turns={result['turns_used']} "
                              f"tokens={result['total_tokens']} "
                              f"reads={len(result['files_read'])}")
                except Exception as e:
                    print(f"CRASH: {e}")
                    results.append({"condition": cond, "trial": t, "repo": repo_name,
                                    "gt_file": gt_file, "error": f"crash: {e}"})

    OUTPUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\n\nResults written to {OUTPUT_PATH}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for cond in conditions:
        cond_results = [r for r in results if r.get("condition") == cond and "error" not in r]
        if not cond_results:
            print(f"\n{cond}: No successful runs")
            continue
        total = len(cond_results)
        correct = sum(1 for r in cond_results if r.get("correct"))
        avg_tokens = sum(r["total_tokens"] for r in cond_results) / total
        avg_turns = sum(r["turns_used"] for r in cond_results) / total
        avg_reads = sum(len(r.get("files_read", [])) for r in cond_results) / total
        read_gt = sum(1 for r in cond_results if r.get("read_edit_file"))
        print(f"\n{cond} ({total} runs):")
        print(f"  Accuracy:          {correct}/{total} ({correct/total*100:.0f}%)")
        print(f"  Read GT file:      {read_gt}/{total} ({read_gt/total*100:.0f}%)")
        print(f"  Avg tokens:        {avg_tokens:.0f}")
        print(f"  Avg turns:         {avg_turns:.1f}")
        print(f"  Avg files read:    {avg_reads:.1f}")

    errors = [r for r in results if "error" in r]
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  [{e['repo']} {e['condition']} t{e['trial']}]: {e['error'][:100]}")


if __name__ == "__main__":
    main()
