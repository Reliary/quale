# Vocab vs. Competition

What Vocab does that nothing else does, and why it matters for LLM agents.

## The Landscape

| Capability | Vocab | grep/rg | semgrep | tree-sitter | CodeQL | SonarQube | depcruise |
|-----------|-------|---------|---------|-------------|--------|-----------|-----------|
| Cross-language (one pipeline) | **native** | yes | per-rule | per-lang | per-lang | per-lang | per-lang |
| Zero config | **yes** | yes | needs rules | needs grammars | needs DB | needs config | needs config |
| ~100ms per query | **yes** | yes | yes | yes | no | no | yes |
| No AST/syntax req | **yes** | yes | needs AST | IS an AST | needs AST | needs AST | needs AST |
| Structural coupling | **native** | no | limited | no | yes | no | yes |
| Blast radius (change impact) | **native** | cobbled | no | no | yes² | no | limited |
| Module boundaries (auto) | **native** | no | no | no | no | no | manual only |
| LLM edit verification | **native** | no | no | no | no | no | no |
| LLM token reduction | **native** | no | no | no | no | no | no |
| Agent bootstrap (read/edit/verify) | **native** | no | no | no | no | no | no |
| CI gate (exit-code) | **native** | no | yes | no | yes | yes | no |
| History-aware (git snapshots) | **native** | no | no | no | no | no | no |
| Cross-repo drift | **native** | scripted | no | no | no | no | scripted |

¹ CodeQL can find some coupling but requires QL queries and a database build — 10-100× slower.
² depcruise does dependency graph analysis but requires per-language setup and manual rule config.

## Where each tool wins (don't compete here)

| Tool | Best at | Vocab doesn't |
|------|---------|---------------|
| **grep/ripgrep** | Finding exact strings fast | Found string search — `search` is a side feature |
| **semgrep** | Finding bug patterns (AST-aware) | Have AST. No pattern language. Different problem |
| **tree-sitter** | Parsing, syntax tree queries | Parse. Different audience (humans vs AGENTS) |
| **CodeQL** | Deep semantic vulnerability analysis | Have 1% of this. Not trying to compete |
| **SonarQube** | Dashboard, trend tracking over time | UI, dashboard, server. CLI-only by design |
| **depcruise** | Visual dependency graph | Visual output. Focused on agent consumption |
| **cloc/tokei** | Line counting | Line counting. Have 1 command for it |

## What No Tool Should Claim

Vocab does not:
- Find bugs (semgrep, CodeQL win)
- Understand semantics (tree-sitter + analysis wins)
- Parse syntax (ALL parser tools win)
- Visualize architecture (depcruise, Structure101 win)
- Replace code review (humans win)
- Measure test coverage (istanbul, coverage.py win)

## The Unique Gap Vocab Fills

**Agent orientation in 100ms without a parser.**

An LLM agent needs four things before editing code:
1. What files exist? → `explore`, `repo-map`
2. What file to read first? → `agent-bootstrap`, `guide`
3. What breaks if I edit this? → `edit-context`, `latent-deps`
4. How do I verify? → `cascade-verify`, `verify-packet`

Every other tool answers ONE of these with 10× the latency and per-language setup.

Vocab answers ALL FOUR in one CLI call (the `guard` composition aggregates 4 signals) with 0 config and 100ms query time.

There is no tool in the same quadrant.
