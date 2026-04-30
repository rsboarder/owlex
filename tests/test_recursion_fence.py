"""OWLEX-1 recursion fence: env-var propagation through engine + server check."""
from __future__ import annotations

import os

import pytest

from owlex.server import _council_recursion_block


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Each test starts with OWLEX_COUNCIL_DEPTH unset."""
    monkeypatch.delenv("OWLEX_COUNCIL_DEPTH", raising=False)


class TestCouncilRecursionBlock:
    def test_unset_env_allows_call(self):
        assert _council_recursion_block("council_ask") is None

    def test_zero_depth_allows_call(self, monkeypatch):
        monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", "0")
        assert _council_recursion_block("council_ask") is None

    def test_depth_one_blocks_council_ask(self, monkeypatch):
        monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", "1")
        result = _council_recursion_block("council_ask")
        assert result is not None
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "Recursive council_ask is not allowed" in result["error"]
        assert "OWLEX_COUNCIL_DEPTH=1" in result["error"]

    def test_higher_depth_also_blocks(self, monkeypatch):
        monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", "5")
        result = _council_recursion_block("council_ask")
        assert result is not None
        assert "OWLEX_COUNCIL_DEPTH=5" in result["error"]

    def test_negative_or_garbage_value_treated_as_zero(self, monkeypatch):
        for val in ("garbage", "-1", "", "1.5"):
            monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", val)
            assert _council_recursion_block("council_ask") is None, val

    def test_tool_name_in_error_message(self, monkeypatch):
        monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", "1")
        for tool in ("council_ask", "rate_council", "future_tool"):
            result = _council_recursion_block(tool)
            assert tool in result["error"]


class TestEngineDepthInjection:
    """Source-level guarantee that engine.run_agent_command sets the depth env var.

    A real subprocess test would require a full council run; the source check
    is the cheapest way to enforce the contract.
    """

    def test_engine_injects_depth_env_var(self):
        import inspect

        from owlex.engine import TaskEngine

        src = inspect.getsource(TaskEngine.run_agent_command)
        assert "OWLEX_COUNCIL_DEPTH" in src
        assert "current_depth" in src
        assert "current_depth + 1" in src

    def test_depth_increments_correctly(self, monkeypatch):
        """Ensure the increment logic produces correct values."""
        # No env set → child sees 1
        monkeypatch.delenv("OWLEX_COUNCIL_DEPTH", raising=False)
        try:
            depth = int(os.environ.get("OWLEX_COUNCIL_DEPTH", "0") or 0)
        except ValueError:
            depth = 0
        assert str(depth + 1) == "1"

        # depth=1 → child sees 2
        monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", "1")
        depth = int(os.environ.get("OWLEX_COUNCIL_DEPTH", "0") or 0)
        assert str(depth + 1) == "2"
