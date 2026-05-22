"""Terminal output formatter — improved UIUX."""

from __future__ import annotations

from vocab.scanner import CodebaseAnalysis


def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _color(text: str, color: str) -> str:
    codes = {
        "header": "\033[1;36m", "subheader": "\033[1;33m",
        "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
        "cyan": "\033[36m", "gray": "\033[90m", "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{codes.get(color, '')}{text}{codes['reset']}"


def _lang_icon(lang: str) -> str:
    icons = {
        "Go": "🔵", "Python": "🟡", "TypeScript": "🔷", "JavaScript": "🟨",
        "Rust": "🦀", "C": "⚪", "C++": "🔷", "Java": "☕", "Kotlin": "🟣",
        "SQL": "🗄️", "JSON": "📋", "YAML": "📄", "Markdown": "📝",
        "Shell": "🐚", "Dockerfile": "🐳", "HTML": "🌐", "CSS": "🎨",
        "TOML": "⚙️", "Unknown": "❓",
    }
    return icons.get(lang, "📄")


def format_terminal(analysis: CodebaseAnalysis, use_color: bool = True) -> str:
    lines = []
    c = lambda t, color: _color(t, color) if use_color else t

    header = f"vocab analyze — {analysis.path}"
    lines.append(c(f"{'━' * 60}", "cyan"))
    lines.append(c(f"  {header}", "header"))
    lines.append(c(f"  files: {analysis.total_files}  phrases: {analysis.total_phrases}  unique: {analysis.total_unique_phrases}", "gray"))
    lines.append(c(f"{'━' * 60}", "cyan"))
    lines.append("")

    # Languages with bars
    lines.append(c("LANGUAGES:", "subheader"))
    sorted_langs = sorted(analysis.languages.items(), key=lambda x: -x[1])
    for lang, count in sorted_langs[:10]:
        pct = count / analysis.total_files * 100 if analysis.total_files else 0
        phrases = analysis.phrases_by_language.get(lang, 0)
        icon = _lang_icon(lang)
        bar = _bar(pct, 15)
        line = f"  {icon} {lang:<12} {c(bar, 'cyan')} {count:>4} files  {pct:>5.1f}%  {phrases:>6} phrases"
        lines.append(line)
    if analysis.shared_across_languages > 0:
        shared_pct = analysis.shared_across_languages / analysis.total_unique_phrases * 100
        lines.append(c(f"  {'─' * 50}", "gray"))
        lines.append(c(f"  Cross-language shared: {analysis.shared_across_languages} ({shared_pct:.1f}%)", "yellow"))
    lines.append("")

    # Top phrases — categorized
    lines.append(c("TOP CONCEPTS:", "subheader"))
    for phrase, freq in analysis.top_phrases[:15]:
        pct = freq / analysis.total_phrases * 100 if analysis.total_phrases else 0
        phrase_disp = phrase[:55] + "..." if len(phrase) > 55 else phrase
        bar = _bar(min(pct * 10, 100), 8)
        lines.append(f"  {c(bar, 'green')} {phrase_disp:<58} {freq:>6} ({pct:.2f}%)")
    lines.append("")

    # Co-occurrence clusters
    if analysis.clusters:
        lines.append(c("CO-OCCURRENCE CLUSTERS (discovered patterns):", "subheader"))
        for cluster in analysis.clusters[:8]:
            display = ", ".join(c[:25] for c in cluster[:4])
            if len(cluster) > 4:
                display += f" ... +{len(cluster) - 4}"
            lines.append(f"  [{c(str(len(cluster)), 'yellow')} concepts] {display}")
        lines.append("")

    # Structural clones
    if analysis.structural_clones:
        lines.append(c("STRUCTURAL CLONE GROUPS:", "subheader"))
        for clone in analysis.structural_clones[:6]:
            langs = "/".join(clone["languages"])
            sim_bar = _bar(clone["similarity"] * 100, 10)
            files_short = [f.split("/")[-1][:30] for f in clone["files"]]
            lines.append(f"  {c(sim_bar, 'cyan')} {clone['similarity']:.0%}  {langs:<10}  {', '.join(files_short[:3])}")
        lines.append("")

    # Landmarks
    if analysis.landmarks:
        lines.append(c("UNIQUE FILES (characteristic code):", "subheader"))
        for lm in analysis.landmarks[:8]:
            icon = _lang_icon(lm["language"])
            uniq_bar = _bar(lm["uniqueness"] * 100, 8)
            lines.append(f"  {icon} {c(uniq_bar, 'green')} {lm['uniqueness']:.0%}  {lm['path']}")
        lines.append("")

    # Dead exports
    if analysis.dead_exports:
        lines.append(c("SINGLE-FILE CONCEPTS (cleanup candidates):", "subheader"))
        for de in analysis.dead_exports[:12]:
            phrase_short = de["phrase"][:45]
            lines.append(f"  • {phrase_short}  {c(de['file'], 'gray')}")
        lines.append("")

    return "\n".join(lines)


def format_quick(analysis: CodebaseAnalysis, use_color: bool = True) -> str:
    """One-glance summary in 10 lines."""
    c = lambda t, color: _color(t, color) if use_color else t
    lines = []

    langs = sorted(analysis.languages.items(), key=lambda x: -x[1])[:3]
    lang_str = " ".join(f"{l}({n})" for l, n in langs)
    top3 = analysis.top_phrases[:3]
    top_str = " ".join(f"{p[:20]}({f})" for p, f in top3)

    lines.append(c(f"{'━' * 50}", "cyan"))
    lines.append(f"  {analysis.path}")
    lines.append(f"  {analysis.total_files} files  {analysis.total_phrases} phrases  {len(analysis.languages)} langs")
    lines.append(c(f"{'━' * 50}", "cyan"))
    lines.append(f"  Top langs:    {lang_str}")
    lines.append(f"  Top concepts: {top_str}")
    lines.append(f"  Clusters:     {len(analysis.clusters)} discovered")
    lines.append(f"  Unique files: {len(analysis.landmarks)} high-signal")
    lines.append(f"  Cleanup:      {len(analysis.dead_exports)} candidates")
    lines.append(c(f"{'━' * 50}", "cyan"))

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
        "top_phrases": [{"phrase": p, "frequency": f} for p, f in analysis.top_phrases[:30]],
        "clusters": analysis.clusters[:20],
        "structural_clones": analysis.structural_clones[:20],
        "landmarks": analysis.landmarks[:20],
        "dead_exports": analysis.dead_exports[:50],
    }
    return json.dumps(data, indent=2)


