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
- **No vendor lock-in**: Uses LiteLLM for model routing—swap providers without code changes

---

## Quick Start

### 1. Configure Environment

```bash
cp .env.sample .env
# Edit .env - at minimum, add your GITHUB_PAT
# If you plan to store module environment entries in the UI,
# also generate DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Start the Stack

```bash
docker compose pull --ignore-pull-failures
docker compose build --pull
docker compose up -d --remove-orphans
```

If MLflow trace or run requests time out under load, increase `MLFLOW_WEB_WORKERS` in `.env` before restarting the stack.

### 3. Access the Platform

| Service | URL |
|---------|-----|
| **Web UI** | http://localhost:3000 |
| **Backend API** | http://localhost:8000 |
| **MLflow** | http://localhost:5001 |
| **LiteLLM Proxy** | http://localhost:4000 |

### 4. Your First Eval

1. **Download** the sample bundle from the Bundles page
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
├── metric.py          # Judge function with judge_metric(example, prediction)
├── bundle.toml        # Metadata and configuration
└── requirements.txt   # Optional: Python dependencies
```

**Key points:**
- GitHub-backed with commit provenance
- Validated before first run
- Can declare optimized program state for loading
- Can optionally narrow optimization targets via `optimization.target_output_fields`

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

An **LM profile** configures model routing through LiteLLM:

- Model name (e.g., `openai/gpt-4o-mini`)
- Temperature, max tokens, timeouts
- API keys and virtual key aliases
- Swap providers without changing bundle code

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
┌──────▼──────┐
│  Backend    │  FastAPI - validation, orchestration, APIs
└──────┬──────┘
       │
┌──────▼──────┐
│   Worker    │  Eval execution, optimization jobs (scales)
└──────┬──────┘
       │
┌──────┴──────────────────────────────┐
│  Postgres  │  Redis  │  MLflow  │  LiteLLM │
└─────────────────────────────────────┘
```

**Services:**
- **Backend**: Control plane (FastAPI)
- **Worker**: Execution engine (async job processing)
- **Postgres**: Primary app store
- **Redis**: Queue + worker coordination
- **MLflow**: Experiment tracking with metadata stored in a dedicated Postgres `mlflow` schema and artifacts on a Docker volume
- **LiteLLM**: Unified LLM gateway

For current stack operations and service expectations, see [`docs/COMPOSE_RUNBOOK.md`](docs/COMPOSE_RUNBOOK.md).

---

## Creating Your Own Bundle

### Minimum Required Files

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

**`metric.py`**
```python
def judge_metric(example, prediction, trace=None):
    # Your scoring logic here. Labels can contain judge-side guidance for an LLM judge.
    instructions = example.label["judge_instructions"]
    score = 1.0 if instructions and prediction.answer else 0.0
    
    return {
        "score": score,
        "rationale": "Match" if score > 0 else "Mismatch",
        "flags": [],
        "raw_response": {}
    }
```

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

[optimization]
target_output_fields = ["answer"]
```

`optimization.target_output_fields` is optional. When provided, DSPy Trainer uses only those predictor output fields as optimization targets instead of reusing every key present in prior prediction payloads. This is useful for bundles whose predictions include debug traces or other bulky internal fields that should still be judged during evals but should not be optimized directly.

### Testing Locally

```bash
cd your-bundle/
python run_agent.py --question "Your test question here"
```

The sample bundle includes `run_agent.py` for local development. Copy it to your bundle and customize as needed.

**Example**: See [`backend/sample_bundles/example-bundle/`](backend/sample_bundles/example-bundle/)

---

## Workflow Deep Dive

### 1. Bundle Validation

When you import a bundle, DSPy Trainer:

- ✅ Verifies `module.py` has `build_program()`
- ✅ Verifies `metric.py` has `judge_metric(example, prediction)`
- ✅ Checks `bundle.toml` structure
- ✅ Validates required fields
- ✅ Tracks Git commit SHA for provenance

