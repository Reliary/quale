"""Reporting commands: ci_report, inspect_repo, repo_fingerprint, stability, lifecycles, timeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict, Counter
from typing import TYPE_CHECKING, Any

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
                     diff_ref: str | None = None, task: str | None = None) -> dict:
    """File-scoped edit/review preflight built from grammar-free signals."""
    from vocab.scanner import scan_codebase, _mirror_signals
    from vocab.compare import pr_blast_radius

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
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
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
            from vocab.bootstrap import bootstrap_repo
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
    sprawl_guard = _edit_sprawl_guard(changed, avoid_expanding, stable_touched, blast)

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
        "edit_sprawl_guard": sprawl_guard,
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
            "note": "vocab preflight scans only local repository files",
        },
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
    if bootstrap:
        for item in bootstrap.get("related_files_for_task", []):
            file = item.get("file")
            if file and item.get("role") == "test" and file not in verify:
                verify.append(file)
    changed_bases = {os.path.splitext(os.path.basename(f))[0].replace(".", "").lower() for f in changed}
    for fv in file_vocabs:
        norm = fv.path.lower()
        if "/test" not in norm and "tests/" not in norm and ".test." not in norm and "_test." not in norm:
            continue
        base = os.path.splitext(os.path.basename(fv.path))[0].replace(".test", "").replace("_test", "").lower()
        if base in changed_bases and fv.path not in verify:
            verify.append(fv.path)
    return verify[:3]


def _explain_verify_candidates(changed: list[str], bootstrap: dict | None, file_vocabs, verify_with: list[str]) -> list[dict[str, str]]:
    """Return per-candidate match reason for each verification file."""
    if not verify_with:
        return []
    changed_bases = {os.path.splitext(os.path.basename(f))[0].replace(".", "").lower() for f in changed}
    changed_dirs = set()
    for f in changed:
        parts = f.split("/")
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
    return details[:3]


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


def _verification_confidence(changed: list[str], verify_with: list[str], mirror: dict | None, file_vocabs) -> dict:
    existing = {fv.path for fv in file_vocabs}
    existing_candidates = [path for path in verify_with if path in existing]
    mirror_ratio = mirror.get("mirror_ratio", 0.0) if mirror else 0.0
    source_count = len(changed)
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


def _edit_sprawl_guard(changed: list[str], avoid_expanding: list[str], stable_touched: list[dict], blast: list[dict]) -> dict:
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

def verification_deserts(path: str, max_results: int = 20) -> dict:
    """Find source files with weak structural verification mirrors.

    This is not test coverage. It only reports places where source files
    lack obvious same-name, nearby, or task-convention test mirrors.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from vocab.scanner import scan_codebase, _is_generated, _is_lock_file, _DEAD_CODE_EXTS
    from vocab.bootstrap import _task_file_role

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
        parts = p.split("/")
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
                or fv.path.startswith((".reliary/", ".vocab-cache/"))
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
    source_parts = source_path.split("/")[:-1]
    source_dir_tokens = {part.lower() for part in source_parts if part}
    for test in test_paths:
        if test in candidates:
            continue
        test_lower = test.lower()
        if stem and stem in _test_stem(test):
            candidates.append(test)
            continue
        overlap = source_dir_tokens & {part.lower() for part in test.split("/")[:-1]}
        if overlap and os.path.basename(source_path).split(".")[0].lower() in test_lower:
            candidates.append(test)
    return list(dict.fromkeys(candidates))[:5]


def _verification_desert_score(source_path: str, candidates: list[str], test_dirs: set[str]) -> float:
    if candidates:
        return 0.0 if len(candidates) >= 2 else 0.25
    parts = source_path.split("/")
    has_nearby_test_dir = any(source_path.startswith(prefix.rsplit("/", 1)[0]) for prefix in test_dirs if "/" in prefix)
    score = 0.75
    if not test_dirs:
        score = 1.0
    elif has_nearby_test_dir:
        score = 0.55
    if any(part in {"examples", "scripts", "docs"} for part in parts):
        score = min(score, 0.45)
    return score


def _verification_desert_reason(score: float, candidates: list[str], test_dirs: set[str], source_path: str) -> str:
    if candidates:
        return "only one obvious verification candidate" if len(candidates) == 1 else "has structural test mirror"
    if not test_dirs:
        return "no test directories detected in scanned files"
    if score >= 0.75:
        return "no same-name or nearby test mirror found"
    return "nearby test directory exists, but no direct source/test mirror found"


# ── Vocab routing policy ─────────────────────────────────────────

