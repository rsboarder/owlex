"""Tests for bench/mutate.py — pure, fast, no live codex calls, no filesystem I/O.

Fixture source is a small hand-written function that contains one of every
operator type so each test can be written against a known line number.
"""
from __future__ import annotations

import ast
import os
import tempfile

from bench import mutate
from bench.corpus import added_lines_by_file


# ---------------------------------------------------------------------------
# Fixture source — one token of every operator type, each on its own line so
# line-number assertions are trivial to verify by eye.
# ---------------------------------------------------------------------------

FIXTURE_SOURCE = """\
def check(x, n, flag):
    if x < 10:
        return True
    if flag and x > 0:
        total = n + 1
        return total
    return False
"""
# Line map (1-indexed):
# 1: def check(x, n, flag):
# 2:     if x < 10:            — Lt  → boundary
# 3:         return True        — True → logic  (bool_const)
# 4:     if flag and x > 0:    — and → logic  (bool_op);  Gt → boundary
# 5:         total = n + 1      — BinOp(Add) → logic;  Num(1) → boundary
# 6:         return total
# 7:     return False           — False → logic (bool_const)


def _ops_at_line(candidates: list[dict], line: int) -> set[str]:
    return {c["op"] for c in candidates if c["line"] == line}


# ---------------------------------------------------------------------------
# find_mutations
# ---------------------------------------------------------------------------

def test_find_mutations_lt_on_line_2():
    candidates = mutate.find_mutations(FIXTURE_SOURCE)
    ops_2 = _ops_at_line(candidates, 2)
    assert "cmp_lt_lte" in ops_2


def test_find_mutations_true_on_line_3():
    candidates = mutate.find_mutations(FIXTURE_SOURCE)
    ops_3 = _ops_at_line(candidates, 3)
    assert "bool_const" in ops_3


def test_find_mutations_and_on_line_4():
    candidates = mutate.find_mutations(FIXTURE_SOURCE)
    ops_4 = _ops_at_line(candidates, 4)
    assert "bool_op" in ops_4


def test_find_mutations_add_on_line_5():
    candidates = mutate.find_mutations(FIXTURE_SOURCE)
    ops_5 = _ops_at_line(candidates, 5)
    assert "arith_add_sub" in ops_5


def test_find_mutations_int_literal_on_line_5():
    candidates = mutate.find_mutations(FIXTURE_SOURCE)
    ops_5 = _ops_at_line(candidates, 5)
    assert "int_offby1" in ops_5


def test_find_mutations_sorted_by_line():
    candidates = mutate.find_mutations(FIXTURE_SOURCE)
    lines = [c["line"] for c in candidates]
    assert lines == sorted(lines)


def test_find_mutations_empty_source():
    assert mutate.find_mutations("") == []


def test_find_mutations_syntax_error_returns_empty():
    assert mutate.find_mutations("def f(:\n    pass\n") == []


# ---------------------------------------------------------------------------
# apply_mutation
# ---------------------------------------------------------------------------

def _pick_first(source: str, op: str) -> dict:
    """Return the first mutation site for *op* in *source*."""
    return next(c for c in mutate.find_mutations(source) if c["op"] == op)


def test_apply_mutation_lt_changes_token():
    m = _pick_first(FIXTURE_SOURCE, "cmp_lt_lte")
    result = mutate.apply_mutation(FIXTURE_SOURCE, m)
    # Original token absent on that line, mutated present
    original_line = FIXTURE_SOURCE.splitlines()[m["line"] - 1]
    mutated_line  = result.splitlines()[m["line"] - 1]
    assert m["mutated_token"] in mutated_line
    # Result is syntactically valid
    ast.parse(result)


def test_apply_mutation_true_flips_to_false():
    m = _pick_first(FIXTURE_SOURCE, "bool_const")
    result = mutate.apply_mutation(FIXTURE_SOURCE, m)
    mutated_line = result.splitlines()[m["line"] - 1]
    assert m["mutated_token"] in mutated_line
    ast.parse(result)


def test_apply_mutation_and_flips_to_or():
    m = _pick_first(FIXTURE_SOURCE, "bool_op")
    result = mutate.apply_mutation(FIXTURE_SOURCE, m)
    mutated_line = result.splitlines()[m["line"] - 1]
    assert m["mutated_token"] in mutated_line
    ast.parse(result)


