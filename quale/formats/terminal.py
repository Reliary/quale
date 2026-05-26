"""Terminal output formatter — grammar-free structural output."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if os.name == 'nt':
    os.system('')

if TYPE_CHECKING:
    from quale.scanner import CodebaseAnalysis


def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "\033[36m" + "█" * filled + "\033[0m" + "░" * (width - filled)


def _color(text: str, color: str) -> str:
    codes = {
        "header": "\033[1;36m", "subheader": "\033[1;33m",
        "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
        "cyan": "\033[36m", "gray": "\033[90m", "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{codes.get(color, '')}{text}{codes['reset']}"


def format_terminal(analysis: CodebaseAnalysis) -> str:
    lines = []

    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('quale analyze — ')}{_color(analysis.path, 'bold')}")
    lines.append(gy(f"  {analysis.total_files} files  {analysis.total_phrases} phrases  {analysis.total_unique_phrases} unique  {len(analysis.languages)} langs"))
    lines.append("")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    # ── Languages ──
    lines.append(sh("LANGUAGES:"))
    sorted_langs = sorted(analysis.languages.items(), key=lambda x: -x[1])
    for lang, count in sorted_langs[:10]:
        pct = count / analysis.total_files * 100 if analysis.total_files else 0
        phrases = analysis.phrases_by_language.get(lang, 0)
        bar = _bar(pct, 15)
        line = f"  {bar} {lang:<12} {count:>4} files  {pct:>5.1f}%  {phrases:>6} phrases"
        lines.append(line)
    if analysis.shared_across_languages > 0:
        shared_pct = analysis.shared_across_languages / analysis.total_unique_phrases * 100
        lines.append(f"  {gy('─' * 50)}")
        lines.append(f"  {y(f'{analysis.shared_across_languages} phrases shared across languages ({shared_pct:.1f}%)')}")
    lines.append("")

    # ── Top phrases (raw frequency, no classification) ──
    if analysis.top_phrases:
        import re
        visible = [(p, f) for p, f in analysis.top_phrases if re.search(r'[A-Za-z]{3,}', p)]
        lines.append(sh("TOP PHRASES:"))
        for i, (phrase, freq) in enumerate(visible[:10]):
            pct = freq / analysis.total_phrases * 100 if analysis.total_phrases else 0
            bar = _bar(pct, 10)
            lines.append(f"  {bar} {phrase[:55]:<55} {freq:>6}x")
        lines.append("")

    # ── Co-occurrence Clusters ──
    if analysis.clusters:
        lines.append(sh("PATTERNS:"))
        for i, (label, cluster) in enumerate(zip(analysis.cluster_labels, analysis.clusters)):
            if i >= 10:
                lines.append(gy(f"  … +{len(analysis.clusters) - 10} more patterns"))
                break
            lines.append(f"  {i+1}. {label} {_color(f'[{len(cluster)} phrases]', 'cyan')}")
        lines.append("")

    # ── Structure Clusters ──
    if analysis.structure_clusters:
        lines.append(sh("STRUCTURE CLUSTERS:"))
        for sc in analysis.structure_clusters[:8]:
            lines.append(f"  {sc['label']:<20} [{sc['file_count']:>3} files] {gy(sc['top_files'][0] if sc['top_files'] else '')}")
            if sc['characteristic_phrases']:
                cp = ", ".join(p[:30] for p in sc['characteristic_phrases'][:4])
                lines.append(f"  {'':>22} phrases: {gy(cp)}")
        if len(analysis.structure_clusters) > 8:
            lines.append(gy(f"  … +{len(analysis.structure_clusters) - 8} more groups"))
        grouped_files = sum(sc["file_count"] for sc in analysis.structure_clusters)
        ungrouped = analysis.total_files - grouped_files
        lines.append(f"  {'':>22} {gy(f'{ungrouped} files ungrouped')}")
        lines.append("")

    # ── Landmarks ──
    if analysis.landmarks:
        lines.append(sh("UNIQUE FILES:"))
        for lm in analysis.landmarks[:8]:
            lines.append(f"  {lm['path']}")
            top = lm.get("unique_phrases", [])
            if top:
                lines.append(f"    {gy('only file with:')} {', '.join(p[:35] for p in top[:3])}")
        lines.append("")

    # ── Dead code (heuristic — disclaimer) ──
    if analysis.dead_exports:
        lines.append(sh("POTENTIAL DEAD CODE (heuristic — may include false positives):"))
        for de in analysis.dead_exports[:10]:
            lines.append(f"  {r('?')} {gr(de['phrase'][:45])}  {gy(de['file'])}")
        if len(analysis.dead_exports) > 10:
            lines.append(gy(f"  … +{len(analysis.dead_exports) - 10} more candidates"))
        lines.append("")

    return "\n".join(lines)


def format_quick(analysis: CodebaseAnalysis) -> str:
    """One-glance summary — grammar free."""
    lines = []

    langs = sorted(analysis.languages.items(), key=lambda x: -x[1])[:3]
    lang_str = " ".join(f"{l}({n})" for l, n in langs)

    unique_explanation = ""
    if analysis.landmarks:
        l = analysis.landmarks[0]
        up = l.get("unique_phrases", [])
        if up:
            unique_explanation = f"({up[0][:30]})"
        else:
            unique_explanation = l['path'].replace("\\", "/").split("/")[-1]

    lines.append(_color(f"{'━' * 50}", "cyan"))
    lines.append(f"  {analysis.path}")
    lines.append(f"  {analysis.total_files} files  {analysis.total_phrases} phrases  {len(analysis.languages)} langs")
    lines.append(_color(f"{'━' * 50}", "cyan"))
    lines.append(f"  Top langs:    {lang_str}")
    lines.append(f"  Patterns:     {len(analysis.clusters)} discovered")
    lines.append(f"  Unique:       {len(analysis.landmarks)} files {unique_explanation}")
    lines.append(f"  Dead code:    {len(analysis.dead_exports)} candidates (heuristic)")
    lines.append(_color(f"{'━' * 50}", "cyan"))

    return "\n".join(lines)


def format_json(analysis: CodebaseAnalysis) -> str:
    import json
    data = {
        "path": analysis.path,
        "summary": {
            "total_files": analysis.total_files,
            "total_phrases": analysis.total_phrases,
            "total_unique_phrases": analysis.total_unique_phrases,
            "languages": len(analysis.languages),
        },
        "languages": analysis.languages,
        "phrases_by_language": analysis.phrases_by_language,
        "shared_across_languages": analysis.shared_across_languages,
        "top_phrases": [{"phrase": p, "frequency": f} for p, f in analysis.top_phrases[:50]],
        "patterns": [{"label": l, "size": len(c)} for l, c in zip(analysis.cluster_labels, analysis.clusters)],
        "landmarks": [{"path": lm["path"], "uniqueness": lm["uniqueness"]} for lm in analysis.landmarks[:20]],
        "dead_exports": [{"phrase": de["phrase"], "file": de["file"]} for de in analysis.dead_exports[:30]],
    }
    return json.dumps(data, indent=2)


def format_html(analysis: CodebaseAnalysis) -> str:
    lang_rows = "".join(f"""
    <tr>
      <td>{lang}</td>
      <td><div class="bar" style="width:{count / analysis.total_files * 100:.1f}%"></div></td>
      <td>{count}</td>
      <td>{count / analysis.total_files * 100:.1f}%</td>
      <td>{analysis.phrases_by_language.get(lang, 0)}</td>
    </tr>""" for lang, count in sorted(analysis.languages.items(), key=lambda x: -x[1]))

    phrase_rows = "".join(f"<tr><td>{p}</td><td>{f}</td></tr>" for p, f in analysis.top_phrases[:30])

    cluster_rows = "".join(f"<tr><td>{i+1}</td><td>{l}</td><td>{len(c)}</td></tr>"
                           for i, (l, c) in enumerate(zip(analysis.cluster_labels, analysis.clusters)))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>quale — {analysis.path}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
  h1 {{ color: #00d4ff; }} h2 {{ color: #ffd700; margin-top: 30px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ padding: 8px 12px; border: 1px solid #333; }}
  th {{ background: #16213e; color: #00d4ff; }}
  tr:nth-child(even) {{ background: #16213e; }}
  .bar {{ background: #00d4ff; height: 18px; border-radius: 3px; }}
  .summary {{ background: #16213e; padding: 16px; border-radius: 8px; margin-bottom: 20px; }}
</style>
</head>
<body>
<h1>quale analyze — {analysis.path}</h1>
<div class="summary">
  <strong>{analysis.total_files}</strong> files &middot;
  <strong>{analysis.total_phrases}</strong> phrases &middot;
  <strong>{len(analysis.languages)}</strong> languages
</div>

<h2>Languages</h2>
<table><tr><th>Language</th><th>%</th><th>Files</th><th>%</th><th>Phrases</th></tr>{lang_rows}</table>

<h2>Top Phrases</h2>
<table><tr><th>Phrase</th><th>Frequency</th></tr>{phrase_rows}</table>

<h2>Patterns</h2>
<table><tr><th>#</th><th>Pattern</th><th>Files</th></tr>{cluster_rows}</table>
</body>
</html>"""


