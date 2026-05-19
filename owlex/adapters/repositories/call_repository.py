"""Repository over the ``calls`` table — all per-agent task rows."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...models import Task
from ._base import SqliteRepository


def _agent_and_round(command: str) -> tuple[str, int]:
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


class SqliteCallRepository(SqliteRepository):
    def save_running(self, task: Task, prompt: str | None = None) -> None:
        try:
            agent, round_n = _agent_and_round(task.command)
            model = getattr(task, "model", None)
            with self._lock:
                self._conn().execute(
                    """INSERT INTO calls
                       (task_id, agent, round, command, council_id, status,
                        started_at, prompt_text, model)
                       VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status      = excluded.status,
                         prompt_text = COALESCE(excluded.prompt_text, calls.prompt_text),
                         model       = COALESCE(excluded.model,       calls.model)""",
                    (
                        task.task_id, agent, round_n, task.command,
                        task.council_id, task.start_time.isoformat(), prompt, model,
                    ),
                )
        except Exception as e:
            self._log_failure("save_running", e)

    def save_complete(self, task: Task, session_id: str | None = None) -> None:
        try:
            completed_at = task.completion_time or datetime.now()
            duration = round((completed_at - task.start_time).total_seconds(), 3)
            last_lines = task.output_lines[-10:] if task.output_lines else None
            agent, round_n = _agent_and_round(task.command)
            model = getattr(task, "model", None)
            with self._lock:
                self._conn().execute(
                    """INSERT INTO calls
                       (task_id, agent, round, command, council_id, status,
                        started_at, completed_at, duration_s,
                        prompt_text, result_text, output_chars, error, last_lines, session_id, model)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status       = excluded.status,
                         completed_at = excluded.completed_at,
                         duration_s   = excluded.duration_s,
                         result_text  = excluded.result_text,
                         output_chars = excluded.output_chars,
                         error        = excluded.error,
                         last_lines   = excluded.last_lines,
                         session_id   = COALESCE(excluded.session_id, calls.session_id),
                         model        = COALESCE(excluded.model,       calls.model)""",
                    (
                        task.task_id, agent, round_n, task.command, task.council_id,
                        task.status, task.start_time.isoformat(), completed_at.isoformat(),
                        duration, task.result,
                        len(task.result) if task.result else None,
                        task.error, json.dumps(last_lines) if last_lines else None,
                        session_id, model,
                    ),
                )
        except Exception as e:
            self._log_failure("save_complete", e)

    def update_position_delta(
        self, task_id: str, *, position_delta: float, position_label: str,
    ) -> None:
        try:
            with self._lock:
                self._conn().execute(
                    "UPDATE calls SET position_delta=?, position_label=? WHERE task_id=?",
                    (round(float(position_delta), 4), position_label, task_id),
                )
        except Exception as e:
            self._log_failure("update_position_delta", e)

    def list_aggregated_by_agent(self, since: str | None = None) -> list[dict[str, Any]]:
        """Per-agent stats in O(1) queries (was N+1 in /api/leaderboard).

        Returns one row per agent with totals, all completed durations (for
        client-side percentile computation), mean pairwise agreement, blind
        rating average + count, and a 7-day daily call-count sparkline.
        """
        where, args = "WHERE status != 'running'", []
        if since:
            where += " AND COALESCE(completed_at, started_at) >= ?"
            args.append(since)

        # 1) totals/completed/failed per agent.
        rows = self._conn().execute(
            f"""SELECT agent,
                       COUNT(*)                                            AS total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed
                  FROM calls {where}
                 GROUP BY agent""",
            args,
        ).fetchall()

        # 2) all completed durations per agent (one query, grouped client-side).
        dur_rows = self._conn().execute(
            f"""SELECT agent, duration_s FROM calls
                 {where} AND status='completed' AND duration_s IS NOT NULL""",
            args,
        ).fetchall()
        durations: dict[str, list[float]] = {}
        for r in dur_rows:
            durations.setdefault(r["agent"], []).append(r["duration_s"])

        # 3) mean pairwise agreement involving each agent.
        agreement_rows = self._conn().execute(
            """SELECT agent, AVG(score) AS avg FROM (
                   SELECT agent_a AS agent, score FROM pairwise_agreements
                   UNION ALL
                   SELECT agent_b AS agent, score FROM pairwise_agreements
               ) GROUP BY agent"""
        ).fetchall()
        agreement = {r["agent"]: r["avg"] for r in agreement_rows}

        # 4) blind ratings.
        blind_rows = self._conn().execute(
            """SELECT agent, AVG(score) AS avg, COUNT(*) AS n
                 FROM agent_scores
                WHERE rater = 'claude_blind'
             GROUP BY agent"""
        ).fetchall()
        blind = {r["agent"]: (r["avg"], r["n"] or 0) for r in blind_rows}

        # 5) 7-day spark per agent.
        spark_rows = self._conn().execute(
            """SELECT agent, substr(started_at, 1, 10) AS day, COUNT(*) AS c
                 FROM calls
                WHERE started_at >= date('now', '-7 days')
             GROUP BY agent, day
             ORDER BY agent, day ASC"""
        ).fetchall()
        spark: dict[str, list[dict[str, Any]]] = {}
        for r in spark_rows:
            spark.setdefault(r["agent"], []).append({"day": r["day"], "calls": r["c"]})

        out = []
        for r in rows:
            agent = r["agent"]
            blind_avg, blind_n = blind.get(agent, (None, 0))
            out.append({
                "agent": agent,
                "total": r["total"],
                "completed": r["completed"],
                "failed": r["failed"],
                "durations": sorted(durations.get(agent, [])),
                "agreement_score": agreement.get(agent),
                "blind_rating_avg": blind_avg,
                "blind_rating_n": blind_n,
                "spark": spark.get(agent, []),
            })
        return out

    def find_by_task_id(self, task_id: str) -> dict | None:
        row = self._conn().execute(
            "SELECT * FROM calls WHERE task_id=?", (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_by_council(self, council_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM calls WHERE council_id=? ORDER BY started_at",
            (council_id,),
        ).fetchall()
        return [dict(r) for r in rows]
