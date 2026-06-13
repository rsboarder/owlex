"""Blind per-agent rating: anonymization persistence + agent_scores writer.

Also covers the blind-rating invariant: council_ask must not surface anything
that lets a reader map the lettered responses back to specific agent seats —
neither seat-identifying metadata (timing, slowest_agent, log) nor a per-response
join key (duration_seconds, which pairs with agent_timing(council_id); task_id).
"""
from __future__ import annotations

import json

import pytest

from owlex import store
from owlex.models import AgentResponse, AgentTiming, CouncilMetadata, CouncilRound
from owlex.prompts import anonymize_round_responses
from owlex.server._council import (
    _anonymize_response_for_rating,
    _sanitize_metadata_for_rating,
)


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


# === Blind-Rating Invariant: metadata sanitization ===

# All known seat names — none of these may appear in the sanitized rater-facing payload.
_ALL_SEATS = {"codex", "gemini", "opencode", "claudeor", "aichat", "cursor"}


def _make_leaky_metadata() -> CouncilMetadata:
    """Build a CouncilMetadata that would expose seat identity in all three leaking fields."""
    return CouncilMetadata(
        total_duration_seconds=45.0,
        rounds=1,
        timing=[
            AgentTiming(agent="codex", round=1, duration_seconds=30.0, status="completed"),
            AgentTiming(agent="gemini", round=1, duration_seconds=45.0, status="completed"),
        ],
        slowest_agent="gemini",
        log=[
            "Round 1: querying codex, gemini, opencode",
            "Roles assigned: codex=security",
        ],
    )


class TestSanitizeMetadataForRating:
    def test_drops_identifying_keys(self):
        sanitized = _sanitize_metadata_for_rating(_make_leaky_metadata())
        assert "timing" not in sanitized, "timing must be stripped for blind rater"
        assert "slowest_agent" not in sanitized, "slowest_agent must be stripped for blind rater"
        assert "log" not in sanitized, "log must be stripped for blind rater"

    def test_retains_non_identifying_scalars(self):
        sanitized = _sanitize_metadata_for_rating(_make_leaky_metadata())
        assert sanitized["total_duration_seconds"] == 45.0
        assert sanitized["rounds"] == 1

    def test_no_seat_name_leaks_in_serialized_payload(self):
        sanitized = _sanitize_metadata_for_rating(_make_leaky_metadata())
        serialized = json.dumps(sanitized)
        for seat in _ALL_SEATS:
            assert seat not in serialized, (
                f"Seat name '{seat}' found in sanitized metadata — "
                "blind-rating invariant violated"
            )


class TestAnonymizeResponseForRating:
    """Per-response scrub: a lettered response must not carry a de-anon handle."""

    def _resp(self) -> AgentResponse:
        return AgentResponse(
            agent="codex", status="completed", content="answer",
            duration_seconds=30.0, task_id="task-codex-123", session_id="sess-1",
        )

    def test_nulls_deanon_handles(self):
        d = _anonymize_response_for_rating(self._resp())
        assert d["agent"] == "anon"
        assert d["session_id"] is None
        assert d["duration_seconds"] is None  # joins with agent_timing(council_id)
        assert d["task_id"] is None

    def test_no_seat_or_join_key_in_serialized_response(self):
        d = _anonymize_response_for_rating(self._resp())
        serialized = json.dumps(d)
        assert "codex" not in serialized
        assert "task-codex-123" not in serialized
        assert "30.0" not in serialized  # the timing join key is gone

    def test_preserves_ratable_content(self):
        d = _anonymize_response_for_rating(self._resp())
        assert d["content"] == "answer"
        assert d["status"] == "completed"

    def test_none_returns_none(self):
        assert _anonymize_response_for_rating(None) is None
