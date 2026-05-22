"""Git integration — concept tracking across history."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class GitRef:
    sha: str
    message: str
    author: str
    timestamp: str


def _git(*args: str, cwd: str) -> str:
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=cwd,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_repo(path: str) -> bool:
    try:
        _git("rev-parse", "--git-dir", cwd=path)
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def list_files(path: str, ref: str | None = None) -> list[str]:
    """List tracked files. If ref is None, list working tree."""
    if ref:
        out = _git("ls-tree", "-r", "--name-only", ref, cwd=path)
    else:
        out = _git("ls-files", cwd=path)
    return [f for f in out.splitlines() if f.strip()]


def read_file_at_ref(path: str, filepath: str, ref: str | None = None) -> str | None:
    """Read file content at ref. If ref is None, read from disk."""
    full = os.path.join(path, filepath)
    if ref is None:
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return None
    try:
        # Use raw binary mode to handle non-UTF8 files
        result = subprocess.run(
            ["git", "show", f"{ref}:{filepath}"],
            capture_output=True, cwd=path, timeout=10,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, RuntimeError):
        return None


def diff_refs(path: str, ref_a: str, ref_b: str) -> list[str]:
    """List files changed between ref_a and ref_b (code files only)."""
    out = _git("diff", "--name-only", ref_a, ref_b, cwd=path)
    _skip_exts = frozenset({".pyc", ".pyo"})
    _skip_dirs = frozenset({"__pycache__", ".git", "node_modules"})
    files = []
    for f in out.splitlines():
        f = f.strip()
        if not f:
            continue
        parts = f.split("/")
        if any(p.endswith(".egg-info") for p in parts):
            continue
        base = parts[-1]
        if any(base.endswith(e) for e in _skip_exts):
            continue
        if any(d in parts for d in _skip_dirs):
            continue
        files.append(f)
    return files


def ref_log(path: str, count: int = 100) -> list[GitRef]:
    """Return recent commit log."""
    out = _git("log", f"--max-count={count}",
               "--format=%H|||%s|||%an|||%ai", cwd=path)
    refs = []
    for line in out.splitlines():
        parts = line.split("|||", 3)
        if len(parts) == 4:
            refs.append(GitRef(sha=parts[0], message=parts[1], author=parts[2], timestamp=parts[3]))
    return refs


def weekly_commits(path: str, weeks: int = 12) -> list[dict]:
    """Group commits by week for timeline analysis."""
    import datetime
    refs = ref_log(path, count=500)
    if not refs:
        return []
    weeks_map: dict[str, list[str]] = {}
    for ref in refs:
        try:
            dt = datetime.datetime.strptime(ref.timestamp[:10], "%Y-%m-%d")
            week_key = dt.strftime("%Y-W%W")
            if week_key not in weeks_map:
                weeks_map[week_key] = []
            weeks_map[week_key].append(ref.sha)
        except ValueError:
            continue
    result = []
    sorted_weeks = sorted(weeks_map.keys())[-weeks:]
    for wk in sorted_weeks:
        result.append({"week": wk, "commit_count": len(weeks_map[wk]), "shas": weeks_map[wk]})
    return result
