"""Behavior tests for the lean second_opinion primitive and MCP tool."""
from __future__ import annotations

import asyncio

import pytest

from owlex import second_opinion as so


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input: bytes | None = None):
        return self._stdout, self._stderr


_VALID_JSONL = (
    'ERROR: codex skill load noise that is not json\n'
    '{"type":"thread.started","thread_id":"t1"}\n'
    '{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"First part."}}\n'
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Second part."}}\n'
    '{"type":"turn.completed"}\n'
)


def test_extract_joins_agent_messages_and_strips_noise():
    text = so._extract_final_message(_VALID_JSONL)
    assert "First part." in text
    assert "Second part." in text
    assert "ERROR" not in text
    assert "thread.started" not in text


def test_extract_empty_on_garbage():
    assert so._extract_final_message("not json\n{broken\n") == ""
    assert so._extract_final_message("") == ""


def test_cmd_builds_expected_argv():
    cmd = so._cmd("/tmp/x")
    assert cmd[0] == "codex" and cmd[1] == "exec"
    assert "--json" in cmd
    assert "--model" in cmd
    assert "--sandbox" in cmd and "read-only" in cmd
    assert "--cd" in cmd and "/tmp/x" in cmd
    assert cmd[-1] == "-"


@pytest.mark.asyncio
async def test_get_second_opinion_success(monkeypatch):
    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stdout=_VALID_JSONL.encode())

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    ok, text, timed_out = await so.get_second_opinion("review this", working_directory="/tmp")
    assert ok is True
    assert timed_out is False
    assert "First part." in text


@pytest.mark.asyncio
async def test_get_second_opinion_fails_closed_on_empty_extraction(monkeypatch):
    """returncode 0 but unparseable output → fail-closed (False)."""
    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stdout=b"garbage not json\n")

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    ok, _, timed_out = await so.get_second_opinion("q", working_directory="/tmp")
    assert ok is False
    assert timed_out is False


@pytest.mark.asyncio
async def test_get_second_opinion_nonzero_returncode(monkeypatch):
    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    ok, _, timed_out = await so.get_second_opinion("q", working_directory="/tmp")
    assert ok is False
    assert timed_out is False


@pytest.mark.asyncio
async def test_get_second_opinion_timeout(monkeypatch):
    class _SlowProc:
        returncode = 0

        async def communicate(self, input: bytes | None = None):
            await asyncio.sleep(10)
            return b"", b""

    async def fake_exec(*args, **kw):
        return _SlowProc()

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    ok, _, timed_out = await so.get_second_opinion("q", working_directory="/tmp", timeout=0.1)
    assert ok is False
    assert timed_out is True


@pytest.mark.asyncio
async def test_get_second_opinion_reaps_child_on_timeout(monkeypatch):
    """On timeout the spawned child must be killed and reaped, not orphaned."""
    reaped = {"killed": False, "waited": False}

    class _HangProc:
        returncode = None

        async def communicate(self, input: bytes | None = None):
            await asyncio.sleep(10)
            return b"", b""

        def kill(self):
            reaped["killed"] = True
            self.returncode = -9

        async def wait(self):
            reaped["waited"] = True
            return self.returncode

    async def fake_exec(*args, **kw):
        return _HangProc()

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    ok, _, timed_out = await so.get_second_opinion("q", working_directory="/tmp", timeout=0.05)
    assert ok is False
    assert timed_out is True
    assert reaped["killed"] is True
    assert reaped["waited"] is True


@pytest.mark.asyncio
async def test_get_second_opinion_missing_cli(monkeypatch):
    async def missing_exec(*args, **kw):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", missing_exec)
    ok, _, timed_out = await so.get_second_opinion("q", working_directory="/tmp")
    assert ok is False
    assert timed_out is False


@pytest.mark.asyncio
async def test_tool_success(monkeypatch):
    from owlex.server import second_opinion as tool

    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stdout=_VALID_JSONL.encode())

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    result = await tool(prompt="review this", working_directory="/tmp")
    assert result["success"] is True
    assert result["model"] == so.MODEL
    assert "First part." in result["opinion"]


@pytest.mark.asyncio
async def test_tool_rejects_blank_prompt(monkeypatch):
    from owlex.server import second_opinion as tool

    result = await tool(prompt="   ")
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGS"


@pytest.mark.asyncio
async def test_tool_rejects_bad_working_directory(monkeypatch):
    from owlex.server import second_opinion as tool

    result = await tool(prompt="q", working_directory="/nonexistent/path/xyz123")
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGS"


@pytest.mark.asyncio
async def test_tool_maps_timeout_to_timeout_code(monkeypatch):
    """A timeout from the primitive must surface as ErrorCode.TIMEOUT, not EXECUTION_FAILED."""
    from owlex.server import second_opinion as tool

    class _HangProc:
        returncode = None

        async def communicate(self, input: bytes | None = None):
            await asyncio.sleep(10)
            return b"", b""

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def fake_exec(*args, **kw):
        return _HangProc()

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    result = await tool(prompt="q", working_directory="/tmp", timeout=0.05)
    assert result["success"] is False
    assert result["error_code"] == "TIMEOUT"


@pytest.mark.asyncio
async def test_tool_logs_invocation_and_outcome(monkeypatch):
    """Every invocation must leave a server-log trace (observability fix)."""
    import owlex.server._second_opinion as so_mod

    emitted: list[str] = []
    monkeypatch.setattr(so_mod, "log", lambda msg: emitted.append(msg))

    async def fake_exec(*args, **kw):
        return _FakeProc(returncode=0, stdout=_VALID_JSONL.encode())

    monkeypatch.setattr(so.asyncio, "create_subprocess_exec", fake_exec)
    result = await so_mod.second_opinion(prompt="review this", working_directory="/tmp")
    assert result["success"] is True
    # entry trace + outcome trace — assert behavior (log fired), not wording
    assert len(emitted) >= 2
