# quale

A CLI that tells you what to edit, what to test, and what to leave alone. Works on any language, no ASTs, no config.

## Quickstart

```bash
pip install quale

cd my-project

# For Humans: Review your current changes
quale review

# For CI: Automated gates
quale ci check origin/main HEAD

# For LLM Agents: JSON-formatted safety packet
quale agent guard src/my_file.ts
```

That's it. One command tells you which files to read, which test to run, and what not to touch. 

## What it solves

Every LLM guesses the wrong test file path on a plain prompt. They all guess `src/foo.test.ts` when the test is in `tests/foo.test.ts`. This is a directory layout problem, not a model quality problem.

quale reads your repo's structure and gives the model what it's missing. 900+ trials across 12 repos and 7 model families: the wrong-path mistake is universal, and quale fixes it every time.

## Persona-Driven Commands

Quale is organized into namespaces tailored for how you work:

### 🧑‍💻 For Human Developers (Top-Level)
| Command | What it does |
|---------|-------------|
| `quale review` | Single human review summary: blast radius, test gaps, hub risk, clones |
| `quale onboard` | 3-step onboarding plan (landmarks, modules, safe directories) |
| `quale refactor-cost path/to/file` | Estimate refactoring effort (blast + escape + clones + hub) |
| `quale explore .` | Onboarding map: best files to read first |

### 🤖 For LLM Agents (`quale agent`)
Agents shouldn't waste tokens memorizing flags. Commands in the `agent` namespace inherently return optimized JSON/IR output.
| Command | What it does |
|---------|-------------|
| `quale agent edit src/file.ts` | File-scoped edit context and risk card in JSON |
| `quale agent guard src/file.ts` | Combined safety packet: guide + hub-risk + complexity |
| `quale agent orient` | Token-optimized structural repo map |

### 🚦 For CI Maintainers (`quale ci`)
| Command | What it does |
|---------|-------------|
| `quale ci init` | Generates a ready-to-use GitHub Actions YAML |
| `quale ci check base head` | Runs structural CI gates (`--fail-on-hub-risk`, `--fail-on-mirror-gap`, etc) |
| `quale ci comment base head`| Posts the PR structural report as a GitHub comment |
| `quale ci trend` | Tracks CI metric trends over time |

### 🔬 For Advanced Analysis (`quale core`)
Over 30 advanced mathematical and structural primitives (e.g., `spectral-gap`, `heisenberg`, `capillary`) are tucked away in the `core` namespace. See `quale core --help` for the full list.

## How it works

Reads code as text. Splits on delimiters, counts phrase frequency per file, measures co-occurrence across files. Same pipeline for every language. Deterministic: same input, same output.

## Limits

- Useless on a new repo (no structure to measure)
- Not a linter, coverage tool, or security scanner
- Verification peaks around 80%. When the candidate set lacks the right test, quale says so

## Further reading

- [docs/COMMANDS.md](docs/COMMANDS.md) (full reference)
- [docs/CI_INTEGRATION.md](docs/CI_INTEGRATION.md) (CI setup)
- [docs/EFFECT_HARNESS.md](docs/EFFECT_HARNESS.md) (methodology and results)
