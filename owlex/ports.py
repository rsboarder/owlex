"""Port (Protocol) definitions for cross-layer dependencies.

Hexagonal/clean-architecture seam: ``Council`` (domain orchestration) calls
``TaskEngine`` (infrastructure) only through the interface declared here.
The runtime ``TaskEngine`` is structurally compatible with ``EnginePort`` —
mypy/pyright reject the wiring if either side drifts, catching the bug where
``_log_timing`` was removed from the engine while a Council call site still
referenced it.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .agents.base import AgentRunner
from .models import Task


@runtime_checkable
class EnginePort(Protocol):
    """The subset of ``TaskEngine`` that ``Council`` is allowed to use.

    Methods listed here are the contract; anything else on ``TaskEngine`` is
    private infrastructure and must not be called from ``Council``.
    """

    def create_task(
        self,
        command: str,
        args: dict[str, Any],
        context: Any = ...,
        council_id: str | None = ...,
    ) -> Task: ...

    async def run_agent(
        self,
        task: Task,
        runner: AgentRunner,
        mode: str = ...,
        prompt: str = ...,
        working_directory: str | None = ...,
        session_ref: str | None = ...,
        enable_search: bool = ...,
        timeout: int | None = ...,
        **kwargs: Any,
    ) -> None: ...

    def log_council_summary(
        self,
        council_id: str,
        round_num: int,
        agent_timings: list[tuple[str, float, str]],
    ) -> None: ...
