"""Ingest real Python bugs from the BugsInPy dataset into the benchmark corpus.

Produces a manifest shaped identically to the mined/mutants manifests so it
slots straight into ``load_corpus(include=(..., "dataset"))``.

Usage::

    python bench/ingest_bugsinpy.py \\
        --out bench/corpus/dataset/manifest.json \\
        --max-per-project 5 \\
        --projects black fastapi tqdm tornado luigi

BugsInPy layout: ``projects/<project>/bugs/<bug_id>/bug_patch.txt`` (unified
diff that FIXES the bug → changed lines are the bug location, exactly like a
fix-commit) plus ``bug.info`` (metadata).  The patch file alone is enough to
label location + type; the project source is NOT vendored.

All stdlib, no network in unit tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so ``bench.corpus`` is importable whether
# this module is run directly (``python bench/ingest_bugsinpy.py``) or as a
# package (``python -m bench.ingest_bugsinpy``).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the shared diff helpers from bench.corpus — no owlex import, pure.
from bench.corpus import added_lines_by_file, reverse_unified_diff  # noqa: E402


# ---------------------------------------------------------------------------
# Clone helper
# ---------------------------------------------------------------------------

_BUGSINPY_URL = "https://github.com/soarsmu/BugsInPy"
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "bugsinpy"


def ensure_clone(cache_dir: str | None = None) -> str:
    """Clone BugsInPy (shallow) into ``cache_dir`` if not already present.

    Returns the local path as a string. Never clones into the owlex repo tree.
    Raises ``RuntimeError`` with a clear message on clone failure.
    """
    root = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
    root.parent.mkdir(parents=True, exist_ok=True)
    if (root / "projects").is_dir():
        return str(root)

    result = subprocess.run(
        ["git", "clone", "--depth", "1", _BUGSINPY_URL, str(root)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"BugsInPy clone failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    if not (root / "projects").is_dir():
        raise RuntimeError(
            f"Clone appeared to succeed but 'projects/' dir is missing in {root}"
        )
    return str(root)


# ---------------------------------------------------------------------------
# Patch parsing
# ---------------------------------------------------------------------------

def _count_changed_lines(diff_text: str) -> int:
    """Count added + removed lines (excluding diff headers)."""
    count = 0
    for line in diff_text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            count += 1
    return count


def _diff_size_label(n: int) -> str:
    """S < 20, M < 80, L >= 80."""
    if n < 20:
        return "S"
    if n < 80:
        return "M"
    return "L"


def _is_test_file(path: str) -> bool:
    """True when a file path looks like a test file."""
    basename = os.path.basename(path)
    return basename.startswith("test_") or basename.endswith("_test.py") or "/tests/" in path or "/test/" in path


def parse_bug_patch(patch_text: str) -> list[dict]:
    """Return ``[{"file": str, "line": int}]`` for each .py file in the patch.

    POLARITY fix: reverses the fix patch before parsing so that the added lines
    in the result represent the *buggy* (pre-fix) code.  Anchors at the first
    added (buggy) line per file.  Non-.py files are skipped.
    PURE — no I/O, no network.
    """
    reversed_patch = reverse_unified_diff(patch_text)
    by_file = added_lines_by_file(reversed_patch)
    results: list[dict] = []
    for path, lines in by_file.items():
        if not path.endswith(".py"):
            continue
        first_line = min(lines) if lines else 1
        results.append({"file": path, "line": first_line})
    return results


# ---------------------------------------------------------------------------
# Ingestion walk
# ---------------------------------------------------------------------------

_DEFAULT_PROJECTS = ["black", "fastapi", "tqdm", "tornado", "luigi"]


def ingest(
    bugsinpy_root: str,
    projects: list[str] | None = None,
    max_per_project: int | None = 5,
) -> list[dict]:
    """Walk ``projects/*/bugs/*/bug_patch.txt`` and emit corpus items.

    Each item shape:

    .. code-block:: json

        {
          "id": "bugsinpy-{project}-{bug_id}",
          "file": "<primary changed .py file>",
          "diff": "<bug_patch text>",
          "bugs": [{"bug_type": "real-bug", "file": ..., "line": ..., "description": "..."}],
          "source": "dataset",
          "lang": "python",
          "diff_size": "S|M|L",
          "risk_domain": "<project>",
          "difficulty": "hard",
          "split": "iterate",
          "provenance": {"source": "BugsInPy", "project": ..., "bug_id": ...}
        }

    Skips patches that touch only test files or only non-.py files.
    Caps per project via ``max_per_project`` to bound corpus size.
    """
    project_list = projects if projects is not None else _DEFAULT_PROJECTS
    root = Path(bugsinpy_root)
    items: list[dict] = []

    for project in project_list:
        bugs_dir = root / "projects" / project / "bugs"
        if not bugs_dir.is_dir():
            continue

        count = 0
        for bug_dir in sorted(bugs_dir.iterdir()):
            if max_per_project is not None and count >= max_per_project:
                break
            bug_id = bug_dir.name
            patch_path = bug_dir / "bug_patch.txt"
            if not patch_path.is_file():
                continue

            patch_text = patch_path.read_text(encoding="utf-8", errors="replace")

            # Parse changed .py files from the fix patch (parse_bug_patch reverses
            # internally so locations point at buggy pre-fix lines)
            py_locations = parse_bug_patch(patch_text)

            # Skip patches that only touch test files or have no .py changes
            non_test = [loc for loc in py_locations if not _is_test_file(loc["file"])]
            if not non_test:
                continue

            primary = non_test[0]
            changed_lines = _count_changed_lines(patch_text)

            # Reverse the patch so the reviewer sees buggy code as added lines
            reversed_patch = reverse_unified_diff(patch_text)

            item_id = f"bugsinpy-{project}-{bug_id}"
            item = {
                "id": item_id,
                "file": primary["file"],
                "diff": reversed_patch,
                "bugs": [
                    {
                        "bug_type": "real-bug",
                        "file": primary["file"],
                        "line": primary["line"],
                        "description": (
                            f"BugsInPy {project} bug #{bug_id} — buggy code before fix"
                        ),
                    }
                ],
                "source": "dataset",
                "lang": "python",
                "diff_size": _diff_size_label(changed_lines),
                "risk_domain": project,
                "difficulty": "hard",
                "split": "iterate",
                "provenance": {
                    "source": "BugsInPy",
                    "project": project,
                    "bug_id": bug_id,
                },
            }
            items.append(item)
            count += 1

    return items


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest real Python bugs from BugsInPy into the benchmark corpus. "
            "Clones the metadata repo (shallow) on first run."
        )
    )
    parser.add_argument(
        "--out",
        default="bench/corpus/dataset/manifest.json",
        help="Output manifest path (default: bench/corpus/dataset/manifest.json)",
    )
    parser.add_argument(
        "--max-per-project",
        type=int,
        default=5,
        metavar="N",
        help="Cap items per project (default: 5, None=unlimited)",
    )
    parser.add_argument(
        "--projects",
        nargs="*",
        default=_DEFAULT_PROJECTS,
        metavar="PROJECT",
        help="BugsInPy project names to include (default: black fastapi tqdm tornado luigi)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        metavar="DIR",
        help="Local cache dir for BugsInPy clone (default: ~/.cache/bugsinpy)",
    )
    args = parser.parse_args()

    print(f"Ensuring BugsInPy clone ... ", end="", flush=True)
    try:
        bugsinpy_root = ensure_clone(cache_dir=args.cache_dir)
        print(f"ok ({bugsinpy_root})")
    except RuntimeError as exc:
        print(f"FAILED\n{exc}")
        raise SystemExit(1) from exc

    items = ingest(
        bugsinpy_root,
        projects=args.projects,
        max_per_project=args.max_per_project,
    )

    manifest = {
        "schema_version": 1,
        "kind": "dataset",
        "description": (
            "BugsInPy corpus: real fixed Python bugs with objective fix-location labels. "
            "Generated by bench/ingest_bugsinpy.py from https://github.com/soarsmu/BugsInPy."
        ),
        "items": items,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    counts: dict[str, int] = {}
    for item in items:
        project = item["provenance"]["project"]
        counts[project] = counts.get(project, 0) + 1

    print(f"Dataset corpus written to {out_path}")
    for project, n in sorted(counts.items()):
        print(f"  {project:<20} {n}")
    print(f"  {'total':<20} {len(items)}")


if __name__ == "__main__":
    main()
