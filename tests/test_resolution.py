"""Resolution: seat/runner/model triple semantics."""
from __future__ import annotations

from owlex.resolution import Resolution, resolve_seat


class TestNative:
    def test_native_codex(self):
        r = resolve_seat("codex")
        assert r.seat == "codex"
        assert r.runner == "codex"
        assert r.model is None
        assert r.is_substituted is False
        assert r.transcript_source == "codex-rollouts"
        assert r.display_name() == "codex"

    def test_native_codex_with_model(self):
        r = resolve_seat("codex", model="gpt-5.5")
        assert r.is_substituted is False
        assert r.model == "gpt-5.5"
        assert r.transcript_source == "codex-rollouts"
        assert r.display_name() == "codex(gpt-5.5)"

    def test_native_claudeor_uses_claude_projects(self):
        assert resolve_seat("claudeor").transcript_source == "claude-projects"

    def test_native_cursor_has_no_transcript(self):
        # Cursor's protobuf store isn't parsed.
        assert resolve_seat("cursor").transcript_source == "none"

    def test_native_gemini_uses_gemini_tmp(self):
        assert resolve_seat("gemini").transcript_source == "gemini-tmp"


class TestSubstituted:
    def test_claudeor_via_codex(self):
        r = resolve_seat("claudeor", runner="codex", model="gpt-5.5-medium")
        assert r.seat == "claudeor"
        assert r.runner == "codex"
        assert r.model == "gpt-5.5-medium"
        assert r.is_substituted is True
        # Transcript follows the RUNNER, not the seat — this is the bug fix.
        assert r.transcript_source == "codex-rollouts"

    def test_substituted_display_is_honest(self):
        r = resolve_seat("claudeor", runner="codex", model="gpt-5.5-medium")
        # Should not claim "Claude/OpenRouter" anywhere.
        assert "claudeor" in r.display_name()
        assert "codex" in r.display_name()
        assert "gpt-5.5" in r.display_name()
        assert "OpenRouter" not in r.display_name()

    def test_substitution_to_runner_with_no_transcript(self):
        # claudeor->cursor: cursor has no transcript, so this seat has none either.
        r = resolve_seat("claudeor", runner="cursor", model="gpt-5.5")
        assert r.transcript_source == "none"


class TestEquality:
    def test_frozen(self):
        r = resolve_seat("codex")
        try:
            r.seat = "gemini"  # type: ignore[misc]
        except Exception:
            pass
        else:
            assert False, "Resolution should be frozen"

    def test_value_equality(self):
        a = resolve_seat("claudeor", runner="codex", model="gpt-5.5")
        b = resolve_seat("claudeor", runner="codex", model="gpt-5.5")
        assert a == b
