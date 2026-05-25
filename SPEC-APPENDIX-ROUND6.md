# vocab — SPEC Appendix: Round 6 Commands

Six genuinely-new commands not covered by the existing codebase.
All are grammar-free, zero-config, read-only, and evidence-over-taste.

## Commands

| Command | Scope | Mode | Description |
|---------|-------|------|-------------|
| `negentropy` | working tree + deep scan | medium | PMI surprise within co-occurrence clusters — replaces 200-token cluster dumps with ~12 tokens |
| `rogue-wave` | working tree + modules | fast | Module-peer vocabulary outlier detection (z-score) — catches files the LLM underestimates |
| `kleiber` | working tree + modules | fast | Module vocabulary scaling efficiency exponent — does the codebase get more efficient as modules grow? |
| `tensegrity` | working tree | medium | Indirect coupling via ≥3 intermediary files — catches coupling `mycorrhiza` misses |
| `implicature` | working tree | fast | 4 Gricean quality violation flags — maps structural signals to LLM-native quality framework |
| `criticality` | working tree + deep scan | medium | 2-hop cascade amplification ratio k — `blast` is 1-hop; this measures amplification |

---

## negentropy

**What it does**: Computes Pointwise Mutual Information (PMI) for phrase pairs within each co-occurrence cluster. Raw co-occurrence counts treat "always-together" pairs the same as "coincidental" pairs. PMI normalizes for base frequency, revealing which pairs are genuinely surprising.

**Computation**:
1. From `--deep` scan, get co-occurrence clusters (phrase groups).
2. For each cluster, compute binary PMI for each phrase pair:
   ```
   PMI(p1, p2) = log2(P(p1, p2) / (P(p1) × P(p2)))
   ```
   where P(p) = fraction of files containing p, P(p1, p2) = fraction containing both.
3. Clip PMI to max +5 bits (prevents ∞ when P(p1, p2) = P(p1) = P(p2)).
4. Sum all PMI values above threshold = cluster negentropy.
5. Top 3 surprising pairs across all clusters.

**Token reduction**: 1 float (repo negentropy) + top 3 surprising pairs = ~12 tokens. Replaces dumping full co-occurrence clusters (~200 tokens).

**Why it's new**: The co-occurrence matrix exists (scanner.py:141-253) but uses raw counts. No PMI normalization is ever computed. This is the first command to normalize co-occurrence by base frequency.

**CLI**:
```
vocab negentropy --path . --format compact
vocab negentropy --path . --format json
```

**JSON output**:
```json
{
  "schema_version": 1,
  "repo_negentropy": 3.42,
  "clusters_analyzed": 7,
  "surprising_pairs": [
    {
      "phrase_a": "SpoolManager",
      "phrase_b": "SpoolConfig",
      "pmi": 4.8,
      "cluster_id": 2,
      "co_occurrence_ratio": 0.95
    }
  ],
  "note": "PMI clipped to max +5 bits. Co-occurrence from deep scan."
}
```

---

## rogue-wave

**What it does**: Detects files whose vocabulary size is ≥2.5 standard deviations above their module's median. A file can have normal `trompe` ratio (balanced lines-to-identifiers) AND still be a rogue wave (3× the vocabulary of its module peers).

**Computation**:
1. Run module detection (`modules`) to assign files to modules.
2. For each module, compute median and std of vocabulary sizes (unique identifier count per file).
3. For each file, compute z-score = (file_vocab_size - module_median) / module_std.
4. Flag files with z-score ≥ 2.5.

**Token reduction**: 1-3 warnings = ~8 tokens. Replaces reading the file to discover it's a multi-concern monster.

**Why it's new**: `explore` ranks files by unique_score globally. No per-module normalization exists. A file with unique_score 42 might be normal in a 200-identifier module but a rogue wave in a 15-identifier module.

**CLI**:
```
vocab rogue-wave --path . --format compact
vocab rogue-wave --path . --format json
vocab rogue-wave --path . --threshold 2.5 --format compact
```

