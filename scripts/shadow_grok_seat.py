"""Shadow-mode R1 generation for Grok-as-7th-seat experiment.

For each historical council, replay the question through Grok CLI as if Grok
were a 7th seat, then compare structural quality vs the 6 existing seats.

Output:
  scripts/shadow_results/seat_r1_responses.jsonl  — one line per council
  scripts/shadow_results/seat_r1_metrics.md       — structural comparison

Usage:
  python scripts/shadow_grok_seat.py [--limit N]
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
from collections import defaultdict
from pathlib import Path

OWLEX_DB = Path(os.path.expanduser("~/.owlex/owlex.db"))
RESULTS_DIR = Path(__file__).parent / "shadow_results"
JSONL_PATH = RESULTS_DIR / "seat_r1_responses.jsonl"
METRICS_PATH = RESULTS_DIR / "seat_r1_metrics.md"

GROK_MODEL = os.getenv("OWLEX_GROK_MODEL", "grok-build")
GROK_TIMEOUT = int(os.getenv("OWLEX_GROK_TIMEOUT", "300"))
GROK_EFFORT = os.getenv("OWLEX_GROK_EFFORT", "low")

COUNCIL_SYSTEM_PREFIX = "IMPORTANT: This is a council deliberation."


def load_councils(limit: int) -> list[dict]:
    """Pick councils with blind ratings (have ground truth from existing rater)
    and at least 3 seats with R1 responses (so the comparison is meaningful)."""
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
            (limit * 2,),
        )
        cids = [r["council_id"] for r in cur.fetchall()]

        councils = []
        for cid in cids:
            cur = conn.execute(
                """
                SELECT agent, prompt_text, result_text, duration_s
                FROM calls
                WHERE council_id=? AND round=1 AND status='completed'
                  AND result_text IS NOT NULL AND length(result_text) > 0
                  AND prompt_text IS NOT NULL AND length(prompt_text) > 100
                ORDER BY agent
                """,
                (cid,),
            )
            r1 = [dict(r) for r in cur.fetchall()]
            if len(r1) >= 3:
                councils.append({"council_id": cid, "r1_calls": r1})
            if len(councils) >= limit:
                break
        return councils
    finally:
        conn.close()


async def call_grok(prompt: str) -> tuple[str, float, str | None]:
    """Run grok -p and return (raw_output, elapsed, error)."""
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        "grok", "-p", prompt,
        "--output-format", "json",
        "--always-approve",
        "--model", GROK_MODEL,
        "--effort", GROK_EFFORT,
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
        return "", time.time() - t0, f"timeout after {GROK_TIMEOUT}s"
    if proc.returncode != 0:
        return "", time.time() - t0, f"grok exit {proc.returncode}: {stderr.decode(errors='replace')[:200]}"
    return stdout.decode(errors="replace").strip(), time.time() - t0, None


def parse_grok_text(raw: str) -> str:
    try:
        outer = json.loads(raw)
        return str(outer.get("text", "") or "")
    except json.JSONDecodeError:
        last = raw.rfind("}")
        if last > 0:
            try:
                return str(json.loads(raw[: last + 1]).get("text", ""))
            except (json.JSONDecodeError, ValueError):
                pass
        return raw


def structural_metrics(text: str) -> dict:
    if not text:
        return {"length": 0, "code_blocks": 0, "file_refs": 0, "bullets": 0, "headings": 0}
    return {
        "length": len(text),
        "code_blocks": text.count("```") // 2,
        "file_refs": len(re.findall(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|sql|md|json|yaml|yml)(?::\d+)?\b", text)),
        "bullets": sum(1 for ln in text.split("\n") if ln.strip().startswith(("- ", "* ", "1.", "2.", "3."))),
        "headings": sum(1 for ln in text.split("\n") if ln.strip().startswith("#")),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=0.4)
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
        print(f"[resume] {len(already_done)} councils already processed")

    print(f"[init] loading up to {args.limit} councils with blind ratings + >=3 seats")
    councils = load_councils(args.limit)
    councils = [c for c in councils if c["council_id"] not in already_done]
    print(f"[init] {len(councils)} councils remaining (effort={GROK_EFFORT}, model={GROK_MODEL})")

    with JSONL_PATH.open("a") as f:
        for i, council in enumerate(councils, 1):
            cid = council["council_id"]
            seed_prompt = council["r1_calls"][0]["prompt_text"]
            raw, elapsed, err = await call_grok(seed_prompt)
            if err:
                print(f"[{i}/{len(councils)}] {cid} — error: {err} ({elapsed:.1f}s)")
                f.write(json.dumps({"council_id": cid, "error": err, "elapsed_s": elapsed}) + "\n")
                f.flush()
                continue
            grok_text = parse_grok_text(raw)
            grok_metrics = structural_metrics(grok_text)
            existing = {
                c["agent"]: {
                    "metrics": structural_metrics(c["result_text"]),
                    "duration_s": c["duration_s"],
                    "preview": c["result_text"][:300],
                }
                for c in council["r1_calls"]
            }
            row = {
                "council_id": cid,
                "elapsed_s": round(elapsed, 2),
                "grok_response": grok_text,
                "grok_metrics": grok_metrics,
                "existing": existing,
            }
            f.write(json.dumps(row) + "\n")
            f.flush()
            rows.append(row)
            print(f"[{i}/{len(councils)}] {cid} — len={grok_metrics['length']} cb={grok_metrics['code_blocks']} fr={grok_metrics['file_refs']} ({elapsed:.1f}s)")
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    print(f"\n[done] wrote {JSONL_PATH}")
    write_metrics(rows)
    print(f"[done] wrote {METRICS_PATH}")


def write_metrics(rows: list[dict]):
    valid = [r for r in rows if r.get("grok_metrics")]
    if not valid:
        METRICS_PATH.write_text("# Seat R1 Metrics\n\nNo valid rows.\n")
        return

    per_agent_metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in valid:
        for key in ("length", "code_blocks", "file_refs", "bullets", "headings"):
            per_agent_metrics["grok"][key].append(r["grok_metrics"][key])
        for agent, info in r["existing"].items():
            for key in ("length", "code_blocks", "file_refs", "bullets", "headings"):
                per_agent_metrics[agent][key].append(info["metrics"][key])

    grok_durations = [r["elapsed_s"] for r in valid]
    per_agent_duration: dict[str, list[float]] = {"grok": grok_durations}
    for r in valid:
        for agent, info in r["existing"].items():
            per_agent_duration.setdefault(agent, []).append(info["duration_s"] or 0)

    def stats(xs: list[float]) -> tuple[float, float, float]:
        if not xs:
            return 0.0, 0.0, 0.0
        srt = sorted(xs)
        mean = sum(xs) / len(xs)
        median = srt[len(srt) // 2]
        return mean, median, max(xs)

    md = [
        "# Grok-as-7th-Seat — Structural R1 Quality (Shadow)",
        "",
        f"**Model**: `{GROK_MODEL}` (effort={GROK_EFFORT}) via Grok CLI",
        f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Councils**: {len(valid)}",
        "",
        "## Latency (seconds)",
        "",
        "| Agent | n | mean | median | max |",
        "|---|---|---|---|---|",
    ]
    for agent in sorted(per_agent_duration.keys(), key=lambda a: -sum(per_agent_duration[a]) / max(1, len(per_agent_duration[a]))):
        xs = per_agent_duration[agent]
        m, med, mx = stats(xs)
        bold = "**" if agent == "grok" else ""
        md.append(f"| {bold}{agent}{bold} | {len(xs)} | {m:.1f} | {med:.1f} | {mx:.1f} |")

    for metric_name, label in (
        ("length", "Response length (chars)"),
        ("code_blocks", "Code blocks (count)"),
        ("file_refs", "File references (count)"),
        ("bullets", "Bullet points (count)"),
        ("headings", "Markdown headings (count)"),
    ):
        md.extend([
            "",
            f"## {label}",
            "",
            "| Agent | n | mean | median | max |",
            "|---|---|---|---|---|",
        ])
        for agent in sorted(per_agent_metrics.keys(), key=lambda a: -sum(per_agent_metrics[a][metric_name]) / max(1, len(per_agent_metrics[a][metric_name]))):
            xs = per_agent_metrics[agent][metric_name]
            m, med, mx = stats(xs)
            bold = "**" if agent == "grok" else ""
            md.append(f"| {bold}{agent}{bold} | {len(xs)} | {m:.1f} | {med:.1f} | {mx:.0f} |")

    md.extend([
        "",
        "## Interpretation notes",
        "",
        "- **Length** alone is not quality, but order-of-magnitude shorter = likely shallower analysis.",
        "- **Code blocks + file refs** = groundedness proxies. Coding-strong seats reference real paths.",
        "- **Headings + bullets** = structure proxy. Too few = stream-of-consciousness; too many = formatting noise.",
        "- **Median (not mean) is the right central-tendency** for response length — distributions are heavy-tailed.",
        "",
        "Next step if Grok looks comparable: full quality experiment with cross-judge blind rating (Phase C).",
        "If Grok is order-of-magnitude shorter / fewer code blocks → likely weak seat, stop here.",
    ])
    METRICS_PATH.write_text("\n".join(md))


if __name__ == "__main__":
    asyncio.run(main())
