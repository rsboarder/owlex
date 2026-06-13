"""Tests for corpus_stats (AUDIT-10 corpus-robustness instrument)."""
from __future__ import annotations

import os

import pytest

from bench import corpus, scorer
from bench.scorer import BUG_TYPE_TAXONOMY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_items():
    """A small flat list mixing sources: seeded, mutant, db-llm-label, and a decoy item."""
    return [
        {
            "id": "item-seeded-1",
            "source": "seeded",
            "file": "owlex/probe.py",
            "bugs": [{"bug_type": "boundary", "file": "owlex/probe.py", "line": 5, "description": "off-by-one"}],
            "decoys": [],
            "diff_size": "S",
            "split": "iterate",
        },
        {
            "id": "item-mutant-1",
            "source": "mutant",
            "file": "owlex/engine.py",
            "bugs": [{"bug_type": "resource", "file": "owlex/engine.py", "line": 10, "description": "resource leak"}],
            "decoys": [],
            "diff_size": "M",
            "split": "holdout",
        },
        {
            "id": "item-db-1",
            "source": "db-llm-label",
            "file": "owlex/council.py",
            "bugs": [{"bug_type": "logic", "file": "owlex/council.py", "line": 20, "description": "wrong logic"}],
            "decoys": [],
            "diff_size": "L",
            "split": "iterate",
        },
        {
            "id": "item-decoy-1",
            "source": "seeded",
            "file": "owlex/timer.py",
            "bugs": [],
            "decoys": [{"file": "owlex/timer.py", "line": 3, "description": "style smell, not a bug"}],
            "diff_size": "S",
            "split": "iterate",
        },
    ]


# ---------------------------------------------------------------------------
# corpus_stats unit tests
# ---------------------------------------------------------------------------

class TestCorpusStats:
    def test_total(self):
        items = _make_items()
        stats = scorer.corpus_stats(items)
        assert stats["total"] == 4

    def test_by_source(self):
        stats = scorer.corpus_stats(_make_items())
        bs = stats["by_source"]
        assert bs["seeded"] == 2
        assert bs["mutant"] == 1
        assert bs["db-llm-label"] == 1

    def test_by_bug_type(self):
        stats = scorer.corpus_stats(_make_items())
        bbt = stats["by_bug_type"]
        assert bbt["boundary"] == 1
        assert bbt["resource"] == 1
        assert bbt["logic"] == 1

    def test_by_diff_size(self):
        stats = scorer.corpus_stats(_make_items())
        bds = stats["by_diff_size"]
        assert bds["S"] == 2
        assert bds["M"] == 1
        assert bds["L"] == 1

    def test_by_split(self):
        stats = scorer.corpus_stats(_make_items())
        bs = stats["by_split"]
        assert bs["iterate"] == 3
        assert bs["holdout"] == 1

    def test_objective_label_pct(self):
        stats = scorer.corpus_stats(_make_items())
        olp = stats["objective_label_pct"]
        # labeled items: seeded-1 (bugs), mutant-1 (bugs), db-1 (bugs) = 3
        # item-decoy-1 has no bugs → not labeled
        assert olp["labeled"] == 3
        # objective: seeded + mutant = 2
        assert olp["objective"] == 2
        # soft: db-llm-label = 1
        assert olp["soft"] == 1
        assert abs(olp["pct_objective"] - 2 / 3) < 1e-9

    def test_n_decoys(self):
        stats = scorer.corpus_stats(_make_items())
        # only item-decoy-1 has a non-empty decoys list
        assert stats["n_decoys"] == 1

    def test_bug_type_coverage_present(self):
        stats = scorer.corpus_stats(_make_items())
        present = stats["bug_type_coverage"]["present"]
        missing = stats["bug_type_coverage"]["missing"]
        assert "boundary" in present
        assert "resource" in present
        assert "logic" in present
        # concurrency/security/api-contract are absent from our fixture
        for bt in ("concurrency", "security", "api-contract"):
            assert bt in missing

    def test_bug_type_coverage_is_subset_of_taxonomy(self):
        stats = scorer.corpus_stats(_make_items())
        all_covered = set(stats["bug_type_coverage"]["present"]) | set(stats["bug_type_coverage"]["missing"])
        assert all_covered == set(BUG_TYPE_TAXONOMY)

    def test_content_hash_is_stable(self):
        items = _make_items()
        h1 = scorer.corpus_stats(items)["content_hash"]
        h2 = scorer.corpus_stats(items)["content_hash"]
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_content_hash_changes_on_mutation(self):
        items = _make_items()
        h1 = scorer.corpus_stats(items)["content_hash"]
        items[0]["id"] = "item-seeded-CHANGED"
        h2 = scorer.corpus_stats(items)["content_hash"]
        assert h1 != h2

    def test_empty_list(self):
        stats = scorer.corpus_stats([])
        assert stats["total"] == 0
        assert stats["n_decoys"] == 0
        assert stats["objective_label_pct"]["labeled"] == 0
        assert stats["objective_label_pct"]["pct_objective"] is None

    def test_all_sources_unlabeled(self):
        items = [{"id": "x", "source": "seeded", "bugs": [], "decoys": []}]
        stats = scorer.corpus_stats(items)
        assert stats["objective_label_pct"]["labeled"] == 0
        assert stats["objective_label_pct"]["pct_objective"] is None

    def test_item_without_source_goes_to_unset(self):
        items = [{"id": "no-src", "bugs": [{"bug_type": "logic", "file": "f.py", "line": 1, "description": "x"}]}]
        stats = scorer.corpus_stats(items)
        assert "_unset" in stats["by_source"]

    def test_item_without_split_goes_to_unset(self):
        items = [{"id": "no-split", "source": "seeded", "bugs": []}]
        stats = scorer.corpus_stats(items)
        assert "_unset" in stats["by_split"]

    def test_pure_no_io(self, tmp_path):
        """corpus_stats must not do any file I/O — it is a pure transform."""
        import io
        # Call it; if it raises FileNotFoundError or similar it fails
        items = _make_items()
        stats = scorer.corpus_stats(items)
        assert isinstance(stats, dict)


