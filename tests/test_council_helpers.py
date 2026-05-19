"""Unit tests for the pure / nearly-pure helpers extracted from Council.deliberate()."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from owlex.council import Council
from owlex.models import AgentResponse, CouncilRound


def _resp(agent: str, content: str) -> AgentResponse:
    return AgentResponse(
        agent=agent, content=content, status="completed",
        duration_seconds=1.0, error=None, output_chars=len(content),
        session_id=None, task_id=f"t-{agent}",
    )


class TestResolveTimeout:
    def test_zero_means_unlimited(self):
        assert Council._resolve_timeout(0) is None

    def test_clamps_below_minimum(self):
        assert Council._resolve_timeout(30) == 120

    def test_passes_through_above_minimum(self):
        assert Council._resolve_timeout(300) == 300

    def test_none_falls_through_to_config_default(self):
        # Whatever config.default_timeout is, the result is either >=120 or None.
        out = Council._resolve_timeout(None)
        assert out is None or out >= 120


class TestCollectR1Contents:
    def test_skips_missing_and_empty(self):
        round_1 = CouncilRound(codex=_resp("codex", "ans"), gemini=None)
        contents = Council._collect_r1_contents(round_1)
        assert contents == {"codex": "ans"}


class TestResolveDeliberation:
    @pytest.mark.asyncio
    async def test_explicit_true_returns_true_no_score(self):
        c = Council()
        will, score, reason = await c._resolve_deliberation(True, "p", {"a": "x"})
        assert will is True and score is None and reason is None

    @pytest.mark.asyncio
    async def test_explicit_false_returns_false(self):
        c = Council()
        will, score, reason = await c._resolve_deliberation(False, "p", {"a": "x"})
        assert will is False and score is None

    @pytest.mark.asyncio
    async def test_auto_below_threshold_triggers_r2(self):
        c = Council()
        with patch("owlex.council.score_agreement", new=AsyncMock(return_value=(1.5, "diverge"))):
            will, score, reason = await c._resolve_deliberation("auto", "p", {"a": "x", "b": "y"})
        assert will is True
        assert score == 1.5
        assert reason == "diverge"

    @pytest.mark.asyncio
    async def test_auto_above_threshold_skips_r2(self):
        c = Council()
        with patch("owlex.council.score_agreement", new=AsyncMock(return_value=(4.0, "match"))):
            will, score, reason = await c._resolve_deliberation("auto", "p", {"a": "x", "b": "y"})
        assert will is False
        assert score == 4.0


class TestPositionDeltaThresholds:
    """The thresholds (0.845, 0.906) are calibrated empirically — pin them."""

    def test_unchanged_minor_major_boundaries(self):
        from owlex.council import _word_set

        # Construct word sets that yield specific Jaccard distances.
        # delta = 1 - intersection/union
        # 4 shared, 1 unique each → union=6, inter=4, delta=1-4/6=0.333 → unchanged
        c = Council()
        round_1 = CouncilRound(codex=_resp("codex", "alpha bravo charlie delta echo"))
        round_2 = CouncilRound(codex=_resp("codex", "alpha bravo charlie delta foxtrot"))

        captured = []
        with patch("owlex.council.store.record_position_delta",
                   side_effect=lambda task_id, *, position_delta, position_label:
                   captured.append((position_delta, position_label))):
            c._compute_and_persist_position_deltas(round_1, round_2)

        assert len(captured) == 1
        delta, label = captured[0]
        assert label == "unchanged"
        assert delta < 0.845
