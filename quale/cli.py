"""quale CLI — grammar-free structural codebase analyzer."""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

try:
    import typer
    from typing_extensions import Annotated
except ImportError:
    print("quale needs `typer` and `typing-extensions`. Install: pip install typer typing-extensions")
    sys.exit(1)

from quale.scanner import (scan_codebase, search_cross_repo_ranked)
from quale.bootstrap import (bootstrap_repo, explore_repo, compute_modules)
from quale.reports import (ci_report, inspect_repo, repo_fingerprint,
                           compute_stability, compute_lifecycles, concept_timeline,
                           preflight_report, build_contract, validate_plan)
from quale.compare import (compare_repos, phrase_provenance, pr_blast_radius)
from quale.formats.terminal import (format_terminal, format_json, format_html, format_quick,
                                     format_lifecycles, format_blast_radius,
                                     format_lifecycles_json, format_blast_json,
                                     format_orphans_json, format_pr_report_markdown,
                                     format_modules, format_modules_json)
from quale.index import index_sequence_hash
from quale.vocabulary import build_vocabulary
from quale.segmenter import segment
from quale import git as vgit


cli = typer.Typer(
    help="""
    quale — structural codebase analysis. No parsers, no config, any language.

    Run `quale review` for a PR review, `quale onboard` for new-repo orientation,
    or `quale agent orient` for LLM agent setup.

    Personas:
      HUMAN  review (PR check), onboard (new repo), refactor-cost, inspect, explore
      CI     check (CI gates), comment (PR comment), trend (metrics over time), init (GitHub Action)
      AGENT  orient (repo map), edit (edit context), guard (risk check)
      CORE   60+ structural primitives — run ``quale core --help``

    Run ``quale core help-agent "your task"`` for command recommendations.
    """,
    add_completion=False,
    context_settings={"help_option_names": ["--help", "-h"]},
)

ci_app = typer.Typer(help="CI actions and automated gates.")
agent_app = typer.Typer(help="LLM Agent optimized commands (JSON/IR outputs).")
core_app = typer.Typer(help="Advanced structural primitives and codebase analysis.")

cli.add_typer(ci_app, name="ci")
cli.add_typer(agent_app, name="agent")
cli.add_typer(core_app, name="core")



def _version_callback(show_version: bool) -> None:
    if show_version:
        from quale import __version__
        typer.echo(f"quale-cli {__version__}")
        raise typer.Exit()


def _help_all(ctx: typer.Context) -> None:
    """Print a compact summary of all commands with first-line descriptions."""
    import re
    with open(__file__, encoding="utf-8") as f:
        src = f.read()
    panels: dict[str, list[tuple[str, str]]] = {}

    def register_cmds(typer_app, prefix=""):
        for c in typer_app.registered_commands:
            name = c.name
            if not name and c.callback:
                name = c.callback.__name__.replace("_", "-")
            if not name:
                continue
            full_name = f"{prefix}{name}"
            doc = (c.callback.__doc__ or "").strip().split("\n")[0] if c.callback else ""
            
            # Simple heuristic: Look for rich_help_panel="Panel" near the definition
            m = re.search(rf'@[a-z_]+\.command\([^)]*rich_help_panel="([^"]+)"[^)]*\)', src)
            
            # A more robust regex: match the command name inside the file
            # Since doing full AST parsing is hard, we just regex for it.
            # Find the command decorator that defines this function
            func_name = c.callback.__name__ if c.callback else ""
            m2 = re.search(rf'@(?:[a-z_]+)\.command\([^)]*rich_help_panel="([^"]+)"[^)]*\)\s*\n\s*def {func_name}', src)
            if not m2:
                m2 = re.search(rf'@(?:[a-z_]+)\.command\([^)]*name="{name}"[^)]*rich_help_panel="([^"]+)"', src)
            if not m2:
                m2 = re.search(rf'@(?:[a-z_]+)\.command\([^)]*rich_help_panel="([^"]+)"[^)]*name="{name}"', src)
                
            panel = m2.group(1) if m2 else "Other"
            if prefix == "ci ": panel = "CI"
            if prefix == "agent ": panel = "Agent Safety"
            panels.setdefault(panel, []).append((full_name, doc))

    register_cmds(cli)
    register_cmds(core_app, "core ")
    register_cmds(ci_app, "ci ")
    register_cmds(agent_app, "agent ")

    for panel in ["Getting Started", "Agent Safety", "Verification", "CI",
                   "Code Analysis", "History", "Maintenance", "Cross-Repo",
                   "Utilities", "Other"]:
        cmds = panels.get(panel, [])
        if not cmds:
            continue
        typer.echo(f"\n\033[1m{panel}:\033[0m")
        for name, doc in sorted(cmds):
            typer.echo(f"  {name:<25s} {doc[:60]}")
    sys.exit(0)


@cli.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", "-V", help="Show version and exit")] = False,
    help_all: Annotated[bool, typer.Option("--help-all", help="Show help for every command")] = False,
):
    if help_all:
        _help_all(ctx)
    _version_callback(version)
    if ctx.invoked_subcommand is None:
        typer.echo("")
        typer.echo(_color("  quale — structural codebase analysis.  ", "header"))
        typer.echo("  No parsers, no config, any language.")
        typer.echo("")
        typer.echo(_color("  For humans:", "subheader"))
        typer.echo("    review                   One-command code review for your PR")
        typer.echo("    onboard                  Onboarding guide for a new repo")
        typer.echo("    refactor-cost <file>     Estimate refactoring effort")
        typer.echo("    inspect                  Full codebase overview")
        typer.echo("    explore                  Map of best files to read")
        typer.echo("")
        typer.echo(_color("  For CI pipelines:", "subheader"))
        typer.echo("    ci check <base> <head>   Run all structural gates (exits 0-7)")
        typer.echo("    ci comment <base> <head> Post PR report to GitHub")
        typer.echo("    ci trend                 CI metric trends")
        typer.echo("")
        typer.echo(_color("  For LLM agents:", "subheader"))
        typer.echo("    agent edit <file>        Safety packet (JSON)")
        typer.echo("    agent guard <file>       Combined guard (JSON)")
        typer.echo("    agent orient             Repo map (JSON)")
        typer.echo("")
        typer.echo(_color("  Under the hood:", "subheader"))
        typer.echo("    core <cmd>               45+ advanced structural primitives")
        typer.echo("")
        typer.echo("  Run 'quale <cmd> --help' or 'quale --help-all' for details.")
        raise typer.Exit()


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


GATE_CODES = {
    "mirror_gap": 4,
    "hub_risk": 5,
    "clone": 6,
    "new_identifiers": 7,
    "blast_tier": 2,
    "stable_touched": 3,
}


def _gate_evaluation(
    data: dict,
    fail_mirror_gap: float | None,
    fail_blast_tier: str | None,
    fail_stable_touched: bool,
    fail_hub_risk: bool = False,
    fail_clone: bool = False,
    fail_new_identifiers: int | None = None,
) -> tuple[list[tuple[int, str]], list[str]]:
    failures = []
    checks = []
    if fail_mirror_gap is not None:
        ratio = data.get("mirror_gap_ratio", 1.0)
        checks.append(f"mirror gap {ratio:.0%} >= {fail_mirror_gap:.0%}")
        if ratio < fail_mirror_gap:
            failures.append((GATE_CODES["mirror_gap"], f"mirror gap {ratio:.0%} < {fail_mirror_gap:.0%}"))
    if fail_blast_tier is not None:
        tier_order = {"none": 0, "local": 1, "moderate": 2, "high": 3, "critical": 4}
        threshold = tier_order.get(fail_blast_tier.lower())
        if threshold is None:
            raise ValueError("Invalid blast tier. Use: local, moderate, high, critical.")
        current_tier = data.get("max_blast_tier", "none")
        checks.append(f"blast tier {current_tier} < {fail_blast_tier.lower()}")
        if tier_order.get(current_tier, 0) >= threshold:
            failures.append((GATE_CODES["blast_tier"], f"blast tier {current_tier} >= {fail_blast_tier.lower()}"))
    if fail_stable_touched:
        count = data.get("stable_touched_count", 0)
        checks.append(f"stable anchors touched {count} == 0")
        if count > 0:
            failures.append((GATE_CODES["stable_touched"], f"{count} stable anchors touched"))
    if fail_hub_risk:
        flagged = data.get("hub_risk_flagged", [])
        checks.append(f"hub risk files {len(flagged)} == 0")
        if flagged:
            failures.append((GATE_CODES["hub_risk"], f"{len(flagged)} changed file(s) in top 10% hub-risk: {', '.join(f['file'] for f in flagged[:5])}"))
    if fail_clone:
        flagged = data.get("clone_flagged", [])
        checks.append(f"clone files {len(flagged)} == 0")
        if flagged:
            failures.append((GATE_CODES["clone"], f"{len(flagged)} changed file(s) are structural clones: {', '.join(f['file'] for f in flagged[:5])}"))
    if fail_new_identifiers is not None:
        count = data.get("new_identifier_count", 0)
        checks.append(f"new identifiers {count} <= {fail_new_identifiers}")
        if count > fail_new_identifiers:
            failures.append((GATE_CODES["new_identifiers"], f"{count} new identifiers introduced (limit {fail_new_identifiers})"))
    return failures, checks


def _relevance_label(score: float) -> tuple[str, str, str]:
    if score >= 0.80:
        return "HIGH", "green", "suggested files contain the task terms"
    if score >= 0.50:
        return "MIXED", "yellow", "some suggestions may be broad matches"
    return "LOW", "red", "inspect manually or use a more specific task"


def _validate_refs(path: str, *refs: str) -> None:
    if not vgit.has_commits(path):
        return
    missing = [ref for ref in refs if not vgit.ref_exists(path, ref)]
    if missing:
        raise ValueError(f"Unknown git ref(s): {', '.join(missing)}")


@core_app.command(rich_help_panel="Getting Started")
def analyze(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json, html, quick")] = "terminal",
    ref: Annotated[str | None, typer.Option("--ref", "-r", help="Git ref to analyze")] = None,
    clones: Annotated[bool, typer.Option("--clones", help="Enable structural clone detection (slower)")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Enable deep analysis: co-occurrence matrix, clusters, landmarks (slower on large repos)")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colored output")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only output on error")] = False,
):

    """List all phrases, languages, files — first-pass structural scan."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if ref is not None:
        try:
            _validate_refs(path, ref)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
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


@cli.command(rich_help_panel="Getting Started")
def diff(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    why: Annotated[bool, typer.Option("--why", help="Show why this diff matters structurally")] = False,
):

    """Compare vocabulary between two git refs."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        _validate_refs(path, ref_a, ref_b)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    from quale.scanner import scan_codebase, _is_lock_file, _is_generated

    try:
        analysis_a = scan_codebase(path, git_ref=ref_a, quiet=True)
        analysis_b = scan_codebase(path, git_ref=ref_b, quiet=True)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    def _skip_diff_phrase(p: str) -> bool:
        low = p.lower()
        if _is_generated(p) or _is_lock_file(p):
            return True
        if "// indirect" in low or "go.sum" in low:
            return True
        if any(kw in p for kw in ["v0.", "v1.", "v2.", "v3.", "v4.", "v5.", "go.sum"]):
            return True
        for ext in [".sum", ".mod", "-lock.json", "yarn.lock", "Cargo.lock", "Gemfile.lock"]:
            if ext in p:
                return True
        return False

    phrases_a: set[str] = set()
    for fv in analysis_a.file_vocabs:
        if not _is_lock_file(fv.path) and not _is_generated(fv.path):
            phrases_a.update(fv.vocabulary.keys())
    phrases_b: set[str] = set()
    for fv in analysis_b.file_vocabs:
        if not _is_lock_file(fv.path) and not _is_generated(fv.path):
            phrases_b.update(fv.vocabulary.keys())

    phrases_a = {p for p in phrases_a if not _skip_diff_phrase(p)}
    phrases_b = {p for p in phrases_b if not _skip_diff_phrase(p)}

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
    if why:
        from quale.formats.terminal import _why_diff
        data = {"changed_files": [], "impacts": [], "mirror_ratio": None}
        typer.echo(_why_diff(data, ref_a, ref_b))


