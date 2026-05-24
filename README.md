# vocab

`vocab` is a grammar-free structural codebase analyzer. It scans any language with the same phrase/file/history pipeline, then surfaces structural signals for agents, humans, and CI without ASTs or per-language setup.

It is an orientation and drift tool. It reports evidence; it does not claim semantic truth, coverage proof, dead-code certainty, or security analysis.

## Start Here

```bash
vocab help-agent "fix upload"          # discoverability: which command for which task
vocab edit-context --files src/spool.ts --format tool   # primary LLM surface: verification + scope control
vocab inspect .                                        # human overview: structure, health, modules
vocab ci-report origin/main HEAD --summary            # CI: blast radius + mirror gap
```

## Workflow 1: Agent Edits Code

Use `edit-context --format tool` when editing a known file. It constrains verification targets and prevents scope creep. The strongest measured surface is `verify_scope` — a stripped-down verification-only variant — at 83% verify hit with zero sprawl.

```bash
vocab edit-context --files src/spool.ts --task "change upload" --format tool   # per-task scope control
vocab edit-context --diff HEAD~1 --task "change upload" --format tool           # diff-scoped
```

Harness results (12-repo, 3-trial, `deepseek-v4-flash`):

| Condition | Verify hit | Sprawl | Tokens | Efficiency |
|-----------|-----------|--------|--------|------------|
| baseline (no vocab) | 17% | 0.5 | 1,060 | 0.83 |
| `edit-context --format tool` (full) | **75%** | **0.0** | 1,658 | 1.60 |
| `diff_edit-context` (diff-scoped) | **75%** | **0.0** | 1,621 | 1.63 |
| `verify_scope` (verification-only) | **83%** | **0.0** | 1,233 | **2.29** |

`verify_scope` (available as `--format tool` on edit-context) strips down to verification candidates and confidence only — no edit decisions. Removing the edit decision reduces cognitive load and improves verification accuracy. Use it when you only need test-file selection.

For initial repo orientation (not per-task), use `repo-map`:

```bash
vocab repo-map --path . --format json   # ~100 token repo skeleton
```

For weak models (or when a step-by-step protocol is needed):

```bash
vocab agent-bootstrap . --task "fix upload" --format checklist
```

Do not treat `agent-bootstrap` as proof that a strong model will find files better. In a 12-repo `deepseek-v4-flash` harness, task-only bootstrap guidance added tokens and did not improve file discovery over a baseline that already had filenames. Its safer role is orientation for humans, smaller models, and unfamiliar repos.

## Workflow 2: Human Reviews A PR

Use `edit-context` when you already know the file or diff being edited:

```bash
vocab edit-context --files src/spool.ts --task "change upload behavior"
vocab edit-context --files src/spool.ts --format tool
vocab edit-context --diff origin/main --format checklist
vocab edit-context --diff HEAD~1 --format json
```

`edit-context` is intentionally file-scoped. It reports capped structural evidence: changed files, read-first context, verification candidates, stable anchors, reverse blast, risk, confidence, and a local-only privacy receipt. It does not claim semantic correctness.

The strongest measured uses are verification selection and scope control:

| Condition | Verify hit | Sprawl | Tokens | Efficiency |
|-----------|-----------|--------|--------|------------|
| baseline (no vocab) | 17% | 0.5 | 1,060 | 0.83 |
| `edit-context --format tool` (oneline) | **75%** | **0.0** | 1,658 | 1.60 |
| `diff_edit-context` (diff-scoped) | **75%** | **0.0** | 1,621 | 1.63 |
| `verify_scope` (verification-only) | **83%** | **0.0** | 1,233 | **2.29** |
| contract --format tool | TBD (experimental) | 0.25 | ~1,800 | TBD |

The effect is strongest on private/unseen TypeScript/Python-ish repos and weak on weird-language public repos where test discovery is structurally poor. Treat `edit-context` as a local review/edit scaffold, not as an oracle.

Preflight guardrails:

- `VERIFY CANDIDATES` means candidate files to inspect, not guaranteed tests.
- `EXPANSION RISK` means inspect before broadening scope, not “never edit.”
- `verification_confidence` explains whether structural test mirrors are strong, mixed, or weak.
- `edit_sprawl_guard` asks agents to question extra edits outside the requested file set.
- output is report-only and must not be used as an automatic prompt-injection or blocking policy.
- every edit-context JSON payload includes `guardrails.mode = "report_only"` and `guardrails.caveat = "May be wrong; inspect before acting."`

If you are not sure whether `vocab` should be used, ask the router:

```bash
vocab route --task "fix upload"                         # often says no_vocab / skeleton only
vocab route --files src/spool.ts --task "fix upload"     # routes to edit-context_tool
```

The router encodes measured behavior: task-only bootstrap can hurt strong-model discovery, while file-scoped edit-context reduces sprawl and helps verification.

LLM UI rule of thumb:

- unknown files + vague task: **do not add vocab**; search normally
- unknown files + scoped task: use `vocab skeleton` only
- known files or diff: use `vocab edit-context --format tool`
- high-risk edit: include the `edit_sprawl_guard` and verification confidence fields

