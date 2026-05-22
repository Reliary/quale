# vocab — SPEC

`vocab` is a grammar-free structural codebase analyzer. It answers cross-language
structural questions about codebases using phrase/file/history matrices — no ASTs,
no parsers, no per-language config.

## Philosophy

1. **Evidence over taste**: Show structural signal, don't hide it behind filter lists.
   Users (agents, humans, CI) apply their own judgment.
2. **Grammar-free**: One pipeline works with any language or format because it
   operates on delimiter-split phrases, not syntax trees.
3. **Zero-config**: No language-specific setup, no plugins, no config files required.
4. **Read-only**: Never mutates the repo. All output is derived from git blobs
   and working-tree files.

## Architecture

```
Input: git repo or working tree
  → phrase_segmenter       (delimiter-split phrases, no grammar)
  → vocabulary_builder     (phrase → frequency map per file)
  → index_encoder          (phrase → base-N index for compression)
  → analysis layers        (explore, modules, stability, timeline, etc.)
  → CLI output             (terminal summary, JSON, CI gates)
```

## Commands

| Command | Scope | Mode | Description |
|---------|-------|------|-------------|
| `agent-bootstrap` | working tree | fast | Repo orientation for agent: next reads, edit files, task plan |
| `inspect` | working tree + history | heavy | Full structural overview: explore, modules, themes, binding concepts, timeline |
| `ci-report` | diff | fast | PR structural impact: blast radius, mirror gap, stable anchors, gates |
| `explore` | working tree | fast | Top files ranked by unique identifiers, themes |
| `modules` | working tree | medium | Parser-free module boundaries from vocabulary overlap |
| `stable` | history | medium | Stable anchors and churn hotspots |
| `lifecycle` | history | medium | Concept lifecycle classes (STABLE, GROWING, DECAYING, EMERGING, etc.) |
| `timeline` | history | medium | Per-week concept entry/exit over N weeks |
| `diff` | history | fast | Vocabulary-level diff between refs |
| `compare` | cross-repo | medium | Bidirectional vocabulary alignment and drift asymmetry |
| `fingerprint` | working tree | fast | Structural identity hash from all-file vocabulary |
| `search` | cross-repo | fast | Cross-repo concept search |
| `provenance` | history | medium | When a phrase entered, spread, and persisted through git history |
| `help-agent` | none | instant | Maps natural-language tasks to vocab command sequences |
| `pr-report` | diff | fast | Consolidated PR comment markdown |
| `gate` | diff | fast | Exit-code gating by blast/orphans/drift/lifecycle |

## Output Schemas

### agent-bootstrap JSON

```json
{
  "schema_version": 1,
  "recommended_next_reads": [{"file": "src/main.ts", "reason": "..."}],
  "task_plan": {"likely_edit": [".git/refs..."], "stable_anchors": [...]},
  "related_files_for_task": [{"file": "...", "matches": 12, "role": "source"|"test"}],
  "task_relevance_score": 0.85,
  "verified_files": [...],
  "unverified_files": [...],
  "module_boundaries": [...],
  "themes": [...],
  "agent_notes": ["..."],
  "total_code_files": 258
}
```

### ci-report JSON

```json
{
  "schema_version": 1,
  "changed_files": ["..."],
  "blast_radius": [{"file": "...", "shared_concepts": 5, "tier": "local"}],
  "mirror_signals": {"mirror_ratio": 0.31, "unmirrored_concepts": [...]},
  "mirror_gap_ratio": 0.31,
  "max_blast_tier": "moderate|high|critical",
  "stable_touched_count": 2,
  "blast_tier_counts": {"local": 5, "moderate": 2, "high": 0, "critical": 0},
  "stable_files_touched": [...],
  "risk_flags": ["..."],
  "summary": "..."
}
```

### inspect JSON

```json
{
  "schema_version": 1,
  "total_files": 258,
  "total_phrases": 42561,
  "total_unique_phrases": 18922,
  "languages": {"TypeScript": 200, "JavaScript": 30, ...},
  "explore": {
    "files": [{"file": "...", "language": "TypeScript", "unique_score": 42.5, "identifiers": 312, "coverage": 0.02}],
    "themes": [...],
    "total_code_files": 258
  },
  "modules": [...],
  "binding_concepts": [{"concept": "SpoolManager", "score": 37.2, "file_count": 12, "languages": ["TypeScript"], "files": [...], "why": "..."}],
  "timeline": [...]
}
```

## Stability

- `schema_version: 1` fields are stable.
- New fields may be added but existing fields will not be removed or renamed within schema_version.
- Terminal output format may change; JSON is the stable surface.

## Niche Language Support

vocab works with any language. Confirmed working on:

- Go, TypeScript, JavaScript, Python, Rust, C, C++, Java, Kotlin, Swift,
  Ruby, PHP, C#, SQL, YAML, JSON, XML, Markdown, TOML, Dockerfile,
  Shell, Protobuf, HTML, CSS, SCSS
- Nix, OCaml, Erlang, Elixir, Zig
- Haskell, Julia, R, Clojure, Scala, ClojureScript, F#, SML

New languages require only adding the extension to `_DEAD_CODE_EXTS`
and `classify_language` — the tokenization pipeline is language-agnostic.

## Limitations

- No semantic understanding. Vocab reports what co-occurs, not what it means.
- No dead-code proof. Vocabulary gap analysis finds candidates but cannot
  distinguish genuinely unused code from indirect references (function values,
  interface dispatch, reflection).
- No coverage proof. Source/test mirror analysis reports vocabulary overlap,
  not branch coverage.
- No architecture-intent inference. Modules from vocabulary overlap may not
  match human-defined module boundaries.
- License boilerplate and generic built-in types (Promise, String, Error)
  can appear as binding concepts in smaller repos. This is intentional:
  they ARE structural signal — just not always useful signal.
