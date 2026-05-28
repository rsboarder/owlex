---
title: "Grok-build v0.2.3 (May 2026) — shadow eval baseline for Owlex seat candidacy"
status: confirmed
category: architecture
type: adr  # point-in-time decision record — re-run if Grok version bumps materially
trigger_on: [agent-integration]
date: 2026-05-28
model_version: grok-build (CLI v0.2.3, default model id 'grok-build', --effort low)
---

## Context

Considered adding Grok as a 7th seat to the existing 6-seat Owlex council (codex, gemini, cursor, claudeor, opencode, aichat). User has SuperGrok subscription — zero per-call cost via official `grok` CLI in headless `--output-format json` mode.

Followed the shadow-replay protocol (see `shadow-replay-protocol-for-seat-evaluation.md`). Three experiments on historical councils from `~/.owlex/owlex.db`.

## Measurements (2026-05-28)

### Experiment 1 — Agreement-judge replay (N=62 councils)

| Metric | Value |
|---|---|
| Cohen's kappa (vs current gpt-5.5 judge, threshold=3.5 for R2) | **0.510** |
| Pearson correlation (raw 1-5 scores) | 0.491 |
| R2-decision binary agreement | 75.8% |
| **Grok-save rate** (when current codex judge failed → fallback overlap) | **20/20 = 100%** |
| Mean latency per call | 43s |
| Grok subprocess errors | 0 |

Confusion matrix (rows = current judge, cols = grok):
- TN=20, FP=8 (grok over-triggers R2)
- FN=7, TP=27

### Experiment 2 — Seat R1 shadow (N=13 valid, 2 timeouts at 300s)

| Seat | Latency mean | Length median | Code blocks median | File refs median |
|---|---|---|---|---|
| **grok-build** | **158s** | **11718** | **0** (9/13 had zero) | 5 |
| gemini | 220s | 4741 | 1 | 5 |
| claudeor | 116s | 4779 | 2 | 29 |
| codex | 111s | 5792 | 1 | 12 |
| opencode | 110s | 5776 | 1 | 14 |
| aichat | 95s | 5580 | 0 | 12 |

Key structural findings:
- Grok was longest on **13/13 councils** (100%) — consistently 2x other seats.
- Grok was slowest on 6/13 councils (46%) — second-slowest after gemini overall.
- 9/13 councils had **zero code blocks** in Grok's response despite being asked to review code.
- 2/15 attempts (13%) hit the 300s timeout — Grok can take >5min on hard prompts.

### Experiment 3 — Blind-rater replay (N=57 councils, 156 agent ratings)

| Metric | Value |
|---|---|
| Score exact match (-1/+1) | 76.9% |
| Top-1 winner agreement (who got the best rating) | **58.9%** |
| Spearman ρ — groundedness | 0.438 |
| Spearman ρ — helpfulness | 0.225 |
| Spearman ρ — correctness | 0.388 |

**Critical bias finding**: 35 of 36 flips went orig +1 → grok -1 (97%). Grok is **systematically stricter** than Claude orchestrator across the board.

**Per-agent flip rate** (asymmetric):
- codex: 11% (6/55)
- aichat: 18% (2/11)
- opencode: 18% (2/11)
- claudeor: 19% (8/43)
- **gemini: 50% (18/36)** — Grok downgrades gemini specifically, far above other seats.

## Decision

| Proposed role | Verdict | Reason |
|---|---|---|
| R1 seat (problem-solver) | **Rejected** | 0 code blocks on 69% of responses; lower file-ref grounding than codex/claudeor/opencode; +30s wall-clock overhead on councils without gemini |
| R2 critique-only seat | Not tested | Verbose narrative style might fit, but separate experiment needed |
| Replace agreement judge | Rejected | Kappa 0.51 — too divergent for direct replacement |
| **Fallback agreement judge** | **Approved (pending implementation)** | 100% recovery rate when current judge fails; off by default until prod shadow validation |
| Replace blind rater | Rejected | Top-1 winner agreement 58.9% (weak); systematic strictness bias; 50% gemini-specific flip rate |
| Ensemble blind rater | Rejected | Dimension correlations 0.22–0.44 (weak-moderate); per-agent bias is non-symmetric |

## Re-test trigger

Re-run the same three scripts if any of the following change materially:

- Grok CLI major version bump (current: v0.2.3) — especially if new default model id appears.
- xAI announces a new coding-tuned model alias.
- Grok adds reasoning_effort / output-format options that materially change response shape.
- Owlex adds a new role (e.g. dedicated R2-critique-only seat) where Grok's verbose-narrative profile might fit better.

Scripts are idempotent (resume-safe via JSONL) and read-only against production DB — re-run cost is ~2 hours of background time, $0 in API charges via subscription.

## Artifacts

```
scripts/shadow_grok_judge.py        — Experiment 1 source
scripts/shadow_grok_seat.py         — Experiment 2 source
scripts/shadow_grok_rater.py        — Experiment 3 source
scripts/shadow_results/agreement_replay.jsonl    — raw E1 data
scripts/shadow_results/agreement_summary.md      — E1 generated summary
scripts/shadow_results/rater_replay.jsonl        — raw E3 data
scripts/shadow_results/rater_summary.md          — E3 generated summary
scripts/shadow_results/seat_r1_responses.jsonl   — raw E2 data
scripts/shadow_results/seat_r1_metrics.md        — E2 generated summary
```
