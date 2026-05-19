"""Recover a completed answer when fail_pattern fires post-completion.

Real-world failure: gemini-cli finishes its answer, writes the full result
to stdout, then attempts an internal follow-up API call that returns HTTP
429 MODEL_CAPACITY_EXHAUSTED. The error JSON lands on stderr, owlex's
fail_patterns matches the substring and SIGKILLs the subprocess. Without
this recovery, the orchestrator marks the call 'failed' and discards an
answer that is sitting right there in stdout.
"""
from __future__ import annotations

import sys

import pytest

from owlex.agents.base import AgentCommand
from owlex.engine import TaskEngine
from owlex.models import TaskStatus


@pytest.mark.asyncio
async def test_recover_completed_answer_when_fail_pattern_fires_after_stdout():
    """Subprocess writes answer to stdout, then stderr matches a fail_pattern.
    Owlex should mark COMPLETED and stash the pattern hit as a warning.
    """
    eng = TaskEngine()
    task = eng.create_task(command="late_fail", args={})

    # Simulate: write 50 bytes of answer to stdout, then emit a stderr line
    # matching the fail_pattern. The reader will kill the subprocess after
    # matching, but stdout is already populated.
    src = (
        "import sys, time\n"
        "sys.stdout.write('the complete answer\\n'); sys.stdout.flush()\n"
        "time.sleep(0.05)\n"
        "sys.stderr.write('MODEL_CAPACITY_EXHAUSTED upstream 429\\n'); sys.stderr.flush()\n"
        "time.sleep(10)\n"
    )
    cmd = AgentCommand(
        command=[sys.executable, "-c", src],
        prompt="",
        output_prefix="Recovered",
        stream=True,
        fail_patterns=["MODEL_CAPACITY_EXHAUSTED"],
    )
    await eng.run_agent_command(task, cmd, timeout=15)

    # The systemic invariant: a completed stdout MUST NOT be discarded just
    # because a fail_pattern fired after it was written.
    assert task.status == TaskStatus.COMPLETED.value, (task.status, task.error)
    assert task.result is not None
    assert "the complete answer" in task.result
    assert task.warnings is not None
    assert "fail_pattern hit" in task.warnings
    assert "MODEL_CAPACITY_EXHAUSTED" in task.warnings


@pytest.mark.asyncio
async def test_fail_pattern_without_stdout_still_marked_failed():
    """When fail_pattern fires BEFORE any stdout exists, behavior stays the
    same — task is FAILED. This is the original semantics for genuinely
    fatal early errors (e.g. quota exhausted before model even responds).
    """
    eng = TaskEngine()
    task = eng.create_task(command="early_fail", args={})

    # No stdout written; stderr immediately matches the fail_pattern.
    src = (
        "import sys, time\n"
        "sys.stderr.write('MODEL_CAPACITY_EXHAUSTED\\n'); sys.stderr.flush()\n"
        "time.sleep(10)\n"
    )
    cmd = AgentCommand(
        command=[sys.executable, "-c", src],
        prompt="",
        output_prefix="EarlyFail",
        stream=True,
        fail_patterns=["MODEL_CAPACITY_EXHAUSTED"],
    )
    await eng.run_agent_command(task, cmd, timeout=15)

    assert task.status == TaskStatus.FAILED.value, (task.status, task.error)
    assert task.result is None
