"""Tests for owlex.adapters.db.migrations."""
from __future__ import annotations

import sqlite3

import pytest

from owlex.adapters.db import migrations


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_fresh_install_applies_all_in_order():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    applied = migrations.run(conn)

    assert applied == [1, 2, 3, 4]
    tables = _tables(conn)
    for expected in ("calls", "council_outcomes", "agent_scores", "council_anonymization",
                     "pairwise_agreements", "schema_migrations"):
        assert expected in tables

    calls_cols = _columns(conn, "calls")
    for col in ("model", "input_tokens", "output_tokens", "finish_reason",
                "position_delta", "position_label"):
        assert col in calls_cols

    assert "backfilled" in _columns(conn, "council_outcomes")

    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    assert versions == {1, 2, 3, 4}


def test_rerun_is_noop():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    migrations.run(conn)
    second = migrations.run(conn)
    assert second == []


def test_checksum_mismatch_raises():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    migrations.run(conn)
    conn.execute(
        "UPDATE schema_migrations SET checksum='deadbeef' WHERE version=2"
    )
    with pytest.raises(migrations.MigrationCorrupted):
        migrations.run(conn)


def test_bootstrap_stamps_existing_db_without_rerunning():
    """A pre-migration DB has 'calls' but no 'schema_migrations'. Bootstrap should
    record all known versions as applied without trying to recreate schema."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    # Simulate pre-migration state: tables already present from old _ensure_columns path.
    conn.execute("CREATE TABLE calls (task_id TEXT PRIMARY KEY)")

    applied = migrations.run(conn)

    assert applied == []  # nothing newly applied
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    assert versions == {1, 2, 3, 4}
    # The pre-existing calls table is untouched (still has only task_id).
    assert _columns(conn, "calls") == {"task_id"}
