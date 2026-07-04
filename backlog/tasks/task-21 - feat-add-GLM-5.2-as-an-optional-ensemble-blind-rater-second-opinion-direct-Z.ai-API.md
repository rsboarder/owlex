---
id: TASK-21
title: >-
  feat: add GLM-5.2 as an optional ensemble blind-rater (second-opinion, direct
  Z.ai API)
status: Done
assignee: []
created_date: '2026-06-17 20:54'
updated_date: '2026-06-17 21:21'
labels: []
dependencies: []
priority: high
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add GLM-5.2 (Z.ai) as an OPTIONAL, off-by-default SECONDARY blind rater that runs ALONGSIDE the existing `claude_blind` orchestrator-rater — an independent "stricter second opinion" to flag weak answers. Based on the 2026-06-18 shadow evaluation: docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md

Why this is the highest-value GLM role:
- In the blind-rater shadow, GLM-5.2 reached 88% (default reasoning) → 94% (reasoning=max) binary accept/reject agreement with the existing claude_blind rater, with NO per-agent bias (per-seat flip rate 8-19%, no concentration) — unlike Grok, which was rejected for a 50% gemini-specific flip rate. So GLM gives an unbiased, uniformly-stricter independent check.
- It is a BINARY second opinion (accept/reject + flag weak answers), NOT a replacement for claude_blind: GLM's graded 1-5 dimension correlations are weak (Spearman ~0.13-0.29) and its winner-ranking agreement is modest, so its fine-grained dimension scores are advisory only.
- Unlike the seat role (TASK-19/20), the rater does NOT use opencode and adds no council latency — it is a one-shot text->JSON call, so it is cheap and low-risk.

Integration path (direct Z.ai API, NOT opencode, NOT a seat):
- Reuse the verified client scripts/_glm_client.py (call_glm with reasoning="max" → sends thinking{enabled} + reasoning_effort:max to the Z.ai Anthropic-compatible endpoint https://api.z.ai/api/anthropic). Token from ~/.owlex/glm_token, model glm-5.2.
- Reference shadow implementation: scripts/shadow_glm_rater.py (it anonymizes R1 responses with the same deterministic salt 'blind:{council_id}' the existing rater uses, then rates letters).
- owlex already has a blind-rating pipeline (agent_scores table, council_anonymization, rate_council_blind). The GLM rater should write a parallel set of agent_scores under a distinct rater id (e.g. 'glm_blind') so it can be compared to claude_blind for ensemble/integrity, not overwrite it.
- Keep all GLM config isolated to ~/.owlex/glm_token + the direct API; do NOT modify ~/.claude/settings.json or set global ANTHROPIC_* env.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GLM-5.2 can be enabled (opt-in, off by default) as an additional blind rater that produces per-agent scores under a distinct rater id (e.g. 'glm_blind'), running via the direct Z.ai Anthropic-compatible API at reasoning=max — alongside, not replacing, the existing claude_blind rater.
- [ ] #2 The GLM rater sees the SAME anonymization as claude_blind (deterministic salt 'blind:{council_id}') so its per-agent scores are directly comparable.
- [ ] #3 GLM's role is treated as a binary second opinion (accept/reject + weak-answer flag); its 1-5 dimension scores are stored but treated as advisory (weak correlation), not authoritative.
- [ ] #4 Enabling/disabling the GLM rater does not affect the existing claude_blind ratings or council latency.
- [ ] #5 No changes to ~/.claude/settings.json or to global ANTHROPIC_* environment variables; token read from ~/.owlex/glm_token.
- [ ] #6 python -m pytest tests/ -q remains green.
- [ ] #7 The task notes / PR link the eval doc (docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md) and reuse scripts/_glm_client.py + scripts/shadow_glm_rater.py patterns.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
This is the role the GLM-5.2 evaluation judged highest-value-per-cost (distinct unbiased signal, no latency). Independent of the seat work (TASK-19/TASK-20).

Implemented as a background derivation: owlex/glm_client.py (NEW, httpx), GlmBlindEvent + _handle_glm_blind in derivations.py, GlmBlindConfig in config.py, gated emit in council.py, optional startup probe in server/__init__.py, tests/test_glm_blind.py (9 tests). Code-reviewed: approved, low risk, 0 P1/P2, 4 P3s: (1) the single derivations worker serializes a ~100s GLM call — bounded by 120s timeout + off-by-default + backfill-recoverable; before flipping ON at scale, give glm_blind its own queue/worker so it doesn't delay pairwise/skills writes; (2) add a score-coercion test; (3) add a fenced-JSON parse test; (4) align owlex/agents/opencode.py GLM token read to honor OWLEX_HOME (TASK-19 scope). 392 tests green. Uncommitted in working tree. Enable: OWLEX_GLM_BLIND_ENABLED=1 + ~/.owlex/glm_token.
<!-- SECTION:NOTES:END -->
