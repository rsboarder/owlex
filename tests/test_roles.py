"""Tests for TeamPreset.runner_overrides and RoleResolver.runner_overrides_for."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest

from owlex.config import KNOWN_RUNNERS
from owlex.models import COUNCIL_SEATS
from owlex.roles import BUILTIN_TEAMS, RoleResolver, TeamPreset, load_user_roles


class TestTeamPresetRunnerOverrides:
    def test_builtin_overrides_are_valid(self):
        for team in BUILTIN_TEAMS.values():
            assert set(team.runner_overrides) <= set(COUNCIL_SEATS)
            assert set(team.runner_overrides.values()) <= KNOWN_RUNNERS
            assert all(seat != runner for seat, runner in team.runner_overrides.items())

    def test_test_spec_carries_claudeor_grok_override(self):
        assert BUILTIN_TEAMS["test_spec"].runner_overrides == {"claudeor": "grok"}

    def test_every_other_builtin_team_carries_no_overrides(self):
        for team_id, team in BUILTIN_TEAMS.items():
            if team_id == "test_spec":
                continue
            assert team.runner_overrides == {}, team_id

    def test_to_dict_round_trips_runner_overrides(self):
        team = BUILTIN_TEAMS["test_spec"]
        data = team.to_dict()
        assert data["runner_overrides"] == {"claudeor": "grok"}
        rebuilt = TeamPreset.from_dict(data)
        assert rebuilt.runner_overrides == {"claudeor": "grok"}

    def test_from_dict_defaults_to_empty_when_absent(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        del data["runner_overrides"]
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {}

    def test_from_dict_skips_unknown_runner_name(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {"claudeor": "not-a-real-runner"}
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {}

    def test_from_dict_keeps_known_runner_and_drops_unknown(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {"claudeor": "grok", "aichat": "bogus"}
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {"claudeor": "grok"}

    def test_from_dict_normalizes_entries_and_skips_unknown_seats(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {
            " ClaudeOR ": " Grok ",
            "claudeorr": "grok",
        }
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {"claudeor": "grok"}

    def test_from_dict_skips_non_string_runner_without_dropping_team(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {
            "claudeor": ["grok"],
            "aichat": " grok ",
        }
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {"aichat": "grok"}

    def test_from_dict_skips_non_string_seat_without_dropping_team(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {1: "grok", "aichat": "grok"}
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {"aichat": "grok"}

    def test_from_dict_ignores_non_dict_overrides_without_dropping_team(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = ["claudeor:grok"]
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {}

    @pytest.mark.parametrize("raw_overrides", [[], "", None])
    def test_from_dict_warns_for_falsy_non_dict_overrides(self, raw_overrides, capsys):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = raw_overrides
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {}
        assert capsys.readouterr().err

    def test_from_dict_last_normalized_collision_wins(self, capsys):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {"ClaudeOR": "grok", "claudeor": "codex"}
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {"claudeor": "codex"}
        assert capsys.readouterr().err

    def test_from_dict_skips_self_override(self):
        data = BUILTIN_TEAMS["code_review"].to_dict()
        data["runner_overrides"] = {"codex": "codex", "aichat": "grok"}
        team = TeamPreset.from_dict(data)
        assert team.runner_overrides == {"aichat": "grok"}

    def test_frozen_dataclass_still_frozen(self):
        team = BUILTIN_TEAMS["test_spec"]
        with pytest.raises(FrozenInstanceError):
            team.runner_overrides = {"codex": "grok"}  # type: ignore[misc]

    def test_load_user_roles_honors_owlex_home(self, tmp_path, monkeypatch):
        team_data = BUILTIN_TEAMS["code_review"].to_dict()
        team_data["id"] = "from_env_home"
        home = tmp_path / "owlex_home"
        home.mkdir()
        (home / "roles.json").write_text(json.dumps({
            "roles": {},
            "teams": {"from_env_home": team_data},
        }))
        monkeypatch.setenv("OWLEX_HOME", str(home))

        _, teams = load_user_roles()

        assert "from_env_home" in teams


class TestRunnerOverridesFor:
    @staticmethod
    def _resolver() -> RoleResolver:
        return RoleResolver(roles={}, teams=dict(BUILTIN_TEAMS))

    def test_returns_overrides_for_team_that_has_them(self):
        assert self._resolver().runner_overrides_for("test_spec") == {"claudeor": "grok"}

    def test_returns_empty_for_team_without_overrides(self):
        assert self._resolver().runner_overrides_for("code_review") == {}

    def test_returns_empty_for_none_spec(self):
        assert self._resolver().runner_overrides_for(None) == {}

    def test_returns_empty_for_dict_spec(self):
        assert self._resolver().runner_overrides_for({"codex": "security"}) == {}

    def test_returns_empty_for_list_spec(self):
        assert self._resolver().runner_overrides_for(["security"]) == {}

    def test_returns_empty_for_unknown_team_name(self):
        assert self._resolver().runner_overrides_for("does_not_exist") == {}

    def test_result_is_a_copy_not_the_teams_own_dict(self):
        overrides = self._resolver().runner_overrides_for("test_spec")
        overrides["codex"] = "grok"
        assert BUILTIN_TEAMS["test_spec"].runner_overrides == {"claudeor": "grok"}
