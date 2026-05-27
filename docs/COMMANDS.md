# Command Reference

## 🧑‍💻 Human Developer (Top-Level)

The top-level commands are optimized for low-friction, actionable insights for developers working in the terminal.

```bash
quale review                          # Single human review summary: blast radius, test gaps, hub risk, clones
quale onboard                         # 3-step onboarding plan (landmarks, modules, safe directories)
quale refactor-cost src/spool.ts      # Estimate refactoring effort for a file: blast + escape + clones + hub
quale inspect .                       # Comprehensive codebase overview: explore + modules + timeline + health
quale explore . --themes              # Onboarding map: best files to read first
quale search SpoolManager             # Find files containing a phrase or concept
quale diff HEAD~1 HEAD                # Compare vocabulary between two git refs
```

## 🤖 LLM Agent (`quale agent`)

Agent commands are tucked away in the `agent` namespace and implicitly return tightly packed JSON or IR DSL. Agents no longer need to remember `--format json` flags.

```bash
quale agent edit src/spool.ts         # File-scoped edit context and risk card
quale agent guard src/spool.ts        # Combined safety packet: guide + hub-risk + complexity + criticality
quale agent orient                    # One-call orientation: solve + triangulate + isolate + repo-map
```

## 🚦 CI Maintainer (`quale ci`)

CI commands are built to act as automated GitHub Actions steps. Exit codes map to specific gates.

```bash
quale ci init                         # Generates a ready-to-use GH Actions YAML at .github/workflows/quale.yml
quale ci check origin/main HEAD       # Runs all gates (exits 0-7)
quale ci comment origin/main HEAD     # Posts the PR report as a GitHub comment
quale ci trend                        # CI metric trends over time
```

## 🔬 Core Primitives (`quale core`)

Over 30 advanced architectural primitives and algorithms power the insights above. They are available via the `core` namespace.

```bash
quale core blast                      # Vocabulary overlap with changed files
quale core drift-check                # Structural anomaly velocity across directories
quale core test-gaps                  # Source files with weak test mirrors
quale core capillary                  # Files with the most inter-file vocabulary edges
quale core spectral-gap               # Modularity score: largest cluster / second largest
quale core phantom                    # Detect framework/library from import/export vocabulary
quale core trap                       # Identifier overlap between two concurrently-edited files
quale core hub-risk                   # High-centrality files with zero edits
quale core escape-velocity            # Phrase removal difficulty: ESCAPED / BOUND / DEEP
quale core heisenberg                 # Mixed refactor/feature edits that must be split
```

## Exit codes (CI)

| Code | Gate | Flag |
|------|------|------|
| 0 | Pass | — |
| 1 | Error | — |
| 2 | Blast tier | `--fail-on-blast-tier <tier>` |
| 3 | Stable anchor | `--fail-on-stable-touched` |
| 4 | Mirror gap | `--fail-on-mirror-gap <ratio>` |
| 5 | Hub risk | `--fail-on-hub-risk` |
| 6 | Clone detected | `--fail-on-clone` |
| 7 | Identifier explosion | `--fail-on-new-identifiers <N>` |
