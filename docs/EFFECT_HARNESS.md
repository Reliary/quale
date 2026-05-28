# Measured Effect

## Cross-model verification (May 2026)

6 models tested on 2 private repos (autopsylab-agent, autopsylab). Every model
without quale guessed the wrong test file. Every model with quale picked the right file.

| Model | Where | Baseline guess | With quale |
|-------|-------|---------------|--------------|
| Qwen/Qwen3-235B-A22B | deepinfra | `src/spool.test.ts` ✗ | `tests/spool.test.ts` ✓ |
| google/gemma-4-31B-it | deepinfra | `test/spool.test.ts` (wrong dir) ✗ | `tests/spool.test.ts` ✓ |
| nvidia/Nemotron-3-Nano-30B | deepinfra | wrong + 277 output tok ✗ | right + 112 output tok ✓ |
| mistralai/Mistral-Small-3.2-24B | deepinfra | wrong directory ✗ | correct ✓ |
| anthropic/claude-opus-4-7 | deepinfra | `src/spool.test.ts` ✗ | `tests/spool.test.ts` ✓ |
| gemma-4-E4B-it-Q4_K_M (local) | llama.cpp CPU | blank (needs 500 tok for reasoning) | correct at 1/3 inference tok |

## deepseek-v4-flash harness (1,100 trials, 12 repos)

### Preflight / verification scope control

| Condition | Test Accuracy | Extra Edits | Tokens | Cost/Benefit |
|-----------|---------------|-------------|--------|--------------|
| baseline (no quale) | 10-20% | 0.40-0.65 | ~1,200 | 0.55-0.90 |
| `edit-context --format tool` | 75% | 0.0 | 1,658 | 1.60 |
| `verify_scope` | 83% | 0.0 | 1,233 | 2.29 |
| `verify_entangle` | 80% | 0.0 | ~1,300 | 2.87 |
| `progressive_verify` | 65% | 0.0 | ~1,280 | 2.27 |

Best all-round: `verify_entangle` (includes git co-change signal).
Best for weak models: `verify_scope` (verification-only, removes edit decision).

### OpenCode (full tool-access agent)

| Condition | Test Accuracy | Extra Edits |
|-----------|---------------|-------------|
| baseline | 46% | 0.31 |
| `fragment_route` | 100% | 0.0 |

## Key takeaways

- **Scope containment is the only durable claim**: every quale condition eliminated agent wandering
  (0.0 extra edits) across all repos and models.
- **Every model benefits from structure**: Qwen 235B and Claude Opus 4 both guess
  `src/spool.test.ts` on a blank prompt--no model is immune to structural blind spots.
- **Structured JSON beats prose**: compact oneline JSON doubles test accuracy rate vs narrative
  guidance.
- **The 17% boundary**: repos without stem-matched tests or co-change history remain
  structurally ambiguous. Quale documents this honestly rather than hallucinating.

## Using the harness

```bash
python scripts/evaluate_quale_effect.py --dry-run --max-cases 2
python scripts/evaluate_quale_effect.py --suite edit-context --trials 3
python scripts/analyze_effect_failures.py /tmp/quale-effect-edit-context-3trial.json
```

### Mapping harness conditions to CLI commands

The harness tested several conditions. Here's how they map to current CLI commands:

| Harness condition | CLI command | Status | Notes |
|-------------------|-------------|--------|-------|
| `edit-context --format tool` | `quale ec` or `quale core edit-context --format tool` | ✓ Active | Primary LLM surface, 75% accuracy |
| `verify_scope` | `quale core verify-scope` | ✓ Active | Verification-only, 83% accuracy on weak models |
| `verify_entangle` | `quale vp` (verify-packet has `entangled_candidates`) | ✓ Via JSON field | Closest match includes co-change signal |
| `progressive_verify` | `quale core veto-cascade` | ✓ Via veto-cascade | Closest match: multi-step deterministic → oscillatory → manual |
| `diff_edit-context` | `quale ec --diff <ref>` | ✓ Via --diff flag | Same engine, use --diff instead of --files |
| `candidate_baseline` | N/A | Baseline only | No-quale control condition |
| `route_policy` | N/A | Internal | Routing logic, not user-facing |
| `ask` | N/A | ✗ Killed | 0% accuracy, worse than baseline |
| `negotiate_simple` | N/A | Experimental | Not yet exposed as CLI command |
| `contract` | N/A | Experimental | Needs more harness trials |

**Recommended commands for users:**
- **LLM agents**: Use `quale ec` (edit context) before every edit
- **Weak models**: Use `quale core verify-scope` for highest accuracy (83%)
- **PR reviews**: Use `quale ec --diff <ref>` for diff-aware context
- **General use**: Use `quale vp` (verify-packet) for co-change signal

Decision rules:
- keep `edit-context --format tool` as primary LLM surface: 75% accuracy, 0 extra edits
- keep `diff_edit-context` for PR/diff workflows: 100% accuracy, 0 extra edits
- kill `ask`: 0% accuracy (worse than baseline)
- `contract` path: experimental, needs more harness trials
- re-run the harness after wording/ranking changes
- mine failure rows after each run
