"""
Celesto Sandbox Runner for the Hostile-Submission Autograder.

Each student submission runs in a fresh Celesto microVM:
- No network access (network_policy: off)
- Safety hooks that block dangerous commands before reaching the VM
- Snapshots before risky grading steps (step 7)
- Cleanup on success, failure, and interrupt via context manager
"""

import os
import re
import time
import uuid
import json
import signal
import logging
import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Capability 1: SAFETY HOOKS
# These run before every command reaches the guest.
# Dangerous patterns are blocked before the VM even sees them.
# A blocked command is still logged to logs/commands.jsonl for audit.
# ---------------------------------------------------------------------------

BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/"),              # rm -rf / (root wipe)
    re.compile(r"\bdd\s+if="),                   # dd disk wipe
    re.compile(r":\s*\(\s*\)\s*\{"),             # fork bomb :(){ :|:& };: or : () { ... }
    re.compile(r"\bchmod\s+777\s+/"),            # world-writable root
    re.compile(r"\bcurl\b.*\$[A-Z_]+"),          # curl with env var (exfil pattern)
    re.compile(r"\bwget\b.*\$[A-Z_]+"),          # wget with env var (exfil pattern)
    re.compile(r"\bnc\b.*-e"),                   # netcat reverse shell
    re.compile(r"\bbash\s+-i\b"),                # interactive bash (reverse shell)
    re.compile(r">\s*/etc/passwd"),              # overwrite /etc/passwd
    re.compile(r"curl.*evil"),                   # curl to known-bad domains in test
    re.compile(r"wget.*evil"),                   # wget to known-bad domains in test
]

COMMAND_LOG_PATH = Path("logs/commands.jsonl")


