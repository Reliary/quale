"""LLM-compact formatters — token-efficient, structure-first, prose-free."""

from __future__ import annotations

from typing import Any

CAPABILITY_FOOTER = (
    "Vocab sees structure, not semantics. "
    "Cannot verify correctness, detect logic errors, or guarantee test quality. "
    "Trust high-confidence signals more than low-confidence ones."
)


def format_preflight_llm(data: dict) -> str:
    """Compact 1-line-per-signal format for preflight data. Steps ordered as recommendation path (T4)."""
    lines: list[str] = []

    risk = data.get("risk", "unknown")
    confidence = data.get("confidence", "unknown")
    temp = data.get("temperature", "WARM")
    peer = data.get("peer_relative_risk", {})
    peer_text = peer.get("peer_text", "")

    changed = data.get("changed_files", [])
    changed_str = ", ".join(changed[:3])
    if len(changed) > 3:
        changed_str += f" +{len(changed)-3}"

    lines.append(f"EDIT:{changed_str} RISK:{risk} CONF:{confidence} TEMP:{temp}")
    if peer_text:
        lines.append(f"  SCOPE:{peer_text}")

    # T4: Edit path recommendation — numbered steps
    reads = data.get("read_first", [])
    candidates = data.get("verification_candidates", data.get("verify_with", []))
    details = data.get("verification_details", [])
    do_not_touch = data.get("expansion_risk", data.get("avoid_expanding_into", []))

    step_reads = [f for f in reads if f not in changed]
    path_lines = []
    if step_reads:
        path_lines.append(f"  1) READ {', '.join(step_reads[:2])}")
    path_lines.append(f"  2) EDIT {changed_str}")
    if candidates:
        cand_strs = []
        for c in candidates[:3]:
            detail = next((d for d in details if d.get("path") == c), None)
            tag = f" ({detail['reason']})" if detail else ""
            cand_strs.append(f"{c}{tag}")
        path_lines.append(f"  3) VERIFY {', '.join(cand_strs)}")
    else:
        path_lines.append(f"  3) VERIFY none — inspect manually")
    if do_not_touch:
        path_lines.append(f"  \u26a0 DNT {', '.join(do_not_touch[:3])}")
    if len(path_lines) >= 2:
        lines.append("  PATH:" + "".join(path_lines))
    else:
        # fallback to flat format
        if reads:
            lines.append(f"  READ:{', '.join(reads[:3])}")
        if candidates:
            cand_strs = []
            for c in candidates[:3]:
                detail = next((d for d in details if d.get("path") == c), None)
                tag = f" ({detail['reason']})" if detail else ""
                cand_strs.append(f"{c}{tag}")
            lines.append(f"  VERIFY:{', '.join(cand_strs)} conf:{data.get('verification_confidence', {}).get('level', 'unknown')}")
        else:
            lines.append(f"  VERIFY:none conf:{data.get('verification_confidence', {}).get('level', 'unknown')}")
    if do_not_touch and not path_lines:
        lines.append(f"  DNT:{', '.join(do_not_touch[:3])}")

    envelope = data.get("safety_envelope", {})
    boundary = envelope.get("at_boundary", [])
    if boundary:
        lines.append(f"  BOUNDARY:{len(boundary)} files {', '.join(boundary[:3])}")

    stable = data.get("stable_anchors_touched", [])
    if stable:
        lines.append(f"  STABLE:{', '.join(s['file'] for s in stable[:3])}")

    reasons = data.get("reasons", [])
    if reasons:
        lines.append(f"  WHY:{'; '.join(reasons[:2])}")

    snr = data.get("snr_annotations", {})
    if snr:
        noise_items = [f"{k}:{v.get('type','?')}" for k, v in snr.items()]
        lines.append(f"  SNR:{'; '.join(noise_items)}")

    # T5: Structural orphans
    orphans = data.get("structural_orphans", [])
    if orphans:
        lines.append(f"  ORPHANS:{len(orphans)} isolated ({', '.join(o['file'] for o in orphans[:2])})")

    # T1: Co-change
    co_change = data.get("co_change", [])
    if co_change:
        cc = "; ".join(f"{c['file']}({c['probability']})" for c in co_change[:3])
        lines.append(f"  CO-CHANGE:{cc}")

    lines.append(f"  {CAPABILITY_FOOTER}")
    return "\n".join(lines)


