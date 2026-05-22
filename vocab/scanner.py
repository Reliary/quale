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
from vocab.concepts import cluster_labels
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
    "target", "build", "dist", "out", ".next", ".nuxt",
    ".terraform", ".serverless",
    ".nyc_output", "coverage", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".parcel-cache", ".turbo", ".cache",
    ".gitlab", ".github",
})

_SKIP_PATH_PARTS = frozenset({
    "node_modules", "vendor", "dist", "build", "target", "out",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".parcel-cache", ".turbo", ".cache", "coverage", "playwright-transform-cache-1000",
})


_SECRET_SHAPED = re.compile(r'^(?:[A-Za-z0-9+/]{24,}|[A-Fa-f0-9]{24,}|AIza[A-Za-z0-9_-]+|MII[A-Za-z0-9+/]+)$')


def _is_actionable_identifier(identifier: str) -> bool:
    """Mechanical identifier guardrail, not semantic filtering."""
    if not identifier:
        return False
    if len(identifier) < 5 or len(identifier) > 40:
        return False
    if _SECRET_SHAPED.match(identifier):
        return False
    return True


def _is_binary(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _BINARY_EXTS:
        return True
    base = os.path.basename(path).lower()
    if base.endswith(".min.js") or base.endswith(".min.css"):
        return True
    return False


def _skip_path(path: str) -> bool:
    parts = set(path.split("/"))
    if parts & _SKIP_PATH_PARTS:
        return True
    if any(p.endswith(".egg-info") for p in parts):
        return True
    return False


def scan_codebase(path: str, git_ref: str | None = None, quiet: bool = False,
                  clones: bool = False, deep: bool = False,
                  max_files: int | None = None,
                  max_seconds: float | None = None) -> CodebaseAnalysis:
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
    if max_files is not None and len(files) > max_files:
        files = files[:max_files]

    total = len(files)
    skipped_binary = 0
    skipped_empty = 0

    for idx, rel_file in enumerate(files):
        if max_seconds is not None and time.time() - start > max_seconds:
            if not quiet:
                print(f"  Scan budget hit after {time.time() - start:.1f}s; returning partial analysis", file=sys.stderr)
            break
        if _skip_path(rel_file):
            continue
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

        content = vgit.read_file_at_ref(path, rel_file, ref=git_ref, check_mode=git_ref is None)
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
    """Heuristic: exported-format identifiers appearing in exactly 1 file.

    This is a best-effort signal, not authoritative dead code detection.
    The segmenter produces line-level phrases (not token-level), so cross-file
    references embedded in expressions like `case SanitizationPolicyRelaxed:`
    are caught by regex extraction. Limitations: reflection, generated code,
    and indirect usage cannot be detected.
    """
    phrase_files: dict[str, str] = {}
    single_file: dict[str, str] = {}

    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{6,40}\b')

    for fv in file_vocabs:
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
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True, max_files=1500, max_seconds=20)
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
                    max_results: int = 200, code_only: bool = True) -> dict:
    """Measure how broadly a PR's vocabulary ripples through unchanged files.

    For each changed file, extract all qualifying identifiers. For every
    unchanged file, count how many identifiers it shares with changed files.
    Returns a flat sorted list — no tiered risk labels.
    """
    pr_set = set(pr_files)

    # When code_only, filter to code extensions only
    if code_only:
        all_file_vocabs = [fv for fv in all_file_vocabs
                          if os.path.splitext(fv.path)[1].lower() in _DEAD_CODE_EXTS]

    # Extract all identifiers from PR files
    pr_vocab: set[str] = set()
    for fv in all_file_vocabs:
        if fv.path in pr_set:
            pr_vocab.update(_extract_identifiers(fv))

    if not pr_vocab:
        return {"impacts": [], "rename_warnings": []}

    # Score unchanged files
    impacts = []
    for fv in all_file_vocabs:
        if fv.path in pr_set:
            continue
        shared = _extract_identifiers(fv) & pr_vocab
        if shared:
            impacts.append({
                "file": fv.path,
                "shared_concepts": len(shared),
                "concepts": sorted(shared, key=lambda x: -len(x))[:8],
            })

    impacts.sort(key=lambda x: -x["shared_concepts"])
    return {"impacts": impacts[:max_results], "rename_warnings": []}


def _is_test_path(path: str) -> bool:
    base = os.path.basename(path).lower()
    parts = {p.lower() for p in path.split("/")}
    return ("test" in parts or "tests" in parts or "testdata" in parts
            or base.endswith("_test.go") or base.endswith(".test.ts")
            or base.endswith(".test.tsx") or base.endswith(".spec.ts")
            or base.endswith(".spec.tsx"))


def _extract_identifiers(fv: FileVocab, min_len: int = 4) -> set[str]:
    token = re.compile(rf'\b[A-Z][A-Za-z0-9_]{{{min_len - 1},40}}\b')
    identifiers: set[str] = set()
    for phrase in fv.vocabulary:
        for match in token.finditer(phrase):
            ident = match.group()
            if _is_actionable_identifier(ident):
                identifiers.add(ident)
    return identifiers


def _identifier_file_map(analysis: CodebaseAnalysis, include_tests: bool = False) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    concept_files: dict[str, set[str]] = defaultdict(set)
    concept_langs: dict[str, set[str]] = defaultdict(set)
    for fv in _code_file_vocabs(analysis):
        if not include_tests and _is_test_path(fv.path):
            continue
        for ident in _extract_identifiers(fv):
            concept_files[ident].add(fv.path)
            concept_langs[ident].add(fv.language)
    return concept_files, concept_langs


