# Compose Runbook

This runbook documents developer bootstrap and day-2 operations for the current Docker Compose stack in this repository.

References:
- `AGENTS.md`

Current shell note:
- The local web shell is currently unauthenticated.
- No Auth0, hosted login, or SSO bootstrap is required to access `http://localhost:8080`.

## Stack Services

Operator/browser traffic goes through Caddy on `http://localhost:${CADDY_HTTP_PORT:-8080}` by default:
- `/` → `web`
- `/api/` → `backend`
- `/mlflow/` → `mlflow`

Direct host ports remain published only for local developer tooling where needed:
- `postgres` (`5432`)
- `redis` (`6379`)

Internal Compose services:
- `postgres`
- `redis`
- `mlflow`
- `backend`
- `worker`
- `endpoint-worker`
- `web`
- `caddy`

Named volumes:
- `postgres_data` for PostgreSQL data
- `mlflow_artifacts` for MLflow artifacts (MLflow metadata is stored in the Postgres `mlflow` schema)
- `bundles_data` shared between `backend` and `worker` at `/tmp/dspy-trainer/bundles` for uploaded module bundles
- `optimization_artifacts_data` shared between `backend` and `worker` at `/tmp/dspy-trainer/optimization_artifacts` so succeeded optimization artifacts can be materialized into new bundles

MLflow concurrency can be tuned with `MLFLOW_WEB_WORKERS` in `.env` (default `4`). MLflow is started with `MLFLOW_STATIC_PREFIX` (default `/mlflow`) so its UI and assets load correctly behind the local reverse proxy. Compose also forwards `CADDY_HTTP_PORT` into the MLflow container so its allowed-hosts list stays aligned if you move the proxy off the default `8080` port. Compose now reads worker replica counts from `.env` via `DSPY_TRAINER_TOTAL_WORKERS` and `DSPY_TRAINER_TOTAL_ENDPOINT_WORKER_REPLICAS`, and it forwards `DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS` when operators need to tune endpoint-worker stale detection.

## Developer Bootstrap

Before starting the stack, ensure `.env` contains `GITHUB_PAT` if you want to import, sync, or push GitHub-backed bundles. Backend and worker read that variable server-side; the web UI only reports whether GitHub access is configured. GitHub imports may target either the repo root or a configured bundle subfolder. Optimization writeback now pushes to an `optimization-<job-prefix>` branch for manual merge, so also set `GIT_COMMIT_NAME` and `GIT_COMMIT_EMAIL` (defaults are provided if omitted).

The default local `.env.sample` now points the web build at relative proxy URLs (`/api`, `/mlflow`) and exposes Caddy on `CADDY_HTTP_PORT=8080`. It also defines `DSPY_TRAINER_TOTAL_WORKERS`, `DSPY_TRAINER_TOTAL_ENDPOINT_WORKER_REPLICAS`, and `DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS` so Compose replica counts and endpoint-worker stale-detection timing can be adjusted from `.env`. Override those values before rebuilding/recreating if you need different worker counts, host/port, or absolute public URLs.

Secret storage note:
- `DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY` is required if you want to store module environment entries or LM Profile provider API keys in Postgres.
- Generate it once and keep it stable across restarts.

LM Profile setup note:
- The repo does not ship a default upstream model configuration.
- Normal operator flow is to start the stack, then create LM Profiles in the app UI.
- Each LM Profile stores the direct provider endpoint, model identifier, optional LM class override, and optional provider API key.

Managed endpoint worker note:
- Compose-backed `endpoint-worker` containers self-register with the backend's durable endpoint-worker registry.
- Operator assignment and readiness are driven directly by that live registry, not by an env-defined logical worker roster.

Bundle runtime note:
- If a tracked bundle contains `requirements.txt`, backend and worker install those dependencies automatically before executing the bundle.

Run from repository root:

```bash
docker compose pull --ignore-pull-failures
docker compose build --pull
docker compose up -d --remove-orphans
```

## Non-Interactive Operations

### Startup

```bash
docker compose up -d --remove-orphans
```

### Teardown

```bash
docker compose down --remove-orphans
```

To also remove local volumes (destructive):

```bash
docker compose down --volumes --remove-orphans
```

### Logs

All services:

```bash
docker compose logs --timestamps --tail=200
```

Follow key services:

