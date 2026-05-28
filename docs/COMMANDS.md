# Command Reference

## Command variants

Quale commands are available in three forms:

1. **Short aliases** (recommended for agents): `quale ec`, `quale vp`, `quale o`
2. **Namespace commands**: `quale core edit-context`, `quale agent orient`
3. **MCP tools**: `edit_context`, `verify_packet`, `orient` (when using MCP server)

All three forms call the same underlying engine. Short aliases are optimized for
agent workflows where token efficiency matters.

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

## 🤖 LLM Agent

Agent commands are available as short aliases (recommended) or full namespace commands.
They return tightly packed JSON. Agents don't need to remember `--format json` flags.

### Short aliases (recommended)

```bash
quale ec src/spool.ts                 # Edit context: verification candidates, risk, scope guard
quale vp src/spool.ts                 # Verify packet: compressed verification scope
quale o                               # Orient: landmarks, modules, languages
```

### Namespace commands (alternative)

```bash
quale agent orient                    # Same as `quale o`
quale agent edit src/spool.ts         # Edit context with verification candidates
quale agent guard src/spool.ts        # Combined safety packet
```

### MCP tools (when using MCP server)

When the MCP server is configured, agents can call these as tools:

```
edit_context(file="src/spool.ts")     # Same as `quale ec`
verify_packet(file="src/spool.ts")    # Same as `quale vp`
orient()                              # Same as `quale o`
```

See [MCP_SETUP.md](MCP_SETUP.md) for configuration instructions.

## 🚦 CI Maintainer (`quale ci`)

CI commands are built to act as automated GitHub Actions steps. Exit codes map to specific gates.

```bash
quale ci init                         # Generates a ready-to-use GH Actions YAML at .github/workflows/quale.yml
quale ci check origin/main HEAD       # Runs all gates (exits 0-7)
quale ci comment origin/main HEAD     # Posts the PR report as a GitHub comment
quale ci trend                        # CI metric trends over time
```

## Unified Commands

These replace multiple old commands with single, mode-switched interfaces.

```bash
quale risk                             # Hub + capillary + vulnerability intersection
quale risk --mode hub                  # Hub risk only
quale risk --mode capillary            # Capillary risk only

quale verify --files src/auth.ts       # Combined verification: mc, scope, packet
quale verify --mode mc                 # Pre-edit multi-choice verification (75% accuracy)
quale verify --mode packet             # Post-edit co-change signal (80% accuracy)
quale verify --mode scope              # Post-edit scope verification (83% accuracy)

quale health                           # Structural health dashboard
quale health --mode score              # Single health score

quale audit HEAD~1 HEAD                # Review a diff: CI + PR + structural
quale temporal --file src/auth.ts      # Temporal analysis: decay + vocabulary trends
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
quale core escape-velocity            # Phrase removal difficulty: External / Contained / Internal
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
