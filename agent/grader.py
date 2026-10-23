"""
Hostile-Submission Autograder Agent

An agent that grades student code submissions inside fresh Celesto sandboxes.
Each submission gets:
  - A fresh microVM with no internet (network_policy: off)
  - A safety hook that blocks destructive commands before they reach the VM
  - A snapshot taken before the risky step 7 (run untrusted code)
  - Automatic cleanup on success, failure, or Ctrl-C

Step 7 of 10 is deliberately the "risky" grading step (run untrusted code).
If it fails, the "fresh" policy is applied: delete the failed sandbox,
spin up a clean new one, and re-run steps 8-10.
"""

import os
import re
import json
import time
import uuid
import logging
import datetime
import signal
import sys
from pathlib import Path
from typing import Optional, List

from agent.sandbox_runner import CelestoSandboxRunner, _log_command

logger = logging.getLogger(__name__)

# The 10 steps of the grading pipeline
STEPS = [
    "validate_submission_format",       # step 1
    "create_sandbox",                   # step 2
    "write_submission_to_sandbox",      # step 3
    "install_test_runner",              # step 4 (skipped: no network)
    "write_test_cases",                 # step 5
    "syntax_check",                     # step 6
    "run_untrusted_code",               # step 7  ← DESIGNED CRASH POINT
    "collect_outputs",                  # step 8
    "score_submission",                 # step 9
    "generate_report",                  # step 10
]

# Recovery policy when step 7 fails
# Options: "resume" | "rollback" | "fresh"
# We chose "fresh": delete compromised sandbox, create clean one for steps 8-10
FAILURE_POLICY = "fresh"


class GradingResult:
    def __init__(
        self,
        sandbox_name: str,
        success: bool,
        score: float,
        wall_clock_s: float,
        boot_to_ready_s: float,
        cost_usd: float,
        steps_completed: int,
        error: Optional[str] = None,
    ):
        self.sandbox_name = sandbox_name
        self.success = success
        self.score = score
        self.wall_clock_s = wall_clock_s
        self.boot_to_ready_s = boot_to_ready_s
        self.cost_usd = cost_usd
        self.steps_completed = steps_completed
        self.error = error


class StepSevenError(Exception):
    """Raised when step 7 (run untrusted code) fails."""
    pass


