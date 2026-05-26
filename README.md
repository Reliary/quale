# quale

A CLI that tells you what to edit, what to test, and what to leave alone. Works on any language, no ASTs, no config.

## Quickstart

```bash
pip install quale

cd my-project
quale guard --task "fix upload" --format tool
```

That's it. One command tells you which files to read, which test to run, and what not to touch. Output is JSON an agent can consume directly.

## What it solves

Every LLM guesses the wrong test file path on a plain prompt. They all guess `src/foo.test.ts` when the test is in `tests/foo.test.ts`. This is a directory layout problem, not a model quality problem.

quale reads your repo's structure and gives the model what it's missing. 900+ trials across 12 repos and 7 model families: the wrong-path mistake is universal, and quale fixes it every time.

## Commands

| Command | What it does |
|---------|-------------|
| `quale --agent-orient` | JSON manifest: conventions, workflow, gotchas (run after install) |
| `quale guard --task "..." --format tool` | Safety packet: what to read, what to test, what not to touch |
| `quale edit-context --files path.ts --task "..." --format tool` | Pre-edit scope: read first, verify with, avoid |
| `quale verify-packet --files path.ts --task "..." --format tool` | Test candidates only (priority, entangle, negative) |
| `quale inspect .` | Onboarding: key files, modules, churn |
| `quale ci-report origin/main HEAD --summary` | CI: blast radius, mirror gap, stable anchors |

For an agent:

1. Run `quale --agent-orient` after pip install — returns a JSON manifest with flag conventions, workflow ordering, format types, and gotchas. The tool teaches itself.
2. Then `quale help-agent "change upload behavior" --format tool` for task-specific recommendations.

Every agent-facing command (`edit-context`, `guard`, `contract`, `check-plan`, `verify-packet`) carries an `_agent_note` field in its `--format tool` JSON explaining the flag syntax for that command.

There is no setup or "init" step. The first time the command runs on a repo, quale automatically scans the codebase and caches the structural map (takes 1-3 seconds). Subsequent calls are instant.

For CI gates:

> `quale ci-report origin/main HEAD --fail-on-blast-tier high`

More commands: [docs/COMMANDS.md](docs/COMMANDS.md)

## How it works

Reads code as text. Splits on delimiters, counts phrase frequency per file, measures co-occurrence across files. Same pipeline for every language. Deterministic: same input, same output.

## Limits

- Useless on a new repo (no structure to measure)
- Not a linter, coverage tool, or security scanner
- Verification peaks around 80%. When the candidate set lacks the right test, quale says so

## Further reading

- [docs/COMMANDS.md](docs/COMMANDS.md) (full reference)
- [docs/EFFECT_HARNESS.md](docs/EFFECT_HARNESS.md) (methodology and results)
