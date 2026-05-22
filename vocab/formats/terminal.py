"""Terminal output formatter — concept-driven output."""

from __future__ import annotations

from vocab.scanner import CodebaseAnalysis
from vocab.concepts import ConceptGroup


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


def _icon_for_category(cat: str) -> str:
    icons = {
        "exported": "🔷", "identifier": "🔹", "error": "❌", "api": "🔗",
        "config": "⚙️", "db": "🗄️", "import_path": "📦",
        "syntax": "　", "other": "📄",
    }
    return icons.get(cat, "📄")


def format_terminal(analysis: CodebaseAnalysis) -> str:
    g = analysis.concept_groups
    lines = []

    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
    y = lambda t: _color(t, "yellow")
    gy = lambda t: _color(t, "gray")

    lines.append(h(f"{'━' * 60}"))
    lines.append(f"  {h('vocab analyze — ')}{_color(analysis.path, 'bold')}")
    lines.append(gy(f"  {analysis.total_files} files  {analysis.total_phrases} phrases  {analysis.total_unique_phrases} unique  {len(analysis.languages)} langs"))
    lines.append(gy(""))
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

    # ── Key Concepts (grouped, filtered) ──
    lines.append(sh("KEY CONCEPTS:"))
    shown_groups = 0

    # Exported types/functions first (most meaningful)
    if g.exported:
        sample = ", ".join(p[:30] for p, _ in g.exported[:5])
        lines.append(f"  {_icon_for_category('exported')} Types/Exports:  {gr(sample)}")
        shown_groups += 1

    # Identifiers
    if g.identifier:
        sample = ", ".join(p[:30] for p, _ in g.identifier[:5])
        lines.append(f"  {_icon_for_category('identifier')} Idents:         {sample}")
        shown_groups += 1

    # Errors
    if g.error:
        sample = ", ".join(p[:35] for p, _ in g.error[:4])
        lines.append(f"  {_icon_for_category('error')} Errors:         {r(sample)}")
        shown_groups += 1

    # API
    if g.api:
        sample = ", ".join(p[:35] for p, _ in g.api[:4])
        lines.append(f"  {_icon_for_category('api')} API:            {sample}")
        shown_groups += 1

    # Config
    if g.config:
        sample = ", ".join(p[:30] for p, _ in g.config[:4])
        lines.append(f"  {_icon_for_category('config')} Config:         {sample}")
        shown_groups += 1

    # DB
    if g.db:
        sample = ", ".join(p[:30] for p, _ in g.db[:4])
        lines.append(f"  {_icon_for_category('db')} DB:             {sample}")
        shown_groups += 1

    # Import paths
    if g.import_path:
        sample = ", ".join(p[:35] for p, _ in g.import_path[:3])
        lines.append(f"  {_icon_for_category('import_path')} Imports:        {sample}")
        shown_groups += 1

    if not shown_groups:
        # Fallback: show top frequency
        sample = ", ".join(p[:40] for p, _ in analysis.top_phrases[:5])
        lines.append(f"  Top: {sample}")
    lines.append("")

    # ── Co-occurrence Clusters ──
    if analysis.clusters:
        lines.append(sh("DISCOVERED PATTERNS:"))
        for i, (label, cluster) in enumerate(zip(analysis.cluster_labels, analysis.clusters)):
            if i >= 10:
                lines.append(gy(f"  … +{len(analysis.clusters) - 10} more patterns"))
                break
            size = _color(f"[{len(cluster)} phrases]", "cyan")
            lines.append(f"  {i+1}. {label} {size}")
        lines.append("")

    # ── Landmarks ──
    if analysis.landmarks:
        lines.append(sh("WHAT MAKES THIS CODEBASE UNIQUE:"))
        for lm in analysis.landmarks[:8]:
            lines.append(f"  {lm['path']}")
            # Show what makes it unique
            top = lm.get("unique_phrases", [])
            if top:
                reasons = ", ".join(p[:35] for p in top[:3])
                lines.append(f"    {gy('only file with:')} {reasons}")
        lines.append("")

    # ── Cleanup ──
    if analysis.dead_exports:
        lines.append(sh("POTENTIAL DEAD CODE:"))
        for de in analysis.dead_exports[:10]:
            lines.append(f"  {r('✗')} {gr(de['phrase'][:45])}  {gy(de['file'])}")
        if len(analysis.dead_exports) > 10:
            lines.append(gy(f"  … +{len(analysis.dead_exports) - 10} more candidates"))
        lines.append("")

    # ── Structure Clusters ──
    if analysis.structure_clusters:
        lines.append(sh("STRUCTURE CLUSTERS (architectural groups):"))
        for sc in analysis.structure_clusters[:8]:
            lines.append(f"  {sc['label']:<20} [{sc['file_count']:>3} files] {gy(sc['top_files'][0] if sc['top_files'] else '')}")
            if sc['characteristic_phrases']:
                cp = ", ".join(p[:30] for p in sc['characteristic_phrases'][:4])
                lines.append(f"  {'':>22} phrases: {gy(cp)}")
        if len(analysis.structure_clusters) > 8:
            lines.append(gy(f"  … +{len(analysis.structure_clusters) - 8} more groups"))
        # Show ungrouped count
        grouped_files = sum(sc["file_count"] for sc in analysis.structure_clusters)
        ungrouped = analysis.total_files - grouped_files
        lines.append(f"  {'':>22} {gy(f'{ungrouped} files ungrouped (no cluster match)')}")
        lines.append("")

    return "\n".join(lines)


