"""Event-sourced derivation worker.

The worker decouples analytics writes (pairwise, position-deltas, skills)
from the MCP response lifecycle. Without this, fast councils where R1
reaches consensus and R2 is skipped lose pairwise writes because the
event loop tears down before the fire-and-forget task completes.
"""
from __future__ import annotations

import asyncio

import pytest

from owlex import derivations


@pytest.mark.asyncio
async def test_emit_and_drain_runs_handler(monkeypatch):
    """A submitted PairwiseEvent reaches its handler before drain returns."""
    handled: list[derivations.PairwiseEvent] = []

    async def fake_handler(event):
        handled.append(event)

    monkeypatch.setitem(derivations._HANDLERS, derivations.PairwiseEvent, fake_handler)

    worker = asyncio.create_task(derivations.run_worker())
    try:
        derivations.emit(derivations.PairwiseEvent(
            council_id="c1", prompt="p", r1_contents={"a": "x", "b": "y"},
        ))
        await derivations.drain(timeout=2.0)
    finally:
        await derivations.shutdown(timeout=1.0)
        await asyncio.wait_for(worker, timeout=2.0)

    assert len(handled) == 1
    assert handled[0].council_id == "c1"


@pytest.mark.asyncio
async def test_handler_exception_does_not_kill_worker(monkeypatch):
    """Worker keeps consuming after a handler raises — bad event must not stop the line."""
    seen_after_error: list[str] = []

    calls: list[int] = []

    async def flaky_handler(event):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first event blows up")
        seen_after_error.append(event.council_id)

    monkeypatch.setitem(derivations._HANDLERS, derivations.PairwiseEvent, flaky_handler)

    worker = asyncio.create_task(derivations.run_worker())
    try:
        derivations.emit(derivations.PairwiseEvent(
            council_id="bad", prompt="p", r1_contents={"a": "x"},
        ))
        derivations.emit(derivations.PairwiseEvent(
            council_id="good", prompt="p", r1_contents={"a": "x"},
        ))
        await derivations.drain(timeout=2.0)
    finally:
        await derivations.shutdown(timeout=1.0)
        await asyncio.wait_for(worker, timeout=2.0)

    assert seen_after_error == ["good"]


@pytest.mark.asyncio
async def test_emit_without_loop_does_not_raise(monkeypatch):
    """``emit`` called outside an event loop must drop silently — derivations
    are best-effort; backfill is the recovery path."""
    # Reset and bypass the queue creation by NOT starting the worker. The emit
    # below happens in the test's running loop, so it actually enqueues; this
    # proves emit doesn't propagate when something goes wrong inside it.
    # (We can't easily simulate "no event loop" inside an async test, so
    # instead we verify emit catches generic queue errors.)
    derivations._reset_for_tests()
    queue = derivations.get_queue()

    # Force put_nowait to raise by replacing with a sentinel that fails.
    def _explode(_event):
        raise RuntimeError("queue is broken")
    monkeypatch.setattr(queue, "put_nowait", _explode)

    # Must NOT raise.
    derivations.emit(derivations.PairwiseEvent(
        council_id="x", prompt="p", r1_contents={},
    ))


@pytest.mark.asyncio
async def test_shutdown_drains_pending_events(monkeypatch):
    """Events enqueued before shutdown are processed during drain."""
    processed: list[str] = []

    async def slow_handler(event):
        await asyncio.sleep(0.05)
        processed.append(event.council_id)

    monkeypatch.setitem(derivations._HANDLERS, derivations.PairwiseEvent, slow_handler)

    worker = asyncio.create_task(derivations.run_worker())
    try:
        for cid in ("a", "b", "c"):
            derivations.emit(derivations.PairwiseEvent(
                council_id=cid, prompt="p", r1_contents={},
            ))
        # Trigger graceful shutdown — drain should wait for all three.
        await derivations.shutdown(timeout=5.0)
        await asyncio.wait_for(worker, timeout=2.0)
    except Exception:
        worker.cancel()
        raise

    assert sorted(processed) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_pairwise_handler_persists_via_store(monkeypatch):
    """End-to-end smoke: real handler invokes store.record_pairwise_agreements."""
    recorded: list[tuple] = []

    def fake_record(council_id, rows, *, source):
        recorded.append((council_id, rows, source))

    monkeypatch.setattr("owlex.store.record_pairwise_agreements", fake_record)

    async def fake_score(prompt, contents):
        return 4.0, "agreed"

    monkeypatch.setattr("owlex.agreement.score_agreement", fake_score)

    event = derivations.PairwiseEvent(
        council_id="c1", prompt="q?",
        r1_contents={"a": "answer a", "b": "answer b", "c": "answer c"},
    )
    await derivations._handle_pairwise(event)

    # C(3,2) = 3 pairs.
    assert len(recorded) == 1
    cid, rows, source = recorded[0]
    assert cid == "c1"
    assert len(rows) == 3
    assert source == "judge"
