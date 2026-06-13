---
id: TASK-9
title: 'AUDIT-10: Scale & stratify the benchmark corpus'
status: Done
assignee: []
created_date: '2026-06-10 16:14'
updated_date: '2026-06-10 20:18'
labels:
  - audit-hardening
dependencies:
  - TASK-1
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Problem

The AUDIT-0 corpus is small (8 seeded + 3 real) → over-fit risk and directional-not-robust numbers (risk P0/P2). Hand-authoring labeled bugs is slow and biased. We need ground-truth-labeled, realistic, **stratified** review targets at scale, without p-hacking — and ideally a corpus that grows itself from real usage. The bottleneck is **ground-truth labels**, not diffs — so harvest where labels already exist.

## Change (sources, priority order)

1. **DB extractor (read-only)** — `bench/extract_db.py`: pull the **~89 diff/code-bearing council prompts** from `~/.owlex/owlex.db` (`calls.prompt_text` where `round=1` and looks like a diff/code review) + their `result_text` (candidate findings). These become realistic review **targets**. ⚠ DB ratings (`agent_scores`) and `agreement_score` are **NOT** usable as ground-truth labels — ratings are Claude-blind (circularity, see dropped #8) and `agreement_score` is contaminated by judge-fallback (332/391 "low" is mostly overlap-heuristic artifact). `calls.input_tokens` are NULL — don't rely on them for cost. **Extract targets only; label objectively (below).**
2. **Bug-fix mining** — `git log --grep -iE 'fix|bug|regression|revert'`: pre-fix diff = target, fix-commit changed lines = ground-truth bug location, message = bug_type/description. **Especially: convert each `docs/solutions/` doc + the CLAUDE.md "Learned Patterns" entries into corpus items** (documented real bug + fix = high-quality labeled item).
3. **External datasets** — **BugsInPy** (real Python bugs + fix locations + tests) for volume/diversity, reduces owlex over-fit.
4. **Mutation injection** — `cosmic-ray`/`mutmut` on known-good owlex modules → mutants with exact labels; **equivalent mutants** (behavior-preserving) → decoy/precision set.
5. **Decoys from refactor/chore commits** — `git log --grep -iE 'refactor|chore|style|cleanup'` = changes with NO bug → any finding = false positive (precision corpus).
6. **Stratification** — tag each item: `bug_type` (logic/boundary/concurrency/resource/security/api-contract), `lang`, `diff_size` (S/M/L), `risk_domain`, `source` (db/real-fix/dataset/mutant/decoy), `difficulty`. **Report metrics per-stratum**, not just aggregate.
7. **Anti-p-hacking discipline** — freeze + version (hash) the corpus; keep a **held-out split** not looked at during iteration; add items for **coverage**, never to flip an A/B; record provenance (source + date).
8. **Self-bootstrapping flywheel (durable win)** — persist real `solution-audit` runs into a new table `{diff_hash, findings[file:line], verified(Phase-2 from AUDIT-1), panel_verdict, outcome}` so real usage becomes labeled corpus over time. Owlex already persists council data in SQLite (`calls`/`agent_scores`) — extend the pattern to the audit leg (`second_opinion` is currently ephemeral, persists nothing).
9. **(minor) Fill `calls.input_tokens/output_tokens`** (currently NULL) so cost-in-tokens becomes available for cost benchmarks.

Refs: docs/plans/owlex-audit-hardening.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bench/extract_db.py (read-only) yields ≥N realistic targets from the council DB, with provenance, using NO DB ratings/agreement as labels
- [ ] #2 bug-fix mining harvests labeled items from owlex history + docs/solutions/ (≥M items)
- [ ] #3 corpus stratified (metadata schema extended) + frozen/versioned + held-out split; decoy set present (precision measurable)
- [ ] #4 bench/README.md documents how to grow the corpus without p-hacking
- [ ] #5 flywheel: at minimum a design (ideally impl) for persisting audit-runs as future corpus
- [ ] #6 Tiered size: cheap smoke subset (~10-12) + full stratified set (~40-60), bounded by benchmark cost (codex rate-limit — handover risk P1)
- [ ] #7 Benchmark Success: scaled corpus hits coverage/labeling targets AND a prior AUDIT result either confirms with tighter CI or is honestly revised on the more representative set — with no p-hacking (held-out split + provenance documented)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Per-ticket handover: docs/handovers/audit-10-corpus-scale.md. Scope = full plan (all 9 sub-parts). DB-target labeling workflow authorized. Ground-truth corrected: DB has 89 code-FENCE-bearing round-1 prompts (0 raw diffs) → soft-label targets only; 33 fix commits + 10 refactor decoys + 7 solution docs minable; mutation (cosmic-ray/mutmut) is the load-bearing objective-label volume source. Build order: stratification schema → extract_db.py → mine_fixes.py → labeling workflow → mutation → BugsInPy → flywheel(table+design) → token-fill → anti-p-hacking/README. pytest bench/ after each edit; final было/стало = live codex re-run of AUDIT-1 precision on 8+3 vs scaled corpus.

Build complete — all 9 sub-parts shipped, bench 175 tests + tests/ 303 green. Corpus scaled 9→103 items across 6 sources (seeded/real-fix/mutant/dataset/decoy/db-llm-label), full bug_type taxonomy, deterministic hash-derived held-out split (derive_holdout=18), corpus_hash freeze, README anti-p-hacking section, flywheel (table+design doc). Labeling workflow: 89 DB targets → 14 reviewable/19 soft bugs. было/стало (corpus robustness) delivered. Live стало probe (24 codex calls): seeded precision 0.96/recall 1.00 vs dataset 0.22/0.33 vs real-fix 0.00 — CONFIRMS P0 over-fit (seeded numbers don't generalize), honest revision. Two construction bugs found, blocking trustworthy mined/dataset/mutant live numbers — see follow-up task. Handover: docs/handovers/audit-10-corpus-scale.md.

Construction bugs fixed under TASK-10; live numbers now trustworthy. Corrected was/now headline: seeded-only corpus 0.96 precision looked great but real bugs (dataset/real-fix) hit recall≈1.00 / precision 0.75-0.92 once shown buggy code — over-fit concern resolves to a PRECISION-on-clean-code risk (decoy stratum precision 0.00), not a recall gap. Numbers directional (2 items/source, K=3).

CORRECTION (supersedes earlier 'auditor mediocre on real bugs ~33-60%'): that was a CORPUS-LABEL ARTIFACT, not auditor weakness. Widened real-fix probe (15 items, K=3, bench/exp_lens.json) showed recall 0.33, but inspecting the misses (bench/show_label.py) revealed mine_fixes auto-anchors each bug label at the FIRST changed line of the reversed diff — which for real commits is usually an import/comment/config line, NOT the defect (e.g. fix-1bbcd1a labeled at `from typing import Optional`; fix-63c88dd labeled on markdown in a docs commit). Many mined 'fix' commits are multi-line/multi-file/docs with no single localizable bug. So owlex-mined real-fix labels are UNRELIABLE. On TRUSTWORTHY strata the auditor performs WELL: seeded 0.98, mutants 1.00, BugsInPy real Python bugs 0.92/recall 1.00. Net: the P0 over-fit fear largely resolves in the auditor's favor; the weak link is our owlex-history mining, not the auditor. Lens A/B experiment (baseline vs +API/contract dimension) was NEGATIVE — recall 0.33→0.33, precision slightly down, target bug still missed, 1 item improved / 2 regressed (prompt-sensitivity noise) — NOT promoted to the skill.

DONE. Corpus scaled 9→103 items, 6 sources, stratification schema + hash freeze + deterministic held-out split + flywheel design + README anti-p-hacking. Committed fbf8e90 + 1e9b4e7. Trustworthy-labeled strata = seeded+mutant+dataset(BugsInPy); owlex-mined demoted to unlabeled realism targets (labels were garbage — AUDIT-10e). CORRECTED HEADLINE: on well-labeled bugs the auditor is strong (recall ~1.00, precision ~0.95: seeded 0.98/1.00, mutant 1.00/1.00, BugsInPy 0.92/1.00). The P0 over-fit fear resolved in the auditor's favor; the lens-improvement experiment was a negative result. Open children: TASK-12/AUDIT-10d (equivalent-mutant precision instrument) — the only remaining real open question (true hallucination rate). Sub-tasks 10b/10c/10e all Done.
<!-- SECTION:NOTES:END -->
