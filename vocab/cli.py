"""vocab CLI — grammar-free structural codebase analyzer."""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

try:
    import typer
    from typing_extensions import Annotated
except ImportError:
    print("vocab needs `typer` and `typing-extensions`. Install: pip install typer typing-extensions")
    sys.exit(1)

from vocab.scanner import (scan_codebase, concept_timeline, search_cross_repo,
                           compute_lifecycles, pr_blast_radius, search_cross_repo_ranked)
from vocab.formats.terminal import (format_terminal, format_json, format_html, format_quick,
                                    format_lifecycles, format_blast_radius,
                                    format_lifecycles_json, format_blast_json,
                                    format_orphans_json, format_pr_report_markdown)
from vocab.index import encode_indices, decode_indices, index_sequence_hash, structural_similarity
from vocab.vocabulary import build_vocabulary
from vocab.segmenter import segment
from vocab import git as vgit
from vocab.config import load_config


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
    deep: Annotated[bool, typer.Option("--deep", help="Enable deep analysis: co-occurrence matrix, clusters, landmarks (slower on large repos)")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colored output")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only output on error")] = False,
):
    try:
        analysis = scan_codebase(path, git_ref=ref, clones=clones, deep=deep, quiet=quiet)
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
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        analysis_a = scan_codebase(path, git_ref=ref_a, quiet=True)
        analysis_b = scan_codebase(path, git_ref=ref_b, quiet=True)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    phrases_a: set[str] = set()
    for fv in analysis_a.file_vocabs:
        phrases_a.update(fv.vocabulary.keys())
    phrases_b: set[str] = set()
    for fv in analysis_b.file_vocabs:
        phrases_b.update(fv.vocabulary.keys())

    new_concepts = phrases_b - phrases_a
    retired_concepts = phrases_a - phrases_b
    stable_concepts = phrases_a & phrases_b

    if format == "json":
        typer.echo(json.dumps({
            "ref_a": ref_a, "ref_b": ref_b,
            "new": sorted(new_concepts)[:50],
            "retired": sorted(retired_concepts)[:50],
            "stable_count": len(stable_concepts),
        }, indent=2))
        return

    typer.echo(_color(f"Comparing {ref_a} → {ref_b}", "header"))
    total = len(phrases_a) | len(phrases_b)
    new_pct = len(new_concepts) / total * 100 if total else 0
    retired_pct = len(retired_concepts) / total * 100 if total else 0
    typer.echo(f"  {_color('+ New', 'green')}     {len(new_concepts):>6} ({new_pct:.1f}%)")
    typer.echo(f"  {_color('- Retired', 'red')}   {len(retired_concepts):>6} ({retired_pct:.1f}%)")
    typer.echo(f"  {_color('○ Stable', 'yellow')}   {len(stable_concepts):>6}")
    if new_concepts:
        typer.echo(_color("NEW CONCEPTS (first 15):", "subheader"))
        for phrase in sorted(new_concepts)[:15]:
            typer.echo(f"  {_color('+', 'green')} {phrase[:60]}")
    if retired_concepts:
        typer.echo(_color("RETIRED CONCEPTS (first 10):", "subheader"))
        for phrase in sorted(retired_concepts)[:10]:
            typer.echo(f"  {_color('-', 'red')} {phrase[:60]}")


