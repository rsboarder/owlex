"""
Tests for council orchestration logic with mocked engine.
"""

import asyncio
import dataclasses
import json
import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from owlex.council import Council, _display_name
from owlex.models import (
    Agent,
    COUNCIL_SEATS,
    CouncilRound,
    CouncilWarningReason,
    Task,
    TaskStatus,
)
from owlex.engine import codex_runner, gemini_runner
from owlex.roles import BUILTIN_ROLES, BUILTIN_TEAMS, RoleId, RoleResolver, TeamPreset
from owlex.server._council import _sanitize_metadata_for_rating


@pytest.fixture
def mock_engine():
    """Create a mock engine for testing council logic.

    Also patches ``shutil.which`` and each runner's ``is_configured`` so all
    six native agents appear available regardless of the developer machine —
    otherwise the council substitutes missing seats with donors and tests that
    expect native names (codex/gemini/opencode/...) break.
    """
    from contextlib import ExitStack
    from owlex.engine import AGENT_RUNNERS

    with ExitStack() as stack:
        mock = stack.enter_context(patch("owlex.council.engine"))
        stack.enter_context(patch("owlex.council.shutil.which", return_value="/usr/bin/fake"))
        # Don't make a real agreement judge call (would hit a real LLM).
        stack.enter_context(patch(
            "owlex.council.score_agreement",
            new=AsyncMock(return_value=(3.0, "mocked")),
        ))
        # Force every runner class to report is_configured=True.
        for runner in AGENT_RUNNERS.values():
            stack.enter_context(patch.object(
                type(runner), "is_configured",
                new_callable=lambda: property(lambda self: True),
            ))

        task_counter = [0]

        def create_task(command, args, context=None, council_id=None):
            task_counter[0] += 1
            return Task(
                task_id=f"test-task-{task_counter[0]}",
                status=TaskStatus.PENDING.value,
                command=command,
                args=args,
                start_time=datetime.now(),
                context=context,
            )

        mock.create_task = MagicMock(side_effect=create_task)
        mock.kill_task_subprocess = AsyncMock()
        yield mock


@pytest.fixture
def mock_config():
    """Mock config to control enable_search setting."""
    with patch("owlex.council.config") as mock:
        mock.codex.enable_search = True
        mock.default_timeout = 300
        yield mock


class TestCouncilRound1:
    """Tests for round 1 parallel execution."""

    async def test_runs_all_agents_in_parallel(self, mock_engine, mock_config):
        """Should run Codex, Gemini, and OpenCode in parallel."""
        call_order = []

        async def mock_run_agent(task, runner, mode="exec", **kwargs):
            agent_name = runner.name
            call_order.append(f"{agent_name}_start")
            await asyncio.sleep(0.05)
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{agent_name.title()} Output:\n\n{agent_name.title()} response"
            task.completion_time = datetime.now()
            call_order.append(f"{agent_name}_end")

        mock_engine.run_agent = mock_run_agent

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate=False,
            timeout=10,
        )

        # All three should start before any ends (parallel execution)
        assert "codex_start" in call_order[:3]
        assert "gemini_start" in call_order[:3]
        assert "opencode_start" in call_order[:3]
        assert response.round_1 is not None
        assert response.round_1.codex.status == "completed"
        assert response.round_1.gemini.status == "completed"
        assert response.round_1.opencode.status == "completed"

    async def test_test_review_injects_role_prefix_for_every_seat(self, mock_engine, mock_config):
        prompts = {}

        async def mock_run_agent(task, runner, mode="exec", prompt="", **kwargs):
            prompts[task.command] = prompt
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
            task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        response = await council.deliberate(
            prompt="Review existing tests",
            roles="test_review",
            deliberate=False,
            timeout=10,
        )

        role_prefix = BUILTIN_ROLES[RoleId.TEST_ADVERSARY.value].round_1_prefix
        assert response.roles == {seat: RoleId.TEST_ADVERSARY.value for seat in COUNCIL_SEATS}
        assert set(prompts) == {f"council_{seat}" for seat in COUNCIL_SEATS}
        assert all(role_prefix in prompt for prompt in prompts.values())

    async def test_handles_timeout(self, mock_engine, mock_config):
        """Should handle agent timeout gracefully."""
        async def mock_run_agent(task, runner, mode="exec", **kwargs):
            if runner.name == "codex":
                await asyncio.sleep(10)  # Longer than timeout
                task.status = TaskStatus.COMPLETED.value
            else:
                task.status = TaskStatus.COMPLETED.value
                task.result = "Gemini Output:\n\nGemini response"
                task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        # Bypass the production COUNCIL_MIN_TIMEOUT=120s floor so a test
        # timeout of 1s actually trips the codex sleep.
        with patch.object(Council, "_resolve_timeout",
                          staticmethod(lambda t: 0 if t == 0 else (t or 1))):
            response = await council.deliberate(
                prompt="Test question",
                deliberate=False,
                timeout=1,
            )

        # Codex should have timed out
        assert response.round_1 is not None
        assert response.round_1.codex.status == "failed"
        # Gemini should succeed
        assert response.round_1.gemini.status == "completed"


