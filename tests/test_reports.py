"""
Automated tests for all required hackathon report files.
Tests verify the contracts specified in the hackathon instructions.

Run unit tests only (no API needed):
    pytest tests/ -v -m "not integration"

Run integration tests (requires docker-compose up):
    pytest tests/ -v
"""

import json
import math
import os
import re
from pathlib import Path

import pytest
import requests

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8080")
REPO_ROOT = Path(__file__).parent.parent
SANDBOX_PATTERN = re.compile(r"^sbx-[a-z0-9]{8,}$")
VALID_STATES = {"created", "ready", "running", "succeeded", "failed", "deleted"}
VALID_CAPABILITY_IDS = {
    "persistence", "snapshots", "celestofs", "browser", "published_ports",
    "network_policy", "safety_hooks", "host_mounts", "coding_agent_preset", "agentor_durable"
}
VALID_ATTACK_TYPES = {"prompt_injection_exfil", "destructive_command", "suspicious_package"}
VALID_POLICIES = {"resume", "rollback", "fresh"}


# ─── Helper ───────────────────────────────────────────────────────────────────

def load_json(relative_path: str):
    p = REPO_ROOT / relative_path
    assert p.exists(), f"Missing required file: {relative_path}"
    return json.loads(p.read_text(encoding="utf-8"))


# ─── docker-compose.yml ───────────────────────────────────────────────────────

def test_docker_compose_exists():
    """docker-compose.yml must exist at the repo root."""
    assert (REPO_ROOT / "docker-compose.yml").exists()


def test_docker_compose_services():
    """docker-compose.yml must define 'api' and 'ledger' services."""
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "api:" in text, "Missing 'api:' service in docker-compose.yml"
    assert "ledger:" in text, "Missing 'ledger:' service in docker-compose.yml"


def test_docker_compose_healthchecks():
    """Both services must have healthchecks with all required fields."""
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "healthcheck" in text
    assert "pg_isready" in text, "ledger healthcheck must use pg_isready"
    assert "/health" in text, "api healthcheck must check /health"
    assert "interval" in text
    assert "timeout" in text
    assert "retries" in text
    assert "start_period" in text


def test_dockerfile_exists():
    """Dockerfile must exist at the repo root."""
    assert (REPO_ROOT / "Dockerfile").exists()


def test_seeds_dir_exists():
    """seeds/ directory and 01_runs.sql must exist."""
    assert (REPO_ROOT / "seeds" / "01_runs.sql").exists()


def test_seeds_contain_seeddemo1():
    """seeds/01_runs.sql must seed the sbx-seeddemo1 run."""
    sql = (REPO_ROOT / "seeds" / "01_runs.sql").read_text(encoding="utf-8")
    assert "sbx-seeddemo1" in sql


# ─── .env.example ─────────────────────────────────────────────────────────────

def test_env_example_exists():
    assert (REPO_ROOT / ".env.example").exists()


def test_env_file_not_committed():
    """Live .env must NOT be in the repository root."""
    assert not (REPO_ROOT / ".env").exists(), \
        "SECURITY: .env must not be committed (use .env.example)"


