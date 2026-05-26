"""Fractional distillation — fold task-irrelevant code blocks.

Indentation-aware block folding preserves syntax while removing
structural noise. Blocks with low task-keyword overlap are replaced
with a fold annotation.
"""

from __future__ import annotations

import os
import re
from typing import Any

from quale.segmenter import segment

_STRUCTURAL_PATTERNS = re.compile(
    r'^\s*[}\]\)]?\s*$|'          # bare closing brackets
    r'(import|export|require|include|from|using|package)\b|'
    r'^\s*(public|private|protected|static|async|function|def|func|class|struct|enum|interface|type)\b|'
    r'^\s*(if|else|for|while|switch|case|try|catch|finally|return|throw)\s|'
    r'^\s*(end|end\s+\w+|when)\b|'  # Ruby/Crystal/YAML
    r'^\s*\}?\s*(else|catch|finally)\b'
)


def _indent_blocks(lines: list[str]) -> list[dict[str, Any]]:
    """Group lines into blocks split by blank lines and indent changes."""
    if not lines:
        return []
    blocks: list[dict[str, Any]] = []
    start = 0
    cur_indent = 0 if lines[0].strip() else -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if start < i:
                blk_lines = [l for l in lines[start:i] if l.strip()]
                if blk_lines:
                    blocks.append({
                        "start": start,
                        "end": i - 1,
                        "indent": len(lines[start]) - len(lines[start].lstrip()),
                        "text": "\n".join(lines[start:i]),
                    })
            start = i + 1
            cur_indent = -1
            continue
        indent = len(line) - len(stripped)
        if cur_indent < 0:
            cur_indent = indent
            start = i
        elif indent < cur_indent:
            blocks.append({
                "start": start,
                "end": i - 1,
                "indent": cur_indent,
                "text": "\n".join(lines[start:i]),
            })
            cur_indent = indent
            start = i

    if start < len(lines):
        remaining = [l for l in lines[start:] if l.strip()]
        if remaining:
            blocks.append({
                "start": start,
                "end": len(lines) - 1,
                "indent": len(lines[start]) - len(lines[start].lstrip()) if lines[start].strip() else 0,
                "text": "\n".join(lines[start:]),
            })
    return blocks


def _is_structural(line: str) -> bool:
    """Check if a line is structural (should never be folded)."""
    return bool(_STRUCTURAL_PATTERNS.search(line))


def _task_keywords(task: str) -> set[str]:
    """Extract keywords from task description."""
    kws: set[str] = set()
    for word in re.findall(r'\b[A-Z][a-zA-Z]{3,}\b', task):
        kws.add(word.lower())
    for word in task.split():
        wl = word.lower().strip(".,;:!?()[]{}\"'")
        if len(wl) >= 4 and wl.isalpha():
            kws.add(wl)
    return kws


def _score_block(block_text: str, task_keywords: set[str]) -> float:
    """Score a block by task-keyword overlap. Uses segment() from segmenter.py."""
    if not task_keywords:
        return 1.0
    result = segment(block_text)
    phrases_lower = set(p.lower() for p in result.phrases)
    overlap = 0
    for tk in task_keywords:
        for p in phrases_lower:
            if tk in p or p in tk:
                overlap += 1
                break
    return round(overlap / len(task_keywords), 3)


def fold_file(path: str = ".", file_path: str = "", task: str = "",
              threshold: float = 0.02) -> dict[str, Any]:
    """Read file, score blocks, fold low-scoring ones.
    
    Returns folded output with metadata. Never folds structural lines.
    """
    abs_path = os.path.abspath(os.path.join(path, file_path)) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(abs_path):
        return {"error": f"file not found: {abs_path}"}

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return {"error": f"read failed: {e}"}

    lines = text.split("\n")
    original_line_count = len(lines)

    if not task.strip():
        return {
            "file_path": file_path,
            "original_lines": original_line_count,
            "visible_lines": original_line_count,
            "compression_pct": 0.0,
            "folded_text": text,
            "folded_blocks": [],
        }

    kws = _task_keywords(task)
    if not kws:
        return {
            "file_path": file_path,
            "original_lines": original_line_count,
            "visible_lines": original_line_count,
            "compression_pct": 0.0,
            "folded_text": text,
            "folded_blocks": [],
        }

    blocks = _indent_blocks(lines)
    if not blocks:
        return {
            "file_path": file_path,
            "original_lines": original_line_count,
            "visible_lines": original_line_count,
            "compression_pct": 0.0,
            "folded_text": text,
            "folded_blocks": [],
        }

    folded_blocks: list[dict[str, Any]] = []
    kept_lines: list[str] = []

    for block in blocks:
        b_start = block["start"]
        b_end = block["end"]
        b_text = block["text"]
        block_lines = b_text.split("\n")

        # Never fold structural blocks (imports, function defs, class defs, return stmts)
        non_empty = [l for l in block_lines if l.strip()]
        if any(_is_structural(l) for l in non_empty):
            kept_lines.extend(lines[b_start:b_end + 1])
            continue

        score = _score_block(b_text, kws)

        if score < threshold and len(block_lines) >= 3:
            foline = f"// [Folded: {len(block_lines)} lines. Score: {score:.3f}]"
            kept_lines.append(foline)
            folded_blocks.append({
                "start": b_start,
                "end": b_end,
                "lines": len(block_lines),
                "score": score,
                "text": b_text,
            })
        else:
            kept_lines.extend(lines[b_start:b_end + 1])

    folded_text = "\n".join(kept_lines)
    visible = len(kept_lines)
    return {
        "file_path": file_path,
        "original_lines": original_line_count,
        "visible_lines": visible,
        "compression_pct": round((1 - visible / max(original_line_count, 1)) * 100, 1),
        "folded_text": folded_text,
        "folded_blocks": folded_blocks[:20],
        "total_folded_lines": sum(b["lines"] for b in folded_blocks),
        "task_keywords": sorted(kws),
    }