class TestCouncilRound2:
    """Tests for round 2 deliberation."""

    async def test_deliberation_includes_round1_answers(self, mock_engine, mock_config):
        """Round 2 prompt should include round 1 answers."""
        round2_prompt = None

        async def mock_run_agent(task, runner, mode="exec", prompt="", **kwargs):
            nonlocal round2_prompt
            if "_delib" in task.command and runner.name == "codex":
                round2_prompt = prompt
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
            task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate=True,
            critique=False,
            timeout=10,
        )

        assert response.round_2 is not None
        assert round2_prompt is not None
        # Round 2 prompt now uses anonymized response labels (RESPONSE A/B/C/...).
        assert "RESPONSE A:" in round2_prompt
        assert "RESPONSE B:" in round2_prompt
        # The actual round-1 content from the other agents must appear.
        assert "Codex response" in round2_prompt or "Gemini response" in round2_prompt

    async def test_critique_mode_prompt(self, mock_engine, mock_config):
        """Critique mode should use critical analysis prompt."""
        round2_prompt = None

        async def mock_run_agent(task, runner, mode="exec", prompt="", **kwargs):
            nonlocal round2_prompt
            if "_delib" in task.command and runner.name == "codex":
                round2_prompt = prompt
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
            task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        await council.deliberate(
            prompt="Test question",
            deliberate=True,
            critique=True,
            timeout=10,
        )

        assert round2_prompt is not None
        assert "senior reviewer" in round2_prompt.lower() or "critical" in round2_prompt.lower()
        assert "bugs" in round2_prompt.lower() or "flaws" in round2_prompt.lower()

    async def test_claude_opinion_included(self, mock_engine, mock_config):
        """Claude's opinion should be included in round 2."""
        round2_prompt = None

        async def mock_run_agent(task, runner, mode="exec", prompt="", **kwargs):
            nonlocal round2_prompt
            if "_delib" in task.command and runner.name == "codex":
                round2_prompt = prompt
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
            task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            claude_opinion="Claude's expert analysis here",
            deliberate=True,
            timeout=10,
        )

        assert round2_prompt is not None
        # The Claude-opinion content is included in the R2 prompt verbatim.
        assert "Claude's expert analysis here" in round2_prompt
        assert response.claude_opinion is not None
        assert response.claude_opinion.content == "Claude's expert analysis here"


