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
                  clones: bool = False) -> CodebaseAnalysis:
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
    co_matrix = CoOccurrenceMatrix()
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

    if not quiet:
        print(f"  Co-occurrence matrix ({min(len(all_file_vocabs), 500)} files)...", file=sys.stderr)
    if len(all_file_vocabs) > 500:
        # Stratified sample by language
        lang_groups: dict[str, list[FileVocab]] = defaultdict(list)
        for fv in all_file_vocabs:
            lang_groups[fv.language].append(fv)
        subset = []
        for fvs in lang_groups.values():
            subset.extend(fvs[:max(1, 500 // len(lang_groups))])
        for fv in subset[:500]:
            co_matrix.add_file(set(fv.vocabulary.keys()))
    clusters = co_matrix.cluster(min_cooccurrence=max(1, len(all_file_vocabs) // 50))

    # Structural clones — opt-in (O(N²) pairwise). Off by default for speed.
    clone_groups = _find_structural_clones(all_file_vocabs, max_files=min(100, len(all_file_vocabs))) if clones else []

    if not quiet:
        print(f"  Computing landmarks...", file=sys.stderr)
    landmarks = _compute_landmarks(all_file_vocabs)

    # Dead exports — sample-based for large repos
    dead_exports = _find_dead_exports(all_file_vocabs)

    # Label clusters for readability
    cluster_labels_list = [cluster_labels(c) for c in clusters]

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

    dead = []
    for phrase, filepath in sorted(single_file.items(), key=lambda x: -len(x[0])):
        dead.append({"phrase": phrase, "file": filepath})
    return dead[:50]


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
