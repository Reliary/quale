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

from vocab.scanner import (scan_codebase, search_cross_repo,
                           search_cross_repo_ranked)
from vocab.bootstrap import (bootstrap_repo, explore_repo, compute_modules)
from vocab.reports import (ci_report, inspect_repo, repo_fingerprint,
                           compute_stability, compute_lifecycles, concept_timeline,
                           preflight_report, build_contract, validate_plan)
from vocab.compare import (compare_repos, phrase_provenance, pr_blast_radius)
from vocab.formats.terminal import (format_terminal, format_json, format_html, format_quick,
                                     format_lifecycles, format_blast_radius,
                                     format_lifecycles_json, format_blast_json,
                                     format_orphans_json, format_pr_report_markdown,
                                     format_search_json, format_search_compact,
                                     format_modules, format_modules_json)
from vocab.index import encode_indices, decode_indices, index_sequence_hash, structural_similarity
from vocab.vocabulary import build_vocabulary
from vocab.segmenter import segment
from vocab import git as vgit
from vocab.config import load_config


cli = typer.Typer(
    help="""
    vocab — grammar-free structural codebase analyzer.

    Orients you, catches hidden dependencies, and reveals architecture without parsers.

    Workflow groups (common uses):
      ORIENTATION    agent-bootstrap, explore, modules, repo-map, inspect
      PREFLIGHT      edit-context, contract, check-plan, verify-scope, diff-structural, route, negotiate
      HISTORY        timeline, lifecycle, stable, provenance, origins
      CROSS-REPO     compare, search, coupling, anomalies
      CI/GATES       ci-report, pr-report, gate
      UTILITIES      analyze, diff, fingerprints, vocabulary-trend, patterns, stop, help-agent, ask, calibration
    """
)


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


def _gate_evaluation(data: dict, fail_mirror_gap: float | None,
                     fail_blast_tier: str | None,
                     fail_stable_touched: bool) -> tuple[list[tuple[int, str]], list[str]]:
    failures = []
    checks = []
    if fail_mirror_gap is not None:
        ratio = data.get("mirror_gap_ratio", 1.0)
        checks.append(f"mirror gap {ratio:.0%} >= {fail_mirror_gap:.0%}")
        if ratio < fail_mirror_gap:
            failures.append((1, f"mirror gap {ratio:.0%} < {fail_mirror_gap:.0%}"))
    if fail_blast_tier is not None:
        tier_order = {"none": 0, "local": 1, "moderate": 2, "high": 3, "critical": 4}
        threshold = tier_order.get(fail_blast_tier.lower())
        if threshold is None:
            raise ValueError("Invalid blast tier. Use: local, moderate, high, critical.")
        current_tier = data.get("max_blast_tier", "none")
        checks.append(f"blast tier {current_tier} < {fail_blast_tier.lower()}")
        if tier_order.get(current_tier, 0) >= threshold:
            failures.append((2, f"blast tier {current_tier} >= {fail_blast_tier.lower()}"))
    if fail_stable_touched:
        count = data.get("stable_touched_count", 0)
        checks.append(f"stable anchors touched {count} == 0")
        if count > 0:
            failures.append((3, f"{count} stable anchors touched"))
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


@cli.command(rich_help_panel="Inspection")
def analyze(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json, html, quick")] = "terminal",
    ref: Annotated[str | None, typer.Option("--ref", "-r", help="Git ref to analyze")] = None,
    clones: Annotated[bool, typer.Option("--clones", help="Enable structural clone detection (slower)")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Enable deep analysis: co-occurrence matrix, clusters, landmarks (slower on large repos)")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colored output")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only output on error")] = False,
):
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


@cli.command(rich_help_panel="Inspection")
def diff(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    why: Annotated[bool, typer.Option("--why", help="Show why this diff matters structurally")] = False,
):
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    try:
        _validate_refs(path, ref_a, ref_b)
    except ValueError as e:
        typer.echo(str(e), err=True)
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
    if why:
        from vocab.formats.terminal import _why_diff
        data = {"changed_files": [], "impacts": [], "mirror_ratio": None}
        typer.echo(_why_diff(data, ref_a, ref_b))


@cli.command(rich_help_panel="Inspection")
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


