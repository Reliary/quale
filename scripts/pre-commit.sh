#!/bin/bash
# Pre-commit hook for quale development.
# When cli.py or reports.py change, run the guardrail test suite.
set -euo pipefail

STAGED_CLI=$(git diff --cached --name-only | grep -c "quale/cli\.py" || true)
STAGED_REPORTS=$(git diff --cached --name-only | grep -c "quale/reports/.*\.py" || true)
STAGED_TESTS=$(git diff --cached --name-only | grep -c "tests/" || true)

if [ "$STAGED_CLI" -eq 0 ] && [ "$STAGED_REPORTS" -eq 0 ] && [ "$STAGED_TESTS" -eq 0 ]; then
    exit 0  # no output-affecting changes
fi

echo "=== Guardrail Layer 1: CLI Smoke Test ==="
python -m pytest tests/test_cli_smoke.py -x -q 2>&1 | tail -3

echo "=== Guardrail Layer 2: Output Contracts ==="
python -m pytest tests/test_output_contracts.py -x -q 2>&1 | tail -3

echo "=== Guardrail Layer 5: Schema Validation ==="
python -m pytest tests/test_schema_validation.py -x -q 2>&1 | tail -3

echo "=== Full test suite ==="
python -m pytest tests/ -x -q 2>&1 | tail -3

echo "All guardrails passed."
