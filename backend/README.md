# Backend

Minimal FastAPI backend and worker skeleton for the compose platform.

## Environment

All settings use the `DSPY_TRAINER_` prefix.

- `DSPY_TRAINER_POSTGRES_DSN` (required)
- `DSPY_TRAINER_REDIS_URL` (default: `redis://localhost:6379/0`)
- `DSPY_TRAINER_QUEUE_NAME` (default: `dspy-trainer:jobs`)
- `DSPY_TRAINER_MLFLOW_TRACKING_URI` (default: `http://localhost:5000`)
- `DSPY_TRAINER_LITELLM_BASE_URL` (default: `http://localhost:4000`)
- `DSPY_TRAINER_LITELLM_API_KEY` (optional; required when LiteLLM health endpoint is protected)
- `DSPY_TRAINER_BACKEND_HOST` (default: `0.0.0.0`)
- `DSPY_TRAINER_BACKEND_PORT` (default: `8000`)

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

export DSPY_TRAINER_POSTGRES_DSN='postgresql://postgres:postgres@localhost:5432/dspy_trainer'
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Health endpoints:

- `GET /health`
- `GET /ready`

Run worker:

```bash
python backend/worker.py
```
