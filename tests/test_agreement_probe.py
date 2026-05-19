"""Startup health-check for the agreement-judge model.

External CLI catalogs (cursor-agent in particular) rotate frequently. A
pinned model name that worked last week may be 404 today. Without a probe,
owlex silently falls back to overlap-heuristic for weeks before anyone
notices that `agreement_reason` keeps saying 'judge failed'.

The probe runs at server start. It is non-blocking: failure does NOT stop
the server; it logs a clear warning. The judge itself still falls back to
heuristic at council time.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from owlex import agreement


class _FakeProc:
    def __init__(self, returncode: int, stderr: bytes):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return b"OK\n", self._stderr


@pytest.mark.asyncio
async def test_probe_ok(monkeypatch):
    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stderr=b"")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True
    assert "probed ok" in msg


@pytest.mark.asyncio
async def test_probe_detects_missing_model(monkeypatch):
    """Cursor's exact error shape when a model name was removed from catalog."""
    err = b"Cannot use this model: gemini-2.5-flash. Available models: gemini-3-flash, gpt-5.5..."

    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=1, stderr=err)

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is False
    assert "not in cursor-agent catalog" in msg
    assert "OWLEX_AGREEMENT_MODEL" in msg  # tells the user how to fix


@pytest.mark.asyncio
async def test_probe_handles_timeout(monkeypatch):
    class _SlowProc:
        returncode = 0

        async def communicate(self):
            await asyncio.sleep(10)
            return b"", b""

    async def fake_exec(*args, **kw):
        return _SlowProc()

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=0.1)
    assert ok is False
    assert "timed out" in msg


@pytest.mark.asyncio
async def test_probe_handles_missing_cli(monkeypatch):
    async def missing_exec(*args, **kw):
        raise FileNotFoundError("agent")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", missing_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is False
    assert "cursor-agent CLI not found" in msg
