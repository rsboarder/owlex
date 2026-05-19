"""Connection bootstrap + thin façade over the repository container.

The actual write logic lives in ``owlex.adapters.repositories``. This module
keeps three responsibilities:

1. Owning the per-thread SQLite connection and the global write lock.
2. Running migrations + the one-time legacy JSONL import on first connect.
3. Re-exporting the legacy ``record_*`` API for call sites that haven't
   moved to the repository container yet.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

from .adapters.db import migrations
from .models import Task


def _owlex_home() -> Path:
    """Resolve owlex's home directory at call time so tests can override via env."""
    override = os.environ.get("OWLEX_HOME")
    return Path(override) if override else Path.home() / ".owlex"


def _default_db_path() -> Path:
    return _owlex_home() / "owlex.db"


def _legacy_jsonl_path() -> Path:
    return _owlex_home() / "logs" / "timing.jsonl"


# Backwards-compat module-level constants — resolved at import time.
# Prefer the helpers above for anything that may run after env mutation (tests).
OWLEX_HOME = _owlex_home()
DB_PATH = _default_db_path()
LEGACY_JSONL = _legacy_jsonl_path()

_LOCK = threading.RLock()
_TLS = threading.local()
_INIT_DONE = False


def _agent_and_round(command: str) -> tuple[str, int]:
    """Derive (base_agent, round) from a council/agent command name."""
    name = command or ""
    if name.startswith("council_"):
        name = name[len("council_"):]
    round_n = 1
    if name.endswith("_delib"):
        name = name[: -len("_delib")]
        round_n = 2
    if "_" in name:
        name = name.split("_", 1)[0]
    return name, round_n


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a per-thread connection. Migrations + legacy import run once globally."""
    global _INIT_DONE
    conn: sqlite3.Connection | None = getattr(_TLS, "conn", None)
    if conn is not None:
        return conn
    if db_path is None:
        db_path = _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    with _LOCK:
        if not _INIT_DONE:
            migrations.run(conn)
            _migrate_legacy_jsonl(conn)
            _INIT_DONE = True
    _TLS.conn = conn
    return conn


def _migrate_legacy_jsonl(conn: sqlite3.Connection, jsonl_path: Path | None = None) -> None:
    if jsonl_path is None:
        jsonl_path = _legacy_jsonl_path()
    if not jsonl_path.exists():
        return
    row = conn.execute("SELECT value FROM meta WHERE key='jsonl_imported'").fetchone()
    if row:
        return
    imported = 0
    with _LOCK:
        conn.execute("BEGIN")
        try:
            for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "council_round_summary":
                    _insert_round_summary(conn, entry)
                    imported += 1
                elif entry.get("task_id"):
                    _insert_legacy_call(conn, entry)
                    imported += 1
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('jsonl_imported', ?)",
                (datetime.now().isoformat() + f" rows={imported}",),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _insert_legacy_call(conn: sqlite3.Connection, entry: dict) -> None:
    command = entry.get("command", "")
    agent, round_n = _agent_and_round(command)
    last_lines = entry.get("last_lines")
    conn.execute(
        """INSERT OR IGNORE INTO calls
           (task_id, agent, round, command, council_id, status,
            started_at, completed_at, duration_s,
            prompt_text, result_text, output_chars, error, last_lines, legacy)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 1)""",
        (
            entry.get("task_id"), agent, round_n, command,
            entry.get("council_id"), entry.get("status", "unknown"),
            entry.get("ts"), entry.get("ts"),
            float(entry.get("duration_s") or 0.0),
            entry.get("preview"), entry.get("output_chars"),
            entry.get("error"),
            json.dumps(last_lines) if last_lines else None,
        ),
    )


def _insert_round_summary(conn: sqlite3.Connection, entry: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO council_rounds
           (council_id, round, ts, fastest, slowest, spread_s, agent_order)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.get("council_id"), int(entry.get("round") or 0),
            entry.get("ts"), entry.get("fastest"), entry.get("slowest"),
            entry.get("spread_s"),
            json.dumps(entry.get("agent_order") or []),
        ),
    )


def _container():
    from .adapters.repositories.container import default
    return default()


# --- Legacy façade — delegates to the repository container. ---

def record_task_running(task: Task, prompt: str | None = None) -> None:
    _container().calls.save_running(task, prompt)


def record_task_complete(task: Task, session_id: str | None = None) -> None:
    _container().calls.save_complete(task, session_id)


def record_council_round(council_id: str, round_num: int,
                         agent_timings: list[tuple[str, float, str]]) -> None:
    _container().councils.save_round(council_id, round_num, agent_timings)


def record_council_outcome(council_id: str, **kwargs) -> None:
    _container().councils.save_outcome(council_id, **kwargs)


def record_session_score(council_id: str, score: int, **kwargs) -> None:
    _container().scores.save_session_score(council_id, score, **kwargs)


def record_pairwise_agreements(
    council_id: str,
    rows: list[tuple[str, str, float, str | None]],
    *, source: str = "judge",
) -> None:
    _container().pairwise.save_batch(council_id, rows, source=source)


def record_position_delta(task_id: str, *, position_delta: float, position_label: str) -> None:
    _container().calls.update_position_delta(
        task_id, position_delta=position_delta, position_label=position_label,
    )


def record_council_anonymization(council_id: str, mapping: dict[str, str]) -> None:
    _container().councils.save_anonymization(council_id, mapping)


def get_council_anonymization(council_id: str) -> dict[str, str]:
    return _container().councils.get_anonymization(council_id)


def record_agent_score(council_id: str, agent: str, score: int, **kwargs) -> None:
    _container().scores.save_agent_score(council_id, agent, score, **kwargs)


def _log_failure(where: str, exc: Exception) -> None:
    print(f"[owlex.store] {where} failed: {exc}", file=sys.stderr, flush=True)


def _reset_for_tests() -> None:
    """Drop the per-thread connection and the one-time-init flag.

    Tests use this between cases when they relocate ``OWLEX_HOME``; production
    code never calls it.
    """
    global _INIT_DONE
    conn = getattr(_TLS, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _TLS.conn = None
    _INIT_DONE = False
    from .adapters.repositories.container import default as _default
    _default.cache_clear()
