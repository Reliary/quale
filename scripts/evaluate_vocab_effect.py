#!/usr/bin/env python3
"""Evaluate whether vocab changes agent behavior.

This harness compares a no-vocab baseline against vocab guidance conditions.
It is intentionally explicit and report-oriented: if vocab only adds tokens without
improving file discovery, verification choice, or edit containment, it should not
be marketed as agent guidance.

Default model: deepseek-v4-flash.
Credentials: DEEPSEEK_API_KEY or ~/.local/share/opencode/auth.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOCAB_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("/tmp/vocab-effect-results.json")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
SOURCE_EXTS = {
    ".c", ".cc", ".clj", ".cpp", ".cs", ".ex", ".exs", ".go", ".h",
    ".hpp", ".hs", ".java", ".jl", ".js", ".jsx", ".kt", ".ml",
    ".mli", ".nim", ".nix", ".php", ".py", ".r", ".rs", ".scala",
    ".swift", ".ts", ".tsx", ".zig", ".erl", ".hrl",
}
SKIP_PARTS = {
    ".git", ".reliary", "__pycache__", "build", "coverage", "dist",
    "node_modules", "target", "vendor",
}


@dataclass(frozen=True)
class Case:
    bucket: str
    repo: str
    path: str
    task: str
    edit_file: str
    read_files: tuple[str, ...]
    verify_files: tuple[str, ...]


CASES: tuple[Case, ...] = (
    # Likely in training data: famous public repos.
    Case("seen_public", "flask", "/tmp/corpus-sweep/flask",
         "add a cache_route decorator for caching route handler responses",
         "src/flask/app.py",
         ("src/flask/app.py", "src/flask/sansio/app.py", "src/flask/helpers.py"),
         ("tests/test_basic.py", "tests/test_helpers.py")),
    Case("seen_public", "django", "/tmp/corpus-sweep/django",
         "add middleware that records HTTP request timing metrics",
         "django/middleware/common.py",
         ("django/middleware/common.py", "django/utils/deprecation.py", "django/middleware/csrf.py"),
         ("tests/middleware/tests.py", "tests/utils_tests/test_deprecation.py")),
    Case("seen_public", "redis", "/tmp/corpus-sweep/redis",
         "add a new client connection accounting field to network handling",
         "src/networking.c",
         ("src/networking.c", "src/server.h", "src/server.c"),
         ("tests/unit/networking.tcl",)),
    Case("seen_public", "nginx", "/tmp/corpus-sweep/nginx",
         "add a new HTTP core directive that affects request handling",
         "src/http/ngx_http_core_module.c",
         ("src/http/ngx_http_core_module.c", "src/http/ngx_http_request.c", "src/core/ngx_conf_file.c"),
         ("src/http/ngx_http_core_module.c",)),

    # Public but less obvious or language-diverse.
    Case("weird_public", "gin", "/tmp/corpus-sweep/gin",
         "add response compression middleware that gzips HTTP responses",
         "auth.go",
         ("gin.go", "context.go", "auth.go"),
         ("middleware_test.go", "gin_test.go")),
    Case("weird_public", "serde", "/tmp/corpus-sweep/serde",
         "add a new serde derive macro for displaying enum variants",
         "serde_derive/src/lib.rs",
         ("serde_derive/src/lib.rs", "serde_derive/src/de.rs", "serde_derive/src/internals/attr.rs"),
         ("test_suite/tests/test_annotations.rs",)),
    Case("weird_public", "otp", "/tmp/corpus-sweep/otp",
         "add supervisor restart strategy validation for child specs",
         "lib/stdlib/src/supervisor.erl",
         ("lib/stdlib/src/supervisor.erl", "lib/stdlib/src/supervisor_bridge.erl"),
         ("lib/stdlib/test/supervisor_SUITE.erl",)),
    Case("weird_public", "Nim", "/tmp/corpus-sweep/Nim",
         "add compiler option validation for a new experimental flag",
         "compiler/options.nim",
         ("compiler/options.nim", "compiler/front/optionsprocessor.nim", "compiler/commands.nim"),
         ("tests/options",)),

    # Private/unseen to the public model training corpus.
    Case("private_unseen", "autopsylab-agent", "/home/user/src/autopsylab-agent",
         "add a new typed evidence envelope called CacheEvidence",
         "packages/core/src/typed-evidence.ts",
         ("packages/core/src/typed-evidence.ts", "packages/core/src/types.ts", "packages/core/src/redaction.ts"),
         ("packages/core/tests/typed-evidence.test.ts",)),
    Case("private_unseen", "autopsylab", "/home/user/src/autopsylab",
         "add a handler for listing fingerprints by source",
         "internal/handlers/fingerprint_read.go",
         ("internal/handlers/fingerprint_read.go", "internal/handlers/app.go", "internal/services/fingerprint.go"),
         ("internal/handlers/fingerprint_read_test.go",)),
    Case("private_unseen", "vocab", "/home/user/src/vocab",
         "add a file-scoped preflight command",
         "vocab/cli.py",
         ("vocab/cli.py", "vocab/reports.py", "vocab/compare.py"),
         ("tests/test_commands.py", "tests/test_cli.py")),
    Case("private_unseen", "llm-semantic-transport", "/home/user/src/llm-semantic-transport",
         "add a minhash compression backend for fuzzy duplicate detection",
         "app/compression/__init__.py",
         ("app/compression/__init__.py", "app/compression/crispr_v2_backend.py", "app/compression/base.py"),
         ("evals/test_crispr_v2.py",)),
)


DISCOVERY_CONDITIONS = ("baseline", "bootstrap_summary", "bootstrap_checklist", "crystallography", "route_policy")
PREFLIGHT_CONDITIONS = (
    "candidate_baseline", "preflight_compact", "preflight_checklist", "verify_mcq",
    "preflight_tool", "preflight_tool_sprawl_guard", "desert_aware_preflight", "route_policy",
    "preflight_tool_llm", "preflight_tool_full",
)


def deepseek_key() -> str:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    auth_path = Path.home() / ".local/share/opencode/auth.json"
    if auth_path.exists():
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        key = data.get("deepseek", {}).get("key")
        if key:
            return key
    raise RuntimeError("missing DeepSeek key: set DEEPSEEK_API_KEY or opencode auth.json")


def deepseek_call(messages: list[dict[str, str]], model: str, temperature: float, json_mode: bool, max_tokens: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {deepseek_key()}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:400] if e.fp else ""
        return {"error": f"HTTP {e.code}: {body_text}"}
    except Exception as e:
        return {"error": str(e)}


def run_vocab(repo_path: str, args: list[str], timeout: int = 90) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "vocab.cli", *args],
        cwd=str(VOCAB_ROOT),
        env={**os.environ, "PYTHONPATH": str(VOCAB_ROOT)},
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return f"VOCAB ERROR: {result.stderr.strip()[:500]}"
    return result.stdout.strip()


def source_files(repo_path: str, limit: int = 120) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "ls-files", "-z"],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            return []
        files = result.stdout.decode("utf-8", errors="replace").strip("\0").split("\0")
    except Exception:
        return []

    selected: list[str] = []
    for file in files:
        if not file:
            continue
        path = Path(file)
        if path.suffix.lower() not in SOURCE_EXTS:
            continue
        if set(path.parts) & SKIP_PARTS:
            continue
        selected.append(file)
    return selected[:limit]


def case_available(case: Case) -> tuple[bool, str]:
    repo = Path(case.path)
    if not (repo / ".git").exists():
        return False, "repo missing"
    if not (repo / case.edit_file).exists():
        return False, f"ground-truth edit file missing: {case.edit_file}"
    return True, "ok"


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"_parse_error": text[:500]}


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def score_discovery(parsed: dict[str, Any], case: Case) -> dict[str, Any]:
    reads = normalize_list(parsed.get("read"))
    edit = parsed.get("edit") if isinstance(parsed.get("edit"), str) else ""
    top3 = reads[:3] + ([edit] if edit else [])
    return {
        "edit_hit": edit == case.edit_file,
        "edit_dir_hit": bool(edit and Path(edit).parent == Path(case.edit_file).parent),
        "read_hit_count": sum(1 for file in case.read_files if file in reads),
        "edit_in_top3": case.edit_file in top3,
        "reads": reads[:5],
        "edit": edit,
    }


def score_preflight(parsed: dict[str, Any], case: Case) -> dict[str, Any]:
    verify = normalize_list(parsed.get("verify"))
    extra_edits = [file for file in normalize_list(parsed.get("extra_edits")) if file != case.edit_file]
    return {
        "verify_hit": any(file in verify for file in case.verify_files),
        "verify_hit_count": sum(1 for file in case.verify_files if file in verify),
        "extra_edit_count": len(extra_edits),
        "extra_edits": extra_edits[:5],
        "verify": verify[:5],
    }


def discovery_messages(case: Case, condition: str, files: list[str]) -> list[dict[str, str]]:
    guidance = ""
    crystallography = ""
    if condition == "bootstrap_summary":
        guidance = run_vocab(case.path, ["agent-bootstrap", case.path, "--task", case.task, "--summary"])
    elif condition == "bootstrap_checklist":
        guidance = run_vocab(case.path, ["agent-bootstrap", case.path, "--task", case.task, "--format", "checklist"])
    elif condition == "crystallography":
        raw = run_vocab(case.path, ["crystallography", "--path", case.path, "--format", "json"])
        try:
            parsed = json.loads(raw)
            crystallography = parsed.get("skeleton", raw)
        except Exception:
            crystallography = raw
    elif condition == "route_policy":
        route = run_route(case, files=None)
        if route.get("action") == "crystallography_only":
            crystallography = run_vocab(case.path, ["skeleton", "--path", case.path])
        elif route.get("action") == "no_vocab":
            guidance = ""
        elif route.get("command"):
            guidance = "Route: " + " ".join(route.get("command", []))

    system = (
        "You are evaluating an unfamiliar codebase. Select files for a task. "
        "Return exactly one compact JSON object and no markdown. "
        "Use keys: read (array of 3 relative paths), edit (one relative path), confidence (low|mixed|high)."
    )
    if crystallography:
        system += f"\n\nRepo structural summary:\n{crystallography}"
    user = [
        f"Repository bucket: {case.bucket}",
        f"Repository: {case.repo}",
        f"Task: {case.task}",
    ]
    if guidance:
        user.extend(["", "Vocab guidance:", guidance])
    user.extend(["", "Source files:", "\n".join(files), "", "Return exactly one compact JSON object only."])
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(user)}]


def preflight_messages(case: Case, condition: str, files: list[str]) -> list[dict[str, str]]:
    guidance = ""
    if condition == "preflight_compact":
        guidance = run_vocab(case.path, ["preflight", "--path", case.path, "--files", case.edit_file, "--task", case.task, "--format", "compact"])
    elif condition == "preflight_checklist":
        guidance = run_vocab(case.path, ["preflight", "--path", case.path, "--files", case.edit_file, "--task", case.task, "--format", "checklist"])
    elif condition == "verify_mcq":
        guidance = run_vocab(case.path, ["verify", "--path", case.path, "--files", case.edit_file, "--task", case.task])
    elif condition == "preflight_tool":
        guidance = preflight_tool_guidance(case)
    elif condition == "preflight_tool_sprawl_guard":
        guidance = preflight_tool_guidance(case, include_sprawl_guard=True)
    elif condition == "desert_aware_preflight":
        guidance = preflight_tool_guidance(case, include_sprawl_guard=True, desert_aware=True)
    elif condition == "preflight_tool_llm":
        guidance = run_vocab(case.path, ["preflight", "--path", case.path, "--files", case.edit_file, "--task", case.task, "--format", "llm"])
    elif condition == "preflight_tool_full":
        guidance = run_vocab(case.path, ["preflight", "--path", case.path, "--files", case.edit_file, "--task", case.task, "--format", "tool"])
    elif condition == "route_policy":
        route = run_route(case, files=[case.edit_file])
        if route.get("action") == "preflight_tool":
            guidance = preflight_tool_guidance(case, include_sprawl_guard=True, desert_aware=True)
        elif route.get("action") == "no_vocab":
            guidance = ""
        elif route.get("command"):
            guidance = "Route: " + " ".join(route.get("command", []))

    system = (
        "You are about to edit a candidate file. Decide verification and avoid unnecessary edit sprawl. "
        "Return exactly one compact JSON object and no markdown. "
        "Use keys: verify (array of relative paths), extra_edits (array of relative paths), should_edit_candidate (boolean)."
    )
    if condition in {"preflight_tool_sprawl_guard", "desert_aware_preflight", "route_policy"}:
        system += " Obey report-only sprawl guidance: do not propose extra_edits unless the task explicitly requires them."
    if condition in {"desert_aware_preflight", "route_policy"}:
        system += " Do not use source files as verification unless they are explicitly test or suite files; empty verify is better than a fake test."
    user = [
        f"Repository bucket: {case.bucket}",
        f"Repository: {case.repo}",
        f"Task: {case.task}",
        f"Candidate edit file: {case.edit_file}",
    ]
    if guidance:
        user.extend(["", "Vocab preflight:", guidance])
    user.extend(["", "Source files:", "\n".join(files), "", "Return exactly one compact JSON object only."])
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(user)}]


def run_route(case: Case, files: list[str] | None) -> dict[str, Any]:
    args = ["route", "--path", case.path, "--task", case.task, "--format", "json"]
    for file in files or []:
        args.extend(["--files", file])
    raw = run_vocab(case.path, args)
    try:
        return json.loads(raw)
    except Exception:
        return {"action": "route_error", "raw": raw}


def preflight_tool_guidance(case: Case, include_sprawl_guard: bool = False, desert_aware: bool = False) -> str:
    raw = run_vocab(case.path, ["preflight", "--path", case.path, "--files", case.edit_file, "--task", case.task, "--format", "tool"])
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw

    parts: list[str] = []
    mc = parsed.get("verification_mc", {})
    candidates = mc.get("candidates") or []
    if candidates:
        parts.append(f"Verification: {mc.get('question', 'Which file verifies this change?')} Candidates: {', '.join(candidates[:3])}")
    else:
        parts.append("Verification: no structural candidates found")

    verification_confidence = parsed.get("verification_confidence", {})
    if verification_confidence:
        reasons = "; ".join(verification_confidence.get("reasons", [])[:2])
        parts.append(f"Verification confidence: {verification_confidence.get('level', 'unknown')} ({reasons})")

    if include_sprawl_guard:
        guard = parsed.get("edit_sprawl_guard", {})
        question = guard.get("question_extra_edits", [])[:3]
        if question:
            risky = ", ".join(item.get("file", "") for item in question if item.get("file"))
            parts.append(f"Sprawl guard: question extra edits outside candidate file: {risky}")
        instruction = guard.get("instruction")
        if instruction:
            parts.append(f"Sprawl instruction: {instruction}")

    if desert_aware and verification_confidence.get("level") in {"low", "mixed", "unknown"}:
        parts.append("Desert warning: verification topology is weak; do not invent tests or use source files as tests.")

    return "\n".join(part for part in parts if part)


def run_trial(case: Case, suite: str, condition: str, trial: int, model: str, temperature: float, dry_run: bool, json_mode: bool, max_tokens: int) -> dict[str, Any]:
    files = source_files(case.path)
    if case.edit_file not in files:
        files = [case.edit_file, *files]
    messages = discovery_messages(case, condition, files) if suite == "discovery" else preflight_messages(case, condition, files)

    row: dict[str, Any] = {
        "suite": suite,
        "bucket": case.bucket,
        "repo": case.repo,
        "condition": condition,
        "trial": trial,
        "task": case.task,
        "gt_edit_file": case.edit_file,
        "prompt_chars": sum(len(message["content"]) for message in messages),
    }
    if dry_run:
        row["dry_run"] = True
        row["prompt_preview"] = messages[-1]["content"][:1200]
        return row

    started = time.time()
    response = deepseek_call(messages, model=model, temperature=temperature, json_mode=json_mode, max_tokens=max_tokens)
    row["elapsed_seconds"] = round(time.time() - started, 2)
    if "error" in response:
        row["error"] = response["error"]
        return row

    usage = response.get("usage", {})
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = extract_json(content)
    row.update({
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "response": content,
        "parsed": parsed,
    })
    if "_parse_error" in parsed:
        retry_row = retry_trial(case, suite, condition, model, temperature, json_mode, max_tokens)
        if retry_row is not None:
            retry_row.update({
                "suite": row["suite"],
                "bucket": row["bucket"],
                "repo": row["repo"],
                "condition": row["condition"],
                "trial": row["trial"],
                "task": row["task"],
                "gt_edit_file": row["gt_edit_file"],
                "retry_after_parse_error": True,
                "initial_response": content[:500],
            })
            return retry_row
        row["parse_error"] = True
        return row
    row.update(score_discovery(parsed, case) if suite == "discovery" else score_preflight(parsed, case))
    return row


def retry_trial(case: Case, suite: str, condition: str, model: str, temperature: float, json_mode: bool, max_tokens: int) -> dict[str, Any] | None:
    files = source_files(case.path, limit=60)
    if case.edit_file not in files:
        files = [case.edit_file, *files]
    messages = discovery_messages(case, condition, files) if suite == "discovery" else preflight_messages(case, condition, files)
    messages[0]["content"] += " If unsure, still return valid JSON with empty arrays."
    response = deepseek_call(messages, model=model, temperature=temperature, json_mode=False if json_mode else json_mode, max_tokens=max(max_tokens, 1800))
    if "error" in response:
        return None
    usage = response.get("usage", {})
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = extract_json(content)
    if "_parse_error" in parsed:
        return None
    row: dict[str, Any] = {
        "prompt_chars": sum(len(message["content"]) for message in messages),
        "elapsed_seconds": None,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "response": content,
        "parsed": parsed,
    }
    row.update(score_discovery(parsed, case) if suite == "discovery" else score_preflight(parsed, case))
    return row


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for suite in sorted({r["suite"] for r in results}):
        summary[suite] = {}
        all_suite_rows = [r for r in results if r["suite"] == suite and not r.get("dry_run")]
        suite_rows = [r for r in all_suite_rows if "error" not in r]
        for condition in sorted({r["condition"] for r in suite_rows}):
            attempted = [r for r in all_suite_rows if r["condition"] == condition]
            errored = [r for r in attempted if "error" in r]
            parsed_errors = [r for r in attempted if r.get("parse_error")]
            rows = [r for r in suite_rows if r["condition"] == condition and not r.get("parse_error")]
            if not rows:
                if attempted:
                    summary[suite][condition] = {
                        "runs": 0,
                        "attempted": len(attempted),
                        "error_rate": round(len(errored) / len(attempted), 3),
                        "parse_error_rate": round(len(parsed_errors) / len(attempted), 3),
                    }
                continue
            if suite == "discovery":
                summary[suite][condition] = {
                    "runs": len(rows),
                    "attempted": len(attempted),
                    "error_rate": round(len(errored) / len(attempted), 3),
                    "parse_error_rate": round(len(parsed_errors) / len(attempted), 3),
                    "edit_hit_rate": avg_bool(rows, "edit_hit"),
                    "edit_in_top3_rate": avg_bool(rows, "edit_in_top3"),
                    "avg_read_hit_count": avg_num(rows, "read_hit_count"),
                    "avg_input_tokens": avg_num(rows, "input_tokens"),
                }
            else:
                summary[suite][condition] = {
                    "runs": len(rows),
                    "attempted": len(attempted),
                    "error_rate": round(len(errored) / len(attempted), 3),
                    "parse_error_rate": round(len(parsed_errors) / len(attempted), 3),
                    "verify_hit_rate": avg_bool(rows, "verify_hit"),
                    "avg_verify_hit_count": avg_num(rows, "verify_hit_count"),
                    "avg_extra_edit_count": avg_num(rows, "extra_edit_count"),
                    "avg_input_tokens": avg_num(rows, "input_tokens"),
                }
    return summary


def avg_bool(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(1 for row in rows if row.get(key)) / max(len(rows), 1), 3)


def avg_num(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(float(row.get(key, 0) or 0) for row in rows) / max(len(rows), 1), 2)


def select_cases(buckets: set[str], max_cases: int | None) -> list[Case]:
    selected: list[Case] = []
    for case in CASES:
        if buckets and case.bucket not in buckets:
            continue
        ok, reason = case_available(case)
        if not ok:
            print(f"skip {case.repo}: {reason}", file=sys.stderr)
            continue
        selected.append(case)
        if max_cases is not None and len(selected) >= max_cases:
            break
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure whether vocab guidance changes agent outcomes.")
    parser.add_argument("--suite", choices=("discovery", "preflight", "all"), default="all")
    parser.add_argument("--bucket", action="append", choices=("seen_public", "weird_public", "private_unseen"), default=[])
    parser.add_argument("--condition", action="append", default=[])
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--no-json-mode", action="store_true", help="Do not request provider JSON response mode")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    suites = ["discovery", "preflight"] if args.suite == "all" else [args.suite]
    cases = select_cases(set(args.bucket), args.max_cases)
    if not cases:
        raise SystemExit("no available cases")

    results: list[dict[str, Any]] = []
    for suite in suites:
        default_conditions = DISCOVERY_CONDITIONS if suite == "discovery" else PREFLIGHT_CONDITIONS
        conditions = tuple(args.condition) if args.condition else default_conditions
        for case in cases:
            for condition in conditions:
                if condition not in default_conditions:
                    continue
                for trial in range(1, args.trials + 1):
                    print(f"[{suite}] {case.bucket}/{case.repo} {condition} trial {trial}", flush=True)
                    results.append(run_trial(case, suite, condition, trial, args.model, args.temperature, args.dry_run, not args.no_json_mode, args.max_tokens))

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "json_mode": not args.no_json_mode,
        "max_tokens": args.max_tokens,
        "dry_run": args.dry_run,
        "summary": summarize(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    print(f"results: {args.output}")


if __name__ == "__main__":
    main()