def _structural_information_score(file_count: int, total_files: int, lang_count: int = 1) -> float:
    """Score concepts by structural information, not semantic allow/deny lists.

    Very common concepts are less informative; very rare concepts are local.
    The useful band is concepts repeated enough to bind files, but not so common
    that they describe the whole programming substrate.
    """
    if total_files <= 0 or file_count <= 0:
        return 0.0
    prevalence = file_count / total_files
    if prevalence > 0.45:
        return 0.0
    repeat = min(file_count / 8, 1.5)
    rarity = 1.0 - prevalence
    lang_bonus = 1.0 + min(lang_count - 1, 2) * 0.15
    return round(file_count * rarity * repeat * lang_bonus, 3)


def _code_file_vocabs(analysis: CodebaseAnalysis) -> list[FileVocab]:
    files = []
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in _DEAD_CODE_EXTS:
            continue
        if _is_lock_file(fv.path) or _is_generated(fv.path):
            continue
        files.append(fv)
    return files


def _mirror_signals(changed: list[str], all_file_vocabs: list[FileVocab]) -> dict:
    """Source/test vocabulary mirror warnings for CI.

    This is intentionally a proxy, not coverage truth. It asks whether changed
    source identifiers have any test-side vocabulary mirror.
    """
    changed_set = set(changed)
    source_concepts: Counter[str] = Counter()
    test_concepts: Counter[str] = Counter()
    related_tests: list[str] = []

    for fv in all_file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in _DEAD_CODE_EXTS or _is_lock_file(fv.path) or _is_generated(fv.path):
            continue
        ids = _extract_identifiers(fv)
        if _is_test_path(fv.path):
            for ident in ids:
                test_concepts[ident] += 1
            if ids:
                related_tests.append(fv.path)
        elif fv.path in changed_set:
            for ident in ids:
                source_concepts[ident] += 1

    unmirrored = sorted(
        (ident for ident in source_concepts if ident not in test_concepts),
        key=lambda x: (-source_concepts[x], -len(x), x),
    )[:30]

    mirrored = sum(1 for ident in source_concepts if ident in test_concepts)
    total = len(source_concepts)
    if total == 0 or not test_concepts:
        return {
            "source_concepts_changed": total,
            "mirrored_source_concepts": 0,
            "mirror_ratio": 0.0,
            "unmirrored_source_concepts": [],
            "note": "No source/test mirror signal available for this change set.",
        }
    return {
        "source_concepts_changed": total,
        "mirrored_source_concepts": mirrored,
        "mirror_ratio": round(mirrored / max(total, 1), 3),
        "unmirrored_source_concepts": unmirrored,
        "note": "Vocabulary mirror is a structural proxy, not test coverage proof.",
    }


def search_cross_repo_ranked(phrase: str, repo_paths: list[str]) -> list[dict]:
    """Cross-repo concept search with concentration ranking.

    Each result shows how central the phrase is to its repo
    (file_count_with_phrase / total_files_in_repo).
    Only counts code files — docs, config, lock files excluded.
    """
    _SEARCH_CODE_EXTS = frozenset({
        ".go", ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".c", ".cpp",
        ".h", ".hpp", ".java", ".kt", ".rb", ".php", ".swift",
    })

    results = []
    for repo in repo_paths:
        try:
            analysis = scan_codebase(repo, quiet=True, max_files=2500, max_seconds=30)
        except Exception:
            continue
        total = 0
        matches = []
        for fv in analysis.file_vocabs:
            ext = os.path.splitext(fv.path)[1].lower()
            if ext not in _SEARCH_CODE_EXTS:
                continue
            total += 1
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
    parts = set(path.split("/"))
    return (_skip_path(path)
            or base.startswith("zz_") or base == "zz_generated.go"
            or base.endswith(".pb.go") or base.endswith(".pb.ts")
            or ".min." in path or ".generated." in path
            or base.startswith("mock_") or base.startswith("_mock")
            or base in ("querier.go", "models.go") or "/sqlc/" in path
            or "mocks" in parts)


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
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True, max_files=1500, max_seconds=20)
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
            analysis = scan_codebase(repo, quiet=True, max_files=2500, max_seconds=30)
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


# ── Stability anchors ──────────────────────────────────────────────

