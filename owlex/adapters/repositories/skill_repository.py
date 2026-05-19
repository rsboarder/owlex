"""Repository over skill_invocations and skill_parse_state.

The original code in ``dashboard.parsers`` writes these rows directly via
raw SQL. This repo provides the same behavior so future call sites can switch
without changing semantics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ._base import SqliteRepository


class SqliteSkillRepository(SqliteRepository):
    def record_parse(self, task_id: str, found: int) -> None:
        try:
            with self._lock:
                self._conn().execute(
                    """INSERT OR REPLACE INTO skill_parse_state
                       (task_id, parsed_at, found) VALUES (?, ?, ?)""",
                    (task_id, datetime.now().isoformat(), int(found)),
                )
        except Exception as e:
            self._log_failure("record_parse", e)

    def insert_invocations(
        self, task_id: str,
        invocations: Iterable[tuple[int, str | None, str, str, str | None]],
    ) -> None:
        """invocations: iterable of (seq, ts, kind, name, args_summary)."""
        try:
            with self._lock:
                conn = self._conn()
                conn.execute("BEGIN")
                try:
                    conn.execute("DELETE FROM skill_invocations WHERE task_id=?", (task_id,))
                    for seq, ts, kind, name, args in invocations:
                        conn.execute(
                            """INSERT INTO skill_invocations
                               (task_id, seq, ts, kind, name, args_summary)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (task_id, seq, ts, kind, name, args),
                        )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        except Exception as e:
            self._log_failure("insert_invocations", e)
