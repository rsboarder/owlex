"""
Tests for the GLM-5.2 seat integration in the OpenCode runner.

Activation: COUNCIL_SUBSTITUTION_MODELS=<seat>:opencode:zai/glm-5.2
Docs: docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md
"""

import json
import os
from unittest.mock import patch

import pytest

from owlex.agents.opencode import OpenCodeRunner, _is_glm_model, _read_glm_token, _build_glm_env_overrides


# --- _is_glm_model -----------------------------------------------------------

class TestIsGlmModel:
    def test_zai_prefix_detected(self):
        assert _is_glm_model("zai/glm-5.2") is True

    def test_glm_in_model_name(self):
        assert _is_glm_model("some-provider/glm-4") is True

    def test_zai_without_slash(self):
        assert _is_glm_model("zai-something") is True

    def test_non_glm_model_unaffected(self):
        assert _is_glm_model("anthropic/claude-sonnet-4") is False

    def test_opencode_default_model_unaffected(self):
        assert _is_glm_model("openai/gpt-4o") is False

    def test_none_returns_false(self):
        assert _is_glm_model(None) is False

    def test_empty_string_returns_false(self):
        assert _is_glm_model("") is False

    def test_case_insensitive(self):
        assert _is_glm_model("ZAI/GLM-5.2") is True


# --- _read_glm_token ---------------------------------------------------------

