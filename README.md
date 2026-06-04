# dspy-trainer

DSPy Trainer is a local evaluation and optimization workbench for DSPy programs. It gives you a GitHub-backed bundle workflow, repeatable eval runs, optimization jobs, worker orchestration, and MLflow-linked provenance without requiring you to build all of that plumbing yourself.

Developer bootstrap and day-2 operations are documented in `docs/COMPOSE_RUNBOOK.md`.

## QuickStart

1. Copy `.env.sample` to `.env` and fill in at least:
   - `GITHUB_PAT`
   - any infra/auth values you want to override locally
2. Start the stack from repo root:

```bash
docker compose pull --ignore-pull-failures
docker compose build --pull
docker compose up -d --remove-orphans
```

3. Open the app:
   - Web UI: `http://localhost:3000`
   - Backend: `http://localhost:8000`
   - MLflow: `http://localhost:5001`
   - LiteLLM proxy: `http://localhost:4000`
4. Download the sample bundle from the Bundles page.
5. Push that bundle to a GitHub repository.
6. Import the repository from the Bundles page.
7. Create an LM Profile in the app.
8. Validate the bundle, create an Evaluation Plan, run evals, then launch an Optimization Job from a successful run.

## What is this?

DSPy Trainer is the control plane around a DSPy program lifecycle:

- bundle authoring
- bundle validation
- repeated evaluation runs
- optimization dataset derivation
- optimization execution
- follow-up baseline vs optimized comparison
- Git-backed writeback with provenance

It is intentionally GitHub-first. The tracked artifact is a repository checkout plus commit SHA, not an opaque uploaded zip.

### What is DSPy?

DSPy is a framework for building LLM programs in Python using typed signatures, modules, predictors, optimizers, and evaluation utilities. Instead of treating prompts as raw strings everywhere, you define structured program components and let DSPy handle execution and optimization patterns.

In this project, a bundle is a small DSPy program package that exposes:

- a runnable program via `build_program()`
- a judge function via `judge_metric(...)`
- metadata in `bundle.toml`

### What is an eval?

An eval is a repeatable test run of a bundle against prepared input/label examples.

At runtime, DSPy Trainer:

1. loads the bundle program
2. executes the program on each input
3. calls the bundle's `judge_metric(example, prediction, trace=None)`
4. records score, rationale, pass/fail, raw outputs, and worker logs
5. aggregates results into a run summary and MLflow-linked provenance

## DSPy Trainer Specific Terminology

### What is a bundle?

A bundle is the unit of code that DSPy Trainer tracks and runs.

For the current architecture, a compatible bundle is a GitHub repository root or GitHub subfolder containing:

- `module.py`
- `metric.py`
- `bundle.toml`

Optional bundle files include:

- `requirements.txt`
- an optimized program state file referenced by `bundle.toml`
- helper scripts such as `run_agent.py`

The validator enforces the core contract before the bundle becomes runnable.

### What is an Evaluation Plan?

An Evaluation Plan is a saved question set and execution recipe.

It stores:

- the target bundle
- the LM profile to run with
- the list of eval rows
- `runs_per_question`
- `max_workers`

When you click Run, DSPy Trainer creates an `AgentRunPlan` from the saved Evaluation Plan and enqueues worker tasks.

### What is an Optimization Job?

An Optimization Job is a background optimization run against a tracked bundle.

It includes:

- source bundle revision and commit provenance
- source run plan reference
- optimization strategy (`BootstrapFewShot`, `MIPROv2`, or `GEPA`)
- execution and helper LM profile routing
- comparison of baseline vs optimized performance
- resulting optimization branch metadata

Successful jobs create:

- an optimization artifact (`program.json` or equivalent state)
- a follow-up evaluation plan and eval run
- a Git branch named `optimization-<job-prefix>` for manual merge

### How do I create my own compatible bundle?

Start from the sample bundle and keep the contract minimal.

Required files:

1. `module.py`
2. `metric.py`
3. `bundle.toml`

Required code behavior:

- `module.py` must define at least one DSPy `Signature`
- `module.py` must define at least one `dspy.Module` subclass
- `module.py` must expose `build_program()` with no required arguments
- `metric.py` must expose `judge_metric(example, prediction)` or `judge_metric(example, prediction, trace=None)`
- `bundle.toml` must include non-empty `name` and `version`
- `bundle.toml` must include numeric `score_pass_threshold` between `0.0` and `1.0`

Optional but useful additions:

- `requirements.txt` for Python dependencies
- `run_agent.py` for local manual testing
- `optimized_program_state` in `bundle.toml` when you want to load saved program state

### How do I test my bundle on the cli before running evals?

The sample bundle includes `run_agent.py` specifically for the local make-it-work loop.

Typical flow:

1. set model credentials in `.env`
2. run the bundle locally
3. inspect the raw prediction result
4. repeat until the basic behavior is good enough for evals

Example:

```bash
python run_agent.py --question "Ticket: I was charged twice for order #8842.
History: Customer already contacted support yesterday and shared the order receipt."
```

The sample runner auto-loads `.env` with `python-dotenv` and reads:

- `DSPY_MODEL` or `OPENAI_MODEL`
- `DSPY_API_BASE` or `OPENAI_API_BASE`
- `DSPY_API_KEY` or `OPENAI_API_KEY`

If you are using an LM Profile virtual key through LiteLLM, point the script at the proxy and use the profile alias model name.

### How do I prepare Input/Output data for evals?

There are two layers to think about:

1. the generic backend eval shape
2. the current UI authoring shape

Generic backend eval shape:

