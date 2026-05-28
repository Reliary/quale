"""Structural analysis helpers: spectral, deficit, cascade, boundary, risk, etc.

These are pure functions that take an `analysis` object (from scan_codebase)
and return dicts. They have no side effects and are tested independently.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from quale import git as vgit


def _safe_islands_data(analysis) -> list[str]:
    """Find structurally isolated blocks safe to edit."""
    safe: list[str] = []
    dir_counts: dict[str, int] = {}
    for fv in analysis.file_vocabs:
        d = os.path.dirname(fv.path) or "."
        dir_counts.setdefault(d, 0)
        dir_counts[d] += len(fv.vocabulary)
    dir_files: dict[str, int] = {}
    for fv in analysis.file_vocabs:
        d = os.path.dirname(fv.path) or "."
        dir_files[d] = dir_files.get(d, 0) + 1
    avg_phrases = sum(dir_counts.values()) / max(len(dir_counts), 1)
    for d, count in sorted(dir_counts.items(), key=lambda x: -x[1]):
        parts = d.split(os.path.sep)
        if any(p.startswith(".") for p in parts if p):
            continue
        if count < avg_phrases * 0.3 and dir_files.get(d, 0) <= 3:
            safe.append(d)
    return sorted(safe)[:10]


def _spectrum_analysis(file: str, analysis) -> dict:
    """Classify identifiers into DC (codebase-wide), LF (module), HF (file-specific) bands."""
    from quale.scanner import _extract_identifiers
    co = analysis.co_occurrence
    if not co:
        return {"band": "unknown", "hf_ids": [], "lf_ids": [], "pct_hf": 0}
    fv = None
    for v in analysis.file_vocabs:
        if v.path == file:
            fv = v
            break
    if not fv:
        return {"band": "unknown", "hf_ids": [], "lf_ids": [], "pct_hf": 0}
    total = max(co.total_docs, 1)
    dc_threshold = max(int(total ** 0.5), 3)
    module_files: set[str] = set()
    for sc in (analysis.structure_clusters or []):
        if file in sc.get("top_files", []):
            module_files.update(sc.get("top_files", []))
            break
    file_identifiers = _extract_identifiers(fv, min_len=4)
    if len(file_identifiers) < 3:
        for phrase in fv.vocabulary:
            if any(c.isupper() for c in phrase) and len(phrase) >= 4:
                file_identifiers.add(phrase)
        if len(file_identifiers) < 3:
            return {"hf_ids": [], "lf_ids": [], "pct_hf": 0, "pct_lf": 0, "pct_dc": 0, "note": "file too small"}
    hf_ids: list[str] = []
    lf_ids: list[str] = []
    dc_ids: list[str] = []
    for ident in file_identifiers:
        doc_count = co.phrase_count.get(ident, 0)
        if doc_count >= dc_threshold:
            dc_ids.append(ident)
        elif module_files:
            module_count = sum(1 for v2 in analysis.file_vocabs
                               if v2.path in module_files and ident in v2.vocabulary
                               or any(ident in seg for seg in v2.vocabulary))
            mod_frac = module_count / max(len(module_files), 1)
            if mod_frac > 0.2:
                lf_ids.append(ident)
            else:
                hf_ids.append(ident)
        elif doc_count <= 3:
            hf_ids.append(ident)
        else:
            lf_ids.append(ident)
    total_ids = max(len(file_identifiers), 1)
    return {
        "hf_ids": hf_ids[:10], "lf_ids": lf_ids[:5],
        "pct_hf": round(len(hf_ids) / total_ids * 100),
        "pct_lf": round(len(lf_ids) / total_ids * 100),
        "pct_dc": round(len(dc_ids) / total_ids * 100),
        "instruction": "Read HF identifiers first; DC band is codebase-wide boilerplate",
    }


def _deficit_analysis(file: str, analysis) -> dict | None:
    """Find module-level identifiers missing from a file."""
    if not analysis.structure_clusters:
        return None
    module = None
    for sc in analysis.structure_clusters:
        if file in sc.get("top_files", []):
            module = sc
            break
    if not module:
        return None
    module_files_list = module.get("top_files", [])
    if len(module_files_list) < 5:
        return None
    mod_counts: Counter = Counter()
    for v in analysis.file_vocabs:
        if v.path in module_files_list and v.path != file:
            for phrase in v.vocabulary:
                mod_counts[phrase] += 1
    mod_total = max(len(module_files_list) - 1, 1)
    file_phrases = set()
    for v in analysis.file_vocabs:
        if v.path == file:
            file_phrases = set(v.vocabulary.keys())
            break
    missing: list[tuple[str, int]] = []
    for phrase, count in mod_counts.most_common(20):
        if phrase not in file_phrases:
            score = count / mod_total
            if score >= 0.5:
                missing.append((phrase, count))
    critical = [p for p, c in missing[:3]] if missing else []
    if not missing:
        return None
    return {
        "missing": len(missing),
        "missing_ids": [p for p, _ in missing[:5]],
        "critical": critical,
        "instruction": "Verify this file belongs in its detected module" if critical else None,
    }


def _cascade_analysis(file: str, analysis, blast: list[dict]) -> dict | None:
    """Transitive coupling: structural disruption beyond direct blast radius."""
    if not blast:
        return None
    all_vocabs: dict[str, set[str]] = {}
    for v in analysis.file_vocabs:
        all_vocabs[v.path] = set(v.vocabulary.keys())
    visited: set[str] = set()
    visited_with_depth: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(file, 0)]
    transitive_count = 0
    total_damped = 0.0
    alpha = 0.5
    while queue:
        current, depth = queue.pop(0)
        if current in visited or depth > 5:
            continue
        visited.add(current)
        visited_with_depth[current] = depth
        if depth > 0:
            transitive_count += 1
            total_damped += alpha ** depth
        current_ids = all_vocabs.get(current, set())
        for item in blast:
            bfile = item.get("file", "")
            if bfile in visited:
                continue
            bf_ids = all_vocabs.get(bfile, set())
            if current_ids and bf_ids:
                jaccard = len(current_ids & bf_ids) / max(len(current_ids | bf_ids), 1)
                if jaccard > 0.05:
                    queue.append((bfile, depth + 1))
        if analysis.structure_clusters:
            for sc in analysis.structure_clusters:
                if current in sc.get("top_files", []):
                    for peer in sc.get("top_files", []):
                        if peer not in visited and peer not in [q[0] for q in queue]:
                            queue.append((peer, depth + 1))
    if transitive_count == 0:
        return None
    return {
        "hops": max(visited_with_depth.values()),
        "transitive_files": transitive_count,
        "damped_score": round(total_damped, 2),
        "ratio_to_blast": round(total_damped / max(len(blast), 1), 2),
        "instruction": "Transitive risk exceeds direct risk — verify expansion scope",
    }


def _cross_cutting_concerns(file: str, analysis) -> list[dict]:
    """Cross-cutting concern detection via identifier-cluster voting."""
    from quale.scanner import _extract_identifiers
    clusters_map: dict[str, set[str]] = {}
    for sc in (analysis.structure_clusters or []):
        label = sc.get("label", "?")
        clusters_map[label] = set(sc.get("top_files", []))
    if len(clusters_map) < 2:
        return []
    co = analysis.co_occurrence
    if not co:
        return []
    concerns: list[dict] = []
    seen_ids: set[str] = set()
    for ident, doc_count in co.phrase_count.items():
        if doc_count < 3 or ident in seen_ids:
            continue
        found_clusters: list[str] = []
        found_files: set[str] = set()
        for label, files in clusters_map.items():
            for fv in analysis.file_vocabs:
                if fv.path in files and ident in _extract_identifiers(fv, min_len=4):
                    found_clusters.append(label)
                    found_files.add(fv.path)
                    break
        if len(found_clusters) >= 2 and len(found_files) >= 4:
            concerns.append({
                "id": ident,
                "cluster_span": len(found_clusters),
                "file_span": len(found_files),
                "clusters": found_clusters[:5],
            })
            seen_ids.add(ident)
    concerns.sort(key=lambda x: (-x["file_span"], -x["cluster_span"]))
    return concerns[:5]


def _risk_vector(stable_touched: list[dict], blast: list[dict],
                 cascade: dict | None, deficit: dict | None) -> dict:
    """Softmax-normalized risk decomposition."""
    weights = {"cascade": 2.0, "stable": 1.5, "deficit": 1.2, "blast": 1.0}
    raw = {
        "cascade": (cascade or {}).get("damped_score", 0) * weights["cascade"],
        "stable": len(stable_touched) * weights["stable"],
        "deficit": (deficit or {}).get("missing", 0) * weights["deficit"],
        "blast": len(blast) * weights["blast"],
    }
    total = sum(raw.values()) or 1.0
    vector = {k: round(v / total, 2) for k, v in raw.items()}
    dominant = max(vector, key=vector.get)
    desc_map = {
        "cascade": "Transitive risk exceeds direct — check expansion scope",
        "stable": "Stable anchors touched — risk from entrenched code",
        "deficit": "Missing module identifiers — risk from misplacement",
        "blast": "Direct blast coupling — risk from immediate dependents",
    }
    return {
        "vector": vector, "dominant": dominant,
        "description": desc_map.get(dominant, ""),
        "instruction": f"Primary risk: {desc_map.get(dominant, '')}",
    }


def _change_acceleration(file: str, path: str) -> dict | None:
    """Second derivative of change frequency: acceleration spike detection."""
    if not vgit.is_repo(path):
        return None
    try:
        week_data = vgit.weekly_commits(path, weeks=24)
    except Exception:
        return None
    if len(week_data) < 4:
        return None
    counts: list[int] = []
    for wk in week_data:
        cnt = sum(1 for sha in wk.get("shas", [])
                  if sha and _file_in_commit(file, sha, path))
        counts.append(cnt)
    velocity = [counts[i + 1] - counts[i] for i in range(len(counts) - 1)]
    acceleration = [velocity[i + 1] - velocity[i] for i in range(len(velocity) - 1)]
    if not acceleration:
        return None
    peak_vel = max(velocity) if velocity else 0
    mean_abs_vel = sum(abs(v) for v in velocity) / max(len(velocity), 1)
    spike_ratio = peak_vel / max(mean_abs_vel, 0.01)
    recent_trend = sum(acceleration[-3:]) / max(len(acceleration[-3:]), 1) if len(acceleration) >= 3 else 0
    trend = "accelerating" if recent_trend > 0.5 else \
            "decelerating" if recent_trend < -0.5 else "stable"
    if spike_ratio < 2:
        return None
    return {
        "peak_velocity": peak_vel,
        "spike_ratio": round(spike_ratio, 1),
        "trend": trend,
        "instruction": f"Change acceleration spike ({spike_ratio:.1f}x normal)" if spike_ratio >= 3 else None,
    }


def _file_in_commit(file: str, sha: str, path: str) -> bool:
    """Quick check if file changed in a commit via git diff-tree."""
    try:
        out = vgit._git_bytes("diff-tree", "--no-commit-id", "-r", "--name-only",
                               sha, cwd=path)
        return file.encode() in out.split(b"\n")
    except Exception:
        return False


def _boundary_entropy(file: str, analysis) -> dict | None:
    """Shannon entropy of cluster membership probabilities."""
    from quale.scanner import _extract_identifiers
    scs = analysis.structure_clusters
    if not scs or len(scs) < 2:
        return None
    file_ids = set(_extract_identifiers(
        next((fv for fv in analysis.file_vocabs if fv.path == file), None),
        min_len=4)) if any(fv.path == file for fv in analysis.file_vocabs) else set()
    if not file_ids:
        return None
    probs: list[float] = []
    cluster_labels_list: list[str] = []
    for sc in scs:
        module_files = set(sc.get("top_files", []))
        module_ids: set[str] = set()
        for fv in analysis.file_vocabs:
            if fv.path in module_files:
                module_ids |= _extract_identifiers(fv, min_len=4)
        shared = len(file_ids & module_ids)
        p = shared / max(len(file_ids), 1)
        if p > 0:
            probs.append(p)
            cluster_labels_list.append(sc.get("label", "?"))
    if not probs:
        return None
    total_p = sum(probs)
    probs = [p / total_p for p in probs]
    entropy = -sum(p * __import__("math").log2(p) for p in probs)
    max_entropy = __import__("math").log2(max(len(probs), 1))
    norm = round(entropy / max_entropy, 2) if max_entropy > 0 else 0.0
    ranked = sorted(zip(cluster_labels_list, probs, strict=False), key=lambda x: -x[1])[:3]
    if norm < 0.3:
        return None
    return {
        "entropy": norm,
        "top_clusters": [r[0] for r in ranked],
        "top_probs": [round(r[1], 2) for r in ranked],
        "instruction": f"Boundary entropy {norm} — file sits between {', '.join(r[0] for r in ranked)}",
    }


def _module_exposure_analysis(file: str, analysis) -> dict | None:
    """Inside-out module boundary: identifiers reaching outside own module."""
    from quale.scanner import _extract_identifiers
    scs = analysis.structure_clusters
    if not scs:
        return None
    module = None
    for sc in scs:
        if file in sc.get("top_files", []):
            module = sc
            break
    if not module:
        return None
    module_files_list = module.get("top_files", [])
    if len(module_files_list) < 3:
        return None
    file_fv = next((fv for fv in analysis.file_vocabs if fv.path == file), None)
    if not file_fv:
        return None
    file_ids = _extract_identifiers(file_fv, min_len=4)
    mod_count: Counter = Counter()
    for fv in analysis.file_vocabs:
        if fv.path in module_files_list and fv.path != file:
            for ident in _extract_identifiers(fv, min_len=4):
                mod_count[ident] += 1
    mod_total = max(len(module_files_list) - 1, 1)
    common = {ident for ident, cnt in mod_count.items()
              if cnt / mod_total >= 0.5}
    everted = [i for i in file_ids if i not in common]
    ratio = len(everted) / max(len(file_ids), 1)
    if ratio < 0.3:
        return None
    return {
        "everted_ids": everted[:8], "everted_count": len(everted),
        "ratio": round(ratio, 2), "module": module.get("label", "?"),
        "instruction": f"{len(everted)}/{len(file_ids)} identifiers reach outside module ({ratio:.0%})",
    }


def _fused_priority_ranking(changed: list[str], blast: list[dict], mirror: dict) -> list[str]:
    """Fused blast + mirror ranking. score = blast + mirror + 2*b*m."""
    blast_scores: dict[str, int] = {}
    for item in blast:
        blast_scores[item.get("file", "")] = item.get("shared_concepts", 1)
    max_blast = max(blast_scores.values()) if blast_scores else 1
    mirror_files: set[str] = set()
    if mirror:
        mirror_files = set(mirror.get("files", []))
    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for f in changed:
        seen.add(f)
    for f, b_score in blast_scores.items():
        if f in seen:
            continue
        seen.add(f)
        b = b_score / max_blast
        m = 1.0 if f in mirror_files else 0.0
        sheared = b + m + 2.0 * b * m
        scored.append((sheared, f))
    for f in mirror_files:
        if f not in seen:
            seen.add(f)
            scored.append((1.0, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored]


def _file_temperature(file: str, lifecycle_data: list[dict], stability_data: list[dict], entropy_data: dict | None) -> str:
    """Compute single-word temperature: COLD/WARM/HOT."""
    for item in lifecycle_data:
        if item.get("file") == file:
            signal = item.get("signal", "")
            if signal in ("EMERGING", "GROWING", "EVOLVING", "SPORADIC"):
                return "HOT"
            if signal in ("DECAYING", "ABANDONED", "DEAD"):
                return "COLD"
            if signal in ("STABLE", "RENAMED"):
                return "WARM"
    for item in stability_data:
        if item.get("file") == file and item.get("persistence", 0) >= 0.8:
            return "COLD"
    return "WARM"


def _peer_relative_risk(changed: list[str], blast: list[dict]) -> dict:
    """Compare blast radius against typical edit."""
    total_shared = sum(item.get("shared_concepts", 0) for item in blast[:10])
    total_impacted = len(blast)
    multiplier = round(max(total_impacted, 1) / 1.2, 2)
    return {
        "blast_file_count": total_impacted,
        "total_shared_concepts": total_shared,
        "vs_median_multiplier": multiplier,
        "peer_text": f"{multiplier}x broader than median edit (blast:{total_impacted})",
    }


def _safety_envelope(changed: list[str], blast: list[dict], stable_touched: list[dict]) -> dict:
    """Define safety envelope: inside/at-boundary/outside."""
    inside = list(changed)
    boundary = list(dict.fromkeys(
        [item.get("file", "") for item in blast[:10] if item.get("file") not in inside]
    ))
    return {
        "inside": inside[:5],
        "at_boundary": boundary[:5],
        "boundary_count": len(boundary),
        "stable_on_boundary": [s["file"] for s in stable_touched[:3] if s.get("file") not in inside],
    }
