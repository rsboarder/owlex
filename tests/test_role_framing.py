"""TASK-15: optional role framing on single-model entrypoints.

Covers the shared resolver helper plus the two tool surfaces (second_opinion and
the start_*_session tools). Assertions are behavior/structure, never exact prompt
wording (repo testing principle).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from owlex.prompts import COUNCIL_SYSTEM_INSTRUCTION
from owlex.roles import BUILTIN_ROLES, resolve_generate_role_prefix

EDGE = "edge_case_adversary"

# All six start_*_session tool names (used by parametrized tests).
_ALL_START_TOOLS = [
    "start_codex_session",
    "start_gemini_session",
    "start_opencode_session",
    "start_claudeor_session",
    "start_aichat_session",
    "start_cursor_session",
]


class TestResolveGenerateRolePrefix:
    def test_none_returns_empty(self):
        assert resolve_generate_role_prefix(None) == ""

    def test_empty_string_returns_empty(self):
        assert resolve_generate_role_prefix("") == ""

    def test_known_role_returns_round1_prefix(self):
        prefix = resolve_generate_role_prefix(EDGE)
        assert prefix == BUILTIN_ROLES[EDGE].round_1_prefix
        assert prefix.strip()

    def test_generate_framing_omits_read_only_advisor_instruction(self):
        # TASK-15 decision: generate calls do NOT carry COUNCIL_SYSTEM_INSTRUCTION.
        assert COUNCIL_SYSTEM_INSTRUCTION not in resolve_generate_role_prefix(EDGE)

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError):
            resolve_generate_role_prefix("not-a-real-role")


class TestSecondOpinionRole:
    @pytest.mark.asyncio
    async def test_role_omitted_prompt_unchanged(self, monkeypatch):
        import owlex.server._second_opinion as mod

        captured = {}

        async def fake_gso(prompt, wd, timeout):
            captured["prompt"] = prompt
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question")
        assert result["success"] is True
        assert captured["prompt"] == "my question"

    @pytest.mark.asyncio
    async def test_role_set_prepends_prefix(self, monkeypatch):
        import owlex.server._second_opinion as mod

        captured = {}

        async def fake_gso(prompt, wd, timeout):
            captured["prompt"] = prompt
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        await mod.second_opinion(prompt="my question", role=EDGE)
        assert captured["prompt"].startswith(BUILTIN_ROLES[EDGE].round_1_prefix)
        assert "my question" in captured["prompt"]
        assert COUNCIL_SYSTEM_INSTRUCTION not in captured["prompt"]

    @pytest.mark.asyncio
    async def test_unknown_role_errors_without_calling_model(self, monkeypatch):
        import owlex.server._second_opinion as mod

        called = {"hit": False}

        async def fake_gso(*a, **k):
            called["hit"] = True
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="not-a-real-role")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert called["hit"] is False


class _FakeTask:
    task_id = "t1"
    status = "running"
    async_task = None


class _FakeSubConfig:
    """Minimal stand-in for any sub-config that needs a model attribute."""
    model = None


class _FakeClaudeORConfig(_FakeSubConfig):
    """Stand-in for ClaudeORConfig with api_key set (bypasses claudeor's API-key gate)."""
    api_key = "test-key"


class _FakeConfig:
    """Stand-in for OwlexConfig that satisfies every tool's post-role-check config reads."""
    claudeor = _FakeClaudeORConfig()
    aichat = _FakeSubConfig()
    cursor = _FakeSubConfig()


class TestStartSessionRole:
    """start_codex_session is the representative start_* surface (all six share
    the same _frame_with_role path)."""

    def _patch_engine(self, monkeypatch):
        import owlex.server._sessions as sess

        captured = {}

        def fake_create_task(*, command, args, context):
            captured["args"] = args
            return _FakeTask()

        async def fake_run_agent(task, runner, **kw):
            captured["run_prompt"] = kw.get("prompt")
            return None

        monkeypatch.setattr(sess.engine, "create_task", fake_create_task)
        monkeypatch.setattr(sess.engine, "run_agent", fake_run_agent)
        return sess, captured

    def _patch_engine_all(self, monkeypatch):
        """Like _patch_engine but also bypasses the claudeor API-key gate."""
        sess, captured = self._patch_engine(monkeypatch)
        monkeypatch.setattr(sess, "config", _FakeConfig())
        return sess, captured

    @pytest.mark.asyncio
    async def test_role_omitted_prompt_unchanged(self, monkeypatch):
        sess, captured = self._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role=None
        )
        await asyncio.sleep(0)  # let the scheduled run_agent coroutine execute
        assert result["success"] is True
        assert captured["args"]["prompt"] == "my question"

    @pytest.mark.asyncio
    async def test_role_set_prepends_prefix(self, monkeypatch):
        sess, captured = self._patch_engine(monkeypatch)
        await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role=EDGE
        )
        await asyncio.sleep(0)
        framed = captured["args"]["prompt"]
        assert framed.startswith(BUILTIN_ROLES[EDGE].round_1_prefix)
        assert "my question" in framed
        assert framed == captured["run_prompt"]

    @pytest.mark.asyncio
    async def test_unknown_role_errors_before_dispatch(self, monkeypatch):
        sess, captured = self._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role="not-a-real-role"
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "args" not in captured  # never reached engine.create_task