@cli.command()
def search(
    phrase: Annotated[str, typer.Argument(help="Phrase to search for")],
    paths: Annotated[list[str], typer.Argument(help="Repo paths to search")] = ["."],
    related: Annotated[bool, typer.Option("--related", "-r", help="Show co-occurring concepts")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json, compact")] = "terminal",
):
    results = search_cross_repo_ranked(phrase, paths)
    if not results:
        if format == "json":
            typer.echo(json.dumps({"phrase": phrase, "results": []}))
        else:
            typer.echo(f"'{phrase}' not found in any repo.")
        return

    if format == "json":
        typer.echo(json.dumps({"phrase": phrase, "results": results}, indent=2))
        return
    if format == "compact":
        for r in results:
            for f in r["files"][:5]:
                typer.echo(f"{r['repo']}:{f['file']}")
        return

    typer.echo(f"'{phrase}' found in {sum(r['matches'] for r in results)} locations across {len(results)} repos:")
    for r in results:
        pct_bar = _bar(r["concentration"] * 100, 10)
        typer.echo(f"  {r['repo']:<20} {pct_bar} {_color(str(r['matches']), 'cyan'):>4} / {r['total_files']:<4} files ({r['concentration']*100:.0f}%)")
        for f in r["files"][:5]:
            typer.echo(f"    {f['file']:<55} {f['language']}")

        if related:
            typer.echo(f"    {_color('(co-occurs with:)', 'gray')}")
            repo_path = next((p for p in paths if os.path.basename(p) == r["repo"] or p == r["repo"]), ".")
            try:
                analysis = scan_codebase(repo_path, quiet=True)
                for f in r["files"][:1]:
                    for fv in analysis.file_vocabs:
                        if fv.path == f["file"]:
                            co_occuring = [p for p in fv.vocabulary if phrase.lower() in p.lower() or p.lower() in phrase.lower()]
                            for p in fv.vocabulary:
                                if p not in co_occuring and len(p) >= 5:
                                    co_occuring.append(p)
                            sample = ", ".join(co_occuring[:5])
                            typer.echo(f"      {sample}")
                            break
            except Exception:
                pass

    if sum(r["matches"] for r in results) > 30:
        remaining = sum(r["matches"] for r in results) - 5 * len(results)
        if remaining > 0:
            typer.echo(f"  … and {remaining} more matches")


@cli.command()
def lifecycle(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 24,
    signal: Annotated[str | None, typer.Option("--signal", "-s", help="Filter by signal type: DEAD, GROWING, STABLE, etc.")] = None,
    show_all: Annotated[bool, typer.Option("--show-all", help="Show GROWING, ACTIVE, and STABLE concepts too")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    fail_on_decaying: Annotated[int, typer.Option("--fail-on-decaying", help="Exit code 1 if N+ concepts are DECAYING")] = 0,
):
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    typer.echo(_color("Computing lifecycles across history...", "gray"), err=True)
    data = compute_lifecycles(path, weeks=weeks)
    if not data:
        typer.echo("No lifecycle data available.")
        return

    if signal:
        data = [d for d in data if d["signal"] == signal.upper()]

    if not data:
        typer.echo(f"No concepts with signal '{signal}' found.")
        return

    if format == "json":
        typer.echo(format_lifecycles_json(data, weeks))
    else:
        typer.echo(format_lifecycles(data, weeks, show_all=show_all))

    if fail_on_decaying > 0:
        decaying = sum(1 for d in data if d["signal"] == "DECAYING")
        if decaying >= fail_on_decaying:
            typer.echo(f"FAIL: {decaying} DECAYING concepts (threshold: {fail_on_decaying})", err=True)
            raise typer.Exit(1)


@cli.command()
def blast(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    fail_on_high: Annotated[int, typer.Option("--fail-on-high", help="Exit code 1 if N+ HIGH-risk files")] = 0,
    fail_on_med: Annotated[int, typer.Option("--fail-on-med", help="Exit code 1 if N+ MEDIUM-risk files")] = 0,
):
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    pr_files = vgit.diff_refs(path, ref_a, ref_b)

    if len(pr_files) > 500:
        typer.echo(_color(f"WARNING: {len(pr_files)} files changed. Approximate.", "yellow"), err=True)
    if len(pr_files) < 3:
        typer.echo(_color(f"WARNING: {len(pr_files)} files changed. Approximate.", "yellow"), err=True)

    try:
        analysis = scan_codebase(path, git_ref=ref_b, quiet=True)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    results = pr_blast_radius(pr_files, analysis.file_vocabs)

    if format == "json":
        typer.echo(format_blast_json(pr_files, results, ref_a, ref_b))
    else:
        typer.echo(format_blast_radius(pr_files, results, ref_a, ref_b))

    if fail_on_high > 0 or fail_on_med > 0:
        high = sum(1 for i in results.get("impacts", []) if i.get("shared_concepts", 0) >= 5)
        med = sum(1 for i in results.get("impacts", []) if 3 <= i.get("shared_concepts", 0) < 5)
        if fail_on_high > 0 and high >= fail_on_high:
            typer.echo(f"FAIL: {high} HIGH-risk files (threshold: {fail_on_high})", err=True)
            raise typer.Exit(1)
        if fail_on_med > 0 and med >= fail_on_med:
            typer.echo(f"FAIL: {med} MEDIUM-risk files (threshold: {fail_on_med})", err=True)
            raise typer.Exit(1)


@cli.command()
def fingerprint(
    path: Annotated[str, typer.Argument(help="Path to file")],
):
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
    try:
        analysis = scan_codebase(path, deep=True, clones=True)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    clones = [c for c in analysis.structural_clones if c["size"] >= min_files and c["similarity"] >= threshold]
    if not clones:
        typer.echo("No structural clone groups found.")
        return

    typer.echo(f"Found {len(clones)} structural clone groups:")
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
    try:
        analysis = scan_codebase(path, deep=True)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not analysis.landmarks:
        typer.echo("No highly unique files found.")
        return

    typer.echo("MOST UNIQUE FILES (highest vocabulary uniqueness):")
    for lm in analysis.landmarks[:limit]:
        typer.echo(f"  {lm['uniqueness']:.2f}  {lm['language']:<12}  {lm['path']}  ({lm['unique_phrases']} unique phrases)")


@cli.command()
def timeline(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 12,
):
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


@cli.command()
def orphans(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    min_risk: Annotated[str, typer.Option("--min-risk", "-r", help="Minimum risk level: RED, YELLOW, ORANGE")] = "YELLOW",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    fail_on_red: Annotated[int, typer.Option("--fail-on-red", help="Exit code 1 if N+ RED-tier orphans")] = 0,
):
    try:
        analysis = scan_codebase(path)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not analysis.dead_exports:
        typer.echo("No orphan exports found.")
        return

    risk_order = {"RED": 0, "YELLOW": 1, "ORANGE": 2, "GREEN": 3}
    min_level = risk_order.get(min_risk.upper(), 1)

    candidates = []
    for de in analysis.dead_exports:
        if "_test." in de["file"] or "/tests/" in de["file"]:
            risk = "GREEN"
        elif "/internal/" in de["file"]:
            risk = "ORANGE"
        else:
            risk = "RED"
        if risk_order.get(risk, 99) >= min_level:
            candidates.append({**de, "risk": risk})

    if format == "json":
        typer.echo(format_orphans_json(analysis, min_risk))
        red_count = sum(1 for c in candidates if c.get("risk") == "RED")
        if fail_on_red > 0 and red_count >= fail_on_red:
            raise typer.Exit(1)
        return

    c = lambda t, color: _color(t, color)
    typer.echo(c(f"{'━' * 50}", "cyan"))
    typer.echo(f"  STRUCTURAL ORPHANS — {len(candidates)} candidates")
    typer.echo(c(f"{'━' * 50}", "cyan"))

    for de in candidates[:30]:
        risk = de["risk"]
        tags = {
            "RED": c("RED", "red"),
            "ORANGE": c("ORANGE", "yellow"),
            "GREEN": c("GREEN", "green"),
        }
        typer.echo(f"  {c('✗', 'red')} {tags.get(risk, '')} {c(de['phrase'][:45], 'bold')}  {c(de['file'], 'gray')}")

    if len(candidates) > 30:
        typer.echo(c(f"  … +{len(candidates) - 30} more candidates", "gray"))

    red_count = sum(1 for c in candidates if c.get("risk") == "RED")
    if fail_on_red > 0 and red_count >= fail_on_red:
        typer.echo(f"FAIL: {red_count} RED-tier orphans (threshold: {fail_on_red})", err=True)
        raise typer.Exit(1)


# ── Gate commands (CI integration) ──

@cli.command()
def gate(
    check: Annotated[str, typer.Argument(help="Check type: blast, orphans, drift")],
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    fail_on_high: Annotated[int, typer.Option("--fail-on-high", help="Max HIGH-risk files (blast)")] = 5,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: json, terminal")] = "json",
):
    """CI gate: run a structural check and exit 1 on violation."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    config = load_config(path)
    config_blast = config.get("blast", {})
    config_orphans = config.get("orphans", {})

    if check == "blast":
        pr_files = vgit.diff_refs(path, ref_a, ref_b)
        analysis = scan_codebase(path, git_ref=ref_b, quiet=True)
        results = pr_blast_radius(pr_files, analysis.file_vocabs)
        impacts = results.get("impacts", [])
        high = sum(1 for i in impacts if i.get("shared_concepts", 0) >= 5)

        threshold = fail_on_high or config_blast.get("max_high", 5)
        passed = high <= threshold

        if format == "json":
            typer.echo(json.dumps({
                "check": "blast", "passed": passed, "high": high,
                "threshold": threshold, "total_impacts": len(impacts),
            }))
        else:
            typer.echo(f"[{'PASS' if passed else 'FAIL'}] blast: {high} HIGH files (threshold {threshold})")

        if not passed:
            raise typer.Exit(1)

    elif check == "orphans":
        pr_files = vgit.diff_refs(path, ref_a, ref_b)
        analysis_a = scan_codebase(path, git_ref=ref_a, quiet=True)
        analysis_b = scan_codebase(path, git_ref=ref_b, quiet=True)

        a_dead = {d["phrase"] for d in analysis_a.dead_exports}
        b_dead = {d["phrase"] for d in analysis_b.dead_exports}
        new_orphans = b_dead - a_dead

        # Filter out test files
        new_orphans = {p for p in new_orphans
                       if not any(
                           p.endswith("_test.go") or "/tests/" in p
                           for p in [next((d["file"] for d in analysis_b.dead_exports if d["phrase"] == p), "")]
                       )}

        max_new = config_orphans.get("max_new_red", 0)
        allowed = set(config_orphans.get("allow", []))
        violating = new_orphans - allowed
        passed = len(violating) <= max_new

        if format == "json":
            typer.echo(json.dumps({
                "check": "orphans", "passed": passed,
                "new_orphans": list(new_orphans)[:30],
                "violating": list(violating)[:30],
                "threshold": max_new,
            }))
        else:
            if violating:
                typer.echo(f"[{'PASS' if passed else 'FAIL'}] orphans: {len(violating)} new RED (threshold {max_new})")
                for v in list(violating)[:10]:
                    typer.echo(f"  ✗ {v}")
            else:
                typer.echo("[PASS] orphans: no new orphans detected")

        if not passed:
            raise typer.Exit(1)

    elif check == "drift":
        pr_files = vgit.diff_refs(path, ref_a, ref_b)
        if not pr_files:
            typer.echo(json.dumps({"check": "drift", "passed": True, "message": "no changed files"}))
            return

        # Compare each changed file's vocabulary to the full repo
        analysis = scan_codebase(path, git_ref=ref_b, quiet=True, deep=True)
        file_vocabs_map = {fv.path: set(fv.vocabulary.keys()) for fv in analysis.file_vocabs}

        changed_vocabs = {}
        for f in pr_files:
            if f in file_vocabs_map:
                changed_vocabs[f] = file_vocabs_map[f]

        if not changed_vocabs:
            typer.echo(json.dumps({"check": "drift", "passed": True, "message": "no code files changed"}))
            return

        # Check all-changed overlap ratio — does this PR touch isolated code?
        all_changed_phrases = set()
        for v in changed_vocabs.values():
            all_changed_phrases.update(v)

        if len(changed_vocabs) <= 1:
            typer.echo(json.dumps({"check": "drift", "passed": True, "message": "single file changed"}))
            return

        # Pairwise overlap between changed files
        pairs = list(changed_vocabs.items())
        low_overlap = []
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                name_a, set_a = pairs[i]
                name_b, set_b = pairs[j]
                intersection = set_a & set_b
                union = set_a | set_b
                if union:
                    jaccard = len(intersection) / len(union)
                    if jaccard < 0.05 and len(intersection) < 3 and len(union) > 10:
                        low_overlap.append({
                            "file_a": name_a, "file_b": name_b,
                            "jaccard": round(jaccard, 3),
                            "shared": len(intersection),
                        })

        max_outliers = config.get("drift", {}).get("max_outliers", 3)
        passed = len(low_overlap) <= max_outliers

        if format == "json":
            typer.echo(json.dumps({
                "check": "drift", "passed": passed,
                "low_overlap_pairs": low_overlap[:20],
                "total_pairs": len(pairs) * (len(pairs) - 1) // 2,
                "threshold": max_outliers,
            }))
        else:
            if low_overlap:
                typer.echo(f"[{'PASS' if passed else 'FAIL'}] drift: {len(low_overlap)} outlier pairs (threshold {max_outliers})")
                for lo in low_overlap[:5]:
                    typer.echo(f"  ⚠ {lo['file_a']} ↔ {lo['file_b']} (jaccard {lo['jaccard']:.2f})")
            else:
                typer.echo("[PASS] drift: all changed files share vocabulary")

        if not passed:
            raise typer.Exit(1)

    else:
        typer.echo(f"Unknown check: {check}. Use: blast, orphans, drift", err=True)
        raise typer.Exit(1)


@cli.command()
def pr_report(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """Generate a consolidated PR structural report (markdown)."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    pr_files = vgit.diff_refs(path, ref_a, ref_b)
    if not pr_files:
        typer.echo("No changed files.")
        return

    # Blast radius
    analysis = scan_codebase(path, git_ref=ref_b, quiet=True)
    blast_results = pr_blast_radius(pr_files, analysis.file_vocabs)

    # New orphans
    analysis_a = scan_codebase(path, git_ref=ref_a, quiet=True)
    a_dead = {d["phrase"] for d in analysis_a.dead_exports}
    b_dead = {d["phrase"] for d in analysis.dead_exports}
    new_orphans_set = b_dead - a_dead
    orphan_details = []
    for de in analysis.dead_exports:
        if de["phrase"] in new_orphans_set:
            risk = "GREEN" if "_test." in de["file"] or "/tests/" in de["file"] else "RED"
            orphan_details.append({**de, "risk": risk})

    md = format_pr_report_markdown(pr_files, blast_results, orphan_details, ref_a, ref_b)
    typer.echo(md)


@cli.command()
def init(path: Annotated[str, typer.Argument(help="Path to repo")] = "."):
    """Generate a .vocab.yml config file with defaults."""
    target = os.path.join(os.path.abspath(path), ".vocab.yml")
    if os.path.exists(target):
        typer.echo(f".vocab.yml already exists at {target}")
        return
    os.makedirs(os.path.abspath(path), exist_ok=True)

    content = """# vocab CI gate configuration
# Run `vocab gate` for these checks in CI pipelines.

blast:
  # Maximum HIGH-risk unchanged files before blocking
  max_high: 5
  # Maximum MEDIUM-risk unchanged files before blocking
  max_med: 20
  # Critical paths — these always fail HIGH regardless of share count
  critical_paths: []
  #   - src/core/**
  #   - src/auth/**

orphans:
  # Maximum new RED-tier orphan exports allowed
  max_new_red: 0
  # Exported names that are allowed as orphans (e.g., public API types)
  allow: []
  #   - PublicError
  #   - ApiResponse

drift:
  # Maximum low-overlap file pairs before blocking
  max_outliers: 3
  # File patterns allowed to drift
  allow: []
  #   - src/stories/**
  #   - docs/**

lifecycle:
  # Minimum weeks of data needed for signal classification
  min_signal_weeks: 4

search:
  # File coverage threshold above which a match is considered "too common"
  common_threshold: 0.8
"""
    with open(target, "w") as f:
        f.write(content)
    typer.echo(f"Created {target}")


def main():
    if len(sys.argv) == 1:
        typer.echo("vocab — grammar-free structural codebase analyzer")
        typer.echo("")
        typer.echo("Commands:")
        typer.echo("  vocab analyze [path]               structural report")
        typer.echo("  vocab diff <a> <b>                 concept delta")
        typer.echo("  vocab blast <a> <b>                PR blast radius")
        typer.echo("  vocab lifecycle [path]             concept lifecycles")
        typer.echo("  vocab timeline [path]              weekly concept history")
        typer.echo("  vocab search <phrase> [repos]      cross-repo search")
        typer.echo("  vocab orphans [path]               structural orphans")
        typer.echo("  vocab fingerprint <file>           structural fingerprint")
        typer.echo("  vocab clone [path]                 structural clones")
        typer.echo("  vocab landmarks [path]             unique files")
        typer.echo("  vocab gate <check> <a> <b>         CI gate (blast|orphans|drift)")
        typer.echo("  vocab pr-report <a> <b>            PR structural report (markdown)")
        typer.echo("  vocab init                         generate .vocab.yml")
        typer.echo("")
        typer.echo("CI examples:")
        typer.echo("  vocab gate blast origin/main HEAD --fail-on-high 3")
        typer.echo("  vocab gate orphans origin/main HEAD")
        typer.echo("  vocab gate drift origin/main HEAD")
        typer.echo("  vocab pr-report origin/main HEAD  # markdown output")
        return
    cli()


if __name__ == "__main__":
    main()
