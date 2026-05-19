"""Repository over session_scores and agent_scores."""
from __future__ import annotations

import json
from datetime import datetime

from ._base import SqliteRepository


class SqliteScoreRepository(SqliteRepository):
    def save_session_score(
        self, council_id: str, score: int, *,
        rater: str = "human",
        label: str | None = None,
        comment: str | None = None,
    ) -> None:
        if score not in (-1, 1):
            raise ValueError("score must be -1 or +1")
        try:
            with self._lock:
                self._conn().execute(
                    """INSERT OR REPLACE INTO session_scores
                       (council_id, rater, score, label, comment, ts)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (council_id, rater, int(score), label, comment, datetime.now().isoformat()),
                )
        except Exception as e:
            self._log_failure("save_session_score", e)

    def save_agent_score(
        self, council_id: str, agent: str, score: int, *,
        rater: str = "claude_blind",
        dimensions: dict | None = None,
        reason: str | None = None,
    ) -> None:
        if score not in (-1, 1):
            raise ValueError("score must be -1 or +1")
        try:
            with self._lock:
                self._conn().execute(
                    """INSERT OR REPLACE INTO agent_scores
                       (council_id, agent, rater, score, dimensions, reason, ts)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        council_id, agent, rater, int(score),
                        json.dumps(dimensions) if dimensions else None,
                        reason, datetime.now().isoformat(),
                    ),
                )
        except Exception as e:
            self._log_failure("save_agent_score", e)
