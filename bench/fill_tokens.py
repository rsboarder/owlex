"""Backfill NULL ``calls.input_tokens`` / ``output_tokens`` (AUDIT-10 sub-part 9).

The council DB never recorded per-call token counts (both columns are NULL), so
cost-in-tokens is unavailable for cost benchmarks. This estimates them from the
stored text (``len(text) // CHARS_PER_TOKEN``) — a coarse proxy, NOT exact
tokenizer output, good enough for relative cost trends.

SAFE BY DEFAULT: dry-run only reports how many rows WOULD be filled. ``--apply``
is required to write, and only ever fills rows that are currently NULL (it never
overwrites a real recorded value). The production DB is the default target, so
run ``--apply`` deliberately, never in tests.
"""
from __future__ import annotations

import argparse
import os
import sqlite3

CHARS_PER_TOKEN = 4


def _db_path() -> str:
    home = os.environ.get("OWLEX_HOME", os.path.expanduser("~/.owlex"))
    return os.path.join(home, "owlex.db")


def estimate_tokens(text: str | None) -> int:
    return (len(text) // CHARS_PER_TOKEN) if text else 0


def count_fillable(db_path: str | None = None) -> int:
    """How many rows have a NULL input/output token count (read-only)."""
    path = db_path or _db_path()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        (n,) = con.execute(
            "SELECT count(*) FROM calls WHERE input_tokens IS NULL OR output_tokens IS NULL"
        ).fetchone()
        return n
    finally:
        con.close()


def fill(db_path: str | None = None, apply: bool = False) -> int:
    """Estimate + (optionally) write token counts for NULL rows. Returns count.

    Only touches rows where the column is currently NULL; an existing value is
    left untouched. With ``apply=False`` nothing is written.
    """
    path = db_path or _db_path()
    if not apply:
        return count_fillable(path)
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT rowid, prompt_text, result_text, input_tokens, output_tokens FROM calls "
            "WHERE input_tokens IS NULL OR output_tokens IS NULL"
        ).fetchall()
        for rowid, prompt, result, it, ot in rows:
            new_it = estimate_tokens(prompt) if it is None else it
            new_ot = estimate_tokens(result) if ot is None else ot
            con.execute(
                "UPDATE calls SET input_tokens=?, output_tokens=? WHERE rowid=?",
                (new_it, new_ot, rowid),
            )
        con.commit()
        return len(rows)
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write estimates (default: dry-run)")
    ap.add_argument("--db", default=None, help="DB path (default: OWLEX_HOME/owlex.db)")
    args = ap.parse_args()
    n = fill(args.db, apply=args.apply)
    verb = "filled" if args.apply else "would fill (dry-run; pass --apply to write)"
    print(f"{verb}: {n} rows with NULL token counts")


if __name__ == "__main__":
    main()
