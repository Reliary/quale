"""Codebase scanner — orchestrates PS + VB + IE + co-occurrence across files."""

from __future__ import annotations

import math
import os
import sys
import re
import time
from collections import defaultdict, Counter
from dataclasses import dataclass

from quale.segmenter import segment
from quale.vocabulary import build_vocabulary
from quale.index import structural_similarity
from quale.analyze import FileVocab, CoOccurrenceMatrix, classify_language
from quale.concepts import cluster_labels
from quale import git as vgit


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
    parts = set(path.replace("\\", "/").split("/"))
    if parts & _SKIP_PATH_PARTS:
        return True
    if any(p.endswith(".egg-info") for p in parts):
        return True
    if path.lower().endswith((".min.js", ".min.css")):
        return True
    return False


# ── Scan cache: shares snapshot results across history-based commands ──
# Cache key: (abs_path, git_ref). 50-entry limit. Cleared per-invocation.
_SCAN_CACHE: dict[tuple[str, str | None], CodebaseAnalysis] = {}
_SCAN_CACHE_MAX = 50


def _scan_cache_key(path: str, git_ref: str | None, deep: bool = False) -> tuple:
    return (os.path.abspath(path), git_ref, deep)


def _scan_cache_clear() -> None:
    _SCAN_CACHE.clear()


