"""Codebase scanner — orchestrates PS + VB + IE + co-occurrence across files."""

from __future__ import annotations

import os
import sys
import re
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass, field
import time

from vocab.segmenter import segment
from vocab.vocabulary import build_vocabulary, Vocabulary
from vocab.index import structural_similarity
from vocab.analyze import FileVocab, CoOccurrenceMatrix, classify_language, compute_uniqueness
from vocab.concepts import extract_concepts, cluster_labels, ConceptGroup
from vocab import git as vgit


@dataclass
class CodebaseAnalysis:
    path: str
    total_files: int
    total_phrases: int
    total_unique_phrases: int
    languages: dict[str, int]
    phrases_by_language: dict[str, int]
    shared_across_languages: int
    top_phrases: list[tuple[str, int]]
    concept_groups: ConceptGroup
    file_vocabs: list[FileVocab]
    co_occurrence: CoOccurrenceMatrix
    clusters: list[list[str]]
    cluster_labels: list[str]
    dead_exports: list[dict]
    structural_clones: list[dict]
    landmarks: list[dict]
    structure_clusters: list[dict] = None


_BINARY_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".gz", ".tar", ".xz", ".bz2", ".zst",
    ".o", ".a", ".so", ".dylib", ".dll", ".exe",
    ".pyc", ".pyo", ".pyd", ".class", ".jar",
    ".pdf", ".mp3", ".mp4", ".avi", ".mov", ".webm",
    ".ico", ".icns", ".webp", ".avif",
    ".db", ".sqlite", ".sqlite3",
    ".sum", ".sig", ".asc",
})

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", ".venv", "venv",
    "__pycache__", ".tox", ".eggs", "eggs",
    "target", "build", "dist", "out", ".next",
    ".terraform", ".serverless",
    ".nyc_output", "coverage",
    ".gitlab", ".github",
})