@cli.command(rich_help_panel="History")
def lifecycle(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 24,
    signal: Annotated[str | None, typer.Option("--signal", "-s", help="Filter by signal type: DEAD, GROWING, STABLE, etc.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
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
        typer.echo(format_lifecycles(data, weeks, show_all=False))


@cli.command(rich_help_panel="CI")
def blast(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
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


@cli.command(name="edit-context",  rich_help_panel="Agent")
def preflight(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str] | None, typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = None,
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against the working tree")] = None,
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: tool(default), verify, json, checklist, compact, llm, full")] = "tool",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show math-heavy signals (SNR, expansion risk details)")] = False,
    why: Annotated[bool, typer.Option("--why", help="Show why each recommendation exists")] = False,
):
    """File-scoped edit context and risk card.

    Examples:
      vocab edit-context --files src/spool.ts --task "change upload behavior"
      vocab edit-context --diff HEAD~1 --format json
      vocab edit-context --files src/spool.ts --format tool
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

    data = preflight_report(path=path, files=files, diff_ref=diff, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)

    verify_candidates = data.get("verification_candidates", data.get("verify_with", []))
    ver_confidence = data.get("verification_confidence", {})
    sprawl = data.get("edit_sprawl_guard", {})
    wa = sprawl.get("warnings", [])
    qs = [w.get("question_extras", "").strip() for w in wa if w.get("question_extras")]
    sprawl_instruction = (
        "Before broadening scope, verify each extra file: " + "; ".join(qs)
        if qs else
        "Do not propose extra_edits unless the task explicitly requires them."
    )
    vtypes = _classify_verify_types(verify_candidates[:5] if verify_candidates else [], data.get("changed_files", []))

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    if format == "llm":
        from vocab.formats.llm import format_preflight_llm
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
        tool_data = {
            "schema_version": 1,
            "risk": data.get("risk", "unknown"),
            "confidence": data.get("confidence", "unknown"),
            "reason": "; ".join(data.get("reasons", [])),
            "changed_files": data.get("changed_files", []),
            "read_first": data.get("read_first", []),
            "verification_mc": {
                "question": "Which file would verify this change?",
                "candidates": verify_candidates[:5] if verify_candidates else [],
                "max_selections": 1,
                "types": vtypes,
            },
            "verification_confidence": ver_confidence,
            "expansion_risk": data.get("expansion_risk", data.get("avoid_expanding_into", [])),
            "edit_sprawl_guard": {**sprawl, "instruction": sprawl_instruction},
            "desert_warning": _desert_text(ver_confidence, data.get("changed_files", [])),
            "guardrails": data.get("guardrails", {}),
        }
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
            "read_first": data.get("read_first", []),
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
            "edit_sprawl_guard": {**sprawl, "instruction": sprawl_instruction},
            "desert_warning": _desert_text(ver_confidence, data.get("changed_files", [])),
            "co_change": data.get("co_change", []),
            "structural_orphans": data.get("structural_orphans", []),
            "file_classifications": data.get("file_classifications", []),
            "keystone_files": data.get("keystone_files", []),
            "snr_annotations": snr,
            "capability_boundary": capability,
            "guardrails": data.get("guardrails", {}),
        }
        typer.echo(json.dumps(tool_data, indent=2))
        return
    if format == "checklist":
        _print_preflight_checklist(data)
        return
    data["verbose"] = verbose
    _print_preflight(data)
    if why:
        from vocab.formats.terminal import _why_edit_context
        typer.echo(_why_edit_context(data))


@cli.command(rich_help_panel="Agent")
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
    typer.echo(json.dumps(data, separators=(",", ":")))


@cli.command(name="check-plan",  rich_help_panel="Agent")
def check_plan(
    contract_file: Annotated[Path, typer.Option("--contract", "-c", help="Contract JSON file")],
    proposal_file: Annotated[Path | None, typer.Option("--proposal", "-p", help="Proposal JSON file; stdin when omitted")] = None,
    allow_paths: Annotated[bool, typer.Option("--allow-paths", help="Allow raw paths in proposal (not recommended)")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: tool(default), json, compact")] = "tool",
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
    if format == "compact":
        if result.get("valid"):
            typer.echo("VALID plan: scope contained")
        elif result.get("needs_reflight"):
            typer.echo("NEEDS_REFLIGHT: scope expansion requested")
        else:
            codes = ", ".join(v.get("code", "unknown") for v in result.get("violations", []))
            typer.echo(f"INVALID plan: {codes}")
        return
    if format == "json":
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(json.dumps(result, separators=(",", ":")))


@cli.command(name="repo-map",  rich_help_panel="Inspection")
def crystallography(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """One-time structural description of a codebase.

    Designed for LLM use: produces a compact skeleton (~100 tokens)
    plus structured detail about test conventions, stable core,
    generated files, and module boundaries. Cache and reuse.
    """
    from vocab.reports import crystallography as _crystallography

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

    c = lambda t, col: _color(t, col)

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
            typer.echo(f"    {c(str(s.get('persistence', 0)), 'green'):>6.0%}  {s['file']}")
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


@cli.command(rich_help_panel="Verification")
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
    from vocab.reports import preflight_report

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


@cli.command(name="reverse-verify",  rich_help_panel="Verification")
def reverse_verify(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed test file(s); repeat or comma-separate")] = [],
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against working tree")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Given changed test files, find source files that need verification.

    Reverse bridge: when tests change, which sources should be rechecked?
    """
    from vocab.reports import reverse_verify_report

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


@cli.command(name="verify-classify",  rich_help_panel="Verification")
def verify_classify(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against working tree")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Classify each changed file's verifiability type and structural gaps."""
    from vocab.reports import verify_classify_report
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


@cli.command(name="verify-bonds",  rich_help_panel="Verification")
def verify_bonds(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Detect when a change requires running multiple test files together."""
    from vocab.reports import covalent_verify_bonds
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


@cli.command(name="verify-drift",  rich_help_panel="Verification")
def verify_drift(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    commits: Annotated[int, typer.Option("--commits", "-n", help="Commits to inspect")] = 10,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Track verification confidence across recent commits."""
    from vocab.reports import verification_drift
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


@cli.command(name="test-gaps",  rich_help_panel="Verification")
def deserts(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    top: Annotated[int, typer.Option("--top", "-n", help="Max desert rows")] = 20,
):
    """Test gap map: source files with weak test mirrors.

    This is structural mirror analysis, not coverage proof.
    """
    from vocab.reports import verification_deserts

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

    c = lambda t, col: _color(t, col)
    ratio = data.get("mirror_ratio", 0.0)
    ratio_color = "green" if ratio >= 0.7 else ("yellow" if ratio >= 0.3 else "red")
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VERIFICATION DESERTS", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Source files: {data.get('source_files', 0)}  Test files: {data.get('test_files', 0)}")
    typer.echo(f"  Structural mirror ratio: {c(f'{ratio:.0%}', ratio_color)}")
    typer.echo(f"  Confidence: {c(data.get('confidence', '?'), 'gray')}")
    typer.echo("")
    for item in data.get("deserts", [])[:top]:
        score = item.get("score", 0.0)
        color = "red" if score >= 0.75 else "yellow"
        typer.echo(f"    {c(f'{score:.2f}', color)}  {item['file']}  {c(item.get('reason', ''), 'gray')}")
    if not data.get("deserts"):
        typer.echo(c("    No strong verification deserts found.", "green"))
    typer.echo("")


@cli.command(name="co-change",  rich_help_panel="Inspection")
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
    from vocab.reports import entanglement_matrix
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


@cli.command(name="cascade-verify", rich_help_panel="Agent")
def cascade_verify_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    why: Annotated[bool, typer.Option("--why", help="Show cascade trace")] = False,
):
    """Cascade verifier — hierarchical verification pipeline.

    Tier 1: Cohesion check (0 tokens) — high cohesion = safe to skip LLM.
    Tier 2: Memory B-Cell cache (0 tokens) — same content hash reuses past outcome.
    Tier 3: Deterministic skip (0 tokens) — stem match + cohesion ≥ 0.7.
    Tier 4: Forced-choice binary decision tree (~400-900 tokens).

    On steady state, ~77% of calls hit Tiers 1-3 (0 tokens).
    """
    from vocab.reports import cascade_verify
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


@cli.command(name="veto-cascade", rich_help_panel="Agent")
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
    from vocab.reports import veto_cascade
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


@cli.command(name="isolate", rich_help_panel="Agent")
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
    from vocab.reports import isolate_modules, _active_gene_pool
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
    from vocab.formats.llm import format_isolate_confirm
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


@cli.command(name="fold", rich_help_panel="Agent")
def fold_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to fold")] = "",
    task: Annotated[str, typer.Option("--task", "-t", help="Task description")] = "",
    threshold: Annotated[float, typer.Option("--threshold", help="Minimum score to keep a block")] = 0.02,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Fractional distillation — fold task-irrelevant code blocks.

    Indentation-aware block folding preserves syntax while removing
    structural noise. 40-80% token reduction on large files.

    Example:
      vocab fold --file src/billing.ts --task 'fix proration'
    """
    from vocab.fold import fold_file
    path_abs = os.path.abspath(path)
    data = fold_file(path=path_abs, file_path=file, task=task, threshold=threshold)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    from vocab.formats.llm import format_folded_file
    typer.echo(format_folded_file(data))


@cli.command(name="drift-check", rich_help_panel="CI")
def drift_check_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to snapshot or check")] = "",
    snapshot: Annotated[bool, typer.Option("--snapshot", help="Take initial baseline snapshot")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Structural drift IMU — detect anomaly velocity in codebase.

    Takes vocabulary snapshots per-file. On check, compares current
    state against baseline and alerts on velocity spikes.

    Example:
      vocab drift-check --file src/billing.ts --snapshot
      vocab drift-check --file src/billing.ts
    """
    from vocab.reports import drift_velocity_snapshot
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


@cli.command(name="mycorrhiza", rich_help_panel="Inspection")
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
    from vocab.reports import mycorrhiza_map, mycorrhiza_with_tolerance, _active_gene_pool
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


@cli.command(name="solve", rich_help_panel="Inspection")
def solve_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    top_n: Annotated[int, typer.Option("--top", help="Number of cipher keys to extract")] = 20,
    focus: Annotated[str, typer.Option("--focus", help="Gravitational Lensing: filter cipher keys to those orbiting a specific concept")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    from vocab.reports import solve_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = solve_report(path=path_abs, top_n=top_n, focus=focus)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    if focus:
        typer.echo(f'Lens: {focus} — {len(data.get("bimoth_index",[]))} orbiting keys, {len(data.get("orbiting_files",[]))} files')
    else:
        typer.echo(data["summary"])
    for i, p in enumerate(data.get("bimoth_index", [])[:5]):
        typer.echo(f"  {i+1}. {p['phrase']} (freq={p['frequency']}) — {', '.join(p['top_files'][:2])}")


@cli.command(name="deflate", rich_help_panel="Inspection")
def deflate_cmd(path=".", file="", diff="", budget: int = 5, format="compact"):
    from vocab.reports import deflate_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file or not diff:
        typer.echo("provide --file and --diff", err=True); raise typer.Exit(1)
    data = deflate_report(path=p, file_path=file, proposed_diff=diff, budget=int(budget))
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    used = data.get("net_new_count", 0)
    bud = data.get("budget", 5)
    if data.get("over_budget"):
        typer.echo(f'  {_color("INFLATION DETECTED", "red")}')
        typer.echo(f'  Budget: {bud}, Used: {used}, Over by: {used - bud}')
        typer.echo(f'  Violations: {", ".join(data.get("net_new_identifiers",[])[:bud+3])}')
    else:
        typer.echo(f'  Gold Standard OK — {used}/{bud} net-new identifiers used.')


@cli.command(name="forecast", rich_help_panel="CI")


@cli.command(name="forecast", rich_help_panel="CI")
def forecast_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s) to forecast risk for")] = [],
    commits: Annotated[int, typer.Option("--commits", help="Git history lookback")] = 500,
    active_days: Annotated[int, typer.Option("--active-days", help="Only analyze files modified in N days (active gene pool)")] = 0,
    seismic: Annotated[bool, typer.Option("--seismic", help="S-Wave mode: exclude P-wave files, isolate latent regression risks")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Doppler Defect Radar — forecast regression risk from structural shifts.

    Scans git history for bugfix commits. For each file changed,
    emits historically bug-prone neighbors with regression probability.
    Zero token cost. All computation from git history.

    Example:
      vocab forecast --files src/billing.ts
    """
    from vocab.reports import forecast_report, _active_gene_pool
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


@cli.command(name="triangulate", rich_help_panel="Agent")
def triangulate_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    task: Annotated[str, typer.Option("--task", "-t", help="Task description")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Byzantine Triangulation — intersect 3 structural probes for target anchor.

    Runs 3 structural views (repo-map, recent diffs, distinctive identifiers)
    without reading source code. Collects 5 phrases per view. Computes
    overlap anchor. No source code sent to LLM.

    Example:
      vocab triangulate --task 'fix billing proration'
    """
    from vocab.reports import triangulate_report
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


@cli.command(name="strata", rich_help_panel="Inspection")
def strata_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to analyze")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Tectonic Fault Lines — carbon-date phrase epochs.

    Maps file content by phrase entry age. Emits fault lines where
    epochs of different ages collide — the most brittle code boundaries.

    Example:
      vocab strata --file src/billing.ts
    """
    from vocab.reports import strata_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True)
        raise typer.Exit(1)
    data = strata_report(path=path_abs, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return
    labels = data.get("epoch_labels", [])
    for e in data.get("epochs", []):
        bucket = e.get("epoch_bucket", 0)
        label = labels[bucket] if bucket < len(labels) else f"epoch_{bucket}"
        typer.echo(f"  Lines {e['start']}-{e['end']}: {label} ({e['age_weeks']}w, {e['lines']} lines)")
    for fl in data.get("fault_lines", [])[:3]:
        gap = fl.get("gap", 0)
        typer.echo(f"  FAULT: lines {fl['older_epoch_start']}/{fl['newer_epoch_start']} gap={gap}w  [{_color('brittle', 'red') if gap > 10 else 'ok'}]")
    if not data.get("epochs"):
        typer.echo(f"  {data.get('note', 'no datable content')}")


@cli.command(name="epidemiology", rich_help_panel="Inspection")
def epidemiology_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", help="History lookback")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Viral R0 Contact Tracing — track phrase spread and displacement.

    Computes R0 for each phrase. Classifies as antigen (displacing debt),
    pathogen (spreading without displacement), or endemic (stable).

    Example:
      vocab epidemiology --weeks 12
    """
    from vocab.reports import epidemiology_report
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


@cli.command(name="orient", rich_help_panel="Agent")
def orient_cmd(path=".", task="", format="compact"):
    from vocab.reports import pipeline_orient
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not task:
        typer.echo("provide --task", err=True); raise typer.Exit(1)
    data = pipeline_orient(path=p, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(f"Cipher keys: {', '.join(data.get('cipher_keys', [])[:5])}")
    typer.echo(f"Anchor: {', '.join(data.get('anchor', []))}")
    for m in data.get("recommended_modules", [])[:2]:
        typer.echo(f'  Module: {", ".join(m.get("exemplars",[])[:3])} ({m.get("match_score",0):.0%})')
    typer.echo(f'  {data.get("total_files_in_scope",0)} files in scope')


@cli.command(name="health", rich_help_panel="CI")
def health_cmd(path=".",
               balance: Annotated[bool, typer.Option("--balance", help="Phototropism: check root-to-shoot vocabulary ratio")] = False,
               format="compact"):
    from vocab.reports import structural_health_score
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = structural_health_score(path=p, balance=balance)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    h = data.get("health", "?")
    c = "green" if h == "good" else ("yellow" if h == "moderate" else "red")
    typer.echo(f"Health: {_color(h.upper(), c)} (debt: {data.get('debt_acceleration',0):.3f})")
    if balance and data.get("root_shoot_ratio"):
        ratio = data["root_shoot_ratio"]
        clr = "red" if ratio > 3 else ("green" if ratio < 0.5 else "yellow")
        typer.echo(f"  Root/Shoot ratio: {ratio}:1 [{_color('Features outgrowing core' if ratio > 3 else 'Core dominates' if ratio < 0.5 else 'Balanced', clr)}]")
    elif balance:
        typer.echo(f"  {data.get('phototropism_note', '')}")


@cli.command(name="pulsar", rich_help_panel="Inspection")
def pulsar_cmd(path=".", file="", format="compact"):
    from vocab.reports import pulsar_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True); raise typer.Exit(1)
    data = pulsar_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    anchors = data.get("pulsar_anchors", [])
    missing = data.get("missing_anchors", [])
    typer.echo(f'{data.get("file","")}: {len(anchors)} pulsar anchors, {data.get("total_tokens",0)} tokens')
    if missing:
        typer.echo(f'  {_color("CLOCK DRIFT ANOMALY", "red")}')
        for m in missing[:3]:
            typer.echo(f'    Missing anchor: {m}')
        typer.echo(f'  Mandate: {data.get("mandate","")}')
    else:
        typer.echo(f'  Pulsar rhythm stable.')


@cli.command(name="heisenberg", rich_help_panel="Inspection")
def heisenberg_cmd(path=".", file="", diff="", format="compact"):
    from vocab.reports import heisenberg_check
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file or not diff:
        typer.echo("provide --file and --diff", err=True); raise typer.Exit(1)
    data = heisenberg_check(path=p, file_path=file, proposed_diff=diff)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    if data.get("uncertainty_violated"):
        typer.echo(f'  {_color("HEISENBERG VIOLATION", "red")}')
        typer.echo(f'  New signal: {", ".join(data.get("new_signal_tokens", [])[:3])}')
        typer.echo(f'  Deleted anchors: {", ".join(data.get("deleted_anchors", [])[:3])}')
        typer.echo(f'  {data.get("mandate","")}')
    else:
        typer.echo(f'  Heisenberg principle respected.')


@cli.command(name="traffic-control", rich_help_panel="Inspection")
def traffic_control_cmd(path=".", file="", intended_import="", format="compact"):
    from vocab.reports import traffic_control_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file or not intended_import:
        typer.echo("provide --file and --intended-import", err=True); raise typer.Exit(1)
    data = traffic_control_report(path=p, file_path=file, intended_import=intended_import)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    src = data.get("source_zone", "?")
    dst = data.get("import_zone", "?")
    if data.get("zoning_violation"):
        typer.echo(f'  {_color("ZONING VIOLATION", "red")}')
        typer.echo(f'  {data.get("source_file","")} ({src}) -> {data.get("intended_import","")} ({dst})')
        typer.echo(f'  {data.get("mandate","")}')
    else:
        typer.echo(f'  Import route clear. ({src} -> {dst})')


@cli.command(name="splice-exons", rich_help_panel="Inspection")
def splice_exons_cmd(path=".", file="", format="compact"):
    from vocab.reports import splice_exons_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True); raise typer.Exit(1)
    data = splice_exons_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    pct = data.get("compression_pct", 0)
    typer.echo(f'{data.get("file","")}: {pct}% reduction ({data.get("exon_count",0)} exons from {data.get("original_lines",0)} lines)')
    for e in data.get("exons", [])[:5]:
        typer.echo(f'  L{e["line"]}: {e["text"]} [{e["type"]}]')


@cli.command(name="project-hologram", rich_help_panel="Inspection")
def hologram_cmd(path=".", dir="", format="compact"):
    from vocab.reports import hologram_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not dir:
        typer.echo("provide --dir", err=True); raise typer.Exit(1)
    data = hologram_report(path=p, directory=dir)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(data.get("hologram", ""))
    typer.echo(f'  Imports: {", ".join(data.get("imports",[])[:5])}')
    typer.echo(f'  Exports: {", ".join(data.get("exports",[])[:5])}')
    typer.echo(f'  Hidden: {data.get("hidden_summary","")}')


@cli.command(name="shard-context", rich_help_panel="Inspection")
def shard_context_cmd(path=".", files="", task="", shards: int = 3, format="compact"):
    from vocab.reports import shard_context_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not files or not task:
        typer.echo("provide --files and --task", err=True); raise typer.Exit(1)
    file_list = [f.strip() for f in files.split(",") if f.strip()]
    data = shard_context_report(path=p, files=file_list, task=task, shard_count=shards)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(f'{len(file_list)} files -> {data.get("shard_count",0)} shards')
    for s in data.get("shards", []):
        typer.echo(f'  Shard {s["shard_index"]}: {", ".join(s["files"])}')
        for b in s.get("boundary_hologram", [])[:2]:
            typer.echo(f'    hologram: {b}')
    typer.echo(f'  Workflow: {data.get("shard_workflow","")}')


@cli.command(name="sentinel", rich_help_panel="Agent")
def sentinel_cmd(path=".", task="", format="compact"):
    from vocab.reports import sentinel_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not task:
        typer.echo("provide --task", err=True); raise typer.Exit(1)
    data = sentinel_report(path=p, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(f'Sentinels: {", ".join(data.get("sentinels",[]))}')
    typer.echo(f'  {data.get("detection","")}')


@cli.command(name="dark-matter", rich_help_panel="CI")
def dark_matter_cmd(repo_a="", repo_b="", format="compact"):
    from vocab.reports import dark_matter_report
    if not repo_a or not repo_b:
        typer.echo("provide --repo-a and --repo-b", err=True); raise typer.Exit(1)
    data = dark_matter_report(repo_a=repo_a, repo_b=repo_b)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(f'Dark matter: {data.get("dark_matter_count",0)} orphan phrases in A that bind to B')
    for b in data.get("bindings", [])[:5]:
        typer.echo(f'  {b["phrase"]}: {b.get("a_file","")} -> {b["b_file_count"]} files in B')


@cli.command(name="supernova", rich_help_panel="Inspection")
def supernova_cmd(path=".", threshold: float = 0.90, format="compact"):
    from vocab.reports import supernova_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = supernova_report(path=p, overlap_threshold=threshold)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(data.get("summary", ""))
    for r in data.get("condensates", [])[:3]:
        typer.echo(f'  {r["trend"]}: {r["action"]} ({", ".join(r["files"])})')


@cli.command(name="chrono-lock", rich_help_panel="Inspection")
def chrono_lock_cmd(path=".", file="", diff="", max_gap: int = 2, format="compact"):
    from vocab.reports import chrono_lock_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file or not diff:
        typer.echo("provide --file and --diff", err=True); raise typer.Exit(1)
    data = chrono_lock_report(path=p, file_path=file, proposed_diff=diff, max_age_gap=max_gap)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    if data.get("chrono_anomaly"):
        typer.echo(f'  {_color("TEMPORAL VIOLATION", "red")}')
        typer.echo(f'  Center of mass: {data.get("center_of_mass_year","?")}')
        typer.echo(f'  Diff introduces: {data.get("max_diff_year","?")}')
        typer.echo(f'  {data.get("mandate","")}')
    else:
        typer.echo(f'  Chrono-lock OK (center of mass: {data.get("center_of_mass_year","?")})')


@cli.command(name="necrotic", rich_help_panel="Inspection")
def necrotic_cmd(path=".", file="", format="compact"):
    from vocab.reports import necrotic_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True); raise typer.Exit(1)
    data = necrotic_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    if data.get("necrotic"):
        typer.echo(f'  {_color("NECROTIC TISSUE", "red")}')
        typer.echo(f'  Blast radius: {data.get("reverse_blast_radius",0)}')
        typer.echo(f'  Orphans: {len(data.get("orphan_phrases",[]))}')
        typer.echo(f'  Lifecycle: {data.get("lifecycle_state","")}')
        typer.echo(f'  {data.get("mandate","")}')
    else:
        typer.echo(f'  File healthy (blast radius: {data.get("reverse_blast_radius",0)})')


@cli.command(name="metamorphic", rich_help_panel="CI")
def metamorphic_cmd(from_repo="", to_repo="", ref="HEAD~1", format="compact"):
    from vocab.reports import metamorphic_mask_report
    if not from_repo or not to_repo:
        typer.echo("provide --from-repo and --to-repo", err=True); raise typer.Exit(1)
    data = metamorphic_mask_report(source_path=from_repo, target_path=to_repo, source_ref=ref)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(f'Mask: {data.get("mask_count",0)} phrase transformations')
    for m in data.get("mask", [])[:5]:
        typer.echo(f'  {m["from"]} -> {m["to"]}')
    typer.echo(f'Craters: {len(data.get("craters",[]))} impacted files')
    for c in data.get("craters", [])[:3]:
        typer.echo(f'  {c["file"]} ({c["coupling_label"]}, {c["impact_count"]} hits)')
    typer.echo(f'  {data.get("migration_order","")}')

    typer.echo(f'Nucleation: {", ".join(sites)}')

@cli.command(name="capillary", rich_help_panel="Inspection")
def capillary_cmd(path=".", format="compact"):
    from vocab.reports import capillary_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = capillary_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    for c in data.get("capillaries", [])[:3]:
        typer.echo(f'  {c["file"]} ({c["edges"]} edges)')

@cli.command(name="spectral-gap", rich_help_panel="Utilities")
def spectral_gap_cmd(path=".", format="compact"):
    from vocab.reports import spectral_gap_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = spectral_gap_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    g = data.get("spectral_gap", 0)
    m = data.get("modularity", "?")
    typer.echo(f'Gap: {g} ({m})')

@cli.command(name="phantom", rich_help_panel="Inspection")
def phantom_cmd(path=".", format="compact"):
    from vocab.reports import phantom_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    data = phantom_report(path=p)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    d = data.get("frameworks_detected", {})
    if d:
        typer.echo(f'  {" ".join(f"{k}({v})" for k,v in sorted(d.items(), key=lambda x:-x[1])[:5])}')
    else:
        typer.echo('  none detected')


@cli.command(name="parity-bit", rich_help_panel="CI")
def parity_bit_cmd(path=".", ref_a="", ref_b="", format="compact"):
    from vocab.reports import parity_bit_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not ref_a or not ref_b:
        typer.echo("provide --ref-a and --ref-b", err=True); raise typer.Exit(1)
    data = parity_bit_report(path=p, ref_a=ref_a, ref_b=ref_b)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    u = data.get("mirror_unchanged", False)
    typer.echo(f'Mirror {"UNCHANGED" if u else "CHANGED"}')


@cli.command(name="catalytic-crack", rich_help_panel="Utilities")
def catalytic_crack_cmd(path=".", file="", format="compact"):
    from vocab.reports import catalytic_crack_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True); raise typer.Exit(1)
    data = catalytic_crack_report(path=p, file_path=file)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    typer.echo(f'{data.get("file","")}: {data.get("fragments_count",0)} fragments from {data.get("total_lines",0)} lines')
    for f in data.get("fragments", [])[:3]:
        typer.echo(f'  Fragment {f["fragment_index"]}: {f["lines"]} lines -> {f["output_file"]}')
        typer.echo(f'    Phrases: {", ".join(f["cluster_phrases"][:3])}')
    for fv in data.get("fragment_vocabularies", [])[:3]:
        for name, phrases in fv.items():
            typer.echo(f'  LLM name: {name} = [{", ".join(phrases)}]')