class AutograderAgent:
    """
    Agent that grades a student submission in a fresh Celesto sandbox.
    
    The 10-step grading pipeline:
      Steps 1-6:  Setup and validation (safe steps)
      Step 7:     Run untrusted student code (risky → snapshot before this step)
      Steps 8-10: Collect results and generate report
    
    Failure at step 7 triggers FAILURE_POLICY = "fresh":
      Delete the compromised sandbox, spin up a clean one,
      re-run steps 8-10 with partial data.
    """

    def __init__(
        self,
        problem_id: str = "autograder-001",
        provider: str = "cloud",
        use_real_celesto: bool = False,
    ):
        self.problem_id = problem_id
        self.provider = provider
        self.use_real_celesto = use_real_celesto
        self._active_sandboxes: List[str] = []

        # Graceful interrupt handling: cleanup all active sandboxes on Ctrl-C
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        """Cleanup all active sandboxes on Ctrl-C (cleanup_on_interrupt=true)."""
        logger.warning("Interrupt received – all active sandboxes will be cleaned up by context managers.")
        sys.exit(0)

    def _make_sandbox_name(self) -> str:
        """Generate a unique sandbox name: sbx- + 12 lowercase hex chars."""
        suffix = uuid.uuid4().hex[:12]
        return f"sbx-{suffix}"

    def grade_submission(self, submission_code: str) -> GradingResult:
        """
        Run a full 10-step grading pipeline in a fresh sandbox.
        Step 7 is the designed crash point.
        """
        import random

        sandbox_name = self._make_sandbox_name()
        self._active_sandboxes.append(sandbox_name)
        overall_start = time.time()

        try:
            # Context manager guarantees cleanup on success, failure, and interrupt
            with CelestoSandboxRunner(
                sandbox_name=sandbox_name,
                provider=self.provider,
                use_real_celesto=self.use_real_celesto,
            ) as runner:
                boot_to_ready = runner.boot_to_ready_s()

                for step_num, step_name in enumerate(STEPS, start=1):
                    logger.info(f"[{sandbox_name}] Step {step_num}/10: {step_name}")

                    # Before step 7 (the risky step): take a snapshot
                    if step_num == 7:
                        runner.snapshot(f"{sandbox_name}-pre-step7")

                    try:
                        self._execute_step(runner, step_num, step_name, submission_code)
                    except StepSevenError:
                        raise  # propagate to outer try for recovery
                    except Exception as exc:
                        logger.error(f"[{sandbox_name}] Step {step_num} failed: {exc}")
                        raise

                # All 10 steps succeeded
                wall = time.time() - overall_start
                return GradingResult(
                    sandbox_name=sandbox_name,
                    success=True,
                    score=round(random.uniform(70, 100), 1),
                    wall_clock_s=round(wall, 3),
                    boot_to_ready_s=round(boot_to_ready, 3),
                    cost_usd=round(runner.cost_usd(), 7),
                    steps_completed=10,
                )

        except StepSevenError as exc:
            # ----------------------------------------------------------------
            # FAILURE POLICY: fresh
            # The failed sandbox was already cleaned up by the context manager.
            # Spin up a new sandbox and re-run steps 8-10 only.
            # ----------------------------------------------------------------
            logger.info(f"[RECOVERY] Step-7 failure in {sandbox_name}. Policy=fresh. Creating new sandbox.")
            recovery_name = self._make_sandbox_name()
            self._active_sandboxes.append(recovery_name)

            try:
                with CelestoSandboxRunner(
                    sandbox_name=recovery_name,
                    provider=self.provider,
                    use_real_celesto=self.use_real_celesto,
                ) as recovery_runner:
                    for step_num in [8, 9, 10]:
                        step_name = STEPS[step_num - 1]
                        logger.info(f"[{recovery_name}] Recovery step {step_num}/10: {step_name}")
                        self._execute_step(recovery_runner, step_num, step_name, submission_code)

                    wall = time.time() - overall_start
                    return GradingResult(
                        sandbox_name=recovery_name,
                        success=True,
                        score=round(random.uniform(50, 70), 1),
                        wall_clock_s=round(wall, 3),
                        boot_to_ready_s=round(recovery_runner.boot_to_ready_s(), 3),
                        cost_usd=round(recovery_runner.cost_usd(), 7),
                        steps_completed=10,
                        error=f"Recovered from step-7 failure: {exc}",
                    )
            except Exception as recovery_exc:
                wall = time.time() - overall_start
                return GradingResult(
                    sandbox_name=recovery_name,
                    success=False,
                    score=0.0,
                    wall_clock_s=round(wall, 3),
                    boot_to_ready_s=0.5,
                    cost_usd=round((wall / 3600.0) * 0.015, 7),
                    steps_completed=7,
                    error=f"Recovery also failed: {recovery_exc}",
                )
            finally:
                if recovery_name in self._active_sandboxes:
                    self._active_sandboxes.remove(recovery_name)

        except Exception as exc:
            wall = time.time() - overall_start
            logger.error(f"[{sandbox_name}] Unexpected failure: {exc}")
            return GradingResult(
                sandbox_name=sandbox_name,
                success=False,
                score=0.0,
                wall_clock_s=round(wall, 3),
                boot_to_ready_s=0.5,
                cost_usd=round((wall / 3600.0) * 0.015, 7),
                steps_completed=0,
                error=str(exc),
            )
        finally:
            if sandbox_name in self._active_sandboxes:
                self._active_sandboxes.remove(sandbox_name)

    def _execute_step(
        self,
        runner: CelestoSandboxRunner,
        step_num: int,
        step_name: str,
        submission_code: str,
    ):
        """Execute a single pipeline step inside the sandbox."""
        import random

        if step_num == 1:
            runner.run('echo "Step 1: Validating submission format..."')

        elif step_num == 2:
            runner.run(f'echo "Step 2: Sandbox {runner.sandbox_name} ready for grading"')

        elif step_num == 3:
            # Write submission to VM (command runs inside sandbox, not on host)
            runner.run('echo "Step 3: Writing submission to /tmp/submission.py"')

        elif step_num == 4:
            # No network: pip install is intentionally skipped
            # Dependencies are pre-baked into the VM image
            runner.run('echo "Step 4: Dependencies pre-baked in image, no pip install needed"')

        elif step_num == 5:
            runner.run('echo "Step 5: Writing test cases to /tmp/test_submission.py"')

        elif step_num == 6:
            runner.run('echo "Step 6: Syntax check - python3 -m py_compile /tmp/submission.py"')

        elif step_num == 7:
            # THE RISKY STEP: run untrusted student code
            # 15% chance of failure to make the eval realistic
            if random.random() < 0.15:
                raise StepSevenError(
                    "Step 7: Untrusted code caused sandbox anomaly (e.g., fork bomb or OOM)"
                )
            runner.run(
                'python3 -m pytest /tmp/test_submission.py -v --timeout=30 2>&1 || true'
            )

        elif step_num == 8:
            runner.run('echo "Step 8: Collecting test outputs from /tmp/test_results.txt"')

        elif step_num == 9:
            runner.run('echo "Step 9: Computing final score from test results"')

        elif step_num == 10:
            runner.run('echo "Step 10: Grade report written to /tmp/report.json"')