def _is_binary(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _BINARY_EXTS:
        return True
    base = os.path.basename(path).lower()
    if base.endswith(".min.js") or base.endswith(".min.css"):
        return True
    return False


def scan_codebase(path: str, git_ref: str | None = None, quiet: bool = False,
                  clones: bool = False, deep: bool = False) -> CodebaseAnalysis:
    path = os.path.abspath(path)
    if git_ref is not None:
        # Ref specified: must be a git repo
        files = vgit.list_files(path, ref=git_ref)
    elif vgit.is_repo(path):
        files = vgit.list_files(path, ref=None)
    else:
        files = []
        for root, dirs, fnames in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for fname in fnames:
                rel = os.path.relpath(os.path.join(root, fname), path)
                files.append(rel)

    lang_counts: dict[str, int] = defaultdict(int)
    lang_phrases: dict[str, int] = defaultdict(int)
    all_file_vocabs: list[FileVocab] = []
    co_matrix = CoOccurrenceMatrix() if deep else None
    all_phrase_counter: Counter[str] = Counter()

    start = time.time()
    total = len(files)
    skipped_binary = 0
    skipped_empty = 0

    for idx, rel_file in enumerate(files):
        if _is_binary(rel_file):
            skipped_binary += 1
            continue

        if not quiet and total > 200 and (idx + 1) % max(1, total // 20) == 0:
            pct = (idx + 1) / total * 100
            elapsed = time.time() - start
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            eta = (total - idx - 1) / rate if rate > 0 else 0
            print(f"  [{pct:3.0f}%] {idx+1}/{total} files ({rate:.0f}/s, {eta:.0f}s remaining)",
                  file=sys.stderr)

        lang = classify_language(rel_file)

        content = vgit.read_file_at_ref(path, rel_file, ref=git_ref)
        if content is None:
            continue

        try:
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
        except Exception:
            pass
        # Quick binary check on content
        if "\x00" in content[:4096]:
            skipped_binary += 1
            continue

        # Skip huge files (500KB max for scan)
        if len(content) > 500 * 1024:
            continue

        seg_result = segment(content)
        if not seg_result.phrases:
            skipped_empty += 1
            continue

        phrases = [p for p in seg_result.phrases if len(p) >= 2]
        if not phrases:
            skipped_empty += 1
            continue

        vocab = build_vocabulary(phrases, seg_result.strategy, seg_result.delimiter)
        if vocab.size == 0:
            skipped_empty += 1
            continue

        phrase_freq: dict[str, int] = {}
        for entry in vocab.entries:
            phrase_freq[entry.text] = entry.frequency
            all_phrase_counter[entry.text] += entry.frequency

        file_vocab = FileVocab(
            path=rel_file,
            vocabulary=phrase_freq,
            language=lang,
            total_phrases=len(phrases),
        )
        all_file_vocabs.append(file_vocab)
        if co_matrix is not None:
            co_matrix.add_file(set(phrase_freq.keys()))
        lang_counts[lang] += 1
        lang_phrases[lang] += len(phrases)

    if not quiet:
        elapsed = time.time() - start
        print(f"  Scanned {len(all_file_vocabs)} files in {elapsed:.1f}s "
              f"(skipped {skipped_binary} binary, {skipped_empty} empty)",
              file=sys.stderr)

    top_phrases = all_phrase_counter.most_common(200)
    total_phrases = sum(fv.total_phrases for fv in all_file_vocabs)
    total_unique = len(all_phrase_counter)

    # Extract categorized concepts — scan deep enough to find real identifiers
    concept_groups = extract_concepts(all_phrase_counter)

    # Shared across languages
    lang_sets: dict[str, set[str]] = defaultdict(set)
    for fv in all_file_vocabs:
        lang_sets[fv.language].update(fv.vocabulary.keys())
    all_langs = list(lang_sets.keys())
    shared = set()
    if len(all_langs) >= 2:
        first_set = lang_sets[all_langs[0]]
        for lang in all_langs[1:]:
            shared |= first_set & lang_sets[lang]

    if co_matrix is not None:
        if not quiet:
            print(f"  Co-occurrence matrix ({min(len(all_file_vocabs), 500)} files)...", file=sys.stderr)
        if len(all_file_vocabs) > 500:
            lang_groups: dict[str, list[FileVocab]] = defaultdict(list)
            for fv in all_file_vocabs:
                lang_groups[fv.language].append(fv)
            subset = []
            for fvs in lang_groups.values():
                subset.extend(fvs[:max(1, 500 // len(lang_groups))])
            for fv in subset[:500]:
                co_matrix.add_file(set(fv.vocabulary.keys()))
        clusters = co_matrix.cluster(min_cooccurrence=max(1, len(all_file_vocabs) // 50))
        cluster_labels_list = [cluster_labels(c) for c in clusters]
        structure_clusters_list = find_structure_clusters(all_file_vocabs, clusters, quiet=quiet)
    else:
        clusters = []
        cluster_labels_list = []
        structure_clusters_list = []

    # Structural clones — opt-in (O(N²) pairwise). Off by default for speed.
    clone_groups = _find_structural_clones(all_file_vocabs, max_files=min(100, len(all_file_vocabs))) if clones else []

    if not quiet:
        print(f"  Computing landmarks...", file=sys.stderr)
    landmarks = _compute_landmarks(all_file_vocabs) if deep else []

    # Dead exports — sample-based for large repos
    dead_exports = _find_dead_exports(all_file_vocabs)

    return CodebaseAnalysis(
        path=path,
        total_files=len(all_file_vocabs),
        total_phrases=total_phrases,
        total_unique_phrases=total_unique,
        languages=dict(lang_counts),
        phrases_by_language=dict(lang_phrases),
        shared_across_languages=len(shared),
        top_phrases=top_phrases,
        concept_groups=concept_groups,
        file_vocabs=all_file_vocabs,
        co_occurrence=co_matrix,
        clusters=clusters,
        cluster_labels=cluster_labels_list,
        dead_exports=dead_exports,
        structural_clones=clone_groups,
        landmarks=landmarks,
        structure_clusters=structure_clusters_list,
    )


def _find_structural_clones(file_vocabs: list[FileVocab],
                            threshold: float = 0.85,
                            max_files: int = 200) -> list[dict]:
    if len(file_vocabs) < 2:
        return []

    # Sample down if too big
    sampled = file_vocabs
    if len(file_vocabs) > max_files:
        # Take a diverse sample: first file from each language group
        seen_langs: dict[str, list[FileVocab]] = defaultdict(list)
        for fv in file_vocabs:
            seen_langs[fv.language].append(fv)
        sampled = []
        for lang, fvs in sorted(seen_langs.items(), key=lambda x: -len(x[1])):
            sampled.extend(fvs[:max_files // len(seen_langs)])
        sampled = sampled[:max_files]

    clones = []
    seen = set()
    for i, fv_a in enumerate(sampled):
        if fv_a.path in seen:
            continue
        indices_a = list(fv_a.vocabulary.values())
        group = [fv_a.path]
        for j, fv_b in enumerate(sampled):
            if i >= j or fv_b.path in seen:
                continue
            try:
                indices_b = list(fv_b.vocabulary.values())
                sim = structural_similarity(indices_a, indices_b)
                if sim >= threshold:
                    group.append(fv_b.path)
                    seen.add(fv_b.path)
            except Exception:
                continue
        if len(group) >= 2:
            clones.append({
                "files": group,
                "similarity": round(sim, 3),
                "languages": list({classify_language(f) for f in group}),
                "size": len(group),
            })
        seen.add(fv_a.path)
    return clones


def _compute_landmarks(file_vocabs: list[FileVocab]) -> list[dict]:
    """Efficient landmark computation using inverted index (O(F*P) not O(F²*P))."""
    phrase_file_count: Counter[str] = Counter()
    file_phrases: list[tuple[str, set[str]]] = []
    for fv in file_vocabs:
        phrases = set(fv.vocabulary.keys())
        file_phrases.append((fv.path, phrases))
        for p in phrases:
            phrase_file_count[p] += 1

    total_files = len(file_vocabs) or 1
    landmarks = []
    for fv, (path, phrases) in zip(file_vocabs, file_phrases):
        if not phrases:
            continue
        unique_phrases = [p for p in phrases if phrase_file_count[p] == 1]
        uniqueness = len(unique_phrases) / len(phrases) if phrases else 0
        if uniqueness > 0.7:
            landmarks.append({
                "path": path,
                "uniqueness": round(uniqueness, 3),
                "unique_phrases": sorted(unique_phrases, key=lambda x: -len(x))[:5],
                "language": fv.language,
            })
    landmarks.sort(key=lambda x: -x["uniqueness"])
    return landmarks


def _find_dead_exports(file_vocabs: list[FileVocab]) -> list[dict]:
    """Find exported identifiers appearing in exactly 1 file.
    
    Extracts identifier tokens from within phrases (since the segmenter
    produces line-level phrases — not token-level). This catches
    cross-file references like `SanitizationPolicyRelaxed` embedded in
    expressions like `case SanitizationPolicyRelaxed:` or
    `return &SanitizationPolicyRelaxed{}`.
    
    Limitation: reflection-based usage cannot be detected.
    """
    phrase_files: dict[str, str] = {}
    single_file: dict[str, str] = {}
    
    # Find exported-format identifiers within any phrase
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')

    for fv in file_vocabs:
        # Exclude lock files, generated code, non-code files, and test files
        ext = os.path.splitext(fv.path)[1].lower()
        if _is_lock_file(fv.path) or _is_generated(fv.path) or ext not in _DEAD_CODE_EXTS:
            continue
        if "/tests/" in fv.path or "/testdata/" in fv.path or fv.path.endswith("_test.go") or ".test.ts" in fv.path:
            continue
        if "/vendor/" in fv.path or fv.path.startswith("vendor/") or "/third_party/" in fv.path or "/third-party/" in fv.path:
            continue
        seen_in_file: set[str] = set()
        for phrase in fv.vocabulary:
            for m in _EXPORT_TOKEN.finditer(phrase):
                token = m.group()
                if token in seen_in_file:
                    continue
                seen_in_file.add(token)
                if token in phrase_files:
                    single_file.pop(token, None)
                else:
                    phrase_files[token] = fv.path
                    single_file[token] = fv.path

    dead = []
    for phrase, filepath in sorted(single_file.items(), key=lambda x: -len(x[0])):
        dead.append({"phrase": phrase, "file": filepath})
    return dead[:50]


# ── Language-common stop-phrases for structural clustering ──
# Computed per-run from most common phrases in each language
_LANG_COMMON_CACHE: dict[str, set[str]] = {}


def _get_language_common(file_vocabs: list[FileVocab], top_n: int = 20) -> dict[str, set[str]]:
    """Compute top N most common phrases per language to subtract from clustering."""
    if _LANG_COMMON_CACHE:
        return _LANG_COMMON_CACHE
    lang_phrase_count: dict[str, Counter[str]] = defaultdict(Counter)
    for fv in file_vocabs:
        for p in fv.vocabulary:
            lang_phrase_count[fv.language][p] += 1
    result = {}
    for lang, counter in lang_phrase_count.items():
        result[lang] = set(p for p, _ in counter.most_common(top_n))
    _LANG_COMMON_CACHE.update(result)
    return result


def find_structure_clusters(file_vocabs: list[FileVocab], phrase_clusters: list[list[str]],
                            min_file_count: int = 3, quiet: bool = False) -> list[dict]:
    """Convert phrase co-occurrence clusters into file structure groups.

    For each phrase cluster, find files that contain ≥2 unique characteristing
    phrases from that cluster (excluding language-common noise). Groups files
    by shared vocabulary → discovers architectural patterns without parsers.
    """
    if not phrase_clusters or len(file_vocabs) < min_file_count:
        return []

    # Precompute language-common phrases to subtract
    lang_common = _get_language_common(file_vocabs, top_n=20)

    structure_groups = []

    for i, cluster_phrases in enumerate(phrase_clusters):
        if len(cluster_phrases) < 2:
            continue

        # Score each file by how many distinct cluster phrases it contains
        file_scores: list[tuple[str, int, str]] = []
        for fv in file_vocabs:
            # Filter out language-common noise and short noise phrases
            common = lang_common.get(fv.language, set())
            meaningful = [p for p in cluster_phrases
                          if p not in common and len(p) >= 3 and p not in common]
            if len(meaningful) < 2:
                # If filtering removed too many, use original set
                meaningful = cluster_phrases

            matches = sum(1 for p in meaningful if p in fv.vocabulary)
            if matches >= 2:
                file_scores.append((fv.path, matches, fv.language))

        if len(file_scores) >= min_file_count:
            # Determine group label
            langs = Counter(l for _, _, l in file_scores)
            top_lang = langs.most_common(1)[0][0]
            test_count = sum(1 for f, _, _ in file_scores
                             if "_test." in f or "/tests/" in f or ".test." in f)

            # Characteristic phrases for labeling
            all_hit_phrases: Counter[str] = Counter()
            for fv in file_vocabs:
                if any(fv.path == f for f, _, _ in file_scores):
                    for p in cluster_phrases:
                        if p in fv.vocabulary and p not in lang_common.get(fv.language, set()):
                            all_hit_phrases[p] += fv.vocabulary[p]

            char_phrases = [p for p, _ in all_hit_phrases.most_common(5)]

            structure_groups.append({
                "cluster_id": i,
                "label": f"{top_lang} {'test' if test_count / max(len(file_scores), 1) > 0.5 else 'source'} group",
                "file_count": len(file_scores),
                "top_files": sorted(f for f, _, _ in file_scores)[:10],
                "characteristic_phrases": char_phrases,
                "languages": dict(langs.most_common(3)),
                "test_ratio": round(test_count / max(len(file_scores), 1), 2),
            })

    # Sort by file_count descending
    structure_groups.sort(key=lambda x: -x["file_count"])
    return structure_groups


def compute_lifecycles(path: str, weeks: int = 24) -> list[dict]:
    """Track each exported concept over git history and classify lifecycle phase.

    Returns lifecycle signals per concept: GROWING, STABLE, DECAYING, DEAD,
    SEASONAL, ABANDONED, SPORADIC, EMERGING.
    """
    from collections import defaultdict

    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    # Track: concept → set of weeks present
    concept_weeks: dict[str, set[int]] = defaultdict(set)
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')

    previous_phrases: set[str] = set()
    rename_pairs: list[tuple[str, str, int]] = []  # (old, new, week_index)

    for week_idx, wk in enumerate(week_data):
        shas = wk.get("shas", [])
        if not shas:
            continue
        try:
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True)
        except Exception:
            continue

        current_phrases: set[str] = set()
        for fv in analysis.file_vocabs:
            # Only look at code files
            ext = os.path.splitext(fv.path)[1].lower()
            if ext not in _DEAD_CODE_EXTS:
                continue
            if _is_lock_file(fv.path) or _is_generated(fv.path):
                continue
            for phrase in fv.vocabulary:
                for m in _EXPORT_TOKEN.finditer(phrase):
                    token = m.group()
                    current_phrases.add(token)
                    concept_weeks[token].add(week_idx)

        # Detect rename pairs: old concepts that disappeared + new concepts that appeared same week
        if previous_phrases and week_idx > 0:
            disappeared = previous_phrases - current_phrases
            appeared = current_phrases - previous_phrases
            if disappeared and appeared:
                # Simple heuristic: if a new concept contains an old concept's name
                for old in list(disappeared)[:5]:
                    old_base = old.replace("V1", "").replace("V2", "").replace("V3", "").replace("V4", "").replace("V5", "")
                    old_base = old_base.replace("Old", "").replace("Legacy", "")
                    for new in list(appeared)[:5]:
                        if old_base and (old_base in new or new.replace("New", "").replace("V2", "").replace("V3", "") == old_base):
                            rename_pairs.append((old, new, week_idx))

        previous_phrases = current_phrases

    total_weeks = len(week_data)
    lifecycles = []

    # Common tokens that appear in every codebase — always noise
    _COMMON_TOKEN = re.compile(r'^(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|FROM|WHERE|AND|OR|NOT|IN|'
                               r'COUNT|SUM|AVG|MIN|MAX|ORDER|GROUP|HAVING|LIMIT|OFFSET|JOIN|ON|AS|SET|'
                               r'Getenv|Printf|Println|Fatal|Close|String|Error|Nil|True|False|None|'
                               r'Type|Struct|Interface|Return|Import|Package|Func|Const|Var|Int|Int64|Int32|'
                               r'Float32|Float64|Bool|Byte|Rune|Uint|Uint8|Uint16|Uint32|Uint64|'
                               r'Get|Set|Has|Is|To|From|New|Make|Append|Copy|Len|Cap|Add|Del|'
                               r'http|HTTP|URL|JSON|XML|HTML|YAML|Base64|UTF8|ASCII|'
                               r'Config|Logger|Handler|Router|Server|Client|DB|'
                               r'WithTimeout|Background|Second|Ping|Database|Identifier|Sanitize|QueryRow|'
                               r'Scan|Query|Exec|Row|Rows|Desc|GroupBy|OrderBy|'
                               r'Encoding|Parsing|Formatting|Validating|Reading|Writing|Serializing)$')

    for concept, weeks_present in concept_weeks.items():
        # Skip extremely common noise tokens
        if _COMMON_TOKEN.match(concept):
            continue
        first = min(weeks_present)
        last = max(weeks_present)
        age = total_weeks - first
        stale = total_weeks - last
        appearances = len(weeks_present)
        ratio = appearances / max(total_weeks, 1)

        # Check if this concept was in a rename pair
        renamed_to = [n for o, n, _ in rename_pairs if o == concept]
        renamed_from = [o for o, n, _ in rename_pairs if n == concept]

        # Check for seasonal pattern: disappeared then reappeared
        has_gap = False
        if len(weeks_present) >= 2:
            sorted_weeks = sorted(weeks_present)
            for i in range(len(sorted_weeks) - 1):
                if sorted_weeks[i + 1] - sorted_weeks[i] > 4:  # >4 week gap
                    has_gap = True
                    break

        # Classify
        if has_gap:
            signal = "SEASONAL"
        elif stale >= 8 and appearances <= 3:
            signal = "DEAD"
        elif stale >= 4 and age >= 12:
            signal = "DECAYING"
        elif appearances <= 2 and stale <= 1:
            signal = "EMERGING"
        elif age <= 4 and ratio >= 0.5:
            signal = "GROWING"
        elif ratio < 0.3 and age > 8:
            signal = "SPORADIC"
        elif first <= 2 and last <= 6 and stale >= 4:
            signal = "ABANDONED"
        elif renamed_to:
            signal = "RENAMED"
        elif renamed_from:
            signal = "RENAMED_TO"
        elif age >= 12 and ratio >= 0.8:
            signal = "STABLE"
        else:
            signal = "ACTIVE"

        item = {
            "concept": concept,
            "signal": signal,
            "age_weeks": age,
            "stale_weeks": stale,
            "appearance_ratio": round(ratio, 2),
            "first_week": first,
            "last_week": last,
        }
        if renamed_to:
            item["renamed_to"] = renamed_to[0]
        if renamed_from:
            item["renamed_from"] = renamed_from[0]

        lifecycles.append(item)

    lifecycles.sort(key=lambda x: ({"DEAD": 0, "ABANDONED": 1, "DECAYING": 2, "SEASONAL": 3,
                                    "RENAMED": 4, "GROWING": 5, "EMERGING": 6, "SPORADIC": 7,
                                    "RENAMED_TO": 8, "ACTIVE": 9, "STABLE": 10}.get(x["signal"], 99),
                                   -x["age_weeks"]))
    return lifecycles


def pr_blast_radius(pr_files: list[str], all_file_vocabs: list[FileVocab],
                    min_shared: int = 2) -> dict:
    """Score unchanged files by vocabulary overlap with PR files.

    For each PR file, extract exported-format identifiers. Then for every
    unchanged file, compute how many identifiers it shares.

    Returns dict with HIGH/MED/LOW buckets and rename warnings.
    """
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')
    pr_set = set(pr_files)

    # Extract all exported identifiers from PR files
    pr_vocab: set[str] = set()
    for fv in all_file_vocabs:
        if fv.path in pr_set:
            for phrase in fv.vocabulary:
                for m in _EXPORT_TOKEN.finditer(phrase):
                    pr_vocab.add(m.group())

    if not pr_vocab:
        return {"impacts": [], "rename_warnings": []}

    # Score unchanged files
    impacts = []
    for fv in all_file_vocabs:
        if fv.path in pr_set:
            continue
        shared: set[str] = set()
        for phrase in fv.vocabulary:
            for m in _EXPORT_TOKEN.finditer(phrase):
                token = m.group()
                if token in pr_vocab:
                    shared.add(token)
        if len(shared) >= min_shared:
            impacts.append({
                "file": fv.path,
                "shared_concepts": len(shared),
                "concepts": sorted(shared, key=lambda x: -len(x))[:8],
                "concentration": round(len(shared) / max(len(pr_vocab), 1), 3),
            })

    impacts.sort(key=lambda x: -x["shared_concepts"])

    # Detect renames: concepts removed from PR files replaced by new ones
    rename_warnings = []
    # Get old names (present in all non-PR files but not in PR)
    old_names: set[str] = set()
    for fv in all_file_vocabs:
        if fv.path not in pr_set:
            for phrase in fv.vocabulary:
                for m in _EXPORT_TOKEN.finditer(phrase):
                    old_names.add(m.group())
    old_names -= pr_vocab

    # Check if PR file vocabulary has partial name matches with old names
    for old in list(old_names)[:10]:
        for new in list(pr_vocab)[:20]:
            if old[:3] and old[:3].lower() in new.lower():
                rename_warnings.append({"old_name": old, "new_name": new})

    return {"impacts": impacts, "rename_warnings": rename_warnings}


def search_cross_repo_ranked(phrase: str, repo_paths: list[str]) -> list[dict]:
    """Cross-repo concept search with concentration ranking.

    Each result shows how central the phrase is to its repo
    (file_count_with_phrase / total_files_in_repo).
    """
    results = []
    for repo in repo_paths:
        try:
            analysis = scan_codebase(repo, quiet=True)
        except Exception:
            continue
        total = len(analysis.file_vocabs)
        matches = []
        for fv in analysis.file_vocabs:
            # Case-insensitive substring match
            if phrase.lower() in " ".join(fv.vocabulary.keys()).lower():
                matches.append({
                    "file": fv.path,
                    "language": fv.language,
                })
        if matches:
            concentration = len(matches) / max(total, 1)
            results.append({
                "repo": os.path.basename(repo),
                "total_files": total,
                "matches": len(matches),
                "concentration": round(concentration, 3),
                "files": matches[:15],
            })

    results.sort(key=lambda x: -x["concentration"])
    return results


def _is_lock_file(path: str) -> bool:
    base = os.path.basename(path)
    return base in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "Cargo.lock", "go.sum", "poetry.lock")


def _is_generated(path: str) -> bool:
    base = os.path.basename(path)
    return (base.startswith("zz_") or base == "zz_generated.go"
            or base.endswith(".pb.go") or base.endswith(".pb.ts")
            or ".min." in path or ".generated." in path)


# Only these file types are scanned for dead export detection
_DEAD_CODE_EXTS = frozenset({
    ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".rs", ".c", ".cpp", ".h", ".hpp", ".java",
    ".kt", ".kts", ".swift", ".rb", ".php",
})


def concept_timeline(path: str, weeks: int = 12) -> list[dict]:
    if not vgit.is_repo(path):
        return []
    weeks_data = vgit.weekly_commits(path, weeks=weeks)
    timeline = []
    prev_phrases: set[str] = set()
    for wk in weeks_data:
        shas = wk.get("shas", [])
        if not shas:
            continue
        try:
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True)
        except Exception:
            continue
        current = {phrase for fv in analysis.file_vocabs for phrase in fv.vocabulary}
        new_concepts = current - prev_phrases if prev_phrases else set()
        retired = prev_phrases - current if prev_phrases else set()
        stable = current & prev_phrases if prev_phrases else current
        timeline.append({
            "week": wk["week"],
            "commits": wk["commit_count"],
            "new_concepts": len(new_concepts),
            "retired_concepts": len(retired),
            "stable_concepts": len(stable),
            "total_concepts": len(current),
        })
        prev_phrases = current
    return timeline


def search_cross_repo(phrase: str, repo_paths: list[str]) -> list[dict]:
    results = []
    for repo in repo_paths:
        try:
            analysis = scan_codebase(repo, quiet=True)
        except Exception:
            continue
        for fv in analysis.file_vocabs:
            if phrase in fv.vocabulary:
                results.append({
                    "repo": os.path.basename(repo),
                    "file": fv.path,
                    "language": fv.language,
                })
    return results