class TestReadGlmToken:
    def test_returns_token_when_file_exists(self, tmp_path, monkeypatch):
        token_file = tmp_path / ".owlex" / "glm_token"
        token_file.parent.mkdir(parents=True)
        token_file.write_text("my-secret-token\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        # Patch Path.home() to return our tmp_path
        with patch("owlex.agents.opencode.Path") as mock_path_cls:
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.return_value = tmp_path / ".owlex" / "glm_token"
            # Use real Path to construct correctly
            from pathlib import Path as RealPath
            with patch("owlex.agents.opencode.Path", RealPath):
                original_home = RealPath.home
                with patch.object(RealPath, "home", staticmethod(lambda: tmp_path)):
                    token = _read_glm_token()
        assert token == "my-secret-token"

    def test_returns_none_when_file_missing(self, tmp_path):
        from pathlib import Path as RealPath
        with patch.object(RealPath, "home", staticmethod(lambda: tmp_path)):
            token = _read_glm_token()
        assert token is None


# --- _build_glm_env_overrides ------------------------------------------------

class TestBuildGlmEnvOverrides:
    def test_returns_xdg_data_home(self):
        with patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            overrides = _build_glm_env_overrides("high")
        assert "XDG_DATA_HOME" in overrides
        assert os.path.isdir(overrides["XDG_DATA_HOME"])

    def test_returns_opencode_config(self):
        with patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            overrides = _build_glm_env_overrides("high")
        assert "OPENCODE_CONFIG" in overrides
        cfg_path = overrides["OPENCODE_CONFIG"]
        assert os.path.isfile(cfg_path)
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert "zai" in cfg["provider"]
        assert "glm-5.2" in cfg["provider"]["zai"]["models"]

    def test_sets_glm_token_when_available(self):
        with patch("owlex.agents.opencode._read_glm_token", return_value="secret-tok"):
            overrides = _build_glm_env_overrides("high")
        assert overrides.get("GLM_TOKEN") == "secret-tok"

    def test_omits_glm_token_when_missing(self):
        with patch("owlex.agents.opencode._read_glm_token", return_value=None):
            overrides = _build_glm_env_overrides("high")
        assert "GLM_TOKEN" not in overrides

    def test_fresh_xdg_dir_per_call(self):
        with patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            o1 = _build_glm_env_overrides("high")
            o2 = _build_glm_env_overrides("high")
        assert o1["XDG_DATA_HOME"] != o2["XDG_DATA_HOME"]

    def test_variant_stored_in_config(self):
        with patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            overrides = _build_glm_env_overrides("max")
        with open(overrides["OPENCODE_CONFIG"]) as f:
            cfg = json.load(f)
        model_opts = cfg["provider"]["zai"]["models"]["glm-5.2"]["options"]
        assert model_opts["reasoning_effort"] == "max"


# --- OpenCodeRunner.build_exec_command with GLM model ------------------------

class TestOpenCodeRunnerGlmExec:
    @pytest.fixture
    def runner(self):
        return OpenCodeRunner()

    def test_glm_model_adds_variant_flag(self, runner, monkeypatch):
        monkeypatch.setenv("OWLEX_GLM_OC_VARIANT", "high")
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            mock_cfg.opencode.model = None
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        assert "--variant" in cmd.command
        idx = cmd.command.index("--variant")
        assert cmd.command[idx + 1] == "high"

    def test_glm_model_flag_set(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            mock_cfg.opencode.model = None
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        assert "--model" in cmd.command
        idx = cmd.command.index("--model")
        assert cmd.command[idx + 1] == "zai/glm-5.2"

    def test_glm_no_format_json(self, runner):
        """GLM path must NOT add --format json (hangs on Z.ai provider)."""
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            mock_cfg.opencode.model = None
            mock_cfg.opencode.json_output = True  # even if global json_output is on
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        assert "--format" not in cmd.command

    def test_glm_env_overrides_present(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="my-token"):
            mock_cfg.opencode.model = None
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        assert cmd.env_overrides is not None
        assert "XDG_DATA_HOME" in cmd.env_overrides
        assert "OPENCODE_CONFIG" in cmd.env_overrides
        assert cmd.env_overrides.get("GLM_TOKEN") == "my-token"

    def test_glm_model_on_agentcommand(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            mock_cfg.opencode.model = None
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        assert cmd.model == "zai/glm-5.2"

    def test_glm_uses_env_variant(self, runner, monkeypatch):
        monkeypatch.setenv("OWLEX_GLM_OC_VARIANT", "max")
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            mock_cfg.opencode.model = None
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        idx = cmd.command.index("--variant")
        assert cmd.command[idx + 1] == "max"

    def test_glm_default_variant_is_high(self, runner, monkeypatch):
        monkeypatch.delenv("OWLEX_GLM_OC_VARIANT", raising=False)
        with patch("owlex.agents.opencode.config") as mock_cfg, \
             patch("owlex.agents.opencode._read_glm_token", return_value="tok"):
            mock_cfg.opencode.model = None
            cmd = runner.build_exec_command(prompt="Q", model_override="zai/glm-5.2")
        idx = cmd.command.index("--variant")
        assert cmd.command[idx + 1] == "high"


# --- Non-GLM models are unaffected -------------------------------------------

class TestOpenCodeRunnerNonGlmUnaffected:
    @pytest.fixture
    def runner(self):
        return OpenCodeRunner()

    def test_normal_model_no_variant(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg:
            mock_cfg.opencode.model = "anthropic/claude-sonnet-4"
            mock_cfg.opencode.agent = None
            mock_cfg.opencode.json_output = False
            cmd = runner.build_exec_command(prompt="Q")
        assert "--variant" not in cmd.command

    def test_normal_model_no_glm_env_overrides(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg:
            mock_cfg.opencode.model = "anthropic/claude-sonnet-4"
            mock_cfg.opencode.agent = None
            mock_cfg.opencode.json_output = False
            cmd = runner.build_exec_command(prompt="Q")
        # env_overrides should be None (no GLM injection)
        assert cmd.env_overrides is None

    def test_normal_model_respects_json_output(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg:
            mock_cfg.opencode.model = None
            mock_cfg.opencode.agent = None
            mock_cfg.opencode.json_output = True
            cmd = runner.build_exec_command(prompt="Q")
        assert "--format" in cmd.command
        idx = cmd.command.index("--format")
        assert cmd.command[idx + 1] == "json"

    def test_no_model_override_uses_config(self, runner):
        with patch("owlex.agents.opencode.config") as mock_cfg:
            mock_cfg.opencode.model = "anthropic/claude-opus-4"
            mock_cfg.opencode.agent = None
            mock_cfg.opencode.json_output = False
            cmd = runner.build_exec_command(prompt="Q")
        assert cmd.model == "anthropic/claude-opus-4"
        assert "--variant" not in cmd.command
