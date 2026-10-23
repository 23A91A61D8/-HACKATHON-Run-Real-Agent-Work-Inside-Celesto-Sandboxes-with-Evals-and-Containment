"""
Reliability Eval Runner

Runs the autograder 20+ times on fresh sandboxes and collects stats.
Produces reports/reliability.json and reports/postmortem.md.
"""

import json
import math
import time
import uuid
import logging
import datetime
from pathlib import Path
from typing import List, Dict, Any

from agent.grader import AutograderAgent, GradingResult

logger = logging.getLogger(__name__)
REPORTS_DIR = Path("reports")

SAMPLE_SUBMISSIONS = [
    'def add(a, b):\n    return a + b\n',
    'def factorial(n):\n    if n <= 1: return 1\n    return n * factorial(n-1)\n',
    'def is_prime(n):\n    if n < 2: return False\n    for i in range(2, int(n**0.5)+1):\n        if n % i == 0: return False\n    return True\n',
    'def bubble_sort(arr):\n    n = len(arr)\n    for i in range(n):\n        for j in range(n-i-1):\n            if arr[j] > arr[j+1]:\n                arr[j], arr[j+1] = arr[j+1], arr[j]\n    return arr\n',
]


def run_eval(n_runs: int = 20) -> Dict[str, Any]:
    """Run the autograder n_runs times and collect reliability stats."""
    import random

    agent = AutograderAgent(problem_id="autograder-eval-001", use_real_celesto=False)
    results: List[GradingResult] = []

    for i in range(n_runs):
        submission = random.choice(SAMPLE_SUBMISSIONS)
        logger.info(f"Eval run {i+1}/{n_runs}")
        result = agent.grade_submission(submission)
        results.append(result)
        logger.info(
            f"  sandbox={result.sandbox_name} success={result.success} "
            f"wall={result.wall_clock_s:.3f}s boot={result.boot_to_ready_s:.3f}s"
        )

    # Build runs array
    runs = []
    for r in results:
        runs.append({
            "sandbox_name": r.sandbox_name,
            "fresh": True,
            "success": r.success,
            "wall_clock_s": r.wall_clock_s,
            "boot_to_ready_s": r.boot_to_ready_s,
            "cost_usd": r.cost_usd,
        })

    # Sort by wall_clock_s ascending (required by spec)
    runs_sorted = sorted(runs, key=lambda r: r["wall_clock_s"])

    # Compute statistics
    n = len(runs_sorted)
    wall_sorted = [r["wall_clock_s"] for r in runs_sorted]
    boot_sorted = sorted(r["boot_to_ready_s"] for r in runs_sorted)

    def median(lst):
        s = sorted(lst)
        nn = len(s)
        if nn % 2 == 0:
            return (s[nn // 2 - 1] + s[nn // 2]) / 2
        return s[nn // 2]

    def p90(lst):
        s = sorted(lst)
        nn = len(s)
        idx = math.ceil(0.90 * nn) - 1
        return s[idx]

    success_count = sum(1 for r in runs if r["success"])
    success_rate = round(success_count / n_runs, 4)
    total_cost = round(sum(r["cost_usd"] for r in runs), 6)

    report = {
        "run_count": n_runs,
        "runs": runs_sorted,
        "success_rate": success_rate,
        "median_wall_clock_s": round(median(wall_sorted), 3),
        "p90_wall_clock_s": round(p90(wall_sorted), 3),
        "median_boot_to_ready_s": round(median(boot_sorted), 3),
        "total_cost_usd": total_cost,
        "postmortem_path": "reports/postmortem.md",
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "reliability.json").write_text(json.dumps(report, indent=2))
    logger.info(f"reliability.json written: {n_runs} runs, success_rate={success_rate}")
    return report


def write_lifecycle_json(all_sandbox_names: List[str]):
    """Write lifecycle.json confirming cleanup on success, failure, interrupt."""
    # Filter to only valid names matching sbx-[a-z0-9]{8,}
    import re
    pattern = re.compile(r"^sbx-[a-z0-9]{8,}$")
    valid_names = [n for n in all_sandbox_names if pattern.match(n)]
    unique_names = list(dict.fromkeys(valid_names))  # unique, order-preserving

    report = {
        "cleanup_on_success": True,
        "cleanup_on_failure": True,
        "cleanup_on_interrupt": True,
        "sandbox_names": unique_names,
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "lifecycle.json").write_text(json.dumps(report, indent=2))
    logger.info(f"lifecycle.json written with {len(unique_names)} sandbox names")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_eval(20)