class TestCouncilMetadata:
    """Tests for council response metadata."""

    async def test_metadata_includes_timing(self, mock_engine, mock_config):
        """Metadata should include timing information."""
        async def mock_run_agent(task, runner, mode="exec", **kwargs):
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
            task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate=True,
            timeout=10,
        )

        assert response.metadata is not None
        assert response.metadata.total_duration_seconds > 0
        assert response.metadata.rounds == 2
        assert len(response.metadata.log) > 0

    async def test_single_round_metadata(self, mock_engine, mock_config):
        """Single round should report rounds=1."""
        async def mock_run_agent(task, runner, mode="exec", **kwargs):
            task.status = TaskStatus.COMPLETED.value
            task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
            task.completion_time = datetime.now()

        mock_engine.run_agent = mock_run_agent

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate=False,
            timeout=10,
        )

        assert response.metadata.rounds == 1


class TestCouncilConfig:
    """Tests for config integration."""

    async def test_uses_config_enable_search(self, mock_engine):
        """Should use config.codex.enable_search setting."""
        search_setting = None

        with patch("owlex.council.config") as mock_config:
            mock_config.codex.enable_search = False
            mock_config.default_timeout = 300

            async def mock_run_agent(task, runner, mode="exec", enable_search=False, **kwargs):
                nonlocal search_setting
                if runner.name == "codex":
                    search_setting = enable_search
                task.status = TaskStatus.COMPLETED.value
                task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
                task.completion_time = datetime.now()

            mock_engine.run_agent = mock_run_agent

            council = Council()
            await council.deliberate(
                prompt="Test question",
                deliberate=False,
                timeout=10,
            )

            assert search_setting is False


def _resolve_participants(
    substitution_models,
    role_spec=None,
    resolver=None,
    which=None,
    configured=None,
    exclude_agents=frozenset(),
    return_council=False,
):
    from contextlib import ExitStack
    from owlex.engine import AGENT_RUNNERS
    from owlex.config import config as real_config

    new_council = dataclasses.replace(
        real_config.council,
        substitution_models=substitution_models,
        exclude_agents=exclude_agents,
    )
    fake_cfg = dataclasses.replace(real_config, council=new_council)

    with ExitStack() as stack:
        # Make every native CLI appear installed + configured.
        stack.enter_context(patch(
            "owlex.council.shutil.which",
            side_effect=which or (lambda _command: "/usr/bin/fake"),
        ))
        for runner in AGENT_RUNNERS.values():
            if isinstance(configured, dict):
                runner_configured = configured.get(runner.name, True)
            elif configured is None:
                runner_configured = True
            else:
                runner_configured = configured
            stack.enter_context(patch.object(
                type(runner), "is_configured",
                new_callable=lambda value=runner_configured: property(lambda self: value),
            ))
        stack.enter_context(patch("owlex.council.config", fake_cfg))
        resolver = resolver or RoleResolver(
            roles=dict(BUILTIN_ROLES), teams=dict(BUILTIN_TEAMS),
        )
        council = Council(role_resolver=resolver)
        parts = council.build_participants(role_spec)
    by_seat = {p.seat: p for p in parts}
    return (council, by_seat) if return_council else by_seat


def _build_result(council, by_seat):
    return council._build_outcome(
        prompt="p",
        working_directory=None,
        round_1=CouncilRound(),
        round_2=None,
        participants=list(by_seat.values()),
        council_start=datetime.now(),
        deliberate=False,
        critique=False,
        agreement_score=None,
        agreement_reason=None,
        claude_opinion=None,
    )