# ---------------------------------------------------------------------------
# A. Backward-compat / byte-identity
# ---------------------------------------------------------------------------

class TestRoleEmptyStringBackwardCompat:
    """A1 — role="" is treated as no-role: success, prompt unchanged."""

    @pytest.mark.asyncio
    async def test_second_opinion_empty_role_prompt_unchanged(self, monkeypatch):
        import owlex.server._second_opinion as mod

        captured = {}

        async def fake_gso(prompt, wd, timeout):
            captured["prompt"] = prompt
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="")
        assert result["success"] is True
        assert captured["prompt"] == "my question"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["start_codex_session", "start_gemini_session"])
    async def test_start_session_empty_role_prompt_unchanged(self, tool_name, monkeypatch):
        """Representative start_* tools: role="" behaves identically to role=None."""
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        tool = getattr(sess, tool_name)
        result = await tool(MagicMock(), prompt="my question", working_directory=None, role="")
        await asyncio.sleep(0)
        assert result["success"] is True
        assert captured["args"]["prompt"] == "my question"


class TestRoleNeutralByteIdentity:
    """A2 — role="neutral" (round_1_prefix is "") → success, prompt byte-identical, no separator."""

    @pytest.mark.asyncio
    async def test_second_opinion_neutral_role_no_separator(self, monkeypatch):
        import owlex.server._second_opinion as mod

        assert BUILTIN_ROLES["neutral"].round_1_prefix == "", "precondition: neutral has empty prefix"

        captured = {}

        async def fake_gso(prompt, wd, timeout):
            captured["prompt"] = prompt
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="neutral")
        assert result["success"] is True
        assert captured["prompt"] == "my question"
        assert "\n\n" not in captured["prompt"]

    @pytest.mark.asyncio
    async def test_start_session_neutral_role_no_separator(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role="neutral"
        )
        await asyncio.sleep(0)
        assert result["success"] is True
        # neutral prefix is empty so no "\n\n" is added at the front
        assert not captured["args"]["prompt"].startswith("\n\n")