def format_quick(analysis: CodebaseAnalysis) -> str:
    """One-glance summary — concept driven."""
    g = analysis.concept_groups
    lines = []

    langs = sorted(analysis.languages.items(), key=lambda x: -x[1])[:3]
    lang_str = " ".join(f"{l}({n})" for l, n in langs)

    concepts = []
    # Prefer meaningful categories over errors/syntax
    for group_name in ["exported", "api", "config", "identifier"]:
        items = getattr(g, group_name, [])
        if items:
            concepts.append(items[0][0][:30])
        if len(concepts) >= 3:
            break
    if not concepts and g.error:
        concepts.append(g.error[0][0][:30])

    unique_explanation = ""
    if analysis.landmarks:
        l = analysis.landmarks[0]
        top = l.get("unique_phrases", [])
        if top:
            unique_explanation = f"({top[0][:30]})"
        else:
            unique_explanation = l['path'].split("/")[-1]

    lines.append(_color(f"{'━' * 50}", "cyan"))
    lines.append(f"  {analysis.path}")
    lines.append(f"  {analysis.total_files} files  {analysis.total_phrases} phrases  {len(analysis.languages)} langs")
    lines.append(_color(f"{'━' * 50}", "cyan"))
    lines.append(f"  Top langs:    {lang_str}")
    lines.append(f"  Concepts:     {' | '.join(concepts[:3])}")
    lines.append(f"  Patterns:     {len(analysis.clusters)} discovered")
    lines.append(f"  Unique:       {len(analysis.landmarks)} files {unique_explanation}")
    lines.append(f"  Dead code:    {len(analysis.dead_exports)} candidates")
    lines.append(_color(f"{'━' * 50}", "cyan"))

    return "\n".join(lines)


def format_json(analysis: CodebaseAnalysis) -> str:
    import json
    g = analysis.concept_groups
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
        "concepts": {
            "exported": [{"name": p, "frequency": f} for p, f in g.exported[:20]],
            "error": [{"name": p, "frequency": f} for p, f in g.error[:10]],
            "api": [{"name": p, "frequency": f} for p, f in g.api[:10]],
            "config": [{"name": p, "frequency": f} for p, f in g.config[:10]],
            "identifier": [{"name": p, "frequency": f} for p, f in g.identifier[:20]],
            "import_path": [{"name": p, "frequency": f} for p, f in g.import_path[:10]],
        },
        "patterns": [{"label": l, "size": len(c)} for l, c in zip(analysis.cluster_labels, analysis.clusters)],
        "landmarks": [{"path": lm["path"], "uniqueness": lm["uniqueness"], "unique_phrases": lm.get("unique_phrases", [])} for lm in analysis.landmarks[:20]],
        "dead_exports": [{"phrase": de["phrase"], "file": de["file"]} for de in analysis.dead_exports[:30]],
    }
    return json.dumps(data, indent=2)


