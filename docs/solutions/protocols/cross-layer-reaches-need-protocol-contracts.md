# Cross-layer reaches between Domain and Infrastructure need Protocol contracts

## Problem

`Council` (domain orchestration) calls methods on `TaskEngine` (infrastructure) through a plain `self._engine` reference. When `TaskEngine` was refactored, the method `_log_timing` was removed — but a call site in `Council._wait_and_handle_timeouts` still referenced it:

```python
# council.py:518
self._engine._log_timing(task)   # AttributeError at runtime
```

Symptoms:
- Council deliberation that hit a subprocess timeout would `AttributeError: 'TaskEngine' object has no attribute '_log_timing'`.
- The exception escaped `_wait_and_handle_timeouts → _run_round_2 → _do_round_2_if_needed → Council.deliberate`.
- The MCP handler's catch-all returned `Council deliberation failed: 'TaskEngine' object has no attribute '_log_timing'`.
- **All post-R2 writes (anonymization, pairwise, outcome) were lost** for that council.

The whole class of bug was waiting for the next refactor of `TaskEngine` private members to surface it.

## Root cause

**Cross-layer coupling was string-based, not contractual.** There was no interface declaring "these are the methods Council is allowed to call on engine." So:

- A refactor of `TaskEngine` could remove or rename any method without static analysis catching it.
- Type checkers had nothing to verify against — `self._engine: Any` (or untyped).
- The bug only surfaced at runtime, in a rarely-executed code path (the timeout branch).

## Solution

Define an explicit `Protocol` in a hexagonal-architecture seam:

```python
# owlex/ports.py
from typing import Any, Protocol, runtime_checkable
from .agents.base import AgentRunner
from .models import Task

@runtime_checkable
class EnginePort(Protocol):
    """The subset of TaskEngine that Council is allowed to use."""

    def create_task(self, command: str, args: dict[str, Any],
                    context: Any = ..., council_id: str | None = ...) -> Task: ...

    async def run_agent(self, task: Task, runner: AgentRunner,
                        mode: str = ..., prompt: str = ...,
                        working_directory: str | None = ...,
                        session_ref: str | None = ...,
                        enable_search: bool = ...,
                        timeout: int | None = ...,
                        **kwargs: Any) -> None: ...

    def log_council_summary(self, council_id: str, round_num: int,
                            agent_timings: list[tuple[str, float, str]]) -> None: ...
```

`Council` annotates: `self._engine: EnginePort`. The `runtime_checkable` decorator lets us `isinstance(eng, EnginePort)` for an explicit structural assertion in tests.

## Why this works

**Static prevention** (mypy/pyright):
- Council can only call methods on `EnginePort`. Calling `self._engine._log_timing(...)` after removal would fail typecheck.
- Adding a new call site for an undeclared method also fails typecheck — forcing the author to either add the method to the port or rethink the reach.

**Runtime prevention** (test):

```python
# tests/test_engine_port.py
def test_task_engine_implements_engine_port():
    assert isinstance(TaskEngine(), EnginePort)

def test_council_only_calls_documented_engine_methods():
    """Static check: every self._engine.X in council.py must be in EnginePort."""
    src = pathlib.Path(inspect.getfile(council)).read_text()
    referenced = set(re.findall(r"self\._engine\.([A-Za-z_]\w*)", src))
    declared = {n for n in dir(EnginePort) if not n.startswith("_")}
    leaks = referenced - declared
    assert not leaks, f"Council reaches undocumented methods: {sorted(leaks)}"
```

The AST-grep test catches the bug class **even if mypy isn't in CI** (which is the current state).

## Prevention

- **T1**: `test_engine_port.py` (5 tests) runs in CI on every commit. If anyone removes a method from `TaskEngine` or adds a new reach in `Council`, tests fail.
- **T2**: When typecheck-in-CI lands (mypy/pyright), the Protocol catches the regression even earlier — at edit time in the IDE.
- **T3** (documentation): every cross-module reach in owlex must go through a Port declared in `owlex/ports.py`. This file is the canonical map of "Domain ↔ Infrastructure" coupling.

## Generalizable rule

Any string-based reach between layers (e.g., `service.repository._query(...)`, `orchestrator._sub_runner._exec(...)`) is a latent refactor-time time bomb. If the call is **load-bearing**, declare it on a Protocol and have the implementation conform.

The cost is one small file. The benefit is that refactors fail at typecheck/test time instead of in production.

## Related

- `tests/test_engine_port.py` — the lockdown.
- `owlex/ports.py` — the contract file. New Ports added here as additional seams emerge.
