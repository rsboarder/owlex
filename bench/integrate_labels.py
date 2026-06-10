"""Convert the db-target labeling-workflow result into a corpus manifest.

Reads the workflow's result JSON (``{result: {items: [...]}}`` or ``{items: [...]}``),
keeps only the reviewable items that carry ≥1 soft bug, and writes them as a
``db-labeled`` manifest. These are SOFT labels (LLM-derived, mostly file=unknown)
— tagged ``source="db-llm-label"`` and ``split="iterate"`` so they stay out of
the held-out/precision-critical set, per the AUDIT-10 anti-p-hacking discipline.
"""
from __future__ import annotations

import argparse
import json


def _diff_size(n_bugs: int) -> str:
    return "S"


def to_items(result_items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in result_items:
        bugs = r.get("bugs") or []
        if not (r.get("is_reviewable") and bugs):
            continue
        primary = bugs[0].get("file") or "unknown"
        out.append(
            {
                "id": r["id"],
                "file": primary,
                "bugs": [
                    {
                        "bug_type": b.get("bug_type", "other"),
                        "file": b.get("file") or "unknown",
                        "line": b.get("line") if isinstance(b.get("line"), int) else 1,
                        "description": b.get("description", ""),
                    }
                    for b in bugs
                ],
                "source": "db-llm-label",
                "lang": "python",
                "diff_size": "S",
                "risk_domain": "council-target",
                "difficulty": "hard",
                "split": "iterate",
                "label_kind": "llm-soft",
                "provenance": {"source": "db-target-soft-labeling", "reason": r.get("reason", "")},
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="workflow result JSON path")
    ap.add_argument("--out", default="bench/corpus/db/labeled.json")
    args = ap.parse_args()
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    result = data.get("result", data)
    items = to_items(result.get("items", []))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "schema_version": 1,
                "kind": "db-labeled",
                "description": "Soft (LLM-derived) labels for council-DB targets — realism/precision only, kept out of the held-out split.",
                "items": items,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Wrote {len(items)} soft-labeled db items → {args.out}")


if __name__ == "__main__":
    main()
