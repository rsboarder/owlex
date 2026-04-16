"""
Compare two eval results to measure improvement.

Usage:
    python -m evals.compare evals/results/baseline.json evals/results/with-context.json
"""

import json
import sys
from pathlib import Path

CRITERIA = ["relevance", "specificity", "actionability", "depth", "accuracy", "consistency"]


def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def compare(before: dict, after: dict):
    b_summary = before["summary"]
    a_summary = after["summary"]

    b_label = before.get("label") or Path(sys.argv[1]).stem
    a_label = after.get("label") or Path(sys.argv[2]).stem

    print(f"=== Council Eval Comparison ===")
    print(f"Before: {b_label} ({before['timestamp'][:19]})")
    print(f"After:  {a_label} ({after['timestamp'][:19]})")
    print(f"Context injection: {before['config'].get('context_injection', False)} → {after['config'].get('context_injection', False)}")
    print()

    # Overall scores
    print(f"{'Criterion':15s}  {'Before':>7s}  {'After':>7s}  {'Delta':>7s}  {'Change':>8s}")
    print("-" * 52)

    improvements = 0
    regressions = 0

    for c in CRITERIA:
        b_val = b_summary.get(c, 0)
        a_val = a_summary.get(c, 0)
        delta = a_val - b_val
        if b_val > 0:
            pct = (delta / b_val) * 100
            change = f"{pct:+.1f}%"
        else:
            change = "n/a"

        indicator = ""
        if delta > 0.1:
            indicator = " ↑"
            improvements += 1
        elif delta < -0.1:
            indicator = " ↓"
            regressions += 1

        print(f"{c:15s}  {b_val:7.2f}  {a_val:7.2f}  {delta:+7.2f}  {change}{indicator}")

    print()

    # Per-question breakdown
    print("Per-question specificity (the key metric for context injection):")
    print(f"{'Question':12s}  {'Before':>7s}  {'After':>7s}  {'Delta':>7s}")
    print("-" * 42)

    b_questions = {q["id"]: q for q in before["questions"]}
    a_questions = {q["id"]: q for q in after["questions"]}

    for qid in b_questions:
        if qid not in a_questions:
            continue
        b_spec = b_questions[qid]["avg_scores"].get("specificity", 0)
        a_spec = a_questions[qid]["avg_scores"].get("specificity", 0)
        delta = a_spec - b_spec
        indicator = " ↑" if delta > 0.2 else (" ↓" if delta < -0.2 else "")
        print(f"{qid:12s}  {b_spec:7.1f}  {a_spec:7.1f}  {delta:+7.1f}{indicator}")

    print()

    # Per-agent breakdown
    print("Per-agent average scores:")
    agent_scores_before = {}
    agent_scores_after = {}

    for q in before["questions"]:
        for agent, data in q["agents"].items():
            scores = data.get("scores", {})
            agent_scores_before.setdefault(agent, []).append(
                sum(scores.get(c, 0) for c in CRITERIA[:5]) / 5
            )

    for q in after["questions"]:
        for agent, data in q["agents"].items():
            scores = data.get("scores", {})
            agent_scores_after.setdefault(agent, []).append(
                sum(scores.get(c, 0) for c in CRITERIA[:5]) / 5
            )

    print(f"{'Agent':12s}  {'Before':>7s}  {'After':>7s}  {'Delta':>7s}")
    print("-" * 42)

    for agent in sorted(set(list(agent_scores_before.keys()) + list(agent_scores_after.keys()))):
        b_vals = agent_scores_before.get(agent, [])
        a_vals = agent_scores_after.get(agent, [])
        b_avg = sum(b_vals) / len(b_vals) if b_vals else 0
        a_avg = sum(a_vals) / len(a_vals) if a_vals else 0
        delta = a_avg - b_avg
        indicator = " ↑" if delta > 0.2 else (" ↓" if delta < -0.2 else "")
        print(f"{agent:12s}  {b_avg:7.2f}  {a_avg:7.2f}  {delta:+7.2f}{indicator}")

    print()

    # Verdict
    if improvements > regressions and improvements >= 3:
        print("✓ IMPROVED — context injection helps on most criteria")
    elif regressions > improvements and regressions >= 3:
        print("✗ REGRESSED — context injection hurts on most criteria")
    else:
        print("~ MIXED — no clear improvement, consider adjusting context strategy")


def main():
    if len(sys.argv) != 3:
        print("Usage: python -m evals.compare <before.json> <after.json>")
        sys.exit(1)

    before = load_results(sys.argv[1])
    after = load_results(sys.argv[2])
    compare(before, after)


if __name__ == "__main__":
    main()
