"""AUDIT-10 было/стало corpus-robustness comparison (no codex; pure stats)."""
from __future__ import annotations

import json

from bench.corpus import load_corpus, load_seeded
from bench.scorer import corpus_stats


def summ(items: list[dict]) -> dict:
    s = corpus_stats(items)
    return {
        "total": s["total"],
        "by_source": s["by_source"],
        "by_bug_type": s["by_bug_type"],
        "by_diff_size": s.get("by_diff_size"),
        "by_split": s.get("by_split"),
        "objective_label_pct": s.get("objective_label_pct"),
        "bug_type_coverage_missing": s["bug_type_coverage"]["missing"],
        "content_hash": s["content_hash"][:12],
    }


def main() -> None:
    bylo = load_seeded("bench/corpus/seeded/manifest.json")["items"]
    stalo = load_corpus()
    print("=== BYLO (pre-AUDIT-10: seeded only) ===")
    print(json.dumps(summ(bylo), indent=2, ensure_ascii=False))
    print("\n=== STALO (assembled stratified corpus; DB soft-labels pending) ===")
    print(json.dumps(summ(stalo), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
