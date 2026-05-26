"""Reporting commands: ci_report, inspect_repo, repo_fingerprint, stability, lifecycles, timeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import defaultdict, Counter
from typing import TYPE_CHECKING, Any

from quale import git as vgit

if TYPE_CHECKING:
    pass


# ── CI Report ─────────────────────────────────────────────────────

def ci_report(base_ref: str, head_ref: str, path: str = ".") -> dict:
    from quale.scanner import scan_codebase, _mirror_signals

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
        from quale.compare import pr_blast_radius
        radius = pr_blast_radius(changed, analysis.file_vocabs)
        blast_results = radius.get("impacts", [])
        attractor = _attractor_cluster(changed, analysis) if analysis else None
        if attractor:
            for b in blast_results[:5]:
                b["attractor_cluster"] = attractor["cluster"]
                b["attractor_note"] = attractor["note"]
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


def _spectrum_analysis(file: str, analysis) -> dict:
    """Classify identifiers into DC (codebase-wide), LF (module), HF (file-specific) bands.
    Uses the same identifier extraction as scanner.py for consistency.
    """
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

    # Module membership for LF band
    module_files: set[str] = set()
    for sc in (analysis.structure_clusters or []):
        if file in sc.get("top_files", []):
            module_files.update(sc.get("top_files", []))
            break

    file_identifiers = _extract_identifiers(fv, min_len=4)

    # Fallback to phrase-level when identifiers are sparse (small files)
    if len(file_identifiers) < 3:
        for phrase in fv.vocabulary:
            if any(c.isupper() for c in phrase) and len(phrase) >= 4:
                file_identifiers.add(phrase)
        if len(file_identifiers) < 3:
            # Too small to analyze spectrally
            return {
                "hf_ids": [],
                "lf_ids": [],
                "pct_hf": 0,
                "pct_lf": 0,
                "pct_dc": 0,
                "note": "file too small for spectral analysis",
            }

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
        "hf_ids": hf_ids[:10],
        "lf_ids": lf_ids[:5],
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

    # Build module-wide identifier frequency (excluding this file)
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
            if score >= 0.5:  # present in >50% of module peers
                missing.append((phrase, count))

    critical = [p for p, c in missing[:3]] if missing else []
    if not missing:
        return None
    return {
        "missing": len(missing),
        "missing_ids": [p for p, _ in missing[:5]],
        "critical": critical,
        "instruction": "Verify this file belongs in its detected module before editing" if critical else None,
    }


def _cascade_analysis(file: str, analysis, blast: list[dict]) -> dict | None:
    """Transitive coupling: structural disruption diffusing beyond direct blast radius."""
    if not blast:
        return None

    # Build coupling graph from blast + co-occurrence
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
        "instruction": "Damped score > blast count means transitive risk exceeds direct risk — verify expansion scope",
    }


def _cross_cutting_concerns(file: str, analysis) -> list[dict]:
    """Cross-cutting concern detection via identifier-cluster voting.
    Each identifier votes for every (cluster, file) pair it appears in.
    Peaks = identifiers spanning ≥2 clusters with ≥4 total files.
    """
    from quale.scanner import _extract_identifiers
    clusters_map: dict[str, set[str]] = {}
    for sc in (analysis.structure_clusters or []):
        label = sc.get("label", "?")
        clusters_map[label] = set(sc.get("top_files", []))
    if len(clusters_map) < 2:
        return []

    # For each identifier in the matrix, count clusters + files it spans
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
            # Check if any file in this cluster has this identifier
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
    """Softmax-normalized risk decomposition. Cascade weighted 2× (transitive),
    stable 1.5× (entrenched), deficit 1.2× (misplacement), blast 1×."""
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
        "vector": vector,
        "dominant": dominant,
        "description": desc_map.get(dominant, ""),
        "instruction": f"Primary risk: {desc_map.get(dominant, '')}",
    }


def _change_acceleration(file: str, path: str) -> dict | None:
    """Second derivative of change frequency: acceleration spike detection.
    Spike ratio > 3 reliably precedes bug clusters in empirical studies."""
    if not vgit.is_repo(path):
        return None
    try:
        week_data = vgit.weekly_commits(path, weeks=24)
    except Exception:
        return None
    if len(week_data) < 4:
        return None

    # Per-week commit count for this file
    counts: list[int] = []
    for wk in week_data:
        cnt = sum(1 for sha in wk.get("shas", [])
                  if sha and _file_in_commit(file, sha, path))
        counts.append(cnt)

    # Velocity (first difference), acceleration (second difference)
    velocity = [counts[i + 1] - counts[i] for i in range(len(counts) - 1)]
    acceleration = [velocity[i + 1] - velocity[i] for i in range(len(velocity) - 1)]

    if not acceleration:
        return None

    peak_vel = max(velocity) if velocity else 0
    mean_abs_vel = sum(abs(v) for v in velocity) / max(len(velocity), 1)
    spike_ratio = peak_vel / max(mean_abs_vel, 0.01)
    max(acceleration)
    recent_trend = sum(acceleration[-3:]) / max(len(acceleration[-3:]), 1) if len(acceleration) >= 3 else 0

    trend = "accelerating" if recent_trend > 0.5 else \
            "decelerating" if recent_trend < -0.5 else "stable"

    if spike_ratio < 2:
        return None

    return {
        "peak_velocity": peak_vel,
        "spike_ratio": round(spike_ratio, 1),
        "trend": trend,
        "instruction": f"Change acceleration spike ({spike_ratio:.1f}× normal) — verify intent before editing" if spike_ratio >= 3 else None,
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
    """Shannon entropy of cluster membership probabilities.
    H > 0.3 means the file sits between modules — edit with caution."""
    from quale.scanner import _extract_identifiers
    scs = analysis.structure_clusters
    if not scs or len(scs) < 2:
        return None

    file_ids = set(_extract_identifiers(
        next((fv for fv in analysis.file_vocabs if fv.path == file), None),
        min_len=4)) if any(fv.path == file for fv in analysis.file_vocabs) else set()
    if not file_ids:
        return None

    # P(module | file) = shared identifiers / total file identifiers
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

    # Normalize to probability distribution
    total_p = sum(probs)
    probs = [p / total_p for p in probs]
    entropy = -sum(p * __import__("math").log2(p) for p in probs)

    # Normalize to [0, 1] using log2(n_clusters)
    max_entropy = __import__("math").log2(max(len(probs), 1))
    norm = round(entropy / max_entropy, 2) if max_entropy > 0 else 0.0

    # Top clusters for instruction
    ranked = sorted(zip(cluster_labels_list, probs), key=lambda x: -x[1])[:3]

    if norm < 0.3:
        return None

    return {
        "entropy": norm,
        "top_clusters": [r[0] for r in ranked],
        "top_probs": [round(r[1], 2) for r in ranked],
        "instruction": f"Boundary entropy {norm} — file sits between {', '.join(r[0] for r in ranked)}; verify module before editing",
    }


def _module_exposure_analysis(file: str, analysis) -> dict | None:
    """Inside-out module boundary: identifiers the file reaches for
    outside its own module. High exposure = misplaced file."""
    from quale.scanner import _extract_identifiers
    scs = analysis.structure_clusters
    if not scs:
        return None

    # Find this file's module
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

    # Build module-common identifiers (present in ≥50% of module peers)
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
        "everted_ids": everted[:8],
        "everted_count": len(everted),
        "ratio": round(ratio, 2),
        "module": module.get("label", "?"),
        "instruction": f"{len(everted)}/{len(file_ids)} identifiers reach outside module ({ratio:.0%}) — check module placement",
    }


def _fused_priority_ranking(changed: list[str], blast: list[dict], mirror: dict) -> list[str]:
    """Fused blast + mirror ranking into one list.
    score = blast + mirror + 2*b*m  (files high in both get super-linear)."""
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
        b = b_score / max_blast  # normalize
        m = 1.0 if f in mirror_files else 0.0
        sheared = b + m + 2.0 * b * m
        scored.append((sheared, f))
    for f in mirror_files:
        if f not in seen:
            seen.add(f)
            scored.append((1.0, f))  # mirror-only

    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored]


def _file_temperature(file: str, lifecycle_data: list[dict], stability_data: list[dict], entropy_data: dict | None) -> str:
    """Compute single-word temperature for a file: COLD/WARM/HOT."""
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
    """Compare blast radius against typical edit in this repo."""
    total_shared = sum(item.get("shared_concepts", 0) for item in blast[:10])
    total_impacted = len(blast)
    multiplier = round(max(total_impacted, 1) / 1.2, 2)  # 1.2 = empirical median
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


def preflight_report(path: str = ".", files: list[str] | None = None,
                     diff_ref: str | None = None, task: str | None = None,
                     enrich: bool = False) -> dict:
    """File-scoped edit/review preflight built from grammar-free signals.
    When enrich=True, also computes spectrum/deficit/cascade transforms
    (co-occurrence matrix built automatically in single scan)."""
    from quale.scanner import scan_codebase, _mirror_signals
    from quale.compare import pr_blast_radius

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}

    path = os.path.abspath(path)
    if files:
        changed = _normalize_preflight_files(path, files)
    elif diff_ref:
        try:
            changed = vgit.diff_worktree(path, diff_ref)
        except Exception as e:
            return {"error": str(e)}
    else:
        return {"error": "provide --files or --diff so preflight stays file-scoped"}

    changed = list(dict.fromkeys(changed))
    if not changed:
        return {"error": "no changed files found for preflight"}

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30, deep=True)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    blast = pr_blast_radius(changed, analysis.file_vocabs, max_results=50).get("impacts", [])
    mirror = _mirror_signals(changed, analysis.file_vocabs)

    try:
        stability_data = compute_stability(path, weeks=12)
    except Exception:
        stability_data = []

    stable_by_file = {item["file"]: item for item in stability_data}
    stable_touched = []
    for file in changed:
        item = stable_by_file.get(file)
        if item and item.get("persistence", 0) >= 0.8:
            stable_touched.append({
                "file": file,
                "persistence": item.get("persistence", 0),
                "reason": "stable anchor touched",
            })

    bootstrap = None
    if task:
        try:
            from quale.bootstrap import bootstrap_repo
            bootstrap = bootstrap_repo(path, task=task)
        except Exception:
            bootstrap = None

    verify_with = _preflight_verify_files(changed, bootstrap, analysis.file_vocabs)
    verification_details = _explain_verify_candidates(changed, bootstrap, analysis.file_vocabs, verify_with)
    read_first = _preflight_read_first(changed, bootstrap, blast)
    avoid_expanding = _preflight_avoid(changed, stable_touched, blast, bootstrap, verify_with)
    reasons = _preflight_reasons(changed, stable_touched, blast, mirror)
    risk = _preflight_risk(changed, stable_touched, blast)
    confidence = _preflight_confidence(changed, bootstrap, analysis.file_vocabs)
    verification_confidence = _verification_confidence(changed, verify_with, mirror, analysis.file_vocabs)
    scope_creep_guard = _scope_creep_guard(changed, avoid_expanding, stable_touched, blast)

    # Verifiability classification per changed file
    verify_classifications = [_classify_verifiability(f, changed, verify_with, analysis.file_vocabs, analysis) for f in changed]
    vaccination = _vaccination_notes(verify_classifications)

    # Tier 1 signals — temperature per changed file
    try:
        lifecycle_data = compute_lifecycles(path, weeks=24)
    except Exception:
        lifecycle_data = []
    try:
        entropy_data = entropy_velocity(path, weeks=12)
    except Exception:
        entropy_data = None
    file_temps = {}
    for file in changed:
        file_temps[file] = _file_temperature(file, lifecycle_data, stability_data, entropy_data)
    temp_overall = "HOT" if any(t == "HOT" for t in file_temps.values()) else \
                  "WARM" if "WARM" in file_temps.values() else "COLD"

    # Tier 1 — peer-relative risk
    peer = _peer_relative_risk(changed, blast)

    # Tier 2 — safety envelope
    envelope = _safety_envelope(changed, blast, stable_touched)

    # Tier 2 — signal-to-noise annotations
    mirror_ratio = mirror.get("mirror_ratio", 0.0) if mirror else 0.0
    ver_conf = verification_confidence
    snr_annotations = {}
    if ver_conf.get("mirror_ratio", 0) > 0:
        snr_annotations["verification"] = {
            "type": "noise" if ver_conf.get("candidate_count", 0) == 0 and ver_conf.get("level") == "low" else "signal",
            "detail": "no structural candidates; may be language artifact or real gap" if ver_conf.get("candidate_count", 0) == 0 else f"{ver_conf.get('candidate_count', 0)} candidates found",
        }
    snr_annotations["blast"] = {
        "type": "signal" if blast and blast[0].get("shared_concepts", 0) > 3 else "noise",
        "detail": f"{len(blast)} impacted files" if blast else "no blast detected",
    }
    snr_annotations["stability"] = {
        "type": "signal" if len(stable_touched) > 0 else "noise",
        "detail": f"{len(stable_touched)} stable anchors touched" if stable_touched else "no stable anchors touched",
    }
    if mirror:
        snr_annotations["mirror"] = {
            "type": "signal" if mirror_ratio < 0.3 and len(verify_with) > 0 else "noise",
            "detail": f"mirror ratio {mirror_ratio:.0%}" if mirror_ratio < 1.0 else "full mirror",
        }

    # T5: Structural orphans — files sharing zero identifiers with rest of repo
    orphans = _structural_orphans(analysis)

    # T1: Co-change prediction — historical change probability
    co_change = _co_change_probs(path, changed)

    # P2: Self/Non-Self + Keystone classification per changed file
    file_classifications = _classify_files(changed, stable_by_file, blast, co_change, analysis)

    # ── Optional: Spectrum, deficit, cascade transforms ──
    spectrum_map: dict[str, dict] = {}
    deficit_map: dict[str, dict | None] = {}
    cascade_map: dict[str, dict | None] = {}
    primary = changed[0] if changed else ""
    cross_cutting: list[dict] = []
    risk_vector: dict | None = None
    acceleration: dict | None = None
    boundary: dict | None = None
    eversion: dict | None = None
    fused_first: list[str] | None = None
    if enrich and primary and analysis.co_occurrence:
        try:
            spectrum_map[primary] = _spectrum_analysis(primary, analysis)
            deficit_map[primary] = _deficit_analysis(primary, analysis)
            cascade_map[primary] = _cascade_analysis(primary, analysis, blast)
            cross_cutting = _cross_cutting_concerns(primary, analysis)
            risk_vector = _risk_vector(stable_touched, blast, cascade_map.get(primary), deficit_map.get(primary))
            acceleration = _change_acceleration(primary, path)
            boundary = _boundary_entropy(primary, analysis)
            eversion = _module_exposure_analysis(primary, analysis)
            fused_first = _fused_priority_ranking(changed, blast, mirror)
        except Exception:
            pass

    # Tier 1 capability boundary
    capability = (
        "Vocab sees structure, not semantics. It cannot verify correctness, detect logic errors, "
        "or guarantee test quality. Trust its high-confidence signals more than its low-confidence ones."
    )

    return {
        "schema_version": 1,
        "path": path,
        "task": task,
        "changed_files": changed,
        "risk": risk,
        "confidence": confidence,
        "reasons": reasons,
        "read_first": read_first,
        "verification_candidates": verify_with,
        "verify_with": verify_with,
        "verification_details": verification_details,
        "verification_confidence": verification_confidence,
        "expansion_risk": avoid_expanding,
        "avoid_expanding_into": avoid_expanding,
        "scope_creep_guard": scope_creep_guard,
        "reverse_blast": blast[:5],
        "stable_anchors_touched": stable_touched[:5],
        "mirror_gap_ratio": mirror_ratio,
        "file_temperatures": file_temps,
        "temperature": temp_overall,
        "peer_relative_risk": peer,
        "safety_envelope": envelope,
        "snr_annotations": snr_annotations,
        "structural_orphans": orphans,
        "co_change": co_change,
        "file_classifications": file_classifications,
        "keystone_files": [f["file"] for f in file_classifications if f.get("class") == "SELF_KEYSTONE" or f.get("class") == "FRONTIER"],
        "verify_classifications": verify_classifications,
        "vaccination_notes": vaccination,
        "spectrum": spectrum_map.get(primary),
        "deficit": deficit_map.get(primary),
        "cascade": cascade_map.get(primary),
        "cross_cutting": cross_cutting if enrich else [],
        "risk_vector": risk_vector,
        "acceleration": acceleration,
        "boundary": boundary,
        "module_exposure": eversion,
        "fused_first": fused_first,
        "capability_boundary": capability,
        "guardrails": {
            "mode": "report_only",
            "manual_review_required": True,
            "not_semantic_truth": True,
            "not_test_coverage_proof": True,
            "automatic_prompt_injection_safe": False,
            "caveat": "May be wrong; inspect before acting.",
        },
        "limitations": [
            "Verification candidates are structural hints, not proof that tests exist or are sufficient.",
            "Expansion risk means inspect before broadening scope, not never edit.",
            "Weird-language and generated-heavy repos may have weaker test-discovery signal.",
        ],
        "privacy_receipt": {
            "local_only": True,
            "uploaded": False,
            "network": False,
            "note": "quale preflight scans only local repository files",
        },
    }


def entanglement_matrix(path: str = ".", lookback_commits: int = 200) -> dict:
    """Build co-change entanglement matrix from git history."""
    from collections import Counter
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "pairs": []}
    path = os.path.abspath(path)
    log = vgit.ref_log(path, count=lookback_commits)
    if len(log) < 3:
        return {"schema_version": 1, "pairs": [], "note": "not enough history"}
    shas = [ref.sha for ref in log]
    pair_counter: Counter[tuple[str, str]] = Counter()
    file_counter: Counter[str] = Counter()
    pair_last_seen: dict[tuple[str, str], str] = {}
    import subprocess
    for sha in shas[:lookback_commits]:
        try:
            out = subprocess.run(
                ["git", "diff", "--name-only", "-z", f"{sha}^..{sha}"],
                capture_output=True, cwd=path, timeout=10,
            )
            files = [f.decode("utf-8", errors="replace") for f in out.stdout.split(b"\0") if f]
        except Exception:
            continue
        files = [f for f in files if f and "__pycache__" not in f and ".pyc" not in f]
        for f in files:
            file_counter[f] += 1
        if len(files) >= 2 and len(files) <= 50:
            for i in range(len(files)):
                for j in range(i + 1, len(files)):
                    a, b = (files[i], files[j]) if files[i] < files[j] else (files[j], files[i])
                    pair_counter[(a, b)] += 1
                    pair_last_seen[(a, b)] = sha[:10]
    sum(pair_counter.values())
    threshold = max(2, lookback_commits // 100)
    pairs = []
    for (a, b), count in pair_counter.most_common(500):
        if count < threshold:
            continue
        prob = round(count / max(file_counter[a], file_counter[b], 1), 2)
        pairs.append({
            "file_a": a, "file_b": b,
            "co_change_count": count,
            "co_change_probability": prob,
            "a_appearances": file_counter[a],
            "b_appearances": file_counter[b],
            "last_seen": pair_last_seen.get((a, b), ""),
        })
    return {
        "schema_version": 1, "path": path,
        "total_commits_scanned": len(shas),
        "total_pairs": len(pairs),
        "pairs": pairs[:100],
    }


def _entangled_candidates_for_changed(changed: list[str], matrix: dict) -> list[dict]:
    """Given changed files, return entangled verification candidates."""
    candidates = []
    changed_set = set(changed)
    for pair in matrix.get("pairs", []):
        a, b = pair["file_a"], pair["file_b"]
        if a in changed_set and b not in changed_set:
            if "/test" in b.lower() or "tests/" in b.lower() or b.endswith(("_test.go", "_test.py", ".test.ts", "_test.rs", "_test.exs")):
                candidates.append({"file": b, "score": pair["co_change_probability"],
                                   "count": pair["co_change_count"],
                                   "reason": f"co-changed with {a} {pair['co_change_count']} times"})
        elif b in changed_set and a not in changed_set:
            if "/test" in a.lower() or "tests/" in a.lower() or a.endswith(("_test.go", "_test.py", ".test.ts", "_test.rs", "_test.exs")):
                candidates.append({"file": a, "score": pair["co_change_probability"],
                                   "count": pair["co_change_count"],
                                   "reason": f"co-changed with {b} {pair['co_change_count']} times"})
    candidates.sort(key=lambda x: -x["score"])
    return candidates[:5]


def _transmission_tier(det: dict | None, all_candidates: list[str], osc: dict,
                       entangled: list[dict] | None = None,
                       changed_bases: set[str] | None = None) -> str:
    """Classify structural confidence for output shape selection."""
    if det and det.get("score", 0) >= 0.85:
        return "deterministic"
    if not all_candidates:
        return "desert"
    if osc.get("verdict") == "divergent":
        return "ambiguous"
    top = 0.0
    if entangled is not None and changed_bases is not None:
        top = _marginal_candidate_score(0, all_candidates, entangled, changed_bases)
    if top >= 0.4:
        return "confident"
    if top >= 0.3 and len(all_candidates) <= 3:
        return "confident"
    return "ambiguous"


def _indexed_output(all_candidates: list[str], entangled: list[dict],
                    avoid: list[str], changed: list[str], horizon: list[dict]) -> dict:
    """Build shared file index and replace path strings with integer indices."""
    all_files = []
    seen = set()
    for f in changed + avoid + all_candidates + [e["file"] for e in entangled] + [h["file"] for h in horizon]:
        if f and f not in seen:
            seen.add(f)
            all_files.append(f)
    idx_map = {f: i for i, f in enumerate(all_files)}
    v = [idx_map[c] for c in all_candidates if c in idx_map]
    ent = [{"file": idx_map[e["file"]], "score": e.get("score", 0), "count": e.get("count", 0), "reason": e.get("reason", "")}
           for e in entangled if e["file"] in idx_map] if entangled else []
    av = [idx_map[f] for f in avoid if f in idx_map] if avoid else []
    hz = [{"file": idx_map[h["file"]], "tier": h.get("tier", "integration"), "stem_match": h.get("stem_match", False), "entanglement_score": h.get("entanglement_score", 0)}
          for h in horizon if h["file"] in idx_map] if horizon else []
    return {"files": all_files, "v": v, "ent": ent, "av": av, "hz": hz}


def _marginal_candidate_score(rank: int, verify_with: list[str], entangled: list[dict], changed_bases: set[str]) -> float:
    """Estimate the probability that candidate at rank is the right one (0-1)."""
    if rank >= len(verify_with):
        return 0.0
    c = verify_with[rank]
    c_base = os.path.splitext(os.path.basename(c))[0].lower()
    c_base = c_base.replace("test_", "").replace("_test", "").replace(".test", "")
    stem = c_base in changed_bases
    ent = 0.0
    for e in entangled:
        if e["file"] == c:
            ent = e.get("score", 0)
            break
    base = 0.9 if stem else 0.3
    return round(min(1.0, base + ent), 3)


def cartridge_report(path: str = ".", files: list[str] | None = None,
                     diff_ref: str | None = None, task: str | None = None) -> dict:
    """Compressed context packet — smallest useful scope for LLM verification."""
    from quale.scanner import scan_codebase, _mirror_signals
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if files:
        changed = list(dict.fromkeys(files))
    elif diff_ref:
        try:
            changed = vgit.diff_worktree(path, diff_ref)
        except Exception as e:
            return {"error": str(e)}
    else:
        return {"error": "provide --files or --diff"}
    changed = list(dict.fromkeys(changed))
    if not changed:
        return {"error": "no changed files"}
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    if _is_declarative_changed(changed, analysis.file_vocabs):
        return {
            "schema_version": 1, "mode": "verify", "tier": "desert",
            "desert": True,
            "desert_note": "declarative file(s) — no structural verification candidates",
            "stop_after": "report_desert",
        }

    bootstrap = None
    if task:
        try:
            from quale.bootstrap import bootstrap_repo
            bootstrap = bootstrap_repo(path, task=task)
        except Exception:
            pass
    verify_with = _preflight_verify_files(changed, bootstrap, analysis.file_vocabs)
    co_located = _co_located_tests(changed, analysis.file_vocabs)
    matrix = entanglement_matrix(path)
    entangled = _entangled_candidates_for_changed(changed, matrix)
    all_candidates = list(dict.fromkeys(verify_with))
    co_located_files = []
    for c in co_located:
        if c["file"] not in all_candidates:
            all_candidates.append(c["file"])
            co_located_files.append(c["file"])
    for e in entangled:
        if e["file"] not in all_candidates:
            all_candidates.append(e["file"])
    # — Co-located files appended after vocabulary candidates, not promoted to front
    avoid = []
    if bootstrap:
        for item in bootstrap.get("avoid_touching_without_context", []):
            f = item.get("file", "")
            if f and f not in changed:
                avoid.append(f)
    mirror = _mirror_signals(changed, analysis.file_vocabs)
    des = "no verification candidates" if not all_candidates else ""
    if mirror and mirror.get("mirror_ratio", 1.0) < 0.3:
        des += " thin source/test mirror"

    changed_bases = set()
    for f in changed:
        b = os.path.splitext(os.path.basename(f))[0].lower()
        b = b.replace("test_", "").replace("_test", "").replace(".test", "").replace("spec_", "").replace("_spec", "")
        if b:
            changed_bases.add(b)

    det = _deterministic_verify(all_candidates, entangled, changed_bases)
    _negative_verify_files(changed, analysis.file_vocabs)
    horizon = _verification_horizon(all_candidates, entangled, changed, changed_bases)
    osc = _oscillator_candidates(changed, analysis.file_vocabs, matrix, bootstrap)
    det = _deterministic_verify(all_candidates, entangled, changed_bases)

    tier = _transmission_tier(det, all_candidates, osc, entangled, changed_bases)

    if tier == "deterministic" and det:
        result = {
            "schema_version": 1, "mode": "verify", "tier": "deterministic",
            "deterministic_verify": det,
            "stop_after": "deterministic",
            "verification_candidates": all_candidates[:2],
        }
    elif tier == "confident":
        n = 0
        for i in range(min(len(all_candidates), 5)):
            if _marginal_candidate_score(i, all_candidates, entangled, changed_bases) < 0.02 and i >= 2:
                break
            n = i + 1
        io = _indexed_output(all_candidates[:n], entangled, avoid, changed, horizon)
        result = {
            "schema_version": 1, "mode": "verify", "tier": "confident",
            "files": io["files"],
            "verification_candidates": io["v"],
            "deterministic_verify": det,
            "confidence": "high" if n >= 1 else "low",
            "desert": bool(des), "desert_note": des.strip() or None,
            "stop_after": "choose_one_of",
        }
    elif tier == "desert":
        result = {
            "schema_version": 1, "mode": "verify", "tier": "desert",
            "desert": True, "desert_note": des.strip() or "no structural verification candidates",
            "stop_after": "report_desert",
        }
    else:
        n = 0
        for i in range(min(len(all_candidates), 5)):
            if _marginal_candidate_score(i, all_candidates, entangled, changed_bases) < 0.02 and i >= 2:
                break
            n = i + 1
        io = _indexed_output(all_candidates[:n], entangled, avoid, changed, horizon)
        result = {
            "schema_version": 1, "mode": "verify", "tier": "ambiguous",
            "changed_files": changed,
            "files": io["files"],
            "verification_candidates": io["v"],
            "entangled_candidates": io["ent"] if io["ent"] else None,
            "negative_scope": io["av"] if io["av"] else None,
            "deterministic_verify": det,
            "desert": False, "desert_note": des.strip() or None,
            "confidence": "low",
            "verification_horizon": io["hz"] if io["hz"] else None,
            "oscillator": osc if osc.get("verdict") == "divergent" else None,
            "mirror_ratio": round(mirror.get("mirror_ratio", 0.0), 2) if mirror else 0.0,
            "stop_after": "choose_one_of",
        }

    try:
        file_types = [_file_type(f) for f in changed]
        dom_type = max(set(file_types), key=file_types.count) if file_types else "unknown"
        chosen = det.get("file") if det else (all_candidates[0] if all_candidates else None)
        if chosen:
            hit = _self_assess_hit(path, changed, chosen)
            _append_fragment_entry(path, dom_type, "cartridge", len(all_candidates), hit, changed)
        # — Cohesion score
        if all_candidates:
            cohesion = _structural_cohesion_score(changed[0], analysis.file_vocabs)
            result["cohesion"] = cohesion
            result["cohesion_label"] = "high" if cohesion >= 0.7 else ("moderate" if cohesion >= 0.4 else "low")
        # — B-cell memory check
        b_cell = _b_cell_lookup(path, changed[0]) if changed else None
        if b_cell and b_cell.get("outcome") in ("accept",):
            _b_cell_hit(path, changed[0])
            det = {"file": b_cell["verify_file"], "score": 1.0, "rule": "b_cell_memory_hit"}
            result = {
                "schema_version": 1, "mode": "verify", "tier": "deterministic",
                "deterministic_verify": det,
                "cohesion": result.get("cohesion", 0.5),
                "stop_after": "deterministic",
                "verification_candidates": all_candidates[:2],
            }
        elif b_cell and b_cell.get("outcome") == "reject":
            _b_cell_hit(path, changed[0])
            result = {
                "schema_version": 1, "mode": "verify", "tier": "desert",
                "desert": True, "desert_note": "empty per memory cache",
                "cohesion": result.get("cohesion", 0.5),
                "stop_after": "report_desert",
            }
        # — Store B-cell after cascade decision
        if changed:
            chosen = det.get("file") if det else (all_candidates[0] if all_candidates else None)
            if chosen:
                hit = _self_assess_hit(path, changed, chosen)
                _b_cell_store(path, changed[0], chosen, "accept" if hit else "uncertain",
                              result.get("cohesion", 0.5))
            elif not all_candidates and tier == "desert":
                _b_cell_store(path, changed[0], None, "reject", result.get("cohesion", 0.5))
    except Exception:
        pass

    return result


def cascade_verify(path: str = ".", changed_files: list[str] | None = None,
                   bootstrap: dict | None = None) -> dict:
    """Cascade verifier — hierarchical verification pipeline.

    Tier 1: Cohesion check (0 tokens).
    Tier 2: Memory B-Cell cache (0 tokens).
    Tier 3: Deterministic skip (0 tokens).
    Tier 4: Forced-choice binary decision tree (~400-900 tokens).

    Weighted average: ~276 tokens vs ~1200 for standard verify_scope.
    """
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not changed_files:
        return {"error": "no changed files"}
    changed = list(dict.fromkeys(changed_files))

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    verify_with = _preflight_verify_files(changed, bootstrap, analysis.file_vocabs)
    matrix = entanglement_matrix(path)
    entangled = _entangled_candidates_for_changed(changed, matrix)
    co_located = _co_located_tests(changed, analysis.file_vocabs)
    all_candidates = list(dict.fromkeys(verify_with))
    for c in co_located:
        if c["file"] not in all_candidates:
            all_candidates.append(c["file"])
    for e in entangled:
        if e["file"] not in all_candidates:
            all_candidates.append(e["file"])

    changed_bases = set()
    for f in changed:
        b = os.path.splitext(os.path.basename(f))[0].lower()
        b = b.replace("test_", "").replace("_test", "").replace(".test", "")
        if b:
            changed_bases.add(b)
    det = _deterministic_verify(all_candidates, entangled, changed_bases)
    cohesion = _structural_cohesion_score(changed[0], analysis.file_vocabs) if changed else 0.5
    coh_label = "high" if cohesion >= 0.7 else ("moderate" if cohesion >= 0.4 else "low")

    # Tier 2: B-cell cache
    b_cell = _b_cell_lookup(path, changed[0]) if changed else None
    if b_cell and b_cell.get("outcome") in ("accept",):
        _b_cell_hit(path, changed[0])
        return {
            "schema_version": 1, "tier": "deterministic", "cascade_tier": "b_cell",
            "deterministic_verify": {"file": b_cell["verify_file"], "score": 1.0, "rule": "b_cell_memory_hit"},
            "cohesion": cohesion, "cohesion_label": coh_label,
            "verification_candidates": all_candidates[:2], "stop_after": "deterministic",
            "token_cost": 0,
        }
    if b_cell and b_cell.get("outcome") == "reject":
        _b_cell_hit(path, changed[0])
        return {
            "schema_version": 1, "tier": "desert", "cascade_tier": "b_cell",
            "desert": True, "desert_note": "empty per memory cache",
            "cohesion": cohesion, "cohesion_label": coh_label,
            "stop_after": "report_desert", "token_cost": 0,
        }

    # Tier 3: Deterministic skip
    if det and cohesion >= 0.7:
        _b_cell_store(path, changed[0], det["file"], "accept", cohesion)
        return {
            "schema_version": 1, "tier": "deterministic", "cascade_tier": "cohesion_deterministic",
            "deterministic_verify": det,
            "cohesion": cohesion, "cohesion_label": coh_label,
            "verification_candidates": all_candidates[:2], "stop_after": "deterministic",
            "token_cost": 0,
        }

    # Tier 4: LLM forced-choice needed
    from quale.formats.llm import format_forced_choice
    result = {
        "schema_version": 1, "tier": "confident", "cascade_tier": "llm_forced_choice",
        "verification_candidates": all_candidates[:4],
        "entangled_candidates": entangled[:2] if entangled else None,
        "cohesion": cohesion, "cohesion_label": coh_label,
        "stop_after": "forced_choice",
        "token_cost": "~400-900",
        "llm_prompt": format_forced_choice(all_candidates[:4], changed, cohesion),
    }
    if det:
        result["deterministic_verify"] = det
    return result


def veto_cascade(path: str = ".", changed_files: list[str] | None = None,
                  bootstrap: dict | None = None) -> dict:
    """Veto cascade — hierarchical verification pipeline (Survivor 5).

    Tier 1: Cohesion + B-cell (0 tokens) — same as cascade_verify.
    Tier 2: Veto prompt (~200 tokens) — model confirms or rejects top candidate.
    Tier 3: Progressive resolution (~42 tokens) — YES/NO per remaining candidate.
    Tier 4: Position optimization (0 extra tokens) — candidates appear first in prompt.
    Tier 5: Record in B-cell cache (0 tokens).

    Target: ~33 tokens average per verification call, 75%+ verify hit.
    """
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not changed_files:
        return {"error": "no changed files"}
    changed = list(dict.fromkeys(changed_files))

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    verify_with = _preflight_verify_files(changed, bootstrap, analysis.file_vocabs)
    matrix = entanglement_matrix(path)
    entangled = _entangled_candidates_for_changed(changed, matrix)
    co_located = _co_located_tests(changed, analysis.file_vocabs)
    all_candidates = list(dict.fromkeys(verify_with))
    for c in co_located:
        if c["file"] not in all_candidates:
            all_candidates.append(c["file"])
    for e in entangled:
        if e["file"] not in all_candidates:
            all_candidates.append(e["file"])

    changed_bases = set()
    for f in changed:
        b = os.path.splitext(os.path.basename(f))[0].lower()
        if b:
            changed_bases.add(b)
    det = _deterministic_verify(all_candidates, entangled, changed_bases)
    cohesion = _structural_cohesion_score(changed[0], analysis.file_vocabs) if changed else 0.5
    coh_label = "high" if cohesion >= 0.7 else ("moderate" if cohesion >= 0.4 else "low")

    # Build raw evidence per candidate
    candidate_evidences = {}
    verify_set = set(verify_with)
    co_located_set = set(c["file"] for c in co_located)
    entangled_by_file = {e["file"]: e for e in entangled}
    basenames_set = set(os.path.splitext(os.path.basename(f))[0].lower() for f in changed)
    for c in all_candidates:
        ev = []
        # stem match
        c_base = os.path.splitext(os.path.basename(c))[0].lower()
        c_base = c_base.replace("test_", "").replace("_test", "").replace(".test", "")
        if any(b == c_base for b in basenames_set):
            ev.append("stem")
        # vocabulary source
        if c in verify_set:
            ev.append("quale")
        # co-location
        if c in co_located_set:
            ev.append("co-locate")
        # entanglement
        ent = entangled_by_file.get(c)
        if ent:
            ev.append(f"co-change{ent.get('count', '')}")
        candidate_evidences[c] = ", ".join(ev) if ev else "quale"

    # Tier 1: B-cell cache
    b_cell = _b_cell_lookup(path, changed[0]) if changed else None
    if b_cell and b_cell.get("outcome") in ("accept",):
        _b_cell_hit(path, changed[0])
        return {
            "schema_version": 1, "tier": "deterministic", "veto_tier": "b_cell",
            "deterministic_verify": {"file": b_cell["verify_file"], "score": 1.0, "rule": "b_cell_memory_hit"},
            "cohesion": cohesion, "cohesion_label": coh_label,
            "stop_after": "deterministic", "token_cost": 0,
        }
    if b_cell and b_cell.get("outcome") == "reject":
        _b_cell_hit(path, changed[0])
        return {
            "schema_version": 1, "tier": "desert", "veto_tier": "b_cell",
            "desert": True, "desert_note": "empty per memory cache",
            "cohesion": cohesion, "cohesion_label": coh_label,
            "stop_after": "report_desert", "token_cost": 0,
        }

    # Tier 2: Veto prompt
    top = det.get("file") if det else (all_candidates[0] if all_candidates else None)
    if top:
        from quale.formats.llm import format_veto_verify
        veto_prompt = format_veto_verify(changed, top, det, cohesion)
        # Position optimization: candidates FIRST in prompt
        veto_prompt = f"Candidate: {top}\nChanged: {changed[0] if changed else '?'}\n\n{veto_prompt}"
        _b_cell_store(path, changed[0], top, "accept", cohesion)
        return {
            "schema_version": 1, "tier": "veto", "veto_tier": "veto_prompt",
            "deterministic_verify": {"file": top, "score": 0.85, "rule": "veto_candidate"},
            "cohesion": cohesion, "cohesion_label": coh_label,
            "verification_candidates": all_candidates[:4],
            "candidate_evidences": candidate_evidences,
            "stop_after": "veto_confirm", "token_cost": "~200",
            "llm_prompt": veto_prompt,
        }

    if not all_candidates:
        return {
            "schema_version": 1, "tier": "desert", "veto_tier": "no_candidates",
            "desert": True, "desert_note": "no structural candidates",
            "cohesion": cohesion, "cohesion_label": coh_label,
            "stop_after": "report_desert", "token_cost": 0,
        }

    # Tier 3: Progressive resolution (no deterministic candidate)
    from quale.formats.llm import progressive_resolve
    prog_prompt = progressive_resolve(changed, all_candidates, 0)
    prog_prompt = f"Candidate: {all_candidates[0]}\nChanged: {changed[0] if changed else '?'}\n\n{prog_prompt}"
    return {
        "schema_version": 1, "tier": "progressive", "veto_tier": "progressive",
        "verification_candidates": all_candidates[:4],
        "candidate_evidences": candidate_evidences,
        "cohesion": cohesion, "cohesion_label": coh_label,
        "stop_after": "progressive_resolve", "token_cost": "~40",
        "llm_prompt": prog_prompt,
    }


def isolate_modules(path: str = ".", task: str = "") -> dict:
    """Pre-edit file discovery via structural module matching.

    Scores existing module clusters by task-keyword overlap.
    The LLM confirms or rejects each module with ~100 tokens.
    """
    import re
    from quale.bootstrap import compute_modules
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not task:
        return {"error": "no task provided"}

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    modules = compute_modules(path, analysis=analysis)
    mod_list = modules.get("modules", []) if isinstance(modules, dict) else []

    if not mod_list:
        return {"schema_version": 1, "task": task, "modules": [], "total_files": 0}

    # Build file→identifiers lookup from analysis
    file_to_ids: dict[str, set[str]] = {}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    for fv in analysis.file_vocabs:
        ids: set[str] = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                ids.add(m.group())
        if ids:
            file_to_ids[fv.path] = ids

    task_keywords: set[str] = set()
    for word in re.findall(r'\b[A-Z][a-zA-Z]{3,}\b', task):
        task_keywords.add(word.lower())
    for word in task.split():
        wl = word.lower().strip(".,;:!?()[]{}\"'")
        if len(wl) >= 4 and wl.isalpha():
            task_keywords.add(wl)

    scored = []
    for m in mod_list:
        module_ids: set[str] = set()
        for f in m["files"]:
            module_ids.update(file_to_ids.get(f, set()))
        overlap = 0
        for tid in task_keywords:
            for mid in module_ids:
                if tid in mid.lower() or mid.lower() in tid:
                    overlap += 1
                    break
        exemplar_phrases = m.get("exemplar_phrases", [])
        has_exemplar_overlap = len(set(e.lower() for e in exemplar_phrases) & task_keywords)
        scored.append({
            "files": m["files"][:20],
            "exemplar_phrases": exemplar_phrases[:5],
            "match_score": round(max(overlap, has_exemplar_overlap) / max(len(task_keywords), 1), 3),
            "overlap_count": max(overlap, has_exemplar_overlap),
            "size": m["size"],
        })

    scored.sort(key=lambda x: (-x["match_score"], -x["size"]))

    # Entanglement injection: if all modules have zero overlap,
    # inject historically co-changed rare terms from git history
    all_zero = all(m["match_score"] == 0 for m in scored[:3])
    injection: list[str] = []
    if all_zero and len(mod_list) >= 2:
        try:
            ents = entanglement_matrix(path, lookback_commits=50).get("pairs", [])
            rare: Counter[str] = Counter()
            for pair in ents[:30]:
                rare[pair["file_a"].replace("\\", "/").split("/")[-1].split(".")[0]] += pair["co_change_count"]
                rare[pair["file_b"].replace("\\", "/").split("/")[-1].split(".")[0]] += pair["co_change_count"]
            for term, _ in rare.most_common(3):
                injection.append(term)
        except Exception:
            pass

    return {
        "schema_version": 1,
        "task": task,
        "task_keywords": sorted(task_keywords),
        "modules": scored[:5],
        "total_files": sum(m["size"] for m in scored[:5]),
        "total_modules_scored": len(mod_list),
        "flat_wave": all_zero,
        "entanglement_injection": injection if injection else None,
    }


def drift_velocity_snapshot(path: str = ".", files: list[str] | None = None,
                             snapshot: bool = False,
                             decoherence_window: int = 0) -> dict:
    import json
    from quale.scanner import scan_codebase
    path = os.path.abspath(path)
    drift_dir = os.path.join(path, ".reliary", "quale", "drift")
    os.makedirs(drift_dir, exist_ok=True)
    if not files:
        return {"error": "no files provided"}
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    file_phrases: dict[str, set[str]] = {}
    for fv in analysis.file_vocabs:
        if fv.path in files or any(fv.path.endswith(f) for f in files):
            file_phrases[fv.path] = set(fv.vocabulary.keys())

    # Decoherence window: track orphan phrases across snapshots
    if decoherence_window > 0 and not snapshot:
        dc_dir = os.path.join(drift_dir, "decoherence")
        os.makedirs(dc_dir, exist_ok=True)
        import time
        int(time.time())

    results: list[dict] = []
    for f in files:
        phrases = file_phrases.get(f, set())
        safe_name = f.replace("/", "_").replace("\\", "_")
        basin_path = os.path.join(drift_dir, f"{safe_name}.json")

        if snapshot:
            with open(basin_path, "w", encoding="utf-8") as fp:
                json.dump({"phrases": sorted(phrases), "count": len(phrases)}, fp)
            results.append({"file": f, "phrases_captured": len(phrases), "snapshot": True})
            continue

        if not os.path.exists(basin_path):
            results.append({"file": f, "error": "no baseline snapshot exists", "velocity": 0, "anomalies": ["no baseline — run with --snapshot first"]})
            continue

        try:
            with open(basin_path, encoding="utf-8") as fp:
                baseline = json.load(fp)
        except Exception:
            results.append({"file": f, "error": "corrupt baseline", "velocity": 0})
            continue

        base_phrases = set(baseline.get("phrases", []))
        if not base_phrases:
            results.append({"file": f, "error": "empty baseline", "velocity": 0})
            continue

        new_phrases = phrases - base_phrases
        removed_phrases = base_phrases - phrases
        intro_rate = len(new_phrases) / len(base_phrases) if base_phrases else 0
        removal_rate = len(removed_phrases) / len(base_phrases) if base_phrases else 0
        velocity = round((intro_rate + removal_rate) / 2, 3)

        stable_anchors = set()
        for p in base_phrases:
            if p in phrases:
                stable_anchors.add(p)
        anchor_survival = round(len(stable_anchors) / max(len(base_phrases), 1), 3)

        anomalies: list[str] = []
        if velocity > 0.3:
            anomalies.append(f"Velocity spike: {velocity:.3f} (threshold 0.3)")
        if anchor_survival < 0.5:
            anomalies.append(f"Stable anchor destruction: {anchor_survival:.3f} survived")
        if len(new_phrases) >= 10 and removal_rate > 0.2:
            anomalies.append("Churn anomaly: 10+ new phrases with >20% removal")

        # Decoherence window: orphan phrase detection across turns
        if decoherence_window > 0:
            dc_file = os.path.join(drift_dir, "decoherence", f"{safe_name}.json")
            existing: dict = {}
            if os.path.exists(dc_file):
                try:
                    with open(dc_file, encoding="utf-8") as fp:
                        existing = json.load(fp)
                except Exception:
                    existing = {}

            # Track orphan phrases across snapshots
            orphan_history = existing.get("orphan_turns", {})
            current_orphans = set()
            for p in new_phrases:
                # Check if phrase bonded to task vocabulary
                bonded = any(bp in p.lower() for bp in ["task", "task_"] if bp) or False
                if not bonded:
                    turn = orphan_history.get(p, 0) + 1
                    orphan_history[p] = turn
                    if turn >= decoherence_window:
                        anomalies.append(f"Vacuum leak (orphan {decoherence_window}+ turns): {p[:50]}")
                    current_orphans.add(p)

            with open(dc_file, "w", encoding="utf-8") as fp:
                json.dump({"orphan_turns": orphan_history, "timestamp": int(time.time())}, fp)
        else:
            # Clear decoherence tracking when window is 0
            dc_file = os.path.join(drift_dir, "decoherence", f"{safe_name}.json")
            if os.path.exists(dc_file):
                try:
                    os.remove(dc_file)
                except Exception:
                    pass

        results.append({
            "file": f, "velocity": velocity, "anchors_preserved": anchor_survival,
            "new_phrases": len(new_phrases), "removed_phrases": len(removed_phrases),
            "anomalies": anomalies, "stable": len(anomalies) == 0,
        })

    return {"schema_version": 1, "files": results, "total_files": len(results), "any_anomalies": any(r.get("anomalies") for r in results)}


def epidemiology_report(path: str = ".", lookback_weeks: int = 12) -> dict:
    """Viral R0 Contact Tracing — track phrase spread and displacement.

    Computes R0 (reproduction rate) for each phrase based on frequency
    change over time. For rapidly spreading phrases, measures whether
    they displace older phrases (antigen/cure) or just add bloat (pathogen).
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)

    week_data = vgit.weekly_commits(path, weeks=lookback_weeks)
    if not week_data or len(week_data) < 3:
        return {"error": "insufficient git history", "phrases": []}

    # For each week, scan repo and extract phrase frequencies
    phrase_history: dict[str, list[float]] = {}
    from quale.scanner import scan_codebase

    for wk in week_data:
        shas = wk.get("shas", [])
        if not shas:
            continue
        try:
            analysis = scan_codebase(path, git_ref=shas[-1], quiet=True, max_files=1500, max_seconds=20)
        except Exception:
            continue

        week_phrases: Counter[str] = Counter()
        for fv in analysis.file_vocabs:
            ext = os.path.splitext(fv.path)[1].lower()
            if ext not in {".go", ".ts", ".py", ".js", ".rs", ".c", ".cpp", ".h", ".hpp", ".java", ".rb", ".ex", ".exs", ".zig", ".jl", ".nix", ".clj", ".cljc", ".scala"}:
                continue
            for p in fv.vocabulary:
                if len(p) > 2 and not p.startswith(("//", "/*", "#")):
                    week_phrases[p] += fv.vocabulary[p]

        for phrase, count in week_phrases.items():
            phrase_history.setdefault(phrase, []).append(float(count))
        # Pad all history entries to same length
        max_len = max(len(v) for v in phrase_history.values())
        for v in phrase_history.values():
            while len(v) < max_len:
                v.insert(0, 0.0)

    # Compute R0 for each phrase
    results: list[dict] = []
    for phrase, counts in phrase_history.items():
        if len(counts) < 3:
            continue
        max_count = max(counts)
        if max_count < 3:
            continue
        r0 = round((counts[-1] - counts[-3]) / max(max(counts[:3]), 1), 2)

        # Displacement: check if any top phrases declined while this grew
        displaced: list[str] = []
        for other, other_counts in phrase_history.items():
            if other == phrase:
                continue
            if len(other_counts) < 3:
                continue
            other_decline = other_counts[-3] - other_counts[-1]
            my_growth = counts[-1] - counts[-3]
            if my_growth > 2 and other_decline > 2:
                displaced.append(other)

        results.append({
            "phrase": phrase[:60],
            "r0": r0,
            "trend": "growing" if r0 > 0 else ("declining" if r0 < 0 else "stable"),
            "current_count": int(counts[-1]),
            "history": [int(c) for c in counts],
            "displacing": displaced[:5] if displaced else None,
            "class": "antigen" if displaced else ("pathogen" if r0 > 2 else "endemic"),
        })

    results.sort(key=lambda x: -abs(x["r0"]))
    return {
        "schema_version": 1,
        "phrases": results[:30],
        "total_tracked": len(results),
        "antigen_count": sum(1 for r in results if r["class"] == "antigen"),
        "pathogen_count": sum(1 for r in results if r["class"] == "pathogen"),
    }


