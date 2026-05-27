# Contributing

Thanks for considering contributing to quale!

## Development setup

```bash
git clone https://github.com/Reliary/quale
cd quale
pip install -e ".[dev]"
```

## Running tests

```bash
# Full suite
python -m pytest tests/ -v

# Specific test files
python -m pytest tests/test_cli_smoke.py -v
python -m pytest tests/test_output_contracts.py -v
```

Our CI runs these classes of tests:

| Layer | File | What it checks |
|-------|------|---------------|
| 1 | `test_cli_smoke.py` | Every command exits 0 and produces non-empty output |
| 2 | `test_output_contracts.py` | Output is useful, not just technical jargon |
| 5 | `test_schema_validation.py` | Agent JSON output matches schema |

## Code style

- Run `ruff check quale/` before committing
- Run `mypy quale/ --ignore-missing-imports` for type checking
- Run `codespell` for typos
- All tests must pass before merging

## Pull request process

1. Create a feature branch off `master`
2. Make your changes
3. Run tests: `python -m pytest tests/ -q`
4. Run lint: `ruff check quale/`
5. Update `CHANGELOG.md`
6. Open a PR with a clear description

## Reporting issues

Use the issue templates: bug reports, feature requests, or command-specific
issues. Include the command you ran, the output you expected, and what
actually happened.

## Questions?

Open a [discussion](https://github.com/Reliary/quale/discussions).
