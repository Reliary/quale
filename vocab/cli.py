"""vocab CLI — grammar-free structural codebase analyzer."""

from __future__ import annotations

import sys
import os
from pathlib import Path

try:
    import typer
    from typing_extensions import Annotated
except ImportError:
    print("vocab needs `typer` and `typing-extensions`. Install: pip install typer typing-extensions")
    sys.exit(1)

from vocab.scanner import scan_codebase, concept_timeline, search_cross_repo
from vocab.formats.terminal import format_terminal, format_json, format_html, format_quick
from vocab.index import encode_indices, decode_indices, index_sequence_hash, structural_similarity
from vocab.vocabulary import build_vocabulary
from vocab.segmenter import segment
from vocab import git as vgit


cli = typer.Typer(help="vocab — grammar-free structural codebase analyzer.")


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


@cli.command()
def analyze(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json, html, quick")] = "terminal",
    ref: Annotated[str | None, typer.Option("--ref", "-r", help="Git ref to analyze")] = None,
    clones: Annotated[bool, typer.Option("--clones", help="Enable structural clone detection (slower)")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colored output")] = False,
):
    """Analyze a codebase and produce structural report."""
    try:
        analysis = scan_codebase(path, git_ref=ref, clones=clones)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(format_json(analysis))
    elif format == "html":
        typer.echo(format_html(analysis))
    elif format == "quick":
        typer.echo(format_quick(analysis))
    else:
        typer.echo(format_terminal(analysis))


@cli.command()
def diff(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """Compare structural vocabulary between two git refs."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Comparing {ref_a} → {ref_b}")
    typer.echo("")

    try:
        analysis_a = scan_codebase(path, git_ref=ref_a)
        analysis_b = scan_codebase(path, git_ref=ref_b)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    # Build phrase sets
    phrases_a: set[str] = set()
    for fv in analysis_a.file_vocabs:
        phrases_a.update(fv.vocabulary.keys())
    phrases_b: set[str] = set()
    for fv in analysis_b.file_vocabs:
        phrases_b.update(fv.vocabulary.keys())

    new_concepts = phrases_b - phrases_a
    retired_concepts = phrases_a - phrases_b
    stable_concepts = phrases_a & phrases_b
    total = len(phrases_a) | len(phrases_b)
    new_pct = len(new_concepts) / total * 100 if total else 0
    retired_pct = len(retired_concepts) / total * 100 if total else 0

    c = lambda t, color: _color(t, color)
    bar_new = _bar(new_pct, 10)
    bar_retired = _bar(retired_pct, 10)

    typer.echo(c(f"{'━' * 50}", "cyan"))
    typer.echo(c(f"  CONCEPT DELTA: {ref_a} → {ref_b}", "header"))
    typer.echo(c(f"{'━' * 50}", "cyan"))
    typer.echo("")
    typer.echo(f"  {c('+ New', 'green')}      {c(bar_new, 'green')} {len(new_concepts):>6} ({new_pct:.1f}%)")
    typer.echo(f"  {c('- Retired', 'red')}   {c(bar_retired, 'red')} {len(retired_concepts):>6} ({retired_pct:.1f}%)")
    typer.echo(f"  {c('○ Stable', 'yellow')}   {len(stable_concepts):>6}")
    typer.echo("")
    if new_concepts:
        typer.echo(c(f"NEW CONCEPTS (first 15):", "subheader"))
        for phrase in sorted(new_concepts)[:15]:
            typer.echo(f"  {c('+', 'green')} {phrase[:60]}")
    if retired_concepts:
        typer.echo(c(f"RETIRED CONCEPTS (first 10):", "subheader"))
        for phrase in sorted(retired_concepts)[:10]:
            typer.echo(f"  {c('-', 'red')} {phrase[:60]}")


@cli.command()
def search(
    phrase: Annotated[str, typer.Argument(help="Phrase to search for")],
    paths: Annotated[list[str], typer.Argument(help="Repo paths to search")] = ["."],
):
    """Search for a phrase across one or more repos."""
    results = search_cross_repo(phrase, paths)
    if not results:
        typer.echo(f"'{phrase}' not found in any repo.")
        return
    typer.echo(f"'{phrase}' found in {len(results)} locations:")
    for r in results[:30]:
        typer.echo(f"  {r['repo']:<20} {r['file']:<50} {r['language']}")


@cli.command()
def fingerprint(
    path: Annotated[str, typer.Argument(help="Path to file")],
):
    """Generate structural fingerprint for a file."""
    if not os.path.isfile(path):
        typer.echo("Not a file.", err=True)
        raise typer.Exit(1)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        typer.echo(f"Error reading file: {e}", err=True)
        raise typer.Exit(1)

    seg_result = segment(content)
    if not seg_result.phrases:
        typer.echo("No phrases found.")
        return

    vocab = build_vocabulary(seg_result.phrases, seg_result.strategy, seg_result.delimiter)
    from collections import Counter
    indices = [vocab.lookup(i) for i in range(1, vocab.size + 1)]
    phrase_counter: dict[str, int] = {}
    for p in seg_result.phrases:
        phrase_counter[p] = phrase_counter.get(p, 0) + 1

    index_list = []
    phrase_to_idx = {e.text: e.index for e in vocab.entries}
    for p in seg_result.phrases:
        idx = phrase_to_idx.get(p, 0)
        if idx:
            index_list.append(idx)

    h = index_sequence_hash(index_list)
    typer.echo(f"Fingerprint: v0-{h}")
    typer.echo(f"Strategy:    {seg_result.strategy}")
    typer.echo(f"Phrases:     {vocab.size} unique / {len(seg_result.phrases)} total")
    typer.echo(f"Indices:     {len(index_list)}")


@cli.command()
def clone(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    threshold: Annotated[float, typer.Option("--threshold", "-t", help="Similarity threshold (0-1)")] = 0.85,
    min_files: Annotated[int, typer.Option("--min-files", "-m", help="Minimum files per clone group")] = 2,
):
    """Find structural clone groups across the codebase."""
    try:
        analysis = scan_codebase(path)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    clones = [c for c in analysis.structural_clones if c["size"] >= min_files and c["similarity"] >= threshold]

    if not clones:
        typer.echo("No structural clone groups found.")
        return

    typer.echo(f"Found {len(clones)} structural clone groups:")
    typer.echo("")
    for i, clone in enumerate(clones, 1):
        langs = "/".join(clone["languages"])
        typer.echo(f"  Group {i} (sim={clone['similarity']:.2f}, {langs}):")
        for f in clone["files"]:
            typer.echo(f"    {f}")


@cli.command()
def landmarks(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 10,
):
    """Find the most characteristic (highest-uniqueness) files."""
    try:
        analysis = scan_codebase(path)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not analysis.landmarks:
        typer.echo("No highly unique files found.")
        return

    typer.echo("MOST UNIQUE FILES (highest vocabulary uniqueness):")
    typer.echo("")
    for lm in analysis.landmarks[:limit]:
        typer.echo(f"  {lm['uniqueness']:.2f}  {lm['language']:<12}  {lm['path']}  ({lm['unique_phrases']} unique phrases)")


@cli.command()
def timeline(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 12,
):
    """Track concept evolution across git history."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = concept_timeline(path, weeks=weeks)
    if not data:
        typer.echo("No timeline data available.")
        return

    c = lambda t, color: _color(t, color)

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c(f"  CONCEPT TIMELINE (last {weeks} weeks)", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo("")
    typer.echo(f"  {'Week':<10} {'Commits':<8} {'New':<8} {'Retired':<8} {'Stable':<8} {'Total':<8} {'Trend'}")
    typer.echo(f"  {'─' * 58}")

    for wk in data:
        new = wk['new_concepts']
        retired = wk['retired_concepts']
        trend = ""
        if new > retired:
            trend = c(f"↑ +{new - retired}", "green")
        elif new < retired:
            trend = c(f"↓ -{retired - new}", "red")
        else:
            trend = c("→ 0", "yellow")
        typer.echo(f"  {wk['week']:<10} {wk['commits']:<8} {c(str(new), 'green'):>8} {c(str(retired), 'red'):>8} {wk['stable_concepts']:<8} {wk['total_concepts']:<8} {trend}")


def main():
    if len(sys.argv) == 1:
        typer.echo("vocab — grammar-free structural codebase analyzer")
        typer.echo("")
        typer.echo("Usage:")
        typer.echo("  vocab analyze [path]               analyze codebase structure")
        typer.echo("  vocab diff <ref_a> <ref_b>         compare two git refs")
        typer.echo("  vocab search <phrase> [repos]      search across repos")
        typer.echo("  vocab fingerprint <file>           structural fingerprint")
        typer.echo("  vocab clone [path]                 find structural clones")
        typer.echo("  vocab landmarks [path]             find unique files")
        typer.echo("  vocab timeline [path]              concept history")
        typer.echo("")
        typer.echo("Options:")
        typer.echo("  vocab analyze --clones             include clone detection (slower)")
        typer.echo("  vocab analyze --format json/html   output format")
        typer.echo("  vocab analyze --ref <git-ref>      analyze a specific git ref")
        return
    cli()


if __name__ == "__main__":
    main()
