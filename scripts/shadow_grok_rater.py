"""Shadow-mode replay of the blind orchestrator-rater through Grok CLI.

Historical councils already have agent_scores recorded by the Claude orchestrator
that called rate_council (rater='claude_blind'). This script re-applies the same
anonymization (deterministic salt='blind:{council_id}'), asks grok-build to rate
the same letters on the same dimensions, then compares per-agent.

Output:
  scripts/shadow_results/rater_replay.jsonl   — one line per council
  scripts/shadow_results/rater_summary.md     — Spearman, top-1 agreement, etc.

Usage:
  python scripts/shadow_grok_rater.py [--limit N] [--sleep 0.5]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

OWLEX_DB = Path(os.path.expanduser("~/.owlex/owlex.db"))
RESULTS_DIR = Path(__file__).parent / "shadow_results"
JSONL_PATH = RESULTS_DIR / "rater_replay.jsonl"
SUMMARY_PATH = RESULTS_DIR / "rater_summary.md"

GROK_MODEL = os.getenv("OWLEX_GROK_MODEL", "grok-build")
GROK_TIMEOUT = int(os.getenv("OWLEX_GROK_TIMEOUT", "180"))

LABELS = "ABCDEFGHIJKLMNOP"
COUNCIL_SYSTEM_PREFIX = "IMPORTANT: This is a council deliberation."
PROJECT_CONTEXT_MARKER = "PROJECT CONTEXT:"


def extract_question(prompt_text: str) -> str:
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
    return text.strip()[:1500]


BLIND_RATE_PROMPT = """\
You are a senior software engineering reviewer evaluating multiple AI advisors' answers to the same question.
The advisors are anonymized — you only see letter labels (Response A, B, C, ...).
Rate each response based on its content alone — be strict and discriminating.

ORIGINAL QUESTION:
{question}

{responses}

For EACH letter present above, return a rating with these fields:
- score: -1 (rejected/poor) or +1 (accepted/good)
- groundedness: 1-5 (does it reference real facts, code, sound reasoning?)
- helpfulness: 1-5 (does it actually answer the question with actionable detail?)
- correctness: 1-5 (is the analysis technically right?)
- reason: one sentence

Respond with ONLY a JSON object mapping letters to ratings, e.g.:
{{"A": {{"score": 1, "groundedness": 4, "helpfulness": 5, "correctness": 4, "reason": "..."}}, "B": {{"score": -1, "groundedness": 1, "helpfulness": 1, "correctness": 1, "reason": "Empty response"}}}}