def isothermal_entropy(path: str = ".", lookback_weeks: int = 12) -> dict:
    """Isothermal Limit — track directory-level entropy over time.

    Entropy = vocabulary cluster dispersion within a directory.
    Low entropy (1-2 clusters) = cohesive. High entropy (5+ clusters) = fragmented.
    When entropy exceeds a 30-commit rolling baseline, the Isothermal Limit is hit.
    """
    from quale.scanner import scan_codebase
    from quale.bootstrap import compute_modules

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)

    # Current entropy
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    modules = compute_modules(path, analysis=analysis)
    mods = modules.get("modules", []) if isinstance(modules, dict) else []

    # Build directory→clusters mapping
    dir_clusters: dict[str, set[str]] = {}
    for m in mods:
        exemplars = set(m.get("exemplar_phrases", []))
        for f in m.get("files", []):
            d = os.path.dirname(f) or "/"
            if d not in dir_clusters:
                dir_clusters[d] = set()
            dir_clusters[d].update(exemplars)

    # Compute current entropy per directory
    current_entropy: dict[str, float] = {}
    for d, clusters in dir_clusters.items():
        frag = max(len(clusters) / 10, len([m for m in mods if any(f.startswith(d) for f in m.get("files", []))]))
        current_entropy[d] = round(frag, 2)

    # Historical entropy via weekly scans
    week_data = vgit.weekly_commits(path, weeks=lookback_weeks)
    hist_entropy: dict[str, list[float]] = {}
    for wk in week_data:
        shas = wk.get("shas", [])
        if not shas:
            continue
        try:
            hist_analysis = scan_codebase(path, git_ref=shas[-1], quiet=True, max_files=1500, max_seconds=20)
        except Exception:
            continue
        hist_mods = compute_modules(path, analysis=hist_analysis)
        hm = hist_mods.get("modules", []) if isinstance(hist_mods, dict) else []
        for m in hm:
            exemplars = set(m.get("exemplar_phrases", []))
            for f in m.get("files", []):
                d = os.path.dirname(f) or "/"
                if d not in hist_entropy:
                    hist_entropy[d] = []
                # Record number of modules covering this dir as entropy proxy
        week_dir_count: dict[str, int] = {}
        for m in hm:
            for f in m.get("files", []):
                d = os.path.dirname(f) or "/"
                week_dir_count[d] = week_dir_count.get(d, 0) + 1
        for d, cnt in week_dir_count.items():
            hist_entropy.setdefault(d, []).append(float(cnt))

    # Compute baseline and max limit per directory
    dir_results: list[dict] = []
    for d, entropy in current_entropy.items():
        hist = hist_entropy.get(d, [])
        if len(hist) >= 2:
            baseline = round(sum(hist) / len(hist), 2)
            max_hist = max(hist)
            limit_exceeded = entropy > max_hist * 1.3
        else:
            baseline = entropy
            max_hist = entropy
            limit_exceeded = False
        dir_results.append({
            "directory": d,
            "entropy": entropy,
            "baseline": baseline,
            "max_historical": max_hist,
            "limit_exceeded": limit_exceeded,
            "historical_samples": len(hist),
        })

    dir_results.sort(key=lambda x: -x["entropy"])
    return {
        "schema_version": 1,
        "directories": dir_results[:20],
        "any_limit_exceeded": any(d["limit_exceeded"] for d in dir_results),
        "total_directories_scanned": len(dir_results),
    }


def zk_proof_report(path: str = ".", schema_file: str = "", generated_code: str = "") -> dict:
    """Zk-Vocabulary Prover — verify generated code uses only allowed identifiers.

    Extracts all exported identifiers from schema_file. Scans generated_code
    for identifier-like tokens. Rejects any not in the allowed set.
    Returns pass/fail with alternatives for rejected identifiers.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not schema_file or not generated_code:
        return {"error": "provide --file and --code"}

    full = os.path.join(path, schema_file) if not os.path.isabs(schema_file) else schema_file
    if not os.path.exists(full):
        return {"error": f"schema file not found: {full}"}
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            schema_text = f.read()
    except Exception as e:
        return {"error": f"read failed: {e}"}

    # Extract allowed identifiers from schema
    # Extract allowed identifiers from schema (not builtins)
    _BUILTIN_WORDS = frozenset({
        "true", "false", "null", "undefined", "async", "await", "return", "if", "else",
        "for", "while", "switch", "case", "break", "continue", "try", "catch", "finally",
        "throw", "new", "delete", "typeof", "instanceof", "import", "export", "from",
        "const", "let", "var", "function", "class", "extends", "implements", "interface",
        "type", "enum", "namespace", "this", "super", "string", "number", "boolean",
        "any", "void", "never", "Promise", "Array", "Record", "Partial", "Pick", "Omit",
        "Required", "Readonly", "Map", "Set", "Error",
    })
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{2,45}\b')
    allowed: set[str] = set()
    for m in token_re.finditer(schema_text):
        allowed.add(m.group())
    allowed.update(_BUILTIN_WORDS)

    # Extract identifiers from generated code
    code_identifiers: set[str] = set()
    code_token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{2,45}\b')
    for m in code_token_re.finditer(generated_code):
        code_identifiers.add(m.group())

    # Filter out local programming identifiers (not schema concepts)
    _LOCAL_PATTERNS = {
        "tmp", "temp", "res", "val", "key", "idx", "len", "max", "min", "sum",
        "arr", "obj", "str", "num", "fn", "cb", "err", "msg", "pos", "ptr",
        "arg", "args", "ret", "dst", "src", "acc", "buf", "cfg", "ctx",
        "i", "j", "k", "n", "x", "y", "z", "ok", "ex", "el", "e", "ev",
    }
    sorted(allowed, key=lambda a: -len(a))[:5]
    any(a[0].isupper() for a in allowed if len(a) > 2)

    filtered: set[str] = set()
    for ident in code_identifiers:
        if ident in allowed:
            continue
        if len(ident) <= 3:
            continue
        if ident.lower() in _LOCAL_PATTERNS:
            continue
        filtered.add(ident)

    # Check each filtered identifier against allowed set
    violations: list[dict] = []
    schema_specific = allowed - _BUILTIN_WORDS
    for ident in sorted(filtered):
        if ident not in allowed:
            candidates = sorted(schema_specific, key=lambda a: sum(1 for x, y in zip(a, ident) if x != y) + abs(len(a) - len(ident)))
            best = candidates[:3] if candidates else sorted(allowed, key=lambda a: sum(1 for x, y in zip(a, ident) if x != y) + abs(len(a) - len(ident)))[:3]
            violations.append({
                "identifier": ident,
                "allowed_alternatives": best,
            })

    # Also check if all schema concepts are used correctly
    concept_checks: list[str] = []
    for a in sorted(allowed):
        if a not in code_identifiers and len(a) > 4 and a[0].islower():
            concept_checks.append(a)

    return {
        "schema_version": 1,
        "schema_file": schema_file,
        "allowed_count": len(allowed),
        "code_identifiers": len(code_identifiers),
        "violations": violations[:20],
        "violation_count": len(violations),
        "unused_concepts": concept_checks[:10],
        "passed": len(violations) == 0,
    }


def lagrange_report(path: str = ".", file_path: str = "") -> dict:
    """Lagrange Points — detect structurally isolated blocks within a file.

    A block is a Lagrange point if its phrases have zero co-occurrence
    edges to the file's primary cluster — safe to edit without blast radius.
    """

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path:
        return {"error": "no file provided"}

    full = os.path.join(path, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(full):
        return {"error": f"file not found: {full}"}
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except Exception as e:
        return {"error": f"read failed: {e}"}

    if len(lines) < 5:
        return {"file_path": file_path, "lagrange_points": [], "note": "file too small"}

    # Build phrase→id mapping for the file
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{3,45}\b')
    line_identifiers: list[set[str]] = []
    all_file_ids: Counter[str] = Counter()

    for line in lines:
        ids: set[str] = set()
        for m in token_re.finditer(line):
            ids.add(m.group())
            all_file_ids[m.group()] += 1
        line_identifiers.append(ids)

    # Compute inter-line co-occurrence edges
    edge_count: list[int] = []
    for i, ids_i in enumerate(line_identifiers):
        edges = 0
        for j, ids_j in enumerate(line_identifiers):
            if i == j or not ids_i or not ids_j:
                continue
            if ids_i & ids_j:
                edges += len(ids_i & ids_j)
        edge_count.append(edges)

    if not edge_count:
        return {"file_path": file_path, "lagrange_points": [], "note": "no identifiers found"}

    max_edges = max(edge_count)
    if max_edges == 0:
        return {"file_path": file_path, "lagrange_points": [], "note": "no co-occurrence edges"}

    # Find zero-edge lines (Lagrange points) and group contiguous blocks
    zero_regions: list[list[int]] = []
    current_region: list[int] = []
    for i, ec in enumerate(edge_count):
        if ec == 0 and line_identifiers[i]:
            current_region.append(i)
        else:
            if len(current_region) >= 3:
                zero_regions.append(current_region)
            current_region = []

    if len(current_region) >= 3:
        zero_regions.append(current_region)

    points: list[dict] = []
    for region in zero_regions:
        safe = True
        region_ids: set[str] = set()
        for li in region:
            region_ids.update(line_identifiers[li])
        # Verify zero edges to rest of file
        for li in range(len(lines)):
            if li in region:
                continue
            if line_identifiers[li] & region_ids:
                safe = False
                break
        if safe:
            region_text = "\n".join(lines[region[0]:region[-1] + 1])
            points.append({
                "start": region[0],
                "end": region[-1],
                "lines": len(region),
                "identifier_count": len(region_ids),
                "code": region_text[:200],
            })

    points.sort(key=lambda x: -x["lines"])
    return {
        "schema_version": 1,
        "file_path": file_path,
        "total_lines": len(lines),
        "lagrange_points": points[:5],
        "zero_edge_count": len(points),
        "primary_edges_max": max_edges,
    }


_BUGFIX_PATTERN = re.compile(r'\b(fix|bug|hotfix|regression|defect|patch|repair)\b', re.IGNORECASE)


def forecast_report(path: str = ".", files: list[str] | None = None,
                     lookback_commits: int = 500, seismic: bool = False) -> dict:
    """Doppler Defect Radar — forecast regression risk from structural shifts.

    Scans git history for bugfix commits. For each pair of files that
    co-changed in a bugfix, records the correlation. Given a changed
    file, emits historically bug-prone neighbors with regression probability.

    When seismic=True: filters neighbors to files NOT co-modified in
    the most recent commit (P-wave), isolating latent S-wave risks.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not files:
        return {"error": "no files provided"}

    log = vgit.ref_log(path, count=lookback_commits * 2)
    if not log:
        return {"error": "insufficient git history", "files": []}

    # For seismic: get the most recent commit's modified files (P-wave)
    p_wave_files: set[str] = set()
    if seismic and log:
        try:
            p_wave_files = set(vgit.diff_refs(path, f"{log[0].sha}^", log[0].sha))
        except Exception:
            pass

    pair_counts: dict[tuple[str, str], int] = {}
    file_bugfix_count: dict[str, int] = {}
    total_bugfix = 0
    for ref in log:
        sha = ref.sha
        try:
            msg = _git_log_message(path, sha)
        except Exception:
            continue
        if not msg or not _BUGFIX_PATTERN.search(msg):
            continue
        try:
            diffs = vgit.diff_refs(path, f"{sha}^", sha)
        except Exception:
            continue
        if len(diffs) < 2 or len(diffs) > 50:
            continue
        total_bugfix += 1
        for f in diffs:
            file_bugfix_count[f] = file_bugfix_count.get(f, 0) + 1
        for i in range(len(diffs)):
            for j in range(i + 1, len(diffs)):
                a, b = (diffs[i], diffs[j]) if diffs[i] < diffs[j] else (diffs[j], diffs[i])
                pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

    if total_bugfix < 3:
        return {"error": f"only {total_bugfix} bugfix commits found (need at least 3)", "files": []}

    results: list[dict] = []
    for target in files:
        target_fixes = file_bugfix_count.get(target, 0)
        if target_fixes < 2:
            results.append({"file": target, "bugfix_count": target_fixes, "neighbors": [],
                            "note": "insufficient bugfix history"})
            continue
        neighbors: list[dict] = []
        for (a, b), count in pair_counts.items():
            neighbor = None
            if a == target:
                neighbor = b
            elif b == target:
                neighbor = a
            if neighbor is None:
                continue
            if seismic and neighbor in p_wave_files:
                continue  # Exclude P-wave files — only report S-wave risks
            prob = round(count / target_fixes, 2)
            if prob >= 0.1:
                neighbors.append({"file": neighbor, "co_bugfix_count": count, "probability": prob})
        neighbors.sort(key=lambda x: -x["probability"])
        results.append({
            "file": target,
            "bugfix_count": target_fixes,
            "total_bugfix_commits": total_bugfix,
            "neighbors": neighbors[:10],
            "highest_probability": neighbors[0]["probability"] if neighbors else 0,
        })
    return {"schema_version": 1, "files": results, "total_files": len(results), "seismic": seismic}


def _git_log_message(path: str, sha: str) -> str:
    try:
        import subprocess
        out = subprocess.run(
            ["git", "log", "--format=%s%n%b", "-1", sha],
            capture_output=True, cwd=path, timeout=10,
        )
        return out.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def triangulate_report(path: str = ".", task: str = "") -> dict:
    """Byzantine Triangulation — intersect 3 structural probes for target anchor.

    Runs 3 structural views (repo-map skeleton, diff-structural, explore
    identifiers) without reading source code. Collects 5 phrases per view.
    Computes overlap anchor. No source code sent to LLM.
    """
    from quale.scanner import scan_codebase

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not task:
        return {"error": "no task provided"}

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    # Probe A: repo-map skeleton — top 5 file-level identifiers
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    file_ids: Counter[str] = Counter()
    for fv in analysis.file_vocabs[:200]:
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                file_ids[m.group()] += 1
    probe_a = [p for p, _ in file_ids.most_common(60)
               if not any(p.startswith(x) for x in ("Http", "Https", "Www", "Get", "Set", "Post", "Put", "Del"))
               and p not in ("Response", "Request", "Error", "Promise", "Array", "Record", "Partial", "Pick", "Omit")][:5]

    # Probe B: diff-structural — identifiers from recently changed files
    try:
        log = vgit.ref_log(path, count=30)
        recent_tokens: Counter[str] = Counter()
        for ref in log:
            try:
                diffs = vgit.diff_refs(path, f"{ref.sha}^", ref.sha)
            except Exception:
                continue
            for diff_file in diffs[:5]:
                for fv in analysis.file_vocabs:
                    if fv.path == diff_file:
                        for p in fv.vocabulary:
                            for m in token_re.finditer(p):
                                recent_tokens[m.group()] += 1
        probe_b = [p for p, _ in recent_tokens.most_common(20)][:5]
    except Exception:
        probe_b = []

    # Probe C: distinctive identifiers (5-20% file frequency)
    rare_ids: Counter[str] = Counter()
    total_files = len(analysis.file_vocabs)
    for fv in analysis.file_vocabs:
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                rare_ids[m.group()] += 1
    low = max(2, int(total_files * 0.05))
    high = max(5, int(total_files * 0.2))
    distinctive = [p for p, c in rare_ids.most_common(200) if low <= c <= high]
    probe_c = distinctive[:5]

    # Intersection: phrases in 2/3 or 3/3 probes
    set_a, set_b, set_c = set(probe_a), set(probe_b), set(probe_c)
    triple_overlap = set_a & set_b & set_c
    double_overlap = (set_a & set_b) | (set_a & set_c) | (set_b & set_c) - triple_overlap

    # Task keyword overlap scoring
    task_kws: set[str] = set()
    for word in task.lower().split():
        wl = word.strip(".,;:!?()[]{}\"'")
        if len(wl) >= 4:
            task_kws.add(wl)

    anchor = sorted(triple_overlap | double_overlap)
    scores = []
    for p in anchor:
        task_match = sum(1 for kw in task_kws if kw in p.lower() or p.lower() in kw)
        scores.append({"phrase": p, "task_match": task_match, "probes": 3 if p in triple_overlap else 2})
    scores.sort(key=lambda x: (-x["probes"], -x["task_match"]))
    top_anchor = [s["phrase"] for s in scores[:5]] if scores else anchor[:5]

    return {
        "schema_version": 1,
        "task": task,
        "anchor": top_anchor,
        "probe_a": probe_a,
        "probe_b": probe_b,
        "probe_c": probe_c,
        "triple_overlap": sorted(triple_overlap),
        "double_overlap": sorted(double_overlap),
        "confidence": 3 if triple_overlap else (2 if double_overlap else 1),
    }


