"""Mutation injector for AUDIT-0 corpus — stdlib only (ast / json / os).

A mutation is one tiny, behavior-changing edit to a known-good source file at
a known line.  The mutated line IS the ground-truth bug location — precise,
objective, no LLM, no hand-labeling.

Operators (each maps to a ``bug_type``):
- comparison swap: ``<``↔``<=``, ``>``↔``>=`` → ``boundary``
- comparison swap: ``==``↔``!=``            → ``logic``
- boolean constant flip: ``True``↔``False``  → ``logic``
- boolean operator swap: `` and ``↔`` or ``  → ``logic``
- arithmetic swap: `` + ``↔`` - ``           → ``logic``  (guards against unary/augmented)
- numeric off-by-one: integer literal ``n``→``n+1`` → ``boundary``

Public API:
    find_mutations(source) -> list[dict]
    apply_mutation(source, mutation) -> str
    generate_mutants(src_path, max_per_file) -> list[dict]
    main()
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import sys

# Support both `python bench/mutate.py` (repo root not on sys.path) and
# `python -m bench.mutate` / pytest (repo root on sys.path).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bench.corpus import added_lines_by_file as _added_lines_by_file  # noqa: E402


# ---------------------------------------------------------------------------
# Operator catalogue
# ---------------------------------------------------------------------------

# Mapping: op_name -> (bug_type, description_template)
_OP_META: dict[str, tuple[str, str]] = {
    "cmp_lt_lte":   ("boundary", "{orig}→{mut}"),
    "cmp_gt_gte":   ("boundary", "{orig}→{mut}"),
    "cmp_eq_neq":   ("logic",    "{orig}→{mut}"),
    "bool_const":   ("logic",    "{orig}→{mut}"),
    "bool_op":      ("logic",    "{orig}→{mut}"),
    "arith_add_sub":("logic",    "{orig}→{mut}"),
    "int_offby1":   ("boundary", "{orig}→{orig_n}+1={mut}"),
}


# ---------------------------------------------------------------------------
# AST visitor — locate mutation sites
# ---------------------------------------------------------------------------

class _MutationVisitor(ast.NodeVisitor):
    """Walk the AST and collect candidate mutation sites."""

    def __init__(self):
        self.candidates: list[dict] = []

    def _add(self, line: int, col: int, op: str, orig: str, mut: str) -> None:
        self.candidates.append({
            "line": line,
            "col": col,
            "op": op,
            "original_token": orig,
            "mutated_token": mut,
            "bug_type": _OP_META[op][0],
        })

    # --- comparison operators ---
    def visit_Compare(self, node: ast.Compare) -> None:
        for cmp_op in node.ops:
            lineno = cmp_op.lineno if hasattr(cmp_op, "lineno") else node.lineno
            col = cmp_op.col_offset if hasattr(cmp_op, "col_offset") else node.col_offset
            if isinstance(cmp_op, ast.Lt):
                self._add(lineno, col, "cmp_lt_lte", "<", "<=")
            elif isinstance(cmp_op, ast.LtE):
                self._add(lineno, col, "cmp_lt_lte", "<=", "<")
            elif isinstance(cmp_op, ast.Gt):
                self._add(lineno, col, "cmp_gt_gte", ">", ">=")
            elif isinstance(cmp_op, ast.GtE):
                self._add(lineno, col, "cmp_gt_gte", ">=", ">")
            elif isinstance(cmp_op, ast.Eq):
                self._add(lineno, col, "cmp_eq_neq", "==", "!=")
            elif isinstance(cmp_op, ast.NotEq):
                self._add(lineno, col, "cmp_eq_neq", "!=", "==")
        self.generic_visit(node)

    # --- boolean constants ---
    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value is True:
            self._add(node.lineno, node.col_offset, "bool_const", "True", "False")
        elif node.value is False:
            self._add(node.lineno, node.col_offset, "bool_const", "False", "True")
        # Integer literals for off-by-one
        elif isinstance(node.value, int) and not isinstance(node.value, bool):
            self._add(node.lineno, node.col_offset, "int_offby1",
                      str(node.value), str(node.value + 1))
        self.generic_visit(node)

    # --- boolean operators (and / or) ---
    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # The operator itself spans the whole node's line; use node position
        if isinstance(node.op, ast.And):
            self._add(node.lineno, node.col_offset, "bool_op", " and ", " or ")
        elif isinstance(node.op, ast.Or):
            self._add(node.lineno, node.col_offset, "bool_op", " or ", " and ")
        self.generic_visit(node)

    # --- arithmetic +/- (binary, not augmented) ---
    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add):
            self._add(node.lineno, node.col_offset, "arith_add_sub", " + ", " - ")
        elif isinstance(node.op, ast.Sub):
            self._add(node.lineno, node.col_offset, "arith_add_sub", " - ", " + ")
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# find_mutations
# ---------------------------------------------------------------------------

def find_mutations(source: str) -> list[dict]:
    """Return all candidate mutation sites for *source*.

    Each site is a dict::

        {line, col, op, original_token, mutated_token, bug_type}

    Results are sorted by (line, col) and are deterministic.  The function is
    pure — it only reads ``source`` and never touches the filesystem.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    visitor = _MutationVisitor()
    visitor.visit(tree)
    return sorted(visitor.candidates, key=lambda c: (c["line"], c["col"]))


