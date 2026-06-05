# DSPy Trainer Informed Plan

## Objective

Build a Docker Compose platform for iterative DSPy development with three user surfaces:

- Backend API/orchestration service (system of record)
- Web UI (browser workflows)
- TUI (local Go binary using Bubble Tea + Lip Gloss)

The platform supports repeated evaluation and optimization loops for imported DSPy modules, with LLM-as-judge, MLflow correlation, and provider-flexible LLM configuration.

## Architecture Overview

### Core Services (Compose)

- `backend`: FastAPI control plane, data model, API, validation pipeline.
- `worker`: async execution engine for AgentRunPlan and OptimizationJob.
- `postgres`: relational metadata + structured JSON payloads.
- `redis`: queueing/coordinator for background jobs.
- `mlflow`: experiment tracking and artifacts.
- `litellm-proxy`: unified multi-provider LLM gateway.
- `web`: browser frontend.

### Local Client

- `tui`: local Go binary; communicates only with backend APIs.

## Imported Module Contract (Minimum Viable Runtime)

An imported module is accepted only if it contains:

1. Entrypoint script that loads and initializes the DSPy program and exposes:
   - `eval(InputObject, JudgeConfig)`
2. Dockerfile that:
   - inherits from the `dspy-trainer` base image
   - builds and runs the entrypoint in a deterministic runtime

The `dspy-trainer` base image includes shared runtime components for:

- Job lifecycle hooks/status reporting
- Control-plane communication with backend
- MLflow telemetry integration
- LiteLLM gateway integration and standard logging

## DSPy Informed Implementation Model

### Agent Composition

Use DSPy signatures + modules as the compositional foundation:

- `Signature` defines I/O contract.
- `ChainOfThought` wraps `Predict` and prepends `reasoning` output.
- Custom `dspy.Module` composes multi-step pipelines by feeding one stage output into the next.

Reference files:

- `dspy/dspy/signatures/signature.py`
- `dspy/dspy/predict/chain_of_thought.py`
- `dspy/dspy/predict/predict.py`
- `dspy/dspy/primitives/module.py`
- `dspy/dspy/primitives/prediction.py`

### Evaluation Runtime

Use `dspy.Evaluate` as inner evaluation engine:

- One `Evaluate(...)` call runs one pass over a devset.
- Backend `AgentRunPlan` orchestrates repeated passes (`runs_per_question`) and scenario slicing.
- `EvaluationResult.results` tuples map to `AgentRunTask` rows.

Reference files:

- `dspy/dspy/evaluate/evaluate.py`
- `dspy/dspy/primitives/example.py`
- `dspy/dspy/utils/parallelizer.py`
- `dspy/dspy/utils/callback.py`

### Optimization Runtime

Represent optimization as `OptimizationJob` using DSPy teleprompters/optimizers:

- Build train/val sets from eval-derived data.
- Build judge-backed metric callable.
- Run optimizer `compile(student, trainset, valset, ...)`.
- Persist optimized artifact + optimizer telemetry.

Initial optimizer support priority:

1. `MIPROv2`
2. `BootstrapFewShot`
3. `GEPA`

Reference files:

- `dspy/dspy/teleprompt/teleprompt.py`
- `dspy/dspy/teleprompt/mipro_optimizer_v2.py`
- `dspy/dspy/teleprompt/bootstrap.py`
- `dspy/dspy/teleprompt/gepa/gepa.py`

## Data Model

- `Project`
- `ModuleImport`
- `RuntimeBundle`
- `Dataset`
- `Scenario`
- `JudgeConfig`
- `AgentRunPlan`
- `AgentRunTask`
- `OptimizationJob`
- `Artifact`
- `LLMConfigProfile`

### LLMConfigProfile (LiteLLM-first)

Store provider-neutral model configuration, for example:

- `name`
- `litellm_model` (for example `openai/gpt-4o-mini`, `anthropic/claude-3-5-sonnet`, `ollama/llama3.1`)
- `api_base` (optional override)
- `temperature`, `max_tokens`, `timeout_s`, `retry_policy`
- `extra_body` (provider-specific JSON overrides)
- `credential_ref` (secret reference only)
- `enabled`

Jobs reference profile IDs, not raw API keys or provider internals.

## MLflow Correlation Strategy (Locked)

- One project-level MLflow experiment per project.
- Each `AgentRunPlan` maps to one parent MLflow run.
- Each `AgentRunTask` maps to correlated child trace/span-like telemetry.
- Backend remains richer source of truth; MLflow is tracking plane.

Required cross-system fields:

- `project_id`
- `module_import_id`
- `run_plan_id`
- `run_task_id`
- `scenario_id`
- `dataset_version`
- `mlflow_experiment_id`
- `mlflow_parent_run_id`
- `mlflow_trace_id`

## Workflow

1. Import module bundle.
2. Validate contract (entrypoint + Dockerfile inheritance).
3. Build container image and run smoke test with temporary payload.
4. Persist diagnostics if failing; enable fast re-validate/retry.
5. Register dataset + scenarios.
6. Launch AgentRunPlan (repetitions, concurrency, seed controls).
7. For each AgentRunTask:
   - execute module
   - run judge via LiteLLM profile
   - persist score/rationale/outputs
   - emit correlated MLflow telemetry
8. Launch OptimizationJob from eval-derived data.
9. Persist optimized artifact and comparison metrics.
10. Iterate.

## API Surface (v1)

### Projects + Imports

- `POST /projects`
- `POST /modules/import`
- `POST /modules/:id/validate`
- `POST /modules/:id/smoke-test`
- `GET /modules/:id/diagnostics`

### Datasets + Scenarios

- `POST /evaluation-datasets`
- `GET /evaluation-datasets`
- `GET /evaluation-datasets/:id`
- `PATCH /evaluation-datasets/:id`
- `POST /evaluation-datasets/:id/duplicate`
- `DELETE /evaluation-datasets/:id`
- `POST /scenarios`

### LLM Configuration

- `POST /llm/profiles`
- `GET /llm/profiles`
- `POST /llm/profiles/:id/validate`
- `PATCH /llm/profiles/:id`

### Evaluation

- `POST /agent-run-plans`
- `GET /agent-run-plans/:id`
- `GET /agent-run-plans/:id/tasks`
- `POST /agent-run-plans/:id/enqueue`

### Optimization

- `POST /optimization/jobs`
- `GET /optimization/jobs/:id`
- `POST /optimization/jobs/:id/cancel`

### Judge + System

- `POST /judge/configs`
- `POST /judge/validate`
- `GET /health`
- `GET /ready`

## LiteLLM Integration Plan

### Why LiteLLM

- Single API surface across many providers.
- Reduces custom adapter burden significantly.
- Supports easier model switching and provider portability.

### Integration Pattern

- Run `litellm-proxy` in compose as inference gateway.
- Backend and workers call LiteLLM endpoint only.
- `LLMConfigProfile.litellm_model` drives provider/model selection.
- Credential refs resolve to proxy/provider secrets at runtime.

### Judge Output Contract

- Enforce strict structured judge response schema (Pydantic/JSON schema).
- Normalize to internal shape:
  - `score` (float)
  - `rationale` (string)
  - `flags` (list)
  - `raw_response` (json)
- Retry/reformat policy for schema failures.

### Governance

- Global caps for timeout/retries/token limits.
- Optional per-project allowlist of models.
- Usage/cost counters persisted and tagged to MLflow and backend records.

## UX Scope

### TUI

- Import/validate/smoke-test wizard
- LLM profile selection and quick validation
- Eval launch + live status
- Optimization launch + status
- Result drill-down with judge rationale and MLflow IDs

### Web

- Same lifecycle with richer run/metric/trend views
- Profile management UI for LiteLLM model configurations
- Baseline vs optimized comparison screens

## Delivery Phases

1. Compose foundation (`backend`, `worker`, `postgres`, `redis`, `mlflow`, `litellm-proxy`, `web`).
2. Core schema/migrations + CRUD APIs.
3. Import contract validator + smoke-test pipeline.
4. LiteLLM profile management + validation endpoints.
5. Eval pipeline (`dspy.Evaluate` loop + MLflow parent-run correlation).
6. Optimization pipeline (`MIPROv2` first, then additional optimizers).
7. TUI MVP.
8. Web MVP.
9. Hardening: idempotency, retries, cancellation, quotas, cost controls.

## Risks and Mitigations

- **Provider output drift**: mitigate with strict schema validation + retry strategy.
- **Cost spikes under repeated evals**: enforce budget caps + per-job limits.
- **Runtime instability in imported modules**: isolate in container runtime and require smoke pass.
- **Correlation gaps between backend and MLflow**: treat correlation IDs as required fields and validate at ingest boundaries.

## Immediate Next Steps

1. Define `LLMConfigProfile` DB schema and secret resolution strategy.
2. Define module contract validator rules and smoke-test payload format.
3. Implement `AgentRunPlan` execution around `dspy.Evaluate` with repeat orchestration.
4. Implement first optimization path using `MIPROv2`.
5. Draft OpenAPI spec for v1 endpoints in this plan.
