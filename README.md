# WARNING

This is still very much a WIP. Move along unless you like the bleeding edge!


# DSPy Trainer
> **A local evaluation and optimization workbench for DSPy programs**  
> Build, test, and optimize your DSPy agents with repeatable evals, automated optimization, and full GitHub-backed provenance.

---

## Why DSPy Trainer?

Building production LLM programs requires iteration—lots of it. DSPy Trainer gives you:

- **GitHub-first workflow**: Track your DSPy programs as repositories, not opaque files
- **Repeatable evaluations**: Run the same tests against different models, prompts, or optimized versions
- **Automated optimization**: Let DSPy's optimizers (MIPROv2, BootstrapFewShot, GEPA) improve your program automatically
- **Full provenance**: Every eval and optimization links to MLflow tracking with commit SHA, metrics, and artifacts
- **No vendor lock-in**: Point LM Profiles at direct provider endpoints without changing bundle code

---

## Quick Start

### 1. Configure Environment

```bash
cp .env.sample .env
# Edit .env: add GITHUB_PAT and stable Compose, network, deployment, and
# local backend image names. The deployer records the immutable image ID.
# If you plan to store module environment entries in the UI OR save LM
# Profile provider API keys, also generate DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Start the Stack

```bash
docker compose pull --ignore-pull-failures
docker compose build --pull backend
docker compose build --pull
docker compose up -d --scale deployer=2 --remove-orphans
for id in $(docker compose ps -q deployer); do
  docker exec "$id" python backend/deployer.py --liveness
  docker exec "$id" python backend/deployer.py --readiness
done
```

If MLflow trace or run requests time out under load, increase `MLFLOW_WEB_WORKERS` in `.env` before restarting the stack.

The default local Compose setup now routes operator/browser traffic through Caddy on `http://localhost:8080`. The web build defaults to relative proxy paths (`/api`, `/mlflow`) so the UI, API, and MLflow links stay on one origin. For non-local deployments, set `CADDY_HTTP_PORT` as needed and override `VITE_API_BASE_URL` and `VITE_MLFLOW_BASE_URL` in `.env` before rebuilding the web image. Compose forwards `CADDY_HTTP_PORT` into the MLflow container so its allowed-hosts list matches the proxy port you expose. The backend automatically derives additional allowed CORS origins from absolute public URLs, and you can extend the allowlist further with `DSPY_TRAINER_CORS_ALLOW_ORIGINS`.

### 3. Access the Platform

| Surface | URL |
|---------|-----|
| **Web UI** | http://localhost:8080/ |
| **Backend API** | http://localhost:8080/api/ |
| **Backend API docs** | http://localhost:8080/api/docs |
| **MLflow** | http://localhost:8080/mlflow/ |

Postgres (`5432`) and Redis (`6379`) remain published directly for local developer tooling. Browser/operator traffic should use the Caddy surface above.

### 4. Your First Eval

1. **Download** a sample bundle from the Bundles page dropdown
2. **Push** it to a GitHub repository
3. **Import** the repository into DSPy Trainer
4. **Create** an LM Profile for your model
5. **Create** a Dataset with test cases
6. **Run** an evaluation plan
7. **Launch** an optimization job from successful runs

📖 **Detailed setup guide**: See [`docs/COMPOSE_RUNBOOK.md`](docs/COMPOSE_RUNBOOK.md)

---

## Core Concepts

### 🎁 Bundle

A **bundle** is a GitHub repository (or subfolder) containing your DSPy program:

```
my-bundle/
├── module.py          # DSPy program with build_program()
├── metric.py          # Judge function with judge_metric(example, prediction, trace=None)
├── bundle.toml        # Metadata and configuration
├── requirements.txt   # Optional: Python dependencies
├── program.json       # Optional optimized program state referenced by bundle.toml
└── run_agent.py       # Optional local helper script for manual testing
```

