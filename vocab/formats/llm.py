"""LLM-compact formatters — token-efficient, structure-first, prose-free."""

from __future__ import annotations

from typing import Any

CAPABILITY_FOOTER = (
    "Vocab sees structure, not semantics. "
    "Cannot verify correctness, detect logic errors, or guarantee test quality. "
    "Trust high-confidence signals more than low-confidence ones."
)


def format_preflight_llm(data: dict) -> str:
    """Compact 1-line-per-signal format for preflight data."""
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

    envelope = data.get("safety_envelope", {})
    boundary = envelope.get("at_boundary", [])
    if boundary:
        lines.append(f"  BOUNDARY:{len(boundary)} files {', '.join(boundary[:3])}")

    reads = data.get("read_first", [])
    if reads:
        lines.append(f"  READ:{', '.join(reads[:3])}")

    ver = data.get("verification_confidence", {})
    ver_level = ver.get("level", "unknown")
    candidates = data.get("verification_candidates", data.get("verify_with", []))
    if candidates:
        lines.append(f"  VERIFY:{', '.join(candidates[:3])} conf:{ver_level}")
    else:
        lines.append(f"  VERIFY:none conf:{ver_level}")

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

    do_not_touch = data.get("expansion_risk", data.get("avoid_expanding_into", []))
    if do_not_touch:
        lines.append(f"  DNT:{', '.join(do_not_touch[:3])}")

    lines.append(f"  {CAPABILITY_FOOTER}")
    return "\n".join(lines)


def format_bootstrap_llm(data: dict) -> str:
    """Compact format for agent-bootstrap output."""
    lines: list[str] = []

    task = data.get("task", "")
    relevance = data.get("task_relevance_score", "?")
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
        lines.append(f"  BIND:{', '.join(b[:2])}")

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
