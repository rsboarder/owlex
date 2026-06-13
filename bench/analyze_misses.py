"""Show, for real-fix items, the planted bug vs what the auditor found per run.

Diagnoses WHY recall is low on the hard stratum: did the reviewer stay silent,
flag the wrong place, or find a different real issue? Read this before changing
the reviewer — fund diagnosis before the fix.
"""
from __future__ import annotations

import argparse
import json

from bench.corpus import load_corpus
from bench.scorer import score_run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="bench/reports/stalo_probe_v3.json")
    ap.add_argument("--source", default="real-fix")
    args = ap.parse_args()
    report = json.load(open(args.report, encoding="utf-8"))
    by_id = {it["id"]: it for it in load_corpus()}
    per_item_runs = report["results"]["raw_diff"]["per_item_runs"]

    for entry in per_item_runs:
        item = by_id.get(entry["id"], {})
        if item.get("source") != args.source:
            continue
        bug = (item.get("bugs") or [{}])[0]
        print(f"## {entry['id']}  — planted bug: {bug.get('file')}:{bug.get('line')}")
        print(f"   {bug.get('description','')[:160]}")
        for ri, run in enumerate(entry["runs"]):
            findings = run.get("findings", [])
            sc = score_run(findings, item.get("bugs", []), item.get("decoys", []),
                           line_window=3, granularity="line")
            verdict = "HIT" if sc["bugs_found"] else ("SILENT" if not findings else "MISS(found-elsewhere)")
            cited = ", ".join(f"{f.get('file','?').split('/')[-1]}:{f.get('line')}" for f in findings[:4])
            print(f"   run {ri}: {verdict:22} findings=[{cited}]")
        print()


if __name__ == "__main__":
    main()
