"""Summarize a stratified live probe report into per-source precision/recall."""
from __future__ import annotations

import argparse
import json

from bench.corpus import load_corpus
from bench.scorer import aggregate, score_run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="bench/reports/stalo_probe.json")
    args = ap.parse_args()
    report = json.load(open(args.report, encoding="utf-8"))

    by_id = {it["id"]: it for it in load_corpus()}
    per_item_runs = report["results"]["raw_diff"]["per_item_runs"]

    by_source: dict[str, list[dict]] = {}
    for entry in per_item_runs:
        item = by_id.get(entry["id"])
        if not item:
            continue
        bugs = item.get("bugs", [])
        decoys = item.get("decoys", [])
        for run in entry["runs"]:
            sc = score_run(run.get("findings", []), bugs, decoys, line_window=3, granularity="line")
            by_source.setdefault(item.get("source", "_unset"), []).append(sc)

    print(f"=== STALO live probe (stratified, raw_diff) — {args.report} ===")
    print(f"wall_time mean: {report['results']['raw_diff']['cost']['wall_time_s']['mean']}s\n")
    pooled: list[dict] = []
    for src in sorted(by_source):
        runs = by_source[src]
        pooled.extend(runs)
        agg = aggregate(runs)
        p, r = agg["precision"], agg["recall"]
        dr = agg["detection_rate"]
        print(
            f"{src:10}  runs={agg['runs']:2}  "
            f"precision={_fmt(p)}  recall={_fmt(r)}  detect={_fmt(dr)}  "
            f"decoy_hits={_fmt(agg['decoy_hits'])}"
        )
    print("-" * 70)
    pa = aggregate(pooled)
    print(
        f"{'POOLED':10}  runs={pa['runs']:2}  "
        f"precision={_fmt(pa['precision'])}  recall={_fmt(pa['recall'])}  "
        f"detect={_fmt(pa['detection_rate'])}  decoy_hits={_fmt(pa['decoy_hits'])}"
    )


def _fmt(ms: dict) -> str:
    if ms is None or ms.get("mean") is None:
        return "  n/a "
    return f"{ms['mean']:.2f}±{ms['stdev']:.2f}"


if __name__ == "__main__":
    main()
