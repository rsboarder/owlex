# `bench/` — AUDIT-0 benchmark harness for `solution-audit`

Measures **detection quality** (precision / recall / detection-rate) and **cost**
(wall-time / reviewer-count) of the cross-model `second_opinion` reviewer against
a ground-truth-labeled corpus, with **K runs + mean ± stdev** (the audit is
non-deterministic — a single before/after run is not a benchmark).

This is **AUDIT-0**, the dependency that unblocks AUDIT-1/2/3/4/6 — every
downstream ticket's было/стало runs through here. Full plan:
[`docs/plans/owlex-audit-hardening.md`](../docs/plans/owlex-audit-hardening.md).

## Scope

The harness targets the **`cross_model`** step only — a direct call to
`owlex.second_opinion.get_second_opinion()`, a plain coroutine a script can
invoke. The 5 Opus dimension judges and `council_ask` run as **Claude Code Agent
spawns**, which a standalone Python script **cannot** drive (plan Open Q1). A
`TARGETS` registry seam is left in [`run.py`](run.py) for `panel` / `council`
targets; they are intentionally absent so `--target panel` fails loudly rather
than silently mis-measuring.

## Layout

```
bench/
  run.py                       # runner + CLI (the only live-codex entry point)
  scorer.py                    # pure scoring: precision/recall, matching, manifest validation
  corpus.py                    # pure corpus loading + unified-diff parsing
  corpus/
    seeded/
      manifest.json            # labeled ground truth (bugs + decoys)
      schema.json              # human-readable contract (validator is scorer.validate_manifest)
      diffs/seed-*.diff        # the synthetic diffs (one planted issue per file)
    real/<sha>.diff            # real owlex git-history diffs, unlabeled (cost/realism)
  baselines/cross_model.json   # committed было snapshot (compact metrics)
  tests/                       # pure, deterministic — never calls codex
```

## Running

```bash
python bench/run.py --help

# plumbing smoke — no codex, deterministic (CI-safe):
python bench/run.py --corpus seeded --runs 1 --dry

# real benchmark (calls codex K times per item per variant — costs time/tokens):
python bench/run.py --corpus seeded --target cross_model --runs 5

# cost/realism on real diffs (unlabeled — cost only, no scoring):
python bench/run.py --corpus real --runs 3

# capture/refresh the committed было baseline:
python bench/run.py --corpus seeded --runs 5 --baseline
```

Flags: `--input-variant {raw_diff,prose,both}` (seeded; default `both`),
`--line-window N` (match tolerance; default 3), `--timeout`, `--out PATH`,
`--manifest`, `--real-dir`.

## Metrics

A **finding** is a `path:line` citation parsed from the reviewer's prose. It
**matches** a labeled bug/decoy when the file matches (basename / path-suffix)
**and** the line is within `± line_window` (default 3) — LLM citations drift a
few lines, so exact-match would read recall ≈ 0.

- **recall** = labeled bugs matched / total bugs (the detection metric AUDIT-2 moves).
- **precision** = true-positive findings / total findings (`null` when the
  reviewer produced nothing — undefined, not zero). The metric AUDIT-1 moves.
- **detection_rate** = fraction of items with ≥1 bug caught (AUDIT-3 quality-guard).
- **decoy_hits** = findings landing on a planted decoy (attributable false positives).
- **cost** = wall-time mean ± stdev + reviewer-count. **Tokens are not captured**
  — `get_second_opinion` returns no token counts and extracting them would
  re-plumb the feature under audit (plan Open Q2).

Each seeded diff is **clean except for its planted bugs + decoys**, so any
finding matching neither is a hallucination (also a false positive). Aggregates
are micro-averaged (every run of every item pooled with equal weight).

## Citation verification (AUDIT-1)

