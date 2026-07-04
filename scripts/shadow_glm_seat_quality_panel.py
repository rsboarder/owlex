"""Panel quality blind-rate — rule out single-rater (gpt-5.5) bias.

Same experiment as shadow_glm_seat_quality.py, but each council's anonymized answer
set (real seats + GLM-5.2-via-opencode, shuffled by a deterministic salt) is rated by
THREE independent raters: gpt-5.5 (codex), claude, gemini. All three see the IDENTICAL
blind layout (letters only — no agent names, no hint which is GLM). If all three rank
GLM near-bottom, "weak seat" is robust; if they disagree, it's rater taste.

Read-only against owlex.db. Output:
  scripts/shadow_results/seat_quality_panel.jsonl
  scripts/shadow_results/seat_quality_panel.md

Usage:
  python scripts/shadow_glm_seat_quality_panel.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shadow_glm_seat_quality import (  # noqa: E402
    OWLEX_DB, RESULTS_DIR, GLM_KEY,
    build_prompt, load_glm_seat_responses, load_incumbents,
    parse_ratings, _rank_key, call_codex,
)

JSONL_PATH = RESULTS_DIR / "seat_quality_panel.jsonl"
SUMMARY_PATH = RESULTS_DIR / "seat_quality_panel.md"
RATERS = ["gpt5.5", "claude", "gemini"]
CLAUDE_TIMEOUT = int(os.getenv("OWLEX_PANEL_CLAUDE_TIMEOUT", "180"))
GEMINI_TIMEOUT = int(os.getenv("OWLEX_PANEL_GEMINI_TIMEOUT", "180"))
DIMS = ("groundedness", "helpfulness", "correctness")


async def call_claude(prompt: str) -> tuple[str, str | None]:
    """Rate via claude CLI (subscription). Run in a temp cwd to minimize project context."""
    with tempfile.TemporaryDirectory() as td:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--output-format", "json",
            cwd=td,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=CLAUDE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill(); await proc.wait()
            return "", f"timeout after {CLAUDE_TIMEOUT}s"
    if proc.returncode != 0:
        return "", f"claude exit {proc.returncode}: {err.decode(errors='replace')[:150]}"
    raw = out.decode(errors="replace")
    try:
        j = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None  # let parse_ratings scan the text
    if isinstance(j, list):
        res = next((e.get("result") for e in reversed(j)
                    if isinstance(e, dict) and e.get("type") == "result"), None)
        return (res or raw), None
    if isinstance(j, dict):
        return (j.get("result") or j.get("text") or raw), None
    return raw, None


async def call_gemini(prompt: str) -> tuple[str, str | None]:
    """Rate via gemini CLI (subscription)."""
    with tempfile.TemporaryDirectory() as td:
        proc = await asyncio.create_subprocess_exec(
            "gemini", "--skip-trust", "-p", prompt,
            cwd=td,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=GEMINI_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill(); await proc.wait()
            return "", f"timeout after {GEMINI_TIMEOUT}s"
    if proc.returncode != 0:
        return "", f"gemini exit {proc.returncode}: {err.decode(errors='replace')[:150]}"
    return out.decode(errors="replace"), None


RATER_FN = {"gpt5.5": call_codex, "claude": call_claude, "gemini": call_gemini}


async def rate_one(rater: str, prompt: str, label_to_key: dict[str, str]) -> dict:
    raw, err = await RATER_FN[rater](prompt)
    if err:
        return {"error": err}
    ratings = parse_ratings(raw, list(label_to_key.keys()))
    if not ratings:
        return {"parse_error": raw[:200]}
    by_agent = {label_to_key[l]: r for l, r in ratings.items() if l in label_to_key}
    ranked = sorted(by_agent.items(), key=lambda kv: _rank_key(kv[1]), reverse=True)
    glm_rank = next((i for i, (a, _) in enumerate(ranked, 1) if a == GLM_KEY), None)
    return {
        "by_agent": by_agent,
        "n": len(by_agent),
        "glm_rating": by_agent.get(GLM_KEY),
        "glm_rank": glm_rank,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=0.3)
    args = parser.parse_args()

    glm_responses = load_glm_seat_responses()
    if not glm_responses:
        print("FATAL: no GLM seat responses", file=sys.stderr); sys.exit(1)

    done: set[str] = set()
    rows: list[dict] = []
    if JSONL_PATH.exists():
        with JSONL_PATH.open() as f:
            for line in f:
                try:
                    r = json.loads(line); done.add(r["council_id"]); rows.append(r)
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"[resume] {len(done)} already done")

    todo = [c for c in glm_responses if c not in done][: args.limit]
    print(f"[init] {len(todo)} councils × {len(RATERS)} raters ({', '.join(RATERS)})")

    with JSONL_PATH.open("a") as f:
        for i, cid in enumerate(todo, 1):
            question, incumbents = load_incumbents(cid)
            if not question or len(incumbents) < 2:
                print(f"[{i}/{len(todo)}] {cid} — skip"); continue
            pairs = incumbents + [(GLM_KEY, glm_responses[cid])]
            prompt, label_to_key = build_prompt(question, pairs, salt=f"quality:{cid}")
            t0 = time.time()
            results = await asyncio.gather(*[rate_one(r, prompt, label_to_key) for r in RATERS])
            panel = dict(zip(RATERS, results))
            rows.append({"council_id": cid, "n_candidates": len(pairs), "panel": panel,
                         "elapsed_s": round(time.time() - t0, 1)})
            f.write(json.dumps(rows[-1]) + "\n"); f.flush()
            ranks = {r: panel[r].get("glm_rank") for r in RATERS}
            print(f"[{i}/{len(todo)}] {cid} — GLM ranks {ranks} of {len(pairs)} ({rows[-1]['elapsed_s']:.0f}s)")
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    print(f"\n[done] wrote {JSONL_PATH}")
    write_summary(rows)
    print(f"[done] wrote {SUMMARY_PATH}")


def write_summary(rows: list[dict]):
    valid = [r for r in rows if r.get("panel")]
    if not valid:
        SUMMARY_PATH.write_text("# Panel Quality Blind-Rate\n\nNo valid rows.\n"); return

    def per_rater(rater):
        n = acc = top1 = 0
        ranks = []
        glm_dims = {d: [] for d in DIMS}
        inc_dims = {d: [] for d in DIMS}
        for r in valid:
            p = r["panel"].get(rater, {})
            ba = p.get("by_agent")
            gr = p.get("glm_rating")
            if not ba or not gr:
                continue
            n += 1
            if gr.get("score") == 1:
                acc += 1
            if p.get("glm_rank") == 1:
                top1 += 1
            if p.get("glm_rank"):
                ranks.append(p["glm_rank"])
            for a, rt in ba.items():
                sink = glm_dims if a == GLM_KEY else inc_dims
                for d in DIMS:
                    v = rt.get(d)
                    if isinstance(v, (int, float)):
                        sink[d].append(float(v))
        mean = lambda xs: (sum(xs) / len(xs)) if xs else float("nan")
        return {
            "n": n, "acc": acc, "top1": top1,
            "mean_rank": mean(ranks),
            "glm": {d: mean(glm_dims[d]) for d in DIMS},
            "inc": {d: mean(inc_dims[d]) for d in DIMS},
        }

    stats = {r: per_rater(r) for r in RATERS}

    # consensus: per council, did GLM rank in the bottom half for ALL raters that scored it?
    bottom_all = 0
    counted = 0
    for r in valid:
        rk = []
        for rater in RATERS:
            p = r["panel"].get(rater, {})
            if p.get("glm_rank") and p.get("n"):
                rk.append((p["glm_rank"], p["n"]))
        if len(rk) == len(RATERS):
            counted += 1
            if all(rank > n / 2 for rank, n in rk):
                bottom_all += 1

    lines = [
        "# Panel Quality Blind-Rate — GLM-5.2 (opencode) vs real seats",
        "",
        f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Councils**: {len(valid)} · raters: {', '.join(RATERS)} (all blind, identical anonymized layout)",
        "",
        "## GLM-5.2 per rater",
        "",
        "| Rater | n | Accept (+1) | Top-1 | Mean rank | GLM ground/help/corr | Incumbents ground/help/corr |",
        "|---|---|---|---|---|---|---|",
    ]
    for rater in RATERS:
        s = stats[rater]
        if not s["n"]:
            lines.append(f"| {rater} | 0 | — | — | — | — | — |")
            continue
        g = s["glm"]; ic = s["inc"]
        lines.append(
            f"| {rater} | {s['n']} | {s['acc']}/{s['n']} = {100*s['acc']/s['n']:.0f}% | "
            f"{s['top1']}/{s['n']} = {100*s['top1']/s['n']:.0f}% | {s['mean_rank']:.2f} | "
            f"{g['groundedness']:.2f}/{g['helpfulness']:.2f}/{g['correctness']:.2f} | "
            f"{ic['groundedness']:.2f}/{ic['helpfulness']:.2f}/{ic['correctness']:.2f} |"
        )
    lines += [
        "",
        "## Consensus",
        "",
        f"- GLM ranked in the **bottom half by ALL {len(RATERS)} raters**: {bottom_all}/{counted} councils.",
        "",
        "## Reading",
        "",
        "If all three raters put GLM near-bottom (low accept, ~0% top-1, mean rank toward N, dims",
        "below incumbents), the weak-seat verdict is robust and not a gpt-5.5 artifact. If raters",
        "diverge (e.g. gemini rates GLM high while gpt-5.5 rates it low), the original signal was",
        "rater taste, not a real quality gap — investigate further before rejecting.",
    ]
    SUMMARY_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
