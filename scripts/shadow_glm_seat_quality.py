"""Quality blind-rate of GLM-5.2's HARNESSED seat answers vs the real seats.

The structural seat metrics (code-blocks/file-refs) are a biased proxy — they
undercount GLM's grounding (it references code inline, not in fenced blocks). This
experiment skips proxies and asks the actual question: is GLM's R1 answer GOOD?

For each council where we have a GLM-via-opencode R1 response
(seat_r1_responses_glm_opencode.jsonl), we pull the REAL seats' R1 answers from
~/.owlex/owlex.db, add GLM's answer as one more anonymized option, shuffle, and ask
gpt-5.5 (via codex CLI — the owlex agreement judge's model) to blind-rate them all.
Then we see where GLM's answer lands (accept rate, top-1 rate, dimension means, rank).

Read-only against owlex.db. Output:
  scripts/shadow_results/seat_quality_glm.jsonl
  scripts/shadow_results/seat_quality_glm.md

Usage:
  python scripts/shadow_glm_seat_quality.py [--limit N] [--sleep 0.5]
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
import tempfile
import time
from pathlib import Path

OWLEX_DB = Path(os.path.expanduser("~/.owlex/owlex.db"))
RESULTS_DIR = Path(__file__).parent / "shadow_results"
GLM_SEAT_JSONL = RESULTS_DIR / "seat_r1_responses_glm_opencode.jsonl"
JSONL_PATH = RESULTS_DIR / "seat_quality_glm.jsonl"
SUMMARY_PATH = RESULTS_DIR / "seat_quality_glm.md"

CODEX_MODEL = os.getenv("OWLEX_AGREEMENT_MODEL", "gpt-5.5")
CODEX_TIMEOUT = int(os.getenv("OWLEX_QUALITY_TIMEOUT", "180"))
GLM_KEY = "glm_oc"  # the candidate (GLM-5.2 via opencode), rated blind alongside real seats

LABELS = "ABCDEFGHIJKLMNOP"
COUNCIL_SYSTEM_PREFIX = "IMPORTANT: This is a council deliberation."
PROJECT_CONTEXT_MARKER = "PROJECT CONTEXT:"

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


def assign_labels_with_salt(pairs: list[tuple[str, str]], salt: str) -> dict[str, str]:
    """Deterministic shuffle -> {letter: agent_key}."""
    rng = random.Random(salt)
    items = list(pairs)
    rng.shuffle(items)
    label_to_key: dict[str, str] = {}
    for i, (agent, _content) in enumerate(items):
        if i >= len(LABELS):
            break
        label_to_key[LABELS[i]] = agent
    return label_to_key


def load_glm_seat_responses() -> dict[str, str]:
    out: dict[str, str] = {}
    if not GLM_SEAT_JSONL.exists():
        return out
    with GLM_SEAT_JSONL.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("glm_metrics") and r.get("glm_response"):
                out[r["council_id"]] = r["glm_response"]
    return out


def load_incumbents(cid: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (question, [(agent, full_response), ...]) for a council, excluding 'test'."""
    uri = f"file:{OWLEX_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT agent, prompt_text, result_text
            FROM calls
            WHERE council_id=? AND round=1 AND status='completed'
              AND result_text IS NOT NULL AND length(result_text) > 0
              AND prompt_text IS NOT NULL AND length(prompt_text) > 100
              AND agent <> 'test'
            ORDER BY agent
            """,
            (cid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    if not rows:
        return "", []
    question = extract_question(rows[0]["prompt_text"])
    return question, [(r["agent"], r["result_text"]) for r in rows]


def build_prompt(question: str, pairs: list[tuple[str, str]], salt: str) -> tuple[str, dict[str, str]]:
    label_to_key = assign_labels_with_salt(pairs, salt)
    key_to_text = {a: t for a, t in pairs}
    parts = []
    for letter in label_to_key:  # insertion order = label order A,B,...
        body = key_to_text[label_to_key[letter]]
        parts.append(f"RESPONSE {letter}:\n{body[:3000]}")
    letters = ", ".join(label_to_key.keys())
    prompt = BLIND_RATE_PROMPT.format(question=question, responses="\n\n".join(parts), letters=letters)
    return prompt, label_to_key


async def call_codex(prompt: str) -> tuple[str, str | None]:
    """Rate via gpt-5.5 through codex CLI (the owlex judge's runner). Prompt on stdin."""
    with tempfile.TemporaryDirectory() as td:
        proc = await asyncio.create_subprocess_exec(
            "codex", "exec", "--skip-git-repo-check", "--model", CODEX_MODEL,
            "--cd", td, "--dangerously-bypass-approvals-and-sandbox", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=CODEX_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "", f"timeout after {CODEX_TIMEOUT}s"
    if proc.returncode != 0:
        return "", f"codex exit {proc.returncode}: {stderr.decode(errors='replace')[:200]}"
    return stdout.decode(errors="replace"), None


def parse_ratings(raw: str, expected: list[str]) -> dict[str, dict] | None:
    """Extract the JSON object mapping letters->ratings from codex stdout."""
    candidates: list[str] = []
    if "```" in raw:
        for chunk in raw.split("```"):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                candidates.append(c)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    candidates.append(raw.strip())
    for c in candidates:
        try:
            parsed = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and any(k in parsed for k in expected):
            cleaned = {k: v for k, v in parsed.items() if k in expected and isinstance(v, dict)}
            if cleaned:
                return cleaned
    return None


def _rank_key(rating: dict) -> tuple:
    def g(k):
        v = rating.get(k)
        return v if isinstance(v, (int, float)) else 0
    return (g("score"), g("correctness"), g("helpfulness"), g("groundedness"))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()

    if not OWLEX_DB.exists():
        print(f"FATAL: {OWLEX_DB} not found", file=sys.stderr)
        sys.exit(1)

    glm_responses = load_glm_seat_responses()
    if not glm_responses:
        print(f"FATAL: no GLM seat responses in {GLM_SEAT_JSONL}", file=sys.stderr)
        sys.exit(1)

    done: set[str] = set()
    rows: list[dict] = []
    if JSONL_PATH.exists():
        with JSONL_PATH.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add(r["council_id"])
                    rows.append(r)
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"[resume] {len(done)} councils already rated")

    todo = [c for c in glm_responses if c not in done][: args.limit]
    print(f"[init] {len(todo)} councils to rate (rater={CODEX_MODEL})")

    with JSONL_PATH.open("a") as f:
        for i, cid in enumerate(todo, 1):
            question, incumbents = load_incumbents(cid)
            if not question or len(incumbents) < 2:
                print(f"[{i}/{len(todo)}] {cid} — skip (insufficient incumbents)")
                continue
            pairs = incumbents + [(GLM_KEY, glm_responses[cid])]
            prompt, label_to_key = build_prompt(question, pairs, salt=f"quality:{cid}")
            t0 = time.time()
            raw, err = await call_codex(prompt)
            elapsed = time.time() - t0
            if err:
                print(f"[{i}/{len(todo)}] {cid} — codex error: {err} ({elapsed:.0f}s)")
                rows.append({"council_id": cid, "error": err})
                f.write(json.dumps(rows[-1]) + "\n"); f.flush()
                continue
            ratings = parse_ratings(raw, list(label_to_key.keys()))
            if not ratings:
                print(f"[{i}/{len(todo)}] {cid} — parse fail ({elapsed:.0f}s)")
                rows.append({"council_id": cid, "parse_error": raw[:300]})
                f.write(json.dumps(rows[-1]) + "\n"); f.flush()
                continue
            by_agent = {}
            for letter, rating in ratings.items():
                agent = label_to_key.get(letter)
                if agent:
                    by_agent[agent] = rating
            glm_r = by_agent.get(GLM_KEY)
            # rank: 1 = best (GLM's position among all candidates by _rank_key desc)
            ranked = sorted(by_agent.items(), key=lambda kv: _rank_key(kv[1]), reverse=True)
            glm_rank = next((idx for idx, (a, _) in enumerate(ranked, 1) if a == GLM_KEY), None)
            rows.append({
                "council_id": cid,
                "n_candidates": len(by_agent),
                "glm_rating": glm_r,
                "glm_rank": glm_rank,
                "by_agent": by_agent,
                "elapsed_s": round(elapsed, 1),
            })
            f.write(json.dumps(rows[-1]) + "\n"); f.flush()
            sc = (glm_r or {}).get("score")
            print(f"[{i}/{len(todo)}] {cid} — GLM score={sc} rank={glm_rank}/{len(by_agent)} ({elapsed:.0f}s)")
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    print(f"\n[done] wrote {JSONL_PATH}")
    write_summary(rows)
    print(f"[done] wrote {SUMMARY_PATH}")


def write_summary(rows: list[dict]):
    valid = [r for r in rows if r.get("glm_rating")]
    if not valid:
        SUMMARY_PATH.write_text("# Seat Quality Blind-Rate\n\nNo valid rows.\n")
        return

    def dim_mean(agentsel, dim):
        vals = []
        for r in valid:
            for a, rt in r["by_agent"].items():
                pick = (a == GLM_KEY) if agentsel == "glm" else (a != GLM_KEY)
                v = rt.get(dim)
                if pick and isinstance(v, (int, float)):
                    vals.append(float(v))
        return sum(vals) / len(vals) if vals else float("nan")

    glm_accept = sum(1 for r in valid if (r["glm_rating"].get("score") == 1))
    glm_top1 = sum(1 for r in valid if r.get("glm_rank") == 1)
    # incumbent accept rate (baseline)
    inc_total = inc_accept = 0
    for r in valid:
        for a, rt in r["by_agent"].items():
            if a == GLM_KEY:
                continue
            inc_total += 1
            if rt.get("score") == 1:
                inc_accept += 1
    ranks = [r["glm_rank"] for r in valid if r.get("glm_rank")]
    mean_rank = sum(ranks) / len(ranks) if ranks else float("nan")
    mean_n = sum(r["n_candidates"] for r in valid) / len(valid)

    md = f"""# Seat Quality Blind-Rate — GLM-5.2 (opencode/max) vs real seats

**Rater**: `{CODEX_MODEL}` via codex CLI (the owlex agreement judge's model), blind.
**Generated**: {time.strftime("%Y-%m-%d %H:%M:%S")}
**Councils rated**: {len(valid)}  (mean {mean_n:.1f} candidates/council, incl. GLM)

GLM-5.2's harnessed R1 answer was dropped in anonymously alongside the real seats'
answers; the rater never knew which was which.

## Headline

| Metric | GLM-5.2 (opencode) | Incumbent seats (pooled) |
|---|---|---|
| Accept rate (+1) | {glm_accept}/{len(valid)} = {100*glm_accept/len(valid):.0f}% | {inc_accept}/{inc_total} = {100*inc_accept/max(1,inc_total):.0f}% |
| **Top-1 (best in council)** | {glm_top1}/{len(valid)} = {100*glm_top1/len(valid):.0f}% | (chance ≈ {100/mean_n:.0f}%) |
| Mean rank (1=best) | {mean_rank:.2f} of {mean_n:.1f} | — |

## Dimension means (1–5)

| Dimension | GLM-5.2 | Incumbents |
|---|---|---|
| groundedness | {dim_mean('glm','groundedness'):.2f} | {dim_mean('inc','groundedness'):.2f} |
| helpfulness  | {dim_mean('glm','helpfulness'):.2f} | {dim_mean('inc','helpfulness'):.2f} |
| correctness  | {dim_mean('glm','correctness'):.2f} | {dim_mean('inc','correctness'):.2f} |

## Reading

This bypasses the structural proxies. If GLM's accept rate and dimension means are
**at or above** the incumbent pool and top-1 rate beats chance, GLM-as-seat is a
quality-competitive seat (the "code-light" structural finding was a proxy artifact).
If GLM lands clearly below the pool, the structural concern reflected a real quality gap.
"""
    SUMMARY_PATH.write_text(md)


if __name__ == "__main__":
    asyncio.run(main())
