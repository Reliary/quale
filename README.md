# quale

[![PyPI version](https://img.shields.io/pypi/v/quale?color=blue)](https://pypi.org/project/quale/)
[![Python versions](https://img.shields.io/pypi/pyversions/quale)](https://pypi.org/project/quale/)
[![CI](https://github.com/Reliary/quale/actions/workflows/ci.yml/badge.svg)](https://github.com/Reliary/quale/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Reliary/quale)](LICENSE)

Structural codebase analysis — tells you what to read, what to edit, and what
to test. Works on any language. No parsers, no config.

```bash
pip install quale

cd my-project
quale review              # review your current changes
quale ci check main HEAD  # automated CI gates
quale agent guard path/to/file.ts  # safety packet for LLMs
```

The wrong-file-path mistake is universal across models. Quale fixes it by
reading your repo's structure and giving the agent what it's missing.

---

## Quickstart

```bash
pip install quale

cd my-project

quale review                     # PR review: blast radius, test gaps, risk
quale onboard                    # new-repo onboarding: landmarks, modules
quale refactor-cost src/main.ts  # refactoring effort estimate
quale agent orient               # repo map for LLM agents
quale ci check origin/main HEAD  # automated CI gates
quale --help                     # full command list
```

That's it. Three commands, thirty seconds, no configuration.

## Persona-driven commands

Commands are organized into namespaces for how you work:

| Persona | Namespace | Purpose |
|---------|-----------|---------|
| Human developer | top-level | `review`, `onboard`, `refactor-cost`, `inspect`, `explore` |
| LLM agent | `quale agent` | Token-optimized JSON: `orient`, `edit`, `guard` |
| CI pipeline | `quale ci` | Automated gates: `check`, `comment`, `trend`, `init` |
| Advanced | `quale core` | 60+ structural primitives |

### Human developer

| Command | What it does |
|---------|-------------|
| `quale review` | Per-file review: stable anchors, hub risk, test connections, action items |
| `quale onboard` | 3-step onboarding: languages, macro-modules, landmark files |
| `quale refactor-cost <file>` | Effort estimate: direct impact, transitive ripple, clones |
| `quale inspect .` | Codebase overview: module layout, tech stack, health |
| `quale explore .` | Best files to read first for a new contributor |

### LLM agent

| Command | What it returns |
|---------|----------------|
| `quale agent edit <file>` | Edit context + `verification_mc` multi-choice candidates (JSON) |
| `quale agent guard <file>` | Risk packet: guide, hub risk, complexity, stable anchors (JSON) |
| `quale agent orient` | Repo map: modules, landmarks, languages, workflow commands (JSON) |

### CI pipeline

| Command | What it does |
|---------|-------------|
| `quale ci init` | Generates a ready-to-use GitHub Actions YAML |
| `quale ci check <base> <head>` | Runs structural gates, exits 0-7 with bitmask |
| `quale ci comment <base> <head>` | Posts structural report as GitHub PR comment |
| `quale ci trend` | Tracks CI metric trends over time |

### Advanced primitives

See `quale core --help` for 60+ structural commands including `hub-risk`,
`spectral-gap`, `criticality`, `coupling-chain`, `diff-structural`, and more.

## What it solves

Every LLM guesses the wrong test file path. Given a source file at
`internal/handlers/review.go`, models consistently guess
`src/handlers/review.test.go` when the actual test is at
`tests/handlers/review_test.go`. This is a directory layout problem, not a
model quality problem.

Quale reads your repo's structure — co-occurrence, module boundaries, test
mirrors — and gives the model what it's missing. Verified across 900+ trials,
12 repos, and 7 model families.

## How it works

Reads code as text. Splits on delimiters, counts phrase frequency per file,
measures co-occurrence across files. Same pipeline for every language.
Deterministic: same input, same output.

No ASTs. No grammars. No parsers. No per-language config. No dependencies
beyond Python 3.10+ and two small packages (typer, typing-extensions).

## Limits

- Useless on a brand-new repo (no structure to measure)
- Not a linter, coverage tool, or security scanner
- Verification peaks around 80% — when the candidate set lacks the right test,
  quale says so
- Requires `git` history for diff-based commands

## Development

```bash
git clone https://github.com/Reliary/quale
cd quale

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Lint and type check
ruff check quale/
mypy quale/ --ignore-missing-imports
```

Contributions welcome. See [CONTRIBUTING](CONTRIBUTING.md).

## Further reading

- [docs/COMMANDS.md](docs/COMMANDS.md) — full command reference
- [docs/CI_INTEGRATION.md](docs/CI_INTEGRATION.md) — CI setup guide
- [docs/EFFECT_HARNESS.md](docs/EFFECT_HARNESS.md) — methodology and results
- [CHANGELOG.md](CHANGELOG.md) — release history

## License

MIT
