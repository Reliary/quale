# quale — agent instructions

## Before editing any file

python3 -m quale edit-context --files <FILE> --task "<TASK>" --format tool

This emits a JSON contract: verification candidates, expansion risk, scope-creep guard, stable anchors touched. Pass `2>/dev/null` before piping to python3 -m json.tool to strip stderr banners.

## Safety check

python3 -m quale guard --file <FILE> --format tool

Combined hub-risk + complexity + criticality packet. Never blocks (always report-only).

## Quick repo orientation

python3 -m quale inspect --path <REPO> --format compact

## Get verification candidates (no edit context)

python3 -m quale verify-packet --file <FILE> --task "<TASK>" --format tool

## Flag conventions

| Convention | Commands |
|------------|----------|
| `--files <CSV>` | edit-context, verify-packet |
| `--file <FILE>` | guard, check-plan, check-diff, zk-proof, deflate, heisenberg, contract |
| `--path <DIR>` | inspect, repo-map, hub-risk, extinct-exports, coupling-chain, anomalies, entropy, drift-check, forecast, isolate, fold, origins |
| Positional `<TEXT>` | search, help-agent |
| `--ref <REF>` | lifecycle, timeline, stable, provenance |

## Important behaviors

- **search strips punctuation** — search bare identifiers (`SpoolManager`), not `SpoolManager(` or `SpoolManager.something`. For literal string search use grep.
- **`--format tool`** emits a structured JSON contract for LLM consumption (keys: verification_mc, risk, expansion_risk, scope_creep_guard, stable_anchors_touched).
- **`--format json`** emits raw data export (different schema, for storage/analysis).
- **`--format compact`** is terminal-friendly (default for most commands).
- **Multiple files** to `--files` use comma separation: `--files "file1.ts,file2.ts"` (not repeated `--files` flags).
- **Most JSON output** goes to stdout; typer banners go to stderr. For clean piping: `command 2>/dev/null | python3 -c "import sys,json; ..."`.
- **repo-map** (formerly crystallography) rejects positional arg — use `--path .`.
- **hub-risk, extinct-exports, coupling-chain** are repo-level only (no `--file` filter).
- **Commands with `--task`**: edit-context, verify-packet, guard, agent-bootstrap, check-plan, help-agent.