Experimental deterministic contracts:

```bash
vocab contract --files src/spool.ts --task "change upload behavior"
vocab contract --files src/spool.ts --format prompt
vocab check-plan --contract contract.json --proposal proposal.json --format json
```

`contract` converts paths into bounded IDs: `F*` for allowed edits, `T*` for verification choices, and `B*` for boundary/context files. `check-plan` validates the LLM's returned IDs and rejects unknown IDs, raw paths, edits outside the allowed scope, and boundary edits that did not request `expand_scope`. In the first focused harness, contracts eliminated raw-path hallucination and invalid IDs, but did not beat `edit-context --format tool` on sprawl; keep this path experimental for now.

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
python scripts/evaluate_vocab_effect.py --suite edit-context --trials 3
python scripts/analyze_effect_failures.py /tmp/vocab-effect-edit-context-3trial.json
```

The harness compares baseline prompts against `candidate_baseline`, `edit-context --format tool`, `diff_edit-context`, `route_policy`, `verify_scope`, `ask`, `negotiate_simple`, and other conditions across likely-seen public repos, weird-language public repos, and private/unseen repos. It records parse/error rates, verification hits, edit sprawl, and token cost.

Decision rule:

- keep `edit-context --format tool` as the primary LLM surface: 75% verify, 0 sprawl
- keep `diff_edit-context` for PR/diff workflows: 100% verify, 0 sprawl
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

These commands are designed to be injected into LLM system prompts or used as tool calls. The primary proven surface is `verify_scope` (83% verify, 0 sprawl — the verification-only subset of edit-context). The full `edit-context --format tool` (75% verify, 0 sprawl) adds edit-scope guardrails.

```bash
vocab edit-context --files src/spool.ts --format tool   # LLM-tool edit-context (proven)
vocab repo-map --path .                        # ~100 token repo skeleton (orientation)
vocab edit-context --diff HEAD~1 --format tool            # diff-scoped edit-context (75% verify)
vocab contract --files src/spool.ts --format tool      # ID-coded scope (experimental)
```

`edit-context --format tool` returns a compact 12-field JSON payload designed for LLM tool-parsing. The verification-only subset (extracting `verification_mc` and `verification_confidence` fields) achieves the highest measured efficiency (2.29) by removing the edit-decision overhead.

Key fields:

- `verification_mc`: multiple-choice verification candidates, max_selections, question
- `verification_confidence`: structural confidence in candidate set (high/mixed/low)
- `edit_sprawl_guard`: report-only instruction to question extra edits outside the changed file set
- `desert_warning`: present when test mirrors are structurally weak; tells the model not to invent tests

The compact oneline format (`separators=(",", ":")`) saves ~120 tokens over pretty-printed JSON with no measurable accuracy loss.

`contract --format tool` converts paths into bounded IDs (`F*` edit, `T*` verify, `B*` boundary) for hallucination-resistant scope control. Requires paired `check-plan` validation. Experimental — harness shows 0 invalid IDs but slightly higher sprawl than edit-context alone.

All payloads include `guardrails.mode: "report_only"`, `guardrails.caveat: "May be wrong; inspect before acting."`, and `schema_version` for downstream handling.

## Useful Commands

### Agent: Scope Control (Proven)
```bash
vocab edit-context --files src/spool.ts --task "..." --format tool  # 75% verify, 0 sprawl
vocab edit-context --diff HEAD~1 --task "..." --format tool           # 75% verify (diff-scoped)
vocab edit-context --files src/spool.ts --task "..." --format verify  # 83% verify (verification-only)
vocab contract --files src/spool.ts --task "..." --format tool     # ID-coded scope (experimental)
vocab check-plan --contract c.json --proposal p.json               # validate LLM proposal
```

### Agent: Orientation (Secondary)
```bash
vocab repo-map --path . --format json         # ~100 token repo skeleton
vocab agent-bootstrap . --task "..." --format checklist  # weak-model step-by-step
vocab help-agent "debug upload"                     # discoverability: which command?
```

### Human: Overview & Health
```bash
vocab inspect .                         # comprehensive overview + health score
vocab explore . --themes                # onboarding map and themes
vocab modules .                         # parser-free module boundaries
vocab test-gaps --path .                  # source files with weak test mirrors
    vocab stop --path . --read file1.ts     # exploration entropy gauge
