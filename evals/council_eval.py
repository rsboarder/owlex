"""
Council evaluation pipeline.
Runs council on fixed questions, scores with LLM-as-judge, saves results.

Usage:
    python -m evals.council_eval --working-dir ~/workspace/bookmatcher
    python -m evals.council_eval --dry-run
    python -m evals.council_eval --working-dir ~/workspace/bookmatcher --label baseline
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add owlex root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from owlex.council import Council
from owlex.config import config
from owlex.models import Agent

from evals.judge import score_response, compute_consistency

EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"
QUESTIONS_FILE = EVALS_DIR / "questions.json"

CRITERIA = ["relevance", "specificity", "actionability", "depth", "accuracy"]


def load_questions(path: Path = QUESTIONS_FILE) -> list[dict]:
    with open(path) as f:
        return json.load(f)


async def run_council(question: str, working_dir: str) -> dict:
    """Run a single council deliberation and return agent responses."""
    council = Council()
    response = await council.deliberate(
        prompt=question,
        working_directory=working_dir,
        deliberate=False,  # R1 only for eval
        timeout=120,
    )

    agents = {}
    for agent in Agent:
        r = getattr(response.round_1, agent.value, None)
        if r and r.status == "completed":
            agents[agent.value] = {
                "content": r.content,
                "duration_s": r.duration_seconds,
                "status": r.status,
            }
        elif r:
            agents[agent.value] = {
                "content": None,
                "duration_s": r.duration_seconds,
                "status": r.status,
                "error": r.error,
            }

    return {
        "agents": agents,
        "total_duration_s": response.metadata.total_duration_seconds,
        "log": response.metadata.log,
    }


async def score_all_agents(
    question_text: str,
    expected_topics: list[str],
    agent_responses: dict,
) -> dict:
    """Score all agent responses for a question."""
    scored = {}
    contents = {}

    for agent_name, data in agent_responses.items():
        content = data.get("content")
        if not content:
            scored[agent_name] = {
                **data,
                "scores": {c: 0 for c in CRITERIA},
            }
            scored[agent_name]["scores"]["reasoning"] = f"Agent failed: {data.get('error', 'no content')}"
            continue

        contents[agent_name] = content
        print(f"    Scoring {agent_name}...", end="", flush=True)
        scores = await score_response(question_text, content, expected_topics)
        print(f" done ({scores.get('relevance', '?')}/{scores.get('specificity', '?')}/{scores.get('depth', '?')})")
        scored[agent_name] = {**data, "scores": scores}

    consistency = compute_consistency(contents)
    return scored, consistency


async def run_eval(working_dir: str, label: str = "") -> dict:
    """Run full evaluation pipeline."""
    questions = load_questions()
    timestamp = datetime.now().isoformat()

    print(f"=== Council Eval: {len(questions)} questions ===")
    print(f"Working dir: {working_dir}")
    print(f"Agents: {', '.join(a.value for a in Agent)}")
    print()

    results = {
        "timestamp": timestamp,
        "label": label,
        "config": {
            "working_directory": working_dir,
            "context_injection": False,
            "deliberation": False,
            "agents": [a.value for a in Agent],
            "exclude_agents": list(config.council.exclude_agents),
            "substitution_models": config.council.substitution_models,
        },
        "questions": [],
    }

    all_scores = {c: [] for c in CRITERIA}
    all_consistency = []

    for i, q in enumerate(questions):
        print(f"[{i+1}/{len(questions)}] {q['id']}: {q['question'][:80]}...")

        # Run council
        print("  Running council...", end="", flush=True)
        council_result = await run_council(q["question"], working_dir)
        print(f" done ({council_result['total_duration_s']:.1f}s)")

        # Score responses
        print("  Scoring responses:")
        scored_agents, consistency = await score_all_agents(
            q["question"], q.get("expected_topics", []), council_result["agents"],
        )

        # Aggregate scores
        question_scores = {c: [] for c in CRITERIA}
        for agent_data in scored_agents.values():
            scores = agent_data.get("scores", {})
            for c in CRITERIA:
                v = scores.get(c, 0)
                if v > 0:
                    question_scores[c].append(v)
                    all_scores[c].append(v)

        avg_scores = {c: (sum(v) / len(v) if v else 0) for c, v in question_scores.items()}
        all_consistency.append(consistency)

        question_result = {
            "id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "expected_topics": q.get("expected_topics", []),
            "agents": scored_agents,
            "consistency": consistency,
            "avg_scores": avg_scores,
            "total_duration_s": council_result["total_duration_s"],
        }
        results["questions"].append(question_result)

        print(f"  Avg: rel={avg_scores['relevance']:.1f} spec={avg_scores['specificity']:.1f} "
              f"act={avg_scores['actionability']:.1f} depth={avg_scores['depth']:.1f} "
              f"acc={avg_scores['accuracy']:.1f} | consistency={consistency:.1f}")
        print()

    # Summary
    summary = {
        c: round(sum(v) / len(v), 2) if v else 0
        for c, v in all_scores.items()
    }
    summary["consistency"] = round(sum(all_consistency) / len(all_consistency), 2) if all_consistency else 0
    summary["total_questions"] = len(questions)
    summary["total_agents_scored"] = sum(len(v) for v in all_scores.values()) // len(CRITERIA)
    results["summary"] = summary

    # Print summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for c in CRITERIA:
        print(f"  {c:15s}: {summary[c]:.2f}")
    print(f"  {'consistency':15s}: {summary['consistency']:.2f}")
    print(f"  Total agents scored: {summary['total_agents_scored']}")

    return results


def save_results(results: dict, label: str = "") -> Path:
    """Save results to JSON file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = label or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    return path


async def main():
    parser = argparse.ArgumentParser(description="Council evaluation pipeline")
    parser.add_argument("--working-dir", "-w", default=os.getcwd(),
                        help="Working directory for council (default: cwd)")
    parser.add_argument("--label", "-l", default="",
                        help="Label for results file (default: timestamp)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show questions and exit without running")
    parser.add_argument("--questions", default=str(QUESTIONS_FILE),
                        help="Path to questions JSON file")
    args = parser.parse_args()

    if args.dry_run:
        questions = load_questions(Path(args.questions))
        print(f"Eval questions ({len(questions)}):")
        for q in questions:
            print(f"  [{q['id']}] ({q['category']}) {q['question'][:80]}...")
            print(f"    Expected topics: {', '.join(q.get('expected_topics', []))}")
        print(f"\nWorking dir: {args.working_dir}")
        print(f"Claude CLI: ", end="")
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        print(stdout.decode().strip() if proc.returncode == 0 else "NOT FOUND")
        return

    results = await run_eval(args.working_dir, args.label)
    path = save_results(results, args.label)
    print(f"\nResults saved to: {path}")


if __name__ == "__main__":
    asyncio.run(main())
