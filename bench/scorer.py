"""Pure, deterministic scoring for the AUDIT-0 harness.

No live codex calls, no I/O — every function here is a pure transform so
``pytest bench/`` is fast and reproducible. The runner (``bench/run.py``) does
the non-deterministic live calls and feeds their parsed output through here.

Vocabulary:
- **finding**  ``{"file", "line", "snippet"}`` — one cited issue a reviewer
  produced (line may be ``None`` when the reviewer named a file but no line).
- **bug**      ``{"bug_type", "file", "line", "description"}`` — a planted
  ground-truth defect from the seeded manifest.
- **decoy**    ``{"file", "line", "description"}`` — a planted plausible-but-not
  -a-bug change; a finding that matches a decoy is a false positive we can
  attribute, so precision degradation is interpretable rather than mysterious.

Matching is ``file`` (basename / suffix) + ``line ± line_window`` — LLM line
citations drift by a few lines, so exact-match would read recall ≈ 0.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics


# A file:line citation: a path ending in `.ext`, then `:<line>`.
CITATION_RE = re.compile(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+):(\d+)")

DEFAULT_LINE_WINDOW = 3


def parse_findings(text: str) -> list[dict]:
    """Extract ``{file, line, snippet}`` findings from reviewer prose.

    Pulls every ``path/to/file.py:123`` citation, de-duplicated by
    ``(basename, line)`` so a reviewer that mentions the same site twice is one
    finding. ``snippet`` is the surrounding text line, kept for human triage.
    """
    findings: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in CITATION_RE.finditer(text or ""):
        file, line = m.group(1), int(m.group(2))
        key = (os.path.basename(file), line)
        if key in seen:
            continue
        seen.add(key)
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.end())
        snippet = text[start : end if end != -1 else len(text)].strip()
        findings.append({"file": file, "line": line, "snippet": snippet})
    return findings


def _same_file(a: str, b: str) -> bool:
    """True when two cited paths plausibly name the same file.

    Codex may cite a bare basename, a repo-relative path, or an absolute one;
    match on basename equality or a path-suffix relationship.
    """
    ba, bb = os.path.basename(a), os.path.basename(b)
    if ba == bb:
        return True
    return a.endswith(b) or b.endswith(a)


def _matches(finding: dict, target: dict, line_window: int, granularity: str = "line") -> bool:
    """True when ``finding`` lands on ``target`` (a bug or decoy).

    ``granularity="line"`` → file match AND line within ``± line_window`` (the
    strict metric raw-diff input is measured at — it can anchor lines).
    ``granularity="file"`` → file match only; the fair yardstick for *prose*
    input, which carries no line numbers so could never satisfy file:line.
    """
    if not _same_file(finding["file"], target["file"]):
        return False
    if granularity == "file":
        return True
    fl = finding.get("line")
    if fl is None:
        return False
    return abs(fl - int(target["line"])) <= line_window


def _dedup(findings: list[dict]) -> list[dict]:
    uniq: list[dict] = []
    seen: set[tuple[str, object]] = set()
    for f in findings:
        key = (os.path.basename(f["file"]), f.get("line"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return uniq


# --- AUDIT-1: mechanical citation verification ---------------------------

def _resolve_file(path: str, post_image: dict[str, str]) -> tuple[str | None, str | None]:
    """Find the post-image file a cited path names (basename / suffix match)."""
    for p, content in post_image.items():
        if _same_file(path, p):
            return p, content
    return None, None


def verify_findings(
    findings: list[dict],
    post_image: dict[str, str],
    *,
    line_window: int = DEFAULT_LINE_WINDOW,
) -> dict:
    """Citation-check findings against the materialized post-image (AUDIT-1).

    The scriptable analog of SKILL.md Phase 2's mechanical check: a finding
    survives only if its cited ``file:line`` resolves to a location that can
    actually be opened — the file exists in the post-image AND the line is
    within it. A hallucinated file or an out-of-range line cannot be read, so it
    is dropped rather than counted as a (false-positive) finding.

    Line resolution carries the same ``± line_window`` EOF tolerance the matcher
    uses: a citation a few lines past the end is LLM line-drift on a real file,
    not a hallucination, so it still resolves (this also guarantees a true
    positive cited near the file's end is never dropped). A line-less finding
    (``line is None``) resolves at the file level.

    Returns ``{"kept": [...], "dropped": [{"finding": f, "reason": <str>}]}``;
    reasons are ``file_unresolved`` / ``line_out_of_range``. This drops only
    citation-unresolvable findings — never a finding that lands on a planted bug
    — so verified-set precision is ≥ raw-set precision by construction.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for f in findings:
        _, content = _resolve_file(f["file"], post_image)
        if content is None:
            dropped.append({"finding": f, "reason": "file_unresolved"})
            continue
        line = f.get("line")
        if line is not None:
            nlines = content.count("\n")
            if not (1 <= line <= nlines + line_window):
                dropped.append({"finding": f, "reason": "line_out_of_range"})
                continue
        kept.append(f)
    return {"kept": kept, "dropped": dropped}


def _drop_stats(item_drops: list[list[dict]]) -> dict:
    """Summarize per-run drops for one item: total, per-run counts, by reason."""
    per_run = [len(d) for d in item_drops]
    by_reason: dict[str, int] = {}
    for d in item_drops:
        for x in d:
            by_reason[x["reason"]] = by_reason.get(x["reason"], 0) + 1
    return {"total": sum(per_run), "per_run": per_run, "by_reason": by_reason}


def score_run(
    findings: list[dict],
    bugs: list[dict],
    decoys: list[dict] | None = None,
    *,
    line_window: int = DEFAULT_LINE_WINDOW,
    granularity: str = "line",
) -> dict:
    """Score one reviewer run for one corpus item against ground truth.

    - **recall** = bugs matched by ≥1 finding / total bugs.
    - **precision** = true-positive findings / total findings; ``None`` when the
      reviewer produced no findings (precision is undefined, not zero — kept
      distinct so the aggregate can skip empty runs honestly).
    - **decoy_hits** = findings that matched a planted decoy (attributable FPs).

    A seeded diff is clean except for its planted bugs+decoys, so any finding
    that matches neither is a hallucination — also counted as a false positive.
    """
    decoys = decoys or []
    uniq = _dedup(findings)

    matched_bugs: set[int] = set()
    tp = 0
    fp = 0
    decoy_hits = 0
    for f in uniq:
        hit = next(
            (i for i, b in enumerate(bugs) if _matches(f, b, line_window, granularity)), None
        )
        if hit is not None:
            tp += 1
            matched_bugs.add(hit)
        else:
            fp += 1
            if any(_matches(f, d, line_window, granularity) for d in decoys):
                decoy_hits += 1

    n_findings = len(uniq)
    bugs_total = len(bugs)
    bugs_found = len(matched_bugs)
    return {
        "n_findings": n_findings,
        "tp": tp,
        "fp": fp,
        "bugs_total": bugs_total,
        "bugs_found": bugs_found,
        "decoy_hits": decoy_hits,
        "precision": (tp / n_findings) if n_findings else None,
        "recall": (bugs_found / bugs_total) if bugs_total else None,
        "detected_any": bugs_found > 0,
    }


def meanstdev(vals: list) -> dict:
    """mean ± stdev over non-``None`` values; sample stdev, 0.0 for n<2."""
    clean = [v for v in vals if v is not None]
    if not clean:
        return {"mean": None, "stdev": 0.0, "n": 0}
    return {
        "mean": statistics.fmean(clean),
        "stdev": statistics.stdev(clean) if len(clean) > 1 else 0.0,
        "n": len(clean),
    }


def aggregate(run_scores: list[dict]) -> dict:
    """Aggregate K run-scores into mean ± stdev — the variance the audit needs.

    A single before/after run is not a benchmark (LLM judges are
    non-deterministic); K≥5 with stdev is the contract.
    """
    return {
        "runs": len(run_scores),
        "precision": meanstdev([r["precision"] for r in run_scores]),
        "recall": meanstdev([r["recall"] for r in run_scores]),
        "detection_rate": meanstdev(
            [1.0 if r["detected_any"] else 0.0 for r in run_scores]
        ),
        "decoy_hits": meanstdev([r["decoy_hits"] for r in run_scores]),
        "n_findings": meanstdev([r["n_findings"] for r in run_scores]),
    }


def score_item(
    item: dict,
    runs: list[list[dict]],
    *,
    line_window: int = DEFAULT_LINE_WINDOW,
    granularity: str = "line",
    verify: bool = False,
) -> dict:
    """Score one corpus item across its K runs (``runs`` = K finding-lists).

    With ``verify=True`` each run's findings are first citation-checked against
    the item's ``post_image`` (AUDIT-1: drop findings whose ``file:line`` does
    not resolve) and the surviving set is scored; an extra ``dropped`` summary
    records what verification removed.
    """
    bugs = item.get("bugs", [])
    decoys = item.get("decoys", [])
    eff_runs = runs
    dropped: dict | None = None
    if verify:
        post_image = item.get("post_image") or {}
        verified = [verify_findings(f, post_image, line_window=line_window) for f in runs]
        eff_runs = [v["kept"] for v in verified]
        dropped = _drop_stats([v["dropped"] for v in verified])
    run_scores = [
        score_run(f, bugs, decoys, line_window=line_window, granularity=granularity)
        for f in eff_runs
    ]
    out = {
        "id": item.get("id"),
        "run_scores": run_scores,
        "aggregate": aggregate(run_scores),
    }
    if dropped is not None:
        out["dropped"] = dropped
    return out


def score_corpus(
    items_runs: list[dict],
    *,
    line_window: int = DEFAULT_LINE_WINDOW,
    granularity: str = "line",
    verify: bool = False,
) -> dict:
    """Score a whole seeded corpus.

    ``items_runs`` = ``[{"item": <manifest item>, "runs": [findings, ...]}]``.
    Returns per-item aggregates plus a pooled corpus aggregate (every run of
    every item flattened — equal weight per run, the standard micro-average).
    ``granularity`` selects ``line`` (strict, for raw-diff) vs ``file`` (the
    fair yardstick for line-less prose input).

    With ``verify=True`` (AUDIT-1) each run is citation-checked first and a
    pooled ``corpus_dropped`` summary is added — the before/after of the
    verification pass reads as raw ``corpus_aggregate.precision`` vs the verified
    run's ``corpus_aggregate.precision``.
    """
    per_item = [
        score_item(
            ir["item"], ir["runs"],
            line_window=line_window, granularity=granularity, verify=verify,
        )
        for ir in items_runs
    ]
    pooled = [rs for pi in per_item for rs in pi["run_scores"]]
    out = {
        "granularity": granularity,
        "line_window": line_window,
        "per_item": per_item,
        "corpus_aggregate": aggregate(pooled),
    }
    if verify:
        per_run = [c for pi in per_item for c in pi["dropped"]["per_run"]]
        by_reason: dict[str, int] = {}
        for pi in per_item:
            for reason, n in pi["dropped"]["by_reason"].items():
                by_reason[reason] = by_reason.get(reason, 0) + n
        out["corpus_dropped"] = {
            "total": sum(per_run),
            "by_reason": by_reason,
            "n_dropped_per_run": meanstdev(per_run),
        }
    return out


# --- manifest validation -------------------------------------------------

_BUG_KEYS = {"bug_type", "file", "line", "description"}
_DECOY_KEYS = {"file", "line", "description"}

MIN_BUGS = 10
MIN_BUG_TYPES = 3
MIN_DECOYS = 2

# Vocab for optional stratification fields validated below.
_DIFF_SIZE_VOCAB = {"S", "M", "L"}
_SPLIT_VOCAB = {"iterate", "holdout"}


def validate_manifest(manifest: dict) -> list[str]:
    """Validate a seeded manifest against the AUDIT-0 contract.

    Returns a list of human-readable error strings (empty list = valid). The
    acceptance bar: ≥10 labeled bugs across ≥3 bug_types + ≥2 decoys, every bug
    /decoy carrying a ``file`` + positive integer ``line``.
    """
    errors: list[str] = []
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        return ["manifest.items must be a non-empty list"]

    total_bugs = 0
    total_decoys = 0
    bug_types: set[str] = set()
    seen_ids: set[str] = set()

    for idx, item in enumerate(items):
        where = f"items[{idx}]"
        iid = item.get("id")
        if not isinstance(iid, str) or not iid:
            errors.append(f"{where}.id missing or not a string")
        elif iid in seen_ids:
            errors.append(f"{where}.id duplicate: {iid!r}")
        else:
            seen_ids.add(iid)
        for field in ("file", "diff_path", "prose_summary"):
            if not isinstance(item.get(field), str) or not item.get(field):
                errors.append(f"{where}.{field} missing or empty")

        bugs = item.get("bugs")
        if not isinstance(bugs, list) or not bugs:
            errors.append(f"{where}.bugs must be a non-empty list")
            bugs = []
        for b_idx, bug in enumerate(bugs):
            bw = f"{where}.bugs[{b_idx}]"
            missing = _BUG_KEYS - set(bug)
            if missing:
                errors.append(f"{bw} missing keys: {sorted(missing)}")
            if not isinstance(bug.get("line"), int) or bug.get("line", 0) <= 0:
                errors.append(f"{bw}.line must be a positive integer")
            if isinstance(bug.get("bug_type"), str) and bug["bug_type"]:
                bug_types.add(bug["bug_type"])
            total_bugs += 1

        for d_idx, decoy in enumerate(item.get("decoys", []) or []):
            dw = f"{where}.decoys[{d_idx}]"
            missing = _DECOY_KEYS - set(decoy)
            if missing:
                errors.append(f"{dw} missing keys: {sorted(missing)}")
            if not isinstance(decoy.get("line"), int) or decoy.get("line", 0) <= 0:
                errors.append(f"{dw}.line must be a positive integer")
            total_decoys += 1

        # Optional stratification fields — validate only when present (absent = no error).
        if "diff_size" in item and item["diff_size"] not in _DIFF_SIZE_VOCAB:
            errors.append(
                f"{where}.diff_size {item['diff_size']!r} not in {sorted(_DIFF_SIZE_VOCAB)}"
            )
        if "split" in item and item["split"] not in _SPLIT_VOCAB:
            errors.append(
                f"{where}.split {item['split']!r} not in {sorted(_SPLIT_VOCAB)}"
            )
        for str_field in ("source", "lang", "risk_domain", "difficulty"):
            if str_field in item:
                val = item[str_field]
                if not isinstance(val, str) or not val:
                    errors.append(f"{where}.{str_field} must be a non-empty string when present")

    if total_bugs < MIN_BUGS:
        errors.append(f"need ≥{MIN_BUGS} bugs, found {total_bugs}")
    if len(bug_types) < MIN_BUG_TYPES:
        errors.append(
            f"need ≥{MIN_BUG_TYPES} bug_types, found {len(bug_types)}: {sorted(bug_types)}"
        )
    if total_decoys < MIN_DECOYS:
        errors.append(f"need ≥{MIN_DECOYS} decoys, found {total_decoys}")
    return errors


# --- stratification helpers -----------------------------------------------

# Recommended (NOT enforced) bug_type taxonomy for coverage reporting:
BUG_TYPE_TAXONOMY = (
    "logic",
    "boundary",
    "concurrency",
    "resource",
    "security",
    "api-contract",
)

STRATUM_FIELDS = ("source", "lang", "diff_size", "risk_domain", "difficulty", "split")


def corpus_hash(manifest: dict) -> str:
    """Stable sha256 over each item's identity + ground-truth fields.

    Hashes only ``(id, file, diff_path, bugs, decoys)`` — NOT the inlined
    ``diff``/``post_image`` — so the value is identical before and after
    ``load_seeded()``.  Use this as a freeze/version anchor to detect corpus
    mutations between benchmark runs (anti-p-hacking: a changed hash means the
    experiment is a new experiment).
    """
    stable = [
        {
            "id": item.get("id"),
            "file": item.get("file"),
            "diff_path": item.get("diff_path"),
            "bugs": item.get("bugs"),
            "decoys": item.get("decoys"),
        }
        for item in manifest.get("items", [])
    ]
    serialized = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def stratum_map(manifest: dict, field: str) -> dict[str, str]:
    """Map each item id to its stratum label for ``field``.

    Items missing the field are assigned ``"_unset"`` so every item appears in
    exactly one stratum bucket even before full annotation.
    """
    return {
        item["id"]: str(item.get(field, "_unset"))
        for item in manifest.get("items", [])
        if "id" in item
    }


def corpus_stats(items: list[dict]) -> dict:
    """Corpus-robustness instrument metrics for a flat list of corpus items.

    Pure — no I/O.  Input is the list returned by ``load_corpus()`` or any
    flat mix of corpus items from different sources.

    Returned keys:

    ``total``
        Total item count.

    ``by_source``
        ``{source_value: count}`` — distribution across corpus origins.

    ``by_bug_type``
        ``{bug_type: count}`` — across all ``bugs`` lists in all items.

    ``by_diff_size``
        ``{diff_size: count}`` — items that carry the field; others ignored.

    ``by_split``
        ``{split: count}`` — "iterate" vs "holdout"; items without the field
        mapped to ``"_unset"``.

    ``objective_label_pct``
        Items are *labeled* when they have ≥1 bug.  Among labeled items,
        *objective* means ``source ∈ {seeded, real-fix, mutant}``; *soft*
        means ``source ∈ {db-llm-label, documented-soft}``.
        Returns ``{"labeled": n, "objective": n, "soft": n, "pct_objective": float|None}``.

    ``n_decoys``
        Count of items that have a non-empty ``decoys`` list.

    ``bug_type_coverage``
        ``{"present": [...], "missing": [...]}`` — which of
        ``BUG_TYPE_TAXONOMY`` appear / are absent.

    ``content_hash``
        ``corpus_hash``-style sha256 over ``(id, file, bugs, decoys)`` for
        every item — the freeze/version anchor.
    """
    total = len(items)
    by_source: dict[str, int] = {}
    by_bug_type: dict[str, int] = {}
    by_diff_size: dict[str, int] = {}
    by_split: dict[str, int] = {}
    n_decoys = 0
    labeled = 0
    objective_labeled = 0
    soft_labeled = 0

    _OBJECTIVE_SOURCES = {"seeded", "real-fix", "mutant", "dataset"}
    _SOFT_SOURCES = {"db-llm-label", "documented-soft", "flywheel"}

    for item in items:
        src = item.get("source", "_unset")
        by_source[src] = by_source.get(src, 0) + 1

        bugs = item.get("bugs") or []
        for bug in bugs:
            bt = bug.get("bug_type", "_unset")
            by_bug_type[bt] = by_bug_type.get(bt, 0) + 1

        if bugs:
            labeled += 1
            if src in _OBJECTIVE_SOURCES:
                objective_labeled += 1
            elif src in _SOFT_SOURCES:
                soft_labeled += 1

        ds = item.get("diff_size")
        if ds is not None:
            by_diff_size[ds] = by_diff_size.get(ds, 0) + 1

        split = item.get("split", "_unset")
        by_split[split] = by_split.get(split, 0) + 1

        decoys = item.get("decoys") or []
        if decoys:
            n_decoys += 1

    pct_objective = (objective_labeled / labeled) if labeled else None

    present = [bt for bt in BUG_TYPE_TAXONOMY if bt in by_bug_type]
    missing = [bt for bt in BUG_TYPE_TAXONOMY if bt not in by_bug_type]

    # Content hash: stable over (id, file, bugs, decoys) — mirrors corpus_hash
    # but works on a flat list rather than a manifest dict.
    stable = [
        {
            "id": item.get("id"),
            "file": item.get("file"),
            "bugs": item.get("bugs"),
            "decoys": item.get("decoys"),
        }
        for item in items
    ]
    serialized = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    content_hash = hashlib.sha256(serialized.encode()).hexdigest()

    return {
        "total": total,
        "by_source": by_source,
        "by_bug_type": by_bug_type,
        "by_diff_size": by_diff_size,
        "by_split": by_split,
        "objective_label_pct": {
            "labeled": labeled,
            "objective": objective_labeled,
            "soft": soft_labeled,
            "pct_objective": pct_objective,
        },
        "n_decoys": n_decoys,
        "bug_type_coverage": {"present": present, "missing": missing},
        "content_hash": content_hash,
        "derived_holdout": sum(
            1 for v in derive_holdout(items).values() if v == "holdout"
        ),
    }


def derive_holdout(items: list[dict], holdout_frac: float = 0.2) -> dict[str, str]:
    """Deterministic, stratified held-out assignment — the anti-p-hacking split.

    Held-out membership is a pure function of the item ``id`` (sha256), so it is
    reproducible across runs and CANNOT be hand-tuned to flip an A/B: you cannot
    move an item in or out of the held-out set without changing its id.  Within
    each ``source`` stratum the lowest-hash ``holdout_frac`` share is held out,
    so every source contributes proportionally rather than one source dominating
    the held-out set.  Returns ``{id: "holdout" | "iterate"}``.
    """
    by_source: dict[str, list[tuple[str, str]]] = {}
    for item in items:
        iid = str(item.get("id", ""))
        h = hashlib.sha256(iid.encode()).hexdigest()
        by_source.setdefault(item.get("source", "_unset"), []).append((h, iid))

    out: dict[str, str] = {}
    for rows in by_source.values():
        rows.sort()
        n_hold = int(len(rows) * holdout_frac)
        for i, (_, iid) in enumerate(rows):
            out[iid] = "holdout" if i < n_hold else "iterate"
    return out


def score_by_stratum(per_item: list[dict], strata: dict[str, str]) -> dict:
    """Aggregate ``score_corpus(...)["per_item"]`` results grouped by stratum.

    ``strata`` is the output of ``stratum_map(manifest, field)``.  For each
    item, all its ``run_scores`` are pooled under the item's stratum label; the
    label bucket is then aggregated with the same ``aggregate()`` used corpus-
    wide so the numbers are directly comparable.  Returned dict is sorted by
    label for deterministic output.
    """
    buckets: dict[str, list[dict]] = {}
    for pi in per_item:
        label = strata.get(pi["id"], "_unlabeled")
        buckets.setdefault(label, []).extend(pi["run_scores"])
    return {label: aggregate(buckets[label]) for label in sorted(buckets)}
