"""Judge timeout is configurable.

Cursor-agent CLI v2026.05.16 silent-buffers output for 30-60s before emitting
any tokens on council-sized prompts. The historical 30s judge timeout fired
on the prompt-think phase rather than on real failure, so every council
ended up in fallback heuristic mode with reason 'judge timeout'. The
timeout is now read from OWLEX_AGREEMENT_TIMEOUT (default 90s).
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


def _reload_agreement():
    from owlex import agreement
    importlib.reload(agreement)
    return agreement


def test_default_judge_timeout_is_90s(monkeypatch):
    monkeypatch.delenv("OWLEX_AGREEMENT_TIMEOUT", raising=False)
    agreement = _reload_agreement()
    assert agreement.DEFAULT_JUDGE_TIMEOUT == 90


def test_env_override_parses(monkeypatch):
    monkeypatch.setenv("OWLEX_AGREEMENT_TIMEOUT", "150")
    agreement = _reload_agreement()
    assert agreement.DEFAULT_JUDGE_TIMEOUT == 150


@pytest.mark.asyncio
async def test_score_agreement_uses_default_when_no_timeout_arg(monkeypatch):
    """When the caller doesn't pass timeout, the resolved value is read from
    DEFAULT_JUDGE_TIMEOUT at call time — not from the function signature.
    """
    monkeypatch.setenv("OWLEX_AGREEMENT_TIMEOUT", "120")
    agreement = _reload_agreement()

    captured: dict[str, object] = {}

    async def fake_wait_for(coro, timeout):
        captured["timeout"] = timeout
        # Cancel the coroutine to release the subprocess we never started.
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(agreement.asyncio, "wait_for", fake_wait_for)

    # Real subprocess creation is fine — wait_for fires immediately.
    score, reason = await agreement.score_agreement(
        "q?", {"a": "answer1", "b": "answer2"},
    )

    assert captured["timeout"] == 120
    # On timeout we fall back to overlap heuristic; reason should reflect that.
    assert "timeout" in reason.lower() or score is not None
