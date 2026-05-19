"""Ordered SQL migrations with checksum-tracked schema_migrations table.

Each migration file lives next to this module and is named NNN_description.sql.
On run(), every file whose version is not yet in schema_migrations is applied
inside a single transaction and recorded with its sha256 checksum. If a file
that has already been applied has a different checksum, run() raises
MigrationCorrupted instead of silently re-running.

Bootstrap: when schema_migrations is missing AND the legacy `calls` table
already exists (a DB created by the pre-migration ``store.py`` code), every
known migration is stamped as applied without re-running its DDL — those
schema effects are already present.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent
_VERSION_RE = re.compile(r"^(\d+)_.+\.sql$")


class MigrationCorrupted(RuntimeError):
    """Raised when an applied migration's checksum no longer matches its file."""


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _discover() -> list[tuple[int, str, Path]]:
    out: list[tuple[int, str, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = _VERSION_RE.match(path.name)
        if not m:
            continue
        out.append((int(m.group(1)), path.name, path))
    out.sort(key=lambda t: t[0])
    return out


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    INTEGER PRIMARY KEY,
               name       TEXT NOT NULL,
               checksum   TEXT NOT NULL,
               applied_at TEXT NOT NULL
           )"""
    )


def _applied(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(r[0]): r[1] for r in rows}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _stamp(conn: sqlite3.Connection, version: int, name: str, checksum: str) -> None:
    conn.execute(
        "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
        (version, name, checksum, datetime.now().isoformat()),
    )


def run(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations. Returns the list of versions applied."""
    migrations = _discover()
    bootstrap = not _table_exists(conn, "schema_migrations") and _table_exists(conn, "calls")
    _ensure_schema_migrations(conn)

    if bootstrap:
        for version, name, path in migrations:
            _stamp(conn, version, name, _checksum(path.read_text(encoding="utf-8")))
        return []

    applied = _applied(conn)
    newly: list[int] = []
    for version, name, path in migrations:
        sql = path.read_text(encoding="utf-8")
        checksum = _checksum(sql)
        if version in applied:
            if applied[version] != checksum:
                raise MigrationCorrupted(
                    f"migration {version} ({name}) checksum mismatch: "
                    f"expected {applied[version]}, file is {checksum}. "
                    "Applied migrations must not be edited in place."
                )
            continue
        # ``executescript`` issues an implicit COMMIT, which conflicts with a
        # manual BEGIN. Parse and execute statements individually inside one
        # transaction so the migration + stamp row are atomic.
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        conn.execute("BEGIN")
        try:
            for stmt in statements:
                conn.execute(stmt)
            _stamp(conn, version, name, checksum)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        newly.append(version)
    return newly
