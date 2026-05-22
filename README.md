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

Use `agent-bootstrap` before editing unfamiliar code:

```bash
vocab agent-bootstrap . --task "fix spool upload"
vocab agent-bootstrap . --task "fix spool upload" --verify-relevance --format json
```

With a task, the terminal summary starts with the best task-specific file to read, likely edit files, relevance, architecture context, and module context. Without a task, it starts with the strongest architecture read. JSON output includes:

- `recommended_next_reads`: files an agent should inspect before editing.
- `task_plan`: likely edit files, stable anchors, and sequence guidance.
- `task_relevance_score`: how many suggested files contain task keywords.
- `verified_files` / `unverified_files`: evidence behind the score.

Use `--verify-relevance` as a sanity check. A low score means the task terms were too broad or the tool found weak matches.

## Workflow 2: Human Reviews A PR

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

## Useful Commands

```bash
vocab inspect .                         # comprehensive overview
vocab explore . --themes                # onboarding map and themes
vocab modules .                         # parser-free module boundaries
vocab stable .                          # stable anchors and churn hotspots
vocab compare ../repo-a ../repo-b       # cross-repo alignment and asymmetry
vocab provenance "SpoolManager" .       # phrase history
vocab fingerprint .                     # repo structural identity
vocab search SpoolManager ../repo-a     # cross-repo phrase search
```

## Scope

`vocab` is intentionally parser-free. It works across languages and formats because it relies on phrase/file/history matrices, not language-specific ASTs. That makes it useful for orientation, drift, impact, and structure. It also means users and agents must apply judgment before treating any signal as policy.