**JSON output**:
```json
{
  "schema_version": 1,
  "modules_analyzed": 5,
  "rogue_waves": [
    {
      "file": "src/handlers/checkout.ts",
      "module": "handlers",
      "vocab_size": 187,
      "module_median": 42,
      "module_std": 28,
      "z_score": 4.1,
      "concerns": ["billing", "shipping", "tax", "payment"]
    }
  ],
  "note": "Concerns derived from co-occurrence cluster membership."
}
```

---

## kleiber

**What it does**: Measures how vocabulary efficiency scales as modules grow. Kleiber's Law (metabolic rate ∝ mass^0.75) says larger organisms are more efficient per unit mass. Same question for code: do larger modules share more vocabulary per file?

**Computation**:
1. For each module with N files, compute efficiency = shared_vocabulary / total_vocabulary.
2. Plot efficiency against module size (N).
3. Fit power law: efficiency ∝ N^α.
4. α < 1 = sublinear scaling (efficient — modules get better as they grow).
5. α > 1 = superlinear scaling (inefficient — modules get worse as they grow).

**Token reduction**: 1 exponent per codebase = ~4 tokens. Replaces reading every module to understand scaling.

**Why it's new**: `modules` detects boundaries but doesn't measure scaling. `spectral-gap` measures modularity quality but not efficiency scaling. `porosity` is codebase-wide, not per-module.

**CLI**:
```
vocab kleiber --path . --format compact
vocab kleiber --path . --format json
```

**JSON output**:
```json
{
  "schema_version": 1,
  "kleiber_exponent": 0.72,
  "scaling": "sublinear",
  "modules": [
    {
      "name": "handlers",
      "size": 47,
      "efficiency": 0.61,
      "shared_vocab": 120,
      "total_vocab": 197
    }
  ],
  "note": "Requires ≥5 modules of varying sizes. Sublinear = modules get more efficient as they grow."
}
```

---

## tensegrity

**What it does**: Detects files with ZERO direct vocabulary overlap but HIGH indirect coupling through ≥3 intermediary files. Like a tensegrity structure: rigid bars don't touch each other but are held together by a continuous cable network.

**Computation**:
1. For each file pair (A, B) in the same module:
   - Check if A ∩ B = ∅ (no shared identifiers).
   - Find all intermediary files C where (A ∩ C ≠ ∅) AND (C ∩ B ≠ ∅).
   - If |C| ≥ 3, A and B are tensegrity-coupled.
2. Rank by number of independent intermediary paths.

**Token reduction**: 3-5 tensegrity pairs = ~15 tokens. Replaces discovering the coupling only after a change cascades through intermediaries.

**Why it's new**: `mycorrhiza` detects direct hidden dependencies (A and B share rare vocabulary). `entanglement` detects direct co-change. Neither detects INDIRECT coupling through intermediaries. `blast` is 1-hop only.

**CLI**:
```
vocab tensegrity --path . --format compact
vocab tensegrity --path . --file src/types.ts --format compact
vocab tensegrity --path . --format json
```

**JSON output**:
```json
{
  "schema_version": 1,
  "tensegrity_pairs": [
    {
      "file_a": "src/types.ts",
      "file_b": "src/billing.ts",
      "direct_overlap": 0,
      "intermediary_count": 4,
      "intermediaries": [
        "src/handlers/checkout.ts",
        "src/services/payment.ts",
        "src/utils/format.ts",
        "src/models/order.ts"
      ],
      "strength": "strong"
    }
  ],
  "note": "Strength: strong (≥5 intermediaries), moderate (3-4), weak (2)."
}
```

---

## implicature

**What it does**: Maps 4 Gricean maxims to structural code quality flags. Each violation is a binary flag the LLM already understands from its training on communication theory.

**Computation**:
1. **Quantity** (say enough, not too much): File with ≥70 lines but <10 unique identifiers = boilerplate violation. Exclude JSON/CSV/XML/test files.
2. **Quality** (be truthful): File using identifiers from ≥5 different modules = semantic sprawl. Exclude barrel files (re-export ratio > 0.5).
3. **Relation** (be relevant): File that imports from distantly-related modules without sharing vocabulary with them = hidden dependency. Works without git history.
4. **Manner** (be clear): File with inconsistent identifier style (camelCase AND snake_case AND PascalCase, ratio > 70/30). Exclude generated files.

**Token reduction**: 4 binary flags = 4 tokens. Replaces pages of code quality analysis.

