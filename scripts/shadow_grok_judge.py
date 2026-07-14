"""Shadow-mode replay of the agreement judge through Grok CLI.

Read-only experiment that re-runs the agreement-judge prompt against historical
council R1 responses, then compares grok-build's scores to the existing judge
(gpt-5.5 via codex CLI) recorded in council_outcomes.agreement_score.

Output:
  scripts/shadow_results/agreement_replay.jsonl  — one JSON line per council
  scripts/shadow_results/agreement_summary.md    — kappa, confusion matrix, etc.

Usage:
  python scripts/shadow_grok_judge.py [--limit N] [--threshold 3.5]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

OWLEX_DB = Path(os.path.expanduser("~/.owlex/owlex.db"))
RESULTS_DIR = Path(__file__).parent / "shadow_results"
JSONL_PATH = RESULTS_DIR / "agreement_replay.jsonl"
SUMMARY_PATH = RESULTS_DIR / "agreement_summary.md"

GROK_MODEL = os.getenv("OWLEX_GROK_MODEL", "grok-build")
GROK_TIMEOUT = int(os.getenv("OWLEX_GROK_TIMEOUT", "120"))

COUNCIL_SYSTEM_PREFIX = "IMPORTANT: This is a council deliberation."
PROJECT_CONTEXT_MARKER = "PROJECT CONTEXT:"

AGREEMENT_PROMPT = """\
You are judging whether multiple AI advisors agree on a software engineering question.

QUESTION:
{question}

{responses}

Score the overall agreement between these responses on a scale of 1-5:
1 = Fundamental disagreement (contradictory recommendations)
2 = Significant differences (different approaches, some conflict)
3 = Partial agreement (same direction but different emphasis/details)
4 = Strong agreement (same recommendations with minor variation)
5 = Full consensus (essentially the same answer)