```json
{
  "input": {"question": "..."},
  "label": {"expected": "..."}
}
```

Current UI workflow:

- each row is authored as a question
- each row stores one expected answer string
- the builder converts that to `input.question` and `label.expected`

Your `judge_metric(...)` can interpret `label.expected` however you want, but it must return:

```json
{
  "score": 1.0,
  "rationale": "why",
  "flags": [],
  "raw_response": {}
}
```

### How do I convert an eval to an optimization run?

Current flow:

1. run an Evaluation Plan
2. open the resulting run in Runs
3. launch an Optimization Job
4. choose the source run plan
5. choose the optimization strategy
6. choose execution/helper LM profiles
7. set the target bundle version

The optimization launcher derives training data from the source run plan:

- `BootstrapFewShot` and `MIPROv2` derive demo-style records from eval passes
- `GEPA` derives feedback-style records from eval outputs and judge rationale

### How do I merge my optimizations in?

Optimization writeback does not update your tracked main branch directly anymore.

Instead, DSPy Trainer:

1. creates a temporary worktree from the tracked bundle checkout
2. writes optimization output into that worktree
3. commits the result
4. pushes to a branch named `optimization-<job-prefix>`

Your merge flow is:

1. review the optimization branch in GitHub
2. merge it manually into your normal branch
3. return to DSPy Trainer
4. refresh sync status on the bundle
5. sync the tracked branch forward

## Deep Dive: How it all works

### How dspy trainer handles bundles and git

Bundle import path:

1. `POST /modules/import` receives GitHub repo URL, branch, and optional subpath.
2. Backend clones the repo into a persistent checkout root.
3. If `github_subpath` is set, validation runs against that subfolder.
4. Validator checks `module.py`, `metric.py`, and `bundle.toml`.
5. Bundle metadata, checkout path, sync state, revision history, commit SHA, and diagnostics are persisted in Postgres.

Git-specific concepts tracked per module:

- `github_repo_url`
- `github_branch`
- `github_subpath`
- `checkout_path`
- `current_commit_sha`
- `upstream_commit_sha`
- `sync_status`
- `current_revision_id`

Sync model:

- `refresh sync status` fetches upstream and compares `HEAD`, `FETCH_HEAD`, and merge-base
- sync states include `synced`, `behind`, `ahead`, `diverged`, and `sync_error`
- mutating operations fail fast if the bundle is `behind`, `diverged`, or `sync_error`

Runtime dependency model:

- if a bundle has `requirements.txt`, backend/worker install dependencies before execution
- installs are cached per process by the hash of `requirements.txt`

### How dspy trainer handles evals

Eval flow:

1. Evaluation Plan is created from authored rows.
2. Running that plan creates an `AgentRunPlan`.
3. `enqueue` expands the run into `AgentRunTask` rows.
4. Redis queues the tasks.
5. Worker processes pull tasks and execute bundle code.
6. Each task loads the bundle program and runs one item.
7. `judge_metric(...)` produces score/rationale/flags/raw response.
8. Task rows are updated with:
   - prediction payload
   - numeric score
   - pass/fail
   - rationale
   - worker log
9. Plan reconciliation updates rollup status and aggregate counts.

MLflow correlation:

- one parent MLflow run is created per `AgentRunPlan`
- task-level execution is correlated back to that parent
- run metadata includes bundle revision / commit provenance

Current status model for eval runs:

- `draft`
- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

### How dspy trainer handles optimizations

Optimization flow:

1. User launches an Optimization Job from a validated bundle and source run plan.
2. Backend records source bundle provenance on the job.
3. Worker derives a training dataset from eval results if needed.
4. Worker executes DSPy optimization.
5. The optimized program state is written to an artifact directory.
6. Backend prepares a writeback bundle and creates a follow-up eval run.
7. Follow-up eval produces the optimized score.
8. Backend commits the optimized output to `optimization-<job-prefix>`.
9. Optimization job stores:
   - resulting bundle branch
   - resulting bundle commit SHA
   - resulting bundle revision ID
   - resulting bundle version

Important behavior:

- the tracked bundle stays pinned to its configured branch until you merge and sync
- optimization writeback updates `bundle.toml` with `optimized_program_state` and `source_optimization_job_id`
- successful optimization jobs create a follow-up Evaluation Plan and eval run automatically

### List of overrideable methods in the bundle

Required in `module.py`:

- `build_program()`
  - must exist
  - must not require positional arguments
  - should return a `dspy.Module`

Optional in `module.py`:

- `build_lm()`
  - if defined, it must not require positional arguments
  - used when the bundle wants to provide its own LM construction path
  - execution can still be overridden by DSPy Trainer LM profiles

Required in `metric.py`:

- `judge_metric(example, prediction)`
- or `judge_metric(example, prediction, trace=None)`

Optional program-state hooks on the program instance returned by `build_program()`:

- `dump_state()`
- `load_state(state)`

These matter when using `optimized_program_state` in `bundle.toml`.

Required `bundle.toml` fields:

- `name`
- `version`
- `score_pass_threshold`

Optional `bundle.toml` fields currently used by DSPy Trainer:

- `optimized_program_state`
- `dspy_version`
- `source_optimization_job_id` (written during optimization writeback)

## Notes

- GitHub access is configured server-side through `GITHUB_PAT` / `DSPY_TRAINER_GITHUB_PAT`.
- The web UI never collects or stores the PAT.
- Sample bundle bootstrap and local runner examples live under `backend/sample_bundles/example-bundle/`.
- LiteLLM runs as an internal proxy and model routing is expected to be configured through LM Profiles created in the app, not through repo-level upstream model env vars.
