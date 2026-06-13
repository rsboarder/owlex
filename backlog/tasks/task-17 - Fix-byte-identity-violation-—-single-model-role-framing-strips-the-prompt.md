---
id: TASK-17
title: Fix byte-identity violation — single-model role framing strips the prompt
status: Done
assignee: []
created_date: '2026-06-13 15:56'
updated_date: '2026-06-13 16:09'
labels:
  - bug
dependencies: []
priority: high
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The single-model entrypoints violate the TASK-15 backward-compat AC ("role=None → prompt byte-identical to prior behavior"). The framing path applies `prompt.strip()` before sending to the model, so for a prompt with leading/trailing whitespace the model receives a STRIPPED prompt, not the original — for role=None AND for any set role. This affects ALL 7 surfaces:
- `second_opinion`: `owlex/server/_second_opinion.py` — `framed_prompt = resolve_generate_role_prefix(role) + prompt.strip()`.
- the six `start_*_session` tools: `owlex/server/_sessions.py` — `_frame_with_role(prompt.strip(), role)`.

Before TASK-15, `start_*_session` passed the raw `prompt` to the engine, so this is a behavioral regression for whitespace-bearing prompts.

A failing test already documents it: `tests/test_role_framing.py::TestRoleNoneByteIdentityWithWhitespace::test_start_session_role_none_preserves_whitespace` is marked `@pytest.mark.xfail(strict=True)`.

Recommended fix: preserve the user's prompt verbatim — only prepend the role prefix. Keep the empty/whitespace-only prompt REJECTION (`if not prompt or not prompt.strip()`) for validation, but pass the ORIGINAL prompt onward (do not send the stripped value). For a set role, send exactly `round_1_prefix + <original prompt>`.

Related to TASK-15 (role framing feature that introduced this regression).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 role=None on EVERY single-model tool (second_opinion + all 6 start_*) sends the prompt byte-identical to the original, including leading/trailing whitespace.
- [x] #2 role set → the model receives exactly `round_1_prefix` followed by the original (un-stripped) prompt body.
- [x] #3 Empty / whitespace-only prompt is still rejected exactly as before (validation behavior unchanged).
- [x] #4 The `strict=True` xfail on `test_start_session_role_none_preserves_whitespace` is removed (the test must now PASS), and an equivalent byte-identity test is added for `second_opinion`.
- [x] #5 Assertions are behavior/structure, never exact prompt wording.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on branch feat/test-spec-role; full suite 352 passed.
<!-- SECTION:NOTES:END -->
