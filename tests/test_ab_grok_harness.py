"""Unit tests for A/B Grok harness pure helpers (no CLI / no DB)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "ab_grok_harness.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("ab_grok_harness", SCRIPTS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ab_grok_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ab():
    return _load_mod()


def test_parse_grok_text_json_envelope(ab):
    raw = '{"text": "Hello from grok", "stopReason": "EndTurn"}'
    assert ab.parse_grok_text(raw) == "Hello from grok"


def test_parse_grok_text_with_prefix(ab):
    raw = 'Grok Output:\n\n{"text": "Body", "x": 1}'
    assert ab.parse_grok_text(raw) == "Body"


def test_strip_tool_narration_drops_prefix(ab):
    text = (
        "I'll read the auth module and the routes next.\n"
        "Pulling the guard implementation.\n"
        "# Council review: authz fix\n\n"
        "**Verdict:** Ship it.\n"
    )
    cleaned = ab.strip_tool_narration(text)
    assert cleaned.startswith("# Council review")
    assert "I'll read" not in cleaned
    assert "Verdict" in cleaned


def test_strip_preserves_content_without_tool_prefix(ab):
    text = "# Review\n\n**Verdict:** ok\n\nEvidence in foo.py:12\n"
    assert ab.strip_tool_narration(text) == text.strip()


def test_structural_metrics_contract(ab):
    text = (
        "**Verdict:** Correct fix.\n\n"
        "## Evidence\n`owlex/council.py:10`\n\n"
        "```python\nprint(1)\n```\n\n"
        "## Residual risks\n- race on fallback\n\n"
        "## Recommendation\nship\n"
    )
    m = ab.structural_metrics(text)
    assert m["has_verdict"] is True
    assert m["has_risks"] is True
    assert m["has_recommendation"] is True
    assert m["code_blocks"] == 1
    assert m["file_refs"] >= 1
    assert m["toolish_start"] is False
    assert m["contract_ok"] is True
    assert m["preamble_chars"] < 50


def test_structural_metrics_toolish(ab):
    text = "I'll inspect the tree then report.\n\nMore prose without heading."
    m = ab.structural_metrics(text)
    assert m["toolish_start"] is True
    assert m["contract_ok"] is False


def test_build_prompt_contract_suffix(ab):
    base = "Review this diff."
    arm = ab.ARMS["contract"]
    out = ab.build_prompt(base, arm)
    assert out.startswith(base)
    assert "Output contract" in out
    assert ab.build_prompt(base, ab.ARMS["baseline"]) == base


def test_apply_arm_strip(ab):
    raw = '{"text": "I\\u0027ll read foo.\\n# Title\\n**Verdict:** ok\\n"}'
    # actual content with I'll
    raw = '{"text": "I\'ll read foo.\\n# Title\\n**Verdict:** ok\\n"}'
    text = ab.apply_arm_to_text(raw, ab.ARMS["strip"])
    assert text.startswith("# Title")


def test_evaluate_gates_clean_win(ab):
    """Synthetic rows: B cleaner + faster + quality win → ship."""
    rows = []
    for i in range(6):
        rows.append(
            {
                "council_id": f"c{i}",
                "a": {
                    "elapsed_s": 200,
                    "metrics": {
                        "preamble_chars": 300,
                        "toolish_start": True,
                        "length": 10000,
                        "has_verdict": True,
                        "contract_ok": False,
                    },
                },
                "b": {
                    "elapsed_s": 150,
                    "metrics": {
                        "preamble_chars": 20,
                        "toolish_start": False,
                        "length": 9000,
                        "has_verdict": True,
                        "contract_ok": True,
                    },
                },
                "rating": {
                    "by_arm": {
                        "baseline": {
                            "score": 1,
                            "helpfulness": 4,
                            "groundedness": 4,
                            "correctness": 4,
                        },
                        "contract_strip": {
                            "score": 1,
                            "helpfulness": 5,
                            "groundedness": 5,
                            "correctness": 5,
                        },
                    }
                },
            }
        )
    gate = ab.evaluate_gates("baseline", "contract_strip", rows, rated=True)
    assert gate["ship"] is True
    assert gate["gates"]["G1_reliability"] is True
    assert gate["gates"]["G4_cleanliness"] is True


def test_evaluate_gates_quality_regression_blocks_ship(ab):
    rows = [
        {
            "council_id": "x",
            "a": {
                "elapsed_s": 100,
                "metrics": {
                    "preamble_chars": 10,
                    "toolish_start": False,
                    "length": 5000,
                },
            },
            "b": {
                "elapsed_s": 90,
                "metrics": {
                    "preamble_chars": 5,
                    "toolish_start": False,
                    "length": 5000,
                },
            },
            "rating": {
                "by_arm": {
                    "baseline": {"score": 1, "helpfulness": 5},
                    "contract_strip": {"score": -1, "helpfulness": 2},
                }
            },
        }
    ]
    gate = ab.evaluate_gates("baseline", "contract_strip", rows, rated=True)
    assert gate["gates"]["G2_quality_noninferior"] is False
    assert gate["ship"] is False
