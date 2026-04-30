"""Blind per-agent rating: anonymization persistence + agent_scores writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from owlex import store
from owlex.models import AgentResponse, CouncilRound
from owlex.prompts import anonymize_round_responses


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point the store at an isolated SQLite file for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    monkeypatch.setattr(store, "_INIT_DONE", False)
    monkeypatch.setattr(store, "_TLS", store.threading.local())
    conn = store.connect(db_path=db_path)
    yield conn


class TestAnonymizeRoundResponses:
    def _round(self):
        return CouncilRound(
            codex=AgentResponse(agent="codex", status="completed", content="a", task_id="t1"),
            gemini=AgentResponse(agent="gemini", status="completed", content="b", task_id="t2"),
            claudeor=AgentResponse(agent="claudeor", status="completed", content="c", task_id="t3"),
            cursor=AgentResponse(agent="cursor", status="completed", content="d", task_id="t4"),
        )

    def test_returns_letter_keyed_dict(self):
        cr = self._round()
        by_label, mapping = anonymize_round_responses(cr, salt="x")
        assert sorted(by_label.keys()) == ["A", "B", "C", "D"]
        assert sorted(mapping.keys()) == ["A", "B", "C", "D"]

    def test_mapping_covers_all_participants(self):
        cr = self._round()
        _, mapping = anonymize_round_responses(cr, salt="x")
        assert set(mapping.values()) == {"codex", "gemini", "claudeor", "cursor"}

    def test_stable_across_calls_with_same_salt(self):
        cr = self._round()
        _, m1 = anonymize_round_responses(cr, salt="council:000123")
        _, m2 = anonymize_round_responses(cr, salt="council:000123")
        assert m1 == m2

    def test_different_salt_likely_different_mapping(self):
        cr = self._round()
        _, m_a = anonymize_round_responses(cr, salt="A")
        _, m_b = anonymize_round_responses(cr, salt="B")
        # Not guaranteed (small n means collisions possible) but useful sanity.
        # Assert at least one possibility differs across many trials.
        salts = [f"s{i}" for i in range(20)]
        observed = {tuple(sorted(anonymize_round_responses(cr, salt=s)[1].items())) for s in salts}
        assert len(observed) > 1

    def test_none_round_returns_none(self):
        assert anonymize_round_responses(None) == (None, None)

    def test_skips_missing_seats(self):
        cr = CouncilRound(
            codex=AgentResponse(agent="codex", status="completed", content="a", task_id="t1"),
            # gemini intentionally absent
            claudeor=AgentResponse(agent="claudeor", status="completed", content="c", task_id="t3"),
        )
        by_label, mapping = anonymize_round_responses(cr, salt="x")
        assert len(by_label) == 2
        assert set(mapping.values()) == {"codex", "claudeor"}


class TestRecordCouncilAnonymization:
    def test_round_trip(self, tmp_db):
        store.record_council_anonymization(
            "BLIND_TEST", {"A": "codex", "B": "gemini", "C": "claudeor"}
        )
        m = store.get_council_anonymization("BLIND_TEST")
        assert m == {"A": "codex", "B": "gemini", "C": "claudeor"}

    def test_empty_mapping_is_noop(self, tmp_db):
        store.record_council_anonymization("BLIND_EMPTY", {})
        assert store.get_council_anonymization("BLIND_EMPTY") == {}

    def test_idempotent_replace(self, tmp_db):
        store.record_council_anonymization("BLIND_IDEM", {"A": "codex"})
        store.record_council_anonymization("BLIND_IDEM", {"A": "gemini"})
        assert store.get_council_anonymization("BLIND_IDEM") == {"A": "gemini"}

    def test_get_unknown_returns_empty(self, tmp_db):
        assert store.get_council_anonymization("DOES_NOT_EXIST") == {}


class TestRecordAgentScore:
    def test_basic_write(self, tmp_db):
        store.record_agent_score(
            "C1", "codex", 1, dimensions={"groundedness": 5}, reason="cited line"
        )
        rows = tmp_db.execute(
            "SELECT agent, score, dimensions, reason, rater FROM agent_scores WHERE council_id='C1'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["agent"] == "codex"
        assert rows[0]["score"] == 1
        assert rows[0]["rater"] == "claude_blind"
        assert json.loads(rows[0]["dimensions"]) == {"groundedness": 5}
        assert rows[0]["reason"] == "cited line"

    def test_thumbs_down(self, tmp_db):
        store.record_agent_score("C2", "gemini", -1, reason="vague")
        score = tmp_db.execute(
            "SELECT score FROM agent_scores WHERE council_id='C2'"
        ).fetchone()["score"]
        assert score == -1

    def test_rejects_invalid_score(self, tmp_db):
        for bad in (0, 2, -2, 99):
            with pytest.raises(ValueError, match="must be -1 or \\+1"):
                store.record_agent_score("C3", "codex", bad)

    def test_no_dimensions_stored_as_null(self, tmp_db):
        store.record_agent_score("C4", "codex", 1)
        dims = tmp_db.execute(
            "SELECT dimensions FROM agent_scores WHERE council_id='C4'"
        ).fetchone()["dimensions"]
        assert dims is None
