---
title: "Shadow-replay against owlex.db is the standard protocol for evaluating new seat/judge candidates"
status: confirmed
category: architecture
trigger_on: [new-code-path, refactor, agent-integration]
date: 2026-05-28
---

## Problem

Adding a new CLI agent as a council seat, judge, or rater is a load-bearing architectural decision:

- It changes council wall-clock latency (slowest seat dominates).
- It changes the perspective diversity of R1 / R2 / blind-rating.
- It depends on a 3rd-party vendor whose model catalog rotates (`docs/solutions/external-tools/cli-catalog-rotation-needs-health-check.md`).
- Vendor marketing claims ("coding-focused", "best at agentic", "fastest") consistently diverge from measured behavior on this specific workload (council deliberation).

Historically, seat additions to Owlex were ad-hoc — "obviously coding-relevant, ship it." That worked when the candidate pool was small and homogeneous (codex / claudeor / gemini / cursor — all general-purpose). It does not work as the candidate pool diversifies (Grok-build, GLM, Qwen-Coder, etc., each with different output styles, latencies, and reliability profiles).

The risk: integrating a seat that adds noise instead of signal, or that fails silently in production after one CLI version bump. The cost: a council seat that goes wrong is expensive to detect (you have to notice rating shifts, agreement-judge drift, or latency regressions in the dashboard).

## Solution

**Before integrating any new agent as a seat, judge, or rater, run a shadow-replay against historical `~/.owlex/owlex.db` data.** The DB already contains everything needed for offline evaluation — no production code changes, no rate-limit exposure.

### What owlex.db gives you for free

| Table | What you can replay |
|---|---|
| `calls` (round=1, status='completed') | Real R1 prompts + responses — feed any candidate the same input |
| `council_outcomes.agreement_score` | Ground truth from current judge — compare a new judge's output |
| `agent_scores` (rater='claude_blind') | Ground truth from blind rater — compare a new rater's output |
| `council_anonymization` (label→agent map, salt=`blind:{council_id}`) | Reproduces the exact anonymization the existing rater saw |

### The three canonical shadow experiments

| Experiment | Purpose | Sample size | Outputs |
|---|---|---|---|
| **1. Agreement-judge replay** | Compare new judge to current gpt-5.5 codex judge on the same R1 responses | ~60 councils (~30-60 min) | Cohen's kappa, R2-decision agreement, grok-save rate when current judge fails |
| **2. Seat-R1 shadow** | Generate the candidate's R1 response on historical questions, compute structural metrics vs existing seats | ~15 councils (~30-60 min) | Length, code blocks, file refs, latency distribution per seat |
| **3. Blind-rater replay** | Re-anonymize R1, ask candidate to blind-rate all letters, compare to existing claude_blind ratings | ~60 councils (~60-90 min) | Score exact match, Spearman per dimension, top-1 winner agreement, per-agent flip rate |

### Decision matrix

| Kappa / Spearman / agreement | Verdict |
|---|---|
| > 0.8 | Direct replacement candidate |
| 0.6–0.8 | Ensemble candidate (cross-validation) |
| 0.4–0.6 | Too divergent for replacement; valuable only as fallback / diagnostic |
| < 0.4 | Diverges fundamentally — different judgment criteria, low ensemble value |

Per-agent breakdowns are non-optional — a candidate that has 11% flip rate on codex but 50% on gemini is **systematically biased**, not noisy. Aggregate metrics hide this.

## Templates

Three reusable scripts live in `scripts/` (read-only against production DB, output to `scripts/shadow_results/`):

```
scripts/shadow_grok_judge.py    # Experiment 1 — agreement judge replay
scripts/shadow_grok_seat.py     # Experiment 2 — seat R1 generation + structural metrics
scripts/shadow_grok_rater.py    # Experiment 3 — blind rater replay
```

Each script:
- Opens `~/.owlex/owlex.db` with `mode=ro` URI (cannot accidentally write).
- Resumes from existing JSONL (re-running picks up where it left off).
- Writes one JSON line per council to `shadow_results/*.jsonl`.
- Generates a date-stamped markdown summary with kappa / correlation / confusion matrix.

To evaluate a new candidate `${MODEL}`:
1. Copy one of the scripts to `shadow_${MODEL}_${experiment}.py`.
2. Replace `grok -p ... --model grok-build` with the candidate CLI invocation.
3. Set `OWLEX_${MODEL}_MODEL` env var with safe default (per the External CLI rotation pattern).
4. Run on at least the 3 experiments above before any production integration.

## Why this works

- **Zero production blast radius**: read-only DB, no `owlex/*` module changes.
- **Ground truth exists already**: blind ratings + agreement scores were collected over months of real councils — orders of magnitude more data than any quick benchmark.
- **Catches profile mismatch early**: marketing claims about "coding-focused" or "fast" rarely survive contact with measured `code_blocks_per_response` and `p95_latency`.
- **Cheap by construction**: subscription-based CLIs (Grok Build, Cursor, Claude Code) cost $0 per shadow-replay call; API-based eval costs a few cents.

## Prevention

When considering a new seat/judge/rater:

1. **Do not integrate before shadow-replay.** "Add as opt-in" still adds latency and dashboard noise; shadow is the actual gate.
2. **Run all three experiments.** A model can pass as judge (decision = binary classification) but fail as rater (preference ranking with bias) — they're different tasks.
3. **Report per-agent breakdowns** — aggregate kappa can hide a 50%-bias on one seat.
4. **Save the date-stamped baseline.** If you reject the candidate, the next time it's reconsidered (new version) the baseline lets you measure delta, not redo the work.

Related learnings:
- `docs/solutions/external-tools/cli-catalog-rotation-needs-health-check.md` (pin model + probe at startup — applies to any shadow script)
- `docs/solutions/testing/path-home-globals-pollute-prod.md` (always `mode=ro` URI for DB reads in scripts)
