"""Tests for grok seat startup health-check (auth / CLI / model).

Never raises; failures never cache; successes stamp for TTL reuse.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from owlex.agents import grok as grok_mod


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, *a, **k):
        return self._stdout, self._stderr

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _ok_stdout(text: str = "ok") -> bytes:
    return json.dumps({"text": text, "stopReason": "EndTurn"}).encode()


@pytest.mark.asyncio
async def test_probe_ok_and_caches(monkeypatch):
    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=0, stdout=_ok_stdout())

    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: "/usr/bin/grok")
    monkeypatch.setattr(grok_mod.asyncio, "create_subprocess_exec", fake_exec)

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is True
    assert "probed ok" in msg
    assert len(calls) == 1

    ok2, msg2 = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert ok2 is True
    assert len(calls) == 1, "second call must reuse cache"
    assert "cached" in msg2


@pytest.mark.asyncio
async def test_probe_missing_cli(monkeypatch):
    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: None)
    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is False
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_probe_auth_failure_not_cached(monkeypatch):
    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(
            returncode=1,
            stderr=b"Error: not signed in. Please login.\n",
        )

    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: "/usr/bin/grok")
    monkeypatch.setattr(grok_mod.asyncio, "create_subprocess_exec", fake_exec)

    for _ in range(2):
        ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
        assert ok is False
        assert "auth" in msg.lower() or "signed" in msg.lower()
    assert len(calls) == 2, "failures must never cache"


@pytest.mark.asyncio
async def test_probe_timeout(monkeypatch):
    import asyncio

    class _Hang:
        returncode = None

        async def communicate(self, *a, **k):
            await asyncio.sleep(1000)
            return b"", b""

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return -9

    async def hanging_exec(*args, **kw):
        return _Hang()

    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: "/usr/bin/grok")
    monkeypatch.setattr(grok_mod.asyncio, "create_subprocess_exec", hanging_exec)

    async def fake_wait_for(awaitable, timeout=None):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(grok_mod.asyncio, "wait_for", fake_wait_for)

    ok, msg = await grok_mod.probe_grok_seat(timeout=1.0, use_cache=False)
    assert ok is False
    assert "timed out" in msg.lower()


@pytest.mark.asyncio
async def test_stale_cache_reprobes(monkeypatch):
    grok_mod._write_probe_cache("old ok")
    path = grok_mod._probe_cache_path()
    data = json.loads(path.read_text())
    data["probed_at"] = time.time() - (grok_mod.PROBE_CACHE_TTL + 10)
    data["model"] = grok_mod.config.grok.model
    path.write_text(json.dumps(data))

    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=0, stdout=_ok_stdout())

    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: "/usr/bin/grok")
    monkeypatch.setattr(grok_mod.asyncio, "create_subprocess_exec", fake_exec)

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert ok is True
    assert len(calls) == 1
    assert "cached" not in msg


def test_probe_command_uses_configured_model():
    cmd = grok_mod._build_probe_command()
    assert cmd[0] == "grok"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == grok_mod.config.grok.model
    # Probe must stay tiny — no output-contract appendix
    p = cmd[cmd.index("-p") + 1]
    assert "Output contract" not in p
