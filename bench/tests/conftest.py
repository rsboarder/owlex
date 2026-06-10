"""Defensive OWLEX_HOME isolation for bench tests.

The main suite's autouse fixture lives in ``tests/conftest.py`` and does not
reach ``bench/``. Bench scorer tests are pure and never import the SQLite store,
but ``bench/run.py`` imports ``owlex.second_opinion`` (and transitively the
``owlex`` package); pointing OWLEX_HOME at a tmp dir here is cheap insurance
against the documented prod-DB pollution failure mode (CLAUDE.md learned
pattern: "Tests Must Override Path.home()-Based Globals").
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolate_owlex_home(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="owlex-bench-home-") as tmp:
        monkeypatch.setenv("OWLEX_HOME", tmp)
        yield tmp
