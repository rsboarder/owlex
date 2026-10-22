"""Server logs must not grow without bound.

Every MCP server start opens a new ``server-{pid}.log``. Nothing pruned them,
so 9373 files / 44 MB had piled up by 2026-10 — unbounded growth in the
operator's home directory with no signal that it was happening.
"""
from __future__ import annotations

import os
import time

import pytest

from owlex.server import _prune_old_server_logs


RETENTION_ENV = "OWLEX_LOG_RETENTION_DAYS"


def _aged_file(directory, name, days_old):
    path = directory / name
    path.write_text("x", encoding="utf-8")
    stamp = time.time() - days_old * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_prunes_logs_past_the_retention_window(tmp_path, monkeypatch):
    monkeypatch.setenv(RETENTION_ENV, "14")
    stale = _aged_file(tmp_path, "server-111.log", days_old=30)
    fresh = _aged_file(tmp_path, "server-222.log", days_old=3)

    _prune_old_server_logs(tmp_path)

    assert not stale.exists()
    assert fresh.exists()


def test_leaves_files_this_module_did_not_write(tmp_path, monkeypatch):
    """Only the server-*.log pattern is ours; operators may keep other files."""
    monkeypatch.setenv(RETENTION_ENV, "14")
    foreign = _aged_file(tmp_path, "keep-me.txt", days_old=400)
    nested = tmp_path / "archive"
    nested.mkdir()
    archived = _aged_file(nested, "server-333.log", days_old=400)

    _prune_old_server_logs(tmp_path)

    assert foreign.exists()
    assert archived.exists(), "the sweep must not recurse into subdirectories"


def test_never_deletes_the_log_of_the_running_server(tmp_path, monkeypatch):
    """A clock skew that back-dates the active log must not unlink it."""
    monkeypatch.setenv(RETENTION_ENV, "14")
    current = _aged_file(tmp_path, f"server-{os.getpid()}.log", days_old=999)

    _prune_old_server_logs(tmp_path)

    assert current.exists()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_retention_off_keeps_everything(tmp_path, monkeypatch, value):
    """Zero or negative retention disables pruning entirely."""
    monkeypatch.setenv(RETENTION_ENV, value)
    ancient = _aged_file(tmp_path, "server-444.log", days_old=999)

    _prune_old_server_logs(tmp_path)

    assert ancient.exists()


def test_malformed_retention_falls_back_to_the_default_window(tmp_path, monkeypatch):
    """A typo must neither crash startup nor silently keep logs forever."""
    monkeypatch.setenv(RETENTION_ENV, "not_a_number")
    stale = _aged_file(tmp_path, "server-555.log", days_old=30)
    fresh = _aged_file(tmp_path, "server-666.log", days_old=3)

    _prune_old_server_logs(tmp_path)

    assert not stale.exists()
    assert fresh.exists()


def test_a_missing_directory_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv(RETENTION_ENV, "14")
    _prune_old_server_logs(tmp_path / "does-not-exist")
