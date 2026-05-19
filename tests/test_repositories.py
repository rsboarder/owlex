"""Repository-layer tests using in-memory SQLite."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from owlex.adapters.db import migrations
from owlex.adapters.repositories import (
    SqliteCallRepository,
    SqliteCouncilRepository,
    SqlitePairwiseRepository,
    SqliteScoreRepository,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    migrations.run(c)
    return c


@pytest.fixture
def repos(conn):
    lock = threading.RLock()
    return SimpleNamespace(
        calls=SqliteCallRepository(lambda: conn, lock),
        councils=SqliteCouncilRepository(lambda: conn, lock),
        scores=SqliteScoreRepository(lambda: conn, lock),
        pairwise=SqlitePairwiseRepository(lambda: conn, lock),
    )


def _task(task_id="t1", command="council_codex", council_id="C1",
          status="completed", result="ok", duration_s=2.5):
    start = datetime.now() - timedelta(seconds=duration_s)
    return SimpleNamespace(
        task_id=task_id,
        command=command,
        council_id=council_id,
        status=status,
        start_time=start,
        completion_time=datetime.now(),
        result=result,
        error=None,
        output_lines=["a", "b"],
        model="opus-4.7",
    )


def test_call_repo_save_running_then_complete(repos, conn):
    t = _task()
    repos.calls.save_running(t, prompt="hi")
    repos.calls.save_complete(t, session_id="sess-1")
    row = conn.execute("SELECT * FROM calls WHERE task_id='t1'").fetchone()
    assert row["status"] == "completed"
    assert row["session_id"] == "sess-1"
    assert row["agent"] == "codex"
    assert row["round"] == 1
    assert row["model"] == "opus-4.7"


def test_call_repo_position_delta_update(repos, conn):
    t = _task(task_id="t2", command="council_codex_delib")
    repos.calls.save_complete(t)
    repos.calls.update_position_delta("t2", position_delta=0.95, position_label="major")
    row = conn.execute("SELECT position_delta, position_label FROM calls WHERE task_id='t2'").fetchone()
    assert row["position_delta"] == 0.95
    assert row["position_label"] == "major"


def test_council_repo_outcome_and_anonymization(repos, conn):
    repos.councils.save_outcome(
        "C1", total_duration_s=12.34, rounds=2,
        deliberation=True, critique=True,
        agreement_score=3.4, agreement_reason="overlap",
        progress_log=["a"], claude_opinion="x",
    )
    repos.councils.save_anonymization("C1", {"A": "codex", "B": "gemini"})

    row = conn.execute("SELECT * FROM council_outcomes WHERE council_id='C1'").fetchone()
    assert row["deliberation"] == 1
    assert row["rounds"] == 2

    assert repos.councils.get_anonymization("C1") == {"A": "codex", "B": "gemini"}
    assert repos.councils.get_anonymization("nope") == {}


def test_pairwise_repo_sorts_pair_keys(repos, conn):
    repos.pairwise.save_batch("C1", [("gemini", "codex", 4.0, "r")], source="judge")
    row = conn.execute("SELECT agent_a, agent_b FROM pairwise_agreements").fetchone()
    assert row["agent_a"] == "codex"
    assert row["agent_b"] == "gemini"


def test_score_repo_validates_score(repos):
    with pytest.raises(ValueError):
        repos.scores.save_session_score("C1", 0)
    with pytest.raises(ValueError):
        repos.scores.save_agent_score("C1", "codex", 2)


def test_call_repo_aggregated_avoids_n_plus_1(repos, conn):
    """Single grouped scan is enough — assert query count is bounded."""
    for i in range(3):
        t = _task(task_id=f"t{i}", command=f"council_codex", duration_s=1.0 + i)
        repos.calls.save_complete(t)
    for i in range(2):
        t = _task(task_id=f"g{i}", command="council_gemini", duration_s=2.0 + i)
        repos.calls.save_complete(t)

    repos.pairwise.save_batch("C1", [("codex", "gemini", 4.5, None)])
    repos.scores.save_agent_score("C1", "codex", 1, rater="claude_blind")

    queries: list[str] = []
    conn.set_trace_callback(queries.append)
    out = repos.calls.list_aggregated_by_agent()
    conn.set_trace_callback(None)

    selects = [q for q in queries if q.strip().upper().startswith("SELECT")]
    # 5 queries: totals, durations, agreement, blind, spark — independent of #agents.
    assert len(selects) == 5

    by_agent = {r["agent"]: r for r in out}
    assert by_agent["codex"]["total"] == 3
    assert by_agent["gemini"]["total"] == 2
    assert by_agent["codex"]["agreement_score"] == 4.5
    assert by_agent["codex"]["blind_rating_n"] == 1