class TestRoleNoneByteIdentityWithWhitespace:
    """A3 — role=None with a whitespace-bearing prompt: engine receives the original raw string."""

    @pytest.mark.asyncio
    async def test_start_session_role_none_preserves_whitespace(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        raw_prompt = "  hello \n"
        result = await sess.start_codex_session(
            MagicMock(), prompt=raw_prompt, working_directory=None, role=None
        )
        await asyncio.sleep(0)
        assert result["success"] is True
        # The engine must receive the original raw string, whitespace included.
        assert captured["args"]["prompt"] == raw_prompt

    @pytest.mark.asyncio
    async def test_second_opinion_role_none_preserves_whitespace(self, monkeypatch):
        import owlex.server._second_opinion as mod

        captured = {}

        async def fake_gso(prompt, wd, timeout):
            captured["prompt"] = prompt
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        raw_prompt = "  hello \n"
        result = await mod.second_opinion(prompt=raw_prompt)
        assert result["success"] is True
        # second_opinion must also forward the original prompt verbatim (role=None → no prefix).
        assert captured["prompt"] == raw_prompt


# ---------------------------------------------------------------------------
# B. Invalid role (extended coverage across all start_* tools)
# ---------------------------------------------------------------------------

class TestInvalidRoleAllStartTools:
    """B4 — unknown role on each of the six start_* tools → INVALID_ARGS, engine not invoked."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", _ALL_START_TOOLS)
    async def test_unknown_role_invalid_args(self, tool_name, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine_all(monkeypatch)
        tool = getattr(sess, tool_name)
        result = await tool(MagicMock(), prompt="my question", working_directory=None, role="not-a-real-role")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "args" not in captured


class TestClaudeORRoleBeforeMissingKey:
    """B4b — start_claudeor_session: unknown role → INVALID_ARGS even without api_key set."""

    @pytest.mark.asyncio
    async def test_unknown_role_wins_over_missing_api_key(self, monkeypatch):
        # Use plain _patch_engine (no _FakeConfig), so config.claudeor.api_key is absent/falsy.
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        result = await sess.start_claudeor_session(
            MagicMock(), prompt="my question", working_directory=None, role="not-a-real-role"
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        # Engine was never reached — role error exits before api_key check.
        assert "args" not in captured


class TestTeamNameAsRoleIsInvalid:
    """B5 — a team name is not a valid role id → INVALID_ARGS."""

    @pytest.mark.asyncio
    async def test_second_opinion_team_name_is_invalid(self, monkeypatch):
        import owlex.server._second_opinion as mod

        called = {"hit": False}

        async def fake_gso(*a, **k):
            called["hit"] = True
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="test_spec")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert called["hit"] is False

    @pytest.mark.asyncio
    async def test_start_session_team_name_is_invalid(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role="test_spec"
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "args" not in captured


class TestWhitespaceOnlyRoleIsInvalid:
    """B6 — whitespace-only role id → INVALID_ARGS (not treated as no-role)."""

    @pytest.mark.asyncio
    async def test_second_opinion_whitespace_role_is_invalid(self, monkeypatch):
        import owlex.server._second_opinion as mod

        called = {"hit": False}

        async def fake_gso(*a, **k):
            called["hit"] = True
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="   ")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert called["hit"] is False

    @pytest.mark.asyncio
    async def test_start_session_whitespace_role_is_invalid(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role="   "
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "args" not in captured


class TestSurroundingWhitespaceRoleIsInvalid:
    """B7 — role id with surrounding whitespace is not trimmed → INVALID_ARGS."""

    @pytest.mark.asyncio
    async def test_second_opinion_trailing_space_role_invalid(self, monkeypatch):
        import owlex.server._second_opinion as mod

        called = {"hit": False}

        async def fake_gso(*a, **k):
            called["hit"] = True
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="edge_case_adversary ")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert called["hit"] is False

    @pytest.mark.asyncio
    async def test_start_session_trailing_space_role_invalid(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role="edge_case_adversary "
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "args" not in captured


class TestCaseMismatchRoleIsInvalid:
    """B8 — role ids are case-sensitive; mismatched case → INVALID_ARGS."""

    @pytest.mark.asyncio
    async def test_second_opinion_case_mismatch_role_invalid(self, monkeypatch):
        import owlex.server._second_opinion as mod

        called = {"hit": False}

        async def fake_gso(*a, **k):
            called["hit"] = True
            return True, "ok", False

        monkeypatch.setattr(mod, "get_second_opinion", fake_gso)
        result = await mod.second_opinion(prompt="my question", role="Edge_Case_Adversary")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert called["hit"] is False

    @pytest.mark.asyncio
    async def test_start_session_case_mismatch_role_invalid(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        result = await sess.start_codex_session(
            MagicMock(), prompt="my question", working_directory=None, role="Edge_Case_Adversary"
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGS"
        assert "args" not in captured


# ---------------------------------------------------------------------------
# C. Happy-path parity
# ---------------------------------------------------------------------------

class TestKnownRoleAllStartTools:
    """C9 — known role across all six start_* tools: prefix prepended once, no COUNCIL_SYSTEM_INSTRUCTION."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", _ALL_START_TOOLS)
    async def test_edge_case_adversary_role_framed_correctly(self, tool_name, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine_all(monkeypatch)
        tool = getattr(sess, tool_name)
        result = await tool(MagicMock(), prompt="my question", working_directory=None, role=EDGE)
        await asyncio.sleep(0)
        assert result["success"] is True
        framed = captured["args"]["prompt"]
        expected_prefix = BUILTIN_ROLES[EDGE].round_1_prefix
        assert framed.startswith(expected_prefix)
        assert "my question" in framed
        assert COUNCIL_SYSTEM_INSTRUCTION not in framed


class TestRoleNotAccumulated:
    """C10 — applying the same role twice in sequence prepends exactly one prefix per call."""

    @pytest.mark.asyncio
    async def test_two_calls_each_prepend_once(self, monkeypatch):
        sess, captured = TestStartSessionRole()._patch_engine(monkeypatch)
        expected_prefix = BUILTIN_ROLES[EDGE].round_1_prefix

        # First call
        await sess.start_codex_session(
            MagicMock(), prompt="question one", working_directory=None, role=EDGE
        )
        await asyncio.sleep(0)
        first_prompt = captured["args"]["prompt"]
        assert first_prompt.startswith(expected_prefix)
        # Prefix appears exactly once
        assert first_prompt.count(expected_prefix) == 1

        # Second call — fresh invocation, fresh captured state
        captured.clear()
        await sess.start_codex_session(
            MagicMock(), prompt="question two", working_directory=None, role=EDGE
        )
        await asyncio.sleep(0)
        second_prompt = captured["args"]["prompt"]
        assert second_prompt.startswith(expected_prefix)
        assert second_prompt.count(expected_prefix) == 1


# ---------------------------------------------------------------------------
# D. Config robustness
# ---------------------------------------------------------------------------

class TestConfigRobustness:
    """D — role=None / builtin resolution must not depend on user's ~/.owlex/roles.json."""

    def test_none_does_not_invoke_resolver(self, monkeypatch):
        """D11 — role=None short-circuits before any config I/O."""
        import owlex.roles as roles_mod

        def _boom():
            raise RuntimeError("create_default_resolver must not be called for role=None")

        monkeypatch.setattr(roles_mod, "create_default_resolver", _boom)
        # Must return "" without calling the resolver at all
        result = resolve_generate_role_prefix(None)
        assert result == ""

    def test_builtin_role_resolves_without_user_config(self, monkeypatch):
        """D12 — builtin roles work even if ~/.owlex/roles.json is absent/ignored."""
        import owlex.roles as roles_mod

        monkeypatch.setattr(roles_mod, "load_user_roles", lambda: ({}, {}))
        result = resolve_generate_role_prefix(EDGE)
        assert result  # non-empty prefix
        assert COUNCIL_SYSTEM_INSTRUCTION not in result
