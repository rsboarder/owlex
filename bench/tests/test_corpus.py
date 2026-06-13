"""Integrity tests for the committed seeded corpus.

Catches hand-authored drift: a manifest that fails the AUDIT-0 contract, a
missing diff file, or a bug/decoy 'line' that doesn't point at a real added
line in that file's diff. These run on the real committed files (deterministic,
no live codex).
"""
from __future__ import annotations

import os

from bench import corpus, scorer
from bench.corpus import reverse_unified_diff


SEEDED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "corpus", "seeded")
MANIFEST = os.path.join(SEEDED_DIR, "manifest.json")


def test_real_manifest_satisfies_contract():
    manifest = corpus.load_seeded(MANIFEST)
    assert scorer.validate_manifest(manifest) == []


def test_every_diff_file_exists_and_is_nonempty():
    manifest = corpus.load_seeded(MANIFEST)
    for item in manifest["items"]:
        assert item["diff"].strip(), f"{item['id']} diff is empty"


def test_diff_parser_on_new_file_hunk():
    diff = (
        "diff --git a/x.py b/x.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/x.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    added = corpus.added_lines_by_file(diff)
    assert added == {"x.py": {1: "line1", 2: "line2", 3: "line3"}}


def test_every_labeled_line_points_at_a_real_added_line():
    """The load-bearing corpus check: each bug/decoy file:line must resolve to
    an actually-added line in that item's diff (else recall scoring is bogus)."""
    manifest = corpus.load_seeded(MANIFEST)
    for item in manifest["items"]:
        added = corpus.added_lines_by_file(item["diff"])
        for kind in ("bugs", "decoys"):
            for label in item.get(kind, []) or []:
                f, line = label["file"], label["line"]
                assert f in added, f"{item['id']}: {kind} file {f} not in diff (have {list(added)})"
                assert line in added[f], (
                    f"{item['id']}: {kind} {f}:{line} not an added line "
                    f"(added lines: {sorted(added[f])})"
                )


def test_labeled_lines_exist_in_post_image_files():
    """Every bug/decoy line must be a real line in the materialized file — covers
    large modified-file items whose diff is only a hunk of the full file."""
    manifest = corpus.load_seeded(MANIFEST)
    for item in manifest["items"]:
        post = item["post_image"]
        for kind in ("bugs", "decoys"):
            for label in item.get(kind, []) or []:
                f, line = label["file"], label["line"]
                assert f in post, f"{item['id']}: {f} not in post_image ({list(post)})"
                nlines = post[f].count("\n")
                assert 1 <= line <= nlines, (
                    f"{item['id']}: {f}:{line} out of range (file has {nlines} lines)"
                )


def test_large_item_diff_is_a_narrow_hunk_of_a_big_file():
    """The AUDIT-2 large-diff probe: the raw diff must be much smaller than the
    full materialized file, else there's no raw-focuses-vs-prose-hunts asymmetry."""
    manifest = corpus.load_seeded(MANIFEST)
    seed07 = next(i for i in manifest["items"] if i["id"] == "seed-07-large-council-config")
    added = corpus.added_lines_by_file(seed07["diff"])["owlex/council_config.py"]
    full_lines = seed07["post_image"]["owlex/council_config.py"].count("\n")
    assert full_lines >= 50                 # the file is genuinely large
    assert len(added) <= full_lines // 2    # the hunk is a small slice of it


def test_reconstruct_post_image_rebuilds_files():
    manifest = corpus.load_seeded(MANIFEST)
    seed01 = next(i for i in manifest["items"] if i["id"] == "seed-01-subprocess-leak")
    files = corpus.reconstruct_post_image(seed01["diff"])
    assert set(files) == {"owlex/probe.py", "owlex/probe_io.py"}
    assert files["owlex/probe.py"].count("\n") == 14  # 14 added content lines
    assert 'return False, "probe timed out"' in files["owlex/probe.py"]
    assert 'errors="replace"' in files["owlex/probe_io.py"]


def test_decoys_are_not_also_labeled_bugs():
    manifest = corpus.load_seeded(MANIFEST)
    for item in manifest["items"]:
        bug_sites = {(b["file"], b["line"]) for b in item.get("bugs", [])}
        decoy_sites = {(d["file"], d["line"]) for d in item.get("decoys", []) or []}
        assert not (bug_sites & decoy_sites), f"{item['id']}: a decoy collides with a bug"


# ---------------------------------------------------------------------------
# reverse_unified_diff
# ---------------------------------------------------------------------------

_SAMPLE_FORWARD = (
    "diff --git a/foo.py b/foo.py\n"
    "index abc..def 100644\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -10,4 +10,4 @@ class Foo:\n"
    " context_before\n"
    "-old_line\n"
    "+new_line\n"
    " context_after\n"
)

_SAMPLE_REVERSED = (
    "diff --git a/foo.py b/foo.py\n"
    "index abc..def 100644\n"
    "+++ a/foo.py\n"
    "--- b/foo.py\n"
    "@@ -10,4 +10,4 @@ class Foo:\n"
    " context_before\n"
    "+old_line\n"
    "-new_line\n"
    " context_after\n"
)


def test_reverse_unified_diff_swaps_added_and_removed():
    """Reversing a -old/+new hunk yields +old/-new with swapped hunk ranges."""
    result = reverse_unified_diff(_SAMPLE_FORWARD)
    assert result == _SAMPLE_REVERSED


def test_reverse_unified_diff_round_trip():
    """reverse_unified_diff(reverse_unified_diff(d)) == d."""
    assert reverse_unified_diff(reverse_unified_diff(_SAMPLE_FORWARD)) == _SAMPLE_FORWARD


def test_reverse_unified_diff_round_trip_asymmetric_hunk():
    """Round-trip holds when old/new line counts differ."""
    diff = (
        "--- a/bar.py\n"
        "+++ b/bar.py\n"
        "@@ -5,3 +5,5 @@\n"
        " ctx\n"
        "-removed_a\n"
        "-removed_b\n"
        "+added_x\n"
        "+added_y\n"
        "+added_z\n"
        " ctx2\n"
    )
    assert reverse_unified_diff(reverse_unified_diff(diff)) == diff


def test_reverse_unified_diff_new_file_becomes_deleted():
    """A new-file diff (--- /dev/null) reverses to a deleted-file diff."""
    diff = (
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+line1\n"
        "+line2\n"
    )
    rev = reverse_unified_diff(diff)
    assert "+++ /dev/null" in rev
    assert "--- b/new.py" in rev
    # The formerly added lines should now be removed
    assert "-line1\n" in rev
    assert "-line2\n" in rev
    # Round-trip
    assert reverse_unified_diff(rev) == diff


def test_reverse_unified_diff_added_lines_point_at_buggy_code():
    """After reversal, added_lines_by_file returns the OLD (buggy) line content."""
    diff = (
        "--- a/owlex/engine.py\n"
        "+++ b/owlex/engine.py\n"
        "@@ -100,3 +100,3 @@\n"
        " ctx\n"
        "-buggy_call()\n"
        "+fixed_call()\n"
        " ctx2\n"
    )
    rev = reverse_unified_diff(diff)
    added = corpus.added_lines_by_file(rev)
    # After reversal the +++ path becomes the old one (a/owlex/engine.py stripped
    # of a/ prefix = owlex/engine.py), and the added line is the buggy one.
    assert "owlex/engine.py" in added
    lines = added["owlex/engine.py"]
    assert any("buggy_call()" in v for v in lines.values())
    assert not any("fixed_call()" in v for v in lines.values())