Rate only these letters: {letters}
"""


def assign_labels_with_salt(pairs: list[tuple[str, str]], salt: str) -> tuple[dict[str, str], dict[str, str]]:
    """Reproduce owlex.anonymize.assign_labels deterministic shuffle."""
    rng = random.Random(salt)
    items = list(pairs)
    rng.shuffle(items)
    by_label: dict[str, str] = {}
    label_to_key: dict[str, str] = {}
    for i, (agent, content) in enumerate(items):
        if i >= len(LABELS):
            break
        by_label[LABELS[i]] = content
        label_to_key[LABELS[i]] = agent
    return by_label, label_to_key


def load_councils(limit: int) -> list[dict]:
    uri = f"file:{OWLEX_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT DISTINCT co.council_id, co.completed_at
            FROM council_outcomes co
            JOIN agent_scores s ON s.council_id = co.council_id AND s.rater='claude_blind'
            ORDER BY co.completed_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        cids = [r["council_id"] for r in cur.fetchall()]
        councils = []
        for cid in cids:
            cur = conn.execute(
                """
                SELECT agent, prompt_text, result_text, status
                FROM calls
                WHERE council_id=? AND round=1 AND status='completed'
                  AND result_text IS NOT NULL AND length(result_text) > 0
                  AND prompt_text IS NOT NULL AND length(prompt_text) > 100
                ORDER BY agent
                """,
                (cid,),
            )
            r1_calls = [dict(r) for r in cur.fetchall()]
            cur = conn.execute(
                """
                SELECT agent, score, dimensions, reason
                FROM agent_scores
                WHERE council_id=? AND rater='claude_blind'
                ORDER BY agent
                """,
                (cid,),
            )
            existing = [dict(r) for r in cur.fetchall()]
            councils.append({"council_id": cid, "r1_calls": r1_calls, "existing_ratings": existing})
        return councils
    finally:
        conn.close()


def build_prompt_and_mapping(council: dict) -> tuple[str | None, dict[str, str], str]:
    calls = council["r1_calls"]
    if len(calls) < 2:
        return None, {}, ""
    question = extract_question(calls[0]["prompt_text"])
    if not question:
        return None, {}, ""
    pairs = [(c["agent"], c["result_text"]) for c in calls]
    salt = f"blind:{council['council_id']}"
    by_label, label_to_agent = assign_labels_with_salt(pairs, salt)
    parts = []
    for letter, body in by_label.items():
        truncated = body[:3000] if len(body) > 3000 else body
        parts.append(f"RESPONSE {letter}:\n{truncated}")
    letters_str = ", ".join(by_label.keys())
    prompt = BLIND_RATE_PROMPT.format(
        question=question,
        responses="\n\n".join(parts),
        letters=letters_str,
    )
    return prompt, label_to_agent, question


async def call_grok(prompt: str) -> tuple[str, str | None]:
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
    return stdout.decode(errors="replace").strip(), None


def parse_grok_ratings(raw: str, expected_letters: list[str]) -> tuple[dict[str, dict] | None, str | None]:
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        last_brace = raw.rfind("}")
        try:
            outer = json.loads(raw[: last_brace + 1])
        except (json.JSONDecodeError, ValueError):
            return None, "outer JSON parse failed"
    text = outer.get("text", "")
    candidates = []
    for match in re.finditer(r"\{[^{}]*\"[A-P]\"[^{}]*\{[^{}]*\}.*?\}", text, re.DOTALL):
        candidates.append(match.group(0))
    if "```" in text:
        for chunk in text.split("```"):
            if chunk.strip().startswith("json"):
                candidates.append(chunk.strip()[4:].strip())
            elif chunk.strip().startswith("{"):
                candidates.append(chunk.strip())
    candidates.append(text.strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and any(k in parsed for k in expected_letters):
                cleaned = {}
                for k, v in parsed.items():
                    if k in expected_letters and isinstance(v, dict):
                        cleaned[k] = v
                if cleaned:
                    return cleaned, None
        except (json.JSONDecodeError, ValueError):
            continue
    return None, f"could not parse ratings; head: {text[:300]}"


def spearman(xs: list[float], ys: list[float]) -> float:
    if not xs or len(xs) != len(ys) or len(xs) < 2:
        return float("nan")
    def rank(vals):
        sorted_pairs = sorted(enumerate(vals), key=lambda p: p[1])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(sorted_pairs):
            j = i
            while j + 1 < len(sorted_pairs) and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
                j += 1
            avg_rank = (i + j + 2) / 2.0
            for k in range(i, j + 1):
                ranks[sorted_pairs[k][0]] = avg_rank
            i = j + 1
        return ranks
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = (sum((a - mx) ** 2 for a in rx)) ** 0.5
    dy = (sum((b - my) ** 2 for b in ry)) ** 0.5
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()

    if not OWLEX_DB.exists():
        print(f"FATAL: {OWLEX_DB} not found", file=sys.stderr)
        sys.exit(1)

    already_done: set[str] = set()
    rows: list[dict] = []
    if JSONL_PATH.exists():
        with JSONL_PATH.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                    already_done.add(row["council_id"])
                    rows.append(row)
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"[resume] {len(already_done)} councils already processed in {JSONL_PATH.name}")

    print(f"[init] loading up to {args.limit} councils with blind ratings")
    councils = load_councils(args.limit)
    councils = [c for c in councils if c["council_id"] not in already_done]
    print(f"[init] {len(councils)} councils remaining to process")

    with JSONL_PATH.open("a") as f:
        for i, council in enumerate(councils, 1):
            cid = council["council_id"]
            prompt, label_to_agent, question = build_prompt_and_mapping(council)
            if not prompt:
                print(f"[{i}/{len(councils)}] {cid} — skip (insufficient R1)")
                continue
            existing_by_agent = {r["agent"]: r for r in council["existing_ratings"]}

            t0 = time.time()
            raw, err = await call_grok(prompt)
            elapsed = time.time() - t0
            if err:
                print(f"[{i}/{len(councils)}] {cid} — grok error: {err} ({elapsed:.1f}s)")
                rows.append({"council_id": cid, "error": err, "elapsed_s": elapsed})
                f.write(json.dumps(rows[-1]) + "\n"); f.flush()
                continue

            ratings, parse_err = parse_grok_ratings(raw, list(label_to_agent.keys()))
            if not ratings:
                print(f"[{i}/{len(councils)}] {cid} — parse fail ({elapsed:.1f}s)")
                rows.append({"council_id": cid, "parse_error": parse_err, "elapsed_s": elapsed})
                f.write(json.dumps(rows[-1]) + "\n"); f.flush()
                continue

            per_agent = []
            for letter, rating in ratings.items():
                agent = label_to_agent.get(letter)
                if not agent:
                    continue
                existing = existing_by_agent.get(agent)
                try:
                    grok_score = int(rating.get("score", 0))
                except (TypeError, ValueError):
                    grok_score = 0
                grok_dims = {k: rating.get(k) for k in ("groundedness", "helpfulness", "correctness")}
                existing_dims = {}
                if existing and existing.get("dimensions"):
                    try:
                        existing_dims = json.loads(existing["dimensions"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                per_agent.append({
                    "letter": letter,
                    "agent": agent,
                    "grok_score": grok_score,
                    "grok_dims": grok_dims,
                    "grok_reason": rating.get("reason", ""),
                    "existing_score": existing["score"] if existing else None,
                    "existing_dims": existing_dims,
                    "existing_reason": existing.get("reason") if existing else None,
                })

            rows.append({
                "council_id": cid,
                "elapsed_s": round(elapsed, 2),
                "per_agent": per_agent,
            })
            f.write(json.dumps(rows[-1]) + "\n"); f.flush()
            print(f"[{i}/{len(councils)}] {cid} — rated {len(per_agent)} agents ({elapsed:.1f}s)")
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    print(f"\n[done] wrote {JSONL_PATH}")
    write_summary(rows)
    print(f"[done] wrote {SUMMARY_PATH}")


def write_summary(rows: list[dict]):
    pairs_score = []
    pairs_ground = []
    pairs_helpful = []
    pairs_correct = []
    flips = 0
    total_paired = 0
    top1_agreements = 0
    top1_total = 0

    for row in rows:
        per_agent = row.get("per_agent")
        if not per_agent:
            continue
        valid = [p for p in per_agent if p.get("existing_score") is not None]
        for p in valid:
            pairs_score.append((p["existing_score"], p["grok_score"]))
            total_paired += 1
            if p["existing_score"] != p["grok_score"]:
                flips += 1
            for key, sink in (
                ("groundedness", pairs_ground),
                ("helpfulness", pairs_helpful),
                ("correctness", pairs_correct),
            ):
                ev = p["existing_dims"].get(key) if p.get("existing_dims") else None
                gv = p["grok_dims"].get(key) if p.get("grok_dims") else None
                if isinstance(ev, (int, float)) and isinstance(gv, (int, float)):
                    sink.append((float(ev), float(gv)))
        if len(valid) >= 2:
            top1_total += 1
            existing_winner = max(valid, key=lambda p: (
                p["existing_score"],
                p["existing_dims"].get("correctness", 0) if p.get("existing_dims") else 0,
                p["existing_dims"].get("helpfulness", 0) if p.get("existing_dims") else 0,
            ))["agent"]
            grok_winner = max(valid, key=lambda p: (
                p["grok_score"],
                p["grok_dims"].get("correctness", 0) if p.get("grok_dims") else 0,
                p["grok_dims"].get("helpfulness", 0) if p.get("grok_dims") else 0,
            ))["agent"]
            if existing_winner == grok_winner:
                top1_agreements += 1

    def corr(pairs):
        if not pairs:
            return float("nan")
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        return spearman(xs, ys)

    agree_pct = ((total_paired - flips) / total_paired * 100) if total_paired else 0
    top1_pct = (top1_agreements / top1_total * 100) if top1_total else 0

    md = f"""# Blind-Rater Shadow Replay — grok-build vs existing claude_blind

