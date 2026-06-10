#!/usr/bin/env python3
"""AUDIT-0 benchmark runner — drives an audit sub-step over a corpus K times.

Scope (AUDIT-0): the ``cross_model`` target only — a direct call to
``owlex.second_opinion.get_second_opinion`` (a plain coroutine, importable). The
Opus dimension-judge panel and ``council_ask`` are Claude Code Agent spawns a
standalone script cannot invoke (plan Open Q1); a ``TARGETS`` registry seam is
left for them but they are out of scope here.

For the seeded corpus the runner captures BOTH input variants per item — the
editorialized ``prose`` summary (the "было") and the ``raw_diff`` (the "стало")
— so AUDIT-2's before/after is ready. Real-corpus diffs are unlabeled: cost only.

Cost: ``get_second_opinion`` returns ``(ok, text, timed_out)`` with no token
counts (extracting them would re-plumb the feature under audit), so the cost
proxy is wall-time + reviewer-count; ``tokens`` is recorded as null (plan Open
Q2).

Usage:
    python bench/run.py --help
    python bench/run.py --corpus seeded --target cross_model --runs 5
    python bench/run.py --corpus seeded --runs 1 --dry          # plumbing smoke, no codex
    python bench/run.py --corpus seeded --runs 5 --baseline      # write bench/baselines/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# Support both `python bench/run.py` (script dir on sys.path) and `python -m
# bench.run` / pytest (repo root on sys.path) by ensuring the repo root resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bench import corpus, scorer  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(_HERE, "corpus", "seeded", "manifest.json")
DEFAULT_REAL_DIR = os.path.join(_HERE, "corpus", "real")
BASELINES_DIR = os.path.join(_HERE, "baselines")


# The audit lens handed to the cross-model reviewer. Mirrors solution-audit's
# dimensions; instructs explicit file:line citations so output is parseable.
AUDIT_LENS = (
    "You are reviewing a code change as an independent non-Claude reviewer. "
    "Audit it across these dimensions: (1) correctness / logic errors and edge "
    "cases, (2) error handling (swallowed errors, wrong defaults), (3) resource "
    "safety (leaks, unreaped subprocesses, races), (4) over-engineering / "
    "needless complexity, (5) performance.\n"
    "For EVERY concrete issue you find, cite the exact location as `path:line` "
    "(file path and line number) followed by a one-line description. Only cite "
    "real issues.\n\n"
)


def build_prompt(item: dict, variant: str) -> str:
    """Compose the reviewer prompt for a corpus item under an input variant.

    ``raw_diff`` → the lens + the real unified diff hunks (AUDIT-2 "стало").
    ``prose``    → the lens + the editorialized prose summary (the "было").
    """
    if variant == "prose":
        body = "Summary of the change under review:\n\n" + item.get("prose_summary", "")
    else:
        body = "Unified diff under review:\n\n" + item.get("diff", "")
    return AUDIT_LENS + body


# --- targets -------------------------------------------------------------

async def _acall_cross_model(prompt: str, timeout: int, working_directory: str | None) -> dict:
    """Live cross-model leg: one timed get_second_opinion call.

    Async so the driver can run several concurrently under a semaphore.
    ``working_directory`` is the materialized per-item checkout (faithful to
    production, where the reviewer has repo read access) or ``None`` for the
    real corpus / dry runs.
    """
    from owlex.second_opinion import get_second_opinion, MODEL, REASONING

    start = time.monotonic()
    ok, text, timed_out = await get_second_opinion(
        prompt, working_directory=working_directory, timeout=timeout
    )
    return {
        "ok": ok,
        "text": text,
        "timed_out": timed_out,
        "wall_time_s": round(time.monotonic() - start, 3),
        "reviewer_count": 1,
        "tokens": None,
        "model": MODEL,
        "reasoning": REASONING,
    }


# Async cross-model targets. AUDIT-3 panel / AUDIT-4 council land here (plan
# Open Q1) — not script-callable yet; intentionally absent so --target rejects
# them loudly.
TARGETS = {
    "cross_model": _acall_cross_model,
}


async def _run_jobs_async(target: str, jobs: list[tuple[str, str | None]], *, concurrency: int, timeout: int) -> list[dict]:
    """Run all live reviewer calls with bounded concurrency, preserving order.

    Each job is ``(prompt, working_directory)``.
    """
    fn = TARGETS[target]
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(prompt: str, cwd: str | None) -> dict:
        async with sem:
            return await fn(prompt, timeout, cwd)

    return await asyncio.gather(*(_one(p, c) for p, c in jobs))


def _materialize(item: dict) -> str:
    """Write a seeded item's post-image files into a fresh temp git repo.

    Gives the reviewer real files to read (faithful to production repo access),
    so prose-mode input is measured fairly rather than against an empty sandbox
    where codex refuses to review. Returns the temp dir (caller cleans up).
    """
    d = tempfile.mkdtemp(prefix="owlex-bench-seed-")
    files = item.get("post_image") or corpus.reconstruct_post_image(item["diff"])
    for path, content in files.items():
        full = os.path.join(d, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    # git init + commit so codex's git status/diff path works like a real repo;
    # best-effort (check=False) — codex's find/rg fallback covers a git-less box.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=bench@owlex", "-c", "user.name=bench", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(cmd, cwd=d, env=env, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return d


def _dry_record() -> dict:
    """Deterministic stub for --dry: exercises parse→score→report without codex."""
    return {
        "ok": True,
        "text": "(dry-run) no findings produced",
        "timed_out": False,
        "wall_time_s": 0.0,
        "reviewer_count": 1,
        "tokens": None,
        "model": "(dry)",
        "reasoning": "(dry)",
    }


# --- execution -----------------------------------------------------------

def _attach_findings(rec: dict) -> dict:
    rec["findings"] = scorer.parse_findings(rec["text"]) if rec["ok"] else []
    rec["n_findings"] = len(rec["findings"])
    return rec


def _cost_block(records: list[dict]) -> dict:
    return {
        "wall_time_s": scorer.meanstdev([r["wall_time_s"] for r in records]),
        "reviewer_count": records[0]["reviewer_count"] if records else None,
        "tokens": None,
        "tokens_note": "not captured — get_second_opinion returns no token counts (plan Open Q2)",
    }


def _variant_block(item_records: list[dict], scored: dict | None) -> dict:
    all_records = [r for ir in item_records for r in ir["runs"]]
    block: dict = {
        "cost": _cost_block(all_records),
        "per_item_runs": item_records,
    }
    if scored is not None:
        block["scored"] = scored
    return block


def execute(args: argparse.Namespace) -> dict:
    """Run the benchmark and return the report dict (does not write it)."""
    if args.target not in TARGETS:
        raise SystemExit(
            f"unknown --target {args.target!r}; available: {sorted(TARGETS)} "
            "(panel/council are not script-callable — plan Open Q1)"
        )

    if args.corpus == "seeded":
        manifest = corpus.load_seeded(args.manifest)
        items = manifest["items"]
        line_window = args.line_window or manifest.get("line_window_default", scorer.DEFAULT_LINE_WINDOW)
        if args.input_variant == "both":
            variants = ["raw_diff", "prose"]
        else:
            variants = [args.input_variant]
    else:  # real: unlabeled, raw diff only, no scoring
        items = corpus.load_real(args.real_dir)
        line_window = args.line_window or scorer.DEFAULT_LINE_WINDOW
        variants = ["raw_diff"]

    # Materialize each seeded item into a temp git repo so the reviewer has real
    # files to read (faithful to production repo access — both input variants get
    # the same file access; only the prompt differs). Skipped for dry / real.
    workdirs: dict[str, str] = {}
    if args.corpus == "seeded" and not args.dry:
        workdirs = {item["id"]: _materialize(item) for item in items}

    try:
        # Flatten every (variant, item, run) into one job list so live calls can
        # run with bounded concurrency, then regroup (order preserved by gather).
        jobs: list[tuple[str, str | None]] = []
        for variant in variants:
            for item in items:
                prompt = build_prompt(item, variant)
                cwd = workdirs.get(item.get("id"))
                for _ in range(args.runs):
                    jobs.append((prompt, cwd))

        if args.dry:
            records = [_attach_findings(_dry_record()) for _ in jobs]
        else:
            raw = asyncio.run(
                _run_jobs_async(args.target, jobs, concurrency=args.concurrency, timeout=args.timeout)
            )
            records = [_attach_findings(r) for r in raw]
    finally:
        for d in workdirs.values():
            shutil.rmtree(d, ignore_errors=True)

    # Regroup records back into variant → item → runs (chunks of args.runs).
    results: dict = {}
    cursor = 0
    for variant in variants:
        item_records = []
        scored_items = []
        for item in items:
            runs = records[cursor : cursor + args.runs]
            cursor += args.runs
            item_records.append({"id": item.get("id"), "variant": variant, "runs": runs})
            if args.corpus == "seeded":
                scored_items.append({"item": item, "runs": [r["findings"] for r in runs]})

        # Score at both granularities from the SAME captured findings (pure, no
        # extra codex): `line` is the strict raw-diff metric; `file` is the fair
        # yardstick for line-less prose (AUDIT-2's apples-to-apples comparison).
        scored = None
        if args.corpus == "seeded":
            scored = {
                "line": scorer.score_corpus(scored_items, line_window=line_window, granularity="line"),
                "file": scorer.score_corpus(scored_items, line_window=line_window, granularity="file"),
            }
        results[variant] = _variant_block(item_records, scored)

    return {
        "target": args.target,
        "corpus": args.corpus,
        "runs": args.runs,
        "line_window": line_window,
        "input_variants": variants,
        "generated_with": {
            "dry": args.dry,
            "timeout": args.timeout,
            "concurrency": args.concurrency,
            "file_access": ("materialized-repo" if (args.corpus == "seeded" and not args.dry) else "none"),
            "model": (None if args.dry else os.getenv("OWLEX_SECOND_OPINION_MODEL", "gpt-5.5")),
            "reasoning": (None if args.dry else os.getenv("OWLEX_SECOND_OPINION_REASONING", "high")),
        },
        "results": results,
    }


def compact_baseline(report: dict) -> dict:
    """Strip per-run raw records → a lean, diffable metrics snapshot for commit.

    Keeps the aggregates that define the "было" (per-item + corpus precision /
    recall / detection-rate and cost) and drops ``per_item_runs`` (and, with
    live codex, the raw reviewer text) so the committed baseline stays small and
    reviewable in git.
    """
    def _compact_scored(s: dict) -> dict:
        return {
            "granularity": s.get("granularity"),
            "line_window": s["line_window"],
            "corpus_aggregate": s["corpus_aggregate"],
            "per_item": [
                {"id": pi["id"], "aggregate": pi["aggregate"]} for pi in s["per_item"]
            ],
        }

    out = {k: v for k, v in report.items() if k != "results"}
    out["results"] = {}
    for variant, block in report["results"].items():
        compact = {"cost": block["cost"]}
        scored = block.get("scored")
        if scored is not None:
            compact["scored"] = {g: _compact_scored(s) for g, s in scored.items()}
        out["results"][variant] = compact
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bench/run.py",
        description="AUDIT-0 benchmark runner for the cross-model solution-audit reviewer.",
    )
    p.add_argument("--corpus", choices=["seeded", "real"], required=True)
    p.add_argument("--target", default="cross_model",
                   help="audit sub-step under test (default: cross_model)")
    p.add_argument("--runs", type=int, default=5, help="K runs per item for variance (default: 5)")
    p.add_argument("--input-variant", choices=["raw_diff", "prose", "both"], default="both",
                   help="seeded only: which input to feed (default: both, for AUDIT-2 before/after)")
    p.add_argument("--line-window", type=int, default=0,
                   help="file:line match tolerance (0 → manifest/default of "
                        f"{scorer.DEFAULT_LINE_WINDOW})")
    p.add_argument("--timeout", type=int, default=int(os.getenv("OWLEX_SECOND_OPINION_TIMEOUT", "120")),
                   help="per-call timeout seconds (default: OWLEX_SECOND_OPINION_TIMEOUT or 120)")
    p.add_argument("--concurrency", type=int, default=1,
                   help="max concurrent live reviewer calls (default: 1 = serial, truest cost; "
                        "raise to shorten wall-time, noted in generated_with.concurrency)")
    p.add_argument("--dry", action="store_true",
                   help="plumbing smoke: stub the reviewer, no codex calls")
    p.add_argument("--out", default=None, help="write JSON report here (default: stdout)")
    p.add_argument("--baseline", action="store_true",
                   help=f"write report to {os.path.join('bench', 'baselines')}/<target>.json")
    p.add_argument("--manifest", default=DEFAULT_MANIFEST, help="seeded manifest path")
    p.add_argument("--real-dir", default=DEFAULT_REAL_DIR, help="real-corpus directory")
    return p.parse_args(argv)


def _write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=2) + "\n")
    print(f"wrote report → {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = execute(args)

    wrote = False
    if args.out:  # full artifact (raw records + reviewer text)
        _write_json(args.out, report)
        wrote = True
    if args.baseline:  # compact, diffable metrics snapshot — both can run in one pass
        os.makedirs(BASELINES_DIR, exist_ok=True)
        _write_json(os.path.join(BASELINES_DIR, f"{args.target}.json"), compact_baseline(report))
        wrote = True
    if not wrote:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