Phase 2 of `solution-audit` mechanically citation-checks findings — open each
`file:line`, drop the ones that don't resolve. The harness models that as a
**pure** step (`scorer.verify_findings`): a finding survives only if its cited
file exists in the materialized post-image **and** its line is in range (with the
same `± line_window` EOF tolerance, so a true positive cited a hair past EOF is
never dropped). A hallucinated file or an out-of-range line can't be opened, so
it's dropped rather than counted as a false positive.

Every seeded run is therefore scored **twice**, for free (no extra codex):

- `results.<variant>.scored.line` — raw, **before** verification (`было`).
- `results.<variant>.scored.verified.line` — citation-checked, **after** (`стало`),
  plus `corpus_dropped` (`total` + `by_reason`: `file_unresolved` / `line_out_of_range`).

Verification only removes citation-unresolvable findings (never a finding that
lands on a planted bug), so **verified precision ≥ raw precision by construction**
and recall is preserved. The win is real only when the reviewer actually
hallucinates citations — that's what the bait item is for:

- **`seed-09-bait-hallucination`** — a real off-by-one in `owlex/retry_budget.py`
  (resolves, survives) next to a call into `owlex/backoff.py`, a module
  deliberately absent from the item's post-image. A reviewer tempted to flag the
  unseen backoff cap cites `owlex/backoff.py:<n>` → `file_unresolved` → dropped.
  (Deterministic demo without codex: a `retry_budget.py:7` + `backoff.py:4`
  finding pair scores raw precision 0.5 → verified 1.0, 1 dropped.)

The before/after reads as `scored.<g>.corpus_aggregate.precision` vs
`scored.verified.<g>.corpus_aggregate.precision`. AUDIT-1's precision metric uses
**`line`** granularity (verification is about exact citations resolving).

> **First live bait probe (2026-06-10, gpt-5.5/high, K=5, `raw_diff`, 5/5 ok) — honest
> negative: 0 findings dropped.** The reviewer found the real bug (recall 0.80) and produced one
> false positive — but cited it at `retry_budget.py:1` (the real import line), **not** the absent
> `owlex/backoff.py`. A citation-disciplined model with repo access won't fabricate a line in a
> file it can't open, so the file-resolution magnet didn't bite; raw precision == verified
> precision (0.60). **Accepted as-is — the magnet is deliberately NOT tuned until it bites** (that
> would p-hack the result, the AUDIT-0 P0 risk).
>
> **What this measures:** mechanical citation-verification has **low yield** on this workload —
> the cross-model rarely emits a non-resolving citation. Its residual false positives (e.g.
> flagging a benign import) *resolve*, so they sit outside mechanical reach; catching them needs
> **semantic** verification (does the cited line actually carry the claimed bug?), which requires
> the Opus judge and isn't script-callable (plan Open Q1 / AUDIT-3 territory). AUDIT-1 still ships:
> it's a free, correct safety net for the low-but-nonzero hallucination case (proven in
> `tests/test_scorer.py`), **not** a measured precision gain. Same shape as AUDIT-2's honest
> correction — the value is real but it isn't the one the premise predicted.

## Adding a corpus item

**Seeded (labeled):**
1. Drop a unified diff at `corpus/seeded/diffs/<id>.diff`. **Put one planted
   issue per file** — different basenames never collide under the line-tolerance
   matcher, so attribution stays unambiguous (no line-spacing math).
2. Add an item to `manifest.json`: `id`, `file`, `diff_path`, `prose_summary`
   (an editorialized, line-anchor-free description — the "было" input variant),
   `bugs[{bug_type,file,line,description}]`, optional `decoys[{file,line,description}]`.
   `line` = the **new-file line number** of the offending added line.
3. `python -m pytest bench/` — `test_corpus.py` verifies every labeled line
   resolves to a real added line and the contract holds (≥10 bugs / ≥3 types /
   ≥2 decoys).

**Real (unlabeled):** `git show <sha> > bench/corpus/real/<sha>.diff`. No labels —
used for cost/realism only.

## Before/after comparison (the downstream pattern)