@cli.command(name="decay", rich_help_panel="Inspection")
def decay_cmd(path=".", file="", weeks=12, half_life=30,
              metabolism: Annotated[bool, typer.Option("--metabolism", help="Active Metabolism: verify pattern declining repo-wide")] = False,
              format="compact"):
    from vocab.reports import decay_report
    p = os.path.abspath(path)
    if not vgit.is_repo(p):
        typer.echo("Not a git repository.", err=True); raise typer.Exit(1)
    if not file:
        typer.echo("provide --file", err=True); raise typer.Exit(1)
    data = decay_report(path=p, file_path=file, lookback_weeks=weeks, half_life_days=half_life, active_metabolism=metabolism)
    if "error" in data:
        typer.echo(data["error"], err=True); raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2)); return
    dp = data.get("decaying_patterns", [])
    if dp:
        typer.echo(f'{_color("TOXICITY CLEARANCE REQUIRED", "red")}')
        for d in dp[:5]:
            typer.echo(f'  {d["phrase"]} -> {d["replacement"]}')
        typer.echo(f'  Mandate: {data.get("mandate","")}')
    else:
        typer.echo(f'{data.get("file","")}: clean — no decaying patterns')


@cli.command(name="entropy", rich_help_panel="Inspection")
def entropy_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", help="History lookback")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Isothermal Limit — track directory-level structural entropy.

    Measures vocabulary cluster dispersion per directory. When entropy
    exceeds the 30-commit rolling baseline, the limit is tripped.

    Example:
      vocab entropy --weeks 12
    """
    from vocab.reports import isothermal_entropy
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
    if data.get("any_limit_exceeded"):
        typer.echo(_color("ISOTHERMAL LIMIT EXCEEDED", "red"))
    else:
        typer.echo(_color("Entropy within limits", "green"))
    for d in data.get("directories", [])[:8]:
        label = _color("HOT", "red") if d["limit_exceeded"] else _color("COLD", "green")
        typer.echo(f"  [{label}] {d['directory']:30s} entropy={d['entropy']:.2f} baseline={d['baseline']:.2f}")


@cli.command(name="zk-proof", rich_help_panel="Agent")
def zk_proof_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="Schema file (source of truth)")] = "",
    code: Annotated[str, typer.Option("--code", help="LLM-generated code to verify")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Zk-Vocabulary Prover — verify generated code uses only allowed identifiers.

    Extracts identifiers from the schema file. Scans generated code.
    Rejects any identifier not in the allowed set with alternatives.

    Example:
      vocab zk-proof --file db/types.ts --code 'const q = db.query(...)'
    """
    from vocab.reports import zk_proof_report
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
        vc = data.get("violation_count", 0)
        for v in data.get("violations", [])[:5]:
            alts = ", ".join(v.get("allowed_alternatives", [])[:2])
            typer.echo(f"  '{v['identifier']}' not in schema. Did you mean: {alts}?")


