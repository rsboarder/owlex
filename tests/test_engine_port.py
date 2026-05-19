"""Structural conformance: TaskEngine must satisfy EnginePort.

Failing this test means a method Council depends on was renamed or removed
on the engine side without updating the call sites — exactly the bug that
caused councils 004454 / 011533 / 113651 to lose their post-deliberation
writes when an agent timed out (Council called ``_engine._log_timing`` after
that method had been removed from ``TaskEngine``).
"""
from __future__ import annotations

import inspect

import pytest

from owlex.engine import TaskEngine
from owlex.ports import EnginePort


# Methods Council legitimately reaches for on the engine.
# Update this list whenever ``EnginePort`` adds/removes members.
_REQUIRED_METHODS = ("create_task", "run_agent", "log_council_summary")


def test_task_engine_implements_engine_port():
    eng = TaskEngine()
    assert isinstance(eng, EnginePort), (
        "TaskEngine no longer satisfies EnginePort — Council depends on this. "
        "Either restore the missing member on TaskEngine or update EnginePort + "
        "all Council call sites in lockstep."
    )


@pytest.mark.parametrize("name", _REQUIRED_METHODS)
def test_task_engine_exposes_required_method(name):
    assert hasattr(TaskEngine, name), f"TaskEngine.{name} is referenced by Council but missing"
    assert callable(getattr(TaskEngine, name))


def test_council_only_calls_documented_engine_methods():
    """Static check: every ``self._engine.<X>`` in council.py must be in EnginePort."""
    import pathlib
    import re
    src = pathlib.Path(inspect.getfile(__import__("owlex.council", fromlist=["x"]))).read_text()
    referenced = set(re.findall(r"self\._engine\.([A-Za-z_][A-Za-z0-9_]*)", src))
    declared = {n for n in dir(EnginePort) if not n.startswith("_")}
    leaks = referenced - declared
    assert not leaks, (
        f"Council calls TaskEngine methods that aren't part of EnginePort: {sorted(leaks)}. "
        "Add them to EnginePort (and document why Council needs them), or stop calling them."
    )
