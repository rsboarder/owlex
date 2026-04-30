"""Refactor #6: env-var parsing helpers in config.py."""
from __future__ import annotations

import pytest

from owlex.config import _get_bool, _get_csv, _get_int, _get_str_or_none, _load_substitution_models


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for var in (
        "X_BOOL", "X_INT", "X_STR", "X_CSV", "COUNCIL_SUBSTITUTION_MODELS",
    ):
        monkeypatch.delenv(var, raising=False)


class TestGetBool:
    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "on", "y"])
    def test_truthy(self, monkeypatch, val):
        monkeypatch.setenv("X_BOOL", val)
        assert _get_bool("X_BOOL", False) is True

    @pytest.mark.parametrize("val", ["false", "False", "0", "no", "off", "n"])
    def test_falsy(self, monkeypatch, val):
        monkeypatch.setenv("X_BOOL", val)
        assert _get_bool("X_BOOL", True) is False

    def test_missing_returns_default(self):
        assert _get_bool("X_BOOL", True) is True
        assert _get_bool("X_BOOL", False) is False

    def test_invalid_falls_back_to_default(self, monkeypatch, capsys):
        monkeypatch.setenv("X_BOOL", "maybe")
        assert _get_bool("X_BOOL", True) is True
        assert "is not a valid boolean" in capsys.readouterr().err


class TestGetInt:
    def test_valid(self, monkeypatch):
        monkeypatch.setenv("X_INT", "42")
        assert _get_int("X_INT", 1) == 42

    def test_default_when_missing(self):
        assert _get_int("X_INT", 7) == 7

    def test_invalid_falls_back(self, monkeypatch, capsys):
        monkeypatch.setenv("X_INT", "not-a-number")
        assert _get_int("X_INT", 7) == 7
        assert "Invalid X_INT" in capsys.readouterr().err

    def test_below_min_falls_back(self, monkeypatch, capsys):
        monkeypatch.setenv("X_INT", "0")
        assert _get_int("X_INT", 300, min_value=1) == 300
        assert "must be >= 1" in capsys.readouterr().err

    def test_at_min_accepted(self, monkeypatch):
        monkeypatch.setenv("X_INT", "1")
        assert _get_int("X_INT", 300, min_value=1) == 1


class TestGetStrOrNone:
    def test_returns_value(self, monkeypatch):
        monkeypatch.setenv("X_STR", "hello")
        assert _get_str_or_none("X_STR") == "hello"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("X_STR", "  hello  ")
        assert _get_str_or_none("X_STR") == "hello"

    def test_empty_is_none(self, monkeypatch):
        monkeypatch.setenv("X_STR", "")
        assert _get_str_or_none("X_STR") is None
        monkeypatch.setenv("X_STR", "   ")
        assert _get_str_or_none("X_STR") is None

    def test_missing_is_none(self):
        assert _get_str_or_none("X_STR") is None


class TestGetCsv:
    def test_basic(self, monkeypatch):
        monkeypatch.setenv("X_CSV", "a,b,c")
        assert _get_csv("X_CSV") == ("a", "b", "c")

    def test_lowercases(self, monkeypatch):
        monkeypatch.setenv("X_CSV", "Codex,GEMINI")
        assert _get_csv("X_CSV") == ("codex", "gemini")

    def test_empty_returns_default(self, monkeypatch):
        monkeypatch.setenv("X_CSV", "")
        assert _get_csv("X_CSV", default=("x",)) == ("x",)

    def test_missing_returns_default(self):
        assert _get_csv("X_CSV", default=("y",)) == ("y",)

    def test_strips_whitespace_and_skips_empty(self, monkeypatch):
        monkeypatch.setenv("X_CSV", " a , , b ")
        assert _get_csv("X_CSV") == ("a", "b")


class TestSubstitutionModels:
    def test_unset_returns_none(self):
        assert _load_substitution_models() is None

    def test_three_part_entry(self, monkeypatch):
        monkeypatch.setenv("COUNCIL_SUBSTITUTION_MODELS", "claudeor:codex:gpt-5-codex")
        assert _load_substitution_models() == {"claudeor": ("codex", "gpt-5-codex")}

    def test_two_part_entry(self, monkeypatch):
        monkeypatch.setenv("COUNCIL_SUBSTITUTION_MODELS", "opencode:grok-4")
        assert _load_substitution_models() == {"opencode": (None, "grok-4")}

    def test_multiple_entries(self, monkeypatch):
        monkeypatch.setenv(
            "COUNCIL_SUBSTITUTION_MODELS",
            "claudeor:codex:gpt-5-codex,opencode:grok-4",
        )
        out = _load_substitution_models()
        assert out == {"claudeor": ("codex", "gpt-5-codex"), "opencode": (None, "grok-4")}

    def test_malformed_entry_skipped(self, monkeypatch, capsys):
        monkeypatch.setenv("COUNCIL_SUBSTITUTION_MODELS", "valid:codex:m,broken,opencode:grok")
        out = _load_substitution_models()
        assert out == {"valid": ("codex", "m"), "opencode": (None, "grok")}
        assert "broken" in capsys.readouterr().err

    def test_all_malformed_returns_none(self, monkeypatch):
        monkeypatch.setenv("COUNCIL_SUBSTITUTION_MODELS", "broken,also-broken")
        assert _load_substitution_models() is None