### 2. Evaluation Execution

When you run an eval plan:

1. Worker loads bundle from checkout
2. Installs `requirements.txt` if present (cached)
3. Builds program via `build_program()`
4. Runs program on each dataset item
5. Calls `judge_metric()` with prediction
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

**Optional in `module.py`:**

```python
def build_lm():
    """Override LM construction if bundle needs custom LM setup"""
    return dspy.LM(model="...", api_key="...")
```

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
   docker compose up -d --force-recreate backend worker
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
| `DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY` | Encrypts module environment entries stored in Postgres | Required for module env UI |
| `DSPY_TRAINER_POSTGRES_DSN` | Postgres connection | ✅ (auto in Compose) |
| `DSPY_TRAINER_REDIS_URL` | Redis connection | ✅ (auto in Compose) |
| `LITELLM_MASTER_KEY` | LiteLLM proxy auth | ✅ (auto in Compose) |

See [`.env.sample`](.env.sample) for full reference.

Generate `DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY` with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### LiteLLM Configuration

LiteLLM runs as an internal proxy. Model routing is configured via **LM Profiles** in the Web UI:

1. Go to LM Profiles page
2. Click "Create Profile"
3. Enter model name (e.g., `openai/gpt-4o-mini`)
4. Add API key or select existing key
5. Configure temperature, max tokens, etc.

LM Profiles provision virtual keys in LiteLLM dynamically.

---

## API Documentation

The backend exposes a comprehensive REST API. Key endpoints:

**Bundles:**
- `POST /modules/import` - Import GitHub bundle
- `POST /modules/{id}/validate` - Validate bundle contract
- `GET /modules/{id}/sync-status` - Check Git sync state
- `POST /modules/{id}/sync` - Pull latest from GitHub

**Datasets:**
- `POST /evaluation-datasets` - Create dataset
- `GET /evaluation-datasets` - List datasets
- `PATCH /evaluation-datasets/{id}/items` - Update dataset items

**Evaluation:**
- `POST /evaluation-plans` - Create reusable eval plan
- `POST /evaluation-plans/{id}/run` - Launch eval from plan
- `GET /agent-run-plans/{id}` - Get run status/results

**Optimization:**
- `POST /optimization/jobs` - Launch optimization job
- `GET /optimization/jobs/{id}` - Get job status
- `POST /optimization/jobs/{id}/cancel` - Cancel running job

**LM Profiles:**
- `POST /lm-profiles` - Create LM profile
- `GET /lm-profiles` - List profiles
- `PATCH /lm-profiles/{id}` - Update profile

**Interactive API docs:** http://localhost:8000/docs (when running)

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
│   │   └── lm/               # LiteLLM integration
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
├── ops/               # LiteLLM proxy config
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
A: Yes! LiteLLM supports 100+ providers. Just create an LM Profile with your provider's model name.

**Q: Do I need to use GitHub?**  
A: Yes, for now. GitHub-first design enables commit provenance and collaborative workflows.

**Q: Can I run this on a remote server?**  
A: Yes. It's a Docker Compose stack, so adjust ports, DNS, and reverse proxying as needed. The current web shell is unauthenticated, so no Auth0 or hosted login setup is required.

**Q: How do I scale worker capacity?**  
A: Increase worker replicas in `docker-compose.yml`:
```yaml
worker:
  deploy:
    replicas: 4
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

## Resources

- **DSPy Framework**: https://dspy.ai
- **LiteLLM Docs**: https://docs.litellm.ai
- **MLflow Docs**: https://mlflow.org/docs/latest/index.html
- **Compose Runbook**: [`docs/COMPOSE_RUNBOOK.md`](docs/COMPOSE_RUNBOOK.md)

---

## License

[Add your license here]

---

**Built for DSPy developers who want to iterate fast without building infrastructure from scratch.**