def compute_stability(path: str, weeks: int = 12, min_appearances: int = 4) -> list[dict]:
    """Find files whose vocabulary barely changes across git history.

    For each file present in at least `min_appearances` snapshots, compute
    phrase persistence (% of phrases that survive across all snapshots).
    Files with high persistence are stability anchors — they change rarely.
    Low persistence files are churn hotspots.
    """
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    # For each file, track: total phrases seen, phrases that persist in latest snapshot
    file_snapshots: dict[str, list[set[str]]] = defaultdict(list)

    for wk in week_data:
        shas = wk.get("shas", [])
        if not shas:
            continue
        try:
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True, max_files=2000, max_seconds=25)
        except Exception:
            continue
        for fv in analysis.file_vocabs:
            file_snapshots[fv.path].append(set(fv.vocabulary.keys()))

    results = []
    for filepath, snapshots in file_snapshots.items():
        if len(snapshots) < min_appearances:
            continue
        if len(snapshots) <= 1:
            continue

        # Compute phrase persistence: phrases that appear in ALL snapshots / total unique phrases
        if not snapshots:
            continue
        all_phrases: set[str] = set()
        for s in snapshots:
            all_phrases.update(s)
        preserved = snapshots[0]
        for s in snapshots[1:]:
            preserved &= s

        total_unique = len(all_phrases) if all_phrases else 1
        persistence = len(preserved) / total_unique

        # Turnover rate: average phrase churn per snapshot
        turnover_rates = []
        for i in range(1, len(snapshots)):
            if snapshots[i-1]:
                churn = len(snapshots[i] - snapshots[i-1]) / max(len(snapshots[i-1]), 1)
                turnover_rates.append(churn)

        avg_turnover = sum(turnover_rates) / max(len(turnover_rates), 1) if turnover_rates else 0

        results.append({
            "file": filepath,
            "persistence": round(persistence, 3),
            "avg_turnover": round(avg_turnover, 3),
            "snapshots": len(snapshots),
            "total_phrases": total_unique,
            "stable_phrases": len(preserved),
        })

    results.sort(key=lambda x: x["persistence"])
    return results


def _snapshot_phrases(analysis: CodebaseAnalysis) -> set[str]:
    """Extract all qualifying identifiers from a codebase snapshot."""
    phrases: set[str] = set()
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in _DEAD_CODE_EXTS:
            continue
        if _is_lock_file(fv.path) or _is_generated(fv.path):
            continue
        phrases.update(_extract_identifiers(fv))
    return phrases


# ── Cross-repo vocabulary alignment ───────────────────────────────

def compare_repos(repo_a: str, repo_b: str) -> dict:
    """Structural vocabulary alignment between two repos.

    Returns A-only, B-only, and shared phrase sets, plus drift analysis:
    phrases in A's code files that don't appear in B.
    """
    analysis_a = scan_codebase(repo_a, quiet=True, max_files=2500, max_seconds=30)
    analysis_b = scan_codebase(repo_b, quiet=True, max_files=2500, max_seconds=30)

    phrases_a = _snapshot_phrases(analysis_a)
    phrases_b = _snapshot_phrases(analysis_b)
    files_a, langs_a = _identifier_file_map(analysis_a, include_tests=False)
    files_b, langs_b = _identifier_file_map(analysis_b, include_tests=False)

    shared = phrases_a & phrases_b
    only_a = phrases_a - phrases_b
    only_b = phrases_b - phrases_a

    # Repo metadata
    a_name = os.path.basename(os.path.normpath(repo_a))
    b_name = os.path.basename(os.path.normpath(repo_b))

    total_a = max(len([fv for fv in _code_file_vocabs(analysis_a) if not _is_test_path(fv.path)]), 1)
    total_b = max(len([fv for fv in _code_file_vocabs(analysis_b) if not _is_test_path(fv.path)]), 1)

    def ranked_drift(phrases: set[str], file_map: dict[str, set[str]], lang_map: dict[str, set[str]], total: int) -> list[dict]:
        rows = []
        for phrase in phrases:
            support = len(file_map.get(phrase, set()))
            if support == 0:
                continue
            rows.append({
                "concept": phrase,
                "score": _structural_information_score(support, total, len(lang_map.get(phrase, set()))),
                "file_count": support,
                "languages": sorted(lang_map.get(phrase, set())),
            })
        rows.sort(key=lambda x: (-x["score"], -x["file_count"], x["concept"]))
        return rows[:30]

    drift_a_to_b = ranked_drift(only_a, files_a, langs_a, total_a)
    drift_b_to_a = ranked_drift(only_b, files_b, langs_b, total_b)

    # Alignment score
    union = len(phrases_a | phrases_b) or 1
    alignment = len(shared) / union

    a_unique_ratio = len(only_a) / max(len(phrases_a), 1)
    b_unique_ratio = len(only_b) / max(len(phrases_b), 1)
    asymmetry_score = round(abs(a_unique_ratio - b_unique_ratio), 3)
    if asymmetry_score < 0.05:
        dominant = "balanced"
    elif a_unique_ratio > b_unique_ratio:
        dominant = f"{a_name}_specific"
    else:
        dominant = f"{b_name}_specific"

    return {
        "schema_version": 1,
        "repo_a": a_name,
        "repo_b": b_name,
        "a_total_phrases": len(phrases_a),
        "b_total_phrases": len(phrases_b),
        "shared_phrases": len(shared),
        "only_in_a": len(only_a),
        "only_in_b": len(only_b),
        "alignment": round(alignment, 3),
        "drift_candidates": [d["concept"] for d in drift_a_to_b],
        "directional_drift": {
            "a_to_b": drift_a_to_b,
            "b_to_a": drift_b_to_a,
        },
        "asymmetry": {
            "score": asymmetry_score,
            "dominant_direction": dominant,
            "a_unique_ratio": round(a_unique_ratio, 3),
            "b_unique_ratio": round(b_unique_ratio, 3),
        },
        "a_languages": dict(sorted(analysis_a.languages.items(), key=lambda x: -x[1])),
        "b_languages": dict(sorted(analysis_b.languages.items(), key=lambda x: -x[1])),
    }


# ── Phrase provenance ─────────────────────────────────────────────

