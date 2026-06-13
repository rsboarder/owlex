"""Startup health-check for the agreement-judge model.

External CLI catalogs (codex's ChatGPT-account allowlist in particular)
rotate. A pinned model name that worked last week may return 400 today.
Without a probe, owlex silently falls back to overlap-heuristic for weeks
before anyone notices that `agreement_reason` keeps saying 'judge failed'.

The probe runs at server start. It is non-blocking: failure does NOT stop
the server; it logs a clear warning. The judge itself still falls back to
heuristic at council time.
"""
from __future__ import annotations

import asyncio

import pytest

from owlex import agreement


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input: bytes | None = None):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_probe_ok(monkeypatch):
    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stdout=b"codex\nOK\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True
    assert "probed ok" in msg


@pytest.mark.asyncio
async def test_probe_detects_missing_model(monkeypatch):
    """Codex's error shape when a model name is not in the ChatGPT-account allowlist."""
    out = (
        b'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
        b'"message":"The \'fake-model\' model is not supported when using Codex with a ChatGPT account."}}\n'
    )

    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=1, stdout=out)

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is False
    assert "not in codex catalog" in msg
    assert "OWLEX_AGREEMENT_MODEL" in msg  # tells the user how to fix


@pytest.mark.asyncio
async def test_probe_handles_timeout(monkeypatch):
    class _SlowProc:
        returncode = 0

        async def communicate(self, input: bytes | None = None):
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
        raise FileNotFoundError("codex")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", missing_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is False
    assert "codex CLI not found" in msg
