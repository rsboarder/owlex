"""Tests for TASK-20: GLM-5.2 tie-breaker escalation on R1 disagreement.

Mirrors tests/test_council.py style + autouse _isolate_owlex_home fixture.
Never hits the real Z.ai API — engine.run_agent is mocked in every test.
Never writes to the production DB — autouse fixture points OWLEX_HOME at tmp.

Acceptance criteria covered:
- Config defaults to disabled (zero latency, glm_escalation_response stays None).
- On disagreement + flag enabled: GLM is invoked exactly once, response attached.
- On consensus (deliberate=False) + flag enabled: GLM is NOT invoked.
- On disagreement + flag disabled (default): GLM is NOT invoked.
- GLM failure is non-fatal: glm_escalation_response stays None, council succeeds.
- CouncilRound is untouched (6 seat fields only).
"""
from __future__ import annotations

import types
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from owlex.council import Council
from owlex.config import load_config
from owlex.models import Agent, AgentResponse, CouncilRound, Task, TaskStatus
from owlex.engine import AGENT_RUNNERS


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_glm_escalation_disabled_by_default(monkeypatch):
    """GlmEscalationConfig.enabled must default False — zero behavior change when unset."""
    monkeypatch.delenv("OWLEX_GLM_ESCALATION_ENABLED", raising=False)
    cfg = load_config()
    assert cfg.glm_escalation.enabled is False


def test_glm_escalation_enabled_via_env(monkeypatch):
    """Setting OWLEX_GLM_ESCALATION_ENABLED=1 turns the flag on."""
    monkeypatch.setenv("OWLEX_GLM_ESCALATION_ENABLED", "1")
    cfg = load_config()
    assert cfg.glm_escalation.enabled is True


def test_glm_escalation_config_defaults(monkeypatch):
    """GlmEscalationConfig has the right defaults for model/timeout.

    Reasoning effort is NOT a field here — it is governed by OWLEX_GLM_OC_VARIANT
    (shared with the GLM seat runner) to avoid a config that silently lies.
    """
    monkeypatch.delenv("OWLEX_GLM_ESCALATION_MODEL", raising=False)
    monkeypatch.delenv("OWLEX_GLM_ESCALATION_TIMEOUT", raising=False)
    cfg = load_config()
    assert cfg.glm_escalation.model == "zai/glm-5.2"
    assert cfg.glm_escalation.timeout == 120
    assert not hasattr(cfg.glm_escalation, "variant"), (
        "variant must not exist on GlmEscalationConfig — use OWLEX_GLM_OC_VARIANT instead"
    )


# ---------------------------------------------------------------------------
# Helpers shared by deliberation tests
# ---------------------------------------------------------------------------

def _make_mock_engine(task_counter=None):
    """Return a MagicMock engine with create_task wired up."""
    if task_counter is None:
        task_counter = [0]
    mock = MagicMock()

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
    return mock


def _patch_council_env(stack, mock_engine, agreement_score=3.0):
    """Enter patches needed for a council run: engine, which, score_agreement, runners."""
    stack.enter_context(patch("owlex.council.engine", mock_engine))
    stack.enter_context(patch("owlex.council.shutil.which", return_value="/usr/bin/fake"))
    stack.enter_context(patch(
        "owlex.council.score_agreement",
        new=AsyncMock(return_value=(agreement_score, "mocked")),
    ))
    for runner in AGENT_RUNNERS.values():
        stack.enter_context(patch.object(
            type(runner), "is_configured",
            new_callable=lambda: property(lambda self: True),
        ))


def _standard_run_agent(task, runner, mode="exec", **kwargs):
    """Synchronous mock for run_agent: marks task completed with boilerplate result."""
    task.status = TaskStatus.COMPLETED.value
    task.result = f"{runner.name.title()} Output:\n\n{runner.name.title()} response"
    task.completion_time = datetime.now()


