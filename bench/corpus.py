"""Corpus loading + unified-diff parsing — pure, no owlex import, no live calls.

Shared by the runner (to inline diff text) and the corpus-integrity test (to
verify every labeled bug/decoy line points at a real added line). Kept separate
from ``run.py`` so tests can import it without pulling ``owlex.second_opinion``.
"""
from __future__ import annotations

import json
import os
import re
import glob


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines_by_file(diff_text: str) -> dict[str, dict[int, str]]:
    """Map each file in a unified diff to ``{new_line_number: added_text}``.

    Tracks the new-file line counter from each ``@@ ... +c,d @@`` header and
    advances it on added (``+``) and context (` `) lines, mirroring how a
    reviewer reading the diff would number post-image lines. ``-`` (removed)
    lines do not advance the new counter.
    """
    out: dict[str, dict[int, str]] = {}
    current: str | None = None
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current = None if path == "/dev/null" else path
            if current is not None:
                out.setdefault(current, {})
            continue
        if line.startswith("--- "):
            continue
        m = _HUNK_RE.match(line)
        if m:
            new_lineno = int(m.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            out[current][new_lineno] = line[1:]
            new_lineno += 1
        elif line.startswith("-"):
            continue
        elif line.startswith("\\"):  # "\ No newline at end of file"
            continue
        else:  # context line (leading space) or blank
            new_lineno += 1
    return out


def reconstruct_post_image(diff_text: str) -> dict[str, str]:
    """Rebuild each file's post-image text from an all-added unified diff.

    Used to materialize a synthetic seeded diff into real on-disk files so the
    reviewer can be given repo read access (faithful to production, where the
    prose-mode call has the repo available). Handles the new-file / all-added
    shape the seeded corpus uses; joins added lines in line-number order.
    """
    out: dict[str, str] = {}
    for path, lines in added_lines_by_file(diff_text).items():
        body = "\n".join(lines[n] for n in sorted(lines))
        out[path] = body + "\n" if body else ""
    return out


def post_image_files(item: dict, base_dir: str) -> dict[str, str]:
    """Full on-disk post-image for an item, to be materialized for the reviewer.

    ``post_image_dir`` (a committed tree of the full changed files) wins — used
    by large modified-file items where the diff is only a narrow hunk of a big
    file, so the diff alone can't rebuild the whole file. Falls back to
    reconstructing from an all-added new-file diff (the small-item default).
    """
    rel = item.get("post_image_dir")
    if not rel:
        return reconstruct_post_image(item.get("diff", ""))
    root = os.path.join(base_dir, rel)
    out: dict[str, str] = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            full = os.path.join(dirpath, n)
            out[os.path.relpath(full, root)] = open(full, encoding="utf-8").read()
    return out


def load_seeded(manifest_path: str) -> dict:
    """Load the manifest, inlining each item's ``diff`` text and ``post_image``."""
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    base = os.path.dirname(os.path.abspath(manifest_path))
    for item in manifest.get("items", []):
        diff_path = item.get("diff_path")
        if diff_path:
            with open(os.path.join(base, diff_path), encoding="utf-8") as df:
                item["diff"] = df.read()
        item["post_image"] = post_image_files(item, base)
    return manifest


def load_real(real_dir: str) -> list[dict]:
    """Load real-corpus diffs (unlabeled) as ``[{id, diff}]`` sorted by id."""
    items = []
    for path in sorted(glob.glob(os.path.join(real_dir, "*.diff"))):
        with open(path, encoding="utf-8") as f:
            items.append({"id": os.path.basename(path)[:-5], "diff": f.read()})
    return items
