"""Unit tests for bench/mine_fixes.py — pure helpers only, no network.

Tests cover:
- infer_bug_type from commit subject strings
- _diff_size_label bucketing (S / M / L thresholds)
- _changed_py_files and _first_changed_line parsing from a hand-written diff
- mine_decoys produces bugs=[] + a populated decoys list
- mine_fix_commits returns validly-shaped items when run against the real repo
  (count not asserted — history is not frozen)
"""
from __future__ import annotations

import sys
import os

# Ensure bench package is importable when running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bench.mine_fixes import (
    infer_bug_type,
    _diff_size_label,
    _changed_py_files,
    _first_changed_line,
    mine_fix_commits,
    mine_decoys,
)


# ---------------------------------------------------------------------------
# infer_bug_type
# ---------------------------------------------------------------------------

def test_infer_bug_type_timeout_is_resource():
    assert infer_bug_type("Fix timeout enforcement: force-kill on council timeout") == "resource"


def test_infer_bug_type_security():
    assert infer_bug_type("Fix security vulnerability and improve MCP server robustness") == "security"


def test_infer_bug_type_json_parsing():
    assert infer_bug_type("Fix double JSON encoding in MCP responses") == "parsing"


def test_infer_bug_type_nameerror_is_none_deref():
    assert infer_bug_type("Fix NameError and improve response visibility") == "none_deref"


def test_infer_bug_type_generic_falls_back_to_logic():
    assert infer_bug_type("Fix session management issues identified by council review") == "logic"


def test_infer_bug_type_regression_label():
    assert infer_bug_type("Refactor: dedupe anonymization, env-helper config, regression tests") == "regression"


# ---------------------------------------------------------------------------
# _diff_size_label
# ---------------------------------------------------------------------------

def test_diff_size_small_boundary():
    assert _diff_size_label(0) == "S"
    assert _diff_size_label(19) == "S"


def test_diff_size_medium_boundary():
    assert _diff_size_label(20) == "M"
    assert _diff_size_label(79) == "M"


def test_diff_size_large_boundary():
    assert _diff_size_label(80) == "L"
    assert _diff_size_label(200) == "L"


# ---------------------------------------------------------------------------
# diff parsing helpers
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/owlex/agents/cursor.py b/owlex/agents/cursor.py
index 7689545..715da40 100644
--- a/owlex/agents/cursor.py
+++ b/owlex/agents/cursor.py
@@ -78,7 +78,7 @@ class CursorRunner(AgentRunner):

     @property
     def cli_command(self) -> str:
-        return "agent"
+        return "cursor-agent"

 @@ -93,7 +93,7 @@ class CursorRunner(AgentRunner):
-        full_command = ["agent", "--print"]
+        full_command = ["cursor-agent", "--print"]
diff --git a/owlex/server/_resources.py b/owlex/server/_resources.py
index 4e7b8f0..34cc000 100644
--- a/owlex/server/_resources.py
+++ b/owlex/server/_resources.py
@@ -24,7 +24,7 @@ async def get_agents() -> str:
-        get_cli_version("agent"),
+        get_cli_version("cursor-agent"),
"""


def test_changed_py_files_extracts_both_py_files():
    files = _changed_py_files(SAMPLE_DIFF)
    assert "owlex/agents/cursor.py" in files
    assert "owlex/server/_resources.py" in files


def test_changed_py_files_ignores_non_py():
    diff = (
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "+new line\n"
        "+++ b/owlex/foo.py\n"
        "@@ -5 +5 @@\n"
        "+pass\n"
    )
    files = _changed_py_files(diff)
    assert files == ["owlex/foo.py"]


def test_first_changed_line_parses_hunk_header():
    diff = (
        "--- a/owlex/council.py\n"
        "+++ b/owlex/council.py\n"
        "@@ -207,7 +207,7 @@\n"
        "-old\n"
        "+new\n"
    )
    assert _first_changed_line(diff, "owlex/council.py") == 207


def test_first_changed_line_fallback_when_file_not_found():
    diff = "--- a/owlex/other.py\n+++ b/owlex/other.py\n@@ -10 +10 @@\n+x\n"
    # ask for a file not present → fallback 1
    assert _first_changed_line(diff, "owlex/council.py") == 1


# ---------------------------------------------------------------------------
# mine_decoys shape contract
# ---------------------------------------------------------------------------

def test_mine_decoys_items_have_empty_bugs_and_populated_decoys():
    """mine_decoys items must carry bugs=[] and at least one decoy entry."""
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    items = mine_decoys(repo=repo_root, max_items=5)
    # May be 0 if the repo has no refactor/chore commits touching .py — that's fine,
    # but if we do get items they must satisfy the contract.
    for item in items:
        assert item["bugs"] == [], f"{item['id']} should have no bugs"
        assert item.get("decoys"), f"{item['id']} should have at least one decoy"
        assert item["source"] == "decoy"
        for d in item["decoys"]:
            assert {"file", "line", "description"} <= set(d)


# ---------------------------------------------------------------------------
# mine_fix_commits shape contract (live, count-free)
# ---------------------------------------------------------------------------

def test_mine_fix_commits_returns_validly_shaped_items():
    """Each mined fix item must carry id/source/bugs with required sub-keys."""
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    items = mine_fix_commits(repo=repo_root, max_items=20)
    assert isinstance(items, list)
    for item in items:
        assert "id" in item and item["id"].startswith("fix-")
        assert item["source"] == "real-fix"
        assert isinstance(item["bugs"], list) and len(item["bugs"]) >= 1
        for bug in item["bugs"]:
            assert {"bug_type", "file", "line", "description"} <= set(bug)
            assert isinstance(bug["line"], int) and bug["line"] >= 1
        assert item.get("diff_size") in ("S", "M", "L")
        assert item.get("split") in ("iterate", "holdout")