def format_lifecycles(lifecycles: list[dict], weeks: int, show_all: bool = False) -> str:
    lines = []
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('CONCEPT LIFECYCLES')} (last {weeks} weeks)")
    lines.append(h(f"{'━' * 60}"))

    signal_order = ["DEAD", "ABANDONED", "DECAYING", "SEASONAL", "RENAMED",
                    "GROWING", "EMERGING", "SPORADIC", "RENAMED_TO", "ACTIVE", "STABLE"]
    signal_labels = {
        "DEAD": "DEAD (gone ≥8 weeks, ≤3 appearances)",
        "ABANDONED": "ABANDONED (brief experiment, 2-4 weeks)",
        "DECAYING": "DECAYING (absent ≥4 weeks, aged ≥12)",
        "SEASONAL": "SEASONAL (disappeared then reappeared)",
        "RENAMED": "RENAMED (this concept was replaced)",
        "GROWING": "GROWING (≤4 weeks old, appearing consistently)",
        "EMERGING": "EMERGING (first seen this week)",
        "SPORADIC": "SPORADIC (comes and goes, <30% appearance)",
        "RENAMED_TO": "RENAMED → (this replaced another concept)",
        "ACTIVE": "ACTIVE (in use, not yet mature)",
        "STABLE": f"STABLE ({sum(1 for l in lifecycles if l['signal'] == 'STABLE')} mature concepts — not listed)",
    }

    for signal in signal_order:
        items = [l for l in lifecycles if l["signal"] == signal]
        if not items:
            continue
        if signal == "STABLE":
            continue
        if not show_all and signal in ("GROWING", "ACTIVE", "SPORADIC", "RENAMED_TO"):
            continue
        lines.append("")
        lines.append(sh(f"{signal_labels.get(signal, signal)}:"))
        for item in items[:15]:
            tag = {"DEAD": r("DEAD"), "DECAYING": y("DECAY"), "GROWING": gr("GROW"),
                   "EMERGING": gr("NEW"), "ABANDONED": y("ABAN")}.get(signal, "")
            concept_str = item["concept"][:40]
            detail = gy(f"[age:{item['age_weeks']}w stale:{item['stale_weeks']}w ratio:{item['appearance_ratio']:.0%}]")
            notes = ""
            if "renamed_to" in item:
                notes = gr(f" → {item['renamed_to'][:25]}")
            elif "renamed_from" in item:
                notes = gy(f" ← {item['renamed_from'][:25]}")
            lines.append(f"  {tag} {concept_str:<42} {detail}{notes}")
        if len(items) > 15:
            lines.append(gy(f"  … +{len(items) - 15} more"))

    lines.append("")
    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def format_blast_radius(pr_files: list[str], results: dict, ref_a: str, ref_b: str) -> str:
    """Flat ranked list — no tiered risk labels."""
    lines = []
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('PR BLAST RADIUS')} {ref_a} → {ref_b}")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")
    lines.append(sh("Changed files:"))
    for f in pr_files[:10]:
        lines.append(f"  {gr('+')} {f}")
    if len(pr_files) > 10:
        lines.append(gy(f"  … +{len(pr_files) - 10} more"))
    lines.append("")

    impacts = results.get("impacts", [])
    if not impacts:
        lines.append(gy("  No measurable blast radius (0 shared identifiers with unchanged files)"))
        lines.append("")
        lines.append(h(f"{'━' * 60}"))
        return "\n".join(lines)

    lines.append(sh("Files sharing identifiers with changed code:"))
    for item in impacts[:20]:
        conc_bar = _bar(min(item["shared_concepts"] * 5, 100), 8)
        concepts = ", ".join(item["concepts"][:4])
        shared_text = str(item["shared_concepts"]) + " shared"
        lines.append(f"  {conc_bar} {item['file'][:55]}  {y(shared_text)} {gy(concepts)}")

    if len(impacts) > 20:
        lines.append(gy(f"  … +{len(impacts) - 20} more files"))
    lines.append("")
    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def format_blast_json(pr_files: list[str], results: dict, ref_a: str, ref_b: str) -> str:
    import json
    return json.dumps({
        "ref_a": ref_a,
        "ref_b": ref_b,
        "changed_files": pr_files,
        "impacts": results.get("impacts", []),
    }, indent=2)


