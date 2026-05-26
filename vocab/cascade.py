"""Token cascade analysis — R₀ (basic reproduction number) for code tokens.

Tracks where tokens are born (first introduced in a commit) and how many
downstream files they infect. R₀ > 1.0 means the token is actively spreading
— an epidemic signal for architectural damage.

Detection: diff consecutive commit identifier vocabularies. A token is born
in commit N if it appears in N's identifier set but not in N-1's. Infection
is when that token appears in a new file in a subsequent commit.
"""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from vocab.scanner import scan_codebase, _extract_identifiers, _code_file_vocabs


@dataclass
class InfectionSite:
    file: str = ""
    commit: str = ""
    generation: int = 0


@dataclass
class CascadeEvent:
    token: str
    birth_commit: str
    birth_file: str
    birth_timestamp: float = 0
    infected: list[InfectionSite] = field(default_factory=list)
    r0: float = 0.0
    confidence: float = 0.0


def _scan_commit(repo: str, commit_hash: str) -> dict[str, set[str]] | None:
    """Checkout commit, scan, return {file: {identifiers}}."""
    r = subprocess.run(["git", "checkout", "--quiet", commit_hash],
                       capture_output=True, cwd=repo)
    if r.returncode != 0:
        return None
    # Clear scan cache so each commit is scanned fresh from checked-out files
    from vocab.scanner import _SCAN_CACHE
    _SCAN_CACHE.clear()
    from vocab.scanner import scan_codebase, _extract_identifiers, _code_file_vocabs
    try:
        analysis = scan_codebase(repo, quiet=True)
    except Exception:
        return None
    result: dict[str, set[str]] = {}
    for fv in _code_file_vocabs(analysis):
        result[fv.path] = _extract_identifiers(fv)
    return result


def scan_cascade(repo: str, since: str = "HEAD~20",
                 window: int = 5, threshold: float = 1.0,
                 quiet: bool = False) -> list[CascadeEvent]:
    """Scan git history for token births and compute R₀ per token.
    
    Diffs identifier sets between consecutive commits. Tokens new in commit N
    (present in N, absent in N-1) are births. Tokens spreading to new files
    in subsequent commits are infections.
    
    Args:
        repo: Path to git repository.
        since: Git refspec for start of window (e.g. HEAD~20).
        window: Generations after birth to track infection.
        threshold: Minimum R₀ to include in results.
        quiet: Suppress progress stderr.
    
    Returns:
        List of CascadeEvents with R₀ above threshold, sorted by R₀ descending.
    """
    if not quiet:
        print(f"  Rho: scanning {since} window={window}", file=sys.stderr)

    # Get chronological commit list
    log = subprocess.run(
        ["git", "log", "--reverse", "--format=%H %ct", since],
        capture_output=True, text=True, cwd=repo,
    )
    if log.returncode != 0:
        sys.stderr.write(f"git log failed: {log.stderr}\n")
        return []

    commits: list[tuple[str, float]] = []
    for line in log.stdout.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            commits.append((parts[0], float(parts[1])))

    if len(commits) < 2:
        if not quiet:
            print("  Fewer than 2 commits; cannot compute cascade.", file=sys.stderr)
        return []

    if not quiet:
        print(f"  {len(commits)} commits ({commits[0][0][:8]}..{commits[-1][0][:8]})", file=sys.stderr)

    # Scan each commit: store {file: {idens}}
    hashes = [h for h, _ in commits]
    commit_scans: dict[str, dict[str, set[str]]] = {}
    commit_all_idents: dict[str, set[str]] = {}

    for h in hashes:
        scan = _scan_commit(repo, h)
        if scan is None:
            continue
        commit_scans[h] = scan
        all_i: set[str] = set()
        for idents in scan.values():
            all_i.update(idents)
        commit_all_idents[h] = all_i

    # Restore HEAD
    subprocess.run(["git", "checkout", "--quiet", "HEAD"],
                   capture_output=True, cwd=repo)

    scannable = list(commit_scans.keys())
    if len(scannable) < 2:
        if not quiet:
            print("  Fewer than 2 scannable commits.", file=sys.stderr)
        return []

    if not quiet:
        print(f"  {len(scannable)} scannable commits", file=sys.stderr)

    # Detect births: token appears in commit N but not N-1
    token_births: dict[str, tuple[str, str, float]] = {}

    for i in range(1, len(scannable)):
        prev_h, curr_h = scannable[i - 1], scannable[i]
        prev_idents = commit_all_idents[prev_h]
        curr_idents = commit_all_idents[curr_h]
        new_tokens = curr_idents - prev_idents

        curr_ts = 0.0
        for h, ts in commits:
            if h == curr_h:
                curr_ts = ts
                break

        curr_scan = commit_scans[curr_h]
        for token in new_tokens:
            if token not in token_births:
                birth_file = ""
                for fpath, idents in curr_scan.items():
                    if token in idents:
                        birth_file = fpath
                        break
                token_births[token] = (curr_h, birth_file, curr_ts)

    if not quiet:
        print(f"  {len(token_births)} unique token births", file=sys.stderr)

    # Track infection: for each token, does it appear in new files in subsequent commits?
    events: list[CascadeEvent] = []
    hash_idx = {h: i for i, h in enumerate(scannable)}

    for token, (birth_h, birth_file, birth_ts) in token_births.items():
        bi = hash_idx.get(birth_h, -1)
        if bi < 0:
            continue

        infected: list[InfectionSite] = []
        seen_files: set[str] = set()
        end = min(bi + window + 1, len(scannable))

        for wi in range(bi + 1, end):
            wh = scannable[wi]
            gen = wi - bi
            scan = commit_scans[wh]
            for fpath, idents in scan.items():
                if token in idents and fpath not in seen_files and fpath != birth_file:
                    infected.append(InfectionSite(file=fpath, commit=wh, generation=gen))
                    seen_files.add(fpath)

        r0 = 0.0
        conf = 0.0
        if infected:
            max_gen = max(s.generation for s in infected)
            r0 = len(infected) / max(max_gen, 1)
            obs = len(set(s.generation for s in infected))
            conf = min(obs / 3.0, 1.0) if window >= 3 else 1.0

        if r0 >= threshold:
            events.append(CascadeEvent(
                token=token, birth_commit=birth_h, birth_file=birth_file,
                birth_timestamp=birth_ts, infected=infected,
                r0=round(r0, 2), confidence=round(conf, 2),
            ))

    events.sort(key=lambda e: -e.r0)
    return events


