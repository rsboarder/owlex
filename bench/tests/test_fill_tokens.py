"""fill_tokens backfill — dry-run safety + NULL-only fill, on a tmp DB only."""
from __future__ import annotations

import sqlite3

from bench.fill_tokens import count_fillable, estimate_tokens, fill


def _fixture_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE calls (prompt_text TEXT, result_text TEXT, "
        "input_tokens INTEGER, output_tokens INTEGER)"
    )
    con.executemany(
        "INSERT INTO calls (prompt_text, result_text, input_tokens, output_tokens) VALUES (?,?,?,?)",
        [
            ("a" * 40, "b" * 8, None, None),   # both NULL → fillable
            ("c" * 40, "d" * 8, 999, None),     # input already set → keep it
            ("e" * 40, "f" * 8, 5, 6),          # both set → not fillable
        ],
    )
    con.commit()
    con.close()


def test_estimate_tokens():
    assert estimate_tokens("x" * 40) == 10
    assert estimate_tokens(None) == 0
    assert estimate_tokens("") == 0


def test_dry_run_writes_nothing(tmp_path):
    db = str(tmp_path / "t.db")
    _fixture_db(db)
    assert count_fillable(db) == 2
    assert fill(db, apply=False) == 2
    # still NULL after dry-run
    con = sqlite3.connect(db)
    nulls = con.execute(
        "SELECT count(*) FROM calls WHERE input_tokens IS NULL OR output_tokens IS NULL"
    ).fetchone()[0]
    con.close()
    assert nulls == 2


def test_apply_fills_only_nulls(tmp_path):
    db = str(tmp_path / "t.db")
    _fixture_db(db)
    assert fill(db, apply=True) == 2
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT input_tokens, output_tokens FROM calls ORDER BY rowid"
    ).fetchall()
    con.close()
    assert rows[0] == (10, 2)     # both estimated
    assert rows[1] == (999, 2)    # input preserved, output estimated
    assert rows[2] == (5, 6)      # untouched
    assert count_fillable(db) == 0
