"""Repository over council_outcomes / council_rounds / council_anonymization."""
from __future__ import annotations

import json
from datetime import datetime

from ._base import SqliteRepository


class SqliteCouncilRepository(SqliteRepository):
    def save_round(
        self, council_id: str, round_num: int,
        agent_timings: list[tuple[str, float, str]],
    ) -> None:
        if not agent_timings:
            return
        try:
            ranking = [f"{name}={dur:.1f}s({st})" for name, dur, st in agent_timings]
            with self._lock:
                self._conn().execute(
                    """INSERT OR REPLACE INTO council_rounds
                       (council_id, round, ts, fastest, slowest, spread_s, agent_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        council_id, round_num, datetime.now().isoformat(),
                        agent_timings[0][0], agent_timings[-1][0],
                        round(agent_timings[-1][1] - agent_timings[0][1], 1)
                        if len(agent_timings) >= 2 else 0,
                        json.dumps(ranking),
                    ),
                )
        except Exception as e:
            self._log_failure("save_round", e)

    def save_outcome(
        self, council_id: str, *,
        total_duration_s: float, rounds: int,
        deliberation: bool, critique: bool,
        agreement_score: float | None = None,
        agreement_reason: str | None = None,
        progress_log: list[str] | None = None,
        claude_opinion: str | None = None,
        backfilled: bool = False,
        completed_at: str | None = None,
    ) -> None:
        try:
            with self._lock:
                self._conn().execute(
                    """INSERT OR REPLACE INTO council_outcomes
                       (council_id, completed_at, total_duration_s,
                        agreement_score, agreement_reason, progress_log,
                        claude_opinion, deliberation, critique, rounds, backfilled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        council_id,
                        completed_at or datetime.now().isoformat(),
                        round(total_duration_s, 3),
                        agreement_score, agreement_reason,
                        json.dumps(progress_log or []),
                        claude_opinion,
                        1 if deliberation else 0,
                        1 if critique else 0,
                        rounds,
                        1 if backfilled else 0,
                    ),
                )
        except Exception as e:
            self._log_failure("save_outcome", e)

    def save_anonymization(self, council_id: str, mapping: dict[str, str]) -> None:
        if not mapping:
            return
        try:
            ts = datetime.now().isoformat()
            with self._lock:
                conn = self._conn()
                conn.execute("BEGIN")
                try:
                    for label, agent in mapping.items():
                        conn.execute(
                            """INSERT OR REPLACE INTO council_anonymization
                               (council_id, label, agent, created_at)
                               VALUES (?, ?, ?, ?)""",
                            (council_id, label, agent, ts),
                        )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        except Exception as e:
            self._log_failure("save_anonymization", e)

    def get_anonymization(self, council_id: str) -> dict[str, str]:
        try:
            rows = self._conn().execute(
                "SELECT label, agent FROM council_anonymization WHERE council_id=?",
                (council_id,),
            ).fetchall()
            return {r["label"]: r["agent"] for r in rows}
        except Exception as e:
            self._log_failure("get_anonymization", e)
            return {}
