"""Output-volume circuit breaker.

Reproduces the symptom that bit councils 011533 and 120700: an agent's tool
subprocess (typically ``rg`` recursing into pnpm ``node_modules`` or hitting
a single-line minified bundle) emits output indefinitely, the runner has no
forward-progress check, and the orchestrator can only kill at the 600s
deadline. The cap implemented in ``TaskEngine._read_stream_lines`` aborts
runaway streams much sooner with a clear error.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from owlex.agents.base import AgentCommand
from owlex.engine import TaskEngine
from owlex.models import TaskStatus


@pytest.mark.asyncio
async def test_runaway_stderr_triggers_kill(monkeypatch):
    monkeypatch.setenv("OWLEX_AGENT_MAX_OUTPUT_BYTES", "10000")

    eng = TaskEngine()
    task = eng.create_task(command="flood", args={})

    # 200 KB of stderr, well above the 10 KB cap.
    flood = (
        "import sys\n"
        "for i in range(200):\n"
        "    sys.stderr.write('x' * 1000 + '\\n'); sys.stderr.flush()\n"
        "import time; time.sleep(60)\n"
    )
    cmd = AgentCommand(
        command=[sys.executable, "-c", flood],
        prompt="",
        output_prefix="Flood Output",
        stream=True,
    )

    await eng.run_agent_command(task, cmd, timeout=30)

    assert task.status == TaskStatus.FAILED.value, task.status
    assert task.error is not None
    assert "exceeded output cap" in task.error, task.error


def test_default_cap_is_25_mb():
    assert TaskEngine._DEFAULT_MAX_STREAM_BYTES == 25_000_000


def test_env_override_parsed(monkeypatch):
    monkeypatch.setenv("OWLEX_AGENT_MAX_OUTPUT_BYTES", "12345")
    assert TaskEngine._max_stream_bytes() == 12345


def test_env_override_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("OWLEX_AGENT_MAX_OUTPUT_BYTES", "not-a-number")
    assert TaskEngine._max_stream_bytes() == TaskEngine._DEFAULT_MAX_STREAM_BYTES


def test_env_override_unset_uses_default(monkeypatch):
    monkeypatch.delenv("OWLEX_AGENT_MAX_OUTPUT_BYTES", raising=False)
    assert TaskEngine._max_stream_bytes() == TaskEngine._DEFAULT_MAX_STREAM_BYTES