def format_html(analysis: CodebaseAnalysis) -> str:
    lang_rows = []
    for lang, count in sorted(analysis.languages.items(), key=lambda x: -x[1]):
        pct = count / analysis.total_files * 100 if analysis.total_files else 0
        phrases = analysis.phrases_by_language.get(lang, 0)
        lang_rows.append(f"""
        <tr>
          <td>{lang}</td>
          <td><div style="background:#1e90ff;height:20px;width:{pct}%"></div></td>
          <td>{count}</td>
          <td>{pct:.1f}%</td>
          <td>{phrases}</td>
        </tr>""")
    phrase_rows = []
    for phrase, freq in analysis.top_phrases[:20]:
        pct = freq / analysis.total_phrases * 100 if analysis.total_phrases else 0
        phrase_rows.append(f"""
        <tr>
          <td style="font-family:monospace">{phrase[:60]}</td>
          <td>{freq}</td>
          <td>{pct:.2f}%</td>
        </tr>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>vocab — {analysis.path}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
  h1 {{ color: #00d4ff; }}
  h2 {{ color: #ffd700; margin-top: 30px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ padding: 8px 12px; border: 1px solid #333; }}
  th {{ background: #16213e; color: #00d4ff; }}
  tr:nth-child(even) {{ background: #16213e; }}
  .bar {{ background: linear-gradient(90deg, #1e90ff, #00d4ff); height: 20px; }}
  .summary {{ background: #16213e; padding: 16px; border-radius: 8px; margin-bottom: 20px; }}
</style>
</head>
<body>
<h1>vocab analyze</h1>
<div class="summary">
  <strong>Path:</strong> {analysis.path}<br>
  <strong>Files:</strong> {analysis.total_files} | <strong>Phrases:</strong> {analysis.total_phrases} | <strong>Languages:</strong> {len(analysis.languages)}
</div>

<h2>Languages</h2>
<table>
  <tr><th>Language</th><th>Distribution</th><th>Files</th><th>%</th><th>Phrases</th></tr>
  {''.join(lang_rows)}
</table>

<h2>Top Concepts</h2>
<table>
  <tr><th>Phrase</th><th>Frequency</th><th>%</th></tr>
  {''.join(phrase_rows)}
</table>

<h2>Discovered Patterns</h2>
<p><strong>Co-occurrence clusters:</strong> {len(analysis.clusters)}</p>
<p><strong>Unique files:</strong> {len(analysis.landmarks)}</p>
<p><strong>Cleanup candidates:</strong> {len(analysis.dead_exports)}</p>

</body>
</html>"""