def format_lifecycles_json(data: list[dict], weeks: int) -> str:
    import json
    return json.dumps({
        "weeks": weeks,
        "total_concepts": len(data),
        "signals": {
            signal: [d for d in data if d["signal"] == signal]
            for signal in set(d["signal"] for d in data)
        },
    }, indent=2)


def format_orphans_json(analysis: CodebaseAnalysis) -> str:
    import json
    return json.dumps({
        "dead_exports": analysis.dead_exports[:50],
        "note": "Heuristic scan — may include false positives. Review before acting."
    }, indent=2)


def format_search_json(results: list[dict]) -> str:
    import json
    return json.dumps(results, indent=2)


def format_search_compact(results: list[dict]) -> str:
    lines = []
    for r in results:
        for f in r["files"][:5]:
            lines.append(f"{r['repo']}:{f['file']} ({f['language']})")
    return "\n".join(lines)


def format_modules(modules_data: dict) -> str:
    """Terminal output for TDA module detection."""
    lines = []
    h = lambda t: _color(t, "header")
    lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    modules = modules_data.get("modules", [])
    total = modules_data.get("total_files", 0)
    grouped = modules_data.get("grouped_files", 0)
    ungrouped = total - grouped

    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('MODULES')} — {len(modules)} found ({grouped}/{total} files grouped)")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    if not modules:
        lines.append(gy("  No persistent modules found. Try a larger or more cohesive codebase."))
        lines.append("")
        lines.append(h(f"{'━' * 60}"))
        return "\n".join(lines)

    for i, m in enumerate(modules, 1):
        pr = m.get("persistence_range", [1, 3])
        bar = _bar((pr[1] - pr[0] + 1) * 10, 10)
        lines.append(f"  {gr(f'Module {i}:')} {m['size']} files  {bar}  thr {pr[0]}→{pr[1]}")
        for f in m["files"][:6]:
            lines.append(f"    {gy(f)}")
        if len(m["files"]) > 6:
            lines.append(gy(f"    … +{len(m['files']) - 6} more"))
        if m.get("exemplar_phrases"):
            ep = ", ".join(p[:30] for p in m["exemplar_phrases"][:5])
            lines.append(f"    {y('phrases:')} {gy(ep)}")
        lines.append("")

    lines.append(gy(f"  {ungrouped} files ungrouped (no persistent module boundary detected)"))
    lines.append("")
    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def format_modules_json(modules_data: dict) -> str:
    import json
    return json.dumps({
        "schema_version": modules_data.get("schema_version", 1),
        "modules": modules_data.get("modules", []),
        "summary": {
            "module_count": len(modules_data.get("modules", [])),
            "total_files": modules_data.get("total_files", 0),
            "grouped_files": modules_data.get("grouped_files", 0),
        },
    }, indent=2)


