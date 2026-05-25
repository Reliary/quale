#!/usr/bin/env python3
"""DOA test: phi-4 builds `vocab tree` with and without vocab guidance.

Task: Add `vocab tree` — prints directory tree with file counts.

   src/
   ├── agent/       (258 files)
   └── server/      (998 files)

Two conditions:
  BASELINE: task + repo context only
  VOCAB:    task + repo context + `edit-context --format tool` JSON

Measured:
  - Correct files chosen (0-3)
  - Syntax validity
  - `vocab tree` runs
  - Token cost
"""

import json, os, subprocess, sys, urllib.request, re, time

DEEPINFRA = "https://api.deepinfra.com/v1/openai/chat/completions"
KEY = "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
VOCAB_ROOT = "/home/user/src/vocab"
OUT_DIR = os.path.join(VOCAB_ROOT, "tmp")
os.makedirs(OUT_DIR, exist_ok=True)

MODEL = "microsoft/phi-4"

# ── Context for both conditions ──────────────────────────────

REPO_CONTEXT = """
vocab/ directory structure:
  vocab/
    __init__.py
    __main__.py
    analyze.py
    bootstrap.py
    cli.py          (4121 lines — all CLI commands)
    compare.py
    concepts.py
    config.py
    fold.py
    git.py
    index.py
    reports.py      (7632 lines — all report functions)
    scanner.py
    segmenter.py
    vocabulary.py
    wordlist.txt
    formats/
      __init__.py
      llm.py
      terminal.py

Key conventions:
  - CLI commands use @cli.command(name="xxx", rich_help_panel="PanelName")
  - Commands default to path=".", format="compact"
  - They call: from vocab.reports import function_name
  - They check: if not vgit.is_repo(p): typer.echo(...); raise typer.Exit(1)
  - Terminal output uses typer.echo()
  - Reports are dict-based, with "error" key on failure
  - scanner.py has scan_codebase() returning CodebaseAnalysis with file_vocabs
  - git.py has list_files(path) returning file list relative to repo root
  - git.py has tree(path) if needed

Existing simple command example (capillary_cmd at cli.py:1458):
@cli.command(name="capillary", rich_help_panel="Code Analysis")
def capillary_cmd(path=".", format="compact"):
    \"\"\"Files with the most inter-file vocabulary edges.\"\"\"
    from vocab.reports import capillary_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = capillary_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    for c in data.get("capillaries", [])[:3]:
        typer.echo(f'  {c["file"]} ({c["edges"]} edges)')

Existing report example (capillary_report in reports.py):
def capillary_report(path=".") -> dict:
    from vocab.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2000, max_seconds=30)
    except Exception as e:
        return {"error": str(e)}
    ...

The vocab tree command should:
1. Use vgit.list_files(path) to list all tracked files
2. Build a tree structure: for each file, split by / and build nested dict
3. Count files per directory
4. Print tree with indentation and file counts
5. Print total file count at the end
"""

VOCAB_GUIDANCE = """{
  "schema_version": 1,
  "risk": "moderate",
  "confidence": "high",
  "reason": "reverse blast reaches 5 ranked files; source/test mirror is thin (4%)",
  "changed_files": ["vocab/reports.py", "vocab/cli.py", "vocab/formats/terminal.py"],
  "read_first": ["vocab/reports.py", "vocab/cli.py", "vocab/formats/terminal.py"],
  "edit_sprawl_guard": {
    "mode": "report_only",
    "allow_changed_files": ["vocab/reports.py", "vocab/cli.py", "vocab/formats/terminal.py"],
    "stable_anchors_touched": [],
    "instruction": "Do not propose extra_edits unless the task explicitly requires them."
  },
  "verification_mc": {
    "question": "Which file would verify this change?",
    "candidates": ["tests/test_cli.py", "tests/test_commands.py", "tests/test_smoke.py"],
    "max_selections": 1
  }
}"""

TASK = """
Add a new command `vocab tree` to the vocab CLI tool.

The command prints the repo's directory tree with file counts per directory:

src/
├── agent/       (258 files)
├── server/      (998 files)
└── vocab/       (47 files)
Total: 1303 files in 3 directories

Implementation plan:
1. In vocab/reports.py: add a tree_report(path=".") function that:
   - Lists all tracked files via vgit.list_files(path)
   - Builds a nested directory tree from file paths
   - Counts files per directory
   - Returns {"tree": [...], "total_files": N, "total_dirs": N}

2. In vocab/cli.py: add a @cli.command for "tree" that:
   - Calls tree_report()
   - Prints indented tree with "├── " and "└── " connectors
   - Shows file count per dir in parentheses
   - Shows total at bottom

3. In vocab/formats/terminal.py: add a format_tree() function (optional)
"""