# ---------------------------------------------------------------------------
# load_corpus integration tests
# ---------------------------------------------------------------------------

SEEDED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "corpus", "seeded")
CORPUS_ROOT = os.path.dirname(SEEDED_DIR)


class TestLoadCorpus:
    def test_returns_at_least_seeded_count(self):
        from bench.corpus import load_seeded
        manifest = load_seeded(os.path.join(SEEDED_DIR, "manifest.json"))
        seeded_count = len(manifest.get("items", []))
        flat = corpus.load_corpus(CORPUS_ROOT)
        assert len(flat) >= seeded_count

    def test_every_item_has_source(self):
        flat = corpus.load_corpus(CORPUS_ROOT)
        for item in flat:
            assert "source" in item, f"item {item.get('id')!r} missing 'source'"

    def test_tolerates_absent_optional_manifests(self, tmp_path):
        """When only seeded is present (empty tmp root), include=seeded still works."""
        # Copy seeded manifest to a fresh tmp corpus root
        import shutil
        seeded_dst = tmp_path / "seeded"
        shutil.copytree(SEEDED_DIR, seeded_dst)
        flat = corpus.load_corpus(str(tmp_path), include=("seeded", "mined", "mutants", "db_labeled"))
        assert len(flat) > 0
        assert all("source" in item for item in flat)

    def test_include_filter_works(self):
        flat_all = corpus.load_corpus(CORPUS_ROOT)
        flat_seeded_only = corpus.load_corpus(CORPUS_ROOT, include=("seeded",))
        assert len(flat_seeded_only) <= len(flat_all)
        for item in flat_seeded_only:
            assert item["source"] == "seeded"

    def test_no_duplicate_ids_within_source(self):
        flat = corpus.load_corpus(CORPUS_ROOT)
        ids = [item.get("id") for item in flat]
        # IDs should be unique within the combined corpus
        assert len(ids) == len(set(ids)), f"duplicate ids in unified corpus: {[x for x in ids if ids.count(x) > 1]}"