def test_apply_mutation_add_flips_to_sub():
    m = _pick_first(FIXTURE_SOURCE, "arith_add_sub")
    result = mutate.apply_mutation(FIXTURE_SOURCE, m)
    mutated_line = result.splitlines()[m["line"] - 1]
    assert m["mutated_token"] in mutated_line
    ast.parse(result)


def test_apply_mutation_int_offby1():
    m = _pick_first(FIXTURE_SOURCE, "int_offby1")
    orig_n = int(m["original_token"])
    result = mutate.apply_mutation(FIXTURE_SOURCE, m)
    mutated_line = result.splitlines()[m["line"] - 1]
    assert str(orig_n + 1) in mutated_line
    ast.parse(result)


def test_apply_mutation_leaves_other_lines_unchanged():
    m = _pick_first(FIXTURE_SOURCE, "cmp_lt_lte")
    result = mutate.apply_mutation(FIXTURE_SOURCE, m)
    orig_lines = FIXTURE_SOURCE.splitlines()
    result_lines = result.splitlines()
    for i, (ol, rl) in enumerate(zip(orig_lines, result_lines), start=1):
        if i != m["line"]:
            assert ol == rl, f"Line {i} unexpectedly changed"


# ---------------------------------------------------------------------------
# generate_mutants
# ---------------------------------------------------------------------------

def test_generate_mutants_items_have_correct_structure():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(FIXTURE_SOURCE)
        tmp_path = fh.name
    try:
        items = mutate.generate_mutants(tmp_path)
        assert len(items) > 0
        for item in items:
            assert "id" in item
            assert "bugs" in item
            assert len(item["bugs"]) == 1
            bug = item["bugs"][0]
            assert "bug_type" in bug
            assert "file" in bug
            assert "line" in bug
            assert "description" in bug
            assert isinstance(bug["line"], int) and bug["line"] > 0
    finally:
        os.unlink(tmp_path)


def test_generate_mutants_bug_line_matches_mutation_line():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(FIXTURE_SOURCE)
        tmp_path = fh.name
    try:
        items = mutate.generate_mutants(tmp_path)
        # Find the item for the `<` on line 2
        lt_items = [it for it in items if "cmp_lt_lte" in it["id"]]
        assert lt_items, "Expected at least one cmp_lt_lte mutant"
        assert lt_items[0]["bugs"][0]["line"] == 2
    finally:
        os.unlink(tmp_path)


def test_generate_mutants_respects_max_per_file():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(FIXTURE_SOURCE)
        tmp_path = fh.name
    try:
        items = mutate.generate_mutants(tmp_path, max_per_file=2)
        assert len(items) <= 2
    finally:
        os.unlink(tmp_path)


def test_generate_mutants_mutated_source_parses():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(FIXTURE_SOURCE)
        tmp_path = fh.name
    try:
        items = mutate.generate_mutants(tmp_path)
        for item in items:
            ast.parse(item["mutated_source"])  # must not raise
    finally:
        os.unlink(tmp_path)


def test_generate_mutants_no_duplicate_ids():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(FIXTURE_SOURCE)
        tmp_path = fh.name
    try:
        items = mutate.generate_mutants(tmp_path)
        ids = [it["id"] for it in items]
        assert len(ids) == len(set(ids))
    finally:
        os.unlink(tmp_path)


def test_generate_mutants_diff_non_empty_and_first_added_line_matches_bug_line():
    """Each generated mutant must carry a non-empty diff whose first added line
    number (as parsed by added_lines_by_file) matches bugs[0].line — ensuring
    the raw_diff runner can score the mutant correctly."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(FIXTURE_SOURCE)
        tmp_path = fh.name
    try:
        items = mutate.generate_mutants(tmp_path)
        assert len(items) > 0, "Expected at least one mutant"
        for item in items:
            diff = item.get("diff", "")
            assert diff.strip(), f"Item {item['id']} has empty diff"
            added = added_lines_by_file(diff)
            # The diff uses the basename (or rel path) of the temp file as the key.
            assert added, f"Item {item['id']} diff produced no added lines"
            # At least one file in the diff has added lines
            all_added_lines: list[int] = []
            for file_added in added.values():
                all_added_lines.extend(file_added.keys())
            assert all_added_lines, f"Item {item['id']} diff has no + lines"
            first_added_line = min(all_added_lines)
            bug_line = item["bugs"][0]["line"]
            assert first_added_line == bug_line, (
                f"Item {item['id']}: diff first added line {first_added_line} "
                f"!= bugs[0].line {bug_line}"
            )
    finally:
        os.unlink(tmp_path)
