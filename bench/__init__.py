"""AUDIT-0 benchmark harness for solution-audit.

Measures BEFORE/AFTER detection quality (precision / recall / detection-rate)
and cost (wall-time / reviewer-count) of the cross-model ``second_opinion``
reviewer against a ground-truth-labeled seeded corpus.

Scope (AUDIT-0): the ``cross_model`` target only — a direct call to
``owlex.second_opinion.get_second_opinion``. The Opus dimension-judge panel and
``council_ask`` are not script-callable (they are Claude Code Agent spawns); a
target seam is left for them but they are out of scope here. See
``bench/README.md`` and ``docs/plans/owlex-audit-hardening.md`` Open Q1.
"""
