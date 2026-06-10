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


# --- verify_findings (AUDIT-1 citation-check) ----------------------------

# A 50-line post-image file the findings below cite into.
POST_IMAGE = {"owlex/worker.py": "x\n" * 50}


def test_verify_keeps_resolving_finding():
    findings = [{"file": "owlex/worker.py", "line": 42, "snippet": ""}]
    out = scorer.verify_findings(findings, POST_IMAGE, line_window=3)
    assert out["kept"] == findings
    assert out["dropped"] == []


def test_verify_drops_hallucinated_file():
    findings = [{"file": "owlex/ghost.py", "line": 5, "snippet": ""}]
    out = scorer.verify_findings(findings, POST_IMAGE, line_window=3)
    assert out["kept"] == []
    assert len(out["dropped"]) == 1
    assert out["dropped"][0]["reason"] == "file_unresolved"


def test_verify_drops_out_of_range_line():
    findings = [{"file": "owlex/worker.py", "line": 999, "snippet": ""}]
    out = scorer.verify_findings(findings, POST_IMAGE, line_window=3)
    assert out["kept"] == []
    assert out["dropped"][0]["reason"] == "line_out_of_range"


def test_verify_keeps_line_within_eof_tolerance():
    # A real 50-line file cited at 52 (= 50 + window-1): LLM drift past EOF on a
    # real file, not a hallucination — kept, so a TP near the end is never lost.
    findings = [{"file": "owlex/worker.py", "line": 52, "snippet": ""}]
    out = scorer.verify_findings(findings, POST_IMAGE, line_window=3)
    assert out["kept"] == findings


def test_verify_keeps_lineless_finding_when_file_resolves():
    findings = [{"file": "owlex/worker.py", "line": None, "snippet": ""}]
    out = scorer.verify_findings(findings, POST_IMAGE, line_window=3)
    assert out["kept"] == findings


def test_score_item_verify_drops_nonresolving_and_records_it():
    item = {
        "id": "seed-x",
        "bugs": [{"bug_type": "boundary", "file": "owlex/worker.py", "line": 42, "description": "d"}],
        "decoys": [],
        "post_image": POST_IMAGE,
    }
    # one run: 1 real (TP) + 1 hallucinated file + 1 out-of-range line
    runs = [[
        {"file": "owlex/worker.py", "line": 42, "snippet": ""},
        {"file": "owlex/ghost.py", "line": 5, "snippet": ""},
        {"file": "owlex/worker.py", "line": 999, "snippet": ""},
    ]]
    si = scorer.score_item(item, runs, line_window=3, granularity="line", verify=True)
    assert si["dropped"]["total"] == 2
    assert si["dropped"]["by_reason"] == {"file_unresolved": 1, "line_out_of_range": 1}
    # surviving set is just the TP → precision 1.0
    assert si["run_scores"][0]["precision"] == 1.0
    assert si["run_scores"][0]["tp"] == 1


def test_verified_precision_is_at_least_raw_precision():
    # Same fixture, scored raw vs verified: verification removes the two
    # unresolvable false positives, so precision rises (1/3 → 1.0) and a true
    # positive is never dropped.
    item = {
        "id": "seed-x",
        "bugs": [{"bug_type": "boundary", "file": "owlex/worker.py", "line": 42, "description": "d"}],
        "decoys": [],
        "post_image": POST_IMAGE,
    }
    runs = [[
        {"file": "owlex/worker.py", "line": 42, "snippet": ""},
        {"file": "owlex/ghost.py", "line": 5, "snippet": ""},
        {"file": "owlex/worker.py", "line": 999, "snippet": ""},
    ]]
    items_runs = [{"item": item, "runs": runs}]
    raw = scorer.score_corpus(items_runs, line_window=3, granularity="line")
    verified = scorer.score_corpus(items_runs, line_window=3, granularity="line", verify=True)
    raw_p = raw["corpus_aggregate"]["precision"]["mean"]
    ver_p = verified["corpus_aggregate"]["precision"]["mean"]
    assert abs(raw_p - 1 / 3) < 1e-9
    assert ver_p == 1.0
    assert ver_p >= raw_p
    # recall is preserved — verification never drops the true positive
    assert verified["corpus_aggregate"]["recall"]["mean"] == raw["corpus_aggregate"]["recall"]["mean"]
    assert verified["corpus_dropped"]["total"] == 2
    assert verified["corpus_dropped"]["by_reason"] == {"file_unresolved": 1, "line_out_of_range": 1}


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


# --- corpus_hash ----------------------------------------------------------

def _minimal_manifest(extra_bugs: int = 0) -> dict:
    """A valid manifest with 10 bugs across 3 types + 2 decoys."""
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
            "decoys": [{"file": f"owlex/mod{i}.py", "line": 3, "description": "d"}],
        })
    return {"items": items}


def test_corpus_hash_is_stable():
    m = _minimal_manifest()
    h1 = scorer.corpus_hash(m)
    h2 = scorer.corpus_hash(m)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_corpus_hash_changes_when_bug_label_changes():
    m = _minimal_manifest()
    h_before = scorer.corpus_hash(m)
    m["items"][0]["bugs"][0]["bug_type"] = "security"
    h_after = scorer.corpus_hash(m)
    assert h_before != h_after