def _why_verify_packet(data: dict) -> str:
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines = []
    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('VERIFICATION PACKET — Why these candidates?')}")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    tier = data.get("tier", "unknown")
    if tier == "deterministic":
        det = data.get("deterministic_verify", {})
        lines.append(f"  {gr('✓ Deterministic')} — only one structurally valid target")
        lines.append(f"    {det.get('file', '?')}  ({det.get('rule', 'stem match')}, score={det.get('score', 0):.2f})")
    elif tier == "desert":
        lines.append(f"  {y('Desert')} — no structural verification candidates")
        note = data.get("desert_note", "")
        if note:
            lines.append(f"    {gy(note)}")
        lines.append(f"    {gy('No test file mirrors this change — inspect manually.')}")
    elif tier == "confident":
        lines.append(f"  {sh('How each candidate was found:')}")
        vocab_candidates = data.get("verification_candidates", [])
        for c in vocab_candidates[:3]:
            lines.append(f"    {gr('→')} {c}  {gy('— shares vocabulary with changed file')}")
        for e in data.get("entangled_candidates", [])[:2]:
            reason = e.get('reason', '')
            lines.append(f"    {y('↗')} {e['file']}  {gy(f'- {reason}' if reason else '- git co-change history')}")
        if data.get("deterministic_verify"):
            det = data["deterministic_verify"]
            rule = det.get("rule", "stem match")
            lines.append(f"    {gr('✓')} {det['file']}  {gy('- ' + rule)}")
    elif tier == "ambiguous":
        lines.append(f"  {sh('Weak signal — why candidates are uncertain:')}")
        lines.append(f"    {gy('No strong vocabulary match or co-change history.')}")
        lines.append(f"    {gy('Candidates below are plausible but structurally ambiguous — verify manually.')}")
        vocab_candidates = data.get("verification_candidates", [])
        for c in vocab_candidates[:3]:
            lines.append(f"    {y('?')} {c}")

    lines.append("")
    if data.get("negative_scope"):
        lines.append(f"  {r('Why avoid these:')}")
        for f in data.get("negative_scope", [])[:3]:
            lines.append(f"    {r('✗')} {f}")
    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def _why_edit_context(data: dict) -> str:
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines = []
    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('EDIT CONTEXT — Why each recommendation?')}")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    risk = data.get("risk", "unknown")
    reasons = data.get("reasons", [])
    lines.append(f"  {sh('Risk:')} {risk}")
    for r_text in reasons:
        lines.append(f"    {gy('•')} {r_text}")
    lines.append("")

    reads = data.get("read_first", [])
    if reads:
        lines.append(f"  {gr('Read these first:')}  {gy('— needed for edit context')}")
        for f in reads[:3]:
            lines.append(f"    {gr('→')} {f}")

    blast = data.get("reverse_blast", [])
    if blast:
        lines.append(f"  {y('Hidden dependencies (blast radius):')}  {gy('— your change shares identifiers with these files')}")
        for item in blast[:3]:
            concepts = ", ".join(item.get("concepts", [])[:3])
            lines.append(f"    {y('↗')} {item.get('file', '')}  ({item.get('shared_concepts', 0)} identifiers: {concepts})")

    stable = data.get("stable_anchors_touched", [])
    if stable:
        lines.append(f"  {r('Stable anchors:')}  {gy('— files unchanged for months, high persistence')}")
        for item in stable[:2]:
            lines.append(f"    {r('!')} {item.get('file', '')}  ({item.get('persistence', 0):.0%} stable)")

    expansion = data.get("expansion_risk", data.get("avoid_expanding_into", []))
    if expansion:
        lines.append(f"  {y('Expansion risk:')}  {gy('— avoid these files unless task expands')}")
        for f in expansion[:3]:
            lines.append(f"    {y('✗')} {f}")

    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def _why_diff(data: dict, ref_a: str, ref_b: str) -> str:
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines = []
    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('STRUCTURAL DIFF')} — {ref_a} → {ref_b}")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    changed = data.get("changed_files", [])
    lines.append(f"  {sh('Changed:')} {len(changed)} files")
    for f in changed[:5]:
        lines.append(f"    {gr('+')} {f}")
    lines.append("")

    impact = data.get("impacts", [])
    if impact:
        lines.append(f"  {sh('Why this matters:')}")
        lines.append(f"    {y(str(len(impact)))}  {gy('files share identifiers with changed code — risk of indirect breakage')}")
        for item in impact[:3]:
            lines.append(f"    {y('↗')} {item.get('file', '')}  ({item.get('shared_concepts', 0)} shared identifiers)")

    mirror = data.get("mirror_ratio", None)
    if mirror is not None:
        pct = mirror * 100
        if pct < 30:
            lines.append(f"    {r('Test mirror weak:')} {gy(f'{pct:.0f}% of new concepts have test coverage')}")
        elif pct < 70:
            lines.append(f"    {y('Test mirror partial:')} {gy(f'{pct:.0f}% of new concepts have test coverage')}")
        else:
            lines.append(f"    {gr('Test mirror strong:')} {gy(f'{pct:.0f}% coverage')}")

    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def _why_inspect(data: dict) -> str:
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines = []
    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('REVIEW — Why this matters')}")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    explore = data.get("explore", {})
    files = explore.get("files", [])
    if files:
        lines.append(f"  {sh('Read these first:')}  {gy('— each is distinctive in its identifiers')}")
        for f in files[:3]:
            tag = f" (unique: {f.get('unique_score', 0)} distinctive identifiers)" if f.get('unique_score', 0) > 0 else ""
            lines.append(f"    {gr('→')} {f['file']}{tag}")

    binding = data.get("binding_concepts", [])
    if binding:
        lines.append(f"  {sh('Why these concepts:')}  {gy('— identifiers that bind many source files')}")
        for bc in binding[:3]:
            langs = ",".join(bc.get("languages", [])[:2])
            lines.append(f"    {bc['concept']}  {gy(str(bc['file_count']) + ' files (' + langs + ')')}")

    debt = data.get("debt_candidates", [])
    if debt:
        lines.append(f"  {y('Debt candidates:')}  {gy('— files with low structural uniqueness')}")
        for d in debt[:3]:
            lines.append(f"    {y(str(d['debt']))}  {d['file']}")

    health = data.get("health_score")
    if health is not None:
        val = "Well-structured" if health >= 0.7 else ("Moderate health" if health >= 0.4 else "Weak health")
        col = "green" if health >= 0.7 else ("yellow" if health >= 0.4 else "red")
        lines.append(f"  {gr('Health:')} {h(col)}{health:.2f} — {val}")

    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def _why_ci_report(data: dict, ref_a: str, ref_b: str) -> str:
    h = lambda t: _color(t, "header")
    lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines = []
    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('CI REPORT — Why this result')}")
    lines.append(h(f"{'━' * 60}"))
    lines.append("")

    mirror = data.get("mirror_ratio", None)
    stable = data.get("stable_touched_count", 0)
    blast = data.get("blast_tier", "none")

    if mirror is not None:
        pct = mirror * 100
        if pct < 30:
            lines.append(f"  {r('✗ Mirror gap:')} {pct:.0f}%  {gy('— most new concepts lack test coverage')}")
        elif pct < 70:
            lines.append(f"  {y('Mirror gap:')} {pct:.0f}%  {gy('— some new concepts lack test coverage')}")
        else:
            lines.append(f"  {gr('✓ Mirror:')} {pct:.0f}%  {gy('— adequate test coverage of new concepts')}")

    if stable > 0:
        lines.append(f"  {r('! Stable anchors:')} {stable}  {gy('— files with high persistence were touched')}")
    else:
        lines.append(f"  {gr('✓ Stable anchors:')} none touched")

    blast_label = {"local": gy("local (no risk)"), "moderate": y("moderate"), "high": r("high"), "critical": r("critical")}.get(blast, gy(str(blast)))
    co = data.get("co_change", {}).get("count", None)
    if blast == "local" or blast == "none":
        lines.append(f"  {gr('✓ Blast:')} local  {gy('— no risk of indirect breakage')}")
    else:
        lines.append(f"  {r('Blast:')} {blast_label}  {gy('— change affects files sharing identifiers')}")
        if co:
            lines.append(f"    {co}  {gy('files have co-change history with this change')}")

    total_defects = sum(1 for v in data.values() if isinstance(v, dict) and v.get("severity") in ("low", "moderate", "high"))
    if total_defects:
        lines.append(f"  {y('Defects:')} {total_defects}  {gy('— see details above for severity and type')}")

    changed = data.get("changed_files", [])
    if changed:
        total = len(changed)
        lines.append(f"  {gy(f'{total} files changed — ')}")
        if total <= 5:
            lines.append(gy('small change set, low structural risk'))
        elif total <= 20:
            lines.append(gy('moderate change set, review blast radius'))
        else:
            lines.append(gy('large change set, high structural risk'))

    lines.append(h(f"{'━' * 60}"))
    return "\n".join(lines)


