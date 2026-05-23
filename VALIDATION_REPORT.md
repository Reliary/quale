# vocab usefulness validation

Date: 2026-05-22

## Summary

`vocab` is useful as a zero-config structural onboarding tool for agents. The strongest validated surface is `agent-bootstrap`; `ci-report` is useful for PR triage; `inspect` is useful but has noisier recommendations on test/script-heavy repos.

## Agent bootstrap

Four real tasks were tested across the agent, server, compressor, and vocab repos.

| Metric | Result |
| --- | ---: |
| Actual edit file in top 1 | 3/4 |
| Actual edit file in top 3 | 4/4 |
| Useful test/verification hint | 3/4 |
| Beat or matched keyword filename baseline | 4/4 |

Notable results:

- Agent typed-evidence task ranked `packages/core/src/typed-evidence.ts` first and surfaced `packages/core/tests/typed-evidence.test.ts`.
- Server ingest typed-evidence task ranked `internal/handlers/ingest.go` second and surfaced `internal/handlers/ingest_test.go`.
- Compressor CRISPR cache task ranked `app/services/session_cache.py` and `app/compression/crispr_v2_backend.py` first/second, but missed the expected CRISPR v2 test hint.
- Vocab CI-gate task ranked `vocab/cli.py`, `vocab/formats/terminal.py`, `vocab/scanner.py`, and `tests/test_cli.py`.

Verdict: pass. This directly addresses the agent pain point: find the likely edit surface and verification files quickly in an unfamiliar repo.

## CI report

Recent `HEAD~1..HEAD` diffs were tested across four repos.

Useful signals:

- `max_blast_tier` matched review intuition for broad/core changes.
- `stable_touched_count` is a useful review flag when nonzero.
- `risk_flags` are readable and actionable as warnings.

Weak signals:

- `mirror_gap_ratio` was low in all tested repos, including cases with tests. It should remain informational, not a default hard gate.
- Blast tier should be treated as review triage, not proof of breakage.

Verdict: useful for PR triage and non-blocking CI comments. Hard gates should remain opt-in.

## Inspect

Four repos were tested.

| Repo | Runtime | Useful top-5 reads |
| --- | ---: | ---: |
| vocab | 0.21s | yes |
| autopsylab-agent | 3.42s | yes |
| autopsylab | 24.56s | yes |
| llm-semantic-transport | 6.50s | no |

`inspect` top files are useful in 3/4 repos. The compressor repo is dominated by tests/scripts because those files contain dense distinctive identifiers. Binding concepts are also sometimes generic (`String`, `Exception`, `ValueError`) and should be interpreted as structural hints, not architecture truth.

Verdict: useful as an explicit onboarding command, but weaker than `agent-bootstrap` and not suitable as the default automatic scan.

## Product conclusion

Useful positioning:

> A zero-config structural map for agents entering unfamiliar codebases.

Avoid positioning as:

> A correctness, coverage, dead-code, security, or architecture-intent analyzer.

## Next recommendation

Keep the current direction. Do not add more commands yet.

Highest-value next fixes:

1. Make `inspect` source-first like `agent-bootstrap` so tests/scripts do not dominate onboarding reads.
2. Down-rank generic binding concepts using structural information score more aggressively, while keeping evidence visible.
3. Keep `mirror_gap_ratio` informational by default.
4. Use `agent-bootstrap` as the primary Reliary init adapter surface; keep full `inspect` explicit or background-cached.