def phrase_provenance(path: str, phrase: str, weeks: int = 24) -> list[dict]:
    """Trace a phrase through git history.

    For each week, report whether the phrase was present and in which files.
    """
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    timeline = []
    for wk in week_data:
        shas = wk.get("shas", [])
        if not shas:
            continue
        try:
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True, max_files=1500, max_seconds=20)
        except Exception:
            continue

        files_present = []
        for fv in analysis.file_vocabs:
            ext = os.path.splitext(fv.path)[1].lower()
            if ext not in _DEAD_CODE_EXTS:
                continue
            if _is_lock_file(fv.path) or _is_generated(fv.path):
                continue
            for p in fv.vocabulary:
                if phrase.lower() in p.lower():
                    files_present.append(fv.path)
                    break

        timeline.append({
            "week": wk["week"],
            "present": len(files_present) > 0,
            "file_count": len(files_present),
            "files": files_present[:5],
        })

    return timeline


# ── Explore / onboarding map ──────────────────────────────────────

def explore_repo(path: str, themes: bool = False, analysis: CodebaseAnalysis | None = None) -> dict:
    """Find code files that best characterize the codebase.

    Scores each code file by how many unique qualifying identifiers it
    contains (exported-format tokens like `SpoolManager`, `IngestRequest`).
    Excludes docs, config, lock files, and generated files.
    Best first picks for onboarding: high-concept-density source files.

    When themes=True, also returns latent structural themes via
    identifier co-occurrence clustering (fast — no deep scan needed).
    """
    if analysis is None:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"files": [], "themes": []}

    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')

    # Compute global identifier frequency (for rare-concept scoring)
    identifier_file_count: Counter[str] = Counter()
    file_identifiers: list[tuple[str, str, set[str]]] = []  # (path, lang, identifiers)

    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in _DEAD_CODE_EXTS:
            continue
        if _is_lock_file(fv.path) or _is_generated(fv.path):
            continue
        if "/tests/" in fv.path or "/testdata/" in fv.path:
            continue

        identifiers: set[str] = set()
        for phrase in fv.vocabulary:
            for m in _EXPORT_TOKEN.finditer(phrase):
                identifiers.add(m.group())

        if not identifiers:
            continue

        for ident in identifiers:
            identifier_file_count[ident] += 1
        file_identifiers.append((fv.path, fv.language, identifiers))

    total_files = max(sum(1 for _, _, _ in file_identifiers), 1)

    scored = []
    for path, lang, identifiers in file_identifiers:
        # Deprioritize generated files — they have dense identifiers but aren't
        # useful onboarding targets. Penalize sqlc models, querier, mocks, etc.
        is_generated = _is_generated(path)
        gen_penalty = 0.3 if is_generated else 1.0

        # Role weighting: source files first, tests/scripts/examples lower
        role = _task_file_role(path)
        role_penalty = {"source": 1.0, "script": 0.6, "example": 0.3, "test": 0.3}.get(role, 0.5)

        # Unique-concept score: identifiers that appear in few files (rare = more characteristic)
        unique_score = sum(1 / max(identifier_file_count[i], 1) for i in identifiers) * gen_penalty * role_penalty

        # Total identifiers
        ident_count = len(identifiers)

        scored.append({
            "file": path,
            "language": lang,
            "identifiers": ident_count,
            "unique_score": round(unique_score, 2),
            "coverage": round(ident_count / max(len(identifier_file_count), 1), 4),
        })

    # Sort by unique_score descending (files with most rare/specialized identifiers first)
    scored.sort(key=lambda x: -x["unique_score"])

    result: dict = {"files": scored[:20], "themes": [], "total_code_files": total_files, "schema_version": 1}

    if themes and len(file_identifiers) >= 10:
        result["themes"] = _compute_themes(file_identifiers)

    return result


def _compute_themes(file_identifiers: list[tuple[str, str, set[str]]]) -> list[dict]:
    """Compute latent structural themes from identifier co-occurrence.

    Clusters identifiers by their file co-occurrence patterns.
    Each resulting theme = a group of identifiers that tend to appear
    in the same files, revealing domain-level concepts.
    """
    df: Counter[str] = Counter()
    for _, _, idents in file_identifiers:
        for ident in idents:
            df[ident] += 1

    total_files = len(file_identifiers) or 1

    # Mid-frequency identifiers: appear in 5-60% of files (min 2)
    mid_freq = {ident for ident, count in df.items()
                if 2 <= count <= total_files * 0.6 and count >= 2}

    if len(mid_freq) < 10:
        return []

    ident_files: dict[str, set[int]] = {}
    for idx, (_, _, idents) in enumerate(file_identifiers):
        for ident in idents & mid_freq:
            ident_files.setdefault(ident, set()).add(idx)

    # Build adjacency via per-file pair counting (O(F·I²) not O(N²))
    co_occurrence: dict[tuple[str, str], int] = Counter()
    for idx, (_, _, idents) in enumerate(file_identifiers):
        file_mid = sorted(idents & mid_freq)
        # Cap per-file identifiers to prevent O(N²) blowup
        if len(file_mid) > 200:
            file_mid = file_mid[:200]
        for i in range(len(file_mid)):
            a = file_mid[i]
            for j in range(i + 1, len(file_mid)):
                b = file_mid[j]
                key = (a, b) if a < b else (b, a)
                co_occurrence[key] += 1

    adjacency: dict[str, set[str]] = defaultdict(set)
    for (a, b), count in co_occurrence.items():
        files_a = len(ident_files.get(a, set()))
        files_b = len(ident_files.get(b, set()))
        smaller = min(files_a, files_b)
        if count >= 2 and count / max(smaller, 1) >= 0.20:
            adjacency[a].add(b)
            adjacency[b].add(a)

    # Connected components in identifier graph = themes
    visited: set[str] = set()
    themes = []
    for ident in sorted(adjacency.keys()):
        if ident in visited or ident not in adjacency:
            continue
        component: set[str] = set()
        stack = [ident]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            for neighbor in adjacency.get(node, set()):
                if neighbor not in visited:
                    stack.append(neighbor)
        if len(component) < 3:
            continue

        theme_file_set: set[int] = set()
        for idx, (_, _, idents) in enumerate(file_identifiers):
            if len(idents & component) >= 2:
                theme_file_set.add(idx)
        if len(theme_file_set) < 3:
            continue

        scores = {}
        for c in component:
            in_files = ident_files.get(c, set())
            in_theme = len(in_files & theme_file_set)
            not_theme = len(in_files - theme_file_set)
            scores[c] = in_theme / max(in_theme + not_theme, 1)

        top_labels = sorted(scores, key=lambda x: -scores[x])[:3]
        themes.append({
            "label": "/".join(top_labels),
            "files": len(theme_file_set),
            "exemplar_phrases": sorted(scores, key=lambda x: -scores[x])[:8],
            "variance_explained": round(len(theme_file_set) / total_files, 3),
        })

    themes.sort(key=lambda x: -x["files"])
    return themes[:5]