def call_llm(model, system_prompt, user_prompt, max_tokens=2000):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    body = json.dumps({"model": model, "messages": messages,
        "max_tokens": max_tokens, "temperature": 0.2}).encode()
    req = urllib.request.Request(DEEPINFRA, data=body,
        headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
        choice = d.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = d.get("usage", {}) or {}
        return msg.get("content", ""), usage
    except Exception as e:
        return f"ERROR: {e}", {}


def extract_code_blocks(text):
    """Extract Python code blocks from model response."""
    blocks = re.findall(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    return blocks


def extract_file_blocks(text):
    """Extract filename: code pairs from response."""
    # Pattern: filename.py or path/to/filename.py followed by code
    blocks = {}
    current_file = None
    current_code = []
    in_code = False

    for line in text.split('\n'):
        m = re.match(r'^```(?:python)?\s*$', line)
        if m:
            if in_code and current_file:
                blocks[current_file] = '\n'.join(current_code)
                current_code = []
            in_code = not in_code
            continue
        if in_code:
            current_code.append(line)
        else:
            fm = re.match(r'^#+?\s*`?(\S+\.py)`?\s*:?$', line)
            if fm:
                current_file = fm.group(1)

    if in_code and current_file:
        blocks[current_file] = '\n'.join(current_code)

    return blocks


def test_condition(label, system_prompt, user_prompt):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    content, usage = call_llm(MODEL, system_prompt, user_prompt, max_tokens=2000)
    pt = usage.get("prompt_tokens", "?")
    ct = usage.get("completion_tokens", "?")
    tt = usage.get("total_tokens", "?")
    elapsed = "?"  # don't have timing in this version

    print(f"  Tokens: prompt={pt}, completion={ct}, total={tt}")
    print(f"\n  Raw response ({len(content)} chars):")
    print(f"  {'─'*60}")
    # Print first 500 chars
    for line in content.split('\n')[:15]:
        print(f"    {line}")
    if len(content.split('\n')) > 15:
        print(f"    ... ({len(content.split('\n')) - 15} more lines)")

    # Extract code blocks
    code_blocks = extract_code_blocks(content)
    file_blocks = extract_file_blocks(content)

    print(f"\n  Code blocks found: {len(code_blocks)}")
    for i, cb in enumerate(code_blocks):
        print(f"    Block {i+1}: {len(cb.split(chr(10)))} lines")

    print(f"  File blocks found: {len(file_blocks)}")
    for fn, fc in file_blocks.items():
        print(f"    {fn}: {len(fc.split(chr(10)))} lines")

    return {
        "label": label,
        "content": content,
        "code_blocks": code_blocks,
        "file_blocks": file_blocks,
        "tokens": {"prompt": pt, "completion": ct, "total": tt},
    }


def apply_changes(file_blocks, label):
    """Write file blocks to disk and test."""
    results = []
    test_branch = f"doa-test-{label.lower().replace(' ', '-')}"

    for filepath, code in file_blocks.items():
        # Normalize path
        if filepath.startswith('vocab/'):
            full_path = os.path.join(VOCAB_ROOT, filepath)
        else:
            full_path = os.path.join(VOCAB_ROOT, 'vocab', filepath)

        results.append({
            "file": filepath,
            "full_path": full_path,
            "lines": len(code.split('\n')),
            "applied": False,
        })

        # Check if path exists
        if os.path.exists(full_path):
            # Would merge — for now just validate syntax
            try:
                compile(code, full_path, 'exec')
                results[-1]['syntax_valid'] = True
            except SyntaxError as e:
                results[-1]['syntax_valid'] = False
                results[-1]['syntax_error'] = str(e)

    return results


def main():
    system_prompt = (
        "You are a Python developer implementing a CLI feature. "
        "Output the COMPLETE modified files as Python code blocks. "
        "For each file change, write a comment header like:\n"
        "# vocab/reports.py\n"
        "```python\n"
        "... full file content or the specific function to add ...\n"
        "```\n"
        "IMPORTANT: Output valid Python that can be compiled."
    )

    # ── BASELINE ──
    base_user = (
        f"Task: {TASK}\n\n"
        f"Repo structure and conventions:\n{REPO_CONTEXT}\n\n"
        f"Output the new code for each file that needs to change. "
        f"Write complete functions, not placeholders."
    )
    baseline = test_condition("BASELINE (no vocab)", system_prompt, base_user)

    # ── VOCAB ──
    vocab_user = (
        f"Task: {TASK}\n\n"
        f"Repo structure and conventions:\n{REPO_CONTEXT}\n\n"
        f"Structural guidance from vocab:\n{VOCAB_GUIDANCE}\n\n"
        f"Key guidance:\n"
        f"- Changed files: vocab/reports.py, vocab/cli.py\n"
        f"- Do NOT edit other files\n"
        f"- Stable anchors: none touched\n\n"
        f"Output the new code for each file."
    )
    time.sleep(1)
    guided = test_condition("VOCAB (with guidance)", system_prompt, vocab_user)

    # ── Summary ──
    print(f"\n\n{'='*70}")
    print("  DOA TEST SUMMARY")
    print(f"{'='*70}")

    for cond in [baseline, guided]:
        print(f"\n  {'─'*60}")
        print(f"  {cond['label']}")
        print(f"  {'─'*60}")
        print(f"  Tokens: {cond['tokens']['total']}")
        print(f"  Code blocks: {len(cond['code_blocks'])}")
        print(f"  File blocks: {len(cond['file_blocks'])}")

        if cond['file_blocks']:
            for fn, fc in cond['file_blocks'].items():
                print(f"    {fn} ({len(fc.split(chr(10)))} lines)")
                try:
                    compile(fc, fn, 'exec')
                    print(f"      → Syntax: VALID")
                except SyntaxError as e:
                    print(f"      → Syntax: ERROR — {e}")
        elif cond['code_blocks']:
            for i, cb in enumerate(cond['code_blocks']):
                print(f"    Block {i+1} ({len(cb.split(chr(10)))} lines)")
                try:
                    compile(cb, f'block_{i}.py', 'exec')
                    print(f"      → Syntax: VALID")
                except SyntaxError as e:
                    # Try just the function
                    lines = cb.split('\n')
                    print(f"      → Syntax: ERROR — {e}")
                    print(f"      → First line: {lines[0] if lines else '(empty)'}")

    # Save full results
    out_path = os.path.join(OUT_DIR, "doa_test_results.json")
    with open(out_path, "w") as f:
        json.dump({
            "baseline": {
                "total_tokens": baseline['tokens']['total'],
                "content": baseline['content'],
                "code_blocks": baseline['code_blocks'],
                "file_blocks": baseline['file_blocks'],
            },
            "vocab": {
                "total_tokens": guided['tokens']['total'],
                "content": guided['content'],
                "code_blocks": guided['code_blocks'],
                "file_blocks": guided['file_blocks'],
            },
        }, f, indent=2)
    print(f"\n  Full results saved to {out_path}")

    # Final verdict
    print(f"\n  {'='*60}")
    print(f"  VERDICT")
    print(f"  {'='*60}")
    for cond in [baseline, guided]:
        valid_blocks = 0
        total_blocks = len(cond['file_blocks']) or len(cond['code_blocks'])
        if cond['file_blocks']:
            for fn, fc in cond['file_blocks'].items():
                try:
                    compile(fc, fn, 'exec')
                    valid_blocks += 1
                except:
                    pass
        elif cond['code_blocks']:
            for i, cb in enumerate(cond['code_blocks']):
                try:
                    compile(cb, f'block_{i}.py', 'exec')
                    valid_blocks += 1
                except:
                    pass

        has_report = any('reports' in k for k in (cond['file_blocks'].keys() if cond['file_blocks'] else [])) or any('tree' in cb.lower() for cb in cond.get('code_blocks', []))
        has_cli = any('cli' in k for k in (cond['file_blocks'].keys() if cond['file_blocks'] else []))
        
        print(f"\n  {cond['label']}: tok={cond['tokens']['total']} valid_blocks={valid_blocks}/{total_blocks} has_report_fn={has_report} has_cli_cmd={has_cli}")


if __name__ == "__main__":
    main()
