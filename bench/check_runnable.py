"""Audit per-source runnability for the live raw_diff probe (no codex)."""
from __future__ import annotations

from bench.corpus import load_corpus


def main() -> None:
    by_source: dict[str, dict] = {}
    for it in load_corpus(include=("seeded", "mined", "mutants", "dataset", "db_labeled")):
        s = it.get("source", "_unset")
        d = by_source.setdefault(s, {"n": 0, "has_diff": 0, "has_post": 0, "has_bugs": 0})
        d["n"] += 1
        if (it.get("diff") or "").strip():
            d["has_diff"] += 1
        if it.get("post_image"):
            d["has_post"] += 1
        if it.get("bugs"):
            d["has_bugs"] += 1
    for s in sorted(by_source):
        d = by_source[s]
        print(f"{s:14} n={d['n']:3}  has_diff={d['has_diff']:3}  has_post={d['has_post']:3}  has_bugs={d['has_bugs']:3}")


if __name__ == "__main__":
    main()
