# vocab

`vocab` is a grammar-free structural codebase analyzer. It scans any language with the same phrase/file/history pipeline, then surfaces structural signals for agents, humans, and CI without ASTs or per-language setup.

It is an orientation and drift tool. It reports evidence; it does not claim semantic truth, coverage proof, dead-code certainty, or security analysis.

## Start Here

```bash
vocab agent-bootstrap . --task "fix upload" --summary
vocab inspect .
vocab help-agent "change API client"
```

## Workflow 1: Agent Enters A Repo

Use `agent-bootstrap` before editing unfamiliar code when you want an orientation map:

```bash
vocab agent-bootstrap . --task "fix spool upload"
vocab agent-bootstrap . --task "fix spool upload" --verify-relevance --format json
```

With a task, the terminal summary starts with a task-specific file to read, likely edit files, relevance, architecture context, and module context. Without a task, it starts with the strongest architecture read. JSON output includes:

- `recommended_next_reads`: files an agent should inspect before editing.
- `task_plan`: likely edit files, stable anchors, and sequence guidance.
- `task_relevance_score`: how many suggested files contain task keywords.
- `verified_files` / `unverified_files`: evidence behind the score.

Use `--verify-relevance` as a sanity check. A low score means the task terms were too broad or the tool found weak matches.

Do not treat `agent-bootstrap` as proof that a strong model will find files better. In a 12-repo `deepseek-v4-flash` harness, task-only bootstrap guidance added tokens and did not improve file discovery over a baseline that already had filenames. Its safer role is orientation for humans, smaller models, and unfamiliar repos.

## Workflow 2: Human Reviews A PR

Use `preflight` when you already know the file or diff being edited:

```bash
vocab preflight --files src/spool.ts --task "change upload behavior"
vocab preflight --files src/spool.ts --format tool
vocab preflight --diff origin/main --format checklist
vocab preflight --diff HEAD~1 --format json
```

`preflight` is intentionally file-scoped. It reports capped structural evidence: changed files, read-first context, verification candidates, stable anchors, reverse blast, risk, confidence, and a local-only privacy receipt. It does not claim semantic correctness.

The strongest measured use so far is verification scaffolding. In a 12-repo, 3-trial `deepseek-v4-flash` harness, preflight improved verification-file selection and reduced unrelated edit expansion:

- baseline verification hit rate: `8.3%`
- `preflight --format compact` verification hit rate: `31.4%`
- `preflight --format checklist` verification hit rate: `33.3%`
- baseline unrelated extra edits: `0.33` per run
- compact preflight unrelated extra edits: `0.06` per run

The effect was strongest on private/unseen TypeScript/Python-ish repos and weak on weird-language public repos where test discovery is still poor. Treat `preflight` as a local review/edit scaffold, not as an oracle.

Preflight guardrails:

- `VERIFY CANDIDATES` means candidate files to inspect, not guaranteed tests.
- `EXPANSION RISK` means inspect before broadening scope, not “never edit.”
- `verification_confidence` explains whether structural test mirrors are strong, mixed, or weak.
- `edit_sprawl_guard` asks agents to question extra edits outside the requested file set.
- output is report-only and must not be used as an automatic prompt-injection or blocking policy.
- every preflight JSON payload includes `guardrails.mode = "report_only"` and `guardrails.caveat = "May be wrong; inspect before acting."`

If you are not sure whether `vocab` should be used, ask the router:

```bash
vocab route --task "fix upload"                         # often says no_vocab / skeleton only
vocab route --files src/spool.ts --task "fix upload"     # routes to preflight_tool
```

The router encodes measured behavior: task-only bootstrap can hurt strong-model discovery, while file-scoped preflight reduces sprawl and helps verification.

LLM UI rule of thumb:

- unknown files + vague task: **do not add vocab**; search normally
- unknown files + scoped task: use `vocab skeleton` only
- known files or diff: use `vocab preflight --format tool`
- high-risk edit: include the `edit_sprawl_guard` and verification confidence fields