Respond with ONLY a JSON object:
{{"score": <1-5>, "reason": "<one sentence>"}}
"""


def extract_question(prompt_text: str) -> str:
    """Strip COUNCIL_SYSTEM_INSTRUCTION + optional PROJECT CONTEXT to recover question."""
    if not prompt_text:
        return ""
    text = prompt_text
    if text.startswith(COUNCIL_SYSTEM_PREFIX):
        idx = text.find("- Provide your analysis")
        if idx >= 0:
            text = text[idx:].split("\n", 1)[1] if "\n" in text[idx:] else text[idx:]
            text = text.lstrip("\n")
    if text.startswith(PROJECT_CONTEXT_MARKER):
        idx = text.find("\n\n")
        if idx >= 0:
            text = text[idx + 2 :]
    return text.strip()[:500]


def load_councils(limit: int, with_blind_only: bool = False) -> list[dict]:
    uri = f"file:{OWLEX_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        where = ""
        if with_blind_only:
            where = "WHERE co.council_id IN (SELECT council_id FROM agent_scores WHERE rater='claude_blind')"
        cur = conn.execute(
            f"""
            SELECT co.council_id, co.agreement_score, co.agreement_reason,
                   co.deliberation, co.completed_at
            FROM council_outcomes co
            {where}
            ORDER BY co.completed_at DESC
            LIMIT ?
            """,
            (1000,),
        )
        councils = [dict(r) for r in cur.fetchall()]
        for c in councils:
            cur = conn.execute(
                """
                SELECT agent, prompt_text, result_text, status
                FROM calls
                WHERE council_id=? AND round=1 AND status='completed'
                  AND result_text IS NOT NULL AND length(result_text) > 0
                  AND prompt_text IS NOT NULL AND length(prompt_text) > 100
                ORDER BY agent
                """,
                (c["council_id"],),
            )
            c["r1_calls"] = [dict(r) for r in cur.fetchall()]
        qualifying = [c for c in councils if len(c["r1_calls"]) >= 2]
        return qualifying[:limit]
    finally:
        conn.close()


def build_prompt(council: dict) -> str | None:
    calls = council["r1_calls"]
    if len(calls) < 2:
        return None
    question = extract_question(calls[0]["prompt_text"])
    if not question:
        return None
    parts = []
    labels = "ABCDEFGHIJKLMNOP"
    for i, call in enumerate(calls):
        if i >= len(labels):
            break
        body = call["result_text"]
        if len(body) > 2000:
            body = body[:2000]
        parts.append(f"RESPONSE {labels[i]}:\n{body}")
    return AGREEMENT_PROMPT.format(question=question, responses="\n\n".join(parts))


async def call_grok(prompt: str) -> tuple[str, str | None]:
    """Run grok CLI in single-prompt mode. Returns (text, error_or_None)."""
    proc = await asyncio.create_subprocess_exec(
        "grok", "-p", prompt,
        "--output-format", "json",
        "--always-approve",
        "--model", GROK_MODEL,
        "--disable-web-search",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=GROK_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", f"timeout after {GROK_TIMEOUT}s"
    if proc.returncode != 0:
        return "", f"grok exit {proc.returncode}: {stderr.decode(errors='replace')[:200]}"
    out = stdout.decode(errors="replace").strip()
    return out, None


def parse_grok_response(raw: str) -> tuple[float | None, str, str | None]:
    """Parse grok --output-format json wrapper, then inner agreement JSON."""
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError as e:
        last_brace = raw.rfind("}")
        if last_brace > 0:
            try:
                outer = json.loads(raw[: last_brace + 1])
            except json.JSONDecodeError:
                return None, "", f"outer parse fail: {e}"
        else:
            return None, "", f"outer parse fail: {e}"
    text = outer.get("text", "") or ""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("{") and "score" in line and "<" not in line:
            try:
                data = json.loads(line)
                score = float(data.get("score", 0))
                return max(1.0, min(5.0, score)), str(data.get("reason", "")), None
            except (json.JSONDecodeError, ValueError):
                continue
    m = re.search(r'"score"\s*:\s*([0-9.]+)', text)
    if m:
        try:
            return float(m.group(1)), "regex-extracted", None
        except ValueError:
            pass
    return None, "", f"no score in text: {text[:200]}"


def cohens_kappa(rater_a: list[int], rater_b: list[int]) -> float:
    if not rater_a or len(rater_a) != len(rater_b):
        return float("nan")
    n = len(rater_a)
    categories = sorted(set(rater_a) | set(rater_b))
    table = {(a, b): 0 for a in categories for b in categories}
    for a, b in zip(rater_a, rater_b):
        table[(a, b)] += 1
    po = sum(table[(c, c)] for c in categories) / n
    marg_a = {c: sum(table[(c, b)] for b in categories) / n for c in categories}
    marg_b = {c: sum(table[(a, c)] for a in categories) / n for c in categories}
    pe = sum(marg_a[c] * marg_b[c] for c in categories)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else float("nan")
    return (po - pe) / (1 - pe)


def pearson(xs: list[float], ys: list[float]) -> float:
    if not xs or len(xs) != len(ys):
        return float("nan")
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    dy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--threshold", type=float, default=3.5,
                        help="agreement score < threshold triggers R2 in production logic")
    parser.add_argument("--sleep", type=float, default=0.5,
                        help="sleep between grok calls (rate-limit friendly)")
    args = parser.parse_args()

    if not OWLEX_DB.exists():
        print(f"FATAL: {OWLEX_DB} not found", file=sys.stderr)
        sys.exit(1)

    print(f"[init] loading up to {args.limit} councils from {OWLEX_DB}")
    councils = load_councils(args.limit)
    print(f"[init] loaded {len(councils)} councils ({sum(1 for c in councils if len(c['r1_calls']) >= 2)} with >=2 R1 responses)")

    results = []
    with JSONL_PATH.open("w") as f:
        for i, council in enumerate(councils, 1):
            cid = council["council_id"]
            if len(council["r1_calls"]) < 2:
                print(f"[{i}/{len(councils)}] {cid} — skip (only {len(council['r1_calls'])} R1)")
                continue
            prompt = build_prompt(council)
            if not prompt:
                print(f"[{i}/{len(councils)}] {cid} — skip (no question extracted)")
                continue

            t0 = time.time()
            raw, err = await call_grok(prompt)
            elapsed = time.time() - t0
            if err:
                print(f"[{i}/{len(councils)}] {cid} — grok error: {err} ({elapsed:.1f}s)")
                row = {
                    "council_id": cid,
                    "original_score": council["agreement_score"],
                    "original_reason": council["agreement_reason"],
                    "grok_score": None,
                    "grok_reason": "",
                    "grok_error": err,
                    "elapsed_s": elapsed,
                    "n_responses": len(council["r1_calls"]),
                }
                f.write(json.dumps(row) + "\n")
                f.flush()
                results.append(row)
                continue

            grok_score, grok_reason, parse_err = parse_grok_response(raw)
            row = {
                "council_id": cid,
                "original_score": council["agreement_score"],
                "original_reason": council["agreement_reason"],
                "original_deliberation": council["deliberation"],
                "grok_score": grok_score,
                "grok_reason": grok_reason,
                "grok_error": parse_err,
                "elapsed_s": round(elapsed, 2),
                "n_responses": len(council["r1_calls"]),
            }
            f.write(json.dumps(row) + "\n")
            f.flush()
            results.append(row)
            orig = council["agreement_score"]
            orig_s = f"{orig:.1f}" if isinstance(orig, (int, float)) else str(orig)
            grok_s = f"{grok_score}" if grok_score is not None else "ERR"
            print(f"[{i}/{len(councils)}] {cid} — orig={orig_s} grok={grok_s} ({elapsed:.1f}s)")
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    print(f"\n[done] wrote {JSONL_PATH}")
    write_summary(results, args.threshold)
    print(f"[done] wrote {SUMMARY_PATH}")


def write_summary(rows: list[dict], threshold: float):
    valid = [r for r in rows if r.get("grok_score") is not None and r.get("original_score") is not None]
    failed = [r for r in rows if r.get("grok_error")]
    judge_failed_orig = [r for r in rows if r.get("original_reason") in ("judge failed", "judge timeout", "judge error")]

    orig_scores = [r["original_score"] for r in valid]
    grok_scores = [r["grok_score"] for r in valid]
    orig_r2 = [1 if r["original_score"] < threshold else 0 for r in valid]
    grok_r2 = [1 if r["grok_score"] < threshold else 0 for r in valid]

    kappa = cohens_kappa(orig_r2, grok_r2)
    corr = pearson(orig_scores, grok_scores)

    tp = sum(1 for o, g in zip(orig_r2, grok_r2) if o == 1 and g == 1)
    tn = sum(1 for o, g in zip(orig_r2, grok_r2) if o == 0 and g == 0)
    fp = sum(1 for o, g in zip(orig_r2, grok_r2) if o == 0 and g == 1)
    fn = sum(1 for o, g in zip(orig_r2, grok_r2) if o == 1 and g == 0)

    mean_orig = sum(orig_scores) / len(orig_scores) if orig_scores else float("nan")
    mean_grok = sum(grok_scores) / len(grok_scores) if grok_scores else float("nan")

    # Grok-saves: cases where original judge failed but grok produced a valid score
    grok_saves = [r for r in valid if r.get("original_reason") in ("judge failed", "judge timeout", "judge error")]

    md = f"""# Agreement-Judge Shadow Replay — grok-build vs gpt-5.5