def scan_codebase(path: str, git_ref: str | None = None, quiet: bool = False,
                  clones: bool = False, deep: bool = False,
                  max_files: int | None = None,
                  max_seconds: float | None = None) -> CodebaseAnalysis:
    path = os.path.abspath(path)
    key = _scan_cache_key(path, git_ref, deep=deep)
    if key in _SCAN_CACHE:
        return _SCAN_CACHE[key]

    if git_ref is not None:
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
        if "\x00" in content[:4096]:
            skipped_binary += 1
            continue

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
        source_fvs = all_file_vocabs
        if len(all_file_vocabs) > 500:
            lang_groups: dict[str, list[FileVocab]] = defaultdict(list)
            for fv in all_file_vocabs:
                lang_groups[fv.language].append(fv)
            subset = []
            for fvs in lang_groups.values():
                subset.extend(fvs[:max(1, 500 // len(lang_groups))])
            source_fvs = subset[:500]
        for fv in source_fvs:
            co_matrix.add_file(_extract_identifiers(fv))
        clusters = co_matrix.cluster(min_cooccurrence=max(1, len(all_file_vocabs) // 50))
        cluster_labels_list = [cluster_labels(c) for c in clusters]
        structure_clusters_list = find_structure_clusters(all_file_vocabs, clusters, quiet=quiet)
    else:
        clusters = []
        cluster_labels_list = []
        structure_clusters_list = []

    clone_groups = _find_structural_clones(all_file_vocabs, max_files=min(100, len(all_file_vocabs))) if clones else []

    if not quiet:
        print("  Computing landmarks...", file=sys.stderr)
    landmarks = _compute_landmarks(all_file_vocabs) if deep else []

    dead_exports = _find_dead_exports(all_file_vocabs)

    result = CodebaseAnalysis(
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
    if len(_SCAN_CACHE) < _SCAN_CACHE_MAX:
        _SCAN_CACHE[key] = result
    return result


# ── Shared constants ──────────────────────────────────────────────

_DEAD_CODE_EXTS = frozenset({
    ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".rs", ".c", ".cpp", ".h", ".hpp", ".java",
    ".kt", ".kts", ".swift", ".rb", ".php",
    ".nix", ".ml", ".mli", ".erl", ".hrl",
    ".ex", ".exs", ".eex", ".heex",
    ".zig",
    ".hs", ".lhs",
    ".clj", ".cljs", ".cljc",
    ".sml", ".fs", ".fsx",
    ".r", ".jl", ".scala",
})


# ── Shared helpers ────────────────────────────────────────────────

_LANG_COMMON_CACHE: dict[str, set[str]] = {}


def _get_language_common(file_vocabs: list[FileVocab], top_n: int = 20) -> dict[str, set[str]]:
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


def _is_test_path(path: str) -> bool:
    base = os.path.basename(path).lower()
    parts = {p.lower() for p in path.replace("\\", "/").split("/")}
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
    if total_files <= 0 or file_count <= 0:
        return 0.0
    prevalence = file_count / total_files
    if prevalence > 0.45:
        return 0.0
    repeat = 2.0 / (1.0 + math.exp(-file_count / 4.0)) - 1.0
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


def _binding_concepts(analysis: CodebaseAnalysis, limit: int = 15) -> list[dict]:
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


def _mirror_signals(changed: list[str], all_file_vocabs: list[FileVocab]) -> dict:
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


def _is_lock_file(path: str) -> bool:
    base = os.path.basename(path)
    return base in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "Cargo.lock", "go.sum", "poetry.lock")


def _is_generated(path: str) -> bool:
    base = os.path.basename(path)
    parts = set(path.replace("\\", "/").split("/"))
    return (_skip_path(path)
            or base.startswith("zz_") or base == "zz_generated.go"
            or base.endswith(".pb.go") or base.endswith(".pb.ts")
            or ".min." in path or ".generated." in path
            or base.startswith("mock_") or base.startswith("_mock")
            or base in ("querier.go", "models.go") or "/sqlc/" in path
            or "mocks" in parts)


def _snapshot_phrases(analysis: CodebaseAnalysis) -> set[str]:
    phrases: set[str] = set()
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in _DEAD_CODE_EXTS:
            continue
        if _is_lock_file(fv.path) or _is_generated(fv.path):
            continue
        phrases.update(_extract_identifiers(fv))
    return phrases


# ── Search (cross-repo, ranked) ───────────────────────────────────

_SEARCH_CODE_EXTS = frozenset({
    ".go", ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".c", ".cpp",
    ".h", ".hpp", ".java", ".kt", ".rb", ".php", ".swift",
})


def search_cross_repo_ranked(phrase: str, repo_paths: list[str]) -> list[dict]:
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


# ── Structural clone detection ─────────────────────────────────────

def _find_structural_clones(file_vocabs: list[FileVocab],
                            threshold: float = 0.85,
                            max_files: int = 200) -> list[dict]:
    if len(file_vocabs) < 2:
        return []

    sampled = file_vocabs
    if len(file_vocabs) > max_files:
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


# ── Landmark computation ───────────────────────────────────────────

def _compute_landmarks(file_vocabs: list[FileVocab]) -> list[dict]:
    phrase_file_count: Counter[str] = Counter()
    file_phrases: list[tuple[str, set[str]]] = []
    for fv in file_vocabs:
        phrases = set(fv.vocabulary.keys())
        file_phrases.append((fv.path, phrases))
        for p in phrases:
            phrase_file_count[p] += 1

    len(file_vocabs) or 1
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


# ── Dead export detection ─────────────────────────────────────────

def _find_dead_exports(file_vocabs: list[FileVocab]) -> list[dict]:
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


# ── Structure clustering ───────────────────────────────────────────

def find_structure_clusters(file_vocabs: list[FileVocab], phrase_clusters: list[list[str]],
                            min_file_count: int = 3, quiet: bool = False) -> list[dict]:
    if not phrase_clusters or len(file_vocabs) < min_file_count:
        return []

    lang_common = _get_language_common(file_vocabs, top_n=20)

    structure_groups = []

    for i, cluster_phrases in enumerate(phrase_clusters):
        if len(cluster_phrases) < 2:
            continue

        file_scores: list[tuple[str, int, str]] = []
        for fv in file_vocabs:
            common = lang_common.get(fv.language, set())
            meaningful = [p for p in cluster_phrases
                          if p not in common and len(p) >= 3 and p not in common]
            if len(meaningful) < 2:
                meaningful = cluster_phrases

            matches = sum(1 for p in meaningful if p in fv.vocabulary)
            if matches >= 2:
                file_scores.append((fv.path, matches, fv.language))

        if len(file_scores) >= min_file_count:
            langs = Counter(l for _, _, l in file_scores)
            top_lang = langs.most_common(1)[0][0]
            test_count = sum(1 for f, _, _ in file_scores
                             if "_test." in f or "/tests/" in f or ".test." in f)

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

    structure_groups.sort(key=lambda x: -x["file_count"])
    return structure_groups
