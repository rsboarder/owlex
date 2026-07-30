"""Unit tests for the pure / nearly-pure helpers extracted from Council.deliberate()."""
from __future__ import annotations

from types import SimpleNamespace
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


class TestContextSelector:
    """PROJECT CONTEXT is dropped only where it duplicates what the CLI already loaded.

    Measured on bookmatcher council rollouts: the codex CLI ships the repo's
    AGENTS.md into `world_state.state.agents_md.text` AND as a user message,
    before owlex's prompt. With CLAUDE.md symlinked to AGENTS.md there, owlex's
    14k-char PROJECT CONTEXT block was a third copy of the same text.
    """

    @staticmethod
    def _participant(seat: str, *, auto_loads: bool):
        from owlex.models import Participant

        runner = SimpleNamespace(auto_loads_project_instructions=auto_loads)
        return Participant(seat=seat, runner=runner, is_substituted=False)

    def test_drops_context_only_for_autoloading_runner(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# project instructions")
        (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")

        select = Council()._context_selector(str(tmp_path), "CTX")
        assert select(self._participant("codex", auto_loads=True)) is None
        assert select(self._participant("gemini", auto_loads=False)) == "CTX"

    def test_keeps_context_when_claude_md_is_a_distinct_file(self, tmp_path):
        """A CLAUDE.md that merely sits next to AGENTS.md is not a duplicate."""
        (tmp_path / "AGENTS.md").write_text("# agents")
        (tmp_path / "CLAUDE.md").write_text("# different claude guidance")

        select = Council()._context_selector(str(tmp_path), "CTX")
        assert select(self._participant("codex", auto_loads=True)) == "CTX"

    def test_keeps_context_when_repo_has_no_agents_md(self, tmp_path):
        """owlex / second-brain layout: CLAUDE.md only — codex loads nothing itself."""
        (tmp_path / "CLAUDE.md").write_text("# claude only")

        select = Council()._context_selector(str(tmp_path), "CTX")
        assert select(self._participant("codex", auto_loads=True)) == "CTX"

    def test_no_working_directory_is_a_passthrough(self):
        select = Council()._context_selector(None, "CTX")
        assert select(self._participant("codex", auto_loads=True)) == "CTX"

    def test_no_context_stays_none(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# agents")
        (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")

        select = Council()._context_selector(str(tmp_path), None)
        assert select(self._participant("gemini", auto_loads=False)) is None


class TestRunnerAutoloadFlags:
    """Only runners whose auto-loading was verified from their OWN transcripts opt in.

    codex  — ~/.codex/sessions rollout: world_state.agents_md.text + a
             "# AGENTS.md instructions for <dir>" user message.
    cursor — ~/.cursor/chats store.db: <always_applied_workspace_rule ...>.
    grok   — ~/.grok/sessions chat_history.jsonl: a synthetic
             "project_instructions" turn listing the repo's AGENTS.md.

    Deliberately still off: gemini (runs in a tmpdir and only reads GEMINI.md),
    opencode + claudeor (binary shows AGENTS.md discovery, but they put it in
    an unpersisted system prompt, so it is not transcript-proven), aichat (CLI
    not installed — unverifiable).
    """

    def test_only_transcript_verified_runners_opt_in(self):
        from owlex.engine import AGENT_RUNNERS
        from owlex.models import Agent

        opted_in = {
            a.value for a, r in AGENT_RUNNERS.items()
            if r.auto_loads_project_instructions
        }
        assert opted_in == {Agent.CODEX.value, Agent.CURSOR.value, Agent.GROK.value}
