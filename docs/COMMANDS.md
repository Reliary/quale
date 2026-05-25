# Command Reference

## Agent: Scope Control (Proven)

```bash
vocab edit-context --files src/spool.ts --task "..." --format tool  # 75% verify, 0 sprawl
vocab edit-context --diff HEAD~1 --task "..." --format tool           # 75% verify (diff-scoped)
vocab edit-context --files src/spool.ts --task "..." --format verify  # 83% verify (verification-only)
vocab contract --files src/spool.ts --task "..." --format tool     # ID-coded scope (experimental)
vocab check-plan --contract c.json --proposal p.json               # validate LLM proposal
```

## Agent: Orientation (Secondary)

```bash
vocab repo-map --path . --format json         # ~100 token repo skeleton
vocab agent-bootstrap . --task "..." --format checklist  # weak-model step-by-step
vocab help-agent "debug upload"                     # discoverability: which command?
```

## Human: Overview & Health

```bash
vocab inspect .                         # comprehensive overview + health score
vocab explore . --themes                # onboarding map and themes
vocab modules .                         # parser-free module boundaries
vocab test-gaps --path .                  # source files with weak test mirrors
vocab stop --path . --read file1.ts        # exploration entropy gauge
```

## Human: Preflight & Guardrails

```bash
vocab edit-context --files src/spool.ts --task "..."   # file-scoped risk card (human)
vocab edit-context --diff origin/main                   # diff-scoped risk card (human)
vocab verify --files src/spool.ts                    # multiple-choice verification
vocab route --files src/spool.ts --task "..."         # decide whether/how to use vocab
```

## CI / PR Tools

```bash
vocab ci-report origin/main HEAD --summary           # blast radius + mirror gap
vocab anomalies --path . --base main --head HEAD        # crystallographic defect detection
vocab patterns --path . --base HEAD~1                 # refactoring pattern recognition
vocab pr-report origin/main HEAD                      # consolidated markdown report
```

## History & Evolution

```bash
vocab vocabulary-trend --path . --weeks 12                    # vocabulary vocabulary-trend velocity
vocab lifecycle . --weeks 24                         # phrase lifecycle (stable/decaying/etc)
vocab stable .                                       # stable anchors and churn hotspots
vocab provenance "SpoolManager" .                    # phrase history through git
vocab timeline . --weeks 4                           # concept entry/exit timeline
vocab origins --path .                               # concept origin tracing (endogenous vs imported)
```

## Cross-Repo & Analysis

```bash
vocab compare ../repo-a ../repo-b --contract-only    # contract-surface drift
vocab search SpoolManager ../repo-a ../repo-b        # cross-repo phrase search
vocab skeleton --path .                              # prompt decompression: ~100-token skip directives
vocab fingerprint .                                  # repo structural identity
vocab coupling --path .                              # concept coupling classification
vocab diff --ref HEAD~10                             # vocabulary changes across git history
vocab landmarks .                                    # characteristic phrases
vocab delta --path .                                 # structural changes since `vocab init`
```

## Structural Analysis

```bash
vocab fold --file src/billing.ts --task "fix proration"           # fractional distillation
vocab drift-check --file src/billing.ts --snapshot                # structural baseline
vocab drift-check --file src/billing.ts                           # velocity spikes
vocab forecast --files src/billing.ts                             # regression risk
vocab mycorrhiza --files src/billing.ts                           # hidden dependencies
vocab isolate --task "Update billing" --format json               # module bisection
vocab heisenberg --file worker.ts --diff "$(cat patch)"           # refactor/feature separation
vocab traffic-control --file UserProfile.tsx --intended-import api_client.ts  # import zoning
vocab pulsar --file core/worker.ts                                # anchor deletion detection
vocab decay --file billing.ts --metabolism                        # legacy pattern clearance
vocab condensate --threshold 0.90                                 # cross-directory clone detection
```