# ---------------------------------------------------------------------------
# apply_mutation  (line-text replacement with col disambiguation)
# ---------------------------------------------------------------------------

def _replace_nth_occurrence(text: str, old: str, new: str, n: int) -> str:
    """Replace the *n*-th (0-indexed) occurrence of *old* in *text* with *new*."""
    idx = -1
    for _ in range(n + 1):
        idx = text.find(old, idx + 1)
        if idx == -1:
            return text  # not enough occurrences — no-op
    return text[:idx] + new + text[idx + len(old):]


def _col_to_occurrence(line_text: str, token: str, col: int) -> int:
    """Return which 0-indexed occurrence of *token* in *line_text* starts at *col*.

    Falls back to the first occurrence when the column doesn't pin an exact
    match (can happen for bool_op / bool_const / int_offby1 whose ``col_offset``
    points at the containing expression, not the token itself).
    """
    # Exact position: check if ``token`` starts exactly at ``col``
    if line_text[col:col + len(token)] == token:
        # Count how many occurrences appear before col
        before = line_text[:col]
        return before.count(token)

    # Fall back: find first occurrence on the line
    return 0


def apply_mutation(source: str, mutation: dict) -> str:
    """Return *source* with exactly one token replaced as described by *mutation*.

    The replacement is performed on the specific line indicated by
    ``mutation["line"]`` (1-indexed).  If the line contains multiple identical
    tokens, ``mutation["col"]`` is used to pick the right occurrence.

    The returned string is always syntactically valid Python (the caller should
    verify with ``ast.parse`` if needed).
    """
    orig = mutation["original_token"]
    mut = mutation["mutated_token"]
    line_no = mutation["line"]  # 1-indexed
    col = mutation["col"]
    op = mutation["op"]

    lines = source.splitlines(keepends=True)
    if line_no < 1 or line_no > len(lines):
        return source

    line = lines[line_no - 1]

    if op == "bool_op":
        # " and " / " or " — replace as substring (spaces included in token)
        occ = _col_to_occurrence(line, orig, col)
        lines[line_no - 1] = _replace_nth_occurrence(line, orig, mut, occ)
    elif op in ("bool_const", "int_offby1"):
        occ = _col_to_occurrence(line, orig, col)
        lines[line_no - 1] = _replace_nth_occurrence(line, orig, mut, occ)
    else:
        # cmp_*  / arith_add_sub — token is a symbol possibly surrounded by spaces
        occ = _col_to_occurrence(line, orig, col)
        lines[line_no - 1] = _replace_nth_occurrence(line, orig, mut, occ)

    return "".join(lines)


# ---------------------------------------------------------------------------
# generate_mutants
# ---------------------------------------------------------------------------