Use `ci-report` locally to see structural impact before review:

```bash
vocab ci-report origin/main HEAD --summary
vocab ci-report origin/main HEAD
```

The summary answers the decision question first:

- `PASS`, `FAIL`, or `INFO`.
- the first failing reason, if any.
- mirror ratio, blast tier, and stable-anchor count.

Use the full output when you need details about changed files, impacted files, mirror gaps, and risk flags.

## Workflow 3: CI Gates Structural Drift

Use opt-in gates when your repo has chosen thresholds:

```bash
vocab ci-report origin/main HEAD --fail-on-mirror-gap 0.70
vocab ci-report origin/main HEAD --fail-on-blast-tier high
vocab ci-report origin/main HEAD --fail-on-stable-touched
```

Exit codes are stable:

- `1`: mirror gap below threshold.
- `2`: blast tier met or exceeded threshold.
- `3`: stable anchors touched.

The report includes deterministic numeric fields for automation:

- `mirror_gap_ratio`: changed source concepts mirrored in tests.
- `max_blast_tier`: highest blast tier from shared concepts (`local`, `moderate`, `high`, `critical`).
- `stable_touched_count`: stable anchor files touched by the change.
- `blast_tier_counts`: count of impacted files by tier.

Gate flags are opt-in. Maintainers choose policy; `vocab` only provides structural measurements.

## Effect Harness

Use the harness when changing agent-facing output:

```bash
python scripts/evaluate_vocab_effect.py --dry-run --max-cases 2
python scripts/evaluate_vocab_effect.py --suite preflight --trials 3
python scripts/analyze_effect_failures.py /tmp/vocab-effect-preflight-3trial.json
```

The harness compares baseline prompts against `agent-bootstrap`, `crystallography`, `preflight --format compact`, `preflight --format checklist`, `preflight --format tool`, route policy, sprawl-guard prompts, and desert-aware prompts across likely-seen public repos, weird-language public repos, and private/unseen repos. It records parse/error rates, edit-file hits, verification hits, unrelated extra edits, and token cost.

Decision rule:

- keep `preflight` if it improves verification choice or reduces edit sprawl without excessive false positives
- keep `agent-bootstrap` as orientation, not as a strong-agent file-discovery booster
- do not add automatic prompt injection unless a harness shows a clear behavioral win
- re-run the harness after wording/ranking changes because “helpful-looking” output can still degrade behavior
- mine failure rows after each run; repeated failures become concrete product fixes, not anecdotes
- prefer `route_policy` or `preflight_tool_sprawl_guard` only if they preserve verification gains while reducing `source_file_as_verification` and `edit_sprawl`

## Downstream Reliary Integration

```bash
reliary init  # future: may run vocab locally and cache under .reliary/
```

Reliary may consume `vocab` later as a local-only adapter surface:

- **Fast**: working-tree scan only, no git history traversal.
- **Lightweight**: cache under `.reliary/`, reused across agent sessions.
- **Privacy**: scan output is structural identifiers and paths — no code content leaves the repo.
- **Not uploaded** by default: `vocab` output stays local unless the user explicitly enables sharing.

For now, `vocab` is intended to stand on its own as the public trust artifact. Reliary server/agent integration should remain thin and local-first.

## LLM Channel

These commands are designed to be injected into LLM system prompts or used as tool calls. They prioritize compression, structure, and multiple-choice over raw data.

```bash
vocab crystallography .             # one-time structural skeleton (~100 tokens)
vocab verify --files src/spool.ts   # multiple-choice verification selection
vocab preflight --files src/spool.ts --format tool   # LLM-tool preflight
```

`crystallography` produces a compact repo-level description with detected test conventions, stable core, generated-file percentages, and module boundaries. The `skeleton` field is designed for LLM system prompt injection (~100 tokens). Full JSON provides structured detail for caching.

`verify` returns verification candidates as a multiple-choice question the LLM can answer by selecting one option. Designed for tool-accessible use where the LLM decides which file to verify.

