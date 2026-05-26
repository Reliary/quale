# vocab — Feature-Persona Matrix

Auto-generated. Add `(v)` for value, `(s)` for signal-only, `(g)` for gate.

| Command | Panel | Description |
|---------|-------|-------------|
| `help-agent` | Getting Started | Recommend useful vocab commands for an agent task. |
| `repo-map` | Getting Started | One-time structural description of a codebase. |
| `cascade-verify` | Agent Safety | Multi-strategy verification pipeline. |
| `check-plan` | Agent Safety | Validate an LLM plan against an ID-coded contract. |
| `edit-context` | Agent Safety | File-scoped edit context and risk card. |
| `fold` | Agent Safety | Replace low-signal blocks with annotations. |
| `guard` | Agent Safety | Combined safety packet: guide + hub-risk + complexity + criticality. |
| `guide` | Agent Safety | One-token file locator for a file. |
| `isolate` | Agent Safety | Pre-edit file discovery via structural module bisection. |
| `triangulate` | Agent Safety | Intersect three structural probes to find the task anchor. |
| `verify-packet` | Agent Safety | Verification packet — compressed scope for LLM verification. |
| `verify-scope` | Agent Safety | Post-edit scope verification: compare actual diff against expected contract. |
| `veto-cascade` | Agent Safety | Veto cascade pipeline — ~33 avg tokens per verification call. |
| `zk-proof` | Agent Safety | Verify generated code identifiers against allowed set. |
| `co-change` | Verification | Show file co-change pairs from git history. |
| `reverse-verify` | Verification | Given changed test files, find source files that need verification. |
| `test-gaps` | Verification | Test gap map: source files with weak test mirrors. |
| `verify-bonds` | Verification | Detect when a change requires running multiple test files together. |
| `verify-classify` | Verification | Classify each changed file's verifiability type and structural gaps. |
| `verify-drift` | Verification | Track verification confidence across recent commits. |
| `check-diff` | CI | Post-proposal defect scan: detect structural violations. |
| `check-pr` | CI | CI PR summary: parity-bit + trap + diff. |
| `ci-report` | CI | CI-ready structural report: blast radius + stable file check + flags. |
| `drift-check` | CI | Structural anomaly velocity across directories. |
| `forecast` | CI | Forecast regression risk from co-change shifts. |
| `health` | CI | 0-1 health from stability, mirror, churn, concept age. |
| `health-score` | CI | 2-axis health: coupling density x modularity. |
| `parity-bit` | CI | SHA-1 of module phrase set. |
| `pr-report` | CI | PR structural report in markdown. |
| `capillary` | Code Analysis | Files with the most inter-file vocabulary edges. |
| `complexity-ratio` | Code Analysis | Apparent lines vs unique identifiers. |
| `coupling-chain` | Code Analysis | Indirect coupling with no direct edge. |
| `criticality` | Code Analysis | 2-hop amplification ratio: changes amplify or dampen. |
| `hub-risk` | Code Analysis | High-centrality files with zero edits. |
| `latent-deps` | Code Analysis | Detect hidden structural dependencies (no direct imports). |
| `phantom` | Code Analysis | Detect framework/library from import/export vocabulary. |
| `porosity` | Code Analysis | Sparse coupling estimate without computing co-occurrence. |
| `spectral-gap` | Code Analysis | Modularity score: largest cluster / second largest. |
| `trap` | Code Analysis | Identifier overlap between two concurrently-edited files. |
| `anomalies` | Maintenance | Detect structural anomalies and outliers in vocabulary. |
| `cleanup-list` | Maintenance | Prioritized cleanup: extinct-exports x escape-velocity. |
| `concept-flow` | Maintenance | Track phrase spread across weekly snapshots. |
| `decay` | Maintenance | Legacy patterns; --metabolism for active decline. |
| `deflate` | Maintenance | Cap net-new identifiers per edit. |
| `diff-structural` | Maintenance | Structural fingerprint diff between two git refs. |
| `entropy` | Maintenance | Dir-level vocabulary fragmentation vs 30-commit baseline. |
| `escape-velocity` | Maintenance | Phrase removal difficulty: ESCAPED / BOUND / DEEP. |
| `extinct-exports` | Maintenance | Multi-file exports never imported externally. |
| `heisenberg` | Maintenance | Mixed refactor/feature edits that must be split. |
| `migration-pairs` | Maintenance | Deterministic phrase substitution from two-repo comparison. |
| `origins` | Maintenance | Concept origin: which concepts are native vs imported?. |
| `safe-islands` | Maintenance | Structurally isolated blocks safe to edit. |
| `solve` | Maintenance | Surface cipher keys: non-dictionary identifiers to learn a repo. |
| `traffic-control` | Maintenance | Zone files by graph centrality percentile. |
| `vocabulary-trend` | Maintenance | Entropy velocity: is vocabulary diversity accelerating or decelerating?. |
| `vulnerability-map` | Maintenance | Overlap of hub-risk and capillary. |
| `coupling` | Cross-Repo | Concept coupling classification: tightly bound, loosely bound, independent. |
| `agent-bootstrap` | Utilities | One-shot agent bootstrap: explore + modules + stability + related files. |
| `fingerprint` | Utilities | Structural fingerprint of a file or entire repo. |
| `orient` | Utilities | One-call orientation: solve + triangulate + isolate. |
