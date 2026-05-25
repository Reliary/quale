#!/usr/bin/env python3
"""Smoke test all candidate models on structural verification.

Case: autopsylab-agent, packages/cli/src/commands/claim.ts
Correct test: packages/cli/tests/claim.test.ts
"""
import json, os, subprocess, sys, time, urllib.request

DEEPINFRA = "https://api.deepinfra.com/v1/openai/chat/completions"
KEY = "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
VOCAB_ROOT = "/home/user/src/vocab"

MODELS = [
    # Cheaper/faster
    "Qwen/Qwen3-Max",
    "Qwen/Qwen3.5-35B-A3B",
    "google/gemma-3-27b-it",
    "microsoft/phi-4",
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    # New families
    "moonshotai/Kimi-K2.5",
    "google/gemini-2.5-pro",
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V4-Pro",
    # Big MOE beasts
    "Qwen/Qwen3-235B-A22B-Thinking-2507",
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
]

# Also try K2.6 and V3.1-Terminus if available
EXTRAS = [
    "moonshotai/Kimi-K2.6",
    "deepseek-ai/DeepSeek-V3.1-Terminus",
]

SRC = "packages/cli/src/commands/claim.ts"
CORRECT = "packages/cli/tests/claim.test.ts"

def run_vocab(extra_args, cwd="/home/user/src/autopsylab-agent"):
    env = {**os.environ, "PYTHONPATH": VOCAB_ROOT}
    r = subprocess.run(
        [sys.executable, "-m", "vocab.cli", *extra_args],
        cwd=cwd, capture_output=True, text=True, timeout=120, env=env
    )
    return r.stdout.strip()

def call_llm(model, prompt, max_tokens=300, timeout=120):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        DEEPINFRA, data=body,
        headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {KEY}"},
        method="POST"
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        elapsed = time.time() - start
        choice = d.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = d.get("usage", {}) or {}
        return msg.get("content", ""), elapsed, usage
    except Exception as e:
        elapsed = time.time() - start
        return f"ERROR: {e}", elapsed, {}

def test_model(label, model):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Model: {model}")
    print(f"{'='*60}")

    # Baseline prompt
    base_prompt = (
        f"Which test file verifies this change?\n"
        f"Source file: {SRC}\n"
        f"Task: add new CLI claim subcommand\n"
        f'Reply with ONLY a JSON object: {{"verify": "<path>"}}. '
        f"Respond with the JSON and nothing else."
    )

    content, elapsed, usage = call_llm(model, base_prompt)
    pt = usage.get("prompt_tokens", "?")
    ct = usage.get("completion_tokens", "?")
    tt = usage.get("total_tokens", "?")

    base_ok = CORRECT in content if content else False
    base_json_valid = False
    try:
        j = json.loads(content.strip())
        if "verify" in j:
            base_json_valid = True
    except:
        pass

    print(f"  [BASELINE] {elapsed:5.1f}s tok={tt} json={base_json_valid} correct={base_ok}")
    print(f"    raw: {content[:80]}")

    # Vocab prompt
    vocab_prompt = (
        f"Which test file verifies this change?\n"
        f"Source file: {SRC}\n"
        f"Task: add new CLI claim subcommand\n"
        f"Vocab analysis: candidates=['packages/cli/tests/claim.test.ts', 'packages/cli/tests/claude-code-tool.test.ts'] "
        f"deterministic_verify=packages/cli/tests/claim.test.ts\n"
        f'Reply with ONLY a JSON object: {{"verify": "<path>"}}. '
        f"Respond with the JSON and nothing else."
    )

    content2, elapsed2, usage2 = call_llm(model, vocab_prompt)
    pt2 = usage2.get("prompt_tokens", "?")
    ct2 = usage2.get("completion_tokens", "?")
    tt2 = usage2.get("total_tokens", "?")

    vocab_ok = CORRECT in content2 if content2 else False
    vocab_json_valid = False
    try:
        j = json.loads(content2.strip())
        if "verify" in j:
            vocab_json_valid = True
    except:
        pass

    print(f"  [VOCAB]   {elapsed2:5.1f}s tok={tt2} json={vocab_json_valid} correct={vocab_ok}")
    print(f"    raw: {content2[:80]}")

    return {
        "model": model,
        "label": label,
        "baseline": {"ok": base_ok, "json": base_json_valid, "tokens": tt, "time": elapsed, "content": content[:120]},
        "vocab": {"ok": vocab_ok, "json": vocab_json_valid, "tokens": tt2, "time": elapsed2, "content": content2[:120]},
        "note": "",
    }


def main():
    # Get vocab output once (shared across all model tests)
    vp_out = run_vocab(["verify-packet", "--files", SRC,
        "--task", "add new CLI claim subcommand", "--format", "json"],
        cwd="/home/user/src/autopsylab-agent")
    print(f"Vocab verify-packet output:\n  {vp_out[:200]}\n")

    results = []
    for model in MODELS:
        slug = model.split("/")[-1]
        r = test_model(slug, model)
        results.append(r)
        time.sleep(1)  # rate limit

    # Test extras
    for model in EXTRAS:
        slug = model.split("/")[-1]
        r = test_model(f"EXTRAS: {slug}", model)
        results.append(r)
        time.sleep(1)

    # Summary
    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  {'MODEL':40s} {'BASE JSON':10s} {'BASE OK':8s} {'VOCAB JSON':10s} {'VOCAB OK':8s}")
    print(f"  {'-'*76}")
    for r in results:
        m = r['model'].split('/')[-1][:38]
        bj = "✓" if r['baseline']['json'] else "✗"
        bo = "✓" if r['baseline']['ok'] else "✗"
        vj = "✓" if r['vocab']['json'] else "✗"
        vo = "✓" if r['vocab']['ok'] else "✗"
        print(f"  {m:40s} {bj:10s} {bo:8s} {vj:10s} {vo:8s}")

    # Save
    out = "/home/user/src/vocab/tmp/model_smoke_test.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out}")

if __name__ == "__main__":
    main()
