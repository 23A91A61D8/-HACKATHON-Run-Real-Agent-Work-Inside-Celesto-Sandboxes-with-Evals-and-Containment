-- Create the runs table if it does not exist
CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(255) UNIQUE NOT NULL,
    sandbox_name VARCHAR(255) NOT NULL,
    problem_id VARCHAR(255) NOT NULL,
    state VARCHAR(50) NOT NULL CHECK (state IN ('created', 'ready', 'running', 'succeeded', 'failed', 'deleted')),
    execution_target VARCHAR(50) NOT NULL CHECK (execution_target = 'celesto_sandbox'),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indices for faster lookups
CREATE INDEX IF NOT EXISTS idx_runs_sandbox_name ON runs(sandbox_name);
CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
CREATE INDEX IF NOT EXISTS idx_runs_problem_id ON runs(problem_id);

-- Seed the demo run (idempotent: re-running does not duplicate)
INSERT INTO runs (run_id, sandbox_name, problem_id, state, execution_target)
VALUES (
    'seed-demo-run-001',
    'sbx-seeddemo1',
    'autograder-demo',
    'deleted',
    'celesto_sandbox'
)
ON CONFLICT (run_id) DO NOTHING;
