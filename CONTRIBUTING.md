# Contributing

Thanks for considering contributing to quale!

## Development setup

```bash
git clone https://github.com/Reliary/quale
cd quale
pip install -e ".[dev]"
```

## Merge strategy

Master is branch-protected. All changes go through feature branches + PRs.

### Branch naming

| Prefix | Purpose | Example |
|--------|---------|---------|
| `fix/` | Bug fixes | `fix/crash-on-empty-repo` |
| `feature/` | New features | `feature/mcp-server` |
| `docs/` | Documentation | `docs/readme-polish` |
| `chore/` | CI, config, tooling | `chore/update-deps` |

### Workflow

1. Branch off `master`: `git checkout -b fix/my-bug`
2. Make changes, commit with descriptive messages
3. Push: `git push -u origin fix/my-bug`
4. Open a PR against `master` via `gh pr create` or GitHub UI
5. CI checks (`test`, `guardrails`, `lint`, `security`) must pass
6. Merge via **squash** — one clean commit per PR

### Updating snapshots

If your change intentionally alters output, update golden files before merging:

```bash
UPDATE_SNAPSHOTS=1 python -m pytest tests/test_snapshots.py -v
git add tests/snapshots/
```

### Stale branches

After merging, clean up:

```bash
git branch -d fix/my-bug
git push origin --delete fix/my-bug
```

## Running tests

```bash
# Full suite
python -m pytest tests/ -v

# By layer
python -m pytest tests/test_cli_smoke.py -v         # Smoke (all commands exit 0)
python -m pytest tests/test_output_contracts.py -v    # Output quality contracts
python -m pytest tests/test_commands.py -v            # CLI integration
python -m pytest tests/test_reports.py -v             # Unit tests
python -m pytest tests/test_snapshots.py -v            # Snapshot regression
python -m pytest tests/test_state.py -v                # State transition
python -m pytest tests/test_structure.py -v            # Structural guardrails

# Update snapshots when output intentionally changes
UPDATE_SNAPSHOTS=1 python -m pytest tests/test_snapshots.py -v
```

## CI gate matrix

| Job | Files | Required for merge | What it catches |
|-----|-------|--------------------|-----------------|
| `test` | Core + install + reports | ✓ | Regression bugs |
| `guardrails` | Smoke, contracts, snapshots, state, structure, dogfood | ✓ | Crashes, UX regressions, drift |
| `lint` | `ruff check quale/` | ✓ | Code style violations |
| `security` | bandit, semgrep, pip-audit, mypy | ✓ | Vulnerabilities, type errors |

## Code style

- Run `ruff check quale/` before committing
- Run `mypy quale/ --ignore-missing-imports` for type checking
- Run `codespell` for typos
- All tests must pass before merging

## Reporting issues

Use the issue templates: bug reports, feature requests, or command-specific
issues. Include the command you ran, the output you expected, and what
actually happened.

## Questions?

Open a [discussion](https://github.com/Reliary/quale/discussions).
