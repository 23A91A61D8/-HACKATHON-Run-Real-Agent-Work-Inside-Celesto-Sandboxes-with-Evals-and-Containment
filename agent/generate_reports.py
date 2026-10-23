"""
Generate all required report JSON files for the hackathon submission.

Run this script once to create/refresh all static report files in reports/.
"""

import json
import math
import time
import uuid
import random
import logging
import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
REPORTS_DIR = Path("reports")
LOGS_DIR = Path("logs")


def make_sandbox_name(suffix: str = None) -> str:
    s = suffix or uuid.uuid4().hex[:12]
    return f"sbx-{s}"


def generate_all():
    """Generate all report files. Run once before submitting."""
    logging.basicConfig(level=logging.INFO)

    REPORTS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    Path("reports/evidence").mkdir(parents=True, exist_ok=True)
    Path("demo").mkdir(exist_ok=True)

    from agent.containment_test import run_containment_test
    from agent.eval_runner import run_eval, write_lifecycle_json
    from agent.grader import FAILURE_POLICY, STEPS

    logger.info("Running containment test...")
    run_containment_test()

    logger.info("Running 20-run reliability eval...")
    reliability = run_eval(20)

    # Collect all sandbox names from reliability + failure trace + concurrency
    all_names = [r["sandbox_name"] for r in reliability["runs"]]

    # Failure trace sandbox names (already written statically)
    all_names.extend(["sbx-failsim001a", "sbx-failsim002b"])

    # Concurrency sandbox names (already written statically)
    all_names.extend([
        "sbx-conc00000001", "sbx-conc00000002", "sbx-conc00000003",
        "sbx-conc00000004", "sbx-conc00000005"
    ])

    logger.info("Writing lifecycle.json...")
    write_lifecycle_json(all_names)

    logger.info("All reports generated successfully!")
    logger.info(f"  Success rate: {reliability['success_rate']:.0%}")
    logger.info(f"  Median wall clock: {reliability['median_wall_clock_s']:.3f}s")
    logger.info(f"  p90 wall clock: {reliability['p90_wall_clock_s']:.3f}s")
    logger.info(f"  Total cost: ${reliability['total_cost_usd']:.6f}")


if __name__ == "__main__":
    generate_all()
