"""Refactor #3: shared anonymization helper used by prompts.py and agreement.py."""
from __future__ import annotations

from owlex.anonymize import LABELS, assign_labels, label_for


class TestLabelFor:
    def test_first_few_letters(self):
        assert label_for(0) == "A"
        assert label_for(1) == "B"
        assert label_for(15) == "P"

    def test_overflow_falls_back_to_number(self):
        assert label_for(len(LABELS)) == str(len(LABELS) + 1)
        assert label_for(99) == "100"


class TestAssignLabels:
    def test_preserves_order_when_no_salt(self):
        pairs = [("codex", "x"), ("gemini", "y"), ("claudeor", "z")]
        by_label, label_to_key = assign_labels(pairs)
        assert by_label == {"A": "x", "B": "y", "C": "z"}
        assert label_to_key == {"A": "codex", "B": "gemini", "C": "claudeor"}

    def test_shuffles_with_salt(self):
        pairs = [(f"a{i}", i) for i in range(8)]
        # Same salt → identical mapping across calls
        m1 = assign_labels(pairs, salt="x")[1]
        m2 = assign_labels(pairs, salt="x")[1]
        assert m1 == m2

    def test_different_salts_likely_different(self):
        pairs = [(f"a{i}", i) for i in range(8)]
        seen = {tuple(sorted(assign_labels(pairs, salt=f"s{i}")[1].items())) for i in range(20)}
        assert len(seen) > 1, "salts produced no variation across 20 trials"

    def test_empty_input(self):
        by_label, label_to_key = assign_labels([])
        assert by_label == {}
        assert label_to_key == {}

    def test_value_preservation(self):
        # Payloads are passed through untouched
        obj = {"complex": "value"}
        pairs = [("codex", obj)]
        by_label, _ = assign_labels(pairs)
        assert by_label["A"] is obj


class TestAnonymizerIntegration:
    """Validates that prompts.py and agreement.py both use the shared helper."""

    def test_prompts_imports_helper(self):
        import inspect

        from owlex import prompts

        src = inspect.getsource(prompts.build_deliberation_prompt)
        assert "assign_labels" in src

    def test_agreement_imports_helper(self):
        import inspect

        from owlex import agreement

        src = inspect.getsource(agreement.score_agreement)
        assert "assign_labels" in src

    def test_anonymize_round_responses_uses_helper(self):
        import inspect

        from owlex import prompts

        src = inspect.getsource(prompts.anonymize_round_responses)
        assert "assign_labels" in src
