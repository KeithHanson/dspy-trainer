# DSPy Trainer Naive Plan

## Goal

Build a Docker Compose stack with:

- A backend server that handles APIs, orchestration, and persistence.
- A web UI that talks to the backend.
- A local TUI (Go binary using Bubble Tea/Lip Gloss) that talks to the backend.
- MLflow tracking integration for eval and optimization workflows.

The product should support repeated evaluate/optimize loops for imported DSPy modules, with an LLM as judge.

## Architecture

### Services

- `backend` (Python/FastAPI): system of record + API + orchestration.
- `worker` (same image as backend): async EvalJob/OptimizationJob execution.
- `postgres`: persistent relational store.
- `redis`: queue and coordination for background jobs.
- `mlflow`: tracking server for run metrics/artifacts/traces.
- `web`: browser UI.
- `tui`: local Go binary (not in compose) that uses backend HTTP APIs.

### Container Contract for Imported Modules

Imported DSPy modules must include:

1. An entrypoint script that loads/initializes the module and exposes:
   - `eval(InputObject, JudgeConfig)`
2. A Dockerfile that:
   - **inherits from the `dspy-trainer` base image**
   - builds/runs the entrypoint

The `dspy-trainer` base image must contain shared runtime components required for:

- Job lifecycle management hooks.
- Backend control-plane communication.
- MLflow telemetry/tracing/metrics integration.
- Standardized logging and status reporting.

These two files are the minimum acceptance criteria for import.

### Scaffolding

Provide generators/skeletons to minimize onboarding friction:

- `dspy-trainer init-module` (or equivalent)
- Generates:
  - Entrypoint script with `eval(InputObject, JudgeConfig)` contract
  - Dockerfile inheriting from `dspy-trainer` base image
  - Sample input/judge config payloads
  - Local smoke-test command

## Core Domain Model

- `Project`: workspace boundary.
- `ModuleImport`: imported module metadata (source, version/hash, status).
- `RuntimeBundle`: contract/build/smoke-test validation status.
- `Dataset`: versioned examples for eval and optimization.
- `Scenario`: parameterized test cases tied to datasets.
- `EvalJob`: repeated scenario execution job.
- `EvalRunItem`: one scenario invocation within an EvalJob.
- `OptimizationJob`: optimization loop using eval-derived or curated data.
- `JudgeConfig`: model/rubric/prompt/schema for LLM-as-judge.
- `Artifact`: logs, reports, snapshots, exported assets.

## MLflow Correlation Strategy (Locked)

- Use **one project-level MLflow Experiment** per project/workspace.
- Each `EvalJob` maps to a **parent MLflow Run**.
- Each `EvalRunItem` maps to child trace/span-like telemetry correlated to that parent run.
- Backend DB keeps richer app metadata; MLflow stores tracking telemetry.

Required correlation fields across backend + MLflow payloads:

- `project_id`
- `module_import_id`
- `eval_job_id`
- `eval_run_item_id`
- `scenario_id`
- `dataset_version`
- `mlflow_experiment_id`
- `mlflow_parent_run_id`
- `mlflow_trace_id` (or span equivalent)

## Workflow

1. Import module bundle.
2. Validate required contract files (entrypoint + Dockerfile base inheritance).
3. Build container image.
4. Run smoke test with temporary input payload.
5. If failure: persist diagnostics and remediation hints; allow retry.
6. If success: mark module as runnable.
7. Register dataset + scenarios.
8. Launch EvalJob (repetitions/concurrency/seed controls).
9. For each EvalRunItem:
   - Execute module eval
   - Run judge model
   - Persist outputs/scores/rationales
   - Emit correlated MLflow telemetry
10. Launch OptimizationJob using resulting data.
11. Compare baseline vs optimized outcomes and iterate.

## API Surface (v1)

### Project + Module Import

- `POST /projects`
- `POST /modules/import`
- `POST /modules/:id/validate`
- `POST /modules/:id/smoke-test`
- `GET /modules/:id/diagnostics`

### Dataset + Scenario

- `POST /datasets`
- `POST /scenarios`

### Eval

- `POST /eval/jobs`
- `GET /eval/jobs/:id`
- `GET /eval/jobs/:id/items`
- `POST /eval/jobs/:id/cancel`

### Optimization

- `POST /optimization/jobs`
- `GET /optimization/jobs/:id`
- `POST /optimization/jobs/:id/cancel`

### Judge + System

- `POST /judge/configs`
- `POST /judge/validate`
- `GET /health`
- `GET /ready`

## UI/TUI Priorities

### TUI (Go + Bubble Tea/Lip Gloss)

- Import wizard with validation/smoke feedback.
- Eval launch form (module/dataset/scenario/judge/repeat).
- Live job monitor (status/logs/scores/links to MLflow IDs).
- Optimization launch and status tracking.

### Web UI

- Same lifecycle as TUI with richer visualizations.
- Run list/detail views.
- Per-item judge rationale and score inspection.
- Baseline vs optimized comparison views.

## Delivery Phases

1. Compose skeleton + backend/web/mlflow wiring.
2. Domain schema + migrations + CRUD endpoints.
3. Module contract validator + smoke-test pipeline.
4. Eval pipeline + parent-run MLflow correlation.
5. Optimization pipeline.
6. TUI MVP (import -> smoke -> eval -> monitor).
7. Web MVP (same flow + run detail/comparison).
8. Hardening (retries, idempotency, cancellation, quotas, cost controls).

## Open Questions (Next Pass)

- Single-tenant MVP vs early multi-tenant boundaries.
- Judge provider abstraction order (OpenAI/Anthropic/local).
- Optimization strategy interface and stop criteria defaults.
- Artifact store strategy for MLflow (local volume vs S3-compatible store).
- Sandboxing/network policy strictness for imported module execution.
