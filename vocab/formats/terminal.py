"""Terminal output formatter."""

from __future__ import annotations

from vocab.scanner import CodebaseAnalysis


def format_terminal(analysis: CodebaseAnalysis) -> str:
    lines = []
    sep = "─" * 60

    lines.append(sep)
    lines.append(f"  vocab analyze — structural codebase analysis")
    lines.append(f"  path: {analysis.path}")
    lines.append(f'  files: {analysis.total_files}  |  phrases: {analysis.total_phrases}  |  languages: {len(analysis.languages)}')
    lines.append(sep)
    lines.append("")

    # Languages
    lines.append("LANGUAGES:")
    for lang, count in sorted(analysis.languages.items(), key=lambda x: -x[1]):
        pct = count / analysis.total_files * 100 if analysis.total_files else 0
        phrases = analysis.phrases_by_language.get(lang, 0)
        lines.append(f"  {lang:<15} {count:>5} files  |  {phrases:>7} phrases  ({pct:.0f}%)")

    shared = analysis.shared_across_languages
    lines.append(f"  {'─'*40}")
    lines.append(f"  Shared across languages:  {shared} phrases  ({shared/analysis.total_unique_phrases*100:.1f}% of unique)" if analysis.total_unique_phrases else "")
    lines.append("")

    # Top phrases
    lines.append("TOP PHRASES (by frequency):")
    for phrase, freq in analysis.top_phrases[:20]:
        pct = freq / analysis.total_phrases * 100 if analysis.total_phrases else 0
        phrase_display = phrase[:60] + "..." if len(phrase) > 60 else phrase
        lines.append(f"  {phrase_display:<62} {freq:>6}  ({pct:.2f}%)")
    lines.append("")

    # Co-occurrence clusters
    if analysis.clusters:
        lines.append("CO-OCCURRENCE CLUSTERS:")
        for cluster in analysis.clusters[:10]:
            display = ", ".join(c[:30] for c in cluster[:5])
            if len(cluster) > 5:
                display += f" ... (+{len(cluster)-5})"
            lines.append(f"  [{len(cluster)} phrases] {display}")
        lines.append("")

    # Structural clones
    if analysis.structural_clones:
        lines.append("STRUCTURAL CLONE GROUPS:")
        for clone in analysis.structural_clones[:10]:
            langs = "/".join(clone["languages"])
            files_short = [f.split("/")[-1] for f in clone["files"]]
            lines.append(f"  sim={clone['similarity']:.2f}  {langs:>10}  {', '.join(files_short)}")
        lines.append("")

    # Landmarks
    if analysis.landmarks:
        lines.append("HIGHLY UNIQUE FILES (lowest vocabulary overlap):")
        for lm in analysis.landmarks[:10]:
            lines.append(f"  {lm['uniqueness']:.2f} unique  {lm['language']:<12} {lm['path']}")
        lines.append("")

    # Dead exports
    if analysis.dead_exports:
        lines.append("DEAD EXPORT CANDIDATES (phrases in 1 file only):")
        for de in analysis.dead_exports[:20]:
            lines.append(f"  {de['phrase']:<40} {de['file']}")
        lines.append("")

    return "\n".join(lines)


def format_json(analysis: CodebaseAnalysis) -> str:
    import json
    data = {
        "path": analysis.path,
        "total_files": analysis.total_files,
        "total_phrases": analysis.total_phrases,
        "total_unique_phrases": analysis.total_unique_phrases,
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
    terminal = format_terminal(analysis)
    safe = terminal.replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>vocab analyze — {analysis.path}</title>
<style>
  body {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
  pre {{ background: #16213e; padding: 16px; border-radius: 8px; line-height: 1.5; }}
</style>
</head>
<body>
<pre>{safe}</pre>
</body>
</html>"""
