# quale — structural grounding for coding agents

quale reads code as text — no ASTs, no parsers, no per-language config — and answers
structural questions every agent and developer needs before changing anything.

**It's useless on day zero.** No files, no structure to measure. After ~1 commit
(`edit-context`), ~5 files (`repo-map`), ~3 commits (`lifecycle`), the signal catches up.

## The measured claim

Every model we tested — Qwen 235B, Gemma 31B, Nemotron 30B, Mistral 24B, Claude Opus 4,
Gemma 4B (local CPU), deepseek-v4-flash — guesses the wrong test file on a plain prompt.
With quale, every single one picks the right file on the first try. That held across
900+ harness trials, 12 repos, and 7 model families.

This isn't a model quality problem. It's a structural blind spot — no model knows where
you put your tests because that information is in your directory layout, not in training
data. Vocab fills that gap.

## What quale isn't

- **Not a linter.** It doesn't check for bugs, style, or correctness.
- **Not a coverage tool.** Test-file candidates are structural hints, not proof of coverage.
- **Not a dead-code detector.** Grammar-free orphan detection is noisy and language-dependent.
- **Not a security policy API.** All output is report-only — use your own judgment.
- **Not useful on a new repo.** First commit has nothing to measure.

## Quickstart

```bash
quale edit-context --files src/spool.ts --task "change upload" --format tool
quale verify-packet --files src/spool.ts --task "change upload" --format tool
quale guard --task "change upload"
```

## How it works

Three primitives, no ASTs:

1. **Segmenter** — splits file content on delimiter boundaries (whitespace, brackets, operators).
2. **Vocabulary** — collects all unique phrases per file into a phrase-file incidence matrix.
3. **Index** — measures co-occurrence, overlap, and historical presence from git history.

Every command is a deterministic function of these three primitives. Same input, same output.

## Persona workflows

### Agent: scope control (proven)

Before editing, run `edit-context` to learn which files to read, which test to update,
and what to leave alone. Measured effect (1,100 trials, 12 repos):

| Condition | Verify hit | Sprawl | Tokens |
|-----------|-----------|--------|--------|
| baseline | 10-20% | 0.40-0.65 | ~1,200 |
| `edit-context --format tool` | 75% | 0.0 | 1,658 |
| `verify_scope` (verification only) | 83% | 0.0 | 1,233 |

Wrap in AGENTS.md or instruction files so any tool-calling model calls quale:

```markdown
Before editing any file, run:
python3 -m quale edit-context --files <FILE> --task "<TASK>" --format tool
```

### Human: preflight and review

```bash
quale edit-context --files src/spool.ts --task "..."
quale edit-context --diff HEAD~1
quale ci-report origin/main HEAD --summary
```

### CI: structural drift gates

```bash
quale ci-report origin/main HEAD --fail-on-mirror-gap 0.70
quale ci-report origin/main HEAD --fail-on-blast-tier high
```

## Cross-model proof

6 models on 2 private repos, the same structural blind spot in every one:

| Model | Baseline | With quale |
|-------|----------|------------|
| Qwen 235B | guesses wrong path ✗ | correct ✓ |
| Gemma 4 31B | wrong directory ✗ | correct ✓ |
| Nemotron 30B | wrong + 277 tok ✗ | right + 112 tok ✓ |
| Mistral 24B | wrong directory ✗ | correct ✓ |
| Claude Opus 4 | `src/spool.test.ts` ✗ | correct ✓ |
| Gemma 4B (local CPU) | blank JSON ✗ | correct ✓ |

## The gap quale leaves open

- **No file content analysis.** Vocab measures phrase-level structure, not logic.
- **No semantic understanding.** It doesn't know what code does.
- **No new-repo support.** A repo with one file has nothing to measure.
- **Verification accuracy ceiling ~80%.** When the candidate set doesn't contain the
  right test file, quale documents the gap honestly rather than hallucinating.

This is by design. Vocab answers one question no other tool answers: "what's the
structure of this codebase, and what should I touch first?"

## Further reading

- [docs/COMMANDS.md](docs/COMMANDS.md) — full command reference
- [docs/EFFECT_HARNESS.md](docs/EFFECT_HARNESS.md) — detailed harness results
- [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md) — grammar-free philosophy and scope
