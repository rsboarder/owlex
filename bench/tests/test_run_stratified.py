"""Stratified corpus mode tests — dry only, no live codex calls.

Exercises:
- Runnable filter: db-llm-label items (no diff/post_image) are excluded.
- --per-source cap: at most N items per source, deterministically sorted by id.
- --max-items global cap.
- Stratified mode forces raw_diff variant, reports per-source counts to stderr.
- Stratified scoring: labeled items (with bugs) produce a scored.line block;
  unlabeled items are included in cost but not in scoring.
"""
from __future__ import annotations

import pytest

from bench import run


# --- fixture helpers --------------------------------------------------------

_RUNNABLE_MUTANT = {
    "id": "mutant-001",
    "source": "mutant",
    "diff": "diff --git a/foo.py b/foo.py\n@@ -1 +1 @@\n-x\n+y",
    "bugs": [{"bug_type": "logic", "file": "foo.py", "line": 1, "description": "wrong value"}],
}
_RUNNABLE_SEEDED = {
    "id": "seeded-001",
    "source": "seeded",
    "diff": "diff --git a/bar.py b/bar.py\n@@ -1 +1 @@\n-a\n+b",
    "bugs": [{"bug_type": "logic", "file": "bar.py", "line": 1, "description": "wrong"}],
}
_RUNNABLE_NO_BUGS = {
    "id": "seeded-002",
    "source": "seeded",
    "diff": "diff --git a/baz.py b/baz.py\n@@ -1 +1 @@\n-c\n+d",
    # no "bugs" key — unlabeled
}
_NON_RUNNABLE_DB = {
    "id": "db-001",
    "source": "db-llm-label",
    # neither diff nor post_image — should be excluded
}


# --- _is_runnable -----------------------------------------------------------

def test_is_runnable_with_diff():
    assert run._is_runnable({"diff": "some diff text"}) is True


def test_is_runnable_with_post_image():
    assert run._is_runnable({"post_image": {"f.py": "code"}}) is True


def test_is_runnable_requires_nonempty_diff():
    assert run._is_runnable({"diff": ""}) is False


def test_is_runnable_db_labeled_item():
    assert run._is_runnable(_NON_RUNNABLE_DB) is False


# --- stratified mode via monkeypatched load_corpus --------------------------

def _make_args(monkeypatch, corpus_items, extra_argv=None):
    """Return a _parse_args Namespace with stratified mode and mocked load_corpus."""
    monkeypatch.setattr(run.corpus, "load_corpus", lambda **kw: list(corpus_items))
    argv = ["--corpus", "stratified", "--runs", "1", "--dry"] + (extra_argv or [])
    return run._parse_args(argv)


def test_stratified_excludes_non_runnable_items(monkeypatch):
    items = [_RUNNABLE_MUTANT, _NON_RUNNABLE_DB]
    args = _make_args(monkeypatch, items)
    report = run.execute(args)
    per_item_runs = report["results"]["raw_diff"]["per_item_runs"]
    ids = [r["id"] for r in per_item_runs]
    assert "db-001" not in ids
    assert "mutant-001" in ids


def test_stratified_forces_raw_diff_variant(monkeypatch):
    args = _make_args(monkeypatch, [_RUNNABLE_MUTANT])
    report = run.execute(args)
    assert report["input_variants"] == ["raw_diff"]
    assert "prose" not in report["results"]


def test_stratified_per_source_cap(monkeypatch):
    items = [
        {"id": "m-001", "source": "mutant", "diff": "d1", "bugs": []},
        {"id": "m-002", "source": "mutant", "diff": "d2", "bugs": []},
        {"id": "m-003", "source": "mutant", "diff": "d3", "bugs": []},
        {"id": "s-001", "source": "seeded", "diff": "d4", "bugs": []},
        {"id": "s-002", "source": "seeded", "diff": "d5", "bugs": []},
    ]
    args = _make_args(monkeypatch, items, ["--per-source", "2"])
    report = run.execute(args)
    ids = [r["id"] for r in report["results"]["raw_diff"]["per_item_runs"]]
    # 2 mutants + 2 seeded = 4 total; m-003 should be excluded (sorted by id, take first 2)
    assert len(ids) == 4
    assert "m-003" not in ids
    assert "m-001" in ids
    assert "m-002" in ids


def test_stratified_max_items_cap(monkeypatch):
    items = [
        {"id": f"m-{i:03d}", "source": "mutant", "diff": "d", "bugs": []}
        for i in range(10)
    ]
    args = _make_args(monkeypatch, items, ["--max-items", "3"])
    report = run.execute(args)
    ids = [r["id"] for r in report["results"]["raw_diff"]["per_item_runs"]]
    assert len(ids) == 3


def test_stratified_per_source_then_max_items(monkeypatch):
    items = [
        {"id": "m-001", "source": "mutant", "diff": "d1", "bugs": []},
        {"id": "m-002", "source": "mutant", "diff": "d2", "bugs": []},
        {"id": "m-003", "source": "mutant", "diff": "d3", "bugs": []},
        {"id": "s-001", "source": "seeded", "diff": "d4", "bugs": []},
        {"id": "s-002", "source": "seeded", "diff": "d5", "bugs": []},
    ]
    # per-source=2 → 4 items, then max-items=3
    args = _make_args(monkeypatch, items, ["--per-source", "2", "--max-items", "3"])
    report = run.execute(args)
    ids = [r["id"] for r in report["results"]["raw_diff"]["per_item_runs"]]
    assert len(ids) == 3


def test_stratified_only_labeled_items_produce_scored_block(monkeypatch):
    items = [_RUNNABLE_MUTANT, _RUNNABLE_NO_BUGS]
    args = _make_args(monkeypatch, items)
    report = run.execute(args)
    block = report["results"]["raw_diff"]
    # Only the item with bugs contributes to scored
    assert "scored" in block
    assert "line" in block["scored"]
    # Both items are in per_item_runs (cost is tracked for all runnable)
    assert len(block["per_item_runs"]) == 2


def test_stratified_no_bugs_items_no_scored_block(monkeypatch):
    items = [_RUNNABLE_NO_BUGS]
    args = _make_args(monkeypatch, items)
    report = run.execute(args)
    block = report["results"]["raw_diff"]
    # No labeled items → no scored block
    assert "scored" not in block


def test_stratified_scored_line_only_not_file_or_prose(monkeypatch):
    args = _make_args(monkeypatch, [_RUNNABLE_MUTANT])
    report = run.execute(args)
    scored = report["results"]["raw_diff"]["scored"]
    assert "line" in scored
    assert "file" not in scored  # stratified scores at line granularity only
    assert "verified" not in scored  # AUDIT-1 verified block is seeded-only


def test_stratified_logs_per_source_counts_to_stderr(monkeypatch, capsys):
    items = [_RUNNABLE_MUTANT, _RUNNABLE_SEEDED]
    args = _make_args(monkeypatch, items)
    run.execute(args)
    captured = capsys.readouterr()
    assert "[bench] stratified corpus" in captured.err
    assert "mutant" in captured.err
    assert "seeded" in captured.err


def test_stratified_unknown_source_raises(monkeypatch):
    monkeypatch.setattr(run.corpus, "load_corpus", lambda **kw: [])
    args = run._parse_args(["--corpus", "stratified", "--runs", "1", "--dry", "--sources", "bogus"])
    with pytest.raises(SystemExit):
        run.execute(args)


def test_stratified_corpus_field_in_report(monkeypatch):
    args = _make_args(monkeypatch, [_RUNNABLE_MUTANT])
    report = run.execute(args)
    assert report["corpus"] == "stratified"