# ── Repo-level structural fingerprint ─────────────────────────────

# ── TDA Module Detection ──────────────────────────────────────────

def compute_modules(path: str, analysis: CodebaseAnalysis | None = None) -> dict:
    """Persistent connected components across rare-identifier thresholds.

    Uses TDA (topological data analysis) on the phrase-file graph:
    1. Extract rare identifiers (appearing in <=10% of files)
    2. Build edges between files sharing rare identifiers
    3. Run union-find at increasing thresholds (1 to 10 shared identifiers)
    4. Identify modules that persist across >=3 consecutive thresholds
    """
    if analysis is None:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{4,40}\b')

    identifier_df: Counter[str] = Counter()
    file_identifiers: dict[str, set[str]] = {}

    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in _DEAD_CODE_EXTS:
            continue
        if _is_lock_file(fv.path) or _is_generated(fv.path):
            continue

        ids: set[str] = set()
        for phrase in fv.vocabulary:
            for m in _EXPORT_TOKEN.finditer(phrase):
                ids.add(m.group())
        if ids:
            file_identifiers[fv.path] = ids
            for ident in ids:
                identifier_df[ident] += 1

    total_files = len(file_identifiers)
    if total_files < 2:
        return {"modules": [], "total_files": total_files, "grouped_files": 0}

    rare_threshold = max(2, total_files // 10)
    rare_ids = {ident for ident, df in identifier_df.items() if df <= rare_threshold}

    files = [f for f in file_identifiers if rare_ids & file_identifiers[f]]
    n = len(files)
    if n < 2:
        return {"modules": [], "total_files": total_files, "grouped_files": 0}

    rare_file_sets: dict[str, set[int]] = {}
    for idx, f in enumerate(files):
        for ident in file_identifiers[f] & rare_ids:
            rare_file_sets.setdefault(ident, set()).add(idx)

    shared_count: dict[tuple[int, int], int] = Counter()
    for ident, idxs in rare_file_sets.items():
        idx_list = list(idxs)
        for i in range(len(idx_list)):
            for j in range(i + 1, len(idx_list)):
                a = idx_list[i] if idx_list[i] < idx_list[j] else idx_list[j]
                b = idx_list[j] if idx_list[i] < idx_list[j] else idx_list[i]
                shared_count[(a, b)] += 1

    def _run_uf(threshold: int) -> list[list[str]]:
        parent = list(range(n))
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py
        for (i, j), count in shared_count.items():
            if count >= threshold:
                union(i, j)
        comps: dict[int, list[str]] = {}
        for i in range(n):
            root = find(i)
            comps.setdefault(root, []).append(files[i])
        return [c for c in comps.values() if len(c) >= 2]

    threshold_modules = {t: _run_uf(t) for t in range(1, 11)}

    module_groups = []
    seen_modules: set[frozenset[str]] = set()

    for t in range(1, 9):
        for comp in threshold_modules.get(t, []):
            comp_set = frozenset(comp)
            if comp_set in seen_modules:
                continue

            persists = True
            for dt in range(1, 3):
                if t + dt > 10:
                    persists = False
                    break
                found = False
                for nc in threshold_modules.get(t + dt, []):
                    if comp_set.issubset(frozenset(nc)):
                        found = True
                        break
                if not found:
                    persists = False
                    break

            if persists:
                seen_modules.add(comp_set)
                exemplars: Counter[str] = Counter()
                for f in comp:
                    for ident in file_identifiers.get(f, set()):
                        exemplars[ident] += 1
                module_groups.append({
                    "files": sorted(comp),
                    "persistence_range": [t, min(t + 2, 10)],
                    "exemplar_phrases": [p for p, _ in exemplars.most_common(8)],
                    "size": len(comp),
                })

    deduped = []
    for m in module_groups:
        m_set = frozenset(m["files"])
        is_subset = any(
            m_set.issubset(frozenset(n["files"])) and m["size"] < n["size"]
            for n in module_groups if m is not n
        )
        if not is_subset:
            deduped.append(m)

    deduped.sort(key=lambda x: -x["size"])
    grouped = len({f for m in deduped for f in m["files"]})

    return {
        "modules": deduped[:30],
        "total_files": total_files,
        "grouped_files": grouped,
        "schema_version": 1,
    }


# ── Combined bootstrap / inspect ──────────────────────────────────

def _compute_agent_notes(path: str, explore_data: dict, modules_data: dict,
                          stability_data: list[dict]) -> list[str]:
    """Generate programmatic guidance notes from available data."""
    notes = []
    total_code = explore_data.get("total_code_files", 0)
    if total_code > 0:
        notes.append(f"Repository has {total_code} code files.")

    themes = explore_data.get("themes", [])
    if themes:
        theme_labels = ", ".join(t["label"][:25] for t in themes[:2])
        notes.append(f"Conceptual themes: {theme_labels}.")

    mod_count = len(modules_data.get("modules", []))
    if mod_count > 0:
        grouped = modules_data.get("grouped_files", 0)
        notes.append(f"{mod_count} module boundaries detected ({grouped} files grouped).")
    else:
        notes.append("No persistent module boundaries — loosely coupled codebase.")

    anchors = [x for x in stability_data if x["persistence"] >= 0.8]
    hotspots = [x for x in stability_data if x["persistence"] <= 0.3 and x["total_phrases"] >= 5]
    if anchors:
        top_anchor = max(anchors, key=lambda x: x["persistence"])
        notes.append(f"Most stable file: {top_anchor['file']} (persistence {top_anchor['persistence']:.0%}).")
    if hotspots:
        notes.append(f"Churn hotspots: {len(hotspots)} files change frequently.")
    return notes


def _binding_concepts(analysis: CodebaseAnalysis, limit: int = 15) -> list[dict]:
    """Concept-first architecture map: identifiers binding many source files."""
    source_files = [fv for fv in _code_file_vocabs(analysis) if not _is_test_path(fv.path)]
    total_files = max(len(source_files), 1)
    concept_files, concept_langs = _identifier_file_map(analysis, include_tests=False)
    rows = []
    for ident, files in concept_files.items():
        if len(files) < 3:
            continue
        score = _structural_information_score(len(files), total_files, len(concept_langs[ident]))
        if score <= 0:
            continue
        rows.append({
            "concept": ident,
            "score": score,
            "file_count": len(files),
            "languages": sorted(concept_langs[ident]),
            "files": sorted(files)[:8],
            "why": "Binds multiple source files; useful for architecture orientation.",
        })
    rows.sort(key=lambda x: (-x["score"], -x["file_count"], x["concept"]))
    return rows[:limit]


def _rank_related_files(path: str, keywords: list[str], analysis: CodebaseAnalysis | None = None) -> list[dict]:
    if analysis is None:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    scores: dict[str, dict] = {}
    lowered = [k.lower() for k in keywords if len(k) >= 4]
    for fv in _code_file_vocabs(analysis):
        haystack = f"{fv.path} " + " ".join(fv.vocabulary.keys())
        hay = haystack.lower()
        path_lower = fv.path.lower()
        matched = []
        score = 0
        for kw in lowered:
            if kw in path_lower:
                score += 6
                matched.append(kw)
            elif kw in hay:
                score += 2
                matched.append(kw)
        if not score:
            continue
        role = _task_file_role(fv.path)
        if role == "test":
            score += 1
        elif role == "script":
            score -= 1
        elif role == "example":
            score -= 2
        if _is_generated(fv.path):
            score -= 5
        if score <= 0:
            continue
        scores[fv.path] = {
            "file": fv.path,
            "phrase": ",".join(dict.fromkeys(matched)),
            "matches": score,
            "role": role,
        }
    ranked = sorted(scores.values(), key=lambda x: (_task_role_rank(x["role"]), -x["matches"], x["file"]))
    source_matches = [item for item in ranked if item["role"] != "test"][:10]
    test_matches = [item for item in ranked if item["role"] == "test"][:5]
    return source_matches + test_matches


def _task_file_role(path: str) -> str:
    parts = [p.lower() for p in path.split("/")]
    if _is_test_path(path):
        return "test"
    if "examples" in parts or "example" in parts:
        return "example"
    if "scripts" in parts or "script" in parts:
        return "script"
    return "source"


def _task_role_rank(role: str) -> int:
    return {"source": 0, "script": 1, "example": 2, "test": 3}.get(role, 4)


def _task_plan(task: str | None, related: list[dict], reads: list[dict],
               modules_data: dict, stability_data: list[dict]) -> dict:
    """Build a compact task plan from structural signals."""
    if not task:
        return {}
    likely_edit = []
    seen: set[str] = set()
    ordered_related = sorted(related, key=lambda x: (_task_role_rank(x.get("role", "source")), -x.get("matches", 0), x["file"]))
    for item in ordered_related:
        path = item["file"]
        if path not in seen:
            seen.add(path)
            likely_edit.append(path)
        if len(likely_edit) >= 8:
            break

    stable_by_file = {x["file"]: x for x in stability_data if x["persistence"] >= 0.8}
    anchors = []
    for read in reads:
        f = read["file"]
        if f in stable_by_file:
            anchors.append({
                "file": f,
                "persistence": stable_by_file[f]["persistence"],
                "reason": "Stable anchor related to task context.",
            })

    module_context = []
    for module in modules_data.get("modules", []):
        module_files = set(module.get("files", []))
        overlap = [f for f in likely_edit if f in module_files]
        if overlap:
            module_context.append({
                "size": module.get("size", 0),
                "files": module.get("files", [])[:8],
                "matched_files": overlap,
                "reason": "Likely task file sits inside this structural module.",
            })

    return {
        "task": task,
        "likely_edit_files": likely_edit,
        "stable_anchors_to_read_first": anchors[:5],
        "module_context": module_context[:3],
        "sequence": [
            "Read source related_files_for_task before editing.",
            "Use recommended_next_reads for architecture context.",
            "Inspect likely_edit_files and their module_context.",
            "Avoid changing stable_anchors_to_read_first unless the task explicitly requires it.",
            "Use related test files as verification hints, not primary edit targets.",
        ],
    }


def bootstrap_repo(path: str, task: str | None = None) -> dict:
    """One-shot agent bootstrap: explore + modules + stability + optional task search."""
    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    explore_data = explore_repo(path, themes=True, analysis=analysis)
    modules_data = compute_modules(path, analysis=analysis)
    stability_data = compute_stability(path, weeks=12)

    # recommended_next_reads: top explore files excluding generated/tests
    reads = []
    for f in explore_data.get("files", []):
        fp = f["file"]
        if _is_generated(fp):
            continue
        if "/tests/" in fp:
            continue
        reason = "Highest identifier coverage" if not reads else "Supplementary coverage"
        reads.append({
            "file": fp,
            "score": f["unique_score"],
            "language": f["language"],
            "reason": reason,
        })
        if len(reads) >= 10:
            break
    if not reads:
        for f in explore_data.get("files", [])[:5]:
            reads.append({
                "file": f["file"],
                "score": f["unique_score"],
                "language": f["language"],
                "reason": "Top coverage file",
            })

    # avoid_touching_without_context: churn hotspots + low-stability
    hotspots = [x for x in stability_data
                if x["persistence"] <= 0.3 and x["total_phrases"] >= 5]
    avoid = []
    for h in sorted(hotspots, key=lambda x: x["persistence"])[:10]:
        avoid.append({
            "file": h["file"],
            "persistence": round(h["persistence"], 2),
            "avg_turnover": round(h["avg_turnover"], 2),
            "reason": "High churn — investigate before modifying",
        })

    # related_files_for_task
    related = []
    keywords = []
    if task:
        task_lower = task.lower()
        keywords = [w for w in task_lower.split() if len(w) > 3 and w not in
                    {"this", "that", "with", "from", "what", "which", "there", "their", "about", "would", "could", "should", "after", "before", "into", "over", "such", "only", "other", "than", "then", "also", "very", "just", "like", "some", "more", "they", "been", "when", "where"}]
        # Also extract capitalized identifiers (CamelCase)
        cap_pattern = re.compile(r'[A-Z][a-z]+[A-Z][A-Za-z0-9]*')
        cap_ids = cap_pattern.findall(task)
        keywords.extend(w.lower() for w in cap_ids)
        keywords = list(dict.fromkeys(keywords))[:5]

        if keywords:
            try:
                related = _rank_related_files(path, keywords[:5], analysis=analysis)
            except Exception:
                related = []

    verified_files = []
    unverified_files = []
    task_relevance_score = 1.0
    if related and keywords:
        for item in related:
            filepath = item["file"]
            try:
                with open(os.path.join(path, filepath), "r", errors="replace") as f:
                    content = f.read().lower()
            except Exception:
                unverified_files.append(filepath)
                continue
            if any(keyword in content for keyword in keywords[:5]):
                verified_files.append(filepath)
            else:
                unverified_files.append(filepath)
        task_relevance_score = len(verified_files) / max(len(related), 1)

    notes = _compute_agent_notes(path, explore_data, modules_data, stability_data)
    themes_out = explore_data.get("themes", [])
    task_plan = _task_plan(task, related, reads, modules_data, stability_data)

    return {
        "schema_version": 1,
        "recommended_next_reads": reads,
        "task_plan": task_plan,
        "avoid_touching_without_context": avoid,
        "related_files_for_task": related[:15] if related else [],
        "task_relevance_score": round(task_relevance_score, 3),
        "verified_files": verified_files,
        "unverified_files": unverified_files,
        "module_boundaries": modules_data.get("modules", []),
        "themes": themes_out,
        "agent_notes": notes,
        "total_code_files": explore_data.get("total_code_files", 0),
    }


def ci_report(base_ref: str, head_ref: str, path: str = ".") -> dict:
    """CI-ready report: blast radius + stable file check + diff summary."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    if vgit.has_commits(path):
        missing = [ref for ref in (base_ref, head_ref) if not vgit.ref_exists(path, ref)]
        if missing:
            return {"error": f"Unknown git ref(s): {', '.join(missing)}"}

    # Changed files between refs
    changed = vgit.diff_refs(path, base_ref, head_ref)
    if not changed:
        return {
            "schema_version": 1,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "changed_files": [],
            "blast_radius": [],
            "mirror_signals": {},
            "mirror_gap_ratio": 1.0,
            "max_blast_tier": "none",
            "stable_touched_count": 0,
            "blast_tier_counts": {"local": 0, "moderate": 0, "high": 0, "critical": 0},
            "stable_files_touched": [],
            "risk_flags": [],
            "summary": "No changed files.",
        }

    # Blast radius
    try:
        analysis = scan_codebase(path, git_ref=head_ref, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        analysis = None

    blast_results = []
    mirror = {}
    if analysis:
        radius = pr_blast_radius(changed, analysis.file_vocabs)
        blast_results = radius.get("impacts", [])
        mirror = _mirror_signals(changed, analysis.file_vocabs)

    # Check if any changed file is a stability anchor or churn hotspot
    try:
        stability_data = compute_stability(path, weeks=12)
    except Exception:
        stability_data = []
    stable_touched = []
    for c in changed:
        for s in stability_data:
            if s["file"] == c:
                if s["persistence"] >= 0.8:
                    stable_touched.append({
                        "file": c,
                        "status": "stable_anchor",
                        "persistence": round(s["persistence"], 2),
                    })
                elif s["persistence"] <= 0.3 and s["total_phrases"] >= 5:
                    stable_touched.append({
                        "file": c,
                        "status": "churn_hotspot",
                        "persistence": round(s["persistence"], 2),
                    })
                break

    # Risk flags
    risk_flags = []
    if len(changed) > 20:
        risk_flags.append(f"Large change set: {len(changed)} files (more than 20)")
    if stable_touched:
        anchors = [s for s in stable_touched if s["status"] == "stable_anchor"]
        if anchors:
            risk_flags.append(f"Touch {len(anchors)} stable anchors that rarely change")
    if blast_results and blast_results[0].get("shared_concepts", 0) > 10:
        risk_flags.append(f"Broad blast radius: top impacted file shares {blast_results[0]['shared_concepts']} concepts")
    if mirror.get("unmirrored_source_concepts"):
        risk_flags.append(f"Mirror gap: {len(mirror['unmirrored_source_concepts'])} changed source concepts not seen in tests")

    blast_tier_counts = {"local": 0, "moderate": 0, "high": 0, "critical": 0}
    max_blast_tier = "none"
    tier_order = {"none": 0, "local": 1, "moderate": 2, "high": 3, "critical": 4}
    for item in blast_results:
        count = item.get("shared_concepts", 0)
        if count <= 10:
            tier = "local"
        elif count <= 20:
            tier = "moderate"
        elif count <= 50:
            tier = "high"
        else:
            tier = "critical"
        blast_tier_counts[tier] += 1
        current_rank = tier_order.get(tier, 0)
        max_rank = tier_order.get(max_blast_tier, 0)
        if current_rank > max_rank:
            max_blast_tier = tier

    mirror_gap_ratio = mirror.get("mirror_ratio", 0.0) if mirror else 0.0
    stable_touched_count = len([s for s in stable_touched if s["status"] == "stable_anchor"])

    return {
        "schema_version": 1,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": changed,
        "blast_radius": blast_results[:30],
        "mirror_signals": mirror,
        "mirror_gap_ratio": mirror_gap_ratio,
        "max_blast_tier": max_blast_tier,
        "stable_touched_count": stable_touched_count,
        "blast_tier_counts": blast_tier_counts,
        "stable_files_touched": stable_touched,
        "risk_flags": risk_flags,
        "summary": (
            f"{len(changed)} files changed. "
            f"{'No blast radius.' if not blast_results else f'{len(blast_results)} impacted files.'} "
            + (f"{len(stable_touched)} stable files touched." if stable_touched else "")
        ),
    }


def inspect_repo(path: str) -> dict:
    """Aggregated overview: stats + explore + modules + stability + timeline."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    explore_data = explore_repo(path, themes=True, analysis=analysis)
    modules_data = compute_modules(path, analysis=analysis)
    timeline_data = concept_timeline(path, weeks=4)
    binding = _binding_concepts(analysis)

    # Compute half-life from lifecycle data
    try:
        lifecycle_data = compute_lifecycles(path, weeks=24)
        if lifecycle_data:
            ages = [l["age_weeks"] for l in lifecycle_data if l["signal"] in ("STABLE", "ACTIVE", "DEAD")]
            avg_age = round(sum(ages) / max(len(ages), 1), 1) if ages else 0
        else:
            avg_age = 0
    except Exception:
        avg_age = 0

    return {
        "schema_version": 1,
        "explore": explore_data,
        "modules": modules_data,
        "binding_concepts": binding,
        "timeline": timeline_data,
        "avg_concept_age_weeks": avg_age,
    }


def repo_fingerprint(path: str) -> dict:
    """Produce a single structural hash for an entire repo.

    Combines all file index sequences into one canonical hash.
    Two structurally identical repos produce the same hash.
    """
    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)

    # Sort files by path for deterministic ordering
    # For each file, encode: path_hash + index_sequence
    import hashlib
    combined = hashlib.sha256()
    total_indices = 0

    for fv in sorted(analysis.file_vocabs, key=lambda x: x.path):
        indices = list(fv.vocabulary.values())
        if not indices:
            continue
        total_indices += len(indices)
        # Feed path hash + index bytes
        path_hash = hashlib.sha256(fv.path.encode()).digest()
        combined.update(path_hash)
        combined.update(str(indices).encode())

    return {
        "fingerprint": f"v0-{combined.hexdigest()[:16]}",
        "files": analysis.total_files,
        "total_phrases": analysis.total_phrases,
        "total_indices": total_indices,
        "languages": len(analysis.languages),
    }
