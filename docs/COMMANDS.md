# Command Reference

## Agent: Getting Started (Install to Productive)

```bash
quale --agent-orient                                    # JSON manifest: conventions, workflow, gotchas
quale help-agent "change upload behavior" --format tool  # task-specific recommendations + workflow
```

Every agent-facing command carries an `_agent_note` field in `--format tool` JSON
explaining its flag syntax.

## Agent: Scope Control (Proven)

```bash
quale edit-context --files src/spool.ts --task "..." --format tool  # 75% accuracy, 0 scope creep
quale edit-context --diff HEAD~1 --task "..." --format tool           # 75% accuracy (diff-scoped)
quale edit-context --files src/spool.ts --task "..." --format verify  # 83% accuracy (test-only)
quale guard --file src/spool.ts --task "..." --format tool          # combined safety packet
quale contract --files src/spool.ts --task "..."                    # ID-coded scope (default tool)
quale check-plan --contract c.json --proposal p.json               # validate LLM proposal
```

## Agent: Orientation (Secondary)

```bash
quale repo-map --path . --format json         # ~100 token repo skeleton
quale agent-bootstrap . --task "..." --format checklist  # weak-model step-by-step
quale help-agent "debug upload" --format tool              # discoverability + conventions + gotchas
```

## Human: Overview & Health

```bash
quale inspect .                         # comprehensive overview + health score
quale explore . --themes                # onboarding map and themes
quale modules .                         # parser-free module boundaries
quale test-gaps --path .                  # source files with weak test mirrors
quale stop --path . --read file1.ts        # exploration entropy gauge
```

## Human: Preflight & Guardrails

```bash
quale edit-context --files src/spool.ts --task "..."   # file-scoped risk card (human)
quale edit-context --diff origin/main                   # diff-scoped risk card (human)
quale verify --files src/spool.ts                    # multiple-choice verification
quale route --files src/spool.ts --task "..."         # decide whether/how to use quale
```

## CI / PR Tools

```bash
quale ci-report origin/main HEAD --summary           # blast radius + mirror gap
quale anomalies --path . --base main --head HEAD        # crystallographic defect detection
quale patterns --path . --base HEAD~1                 # refactoring pattern recognition
quale pr-report origin/main HEAD                      # consolidated markdown report
```

## History & Evolution

```bash
quale vocabulary-trend --path . --weeks 12                    # vocabulary trend velocity
quale lifecycle . --weeks 24                         # phrase lifecycle (stable/decaying/etc)
quale stable .                                       # stable anchors and churn hotspots
quale provenance "SpoolManager" .                    # phrase history through git
quale timeline . --weeks 4                           # concept entry/exit timeline
quale origins --path .                               # concept origin tracing (endogenous vs imported)
```

## Cross-Repo & Analysis

```bash
quale compare ../repo-a ../repo-b --contract-only    # contract-surface drift
quale search SpoolManager ../repo-a ../repo-b        # cross-repo phrase search
quale skeleton --path .                              # prompt decompression: ~100-token skip directives
quale fingerprint .                                  # repo structural identity
quale coupling --path .                              # concept coupling classification
quale diff --ref HEAD~10                             # vocabulary changes across git history
quale landmarks .                                    # characteristic phrases
quale delta --path .                                 # structural changes since `quale init`
```

## Structural Analysis

```bash
quale fold --file src/billing.ts --task "fix proration"           # fractional distillation
quale drift-check --file src/billing.ts --snapshot                # structural baseline
quale drift-check --file src/billing.ts                           # velocity spikes
quale forecast --files src/billing.ts                             # regression risk
quale latent-deps --files src/billing.ts                          # hidden dependencies
quale isolate --task "Update billing" --format json               # module bisection
quale heisenberg --file worker.ts --diff "$(cat patch)"           # refactor/feature separation
quale traffic-control --file UserProfile.tsx --intended-import api_client.ts  # import zoning
quale decay --file billing.ts --metabolism                        # legacy pattern clearance
quale clone --threshold 0.90                                      # cross-directory clone detection
```

## Exit codes

| Code | Meaning | Examples |
|------|---------|---------|
| 0 | Success | All commands |
| 1 | General error | Invalid path, not a git repo, parse failure |
| 2 | CI gate: blast tier | `ci-report --fail-on-blast-tier high` |
| 3 | CI gate: stable anchor | `ci-report --fail-on-stable-touched` |

## All commands (generated from CLI)

See [FEATURE_MATRIX.md](FEATURE_MATRIX.md) for the full auto-generated command list
grouped by panel (Agent Safety, CI, Code Analysis, etc.).
