#!/usr/bin/env python3
"""Experiment v3: does vocab guidance reduce file-discovery cost?

Measures: how many files does deepseek-v4-flash need to READ before it
encounters the correct edit target?  No DONE commitment needed — just
observe the exploration path.

Tested on repos where the correct file's name doesn't contain task keywords,
so keyword-based search fails and vocab's identifier-based ranking should win.
"""

import json, os, re, subprocess, sys, urllib.request, urllib.error, time
from pathlib import Path

DEEPSEEK_KEY = json.loads(Path.home().joinpath('.local/share/opencode/auth.json').read_text())['deepseek']['key']
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
TRIALS = 2
MAX_TURNS = 20
MAX_READS = 10
FILE_LISTING_CAP = 80
TEMPERATURE = 0.2
VOCAB_DIR = Path.home() / "src/vocab"
OUTPUT_PATH = Path("/tmp/v3_experiment_results.json")

# Non-obvious pairs: filename doesn't contain task keywords
FIXTURES = [
    (Path.home() / "src/autopsylab-agent",
     "packages/core/src/synthetic.ts",
     "add safety constraint to slow dangerous test variant generation"),
    (Path.home() / "src/autopsylab",
     "internal/handlers/app.go",
     "add dashboard summary row for high-usage metrics"),
    (Path.home() / "src/llm-semantic-transport",
     "app/strategies/structural_prompt.py",
     "add SECONDARY classification that triggers rollback on HTTP 504"),
]

SYSTEM_PROMPT = """You are an agent exploring an unfamiliar codebase. Your task is to find the file that needs to be edited.

Task: {task}

Commands (one per response):
- READ <path> — See the full contents of a file (with line numbers)
- GREP <pattern> — Search for a regex pattern across the codebase

Read several files to understand the codebase. When you have read enough files, use the READ command to confirm which file is the correct edit target. There is no time pressure — explore naturally.

Rules:
- One command per response, on its own line
- Use relative paths from repo root"""


def run_vocab(repo_path, task, fmt):
    try:
        r = subprocess.run(
            [sys.executable, '-m', 'vocab.cli', 'agent-bootstrap', str(repo_path),
             '--task', task, '--format', fmt],
            cwd=str(VOCAB_DIR), env={**os.environ, 'PYTHONPATH': str(VOCAB_DIR)},
            capture_output=True, text=True, timeout=60)
        return r.stdout.strip() or None
    except Exception:
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
    exts = {'.ts', '.go', '.py', '.rs', '.js', '.tsx', '.jsx', '.rb', '.java',
            '.c', '.h', '.cpp', '.hpp', '.cs', '.swift', '.kt', '.zig', '.ex',
            '.exs', '.hs', '.jl', '.ml', '.mli', '.nix', '.clj', '.scala', '.r'}
    sources, tests = [], []
    skip = {'.git', 'node_modules', 'vendor', 'target', 'build', 'dist',
            '__pycache__', '.reliary', 'artifacts', 'third_party', '.vercel'}
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'ls-files', '-z'],
            capture_output=True, timeout=15)
        if result.returncode != 0:
            return [f"(error: git ls-files returned {result.returncode})"]
        all_files = result.stdout.decode('utf-8', errors='replace').strip('\0').split('\0')
        for rel in all_files:
            if not rel.strip() or Path(rel).suffix not in exts:
                continue
            if set(rel.split("/")) & skip:
                continue
            name = rel.rsplit("/", 1)[-1].lower()
            (tests if ('test' in name or 'spec' in name or rel.startswith("tests/")
                       or rel.startswith("spec/") or "test/" in rel)
             else sources).append(rel)
        listing = (sources + tests)[:FILE_LISTING_CAP]
        return listing if listing else [f"(no source files found in {Path(repo_path).name})"]
    except subprocess.TimeoutExpired:
        return ["(error: git ls-files timed out)"]
    except Exception as e:
        return [f"(error: {e})"]


def read_file_content(repo_path, file_path):
    try:
        full_path = Path(repo_path) / file_path
        if not full_path.exists() or not full_path.is_file():
            return f"Error: file not found at {file_path}"
        lines = full_path.read_text(encoding='utf-8', errors='replace').splitlines()
        out = [f"{i+1}: {l}" for i, l in enumerate(lines[:300])]
        return "\n".join(out)
    except Exception as e:
        return f"Error reading {file_path}: {e}"


def grep_content(repo_path, pattern):
    try:
        rg_cmd = subprocess.run(
            ['rg', '-n', '--no-heading', pattern, '--type-add',
             'code:*.ts,*.go,*.py,*.rs,*.js,*.tsx,*.jsx',
             '-t', 'code', str(repo_path),
             '-g', '!*.min.*', '-g', '!node_modules',
             '-g', '!vendor', '-g', '!target', '-g', '!build'],
            capture_output=True, text=True, timeout=30)
        matches = [l for l in rg_cmd.stdout.strip().split('\n') if l.strip()]
        out = [m.replace(str(repo_path) + '/', '')[:250]
               for m in matches[:20]]
        if not out:
            return f"No matches for '{pattern}'"
        result = "\n".join(out)
        if len(matches) > 20:
            result += f"\n(... {len(matches) - 20} more matches)"
        return result
    except FileNotFoundError:
        return "Error: rg not available"
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error grepping: {e}"