_SESSION_SKIP_FILE = None


def format_bootstrap_llm(data: dict) -> str:
    """Compact format for agent-bootstrap output."""
    lines: list[str] = []

    task = data.get("task", "")
    total = data.get("total_code_files", 0)
    relevance = data.get("task_relevance_score", "?")

    # T3: Exploration probe — skip gate for large repos without task
    if not task and total > 100:
        lines.append(f"VOCAB:{total} files. Reply SKIP to suppress guidance.")
        lines.append(f"  {CAPABILITY_FOOTER}")
        return "\n".join(lines)

    # T2: Negative file set
    low_rel = data.get("low_relevance_files", [])
    if low_rel:
        lines.append(f"SKIP:{len(low_rel)} files share 0 concepts — safe to ignore ({', '.join(low_rel[:3])})")

    lines.append(f"TASK:{task} RELEVANCE:{relevance}")

    task_plan = data.get("task_plan", data.get("task_plan", {}))
    if isinstance(task_plan, dict):
        edit = task_plan.get("likely_edit", [])
        if edit:
            lines.append(f"  EDIT:{', '.join(edit[:3])}")
        reads = task_plan.get("read_first", [])
        if reads:
            lines.append(f"  READ:{', '.join(reads[:3])}")
        verify = task_plan.get("verify_with", [])
        if verify:
            lines.append(f"  VERIFY:{', '.join(verify[:3])}")
        plan_reads = data.get("recommended_next_reads", [])
        if plan_reads:
            pf = [r.get("file", "") for r in plan_reads[:3] if r.get("file")]
            if pf:
                lines.append(f"  READ_MORE:{', '.join(pf)}")

    relate = data.get("related_files_for_task", [])
    sources = [r.get("file", "") for r in relate if r.get("role") == "source"]
    tests = [r.get("file", "") for r in relate if r.get("role") == "test"]
    if sources:
        lines.append(f"  REL_SRC:{', '.join(sources[:3])}")
    if tests:
        lines.append(f"  REL_TEST:{', '.join(tests[:3])}")

    stable = data.get("avoid_touching_without_context", [])
    if stable:
        files = [s.get("file", "") for s in stable[:3] if s.get("file")]
        if files:
            lines.append(f"  DNT:{', '.join(files)}")

    binding = data.get("binding_concepts", [])
    if binding:
        bind_strs = [b.get("concept", str(b))[:20] for b in binding[:2] if isinstance(b, dict)]
        if bind_strs:
            lines.append(f"  BIND:{', '.join(bind_strs)}")

    lines.append(f"  {CAPABILITY_FOOTER}")
    return "\n".join(lines)


def format_contract_llm(contract: dict) -> str:
    """Compact contract format for commitment protocol."""
    lines: list[str] = []
    lines.append(f"CONTRACT:{contract.get('id','?')}")
    lines.append(f"  FILES:{', '.join(contract.get('files',[]))}")
    lines.append(f"  RISK:{contract.get('risk','?')} CONF:{contract.get('confidence','?')}")
    lines.append(f"  SCOPE_HASH:{contract.get('scope_hash','?')}")
    if contract.get("verification_candidates"):
        lines.append(f"  VERIFY:{', '.join(contract['verification_candidates'][:3])}")
    boundary = contract.get("at_boundary", [])
    if boundary:
        lines.append(f"  BOUNDARY:{' '.join(boundary[:3])}")
    return "\n".join(lines)