def _make_glm_escalation_config(enabled=True, model="zai/glm-5.2", timeout=120):
    return types.SimpleNamespace(
        enabled=enabled, model=model, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Disagreement + flag enabled → GLM invoked once, response attached
# ---------------------------------------------------------------------------

async def test_glm_escalation_invoked_on_disagreement_when_enabled():
    """GLM-5.2 is called exactly once and glm_escalation_response is set on disagreement."""
    glm_call_count = [0]
    mock_engine = _make_mock_engine()

    async def mock_run_agent(task, runner, mode="exec", model_override=None, **kwargs):
        if task.command == "council_glm_escalation":
            glm_call_count[0] += 1
            # Simulate GLM completing successfully
            task.status = TaskStatus.COMPLETED.value
            task.result = "OpenCode Output:\n\nGLM tie-breaker opinion"
            task.completion_time = datetime.now()
        else:
            _standard_run_agent(task, runner, mode=mode)

    mock_engine.run_agent = AsyncMock(side_effect=mock_run_agent)

    glm_cfg = _make_glm_escalation_config(enabled=True)

    with ExitStack() as stack:
        _patch_council_env(stack, mock_engine, agreement_score=3.0)  # below AUTO_DELIBERATION_THRESHOLD=4.0
        # Patch glm_escalation config to be enabled
        fake_cfg = stack.enter_context(patch("owlex.council.config"))
        fake_cfg.codex.enable_search = True
        fake_cfg.default_timeout = 300
        fake_cfg.glm_blind.enabled = False
        fake_cfg.glm_escalation = glm_cfg

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate="auto",
            timeout=10,
        )

    assert glm_call_count[0] == 1, "GLM must be invoked exactly once on disagreement"
    assert response.glm_escalation_response is not None
    assert response.glm_escalation_response.status == "completed"
    assert "GLM tie-breaker opinion" in (response.glm_escalation_response.content or "")


# ---------------------------------------------------------------------------
# Consensus (deliberate=False) + flag enabled → GLM NOT invoked
# ---------------------------------------------------------------------------

async def test_glm_escalation_not_invoked_on_consensus():
    """When deliberate=False (consensus), GLM escalation must not run even if flag is on."""
    glm_call_count = [0]
    mock_engine = _make_mock_engine()

    async def mock_run_agent(task, runner, mode="exec", **kwargs):
        if task.command == "council_glm_escalation":
            glm_call_count[0] += 1
        _standard_run_agent(task, runner, mode=mode)

    mock_engine.run_agent = AsyncMock(side_effect=mock_run_agent)

    glm_cfg = _make_glm_escalation_config(enabled=True)

    with ExitStack() as stack:
        _patch_council_env(stack, mock_engine, agreement_score=5.0)
        fake_cfg = stack.enter_context(patch("owlex.council.config"))
        fake_cfg.codex.enable_search = True
        fake_cfg.default_timeout = 300
        fake_cfg.glm_blind.enabled = False
        fake_cfg.glm_escalation = glm_cfg

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate=False,  # explicit no-deliberation → no disagreement
            timeout=10,
        )

    assert glm_call_count[0] == 0
    assert response.glm_escalation_response is None


# ---------------------------------------------------------------------------
# Flag disabled (default) + disagreement → GLM NOT invoked
# ---------------------------------------------------------------------------

async def test_glm_escalation_not_invoked_when_flag_off():
    """With flag disabled, GLM is never called regardless of agreement level."""
    glm_call_count = [0]
    mock_engine = _make_mock_engine()

    async def mock_run_agent(task, runner, mode="exec", **kwargs):
        if task.command == "council_glm_escalation":
            glm_call_count[0] += 1
        _standard_run_agent(task, runner, mode=mode)

    mock_engine.run_agent = AsyncMock(side_effect=mock_run_agent)

    glm_cfg = _make_glm_escalation_config(enabled=False)  # flag off

    with ExitStack() as stack:
        _patch_council_env(stack, mock_engine, agreement_score=3.0)  # disagreement
        fake_cfg = stack.enter_context(patch("owlex.council.config"))
        fake_cfg.codex.enable_search = True
        fake_cfg.default_timeout = 300
        fake_cfg.glm_blind.enabled = False
        fake_cfg.glm_escalation = glm_cfg

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate="auto",
            timeout=10,
        )

    assert glm_call_count[0] == 0
    assert response.glm_escalation_response is None


