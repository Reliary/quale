# vocab — Feature-Persona Matrix

## Personas

| Persona | Need | Works best with |
|---------|------|----------------|
| **Agent** (LLM coding agent) | Bounded guidance, scope control, speed labels | JSON output, compact format, single-file flags |
| **Human** (developer) | Structural insight, cleanup lists, architecture discovery | Terminal output, --format markdown |
| **CI** (pipeline gate) | Deterministic pass/fail, trend numbers, hash comparisons | JSON output, exit codes, zero-token overhead |

---

## Core Analysis (always available)

| Command | Signal | Agent | Human | CI | Why |
|---------|--------|-------|-------|----|-----|
| `specral-gap` | Single-number modularity score | ❌ | ⭐ | ⭐ | 1 float = repo health. Agent can't act on it |
| `capillary` | High-edge-count files | ❌ | ⭐ | ❌ | Lists most coupled files. One-time insight |
| `phantom` | Framework/library detection | ⭐ | ⭐ | ❌ | "This repo uses Zustand, not Redux" — agent convention hint |

## Agent Guidance

| Command | Signal | Token cost | Why |
|---------|--------|-----------|-----|
| `guide --file X` | 1-token unique file locator | ~2 | "Navigate to PgPaymentRepository" instead of path exploration |
| `trompe --file X` | Apparent vs true complexity | ~4 | "Skip at 4x speed" or "2x attention" |
| `thanatosis` | High-centrality rarely-edited files | ~8 | "Don't touch this file — 0 edits, 1054 centrality" |
| `tensegrity` | Indirect coupling via intermediaries | ~15 | "Edit A will break B through C,D,E intermediaries" |
| `criticality --file X` | 2-hop amplification ratio | ~4 | "k=3.2 — your edit will amplify" |

## Human Insight

| Command | Signal | Why |
|---------|--------|-----|
| `thylacine` | Extinct exports (defined everywhere, imported nowhere) | Cleanup backlog |
| `tensegrity` | Indirect coupling | Debug hidden dependencies |
| `capillary` | Most coupled files | Refactoring targets |
| `thanatosis` | Playing-dead structural hubs | Risk assessment |
| `trap --file-a X --file-b Y` | Identifier-level overlap | Safe parallel editing confidence |

## CI Gates

| Command | Signal | Token cost | Gate logic |
|---------|--------|-----------|------------|
| `porosity` | Excess coupling vs random | ~4 | "excess_porosity dropped → run blast on every PR" |
| `trap --file-a X --file-b Y` | Divergence/over-trap warning | ~8 | "<10% = divergence gap → flag for review" |
| `parity-bit --ref-a A --ref-b B` | Test mirror unchanged | 16 bytes | "mirror_unchanged = skip test suite" |
| `escape-velocity --min-freq N` | Phrase removal effort label | ~8 | "DEEP = need multi-sprint effort" |

## Verification & Safety

| Command | Signal | Best for |
|---------|--------|----------|
| `mirage --test-file X` | Test claims misaligned with structure | Agent pre-edit verification |
| `trap --file-a X --file-b Y` | Identifier-level conflict prediction | CI pre-merge |
| `check-plan` | Contract validation | Agent post-generation |

## History & Trends

| Command | Signal | Frequency |
|---------|--------|----------|
| `specral-gap` | Modularity trend | Weekly CI |
| `porosity` | Coupling trend | Weekly CI |
| `forecast --files X` | Bugfix regression risk | Pre-edit |
| `decay --file X --metabolism` | Active migration in progress | Pre-edit |

## Cross-repo

| Command | Signal | Use case |
|---------|--------|----------|
| `dark-matter --repo-a X --repo-b Y` | Orphans in A that bind to B | Distributed system safety |
| `compare --contract-only` | Contract surface drift | API compatibility |
| `migrate --from X --to Y` | Phrase-level substitution mask | Zero-hallucination migrations |

## Quick reference by persona

### Agent workflow
```bash
vocab guide --file target.ts                    # 1-token file location
vocab thanatosis --path .                       # don't-touch list
vocab trompe --file target.ts                    # skip speed
vocab tensegrity --path .                        # indirect coupling check
vocab criticality --file target.ts                # amplification check
vocab mirage --test-file target.test.ts           # verification alignment
vocab trap --file-a X --file-b Y                  # parallel edit safety
```

### Human workflow
```bash
vocab capillary --path .                         # most coupled files
vocab thylacine --path .                         # extinct exports to clean
vocab tenegrity --path .                          # hidden indirect coupling
vocab thanatosis --path .                         # risky untouched files
vocab specral-gap --path .                        # modularity score
vocab phantom --path .                            # framework detection
```

### CI workflow
```bash
vocab porosity --path --format json               # coupling trend gate
vocab parit-bit --ref-a main --ref-b HEAD         # test mirror gate
vocab trap --file-a X --file-b Y --format json    # pre-merge conflict check
vocab escape-velocity --path --format json         # effort estimation
```