`preflight --format tool` returns a JSON payload with an explicit `verification_mc` block containing the question, candidates, and max_selections — structured for LLM tool-parsing.

It also includes:

- `verification_confidence`: structural confidence in the candidate set.
- `edit_sprawl_guard`: report-only guidance for questioning extra edits.

All three include metadata fields (`schema_version`, `guardrails.mode`, `local_only`) so the receiving system knows the data is advisory, not authoritative.

## Useful Commands

### Orientation & Onboarding
```bash
vocab inspect .                         # comprehensive overview + health score
vocab explore . --themes                # onboarding map and themes
vocab modules .                         # parser-free module boundaries
vocab agent-bootstrap . --task "..."    # agent orientation with file ranking
vocab help-agent "debug upload"         # natural-language command suggestions
```

### Pre-Edit & Pre-Commit Safeguards
```bash
vocab preflight --files src/spool.ts --task "..."   # file-scoped risk card
vocab preflight --diff origin/main                   # diff-scoped risk card
vocab verify --files src/spool.ts                    # multiple-choice verification
vocab deserts --path .                               # source files with weak test mirrors
vocab route --files src/spool.ts --task "..."         # decide whether/how to use vocab
vocab stop --path . --read file1.ts --read file2.ts  # exploration entropy gauge
```

### CI / PR Tools
```bash
vocab ci-report origin/main HEAD --summary           # blast radius + mirror gap
vocab lattice --path . --base main --head HEAD        # crystallographic defect detection
vocab patterns --path . --base HEAD~1                 # refactoring pattern recognition
vocab pr-report origin/main HEAD                      # consolidated markdown report
```

### History & Evolution
```bash
vocab entropy --path . --weeks 12                    # vocabulary entropy velocity
vocab lifecycle . --weeks 24                         # phrase lifecycle (stable/decaying/etc)
vocab stable .                                       # stable anchors and churn hotspots
vocab provenance "SpoolManager" .                    # phrase history through git
vocab timeline . --weeks 4                           # concept entry/exit timeline
vocab genesis --path .                               # concept origin tracing (endogenous vs imported)
```

### Cross-Repo & Analysis
```bash
vocab compare ../repo-a ../repo-b --contract-only    # contract-surface drift
vocab search SpoolManager ../repo-a ../repo-b        # cross-repo phrase search
vocab crystallography .                              # one-time repo skeleton for LLM prompts
vocab skeleton --path .                              # prompt decompression: ~100-token skip directives
vocab fingerprint .                                  # repo structural identity
vocab bond --path .                                  # concept bond classification (covalent/ionic/metallic)
vocab diff --ref HEAD~10                             # vocabulary changes across git history
vocab landmarks .                                    # characteristic phrases
vocab delta --path .                                 # dead reckoning: structural changes since `vocab init`
```

## Philosophy

`vocab` treats code as a **phrase-file incidence matrix** — a vocabulary lattice that can be diffed, analyzed, and traced without knowing the language. This reframing unlocks capabilities that no existing tool provides:

- **Grammar-free**: Works across languages because it operates on delimiter boundaries and phrase frequencies, not ASTs or parsers.
- **Deterministic**: Same input → same output. No models, no training, no randomness.
- **Strictly structural**: Reports phrase presence, absence, and co-occurrence. Never claims semantic meaning, correctness, or intent.
- **Evidence over taste**: No semantic denylists. Filters are mechanical (generated paths, identifier length, frequency). Users apply judgment.
- **Local-first**: No network, no upload. All computation is git-backed and filesystem-scoped.
- **Report-only**: Every command is advisory. Guardrails are informational, not blocking, unless explicit `--fail-on-*` flags are used.

## Scope

`vocab` is intentionally parser-free. It works across languages and formats because it relies on phrase/file/history matrices, not language-specific ASTs. That makes it useful for orientation, drift, impact, and structure. It also means users and agents must apply judgment before treating any signal as policy.
