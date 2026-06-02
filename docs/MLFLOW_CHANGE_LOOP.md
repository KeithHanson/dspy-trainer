# MLflow Change Loop

Use this loop whenever you change backend eval/trace/judge code and want to verify behavior in MLflow.

## 1) Make code changes

Edit the relevant files (typically under `backend/app/executor/`, `backend/app/services.py`, or `examples/module_bundles/simple_echo_agent/`).

Recommended quick test before rebuilding:

```bash
pytest -q backend/tests/test_optimization_dataset_builders.py backend/tests/test_optimization_jobs_api.py
```

## 2) Rebuild and restart containers in daemon mode

From repo root:

```bash
docker compose up -d --build backend worker
```

This recreates backend/worker with your latest code and keeps services running in the background.

## 3) Re-run the example eval script

```bash
./scripts/run_example_eval.sh
```

Capture these values from output:

- `RUN_PLAN_ID`
- `mlflow_parent_run_id`
- each `mlflow_trace_id` in `Fetching run plan tasks`

## 4) Verify in MLflow UI

Open MLflow:

- `http://localhost:5001/#/experiments/1/evaluation-runs`

Check the newest run:

- Exactly one parent run for the run plan
- Trace rows exist for each run task
- `judge_metric` appears once per trace (no `1 (2)` duplication)

## 5) Verify with MCP (authoritative checks)

Run MCP checks for the latest parent run id.

### A. Confirm run shape

- `mlflow_mcp_describe_run` on the parent run id
- Expect: status `FINISHED`, `plan_id` tag present

### B. Confirm traces linked to the parent run

Use client search (or MCP trace search where available) filtered by `run_id`.

Expected:

- trace count equals number of run tasks (normally 2)

### C. Confirm assessments on traces

For each trace id, call `mlflow_mcp_get_trace`.

Expected:

- one `judge_metric` assessment per trace
- numeric `feedback.value` (`1.0`/`0.0`)
- metadata includes `mlflow.assessment.sourceRunId = <parent_run_id>`

## 6) Troubleshooting checklist

- **No new behavior after code edits**: forgot `--build` on `docker compose up -d`.
- **No traces linked**: inspect `mlflow_parent_run_id` and trace linking path in the agent run task execution flow.
- **Duplicate assessments (`1 (2)`)**: inspect dedupe logic in `_cleanup_duplicate_judge_assessments` and normalization pass.
- **NaN in AVG**: verify assessments are numeric and run-scoped metadata is present.

## 7) Repeat loop

When behavior is off:

1. patch code,
2. rebuild daemon containers,
3. rerun script,
4. inspect with MCP + UI,
5. repeat until expected state is confirmed.
