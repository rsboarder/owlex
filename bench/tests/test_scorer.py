"""Fixture-based unit tests for the pure scorer — no live codex, deterministic.

The hand-checked fixture below has known TP / FP / FN so precision/recall math
is verified against numbers a human can re-derive by eye.
"""
from __future__ import annotations

from bench import scorer


# --- parse_findings ------------------------------------------------------

def test_parse_findings_extracts_file_line_citations():
    text = (
        "The reaper is missing at owlex/worker.py:42 so the child leaks.\n"
        "Also agreement.py:7 parses the env wrong."
    )
    findings = scorer.parse_findings(text)
    assert {(f["file"], f["line"]) for f in findings} == {
        ("owlex/worker.py", 42),
        ("agreement.py", 7),
    }
    # snippet keeps the surrounding context line for triage
    assert any("reaper is missing" in f["snippet"] for f in findings)


def test_parse_findings_dedupes_by_basename_and_line():
    text = "owlex/worker.py:42 ... worker.py:42 ... owlex/worker.py:42"
    assert len(scorer.parse_findings(text)) == 1


def test_parse_findings_empty_and_no_citations():
    assert scorer.parse_findings("") == []
    assert scorer.parse_findings("no citations here, just prose") == []


# --- score_run: matching + precision/recall ------------------------------

BUGS = [
    {"bug_type": "resource_leak", "file": "owlex/worker.py", "line": 42, "description": "x"},
    {"bug_type": "error_swallow", "file": "owlex/agreement.py", "line": 100, "description": "y"},
]
DECOYS = [
    {"file": "owlex/worker.py", "line": 12, "description": "import reorder"},
]


def test_exact_and_within_window_match_is_true_positive():
    findings = [
        {"file": "owlex/worker.py", "line": 42, "snippet": ""},   # exact
        {"file": "owlex/agreement.py", "line": 102, "snippet": ""},  # +2, window 3
    ]
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3)
    assert s["tp"] == 2
    assert s["fp"] == 0
    assert s["bugs_found"] == 2
    assert s["recall"] == 1.0
    assert s["precision"] == 1.0
    assert s["decoy_hits"] == 0
    assert s["detected_any"] is True


def test_outside_window_is_a_miss():
    findings = [{"file": "owlex/worker.py", "line": 48, "snippet": ""}]  # +6 > window 3
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3)
    assert s["bugs_found"] == 0
    assert s["fp"] == 1
    assert s["recall"] == 0.0
    assert s["precision"] == 0.0


def test_decoy_hit_is_attributable_false_positive():
    findings = [{"file": "owlex/worker.py", "line": 12, "snippet": ""}]  # the decoy
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3)
    assert s["tp"] == 0
    assert s["fp"] == 1
    assert s["decoy_hits"] == 1


def test_hallucination_is_false_positive_but_not_a_decoy():
    findings = [{"file": "owlex/nowhere.py", "line": 999, "snippet": ""}]
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3)
    assert s["fp"] == 1
    assert s["decoy_hits"] == 0


def test_known_tp_fp_fn_fixture_math():
    # ground truth: 2 bugs. findings: 1 real (worker:42), 1 decoy, 1 halluc.
    # → TP=1, FP=2, FN=1 (agreement bug missed) → precision 1/3, recall 1/2.
    findings = [
        {"file": "owlex/worker.py", "line": 41, "snippet": ""},   # TP (-1)
        {"file": "owlex/worker.py", "line": 12, "snippet": ""},   # FP (decoy)
        {"file": "owlex/ghost.py", "line": 5, "snippet": ""},     # FP (halluc)
    ]
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3)
    assert s["tp"] == 1
    assert s["fp"] == 2
    assert s["bugs_found"] == 1
    assert s["recall"] == 0.5
    assert abs(s["precision"] - 1 / 3) < 1e-9
    assert s["decoy_hits"] == 1


def test_file_granularity_matches_when_line_is_far_off():
    # A finding in the right file but a wrong/absent line: misses at line
    # granularity (the prose problem), matches at file granularity (the fair
    # yardstick for line-less prose input).
    findings = [{"file": "owlex/worker.py", "line": 999, "snippet": ""}]
    line = scorer.score_run(findings, BUGS, DECOYS, line_window=3, granularity="line")
    file = scorer.score_run(findings, BUGS, DECOYS, line_window=3, granularity="file")
    assert line["recall"] == 0.0
    assert file["recall"] == 0.5  # 1 of 2 bugs' files identified
    assert file["tp"] == 1


def test_file_granularity_still_attributes_decoys_by_file():
    findings = [{"file": "owlex/worker.py", "line": 1, "snippet": ""}]  # decoy file, wrong line
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3, granularity="file")
    # worker.py hosts both a bug (line 42) and the decoy (line 12); at file
    # granularity the finding matches the bug first → TP, not a decoy hit.
    assert s["tp"] == 1
    assert s["decoy_hits"] == 0


