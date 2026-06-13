"""Anti-sycophancy preamble is injected at the single R2 deliberation choke-point."""
from __future__ import annotations

from owlex.prompts import (
    ANTISYCOPHANCY_PREAMBLE,
    build_deliberation_prompt,
    build_deliberation_prompt_with_role,
)
from owlex.roles import BUILTIN_ROLES, RoleId, create_default_resolver

MARKER = "DELIBERATION INTEGRITY"


def test_preamble_present_in_revise_mode():
    prompt = build_deliberation_prompt(
        original_prompt="q", codex_answer="a", gemini_answer="b", critique=False
    )
    assert MARKER in prompt


def test_preamble_present_in_critique_mode():
    prompt = build_deliberation_prompt(
        original_prompt="q", codex_answer="a", gemini_answer="b", critique=True
    )
    assert MARKER in prompt


def test_preamble_present_in_role_injected_r2():
    skeptic = BUILTIN_ROLES[RoleId.SKEPTIC.value]
    prompt = build_deliberation_prompt_with_role(
        original_prompt="q", role=skeptic, codex_answer="a", gemini_answer="b"
    )
    assert MARKER in prompt
    # role prefix is still prepended ahead of the base prompt
    assert prompt.index("[ROLE: Skeptic") < prompt.index(MARKER)


def test_preamble_constant_nonempty():
    assert ANTISYCOPHANCY_PREAMBLE.strip()


class TestSynthesizerRole:
    def test_synthesizer_round_2_prefix_nonempty(self):
        assert BUILTIN_ROLES["synthesizer"].round_2_prefix.strip()

    def test_synthesizer_role_id_matches_key(self):
        role = BUILTIN_ROLES[RoleId.SYNTHESIZER.value]
        assert role.id == RoleId.SYNTHESIZER.value


class TestDialecticTeam:
    def test_dialectic_team_has_two_skeptics_and_one_synthesizer(self):
        resolver = create_default_resolver()
        agents = ("codex", "gemini", "opencode", "claudeor", "aichat", "cursor")
        mapping = resolver.resolve("dialectic", agents)

        assert len(mapping) == 6

        role_ids = [role.id for role in mapping.values()]
        assert role_ids.count("skeptic") == 2
        assert role_ids.count("synthesizer") == 1

    def test_dialectic_team_six_keys(self):
        resolver = create_default_resolver()
        agents = ("codex", "gemini", "opencode", "claudeor", "aichat", "cursor")
        mapping = resolver.resolve("dialectic", agents)
        assert set(mapping.keys()) == set(agents)


class TestEdgeCaseAdversaryRole:
    def test_role_exists_with_nonempty_prefixes(self):
        role = BUILTIN_ROLES[RoleId.EDGE_CASE_ADVERSARY.value]
        assert role.round_1_prefix.strip()
        assert role.round_2_prefix.strip()

    def test_role_id_matches_key(self):
        role = BUILTIN_ROLES["edge_case_adversary"]
        assert role.id == "edge_case_adversary"


class TestTestSpecTeam:
    """test_spec assigns edge_case_adversary to every seat (AC #2/#3)."""

    def test_resolves_edge_case_adversary_for_default_seats(self):
        resolver = create_default_resolver()
        mapping = resolver.resolve("test_spec")  # active_agents=None → DEFAULT_AGENT_ORDER
        assert mapping  # every default seat resolved
        for role in mapping.values():
            assert role.id == "edge_case_adversary"

    def test_resolves_edge_case_adversary_for_full_council(self):
        resolver = create_default_resolver()
        agents = ("codex", "gemini", "opencode", "claudeor", "aichat", "cursor")
        mapping = resolver.resolve("test_spec", agents)
        assert set(mapping.keys()) == set(agents)
        assert all(role.id == "edge_case_adversary" for role in mapping.values())