**Why it's new**: `health` gives 1 score. `check-diff` finds structural defects. Neither maps to a quality framework the LLM natively understands. The Relation maxim specifically works WITHOUT git history (unlike `mycorrhiza`).

**CLI**:
```
vocab implicature --path . --format compact
vocab implicature --path . --file src/billing.ts --format compact
vocab implicature --path . --format json
```

**JSON output**:
```json
{
  "schema_version": 1,
  "files_analyzed": 258,
  "violations": [
    {
      "file": "src/config.ts",
      "quantity": false,
      "quality": false,
      "relation": true,
      "manner": false,
      "details": {
        "relation": "imports from 3 modules without shared vocabulary: auth, billing, infra"
      }
    }
  ],
  "summary": "1 Relation violation: src/config.ts has hidden cross-module dependency."
}
```

---

## criticality

**What it does**: Computes the 2-hop cascade amplification ratio k. If editing file A causes 3 direct downstream edits and those 3 cause 9 more, k=3. If they cause 0 more, k=0. `blast` measures direct impact (1-hop); `criticality` measures amplification (2-hop).

**Computation**:
1. For each file A:
   - 1-hop: files sharing vocabulary with A (direct blast).
   - 2-hop: files sharing vocabulary with 1-hop files (indirect blast).
   - k = |2-hop| / |1-hop| (amplification ratio).
2. Filter: require each hop to appear in ≥3 distinct commits (reduces noise from one-off batch commits).
3. Classify: k < 0.5 = subcritical (changes dampen), k ≈ 1.0 = critical (changes propagate linearly), k > 1.5 = supercritical (changes amplify).

**Token reduction**: 1 float per file = 1-4 tokens. Replaces pages of blast radius analysis.

**Why it's new**: `blast` is 1-hop only. `forecast` uses bugfix history. No command computes the AMPLIFICATION RATIO across two hops. Two files with same blast radius can have k=0.2 vs k=4.0 — different beast.

**CLI**:
```
vocab criticality --path . --format compact
vocab criticality --path . --file src/types.ts --format compact
vocab criticality --path . --format json
```

**JSON output**:
```json
{
  "schema_version": 1,
  "files_analyzed": 258,
  "criticality_scores": [
    {
      "file": "src/types.ts",
      "k": 3.2,
      "classification": "supercritical",
      "one_hop": 5,
      "two_hop": 16,
      "one_hop_files": ["src/handlers/checkout.ts", "src/services/payment.ts", "..."],
      "two_hop_files": ["src/utils/format.ts", "src/models/order.ts", "..."]
    }
  ],
  "note": "k = two_hop / one_hop. Subcritical < 0.5, critical ≈ 1.0, supercritical > 1.5."
}
```

---

## Cross-command synergies

| Pair | Synergy |
|------|---------|
| `negentropy` + `spectral-gap` | High negentropy + low spectral gap = well-separated modules with deeply entangled internal structure |
| `rogue-wave` + `criticality` | A rogue wave that's also supercritical = the file is both a multi-concern monster AND an amplification hub |
| `kleiber` + `modules` | Kleiber exponent tells you whether module detection is finding natural boundaries (sublinear) or artificial splits (superlinear) |
| `tensegrity` + `mycorrhiza` | Tensegrity catches indirect coupling; mycorrhiza catches direct hidden deps. Together: complete coupling map |
| `implicature` + `health` | Health score = 1 number. Implicature = 4 diagnostic dimensions. Health tells you "how sick"; implicature tells you "what's wrong" |
| `criticality` + `blast` | Blast = "what files are affected." Criticality = "how much will it cascade." Together: complete impact assessment |

## Implementation notes

- `negentropy` requires `--deep` scan (co-occurrence matrix). Falls back gracefully if not available.
- `rogue-wave` requires module detection. Falls back to codebase-wide outlier detection if no modules.
- `kleiber` requires ≥5 modules of varying sizes. Skips if insufficient data.
- `tensegrity` is O(files² × intermediaries) per module. For 30-file modules: ~27K checks. Fast with set operations.
- `implicature` is O(files) — single pass over file vocabularies. No history needed.
- `criticality` is O(files × avg_blast²). For repos with avg blast ≤ 20: tractable.
