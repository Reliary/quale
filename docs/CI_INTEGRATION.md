# CI Integration Guide

## GitHub Actions

```yaml
name: quale structural review
on: [pull_request]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Install quale
        run: pip install quale
      - name: Human review
        run: quale review
      - name: CI gates
        run: |
          quale ci check origin/${{ github.base_ref }} HEAD \
            --fail-on-blast-tier high \
            --fail-on-mirror-gap 0.50 \
            --fail-on-stable-touched \
            --fail-on-hub-risk \
            --fail-on-clone \
            --fail-on-new-identifiers 30
      - name: PR report
        run: |
          quale ci comment origin/${{ github.base_ref }} HEAD
```

## Exit Codes

| Code | Gate | Flag |
|------|------|------|
| 0 | Pass | — |
| 1 | Error | — |
| 2 | Blast tier | `--fail-on-blast-tier <tier>` |
| 3 | Stable anchor | `--fail-on-stable-touched` |
| 4 | Mirror gap | `--fail-on-mirror-gap <ratio>` |
| 5 | Hub risk | `--fail-on-hub-risk` |
| 6 | Clone detected | `--fail-on-clone` |
| 7 | Identifier explosion | `--fail-on-new-identifiers <N>` |

## Quick Reference

| What | Command |
|------|---------|
| Pre-PR check | `quale review` |
| CI gate | `quale ci check origin/main HEAD --fail-on-blast-tier high` |
| Trend tracking | `quale ci trend` |
| PR comment | `quale ci comment origin/main HEAD` |
| Onboarding | `quale onboard` |
| Refactor estimate | `quale refactor-cost <file>` |

## Notes

- Structural scan takes ~1-3 seconds on first run; cached afterward.
- Add `.quale/` to your `.gitignore`.
- `ci-trend` reads `.quale/ci-history.jsonl` which accumulates across runs.
  This file is local-only and should not be committed.
