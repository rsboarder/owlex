"""Experiment: does adding an API/type-contract dimension to the review lens
raise recall on real owlex-history bugs — especially the 'quiet' contract bug
(fix-1bbcd1a, Pydantic Field defaults) the baseline lens misses 5/5?

A/B on the real-fix stratum: baseline lens vs baseline+contract-dimension, K runs
each, raw_diff (no file access). Pure improvement experiment — kept OUT of the
committed runner until/unless it proves out.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from bench.run import AUDIT_LENS
from bench.corpus import load_corpus
from bench.scorer import aggregate, parse_findings, score_run

BASELINE = AUDIT_LENS
CONTRACT = AUDIT_LENS + (
    "ALSO audit dimension (6) API / TYPE-CONTRACT correctness: scrutinize "
    "parameter optionality and default values (Pydantic Field defaults, "
    "Optional/None handling, mutable defaults), function and return-type "
    "signatures, and schema/interface definitions. These are subtle contract "
    "bugs that do not crash but silently violate the intended interface.\n\n"
)
LENSES = {"baseline": BASELINE, "contract": CONTRACT}


async def _run_one(sem, lens_text, item, timeout):
    from owlex.second_opinion import get_second_opinion
    prompt = lens_text + "Unified diff under review:\n\n" + item.get("diff", "")
    async with sem:
        ok, text, timed_out = await get_second_opinion(prompt, working_directory=None, timeout=timeout)
    findings = parse_findings(text or "") if ok else []
    return score_run(findings, item.get("bugs", []), item.get("decoys", []),
                     line_window=3, granularity="line")


async def main_async(args):
    items = [it for it in load_corpus() if it.get("source") == "real-fix" and (it.get("diff") or "").strip()]
    sem = asyncio.Semaphore(args.concurrency)
    out = {"k": args.runs, "n_items": len(items), "lenses": {}}

    for lens_name, lens_text in LENSES.items():
        tasks = []
        index = []  # (item_id) parallel to tasks
        for it in items:
            for _ in range(args.runs):
                tasks.append(_run_one(sem, lens_text, it, args.timeout))
                index.append(it["id"])
        scores = await asyncio.gather(*tasks)
        # group by item
        per_item: dict[str, list] = {}
        for iid, sc in zip(index, scores):
            per_item.setdefault(iid, []).append(sc)
        out["lenses"][lens_name] = {
            "pooled": aggregate([s for ss in per_item.values() for s in ss]),
            "per_item": {iid: aggregate(ss) for iid, ss in per_item.items()},
        }
        print(f"[{lens_name}] pooled recall={_f(out['lenses'][lens_name]['pooled']['recall'])} "
              f"precision={_f(out['lenses'][lens_name]['pooled']['precision'])}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")

    # head-to-head: per-item recall delta
    base = out["lenses"]["baseline"]["per_item"]
    cont = out["lenses"]["contract"]["per_item"]
    print("\n=== per-item recall (baseline → contract) ===")
    for iid in sorted(base):
        b = base[iid]["recall"]["mean"]
        c = cont[iid]["recall"]["mean"]
        mark = "  <-- IMPROVED" if (c or 0) > (b or 0) else ("  <-- regressed" if (c or 0) < (b or 0) else "")
        print(f"  {iid:14} {b:.2f} → {c:.2f}{mark}")


def _f(ms):
    return "n/a" if not ms or ms.get("mean") is None else f"{ms['mean']:.2f}±{ms['stdev']:.2f}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default="bench/reports/exp_lens.json")
    asyncio.run(main_async(ap.parse_args()))