Each downstream ticket compares two configurations on the **same** corpus and
reports the metric delta as mean ± stdev over K runs. Example — **AUDIT-2**
(prose summary vs raw diff):

```bash
python bench/run.py --corpus seeded --runs 5 --input-variant both --out /tmp/audit2.json
# compare results.prose.scored vs results.raw_diff.scored — recall mean ± stdev.
# success: raw-diff recall ≥ prose recall, no per-type regression.
```

The `both` variant runs both inputs in one pass so the comparison is paired.

## Baseline

[`baselines/cross_model.json`](baselines/cross_model.json) is a **compact** metrics
snapshot (aggregates + cost, no raw records) so it stays diffable in git.

**Captured 2026-06-10** (gpt-5.5 / reasoning=high, K=5, 8 items, 80/80 ok, `file_access=materialized-repo`):

| variant | line-recall | file-recall | precision | wall/call |
|---------|-------------|-------------|-----------|-----------|
| `raw_diff` | 0.97 ± 0.16 | 0.97 ± 0.16 | 0.88 ± 0.19 | 20.5s |
| `prose`    | 0.95 ± 0.22 | 0.97 ± 0.16 | 0.86 ± 0.25 | 43.3s |

Refresh with `python bench/run.py --corpus seeded --runs 5 --baseline`
(`--concurrency 5` ≈ 7 min; ~80 codex calls).

> **Baseline is the 8-item (`seed-01..08`) snapshot — it predates the `verified`
> block and the AUDIT-1 bait item.** `seed-09-bait-hallucination` and the
> raw-vs-`verified` precision columns land on the next full re-baseline, deferred
> on cost (a 9-item × 2-variant × K=5 refresh is ~90 codex calls — near the
> rate-limit ceiling; prefer targeted `--input-variant raw_diff` probes on the
> bait item while iterating). The verify mechanism itself is proven
> deterministically in `tests/test_scorer.py`.

> **The honest result does NOT support AUDIT-2's recall premise — it's a cost win.**
> With repo access (faithful to production — both variants get the materialized files
> via `_materialize`), `prose` and `raw_diff` detect **equally** (line-recall 0.95 vs
> 0.97; file-recall 0.97 vs 0.97). The earlier dramatic `0.00 → 0.87` was **entirely
> an empty-sandbox artifact**: given only prose and no readable files, codex refuses to
> review and demands the diff. Once it can read the files, prose loses almost nothing.
>
> What AUDIT-2 **does** win, measured here:
> - **Cost**: `raw_diff` is ~2.1× faster (20.5s vs 43.3s) — with only prose, codex
>   hunts through the repo instead of reading focused hunks. The robust, consistent win.
> - **Bias** (AUDIT-2's other argument) is orthogonal to recall — this benchmark does
>   not measure whose framing shapes the review.
>
> **The one regime where raw edges prose on recall**: `seed-08` (a subtle one-line
> guard removal buried in a 94-line file) is the *only* item where raw beats prose at
> line-precision (1.00 vs 0.80) — the raw hunk screams "removed a bound" while prose
> makes codex re-derive it. But that's 1 of 8 items, within stdev. `seed-07` (a larger
> but recognizable new block) is parity. So even on large diffs the recall gap is
> marginal; **AUDIT-2 ships justified on cost, not recall** (recorded as fact).
>
> Other reads: `seed-04-jsonl-parse` (negative-slice boundary) is the hard item —
> recall ~0.80 both variants. Precision ~0.9 is mildly inflated: the ±3 line window
> absorbs unlabeled-but-real nearby findings as TPs, leaving AUDIT-1 (verification)
> real headroom.

## Growing the corpus without p-hacking (AUDIT-10)

### Sources and label quality