@cli.command(name="lagrange", rich_help_panel="Inspection")
def lagrange_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    file: Annotated[str, typer.Option("--file", help="File to analyze")] = "",
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Lagrange Points — detect structurally isolated blocks safe to edit.

    Finds blocks with zero co-occurrence edges to the file's primary
    clusters. Editing these blocks has zero blast radius.

    Example:
      vocab lagrange --file legacy.ts
    """
    from vocab.reports import lagrange_report
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
        typer.echo(_color(f"Lagrange Points: {len(pts)} safe injection sites", "green"))
        for p in pts[:3]:
            typer.echo(f"  Lines {p['start']}-{p['end']} ({p['lines']} lines, {p['identifier_count']} identifiers)")
            typer.echo(f"    {p['code'][:80]}...")
    else:
        note = data.get("note", "none found")
        typer.echo(f"No Lagrange Points: {note}")


@cli.command(name="phase-shift", rich_help_panel="Inspection")
def phase_shift_cmd(
    repo_a: Annotated[str, typer.Option("--repo-a", help="Pre-migration repo path")] = "",
    repo_b: Annotated[str, typer.Option("--repo-b", help="Post-migration repo path")] = "",
    min_freq: Annotated[int, typer.Option("--min-freq", help="Minimum frequency to include")] = 2,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Phase-Vocoder Differential Mask — deterministic migration substitution pairs.

    Scans two repos (pre/post migration). Extracts phrase-level delta.
    Output is a deterministic replacement task: apply these substitutions.

    Example:
      vocab phase-shift --repo-a ./pre-migration --repo-b ./post-migration
    """
    from vocab.reports import phase_shift_report
    if not repo_a or not repo_b:
        typer.echo("provide --repo-a and --repo-b", err=True)
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


