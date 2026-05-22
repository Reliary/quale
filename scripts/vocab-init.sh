#!/usr/bin/env bash
set -euo pipefail

# vocab-init.sh — run vocab analysis and cache for Reliary adapter
# Usage: vocab-init.sh <repo-path> [task]
# Output: <repo-path>/.reliary/vocab/bootstrap.json + .reliary/vocab/bootstrap_summary.txt

REPO="${1:-.}"
TASK="${2:-}"
CACHE_DIR="${REPO}/.reliary/vocab"

if ! command -v python3 &>/dev/null; then
  echo "error: python3 required" >&2
  exit 1
fi

mkdir -p "$CACHE_DIR"

# agent-bootstrap — working-tree scan
if [ -n "$TASK" ]; then
  python3 -m vocab.cli agent-bootstrap "$REPO" --task "$TASK" --format json \
    > "$CACHE_DIR/bootstrap.json" 2>/dev/null || true
  python3 -m vocab.cli agent-bootstrap "$REPO" --task "$TASK" --summary \
    2>/dev/null > "$CACHE_DIR/bootstrap_summary.txt" || true
else
  python3 -m vocab.cli agent-bootstrap "$REPO" --format json \
    > "$CACHE_DIR/bootstrap.json" 2>/dev/null || true
  python3 -m vocab.cli agent-bootstrap "$REPO" --summary \
    2>/dev/null > "$CACHE_DIR/bootstrap_summary.txt" || true
fi

# Verify cache was written
if [ -s "$CACHE_DIR/bootstrap.json" ]; then
  echo "cached to $CACHE_DIR/"
else
  echo "warning: vocab analysis produced no output" >&2
  rm -f "$CACHE_DIR/bootstrap.json"
  exit 0
fi