def test_corpus_hash_ignores_inlined_diff_and_post_image():
    """Hash must be identical before and after load_seeded() inlines diff/post_image."""
    m = _minimal_manifest()
    h_before = scorer.corpus_hash(m)
    # Simulate what load_seeded() does: inject inline content
    for item in m["items"]:
        item["diff"] = "--- /dev/null\n+++ b/x.py\n@@ -0,0 +1 @@\n+x\n"
        item["post_image"] = {"x.py": "x\n"}
    h_after = scorer.corpus_hash(m)
    assert h_before == h_after


# --- stratum_map ----------------------------------------------------------

def test_stratum_map_returns_expected_mapping():
    m = {
        "items": [
            {"id": "a", "split": "iterate"},
            {"id": "b", "split": "holdout"},
            {"id": "c"},  # field absent → _unset
        ]
    }
    result = scorer.stratum_map(m, "split")
    assert result == {"a": "iterate", "b": "holdout", "c": "_unset"}


def test_stratum_map_unknown_field_gives_unset():
    m = {"items": [{"id": "x", "lang": "python"}]}
    result = scorer.stratum_map(m, "nonexistent_field")
    assert result == {"x": "_unset"}


# --- score_by_stratum -----------------------------------------------------

def _make_run_scores(recall: float, precision: float | None) -> dict:
    return {
        "n_findings": 1 if precision is not None else 0,
        "tp": 1 if precision == 1.0 else 0,
        "fp": 0,
        "bugs_total": 1,
        "bugs_found": 1 if recall == 1.0 else 0,
        "decoy_hits": 0,
        "precision": precision,
        "recall": recall,
        "detected_any": recall > 0,
    }


def test_score_by_stratum_groups_and_aggregates():
    per_item = [
        {"id": "a", "run_scores": [_make_run_scores(1.0, 1.0), _make_run_scores(0.0, None)]},
        {"id": "b", "run_scores": [_make_run_scores(1.0, 1.0)]},
        {"id": "c", "run_scores": [_make_run_scores(0.0, 0.0)]},
    ]
    strata = {"a": "iterate", "b": "iterate", "c": "holdout"}
    result = scorer.score_by_stratum(per_item, strata)

    assert set(result.keys()) == {"iterate", "holdout"}
    # iterate: items a and b → 3 run_scores total; recall values 1.0, 0.0, 1.0 → mean 2/3
    assert abs(result["iterate"]["recall"]["mean"] - 2 / 3) < 1e-9
    assert result["iterate"]["runs"] == 3
    # holdout: item c → 1 run_score; recall 0.0
    assert result["holdout"]["recall"]["mean"] == 0.0
    assert result["holdout"]["runs"] == 1


def test_score_by_stratum_uses_unlabeled_for_missing_ids():
    per_item = [
        {"id": "unknown-id", "run_scores": [_make_run_scores(1.0, 1.0)]},
    ]
    strata = {}  # no mapping → _unlabeled
    result = scorer.score_by_stratum(per_item, strata)
    assert "_unlabeled" in result
    assert result["_unlabeled"]["runs"] == 1


def test_score_by_stratum_result_is_sorted_by_label():
    per_item = [
        {"id": "z", "run_scores": [_make_run_scores(0.0, 0.0)]},
        {"id": "a", "run_scores": [_make_run_scores(1.0, 1.0)]},
    ]
    strata = {"z": "zzz-label", "a": "aaa-label"}
    result = scorer.score_by_stratum(per_item, strata)
    assert list(result.keys()) == ["aaa-label", "zzz-label"]


# --- validate_manifest stratification field validation -------------------

def test_validate_manifest_rejects_bad_diff_size():
    m = _valid_manifest()
    m["items"][0]["diff_size"] = "XL"  # not in {S, M, L}
    errors = scorer.validate_manifest(m)
    assert any("diff_size" in e for e in errors)
    assert len(errors) == 1  # only the one stratification error


def test_validate_manifest_rejects_bad_split():
    m = _valid_manifest()
    m["items"][0]["split"] = "train"  # not in {iterate, holdout}
    errors = scorer.validate_manifest(m)
    assert any("split" in e for e in errors)
    assert len(errors) == 1


def test_validate_manifest_accepts_absent_stratification_fields():
    # A manifest with no stratification fields at all must still pass.
    m = _valid_manifest()
    for item in m["items"]:
        for f in ("source", "lang", "diff_size", "risk_domain", "difficulty", "split"):
            item.pop(f, None)
    assert scorer.validate_manifest(m) == []


def test_validate_manifest_accepts_valid_stratification_fields():
    m = _valid_manifest()
    m["items"][0].update({
        "source": "seeded",
        "lang": "python",
        "diff_size": "S",
        "risk_domain": "subprocess",
        "difficulty": "easy",
        "split": "iterate",
    })
    assert scorer.validate_manifest(m) == []


def test_validate_manifest_rejects_empty_string_in_str_field():
    m = _valid_manifest()
    m["items"][0]["source"] = ""  # empty string is not allowed when present
    errors = scorer.validate_manifest(m)
    assert any("source" in e for e in errors)
