"""Event-sourced derivation pipeline.

Owlex separates two classes of write at the persistence layer:

  1. **Canonical facts** (block the MCP response):
     - `calls`, `council_anonymization`, `council_outcomes`

  2. **Derived analytics** (DO NOT block the MCP response):
     - `pairwise_agreements`, `position_deltas` (council.py)
     - `skill_invocations`              (engine.py)

Before this module existed, derivations were either inline-awaited (adding
30-90s to user-visible latency on a 4-agent council) or fire-and-forget via
``asyncio.create_task`` (subject to event-loop shutdown losing the write
silently when the council finished fast — e.g. R1 consensus skips R2).

This module owns the queue + long-lived consumer that decouples those two
lifecycles:

  Council/Engine emits a ``DerivationEvent`` → put on a process-wide queue.
  The worker (started in ``server.main()``) pops events forever and runs the
  matching handler, persisting results when ready. On server shutdown the
  worker drains remaining events with a bounded timeout, so a graceful
  ``SIGTERM`` does not lose work.

The same derivation handlers are reused by ``dashboard/backfill.py`` for
historical recovery, so derivation logic has one canonical implementation.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CouncilRound, Task


# === Event types ===

@dataclass(frozen=True)
class PairwiseEvent:
    """Compute and persist C(N,2) pairwise agreement scores for a council's R1."""
    council_id: str
    prompt: str
    r1_contents: dict[str, str]


@dataclass(frozen=True)
class PositionDeltaEvent:
    """Compute and persist R1→R2 Jaccard distance per agent that participated in both."""
    round_1: "CouncilRound"
    round_2: "CouncilRound"


@dataclass(frozen=True)
class SkillsEvent:
    """Parse a completed task's agent transcript and persist skill_invocations."""
    task_id: str
    seat: str
    runner: str
    ts: str


DerivationEvent = PairwiseEvent | PositionDeltaEvent | SkillsEvent


# === Handlers (single source of truth for derivation logic) ===

async def _handle_pairwise(event: PairwiseEvent) -> None:
    from . import store
    from .agreement import score_agreement

    agents = sorted(event.r1_contents.keys())
    if len(agents) < 2:
        return
    pairs = [(agents[i], agents[j]) for i in range(len(agents)) for j in range(i + 1, len(agents))]
    sem = asyncio.Semaphore(5)

    async def _one(a: str, b: str) -> tuple[str, str, float, str]:
        async with sem:
            score, reason = await score_agreement(
                event.prompt, {a: event.r1_contents[a], b: event.r1_contents[b]},
            )
            return a, b, float(score), reason

    results = await asyncio.gather(*[_one(a, b) for a, b in pairs], return_exceptions=True)
    rows: list[tuple[str, str, float, str | None]] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        rows.append(r)  # type: ignore[arg-type]
    if rows:
        store.record_pairwise_agreements(event.council_id, rows, source="judge")
        _log(f"pairwise: persisted {len(rows)} pairs for {event.council_id}")


async def _handle_position_delta(event: PositionDeltaEvent) -> None:
    from . import store
    from .models import Agent

    def _word_set(s: str) -> set[str]:
        return {w.lower().strip(".,;:!?()\"'`") for w in s.split() if len(w) > 5 and w.isalpha()}

    for agent in Agent:
        r1 = getattr(event.round_1, agent.value, None)
        r2 = getattr(event.round_2, agent.value, None)
        if not (r1 and r2 and r1.content and r2.content and r2.task_id):
            continue
        t1 = _word_set(r1.content)
        t2 = _word_set(r2.content)
        if not t1 and not t2:
            continue
        inter = len(t1 & t2)
        union = len(t1 | t2) or 1
        delta = 1.0 - (inter / union)
        label = "unchanged" if delta < 0.845 else "minor" if delta < 0.906 else "major"
        store.record_position_delta(r2.task_id, position_delta=delta, position_label=label)


async def _handle_skills(event: SkillsEvent) -> None:
    from .dashboard.parsers import parse_and_persist
    await asyncio.to_thread(
        parse_and_persist,
        event.task_id, event.seat, event.ts, None,
        runner=event.runner,
    )


_HANDLERS: dict[type, callable] = {  # type: ignore[type-arg]
    PairwiseEvent: _handle_pairwise,
    PositionDeltaEvent: _handle_position_delta,
    SkillsEvent: _handle_skills,
}


# === Queue + worker ===

_queue: "asyncio.Queue[DerivationEvent | None] | None" = None


def get_queue() -> "asyncio.Queue[DerivationEvent | None]":
    """Process-wide singleton queue. Lazily created on first access.

    Producers (Council, Engine) call this; consumers (the worker) also call it.
    The queue lives for the lifetime of the running event loop.
    """
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def emit(event: DerivationEvent) -> None:
    """Non-blocking enqueue. If no event loop is running (e.g. tests outside
    asyncio), the event is silently dropped — derivations are best-effort and
    must never raise into the caller.
    """
    try:
        get_queue().put_nowait(event)
    except RuntimeError:
        # No running event loop. Acceptable: backfill will recompute later.
        pass
    except Exception as e:
        _log(f"emit failed: {e}")


async def run_worker() -> None:
    """Long-lived consumer. Pops events forever; ``None`` is the shutdown sentinel.

    Exceptions in handlers are logged but never propagate — one bad event must
    not stop the worker. Started by ``server.main()`` alongside the MCP server.
    """
    queue = get_queue()
    _log("derivation worker started")
    while True:
        event = await queue.get()
        if event is None:
            queue.task_done()
            _log("derivation worker received shutdown sentinel")
            return
        try:
            handler = _HANDLERS.get(type(event))
            if handler is None:
                _log(f"no handler for {type(event).__name__}; dropping")
            else:
                await handler(event)
        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            _log(f"handler {type(event).__name__} failed: {e}\n{tb}")
        finally:
            queue.task_done()


async def drain(timeout: float = 30.0) -> int:
    """Wait for the queue to empty, with a bounded deadline.

    Called from ``server.main()`` on shutdown. Returns the number of events
    that did not complete in time (zero on clean drain).
    """
    queue = get_queue()
    pending_before = queue.qsize()
    if pending_before == 0:
        return 0
    _log(f"draining {pending_before} pending derivation events (timeout {timeout}s)")
    try:
        await asyncio.wait_for(queue.join(), timeout=timeout)
        _log("derivation queue drained cleanly")
        return 0
    except asyncio.TimeoutError:
        remaining = queue.qsize()
        _log(f"derivation drain timed out with {remaining} events pending (run backfill to recover)")
        return remaining


async def shutdown(timeout: float = 30.0) -> None:
    """Signal worker to stop and wait for drain.

    Puts the shutdown sentinel after the drain to ensure all enqueued events
    are processed first. Idempotent — safe to call multiple times.
    """
    queue = get_queue()
    await drain(timeout=timeout)
    await queue.put(None)


def _log(msg: str) -> None:
    print(f"[owlex.derivations] {msg}", file=sys.stderr, flush=True)


def _reset_for_tests() -> None:
    """Reset the singleton queue. Tests use this to isolate between cases."""
    global _queue
    _queue = None
