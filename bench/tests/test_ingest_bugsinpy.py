"""Unit tests for bench/ingest_bugsinpy.py — NO network.

Builds a fixture BugsInPy tree in tmp_path with:
  - projects/foo/bugs/1/bug_patch.txt  (real .py change → included)
  - projects/foo/bugs/2/bug_patch.txt  (test-only change → skipped)
  - projects/bar/bugs/1/bug_patch.txt  (real .py change → included, bar project)

Asserts:
- ``ingest`` returns correctly shaped, validly labeled items.
- Test-only bug patches are skipped.
- ``parse_bug_patch`` resolves to the right file/line.
- ``load_corpus(include=("dataset",))`` works with manifest present and absent.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bench.ingest_bugsinpy import ingest, parse_bug_patch
from bench import corpus


# ---------------------------------------------------------------------------
# Hand-written diffs
# ---------------------------------------------------------------------------

REAL_BUG_PATCH = """\
diff --git a/src/black/linegen.py b/src/black/linegen.py
--- a/src/black/linegen.py
+++ b/src/black/linegen.py
@@ -42,7 +42,7 @@
 def visit_default(self, node: LN) -> Iterator[Line]:
-    yield from self.visit(node)
+    yield from self._visit(node)
"""

TEST_ONLY_PATCH = """\
diff --git a/tests/test_black.py b/tests/test_black.py
--- a/tests/test_black.py
+++ b/tests/test_black.py
@@ -10,3 +10,4 @@
 def test_foo():
+    assert True
"""

BAR_BUG_PATCH = """\
diff --git a/tornado/ioloop.py b/tornado/ioloop.py
--- a/tornado/ioloop.py
+++ b/tornado/ioloop.py
@@ -200,6 +200,7 @@
 class IOLoop:
+    _instance = None
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bugsinpy_fixture(tmp_path):
    """Build a minimal BugsInPy tree."""
    def make_patch(project, bug_id, content):
        bug_dir = tmp_path / "projects" / project / "bugs" / str(bug_id)
        bug_dir.mkdir(parents=True, exist_ok=True)
        (bug_dir / "bug_patch.txt").write_text(content, encoding="utf-8")

    make_patch("foo", 1, REAL_BUG_PATCH)
    make_patch("foo", 2, TEST_ONLY_PATCH)
    make_patch("bar", 1, BAR_BUG_PATCH)
    return str(tmp_path)


@pytest.fixture()
def dataset_manifest(tmp_path, bugsinpy_fixture):
    """Run ingest on the fixture and return the produced items + manifest path."""
    items = ingest(bugsinpy_fixture, projects=["foo", "bar"], max_per_project=5)
    manifest = {
        "schema_version": 1,
        "kind": "dataset",
        "description": "test",
        "items": items,
    }
    manifest_dir = tmp_path / "corpus" / "dataset"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(manifest_path), items


# ---------------------------------------------------------------------------
# parse_bug_patch
# ---------------------------------------------------------------------------

def test_parse_bug_patch_returns_file_and_line():
    locations = parse_bug_patch(REAL_BUG_PATCH)
    assert len(locations) == 1
    loc = locations[0]
    assert loc["file"].endswith(".py")
    assert isinstance(loc["line"], int)
    assert loc["line"] >= 1


def test_parse_bug_patch_skips_non_py():
    diff = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
+new line
"""
    assert parse_bug_patch(diff) == []


def test_parse_bug_patch_returns_one_entry_per_py_file():
    diff = """\