**Model**: `{GROK_MODEL}` via Grok CLI
**Generated**: {time.strftime("%Y-%m-%d %H:%M:%S")}

## Volume

| Metric | Count |
|---|---|
| Councils replayed | {len(rows)} |
| Total per-agent comparisons | {total_paired} |

## Score agreement (-1 / +1)

| Metric | Value |
|---|---|
| Exact match | {total_paired - flips}/{total_paired} = {agree_pct:.1f}% |
| Disagreements (flips) | {flips} |

## Dimension correlation (Spearman ρ)

| Dimension | ρ | N pairs |
|---|---|---|
| groundedness | {corr(pairs_ground):.3f} | {len(pairs_ground)} |
| helpfulness  | {corr(pairs_helpful):.3f} | {len(pairs_helpful)} |
| correctness  | {corr(pairs_correct):.3f} | {len(pairs_correct)} |

## Top-1 winner agreement (who got the best rating in each council)

| Metric | Value |
|---|---|
| Same winner | {top1_agreements}/{top1_total} = {top1_pct:.1f}% |

## Interpretation guide

| Spearman ρ | Verdict |
|---|---|
| > 0.7 | Grok is a strong independent rater — high correlation with existing rater |
| 0.4–0.7 | Moderate correlation — useful as a secondary signal, not replacement |
| < 0.4 | Diverges — different judgment criteria, low ensemble value |

Top-1 winner agreement >70% means Grok would pick the same "best" answer most of the time —
useful as a sanity check on the existing rater.

Note: the existing rater is Claude orchestrator (varies per session — different sessions used
different Claude versions). This compares two independent judges, not "ground truth vs candidate."
"""
    SUMMARY_PATH.write_text(md)


if __name__ == "__main__":
    asyncio.run(main())
