"""Skill parser must dispatch by runner, not seat.

Regression: when claudeor was substituted to codex, the parser still scanned
~/.claude/projects/ for the parent Claude Code session's transcript and
mis-attributed the parent's tool calls (council_ask, rate_council, ...) to
the council's claudeor task. Dispatching by ``runner`` instead of ``seat``
prevents that.
"""
from __future__ import annotations

from owlex.dashboard import parsers


class _Stub:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple] = []

    def __call__(self, task_id, ts, session_id=None):
        self.calls.append((task_id, ts, session_id))
        return [{"ts": ts, "kind": "tool", "name": f"sentinel_{self.name}"}]


def test_dispatch_falls_back_to_agent_when_no_runner(monkeypatch):
    stub_claudeor = _Stub("claudeor")
    monkeypatch.setitem(parsers._REGISTRY, "claudeor", stub_claudeor)

    result = parsers.parse_for("claudeor", "tid", "2026-05-01T00:00")

    assert stub_claudeor.calls == [("tid", "2026-05-01T00:00", None)]
    assert result == [{"ts": "2026-05-01T00:00", "kind": "tool", "name": "sentinel_claudeor"}]


def test_dispatch_routes_substituted_seat_to_runner_parser(monkeypatch):
    """Substituted claudeor->codex must use codex parser, not claudeor's."""
    stub_claudeor = _Stub("claudeor")
    stub_codex = _Stub("codex")
    monkeypatch.setitem(parsers._REGISTRY, "claudeor", stub_claudeor)
    monkeypatch.setitem(parsers._REGISTRY, "codex", stub_codex)

    result = parsers.parse_for("claudeor", "tid", "2026-05-01T00:00", runner="codex")

    # Crucial: the claudeor parser must NOT have been invoked. Otherwise the
    # parent Claude Code session leaks into the council's claudeor task.
    assert stub_claudeor.calls == []
    assert stub_codex.calls == [("tid", "2026-05-01T00:00", None)]
    assert result[0]["name"] == "sentinel_codex"


def test_dispatch_unknown_runner_returns_empty(monkeypatch):
    monkeypatch.setitem(parsers._REGISTRY, "codex", _Stub("codex"))
    assert parsers.parse_for("claudeor", "tid", "2026-05-01T00:00", runner="unknown") == []