def test_env_example_required_keys():
    """All required env vars must be documented in .env.example."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    required = [
        "CELESTO_PROVIDER", "CELESTO_API_KEY", "LLM_API_KEY",
        "LLM_MODEL", "DATABASE_URL", "API_PORT"
    ]
    for key in required:
        assert key in text, f"Missing required key in .env.example: {key}"


# ─── reports/problem.json ─────────────────────────────────────────────────────

def test_problem_json_exists():
    assert (REPO_ROOT / "reports" / "problem.json").exists()


def test_problem_json_content():
    data = load_json("reports/problem.json")
    assert len(data.get("who", "")) >= 40, \
        f"problem.json 'who' must be >= 40 chars, got {len(data.get('who', ''))}"
    assert len(data.get("agent_work", "")) >= 40, \
        f"problem.json 'agent_work' must be >= 40 chars"
    assert len(data.get("proof_output", "")) >= 20, \
        f"problem.json 'proof_output' must be >= 20 chars"
    assert data.get("idea_source") in ("idea_bank", "original"), \
        f"idea_source must be 'idea_bank' or 'original', got: {data.get('idea_source')}"


# ─── reports/capabilities.json ────────────────────────────────────────────────

def test_capabilities_json_exists():
    assert (REPO_ROOT / "reports" / "capabilities.json").exists()


def test_capabilities_json_content():
    data = load_json("reports/capabilities.json")
    caps = data.get("capabilities", [])
    assert len(caps) >= 2, f"Must have at least 2 capabilities, got {len(caps)}"

    ids = [c["id"] for c in caps]
    assert len(ids) == len(set(ids)), "Capability ids must be unique"

    for cap in caps:
        cap_id = cap.get("id", "")
        assert cap_id in VALID_CAPABILITY_IDS, \
            f"Unknown capability id: '{cap_id}'. Valid: {VALID_CAPABILITY_IDS}"
        why_len = len(cap.get("why", ""))
        assert why_len >= 80, \
            f"'why' for capability '{cap_id}' must be >= 80 chars, got {why_len}"
        evidence = REPO_ROOT / cap["evidence_path"]
        assert evidence.exists(), \
            f"evidence_path does not exist: {cap['evidence_path']}"


# ─── reports/lifecycle.json ───────────────────────────────────────────────────

def test_lifecycle_json_exists():
    assert (REPO_ROOT / "reports" / "lifecycle.json").exists()


def test_lifecycle_json_content():
    data = load_json("reports/lifecycle.json")
    assert data.get("cleanup_on_success") is True, "cleanup_on_success must be true"
    assert data.get("cleanup_on_failure") is True, "cleanup_on_failure must be true"
    assert data.get("cleanup_on_interrupt") is True, "cleanup_on_interrupt must be true"

    names = data.get("sandbox_names", [])
    assert len(names) > 0, "sandbox_names must be non-empty"
    assert len(names) == len(set(names)), "sandbox_names must be unique"

    for name in names:
        assert SANDBOX_PATTERN.match(name), \
            f"Invalid sandbox name in lifecycle.json: '{name}'"

    # Each name must appear in commands.jsonl OR reliability.json
    cmd_path = REPO_ROOT / "logs/commands.jsonl"
    rel_path = REPO_ROOT / "reports/reliability.json"
    cmd_text = cmd_path.read_text(encoding="utf-8") if cmd_path.exists() else ""
    rel_text = rel_path.read_text(encoding="utf-8") if rel_path.exists() else ""

    for name in names:
        assert name in cmd_text or name in rel_text, \
            f"Sandbox '{name}' from lifecycle.json not found in commands.jsonl or reliability.json"


# ─── logs/commands.jsonl ──────────────────────────────────────────────────────

def test_commands_jsonl_exists():
    assert (REPO_ROOT / "logs" / "commands.jsonl").exists()


def test_commands_jsonl_content():
    p = REPO_ROOT / "logs/commands.jsonl"
    lines = [l for l in p.read_text(encoding="utf-8").strip().splitlines() if l.strip()]
    assert len(lines) >= 1, "commands.jsonl must have at least one line"

    for i, line in enumerate(lines):
        obj = json.loads(line)
        assert "ts" in obj, f"Line {i+1}: missing 'ts'"
        assert "sandbox_name" in obj, f"Line {i+1}: missing 'sandbox_name'"
        assert SANDBOX_PATTERN.match(obj["sandbox_name"]), \
            f"Line {i+1}: invalid sandbox_name: '{obj['sandbox_name']}'"
        assert obj.get("command"), f"Line {i+1}: 'command' must be non-empty"
        assert obj.get("decision") in ("allow", "block"), \
            f"Line {i+1}: 'decision' must be 'allow' or 'block', got '{obj.get('decision')}'"
        assert obj.get("execution_target") == "celesto_sandbox", \
            f"Line {i+1}: execution_target must be 'celesto_sandbox', got '{obj.get('execution_target')}'"


# ─── reports/reliability.json ─────────────────────────────────────────────────

def test_reliability_json_exists():
    assert (REPO_ROOT / "reports" / "reliability.json").exists()


def test_reliability_json_content():
    data = load_json("reports/reliability.json")
    runs = data.get("runs", [])
    run_count = data.get("run_count", 0)

    assert run_count >= 20, f"run_count must be >= 20, got {run_count}"
    assert run_count == len(runs), \
        f"run_count ({run_count}) must equal len(runs) ({len(runs)})"

    names = [r["sandbox_name"] for r in runs]
    assert len(names) == len(set(names)), "sandbox_names in runs must be unique"

    for i, r in enumerate(runs):
        name = r.get("sandbox_name", "")
        assert SANDBOX_PATTERN.match(name), \
            f"Run {i+1}: invalid sandbox_name '{name}'"
        assert r.get("fresh") is True, f"Run {i+1}: fresh must be true"
        assert isinstance(r.get("success"), bool), \
            f"Run {i+1}: success must be boolean"
        assert r.get("wall_clock_s", -1) >= 0, \
            f"Run {i+1}: wall_clock_s must be >= 0"
        assert r.get("boot_to_ready_s", -1) >= 0, \
            f"Run {i+1}: boot_to_ready_s must be >= 0"
        assert r.get("cost_usd", -1) >= 0, \
            f"Run {i+1}: cost_usd must be >= 0"

    # Verify statistics match the runs data (within tolerance)
    wall_sorted = sorted(r["wall_clock_s"] for r in runs)
    boot_sorted = sorted(r["boot_to_ready_s"] for r in runs)
    n = len(wall_sorted)

    def median_fn(lst):
        s = sorted(lst)
        nn = len(s)
        if nn % 2 == 0:
            return (s[nn // 2 - 1] + s[nn // 2]) / 2
        return s[nn // 2]

    def p90_fn(lst):
        s = sorted(lst)
        nn = len(s)
        idx = math.ceil(0.90 * nn) - 1
        return s[idx]

    expected_median = median_fn(wall_sorted)
    assert abs(data["median_wall_clock_s"] - expected_median) <= 0.001, \
        f"median_wall_clock_s: expected {expected_median:.3f}, got {data['median_wall_clock_s']}"

    expected_p90 = p90_fn(wall_sorted)
    assert abs(data["p90_wall_clock_s"] - expected_p90) <= 0.001, \
        f"p90_wall_clock_s: expected {expected_p90:.3f}, got {data['p90_wall_clock_s']}"

    expected_boot_median = median_fn(boot_sorted)
    assert abs(data["median_boot_to_ready_s"] - expected_boot_median) <= 0.001, \
        f"median_boot_to_ready_s: expected {expected_boot_median:.3f}, got {data['median_boot_to_ready_s']}"

    success_count = sum(1 for r in runs if r["success"])
    expected_rate = success_count / run_count
    assert abs(data["success_rate"] - expected_rate) <= 0.001, \
        f"success_rate: expected {expected_rate:.4f}, got {data['success_rate']}"

    expected_cost = sum(r["cost_usd"] for r in runs)
    assert abs(data["total_cost_usd"] - expected_cost) <= 0.01, \
        f"total_cost_usd: expected {expected_cost:.6f}, got {data['total_cost_usd']}"

    assert data.get("postmortem_path") == "reports/postmortem.md"
    postmortem = REPO_ROOT / "reports/postmortem.md"
    assert postmortem.exists(), "reports/postmortem.md must exist"
    assert len(postmortem.read_text(encoding="utf-8")) >= 200, \
        f"postmortem.md must be >= 200 chars"


# ─── reports/containment.json ─────────────────────────────────────────────────

def test_containment_json_exists():
    assert (REPO_ROOT / "reports" / "containment.json").exists()


def test_containment_json_content():
    data = load_json("reports/containment.json")
    assert data.get("attack") in VALID_ATTACK_TYPES, \
        f"attack must be one of {VALID_ATTACK_TYPES}, got '{data.get('attack')}'"
    assert data.get("blocked") is True, "blocked must be true"
    assert data.get("host_untouched") is True, "host_untouched must be true"

    evidence_paths = data.get("evidence_paths", [])
    assert len(evidence_paths) >= 1, "evidence_paths must have at least one entry"
    for ep in evidence_paths:
        assert (REPO_ROOT / ep).exists(), f"Evidence path does not exist: {ep}"


# ─── reports/failure_policy.json ──────────────────────────────────────────────

def test_failure_policy_json_exists():
    assert (REPO_ROOT / "reports" / "failure_policy.json").exists()


def test_failure_policy_json_content():
    data = load_json("reports/failure_policy.json")
    assert data.get("failed_step") == 7, \
        f"failed_step must be 7, got {data.get('failed_step')}"
    assert data.get("total_steps") == 10, \
        f"total_steps must be 10, got {data.get('total_steps')}"
    assert data.get("policy") in VALID_POLICIES, \
        f"policy must be one of {VALID_POLICIES}, got '{data.get('policy')}'"
    rationale_len = len(data.get("rationale", ""))
    assert rationale_len >= 200, \
        f"rationale must be >= 200 chars, got {rationale_len}"
    trace_path = data.get("trace_path")
    assert trace_path == "reports/failure_trace.jsonl", \
        f"trace_path must be 'reports/failure_trace.jsonl', got '{trace_path}'"


def test_failure_trace_jsonl():
    """failure_trace.jsonl must contain a policy_applied event matching the policy."""
    policy_data = load_json("reports/failure_policy.json")
    policy = policy_data["policy"]

    trace_file = REPO_ROOT / "reports/failure_trace.jsonl"
    assert trace_file.exists(), "reports/failure_trace.jsonl must exist"

    found = False
    for line in trace_file.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("event") == "policy_applied" and obj.get("policy") == policy:
            found = True
            break
    assert found, \
        f"No 'policy_applied' line with policy='{policy}' found in failure_trace.jsonl"


# ─── reports/concurrency.json ─────────────────────────────────────────────────

def test_concurrency_json_exists():
    assert (REPO_ROOT / "reports" / "concurrency.json").exists()


def test_concurrency_json_content():
    data = load_json("reports/concurrency.json")
    jobs = data.get("jobs", [])
    assert len(jobs) >= 5, f"Must have at least 5 concurrent jobs, got {len(jobs)}"

    names = [j["sandbox_name"] for j in jobs]
    assert len(names) == len(set(names)), "Job sandbox_names must be unique"

    for j in jobs:
        assert SANDBOX_PATTERN.match(j["sandbox_name"]), \
            f"Invalid sandbox_name in concurrency: '{j['sandbox_name']}'"
        assert j.get("started_at"), "started_at must be non-empty"

    breakage_len = len(data.get("breakage_notes", ""))
    assert breakage_len >= 40, \
        f"breakage_notes must be >= 40 chars, got {breakage_len}"


# ─── README.md ────────────────────────────────────────────────────────────────

def test_readme_exists():
    assert (REPO_ROOT / "README.md").exists()


def test_readme_required_phrases():
    """README.md must contain all required phrases."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    required_phrases = [
        "Problem statement",
        "How I used Celesto",
        "Eval report",
        "Containment test",
        "State and failure design",
        "Setup",
    ]
    for phrase in required_phrases:
        assert phrase in readme, \
            f"README.md missing required phrase: '{phrase}'"


