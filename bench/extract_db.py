#!/usr/bin/env python3
"""Read-only extractor for council-DB review targets.

Pulls round-1 prompts that embed a code fence from the owlex SQLite council DB.
These are UNLABELED soft-label targets only — no ``bugs`` key, no ratings/agreement.
Labels come later via a separate workflow (``source=db-llm-label``).

Safety: opens SQLite in read-only URI mode. Never writes. No owlex imports.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3


def _default_db_path() -> str:
    owlex_home = os.environ.get("OWLEX_HOME", os.path.expanduser("~/.owlex"))
    return os.path.join(owlex_home, "owlex.db")


def extract_targets(db_path: str | None = None, limit: int | None = None) -> list[dict]:
    """Return one representative row per council_id where round=1 and prompt contains a code fence.

    Selects ``MIN(rowid)`` per ``council_id`` so the result is deterministic.
    Returns items shaped for the bench corpus — no ``bugs`` key (unlabeled).

    Args:
        db_path: Path to owlex SQLite DB. Defaults to ``$OWLEX_HOME/owlex.db``.
        limit:   Cap on returned item count (for smoke runs).

    Returns:
        List of target dicts, each with keys:
        ``id``, ``council_id``, ``prompt_text``, ``result_text``, ``source``,
        ``lang``, ``split``, ``provenance``.
    """
    resolved_path = db_path or _default_db_path()

    if not os.path.exists(resolved_path):
        return []

    uri = f"file:{resolved_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return []

    try:
        sql = """
            SELECT council_id, prompt_text, result_text
            FROM calls
            WHERE round = 1
              AND prompt_text LIKE '%```%'
            GROUP BY council_id
            HAVING rowid = MIN(rowid)
            ORDER BY MIN(rowid)
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        cursor = conn.execute(sql)
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []

    conn.close()

    items = []
    for council_id, prompt_text, result_text in rows:
        items.append({
            "id": f"db-{council_id}",
            "council_id": council_id,
            "prompt_text": prompt_text or "",
            "result_text": result_text or "",
            "source": "db",
            "lang": "python",
            "split": "iterate",
            "provenance": {
                "source": "owlex-council-db",
                "council_id": council_id,
                "extracted_round": 1,
            },
        })
    return items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract unlabeled review targets from the owlex council DB."
    )
    parser.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "corpus", "db", "targets.json"
        ),
        help="Output JSON path (default: bench/corpus/db/targets.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on number of targets (for smoke runs)",
    )
    parser.add_argument(
        "--db",
        default=None,
        dest="db_path",
        help="Path to owlex.db (default: $OWLEX_HOME/owlex.db)",
    )
    args = parser.parse_args()

    items = extract_targets(db_path=args.db_path, limit=args.limit)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    payload = {
        "schema_version": 1,
        "kind": "targets",
        "description": (
            "Read-only council-DB review targets, UNLABELED (soft-label only). "
            "Extracted from round-1 prompts containing code fences. "
            "No 'bugs' key — labels are assigned via a separate workflow (source=db-llm-label)."
        ),
        "items": items,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Extracted {len(items)} targets → {args.out}")


if __name__ == "__main__":
    main()
