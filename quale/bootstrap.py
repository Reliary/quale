"""Agent onboarding commands: bootstrap, explore, modules, task planning."""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import TYPE_CHECKING

from quale import git as vgit

if TYPE_CHECKING:
    from quale.scanner import CodebaseAnalysis, FileVocab


_EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')


# ── Explore / onboarding map ──────────────────────────────────────

def explore_repo(path: str, themes: bool = False, analysis: CodebaseAnalysis | None = None) -> dict:
    if analysis is None:
        from quale.scanner import scan_codebase
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"files": [], "themes": []}

    from quale.scanner import _DEAD_CODE_EXTS, _is_lock_file, _is_generated
    from quale.bootstrap import _task_file_role

    identifier_file_count: Counter[str] = Counter()
    file_identifiers: list[tuple[str, str, set[str]]] = []

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
        is_generated_flag = _is_generated(path)
        gen_penalty = 0.3 if is_generated_flag else 1.0

        role = _task_file_role(path)
        role_penalty = {"source": 1.0, "header": 0.6, "script": 0.6, "example": 0.3, "test": 0.3, "minified": 0.1, "benchmark": 0.3}.get(role, 0.5)

        unique_score = sum(1 / max(identifier_file_count[i], 1) for i in identifiers) * gen_penalty * role_penalty

        ident_count = len(identifiers)
        distinctive = sorted(identifiers, key=lambda i: identifier_file_count.get(i, 999))[:3]

        scored.append({
            "file": path,
            "language": lang,
            "identifiers": ident_count,
            "unique_score": round(unique_score, 2),
            "coverage": round(ident_count / max(len(identifier_file_count), 1), 4),
            "distinctive_ids": distinctive,
        })

    scored.sort(key=lambda x: -x["unique_score"])

    result: dict = {"files": scored[:20], "themes": [], "total_code_files": total_files, "schema_version": 1}

    if themes and len(file_identifiers) >= 10:
        result["themes"] = _compute_themes(file_identifiers)

    return result


def _binding_concepts_from_analysis(analysis, limit=10):
    """Get binding concepts from a shared scan analysis."""
    from quale.scanner import _binding_concepts
    return _binding_concepts(analysis, limit=limit)


def _distinctive_ids(file_identifiers: list[tuple[str, str, set[str]]],
                     identifier_file_count: Counter[str],
                     file_path: str) -> list[str]:
    matched = None
    for fpath, _, idents in file_identifiers:
        if fpath == file_path:
            matched = idents
            break
    if not matched:
        return []
    sorted_ids = sorted(matched, key=lambda i: identifier_file_count.get(i, 999))
    return sorted_ids[:3]


def _compute_themes(file_identifiers: list[tuple[str, str, set[str]]]) -> list[dict]:
    df: Counter[str] = Counter()
    for _, _, idents in file_identifiers:
        for ident in idents:
            df[ident] += 1

    total_files = len(file_identifiers) or 1

    mid_freq = {ident for ident, count in df.items()
                if 2 <= count <= total_files * 0.6 and count >= 2}

    if len(mid_freq) < 10:
        return []

    ident_files: dict[str, set[int]] = {}
    for idx, (_, _, idents) in enumerate(file_identifiers):
        for ident in idents & mid_freq:
            ident_files.setdefault(ident, set()).add(idx)

    from collections import defaultdict
    co_occurrence: dict[tuple[str, str], int] = Counter()
    for idx, (_, _, idents) in enumerate(file_identifiers):
        file_mid = sorted(idents & mid_freq)
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


# ── TDA Module Detection ──────────────────────────────────────────

def compute_modules(path: str, analysis: CodebaseAnalysis | None = None) -> dict:
    if analysis is None:
        from quale.scanner import scan_codebase
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)

    from quale.scanner import _DEAD_CODE_EXTS, _is_lock_file, _is_generated

    token = re.compile(r'\b[A-Z][A-Za-z0-9_]{4,40}\b')

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
            for m in token.finditer(phrase):
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

    from collections import defaultdict
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


# ── Task planning ─────────────────────────────────────────────────

def _task_file_role(path: str) -> str:
    parts = [p.lower() for p in path.replace("\\", "/").split("/")]
    if any(x in parts for x in ("test", "tests", "testdata")):
        from quale.scanner import _is_test_path
        if _is_test_path(path):
            return "test"
    if "examples" in parts or "example" in parts:
        return "example"
    if "scripts" in parts or "script" in parts:
        return "script"
    if "benchmarks" in parts or "benchmark" in parts or "asv_bench" in parts:
        return "benchmark"
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("h", "hpp", "hxx"):
        return "header"
    if path.lower().endswith((".min.js", ".min.css")):
        return "minified"
    return "source"


def _task_role_rank(role: str) -> int:
    return {"source": 0, "header": 0, "script": 1, "example": 2, "test": 3}.get(role, 4)


def _compute_agent_notes(path: str, explore_data: dict, modules_data: dict,
                          stability_data: list[dict]) -> list[str]:
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


