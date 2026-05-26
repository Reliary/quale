# quale — structural codebase analysis

Reads code as text, no ASTs, no config. Answers structural questions about any codebase.

Useless on day zero. After ~1 commit, signal builds.

## What it does

Every model we tested guesses the wrong test file on a plain prompt. With quale, every
model picks the right one on the first try. 900+ trials, 12 repos, 7 model families.

The blind spot is structural, not semantic: no model knows your directory layout because
that's not in training data.

## Quickstart

```bash
quale edit-context --files src/spool.ts --task "change upload" --format tool
quale verify-packet --files src/spool.ts --task "change upload" --format tool
quale guard --task "change upload"
```

## For an agent

Put this in AGENTS.md or an instruction file:

```markdown
Before editing, run:
quale edit-context --files $FILE --task "$TASK" --format tool
```

The tool emits structured JSON. The agent reads it, picks the right test file,
and stays in scope. Measured: 75% verify hit, zero sprawl (vs 10-20% verify,
0.4-0.65 sprawl without).

## For a developer

```bash
quale edit-context --files src/spool.ts --task "..."
quale edit-context --diff HEAD~1
quale ci-report origin/main HEAD --summary
quale inspect .
```

## For CI

```bash
quale ci-report origin/main HEAD --fail-on-blast-tier high
quale ci-report origin/main HEAD --fail-on-mirror-gap 0.70
```

Exit codes: 0 = pass, 1 = error, 2 = blast gate, 3 = stable anchor gate.

## Cross-model verification

| Model | Baseline | With quale |
|-------|----------|------------|
| Qwen 235B | wrong path | correct |
| Gemma 4 31B | wrong dir | correct |
| Nemotron 30B | wrong + 277 tok | right + 112 tok |
| Mistral 24B | wrong dir | correct |
| Claude Opus 4 | src/spool.test.ts | tests/spool.test.ts |
| Gemma 4B (local CPU) | blank JSON | correct |

Every model makes the same `src/foo.test.ts` error. quale corrects it.

## What it is not

Linter, coverage tool, dead-code detector, security policy. All output is
report-only. Verification accuracy peaks around 80% -- when the candidate set
lacks the right test file, quale says so rather than guessing.

## How it works

Three primitives, same pipeline for every language:

1. Segmenter: split file content on delimiters
2. Vocabulary: collect unique phrases per file
3. Index: measure co-occurrence, overlap, and history

Deterministic. Same input, same output, every time.

## Further reading

- [docs/COMMANDS.md](docs/COMMANDS.md) -- full command reference
- [docs/EFFECT_HARNESS.md](docs/EFFECT_HARNESS.md) -- harness methodology and results
