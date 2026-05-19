"""Repository over pairwise_agreements."""
from __future__ import annotations

from datetime import datetime

from ._base import SqliteRepository


class SqlitePairwiseRepository(SqliteRepository):
    def save_batch(
        self, council_id: str,
        rows: list[tuple[str, str, float, str | None]],
        *, source: str = "judge",
    ) -> None:
        """rows: list of (agent_a, agent_b, score, reason). Pair stored sorted."""
        if not rows:
            return
        try:
            ts = datetime.now().isoformat()
            with self._lock:
                conn = self._conn()
                conn.execute("BEGIN")
                try:
                    for a, b, score, reason in rows:
                        a_, b_ = sorted([a, b])
                        conn.execute(
                            """INSERT OR REPLACE INTO pairwise_agreements
                               (council_id, agent_a, agent_b, score, reason, source, computed_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (council_id, a_, b_, float(score), reason, source, ts),
                        )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        except Exception as e:
            self._log_failure("save_batch", e)
