---
id: TASK-16
title: council_ask R1 output leaks agent identity — undermines blind rating
status: Done
assignee: []
created_date: '2026-06-13 15:45'
updated_date: '2026-06-13 16:09'
labels:
  - security
dependencies: []
priority: medium
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
owlex anonymizes round-1 council responses as "Response A/B/C…" specifically so that rate_council is a BLIND quality signal (prevents self-preference and brand bias). However, the payload returned by council_ask also contains a per-agent timing/metadata section that names the actual agents (codex, gemini, opencode, claudeor, aichat, cursor) in completion order. A reader (human or downstream agent) can correlate that timing/order section with the lettered responses and de-anonymize letters → agent names, defeating the blind-rating guarantee.

Observed concretely on 2026-06-13: council id 192846 was processed by a downstream summarizer which recovered the full letter→agent mapping purely from the timing metadata embedded in the council_ask result — without any privileged access.

The rater-facing R1 payload must not let a reader map lettered responses back to specific agent seats. Either omit the agent-named timing section from the result surfaced for rating, or key all timing/metadata by the anonymized letter (and only) so identity cannot be recovered until after rating.

Code anchors: owlex/prompts.py (anonymize_round_responses, assign_labels), owlex/anonymize.py, owlex/council.py (R1 assembly + timing), owlex/server/_council.py (council_ask result payload). Note there is a separate agent_timing tool — that's fine; the issue is timing identity bleeding into the council_ask result itself.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The R1 result payload returned by council_ask does not expose any mapping from response letters to agent seat names (no per-agent-named timing alongside lettered responses).
- [x] #2 If timing is retained in that payload, it is keyed by anonymized letter only (or omitted), so letters cannot be re-identified pre-rating.
- [x] #3 A test asserts that no agent seat name (codex/gemini/opencode/claudeor/aichat/cursor) appears in the rater-facing R1 payload in a way correlatable with the lettered responses. Assert behavior/structure, not exact wording.
- [x] #4 The blind-rating intent is documented near the anonymization code so the property isn't silently regressed later.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on branch feat/test-spec-role; full suite 352 passed.
<!-- SECTION:NOTES:END -->