def route_recommendation(path: str, task: str | None = None, files: list[str] | None = None) -> dict:
    """Decide whether vocab should be used for this interaction.

    The measured data says task-only bootstrap can hurt strong models, while
    file-scoped preflight can reduce edit sprawl and improve verification.
    This router encodes that policy explicitly.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    normalized_files = _normalize_preflight_files(os.path.abspath(path), files or []) if files else []
    reasons: list[str] = []
    warnings: list[str] = []

    if normalized_files:
        action = "preflight_tool"
        command = ["vocab", "preflight", "--path", path]
        for file in normalized_files[:10]:
            command.extend(["--files", file])
        if task:
            command.extend(["--task", task])
        command.extend(["--format", "tool"])
        reasons.append("file-scoped context is available; measured preflight reduces edit sprawl")
    elif task and _task_is_vague(task):
        action = "no_vocab"
        command = []
        reasons.append("task is vague and file scope is unknown; bootstrap has measured discovery harm on strong models")
        warnings.append("ask for target files or run normal search/grep first")
    elif task:
        action = "crystallography_only"
        command = ["vocab", "skeleton", "--path", path]
        reasons.append("task has no file scope; use only a tiny repo skeleton, not bootstrap guidance")
    else:
        action = "inspect_human"
        command = ["vocab", "inspect", path]
        reasons.append("no task/files provided; use human-oriented orientation")

    deserts = verification_deserts(path, max_results=5)
    if not deserts.get("error"):
        mirror_ratio = deserts.get("mirror_ratio", 0.0)
        if mirror_ratio < 0.25 and _is_weird_language(path):
            warnings.append("verification topology is sparse; treat test suggestions as low confidence")

    return {
        "schema_version": 1,
        "action": action,
        "command": command,
        "reasons": reasons,
        "warnings": warnings,
        "policy": {
            "bootstrap_default": "avoid_for_strong_models",
            "preflight_default": "use_when_files_known",
            "auto_prompt_injection": False,
        },
        "confidence": "high" if normalized_files else "mixed",
    }


def _task_is_vague(task: str) -> bool:
    words = [word for word in re.findall(r"[A-Za-z0-9_]+", task.lower()) if len(word) > 2]
    vague = {"fix", "improve", "update", "change", "refactor", "clean", "reliability", "performance", "bug", "stuff", "thing"}
    if len(words) <= 3:
        return True
    if sum(1 for word in words if word in vague) / max(len(words), 1) >= 0.5:
        return True
    return False


def _is_weird_language(path: str) -> bool:
    """Heuristic: repo is dominated by non-mainstream languages where structural test discovery is weak."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "ls-files"],
            capture_output=True, text=True, timeout=10,
        )
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


# ── Cached scan cache for delta / anomaly detection ───────────────

_CACHE_DIR = ".vocab-cache"

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
    """Path to calibration log under .vocab-cache."""
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
        from vocab.scanner import scan_codebase
        analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
        if not analysis.file_vocabs:
            return 0.5

        total = analysis.total_files
        languages = analysis.languages or {}

        # Language diversity score (mono = healthy? poly = healthy? neutral)
        lang_score = min(len(languages) / 5, 1.0)

        # Generated file penalty
        from vocab.scanner import _is_generated
        gen_count = sum(1 for fv in analysis.file_vocabs if _is_generated(fv.path))
        gen_penalty = 1.0 - min(gen_count / max(total, 1) / 0.5, 1.0)

        # Mirror gap: source/test balance
        from vocab.scanner import _mirror_signals
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
                dead = sum(1 for l in lifecycle_data if l["signal"] == "DEAD")
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
    from vocab.scanner import scan_codebase

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return []

    from collections import Counter
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
        return [{"note": "no cached scan to compare against; run vocab init first"}]

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
        return {"error": "no cached scan; run vocab init first", "schema_version": 1}

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
        from vocab.scanner import _mirror_signals
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
    from vocab.scanner import scan_codebase, _binding_concepts, _is_generated, _is_lock_file
    from vocab.bootstrap import explore_repo, compute_modules

    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    analysis = scan_codebase(path, quiet=True, max_files=2500, max_seconds=30)
    if not analysis.file_vocabs:
        return {"error": "No source files found.", "schema_version": 1}

    explore_data = explore_repo(path, themes=False, analysis=analysis)
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
                "sample_files": [f.split("/")[-1] for f in m.get("files", [])[:3]],
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
        stable_files = [s["file"].split("/")[-1] for s in stable_core[:3]]
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
    from vocab.scanner import scan_codebase, _binding_concepts

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
    from vocab.scanner import scan_codebase
    from vocab.segmenter import segment

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
                other_after = vgit.read_file_at_ref(path, other, head_ref)
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
    from vocab.scanner import scan_codebase

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
    remaining_files = analysis.total_files - len(read_files)

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

    confidence = "high"
    if not fingerprint_changed and file_delta == 0:
        confidence = "none — no structural changes detected"

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
        from vocab.compare import pr_blast_radius
        from vocab.scanner import scan_codebase
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
    from vocab.scanner import _is_generated, _is_lock_file, _code_file_vocabs
    from vocab.compare import _extract_identifiers

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
    from vocab.scanner import scan_codebase

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

    Measures whether the codebase is accelerating toward chaos or
    decelerating toward stability. Returns entropy values at each
    time interval plus the velocity (rate of change) and acceleration.
    """
    if not vgit.is_repo(path):
        return {"error": "Not a git repository.", "schema_version": 1}

    from vocab.scanner import scan_codebase
    from math import log2

    # Collect vocabulary snapshots at each interval
    snapshots: list[dict[str, Any]] = []
    for i in range(weeks // interval_weeks + 1):
        age_weeks = i * interval_weeks
        ref = f"HEAD~{age_weeks * 7}" if age_weeks > 0 else None
        # Validate ref
        if age_weeks > 0:
            try:
                vgit._git("rev-parse", "--verify", "--quiet", f"HEAD~{age_weeks * 7}^{{commit}}", cwd=path)
            except Exception:
                break

        analysis = scan_codebase(path, quiet=True, git_ref=ref, max_files=2500, max_seconds=30)
        if not analysis.file_vocabs:
            break

        phrase_counts: Counter = Counter()
        total_phrases = 0
        for fv in analysis.file_vocabs:
            for phrase in fv.vocabulary:
                phrase_counts[phrase] += 1
                total_phrases += 1

        if total_phrases == 0:
            break

        # Shannon entropy: H = -sum(p_i * log2(p_i))
        entropy = 0.0
        for count in phrase_counts.values():
            p = count / total_phrases
            entropy -= p * log2(p)

        snapshots.append({
            "age_weeks": age_weeks,
            "entropy": round(entropy, 4),
            "total_phrases": total_phrases,
            "unique_phrases": len(phrase_counts),
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

    from vocab.scanner import scan_codebase

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

    from vocab.scanner import scan_codebase
    from itertools import combinations

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
