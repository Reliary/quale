# vocab

`vocab` is a grammar-free structural codebase analyzer. It scans any language with the same phrase/file/history pipeline, then surfaces structural signals for agents, humans, and CI without ASTs or per-language setup.

It is an orientation and drift tool. It reports evidence; it does not claim semantic truth, coverage proof, dead-code certainty, or security analysis.

## Start Here

```bash
vocab help-agent "fix upload"          # discoverability: which command for which task
vocab preflight --files src/spool.ts --format tool   # primary LLM surface: verifiation + scope control
vocab inspect .                                        # human overview: structure, health, modules
vocab ci-report origin/main HEAD --summary            # CI: blast radius + mirror gap
```

## Workflow 1: Agent Edits Code

Use `preflight --format tool` when editing a known file. It constrains verification targets and prevents scope creep. Proven in harness:

| Condition | Verify hit | Sprawl | Tokens | Efficiency |
|-----------|-----------|--------|--------|------------|
| baseline (no vocab) | 8% | 0.5 | 1K | 0.67 |
| `preflight --format tool` | **75%** | **0.0** | 1.7K | 1.60 |
| `preflight --diff HEAD~1` | **100%** | **0.0** | 1.7K | 1.82 |

```bash
vocab preflight --files src/spool.ts --task "change upload" --format tool   # per-task scope control
vocab preflight --diff HEAD~1 --task "change upload" --format tool           # diff-scoped (best)
```

For initial repo orientation (not per-task), use `crystallography`:

```bash
vocab crystallography --path . --format json   # ~100 token repo skeleton
```

For weak models (or when a step-by-step protocol is needed):

```bash
vocab agent-bootstrap . --task "fix upload" --format checklist
```

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

The strongest measured use is verification scaffolding and scope control. In a 12-repo, 3-trial `deepseek-v4-flash` harness:

| Condition | Verify hit | Sprawl | Tokens | Efficiency |
|-----------|-----------|--------|--------|------------|
| baseline (no vocab) | 8% | 0.5 | 1,060 | 0.50 |
| `preflight --format tool` | **75%** | **0.0** | 1,658 | 1.60 |
| `preflight --diff HEAD~1` | **100%** | **0.0** | 1,748 | 1.82 |
| contract --format tool | TBD (experimental) | 0.25 | ~1,800 | TBD |

The effect is strongest on private/unseen TypeScript/Python-ish repos and weak on weird-language public repos where test discovery is structurally poor. Treat `preflight` as a local review/edit scaffold, not as an oracle.

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

Experimental deterministic contracts:

```bash
vocab contract --files src/spool.ts --task "change upload behavior"
vocab contract --files src/spool.ts --format prompt
vocab check-plan --contract contract.json --proposal proposal.json --format json
```

`contract` converts paths into bounded IDs: `F*` for allowed edits, `T*` for verification choices, and `B*` for boundary/context files. `check-plan` validates the LLM's returned IDs and rejects unknown IDs, raw paths, edits outside the allowed scope, and boundary edits that did not request `expand_scope`. In the first focused harness, contracts eliminated raw-path hallucination and invalid IDs, but did not beat `preflight --format tool` on sprawl; keep this path experimental for now.

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

The harness compares baseline prompts against `candidate_baseline`, `preflight --format tool`, `diff_preflight`, `route_policy`, `verify_scope`, `ask`, `negotiate_simple`, and other conditions across likely-seen public repos, weird-language public repos, and private/unseen repos. It records parse/error rates, verification hits, edit sprawl, and token cost.

Decision rule:

- keep `preflight --format tool` as the primary LLM surface: 75% verify, 0 sprawl
- keep `diff_preflight` for PR/diff workflows: 100% verify, 0 sprawl
- kill `ask`: 0% verify (worse than baseline)
- `contract` path: experimental, needs more harness trials
- do not add automatic prompt injection unless a harness shows a clear behavioral win
- re-run the harness after wording/ranking changes because "helpful-looking" output can still degrade behavior
- mine failure rows after each run; repeated failures become concrete product fixes, not anecdotes

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

These commands are designed to be injected into LLM system prompts or used as tool calls. The primary proven surface is `preflight --format tool` (75% verify, 0 sprawl in harness). Secondary surfaces serve orientation or experimental contract workflows.

```bash
vocab preflight --files src/spool.ts --format tool   # LLM-tool preflight (proven)
vocab crystallography --path .                        # ~100 token repo skeleton (orientation)
vocab preflight --diff HEAD~1 --format tool            # diff-scoped preflight (100% verify)
vocab contract --files src/spool.ts --format tool      # ID-coded scope (experimental)
```

`preflight --format tool` returns a compact 12-field JSON payload designed for LLM tool-parsing. Verified across 24 3-trial harness trials to improve verification selection and eliminate edit sprawl compared to unstructured baseline.

Key fields:

- `verification_mc`: multiple-choice verification candidates, max_selections, question
- `verification_confidence`: structural confidence in candidate set (high/mixed/low)
- `edit_sprawl_guard`: report-only instruction to question extra edits outside the changed file set
- `desert_warning`: present when test mirrors are structurally weak; tells the model not to invent tests

The compact oneline format (`separators=(",", ":")`) saves ~120 tokens over pretty-printed JSON with no measurable accuracy loss.

`contract --format tool` converts paths into bounded IDs (`F*` edit, `T*` verify, `B*` boundary) for hallucination-resistant scope control. Requires paired `check-plan` validation. Experimental — harness shows 0 invalid IDs but slightly higher sprawl than preflight alone.

All payloads include `guardrails.mode: "report_only"`, `guardrails.caveat: "May be wrong; inspect before acting."`, and `schema_version` for downstream handling.

## Useful Commands

### Agent: Scope Control (Proven)
```bash
vocab preflight --files src/spool.ts --task "..." --format tool  # 75% verify, 0 sprawl
vocab preflight --diff HEAD~1 --task "..." --format tool           # 100% verify, 0 sprawl
vocab contract --files src/spool.ts --task "..." --format tool     # ID-coded scope (experimental)
vocab check-plan --contract c.json --proposal p.json               # validate LLM proposal
```

### Agent: Orientation (Secondary)
```bash
vocab crystallography --path . --format json         # ~100 token repo skeleton
vocab agent-bootstrap . --task "..." --format checklist  # weak-model step-by-step
vocab help-agent "debug upload"                     # discoverability: which command?
```

### Human: Overview & Health
```bash
vocab inspect .                         # comprehensive overview + health score
vocab explore . --themes                # onboarding map and themes
vocab modules .                         # parser-free module boundaries
vocab deserts --path .                  # source files with weak test mirrors
vocab stop --path . --read file1.ts     # exploration entropy gauge
```

### Human: Preflight & Guardrails
```bash
vocab preflight --files src/spool.ts --task "..."   # file-scoped risk card (human)
vocab preflight --diff origin/main                   # diff-scoped risk card (human)
vocab verify --files src/spool.ts                    # multiple-choice verification
vocab route --files src/spool.ts --task "..."         # decide whether/how to use vocab
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
