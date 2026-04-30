"""One-shot backfills for legacy councils.

Two modes:
- (default) Aggregate agreement: re-run :func:`owlex.agreement.score_agreement`
  on R1 previews → ``council_outcomes`` row with ``backfilled=1``.
- ``--pairwise`` Pairwise matrix: compute Jaccard term-overlap between every
  unordered pair of R1 previews → ``pairwise_agreements`` rows with
  ``source='overlap'``. Free; no LLM calls.

Usage:
    python -m owlex.dashboard.backfill --limit 100 --concurrency 3 --random
    python -m owlex.dashboard.backfill --pairwise --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from datetime import datetime

from .. import store
from ..agreement import score_agreement

PLACEHOLDER_QUESTION = (
    "(historical council; original prompt unavailable — judge agreement based on the responses themselves)"
)


def _select_candidates(limit: int, randomize: bool) -> list[tuple[str, str]]:
    """Return [(council_id, ended_at)] for councils that need backfill."""
    conn = store.connect()
    rows = conn.execute(
        """SELECT c.council_id,
                  MAX(c.completed_at) AS ended_at,
                  COUNT(*)            AS r1_count
             FROM calls c
        LEFT JOIN council_outcomes o ON o.council_id = c.council_id
            WHERE c.council_id IS NOT NULL
              AND c.round = 1
              AND c.status = 'completed'
              AND c.result_text IS NOT NULL
              AND length(c.result_text) > 30
              AND o.council_id IS NULL
            GROUP BY c.council_id
           HAVING r1_count >= 2"""
    ).fetchall()
    cands = [(r["council_id"], r["ended_at"]) for r in rows]
    if randomize:
        random.shuffle(cands)
    else:
        cands.sort(key=lambda x: x[1] or "", reverse=True)
    return cands[:limit]


def _r1_responses(council_id: str) -> dict[str, str]:
    conn = store.connect()
    rows = conn.execute(
        """SELECT agent, result_text
             FROM calls
            WHERE council_id = ?
              AND round = 1
              AND status = 'completed'
              AND result_text IS NOT NULL
              AND length(result_text) > 30""",
        (council_id,),
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        # If multiple calls for the same agent (rare), keep the longest.
        prev = out.get(r["agent"])
        if prev is None or len(r["result_text"]) > len(prev):
            out[r["agent"]] = r["result_text"]
    return out


async def _score_one(council_id: str, ended_at: str, sem: asyncio.Semaphore) -> tuple[str, float | None, str]:
    async with sem:
        responses = _r1_responses(council_id)
        if len(responses) < 2:
            return council_id, None, "skipped (<2 R1 responses)"
        try:
            score, reason = await score_agreement(PLACEHOLDER_QUESTION, responses, timeout=60)
        except Exception as e:
            return council_id, None, f"error: {e}"
        store.record_council_outcome(
            council_id,
            total_duration_s=0.0,
            rounds=0,
            deliberation=False,
            critique=False,
            agreement_score=score,
            agreement_reason=reason,
            progress_log=[f"[backfill] re-scored from R1 previews on {datetime.now().isoformat()}"],
            claude_opinion=None,
            backfilled=True,
            completed_at=ended_at,
        )
        return council_id, score, reason


async def main_async(limit: int, concurrency: int, randomize: bool, dry_run: bool) -> None:
    candidates = _select_candidates(limit, randomize)
    if not candidates:
        print("Nothing to backfill — every council with R1 data already has an outcome row.")
        return
    print(f"Backfilling {len(candidates)} councils (concurrency={concurrency}{', random' if randomize else ''})...")
    if dry_run:
        for cid, ts in candidates[:10]:
            print(f"  would score council={cid} ended={ts}")
        print(f"  …{len(candidates)} total. Re-run without --dry-run to execute.")
        return

    sem = asyncio.Semaphore(concurrency)
    tasks = [asyncio.create_task(_score_one(cid, ts, sem)) for cid, ts in candidates]
    started = time.monotonic()
    done = ok = 0
    for fut in asyncio.as_completed(tasks):
        cid, score, reason = await fut
        done += 1
        if score is not None:
            ok += 1
            print(f"[{done}/{len(tasks)}] {cid}  {score:.1f}/5  — {reason}")
        else:
            print(f"[{done}/{len(tasks)}] {cid}  --   {reason}")
    elapsed = time.monotonic() - started
    print(f"\nDone. {ok}/{len(tasks)} scored in {elapsed:.1f}s.")


def _word_set(text: str, min_len: int = 5) -> set[str]:
    import re as _re
    return {w.lower() for w in _re.findall(r"[A-Za-z]+", text or "") if len(w) >= min_len}


def run_skills(limit: int) -> None:
    """Walk completed calls without skill_parse_state and run their parser."""
    from .parsers import parse_and_persist
    conn = store.connect()
    rows = conn.execute(
        """SELECT c.task_id, c.agent, COALESCE(c.completed_at, c.started_at) AS ts, c.session_id
             FROM calls c
        LEFT JOIN skill_parse_state s ON s.task_id = c.task_id
            WHERE c.status IN ('completed', 'failed')
              AND s.task_id IS NULL
            ORDER BY c.started_at DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    if not rows:
        print("Nothing to backfill — every completed call already has a parse-state row.")
        return
    print(f"Parsing skill/tool invocations for {len(rows)} calls...")
    found_total = 0
    for i, r in enumerate(rows, 1):
        try:
            n = parse_and_persist(r["task_id"], r["agent"], r["ts"], session_id=r["session_id"])
        except Exception as e:
            print(f"[{i}/{len(rows)}] {r['task_id']}  ERR {e}")
            continue
        found_total += n
        if n > 0 or i % 50 == 0:
            print(f"[{i}/{len(rows)}] {r['task_id']}  {r['agent']:9}  {n} invocations")
    print(f"\nDone. {found_total} invocations across {len(rows)} calls.")


