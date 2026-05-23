#!/usr/bin/env python3
"""Blind agent-bootstrap test against available repos."""

import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(os.path.expanduser("~"))
ENV = dict(os.environ, PYTHONPATH="/home/user/src/vocab")

REPOS = {
    # Public corpus repos (in /tmp/corpus-sweep/)
    "cpython":     ("/tmp/corpus-sweep/cpython", "improve string formatting"),
    "django":      ("/tmp/corpus-sweep/django", "add new middleware"),
    "flask":       ("/tmp/corpus-sweep/flask", "add new route"),
    "gin":         ("/tmp/corpus-sweep/gin", "add new middleware"),
    "go":          ("/tmp/corpus-sweep/go", "optimise slice allocation"),
    "grafana":     ("/tmp/corpus-sweep/grafana", "add new panel"),
    "kubernetes":  ("/tmp/corpus-sweep/kubernetes", "add new pod condition"),
    "laravel":     ("/tmp/corpus-sweep/laravel", "add new middleware"),
    "linux":       ("/tmp/corpus-sweep/linux", "add new file system"),
    "mermaid":     ("/tmp/corpus-sweep/mermaid", "add new diagram type"),
    "neovim":      ("/tmp/corpus-sweep/neovim", "add new API event"),
    "nginx":       ("/tmp/corpus-sweep/nginx", "add new module"),
    "Nim":         ("/tmp/corpus-sweep/Nim", "add new stdlib module"),
    "otp":         ("/tmp/corpus-sweep/otp", "add new BIF function"),
    "pandas":      ("/tmp/corpus-sweep/pandas", "add new DataFrame method"),
    "php-src":     ("/tmp/corpus-sweep/php-src", "add new opcode"),
    "prometheus":  ("/tmp/corpus-sweep/prometheus", "add new metric type"),
    "redis":       ("/tmp/corpus-sweep/redis", "add new command"),
    "rust":        ("/tmp/corpus-sweep/rust", "improve borrow checker"),
    "serde":       ("/tmp/corpus-sweep/serde", "add new deserialize"),
    "svelte":      ("/tmp/corpus-sweep/svelte", "add new compiler pass"),
    # Agent repos
    "agent":       ("/home/user/src/autopsylab-agent", "add new typed evidence envelope"),
    "server":      ("/home/user/src/autopsylab", "implement fingerprint-to-recovery playbook lookup"),
    "compressor":  ("/home/user/src/llm-semantic-transport", "add new compression backend"),
    "vocab":       ("/home/user/src/vocab", "add new scan command"),
}

def is_git(path):
    try:
        subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, timeout=5)
        return True
    except:
        return False

repos = {k: v for k, v in REPOS.items() if os.path.isdir(v[0]) and is_git(v[0])}
print(f"Testing {len(repos)} repos...\n")

hits, misses = 0, 0
details = []

for name, (path, task) in sorted(repos.items()):
    try:
        ps = subprocess.run(
            [sys.executable, "-m", "vocab.cli", "agent-bootstrap", path, "--task", task, "--summary"],
            cwd=ROOT, env=ENV, text=True, capture_output=True, timeout=120,
        )
        lines = ps.stdout.strip().split("\n")

        pj = subprocess.run(
            [sys.executable, "-m", "vocab.cli", "agent-bootstrap", path, "--task", task, "--format", "json"],
            cwd=ROOT, env=ENV, text=True, capture_output=True, timeout=120,
        )
        data = json.loads(pj.stdout) if pj.returncode == 0 else {}

        read_file = likely_file = ""
        for line in lines:
            if line.startswith("    Read:") and not read_file:
                read_file = line.split("Read:")[-1].split("—")[0].strip()
            if "Likely edit:" in line:
                likely_file = line.split("Likely edit:")[-1].split("—")[0].strip()

        top_read = likely_file or read_file
        score = data.get("task_relevance_score", 0)
        bc_count = len(data.get("binding_concepts", []))
        keywords = [w for w in task.lower().split() if len(w) > 3]
        hit = any(k in top_read.lower() for k in keywords) if top_read else False

        if hit:
            hits += 1
            print(f"  ✓ {name:25s} {top_read} ({score:.0%} bc={bc_count})")
        else:
            misses += 1
            print(f"  ✗ {name:25s} top={top_read or '(none)'} score={score:.0%} bc={bc_count}")

        details.append((name, hit, ps.returncode, pj.returncode, score, bc_count, top_read))

    except Exception as e:
        print(f"  !! {name}: {e}")
        details.append((name, False, 1, 1, 0, 0, ""))

total = hits + misses
print(f"\n{'=' * 60}")
print(f"  HIT RATE: {hits}/{total} ({hits/total*100:.0f}%)")
print(f"{'=' * 60}")
for name, hit, rc1, rc2, score, bc, top in details:
    tag = "✓" if hit else ("✗" if hit is False else "?")
    print(f"  [{tag}] {name:25s} score={score:.0%} bc={bc:2d} top={top or '<none>'}")
