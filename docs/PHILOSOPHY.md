# Philosophy

quale treats code as a **phrase-file incidence matrix** — a qualeulary lattice that
can be diffed, analyzed, and traced without knowing the language. This reframing
unlocks capabilities that no existing tool provides:

- **Grammar-free**: Works across languages because it operates on delimiter boundaries
  and phrase frequencies, not ASTs or parsers.
- **Deterministic**: Same input → same output. No models, no training, no randomness.
- **Strictly structural**: Reports phrase presence, absence, and co-occurrence. Never
  claims semantic meaning, correctness, or intent.
- **Evidence over taste**: No semantic denylists. Filters are mechanical (generated
  paths, identifier length, frequency). Users apply judgment.
- **Local-first**: No network, no upload. All computation is git-backed and
  filesystem-scoped.
- **Report-only**: Every command is advisory. Guardrails are informational, not
  blocking, unless explicit `--fail-on-*` flags are used.

## Scope

quale is intentionally parser-free. It works across languages and formats because it
relies on phrase/file/history matrices, not language-specific ASTs. That makes it
useful for orientation, drift, impact, and structure. It also means users and agents
must apply judgment before treating any signal as policy.
