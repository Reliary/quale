"""Cross-repo comparison commands: compare, provenance, blast radius."""

from __future__ import annotations

import os
from collections import defaultdict, Counter
from typing import TYPE_CHECKING

from vocab import git as vgit

if TYPE_CHECKING:
    from vocab.scanner import CodebaseAnalysis, FileVocab


# ── Compare repos ─────────────────────────────────────────────────

def compare_repos(repo_a: str, repo_b: str) -> dict:
    from vocab.scanner import scan_codebase, _snapshot_phrases, _identifier_file_map, _code_file_vocabs, _structural_information_score, _is_test_path

    analysis_a = scan_codebase(repo_a, quiet=True, max_files=2500, max_seconds=30)
    analysis_b = scan_codebase(repo_b, quiet=True, max_files=2500, max_seconds=30)

    phrases_a = _snapshot_phrases(analysis_a)
    phrases_b = _snapshot_phrases(analysis_b)
    files_a, langs_a = _identifier_file_map(analysis_a, include_tests=False)
    files_b, langs_b = _identifier_file_map(analysis_b, include_tests=False)

    shared = phrases_a & phrases_b
    only_a = phrases_a - phrases_b
    only_b = phrases_b - phrases_a

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
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    from vocab.scanner import scan_codebase, _is_lock_file, _is_generated

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


# ── Blast radius ──────────────────────────────────────────────────

def _extract_identifiers(fv: FileVocab, min_len: int = 4) -> set[str]:
    import re
    from vocab.scanner import _is_actionable_identifier
    token = re.compile(rf'\b[A-Z][A-Za-z0-9_]{{{min_len - 1},40}}\b')
    identifiers: set[str] = set()
    for phrase in fv.vocabulary:
        for match in token.finditer(phrase):
            ident = match.group()
            if _is_actionable_identifier(ident):
                identifiers.add(ident)
    return identifiers


def pr_blast_radius(pr_files: list[str], all_file_vocabs: list[FileVocab],
                    max_results: int = 200, code_only: bool = True) -> dict:
    pr_set = set(pr_files)

    if code_only:
        all_file_vocabs = [fv for fv in all_file_vocabs
                          if os.path.splitext(fv.path)[1].lower() in _DEAD_CODE_EXTS]

    pr_vocab: set[str] = set()
    for fv in all_file_vocabs:
        if fv.path in pr_set:
            pr_vocab.update(_extract_identifiers(fv))

    if not pr_vocab:
        return {"impacts": [], "rename_warnings": []}

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
