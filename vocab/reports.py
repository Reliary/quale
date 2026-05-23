"""Reporting commands: ci_report, inspect_repo, repo_fingerprint, stability, lifecycles, timeline."""

from __future__ import annotations

import os
import re
from collections import defaultdict, Counter
from typing import TYPE_CHECKING

from vocab import git as vgit

if TYPE_CHECKING:
    from vocab.scanner import CodebaseAnalysis, FileVocab


# ── CI Report ─────────────────────────────────────────────────────

def ci_report(base_ref: str, head_ref: str, path: str = ".") -> dict:
    from vocab.scanner import scan_codebase, _mirror_signals

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    if vgit.has_commits(path):
        missing = [ref for ref in (base_ref, head_ref) if not vgit.ref_exists(path, ref)]
        if missing:
            return {"error": f"Unknown git ref(s): {', '.join(missing)}"}

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

    try:
        analysis = scan_codebase(path, git_ref=head_ref, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        analysis = None

    blast_results = []
    mirror = {}
    if analysis:
        from vocab.compare import pr_blast_radius
        radius = pr_blast_radius(changed, analysis.file_vocabs)
        blast_results = radius.get("impacts", [])
        mirror = _mirror_signals(changed, analysis.file_vocabs)

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


# ── Stability anchors ─────────────────────────────────────────────

def compute_stability(path: str, weeks: int = 12, min_appearances: int = 4) -> list[dict]:
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    from vocab.scanner import scan_codebase

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


# ── Lifecycles ────────────────────────────────────────────────────

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


def compute_lifecycles(path: str, weeks: int = 24) -> list[dict]:
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    from vocab.scanner import scan_codebase, _is_lock_file, _is_generated

    concept_weeks: dict[str, set[int]] = defaultdict(set)
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')

    previous_phrases: set[str] = set()
    rename_pairs: list[tuple[str, str, int]] = []

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

        if previous_phrases and week_idx > 0:
            disappeared = previous_phrases - current_phrases
            appeared = current_phrases - previous_phrases
            if disappeared and appeared:
                for old in list(disappeared)[:5]:
                    old_base = old.replace("V1", "").replace("V2", "").replace("V3", "").replace("V4", "").replace("V5", "")
                    old_base = old_base.replace("Old", "").replace("Legacy", "")
                    for new in list(appeared)[:5]:
                        if old_base and (old_base in new or new.replace("New", "").replace("V2", "").replace("V3", "") == old_base):
                            rename_pairs.append((old, new, week_idx))

        previous_phrases = current_phrases

    total_weeks = len(week_data)
    lifecycles = []

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
        if _COMMON_TOKEN.match(concept):
            continue
        first = min(weeks_present)
        last = max(weeks_present)
        age = total_weeks - first
        stale = total_weeks - last
        appearances = len(weeks_present)
        ratio = appearances / max(total_weeks, 1)

        renamed_to = [n for o, n, _ in rename_pairs if o == concept]
        renamed_from = [o for o, n, _ in rename_pairs if n == concept]

        has_gap = False
        if len(weeks_present) >= 2:
            sorted_weeks = sorted(weeks_present)
            for i in range(len(sorted_weeks) - 1):
                if sorted_weeks[i + 1] - sorted_weeks[i] > 4:
                    has_gap = True
                    break

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


# ── Concept timeline ──────────────────────────────────────────────

def concept_timeline(path: str, weeks: int = 12) -> list[dict]:
    if not vgit.is_repo(path):
        return []
    weeks_data = vgit.weekly_commits(path, weeks=weeks)
    timeline = []
    prev_phrases: set[str] = set()

    from vocab.scanner import scan_codebase

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


# ── Inspect repo ──────────────────────────────────────────────────

def inspect_repo(path: str) -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from vocab.scanner import scan_codebase, _binding_concepts
    from vocab.bootstrap import explore_repo, compute_modules

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    explore_data = explore_repo(path, themes=True, analysis=analysis)
    modules_data = compute_modules(path, analysis=analysis)
    timeline_data = concept_timeline(path, weeks=4)
    binding = _binding_concepts(analysis)

    try:
        lifecycle_data = compute_lifecycles(path, weeks=24)
        if lifecycle_data:
            ages = [l["age_weeks"] for l in lifecycle_data if l["signal"] in ("STABLE", "ACTIVE", "DEAD")]
            avg_age = round(sum(ages) / max(len(ages), 1), 1) if ages else 0
        else:
            avg_age = 0
    except Exception:
        avg_age = 0

    # Debt candidates: files with low uniqueness + low identifier coverage
    # debt = (1 - normalized_uniqueness) * (1 - coverage)
    # High debt = generic file with churn potential
    files = explore_data.get("files", [])
    debt_candidates = []
    for f in files:
        unique = f.get("unique_score", 0)
        coverage = f.get("coverage", 0)
        norm_unique = min(unique / 30, 1.0)
        debt = round((1.0 - norm_unique) * max(0.0, 1.0 - coverage), 3)
        if debt >= 0.5:
            debt_candidates.append({
                "file": f["file"],
                "debt": debt,
                "unique_score": round(unique, 1),
                "coverage": round(coverage, 3),
                "language": f.get("language", "?"),
            })
    debt_candidates.sort(key=lambda x: -x["debt"])

    return {
        "schema_version": 1,
        "explore": explore_data,
        "modules": modules_data,
        "binding_concepts": binding,
        "timeline": timeline_data,
        "avg_concept_age_weeks": avg_age,
        "debt_candidates": debt_candidates[:15],
    }


# ── Repo fingerprint ──────────────────────────────────────────────

def repo_fingerprint(path: str) -> dict:
    from vocab.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)

    import hashlib
    combined = hashlib.sha256()
    total_indices = 0

    for fv in sorted(analysis.file_vocabs, key=lambda x: x.path):
        indices = list(fv.vocabulary.values())
        if not indices:
            continue
        total_indices += len(indices)
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