# ─── API integration tests (require docker-compose up) ────────────────────────

@pytest.mark.integration
def test_api_health():
    """GET /health must return 200 with {"status": "ok"}."""
    resp = requests.get(f"{BASE_URL}/health", timeout=10)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.integration
def test_api_runs_list_includes_seed():
    """GET /api/runs must include the seeded sbx-seeddemo1 run."""
    resp = requests.get(f"{BASE_URL}/api/runs", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    names = [r["sandbox_name"] for r in data]
    assert "sbx-seeddemo1" in names, \
        f"sbx-seeddemo1 not found in /api/runs. Got names: {names}"


@pytest.mark.integration
def test_api_runs_create_valid():
    """POST /api/runs with valid body must return 201."""
    import uuid
    payload = {
        "run_id": str(uuid.uuid4()),
        "sandbox_name": "sbx-testrun0001",
        "problem_id": "test-problem-001",
        "state": "created",
        "execution_target": "celesto_sandbox",
    }
    resp = requests.post(f"{BASE_URL}/api/runs", json=payload, timeout=10)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["sandbox_name"] == "sbx-testrun0001"
    assert data["state"] == "created"
    assert data["execution_target"] == "celesto_sandbox"


@pytest.mark.integration
def test_api_runs_reject_host_execution_target():
    """POST /api/runs with execution_target=host must return 400."""
    import uuid
    payload = {
        "run_id": str(uuid.uuid4()),
        "sandbox_name": "sbx-testrun0002",
        "problem_id": "test-problem-002",
        "state": "created",
        "execution_target": "host",  # This must be rejected
    }
    resp = requests.post(f"{BASE_URL}/api/runs", json=payload, timeout=10)
    assert resp.status_code == 400, \
        f"Expected 400 for host execution_target, got {resp.status_code}"


@pytest.mark.integration
def test_api_runs_reject_bad_sandbox_name():
    """POST /api/runs with invalid sandbox_name must return 400."""
    import uuid
    payload = {
        "run_id": str(uuid.uuid4()),
        "sandbox_name": "bad-name",  # Doesn't match sbx-[a-z0-9]{8,}
        "problem_id": "test-problem-003",
        "state": "created",
        "execution_target": "celesto_sandbox",
    }
    resp = requests.post(f"{BASE_URL}/api/runs", json=payload, timeout=10)
    assert resp.status_code == 400, \
        f"Expected 400 for bad sandbox_name, got {resp.status_code}"


@pytest.mark.integration
def test_api_runs_reject_invalid_state():
    """POST /api/runs with unknown state must return 400."""
    import uuid
    payload = {
        "run_id": str(uuid.uuid4()),
        "sandbox_name": "sbx-testrun0004",
        "problem_id": "test-problem-004",
        "state": "unknown_state",
        "execution_target": "celesto_sandbox",
    }
    resp = requests.post(f"{BASE_URL}/api/runs", json=payload, timeout=10)
    assert resp.status_code == 400, \
        f"Expected 400 for invalid state, got {resp.status_code}"
