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
import importlib
import json
import time
from pathlib import Path

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


# === Judge cwd: no repo AGENTS.md in the prompt ===
#
# Without --cd, codex inherits the MCP server's cwd (the project root) and
# auto-loads that repo's AGENTS.md into every judge prompt. The judge is pure
# text classification in a read-only sandbox, so repo context is dead weight.


def test_judge_command_pins_an_empty_cwd():
    cmd = agreement._build_judge_command()
    assert "--cd" in cmd
    cwd = Path(cmd[cmd.index("--cd") + 1])
    assert cwd.is_dir()
    assert not any(cwd.iterdir()), "judge cwd must stay empty so codex finds no AGENTS.md"


def test_judge_cwd_is_idempotent():
    """Concurrent judge calls must not race on creating the scratch dir."""
    assert agreement._judge_cwd() == agreement._judge_cwd()


# === Probe cache ===
#
# The probe answers "is the pinned model still in the codex catalog" — weeks-
# scale truth — but re-ran on every MCP server start. Successes are stamped;
# failures never are, so a model going away is still caught next start.


def _write_stamp(monkeypatch, *, model: str, age_seconds: float, message: str = "probed ok") -> None:
    agreement._write_probe_cache(message)
    path = agreement._probe_cache_path()
    data = json.loads(path.read_text())
    data["model"] = model
    data["probed_at"] = time.time() - age_seconds
    path.write_text(json.dumps(data))


@pytest.mark.asyncio
async def test_fresh_success_is_reused_without_spawning_codex(monkeypatch):
    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=0, stdout=b"codex\nOK\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)

    ok, _ = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True
    assert len(calls) == 1

    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True
    assert len(calls) == 1, "second start must reuse the stamp, not spawn codex"
    assert "cached" in msg, "a reused result must be distinguishable in the log"


@pytest.mark.asyncio
async def test_failure_is_never_cached(monkeypatch):
    """A model going away must be re-detected on every start."""
    calls = []

    async def failing_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=1, stdout=b"ERROR: model is not supported\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", failing_exec)

    for _ in range(2):
        ok, _ = await agreement.probe_agreement_model(timeout=5.0)
        assert ok is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_stale_stamp_triggers_a_real_probe(monkeypatch):
    _write_stamp(monkeypatch, model=agreement.AGREEMENT_MODEL,
                 age_seconds=agreement.PROBE_CACHE_TTL + 60)
    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=0, stdout=b"codex\nOK\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, msg = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True
    assert len(calls) == 1
    assert "cached" not in msg


@pytest.mark.asyncio
async def test_stamp_for_a_different_model_is_ignored(monkeypatch):
    _write_stamp(monkeypatch, model="some-other-model", age_seconds=10)
    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=0, stdout=b"codex\nOK\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, _ = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_corrupt_stamp_does_not_break_startup(monkeypatch):
    path = agreement._probe_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stdout=b"codex\nOK\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, _ = await agreement.probe_agreement_model(timeout=5.0)
    assert ok is True


@pytest.mark.asyncio
async def test_use_cache_false_forces_a_real_probe(monkeypatch):
    _write_stamp(monkeypatch, model=agreement.AGREEMENT_MODEL, age_seconds=10)
    calls = []

    async def fake_exec(*args, **kw):
        calls.append(args)
        return _FakeProc(returncode=0, stdout=b"codex\nOK\n")

    monkeypatch.setattr(agreement.asyncio, "create_subprocess_exec", fake_exec)
    ok, _ = await agreement.probe_agreement_model(timeout=5.0, use_cache=False)
    assert ok is True
    assert len(calls) == 1


def test_probe_ttl_is_env_overridable(monkeypatch):
    monkeypatch.setenv("OWLEX_AGREEMENT_PROBE_TTL", "111")
    reloaded = importlib.reload(agreement)
    try:
        assert reloaded.PROBE_CACHE_TTL == 111
    finally:
        monkeypatch.delenv("OWLEX_AGREEMENT_PROBE_TTL", raising=False)
        importlib.reload(reloaded)