@cli.command(name="shrapnel", rich_help_panel="Inspection")
def shrapnel_cmd(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", help="History lookback")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output: compact, json")] = "compact",
):
    """Dead-Water Shrapnel — find phrases stranded by cavitated neighbors.

    Scans git history for phrases that appeared, then disappeared
    (cavitated). Finds remaining phrases uniquely entangled with them.

    Example:
      vocab shrapnel --weeks 12
    """
    from vocab.reports import shrapnel_report
    path_abs = os.path.abspath(path)
    if not vgit.is_repo(path_abs):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)
    data = shrapnel_report(path=path_abs, lookback_weeks=weeks)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
    if format == "json":
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    cav = data.get("cavitated", [])
    shrap = data.get("shrapnel", [])
    typer.echo(f"Cavitated phrases: {len(cav)}  Shrapnel fragments: {len(shrap)}")
    for s in shrap[:5]:
        typer.echo(f"  '{s['cavitated'][:30]}' -> '{s['stranded'][:30]}' in {s['file']}")


@cli.command(name="verify-packet",  rich_help_panel="Agent")
def cartridge(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    files: Annotated[list[str], typer.Option("--files", help="Changed file(s); repeat or comma-separate")] = [],
    diff: Annotated[str | None, typer.Option("--diff", help="Git ref to diff against working tree")] = None,
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    why: Annotated[bool, typer.Option("--why", help="Show why each candidate exists")] = False,
):
    """Verification packet — compressed scope for LLM verification.
    
    Examples:
      vocab verify-packet --files src/spool.ts
      vocab verify-packet --files src/spool.ts --why
      vocab verify-packet --files src/spool.ts --format json
    """
    from vocab.reports import cartridge_report
    data = cartridge_report(path=path, files=files or None, diff_ref=diff, task=task)
    if "error" in data:
        typer.echo(data["error"], err=True)
        raise typer.Exit(1)
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
        from vocab.formats.terminal import _why_verify_packet
        typer.echo(_why_verify_packet(data))