def generate_mutants(src_path: str, max_per_file: int | None = None) -> list[dict]:
    """Read *src_path*, produce up to *max_per_file* corpus items (one per mutation).

    Each returned item is a dict shaped to match the bench corpus schema::

        {
            "id": "mut-<basename>-<line>-<op>",
            "file": <src rel path>,
            "diff": <unified diff from original to mutated>,
            "mutated_source": <full mutated file text>,
            "bugs": [{bug_type, file, line, description}],
            "source": "mutant",
            "lang": "python",
            "diff_size": "S",
            "risk_domain": "mutation",
            "difficulty": "medium",
            "split": "iterate",
            "provenance": {source, src, op},
        }

    Mutants whose ``apply_mutation`` result does not parse are silently skipped
    (syntax-invalids are useless as benchmark items).  The mutant's full source
    is materialised under ``bench/corpus/mutants/sources/<id>/<file>`` relative
    to the repo root detected from *src_path*.
    """
    with open(src_path, encoding="utf-8") as fh:
        source = fh.read()

    mutations = find_mutations(source)

    # Deduplicate: one mutation per (line, op) — avoid producing two almost-
    # identical items when the same operator appears twice on a line.
    seen_line_op: set[tuple[int, str]] = set()
    unique: list[dict] = []
    for m in mutations:
        key = (m["line"], m["op"])
        if key not in seen_line_op:
            seen_line_op.add(key)
            unique.append(m)

    if max_per_file is not None:
        unique = unique[:max_per_file]

    # Determine repo root and relative path
    abs_src = os.path.abspath(src_path)
    # Walk up until we find a directory containing bench/ or owlex/ at its root
    repo_root = _find_repo_root(abs_src)
    rel_src = os.path.relpath(abs_src, repo_root) if repo_root else os.path.basename(abs_src)
    basename = os.path.basename(src_path).replace(".py", "")

    items: list[dict] = []
    for m in unique:
        mutated = apply_mutation(source, m)

        # Syntactic validity guard
        try:
            ast.parse(mutated)
        except SyntaxError:
            continue

        op = m["op"]
        line = m["line"]
        orig = m["original_token"]
        mut_tok = m["mutated_token"]
        item_id = f"mut-{basename}-{line}-{op}"

        # Build a minimal unified diff (original → mutated) with standard headers
        # so the existing raw_diff runner and added_lines_by_file parser can read it.
        diff = "".join(difflib.unified_diff(
            source.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile=f"a/{rel_src}",
            tofile=f"b/{rel_src}",
            n=3,
        ))

        # Re-anchor bug line via the diff parser so it is exactly consistent with
        # how the scorer reads the diff (the + line carrying the mutated token).
        added = _added_lines_by_file(diff)
        file_added = added.get(rel_src, {})
        if file_added:
            # For a single-token change the diff has exactly one added line;
            # take the smallest line number among the added lines for that file.
            line = min(file_added)

        # Materialize source file if repo root is known
        if repo_root:
            dest_dir = os.path.join(
                repo_root, "bench", "corpus", "mutants", "sources", item_id,
                os.path.dirname(rel_src),
            )
            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(
                repo_root, "bench", "corpus", "mutants", "sources", item_id, rel_src,
            )
            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            with open(dest_file, "w", encoding="utf-8") as fh:
                fh.write(mutated)

        # Build description phrased as a defect (not a neutral label).
        desc_tmpl = _OP_META[op][1]
        mutation_label = desc_tmpl.format(orig=orig, mut=mut_tok, orig_n=orig)
        description = f"mutation {op}: introduces {mut_tok} in place of {orig} ({mutation_label})"

        item: dict = {
            "id": item_id,
            "file": rel_src,
            "diff": diff,
            "mutated_source": mutated,
            "bugs": [
                {
                    "bug_type": m["bug_type"],
                    "file": rel_src,
                    "line": line,
                    "description": description,
                }
            ],
            "source": "mutant",
            "lang": "python",
            "diff_size": "S",
            "risk_domain": "mutation",
            "difficulty": "medium",
            "split": "iterate",
            "provenance": {
                "source": "ast-mutation",
                "src": src_path,
                "op": op,
            },
        }
        items.append(item)

    return items


def _find_repo_root(abs_path: str) -> str | None:
    """Walk up from *abs_path* to find the repo root (contains .git or pyproject.toml)."""
    directory = os.path.dirname(abs_path)
    while True:
        if os.path.exists(os.path.join(directory, ".git")):
            return directory
        if os.path.exists(os.path.join(directory, "pyproject.toml")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate mutation-based corpus items from known-good owlex modules.",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=["owlex/agreement.py", "owlex/roles.py", "owlex/prompts.py"],
        metavar="FILE",
        help="Source files to mutate (default: owlex/agreement.py owlex/roles.py owlex/prompts.py)",
    )
    parser.add_argument(
        "--out",
        default="bench/corpus/mutants/manifest.json",
        metavar="PATH",
        help="Output manifest JSON path",
    )
    parser.add_argument(
        "--max-per-file",
        type=int,
        default=8,
        metavar="N",
        help="Maximum mutants per source file (default: 8)",
    )
    args = parser.parse_args()

    all_items: list[dict] = []
    per_file: dict[str, int] = {}
    for target in args.targets:
        if not os.path.exists(target):
            print(f"[WARN] {target}: not found, skipping")
            continue
        items = generate_mutants(target, max_per_file=args.max_per_file)
        per_file[target] = len(items)
        all_items.extend(items)

    manifest: dict = {
        "schema_version": 1,
        "kind": "mutants",
        "description": (
            "Auto-generated mutation corpus. Each item is one behavior-changing edit "
            "to a known-good owlex module; the mutated line number is the objective "
            "ground-truth bug location — no LLM labeling, no hand annotation."
        ),
        "items": all_items,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    for target, count in per_file.items():
        print(f"  {target}: {count} mutants")
    print(f"  total: {len(all_items)} mutants → {args.out}")


if __name__ == "__main__":
    main()
