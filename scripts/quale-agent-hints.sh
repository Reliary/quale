#!/usr/bin/env bash
set -euo pipefail

# quale-agent-hints.sh — read cached quale JSON, emit agent-prompt hints
# Usage: quale-agent-hints.sh <repo-path>
# Output: plain text hints (stdout) suitable for system prompt injection

REPO="${1:-.}"
CACHE_FILE="${REPO}/.reliary/quale/bootstrap.json"
TASK_FILE="${REPO}/.reliary/quale/bootstrap_summary.txt"

if [ ! -f "$CACHE_FILE" ]; then
  exit 0
fi

python3 -c "
import json, sys

with open('$CACHE_FILE') as f:
    d = json.load(f)

lines = []

total = d.get('total_code_files', 0)
if total:
    lines.append(f'Repository: {total} code files.')

reads = d.get('recommended_next_reads', [])
if reads:
    top = [r['file'] for r in reads[:3]]
    lines.append(f'Key files: {\", \".join(top)}.')

modules = d.get('module_boundaries', [])
if modules:
    labels = [m.get('label', '')[:30] for m in modules[:3] if m.get('label')]
    if labels:
        lines.append(f'Module groups: {\", \".join(labels)}.')

related = d.get('related_files_for_task', [])
if related:
    top_edit = related[0]['file']
    lines.append(f'Task-adjacent: edit {top_edit}.')
    hints = [r['file'] for r in related[1:4]]
    if hints:
        lines.append(f'Related: {\", \".join(hints)}.')

verified = d.get('verified_files', [])
unverified = d.get('unverified_files', [])
if verified and unverified:
    lines.append(f'Verified {len(verified)}/{len(verified)+len(unverified)} task concept matches.')

notes = d.get('agent_notes', [])
for n in notes[:2]:
    lines.append(n)

if lines:
    print('[Vocab]', ' '.join(lines))
"