def test_no_findings_precision_is_none_recall_zero():
    s = scorer.score_run([], BUGS, DECOYS)
    assert s["precision"] is None
    assert s["recall"] == 0.0
    assert s["detected_any"] is False


def test_duplicate_findings_counted_once():
    findings = [
        {"file": "owlex/worker.py", "line": 42, "snippet": ""},
        {"file": "worker.py", "line": 42, "snippet": ""},  # same site, basename only
    ]
    s = scorer.score_run(findings, BUGS, DECOYS, line_window=3)
    assert s["n_findings"] == 1
    assert s["tp"] == 1


# --- aggregate -----------------------------------------------------------

def test_aggregate_mean_and_stdev():
    run_scores = [
        scorer.score_run([{"file": "owlex/worker.py", "line": 42, "snippet": ""}], BUGS, DECOYS),
        scorer.score_run([], BUGS, DECOYS),  # precision None → skipped in precision agg
    ]
    agg = scorer.aggregate(run_scores)
    assert agg["runs"] == 2
    assert agg["recall"]["mean"] == 0.25  # (0.5 + 0.0) / 2
    assert agg["recall"]["n"] == 2
    # precision: only the first run had findings (1.0); second is None → n=1
    assert agg["precision"]["mean"] == 1.0
    assert agg["precision"]["n"] == 1
    assert agg["detection_rate"]["mean"] == 0.5


def test_aggregate_all_none_precision():
    agg = scorer.aggregate([scorer.score_run([], BUGS, DECOYS) for _ in range(3)])
    assert agg["precision"]["mean"] is None
    assert agg["precision"]["n"] == 0


# --- score_corpus --------------------------------------------------------

def test_score_corpus_per_item_and_pooled():
    item = {"id": "seed-x", "bugs": BUGS, "decoys": DECOYS}
    runs = [
        [{"file": "owlex/worker.py", "line": 42, "snippet": ""}],
        [{"file": "owlex/worker.py", "line": 42, "snippet": ""},
         {"file": "owlex/agreement.py", "line": 100, "snippet": ""}],
    ]
    result = scorer.score_corpus([{"item": item, "runs": runs}], line_window=3)
    assert len(result["per_item"]) == 1
    assert result["per_item"][0]["id"] == "seed-x"
    # pooled over 2 runs: recall 0.5 and 1.0 → mean 0.75
    assert result["corpus_aggregate"]["recall"]["mean"] == 0.75


# --- validate_manifest ---------------------------------------------------

def _valid_manifest():
    items = []
    types = ["resource_leak", "error_swallow", "boundary"]
    for i in range(5):
        items.append({
            "id": f"seed-{i:02d}",
            "file": f"owlex/mod{i}.py",
            "diff_path": f"diffs/seed-{i:02d}.diff",
            "prose_summary": "did a thing",
            "bugs": [
                {"bug_type": types[i % 3], "file": f"owlex/mod{i}.py", "line": 1, "description": "d"},
                {"bug_type": types[(i + 1) % 3], "file": f"owlex/mod{i}.py", "line": 2, "description": "d"},
            ],
            "decoys": [{"file": f"owlex/mod{i}.py", "line": 1, "description": "d"}],
        })
    return {"items": items}


def test_validate_manifest_accepts_valid():
    assert scorer.validate_manifest(_valid_manifest()) == []


def test_validate_manifest_rejects_too_few_bugs():
    m = {"items": [{
        "id": "x", "file": "f.py", "diff_path": "diffs/x.diff", "prose_summary": "p",
        "bugs": [{"bug_type": "a", "file": "f.py", "line": 1, "description": "d"}],
        "decoys": [{"file": "f.py", "line": 1, "description": "d"},
                   {"file": "f.py", "line": 2, "description": "d"}],
    }]}
    errors = scorer.validate_manifest(m)
    assert any("≥10 bugs" in e for e in errors)
    assert any("bug_types" in e for e in errors)


def test_validate_manifest_rejects_missing_fields_and_bad_line():
    m = {"items": [{
        "id": "x", "file": "f.py", "diff_path": "diffs/x.diff", "prose_summary": "p",
        "bugs": [{"bug_type": "a", "file": "f.py", "line": 0, "description": "d"}],
        "decoys": [],
    }]}
    errors = scorer.validate_manifest(m)
    assert any("line must be a positive integer" in e for e in errors)
    assert any("≥2 decoys" in e for e in errors)


def test_validate_manifest_empty():
    assert scorer.validate_manifest({"items": []}) == ["manifest.items must be a non-empty list"]