def extract_command(text):
    for line in text.split('\n'):
        line = line.strip()
        m = re.match(r'(READ|GREP)\s+(.+)', line, re.IGNORECASE)
        if m:
            return m.group(1).upper(), m.group(2).strip()
    return None, None


def run_trial(repo_path, task, gt_file, condition, trial):
    repo_name = Path(repo_path).name
    files = get_file_listing(repo_path)

    gt_basename = gt_file.rsplit("/", 1)[-1]
    gt_in_listing = any(gt_basename in f for f in files)
    if not gt_in_listing:
        return {"repo": repo_name, "condition": condition, "trial": trial,
                "gt_file": gt_file, "error": f"GT '{gt_basename}' not in listing"}

    sys_prompt = SYSTEM_PROMPT.format(task=task)

    # 80 source paths → fit in context window without drowning signal
    listing = "\n".join(files)
    user_msg = f"Code files in {repo_name}:\n\n{listing}\n\nWhich file do you want to read first?"

    if condition != "baseline":
        o = run_vocab(repo_path, task, condition)
        if o:
            user_msg = f"Vocab analysis:\n{o}\n\n---\n\n" + user_msg

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ]

    total_tokens = 0
    read_order = []
    read_positions = {}  # file → 0-based READ index
    turn = 0
    reads_issued = 0
    errors = []

    while turn < MAX_TURNS and reads_issued < MAX_READS:
        t0 = time.time()
        result = deepseek_call(messages, TEMPERATURE)
        elapsed = time.time() - t0

        if "error" in result:
            errors.append(result["error"])
            return {"repo": repo_name, "condition": condition, "trial": trial,
                    "gt_file": gt_file, "error": result["error"]}

        usage = result.get("usage", {})
        total_tokens += usage.get("total_tokens", 0) or (
            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))

        resp = result["choices"][0]["message"]["content"]
        cmd, arg = extract_command(resp)

        if cmd == "READ":
            if arg not in read_positions:
                read_positions[arg] = reads_issued
                read_order.append(arg)
            reads_issued += 1
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
                "content": "Use READ <path> or GREP <pattern> on its own line."})

        turn += 1

    gt_read = any(gt_basename in f.replace("\\", "/").split("/")[-1] for f in read_positions)
    gt_position = None
    for f, pos in sorted(read_positions.items(), key=lambda x: x[1]):
        if gt_basename in f.split("/")[-1]:
            gt_position = pos
            break

    return {
        "repo": repo_name,
        "condition": condition,
        "trial": trial,
        "gt_file": gt_file,
        "gt_read": gt_read,
        "gt_position": gt_position,
        "total_tokens": total_tokens,
        "total_reads": reads_issued,
        "turns_used": turn,
        "read_order": read_order[:5],
        "errors": errors,
    }


def main():
    results = []
    conditions = ["baseline", "summary", "checklist"]

    for repo_path, gt_file, task in FIXTURES:
        repo_name = Path(repo_path).name
        print(f"\n{'='*60}")
        print(f"REPO: {repo_name}")
        print(f"  Task: {task}")
        print(f"  GT:   {gt_file}")
        print(f"{'='*60}")

        for cond in conditions:
            for t in range(1, TRIALS + 1):
                print(f"\n  [{cond} trial {t}/{TRIALS}] ...", end=" ", flush=True)
                try:
                    r = run_trial(repo_path, task, gt_file, cond, t)
                    results.append(r)
                    if "error" in r:
                        print(f"ERROR: {r['error'][:80]}")
                    else:
                        gt_info = f"GT pos={r['gt_position']}" if r['gt_read'] else "GT never read"
                        reads = r.get('total_reads', 0)
                        toks = r.get('total_tokens', 0)
                        print(f"{reads} reads, {toks} tokens | {gt_info}")
                except Exception as e:
                    print(f"CRASH: {e}")
                    results.append({"repo": repo_name, "condition": cond, "trial": t,
                                    "gt_file": gt_file, "error": f"crash: {e}"})

    OUTPUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\n\nResults written to {OUTPUT_PATH}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for cond in conditions:
        rlist = [r for r in results if r.get("condition") == cond and "error" not in r]
        if not rlist:
            print(f"\n{cond}: No successful runs")
            continue
        n = len(rlist)
        gt_read_count = sum(1 for r in rlist if r.get("gt_read"))
        gt_within_3 = sum(1 for r in rlist if r.get("gt_position") is not None and r["gt_position"] < 3)
        positions = [r["gt_position"] for r in rlist if r["gt_position"] is not None]
        avg_pos = sum(positions) / len(positions) if positions else -1
        avg_tokens = sum(r["total_tokens"] for r in rlist) / n
        avg_reads = sum(r["total_reads"] for r in rlist) / n
        print(f"\n{cond} ({n} runs):")
        print(f"  Read GT file:       {gt_read_count}/{n} ({gt_read_count/n*100:.0f}%)")
        print(f"  GT within first 3:  {gt_within_3}/{n} ({gt_within_3/n*100:.0f}%)")
        print(f"  Avg GT position:    {avg_pos:.1f}" if positions else "  Avg GT position:    never")
        print(f"  Avg reads:          {avg_reads:.1f}")
        print(f"  Avg tokens:         {avg_tokens:.0f}")

    errors = [r for r in results if "error" in r]
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  [{e['repo']} {e['condition']} t{e['trial']}]: {e['error'][:100]}")


if __name__ == "__main__":
    main()