def format_html(analysis: CodebaseAnalysis) -> str:
    g = analysis.concept_groups

    lang_rows = "".join(f"""
    <tr>
      <td>{lang}</td>
      <td><div class="bar" style="width:{count / analysis.total_files * 100:.1f}%"></div></td>
      <td>{count}</td>
      <td>{count / analysis.total_files * 100:.1f}%</td>
      <td>{analysis.phrases_by_language.get(lang, 0)}</td>
    </tr>""" for lang, count in sorted(analysis.languages.items(), key=lambda x: -x[1]))

    concept_sections = ""
    for title, items in [("Types/Exports", g.exported[:15]), ("API Routes", g.api[:10]),
                         ("Error Types", g.error[:10]), ("Config Keys", g.config[:10])]:
        if not items:
            continue
        rows = "".join(f"<tr><td>{p}</td><td>{f}</td></tr>" for p, f in items)
        concept_sections += f"<h2>{title}</h2><table>{rows}</table>"

    cluster_rows = "".join(f"<tr><td>{i+1}</td><td>{l}</td><td>{len(c)}</td></tr>"
                           for i, (l, c) in enumerate(zip(analysis.cluster_labels, analysis.clusters)))

    unique_rows = "".join(f"""
    <tr><td>{lm['path'][:60]}</td><td>{lm.get('unique_phrases', [''])[0][:40] if lm.get('unique_phrases') else ''}</td></tr>
    """ for lm in analysis.landmarks[:15])

    dead_rows = "".join(f"<tr><td>{de['phrase'][:50]}</td><td>{de['file'][:40]}</td></tr>"
                        for de in analysis.dead_exports[:20])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>vocab — {analysis.path}</title>
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
<h1>vocab analyze — {analysis.path}</h1>
<div class="summary">
  <strong>{analysis.total_files}</strong> files &middot;
  <strong>{analysis.total_phrases}</strong> phrases &middot;
  <strong>{len(analysis.languages)}</strong> languages
</div>

<h2>Languages</h2>
<table><tr><th>Language</th><th>%</th><th>Files</th><th>%</th><th>Phrases</th></tr>{lang_rows}</table>

{concept_sections}

<h2>Discovered Patterns</h2>
<table><tr><th>#</th><th>Pattern</th><th>Files</th></tr>{cluster_rows}</table>

<h2>Unique Files</h2>
<table><tr><th>File</th><th>Characteristic</th></tr>{unique_rows}</table>

<h2>Dead Code Candidates</h2>
<table><tr><th>Phrase</th><th>Location</th></tr>{dead_rows}</table>
</body>
</html>"""


def format_lifecycles(lifecycles: list[dict], weeks: int, show_all: bool = False) -> str:
    """Format lifecycle signals for terminal output."""
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
    """Format PR blast radius for terminal output."""
    lines = []
    h = lambda t: _color(t, "header")
    sh = lambda t: _color(t, "subheader")
    gr = lambda t: _color(t, "green")
    r = lambda t: _color(t, "red")
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
        lines.append(gy("  No measurable blast radius"))
        lines.append("")
        return "\n".join(lines)

    lines.append(sh("Blast radius (unchanged files sharing concepts):"))
    high = [i for i in impacts if i["shared_concepts"] >= 5]
    med = [i for i in impacts if 3 <= i["shared_concepts"] < 5]
    low = [i for i in impacts if 1 <= i["shared_concepts"] < 3]

    for label, bucket, limit in [("HIGH", high, 8), ("MED", med, 6), ("LOW", low, 4)]:
        if not bucket:
            continue
        color_fn = r if label == "HIGH" else (y if label == "MED" else gy)
        lines.append(f"  {color_fn(label)} — {len(bucket)} files")
        for item in bucket[:limit]:
            conc_bar = _bar(item["concentration"] * 1000, 8)
            concepts = ", ".join(item["concepts"][:4])
            lines.append(f"    {conc_bar} {item['file'][:55]}  {gy('shares:')} {concepts}")

    if len(impacts) > 18:
        lines.append(gy(f"  … +{len(impacts) - 18} more files"))

    rename_warnings = results.get("rename_warnings", [])
    if rename_warnings:
        lines.append("")
        lines.append(y("Suspected renames:"))
        for rw in rename_warnings[:5]:
            lines.append(f"  {r('✗')} {rw['old_name'][:30]}  {gr('→')}  {rw['new_name'][:30]}")

    lines.append("")
    return "\n".join(lines)
