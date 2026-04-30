"""FastAPI app + uvicorn entry point for the owlex dashboard.

Reads the canonical store at ``~/.owlex/owlex.db``. No mirror, no ingest job —
data appears as soon as the engine writes it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from statistics import median

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import store
from . import parsers

WEB_DIST = Path(__file__).parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Touch the store from the main thread to run schema + legacy import once.
    store.connect()
    app.state.write_lock = threading.Lock()
    try:
        yield
    finally:
        pass


app = FastAPI(title="owlex dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _conn() -> sqlite3.Connection:
    # Each FastAPI threadpool worker gets its own connection via store's TLS.
    return store.connect()


def _row_to_call(row: sqlite3.Row) -> dict:
    out = {
        "task_id": row["task_id"],
        "ts": row["completed_at"] or row["started_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "agent": row["agent"],
        "round": row["round"],
        "command": row["command"],
        "status": row["status"],
        "duration_s": row["duration_s"],
        "council_id": row["council_id"],
        "error": row["error"],
        "output_chars": row["output_chars"],
    }
    # Optional OTel/position fields — present only when the row has them.
    keys = row.keys()
    for opt in ("model", "input_tokens", "output_tokens", "finish_reason", "position_delta", "position_label"):
        if opt in keys and row[opt] is not None:
            out[opt] = row[opt]
    return out


@app.get("/api/stats")
def stats(since: str | None = None) -> dict:
    where, args = "WHERE status != 'running'", []
    if since:
        where += " AND COALESCE(completed_at, started_at) >= ?"
        args.append(since)

    rows = _conn().execute(
        f"""SELECT agent,
                   COUNT(*)                                            AS total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed,
                   AVG(duration_s)                                     AS avg_s
              FROM calls {where}
             GROUP BY agent
             ORDER BY total DESC""",
        args,
    ).fetchall()

    by_agent = []
    for r in rows:
        durs = [
            x["duration_s"]
            for x in _conn().execute(
                f"SELECT duration_s FROM calls WHERE agent=? AND {where[len('WHERE '):]}",
                [r["agent"], *args],
            ).fetchall()
            if x["duration_s"] is not None
        ]
        durs_sorted = sorted(durs)
        p50 = median(durs_sorted) if durs_sorted else 0.0
        p95 = durs_sorted[int(0.95 * (len(durs_sorted) - 1))] if durs_sorted else 0.0
        by_agent.append({
            "agent": r["agent"],
            "total": r["total"],
            "completed": r["completed"],
            "failed": r["failed"],
            "success_rate": (r["completed"] / r["total"]) if r["total"] else 0.0,
            "avg_s": round(r["avg_s"] or 0.0, 2),
            "p50_s": round(p50, 2),
            "p95_s": round(p95, 2),
        })

    totals = _conn().execute(
        f"""SELECT COUNT(*)                                            AS total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed
              FROM calls {where}""",
        args,
    ).fetchone()
    in_flight = _conn().execute("SELECT COUNT(*) AS c FROM calls WHERE status='running'").fetchone()["c"]

    return {
        "total": totals["total"] or 0,
        "completed": totals["completed"] or 0,
        "failed": totals["failed"] or 0,
        "in_flight": in_flight,
        "by_agent": by_agent,
    }


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    n = total
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@app.get("/api/leaderboard")
def leaderboard(since: str | None = None) -> dict:
    """Per-agent ranked stats with Wilson CIs and 7d activity sparkline."""
    where, args = "WHERE status != 'running'", []
    if since:
        where += " AND COALESCE(completed_at, started_at) >= ?"
        args.append(since)

    rows = _conn().execute(
        f"""SELECT agent,
                   COUNT(*)                                            AS total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed
              FROM calls {where}
             GROUP BY agent""",
        args,
    ).fetchall()

    out = []
    for r in rows:
        agent = r["agent"]
        durations = [
            x["duration_s"]
            for x in _conn().execute(
                f"SELECT duration_s FROM calls WHERE agent=? AND status='completed' AND duration_s IS NOT NULL "
                f"{('AND COALESCE(completed_at, started_at) >= ?' if since else '')}",
                ([agent, since] if since else [agent]),
            ).fetchall()
        ]
        durations.sort()
        n = len(durations)
        p50 = durations[n // 2] if n else 0.0
        p95 = durations[int(0.95 * (n - 1))] if n else 0.0
        lo, hi = _wilson_interval(r["completed"] or 0, r["total"] or 0)

        # Mean pairwise agreement involving this agent
        agreement = _conn().execute(
            "SELECT AVG(score) FROM pairwise_agreements WHERE agent_a = ? OR agent_b = ?",
            (agent, agent),
        ).fetchone()[0]

        # Blind rating average (-1..+1) from claude_blind ratings.
        blind_row = _conn().execute(
            "SELECT AVG(score) AS avg, COUNT(*) AS n FROM agent_scores "
            "WHERE agent = ? AND rater = 'claude_blind'",
            (agent,),
        ).fetchone()
        blind_avg = blind_row["avg"]
        blind_n = blind_row["n"] or 0

        # 7-day spark (call counts per day)
        spark_rows = _conn().execute(
            """SELECT substr(started_at, 1, 10) AS day, COUNT(*) AS c
                 FROM calls
                WHERE agent = ?
                  AND started_at >= date('now', '-7 days')
                GROUP BY day
                ORDER BY day ASC""",
            (agent,),
        ).fetchall()
        spark = [{"day": r2["day"], "calls": r2["c"]} for r2 in spark_rows]

        out.append({
            "agent": agent,
            "total": r["total"],
            "completed": r["completed"],
            "failed": r["failed"],
            "success_pct": (r["completed"] / r["total"]) * 100 if r["total"] else 0.0,
            "ci_low_pct": lo * 100,
            "ci_high_pct": hi * 100,
            "p50_s": round(p50, 2),
            "p95_s": round(p95, 2),
            "agreement_score": round(agreement, 2) if agreement is not None else None,
            "blind_rating_avg": round(blind_avg, 3) if blind_avg is not None else None,
            "blind_rating_n": blind_n,
            "spark": spark,
        })

    out.sort(key=lambda r: (-(r.get("agreement_score") or -1), -r["success_pct"]))
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return {"agents": out}


@app.get("/api/timeseries")
def timeseries(days: int = 30) -> dict:
    """Daily activity buckets per agent — drives the area chart."""
    rows = _conn().execute(
        f"""SELECT substr(started_at, 1, 10) AS day,
                   agent,
                   COUNT(*)                                            AS calls,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed,
                   AVG(duration_s)                                     AS avg_s
              FROM calls
             WHERE status != 'running'
               AND started_at >= date('now', ?)
             GROUP BY day, agent
             ORDER BY day ASC""",
        (f"-{int(days)} days",),
    ).fetchall()

    by_day: dict[str, dict] = {}
    for r in rows:
        d = by_day.setdefault(r["day"], {"day": r["day"], "total": 0})
        d[r["agent"]] = r["calls"]
        d["total"] += r["calls"]

    return {"days": list(by_day.values())}


@app.get("/api/latency_distribution")
def latency_distribution() -> dict:
    """Per-agent latency buckets for histogram/box."""
    rows = _conn().execute(
        """SELECT agent, duration_s
             FROM calls
            WHERE status='completed' AND duration_s > 0"""
    ).fetchall()
    by_agent: dict[str, list[float]] = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r["duration_s"])
    out = []
    for agent, durs in by_agent.items():
        durs.sort()
        n = len(durs)
        if n == 0:
            continue
        out.append({
            "agent": agent,
            "n": n,
            "min": round(durs[0], 1),
            "p25": round(durs[max(0, int(0.25 * (n - 1)))], 1),
            "p50": round(durs[int(0.50 * (n - 1))], 1),
            "p75": round(durs[int(0.75 * (n - 1))], 1),
            "p95": round(durs[int(0.95 * (n - 1))], 1),
            "max": round(durs[-1], 1),
        })
    out.sort(key=lambda x: x["p50"])
    return {"by_agent": out}


DURATION_BUCKETS = [
    ("0–10s",   0,    10),
    ("10–30s",  10,   30),
    ("30–60s",  30,   60),
    ("60–120s", 60,   120),
    ("120–300s",120,  300),
    ("300s+",   300,  10**9),
]


@app.get("/api/latency_heatmap")
def latency_heatmap(agent: str | None = None, days: int = 30) -> dict:
    """Hour-of-day × duration-bucket heatmap counts."""
    args: list = [f"-{int(days)} days"]
    where = "WHERE status='completed' AND duration_s > 0 AND started_at >= datetime('now', ?)"
    if agent:
        where += " AND agent = ?"
        args.append(agent)
    rows = _conn().execute(
        f"""SELECT CAST(strftime('%H', started_at) AS INTEGER) AS hour,
                   duration_s
              FROM calls
              {where}""",
        args,
    ).fetchall()
    grid = [[0] * 24 for _ in DURATION_BUCKETS]
    for r in rows:
        h = r["hour"] or 0
        d = r["duration_s"]
        for bi, (_, lo, hi) in enumerate(DURATION_BUCKETS):
            if lo <= d < hi:
                grid[bi][h] += 1
                break
    buckets = [{"label": label, "lo": lo, "hi": hi} for label, lo, hi in DURATION_BUCKETS]
    return {"hours": list(range(24)), "buckets": buckets, "grid": grid, "total": len(rows)}


@app.get("/api/agreement_distribution")
def agreement_distribution() -> dict:
    """Histogram of council agreement scores (0–5, half-step bins).

    Splits live vs backfilled so the UI can stack/footnote them separately.
    """
    rows = _conn().execute(
        "SELECT agreement_score, backfilled FROM council_outcomes WHERE agreement_score IS NOT NULL"
    ).fetchall()
    bins: dict[float, dict[str, int]] = {}
    for r in rows:
        bucket = round(float(r["agreement_score"]) * 2) / 2
        slot = bins.setdefault(bucket, {"live": 0, "backfilled": 0})
        slot["backfilled" if r["backfilled"] else "live"] += 1
    out = [
        {"bin": k, "live": bins[k]["live"], "backfilled": bins[k]["backfilled"], "count": bins[k]["live"] + bins[k]["backfilled"]}
        for k in sorted(bins.keys())
    ]
    total = sum(b["count"] for b in out)
    return {"buckets": out, "total": total, "live": sum(b["live"] for b in out), "backfilled": sum(b["backfilled"] for b in out)}


@app.get("/api/sessions")
def sessions(
    limit: int = 50,
    offset: int = 0,
    since: str | None = None,
    until: str | None = None,
    agreement_min: float | None = None,
    agreement_max: float | None = None,
) -> dict:
    having: list[str] = []
    args: list = []
    if agreement_min is not None:
        having.append("agreement_score >= ?")
        args.append(agreement_min)
    if agreement_max is not None:
        having.append("agreement_score <= ?")
        args.append(agreement_max)
    where_extra = ["c.council_id IS NOT NULL"]
    if since:
        where_extra.append("substr(c.started_at, 1, 10) >= ?")
        args.append(since[:10])
    if until:
        where_extra.append("substr(c.started_at, 1, 10) <= ?")
        args.append(until[:10])
    where_clause = "WHERE " + " AND ".join(where_extra)
    having_clause = ("HAVING " + " AND ".join(having)) if having else ""
    args.extend([limit, offset])
    rows = _conn().execute(
        f"""SELECT c.council_id,
                  MIN(c.started_at)                                       AS started_at,
                  MAX(COALESCE(c.completed_at, c.started_at))              AS ended_at,
                  COUNT(*)                                                AS calls,
                  SUM(CASE WHEN c.status='failed' THEN 1 ELSE 0 END)      AS failed,
                  SUM(CASE WHEN c.status='running' THEN 1 ELSE 0 END)     AS running,
                  GROUP_CONCAT(DISTINCT c.agent)                          AS agents,
                  o.agreement_score                                       AS agreement_score,
                  o.deliberation                                          AS deliberation
             FROM calls c
        LEFT JOIN council_outcomes o ON o.council_id = c.council_id
            {where_clause}
            GROUP BY c.council_id
            {having_clause}
            ORDER BY ended_at DESC
            LIMIT ? OFFSET ?""",
        args,
    ).fetchall()
    return {
        "sessions": [
            {
                "council_id": r["council_id"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "calls": r["calls"],
                "failed": r["failed"],
                "running": r["running"],
                "agents": (r["agents"] or "").split(","),
                "agreement_score": r["agreement_score"],
                "deliberation": bool(r["deliberation"]) if r["deliberation"] is not None else None,
            }
            for r in rows
        ]
    }


@app.get("/api/sessions/{council_id}/tree")
def session_tree(council_id: str) -> dict:
    """Hierarchical session view for the Trace Gantt: session → rounds → calls → skills."""
    calls = _conn().execute(
        """SELECT task_id, agent, round, status, started_at, completed_at, duration_s
             FROM calls
            WHERE council_id = ?
            ORDER BY started_at ASC""",
        (council_id,),
    ).fetchall()
    if not calls:
        raise HTTPException(404, "session not found")
    task_ids = [c["task_id"] for c in calls]
    placeholders = ",".join("?" * len(task_ids))
    skills_by_task: dict[str, list[dict]] = {tid: [] for tid in task_ids}
    if task_ids:
        for r in _conn().execute(
            f"""SELECT task_id, seq, ts, kind, name, args_summary
                 FROM skill_invocations
                WHERE task_id IN ({placeholders})
                ORDER BY task_id, seq ASC""",
            task_ids,
        ):
            skills_by_task[r["task_id"]].append({
                "seq": r["seq"], "ts": r["ts"], "kind": r["kind"], "name": r["name"], "args_summary": r["args_summary"],
            })

    rounds: dict[int, list[dict]] = {}
    starts: list[str] = []
    ends: list[str] = []
    for c in calls:
        starts.append(c["started_at"])
        if c["completed_at"]:
            ends.append(c["completed_at"])
        rounds.setdefault(c["round"], []).append({
            "task_id": c["task_id"],
            "agent": c["agent"],
            "status": c["status"],
            "started_at": c["started_at"],
            "completed_at": c["completed_at"],
            "duration_s": c["duration_s"],
            "skills": skills_by_task.get(c["task_id"], []),
        })

    return {
        "council_id": council_id,
        "started_at": min(starts) if starts else None,
        "ended_at": max(ends) if ends else None,
        "rounds": [{"round": k, "calls": v} for k, v in sorted(rounds.items())],
    }


@app.get("/api/sessions/{council_id}/timeline")
def session_timeline(council_id: str) -> dict:
    """Per-agent R1↔R2 timeline with position deltas — drives the vote-flip swimlanes."""
    rows = _conn().execute(
        """SELECT agent, round, task_id, started_at, completed_at, duration_s,
                  position_delta, position_label, status
             FROM calls
            WHERE council_id = ?
            ORDER BY round ASC, started_at ASC""",
        (council_id,),
    ).fetchall()
    by_agent: dict[str, dict] = {}
    for r in rows:
        slot = by_agent.setdefault(r["agent"], {"agent": r["agent"], "r1": None, "r2": None})
        rec = {
            "task_id": r["task_id"],
            "started_at": r["started_at"],
            "completed_at": r["completed_at"],
            "duration_s": r["duration_s"],
            "status": r["status"],
        }
        if r["round"] == 1:
            slot["r1"] = rec
        elif r["round"] == 2:
            rec["position_delta"] = r["position_delta"]
            rec["position_label"] = r["position_label"]
            slot["r2"] = rec
    return {"council_id": council_id, "agents": list(by_agent.values())}


@app.get("/api/sessions/{council_id}/matrix")
def session_matrix(council_id: str) -> dict:
    """Pairwise agreement matrix for a council. Returns participants + symmetric grid."""
    rows = _conn().execute(
        "SELECT agent_a, agent_b, score, source, reason FROM pairwise_agreements WHERE council_id=?",
        (council_id,),
    ).fetchall()
    if not rows:
        return {"participants": [], "cells": [], "sources": [], "reasons": []}
    agents: set[str] = set()
    for r in rows:
        agents.add(r["agent_a"])
        agents.add(r["agent_b"])
    participants = sorted(agents)
    n = len(participants)
    idx = {a: i for i, a in enumerate(participants)}
    cells: list[list[float | None]] = [[None] * n for _ in range(n)]
    sources: list[list[str | None]] = [[None] * n for _ in range(n)]
    reasons: list[list[str | None]] = [[None] * n for _ in range(n)]
    for i in range(n):
        cells[i][i] = 5.0  # diagonal
        sources[i][i] = "self"
    for r in rows:
        i, j = idx[r["agent_a"]], idx[r["agent_b"]]
        cells[i][j] = cells[j][i] = float(r["score"])
        sources[i][j] = sources[j][i] = r["source"]
        reasons[i][j] = reasons[j][i] = r["reason"]
    return {"participants": participants, "cells": cells, "sources": sources, "reasons": reasons}


@app.post("/api/sessions/{council_id}/score")
def score_session(council_id: str, body: dict = Body(...)) -> dict:
    score = body.get("score")
    if score not in (-1, 1):
        raise HTTPException(400, "score must be -1 or +1")
    label = body.get("label")
    comment = body.get("comment")
    rater = body.get("rater") or "human"
    store.record_session_score(council_id, score, rater=rater, label=label, comment=comment)
    return {"ok": True}


@app.get("/api/scores/timeseries")
def scores_timeseries(days: int = 30, rater: str | None = None) -> dict:
    """Whole-council human ratings over time, optionally filtered by rater."""
    args: list = [f"-{int(days)} days"]
    where = "WHERE ts >= date('now', ?)"
    if rater:
        where += " AND rater = ?"
        args.append(rater)
    rows = _conn().execute(
        f"""SELECT substr(ts, 1, 10) AS day,
                  SUM(CASE WHEN score = 1  THEN 1 ELSE 0 END) AS up,
                  SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) AS down
             FROM session_scores
            {where}
            GROUP BY day
            ORDER BY day ASC""",
        args,
    ).fetchall()
    return {"days": [{"day": r["day"], "up": r["up"], "down": r["down"]} for r in rows]}


@app.get("/api/sessions/{council_id}/agent_scores")
def session_agent_scores(council_id: str) -> dict:
    """Per-agent blind ratings for a council. Empty list if council was not blind-rated."""
    rows = _conn().execute(
        """SELECT agent, rater, score, dimensions, reason, ts
             FROM agent_scores
            WHERE council_id = ?
            ORDER BY ts ASC""",
        (council_id,),
    ).fetchall()
    out = []
    for r in rows:
        dims = None
        if r["dimensions"]:
            try:
                dims = json.loads(r["dimensions"])
            except json.JSONDecodeError:
                dims = None
        out.append({
            "agent": r["agent"],
            "rater": r["rater"],
            "score": r["score"],
            "dimensions": dims,
            "reason": r["reason"],
            "ts": r["ts"],
        })
    return {"council_id": council_id, "ratings": out}


@app.get("/api/leaderboard/by_model")
def leaderboard_by_model() -> dict:
    """Per-(agent, model) blind-rating breakdown — exposes substitution effects.

    A council seat (agent column) may be substituted to run a different model.
    This endpoint shows blind ratings grouped by the *resolved model identifier*
    so operators can see, e.g., codex-runner-with-gpt-5-codex separately from
    codex-runner-with-default.
    """
    rows = _conn().execute(
        """SELECT c.agent, c.model,
                  COUNT(s.score)              AS n,
                  AVG(s.score)                AS blind_avg,
                  AVG(c.duration_s)           AS avg_s
             FROM calls c
        LEFT JOIN agent_scores s
               ON s.council_id = c.council_id
              AND s.agent = c.agent
              AND s.rater = 'claude_blind'
            WHERE c.status = 'completed'
            GROUP BY c.agent, COALESCE(c.model, '<default>')
            ORDER BY n DESC, c.agent ASC"""
    ).fetchall()
    return {
        "rows": [
            {
                "agent": r["agent"],
                "model": r["model"] or "<default>",
                "rated_n": r["n"] or 0,
                "blind_avg": round(r["blind_avg"], 3) if r["blind_avg"] is not None else None,
                "avg_duration_s": round(r["avg_s"] or 0.0, 1),
            }
            for r in rows
        ]
    }


@app.get("/api/integrity/blind_vs_agreement")
def integrity_blind_vs_agreement() -> dict:
    """Blinding-integrity check: per-agent mean blind rating vs mean pairwise agreement.

    A large positive deviation for one agent (e.g. claudeor) suggests the orchestrator
    can identify the agent through stylistic fingerprinting despite anonymization.
    """
    blind_rows = _conn().execute(
        """SELECT agent, AVG(score) AS blind_avg, COUNT(*) AS n
             FROM agent_scores
            WHERE rater = 'claude_blind'
            GROUP BY agent"""
    ).fetchall()
    out = []
    for r in blind_rows:
        agreement = _conn().execute(
            "SELECT AVG(score) FROM pairwise_agreements WHERE agent_a = ? OR agent_b = ?",
            (r["agent"], r["agent"]),
        ).fetchone()[0]
        # Normalize agreement (1..5 → -1..+1) so it's directly comparable to blind_avg.
        agreement_norm = (agreement - 3) / 2 if agreement is not None else None
        out.append({
            "agent": r["agent"],
            "blind_avg": round(r["blind_avg"], 3),
            "blind_n": r["n"],
            "agreement_norm": round(agreement_norm, 3) if agreement_norm is not None else None,
            "deviation": (
                round(r["blind_avg"] - agreement_norm, 3)
                if agreement_norm is not None else None
            ),
        })
    return {"agents": out}


@app.get("/api/sessions/{council_id}")
def session_detail(council_id: str) -> dict:
    calls = _conn().execute(
        "SELECT * FROM calls WHERE council_id=? ORDER BY started_at ASC",
        (council_id,),
    ).fetchall()
    if not calls:
        raise HTTPException(404, "session not found")
    summaries = _conn().execute(
        "SELECT * FROM council_rounds WHERE council_id=? ORDER BY round ASC",
        (council_id,),
    ).fetchall()
    outcome = _conn().execute(
        "SELECT * FROM council_outcomes WHERE council_id=?",
        (council_id,),
    ).fetchone()
    score_rows = _conn().execute(
        "SELECT score, label, comment, rater, ts FROM session_scores WHERE council_id=? ORDER BY ts ASC",
        (council_id,),
    ).fetchall()
    # Per-agent R1/R2 answer panels for the side-by-side view.
    answer_rows = _conn().execute(
        """SELECT task_id, agent, round, status, duration_s, started_at,
                  result_text, error, position_delta, position_label
             FROM calls
            WHERE council_id = ?
            ORDER BY agent ASC, round ASC""",
        (council_id,),
    ).fetchall()
    answers: dict[str, dict] = {}
    for r in answer_rows:
        slot = answers.setdefault(r["agent"], {"agent": r["agent"], "r1": None, "r2": None})
        rec = {
            "task_id": r["task_id"],
            "status": r["status"],
            "duration_s": r["duration_s"],
            "started_at": r["started_at"],
            "result_text": r["result_text"],
            "error": r["error"],
        }
        if r["round"] == 2:
            rec["position_delta"] = r["position_delta"]
            rec["position_label"] = r["position_label"]
        slot["r1" if r["round"] == 1 else "r2"] = rec
    return {
        "council_id": council_id,
        "calls": [_row_to_call(c) for c in calls],
        "answers": list(answers.values()),
        "scores": [
            {"score": r["score"], "label": r["label"], "comment": r["comment"], "rater": r["rater"], "ts": r["ts"]}
            for r in score_rows
        ],
        "rounds": [
            {
                "round": s["round"],
                "ts": s["ts"],
                "fastest": s["fastest"],
                "slowest": s["slowest"],
                "spread_s": s["spread_s"],
                "agent_order": json.loads(s["agent_order"] or "[]"),
            }
            for s in summaries
        ],
        "outcome": (
            {
                "completed_at": outcome["completed_at"],
                "total_duration_s": outcome["total_duration_s"],
                "agreement_score": outcome["agreement_score"],
                "agreement_reason": outcome["agreement_reason"],
                "progress_log": json.loads(outcome["progress_log"] or "[]"),
                "claude_opinion": outcome["claude_opinion"],
                "deliberation": bool(outcome["deliberation"]),
                "critique": bool(outcome["critique"]),
                "rounds": outcome["rounds"],
            }
            if outcome
            else None
        ),
    }


@app.get("/api/calls")
def calls(
    agent: str | None = None,
    status: str | None = None,
    council_id: str | None = None,
    q: str | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    hour: int | None = None,
    tool: str | None = None,
    skill: str | None = None,
    sort: str = "started_desc",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    clauses, args = [], []
    if agent:
        clauses.append("c.agent = ?")
        args.append(agent)
    if status:
        clauses.append("c.status = ?")
        args.append(status)
    if council_id:
        clauses.append("c.council_id = ?")
        args.append(council_id)
    if q:
        clauses.append("(c.result_text LIKE ? OR c.prompt_text LIKE ? OR c.error LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if duration_min is not None:
        clauses.append("c.duration_s >= ?")
        args.append(duration_min)
    if duration_max is not None:
        clauses.append("c.duration_s < ?")
        args.append(duration_max)
    if hour is not None:
        clauses.append("CAST(strftime('%H', c.started_at) AS INTEGER) = ?")
        args.append(int(hour))
    if tool:
        clauses.append(
            "EXISTS (SELECT 1 FROM skill_invocations s WHERE s.task_id = c.task_id AND s.kind='tool' AND s.name = ?)"
        )
        args.append(tool)
    if skill:
        clauses.append(
            "EXISTS (SELECT 1 FROM skill_invocations s WHERE s.task_id = c.task_id AND s.kind='skill' AND s.name = ?)"
        )
        args.append(skill)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = {
        "started_asc": "c.started_at ASC",
        "started_desc": "c.started_at DESC",
        "duration_asc": "c.duration_s ASC",
        "duration_desc": "c.duration_s DESC",
    }.get(sort, "c.started_at DESC")
    rows = _conn().execute(
        f"SELECT c.* FROM calls c {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return {"calls": [_row_to_call(r) for r in rows]}


@app.get("/api/skills/names")
def skill_names(agent: str | None = None) -> dict:
    """Distinct tool/skill names with usage counts. Powers the filter dropdown."""
    args: list = []
    join = ""
    where = "WHERE 1=1"
    if agent:
        join = "JOIN calls c ON c.task_id = s.task_id"
        where += " AND c.agent = ?"
        args.append(agent)
    rows = _conn().execute(
        f"""SELECT s.name, s.kind, COUNT(*) AS n
              FROM skill_invocations s
              {join}
              {where}
             GROUP BY s.name, s.kind
             ORDER BY n DESC""",
        args,
    ).fetchall()
    tools = [{"name": r["name"], "count": r["n"]} for r in rows if r["kind"] == "tool"]
    skills = [{"name": r["name"], "count": r["n"]} for r in rows if r["kind"] == "skill"]
    return {"tools": tools, "skills": skills}


@app.get("/api/calls/in_flight")
def in_flight() -> dict:
    rows = _conn().execute(
        "SELECT * FROM calls WHERE status='running' ORDER BY started_at ASC"
    ).fetchall()
    now = datetime.now()
    out = []
    for r in rows:
        started = datetime.fromisoformat(r["started_at"])
        out.append({
            **_row_to_call(r),
            "elapsed_s": round((now - started).total_seconds(), 1),
        })
    return {"calls": out}


def _load_skills(task_id: str, agent: str, ts: str, session_id: str | None) -> list[dict]:
    conn = _conn()
    cached = conn.execute(
        "SELECT 1 FROM skill_parse_state WHERE task_id=?", (task_id,)
    ).fetchone()
    if not cached:
        invocations = parsers.parse_for(agent, task_id, ts, session_id=session_id)
        with app.state.write_lock:
            conn.execute("BEGIN")
            try:
                for i, inv in enumerate(invocations):
                    conn.execute(
                        """INSERT INTO skill_invocations
                           (task_id, seq, ts, kind, name, args_summary)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (task_id, i, inv.get("ts"), inv["kind"], inv["name"], inv.get("args_summary")),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO skill_parse_state(task_id, parsed_at, found) VALUES (?, ?, ?)",
                    (task_id, datetime.now().isoformat(), len(invocations)),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    rows = conn.execute(
        "SELECT seq, ts, kind, name, args_summary FROM skill_invocations "
        "WHERE task_id=? ORDER BY seq ASC",
        (task_id,),
    ).fetchall()
    return [
        {"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], "name": r["name"], "args_summary": r["args_summary"]}
        for r in rows
    ]


@app.get("/api/calls/{task_id}")
def call_detail(task_id: str) -> dict:
    row = _conn().execute("SELECT * FROM calls WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "call not found")
    last_lines = json.loads(row["last_lines"]) if row["last_lines"] else []
    skills = (
        _load_skills(task_id, row["agent"], row["completed_at"] or row["started_at"], row["session_id"])
        if row["status"] != "running"
        else []
    )
    return {
        **_row_to_call(row),
        "prompt_text": row["prompt_text"],
        "result_text": row["result_text"],
        "last_lines": last_lines,
        "session_id": row["session_id"],
        "legacy": bool(row["legacy"]) if "legacy" in row.keys() else False,
        "skills": skills,
    }


# --- Static frontend (built React app) ---
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    def root_index():
        return FileResponse(WEB_DIST / "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = WEB_DIST / full_path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
else:
    @app.get("/")
    def root_index_dev():
        return {
            "message": "owlex dashboard API ready. Run `npm run build` in owlex/dashboard/web to bundle the UI.",
            "endpoints": ["/api/stats", "/api/sessions", "/api/calls", "/api/calls/in_flight"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(prog="owlex-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(
        "owlex.dashboard.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