def format_pr_report_markdown(pr_files: list[str], blast_results: dict | None,
                               orphan_results: list[dict] | None,
                               ref_a: str, ref_b: str,
                               pattern_data: dict | None = None) -> str:
    lines = []
    lines.append(f"## PR Structural Report: `{ref_a}` → `{ref_b}`")
    lines.append("")
    lines.append(f"**{len(pr_files)} files changed**")
    lines.append("")
    if blast_results:
        impacts = blast_results.get("impacts", [])
        lines.append("### Blast Radius")
        total = len(impacts)
        top_share = impacts[0]["shared_concepts"] if impacts else 0
        lines.append(f"- {total} unchanged files share identifiers with changed code (top: {top_share} shared)")
        if impacts:
            lines.append(f"- Most impacted: `{impacts[0]['file']}` ({impacts[0]['shared_concepts']} shared identifiers)")
    if orphan_results:
        lines.append("")
        lines.append("### Orphans (heuristic)")
        lines.append(f"- {len(orphan_results)} single-file exported identifiers — may indicate dead code")
        for o in orphan_results[:5]:
            lines.append(f"  - `{o['phrase']}` in `{o['file']}`")
    if pattern_data and not pattern_data.get("error"):
        patterns = pattern_data.get("patterns", [])
        if patterns:
            counts: dict[str, int] = {}
            for item in patterns:
                counts[item.get("type", "unknown")] = counts.get(item.get("type", "unknown"), 0) + 1
            lines.append("")
            lines.append("### Refactoring Pattern Hints")
            lines.append("- " + ", ".join(f"`{kind}`: {count}" for kind, count in sorted(counts.items())))
            lines.append(f"- Confidence: {pattern_data.get('confidence', 'mixed')}")
            lines.append("- Advisory only: confirm with the diff before acting.")
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `quale` — grammar-free structural analysis_")
    return "\n".join(lines)
