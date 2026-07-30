# A/B benchmark — Grok seat harness improvements

**Status:** **SHIPPED Phase-0** (2026-07-30) — prod defaults on in `owlex/agents/grok.py` + `GrokConfig`  
**Script:** `scripts/ab_grok_harness.py`  
**Evidence:** N=5 rated, win_rate_B=0.8, delta_help=+0.8, all G1–G5 pass (`scripts/shadow_results/ab_grok_baseline_vs_contract_strip.md`)  
**Date:** 2026-07-30

## Why

Proposed Grok seat improvements (output contract, tool-narration strip, optional R2 effort, richer context) must not ship on vibes. Live seat quality is already high (~4.8 helpfulness); we need a **paired A/B** that detects:

- quality regressions (non-inferiority),
- latency wins/losses,
- structural cleanliness (verdict-first, less tool monologue).

This is **not** the 3-experiment seat-candidacy protocol (`shadow-replay-protocol-for-seat-evaluation.md`). That protocol decides *whether a model may sit*. This benchmark decides *whether a harness change for an already-shipped seat is better*.

## Corpus

Source: historical councils in `~/.owlex/owlex.db` (read-only URI).

| Filter | Rationale |
|--------|-----------|
| `calls.agent='aichat' AND model LIKE 'grok%'` R1 completed | Same product seat we will change |
| Sibling `codex` R1 row with `length(prompt_text) > 500` | aichat/grok rows often have empty `prompt_text`; codex stores the shared council prompt |
| Prefer last 60d, order by `completed_at` desc | Distribution matches current workload |

Each item:

```json
{
  "council_id": "...",
  "prompt": "<full council R1 prompt>",
  "baseline_prod": {
    "duration_s": 203.0,
    "result_preview": "..."
  }
}
```

~60 eligible items as of 2026-07-30. Default run size: **N=20** (wall ~2h at ~3min/call × 2 arms); smoke: **N=3**.

## Arms

An arm is a pure harness profile applied to the **same** prompt + model id.

| Arm id | Effort | Output contract suffix | Strip tool narration (post) | Notes |
|--------|--------|------------------------|-----------------------------|-------|
| `baseline` | low | no | no | Production mirror |
| `contract` | low | yes | no | Prompt-only treatment |
| `strip` | low | no | yes | Post-process only (can derive from `baseline` raw) |
| `contract_strip` | low | yes | yes | Combined Phase-0 proposal |
| `effort_medium` | medium | no | no | Effort A/B alone |

Default comparison: **`baseline` vs `contract_strip`** (the recommended ship package).

Flags must not change model id (`OWLEX_GROK_MODEL`, default `grok-4.5`).

## Metrics

### Structural (always, free after generation)

| Metric | Definition | Direction |
|--------|------------|-----------|
| `elapsed_s` | wall time of `grok` subprocess | lower better |
| `raw_chars` / `clean_chars` | length before/after clean | — |
| `preamble_chars` | chars before first `^#{1,3}\s` or `**Verdict**` | lower better |
| `toolish_start` | bool: starts with I'll/Reading/… | lower rate better |
| `has_verdict` | verdict / executive summary / recommendation marker | higher better |
| `code_blocks` | fenced blocks | higher usually better (proxy) |
| `file_refs` | `path.ext` / `path.ext:line` | higher usually better (proxy) |
| `contract_ok` | starts with Verdict-like + has Risks + Recommendation sections | higher better |

### Quality (optional, costs 1 codex judge call per council)

Blind pairwise: anonymize arm A vs arm B, rate with same schema as `claude_blind` dimensions via codex/`OWLEX_AGREEMENT_MODEL`.

| Metric | Definition |
|--------|------------|
| `win_rate_b` | fraction of councils where B score > A (ties split 0.5) |
| `delta_help` | mean(help_B − help_A) |
| `delta_ground` / `delta_correct` | same |
| `accept_rate` | fraction score=+1 per arm |

### Reliability

| Metric | Definition |
|--------|------------|
| `error_rate` | timeouts / non-zero exits |
| `parse_fail_rate` | empty text after JSON parse |

## Procedure

```bash
# 1) Smoke (3 councils, structural only)
python scripts/ab_grok_harness.py --arm-a baseline --arm-b contract_strip --limit 3

# 2) Full structural A/B (default N=20)
python scripts/ab_grok_harness.py --arm-a baseline --arm-b contract_strip --limit 20

# 3) Add blind quality (after structural looks sane)
python scripts/ab_grok_harness.py --arm-a baseline --arm-b contract_strip --limit 20 --rate

# 4) Effort-only check (separate)
python scripts/ab_grok_harness.py --arm-a baseline --arm-b effort_medium --limit 15 --rate
```

Properties:

- DB opened `mode=ro` only.
- Resume-safe JSONL per arm pair under `scripts/shadow_results/ab_grok_<a>_vs_<b>.jsonl`.
- Markdown summary `scripts/shadow_results/ab_grok_<a>_vs_<b>.md`.
- `--cwd` optional working directory for tool use (default: empty temp; prompts already carry PROJECT CONTEXT).

## Ship gates (pre-registered — do not move after seeing results)

For **`contract_strip` vs `baseline`** to ship Phase-0:

| Gate | Rule |
|------|------|
| G1 Reliability | `error_rate_B ≤ error_rate_A + 0.05` and absolute `error_rate_B ≤ 0.10` |
| G2 Quality non-inferior | if `--rate`: `win_rate_B ≥ 0.45` **and** `delta_help ≥ −0.25` |
| G3 Quality useful **or** latency win | if `--rate`: `win_rate_B ≥ 0.55` **or** `delta_help ≥ +0.15` **or** (G2 and `median_elapsed_B ≤ 0.90 * median_elapsed_A`) |
| G4 Cleanliness | `mean(preamble_chars_B) ≤ 0.5 * mean(preamble_chars_A)` **or** `toolish_start_B ≤ 0.5 * toolish_start_A` |
| G5 No verbosity explosion | `median(clean_chars_B) ≤ 1.25 * median(clean_chars_A)` |

**Ship** only if G1 ∧ G2 ∧ G4 ∧ G5 ∧ G3.  
**Iterate** (do not ship) if G2 fails.  
**Ship as latency/cleanliness-only** only if G1∧G2∧G4∧G5 and G3 via latency clause.

For **`effort_medium` vs `baseline`**: same gates, but G3 requires quality win (`win_rate ≥ 0.55` or `delta_help ≥ +0.15`); pure latency win is **not** enough (medium is expected slower).

## What this does *not* measure

- Full multi-seat council wall clock (only single-seat R1 replay).
- R2 position delta (needs paired R2 prompts; phase-2 extension).
- Orchestrator-side context-pack changes (different prompt distribution; add arm `context_pack` later when pack builder exists).
- Production `claude_blind` rater (uses codex judge as stand-in; good for relative A/B, not absolute parity with dashboard).

## Extension points

1. `context_pack` arm: rewrite prompt via a pure function `pack_prompt(prompt) -> prompt` once implemented.
2. R2 arm: load deliberation prompts from `round=2` codex rows when present.
3. Multi-arm tournament: generate once per unique (effort, contract), derive strip post-hoc to save calls.

## Relation to production code

Until gates pass, **do not** change `owlex/agents/grok.py` production defaults. The script inlines harness variants so A/B never requires flipping prod env mid-experiment.