@cli.command(name="check-diff", rich_help_panel="CI")
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
    from vocab.reports import check_diff_report
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


@cli.command(rich_help_panel="Agent")
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
    from vocab.reports import route_recommendation

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

    c = lambda t, col: _color(t, col)
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


@cli.command(rich_help_panel="Inspection")
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


@cli.command(rich_help_panel="Inspection")
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


@cli.command(rich_help_panel="History")
def timeline(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    weeks: Annotated[int, typer.Option("--weeks", "-w", help="Weeks of history")] = 12,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    if not vgit.is_repo(path):
        typer.echo("Not a git repository.", err=True)
        raise typer.Exit(1)

    data = concept_timeline(path, weeks=weeks)
    if not data:
        typer.echo("No timeline data available.")
        return

    c = lambda t, color: _color(t, color)

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


@cli.command(rich_help_panel="History")
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

    c = lambda t, color: _color(t, color)

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


@cli.command(rich_help_panel="Inspection")
def explore(
    path: Annotated[str, typer.Argument(help="Path to codebase")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
    themes: Annotated[bool, typer.Option("--themes", "-t", help="Also detect latent structural themes (slower)")] = False,
):
    """Onboarding map: best files to read first.

    Ranks files by vocabulary coverage — files with highest coverage
    contain the most representative concepts. Start here.

    With --themes, runs deeper analysis to discover conceptual groupings
    across the codebase.
    """
    path = os.path.abspath(path)
    data = explore_repo(path, themes=themes)
    files = data.get("files", [])
    if not files:
        typer.echo("No files found.")
        return

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    c = lambda t, col: _color(t, col)
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
    c = lambda t, col: _color(t, col)

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
    first_arch = None
    if first_task_read:
        for r in reads:
            if r["file"] != first_task_read:
                first_arch = r["file"]
                break
    elif reads:
        first_arch = reads[0]["file"]

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
        typer.echo(c(f"           Understand this file before making changes.", "gray"))
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
        typer.echo(c(f"           Architecture context for the task.", "gray"))

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
        typer.echo(c(f"           All tests must pass after edit.", "gray"))

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
    c = lambda t, col: _color(t, col)
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
    typer.echo(c("  Mode: report-only; do not treat as semantic truth or coverage proof.", "gray"))


def _print_preflight_checklist(data: dict) -> None:
    c = lambda t, col: _color(t, col)
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


@cli.command(name="agent-bootstrap",  rich_help_panel="Agent")
def agent_bootstrap(
    path: Annotated[str, typer.Argument(help="Repository path")] = ".",
    task: Annotated[str | None, typer.Option("--task", "-t", help="Optional task description to find related files")] = None,
    verify_relevance: Annotated[bool, typer.Option("--verify-relevance", help="Verify surfaced files contain task keywords")] = False,
    summary: Annotated[bool, typer.Option("--summary", help="Only show the decision-oriented startup summary")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json, checklist, llm")] = "compact",
):
    """One-shot agent bootstrap: explore + modules + stability + related files.

    Examples:
      vocab agent-bootstrap . --task "fix upload" --summary
      vocab agent-bootstrap . --task "fix upload" --verify-relevance --format json
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
        from vocab.formats.llm import format_bootstrap_llm
        typer.echo(format_bootstrap_llm(data))
        return

    if format == "checklist":
        _print_agent_checklist(data, task)
        return

    if verify_relevance and "task_relevance_score" in data:
        score = data["task_relevance_score"]
        label, color, reason = _relevance_label(score)
        typer.echo(_color(f"  Task relevance: {label} ({score:.0%}) - {reason}", color), err=True)

    c = lambda t, col: _color(t, col)
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
            typer.echo(f"           {c(bc[1]['concept'], 'yellow')} ({bc[1]['file_count']} files){c(f' — {bc[1]["files"][0]}', 'gray')}")
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
            typer.echo(f"    {c(b['concept'], 'yellow'):<30} {c(f'{b["file_count"]:>4} files', 'cyan')}  {c(files_str, 'gray')}")
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
            files_preview = ", ".join(f.split("/")[-1] for f in m["files"][:3])
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


@cli.command(rich_help_panel="Agent")
def skeleton(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Prompt decompression: emit only the ~100-token skeleton for LLM system prompts.

    Skip directives tell the LLM which files to ignore (generated, vendor) and
    which test conventions to expect. Meant to REDUCE prompt noise.
    """
    from vocab.reports import crystallography as _crystallography

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

    c = lambda t, col: _color(t, col)
    typer.echo(data.get("skeleton", ""))
    if data.get("generated_pct", 0) > 5:
        typer.echo(c(f"\n  Skip: {data['generated_pct']}% generated files — do not edit without confirmation.", "gray"))
    if data.get("test_convention", "unknown") != "unknown":
        typer.echo(c(f"  Skip: tests follow {data['test_convention']} convention — already covered.", "gray"))


@cli.command(rich_help_panel="History")
def delta(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Dead reckoning: show structural changes since last vocab init scan.

    Requires a cached scan from `vocab init` or `vocab repo-map --save`.
    """
    from vocab.reports import repo_delta

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

    c = lambda t, col: _color(t, col)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(c("  VOCAB DELTA", "header"))
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  Files: {c(str(data.get('old_files', 0)), 'gray')} → {c(str(data.get('new_files', 0)), 'cyan')} ({c(f'{data.get('file_delta', 0):+d}', 'green')})")
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


@cli.command(name="ci-report",  rich_help_panel="CI")
def ci_report_cmd(
    ref_a: Annotated[str, typer.Argument(help="Base git ref (e.g. origin/main)")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref (e.g. HEAD)")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    fail_mirror_gap: Annotated[float | None, typer.Option("--fail-on-mirror-gap", help="Fail if mirror_gap_ratio < threshold")] = None,
    fail_blast_tier: Annotated[str | None, typer.Option("--fail-on-blast-tier", help="Fail if max_blast_tier >= tier (local/moderate/high/critical)")] = None,
    fail_stable_touched: Annotated[bool, typer.Option("--fail-on-stable-touched", help="Fail if any stable anchors touched")] = False,
    summary: Annotated[bool, typer.Option("--summary", help="Only show pass/fail, reason, and core metrics")] = False,
    why: Annotated[bool, typer.Option("--why", help="Show why each result")] = False,
):
    """CI-ready structural report: blast radius + stable file check + flags.

    Analyzes the structural impact of a change set without blocking.
    Designed for CI pipelines that want a summary, not a gate.

    Examples:
      vocab ci-report origin/main HEAD --summary
      vocab ci-report origin/main HEAD --fail-on-mirror-gap 0.70
      vocab ci-report origin/main HEAD --fail-on-blast-tier high
      vocab ci-report origin/main HEAD --fail-on-stable-touched
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
            data, fail_mirror_gap, fail_blast_tier, fail_stable_touched
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        if gate_failures:
            raise typer.Exit(gate_failures[0][0])
        return

    c = lambda t, col: _color(t, col)
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
        f"  {c('Metrics:', 'subheader')} mirror {data.get('mirror_gap_ratio', 0.0):.0%}, "
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
    typer.echo(f"    Mirror gap: {c(f'{data.get('mirror_gap_ratio', 0.0):.0%}', 'cyan')}")
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
        from vocab.formats.terminal import _why_ci_report
        typer.echo(_why_ci_report(data, ref_a, ref_b))


@cli.command(rich_help_panel="Inspection")
def inspect(
    path: Annotated[str, typer.Argument(help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
    anomalies: Annotated[bool, typer.Option("--anomalies", help="Load cached scan and show deltas")] = False,
    why: Annotated[bool, typer.Option("--why", help="Show why each section matters")] = False,
):
    """Comprehensive codebase overview: explore + modules + timeline + health.

    Single command that tells you what matters about a codebase:
    top files, module boundaries, structural themes, stability, churn, health score.
    """
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
        from vocab.reports import detect_anomalies
        data["anomalies"] = detect_anomalies(path)

    if format == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    c = lambda t, col: _color(t, col)
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
            files_preview = ", ".join(f.split("/")[-1] for f in m["files"][:3])
            typer.echo(f"    {bar} {m['size']} files  thr {pr[0]}→{pr[1]}  {c(files_preview, 'gray')}")
        if module_count > 5:
            typer.echo(c(f"    … +{module_count - 5} more modules", "gray"))
        typer.echo("")

    debt = data.get("debt_candidates", [])
    if debt:
        typer.echo(c("  DEBT CANDIDATES (low uniqueness + churn potential):", "subheader"))
        for d in debt[:8]:
            bar = _bar(d["debt"] * 100, 10)
            typer.echo(f"    {bar} {c(f'{d['debt']:.2f}', 'red')}  {c(d['language'], 'gray'):<8} {d['file']}")
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
        from vocab.formats.terminal import _why_inspect
        typer.echo(_why_inspect(data))


@cli.command(rich_help_panel="Inspection")
def modules(
    path: Annotated[str, typer.Argument(help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: terminal, json")] = "terminal",
):
    """Detect parser-free module boundaries from rare identifier overlap."""
    data = compute_modules(os.path.abspath(path))
    if format == "json":
        typer.echo(format_modules_json(data))
    else:
        typer.echo(format_modules(data))


@cli.command(name="help-agent",  rich_help_panel="Utilities")
def help_agent(task: Annotated[str, typer.Argument(help="Engineering task description")]):
    """Recommend useful vocab commands for an agent task."""
    task_lower = task.lower()
    commands: list[tuple[str, str, bool]] = []

    # Primary agent surface — proven by harness
    commands.append(("vocab edit-context --path . --files <file> --task \"<task>\" --format tool",
                     "Verify candidates and stay in scope for a candidate edit file.", True))
    commands.append(("vocab contract --path . --files <file> --task \"<task>\" --format tool",
                     "Bounded ID-coded scope contract (experimental).", True))
    commands.append(("vocab check-plan --contract <contract.json> --proposal <proposal.json>",
                     "Validate LLM proposal against contract (experimental).", True))

    # Task-specific secondary
    if any(word in task_lower for word in ("pr", "review", "change", "refactor", "edit", "feature", "fix")):
        commands.append(("vocab edit-context --path . --diff HEAD~1 --task \"<task>\" --format tool",
                         "Diff-scoped edit-context for PR review (100% verify in testing).", True))
        commands.append(("vocab ci-report origin/main HEAD --format json",
                         "Check structural impact before PR (human/CI tool).", False))

    # Orientation
    commands.append(("vocab repo-map --path . --format json",
                     "Compact repo skeleton for initial orientation (not per-task).", False))
    commands.append(("vocab agent-bootstrap . --task \"<task>\" --format checklist",
                     "Weak-model orientation: step-by-step protocol (not for strong models).", True))

    # Deep investigations
    if any(word in task_lower for word in ("history", "why", "when", "provenance", "timeline")):
        commands.append(("vocab provenance <phrase> --format json",
                         "Trace when a concept appeared or disappeared.", True))
        commands.append(("vocab stable . --format json",
                         "Surface files and phrases that persist across git history.", False))
    if any(word in task_lower for word in ("contract", "integration", "cross", "drift", "compare")):
        commands.append(("vocab compare <repo-a> <repo-b> --format json",
                         "Cross-repo vocabulary alignment and drift asymmetry.", True))

    # Unmeasured agent commands — harness-validated behavior unknown
    if any(word in task_lower for word in ("negotiate", "scope")):
        commands.append(("vocab negotiate --path . --files <file> --task \"<task>\" --format json",
                         "[UNMEASURED] Multi-turn scope containment protocol.", True))
    if any(word in task_lower for word in ("verify", "test", "check")):
        commands.append(("vocab verify --path . --files <file> --task \"<task>\"",
                         "[UNMEASURED] Multiple-choice verification candidates.", True))
    if any(word in task_lower for word in ("route", "decide", "whether")):
        commands.append(("vocab route --path . --task \"<task>\" --format json",
                         "[UNMEASURED] Routing logic that decides when to use vocab.", True))

    # Discoverability
    commands.append(("vocab explore . --format json --quick",
                     "Quick onboarding: most distinctive source files.", False))
    commands.append(("vocab help-agent \"<task>\"",
                     "This command — show recommended commands for any task.", True))

    typer.echo(json.dumps({
        "schema_version": 1,
        "task": task,
        "commands": [
            {"cmd": cmd, "why": why, "requires_user_value": requires_value}
            for cmd, why, requires_value in commands
        ],
    }, indent=2))


@cli.command(rich_help_panel="Cross-Repo")
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

    c = lambda t, color: _color(t, color)
    typer.echo(c(f"{'━' * 60}", "cyan"))
    typer.echo(f"  {c('VOCABULARY ALIGNMENT', 'header')}: {result['repo_a']} <-> {result['repo_b']}")
    if result.get("contract_only"):
        typer.echo(c(f"  (contract surface only — api/, client/, types)", "gray"))
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


@cli.command(rich_help_panel="History")
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


@cli.command(name="fingerprint",  rich_help_panel="Utilities")
def fingerprint_cmd(target: Annotated[str, typer.Argument(help="File or repo path")]):
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
    vocab = build_vocabulary(seg_result.phrases, seg_result.strategy, seg_result.delimiter)
    phrase_to_idx = {e.text: e.index for e in vocab.entries}
    index_list = [phrase_to_idx[p] for p in seg_result.phrases if p in phrase_to_idx]
    typer.echo(f"Fingerprint: v0-{index_sequence_hash(index_list)}")
    typer.echo(f"Phrases: {vocab.size} unique / {len(seg_result.phrases)} total")


@cli.command(rich_help_panel="Inspection")
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


@cli.command(name="pr-report",  rich_help_panel="CI")
def pr_report(
    ref_a: Annotated[str, typer.Argument(help="Base git ref")],
    ref_b: Annotated[str, typer.Argument(help="Target git ref")],
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
):
    """Generate a consolidated PR structural report (markdown)."""
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
    from vocab.reports import refactoring_patterns
    pattern_data = refactoring_patterns(path, base_ref=ref_a, head_ref=ref_b)
    typer.echo(format_pr_report_markdown(pr_files, blast_results, [], ref_a, ref_b, pattern_data=pattern_data))


@cli.command(rich_help_panel="Utilities")
def init(
    path: Annotated[str, typer.Argument(help="Path to repo")] = ".",
    seed: Annotated[bool, typer.Option("--seed", "--no-seed", help="Seed fragment router from git history")] = True,
):
    """Generate a .vocab.yml config file and cache repo-map scan.

    By default also seeds the fragment router using up to 20 historical
    commits so the adaptive router has accuracy data before the first
    LLM task runs. Use --no-seed to skip.

    Speed: seeding scans up to 2500 files. On large repos (2400+ files)
    may take 10-30s additional.
    """
    target = os.path.join(os.path.abspath(path), ".vocab.yml")
    if not os.path.exists(target):
        os.makedirs(os.path.abspath(path), exist_ok=True)
        content = """# vocab CI configuration
# Structural checks for CI pipelines.

blast:
  max_impacted: 20
  critical_paths: []

lifecycle:
  min_signal_weeks: 4

search:
  common_threshold: 0.8
"""
        with open(target, "w") as f:
            f.write(content)
        typer.echo(f"Created {target}")

    from vocab.reports import crystallography, _save_cached
    path_abs = os.path.abspath(path)
    if vgit.is_repo(path_abs):
        data = crystallography(path_abs)
        if "error" not in data:
            _save_cached(path_abs, data)
            typer.echo("Cached repo-map scan for delta tracking.")
        if seed:
            from vocab.reports import seed_fragment_matrix
            typer.echo("Seeding fragment router from recent commits... ", nl=False)
            seed_data = seed_fragment_matrix(path_abs, max_commits=20)
            seeded = seed_data.get("seeded_trials", 0)
            typer.echo(_color(f"done ({seeded} historical trials seeded).", "green"))
    else:
        typer.echo("Not a git repository; skipping cache.")


def main():
    if len(sys.argv) == 1:
        typer.echo("vocab — grammar-free structural codebase analyzer")
        typer.echo("")
        typer.echo("Start here:")
        typer.echo("  vocab agent-bootstrap . --task \"fix upload\" --summary")
        typer.echo("  vocab inspect .")
        typer.echo("  vocab help-agent \"change API client\"")
        typer.echo("")
        typer.echo("CI / PR:")
        typer.echo("  vocab ci-report origin/main HEAD --summary")
        typer.echo("  vocab ci-report origin/main HEAD --fail-on-blast-tier high")
        typer.echo("  vocab blast origin/main HEAD")
        typer.echo("  vocab pr-report origin/main HEAD")
        typer.echo("")
        typer.echo("History / structure:")
        typer.echo("  vocab repo-map .               one-time repo summary (LLM)")
        typer.echo("  vocab vocabulary-trend --path .                vocabulary diversity over history")
        typer.echo("  vocab patterns --path .               refactoring pattern hints")
        typer.echo("  vocab anomalies --path .                structural defect summary")
        typer.echo("  vocab stable .")
        typer.echo("  vocab timeline . --format json")
        typer.echo("  vocab provenance SpoolManager . --format json")
        typer.echo("  vocab modules .")
        typer.echo("  vocab fingerprint .")
        typer.echo("")
        typer.echo("Cross-repo / search:")
        typer.echo("  vocab search SpoolManager ../repo-a ../repo-b")
        typer.echo("  vocab compare ../repo-a ../repo-b --format json")
        typer.echo("")
        typer.echo("Other commands: analyze, diff, lifecycle, explore, clone, landmarks, orphans, init, coupling, origins, stop")
        typer.echo("Tip: most agent-facing commands support --format json.")
        return
    cli()


@cli.command(name="anomalies",  rich_help_panel="Inspection")
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
    from vocab.reports import lattice_defects

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

    c = lambda t, col: _color(t, col)
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


@cli.command(rich_help_panel="Inspection")
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
    from vocab.reports import refactoring_patterns

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

    c = lambda t, col: _color(t, col)
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


@cli.command(rich_help_panel="Utilities")
def stop(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    read: Annotated[list[str] | None, typer.Option("--read", help="Files already read; repeat")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Agent exploration: should you keep reading?

    Tracks concept coverage as you read files and signals
    when further exploration has diminishing returns.
    """
    from vocab.reports import exploration_entropy

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

    c = lambda t, col: _color(t, col)
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


@cli.command(name="vocabulary-trend",  rich_help_panel="Inspection")
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
    from vocab.reports import entropy_velocity

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

    c = lambda t, col: _color(t, col)
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


@cli.command(name="origins",  rich_help_panel="History")
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
    from vocab.reports import concept_genesis

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

    c = lambda t, col: _color(t, col)
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


@cli.command(name="coupling",  rich_help_panel="Cross-Repo")
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
    from vocab.reports import concept_bonds

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

    c = lambda t, col: _color(t, col)
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


@cli.command(name="diff-structural",  rich_help_panel="Inspection")
def diff_structural(
    path: Annotated[str, typer.Argument(help="Repository path")] = ".",
    ref_a: Annotated[str | None, typer.Option("--before", help="Base ref (default: HEAD~1)")] = None,
    ref_b: Annotated[str | None, typer.Option("--after", help="Head ref (default: HEAD)")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Structural fingerprint diff between two git refs.

    Compares repo fingerprints, detects lattice defects,
    measures diversity acceleration, and lists changed files.
    All from grammar-free structural signals.
    """
    from vocab.reports import structural_diff

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

    c = lambda t, col: _color(t, col)
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


@cli.command(rich_help_panel="Utilities")
def ask(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    question: Annotated[str, typer.Argument(help="Question about the repo")] = "",
    files: Annotated[list[str] | None, typer.Option("--files", help="Scoped file(s); repeat or comma-separate")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Answer natural-language questions about a repo using existing structural data.

    Examples:
      vocab ask "Is src/spool.ts safe to edit?"
      vocab ask "What verifies changes to cli.py?"
      vocab ask "What files share concepts with ingest.go?"
      vocab ask "Is this repo healthy?"
      vocab ask "Does this repo have tests?"
    """
    from vocab.reports import answer_question

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

    c = lambda t, col: _color(t, col)
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
    cap = "Vocab sees structure, not semantics. Answers are structural hints only."
    typer.echo(c(f"  {cap}", "gray"))


@cli.command(name="verify-scope",  rich_help_panel="Agent")
def verify_scope(
    path: Annotated[str, typer.Argument(help="Repository path")] = ".",
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
    from vocab.reports import verify_scope as _verify_scope

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

    c = lambda t, col: _color(t, col)
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
    typer.echo(c("  Mode: report-only receipt; identifies scope changes, not correctness.", "gray"))


@cli.command(rich_help_panel="Utilities")
def calibration(
    path: Annotated[str, typer.Option("--path", "-p", help="Path to repo")] = ".",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: compact, json")] = "compact",
):
    """Show vocab's accuracy on this repo from past verify-scope runs.

    Tracks verification hit rate and scope accuracy over time.
    Requires verify-scope to have been run at least 3 times on this repo.
    """
    from vocab.reports import compute_calibration

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

    c = lambda t, col: _color(t, col)
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


if __name__ == "__main__":
    main()