**Key points:**
- GitHub-backed with commit provenance
- Validated before first run
- Can declare optimized program state for loading
- Can optionally narrow optimization targets via `optimization.target_output_fields`
- Can optionally declare system package install commands via `runtime.system_dependency_commands`

### 📊 Dataset

A **dataset** is a reusable collection of test cases for a bundle:

```json
{
  "input": {"question": "How do I reset my password?"},
  "label": {"judge_instructions": "Use these instructions to judge whether the reply routes the user to /reset-password and explains the next step clearly."}
}
```

- Scoped to a specific bundle
- Reusable across evaluation plans
- Validates against bundle's declared input/label schema

### 📋 Evaluation Plan

An **evaluation plan** runs your bundle against a dataset:

- Select dataset, LM profile, bundle version
- Configure `runs_per_question` and `max_workers`
- Creates a run plan with detailed per-task results
- Links to MLflow parent run for tracking

### 🚀 Optimization Job

An **optimization job** uses DSPy optimizers to improve your program:

1. **Derive training data** from eval results
2. **Run optimizer** (MIPROv2, BootstrapFewShot, or GEPA)
3. **Generate optimized artifact** (e.g., `program.json`)
4. **Push to optimization branch** for review
5. **Run comparison eval** (baseline vs optimized)

**Result**: A Git branch you can review and merge manually.

### 🤖 LM Profile

An **LM profile** configures direct provider runtime access:

- Model name (e.g., `openai/gpt-4o-mini`)
- Temperature, max tokens, timeouts
- Optional provider API key storage
- Swap providers without changing bundle code

### 🔌 Managed Endpoint

A **managed endpoint** exposes a validated bundle to external callers with a rotatable API key. Creation is accepted only when the module's exact current revision is validated and has a ready, non-pruned local image; otherwise the API returns `409` with a stable `endpoint_revision_not_ready` or `endpoint_image_not_ready` code and writes no endpoint or deployment intent. New endpoints begin as managed-image deployments and become invokable only after their exact container slots report ready.

- Create, rename, delete, and rotate keys from the bundle detail page
- `POST /bundle-endpoints/{id}/invoke` returns one JSON output payload
- `POST /bundle-endpoints/{id}/stream` returns an SSE stream of incremental `delta` events followed by a `final` event
- Each invocation is traced in MLflow under the `dspy-trainer-managed-endpoints` experiment, tagged with its invocation, endpoint, worker, module, profile, and bundle revision identifiers
- Trace inputs and outputs capture the endpoint payload, while DSPy autologging records model execution as child spans
- Streaming bundles must implement `emit(..., emit=<callback>)` on the built program and return a final prediction payload

---

## What is DSPy?

