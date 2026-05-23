#!/usr/bin/env python3
"""Mine vocab effect-harness failures into deterministic bug classes.

This script is intentionally offline and report-only. It turns measured rows
from scripts/evaluate_vocab_effect.py into small, actionable failure classes:
wrong verification file, source-file-as-test, edit sprawl, parse failures,
and conditions where vocab underperformed baseline.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("results", [])


def classify(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    if row.get("error"):
        labels.append("api_error")
    if row.get("parse_error") or row.get("parsed", {}).get("_parse_error"):
        labels.append("parse_error")

    suite = row.get("suite")
    if suite == "discovery":
        if not row.get("edit_hit"):
            labels.append("wrong_edit_file")
        if not row.get("edit_in_top3"):
            labels.append("edit_not_in_top3")
    elif suite == "preflight":
        verify = row.get("verify", []) or []
        if not row.get("verify_hit"):
            labels.append("wrong_verification")
        if any(_looks_like_source(path) for path in verify):
            labels.append("source_file_as_verification")
        if row.get("extra_edit_count", 0) > 0:
            labels.append("edit_sprawl")
        if row.get("extra_edit_count", 0) >= 2:
            labels.append("large_edit_sprawl")
    return labels or ["ok"]


def _looks_like_source(path: str) -> bool:
    lower = path.lower()
    if any(token in lower for token in ("test", "spec", "suite")):
        return False
    return any(lower.endswith(ext) for ext in (".py", ".ts", ".tsx", ".js", ".go", ".rs", ".c", ".h", ".erl", ".nim"))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    label_counts: Counter[str] = Counter()
    by_condition: dict[str, Counter[str]] = defaultdict(Counter)
    by_bucket: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        labels = classify(row)
        for label in labels:
            label_counts[label] += 1
            by_condition[row.get("condition", "unknown")][label] += 1
            by_bucket[row.get("bucket", "unknown")][label] += 1
            if label != "ok" and len(examples[label]) < 5:
                examples[label].append({
                    "repo": row.get("repo"),
                    "bucket": row.get("bucket"),
                    "condition": row.get("condition"),
                    "task": row.get("task"),
                    "verify": row.get("verify"),
                    "extra_edits": row.get("extra_edits"),
                    "edit": row.get("edit"),
                    "gt_edit_file": row.get("gt_edit_file"),
                })

    return {
        "schema_version": 1,
        "rows": len(rows),
        "label_counts": dict(label_counts.most_common()),
        "by_condition": {key: dict(counter.most_common()) for key, counter in sorted(by_condition.items())},
        "by_bucket": {key: dict(counter.most_common()) for key, counter in sorted(by_bucket.items())},
        "examples": examples,
        "recommendations": recommendations(label_counts, by_condition),
    }


def recommendations(labels: Counter[str], by_condition: dict[str, Counter[str]]) -> list[str]:
    recs: list[str] = []
    if labels.get("source_file_as_verification", 0):
        recs.append("Strengthen verification candidate filtering: down-rank source files unless explicitly test-like.")
    if labels.get("edit_sprawl", 0):
        recs.append("Keep edit_sprawl_guard in preflight_tool and test whether prompts obey question_extra_edits.")
    if labels.get("wrong_verification", 0):
        recs.append("Add or improve verification_deserts for repos/buckets with repeated wrong verification.")
    if labels.get("wrong_edit_file", 0):
        recs.append("Do not market task-only bootstrap as strong-model discovery aid; route vague tasks away from vocab.")
    if by_condition.get("bootstrap_summary", {}).get("wrong_edit_file", 0):
        recs.append("Bootstrap summary should remain human/weak-agent orientation, not automatic LLM prompt prefix.")
    return recs


def print_markdown(summary: dict[str, Any]) -> None:
    print("# vocab Effect Failure Analysis")
    print("")
    print(f"Rows analyzed: `{summary['rows']}`")
    print("")
    print("## Failure Classes")
    for label, count in summary["label_counts"].items():
        print(f"- `{label}`: {count}")
    print("")
    print("## Recommendations")
    for rec in summary.get("recommendations", []):
        print(f"- {rec}")
    print("")
    print("## Examples")
    for label, rows in summary.get("examples", {}).items():
        print(f"### {label}")
        for row in rows:
            print(f"- `{row['repo']}` `{row['condition']}`: {row.get('task', '')}")
        print("")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="effect harness JSON path")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    summary = summarize(load_rows(args.path))
    if args.format == "json":
        print(json.dumps(summary, indent=2))
    else:
        print_markdown(summary)


if __name__ == "__main__":
    main()
