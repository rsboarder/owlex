"""Characterize the auditor's false positives on decoy (benign-refactor) items.

Decoys carry NO bug, so every finding is a false positive by construction. This
reads a stratified probe report, isolates the decoy items, and prints what the
reviewer flagged on each — so the over-flagging can be characterized (hallucinated
bug vs. style nit vs. arguably-legit concern) rather than just counted.
"""
from __future__ import annotations

import argparse
import json

from bench.corpus import load_corpus
from bench.scorer import meanstdev, parse_findings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="bench/reports/stalo_probe_v3.json")
    ap.add_argument("--full", action="store_true", help="print full finding text, not just counts")
    args = ap.parse_args()
    report = json.load(open(args.report, encoding="utf-8"))
    by_id = {it["id"]: it for it in load_corpus()}
    per_item_runs = report["results"]["raw_diff"]["per_item_runs"]

    decoys = [e for e in per_item_runs if (by_id.get(e["id"], {}).get("source") == "decoy")]
    print(f"=== Decoy false-positive analysis — {args.report} ({len(decoys)} decoy items) ===\n")

    fp_per_run: list[int] = []
    for entry in decoys:
        item = by_id.get(entry["id"], {})
        runs = entry["runs"]
        print(f"## {entry['id']}  (refactor/chore — NO real bug)")
        decoy_notes = [d.get("description", "") for d in item.get("decoys", [])]
        if decoy_notes:
            print(f"   benign change: {decoy_notes[0][:140]}")
        for ri, run in enumerate(runs):
            findings = run.get("findings") or parse_findings(run.get("text", ""))
            fp_per_run.append(len(findings))
            print(f"   run {ri}: {len(findings)} findings (all FP)")
            if args.full:
                for f in findings:
                    print(f"       - {f.get('file')}:{f.get('line')}  {f.get('snippet','')[:120]}")
        print()

    ms = meanstdev(fp_per_run)
    nonzero = sum(1 for n in fp_per_run if n > 0)
    print("-" * 70)
    print(f"FP findings per decoy run: mean {ms['mean']:.2f} ± {ms['stdev']:.2f}  (n={ms['n']} runs)")
    print(f"runs with ≥1 false finding: {nonzero}/{len(fp_per_run)}  "
          f"({100*nonzero/len(fp_per_run):.0f}% of decoy runs over-flagged)")


if __name__ == "__main__":
    main()