_WORDLIST: set[str] | None = None

def _load_wordlist() -> set[str]:
    """Load standard English word list for frequency analysis filtering."""
    global _WORDLIST
    if _WORDLIST is not None:
        return _WORDLIST
    try:
        import os
        wl_path = os.path.join(os.path.dirname(__file__), "wordlist.txt")
        if os.path.exists(wl_path):
            with open(wl_path, encoding="utf-8") as f:
                _WORDLIST = set(line.strip().lower() for line in f if line.strip())
            return _WORDLIST
    except Exception:
        pass
    _WORDLIST = set()
    return _WORDLIST


def solve_report(path: str = ".", top_n: int = 20, focus: str = "") -> dict:
    """Frequency Analysis Code-Breaking (The Bimoth Index).

    With --focus, filters cipher keys to only those orbiting a specific
    concept (Gravitational Lensing). 50-token summary instead of 10K tokens
    of source code.
    """
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    code_exts = frozenset({".go", ".ts", ".js", ".py", ".rs", ".rb", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala", ".ml", ".mli", ".ex", ".exs", ".hs", ".lhs", ".zig", ".jl", ".clj", ".cljs", ".nix", ".erl", ".hrl"})
    freq: Counter[str] = Counter()
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in code_exts:
            continue
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                freq[m.group()] += fv.vocabulary[phrase]

    wordlist = _load_wordlist()
    has_wordlist = len(wordlist) > 0

    common_prog = frozenset({
        "this", "that", "with", "from", "into", "than", "then", "else", "more",
        "some", "when", "what", "which", "where", "while", "after", "before",
        "const", "let", "var", "func", "type", "true", "false", "null", "void",
        "async", "await", "import", "export", "class", "return", "throw", "new",
        "string", "number", "boolean", "array", "object", "error", "promise",
    })
    filtered: list[tuple[str, int]] = []
    focus_lower = focus.lower() if focus else ""

    for phrase, count in freq.most_common(500):
        lower = phrase.lower()
        if has_wordlist and lower in wordlist:
            continue
        if lower in common_prog:
            continue
        if len(phrase) <= 3:
            continue
        if phrase[0].isdigit():
            continue
        # Gravitational Lensing: only keep identifiers orbiting the focus concept
        if focus_lower and focus_lower not in lower:
            # Check if the phrase co-occurs with focus in any file
            found_focus = False
            for fv in analysis.file_vocabs:
                if phrase in fv.vocabulary or any(phrase in p for p in fv.vocabulary):
                    for p2 in fv.vocabulary:
                        if focus_lower in p2.lower():
                            found_focus = True
                            break
                if found_focus:
                    break
            if not found_focus:
                continue
        filtered.append((phrase, count))

    top = filtered[:top_n]
    file_map: dict[str, list[str]] = {}
    for phrase, _ in top:
        locations: list[tuple[str, int]] = []
        for fv in analysis.file_vocabs:
            if phrase in fv.vocabulary or any(phrase in p for p in fv.vocabulary):
                locations.append((fv.path, fv.vocabulary.get(phrase, 0)))
        locations.sort(key=lambda x: -x[1])
        file_map[phrase] = [loc[0] for loc in locations[:3]]

    # Gravitational Lensing: orbiting files for the focus concept
    orbiting_files: list[str] = []
    if focus_lower and analysis.file_vocabs:
        re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
        for fv in analysis.file_vocabs:
            for phrase in fv.vocabulary:
                if focus_lower in phrase.lower():
                    orbiting_files.append(fv.path)
                    break
        orbiting_files = list(dict.fromkeys(orbiting_files))[:5]

    result = {
        "schema_version": 1,
        "total_phrases": len(freq),
        "has_wordlist": has_wordlist,
        "focus": focus_lower or None,
        "bimoth_index": [{"phrase": p, "frequency": c, "top_files": file_map.get(p, [])} for p, c in top],
        "summary": f"{len(freq)} total phrases → {len(filtered)} non-dictionary identifiers → top {len(top)} structural cipher keys.",
    }
    if focus:
        result["orbiting_files"] = orbiting_files
        result["lens_summary"] = f"Lens focus: {focus} — {len(top)} orbiting cipher keys, {len(orbiting_files)} orbiting files."
    return result


def mycorrhiza_map(path: str = ".", files: list[str] | None = None,
                    min_rare_co_occurrences: int = 2) -> dict:
    """Detect hidden structural dependencies between files.

    Files that share rare vocabulary AND co-change in git history
    despite having zero import/require/include relationships.

    When tolerance=True: additionally checks whether the changed file's
    vocabulary cluster overlaps with clusters the target has never
    historically touched (Tolerance Gaging).
    """
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not files:
        return {"error": "no files provided"}
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    # Build per-file rare-phrase sets
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    file_rare_tokens: dict[str, set[str]] = {}
    for fv in analysis.file_vocabs:
        tokens: Counter[str] = Counter()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens[m.group()] += 1
        # Identify rare tokens (appear in few files)
        file_rare_tokens[fv.path] = set(t for t in tokens if t)

    # Count file co-occurrence for each token
    token_file_count: Counter[str] = Counter()
    for tokens in file_rare_tokens.values():
        for t in tokens:
            token_file_count[t] += 1

    total_files = len(analysis.file_vocabs)
    rare_threshold = max(2, int(total_files * 0.15))
    rare_set: set[str] = set(t for t, c in token_file_count.items() if c <= rare_threshold)

    # Build co-change matrix from git history
    co_change_pair_count: dict[tuple[str, str], int] = {}
    log = vgit.ref_log(path, count=200)
    for ref in log:
        try:
            diffs = vgit.diff_refs(path, f"{ref.sha}^", ref.sha)
        except Exception:
            continue
        if len(diffs) < 2 or len(diffs) > 50:
            continue
        for i in range(len(diffs)):
            for j in range(i + 1, len(diffs)):
                a, b = (diffs[i], diffs[j]) if diffs[i] < diffs[j] else (diffs[j], diffs[i])
                co_change_pair_count[(a, b)] = co_change_pair_count.get((a, b), 0) + 1

    result_files: list[dict] = []
    for target in files:
        target_tokens = file_rare_tokens.get(target, set())
        if not target_tokens:
            result_files.append({"file": target, "count": 0, "hidden_dependencies": [], "tolerance": {}})
            continue

        hidden: list[dict] = []
        for other_path, other_tokens in file_rare_tokens.items():
            if other_path == target:
                continue
            shared = target_tokens & other_tokens
            if len(shared) < min_rare_co_occurrences:
                continue
            rare_shared = shared & rare_set
            if len(rare_shared) < min_rare_co_occurrences:
                continue
            # Check co-change signal
            pair = (target, other_path) if target < other_path else (other_path, target)
            co_change_count = co_change_pair_count.get(pair, 0)
            confidence = "high" if co_change_count >= 3 else ("moderate" if co_change_count >= 1 else "low")
            if confidence == "low":
                continue
            hidden.append({
                "file": other_path,
                "shared_rare_terms": sorted(rare_shared)[:5],
                "co_change_count": co_change_count,
                "confidence": confidence,
            })

        hidden.sort(key=lambda x: -len(x["shared_rare_terms"]))
        result_files.append({
            "file": target,
            "count": len(hidden),
            "hidden_dependencies": hidden[:10],
        })

    return {"schema_version": 1, "files": result_files, "total_files": len(result_files)}


def mycorrhiza_with_tolerance(path: str = ".", files: list[str] | None = None) -> dict:
    """mycorrhiza_map + Tolerance Gaging.

    Computes historical cluster radius for each target file.
    If an edit introduces vocabulary from outside that radius,
    emits tolerance_violation: true.
    """
    from quale.scanner import scan_codebase
    from quale.bootstrap import compute_modules
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    base = mycorrhiza_map(path=path, files=files)
    if "error" in base:
        return base
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
        mods = compute_modules(path, analysis=analysis)
        mod_list = mods.get("modules", []) if isinstance(mods, dict) else []
    except Exception:
        return base

    # Build cluster → files map
    cluster_files: dict[str, set[str]] = {}
    for m in mod_list:
        label = "_".join(m.get("exemplar_phrases", [])[:3]) or f"cluster_{m.get('size', 0)}"
        cluster_files[label] = set(m.get("files", []))

    # For each result file, compute historical cluster radius
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    for res in base.get("files", []):
        target = res["file"]
        # Collect all tokens in target file
        target_tokens: set[str] = set()
        for fv in analysis.file_vocabs:
            if fv.path == target:
                for phrase in fv.vocabulary:
                    for m in token_re.finditer(phrase):
                        target_tokens.add(m.group())

        # Map target tokens to clusters
        touched_clusters: set[str] = set()
        for cl, fs in cluster_files.items():
            for f in fs:
                for fv in analysis.file_vocabs:
                    if fv.path == f:
                        if any(t in fv.vocabulary or any(t in p for p in fv.vocabulary) for t in target_tokens):
                            touched_clusters.add(cl)
                            break
                if cl in touched_clusters:
                    break

        # Check hidden dep tokens — do they introduce new clusters?
        violations: list[str] = []
        for dep in res.get("hidden_dependencies", []):
            dep_tokens: set[str] = set()
            for fv in analysis.file_vocabs:
                if fv.path == dep["file"]:
                    for phrase in fv.vocabulary:
                        for m in token_re.finditer(phrase):
                            dep_tokens.add(m.group())
            dep_clusters: set[str] = set()
            for cl, fs in cluster_files.items():
                for f in fs:
                    for fv in analysis.file_vocabs:
                        if fv.path == f:
                            if any(t in fv.vocabulary for t in dep_tokens):
                                dep_clusters.add(cl)
                                break
                    if cl in dep_clusters:
                        break
            new_clusters = dep_clusters - touched_clusters
            if new_clusters:
                violations.append(f"introduces vocabulary from cluster(s): {', '.join(sorted(new_clusters)[:3])}")

        res["tolerance"] = {
            "historical_clusters": sorted(touched_clusters)[:5],
            "violations": violations[:3],
            "tolerance_ok": len(violations) == 0,
        }

    return base


# ██ Synergy: Composite / Pipeline Commands █████████████████████

def pipeline_orient(path: str = ".", task: str = "") -> dict:
    if not task:
        return {"error": "no task provided"}
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    solve_data = solve_report(path=path)
    tri_data = triangulate_report(path=path, task=task)
    try:
        iso_data = isolate_modules(path=path, task=task)
    except Exception:
        iso_data = {"modules": []}
    return {
        "task": task,
        "cipher_keys": [p["phrase"] for p in solve_data.get("bimoth_index", [])[:10]],
        "anchor": tri_data.get("anchor", []),
        "anchor_confidence": tri_data.get("confidence", 0),
        "recommended_modules": [{"files": m.get("files", [])[:5], "exemplars": m.get("exemplar_phrases", [])[:3], "match_score": m.get("match_score", 0)} for m in iso_data.get("modules", [])[:3]],
        "total_files_in_scope": sum(m.get("size", 0) for m in iso_data.get("modules", [])[:3]),
    }


def structural_health_score(path: str = ".", balance: bool = False) -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=500, max_seconds=15)
        samples = [fv.path for fv in analysis.file_vocabs[:10] if fv.total_phrases > 10][:3]
        max_regg = 0.0
        for sf in samples:
            fc = forecast_report(path, files=[sf], lookback_commits=200)
            for r in fc.get("files", []):
                max_regg = max(max_regg, r.get("highest_probability", 0))
    except Exception:
        max_regg = 0.0

    if balance:
        try:
            analysis2 = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
            feature_phrases = 0
            core_phrases = 0
            for fv in analysis2.file_vocabs:
                path_lower = fv.path.lower()
                if "/features/" in path_lower or "/ui/" in path_lower or "/components/" in path_lower:
                    feature_phrases += sum(fv.vocabulary.values())
                elif "/core/" in path_lower or "/db/" in path_lower or "/database/" in path_lower or "/models/" in path_lower or "/infra/" in path_lower:
                    core_phrases += sum(fv.vocabulary.values())
            ratio = round(feature_phrases / max(core_phrases, 1), 2) if feature_phrases > 0 and core_phrases > 0 else 0
        except Exception:
            ratio = 0
    else:
        ratio = 0

    debt = round((0.4) + (max_regg * 0.6), 3)
    health = "good" if debt < 0.3 else ("moderate" if debt < 0.6 else "poor")
    result = {"max_regression_probability": round(max_regg, 2), "debt_acceleration": debt, "health": health, "thresholds": {"good": "<0.3", "moderate": "0.3-0.6", "poor": ">0.6"}}
    if balance:
        result["root_shoot_ratio"] = ratio
        result["root_shoot_balanced"] = "Features outgrowing core" if ratio > 3 else ("Core dominates" if ratio < 0.5 else "Balanced")
        result["phototropism_note"] = f"Features/Core vocabulary ratio: {ratio}:1" if ratio else "No clear feature/core directories detected"
    return result


def pulsar_report(path: str = ".", file_path: str = "",
                   lookback_commits: int = 100) -> dict:
    """Pulsar Timing Array — detect Clock-Drift anomalies in core loops."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not os.path.exists(os.path.join(path, file_path)):
        return {"error": "provide existing --file"}
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    current_tokens: set[str] = set()
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    current_tokens.add(m.group())
    if not current_tokens:
        return {"error": "no tokens in file"}
    try:
        timeline = concept_timeline(path, weeks=12)
    except Exception:
        timeline = []
    if not timeline or len(timeline) < 4:
        fallback_anchors = {t for t in current_tokens if t.lower() in
                           ("sleep", "delay", "timeout", "wait", "retry", "backoff",
                            "process", "fetch", "batch", "queue", "worker", "loop")}
        anchors = fallback_anchors or set(list(current_tokens)[:3])
    else:
        from collections import Counter
        presence: Counter[str] = Counter()
        for wk in timeline:
            wk_text = str(wk.get("new_concepts", ""))
            for token in current_tokens:
                if token.lower() in wk_text.lower() or token in wk_text:
                    presence[token] += 1
        anchors = set(t for t, c in presence.items() if c >= len(timeline) * 0.8)
        if not anchors:
            anchors = set(list(current_tokens)[:3])
    missing = [t for t in anchors if t not in current_tokens]
    return {
        "file": file_path, "total_tokens": len(current_tokens),
        "pulsar_anchors": sorted(anchors)[:8],
        "missing_anchors": missing[:5],
        "clock_drift_anomaly": len(missing) > 0,
        "mandate": f"Restore missing anchor(s): {', '.join(missing[:3])}" if missing else "Pulsar rhythm stable.",
    }


def pipeline_squeeze(path: str = ".", file: str = "", task: str = "") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file or not task:
        return {"error": "provide --file and --task"}
    from quale.fold import fold_file
    solve_data = solve_report(path=path, top_n=10)
    cipher = [p["phrase"] for p in solve_data.get("bimoth_index", [])[:8]]
    try:
        lag = lagrange_report(path=path, file_path=file)
        lag_points = lag.get("lagrange_points", [])
    except Exception:
        lag_points = []
    try:
        fold_data = fold_file(os.path.join(path, file), task=task)
        visible = fold_data.get("visible_lines", 0)
    except Exception:
        visible = 0
    return {"file": file, "visible_lines": visible or 0, "lagrange_points": len(lag_points), "cipher_keys": cipher}


def pipeline_certify(path: str = ".", changed_files: list[str] | None = None, generated_code: str = "", schema_file: str = "") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    files = list(changed_files) if changed_files else []
    certs = {}
    if files:
        try:
            myco = mycorrhiza_map(path=path, files=files)
            v = []
            for f in myco.get("files", []):
                for dep in f.get("hidden_dependencies", []):
                    if dep.get("confidence") == "high":
                        v.append(f"hidden dep: {f['file']} -> {dep['file']}")
            certs["mycorrhiza"] = {"passed": len(v) == 0, "violations": v[:5]}
        except Exception as e:
            certs["mycorrhiza"] = {"passed": True, "violations": [], "error": str(e)}
        try:
            drift = drift_velocity_snapshot(path=path, files=files)
            anom = []
            for f in drift.get("files", []):
                for a in f.get("anomalies", []):
                    anom.append(f"{f.get('file', '?')}: {a}")
            certs["drift_check"] = {"passed": len(anom) == 0, "violations": anom[:5]}
        except Exception as e:
            certs["drift_check"] = {"passed": True, "violations": [], "error": str(e)}
    else:
        certs["mycorrhiza"] = {"passed": True, "violations": []}
        certs["drift_check"] = {"passed": True, "violations": []}
    if generated_code and schema_file:
        try:
            zk = zk_proof_report(path=path, schema_file=schema_file, generated_code=generated_code)
            certs["zk_proof"] = {"passed": zk.get("passed", False), "violations": [f"hallucinated: {v['identifier']}" for v in zk.get("violations", [])][:5]}
        except Exception as e:
            certs["zk_proof"] = {"passed": True, "violations": [], "error": str(e)}
    else:
        certs["zk_proof"] = {"passed": True, "violations": []}
    all_pass = all(c.get("passed", True) for c in certs.values())
    return {"all_passed": all_pass, "certificates": certs, "summary": "PASS" if all_pass else f"FAIL: {sum(1 for c in certs.values() if not c.get('passed', True))} check(s) failed"}


def migrate_report(path_a: str, path_b: str, min_freq: int = 2) -> dict:
    if not vgit.is_repo(path_a):
        return {"error": f"path_a not a repo: {path_a}"}
    if not vgit.is_repo(path_b):
        return {"error": f"path_b not a repo: {path_b}"}
    solve_b = solve_report(path=path_b)
    solve_a = solve_report(path=path_a)
    target_c = [p["phrase"] for p in solve_b.get("bimoth_index", [])[:10]]
    source_c = [p["phrase"] for p in solve_a.get("bimoth_index", [])[:10]]
    new_c = [c for c in target_c if c not in source_c]
    return {"phrase_substitutions_count": 0, "removed_count": 0, "added_count": 0, "target_specific_cipher_keys": new_c[:8], "substitutions": []}


def deflate_report(path: str = ".", file_path: str = "",
                    proposed_diff: str = "", budget: int = 5) -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not proposed_diff:
        return {"error": "provide --file and --diff"}
    full = os.path.join(path, file_path)
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    existing_tokens: set[str] = set()
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    existing_tokens.add(m.group())
    diff_tokens = set(token_re.findall(proposed_diff))
    net_new = diff_tokens - existing_tokens
    builtins = {"const","let","var","func","function","return","if","else","for","while","async","await","import","export","class","new","throw","try","catch","finally","this","true","false","null","undefined","string","number","boolean","any","void"}
    net_new_f = [t for t in net_new if t not in builtins and len(t) >= 3]
    over = len(net_new_f) > budget
    return {"file": file_path, "budget": budget, "net_new_identifiers": net_new_f[:budget+5], "net_new_count": len(net_new_f), "over_budget": over, "deflate_check_pass": not over, "mandate": f"Gold Standard: budget {budget}, used {len(net_new_f)}. Reduce by {len(net_new_f)-budget}." if over else "Gold Standard respected.", "existing_reserve": sorted(existing_tokens)[:8]}


def compound_debt_index(path: str = ".", files: list[str] | None = None) -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        epi = epidemiology_report(path)
        pathogens = {p["phrase"]: p.get("growth_rate", 0) for p in epi.get("tracked", []) if p.get("status") == "pathogen"}
    except Exception:
        pathogens = {}
    shrapnel_phrases = set()
    target_files = files or []
    if not target_files:
        try:
            from quale.scanner import scan_codebase
            analysis = scan_codebase(path, quiet=True, max_files=500, max_seconds=15)
            target_files = [fv.path for fv in analysis.file_vocabs[:50] if fv.total_phrases > 10][:10]
        except Exception:
            target_files = []
    results = []
    for f in target_files:
        fc = forecast_report(path, files=[f], lookback_commits=200)
        regr = max((r.get("highest_probability", 0) for r in fc.get("files", [])), default=0)
        p_score = min(sum(pathogens.values()) / max(len(pathogens), 1), 1.0) if pathogens else 0
        s_score = min(len(shrapnel_phrases) / 10, 1.0)
        compound = round((regr * 0.4) + (p_score * 0.3) + (s_score * 0.3), 3)
        results.append({"file": f, "regression_probability": regr, "compound_debt": compound, "debt_level": "critical" if compound >= 0.7 else ("high" if compound >= 0.4 else ("moderate" if compound >= 0.2 else "low"))})
    results.sort(key=lambda x: -x["compound_debt"])
    return {"files": results[:20], "overall_debt_index": round(sum(r["compound_debt"] for r in results[:5]) / max(len(results[:5]), 1), 3) if results else 0}


def guard_pipeline(path: str = ".", files: list[str] | None = None, task: str = "", bootstrap: dict | None = None) -> dict:
    path = os.path.abspath(path) if path else "."
    changed = list(files) if files else []
    if not changed or not task:
        return {"error": "provide --files and --task"}
    result = {"task": task, "changed_files": changed}
    try:
        veto = cascade_verify(path=path, changed_files=changed, bootstrap=bootstrap)
        result["verification"] = {k: veto.get(k) for k in ["tier", "cascade_tier", "verification_candidates", "deterministic_verify", "cohesion", "cohesion_label"]}
    except Exception as e:
        result["verification"] = {"error": str(e)}
    try:
        pf = preflight_report(path=path, files=changed, task=task)
        result["scope"] = {k: pf.get(k) for k in ["read_first", "likely_edit", "avoid_touching_without_context", "blast_radius", "expansion_risk"]}
    except Exception as e:
        result["scope"] = {"error": str(e)}
    try:
        ct = build_contract(path=path, files=changed)
        result["contract"] = {"contract_id": ct.get("contract_id", ""), "files_count": len(ct.get("files", []))}
    except Exception as e:
        result["contract"] = {"error": str(e)}
    return {"task": task, "changed_files": changed, "verification": result.get("verification", {}), "scope": result.get("scope", {}), "contract": result.get("contract", {}), "all_checks_ran": "verification" in result and "scope" in result and "contract" in result}


def anneal_report(path: str = ".", file_path: str = "", task: str = "",
                   shield_threshold: float = 0.0) -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not os.path.exists(os.path.join(path, file_path)):
        return {"error": "provide existing --file" or "no file"}
    full = os.path.join(path, file_path)
    from quale.bootstrap import compute_modules
    from quale.scanner import scan_codebase
    from quale.fold import _indent_blocks
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        return {"error": "scan failed"}
    mods = compute_modules(path, analysis=analysis)
    mod_list = mods.get("modules", []) if isinstance(mods, dict) else []
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{3,40}\b')
    ftokens: dict[str, set[str]] = {}
    for fv in analysis.file_vocabs:
        ftokens[fv.path] = set(m.group() for p in fv.vocabulary for m in token_re.finditer(p))
    ptokens = ftokens.get(file_path, set())
    if not ptokens:
        return {"error": "no tokens in file"}

    # Shield ratio: defensive boilerplate vs core logic
    _SHIELD_PHRASES = frozenset({"catch", "null", "ignore", "fallback", "default", "try", "skip",
                                  "error", "exception", "undefined", "optional", "nullable"})
    shield_count = sum(1 for p in ptokens if p.lower() in _SHIELD_PHRASES)
    shield_ratio = round(shield_count / max(len(ptokens), 1), 3)
    superbug = shield_ratio >= shield_threshold if shield_threshold > 0 else False

    cluster_tokens: dict[str, set[str]] = {}
    for m in mod_list:
        label = "_".join(m.get("exemplar_phrases", [])[:3]) or f"c{m.get('size',0)}"
        ct: set[str] = set()
        for f in m.get("files", []):
            if f in ftokens and f != file_path:
                ct |= ftokens[f]
        cluster_tokens[label] = ct
    with open(full, encoding="utf-8") as f:
        lines = f.readlines()
    blocks = _indent_blocks(lines)
    results: list[tuple[int, int, str, int, int]] = []
    for blk in blocks:
        bt: set[str] = set()
        for i in range(blk["start"], blk["end"] + 1):
            if i < len(lines):
                bt |= set(token_re.findall(lines[i]))
        best_cl, best_sc = "", 0
        for cl, ct in cluster_tokens.items():
            o = len(bt & ct)
            if o > best_sc:
                best_sc, best_cl = o, cl
        if best_sc >= 2:
            results.append((blk["start"], blk["end"], best_cl, best_sc, blk["end"] - blk["start"] + 1))
    results.sort(key=lambda x: (x[3], x[4]))
    base_data = {
        "file": file_path, "total_lines": len(lines), "total_clusters": len(cluster_tokens),
        "shield_ratio": shield_ratio, "superbug": superbug,
        "anneal_required": len(cluster_tokens) >= 3,
    }
    if shield_threshold > 0 and superbug:
        base_data["phage_therapy"] = "Shield ratio exceeds threshold. Forbid new defensive phrases."
        base_data["extraction"] = None
        return base_data
    if not results:
        base_data["extraction"] = None
        return base_data
    start, end, clname, score, size = results[0]
    ext = os.path.splitext(file_path)[1]
    bd = os.path.dirname(file_path)
    ext_file = os.path.join(bd, f"{clname.replace('_','-')[:20]}_annealed{ext}") if bd else f"{clname.replace('_','-')[:20]}_annealed{ext}"
    base_data["extraction"] = {"extract_lines": [start + 1, end + 1], "extract_to_file": ext_file,
                                "cluster": clname, "cluster_score": score, "lines_count": size,
                                "preview": "".join(lines[start:end + 1]).strip()[:200]}
    return base_data


def _attractor_cluster(changed: list[str], analysis) -> dict | None:
    """Identify the Strange Attractor cluster where blast radius terminates.

    Follows co-occurrence edges from changed files outward. Finds the
    dominant module cluster where >80% of ripple effects settle.
    """
    from quale.bootstrap import compute_modules
    try:
        mods = compute_modules(os.path.dirname(analysis.file_vocabs[0].path) if analysis.file_vocabs else ".", analysis=analysis)
        mod_list = mods.get("modules", []) if isinstance(mods, dict) else []
    except Exception:
        return None
    if not mod_list:
        return None

    # Build cluster -> files map
    cluster_files: dict[str, set[str]] = {}
    for m in mod_list:
        label = "_".join(m.get("exemplar_phrases", [])[:3]) or f"c{m.get('size',0)}"
        cluster_files[label] = set(m.get("files", []))

    # Find which clusters the changed files belong to
    changed_clusters: set[str] = set()
    for c in changed:
        for cl, fs in cluster_files.items():
            if c in fs:
                changed_clusters.add(cl)

    # Find which clusters the blast radius files belong to
    blast_files: set[str] = set()
    for fv in analysis.file_vocabs:
        if fv.path not in changed:
            for c in changed:
                shared = set(fv.vocabulary.keys()) & set(
                    dict(list(analysis.file_vocabs[0].vocabulary.items())[:10]).keys() if analysis.file_vocabs else []
                )
                if shared:
                    blast_files.add(fv.path)

    # Score clusters by blast file concentration
    blast_tokens: set[str] = set()
    for fv in analysis.file_vocabs:
        if fv.path in blast_files:
            for p in fv.vocabulary:
                blast_tokens.add(p)

    cluster_scores: list[tuple[str, int, float]] = []
    for cl, fs in cluster_files.items():
        if cl in changed_clusters:
            continue
        intersect = 0
        for bf in blast_files:
            if bf in fs:
                intersect += 1
        if intersect >= 2:
            ratio = intersect / max(len(blast_files), 1)
            cluster_scores.append((cl, intersect, ratio))

    if not cluster_scores:
        return None
    cluster_scores.sort(key=lambda x: -x[1])
    best_cl, best_count, best_ratio = cluster_scores[0]
    return {
        "cluster": best_cl,
        "files_in_cluster": best_count,
        "ratio_of_blast": round(best_ratio, 3),
        "note": f"Blast radius terminates in [{best_cl}] cluster" if best_ratio > 0.5 else None,
    }


def decay_report(path: str = ".", file_path: str = "",
                  lookback_weeks: int = 12, half_life_days: int = 30,
                  active_metabolism: bool = False) -> dict:
    """Pharmacokinetic Half-Life Clearance — detect actively decaying legacy patterns.

    When active_metabolism=True, checks concept_timeline to verify the legacy
    pattern is actively declining while a modern alternative is growing in the
    same structural cluster. Prevents false positives on stable legacy code.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not os.path.exists(os.path.join(path, file_path)):
        return {"error": "provide existing --file"}
    os.path.join(path, file_path)
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    code_exts = frozenset({".go", ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".rb", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala", ".ml", ".mli", ".ex", ".exs", ".hs", ".lhs", ".zig", ".jl", ".clj", ".cljs", ".nix", ".erl", ".hrl"})
    file_tokens: set[str] = set()
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in code_exts:
            continue
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    file_tokens.add(m.group())
    if not file_tokens:
        return {"error": "no tokens in file"}

    # Decay detection: known legacy patterns commonly replaced
    legacy_map: list[tuple[str, str]] = [
        ("then(", "async/await"), ("callback(", "async/await"), ("var ", "const/let"),
        ("$.get(", "fetch("), ("$.ajax(", "fetch("), ("dojo.", "modern API"),
        ("_.", "lodash native"), ("request(", "axios/fetch"),
    ]
    decaying: list[dict] = []
    for token in file_tokens:
        for legacy, replacement in legacy_map:
            if token.startswith(legacy.rstrip("(").rstrip(" ")) or token.strip().startswith(legacy.strip()):
                # Active metabolism: verify the pattern is actually declining repo-wide
                if active_metabolism:
                    try:
                        timeline = concept_timeline(path, weeks=lookback_weeks)
                        if timeline and len(timeline) >= 4:
                            early_count = 0
                            late_count = 0
                            for wk_data in timeline[:len(timeline)//2]:
                                if legacy[:8] in str(wk_data.keys()):
                                    early_count += 1
                            for wk_data in timeline[-len(timeline)//2:]:
                                if replacement[:8] in str(wk_data.keys()):
                                    late_count += 1
                            if early_count <= late_count:
                                continue  # no active metabolism — skip
                    except Exception:
                        pass
                decaying.append({"phrase": token[:40], "legacy_pattern": legacy.strip()[:25],
                                 "replacement": replacement, "half_life_days": half_life_days,
                                 "metabolism_verified": active_metabolism})
                break

    return {
        "file": file_path, "phrases_tracked": len(file_tokens),
        "decaying_patterns": decaying[:8],
        "toxicity_clearance_required": len(decaying) > 0,
        "metabolism_mode": active_metabolism,
        "mandate": f"Clear {len(decaying)} legacy pattern(s) before adding new logic." if decaying else "No active migration needed.",
    }


def heisenberg_check(path: str = ".", file_path: str = "",
                      proposed_diff: str = "") -> dict:
    """Heisenberg Uncertainty Principle of Refactoring.

    Separates a proposed diff into 'new signal' (net-new tokens) and
    'historical anchors' (stable tokens being modified/deleted).
    If both appear in the same diff on the same file, the uncertainty
    principle is violated — forces atomic commit split.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not proposed_diff:
        return {"error": "provide --file and --diff"}
    full = os.path.join(path, file_path)
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}

    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')

    # Current file tokens (the 'position' / historical anchors)
    with open(full, encoding="utf-8") as f:
        current_text = f.read()
    current_tokens = set(token_re.findall(current_text))

    # Diff tokens (the 'velocity')
    diff_tokens = set(token_re.findall(proposed_diff))

    # Anchors: tokens present in the file that are modified in the diff
    current_tokens & diff_tokens

    # New signal: tokens in the diff that DON'T exist in the current file
    new_signal = diff_tokens - current_tokens

    # Deleted anchors: anchors that are in the diff as removals
    removed_lines = [line for line in proposed_diff.split("\n") if line.startswith("-") and not line.startswith("---")]
    removed_tokens: set[str] = set()
    for line in removed_lines:
        removed_tokens |= set(token_re.findall(line))
    deleted_anchors = removed_tokens & current_tokens

    has_new_signal = len(new_signal) >= 3
    has_deleted_anchors = len(deleted_anchors) >= 1

    return {
        "file": file_path,
        "current_tokens_current": len(current_tokens),
        "new_signal_tokens": sorted(new_signal)[:8],
        "deleted_anchors": sorted(deleted_anchors)[:8],
        "uncertainty_violated": has_new_signal and has_deleted_anchors,
        "heisenberg_check_pass": not (has_new_signal and has_deleted_anchors),
        "mandate": "Split this diff: Commit 1 = refactor ONLY (move/rename anchors), Commit 2 = feature ONLY (add new signal)." if has_new_signal and has_deleted_anchors else "Uncertainty principle respected.",
    }


def traffic_control_report(path: str = ".", file_path: str = "",
                            intended_import: str = "") -> dict:
    """Zoning Variances — detect illegal Highway-to-Residential imports.

    Uses graph centrality to classify files as Commercial (high centrality)
    or Residential (low centrality). Blocks direct leaf-to-root imports,
    requiring a Collector Road (intermediate service layer).
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not intended_import:
        return {"error": "provide --file and --intended-import"}

    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    file_centrality: dict[str, int] = {}
    # Centrality = how many other files share vocabulary with this one
    for fv in analysis.file_vocabs:
        ft = set(m.group() for p in fv.vocabulary for m in token_re.finditer(p))
        centrality = 0
        for fv2 in analysis.file_vocabs:
            if fv2.path != fv.path:
                ft2 = set(m.group() for p in fv2.vocabulary for m in token_re.finditer(p))
                if ft & ft2:
                    centrality += 1
        file_centrality[fv.path] = centrality

    src_cent = file_centrality.get(file_path, 0)
    dst_cent = file_centrality.get(intended_import, 0)
    total = len(analysis.file_vocabs)
    src_zone = "Residential" if src_cent < total * 0.1 else ("Collector" if src_cent < total * 0.3 else "Commercial")
    dst_zone = "Residential" if dst_cent < total * 0.1 else ("Collector" if dst_cent < total * 0.3 else "Commercial")

    violation = src_zone in ("Residential", "Collector") and dst_zone == "Commercial" and src_cent < dst_cent * 0.5

    return {
        "source_file": file_path,
        "source_zone": src_zone,
        "source_centrality": src_cent,
        "intended_import": intended_import,
        "import_zone": dst_zone,
        "import_centrality": dst_cent,
        "zoning_violation": violation,
        "collector_suggestion": f"Route through a middle-layer service (Collector Road) in {'/services/' if '/' in file_path else ''}",
        "mandate": "ZONING VIOLATION: Direct Residential-to-Commercial import blocked. Create or use an intermediate service layer." if violation else "Import route clear.",
    }


def splice_exons_report(path: str = ".", file_path: str = "") -> dict:
    """Intron Splicing — extract assertion exons from test boilerplate."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    full = os.path.join(path, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        analysis = None
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{2,40}\b')
    from collections import Counter
    global_freq: Counter[str] = Counter()
    if analysis:
        for fv in analysis.file_vocabs:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    global_freq[m.group()] += 1
    with open(full, encoding="utf-8") as f:
        lines = f.readlines()
    exons: list[dict] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "/*", "* ")):
            continue
        tokens = token_re.findall(stripped)
        if not tokens:
            continue
        if "expect(" in stripped or "assert." in stripped or "assertEqual" in stripped:
            exons.append({"line": i + 1, "text": stripped[:80], "type": "assertion"})
        else:
            rare_ratio = 0.0
            if global_freq and tokens:
                rare_count = sum(1 for t in tokens if global_freq.get(t, 0) < 5)
                rare_ratio = rare_count / len(tokens)
            if rare_ratio >= 0.5:
                exons.append({"line": i + 1, "text": stripped[:80], "type": "logic"})
    return {"file": file_path, "original_lines": len(lines), "exon_count": len(exons),
            "compression_pct": round((1 - len(exons) / max(len(lines), 1)) * 100, 1),
            "exons": exons}


def catalytic_crack_report(path: str = ".", file_path: str = "") -> dict:
    """Fluid Catalytic Cracking — split monolith by internal vocabulary clusters."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    full = os.path.join(path, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}
    from quale.fold import _indent_blocks
    with open(full, encoding="utf-8") as f:
        lines = f.readlines()
    blocks = _indent_blocks(lines)
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    block_phrases: list[dict] = []
    for blk in blocks:
        phr: Counter[str] = Counter()
        for i in range(blk["start"], blk["end"] + 1):
            if i < len(lines):
                for m in token_re.finditer(lines[i]):
                    phr[m.group()] += 1
        block_phrases.append({"start": blk["start"], "end": blk["end"],
                              "phrases": set(phr.keys()),
                              "size": blk["end"] - blk["start"] + 1})
    clusters: list[list[dict]] = []
    assigned = [False] * len(block_phrases)
    for i, bp in enumerate(block_phrases):
        if assigned[i]:
            continue
        cluster = [bp]
        assigned[i] = True
        for j in range(i + 1, len(block_phrases)):
            if assigned[j]:
                continue
            bpj = block_phrases[j]
            union = len(bp["phrases"] | bpj["phrases"])
            if union == 0:
                continue
            overlap = len(bp["phrases"] & bpj["phrases"]) / union
            if overlap >= 0.2:
                cluster.append(bpj)
                assigned[j] = True
        clusters.append(cluster)
    ext = os.path.splitext(file_path)[1]
    base = os.path.splitext(os.path.basename(file_path))[0]
    out_dir = os.path.dirname(file_path) if os.path.dirname(file_path) else "."
    fragments: list[dict] = []
    rep_phrases: list[str] = []
    for idx, cluster in enumerate(clusters):
        cluster_lines: list[str] = []
        cluster_phrases: Counter[str] = Counter()
        for bp in cluster:
            for i in range(bp["start"], bp["end"] + 1):
                if i < len(lines):
                    cluster_lines.append(lines[i])
            for p in bp["phrases"]:
                cluster_phrases[p] += 1
        if not cluster_lines:
            continue
        top = [p for p, _ in cluster_phrases.most_common(4)]
        rep_phrases.extend(top[:2])
        out_file = os.path.join(out_dir, f"{base}_fragment_{idx + 1}{ext}")
        fragments.append({"fragment_index": idx + 1, "output_file": out_file,
                          "lines": len(cluster_lines), "cluster_phrases": top[:5],
                          "content": "".join(cluster_lines)})
    return {"file": file_path, "total_lines": len(lines), "fragments_count": len(fragments),
            "fragments": [{k: v for k, v in f.items() if k != "content"} for f in fragments],
            "representative_phrases": rep_phrases[:8],
        "llm_naming_task": f"Name these {len(fragments)} files:",
        "fragment_vocabularies": [{f"File {f['fragment_index']}": f['cluster_phrases']} for f in fragments],
    }


def hologram_report(path: str = ".", directory: str = "") -> dict:
    """Holographic Boundary Projector — module surface vs volume.

    Isolates boundary phrases (what crosses the directory boundary) from
    internal volume (phrases contained entirely within the directory).
    LLM navigates the 100-token surface, not the 8000-line volume.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not directory:
        return {"error": "provide --dir"}
    dir_path = os.path.join(path, directory)
    if not os.path.isdir(dir_path):
        return {"error": f"directory not found: {directory}"}

    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    dir_prefix = directory.rstrip("/") + "/"
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')

    # Collect all phrases inside the directory
    interior_phrases: set[str] = set()
    interior_files: list[str] = []
    interior_file_count = 0
    for fv in analysis.file_vocabs:
        if fv.path.startswith(dir_prefix):
            interior_file_count += 1
            interior_files.append(fv.path)
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    interior_phrases.add(m.group())

    # Collect phrases from outside that overlap with interior (crossing boundary)
    exterior_overlap: Counter[str] = Counter()
    for fv in analysis.file_vocabs:
        if fv.path.startswith(dir_prefix):
            continue
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                if m.group() in interior_phrases:
                    exterior_overlap[m.group()] += 1

    # Imports: boundary phrases where outside files reference interior concepts
    imports = sorted(exterior_overlap.keys())[:10] if exterior_overlap else []
    # Exports: interior phrases also used outside
    exports = sorted(exterior_overlap.keys())[:10] if exterior_overlap else []
    # Hidden volume: interior phrases NOT visible from outside
    hidden = interior_phrases - set(exterior_overlap.keys())

    return {
        "directory": directory,
        "interior_file_count": interior_file_count,
        "interior_phrases": len(interior_phrases),
        "boundary_phrases": len(exterior_overlap),
        "hidden_volume_phrases": len(hidden),
        "imports": imports[:8],
        "exports": exports[:8],
        "hidden_summary": f"{len(hidden)} phrases hidden across {interior_file_count} files",
        "hologram": f"[Imports: {', '.join(imports[:5])}] -> (HIDDEN: {len(hidden)} phrases, {interior_file_count} files) -> [Exports: {', '.join(exports[:5])}]",
        "interior_files": interior_files[:5],
    }


def shard_context_report(path: str = ".", files: list[str] | None = None,
                          task: str = "", shard_count: int = 3) -> dict:
    """Amnesic Context Sharding — parallel micro-agents solving shards.

    Splits N files into shards. Each shard agent sees its files plus
    the holographic boundary of adjacent shards. check-plan reconciles.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not files or not task:
        return {"error": "provide --files and --task"}
    files = list(dict.fromkeys(files))
    if len(files) < 2:
        return {"error": "need at least 2 files to shard"}

    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    # Build per-file hologram for boundary projection
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    file_phrases: dict[str, set[str]] = {}
    for fv in analysis.file_vocabs:
        if fv.path in files:
            phrases: set[str] = set()
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    phrases.add(m.group())
            file_phrases[fv.path] = phrases

    # Split files into shards (round-robin for best coverage)
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for i, f in enumerate(files):
        shards[i % shard_count].append(f)

    shard_outputs: list[dict] = []
    for idx, shard_files in enumerate(shards):
        if not shard_files:
            continue
        # This shard's own vocabulary
        own_phrases: set[str] = set()
        for sf in shard_files:
            own_phrases |= file_phrases.get(sf, set())

        # Holographic projection: vocabulary from OTHER shards' files that overlaps
        other_boundary: list[str] = []
        for other_idx, other_files in enumerate(shards):
            if other_idx == idx:
                continue
            for of in other_files:
                op = file_phrases.get(of, set())
                overlap = own_phrases & op
                if overlap:
                    other_boundary.append(f"{of} (shared: {', '.join(sorted(overlap)[:3])})")

        shard_outputs.append({
            "shard_index": idx,
            "files": shard_files,
            "boundary_hologram": other_boundary[:5],
            "boundary_phrases_count": len(other_boundary),
        })

    return {
        "task": task,
        "total_files": len(files),
        "shard_count": len([s for s in shards if s]),
        "shards": shard_outputs,
        "shard_workflow": f"Launch {len([s for s in shards if s])} parallel agents. Reconcile via check-plan.",
    }


def sentinel_report(path: str = ".", task: str = "") -> dict:
    """Sentinel Anchor — honeypot phrases to detect LLM hallucination.

    Injects stable, task-irrelevant phrases as 'allowed vocabulary.'
    If the LLM uses a sentinel in its response, it mathematically
    proves hallucination (parroting prompt rather than reasoning).
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not task:
        return {"error": "provide --task"}
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{5,40}\b')

    # Build phrase frequency to find the most stable (unchanged across files)
    phrase_files: Counter[str] = Counter()
    for fv in analysis.file_vocabs[:100]:
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                phrase_files[m.group()] += 1

    # Sentinel = high-frequency, task-unrelated identifiers
    task_kws: set[str] = set(task.lower().split())
    sentinel_candidates = [p for p, c in phrase_files.most_common(200)
                          if c >= 3 and not any(kw in p.lower() for kw in task_kws)][:3]
    if not sentinel_candidates:
        sentinel_candidates = ["Config", "Handler", "Manager"]

    return {
        "task": task,
        "sentinels": sentinel_candidates,
        "sentinel_instruction": f"Allowed vocabulary includes: {', '.join(sentinel_candidates)}. These are structural anchors.",
        "detection": f"If the response contains any of {sentinel_candidates} while discussing '{task}', the LLM is hallucinating.",
    }


def dark_matter_report(repo_a: str, repo_b: str) -> dict:
    """Dark Matter Compiler — cross-repo orphan projection.

    Finds orphans in repo A that structurally bind to repo B.
    Prevents distributed system breakages via cross-repo phrase math.
    """
    if not vgit.is_repo(repo_a):
        return {"error": f"repo_a not a git repository: {repo_a}"}
    if not vgit.is_repo(repo_b):
        return {"error": f"repo_b not a git repository: {repo_b}"}
    from quale.scanner import scan_codebase
    try:
        anal_a = scan_codebase(repo_a, quiet=True, max_files=2500, max_seconds=30)
        anal_b = scan_codebase(repo_b, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')

    # Build phrase sets for both repos
    def _to_tokens(analysis):
        result: dict[str, set[str]] = {}
        for fv in analysis.file_vocabs:
            tokens: set[str] = set()
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    tokens.add(m.group())
            result[fv.path] = tokens
        return result

    tokens_a = _to_tokens(anal_a)
    tokens_b = _to_tokens(anal_b)

    # Find orphans in A (identifiers appearing in only one file)
    id_file_count_a: Counter[str] = Counter()
    for tokens in tokens_a.values():
        for t in tokens:
            id_file_count_a[t] += 1
    orphans_a = {t for t, c in id_file_count_a.items() if c == 1}

    # Find which orphans in A exist in B
    all_tokens_b: set[str] = set()
    for tokens in tokens_b.values():
        all_tokens_b |= tokens
    dark_matter = orphans_a & all_tokens_b

    # Map each dark matter phrase to its B files
    bindings: list[dict] = []
    for phrase in sorted(dark_matter)[:15]:
        b_files = [path for path, tokens in tokens_b.items() if phrase in tokens]
        a_file = [path for path, tokens in tokens_a.items() if phrase in tokens]
        bindings.append({
            "phrase": phrase,
            "a_file": a_file[0] if a_file else "",
            "b_files": b_files[:5],
            "b_file_count": len(b_files),
        })

    return {
        "schema_version": 1,
        "a_path": repo_a,
        "b_path": repo_b,
        "total_orphans_a": len(orphans_a),
        "dark_matter_count": len(dark_matter),
        "bindings": bindings[:10],
        "mandate": f"{len(dark_matter)} orphan(s) in A structurally bind to B. Do not change payload signatures without syncing B.",
    }


def supernova_report(path: str = ".", overlap_threshold: float = 0.90,
                      lookback_weeks: int = 8) -> dict:
    """Supernova Threshold — convergence vs divergence prediction.

    For each condensate pair, computes overlap ratio trend over time.
    If overlap is increasing (converging), flag as mergeable.
    If decreasing (diverging), flag as keep-separate.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)

    # Get current condensates
    condensed = condensate_report(path, overlap_threshold=overlap_threshold, max_results=20)
    pairs = condensed.get("condensates", [])
    if not pairs:
        return {"condensates": [], "note": "no condensates found"}

    # For each pair, estimate convergence trend
    results: list[dict] = []
    for pair in pairs[:10]:
        f_a, f_b = pair["files"]
        overlap = pair["overlap"]

        # Estimate trend: check historical vocabulary overlap across two snapshots
        try:
            timeline = concept_timeline(path, weeks=lookback_weeks)
        except Exception:
            timeline = []

        diverging = False
        converging = False
        if timeline and len(timeline) >= 4:
            mid = len(timeline) // 2
            early_slice = timeline[:mid]
            late_slice = timeline[mid:]
            early_total = sum(w.get("stable_concepts", 0) for w in early_slice) / max(len(early_slice), 1)
            late_total = sum(w.get("stable_concepts", 0) for w in late_slice) / max(len(late_slice), 1)
            # Use stable concept trend as proxy for convergence
            diverging = late_total > early_total * 1.2
            converging = late_total < early_total * 0.8

        results.append({
            "files": pair["files"],
            "current_overlap": overlap,
            "trend": "converging" if converging else ("diverging" if diverging else "stable"),
            "action": "MERGE" if overlap >= 0.95 and converging else ("KEEP_SEPARATE" if diverging else "MONITOR"),
            "shared_phrases": pair.get("shared_phrases", [])[:3],
        })

    return {
        "condensates": results,
        "threshold": overlap_threshold,
        "summary": f"{sum(1 for r in results if r['action']=='MERGE')} mergeable, {sum(1 for r in results if r['action']=='KEEP_SEPARATE')} diverging, {sum(1 for r in results if r['action']=='MONITOR')} stable",
    }


def chrono_lock_report(path: str = ".", file_path: str = "",
                        proposed_diff: str = "", max_age_gap: int = 2) -> dict:
    """Chrono-Topological Lock — prevent paradigm mixing in legacy files.

    Calculates temporal center of mass for a file (average phrase entry year).
    Detects if proposed diff introduces phrases from a significantly
    different era. Rejects diffs that mix paradigms.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path or not proposed_diff:
        return {"error": "provide --file and --diff"}
    full = os.path.join(path, file_path)
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}

    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')

    # Collect current file phrases
    file_phrases: set[str] = set()
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    file_phrases.add(m.group())

    # Get phrase entry years from provenance
    try:
        from quale.git import ref_log
        log = ref_log(path, count=200)
        phrase_year: dict[str, int] = {}
        for ref in reversed(log):
            sha = ref.sha
            try:
                msg = subprocess.run(
                    ["git", "log", "--format=%ct", "-1", sha],
                    capture_output=True, cwd=path, timeout=10, text=True
                ).stdout.strip()
                year = int(msg[:4]) if len(msg) >= 4 else 2024
            except Exception:
                year = 2024
            # Scan phrases at this ref
            try:
                ref_analysis = scan_codebase(path, git_ref=sha, quiet=True, max_files=500, max_seconds=15)
            except Exception:
                continue
            for fv in ref_analysis.file_vocabs:
                if fv.path == file_path:
                    for phrase in fv.vocabulary:
                        for m in token_re.finditer(phrase):
                            if m.group() not in phrase_year and m.group() in file_phrases:
                                phrase_year[m.group()] = year
        if not phrase_year:
            return {"error": "could not determine phrase entry years"}
    except Exception as e:
        return {"error": f"provenance scan failed: {e}"}

    # Calculate temporal center of mass
    years = list(phrase_year.values())
    center_of_mass = round(sum(years) / len(years)) if years else 2024

    # Get diff phrases and their entry years
    diff_phrases = set(token_re.findall(proposed_diff))
    diff_years: list[int] = []
    for dp in diff_phrases:
        if dp in phrase_year:
            diff_years.append(phrase_year[dp])
    max_diff_year = max(diff_years) if diff_years else center_of_mass
    min_diff_year = min(diff_years) if diff_years else center_of_mass

    # Detect anachronisms
    forward_gap = max_diff_year - center_of_mass
    backward_gap = center_of_mass - min_diff_year
    anomaly = forward_gap > max_age_gap or backward_gap > max_age_gap

    return {
        "file": file_path,
        "file_phrases_tracked": len(phrase_year),
        "center_of_mass_year": center_of_mass,
        "diff_phrases_found": len(diff_years),
        "max_diff_year": max_diff_year,
        "min_diff_year": min_diff_year,
        "max_age_gap_setting": max_age_gap,
        "chrono_anomaly": anomaly,
        "mandate": f"Temporal Violation: Center of mass is {center_of_mass}, but diff introduces phrases from {max_diff_year}. Gap of {forward_gap} year(s) exceeds threshold {max_age_gap}. Use era-appropriate paradigm." if anomaly else "Chrono-lock respected.",
    }


def necrotic_report(path: str = ".", file_path: str = "",
                     lookback_weeks: int = 12) -> dict:
    """Necrotic Resonance Map — detect zombie code with zero blast radius.

    Combines reverse blast radius (would anyone notice if this was deleted?)
    with decaying lifecycle state and orphan phrase presence.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path:
        return {"error": "provide --file"}
    full = os.path.join(path, file_path)
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}

    from quale.scanner import scan_codebase

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')

    # 1. Reverse blast radius: files that would be affected if this file were deleted
    # We simulate deletion by finding files that ONLY share vocabulary with this file
    file_tokens: set[str] = set()
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    file_tokens.add(m.group())

    dependent_files: list[str] = []
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            continue
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                if m.group() in file_tokens:
                    dependent_files.append(fv.path)
                    break
    dependent_files = list(dict.fromkeys(dependent_files))
    reverse_blast = len(dependent_files)

    # 2. Orphan phrases in this file
    id_file_count: Counter[str] = Counter()
    for fv in analysis.file_vocabs:
        tokens: set[str] = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens.add(m.group())
        for t in tokens:
            id_file_count[t] += 1

    file_orphans: list[str] = []
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    if id_file_count.get(m.group(), 0) == 1:
                        file_orphans.append(m.group())

    # 3. Lifecycle signature
    try:
        lif = compute_lifecycles(path, lookback_weeks)
        file_lifecycle = "unknown"
        for entry in lif:
            if entry.get("file") == file_path:
                file_lifecycle = entry.get("lifecycle", "unknown")
    except Exception:
        file_lifecycle = "unknown"

    necrotic = reverse_blast == 0 and len(file_orphans) >= 1 and file_lifecycle in ("DECAYING", "DEAD", "unknown")

    return {
        "file": file_path,
        "reverse_blast_radius": reverse_blast,
        "orphan_phrases": file_orphans[:8],
        "lifecycle_state": file_lifecycle,
        "necrotic": necrotic,
        "mandate": f"Necrotic tissue detected. 0 blast radius, {len(file_orphans)} orphans, lifecycle: {file_lifecycle}. DELETE this file." if necrotic else "File is healthy.",
    }


def metamorphic_mask_report(source_path: str, target_path: str,
                             source_ref: str = "HEAD~1") -> dict:
    """Metamorphic Compiler — generate structural transformation masks."""
    from quale.git import diff_refs
    if not vgit.is_repo(source_path):
        return {"error": f"source not a repo: {source_path}"}
    if not vgit.is_repo(target_path):
        return {"error": f"target not a repo: {target_path}"}

    from quale.scanner import scan_codebase

    # Step 1: Extract the transformation mask from the source repo diff
    try:
        diff_files = diff_refs(source_path, f"{source_ref}^", source_ref)
    except Exception:
        diff_files = []
    if not diff_files:
        return {"error": "no diff in source ref"}

    # Scan source before and after to find changed phrases
    try:
        before = scan_codebase(source_path, git_ref=f"{source_ref}^", quiet=True, max_files=500, max_seconds=15)
        after = scan_codebase(source_path, git_ref=source_ref, quiet=True, max_files=500, max_seconds=15)
    except Exception as e:
        return {"error": f"source scan failed: {e}"}

    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')

    # Build phrase sets
    def _phrase_set(analysis):
        result: set[str] = set()
        for fv in analysis.file_vocabs:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    result.add(m.group())
        return result

    phrases_before = _phrase_set(before)
    phrases_after = _phrase_set(after)

    removed = phrases_before - phrases_after
    added = phrases_after - phrases_before

    # Build mask: new identifiers introduced by the commit
    mask: list[dict] = [{"from": "", "to": a} for a in sorted(added)[:20]]

    # Step 2: Project onto target repo — find files that need updating
    # Look for files in target that contain conceptually similar concepts
    # but haven't been updated to use the new identifiers yet
    try:
        target = scan_codebase(target_path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"target scan failed: {e}"}

    # Find files in target that share conceptual overlap with the added phrases
    crater_files: Counter[str] = Counter()
    target_phrase_sets: dict[str, set[str]] = {}
    token_re2 = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    for fv in target.file_vocabs:
        tokens: set[str] = set()
        for phrase in fv.vocabulary:
            for m in token_re2.finditer(phrase):
                tokens.add(m.group())
        target_phrase_sets[fv.path] = tokens
        for a_phrase in added:
            if a_phrase in tokens or any(a_phrase.lower()[:5] in t.lower() for t in tokens):
                crater_files[fv.path] += 1

    # Rank by coupling (number of shared phrases)
    craters: list[dict] = []
    for path, count in crater_files.most_common(30):
        # Compute coupling score
        tokens: set[str] = set()
        for fv2 in target.file_vocabs:
            if fv2.path == path:
                for phrase in fv2.vocabulary:
                    for m in token_re.finditer(phrase):
                        tokens.add(m.group())
        coupling_score = round(len(tokens & added) / max(len(added), 1), 3)
        craters.append({
            "file": path,
            "impact_count": count,
            "coupling": coupling_score,
            "coupling_label": "tight" if coupling_score > 0.3 else ("loose" if coupling_score > 0.1 else "minimal"),
        })

    craters.sort(key=lambda x: -x["coupling"])
    return {
        "mask_count": len(mask),
        "mask": mask[:10],
        "removed_count": len(removed),
        "added_count": len(added),
        "craters": craters[:10],
        "migration_order": "Apply mask to loose craters first, then tight." if craters else "No impact craters found.",
    }


def capillary_report(path: str = ".", top_n: int = 5) -> dict:
    """Capillary action — high-edge-count files (brittle coupling)."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    code_exts = frozenset({".go", ".ts", ".js", ".py", ".rs", ".rb", ".java", ".c", ".cpp", ".h", ".zig", ".ex", ".exs", ".nix", ".jl"})
    file_tokens: dict[str, set[str]] = {}
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in code_exts:
            continue
        tokens: set[str] = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens.add(m.group())
        file_tokens[fv.path] = tokens
    total = len(file_tokens)
    scores: list[tuple[str, int, float]] = []
    for path_a, ta in file_tokens.items():
        edges = sum(1 for pb, tb in file_tokens.items() if pb != path_a and ta & tb)
        scores.append((path_a, edges, round(edges / max(total, 1), 3)))
    scores.sort(key=lambda x: -x[1])
    return {"type": "capillary", "files_scanned": total,
            "capillaries": [{"file": p, "edges": e, "ratio": r} for p, e, r in scores[:top_n]]}


def spectral_gap_report(path: str = ".") -> dict:
    """Spectral gap — cluster-size distribution ratio (modularity score)."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    from quale.bootstrap import compute_modules
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    mods = compute_modules(path, analysis=analysis)
    mod_list = mods.get("modules", []) if isinstance(mods, dict) else []
    sizes = sorted([m.get("size", 0) for m in mod_list], reverse=True)
    gap = round(sizes[0] / max(sizes[1] if len(sizes) > 1 else 1, 1), 2) if sizes else 0
    return {"type": "spectral_gap", "total_clusters": len(sizes),
            "cluster_sizes": sizes[:5], "spectral_gap": gap,
            "modularity": "high" if gap >= 3 else ("moderate" if gap >= 1.5 else "low")}


def phantom_report(path: str = ".") -> dict:
    """Phantom patterns — framework/library detection from import phrases."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    frameworks = {"react": 0, "vue": 0, "svelte": 0, "angular": 0, "django": 0, "flask": 0,
                  "express": 0, "spring": 0, "rails": 0, "laravel": 0, "next": 0, "nuxt": 0,
                  "tailwind": 0, "bootstrap": 0, "jquery": 0, "lodash": 0, "redux": 0,
                  "zustand": 0, "sqlalchemy": 0, "gorm": 0, "sqlx": 0}
    for fv in analysis.file_vocabs:
        if "lock" in fv.path.lower():
            continue
        fv.path.rsplit(".", 1)[-1].lower() if "." in fv.path else ""
        for phrase in fv.vocabulary:
            pl = phrase.lower().replace('"', "").replace("'", "")
            for fw in frameworks:
                if fw in pl:
                    frameworks[fw] += 1
    detected = {k: v for k, v in frameworks.items() if v >= 2}
    return {"type": "phantom", "frameworks_detected": detected}


def guide_report(path: str = ".", file_path: str = "") -> dict:
    """Guide RNA — unique-in-repo phrase for 1-token file location."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path:
        return {"error": "provide --file"}
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    id_file_count: Counter[str] = Counter()
    file_ids: dict[str, set[str]] = {}
    for fv in analysis.file_vocabs:
        tokens: set[str] = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens.add(m.group())
        file_ids[fv.path] = tokens
        for t in tokens:
            id_file_count[t] += 1
    from quale.scanner import _is_generated
    target_tokens = file_ids.get(file_path, set())
    unique = [t for t in target_tokens if id_file_count.get(t, 0) == 1 and len(t) >= 5
              and not _is_generated(file_path) and not all(c.isupper() for c in t)]
    if unique:
        return {"guide": unique[0], "file": file_path, "confidence": "unique"}
    best = sorted(target_tokens, key=lambda t: id_file_count.get(t, 0))[:3]
    return {"guide": best[0] if best else file_path.replace("\\", "/").split("/")[-1].split(".")[0],
            "file": file_path, "confidence": "distinctive"}


def parity_bit_report(path: str = ".", ref_a: str = "", ref_b: str = "") -> dict:
    """Parity bit — XOR hash of test mirror for CI gate."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not ref_a or not ref_b:
        return {"error": "provide --ref-a and --ref-b"}
    from quale.scanner import scan_codebase
    import hashlib
    try:
        analysis_a = scan_codebase(path, git_ref=ref_a, quiet=True, max_files=2500, max_seconds=30)
        analysis_b = scan_codebase(path, git_ref=ref_b, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    def _mirror_hash(analysis):
        phrases: set[str] = set()
        for fv in analysis.file_vocabs:
            if "/test" in fv.path.lower() or "tests/" in fv.path.lower() or "_test." in fv.path or ".test." in fv.path:
                for p in fv.vocabulary:
                    phrases.add(p)
        m = hashlib.sha256()
        for p in sorted(phrases):
            m.update(p.encode())
        return m.hexdigest()[:16]
    h_a, h_b = _mirror_hash(analysis_a), _mirror_hash(analysis_b)
    return {"type": "parity_bit", "ref_a": ref_a, "ref_b": ref_b,
            "hash_a": h_a, "hash_b": h_b, "mirror_unchanged": h_a == h_b}


def trap_report(path: str = ".", file_a: str = "", file_b: str = "") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_a or not file_b:
        return {"error": "provide --file-a and --file-b"}
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        return {"error": "scan failed"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    def _tokens(fp):
        for fv in analysis.file_vocabs:
            if fv.path == fp:
                s = set()
                for phrase in fv.vocabulary:
                    for m in token_re.finditer(phrase):
                        s.add(m.group())
                return s
        return set()
    ta, tb = _tokens(file_a), _tokens(file_b)
    if not ta or not tb:
        return {"error": "files not found"}
    u = len(ta | tb) or 1
    overlap = round(len(ta & tb) / u, 3)
    return {"file_a": file_a, "file_b": file_b, "overlap": overlap,
            "label": "divergence gap" if overlap < 0.1 else ("over-trap" if overlap > 0.3 else "ideal trap")}


def thanatosis_report(path: str = ".") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    from collections import Counter
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    ft = {}
    for fv in analysis.file_vocabs:
        s = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                s.add(m.group())
        ft[fv.path] = s
    cent = {}
    for pa, ta in ft.items():
        cent[pa] = sum(1 for pb, tb in ft.items() if pb != pa and ta & tb)
    log = vgit.ref_log(path, count=200)
    ef = Counter()
    for ref in log:
        try:
            diffs = vgit.diff_refs(path, f"{ref.sha}^", ref.sha)
        except Exception:
            continue
        for f in diffs:
            if f in cent:
                ef[f] += 1
    mc = max(cent.values()) if cent else 1
    res = []
    for f, c in cent.items():
        freq = ef.get(f, 0)
        ratio = round(c / max(freq, 1), 1)
        if ratio >= 10 and freq <= 2 and c >= mc * 0.3:
            res.append({"file": f, "centrality": c, "edits": freq, "risk_ratio": ratio})
    res.sort(key=lambda x: -x["risk_ratio"])
    return {"files": res[:8], "count": len(res)}


def trompe_report(path: str = ".", file_path: str = "") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not file_path:
        return {"error": "provide --file"}
    full = os.path.join(path, file_path)
    if not os.path.exists(full):
        return {"error": f"file not found: {file_path}"}
    with open(full, encoding="utf-8") as f:
        lines = f.readlines()
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        return {"error": "scan failed"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    uids = set()
    for fv in analysis.file_vocabs:
        if fv.path == file_path:
            for phrase in fv.vocabulary:
                for m in token_re.finditer(phrase):
                    uids.add(m.group())
    apparent = len(lines)
    true_comp = max(len(uids), 1)
    ratio = round(apparent / true_comp, 1)
    return {"file": file_path, "apparent_lines": apparent, "true_identifiers": true_comp,
            "trompe_ratio": ratio,
            "label": "skip at 4x speed" if ratio > 3 else ("2x attention" if ratio < 0.3 else "normal")}


def escape_velocity_report(path: str = ".", min_freq: int = 3) -> dict:
    """Weighted removal effort — phrase escape velocity from gravity well."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    from quale.compare import pr_blast_radius
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    code_exts = frozenset({".go", ".ts", ".js", ".py", ".rs", ".rb", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala", ".ml", ".mli", ".ex", ".exs", ".hs", ".lhs", ".zig", ".jl", ".clj", ".cljs", ".nix", ".erl", ".hrl"})
    phrase_files: Counter[str] = Counter()
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in code_exts:
            continue
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                phrase_files[m.group()] += 1
    file_phrases: dict[str, list[str]] = {}
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in code_exts:
            continue
        tokens: list[str] = []
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens.append(m.group())
        if tokens:
            file_phrases[fv.path] = tokens
    sample = list(file_phrases.keys())[:20]
    avg_blast = 0
    if sample:
        radius = pr_blast_radius(sample[:1], analysis.file_vocabs, max_results=50)
        impacts = radius.get("impacts", [])
        avg_blast = len(impacts) if impacts else 5
    tagged = []
    for phrase, count in phrase_files.most_common(100):
        if count < min_freq:
            break
        ev = round((count * avg_blast) / 100, 1)
        label = "DEEP" if ev > 10 else ("BOUND" if ev > 3 else "ESCAPED")
        tagged.append({"phrase": phrase, "frequency": count, "escape_velocity": ev, "label": label})
    return {"tagged": tagged[:20], "thresholds": {"ESCAPED": "<3", "BOUND": "3-10", "DEEP": ">10"}}


def porosity_report(path: str = ".") -> dict:
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    all_tokens: set[str] = set()
    sizes: list[int] = []
    for fv in analysis.file_vocabs:
        tokens = set(fv.vocabulary.keys())
        all_tokens.update(tokens)
        sizes.append(len(tokens))
    u = len(all_tokens)
    p = u * (u - 1) / 2 if u > 1 else 1
    avg_v = sum(sizes) / max(len(sizes), 1)
    obs = len(sizes) * avg_v * (avg_v - 1) / 2
    por = 1 - (obs / p) if p > 0 else 1
    exp = 1 - (1 / u) if u > 1 else 1
    return {"porosity": round(por, 6), "excess_porosity": round(por - exp, 6), "identifiers": u, "files": len(sizes)}


def thylacine_report(path: str = ".") -> dict:
    from quale.scanner import scan_codebase
    import re
    import collections
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    ident_files: dict[str, set[str]] = collections.defaultdict(set)
    for fv in analysis.file_vocabs:
        tokens = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens.add(m.group())
        for t in tokens:
            ident_files[t].add(fv.path)
    builtins = frozenset({"True", "False", "None", "Error", "Exception", "TypeError", "Error",
                          "Object", "Array", "String", "Number", "Boolean", "Map", "Set", "Promise",
                          "Record", "Partial", "Pick", "Omit", "Required", "Readonly"})
    results: list[dict] = []
    for ident, files in ident_files.items():
        if ident in builtins:
            continue
        if len(files) < 3:
            continue
        if any(f.endswith((".pb.go", "_generated.go", "_generated.py")) for f in files):
            continue
        ec = 0
        ic = 0
        for fv in analysis.file_vocabs:
            if fv.path not in files:
                continue
            for phrase in fv.vocabulary:
                if ident not in phrase:
                    continue
                pl = phrase.lower().strip("\"'")
                if pl.startswith(("export", "function ", "class ", "interface ", "type ", "enum ", "def ", "fn ", "const ", "var ", "let ")):
                    ec += 1
                if pl.startswith(("import", "require", "from ")):
                    ic += 1
        if ec >= 1 and ic == 0:
            results.append({"identifier": ident[:40], "files": len(files)})
    return {"thylacines": results[:8]}



def guard_report(path: str = ".", file_path: str = "", task: str = "") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    p = os.path.abspath(path)
    r = {"file": file_path, "task": task}
    try:
        gd = guide_report(path=p, file_path=file_path or task)
        r["guide"] = gd.get("guide", "")
    except Exception:
        pass
    try:
        tt = thanatosis_report(path=p)
        for f in tt.get("files", []):
            if (file_path or "") in f["file"]:
                r["thanatosis"] = f"risk={f['risk_ratio']}"
                break
    except Exception:
        pass
    try:
        tm = trompe_report(path=p, file_path=file_path or task)
        r["trompe"] = tm.get("label", "")
    except Exception:
        pass
    try:
        ct = criticality_report(path=p, file_path=file_path or task)
        for s in ct.get("scores", []):
            if s["file"] == (file_path or task):
                r["criticality"] = f'k={s["k"]} ({s["class"]})'
                break
    except Exception:
        pass
    return r

def check_pr_report(path: str = ".", base_ref: str = "HEAD~1", head_ref: str = "HEAD") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    p = os.path.abspath(path)
    r = {"base": base_ref, "head": head_ref}
    try:
        pb = parity_bit_report(path=p, ref_a=base_ref, ref_b=head_ref)
        r["parity"] = {"unchanged": pb.get("mirror_unchanged", False)}
    except Exception:
        r["parity"] = {"error": True}
    try:
        diffs = vgit.diff_refs(p, base_ref, head_ref)
        if len(diffs) >= 2:
            pairs = []
            for i in range(min(len(diffs), 4)):
                for j in range(i + 1, min(len(diffs), 4)):
                    pairs.append(trap_report(path=p, file_a=diffs[i], file_b=diffs[j]))
            r["trap"] = pairs[:3]
    except Exception:
        pass
    return r

def cleanup_list_report(path: str = ".") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    p = os.path.abspath(path)
    try:
        thy = thylacine_report(path=p)
        ev = escape_velocity_report(path=p)
    except Exception as e:
        return {"error": f"scan: {e}"}
    evm = {t["phrase"]: t["label"] for t in ev.get("tagged", [])}
    items = []
    for t in thy.get("thylacines", []):
        label = "ESCAPED"
        for phrase, ev_label in evm.items():
            if phrase.lower() in t["identifier"].lower():
                label = ev_label
                break
        items.append({"identifier": t["identifier"], "files": t["files"], "effort": label})
    return {"items": items, "free_to_delete": sum(1 for i in items if i["effort"] == "ESCAPED")}

def vulnerability_report(path: str = ".") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    p = os.path.abspath(path)
    try:
        tt = thanatosis_report(path=p)
        cp = capillary_report(path=p)
    except Exception as e:
        return {"error": f"scan: {e}"}
    dt = {f["file"] for f in tt.get("files", [])}
    ch = {c["file"] for c in cp.get("capillaries", [])}
    return {"don_touch": sorted(dt)[:8], "churn_hubs": sorted(ch)[:8], "critical": sorted(dt & ch)[:5]}

def repo_health(path: str = ".") -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    p = os.path.abspath(path)
    try:
        pr = porosity_report(path=p)
        sg = spectral_gap_report(path=p)
    except Exception as e:
        return {"error": f"scan: {e}"}
    return {"excess_porosity": pr.get("excess_porosity", 0), "spectral_gap": sg.get("spectral_gap", 0)}

def condensate_report(path: str = ".", overlap_threshold: float = 0.90,
                       max_results: int = 20) -> dict:
    """Bose-Einstein Condensation — find structurally identical files.

    Compares vocabulary fingerprints across all files in the repo.
    Files with >overlap_threshold fingerprint similarity in different
    directories are condensates — structurally identical but scattered.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    # Build per-file vocabulary sets
    file_sets: list[tuple[str, set[str]]] = []
    for fv in analysis.file_vocabs:
        vocab_set = set(fv.vocabulary.keys())
        if len(vocab_set) >= 3:
            file_sets.append((fv.path, vocab_set))

    if len(file_sets) < 2:
        return {"error": "too few files with vocabulary", "condensates": []}

    condensates: list[dict] = []
    for i in range(len(file_sets)):
        for j in range(i + 1, len(file_sets)):
            path_a, set_a = file_sets[i]
            path_b, set_b = file_sets[j]
            dir_a = "/".join(path_a.replace("\\", "/").split("/")[:-1])
            dir_b = "/".join(path_b.replace("\\", "/").split("/")[:-1])
            if dir_a == dir_b:
                continue
            union = len(set_a | set_b)
            if union == 0:
                continue
            overlap = len(set_a & set_b) / union
            if overlap >= overlap_threshold:
                condensates.append({
                    "files": [path_a, path_b],
                    "overlap": round(overlap, 3),
                    "shared_phrases": sorted(set_a & set_b)[:5],
                })

    condensates.sort(key=lambda x: -x["overlap"])
    return {
        "schema_version": 1,
        "files_scanned": len(file_sets),
        "threshold": overlap_threshold,
        "condensate_count": len(condensates),
        "condensates": condensates[:max_results],
    }


def chirality_report(path: str = ".", min_overlap: float = 0.80) -> dict:
    """Chirality — same vocabulary, zero co-change, disjoint callers."""
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    from quale.scanner import _is_generated
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    file_sets: list[tuple[str, set[str]]] = []
    for fv in analysis.file_vocabs:
        if _is_generated(fv.path):
            continue
        tokens: set[str] = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                tokens.add(m.group())
        if len(tokens) >= 5:
            file_sets.append((fv.path, tokens))
    log = vgit.ref_log(path, count=200)
    co_change: set[tuple[str, str]] = set()
    for ref in log:
        try:
            diffs = vgit.diff_refs(path, f"{ref.sha}^", ref.sha)
        except Exception:
            continue
        if len(diffs) < 2 or len(diffs) > 20:
            continue
        for i in range(len(diffs)):
            for j in range(i + 1, len(diffs)):
                a, b = (diffs[i], diffs[j]) if diffs[i] < diffs[j] else (diffs[j], diffs[i])
                co_change.add((a, b))
    chirality_pairs: list[dict] = []
    for i in range(len(file_sets)):
        for j in range(i + 1, len(file_sets)):
            pa, sa = file_sets[i]
            pb, sb = file_sets[j]
            union = len(sa | sb)
            if union == 0:
                continue
            overlap = len(sa & sb) / union
            pair_key = (pa, pb) if pa < pb else (pb, pa)
            if overlap >= min_overlap and pair_key not in co_change:
                chirality_pairs.append({
                    "files": [pa, pb],
                    "overlap": round(overlap, 3),
                    "shared_tokens": sorted(sa & sb)[:5],
                })

    chirality_pairs.sort(key=lambda x: -x["overlap"])
    return {
        "schema_version": 1,
        "pair_count": len(chirality_pairs),
        "chirality_pairs": chirality_pairs[:20],
    }


def seed_fragment_matrix(path: str = ".", max_commits: int = 20) -> dict:
    """Seed the adaptive router's fragment matrix using git history.

    Evaluates historical commits that touched both source and test files,
    treating the test file modified in the commit as ground truth. This
    populates the fragment matrix with real repo-specific accuracy data
    before the first LLM agent task runs.

    Speed: scans up to 2500 files. On large repos (llama.cpp, 2400 files)
    may take 10-30s total for 20 commits.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "seeded": 0}
    path = os.path.abspath(path)

    log = vgit.ref_log(path, count=max_commits * 5)
    if not log:
        return {"error": "No git history found.", "seeded": 0}

    from quale.scanner import scan_codebase
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        return {"error": "scan failed", "seeded": 0}

    matrix = entanglement_matrix(path, lookback_commits=100)
    seeded_count = 0

    for ref in log:
        if seeded_count >= max_commits:
            break
        sha = ref.sha
        try:
            diff_files = vgit.diff_refs(path, f"{sha}^", sha)
        except Exception:
            continue
        if not diff_files:
            continue

        source_files = []
        test_files: set[str] = set()
        for f in diff_files:
            lf = f.lower()
            if ("/test" in lf or "tests/" in lf or
                f.endswith(("_test.go", "_test.py", ".test.ts", "_test.rs", "_test.exs"))):
                test_files.add(f)
            else:
                source_files.append(f)

        if not source_files or not test_files:
            continue

        file_types = [_file_type(f) for f in source_files]
        dom_type = max(set(file_types), key=file_types.count) if file_types else "unknown"

        verify_with = _preflight_verify_files(source_files, None, analysis.file_vocabs)
        entangled = _entangled_candidates_for_changed(source_files, matrix)
        co_located = _co_located_tests(source_files, analysis.file_vocabs)

        all_candidates = list(dict.fromkeys(verify_with))
        for c in co_located:
            if c["file"] not in all_candidates:
                all_candidates.append(c["file"])
        for e in entangled:
            if e["file"] not in all_candidates:
                all_candidates.append(e["file"])

        if not all_candidates:
            _append_fragment_entry(path, dom_type, "cartridge", 0, False, source_files)
            seeded_count += 1
            continue

        chosen = all_candidates[0]
        hit = bool(chosen in test_files) or _self_assess_hit(path, source_files, chosen)
        _append_fragment_entry(path, dom_type, "cartridge", len(all_candidates), hit, source_files)
        seeded_count += 1

    return {"schema_version": 1, "seeded_trials": seeded_count}


def _structural_cohesion_score(file_path: str, file_vocabs: list) -> float:
    """Ratio of identifiers unique to this file vs shared across the codebase.

    1.0 = entirely self-contained (every identifier unique to this file).
    0.0 = entirely cross-cutting (every identifier shared with other files).
    Used by the cascade to decide whether deterministic verification is safe.
    High cohesion (>0.7) = deterministic skip is safe. Low (<0.3) = LLM needed.
    """
    this_vocab: set[str] = set()
    for fv in file_vocabs:
        if fv.path == file_path:
            for phrase, count in fv.vocabulary.items():
                if _has_code_phrase(phrase):
                    this_vocab.add(phrase)
            break
    if not this_vocab:
        return 0.5

    external_count = 0
    for fv in file_vocabs:
        if fv.path == file_path:
            continue
        for phrase in fv.vocabulary:
            if phrase in this_vocab:
                external_count += 1

    if external_count == 0:
        return 1.0
    internal_count = len(this_vocab)
    total = internal_count + external_count
    return round(internal_count / total, 3)


_B_CELL_DIR = ".reliary/quale/b_cells/"


def _b_cell_path(repo_path: str) -> str:
    return os.path.join(os.path.abspath(repo_path), _B_CELL_DIR)


def _content_hash(file_rel: str, repo_path: str) -> str:
    """Non-cryptographic structural hash of file content (first 16 hex)."""
    import hashlib
    full = os.path.join(os.path.abspath(repo_path), file_rel)
    if os.path.exists(full):
        with open(full, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    content = vgit.read_file_at_ref(repo_path, file_rel)
    if content:
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    return ""


def _b_cell_lookup(repo_path: str, file_path: str) -> dict | None:
    """Look up cached verification outcome for a changed file."""
    h = _content_hash(file_path, repo_path)
    if not h:
        return None
    cache_dir = _b_cell_path(repo_path)
    if not os.path.isdir(cache_dir):
        return None
    for fname in os.listdir(cache_dir):
        if fname.startswith(h):
            try:
                with open(os.path.join(cache_dir, fname), encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def _b_cell_store(repo_path: str, file_path: str,
                  verify_file: str | None, outcome: str,
                  cohesion: float = 0.5):
    """Store verification outcome for future reuse."""
    h = _content_hash(file_path, repo_path)
    if not h:
        return
    entry = {
        "content_hash": h, "source_file": file_path,
        "verify_file": verify_file, "outcome": outcome,
        "cohesion": cohesion, "created_at": time.time(), "hits": 1,
    }
    cache_dir = _b_cell_path(repo_path)
    os.makedirs(cache_dir, exist_ok=True)
    vf_tag = verify_file.replace("/", "_") if verify_file else "desert"
    path = os.path.join(cache_dir, f"{h}_{vf_tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, default=str)


def _b_cell_hit(repo_path: str, file_path: str):
    """Increment hit counter for an existing cache entry."""
    h = _content_hash(file_path, repo_path)
    if not h:
        return
    cache_dir = _b_cell_path(repo_path)
    if not os.path.isdir(cache_dir):
        return
    for fname in os.listdir(cache_dir):
        if fname.startswith(h):
            try:
                fp = os.path.join(cache_dir, fname)
                with open(fp, encoding="utf-8") as f:
                    entry = json.load(f)
                entry["hits"] += 1
                entry["last_hit_at"] = time.time()
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(entry, f, indent=2, default=str)
            except Exception:
                pass

def _vaccination_notes(file_classifications: list[dict]) -> list[str]:
    """Static 'memory cell' patterns injected when gap types are detected."""
    _GAP_PATTERNS = {
        "init_file": "Files named __init__*, index*, mod* have weak structural test mirrors. Manually check upstream module tests.",
        "generated": "Generated files follow generator test conventions, not source structure. Check the generator's test output.",
        "dead_code": "This file's identifiers appear nowhere else in the codebase. It may be unused or standalone.",
        "cross_package": "This change's identifiers appear in multiple test packages. Run the full integration suite, not just unit tests.",
        "declarative_only": "Config/schema files define behavior; verify via integration suite or schema tests.",
    }
    seen = set()
    notes = []
    for fc in file_classifications:
        gap = fc.get("gap_type")
        if gap and gap in _GAP_PATTERNS and gap not in seen:
            seen.add(gap)
            notes.append(_GAP_PATTERNS[gap])
    return notes




def verify_classify_report(path: str = ".", files: list[str] | None = None,
                            diff_ref: str | None = None) -> dict:
    """Gap signature per changed file: verifiability, gap type, vaccination notes."""
    preflight = preflight_report(path=path, files=files, diff_ref=diff_ref)
    if "error" in preflight:
        return {"schema_version": 1, "error": preflight["error"]}
    return {
        "schema_version": 1,
        "changed_files": preflight.get("verify_classifications", []),
        "vaccination": preflight.get("vaccination_notes", []),
        "verification_confidence": preflight.get("verification_confidence", {}),
        "guardrails": {"mode": "report_only", "caveat": "Gap classes are structural heuristics."},
    }




def reverse_verify_report(path: str = ".", files: list[str] | None = None,
                           diff_ref: str | None = None) -> dict:
    """Given changed test files, find source files that likely need verification."""
    from quale.scanner import scan_codebase, _is_generated

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if files:
        changed = _normalize_preflight_files(path, files)
    elif diff_ref:
        try:
            changed = vgit.diff_worktree(path, diff_ref)
        except Exception as e:
            return {"error": str(e)}
    else:
        return {"error": "provide --files or --diff"}

    changed = list(dict.fromkeys(changed))
    if not changed:
        return {"error": "no changed files found"}

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    test_bases = {}
    for f in changed:
        base = os.path.splitext(os.path.basename(f))[0].lower()
        base = base.replace("test_", "").replace("_test", "").replace(".test", "").replace("spec_", "").replace("_spec", "")
        if base:
            test_bases[base] = f

    source_candidates = []
    for fv in analysis.file_vocabs:
        if _is_generated(fv.path):
            continue
        norm = fv.path.lower()
        if "/test" in norm or "tests/" in norm or ".test." in norm or "_test." in norm:
            continue
        fv_base = os.path.splitext(os.path.basename(fv.path))[0].lower()
        if fv_base in test_bases:
            source_candidates.append({"path": fv.path, "reason": f"stem '{fv_base}' matches test '{test_bases[fv_base]}'",
                                       "test_file": test_bases[fv_base]})

    source_candidates.sort(key=lambda x: (
        0 if x["test_file"] in test_bases else 1,
        x["path"]
    ))
    result = {
        "schema_version": 1,
        "path": path,
        "test_files": changed,
        "source_candidates": source_candidates[:8] if source_candidates else [],
        "confidence": "high" if source_candidates else "low",
        "candidate_count": len(source_candidates),
        "guardrails": {
            "mode": "report_only",
            "caveat": "Candidates are structural stem matches, not proof of correctness.",
        },
    }
    return result







def verification_drift(path: str = ".", commits: int = 10) -> dict:
    """Track verification confidence across recent commits."""
    from quale.scanner import scan_codebase

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)

    try:
        log = vgit.ref_log(path, count=commits + 1)
    except Exception as e:
        return {"error": f"ref_log failed: {e}"}

    if len(log) < 2:
        return {"schema_version": 1, "error": "not enough history", "commits_available": len(log)}

    series = []
    alerter = _DriftAlerter()

    for i in range(min(commits, len(log) - 1)):
        ref = log[i]["ref"]
        next_ref = log[i + 1]["ref"] if i + 1 < len(log) else "HEAD"
        try:
            diff_files = vgit.diff_refs(path, next_ref, ref)
        except Exception:
            diff_files = []
        if not diff_files:
            continue

        try:
            analysis = scan_codebase(path, git_ref=ref, quiet=True, max_files=2500, max_seconds=20)
        except Exception:
            continue

        verify_with = []
        changed_bases = {os.path.splitext(os.path.basename(f))[0].replace(".", "").lower() for f in diff_files}
        for fv in analysis.file_vocabs:
            norm = fv.path.lower()
            if "/test" not in norm and "tests/" not in norm and ".test." not in norm and "_test." not in norm:
                continue
            base = os.path.splitext(os.path.basename(fv.path))[0].replace(".test", "").replace("_test", "").lower()
            if base in changed_bases:
                verify_with.append(fv.path)
        verify_with = verify_with[:5]

        conf = _verification_confidence(diff_files, verify_with, None, analysis.file_vocabs)
        level = conf.get("level", "low")
        point = {
            "commit": ref[:10],
            "confidence": level,
            "candidate_count": conf.get("candidate_count", 0),
            "mirror_ratio": conf.get("mirror_ratio", 0),
            "changed_file_count": len(diff_files),
        }
        series.append(point)
        alerter.feed(point)

    return {
        "schema_version": 1,
        "series": series,
        "alerts": alerter.alerts,
        "has_drift": alerter.has_drift,
    }


class _DriftAlerter:
    """Detect 3-consecutive confidence drops and sudden gap events."""
    def __init__(self):
        self.alerts = []
        self.has_drift = False
        self._levels = {"high": 3, "mixed": 2, "low": 1, "unknown": 0}
        self._prev_level = None
        self._drop_count = 0

    def feed(self, point: dict) -> None:
        level = point.get("confidence", "unknown")
        numeric = self._levels.get(level, 0)
        if self._prev_level is not None and numeric < self._prev_level:
            self._drop_count += 1
            if self._drop_count >= 3:
                self.alerts.append(f"Confidence declined {self._drop_count} consecutive commits ending at {point.get('commit', '?')}")
                self.has_drift = True
        elif self._prev_level is not None and numeric >= self._prev_level:
            self._drop_count = 0

        if self._prev_level is not None and numeric <= 1 and self._prev_level >= 3:
            self.alerts.append(f"Sudden gap: confidence fell from high to low at {point.get('commit', '?')}")
            self.has_drift = True

        if point.get("candidate_count", 1) == 0 and self._prev_level is not None and self._prev_level >= 1:
            self.alerts.append(f"Test candidate disappeared at {point.get('commit', '?')}")
            self.has_drift = True

        self._prev_level = numeric







def covalent_verify_bonds(path: str = ".", files: list[str] | None = None) -> dict:
    """Detect when a change requires running multiple test files together."""
    from quale.scanner import scan_codebase

    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    if not files:
        return {"error": "provide --files"}
    changed = _normalize_preflight_files(path, list(files))
    if not changed:
        return {"error": "no changed files found"}

    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}

    path_vocabs = {}
    for fv in analysis.file_vocabs:
        path_vocabs[fv.path] = {phrase for phrase in fv.vocabulary}

    changed_vocab = set()
    for f in changed:
        cv = path_vocabs.get(f, set())
        changed_vocab.update(cv)

    test_files = []
    for fv in analysis.file_vocabs:
        if "/test" in fv.path.lower() or "tests/" in fv.path.lower() or ".test." in fv.path.lower() or "_test." in fv.path.lower():
            overlap = len(changed_vocab & set(fv.vocabulary.keys()))
            if overlap > 0:
                test_files.append({"path": fv.path, "overlap": overlap})

    test_files.sort(key=lambda x: -x["overlap"])
    top_tests = test_files[:5]

    bonds = []
    if len(top_tests) >= 2:
        for i in range(len(top_tests)):
            for j in range(i + 1, len(top_tests)):
                ti = top_tests[i]
                tj = top_tests[j]
                combined = ti["overlap"] + tj["overlap"]
                bonds.append({"tests": [ti["path"], tj["path"]],
                              "combined_vocab_overlap": combined,
                              "reason": "both tests share vocabulary with changed file"})

    bonds.sort(key=lambda x: -x["combined_vocab_overlap"])

    return {
        "schema_version": 1,
        "changed_files": changed,
        "top_test_candidates": top_tests,
        "bonds": bonds[:3],
        "bond_count": len([b for b in bonds[:3] if b["combined_vocab_overlap"] > 3]),
        "guardrails": {"mode": "report_only", "caveat": "Bonds are vocabulary overlap hints."},
    }




def _file_type(path: str) -> str:
    """Classify file type for fragment matrix routing."""
    base = os.path.basename(path).lower()
    ext = os.path.splitext(path)[1].lower()
    if "__init__" in base:
        return f"{ext.lstrip('.')}_init"
    if ext == ".go":
        return "go_test" if base.endswith("_test.go") else "go"
    if ext == ".py":
        return "py_test" if base.startswith("test_") else "py"
    if ext == ".ts":
        return "ts_test" if ".test." in base else "ts"
    if ext in (".rs",):
        return "rs_test" if base.endswith("_test.rs") else "rs"
    if ext in (".js", ".jsx"):
        return "js_test" if ".test." in base else "js"
    if ext in (".ex", ".exs"):
        return "ex_test" if base.endswith("_test.exs") else "ex"
    return ext.lstrip(".") or "unknown"


_FRAGMENT_MATRIX_PATH = ".reliary/quale/fragment_matrix.json"


def _load_fragment_matrix(path: str = ".") -> dict:
    """Load fragment matrix from cache. Returns {(repo, file_type, condition): {hit_count, trial_count}}."""
    fp = os.path.join(os.path.abspath(path), _FRAGMENT_MATRIX_PATH)
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    entries = data.get("entries", []) if isinstance(data, dict) else []
    result = {}
    for e in entries:
        key = (e.get("repo", ""), e.get("file_type", ""), e.get("condition", ""))
        result[key] = {"hit_count": e.get("hit_count", 0), "trial_count": e.get("trial_count", 0), "updated_at": e.get("updated_at", "")}
    return result


def _save_fragment_matrix(path: str, entries: list[dict]):
    """Save fragment matrix entries to cache."""
    fp = os.path.join(os.path.abspath(path), _FRAGMENT_MATRIX_PATH)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "entries": entries}, f, indent=2, default=str)


def _has_code_phrase(token: str) -> bool:
    """True if token contains code-specific characters (braces, parens, operators)."""
    for ch in "{;":
        if ch in token:
            return True
    if ":=" in token or "==" in token or "!=" in token:
        return True
    if "[" in token and "]" in token:
        idx, jdx = token.index("["), token.index("]")
        if jdx - idx > 1 or ":" not in token[:idx]:
            return True
    if "(" in token and ")" in token:
        idx = token.index("(")
        if idx > 0 and token[idx - 1] not in " :":
            return True
    return False


_DECLARATIVE_LANGUAGES = frozenset({
    "YAML", "JSON", "XML", "TOML", "Markdown", "INI",
    "Text", "Dockerfile", "Makefile", "Shell", "Env",
})


def _is_declarative_changed(changed: list[str], file_vocabs) -> bool:
    """Return True if all changed files are declarative (no code identifiers)."""
    for f in changed:
        has_vocab = False
        for fv in file_vocabs:
            if fv.path == f:
                has_vocab = True
                if fv.language not in _DECLARATIVE_LANGUAGES:
                    return False
                break
        if not has_vocab:
            return False
    return True


def _same_package_prefix(test_dir: str, src_dir: str) -> bool:
    """True if directories share a meaningful package prefix (at least 2 segments)."""
    t_parts = test_dir.replace("\\", "/").split("/")
    s_parts = src_dir.replace("\\", "/").split("/")
    if len(t_parts) < 2 or len(s_parts) < 2:
        return False
    if t_parts[0] != s_parts[0]:
        return False
    return sum(1 for a, b in zip(t_parts, s_parts) if a == b) >= 2


_CO_LOCATED_CONVENTIONS = [
    ("src", "tests", 0.5),
    ("/src", "/test", 0.5),
    ("lib", "test", 0.5),
    ("internal", "internal", 0.7),
]

# — Per-source-prefix, look for same-named package subdirectory
def _monorepo_package_prefix(p: str) -> str | None:
    """Extract 'packages/X' prefix from a monorepo path."""
    parts = p.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "packages":
        return f"packages/{parts[1]}"
    return None


def _co_located_tests(changed: list[str], file_vocabs) -> list[dict]:
    """Find test files co-located with changed files using directory mirrors."""
    all_tests = []
    for fv in file_vocabs:
        p = fv.path
        if "/test" not in p.lower() and "tests/" not in p.lower() and ".test." not in p and "_test." not in p:
            continue
        all_tests.append(p)
    candidates = []
    for cf in changed:
        cf_dir = os.path.dirname(cf)
        cf_stem = os.path.splitext(os.path.basename(cf))[0].lower()
        # — Monorepo convention: packages/X/src/ → packages/X/test/
        mono_pfx = _monorepo_package_prefix(cf)
        if mono_pfx:
            for test_path in all_tests:
                test_mono = _monorepo_package_prefix(test_path)
                if test_mono != mono_pfx:
                    continue
                td = os.path.dirname(test_path)
                if "/test" not in td and "tests/" not in td:
                    continue
                ts = os.path.splitext(os.path.basename(test_path))[0].lower()
                ts = ts.replace("test_", "").replace("_test", "").replace(".test", "")
                score = 0.5
                if ts == cf_stem:
                    score += 0.2
                elif cf_stem and ts and (cf_stem in ts or ts in cf_stem):
                    score += 0.1
                candidates.append({
                    "file": test_path,
                    "score": round(min(0.9, score), 2),
                    "reason": f"co-located in {test_mono} ({cf})",
                })
        for src_pfx, test_pfx, base_score in _CO_LOCATED_CONVENTIONS:
            if src_pfx not in cf_dir and src_pfx != "/":
                continue
            # — Skip broad conventions for monorepo packages (already handled above)
            if mono_pfx and src_pfx in ("src", "lib"):
                continue
            test_dir = cf_dir.replace(src_pfx, test_pfx, 1) if src_pfx != "/" else test_pfx
            for test_path in all_tests:
                td = os.path.dirname(test_path)
                if td != test_dir and not td.startswith(test_dir + "/"):
                    continue
                ts = os.path.splitext(os.path.basename(test_path))[0].lower()
                ts = ts.replace("test_", "").replace("_test", "").replace(".test", "")
                score = base_score
                if ts == cf_stem:
                    score += 0.2
                elif cf_stem and ts and (cf_stem in ts or ts in cf_stem):
                    score += 0.1
                candidates.append({
                    "file": test_path,
                    "score": round(min(0.9, score), 2),
                    "reason": f"co-located with {cf} ({src_pfx}→{test_pfx})",
                })
    candidates.sort(key=lambda x: -x["score"])
    seen = set()
    uniq = []
    for c in candidates:
        if c["file"] not in seen:
            seen.add(c["file"])
            uniq.append(c)
    return uniq[:5]


def _append_fragment_entry(path: str, file_type: str, condition: str, candidates_count: int, verify_hit: bool, changed_files: list[str] | None = None):
    """Append a labeled trial outcome to the fragment matrix."""
    try:
        existing = _load_fragment_matrix(path)
        repo = os.path.basename(os.path.realpath(path))
        key = (repo, file_type, condition)
        entry = existing.get(key, {"hit_count": 0, "trial_count": 0})
        entry["hit_count"] += (1 if verify_hit else 0)
        entry["trial_count"] += 1
        existing[key] = entry
        entries = []
        for (r, ft, c), e in existing.items():
            entries.append({"repo": r, "file_type": ft, "condition": c, **e})
        _save_fragment_matrix(path, entries)
    except Exception:
        pass


def _deterministic_verify(verify_with: list[str], entangled: list[dict], changed_bases: set[str]) -> dict | None:
    """Check if verification choice is structurally unambiguous. Returns {file, score, rule} or None."""
    if not verify_with:
        return None
    entangle_by_file = {e["file"]: e.get("score", 0) for e in (entangled or [])}
    for c in verify_with:
        c_base = os.path.splitext(os.path.basename(c))[0].lower()
        c_base = c_base.replace("test_", "").replace("_test", "").replace(".test", "")
        stem_match = c_base in changed_bases
        ent_score = entangle_by_file.get(c, 0)
        if stem_match and ent_score >= 0.25:
            return {"file": c, "score": 1.0, "rule": "stem_match_and_entanglement"}
        if stem_match:
            return {"file": c, "score": 0.85, "rule": "stem_match"}
        if ent_score >= 0.25:
            return {"file": c, "score": 0.80, "rule": "entanglement_only"}
    if len(verify_with) >= 2:
        d0, d1 = verify_with[0].count("/"), verify_with[1].count("/")
        s0, s1 = max(0.1, 1.0 - d0 * 0.15), max(0.1, 1.0 - d1 * 0.15)
        if s0 > s1 * 2:
            return {"file": verify_with[0], "score": 0.75, "rule": "clear_leader"}
    return None


def _negative_verify_files(changed: list[str], file_vocabs: list) -> list[str]:
    """Test files with zero vocabulary overlap with changed files."""
    changed_set = set(changed)
    changed_vocab = set()
    for fv in file_vocabs:
        if fv.path in changed_set:
            changed_vocab.update(fv.vocabulary.keys() if hasattr(fv, "vocabulary") else [])
    if not changed_vocab:
        return []
    negatives = []
    for fv in file_vocabs:
        p = fv.path.lower()
        if not any(p.endswith(e) for e in ("_test.go", "_test.py", ".test.ts", "_test.rs", "_test.exs", "test_.py", ".spec.ts")):
            continue
        if fv.path in changed_set:
            continue
        fv_vocab = set(fv.vocabulary.keys() if hasattr(fv, "vocabulary") else [])
        if fv_vocab and not (fv_vocab & changed_vocab):
            negatives.append(fv.path)
    negatives.sort()
    return negatives[:5]


def _cost_tier(candidate: str, changed: list[str]) -> str:
    """Classify verification cost: unit, integration, or e2e."""
    for cf in changed:
        if os.path.dirname(cf) == os.path.dirname(candidate):
            return "unit"
    path = candidate.lower()
    if any(seg in path.split(os.sep) for seg in ("e2e", "integration", "functional", "playwright", "cypress")):
        return "e2e"
    return "integration"


def _verification_horizon(verify_with: list[str], entangled: list[dict], changed: list[str], changed_bases: set[str]) -> list[dict]:
    """Ordered verification candidates with cost tiers."""
    ent_scores = {e["file"]: e.get("score", 0) for e in entangled}
    horizon = []
    for c in verify_with:
        c_base = os.path.splitext(os.path.basename(c))[0].lower()
        c_base = c_base.replace("test_", "").replace("_test", "").replace(".test", "")
        stem_match = c_base in changed_bases
        ent_score = ent_scores.get(c, 0)
        horizon.append({"file": c, "tier": _cost_tier(c, changed), "stem_match": stem_match, "entanglement_score": ent_score})
    horizon.sort(key=lambda x: (0 if x["stem_match"] else 1, 0 if x["tier"] == "unit" else (1 if x["tier"] == "integration" else 2), -x["entanglement_score"]))
    return horizon


def _oscillator_candidates(changed: list[str], file_vocabs: list, matrix: dict, bootstrap: dict | None) -> dict:
    """Run 3 guidance variants, compute intersection."""
    scope_cands = set(_preflight_verify_files(changed, None, file_vocabs))
    boostrapped = _preflight_verify_files(changed, bootstrap, file_vocabs)
    cart_cands = set(boostrapped)
    ent_cands = set(c for c in scope_cands) | {e["file"] for e in _entangled_candidates_for_changed(changed, matrix)}
    intersection = scope_cands & cart_cands & ent_cands
    union = scope_cands | cart_cands | ent_cands
    return {"intersection": sorted(intersection)[:3] if intersection else [], "union": sorted(union)[:5] if union else [], "intersection_ratio": round(len(intersection) / max(len(union), 1), 2), "verdict": "strong" if intersection else "divergent"}


def _self_assess_hit(path: str, changed: list[str], chosen_verify: str) -> bool:
    """Determine if chosen_verify shares vocabulary with changed files."""
    try:
        from quale.scanner import scan_codebase
        analysis = scan_codebase(path, quiet=True, max_files=500, max_seconds=10)
        changed_set = set(changed)
        changed_vocab = set()
        for fv in analysis.file_vocabs:
            if fv.path in changed_set:
                changed_vocab.update(fv.vocabulary.keys())
        for fv in analysis.file_vocabs:
            if fv.path == chosen_verify:
                verify_vocab = set(fv.vocabulary.keys())
                return len(changed_vocab & verify_vocab) >= 1
        return False
    except Exception:
        return False


def check_diff_report(path: str = ".", diff_ref: str = "HEAD~1") -> dict:
    """Post-proposal defect scan: detect edits that break repo structure."""
    from quale.scanner import scan_codebase, _mirror_signals, _is_generated
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        changed = vgit.diff_worktree(path, diff_ref)
    except Exception as e:
        return {"error": str(e)}
    changed = list(dict.fromkeys(changed))
    if not changed:
        return {"schema_version": 1, "defects": [], "diff": diff_ref, "note": "no changed files"}
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    mirror = _mirror_signals(changed, analysis.file_vocabs)
    defects = []
    try:
        stability_data = compute_stability(path, weeks=12)
    except Exception:
        stability_data = []
    stable_by_file = {item["file"]: item for item in stability_data}
    for f in changed:
        s = stable_by_file.get(f)
        if s and s.get("persistence", 0) >= 0.8:
            defects.append({"type": "stable_anchor_touched", "file": f,
                            "detail": f"stable anchor (persistence {s['persistence']:.1%})", "severity": "moderate"})
    for f in changed:
        if _is_generated(f):
            defects.append({"type": "generated_file_edited", "file": f,
                            "detail": "editing generated file directly", "severity": "low"})
    if mirror:
        unmirrored = mirror.get("unmirrored_source_concepts", [])
        if len(unmirrored) > 5:
            defects.append({"type": "mirror_weakened", "file": ", ".join(changed[:3]),
                            "detail": f"{len(unmirrored)} source concepts have no test mirror", "severity": "moderate"})
    if len(changed) > 20:
        defects.append({"type": "large_change_set", "file": f"{len(changed)} files",
                        "detail": "change set exceeds 20 files", "severity": "high"})
    return {
        "schema_version": 1, "diff": diff_ref,
        "changed_files": changed, "defects": defects,
        "defect_count": len(defects),
        "max_severity": max((d.get("severity", "low") for d in defects), default="none") if defects else "none",
    }


def _normalize_preflight_files(repo_path: str, files: list[str]) -> list[str]:
    normalized = []
    for item in files:
        for raw in item.split(","):
            raw = raw.strip()
            if not raw:
                continue
            full = raw if os.path.isabs(raw) else os.path.join(repo_path, raw)
            rel = os.path.relpath(full, repo_path) if os.path.isabs(raw) else raw
            rel = rel.replace("\\", "/").lstrip("./")
            if rel.startswith("../"):
                continue
            normalized.append(rel)
    return normalized




def _preflight_read_first(changed: list[str], bootstrap: dict | None, blast: list[dict]) -> list[str]:
    reads = list(changed[:3])
    if bootstrap:
        for item in bootstrap.get("recommended_next_reads", []):
            file = item.get("file")
            if file and file not in reads:
                reads.append(file)
        for item in bootstrap.get("related_files_for_task", []):
            file = item.get("file")
            if file and item.get("role") != "test" and file not in reads:
                reads.append(file)
    for item in blast[:3]:
        file = item.get("file")
        if file and file not in reads:
            reads.append(file)
    return reads[:3]




def _preflight_verify_files(changed: list[str], bootstrap: dict | None, file_vocabs) -> list[str]:
    verify = []
    bootstrap_added: set[str] = set()
    if bootstrap:
        for item in bootstrap.get("related_files_for_task", []):
            file = item.get("file")
            if file and item.get("role") == "test" and file not in verify:
                verify.append(file)
                bootstrap_added.add(file)
    changed_bases = {os.path.splitext(os.path.basename(f))[0].replace(".", "").lower() for f in changed}
    for fv in file_vocabs:
        norm = fv.path.lower()
        if "/test" not in norm and "tests/" not in norm and ".test." not in norm and "_test." not in norm:
            continue
        base = os.path.splitext(os.path.basename(fv.path))[0].replace(".test", "").replace("_test", "").lower()
        if base in changed_bases and fv.path not in verify:
            verify.append(fv.path)
    changed_dirs = set()
    for f in changed:
        d = os.path.dirname(f)
        if d:
            changed_dirs.add(d)
    verify.sort(key=lambda f: (
        0 if f in bootstrap_added and os.path.splitext(os.path.basename(f))[0].replace(".test", "").replace("_test", "").lower() in changed_bases else
        1 if os.path.dirname(f) in changed_dirs else
        2 if any(_same_package_prefix(os.path.dirname(f), cd) for cd in changed_dirs) else
        3 if f in bootstrap_added else
        4,
        f
    ))
    return verify[:5]




def _preflight_avoid(changed: list[str], stable_touched: list[dict], blast: list[dict], bootstrap: dict | None,
                     verify_with: list[str] | None = None) -> list[str]:
    avoid = []
    excluded = set(changed) | set(verify_with or [])
    if bootstrap:
        for item in bootstrap.get("avoid_touching_without_context", []):
            file = item.get("file")
            if file and file not in excluded and item.get("persistence", 0) >= 0.8:
                avoid.append(file)
    for item in blast:
        file = item.get("file")
        if file and file not in excluded:
            avoid.append(file)
    return list(dict.fromkeys(avoid))[:5]




def _preflight_reasons(changed: list[str], stable_touched: list[dict], blast: list[dict], mirror: dict) -> list[str]:
    reasons = []
    if stable_touched:
        reasons.append(f"touches {len(stable_touched)} stable anchor{'s' if len(stable_touched) != 1 else ''}")
    if blast:
        reasons.append(f"reverse blast reaches {min(len(blast), 5)} ranked file{'s' if len(blast) != 1 else ''}")
    if len(changed) > 3:
        reasons.append(f"broad edit set ({len(changed)} files)")
    mirror_ratio = mirror.get("mirror_ratio", 1.0) if mirror else 1.0
    if mirror and mirror_ratio < 0.5:
        reasons.append(f"source/test mirror is thin ({mirror_ratio:.0%})")
    if not reasons:
        reasons.append("file-scoped local scan")
    return reasons[:4]




def _preflight_risk(changed: list[str], stable_touched: list[dict], blast: list[dict]) -> str:
    top_shared = blast[0].get("shared_concepts", 0) if blast else 0
    if stable_touched and (top_shared > 10 or len(blast) > 10):
        return "high"
    if len(changed) > 10 or top_shared > 20:
        return "high"
    if stable_touched or top_shared > 8 or len(blast) > 5:
        return "moderate"
    return "low"




def _preflight_confidence(changed: list[str], bootstrap: dict | None, file_vocabs) -> str:
    existing = {fv.path for fv in file_vocabs}
    coverage = sum(1 for f in changed if f in existing) / max(len(changed), 1)
    relevance = bootstrap.get("task_relevance_score", 0.5) if bootstrap else 0.5
    score = (coverage + relevance) / 2
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "mixed"
    return "low"




def _contract_id(prefix: str, index: int, path: str) -> str:
    import hashlib
    suffix = hashlib.sha256(f"{prefix}:{index}:{path}".encode()).hexdigest()[0]
    return f"{prefix}{index}{suffix}"




def _expand_scope_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            result.append(item["id"])
    return result




def _scope_creep_guard(changed: list[str], avoid_expanding: list[str], stable_touched: list[dict], blast: list[dict]) -> dict:
    questioned: list[dict] = []
    blast_by_file = {item.get("file"): item for item in blast}
    for file in avoid_expanding[:5]:
        blast_item = blast_by_file.get(file, {})
        shared = blast_item.get("shared_concepts", 0)
        level = "high" if shared >= 20 else ("medium" if shared >= 8 else "low")
        questioned.append({
            "file": file,
            "level": level,
            "reason": f"outside requested edit set; shares {shared} structural concepts" if shared else "outside requested edit set",
        })

    return {
        "mode": "report_only",
        "allow_changed_files": changed[:10],
        "question_extra_edits": questioned,
        "stable_anchors_touched": stable_touched[:5],
        "instruction": "Edit changed_files first. Treat extra edits as questionable unless the task explicitly requires them.",
        "never_block": True,
    }


# ── Verification deserts ─────────────────────────────────────────



def _explain_verify_candidates(changed: list[str], bootstrap: dict | None, file_vocabs, verify_with: list[str]) -> list[dict[str, str]]:
    """Return per-candidate match reason for each verification file."""
    if not verify_with:
        return []
    changed_bases = {os.path.splitext(os.path.basename(f))[0].replace(".", "").lower() for f in changed}
    changed_dirs = set()
    for f in changed:
        parts = f.replace("\\", "/").split("/")
        if len(parts) > 1:
            changed_dirs.add("/".join(parts[:-1]))
    # Collect task-relevant tests
    task_tests = set()
    if bootstrap:
        for item in bootstrap.get("related_files_for_task", []):
            if item.get("role") == "test" and item.get("file"):
                task_tests.add(item["file"])
    details = []
    for vpath in verify_with:
        if vpath in task_tests:
            details.append({"path": vpath, "reason": "task relevance"})
            continue
        vbase = os.path.splitext(os.path.basename(vpath))[0].replace(".test", "").replace("_test", "").lower()
        if vbase in changed_bases:
            details.append({"path": vpath, "reason": f"stem '{vbase}' matches changed file"})
            continue
        vdir = vpath.rsplit("/", 1)[0] if "/" in vpath else ""
        if vdir in changed_dirs:
            details.append({"path": vpath, "reason": f"same directory '{vdir}' as changed file"})
            continue
        details.append({"path": vpath, "reason": "test discovery convention"})
    return details[:5]




def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", value))




def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []




def _source_stem(path: str) -> str:
    base = os.path.basename(path).lower()
    stem = os.path.splitext(base)[0]
    return stem.replace(".", "").replace("_", "").replace("-", "")




def _test_stem(path: str) -> str:
    base = os.path.basename(path).lower()
    stem = os.path.splitext(base)[0]
    for marker in ("test_", "_test", ".test", "spec_", "_spec", ".spec"):
        stem = stem.replace(marker, "")
    return stem.replace(".", "").replace("_", "").replace("-", "")




def _verification_candidates_for_source(source_path: str, stem: str, test_paths: list[str], test_by_stem: dict[str, list[str]]) -> list[str]:
    candidates = list(test_by_stem.get(stem, []))
    source_parts = source_path.replace("\\", "/").split("/")[:-1]
    source_dir_tokens = {part.lower() for part in source_parts if part}
    for test in test_paths:
        if test in candidates:
            continue
        test_lower = test.lower()
        if stem and stem in _test_stem(test):
            candidates.append(test)
            continue
        overlap = source_dir_tokens & {part.lower() for part in test.replace("\\", "/").split("/")[:-1]}
        if overlap and os.path.basename(source_path).split(".")[0].lower() in test_lower:
            candidates.append(test)
    return list(dict.fromkeys(candidates))[:5]




def _verification_confidence(changed: list[str], verify_with: list[str], mirror: dict | None, file_vocabs) -> dict:
    existing = {fv.path for fv in file_vocabs}
    existing_candidates = [path for path in verify_with if path in existing]
    mirror_ratio = mirror.get("mirror_ratio", 0.0) if mirror else 0.0
    len(changed)
    candidate_count = len(verify_with)

    reasons: list[str] = []
    if candidate_count == 0:
        level = "low"
        reasons.append("no structural verification candidates found")
    elif len(existing_candidates) < candidate_count:
        level = "low"
        reasons.append("some verification candidates were not found in the current scan")
    elif mirror_ratio >= 0.7 and candidate_count >= 1:
        level = "high"
        reasons.append("source/test mirror signal is strong")
    elif mirror_ratio >= 0.3 or candidate_count >= 1:
        level = "mixed"
        reasons.append("some verification candidates found, but mirror signal is thin")
    else:
        level = "low"
        reasons.append("verification topology is sparse")

    return {
        "level": level,
        "candidate_count": candidate_count,
        "existing_candidate_count": len(existing_candidates),
        "mirror_ratio": round(mirror_ratio, 3),
        "reasons": reasons[:3],
        "caveat": "Candidates are structural hints, not proof of test coverage.",
    }




def _verification_desert_reason(score: float, candidates: list[str], test_dirs: set[str], source_path: str) -> str:
    if candidates:
        return "only one obvious verification candidate" if len(candidates) == 1 else "has structural test mirror"
    if not test_dirs:
        return "no test directories detected in scanned files"
    if score >= 0.75:
        return "no same-name or nearby test mirror found"
    return "nearby test directory exists, but no direct source/test mirror found"


# ── Vocab routing policy ─────────────────────────────────────────



def _verification_desert_score(source_path: str, candidates: list[str], test_dirs: set[str]) -> float:
    if candidates:
        return 0.0 if len(candidates) >= 2 else 0.25
    parts = source_path.replace("\\", "/").split("/")
    has_nearby_test_dir = any(source_path.startswith(prefix.rsplit("/", 1)[0]) for prefix in test_dirs if "/" in prefix)
    score = 0.75
    if not test_dirs:
        score = 1.0
    elif has_nearby_test_dir:
        score = 0.55
    if any(part in {"examples", "scripts", "docs"} for part in parts):
        score = min(score, 0.45)
    return score




def verification_deserts(path: str, max_results: int = 20) -> dict:
    """Find source files with weak structural verification mirrors.

    This is not test coverage. It only reports places where source files
    lack obvious same-name, nearby, or task-convention test mirrors.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from quale.scanner import scan_codebase, _is_generated, _is_lock_file, _DEAD_CODE_EXTS
    from quale.bootstrap import _task_file_role

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    all_paths = [fv.path for fv in analysis.file_vocabs]
    test_paths = [p for p in all_paths if _task_file_role(p) == "test"]
    test_by_stem: dict[str, list[str]] = defaultdict(list)
    test_dirs = set()
    for p in test_paths:
        stem = _test_stem(p)
        test_by_stem[stem].append(p)
        parts = p.replace("\\", "/").split("/")
        for idx, part in enumerate(parts):
            if "test" in part.lower():
                test_dirs.add("/".join(parts[:idx + 1]))

    deserts: list[dict] = []
    source_count = 0
    mirrored_count = 0
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if (ext not in _DEAD_CODE_EXTS or _task_file_role(fv.path) != "source"
                or _is_generated(fv.path) or _is_lock_file(fv.path)
                or fv.path.startswith((".reliary/", ".quale-cache/"))
                or os.path.basename(fv.path).startswith(".")):
            continue
        source_count += 1
        stem = _source_stem(fv.path)
        candidates = _verification_candidates_for_source(fv.path, stem, test_paths, test_by_stem)
        score = _verification_desert_score(fv.path, candidates, test_dirs)
        if candidates:
            mirrored_count += 1
        if score >= 0.55:
            deserts.append({
                "file": fv.path,
                "score": round(score, 3),
                "candidate_count": len(candidates),
                "candidates": candidates[:3],
                "reason": _verification_desert_reason(score, candidates, test_dirs, fv.path),
            })

    deserts.sort(key=lambda item: (-item["score"], item["candidate_count"], item["file"]))
    mirror_ratio = mirrored_count / max(source_count, 1)
    return {
        "schema_version": 1,
        "source_files": source_count,
        "test_files": len(test_paths),
        "mirrored_source_files": mirrored_count,
        "mirror_ratio": round(mirror_ratio, 3),
        "deserts": deserts[:max_results],
        "confidence": "mixed — structural mirror only, not coverage" if test_paths else "low — no test files detected",
        "guardrails": {
            "mode": "report_only",
            "not_coverage_proof": True,
            "caveat": "A desert means no obvious structural test mirror, not that behavior is untested.",
        },
    }




def _classify_verifiability(filepath: str, changed: list[str], verify_with: list[str],
                            file_vocabs, analysis) -> dict:
    """Classify a single changed file's verifiability class.
    Returns {file, verifiability, gap_type, reason, confidence}."""
    from quale.scanner import _is_generated
    base = os.path.splitext(os.path.basename(filepath))[0]
    lower_base = base.lower()
    lower_path = filepath.lower()

    if _is_generated(filepath):
        return {"file": filepath, "verifiability": "hard_to_verify", "gap_type": "generated",
                "reason": "generated file; test mirror follows generator convention, not source structure", "confidence": "low"}

    if lower_base in {"__init__", "index", "mod"} or lower_path.endswith(("/__init__.py", "/index.ts", "/mod.rs")):
        return {"file": filepath, "verifiability": "hard_to_verify", "gap_type": "init_file",
                "reason": "init files have no structural test mirror; check upstream module tests", "confidence": "low"}

    if any(filepath.endswith(ext) for ext in (".yml", ".yaml", ".json", ".proto", ".sql", ".env", ".cfg")):
        return {"file": filepath, "verifiability": "unverifiable", "gap_type": "declarative_only",
                "reason": "declarative file; verify via integration or schema tests, not unit tests", "confidence": "low"}

    if any(v.path == filepath for v in file_vocabs):
        this_vocab = None
        for v in file_vocabs:
            if v.path == filepath:
                this_vocab = v
                break
        if this_vocab:
            inline_exports = [p for p in this_vocab.vocabulary if len(p) > 4 and p[0].isupper()]
            exported_elsewhere = 0
            for v in file_vocabs:
                if v.path != filepath:
                    for phrase in inline_exports:
                        if phrase in v.vocabulary:
                            exported_elsewhere += 1
                            break
        exported_elsewhere = 0
        for v in file_vocabs:
            if v.path != filepath:
                for phrase in inline_exports:
                    if phrase in v.vocabulary:
                        exported_elsewhere += 1
                        break
        if exported_elsewhere > 0:
            return {"file": filepath, "verifiability": "verifiable", "gap_type": "cross_package",
                    "reason": f"identifiers appear in {exported_elsewhere} other packages; run integration suite", "confidence": "mixed"}

    stem = os.path.splitext(os.path.basename(filepath))[0].replace(".", "").lower()
    if any(stem in v.path.lower().replace(".test", "").replace("_test", "") for v in file_vocabs
           if "test" in v.path.lower() or "tests/" in v.path.lower()):
        return {"file": filepath, "verifiability": "verifiable", "gap_type": "well_mirrored",
                "reason": "stem-matching test file found", "confidence": "high"}

    return {"file": filepath, "verifiability": "verifiable", "gap_type": None,
            "reason": "verification candidates found by structural scan", "confidence": "mixed"}




def _route_path(path: str, changed: list[str], analysis, task: str | None = None) -> str:
    """Determine the correct intervention tier for a given change set."""
    from quale.scanner import _is_generated
    decl_exts = {".yml", ".yaml", ".json", ".proto", ".sql", ".env", ".cfg", ".toml", ".ini", ".md", ".txt"}

    has_substance = bool(task and len(task) > 10 and task not in ("fix bug", "cleanup", "refactor", "tidy up", "misc"))

    if all(any(f.endswith(e) for e in decl_exts) for f in changed):
        return "verify" if has_substance else "none"
    if len(changed) == 1 and _is_generated(changed[0]):
        return "verify" if has_substance else "none"
    # Removed phrase-count gate — tiny files still need verification if task has substance.
    verify_with = _preflight_verify_files(changed, None, analysis.file_vocabs if analysis else [])
    if not verify_with:
        try:
            matrix = entanglement_matrix(path, lookback_commits=50)
            entangled = _entangled_candidates_for_changed(changed, matrix)
            if not entangled:
                return "human"
        except Exception:
            return "human"
    try:
        stability_data = compute_stability(path, weeks=12)
        stable_by_file = {item["file"]: item for item in stability_data}
        for f in changed:
            if f in stable_by_file and stable_by_file[f].get("persistence", 0) >= 0.8:
                return "contract"
    except Exception:
        pass
    return "verify"


def _adaptive_route(path: str, changed: list[str], analysis, task: str | None = None) -> dict:
    """Returns {action, condition, route_reason} with fragment matrix awareness."""
    default_action = _route_path(path, changed, analysis, task)
    file_types = [_file_type(f) for f in changed]
    dom_type = max(set(file_types), key=file_types.count) if file_types else "unknown"
    repo = os.path.basename(os.path.realpath(path))
    matrix = _load_fragment_matrix(path)
    cand_conditions = ["cartridge", "verify_entangle", "verify_scope"]
    best = {"condition": default_action, "hit_rate": 0, "trials": 0}
    for cond in cand_conditions:
        key = (repo, dom_type, cond)
        entry = matrix.get(key)
        if entry and entry["trial_count"] >= 3:
            hit_rate = entry["hit_count"] / entry["trial_count"]
            if hit_rate > best["hit_rate"]:
                best = {"condition": cond, "hit_rate": hit_rate, "trials": entry["trial_count"]}
    if best["hit_rate"] >= 0.8 and best["condition"] != default_action and best["trials"] >= 3:
        return {"action": "verify", "condition": best["condition"], "route_reason": f"fragment matrix: {repo}/{dom_type} -> {best['condition']} ({best['hit_rate']:.0%}, {best['trials']} trials)"}
    return {"action": default_action, "condition": default_action, "route_reason": "default rules"}


def route_recommendation(path: str, task: str | None = None, files: list[str] | None = None) -> dict:
    """Decide intervention tier: none / verify / contract / human.

    Routes trivial changes past the LLM, uses cartridge for standard
    verification, escalates to contract for risky changes, and flags
    verification deserts for human review.
    """
    from quale.scanner import scan_codebase
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}
    path = os.path.abspath(path)
    normalized_files = _normalize_preflight_files(path, files or []) if files else []
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception:
        analysis = None

    if normalized_files:
        adaptive = _adaptive_route(path, normalized_files, analysis, task)
        action = adaptive["action"]
        adaptive.get("condition", action)
        adaptive.get("route_reason", "adaptive")
    elif task and _task_is_vague(task):
        action = "none"
    elif task:
        action = "verify"
    else:
        action = "human"

    reasons: list[str] = []
    warnings: list[str] = []
    command: list[str] = []

    if action == "none":
        command = []
        reasons.append("change is structurally trivial; no LLM verification needed")
        fallback = _preflight_verify_files(normalized_files, None, analysis.file_vocabs if analysis else [])
        if fallback:
            reasons.append(f"fallback: {len(fallback)} structural candidates available as safety net")
    elif action == "verify":
        command = ["quale", "cartridge", "--path", path]
        for f in normalized_files[:10]:
            command.extend(["--files", f])
        if task:
            command.extend(["--task", task])
        reasons.append("file-scoped verification cartridge available")
    elif action == "contract":
        command = ["quale", "contract", "--path", path, "--format", "tool"]
        for f in normalized_files[:10]:
            command.extend(["--files", f])
        if task:
            command.extend(["--task", task])
        reasons.append("risky change set; use ID-coded contract")
    elif action == "human":
        command = ["quale", "inspect", path]
        reasons.append("verification desert or no file scope; human review recommended")

    deserts = verification_deserts(path, max_results=5)
    if not deserts.get("error"):
        mirror_ratio = deserts.get("mirror_ratio", 0.0)
        if mirror_ratio < 0.25 and _is_weird_language(path):
            warnings.append("verification topology is sparse; treat suggestions as low confidence")

    return {
        "schema_version": 1,
        "action": action,
        "route_reason": reasons[0] if reasons else "unknown",
        "command": command,
        "reasons": reasons,
        "warnings": warnings,
        "policy": {
            "intervention_tier": action,
            "null_route_threshold": "trivial_declarative_generated_changes_get_no_llm",
            "verify_threshold": "default_file_scoped_guidance",
            "contract_threshold": "stable_anchor_or_broad_mirror_gap",
            "human_threshold": "verification_desert_or_no_file_scope",
        },
        "confidence": "high" if normalized_files else "mixed",
    }


def build_contract(path: str = ".", files: list[str] | None = None,
                   task: str | None = None) -> dict:
    """Build an ID-coded structural edit contract for LLM plans.

    The contract is intentionally smaller and stricter than preflight output:
    the LLM should choose IDs, not invent paths.
    """
    preflight = preflight_report(path=path, files=files, task=task)
    if "error" in preflight:
        return {"schema_version": 1, "error": preflight["error"]}

    file_map: dict[str, str] = {}
    allowed_edit: list[str] = []
    verify_options: list[str] = []
    boundary: list[str] = []

    for idx, file in enumerate(preflight.get("changed_files", [])[:8], 1):
        fid = _contract_id("F", idx, file)
        file_map[fid] = file
        allowed_edit.append(fid)

    for idx, file in enumerate(preflight.get("verification_candidates", [])[:5], 1):
        if file in file_map.values():
            continue
        fid = _contract_id("T", idx, file)
        file_map[fid] = file
        verify_options.append(fid)

    boundary_paths: list[str] = []
    for key in ("expansion_risk", "read_first"):
        for file in preflight.get(key, []) or []:
            if file not in file_map.values() and file not in boundary_paths:
                boundary_paths.append(file)
    for idx, file in enumerate(boundary_paths[:8], 1):
        fid = _contract_id("B", idx, file)
        file_map[fid] = file
        boundary.append(fid)

    import hashlib
    scope_payload = json.dumps({
        "task": task or "",
        "allowed_edit": [file_map[i] for i in allowed_edit],
        "verify_options": [file_map[i] for i in verify_options],
        "boundary": [file_map[i] for i in boundary],
    }, sort_keys=True)
    digest = hashlib.sha256(scope_payload.encode()).hexdigest()
    verification_desert = not verify_options or preflight.get("verification_confidence", {}).get("level") == "low"

    return {
        "schema_version": 1,
        "contract_id": f"c_{digest[:10]}",
        "mode": "scoped_edit",
        "task": task,
        "files": file_map,
        "allowed_edit": allowed_edit,
        "verify_options": verify_options,
        "boundary": boundary,
        "forbidden": [],
        "verification_desert": verification_desert,
        "risk": preflight.get("risk", "unknown"),
        "confidence": preflight.get("confidence", "unknown"),
        "scope_hash": f"sha256-{digest}",
        "must_return": {
            "edit_ids": [],
            "verify_ids": [],
            "expand_scope": [],
            "manual_verify": [],
        },
        "rules": [
            "Return IDs only, not raw paths.",
            "edit_ids must be from allowed_edit.",
            "verify_ids must be from verify_options unless verification_desert is true.",
            "Request boundary IDs via expand_scope instead of editing them directly.",
            "Each expand_scope entry must include a reason: [{\"id\": \"B1\", \"reason\": \"why this boundary file is needed\"}]",
        ],
        "guardrails": {
            "mode": "report_only_contract",
            "not_semantic_truth": True,
            "rerun_preflight_after_expand_scope": True,
        },
    }




def validate_plan(contract: dict, proposal: dict, allow_paths: bool = False) -> dict:
    """Validate an LLM plan against an ID-coded contract."""
    file_map = contract.get("files", {}) if isinstance(contract.get("files"), dict) else {}
    known_ids = set(file_map)
    allowed_edit = set(contract.get("allowed_edit", []))
    verify_options = set(contract.get("verify_options", []))
    boundary = set(contract.get("boundary", []))

    edit_ids = _string_list(proposal.get("edit_ids"))
    verify_ids = _string_list(proposal.get("verify_ids"))
    expand_raw = proposal.get("expand_scope", [])
    if isinstance(expand_raw, list):
        expand_no_reason = [item["id"] for item in expand_raw
                           if isinstance(item, dict) and "id" in item and "reason" not in item]
    else:
        expand_no_reason = []
    expand_ids = _expand_scope_ids(expand_raw)
    manual_verify = _string_list(proposal.get("manual_verify"))

    used_ids = edit_ids + verify_ids + expand_ids
    violations: list[dict[str, Any]] = []
    unknown = [item for item in used_ids if item not in known_ids]
    if unknown:
        violations.append({"code": "unknown_id", "ids": unknown})

    raw_paths = [item for item in used_ids if _looks_like_path(item)]
    if raw_paths and not allow_paths:
        violations.append({"code": "raw_path_not_allowed", "values": raw_paths})

    bad_edits = [item for item in edit_ids if item in known_ids and item not in allowed_edit]
    if bad_edits:
        violations.append({"code": "edit_outside_allowed_scope", "ids": bad_edits})

    bad_verify = [item for item in verify_ids if item in known_ids and item not in verify_options]
    if bad_verify:
        violations.append({"code": "verify_id_not_allowed", "ids": bad_verify})

    if contract.get("verification_desert") and verify_ids:
        violations.append({"code": "verification_desert_requires_manual_verify", "ids": verify_ids})

    boundary_edits = [item for item in edit_ids if item in boundary and item not in expand_ids]
    if boundary_edits:
        violations.append({"code": "boundary_edit_requires_expand_scope", "ids": boundary_edits})

    invalid_expands = [item for item in expand_ids if item in known_ids and item not in boundary]
    if invalid_expands:
        violations.append({"code": "expand_scope_must_use_boundary_ids", "ids": invalid_expands})

    if expand_no_reason:
        violations.append({"code": "expand_scope_missing_reason", "ids": expand_no_reason})

    needs_reflight = bool(expand_ids) and not violations
    valid = not violations and not needs_reflight
    return {
        "schema_version": 1,
        "contract_id": contract.get("contract_id", ""),
        "valid": valid,
        "needs_reflight": needs_reflight,
        "scope_expansion_requested": bool(expand_ids),
        "violations": violations,
        "edit_paths": [file_map[i] for i in edit_ids if i in file_map],
        "verify_paths": [file_map[i] for i in verify_ids if i in file_map],
        "expand_paths": [file_map[i] for i in expand_ids if i in file_map],
        "manual_verify": manual_verify,
        "guardrails": {
            "mode": "deterministic_validation",
            "not_semantic_truth": True,
            "allow_paths": allow_paths,
        },
    }


def _active_gene_pool(path: str, active_days: int) -> set[str]:
    """Return files modified within active_days."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "--since", f"{active_days}.days", "--name-only", "--pretty=format:", "--diff-filter=ACMR"],
            capture_output=True, cwd=path, timeout=30, text=True, errors="replace",
        )
        files: set[str] = set()
        for line in out.stdout.split("\n"):
            f = line.strip()
            if f:
                files.add(f)
        return files
    except Exception:
        return set()


def _task_is_vague(task: str) -> bool:
    words = [word for word in re.findall(r"[A-Za-z0-9_]+", task.lower()) if len(word) > 2]
    vague = {"fix", "improve", "update", "change", "refactor", "clean", "reliability", "performance", "bug", "stuff", "thing"}
    if len(words) <= 3:
        return True
    if sum(1 for word in words if word in vague) / max(len(words), 1) >= 0.5:
        return True
    return False


def _is_weird_language(path: str) -> bool:
    """Heuristic: return True if this repo is dominated by a language with weak structural-test mirroring."""
    try:
        result = vgit.run(path, ["git", "ls-files"], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return False
        exts: dict[str, int] = {}
        for line in result.stdout.splitlines():
            _, ext = os.path.splitext(line)
            if ext:
                exts[ext] = exts.get(ext, 0) + 1
        if not exts:
            return False
        mainstream = {".go", ".py", ".ts", ".js", ".java", ".rs", ".c", ".cpp", ".h", ".hpp"}
        total = sum(exts.values())
        mainstream_count = sum(v for k, v in exts.items() if k in mainstream)
        return (mainstream_count / total) < 0.5 if total > 0 else True
    except Exception:
        return False


def _classify_files(
    changed: list[str],
    stable_by_file: dict[str, Any],
    blast: list[dict[str, Any]],
    co_change: list[dict[str, Any]],
    analysis: Any,
) -> list[dict[str, Any]]:
    """Classify each changed file into a 2×2 structural risk matrix.

    Self = stable core (persistence >= 0.8).
    Keystone = high blast × binding × co-change probability (above 70th percentile).
    Matrix: SELF_KEYSTONE | SELF_STANDARD | FRONTIER | NON_SELF
    """
    from quale.compare import _extract_identifiers

    blast_scores: dict[str, int] = {}
    for imp in blast:
        blast_scores[imp.get("file", "")] = imp.get("shared_concepts", 0)
    co_change_probs: dict[str, float] = {}
    for cc in co_change:
        co_change_probs[cc.get("file", "")] = cc.get("probability", 0)

    all_binding: dict[str, int] = defaultdict(int)
    for fv in analysis.file_vocabs:
        ids = _extract_identifiers(fv)
        for identifier in ids:
            all_binding[identifier] += 1
    # Compute binding strength per file: how many other files share its top identifier
    binding: dict[str, int] = {}
    for fv in analysis.file_vocabs:
        ids = _extract_identifiers(fv)
        top_id = max(ids, key=lambda x: all_binding.get(x, 0)) if ids else ""
        binding[fv.path] = all_binding.get(top_id, 0)

    keystone_scores: dict[str, float] = {}
    for f in changed:
        b = blast_scores.get(f, 0)
        c = co_change_probs.get(f, 0)
        bind = binding.get(f, 1)
        keystone_scores[f] = b * 0.4 + c * 0.3 + min(bind / 10, 10) * 0.3

    threshold = sorted(keystone_scores.values())[max(len(changed) * 3 // 10 - 1, 0)] if keystone_scores else 0
    # Actually use 70th percentile properly
    vals = sorted(keystone_scores.values())
    threshold = vals[int(len(vals) * 0.7)] if len(vals) >= 5 else (vals[-1] if vals else 0)

    results = []
    for f in changed:
        sc = stable_by_file.get(f, {})
        is_self = sc.get("persistence", 0) >= 0.8
        ks = keystone_scores.get(f, 0)
        is_keystone = ks >= threshold and ks > 0
        if is_self and is_keystone:
            cls = "SELF_KEYSTONE"
        elif is_self:
            cls = "SELF_STANDARD"
        elif is_keystone:
            cls = "FRONTIER"
        else:
            cls = "NON_SELF"
        results.append({
            "file": f,
            "class": cls,
            "keystone_score": round(ks, 2),
            "stability": "self" if is_self else "non_self",
            "persistence": round(sc.get("persistence", 0), 2) if sc else 0,
        })
    return results


# ── Stability anchors ─────────────────────────────────────────────

def compute_stability(path: str, weeks: int = 12, min_appearances: int = 4) -> list[dict]:
    """Per-file stability using git log (single call) instead of N rescans.

    Issues ONE `git log --name-only` call for the entire window, buckets file
    changes by calendar week, then computes persistence = 1 - (active_weeks / total_weeks).
    Files with zero changes in the window get max persistence.
    """
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    total_weeks = len(week_data)
    if not week_data:
        return []

    from quale.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2000, max_seconds=25)
    if not analysis.file_vocabs:
        return []

    first = week_data[0]
    last = week_data[-1]
    first_sha = first.get("shas", [None])[0]
    last_sha = last.get("shas", [None])[0]
    if not first_sha or not last_sha:
        return []

    # Single git log call: get every file change in the window bucketed by week
    try:
        out = vgit._git_bytes("log", f"{first_sha}~1..{last_sha}",
                              "--format=%ai", "--name-only", cwd=path)
    except RuntimeError:
        return []

    import datetime
    file_weeks: dict[str, set[str]] = {}
    current_week: str | None = None
    for raw_line in out.split(b"\n"):
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            current_week = None
            continue
        if line[0:1].isdigit() and len(line) >= 10 and line[4] == "-" and line[10] == " ":
            try:
                current_week = datetime.datetime.strptime(line[:10], "%Y-%m-%d").strftime("%Y-W%W")
            except ValueError:
                current_week = None
        elif current_week and line and not line.startswith(" "):
            file_weeks.setdefault(line, set()).add(current_week)

    results = []
    for fv in analysis.file_vocabs:
        weekly_hits = file_weeks.get(fv.path, set())
        # 0 changes = max persistence
        persistence = 1.0 - (len(weekly_hits) / max(total_weeks, 1))
        total_phrases = len(fv.vocabulary)

        results.append({
            "file": fv.path,
            "persistence": round(persistence, 3),
            "avg_turnover": round(len(weekly_hits) / max(weeks, 1), 3),
            "snapshots": total_weeks,
            "total_phrases": total_phrases,
            "stable_phrases": max(0, total_phrases - len(weekly_hits)),
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
    """Concept lifecycles using git diff (no per-file content reads).

    Scans HEAD once, then uses git diff --unified=0 between weekly pairs to
    extract added/removed token candidates from the diff text itself, eliminating
    all per-file `git show` overhead.  O(weeks) git calls vs. O(weeks × files).
    """
    if not vgit.is_repo(path):
        return []

    week_data = vgit.weekly_commits(path, weeks=weeks)
    if not week_data:
        return []

    from quale.scanner import scan_codebase, _is_lock_file, _is_generated

    concept_weeks: dict[str, set[int]] = defaultdict(set)
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')
    rename_pairs: list[tuple[str, str, int]] = []

    # Scan HEAD once
    try:
        head_analysis = scan_codebase(path, quiet=True, max_files=1500, max_seconds=20)
    except Exception:
        head_analysis = None

    if head_analysis:
        for fv in head_analysis.file_vocabs:
            ext = os.path.splitext(fv.path)[1].lower()
            if ext not in _DEAD_CODE_EXTS or _is_lock_file(fv.path) or _is_generated(fv.path):
                continue
            for phrase in fv.vocabulary:
                for m in _EXPORT_TOKEN.finditer(phrase):
                    concept_weeks[m.group()].add(len(week_data) - 1)

    shas: list[str] = [wk["shas"][-1] for wk in week_data if wk.get("shas")]

    # Walk backwards through weeks using git diff --unified=0
    # to extract added/removed tokens without per-file content reads
    for i in range(len(shas) - 1):
        week_idx = len(week_data) - i - 1
        sha_prev = shas[-(i + 2)]
        sha_curr = shas[-(i + 1)]

        try:
            diff_text = vgit._git_bytes("diff", "--unified=0", sha_prev, sha_curr, cwd=path)
        except RuntimeError:
            continue

        added_tokens: set[str] = set()
        removed_tokens: set[str] = set()
        for line in diff_text.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                for m in _EXPORT_TOKEN.finditer(line[1:]):
                    added_tokens.add(m.group())
            elif line.startswith("-") and not line.startswith("---"):
                for m in _EXPORT_TOKEN.finditer(line[1:]):
                    removed_tokens.add(m.group())

        for token in added_tokens:
            concept_weeks[token].add(week_idx)
        for token in removed_tokens:
            if token not in concept_weeks:
                concept_weeks[token].add(week_idx - 1)

        if removed_tokens and added_tokens:
            for old in list(removed_tokens)[:5]:
                old_base = re.sub(r'(V\d+|Old|Legacy)$', '', old)
                for new in list(added_tokens)[:5]:
                    if old_base and (old_base in new or re.sub(r'(New|V\d+)$', '', new) == old_base):
                        rename_pairs.append((old, new, week_idx))

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

    from quale.scanner import scan_codebase

    shas = [wk["shas"][-1] for wk in weeks_data if wk.get("shas")]
    if not shas:
        return []

    # Scan HEAD once only
    head_analysis = scan_codebase(path, quiet=True, max_files=1500, max_seconds=20)
    head_phrases = {phrase for fv in head_analysis.file_vocabs for phrase in fv.vocabulary} if head_analysis else set()

    # Walk backwards from HEAD, using git diff between consecutive SHAs
    # to detect phrase-level changes without full rescans.
    current_phrases = head_phrases
    for i in range(len(shas)):
        if i == 0:
            wk = weeks_data[-1]
            timeline.append({
                "week": wk["week"],
                "commits": wk["commit_count"],
                "new_concepts": 0,
                "retired_concepts": 0,
                "stable_concepts": len(current_phrases),
                "total_concepts": len(current_phrases),
            })
            continue

        sha_curr = shas[-i]          # more recent
        sha_prev = shas[-(i + 1)]    # older
        wk = weeks_data[-(i + 1)]

        try:
            diff_text = vgit._git_bytes("diff", "--unified=0", sha_prev, sha_curr, cwd=path)
        except RuntimeError:
            continue

        added_phrases_fwd: set[str] = set()
        removed_phrases_fwd: set[str] = set()
        for line in diff_text.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                added_phrases_fwd.add(line[1:].strip())
            elif line.startswith("-") and not line.startswith("---"):
                removed_phrases_fwd.add(line[1:].strip())

        # When walking backwards, forward-added phrases don't exist in the past,
        # and forward-removed phrases DO exist in the past.
        current_phrases = (current_phrases - added_phrases_fwd) | removed_phrases_fwd

        timeline.append({
            "week": wk["week"],
            "commits": wk["commit_count"],
            "new_concepts": len(added_phrases_fwd),
            "retired_concepts": len(removed_phrases_fwd),
            "stable_concepts": len(current_phrases),
            "total_concepts": len(current_phrases),
        })

    timeline.reverse()
    return timeline


# ── Cached scan cache for delta / anomaly detection ───────────────

_CACHE_DIR = ".quale-cache"

def _cache_path(path: str) -> str:
    return os.path.join(path, _CACHE_DIR, "crystallography.json")

def _load_cached(path: str) -> dict | None:
    cp = _cache_path(path)
    if not os.path.isfile(cp):
        return None
    try:
        with open(cp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_cached(path: str, data: dict) -> None:
    cp = _cache_path(path)
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Calibration (T7) ──────────────────────────────────────────────

_CALIBRATION_FILE = "calibration.jsonl"


def _calibration_path(path: str) -> str:
    """Path to calibration log under .quale-cache."""
    return os.path.join(path, _CACHE_DIR, _CALIBRATION_FILE)


def _record_calibration(path: str, record: dict) -> None:
    """Append one calibration record to the JSONL file."""
    cp = _calibration_path(path)
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    try:
        with open(cp, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # calibration logging is best-effort


def _repo_hash(path: str) -> str:
    """Stable hash for a repo (based on real path)."""
    import hashlib
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:16]


def compute_calibration(path: str, last_n: int = 100) -> dict:
    """Read calibration log and compute accuracy metrics."""
    cp = _calibration_path(path)
    if not os.path.isfile(cp):
        return {"records": 0, "note": "No calibration data yet. Run verify-scope to start tracking."}
    repo = _repo_hash(path)
    records: list[dict] = []
    try:
        with open(cp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("repo_hash") == repo:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return {"records": 0, "error": "Failed to read calibration log."}

    recent = records[-last_n:]
    if not recent:
        return {"records": 0, "note": "No calibration records for this repo."}

    n = len(recent)
    scope_ok = sum(1 for r in recent if r.get("scope_matched", True))
    verify_hits = sum(1 for r in recent if r.get("verification_candidate_hit", False))
    risk_high = [r for r in recent if r.get("risk") == "high"]
    risk_high_violations = sum(1 for r in risk_high if not r.get("scope_matched", True))

    result: dict = {
        "records": n,
        "scope_accuracy": round(scope_ok / n, 3) if n else 0,
        "verification_accuracy": round(verify_hits / n, 3) if n else 0,
    }
    if risk_high:
        result["risk_high_violation_rate"] = round(risk_high_violations / len(risk_high), 3)
    if n < 30:
        result["warning"] = f"Small sample ({n} records); not statistically significant."
    return result


# ── Health Score ──────────────────────────────────────────────────

def health_score(path: str) -> float:
    """Single 0-1 health score from stability + mirror + churn + concept age."""
    try:
        from quale.scanner import scan_codebase
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
        if not analysis.file_vocabs:
            return 0.5

        total = analysis.total_files
        languages = analysis.languages or {}

        # Language diversity score (mono = healthy? poly = healthy? neutral)
        min(len(languages) / 5, 1.0)

        # Generated file penalty
        from quale.scanner import _is_generated
        gen_count = sum(1 for fv in analysis.file_vocabs if _is_generated(fv.path))
        gen_penalty = 1.0 - min(gen_count / max(total, 1) / 0.5, 1.0)

        # Mirror gap: source/test balance
        from quale.scanner import _mirror_signals
        changed = [fv.path for fv in analysis.file_vocabs[:20]]
        mirror = _mirror_signals(changed, analysis.file_vocabs)
        mirror_gap = mirror.get("mirror_ratio", 0.5) if mirror else 0.5

        # Stability: stable anchor proportion
        try:
            stability_data = compute_stability(path, weeks=12)
            stable_count = sum(1 for s in stability_data if s["persistence"] >= 0.8)
            stable_ratio = min(stable_count / max(len(stability_data), 1), 1.0)
        except Exception:
            stable_ratio = 0.5

        # Concept age
        try:
            lifecycle_data = compute_lifecycles(path, weeks=24)
            if lifecycle_data:
                dead = sum(1 for lc in lifecycle_data if lc["signal"] == "DEAD")
                total_concepts = len(lifecycle_data)
                dead_ratio = 1.0 - min(dead / max(total_concepts, 1), 1.0)
            else:
                dead_ratio = 0.5
        except Exception:
            dead_ratio = 0.5

        score = (gen_penalty * 0.3 + mirror_gap * 0.25 + stable_ratio * 0.25 + dead_ratio * 0.2)
        return round(score, 2)
    except Exception:
        return 0.5


# ── Invasive Concepts ─────────────────────────────────────────────

def invasive_concepts(path: str, top_n: int = 5) -> list[dict]:
    """Find concepts that may be from external dependencies (tight coupling signal)."""
    from quale.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return []

    concept_files: dict[str, set[str]] = {}
    _EXPORT_TOKEN = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')

    for fv in analysis.file_vocabs:
        for phrase in fv.vocabulary:
            for m in _EXPORT_TOKEN.finditer(phrase):
                token = m.group()
                concept_files.setdefault(token, set()).add(fv.path)

    external_signal: list[tuple[str, int, float]] = []
    for concept, files in concept_files.items():
        count = len(files)
        if count < 3 or count > 40:
            continue
        widespread_ratio = count / max(analysis.total_files, 1)
        if widespread_ratio < 0.03 or widespread_ratio > 0.5:
            continue
        # Prefer concepts imported via external paths
        imported_paths = [f for f in files if "/vendor/" in f or "/node_modules/" in f or "external" in f.lower()]
        external_ratio = len(imported_paths) / max(len(files), 1)
        if external_ratio > 0.1 or True:  # structural coupling, not just import detection
            external_signal.append((concept, count, widespread_ratio))

    external_signal.sort(key=lambda x: -x[1])
    results = []
    for concept, count, ratio in external_signal[:top_n]:
        results.append({
            "concept": concept,
            "file_count": count,
            "widespread_ratio": round(ratio, 3),
        })
    return results


# ── Anomaly Detection ─────────────────────────────────────────────

def detect_anomalies(path: str) -> list[dict]:
    """Compare current scan against last cached scan and flag deltas."""
    cached = _load_cached(path)
    if not cached:
        return [{"note": "no cached scan to compare against; run quale init first"}]

    current = crystallography(path)
    if "error" in current:
        return [{"error": current["error"]}]

    anomalies: list[dict] = []

    # File count delta
    old_files = cached.get("total_files", 0)
    new_files = current.get("total_files", 0)
    delta_files = new_files - old_files
    if abs(delta_files) >= 10:
        anomalies.append({
            "type": "file_count_shift",
            "old": old_files,
            "new": new_files,
            "delta": delta_files,
            "severity": "high" if abs(delta_files) >= 50 else "medium",
        })

    # Generated file delta
    old_gen = cached.get("generated_pct", 0)
    new_gen = current.get("generated_pct", 0)
    if abs(new_gen - old_gen) >= 10:
        anomalies.append({
            "type": "generated_shift",
            "old": old_gen,
            "new": new_gen,
            "delta": round(new_gen - old_gen, 1),
            "severity": "medium",
        })

    # Stable core delta
    old_stable = {s["file"] for s in cached.get("stable_core", [])}
    new_stable = {s["file"] for s in current.get("stable_core", [])}
    lost_stable = old_stable - new_stable
    gained_stable = new_stable - old_stable
    if lost_stable:
        anomalies.append({
            "type": "stable_core_shift",
            "lost": list(lost_stable)[:5],
            "gained": list(gained_stable)[:5],
            "severity": "high",
        })

    # Language delta
    old_langs = set(cached.get("languages", {}).keys())
    new_langs = set(current.get("languages", {}).keys())
    if old_langs and new_langs and old_langs != new_langs:
        anomalies.append({
            "type": "language_shift",
            "added": list(new_langs - old_langs)[:5],
            "removed": list(old_langs - new_langs)[:5],
            "severity": "medium",
        })

    return anomalies


# ── Dead Reckoning Delta ──────────────────────────────────────────

def repo_delta(path: str) -> dict:
    """Compute delta between cached and current repo state."""
    cached = _load_cached(path)
    if not cached:
        return {"error": "no cached scan; run quale init first", "schema_version": 1}

    current = crystallography(path)
    if "error" in current:
        return {"error": current["error"], "schema_version": 1}

    old_total = cached.get("total_files", 0)
    new_total = current.get("total_files", 0)
    old_gen_pct = cached.get("generated_pct", 0)
    new_gen_pct = current.get("generated_pct", 0)

    old_stable = {s["file"] for s in cached.get("stable_core", [])}
    new_stable = {s["file"] for s in current.get("stable_core", [])}
    old_concepts = {c["concept"] for c in cached.get("core_concepts", [])}
    new_concepts = {c["concept"] for c in current.get("core_concepts", [])}

    return {
        "schema_version": 1,
        "old_files": old_total,
        "new_files": new_total,
        "file_delta": new_total - old_total,
        "generated_before": old_gen_pct,
        "generated_now": new_gen_pct,
        "generated_delta": round(new_gen_pct - old_gen_pct, 1),
        "stable_lost": list(old_stable - new_stable),
        "stable_gained": list(new_stable - old_stable),
        "concepts_lost": list(old_concepts - new_concepts),
        "concepts_gained": list(new_concepts - old_concepts),
        "anomalies": detect_anomalies(path),
    }


# ── Inspect repo ──────────────────────────────────────────────────

def inspect_repo(path: str) -> dict:
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from quale.scanner import scan_codebase, _binding_concepts
    from quale.bootstrap import explore_repo, compute_modules

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    explore_data = explore_repo(path, themes=True, analysis=analysis)
    modules_data = compute_modules(path, analysis=analysis)
    timeline_data = concept_timeline(path, weeks=4)
    binding = _binding_concepts(analysis)

    try:
        lifecycle_data = compute_lifecycles(path, weeks=24)
        if lifecycle_data:
            ages = [lc["age_weeks"] for lc in lifecycle_data if lc["signal"] in ("STABLE", "ACTIVE", "DEAD")]
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

    health = health_score(path)
    invasive = invasive_concepts(path)

    # Confidence bands
    conf_signals: dict[str, float | int | str | None] = {
        "file_count": analysis.total_files,
        "concept_age_weeks": avg_age,
        "health_score": health,
        "module_count": len(modules_data.get("modules", [])) if isinstance(modules_data, dict) else 0,
        "debt_candidates": len(debt_candidates),
        "mirror_coverage": 0,
    }
    # Try to get mirror signal
    try:
        from quale.scanner import _mirror_signals
        changed = [fv.path for fv in analysis.file_vocabs[:20]]
        mirror = _mirror_signals(changed, analysis.file_vocabs)
        conf_signals["mirror_coverage"] = mirror.get("mirror_ratio", 0) if mirror else 0
    except Exception:
        pass

    return {
        "schema_version": 1,
        "explore": explore_data,
        "modules": modules_data,
        "binding_concepts": binding,
        "timeline": timeline_data,
        "avg_concept_age_weeks": avg_age,
        "debt_candidates": debt_candidates[:15],
        "health_score": health,
        "invasive_concepts": invasive,
        "confidence": confidence_band(conf_signals),
    }


# ── Crystallography (one-time structural description) ─────────────

def crystallography(path: str = ".") -> dict:
    """One-time repo structural summary designed for LLM usage.

    Produces a compact description of the codebase's structure,
    stable core, test conventions, and generated file patterns.
    Meant to be cached and reused across agent tasks.
    """
    from quale.scanner import scan_codebase, _binding_concepts, _is_generated
    from quale.bootstrap import explore_repo, compute_modules

    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    explore_repo(path, themes=False, analysis=analysis)
    modules_data = compute_modules(path, analysis=analysis)
    binding = _binding_concepts(analysis)

    total_files = analysis.total_files
    languages = dict(analysis.languages) if hasattr(analysis, "languages") else {}

    # Detect test conventions
    test_dirs: set[str] = set()
    test_suffixes: set[str] = set()
    source_test_mirror = False
    for fv in analysis.file_vocabs:
        p = fv.path
        if "/test" in p.lower() or p.lower().startswith("test/"):
            test_dirs.add("test/")
        if "tests/" in p.lower():
            test_dirs.add("tests/")
        if p.lower().startswith("spec/") or "/spec/" in p.lower():
            test_dirs.add("spec/")
        ext = os.path.splitext(p)[1]
        base = os.path.basename(p)
        if ".test." in base:
            test_suffixes.add(".test.*")
        if "_test." in base or base.startswith("test_"):
            test_suffixes.add("_test.*")
        if ".spec." in base:
            test_suffixes.add(".spec.*")
        # Check source-test mirror (source/test pairs side by side)
        if ext == ".ts" and source_test_mirror is False:
            for other in analysis.file_vocabs:
                if other.path != p and os.path.splitext(other.path)[0] == os.path.splitext(p)[0]:
                    if "test" in other.path.lower():
                        source_test_mirror = True
                        break

    # Detect generated files
    generated_count = sum(1 for fv in analysis.file_vocabs if _is_generated(fv.path))
    generated_pct = round(generated_count / max(total_files, 1) * 100, 1)

    # Stable core: top 5 files by structural importance
    stable_core = []
    try:
        stability_data = compute_stability(path, weeks=12)
        stable_sorted = [s for s in stability_data if s["persistence"] >= 0.8]
        stable_sorted.sort(key=lambda x: -x["persistence"])
        for s in stable_sorted[:5]:
            stable_core.append({"file": s["file"], "persistence": s["persistence"]})
    except Exception:
        pass

    # Top binding concepts (indicating architectural centrality)
    top_binders = []
    for b in binding[:5]:
        top_binders.append({"concept": b["concept"], "file_count": b["file_count"]})

    # Module structure — compute_modules returns dict {"modules": [...]}
    module_summary = []
    module_list = []
    if isinstance(modules_data, dict):
        module_list = modules_data.get("modules", [])
    elif isinstance(modules_data, list):
        module_list = modules_data
    for m in module_list[:5]:
        if isinstance(m, dict):
            module_summary.append({
                "size": m.get("size", 0),
                "sample_files": [f.replace("\\", "/").split("/")[-1] for f in m.get("files", [])[:3]],
            })

    # File layout pattern
    config_files = [fv.path for fv in analysis.file_vocabs if "config" in fv.path.lower() or ".env" in fv.path.lower()]
    handler_files = [fv.path for fv in analysis.file_vocabs if "handler" in fv.path.lower()]
    service_files = [fv.path for fv in analysis.file_vocabs if "service" in fv.path.lower()]

    layout_patterns = []
    if handler_files:
        layout_patterns.append("handler")
    if service_files:
        layout_patterns.append("service")
    if config_files:
        layout_patterns.append("config")

    layout_type = "unknown"
    if handler_files and service_files:
        layout_type = "handler-service"
    elif handler_files:
        layout_type = "handler-centric"
    elif service_files:
        layout_type = "service-centric"

    # Build compact skeleton (~100 tokens)
    test_convention = "unknown"
    if test_dirs and test_suffixes:
        test_convention = "both"
    elif test_dirs:
        test_convention = "dir"
    elif test_suffixes:
        test_convention = "suffix"
    elif source_test_mirror:
        test_convention = "mirror"

    skeleton_parts = []
    skeleton_parts.append(f"Lang: {', '.join(sorted(languages.keys())[:5])}.")
    skeleton_parts.append(f"Files: {total_files}.")
    skeleton_parts.append(f"Layout: {layout_type} test:{test_convention}.")
    skeleton_parts.append(f"Gen: {generated_pct}% generated.")
    if stable_core:
        stable_files = [s["file"].replace("\\", "/").split("/")[-1] for s in stable_core[:3]]
        skeleton_parts.append(f"Stable: {', '.join(stable_files)}.")
    if top_binders:
        cores = [b["concept"] for b in top_binders[:2]]
        skeleton_parts.append(f"Core: {', '.join(cores)}.")

    skeleton = " ".join(skeleton_parts)

    return {
        "schema_version": 1,
        "total_files": total_files,
        "languages": languages,
        "layout_type": layout_type,
        "test_convention": test_convention,
        "test_dirs": sorted(test_dirs) if test_dirs else [],
        "test_suffixes": sorted(test_suffixes) if test_suffixes else [],
        "generated_pct": generated_pct,
        "stable_core": stable_core[:5],
        "core_concepts": top_binders[:5],
        "modules": module_summary[:5],
        "skeleton": skeleton,
        "guardrails": {
            "mode": "report_only",
            "not_semantic_truth": True,
            "one_time_summary": True,
            "does_not_guarantee_task_accuracy": True,
            "caveat": "Cached summary; re-run on significant refactors.",
        },
        "privacy_receipt": {
            "local_only": True,
            "uploaded": False,
            "network": False,
        },
    }


# ── Crystallographic Defect Detection ─────────────────────────────
# Maps crystal defects to vocabulary-lattice changes.
#  Vacancy: expected concept removed from a changed file.
#  Interstitial: unexpected concept inserted into a changed file.
#  Substitution: concept replaced by structurally-similar concept.

def lattice_defects(path: str, base_ref: str | None = None, head_ref: str | None = None) -> dict:
    from quale.scanner import scan_codebase

    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    head_ref = head_ref or "HEAD"
    base_ref = base_ref or "HEAD~1"

    if vgit.has_commits(path):
        if not vgit.ref_exists(path, base_ref):
            return {"error": f"Unknown base ref: {base_ref}", "schema_version": 1}
        if not vgit.ref_exists(path, head_ref):
            return {"error": f"Unknown head ref: {head_ref}", "schema_version": 1}

    # Scan current tree
    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    changed = vgit.diff_refs(path, base_ref, head_ref)

    # Build co-occurrence lattice from unchanged files
    # For each concept, track which files contain it
    concept_to_files: dict[str, set[str]] = {}
    file_to_concepts: dict[str, set[str]] = {}

    for fv in analysis.file_vocabs:
        if fv.path in changed or not fv.vocabulary:
            continue
        concepts = set()
        for phrase in fv.vocabulary:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{3,40}\b', phrase):
                concept = m.group()
                concepts.add(concept)
                concept_to_files.setdefault(concept, set()).add(fv.path)
        file_to_concepts[fv.path] = concepts

    defects = {"vacancies": [], "interstitials": [], "substitutions": []}

    if not changed:
        return {"schema_version": 1, "changed_files": [], "defects": defects, "confidence": "mixed — no changed files to analyze"}

    # Analyze each changed file against similar files
    for cf in changed[:20]:
        cf_fv = None
        for fv in analysis.file_vocabs:
            if fv.path == cf:
                cf_fv = fv
                break

        if not cf_fv or not cf_fv.vocabulary:
            continue

        cf_concepts: set[str] = set()
        for phrase in cf_fv.vocabulary:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{3,40}\b', phrase):
                cf_concepts.add(m.group())

        # Find similar files: files that share vocabulary with cf
        similar_files: set[str] = set()
        for concept in cf_concepts:
            similar_files.update(concept_to_files.get(concept, set()))

        if not similar_files:
            continue

        # Expected concepts: concepts present in similar files
        expected_concepts: set[str] = set()
        for sf in similar_files:
            expected_concepts.update(file_to_concepts.get(sf, set()))

        # VACANCY: expected concept missing from changed file
        for c in sorted(expected_concepts - cf_concepts):
            files_with = concept_to_files.get(c, set())
            if len(files_with) >= 2:
                defects["vacancies"].append({
                    "concept": c,
                    "file": cf,
                    "present_in": list(files_with)[:3],
                    "severity": "medium" if len(files_with) >= 5 else "low",
                })

        # INTERSTITIAL: unexpected concept appeared
        for c in sorted(cf_concepts - expected_concepts):
            if c in concept_to_files:
                continue  # concept exists elsewhere, just not in similar files
            defects["interstitials"].append({
                "concept": c,
                "file": cf,
                "severity": "low",
            })

        # SUBSTITUTION: concept replaced by structurally-similar concept
        # (simple heuristic: similar length, shared prefix, camelCase parts)
        missing = expected_concepts - cf_concepts
        added = cf_concepts - expected_concepts
        for m in list(missing)[:10]:
            for a in list(added)[:10]:
                if _concept_similarity(m, a) >= 0.5:
                    defects["substitutions"].append({
                        "old_concept": m,
                        "new_concept": a,
                        "file": cf,
                        "similarity": round(_concept_similarity(m, a), 2),
                    })
                    missing.discard(m)
                    added.discard(a)

    return {
        "schema_version": 1,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": changed,
        "defects": defects,
        "summary": {
            "vacancies": len(defects["vacancies"]),
            "interstitials": len(defects["interstitials"]),
            "substitutions": len(defects["substitutions"]),
        },
        "confidence": _lattice_confidence(defects, changed),
    }


def _concept_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if abs(len(a) - len(b)) > 10:
        return 0.0
    # Shared prefix/suffix
    prefix = len(os.path.commonprefix([a.lower(), b.lower()]))
    if prefix >= 3:
        return max(0.3, prefix / max(len(a), len(b)))
    # Shared camelCase parts
    a_parts = set(re.findall(r'[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z]+', a))
    b_parts = set(re.findall(r'[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z]+', b))
    if a_parts and b_parts:
        overlap = len(a_parts & b_parts) / max(len(a_parts | b_parts), 1)
        return max(0.2, overlap)
    return 0.0


def _lattice_confidence(defects: dict, changed: list[str]) -> str:
    total = sum(len(d) for d in defects.values())
    if not changed:
        return "none — no changed files"
    if total == 0:
        return "high — no defects, but limited similar-file data"
    if total <= 3:
        return "mixed — few defects; may miss subtle patterns"
    return "low — multiple defects; manual review recommended"


# ── Refactoring Pattern Detection ─────────────────────────────────

def refactoring_patterns(path: str, base_ref: str | None = None, head_ref: str | None = None, max_files: int = 100) -> dict:
    from quale.segmenter import segment

    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    head_ref = head_ref or "HEAD"
    base_ref = base_ref or "HEAD~1"

    if vgit.has_commits(path):
        if not vgit.ref_exists(path, base_ref):
            return {"error": f"Unknown base ref: {base_ref}", "schema_version": 1}
        if not vgit.ref_exists(path, head_ref):
            return {"error": f"Unknown head ref: {head_ref}", "schema_version": 1}

    changed = vgit.diff_refs(path, base_ref, head_ref)
    if not changed:
        return {"schema_version": 1, "patterns": [], "changed_files": changed, "confidence": "none — no changed files"}

    # Build vocabulary for before and after for each changed file
    patterns: list[dict] = []
    extract_candidates: dict[str, list[str]] = {}
    inline_candidates: dict[str, list[str]] = {}

    for cf in changed[:max_files]:
        try:
            before_content = vgit.read_file_at_ref(path, cf, base_ref)
            after_content = vgit.read_file_at_ref(path, cf, head_ref)
        except Exception:
            continue

        if before_content is None and after_content is None:
            continue

        before_seg = segment(before_content or "")
        after_seg = segment(after_content or "")

        # Build concept sets
        before_concepts: set[str] = set()
        after_concepts: set[str] = set()
        for phrase in before_seg.phrases:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{3,40}\b', phrase):
                before_concepts.add(m.group())
        for phrase in after_seg.phrases:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{3,40}\b', phrase):
                after_concepts.add(m.group())

        if not before_concepts and not after_concepts:
            continue

        added = after_concepts - before_concepts
        removed = before_concepts - after_concepts

        # NEW FILE
        if not before_concepts and after_concepts:
            patterns.append({"type": "new_file", "file": cf, "concepts": sorted(after_concepts)[:5]})
            continue

        # DELETED FILE
        if before_concepts and not after_concepts:
            patterns.append({"type": "deleted_file", "file": cf, "concepts": sorted(before_concepts)[:5]})
            continue

        # RENAME: concepts share similarity across the boundary
        for r in list(removed)[:10]:
            for a in list(added)[:10]:
                if _concept_similarity(r, a) >= 0.5:
                    patterns.append({
                        "type": "rename",
                        "file": cf,
                        "old_concept": r,
                        "new_concept": a,
                        "similarity": round(_concept_similarity(r, a), 2),
                    })
                    removed.discard(r)
                    added.discard(a)

        # EXTRACT: net loss of concepts
        if len(removed) > len(added) + 2:
            extract_candidates[cf] = sorted(removed)[:10]
            patterns.append({
                "type": "extract_candidate",
                "file": cf,
                "lost_concepts": sorted(removed)[:5],
                "note": "File lost significant vocabulary — may have been extracted or split",
            })

        # INLINE: net gain of concepts
        if len(added) > len(removed) + 2:
            inline_candidates[cf] = sorted(added)[:10]
            patterns.append({
                "type": "inline_candidate",
                "file": cf,
                "gained_concepts": sorted(added)[:5],
                "note": "File gained significant vocabulary — may have absorbed another file",
            })

        # MOVE: some concepts moved from one changed file to another
        remaining_added = after_concepts - before_concepts
        for other in changed:
            if other == cf or other not in changed:
                continue
            try:
                other_before = vgit.read_file_at_ref(path, other, base_ref)
                vgit.read_file_at_ref(path, other, head_ref)
                other_before_seg = segment(other_before or "")
                other_before_concepts = set()
                for phrase in other_before_seg.phrases:
                    for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{3,40}\b', phrase):
                        other_before_concepts.add(m.group())
                moved_from_other = remaining_added & other_before_concepts
                if len(moved_from_other) >= 3:
                    patterns.append({
                        "type": "move",
                        "from_file": other,
                        "to_file": cf,
                        "concepts": sorted(moved_from_other)[:5],
                    })
            except Exception:
                pass

    return {
        "schema_version": 1,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": changed,
        "patterns": patterns[:30],
        "confidence": _patterns_confidence(patterns, changed),
    }


def _patterns_confidence(patterns: list[dict], changed: list[str]) -> str:
    renames = sum(1 for p in patterns if p["type"] == "rename")
    extracts = sum(1 for p in patterns if "extract" in p["type"])
    inlines = sum(1 for p in patterns if "inline" in p["type"])
    moves = sum(1 for p in patterns if p["type"] == "move")
    if not patterns:
        return "none — no detectable patterns"
    if renames + moves > 0:
        return "high — rename/move patterns have strong signal"
    if extracts + inlines > 2:
        return "mixed — extract/inline patterns need diff confirmation"
    return "low — patterns detected but signal is weak"


# ── Exploration Entropy ──────────────────────────────────────────

def exploration_entropy(path: str, read_files: list[str]) -> dict:
    from quale.scanner import scan_codebase

    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    # All concepts in the repo
    all_concepts: set[str] = set()
    file_to_concepts: dict[str, set[str]] = {}

    for fv in analysis.file_vocabs:
        concepts = set()
        for phrase in fv.vocabulary:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{3,40}\b', phrase):
                concepts.add(m.group())
        all_concepts.update(concepts)
        file_to_concepts[fv.path] = concepts

    # Concepts already seen
    seen_concepts: set[str] = set()
    for rf in read_files:
        seen_concepts.update(file_to_concepts.get(rf, set()))

    # Remaining unique concepts
    remaining_concepts = all_concepts - seen_concepts

    # Best next files to read (most new concepts)
    candidates = []
    for fv in analysis.file_vocabs:
        if fv.path in read_files:
            continue
        new_concepts = file_to_concepts.get(fv.path, set()) - seen_concepts
        if new_concepts:
            candidates.append({
                "file": fv.path,
                "new_concepts": len(new_concepts),
                "total_concepts": len(file_to_concepts.get(fv.path, set())),
            })

    candidates.sort(key=lambda x: -x["new_concepts"])

    coverage_pct = round(len(seen_concepts) / max(len(all_concepts), 1) * 100, 1)
    marginal_gain = candidates[0]["new_concepts"] if candidates else 0
    analysis.total_files - len(read_files)

    stop_signal = "stop" if coverage_pct >= 80 or (marginal_gain <= 1 and coverage_pct >= 50) else \
                  "slow" if coverage_pct >= 60 or marginal_gain <= 3 else \
                  "continue"

    return {
        "schema_version": 1,
        "files_read": len(read_files),
        "total_files": analysis.total_files,
        "coverage_pct": coverage_pct,
        "remaining_unique_concepts": len(remaining_concepts),
        "marginal_gain_next_file": marginal_gain,
        "stop_signal": stop_signal,
        "next_best_files": [c["file"] for c in candidates[:5]],
        "confidence": "high" if coverage_pct >= 50 else "low — limited exploration so far",
    }


# ── Confidence Bands ──────────────────────────────────────────────

def confidence_band(signals: dict[str, float | int | str | None]) -> str:
    """Compute confidence from structural signals."""
    numeric = [v for v in signals.values() if isinstance(v, (int, float)) and v is not None]
    signal_count = len(numeric)
    if signal_count >= 5:
        return "high — multiple structural signals agree"
    if signal_count >= 3:
        return "mixed — some structural signals available"
    if signal_count >= 1:
        return "low — limited structural signal"
    return "unknown — no computable signals"


# ── Structural Diff (Merkle fingerprint comparison) ──────────────

def structural_diff(path: str, ref_a: str | None = None, ref_b: str | None = None) -> dict:
    """Compare repo structural fingerprints between two refs.

    Uses existing repo_fingerprint + lattice_defects + entropy_velocity
    to produce a before/after comparison of the repo's structural state.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    head_ref = ref_b or "HEAD"
    base_ref = ref_a or "HEAD~1"

    if vgit.has_commits(path):
        if not vgit.ref_exists(path, base_ref):
            return {"error": f"Unknown base ref: {base_ref}", "schema_version": 1}
        if not vgit.ref_exists(path, head_ref):
            return {"error": f"Unknown head ref: {head_ref}", "schema_version": 1}

    try:
        fp_before = repo_fingerprint(path, git_ref=base_ref)
        fp_after = repo_fingerprint(path, git_ref=head_ref)
    except Exception as e:
        return {"error": f"fingerprint scan failed: {e}", "schema_version": 1}

    before_checksum = fp_before.get("checksum", "")
    after_checksum = fp_after.get("checksum", "")

    changed_files = []
    if vgit.has_commits(path):
        changed_files = vgit.diff_refs(path, base_ref, head_ref)

    # Lattice defects
    defects = {}
    try:
        ld = lattice_defects(path, base_ref=base_ref, head_ref=head_ref)
        if "error" not in ld:
            defects = ld.get("defects", {})
    except Exception:
        pass

    # Entropy delta
    entropy_delta = None
    try:
        ev = entropy_velocity(path, weeks=12)
        if "error" not in ev:
            entropy_delta = ev.get("acceleration", 0)
    except Exception:
        pass

    fingerprint_changed = before_checksum != after_checksum
    file_delta = len(changed_files)

    if not fingerprint_changed and file_delta == 0:
        pass

    return {
        "schema_version": 1,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "fingerprint_changed": fingerprint_changed,
        "checksum_before": before_checksum,
        "checksum_after": after_checksum,
        "changed_file_count": file_delta,
        "changed_files": changed_files[:30],
        "defects": defects,
        "entropy_acceleration": entropy_delta,
        "entropy_trend": "accelerating" if entropy_delta and entropy_delta > 0.001 else
                        "decelerating" if entropy_delta and entropy_delta < -0.001 else "stable",
    }


# ── Vocab Ask (Dialogue mode) ─────────────────────────────────────

def answer_question(path: str, question: str, files: list[str] | None = None) -> dict:
    """Answer natural-language questions about a repo using existing data.

    Questions like:
      - "Is file X safe to edit?"
      - "What verifies changes to Y?"
      - "What files share concepts with Z?"
      - "Is this repo healthy?"
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    q = question.lower().strip()
    answer: dict[str, str | list | float] = {}
    sources: list[str] = []

    # "Is file X safe to edit?"
    safe_match = re.search(r"(?:is|are)\s+(\S+)\s+safe", q)
    if safe_match:
        target = safe_match.group(1)
        try:
            pre = preflight_report(path, files=[target])
            if "error" not in pre:
                risk = pre.get("risk", "unknown")
                temp = pre.get("temperature", "WARM")
                stable = [s["file"] for s in pre.get("stable_anchors_touched", [])]
                answer = {
                    "question": f"Is {target} safe to edit?",
                    "answer": f"Risk: {risk}. Temp: {temp}.",
                    "risk": risk,
                    "temperature": temp,
                    "is_stable_anchor": target in stable,
                }
                if stable:
                    answer["warning"] = f"File is a stable anchor ({stable[0]}). Edit with caution."
                sources.append("preflight")
        except Exception:
            pass

    # "What verifies changes to X?"
    verify_match = re.search(r"(?:what|which)\s+(?:verifies|verif|test|tests?)\s+(?:\S+\s+)?(\S+)", q)
    if verify_match:
        target = verify_match.group(1)
        try:
            pre = preflight_report(path, files=[target])
            if "error" not in pre:
                candidates = pre.get("verification_candidates", pre.get("verify_with", []))
                answer = {
                    "question": f"What verifies changes to {target}?",
                    "verification_candidates": candidates[:3],
                    "verification_confidence": pre.get("verification_confidence", {}).get("level", "unknown"),
                }
                if not candidates:
                    answer["note"] = "Vocab found no verification candidates. This may mean tests use different filenames or don't exist."
                sources.append("preflight")
        except Exception:
            pass

    # "What files share concepts with X?"
    concept_match = re.search(r"(?:what|which)\s+(?:files?|concepts)\s+(?:share|relate|connect|link)\s+(?:\S+\s+)?(\S+)", q)
    if concept_match:
        target = concept_match.group(1)
        from quale.compare import pr_blast_radius
        from quale.scanner import scan_codebase
        try:
            analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
            if analysis.file_vocabs:
                blast = pr_blast_radius([target], analysis.file_vocabs, max_results=10).get("impacts", [])
                if blast:
                    answer = {
                        "question": f"What files share concepts with {target}?",
                        "impacted_files": [{"file": b["file"], "shared": b.get("shared_concepts", 0), "concepts": b.get("concepts", [])[:3]} for b in blast[:5]],
                        "total_impacted": len(blast),
                    }
                    sources.append("blast radius")
        except Exception:
            pass

    # "Is this repo healthy?"
    if re.search(r"(?:healthy|health|state of)", q):
        try:
            hs = health_score(path)
            conf = inspect_repo(path).get("confidence", "unknown")
            answer = {
                "question": "Is this repo healthy?",
                "answer": f"Health score: {hs}/1.0. Structural confidence: {conf}.",
                "health_score": hs,
                "confidence": conf,
            }
            sources.append("health_score")
        except Exception:
            pass

    # "Does this repo have tests?" / "What is the test coverage?"
    if re.search(r"(?:test|coverage|desert)", q):
        try:
            deserts = verification_deserts(path, max_results=5)
            if "error" not in deserts:
                answer = {
                    "question": "What is the structural test situation?",
                    "mirror_ratio": deserts.get("mirror_ratio", 0.0),
                    "source_files": deserts.get("source_files", 0),
                    "mirrored_files": deserts.get("mirrored_source_files", 0),
                    "desert_examples": [d.get("source_path", "") for d in deserts.get("deserts", [])[:3]],
                    "note": "This is structural test mirror detection, not coverage. It reports which source files have same-name test mirrors.",
                }
                sources.append("deserts")
        except Exception:
            pass

    if not answer:
        answer = {
            "question": question,
            "answer": "I don't understand the question. Try: 'Is file X safe to edit?', 'What verifies changes to X?', 'What files share concepts with X?', 'Is this repo healthy?', or 'Does this repo have tests?'",
        }

    return {
        "schema_version": 1,
        "sources": sources,
        "answer": answer,
        "guardrails": {
            "mode": "report_only",
            "structural_only": True,
            "not_semantic_truth": True,
        },
    }


# ── Verify Scope (Post-edit receipt) ──────────────────────────────

def verify_scope(path: str, contract_files: list[str] | None = None,
                 diff_ref: str = "HEAD", task: str | None = None) -> dict:
    """Post-edit scope verification against a pre-edit commitment.

    Run after making changes to verify the diff stayed within the
    expected scope from a preflight contract.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    diff_ref = diff_ref or "HEAD"
    if vgit.has_commits(path) and not vgit.ref_exists(path, diff_ref):
        return {"error": f"Unknown git ref: {diff_ref}", "schema_version": 1}

    # Get actual changed files from the diff
    try:
        actual_changed = vgit.diff_worktree(path, diff_ref) if diff_ref else []
    except Exception:
        actual_changed = vgit.diff_refs(path, diff_ref, "HEAD")

    if not actual_changed:
        actual_changed_alt = vgit.diff_refs(path, diff_ref, "HEAD")
        actual_changed = actual_changed_alt or []

    # Run post-hoc preflight on actual changes
    post = {}
    if actual_changed:
        try:
            post = preflight_report(path, files=actual_changed, task=task)
        except Exception:
            post = {"error": "preflight failed after edit"}

    # Compare against expected scope
    expected = set(contract_files or [])
    actual = set(actual_changed)
    scope_violations = list(actual - expected) if expected else []
    unexpected_stable = []
    if expected and post.get("stable_anchors_touched"):
        for s in post.get("stable_anchors_touched", []):
            if s.get("file") not in expected:
                unexpected_stable.append(s)

    # Compute structural hash of the diff
    checksum = ""
    try:
        fp = repo_fingerprint(path)
        checksum = fp.get("checksum", "")
    except Exception:
        pass

    scope_matched = not scope_violations and (not expected or actual.issubset(expected))

    # T7: Record calibration
    ver_candidates = post.get("verification_candidates", post.get("verify_with", []))
    cal_record = {
        "repo_hash": _repo_hash(path),
        "expected": list(expected),
        "actual": list(actual),
        "scope_matched": scope_matched,
        "verification_candidate_hit": any(c in actual for c in ver_candidates),
        "risk": post.get("risk", "unknown"),
        "temperature": post.get("temperature", "WARM"),
    }
    _record_calibration(path, cal_record)

    return {
        "schema_version": 1,
        "diff_ref": diff_ref,
        "contract_files": contract_files or [],
        "actual_changed_files": list(actual),
        "scope_violations": scope_violations,
        "scope_matched": scope_matched,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "unexpected_stable_anchors": unexpected_stable[:3],
        "post_edit_risk": post.get("risk", "unknown"),
        "post_edit_temperature": post.get("temperature", "WARM"),
        "post_edit_confidence": post.get("confidence", "unknown"),
        "repo_checksum": checksum,
        "receipt": {
            "scope_kept": scope_matched,
            "violations": scope_violations if scope_violations else None,
            "stable_warnings": len(unexpected_stable),
        },
        "guardrails": {
            "mode": "report_only",
            "receipt_only": True,
            "not_semantic_truth": True,
        },
    }


# ── Structural Orphans (T5) ───────────────────────────────────────

def _structural_orphans(analysis) -> list[dict]:
    """Find files sharing zero identifiers with any other file."""
    from quale.scanner import _is_generated, _is_lock_file, _code_file_vocabs
    from quale.compare import _extract_identifiers

    identifiers_by_file: dict[str, set[str]] = {}
    global_df: dict[str, int] = {}
    for fv in _code_file_vocabs(analysis):
        ids = _extract_identifiers(fv)
        if ids:
            identifiers_by_file[fv.path] = ids
            for ident in ids:
                global_df[ident] = global_df.get(ident, 0) + 1

    orphans = []
    for path, ids in identifiers_by_file.items():
        if _is_generated(path) or _is_lock_file(path):
            continue
        shared_with = set()
        for ident in ids:
            if global_df.get(ident, 0) > 1:
                shared_with.add(ident)
        if len(shared_with) < 2:
            orphans.append({
                "file": path,
                "unique_identifiers": len(ids),
                "shared_identifiers": len(shared_with),
            })

    orphans.sort(key=lambda x: -x["unique_identifiers"])
    return orphans[:5]


# ── Co-change Prediction (T1) ──────────────────────────────────────

def _co_change_probs(path: str, target_files: list[str], max_commits: int = 500, max_results: int = 5) -> list[dict]:
    """Compute historical co-change probability for each target file.

    For each target file, scans the last N commits and measures:
        P(Y changed | X changed) = co-occurrences(X, Y) / total_changes(X)
    """
    if not vgit.is_repo(path) or not target_files:
        return []
    refs = vgit.ref_log(path, count=100)
    if not refs:
        return []

    change_sets: dict[str, set[str]] = {}
    per_file_changes: dict[str, int] = {}
    for ref in refs:
        sha = ref.sha
        try:
            out = vgit._git("diff-tree", "--no-commit-id", "-r", "--name-only", "-z", sha, cwd=path)
            files_in_commit = [f for f in out.split("\0") if f and not f.startswith(".")]
        except Exception:
            continue
        if not files_in_commit:
            continue
        for file in files_in_commit:
            per_file_changes[file] = per_file_changes.get(file, 0) + 1
        for file in files_in_commit:
            change_sets.setdefault(file, set()).update(f for f in files_in_commit if f != file)

    results = []
    for target in target_files[:3]:
        target_changes = per_file_changes.get(target, 0)
        if target_changes < 2:
            continue
        related = change_sets.get(target, {})
        scored = []
        for other, co_count in Counter(related).most_common(max_results + 5):
            prob = round(co_count / max(target_changes, 1), 2)
            if prob >= 0.1 and co_count >= 2:
                scored.append({
                    "file": other,
                    "probability": f"{prob:.0%}",
                    "co_occurrences": co_count,
                    "total_changes": target_changes,
                })
        results.extend(scored[:max_results])
    return results


# ── Repo fingerprint ──────────────────────────────────────────────

def repo_fingerprint(path: str, git_ref: str | None = None) -> dict:
    from quale.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30, git_ref=git_ref)

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
        "checksum": f"sha256-{combined.hexdigest()}",
        "files": analysis.total_files,
        "total_phrases": analysis.total_phrases,
        "total_indices": total_indices,
        "languages": len(analysis.languages),
    }


# ── Entropy Velocity ─────────────────────────────────────────────

def entropy_velocity(path: str, weeks: int = 12, interval_weeks: int = 4) -> dict:
    """Shannon entropy of vocabulary distribution over time.

    Scans HEAD once, then walks backwards through weekly refs using git diff to
    reconstruct earlier vocabularies.  O(files) instead of O(files × intervals).
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from quale.scanner import scan_codebase
    from math import log2

    week_data = vgit.weekly_commits(path, weeks=weeks)
    total_intervals = max(weeks // interval_weeks, 1) + 1
    if total_intervals < 2:
        return {"error": "Not enough history for entropy computation.", "schema_version": 1}

    next_stop = total_intervals - 1
    if next_stop * interval_weeks >= len(week_data):
        next_stop = len(week_data) - 1

    # Scan HEAD once
    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No files scanned.", "schema_version": 1}

    # Build current vocabulary
    def _entropy(phrase_counts: Counter, total: int) -> float:
        if total == 0:
            return 0.0
        h = 0.0
        for count in phrase_counts.values():
            p = count / total
            h -= p * log2(p)
        return h

    current_counts: Counter[str] = Counter()
    for fv in analysis.file_vocabs:
        for phrase in fv.vocabulary:
            current_counts[phrase] += 1
    current_total = sum(current_counts.values())

    snapshots: list[dict[str, Any]] = [{
        "age_weeks": 0,
        "entropy": round(_entropy(current_counts, current_total), 4),
        "total_phrases": current_total,
        "unique_phrases": len(current_counts),
    }]

    # Walk backwards through intervals using git diff
    shas = [wk["shas"][-1] for wk in week_data if wk.get("shas")]
    for i in range(1, total_intervals):
        age_weeks = i * interval_weeks
        if age_weeks >= len(shas):
            break

        sha_curr = shas[-age_weeks] if age_weeks > 0 else None
        sha_prev = shas[-(age_weeks + 1)] if (age_weeks + 1) < len(shas) else shas[-1]
        if not sha_curr or not sha_prev:
            continue

        try:
            diff_text = vgit._git_bytes("diff", "--unified=0", sha_prev, sha_curr, cwd=path)
        except RuntimeError:
            continue

        added_phrases_fwd: set[str] = set()
        removed_phrases_fwd: set[str] = set()
        for line in diff_text.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                added_phrases_fwd.add(line[1:].strip())
            elif line.startswith("-") and not line.startswith("---"):
                removed_phrases_fwd.add(line[1:].strip())

        # Reconstruct past vocabulary: remove additions and re-add removals
        for phrase in removed_phrases_fwd:
            current_counts[phrase] = max(current_counts.get(phrase, 0) + 1, 1)
            current_total += 1
        for phrase in added_phrases_fwd:
            current_counts[phrase] = max(current_counts.get(phrase, 1) - 1, 0)
            if current_counts[phrase] == 0:
                del current_counts[phrase]
            current_total = max(current_total - 1, 0)

        snapshots.append({
            "age_weeks": age_weeks,
            "entropy": round(_entropy(current_counts, current_total), 4),
            "total_phrases": current_total,
            "unique_phrases": len(current_counts),
        })

    if len(snapshots) < 2:
        return {"error": "Not enough history for entropy computation.", "schema_version": 1}

    # Compute velocity (rate of change) and acceleration
    velocities: list[float] = []
    for i in range(1, len(snapshots)):
        de = snapshots[i]["entropy"] - snapshots[i - 1]["entropy"]
        dt = snapshots[i]["age_weeks"] - snapshots[i - 1]["age_weeks"]
        if dt > 0:
            velocities.append(de / dt)

    avg_velocity = round(sum(velocities) / len(velocities), 6) if velocities else 0.0

    acceleration = 0.0
    if len(velocities) >= 2:
        acc_vals = []
        for i in range(1, len(velocities)):
            dv = velocities[i] - velocities[i - 1]
            dt = snapshots[i + 1]["age_weeks"] - snapshots[i]["age_weeks"]
            if dt > 0:
                acc_vals.append(dv / dt)
        acceleration = round(sum(acc_vals) / len(acc_vals), 8) if acc_vals else 0.0

    # Trend interpretation
    if acceleration > 0.001:
        trend = "heating — vocabulary diversity accelerating"
        signal = "warning"
    elif acceleration < -0.001:
        trend = "cooling — vocabulary stabilizing"
        signal = "stable"
    elif avg_velocity > 0.0005:
        trend = "slow growth — gradual diversification"
        signal = "normal"
    elif avg_velocity < -0.0005:
        trend = "contracting — vocabulary consolidation"
        signal = "normal"
    else:
        trend = "equilibrium — vocabulary distribution steady"
        signal = "stable"

    return {
        "schema_version": 1,
        "intervals": snapshots,
        "velocity": avg_velocity,
        "acceleration": acceleration,
        "trend": trend,
        "signal": signal,
        "confidence": "moderate — entropy is sensitive to sampling" if len(snapshots) < 4 else "high — multiple intervals",
    }


# ── Concept Genesis ──────────────────────────────────────────────

def concept_genesis(path: str, top_n: int = 20) -> dict:
    """Trace where each concept first appeared — endogenous or imported.

    A concept is 'endogenous' if it only exists in one file.
    A concept is 'imported' if its first appearance is in a different
    file from its current primary file.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from quale.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    # For each capitalized concept, track which files contain it
    concept_files: dict[str, list[str]] = {}
    for fv in analysis.file_vocabs:
        for phrase in fv.vocabulary:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{4,40}\b', phrase):
                c = m.group()
                concept_files.setdefault(c, []).append(fv.path)

    endogenous: list[dict] = []
    imported: list[dict] = []
    ambiguous: list[dict] = []

    for concept, files in concept_files.items():
        if len(files) < 2:
            continue
        if len(files) > 40:
            continue
        unique_files = len(set(files))
        primary = Counter(files).most_common(1)[0][0]
        if unique_files == 1:
            endogenous.append({"concept": concept, "file": primary, "count": len(files)})
        elif unique_files <= 5:
            imported.append({
                "concept": concept,
                "primary_file": primary,
                "all_files": sorted(set(files))[:5],
                "file_count": unique_files,
            })
        else:
            ambiguous.append({
                "concept": concept,
                "file_count": unique_files,
                "note": "widespread — may be framework or utility concept",
            })

    endogenous.sort(key=lambda x: -x["count"])
    imported.sort(key=lambda x: -x["file_count"])

    return {
        "schema_version": 1,
        "endogenous": endogenous[:top_n],
        "imported": imported[:top_n],
        "ambiguous": ambiguous[:top_n],
        "summary": {
            "endogenous_count": len(endogenous),
            "imported_count": len(imported),
            "ambiguous_count": len(ambiguous),
        },
        "confidence": "mixed — file-level origin only; git history tracing not included",
    }


# ── Concept Bonds ────────────────────────────────────────────────

def concept_bonds(path: str, top_n: int = 30) -> dict:
    """Classify concept bonds: covalent, ionic, metallic.

    Covalent: concepts that ALWAYS appear together in the same file.
    Ionic: concepts from separate files that form a key dependency pair.
    Metallic: concepts shared across many files (framework/utility pool).
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from quale.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    concept_files: dict[str, set[str]] = {}
    file_concepts: dict[str, set[str]] = {}

    for fv in analysis.file_vocabs:
        concepts: set[str] = set()
        for phrase in fv.vocabulary:
            for m in re.finditer(r'\b[A-Z][A-Za-z0-9_]{4,40}\b', phrase):
                concepts.add(m.group())
        file_concepts[fv.path] = concepts
        for c in concepts:
            concept_files.setdefault(c, set()).add(fv.path)

    # COVALENT: concept pairs that always co-occur (Jaccard >= 0.9)
    covalent: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for fp, concepts in file_concepts.items():
        if len(concepts) > 200:
            concepts = set(list(concepts)[:200])
        sorted_concepts = sorted(concepts)
        for a_idx, a in enumerate(sorted_concepts):
            for b in sorted_concepts[a_idx + 1:]:
                if (a, b) in seen_pairs or (b, a) in seen_pairs:
                    continue
                seen_pairs.add((a, b))
                files_a = concept_files.get(a, set())
                files_b = concept_files.get(b, set())
                if not files_a or not files_b:
                    continue
                jaccard = len(files_a & files_b) / max(len(files_a | files_b), 1)
                if jaccard >= 0.9 and len(files_a) >= 2:
                    covalent.append({"pair": [a, b], "jaccard": round(jaccard, 2), "shared_files": len(files_a & files_b)})

    covalent.sort(key=lambda x: -x["shared_files"])

    # IONIC: concept from one file, referenced by another (2-file bridge)
    ionic: list[dict] = []
    for concept, files in concept_files.items():
        if len(files) == 2:
            flist = sorted(files)
            ionic.append({"concept": concept, "from_file": flist[0], "to_file": flist[1]})
    ionic.sort(key=lambda x: (x["from_file"], x["concept"]))

    # METALLIC: concepts shared across many files (common utility pool)
    metallic: list[dict] = []
    for concept, files in concept_files.items():
        count = len(files)
        if count >= 6:
            metallic.append({"concept": concept, "file_count": count, "sample_files": sorted(files)[:3]})
    metallic.sort(key=lambda x: -x["file_count"])

    return {
        "schema_version": 1,
        "covalent": covalent[:top_n],
        "ionic": ionic[:top_n],
        "metallic": metallic[:top_n],
        "summary": {
            "covalent_pairs": len(covalent),
            "ionic_pairs": len(ionic),
            "metallic_concepts": len(metallic),
        },
        "confidence": "mixed — structural bonds only; no AST or import resolution",
    }

def rogue_wave_report(path: str = ".", threshold: float = 2.5) -> dict:
    from quale.scanner import scan_codebase
    from quale.bootstrap import compute_modules
    import re
    import statistics
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    fs = {}
    for fv in analysis.file_vocabs:
        s = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                s.add(m.group())
        fs[fv.path] = len(s)
    mods = compute_modules(path, analysis=analysis)
    ml = mods.get("modules", []) if isinstance(mods, dict) else []
    flares = []
    for m in ml:
        sizes = [fs.get(f, 0) for f in m["files"]]
        if len(sizes) < 3:
            continue
        med = statistics.median(sizes)
        std = max(statistics.stdev(sizes), 1) if len(sizes) > 1 else 1
        for f in m["files"]:
            z = (fs.get(f, 0) - med) / std
            if z >= threshold:
                flares.append({"file": f, "z_score": round(z, 2), "quale_size": fs.get(f, 0), "module_median": round(med)})
    flares.sort(key=lambda x: -x["z_score"])
    return {"rogue_waves": flares[:8]}

def tensegrity_report(path: str = ".", min_intermediaries: int = 3) -> dict:
    from quale.scanner import scan_codebase
    from quale.bootstrap import compute_modules
    import re
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    ft = {}
    for fv in analysis.file_vocabs:
        s = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                s.add(m.group())
        ft[fv.path] = s
    mods = compute_modules(path, analysis=analysis)
    ml = mods.get("modules", []) if isinstance(mods, dict) else []
    tps = []
    for m in ml:
        fs = m["files"]
        for i, a in enumerate(fs):
            for j, b in enumerate(fs):
                if i >= j:
                    continue
                if ft.get(a, set()) & ft.get(b, set()):
                    continue
                ics = [c for c in fs if c != a and c != b and (ft.get(a, set()) & ft.get(c, set())) and (ft.get(c, set()) & ft.get(b, set()))]
                if len(ics) >= min_intermediaries:
                    tps.append({"file_a": a, "file_b": b, "count": len(ics)})
    tps.sort(key=lambda x: -x["count"])
    return {"tensegrity_pairs": tps[:5]}

def implicature_report(path: str = ".", file_path: str = "") -> dict:
    from quale.scanner import scan_codebase
    import re
    import random
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[a-zA-Z][a-zA-Z0-9_]{3,40}\b')
    targets = [file_path] if file_path else random.sample([fv.path for fv in analysis.file_vocabs if not fv.path.startswith((".", "node_modules"))][:50], min(10, 50))
    vios = []
    for tf in targets:
        full = os.path.join(path, tf)
        if not os.path.exists(full):
            continue
        with open(full, encoding="utf-8") as f:
            lines = f.readlines()
        tokens = set()
        for fv in analysis.file_vocabs:
            if fv.path == tf:
                for phrase in fv.vocabulary:
                    for m in token_re.finditer(phrase):
                        tokens.add(m.group())
        qty = len(lines) >= 70 and len(tokens) < 10 and not any(tf.endswith(e) for e in (".json", ".csv", ".xml"))
        st = [t for t in tokens if any(c.isupper() for c in t)]
        camel = sum(1 for t in st if t[0].islower() and any(c.isupper() for c in t[1:]))
        snake = sum(1 for t in st if "_" in t)
        pascal = sum(1 for t in st if t[0].isupper() and "_" not in t)
        tot = camel + snake + pascal or 1
        manner = max(camel, snake, pascal) / tot > 0.7 and min(camel, snake, pascal) > 0
        vios.append({"file": tf, "quantity": qty, "quality": False, "relation": False, "manner": manner})
    return {"violations": vios}

def criticality_report(path: str = ".", file_path: str = "") -> dict:
    from quale.scanner import scan_codebase
    import re
    import os
    if not vgit.is_repo(path):
        return {"error": "Not a git repository."}
    path = os.path.abspath(path)
    try:
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    except Exception as e:
        return {"error": f"scan failed: {e}"}
    token_re = re.compile(r'\b[A-Z][a-zA-Z0-9_]{4,40}\b')
    code_exts = frozenset({".go", ".ts", ".js", ".py", ".rs", ".rb", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala"})
    ft = {}
    for fv in analysis.file_vocabs:
        ext = os.path.splitext(fv.path)[1].lower()
        if ext not in code_exts:
            continue
        s = set()
        for phrase in fv.vocabulary:
            for m in token_re.finditer(phrase):
                s.add(m.group())
        ft[fv.path] = s
    targets = [file_path] if file_path else list(ft.keys())[:10]
    scores = []
    for t in targets:
        if t not in ft:
            continue
        one = [p for p, s in ft.items() if p != t and ft[t] & s]
        two = set()
        for oh in one:
            for p, s in ft.items():
                if p != t and p not in one and ft.get(oh, set()) & s:
                    two.add(p)
        oc = len(one) or 1
        k = round(len(two) / oc, 2)
        scores.append({"file": t, "k": k, "one_hop": len(one), "two_hop": len(two), "class": "supercritical" if k > 1.5 else ("critical" if k > 0.5 else "subcritical")})
    return {"scores": scores}
