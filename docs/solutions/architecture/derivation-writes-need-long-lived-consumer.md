# Derivation writes need a long-lived consumer, not fire-and-forget

## Problem

Owlex had two classes of database write mixed into one request lifecycle:

1. **Canonical facts** (`calls`, `council_anonymization`, `council_outcomes`) — must complete before the MCP response returns.
2. **Derived analytics** (`pairwise_agreements`, `position_deltas`, `skill_invocations`) — computed from facts, not user-facing.

The "obvious" optimization was to keep facts on the critical path but move derivations off it via `asyncio.create_task(...)` — fire-and-forget. This shaved 30-90s from response latency.

**It broke on fast councils.** When R1 reached consensus and R2 was skipped, the council finished in seconds. The MCP server sent the response, no more requests, event loop tore down — and the fire-and-forget tasks were **cancelled mid-flight**. Pairwise rows: 0. Skills rows: 0.

Symptoms in the DB:

```
council_id 132703   pairwise_agreements: 6   (R1+R2, slow enough to finish)
council_id 163400   pairwise_agreements: 0   (R1 consensus → R2 skipped → too fast)
council_id 192255   pairwise_agreements: 0   (same — R1 consensus skipped R2)
```

## Root cause

Background analytics tasks need an **owner** whose lifecycle is independent of any single MCP request. `asyncio.create_task(coro)` returns a Task tied to the current event loop — when the loop wraps up (after the response is sent and no live work remains), pending tasks are cancelled.

**Architectural rule**: never use `asyncio.create_task(...)` for work that must outlive the surrounding request scope, when the request scope itself triggers loop shutdown.

## Solution

Event-sourced derivation pipeline. One module owns:
- a process-wide `asyncio.Queue`,
- a `run_worker()` coroutine that consumes events forever,
- a `shutdown(timeout=N)` that drains the queue before the worker exits.

Producers (Council, Engine) call `derivations.emit(event)` — non-blocking enqueue. Consumers never see the producer's request scope.

```python
# owlex/derivations.py — abridged

@dataclass(frozen=True)
class PairwiseEvent:
    council_id: str
    prompt: str
    r1_contents: dict[str, str]

_queue: asyncio.Queue | None = None

def get_queue():
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue

def emit(event):
    try:
        get_queue().put_nowait(event)
    except RuntimeError:
        pass  # no loop running — backfill recovers

async def run_worker():
    queue = get_queue()
    while True:
        event = await queue.get()
        if event is None:
            queue.task_done()
            return
        try:
            handler = _HANDLERS.get(type(event))
            if handler:
                await handler(event)
        except Exception as e:
            print(f"handler failed: {e}", file=sys.stderr)
        finally:
            queue.task_done()

async def shutdown(timeout: float = 30.0):
    queue = get_queue()
    try:
        await asyncio.wait_for(queue.join(), timeout=timeout)
    except asyncio.TimeoutError:
        pass  # backfill will recover
    await queue.put(None)  # shutdown sentinel
```

`server/main()` starts the worker once and drains on SIGTERM:

```python
derivation_worker = asyncio.create_task(_derivations.run_worker())
try:
    await server_task  # MCP loop
finally:
    await _derivations.shutdown(timeout=30.0)
    await asyncio.wait_for(derivation_worker, timeout=5.0)
```

Council emits instead of fire-and-forget:

```python
# Before:
asyncio.create_task(self._persist_pairwise_safe(prompt, r1_contents))

# After:
_derivations.emit(_derivations.PairwiseEvent(
    council_id=self.council_id, prompt=prompt, r1_contents=r1_contents,
))
```

## Why this works

- The worker's lifetime is bound to the MCP **server process**, not any one request.
- Queue is the explicit handoff boundary: producer side is request-scoped, consumer side is process-scoped.
- Shutdown drain with bounded timeout: graceful path completes pending work; pathological path falls through to backfill (`dashboard/backfill.py` can recompute from canonical facts later).
- Exceptions in handlers are isolated — one bad event never stops the worker.

## Prevention

- **Lint/AST rule**: ban `asyncio.create_task(...)` in any function that performs DB writes inside the create_task'd coroutine. Send to the derivations queue instead. (T1 — pending implementation as a custom AST check; for now, code-review enforced.)
- **Regression tests** (`tests/test_derivations.py`):
  - `test_emit_and_drain_runs_handler` — submitted event reaches handler before drain returns.
  - `test_shutdown_drains_pending_events` — events enqueued before shutdown are processed.
  - `test_handler_exception_does_not_kill_worker` — one bad event must not stop the line.

## Related

- See `docs/solutions/subprocess/fail-pattern-recovery.md` — same architectural lesson at the subprocess level.
- `dashboard/backfill.py` — recovery path when live derivation drops an event.