def format_rho_report(events: list[CascadeEvent],
                       repo: str = "", since: str = "",
                       threshold: float = 1.0,
                       compact: bool = False) -> str:
    """Human-readable R₀ report."""
    if compact:
        return _format_compact(events, threshold)
    return _format_detailed(events, repo, since, threshold)


def _format_compact(events: list[CascadeEvent], threshold: float) -> str:
    spreading = [e for e in events if e.r0 >= threshold]
    contained = [e for e in events if 0 < e.r0 < threshold]

    lines = ["═══ Rho: Token Cascade Report ═══"]
    lines.append(f"Threshold: R₀ ≥ {threshold}")
    lines.append("")

    if spreading:
        lines.append(f"\u0001f534 SPREADING ({len(spreading)})")
        for e in spreading:
            risk = "HIGH" if e.r0 >= 2.0 else "MODERATE" if e.r0 >= 1.5 else "LOW"
            lines.append(f"  {e.token:<25} R₀={e.r0:<5}  {risk:<8} {e.birth_file} \u2192 {len(e.infected)} files")
        lines.append("")

    if contained:
        lines.append(f"\u2705 CONTAINED ({len(contained)})")
        for e in contained[:5]:
            lines.append(f"  {e.token:<25} R₀={e.r0:<5}  {e.birth_file}")
        if len(contained) > 5:
            lines.append(f"  ... and {len(contained) - 5} more")
        lines.append("")

    if spreading:
        lines.append(f"Result: FAIL \u2014 {len(spreading)} spreading token(s) detected")
        lines.append("Action: review the token introduction and restrict its spread")
    else:
        total = len(spreading) + len(contained)
        lines.append(f"Result: PASS \u2014 {total} tokens checked, 0 spreading")

    return "\n".join(lines)


def _format_detailed(events: list[CascadeEvent], repo: str,
                      since: str, threshold: float) -> str:
    spreading = [e for e in events if e.r0 >= threshold]
    contained = [e for e in events if 0 < e.r0 < threshold]

    lines = ["═══ Rho: Detailed Token Cascade Report ═══"]
    lines.append(f"Repository: {repo}")
    lines.append(f"Range: {since}")
    lines.append(f"Threshold: R₀ \u2265 {threshold}")
    lines.append(f"Total events: {len(events)}")
    lines.append("")

    if spreading:
        lines.append(f"\u0001f534 SPREADING ({len(spreading)})")
        for e in spreading:
            risk = "HIGH" if e.r0 >= 2.0 else "MODERATE" if e.r0 >= 1.5 else "LOW"
            lines.append("")
            lines.append(f"  {e.token}  (R\u2080 = {e.r0}, {risk} risk, conf: {e.confidence})")
            lines.append(f"  \u251c\u2500 Birth commit: {e.birth_commit[:12]}")
            lines.append(f"  \u251c\u2500 Birth file:   {e.birth_file}")
            lines.append(f"  \u2514\u2500 Infected {len(e.infected)} file(s):")
            for site in e.infected:
                lines.append(f"       gen {site.generation}: {site.file} ({site.commit[:12]})")
        lines.append("")

    if contained:
        lines.append(f"\u2705 CONTAINED ({len(contained)})")
        for e in contained[:5]:
            lines.append(f"  {e.token:<25} R\u2080={e.r0:<5}  {e.birth_file}")
        if len(contained) > 5:
            lines.append(f"  ... and {len(contained) - 5} more")
        lines.append("")

    total_spread = len(spreading)
    total_contain = len(contained)
    total_zero = len(events) - total_spread - total_contain
    lines.append(f"Summary: {total_spread} spreading, {total_contain} contained, {total_zero} silent")

    if spreading:
        lines.append("")
        lines.append("Recommendation:")
        for e in spreading:
            lines.append(f"  - Review {e.token}: introduced in {e.birth_file}, "
                        f"spread to {len(e.infected)} files")

    return "\n".join(lines)


def format_rho_csv(events: list[CascadeEvent]) -> str:
    lines = ["token,birth_commit,birth_file,r0,confidence,infected_count"]
    for e in events:
        lines.append(f"{e.token},{e.birth_commit},{e.birth_file},{e.r0},{e.confidence},{len(e.infected)}")
    return "\n".join(lines)


def format_rho_json(events: list[CascadeEvent]) -> str:
    import json
    data = []
    for e in events:
        data.append({
            "token": e.token,
            "birth_commit": e.birth_commit,
            "birth_file": e.birth_file,
            "r0": e.r0,
            "confidence": e.confidence,
            "infected": [
                {"file": s.file, "commit": s.commit, "generation": s.generation}
                for s in e.infected
            ],
        })
    return json.dumps(data, indent=2)