```bash
docker compose logs -f --timestamps backend worker
```

### Rebuild

Rebuild all images and restart:

```bash
docker compose build --pull
docker compose up -d --remove-orphans
```

Rebuild only backend and worker:

```bash
docker compose build --pull backend worker
docker compose up -d --remove-orphans backend worker
```

## Health Verification

### Compose Health Status

```bash
docker compose ps
```

Expected: all services show `running` and health-enabled services become `healthy`.

### Endpoint Checks from Host

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
curl -fsS http://localhost:3000/health
curl -fsS "http://localhost:${CADDY_HTTP_PORT:-8080}/health"
curl -fsS "http://localhost:${CADDY_HTTP_PORT:-8080}/api/health"
curl -fsS "http://localhost:${CADDY_HTTP_PORT:-8080}/api/ready"
curl -fsS "http://localhost:${CADDY_HTTP_PORT:-8080}/mlflow/"
```

### Backend Dependency Checks from Container

```bash
docker compose exec -T backend python -c "import os, redis; redis.Redis.from_url(os.environ['DSPY_TRAINER_REDIS_URL']).ping(); print('redis ok')"
docker compose exec -T backend python -c "import os, urllib.request; urllib.request.urlopen(os.environ['DSPY_TRAINER_MLFLOW_TRACKING_URI'], timeout=5); print('mlflow ok')"
docker compose exec -T backend python -c "import os; print('github ok' if (os.environ.get('DSPY_TRAINER_GITHUB_PAT') or os.environ.get('GITHUB_PAT')) else 'github missing')"
docker compose exec -T backend python -c "import os; print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_NAME', '')); print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_EMAIL', ''))"
```

## Troubleshooting

### LM Profile Connection or Provider Auth Issues

Symptoms:
- LM Profile `Test connection` fails in the UI.
- Eval or optimization logs contain provider auth, URL, or model-not-found errors.

Checks:
- Confirm the LM Profile `api_base`, `model`, and `model_type` match the target provider.
- Re-enter the provider API key in the LM Profile if credentials changed.
- If the bundle needs additional env vars (for example Azure API version), configure them on the module import and recreate the run.

### Backend Not Ready

Symptoms:
- `curl -fsS http://localhost:8080/api/ready` fails.
- `docker compose ps` shows `backend` as `starting` or `unhealthy`.

Checks:

```bash
docker compose logs --tail=200 backend
docker compose ps postgres redis mlflow backend
```

Remediation sequence:
1. Confirm upstream services are healthy (`postgres`, `redis`, `mlflow`).
2. Restart backend after dependencies are healthy:

```bash
docker compose restart backend
```

3. If startup still fails, recreate backend and worker with rebuild:

```bash
docker compose build --pull backend worker
docker compose up -d --force-recreate backend worker
```

### GitHub Bundle Access Not Configured

Symptoms:
- Bundles page says GitHub access is not configured.
- `POST /modules/import` for `source=github` fails with a GitHub access configuration error.

Checks:

```bash
docker compose exec -T backend python -c "import os; print(bool(os.environ.get('DSPY_TRAINER_GITHUB_PAT') or os.environ.get('GITHUB_PAT')))"
docker compose exec -T worker python -c "import os; print(bool(os.environ.get('DSPY_TRAINER_GITHUB_PAT') or os.environ.get('GITHUB_PAT')))"
```

Remediation:
- Add `GITHUB_PAT` to `.env`.
- Recreate backend and worker so they pick up the new env:

```bash
docker compose up -d --force-recreate backend worker
```

### Git Commit Identity Missing For Optimization Writeback

Symptoms:
- Optimization job reaches writeback and fails with `Author identity unknown`.

Checks:

```bash
docker compose exec -T backend python -c "import os; print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_NAME')); print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_EMAIL'))"
```

Remediation:
- Add `GIT_COMMIT_NAME` and `GIT_COMMIT_EMAIL` to `.env` if you want identities other than the defaults.
- Recreate backend and worker:

```bash
docker compose up -d --force-recreate backend worker
```

### Container Reconciliation (stale state)

```bash
docker compose down --remove-orphans
docker compose up -d --remove-orphans
```

If unresolved, perform a clean rebuild:

```bash
docker compose down --volumes --remove-orphans
docker compose build --pull
docker compose up -d --remove-orphans
```
