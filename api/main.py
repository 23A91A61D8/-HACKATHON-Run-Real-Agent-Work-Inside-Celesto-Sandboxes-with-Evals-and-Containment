"""
Celesto Hackathon - Hostile-Submission Autograder
Run Ledger API: tracks sandbox runs and their states.
"""

import os
import re
import uuid
import datetime
import logging
from contextlib import asynccontextmanager
from typing import Optional, List

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, ValidationError
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ledger:ledger@ledger:5432/ledger")
API_PORT = int(os.getenv("API_PORT", "8080"))

VALID_STATES = {"created", "ready", "running", "succeeded", "failed", "deleted"}
SANDBOX_NAME_PATTERN = re.compile(r"^sbx-[a-z0-9]{8,}$")

db_pool: Optional[asyncpg.Pool] = None

# In-memory ledger fallback (used when running locally without Docker / PostgreSQL)
in_memory_runs: List[dict] = [
    {
        "run_id": "seed-demo-run-001",
        "sandbox_name": "sbx-seeddemo1",
        "problem_id": "autograder-demo",
        "state": "deleted",
        "execution_target": "celesto_sandbox",
        "created_at": "2026-10-21T06:30:00Z",
        "updated_at": "2026-10-21T06:30:00Z",
    }
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database connection pool lifecycle with graceful fallback."""
    global db_pool
    logger.info("Attempting database connection to PostgreSQL...")
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
        logger.info("Database pool created successfully.")
    except Exception as exc:
        logger.warning(
            f"PostgreSQL not available ({exc}). "
            "Running in standalone mode with in-memory ledger."
        )
        db_pool = None
    yield
    if db_pool:
        await db_pool.close()
        logger.info("Database pool closed.")


app = FastAPI(
    title="Celesto Autograder Run Ledger",
    description="Tracks sandbox runs for the Hostile-Submission Autograder agent",
    version="1.0.0",
    lifespan=lifespan,
)


class RunCreate(BaseModel):
    run_id: str
    sandbox_name: str
    problem_id: str
    state: str
    execution_target: str

    @field_validator("sandbox_name")
    @classmethod
    def validate_sandbox_name(cls, v: str) -> str:
        if not SANDBOX_NAME_PATTERN.match(v):
            raise ValueError(
                "sandbox_name must match sbx- followed by 8 or more lowercase letters or digits"
            )
        return v

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        if v not in VALID_STATES:
            raise ValueError(f"state must be one of: {', '.join(sorted(VALID_STATES))}")
        return v

    @field_validator("execution_target")
    @classmethod
    def validate_execution_target(cls, v: str) -> str:
        if v != "celesto_sandbox":
            raise ValueError("execution_target must be 'celesto_sandbox'")
        return v


@app.get("/health")
async def health():
    """Health check endpoint. Returns 200 with status ok."""
    return {"status": "ok"}


@app.get("/api/runs")
async def list_runs():
    """List all runs from the ledger. Always includes the seeded sbx-seeddemo1 run."""
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id, sandbox_name, problem_id, state, execution_target, "
                "created_at::text, updated_at::text FROM runs ORDER BY created_at DESC"
            )
        return [dict(row) for row in rows]
    return list(in_memory_runs)


@app.post("/api/runs", status_code=201)
async def create_run(request: Request):
    """
    Record a new sandbox run in the ledger.
    Returns 201 with the stored object on success.
    Returns 400 if validation fails (bad sandbox_name, unknown state, wrong execution_target).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "Request body must be a JSON object"})

    run_id = body.get("run_id")
    sandbox_name = body.get("sandbox_name")
    problem_id = body.get("problem_id")
    state = body.get("state")
    execution_target = body.get("execution_target")

    if not run_id or not isinstance(run_id, str):
        return JSONResponse(status_code=400, content={"detail": "run_id is required"})
    if not problem_id or not isinstance(problem_id, str):
        return JSONResponse(status_code=400, content={"detail": "problem_id is required"})
    if execution_target != "celesto_sandbox":
        return JSONResponse(status_code=400, content={"detail": "execution_target must be 'celesto_sandbox'"})
    if state not in VALID_STATES:
        return JSONResponse(status_code=400, content={"detail": f"state must be one of: {sorted(VALID_STATES)}"})
    if not sandbox_name or not isinstance(sandbox_name, str) or not SANDBOX_NAME_PATTERN.match(sandbox_name):
        return JSONResponse(status_code=400, content={"detail": "sandbox_name must match sbx- followed by 8+ alphanumeric characters"})

    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO runs (run_id, sandbox_name, problem_id, state, execution_target)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (run_id) DO UPDATE
                    SET state = EXCLUDED.state,
                        updated_at = NOW()
                RETURNING run_id, sandbox_name, problem_id, state, execution_target,
                          created_at::text, updated_at::text
                """,
                run_id,
                sandbox_name,
                problem_id,
                state,
                execution_target,
            )
        return JSONResponse(status_code=201, content=dict(row))

    # In-memory fallback
    now = datetime.datetime.utcnow().isoformat() + "Z"
    item = {
        "run_id": run_id,
        "sandbox_name": sandbox_name,
        "problem_id": problem_id,
        "state": state,
        "execution_target": execution_target,
        "created_at": now,
        "updated_at": now,
    }
    # Upsert by run_id
    for idx, existing in enumerate(in_memory_runs):
        if existing["run_id"] == run_id:
            in_memory_runs[idx] = item
            return JSONResponse(status_code=201, content=item)
    in_memory_runs.insert(0, item)
    return JSONResponse(status_code=201, content=item)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 400 for validation errors as required by hackathon spec."""
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError):
    """Return 400 for pydantic validation errors."""
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Return 400 for value errors."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, (RequestValidationError, ValidationError, ValueError)):
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=API_PORT,
        reload=False,
        log_level="info",
    )