def run_position_deltas() -> None:
    """Compute position_delta/position_label on every R2 call that has an R1 sibling."""
    conn = store.connect()
    rows = conn.execute(
        """SELECT r2.task_id AS r2_task, r1.result_text AS r1_text, r2.result_text AS r2_text
             FROM calls r2
             JOIN calls r1 ON r1.council_id = r2.council_id AND r1.agent = r2.agent AND r1.round = 1
            WHERE r2.round = 2
              AND r2.status = 'completed'
              AND r2.result_text IS NOT NULL
              AND r1.result_text IS NOT NULL
              AND r2.position_label IS NULL"""
    ).fetchall()
    if not rows:
        print("Nothing to backfill — every eligible R2 row already has a position label.")
        return
    print(f"Computing position deltas for {len(rows)} R2 calls...")
    for r in rows:
        sa = _word_set(r["r1_text"])
        sb = _word_set(r["r2_text"])
        union = len(sa | sb) or 1
        delta = 1.0 - (len(sa & sb) / union)
        label = "unchanged" if delta < 0.845 else "minor" if delta < 0.906 else "major"
        store.record_position_delta(r["r2_task"], position_delta=delta, position_label=label)
    print(f"Done. Labeled {len(rows)} R2 calls.")


def _jaccard_score(a: str, b: str) -> float:
    """Map Jaccard overlap (0..1) to the 1..5 agreement scale."""
    sa, sb = _word_set(a), _word_set(b)
    if not sa and not sb:
        return 3.0
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    overlap = inter / union
    return 1.0 + 4.0 * overlap


def _select_pairwise_candidates(limit: int) -> list[str]:
    """Council IDs that have ≥2 R1 responses and no pairwise rows yet."""
    conn = store.connect()
    rows = conn.execute(
        """SELECT c.council_id, COUNT(*) AS n
             FROM calls c
        LEFT JOIN pairwise_agreements p ON p.council_id = c.council_id
            WHERE c.council_id IS NOT NULL
              AND c.round = 1
              AND c.status = 'completed'
              AND c.result_text IS NOT NULL
              AND length(c.result_text) > 30
              AND p.council_id IS NULL
            GROUP BY c.council_id
           HAVING n >= 2
            ORDER BY MAX(c.completed_at) DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [r["council_id"] for r in rows]


def run_pairwise(limit: int) -> None:
    cids = _select_pairwise_candidates(limit)
    if not cids:
        print("Nothing to backfill — every eligible council already has a pairwise matrix.")
        return
    print(f"Backfilling pairwise matrices for {len(cids)} councils (overlap heuristic, free)...")
    total_pairs = 0
    for i, cid in enumerate(cids, 1):
        responses = _r1_responses(cid)
        if len(responses) < 2:
            print(f"[{i}/{len(cids)}] {cid}  skipped (<2 R1)")
            continue
        agents = sorted(responses.keys())
        rows: list[tuple[str, str, float, str | None]] = []
        for ai in range(len(agents)):
            for bj in range(ai + 1, len(agents)):
                a, b = agents[ai], agents[bj]
                rows.append((a, b, _jaccard_score(responses[a], responses[b]), "overlap heuristic"))
        store.record_pairwise_agreements(cid, rows, source="overlap")
        total_pairs += len(rows)
        print(f"[{i}/{len(cids)}] {cid}  {len(rows)} pairs")
    print(f"\nDone. {total_pairs} pairs written across {len(cids)} councils.")


def main() -> None:
    p = argparse.ArgumentParser(prog="owlex-backfill")
    p.add_argument("--limit", type=int, default=100, help="Max councils to backfill (default 100)")
    p.add_argument("--concurrency", type=int, default=3, help="Parallel judge calls (default 3)")
    p.add_argument("--random", action="store_true", help="Pick a random sample instead of newest first")
    p.add_argument("--dry-run", action="store_true", help="Show what would be backfilled without calling the judge")
    p.add_argument("--pairwise", action="store_true", help="Backfill the pairwise matrix using overlap heuristic (free)")
    p.add_argument("--positions", action="store_true", help="Backfill position deltas on R2 calls (free)")
    p.add_argument("--skills", action="store_true", help="Parse skill/tool invocations from agent session files (free)")
    args = p.parse_args()
    try:
        if args.pairwise:
            run_pairwise(args.limit)
        elif args.positions:
            run_position_deltas()
        elif args.skills:
            run_skills(args.limit)
        else:
            asyncio.run(main_async(args.limit, args.concurrency, args.random, args.dry_run))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)


if __name__ == "__main__":
    main()
