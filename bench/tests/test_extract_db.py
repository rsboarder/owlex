"""Tests for bench/extract_db.py — read-only council-DB target extractor.

Uses a tiny fixture SQLite DB so the production DB (~/.owlex/owlex.db) is never
touched. The autouse ``_isolate_owlex_home`` fixture in conftest.py already
redirects OWLEX_HOME to a tmp dir, but these tests pass an explicit ``db_path``
for full control.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

# Support running as `python -m pytest bench/` (repo root on sys.path) and as
# `python bench/tests/test_extract_db.py` (bench/ on sys.path).
import sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bench.extract_db import extract_targets  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fixture_db(path: str) -> None:
    """Create a minimal ``calls`` table with 3 rows:
    - row 1: round=1, code fence, council_id='c1'  → INCLUDED
    - row 2: round=1, code fence, council_id='c2'  → INCLUDED
    - row 3: round=2, code fence, council_id='c1'  → EXCLUDED (round != 1)
    Plus a 4th row: round=1, no code fence, council_id='c3' → EXCLUDED (no ```)
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE calls (
            rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
            council_id TEXT NOT NULL,
            agent      TEXT,
            round      INTEGER,
            status     TEXT,
            prompt_text TEXT,
            result_text TEXT,
            model      TEXT
        )
        """
    )
    rows = [
        ("c1", "codex", 1, "completed", "Review this:\n```python\nx = 1\n```", "Looks good"),
        ("c2", "gemini", 1, "completed", "Check:\n```js\nconsole.log(1)\n```", "Fine"),
        ("c1", "codex", 2, "completed", "Round2:\n```python\ny=2\n```", "R2 answer"),
        ("c3", "cursor", 1, "completed", "No code fence here at all", "Nothing"),
    ]
    conn.executemany(
        "INSERT INTO calls (council_id, agent, round, status, prompt_text, result_text) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_returns_only_round1_fenced(tmp_path):
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    items = extract_targets(db_path=db)

    assert len(items) == 2
    ids = {i["id"] for i in items}
    assert ids == {"db-c1", "db-c2"}


def test_extract_item_shape(tmp_path):
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    items = extract_targets(db_path=db)
    item = next(i for i in items if i["id"] == "db-c1")

    assert item["source"] == "db"
    assert item["council_id"] == "c1"
    assert "```" in item["prompt_text"]
    assert item["result_text"] == "Looks good"
    assert item["lang"] == "python"
    assert item["split"] == "iterate"
    assert item["provenance"]["source"] == "owlex-council-db"
    assert item["provenance"]["council_id"] == "c1"
    assert item["provenance"]["extracted_round"] == 1


def test_no_bugs_key(tmp_path):
    """Unlabeled targets must NOT carry a 'bugs' key — labels come from a separate workflow."""
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    items = extract_targets(db_path=db)
    for item in items:
        assert "bugs" not in item


def test_no_rating_or_agreement_fields(tmp_path):
    """Must not expose ratings/agreement (circularity / judge-contamination concerns)."""
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    items = extract_targets(db_path=db)
    forbidden = {"rating", "agreement_score", "score", "agent_score"}
    for item in items:
        assert not (forbidden & set(item)), f"Forbidden fields present: {forbidden & set(item)}"


def test_limit_caps_results(tmp_path):
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    items = extract_targets(db_path=db, limit=1)
    assert len(items) == 1


def test_nonexistent_db_returns_empty():
    items = extract_targets(db_path="/tmp/owlex-nonexistent-fixture-db-xyz.db")
    assert items == []


def test_empty_db_returns_empty(tmp_path):
    """An existing DB with no ``calls`` table returns [] without crashing."""
    db = str(tmp_path / "empty.db")
    # Create an empty SQLite file (no tables).
    conn = sqlite3.connect(db)
    conn.close()

    items = extract_targets(db_path=db)
    assert items == []


def test_extractor_does_not_write_to_db(tmp_path):
    """The fixture DB file's mtime must be unchanged after extraction (read-only guarantee)."""
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    mtime_before = os.path.getmtime(db)
    extract_targets(db_path=db)
    mtime_after = os.path.getmtime(db)

    assert mtime_before == mtime_after, "extract_targets modified the DB file"


def test_readonly_uri_rejects_writes(tmp_path):
    """Opening the fixture DB via read-only URI should raise on an INSERT attempt."""
    db = str(tmp_path / "test.db")
    _make_fixture_db(db)

    uri = f"file:{db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO calls (council_id) VALUES ('injected')")
    conn.close()


def test_one_row_per_council_id(tmp_path):
    """Two round-1 fenced rows under the same council_id yield only one output item."""
    db = str(tmp_path / "dedup.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE calls (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            council_id TEXT,
            agent TEXT,
            round INTEGER,
            status TEXT,
            prompt_text TEXT,
            result_text TEXT,
            model TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO calls (council_id, agent, round, status, prompt_text, result_text) VALUES (?,?,?,?,?,?)",
        [
            ("cx", "a1", 1, "completed", "q1 ```python\nx=1\n```", "r1"),
            ("cx", "a2", 1, "completed", "q1 ```python\nx=1\n```", "r2"),
        ],
    )
    conn.commit()
    conn.close()

    items = extract_targets(db_path=db)
    assert len(items) == 1
    assert items[0]["id"] == "db-cx"


def test_main_writes_json(tmp_path, monkeypatch):
    """main() writes a valid JSON file with the expected envelope keys."""
    import subprocess, sys

    db = str(tmp_path / "test.db")
    _make_fixture_db(db)
    out = str(tmp_path / "out" / "targets.json")

    result = subprocess.run(
        [sys.executable, os.path.join(_REPO_ROOT, "bench", "extract_db.py"),
         "--db", db, "--out", out],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    with open(out) as f:
        data = json.load(f)

    assert data["schema_version"] == 1
    assert data["kind"] == "targets"
    assert "UNLABELED" in data["description"]
    assert len(data["items"]) == 2