def bootstrap_repo(path: str, task: str | None = None) -> dict:
    from quale.scanner import scan_codebase, _is_generated, _code_file_vocabs

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    explore_data = explore_repo(path, themes=True, analysis=analysis)
    modules_data = compute_modules(path, analysis=analysis)

    stable_data = []
    try:
        file_count = len(vgit.list_files(path, ref=None))
    except Exception:
        file_count = 0
    if file_count > 2000:
        pass
    else:
        from quale.reports import compute_stability
        stable_data = compute_stability(path, weeks=12)
    stability_data = stable_data

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
            "distinctive_ids": f.get("distinctive_ids", []),
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
                "distinctive_ids": f.get("distinctive_ids", []),
            })

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

    related = []
    keywords = []
    if task:
        task_lower = task.lower()
        keywords = [w for w in task_lower.split() if len(w) > 3 and w not in
                    {"this", "that", "with", "from", "what", "which", "there", "their", "about", "would", "could", "should", "after", "before", "into", "over", "such", "only", "other", "than", "then", "also", "very", "just", "like", "some", "more", "they", "been", "when", "where"}]
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
    task_relevance_score = 0.0
    if related and keywords:
        for item in related:
            filepath = item["file"]
            try:
                with open(os.path.join(path, filepath), "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().lower()
            except Exception:
                unverified_files.append(filepath)
                continue
            if any(keyword in content for keyword in keywords[:5]):
                verified_files.append(filepath)
            else:
                unverified_files.append(filepath)
        task_relevance_score = len(verified_files) / max(len(related), 1)

    # T2: Negative file set — files sharing ZERO concepts with task
    low_relevance = []
    if keywords and analysis:
        lowered = [k.lower() for k in keywords if len(k) >= 4]
        from quale.scanner import _code_file_vocabs, _is_generated, _is_lock_file
        related_paths = {r["file"] for r in related}
        for fv in _code_file_vocabs(analysis):
            if fv.path in related_paths:
                continue
            if _is_generated(fv.path) or _is_lock_file(fv.path):
                continue
            haystack = f"{fv.path} " + " ".join(fv.vocabulary.keys())
            hay = haystack.lower()
            if not any(kw in hay for kw in lowered):
                low_relevance.append(fv.path)
        low_relevance = low_relevance[:5] if len(low_relevance) <= 5 else (low_relevance[:5] if analysis.total_files < 500 else [])
        if not low_relevance or analysis.total_files < 30:
            low_relevance = []

    notes = _compute_agent_notes(path, explore_data, modules_data, stability_data)
    themes_out = explore_data.get("themes", [])
    task_plan = _task_plan(task, related, reads, modules_data, stability_data)

    # Binding concepts
    bc = _binding_concepts_from_analysis(analysis, limit=10)

    # Enrich related items with distinctive_ids from explore data
    id_file_map: dict[str, list[str]] = {}
    for f_entry in explore_data.get("files", []):
        if f_entry.get("distinctive_ids"):
            id_file_map[f_entry["file"]] = f_entry["distinctive_ids"]
    for item in related:
        fp = item.get("file", "")
        if fp in id_file_map:
            item["distinctive_ids"] = id_file_map[fp]

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
        "binding_concepts": bc,
        "themes": themes_out,
        "agent_notes": notes,
        "total_code_files": explore_data.get("total_code_files", 0),
        "low_relevance_files": low_relevance,
    }


def _rank_related_files(path: str, keywords: list[str], analysis: CodebaseAnalysis | None = None) -> list[dict]:
    from quale.scanner import scan_codebase, _code_file_vocabs, _is_generated

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
            score -= 2
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

    # Second pass: identifier-sharing boost
    # Files matching no keywords may still be structurally relevant (e.g. pattern files).
    # If they share distinctive identifiers with the top-ranked keyword match, pull them in.
    if analysis and source_matches:
        top_keyword_file = source_matches[0]["file"]
        top_keyword_ids = set()
        for fv in _code_file_vocabs(analysis):
            if fv.path == top_keyword_file:
                top_keyword_ids = set(fv.vocabulary.keys())
                break
        scored_paths = set(scores.keys())
        extra: list[dict] = []
        for fv in _code_file_vocabs(analysis):
            if fv.path in scored_paths:
                continue
            role = _task_file_role(fv.path)
            if role in ("test", "script", "example", "minified"):
                continue
            if _is_generated(fv.path):
                continue
            fv_ids = set(fv.vocabulary.keys())
            shared = len(top_keyword_ids & fv_ids)
            if shared >= 6:
                extra.append({
                    "file": fv.path,
                    "phrase": "structural-match",
                    "matches": shared,
                    "role": role,
                })
        extra.sort(key=lambda x: -x["matches"])
        source_matches = (source_matches + extra)[:10]

    return source_matches + test_matches


def _task_plan(task: str | None, related: list[dict], reads: list[dict],
               modules_data: dict, stability_data: list[dict]) -> dict:
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
