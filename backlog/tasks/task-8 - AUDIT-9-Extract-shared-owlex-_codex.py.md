---
id: TASK-8
title: 'AUDIT-9: Extract shared owlex/_codex.py'
status: To Do
assignee: []
created_date: '2026-06-10 15:36'
labels:
  - audit-hardening
dependencies: []
priority: low
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** `second_opinion._cmd` ≈ `agreement._build_judge_command` (both build a `codex exec … --sandbox read-only … -` argv) AND `_terminate(proc)` is byte-identical in `owlex/second_opinion.py` and `owlex/agreement.py` — a 2×2 duplication cluster. The audit deferred extraction to "when a 3rd consumer appears," but the cluster already exists.

**Change:** extract `owlex/_codex.py` with `build_codex_exec_argv(model, reasoning, *, json=False, sandbox="read-only", cwd=None, skip_git_repo_check=True)` + `terminate(proc)`; route `second_opinion.py` and `agreement.py` through it. Behavior byte-identical (each call site's generated argv unchanged).

**NOTE:** touches `_cmd` — sequence after AUDIT-2/AUDIT-5 if they change `_cmd`/FRAME.

**Refs:** docs/plans/owlex-audit-hardening.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 both modules import the shared helpers
- [ ] #2 generated argv for each existing call site identical to before (assert in a test)
- [ ] #3 full suite green (303+)
- [ ] #4 Benchmark success: duplication eliminated (one source of truth), argv byte-identical, zero behavior change, suite green
<!-- AC:END -->
