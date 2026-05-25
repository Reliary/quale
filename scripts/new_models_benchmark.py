#!/usr/bin/env python3
"""New models on the structural benchmark (cross-dir + same-dir)."""
import json, os, subprocess, sys, urllib.request, re, time
from pathlib import Path

DEEPINFRA = "https://api.deepinfra.com/v1/openai/chat/completions"
API_KEY = "dat5OdxUIGqHKqwjiq0DAjFHzGtcZQ1e"
VOCAB_ROOT = "/home/user/src/vocab"

MODELS = [
    "XiaomiMiMo/MiMo-V2.5-Pro",
    "Qwen/Qwen3.6-35B-A3B",
    "Qwen/Qwen3-Max-Thinking",
]

def llm(model, prompt, max_tokens=600):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.1}).encode()
    req = urllib.request.Request(DEEPINFRA, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read())
        choice = d.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = d.get("usage", {}) or {}
        cd = usage.get("completion_tokens_details", {}) or {}
        return msg.get("content", ""), usage
    except Exception as e:
        return f"ERROR: {e}", {}

def run_vocab(args, cwd):
    env = {**os.environ, "PYTHONPATH": VOCAB_ROOT}
    r = subprocess.run([sys.executable, "-m", "vocab.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
    return r.stdout.strip()

def extract_path(text):
    m = re.search(r'"verify"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else text.strip()[:120]

CROSS_DIR = [
    ("agent: spool.ts", "/home/user/src/autopsylab-agent",
     "packages/core/src/spool.ts", "packages/core/tests/spool.test.ts",
     "add retry with backoff to spool upload"),
    ("agent: redaction.ts", "/home/user/src/autopsylab-agent",
     "packages/core/src/redaction.ts", "packages/core/tests/redaction.test.ts",
     "fix PII redaction in error messages"),
    ("agent: plugin.ts", "/home/user/src/autopsylab-agent",
     "packages/opencode/src/plugin.ts", "packages/opencode/tests/plugin.test.ts",
     "add event handler for session end"),
    ("server: fingerprint_read.go", "/home/user/src/autopsylab",
     "internal/handlers/fingerprint_read.go", "tests/functional/api_fingerprints_test.go",
     "add handler for listing fingerprints"),
    ("server: auth.go", "/home/user/src/autopsylab",
     "internal/handlers/auth.go", "tests/unit/handlers/auth_test.go",
     "validate API key authentication"),
    ("server: runbook_factory.go", "/home/user/src/autopsylab",
     "internal/services/runbook_factory.go", "tests/functional/api_playbooks_test.go",
     "modify runbook generation logic"),
]

SAME_DIR = [
    ("prometheus: analyze.go", "/tmp/corpus-sweep/prometheus",
     "cmd/promtool/analyze.go", "cmd/promtool/analyze_test.go",
     "add new promtool analysis rule"),
    ("prometheus: backfill.go", "/tmp/corpus-sweep/prometheus",
     "cmd/promtool/backfill.go", "cmd/promtool/backfill_test.go",
     "add backfill support for remote write"),
]

def main():
    results = []
    all_cases = CROSS_DIR + SAME_DIR

    for label, repo_path, src_file, gt_test, task in all_cases:
        ptype = "cross-dir" if any(f in label for f in ["agent:", "server:"]) else "same-dir"
        print(f"\n  [{ptype}] {label}")
        print(f"    GT: {gt_test}")

        vp_out = run_vocab(["verify-packet", "--files", src_file, "--task", task, "--format", "json"], repo_path)
        try:
            vpd = json.loads(vp_out) if vp_out.strip() else {}
        except:
            vpd = {}
        vtier = vpd.get("tier", "?")
        candidates = [str(c) for c in vpd.get("verification_candidates", [])]
        deter = vpd.get("deterministic_verify", {})
        print(f"    Vocab: tier={vtier} det={deter.get('file','')} cand={candidates[:3]}")

        for model in MODELS:
            for cond, prompt_fn in [
                ("baseline", lambda: f"Task: {task}. Edit file: {src_file}. "
                                     f'Which test verifies this change? Reply JSON: {{"verify": "<path>"}}'),
                ("vocab", lambda: f"Edit file: {src_file}. "
                    f"Vocab ({vtier}): candidates={candidates[:4]} "
                    f"deterministic={deter.get('file','')}. "
                    f'Which test verifies? Reply JSON: {{"verify": "<path>"}}'),
            ]:
                content, usage = llm(model, prompt_fn())
                pred = extract_path(content)
                match = gt_test in pred
                pt = usage.get("prompt_tokens", 0)
                ot = usage.get("completion_tokens", 0)
                tt = usage.get("total_tokens", 0)
                status = "✓" if match else "✗"

                # Extract thinking tokens if available
                cd = usage.get("completion_tokens_details", {}) or {}
                rt = cd.get("reasoning_tokens", None)

                extra = f" (rt={rt})" if rt else ""
                print(f"    {model.split('/')[-1][:30]:30s} [{cond:8s}] {pred:45s} {status}  p={pt} o={ot} t={tt}{extra}")
                results.append({"model": model, "label": label, "type": ptype,
                    "condition": cond, "gt": gt_test, "prediction": pred,
                    "correct": match, "prompt_tokens": pt, "output_tokens": ot,
                    "total_tokens": tt, "vocab_tier": vtier})
            time.sleep(0.3)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for model in MODELS:
        mr = [r for r in results if r["model"] == model]
        for ptype in ["cross-dir", "same-dir"]:
            pr = [r for r in mr if ptype in r["type"]]
            if not pr:
                continue
            base_ok = sum(1 for r in pr if r["condition"]=="baseline" and r["correct"])
            base_tot = sum(1 for r in pr if r["condition"]=="baseline")
            vocab_ok = sum(1 for r in pr if r["condition"]=="vocab" and r["correct"])
            vocab_tot = sum(1 for r in pr if r["condition"]=="vocab")
            base_avg_t = sum(r["total_tokens"] for r in pr if r["condition"]=="baseline") / base_tot
            vocab_avg_t = sum(r["total_tokens"] for r in pr if r["condition"]=="vocab") / vocab_tot
            print(f"  {model.split('/')[-1][:35]:35s} {ptype:12s}  base={base_ok}/{base_tot} ({base_avg_t:.0f}t)  vocab={vocab_ok}/{vocab_tot} ({vocab_avg_t:.0f}t)")

    out = {"results": results}
    with open("/tmp/vocab-new-models.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to /tmp/vocab-new-models.json")

if __name__ == "__main__":
    main()