class TestBuildParticipantsSubstitutionOverride:
    """COUNCIL_SUBSTITUTION_MODELS with an explicit runner must force substitution
    even when the seat's own CLI is installed.

    Regression: an auth-dead-but-installed gemini binary stayed in ``available``,
    ran natively (exiting on IneligibleTierError), and the
    ``gemini:opencode:zai/glm-5.2`` override was silently ignored — the seat
    failed every council despite a valid config entry.
    """

    def test_explicit_runner_override_forces_substitution_for_available_seat(self):
        by_seat = _resolve_participants({"gemini": ("opencode", "zai/glm-5.2")})
        gem = by_seat["gemini"]
        assert gem.is_substituted is True
        assert gem.donor == "opencode"
        assert gem.model_override == "zai/glm-5.2"
        assert gem.runner.name == "opencode"
        # The donor's own seat is untouched (stays native).
        assert by_seat["opencode"].is_substituted is False

    def test_available_seat_without_override_stays_native(self):
        by_seat = _resolve_participants(None)
        assert by_seat["gemini"].is_substituted is False
        assert by_seat["gemini"].model_override is None

    def test_non_seat_runner_override_selects_that_runner(self):
        """A named runner that isn't itself an ALL_SEATS member (e.g. grok) must
        still be selected as the donor — not silently rerouted to donor_pool[0].

        Regression: the donor gate only accepted runner_name if it was ALSO an
        available *seat*; grok is a registered AGENT_RUNNERS entry but not a
        seat, so it always fell through to the default donor (codex), and the
        model override (grok-4.5) got handed to codex instead of grok.
        """
        by_seat = _resolve_participants({"aichat": ("grok", "grok-4.5")})
        p = by_seat["aichat"]
        assert p.runner.name == "grok"
        assert p.model_override == "grok-4.5"
        assert p.runner.output_prefix == "Grok Output"

    def test_named_donor_must_be_configured(self):
        by_seat = _resolve_participants(
            {"codex": ("claudeor", None)},
            configured={"claudeor": False},
        )
        assert "codex" not in by_seat
        assert all(p.runner.name != "claudeor" for p in by_seat.values())

    def test_named_excluded_seat_cannot_be_a_donor(self):
        by_seat = _resolve_participants(
            {"codex": ("gemini", None)},
            exclude_agents=frozenset({"gemini"}),
        )
        assert "codex" not in by_seat
        assert "gemini" not in by_seat
        assert all(p.runner.name != "gemini" for p in by_seat.values())

    def test_excluded_non_seat_runner_cannot_be_a_donor(self):
        council, by_seat = _resolve_participants(
            None,
            role_spec="test_spec",
            exclude_agents=frozenset({"grok"}),
            return_council=True,
        )
        assert "claudeor" not in by_seat
        assert all(p.runner.name != "grok" for p in by_seat.values())
        assert len(council._warning_summary()) == 1