```

### Human: Preflight & Guardrails
```bash
vocab edit-context --files src/spool.ts --task "..."   # file-scoped risk card (human)
vocab edit-context --diff origin/main                   # diff-scoped risk card (human)
vocab verify --files src/spool.ts                    # multiple-choice verification
vocab route --files src/spool.ts --task "..."         # decide whether/how to use vocab
```

### CI / PR Tools
```bash
vocab ci-report origin/main HEAD --summary           # blast radius + mirror gap
vocab anomalies --path . --base main --head HEAD        # crystallographic defect detection
vocab patterns --path . --base HEAD~1                 # refactoring pattern recognition
vocab pr-report origin/main HEAD                      # consolidated markdown report
```

### History & Evolution
```bash
vocab vocabulary-trend --path . --weeks 12                    # vocabulary vocabulary-trend velocity
vocab lifecycle . --weeks 24                         # phrase lifecycle (stable/decaying/etc)
vocab stable .                                       # stable anchors and churn hotspots
vocab provenance "SpoolManager" .                    # phrase history through git
vocab timeline . --weeks 4                           # concept entry/exit timeline
vocab origins --path .                               # concept origin tracing (endogenous vs imported)
```

### Cross-Repo & Analysis
```bash
vocab compare ../repo-a ../repo-b --contract-only    # contract-surface drift
vocab search SpoolManager ../repo-a ../repo-b        # cross-repo phrase search
vocab skeleton --path .                              # prompt decompression: ~100-token skip directives
vocab fingerprint .                                  # repo structural identity
vocab coupling --path .                              # concept coupling classification (tight/loose/independent)
vocab diff --ref HEAD~10                             # vocabulary changes across git history
vocab landmarks .                                    # characteristic phrases
vocab delta --path .                                 # dead reckoning: structural changes since `vocab init`
vocab fold --file src/billing.ts --task "fix proration"  # fractional distillation: fold irrelevant code blocks (~40-80% reduction)
vocab drift-check --file src/billing.ts --snapshot        # take structural baseline for drift monitoring
vocab drift-check --file src/billing.ts                   # detect velocity spikes (>30% vocabulary turnover)
vocab forecast --files src/billing.ts                     # Doppler radar: forecast regression risk from bugfix history
vocab mycorrhiza --files src/billing.ts                   # hidden structural dependencies (no imports)
vocab isolate --task "Update billing" --format json       # structural module bisection (85% edit-in-top-3)
```

## Measured Effect (LLM Agent Harness)

Results from ~1,100 trials across 10 private and public repos using `deepseek-v4-flash`.

### Preflight (verification scope control)

| Condition | Verify | Sprawl | Tokens | Efficiency |
|-----------|--------|--------|--------|------------|
| baseline (no vocab) | 10-20% | 0.40-0.65 | ~1,200 | 0.55-0.90 |
| `progressive_verify` | 65% | **0.0** | ~1,280 | 2.27 |
| `veto_cascade` | 71% | **0.0** | ~1,225 | 2.38 |
| `multi_turn_progressive` | 60% | **0.0** | **~88** | **31.52** |
| `hybrid_progressive` | **70%** | **0.0** | ~650 | ~10-15 |
| `verify_entangle` | 80% | **0.0** | ~1,300 | **2.87** |

Best efficiency: `multi_turn_progressive` — 93% token reduction at 28-31× efficiency.
Best verify rate: `verify_entangle` at 80% (includes git co-change signal).
Best all-round: `hybrid_progressive` — 70% verify, 0 sprawl, ~650 tokens avg.

### Discovery (edit file identification)

| Condition | Edit-in-top-3 | Tokens |
|-----------|--------------|--------|
| baseline (grep + guess) | 80% | ~1,200 |
| `isolate` | **85%** | **~100** |

Module-based structural bisection matches baseline accuracy at ~1/12 the token cost.

### OpenCode (full tool-access agent)

| Condition | Verify | Sprawl | Tokens | Efficiency |
|-----------|--------|--------|--------|------------|
| baseline | 46% | 0.31 | ~45,800 | 0.93 |
| `fragment_route` | **100%** | **0.0** | ~41,800 | **3.29** |

Adaptive router selects the best condition per repo using fragment matrix history.

### Key takeaways

- **Sprawl is the most consistent win**: every vocab condition eliminated agent wandering (0.0 sprawl) across all repos.
- **Structured JSON beats prose**: compact oneline JSON doubles verify rate vs narrative guidance.
- **Git co-change bridges vocabulary gaps**: `verify_entangle` fixes the `__init__.py` → `test_*.py` class of failure that pure vocabulary analysis misses.
- **Multi-turn YES/NO is most efficient**: 93% fewer tokens at 60% verify. Escalate to full context on reject for 70% at ~650 tok.
- **Contract system eliminates path hallucination**: 0 invalid IDs, 0 raw paths across all measured conditions.
- **`vocab forecast` detects hidden regression risk**: scanning bugfix history reveals files that historians regress together despite zero imports — 71% probability on vocab's own reports.py/evaluate_vocab_effect.py pair.
- **`vocab mycorrhiza` catches coupling no linter can**: structural dependencies (shared rare vocabulary + co-change) between files with no declared imports.
- **`vocab drift-check` monitors structural decay**: per-file vocabulary velocity tracking. Alerts on >30% turnover. Zero token cost, background-runnable.
- **`vocab fold` reduces context on large files**: indentation-aware block folding by task relevance. 18% reduction on unrelated task. Structural lines protected.
- **The 17% boundary**: repos without stem-matched tests or co-change history (opencode-index, llama-cpp) remain structurally ambiguous across all conditions. Vocab documents this honestly rather than hallucinating.

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