**Model**: `{GROK_MODEL}` via Grok CLI
**Generated**: {time.strftime("%Y-%m-%d %H:%M:%S")}
**Threshold for R2-needed**: agreement_score < {threshold}

## Volume

| Metric | Count |
|---|---|
| Total councils replayed | {len(rows)} |
| Successful grok calls (parsed) | {len(valid)} |
| Failed grok calls / parse errors | {len(failed)} |
| Original judge had failed (fallback to overlap) | {len(judge_failed_orig)} |
| **Grok saves** (original failed, grok produced score) | {len(grok_saves)} |

## Score correlation

| Metric | Value |
|---|---|
| Pearson correlation (raw 1-5 scores) | {corr:.3f} |
| Mean original score | {mean_orig:.2f} |
| Mean grok score | {mean_grok:.2f} |

## R2-needed decision agreement (binary, threshold={threshold})

| Metric | Value |
|---|---|
| Cohen's kappa | {kappa:.3f} |
| Agreement % | {(tp + tn) / len(valid) * 100 if valid else 0:.1f}% |

Confusion matrix (rows = orig judge, cols = grok):

|              | grok says R2 NOT needed | grok says R2 needed |
|---|---|---|
| orig R2 NOT needed | TN={tn} | FP={fp} |
| orig R2 needed     | FN={fn} | TP={tp} |

## Interpretation guide

| Kappa | Verdict |
|---|---|
| > 0.8 | Grok can REPLACE current judge (high agreement) |
| 0.6–0.8 | Grok can ENSEMBLE with current judge (cross-validation) |
| 0.4–0.6 | Grok is too divergent — not safe to substitute |
| < 0.4 | Grok diverges fundamentally — different judgment criteria |

Pearson correlation interpretation: > 0.7 strong, 0.4–0.7 moderate, < 0.4 weak.

## Grok-saves potential

Of {len(judge_failed_orig)} councils where the original judge failed, grok produced a valid score in {len(grok_saves)}.
If kappa is high, grok could serve as fallback when codex judge fails.
"""
    SUMMARY_PATH.write_text(md)


if __name__ == "__main__":
    asyncio.run(main())