@cli.command(rich_help_panel="Getting Started")
def search(
    phrase: Annotated[str, typer.Argument(help="Phrase to search for")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    related: Annotated[bool, typer.Option("--related", "-r", help="Show co-occurring concepts")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json, compact")] = "terminal",
):

    """Find files containing a phrase or concept."""
    results = search_cross_repo_ranked(phrase, [path])
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
            repo_path = next((p for p in [path] if os.path.basename(p) == r["repo"] or p == r["repo"]), ".")
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


@core_app.command(rich_help_panel="History")
def lifecycle(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 24,
    signal: Annotated[str | None, typer.Option("--signal", "-s", help="Filter by signal type: DEAD, GROWING, STABLE, etc.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):

    """Classify phrases as growing, stable, decaying, dead, seasonal."""
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
        typer.echo(format_lifecycles(data, weeks, show_all=False))


@core_app.command(rich_help_panel="CI")
def blast(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):

    """Vocabulary overlap with changed files."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        _validate_refs(path, ref_a, ref_b)
    except ValueError as e:
        typer.echo(str(e), err=True)
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


@core_app.command(name="edit-context",  rich_help_panel="Agent Safety")
def preflight(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str] | None, typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = None,
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against the working tree")] = None,
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: tool(default), verify, json, checklist, compact, llm, full")] = "tool",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show math-heavy signals (SNR, expansion risk details)")] = False,
    why: Annotated[bool, typer.Option("--why", help="Show why each recommendation exists")] = False,
    enrich: Annotated[bool, typer.Option("--enrich", help="Compute spectrum/deficit/cascade + cross-cutting/risk-vector/acceleration/boundary/module-exposure/fused-priority (single deep scan — ~1s cold)")] = False,
):
    """File-scoped edit context and risk card.

    Examples:
      quale edit-context --files src/spool.ts --task "change upload behavior"
      quale edit-context --diff HEAD~1 --format json
      quale edit-context --files src/spool.ts --format tool
    """
    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not files and not diff:
        typer.echo("provide --files or --diff", err=True)
        raise typer.Exit(1)
    if diff is not None and vgit.has_commits(path) and not vgit.ref_exists(path, diff):
        typer.echo(f"Unknown git ref: {diff}", err=True)
        raise typer.Exit(1)

    data = preflight_report(path=path, files=files, diff_ref=diff, task=task, enrich=enrich)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    verify_candidates = data.get("verification_candidates", data.get("verify_with", []))
    ver_confidence = data.get("verification_confidence", {})
    scope_creep = data.get("scope_creep_guard", {})
    wa = scope_creep.get("warnings", [])
    qs = [w.get("question_extras", "").strip() for w in wa if w.get("question_extras")]
    scope_creep_instruction = (
        "Before broadening scope, verify each extra file: " + "; ".join(qs)
        if qs else
        "Do not propose extra_edits unless the task explicitly requires them."
    )
    vtypes = _classify_verify_types(verify_candidates[:5] if verify_candidates else [], data.get("changed_files", []))

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if format == "llm":
        from quale.formats.llm import format_preflight_llm
        typer.echo(format_preflight_llm(data))
        return
    if format == "verify":
        vtypes = _classify_verify_types(verify_candidates[:5] if verify_candidates else [], data.get("changed_files", []))
        vac_notes = data.get("vaccination_notes", [])
        verify_data = {
            "schema_version": 1,
            "verification_mc": {
                "question": "Which file would verify this change?",
                "candidates": verify_candidates[:5] if verify_candidates else [],
                "max_selections": 1,
                "types": vtypes,
            },
            "verification_confidence": ver_confidence,
            "desert_warning": _desert_text(ver_confidence, data.get("changed_files", [])),
            "verifiability": data.get("verify_classifications", []),
        }
        if vac_notes:
            verify_data["vaccination"] = vac_notes
        typer.echo(json.dumps(verify_data, separators=(",", ":")))
        return
    if format == "tool":
        tool_data = _build_edit_tool_format(data, verify_candidates, vtypes, ver_confidence, scope_creep, scope_creep_instruction)
        typer.echo(json.dumps(tool_data, separators=(",", ":")))
        return
    if format == "full":
        # Full signal set for human inspection or research
        peer = data.get("peer_relative_risk", {})
        envelope = data.get("safety_envelope", {})
        snr = data.get("snr_annotations", {})
        capability = data.get("capability_boundary", "")
        tool_data = {
            "schema_version": 1,
            "risk": data.get("risk", "unknown"),
            "confidence": data.get("confidence", "unknown"),
            "temperature": data.get("temperature", "WARM"),
            "peer_relative": peer.get("peer_text", ""),
            "reason": "; ".join(data.get("reasons", [])),
            "changed_files": data.get("changed_files", []),
            "read_first": data.get("fused_first", data.get("read_first", [])),
            "safety_envelope": {
                "inside": envelope.get("inside", []),
                "at_boundary": envelope.get("at_boundary", []),
                "boundary_count": envelope.get("boundary_count", 0),
            },
            "verification_mc": {
                "question": "Which file would verify this change?",
                "candidates": verify_candidates[:5] if verify_candidates else [],
                "max_selections": 1,
                "types": vtypes,
            },
            "verification_details": data.get("verification_details", []),
            "verification_confidence": ver_confidence,
            "expansion_risk": data.get("expansion_risk", data.get("avoid_expanding_into", [])),
            "scope_creep_guard": {**scope_creep, "instruction": scope_creep_instruction},
            "desert_warning": _desert_text(ver_confidence, data.get("changed_files", [])),
            "co_change": data.get("co_change", []),
            "structural_orphans": data.get("structural_orphans", []),
            "file_classifications": data.get("file_classifications", []),
            "keystone_files": data.get("keystone_files", []),
            "snr_annotations": snr,
            "capability_boundary": capability,
            "guardrails": data.get("guardrails", {}),
            "spectrum": data.get("spectrum"),
            "deficit": data.get("deficit"),
            "cascade": data.get("cascade"),
            "cross_cutting": data.get("cross_cutting"),
            "risk_vector": data.get("risk_vector"),
            "acceleration": data.get("acceleration"),
            "boundary": data.get("boundary"),
            "module_exposure": data.get("module_exposure"),
        }
        typer.echo(json.dumps(tool_data, indent=2))
        return
    if format == "checklist":
        _print_preflight_checklist(data)
        return
    data["verbose"] = verbose
    _print_preflight(data)
    if why:
        from quale.formats.terminal import _why_edit_context
        typer.echo(_why_edit_context(data))


@core_app.command(rich_help_panel="Agent Safety")
def contract(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str] | None, typer.Option("--files", help="Allowed edit file(s); repeat or comma-separate")] = None,
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: tool(default), json, prompt")] = "tool",
):
    """Emit an ID-coded structural contract for deterministic LLM plans.

    The LLM should return IDs from the contract, not raw paths.
    """
    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not files:
        typer.echo("provide --files so contract stays file-scoped", err=True)
        raise typer.Exit(1)

    data = build_contract(path=path, files=files, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if format == "prompt":
        typer.echo("Return exactly one JSON object using IDs only: {\"edit_ids\":[],\"verify_ids\":[],\"expand_scope\":[{\"id\":\"B1\",\"reason\":\"why\"}],\"manual_verify\":[]}")
        typer.echo(json.dumps(data, separators=(",", ":")))
        return
    # tool (default) — compact JSON contract with agent note
    data["schema_version"] = 1
    data["_agent_note"] = "--files takes comma-separated paths; return IDs from this contract, not raw paths"
    typer.echo(json.dumps(data, separators=(",", ":")))


@core_app.command(name="check-plan",  rich_help_panel="Agent Safety")
def check_plan(
    contract_file: Annotated[Path, typer.Option("--contract", "-c", help="Contract JSON file")],
    proposal_file: Annotated[Path | None, typer.Option("--proposal", "-p", help="Proposal JSON file; stdin when omitted")] = None,
    allow_paths: Annotated[bool, typer.Option("--allow-paths", help="Allow raw paths in proposal (not recommended)")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Validate an LLM plan against an ID-coded contract."""
    try:
        contract_data = json.loads(contract_file.read_text(encoding="utf-8"))
    except Exception as e:
        typer.echo(f"failed to read contract: {e}", err=True)
        raise typer.Exit(1)
    try:
        raw = proposal_file.read_text(encoding="utf-8") if proposal_file else sys.stdin.read()
        proposal = json.loads(raw)
    except Exception as e:
        typer.echo(f"failed to read proposal: {e}", err=True)
        raise typer.Exit(1)

    result = validate_plan(contract_data, proposal, allow_paths=allow_paths)
    if format == "json":
        typer.echo(json.dumps(result, indent=2))
        return
    if format == "compact":
        if result.get("valid"):
            typer.echo("VALID plan: scope contained")
        elif result.get("needs_reflight"):
            typer.echo("NEEDS_REFLIGHT: scope expansion requested")
        else:
            codes = ", ".join(v.get("code", "unknown") for v in result.get("violations", []))
            typer.echo(f"INVALID plan: {codes}")
        return
    # tool (default) — compact JSON
    result["_agent_note"] = "--contract and --proposal take file paths, not JSON-inline; pass via file or stdin"
    typer.echo(json.dumps(result, separators=(",", ":")))


@core_app.command(name="repo-map",  rich_help_panel="Getting Started")
def crystallography(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """One-time structural description of a codebase.

    Designed for LLM use: produces a compact skeleton (~100 tokens)
    plus structured detail about test conventions, stable core,
    generated files, and module boundaries. Cache and reuse.
    """
    from quale.reports import crystallography as _crystallography

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = _crystallography(path)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VOCAB CRYSTALLOGRAPHY", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo("")
    typer.echo(c("  Skeleton:", "subheader"))
    typer.echo(f"    {c(data.get('skeleton', ''), 'gray')}")
    typer.echo("")
    typer.echo(f"  Files: {c(str(data.get('total_files', 0)), 'cyan')}  "
               f"Layout: {c(data.get('layout_type', '?'), 'green')}  "
               f"Test: {c(data.get('test_convention', '?'), 'yellow')}  "
               f"Generated: {c(str(data.get('generated_pct', 0)) + '%', 'gray')}")
    typer.echo("")

    stable = data.get("stable_core", [])
    if stable:
        typer.echo(c("  Stable Core:", "subheader"))
        for s in stable[:5]:
            val = s.get('persistence', 0) or 0
            typer.echo(f"    {c(f'{val:.0%}', 'green'):>6s}  {s['file']}")
        typer.echo("")

    concepts = data.get("core_concepts", [])
    if concepts:
        typer.echo(c("  Core Concepts:", "subheader"))
        for b in concepts[:5]:
            typer.echo(f"    {c(b['concept'], 'yellow'):<30} {c(str(b['file_count']), 'cyan'):>4} files")
        typer.echo("")

    modules = data.get("modules", [])
    if modules:
        typer.echo(c("  Modules:", "subheader"))
        for m in modules[:5]:
            files_str = ", ".join(m.get("sample_files", []))
            typer.echo(f"    {c(str(m['size']), 'cyan'):>4} files  {c(files_str, 'gray')}")
        typer.echo("")

    test_dirs = data.get("test_dirs", [])
    test_suffixes = data.get("test_suffixes", [])
    if test_dirs or test_suffixes:
        typer.echo(c("  Test Conventions:", "subheader"))
        if test_dirs:
            typer.echo(f"    Test dirs: {', '.join(test_dirs)}")
        if test_suffixes:
            typer.echo(f"    Test patterns: {', '.join(test_suffixes)}")
        typer.echo("")

    caveat = data.get("guardrails", {}).get("caveat", "")
    if caveat:
        typer.echo(c(f"  Caveat: {caveat}", "yellow"))


@core_app.command(rich_help_panel="Verification")
def verify(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description for scoring")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: mcq, json")] = "mcq",
):
    """Multiple-choice verification selection for LLMs.

    Given changed files, presents up to 3 candidate verification files
    as a multiple-choice question the LLM can answer by selecting one.
    """
    from quale.reports import preflight_report

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    if not files:
        typer.echo("provide --files so verify stays file-scoped", err=True)
        raise typer.Exit(1)

    data = preflight_report(path=path, files=files, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    candidates = data.get("verification_candidates", data.get("verify_with", []))
    if not candidates:
        typer.echo("No verification candidates found.", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps({
            "schema_version": 1,
            "changed_files": data.get("changed_files", []),
            "verification_candidates": candidates,
            "task": task,
            "guardrails": {
                "mode": "report_only",
                "caveat": "Candidates are structural hints, not proof of coverage.",
            },
        }, indent=2))
        return

    # MCQ format (default) — designed for LLM consumption
    typer.echo("# Verification Candidates")
    typer.echo("Which file would verify this change? Select one.")
    typer.echo("")
    labels = ["A", "B", "C"]
    for i, candidate in enumerate(candidates):
        label = labels[i] if i < len(labels) else f"({i+1})"
        typer.echo(f"  {label}. {candidate}")
    typer.echo("")
    typer.echo('Return the label of the best candidate (e.g., "A").')


@core_app.command(name="reverse-verify",  rich_help_panel="Verification")
def reverse_verify(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed test file(s); repeat or comma-separate")] = [],
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against working tree")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Given changed test files, find source files that need verification.

    Reverse bridge: when tests change, which sources should be rechecked?
    """
    from quale.reports import reverse_verify_report

    data = reverse_verify_report(path=path, files=files or None, diff_ref=diff)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    candidates = data.get("source_candidates", [])
    if not candidates:
        typer.echo("No source candidates found.")
        return
    typer.echo(f"Test files: {', '.join(data.get('test_files', []))}")
    typer.echo(f"Confidence: {data['confidence']}")
    typer.echo("Source candidates:")
    for c in candidates[:5]:
        typer.echo(f"  {c['path']}  ({c['reason']})")


@core_app.command(name="verify-classify",  rich_help_panel="Verification")
def verify_classify(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against working tree")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Classify each changed file's verifiability type and structural gaps."""
    from quale.reports import verify_classify_report
    data = verify_classify_report(path=path, files=files or None, diff_ref=diff)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    for fc in data.get("changed_files", []):
        gap = fc.get("gap_type") or "—"
        conf = fc.get("confidence", "?")
        typer.echo(f"  {fc['file']:45s} verifiability={fc['verifiability']:15s} gap={gap:20s} confidence={conf}")
    for v in data.get("vaccination", []):
        typer.echo(f"  🧬 {v}")


@core_app.command(name="verify-bonds",  rich_help_panel="Verification")
def verify_bonds(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Detect when a change requires running multiple test files together."""
    from quale.reports import covalent_verify_bonds
    data = covalent_verify_bonds(path=path, files=files or None)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    for b in data.get("bonds", []):
        typer.echo(f"  Bond: {b['tests'][0]}  ↔  {b['tests'][1]}  (overlap={b['combined_vocab_overlap']})")
    if not data.get("bonds"):
        typer.echo("No bonded test pairs found.")


@core_app.command(name="verify-drift",  rich_help_panel="Verification")
def verify_drift(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    commits: Annotated[int, typer.Option("--commits", "-n", help="Commits to inspect")] = 10,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Track verification confidence across recent commits."""
    from quale.reports import verification_drift
    data = verification_drift(path=path, commits=commits)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    for pt in data.get("series", []):
        marker = "⬇" if pt.get("alerts") else " "
        typer.echo(f"  {pt['commit']:12s} {pt['confidence']:8s} cand={pt['candidate_count']:2d} mirror={pt['mirror_ratio']:.2f} {marker}")
    for a in data.get("alerts", []):
        typer.echo(f"  ⚠ {a}")
    if not data.get("alerts"):
        typer.echo("  No drift detected.")


@core_app.command(name="test-gaps",  rich_help_panel="Verification")
def deserts(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    top: Annotated[int, typer.Option("--top", "-n", help="Max desert rows")] = 20,
):
    """Test gap map: source files with weak test mirrors.

    This is structural mirror analysis, not coverage proof.
    """
    from quale.reports import verification_deserts

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = verification_deserts(path, max_results=top)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    ratio = data.get("mirror_ratio", 0.0)
    ratio_color = "green" if ratio >= 0.7 else ("yellow" if ratio >= 0.3 else "red")
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  TEST COVERAGE REPORT", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Source files: {data.get('source_files', 0)}  Test files: {data.get('test_files', 0)}")
    typer.echo(f"  Test mirror coverage: {c(f'{ratio:.0%}', ratio_color)}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")
    for item in data.get("deserts", [])[:top]:
        score = item.get("score", 0.0)
        color = "red" if score >= 0.75 else "yellow"
        typer.echo(f"    {c(f'{score:.2f}', color)}  {item['file']}  {c(item.get('reason', ''), 'gray')}")
    if not data.get("deserts"):
        typer.echo(c("    No strong verification deserts found.", "green"))
    typer.echo("")


@core_app.command(name="co-change",  rich_help_panel="Verification")
def entangle(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    lookback: Annotated[int, typer.Option("--lookback", "-n", help="Commits to scan")] = 200,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    target: Annotated[str | None, typer.Option("--target", help="Show only pairs involving this file")] = None,
):
    """Show file co-change pairs from git history.

    Entangled files share no vocabulary but are frequently committed together.
    Bridges the structural gap where phrase-matching fails.
    """
    from quale.reports import entanglement_matrix
    data = entanglement_matrix(path=path, lookback_commits=lookback)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    pairs = data.get("pairs", [])
    if target:
        pairs = [p for p in pairs if target in (p["file_a"], p["file_b"])]
    if not pairs:
        typer.echo(f"No entangled pairs found ({data.get('note', '')})")
        return
    typer.echo(f"Top entangled pairs (scanned {data['total_commits_scanned']} commits):")
    for p in pairs[:20]:
        marker = " ⬅" if target in (p["file_a"], p["file_b"]) else ""
        typer.echo(f"  {p['file_a']:45s} ↔ {p['file_b']:45s}  count={p['co_change_count']:3d} prob={p['co_change_probability']:.2f}{marker}")


@core_app.command(name="cascade-verify", rich_help_panel="Agent Safety")
def cascade_verify_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    why: Annotated[bool, typer.Option("--why", help="Show cascade trace")] = False,
):
    """Multi-strategy verification pipeline.

    Tier 1: Cohesion check (0 tokens) — high cohesion = safe to skip LLM.
    Tier 2: Memory B-Cell cache (0 tokens) — same content hash reuses past outcome.
    Tier 3: Deterministic skip (0 tokens) — stem match + cohesion ≥ 0.7.
    Tier 4: Forced-choice binary decision tree (~400-900 tokens).

    On steady state, ~77% of calls hit Tiers 1-3 (0 tokens).
    """
    from quale.reports import cascade_verify
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not files:
        typer.echo("provide --files", err=True)
        raise typer.Exit(1)
    data = cascade_verify(path=path_abs, changed_files=list(files))
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    tier = data.get("tier", "unknown")
    det = data.get("deterministic_verify", {})
    coh = data.get("cohesion_label", "?")
    if tier == "deterministic":
        typer.echo(f"Cascade: {tier}  |  Cohesion: {coh}  |  Verify: {det.get('file', '?')}  |  Token cost: 0")
    elif tier == "desert":
        note = data.get("desert_note", "no candidates")
        typer.echo(f"Cascade: {tier}  |  Cohesion: {coh}  |  Token cost: 0  |  {note}")
    else:
        cands = data.get("verification_candidates", [])
        typer.echo(f"Cascade: llm_forced_choice  |  Cohesion: {coh}  |  Candidates: {len(cands)}")
        for c in cands[:4]:
            typer.echo(f"  {c}")
        typer.echo("  Token cost: ~400-900 (forced choice binary tree)")
    if why:
        typer.echo(f"  Cohesion score: {data.get('cohesion', '?')} "
                    f"{'(safe to skip LLM)' if data.get('cohesion', 0) >= 0.7 else '(needs LLM)'}")


@core_app.command(name="veto-cascade", rich_help_panel="Agent Safety")
def veto_cascade_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s)")] = [],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Veto cascade pipeline — ~33 avg tokens per verification call.

    Tier 1: Cohesion + B-cell (0 tokens)
    Tier 2: Veto prompt (~200 tokens)
    Tier 3: Progressive resolution (~42 tokens)
    """
    from quale.reports import veto_cascade
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not files:
        typer.echo("provide --files", err=True)
        raise typer.Exit(1)
    data = veto_cascade(path=path_abs, changed_files=list(files))
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    tier = data.get("tier", "?")
    veto_t = data.get("veto_tier", "?")
    coh = data.get("cohesion_label", "?")
    tok = data.get("token_cost", "?")
    if tier == "deterministic" or tier == "desert":
        typer.echo(f"Veto cascade: {tier}  Tier: {veto_t}  Cohesion: {coh}  Token cost: {tok}")
    elif tier == "veto":
        top = data.get("deterministic_verify", {}).get("file", "")
        typer.echo(f"Veto cascade: veto_prompt  Target: {top}  Tokens: {tok}  Cohesion: {coh}")
    else:
        cands = data.get("verification_candidates", [])
        typer.echo(f"Veto cascade: progressive  Candidates: {len(cands)}  Tokens: {tok}  Cohesion: {coh}")


@core_app.command(name="isolate", rich_help_panel="Agent Safety")
def isolate_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    task: Annotated[str, typer.Option("--task", "-t", help="Task description")] = "",
    turn: Annotated[int, typer.Option("--turn", help="Which module to evaluate (0-based)")] = 0,
    active_days: Annotated[int, typer.Option("--active-days", help="Only consider modules active in N days")] = 0,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    why: Annotated[bool, typer.Option("--why", help="Show why this module was ranked here")] = False,
):
    """Pre-edit file discovery via structural module bisection.

    Scores module clusters by task-keyword overlap. Each turn presents
    one module for YES/NO confirmation. ~100 tokens per turn.
    """
    from quale.reports import isolate_modules, _active_gene_pool
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not task:
        typer.echo("provide --task", err=True)
        raise typer.Exit(1)
    data = isolate_modules(path=path_abs, task=task)
    if active_days > 0:
        pool = _active_gene_pool(path_abs, active_days)
        for mod in data.get("modules", []):
            mod["files"] = [f for f in mod.get("files", []) if f in pool]
        data["modules"] = [m for m in data.get("modules", []) if m.get("files")]
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    mods = data.get("modules", [])
    if not mods:
        typer.echo("No structural modules found for this repo.", err=True)
        raise typer.Exit(1)
    if turn >= len(mods):
        typer.echo(f"Turn {turn} exceeds available modules ({len(mods)}). Try --turn 0 through {len(mods)-1}.", err=True)
        raise typer.Exit(1)
    module = mods[turn]
    from quale.formats.llm import format_isolate_confirm
    prompt = format_isolate_confirm(task, module, turn)
    if format == "json":
        typer.echo(json.dumps({
            "schema_version": 1,
            "tier": "bisection",
            "turn": turn,
            "total_modules": len(mods),
            "task": task,
            "task_keywords": data.get("task_keywords", []),
            "flat_wave": data.get("flat_wave"),
            "entanglement_injection": data.get("entanglement_injection"),
            "token_cost": "~100",
            "llm_prompt": prompt,
            "module_scores": [{ "score": m["match_score"], "size": m["size"], "overlap": m["overlap_count"] } for m in mods],
        }, indent=2))
        return
    typer.echo(_color("STRUCTURAL BISECTION", "header"))
    typer.echo(f"Task: {task}")
    typer.echo(f"Turn {turn}/{len(mods)-1} | Module match score: {module['match_score']:.3f} | {module['size']} files")
    files = module.get("files", [])
    if files:
        typer.echo(_color(f"Files ({len(files)}):", "subheader"))
        for f in files[:6]:
            typer.echo(f"  {f}")
    ph = module.get("exemplar_phrases", [])
    if ph:
        typer.echo(f"Key concepts: {', '.join(ph[:5])}")
    typer.echo("")
    if why and turn > 0:
        prev = mods[turn - 1]
        typer.echo(f"Previous module score: {prev['match_score']:.3f} (difference: {module['match_score'] - prev['match_score']:+.3f})")
    typer.echo(_color("Present this module for LLM confirmation:", "dim"))
    typer.echo(prompt)


@core_app.command(name="fold", rich_help_panel="Agent Safety")
def fold_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to fold")] = "",
    task: Annotated[str, typer.Option("--task", "-t", help="Task description")] = "",
    threshold: Annotated[float, typer.Option("--threshold", help="Minimum score to keep a block")] = 0.02,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Replace low-signal blocks with annotations.

    Indentation-aware block folding preserves syntax while removing
    structural noise. 40-80% token reduction on large files.

    Example:
      quale fold --file src/billing.ts --task 'fix proration'
    """
    from quale.fold import fold_file
    path_abs = os.path.abspath(path)
    data = fold_file(path=path_abs, file_path=file, task=task, threshold=threshold)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    from quale.formats.llm import format_folded_file
    typer.echo(format_folded_file(data))


@core_app.command(name="drift-check", rich_help_panel="CI")
def drift_check_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to snapshot or check")] = "",
    snapshot: Annotated[bool, typer.Option("--snapshot", help="Take initial baseline snapshot")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Structural anomaly velocity across directories.

    Takes vocabulary snapshots per-file. On check, compares current
    state against baseline and alerts on velocity spikes.

    Example:
      quale drift-check --file src/billing.ts --snapshot
      quale drift-check --file src/billing.ts
    """
    from quale.reports import drift_velocity_snapshot
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True)
        raise typer.Exit(1)
    data = drift_velocity_snapshot(path=path_abs, files=[file], snapshot=snapshot)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if snapshot:
        typer.echo(f"Drift baseline saved for {file} ({data.get('phrases_captured', 0)} phrases)")
        return
    vel = data.get("velocity", 0)
    anomalies = data.get("anomalies", [])
    if anomalies:
        for a in anomalies[:5]:
            typer.echo(f"  IMU: {a}")
    else:
        typer.echo(f"Drift stable. Velocity: {vel:.3f}")


@core_app.command(name="latent-deps", rich_help_panel="Code Analysis")
def mycorrhiza_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="File(s) to map hidden deps for")] = [],
    active_days: Annotated[int, typer.Option("--active-days", help="Only analyze files modified in N days (active gene pool)")] = 0,
    tolerance: Annotated[bool, typer.Option("--tolerance", help="Tolerance Gaging: check if edit introduces vocabulary outside historical cluster radius")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Detect hidden structural dependencies (no direct imports).

    Files that share rare vocabulary AND co-change in git history
    despite having zero import/require/include relationships.

    Use --tolerance to detect when an edit introduces vocabulary
    from clusters the target file has never historically touched.
    """
    from quale.reports import mycorrhiza_map, mycorrhiza_with_tolerance, _active_gene_pool
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not files:
        typer.echo("provide --files", err=True)
        raise typer.Exit(1)
    if active_days > 0:
        pool = _active_gene_pool(path_abs, active_days)
        files = [f for f in files if f in pool]
        if not files:
            typer.echo("No files in active gene pool.", err=True)
            raise typer.Exit(1)
    data = mycorrhiza_with_tolerance(path=path_abs, files=list(files)) if tolerance else mycorrhiza_map(path=path_abs, files=list(files))
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    for f in data.get("files", []):
        count = f.get("count", 0)
        label = _color(f"  {count} hidden deps", "yellow") if count > 0 else _color("  no hidden deps", "green")
        typer.echo(f"{f['file']}: {label}")
        for dep in f.get("hidden_dependencies", [])[:3]:
            conf = dep.get("confidence", "moderate")
            clr = "yellow" if conf == "moderate" else "red"
            typer.echo(f"    -> {dep['file']}  [{_color(conf, clr)}] shared: {', '.join(dep['shared_rare_terms'][:3])}")
        if tolerance and f.get("tolerance", {}).get("violations"):
            for v in f["tolerance"]["violations"]:
                typer.echo(f"    {_color('TOLERANCE VIOLATION:', 'red')} {v}")


@core_app.command(name="solve", rich_help_panel="Maintenance")
def solve_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    top_n: Annotated[int, typer.Option("--top", help="Number of cipher keys to extract")] = 20,
    focus: Annotated[str, typer.Option("--focus", help="Gravitational Lensing: filter cipher keys to those orbiting a specific concept")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Surface cipher keys: non-dictionary identifiers to learn a repo."""

    from quale.reports import solve_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = solve_report(path=path_abs, top_n=top_n, focus=focus)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    total_phrases = data.get("total_phrases", 0)
    total_keys = len(data.get("bimoth_index", []))
    if focus:
        typer.echo(f"Top identifiers related to \033[36m{focus}\033[0m \u2014 {total_keys} keys, {len(data.get('orbiting_files',[]))} files")
    else:
        typer.echo(f"Top {total_keys} non-dictionary identifiers across {total_phrases} total phrases in repo:")
    for i, p in enumerate(data.get("bimoth_index", [])[:8]):
        typer.echo(f"  {i+1}. \033[33m{p['phrase']}\033[0m (appears {p['frequency']} times) \u2014 e.g. {', '.join(p['top_files'][:2])}")


@core_app.command(name="deflate", rich_help_panel="Maintenance")
def deflate_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to deflate")] = "",
    diff: Annotated[str, typer.Option("--diff", help="Git ref")] = "",
    budget: Annotated[int, typer.Option("--budget", help="Token budget")] = 5,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
) -> None:
    """Cap net-new identifiers per edit."""

    from quale.reports import deflate_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file or not diff:
        typer.echo("provide --file <path> and --diff <ref> (e.g. --file src/main.go --diff HEAD~1)", err=True)
        raise typer.Exit(1)
    data = deflate_report(path=p, file_path=file, proposed_diff=diff, budget=int(budget))
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    used = data.get("net_new_count", 0)
    bud = data.get("budget", 5)
    if data.get("over_budget"):
        typer.echo(f'  {_color("INFLATION DETECTED", "red")}')
        typer.echo(f'  Budget: {bud}, Used: {used}, Over by: {used - bud}')
        typer.echo(f'  Violations: {", ".join(data.get("net_new_identifiers",[])[:bud+3])}')
    else:
        typer.echo(f'  Gold Standard OK — {used}/{bud} net-new identifiers used.')


@core_app.command(name="forecast", rich_help_panel="CI")
def forecast_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s) to forecast risk for")] = [],
    commits: Annotated[int, typer.Option("--commits", help="Git history lookback")] = 500,
    active_days: Annotated[int, typer.Option("--active-days", help="Only analyze files modified in N days (active gene pool)")] = 0,
    seismic: Annotated[bool, typer.Option("--seismic", help="S-Wave mode: exclude P-wave files, isolate latent regression risks")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Forecast regression risk from co-change shifts.

    Scans git history for bugfix commits. For each file changed,
    emits historically bug-prone neighbors with regression probability.
    Zero token cost. All computation from git history.

    Example:
      quale forecast --files src/billing.ts
    """
    from quale.reports import forecast_report, _active_gene_pool
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not files:
        typer.echo("provide --files", err=True)
        raise typer.Exit(1)
    if active_days > 0:
        pool = _active_gene_pool(path_abs, active_days)
        files = [f for f in files if f in pool]
        if not files:
            typer.echo("No files in active gene pool. Try --active-days 0 or increase the window.", err=True)
            raise typer.Exit(1)
    data = forecast_report(path=path_abs, files=list(files), lookback_commits=commits, seismic=seismic)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    for res in data.get("files", []):
        note = res.get("note", "")
        if note:
            typer.echo(f"{res['file']}: {note}")
            continue
        bc = res.get("bugfix_count", 0)
        top = res.get("highest_probability", 0)
        label = _color(f"  regr: {top:.0%}  |  bugfixes: {bc}", "yellow" if top >= 0.3 else "green")
        typer.echo(f"{res['file']}: {label}")
        for n in res.get("neighbors", [])[:3]:
            prob = n.get("probability", 0)
            clr = "red" if prob >= 0.5 else "yellow"
            typer.echo(f"    -> {n['file']}  [{_color(f'{prob:.0%}', clr)}] ({n['co_bugfix_count']}×)")
    if seismic:
        typer.echo(_color("  Seismic mode: P-wave files excluded. Only S-wave risks shown.", "yellow"))


@core_app.command(name="triangulate", rich_help_panel="Agent Safety")
def triangulate_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    task: Annotated[str, typer.Option("--task", "-t", help="Task description")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Intersect three structural probes to find the task anchor.

    Runs 3 structural views (repo-map, recent diffs, distinctive identifiers)
    without reading source code. Collects 5 phrases per view. Computes
    overlap anchor. No source code sent to LLM.

    Example:
      quale triangulate --task 'fix billing proration'
    """
    from quale.reports import triangulate_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not task:
        typer.echo("provide --task", err=True)
        raise typer.Exit(1)
    data = triangulate_report(path=path_abs, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    anchor = data.get("anchor", [])
    conf = data.get("confidence", 1)
    conf_label = _color("HIGH", "green") if conf >= 3 else (_color("MEDIUM", "yellow") if conf >= 2 else _color("LOW", "red"))
    typer.echo(f"Target anchor: {', '.join(anchor)}  [{conf_label} confidence]")
    typer.echo(f"  Probe A (repo-map): {', '.join(data.get('probe_a', []))}")
    typer.echo(f"  Probe B (recent diffs): {', '.join(data.get('probe_b', []))}")
    typer.echo(f"  Probe C (distinctive ids): {', '.join(data.get('probe_c', []))}")



@core_app.command(name="concept-flow", rich_help_panel="Maintenance")
def epidemiology_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", help="History lookback")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Track phrase spread across weekly snapshots.

    Computes R0 for each phrase. Classifies as antigen (displacing debt),
    pathogen (spreading without displacement), or endemic (stable).

    Example:
      quale epidemiology --weeks 12
    """
    from quale.reports import epidemiology_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = epidemiology_report(path=path_abs, lookback_weeks=weeks)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    if data.get("pathogen_count", 0) > 0:
        typer.echo(f"Pathogens: {data['pathogen_count']}  Antigens: {data['antigen_count']}  Total tracked: {data['total_tracked']}")
        for p in data.get("phrases", [])[:5]:
            cls = p.get("class", "?")
            r0 = p.get("r0", 0)
            clr = "red" if cls == "pathogen" else ("green" if cls == "antigen" else "white")
            label = p.get('phrase', '?')[:50]
            typer.echo(f"  [{_color(cls.upper(), clr)}] R0={r0:+.2f}  {label}")
    else:
        typer.echo(f"All stable. {data['total_tracked']} phrases tracked.")


@core_app.command(name="orient", rich_help_panel="Utilities")
def orient_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    task: Annotated[str, typer.Option("--task", help="Task description")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
) -> None:
    """One-call orientation: solve + triangulate + isolate."""

    from quale.reports import pipeline_orient
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not task:
        typer.echo("provide --task", err=True)
        raise typer.Exit(1)
    data = pipeline_orient(path=p, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo(f"Cipher keys: {', '.join(data.get('cipher_keys', [])[:5])}")
    typer.echo(f"Anchor: {', '.join(data.get('anchor', []))}")
    for m in data.get("recommended_modules", [])[:2]:
        typer.echo(f'  Module: {", ".join(m.get("exemplars",[])[:3])} ({m.get("match_score",0):.0%})')
    typer.echo(f'  {data.get("total_files_in_scope",0)} files in scope')


@core_app.command(name="health", rich_help_panel="CI")
def health_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    balance: Annotated[bool, typer.Option("--balance", help="Root-to-shoot ratio check")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """0-1 health from stability, mirror, churn, concept age. """
    from quale.reports import structural_health_score
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = structural_health_score(path=p, balance=balance)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    h = data.get("health", "?")
    c = "green" if h == "good" else ("yellow" if h == "moderate" else "red")
    if h == "good":
        summary = "Good structural health \u2014 low coupling, good modularity."
    elif h == "moderate":
        summary = "Moderate \u2014 some coupling debt, refactoring may help."
    else:
        summary = "Poor \u2014 high coupling or weak modularity. Consider refactoring."
    typer.echo(f"Health: {_color(h.upper(), c)} (debt: {data.get('debt_acceleration',0):.3f})")
    typer.echo(f"  {summary}")
    if balance and data.get("root_shoot_ratio"):
        ratio = data["root_shoot_ratio"]
        clr = "red" if ratio > 3 else ("green" if ratio < 0.5 else "yellow")
        typer.echo(f"  Root/Shoot ratio: {ratio}:1 [{_color('Features outgrowing core' if ratio > 3 else 'Core dominates' if ratio < 0.5 else 'Balanced', clr)}]")
    elif balance:
        typer.echo(f"  {data.get('phototropism_note', '')}")


@core_app.command(name="heisenberg", rich_help_panel="Maintenance")
def heisenberg_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to check")] = "",
    diff: Annotated[str, typer.Option("--diff", help="Proposed diff ref")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Mixed refactor/feature edits that must be split."""

    from quale.reports import heisenberg_check
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file or not diff:
        typer.echo("provide --file <path> and --diff <ref> (e.g. --file src/main.go --diff HEAD~1)", err=True)
        raise typer.Exit(1)
    data = heisenberg_check(path=p, file_path=file, proposed_diff=diff)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if data.get("uncertainty_violated"):
        typer.echo(f'  \033[31m\u26a0 Mixed change detected\033[0m')
        typer.echo(f'  New identifiers: {", ".join(data.get("new_signal_tokens", [])[:3])}')
        typer.echo(f'  Deleted identifiers: {", ".join(data.get("deleted_anchors", [])[:3])}')
        typer.echo(f'  {data.get("mandate","")}')
        typer.echo('  \033[90mSuggestion: split this change into separate refactor + feature edits.\033[0m')
    else:
        typer.echo(f'  \033[32m\u2713 Change is focused\033[0m \u2014 no mixed refactor/feature detected.')


@core_app.command(name="traffic-control", rich_help_panel="Maintenance")
def traffic_control_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to check")] = "",
    intended_import: Annotated[str, typer.Option("--intended-import", help="Intended import to verify")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Zone files by graph centrality percentile."""

    from quale.reports import traffic_control_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file or not intended_import:
        typer.echo("provide --file and --intended-import", err=True)
        raise typer.Exit(1)
    data = traffic_control_report(path=p, file_path=file, intended_import=intended_import)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    src = data.get("source_zone", "?")
    dst = data.get("import_zone", "?")
    if data.get("zoning_violation"):
        typer.echo(f'  {_color("ZONING VIOLATION", "red")}')
        typer.echo(f'  {data.get("source_file","")} ({src}) -> {data.get("intended_import","")} ({dst})')
        typer.echo(f'  {data.get("mandate","")}')
    else:
        typer.echo(f'  Import route clear. ({src} -> {dst})')


@core_app.command(name="capillary", rich_help_panel="Code Analysis")
def capillary_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Files with the most inter-file vocabulary edges."""

    from quale.reports import capillary_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = capillary_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    caps = data.get("capillaries", [])[:5]
    typer.echo(f"Files with the most inter-file connections ({len(caps)} shown):")
    for c in caps:
        typer.echo(f'  \u25cf {c["file"]} \u2014 {c["edges"]} shared vocabulary links')

@core_app.command(name="spectral-gap", rich_help_panel="Code Analysis")
def spectral_gap_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Modularity score: largest cluster / second largest."""

    from quale.reports import spectral_gap_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = spectral_gap_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    g = data.get("spectral_gap", 0)
    m = data.get("modularity", "?")
    if g >= 3.0:
        typer.echo(f"Module separation: high \u2014 largest cluster is {g}x the second largest")
        typer.echo("  \u2192 Check for a monolith. One module dominates vocabulary.")
    elif g >= 1.5:
        typer.echo(f"Module separation: moderate (gap={g})")
    else:
        typer.echo(f"Module separation: flat (gap={g})")
        typer.echo("  \u2192 Vocabulary is evenly distributed. No dominant cluster.")

@core_app.command(name="phantom", rich_help_panel="Code Analysis")
def phantom_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Detect framework/library from import/export vocabulary."""

    from quale.reports import phantom_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = phantom_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    d = data.get("frameworks_detected", {})
    typer.echo(f"Frameworks detected from import vocabulary:")
    if d:
        for k, v in sorted(d.items(), key=lambda x: -x[1])[:5]:
            typer.echo(f'  \u25cf {k} \u2014 {v} files')
    else:
        typer.echo('  None detected')


@core_app.command(name="parity-bit", rich_help_panel="CI")
def parity_bit_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    ref_a: Annotated[str, typer.Option("--ref-a", help="Base ref")] = "",
    ref_b: Annotated[str, typer.Option("--ref-b", help="Head ref")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """SHA-1 of module phrase set. [GATE: CHANGED vs UNCHANGED]"""
    from quale.reports import parity_bit_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not ref_a or not ref_b:
        typer.echo("provide --ref-a and --ref-b (e.g. --ref-a HEAD~1 --ref-b HEAD)", err=True)
        raise typer.Exit(1)
    data = parity_bit_report(path=p, ref_a=ref_a, ref_b=ref_b)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    u = data.get("mirror_unchanged", False)
    typer.echo(f'Mirror {"UNCHANGED" if u else "CHANGED"}')


@core_app.command(name="guide", rich_help_panel="Agent Safety")
def guide_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to analyze")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """One-token file locator for a file. """
    from quale.reports import guide_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True)
        raise typer.Exit(1)
    data = guide_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    g = data.get("guide", "")
    c = data.get("confidence", "")
    typer.echo(f"{g} [{c}]")


@core_app.command(name="decay", rich_help_panel="Maintenance")
def decay_cmd(path=".", file="", weeks=12, half_life=30,
              metabolism: Annotated[bool, typer.Option("--metabolism", help="Active Metabolism: verify pattern declining repo-wide")] = False,
              format="compact"):
    """Legacy patterns; --metabolism for active decline."""

    from quale.reports import decay_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True)
        raise typer.Exit(1)
    data = decay_report(path=p, file_path=file, lookback_weeks=weeks, half_life_days=half_life, active_metabolism=metabolism)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    dp = data.get("decaying_patterns", [])
    if dp:
        typer.echo(f'{_color("TOXICITY CLEARANCE REQUIRED", "red")}')
        for d in dp[:5]:
            typer.echo(f'  {d["phrase"]} -> {d["replacement"]}')
        typer.echo(f'  Mandate: {data.get("mandate","")}')
    else:
        typer.echo(f'{data.get("file","")}: clean — no decaying patterns')


@core_app.command(name="entropy", rich_help_panel="Maintenance")
def entropy_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", help="History lookback")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Dir-level vocabulary fragmentation vs 30-commit baseline.

    Measures vocabulary cluster dispersion per directory. When entropy
    exceeds the 30-commit rolling baseline, the limit is tripped.

    Example:
      quale entropy --weeks 12
    """
    from quale.reports import isothermal_entropy
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = isothermal_entropy(path=path_abs, lookback_weeks=weeks)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    typer.echo(f"Directory vocabulary spread vs. rolling baseline:")
    if data.get("any_limit_exceeded"):
        typer.echo(f"  \u26a0 Some directories have unusually high vocabulary fragmentation.")
    for d in data.get("directories", [])[:10]:
        exceeded = d["limit_exceeded"]
        mark = "\u25cf" if exceeded else "\u25cb"
        note = " (fragmented)" if exceeded else ""
        typer.echo(f"  {mark} {d['directory']:35s} spread={d['entropy']:.2f} baseline={d['baseline']:.2f}{note}")


@core_app.command(name="zk-proof", rich_help_panel="Agent Safety")
def zk_proof_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="Schema file (source of truth)")] = "",
    code: Annotated[str, typer.Option("--code", help="LLM-generated code to verify")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Verify generated code identifiers against allowed set.

    Extracts identifiers from the schema file. Scans generated code.
    Rejects any identifier not in the allowed set with alternatives.

    Example:
      quale zk-proof --file db/types.ts --code 'const q = db.query(...)'
    """
    from quale.reports import zk_proof_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file or not code:
        typer.echo("provide --file and --code", err=True)
        raise typer.Exit(1)
    data = zk_proof_report(path=path_abs, schema_file=file, generated_code=code)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    if data.get("passed"):
        typer.echo(_color("ZK-PROOF PASSED", "green"))
        typer.echo(f"  All {data['code_identifiers']} identifiers valid against {data['allowed_count']}-item vocabulary.")
    else:
        typer.echo(_color("ZK-PROOF FAILED", "red"))
        data.get("violation_count", 0)
        for v in data.get("violations", [])[:5]:
            alts = ", ".join(v.get("allowed_alternatives", [])[:2])
            typer.echo(f"  '{v['identifier']}' not in schema. Did you mean: {alts}?")


@core_app.command(name="safe-islands", rich_help_panel="Maintenance")
def lagrange_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to analyze")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Structurally isolated blocks safe to edit.

    Finds blocks with zero co-occurrence edges to the file's primary
    clusters. Editing these blocks has zero blast radius.

    Example:
      quale lagrange --file legacy.ts
    """
    from quale.reports import lagrange_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True)
        raise typer.Exit(1)
    data = lagrange_report(path=path_abs, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    pts = data.get("lagrange_points", [])
    if pts:
        typer.echo(f"Safe injection sites found: {len(pts)}")
        for p in pts[:3]:
            typer.echo(f"  Lines {p['start']}-{p['end']} ({p['lines']} lines, {p['identifier_count']} identifiers)")
            typer.echo(f"    {p['code'][:80]}...")
    else:
        note = data.get("note", "none found")
        typer.echo(f"No safe injection sites found: {note}")


@core_app.command(name="migration-pairs", rich_help_panel="Maintenance")
def phase_shift_cmd(
    repo_a: Annotated[str, typer.Option("--repo-a", help="Pre-migration repo path")] = "",
    repo_b: Annotated[str, typer.Option("--repo-b", help="Post-migration repo path")] = "",
    min_freq: Annotated[int, typer.Option("--min-freq", help="Minimum frequency to include")] = 2,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Deterministic phrase substitution from two-repo comparison.

    Scans two repos (pre/post migration). Extracts phrase-level delta.
    Output is a deterministic replacement task: apply these substitutions.

    Example:
      quale phase-shift --repo-a ./pre-migration --repo-b ./post-migration
    """
    if not repo_a or not repo_b:
        typer.echo("provide --repo-a and --repo-b", err=True)
        raise typer.Exit(1)
    try:
        from quale.reports import phase_shift_report
    except ImportError:
        typer.echo("phase-shift report not available (function removed)", err=True)
        raise typer.Exit(1)
    data = phase_shift_report(path_a=repo_a, path_b=repo_b, min_freq=min_freq)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    typer.echo(data.get("mask_summary", ""))
    for s in data.get("substitutions", [])[:10]:
        typer.echo(f"  {s['from'][:40]} -> {s['to'][:40]}")



@core_app.command(name="verify-packet",  rich_help_panel="Agent Safety")
def cartridge(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against working tree")] = None,
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: tool(default), json, compact")] = "tool",
    why: Annotated[bool, typer.Option("--why", help="Show why each candidate exists")] = False,
):
    """Verification packet — compressed scope for LLM verification.
    
    Examples:
      quale verify-packet --files src/spool.ts
      quale verify-packet --files src/spool.ts --why
      quale verify-packet --files src/spool.ts --format json
    """
    from quale.reports import cartridge_report
    data = cartridge_report(path=path, files=files or None, diff_ref=diff, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "tool":
        tool_data = {
            "schema_version": 1,
            "tier": data.get("tier", "unknown"),
            "confidence": data.get("confidence", "—"),
            "verification_candidates": data.get("verification_candidates", []),
            "entangled_candidates": data.get("entangled_candidates", []),
            "negative_scope": data.get("negative_scope", []),
            "deterministic_verify": data.get("deterministic_verify"),
            "desert_note": data.get("desert_note"),
            "verification_confidence": data.get("verification_confidence", {}),
            "_agent_note": "--files takes comma-separated paths, not repeated flags",
        }
        typer.echo(json.dumps(tool_data, separators=(",", ":")))
        return
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    tier = data.get("tier", "unknown")
    conf = data.get("confidence", "—")
    stop = data.get("stop_after", "—")
    if tier == "deterministic":
        det = data.get("deterministic_verify", {})
        typer.echo(f"Tier: deterministic  |  Verify: {det.get('file', '?')} (score={det.get('score', 0):.2f})")
    elif tier == "desert":
        typer.echo(f"Tier: desert  |  {data.get('desert_note', 'no structural candidates')}")
    else:
        typer.echo(f"Tier: {tier}  |  Confidence: {conf}  |  Stop-after: {stop}")
    if data.get("verification_candidates"):
        typer.echo("Verify candidates:")
        for c in data["verification_candidates"]:
            typer.echo(f"  {c}")
    if data.get("entangled_candidates"):
        typer.echo("Entangled candidates:")
        for e in data.get("entangled_candidates", []):
            reason = e.get('reason', '') or f"prob={e.get('score',0):.2f}"
            typer.echo(f"  {e['file']}  ({reason})")
    if data.get("negative_scope"):
        typer.echo("Avoid editing:")
        for f in data["negative_scope"]:
            typer.echo(f"  {f}")
    if data.get("desert"):
        typer.echo(f"Desert: {data.get('desert_note', 'no candidates')}")
    if why:
        from quale.formats.terminal import _why_verify_packet
        typer.echo(_why_verify_packet(data))


@core_app.command(name="check-diff", rich_help_panel="CI")
def check_diff(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    diff_ref: Annotated[str, typer.Option("--diff", help="Git ref to diff against (default HEAD~1)")] = "HEAD~1",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    fail_on_defect: Annotated[str | None, typer.Option("--fail-on-defect", help="Fail on given severity (low/moderate/high)")] = None,
):
    """Post-proposal defect scan: detect structural violations.
    
    Checks for stable anchor edits, generated file edits, mirror weakening,
    and large change sets. Report-only by default — use --fail-on-defect
    to enforce a minimum severity threshold in CI.
    """
    from quale.reports import check_diff_report
    data = check_diff_report(path=path, diff_ref=diff_ref)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if not data.get("defects"):
        typer.echo("No structural defects detected.")
        return
    for d in data["defects"]:
        severity = d.get("severity", "low")
        marker = "🔴" if severity == "high" else ("🟡" if severity == "moderate" else "⚪")
        typer.echo(f"  {marker} [{severity}] {d['type']}: {d['file']}  — {d.get('detail', '')}")
    if fail_on_defect:
        levels = {"low": 1, "moderate": 2, "high": 3}
        threshold = levels.get(fail_on_defect, 0)
        max_sev = data.get("max_severity", "none")
        if levels.get(max_sev, 0) >= threshold:
            typer.echo(f"  FAIL: max severity {max_sev} >= threshold {fail_on_defect}", err=True)
            raise typer.Exit(1)


@core_app.command(rich_help_panel="Agent Safety")
def route(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    task: Annotated[str | None, typer.Option("--task", "-t", help="Task description")] = None,
    files: Annotated[list[str] | None, typer.Option("--files", help="Known target files; repeat or comma-separate")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Route intervention tier: none / verify / contract / human.

    Routes trivial changes past the LLM, uses verify-packet for standard
    verification, escalates to contract for risky changes, and flags
    test gaps for human review.
    """
    from quale.reports import route_recommendation

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = route_recommendation(path, task=task, files=files)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    action = data.get("action", "unknown")
    color = "green" if action == "verify" else ("yellow" if action == "contract" else "gray")
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VOCAB ROUTE", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Action: {c(action, color)}")
    typer.echo(f"  Tier: {c(action, color)}")
    if data.get("command"):
        typer.echo(f"  Command: {c(' '.join(data['command']), 'green')}")
    for reason in data.get("reasons", []):
        typer.echo(f"  Why: {c(reason, 'gray')}")
    for warning in data.get("warnings", []):
        typer.echo(f"  Warning: {c(warning, 'yellow')}")
    typer.echo("")


@core_app.command(rich_help_panel="Utilities")
def clone(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    threshold: Annotated[float, typer.Option("--threshold", "-t", help="Similarity threshold (0-1)")] = 0.85,
    min_files: Annotated[int, typer.Option("--min-files", "-m", help="Minimum files per clone group")] = 2,
):

    """Structural clone detection via fingerprint overlap."""
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


@core_app.command(rich_help_panel="Utilities")
def landmarks(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 10,
):

    """Phrases that distinguish a file from all others."""
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


@core_app.command(rich_help_panel="History")
def timeline(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):

    """Track phrases through git history."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = concept_timeline(path, weeks=weeks)
    if not data:
        typer.echo("No timeline data available.")
        return

    def c(t, color):
        return _color(t, color)

    if format == "json":
        typer.echo(json.dumps({
            "schema_version": 1,
            "weeks": weeks,
            "timeline": data,
        }, indent=2))
        return
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


@core_app.command(rich_help_panel="History")
def stable(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 12,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 20,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    """Stability anchors: files with highest/lowest phrase persistence.

    High-persistence files barely change — they're stable core infrastructure.
    Low-persistence files are churn hotspots — they change every week.
    """
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = compute_stability(path, weeks=weeks)
    if not data:
        if format == "json":
            typer.echo(json.dumps({
                "schema_version": 1,
                "stability_anchors": [],
                "churn_hotspots": [],
                "total_files": 0,
                "weeks": weeks,
            }))
        else:
            typer.echo("Not enough snapshot data.")
        return

    def c(t, color):
        return _color(t, color)

    if format == "json":
        typer.echo(json.dumps({
            "schema_version": 1,
            "stability_anchors": sorted([x for x in data if x["persistence"] >= 0.8], key=lambda x: -x["persistence"]),
            "churn_hotspots": sorted([x for x in data if x["persistence"] <= 0.3 and x["total_phrases"] >= 5], key=lambda x: x["persistence"]),
            "total_files": len(data),
            "weeks": weeks,
        }, indent=2))
        return

    # Anchors (top persistence)
    anchors = sorted(data, key=lambda x: -x["persistence"])[:limit]
    churn = sorted(data, key=lambda x: x["persistence"])[:limit]

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c(f"  STABILITY ANCHORS (last {weeks} weeks)", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo("")
    typer.echo(c("  STABLE FILES (barely change):", "subheader"))
    for item in anchors:
        if item["persistence"] >= 0.8:
            bar_n = int(item["persistence"] * 10)
            bar = "█" * bar_n + "░" * (10 - bar_n)
            per = item.get("persistence", 0)
            stable_ph = item.get("stable_phrases", 0)
            typer.echo("  " + bar + " " + c(f"{per:.0%}", "green") + f"  {item['file']:<55} " + c(f"({stable_ph} stable)", "gray"))

    typer.echo("")
    typer.echo(c("  CHURN HOTSPOTS (change every week):", "subheader"))
    for item in churn:
        if item["persistence"] <= 0.3 and item["total_phrases"] >= 5:
            bar_n = max(1, int((1 - item["persistence"]) * 10))
            bar = "░" * bar_n + "█" * (10 - bar_n)
            per = item.get("persistence", 0)
            avg_turn = item.get("avg_turnover", 0)
            typer.echo("  " + bar + " " + c(f"{per:.0%}", "red") + f"  {item['file']:<55} " + c(f"(turnover {avg_turn:.0%}/wk)", "gray"))

    typer.echo(c(f"\n  {len([x for x in data if x['persistence'] >= 0.8])} stable files, {len([x for x in data if x['persistence'] <= 0.3])} churn hotspots", "gray"))


@cli.command(rich_help_panel="Getting Started")
def explore(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    path_opt: Annotated[str, typer.Option("--path", "-p", help="Path to codebase", hidden=True)] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    themes: Annotated[bool, typer.Option("--themes", "-t", help="Also detect latent structural themes (slower)")] = False,
):
    """Onboarding map: best files to read first.

    Ranks files by vocabulary coverage — files with highest coverage
    contain the most representative concepts. Start here.

    With --themes, runs deeper analysis to discover conceptual groupings
    across the codebase.
    """
    if path_opt:
        path = path_opt
    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = explore_repo(path, themes=themes)
    files = data.get("files", [])
    if not files:
        typer.echo("No files found.")
        return

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  EXPLORE", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo("")
    typer.echo(c("  READ FIRST:", "subheader"))
    for f in files[:15]:
        score_str = f'{f["unique_score"]:6.1f}'
        typer.echo(f"    {c(f['language'], 'cyan'):<10} {c(score_str, 'green')}  {f['file']:<50}")
    typer.echo("")

    for theme in data.get("themes", [])[:3]:
        files_str = f"{theme['files']} files ({theme['variance_explained']:.0%})"
        typer.echo(f"    {c(theme['label'][:35], 'cyan'):<35} {c(files_str, 'yellow')}")
    if data.get("themes"):
        typer.echo("")


def _print_agent_checklist(data: dict, task: str | None):
    def c(t, col):
        return _color(t, col)

    reads = data.get("recommended_next_reads", [])
    related = data.get("related_files_for_task", [])
    bc = data.get("binding_concepts", [])
    likely = data.get("task_plan", {}).get("likely_edit_files", [])
    stability = data.get("avoid_touching_without_context", [])
    modules = data.get("module_boundaries", [])
    themes = data.get("themes", [])
    total_code = data.get("total_code_files", 0)

    source_related = [item for item in related if item.get("role") != "test"]
    test_related = [item for item in related if item.get("role") == "test"]
    first_task_read = source_related[0]["file"] if source_related else None
    first_edit = likely[0] if likely else None
    first_test = test_related[0]["file"] if test_related else None
    if first_task_read:
        for r in reads:
            if r["file"] != first_task_read:
                r["file"]
                break
    elif reads:
        reads[0]["file"]

    # Header
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  AGENT BOOTSTRAP — EXECUTABLE CHECKLIST", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    if task:
        relevance = data.get("task_relevance_score", 0)
        rl, rc, rr = _relevance_label(relevance)
        typer.echo(c(f"  TASK: {task}", "bold"))
        typer.echo(c(f"  TASK MATCH: {rl} ({relevance:.0%}) — {rr}", rc))
    typer.echo("")

    # Steps
    step = 0
    seen_paths: set[str] = set()
    typer.echo(c("  STEPS (execute in order):", "subheader"))
    typer.echo(c(f"  {'─' * 55}", "gray"))

    # Phase 1: READ (task-related source file)
    if task and first_task_read and first_task_read not in seen_paths:
        step += 1
        seen_paths.add(first_task_read)
        ids = ""
        for item in source_related:
            if item.get("file") == first_task_read and item.get("distinctive_ids"):
                ids = c(f" → {', '.join(item['distinctive_ids'][:3])}", "gray")
                break
        typer.echo(f"    [{step}] READ   {c(first_task_read, 'green')}{ids}")
        typer.echo(c("           Understand this file before making changes.", "gray"))
        seen_paths.add(first_task_read)

    # Phase 2: CONTEXT (architecture reads that differ from task read)
    arch_entries = []
    for r in reads:
        if r["file"] not in seen_paths and len(arch_entries) < 2:
            arch_entries.append(r)
    for entry in arch_entries:
        step += 1
        seen_paths.add(entry["file"])
        ids = c(f" → {', '.join(entry['distinctive_ids'][:3])}", "gray") if entry.get("distinctive_ids") else ""
        typer.echo(f"    [{step}] CONTEXT {c(entry['file'], 'cyan')}{ids}")
        typer.echo(c("           Architecture context for the task.", "gray"))

    # Phase 3: PREREQUISITE (binding concepts)
    bc_shown = 0
    for b in bc:
        if bc_shown >= 2:
            break
        if b["file_count"] >= 3:
            step += 1
            bc_shown += 1
            def_file = b.get("files", ["unknown"])[0]
            typer.echo(f"    [{step}] PREREQ {c(b['concept'], 'yellow')} ({c(str(b['file_count']), 'cyan')} files)")
            typer.echo(c(f"           Defined in {def_file}. Understand before editing.", "gray"))

    # Phase 4: EDIT (likely edit target)
    if task and first_edit:
        step += 1
        ids = ""
        if first_edit in seen_paths:
            ids = c(" (already read above — now edit it)", "gray")
        else:
            seen_paths.add(first_edit)
            for item in source_related:
                if item.get("file") == first_edit and item.get("distinctive_ids"):
                    ids = c(f" → defines {', '.join(item['distinctive_ids'][:3])}", "gray")
                    break
        typer.echo(f"    [{step}] EDIT   {c(first_edit, 'yellow')}{ids}")

    # Phase 5: VERIFY (test files)
    if task and first_test and first_test not in seen_paths:
        step += 1
        seen_paths.add(first_test)
        typer.echo(f"    [{step}] VERIFY {c(first_test, 'magenta')}")
        typer.echo(c("           All tests must pass after edit.", "gray"))

    typer.echo("")

    # Guardrails
    if stability or bc:
        typer.echo(c("  GUARDRAILS:", "subheader"))
        typer.echo(c(f"  {'═' * 55}", "gray"))

        for s in stability[:5]:
            pct = s.get("persistence", 0)
            if pct <= 0.3:
                label = "HIGH CHURN"
            elif pct >= 0.8:
                label = "STABLE"
            else:
                continue
            typer.echo(f"    ⚠ {c('DO NOT EDIT', 'red')}: {s['file']} ({label} {pct:.0%})")
            typer.echo(c(f"      {s.get('reason', '')}", "gray"))

        for b in bc[:3]:
            if b["file_count"] >= 5 and b["concept"] not in [x["concept"] for x in bc[:bc_shown] if bc_shown]:
                def_file = b.get("files", ["unknown"])[0]
                typer.echo(f"    ⚠ {c('UNDERSTAND FIRST', 'yellow')}: {b['concept']} ({b['file_count']} files)")

        typer.echo("")

    # Footer
    mod_str = f"{len(modules)} module boundaries" if modules else ""
    theme_str = f"{len(themes)} themes" if themes else ""
    footer = " | ".join(filter(None, [mod_str, theme_str, f"{total_code} code files"]))
    if footer:
        typer.echo(c(f"  {footer}", "gray"))

    # Protocol
    typer.echo("")
    typer.echo(c(f"  {'─' * 55}", "gray"))
    typer.echo(c("  Execute steps in order. Stop and report if any step fails.", "subheader"))


def _print_preflight(data: dict) -> None:
    def c(t, col):
        return _color(t, col)
    risk_color = {"low": "green", "moderate": "yellow", "high": "red", "unknown": "gray"}.get(data.get("risk"), "gray")
    temp = data.get("temperature", "WARM")
    temp_color = {"HOT": "red", "WARM": "yellow", "COLD": "cyan"}.get(temp, "gray")
    peer = data.get("peer_relative_risk", {})
    peer_text = peer.get("peer_text", "")
    caveat = data.get("guardrails", {}).get("caveat", "May be wrong; inspect before acting.")
    verbose = data.get("verbose", False)

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  PREFLIGHT ASSESSMENT", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))

    # ── Phase 0: Structural Cohesion Warning ────────────────────────
    cohesion = data.get("cohesion")
    if cohesion is not None:
        if cohesion < 0.3:
            typer.echo(f"  {c('LOW COHESION', 'red')}: {c(f'{cohesion:.2f}', 'red')}  — change propagates across many files; verify broadly")
        elif cohesion >= 0.7:
            typer.echo(f"  {c('HIGH COHESION', 'green')}: {c(f'{cohesion:.2f}', 'green')}  — self-contained change; safe to verify locally")
        else:
            typer.echo(f"  {c('Cohesion:', 'yellow')} {c(f'{cohesion:.2f}', 'yellow')}")

    # ── Phase 1: Executive Summary ────────────────────────────────
    typer.echo(f"  {c('RISK', risk_color)}: {c(data.get('risk', 'unknown'), risk_color)}  "
               f"{c('TEMP', temp_color)}: {c(temp, temp_color)}  "
               f"{c('CONF', 'cyan')}: {c(data.get('confidence', 'unknown'), 'cyan')}")
    if peer_text:
        typer.echo(f"  {c('SCOPE', 'gray')}: {peer_text}")
    typer.echo(f"  {c('WHY', 'gray')}: {'; '.join(data.get('reasons', ['No structural risk flags']))}")
    if verbose:
        env = data.get("safety_envelope", {})
        in_list = env.get("inside", [])
        if in_list:
            typer.echo(f"  {c('SAFETY ENVELOPE', 'gray')}: {len(in_list)} inside, {len(env.get('at_boundary', []))} at boundary")
    typer.echo(c(f"  ⓘ {caveat}", "gray"))
    typer.echo("")

    changed = data.get("changed_files", [])
    if changed:
        typer.echo(c("  PROPOSED CHANGES:", "subheader"))
        for file in changed[:8]:
            ft = data.get("file_temperatures", {}).get(file, "")
            ft_tag = f" {c(f'[{ft}]', temp_color)}" if ft else ""
            typer.echo(f"    {c('+', 'green')} {file}{ft_tag}")
        typer.echo("")

    # ── Phase 2: Action Plan ──────────────────────────────────────
    reads = data.get("read_first", [])
    candidates = data.get("verification_candidates", data.get("verify_with", []))
    avoids = data.get("expansion_risk", data.get("avoid_expanding_into", []))
    envelope = data.get("safety_envelope", {})

    reads_only = [f for f in reads if f not in changed]
    if reads_only or changed or candidates or avoids:
        typer.echo(c("  RECOMMENDED PATH:", "subheader"))
        if reads_only:
            typer.echo(f"    1. {c('READ', 'green')}  {', '.join(reads_only[:2])}  {c('— context for this edit', 'gray')}")
        typer.echo(f"    {'2.' if reads_only else '1.'} {c('EDIT', 'green')}  {', '.join(changed[:3])}")
        vi = len(changed) > 3
        if vi:
            typer.echo(f"    +{len(changed)-3} more")
        if candidates:
            details = data.get("verification_details", [])
            for c_path in candidates[:3]:
                d = next((d for d in details if d.get("path") == c_path), None)
                tag = " " + c("[" + d["reason"] + "]", "gray") if d else ""
                typer.echo("    3. " + c("VERIFY", "cyan") + "  " + c_path + tag)
        else:
            vc = data.get("verification_confidence", {})
            if vc.get("level") in ("low", "unknown"):
                typer.echo(f"    3. {c('VERIFY', 'cyan')}  {c('No candidates found — inspect manually', 'yellow')}")
        boundary = envelope.get("at_boundary", [])
        if boundary:
            typer.echo(f"    {c('BOUNDARY', 'yellow')}: {', '.join(boundary[:3])}  {c('— verify before touching', 'yellow')}")
        if avoids:
            typer.echo(f"    {c('DNT', 'red')}: {', '.join(avoids[:3])}  {c('— expand scope carefully', 'red')}")
        typer.echo("")

    # ── Phase 3: Gotchas (only if >0) ─────────────────────────────
    gotcha_sections = []

    # HIDDEN DEPENDENCIES (was: reverse_blast)
    blast = data.get("reverse_blast", [])
    if blast:
        items = []
        for item in blast[:5]:
            concepts = ", ".join(item.get("concepts", [])[:3])
            items.append(f"    {c(str(item.get('shared_concepts', 0)), 'yellow')} shared  {item.get('file')}  {c(concepts, 'gray')}")
        if items:
            gotcha_sections.append((c("  HIDDEN DEPENDENCIES (Blast Radius):", "subheader"), items))

    # HISTORICAL BLIND SPOTS (co-change)
    co_change = data.get("co_change", [])
    if co_change:
        items = []
        for cc in co_change[:3]:
            prob = cc.get("probability", "")
            fname = cc.get("file", "")
            cocount = cc.get("co_occurrences", 0)
            tag = "(" + str(cocount) + "\u00d7)"  # times sign
            items.append("    " + c(prob, "yellow") + "  " + fname + "  " + c(tag, "gray"))
        gotcha_sections.append((c("  HISTORICAL PATTERNS (Co-change):", "subheader"), items))

    # STABLE ANCHORS TOUCHED (was: stable_anchors_touched)
    stable = data.get("stable_anchors_touched", [])
    if stable:
        items = []
        for item in stable[:3]:
            persistence = item.get("persistence", 0)
            items.append(f"    {c(item.get('file', ''), 'red')}  {c(f'{persistence:.0%} stable', 'gray')}")
        gotcha_sections.append((c("  ARCHITECTURAL STABILITY:", "subheader"), items))

    # ISOLATED CODE (was: structural_orphans)
    orphans = data.get("structural_orphans", [])
    if orphans:
        items = []
        for o in orphans[:3]:
            uid = o.get("unique_identifiers", 0)
            items.append("    " + o.get("file", "") + "  " + c(str(uid) + " unique identifiers", "gray"))
        gotcha_sections.append((c("  ISOLATED CODE (Structural Orphans):", "subheader"), items))

    if gotcha_sections:
        typer.echo(c("  GOTCHAS:", "subheader"))
        for title, items in gotcha_sections:
            typer.echo(title)
            for item in items:
                typer.echo(item)
        typer.echo("")

    # ── Verbose-only signals ──────────────────────────────────────
    if verbose:
        snr = data.get("snr_annotations", {})
        if snr:
            typer.echo(c("  SIGNAL ANALYSIS:", "subheader"))
            for key, val in snr.items():
                t = val.get("type", "?")
                tc = "green" if t == "signal" else "gray"
                typer.echo(f"    {c(f'{key}:{t}', tc)}  {c(val.get('detail', ''), 'gray')}")
            typer.echo("")
        do_not_touch = data.get("expansion_risk", data.get("avoid_expanding_into", []))
        if do_not_touch:
            typer.echo(c("  EXPANSION RISK (verbose):", "subheader"))
            for item in do_not_touch[:5]:
                typer.echo(f"    {c('✗', 'red')} {item}")
            typer.echo("")

    # Footer
    receipt = data.get("privacy_receipt", {})
    typer.echo(c(f"  Privacy: local only={receipt.get('local_only', True)}, uploaded={receipt.get('uploaded', False)}, network={receipt.get('network', False)}", "gray"))
    cap = data.get("capability_boundary", "")
    if cap:
        typer.echo(c(f"  {cap}", "gray"))
    typer.echo(c("  Mode: report-only\n    do not treat as semantic truth or coverage proof.", "gray"))


def _print_preflight_checklist(data: dict) -> None:
    def c(t, col):
        return _color(t, col)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VOCAB PREFLIGHT — CHECKLIST", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Risk: {data.get('risk', 'unknown')} ({data.get('confidence', 'unknown')} confidence)")
    typer.echo(c(f"  Caveat: {data.get('guardrails', {}).get('caveat', 'May be wrong; inspect before acting.')}", "yellow"))
    typer.echo("")
    step = 1
    for file in data.get("read_first", [])[:3]:
        typer.echo(f"  [{step}] READ   {file}")
        step += 1
    for file in data.get("expansion_risk", data.get("avoid_expanding_into", []))[:5]:
        typer.echo(f"  [{step}] INSPECT expansion risk {file} before broadening scope")
        step += 1
    for file in data.get("changed_files", [])[:5]:
        typer.echo(f"  [{step}] EDIT   {file} only if required by the task")
        step += 1
    for file in data.get("verification_candidates", data.get("verify_with", []))[:3]:
        typer.echo(f"  [{step}] VERIFY CANDIDATE {file}")
        step += 1
    typer.echo("")
    typer.echo(c("  Report-only. Stop and inspect manually if risk is high or the changed file is unexpected.", "subheader"))


@core_app.command(name="agent-bootstrap",  rich_help_panel="Utilities")
def agent_bootstrap(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description to find related files")] = None,
    verify_relevance: Annotated[bool, typer.Option("--verify-relevance", help="Verify surfaced files contain task keywords")] = False,
    summary: Annotated[bool, typer.Option("--summary", help="Only show the decision-oriented startup summary")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json, checklist, llm")] = "compact",
):
    """One-shot agent bootstrap: explore + modules + stability + related files.

    Examples:
      quale agent-bootstrap . --task "fix upload" --summary
      quale agent-bootstrap . --task "fix upload" --verify-relevance --format json
    """
    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        data = bootstrap_repo(path, task=task)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    if format == "llm":
        from quale.formats.llm import format_bootstrap_llm
        typer.echo(format_bootstrap_llm(data))
        return

    if format == "checklist":
        _print_agent_checklist(data, task)
        return

    if verify_relevance and "task_relevance_score" in data:
        score = data["task_relevance_score"]
        label, color, reason = _relevance_label(score)
        typer.echo(_color(f"  Task relevance: {label} ({score:.0%}) - {reason}", color), err=True)

    def c(t, col):
        return _color(t, col)
    relevance = data.get("task_relevance_score", 1.0)
    relevance_label, relevance_color, relevance_reason = _relevance_label(relevance)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  AGENT BOOTSTRAP", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))

    reads = data.get("recommended_next_reads", [])
    related = data.get("related_files_for_task", [])
    modules = data.get("module_boundaries", [])
    bc = data.get("binding_concepts", [])
    likely = data.get("task_plan", {}).get("likely_edit_files", [])
    source_related = [item for item in related if item.get("role") != "test"]
    test_related = [item for item in related if item.get("role") == "test"]
    first_task_read = source_related[0]["file"] if source_related else (related[0]["file"] if task and related else None)
    first_arch_read = reads[0]["file"] if reads else None

    # Build annotation for the main read line
    read_annotation = ""
    if task and source_related and source_related[0].get("distinctive_ids"):
        read_annotation = f" — {', '.join(source_related[0]['distinctive_ids'])}"
    elif not task and reads and reads[0].get("distinctive_ids"):
        read_annotation = f" — {', '.join(reads[0]['distinctive_ids'])}"

    typer.echo(c("  START HERE:", "subheader"))
    typer.echo(f"    Read: {c(first_task_read or first_arch_read or 'no files found', 'green')}{c(read_annotation, 'gray')}")
    if task:
        typer.echo(f"    Task match: {c(relevance_label, relevance_color)} ({relevance:.0%}) - {c(relevance_reason, 'gray')}")
        if likely:
            task_annotation = ""
            for item in related:
                if item.get("file") == likely[0] and item.get("distinctive_ids"):
                    task_annotation = f" — defines {', '.join(item['distinctive_ids'][:3])}"
                    break
            typer.echo(f"    Likely edit: {c(likely[0], 'yellow')}{c(task_annotation, 'gray')}")
        if test_related:
            typer.echo(f"    Verification hint: {c(test_related[0]['file'], 'cyan')}")
        if first_arch_read and first_arch_read != first_task_read:
            arch_annotation = ""
            for r in reads:
                if r["file"] == first_arch_read and r.get("distinctive_ids"):
                    arch_annotation = f" — {', '.join(r['distinctive_ids'])}"
                    break
            typer.echo(f"    Architecture context: {c(first_arch_read, 'cyan')}{c(arch_annotation, 'gray')}")
    typer.echo(f"    Modules: {c(str(len(modules)), 'cyan')} structural groups detected")
    if bc:
        top = bc[0]
        typer.echo(f"    Binds: {c(top['concept'], 'yellow')} ({top['file_count']} files){c(' — read first to understand the dependency chain', 'gray')}")
        if len(bc) > 1:
            _f0 = bc[1]["files"][0]
            typer.echo(f"           {c(bc[1]['concept'], 'yellow')} ({bc[1]['file_count']} files){c(' — ' + _f0, 'gray')}")
    typer.echo("")

    # Anti-guidance: files to NOT touch
    avoid = data.get("avoid_touching_without_context", [])
    if avoid:
        bad_anchor = [a for a in avoid[:3] if a.get("persistence", 0) >= 0.9]
        if bad_anchor:
            typer.echo(c("  DO NOT EDIT:", "subheader"))
            for a in bad_anchor[:3]:
                pct_str = f'{a["persistence"]:.0%}'
                typer.echo(f"    {c('✗', 'red')} {c(a['file'], 'yellow')}  (stable {pct_str} — {c(a.get('reason', 'architectural foundation'), 'gray')})")
            typer.echo("")

    if summary:
        return

    notes = data.get("agent_notes", [])
    if notes:
        for n in notes:
            typer.echo(f"  {c('→', 'green')} {c(n, 'gray')}")
    typer.echo("")

    if reads:
        read_label = "ARCHITECTURE READS:" if task else "READ FIRST:"
        typer.echo(c(f"  {read_label}", "subheader"))
        for r in reads:
            score_str = f'{r["score"]:6.1f}'
            annotation = ""
            if r.get("distinctive_ids"):
                annotation = c(f" — {', '.join(r['distinctive_ids'][:3])}", "gray")
            typer.echo(f"    {c(r['language'], 'cyan'):<10} {c(score_str, 'green')}  {r['file']:<50}  {c(r['reason'], 'gray')}{annotation}")
        typer.echo("")

    avoid = data.get("avoid_touching_without_context", [])
    if avoid:
        typer.echo(c("  AVOID TOUCHING WITHOUT CONTEXT:", "subheader"))
        for a in avoid:
            pct_str = f'{a["persistence"]:.0%}'
            typer.echo(f"    {c(pct_str, 'red'):>6}  {a['file']:<50}  {c(a['reason'], 'gray')}")
        typer.echo("")

    if related:
        typer.echo(c(f"  RELATED FILES (task: {task}):", "subheader"))
        for r in related[:5]:
            role = r.get("role", "source")
            role_color = "yellow" if role == "source" else "cyan"
            annotation = ""
            if r.get("distinctive_ids"):
                annotation = c(f" — {', '.join(r['distinctive_ids'][:3])}", "gray")
            typer.echo(f"    {c(role, role_color):<10} {r['file']:<50}  {c(r['phrase'], 'gray')}{annotation}")
        typer.echo("")

    if bc:
        typer.echo(c("  BINDING CONCEPTS:", "subheader"))
        for b in bc[:6]:
            files_str = ', '.join(b["files"][:3])
            _nfiles = f"{b['file_count']:>4} files"
            typer.echo(f"    {c(b['concept'], 'yellow'):<30} {c(_nfiles, 'cyan')}  {c(files_str, 'gray')}")
        typer.echo("")

    task_plan = data.get("task_plan", {})
    if task_plan:
        likely = task_plan.get("likely_edit_files", [])
        if likely:
            typer.echo(c("  TASK PLAN:", "subheader"))
            for f in likely[:5]:
                typer.echo(f"    {c('edit?', 'yellow'):<8} {f}")
            for step in task_plan.get("sequence", [])[:3]:
                typer.echo(f"    {c('→', 'green')} {c(step, 'gray')}")
            typer.echo("")

    if modules:
        typer.echo(c(f"  MODULE BOUNDARIES ({len(modules)} found):", "subheader"))
        for m in modules[:5]:
            files_preview = ", ".join(f.replace("\\", "/").split("/")[-1] for f in m["files"][:3])
            pr = m.get("persistence_range", [1, 2])
            typer.echo(f"    {m['size']} files  thr {pr[0]}→{pr[1]}  {c(files_preview, 'gray')}")
        if len(modules) > 5:
            typer.echo(c(f"    … +{len(modules) - 5} more", "gray"))
        typer.echo("")

    themes = data.get("themes", [])
    if themes:
        typer.echo(c("  THEMES:", "subheader"))
        for th in themes[:2]:
            files_str = f"{th['files']} files ({th['variance_explained']:.0%})"
            typer.echo(f"    {c(th['label'][:35], 'cyan'):<35} {c(files_str, 'yellow')}")
        typer.echo("")


@core_app.command(rich_help_panel="Utilities")
def skeleton(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Prompt decompression: emit only the ~100-token skeleton for LLM system prompts.

    Skip directives tell the LLM which files to ignore (generated, vendor) and
    which test conventions to expect. Meant to REDUCE prompt noise.
    """
    from quale.reports import crystallography as _crystallography

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = _crystallography(path)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps({"schema_version": 1, "skeleton": data.get("skeleton", ""), "skip_directives": [
            f"Generated files: {data.get('generated_pct', 0)}%",
            f"Test convention: {data.get('test_convention', 'unknown')}",
        ]}, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    typer.echo(data.get("skeleton", ""))
    if data.get("generated_pct", 0) > 5:
        typer.echo(c(f"\n  Skip: {data['generated_pct']}% generated files — do not edit without confirmation.", "gray"))
    if data.get("test_convention", "unknown") != "unknown":
        typer.echo(c(f"  Skip: tests follow {data['test_convention']} convention — already covered.", "gray"))


@core_app.command(rich_help_panel="History")
def delta(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Structural changes since last quale init scan.

    Requires a cached scan from `quale init` or `quale repo-map --save`.
    """
    from quale.reports import repo_delta

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = repo_delta(path)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VOCAB DELTA", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    _fd = data.get('file_delta', 0)
    _fd_str = f"{_fd:+d}" if _fd >= 0 else f"{_fd:+d}"
    typer.echo(f"  Files: {c(str(data.get('old_files', 0)), 'gray')} → {c(str(data.get('new_files', 0)), 'cyan')} ({c(_fd_str, 'green')})")
    gen_delta = data.get('generated_delta', 0)
    if gen_delta:
        typer.echo(f"  Generated: {c(f'{gen_delta:+.1f}%', 'yellow')}")
    stable_lost = data.get('stable_lost', [])
    stable_gained = data.get('stable_gained', [])
    if stable_lost:
        typer.echo(f"  Stable lost: {c(', '.join(stable_lost[:3]), 'red')}")
    if stable_gained:
        typer.echo(f"  Stable gained: {c(', '.join(stable_gained[:3]), 'green')}")

    anomalies = data.get('anomalies', [])
    if anomalies and anomalies[0].get("note"):
        typer.echo(c(f"\n  {anomalies[0]['note']}", "yellow"))
    elif anomalies:
        typer.echo(c("\n  Anomalies:", "yellow"))
        for a in anomalies[:3]:
            severity_color = "red" if a.get("severity") == "high" else "yellow"
            typer.echo(f"    {c(a['type'], severity_color)}: {c(str(a.get('delta', '')), 'gray')}")


@core_app.command(name="ci-report",  rich_help_panel="CI")
def ci_report_cmd(
    ref_a: Annotated[str, typer.Argument(help="Base git ref (e.g. origin/main)")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref (e.g. HEAD)")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    fail_mirror_gap: Annotated[float | None, typer.Option("--fail-on-mirror-gap", help="Fail if mirror_gap_ratio < threshold")] = None,
    fail_blast_tier: Annotated[str | None, typer.Option("--fail-on-blast-tier", help="Fail if max_blast_tier >= tier (local/moderate/high/critical)")] = None,
    fail_stable_touched: Annotated[bool, typer.Option("--fail-on-stable-touched", help="Fail if any stable anchors touched")] = False,
    fail_hub_risk: Annotated[bool, typer.Option("--fail-on-hub-risk", help="Fail if changed file is in top 10% hub-risk")] = False,
    fail_clone: Annotated[bool, typer.Option("--fail-on-clone", help="Fail if changed file is a structural clone")] = False,
    fail_new_identifiers: Annotated[int | None, typer.Option("--fail-on-new-identifiers", help="Fail if more than N new identifiers introduced")] = None,
    summary: Annotated[bool, typer.Option("--summary", help="Only show pass/fail, reason, and core metrics")] = False,
    why: Annotated[bool, typer.Option("--why", help="Show why each result")] = False,
):
    """CI-ready structural report: blast radius + stable file check + flags.

    Analyzes the structural impact of a change set without blocking.
    Designed for CI pipelines that want a summary, not a gate.

    Examples:
      quale ci-report origin/main HEAD --summary
      quale ci-report origin/main HEAD --fail-on-mirror-gap 0.70
      quale ci-report origin/main HEAD --fail-on-blast-tier high
      quale ci-report origin/main HEAD --fail-on-stable-touched
    """
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        data = ci_report(ref_a, ref_b, path)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    try:
        gate_failures, gate_checks = _gate_evaluation(
            data, fail_mirror_gap, fail_blast_tier, fail_stable_touched,
            fail_hub_risk=fail_hub_risk, fail_clone=fail_clone,
            fail_new_identifiers=fail_new_identifiers,
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        if gate_failures:
            raise typer.Exit(gate_failures[0][0])
        return

    def c(t, col):
        return _color(t, col)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c(f"  CI REPORT: {data['base_ref']} → {data['head_ref']}", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo("")

    configured_gates = bool(gate_checks)
    if gate_failures:
        typer.echo(f"  {c('FAIL', 'red')}: {gate_failures[0][1]}")
    elif configured_gates:
        typer.echo(f"  {c('PASS', 'green')}: configured gates passed")
    else:
        typer.echo(f"  {c('INFO', 'cyan')}: no gates configured")
    typer.echo(
        f"  {c('Metrics:', 'subheader')} mirror coverage {data.get('mirror_gap_ratio', 0.0):.0%}, "
        f"blast {data.get('max_blast_tier', 'none')}, "
        f"stable touched {data.get('stable_touched_count', 0)}"
    )
    if gate_failures:
        for _, failure in gate_failures[1:]:
            typer.echo(f"  {c('also:', 'gray')} {failure}")
    typer.echo("")

    if summary:
        if gate_failures:
            raise typer.Exit(gate_failures[0][0])
        return

    changed = data.get("changed_files", [])
    typer.echo(c(f"  Changed files: {len(changed)}", "subheader"))
    for f in changed[:8]:
        typer.echo(f"    {c('+', 'green')} {f}")
    if len(changed) > 8:
        typer.echo(c(f"    … +{len(changed) - 8} more", "gray"))
    typer.echo("")

    blast = data.get("blast_radius", [])
    if blast:
        typer.echo(c(f"  BLAST RADIUS ({len(blast)} impacted files):", "subheader"))
        for item in blast[:10]:
            conc_bar = _bar(min(item.get("shared_concepts", 0) * 5, 100), 8)
            conc = ", ".join(item.get("concepts", [])[:3])
            typer.echo(f"    {conc_bar} {item['file'][:50]}  {c(str(item.get('shared_concepts', 0)), 'yellow')} shared {c(conc, 'gray')}")
        if len(blast) > 10:
            typer.echo(c(f"    … +{len(blast) - 10} more", "gray"))
        typer.echo("")

    stable_touched = data.get("stable_files_touched", [])
    if stable_touched:
        typer.echo(c("  STABLE FILES TOUCHED:", "subheader"))
        for s in stable_touched:
            status_color = "yellow" if s["status"] == "churn_hotspot" else "red"
            typer.echo(f"    {c(s['file'][:55], status_color)}  {c(s['status'], 'gray')}")
        typer.echo("")

    flags = data.get("risk_flags", [])
    if flags:
        typer.echo(c("  RISK FLAGS:", "subheader"))
        for flag in flags:
            typer.echo(f"    {c('⚠', 'yellow')} {flag}")
        typer.echo("")

    mirror = data.get("mirror_signals", {})
    gaps = mirror.get("unmirrored_source_concepts", [])
    if gaps:
        typer.echo(c("  SOURCE/TEST MIRROR GAPS:", "subheader"))
        typer.echo(c(f"    {mirror.get('mirrored_source_concepts', 0)}/{mirror.get('source_concepts_changed', 0)} changed source concepts mirrored in tests", "gray"))
        typer.echo(f"    {', '.join(gaps[:12])}")
        typer.echo(c(f"    {mirror.get('note', '')}", "gray"))
        typer.echo("")

    typer.echo(c("  GATE METRICS:", "subheader"))
    _mg = data.get('mirror_gap_ratio', 0.0)
    _mg_str = f"{_mg:.0%}"
    typer.echo(f"    Mirror gap: {c(_mg_str, 'cyan')}")
    typer.echo(f"    Max blast tier: {c(data.get('max_blast_tier', 'none'), 'yellow')}")
    typer.echo(f"    Stable anchors touched: {c(str(data.get('stable_touched_count', 0)), 'cyan')}")
    for check in gate_checks:
        typer.echo(f"    {c('check', 'gray')} {check}")
    for _, failure in gate_failures:
        typer.echo(f"    {c('FAIL', 'red')} {failure}")
    if (fail_mirror_gap is not None or fail_blast_tier is not None or fail_stable_touched) and not gate_failures:
        typer.echo(f"    {c('PASS', 'green')} configured gates passed")
    typer.echo("")

    typer.echo(c(f"  Summary: {data.get('summary', '')}", "gray"))
    typer.echo("")

    if gate_failures:
        raise typer.Exit(gate_failures[0][0])

    if why:
        from quale.formats.terminal import _why_ci_report
        typer.echo(_why_ci_report(data, ref_a, ref_b))


@cli.command(rich_help_panel="Getting Started")
def inspect(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    path_opt: Annotated[str, typer.Option("--path", "-p", help="Path to repo", hidden=True)] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    anomalies: Annotated[bool, typer.Option("--anomalies", help="Load cached scan and show deltas")] = False,
    why: Annotated[bool, typer.Option("--why", help="Show why each section matters")] = False,
):
    """Comprehensive codebase overview: explore + modules + timeline + health.

    Single command that tells you what matters about a codebase:
    top files, module boundaries, structural themes, stability, churn, health score.
    """
    if path_opt:
        path = path_opt
    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        data = inspect_repo(path)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if anomalies:
        from quale.reports import detect_anomalies
        data["anomalies"] = detect_anomalies(path)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    explore_data = data.get("explore", {})
    modules_data = data.get("modules", {})
    timeline_data = data.get("timeline", [])
    avg_age = data.get("avg_concept_age_weeks", 0)
    files = explore_data.get("files", [])
    themes = explore_data.get("themes", [])
    total_code = explore_data.get("total_code_files", 0)
    module_count = len(modules_data.get("modules", []))
    grouped = modules_data.get("grouped_files", 0)
    latest = timeline_data[-1] if timeline_data else {}
    latest_commits = latest.get("commits", 0)

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  {c('INSPECT —', 'header')} {c(path, 'bold')} ({total_code} files)")
    if avg_age:
        typer.echo(c(f"  Avg concept age: {avg_age} weeks", "gray"))
    if data.get("confidence"):
        typer.echo(c(f"  Confidence: {data['confidence']}", "gray"))

    if module_count:
        ungrouped = total_code - grouped
        typer.echo(c(f"  {module_count} module boundaries ({grouped}/{ungrouped} files grouped/ungrouped)", "gray"))
    if timeline_data:
        typer.echo(c(f"  Latest week: {latest_commits} commits, {latest.get('new_concepts', 0)} new concepts", "gray"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo("")

    if files:
        typer.echo(c("  TOP FILES (read first):", "subheader"))
        for f in files[:10]:
            prefix = c("→", "green") if f["unique_score"] > 30 else c("·", "gray")
            score_str = f'{f["unique_score"]:6.1f}'
            lang = f['language']
            typer.echo(f"    {prefix} {c(score_str, 'cyan')}  {c(lang, 'gray'):<8} {f['file']}")

        if themes:
            typer.echo("")
            typer.echo(c("  THEMES:", "subheader"))
            for th in themes[:3]:
                bar = _bar(th["variance_explained"] * 100, 12)
                pct = f'{th["variance_explained"]:.0%}'
                typer.echo(f"    {bar} {c(th['label'][:35], 'cyan'):<35} {c(th['files'], 'yellow'):>4} files ({pct})")
        typer.echo("")

    binding = data.get("binding_concepts", [])
    if binding:
        typer.echo(c("  BINDING CONCEPTS:", "subheader"))
        for bc in binding[:8]:
            langs = ",".join(bc.get("languages", []))
            typer.echo(f"    {c(bc['concept'], 'cyan'):<35} {c(str(bc['file_count']), 'yellow'):>3} files  {c(langs, 'gray')}")
        typer.echo("")

    if module_count > 0:
        typer.echo(c(f"  MODULE BOUNDARIES ({module_count} found):", "subheader"))
        for m in modules_data.get("modules", [])[:5]:
            pr = m.get("persistence_range", [1, 3])
            bar = _bar((pr[1] - pr[0] + 1) * 10, 10)
            files_preview = ", ".join(f.replace("\\", "/").split("/")[-1] for f in m["files"][:3])
            typer.echo(f"    {bar} {m['size']} files  thr {pr[0]}→{pr[1]}  {c(files_preview, 'gray')}")
        if module_count > 5:
            typer.echo(c(f"    … +{module_count - 5} more modules", "gray"))
        typer.echo("")

    debt = data.get("debt_candidates", [])
    if debt:
        typer.echo(c("  DEBT CANDIDATES (low uniqueness + churn potential):", "subheader"))
        for d in debt[:8]:
            bar = _bar(d["debt"] * 100, 10)
            _dbt = f"{d['debt']:.2f}"
            typer.echo(f"    {bar} {c(_dbt, 'red')}  {c(d['language'], 'gray'):<8} {d['file']}")
        typer.echo("")

    health = data.get("health_score")
    if health is not None:
        health_color = "green" if health >= 0.7 else ("yellow" if health >= 0.4 else "red")
        if health >= 0.7:
            val = "Well-structured with strong test coverage and low churn."
        elif health >= 0.4:
            val = "Moderate health — has some stability but may have churn or test gaps."
        else:
            val = "Weak structural health — high churn, sparse tests, or architectural drift."
        typer.echo(f"  Structural health: {c(f'{health:.2f}/1.0', health_color)}  {c(f'— {val}', 'gray')}")
    invasive = data.get("invasive_concepts", [])
    if invasive:
        inv_concepts = ", ".join(i["concept"] for i in invasive[:3])
        typer.echo(f"  Broad cross-file concepts: {c(inv_concepts, 'yellow')}")

    anomalies = data.get("anomalies", [])
    if anomalies and not anomalies[0].get("note"):
        typer.echo(c("\n  Anomalies:", "red"))
        for a in anomalies[:3]:
            typer.echo(f"    {c(a['type'], 'red' if a.get('severity') == 'high' else 'yellow')}")
    typer.echo("")

    typer.echo(c(f"{'━' * 60}", "cyan"))

    if why:
        from quale.formats.terminal import _why_inspect
        typer.echo(_why_inspect(data))


@core_app.command(rich_help_panel="Getting Started")
def modules(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    """Detect parser-free module boundaries from rare identifier overlap."""
    path = os.path.abspath(path)
    if not vgit.is_repo(path) or not vgit.has_commits(path):
        typer.echo("Not a git repository with commits.", err=True)
        raise typer.Exit(1)
    data = compute_modules(path)
    if format == "json":
        typer.echo(format_modules_json(data))
    else:
        typer.echo(format_modules(data))


@core_app.command(name="help-agent",  rich_help_panel="Getting Started")
def help_agent(
    task: Annotated[str, typer.Argument(help="Engineering task description")],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: tool, json")] = "tool",
) -> None:
    """Recommend useful quale commands for an agent task."""
    task_lower = task.lower()
    commands: list[tuple[str, str, bool]] = []

    # Primary agent surface — proven by harness
    commands.append(("quale edit-context --path . --files <file> --task \"<task>\" --format tool",
                     "Verify candidates and stay in scope for a candidate edit file.", True))
    commands.append(("quale contract --path . --files <file> --task \"<task>\" --format tool",
                     "Bounded ID-coded scope contract (experimental).", True))
    commands.append(("quale check-plan --contract <contract.json> --proposal <proposal.json>",
                     "Validate LLM proposal against contract (experimental).", True))

    # Task-specific secondary
    if any(word in task_lower for word in ("pr", "review", "change", "refactor", "edit", "feature", "fix")):
        commands.append(("quale edit-context --path . --diff HEAD~1 --task \"<task>\" --format tool",
                         "Diff-scoped edit-context for PR review (100% verify in testing).", True))
        commands.append(("quale ci-report origin/main HEAD --format json",
                         "Check structural impact before PR (human/CI tool).", False))

    # Orientation
    commands.append(("quale repo-map --path . --format json",
                     "Compact repo skeleton for initial orientation (not per-task).", False))
    commands.append(("quale agent-bootstrap . --task \"<task>\" --format checklist",
                     "Weak-model orientation: step-by-step protocol (not for strong models).", True))

    # Deep investigations
    if any(word in task_lower for word in ("history", "why", "when", "provenance", "timeline")):
        commands.append(("quale provenance <phrase> --format json",
                         "Trace when a concept appeared or disappeared.", True))
        commands.append(("quale stable . --format json",
                         "Surface files and phrases that persist across git history.", False))
    if any(word in task_lower for word in ("contract", "integration", "cross", "drift", "compare")):
        commands.append(("quale compare <repo-a> <repo-b> --format json",
                         "Cross-repo vocabulary alignment and drift asymmetry.", True))

    # Unmeasured agent commands — harness-validated behavior unknown
    if any(word in task_lower for word in ("negotiate", "scope")):
        commands.append(("quale negotiate --path . --files <file> --task \"<task>\" --format json",
                         "[UNMEASURED] Multi-turn scope containment protocol.", True))
    if any(word in task_lower for word in ("verify", "test", "check")):
        commands.append(("quale verify --path . --files <file> --task \"<task>\"",
                         "[UNMEASURED] Multiple-choice verification candidates.", True))
    if any(word in task_lower for word in ("route", "decide", "whether")):
        commands.append(("quale route --path . --task \"<task>\" --format json",
                         "[UNMEASURED] Routing logic that decides when to use quale.", True))

    # Discoverability
    commands.append(("quale explore . --format json --quick",
                     "Quick onboarding: most distinctive source files.", False))
    commands.append(("quale help-agent \"<task>\"",
                     "This command — show recommended commands for any task.", True))

    if format == "tool":
        typer.echo(json.dumps({
            "schema_version": 1,
            "task": task,
            "workflow": ["edit-context", "guard", "contract", "verify-packet"],
            "command_conventions": {
                "--files <CSV>": ["edit-context", "verify-packet"],
                "--file <FILE>": ["guard", "check-plan", "contract", "check-diff", "deflate", "heisenberg"],
                "--path <DIR>": ["inspect", "repo-map", "hub-risk", "extinct-exports", "coupling-chain", "anomalies", "entropy", "drift-check", "forecast", "isolate", "fold", "origins"],
                "positional <TEXT>": ["search", "help-agent"],
                "--ref <REF>": ["lifecycle", "timeline", "stable", "provenance"]
            },
            "gotchas": [
                "search strips punctuation — search bare identifiers, not func(",
                "hub-risk, extinct-exports, coupling-chain are repo-level only (no --file)",
                "repo-map rejects positional arg — use --path .",
                "Pipe --format tool output via 2>/dev/null before piping to json.tool to strip stderr banners",
                "--files takes comma-separated paths, not repeated flags",
                "--format tool is for LLM consumption, --format json is for data export",
                "--format compact is terminal-friendly (default for most commands)"
            ],
            "_agent_note": "Run 'quale --agent-orient' after pip install for full flag conventions and workflow",
            "commands": [
                {"cmd": cmd, "why": why, "requires_user_value": requires_value}
                for cmd, why, requires_value in commands
            ],
        }, indent=2))
    else:
        typer.echo(json.dumps({
            "schema_version": 1,
            "task": task,
            "commands": [
                {"cmd": cmd, "why": why, "requires_user_value": requires_value}
                for cmd, why, requires_value in commands
            ],
        }, indent=2))


@core_app.command(rich_help_panel="Cross-Repo")
def compare(
    repo_a: Annotated[str, typer.Argument(help="First repo path")],
    repo_b: Annotated[str, typer.Argument(help="Second repo path")],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    contract_only: Annotated[bool, typer.Option("--contract-only", help="Only compare contract surface paths (api/, client/, types)")] = False,
    fail_on_drift: Annotated[float | None, typer.Option("--fail-on-drift", help="Exit with code 1 if drift score exceeds this threshold (0-1)")] = None,
):
    """Cross-repo vocabulary alignment and drift asymmetry."""
    repo_a = os.path.abspath(repo_a)
    repo_b = os.path.abspath(repo_b)
    if not vgit.is_repo(repo_a) or not vgit.is_repo(repo_b):
        typer.echo("Both paths must be git repositories.", err=True)
        raise typer.Exit(1)

    result = compare_repos(repo_a, repo_b, contract_only=contract_only)
    if format == "json":
        typer.echo(json.dumps(result, indent=2))
        if fail_on_drift is not None:
            drift = result.get("drift_score", 1.0)
            if drift >= fail_on_drift:
                typer.echo(f"  (drift {drift:.2f} >= threshold {fail_on_drift}) FAIL")
                raise typer.Exit(1)
        return

    def c(t, color):
        return _color(t, color)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  {c('VOCABULARY ALIGNMENT', 'header')}: {result['repo_a']} <-> {result['repo_b']}")
    if result.get("contract_only"):
        typer.echo(c("  (contract surface only — api/, client/, types)", "gray"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  {result['repo_a']}: {result['a_total_phrases']} concepts")
    typer.echo(f"  {result['repo_b']}: {result['b_total_phrases']} concepts")
    typer.echo(f"  Shared: {result['shared_phrases']} ({result['alignment']:.0%} aligned)")
    drift = result.get("drift_score", 0.0)
    drift_color = "green" if drift < 0.10 else ("yellow" if drift < 0.25 else "red")
    typer.echo(f"  Drift: {c(f'{drift:.0%}', drift_color)}")
    for phrase in result.get("drift_candidates", [])[:15]:
        typer.echo(f"  - {phrase}")
    if fail_on_drift is not None:
        if drift >= fail_on_drift:
            typer.echo(c(f"  DRIFT FAIL: {drift:.2f} >= {fail_on_drift}", "red"))
            raise typer.Exit(1)
        else:
            typer.echo(c(f"  drift {drift:.2f} < {fail_on_drift} PASS", "green"))


@core_app.command(rich_help_panel="History")
def provenance(
    phrase: Annotated[str, typer.Argument(help="Phrase to trace")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 24,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    """Trace a phrase's presence through git history."""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = phrase_provenance(path, phrase, weeks=weeks)
    if format == "json":
        typer.echo(json.dumps({"schema_version": 1, "phrase": phrase, "weeks": weeks, "history": data}, indent=2))
        return
    for item in data:
        status = "present" if item["present"] else "absent"
        typer.echo(f"{item['week']} {status} {item.get('file_count', 0)} files")


@core_app.command(name="fingerprint",  rich_help_panel="Utilities")
def fingerprint_cmd(target: Annotated[str, typer.Argument(help="File or repo path")]) -> None:
    """Structural fingerprint of a file or entire repo."""
    target = os.path.abspath(target)
    if os.path.isdir(target):
        typer.echo(json.dumps(repo_fingerprint(target), indent=2))
        return
    if not os.path.isfile(target):
        typer.echo("Not a file or directory.", err=True)
        raise typer.Exit(1)
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    seg_result = segment(content)
    quale = build_vocabulary(seg_result.phrases, seg_result.strategy, seg_result.delimiter)
    phrase_to_idx = {e.text: e.index for e in quale.entries}
    index_list = [phrase_to_idx[p] for p in seg_result.phrases if p in phrase_to_idx]
    typer.echo(f"Fingerprint: v0-{index_sequence_hash(index_list)}")
    typer.echo(f"Phrases: {quale.size} unique / {len(seg_result.phrases)} total")


@core_app.command(rich_help_panel="Utilities")
def orphans(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    """Heuristic single-file exported identifier scan."""
    analysis = scan_codebase(path)
    if format == "json":
        typer.echo(format_orphans_json(analysis))
        return
    if not analysis.dead_exports:
        typer.echo("No single-file exports found.")
        return
    for item in analysis.dead_exports[:30]:
        typer.echo(f"? {item['phrase']} {item['file']}")


def _detect_pr_number() -> int | None:
    """Detect PR number from GitHub Actions environment."""
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/"):
        parts = ref.split("/")
        if len(parts) >= 3 and parts[2].isdigit():
            return int(parts[2])
    return None


@core_app.command(name="pr-report",  rich_help_panel="CI")
def pr_report(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    post_comment: Annotated[bool, typer.Option("--post-comment", help="Post report as PR comment via gh CLI")] = False,
    pr_number: Annotated[int | None, typer.Option("--pr", help="PR number (auto-detected if in CI)")] = None,
):
    """PR structural report in markdown. [INFO: always exits 0]"""
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    try:
        _validate_refs(path, ref_a, ref_b)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    pr_files = vgit.diff_refs(path, ref_a, ref_b)
    if not pr_files:
        typer.echo("No changed files.")
        return
    analysis = scan_codebase(path, git_ref=ref_b, quiet=True)
    blast_results = pr_blast_radius(pr_files, analysis.file_vocabs)
    from quale.reports import refactoring_patterns
    pattern_data = refactoring_patterns(path, base_ref=ref_a, head_ref=ref_b)
    report_text = format_pr_report_markdown(pr_files, blast_results, [], ref_a, ref_b, pattern_data=pattern_data)
    if post_comment:
        try:
            pr = pr_number or _detect_pr_number()
            if pr:
                subprocess.run(
                    ["gh", "pr", "comment", str(pr), "--body", report_text],
                    check=True, capture_output=True, text=True,
                )
                typer.echo(f"Posted PR #{pr} comment.")
            else:
                typer.echo("Could not detect PR number. Set --pr or run in GitHub Actions.", err=True)
        except FileNotFoundError:
            typer.echo("gh CLI not found. Install GitHub CLI or skip --post-comment.", err=True)
        except subprocess.CalledProcessError as e:
            typer.echo(f"Failed to post comment: {e.stderr}", err=True)
        return
    typer.echo(report_text)


@core_app.command(rich_help_panel="Utilities")
def init(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    seed: Annotated[bool, typer.Option("--seed", "--no-seed", help="Seed fragment router from git history")] = True,
):
    """Generate a .quale.yml config file and cache repo-map scan.

    By default also seeds the fragment router using up to 20 historical
    commits so the adaptive router has accuracy data before the first
    LLM task runs. Use --no-seed to skip.

    Speed: seeding scans up to 2500 files. On large repos (2400+ files)
    may take 10-30s additional.
    """
    target = os.path.join(os.path.abspath(path), ".quale.yml")
    if not os.path.exists(target):
        os.makedirs(os.path.abspath(path), exist_ok=True)
        content = """# quale CI configuration
# Structural checks for CI pipelines.

blast:
  max_impacted: 20
  critical_paths: []

lifecycle:
  min_signal_weeks: 4

search:
  common_threshold: 0.8
"""
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        typer.echo(f"Created {target}")

    from quale.reports import crystallography, _save_cached
    path_abs = os.path.abspath(path)
    if vgit.is_repo(path_abs):
        data = crystallography(path_abs)
        if "error" not in data:
            _save_cached(path_abs, data)
            typer.echo("Cached repo-map scan for delta tracking.")
        if seed:
            from quale.reports import seed_fragment_matrix
            typer.echo("Seeding fragment router from recent commits... ", nl=False)
            seed_data = seed_fragment_matrix(path_abs, max_commits=20)
            seeded = seed_data.get("seeded_trials", 0)
            typer.echo(_color(f"done ({seeded} historical trials seeded).", "green"))
    else:
        typer.echo("Not a git repository\n        skipping cache.")


def _entry_main():
    if "--version" in sys.argv or "-V" in sys.argv:
        from quale import __version__
        typer.echo(f"quale-cli {__version__}")
        return
    if "--agent-orient" in sys.argv:
        typer.echo(json.dumps({
            "schema_version": 1,
            "name": "quale",
            "description": "Grammar-free structural codebase analyzer. Zero config, one scan, every language.",
            "quickstart": "quale guard --file <FILE> --task \"<TASK>\" --format tool  # combined safety packet",
            "recommended_workflow": [
                {"step": 1, "command": "quale repo-map --path <REPO> --format json", "why": "Compact repo skeleton for initial orientation (once per repo)"},
                {"step": 2, "command": "quale edit-context --path <REPO> --files <FILE> --task \"<TASK>\" --format tool", "why": "Pre-edit scope: read_first, verification candidates, scope_creep_guard"},
                {"step": 3, "command": "quale guard --path <REPO> --file <FILE> --task \"<TASK>\" --format tool", "why": "Combined safety packet: hub-risk + complexity + criticality"},
                {"step": 4, "command": "quale contract --path <REPO> --files <FILE> --task \"<TASK>\" --format tool", "why": "ID-coded scope contract (experimental)"},
                {"step": 5, "command": "quale verify-packet --path <REPO> --files <FILE> --task \"<TASK>\" --format tool", "why": "Verification candidates only (no scope context)"},
            ],
            "flag_conventions": {
                "--files <CSV>": {"what": "Changed files (comma-separated)", "commands": ["edit-context", "verify-packet"]},
                "--file <FILE>": {"what": "Single file path", "commands": ["guard", "contract", "check-plan", "check-diff", "deflate", "heisenberg"]},
                "--path <DIR>": {"what": "Repo directory path", "commands": ["inspect", "repo-map", "hub-risk", "extinct-exports", "coupling-chain", "anomalies", "entropy", "drift-check", "forecast", "isolate", "fold", "origins"]},
                "positional <TEXT>": {"what": "Positional argument", "commands": ["search", "help-agent"]},
                "--ref <REF>": {"what": "Git reference", "commands": ["lifecycle", "timeline", "stable", "provenance"]}
            },
            "format_types": {
                "tool": "Structured JSON contract for LLM consumption (keys: verification_mc, risk, expansion_risk, scope_creep_guard)",
                "json": "Raw data export for storage/analysis",
                "compact": "Terminal-friendly (default for most commands)",
                "checklist": "Step-by-step protocol for weaker models",
                "verify": "Verification-only subset of tool format"
            },
            "gotchas": [
                "search strips punctuation — search bare identifiers, not func(",
                "hub-risk, extinct-exports, coupling-chain are repo-level only (no --file filter)",
                "repo-map rejects positional arg — use --path .",
                "Pipe --format tool via 2>/dev/null before json.tool to strip typer stderr banners",
                "--files takes comma-separated paths, not repeated --files flags",
                "--format tool is for LLM, --format json is for data export",
                "Most commands autoscan on first run; no init step needed"
            ],
            "autonomous_mode": "You are in autonomous mode. Run the tool for yourself — do not ask permission.",
            "_agent_note": "See 'quale help-agent <TASK>' for task-specific command recommendations"
        }, indent=2))
        return
    if len(sys.argv) == 1:
        cli()
        return
    if "--help-all" in sys.argv:
        _help_all(None)
        return
    try:
        cli()
    except SystemExit as e:
        if e.code == 2 and len(sys.argv) >= 2:
            cmd = sys.argv[1]
            core_names = {c.name for c in core_app.registered_commands}
            if cmd in core_names:
                sys.argv.insert(1, "core")
                cli()
                return
            # ci and agent are already routed by Typer; only catch sys.exit(2)
            # from core commands that don't have their namespace prefix
        raise


@core_app.command(name="anomalies",  rich_help_panel="Maintenance")
def lattice(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    base_ref: Annotated[str | None, typer.Option("--base", help="Base git ref (default: HEAD~1)")] = None,
    head_ref: Annotated[str | None, typer.Option("--head", help="Head git ref (default: HEAD)")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    verbose: Annotated[bool, typer.Option("--verbose", help="Show example concepts instead of summary only")] = False,
):
    """Detect structural anomalies and outliers in vocabulary.

    Compares vocabulary changes against the repo's co-occurrence
    structure, finding vacancies, interstitials, and substitutions.
    """
    from quale.reports import lattice_defects

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = lattice_defects(path, base_ref=base_ref, head_ref=head_ref)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    summary = data.get("summary", {})
    defects = data.get("defects", {})

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c(f"  VOCAB LATTICE — {data.get('base_ref', '?')} → {data.get('head_ref', '?')}", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Changed: {len(data.get('changed_files', []))} files")
    typer.echo(f"  Missing expected concepts: {c(str(summary.get('vacancies', 0)), 'red')}  "
               f"Unexpected concepts: {c(str(summary.get('interstitials', 0)), 'yellow')}  "
               f"Substitutions: {c(str(summary.get('substitutions', 0)), 'cyan')}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")

    if not verbose:
        typer.echo(c("  Summary only. Use --verbose for example concepts.", "gray"))
        typer.echo("")
        return

    vac = defects.get("vacancies", [])
    if vac:
        typer.echo(c("  MISSING EXPECTED CONCEPTS:", "red"))
        for v in vac[:5]:
            present = ", ".join(v.get("present_in", [])[:2])
            typer.echo(f"    {c(v['concept'], 'yellow'):<30} in {c(v['file'], 'green')} — still in {c(present, 'gray')}")
        typer.echo("")

    inter = defects.get("interstitials", [])
    if inter:
        typer.echo(c("  UNEXPECTED CONCEPTS:", "yellow"))
        for i in inter[:5]:
            typer.echo(f"    {c(i['concept'], 'yellow'):<30} in {c(i['file'], 'green')}")
        typer.echo("")

    sub = defects.get("substitutions", [])
    if sub:
        typer.echo(c("  SUBSTITUTIONS (concept replaced):", "cyan"))
        for s in sub[:5]:
            typer.echo(f"    {c(s['old_concept'], 'red')} → {c(s['new_concept'], 'green')}  in {s['file']}  ({s.get('similarity', 0):.0%})")
        typer.echo("")


@core_app.command(rich_help_panel="Maintenance")
def patterns(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    base_ref: Annotated[str | None, typer.Option("--base", help="Base git ref (default: HEAD~1)")] = None,
    head_ref: Annotated[str | None, typer.Option("--head", help="Head git ref (default: HEAD)")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    verbose: Annotated[bool, typer.Option("--verbose", help="Show more examples per pattern type")] = False,
):
    """Refactoring pattern detection: rename, extract, inline, move.

    Detects structural patterns in vocabulary changes without ASTs:
    rename (concept A → B), extract (lost vocabulary), inline (gained),
    and move (vocabulary migrated between files).
    """
    from quale.reports import refactoring_patterns

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = refactoring_patterns(path, base_ref=base_ref, head_ref=head_ref)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    patterns = data.get("patterns", [])

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c(f"  REFACTORING PATTERNS — {data.get('base_ref', '?')} → {data.get('head_ref', '?')}", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Changed: {len(data.get('changed_files', []))} files  Detected: {c(str(len(patterns)), 'green')} patterns")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")

    by_type: dict[str, list[dict]] = {}
    for p in patterns:
        by_type.setdefault(p["type"], []).append(p)

    if not by_type:
        return

    typer.echo(c("  Summary:", "subheader"))
    typer.echo("    " + ", ".join(f"{ptype}: {len(items)}" for ptype, items in sorted(by_type.items())))
    typer.echo("")

    for ptype, items in by_type.items():
        color = {"rename": "green", "move": "cyan", "new_file": "yellow", "deleted_file": "red"}.get(ptype, "gray")
        typer.echo(c(f"  {ptype.upper()} ({len(items)}):", color))
        limit = 3 if verbose else 1
        for item in items[:limit]:
            if ptype == "rename":
                typer.echo(f"    {c(item['old_concept'], 'red')} → {c(item['new_concept'], 'green')}  in {item['file']}  ({item['similarity']:.0%})")
            elif ptype == "move":
                typer.echo(f"    {c(', '.join(item['concepts'][:3]), 'yellow')}  {c(item['from_file'], 'red')} → {c(item['to_file'], 'green')}")
            elif "extract" in ptype:
                typer.echo(f"    {item['file']} lost {c(', '.join(item.get('lost_concepts', [])[:3]), 'yellow')}")
            elif "inline" in ptype:
                typer.echo(f"    {item['file']} gained {c(', '.join(item.get('gained_concepts', [])[:3]), 'yellow')}")
            else:
                typer.echo(f"    {item['file']} {c(', '.join(item.get('concepts', [])[:3]), 'gray')}")
        if len(items) > limit:
            suffix = " more" if verbose else " more; use --verbose"
            typer.echo(f"    … +{len(items) - limit}{suffix}")
        typer.echo("")


@core_app.command(rich_help_panel="Utilities")
def stop(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    read: Annotated[list[str] | None, typer.Option("--read", help="Files already read; repeat")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Agent exploration: should you keep reading?

    Tracks concept coverage as you read files and signals
    when further exploration has diminishing returns.
    """
    from quale.reports import exploration_entropy

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    read_files = read or []
    data = exploration_entropy(path, read_files)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    signal = data.get("stop_signal", "continue")
    sig_color = "green" if signal == "stop" else ("yellow" if signal == "slow" else "cyan")

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  EXPLORATION ENTROPY", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Read: {c(str(data.get('files_read', 0)), 'cyan')}/{data.get('total_files', 0)} files")
    typer.echo(f"  Coverage: {c(str(data.get('coverage_pct', 0)) + '%', 'green')} of unique concepts")
    typer.echo(f"  Next file adds: {c(str(data.get('marginal_gain_next_file', 0)), 'yellow')} new concepts")
    typer.echo(f"  Signal: {c(signal.upper(), sig_color)}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")

    if signal == "stop":
        typer.echo(c("  No more exploration needed. What you've read covers the concepts.", "green"))
    elif signal == "slow":
        typer.echo(c("  Diminishing returns. Consider acting on what you know.", "yellow"))
        next_files = data.get("next_best_files", [])[:3]
        if next_files:
            typer.echo(c(f"  If continuing, read: {', '.join(next_files)}", "gray"))
    else:
        next_files = data.get("next_best_files", [])[:3]
        if next_files:
            typer.echo(c(f"  Next best: {', '.join(next_files)}", "green"))

    typer.echo("")


@core_app.command(name="vocabulary-trend",  rich_help_panel="Maintenance")
def entropy(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks to analyze")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Entropy velocity: is vocabulary diversity accelerating or decelerating?

    Shannon entropy of phrase distribution measured at 4-week
    intervals. Acceleration > 0 = heating up (diversifying fast).
    Acceleration < 0 = cooling down (stabilizing).
    """
    from quale.reports import entropy_velocity

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = entropy_velocity(path, weeks=weeks)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    sig_color = "red" if data.get("signal") == "warning" else ("green" if data.get("signal") == "stable" else "cyan")

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  ENTROPY VELOCITY", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Velocity: {c(str(data.get('velocity')), 'cyan')}  "
               f"Accel: {c(str(data.get('acceleration')), 'yellow')}")
    typer.echo(f"  Trend: {c(data.get('trend', '?'), sig_color)}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")

    for snap in data.get("intervals", []):
        bar_len = min(int(snap["diversity"] * 10), 40)
        bar = "\u2588" * bar_len + "\u2591" * max(0, 40 - bar_len)
        typer.echo(f"  {c(str(snap['age_weeks']), 'gray'):>4}w ago  {bar}  {snap['entropy']:.4f}  ({snap['unique_phrases']} unique)")
        typer.echo("")


@core_app.command(name="origins",  rich_help_panel="Maintenance")
def genesis(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    top: Annotated[int, typer.Option("--top", "-n", help="Max results per category")] = 20,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    verbose: Annotated[bool, typer.Option("--verbose", help="Show more example concepts")] = False,
):
    """Concept origin: which concepts are native vs imported?

    Endogenous: exists only in one file.
    Imported: appears in 2-5 files — may be a shared dependency.
    Ambiguous: widespread (6+ files) — framework or utility concept.
    """
    from quale.reports import concept_genesis

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = concept_genesis(path, top_n=top)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    summary = data.get("summary", {})

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  CONCEPT GENESIS", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Endogenous: {c(str(summary.get('endogenous_count', 0)), 'green')}  "
               f"Imported: {c(str(summary.get('imported_count', 0)), 'cyan')}  "
               f"Ambiguous: {c(str(summary.get('ambiguous_count', 0)), 'gray')}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")

    limit = 8 if verbose else 3

    endo = data.get("endogenous", [])
    if endo:
        typer.echo(c("  LOCAL-ONLY CONCEPTS:", "green"))
        for e in endo[:limit]:
            typer.echo(f"    {c(e['concept'], 'yellow'):<30} {c(e['file'], 'green')}  ({e['count']}x)")
        typer.echo("")

    imp = data.get("imported", [])
    if imp:
        typer.echo(c("  SHARED 2-5 FILE CONCEPTS:", "cyan"))
        for i in imp[:limit]:
            typer.echo(f"    {c(i['concept'], 'yellow'):<30} primary: {i['primary_file']}  ({i['file_count']} files)")
        typer.echo("")

    amb = data.get("ambiguous", [])
    if amb:
        typer.echo(c("  WIDESPREAD CONCEPTS:", "gray"))
        for a in amb[:limit]:
            typer.echo(f"    {a['concept']}  ({a['file_count']} files across repo)")
        typer.echo("")


@core_app.command(name="coupling",  rich_help_panel="Cross-Repo")
def bond(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    top: Annotated[int, typer.Option("--top", "-n", help="Max results per bond type")] = 30,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    verbose: Annotated[bool, typer.Option("--verbose", help="Show more bond examples")] = False,
):
    """Concept coupling classification: tightly bound, loosely bound, independent.

    Covalent: concepts that always appear together (Jaccard >= 0.9).
    Ionic: concepts that bridge exactly 2 files.
    Metallic: concepts shared across 6+ files (framework pool).
    """
    from quale.reports import concept_bonds

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = concept_bonds(path, top_n=top)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    summary = data.get("summary", {})

    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  CONCEPT BONDS", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Always-together pairs: {c(str(summary.get('covalent_pairs', 0)), 'green')}  "
               f"2-file bridges: {c(str(summary.get('ionic_pairs', 0)), 'cyan')}  "
               f"Shared utility pool: {c(str(summary.get('metallic_concepts', 0)), 'gray')}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")

    limit = 8 if verbose else 3

    cov = data.get("covalent", [])
    if cov:
        typer.echo(c("  ALWAYS-TOGETHER PAIRS:", "green"))
        for pair in cov[:limit]:
            concepts = " + ".join(pair["pair"])
            typer.echo(f"    {c(concepts, 'yellow'):<60} {pair['shared_files']} files  J={pair['jaccard']}")
        typer.echo("")

    ion = data.get("ionic", [])
    if ion:
        typer.echo(c("  2-FILE BRIDGES:", "cyan"))
        for i in ion[:limit]:
            typer.echo(f"    {c(i['concept'], 'yellow'):<30} {c(i['from_file'], 'red')} → {c(i['to_file'], 'green')}")
        typer.echo("")

    met = data.get("metallic", [])
    if met:
        typer.echo(c("  SHARED UTILITY POOL:", "gray"))
        for m in met[:limit]:
            samples = ", ".join(m["sample_files"][:2])
            typer.echo(f"    {c(m['concept'], 'yellow'):<30} {m['file_count']} files  ({samples})")
        typer.echo("")


@core_app.command(name="diff-structural",  rich_help_panel="Maintenance")
def diff_structural(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    ref_a: Annotated[str | None, typer.Option("--before", help="Base ref (default: HEAD~1)")] = None,
    ref_b: Annotated[str | None, typer.Option("--after", help="Head ref (default: HEAD)")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Structural fingerprint diff between two git refs.

    Compares repo fingerprints, detects lattice defects,
    measures diversity acceleration, and lists changed files.
    All from grammar-free structural signals.
    """
    from quale.reports import structural_diff

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = structural_diff(path, ref_a=ref_a, ref_b=ref_b)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  STRUCTURAL DIFF", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Fingerprint changed: {c(str(data.get('fingerprint_changed', '?')), 'yellow')}")
    typer.echo(f"  Changed files: {c(str(data.get('changed_file_count', 0)), 'cyan')}")
    if data.get("diversity_acceleration") is not None:
        trend = data.get("diversity_trend", "stable")
        tc = "red" if trend == "accelerating" else "green"
        typer.echo(f"  Entropy: {c(trend, tc)} ({data['diversity_acceleration']})")
    defects = data.get("defects", {})
    if defects:
        total = sum(len(v) for v in defects.values())
        typer.echo(f"  Lattice defects: {c(str(total), 'yellow')}")
    changed = data.get("changed_files", [])
    if changed:
        typer.echo("")
        typer.echo(c("  Changed files:", "subheader"))
        for f in changed[:10]:
            typer.echo(f"    {c('~', 'yellow')} {f}")
        if len(changed) > 10:
            typer.echo(f"    ... and {len(changed)-10} more")
    typer.echo("")


@core_app.command(rich_help_panel="Utilities")
def ask(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    question: Annotated[str, typer.Argument(help="Question about the repo")] = "",
    files: Annotated[list[str] | None, typer.Option("--files", help="Scoped file(s); repeat or comma-separate")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Answer natural-language questions about a repo using existing structural data.

    Examples:
      quale ask "Is src/spool.ts safe to edit?"
      quale ask "What verifies changes to cli.py?"
      quale ask "What files share concepts with ingest.go?"
      quale ask "Is this repo healthy?"
      quale ask "Does this repo have tests?"
    """
    from quale.reports import answer_question

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    if not question:
        typer.echo("Provide a question, e.g.: 'Is src/spool.ts safe to edit?'", err=True)
        raise typer.Exit(1)

    data = answer_question(path, question, files=files)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    ans = data.get("answer", {})
    if isinstance(ans, dict):
        typer.echo(c(f"{'━' * 60}", "cyan"))
        typer.echo(c("  VOCAB ASK", "header"))
        typer.echo(c(f"{'━' * 60}", "cyan"))
        for key, val in ans.items():
            if isinstance(val, list):
                typer.echo(f"  {c(key.replace('_', ' ').title(), 'subheader')}:")
                for item in val[:5]:
                    if isinstance(item, dict):
                        typer.echo(f"    {'  '.join(f'{k}:{v}' for k, v in item.items()[:3])}")
                    else:
                        typer.echo(f"    {item}")
            elif isinstance(val, str):
                typer.echo(f"  {c(key.replace('_', ' ').title(), 'subheader')}: {val}")
            elif val is not None:
                typer.echo(f"  {c(key.replace('_', ' ').title(), 'subheader')}: {val}")
        typer.echo("")
    sources = data.get("sources", [])
    if sources:
        typer.echo(c(f"  Sources: {', '.join(sources)}", "gray"))
    cap = "Quale sees structure, not semantics. Answers are structural hints only."
    typer.echo(c(f"  {cap}", "gray"))


@core_app.command(name="verify-scope",  rich_help_panel="Agent Safety")
def verify_scope(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str] | None, typer.Option("--files", help="Expected/contract files; repeat or comma-separate")] = None,
    diff: Annotated[str, typer.Option("--diff", help="Git ref to diff against")] = "HEAD",
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Post-edit scope verification: compare actual diff against expected contract.

    Run after editing to verify scope matched the edit-context commitment.
    Reports scope violations, unexpected stable anchor touches, and
    produces a structural receipt.
    """
    from quale.reports import verify_scope as _verify_scope

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    if files:
        norm = []
        for item in files:
            for raw in item.split(","):
                raw = raw.strip()
                if raw:
                    norm.append(raw)
        contract_files = norm
    else:
        contract_files = None

    data = _verify_scope(path, contract_files=contract_files, diff_ref=diff, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    receipt = data.get("receipt", {})
    scope_kept = receipt.get("scope_kept", False)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  SCOPE VERIFICATION", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Scope matched: {c('YES' if scope_kept else 'NO', 'green' if scope_kept else 'red')}")
    typer.echo(f"  Expected files: {c(str(data.get('expected_count', 0)), 'cyan')}")
    typer.echo(f"  Actual files: {c(str(data.get('actual_count', 0)), 'cyan')}")

    violations = data.get("scope_violations", [])
    if violations:
        typer.echo("")
        typer.echo(c("  SCOPE VIOLATIONS:", "red"))
        for v in violations[:5]:
            typer.echo(f"    {c('✗', 'red')} {v}")
        typer.echo("")

    stable_warnings = data.get("unexpected_stable_anchors", [])
    if stable_warnings:
        typer.echo(c("  UNEXPECTED STABLE TOUCHES:", "red"))
        for s in stable_warnings[:3]:
            typer.echo(f"    {c('!', 'red')} {s.get('file', '')}")

    risk = data.get("post_edit_risk", "unknown")
    risk_c = {"low": "green", "moderate": "yellow", "high": "red", "unknown": "gray"}.get(risk, "gray")
    typer.echo(f"  Post-edit risk: {c(risk, risk_c)}  "
               f"Temp: {c(data.get('post_edit_temperature', 'WARM'), 'cyan')}")

    actual = data.get("actual_changed_files", [])
    if actual:
        typer.echo("")
        typer.echo(c("  Files changed:", "subheader"))
        for f in actual[:8]:
            status = "✓" if f in (contract_files or []) else "?"
            typer.echo(f"    {c(status, 'green' if status == '✓' else 'yellow')} {f}")
        if len(actual) > 8:
            typer.echo(f"    ... and {len(actual)-8} more")
    typer.echo("")

    checksum = data.get("repo_checksum", "")
    if checksum and format != "json":
        typer.echo(c(f"  Receipt checksum: {checksum[:16]}...", "gray"))
    typer.echo(c("  Mode: report-only receipt\n    identifies scope changes, not correctness.", "gray"))


@core_app.command(rich_help_panel="Utilities")
def calibration(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Show quale's accuracy on this repo from past verify-scope runs.

    Tracks verification hit rate and scope accuracy over time.
    Requires verify-scope to have been run at least 3 times on this repo.
    """
    from quale.reports import compute_calibration

    path = os.path.abspath(path)
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = compute_calibration(path)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    def c(t, col):
        return _color(t, col)
    records = data.get("records", 0)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VOCAB CALIBRATION", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    if records == 0:
        typer.echo(f"  {c(data.get('note', 'No records.'), 'yellow')}")
        return
    typer.echo(f"  Records: {c(str(records), 'cyan')} past verify-scope runs on this repo")

    scope = data.get("scope_accuracy", 0)
    sc = "green" if scope >= 0.8 else "yellow"
    scope_val = f"Vocab's scope predictions are {'reliable' if scope >= 0.8 else 'moderately reliable' if scope >= 0.5 else 'unreliable'}. Verify scope manually."
    typer.echo(f"  {c('SCOPE ACCURACY', sc)}: {c(f'{scope:.0%}', sc)}  {c(f'— {scope_val}', 'gray')}")

    verify = data.get("verification_accuracy", 0)
    vc = "green" if verify >= 0.6 else "yellow"
    verify_val = f"Vocab's test suggestions are {'reliable' if verify >= 0.6 else 'unreliable'}. Trust verification candidates {'cautiously' if verify < 0.6 else 'as strong hints'}."
    typer.echo(f"  {c('VERIFY ACCURACY', vc)}: {c(f'{verify:.0%}', vc)}  {c(f'— {verify_val}', 'gray')}")

    if "risk_high_violation_rate" in data:
        rv = data["risk_high_violation_rate"]
        rv_val = f"High-risk warnings were violated {rv:.0%} of the time. {'Escalate confidence in risk warnings.' if rv < 0.5 else 'Risk warnings are underconfident — treat HIGH risk seriously.'}"
        typer.echo(f"  {c('RISK CALIBRATION', 'red')}: HIGH violations: {c(f'{rv:.0%}', 'red')}  {c(f'— {rv_val}', 'gray')}")

    if "warning" in data:
        typer.echo(c(f"  ⚠ {data['warning']}", "yellow"))
    typer.echo("")


def _classify_verify_types(candidates: list[str], changed_files: list[str]) -> dict[str, str]:
    """Label each verification candidate with its test type."""
    changed_dirs = set()
    for f in changed_files:
        d = os.path.dirname(f)
        if d:
            changed_dirs.add(d)
    types = {}
    for c in candidates:
        cdir = os.path.dirname(c)
        if cdir in changed_dirs:
            types[c] = "unit"
        elif "tests/" in c or "test/" in c or cdir in ("tests", "test"):
            types[c] = "integration"
        else:
            types[c] = "cross_package"
    return types


def _desert_text(ver_confidence: dict, changed_files: list[str]) -> str:
    """Return a desert-warning string for the tool format, or empty if confidence is high."""
    level = ver_confidence.get("level", "high") if isinstance(ver_confidence, dict) else "high"
    if level == "high":
        return ""
    reasons = ver_confidence.get("reasons", []) if isinstance(ver_confidence, dict) else []
    if not changed_files and level in ("low", "unknown"):
        return "No files provided; verification suggestions may be unreliable."
    if level == "low" and reasons:
        return f"Verification confidence is low: {'; '.join(reasons)}"
    return f"Verification confidence is {level}; structurally conservative."


@core_app.command(name="escape-velocity", rich_help_panel="Maintenance")
def escape_velocity_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Phrase removal difficulty: ESCAPED / BOUND / DEEP."""

    from quale.reports import escape_velocity_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = escape_velocity_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    tagged = data.get("tagged", [])
    typer.echo(f"Identifier reach across the repo:")
    for t in tagged[:8]:
        note = ""
        if t["label"] == "ESCAPED":
            note = " \u2014 appears outside origin module, hard to rename/remove"
        elif t["label"] == "BOUND":
            note = " \u2014 mostly contained, moderate removal difficulty"
        elif t["label"] == "DEEP":
            note = " \u2014 internal only, safe to rename locally"
        typer.echo(f'  {t["label"]:<8} {t["phrase"]}{note}')
@core_app.command(name="trap", rich_help_panel="Code Analysis")
def trap_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file_a: Annotated[str, typer.Option("--file-a", help="First file")] = "",
    file_b: Annotated[str, typer.Option("--file-b", help="Second file")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Identifier overlap between two concurrently-edited files."""

    from quale.reports import trap_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file_a or not file_b:
        typer.echo("provide --file-a and --file-b", err=True)
        raise typer.Exit(1)
    data = trap_report(path=p, file_a=file_a, file_b=file_b)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    overlap = data.get("overlap", 0)
    label = data.get("label", "")
    if overlap >= 0.8:
        typer.echo(f'  \033[31m\u26a0 High merge risk\033[0m \u2014 {overlap:.0%} identifier overlap')
        typer.echo('  \033[90mThese files share many identifiers. Concurrent edits risk naming conflicts.\033[0m')
    elif overlap >= 0.5:
        typer.echo(f'  \033[33m\u25cf Moderate overlap\033[0m \u2014 {overlap:.0%}')
    else:
        typer.echo(f'  \033[32m\u25cb Low overlap\033[0m \u2014 {overlap:.0%} \u2014 safe to edit concurrently')

@core_app.command(name="hub-risk", rich_help_panel="Code Analysis")
def thanatosis_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """High-centrality files with zero edits."""

    from quale.reports import thanatosis_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = thanatosis_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    files = data.get("files", [])[:5]
    typer.echo("Files that are highly coupled but rarely edited:")
    for f in files:
        typer.echo(f'  \u25cf {f["file"]} \u2014 {f["centrality"]} file couplings, {f["edits"]} edits')
    if files:
        typer.echo("These files touch many others but are not actively maintained.")

@core_app.command(name="complexity-ratio", rich_help_panel="Code Analysis")
def trompe_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to analyze")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Apparent lines vs unique identifiers."""

    from quale.reports import trompe_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True)
        raise typer.Exit(1)
    data = trompe_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo(f'Trompe: {data.get("trompe_ratio",0)} — {data.get("label","")}')

@core_app.command(name="porosity", rich_help_panel="Code Analysis")
def porosity_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Sparse coupling estimate without computing co-occurrence."""

    from quale.reports import porosity_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = porosity_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    p = data.get("porosity", 0)
    if p > 0.9:
        label = "High (low coupling)"
    elif p > 0.5:
        label = "Moderate"
    else:
        label = "Low (dense coupling)"
    typer.echo(f"Coupling sparsity: {p:.4f} \u2014 {label}")
    typer.echo("  Higher values = less co-occurrence = lower coupling (good).")

@core_app.command(name="extinct-exports", rich_help_panel="Maintenance")
def thylacine_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Multi-file exports never imported externally."""

    from quale.reports import thylacine_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = thylacine_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    thy = data.get("thylacines", [])
    typer.echo(f'Exports declared in multiple files but never imported externally:')
    typer.echo(f'  Count: {len(thy)}')
    for t in thy[:5]:
        typer.echo(f'  \u25cf {t["identifier"]} (defined in {t["files"]} files)')
    if thy:
        typer.echo('  Next: quale core cleanup-list | quale core escape-velocity')

@core_app.command(name="coupling-chain", rich_help_panel="Code Analysis")
def tensegrity_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Indirect coupling with no direct edge."""

    from quale.reports import tensegrity_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = tensegrity_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    pairs = data.get("tensegrity_pairs", [])
    typer.echo(f"Indirectly coupled file pairs (no direct edge):")
    for tp in pairs[:5]:
        typer.echo(f'  \u25cf {tp["file_a"]} <-> {tp["file_b"]} ({tp["count"]} indirect links)')
    if not pairs:
        typer.echo("  None found \u2014 no indirect coupling detected.")

@core_app.command(name="criticality", rich_help_panel="Code Analysis")
def criticality_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to analyze")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """2-hop amplification ratio: changes amplify or dampen."""

    from quale.reports import criticality_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = criticality_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    scores = data.get("scores", [])
    typer.echo(f"Change amplification risk (k > 1 = changes cascade):")
    for s in scores[:5]:
        marker = "\u26a0" if s["k"] > 1 else "\u25cb"
        typer.echo(f'  {marker} {s["file"]}: amp={s["k"]:.2f} ({s["class"]})')
    if not scores:
        typer.echo("  No files with notable amplification.")


@core_app.command(name="guard", rich_help_panel="Agent Safety")
def guard_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str | None, typer.Option("--file", help="File to guard against")] = None,
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
) -> None:
    """Combined safety packet: guide + hub-risk + complexity + criticality. """
    from quale.reports import guard_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = guard_report(path=p, file_path=file, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if format == "tool":
        tool_data = {
            "schema_version": 1,
            "file": data.get("file"),
            "risk": data.get("risk", "unknown"),
            "guide": data.get("guide"),
            "hub_risk": data.get("hub_risk", []),
            "complexity_ratio": data.get("complexity_ratio"),
            "criticality": data.get("criticality", {}),
            "stable_anchors_touched": data.get("stable_anchors_touched", []),
            "reverse_blast": data.get("reverse_blast", []),
            "_agent_note": "--file takes a single file path (not --files); no comma-separation",
        }
        typer.echo(json.dumps(tool_data, separators=(",", ":")))
        return
    for k, v in data.items():
        if k in ("file", "task") or not v:
            continue
        if k in ("risk",):
            typer.echo(f'  Risk score: {v} (0=low, higher=more coupling exposure)')
        elif isinstance(v, list):
            for item in v[:3]:
                typer.echo(f'  \u25cf {item}')
        elif isinstance(v, dict):
            for sk, sv in v.items():
                typer.echo(f'  {sk}: {sv}')
        else:
            typer.echo(f'  {k}: {v}')

@core_app.command(name="check-pr", rich_help_panel="CI")
def check_pr_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    base: Annotated[str, typer.Option("--base", help="Base git ref")] = "HEAD~1",
    head: Annotated[str, typer.Option("--head", help="Target git ref")] = "HEAD",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """CI PR summary: parity-bit + trap + diff. [INFO: always exits 0]"""
    from quale.reports import check_pr_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = check_pr_report(path=p, base_ref=base, head_ref=head)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    unch = data.get("parity", {}).get("unchanged", False)
    typer.echo(f"Structural hash: {'\u2713 unchanged' if unch else '\u26a0 changed since base ref'}")
    for tp in data.get("trap", [])[:3]:
        la = tp.get("label", "")
        fa = tp.get("file_a", "") or tp.get("file", "")
        fb = tp.get("file_b", "")
        if not fa and not fb:
            continue
        mark = "\u26a0" if "HIGH" in la or "OVERLAP" in la.upper() else "\u25cf"
        pair = f"{fa} <-> {fb}" if fb else fa
        typer.echo(f'  {mark} {pair}: {la}')

@core_app.command(name="cleanup-list", rich_help_panel="Maintenance")
def cleanup_list_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Prioritized cleanup: extinct-exports x escape-velocity."""

    from quale.reports import cleanup_list_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = cleanup_list_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    items = data.get("items", [])
    typer.echo(f'{len(items)} candidates for cleanup:')
    for i in items[:5]:
        label = i.get("effort", "?")
        note = " (appears outside origin module)" if label == "ESCAPED" else (" (mostly contained)" if label == "BOUND" else "")
        typer.echo(f'  \u25cf {i["identifier"]}: {label}{note} \u2014 {i["files"]} files')

@core_app.command(name="vulnerability-map", rich_help_panel="Maintenance")
def vulnerability_map_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """Overlap of hub-risk and capillary."""

    from quale.reports import vulnerability_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = vulnerability_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    dt = len(data.get("don_touch", []))
    ch = len(data.get("churn_hubs", []))
    cr = len(data.get("critical", []))
    typer.echo(f"  Don't-touch (hub-risk + capillary): {dt} files")
    typer.echo(f"  Churn hubs (high edit + high coupling): {ch} files")
    typer.echo(f"  Critical (all three): {cr} files")
    if cr:
        typer.echo("  \u26a0 These files are high-risk: coupled, connected, and critical.")

@core_app.command(name="health-score", rich_help_panel="CI")
def health_score_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
) -> None:
    """2-axis health: coupling density x modularity. """
    from quale.reports import repo_health as health_score
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = health_score(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    coupling = "coupled" if data.get("excess_porosity", 0) < 0 else "sparse"
    mod = "gapped" if data.get("spectral_gap", 0) >= 2 else ("moderate" if data.get("spectral_gap", 0) >= 1 else "flat")
    if coupling == "coupled" and mod in ("gapped", "moderate"):
        note = "High coupling with weak modularity. Consider refactoring."
    elif coupling == "coupled":
        note = "High coupling density. Files share many identifiers."
    else:
        note = "Low coupling, well-modularized."
    typer.echo(f"Structural health: {coupling} + {mod}")
    typer.echo(f"  {note}")


@cli.command(name="review", rich_help_panel="Code Analysis")
def review_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    base_ref: Annotated[str, typer.Option("--base", "-b", help="Base git ref")] = "HEAD~1",
    head_ref: Annotated[str, typer.Option("--head", "-H", help="Target git ref")] = "HEAD",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Single-review summary: blast radius, test gaps, hub risk, clones.

    Combines ci-report, mirror signals, and hub-risk into one human-readable
    review card. Run before opening a PR.

    Examples:
      quale review
      quale review --base origin/main --format json
    """
    from quale.reports import review_summary
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = review_summary(path=p, base_ref=base_ref, head_ref=head_ref)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo("")
    typer.echo(_color(f"═══ Review: {base_ref} \u2192 {head_ref} ({len(data.get('changed_files', []))} files) ═══", "header"))
    typer.echo("")

    # Per-file annotations
    typer.echo("  Changes:")
    for f in data.get("file_annotations", []):
        anns = f.get("annotations", [])
        if f["severity"] == "high":
            mark = f"  \033[31m\u25cf {f['file']}\033[0m"
        elif f["severity"] == "medium":
            mark = f"  \033[33m\u25cf {f['file']}\033[0m"
        else:
            mark = f"  \033[32m\u25cb {f['file']}\033[0m"
        ann_text = f" \u2014 {', '.join(anns)}" if anns else ""
        typer.echo(f"{mark}{ann_text}")

    # Test connections
    typer.echo("")
    typer.echo("  Test connections:")
    for tm in data.get("test_mirrors", []):
        if tm.get("has_mirror"):
            mf = tm["mirror_files"][0]
            typer.echo(f"    \033[32m\u25cf {tm['source']} \u2192 {mf}\033[0m")
        else:
            typer.echo(f"    \033[33m\u25cb {tm['source']} \u2192 (no test mirror found)\033[0m")

    # Risk flags from ci_report
    typer.echo("")
    typer.echo("  Risk flags:")
    for rf in data.get("risk_flags", []):
        typer.echo(f"    \033[31m\u26a0 {rf}\033[0m")
    if not data.get("risk_flags"):
        typer.echo("    \033[32mNone\033[0m")

    # Action items
    action_items = []
    stable_anchors = data.get("stable_anchors_touched", [])
    for sa in stable_anchors:
        action_items.append(f"Review {sa} carefully \u2014 stable file, rarely changes")
    mirror_gap = data.get("mirror_gap_ratio", 1.0)
    if mirror_gap < 0.5:
        action_items.append(f"Add or update tests \u2014 only {mirror_gap:.0%} of changed files have test mirrors")
    if data.get("hub_risk_flagged"):
        action_items.append("Check hub-risk files \u2014 they have high coupling")
    if data.get("clone_flagged"):
        action_items.append("Review structural clones \u2014 similar files may need refactoring")
    if data.get("blast_radius_count", 0) > 0:
        action_items.append(f"Run `quale core diff-structural` for full structural diff")
    typer.echo("")
    typer.echo("  Action items:")
    for i, ai in enumerate(action_items[:5], 1):
        typer.echo(f"    {i}. {ai}")
    if not action_items:
        typer.echo("    \033[32mNo action items \u2014 clean change set.\033[0m")

    # Verdict line
    typer.echo("")
    verdict = data.get("review", "FAIL")
    summary = data.get("summary", "")
    if verdict == "PASS":
        typer.echo(f"  \033[32mResult: PASS \u2014 {summary}\033[0m")
    else:
        typer.echo(f"  \033[31mResult: {verdict} \u2014 {summary}\033[0m")


@cli.command(name="onboard", rich_help_panel="Code Analysis")
def onboard_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Onboarding plan: landmark files, module map, safe directories.

    Produces a 3-step plan for a new developer joining the codebase.
    Shows what to read first, which modules exist, and what's safe to edit.

    Examples:
      quale onboard
      quale onboard --format json
    """
    from quale.reports import onboard_plan
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = onboard_plan(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo("")
    typer.echo(_color("═══ Onboarding Plan ═══", "header"))
    langs = data.get("languages", [])
    if langs:
        lang_str = ", ".join(f"{l}({c})" for l, c in langs)
        typer.echo(f"  Languages: {lang_str}")
        typer.echo(f"  Total files: {data.get('total_files', 0)}")
    for step in data.get("steps", []):
        n = step.get("step", 0)
        title = step.get("title", "")
        items = step.get("items", [])
        typer.echo(f"")
        typer.echo(f"  \033[36mStep {n}\033[0m \033[1m{title}\033[0m")
        for item in items[:5]:
            if "file" in item:
                file_part = item["file"]
                why_part = item.get("why", "")
                if item.get("file") == "(none)":
                    typer.echo(f"    \033[32m\u25cb {file_part} \u2014 {item.get('risk', '')}\033[0m")
                elif "risk" in item:
                    typer.echo(f"    \033[33m\u26a0 {file_part} \u2014 {item.get('risk', '')}\033[0m")
                else:
                    typer.echo(f"    \033[36m\u25cf {file_part}\033[0m  \033[90m{why_part}\033[0m")
            elif "module" in item:
                samples = ", ".join(item.get("sample_files", [])[:2])
                typer.echo(f"    \033[35m\u25cf {item['module']}\033[0m  \033[90m({item.get('file_count', 0)} files: {samples})\033[0m")


@cli.command(name="refactor-cost", rich_help_panel="Code Analysis")
def refactor_cost_cmd(
    file_path: Annotated[str, typer.Argument(help="File to estimate refactoring effort")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Estimate refactoring effort for a file: blast + escape + clones + hub.

    Combines direct impact count, transitive coupling, escape velocity,
    structural clones, and hub-risk percentile into a single effort tier.

    Examples:
      quale refactor-cost src/spool.ts
      quale refactor-cost src/spool.ts --format json
    """
    from quale.reports import refactor_effort
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = refactor_effort(path=p, file_path=file_path)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    effort = data.get("effort", "UNKNOWN")
    teal = "\033[36m"
    green = "\033[32m"
    yellow = "\033[33m"
    red = "\033[31m"
    reset = "\033[0m"
    if effort == "LOW":
        color = green
    elif effort == "MEDIUM":
        color = yellow
    else:
        color = red
    direct = data.get('direct_impact', 0)
    transitive = data.get('transitive_estimate', 0)
    escaped = data.get('escape_velocity', 'UNKNOWN') == 'ESCAPED'
    hub_pct = data.get('hub_percentile', 0)
    clones = data.get("clone_files", [])
    pre = data.get("pre_clean", [])

    typer.echo("")
    typer.echo(f"  {teal}═══ Refactor Cost: {file_path} ═══{reset}")
    typer.echo("")
    if direct <= 3 and not escaped:
        typer.echo(f"  {green}Simple change — mostly self-contained.{reset}")
    elif direct <= 15 and not escaped:
        typer.echo(f"  {yellow}Moderate impact — changing this file will touch a few others.{reset}")
    else:
        typer.echo(f"  {red}High impact — this change touches {direct} files total. Plan carefully.{reset}")
    typer.echo("")
    typer.echo("  What this means:")
    if direct > 0:
        typer.echo(f"   • This file shares identifiers with {direct} file(s). If you rename a symbol, all of them need updating.")
    if transitive > direct:
        typer.echo(f"   • Indirectly, ~{transitive} files import those {direct} files — the ripple effect is wider than the direct count.")
    if escaped:
        typer.echo(f"   • Its vocabulary has \"escaped\" beyond the original module — other files now depend on concepts defined here.")
    if hub_pct > 0:
        typer.echo(f"   • It's more coupled than {100 - hub_pct}% of files in this repo.")
    if clones:
        typer.echo(f"   • {len(clones)} structural clone(s) exist: {', '.join(clones[:3])}. Fixing one means fixing them all.")
    typer.echo("")
    if effort == "LOW":
        typer.echo(f"  {color}Estimated effort: {effort} — straightforward, safe to start{reset}")
    elif effort == "MEDIUM":
        typer.echo(f"  {color}Estimated effort: {effort} — plan for an afternoon{reset}")
    else:
        typer.echo(f"  {color}Estimated effort: {effort} — may take a day or more{reset}")
    if pre:
        typer.echo(f"  Pre-clean: Refactor clones first: {', '.join(pre[:3])}")
    if data.get("risk_note"):
        typer.echo(f"  Risk: {data['risk_note']}")


@core_app.command(name="ci-trend", rich_help_panel="CI")
def ci_trend_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Window in weeks")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """CI metric trends: blast radius, mirror gap, health over time.

    Reads .quale/ci-history.jsonl (appended on each ci-report run) and
    reports trend slopes.

    Examples:
      quale ci-trend
      quale ci-trend --weeks 24 --format json
    """
    from quale.reports import ci_trend
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = ci_trend(path=p, weeks=weeks)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo("")
    typer.echo(_color(f"═══ CI Trend (last {weeks} weeks) ═══", "header"))
    for metric, info in data.get("trends", {}).items():
        vals = info.get("values", [])
        trend = info.get("trend", "unknown")
        typer.echo(f"  {metric:<20} {', '.join(str(v) for v in vals)} ({trend})")

def _build_edit_tool_format(data, verify_candidates, vtypes, ver_confidence, scope_creep, scope_creep_instruction):
    return {
        "schema_version": 1,
        "risk": data.get("risk", "unknown"),
        "confidence": data.get("confidence", "unknown"),
        "reason": "; ".join(data.get("reasons", [])),
        "changed_files": data.get("changed_files", []),
        "read_first": data.get("fused_first", data.get("read_first", [])),
        "verification_mc": {
            "question": "Which file would verify this change?",
            "candidates": verify_candidates[:5] if verify_candidates else [],
            "max_selections": 1,
            "types": vtypes,
        },
        "verification_confidence": ver_confidence,
        "expansion_risk": data.get("expansion_risk", data.get("avoid_expanding_into", [])),
        "scope_creep_guard": {**scope_creep, "instruction": scope_creep_instruction},
        "desert_warning": _desert_text(ver_confidence, data.get("changed_files", [])),
        "guardrails": data.get("guardrails", {}),
        "spectrum": data.get("spectrum"),
        "deficit": data.get("deficit"),
        "cascade": data.get("cascade"),
        "cross_cutting": data.get("cross_cutting"),
        "risk_vector": data.get("risk_vector"),
        "acceleration": data.get("acceleration"),
        "boundary": data.get("boundary"),
        "module_exposure": data.get("module_exposure"),
        "_agent_note": "--files takes comma-separated paths, not repeated flags; pipe via 2>/dev/null for clean JSON",
    }

# ── Agent Namespace ────────────────────────────────────────────────────────

@agent_app.command(name="edit")
def agent_edit(
    file: Annotated[str, typer.Argument(help="File being edited")],
    task: Annotated[str, typer.Option("--task", help="The goal of the edit")] = "",
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """File-scoped edit context and risk card (proven tool format)."""
    import json
    from quale.reports import preflight_report
    import os
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    report = preflight_report(p, [file], diff_ref=None, task=task)
    if "error" in report:
        typer.echo(report["error"], err=True)
        raise typer.Exit(1)
    cand = report.get("verification_candidates", report.get("verify_with", []))
    vc = report.get("verification_confidence", {})
    sc = report.get("scope_creep_guard", {})
    wa = sc.get("warnings", [])
    qs = [w.get("question_extras", "").strip() for w in wa if w.get("question_extras")]
    sci = "Before broadening scope, verify each extra file: " + "; ".join(qs) if qs else "Do not propose extra_edits unless the task explicitly requires them."
    vt = _classify_verify_types(cand[:5] if cand else [], report.get("changed_files", []))
    tool_data = _build_edit_tool_format(report, cand, vt, vc, sc, sci)
    typer.echo(json.dumps(tool_data, separators=(",", ":")))

@agent_app.command(name="orient")
def agent_orient(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """Token-optimized repo orientation for LLM Agents.

    Returns a structured JSON payload: repo map, landmarks (what to read),
    modules, and a recommended workflow.
    """
    import json
    from quale.reports import orient_report
    import os
    p = os.path.abspath(path)
    try:
        data = orient_report(p)
        if "error" in data:
            typer.echo(json.dumps(data), err=True)
            raise typer.Exit(1)
        typer.echo(json.dumps(data, indent=2))
    except Exception as e:
        typer.echo(json.dumps({"error": str(e)}))
        raise typer.Exit(1)

@agent_app.command(name="guard")
def agent_guard(
    file: Annotated[str, typer.Argument(help="File to guard")],
    task: Annotated[str, typer.Option("--task", help="The goal of the edit")] = "",
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """Combined safety packet (tool format)."""
    import json
    from quale.reports import guard_report
    import os
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = guard_report(p, file_path=file, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    tool_data = {
        "schema_version": 1,
        "file": data.get("file"),
        "risk": data.get("risk", "unknown"),
        "guide": data.get("guide"),
        "hub_risk": data.get("hub_risk", []),
        "complexity_ratio": data.get("complexity_ratio"),
        "criticality": data.get("criticality", {}),
        "stable_anchors_touched": data.get("stable_anchors_touched", []),
        "reverse_blast": data.get("reverse_blast", []),
        "_agent_note": "--file takes a single file path; no comma-separation",
    }
    typer.echo(json.dumps(tool_data, separators=(",", ":")))


# ── CI Namespace ──────────────────────────────────────────────────────────

@ci_app.command(name="init")
def ci_init(
    path: Annotated[str, typer.Option("--path", "-p", help="Repo root")] = ".",
):
    """Generates GH Actions YAML."""
    import os
    import yaml
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    workflow_dir = os.path.join(p, ".github/workflows")
    os.makedirs(workflow_dir, exist_ok=True)
    workflow_path = os.path.join(workflow_dir, "quale.yml")
    
    content = """name: quale guardrails
on: [pull_request]

jobs:
  guardrails:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Install quale
        run: pip install quale
      - name: CI gate
        run: quale ci check origin/${{ github.base_ref }} HEAD
      - name: PR comment
        run: quale ci comment origin/${{ github.base_ref }} HEAD
      - name: Smoke test — all commands
        run: python -m pytest tests/test_cli_smoke.py -v
      - name: Output contracts
        run: python -m pytest tests/test_output_contracts.py -v
      - name: Dogfood — self-review
        run: |
          quale review --path .
          quale onboard --path .
          quale agent orient --path .
"""
    with open(workflow_path, "w") as f:
        f.write(content)
    typer.echo(f"Created {workflow_path}")


@ci_app.command(name="check")
def ci_check(
    base_ref: Annotated[str, typer.Argument(help="Base git ref")],
    head_ref: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """Runs all gates (exits 0-7)."""
    import yaml
    from quale.cli import ci_report_cmd
    import os
    p = os.path.abspath(path)
    
    # Defaults
    blast_tier = "high"
    mirror_gap = 0.50
    stable_touched = True
    hub_risk = True
    clone_fail = True
    new_identifiers = 30
    
    config_path = os.path.join(p, ".quale.yml")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            ci_config = config.get("ci", {})
            blast_tier = ci_config.get("fail-on-blast-tier", blast_tier)
            mirror_gap = ci_config.get("fail-on-mirror-gap", mirror_gap)
            stable_touched = ci_config.get("fail-on-stable-touched", stable_touched)
            hub_risk = ci_config.get("fail-on-hub-risk", hub_risk)
            clone_fail = ci_config.get("fail-on-clone", clone_fail)
            new_identifiers = ci_config.get("fail-on-new-identifiers", new_identifiers)
        except Exception:
            pass
            
    ci_report_cmd(
        ref_a=base_ref,
        ref_b=head_ref,
        path=path,
        summary=True,
        fail_blast_tier=blast_tier,
        fail_mirror_gap=mirror_gap,
        fail_stable_touched=stable_touched,
        fail_hub_risk=hub_risk,
        fail_clone=clone_fail,
        fail_new_identifiers=new_identifiers
    )


@ci_app.command(name="comment")
def ci_comment(
    base_ref: Annotated[str, typer.Argument(help="Base git ref")],
    head_ref: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    pr_number: Annotated[int, typer.Option("--pr", help="PR number")] = None,
):
    """Posts the PR report."""
    from quale.cli import pr_report
    pr_report(ref_a=base_ref, ref_b=head_ref, path=path, post_comment=True, pr_number=pr_number)


@ci_app.command(name="trend")
def ci_trend_wrapper(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history to analyze")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    """CI metric trends over time."""
    from quale.cli import ci_trend_cmd
    ci_trend_cmd(path=path, weeks=weeks, format=format)

if __name__ == "__main__":
    _entry_main()