class TestBuildParticipantsTeamRunnerOverrides:
    """TeamPreset.runner_overrides route ONE seat through a different runner,
    scoped to that team, without touching the global COUNCIL_SUBSTITUTION_MODELS
    override or the seats the team doesn't name.
    """

    @pytest.mark.parametrize("team_id", ("test_spec", "test_review"))
    def test_test_teams_route_claudeor_through_grok(self, team_id):
        by_seat = _resolve_participants(None, role_spec=team_id)
        claudeor = by_seat["claudeor"]
        assert claudeor.is_substituted is True
        assert claudeor.donor == "grok"
        assert claudeor.runner.name == "grok"
        # Runner-only override: no model string is pinned in the team preset.
        assert claudeor.model_override is None

    def test_test_spec_leaves_other_seats_native(self):
        by_seat = _resolve_participants(None, role_spec="test_spec")
        for seat in COUNCIL_SEATS:
            if seat == "claudeor":
                continue
            assert by_seat[seat].is_substituted is False

    def test_code_review_team_unaffected_by_new_field(self):
        """A global COUNCIL_SUBSTITUTION_MODELS-style override for claudeor
        still applies under a team that carries no runner_overrides of its own.
        """
        by_seat = _resolve_participants(
            {"claudeor": ("opencode", "zai/glm-5.2")}, role_spec="code_review",
        )
        claudeor = by_seat["claudeor"]
        assert claudeor.is_substituted is True
        assert claudeor.donor == "opencode"
        assert claudeor.model_override == "zai/glm-5.2"

    def test_no_role_spec_unaffected_by_new_field(self):
        by_seat = _resolve_participants(
            {"claudeor": ("opencode", "zai/glm-5.2")}, role_spec=None,
        )
        claudeor = by_seat["claudeor"]
        assert claudeor.is_substituted is True
        assert claudeor.donor == "opencode"
        assert claudeor.model_override == "zai/glm-5.2"

    def test_unknown_runner_in_team_override_drops_the_seat(self):
        """An unknown runner name in a team override must skip the seat —
        never silently reroute it onto an unrelated donor. Mirrors the
        existing COUNCIL_SUBSTITUTION_MODELS unknown-runner behavior.
        """
        import dataclasses
        bogus_team = dataclasses.replace(
            BUILTIN_TEAMS["test_spec"], runner_overrides={"claudeor": "not_a_real_runner"},
        )
        base = RoleResolver(roles=dict(BUILTIN_ROLES), teams=dict(BUILTIN_TEAMS))
        teams = dict(base.teams)
        teams["test_spec"] = bogus_team
        resolver = RoleResolver(roles=base.roles, teams=teams)

        by_seat = _resolve_participants(
            None, role_spec="test_spec", resolver=resolver,
        )
        assert "claudeor" not in by_seat
        for seat in COUNCIL_SEATS:
            if seat == "claudeor":
                continue
            assert seat in by_seat
            assert by_seat[seat].is_substituted is False

    def test_team_override_normalizes_case_and_whitespace(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["id"] = "normalized"
        data["runner_overrides"] = {" ClaudeOR ": " Grok "}
        team = TeamPreset.from_dict(data)
        base = RoleResolver(roles=dict(BUILTIN_ROLES), teams=dict(BUILTIN_TEAMS))
        teams = dict(base.teams)
        teams[team.id] = team
        resolver = RoleResolver(roles=base.roles, teams=teams)

        by_seat = _resolve_participants(None, role_spec="normalized", resolver=resolver)
        assert by_seat["claudeor"].runner.name == "grok"

    def test_team_override_wins_over_global_model_override(self):
        by_seat = _resolve_participants(
            {"claudeor": ("opencode", "zai/glm-5.2")},
            role_spec="test_spec",
        )
        assert by_seat["claudeor"].runner.name == "grok"
        assert by_seat["claudeor"].model_override is None

    def test_missing_team_runner_is_reported_in_result_without_identifiers(self):
        from owlex.engine import AGENT_RUNNERS

        def which(command):
            return None if command == AGENT_RUNNERS[Agent.GROK].cli_command else "/usr/bin/fake"

        council, by_seat = _resolve_participants(
            None,
            role_spec="test_spec",
            which=which,
            return_council=True,
        )
        assert "claudeor" not in by_seat

        response = _build_result(council, by_seat)
        assert len(response.metadata.warnings) == 1

        sanitized = _sanitize_metadata_for_rating(response.metadata)
        assert len(sanitized["warnings"]) == 1
        serialized = json.dumps(sanitized)
        for identifier in (*COUNCIL_SEATS, Agent.GROK.value):
            assert identifier not in serialized

    def test_model_less_substitution_log_does_not_emit_none(self):
        council, _ = _resolve_participants(None, role_spec="test_spec", return_council=True)
        substitution_logs = [
            entry for entry in council.log_entries
            if entry.startswith("Substituting agents:")
        ]
        assert substitution_logs
        assert all("None" not in entry for entry in substitution_logs)

    def test_display_name_uses_donor_model_not_seat_model(self):
        from owlex.config import config as real_config

        by_seat = _resolve_participants(None, role_spec="test_spec")
        fake_cfg = dataclasses.replace(
            real_config,
            claudeor=dataclasses.replace(real_config.claudeor, model="deepseek/deepseek-v3.2"),
            grok=dataclasses.replace(real_config.grok, model="grok-4.5"),
        )
        with patch("owlex.council.config", fake_cfg):
            display = _display_name(by_seat["claudeor"])
        assert "grok-4.5" in display
        assert "deepseek/deepseek-v3.2" not in display

    def test_two_seats_on_one_donor_render_distinctly(self):
        by_seat = _resolve_participants(
            None,
            configured={"aichat": False, "cursor": False},
        )
        assert _display_name(by_seat["aichat"]) != _display_name(by_seat["cursor"])

    def test_no_donor_warning_is_reported_without_identifiers(self):
        council, by_seat = _resolve_participants(
            None,
            configured=False,
            return_council=True,
        )
        response = _build_result(council, by_seat)
        assert len(response.metadata.warnings) == 1

        sanitized = _sanitize_metadata_for_rating(response.metadata)
        assert len(sanitized["warnings"]) == 1
        serialized = json.dumps(sanitized)
        for identifier in (*COUNCIL_SEATS, Agent.GROK.value):
            assert identifier not in serialized

    def test_every_warning_reason_survives_as_a_bounded_summary(self):
        council = Council()
        # Fallback failures leave the seat in the returned payload, so their
        # reason must never enter the caller-facing dropped-seat enum.
        assert "FALLBACK_RUNNER_UNAVAILABLE" not in CouncilWarningReason.__members__
        for reason in CouncilWarningReason:
            assert re.fullmatch(r"[a-z ]+", reason.value)
            council.warn("detailed internal warning", reason=reason)

        response = _build_result(council, {})
        sanitized = _sanitize_metadata_for_rating(response.metadata)
        assert len(sanitized["warnings"]) == len(CouncilWarningReason)
        serialized = json.dumps(sanitized)
        for identifier in (*COUNCIL_SEATS, Agent.GROK.value):
            assert identifier not in serialized

    def test_warn_rejects_unbounded_reason(self):
        with pytest.raises(TypeError):
            Council().warn("detailed internal warning", reason="seat leaked")  # type: ignore[arg-type]

    def test_team_override_for_excluded_seat_is_ignored(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["id"] = "excluded_override"
        data["runner_overrides"] = {"gemini": "grok"}
        team = TeamPreset.from_dict(data)
        base = RoleResolver(roles=dict(BUILTIN_ROLES), teams=dict(BUILTIN_TEAMS))
        teams = dict(base.teams)
        teams[team.id] = team
        resolver = RoleResolver(roles=base.roles, teams=teams)
        assert resolver.runner_overrides_for(team.id) == {"gemini": "grok"}

        by_seat = _resolve_participants(
            None,
            role_spec=team.id,
            resolver=resolver,
            exclude_agents=frozenset({"gemini"}),
        )
        assert "gemini" not in by_seat
        assert all(p.runner.name != "grok" for p in by_seat.values())


class TestFallbackAvailability:
    @pytest.mark.asyncio
    async def test_excluded_fallback_runner_is_not_started(self, mock_engine):
        from owlex.config import config as real_config

        fake_cfg = dataclasses.replace(
            real_config,
            gemini=dataclasses.replace(
                real_config.gemini,
                fallback_runner="cursor",
                fallback_model="fallback-model",
            ),
            council=dataclasses.replace(
                real_config.council,
                exclude_agents=frozenset({"cursor"}),
            ),
        )
        from owlex.engine import AGENT_RUNNERS

        cursor = AGENT_RUNNERS[Agent.CURSOR]
        with patch("owlex.council.shutil.which", return_value="/usr/bin/fake"), patch.object(
            type(cursor),
            "is_configured",
            new_callable=lambda: property(lambda self: True),
        ), patch("owlex.council.config", fake_cfg):
            council = Council(task_engine=mock_engine)
            result = await council._run_fallback(
                "gemini", "gemini", "prompt", None, datetime.now(),
            )

        assert result == (None, None)
        response = _build_result(council, {})
        assert response.metadata.warnings == []


def test_every_agent_has_a_config_attr():
    from owlex.config import config

    assert all(hasattr(config, agent.value) for agent in Agent)
