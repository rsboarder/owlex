"""Runner tests — pure-helper + dry-mode end-to-end. No live codex calls.

Dry mode exercises the full build_prompt → parse_findings → score_corpus →
report path so the plumbing is verified deterministically; live calls are out
of scope for the test suite (they cost codex time/tokens).
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from bench import run


def test_build_prompt_raw_diff_carries_the_real_diff():
    item = {"diff": "diff --git a/x.py b/x.py\n+boom", "prose_summary": "editorialized words"}
    prompt = run.build_prompt(item, "raw_diff")
    assert "Unified diff under review" in prompt
    assert "+boom" in prompt
    assert "editorialized words" not in prompt
    assert "path:line" in prompt  # the citation instruction from AUDIT_LENS


def test_build_prompt_prose_carries_the_summary_not_the_diff():
    item = {"diff": "diff --git a/x.py b/x.py\n+boom", "prose_summary": "editorialized words"}
    prompt = run.build_prompt(item, "prose")
    assert "editorialized words" in prompt
    assert "+boom" not in prompt


def test_materialize_writes_post_image_files_to_disk():
    item = {
        "id": "x",
        "diff": (
            "diff --git a/owlex/m.py b/owlex/m.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/owlex/m.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def f():\n"
            "+    return 1\n"
        ),
    }
    d = run._materialize(item)
    try:
        target = os.path.join(d, "owlex", "m.py")
        assert os.path.exists(target)
        with open(target) as fh:
            assert fh.read() == "def f():\n    return 1\n"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unknown_target_is_rejected_loudly():
    args = run._parse_args(["--corpus", "seeded", "--target", "panel", "--dry"])
    with pytest.raises(SystemExit):
        run.execute(args)


def test_dry_seeded_run_produces_scored_report_for_both_variants():
    args = run._parse_args(["--corpus", "seeded", "--runs", "2", "--dry"])
    n_items = len(run.corpus.load_seeded(args.manifest)["items"])
    report = run.execute(args)
    assert report["corpus"] == "seeded"
    assert report["input_variants"] == ["raw_diff", "prose"]
    assert report["generated_with"]["dry"] is True
    for variant in ("raw_diff", "prose"):
        block = report["results"][variant]
        # raw line/file + the AUDIT-1 verified block
        assert set(block["scored"]) == {"line", "file", "verified"}
        assert set(block["scored"]["verified"]) == {"line", "file"}
        # dry → no findings → recall 0, detection_rate 0 at both granularities
        assert block["scored"]["line"]["corpus_aggregate"]["recall"]["mean"] == 0.0
        assert block["scored"]["file"]["corpus_aggregate"]["recall"]["mean"] == 0.0
        # dry → no citations → nothing to verify, nothing dropped
        assert block["scored"]["verified"]["line"]["corpus_dropped"]["total"] == 0
        assert block["scored"]["verified"]["line"]["corpus_aggregate"]["recall"]["mean"] == 0.0
        assert block["cost"]["tokens"] is None
        assert len(block["per_item_runs"]) == n_items  # every seeded item scored


def test_dry_single_variant_seeded():
    args = run._parse_args(["--corpus", "seeded", "--runs", "1", "--dry", "--input-variant", "raw_diff"])
    report = run.execute(args)
    assert report["input_variants"] == ["raw_diff"]
    assert "prose" not in report["results"]


def test_dry_real_run_has_cost_but_no_scoring():
    args = run._parse_args(["--corpus", "real", "--runs", "1", "--dry"])
    report = run.execute(args)
    assert report["corpus"] == "real"
    block = report["results"]["raw_diff"]
    assert "scored" not in block            # real corpus is unlabeled
    assert "cost" in block
    assert len(block["per_item_runs"]) >= 3  # ≥3 real diffs


def test_compact_baseline_drops_raw_records_keeps_aggregates():
    args = run._parse_args(["--corpus", "seeded", "--runs", "2", "--dry"])
    report = run.execute(args)
    compact = run.compact_baseline(report)
    block = compact["results"]["raw_diff"]
    assert "per_item_runs" not in block                          # raw records stripped
    assert set(block["scored"]) == {"line", "file", "verified"}  # granularities + verified kept
    assert "corpus_aggregate" in block["scored"]["line"]
    assert all(set(pi) == {"id", "aggregate"} for pi in block["scored"]["file"]["per_item"])
    # the verified block survives compaction with its drop accounting
    assert "corpus_dropped" in block["scored"]["verified"]["line"]
    assert compact["target"] == report["target"]


def test_main_dry_writes_report_file(tmp_path):
    out = tmp_path / "report.json"
    rc = run.main(["--corpus", "seeded", "--runs", "1", "--dry", "--out", str(out)])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["target"] == "cross_model"
    assert report["runs"] == 1


def test_main_out_and_baseline_write_both_in_one_pass(tmp_path, monkeypatch):
    out = tmp_path / "full.json"
    monkeypatch.setattr(run, "BASELINES_DIR", str(tmp_path / "baselines"))
    rc = run.main(["--corpus", "seeded", "--runs", "1", "--dry", "--out", str(out), "--baseline"])
    assert rc == 0
    full = json.loads(out.read_text())
    compact = json.loads((tmp_path / "baselines" / "cross_model.json").read_text())
    # full keeps raw records; compact strips them
    assert "per_item_runs" in full["results"]["raw_diff"]
    assert "per_item_runs" not in compact["results"]["raw_diff"]
