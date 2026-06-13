"""Tests for bench/flywheel.py — round-trips, UPSERT dedup, corpus conversion.

All tests use an explicit tmp db_path so they never touch the production DB
(the bench conftest autouse fixture also sets OWLEX_HOME to a tmp dir, but we
pass db_path explicitly for belt-and-suspenders isolation and to be independent
of the env var).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from bench.flywheel import diff_hash_of, load_runs, record_run, runs_as_corpus


@pytest.fixture()
def tmp_db(tmp_path):
    """A fresh DB path inside a pytest tmp directory."""
    return str(tmp_path / "audit_corpus.db")


# ---------------------------------------------------------------------------
# diff_hash_of
# ---------------------------------------------------------------------------


def test_diff_hash_of_is_stable():
    text = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n+x = 1\n"
    assert diff_hash_of(text) == diff_hash_of(text)


def test_diff_hash_of_differs_on_different_text():
    assert diff_hash_of("aaa") != diff_hash_of("bbb")


# ---------------------------------------------------------------------------
# record_run + load_runs round-trip
# ---------------------------------------------------------------------------


def test_record_and_load_round_trip(tmp_db):
    findings = [{"file": "foo.py", "line": 10, "description": "off-by-one"}]
    record_run(
        diff_hash="abc123",
        findings=findings,
        outcome="completed",
        recorded_at="2026-01-01T00:00:00",
        db_path=tmp_db,
    )
    runs = load_runs(db_path=tmp_db)
    assert len(runs) == 1
    row = runs[0]
    assert row["diff_hash"] == "abc123"
    assert row["findings"] == findings
    assert row["verified"] is None
    assert row["outcome"] == "completed"
    assert row["recorded_at"] == "2026-01-01T00:00:00"


def test_load_runs_returns_empty_when_db_absent(tmp_path):
    missing = str(tmp_path / "no_such.db")
    assert load_runs(db_path=missing) == []


def test_round_trip_with_verified(tmp_db):
    findings = [{"file": "a.py", "line": 5, "description": "null deref"}]
    verified = [{"file": "a.py", "line": 5, "description": "null deref"}]
    record_run(
        diff_hash="deadbeef",
        findings=findings,
        verified=verified,
        panel_verdict="critical",
        outcome="reviewed",
        recorded_at="2026-06-10T12:00:00",
        db_path=tmp_db,
    )
    runs = load_runs(db_path=tmp_db)
    assert len(runs) == 1
    row = runs[0]
    assert row["verified"] == verified
    assert row["panel_verdict"] == "critical"


# ---------------------------------------------------------------------------
# UPSERT — same diff_hash replaces, not duplicates
# ---------------------------------------------------------------------------


def test_upsert_replaces_on_same_diff_hash(tmp_db):
    record_run(
        diff_hash="dup",
        findings=[{"file": "x.py", "line": 1, "description": "first"}],
        outcome="v1",
        db_path=tmp_db,
    )
    record_run(
        diff_hash="dup",
        findings=[{"file": "x.py", "line": 1, "description": "second"}],
        outcome="v2",
        db_path=tmp_db,
    )
    runs = load_runs(db_path=tmp_db)
    assert len(runs) == 1
    assert runs[0]["outcome"] == "v2"
    assert runs[0]["findings"][0]["description"] == "second"


def test_distinct_diff_hashes_produce_separate_rows(tmp_db):
    record_run(diff_hash="h1", findings=[], db_path=tmp_db)
    record_run(diff_hash="h2", findings=[], db_path=tmp_db)
    assert len(load_runs(db_path=tmp_db)) == 2


# ---------------------------------------------------------------------------
# runs_as_corpus
# ---------------------------------------------------------------------------


def test_runs_as_corpus_converts_verified_to_bug_items(tmp_db):
    diff_hash = diff_hash_of("some diff text here")
    verified = [
        {"file": "mod.py", "line": 42, "description": "race condition"},
        {"file": "mod.py", "line": 55, "description": "unclosed resource"},
    ]
    record_run(
        diff_hash=diff_hash,
        findings=verified,
        verified=verified,
        outcome="completed",
        db_path=tmp_db,
    )
    corpus = runs_as_corpus(db_path=tmp_db)
    assert len(corpus) == 1
    item = corpus[0]
    assert item["id"] == f"fly-{diff_hash[:8]}"
    assert item["source"] == "flywheel"
    assert item["split"] == "iterate"
    assert len(item["bugs"]) == 2
    for bug in item["bugs"]:
        assert bug["bug_type"] == "flywheel"
    assert item["provenance"]["source"] == "audit-run"
    assert item["provenance"]["diff_hash"] == diff_hash


def test_runs_as_corpus_skips_runs_without_verified(tmp_db):
    record_run(
        diff_hash="noverify",
        findings=[{"file": "a.py", "line": 1, "description": "issue"}],
        verified=None,
        db_path=tmp_db,
    )
    assert runs_as_corpus(db_path=tmp_db) == []


def test_runs_as_corpus_skips_empty_verified_list(tmp_db):
    record_run(
        diff_hash="emptyverify",
        findings=[{"file": "a.py", "line": 1, "description": "issue"}],
        verified=[],
        db_path=tmp_db,
    )
    assert runs_as_corpus(db_path=tmp_db) == []


def test_runs_as_corpus_bug_fields(tmp_db):
    verified = [{"file": "util.py", "line": 7, "description": "wrong default"}]
    record_run(
        diff_hash="fieldcheck",
        findings=verified,
        verified=verified,
        db_path=tmp_db,
    )
    corpus = runs_as_corpus(db_path=tmp_db)
    bug = corpus[0]["bugs"][0]
    assert bug["file"] == "util.py"
    assert bug["line"] == 7
    assert bug["description"] == "wrong default"


def test_runs_as_corpus_multiple_runs(tmp_db):
    for i in range(3):
        v = [{"file": f"f{i}.py", "line": i + 1, "description": f"issue {i}"}]
        record_run(
            diff_hash=f"hash{i}",
            findings=v,
            verified=v,
            db_path=tmp_db,
        )
    corpus = runs_as_corpus(db_path=tmp_db)
    assert len(corpus) == 3
    ids = {item["id"] for item in corpus}
    assert len(ids) == 3  # all unique


# ---------------------------------------------------------------------------
# Prod isolation — never touches real OWLEX_HOME
# ---------------------------------------------------------------------------


def test_does_not_touch_production_db(tmp_db, monkeypatch):
    """Explicit tmp db_path keeps writes away from OWLEX_HOME regardless of env."""
    monkeypatch.setenv("OWLEX_HOME", "/tmp/should-not-be-touched-by-flywheel-test")
    record_run(diff_hash="isolated", findings=[], db_path=tmp_db)
    prod_path = "/tmp/should-not-be-touched-by-flywheel-test/audit_corpus.db"
    assert not os.path.exists(prod_path)
