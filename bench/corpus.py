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
_HUNK_FULL_RE = re.compile(
    r"^(@@ -)(\d+)((?:,\d+)?)( \+)(\d+)((?:,\d+)?)( @@)(.*)"
)


def reverse_unified_diff(diff_text: str) -> str:
    """Reverse a unified diff so that removed and added lines swap roles.

    The result presents the pre-fix (buggy) code as ``+`` (added) lines and the
    post-fix (corrected) code as ``-`` (removed) lines.  This is the POLARITY
    fix: feeding the reversed diff to ``added_lines_by_file`` then yields the
    *buggy* line positions rather than the corrected ones.

    Transform rules:
    - ``--- a/PATH`` / ``+++ b/PATH`` header pair: swap them so the old path
      becomes the new ``+++`` path (and vice-versa).  ``/dev/null`` is handled
      correctly (new-file becomes deleted-file and vice-versa).
    - ``diff --git``, ``index``, ``similarity index``, mode lines: pass through
      unchanged.
    - Hunk header ``@@ -a,b +c,d @@[ ctx]`` → ``@@ -c,d +a,b @@[ ctx]``.
      The single-number short form ``@@ -a +c @@`` is also handled.
    - Body lines: leading ``+`` → ``-``, leading ``-`` → ``+``.
    - Context lines (leading space) and ``\\ No newline at end of file`` pass
      through unchanged.
    """
    lines = diff_text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # --- / +++ header pair (or +++ / --- after a previous reversal): consume
        # both and emit swapped.  We detect a header pair as any line starting
        # with "--- " or "+++ " that is immediately followed by its counterpart.
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            out.append("+++ " + line[4:])
            out.append("--- " + lines[i + 1][4:])
            i += 2
            continue
        if line.startswith("+++ ") and i + 1 < len(lines) and lines[i + 1].startswith("--- "):
            out.append("--- " + line[4:])
            out.append("+++ " + lines[i + 1][4:])
            i += 2
            continue

        # Hunk header: swap old/new ranges
        m = _HUNK_FULL_RE.match(line)
        if m:
            # Groups: 1="-", 2=old_start, 3=old_count_with_comma, 4=" +",
            #         5=new_start, 6=new_count_with_comma, 7=" @@", 8=trailing_ctx
            out.append(
                f"@@ -{m.group(5)}{m.group(6)} +{m.group(2)}{m.group(3)} @@{m.group(8)}"
            )
            i += 1
            continue

        # Body lines: swap + and -
        if line.startswith("+") and not line.startswith("+++"):
            out.append("-" + line[1:])
            i += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            out.append("+" + line[1:])
            i += 1
            continue

        # Everything else (context, git headers, index lines, mode lines,
        # "\ No newline at end of file"): pass through unchanged
        out.append(line)
        i += 1

    return "\n".join(out)


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
            if path.startswith("b/") or path.startswith("a/"):
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


def load_mined(path: str) -> list[dict]:
    """Load mined manifest items (source=real-fix / decoy). Returns [] if absent."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest.get("items", [])


def load_dataset(path: str) -> list[dict]:
    """Load BugsInPy dataset manifest items (source=dataset). Returns [] if absent."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest.get("items", [])


def load_mutants(path: str) -> list[dict]:
    """Load mutants manifest items, inlining post_image from sources/<id>/.

    Mirrors the seeded ``post_image_files`` pattern: if the manifest item has
    a ``post_image_dir`` the full tree is read; otherwise the diff is
    reconstructed.  Returns [] if the manifest is absent.
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for item in manifest.get("items", []):
        item["post_image"] = post_image_files(item, base)
    return manifest.get("items", [])


def load_db_targets(path: str) -> list[dict]:
    """Load DB-targets (kind=targets, UNLABELED). Returns [] if absent."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest.get("items", [])


def load_db_labeled(path: str) -> list[dict]:
    """Load DB labeled items (kind=db-labeled, source=db-llm-label). Returns [] if absent."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest.get("items", [])


def load_corpus(
    root: str = "bench/corpus",
    include: tuple[str, ...] = ("seeded", "mined", "mutants", "dataset", "db_labeled"),
) -> list[dict]:
    """Unified flat corpus loader across all sources.

    Loads each requested source, tags every item with its ``source`` field
    (if not already set), and returns a single flat list.  Missing optional
    manifests are silently skipped — the seeded manifest is the only required
    one.

    ``include`` selects which sources to load:
      ``"seeded"``     → ``corpus/seeded/manifest.json``
      ``"mined"``      → ``corpus/mined/manifest.json``
      ``"mutants"``    → ``corpus/mutants/manifest.json``
      ``"db_labeled"`` → ``corpus/db/labeled.json``
      ``"dataset"``    → ``corpus/dataset/manifest.json``
    """
    items: list[dict] = []

    if "seeded" in include:
        manifest_path = os.path.join(root, "seeded", "manifest.json")
        manifest = load_seeded(manifest_path)
        for item in manifest.get("items", []):
            item.setdefault("source", "seeded")
            items.append(item)

    if "mined" in include:
        for item in load_mined(os.path.join(root, "mined", "manifest.json")):
            item.setdefault("source", "real-fix")
            items.append(item)

    if "mutants" in include:
        for item in load_mutants(os.path.join(root, "mutants", "manifest.json")):
            item.setdefault("source", "mutant")
            items.append(item)

    if "db_labeled" in include:
        for item in load_db_labeled(os.path.join(root, "db", "labeled.json")):
            item.setdefault("source", "db-llm-label")
            items.append(item)

    if "dataset" in include:
        for item in load_dataset(os.path.join(root, "dataset", "manifest.json")):
            item.setdefault("source", "dataset")
            items.append(item)

    return items