# ---------------------------------------------------------------------------
# GLM failure is non-fatal
# ---------------------------------------------------------------------------

async def test_glm_escalation_failure_is_non_fatal():
    """A GLM engine failure must not break the council; glm_escalation_response stays None."""
    mock_engine = _make_mock_engine()

    async def mock_run_agent(task, runner, mode="exec", **kwargs):
        if task.command == "council_glm_escalation":
            task.status = TaskStatus.FAILED.value
            task.error = "Z.ai auth error"
            task.completion_time = datetime.now()
        else:
            _standard_run_agent(task, runner, mode=mode)

    mock_engine.run_agent = AsyncMock(side_effect=mock_run_agent)

    glm_cfg = _make_glm_escalation_config(enabled=True)

    with ExitStack() as stack:
        _patch_council_env(stack, mock_engine, agreement_score=3.0)
        fake_cfg = stack.enter_context(patch("owlex.council.config"))
        fake_cfg.codex.enable_search = True
        fake_cfg.default_timeout = 300
        fake_cfg.glm_blind.enabled = False
        fake_cfg.glm_escalation = glm_cfg

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate="auto",
            timeout=10,
        )

    # Council must complete normally; no escalation response on failure
    assert response.round_1 is not None
    assert response.glm_escalation_response is None


async def test_glm_escalation_exception_is_non_fatal():
    """An exception in _invoke_glm_escalation must not propagate; council still succeeds."""
    mock_engine = _make_mock_engine()

    async def mock_run_agent(task, runner, mode="exec", **kwargs):
        if task.command == "council_glm_escalation":
            raise RuntimeError("simulated Z.ai network error")
        _standard_run_agent(task, runner, mode=mode)

    mock_engine.run_agent = AsyncMock(side_effect=mock_run_agent)

    glm_cfg = _make_glm_escalation_config(enabled=True)

    with ExitStack() as stack:
        _patch_council_env(stack, mock_engine, agreement_score=3.0)
        fake_cfg = stack.enter_context(patch("owlex.council.config"))
        fake_cfg.codex.enable_search = True
        fake_cfg.default_timeout = 300
        fake_cfg.glm_blind.enabled = False
        fake_cfg.glm_escalation = glm_cfg

        council = Council()
        response = await council.deliberate(
            prompt="Test question",
            deliberate="auto",
            timeout=10,
        )

    assert response.round_1 is not None
    assert response.glm_escalation_response is None


# ---------------------------------------------------------------------------
# CouncilRound is untouched (structural invariant)
# ---------------------------------------------------------------------------

def test_council_round_has_no_glm_field():
    """CouncilRound must have exactly the 6 original seat fields and no GLM field."""
    round_fields = set(CouncilRound.model_fields.keys())
    expected_seats = {"codex", "gemini", "opencode", "claudeor", "aichat", "cursor"}
    assert expected_seats == round_fields, (
        f"CouncilRound fields changed — expected {expected_seats}, got {round_fields}. "
        "Do not add GLM or any non-seat field to CouncilRound (TASK-20 invariant)."
    )


# ---------------------------------------------------------------------------
# CouncilResponse has the new field
# ---------------------------------------------------------------------------

def test_council_response_has_glm_escalation_field():
    """CouncilResponse must expose glm_escalation_response: AgentResponse | None = None."""
    from owlex.models import CouncilResponse
    assert "glm_escalation_response" in CouncilResponse.model_fields
    field = CouncilResponse.model_fields["glm_escalation_response"]
    assert field.default is None
