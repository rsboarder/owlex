"""Regression contract: owlex's own codex calls rotate from ONE pin.

codex's model catalog rotates, and a dead pin fails quietly: the agreement
judge degrades to the term-overlap heuristic while still returning a score,
and ``second_opinion`` fails outright. In 2026-10 both modules carried their
own ``"gpt-5.5"`` default, so a single rotation broke both — and because only
the judge has a fallback, 19 of 21 councils scored agreement by string
overlap for nine days before anyone looked.

These tests lock the single rotation point so the next rotation is one edit.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from owlex import agreement, second_opinion
from owlex.config import CODEX_DEFAULT_MODEL


_OWLEX_DIR = Path(__file__).resolve().parent.parent / "owlex"

# Call sites that run codex as owlex itself (not as a council seat, which
# carries its own model): module path → the module-level name holding the model.
_PINNED_CALL_SITES = {
    "agreement.py": "AGREEMENT_MODEL",
    "second_opinion.py": "MODEL",
}


def _default_arg_source(path: Path, target: str) -> str:
    """Return the source of the fallback passed to ``os.getenv`` for ``target``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if target not in names:
            continue
        call = node.value
        assert isinstance(call, ast.Call), f"{path.name}:{target} is not an os.getenv call"
        assert len(call.args) == 2, f"{path.name}:{target} has no explicit fallback"
        return ast.unparse(call.args[1])
    pytest.fail(f"{path.name} no longer assigns {target} at module scope")


@pytest.mark.parametrize(("filename", "target"), sorted(_PINNED_CALL_SITES.items()))
def test_call_site_falls_back_to_shared_pin(filename, target):
    """Each call site must defer to CODEX_DEFAULT_MODEL, not its own literal."""
    assert _default_arg_source(_OWLEX_DIR / filename, target) == "CODEX_DEFAULT_MODEL", (
        f"{filename} pins its own codex model again. Keep the fallback as "
        "CODEX_DEFAULT_MODEL so one rotation fixes every call site at once."
    )


def test_no_module_hardcodes_a_codex_model_literal():
    """Only config.py may name a concrete gpt-* model."""
    offenders = []
    for path in sorted(_OWLEX_DIR.rglob("*.py")):
        if path.name == "config.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""["']gpt-[\w.\-]+["']""", line):
                offenders.append(f"{path.relative_to(_OWLEX_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "concrete gpt-* model literals outside config.py:\n  " + "\n  ".join(offenders)
    )


def test_shared_pin_is_env_overridable(monkeypatch):
    """Recovery from a rotation must not need a code change."""
    monkeypatch.setenv("OWLEX_CODEX_MODEL", "gpt-9-imaginary")
    import importlib

    import owlex.config

    try:
        reloaded = importlib.reload(owlex.config)
        assert reloaded.CODEX_DEFAULT_MODEL == "gpt-9-imaginary"
    finally:
        monkeypatch.delenv("OWLEX_CODEX_MODEL", raising=False)
        importlib.reload(owlex.config)


def test_resolved_models_are_non_empty():
    """A blank env var must not silently produce a model-less codex command."""
    for name, value in (
        ("agreement.AGREEMENT_MODEL", agreement.AGREEMENT_MODEL),
        ("second_opinion.MODEL", second_opinion.MODEL),
        ("config.CODEX_DEFAULT_MODEL", CODEX_DEFAULT_MODEL),
    ):
        assert value and value.strip(), f"{name} resolved to an empty model"
