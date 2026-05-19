"""Heartbeat instrumentation: surface stalled subprocesses before timeout."""
from __future__ import annotations

import asyncio
import sys

import pytest

from owlex.agents.base import AgentCommand
from owlex.engine import TaskEngine
from owlex.models import TaskStatus


@pytest.mark.asyncio
async def test_last_output_monotonic_set_on_stream_lines():
    eng = TaskEngine()
    task = eng.create_task(command="ts", args={})
    cmd = AgentCommand(
        command=[sys.executable, "-c", "import sys; sys.stderr.write('hello\\n')"],
        prompt="",
        output_prefix="OK",
        stream=True,
    )
    await eng.run_agent_command(task, cmd, timeout=10)
    assert task.last_output_monotonic is not None
    assert any("hello" in line for line in task.output_lines)


@pytest.mark.asyncio
async def test_heartbeat_logs_stall(capfd):
    eng = TaskEngine()
    task = eng.create_task(command="stall", args={})
    # Subprocess that emits one early line then sleeps. Heartbeat (interval=0.2s)
    # should fire several times reporting growing idle time.
    src = (
        "import sys, time\n"
        "sys.stderr.write('first\\n'); sys.stderr.flush()\n"
        "time.sleep(1.5)\n"
    )
    asyncio.get_event_loop()
    hb = asyncio.create_task(eng._stall_heartbeat(task, interval_s=0.2))
    cmd = AgentCommand(
        command=[sys.executable, "-c", src],
        prompt="",
        output_prefix="STALL",
        stream=True,
    )
    try:
        await eng.run_agent_command(task, cmd, timeout=10)
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    err = capfd.readouterr().err
    assert "[heartbeat]" in err, err[-500:]