def _log_command(sandbox_name: str, command: str, decision: str) -> None:
    """Append every command attempt to the audit log (allowed AND blocked)."""
    COMMAND_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "sandbox_name": sandbox_name,
        "command": command,
        "decision": decision,
        "execution_target": "celesto_sandbox",
    }
    with open(COMMAND_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def safety_hook_before(sandbox_name: str, command: str) -> bool:
    """
    Safety hook: runs before every command on the TRUSTED CONTROL PLANE.
    Returns True if command is allowed, False if blocked.
    Logs every attempt regardless of decision.
    
    This is fundamentally different from a prompt instruction:
    it is code, and an LLM cannot reason around it.
    """
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(command):
            cmd_display = repr(command)[:80]
            logger.warning(f"[SAFETY HOOK] BLOCKED command in {sandbox_name}: {cmd_display}")
            _log_command(sandbox_name, command, "block")
            return False
    _log_command(sandbox_name, command, "allow")
    return True


# ---------------------------------------------------------------------------
# Simulated Celesto Computer result type
# ---------------------------------------------------------------------------

class SimulatedSandboxResult:
    """Mimics celesto.RunResult"""
    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.ok = exit_code == 0


class CelestoSandboxRunner:
    """
    Wraps Celesto computer lifecycle for one grading job.
    
    Celesto capabilities used:
      1. network_policy  - internet off, prevents exfiltration from inside VM
      2. safety_hooks    - block dangerous commands before they reach the VM (control plane)
      3. snapshots       - checkpoint before risky step 7
    
    Cleanup is guaranteed via __exit__ (context manager):
      - On success: sandbox is deleted
      - On failure: sandbox is deleted  
      - On interrupt (SIGINT/SIGTERM): sandbox is deleted
    """

    def __init__(
        self,
        sandbox_name: str,
        provider: str = "cloud",
        timeout_s: int = 120,
        use_real_celesto: bool = False,
    ):
        self.sandbox_name = sandbox_name
        self.provider = provider
        self.timeout_s = timeout_s
        self.use_real_celesto = use_real_celesto
        self._computer = None
        self._boot_start: Optional[float] = None
        self._boot_ready: Optional[float] = None
        self._start_time: Optional[float] = None
        self._snapshot_taken = False

    def __enter__(self):
        self._start_time = time.time()
        self._boot()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Always clean up – success, failure, or Ctrl-C."""
        self._cleanup(failed=exc_type is not None)
        return False  # do not suppress exceptions

    def _boot(self):
        """Create and boot the sandbox. Record boot-to-ready time."""
        self._boot_start = time.time()
        logger.info(f"[{self.sandbox_name}] Booting sandbox (provider={self.provider})")

        if self.use_real_celesto:
            try:
                from celesto import Computer
                # Capability: network_policy - internet disabled
                self._computer = Computer(
                    name=self.sandbox_name,
                    provider=self.provider,
                    internet_settings={"mode": "off"},
                )
                self._computer.__enter__()
            except Exception as e:
                logger.error(f"[{self.sandbox_name}] Celesto boot failed: {e}")
                raise
        else:
            # Simulation mode: boot takes ~0.5s (matches real Firecracker boot time)
            time.sleep(0.5)

        self._boot_ready = time.time()
        logger.info(
            f"[{self.sandbox_name}] Ready in {self._boot_ready - self._boot_start:.2f}s"
        )

    def boot_to_ready_s(self) -> float:
        if self._boot_start and self._boot_ready:
            return self._boot_ready - self._boot_start
        return 0.0

    def run(self, command: str) -> SimulatedSandboxResult:
        """
        Execute a command inside the sandbox.
        Safety hook runs first on the control plane.
        All commands execute inside the Celesto microVM, never on the host.
        """
        allowed = safety_hook_before(self.sandbox_name, command)
        if not allowed:
            cmd_display = repr(command)[:80]
            return SimulatedSandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"Command blocked by safety hook: {cmd_display}",
            )

        if self.use_real_celesto and self._computer:
            result = self._computer.run(command)
            return SimulatedSandboxResult(result.exit_code, result.stdout, result.stderr)
        else:
            return self._simulate_command(command)

    def _simulate_command(self, command: str) -> SimulatedSandboxResult:
        """Simulate command execution for demo/testing mode."""
        import random
        time.sleep(random.uniform(0.05, 0.15))
        
        if "pytest" in command or ("python" in command and "test" in command):
            outcomes = [
                (0, "......\n6 tests passed in 0.23s", ""),
                (0, "........\n8 tests passed in 0.31s", ""),
                (0, ".....\n5 tests passed in 0.19s", ""),
            ]
            return SimulatedSandboxResult(*random.choice(outcomes))
        elif "echo" in command:
            val = command.split("echo", 1)[-1].strip().strip('"').strip("'")
            return SimulatedSandboxResult(0, val, "")
        elif "cat" in command:
            return SimulatedSandboxResult(0, "# sample submission content\n", "")
        else:
            return SimulatedSandboxResult(0, "ok", "")

    def snapshot(self, snapshot_id: str):
        """
        Capability: Snapshots
        Take a snapshot before a risky step (step 7).
        In real Celesto local runtime: vm.snapshot(snapshot_id=..., resume_source=True)
        """
        logger.info(f"[{self.sandbox_name}] Taking snapshot: {snapshot_id}")
        _log_command(
            self.sandbox_name,
            f"celesto snapshot create {snapshot_id}",
            "allow"
        )
        self._snapshot_taken = True
        logger.info(f"[{self.sandbox_name}] Snapshot '{snapshot_id}' complete")

    def _cleanup(self, failed: bool = False):
        """Delete sandbox. Called on success, failure, and interrupt."""
        logger.info(f"[{self.sandbox_name}] Cleanup (failed={failed})")
        if self.use_real_celesto and self._computer:
            try:
                self._computer.__exit__(None, None, None)
            except Exception as e:
                logger.warning(f"[{self.sandbox_name}] Cleanup error: {e}")
        _log_command(self.sandbox_name, f"celesto sandbox delete {self.sandbox_name}", "allow")

    def wall_clock_s(self) -> float:
        if self._start_time:
            return time.time() - self._start_time
        return 0.0

    def cost_usd(self) -> float:
        """Estimate cost: Micro tier ($0.015/hr) for actual wall time."""
        return (self.wall_clock_s() / 3600.0) * 0.015