[**DSPy**](https://dspy.ai) is a framework for **programming** (not prompting) LLM applications:

- Define typed **signatures** (input/output contracts)
- Compose **modules** (predictors, chains, pipelines)
- Use **optimizers** to improve prompts and weights automatically
- Evaluate with **metrics** instead of manual prompt tweaking

Instead of:
```python
prompt = "You are a helpful assistant. Answer this question: {question}"
```

You write:
```python
class Answer(dspy.Signature):
    question: str = dspy.InputField()
    answer: str = dspy.OutputField()

program = dspy.ChainOfThought(Answer)
```

DSPy handles execution, optimization, and prompt engineering for you.

---

## Architecture Overview

```
┌─────────────┐
│   Web UI    │  React app - create bundles, plans, view results
└──────┬──────┘
       │
┌──────▼──────┐       ┌──────────────┐
│  Backend    │       │   Deployer   │  image builds and endpoint reconciliation
└──────┬──────┘       └──────┬───────┘
       │                     │ Docker socket (deployer only)
┌──────▼──────┐              │
│   Worker    │              │
└──────┬──────┘              │
       │                     │
┌──────┴─────────────────────┴────────┐
│        Postgres  │  Redis  │  MLflow │
└──────────────────────────────────────┘
```

**Services:**
- **Backend**: Control plane (FastAPI)
- **Worker**: Execution engine (async job processing)
- **Deployer**: Internal-only PostgreSQL advisory leader that builds revision images and reconciles exact-image endpoint containers; it alone installs the Docker SDK and mounts the Docker socket
- **Postgres**: Primary app store
- **Redis**: Queue + worker coordination
- **MLflow**: Experiment tracking with metadata stored in a dedicated Postgres `mlflow` schema and artifacts on a Docker volume
- **LM Profiles**: Stored direct-provider runtime config

For current stack operations and service expectations, see [`docs/COMPOSE_RUNBOOK.md`](docs/COMPOSE_RUNBOOK.md).

---

## Creating Your Own Bundle

### Minimum Required Files

DSPy Trainer ships with multiple downloadable sample bundles, including:

- a deterministic `r`-counting agent
- an IT ticket triage agent with an LLM-as-judge metric
- an event extraction agent inspired by the DSPy website example

These samples are exposed from the Bundles page download dropdown so operators can validate the import and eval flow with different bundle patterns.

Required files at the selected bundle root:

- `module.py`
- `metric.py`
- `bundle.toml`

Optional files commonly used by bundles:

- `requirements.txt`
- `program.json` or another file referenced by `optimized_program_state`
- `run_agent.py`

**`module.py`**
```python
import dspy

class YourSignature(dspy.Signature):
    question: str = dspy.InputField()
    answer: str = dspy.OutputField()

class YourProgram(dspy.Module):
    def __init__(self):
        self.predictor = dspy.ChainOfThought(YourSignature)
    
    def forward(self, question):
        return self.predictor(question=question)

def build_program():
    return YourProgram()
```

`module.py` must satisfy the validator/runtime contract:

- Define at least one DSPy `Signature`
- Define at least one `dspy.Module` subclass
- Export top-level `build_program()`

**`metric.py`**
```python
def judge_metric(example, prediction, trace=None):
    expected = example.label["expected"]
    matched = prediction.answer == expected
    
    return {
        "score": 1.0 if matched else 0.0,
        "rationale": "exact_match" if matched else "mismatch",
        "flags": [] if matched else ["answer_mismatch"],
        "raw_response": {"expected": expected, "got": prediction.answer}
    }
```

`metric.py` contract:

- Must export top-level `judge_metric(example, prediction)`
- A third optional `trace=None` parameter is allowed for compatibility
- Runtime currently requires the exact return keys: `score`, `rationale`, `flags`, `raw_response`
- `score` must be numeric, `rationale` must be a string, and `flags` must be `list[str]`

**`bundle.toml`**
```toml
name = "your-bundle-name"
version = "0.1.0"
score_pass_threshold = 0.8
dspy_version = ">=2.5,<3.0"

[evaluation.input]
fields = [{ key = "question", label = "Question", required = true, multiline = true }]

[evaluation.label]
fields = [{ key = "expected", label = "Expected", required = true, multiline = true }]

[runtime]
system_dependency_commands = []

[optimization]
target_output_fields = ["answer"]
```

`optimization.target_output_fields` is optional. When provided, DSPy Trainer uses only those predictor output fields as optimization targets instead of reusing every key present in prior prediction payloads. This is useful for bundles whose predictions include debug traces or other bulky internal fields that should still be judged during evals but should not be optimized directly.

Other supported `bundle.toml` fields:

- `optimized_program_state = "program.json"` loads a saved program state before eval/invoke/optimization
- `runtime.system_dependency_commands = ["..."]` runs shell commands before `requirements.txt` installation
- `evaluation.input.fields` and `evaluation.label.fields` define the dataset contract used by the UI and API

### Testing Locally

```bash
cd your-bundle/
python run_agent.py ...
```

`run_agent.py` is not required by DSPy Trainer. The sample bundles include it as a convenience script only.

**Examples**:

- [`backend/sample_bundles/example-bundle/`](backend/sample_bundles/example-bundle/)
- [`backend/sample_bundles/it-ticket-triage-bundle/`](backend/sample_bundles/it-ticket-triage-bundle/)
- [`backend/sample_bundles/event-extraction-bundle/`](backend/sample_bundles/event-extraction-bundle/)

---

## Workflow Deep Dive

### 1. Bundle Validation

When you import a bundle, DSPy Trainer:

- ✅ Verifies the bundle root contains `module.py`, `metric.py`, and `bundle.toml`
- ✅ Verifies `module.py` defines at least one DSPy `Signature`, a `dspy.Module` subclass, and `build_program()`
- ✅ Verifies `metric.py` defines `judge_metric(example, prediction)`
- ✅ Checks `bundle.toml` structure
- ✅ Validates required fields
- ✅ Tracks Git commit SHA for provenance

### 2. Evaluation Execution

When you run an eval plan:

1. Worker loads bundle from checkout
2. Installs `requirements.txt` if present (cached)
3. Builds program via `build_program()`
4. Runs program on each dataset item
5. Calls `judge_metric()` with the built example and prediction
6. Records score, rationale, flags, and raw outputs
7. Updates MLflow with correlated telemetry

### 3. Optimization Flow

When you launch an optimization job:

1. Derives training data from eval results
   - **BootstrapFewShot/MIPROv2**: Use high-scoring examples as demos
   - **GEPA**: Use judge rationale as feedback
   - Bundles can restrict optimization targets with `bundle.toml` `optimization.target_output_fields`
2. Runs DSPy optimizer (`compile()`)
3. Generates optimized artifact (e.g., `program.json`)
4. Creates temporary Git worktree
5. Writes artifact + updates `bundle.toml`
6. Commits and pushes to `optimization-<job-prefix>` branch
7. Runs follow-up eval (baseline vs optimized comparison)

**Result**: Review the branch in GitHub, then merge manually.

### 4. Git Integration

DSPy Trainer tracks bundle state:

- `github_repo_url`, `github_branch`, `github_subpath`
- `current_commit_sha` (what's checked out)
- `upstream_commit_sha` (what's on GitHub)
- `sync_status`: `synced`, `behind`, `ahead`, `diverged`, `sync_error`

Optimization writeback:
- Does **not** modify your tracked branch directly
- Pushes to a separate `optimization-*` branch
- You review and merge manually

---

## Advanced Topics

### Bundle Hooks

Supported bundle hooks and override points:

**Required in `module.py`:**

```python
def build_program():
    return YourProgram()
```

**Optional in `module.py`:**

```python
def build_lm():
    """Override LM construction if bundle needs custom LM setup"""
    return dspy.LM(model="...", api_key="...")
```

Notes:

- If `build_lm()` exists, DSPy Trainer prefers it over any selected LM Profile for eval/invoke/optimization execution.
- `build_lm()` must not require positional arguments.

**Optional on the built program instance for managed endpoint streaming:**

```python
class YourProgram(dspy.Module):
    def emit(self, question: str, emit):
        emit({"chunk": 1, "text": question[:2]})
        return dspy.Prediction(answer=question.upper())
```

Notes:

- Required only for `POST /bundle-endpoints/{id}/stream`
- The method must accept an `emit` callback parameter and return the final prediction payload

**Optional on program instance:**

```python
class YourProgram(dspy.Module):
    def dump_state(self):
        """Export optimized state for persistence"""
        return {"demos": self.predictor.demos}
    
    def load_state(self, state):
        """Load optimized state from bundle.toml"""
        self.predictor.demos = state["demos"]
```

Notes:

- If `bundle.toml` declares `optimized_program_state`, the built program must implement `load_state(state)`.
- Optimization writeback persists `program.dump_state()` when available. If `dump_state()` is absent, the saved state file will be an empty JSON object.
- Optimization also assumes `program.named_predictors()` exposes predictor output fields.

### Optimization Strategies

| Strategy | Best For | Training Data Source |
|----------|----------|---------------------|
| **BootstrapFewShot** | Few-shot learning | High-scoring eval examples |
| **MIPROv2** | Instruction + demo optimization | High-scoring eval examples |
| **GEPA** | Reflection-based optimization | Judge rationale as feedback |

### Dataset Schema

Each dataset record uses:

```json
{
  "input": {
    "<bundle_input_key>": "value"
  },
  "label": {
    "<metric_label_key>": "value"
  }
}
```

The bundle's `evaluation.input.fields` and `evaluation.label.fields` in `bundle.toml` define the expected keys.
For LLM-as-judge bundles, the label side can be rubric-style guidance such as `judge_instructions`, not only a single gold answer.

### MLflow Correlation

- One MLflow **experiment** per project
- One parent **run** per evaluation plan
- Task-level telemetry correlated to parent run
- Backend remains source of truth; MLflow is tracking plane

---

## Troubleshooting

### Bundle Validation Fails

**Check:**
- Does `module.py` define `build_program()`?
- Does `metric.py` define `judge_metric(example, prediction)`?
- Is `bundle.toml` valid TOML with `name`, `version`, `score_pass_threshold`?

**Debug:**
```bash
docker compose logs --tail=200 backend
```

### Eval Tasks Fail

**Common causes:**
- Missing/invalid LM Profile
- Bundle `requirements.txt` dependencies failed to install
- Judge metric crashed or returned invalid shape

**Debug:**
```bash
docker compose logs --tail=200 worker
```

**View task logs:** Check the Runs page in Web UI for per-task worker logs.

### GitHub Access Not Configured

**Symptoms:**
- Bundles page says "GitHub access not configured"

**Fix:**
1. Add `GITHUB_PAT` to `.env`
2. Restart services:
   ```bash
   docker compose up -d --force-recreate backend worker endpoint-worker
   ```

### Optimization Writeback Fails

**Symptoms:**
- Job reaches writeback and fails with "Author identity unknown"

**Fix:**
1. Add `GIT_COMMIT_NAME` and `GIT_COMMIT_EMAIL` to `.env`
2. Restart services:
   ```bash
   docker compose up -d --force-recreate backend worker
   ```

---

## Configuration

### Environment Variables

Key variables in `.env`:

| Variable | Purpose | Required |
|----------|---------|----------|
| `GITHUB_PAT` | GitHub API access for bundle import/sync | ✅ |
| `GIT_COMMIT_NAME` | Git author name for optimization commits | Recommended |
| `GIT_COMMIT_EMAIL` | Git author email for optimization commits | Recommended |
| `DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY` | Encrypts module environment entries and LM Profile provider API keys stored in Postgres | Required for module env UI and LM Profile API key storage |
| `DSPY_TRAINER_TOTAL_WORKERS` | Number of general worker containers in Compose | Optional |
| `DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY` | Deployment-wide maximum concurrent bundle dependency installs (default `8`) | Optional |
| `DSPY_TRAINER_TOTAL_ENDPOINT_WORKER_REPLICAS` | Number of dedicated endpoint worker containers in Compose | Optional |
| `DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS` | Seconds before an endpoint-worker heartbeat is marked stale | Optional |
| `DSPY_TRAINER_DEPLOYER_ENDPOINT_READINESS_TIMEOUT_SECONDS` | Seconds allowed for an exact managed-image worker to report ready before rollback | Optional |
| `DSPY_TRAINER_DEPLOYER_ENDPOINT_DRAIN_TIMEOUT_SECONDS` | Seconds allowed for an active invocation to drain before forced removal | Optional |
| `DSPY_TRAINER_DEPLOYER_IMAGE_RETENTION_COUNT` | Ready images retained per module, minimum `2`; referenced images are always protected | Optional |
| `DSPY_TRAINER_COMPOSE_NETWORK_PROJECT_LABEL` | Exact Compose project label required on the selected runtime network | ✅ in deployment environments |
| `DSPY_TRAINER_POSTGRES_DSN` | Postgres connection | ✅ (auto in Compose) |
| `DSPY_TRAINER_REDIS_URL` | Redis connection | ✅ (auto in Compose) |

See [`.env.sample`](.env.sample) for full reference.

Generate `DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY` with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### LM Profile Configuration

LM Profiles store direct provider endpoint configuration in the Web UI:

1. Go to LM Profiles page
2. Click "Create Profile"
3. Enter model name (e.g., `openai/gpt-4o-mini`)
4. Add API key or select existing key
5. Configure temperature, max tokens, etc.

LM Profiles store the provider model, API base, model type, optional LM class override, and optional provider API key.

### Managed Endpoint Workers

Managed bundle endpoints do not execute inside the backend container. The backend authenticates, selects a ready build-pinned worker, enqueues, and relays responses. The deployer creates deterministic per-endpoint slots from immutable revision-image IDs on the actual Compose network; the static `endpoint-worker` service remains migration-only for deployments explicitly marked `legacy_static`.

- Each endpoint's `pinned_worker_count` is the desired managed container count. Slots, names, and retirement order are deterministic.
- A rollout starts one target slot at a time and requires a live, claim-eligible `managed_image` registration with matching endpoint and desired/warmed build and revision before the old slot drains. The endpoint's active module, runtime environment, and trace provenance remain unchanged until the target slots are ready and the deployment and endpoint module are promoted atomically.
- Draining atomically marks the managed container ineligible and blocks the worker's next claim. A restart cannot re-register a draining slot, while an already claimed invocation may finish until the configured timeout; a timed-out removal is recorded with bounded container logs.
- Removed managed-container rows remain durable history. A replacement may reuse its deterministic container name with a new Docker ID only after the prior row reaches removed; PostgreSQL rejects two non-removed rows with the same name.
- Failed target startup or readiness restores the old revision capacity and preserves the failed build, revision, module, reason, and build-log link in deployment status. The automatic scheduler suppresses that exact failed build across reconciliation cycles and restarts until a newer build or explicit retry exists.
- Reconciler restart adopts only containers with the complete ownership/provenance label set, exact deployment owner, and exact immutable image ID. Scale-down and endpoint deletion remove only fully owned slots; similarly named, partially labeled, wrong-owner, and wrong-image containers are never adopted or deleted.
- Image cleanup retains at least the current and previous ready images per module and protects every active, target, previous, or failed deployment build and every build referenced by a non-removed managed container.
- Endpoint jobs use build/revision-specific Redis queues. After `BRPOP`, an atomic PostgreSQL claim serializes with drain; a losing claim puts the exact job back on the same queue once rather than executing it.

The revision-image build layer is deliberately separate from endpoint scheduling and rollout. Given one immutable revision snapshot and its content digest, it creates a deterministic tar context, uses the configured immutable backend image ID as `FROM`, runs `runtime.system_dependency_commands` before `requirements.txt`, writes a sorted Python package manifest to `/opt/dspy-trainer/python-manifest.txt`, bakes the bundle at `/opt/dspy-bundle`, and installs the platform endpoint-worker entrypoint. Builds use the local Docker Engine cache and host architecture only; the builder has no registry login, pull, or push path.

The context includes every bundle asset except platform control directories (including `.git` and Python/tool caches) and secret-like files such as `.env*`, private keys, and credential files. A bundle may explicitly include a required in-root secret-like fixture with exact paths under `[image_build]` in `bundle.toml`, for example `include_files = ["fixtures/test.key"]`. Overrides cannot escape the snapshot root, name directories, or restore platform control directories. Runtime environment entries, provider keys, GitHub credentials, and host environment values are never build inputs.

Local tags use `<repository>:<revision>-<build-digest-prefix>`; the digest includes the immutable build ID and generation, so rebuilding a revision never mutates an earlier tag or record. Every image has the complete `io.dspy-trainer.*` ownership and provenance set: `platform-owner`, stack `owner`, `managed-kind`, `module-id`, `revision-id`, `build-id`, `build-generation`, `source-commit`, `source-content-digest`, `dependency-digest`, `build-digest`, `base-image-id`, `platform-version`, and `schema-version`. Image adoption or deletion must first validate the full label set and stack owner; a successful build is ready only after inspecting the returned immutable image ID and matching every label.

---

## Developer Process

This repository uses `bd` (beads) for task tracking.

1. Check available work with `bd ready --json`
2. Claim the bead before editing code
3. Make the smallest correct change
4. Add or update tests for behavior changes
5. Run the relevant quality gates
6. Commit and push before ending the session

Useful verification commands:

```bash
# backend
python -m pytest backend/tests

# targeted backend tests
python -m pytest backend/tests/test_bundle_validator.py backend/tests/test_modules_api.py

# frontend
cd web
npm test
npm run build

# compose health
docker compose ps
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:8080/api/ready
curl -fsS http://localhost:8080/mlflow/
```

If you change backend runtime behavior that affects running containers, rebuild or recreate the affected services before handoff.

---

## API Documentation

The backend exposes a comprehensive REST API. Key endpoints:

**Bundles:**
- `GET /samples/module-bundles` - List downloadable sample bundles
- `GET /samples/module-bundle` - Download a sample bundle zip, optionally with `?sample=<slug>`
- `POST /modules/import` - Import GitHub bundle
- `POST /modules/{id}/validate` - Validate bundle contract
- `POST /modules/{id}/smoke-test` - Run a one-off bundle smoke test
- `GET /modules/{id}/sync-status` - Check Git sync state
- `POST /modules/{id}/sync` - Pull latest from GitHub
- `GET /modules/{id}/endpoints` - List managed bundle endpoints
- `POST /modules/{id}/endpoints` - Create a managed bundle endpoint
- `PATCH /modules/{id}/endpoints/{endpointId}` - Rename an endpoint
- `POST /modules/{id}/endpoints/{endpointId}/regenerate-key` - Rotate the endpoint key
- `DELETE /modules/{id}/endpoints/{endpointId}` - Delete an endpoint
- `POST /bundle-endpoints/{endpointId}/invoke` - Invoke a bundle synchronously with JSON output
- `POST /bundle-endpoints/{endpointId}/stream` - Invoke a bundle over SSE with `delta` and `final` events

**Datasets:**
- `POST /evaluation-datasets` - Create dataset
- `GET /evaluation-datasets` - List datasets
- `PATCH /evaluation-datasets/{id}` - Update dataset items and metadata

**Evaluation:**
- `POST /evaluation-plans` - Create reusable eval plan
- `POST /agent-run-plans` - Create an agent run plan
- `POST /agent-run-plans/{id}/enqueue` - Launch a created agent run plan
- `GET /agent-run-plans/{id}` - Get run status/results

**Optimization:**
- `POST /optimization/jobs` - Launch optimization job
- `GET /optimization/jobs/{id}` - Get job status
- `POST /optimization/jobs/{id}/cancel` - Cancel running job

**LM Profiles:**
- `POST /lm-profiles` - Create LM profile
- `GET /lm-profiles` - List profiles
- `PATCH /lm-profiles/{id}` - Update profile

**Interactive API docs:** http://localhost:8080/api/docs (when running through the default local Caddy proxy)

---

## Development Guide

### Running Tests

**Backend:**
```bash
cd backend/
pytest
```

**Frontend:**
```bash
cd web/
npm test
```

### Project Structure

```
dspy-trainer/
├── backend/           # FastAPI backend + worker
│   ├── app/
│   │   ├── main.py           # API routes
│   │   ├── services.py       # Orchestration layer
│   │   ├── config.py         # Pydantic settings
│   │   ├── executor/         # Bundle execution
│   │   ├── validator/        # Bundle validation
│   │   └── lm/               # LM runtime adapters
│   ├── worker.py             # Redis queue worker
│   ├── tests/
│   └── sample_bundles/       # Example bundle
├── web/               # React frontend
│   ├── src/
│   │   ├── bundles/
│   │   ├── plans/
│   │   ├── runs/
│   │   ├── datasets/
│   │   ├── optimization/
│   │   └── lmProfiles/
│   └── package.json
├── docs/              # Architecture & ops docs
├── ops/               # operational helpers
├── dspy/              # DSPy reference submodule
└── docker-compose.yml
```

### Adding New Features

**Conventions:**
- Backend: Extend `AppServices` in `services.py`
- Frontend: Page-centric components with colocated state
- Tests: Required for new functionality

See [`.serena/memories/conventions.md`](.serena/memories/conventions.md) for detailed patterns.

---

## FAQ

**Q: What's the difference between a bundle and a DSPy program?**  
A: A bundle is the packaging format DSPy Trainer uses. Your DSPy program lives in `module.py` inside the bundle.

**Q: Can I use my own LLM provider?**  
A: Yes. Create an LM Profile with your provider's model name, base URL, and API key if required.

**Q: Do I need to use GitHub?**  
A: Yes, for now. GitHub-first design enables commit provenance and collaborative workflows.

**Q: Can I run this on a remote server?**  
A: Yes. It's a Docker Compose stack, so adjust ports and DNS as needed. The current web shell is unauthenticated, so no Auth0 or hosted login setup is required.
A: Yes. It's a Docker Compose stack with Caddy providing the default local reverse-proxy surface. Adjust ports, DNS, and proxy URLs as needed. The current web shell is unauthenticated, so no Auth0 or hosted login setup is required.

**Q: How do I scale worker capacity?**  
A: Set the worker replica env vars in `.env`, then recreate the stack:
```env
DSPY_TRAINER_TOTAL_WORKERS=4
DSPY_TRAINER_TOTAL_ENDPOINT_WORKER_REPLICAS=16
DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS=300
DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY=8
```

**Q: Can I use this for production LLM apps?**  
A: DSPy Trainer is designed for **development and optimization**. Once you've optimized your bundle, deploy the DSPy program itself (not DSPy Trainer) in production.

---

## Contributing

This project uses `bd` (beads) for issue tracking:

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

See [`AGENTS.md`](AGENTS.md) for detailed contribution guidelines.

---

## Revision Image Builds

The backend first freezes source bytes into a read-only, content-addressed snapshot, validates that immutable snapshot, and only then records a passed/synced revision and enqueues its image build. Snapshot or validation failure leaves passed/synced/current-revision state unchanged. Coordinator or image-build failure after revision finalization remains independent; module and revision responses expose it separately in `image_build`.

Operator APIs:

- `GET /revision-image-builds` lists builds; filter with `module_id`, `revision_id`, or `status`, and paginate with `limit`/`offset`.
- `GET /revision-image-builds/{build_id}` returns build status and provenance without the build log.
- `GET /revision-image-builds/{build_id}/logs?offset=0&limit=16384` returns a bounded byte window of retained logs.
- `POST /revision-image-builds/{build_id}/retry` queues a new generation for an eligible failed or ready build.
- `POST /revision-image-builds/rebuild-all` queues one current-base generation for every currently eligible revision.

Retry and rebuild-all requests reject duplicate active work with a stable `409` `build_conflict`. A previously ready image remains available while a newer generation is queued, building, or failed.

## Resources

- **DSPy Framework**: https://dspy.ai

- **MLflow Docs**: https://mlflow.org/docs/latest/index.html
- **Compose Runbook**: [`docs/COMPOSE_RUNBOOK.md`](docs/COMPOSE_RUNBOOK.md)

---

## License

[Add your license here]

---

**Built for DSPy developers who want to iterate fast without building infrastructure from scratch.**
