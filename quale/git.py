"""Git integration — concept tracking across history."""

from __future__ import annotations

import subprocess
import os
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


def _git_bytes(*args: str, cwd: str) -> bytes:
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, cwd=cwd,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def _split_nul(output: bytes) -> list[bytes]:
    return [item for item in output.split(b"\0") if item]


def is_repo(path: str) -> bool:
    try:
        _git("rev-parse", "--git-dir", cwd=path)
        return _git("rev-parse", "--is-inside-work-tree", cwd=path) == "true"
    except (RuntimeError, FileNotFoundError):
        return False


def has_commits(path: str) -> bool:
    try:
        _git("rev-parse", "--verify", "--quiet", "HEAD^{commit}", cwd=path)
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def ref_exists(path: str, ref: str) -> bool:
    try:
        _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=path)
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def list_files(path: str, ref: str | None = None) -> list[str]:
    """List tracked files. If ref is None, list working tree."""
    if ref:
        try:
            out = _git_bytes("ls-tree", "-rz", ref, cwd=path)
        except RuntimeError:
            return []
        files = []
        for entry in _split_nul(out):
            if b"\t" not in entry:
                continue
            meta, f = entry.split(b"\t", 1)
            mode = meta.split()[0].decode("ascii", errors="ignore") if meta else ""
            if mode in {"120000", "160000"}:
                continue
            if f:
                files.append(_decode_path(f))
        return files
    else:
        try:
            out = _git_bytes("ls-files", "-z", cwd=path)
        except RuntimeError:
            return []
        try:
            untracked = _git_bytes("ls-files", "-z", "--others", "--exclude-standard", cwd=path)
            if untracked:
                out = out + b"\0" + untracked
        except RuntimeError:
            pass
    files = []
    for f in _split_nul(out):
        if not f:
            continue
        f = _decode_path(f)
        full = os.path.join(path, f)
        if ref is None and (os.path.islink(full) or not os.path.isfile(full)):
            continue
        files.append(f)
    return files


def read_file_at_ref(path: str, filepath: str, ref: str | None = None, check_mode: bool = True) -> str | None:
    """Read file content at ref. If ref is None, read from disk."""
    full = os.path.join(path, filepath)
    if ref is None:
        try:
            if os.path.islink(full):
                return None
            if not os.path.isfile(full):
                return None
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            return None
    try:
        if check_mode:
            try:
                tree_out = _git_bytes("ls-tree", "-z", ref, "--", filepath, cwd=path)
                first_entry = _split_nul(tree_out)[0] if tree_out else b""
                mode = first_entry.split()[0].decode("ascii", errors="ignore") if first_entry else ""
                if mode in {"120000", "160000"}:
                    return None
            except RuntimeError:
                return None
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
    _skip_exts = frozenset({".pyc", ".pyo"})
    _skip_parts = frozenset({
        "__pycache__", ".git", "node_modules", "vendor", "dist", "build",
        "target", "out", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".parcel-cache", ".turbo", ".cache", "coverage",
    })
    files = []
    try:
        out = _git_bytes("diff", "--name-only", "-z", ref_a, ref_b, cwd=path)
    except RuntimeError:
        return []
    for f in _split_nul(out):
        if not f:
            continue
        f = _decode_path(f)
        parts = f.replace("\\", "/").split("/")
        if any(p.endswith(".egg-info") for p in parts):
            continue
        if any(d in parts for d in _skip_parts):
            continue
        base = parts[-1]
        if any(base.endswith(e) for e in _skip_exts):
            continue
        files.append(f)
    return files


def diff_worktree(path: str, ref: str) -> list[str]:
    """List files changed between a ref and the current working tree/HEAD."""
    _skip_exts = frozenset({".pyc", ".pyo"})
    _skip_parts = frozenset({
        "__pycache__", ".git", "node_modules", "vendor", "dist", "build",
        "target", "out", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".parcel-cache", ".turbo", ".cache", "coverage",
    })
    out = _git_bytes("diff", "--name-only", "-z", ref, cwd=path)
    files = []
    for f in _split_nul(out):
        if not f:
            continue
        f = _decode_path(f)
        parts = f.replace("\\", "/").split("/")
        if any(p.endswith(".egg-info") for p in parts):
            continue
        if any(d in parts for d in _skip_parts):
            continue
        base = parts[-1]
        if any(base.endswith(e) for e in _skip_exts):
            continue
        files.append(f)
    return files


def ref_log(path: str, count: int = 100) -> list[GitRef]:
    """Return recent commit log."""
    try:
        out = _git("log", f"--max-count={count}",
                   "--format=%H|||%s|||%an|||%ai", cwd=path)
    except RuntimeError:
        return []
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