| Source key | How it's built | Label type |
|------------|---------------|------------|
| `seeded` | Hand-crafted synthetic diffs with deliberately injected bugs + decoys | **Objective** — ground truth, manually planted |
| `real-fix` | Real bug-fix commits from owlex git history (via `mine_fixes.py`) | **Objective** — the commit IS the ground truth |
| `mutant` | AST-mutated variants of known-good code (generated by mutant scripts) | **Objective** — mutation IS the injected defect |
| `db-llm-label` | Council DB prompts soft-labeled by an LLM (via `prep_labeling.py`) | **Soft** — LLM-derived, not verified ground truth |
| `documented-soft` | Bugs described in docs/solutions / CLAUDE.md (inferred from descriptions) | **Soft** — documentation-derived, not code-verified |
| `decoys` (within items) | Plausible-but-not-a-bug changes alongside bugs | Objective (planted) — precision measurability |

**Rule**: objective sources (seeded / real-fix / mutant) give trustworthy recall measurements. Soft-label sources inflate coverage numbers but are less reliable for A/B benchmarking. Track `objective_label_pct` to ensure the benchmark's load-bearing recall metric stays anchored to objective labels.

### Stratification schema

Every corpus item MAY carry these fields (validated when present, never required):

| Field | Values | Purpose |
|-------|--------|---------|
| `source` | seeded / real-fix / mutant / db-llm-label / documented-soft / decoy | Label quality tier |
| `lang` | python / typescript / etc. | Language coverage |
| `diff_size` | S / M / L | Complexity stratum |
| `risk_domain` | subprocess / config / parsing / ... | Domain coverage |
| `difficulty` | easy / medium / hard | Detection difficulty |
| `split` | iterate / holdout | Experiment safety |

**Bug-type taxonomy** (`BUG_TYPE_TAXONOMY` in `scorer.py`): `logic`, `boundary`, `concurrency`, `resource`, `security`, `api-contract`. Track `bug_type_coverage["missing"]` to see which types are under-represented.

### FREEZE discipline (anti-p-hacking)

`corpus_hash` (in `scorer.py`) computes a stable sha256 over `(id, file, diff_path, bugs, decoys)` for a seeded manifest. `corpus_stats` computes an equivalent `content_hash` over a flat item list.

**Protocol**: before running any A/B benchmark, record the current hash. If the hash changes between the baseline run and the comparison run, it is a **new experiment** — the before/after numbers are not comparable. Never tune the corpus to flip a result; that invalidates the measurement.

### HELD-OUT split

Items with `"split": "holdout"` are **never examined during iteration**. They exist solely for final confirmation after an improvement has converged. Peeking at holdout items during development is p-hacking — treat them as locked behind a one-way door.

Items with `"split": "iterate"` are the working set. All tuning, re-runs, and A/B comparisons happen here.

### Rule: add items for coverage, never to flip an A/B

When extending the corpus:

1. Add items that fill a **missing** stratum (`bug_type_coverage["missing"]`, underrepresented `risk_domain`, new `lang`).
2. Record `source` + today's date in the item's `provenance` field so every item has an audit trail.
3. Re-run `corpus_stats` and confirm `bug_type_coverage["missing"]` shrank.
4. **Never add an item because you noticed the new recall is higher.** If you add items and then re-run the A/B, that is a new experiment — record a new baseline hash.

### Computing coverage

Save as `bench/show_corpus_stats.py` and run it, or use `bench/corpus_stats_cli.py`:

```python
# bench/corpus_stats_cli.py — already committed
from bench.corpus import load_corpus
from bench.scorer import corpus_stats
import json

stats = corpus_stats(load_corpus())
print(json.dumps(stats, indent=2, ensure_ascii=False))
```

Key fields to monitor:

- `total` — corpus size
- `by_source` — distribution; watch for soft-label drift
- `objective_label_pct.pct_objective` — should stay high (> 0.7 is a reasonable floor)
- `bug_type_coverage.missing` — types with zero representation
- `by_split` — confirm holdout items are not being accidentally iterated on

## Self-test

```bash
python -m pytest bench/ -q     # pure scorer/corpus/runner — fast, no codex
python -m pytest tests/  -q     # the main suite is unaffected (testpaths=["tests"])
```