--- a/foo.py
+++ b/foo.py
@@ -1,0 +1,1 @@
+line_a
--- a/bar.py
+++ b/bar.py
@@ -5,0 +5,1 @@
+line_b
"""
    locs = parse_bug_patch(diff)
    files = {l["file"] for l in locs}
    assert "foo.py" in files
    assert "bar.py" in files


# ---------------------------------------------------------------------------
# ingest — item shape
# ---------------------------------------------------------------------------

def test_ingest_returns_items_for_non_test_patches(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo"], max_per_project=5)
    ids = [i["id"] for i in items]
    assert "bugsinpy-foo-1" in ids


def test_ingest_skips_test_only_patches(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo"], max_per_project=5)
    ids = [i["id"] for i in items]
    assert "bugsinpy-foo-2" not in ids


def test_ingest_item_has_required_keys(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo"], max_per_project=5)
    assert len(items) >= 1
    item = items[0]
    for key in ("id", "file", "diff", "bugs", "source", "lang", "diff_size",
                "risk_domain", "difficulty", "split", "provenance"):
        assert key in item, f"key {key!r} missing from item"


def test_ingest_item_id_format(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo", "bar"], max_per_project=5)
    for item in items:
        project = item["provenance"]["project"]
        bug_id = item["provenance"]["bug_id"]
        assert item["id"] == f"bugsinpy-{project}-{bug_id}"


def test_ingest_item_source_is_dataset(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo"], max_per_project=5)
    for item in items:
        assert item["source"] == "dataset"


def test_ingest_item_bugs_are_validly_shaped(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo", "bar"], max_per_project=5)
    for item in items:
        assert isinstance(item["bugs"], list)
        assert len(item["bugs"]) >= 1
        for bug in item["bugs"]:
            for key in ("bug_type", "file", "line", "description"):
                assert key in bug, f"bug missing key {key!r}"
            assert isinstance(bug["line"], int) and bug["line"] >= 1
            assert bug["bug_type"] == "real-bug"


def test_ingest_item_provenance_has_source_project_bug_id(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo", "bar"], max_per_project=5)
    for item in items:
        prov = item["provenance"]
        assert prov["source"] == "BugsInPy"
        assert "project" in prov
        assert "bug_id" in prov


def test_ingest_diff_size_label_is_valid(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo", "bar"], max_per_project=5)
    for item in items:
        assert item["diff_size"] in ("S", "M", "L")


def test_ingest_max_per_project_cap(bugsinpy_fixture):
    items_1 = ingest(bugsinpy_fixture, projects=["foo"], max_per_project=1)
    assert len(items_1) <= 1


def test_ingest_multi_project(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["foo", "bar"], max_per_project=5)
    projects_seen = {i["provenance"]["project"] for i in items}
    assert "foo" in projects_seen
    assert "bar" in projects_seen


def test_ingest_missing_project_dir_is_skipped(bugsinpy_fixture):
    items = ingest(bugsinpy_fixture, projects=["nonexistent"], max_per_project=5)
    assert items == []


# ---------------------------------------------------------------------------
# load_corpus dataset integration
# ---------------------------------------------------------------------------

def test_load_corpus_dataset_present(dataset_manifest, tmp_path):
    manifest_path, expected_items = dataset_manifest
    corpus_root = str(tmp_path / "corpus")
    # load_corpus("dataset") loads from corpus_root/dataset/manifest.json
    items = corpus.load_corpus(root=corpus_root, include=("dataset",))
    assert len(items) == len(expected_items)
    for item in items:
        assert item["source"] == "dataset"


def test_load_corpus_dataset_absent(tmp_path):
    corpus_root = str(tmp_path / "nonexistent_corpus")
    # Should silently return empty list (no seeded manifest either)
    items = corpus.load_corpus(root=corpus_root, include=("dataset",))
    assert items == []


def test_load_dataset_returns_empty_on_missing_path():
    assert corpus.load_dataset("/nonexistent/path/manifest.json") == []


def test_load_corpus_tolerates_absent_dataset_manifest(tmp_path):
    """dataset is in the default include, but its manifest is optional.

    A corpus root with only a seeded manifest (no dataset/manifest.json) must
    load cleanly — ``load_dataset`` returns [] for the absent file rather than
    raising — so callers never need to special-case which sources exist on disk.
    """
    corpus_root = str(tmp_path / "corpus")
    os.makedirs(os.path.join(corpus_root, "seeded"), exist_ok=True)
    with open(os.path.join(corpus_root, "seeded", "manifest.json"), "w") as f:
        json.dump({"items": [{"id": "s1", "diff_path": "x.diff", "bugs": []}]}, f)
    with open(os.path.join(corpus_root, "seeded", "x.diff"), "w") as f:
        f.write("")
    items = corpus.load_corpus(root=corpus_root, include=("seeded", "dataset"))
    assert all(it.get("source") for it in items)
    assert not any(it.get("source") == "dataset" for it in items)
