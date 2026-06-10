# AUDIT-10 — Scale & stratify the benchmark corpus (+ flywheel) — per-ticket handover (2026-06-10)

Backlog: **TASK-9** (label `audit-hardening`). Depends on AUDIT-0 ✅ → unblocked.
Canonical spec: `docs/plans/owlex-audit-hardening.md` § "AUDIT-10". Program handover: `docs/handovers/audit-hardening-remaining.md`.
**User decisions this session:** (1) scope = **full plan as written** (all 9 sub-parts); (2) **labeling workflow authorized** for the DB-target fan-out.

## Why this ticket
The AUDIT-0 corpus (8 seeded + 3 real) is over-fit-prone → every detection number is directional, not robust (risk **P0/P2**). Grow it to a **stratified, objectively-labeled, frozen+versioned** corpus so AUDIT-1/-2 results re-confirm with tighter CI (or are honestly revised).

## Ground truth corrected (verified against the repo + DB this session — DO NOT trust the old "~89 diff-bearing" wording)
| Source | Plan's wording | **Verified reality** | Use |
|---|---|---|---|
| DB council prompts | "~89 diff-bearing" | **89 round-1 prompts contain a code *fence*** (```); **0 contain raw `diff --git`/`@@ `**. Prose question + embedded snippet, **no file:line anchors**. | realism/soft-label **targets only**, `source=db-llm-label`, kept OUT of the precision/held-out set |
| owlex fix commits | "git log --grep" (malformed `-iE`) | **33** `fix\|bug\|regress\|revert` commits (correct multi-`--grep`); many are recent audit `feat`s caught on the `fix` substring → **filter to true bug-fixes** | pre-fix diff = target, fix lines = label |
| refactor/chore | decoy source | **10** commits | decoys (any finding = FP) |
| `docs/solutions/` | "high-quality labeled" | **7** docs | hand-traceable labeled items |
| CLAUDE.md Learned Patterns | — | ~5 entries | labeled items |
| mutation (`cosmic-ray`/`mutmut`) | priority #4 | **the only objective-label source that scales** — owlex history alone can't hit 40–60 | bulk labeled + equivalent-mutant decoys |
| BugsInPy | priority #3 | external; reduces owlex over-fit | volume/diversity |

**Implication:** mutation is load-bearing for volume, not the DB. DB gives realism + soft labels via the authorized workflow.

## Build order (pytest bench/ after EACH edit — that loop validates harness code, NOT the corpus-robustness metric)
1. **Stratification schema** — extend `bench/corpus/seeded/schema.json` + `bench/corpus.py` loader + `bench/scorer.py` (`validate_manifest` + new per-stratum scoring). New per-item metadata: `bug_type` (logic/boundary/concurrency/resource/security/api-contract), `lang`, `diff_size` (S/M/L), `risk_domain`, `source` (db/real-fix/dataset/mutant/decoy/seeded), `difficulty`, `split` (iterate|holdout). **Backwards-compatible** — existing 8 items keep validating.
2. `bench/extract_db.py` (**read-only**) — pull the 89 code-bearing round-1 prompts (`prompt_text`/`result_text`) as targets. **NO** `agent_scores`/`agreement_score` as labels (circularity + judge-fallback contamination). Provenance row per item.
3. `bench/mine_fixes.py` — owlex fix-commits (pre-fix diff=target, fix lines=label) + `docs/solutions/` + Learned Patterns → labeled items; refactor/chore commits → decoys.
4. **Labeling workflow** (authorized) — fan out over the 89 DB targets → `{bug_type, file:line?, description}` JSON, `source=db-llm-label`, kept out of held-out split. (LLM-derived = weaker ground truth; tag + segregate.)
5. **Mutation injection** — `cosmic-ray`/`mutmut` on known-good owlex modules → mutants (exact labels) + equivalent mutants (decoys).
6. **BugsInPy** ingestion → external labeled volume.
7. **Flywheel** — new SQLite table `{diff_hash, findings[file:line], verified(AUDIT-1 Phase-2), panel_verdict, outcome}` so real `solution-audit` runs become future corpus (`second_opinion` is currently ephemeral). Design doc + impl.
8. **`calls.input_tokens/output_tokens` fill** (currently NULL) → cost-in-tokens for cost benchmarks.
9. **Anti-p-hacking** — freeze + content-hash the corpus; held-out split untouched during iteration; provenance (source+date); README "grow the corpus without p-hacking" section. Tiered: smoke (~10–12) + full stratified (~40–60), bounded by codex rate-limit.

## Benchmark (было/стало) — the corpus IS the instrument
- Metric: corpus size + stratum coverage + % objectively-labeled (vs hand-authored) + **variance/CI of a downstream metric**.
- Procedure: re-run AUDIT-1 precision (or AUDIT-2 recall) on **было** 8+3 vs **стало** scaled stratified corpus.
- Success: coverage/labeling targets hit AND a prior AUDIT result confirms with **tighter CI** or is **honestly revised**, no p-hacking (held-out + provenance documented).
- ⚠ This is a **live codex run** (~60–80 calls, foreground `--concurrency 5`, ~7 min) — hits the rate ceiling (risk P1). The pytest loop is the fast inner loop; the live run is the actual metric.

## Guardrails (carried from program handover)
- DB is **read-only**. Tests' autouse `_isolate_owlex_home` must keep prod DB untouched — extractor opens DB read-only, not via store writes.
- **damage-control hook** blocks Bash containing `.keys`/`.dump`/`rm -f`/`rm -rf` substrings → put such code in a file, never inline `python -c`.
- Don't p-hack: add items for coverage, never to flip an A/B.
- `second_opinion` feature is still uncommitted; flywheel must not couple the corpus PR to it (design doc + guarded impl).
- The skill under improvement (`~/.claude/skills/solution-audit/SKILL.md`) is external — not edited by this ticket (AUDIT-10 is pure bench/).

## Verify
- `python -m pytest bench/ -q` (was 44) and `python -m pytest tests/ -q` (was 303) stay green.
- `python bench/run.py --corpus seeded --runs 1 --dry` → JSON, no codex.

## RESULTS (this session, 2026-06-10)
**Built & green — bench 175 tests, tests/ 303, no regression.** All 9 sub-parts delivered:
- Stratification schema + `corpus_hash`/`stratum_map`/`score_by_stratum`/`corpus_stats` + `derive_holdout` (deterministic, hash-based, un-cherry-pickable held-out split).
- `extract_db.py` (read-only) → 89 DB targets. Labeling **workflow** (89 Sonnet agents) → 14 reviewable / 19 soft bugs (75 correctly rejected as non-code-review). `integrate_labels.py` → `db/labeled.json`.
- `mine_fixes.py` → 33 (15 fix + 11 doc + 7 decoy). `mutate.py` → 22 AST mutants. `ingest_bugsinpy.py` → 25 real bugs. `flywheel.py` + `docs/design/audit-flywheel.md`. `fill_tokens.py` (dry-run-safe).
- **Corpus: 103 items, 6 sources, full bug_type taxonomy, derived_holdout=18.** README anti-p-hacking section.

### было/стало (corpus robustness — primary metric, no codex): 9→103 items, 1→6 sources, 6→15 bug_types, taxonomy gaps 5→0.

### Live стало probe (24 codex calls, raw_diff, K=3 — `bench/reports/stalo_probe.json`, summarize via `bench/summarize_probe.py`):
| source | precision | recall | note |
|---|---|---|---|
| seeded | 0.96±0.10 | 1.00 | purpose-built — auditor aces |
| dataset (BugsInPy) | 0.22±0.19 | 0.33 | real bugs, unfamiliar code → mostly missed |
| real-fix | 0.00 | 0.00 | ⚠ construction artifact (see below) |
| decoy | 0.00 (all FP) | — | over-flags benign refactors |

**Headline: seeded numbers DO NOT generalize — P0 over-fit confirmed by measurement.** Honest revision (plan's success criterion), NOT p-hacked.

## ✅ CONSTRUCTION BUGS FIXED (AUDIT-10b / TASK-10) + re-probe (v2)
Both bugs below were fixed. `reverse_unified_diff` added to `bench/corpus.py`; `mine_fixes.py`/`ingest_bugsinpy.py` now present the BUGGY pre-fix code as `+` lines; `mutate.py` emits a unit diff per mutant (has_diff=22, runnable). bench 181 tests + tests/ 303 green.

**Re-probe (`bench/reports/stalo_probe_v2.json`, 30 codex calls, K=3) — the fix validated:**
| source | v1 (broken) | v2 (fixed) precision / recall |
|---|---|---|
| seeded | 0.96/1.00 | 0.94 / 1.00 |
| dataset | 0.22/0.33 | **0.92 / 1.00** |
| real-fix | 0.00/0.00 | **0.75 / 1.00** |
| mutant | excluded | **1.00 / 1.00** |
| decoy | 0.00 FP | 0.00 (all FP) |

**Conclusion (honest):** the catastrophic v1 real-bug numbers were the POLARITY artifact, NOT auditor weakness. With buggy code shown, **recall ≈ 1.00 everywhere**. The surviving real signal is **precision on benign refactors (decoy 0.00 — the auditor over-flags clean code)**. Numbers are directional (2 items/source, K=3, wide stdev) — a larger run is needed for tight CIs, but construction is now sound.

## AUDIT-10c (TASK-11) — tighter K=5 probe + decoy FP investigation (`bench/reports/stalo_probe_v3.json`, 75 calls)
**Per-source (K=5, 3 items/source):**
| source | precision | recall | read |
|---|---|---|---|
| seeded | 0.98 | 1.00 | synthetic — aced |
| mutant | 1.00 | 1.00 | AST mutants — aced |
| dataset (BugsInPy) | 0.92 | 1.00 | public real bugs — aced |
| **real-fix (owlex history)** | **0.58** | **0.60 ±0.51** | **the genuinely HARD stratum — high variance, ~40% of real owlex bugs missed** |
| decoy | 0.00 (all "FP") | — | see below — instrument is wrong |

**Headline (honest, revised):** synthetic/mutant/public bugs are caught ~100%; **real owlex-history bugs are the hard case (~0.60 recall, huge variance)**. That is the real over-fit signal — purpose-built corpora overstate; real history is where the auditor struggles.

**Decoy FP investigation — KEY METHODOLOGICAL FINDING:** decoy precision reads 0.00, but the analyzer (`bench/analyze_decoys.py --full`) shows the "false positives" are mostly **legitimate-looking concerns about the real refactored code** (e.g. "timeout kills only top-level process, children survive" — owlex's own documented bug class; "bare except swallows cancellation"; "tests inspect source text, not behavior"), NOT hallucinations. The merge-commit decoy got 0 findings (correct silence). 47% of decoy runs produced ≥1 finding (mean 1.33±1.59).
→ **Decoys built from REAL refactor commits don't measure hallucination — they measure the auditor finding real issues in real code, miscredited as FP by the "no planted bug = any finding is FP" rule.** A true precision/hallucination instrument needs **guaranteed-clean equivalent mutants** (behavior-preserving AST transforms of known-good code) — which the original AUDIT-10 plan called for but we built refactor decoys instead. **Follow-up: add an equivalent-mutant precision set.**

## (resolved) TWO CONSTRUCTION BUGS the probe exposed
1. **Polarity:** `mine_fixes`/`ingest_bugsinpy` feed the reviewer the **fix-commit diff** (corrected code), not the **pre-fix** buggy code → real-fix recall 0.00 is largely an artifact (reviewer shown already-fixed code). FIX: present the reverse-patched pre-image as the review target; re-anchor labels to the removed (buggy) lines. Until then, real-fix/dataset live numbers are NOT trustworthy.
2. **Mutants not runnable in raw_diff:** `load_mutants` doesn't inline the materialized `post_image` (audit: `bench/check_runnable.py` shows mutant has_diff=0 has_post=0), so all 22 mutants are filtered out of the live path. FIX: inline post_image in `load_mutants`, OR have `mutate.py` also emit a unit `diff` (original→mutated) so the raw_diff variant works.

## Build order / remaining
- Fix #1 + #2 above, then re-run the bounded probe → trustworthy mined/dataset/mutant live numbers.
- Optional: a full (rate-limited) re-baseline once polarity is fixed.

## Branch
`audit-0-bench-harness` (off `main`, protected — PRs via `gh pr create -R rsboarder/owlex`). Commits not pushed. AUDIT-10 work uncommitted in working tree (bench/ + docs/).
